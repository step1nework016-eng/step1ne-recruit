#!/usr/bin/env python3
"""Step1ne 搜尋成效週報：產生某個週一的快照 data/<YYYY-MM-DD>.json，並更新 data/index.json。

用法：build_weekly.py [--date YYYY-MM-DD] [--out 目錄]   （預設＝今天；用過去的週一可回填歷史）
資料：GSC（截止＝報告日−3 天，GSC 延遲）、GA4、notes/<date>.json（本週做了什麼／下一步／AI 實測）
  notes 找的順序：repo 的 seo_report/notes/（人工寫的優先）→ <out>/notes/（auto_notes.py 自動產的）
  都沒有就沿用上一份並標「沿用」。

2026-10-12 從 ~/下載項目/step1ne-seo-records/report/ 搬進 step1ne-recruit repo，WSL2 每週一跑（E33）。
路徑都可設定：
  SEO_SA_JSON      Google 服務帳號金鑰檔（預設 ~/.config/workflow-os/aijobseo-sa.json；金鑰不進版控）
  SEO_REPORT_OUT   產出的 data/、notes/ 放哪（預設 ~/aijob-automation/state/seo_report；不寫進 repo，免得擋 git pull）
"""
import argparse, json, os, re, sys
from datetime import date, timedelta
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = 'sc-domain:step1ne.com'
OUT = os.path.expanduser(os.environ.get('SEO_REPORT_OUT') or '~/aijob-automation/state/seo_report')


def _sa_path():
    """金鑰檔：環境變數 → 標準位置 → Mac 舊位置（只是相容，WSL2 不會有）。"""
    for p in (os.environ.get('SEO_SA_JSON'), '~/.config/workflow-os/aijobseo-sa.json',
              '/Users/user/Downloads/資料/aijobseo-c57ff314ac70.json'):
        if p and os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    raise SystemExit('找不到 Google 服務帳號金鑰檔：請放到 ~/.config/workflow-os/aijobseo-sa.json（或設 SEO_SA_JSON）')




class gsc:
    """原本 import _tools/gsc.py；_tools 不在 repo 裡（WSL2 沒有），只用到兩個函式就直接放這裡。"""
    @staticmethod
    def svc():
        from google.oauth2 import service_account
        from googleapiclient.discovery import build as _b
        cr = service_account.Credentials.from_service_account_file(
            _sa_path(), scopes=['https://www.googleapis.com/auth/webmasters.readonly'])
        return _b('searchconsole', 'v1', credentials=cr, cache_discovery=False)

    @staticmethod
    def q(s, site, start, end, dims, n, filters=None):
        body = {'startDate': start.isoformat(), 'endDate': end.isoformat(), 'dimensions': dims, 'rowLimit': n}
        if filters:
            body['dimensionFilterGroups'] = [{'filters': filters}]
        return s.searchanalytics().query(siteUrl=site, body=body).execute().get('rows', [])
ENT = ['how-to-choose-headhunter','headhunter-fees','work-with-headhunters','dispatch-cost-breakdown','dispatch-legal-risks',
       'dispatch-agency-how-to-choose','recruitment-cost-calculation','recruitment-agency-guide','how-to-find-talent',
       'entry-level-recruitment-outsourcing','mass-hiring-guide','executive-hiring-guide','commission-recruiting',
       'hiring-assessment','/about/']
RET = ['douyin-live-payout-taiwan','streamer-revenue-split','streamer-recruitment-guide','streamer-contract-employment-status']
KW = re.compile(r'獵頭|派遣|招募|代招|人才|徵才|人力|找人|委託|headhunt|招聘|仲介')
EV = ['employer_lead_submit','hiring_assessment_lead','form_submit','job_apply_landing','line_redirect','line_cta_click']

def seg(u):
    if any(r in u for r in RET): return 'retired'
    if any(e in u for e in ENT): return 'enterprise'
    if '/jobs/' in u: return 'jobs'
    if '/articles/' in u: return 'jobseeker'
    return 'other'

def ga4(a, b):
    from google.oauth2 import service_account
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric, FilterExpression, Filter
    c = BetaAnalyticsDataClient(credentials=service_account.Credentials.from_service_account_file(
        _sa_path(), scopes=['https://www.googleapis.com/auth/analytics.readonly']))
    r = c.run_report(RunReportRequest(property='properties/547366147', date_ranges=[DateRange(start_date=a, end_date=b)],
        dimensions=[Dimension(name='eventName')], metrics=[Metric(name='eventCount')],
        dimension_filter=FilterExpression(filter=Filter(field_name='eventName', in_list_filter=Filter.InListFilter(values=EV)))))
    out = {x.dimension_values[0].value: int(x.metric_values[0].value) for x in r.rows}
    r = c.run_report(RunReportRequest(property='properties/547366147', date_ranges=[DateRange(start_date=a, end_date=b)],
        metrics=[Metric(name='sessions')]))
    out['sessions'] = int(r.rows[0].metric_values[0].value) if r.rows else 0
    return out

def behavior(a, b):
    """GA4 使用者行為：整站指標、流量管道、裝置、到達頁（含企業端頁面停留）。"""
    from google.oauth2 import service_account
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric, OrderBy
    c = BetaAnalyticsDataClient(credentials=service_account.Credentials.from_service_account_file(
        _sa_path(), scopes=['https://www.googleapis.com/auth/analytics.readonly']))
    def run(dims, mets, limit=10, order=None):
        r = c.run_report(RunReportRequest(property='properties/547366147', date_ranges=[DateRange(start_date=a, end_date=b)],
            dimensions=[Dimension(name=d) for d in dims], metrics=[Metric(name=m) for m in mets], limit=limit,
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order or mets[0]), desc=True)]))
        return [{**{d: x.dimension_values[i].value for i, d in enumerate(dims)},
                 **{m: float(x.metric_values[j].value) for j, m in enumerate(mets)}} for x in r.rows]
    M = ['sessions', 'totalUsers', 'newUsers', 'engagementRate', 'averageSessionDuration', 'screenPageViewsPerSession']
    site = run([], M, 1)
    ch = run(['sessionDefaultChannelGroup'], ['sessions', 'engagementRate', 'averageSessionDuration'], 8)
    dev = run(['deviceCategory'], ['sessions', 'engagementRate'], 4)
    land = run(['landingPage'], ['sessions', 'engagementRate', 'averageSessionDuration', 'keyEvents'], 60)
    ent = [x for x in land if any(e in x['landingPage'] for e in ENT)]
    top = [x for x in land if x['landingPage'] not in ('(not set)',)][:10]
    for x in top + ent[:10]:
        x['title'] = page_title('https://step1ne.com' + x['landingPage'])
    return {'site': site[0] if site else {}, 'channels': ch, 'devices': dev, 'landing_top': top, 'landing_ent': ent[:10]}

_TITLES = {}
def page_title(url):
    """抓頁面 <title>（快取）；抓不到就用網址路徑。"""
    if url in _TITLES: return _TITLES[url]
    import urllib.request, html as H
    t = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 Step1neReport'})
        raw = urllib.request.urlopen(req, timeout=15).read(60000).decode('utf-8', 'replace')
        m = re.search(r'<title>(.*?)</title>', raw, re.S)
        if m: t = re.sub(r'\s*[｜|]\s*Step1ne.*$', '', H.unescape(m.group(1)).strip())
    except Exception:
        pass
    _TITLES[url] = t or url.replace('https://step1ne.com', '') or '/'
    return _TITLES[url]

def kw_pages(s, a, b, queries):
    """每個企業字是哪些頁面在吃（query×page），附頁面標題。"""
    want = set(queries); out = defaultdict(list)
    for r in gsc.q(s, SITE, a, b, ['query', 'page'], 25000):
        q, u = r['keys']
        if q not in want or '#' in u: continue
        out[q].append({'url': u, 'title': page_title(u), 'c': int(r['clicks']), 'i': int(r['impressions']), 'p': round(r['position'], 1)})
    for q in out: out[q].sort(key=lambda x: -x['i'])
    return out

def build(rep):
    s = gsc.svc()
    end = rep - timedelta(days=3)
    start = end - timedelta(days=12 * 7 - 1)
    W = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in gsc.q(s, SITE, start, end, ['date', 'page'], 25000):
        d, u = r['keys']
        if '#' in u: continue
        wk = (date.fromisoformat(d) - start).days // 7
        a = W[wk][seg(u)]; a[0] += r['clicks']; a[1] += r['impressions']
    weeks = [{'week': (start + timedelta(days=wk * 7)).isoformat(),
              **{k: {'c': int(v[0]), 'i': int(v[1])} for k, v in W[wk].items()}} for wk in sorted(W)]
    st = end - timedelta(days=27); pe = st - timedelta(days=1); ps = pe - timedelta(days=27)
    def kw(a, b): return {r['keys'][0]: r for r in gsc.q(s, SITE, a, b, ['query'], 25000) if KW.search(r['keys'][0])}
    now, old = kw(st, end), kw(ps, pe)
    rows = []
    for k in set(now) | set(old):
        n, o = now.get(k, {}), old.get(k, {})
        rows.append({'q': k, 'c': int(n.get('clicks', 0)), 'i': int(n.get('impressions', 0)),
                     'p': round(n['position'], 1) if n else None, 'pc': int(o.get('clicks', 0)),
                     'pi': int(o.get('impressions', 0)), 'pp': round(o['position'], 1) if o else None})
    rows.sort(key=lambda r: -(r['i'] + r['pi']))
    kp = kw_pages(s, st, end, [r['q'] for r in rows[:25]])
    for r in rows[:25]: r['pages'] = kp.get(r['q'], [])[:5]
    g = {'now': ga4(st.isoformat(), end.isoformat()), 'prev': ga4(ps.isoformat(), pe.isoformat())}
    beh = {'now': behavior(st.isoformat(), end.isoformat()), 'prev': behavior(ps.isoformat(), pe.isoformat())}
    return {'report_date': rep.isoformat(), 'window': f'{st}~{end}', 'prev': f'{ps}~{pe}',
            'weeks': weeks, 'kw': rows[:25], 'ga4': g, 'behavior': beh}

def _notes_dirs():
    return [os.path.join(HERE, 'notes'), os.path.join(OUT, 'notes')]


def notes_for(rep):
    for d in _notes_dirs():
        p = os.path.join(d, f'{rep}.json')
        if os.path.exists(p):
            n = json.load(open(p, encoding='utf-8')); n['carried'] = False; return n
    prev = sorted({f for d in _notes_dirs() if os.path.isdir(d) for f in os.listdir(d)
                   if re.match(r'\d{4}-\d{2}-\d{2}\.json$', f) and f < f'{rep}.json'})
    if prev:
        f = prev[-1]
        p = next(os.path.join(d, f) for d in _notes_dirs() if os.path.exists(os.path.join(d, f)))
        n = json.load(open(p, encoding='utf-8'))
        n['carried'] = True; n['carried_from'] = f[:-5]; n['done'] = []
        return n
    return {'carried': True, 'done': [], 'next': [], 'geo': []}

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--date'); ap.add_argument('--out'); a = ap.parse_args()
    if a.out: OUT = os.path.abspath(os.path.expanduser(a.out))
    rep = date.fromisoformat(a.date) if a.date else date.today()
    data = build(rep); data['notes'] = notes_for(rep.isoformat())
    dd = os.path.join(OUT, 'data'); os.makedirs(dd, exist_ok=True)
    json.dump(data, open(os.path.join(dd, f'{rep}.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    idx = sorted({f[:-5] for f in os.listdir(dd) if re.match(r'\d{4}-\d{2}-\d{2}\.json$', f)}, reverse=True)
    json.dump({'reports': idx}, open(os.path.join(dd, 'index.json'), 'w'))
    print('ok', rep, len(data['weeks']), 'weeks', len(data['kw']), 'kw', '->', os.path.join(dd, f'{rep}.json'))
