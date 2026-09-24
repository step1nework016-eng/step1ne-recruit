#!/usr/bin/env python3
"""職缺收件的跑腿：把顧問送出的收件單推著往下走。

## 為什麼需要這支

整條流程有三段，但中間**沒有人在推**：

    顧問填表 → [?] → 總指揮擬 JD → 群組三顆按鈕 → [?] → 產出職缺頁 → 上線

Worker 收得到表單、也改得了狀態，但它跑不了 Python、也 push 不了 git。
所以那兩個 [?] 一直是空的——顧問送出之後什麼都不會發生，
按了「核准發布」也只是資料庫欄位變了，網站上還是沒有那個職缺。

這支就是那兩個 [?]，由 launchd 每分鐘叫一次。

## 它做什麼

1. `status='new'` 的收件單 → 跑 `draft_job.py --intake <id>`（擬 JD、推群組等審核）
2. `status='rewrite'`（顧問按了重寫並留了意見）→ 帶著意見重擬
3. `status='approved'` → 跑 `publish_approved.py --intake <id>` 產出職缺頁

⚠️ **產完不會自動 git push**，需要人確認才上線——這是 Jacky 明確要求的
「不要自動發佈」。要改成全自動的話把 PUSH_ON_APPROVE 設 True，
並且要有心理準備：職缺頁寫錯薪資或漏擋歧視條件會直接上線。

## 為什麼一次只處理一筆

擬 JD 要叫 claude-opus-5，一次好幾筆會同時開好幾個模型呼叫，
既慢又可能互相搶資源。一分鐘一筆對這個量級（一天不會超過幾筆）綽綽有餘。
"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
LOCK = '/tmp/step1ne-jobintake.lock'
PUSH_ON_APPROVE = False      # 見上方說明，預設不自動上線


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
    # ⚠️ 這個變數在時 claude 會拒絕啟動（防巢狀 session），子流程要擬 JD 一定要拿掉
    e.pop('CLAUDECODE', None)
    e.pop('CLAUDE_CODE_ENTRYPOINT', None)
    return e


def d1(sql):
    # 2026-09-24：先走 HTTP（d1_http），不行才退回 npx wrangler。
    # 每次開 npx wrangler 要一分鐘上下、8GB 機器還會變卡——排程每分鐘跑一次時
    # 光「問一下有沒有新收件單」就吃掉大半時間。跟 interview_daemon 同一個做法。
    try:
        sys.path.insert(0, RECRUIT)
        import d1_http
        return d1_http.query(sql).get('results') or []
    except Exception as e:
        log(f'HTTP 查 D1 失敗，改用 wrangler：{str(e)[:120]}')
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', 'step1ne-recruit',
         '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env(), capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout)[0]['results']
    except Exception:
        # 查詢失敗要吵。回空陣列跟「沒有待辦」長得一模一樣，
        # 分不出來的話排程壞掉也不會有人發現。
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return []


def run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       cwd=HERE, env=env(), capture_output=True, text=True, timeout=900)
    out = (r.stdout or '') + (r.stderr or '')
    return r.returncode == 0, out.strip()[-800:]


def main():
    # 檔案鎖：擬 JD 可能跑好幾分鐘，別讓下一分鐘的排程重疊進來
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 900:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        # 規則寫在三個地方，不一致不會報錯只會安靜做錯事（見 check_rules.py）。
        # 順手比一次，錯了就吵——這關係到履歷具名與客戶隱私。
        ok, out = run('check_rules.py')
        if not ok:
            log('⚠️ 客戶對象規則不一致：\n' + out)

        # ⚠️ 欄位名要對。第一版寫了不存在的 job_title，wrangler 直接報錯，
        # 而 d1() 的 except 把錯誤吃掉回空陣列——結果是「排程有在跑但什麼都不做」，
        # log 全空、也沒有任何錯誤訊息。這種靜默失敗最難查，所以下面加了 debug log。
        # 2026-09-24：擬稿搬到 WSL2，上架留在 Mac（上架要動 step1ne-stopgap-site，那個 repo 只在 Mac）。
        # 用環境變數 JOBINTAKE_STAGES 決定這台做哪幾段：
        #   WSL2：new,rewrite（擬稿）　Mac：approved（上架）　沒設＝三段都做（舊行為）
        stages = [x.strip() for x in os.environ.get('JOBINTAKE_STAGES', 'new,rewrite,approved').split(',') if x.strip()]
        rows = d1("SELECT id, status, client_name FROM job_intakes "
                  f"WHERE status IN ({','.join(repr(x) for x in stages)}) "
                  "ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, created_at ASC LIMIT 1")
        if not rows:
            return
        it = rows[0]
        iid, st = it['id'], it['status']
        title = it.get('client_name') or iid[:8]

        if st in ('new', 'rewrite'):
            # 先搶再做：兩台機器同一分鐘看到同一張單時，只有搶到的那台擬。
            # 搬遷期間 Mac 跟 WSL2 可能同時開著，不搶會各擬一份、群組收到兩則。
            try:
                sys.path.insert(0, RECRUIT)
                import d1_http
                res = d1_http.query(f"UPDATE job_intakes SET status='drafting' WHERE id='{iid}' AND status='{st}'")
                if not ((res.get('meta') or {}).get('changes')):
                    log(f'{title}：被另一台搶走了，跳過')
                    return
            except Exception as e:
                log(f'搶單失敗（這輪先不做）：{str(e)[:120]}')
                return
            log(f'擬 JD：{title}（{st}）')
            ok, out = run('draft_job.py', '--intake', iid)
            log(('  完成' if ok else '  ❌ 失敗：') + ('' if ok else out[-400:]))
            if not ok:
                # draft_job 失敗時會退回它看到的狀態（已經是 drafting），這裡改回原本的，下一輪才撿得到
                d1(f"UPDATE job_intakes SET status='{st}' WHERE id='{iid}' AND status='drafting'")
        elif st == 'approved':
            log(f'核准發布：{title}')
            ok, out = run('publish_approved.py', '--intake', iid)
            if not ok:
                log('  ❌ 產檔失敗：' + out[-400:])
                return
            log('  頁面已產出')
            if PUSH_ON_APPROVE:
                site = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
                subprocess.run(['git', 'add', '-A'], cwd=site, timeout=120)
                subprocess.run(['git', 'commit', '-q', '-m', f'新增職缺：{title}'], cwd=site, timeout=120)
                subprocess.run(['git', 'push', '-q', 'deploy', 'main'], cwd=site, timeout=300)
                log('  已推上線')
            else:
                log('  ⚠️ 尚未上線——PUSH_ON_APPROVE=False，要有人確認後推 git')
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
