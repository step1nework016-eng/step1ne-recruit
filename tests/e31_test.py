#!/usr/bin/env python3
"""E31：多張截圖——同一個 resume_file_id、文字被重寫（files.parsed_at 變了）→ 快取整份重抓（假 d1／假 _fetch_static）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)
D.log = lambda *a, **k: None
row = {'resume_file_id': 'f1', 'resume_url': '', 'interview_state': 'not_started', 'parsed_at': '2026-10-12 10:00:00'}
calls = {'fetch': 0, 'sql': 0}
def fake_d1(sql):
    calls['sql'] += 1
    return [dict(row)] if 'a.resume_file_id' in sql else []
D.d1 = fake_d1
def fake_fetch(app_id):
    calls['fetch'] += 1
    return {'resume_readable': True, 'assessment': 'a', 'screening': 's'}
D._fetch_static = fake_fetch
APP = 'app-multipage'
def fresh_static(**kw):
    s = {'resume_readable': True, 'assessment': 'a', 'screening': 's'}; s.update(kw); return s

D._late_checked.clear(); D._STATIC_CACHE.clear()
s = fresh_static()
out = D._late_refresh(APP, s)
chk('已讀得到履歷、面談沒開始：第一次只記 key，不重抓', calls['fetch'] == 0 and out.get('_resume_key') == 'f1||2026-10-12 10:00:00', (calls, out))

D._late_checked.clear()
row['parsed_at'] = '2026-10-12 10:05:00'          # 第 2 頁到了，整組重 OCR，同一個檔案、parsed_at 變了
out = D._late_refresh(APP, s)
chk('同一個檔案、parsed_at 變了 → 整份重抓並換快取', calls['fetch'] == 1 and D._STATIC_CACHE.get(APP) is out and out['_resume_key'].endswith('10:05:00'), (calls, out))

D._late_checked.clear(); calls['fetch'] = 0
out2 = D._late_refresh(APP, out)
chk('parsed_at 沒再變 → 不重抓', calls['fetch'] == 0 and out2 is out)

# 30 秒節流
n0 = calls['sql']; D._late_refresh(APP, out); chk('30 秒內不重複查 D1', calls['sql'] == n0)

# 面談開始後不換
D._late_checked.clear(); calls['fetch'] = 0
row['interview_state'] = 'active'
s3 = fresh_static(_resume_key='f1||2026-10-12 10:05:00')
D._late_refresh(APP, s3)
chk('面談開始 → 記下 _interview_started', s3.get('_interview_started') is True)
D._late_checked.clear(); n0 = calls['sql']
row['parsed_at'] = '2026-10-12 10:30:00'
D._late_refresh(APP, s3)
chk('面談開始後：不再查 D1、不重抓（避免中途資料變動）', calls['sql'] == n0 and calls['fetch'] == 0, (calls,))

# 履歷還沒到（need_r）仍照 E30 運作
D._late_checked.clear(); calls['fetch'] = 0
row.update(resume_file_id=None, resume_url='', interview_state='not_started')
s4 = {'resume_readable': False, 'assessment': 'a', 'screening': 's'}
out = D._late_refresh(APP, s4)
chk('履歷還沒到：不重抓（E30 行為不變）', calls['fetch'] == 0 and out is s4)
D._late_checked.clear(); row['resume_file_id'] = 'f9'
out = D._late_refresh(APP, s4)
chk('履歷到了：整份重抓（E30 行為不變）', calls['fetch'] == 1 and out.get('resume_readable') is True)

def boom(sql): raise RuntimeError('d1 down')
D.d1 = boom; D._late_checked.clear()
try:
    D._late_refresh(APP, fresh_static()); chk('D1 失敗 → 沿用快取、不丟例外', True)
except Exception as e:
    chk('D1 失敗 → 沿用快取、不丟例外', False, e)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
