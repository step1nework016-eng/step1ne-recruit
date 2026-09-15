#!/usr/bin/env python3
"""每天跑一次：依序幫每位在職顧問各挑一個「最久沒被主動找過人」的開著職缺，
跑一次 talent_sourcing_agent.py。

為什麼要有這支：talent_sourcing_agent.py 本身只認得「幫我跑這一個職缺」，
不會自己排隊跑過所有職缺。這支負責排隊——換掉舊的 headhunter-crawler
（已於 2026-08-26 停用）當作主力主動找人機制。

2026-09-15 改：原本全公司共用一個挑選邏輯，不分顧問。Jacky 要求打包給
其他顧問用之後，查證過 jobs 表本身沒有「這個職缺歸哪位顧問」的欄位
（見 migrations/2026-09-15_jobs_owner_and_sourcing_requests.sql 的說明），
改成：
  1. 讀 consultants 表裡 is_active=1 的每一位顧問
  2. 每位顧問各挑一個「他名下最久沒找過人」的開放職缺，判斷順序：
     a) jobs.owner 明確指定給這位顧問的（新加的欄位，顧問之後可在後台指定）
     b) 沒有明確指定的話，退而求其次：從 applications.owner 反推
        「這個職缺主要是這位顧問在跑」（該職缺已有歸屬的應徵者裡，
        這位顧問是人數最多的那個）
     c) 兩種都推不出來 → 誠實跳過這位顧問，不亂猜、不搶別人的職缺
  3. 依序（不是同時）幫每位顧問各跑一個，一次一個——原本的資源考量
     （15-40 分鐘一場、同時跑好幾場太重）沒有變，顧問數變多代表這支
     總耗時會等比例拉長，這是刻意的取捨，不是遺漏。

⚠️ 覆蓋率現況（2026-09-15 實測）：28 個開放職缺裡只有 8 個能靠
applications.owner 反推出顧問，18 個完全查無歸屬——這代表目前只有
「該顧問名下已經有歸屬應徵者」的職缺才排得進來。要讓更多職缺被排到，
需要顧問去後台把 jobs.owner 明確填上，這支不會自動猜。

用法：
    python3 talent_sourcing_daily.py                # 依序跑過所有在職顧問
    python3 talent_sourcing_daily.py --dry           # 只印會選哪個職缺，不真的跑
    python3 talent_sourcing_daily.py --consultant phoebe   # 只跑指定顧問這一位
"""
import os
import sys
import argparse
import subprocess
import importlib.util
import time
import fcntl

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_TAG = 'AI獵頭顧問專員'
LOCK_PATH = os.path.join(HERE, '.talent_sourcing.lock')

spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


class _Lock:
    """跟 talent_sourcing_tg_worker.py 共用同一把鎖——避免顧問在 Telegram
    打字觸發即時搜尋的同時，剛好撞上每天 9 點的排程，兩邊同時跑
    talent_sourcing_agent.py（15-40 分鐘、真的在呼叫 claude -p 燒 API 額度，
    同時跑兩場沒有任何好處，只有風險）。non-blocking：搶不到就直接放棄
    這一輪，不排隊等，避免排程卡死。"""
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


def active_consultants():
    rows = D.d1("SELECT id, display_name FROM consultants WHERE is_active=1 ORDER BY display_name")
    return rows or []


def _oldest_not_sourced(rows):
    if not rows:
        return None
    rows.sort(key=lambda r: r.get('last_sourced') or '0000-00-00')
    return rows[0]


def pick_job_for(consultant_id, exclude_slugs):
    """回傳 (job_or_None, 'explicit'|'derived'|None)。"""
    exclude_sql = ''
    if exclude_slugs:
        in_list = ','.join(D.q(s) for s in exclude_slugs)
        exclude_sql = f' AND j.slug NOT IN ({in_list})'

    last_sourced_expr = (
        "(SELECT MAX(sc.created_at) FROM sourced_candidates sc "
        f" WHERE sc.job_slug = j.slug AND sc.source LIKE '{SOURCE_TAG}%') AS last_sourced"
    )

    # a) jobs.owner 明確指定
    rows = D.d1(
        f"SELECT j.slug, j.title, {last_sourced_expr} "
        f"FROM jobs j WHERE j.status = 'open' AND j.owner = {D.q(consultant_id)}{exclude_sql}"
    )
    picked = _oldest_not_sourced(rows)
    if picked:
        return picked, 'explicit'

    # b) 從 applications.owner 反推「這個職缺主要是這位顧問在跑」
    rows = D.d1(
        f"SELECT j.slug, j.title, {last_sourced_expr} "
        f"FROM jobs j WHERE j.status = 'open'{exclude_sql} "
        f"AND (SELECT a.owner FROM applications a WHERE a.job_slug = j.slug "
        f"     AND a.owner IS NOT NULL GROUP BY a.owner "
        f"     ORDER BY COUNT(*) DESC LIMIT 1) = {D.q(consultant_id)}"
    )
    picked = _oldest_not_sourced(rows)
    if picked:
        return picked, 'derived'

    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--consultant', help='只跑指定顧問（consultants.id），略過其他人')
    a = ap.parse_args()

    consultants = active_consultants()
    if a.consultant:
        consultants = [c for c in consultants if c['id'] == a.consultant]
        if not consultants:
            log(f'找不到在職顧問 {a.consultant}，或這位顧問目前不是 is_active=1，跳過')
            return

    if not consultants:
        log('目前沒有任何在職顧問（consultants.is_active=1），跳過')
        return

    plan = []
    picked_slugs = []
    for c in consultants:
        job, how = pick_job_for(c['id'], picked_slugs)
        if not job:
            log(f"{c['display_name']}（{c['id']}）：名下沒有查得到歸屬的開放職缺，跳過"
                f"（jobs.owner 沒填，applications.owner 也反推不出來——"
                f"要排進來的話請去後台把職缺的負責顧問填上）")
            continue
        picked_slugs.append(job['slug'])
        plan.append((c, job, how))
        tag = '明確指定' if how == 'explicit' else '反推歸屬'
        log(f"{c['display_name']}（{c['id']}）今天選：{job['title']}（{job['slug']}），"
            f"{tag}，上次主動找人時間：{job.get('last_sourced') or '從沒找過'}")

    if not plan:
        log('沒有任何顧問能排到職缺，本次不執行')
        return

    if a.dry:
        return

    lock = _Lock(LOCK_PATH)
    if not lock.acquire():
        log('搶不到執行鎖——可能有顧問在 Telegram 觸發即時搜尋正在跑，這一輪整批跳過，明天再排')
        return

    try:
        for c, job, how in plan:
            log(f"開始跑：{c['display_name']} → {job['title']}（{job['slug']}）")
            cmd = [sys.executable, os.path.join(HERE, 'talent_sourcing_agent.py'), '--job', job['slug']]
            r = subprocess.run(cmd, cwd=HERE, timeout=3000)
            log(f"完成，exit code={r.returncode}")
    finally:
        lock.release()


if __name__ == '__main__':
    main()
