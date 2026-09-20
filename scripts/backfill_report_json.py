#!/usr/bin/env python3
"""補產 reports.content_json（2026-09-20）

## 為什麼需要這支

面談結束後，系統會把純文字報告再送一次 AI 轉成結構化 JSON 存進
`reports.content_json`。撞到 claude 額度上限時，CLI 吐的是
「You've hit your limit」而不是 JSON，`report_to_json()` 解析不到就
`return None`，content_json 被寫成 NULL——**而且全系統沒有任何一支程式
會回頭補**（grep 'content_json IS NULL' 是零命中）。

實際損失：59 筆報告裡 12 筆是 NULL（2026-07-30 ~ 08-12）。

不是「少了個好看的欄位」而已。P3 推薦引擎、後台結構化欄位、客戶版履歷
都只讀 content_json：
  - 林均緯的純文字報告寫著「❌ 不接受派遣（問兩次，明確拒絕）」，
    但 content_json 是 NULL，推薦引擎完全沒看到這句，只能拿應徵表單的
    「有 Revit 證照」去配對。那次剛好配到全職職缺沒出事，是運氣不是設計。

`interview_daemon.py` 那邊已經補上重試與 TG 警報（見 report_to_json），
這支負責把歷史上已經壞掉的補回來。

## 用法

    python3 scripts/backfill_report_json.py --dry-run   # 只列出要補哪幾筆
    python3 scripts/backfill_report_json.py             # 實際補
    python3 scripts/backfill_report_json.py --limit 3   # 一次只補 3 筆

⚠️ 每筆會呼叫一次 claude，會吃額度。有人正在面談時不要跑——
   今天（09-20）就是因為額度問題出過事。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import d1_http                      # noqa: E402
import interview_daemon as D        # noqa: E402


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def pending():
    """需要補的報告，舊的先補（越舊的越可能已經被顧問翻過、落差越有感）。"""
    return d1_http.query(
        "SELECT r.id, r.application_id, r.content_md, a.name "
        "FROM reports r LEFT JOIN applications a ON a.id = r.application_id "
        "WHERE r.content_json IS NULL AND r.content_md IS NOT NULL "
        "  AND length(r.content_md) > 200 "     # 51 字那種殘骸補了也沒意義
        # 2026-08-07 壓力測試留下的假人選（假負載1~3，信箱 @example.invalid，
        # 逐字稿全空）。補了也只是把「這是假資料」翻譯成 JSON，純浪費額度。
        "  AND r.application_id NOT LIKE 'gate-load%' "
        "  AND COALESCE(a.name,'') NOT LIKE '假負載%' "
        "  AND COALESCE(a.name,'') NOT LIKE '%測試%' "
        "ORDER BY r.created_at ASC")['results']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    rows = pending()
    if args.limit:
        rows = rows[:args.limit]

    print(f'要補 {len(rows)} 筆：')
    for r in rows:
        print(f"  - {r['name'] or '(無名)'}　報告 {r['id']}　純文字 {len(r['content_md'])} 字")
    if args.dry_run:
        print('\n（--dry-run，沒有實際呼叫 AI）')
        return 0

    ok = fail = 0
    for r in rows:
        name = r['name'] or '(無名)'
        print(f'\n── {name} ──')
        try:
            # 用 daemon 自己的 context_for()，不要另外寫一套抓履歷/職缺的邏輯，
            # 不然補出來的 JSON 會跟正常流程產的不一樣。
            ctx = D.context_for(r['application_id'])
        except Exception as e:
            print(f'  ❌ 讀不到 context：{e}')
            fail += 1
            continue

        blob = D.report_to_json(r['content_md'], ctx, name, app_id=r['application_id'])
        if not blob:
            print('  ❌ 產生失敗（原因看上面 report_to_json 印的訊息）')
            fail += 1
            continue

        d1_http.query(
            f"UPDATE reports SET content_json={q(blob)} WHERE id={q(r['id'])}")
        print(f'  ✅ 已補上（{len(blob)} 字元）')
        ok += 1

    print(f'\n完成：成功 {ok} 筆，失敗 {fail} 筆')
    if fail:
        print('失敗的可以重跑這支，已經補好的不會再被撈出來。')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
