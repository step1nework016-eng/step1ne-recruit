#!/usr/bin/env python3
"""叫阿財把某一場面談收尾（顧問手動觸發）。

為什麼需要這支：候選人有時候就是停在那裡不回也不關視窗。
逾時 15 分鐘的自動收尾會用「看您這邊暫時沒有回覆」那套說法，
但顧問看到談得差不多了、想正常收掉時，應該走 Phase 6 的正式收尾，
而不是用「你沒回應」的語氣結束——那對談了半小時的人不太公道。

⚠️ 收尾前會先檢查必問項。缺的話這一輪改成補問，不會直接收——
2026-07-30 就是因為沒有這個檢查，在阿財剛問出「待業期間在做什麼」的下一刻
被叫去收尾，那題答案永遠拿不到了，報告只能寫「未確認」丟給顧問。

用法：
    python3 force_close.py <候選人姓名或 application_id>
    python3 force_close.py <...> --now    # 不管缺什麼，直接收
"""
import json, os, subprocess, sys, datetime, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def main():
    if len(sys.argv) < 2:
        sys.exit('用法：python3 force_close.py <姓名或 id>')
    key = sys.argv[1]

    rows = D.d1(f"SELECT id, name, job_slug, interview_state FROM applications "
                f"WHERE id = {D.q(key)} OR name = {D.q(key)}")
    if not rows:
        sys.exit(f'找不到「{key}」')
    app = rows[0]
    if app['interview_state'] != 'active':
        sys.exit(f"{app['name']} 的狀態是 {app['interview_state']}，不是進行中，不用收尾")

    # 搶跟 daemon 共用的鎖——搶不到代表 daemon 剛好也在處理這一場，
    # 兩邊同時寫訊息會互相覆蓋（2026-07-30 實際發生過，面談被搞壞兩次）。
    # 不要硬上，等幾秒讓 daemon 那輪處理完，通常幾十秒內就會釋放。
    import time as _t
    for _ in range(15):
        if D.acquire_lock(app['id']):
            break
        print('  daemon 正在處理這一場，等它這輪跑完…')
        _t.sleep(4)
    else:
        sys.exit('  daemon 一直沒放開這一場，先不要收尾——可能它正在寫入，硬上會撞壞資料。')

    try:
        print(f"  對象：{app['name']}（{app['job_slug']}）")
        ctx = D.context_for(app['id'])

        now_flag = '--now' in sys.argv

        task = """

─────────  現在要做的事  ─────────

顧問要求收尾。但**收尾之前先自己檢查這五項有沒有真的拿到答案**：

1. 目前在職還是待業（待業要知道多久、期間在做什麼）
2. 為什麼想看新機會
3. 能不能接受派遣（職缺是派遣時）
4. **期望薪資**——表單沒填就要問，不能省
5. **可到職日**——表單沒填就要問，客戶急的話這題決定案子走不走得下去

### 如果有任何一項沒拿到

**這一輪不要收尾。** 把缺的那幾項用一到兩則訊息問完，`end` 設 **false**。
問法要自然，不要變成點名式的清單。

例：
「差不多要收尾了，還有兩件事想跟您確認一下——
您期望的待遇大概在什麼範圍？另外如果順利的話，大概什麼時候可以到職？」

### 五項都拿到了

進入 Phase 6 的正式收尾，`end` 設 **true**。
因為候選人這一輪不會再回話，把兩則合成一次輸出：
先講「時間差不多到一個段落」，再問有沒有想補充或想問的，
然後說明後續流程、請他推薦朋友、道謝收尾。

⚠️ 不要用「看您這邊沒有回覆」那種語氣——他談了很久，那樣講不公道。
"""
        if now_flag:
            task = """

─────────  現在要做的事  ─────────

顧問要求**立刻收尾**，不要再問任何問題（已指定 --now）。
直接進入 Phase 6 的正式收尾，兩則合成一次輸出，`end` 設 true。
⚠️ 不要用「看您這邊沒有回覆」那種語氣。
"""

        prompt = D.build_prompt(ctx, D.skill('talk')) + task
        print('  請阿財檢查必問項並產生訊息…' if not now_flag else '  直接收尾（--now）…')
        result = D.run_claude(prompt)
        msgs = [m for m in (result.get('messages') or []) if str(m).strip()][:3]
        if not msgs:
            sys.exit('阿財沒有產出訊息，沒有動任何資料')
        ending = bool(result.get('end'))

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        vals = ','.join(f"({D.q(app['id'])},'assistant',{D.q(m)},'{now}')" for m in msgs)
        D.d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES {vals}")
        print(f'  ✅ 已送出 {len(msgs)} 則：')
        for m in msgs:
            for line in m.split('\n'):
                print(f'     {line}')
            print()

        if not ending:
            print('  ⏸ 阿財判斷還有必問項沒拿到，改成補問，這場繼續。')
            print('     等候選人回覆之後，引擎會自己接下去；要強制收就加 --now。')
            return

        print('  產報告中（Opus，約一分鐘）…')
        D.finish(app['id'], app['name'], app['job_slug'], ctx, abandoned=False)
        print('  ✅ 完成，報告已存並通知顧問')
    finally:
        # 不管上面是正常結束、中途 return、還是例外，鎖都要放掉——
        # 不放的話這場會卡死在鎖定狀態，daemon 永遠搶不回去。
        D.release_lock(app['id'])


if __name__ == '__main__':
    main()
