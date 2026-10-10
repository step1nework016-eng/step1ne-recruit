"""E23b 測試（記憶體 sqlite＋假的 claude／鎖，不連正式 D1、不呼叫 claude）：
 A. prewarm_candidates：pending_assessment 建立後就預熱（含各種不該預熱的情形）
 B. _post_prewarmed_opening：進房後才算好的預熱開場白由 daemon 直接貼（原子、只貼一次、保護特徵字眼不貼）
 C. do_prewarm：不佔面談的鎖／_busy；人已進房才算好的結果不存
 D. handle()：有預熱開場白時完全不呼叫 claude；沒有時照舊
 E. 主迴圈把預熱派到自己的名額
"""
import os, sys, sqlite3, datetime, json
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D

fails = []
def check(name, cond, detail=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))
    if not cond: fails.append(name)

logs = []
D.log = lambda m: logs.append(m)
DB = sqlite3.connect(':memory:'); DB.row_factory = sqlite3.Row
for ddl in (
    "CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, job_slug TEXT, status TEXT, interview_state TEXT, remind_at TEXT,"
    " created_at TEXT, prewarmed_opening TEXT, prewarmed_at TEXT, resume_file_id TEXT, resume_url_parsed_at TEXT, lock_expires_at TEXT)",
    "CREATE TABLE jobs (slug TEXT, seniority TEXT)",
    "CREATE TABLE reports (application_id TEXT)",
    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)",
):
    DB.execute(ddl)
def sdb(sql, *a, **k):
    cur = DB.execute(sql); rows = [dict(r) for r in cur.fetchall()] if cur.description else []; DB.commit(); return rows
def sdb_raw(sql, *a, **k):
    cur = DB.execute(sql); DB.commit(); return {'meta': {'changes': cur.rowcount}}
D.d1, D.d1_raw = sdb, sdb_raw
now = datetime.datetime.now()
T = lambda h: (now + datetime.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M:%S')
ENTRY = D.MK.ENTRY_MARKER
def app(i, status='pending_assessment', created=-0.1, resume='f1', parsed=None, state=None, warm=None):
    DB.execute("INSERT INTO applications (id,name,job_slug,status,interview_state,created_at,prewarmed_opening,resume_file_id,resume_url_parsed_at)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (i, i, 'jb', status, state, T(created), warm, resume, parsed)); DB.commit()
def msg(i, role, text):
    DB.execute("INSERT INTO messages (application_id,role,content,created_at) VALUES (?,?,?,?)", (i, role, text, T(0))); DB.commit()
def msgs_of(i): return [(r['role'], r['content']) for r in sdb(f"SELECT role, content FROM messages WHERE application_id='{i}' ORDER BY id")]
DB.execute("INSERT INTO jobs VALUES ('jb','mid')"); DB.commit()

print('\n[A] prewarm_candidates')
app('pa_new'); app('pa_link', resume=None, parsed=T(-0.05)); app('pa_nores', resume=None); app('pa_old', created=-5)
app('pa_active', state='active'); app('pa_warm', warm='["x"]'); app('rdy', status='ready'); app('other', status='rejected')
got = sorted(x['id'] for x in D.prewarm_candidates())
check('剛建立（pending_assessment）、履歷抓得到文字的 → 預熱；ready 照舊', got == ['pa_link', 'pa_new', 'rdy'], got)
check('履歷抓不到／超過 3 小時／已在面談／已預熱／其他狀態 → 不預熱', not ({'pa_nores', 'pa_old', 'pa_active', 'pa_warm', 'other'} & set(got)))
D.PREWARM_EARLY = False
check('關掉開關 → 回到只預熱 ready／預約', sorted(x['id'] for x in D.prewarm_candidates()) == ['rdy'])
D.PREWARM_EARLY = True

print('\n[B] _post_prewarmed_opening')
OPEN = ['您好，請問是林小安嗎？', '先跟您說明一下流程。', '這個職缺是獵頭顧問。']
DB.execute("DELETE FROM messages")
app('b1', warm=json.dumps(OPEN)); msg('b1', 'candidate', ENTRY)
check('只有進房標記＋有預熱 → 貼上 3 則', D._post_prewarmed_opening('b1', 'b1') is True and [r for r, _ in msgs_of('b1')] == ['candidate', 'assistant', 'assistant', 'assistant'])
check('貼上的內容與順序正確', [c for r, c in msgs_of('b1') if r == 'assistant'] == OPEN)
check('再叫一次不會貼第二份（已經有 assistant 訊息）', D._post_prewarmed_opening('b1', 'b1') is False and len(msgs_of('b1')) == 4)
app('b2', warm=json.dumps(OPEN)); msg('b2', 'candidate', ENTRY); msg('b2', 'assistant', 'Worker 已經貼過了')
check('Worker 已先貼了 → 不再貼', D._post_prewarmed_opening('b2', 'b2') is False and len(msgs_of('b2')) == 2)
app('b3', warm=json.dumps(OPEN)); msg('b3', 'candidate', ENTRY); msg('b3', 'candidate', '我先問一下薪水')
check('候選人已經說話了 → 不貼（走正常回覆）', D._post_prewarmed_opening('b3', 'b3') is False)
app('b4'); msg('b4', 'candidate', ENTRY)
check('沒有預熱 → 不貼', D._post_prewarmed_opening('b4', 'b4') is False)
app('b5', warm='不是 JSON'); msg('b5', 'candidate', ENTRY)
check('預熱內容壞掉 → 不貼、不丟例外', D._post_prewarmed_opening('b5', 'b5') is False)
bad = D.law5_hits
D.law5_hits = lambda t: ['年齡'] if '年紀' in t else []
app('b6', warm=json.dumps(['您年紀多大？'])); msg('b6', 'candidate', ENTRY)
check('含就服法保護特徵字眼 → 不貼（交給現場生成那條有重生成的路）', D._post_prewarmed_opening('b6', 'b6') is False and len(msgs_of('b6')) == 1)
D.law5_hits = bad
app('b7', warm=json.dumps(['單句'])); msg('b7', 'candidate', ENTRY)
check('只有 1 則也能貼', D._post_prewarmed_opening('b7', 'b7') is True and len(msgs_of('b7')) == 2)

print('\n[C] do_prewarm')
D.context_for = lambda a: {'job': {}}; D.build_prompt = lambda c, s: 'P'; D.skill = lambda *a: 'S'
D._snapshot_session_files = lambda: {}; D.log_token_usage = lambda *a: None
D.run_claude = lambda *a, **k: {'messages': ['嗨', '你好']}
released = []; D.release_lock = lambda i: released.append(i)
app('c1'); D._prewarm_busy.add('c1'); D._busy.discard('c1')
D._STATIC_CACHE['c1'] = {'job': {}, 'note': '交卷前抓的 static（沒有測驗結果）'}
D.do_prewarm({'id': 'c1', 'name': 'c1'})
row = sdb("SELECT prewarmed_opening FROM applications WHERE id='c1'")[0]
check('算好 → 存進 prewarmed_opening', json.loads(row['prewarmed_opening']) == ['嗨', '你好'])
check('預熱結束後清掉靜態快取（避免整場讀不到後來才有的測驗／初篩）', 'c1' not in D._STATIC_CACHE)
check('不碰面談的 DB 鎖（沒有 release_lock），_prewarm_busy 清掉', released == [] and 'c1' not in D._prewarm_busy)
app('c2', state='active'); D._prewarm_busy.add('c2')
D.do_prewarm({'id': 'c2', 'name': 'c2'})
check('人已經進房才算好 → 不存、log 說明', sdb("SELECT prewarmed_opening p FROM applications WHERE id='c2'")[0]['p'] is None and any('已經進房' in l for l in logs))
D._busy.add('c3'); app('c3'); D._prewarm_busy.add('c3')
D.do_prewarm({'id': 'c3', 'name': 'c3'})
check('預熱不會動到 _busy（面談處理中的標記）', 'c3' in D._busy); D._busy.discard('c3')

print('\n[D] handle()：有預熱 → 不叫 claude')
calls = {'claude': 0}
def boom(*a, **k): calls['claude'] += 1; raise RuntimeError('不該呼叫 claude')
D.run_claude = boom
D._mark_host = lambda *a: None; D._waited_sec = lambda r: 0; D.snap_signals = lambda *a: None
for i in ('h1',): app(i, status='ready', warm=json.dumps(OPEN)); msg(i, 'candidate', ENTRY)
D._busy.add('h1')
D.handle({'id': 'h1', 'name': 'h1', 'job_slug': 'jb'})
check('有預熱開場白 → 直接貼、完全沒叫 claude', calls['claude'] == 0 and [c for r, c in msgs_of('h1') if r == 'assistant'] == OPEN)
check('handle 結束後清掉 _busy', 'h1' not in D._busy)
app('h2', status='ready'); msg('h2', 'candidate', ENTRY); D._busy.add('h2')
D.tg = lambda *a, **k: None
D.context_for = lambda a: {'job': {}, 'application': {}, 'conversation': [{'role': 'candidate', 'content': ENTRY}]}
D.talk_skill = lambda ctx: 'S'; D._notify_cross_job_interest = lambda *a: None; D._max_msg_id = lambda a: 0
D.handle({'id': 'h2', 'name': 'h2', 'job_slug': 'jb'})
check('沒有預熱 → 照舊走原本流程（會去叫 claude；這裡假的 claude 會丟例外、不貼預熱）', calls['claude'] >= 1)

print('\n[E] 主迴圈把預熱派到自己的名額')
src = open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read()
check('迴圈有 do_prewarm 專屬分支（_prewarm_busy／MAX_PREWARM_PARALLEL，不走 acquire_lock）', 'if fn is do_prewarm:' in src and 'MAX_PREWARM_PARALLEL' in src)
i = src.index('if fn is do_prewarm:'); j = src.index('if fn is do_plan:')
check('專屬分支裡沒有 acquire_lock／_busy.add', 'acquire_lock' not in src[i:j] and '_busy.add' not in src[i:j].replace('_prewarm_busy.add', ''))

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
