#!/usr/bin/env python3
"""handle() 過時待回清單保險：最後一則已是阿財 → 略過（不呼叫模型、不寫訊息）；最後一則是候選人 → 照常往下（離線、假依賴）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)

class Reached(Exception): pass
def run_handle(last_role):
    seen = {'sql': [], 'claude': 0, 'insert': 0, 'ctx': 0}
    def fake_d1(sql, *a, **k):
        seen['sql'].append(sql)
        if sql.startswith('SELECT role FROM messages'):
            return [{'role': last_role}]
        if sql.startswith('INSERT INTO messages'):
            seen['insert'] += 1
        return []
    class T:
        def __init__(s, *a, **k): pass
        def start(s): pass
        def close(s): pass
    def ctx_for(a):
        seen['ctx'] += 1
        raise Reached()           # 走到這裡代表保險放行了（後面的流程這個測試不關心）
    D.d1 = fake_d1; D._mark_host = lambda *a, **k: None; D._Transition = T
    D._post_prewarmed_opening = lambda *a, **k: False
    D._max_msg_id = lambda a: 1; D.context_for = ctx_for
    D.log = lambda *a, **k: None; D.tg = lambda *a, **k: None
    D.run_claude = lambda *a, **k: seen.__setitem__('claude', seen['claude'] + 1)
    D.release_lock = lambda a: None
    D._busy.add('app-x')
    D.handle({'id': 'app-x', 'name': '測試', 'job_slug': 'j', 'job_title': '職缺'})
    return seen
s = run_handle('assistant')
chk('最後一則已是阿財（待回清單過時）→ 略過：不讀對話、不呼叫模型、不寫訊息', s['ctx'] == 0 and s['claude'] == 0 and s['insert'] == 0, s)
chk('檢查時排除「過渡語」（用 sql_not_transition）', any('SELECT role FROM messages' in q and 'ORDER BY id DESC LIMIT 1' in q for q in s['sql']))
s = run_handle('candidate')
chk('最後一則是候選人 → 保險放行，照常往下走', s['ctx'] == 1, s)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
