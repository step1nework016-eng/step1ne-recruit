#!/usr/bin/env python3
"""2026-10-24 AI 卡住時背景重試（謝佳恩事件）——離線模擬，用記憶體 SQLite 代替 D1，真的跑 SQL 守衛。

不連正式 D1、不打 claude、不推 TG（全部換成假的）；應徵者姓名一律「測試」。
情境 A：卡兩次後接上 → 只一句等待語、不道歉、TG 一則、第二次用備用模型、接上第一句「抱歉讓您久等了」。
情境 B：等滿 10 分鐘仍接不上 → 道歉一次（整場只一次）、TG「需要顧問接手」一則；再失敗不再道歉；接上後先說抱歉久等。
"""
import datetime
import os
import sqlite3
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
import interview_markers as MK

bad = 0


def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}'))
    bad += (not c)


db = sqlite3.connect(':memory:', check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = threading.Lock()
db.executescript("""
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT);
CREATE TABLE notify_throttle (key TEXT PRIMARY KEY, last_at TEXT);
CREATE TABLE applications (id TEXT PRIMARY KEY, lock_expires_at TEXT);
CREATE TABLE reports (id INTEGER PRIMARY KEY, application_id TEXT);
""")


def fake_d1_raw(sql):
    with db_lock:
        cur = db.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        db.commit()
        return {'results': rows, 'meta': {'changes': cur.rowcount if cur.rowcount > 0 else 0}}


TG, LOGS, CALLS = [], [], []
D.d1_raw = fake_d1_raw
D.d1 = lambda sql: fake_d1_raw(sql)['results']
D.tg = lambda text, *a, **k: TG.append(text)
D.log = lambda m: LOGS.append(m)
D._mark_host = lambda *a, **k: None
D._post_prewarmed_opening = lambda *a, **k: False
D.context_for = lambda app_id: {'application': {'id': app_id, 'note': ''}, 'conversation': [{'role': 'candidate', 'content': 'x'}]}
D._reschedule_requested = lambda conv: False
D._notify_cross_job_interest = lambda *a, **k: None
D.build_prompt = lambda ctx, skill: 'PROMPT'
D.talk_skill = lambda ctx: ''
for f in ('_pace_hints', '_technique_hints', '_implied_hints'):
    setattr(D, f, lambda ctx: '')
D._quick_hint = lambda app: ''
D._snapshot_session_files = lambda: None
D.log_token_usage = lambda *a, **k: None
D._newer_candidate_msgs = lambda *a, **k: 0
D._one_message = lambda msgs, ctx: msgs
D._save_quick_replies = lambda *a, **k: None
D.snap_signals = lambda *a, **k: None
D._resumed_after_idle_wrap = lambda a: False
D.finish = lambda *a, **k: None
D.TRANSITION_AFTER_SEC = 1           # 等待過渡語：測試時 1 秒就送（正式 60 秒）

PLAN = []    # 每次 run_claude 依序取：'fail' 或 'ok'


def fake_run_claude(prompt, first_try=None, total=None, model=None):
    CALLS.append({'model': model, 'total': total})
    act = PLAN.pop(0)
    if act == 'fail':
        time.sleep(1.6)                # 讓等待過渡語的計時器有機會觸發
        raise RuntimeError('claude 呼叫連續失敗（逾時或回覆不是合法 JSON），放棄這輪：模擬逾時')
    return {'messages': ['那您期望的薪資大概是多少呢？'], 'end': False}


D.run_claude = fake_run_claude


def ts(ago):
    return (datetime.datetime.now() - datetime.timedelta(seconds=ago)).strftime('%Y-%m-%d %H:%M:%S')


def setup(app_id, cand_ago):
    db.execute("INSERT INTO applications (id) VALUES (?)", (app_id,))
    db.execute("INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)",
               (app_id, 'assistant', '想請教您對薪資的想法？', ts(cand_ago + 30)))
    db.execute("INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)",
               (app_id, 'candidate', '我學習能力好，工作效率高', ts(cand_ago)))
    db.commit()


def age_round(app_id, ago):
    """把這一輪人選那則訊息改成 ago 秒前（模擬時間過去）。"""
    db.execute("UPDATE messages SET created_at=? WHERE application_id=? AND role='candidate' AND id=("
               "SELECT MIN(id) FROM messages WHERE application_id=? AND role='candidate' AND id>("
               "SELECT MAX(id) FROM messages WHERE application_id=? AND role='assistant' AND content='想請教您對薪資的想法？'))",
               (ts(ago), app_id, app_id, app_id))
    db.commit()


def run(app_id):
    db.execute("UPDATE applications SET lock_expires_at=NULL WHERE id=?", (app_id,))
    D._busy.add(app_id)
    D.handle({'id': app_id, 'name': '測試', 'job_slug': 'test-job', 'job_title': '測試職缺', 'last_at': ts(0)})
    time.sleep(0.2)


def msgs(app_id):
    return [dict(r) for r in db.execute("SELECT role, content FROM messages WHERE application_id=? ORDER BY id", (app_id,))]


def lock(app_id):
    return db.execute("SELECT lock_expires_at FROM applications WHERE id=?", (app_id,)).fetchone()[0]


apology = MK.APOLOGY_TEXTS[0]

# ── 情境 A：卡兩次，第三次接上 ──
A = 'test-stuck-A'
setup(A, 0)
TG.clear(); CALLS.clear()
PLAN[:] = ['fail']
run(A)
m = msgs(A)
chk('A1 失敗：沒有送道歉', not any(x['content'] == apology for x in m), m)
chk('A1 失敗：送了一句等待語', sum(MK.is_transition(x['content']) and x['content'] != apology for x in m) == 1, m)
chk('A1 失敗：TG 推一則「已在背景重試」', len(TG) == 1 and '已在背景重試' in TG[0], TG)
chk('A1 失敗：鎖設到之後（約 30 秒）才到期，不是馬上放掉', lock(A) is not None and lock(A) > ts(-20), lock(A))
chk('A1 第一次用主模型', CALLS[-1]['model'] is None, CALLS)

age_round(A, 250)                       # 模擬 4 分鐘後另一台接手重試
PLAN[:] = ['fail']
run(A)
m = msgs(A)
chk('A2 再失敗：仍沒有道歉', not any(x['content'] == apology for x in m), m)
chk('A2 再失敗：等待語沒有重複', sum(MK.is_transition(x['content']) for x in m) == 1, m)
chk('A2 再失敗：TG 沒有再推', len(TG) == 1, TG)
chk('A2 這次改用備用模型 ' + D.FALLBACK_TALK_MODEL, CALLS[-1]['model'] == D.FALLBACK_TALK_MODEL, CALLS)
chk('A2 時間預算不超過剩下的 10 分鐘', CALLS[-1]['total'] is not None and CALLS[-1]['total'] <= 600 - 250 + 1, CALLS)

age_round(A, 320)
PLAN[:] = ['ok']
run(A)
m = msgs(A)
chk('A3 接上：第三次回主模型（備用只用一次）', CALLS[-1]['model'] is None, CALLS)
chk('A3 接上：第一句是「抱歉讓您久等了，我們繼續。」', m[-1]['role'] == 'assistant' and m[-1]['content'].startswith(D.RESUME_PREFIX), m[-1])
chk('A3 接上：整輪沒道歉、TG 只有一則', not any(x['content'] == apology for x in m) and len(TG) == 1, TG)
chk('A3 接上：鎖已放掉', lock(A) is None, lock(A))
print('   A 人選看到的：', [x['content'][:30] for x in m])

# ── 情境 B：等滿 10 分鐘仍接不上 ──
B = 'test-stuck-B'
setup(B, 0)
TG.clear(); CALLS.clear()
PLAN[:] = ['fail']
run(B)
chk('B1 失敗：TG 一則「已在背景重試」、不道歉',
    len(TG) == 1 and not any(x['content'] == apology for x in msgs(B)), (TG, msgs(B)))
age_round(B, 560)
PLAN[:] = ['fail']
run(B)
m = msgs(B)
chk('B2 等滿約 10 分鐘仍失敗：送一次道歉', sum(x['content'] == apology for x in m) == 1, m)
chk('B2 TG 推「需要顧問接手」（第 2 則，不同問題）', len(TG) == 2 and '需要顧問接手' in TG[1], TG)
chk('B2 鎖 2 分鐘後到期（背景續試）', lock(B) is not None and lock(B) > ts(-100), lock(B))
age_round(B, 700)
PLAN[:] = ['fail']
run(B)
m = msgs(B)
chk('B3 再失敗：不再道歉、不再推 TG', sum(x['content'] == apology for x in m) == 1 and len(TG) == 2, (m, TG))
# 人選道歉後又講一句（新的一輪），還是接不上、等很久 → 整場仍不再道歉
db.execute("INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)",
           (B, 'candidate', '請問還在嗎？', ts(5)))
db.commit()
age_round(B, 900)
PLAN[:] = ['fail']
run(B)
m = msgs(B)
chk('B4 同一輪人選又追問、仍失敗：整場還是只有一次道歉', sum(x['content'] == apology for x in m) == 1, m)
PLAN[:] = ['ok']
run(B)
m = msgs(B)
chk('B5 恢復：第一句先說抱歉久等', m[-1]['content'].startswith(D.RESUME_PREFIX), m[-1])
chk('B5 恢復：TG 推一則「已重新接上」（顧問被叫去接手過，要讓他知道）', len(TG) == 3 and '已重新接上' in TG[2], TG)
PLAN[:] = ['ok']
db.execute("INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)",
           (B, 'candidate', '大概四萬五', ts(1)))
db.commit()
run(B)
m = msgs(B)
chk('B6 下一輪正常：不再加「抱歉久等」前綴', not m[-1]['content'].startswith(D.RESUME_PREFIX), m[-1])
print('   B 人選看到的：', [x['content'][:30] for x in m])
print('   TG：', [t.split('\n')[0] for t in TG])

print(f'\n失敗 {bad} 項')
sys.exit(1 if bad else 0)
