#!/usr/bin/env python3
"""客戶職缺雲端表自動比對（2026-10-12 Jacky：遊戲橘子每週寄的需求表要自動比對，WSL2 跑）。

客戶（目前是遊戲橘子）每週把最新職缺放在同一個 Google 試算表，職缺關閉不另外通知。
這支每天讀一次那份表（有連結就能讀的公開表），跟上次的快照、跟我們 jobs 表比：
  🆕 表上有、我們沒上架的職缺
  ✏️ 表上同一個職缺的薪資／班別／條件文字變了
  ⚠️ 我們還開著、但表上已經沒有的職缺（可能已關閉，要問客戶）
有變化才推 TG（客戶群組「職缺」主題）；沒變化安靜。每週一固定推一次總覽（就算沒變化）。

設定與快照存在 D1 client_sheet_watch（一個客戶一列）：
  sheet_id、gids（逗號分隔分頁）、exclude_regex（我們的職缺標題符合就不算「表上沒有」，例如技術職不在這份表）
  snapshot_json（上次讀到的列）、checked_at、notified_at

用法：
    python3 client_sheet_watch.py            # 跑一輪（WSL2 timer 每天 15:00）
    python3 client_sheet_watch.py --dry      # 只印不推 TG、不存快照
    python3 client_sheet_watch.py --weekly   # 強制推總覽
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402
from tg_route import route  # noqa: E402

TITLE_KEYS = ('職位名稱', '職務名稱', '職缺名稱')
SKIP_KEYS = ('工作內容',)   # 長文字只算雜湊，不逐字顯示


def q(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def ensure_table():
    d1_http.query("""CREATE TABLE IF NOT EXISTS client_sheet_watch (
        client_id TEXT PRIMARY KEY, label TEXT, sheet_id TEXT NOT NULL, gids TEXT,
        exclude_regex TEXT, snapshot_json TEXT, checked_at TEXT, notified_at TEXT, is_active INTEGER DEFAULT 1)""")
    d1_http.query("INSERT OR IGNORE INTO client_sheet_watch(client_id,label,sheet_id,gids,exclude_regex) VALUES("
                  "'co_a732b59e','遊戲橘子','1UXyQKuZlm5n3Hbl2-dB759qjSMfjXXzeyjaQxHwqZD4','1196523559,1661779897',"
                  "'工程師|開發|Engineer|Developer')")


def fetch_tab(sheet_id, gid):
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 step1ne-sheet-watch'})
    raw = urllib.request.urlopen(req, timeout=60).read().decode('utf-8', 'replace')
    if raw.lstrip().startswith('<'):
        raise RuntimeError(f'分頁 {gid} 讀不到（可能權限改成不公開）')
    rows = [r for r in csv.reader(io.StringIO(raw)) if any(c.strip() for c in r)]
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        d = {head[i]: (r[i].strip() if i < len(r) else '') for i in range(len(head)) if head[i]}
        title = next((d[k] for k in TITLE_KEYS if d.get(k)), '')
        if not title:
            continue
        title = re.split(r'[\n(（]', title)[0].strip()
        unit = d.get('公司部門') or d.get('公司別') or ''
        out.append({'key': f'{unit}|{title}', 'title': title, 'unit': unit, 'fields': d})
    return out


def norm(s):
    return re.sub(r'[\s（）()・／/｜|－\-]', '', s or '')


def short(d):
    keep = []
    for k, v in d.items():
        if k in SKIP_KEYS or k in TITLE_KEYS:
            continue
        if v:
            keep.append(f'{k}：{v.splitlines()[0][:40]}')
    return '；'.join(keep[:4])


def fingerprint(d):
    return {k: (hashlib.md5(v.encode()).hexdigest()[:10] if k in SKIP_KEYS else v) for k, v in d.items()}


def tg(text, dry):
    if dry:
        print('[dry TG]\n' + text)
        return
    chat, thread = route('client_jobs')
    env = {}
    for line in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')):
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            env[k] = v.strip().strip('"').strip("'")
    data = {'chat_id': chat or env.get('TG_CHAT_ID'), 'text': text[:4000], 'disable_web_page_preview': 'true'}
    if thread:
        data['message_thread_id'] = thread
    urllib.request.urlopen(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                           data=urllib.parse.urlencode(data).encode(), timeout=20)


def run_one(w, dry, weekly):
    rows = []
    for gid in [g.strip() for g in (w.get('gids') or '0').split(',') if g.strip()]:
        rows += fetch_tab(w['sheet_id'], gid)
    seen = {}
    for r in rows:   # 同一職缺出現在兩個分頁（第一頁常是重複）只留一筆
        seen.setdefault(r['key'], r)
    rows = list(seen.values())
    prev = json.loads(w.get('snapshot_json') or '{}')
    cur = {r['key']: fingerprint(r['fields']) for r in rows}

    jobs = d1_http.query(
        "SELECT slug, title FROM jobs WHERE status != 'closed' AND "
        f"(company_id={q(w['client_id'])} OR client_name IN "
        f"(SELECT display_name FROM client_companies WHERE id={q(w['client_id'])}))")['results']
    ex = re.compile(w['exclude_regex']) if w.get('exclude_regex') else None

    def match(title):
        t = norm(title)
        return [j for j in jobs if t and (t in norm(j['title']) or norm(j['title']).startswith(t))]

    new, changed, gone = [], [], []
    for r in rows:
        m = match(r['title'])
        if not m:
            new.append(r)
        p = prev.get(r['key'])
        if p is not None and p != cur[r['key']]:
            diffs = [k for k in set(p) | set(cur[r['key']]) if p.get(k) != cur[r['key']].get(k)]
            changed.append((r, diffs, m))
    sheet_titles = [norm(r['title']) for r in rows]
    for j in jobs:
        if ex and ex.search(j['title']):
            continue
        nj = norm(j['title'])
        if not any(t and (t in nj or nj.startswith(t)) for t in sheet_titles):
            gone.append(j)

    today = datetime.date.today()
    is_monday = today.weekday() == 0
    lines = [f"📋 {w['label']} 職缺表比對（{today.strftime('%m/%d')}）", f"表上共 {len(rows)} 個職缺"]
    if new:
        lines.append('\n🆕 表上有、我們還沒上架：')
        lines += [f"・{r['unit']} {r['title']}（{short(r['fields'])}）" for r in new]
    if changed:
        lines.append('\n✏️ 內容有改：')
        lines += [f"・{r['title']}：改了「{'、'.join(d)}」" + (f"→ 我們的 {m[0]['slug']} 要不要跟著改" if m else '')
                  for r, d, m in changed]
    if gone:
        lines.append('\n⚠️ 我們還開著、表上已經沒有（可能關了，要問客戶）：')
        lines += [f"・{j['title']}（{j['slug']}）" for j in gone]
    if not (new or changed or gone):
        lines.append('跟我們系統一致，沒有要處理的。')
    lines.append(f"\n表：https://docs.google.com/spreadsheets/d/{w['sheet_id']}")

    # 推播條件：內容有改、或「新／消失」名單跟上次推的不一樣、或週一總覽
    sig = hashlib.md5(json.dumps([sorted(r['key'] for r in new), sorted(j['slug'] for j in gone)]).encode()).hexdigest()
    last_sig = (w.get('notified_at') or '').split('|')[-1]
    push = weekly or is_monday or bool(changed) or sig != last_sig
    print('\n'.join(lines))
    if push:
        tg('\n'.join(lines), dry)
    if not dry:
        d1_http.query(
            f"UPDATE client_sheet_watch SET snapshot_json={q(json.dumps(cur, ensure_ascii=False))}, "
            f"checked_at=datetime('now','+8 hours')"
            + (f", notified_at={q(datetime.datetime.now().strftime('%Y-%m-%d %H:%M') + '|' + sig)}" if push else '')
            + f" WHERE client_id={q(w['client_id'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--weekly', action='store_true')
    a = ap.parse_args()
    ensure_table()
    for w in d1_http.query("SELECT * FROM client_sheet_watch WHERE is_active=1")['results']:
        try:
            run_one(w, a.dry, a.weekly)
        except Exception as e:
            msg = f"⛔ {w.get('label')} 職缺表讀取失敗：{str(e)[:200]}"
            print(msg)
            tg(msg, a.dry)


if __name__ == '__main__':
    main()
