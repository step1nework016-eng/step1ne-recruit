#!/usr/bin/env python3
"""company_jobs_104.py — 一家公司在 104 目前有哪些職缺（認識公司研究卡用，2026-10-14 Jacky）

104 的公司頁 API 會被 Cloudflare 擋，所以改用「公司名 + 職類關鍵字」搜尋，只留公司名對得上的職缺。
只抓公開欄位（職稱、刊登日、地區、薪資、連結）。每個關鍵字間停 1～2 秒。
用法：python3 company_jobs_104.py 小北百貨 [公司名比對字=小北]
"""
import json
import random
import sys
import time
import urllib.parse
import urllib.request

import search_104 as S

ROLES = ['財務', '會計', '稅務', '稽核', '採購', '商品', '營運', '資訊', '工程師', '人資', '行政', '企劃', '行銷', '物流', '倉儲',
         '店長', '督導', '區域', '主管', '經理', '協理', '總', '法務', '品管', '業務', '設計', '客服', '電商', '數據', '專案']


def jobs(name, match=None, roles=ROLES):
    match = match or name[:2]
    op = S._opener()
    seen, out = set(), []
    for rk in roles:
        kw = f'{name} {rk}'
        for page in (1, 2):
            url = 'https://www.104.com.tw/jobs/search/api/jobs?' + urllib.parse.urlencode(
                {'keyword': kw, 'page': page, 'pagesize': 20, 'order': 15, 'jobsource': 'joblist_search', 'ro': 0})
            req = urllib.request.Request(url, headers={'User-Agent': S.UA, 'Accept': 'application/json',
                                                       'Referer': 'https://www.104.com.tw/jobs/search/?keyword=' + urllib.parse.quote(kw)})
            try:
                d = json.loads(op.open(req, timeout=20).read())['data']
            except Exception:
                break
            L = d if isinstance(d, list) else (d or {}).get('list') or []
            hit = 0
            for j in L:
                if match not in str(j.get('custName') or ''):
                    continue
                hit += 1
                link = (j.get('link') or {}).get('job') or ''
                if link in seen:
                    continue
                seen.add(link)
                out.append({'job': str(j.get('jobName') or ''), 'date': str(j.get('appearDate') or ''),
                            'area': str(j.get('jobAddrNoDesc') or ''), 'salary': (f"{j.get('salaryLow')}-{j.get('salaryHigh')}" if j.get('salaryLow') else ''),
                            'url': link, 'role': str(j.get('jobRole') or ''), 'desc': ' '.join(str(j.get('description') or '').split())[:200]})
            if hit == 0:
                break
            time.sleep(random.uniform(0.8, 1.5))
        time.sleep(random.uniform(0.8, 1.5))
    return out


if __name__ == '__main__':
    r = jobs(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    print(len(r), '個職缺', file=sys.stderr)
