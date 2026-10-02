#!/usr/bin/env python3
"""客戶需求表巡檢：比對「這次看到的」跟「上次看到的」，有變化就推 TG。

2026-10-01 Jacky 交辦。遊戲橘子的用人需求放在一份 Google 試算表，客戶不另外通知，
而是直接改表：**不招的缺會被隱藏（整個分頁或某幾列），新的缺直接加列**。
我們 3/19 之後就沒再打開過，結果 4 個早就不招的缺還開在系統上、4 個新缺沒上架。

⚠️ 一定要用瀏覽器「人眼看到的畫面」來判斷，不能用 Drive 讀檔：
   Drive 讀檔會把被隱藏的列一起讀出來，等於把不招的缺當成在招。
   所以讀表那一段交給 Windows 那台的 Claude 用 Chrome 做（prompts/client_sheet_watch.md），
   這支只負責：存快照、跟上次比、對照我們系統的職缺、推 TG。

輸入（Claude 用 Chrome 讀完存成 JSON）：
  {
    "sheet_id": "1UXy…",
    "client_company_id": "co_a732b59e",
    "tabs": [
      {"name": "優服客服", "rows": [
          {"row": 2, "職位名稱": "餐飲品牌客服", "辦公室": "中和", "…": "…（每一欄完整內容）"}
      ]}
    ],
    "hidden_tabs": ["橘子支付", "雲力"]
  }
  只放「看得到的分頁」裡「看得到的列」。被隱藏的分頁只列名字。

用法：
  python3 client_sheet_watch.py --file 'C:/Users/.../Temp/sheet.json' --dry-run
  python3 client_sheet_watch.py --file 'C:/Users/.../Temp/sheet.json' --notify
  --dry-run：只印出會發什麼，不存快照、不發 TG
  --notify ：存快照；有變化（或第一次）才發 TG 到「🍊 客戶需求表異動」主題

這支**不會自動關職缺、不會自動新增職缺**——那是 Jacky 決定的事，TG 上只列出建議。
"""
import datetime
import json
import os
import re
import sys
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1q  # noqa: E402
from save_104 import win_to_wsl  # noqa: E402

TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
TOPIC_KEY = 'client_sheet_watch'
TOPIC_NAME = '🍊 客戶需求表異動'
TITLE_KEYS = ('職位名稱', '職缺名稱', '職稱')


def rows(sql, params=None):
    return d1q._post(sql, params).get('results') or []


def run(sql, params=None):
    return d1q._post(sql, params)


def load(argv):
    src = argv[argv.index('--file') + 1] if '--file' in argv else '-'
    text = sys.stdin.read() if src == '-' else open(win_to_wsl(src), encoding='utf-8-sig').read()
    return json.loads(text)


def _norm(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()


def job_key(tab, r):
    """同一個缺的識別：分頁＋職位名稱（列號會因為客戶插列而變，不能拿來當 key）。"""
    title = next((r.get(k) for k in TITLE_KEYS if r.get(k)), '') or f"第{r.get('row')}列"
    return f"{tab}｜{_norm(title).split(' (')[0].split('(')[0]}"


def flatten(snap):
    out = {}
    for t in snap.get('tabs') or []:
        for r in t.get('rows') or []:
            k = job_key(t['name'], r)
            out[k] = {kk: _norm(vv) for kk, vv in r.items() if kk != 'row'}
    return out


def diff(prev, cur):
    a, b = flatten(prev), flatten(cur)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = []
    for k in sorted(set(a) & set(b)):
        fields = [f for f in sorted(set(a[k]) | set(b[k])) if a[k].get(f, '') != b[k].get(f, '')]
        if fields:
            changed.append((k, fields))
    tabs_hidden = sorted(set(cur.get('hidden_tabs') or []) - set(prev.get('hidden_tabs') or []))
    tabs_shown = sorted(set(prev.get('hidden_tabs') or []) - set(cur.get('hidden_tabs') or []))
    return added, removed, changed, tabs_hidden, tabs_shown


def our_open_jobs(company_id):
    name = (rows("SELECT display_name FROM client_companies WHERE id=?", [company_id]) or [{}])[0].get('display_name') or ''
    base = re.sub(r'(集團|股份有限公司|有限公司)$', '', name)
    return rows("SELECT slug, title FROM jobs WHERE status IN ('open','active') AND slug!='unspecified' "
                "AND (client_name LIKE ? OR client_name LIKE ?) ORDER BY title",
                [f'%{base}%', f'%{name}%'])


def build_text(cur, prev, d, jobs):
    added, removed, changed, tabs_hidden, tabs_shown = d
    n_visible = sum(len(t.get('rows') or []) for t in cur.get('tabs') or [])
    lines = [f"🍊 遊戲橘子需求表巡檢（{datetime.datetime.now():%m/%d %H:%M}）",
             f"目前看得到的缺：{n_visible} 個；被隱藏的分頁：{len(cur.get('hidden_tabs') or [])} 個"]
    if prev is None:
        lines.append('\n（第一次建立基準，下次起只回報變化）')
        lines.append('\n看得到的缺：')
        lines += [f'・{k}' for k in sorted(flatten(cur))]
    else:
        if not any(d):
            lines.append('\n沒有變化。')
        if added:
            lines.append('\n🆕 新出現的缺（建議上架）：')
            lines += [f'・{k}' for k in added]
        if removed:
            lines.append('\n🙈 不見了／被隱藏的缺（建議關閉對應職缺）：')
            lines += [f'・{k}' for k in removed]
        if changed:
            lines.append('\n✏️ 內容有改的缺：')
            lines += [f'・{k}：改了 {"、".join(f)}' for k, f in changed]
        if tabs_hidden:
            lines.append('\n整個分頁被隱藏：' + '、'.join(tabs_hidden))
        if tabs_shown:
            lines.append('\n整個分頁重新出現：' + '、'.join(tabs_shown))
    lines.append(f'\n我們系統上架中的遊戲橘子職缺（{len(jobs)} 個）：')
    lines += [f'・{j["title"]}' for j in jobs]
    lines.append('\n⚠️ 系統不會自動關閉或上架，要動哪個請跟總指揮說。')
    return '\n'.join(lines)


def topic_id():
    r = rows("SELECT topic_id FROM bot_topics WHERE key=?", [TOPIC_KEY])
    if r:
        return r[0]['topic_id']
    env = _tg_env()
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/createForumTopic",
                                 data=json.dumps({'chat_id': env['TG_CHAT_ID'], 'name': TOPIC_NAME}).encode(),
                                 headers={'Content-Type': 'application/json'})
    tid = json.load(urllib.request.urlopen(req, timeout=20))['result']['message_thread_id']
    run("INSERT INTO bot_topics (key, topic_id) VALUES (?,?)", [TOPIC_KEY, tid])
    return tid


def _tg_env():
    env = {}
    for ln in open(TG_ENV, encoding='utf-8'):
        k, _, v = ln.strip().partition('=')
        if k and not k.startswith('#'):
            env[k] = v.strip('"\'')
    return env


def send_tg(text):
    env = _tg_env()
    chat, thread = env['TG_CHAT_ID'], None
    try:   # 2026-10-02：搬到「step1ne客戶」群組 🍊 客戶需求表；沒設照舊
        sys.path.insert(0, os.path.dirname(HERE))
        import tg_route
        _c, _t = tg_route.route('client_sheet')
        if _c and _t:
            chat, thread = _c, _t
    except Exception:
        pass
    body = {'chat_id': chat, 'text': text[:4000], 'message_thread_id': thread or topic_id()}
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=20)).get('ok')


def main(argv):
    if '-h' in argv or '--help' in argv:
        print(__doc__)
        return
    os.environ.setdefault('TZ', 'Asia/Taipei')
    cur = load(argv)
    sid = cur.get('sheet_id') or ''
    if not sid or not cur.get('tabs'):
        sys.exit('❌ JSON 缺 sheet_id 或 tabs，可能沒讀到表，不存快照')
    last = rows("SELECT snapshot_json FROM client_sheet_snapshots WHERE sheet_id=? ORDER BY taken_at DESC LIMIT 1", [sid])
    prev = json.loads(last[0]['snapshot_json']) if last else None
    d = diff(prev, cur) if prev else ([], [], [], [], [])
    text = build_text(cur, prev, d, our_open_jobs(cur.get('client_company_id') or 'co_a732b59e'))
    print(text)
    if '--dry-run' in argv:
        print('\n（--dry-run：沒有存快照、沒有發 TG）')
        return
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run("INSERT INTO client_sheet_snapshots (id, sheet_id, client_company_id, taken_at, snapshot_json, diff_text, created_at) "
        "VALUES (?,?,?,?,?,?,?)", [str(uuid.uuid4()), sid, cur.get('client_company_id'), now,
                                   json.dumps(cur, ensure_ascii=False), text, now])
    if '--notify' in argv and (prev is None or any(d)):
        print('TG 發送：', send_tg(text))
    elif '--notify' in argv:
        print('（沒有變化，不發 TG）')


if __name__ == '__main__':
    main(sys.argv)
