#!/usr/bin/env python3
"""一對一的信從 GoDaddy official@ 寄出（2026-10-05 Jacky 拍板）。

顧問後台的「約電話」「確認電話時間」「回客戶」三種信，後台只把信放進 D1 的 mail_outbox，
這支每分鐘撈出來：
    1. 用 GoDaddy SMTP（smtpout.secureserver.net:465）以 official@ 寄出
       ——有對方上一封的 Message-ID 就帶 In-Reply-To／References，對方信箱會接在同一串
    2. 用 IMAP 把同一封存進 official@ 的「Sent（寄件備份）」，Jacky 在網頁信箱看得到完整來回
    3. 標記 sent；寄失敗標 failed 並推 TG
Mac 關機或斷網超過 10 分鐘：後台每 10 分鐘的排程會改用 Resend 補寄（outboxFallback），信不會卡住。

帳密：~/.config/workflow-os/step1ne-mailbox.env（跟 mailbox_poll.py 共用）。
用法：python3 mail_outbox_send.py [--dry-run]
"""
import datetime
import email.utils
import imaplib
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_tools'))
import d1_rw  # noqa: E402
sys.path.insert(0, HERE)
from weekday_check import weekday_errors  # noqa: E402

SMTP_HOST = 'smtpout.secureserver.net'


def _env(path):
    e = {}
    for l in open(os.path.expanduser(path), encoding='utf-8'):
        k, _, v = l.strip().partition('=')
        if k:
            e[k] = v.strip('"\'')
    return e


def _tg(text):
    try:
        e = _env('~/.config/workflow-os/step1ne-tg.env')
        d = urllib.parse.urlencode({'chat_id': e['TG_CHAT_ID'], 'message_thread_id': e.get('TG_THREAD_ID') or '', 'text': text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage", d, timeout=20)
    except Exception:
        pass


def main():
    dry = '--dry-run' in sys.argv
    try:
        rows = d1_rw.q("SELECT * FROM mail_outbox WHERE status='pending' ORDER BY created_at LIMIT 10")
    except Exception as e:
        if 'no such table' in str(e):
            return
        raise
    if not rows:
        return
    box = _env('~/.config/workflow-os/step1ne-mailbox.env')
    user, pw = box['MAIL_IMAP_USER'], box['MAIL_IMAP_PASS']
    smtp = None if dry else smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=30, context=ssl.create_default_context())
    imap = None
    try:
        if smtp:
            smtp.login(user, pw)
        for m in rows:
            if dry:
                print(f"[dry] {m['kind']} → {m['to_email']}｜{m['subject']}")
                continue
            # 日期跟星期對不上就不寄（2026-10-08 寄出「10/9（四）」，其實是星期五）
            wd_err = weekday_errors((m.get('subject') or '') + '\n' + (m.get('text') or ''))
            if wd_err:
                d1_rw.q("UPDATE mail_outbox SET status='blocked', error=? WHERE id=? AND status='pending'",
                        ['星期寫錯，已擋下：' + '；'.join(wd_err)[:250], m['id']])
                _tg(f"⛔ 信沒寄出（星期寫錯）：{m['to_email']}｜{m['subject']}\n" + '\n'.join(wd_err))
                print(f"⛔ {m['to_email']}：{wd_err}")
                continue
            # 搶這封（Mac 跟後台補寄同時看到同一封時，只會有一邊寄）
            claim = d1_rw.q("UPDATE mail_outbox SET status='sending', via='godaddy', attempts=COALESCE(attempts,0)+1 "
                            "WHERE id=? AND status='pending' RETURNING id", [m['id']])
            if not claim:
                continue
            msg = EmailMessage()
            msg['From'] = m['from_addr'] or f'Step1ne 德仁管理顧問 <{user}>'
            msg['To'] = m['to_email']
            try:
                rt = [x for x in json.loads(m.get('reply_to') or '[]') if x]
            except Exception:
                rt = []
            if rt:
                msg['Reply-To'] = ', '.join(rt)
            msg['Subject'] = m['subject'] or ''
            msg['Date'] = email.utils.formatdate(localtime=True)
            mid = email.utils.make_msgid(domain='step1ne.com')
            msg['Message-ID'] = mid
            if m.get('in_reply_to'):
                msg['In-Reply-To'] = m['in_reply_to']
                msg['References'] = m['in_reply_to']
            msg.set_content(m.get('text') or '')
            if m.get('html'):
                msg.add_alternative(m['html'], subtype='html')
            try:
                smtp.send_message(msg)
            except Exception as e:
                d1_rw.q("UPDATE mail_outbox SET status='pending', error=? WHERE id=?", [f'GoDaddy 寄信失敗：{e}'[:300], m['id']])
                print(f"❌ {m['to_email']}：{e}")
                if (m.get('attempts') or 0) >= 2:
                    _tg(f"⚠️ official@ 寄信失敗 3 次：{m['to_email']}｜{m['subject']}\n原因：{str(e)[:200]}\n10 分鐘後會改用 Resend 補寄。")
                continue
            d1_rw.q("UPDATE mail_outbox SET status='sent', sent_at=?, message_id=?, error=NULL WHERE id=?",
                    [datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), mid, m['id']])
            print(f"✅ {m['kind']} → {m['to_email']}｜{m['subject']}")
            # 存一份到「寄件備份」——存不進去不影響已經寄出
            try:
                if imap is None:
                    imap = imaplib.IMAP4_SSL(box['MAIL_IMAP_HOST'], 993, timeout=30)
                    imap.login(user, pw)
                imap.append('Sent', '\\Seen', imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            except Exception as e:
                print(f"（存寄件備份失敗，信已寄出：{e}）")
    finally:
        for c in (smtp, imap):
            try:
                c and (c.quit() if c is smtp else c.logout())
            except Exception:
                pass


if __name__ == '__main__':
    main()
