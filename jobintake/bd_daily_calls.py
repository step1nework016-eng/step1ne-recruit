#!/usr/bin/env python3
"""每天早上給每位顧問一張「今天要打的 N 家」清單，推 TG（2026-09-30 Jacky：本週簽 20 家，要突破）。

資料來源：顧問後台同一支開發進度 API（/admin/bd/dashboard?fresh=1），跟後台看到的完全一致。

每位顧問一則訊息：
  ① 今天到期的跟進（客戶跟進分頁排了今天以前日期的、或 AI 整理建議今天以前要做的）
  ② 今天要打的 N 家新客戶，照「熱度」排：
     對方回過信 > 寄過開發信（問有沒有收到）> 有新消息／切入點 > 手上有適合的人選 > 有人資電話或分機 > 外商 > 有具體職缺
只挑：指派給這位、資料合格（電話＋104＋官網）、還沒聯繫過、沒打過電話、不在備選池、沒排未來日期的。

每天 08:15 由 WSL2 systemd timer（step1ne-bddailycalls）執行。
用法：python3 bd_daily_calls.py [--dry-run] [--n 45]
"""
import datetime
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
API = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
CONSULTANTS = ['Jacky', 'Phoebe']
ACT = {'call': '📞 打電話', 'mail': '✉️ 寄信', 'wait': '⏳ 等回覆', 'meet': '🤝 約見面'}


def env_file(name):
    out = {}
    p = os.path.expanduser(f'~/.config/workflow-os/{name}')
    if os.path.exists(p):
        for ln in open(p, encoding='utf-8'):
            k, _, v = ln.strip().partition('=')
            if k and not k.startswith('#'):
                out[k.strip()] = v.strip().strip('\'"')
    return out


def dashboard():
    tok = env_file('recruit.env').get('RECRUIT_ADMIN_TOKEN') or os.environ.get('RECRUIT_ADMIN_TOKEN', '')
    req = urllib.request.Request(f'{API}/admin/bd/dashboard?fresh=1', headers={'Authorization': f'Bearer {tok}', 'User-Agent': 'step1ne-bd-daily-calls/1.0'})
    return json.load(urllib.request.urlopen(req, timeout=90))['rows']


def short(name):
    for x in ('股份有限公司', '有限公司'):
        name = name.replace(x, '')
    return name.split('（')[0].split('(')[0][:14]


def phone(r):
    if r.get('hr_phone'):
        return f"人資 {r['hr_phone']}"
    p = r.get('call_phone') or r.get('company_phone') or ''
    return f"總機 {p}" if p else '（沒電話）'


def score(r):
    s, why = 0, []
    st = r.get('stage')
    if st in ('replied', 'jd_shared'):
        s += 50; why.append('對方回過信')
    elif r.get('channel') in ('email', 'both') and st in ('sent', 'delivered', 'opened'):
        s += 20; why.append('寄過信' + ('、有開信' if st == 'opened' else '') + '，問有沒有收到')
        if st == 'opened':
            s += 5
    if r.get('trigger'):
        s += 12; why.append('有新消息')
    if r.get('has_candidate'):
        s += 10; why.append('有適合人選')
    if r.get('hr_phone') or '#' in str(r.get('call_phone') or '') or '分機' in str(r.get('call_phone') or ''):
        s += 6; why.append('有人資電話／分機')
    if str(r.get('industry') or '').startswith('外商'):
        s += 5; why.append('外商')
    if r.get('job_title'):
        s += 3
    return s, why


def build(rows, name, n, today):
    mine = [r for r in rows if r.get('assigned_to') == name and not r.get('parked_at')]
    follow = []
    for r in mine:
        due, act = r.get('next_due'), r.get('next_action')
        if not (due or act) and r.get('ai_suggest'):
            due, act = r['ai_suggest'].get('due'), r['ai_suggest'].get('action')
        if due and due <= today and act != 'pause':
            follow.append((due, r, act))
    follow.sort(key=lambda x: (x[0], x[1]['company']))
    fresh = [r for r in mine if not r.get('qc_missing') and not r.get('contacted_at') and not r.get('last_call_result')
             and not r.get('next_due') and r.get('stage') not in ('closed',)]
    ranked = sorted(fresh, key=lambda r: -score(r)[0])[:n]
    return follow, ranked, len(fresh)


def render(name, follow, ranked, pool, today):
    d = datetime.date.fromisoformat(today)
    lines = [f"☎️ {name}｜今天要打（{d.month}/{d.day}）", '']
    if follow:
        lines.append(f"📌 先做：到期的跟進 {len(follow)} 家")
        for due, r, act in follow[:15]:
            late = '⚠️過期 ' if due < today else ''
            lines.append(f"・{late}{short(r['company'])}｜{ACT.get(act, '跟進')}｜{phone(r)}")
        lines.append('')
    lines.append(f"🆕 新客戶 {len(ranked)} 家（最熱的排前面；還有 {max(0, pool - len(ranked))} 家沒排進來）")
    for i, r in enumerate(ranked, 1):
        _, why = score(r)
        lines.append(f"{i}. {short(r['company'])}｜{phone(r)}" + (f"｜{'、'.join(why[:2])}" if why else ''))
    lines += ['', '打完當下在卡片「記錄電話結果」＋選下一步和日期，明天清單就會自動更新。',
              '後台：https://step1ne.com/consultant/ → 客戶 → 開發進度']
    return '\n'.join(lines)


def tg_send(text):
    e = env_file('step1ne-tg.env')
    topic = 6416
    chunks, cur = [], ''
    for ln in text.split('\n'):
        if len(cur) + len(ln) + 1 > 3800:
            chunks.append(cur); cur = ''
        cur += ln + '\n'
    chunks.append(cur)
    for c in chunks:
        body = {'chat_id': e['TG_CHAT_ID'], 'text': c, 'message_thread_id': topic, 'disable_web_page_preview': True}
        req = urllib.request.Request(f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                                     data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        json.load(urllib.request.urlopen(req, timeout=20))


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    n = int(args[args.index('--n') + 1]) if '--n' in args else 45
    today = datetime.datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    rows = dashboard()
    for name in CONSULTANTS:
        follow, ranked, pool = build(rows, name, n, today)
        text = render(name, follow, ranked, pool, today)
        if dry:
            print(text); print('─' * 30)
        else:
            tg_send(text)
            print(name, '已推 TG', len(follow), len(ranked), flush=True)


if __name__ == '__main__':
    main()
