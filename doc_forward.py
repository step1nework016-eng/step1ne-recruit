#!/usr/bin/env python3
"""客戶指定表單：發給人選 → 人選回傳 → 自動轉給客戶人資（2026-10-07 Jacky：「做通用的，每個職缺都可以，先用美德試」）。

流程
1. send-forms：把客戶卡片「客戶指定表單／附件」（client_files）用 official@ 寄給人選，同時登記一筆
   candidate_doc_requests（等這位人選回傳、要轉給客戶的誰、接在哪一串信）。⚠️ 寄給人選前要 Jacky 點頭。
2. tick（launchd 每 5 分鐘）：查 official@ 與 jackychen@ 收件匣，登記中的人選來信——
   ・有 Word／PDF／圖片附件 → 用 jackychen@ 轉給客戶人資（接在原本那串信、副本 official@）→ TG 私訊 Jacky
   ・沒附件 → 只私訊 Jacky，不轉
   ・過了期限還沒回 → 私訊 Jacky，標成 expired
   Jacky 已授權「人選回傳的表單自動轉給客戶」這一件事；其他給客戶的信仍要他說寄才寄。
3. add：已經用別的方式把表單給人選（例如手動寄），只登記等回傳。

只能在 Mac 跑：jackychen@ 的密碼只存在這台（~/.config/workflow-os/jacky-mailbox.env）。

用法：
  python3 doc_forward.py tick
  python3 doc_forward.py add --app <application_id> --thread '<客戶信 Message-ID>' --subject 'Re: …' [--to 'Name <a@b>'] [--days 7]
  python3 doc_forward.py send-forms --app <application_id> --thread '<Message-ID>' --subject 'Re: …' [--note '面試 10/12 10:00'] [--days 7]
  python3 doc_forward.py list
"""
import argparse
import datetime
import email
import email.message
import email.utils
import imaplib
import json
import mimetypes
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
import uuid
from email.header import decode_header, make_header

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402
from weekday_check import weekday_errors  # noqa: E402


# ── 2026-10-08 加：轉給客戶前先檢查人事資料表有沒有填完整 ──
# 起因：丁以岡回傳的 GAHR04 工作經歷只寫到 2021（漏了現職），16:33 被原封不動轉給美德，
# 信上還寫「填寫完成」。Jacky：不完整就先停下來發 TG 問，不要直接轉出。
# 只檢查「看得出是人事資料表」的檔案（有「工作經歷」字樣）；圖片讀不到字，一律先停下來給人看。
def att_text(fn, data):
    import io
    low = fn.lower()
    try:
        if low.endswith('.docx'):
            import docx
            d = docx.Document(io.BytesIO(data))
            lines = [p.text for p in d.paragraphs]
            for t in d.tables:
                for row in t.rows:
                    cells = []
                    for c in row.cells:
                        x = c.text.strip()
                        if x and x not in cells:
                            cells.append(x)
                    lines.append(' | '.join(cells))
            # 2026-10-09 修：照片常用「浮動圖片」(wp:anchor) 放，inline_shapes 數不到（杜偉銘的照片被誤判成沒有）。
            # 改成數文件裡實際引用的圖片檔。
            n_img = sum(1 for r in d.part.rels.values() if 'image' in r.reltype)
            return '\n'.join(lines), n_img
        if low.endswith('.pdf'):
            import pymupdf
            d = pymupdf.open(stream=data, filetype='pdf')
            return ''.join(pg.get_text() for pg in d), sum(len(pg.get_images()) for pg in d)
    except Exception as e:
        return None, f'讀不到內容（{e.__class__.__name__}）'
    return None, '圖片或其他格式，程式讀不到字'


def form_problems(atts):
    """回傳問題清單；空的＝看起來完整（或不是人事資料表，不檢查）。"""
    probs = []
    this_year = datetime.date.today().year
    for fn, _ct, data in atts:
        text, imgs = att_text(fn, data)
        if text is None:
            probs.append(f'{fn}：{imgs}，請人工確認')
            continue
        if '工作經歷' not in text:
            continue
        work = text.split('工作經歷', 1)[1].split('家庭狀況', 1)[0]
        years = [int(y) for y in re.findall(r'(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)', work)]
        current = re.search(r'至今|迄今|在職中|現職|[Pp]resent', work)
        if years and not current and max(years) < this_year - 1:
            probs.append(f'{fn}：工作經歷只寫到 {max(years)} 年，之後（含目前工作）沒寫')
        m = re.search(r'希望待遇(.*?)(教育程度|$)', text, re.S)
        if m:
            rest = re.sub(r'依公司規定|月薪|[⬜□☐_＿|\s:：$＄]', '', m.group(1))
            if not re.search(r'\d|✓|☑|■|面議|依', rest):
                probs.append(f'{fn}：希望待遇沒填')
        if imgs == 0:
            probs.append(f'{fn}：沒有大頭照')
    return probs

IMAP_HOST, SMTP_HOST = 'imap.secureserver.net', 'smtpout.secureserver.net'
JACKY_TG = '8365775688'
ATT_OK = re.compile(r'\.(docx?|pdf|jpe?g|png|heic)$', re.I)


def q(v):
    return "'" + str(v).replace("'", "''") + "'" if v is not None else 'NULL'


def envf(p):
    out = {}
    for line in open(os.path.expanduser(p), encoding='utf-8'):
        if '=' in line and not line.startswith('#'):
            k, v = line.rstrip('\n').split('=', 1)
            out[k] = v.strip("'\"")
    return out


def official():
    e = envf('~/.config/workflow-os/step1ne-mailbox.env')
    return e['MAIL_IMAP_USER'], e['MAIL_IMAP_PASS']


def jacky():
    e = envf('~/.config/workflow-os/jacky-mailbox.env')
    return e['MAIL_USER'], e['MAIL_PASS']


def tg(text):
    e = envf('~/.config/workflow-os/step1ne-tg.env')
    urllib.request.urlopen(f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                           urllib.parse.urlencode({'chat_id': JACKY_TG, 'text': text}).encode(), timeout=30)


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def ensure():
    d1_http.query("""CREATE TABLE IF NOT EXISTS candidate_doc_requests (
      id TEXT PRIMARY KEY, application_id TEXT, company_id TEXT, candidate_name TEXT, candidate_email TEXT,
      client_to TEXT, client_cc TEXT, thread_message_id TEXT, thread_subject TEXT, doc_titles TEXT,
      status TEXT, created_at TEXT, created_by TEXT, expires_at TEXT, forwarded_at TEXT, note TEXT)""")
    d1_http.query("CREATE TABLE IF NOT EXISTS candidate_doc_seen (message_id TEXT PRIMARY KEY, request_id TEXT, seen_at TEXT)")


def app_info(app_id):
    r = d1_http.query(
        f"SELECT a.id, a.name, a.email, j.company_id, c.display_name, c.contact_name, c.contact_email "
        f"FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug LEFT JOIN client_companies c ON c.id=j.company_id "
        f"WHERE a.id={q(app_id)}")['results']
    if not r:
        sys.exit('找不到這位人選')
    return r[0]


def register(a, thread, subject, to, days, titles, by='Jacky', note=None):
    rid = str(uuid.uuid4())
    exp = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    d1_http.query(
        "INSERT INTO candidate_doc_requests (id, application_id, company_id, candidate_name, candidate_email, client_to, client_cc, "
        "thread_message_id, thread_subject, doc_titles, status, created_at, created_by, expires_at, note) VALUES ("
        f"{q(rid)}, {q(a['id'])}, {q(a['company_id'])}, {q(a['name'])}, {q(a['email'])}, {q(to)}, 'official@step1ne.com', "
        f"{q(thread)}, {q(subject)}, {q(titles)}, 'waiting', {q(now())}, {q(by)}, {q(exp)}, {q(note)})")
    return rid, exp


def cmd_add(args):
    ensure()
    a = app_info(args.app)
    to = args.to or (f"{a['contact_name']} <{a['contact_email']}>" if a.get('contact_email') else None)
    if not to:
        sys.exit('客戶卡片沒有人資信箱，請用 --to 指定')
    titles = '、'.join(r['title'] for r in d1_http.query(
        f"SELECT title FROM client_files WHERE company_id={q(a['company_id'])} ORDER BY created_at")['results'])
    rid, exp = register(a, args.thread, args.subject, to, args.days, titles, note=args.note)
    print(f'已登記：{a["name"]} 回傳後轉給 {to}（{exp} 前）｜{rid}')


def cmd_send_forms(args):
    ensure()
    a = app_info(args.app)
    if not a.get('email') or '@no-email' in a['email']:
        sys.exit('人選沒有 Email')
    files = d1_http.query(f"SELECT title, file_id FROM client_files WHERE company_id={q(a['company_id'])} ORDER BY created_at")['results']
    # 2026-10-08：客戶卡片也會放內部文件（客戶回覆的問題集、JD），原本這裡會全部夾給人選。
    # 一定要用 --only 指定要寄哪幾份（標題關鍵字），沒指定就不寄。
    if not args.only:
        sys.exit('請用 --only 指定要寄的表單（標題關鍵字，例如 --only GAHR04），避免把內部文件寄給人選')
    files = [f for f in files if any(k in (f['title'] or '') for k in args.only)]
    if not files:
        sys.exit('找不到符合 --only 的表單')
    tok = envf('~/.config/workflow-os/recruit.env')['RECRUIT_ADMIN_TOKEN']
    msg = email.message.EmailMessage()
    msg['From'] = 'Jacky Chen <official@step1ne.com>'
    msg['To'] = a['email']
    msg['Subject'] = "人事資料表｜請填寫後回傳"
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='step1ne.com')
    days = args.days
    due = (datetime.date.today() + datetime.timedelta(days=max(1, days - 1))).strftime('%m/%d')
    body = (f"{a['name']} 您好，\n\n{(args.note + chr(10) + chr(10)) if args.note else ''}"
            f"企業請您先填寫附件資料表（{'、'.join(f['title'] for f in files)}），"
            f"填好後直接回覆這封信、夾上檔案即可（拍照或掃描也可以），麻煩於 {due} 前回傳，我們會轉交給企業。\n\n"
            "有任何問題隨時跟我說，謝謝！\n\nJacky\nStep1ne｜德仁管理顧問有限公司\n")
    wd_err = weekday_errors(body + '\n' + str(msg['Subject'] or ''))
    if wd_err:
        sys.exit('星期寫錯，沒有寄出：' + '；'.join(wd_err))
    msg.set_content(body)
    for f in files:
        req = urllib.request.Request(f"https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/file/{f['file_id']}",
                                     headers={'Authorization': 'Bearer ' + tok, 'User-Agent': 'step1ne-ops'})
        resp = urllib.request.urlopen(req, timeout=60)
        cd = resp.headers.get('content-disposition') or ''
        m = re.search(r"filename\*=UTF-8''([^;]+)", cd)
        fn = urllib.parse.unquote(m.group(1)) if m else f['title']
        ctype = (mimetypes.guess_type(fn)[0] or 'application/octet-stream').split('/')
        msg.add_attachment(resp.read(), maintype=ctype[0], subtype=ctype[1], filename=fn)
    u, p = official()
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=60, context=ssl.create_default_context()) as s:
        s.login(u, p)
        s.send_message(msg)
    im = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    im.login(u, p)
    im.append('Sent', '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    im.logout()
    to = args.to or f"{a['contact_name']} <{a['contact_email']}>"
    rid, exp = register(a, args.thread, args.subject, to, days, '、'.join(f['title'] for f in files), note=args.note)
    print(f'已寄表單給 {a["name"]}（{a["email"]}），登記等回傳 → 轉給 {to}（{exp} 前）')


def scan(user, pw, cand, since):
    out = []
    im = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    im.login(user, pw)
    im.select('INBOX', readonly=True)
    typ, ids = im.search(None, f'(FROM "{cand}" SINCE "{since}")')
    for i in ids[0].split():
        m = email.message_from_bytes(im.fetch(i, '(BODY.PEEK[])')[1][0][1])
        atts = []
        for part in m.walk():
            fn = part.get_filename()
            if fn:
                atts.append((str(make_header(decode_header(fn))), part.get_content_type(), part.get_payload(decode=True)))
        out.append((m['Message-ID'] or f'{user}-{i.decode()}', m, atts))
    im.logout()
    return out


def forward(r, atts):
    u, p = jacky()
    msg = email.message.EmailMessage()
    msg['From'] = f'Jacky Chen <{u}>'
    msg['To'] = r['client_to']
    msg['Cc'] = r['client_cc'] or 'official@step1ne.com'
    msg['Subject'] = r['thread_subject'] or f"{r['candidate_name']} 面試資料表"
    if r.get('thread_message_id'):
        msg['In-Reply-To'] = r['thread_message_id']
        msg['References'] = r['thread_message_id']
    msg['Date'] = email.utils.formatdate(localtime=True)
    msg['Message-ID'] = email.utils.make_msgid(domain='step1ne.com')
    msg.set_content(f"您好，\n\n附上{r['candidate_name']}填寫完成的面試資料表，請您查收。\n\n謝謝！\n\n"
                    "Jacky\nStep1ne｜德仁管理顧問有限公司\n分機 25\n")
    for fn, ctype, data in atts:
        mt, st = ctype.split('/', 1) if '/' in ctype else ('application', 'octet-stream')
        msg.add_attachment(data, maintype=mt, subtype=st, filename=fn)
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=60, context=ssl.create_default_context()) as s:
        s.login(u, p)
        s.send_message(msg)
    im = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    im.login(u, p)
    im.append('"Sent"', '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
    im.logout()


def cmd_tick(args):
    ensure()
    rows = d1_http.query("SELECT * FROM candidate_doc_requests WHERE status IN ('waiting','held')")['results']
    if not rows:
        return
    seen = {r['message_id'] for r in d1_http.query("SELECT message_id FROM candidate_doc_seen")['results']}
    today = datetime.date.today().isoformat()
    boxes = [official(), jacky()]
    for r in rows:
        if r.get('expires_at') and today > r['expires_at']:
            d1_http.query(f"UPDATE candidate_doc_requests SET status='expired' WHERE id={q(r['id'])}")
            tg(f"⏹ {r['candidate_name']} 的面試資料表到 {r['expires_at']} 還沒回傳，自動轉寄已停止。要不要提醒他？")
            continue
        since = datetime.datetime.strptime(r['created_at'][:10], '%Y-%m-%d').strftime('%d-%b-%Y')
        msgs = []
        for u, p in boxes:
            msgs += scan(u, p, r['candidate_email'], since)
        if r['id'] == getattr(args, 'force_id', None):
            msgs.reverse()   # 放行時用人選最新的那一封
        for mid, m, atts in msgs:
            if mid in seen:
                continue
            d1_http.query(f"INSERT OR IGNORE INTO candidate_doc_seen (message_id, request_id, seen_at) VALUES ({q(mid)}, {q(r['id'])}, {q(now())})")
            seen.add(mid)
            subj = str(make_header(decode_header(m['Subject'] or '')))
            ok = [a for a in atts if ATT_OK.search(a[0])]
            if not ok:
                tg(f"📩 {r['candidate_name']} 回信了，但沒有附 Word／PDF／圖片檔，沒轉寄。\n主旨：{subj}\n請到信箱看內容。")
                continue
            probs = [] if r['id'] == getattr(args, 'force_id', None) else form_problems(ok)
            if probs:
                d1_http.query(f"UPDATE candidate_doc_requests SET status='held', note={q('；'.join(probs)[:900])} WHERE id={q(r['id'])}")
                tg(f"✋ {r['candidate_name']} 回傳了資料表，但看起來沒填完整，先不轉給 {r['client_to']}：\n・" + '\n・'.join(probs) +
                   "\n\n人選補好再回信會自動重新檢查；要照現在這份直接轉，跟 Claude 說「放行 " + r['candidate_name'] + "」。")
                continue
            forward(r, ok)
            d1_http.query(f"UPDATE candidate_doc_requests SET status='forwarded', forwarded_at={q(now())} WHERE id={q(r['id'])}")
            names = '、'.join(a[0] for a in ok)
            need = len([t for t in (r.get('doc_titles') or '').split('、') if t])
            warn = f"\n⚠️ 客戶要 {need} 份，這次只有 {len(ok)} 個檔案，確認一下有沒有漏。" if need and len(ok) < need else ''
            tg(f"✅ {r['candidate_name']} 的面試資料表已用 jackychen@ 轉給 {r['client_to']}（副本 official@）\n附件：{names}{warn}")
            break


def cmd_release(args):
    """被擋下（held）的資料表：清掉已讀紀錄、用 force 重跑一次，照人選最後一封有附件的信直接轉。"""
    r = d1_http.query(f"SELECT * FROM candidate_doc_requests WHERE application_id={q(args.app)} AND status='held'")['results']
    if not r:
        sys.exit('這位人選沒有被擋下的資料表')
    d1_http.query(f"DELETE FROM candidate_doc_seen WHERE request_id={q(r[0]['id'])}")
    args.force_id = r[0]['id']
    cmd_tick(args)


def cmd_list(args):
    ensure()
    for r in d1_http.query("SELECT candidate_name, client_to, status, expires_at, forwarded_at FROM candidate_doc_requests ORDER BY created_at DESC LIMIT 20")['results']:
        print(r)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('tick')
    rl = sub.add_parser('release', help='被擋下的資料表照現在這份直接轉')
    rl.add_argument('--app', required=True)
    sub.add_parser('list')
    for name in ('add', 'send-forms'):
        s = sub.add_parser(name)
        s.add_argument('--app', required=True)
        s.add_argument('--thread', default=None)
        s.add_argument('--subject', default=None)
        s.add_argument('--to', default=None)
        s.add_argument('--days', type=int, default=7)
        s.add_argument('--note', default=None)
        s.add_argument('--only', action='append', default=[], help='只寄標題含這個關鍵字的表單，可重複')
    a = ap.parse_args()
    {'tick': cmd_tick, 'list': cmd_list, 'add': cmd_add, 'send-forms': cmd_send_forms, 'release': cmd_release}[a.cmd](a)
