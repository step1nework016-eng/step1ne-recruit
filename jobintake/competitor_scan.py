#!/usr/bin/env python3
"""競品職缺反查：讀同業獵頭公司網站上公開刊登的職缺，反推是哪家企業委託，
變成客戶開發名單。方法細節與各家網站的踩雷紀錄寫在
`references/競品職缺反查.md`，改動邏輯前先讀那份。

## 目前支援的來源

- **HuntByte**（sitemap-jobs.xml）——⭐ 目前最好用的來源。
  這家不像典型獵頭那樣把客戶名稱藏起來，**職缺頁的 `<meta property="og:title">`
  直接就是「職稱－公司全名｜HuntByte」**，不用反推、不用猜，讀 meta tag 就有。
  純 HTTP（curl 等級）就能讀到，不用瀏覽器、不會被擋。

- Robert Walters——sitemap 讀得到（450 筆），**但詳情頁被 PerimeterX 擋住**
  （連真的瀏覽器開都直接 403「訪問此頁面已被拒絕」）。這支目前只處理
  「讀 sitemap 網址裡的職缺標題」這一段（標題本身常常已經有夠用的線索，
  例如「外商電源大廠」「半導體製造業龍頭」），**沒有做詳情頁抓取**，
  真的要用要嘛人工開瀏覽器看，要嘛之後另外評估要不要做進一步處理。
  ⚠️ 遇到這種擋，就是停，不要試著繞過驗證碼或換 IP。

- RecruitFirst、H&L 智理——sitemap 裡目前只找得到部落格文章，找不到職缺頁，
  可能職缺放在另一個系統（外部 ATS／不同網域）。這支還沒處理，
  下次要用的人請先手動看一次網站結構。
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import client_guard  # noqa: E402
import d1_http as D  # noqa: E402

UA = 'Mozilla/5.0 (compatible; Step1ne-Research/1.0; +internal recruitment lead research)'
RATE_LIMIT_SEC = 1.0   # 每個網站每秒最多 1 次，任務規定的界線
CACHE_DIR = os.path.join(HERE, 'bd_work', 'competitor_scan')
CACHE_FILE = os.path.join(CACHE_DIR, f"huntbyte_{datetime.date.today().isoformat()}.json")


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace')


def og(html, prop):
    m = re.search(rf'<meta property="{prop}" content="([^"]*)"', html)
    return m.group(1) if m else None


def huntbyte_job_urls():
    xml = fetch('https://www.huntbyte.com/sitemap-jobs.xml')
    return re.findall(r'<loc>([^<]+)</loc>', xml)


def huntbyte_scan(limit=None, sleep=RATE_LIMIT_SEC, log=print):
    """回傳 [{url, company, title, location, employment}]。企業名直接從 og:title 讀出來，不用猜。"""
    urls = huntbyte_job_urls()
    if limit:
        urls = urls[:limit]
    out = []
    for i, url in enumerate(urls, 1):
        try:
            html = fetch(url)
        except (urllib.error.URLError, TimeoutError) as e:
            log(f'  [{i}/{len(urls)}] 抓取失敗，跳過：{url} ({e})')
            time.sleep(sleep)
            continue
        title = og(html, 'og:title') or ''
        desc = og(html, 'og:description') or ''
        # og:title 格式固定是「職稱－公司全名｜HuntByte」
        m = re.match(r'^(.*?)－(.*?)｜HuntByte$', title)
        role, company = (m.group(1), m.group(2)) if m else (title, None)
        loc_m = re.search(r'地點：([^，]+)', desc)
        emp_m = re.search(r'類型：([^。]+)', desc)
        row = {
            'url': url, 'company': company, 'role': role,
            'location': loc_m.group(1) if loc_m else None,
            'employment': emp_m.group(1) if emp_m else None,
        }
        out.append(row)
        if i % 20 == 0:
            log(f'  [{i}/{len(urls)}] ...')
        time.sleep(sleep)
    return out


def robertwalters_titles():
    """只讀 sitemap 網址裡的職缺標題，不進詳情頁（會被 PerimeterX 擋）。"""
    import urllib.parse
    xml = fetch('https://www.robertwalters.com.tw/advert_links.xml')
    urls = re.findall(r'<loc>([^<]+)</loc>', xml)
    out = []
    for u in urls:
        slug = u.rstrip('/').split('/')[-1].replace('.html', '')
        jid, _, rest = slug.partition('-')
        title = urllib.parse.unquote(rest).replace('-', ' ').strip()
        category = u.split('/jobs/')[1].split('/')[0] if '/jobs/' in u else ''
        out.append({'url': u, 'job_id': jid, 'title': title, 'category': category, 'company': None})
    return out



# ⚠️ 2026-09-24 加：HuntByte 上排名第一的「數位獵人科技股份有限公司」貼了 69 筆
# 職缺，職稱大多掛「【代徵】」——這代表它自己就是一家人力／獵頭仲介公司，
# 在幫「它的」客戶刊登職缺，og:title 抓到的是仲介公司的名字，不是真正要用人的
# 企業。這種一定要濾掉，不然會把同業誤當成開發目標（違反挑公司的硬規則：
# 不要挑同業）。判斷方式：公司名稱裡有這些字，或者這家公司底下的職缺標題
# 大量出現「代徵」，就當同業處理，不進開發名單。
AGENCY_NAME_HINTS = ('獵人', '獵頭', '人力銀行', '人力資源顧問', '人才顧問',
                     '獵才', '人力仲介', 'RECRUIT', 'HR CONSULT')
AGENCY_TITLE_HINT = '代徵'


def looks_like_agency(company, jobs):
    up = (company or '').upper()
    if any(h in up for h in AGENCY_NAME_HINTS):
        return True
    titled = [j for j in jobs if AGENCY_TITLE_HINT in (j.get('role') or '')]
    return len(jobs) >= 3 and len(titled) / len(jobs) >= 0.5


def d1_query_list(sql):
    return D.query(sql).get('results') or []


def guard_filter(companies):
    """companies: list[str]。回 (可以用的公司名清單, 被擋的清單含理由)。"""
    clients = client_guard.load_clients(d1_query_list)
    ok, blocked, warned = client_guard.filter_targets(companies, clients)
    return ok, blocked, warned


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def upsert_lead(company, signal, source_kind, note):
    """寫進 company_leads，同一家公司＋同一個 signal 不要重複插入。"""
    exists = D.query(
        f"SELECT id FROM company_leads WHERE company={q(company)} AND signal={q(signal)}"
    ).get('results')
    if exists:
        return False
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    D.query(
        f"INSERT INTO company_leads (company, signal, signal_type, source_kind, note, status, created_at, updated_at) "
        f"VALUES ({q(company)}, {q(signal)}, {q('competitor_posting')}, {q(source_kind)}, {q(note)}, {q('new')}, {q(now)}, {q(now)})"
    )
    return True


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=None)
    p.add_argument('--write', action='store_true', help='實際寫入 company_leads；不加只印出結果')
    p.add_argument('--refresh', action='store_true', help='不用今天的快取，重新爬一次')
    args = p.parse_args()

    print('== HuntByte 掃描 ==')
    if not args.limit and not args.refresh and os.path.exists(CACHE_FILE):
        print(f'  用今天的快取：{CACHE_FILE}（要重爬用 --refresh）')
        rows = json.load(open(CACHE_FILE, encoding='utf-8'))
    else:
        rows = huntbyte_scan(limit=args.limit)
        if not args.limit:
            os.makedirs(CACHE_DIR, exist_ok=True)
            json.dump(rows, open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from collections import defaultdict
    by_company = defaultdict(list)
    for r in rows:
        if r['company']:
            by_company[r['company']].append(r)

    print(f'共 {len(rows)} 筆職缺，{len(by_company)} 家公司（去重後）')

    ok, blocked, warned = guard_filter(list(by_company.keys()))
    ok_set = set(ok)
    if blocked:
        print(f'\n⛔ 過濾掉 {len(blocked)} 家既有／禁止接觸的客戶：')
        for b in blocked:
            print(f"  {b['company']} —— {b['why']}")

    candidates = [(c, jobs) for c, jobs in by_company.items() if c in ok_set]
    agencies = [(c, jobs) for c, jobs in candidates if looks_like_agency(c, jobs)]
    ranked = sorted(
        [(c, jobs) for c, jobs in candidates if not looks_like_agency(c, jobs)],
        key=lambda x: len(x[1]), reverse=True)

    if agencies:
        print(f'\n🚫 濾掉 {len(agencies)} 家看起來是同業仲介（名稱含獵人/獵頭類字眼，或職缺大量標「代徵」）：')
        for c, jobs in sorted(agencies, key=lambda x: len(x[1]), reverse=True):
            print(f"  [{len(jobs)}] {c}")

    print(f'\n可以開發、依職缺數排序（前 30）：')
    for company, jobs in ranked[:30]:
        roles = '、'.join(j['role'] for j in jobs[:3])
        print(f"  [{len(jobs)}] {company} —— {roles}")

    if args.write:
        n = 0
        for company, jobs in ranked:
            signal = f"HuntByte 上同時刊登 {len(jobs)} 個職缺：" + '、'.join(j['role'] for j in jobs[:5])
            note = f"來源：HuntByte（{jobs[0]['url']}）。地點：{jobs[0].get('location') or '未知'}"
            if upsert_lead(company, signal, 'huntbyte', note):
                n += 1
        print(f'\n寫入 company_leads {n} 筆新線索（source_kind=huntbyte）')
