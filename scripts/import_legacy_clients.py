#!/usr/bin/env python3
"""把舊系統的客戶與客戶職缺匯入現在的顧問後台（2026-09-21）

## 資料來源

舊系統（2026 上半年）留下的匯出檔，不在版控裡，路徑寫死在 SOURCE：
  ~/claude-projects/招募資料/from-telegram/step1ne 職缺管理 - 工作表1.csv
      53 個職缺，欄位比現在的系統還細（關鍵挑戰／吸引亮點／招募困難點／面試流程）
  ~/Library/CloudStorage/.../可聯繫人選_按職缺分組.csv
      只拿來補「職缺管理表裡沒有、但人選檔裡出現過」的職缺名稱

## 這支只搬客戶與職缺，不搬人選

人選那份是 86,589 筆的獨立檔案，牽涉個資與去重，要另外評估。

## 安全設計

  - 職缺一律建成 status='draft'。官網是靜態站（寫 D1 不會自動上線），
    但 draft 讓顧問後台看得到、又不會被當成在辦職缺進到 AI 配對池
    （MATCHABLE_STATUSES 只認 open/active，見 recommendation_service.py）。
    顧問看過覺得對，再自己按「開啟／關閉職缺」。
  - slug 重複就跳過，不覆蓋既有職缺。
  - 客戶用 display_name 比對，已存在就沿用不重建。
  - 跑之前先 --dry-run 看會建什麼。

    python3 scripts/import_legacy_clients.py --dry-run
    python3 scripts/import_legacy_clients.py
"""
import argparse
import csv
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

JOBS_CSV = os.path.expanduser(
    '~/claude-projects/招募資料/from-telegram/step1ne 職缺管理 - 工作表1.csv')
CANDS_CSV = os.path.expanduser(
    '~/Library/CloudStorage/GoogleDrive-aiagentg888@gmail.com/我的雲端硬碟/可聯繫人選_按職缺分組.csv')

# Jacky 2026-09-21 指定的三家。用關鍵字比對是因為舊檔裡的寫法不統一
# （「CMoney (全躍財經資訊股份有限公司)」中間有空格、括號全形半形混用）。
WANTED = {
    '士芃': '士芃科技股份有限公司',
    '一通': '一通數位有限公司',
    'CMoney': 'CMoney（全躍財經資訊股份有限公司）',
    '全躍': 'CMoney（全躍財經資訊股份有限公司）',
}

SLUGS = {
    ('士芃科技股份有限公司', 'BIM工程師'): 'bim-engineer-linkou',
    ('一通數位有限公司', 'C++ Developer (後端工程師)'): 'cpp-backend-engineer-neihu',
    ('一通數位有限公司', 'Java Developer (後端工程師)'): 'java-backend-engineer-neihu',
    ('一通數位有限公司', '系統維運工程師 (DevOps)'): 'devops-engineer-neihu',
    ('CMoney（全躍財經資訊股份有限公司）', 'Sr. BigData Engineer (資深大數據工程師)'):
        'senior-bigdata-engineer-cmoney',
}


def q(v):
    if v is None or v == '':
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def canon(name):
    for kw, full in WANTED.items():
        if kw in (name or ''):
            return full
    return None


def parse_salary(text):
    """「60,000~80,000元/月」「40k以上」→ (min, max, unit)。

    抓不出來就回 (None, None, 'MONTH')——寧可留空讓顧問自己填，
    也不要猜一個數字進去；薪資填錯比沒填危險得多（今天的推薦稽核就踩過）。
    """
    t = (text or '').replace(',', '').replace('，', '')
    nums = [int(n) for n in re.findall(r'(\d{4,7})', t)]
    if not nums:
        ks = [int(k) * 1000 for k in re.findall(r'(\d{2,3})\s*[kK]', t)]
        nums = ks
    if not nums:
        return None, None, 'MONTH'
    return (min(nums), max(nums) if len(nums) > 1 else None, 'MONTH')


def parse_years(text):
    m = re.search(r'(\d+)\s*年', text or '')
    return int(m.group(1)) if m else None


def parse_headcount(text):
    m = re.findall(r'\d+', text or '')
    return int(m[-1]) if m else None


def load_jobs():
    rows = list(csv.DictReader(open(JOBS_CSV, encoding='utf-8-sig')))
    out = []
    for r in rows:
        full = canon(r.get('客戶公司'))
        if not full or not (r.get('職位名稱') or '').strip():
            continue
        out.append((full, r))
    # 人選檔裡出現、但職缺管理表沒有的職缺（CMoney 那筆就是）——只補標題，
    # 其餘欄位留空並在 notes 註明來源，不要編造內容。
    seen = {(f, (r['職位名稱'] or '').strip()) for f, r in out}
    if os.path.exists(CANDS_CSV):
        for r in csv.DictReader(open(CANDS_CSV, encoding='utf-8-sig')):
            full = canon(r.get('客戶公司'))
            title = (r.get('目標職缺') or '').strip()
            if full and title and (full, title) not in seen:
                seen.add((full, title))
                out.append((full, {'職位名稱': title, '客戶公司': full, '_thin': True}))
    return out


def ensure_company(name, dry):
    hit = d1_http.query(
        f"SELECT id, display_name FROM client_companies WHERE display_name={q(name)}")['results']
    if hit:
        print(f"  客戶已存在，沿用：{name}（{hit[0]['id']}）")
        return hit[0]['id']
    cid = 'co_' + uuid.uuid4().hex[:8]
    print(f"  ➕ 新增客戶：{name}（{cid}）")
    if not dry:
        # portal_token 是 NOT NULL——客戶用它登入自己的 portal 看進度。
        # 長度照既有資料的 48 位十六進位（uuid4 兩組去掉 dash 剛好 48）。
        token = (uuid.uuid4().hex + uuid.uuid4().hex)[:48]
        d1_http.query(
            "INSERT INTO client_companies (id, display_name, portal_token, relation, "
            "relation_note, created_at, updated_at) VALUES ("
            f"{q(cid)}, {q(name)}, {q(token)}, 'signed', "
            f"{q('2026-09-21 從舊系統「step1ne 職缺管理」匯入')}, "
            "datetime('now','+8 hours'), datetime('now','+8 hours'))")
    return cid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    jobs = load_jobs()
    print(f"{'（試跑，不寫入）' if dry else '（實際寫入）'}　找到 {len(jobs)} 個職缺\n")

    companies = {}
    created = skipped = 0
    for full, r in sorted(jobs, key=lambda x: (x[0], x[1]['職位名稱'])):
        title = (r['職位名稱'] or '').strip()
        slug = SLUGS.get((full, title))
        if not slug:
            print(f"  ⚠️ 沒有預先指定 slug，跳過：{full}｜{title}")
            skipped += 1
            continue
        if d1_http.query(f"SELECT slug FROM jobs WHERE slug={q(slug)}")['results']:
            print(f"  ⏭️ slug 已存在，跳過不覆蓋：{slug}")
            skipped += 1
            continue

        if full not in companies:
            companies[full] = ensure_company(full, dry)
        cid = companies[full]

        thin = r.get('_thin')
        smin, smax, sunit = parse_salary(r.get('薪資範圍'))
        # 舊表的欄位在新系統沒有一一對應的，統一收進 notes（顧問看得到、
        # 不會外流給候選人），不要硬塞進對外欄位。
        extra = []
        for k in ('關鍵挑戰', '吸引亮點', '招募困難點', '特殊條件', '產業背景要求', '團隊規模'):
            v = (r.get(k) or '').strip()
            if v:
                extra.append(f'{k}：{v}')
        src = '2026-09-21 從舊系統「step1ne 職缺管理」匯入'
        if thin:
            src += '（舊系統只有職缺名稱，其餘欄位需補）'
        notes = src + ('\n' + '\n'.join(extra) if extra else '')

        cols = {
            'slug': slug, 'title': title, 'client_name': full, 'company_id': cid,
            'status': 'draft', 'client_named': 1,
            'department': (r.get('部門') or '').strip() or None,
            'headcount': parse_headcount(r.get('需求人數')),
            'salary_min': smin, 'salary_max': smax,
            'salary_unit': sunit if smin else None,
            'salary_note': (r.get('薪資範圍') or '').strip() or None,
            'locations': (r.get('工作地點') or '').strip() or None,
            'must_skills': (r.get('主要技能') or '').strip() or None,
            'years_min': parse_years(r.get('經驗要求')),
            'education_level': (r.get('學歷要求') or '').strip() or None,
            'language_requirement': (r.get('語言要求') or '').strip() or None,
            'preferred_background': (r.get('產業背景要求') or '').strip() or None,
            'team_size': (r.get('團隊規模') or '').strip() or None,
            'interview_process': (r.get('面試流程') or '').strip() or None,
            'talking_points': (r.get('吸引亮點') or '').strip() or None,
            'notes': notes,
        }
        cols = {k: v for k, v in cols.items() if v is not None}
        print(f"  ➕ {full}｜{title}")
        print(f"      slug={slug}　狀態=draft　"
              f"薪資={cols.get('salary_min','—')}~{cols.get('salary_max','—')}　"
              f"地點={cols.get('locations','—')}")
        if thin:
            print("      ⚠️ 舊系統只有職缺名稱，其餘欄位空白，需要補")
        if not dry:
            names = ', '.join(cols) + ', updated_at'
            vals = ', '.join(q(v) if not isinstance(v, int) else str(v)
                             for v in cols.values()) + ", datetime('now','+8 hours')"
            d1_http.query(f"INSERT INTO jobs ({names}) VALUES ({vals})")
        created += 1

    print(f"\n{'會建立' if dry else '已建立'} {created} 個職缺、{len(companies)} 家客戶；跳過 {skipped} 個")
    if dry:
        print("確認沒問題後，拿掉 --dry-run 再跑一次。")
    else:
        print("全部是 draft，顧問後台看得到但不會進 AI 配對池。")
        print("確認內容正確後，到職缺總覽按「開啟／關閉職缺」改成招募中。")
    return 0


if __name__ == '__main__':
    sys.exit(main())
