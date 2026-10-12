#!/usr/bin/env python3
"""E30：履歷晚到（LINE）→ 快取整份重抓；沒有履歷不預熱（假 d1／假 _fetch_static）"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)
D.log = lambda *a, **k: None
calls = {'fetch': 0, 'sql': []}
row = {'resume_file_id': None, 'resume_url': ''}
def fake_d1(sql):
    calls['sql'].append(sql)
    if 'a.resume_file_id' in sql or 'resume_file_id, resume_url' in sql:
        return [dict(row)]
    return []
D.d1 = fake_d1
def fake_fetch(app_id):
    calls['fetch'] += 1
    return {'resume_readable': True, 'assessment': 'x', 'screening': 'y', 'resume_text': '履歷內容'}
D._fetch_static = fake_fetch
APP = 'app-late-resume'
static = {'resume_readable': False, 'assessment': 'a', 'screening': 's'}
D._late_checked.clear(); D._STATIC_CACHE.clear()

out = D._late_refresh(APP, static)
chk('履歷還沒到：不重抓、回原快取', out is static and calls['fetch'] == 0)
D._late_checked.clear()
row['resume_file_id'] = 'file-1'
out = D._late_refresh(APP, static)
chk('履歷到了（換了一份）：整份重抓並換掉快取', calls['fetch'] == 1 and out.get('resume_readable') is True and D._STATIC_CACHE.get(APP) is out, (calls, out))
chk('重抓的結果記下 _resume_key（避免每 30 秒重抓）', out.get('_resume_key') == 'file-1||')

# 抽不到字的圖片履歷：同一份不要每 30 秒重抓
static2 = {'resume_readable': False, '_resume_key': 'file-1||', 'assessment': 'a', 'screening': 's'}
D._late_checked.clear(); calls['fetch'] = 0
D._late_refresh(APP, static2)
chk('同一份履歷（抽不到字）→ 不重抓', calls['fetch'] == 0)
row['resume_file_id'] = 'file-2'
D._late_checked.clear()
D._late_refresh(APP, static2)
chk('換成另一份履歷 → 再抓一次', calls['fetch'] == 1)

# 30 秒節流
D._late_checked.clear(); calls['fetch'] = 0; n0 = len(calls['sql'])
static3 = {'resume_readable': False, 'assessment': 'a', 'screening': 's'}
row['resume_file_id'] = None
D._late_refresh(APP, static3); n1 = len(calls['sql'])
D._late_refresh(APP, static3); n2 = len(calls['sql'])
chk('30 秒內不重複查 D1', n2 == n1 and n1 > n0, (n0, n1, n2))

# 履歷讀得到時完全不查
D._late_checked.clear(); n0 = len(calls['sql'])
ok = {'resume_readable': True, 'assessment': 'a', 'screening': 's', '_interview_started': True}   # E31：面談還沒開始時會多看一眼 parsed_at；開始後才完全不查
D._late_refresh(APP, ok)
chk('已有可讀履歷＋測驗＋初篩、面談已開始 → 不查 D1', len(calls['sql']) == n0)

# D1 查詢失敗：沿用快取、不丟例外
def boom(sql): raise RuntimeError('d1 down')
D.d1 = boom; D._late_checked.clear()
try:
    out = D._late_refresh(APP, {'resume_readable': False, 'assessment': 'a', 'screening': 's'})
    chk('D1 失敗 → 沿用快取、不丟例外', True)
except Exception as e:
    chk('D1 失敗 → 沿用快取、不丟例外', False, e)

# 預熱 SQL：沒有履歷資訊的不預熱
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'interview_daemon.py'), encoding='utf-8').read()
i = src.index('def prewarm_candidates')
body = src[i:i + 6000]
chk('prewarm_candidates SQL 要求有履歷（file／url／104／補件）', "a.resume_file_id IS NOT NULL" in body and "resume_104_url" in body and "resume_extra_files" in body)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
