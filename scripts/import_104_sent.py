#!/usr/bin/env python3
"""把「從 104 下載、已經寄過信、還沒通電話」的人選批次建成顧問後台的人選卡片。

2026-10-23 建（第一次用在：新竹 BIM 工程師（儲備培訓）20 位無接通人選）。

做的事：
  1. 讀履歷資料夾（檔名＝姓名.pdf）＋寄信紀錄資料夾（ab_tracking.csv：name,email,version,sent_at）
  2. 每份履歷用 pdftotext 抓：聯絡電話（寫「求職者已隱藏」就留空）、希望待遇、可上班日
  3. 先查系統有沒有這個人（同 Email，或同名＋同電話）：
       有 → 不重建，只補 104 來源標籤（原本是空／不明／顧問加入才改）＋把備註接在後面
       沒有 → 呼叫後台 POST /admin/create-candidate 建卡片＋上傳履歷 PDF
  4. 建卡一律 hold=true（狀態「新進件」）：阿財不預熱、不擬題目、不發提醒；不寄任何信、不發 LINE；
     quiet=true 不推 TG 建檔通知（最後印一份彙整給人看）。

用法（先 --dry-run 看清單，確認後再拿掉）：
  python3 scripts/import_104_sent.py \
    --pdf-dir ~/Desktop/104/已寄出 \
    --sent-dir ~/下載項目/BIM新竹_無接通信件AB_20261023 \
    --sent-dir ~/下載項目/BIM新竹_無接通信件AB_第三批_20261023 \
    --job semiconductor-bim-mep-drafting-trainee-hsinchu \
    --mail-label "BIM 信" --dry-run

備註格式：「【104 無接通】10/23 已寄 BIM 信（A 版），尚未電話聯繫」
寄信日期取 ab_tracking.csv 的 sent_at；沒有寄信紀錄的履歷會跳過並列出來（不猜版本）。
"""
import argparse
import base64
import csv
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import d1_http as D  # noqa: E402
import api_token  # noqa: E402

API = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'
NOTE_TAG = '【104 無接通】'


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def digits(s):
    return re.sub(r'\D', '', s or '')


def load_sent(dirs):
    out = {}
    for d in dirs:
        p = os.path.join(os.path.expanduser(d), 'ab_tracking.csv')
        with open(p, encoding='utf-8-sig') as fh:
            for r in csv.DictReader(fh):
                name = (r.get('name') or '').strip()
                if name:
                    out[name] = {'email': (r.get('email') or '').strip().lower(),
                                 'version': (r.get('version') or '').strip(),
                                 'sent_at': (r.get('sent_at') or '').strip(),
                                 'job': (r.get('job') or '').strip()}
    return out


def pdf_fields(path):
    txt = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True).stdout

    def grab(label):
        m = re.search(label + r'[ \t]+(.+)', txt)
        return m.group(1).strip() if m else ''
    phone_raw = grab('聯絡電話')
    phone = None
    if phone_raw and '隱藏' not in phone_raw:
        m = re.search(r'0\d[\d\- ]{7,12}\d', phone_raw)
        phone = m.group(0).replace(' ', '') if m else None
    email = ''
    m = re.search(r'E-?mail[ \t]+(\S+@\S+)', txt, re.I)
    if m:
        email = m.group(1).strip().lower()
    return {'phone': phone, 'email': email,
            'expected_salary': grab('希望待遇') or '未填（104 履歷）',
            'available_date': grab('可上班日') or '未填（104 履歷）',
            'text_ok': len(txt.strip()) > 100}


def mmdd(sent_at):
    m = re.match(r'\d{4}-(\d{2})-(\d{2})', sent_at or '')
    return f'{int(m.group(1))}/{int(m.group(2))}' if m else ''


def find_existing(emails, names):
    hits = []
    for i in range(0, len(emails), 90):
        part = emails[i:i + 90]
        if part:
            hits += D.query("SELECT id, name, email, phone, origin, note, status, created_at FROM applications "
                            f"WHERE lower(email) IN ({','.join(q(e) for e in part)})")['results'] or []
    for i in range(0, len(names), 90):
        part = names[i:i + 90]
        if part:
            hits += D.query("SELECT id, name, email, phone, origin, note, status, created_at FROM applications "
                            f"WHERE name IN ({','.join(q(n) for n in part)})")['results'] or []
    uniq = {}
    for h in hits:
        uniq[h['id']] = h
    return list(uniq.values())


def post(path, body, tok):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method='POST',
                                 headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {tok}',
                                          'User-Agent': 'Mozilla/5.0 step1ne-import-104'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {'ok': False, 'error': f'HTTP {e.code}'}
    except Exception as e:  # 逾時／斷線：不中斷整批；重跑一次會用 Email 認出已建好的，不會重複
        return {'ok': False, 'error': f'連線失敗：{e}'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf-dir', required=True)
    ap.add_argument('--sent-dir', action='append', required=True, help='含 ab_tracking.csv 的資料夾，可給多次')
    ap.add_argument('--job', required=True, help='職缺 slug')
    ap.add_argument('--mail-label', default='職缺', help='備註裡寫「已寄 ___ 信」')
    ap.add_argument('--by', default=None, help='寫進 handled_by（選填）')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    sent = load_sent(a.sent_dir)
    pdf_dir = os.path.expanduser(a.pdf_dir)
    pdfs = sorted(f for f in os.listdir(pdf_dir) if f.lower().endswith('.pdf'))
    people, skipped = [], []
    for f in pdfs:
        name = os.path.splitext(f)[0].strip()
        s = sent.get(name)
        if not s:
            skipped.append((name, '寄信紀錄裡找不到這個人'))
            continue
        info = pdf_fields(os.path.join(pdf_dir, f))
        email = s['email'] or info['email']
        ver = f"{s['version']} 版" if s['version'] else '版本未記'
        note = f"{NOTE_TAG}{mmdd(s['sent_at'])} 已寄 {a.mail_label}（{ver}），尚未電話聯繫"
        people.append({'name': name, 'file': f, 'email': email, 'note': note, **info})

    existing = find_existing([p['email'] for p in people if p['email']], [p['name'] for p in people])
    tok = api_token.get()
    created, updated, failed = [], [], []
    for p in people:
        dup = [e for e in existing if p['email'] and (e.get('email') or '').lower() == p['email']]
        if not dup and p['phone']:
            dup = [e for e in existing if e['name'] == p['name'] and digits(e.get('phone')) and digits(e.get('phone')) == digits(p['phone'])]
        same_name_only = [e for e in existing if e['name'] == p['name'] and e not in dup]
        if dup:
            e = sorted(dup, key=lambda x: x.get('created_at') or '')[-1]
            set_origin = (e.get('origin') in (None, '', '不明', '顧問加入'))
            row = {'name': p['name'], 'id': e['id'], 'old_origin': e.get('origin'), 'origin_set': set_origin}
            if not a.dry_run:
                sets = []
                if set_origin:
                    sets.append("origin='104'")
                if NOTE_TAG not in (e.get('note') or ''):
                    sets.append(f"note=CASE WHEN COALESCE(note,'')='' THEN {q(p['note'])} ELSE note || char(10) || {q(p['note'])} END")
                if sets:
                    D.query(f"UPDATE applications SET {', '.join(sets)} WHERE id={q(e['id'])}")
            updated.append(row)
            continue
        if a.dry_run:
            created.append({'name': p['name'], 'email': p['email'], 'phone': p['phone'] or '（履歷隱藏）',
                            'salary': p['expected_salary'], 'avail': p['available_date'], 'note': p['note'],
                            'same_name_warn': bool(same_name_only)})
            continue
        with open(os.path.join(pdf_dir, p['file']), 'rb') as fh:
            b64 = base64.b64encode(fh.read()).decode()
        r = post('/admin/create-candidate', {
            'name': p['name'], 'job_slug': a.job, 'email': p['email'], 'phone': p['phone'],
            'expected_salary': p['expected_salary'], 'available_date': p['available_date'],
            'note': p['note'], 'channel': '104', 'origin': '104', 'hold': True, 'quiet': True,
            'send_mail': False, 'by': a.by,
            'resume_b64': b64, 'resume_name': p['file'], 'resume_mime': 'application/pdf'}, tok)
        if r.get('ok') and r.get('status') == 'new' and r.get('has_resume'):
            created.append({'name': p['name'], 'id': r['id'], 'same_name_warn': bool(same_name_only)})
        elif r.get('ok'):
            # 舊版後台（沒認得 hold/origin）會建成 ready——立刻改回 new 並補標籤，避免阿財撈到
            D.query(f"UPDATE applications SET status='new', origin='104' WHERE id={q(r['id'])}")
            created.append({'name': p['name'], 'id': r['id'], 'warn': f"後台回傳 status={r.get('status')} has_resume={r.get('has_resume')}，已手動改 new"})
        else:
            failed.append({'name': p['name'], 'error': r.get('error')})

    print(json.dumps({'dry_run': a.dry_run, 'created': created, 'updated_existing': updated,
                      'failed': failed, 'skipped_no_send_record': skipped}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
