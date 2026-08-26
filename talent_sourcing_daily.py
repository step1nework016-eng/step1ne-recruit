#!/usr/bin/env python3
"""每天跑一次：挑一個「最久沒被主動找過人」的開著職缺，跑一次 talent_sourcing_agent.py。

為什麼要有這支：talent_sourcing_agent.py 本身只認得「幫我跑這一個職缺」，
不會自己排隊跑過所有職缺。這支負責排隊——每天固定挑最需要的那一個，
換掉舊的 headhunter-crawler（已於 2026-08-26 停用）當作主力主動找人機制。

規則：
- 只挑 status='open' 的職缺（沒在招募的不用浪費時間找）
- 排除最近 7 天內已經有 talent-intelligence-sourcing 找過的職缺（不用每天重找同一個）
- 一次只跑一個（避免同時間跑好幾個 15-40 分鐘的搜尋，資源/API 用量都太重）
- 用完整模式（校準+正式搜尋），不是 --sample-only

用法：
    python3 talent_sourcing_daily.py           # 正常跑
    python3 talent_sourcing_daily.py --dry     # 只印會選哪個職缺，不真的跑
"""
import os
import sys
import argparse
import subprocess
import importlib.util
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_TAG = 'AI獵頭顧問專員'

spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def pick_job():
    rows = D.d1(
        "SELECT j.slug, j.title, "
        "(SELECT MAX(sc.created_at) FROM sourced_candidates sc "
        f"  WHERE sc.job_slug = j.slug AND sc.source LIKE '{SOURCE_TAG}%') AS last_sourced "
        "FROM jobs j WHERE j.status = 'open'"
    )
    if not rows:
        return None
    # 沒找過的排最前面；找過的按最久沒找排序
    rows.sort(key=lambda r: r.get('last_sourced') or '0000-00-00')
    return rows[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    job = pick_job()
    if not job:
        log('沒有任何開著的職缺，跳過')
        return

    log(f"今天選：{job['title']}（{job['slug']}），上次主動找人時間：{job.get('last_sourced') or '從沒找過'}")
    if a.dry:
        return

    cmd = [sys.executable, os.path.join(HERE, 'talent_sourcing_agent.py'), '--job', job['slug']]
    r = subprocess.run(cmd, cwd=HERE, timeout=3000)
    log(f"完成，exit code={r.returncode}")


if __name__ == '__main__':
    main()
