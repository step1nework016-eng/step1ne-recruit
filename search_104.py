#!/usr/bin/env python3
"""104 職缺搜尋（公開資訊）——人選敲門用（2026-10-05 Jacky）。

用途：拿人選履歷想出的關鍵字，到 104 搜「最近有在徵這類職缺」的公司，
當開發客戶的名單；每筆附職缺連結與刊登日期，也是「寄開發信前 14 天內查過職缺」的證據。

只抓搜尋結果頁的公開欄位（公司名、職稱、刊登日、地區、薪資、連結），不抓聯絡人個資。
先開一次搜尋頁拿 session cookie，再打搜尋 API；每個關鍵字之間停 1～2 秒，不要打太兇。

用法：python3 search_104.py "供應鏈 採購主管" [天數=30]
"""
import datetime
import http.cookiejar
import json
import random
import sys
import time
import urllib.parse
import urllib.request

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/129.0 Safari/537.36')
# 2026-10-06 Jacky：藝珂人事顧問（Adecco）出現在人選敲門名單——同業刊的缺不會說客戶是誰，等於白打。
# 原本漏了「人事顧問」這種寫法，外商同業也要列英文名。
STAFFING = ('人力', '派遣', '人才顧問', '獵頭', '獵才', '管理顧問', '人資顧問', '人事顧問', '人力資源', '外包', '仲介',
            '藝珂', '萬寶華', '任仕達', '華德士', '保聖那', 'Adecco', 'Manpower', 'Randstad', 'Robert Walters',
            'Michael Page', 'PERSOL', 'Hays')


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(urllib.request.Request('https://www.104.com.tw/jobs/search/',
                                   headers={'User-Agent': UA, 'Accept-Language': 'zh-TW,zh;q=0.9'}), timeout=20).read()
    return op


def search(keywords, days=30, pages=2, opener=None):
    """回傳去重後的職缺清單：[{company, job, date, area, salary, url, keyword}]。"""
    op = opener or _opener()
    since = (datetime.date.today() - datetime.timedelta(days=days)).strftime('%Y%m%d')
    seen, out = set(), []
    for kw in keywords:
        for page in range(1, pages + 1):
            url = 'https://www.104.com.tw/jobs/search/api/jobs?' + urllib.parse.urlencode(
                {'keyword': kw, 'page': page, 'pagesize': 20, 'order': 15, 'jobsource': 'joblist_search', 'ro': 0})
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'zh-TW,zh;q=0.9',
                'Referer': 'https://www.104.com.tw/jobs/search/?keyword=' + urllib.parse.quote(kw)})
            try:
                d = json.loads(op.open(req, timeout=20).read())
            except Exception:
                break
            data = d.get('data')
            jobs = data if isinstance(data, list) else (data or {}).get('list') or []
            if not jobs:
                break
            for j in jobs:
                company = str(j.get('custName') or '').strip()
                job = str(j.get('jobName') or '').strip()
                date = str(j.get('appearDate') or '')
                if not company or not job or (date and date < since):
                    continue
                if any(w.lower() in company.lower() for w in STAFFING):
                    continue
                key = (company, job)
                if key in seen:
                    continue
                seen.add(key)
                link = (j.get('link') or {}).get('job') or ''
                if link.startswith('//'):
                    link = 'https:' + link
                sal_lo, sal_hi = j.get('salaryLow'), j.get('salaryHigh')
                out.append({'company': company, 'job': job, 'date': date,
                            'area': str((j.get('jobAddrNoDesc') or j.get('jobAddress') or ''))[:20],
                            'salary': f"{sal_lo}-{sal_hi}" if sal_lo else '',
                            'url': link, 'keyword': kw})
            time.sleep(random.uniform(1.0, 2.0))
    return out


if __name__ == '__main__':
    kw = sys.argv[1] if len(sys.argv) > 1 else '採購主管'
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    res = search([kw], days=days, pages=1)
    print(len(res))
    for r in res[:15]:
        print(r['date'], r['company'], '|', r['job'], '|', r['url'])
