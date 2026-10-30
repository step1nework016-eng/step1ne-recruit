#!/usr/bin/env python3
"""hiring_leads_scan.py — 「依職缺開發」名單（2026-10-14 Jacky：ph 要針對有發布財務職缺的企業開發）

到 104 搜尋某一類職缺（最近 N 天），把「公司」整理成名單寫進 D1 bd_hiring_leads，
顧問在 後台 → 客戶 → 依職缺開發 挑要不要加入開發進度（跟「人選敲門名單」同一套做法）。

只抓搜尋結果頁的公開欄位（公司名、職稱、刊登日、地區、連結）；不抓聯絡人。
同業人力公司、幼兒園／診所／學校等不是我們目標的會濾掉。
已經在開發進度或客戶名單裡的公司，後台顯示時會標出來（這支不判斷）。

用法：python3 hiring_leads_scan.py [類別=財務會計] [天數=30]   （每天跑一次即可，重複跑不會重複寫）
"""
import sys as _sys
# 2026-10-30 Jacky：「停掉」——104 只能在 Jacky 監督下碰，不准排程自動跑。要恢復請 Jacky 本人說。
if '--i-know-jacky-approved' not in _sys.argv:
    print('已停用（2026-10-30 Jacky）：依職缺開發 104 掃描。104 只能在 Jacky 監督下操作。'); _sys.exit(0)
import re
import sys
import json
import datetime

import d1_http as D
import search_104 as S

CATS = {
    '財務會計': {
        'keywords': ['財務主管', '財務經理', '財務長', '會計主管', '會計經理', '財務副理', '會計副理'],
        'title': re.compile(r'財務|會計|財會|CFO|Finance|Accounting', re.I),
        'senior': re.compile(r'主管|經理|協理|副理|財務長|處長|副總|主任|Manager|Controller|Head|Director|CFO', re.I),
    },
    # 2026-10-27 Jacky：ph 也要開發「無經驗可培訓」的 BIM／繪圖／助理工程師缺（手上有一批相關科系新鮮人）
    '無經驗培訓': {
        'keywords': ['BIM 不拘經驗', 'Revit 新鮮人', '機電繪圖 不拘', '建築繪圖 新鮮人', '工程助理 不拘經驗', '儲備工程師', '助理工程師 新鮮人', '繪圖員 無經驗可'],
        'title': re.compile(r'BIM|Revit|繪圖|製圖|工程助理|儲備工程師|助理工程師|設計助理|CAD', re.I),
        'senior': None,
        'entry': re.compile(r'不拘|無經驗|新鮮人|可培訓|培訓|應屆|沒經驗'),
    },
}
SKIP_CORP = ('幼兒園', '診所', '學校', '補習班', '安親', '托嬰', '教會', '寺', '基金會', '協會')


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def ensure():
    D.query("""CREATE TABLE IF NOT EXISTS bd_hiring_leads (
        id TEXT PRIMARY KEY, category TEXT NOT NULL, company TEXT NOT NULL,
        jobs_json TEXT, latest_date TEXT, job_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'new', decided_by TEXT, decided_at TEXT, outreach_id TEXT,
        found_at TEXT, updated_at TEXT, UNIQUE(category, company))""")


def main():
    cat = sys.argv[1] if len(sys.argv) > 1 else '財務會計'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    cfg = CATS[cat]
    ensure()
    res = S.search(cfg['keywords'], days=days, pages=3)
    by = {}
    for r in res:
        if not cfg['title'].search(r['job']) or any(w in r['company'] for w in SKIP_CORP):
            continue
        if cfg.get('entry') and not cfg['entry'].search(r['job'] + ' ' + r.get('desc', '')):
            continue
        by.setdefault(r['company'], []).append(r)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    n = 0
    for company, jobs in by.items():
        if cfg['senior'] and not any(cfg['senior'].search(j['job']) for j in jobs):
            continue   # 只有專員／助理的先不放，主管級才值得獵頭去談
        jobs.sort(key=lambda j: j['date'], reverse=True)
        keep = [{'job': j['job'], 'date': j['date'], 'url': j['url'], 'area': j['area'], 'salary': j['salary']} for j in jobs[:6]]
        D.query(f"INSERT INTO bd_hiring_leads(id,category,company,jobs_json,latest_date,job_count,status,found_at,updated_at) "
                f"VALUES({q(cat + '-' + company)},{q(cat)},{q(company)},{q(json.dumps(keep, ensure_ascii=False))},"
                f"{q(keep[0]['date'])},{len(jobs)},'new',{q(now)},{q(now)}) "
                f"ON CONFLICT(category,company) DO UPDATE SET jobs_json=excluded.jobs_json, latest_date=excluded.latest_date, "
                f"job_count=excluded.job_count, updated_at=excluded.updated_at")
        n += 1
    print(f'{cat}：104 搜到 {len(res)} 筆職缺，寫入／更新 {n} 家公司')


if __name__ == '__main__':
    main()
