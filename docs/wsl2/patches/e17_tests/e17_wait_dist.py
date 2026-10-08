import json, subprocess, os, glob, datetime, statistics, collections
os.chdir(glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit/nightly_pipeline'))[0])
def q(s):
    return json.loads(subprocess.run(['python3', 'd1q.py', 'q', s], capture_output=True, text=True).stdout)
P = lambda s: datetime.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
APOL = '不好意思，我這邊系統出了點狀況'
ENTRY = '（候選人已進入面談室）'
apps = q("SELECT id, name FROM applications WHERE interview_started_at >= '2026-09-22' AND interview_state IN ('done','paused','active')")
rounds, openings = [], []
for a in apps:
    rows = q(f"SELECT role, content, created_at FROM messages WHERE application_id='{a['id']}' ORDER BY id")
    pend = None
    for r in rows:
        t = P(r['created_at'])
        if r['role'] == 'candidate':
            if pend is None: pend = (t, r['content'] == ENTRY)
        elif r['role'] == 'assistant':
            if pend is not None:
                if not r['content'].startswith(APOL):
                    (openings if pend[1] else rounds).append((t - pend[0]).total_seconds())
                pend = None
def stat(name, xs):
    xs = sorted(xs); n = len(xs)
    f = lambda th: sum(1 for x in xs if x > th)
    print(f"{name}: 共 {n} 輪｜中位 {statistics.median(xs):.0f}s｜平均 {sum(xs)/n:.0f}s｜p90 {xs[int(n*0.9)]:.0f}s｜p95 {xs[int(n*0.95)]:.0f}s｜最大 {xs[-1]:.0f}s")
    for th in (30, 40, 60, 90, 120):
        print(f"    超過 {th} 秒：{f(th)} 輪（{f(th)*100/n:.0f}%）")
print(f"自 9/22 起 {len(apps)} 場面談")
stat('一般輪次（人選回話 → 阿財第一則正式回覆，排除道歉與進房開場）', rounds)
stat('進房開場', openings)
