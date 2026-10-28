#!/usr/bin/env python3
"""social_thread_check.py — 串文完整性檢查（2026-10-28 Jacky「做」；起因 DR #1053、EYLISE #1056 第 4 則沒發出去）

為什麼有這支：Worker 的串文發文（src/index.js soc_approve → postOne 迴圈）每一則只拿到 Threads 回的 id 就當成功，
不會回頭確認那一則真的在串文裡；Threads 偶爾卡住某一則，資料庫卻早已寫成「已發」（10/21 #992、10/28 #1053／#1056）。
Worker 在 Mac 管、這台沒有部署權限，所以這裡先用「事後數則數」補一道眼睛：

  每 10 分鐘掃最近 24 小時狀態為 posted 的 Threads 貼文，
  1) 照 Worker 同一套規則算「這份草稿應該發成幾則」（標記切串＋480 字切＋第一則連結搬到最後一則）
  2) 用 Threads API 讀該貼文的 conversation，數「實際有幾則」
  3) 少了 → TG 私訊 Jacky（帶是哪個帳號、缺幾則、連結）；多了 → 提醒可能重複；後來補齊 → 再通知一次「已補齊」

只讀：不改任何 Threads 貼文、不補發、不改佇列。同一種狀態只通知一次（記憶檔），不會一直響。
貼文發出後 10 分鐘內不判（讓晚出現的那則有時間冒出來）。

用法：
  python3 social_thread_check.py          # 正常跑（會通知）
  python3 social_thread_check.py --dry    # 只印結果，不通知、不寫記憶檔
  python3 social_thread_check.py --hours 72 --dry
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

JACKY_TG = '8365775688'
STATE_PATH = os.path.expanduser('~/aijob-automation/state/social_thread_check.json')
GRACE_SEC = 600
FMT = '%Y-%m-%d %H:%M:%S'


def log(m):
    print(f'[{time.strftime("%m-%d %H:%M:%S")}] {m}', flush=True)


def tw_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None) + datetime.timedelta(hours=8)


# ── 照 Worker（src/index.js）同一套規則算應有則數 ─────────────────────────
def split_for_threads(text, max_len=480):
    paras = re.split(r'\n\s*\n', text)
    chunks, cur = [], ''

    def flush():
        nonlocal cur
        if cur:
            chunks.append(cur)
            cur = ''
    for para in paras:
        cand = cur + '\n\n' + para if cur else para
        if len(cand) <= max_len:
            cur = cand
            continue
        flush()
        if len(para) <= max_len:
            cur = para
            continue
        for line in para.split('\n'):
            c2 = cur + '\n' + line if cur else line
            if len(c2) <= max_len:
                cur = c2
                continue
            flush()
            if len(line) <= max_len:
                cur = line
                continue
            for i in range(0, len(line), max_len):
                chunks.append(line[i:i + max_len])
    flush()
    return chunks


def split_by_chinese_markers(text):
    lines = str(text).split('\n')
    marks = [i for i, l in enumerate(lines)
             if re.match(r'^[【\[]?\s*串文\s*第\s*(\d{1,2})\s*則\s*[】\]]?$', l.strip())]
    if not marks:
        return None
    bounds = [-1] + marks + [len(lines)]
    parts = []
    for b in range(len(bounds) - 1):
        part = '\n'.join(lines[bounds[b] + 1:bounds[b + 1]]).strip()
        if part:
            parts.append(part)
    return parts if len(parts) >= 2 else None


def split_by_thread_markers(text):
    zh = split_by_chinese_markers(text)
    if zh:
        return zh
    lines = str(text).split('\n')
    marks = []
    for i, l in enumerate(lines):
        m = re.match(r'^(\d{1,2})\s*/\s*(\d{1,2})$', l.strip())
        if m:
            marks.append((i, int(m.group(1)), int(m.group(2))))
    n = len(marks)
    if n < 2 or any(k != idx + 1 or tot != n for idx, (_, k, tot) in enumerate(marks)):
        return None
    head = '\n'.join(lines[:marks[0][0]]).strip()
    parts = []
    for idx, (i, _, _) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < n else len(lines)
        part = '\n'.join(lines[i + 1:end]).strip()
        if idx == 0 and head:
            part = head + '\n\n' + part
        parts.append(part)
    return parts


def expected_chunks(draft):
    marked = split_by_thread_markers(draft)
    plain = str(draft or '').strip()
    if marked:
        chunks = [c for p in marked for c in split_for_threads(p)]
    elif len(plain) > 300:
        chunks = split_for_threads(plain, 250)
    else:
        chunks = split_for_threads(plain)
    if chunks and re.search(r'https?://', chunks[0]):
        paras = re.split(r'\n\s*\n', chunks[0])
        keep = '\n\n'.join(x for x in paras if not re.search(r'https?://', x)).strip()
        moved = '\n\n'.join(x for x in paras if re.search(r'https?://', x)).strip()
        if keep and moved:
            chunks[0] = keep
            if len(chunks) <= 1:
                chunks.append(moved)
            else:
                chunks[-1] = chunks[-1] + '\n\n' + moved
    return chunks


def norm(s):
    return re.sub(r'\s+', '', str(s or ''))


def prefix_of(chunk, n=14):
    return norm(chunk)[:n]


# ── Threads 只讀查詢 ──────────────────────────────────────────────────
def threads_get(token, path, **kw):
    kw['access_token'] = token
    url = f'https://graph.threads.net/v1.0/{path}?' + urllib.parse.urlencode(kw)
    try:
        return json.loads(urllib.request.urlopen(url, timeout=30).read())
    except urllib.error.HTTPError as e:
        return {'_err': f'HTTP {e.code}'}
    except Exception as e:
        return {'_err': str(e)[:80]}


def dm(text):
    try:
        e = dict(l.strip().split('=', 1) for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        r = json.loads(urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode({'chat_id': JACKY_TG, 'text': text, 'disable_web_page_preview': 'true'}).encode(),
            timeout=20).read())
        return bool(r.get('ok'))
    except Exception as ex:
        log(f'TG 私訊失敗：{str(ex)[:120]}')
        return False


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    json.dump(st, open(STATE_PATH, 'w', encoding='utf-8'), ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--hours', type=int, default=24)
    a = ap.parse_args()

    now = tw_now()
    since = (now - datetime.timedelta(hours=a.hours)).strftime(FMT)
    rows = d1_http.query(
        "SELECT q.id, q.account_id, q.draft, q.url, q.posted_at, q.topic_id, "
        "a.label, a.access_token, a.platform_user_id, a.force_link_on_posts "
        "FROM social_post_queue q JOIN social_accounts a ON a.id=q.account_id "
        f"WHERE q.status='posted' AND a.platform='threads' AND q.posted_at >= '{since}' "
        "AND q.url LIKE '%threads.com%' ORDER BY q.id").get('results') or []

    st = load_state()
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r['account_id'], []).append(r)

    problems, checked, skipped, api_fail = [], 0, 0, 0
    for acc_id, items in by_acc.items():
        a0 = items[0]
        listing = threads_get(a0['access_token'], f"{a0['platform_user_id']}/threads",
                              fields='id,permalink,timestamp,username,text', limit=50)
        if '_err' in listing:
            api_fail += 1
            log(f"帳號 {a0['label']} 讀不到 Threads 列表：{listing['_err']}")
            continue
        roots = {(t.get('permalink') or '').rstrip('/').split('/')[-1]: t for t in listing.get('data', [])}
        for r in items:
            if (now - datetime.datetime.strptime(r['posted_at'][:19], FMT)).total_seconds() < GRACE_SEC:
                skipped += 1
                continue
            short = r['url'].rstrip('/').split('/')[-1]
            root = roots.get(short)
            if not root:
                skipped += 1
                log(f"#{r['id']} {r['label']}：在帳號最近 50 篇裡找不到主文（{short}），略過")
                continue
            if not str(r['draft'] or '').strip():
                skipped += 1
                log(f"#{r['id']} {r['label']}：佇列草稿是空的（被重產清掉？），略過")
                continue
            conv = threads_get(a0['access_token'], f"{root['id']}/conversation", fields='id,timestamp,username,text', limit=50)
            if '_err' in conv:
                api_fail += 1
                log(f"#{r['id']} 讀 conversation 失敗：{conv['_err']}")
                continue
            # conversation 含所有人的留言，也含帳號自己回留言的對話（不是重複發文）。
            # 所以不能只數則數：逐則拿「草稿切出來的每一則開頭」去比對該帳號自己發的文字。
            chunks = expected_chunks(r['draft'])
            own_texts = [norm(root.get('text'))] + [norm(t.get('text')) for t in conv.get('data', [])
                                                    if t.get('username') == root.get('username')]
            missing, dup = [], []
            for i, ch in enumerate(chunks, 1):
                pref = prefix_of(ch)
                hits = sum(1 for t in own_texts if t.startswith(pref))
                if hits == 0:
                    missing.append(i)
                elif hits > 1:
                    dup.append(i)
            state = 'ok' if not missing and not dup else 'bad:' + ','.join(f'缺{i}' for i in missing) + ','.join(f'重複{i}' for i in dup)
            checked += 1
            prev = st.get(str(r['id']), 'ok')
            log(f"#{r['id']} {r['label']}：草稿切成 {len(chunks)} 則；缺 {missing or '無'}、重複 {dup or '無'} → {state}")
            if state != 'ok' and state != prev:
                parts = []
                if missing:
                    parts.append(f"缺第 {'、'.join(map(str, missing))} 則（共 {len(chunks)} 則）")
                if dup:
                    parts.append(f"第 {'、'.join(map(str, dup))} 則重複發了")
                problems.append((r, f"⚠️ 串文不完整｜{r['label']}｜排隊 #{r['id']}\n{'；'.join(parts)}\n{r['url']}\n"
                                    f"（發文時間 {r['posted_at'][:16]}；Threads 若只是晚出現，補齊後會再通知）"))
            elif state == 'ok' and prev != 'ok':
                problems.append((r, f"✅ 串文已補齊｜{r['label']}｜排隊 #{r['id']}\n{len(chunks)} 則都在了\n{r['url']}"))
            st[str(r['id'])] = state

    # 記憶檔只留近 3 天的，避免一直長大
    keep_ids = {str(r['id']) for r in rows}
    st = {k: v for k, v in st.items() if k in keep_ids}

    log(f'完成：檢查 {checked} 篇、略過 {skipped}、API 失敗 {api_fail}、要通知 {len(problems)}')
    if a.dry:
        for _, t in problems:
            print('--- (dry) 會通知：\n' + t)
        return
    for r, t in problems:
        if not dm(t):
            st.pop(str(r['id']), None)   # 沒發成功就不記，下輪再試
    save_state(st)


if __name__ == '__main__':
    main()
