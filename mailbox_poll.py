#!/usr/bin/env python3
"""official@step1ne.com（GoDaddy 信箱）收信 → 系統。

為什麼要有這支（2026-10-05 Jacky）：
    系統寄出的信，Reply-To 有兩個：official@ 跟 reply@reply.step1ne.com（Resend 收得到）。
    但很多人只回 official@——李佳龍、杜偉銘、恩群 Penny 的回信系統都沒抓到，
    全靠 Jacky 自己翻信箱才發現。這支每 5 分鐘用 IMAP 撈 official@ 的新信，
    交給 recruit worker 的 /internal/mailbox-ingest 比對（約電話回信／開發信回信／人選來信／客戶來信），
    比對到就推 TG，跟 Resend 收信那條路同一套規則。

安全邊界：
    · 只讀：用 readonly 開收件匣、BODY.PEEK 抓信——不會把信標成已讀、不刪信、不搬信、不回信。
    · 密碼只在 ~/.config/workflow-os/step1ne-mailbox.env（Jacky 自己輸入，chmod 600），程式不印出。
    · Mac／WSL2 兩台都排的話，用 deploy_locks 搶 10 分鐘的鎖，同一輪只有一台跑。

用法：
    python3 mailbox_poll.py            # 排程用：撈上次之後的新信
    python3 mailbox_poll.py --dry-run  # 只列出會送哪些信，不送
    python3 mailbox_poll.py --since 2026-10-05   # 從某天開始補（第一次啟用用）
"""
import base64
import datetime
import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import socket
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser('~/.config/workflow-os/step1ne-mailbox.state.json')
SKIP_FROM = re.compile(r'(@step1ne\.com$|@secureserver\.net$|godaddy|@titan\.email$|mailer-daemon|postmaster)', re.I)
DOC = re.compile(r'\.(pdf|docx?)$', re.I)
MAX_ATT = 8 * 1024 * 1024


def _env(path):
    e = {}
    for l in open(os.path.expanduser(path), encoding='utf-8'):
        k, _, v = l.strip().partition('=')
        if k:
            e[k] = v.strip('"\'')
    return e


def _dec(v):
    try:
        return str(email.header.make_header(email.header.decode_header(v or '')))
    except Exception:
        return v or ''


def _text(msg):
    """取純文字內容；只有 HTML 就去標籤。回覆信只留新寫的部分（切掉「於…寫道」以下的引用）。"""
    plain, html = '', ''
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get_filename():
            continue
        ct = part.get_content_type()
        try:
            payload = part.get_payload(decode=True) or b''
            s = payload.decode(part.get_content_charset() or 'utf-8', 'replace')
        except Exception:
            continue
        if ct == 'text/plain' and not plain:
            plain = s
        elif ct == 'text/html' and not html:
            html = s
    if not plain and html:
        plain = re.sub(r'<style[\s\S]*?</style>|<script[\s\S]*?</script>', ' ', html, flags=re.I)
        plain = re.sub(r'<br\s*/?>|</p>|</div>', '\n', plain, flags=re.I)
        plain = re.sub(r'<[^>]+>', ' ', plain).replace('&nbsp;', ' ')
    cut = re.search(r'\n[^\n]*(於\s*\d{4}年.*寫道|On .+wrote:|-----Original Message-----|寄件者[:：]|From:\s)', plain)
    if cut:
        plain = plain[:cut.start()]
    plain = re.sub(r'[ \t]+\n', '\n', plain)
    return re.sub(r'\n{3,}', '\n\n', plain).strip()


def _attachments(msg):
    out = []
    for part in msg.walk():
        name = _dec(part.get_filename() or '')
        if not name or not DOC.search(name):
            continue
        data = part.get_payload(decode=True) or b''
        if 0 < len(data) <= MAX_ATT:
            out.append({'filename': name, 'mime': part.get_content_type(), 'b64': base64.b64encode(data).decode()})
    return out


def _claim_lock():
    """同 parse_resumes：Mac／WSL2 同一輪只有一台跑。"""
    sys.path.insert(0, os.path.join(HERE, '..', '_tools'))
    try:
        import d1_rw
        host = socket.gethostname().replace("'", '')
        rows = d1_rw.q(
            "INSERT INTO deploy_locks (target, owner, host, acquired_at) "
            f"VALUES ('cron:mailbox_poll', 'mailbox_poll', '{host}', datetime('now','+8 hours')) "
            "ON CONFLICT(target) DO UPDATE SET host=excluded.host, acquired_at=excluded.acquired_at "
            "WHERE deploy_locks.host = excluded.host OR deploy_locks.acquired_at < datetime('now','+8 hours','-10 minutes') "
            "RETURNING host")
        return bool(rows) and rows[0].get('host') == host
    except Exception as e:
        print(f'（搶鎖失敗，照常執行：{e}）')
        return True


def _post(api, token, payload):
    req = urllib.request.Request(api.rstrip('/') + '/internal/mailbox-ingest', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}',
                                          'User-Agent': 'Mozilla/5.0 step1ne-mailbox-poll'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    dry = '--dry-run' in sys.argv
    since = sys.argv[sys.argv.index('--since') + 1] if '--since' in sys.argv else None
    if not dry and not _claim_lock():
        print('另一台機器這一輪已經在讀信，跳過')
        return
    box = _env('~/.config/workflow-os/step1ne-mailbox.env')
    rec = _env('~/.config/workflow-os/recruit.env')
    api, token = rec.get('RECRUIT_API_URL'), rec.get('RECRUIT_ADMIN_TOKEN')
    state = {}
    try:
        state = json.load(open(STATE))
    except Exception:
        pass

    m = imaplib.IMAP4_SSL(box['MAIL_IMAP_HOST'], 993, timeout=30)
    try:
        m.login(box['MAIL_IMAP_USER'], box['MAIL_IMAP_PASS'])
        typ, data = m.select('INBOX', readonly=True)
        uidvalidity = None
        typ2, uv = m.response('UIDVALIDITY')
        if uv and uv[0]:
            uidvalidity = uv[0].decode() if isinstance(uv[0], bytes) else str(uv[0])
        last = int(state.get('last_uid') or 0) if state.get('uidvalidity') == uidvalidity else 0
        if since:
            d = datetime.datetime.strptime(since, '%Y-%m-%d').strftime('%d-%b-%Y')
            typ, d1 = m.uid('search', None, 'SINCE', d)
        elif last:
            typ, d1 = m.uid('search', None, f'UID {last + 1}:*')
        else:
            # 第一次跑、沒指定日期：只從今天開始，不把整個信箱舊信倒進來
            typ, d1 = m.uid('search', None, 'SINCE', datetime.date.today().strftime('%d-%b-%Y'))
        uids = [int(x) for x in (d1[0].split() if d1 and d1[0] else []) if int(x) > (0 if since else last)]
        sent = 0
        max_uid = last
        for uid in uids:
            typ, md = m.uid('fetch', str(uid), '(BODY.PEEK[])')
            raw = next((x[1] for x in md if isinstance(x, tuple)), None)
            max_uid = max(max_uid, uid)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            frm = _dec(msg.get('From'))
            addr = email.utils.parseaddr(frm)[1].lower()
            if not addr or SKIP_FROM.search(addr):
                continue
            subject = _dec(msg.get('Subject'))
            try:
                dt = email.utils.parsedate_to_datetime(msg.get('Date')).astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                date_tpe = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                date_tpe = None
            payload = {'message_id': (msg.get('Message-ID') or f'uid:{uidvalidity}:{uid}').strip(),
                       'from': frm, 'subject': subject, 'text': _text(msg), 'date_taipei': date_tpe,
                       'attachments': _attachments(msg)}
            if dry:
                print(f'[dry] {date_tpe}｜{addr}｜{subject[:60]}｜附件 {len(payload["attachments"])}')
                continue
            try:
                res = _post(api, token, payload)
                sent += 1
                print(f'{date_tpe}｜{addr}｜{subject[:50]} → {res.get("matched")}{"（已推 TG）" if res.get("notified") else ""}')
            except Exception as e:
                print(f'❌ 送出失敗 uid={uid}：{e}')
                max_uid = min(max_uid, uid - 1)   # 下輪重試這封
                break
        if not dry:
            json.dump({'uidvalidity': uidvalidity, 'last_uid': max_uid,
                       'checked_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, open(STATE, 'w'))
            os.chmod(STATE, 0o600)
        print(f'檢查 {len(uids)} 封，送進系統 {sent} 封')
    finally:
        try:
            m.logout()
        except Exception:
            pass


if __name__ == '__main__':
    main()
