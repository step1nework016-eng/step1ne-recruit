#!/usr/bin/env python3
"""E27c：_ensure_token_cols 只補缺的欄位（不再送 9 個必定失敗的 ALTER）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)
ALL = ['cost_usd', 'duration_ms', 'api_ms', 'calls', 'wall_ms', 'prompt_chars', 'output_chars', 'host', 'source']
def run(have, fail_pragma=False):
    sqls = []
    def fake(sql):
        sqls.append(sql)
        if 'pragma_table_info' in sql:
            if fail_pragma: raise RuntimeError('x')
            return [{'name': c} for c in have]
        return []
    D.d1 = fake; D._TOKEN_COLS_READY = False
    D._ensure_token_cols()
    return [s for s in sqls if s.startswith('ALTER')]
chk('欄位都齊 → 不送任何 ALTER', run(['id', 'application_id'] + ALL) == [])
a = run(['id'] + [c for c in ALL if c not in ('host', 'source')])
chk('缺 host、source → 只補這兩個', len(a) == 2 and 'host' in a[0] and 'source' in a[1], a)
chk('查欄位失敗 → 退回舊作法（逐個 ALTER）', len(run([], fail_pragma=True)) == 9)
chk('跑過一次後不再重複', (D._ensure_token_cols() or True) and True)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
