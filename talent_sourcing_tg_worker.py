#!/usr/bin/env python3
"""顧問在 Telegram 打字觸發「幫我找這個職缺的人才」的佇列認領器。

2026-09-15 建立。背景：talent_sourcing_agent.py 實際跑一次要 15-40 分鐘
（校準+正式搜尋都呼叫 claude -p，真的在燒 API 額度），Cloudflare Worker
沒有辦法直接跑這種長工作，所以顧問在 TG 打字之後，Worker
（step1ne-recruit/src/index.js，「🔍 顧問找人才」topic）只把請求寫進
talent_sourcing_requests 這張表（狀態 pending），立刻回覆「已排入搜尋」，
真正執行交給這支常駐 daemon 認領。

⚠️ 刻意不共用 ai_jobs／ai_worker.py 那套佇列：ai_worker.py 是給
call_notes_summary、call_prep 這類「幾秒到 8 分鐘、claude -p 關掉
Bash/WebSearch 等工具」的輕量整理工作設計的（480 秒逾時 + NO_TOOLS）。
talent_sourcing_agent.py 需要開 WebSearch/WebFetch 真的去找人，跑
15-40 分鐘，塞進 ai_worker.py 會被逾時砍斷、也會被工具限制卡死。
改成跟 interview_daemon.py／checkup_daemon.py 一樣，各自顧自己一張表
的常駐 daemon，是延續既有架構，不是另外發明一套。

跟每天 9 點跑的 talent_sourcing_daily.py 共用同一把檔案鎖
（.talent_sourcing.lock），避免兩邊同時跑 talent_sourcing_agent.py。

用法：
    python3 talent_sourcing_tg_worker.py            # 常駐，每 30 秒撈一次
    python3 talent_sourcing_tg_worker.py --once      # 跑一輪就結束（測試用）
"""
import argparse
import fcntl
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

LOCK_PATH = os.path.join(HERE, '.talent_sourcing.lock')
TG_ENV_PATH = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
POLL_SEC = 30
AGENT_TIMEOUT = 3000  # 略高於 talent_sourcing_agent.py 自己的 2400 秒，留緩衝
WORKER_ID = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


class _Lock:
    """跟 talent_sourcing_daily.py 裡那一份是同一個檔案、同一套邏輯——
    兩支各自維護一份是刻意的（跟這個 repo 其他 daemon 的風格一致，
    不為了共用兩行程式碼多繞一層 import）。"""
    def __init__(self, path):
        self.path = path
        self.fh = None

    def acquire(self):
        self.fh = open(self.path, 'w')
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self):
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None


def _tg_env():
    vals = {}
    with open(TG_ENV_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                vals[k.strip()] = v.strip().strip('"\'')
    return vals


def tg_send(chat_id, thread_id, text):
    e = _tg_env()
    data = {'chat_id': chat_id, 'text': text}
    if thread_id:
        data['message_thread_id'] = thread_id
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode(data).encode(), timeout=20)
    except Exception as ex:
        log(f'  ⚠️ 回覆 TG 失敗（不影響搜尋結果已經存進資料庫）：{str(ex)[:150]}')


def claim_one():
    rows = d1_http.query(
        "SELECT * FROM talent_sourcing_requests WHERE status='pending' "
        "ORDER BY created_at LIMIT 1")['results']
    if not rows:
        return None
    row = rows[0]
    claim = d1_http.query(
        f"UPDATE talent_sourcing_requests SET status='running', "
        f"started_at=datetime('now','+8 hours'), attempts=attempts+1, "
        f"worker_id={q(WORKER_ID)} WHERE id={q(row['id'])} AND status='pending'")
    if not claim.get('meta', {}).get('changes'):
        return None
    return row


def job_title(slug):
    rows = d1_http.query(f"SELECT title FROM jobs WHERE slug={q(slug)}")['results']
    return rows[0]['title'] if rows else slug


def candidate_count_since(slug, since):
    rows = d1_http.query(
        f"SELECT COUNT(*) c FROM sourced_candidates "
        f"WHERE job_slug={q(slug)} AND created_at > {q(since)}")['results']
    return rows[0]['c'] if rows else 0


def candidate_names_since(slug, since, limit=8):
    rows = d1_http.query(
        f"SELECT name, company FROM sourced_candidates "
        f"WHERE job_slug={q(slug)} AND created_at > {q(since)} "
        f"ORDER BY created_at DESC LIMIT {limit}")['results']
    return rows or []


def process(row):
    slug = row['job_slug']
    title = job_title(slug)
    who = row.get('requested_by') or '顧問'
    before = d1_http.query("SELECT datetime('now','+8 hours') t")['results'][0]['t']

    log(f"開始跑：{who} 要求的「{title}」（{slug}）")
    cmd = [sys.executable, os.path.join(HERE, 'talent_sourcing_agent.py'), '--job', slug]
    try:
        r = subprocess.run(cmd, cwd=HERE, timeout=AGENT_TIMEOUT,
                            capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        d1_http.query(
            f"UPDATE talent_sourcing_requests SET status='failed', "
            f"error='執行超過 {AGENT_TIMEOUT} 秒逾時', "
            f"done_at=datetime('now','+8 hours') WHERE id={q(row['id'])}")
        tg_send(row['chat_id'], row['thread_id'],
                f"⚠️「{title}」的搜尋超過時間還沒跑完，先中止了。麻煩晚點再試一次，"
                f"或直接跟 Jacky 反映。")
        return

    saved = candidate_count_since(slug, before)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or '')[-300:]
        d1_http.query(
            f"UPDATE talent_sourcing_requests SET status='failed', error={q(err)}, "
            f"candidates_saved={saved}, done_at=datetime('now','+8 hours') "
            f"WHERE id={q(row['id'])}")
        tg_send(row['chat_id'], row['thread_id'],
                f"❌「{title}」搜尋失敗了（exit code {r.returncode}），已經記錄下來，"
                f"麻煩跟 Jacky 反映一下。")
        log(f'  ❌ 失敗：{err[:150]}')
        return

    names = candidate_names_since(slug, before)
    lines = '\n'.join(f"・{n.get('name')}（{n.get('company') or '未知公司'}）" for n in names)
    msg = f"✅「{title}」搜尋完成，新增 {saved} 位候選人進池子"
    if lines:
        msg += f"：\n{lines}"
    if saved > len(names):
        msg += f"\n…等共 {saved} 位，其餘到後台履歷池看。"
    d1_http.query(
        f"UPDATE talent_sourcing_requests SET status='done', candidates_saved={saved}, "
        f"done_at=datetime('now','+8 hours') WHERE id={q(row['id'])}")
    tg_send(row['chat_id'], row['thread_id'], msg)
    log(f'  ✅ 完成，新增 {saved} 位')


def tick():
    lock = _Lock(LOCK_PATH)
    if not lock.acquire():
        return False  # 每天 9 點的排程正在跑，這一輪不搶
    try:
        row = claim_one()
        if not row:
            return False
        process(row)
        return True
    finally:
        lock.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()

    if a.once:
        tick()
        return

    log(f'常駐啟動，裝置：{WORKER_ID}，每 {POLL_SEC} 秒撈一次')
    while True:
        try:
            tick()
        except Exception as e:
            log(f'⚠️ tick 例外（不中斷常駐）：{str(e)[:200]}')
        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
