"""E24 測試（記憶體 sqlite＋假 claude，不連正式 D1、不呼叫 claude）：
 A. _clean_out／寫出去的訊息全形標點    B. _pace_hints：起手式重複、問題預算
 C. 題目計畫：題數上限、全形、verify 旗標   D. 題庫讀進來就清成全形
 E. 預熱開場白存進去前清成全形           F. 提示詞新規則在不在、SKILL 路徑可覆蓋
"""
import os, sys, sqlite3, datetime, json
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D
import cjk_punct as CP

fails = []
def check(name, cond, detail=''):
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))
    if not cond: fails.append(name)

logs = []; D.log = lambda m: logs.append(m)
DB = sqlite3.connect(':memory:'); DB.row_factory = sqlite3.Row
for ddl in (
    "CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, job_slug TEXT, status TEXT, interview_state TEXT, created_at TEXT,"
    " prewarmed_opening TEXT, prewarmed_at TEXT, interview_plan_json TEXT, interview_plan_at TEXT)",
    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)",
    "CREATE TABLE reports (application_id TEXT)",
    "CREATE TABLE job_expertise (job_slug TEXT, domain TEXT, topics_json TEXT, questions_json TEXT, blockers_json TEXT, ladder_json TEXT,"
    " start_level INT, expected_level INT, workstyle_json TEXT, screen_tier TEXT, acai_v2 INT)",
    "CREATE TABLE job_card_profile (job_slug TEXT, masked_summary_text TEXT)",
):
    DB.execute(ddl)
def sdb(sql, *a, **k):
    cur = DB.execute(sql); rows = [dict(r) for r in cur.fetchall()] if cur.description else []; DB.commit(); return rows
def sdb_raw(sql, *a, **k):
    cur = DB.execute(sql); DB.commit(); return {'meta': {'changes': cur.rowcount}}
D.d1, D.d1_raw = sdb, sdb_raw
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def app(i, state=None, warm=None):
    DB.execute("INSERT INTO applications (id,name,job_slug,status,interview_state,created_at,prewarmed_opening) VALUES (?,?,?,?,?,?,?)",
               (i, i, 'jb', 'ready', state, now, warm)); DB.commit()
def msg(i, role, text):
    DB.execute("INSERT INTO messages (application_id,role,content,created_at) VALUES (?,?,?,?)", (i, role, text, now)); DB.commit()
def asst(i): return [r['content'] for r in sdb(f"SELECT content FROM messages WHERE application_id='{i}' AND role='assistant' ORDER BY id")]

print('\n[A] 寫出去的訊息全形標點')
check('_clean_out：半形 → 全形', D._clean_out('好,了解,您通勤方便嗎?') == '好，了解，您通勤方便嗎？')
check('_clean_out：數字、網址不動', D._clean_out('約10:30見,網址 https://a.com/x?y=1') == '約10:30見，網址 https://a.com/x?y=1')
orig = CP.fix_cjk_punct
CP.fix_cjk_punct = lambda t: (_ for _ in ()).throw(RuntimeError('x'))
check('清洗壞掉 → 原文照送、不丟例外', D._clean_out('好,了解') == '好,了解')
CP.fix_cjk_punct = orig
# handle() 寫出去
D.context_for = lambda a: {'job': {}, 'application': {}, 'conversation': [{'role': 'candidate', 'content': D.MK.ENTRY_MARKER}], 'plan': {}}
D.build_prompt = lambda c, s: 'P'; D.talk_skill = lambda c: 'S'; D._notify_cross_job_interest = lambda *a: None
D._max_msg_id = lambda a: 0; D._newer_candidate_msgs = lambda *a: 0; D._mark_host = lambda *a: None; D._waited_sec = lambda r: 0
D.snap_signals = lambda *a: None; D.release_lock = lambda i: None; D.log_token_usage = lambda *a: None; D._snapshot_session_files = lambda: {}
D.run_claude = lambda *a, **k: {'messages': ['好,了解,11月可以到職。', '您目前住的地方通勤方便嗎?'], 'end': False}
app('h1'); msg('h1', 'candidate', D.MK.ENTRY_MARKER); D._busy.add('h1')
D.handle({'id': 'h1', 'name': 'h1', 'job_slug': 'jb'})
check('handle() 寫進資料庫的是全形', asst('h1') == ['好，了解，11月可以到職。', '您目前住的地方通勤方便嗎？'], asst('h1'))
app('h2', warm=json.dumps(['您好,請問是林小安嗎?', '我是阿財,先說明流程:大約30分鐘。'], ensure_ascii=False)); msg('h2', 'candidate', D.MK.ENTRY_MARKER)
check('進房後才算好的預熱開場白直接貼上時也清成全形', D._post_prewarmed_opening('h2', 'h2') is True and asst('h2') == ['您好，請問是林小安嗎？', '我是阿財，先說明流程：大約30分鐘。'], asst('h2'))

print('\n[B] _pace_hints')
def conv(*pairs): return [{'role': r, 'content': c} for r, c in pairs]
c1 = conv(('assistant', '您好'), ('candidate', 'a'), ('assistant', '好，了解，A。'), ('candidate', 'b'), ('assistant', '好，了解，B。'), ('candidate', 'c'), ('assistant', '好，了解，C。'))
h = D._pace_hints({'conversation': c1, 'job': {'seniority': 'mid'}, 'plan': {}})
check('「好」連用 → 提醒起手式重複（含次數）', '起手式重複' in h and '「好」×3' in h, h[:80])
check('起手式變化多 → 沒有重複提醒', '起手式重複' not in D._pace_hints({'conversation': conv(('assistant', '您好'), ('assistant', '嗯嗯，A'), ('assistant', '懂了，B'), ('assistant', 'OK，C')), 'job': {}, 'plan': {}}))
check('少於 3 則 assistant → 不提醒', D._pace_hints({'conversation': conv(('assistant', '好，A'), ('assistant', '好，B')), 'job': {}, 'plan': {}}) == '')
def qconv(n): return conv(*[('assistant', f'第{i}題您怎麼看？') for i in range(n)])
plan10 = {'questions': [{'q': 'x'}] * 10}
check('問了 9 則（預算 14）→ 沒有預算提醒', '預算' not in D._pace_hints({'conversation': qconv(9), 'job': {'seniority': 'mid'}, 'plan': plan10}))
check('問了 11 則（預算 14，剩 3 內）→ 快到了', '快到了' in D._pace_hints({'conversation': qconv(11), 'job': {'seniority': 'mid'}, 'plan': plan10}))
check('問了 14 則 → 預算用完，不再開新題', '用完了' in D._pace_hints({'conversation': qconv(14), 'job': {'seniority': 'mid'}, 'plan': plan10}))
check('計畫 12 題也只算 10＋4＝14', '用完了' in D._pace_hints({'conversation': qconv(14), 'job': {}, 'plan': {'questions': [{'q': 'x'}] * 12}}))
check('中高階（senior）不設上限', '預算' not in D._pace_hints({'conversation': qconv(30), 'job': {'seniority': 'senior'}, 'plan': plan10}))
check('沒有問號的訊息不算題數', '預算' not in D._pace_hints({'conversation': conv(*[('assistant', '好，了解。') for i in range(20)]), 'job': {}, 'plan': plan10}))
check('半形問號也算', '用完了' in D._pace_hints({'conversation': conv(*[('assistant', f'第{i}題呢?') for i in range(14)]), 'job': {}, 'plan': plan10}))

print('\n[C] 題目計畫')
check('_plan_cap：mid 10、senior 12、沒資料 10', (D._plan_cap({'job': {'seniority': 'mid'}}), D._plan_cap({'job': {'seniority': 'senior'}}), D._plan_cap({})) == (10, 12, 10))
D.skill = lambda *a: 'S'
big = {'questions': [{'q': f'您目前這份工作,第{i}題?', 'why': 'w', 'followup': '再說一下,好嗎?', 'verify': i == 3} for i in range(14)],
       'faq': [{'q': '加班嗎?', 'a': '這題要轉給顧問'}]}
D.run_claude = lambda *a, **k: big
D.context_for = lambda a: {'job': {'seniority': 'mid'}}
app('p1'); D._plan_busy.add('p1')
D.do_plan({'id': 'p1', 'name': 'p1'})
pl = json.loads(sdb("SELECT interview_plan_json p FROM applications WHERE id='p1'")[0]['p'])
check('mid：14 題 → 只留 10 題', len(pl['questions']) == 10, len(pl['questions']))
check('計畫裡的題目、追問、faq 都是全形', all(CP.count_halfwidth(x['q'] + x['followup']) == 0 for x in pl['questions']) and CP.count_halfwidth(pl['faq'][0]['q']) == 0)
check('verify 旗標保留', pl['questions'][3].get('verify') is True)
D.context_for = lambda a: {'job': {'seniority': 'senior'}}
app('p2'); D._plan_busy.add('p2'); D.do_plan({'id': 'p2', 'name': 'p2'})
check('senior：14 題 → 留 12 題', len(json.loads(sdb("SELECT interview_plan_json p FROM applications WHERE id='p2'")[0]['p'])['questions']) == 12)

print('\n[D] 題庫讀進來就清成全形')
DB.execute("INSERT INTO job_expertise VALUES ('jb','獵頭','[{\"name\":\"主題一,客戶需求\"}]','[\"您有沒有跟前輩開過會?那時聽到什麼?\"]','[\"通勤:很遠\"]',NULL,2,3,NULL,NULL,0)"); DB.commit()
src = D.fetch_job_understanding_sources('jb')
dump = json.dumps(src['expertise'], ensure_ascii=False) + json.dumps(src['blockers'], ensure_ascii=False)
check('專業題庫與到職障礙的半形標點被清成全形', CP.count_halfwidth(dump) == 0 and '主題一，客戶需求' in dump, dump[:80])

print('\n[E] 預熱開場白存進去前清成全形')
D.context_for = lambda a: {'job': {}}; D.build_prompt = lambda c, s: 'P'
D.run_claude = lambda *a, **k: {'messages': ['您好,請問是林小安嗎?', '先說明流程:大約30分鐘。']}
app('w1'); D._prewarm_busy.add('w1'); D.do_prewarm({'id': 'w1', 'name': 'w1'})
check('存進 prewarmed_opening 的是全形（Worker 進房直接貼這份）', json.loads(sdb("SELECT prewarmed_opening p FROM applications WHERE id='w1'")[0]['p']) == ['您好，請問是林小安嗎？', '先說明流程：大約30分鐘。'])

print('\n[F] 提示詞新規則與路徑覆蓋')
check('TALK_LITE：不要每輪複誦、禁用「我記下來了」、必追問、全形標點', all(k in D.TALK_LITE for k in ('不要每一輪都複述', '我記下來了', '期待落差', '全形')))
check('TALK_LITE 不再叫他「簡短複述你聽到的重點」', '簡短複述你聽到的重點' not in D.TALK_LITE)
check('PLAN_PROMPT：8–10 題、verify、薪資依據、全形', all(k in D.PLAN_PROMPT for k in ('8–10', '"verify"', '追問依據', '全形標點')))
ctxp = {'application': {}, 'job': {}, 'conversation': [{'role': 'candidate', 'content': 'hi'}], 'plan': {'questions': [{'q': '履歷寫招募120人，怎麼做到的？', 'verify': True}, {'q': '普通題'}]}}
txt = D.build_prompt(ctxp, 'S') if D.build_prompt.__name__ != '<lambda>' else ''
check('SKILL_PATH 預設與 INTERVIEW_SKILL_PATH 覆蓋寫法都在', 'INTERVIEW_SKILL_PATH' in open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read())

print('\n[G] 單獨一則「嗯，」併進下一則')
check('「嗯，」＋問題 → 併成一則', D._merge_fragments(['嗯，', '那你當時怎麼處理？']) == ['嗯，那你當時怎麼處理？'])
check('「OK，」「懂了，」也併', D._merge_fragments(['OK，', '下一題。', '補充']) == ['OK，下一題。', '補充'] and D._merge_fragments(['懂了，', 'Q？']) == ['懂了，Q？'])
check('正常的兩則不動', D._merge_fragments(['好，11月可以到職，我了解了。', '那通勤方便嗎？']) == ['好，11月可以到職，我了解了。', '那通勤方便嗎？'])
check('只有一則不動', D._merge_fragments(['嗯，']) == ['嗯，'])
D.run_claude = lambda *a, **k: {'messages': ['嗯，', '您目前住的地方通勤方便嗎?'], 'end': False}
D.context_for = lambda a: {'job': {}, 'application': {}, 'conversation': [{'role': 'candidate', 'content': D.MK.ENTRY_MARKER}], 'plan': {}}
app('g1'); msg('g1', 'candidate', D.MK.ENTRY_MARKER); D._busy.add('g1')
D.handle({'id': 'g1', 'name': 'g1', 'job_slug': 'jb'})
check('handle() 寫進資料庫的是併好的一則＋全形', asst('g1') == ['嗯，您目前住的地方通勤方便嗎？'], asst('g1'))
check('TALK_LITE 有「同一則」與「先記下來」規則', '同一則訊息' in D.TALK_LITE and '先記下來' in D.TALK_LITE)

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
