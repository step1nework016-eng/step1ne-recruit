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
    out = {'site': site[0] if site else {}, 'channels': ch, 'devices': dev, 'landing_top': top, 'landing_ent': ent[:10]}
    # 2026-10-12 新增三張圖的資料（漏斗／路徑／手機vs電腦）。任何一塊失敗都不影響舊欄位。
    for k, fn in (('funnel_seeker', funnel_seeker), ('funnel_employer', funnel_employer),
                  ('paths', top_paths), ('device_funnel', device_funnel)):
        try:
            out[k] = fn(c, a, b)
        except Exception as ex:
            print('⚠️', k, '算不出來：', repr(ex)[:200])
    return out

# ───────── 2026-10-12：使用者行為三張圖（漏斗／路徑／手機vs電腦） ─────────
INAPP = ('Safari (in-app)', 'Android Webview')   # GA4 只分得出「App 內建瀏覽器」，分不出是 LINE、IG 還是 Threads
SKIP_PATH = ('/consultant', '/admin', '/pick', '/chat/')   # 內部頁／面談連結不算使用者路徑
PROP = 'properties/547366147'


def _fs(field, value, mt='EXACT'):
    from google.analytics.data_v1beta.types import FilterExpression, Filter
    return FilterExpression(filter=Filter(field_name=field, string_filter=Filter.StringFilter(
        value=value, match_type=getattr(Filter.StringFilter.MatchType, mt))))


def _and(*xs):
    from google.analytics.data_v1beta.types import FilterExpression, FilterExpressionList
    return FilterExpression(and_group=FilterExpressionList(expressions=[x for x in xs if x is not None]))


def _or(*xs):
    from google.analytics.data_v1beta.types import FilterExpression, FilterExpressionList
    return FilterExpression(or_group=FilterExpressionList(expressions=list(xs)))


def _rows(c, a, b, dims, mets, flt=None, limit=10000):
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric
    r = c.run_report(RunReportRequest(property=PROP, date_ranges=[DateRange(start_date=a, end_date=b)],
        dimensions=[Dimension(name=d) for d in dims], metrics=[Metric(name=m) for m in mets],
        dimension_filter=flt, limit=limit))
    return [([v.value for v in x.dimension_values], [float(v.value) for v in x.metric_values]) for x in r.rows]


def _users(c, a, b, flt, by=None):
    """符合條件的不重複人數；by＝再依哪個維度拆（回 {值: 人數}），不拆就回一個數字。"""
    rows = _rows(c, a, b, [by] if by else [], ['totalUsers'], flt)
    if by:
        return {d[0]: int(m[0]) for d, m in rows}
    return int(rows[0][1][0]) if rows else 0


def _d1(sql, params=None):
    """讀系統紀錄（D1）。讀不到就回 None，圖上那一格標「沒取得」，不讓整份週報失敗。"""
    import urllib.request
    try:
        e = {}
        for l in open(os.path.expanduser('~/.config/workflow-os/cf.env'), encoding='utf-8'):
            l = l.strip()
            if l and not l.startswith('#') and '=' in l:
                k, _, x = l.partition('='); e[k.strip()] = x.strip().strip('"\'')
        r = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{e['CLOUDFLARE_ACCOUNT_ID']}/d1/database/67077b4b-42d9-4086-8d04-b3658a89cffd/query",
            data=json.dumps({'sql': sql, 'params': params or []}).encode(),
            headers={'Authorization': 'Bearer ' + e['CLOUDFLARE_API_TOKEN'], 'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(r, timeout=60))['result'][0]['results']
    except Exception as ex:
        print('⚠️ D1 讀不到：', repr(ex)[:150])
        return None


def _next_day(b):
    return (date.fromisoformat(b) + timedelta(days=1)).isoformat()


def _seeker_filters():
    """求職者四步的 GA 條件。注意：各步驟是分開算的人數，不是同一批人一路追下去。"""
    app = _fs('pagePath', '/apply', 'BEGINS_WITH')          # /apply/ 和 /apply-beta/ 都算
    return [
        ('進職缺頁', _fs('pagePath', '/jobs/', 'BEGINS_WITH')),
        ('進應徵頁', app),
        ('開始填表', _and(_fs('eventName', 'form_start'), app)),
        ('送出應徵', _and(_fs('eventName', 'form_submit'), app)),
    ]


def funnel_seeker(c, a, b):
    steps = [{'k': n, 'n': _users(c, a, b, f), 'src': 'ga'} for n, f in _seeker_filters()]
    # 系統紀錄：只算網站實際送進來的（排除測試、顧問自己加的、AI 找人、LinkedIn 手動匯入、104 下載匯入）
    ok = "origin NOT IN ('測試','顧問加入','AI 找人','LinkedIn','104')"
    r = _d1(f"SELECT COUNT(*) n, SUM(interview_started_at IS NOT NULL) st FROM applications "
            f"WHERE created_at>=? AND created_at<? AND {ok}", [a, _next_day(b)])
    steps.append({'k': '系統收到應徵', 'n': int(r[0]['n']) if r else None, 'src': 'db'})
    steps.append({'k': '進阿財面談', 'n': int(r[0]['st'] or 0) if r else None, 'src': 'db'})
    extra = {}
    for ev in ('apply_section_complete', 'resume_method_select', 'apply_blocked', 'apply_abandon', 'apply_think_open'):
        rr = _rows(c, a, b, ['eventName'], ['eventCount', 'totalUsers'], _fs('eventName', ev))
        extra[ev] = {'events': int(rr[0][1][0]), 'users': int(rr[0][1][1])} if rr else {'events': 0, 'users': 0}
    return {'steps': steps, 'extra': extra}


def funnel_employer(c, a, b):
    ent = _or(*[_fs('pagePath', e, 'CONTAINS') for e in ENT + ['hiring-assessment']])
    leads = _or(_fs('eventName', 'employer_lead_submit'), _fs('eventName', 'hiring_assessment_lead'))
    steps = [
        {'k': '進企業端頁面', 'n': _users(c, a, b, ent), 'src': 'ga'},
        {'k': '看委託招募方案頁', 'n': _users(c, a, b, _fs('pagePath', '/commission-recruiting/', 'BEGINS_WITH')), 'src': 'ga'},
        {'k': '送出需求表單', 'n': _users(c, a, b, leads), 'src': 'ga'},
    ]
    r = _d1("SELECT COUNT(*) n FROM company_leads WHERE source_kind='website' AND created_at>=? AND created_at<?",
            [a, _next_day(b)])
    steps.append({'k': '系統收到官網詢問', 'n': int(r[0]['n']) if r else None, 'src': 'db'})
    return {'steps': steps}


def _norm_path(u):
    from urllib.parse import urlparse
    p = urlparse(u)
    if p.netloc not in ('step1ne.com', 'www.step1ne.com'):
        return None
    return p.path or '/'


def top_paths(c, a, b, n=10, min_n=2):
    """站內「上一頁 → 這一頁」。做法：GA4 page_view 事件的 pageReferrer（來源網址）＋ pagePath（這頁），
    只留來源在 step1ne.com 內的；同一頁重新整理（A→A）、內部頁不算。算的是「次數」，不是人數。"""
    rows = _rows(c, a, b, ['pageReferrer', 'pagePath'], ['eventCount', 'totalUsers'],
                 _and(_fs('eventName', 'page_view'), _fs('pageReferrer', 'https://step1ne.com', 'BEGINS_WITH')))
    agg = defaultdict(lambda: [0, 0])
    for (ref, path), (ev, us) in rows:
        src = _norm_path(ref)
        if not src or src == path or any(x in src or x in path for x in SKIP_PATH):
            continue
        agg[(src, path)][0] += int(ev); agg[(src, path)][1] += int(us)
    flows = sorted(((k, v) for k, v in agg.items() if v[0] >= min_n), key=lambda kv: -kv[1][0])[:n]
    return {'method': 'GA4 page_view 的 pageReferrer → pagePath（站內），算次數',
            'total_transitions': sum(v[0] for v in agg.values()),
            'flows': [{'from': k[0], 'from_title': page_title('https://step1ne.com' + k[0]),
                       'to': k[1], 'to_title': page_title('https://step1ne.com' + k[1]), 'n': v[0], 'u': v[1]}
                      for k, v in flows]}


def device_funnel(c, a, b):
    """同一條求職者漏斗，依「裝置」與「是否在 App 內建瀏覽器」拆開。人數是各步驟分開算的不重複人數。"""
    names = [n for n, _ in _seeker_filters()]
    by_dev = {}; by_br = {}
    for i, (n, f) in enumerate(_seeker_filters()):
        for d, v in _users(c, a, b, f, 'deviceCategory').items():
            by_dev.setdefault(d, [0] * 4)[i] = v
        for br, v in _users(c, a, b, f, 'browser').items():
            g = by_br.setdefault('in_app' if br in INAPP else 'other', [0] * 4)
            g[i] += v        # 同一人不太可能同時用兩種瀏覽器，直接加總（略有重複，量級可忽略）
    sess = {d[0]: int(m[0]) for d, m in _rows(c, a, b, ['deviceCategory'], ['sessions'])}
    brs = {d[0]: int(m[0]) for d, m in _rows(c, a, b, ['browser'], ['sessions'])}
    tot = sum(brs.values()) or 1
    inapp_s = sum(v for k, v in brs.items() if k in INAPP)
    return {'steps': names,
            'devices': [{'device': d, 'sessions': sess.get(d, 0), 'users': v} for d, v in
                        sorted(by_dev.items(), key=lambda kv: -sess.get(kv[0], 0))],
            'inapp': {'sessions': inapp_s, 'share': round(inapp_s / tot, 3), 'users': by_br.get('in_app', [0] * 4),
                      'others': by_br.get('other', [0] * 4),
                      'method': "GA4 瀏覽器欄位＝'Safari (in-app)' 或 'Android Webview'；分不出是 LINE、IG 還是 Threads"}}


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
