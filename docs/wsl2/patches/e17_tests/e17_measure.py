import json, subprocess, os, glob, datetime, collections
os.chdir(glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit/nightly_pipeline'))[0])
def q(s):
    r = subprocess.run(['python3', 'd1q.py', 'q', s], capture_output=True, text=True)
    return json.loads(r.stdout)
P = lambda s: datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S') if s else None

print("=== bookings 欄位")
cols = [c['name'] for c in q("PRAGMA table_info(bookings)")]
print(cols)
print("=== bookings 近 8 筆")
for r in q("SELECT * FROM bookings ORDER BY rowid DESC LIMIT 8"):
    print(json.dumps(r, ensure_ascii=False)[:260])
print("=== applications 跟預約相關的欄位")
acols = [c['name'] for c in q("PRAGMA table_info(applications)")]
print([c for c in acols if any(k in c for k in ('remind','mode','book','slot','sched','note','approv','ready','status','consent'))])
print("=== interview_mode × status 分布")
print(json.dumps(q("SELECT interview_mode, status, COUNT(*) n FROM applications GROUP BY interview_mode, status ORDER BY n DESC"), ensure_ascii=False))

sel = "a.id, a.name, a.status, a.interview_mode, a.remind_at, a.interview_state st, a.created_at, a.ready_notified_at, a.interview_started_at, a.interview_plan_at, a.prewarmed_at, a.pre_interview_note"
have = [c for c in ('pre_interview_note',) if c in acols]
if not have:
    sel = sel.replace(", a.pre_interview_note", "")
rows = q(f"SELECT {sel}, (SELECT MIN(s.created_at) FROM assessments s WHERE s.application_id=a.id) assess_at, COALESCE(j.seniority,'mid') sen "
         f"FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug "
         f"WHERE a.interview_started_at IS NOT NULL ORDER BY a.interview_started_at DESC LIMIT 30")
print(f"\n=== 最近 30 場（有 interview_started_at）：進房時計畫好了沒")
ok = collections.Counter(); late = []
print(f"{'姓名':<8}{'職級':<7}{'mode':<10}{'進房':<17}{'計畫完成':<17}{'領先分':>7}  備註")
for r in rows:
    st, pl = P(r['interview_started_at']), P(r.get('interview_plan_at'))
    lead = (st - pl).total_seconds() / 60 if (st and pl) else None
    ready_before = lead is not None and lead > 0
    bucket = '計畫已好' if ready_before else ('計畫在進房後才完成' if pl else '整場沒有計畫')
    ok[bucket] += 1
    note = []
    if r.get('remind_at'): note.append(f"remind_at={r['remind_at'][5:16]}")
    if r.get('interview_mode'): note.append(f"mode={r['interview_mode']}")
    if r.get('pre_interview_note'): note.append('有交代')
    print(f"{r['name']:<8}{r['sen']:<7}{str(r.get('interview_mode') or '-'):<10}{r['interview_started_at'][5:16]:<17}{(r.get('interview_plan_at') or '-')[5:16]:<17}{('%.1f' % lead) if lead is not None else '-':>7}  {bucket} {' '.join(note)}")
print("\n分布:", dict(ok))
sch = [r for r in rows if r.get('remind_at')]
print(f"其中有 remind_at（曾預約）的: {len(sch)} 場；計畫已好: {sum(1 for r in sch if r.get('interview_plan_at') and r['interview_plan_at'] < r['interview_started_at'])}")
print("\n=== 目前待面談（status scheduled/awaiting_booking/ready 且尚未開始）")
for r in q("SELECT a.name, a.status, a.interview_mode, a.remind_at, a.interview_state, a.interview_plan_at, a.created_at FROM applications a "
           "WHERE a.status IN ('scheduled','awaiting_booking','ready') AND (a.interview_state IS NULL OR a.interview_state='not_started') ORDER BY a.remind_at"):
    print(json.dumps(r, ensure_ascii=False))
