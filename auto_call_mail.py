#!/usr/bin/env python3
"""面談結束後自動擬「約電話信」→ 負責顧問 TG 私訊確認卡（按了才寄）。

2026-11-01 建立（Jacky 10/31 拍板方案 A）。以前都是面談完有人想起來才手動跑
consultant_ops.py call-mail，人選容易冷掉。

🚨 這支**絕對不寄信**。只做 consultant_ops.py call-mail 一模一樣的事：
   跟後台要草稿 → 存一筆 consultant_ops_actions(kind='call_mail', status=pending) → 貼確認卡。
   真正寄出只發生在 step1ne-recruit Worker 的 cop_ok（Jacky／Phoebe 按了才寄，寄件 official@）。

誰會出卡（全部成立才出）：
  * 面談真的結束：interview_state done／paused 且 interview_ended_at 有值，
    而且是上線之後（SINCE）結束的、最近 WINDOW_DAYS 天內——不回頭補舊人選
  * 阿財報告已經出來（最多等 REPORT_WAIT_MIN 分鐘，報告一直沒出來也照出卡）
跳過不出卡：
  * 最新一份報告分流是 D 人才池／E Hard Gate 沒過（不適合）
  * 已有電洽紀錄／電洽預約
  * 已有約電話信的確認卡（任何狀態，含這支自己出過的＝每人只出一次）、已寄出的確認電話信或已登記電話
  * call_booking_mails 已有寄出／寄送中的信
  * origin='104'、origin='測試'、佔位信箱 @no-email.step1ne.local、沒有 Email
  * 已推薦給客戶（candidate_forwards 有紀錄）
  * 重複／已被取代的應徵、已婉拒（status rejected/duplicate/closed）

送卡：一律送 Jacky 私訊；負責顧問是 Phoebe 的再送一份到 Phoebe 私訊（2026-11-02 Jacky）。
同一張卡兩邊按只會寄一次（Worker 先搶 consultant_ops_actions 狀態）。Jacky、Phoebe 都能按。
信件不寫顧問名字，以「全民獵才招募團隊」口吻（模板在後台 callMailDraft；by 只記誰負責）。

開關：D1 site_settings 'auto_call_mail'＝on／off（沒設＝off）。
跑在哪：interview_daemon.py 主迴圈每 AUTO_EVERY_SEC 秒叫一次 tick()，**只在主力機（WSL2，沒設
INTERVIEW_BACKUP_DELAY_SEC）跑**；就算兩台都跑，create_action 先占位再貼卡，也只會出一張。

手動測試：
  python3 auto_call_mail.py --dry-run              # 列出現在會出卡／會跳過的人，不寫任何東西
  python3 auto_call_mail.py --dry-run --app <id>   # 指定一位（不看時間窗），看判斷＋信件草稿
  python3 auto_call_mail.py --app <id> --test      # 真的出一張卡給 Jacky（標明測試，馬上自動取消）
"""
import argparse, datetime as dt, json, sys

import consultant_ops as c

SINCE = '2026-11-01 00:00:00'     # 上線日：這之前結束的面談不補出卡
WINDOW_DAYS = 3
REPORT_WAIT_MIN = 30
UNFIT_ROUTES = ('D', 'E')         # D 人才池、E Hard Gate 沒過
SKIP_ORIGINS = ('104', '測試')
DONE_KINDS = ('call_confirm', 'book_call')   # 這兩種「已完成」＝已約好電話

# 一次查完所有條件（D1 讀取量：只掃時間窗內結束的面談）
_SQL = f"""
SELECT a.id, a.name, a.email, a.owner, a.origin, a.job_slug, COALESCE(j.title, a.job_title) AS job_title,
       a.interview_state, a.interview_ended_at,
       (SELECT r.route_code FROM reports r WHERE r.application_id=a.id ORDER BY r.created_at DESC LIMIT 1) AS route_code,
       (SELECT r.route_label FROM reports r WHERE r.application_id=a.id ORDER BY r.created_at DESC LIMIT 1) AS route_label,
       (SELECT COUNT(*) FROM reports r WHERE r.application_id=a.id) AS n_reports,
       EXISTS(SELECT 1 FROM candidate_notes n WHERE n.application_id=a.id AND n.type IN ('電洽紀錄','電洽預約')) AS has_call_note,
       EXISTS(SELECT 1 FROM consultant_ops_actions o WHERE o.application_id=a.id AND o.kind='call_mail') AS has_call_mail_card,
       EXISTS(SELECT 1 FROM consultant_ops_actions o WHERE o.application_id=a.id
                AND o.kind IN ({','.join("'" + k + "'" for k in DONE_KINDS)}) AND o.status='done') AS has_booked,
       EXISTS(SELECT 1 FROM call_booking_mails m WHERE m.application_id=a.id AND m.status IN ('sent','sending')) AS has_call_mail_sent,
       EXISTS(SELECT 1 FROM candidate_forwards f WHERE f.application_id=a.id) AS forwarded
  FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug
 WHERE {{where}}
 ORDER BY a.interview_ended_at
"""


def _now():
    return c.now_tpe()


def candidates(app_id=None):
    if app_id:
        return c.rows(_SQL.format(where='a.id=?'), [app_id])
    since = max(SINCE, c.fmt(_now() - dt.timedelta(days=WINDOW_DAYS)) + ':00')
    return c.rows(_SQL.format(where="a.interview_ended_at IS NOT NULL AND a.interview_ended_at >= ? "
                                    "AND a.interview_state IN ('done','paused')"), [since])


def skip_reason(r, ignore_window=False, allow_test=False):
    """回傳跳過原因（字串）；可以出卡回 None。"""
    if r.get('interview_state') not in ('done', 'paused') or not r.get('interview_ended_at'):
        return '面談還沒結束'
    if (r.get('origin') or '') in SKIP_ORIGINS and not (allow_test and r.get('origin') == '測試'):
        return f'來源是 {r["origin"]}'
    email = (r.get('email') or '').strip().lower()
    if not email or email.endswith('@no-email.step1ne.local'):
        return '沒有可用的 Email（佔位信箱）'
    if r.get('route_code') in UNFIT_ROUTES:
        return f'阿財分流：{r["route_code"]} {r.get("route_label") or ""}'.strip()
    if r.get('has_call_note'):
        return '已有電洽紀錄／電洽預約'
    if r.get('has_call_mail_card'):
        return '已經有約電話信的確認卡（每人只出一次）'
    if r.get('has_booked'):
        return '已經約好電話'
    if r.get('has_call_mail_sent'):
        return '已經寄過約電話信'
    if r.get('forwarded'):
        return '已推薦給客戶'
    if not ignore_window and not r.get('n_reports'):
        try:
            ended = dt.datetime.strptime(str(r['interview_ended_at'])[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            ended = _now()
        if _now() - ended < dt.timedelta(minutes=REPORT_WAIT_MIN):
            return '等阿財報告出來'
    return None


def _status_skip(app_id):
    """applications 的狀態（重複／已被取代／已婉拒）——放這裡不放大查詢，欄位少讀一點。"""
    r = c.rows("SELECT status, superseded_by FROM applications WHERE id=?", [app_id])
    if not r:
        return '找不到人選'
    if r[0].get('superseded_by'):
        return '這筆應徵已被取代'
    if (r[0].get('status') or '') in ('duplicate', 'rejected', 'closed'):
        return f'應徵狀態是 {r[0]["status"]}'
    return None


def owner_target(r):
    o = (r.get('owner') or '').strip().lower()
    t = c.OWNER_TG.get(o) or c.OWNER_TG['jacky']
    return t


def make_card(r, dry_run=False, test=False):
    """出卡（或 dry-run 只產草稿）。回傳 dict 說明結果。"""
    t = owner_target(r)
    app = {'id': r['id'], 'name': r['name'], 'job_slug': r.get('job_slug'), 'job_title': r.get('job_title')}
    send_params, d, extra = c.call_mail_draft(app, t['name'])
    send_params['_auto'] = 'interview_done'   # 底線開頭的參數 Worker 寄信時會拿掉，只是記號
    if dry_run:
        to_tg = 'Jacky' + ('' if t['name'] == 'Jacky' else '＋' + t['name'])
        return {'ok': True, 'dry_run': True, 'to_tg': to_tg, 'by': t['name'], 'mail_to': d['to'],
                'subject': d['subject'], 'text': d['text']}
    jk = c.OWNER_TG['jacky']
    target = OWNER_TG_TEST if test else jk          # 主卡一律在 Jacky 私訊
    also = () if test or t['tg_id'] == jk['tg_id'] else (t['tg_id'],)   # Phoebe 負責的再送她一份
    w = {'tg_id': t['tg_id'], 'username': t['username'], 'name': f'系統（面談後自動・{t["name"]}）',
         'chat': target['tg_id'], 'thread': ''}
    route = f'{r.get("route_code") or "—"} {r.get("route_label") or ""}'.strip()
    head = (f'🤖 阿財面談結束（{str(r["interview_ended_at"])[5:16]}），系統先幫你擬好約電話信。\n'
            f'負責顧問：{t["name"]}｜阿財分流：{route}\n')
    tail = ('─' * 14 + '\n' + ('\n'.join(extra) + '\n' if extra else '') +
            ('這張卡 Jacky、Phoebe 私訊各一份，誰先按都只會寄一次。' if also else '') +
            f'信件以全民獵才招募團隊名義、從 official@ 寄。按「✅ 確認寄出」才會真的寄（Jacky、Phoebe 都可以按），'
            f'{c.EXPIRE_H} 小時內有效；不想約就按「✖ 不寄了」，這位不會再自動出卡。')
    card = head + c.card_head('確認寄出｜約電話信', app, d['to'], d['subject']) + d['text'] + '\n' + tail
    if test:
        card = '【測試・請勿按確認，等一下會自動取消】\n' + card
    aid = c.create_action('call_mail', app, send_params, d['to'], d['subject'], d['text'], card,
                          [('✅ 確認寄出', 'cop_ok'), ('✖ 不寄了', 'cop_no')], w=w, only_if_none=('call_mail',),
                          also_chats=also)
    if not aid:
        return {'ok': False, 'skip': '已經有約電話信的確認卡（別台剛出過）'}
    return {'ok': True, 'action_id': aid, 'to_tg': '＋'.join([target['name']] + [t['name'] for _ in also]), 'by': t['name'], 'mail_to': d['to'], 'subject': d['subject']}


OWNER_TG_TEST = c.OWNER_TG['jacky']


def enabled():
    try:
        r = c.rows("SELECT value FROM site_settings WHERE key='auto_call_mail'")
        return bool(r) and str(r[0].get('value') or '').strip().lower() == 'on'
    except BaseException:
        return False


def tick(log=print, dry_run=False):
    """daemon 每幾分鐘叫一次。任何錯誤都吞掉只記 log（c.die 是 sys.exit，要連 SystemExit 一起接）。"""
    made = []
    try:
        if not dry_run and not enabled():
            return made
        for r in candidates():
            why = skip_reason(r) or _status_skip(r['id'])
            if why:
                if dry_run:
                    log(f'  跳過 {r["name"]}（{r["id"][:8]}）：{why}')
                continue
            try:
                res = make_card(r, dry_run=dry_run)
            except BaseException as e:   # 草稿被後台擋（例如冷卻期）→ 這輪不出，記 log
                log(f'約電話信自動擬稿：{r["name"]} 沒出卡（{type(e).__name__} {str(e)[:120]}）')
                continue
            log(f'約電話信自動擬稿：{r["name"]} → {res}' if dry_run else
                f'約電話信自動擬稿：{r["name"]} → 卡片送 {res.get("to_tg")}（{res.get("action_id") or res.get("skip")}）')
            made.append(res)
    except BaseException as e:
        log(f'約電話信自動擬稿失敗（不影響面談）：{type(e).__name__} {str(e)[:200]}')
    return made


def _cancel_test_card(aid):
    r = c.rows("SELECT chat_id, tg_message_id FROM consultant_ops_actions WHERE id=?", [aid])
    c.d1("UPDATE consultant_ops_actions SET status='cancelled', decided_by='工程測試自動取消', decided_at=? WHERE id=? AND status='pending'",
         [c.fmt(c.now_tpe()), aid])
    if r and r[0].get('tg_message_id'):
        c.tg('editMessageReplyMarkup', {'chat_id': r[0]['chat_id'], 'message_id': int(r[0]['tg_message_id']),
                                        'reply_markup': {'inline_keyboard': [[{'text': '🧪 測試卡，已自動取消，沒有寄', 'callback_data': 'noop'}]]}})


def main():
    ap = argparse.ArgumentParser(description='面談後自動擬約電話信（確認卡）')
    ap.add_argument('--dry-run', action='store_true', help='只列出判斷＋草稿，不寫入、不貼卡')
    ap.add_argument('--app', help='只看這一位（不看時間窗）')
    ap.add_argument('--test', action='store_true', help='搭配 --app：真的貼一張測試卡給 Jacky，隨即自動取消')
    ap.add_argument('--since', help='搭配 --dry-run：假裝上線時間是這個（看過去幾天會怎麼判斷）')
    a = ap.parse_args()
    if not a.app:
        if not a.dry_run:
            c.die('要真的跑請交給 daemon；手動請加 --dry-run')
        global SINCE
        SINCE = a.since or SINCE
        print(f'時間窗：{SINCE} 之後、最近 {WINDOW_DAYS} 天結束的面談')
        tick(dry_run=True)
        return
    rs = candidates(a.app)
    if not rs:
        c.die('找不到這位人選')
    r = rs[0]
    why = skip_reason(r, ignore_window=True, allow_test=a.test) or _status_skip(r['id'])
    print(f'人選：{r["name"]}｜負責：{r.get("owner") or "（無）"}｜來源：{r.get("origin")}｜分流：{r.get("route_code")}')
    if why:
        print(f'➡️ 不出卡：{why}')
        return
    if a.test:
        res = make_card(r, test=True)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        if res.get('action_id'):
            _cancel_test_card(res['action_id'])
            print('（測試卡已自動取消）')
        return
    res = make_card(r, dry_run=True)
    print(f'➡️ 會出卡：送 {res["to_tg"]} 私訊，署名 {res["by"]}，寄到 {res["mail_to"]}\n主旨：{res["subject"]}\n--- 信件 ---\n{res["text"]}')


if __name__ == '__main__':
    main()
