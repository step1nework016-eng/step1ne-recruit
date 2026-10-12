#!/usr/bin/env python3
"""兩個公司信箱（official@、jackychen@）的收件匣＋寄件備份，存一份進 Step1ne 系統。

為什麼要有這支（2026-10-12 Jacky 核准）：顧問要在後台查「我們跟這個人選／這家客戶來回寄過什麼」，
不用每次翻信箱。mailbox_poll.py 只挑「對得上的新來信推 TG」，不留全文、不留寄件、不留附件；這支是完整存檔。

存什麼：每封信的信箱、收/寄、時間（台北）、寄件人、收件人、副本、主旨、Message-ID、回信串、
純文字內文（約 50KB 上限）、附件（存 R2，15MB 上限；簽名檔那種 20KB 以下小圖跳過）。
並用 Email／公司網域自動對到人選卡（applications）、客戶卡（client_companies／client_contacts）、開發紀錄（bd_outreach）。

安全邊界：
    · 只讀：readonly 開資料夾、BODY.PEEK 抓信——不標已讀、不刪、不搬、不寄。
    · 只在 Mac 跑：jackychen@ 的密碼只在這台（~/.config/workflow-os/jacky-mailbox.env）。
    · jackychen@ 的信只准 Jacky／Phoebe 看；讀取畫面等身分機制定案才做（見 migrations/2026-10-12_mail_archive.sql）。

用法：
    python3 mail_archive.py                 # 排程用（launchd 每 10 分鐘）：撈上次之後的新信，每個資料夾一輪最多 40 封
    python3 mail_archive.py --backfill 30   # 第一次：補最近 30 天（不設封數上限）
    python3 mail_archive.py --dry-run       # 只列出會存哪些
"""
import datetime
import email
import email.header
import email.utils
import fcntl
import html as htmllib
import imaplib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_tools'))
import d1_rw  # noqa: E402

CFG = os.path.expanduser('~/.config/workflow-os')
STATE = os.path.join(CFG, 'mail_archive.state.json')
LOCK = os.path.join(CFG, 'mail_archive.lock')
HOST = 'imap.secureserver.net'
API = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
TPE = datetime.timezone(datetime.timedelta(hours=8))
FOLDERS = [('INBOX', 'in'), ('Sent', 'sent')]
BODY_MAX = 50_000
ATT_MAX = 15 * 1024 * 1024
SMALL_IMG = 20 * 1024
PER_RUN = 40
FREE_DOMAINS = {'gmail.com', 'yahoo.com', 'yahoo.com.tw', 'hotmail.com', 'hotmail.com.tw', 'outlook.com', 'live.com',
                'msn.com', 'icloud.com', 'me.com', 'mac.com', 'qq.com', '163.com', '126.com', 'pchome.com.tw',
                'seed.net.tw', 'hinet.net', 'ms1.hinet.net', 'protonmail.com', 'proton.me', 'googlemail.com',
                'aol.com', 'gmx.com', 'mail.com', 'yandex.com', 'step1ne.com', 'aijob.com.tw'}


def log(m):
    print(f'[{time.strftime("%m-%d %H:%M:%S")}] {m}', flush=True)


def env_file(path):
    out = {}
    for line in open(os.path.join(CFG, path), encoding='utf-8'):
        k, _, v = line.strip().partition('=')
        if k:
            out[k] = v.strip('"\'')
    return out


def admin_token():
    return env_file('recruit.env')['RECRUIT_ADMIN_TOKEN']


def boxes():
    a = env_file('step1ne-mailbox.env')
    b = env_file('jacky-mailbox.env')
    return [('official@step1ne.com', a['MAIL_IMAP_USER'], a['MAIL_IMAP_PASS']),
            ('jackychen@step1ne.com', b['MAIL_USER'], b['MAIL_PASS'])]


def dec(v):
    try:
        return str(email.header.make_header(email.header.decode_header(v or ''))).strip()
    except Exception:
        return (v or '').strip()


def addr_list(msg, header):
    vals = [dec(x) for x in msg.get_all(header, [])]
    return [(n, a.lower()) for n, a in email.utils.getaddresses(vals) if a and '@' in a]


def html_to_text(h):
    h = re.sub(r'(?is)<(style|script|head)\b.*?</\1>', ' ', h)
    h = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>', '\n', h)
    h = re.sub(r'<[^>]+>', ' ', h)
    return htmllib.unescape(h)


def body_text(msg):
    plain, html = '', ''
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart' or part.get_filename():
            continue
        if (part.get('Content-Disposition') or '').lower().startswith('attachment'):
            continue
        ct = part.get_content_type()
        if ct not in ('text/plain', 'text/html'):
            continue
        try:
            s = (part.get_payload(decode=True) or b'').decode(part.get_content_charset() or 'utf-8', 'replace')
        except Exception:
            continue
        if ct == 'text/plain' and not plain:
            plain = s
        elif ct == 'text/html' and not html:
            html = s
    text = plain if plain.strip() else html_to_text(html)
    text = text.replace('\r\n', '\n').replace('\xa0', ' ')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if len(text) > BODY_MAX:
        return text[:BODY_MAX], 1
    return text, 0


def attachments(msg):
    """回傳 [(序號, 檔名, mime, bytes 或 None, 跳過原因)]，序號固定（重跑也一樣）。"""
    out, idx = [], 0
    for part in msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        disp = (part.get('Content-Disposition') or '').lower()
        name = dec(part.get_filename() or '')
        ctype = part.get_content_type()
        is_att = disp.startswith('attachment') or bool(name)
        if not is_att:
            continue
        if ctype in ('text/plain', 'text/html') and not name:
            continue
        data = part.get_payload(decode=True) or b''
        i = idx
        idx += 1
        if not data:
            continue
        inline_img = ctype.startswith('image/') and (part.get('Content-ID') or disp.startswith('inline'))
        if ctype.startswith('image/') and len(data) < SMALL_IMG:
            continue                      # 簽名檔 logo 這類小圖
        if inline_img and not disp.startswith('attachment'):
            continue                      # 內文裡的嵌入圖片，不是附件
        name = name or f'attachment_{i}'
        if len(data) > ATT_MAX:
            out.append((i, name, ctype, None, f'{name}（{len(data) // 1024 // 1024}MB，超過 15MB 沒存）'))
        else:
            out.append((i, name, ctype, data, None))
    return out


def taipei_time(msg, internal=None):
    try:
        return email.utils.parsedate_to_datetime(msg.get('Date')).astimezone(TPE).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    if internal:
        try:
            return datetime.datetime.fromtimestamp(time.mktime(internal), TPE).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
    return None


# ── 對到人選／客戶 ──
class Linker:
    def __init__(self):
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        self.apps = {}
        for r in d1_rw.q("SELECT id, name, lower(trim(email)) AS e, created_at FROM applications "
                         "WHERE email IS NOT NULL AND email LIKE '%@%' ORDER BY created_at"):
            self.apps[r['e']] = r                         # 同一個 Email 多筆應徵：留最新那筆
        self.companies, self.co_domain = {}, {}
        for r in d1_rw.q("SELECT id, display_name, lower(trim(contact_email)) AS e FROM client_companies"):
            if r['e'] and '@' in r['e']:
                self.companies[r['e']] = r
                d = r['e'].split('@', 1)[1]
                if d not in FREE_DOMAINS:
                    self.co_domain.setdefault(d, r)
        self.contacts = {}
        for r in d1_rw.q("SELECT id, client_name, contact_name, lower(trim(email)) AS e FROM client_contacts"):
            if r['e'] and '@' in r['e']:
                self.contacts[r['e']] = r
                co = next((c for c in self.companies.values() if c['display_name'] == r['client_name']), None)
                d = r['e'].split('@', 1)[1]
                if co and d not in FREE_DOMAINS:
                    self.co_domain.setdefault(d, co)
        self.bd, self.bd_domain = {}, {}
        for r in d1_rw.q("SELECT id, company, lower(trim(contact_email)) AS e FROM bd_outreach "
                         "WHERE contact_email LIKE '%@%' ORDER BY created_at"):
            self.bd[r['e']] = r
            d = r['e'].split('@', 1)[1]
            if d not in FREE_DOMAINS:
                self.bd_domain[d] = r
        self.loaded = True

    def match(self, addrs):
        self.load()
        links = []
        for a in addrs:
            d = a.split('@', 1)[1]
            if d in ('step1ne.com', 'reply.step1ne.com'):
                continue
            if a in self.apps:
                r = self.apps[a]
                links.append(('candidate', r['id'], r['name'], 'email', a))
            if a in self.companies:
                r = self.companies[a]
                links.append(('client', r['id'], r['display_name'], 'email', a))
            elif d in self.co_domain:
                r = self.co_domain[d]
                links.append(('client', r['id'], r['display_name'], 'domain', a))
            if a in self.contacts:
                r = self.contacts[a]
                links.append(('client_contact', r['id'], f"{r['client_name']}／{r['contact_name'] or ''}".rstrip('／'), 'email', a))
            if a in self.bd:
                r = self.bd[a]
                links.append(('bd', r['id'], r['company'], 'email', a))
            elif d in self.bd_domain:
                r = self.bd_domain[d]
                links.append(('bd', r['id'], r['company'], 'domain', a))
        seen, out = set(), []
        for l in links:
            if (l[0], l[1]) not in seen:
                seen.add((l[0], l[1]))
                out.append(l)
        return out


def upload(token, archive_id, idx, name, mime, data):
    q = urllib.parse.urlencode({'archive_id': archive_id, 'idx': idx, 'name': name, 'mime': mime})
    req = urllib.request.Request(f'{API}/admin/mail-archive/attachment?{q}', data=data, method='POST',
                                 headers={'Authorization': f'Bearer {token}', 'User-Agent': UA,
                                          'Content-Type': 'application/octet-stream'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def store(mailbox, folder, imap_folder, uv, uid, raw, internal, linker, token, dry):
    msg = email.message_from_bytes(raw)
    mid = (msg.get('Message-ID') or '').strip() or f'uid:{mailbox}:{imap_folder}:{uv}:{uid}'
    frm = addr_list(msg, 'From')
    to, cc = addr_list(msg, 'To'), addr_list(msg, 'Cc')
    subject = dec(msg.get('Subject'))
    when = taipei_time(msg, internal)
    text, trunc = body_text(msg)
    atts = attachments(msg)
    to_save = [a for a in atts if a[3] is not None]
    skipped = '；'.join(a[4] for a in atts if a[4]) or None
    others = [a for _, a in (frm + to + cc)]
    links = linker.match(list(dict.fromkeys(others)))
    if dry:
        log(f'[dry] {mailbox}｜{folder}｜{when}｜{(frm[0][1] if frm else "")}｜{subject[:50]}｜附件{len(to_save)}｜對到{[l[0] + ":" + str(l[2]) for l in links]}')
        return 'dry', len(to_save), bool(links)

    fmt = lambda lst: ', '.join(f'{n} <{a}>' if n else a for n, a in lst)  # noqa: E731
    prim = next((l for t in ('candidate', 'client', 'client_contact', 'bd') for l in links if l[0] == t), None)
    rows = d1_rw.q(
        "INSERT OR IGNORE INTO mail_archive (mailbox, folder, imap_folder, uidvalidity, uid, message_id, in_reply_to, refs, "
        "sent_at, from_addr, from_name, to_addrs, cc_addrs, subject, body_text, body_truncated, att_count, att_saved, "
        "att_skipped, is_system_copy, link_type, link_id, link_name, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,datetime('now','+8 hours')) RETURNING id",
        [mailbox, folder, imap_folder, uv, uid, mid[:500], (msg.get('In-Reply-To') or '').strip()[:500] or None,
         re.sub(r'\s+', ' ', msg.get('References') or '').strip()[:2000] or None, when,
         frm[0][1] if frm else None, frm[0][0] if frm else None, fmt(to)[:4000], fmt(cc)[:4000] or None,
         subject[:1000], text, trunc, len(to_save), skipped,
         1 if msg.get('X-Step1ne-Copy') else 0,
         prim[0] if prim else None, str(prim[1]) if prim else None, prim[2] if prim else None])
    if rows:
        aid = rows[0]['id']
        new = True
    else:
        r = d1_rw.q("SELECT id, att_count, att_saved FROM mail_archive WHERE mailbox=? AND folder=? AND message_id=?",
                    [mailbox, folder, mid[:500]])
        if not r or (r[0]['att_saved'] or 0) >= (r[0]['att_count'] or 0):
            return 'dup', 0, False
        aid, new = r[0]['id'], False          # 上次附件沒存完，補存
    if new and links:
        vals, params = [], []
        for t, i, n, by, a in links:
            vals.append('(?,?,?,?,?,?)')
            params += [aid, t, str(i), n, by, a]
        d1_rw.q("INSERT OR IGNORE INTO mail_archive_links (archive_id, link_type, link_id, link_name, match_by, addr) VALUES "
                + ','.join(vals), params)
    saved = 0
    for idx, name, mime, data, _ in to_save:
        try:
            res = upload(token, aid, idx, name, mime, data)
            saved += 1 if res.get('ok') else 0
        except urllib.error.HTTPError as e:
            log(f'⚠️ 附件存不進去 #{aid} {name}：HTTP {e.code} {e.read().decode()[:200]}')
        except Exception as e:
            log(f'⚠️ 附件存不進去 #{aid} {name}：{e}')
    return ('new' if new else 'fix'), saved, bool(links)


def run_folder(m, mailbox, imap_folder, folder, state, since_days, dry, linker, token, stats):
    typ, _ = m.select(f'"{imap_folder}"', readonly=True)
    if typ != 'OK':
        log(f'⚠️ {mailbox} 打不開 {imap_folder}')
        return
    uv = m.response('UIDVALIDITY')[1]
    uv = uv[0].decode() if uv and isinstance(uv[0], bytes) else str(uv[0] if uv else '')
    key = f'{mailbox}|{imap_folder}'
    st = state.get(key) or {}
    last = int(st.get('last_uid') or 0) if st.get('uidvalidity') == uv else 0
    if since_days:
        d = (datetime.date.today() - datetime.timedelta(days=since_days)).strftime('%d-%b-%Y')
        typ, d1 = m.uid('search', None, 'SINCE', d)
        cap = None
    elif last:
        typ, d1 = m.uid('search', None, f'UID {last + 1}:*')
        cap = PER_RUN
    else:
        # 還沒補過舊信、狀態又不見了：只抓今天，不把整個信箱倒進來（補舊信要明確下 --backfill）
        typ, d1 = m.uid('search', None, 'SINCE', datetime.date.today().strftime('%d-%b-%Y'))
        cap = PER_RUN
    uids = sorted(int(x) for x in (d1[0].split() if d1 and d1[0] else []))
    if not since_days:
        uids = [u for u in uids if u > last]
    if cap:
        uids = uids[:cap]
    s = stats.setdefault(key, {'checked': 0, 'new': 0, 'dup': 0, 'att': 0, 'linked': 0, 'err': 0})
    max_uid = last
    for u in uids:
        s['checked'] += 1
        try:
            typ, md = m.uid('fetch', str(u), '(INTERNALDATE BODY.PEEK[])')
            raw, internal = None, None
            for x in md or []:
                if isinstance(x, tuple):
                    raw = x[1]
                    internal = imaplib.Internaldate2tuple(x[0])
            if not raw:
                max_uid = max(max_uid, u)
                continue
            res, saved, linked = store(mailbox, folder, imap_folder, uv, u, raw, internal, linker, token, dry)
            s['new' if res in ('new', 'dry') else 'dup'] += 1
            s['att'] += saved
            s['linked'] += 1 if linked else 0
            max_uid = max(max_uid, u)
        except Exception as e:
            s['err'] += 1
            log(f'❌ {key} uid={u}：{e}')
            break                                   # 下一輪從這封重試
        if not dry and max_uid > last:
            state[key] = {'uidvalidity': uv, 'last_uid': max_uid, 'checked_at': time.strftime('%Y-%m-%d %H:%M:%S')}
            save_state(state)


def save_state(state):
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def main():
    dry = '--dry-run' in sys.argv
    since_days = int(sys.argv[sys.argv.index('--backfill') + 1]) if '--backfill' in sys.argv else 0
    lockf = open(LOCK, 'w')
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('上一輪還在跑，這輪跳過')
        return
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    token = admin_token()
    linker, stats = Linker(), {}
    for mailbox, user, pw in boxes():
        try:
            m = imaplib.IMAP4_SSL(HOST, 993, timeout=60)
            m.login(user, pw)
        except Exception as e:
            log(f'❌ {mailbox} 登入失敗：{e}')
            continue
        try:
            for imap_folder, folder in FOLDERS:
                run_folder(m, mailbox, imap_folder, folder, state, since_days, dry, linker, token, stats)
        finally:
            try:
                m.logout()
            except Exception:
                pass
    for k, s in stats.items():
        if s['checked'] or s['err']:
            log(f"{k}：看了 {s['checked']}｜新存 {s['new']}｜已存過 {s['dup']}｜附件 {s['att']}｜對到卡片 {s['linked']}｜錯誤 {s['err']}")


if __name__ == '__main__':
    main()
