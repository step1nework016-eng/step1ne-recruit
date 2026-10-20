#!/usr/bin/env python3
"""pipeline_dash/build.py — 給 Jim 看的帶案儀表板（https://pipeline.step1ne.com，通行碼門禁）

2026-10-20 Jacky 拍板：一週一頁、週日晚上封存、數字旁標「比上週」。

每次跑：
  1. 從 D1 重算「本週新增」與「累計到本週」（含每個數字背後的人選名單）
  2. 寫進 D1 pipeline_weekly（wk＝該週週一日期）；已封存（frozen=1）的週不再覆寫
  3. 跨週後第一次跑：上一週用日期重算一次「新增」後封存（累計保留上週最後一次跑的結果）
  4. 組 index.html（template.html＋所有週資料）→ wrangler deploy 到 step1ne 帳號的 Worker

人工欄位（球在誰、說明、Email 推薦、開發中客戶、新客戶、人選狀態覆寫）放在
D1 site_settings key=pipeline_dash_overlay（JSON），每週快照會連同當週的人工欄位一起存。

用法：
  python3 pipeline_dash/build.py               # 平常排程跑這個
  python3 pipeline_dash/build.py --no-deploy   # 只更新資料不上線
  python3 pipeline_dash/build.py --backfill    # 補過去幾週的「新增」（只補沒有的週）
部署需要 ~/.config/workflow-os/step1ne-frontend-cf.env（step1ne.com 那個帳號的 token）。
"""
import argparse
import base64
import gzip
import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import consultant_ops as c  # noqa: E402

ACCOUNT_PREFIX = '673dc235'          # Official@step1ne.com 帳號
CF_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-frontend-cf.env')
BACKFILL_FROM = dt.date(2026, 8, 31)  # 回填最早從這週開始
BD = None
ACTIVE = {'open', 'social_only'}   # social_only＝未簽約客戶先上社群的職缺
TABS = [('all', lambda o: True), ('jacky', lambda o: o == 'jacky'), ('phoebe', lambda o: o == 'phoebe')]
IV = {'interviewed', 'passed', 'ready', 'scheduled', 'pending_assessment', 'reported', 'rejected', 'closed'}
STL = {'stage1': '客戶一面', 'stage2': '客戶二面', 'offer': '談 Offer', 'onboard': '已到職'}
KEYS = ('app', 'iv', 'fw', 'active', 'ci', 'of', 'on')
ALIAS = [('橘子', '遊戲橘子'), ('美德', '美德'), ('弘昌', '弘昌'), ('律准', '律准'), ('築樂', '築樂'),
         ('海德生', '海德生'), ('新享動', '新享動'), ('華上', '華上'), ('招待所', '私人招待所'),
         ('宇泰華', '宇泰華'), ('鏮正', '鏮正')]


def short(name, cid):
    if not name or cid == 'co_step1ne' or (cid or '').startswith('co_blk'):
        return None
    for k, v in ALIAS:
        if k in name:
            return v
    return re.sub(r'（.*?）|\(.*?\)|股份有限公司|有限公司|集團', '', name).strip() or None


def loc(v):
    v = v or ''
    if v.strip().startswith('['):
        for p in (json.loads, ast.literal_eval):
            try:
                return '、'.join(map(str, p(v)))
            except Exception:
                pass
    return v.strip()


def nm(n):
    return (n or '（未填名）').strip()


def week_start(d):
    return d - dt.timedelta(days=d.weekday())


def load():
    jobs = c.rows("SELECT j.slug,j.title,j.status,j.locations,j.company_id cid,"
                  "COALESCE(cc.display_name,j.client_name,'') co,COALESCE(cc.relation,'') rel FROM jobs j "
                  "LEFT JOIN client_companies cc ON cc.id=j.company_id", [])
    apps = c.rows("SELECT id,name,job_slug,status,created_at,interview_ended_at,"
                  "LOWER(COALESCE(owner,'')) owner FROM applications WHERE status!='duplicate'", [])
    fws = c.rows("SELECT f.application_id,a.name,COALESCE(f.job_slug,a.job_slug) js,"
                 "COALESCE(f.manual_stage,'') st,f.manual_stage_at sat,f.forwarded_at fat,"
                 "f.client_rejected_at r,f.advisor_not_recommended_at nr,f.candidate_withdrawn_at w,"
                 "LOWER(COALESCE(a.owner,'')) owner FROM candidate_forwards f "
                 "JOIN applications a ON a.id=f.application_id", [])
    # 報到日還沒到的「已到職」先算錄取（2026-10-20 邱萬豪：客戶上傳 offer 時標了到職，實際 11/10 才報到）
    od = {r['application_id']: r['d'] for r in c.rows(
        "SELECT application_id, COALESCE(actual_start_date, onboard_date, expected_start_date, offer_start_date) d "
        "FROM placements WHERE application_id IS NOT NULL", []) if r['d']}
    today = str(c.now_tpe())[:10]
    for f in fws:
        d = od.get(f['application_id'])
        if f['st'] == 'onboard' and d and d[:10] > today:
            f['st'], f['start'] = 'offer', d[:10]
    for j in jobs:
        j['g'] = short(j['co'], j['cid'])
        t = j['title']
        l = re.split(r'[（(，,]', loc(j['locations']))[0].strip()
        if l and l not in t:
            t += f'｜{l}'
        if j['status'] not in ACTIVE:
            t += '（已關閉）'
        j['t'] = t
    return {j['slug']: j for j in jobs if j['g']}, apps, fws


def fw_label(f, ovr):
    n = nm(f['name'])
    if f['st'] == 'offer' and f['w']:
        lab = '錄取後未報到'
    elif f.get('start'):
        lab = f"已錄取，{int(f['start'][5:7])}/{int(f['start'][8:10])} 報到"
    elif f['r']:
        lab = '客戶婉拒'
    elif f['nr']:
        lab = '顧問不推'
    elif f['w']:
        lab = '人選退出'
    else:
        lab = STL.get(f['st'], '等客戶回覆')
    return n, ovr.get(n, lab)


def build_view(jobmap, apps, fws, ovr, mode, ws=None, we=None):
    """mode='cum'：開放中職缺＋已關閉但有錄取的職缺，全部歷史；
       mode='new'：只算日期落在 [ws, we) 的事件（新應徵、面談完成、推薦、客戶面試／錄取／到職）。"""
    def inwin(ts):
        return ts and ws <= ts[:10] < we
    hired = {f['js'] for f in fws if f['st'] in ('offer', 'onboard')}
    # 已關閉的職缺：有錄取、或最近 30 天還有推薦（例如客戶暫緩的職缺）也要算
    recent = str(dt.date.fromisoformat(str(c.now_tpe())[:10]) - dt.timedelta(days=30))
    hired |= {f['js'] for f in fws if (f['fat'] or '') >= recent and not f['nr']}
    if mode == 'cum':
        scope = {s for s, j in jobmap.items() if j['status'] in ACTIVE or s in hired}
    else:
        scope = set(jobmap)
    signed = {j['g'] for j in jobmap.values() if j['rel'] in ('signed', 'private')}
    out = {}
    for tab, flt in TABS:
        D = defaultdict(dict)

        def slot(slug):
            j = jobmap[slug]
            return D[j['g']].setdefault(slug, {'t': j['t'], 'open': j['status'] in ACTIVE, **{k: [] for k in KEYS}})
        if mode == 'cum':
            for s in scope:
                slot(s)
        for a in apps:
            if a['job_slug'] not in scope or not flt(a['owner']):
                continue
            if mode == 'cum' or inwin(a['created_at']):
                slot(a['job_slug'])['app'].append(nm(a['name']))
            if (mode == 'cum' and a['status'] in IV) or (mode == 'new' and inwin(a['interview_ended_at'])):
                slot(a['job_slug'])['iv'].append(nm(a['name']))
        for f in fws:
            if f['js'] not in scope or not flt(f['owner']) or f['nr']:
                continue   # 顧問不推＝沒有真的推給客戶，不算推薦
            n, lab = fw_label(f, ovr)
            done = f['r'] or f['nr'] or f['w'] or f['st'] == 'onboard'
            if mode == 'cum' or inwin(f['fat']):
                x = slot(f['js'])
                x['fw'].append(f'{n}（{lab}）')
                if not done:
                    x['active'].append(n)
            stage_hit = mode == 'cum' or inwin(f['sat'])
            if f['st'] in STL and stage_hit:
                x = slot(f['js'])
                x['ci'].append(f'{n}（{lab}）')
                if f['st'] in ('offer', 'onboard'):
                    x['of'].append(f'{n}（{lab}）')
                if f['st'] == 'onboard':
                    x['on'].append(n)
        det = {g: [{'slug': s, **v} for s, v in sorted(js.items(), key=lambda kv: (not kv[1]['open'], -len(kv[1]['app'])))]
               for g, js in D.items()}
        rows = [{'client': g, 'signed': g in signed, 'jobs': sum(1 for j in js if j['open']), **{k: sum(len(j[k]) for j in js) for k in KEYS}}
                for g, js in det.items()]
        if mode == 'new':
            rows = [r for r in rows if any(r[k] for k in KEYS)]
            det = {g: v for g, v in det.items() if g in {r['client'] for r in rows}}
        out[tab] = {'rows': sorted(rows, key=lambda x: (-x['app'], -x['fw'], -x['jobs'])), 'det': det}
    return out


RES = {'gatekeeper': '總機擋下', 'got_contact': '拿到窗口', 'got_email': '拿到信箱', 'interested': '有興趣',
       'no_answer': '沒接', 'not_hiring': '目前沒在徵', 'not_interested': '沒興趣'}
KIND = {'call': '電話', 'mail': '寄信', 'other': '其他'}
STAGE_ORDER = ['未聯繫', '已聯繫', '拿到窗口', '已寄資料', '有興趣', '談需求', '已開職缺']


def load_bd():
    st = c.rows("SELECT company,stage,tag,next_step,next_due,parked_at,updated_by FROM bd_company_status", [])
    logs = c.rows("SELECT company,kind,result,note,caller,COALESCE(event_at,created_at) at,mail_sent FROM bd_call_logs "
                  "WHERE deleted_at IS NULL ORDER BY COALESCE(event_at,created_at) DESC", [])
    own = {}
    for r in c.rows("SELECT company,assigned_to FROM bd_outreach WHERE assigned_to IS NOT NULL AND status!='superseded' "
                    "ORDER BY updated_at", []):
        own[r['company']] = r['assigned_to']
    return st, logs, own


def log_text(l):
    k = KIND.get(l['kind'] or '', '電話' if l['result'] else '紀錄')
    res = RES.get(l['result'] or '', l['result'] or '')
    note = (l['note'] or '').strip()
    note = note if 0 < len(note) <= 60 else ''
    return f"{l['at'][5:10].replace('-', '/')} {k}" + (f'：{res}' if res else '') + (f'（{note}）' if note else '') + f"｜{(l['caller'] or '').capitalize()}"


def derive_stage(s, ls):
    if s.get('stage'):
        return s['stage']
    rs = {l['result'] for l in ls}
    if 'interested' in rs:
        return '有興趣'
    if any(l['kind'] == 'mail' or l['mail_sent'] for l in ls):
        return '已寄資料'
    if rs & {'got_contact', 'got_email', '拿到窗口', '查到窗口'}:
        return '拿到窗口'
    return '已聯繫' if ls else '未聯繫'


def bd_view(bd, recruit_rows, ws, we):
    st, logs, own = bd
    by = defaultdict(list)
    for l in logs:
        by[l['company']].append(l)
    unsigned = {r['client']: r for r in recruit_rows if not r.get('signed')}
    out = {}
    for tab, flt in TABS:
        lst, wk, seen = [], [], set()
        kc = defaultdict(int)
        for s in st:
            if s['parked_at']:
                continue
            co = s['company']
            owner = own.get(co) or s['updated_by'] or ''
            if not flt(owner.lower()):
                continue
            ls = by.get(co, [])
            sg = derive_stage(s, ls)
            nm_ = short(co, '') or co
            item = {'company': nm_, 'owner': owner, 'stage': sg, 'hot': s['tag'] == 'HOT',
                    'last': log_text(ls[0]) if ls else '', 'next': s['next_step'] or '', 'due': (s['next_due'] or '')[5:].replace('-', '/'),
                    'logs': [log_text(l) for l in ls[:10]]}
            r = unsigned.get(nm_)
            if r:
                item['recruit'] = f"已開 {r['jobs']} 缺｜應徵 {r['app']}｜推薦 {r['fw']}"
                item['stage'] = sg = '已開職缺'
            seen.add(nm_)
            lst.append(item)
            kc[sg] += 1
            wl = [l for l in ls if ws <= l['at'][:10] < we]
            if wl:
                wk.append({**item, 'logs': [log_text(l) for l in wl]})
        if tab == 'all':   # 沒在開發看板上、但已經開職缺的未簽約客戶（例如先上社群的）
            for g, r in unsigned.items():
                if g not in seen and (r['app'] or r['fw'] or r['jobs']):
                    lst.append({'company': g, 'owner': '', 'stage': '已開職缺', 'hot': False, 'last': '', 'next': '', 'due': '', 'logs': [],
                                'recruit': f"已開 {r['jobs']} 缺｜應徵 {r['app']}｜推薦 {r['fw']}"})
                    kc['已開職缺'] += 1
        def kpi(items, logs_in):
            return {'companies': len(items),
                    'contacted': sum(1 for i in items if i['stage'] != '未聯繫'),
                    'interested': sum(1 for i in items if i['stage'] in STAGE_ORDER[4:]),
                    'hot': sum(1 for i in items if i['hot'])}
        wlogs = [l for i in wk for l in i['logs']]
        out[tab] = {
            'cum': {'kpi': kpi(lst, None), 'stages': {k: kc.get(k, 0) for k in STAGE_ORDER},
                    'list': sorted(lst, key=lambda i: (-(STAGE_ORDER.index(i['stage']) if i['stage'] in STAGE_ORDER else 0), not i['hot'], i['company']))},
            'new': {'kpi': {'companies': len(wk), 'calls': sum(1 for t in wlogs if ' 電話' in t), 'mails': sum(1 for t in wlogs if ' 寄信' in t),
                            'interested': sum(1 for t in wlogs if '有興趣' in t)},
                    'list': sorted(wk, key=lambda i: -len(i['logs']))}}
    return out


def days(apps, ws):
    res = {}
    for tab, flt in TABS:
        cnt = defaultdict(int)
        for a in apps:
            t = a['interview_ended_at']
            if t and flt(a['owner']):
                cnt[t[:10]] += 1
        res[tab] = [cnt.get(str(ws + dt.timedelta(days=i)), 0) for i in range(7)]
    return res


def ensure_tables():
    c.rows("CREATE TABLE IF NOT EXISTS pipeline_weekly (wk TEXT PRIMARY KEY, data TEXT NOT NULL, "
           "frozen INTEGER NOT NULL DEFAULT 0, updated_at TEXT)", [])


def overlay():
    r = c.rows("SELECT value FROM site_settings WHERE key='pipeline_dash_overlay'", [])
    return json.loads(r[0]['value']) if r else {}


def snapshot(jobmap, apps, fws, ov, ws, now, with_cum):
    we = ws + dt.timedelta(days=7)
    ovr = ov.get('status_override', {})
    s = {'wk': str(ws), 'label': f'{ws.month}/{ws.day}–{(we - dt.timedelta(days=1)).month}/{(we - dt.timedelta(days=1)).day}',
            'upd': now, 'new': build_view(jobmap, apps, fws, ovr, 'new', str(ws), str(we)),
            'cum': build_view(jobmap, apps, fws, ovr, 'cum') if with_cum else None,
            'days': days(apps, ws), 'ov': ov if with_cum else None}
    rr = s['cum']['all']['rows'] if s['cum'] else build_view(jobmap, apps, fws, ovr, 'cum')['all']['rows']
    s['bd'] = bd_view(BD, rr, str(ws), str(we))
    if not with_cum:   # 過去的週只留「這週新增」；開發公司的累計清單是當下狀態，放進舊週會誤導
        s['bd'] = {t: {'cum': None, 'new': v['new']} for t, v in s['bd'].items()}
    return s


def pack(obj):
    # D1 一次寫入有大小上限（約 100KB），快照壓縮後再存
    return 'gz:' + base64.b64encode(gzip.compress(json.dumps(obj, ensure_ascii=False).encode())).decode()


def unpack(s):
    return json.loads(gzip.decompress(base64.b64decode(s[3:]))) if s.startswith('gz:') else json.loads(s)


def save(snap, frozen):
    c.rows("INSERT INTO pipeline_weekly(wk,data,frozen,updated_at) VALUES(?,?,?,?) "
           "ON CONFLICT(wk) DO UPDATE SET data=excluded.data,frozen=excluded.frozen,updated_at=excluded.updated_at",
           [snap['wk'], pack(snap), frozen, snap['upd']])


def run(backfill=False):
    ensure_tables()
    now = str(c.now_tpe())[:16]
    today = dt.date.fromisoformat(now[:10])
    cur = week_start(today)
    global BD
    jobmap, apps, fws = load()
    BD = load_bd()
    ov = overlay()
    have = {r['wk']: r for r in c.rows("SELECT wk,frozen,data FROM pipeline_weekly", [])}
    # 跨週封存：比本週早、還沒封存的週 → 用日期重算「新增」，累計沿用最後一次跑的結果
    for wk, r in have.items():
        if wk < str(cur) and not r['frozen']:
            old = unpack(r['data'])
            snap = snapshot(jobmap, apps, fws, old.get('ov') or ov, dt.date.fromisoformat(wk), old['upd'], False)
            snap['cum'], snap['ov'] = old.get('cum'), old.get('ov')
            snap.pop('prev_same', None)
            snap.pop('bd_prev_same', None)
            save(snap, 1)
            print('封存', wk)
    if backfill:
        w = BACKFILL_FROM
        while w < cur:
            if str(w) not in have:
                s = snapshot(jobmap, apps, fws, ov, w, now, False)
                s['backfill'] = True
                save(s, 1)
                print('回填', w)
            w += dt.timedelta(days=7)
    snap = snapshot(jobmap, apps, fws, ov, cur, now, True)
    # 本週還沒過完：另存「上週同一段日期」的新增數，畫面上比上週同期才公平
    pws = cur - dt.timedelta(days=7)
    snap['prev_same'] = build_view(jobmap, apps, fws, ov.get('status_override', {}), 'new',
                                   str(pws), str(pws + dt.timedelta(days=(today - cur).days + 1)))
    pbd = bd_view(BD, [], str(pws), str(pws + dt.timedelta(days=(today - cur).days + 1)))
    snap['bd_prev_same'] = {t: v['new']['kpi'] for t, v in pbd.items()}
    save(snap, 0)
    print('本週已更新', cur, now)


def render():
    rows = c.rows("SELECT data FROM pipeline_weekly ORDER BY wk DESC LIMIT 26", [])
    weeks = [unpack(r['data']) for r in rows]
    tpl = open(os.path.join(HERE, 'template.html'), encoding='utf-8').read()
    html = tpl.replace('/*__DATA__*/null', json.dumps({'weeks': weeks}, ensure_ascii=False))
    open(os.path.join(HERE, 'index.html'), 'w', encoding='utf-8').write(html)
    return len(html)


def deploy():
    env = dict(os.environ)
    for line in open(CF_ENV, encoding='utf-8'):
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.strip().split('=', 1)
            env[k.strip()] = v.strip().strip('"\'')
    import urllib.request
    req = urllib.request.Request('https://api.cloudflare.com/client/v4/accounts',
                                 headers={'Authorization': f"Bearer {env['CLOUDFLARE_API_TOKEN']}"})
    accts = json.load(urllib.request.urlopen(req, timeout=30))['result']
    env['CLOUDFLARE_ACCOUNT_ID'] = [a['id'] for a in accts if a['id'].startswith(ACCOUNT_PREFIX)][0]
    p = subprocess.run(['npx', 'wrangler', 'deploy'], cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise SystemExit('部署失敗：' + (p.stderr or p.stdout)[-800:])
    print('已上線', re.findall(r'Current Version ID: \S+', p.stdout))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-deploy', action='store_true')
    ap.add_argument('--backfill', action='store_true')
    a = ap.parse_args()
    run(a.backfill)
    print('index.html', render(), 'bytes')
    if not a.no_deploy:
        deploy()
