#!/usr/bin/env python3
"""發財基地行事曆：程式直接建立／查詢事件（不用開 Chrome）。
鑰匙：~/.config/workflow-os/pm-session.env 的 PM_REFRESH_TOKEN（由 pm_login.py 產生），每次用完會換成新的一把寫回去。
用法：
  python3 pm_calendar.py list 2026-10-08 2026-10-14
  python3 pm_calendar.py add "電洽｜XXX｜職缺" 2026-10-09 16:00 16:30 --loc "0912-345-678" [--invite Phoebe] [--remind 30]
同一天同標題已存在就不重複建立。預設不邀請任何人（邀請會發 TG 給對方）。"""
import json, os, sys, argparse, urllib.request, datetime
ENV = os.path.expanduser('~/.config/workflow-os/pm-session.env')
API = 'https://pm.aijob.com.tw/api/v1'

def _req(method, path, body=None, token=None):
    h = {'Content-Type': 'application/json', 'User-Agent': 'step1ne-ops'}
    if token: h['Authorization'] = 'Bearer ' + token
    r = urllib.request.Request(API + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(r, timeout=20) as resp:
        t = resp.read().decode()
        return json.loads(t) if t else None

def token():
    rt = dict(l.strip().split('=', 1) for l in open(ENV) if '=' in l).get('PM_REFRESH_TOKEN')
    if not rt: raise SystemExit('還沒登入：請在終端機跑 python3 ~/claude-projects/工作流程技能包/_tools/pm_login.py')
    try:
        d = _req('POST', '/auth/refresh', {'refreshToken': rt})
    except urllib.error.HTTPError as e:
        raise SystemExit(f'換新鑰匙失敗（{e.code}），可能被登出了：請重跑 pm_login.py')
    if d.get('refreshToken') and d['refreshToken'] != rt:
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f: f.write(f"PM_REFRESH_TOKEN={d['refreshToken']}\n")
    return d['token']

def tw_to_utc(date, hm):
    dt = datetime.datetime.strptime(f'{date} {hm}', '%Y-%m-%d %H:%M') - datetime.timedelta(hours=8)
    return dt.strftime('%Y-%m-%dT%H:%M:00Z')

def list_events(tk, frm, to):
    return _req('GET', f'/calendar/events?from={frm}&to={to}', token=tk) or []

def add_event(title, date, start, end, loc=None, note=None, invite=(), remind=None, tk=None):
    tk = tk or token()
    start_utc = tw_to_utc(date, start)
    who = title.split('｜')[1] if title.count('｜') >= 1 else title
    for e in list_events(tk, date, date):
        # 同標題，或同一個時間、標題裡有同一個人名 → 視為已登記（手動建的標題寫法可能不一樣）
        if e.get('title') == title or (str(e.get('startAt', ''))[:16] == start_utc[:16] and who and who in (e.get('title') or '')):
            return {'skipped': True, 'id': e['id']}
    ids = []
    if invite:
        users = _req('GET', '/users/pickable', token=tk) or []
        for n in invite:
            u = next((x for x in users if x.get('name') == n), None)
            if not u: raise SystemExit(f'找不到成員：{n}')
            ids.append(u['id'])
    body = {'title': title, 'startAt': tw_to_utc(date, start), 'endAt': tw_to_utc(date, end), 'location': loc, 'note': note,
            'allDay': False, 'remindMinutes': remind, 'isPrivate': False, 'attendeeIds': ids}
    return _req('POST', '/calendar/events', body, token=tk)

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd')
    a = sub.add_parser('add'); a.add_argument('title'); a.add_argument('date'); a.add_argument('start'); a.add_argument('end')
    a.add_argument('--loc'); a.add_argument('--note'); a.add_argument('--invite', action='append', default=[]); a.add_argument('--remind', type=int)
    l = sub.add_parser('list'); l.add_argument('frm'); l.add_argument('to')
    x = ap.parse_args()
    if x.cmd == 'list':
        for e in list_events(token(), x.frm, x.to):
            print(e['startAt'], e['title'], '｜', ','.join(a['name'] for a in e.get('attendees') or []))
    elif x.cmd == 'add':
        r = add_event(x.title, x.date, x.start, x.end, x.loc, x.note, x.invite, x.remind)
        print('已存在，略過' if r.get('skipped') else f"✅ 已建立：{r.get('id')}")
    else:
        ap.print_help()
