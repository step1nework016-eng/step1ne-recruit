import json, subprocess, os, glob, datetime, collections
os.chdir(glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit/nightly_pipeline'))[0])
def q(s):
    r = subprocess.run(['python3', 'd1q.py', 'q', s], capture_output=True, text=True)
    return json.loads(r.stdout)
P = lambda s: datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S') if s else None
rows = q("""SELECT a.name, COALESCE(j.seniority,'mid') sen, a.interview_mode mode, a.created_at, a.ready_notified_at rn,
  (SELECT MIN(s.created_at) FROM assessments s WHERE s.application_id=a.id) assess_at,
  a.interview_started_at st, a.interview_plan_at pl, a.prewarmed_at pw
  FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug
  WHERE a.interview_started_at >= '2026-09-22' ORDER BY a.interview_started_at DESC""")
print(f"{'姓名':<10}{'職級':<7}{'交卷':<12}{'進房':<12}{'計畫好':<12}{'交卷→進房':>9}{'交卷→計畫好':>11}{'開場預熱':<12}")
f = lambda s: s[5:16][6:] if s else '-'
cats = collections.Counter()
for r in rows:
    a, s, p = P(r['assess_at']), P(r['st']), P(r['pl'])
    d_as = (s - a).total_seconds()/60 if (a and s) else None
    d_ap = (p - a).total_seconds()/60 if (a and p) else None
    if p and s and p < s: c = '計畫已好'
    elif d_as is not None and d_ap is not None and d_as < 6: c = '交卷後很快進房（<6分），計畫還在擬'
    elif p: c = '交卷後夠久但計畫仍晚（擬失敗／冷卻／排隊）'
    else: c = '沒有計畫'
    cats[c] += 1
    print(f"{r['name']:<10}{r['sen']:<7}{f(r['assess_at']):<12}{f(r['st']):<12}{f(r['pl']):<12}{('%.1f' % d_as) if d_as is not None else '-':>9}{('%.1f' % d_ap) if d_ap is not None else '-':>11}  {f(r['pw']):<10}{c}")
print("\n自 9/22 起共", len(rows), "場：", dict(cats))
