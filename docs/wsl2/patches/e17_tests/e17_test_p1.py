"""E17-1 測試：用記憶體裡的 sqlite 跑「真正的」daemon 程式（handle()、active_sessions()、context_for()…），
D1 與 claude 都換成假的，所以不碰正式資料庫、也不呼叫 claude。
時間縮放：真實 40 秒 → 0.4 秒；「LLM 卡 60 秒」→ 0.6 秒；「卡 10 秒」→ 0.1 秒。"""
import os, sys, time, sqlite3, threading, random, json
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D
import interview_markers as MK

DB = sqlite3.connect(':memory:', check_same_thread=False)
DB.row_factory = sqlite3.Row
DBLOCK = threading.Lock()
for ddl in (
    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)",
    "CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, job_slug TEXT, interview_started_at TEXT, interview_state TEXT,"
    " start_notified_at TEXT, job_title TEXT, hold_until TEXT, interview_plan_json TEXT, lock_expires_at TEXT)",
    "CREATE TABLE jobs (slug TEXT, interview_language TEXT, seniority TEXT)",
    "CREATE TABLE reports (application_id TEXT)",
):
    DB.execute(ddl)
DB.commit()


def fake_d1(sql, *a, **k):
    with DBLOCK:
        try:
            cur = DB.execute(sql)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            DB.commit()
            return rows
        except sqlite3.Error as e:
            if 'no such table' in str(e) or 'no such column' in str(e):
                return []
            raise


D.d1 = fake_d1
D.log = lambda m: None
logs = []
NOW = lambda: time.strftime('%Y-%m-%d %H:%M:%S')


def msg(app, role, content, ts=None):
    fake_d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES ({D.q(app)}, {D.q(role)}, {D.q(content)}, {D.q(ts or NOW())})")


def thread(app):
    return fake_d1(f"SELECT id, role, content FROM messages WHERE application_id={D.q(app)} ORDER BY id")


def short(t):
    return [('候' if r['role'] == 'candidate' else ('過' if MK.is_transition(r['content']) else ('歉' if r['content'].startswith('不好意思') else '阿'))) for r in t]


ok_all = True


def check(name, cond, detail=''):
    global ok_all
    ok_all &= bool(cond)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))


TR = MK.TRANSITION_TEXTS[0]
D.TRANSITION_AFTER_SEC = 0.4          # 縮放：= 真實 40 秒

# ── 1. 判斷「誰在等」「訊息數」「對話歷史」時，過渡語要當作不存在 ──
print('\n[1] 送了過渡語之後，誰在等／訊息數／給 LLM 的歷史')
A = 'app-A'
fake_d1(f"INSERT INTO applications (id,name,job_slug,interview_state,interview_started_at) VALUES ('{A}','甲','j','active','2026-10-08 10:00:00')")
msg(A, 'assistant', '您好'); msg(A, 'candidate', '我的回答'); msg(A, 'assistant', TR)
rows = D.active_sessions()
r = [x for x in rows if x['id'] == A][0]
check('active_sessions：最後一則仍是 candidate（阿財還會去回他）', r['last_role'] == 'candidate', r['last_role'])
check('active_sessions：訊息數不含過渡語（3 則裡只算 2）', r['n'] == 2, r['n'])
check('pending() 還撈得到他', len(D.pending(rows)) == 1)
D._fetch_static = lambda app_id: {'job': {'slug': 'j'}, 'application': {}}
ctx = D.context_for(A)
check('context_for 的對話歷史沒有過渡語', all(not MK.is_transition(m['content']) for m in ctx['conversation']) and len(ctx['conversation']) == 2,
      [m['content'][:6] for m in ctx['conversation']])
fake_d1(f"UPDATE applications SET interview_state='paused' WHERE id='{A}'")
check('post_wrap_questions：收尾後人選又問、中間夾著過渡語，仍判成「在等回覆」',
      any(x['id'] == A for x in D.post_wrap_questions()))
check('paused_sessions.last_at 取的是人選那則', [x for x in D.paused_sessions() if x['id'] == A][0]['last_at'])
# 反例：真的 assistant 回覆之後，才算輪到人選
msg(A, 'assistant', '正式回覆')
fake_d1(f"UPDATE applications SET interview_state='active' WHERE id='{A}'")
r = [x for x in D.active_sessions() if x['id'] == A][0]
check('正式回覆之後最後一則變 assistant', r['last_role'] == 'assistant')

# ── 2. SQL 守衛本身 ──
print('\n[2] 守衛：只在「最後一則是人選、不是進房標記、這輪還沒送過」才寫入')
def send(app):
    fake_d1(MK.sql_send_transition(D.q(app), D.q(NOW())))
B = 'app-B'
msg(B, 'candidate', MK.ENTRY_MARKER); send(B)
check('最後一則是「進入面談室」標記 → 不送', short(thread(B)) == ['候'], short(thread(B)))
C = 'app-C'
msg(C, 'candidate', '答'); msg(C, 'assistant', '正式回覆'); send(C)
check('最後一則是 assistant（已被回）→ 不送', short(thread(C)) == ['候', '阿'])
E = 'app-E'
msg(E, 'candidate', '答'); send(E); send(E); send(E)
check('連按三次只寫入一次', short(thread(E)) == ['候', '過'], short(thread(E)))
msg(E, 'candidate', '又講一句'); send(E)
check('同一輪（還沒有新的正式回覆）人選又講話 → 不再送第二句', short(thread(E)) == ['候', '過', '候'], short(thread(E)))
msg(E, 'assistant', '正式回覆'); msg(E, 'candidate', '下一輪的回答'); send(E)
check('正式回覆之後的下一輪 → 可以再送一次', short(thread(E)) == ['候', '過', '候', '阿', '候', '過'], short(thread(E)))

# ── 3. 用真的 handle() 跑：LLM 卡 60 秒／10 秒／兩個程序同時 ──
print('\n[3] 真的 handle()：LLM 卡住不同久')
D._mark_host = lambda *a, **k: None
D._notify_cross_job_interest = lambda *a, **k: None
D.build_prompt = lambda ctx, skill: 'P'
D.talk_skill = lambda ctx: 'S'
D._snapshot_session_files = lambda: {}
D.log_token_usage = lambda *a, **k: None
D.law5_hits = lambda m: []
D.snap_signals = lambda *a, **k: None
D.release_lock = lambda *a, **k: None
D.tg = lambda *a, **k: None
D.finish = lambda *a, **k: None
D.context_for = lambda app_id, cache=True: {'conversation': [m for m in fake_d1(
    f"SELECT role, content, created_at FROM messages WHERE application_id={D.q(app_id)} AND {MK.sql_not_transition('content')} ORDER BY id")],
    'job': {'slug': 'j'}, 'application': {}}


def run_case(app, llm_sec, fail=False, other_reply_at=None, post_msg_at=None):
    fake_d1(f"INSERT OR IGNORE INTO applications (id,name,job_slug,interview_state) VALUES ('{app}','{app}','j','active')")
    msg(app, 'assistant', '上一個問題'); msg(app, 'candidate', '人選的回答')

    def fake_claude(prompt, **k):
        time.sleep(llm_sec)
        if fail:
            raise RuntimeError('claude 呼叫連續失敗')
        return {'messages': ['正式回覆'], 'end': False}
    D.run_claude = fake_claude
    ths = []
    if other_reply_at is not None:
        t = threading.Timer(other_reply_at, lambda: msg(app, 'assistant', '顧問手動回覆')); t.start(); ths.append(t)
    if post_msg_at is not None:
        t = threading.Timer(post_msg_at, lambda: msg(app, 'candidate', '等不及又補一句')); t.start(); ths.append(t)
    D.handle({'id': app, 'name': app, 'job_slug': 'j'})
    time.sleep(0.2)
    for t in ths: t.join()
    return short(thread(app))


s = run_case('c60', 0.6)
check('卡 60 秒（0.6）→ 過渡語恰好 1 次，且在正式回覆之前', s == ['阿', '候', '過', '阿'], s)
s = run_case('c10', 0.1)
check('卡 10 秒（0.1）→ 不送過渡語', s == ['阿', '候', '阿'], s)
s = run_case('c200', 1.3)
check('卡很久（1.3 = 130 秒）→ 仍然只送 1 次', s.count('過') == 1 and s[-1] == '阿', s)
s = run_case('cfail', 0.6, fail=True)
check('卡了又失敗 → 過渡語後仍會寫道歉（最後一則真實訊息是人選，不是 assistant）', s == ['阿', '候', '過', '歉'], s)
s = run_case('cother', 0.9, other_reply_at=0.2)
check('別人（顧問）在第 20 秒先回了 → 不送過渡語', '過' not in s, s)
s = run_case('cnew', 0.9, post_msg_at=0.1)
check('算的時候人選又補一句 → 過渡語最多 1 次；舊回覆被丟棄（stale）時沒有正式回覆寫入', s.count('過') <= 1 and s.count('阿') == 1, s)

# 兩個程序同時處理同一場（主力機＋備援機）
fake_d1("INSERT INTO applications (id,name,job_slug,interview_state) VALUES ('c2p','c2p','j','active')")
msg('c2p', 'assistant', '上一個問題'); msg('c2p', 'candidate', '人選的回答')
tr = [D._Transition('c2p', 'c2p', 0), D._Transition('c2p', 'c2p', 0)]
for t in tr: t.start()
time.sleep(0.7)
for t in tr: t.close()
check('兩個程序同時（各自計時）→ 過渡語只寫入 1 句', short(thread('c2p')).count('過') == 1, short(thread('c2p')))

# ── 4. 競態：正式回覆與過渡語在同一瞬間 ──
print('\n[4] 競態：300 次，正式回覆時間隨機落在過渡語門檻前後')
D.TRANSITION_AFTER_SEC = 0.03
D.TRANSITION_MIN_DELAY_SEC = 0.0
bad_order = multi = sent = 0
for i in range(300):
    app = f'race{i}'
    msg(app, 'assistant', '上一個問題'); msg(app, 'candidate', '回答')
    t = D._Transition(app, app, 0); t.start()
    time.sleep(random.uniform(0.012, 0.050))
    t.close()
    msg(app, 'assistant', '正式回覆')          # 正式回覆（普通 INSERT，沒有任何守衛）
    time.sleep(0.045)                           # 讓任何殘留的計時器跑完
    s = short(thread(app))
    n_tr = s.count('過')
    sent += n_tr
    if n_tr > 1: multi += 1
    # 正式回覆是最後一則；過渡語若有，必須在它前面
    if s[-1] != '阿' or ('過' in s and s.index('過') > len(s) - 2): bad_order += 1
check(f'300 次裡：沒有任何一次過渡語落在正式回覆「之後」或寫了兩句（有送出的 {sent} 次、沒送的 {300 - sent} 次）',
      bad_order == 0 and multi == 0 and 20 < sent < 280, f'順序錯={bad_order} 重複={multi} 送出={sent}')

print('\n全部通過' if ok_all else '\n有失敗')
sys.exit(0 if ok_all else 1)
