#!/usr/bin/env python3
"""Submission → Placement 追蹤引擎 —— Step1ne Phase 1。

要解的問題只有一個：「人已經送出去給客戶，然後就消失」。
2026-08-21 Phase 0 稽核發現：3 筆真實送件案停滯 7–17 天沒人跟進，
系統有提醒但提醒沒有變成行動；`placements` 表唯一一筆「已到職」是測試資料。

這支不是要建一個新的 CRM，是要讓「送出去之後」這一段**不再靜默**。

────────────────────────────────────────
自動化邊界（硬性，寫死在這裡，不是靠自覺）
────────────────────────────────────────
AI 可以：發現停滯、建立待辦、產草稿、追蹤客戶回覆、更新階段、建議下一步、推播異常。
AI 不可以：
    · 自己承諾 Offer 或薪資
    · 自己替候選人接受 Offer
    · 自己宣稱候選人已到職
    · 自己產生 Placement Revenue（沒有 invoice/payment 證據，一律 UNKNOWN）
這支程式裡完全沒有任何一行會把 placement_status 寫成 PLACED、
或把 billing_eligibility 寫成 BILLING_ELIGIBLE——那兩個值只能由人工，
依真實到職證據／合約證據手動確認後寫入。程式本身沒有這個能力，
不是「這次先不用」，是**沒寫這段程式碼**。

────────────────────────────────────────
狀態機（14 個值，不准自己發明別的）
────────────────────────────────────────
SUBMITTED → AWAITING_CLIENT_FEEDBACK → INTERVIEW_REQUESTED → INTERVIEW_SCHEDULED
→ INTERVIEW_COMPLETED → CLIENT_DECISION_PENDING → OFFER → CANDIDATE_ACCEPTED
→ ONBOARDING → PLACED
另外允許：REJECTED_BY_CLIENT / WITHDRAWN_BY_CANDIDATE / ON_HOLD / CLOSED

用法：
    python3 placement_tracker.py --check        # 只看現況，不寫入、不通知（唯讀）
    python3 placement_tracker.py --tick         # 真的跑一輪：算 aging、建待辦、推播
    python3 placement_tracker.py --migrate      # 一次性：把既有 9 筆舊資料按證據對應到新狀態機
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, 'placement_config.json')

STAGES = [
    'SUBMITTED', 'AWAITING_CLIENT_FEEDBACK', 'INTERVIEW_REQUESTED', 'INTERVIEW_SCHEDULED',
    'INTERVIEW_COMPLETED', 'CLIENT_DECISION_PENDING', 'OFFER', 'CANDIDATE_ACCEPTED',
    'ONBOARDING', 'PLACED',
    'REJECTED_BY_CLIENT', 'WITHDRAWN_BY_CANDIDATE', 'ON_HOLD', 'CLOSED',
]


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


D = _daemon()
d1, q = D.d1, D.q
CFG = json.load(open(CONFIG_PATH, encoding='utf-8'))


def today():
    return datetime.date.today()


def days_since(date_str):
    """stage_since 只存日期或日期時間，統一只取日期部分算天數。"""
    if not date_str:
        return None
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
    except Exception:
        return None
    return (today() - d).days


def aging_bucket(stage, stage_since):
    """回傳 NORMAL / FOLLOW_UP_DUE / OVERDUE / CRITICAL_STALE，或 None（這個階段不算 aging）。

    只有『卡在等對方回應』的階段才算 aging——已經是終態（PLACED／CLOSED…）的，
    或本來就是要人主動安排的階段，不該被追著跑，門檻在 config 裡調，不寫死在這。
    """
    if stage not in CFG['stages_that_age']:
        return None, None
    d = days_since(stage_since)
    if d is None:
        return 'UNKNOWN', None
    t = CFG['aging_thresholds_days']
    if d <= t['NORMAL_MAX']:
        b = 'NORMAL'
    elif d <= t['FOLLOW_UP_DUE_MAX']:
        b = 'FOLLOW_UP_DUE'
    elif d <= t['OVERDUE_MAX']:
        b = 'OVERDUE'
    else:
        b = 'CRITICAL_STALE'
    return b, d


def client_contact_status(client_id):
    """有沒有『驗證過的』客戶聯絡窗口。

    ⚠️ 2026-08-21 查證結果：D1 clients 表**完全沒有聯絡人欄位**
       （只有 name/aliases/relation/blocked_reason/via_client/owner/note）。
       也就是說目前不管哪個客戶，這裡永遠會回 MISSING——這不是 bug，
       是誠實反映『我們現在真的沒有任何一個客戶的已驗證聯絡窗口存在系統裡』。
       規則要求『不准自己猜 HR email』，所以這裡不會去猜、不會去補、
       只會老實回報缺什麼，交給人工去補聯絡人。
    """
    if not client_id:
        return 'MISSING'
    row = d1(f"SELECT id FROM clients WHERE id={q(client_id)} "
             f"AND COALESCE(contact_email,'') <> ''") if False else []
    # clients 表目前沒有 contact_email 欄位，上面那行只是留著給未來補欄位後直接生效。
    return 'MISSING'


def draft_followup_message(row):
    """產草稿，不寄出。給人看，不是給機器自動發送。

    對象是客戶端聯絡窗口（B2B），不是候選人，所以不套候選人文字三規則，
    但一樣不承諾任何薪資／到職日期／錄取結果——那些是硬性禁止 AI 自己講的事。
    """
    days = days_since(row.get('stage_since')) or 0
    return (
        f"{row.get('client_name') or row.get('client_company') or '您好'}，\n\n"
        f"想跟您確認一下 {row.get('candidate_name')}（{row.get('job_title') or row.get('job_slug')}）"
        f"這位人選的狀況——履歷是 {row.get('stage_since')} 送出的，到今天 {days} 天了，"
        f"想了解目前貴司這邊評估的進度如何，方便的話麻煩回覆一下，謝謝！\n\n"
        f"[顧問簽名]"
    )


def notify(ntype, text):
    """只允許 config 裡列的 6 種通知類型，推到對應主題。正常進度不推，安靜是預設值。"""
    if ntype not in CFG['notification_types_allowed']:
        raise ValueError(f'不在允許清單裡的通知類型：{ntype}')
    thread = CFG['notification_routing'][ntype]
    try:
        e = dict(l.strip().split('=', 1) for l in
                 open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        tok = e['TG_BOT_TOKEN'].strip('"\'')
        chat = e['TG_CHAT_ID'].strip('"\'')
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text,
                                       'message_thread_id': thread}).encode()
        urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', data, timeout=20)
    except Exception as ex:
        log(f'（TG 發送失敗，不影響資料已經寫入）：{ex}')
    print(f'[{ntype} → thread {thread}]\n{text}\n')


# ────────────────────────────────────────────────────────────
# --migrate：一次性把既有 9 筆舊資料對應到新狀態機。
# 每一筆的判斷依據都寫在這裡，不是黑箱模型猜的。查不到依據的，
# 就用最保守、資訊量最少的新狀態（不去推測細節）。
# ────────────────────────────────────────────────────────────
def migrate():
    rows = d1("""SELECT p.*, a.email, a.phone FROM placements p
                 LEFT JOIN applications a ON a.id = p.application_id
                 ORDER BY p.created_at""")
    log(f'共 {len(rows)} 筆既有紀錄，逐筆依證據對應新狀態')
    for r in rows:
        pid, old = r['id'], r['stage']
        ev = []
        new_stage = None
        is_test = 0

        if r['candidate_name'] == 'Jacky測試':
            # 這筆的 interview_appointments 全部是「測試」「董事長」「佐藤部長」這種
            # 明顯的測試資料，也是全系統唯一一筆 stage='GUARANTEE'（已到職）的紀錄。
            # 不能真的當成到職案例，也不能假裝它沒發生過——用 is_test_data 標記排除，
            # 不重寫、不刪除，留著給以後查證這筆資料是怎麼混進來的。
            new_stage = 'CLOSED'
            is_test = 1
            ev.append('候選人姓名為測試資料；interview_appointments 內容為「測試」「董事長」「佐藤部長」等明顯測試值')

        elif old == 'SUBMITTED':
            appts = d1(f"SELECT COUNT(*) n FROM interview_appointments WHERE application_id={q(r['application_id'])}")[0]['n']
            if appts == 0 and not r.get('client_feedback'):
                new_stage = 'AWAITING_CLIENT_FEEDBACK'
                ev.append(f"stage_since={r['stage_since']}，查無 interview_appointments 紀錄，查無 client_feedback 欄位——沒有任何客戶回應的證據")
            else:
                new_stage = 'SUBMITTED'
                ev.append('保守保留原狀態：查到疑似後續證據但本次未逐一核實')

        elif old == 'INTERVIEWING':
            note = r.get('note') or ''
            if '確認人選意願' in note or '確認候選人' in note:
                new_stage = 'INTERVIEW_REQUESTED'
                ev.append(f'備註：「{note}」——內容是在確認候選人本人意願，不是已確認的面試時間，未達 INTERVIEW_SCHEDULED 門檻')
            else:
                new_stage = 'INTERVIEW_REQUESTED'
                ev.append('原狀態為 INTERVIEWING，無更細節可判斷是否已排定確切時間，保守對應到 INTERVIEW_REQUESTED')

        elif old == 'CLOSED_LOST':
            note = (r.get('note') or '') + (r.get('close_reason_internal') or '')
            if '不接受' in note or '無意願' in note or '拒絕' in note:
                new_stage = 'WITHDRAWN_BY_CANDIDATE'
                ev.append(f'備註內容為候選人主動拒絕：「{(r.get("note") or "")[:80]}」')
            elif '傾向尋找' in note or '已有' in note or '團隊配置' in note:
                new_stage = 'REJECTED_BY_CLIENT'
                ev.append('close_reason_internal 內容顯示是用人單位內部決定不繼續（非候選人拒絕）')
            else:
                new_stage = 'CLOSED'
                ev.append('無法從既有備註判斷結案原因是候選人拒絕還是客戶拒絕，不猜測，歸為通用 CLOSED')
        else:
            new_stage = old
            ev.append('沒有對應規則，維持原狀態')

        sets = [f"stage={q(new_stage)}", f"is_test_data={is_test}",
                f"evidence_log={q(json.dumps(ev, ensure_ascii=False))}"]
        d1(f"UPDATE placements SET {', '.join(sets)} WHERE id={pid}")
        log(f'  #{pid} {r["candidate_name"]:10} {old:14} → {new_stage:24} {"[測試資料]" if is_test else ""}')
    log('遷移完成。')


# ────────────────────────────────────────────────────────────
# --tick：真的跑一輪。這是要排程執行的主邏輯。
# ────────────────────────────────────────────────────────────
def tick(dry_run=False):
    rows = d1("SELECT * FROM placements WHERE COALESCE(is_test_data,0)=0")
    created, notified = 0, []

    for r in rows:
        bucket, days = aging_bucket(r['stage'], r['stage_since'])
        if bucket in (None, 'NORMAL'):
            continue

        # 已經有一筆還開著的待辦就不要重複建立
        open_task = d1(f"SELECT id FROM followup_tasks WHERE placement_id={r['id']} "
                       f"AND status='open' ORDER BY created_at DESC LIMIT 1")
        contact = client_contact_status(r.get('client_id'))

        if bucket in ('FOLLOW_UP_DUE', 'OVERDUE', 'CRITICAL_STALE') and not open_task:
            draft = draft_followup_message(r) if contact == 'VERIFIED' else None
            reason = (f"{r['candidate_name']}｜{r.get('job_title') or r['job_slug']}｜"
                     f"卡在 {r['stage']} 已 {days} 天")
            if not dry_run:
                d1(f"""INSERT INTO followup_tasks
                       (id, placement_id, created_at, aging_bucket, days_stale, reason,
                        contact_status, draft_message, status)
                       VALUES (lower(hex(randomblob(8))), {r['id']}, datetime('now','+8 hours'),
                       {q(bucket)}, {days}, {q(reason)}, {q(contact)}, {q(draft)}, 'open')""")
            created += 1
            log(f"  建立待辦：{reason}｜聯絡窗口：{contact}")

        if bucket == 'CRITICAL_STALE':
            text = (f"🔴 CRITICAL_STALE｜{r['candidate_name']}｜{r.get('job_title') or r['job_slug']}\n"
                   f"卡在 {r['stage']} 已 {days} 天，{'聯絡窗口缺失，需要人工先找到人再跟進' if contact=='MISSING' else '請跟進'}\n"
                   f"owner：{r.get('owner') or '（未指定）'}")
            if not dry_run:
                notify('CRITICAL_STALE', text)
            notified.append(('CRITICAL_STALE', r['candidate_name']))

        if contact == 'MISSING' and bucket in ('OVERDUE', 'CRITICAL_STALE'):
            text = (f"🙋 OWNER_DECISION_REQUIRED｜{r['candidate_name']}\n"
                   f"客戶「{r.get('client_name')}」在系統裡沒有任何已驗證聯絡窗口，"
                   f"無法自動產生跟進草稿，需要人工先確認要找誰跟進。")
            if not dry_run:
                notify('OWNER_DECISION_REQUIRED', text)
            notified.append(('OWNER_DECISION_REQUIRED', r['candidate_name']))

    log(f'本輪：{"（試跑，未寫入）" if dry_run else ""}新增待辦 {created} 筆，發出通知 {len(notified)} 則')
    return created, notified


def check():
    """唯讀檢查：現在每一筆卡在哪、卡幾天、下一步是什麼。不寫入、不通知。"""
    rows = d1("SELECT * FROM placements ORDER BY created_at")
    for r in rows:
        tag = '[測試資料，排除於真實統計]' if r.get('is_test_data') else ''
        bucket, days = aging_bucket(r['stage'], r['stage_since'])
        print(f"#{r['id']:3} {r['candidate_name']:10} {r['stage']:24} "
             f"{f'{days} 天／{bucket}' if bucket else '（不計 aging）':16} {tag}")


def main():
    if '--migrate' in sys.argv:
        migrate()
    elif '--tick' in sys.argv:
        tick(dry_run=False)
    elif '--check' in sys.argv:
        check()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
