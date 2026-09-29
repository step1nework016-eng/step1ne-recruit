#!/usr/bin/env python3
"""開發客戶每日數字（2026-09-29 Jacky：本週目標簽下 20 家，動員所有顧問一起打電話）。

每天 17:00 推到 TG「📬 開發信 開信・回信」主題（bot_topics.bd_signals）：
今天每位顧問打了幾通、各種結果幾通、拿到幾個人資電話／信箱、有興趣幾家、本週累計簽約幾家。
數字全部從 bd_outreach 讀（顧問在開發進度卡片上按「記錄電話結果」寫進去的），不估算。

用法：python3 bd_daily_report.py [--dry-run]
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D

ZH = {'no_answer': '沒接', 'gatekeeper': '被總機擋', 'got_contact': '問到窗口', 'got_email': '拿到信箱',
      'interested': '有興趣', 'not_hiring': '沒在徵人', 'not_interested': '拒絕'}
WEEK_GOAL = 20


def q(sql):
    r = D.query(sql)
    return (r.get('results') if isinstance(r, dict) else r) or []


def main():
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    monday = (datetime.datetime.now() - datetime.timedelta(days=datetime.datetime.now().weekday())).strftime('%Y-%m-%d')
    # 每一通都在 bd_call_logs 留一筆（同一家打三次就是三筆），日報照實算通數
    calls = q(f"SELECT COALESCE(caller,'（沒填是誰）') who, result r, COUNT(*) n FROM bd_call_logs "
              f"WHERE substr(created_at,1,10)='{today}' GROUP BY who, r")
    by_who = {}
    for c in calls:
        by_who.setdefault(c['who'], {})[c['r']] = c['n']
    assigned = q("SELECT COALESCE(assigned_to,'（未指派）') who, COUNT(DISTINCT company) n FROM bd_outreach "
                 "WHERE status NOT IN ('rejected','superseded','draft','blocked') GROUP BY who")
    hr = q(f"SELECT COUNT(DISTINCT company) n FROM bd_outreach WHERE hr_phone IS NOT NULL")[0]['n']
    phones = q("SELECT COUNT(DISTINCT company) n FROM bd_outreach WHERE hr_phone IS NOT NULL OR company_phone IS NOT NULL OR call_phone IS NOT NULL")[0]['n']
    total = q("SELECT COUNT(DISTINCT company) n FROM bd_outreach WHERE status NOT IN ('rejected','superseded','draft','blocked')")[0]['n']
    signed_week = q(f"SELECT COUNT(DISTINCT company) n FROM bd_outreach WHERE manual_stage='closed' AND substr(COALESCE(manual_stage_at,updated_at),1,10) >= '{monday}'")[0]['n']
    interested_week = q(f"SELECT COUNT(DISTINCT company) n FROM bd_call_logs WHERE result='interested' AND substr(created_at,1,10) >= '{monday}'")[0]['n']
    calls_week = q(f"SELECT COUNT(*) n FROM bd_call_logs WHERE substr(created_at,1,10) >= '{monday}'")[0]['n']

    lines = [f'📊 開發客戶日報｜{today}', '']
    lines.append(f'🎯 本週簽約：{signed_week}／{WEEK_GOAL} 家　有興趣：{interested_week} 家　本週累計電話：{calls_week} 通')
    lines.append(f'📇 名單 {total} 家，有電話 {phones} 家（人資電話 {hr} 家）')
    lines.append('')
    if by_who:
        lines.append('☎️ 今天各顧問電訪：')
        for who, rs in sorted(by_who.items(), key=lambda x: -sum(x[1].values())):
            detail = '、'.join(f'{ZH.get(k, k)} {v}' for k, v in sorted(rs.items(), key=lambda x: -x[1]))
            lines.append(f'・{who}：{sum(rs.values())} 通（{detail}）')
    else:
        lines.append('☎️ 今天還沒有人在卡片上記錄電話結果。')
    lines.append('')
    lines.append('👥 目前指派：' + '、'.join(f"{a['who']} {a['n']}" for a in sorted(assigned, key=lambda x: -x['n'])))
    lines.append('')
    lines.append('打完一通就在「客戶／BD → 開發進度」卡片按「記錄電話結果」，數字才算得到。')
    text = '\n'.join(lines)
    if '--dry-run' in sys.argv:
        print(text)
        return
    env = {}
    for ln in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8'):
        k, _, v = ln.strip().partition('=')
        env[k] = v.strip('"\'')
    topic = (q("SELECT topic_id FROM bot_topics WHERE key='bd_signals'") or [{}])[0].get('topic_id')
    body = {'chat_id': env['TG_CHAT_ID'], 'text': text}
    if topic:
        body['message_thread_id'] = topic
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    print(json.load(urllib.request.urlopen(req, timeout=20)).get('ok'))


if __name__ == '__main__':
    main()
