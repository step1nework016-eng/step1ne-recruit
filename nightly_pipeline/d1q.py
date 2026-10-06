#!/usr/bin/env python3
"""夜間工作專用的資料庫小工具——只能「查」和「新增」，其他一律擋掉。

給 claude -p 夜間工作呼叫，讓它不用自己拼 SQL 字串、也不會誤刪資料：

  python3 d1q.py q "SELECT ..."                 # 查詢，印出 JSON 陣列
  python3 d1q.py insert <表名> '<JSON 物件或陣列>'   # 新增一筆或多筆
  python3 d1q.py insert-ignore <表名> '<JSON>'     # 同上，但主鍵重複就略過（bd_company_profiles 用）
  python3 d1q.py cols <表名>                      # 看欄位名
  JSON 很長或有引號時：用 '-' 從標準輸入讀（heredoc），或 '@檔案路徑'

規則（寫死在程式裡，不靠提示詞自律）：
- q 只接受 SELECT／WITH／PRAGMA table_info 開頭，而且不能含分號後面再接一段。
- insert 只能寫進 ALLOWED_TABLES 這幾張表；欄位名必須是表上真的有的欄位。
- 沒給 id 會自動補 uuid；表上有 created_at／updated_at／found_at 而沒給，會自動補台灣時間。
- 沒有 update／delete／drop／alter 的入口。

憑證走 step1ne-recruit/d1_http.py（~/.config/workflow-os/cf.env）。
"""
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D  # noqa: E402

ALLOWED_TABLES = {'bd_outreach', 'bd_company_profiles', 'bd_hr_contacts', 'sourced_candidates', 'bd_foreign_watch'}
TIME_COLS = ('created_at', 'updated_at', 'found_at')
READ_OK = re.compile(r'^\s*(SELECT|WITH|PRAGMA\s+table_info)\b', re.I)
WRITE_WORDS = re.compile(r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|VACUUM)\b', re.I)


def _post(sql, params=None, timeout=60):
    tok, acc = D._cfg()
    if not (tok and acc):
        raise SystemExit('❌ cf.env 裡沒有 CLOUDFLARE_API_TOKEN／ACCOUNT_ID')
    body = {'sql': sql}
    if params is not None:
        body['params'] = params
    req = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{D.DB_ID}/query',
        data=json.dumps(body).encode('utf-8'),
        headers={'authorization': f'Bearer {tok}', 'content-type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise SystemExit(f'❌ D1 HTTP {e.code}: {e.read()[:300]!r}')
    if not res.get('success'):
        raise SystemExit(f'❌ D1 錯誤：{json.dumps(res.get("errors"), ensure_ascii=False)[:300]}')
    return (res.get('result') or [{}])[0]


def _strip_strings(sql):
    """把字串常數拿掉再檢查關鍵字，避免 WHERE note LIKE '%update%' 被誤擋。"""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def cmd_q(sql):
    bare = _strip_strings(sql)
    if not READ_OK.match(sql) or ';' in bare.rstrip().rstrip(';') or WRITE_WORDS.search(bare):
        raise SystemExit('❌ q 只能跑單一段 SELECT／WITH／PRAGMA table_info。要新增請用 insert。')
    print(json.dumps(_post(sql).get('results') or [], ensure_ascii=False, indent=1))


def _cols(table):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
        raise SystemExit('❌ 表名格式不對')
    rows = _post(f'PRAGMA table_info({table})').get('results') or []
    return [r['name'] for r in rows]


def cmd_insert(table, payload, ignore=False):
    if table not in ALLOWED_TABLES:
        raise SystemExit(f'❌ 夜間工作只能新增到：{", ".join(sorted(ALLOWED_TABLES))}')
    if payload == '-':
        payload = sys.stdin.read()
    elif payload.startswith('@'):
        payload = open(payload[1:], encoding='utf-8').read()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise SystemExit(f'❌ JSON 格式錯：{e}')
    rows = data if isinstance(data, list) else [data]
    # 2026-10-01：夜間名單把「台灣美光記憶體」放進開發名單——美光是律准科技的終端客戶。
    # 原本只靠提示詞叫 AI 自己比對客戶名單，AI 漏看就漏了。改成寫入前由程式擋：
    # 命中已簽約／洽談中／終端客戶／禁止接觸的公司，這一筆直接不寫，其他照寫。
    if table == 'bd_outreach':
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'jobintake'))
        import client_guard as G  # noqa: E402
        clients = G.load_clients(lambda sql: _post(sql).get('results') or [])
        kept = []
        for row in rows:
            hit = G.check((row or {}).get('company') if isinstance(row, dict) else '', clients)
            if hit and hit.get('verdict') == 'block':
                print(f"⛔ 不寫入：{hit['company']} 對到客戶名單「{hit['matched']}」——{hit['why']}")
                continue
            kept.append(row)
        rows = kept
        if not rows:
            print('（這批全部被客戶名單擋下，沒有寫入任何一筆）')
            return
    # 2026-10-06：夜間找人把 Medtecs Group（＝美德醫療，就是這個職缺的客戶）的副總
    # 排成美德營運發展主管的 A 級人選。從客戶自己公司挖人是獵頭大忌——改成寫入前由程式擋：
    # 現職公司或職稱裡對到任何已簽約／洽談中／終端客戶／禁止接觸的公司，這一筆不寫。
    if table == 'sourced_candidates':
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'jobintake'))
        import client_guard as G  # noqa: E402
        clients = G.load_clients(lambda sql: _post(sql).get('results') or [])
        kept = []
        for row in rows:
            r = row if isinstance(row, dict) else {}
            hit = None
            for field in ('company', 'headline'):
                h = G.check(str(r.get(field) or ''), clients, strict=True) if r.get(field) else None
                if h and h.get('verdict') == 'block':
                    hit = h
                    break
            if hit:
                print(f"⛔ 不寫入人選 {r.get('name')}：現職對到客戶名單「{hit['matched']}」——不能從客戶公司挖人")
                continue
            kept.append(row)
        rows = kept
        if not rows:
            print('（這批人選全部是客戶公司的人，沒有寫入任何一筆）')
            return
    cols = set(_cols(table))
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    done = 0
    for row in rows:
        if not isinstance(row, dict) or not row:
            raise SystemExit('❌ 每一筆都要是 JSON 物件')
        bad = [k for k in row if k not in cols]
        if bad:
            raise SystemExit(f'❌ {table} 沒有這些欄位：{bad}（不准新增或改欄位名；先用 cols 看）')
        row = dict(row)
        if 'id' in cols and table != 'bd_company_profiles' and not row.get('id'):
            row['id'] = str(uuid.uuid4())
        for c in TIME_COLS:
            if c in cols and not row.get(c):
                row[c] = now
        for k, v in list(row.items()):
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v, ensure_ascii=False)
        keys = list(row)
        verb = 'INSERT OR IGNORE' if ignore else 'INSERT'
        sql = f'{verb} INTO {table} ({", ".join(keys)}) VALUES ({", ".join("?" for _ in keys)})'
        meta = _post(sql, [row[k] for k in keys]).get('meta') or {}
        done += int(meta.get('changes') or 0)
        print(json.dumps({'table': table, 'id': row.get('id') or row.get('company'),
                          'changes': meta.get('changes')}, ensure_ascii=False))
    print(f'✅ {table} 新增 {done} 筆')


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    op = argv[1]
    if op == 'q':
        cmd_q(argv[2])
    elif op == 'cols':
        print(json.dumps(_cols(argv[2]), ensure_ascii=False))
    elif op in ('insert', 'insert-ignore') and len(argv) >= 4:
        cmd_insert(argv[2], argv[3], ignore=(op == 'insert-ignore'))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
