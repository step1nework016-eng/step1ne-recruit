#!/usr/bin/env python3
"""顧問在「招募職缺」頁改了 JD 之後，把改動真的變成網站上的樣子。

## 為什麼需要這支

顧問後台的「編輯 JD」彈窗存檔時，Worker 只能把新內容寫進 D1
（jobs.jd_spec_json），標記 jd_regen_pending=1——Worker 跑不了 Python、
也 push 不了 git，改不動 jobs/<slug>/index.html 這個靜態檔案。

這支就是那個「把 DB 裡的新內容變成真的網頁」的跑腿，由 launchd 每 10
分鐘叫一次：撿一筆 jd_regen_pending=1 的職缺，組出完整規格 JSON，
呼叫既有的 publish_job.py（新增職缺時就是用它產頁面／更新列表卡／
sitemap／apply/jobs.json／D1），跑完直接部署。

## 為什麼一次只處理一筆

跟 tick.py 同樣的理由：這個量級一天不會超過幾筆，一次一筆比較好排查
是哪一筆出的問題；而且 publish_job.py 的 --deploy 會整包 git add -A、
commit、push，兩筆疊在一起 push 出錯了也分不清是哪一筆的內容壞的。

## 匿名客戶的防線

jobs.client_named=0 的職缺（未簽約、對外要匿名）絕對不能把 client_name
寫進要產頁面的規格裡——這裡再檢查一次，不只靠 Worker 那邊 hitsClientNames
抓文字，是雙重保險：文字比對抓得到「提到客戶名字」，但抓不到「這欄位
本來就是客戶名字」這種結構性外洩。
"""
import json, os, re, subprocess, sys, time, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
LOCK = '/tmp/step1ne-jdregen.lock'

sys.path.insert(0, HERE)
import draft_job as DJ  # noqa: E402  借用 tg_send／_esc
import slug_promote as SP  # noqa: E402  暫存網址轉正


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def env():
    e = dict(os.environ)
    for f in ('cf.env', 'tokens.env'):
        p = os.path.expanduser(f'~/.config/workflow-os/{f}')
        if not os.path.exists(p):
            continue
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                e[k.strip()] = v.strip().strip('\'"')
    e.pop('CLAUDECODE', None)
    e.pop('CLAUDE_CODE_ENTRYPOINT', None)
    return e


def d1(sql):
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', 'step1ne-recruit',
         '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env(), capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout)[0]['results']
    except Exception:
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return []


def q(v):
    """SQL 字串安全引號——跟 interview_daemon.py 的 D.q 同樣規則。"""
    if v is None:
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def build_spec(job, saved_spec):
    """把存起來的 jd_spec_json 補成 publish_job.py 需要的完整規格。

    顧問改欄位時常常只改一兩個（例如只改薪資），沒有義務每次都把
    page_title／description 這種 SEO 用欄位重打一次——這裡用合理的
    預設值補齊，讓他不用管這些技術性欄位也能存檔。
    """
    spec = dict(saved_spec or {})
    spec['slug'] = job['slug']
    title = spec.get('title') or job.get('title') or job['slug']
    spec['title'] = title
    if not spec.get('page_title'):
        spec['page_title'] = f'{title}｜Step1ne 德仁管理顧問'
    if not spec.get('description'):
        spec['description'] = spec.get('card_desc') or spec.get('intro') or title
    if not spec.get('employment'):
        spec['employment'] = ['FULL_TIME']
    # 未簽約客戶對外一律匿名——這個欄位絕對不能出現在要產頁面的規格裡，
    # 不管 jd_spec_json 裡有沒有存到（理論上顧問不該存，這裡再擋一次）。
    if str(job.get('client_named')) == '0':
        spec.pop('client_name', None)
    elif job.get('client_name') and not spec.get('client_name'):
        spec['client_name'] = job['client_name']
    return spec


def promote_slug_if_needed(job, saved):
    """pending- 暫存網址在發布前轉成語意 slug。轉不成就不發布。

    2026-09-07 查到 6 筆職缺就是帶著 `pending-co_802bdd6b-...` 這種暫存代號
    公開在架上的——因為整條上架鏈路（客戶建缺 → 顧問核准 → AI 擬稿 →
    重產頁面 → 部署）沒有任何一步負責換掉它。這裡就是那一步：
    publish_job.py 是最後一道門（它會直接拒絕 pending- 開頭的 slug），
    這裡是**唯一**該把它換掉的地方。
    """
    slug = job['slug']
    if not SP.is_pending(slug):
        return slug, None
    new_slug, err = SP.promote(slug, saved.get('slug_suggestion'), d1, d1)
    if err:
        return slug, err
    log(f'🔤 網址轉正：{slug} → {new_slug}')
    DJ.tg_send(
        f'🔤 <b>職缺網址已轉正</b>　{DJ._esc(job.get("title") or new_slug)}\n'
        f'{DJ._esc(slug)} → <code>{DJ._esc(new_slug)}</code>（發布前轉，沒有對外過的舊網址）',
        [], new_slug)
    return new_slug, None


def process_one(job):
    slug = job['slug']
    saved = {}
    if job.get('jd_spec_json'):
        try:
            saved = json.loads(job['jd_spec_json']) or {}
        except Exception:
            log(f'⚠️ {slug} 的 jd_spec_json 壞掉，無法解析')
            mark_done(slug, error='jd_spec_json 壞掉（不是合法 JSON），需要人工檢查')
            return

    slug, slug_err = promote_slug_if_needed(job, saved)
    if slug_err:
        log(f'⛔ 不發布：{slug_err}')
        mark_done(job['slug'], error=slug_err)
        DJ.tg_send(
            f'⛔ <b>職缺沒有上架</b>　{DJ._esc(job.get("title") or job["slug"])}\n'
            f'{DJ._esc(slug_err)}', [], job['slug'])
        return
    job = {**job, 'slug': slug}

    spec = build_spec(job, saved)
    for need in ('title', 'page_title', 'description'):
        if not spec.get(need):
            mark_done(slug, error=f'缺少必要欄位：{need}，無法產生頁面')
            return

    fd, path = tempfile.mkstemp(prefix=f'jdregen_{slug}_', suffix='.json')
    os.close(fd)
    json.dump(spec, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    log(f'重新產生：{spec["title"]}（{slug}）')
    r = subprocess.run(
        [sys.executable, os.path.join(RECRUIT, 'publish_job.py'), path, '--deploy'],
        cwd=RECRUIT, env=env(), capture_output=True, text=True, timeout=300)
    out = ((r.stdout or '') + (r.stderr or '')).strip()
    try:
        os.remove(path)
    except Exception:
        pass

    if r.returncode != 0:
        log(f'❌ 失敗：{out[-500:]}')
        mark_done(slug, error=out[-500:])
        DJ.tg_send(
            f'❌ <b>JD 更新失敗</b>　{DJ._esc(spec["title"])}\n'
            f'{DJ._esc(out[-400:])}\n'
            f'頁面內容還沒套用，需要人工檢查（改擬稿或直接改檔案）。', [], slug)
        return

    log(f'✅ 完成：{slug}')
    mark_done(slug, error=None)
    DJ.tg_send(
        f'✅ <b>JD 已更新上線</b>　{DJ._esc(spec["title"])}\n'
        f'https://step1ne.com/jobs/{DJ._esc(slug)}/', [], slug)


def mark_done(slug, error):
    now_sql = "datetime('now','+8 hours')"
    err_sql = q(error) if error else 'NULL'
    d1(f"UPDATE jobs SET jd_regen_pending=0, jd_regen_last_at={now_sql}, "
       f"jd_regen_last_error={err_sql} WHERE slug={q(slug)}")


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 900:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        rows = d1("SELECT slug, title, client_name, client_named, jd_spec_json "
                  "FROM jobs WHERE jd_regen_pending = 1 "
                  "ORDER BY jd_updated_at ASC LIMIT 1")
        if not rows:
            return
        process_one(rows[0])
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


# ⚠️ 2026-09-10 改：這支原本純靠 launchd StartInterval 每10分鐘戳一次，
# 今天連續在好幾支排程上撞到「StartInterval 計時器安靜停止跳動」的病——
# log 停在 09-08 12:40，兩天沒動靜也沒人發現。改成跟 ai_worker.py 同一套
# 常駐迴圈，plist 配 RunAtLoad+KeepAlive，不再依賴會壞的定時器；
# main() 內部的鎖跟「一次只處理一筆」邏輯完全不變，只是換成自己睡覺再叫自己。
if __name__ == '__main__':
    if '--once' in sys.argv:
        main()
    else:
        log('JD 重新發布處理器啟動（常駐，每 10 分鐘掃一次）')
        while True:
            try:
                main()
            except Exception as e:
                log(f'⚠️ 這一輪出錯（不影響下一輪）：{str(e)[:200]}')
            time.sleep(600)
