#!/usr/bin/env python3
"""夜間工作專用的資料庫小工具——只能「查」和「新增」，其他一律擋掉。

給 claude -p 夜間工作呼叫，讓它不用自己拼 SQL 字串、也不會誤刪資料：

  python3 d1q.py q "SELECT ..."                 # 查詢，印出 JSON 陣列
  python3 d1q.py insert <表名> '<JSON 物件或陣列>'   # 新增一筆或多筆
  python3 d1q.py insert-ignore <表名> '<JSON>'     # 同上，但主鍵重複就略過（bd_company_profiles 用）
  python3 d1q.py cols <表名>                      # 看欄位名
  python3 d1q.py conds <職缺代號>                  # 這個職缺的必要條件編號清單（找人逐條對照用）
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


# E19（2026-10-09）：夜間找人一天三次，要知道每一輪讀寫了多少列。D1Q_STATS_FILE 有設才記，沒設完全不動作。
# 每個 d1q 行程結束時 append 一行（累計這個行程的 rows_read／rows_written）；run_nightly.sh 依 D1Q_RUN_ID 加總。
_STATS = {'calls': 0, 'rows_read': 0, 'rows_written': 0}


def _flush_stats():
    path = os.environ.get('D1Q_STATS_FILE')
    if not path or not _STATS['calls']:
        return
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'run': os.environ.get('D1Q_RUN_ID', ''),
                                'cmd': sys.argv[1] if len(sys.argv) > 1 else '', **_STATS}, ensure_ascii=False) + '\n')
    except OSError:
        pass


import atexit  # noqa: E402
atexit.register(_flush_stats)


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
    out = (res.get('result') or [{}])[0]
    m = out.get('meta') or {}
    _STATS['calls'] += 1
    _STATS['rows_read'] += int(m.get('rows_read') or 0)
    _STATS['rows_written'] += int(m.get('rows_written') or 0)
    return out


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


GRADE_KEYS = ('must_check', 'function_match', 'industry_required', 'industry_match', 'grade_reason', 'profile_url')
NO_PROFILE_LOG = os.path.join(os.path.dirname(HERE), 'sourcing_runs', 'no_profile_leads.jsonl')


def _claimed_grade(r):
    """AI 自己評的等第（沒給 grade 就看分數），跟 sourcing_quality.enforce_grade 的算法一致。"""
    g = str(r.get('grade') or '').strip().upper()[:1]
    if g and g in 'ABCD':
        return g
    try:
        s = int(float(r.get('fit_score') if r.get('fit_score') is not None else r.get('score') or 0))
    except (TypeError, ValueError):
        s = 0
    return 'A' if s >= 80 else 'B' if s >= 60 else 'C' if s >= 40 else 'D'


def _must_check_problems(r, conds):
    """E19（2026-10-09，Jacky 核准）：AI 自評 A／B 的人，must_check 要把職缺的每一條必要條件編號都填滿、
    status 只能是 met／unmet／unknown。缺編號或 status 亂寫，過去是靜默降成 C（10/9 培訓工程設計工程師就是漏填一條，
    兩位被降級，模型自己都不知道）——改成直接報錯，讓它補齊再寫。
    自評 C／D 的不查（本來就不該花時間寫進來）；職缺沒有必要條件（conds 空）也不查。沒問題回 None。"""
    if not conds or _claimed_grade(r) not in ('A', 'B'):
        return None
    checks = r.get('must_check')
    have = {}
    if isinstance(checks, list):
        for x in checks:
            try:
                have[int(x.get('no'))] = str(x.get('status') or '').strip().lower()
            except (TypeError, ValueError, AttributeError):
                continue
    missing = [c for c in conds if c['no'] not in have]
    bad_status = [(c, have[c['no']]) for c in conds if c['no'] in have and have[c['no']] not in ('met', 'unmet', 'unknown')]
    if not missing and not bad_status:
        return None
    return {'missing': missing, 'bad_status': bad_status, 'no_list': not isinstance(checks, list)}


def _must_check_error(bad_rows):
    lines = ['❌ 這次沒有寫入任何一筆：自評 A／B 的人選，must_check 沒有把職缺的必要條件逐條填完。',
             '   （缺編號不會再被靜默降級——請補齊後把同一筆重新 insert 一次。）']
    slugs = []
    for name, slug, pr in bad_rows:
        slugs.append(slug)
        lines.append(f'• 人選「{name}」（職缺 {slug}）：')
        if pr['no_list']:
            lines.append('    完全沒有附 must_check（要是 [{"no":編號,"status":"met|unmet|unknown","evidence":"…"}, …]）。')
        if pr['missing']:
            lines.append('    缺這幾條的編號：' + '、'.join(str(c['no']) for c in pr['missing']))
            for c in pr['missing']:
                lines.append(f"      {c['no']}. {c['text'][:80]}" + ('（只能電話確認，公開資料看不到就填 unknown）' if c['kind'] == 'phone' else ''))
        for c, st in pr['bad_status']:
            lines.append(f"    編號 {c['no']} 的 status 寫成「{st or '空白'}」，只能是 met／unmet／unknown")
    lines.append('→ 完整編號清單：' + '；'.join(f'python3 d1q.py conds {s}' for s in sorted(set(slugs))) +
                 '。公開資料看不到的條件填 unknown（沒寫到不是 unmet），**不要省略任何一條**。')
    return '\n'.join(lines)


def _sourced_gate(rows):
    """關卡一：沒有個人頁（只有新聞／公告／公司官網／名錄）→ 不寫入，記到本機線索檔。
    關卡二：用 AI 附的逐條對照（must_check 等欄位，這些不是資料表欄位，寫入前拿掉）算等第上限；
    沒附逐條對照的最高 C。A 仍然要 verify_status='verified' 才留得住（10/6 規則）。
    關卡二之前（10/9 加）：自評 A／B 的人 must_check 缺條件編號或 status 不合法 → 整批報錯、不寫入（見 _must_check_problems）。"""
    import sourcing_quality as Q  # noqa: E402（上層資料夾，已在 sys.path）
    jobs, kept, bad_rows = {}, [], []
    for r in rows:
        if not isinstance(r, dict):
            kept.append(r)
            continue
        if not Q.place_profile_links(r):
            print(f"🚫 不寫入人選 {r.get('name')}：沒有個人頁（只有 {str(r.get('source_url') or '無網址')[:70]}）——只記成線索")
            try:
                os.makedirs(os.path.dirname(NO_PROFILE_LOG), exist_ok=True)
                with open(NO_PROFILE_LOG, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                        'job_slug': r.get('job_slug'), 'source': r.get('source'), 'name': r.get('name'),
                                        'company': r.get('company'), 'headline': r.get('headline'),
                                        'source_url': r.get('source_url'), 'why': r.get('note')}, ensure_ascii=False) + '\n')
            except OSError:
                pass
            continue
        slug = r.get('job_slug') or ''
        if slug not in jobs:
            jobs[slug] = (_post('SELECT * FROM jobs WHERE slug=?', [slug]).get('results') or [{}])[0]
        job = jobs[slug]
        conds = Q.job_conditions(job)
        pr = _must_check_problems(r, conds)
        if pr:
            bad_rows.append((r.get('name') or '（沒名字）', slug, pr))
            continue
        g, score, reason, det = Q.enforce_grade(r, conds, verified=(r.get('verify_status') == 'verified'))
        if r.get('grade') and r.get('grade') != g:
            print(f"↘︎ {r.get('name')}：AI 評 {r.get('grade')}，逐條對照後 {g}（{'；'.join(det['why']) or '—'}）")
        old_note = str(r.get('note') or '')
        contact = (re.search(r'聯絡方式來源[:：][^｜\n]*', old_note) or [None])[0] if old_note else None
        r['note'] = Q.grade_note(r, g, det, tag=contact or '')
        r['grade'], r['score'] = g, score
        extra = {k: r.get(k) for k in GRADE_KEYS if k in r}
        extra.update({'grade_jd_fp': Q.job_fingerprint(job), 'grade_rule': 'must-check-v1', 'grade_detail': det,
                      'ai_note': old_note})
        try:
            base = json.loads(r['raw_json']) if isinstance(r.get('raw_json'), str) else (r.get('raw_json') or {})
        except ValueError:
            base = {'raw': r.get('raw_json')}
        base.update(extra)
        r['raw_json'] = base
        for k in GRADE_KEYS:
            r.pop(k, None)
        kept.append(r)
    if bad_rows:
        # 整批都不寫（一次報完所有問題，避免寫一半）；模型補齊後重新 insert
        raise SystemExit(_must_check_error(bad_rows))
    return kept


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
    if table == 'sourced_candidates':
        # 2026-10-08（Jacky 核准）：兩道關卡，規則在 step1ne-recruit/sourcing_quality.py，白天找人也用同一份
        rows = _sourced_gate(rows)
        if not rows:
            print('（這批人選都沒有個人頁，沒有寫入任何一筆）')
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
    elif op == 'conds':
        # 夜間找人逐條對照用：跟寫入時程式用的是同一份編號（sourcing_quality.job_conditions）
        import sourcing_quality as Q  # noqa: E402
        job = (_post('SELECT * FROM jobs WHERE slug=?', [argv[2]]).get('results') or [None])[0]
        if not job:
            raise SystemExit(f'❌ 找不到職缺 {argv[2]}')
        print(Q.conditions_prompt(Q.job_conditions(job)))
    elif op == 'cols':
        print(json.dumps(_cols(argv[2]), ensure_ascii=False))
    elif op in ('insert', 'insert-ignore') and len(argv) >= 4:
        cmd_insert(argv[2], argv[3], ignore=(op == 'insert-ignore'))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
