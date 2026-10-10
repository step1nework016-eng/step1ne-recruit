"""E17-2 測試。A 段：sqlite 單元測試（跑真的 SQL）；B 段：對正式 D1 做「唯讀乾跑」（UPDATE／INSERT 一律攔下、只印出會做什麼）。"""
import os, sys, time, sqlite3, datetime, json, subprocess
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D

ok_all = True
def check(name, cond, detail=''):
    global ok_all
    ok_all &= bool(cond)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f'  ｜{detail}' if detail else ''))

REAL_D1 = D.d1
logs = []
D.log = lambda m: logs.append(m)

# ───────────── A. sqlite 單元測試 ─────────────
DB = sqlite3.connect(':memory:')
DB.row_factory = sqlite3.Row
for ddl in (
    "CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, job_slug TEXT, status TEXT, interview_state TEXT, remind_at TEXT,"
    " created_at TEXT, interview_plan_json TEXT, interview_plan_at TEXT, prewarmed_opening TEXT, prewarmed_at TEXT, pre_interview_note_at TEXT,"
    " resume_file_id TEXT, resume_url_parsed_at TEXT)",     # E17c：plan_candidates 的提早擬名單會看這兩欄
    "CREATE TABLE jobs (slug TEXT, seniority TEXT, updated_at TEXT)",
    "CREATE TABLE job_expertise (job_slug TEXT, edited_at TEXT)",
    "CREATE TABLE assessments (application_id TEXT)",
    "CREATE TABLE screenings (application_id TEXT, created_at TEXT)",      # E17c：作廢檢查會看初篩時間
):
    DB.execute(ddl)
def sdb(sql, *a, **k):
    cur = DB.execute(sql); rows = [dict(r) for r in cur.fetchall()] if cur.description else []; DB.commit(); return rows
D.d1 = sdb
D.clear_static_cache = lambda *a, **k: None
now = datetime.datetime.now()
T = lambda h: (now + datetime.timedelta(hours=h)).strftime('%Y-%m-%d %H:%M:%S')
def app(i, status, remind=None, sen='mid', assess=True, state=None, plan=None, plan_at=None, warm=None, warm_at=None, note_at=None):
    DB.execute("INSERT INTO applications (id,name,job_slug,status,interview_state,remind_at,created_at,interview_plan_json,interview_plan_at,prewarmed_opening,prewarmed_at,pre_interview_note_at)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (i, i, 'jb', status, state, remind, T(-30), plan, plan_at, warm, warm_at, note_at))
    if assess: DB.execute("INSERT INTO assessments VALUES (?)", (i,))
    DB.commit()
DB.execute("INSERT INTO jobs VALUES ('jb','mid',?)", (T(-100),)); DB.commit()
app('s_in',   'scheduled', remind=T(5))                     # 預約 5 小時後、已交卷 → 要擬
app('s_far',  'scheduled', remind=T(80))                    # 預約 80 小時後 → 還沒到
app('s_past', 'scheduled', remind=T(-3))                    # 預約時間已過 → 不擬（他不會來了／會走 ready）
app('s_noas', 'scheduled', remind=T(5), assess=False)       # 預約了但還沒交卷（中階）→ 不擬
app('r_ready','ready')                                      # 原本就會擬
app('ab',     'awaiting_booking', remind=T(5))              # 額滿、還沒選時間 → 不擬
app('s_have', 'scheduled', remind=T(5), plan='{"questions":[]}', plan_at=T(-1))   # 已經有計畫 → 不重複擬
DB.execute("UPDATE jobs SET seniority='senior' WHERE slug='jb'"); DB.commit()
app('s_sen_noas', 'scheduled', remind=T(6), assess=False)   # 中高階免測驗 → 要擬
DB.execute("UPDATE jobs SET seniority='mid' WHERE slug='jb'"); DB.commit()
print('\n[A1] plan_candidates／prewarm_candidates 的名單')
got = sorted(x['id'] for x in D.plan_candidates())
check('預約在 24 小時內且已交卷／免測驗的人選會被撈到；其他情形照舊不撈', got == ['r_ready', 's_in'], got)
gotw = sorted(x['id'] for x in D.prewarm_candidates())
# E23（2026-10-10 Mac 09f1478）拿掉了「要先交卷」條件：預約在 24 小時內的都預熱（含還沒交卷的 s_noas、免測驗的 s_sen_noas）
check('開場白預熱名單（預約在 24 小時內；不再要求交卷）', gotw == ['r_ready', 's_have', 's_in', 's_noas', 's_sen_noas'], gotw)
D.PLAN_SCHEDULED_LEAD_HOURS = 100
got2 = sorted(x['id'] for x in D.plan_candidates())
check('把窗口改大就撈得到更遠的預約（常數可調）', 's_far' in got2, got2)
D.PLAN_SCHEDULED_LEAD_HOURS = 24

print('\n[A2] 計畫／開場白過期作廢')
DB.execute("DELETE FROM applications"); DB.execute("DELETE FROM jobs"); DB.commit()
DB.execute("INSERT INTO jobs VALUES ('jb','mid',?)", (T(-100),)); DB.commit()
app('note_new', 'ready', plan='{"q":1}', plan_at=T(-3), warm='["hi"]', warm_at=T(-2.5), note_at=T(-1))      # 交代在擬好之後改過
app('fresh',    'ready', plan='{"q":1}', plan_at=T(-1), warm='["hi"]', warm_at=T(-1), note_at=T(-3))        # 交代比計畫舊 → 保留
app('active',   'ready', state='active', plan='{"q":1}', plan_at=T(-3), warm='["hi"]', warm_at=T(-3), note_at=T(-1))  # 已進房 → 不動
app('sched_n',  'scheduled', remind=T(5), plan='{"q":1}', plan_at=T(-3), note_at=T(-2))
D._stale_prep_checked_at = 0
D.invalidate_stale_prepared()
st = {r['id']: r for r in sdb("SELECT * FROM applications")}
check('交代在計畫之後改過 → 計畫與開場白都作廢', st['note_new']['interview_plan_json'] is None and st['note_new']['prewarmed_opening'] is None)
check('交代比計畫舊 → 保留', st['fresh']['interview_plan_json'] is not None and st['fresh']['prewarmed_opening'] is not None)
check('已經進房間的人不動', st['active']['interview_plan_json'] is not None)
check('預約中的人一樣會作廢', st['sched_n']['interview_plan_json'] is None)
# 職缺被改
sdb("UPDATE applications SET interview_plan_json='{\"q\":2}', interview_plan_at=?, prewarmed_opening=NULL, prewarmed_at=NULL WHERE id='fresh'", ) if False else None
DB.execute("UPDATE applications SET interview_plan_json='{\"q\":2}', interview_plan_at=? WHERE id='fresh'", (T(-2),)); DB.commit()
DB.execute("UPDATE jobs SET updated_at=? WHERE slug='jb'", (T(-0.5),)); DB.commit()
D._stale_prep_checked_at = 0; D.invalidate_stale_prepared()
check('職缺在計畫之後改過 → 作廢', sdb("SELECT interview_plan_json p FROM applications WHERE id='fresh'")[0]['p'] is None)
# 上限：同一個人最多作廢 3 次
for k in range(5):
    DB.execute("UPDATE applications SET interview_plan_json='{\"q\":3}', interview_plan_at=? WHERE id='fresh'", (T(-2),)); DB.commit()
    D._stale_prep_checked_at = 0; D.invalidate_stale_prepared()
check('同一個人最多作廢 3 次（職缺連續被改不會一直重擬）', D._stale_prep_count.get('fresh', 0) == 3, D._stale_prep_count.get('fresh'))
# compare-and-set：作廢那一瞬間如果剛好重擬好新版，不能把新版刪掉
DB.execute("INSERT INTO applications (id,name,job_slug,status,interview_plan_json,interview_plan_at,pre_interview_note_at,created_at) VALUES ('cas','cas','jb','ready','{\"q\":9}',?,?,?)", (T(-3), T(-1), T(-9))); DB.commit()
orig = D.d1
def racing(sql, *a, **k):
    if sql.lstrip().startswith('UPDATE applications SET interview_plan_json=NULL') and 'id=\'cas\'' in sql:
        DB.execute("UPDATE applications SET interview_plan_json='{\"q\":NEW}', interview_plan_at=? WHERE id='cas'", (T(0),)); DB.commit()   # 別的執行緒剛好重擬完
    return orig(sql, *a, **k)
D.d1 = racing; D._stale_prep_checked_at = 0; D.invalidate_stale_prepared(); D.d1 = orig
check('compare-and-set：剛好重擬好的新版不會被刪', 'NEW' in (sdb("SELECT interview_plan_json p FROM applications WHERE id='cas'")[0]['p'] or ''))

print('\n[A3] 失敗後重試間隔／背景工作逾時')
D._plan_cooldown.clear(); D._plan_fails.clear()
seq = []
for i in range(10):
    D._plan_mark_failed('x', 'x'); seq.append(round(D._plan_cooldown['x'] - time.time()))
check('冷卻 60/120/300/600…，累計 8 次退回 1800', seq[:4] == [60, 120, 300, 600] and seq[7] == 1800, seq)
touts = []
class R:  # 假的 subprocess 結果
    returncode = 0; stdout = '{"messages":["a"]}'; stderr = ''
def fake_run(cmd, **kw):
    touts.append(kw.get('timeout')); return R()
real_sub = subprocess.run
D.subprocess.run = fake_run
D.run_claude('x'); D.run_claude('x', first_try=D.BG_FIRST_TRY_SEC, total=D.BG_TOTAL_SEC)
D.subprocess.run = real_sub
check('面談回覆仍是原本的第一次逾時；背景工作改用較長的', touts[0] <= D.FIRST_TRY_TIMEOUT and 200 < touts[1] <= D.BG_FIRST_TRY_SEC, touts)

# ───────────── B. 對正式 D1 的唯讀乾跑 ─────────────
print('\n[B] 對正式 D1 的唯讀乾跑（UPDATE／INSERT 一律攔下）')
would = []
def ro(sql, *a, **k):
    if sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE', 'REPLACE')):
        would.append(sql[:150].replace('\n', ' ')); return []
    return REAL_D1(sql, *a, **k)
D.d1 = ro
D._stale_prep_checked_at = 0; D._stale_prep_count.clear()
try:
    D.invalidate_stale_prepared()
    print('  會作廢（現在的正式資料）：')
    for w in would: print('   -', w)
    for l in logs: print('   log>', l)
    print('  plan_candidates() 現在會撈到：', [(x['name'], x['id'][:8]) for x in D.plan_candidates()])
    print('  prewarm_candidates() 現在會撈到：', [(x['name'], x['id'][:8]) for x in D.prewarm_candidates()])
    D.PLAN_SCHEDULED_LEAD_HOURS = 24 * 5
    print('  （把窗口放大到 5 天，示範預約人選會被撈到）plan_candidates()：', [(x['name'], x['id'][:8]) for x in D.plan_candidates()])
except BaseException as e:
    print('  乾跑失敗（可能是這個環境沒有正式 D1 憑證）：', type(e).__name__, str(e)[:160])

print('\n單元測試全部通過' if ok_all else '\n有失敗')
sys.exit(0 if ok_all else 1)
