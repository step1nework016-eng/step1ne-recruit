#!/usr/bin/env python3
"""把早上用 Chrome 在 104 查到的東西寫回開發名單。

給 Windows 那台 Claude 桌面版的 07:00 排程任務用（prompts/chrome_104_morning.md）。

輸入：一個 JSON 陣列（檔案路徑、Windows 路徑、或 '-' 從標準輸入讀），每家一筆：
  {
    "company": "正式名稱（要跟 cards_need_104.py 給的一字不差）",
    "status": "ok" | "captcha" | "not_found" | "error",
    "note": "卡住的原因（status 不是 ok 時寫）",
    "company_104_url": "https://www.104.com.tw/company/xxxx",
    "contact_name": "104 公司頁上的聯絡人（例：人才招募部 王小姐）",
    "contact_phone": "104 公司頁上的電話",
    "jobs_total": "99+ 或 數字（104 公司頁上寫的現開職缺數）",
    "categories": ["把前 20 個職缺歸成幾類，一類一行：職稱群 — 地點，條件", "..."]
  }

逐家寫回（只補空的，不蓋掉顧問已經填的東西）：
  - bd_outreach.company_104_url：空的才補
  - bd_outreach.hr_phone／hr_contact：空的才補；phone_source 註明「104 公司頁」；call_phone 空的才補
  - 原本「只缺 104 企業頁」而沒指派的公司，補到 104 之後三項就齊了 → 依產業指派（Jacky 五大類 → Jacky，其他 → Phoebe）
  - bd_company_profiles.hiring 最前面加一段「104 現開 N 個職缺（M/D 查，以下是前 20 個的分類）：…」
    （格式照「世界先進積體電路股份有限公司」那筆；前一天加過的那段會換成新的，不會一直疊）

用法：
  python3 save_104.py 檔案.json [--dry-run] [--notify]
  python3 save_104.py --file 'C:\\Users\\jack\\AppData\\Local\\Temp\\step1ne_104.json' --notify
  cat x.json | python3 save_104.py - --notify
  --dry-run：只印出會改什麼、TG 會發什麼，不寫資料庫、不發 TG
  --notify ：寫完發一則 TG 摘要到「📬 開發信 開信・回信」主題（bot_topics.bd_signals）
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import d1q  # noqa: E402  （用它的參數化查詢，避免公司名裡的引號出事）

SKIP_STATUS = "('rejected','superseded','draft','blocked','invalid')"
JACKY_SECTORS = ('半導體', '電子', '能源', '軟體', '網路', '生技', '醫療', '金融', '財會')
TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
HEAD_RE = re.compile(r'^104 (現開|目前沒有開缺)')


def run(sql, params=None):
    return d1q._post(sql, params)


def rows(sql, params=None):
    return run(sql, params).get('results') or []


def win_to_wsl(path):
    """C:\\Users\\... → /mnt/c/Users/...（先試 wslpath，沒有就自己換）。"""
    if not re.match(r'^[A-Za-z]:[\\/]', path):
        return path
    try:
        return subprocess.check_output(['wslpath', '-u', path], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return '/mnt/' + path[0].lower() + path[2:].replace('\\', '/')


def load_input(argv):
    src = None
    if '--file' in argv:
        src = argv[argv.index('--file') + 1]
    else:
        pos = [a for a in argv[1:] if not a.startswith('--')]
        src = pos[0] if pos else '-'
    if src == '-':
        text = sys.stdin.read()
    else:
        with open(win_to_wsl(src), encoding='utf-8-sig') as f:
            text = f.read()
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get('items') or [data]
    return data


def hiring_block(item, today_md):
    total = str(item.get('jobs_total') or '').strip()
    cats = [c.strip() for c in (item.get('categories') or []) if str(c).strip()]
    if total in ('0', '') and not cats:
        head = f'104 目前沒有開缺（{today_md} 查）'
    else:
        head = f'104 現開 {total or "?"} 個職缺（{today_md} 查，以下是前 20 個的分類）：'
    lines = [head] + cats
    name, phone = (item.get('contact_name') or '').strip(), (item.get('contact_phone') or '').strip()
    if name or phone:
        lines.append(f'招募聯絡：{" ".join(x for x in (name, phone) if x)}（104 公司頁）')
    return '\n'.join(lines)


def merge_hiring(old, block):
    old = old or ''
    if HEAD_RE.match(old) and '\n\n' in old:     # 這支程式前一天加過的那段（用空行隔開）→ 換成今天的
        old = old.split('\n\n', 1)[1]
    elif HEAD_RE.match(old) and not old.strip():
        old = ''
    # 其他情況（例如人工寫的舊 104 資料，後面還接著推測需求）整段保留在下面，不刪任何字
    return block + ('\n\n' + old if old.strip() else '')


def process(item, dry, today_md, stats):
    co = (item.get('company') or '').strip()
    st = (item.get('status') or 'ok').strip()
    if not co:
        stats['stuck'].append('（沒有公司名的一筆）')
        return
    if st != 'ok':
        stats['stuck'].append(f"{co}：{ {'captcha': '人機驗證', 'not_found': '104 找不到', 'error': '出錯'}.get(st, st) }"
                              + (f"（{item.get('note')}）" if item.get('note') else ''))
        return
    outs = rows(f"SELECT id, company_104_url, hr_phone, hr_contact, call_phone, phone_source, assigned_to, guard_json, industry "
                f"FROM bd_outreach WHERE company=? AND channel='phone' AND status NOT IN {SKIP_STATUS}", [co])
    if not outs:
        stats['stuck'].append(f'{co}：名單裡找不到這個公司名（名字要一字不差）')
        return
    stats['done'] += 1
    url = (item.get('company_104_url') or '').strip()
    if url and '104.com.tw/company/' not in url:
        print(f'  ⚠️ {co}：104 網址格式不對，不寫：{url}')
        url = ''
    phone = (item.get('contact_phone') or '').strip()
    cname = (item.get('contact_name') or '').strip()
    src_note = f'104 公司頁 {url or outs[0].get("company_104_url") or ""}'.strip()
    acts = []
    got_url = got_hr = False
    for o in outs:
        sets, params = [], []
        if url and not (o.get('company_104_url') or '').strip():
            sets.append('company_104_url=?'); params.append(url); acts.append('補 104 企業頁')
            got_url = True
        if phone and not (o.get('hr_phone') or '').strip():
            sets.append('hr_phone=?'); params.append(phone)
            ps = (o.get('phone_source') or '').strip()
            sets.append('phone_source=?'); params.append(f'{ps}；人資電話：{src_note}' if ps else f'人資電話：{src_note}')
            if not (o.get('call_phone') or '').strip():
                sets.append('call_phone=?'); params.append(phone)
            acts.append(f'補招募電話 {phone}')
            got_hr = True
        if cname and not (o.get('hr_contact') or '').strip():
            sets.append('hr_contact=?'); params.append(cname); acts.append(f'補招募窗口 {cname}')
        # 原本只缺 104 → 補到之後就合格，依產業指派
        if not o.get('assigned_to') and url:
            try:
                g = json.loads(o.get('guard_json') or '{}') or {}
            except json.JSONDecodeError:
                g = {}
            miss = g.get('missing')
            if isinstance(miss, list) and miss and all('104' in str(m) for m in miss):
                sector = f"{g.get('sector') or ''} {o.get('industry') or ''}"
                who = 'Jacky' if any(k in sector for k in JACKY_SECTORS) else 'Phoebe'
                g.update({'qualified': True, 'missing': [], 'qualified_by': f'104 晨間補齊 {today_md}'})
                sets += ['assigned_to=?', 'guard_json=?']; params += [who, json.dumps(g, ensure_ascii=False)]
                acts.append(f'補齊三項，指派給 {who}')
                if f'{co}→{who}' not in stats['assigned']:
                    stats['assigned'].append(f'{co}→{who}')
        if sets:
            sets.append('updated_at=?'); params.append(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            sql = f"UPDATE bd_outreach SET {', '.join(sets)} WHERE id=?"
            if not dry:
                run(sql, params + [o['id']])
    stats['url'] += int(got_url)
    stats['hr'] += int(got_hr)
    # 認識客戶：hiring 最前面加 104 那段
    block = hiring_block(item, today_md)
    prof = rows('SELECT hiring FROM bd_company_profiles WHERE company=?', [co])
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if prof:
        new = merge_hiring(prof[0].get('hiring'), block)
        if not dry:
            run('UPDATE bd_company_profiles SET hiring=?, updated_at=? WHERE company=?', [new, now, co])
    else:
        if not dry:
            run('INSERT INTO bd_company_profiles (company, hiring, sources, updated_by, updated_at) VALUES (?,?,?,?,?)',
                [co, block, src_note, 'Chrome104晨間', now])
    acts.append('認識客戶加上 104 現開職缺')
    stats['hiring'] += 1
    print(f"{'［試跑］' if dry else '✅'} {co}：{'、'.join(dict.fromkeys(acts))}")
    if dry:
        print('    ' + block.replace('\n', '\n    '))


def notify_text(stats, n_items, today_md):
    lines = [f'🔎 104 晨間補資料｜{today_md}',
             f'查了 {n_items} 家，寫回 {stats["done"]} 家',
             f'補到招募窗口電話 {stats["hr"]} 家、補到 104 企業頁 {stats["url"]} 家、更新現開職缺 {stats["hiring"]} 家']
    if stats['assigned']:
        lines.append('補齊三項、新指派：' + '、'.join(stats['assigned']))
    if stats['stuck']:
        lines.append(f'卡住 {len(stats["stuck"])} 家：')
        lines += [f'・{s}' for s in stats['stuck']]
    return '\n'.join(lines)


def send_tg(text):
    env = {}
    with open(TG_ENV, encoding='utf-8') as fh:
        for ln in fh:
            k, _, v = ln.strip().partition('=')
            if k and not k.startswith('#'):
                env[k] = v.strip('"\'')
    topic = (rows("SELECT topic_id FROM bot_topics WHERE key='bd_signals'") or [{}])[0].get('topic_id')
    chat = env['TG_CHAT_ID']
    try:   # 2026-10-02：搬到「step1ne客戶」群組 ☎️ 電訪・日報；沒設照舊
        import tg_route
        _c, _t = tg_route.route('client_calls')
        if _c and _t:
            chat, topic = _c, _t
    except Exception:
        pass
    body = {'chat_id': chat, 'text': text}
    if topic:
        body['message_thread_id'] = topic
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=20)).get('ok')


def main(argv):
    if '-h' in argv or '--help' in argv:
        print(__doc__)
        return
    os.environ.setdefault('TZ', 'Asia/Taipei')
    try:
        import time
        time.tzset()
    except AttributeError:
        pass
    dry = '--dry-run' in argv
    items = load_input(argv)
    now = datetime.datetime.now()
    today_md = f'{now.month}/{now.day}'
    stats = {'done': 0, 'hr': 0, 'url': 0, 'hiring': 0, 'assigned': [], 'stuck': []}
    for it in items:
        process(it, dry, today_md, stats)
    text = notify_text(stats, len(items), today_md)
    print('\n' + text)
    if '--notify' in argv:
        if dry:
            print('\n（--dry-run：TG 沒有發送）')
        else:
            print('TG 發送：', send_tg(text))


if __name__ == '__main__':
    main(sys.argv)
