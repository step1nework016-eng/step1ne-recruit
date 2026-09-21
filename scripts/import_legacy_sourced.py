#!/usr/bin/env python3
"""把舊系統三家客戶的人選搬進人才池（sourced_candidates）2026-09-21

## 為什麼是 sourced_candidates 不是 applications

這些人**沒有應徵過**，是當年主動找到的。放進 applications（顧問後台的
「工作台」）會：污染應徵數字、被當成待處理人選一直催顧問、甚至可能觸發
阿財發面談邀請給根本沒投履歷的人。sourced_candidates 才是「我們主動找到
的人」那個池子，顯示在人選中心的「人才池」分頁。

## Jacky 2026-09-21 的決定：只搬有 LinkedIn 的

原始名單 154 位，品質查核結果：
  - 36% 現職欄位空白
  - 34 位只有 GitHub 沒有 LinkedIn，而且看內容（Angelboy、pjchender 這種
    台灣資安／前端圈帳號）是當年爬蟲抓錯領域的結果，跟 BIM 無關
  - 154 位裡 134 位沒有 AI 評級
只搬有 LinkedIn 的 90 位——沒有 LinkedIn 也沒有 email 的那批實際上聯絡不到，
搬進去只會讓人才池更難用。

## 去重

以 LinkedIn 網址為主鍵（正規化後比對：去掉協定、www、結尾斜線、query）。
撞到就跳過，不覆蓋既有資料——既有那筆可能已經有聯繫紀錄。

    python3 scripts/import_legacy_sourced.py --dry-run
    python3 scripts/import_legacy_sourced.py
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

SRC = os.path.expanduser(
    '~/Library/CloudStorage/GoogleDrive-aiagentg888@gmail.com/我的雲端硬碟/可聯繫人選_按職缺分組.csv')

CLIENTS = ('士芃', '一通', 'CMoney', '全躍')

# 舊職缺名 → 這次匯入建立的 slug（見 import_legacy_clients.py 的 SLUGS）
JOB_SLUG = {
    'BIM工程師': 'bim-engineer-linkou',
    '系統維運工程師 (DevOps)': 'devops-engineer-neihu',
    'Java Developer (後端工程師)': 'java-backend-engineer-neihu',
    'C++ Developer (後端工程師)': 'cpp-backend-engineer-neihu',
    'Sr. BigData Engineer (資深大數據工程師)': 'senior-bigdata-engineer-cmoney',
}

# 舊系統的狀態 → 現有資料實際在用的值（查過分布，不是自己發明的：
# new 3606／contacted 42／rejected 40／standby 2）
STATUS = {
    '未開始': 'new',
    '聯繫階段': 'contacted',
    '婉拒': 'rejected',
    '備選人才': 'standby',
    'AI推薦': 'new',      # 舊系統的「AI推薦」只代表被演算法選出來，還沒人聯絡過
}


def q(v):
    if v is None or v == '':
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def norm_li(url):
    """LinkedIn 網址正規化，用來比對同一個人。

    同一個人可能被存成 http/https、有沒有 www、結尾有沒有斜線、後面有沒有
    ?originalSubdomain=tw 這種追蹤參數——不正規化就會重複匯入。
    """
    u = (url or '').strip().lower()
    if not u:
        return ''
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^[a-z]{2,3}\.linkedin\.com', 'linkedin.com', u)
    u = re.sub(r'^www\.', '', u)
    u = u.split('?')[0].split('#')[0].rstrip('/')
    return u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    rows = [r for r in csv.DictReader(open(SRC, encoding='utf-8-sig'))
            if any(t in (r.get('客戶公司') or '') for t in CLIENTS)]
    withli = [r for r in rows if (r.get('linkedin') or '').strip()]
    print(f"{'（試跑）' if dry else '（實際寫入）'}　"
          f"三家客戶共 {len(rows)} 位，其中有 LinkedIn 的 {len(withli)} 位\n")

    # 既有人才池的 LinkedIn，一次撈完在記憶體比對（3,690 筆，比逐筆查快得多）
    existing = {}
    for r in d1_http.query(
            "SELECT id, name, linkedin_url FROM sourced_candidates "
            "WHERE linkedin_url IS NOT NULL AND linkedin_url != ''")['results']:
        k = norm_li(r['linkedin_url'])
        if k:
            existing.setdefault(k, r)
    print(f"現有人才池有 LinkedIn 的 {len(existing)} 筆，用來去重\n")

    seen_in_file = {}
    added = dup_db = dup_file = nojob = 0
    for r in sorted(withli, key=lambda x: (x['目標職缺'], x['姓名'])):
        key = norm_li(r['linkedin'])
        if key in existing:
            print(f"  ⏭️ 人才池已有：{r['姓名'][:20]}　→ 既有 {existing[key]['name'][:18]}")
            dup_db += 1
            continue
        if key in seen_in_file:
            print(f"  ⏭️ 檔案內重複：{r['姓名'][:20]}")
            dup_file += 1
            continue
        slug = JOB_SLUG.get((r['目標職缺'] or '').strip())
        if not slug:
            print(f"  ⚠️ 對不到職缺，跳過：{r['姓名']}｜{r['目標職缺']}")
            nojob += 1
            continue

        seen_in_file[key] = True
        cols = {
            'source': 'legacy_import',
            'source_url': (r.get('linkedin') or '').strip(),
            'name': (r.get('姓名') or '').strip(),
            'headline': (r.get('現職') or '').strip() or None,
            'location': (r.get('地區') or '').strip() or None,
            'linkedin_url': (r.get('linkedin') or '').strip(),
            'github_url': (r.get('github') or '').strip() or None,
            'job_slug': slug,
            'grade': (r.get('ai評級') or '').strip() or None,
            'status': STATUS.get((r.get('狀態') or '').strip(), 'new'),
            'note': f"2026-09-21 從舊系統匯入（舊 id {r.get('id')}，"
                    f"原目標職缺「{r['目標職缺']}」，{r.get('客戶公司')}）",
            'raw_json': json.dumps(r, ensure_ascii=False),
        }
        cols = {k: v for k, v in cols.items() if v is not None}
        print(f"  ➕ {cols['name'][:20]:<22} {slug:<32} {cols['status']}")
        if not dry:
            names = ', '.join(cols) + ', created_at'
            vals = ', '.join(q(v) for v in cols.values()) + ", datetime('now','+8 hours')"
            d1_http.query(f"INSERT INTO sourced_candidates ({names}) VALUES ({vals})")
        added += 1

    print(f"\n{'會新增' if dry else '已新增'} {added} 位")
    print(f"  跳過：人才池已有 {dup_db}｜檔案內重複 {dup_file}｜對不到職缺 {nojob}")
    print(f"  沒搬：{len(rows) - len(withli)} 位（沒有 LinkedIn，實際上聯絡不到）")
    if dry:
        print("\n確認沒問題後，拿掉 --dry-run 再跑一次。")
    else:
        print("\n看這裡：顧問後台 → 人選 → 人才池")
    return 0


if __name__ == '__main__':
    sys.exit(main())
