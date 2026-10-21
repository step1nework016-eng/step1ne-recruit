#!/usr/bin/env python3
"""人才標籤 backfill（2026-10-21）。

預設只「數」不排：印出會排幾筆工作。加 --apply 才真的寫進 ai_jobs。
排進去的是：
  - candidate_tagging_backfill：每位人選最新一份有結構化內容的阿財報告各一筆（不觸發配對、不發 TG）
  - job_tagging：開著的職缺（open／active／social_only）各一筆
AI 由 ai_worker 接（排在背景工作最後面）。Mac 記憶體小，跑之前在 Mac 的
~/.config/workflow-os/ai_worker_skip_kinds.txt 加上 candidate_tagging_backfill,job_tagging
讓 WSL2 那台接。

用法：
  python3 scripts/backfill_talent_tags.py                 # 只數
  python3 scripts/backfill_talent_tags.py --apply         # 真的排
  python3 scripts/backfill_talent_tags.py --preview <application_id> ...   # 當場跑 AI 看輸出，不寫資料（會呼叫 claude）
  python3 scripts/backfill_talent_tags.py --preview-job <job_slug> ...
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402
import recommendation_service as rs  # noqa: E402


def latest_reports():
    rows = d1_http.query(
        'SELECT a.id AS application_id, r.id AS report_id FROM applications a '
        '  JOIN reports r ON r.id = (SELECT r2.id FROM reports r2 WHERE r2.application_id = a.id '
        "                             AND r2.content_json IS NOT NULL AND r2.content_json <> '' "
        '                             ORDER BY r2.created_at DESC LIMIT 1) '
        ' WHERE a.superseded_by IS NULL '
        "   AND COALESCE(a.status,'') NOT IN ('duplicate')")['results'] or []
    return rows


def open_jobs():
    st = ', '.join(rs._q(s) for s in rs.REVERSE_MATCH_STATUSES)
    ph = ', '.join(rs._q(s) for s in rs.PLACEHOLDER_JOB_SLUGS)
    return d1_http.query(
        f"SELECT slug, updated_at FROM jobs WHERE COALESCE(status,'open') IN ({st}) AND slug NOT IN ({ph})")['results'] or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--preview', nargs='*')
    ap.add_argument('--preview-job', nargs='*')
    a = ap.parse_args()
    if a.preview or a.preview_job:
        import ai_worker as w
        for app_id in a.preview or []:
            print(json.dumps(w.tag_candidate(app_id, dry_run=True), ensure_ascii=False, indent=1))
        for slug in a.preview_job or []:
            print(json.dumps(w.tag_job(slug, dry_run=True), ensure_ascii=False, indent=1))
        return
    reps, jobs = latest_reports(), open_jobs()
    print(f'人選（最新一份結構化報告）：{len(reps)} 位｜開著的職缺：{len(jobs)} 個')
    if not a.apply:
        print('（只數不排；確認後加 --apply）')
        return
    n = m = 0
    for r in reps:
        if rs._queue_ai_job(f"ctag:{r['report_id']}", 'candidate_tagging_backfill',
                            {'application_id': r['application_id'], 'report_id': r['report_id'],
                             'match_after': False, 'source': 'ai_backfill'}):
            n += 1
    for j in jobs:
        if rs.queue_job_tagging(j['slug'], j.get('updated_at')):
            m += 1
    print(f'已排：人選標籤 {n} 筆、職缺標籤 {m} 筆（已排過的會自動略過）')


if __name__ == '__main__':
    main()
