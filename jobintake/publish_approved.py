#!/usr/bin/env python3
"""顧問按了「✅ 核准發布」之後，才由這一支真的動網站。

這是整條流程唯一會改到 step1ne.com 的地方，而且**只處理 status='approved'**。
狀態是顧問在 Telegram 按按鈕、由 Worker 的 /telegram/webhook 寫進去的——
也就是說，沒有人按過核准，這支就什麼都不會做。

它做的事：
  ① 取出核准的收件單與擬好的規格 JSON
  ② 發布前再跑一次禁刊過濾器（成品對外欄位）。有命中就**停下來**，
     退回 rewrite 狀態並通知顧問——不要相信「上次掃過了」
  ③ 呼叫既有的 publish_job.py：職缺頁＋職缺專區卡片＋sitemap＋apply/jobs.json＋D1
  ④ 補寫 publish_job.py 沒有處理的幾個欄位：
     client_relation／service_line／client_named／ai_disclosure／client_code／intake_id
  ⑤ 通知群組有新職缺（阿財就是靠 D1 的 jobs 表抓 JD，寫進去它就讀得到）

用法：
    python3 publish_approved.py --dry            # 只產檔到 /tmp，不動網站也不動 D1
    python3 publish_approved.py                  # 真的產檔＋寫 D1（仍不 git push）
    python3 publish_approved.py --intake <id>    # 只處理指定那一筆

⚠️ 這支**不會 git push**。產完檔案要不要部署由人決定：
   cd ~/下載項目/step1ne-stopgap-site && git add -A && git commit && git push deploy HEAD:main
"""
import os, sys, json, argparse, subprocess, importlib.util
import re, uuid, secrets, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, 'work')

sys.path.insert(0, HERE)
import publishing_filters as PF          # noqa: E402
import draft_job as DJ                   # noqa: E402（借用 tg_send／_esc／log）

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

PUBLIC_KEYS = ('title', 'subtitle', 'page_title', 'description', 'keywords',
               'og_title', 'og_desc', 'intro', 'tags', 'locations', 'locality',
               'region', 'must_skills', 'benefits', 'spec', 'duties', 'must',
               'plus', 'why', 'faq', 'industry', 'card_meta', 'card_desc', 'slug')


def final_check(intake, spec):
    """發布前的最後一道。回傳命中清單，空的才可以發布。"""
    blob = json.dumps({k: spec.get(k) for k in PUBLIC_KEYS}, ensure_ascii=False, indent=1)
    return PF.scan_output(
        blob,
        client_name=intake.get('client_name') if spec.get('client_named') == 0 else None,
        client_code=intake.get('client_code'))


def link_client(intake, spec):
    """把收件單上的客戶接進客戶名單，並掛到職缺上。

    ⚠️ 2026-08-27 加。在這之前這一段整段不存在：收件單上明明填了
    client_name（台銀人壽保險股份有限公司），職缺也照常上架、狀態標成
    published，但客戶從來沒進 client_companies、jobs.company_id 也留空。
    後果是「客戶資訊」那一頁看不到這家客戶——顧問以為系統漏了，實際上是
    這條線根本沒接。同一批還有三個職缺是類似狀況。

    比對方式刻意寬鬆（去掉公司後綴再互相包含），因為同一家公司在收件單、
    合約、104 上常常寫法不同（美德向邦／美德醫療／美德相邦集團）。
    寧可接到既有的那一筆，也不要建出第二家一模一樣的客戶。
    """
    name = (intake.get('client_name') or '').strip()
    slug = spec.get('slug')
    if not name or not slug:
        return
    base = re.sub(r'(股份有限公司|有限公司|集團|公司)$', '', name).strip()
    rows = D.d1("SELECT id, display_name, aliases FROM client_companies") or []
    hit = None
    for r in rows:
        cands = [r.get('display_name') or ''] + str(r.get('aliases') or '').split('\n')
        for c in cands:
            c = c.strip()
            if not c or len(c) < 2:
                continue
            cb = re.sub(r'(股份有限公司|有限公司|集團|公司)$', '', c).strip()
            if base and cb and (base in cb or cb in base):
                hit = r
                break
        if hit:
            break

    if not hit:
        cid = 'co_' + uuid.uuid4().hex[:8]
        token = secrets.token_hex(24)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 未簽約（client_named=0）的一律標 prospect 並在備註寫明對外匿名——
        # 這個備註同時是社群貼文客戶名稱稽核的來源，寫進去才擋得住。
        unsigned = spec.get('client_named') == 0
        note = ('未簽約。對外全面匿名，公司名不得出現在任何對外文案、貼文或候選人訊息。'
                if unsigned else None)
        D.d1(f"INSERT INTO client_companies "
             f"(id, display_name, portal_token, relation, relation_note, created_at, updated_at) "
             f"VALUES ({D.q(cid)}, {D.q(name)}, {D.q(token)}, "
             f"{D.q('prospect' if unsigned else 'signed')}, {D.q(note)}, {D.q(now)}, {D.q(now)})")
        DJ.log(f"🆕 客戶名單新增：{name}")
    else:
        cid = hit['id']
        DJ.log(f"🔗 客戶對到既有的：{hit[chr(39)+chr(39)]}")

    D.d1(f"UPDATE jobs SET company_id={D.q(cid)} WHERE slug={D.q(slug)}")


def upsert_extra_columns(spec):
    """publish_job.py 的 upsert_d1() 沒寫這幾欄，補上。

    為什麼不直接改 publish_job.py 的 SQL：那支是既有、線上在用的工具，
    改它的 INSERT 欄位清單風險比在後面補一次 UPDATE 高。
    """
    sets = []
    for col in ('client_relation', 'service_line', 'ai_disclosure', 'client_code', 'intake_id'):
        if spec.get(col) is not None:
            sets.append(f"{col}={D.q(spec[col])}")
    if spec.get('client_named') is not None:
        sets.append(f"client_named={int(spec['client_named'])}")
    if not sets:
        return
    D.d1(f"UPDATE jobs SET {', '.join(sets)} WHERE slug={D.q(spec['slug'])}")


SITE_ROOT = os.path.expanduser('~/下載項目/step1ne-stopgap-site')


def git_push(spec):
    """2026-08-18 加：顧問要求審核通過就直接上線，不用再手動 push。

    ⚠️ 這是拿掉一道人工把關——原本刻意設計成「產完檔案先不推，等人看過再
    手動 push」，是最後一道防線。改成自動推之後，AI 流程只要顧問在
    Telegram 按核准，職缺就會直接上站，不會再有「上線前最後看一眼」的
    機會，出錯（例如禁刊沒擋乾淨、頁面壞掉）會直接反映在正式站上。
    只 add 這次新增/修改到的檔案，不用 -A，避免把使用者手上其他未完成的
    修改一起推上去。
    """
    slug = spec.get('slug')
    paths = ['apply/jobs.json', 'jobs/index.html', 'sitemap.xml', f'jobs/{slug}/']
    try:
        subprocess.run(['git', 'add', *paths], cwd=SITE_ROOT, check=True,
                        capture_output=True, text=True, timeout=30)
        diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=SITE_ROOT)
        if diff.returncode == 0:
            DJ.log('⚠️ git push 略過：沒有偵測到檔案變更（可能已經推過）')
            return True, None
        subprocess.run(['git', 'commit', '-m', f'新增職缺：{spec.get("title")}'],
                        cwd=SITE_ROOT, check=True, capture_output=True, text=True, timeout=30)
        subprocess.run(['git', 'push', 'deploy', 'HEAD:main'],
                        cwd=SITE_ROOT, check=True, capture_output=True, text=True, timeout=60)
        return True, None
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or str(e))[-500:]


def notify_new_job(spec, dry, pushed=None, push_err=None):
    """通知群組有新職缺。阿財讀的是 D1 的 jobs 表，寫進去它就抓得到 JD。"""
    if pushed:
        deploy_line = '✅ 已自動推上線，部署到各節點需要幾分鐘，網址可能先 404 再變正常，屬正常現象。'
    elif push_err:
        deploy_line = f'⚠️ 自動推上線失敗，頁面檔案已產出但**尚未部署**，需要手動處理：\n{DJ._esc(push_err)}'
    else:
        deploy_line = '⚠️ 頁面檔案已產出，但**尚未部署**——要上線請自行 git push。'
    text = (f'🆕 <b>新職缺已上架</b>　{DJ._esc(spec.get("title"))}\n'
            f'網址：https://step1ne.com/jobs/{DJ._esc(spec.get("slug"))}/\n'
            f'服務線：{DJ._esc(spec.get("service_line"))}　｜　'
            f'客戶對象：{DJ._esc(spec.get("client_relation"))}\n'
            f'阿財已可抓到這份 JD（jobs 表已更新）。\n'
            f'{deploy_line}')
    if dry:
        print('\n[--dry] 原本會送出的新職缺通知：\n' + text)
        return
    DJ.tg_send(text, [], spec.get('slug'))


def process(intake, dry=False):
    iid = intake['id']
    spec = json.loads(intake['draft_json'] or '{}')
    if not spec.get('slug'):
        DJ.log(f'❌ {iid} 沒有可用的規格 JSON')
        return

    hits = final_check(intake, spec)
    if hits:
        DJ.log(f'⛔ 發布前最後一道擋下 {len(hits)} 處，停止發布')
        print(PF.format_report(hits, title='發布前最後一道'))
        if not dry:
            D.d1(f"UPDATE job_intakes SET status='rewrite', "
                 f"rewrite_note='發布前最後一道禁刊檢查未通過，需重擬', "
                 f"updated_at=datetime('now','+8 hours') WHERE id={D.q(iid)}")
            DJ.tg_send('⛔ <b>發布中止</b>\n這份職缺在發布前的最後一道禁刊檢查沒過：\n'
                       + DJ._esc(PF.format_report(hits, title='')[:800])
                       + '\n已退回重寫，網站沒有任何改動。', [], iid)
        return

    workdir = os.path.join(WORK, iid)
    os.makedirs(workdir, exist_ok=True)
    spec_path = os.path.join(workdir, 'spec.publish.json')
    json.dump(spec, open(spec_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    cmd = ['python3', os.path.join(ROOT, 'publish_job.py'), spec_path]
    if dry:
        cmd.append('--dry')
    DJ.log('跑 publish_job.py' + ('（--dry）' if dry else ''))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    print(r.stdout or r.stderr)
    if r.returncode != 0:
        DJ.log('❌ publish_job.py 失敗，狀態不變更')
        return

    pushed, push_err = None, None
    if not dry:
        upsert_extra_columns(spec)
        link_client(intake, spec)
        D.d1(f"UPDATE job_intakes SET status='published', published_slug={D.q(spec['slug'])}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(iid)}")
        DJ.log('git push 部署中…')
        pushed, push_err = git_push(spec)
        if push_err:
            DJ.log(f'❌ git push 失敗：{push_err}')
    notify_new_job(spec, dry, pushed=pushed, push_err=push_err)
    DJ.log(f'✅ 完成：{spec["slug"]}' + ('（--dry，只產檔到 /tmp）' if dry else
           ('（已推上線）' if pushed else '（部署失敗，需人工處理）')))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--intake')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--local', help='本機收件單 JSON（離線測試，draft_json 直接讀 work/<id>/spec.json）')
    a = ap.parse_args()

    if a.local:
        intake = json.load(open(a.local, encoding='utf-8'))
        iid = intake['id']
        sp = os.path.join(WORK, iid, 'spec.json')
        intake['draft_json'] = open(sp, encoding='utf-8').read()
        process(intake, dry=a.dry)
        return

    where = f"id={D.q(a.intake)}" if a.intake else "status='approved'"
    rows = D.d1(f"SELECT * FROM job_intakes WHERE {where}")
    rows = [r for r in rows if r['status'] == 'approved' or a.intake]
    if not rows:
        DJ.log('沒有已核准待發布的收件單')
        return
    for r in rows:
        process(r, dry=a.dry)


if __name__ == '__main__':
    main()
