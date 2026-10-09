"""E17c 測試（全部記憶體 sqlite＋假 fetch，不連正式 D1、不呼叫 claude）：
 A. plan_candidates 的「提早擬」名單條件
 B. 提早擬之後的快取補撈（測驗結果、初篩）：補得到、不重複撈、有節流、不誤補、不影響原本已有的
 C. 輪詢迴圈的「有人在面談時不提早擬」過濾
 D. invalidate_stale_prepared 也涵蓋 pending_assessment
"""
import os, sys, time, sqlite3, datetime, json
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
    " created_at TEXT, interview_plan_json TEXT, interview_plan_at TEXT, prewarmed_opening TEXT, prewarmed_at TEXT, pre_interview_note_at TEXT,"
    " resume_file_id TEXT, resume_url_parsed_at TEXT)",
    "CREATE TABLE jobs (slug TEXT, seniority TEXT, updated_at TEXT)",
    "CREATE TABLE job_expertise (job_slug TEXT, edited_at TEXT)",
    "CREATE TABLE assessments (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, b5_o REAL, b5_c REAL, b5_e REAL, b5_a REAL, b5_n REAL,"
    " grit REAL, grit_interest REAL, grit_effort REAL, quality_flag TEXT)",
    "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)",
    "CREATE TABLE screenings (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, score REAL, summary TEXT, risks TEXT, questions TEXT, hard_fail TEXT, created_at TEXT)",
):
    DB.execute(ddl)
calls = []
def sdb(sql, *a, **k):
    calls.append(sql)
    cur = DB.execute(sql); rows = [dict(r) for r in cur.fetchall()] if cur.description else []; DB.commit(); return rows
D.d1 = sdb
now = datetime.datetime.now()
T = lambda h: (now + datetime.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M:%S')
def app(i, status, created=-0.2, resume_file='f1', parsed=None, state=None, plan=None, plan_at=None, warm=None, warm_at=None, note_at=None):
    DB.execute("INSERT INTO applications (id,name,job_slug,status,interview_state,created_at,interview_plan_json,interview_plan_at,prewarmed_opening,prewarmed_at,pre_interview_note_at,resume_file_id,resume_url_parsed_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, i, 'jb', status, state, T(created), plan, plan_at, warm, warm_at, note_at, resume_file, parsed))
    DB.commit()
DB.execute("INSERT INTO jobs VALUES ('jb','mid',?)", (T(-100),)); DB.commit()

print('\n[A] 提早擬名單')
app('p_new',    'pending_assessment')                                  # 剛投遞、履歷是上傳檔 → 要擬
app('p_linkok', 'pending_assessment', resume_file=None, parsed=T(-0.1)) # 連結履歷、已解析 → 要擬
app('p_nores',  'pending_assessment', resume_file=None)                # 履歷抓不到文字 → 不擬
app('p_old',    'pending_assessment', created=-80)                     # 投遞超過 72 小時 → 不擬
app('p_wait',   'pending_assessment', created=-48)                     # 投遞 48 小時、還沒進房（等很久才來的人）→ 要擬
app('p_have',   'pending_assessment', plan='{"questions":[]}', plan_at=T(-0.1))   # 已有計畫 → 不重複
app('p_active', 'pending_assessment', state='active')                  # 已在面談 → 走原本 active 那條（不是 early）
app('p_other',  'new')                                                 # 其他狀態不碰
rows = {x['id']: x for x in D.plan_candidates()}
check('剛投遞、履歷抓得到文字的會被撈到（含 48 小時前投遞、還沒進房的）', {'p_new', 'p_linkok', 'p_wait'} <= set(rows), sorted(rows))
check('履歷抓不到／投遞太久／已有計畫／其他狀態 → 不撈', not ({'p_nores', 'p_old', 'p_have', 'p_other'} & set(rows)), sorted(rows))
check('提早擬的人 early 旗標為 1，進房中的人不是 early', rows['p_new']['early'] == 1 and rows['p_active']['early'] == 0, {k: v['early'] for k, v in rows.items()})
D.PLAN_EARLY = False
rows_off = {x['id'] for x in D.plan_candidates()}
check('關掉開關 → 提早擬名單消失（回到原本行為）', 'p_new' not in rows_off and 'p_linkok' not in rows_off, sorted(rows_off))
D.PLAN_EARLY = True

print('\n[B] 快取補撈')
STATIC = {'application': {'name': 'x'}, 'job': {'slug': 'jb'}}
D._STATIC_CACHE.clear(); D._late_checked.clear()
fetches = []
D._fetch_static = lambda app_id: (fetches.append(app_id), dict(STATIC))[1]
ctx = D.context_for('p_new')
check('第一次 context_for：抓 static（沒測驗、沒初篩）', 'assessment' not in ctx and 'screening' not in ctx and len(fetches) == 1)
DB.execute("INSERT INTO assessments (application_id,b5_o,b5_c,b5_e,b5_a,b5_n,grit,grit_interest,grit_effort,quality_flag) VALUES ('p_new',3,4,3,3,2,3.5,3,4,NULL)"); DB.commit()
n0 = len(calls)
ctx = D.context_for('p_new')
check('30 秒內再叫：節流，不重撈', 'assessment' not in ctx and not any('FROM assessments' in c for c in calls[n0:]))
D._late_checked['p_new'] = time.time() - 31
ctx = D.context_for('p_new')
check('超過節流時間 → 補上測驗結果', ctx.get('assessment', {}).get('b5_c') == 4 and 'screening' not in ctx)
check('補上時沒有重抓整份 static', len(fetches) == 1, len(fetches))
DB.execute("INSERT INTO screenings (application_id,score,summary,risks,questions,hard_fail) VALUES ('p_new',72,'ok','[\"r1\"]','[\"q1\",\"q2\"]','')"); DB.commit()
D._late_checked['p_new'] = time.time() - 31
n1 = len(calls)
ctx = D.context_for('p_new')
check('之後初篩出現 → 也補上（內容正確解析）', ctx.get('screening', {}).get('questions') == ['q1', 'q2'] and ctx['screening']['score'] == 72)
check('只撈缺的那一段（測驗已有就不再撈）', not any('FROM assessments' in c for c in calls[n1:]))
n2 = len(calls); D._late_checked['p_new'] = 0
ctx = D.context_for('p_new')
check('兩段都齊了 → 之後完全不再查', not any(('FROM assessments' in c or 'FROM screenings' in c) for c in calls[n2:]))
check('快取本體也更新了（下一個 context_for 直接拿到）', 'assessment' in D._STATIC_CACHE['p_new'] and 'screening' in D._STATIC_CACHE['p_new'])
D.clear_static_cache('p_new')
check('clear_static_cache 一併清節流紀錄', 'p_new' not in D._late_checked and 'p_new' not in D._STATIC_CACHE)
# 沒有出現的就一直沒有（不誤補、不丟例外）
D._STATIC_CACHE.clear(); D._late_checked.clear()
ctx = D.context_for('p_never'); D._late_checked['p_never'] = 0
ctx = D.context_for('p_never')
check('一直沒有測驗／初篩的人不會被誤補', 'assessment' not in ctx and 'screening' not in ctx)
# 原本就有的不動
D._STATIC_CACHE.clear(); D._late_checked.clear()
STATIC2 = dict(STATIC, assessment={'b5_c': 1}, screening={'score': 1, 'questions': []})
D._fetch_static = lambda app_id: dict(STATIC2)
n3 = len(calls)
ctx = D.context_for('p_have2'); ctx = D.context_for('p_have2')
check('static 一開始就有兩段 → 補撈完全不查 D1', not any(('FROM assessments' in c or 'FROM screenings' in c) for c in calls[n3:]))
# D1 補撈失敗不影響面談
D._STATIC_CACHE.clear(); D._late_checked.clear(); D._fetch_static = lambda app_id: dict(STATIC)
orig = D._fetch_assessment
def boom(app_id): raise RuntimeError('d1 down')
D._fetch_assessment = boom
ctx = D.context_for('p_err'); D._late_checked['p_err'] = 0
ctx = D.context_for('p_err')
check('補撈拋例外 → 沿用快取、面談照常', 'assessment' not in ctx and any('補撈測驗' in l for l in logs))
D._fetch_assessment = orig

print('\n[C] 有人在面談時不提早擬（輪詢迴圈的過濾條件）')
src = open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read()
check('迴圈裡有「_busy 非空 → 去掉 early」的過濾', "if _live_busy:" in src and "not a.get('early')" in src)

print('\n[D] 作廢檢查涵蓋 pending_assessment')
DB.execute("DELETE FROM applications"); DB.commit()
app('pa_note', 'pending_assessment', plan='{"q":1}', plan_at=T(-0.5), note_at=T(-0.1))
D.clear_static_cache = lambda *a, **k: None
D._stale_prep_checked_at = 0; D.invalidate_stale_prepared()
check('提早擬的計畫在顧問交代之後被改 → 作廢重擬', sdb("SELECT interview_plan_json p FROM applications WHERE id='pa_note'")[0]['p'] is None)

print('\n[D2] 初篩晚於計畫 → 計畫重擬（人還沒進房）；開場白不動')
DB.execute("DELETE FROM applications"); DB.execute("DELETE FROM screenings"); DB.commit()
D._stale_prep_count.clear()
app('scr_late',  'pending_assessment', plan='{"q":1}', plan_at=T(-2), warm='["hi"]', warm_at=T(-2))
app('scr_early', 'pending_assessment', plan='{"q":1}', plan_at=T(-1), warm='["hi"]', warm_at=T(-1))
app('scr_none',  'pending_assessment', plan='{"q":1}', plan_at=T(-1))
app('scr_act',   'ready', state='active', plan='{"q":1}', plan_at=T(-2))
for i, h in (('scr_late', -1), ('scr_early', -3), ('scr_act', -1)):
    DB.execute("INSERT INTO screenings (application_id, created_at) VALUES (?,?)", (i, T(h)))
DB.commit()
D._stale_prep_checked_at = 0; D.invalidate_stale_prepared()
st = {r['id']: r for r in sdb("SELECT * FROM applications")}
check('計畫之後才有初篩 → 計畫作廢重擬', st['scr_late']['interview_plan_json'] is None)
check('開場白不因初篩作廢', st['scr_late']['prewarmed_opening'] is not None)
check('初篩比計畫早（計畫已經看過）→ 保留', st['scr_early']['interview_plan_json'] is not None)
check('沒有初篩 → 保留', st['scr_none']['interview_plan_json'] is not None)
check('已進房的人不動', st['scr_act']['interview_plan_json'] is not None)
D.PLAN_REDO_ON_SCREENING = False
DB.execute("UPDATE applications SET interview_plan_json='{\"q\":2}', interview_plan_at=? WHERE id='scr_late'", (T(-2),)); DB.commit()
D._stale_prep_checked_at = 0; D.invalidate_stale_prepared()
check('關掉開關 → 不因初篩作廢', sdb("SELECT interview_plan_json p FROM applications WHERE id='scr_late'")[0]['p'] is not None)
D.PLAN_REDO_ON_SCREENING = True

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
