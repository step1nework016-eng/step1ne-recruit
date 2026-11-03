#!/usr/bin/env python3
"""每天早上阿財私訊 Jacky（2026-11-03 Jacky：「每天事先通知我當天有安排的電洽跟會議；如果沒有，就回顧昨天做了什麼、本週目標還缺什麼」）。

今天有安排 → 列出：發財基地行事曆＋電話提醒（consultant_reminders）＋面試排程（interview_appointments），同一件事只列一次。
今天沒安排 → 昨天做了什麼（從系統紀錄算）＋本週目標還缺什麼。
只放全民獵才的事（11/3 Jacky：「你負責全民獵才的就好」）。
要讀發財基地行事曆，所以只能在 Mac 跑（pm-session.env 不能跟 WSL2 共用）。
用法：python3 morning_brief.py [--dry-run] [--recap]（--recap：不管今天有沒有安排都附回顧，測試用）
"""
import datetime as dt, json, os, sys, urllib.parse, urllib.request
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import pm_calendar as pc
import consultant_ops as c

TZ = ZoneInfo('Asia/Taipei')
JACKY_TG = '8365775688'
now = dt.datetime.now(TZ)
today = now.date()
yday = today - dt.timedelta(days=1)
mon = today - dt.timedelta(days=today.weekday())
W = '一二三四五六日'
fmt = lambda d: f'{d.month}/{d.day}（{W[d.weekday()]}）'


def today_items():
    items = []
    try:
        tk = pc.token()
        for e in pc._req('GET', f'/calendar/events?from={today}&to={today + dt.timedelta(days=1)}', token=tk) or []:
            s = dt.datetime.fromisoformat(e['startAt'].replace('Z', '+00:00')).astimezone(TZ)
            if s.date() == today:
                items.append((s.strftime('%H:%M'), e.get('title') or '（沒標題）', 'cal'))
    except Exception as ex:
        items.append(('--:--', f'⚠️ 發財基地行事曆讀不到（{str(ex)[:60]}），下面只有系統裡的電話／面試', 'err'))
    d0, d1 = f'{today} 00:00', f'{today + dt.timedelta(days=1)} 00:00'
    for r in c.rows("""SELECT call_at, candidate_name, message FROM consultant_reminders
                       WHERE call_at>=? AND call_at<? AND status IN ('pending','sending','sent')""", [d0, d1]):
        items.append((str(r['call_at'])[11:16], f'⏰ 提醒｜{r["candidate_name"]}｜{c.clip(r.get("message") or "", 50)}', 'rem'))
    for r in c.rows("""SELECT i.confirmed_slot, i.stage, a.name, a.job_title, LOWER(COALESCE(a.owner,'')) owner
                       FROM interview_appointments i JOIN applications a ON a.id=i.application_id
                       WHERE i.confirmed_slot>=? AND i.confirmed_slot<?""", [d0, d1]):
        who = {'jacky': 'Jacky', 'phoebe': 'Phoebe'}.get(r['owner'], '未指派')
        items.append((str(r['confirmed_slot'])[11:16], f'📅 {r["name"]}｜{("第" + str(r["stage"]) + "關面試") if str(r.get("stage") or "").isdigit() else (r.get("stage") or "面試")}｜{r.get("job_title") or ""}（{who} 跟進）', 'appt'))
    # 同一件事（行事曆＋系統各一筆）只留系統那筆：同時間、名字出現在行事曆標題裡
    keep = []
    for t, txt, k in sorted(items):
        if k == 'cal' and any(t == t2 and k2 != 'cal' and txt2.split('｜')[0].strip('☎️📅 ') in txt for t2, txt2, k2 in items):
            continue
        keep.append((t, txt))
    return keep


def recap():
    a, b = f'{yday} 00:00', f'{today} 00:00'
    n = lambda q, p=(): (c.rows(q, list(p)) or [{'n': 0}])[0]['n']
    L = [f'昨天 {fmt(yday)} 系統記到的：']
    new_apps = c.rows("SELECT DISTINCT name FROM applications WHERE created_at>=? AND created_at<? AND name NOT LIKE '%測試%' ORDER BY created_at", [a, b])
    L.append(f'・新應徵 {len(new_apps)} 位' + (f'：{"、".join(x["name"] for x in new_apps[:6])}' if new_apps else ''))
    L.append(f'・跟阿財面談完 {n("SELECT COUNT(*) n FROM applications WHERE interview_ended_at>=? AND interview_ended_at<?", (a, b))} 位')
    fw = c.rows("""SELECT a.name, cc.display_name co FROM candidate_forwards f JOIN applications a ON a.id=f.application_id
                   LEFT JOIN client_companies cc ON cc.id=f.company_id WHERE f.forwarded_at>=? AND f.forwarded_at<?""", [a, b])
    rm = c.rows("SELECT subject FROM mail_outbox WHERE status='sent' AND sent_at>=? AND sent_at<? AND subject LIKE '%推薦人選%'", [a, b])
    L.append(f'・推薦給客戶 {len(fw)} 位' + (f'：{"、".join(x["name"] + "→" + (x["co"] or "?")[:6] for x in fw[:5])}' if fw else '')
             + (f'；另外用 Email 寄推薦信 {len(rm)} 封' if rm else ''))
    L.append(f'・人選電洽紀錄 {n("SELECT COUNT(*) n FROM candidate_notes WHERE created_at>=? AND created_at<? AND content LIKE ?", (a, b, "%電洽%"))} 筆、客戶開發電話 {n("SELECT COUNT(*) n FROM bd_call_logs WHERE created_at>=? AND created_at<? AND deleted_at IS NULL", (a, b))} 通')
    L.append(f'・寄出信件：人選 {n("SELECT COUNT(*) n FROM mail_outbox WHERE status=? AND sent_at>=? AND sent_at<? AND ref_table=?", ("sent", a, b, "applications"))} 封、客戶 {n("SELECT COUNT(*) n FROM mail_outbox WHERE status=? AND sent_at>=? AND sent_at<? AND ref_table=?", ("sent", a, b, "bd_company"))} 封')
    L.append(f'・人事資料表交回 {n("SELECT COUNT(*) n FROM candidate_form_submissions WHERE submitted_at>=? AND submitted_at<?", (a, b))} 份')
    return L


def goals():
    w0, w1 = f'{mon} 00:00', f'{today + dt.timedelta(days=1)} 00:00'
    n = lambda q, p=(): (c.rows(q, list(p)) or [{'n': 0}])[0]['n']
    L = [f'本週目標（{fmt(mon)} 起）還缺什麼：']
    board = c.rows('SELECT name, contract FROM client_board WHERE COALESCE(archived,0)=0 ORDER BY sort')
    signed = [x['name'] for x in board if str(x.get('contract') or '').startswith('已簽')]
    L.append(f'1. 主力 {len(board)} 家簽 4 家：已簽 {len(signed)} 家（{"、".join(signed) or "無"}），還差 {max(0, 4 - len(signed))} 家')
    days = max(1, min(5, (today - mon).days))
    got = n("SELECT COUNT(*) n FROM applications WHERE created_at>=? AND created_at<?", (w0, f'{today} 00:00'))
    L.append(f'2. 每天 100 位可推薦人選：本週到昨天系統新增人選卡 {got} 位，目標 {100 * days} 位（104 列印、LinkedIn 名單沒建卡的不在這個數字裡）')
    anon = n("SELECT COUNT(*) n FROM mail_outbox WHERE status='sent' AND sent_at>=? AND attachments_json LIKE ?", (w0, '%匿名人選推薦_%'))
    L.append(f'3. 開發中客戶匿名履歷：本週寄出 {anon} 封')
    cm = n("""SELECT COUNT(*) n FROM candidate_forwards f JOIN client_companies cc ON cc.id=f.company_id
              WHERE f.forwarded_at>=? AND (cc.display_name LIKE '%全曜%' OR cc.display_name LIKE '%CMoney%')""", (w0,))
    for r in c.rows("SELECT subject FROM mail_outbox WHERE status='sent' AND sent_at>=? AND to_email LIKE '%@cmoney.com.tw' AND subject LIKE '%推薦人選%'", [w0]):
        cm += len([x for x in str(r['subject']).split('推薦人選')[-1].lstrip('：:').split('、') if x.strip()])
    L.append(f'4. CMoney 推薦人選：本週推了 {cm} 位（系統推薦＋Email 推薦信）')
    return L


def main():
    items = today_items()
    parts = [f'早安 Jacky ☀️ 今天 {fmt(today)}']
    real = [x for x in items if not x[1].startswith('⚠️')]
    if real:
        parts.append('今天有安排：\n' + '\n'.join(f'・{t}　{txt}' for t, txt in items))
    else:
        if items:
            parts.append(items[0][1])
        parts.append('今天沒有排電話或會議。')
    if not real or '--recap' in sys.argv:
        parts.append('\n'.join(recap()))
        parts.append('\n'.join(goals()))
    text = '\n\n'.join(parts)
    if '--dry-run' in sys.argv:
        print(text); return
    r = c.tg('sendMessage', {'chat_id': JACKY_TG, 'text': text[:4000], 'disable_web_page_preview': True})
    print('已送出' if r.get('ok') else f'送出失敗：{r.get("description")}')


if __name__ == '__main__':
    main()
