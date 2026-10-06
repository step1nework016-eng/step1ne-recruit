#!/usr/bin/env python3
"""系統自動寄出的信（走 Resend 的那些），補一份到 official@ 的「Sent（寄件備份）」。

為什麼要有這支（2026-10-06 Jacky）：開發信、追蹤信、人選邀請、約電話信都是 Resend 寄的，
Resend 不會把副本放進 GoDaddy 信箱的寄件備份，顧問在信箱裡查不到「我們跟這家寄過什麼」。
一對一回信（mail_outbox，10/5 起走 GoDaddy）本來就有寄件備份，這支不碰。

做法：撈已寄出、還沒備份過的信 → 照原本寄出的時間組一封信 → IMAP APPEND 到 Sent。
備份過的記在 D1 mail_sent_mirror（ref 當主鍵），兩台機器同時跑也不會重複放。
只放「副本」，不會真的寄出任何東西。

用法：
    python3 mail_sent_mirror.py            # 補一輪（排程每 10 分鐘叫一次）
    python3 mail_sent_mirror.py --dry      # 只列出會補哪些，不動信箱
"""
import datetime
import email.message
import email.utils
import imaplib
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('idaemon', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

TPE = datetime.timezone(datetime.timedelta(hours=8))
FROM = 'Step1ne 德仁管理顧問 <official@step1ne.com>'
# 2026-10-06 起三支 Worker 經 Resend 寄出的每一封都會記進 mail_sent_log（含原本的寄件人、HTML）。
# 這個時間點之後的信一律從 mail_sent_log 補，各業務表只補這之前的舊信，避免同一封放兩次。
CUTOVER = '2026-10-06 13:45:00'


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def env_file(path):
    out = {}
    for line in open(os.path.expanduser(path), encoding='utf-8'):
        k, _, v = line.strip().partition('=')
        if k:
            out[k] = v.strip('"\'')
    return out


def pending():
    """各種自動寄出的信，統一成 (ref, 寄出時間, 收件, 主旨, 內文)。"""
    D.d1("CREATE TABLE IF NOT EXISTS mail_sent_mirror (ref TEXT PRIMARY KEY, mirrored_at TEXT)")
    done = {r['ref'] for r in (D.d1("SELECT ref FROM mail_sent_mirror") or [])}
    out = []

    def add(ref, at, to, subject, body):
        if ref in done or not at or str(at) >= CUTOVER:
            return
        out.append((ref, at, to or '', subject or '（無主旨）', body or '', None, None))

    for r in D.d1("SELECT id, sent_at, from_addr, to_email, subject, text, html FROM mail_sent_log") or []:
        ref = f"mail_sent_log:{r['id']}"
        if ref not in done and r['sent_at']:
            out.append((ref, r['sent_at'], r['to_email'] or '', r['subject'] or '（無主旨）', r['text'] or '', r['html'], r['from_addr']))

    for r in D.d1("SELECT id, contact_email, subject, body, sent_at FROM bd_outreach WHERE sent_at IS NOT NULL") or []:
        add(f"bd_outreach:{r['id']}", r['sent_at'], r['contact_email'], r['subject'], r['body'])
    for r in D.d1("SELECT id, contact_email, subject, followup1, followup1_sent_at FROM bd_outreach "
                  "WHERE followup1_sent_at IS NOT NULL") or []:
        add(f"bd_outreach_f1:{r['id']}", r['followup1_sent_at'], r['contact_email'], 'Re: ' + (r['subject'] or ''), r['followup1'])
    for r in D.d1("SELECT id, contact_email, subject, followup2, followup2_sent_at FROM bd_outreach "
                  "WHERE followup2_sent_at IS NOT NULL") or []:
        add(f"bd_outreach_f2:{r['id']}", r['followup2_sent_at'], r['contact_email'], 'Re: ' + (r['subject'] or ''), r['followup2'])
    for r in D.d1("SELECT id, to_email, subject, body, sent_at FROM call_booking_mails WHERE sent_at IS NOT NULL") or []:
        add(f"call_booking_mails:{r['id']}", r['sent_at'], r['to_email'], r['subject'], r['body'])
    # 人選邀請信系統只存了主旨跟邀請原因，沒有存完整內文——照實寫出來，不要假裝是原信
    for r in D.d1("SELECT id, to_email, subject, why, sent_at FROM sourced_email_invites WHERE sent_at IS NOT NULL") or []:
        add(f"sourced_email_invites:{r['id']}", r['sent_at'], r['to_email'], r['subject'],
            f"（系統只存了主旨與邀請原因，沒有存完整內文）\n\n邀請原因：{r['why'] or ''}")
    return out


def build(at, to, subject, body, html=None, from_addr=None):
    try:
        dt = datetime.datetime.strptime(str(at)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=TPE)
    except ValueError:
        dt = datetime.datetime.strptime(str(at)[:10], '%Y-%m-%d').replace(tzinfo=TPE)
    msg = email.message.EmailMessage()
    msg['From'] = from_addr or FROM
    msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = email.utils.format_datetime(dt)
    msg['X-Step1ne-Copy'] = 'system-sent-backup'
    note = f"（寄件備份副本：這封由系統於 {dt:%Y-%m-%d %H:%M} 自動寄出，這裡只是存檔，沒有再寄一次）"
    if not body and html:
        import re as _re, html as _h
        body = _h.unescape(_re.sub(r'<[^>]+>', '\n', _re.sub(r'(?is)<(style|script).*?</\1>', '', html)))
        body = _re.sub(r'\n\s*\n+', '\n\n', body).strip()
    msg.set_content(f"{body}\n\n——\n{note}")
    if html:
        msg.add_alternative(f"{html}<p style=\"color:#888;font-size:12px\">{note}</p>", subtype='html')
    return dt, msg


def main():
    dry = '--dry' in sys.argv
    todo = pending()
    if not todo:
        return
    log(f'要補進寄件備份：{len(todo)} 封')
    if dry:
        for ref, at, to, subject, *_ in todo[:50]:
            log(f'  {at}｜{to}｜{subject[:40]}｜{ref}')
        return
    box = env_file('~/.config/workflow-os/step1ne-mailbox.env')
    imap = imaplib.IMAP4_SSL(box['MAIL_IMAP_HOST'], 993, timeout=30)
    imap.login(box['MAIL_IMAP_USER'], box['MAIL_IMAP_PASS'])
    n = 0
    try:
        for ref, at, to, subject, body, html, from_addr in todo:
            dt, msg = build(at, to, subject, body, html, from_addr)
            typ, _ = imap.append('Sent', '\\Seen', imaplib.Time2Internaldate(dt.timestamp()), msg.as_bytes())
            if typ != 'OK':
                log(f'⚠️ {ref} 放不進寄件備份：{typ}')
                continue
            D.d1(f"INSERT OR IGNORE INTO mail_sent_mirror (ref, mirrored_at) VALUES ({D.q(ref)}, datetime('now','+8 hours'))")
            n += 1
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    log(f'✅ 補進寄件備份 {n} 封')


if __name__ == '__main__':
    main()
