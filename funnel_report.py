#!/usr/bin/env python3
"""應徵漏斗報表（2026-10-21，快速應徵測試；計畫書 4.2）

用法：
  python3 funnel_report.py --from 2026-10-22 --to 2026-11-04 [--flow quick|original|all] [--window 7] [--json]

規則：
- 一個「人」＝ anon_id（前端給的匿名編號）；沒有 anon_id 的應徵就用 application_id 當一個人。
- cohort：這個人「第一次出現」（任何漏斗事件）落在 --from～--to 之間。
- 觀察窗：從第一次出現起算 --window 天內完成的階段才算（預設 7 天）。
- 每一階段的人數都是不重複人數；「轉換」＝ 上一階段的人當中也到了這一階段的人數 ÷ 上一階段人數。
- consultant_valid（顧問認定有效）還沒做按鈕，固定顯示 N/A，轉換跳過它接上一個有資料的階段。
- client_recommended 來自 candidate_forwards（顧問把人選轉給企業）。
- ⚠️ 原本流程（original）測驗在面談前、快速應徵（quick）在面談後，測驗兩格不能直接比，只當描述。
資料來源：D1 apply_funnel_events（public-worker 與 interview_daemon 寫入）。只讀，不寫。
"""
import argparse, datetime, json, os, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consultant_ops as c  # noqa: E402

STAGES = [
    ('landing', 'apply_landing', '應徵頁到達'),
    ('form_start', 'apply_form_start', '開始填表'),
    ('step1_complete', 'apply_step1_complete', '第一階段完成'),
    ('interview_start', 'interview_start', 'AI 面談開始'),
    ('interview_done', 'interview_done', 'AI 面談完成'),
    ('assessment_invited', 'assessment_invited', '測驗邀請'),
    ('assessment_done', 'assessment_done', '測驗完成'),
    ('consultant_valid', None, '顧問認定有效'),
    ('client_recommended', None, '推薦給企業'),
]
EVENT_TO_STAGE = {ev: key for key, ev, _ in STAGES if ev}
FMT = '%Y-%m-%d %H:%M:%S'


def ts(s):
    s = str(s or '').replace('T', ' ')
    for f, n in ((FMT, 19), ('%Y-%m-%d %H:%M', 16), ('%Y-%m-%d', 10)):
        try:
            return datetime.datetime.strptime(s[:n], f)
        except ValueError:
            continue
    return None


def fetch_events(end_excl):
    out, last = [], 0
    while True:
        rs = c.rows('SELECT id, anon_id, application_id, flow, job_slug, event, created_at FROM apply_funnel_events '
                    'WHERE id > ? AND created_at < ? ORDER BY id LIMIT 5000', [last, end_excl])
        out += rs
        if len(rs) < 5000:
            return out
        last = rs[-1]['id']


def fetch_forwards(app_ids):
    first = {}
    ids = sorted(app_ids)
    for i in range(0, len(ids), 90):   # D1 的 IN 參數上限，90 筆一批
        chunk = ids[i:i + 90]
        rs = c.rows(f"SELECT application_id, MIN(forwarded_at) AS at FROM candidate_forwards "
                    f"WHERE application_id IN ({','.join('?' * len(chunk))}) GROUP BY application_id", chunk)
        for r in rs:
            first[r['application_id']] = r['at']
    return first


def build(d_from, d_to, window):
    start = datetime.datetime.strptime(d_from, '%Y-%m-%d')
    end = datetime.datetime.strptime(d_to, '%Y-%m-%d') + datetime.timedelta(days=1)   # --to 當天含
    obs_end = end + datetime.timedelta(days=window)
    events = fetch_events(obs_end.strftime(FMT))

    # 應徵編號 → anon_id（任何一列同時有兩者就接起來）
    app2anon = {}
    for e in events:
        if e['anon_id'] and e['application_id']:
            app2anon.setdefault(e['application_id'], e['anon_id'])

    people = {}
    for e in sorted(events, key=lambda x: (x['created_at'], x['id'])):
        anon = e['anon_id'] or app2anon.get(e['application_id'])
        pid = anon or (('app:' + e['application_id']) if e['application_id'] else None)
        if not pid:
            continue
        t = ts(e['created_at'])
        p = people.setdefault(pid, {'first': t, 'flow': None, 'job': None, 'step1_flow': None,
                                    'step1_job': None, 'apps': set(), 'stages': {}})
        if e['application_id']:
            p['apps'].add(e['application_id'])
        if e['flow'] and not p['flow']:
            p['flow'] = e['flow']
        if e['job_slug'] and not p['job']:
            p['job'] = e['job_slug']
        if e['event'] == 'apply_step1_complete' and not p['step1_flow']:
            p['step1_flow'], p['step1_job'] = e['flow'], e['job_slug']
        st = EVENT_TO_STAGE.get(e['event'])
        if st and st not in p['stages'] and t and p['first'] and t < p['first'] + datetime.timedelta(days=window):
            p['stages'][st] = t

    cohort = {pid: p for pid, p in people.items() if p['first'] and start <= p['first'] < end}
    fwd = fetch_forwards({a for p in cohort.values() for a in p['apps']})
    for p in cohort.values():
        lim = p['first'] + datetime.timedelta(days=window)
        ats = [ts(fwd[a]) for a in p['apps'] if a in fwd]
        if any(a and a < lim for a in ats):
            p['stages']['client_recommended'] = True
        p['flow'] = p['step1_flow'] or p['flow'] or 'unknown'
        p['job'] = p['step1_job'] or p['job'] or '（未選職缺）'
    return cohort


def funnel(group):
    rows, prev = [], None
    first_n = None
    for key, _ev, label in STAGES:
        if key == 'consultant_valid':
            rows.append({'stage': key, 'label': label, 'count': None, 'conv': None})
            continue
        reached = {pid for pid, p in group.items() if key in p['stages']}
        conv = None
        if prev is not None:
            num = len(reached & prev)
            conv = {'num': num, 'den': len(prev), 'rate': (num / len(prev)) if prev else None}
        if first_n is None:
            first_n = len(reached)
        rows.append({'stage': key, 'label': label, 'count': len(reached), 'conv': conv,
                     'of_landing': {'num': len(reached), 'den': first_n}})
        prev = reached
    return rows


def main():
    ap = argparse.ArgumentParser(description='應徵漏斗報表')
    ap.add_argument('--from', dest='d_from', required=True)
    ap.add_argument('--to', dest='d_to', required=True)
    ap.add_argument('--flow', default='all', choices=['all', 'quick', 'original'])
    ap.add_argument('--window', type=int, default=7, help='觀察窗天數（從第一次出現起算）')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    cohort = build(a.d_from, a.d_to, a.window)
    flows = ['quick', 'original'] if a.flow == 'all' else [a.flow]
    result = {'from': a.d_from, 'to': a.d_to, 'window_days': a.window, 'flows': {}}
    for fl in flows:
        g = {pid: p for pid, p in cohort.items() if p['flow'] == fl}
        by_job = defaultdict(dict)
        for pid, p in g.items():
            by_job[p['job']][pid] = p
        result['flows'][fl] = {'total': funnel(g), 'jobs': {j: funnel(v) for j, v in sorted(by_job.items())}}

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    pct = lambda x: '—' if not x or x['rate'] is None else f"{x['rate'] * 100:.0f}%（{x['num']}/{x['den']}）"
    print(f"應徵漏斗｜cohort {a.d_from}～{a.d_to}（第一次出現）｜觀察窗 {a.window} 天")
    for fl, data in result['flows'].items():
        name = {'quick': '快速應徵', 'original': '原本流程'}[fl]
        for title, rows in [('全部職缺', data['total'])] + list(data['jobs'].items()):
            print(f"\n■ {name}｜{title}")
            print(f"  {'階段':<12}{'人數':>6}   轉換（比上一階段）／ 比到達")
            for r in rows:
                cnt = 'N/A' if r['count'] is None else str(r['count'])
                if r['count'] is None:
                    print(f"  {r['label']:<12}{cnt:>6}   尚未建置")
                    continue
                ol = r['of_landing']
                ol = dict(ol, rate=(ol['num'] / ol['den']) if ol['den'] else None)
                print(f"  {r['label']:<12}{cnt:>6}   {pct(r['conv'])} ／ {pct(ol)}")
    if 'original' in result['flows']:
        print('\n※ 原本流程的測驗在面談前、快速應徵在面談後，測驗兩格只當描述，不能直接比。')


if __name__ == '__main__':
    main()
