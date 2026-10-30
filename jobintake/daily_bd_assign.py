#!/usr/bin/env python3
"""每天早上自動分配新的開發公司給顧問（2026-10-30 Jacky 拍板：選 A）。

為什麼要有這支：hiring_leads_scan.py 每天把 104「正在徵人的公司」丟進 bd_hiring_leads，
但只放在「待挑選」，沒有人挑就不會進電話名單——財會類累積了 483 家沒人處理。
Jacky 要的是：每天早上系統自己挑 N 家（預設 10 家）放進 Phoebe 的「今天要打」。

做的事（工作日早上跑一次）：
  1. 先跑一次 hiring_leads_scan.py 無經驗培訓 14（BIM／繪圖／助理工程師，跟手上 200 多位 BIM 新鮮人對得上）
  2. 從 bd_hiring_leads 挑還沒處理（status='new'）、而且系統裡還沒開發過的公司：
     無經驗培訓 優先，不夠再從 財務會計 補；同類別裡刊登越新、缺越多的先
  3. 每家到 104 公司頁抓「公開的」電話、人資稱呼、地址——沒公開電話的跳過（打不了）
  4. 呼叫後台 /admin/hiring-leads/<id>/pick 加入開發（後台會擋掉客戶名單上的公司），
     再把電話、窗口、104 公司頁補進 bd_outreach
  5. 加入的名單 batch＝hiring-<今天>，電話名單「今天要打」會排在優先名單、到期追蹤後面
不寄任何信、不發 TG；只寫名單。

用法：
    python3 jobintake/daily_bd_assign.py              # 正式跑（預設 10 家給 Phoebe）
    python3 jobintake/daily_bd_assign.py --dry-run    # 只列出會挑哪幾家
    python3 jobintake/daily_bd_assign.py --n 15 --assignee Phoebe --no-scan
"""
import sys as _sys
# 2026-10-30 Jacky：「停掉」——104 只能在 Jacky 監督下碰，不准排程自動跑。要恢復請 Jacky 本人說。
if '--i-know-jacky-approved' not in _sys.argv:
    print('已停用（2026-10-30 Jacky）：每日開發自動分配（會查 104 公司資料）。104 只能在 Jacky 監督下操作。'); _sys.exit(0)
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import consultant_ops as c  # noqa: E402
import search_104 as s104  # noqa: E402

CATS = ['無經驗培訓', '財務會計']
BY = '系統自動分配'


def co_info(op, job_url):
    """從職缺網址找到公司頁，回傳 (公司頁網址, 電話, 人資稱呼, 地址)；抓不到回 None。"""
    m = re.search(r'/job/([a-z0-9]+)', job_url or '')
    if not m:
        return None
    no = m.group(1)
    h = {'User-Agent': s104.UA, 'Accept': 'application/json', 'Referer': f'https://www.104.com.tw/job/{no}'}
    try:
        d = json.loads(op.open(urllib.request.Request(f'https://www.104.com.tw/job/ajax/content/{no}', headers=h), timeout=20).read())['data']
    except Exception:
        return None
    cust_url = ((d.get('header') or {}).get('custUrl') or '').split('?')[0]
    cid = cust_url.rstrip('/').split('/')[-1] if cust_url else ''
    job_phone = [p for p in ((d.get('contact') or {}).get('phone') or []) if p]
    hr = (d.get('contact') or {}).get('hrName') or ''
    phone, addr = '', ''
    if cid:
        try:
            cd = json.loads(op.open(urllib.request.Request(f'https://www.104.com.tw/api/companies/{cid}/content',
                                                           headers={**h, 'Referer': cust_url}), timeout=20).read())['data']
            p = str(cd.get('phone') or '').strip()
            if p and '不提供' not in p:
                phone = p
            addr = cd.get('address') or ''
            hr = hr or cd.get('hrName') or ''
        except Exception:
            pass
    phone = phone or (job_phone[0] if job_phone else '')
    return cust_url, phone, hr, addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=10)
    ap.add_argument('--assignee', default='Phoebe')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-scan', action='store_true')
    a = ap.parse_args()
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')

    if not a.no_scan:
        subprocess.run([sys.executable, os.path.join(ROOT, 'hiring_leads_scan.py'), '無經驗培訓', '14'], cwd=ROOT,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)

    # 今天已經分配過就只補到 N
    done_today = c.rows("SELECT COUNT(*) n FROM bd_hiring_leads WHERE decided_by=? AND decided_at>=?", [BY, today])[0]['n']
    need = max(0, a.n - int(done_today or 0))
    if need == 0:
        print(f'今天已經分配 {done_today} 家，不用再補')
        return

    known = {r['company'] for r in c.rows("SELECT DISTINCT company FROM bd_outreach", [])}
    op = s104._opener()
    picked, skipped = [], []
    for cat in CATS:
        if len(picked) >= need:
            break
        leads = c.rows("SELECT id, company, jobs_json FROM bd_hiring_leads WHERE status='new' AND category=? "
                       "ORDER BY latest_date DESC, job_count DESC LIMIT 120", [cat])
        for ld in leads:
            if len(picked) >= need:
                break
            if ld['company'] in known:
                continue
            try:
                jobs = json.loads(ld['jobs_json'] or '[]')
            except Exception:
                jobs = []
            info = co_info(op, (jobs[0] if jobs else {}).get('url'))
            time.sleep(1.2)
            if not info or not info[1]:
                skipped.append((ld['company'], '104 沒公開電話'))
                continue
            cust_url, phone, hr, addr = info
            row = {'lead': ld['id'], 'company': ld['company'], 'cat': cat, 'phone': phone, 'hr': hr, 'addr': addr,
                   'url': cust_url, 'job': (jobs[0] if jobs else {}).get('job', '')}
            if a.dry_run:
                picked.append(row)
                continue
            r = c.bo(f"/admin/hiring-leads/{ld['id']}/pick", {'by': BY, 'assignee': a.assignee})
            if not r.get('ok'):
                skipped.append((ld['company'], r.get('error') or '加入失敗'))
                continue
            c.d1("UPDATE bd_outreach SET company_phone=COALESCE(NULLIF(company_phone,''),?), hr_contact=COALESCE(NULLIF(hr_contact,''),?), "
                 "company_104_url=COALESCE(NULLIF(company_104_url,''),?), phone_source=COALESCE(phone_source,'104 公司頁（每日自動分配）') WHERE id=?",
                 [phone, hr or None, cust_url or None, r['outreach_id']])
            row['outreach_id'] = r['outreach_id']
            picked.append(row)
            known.add(ld['company'])

    print(json.dumps({'date': today, 'dry_run': a.dry_run, 'assignee': a.assignee, 'picked': len(picked),
                      'list': [f"{p['cat']}｜{p['company']}｜{p['job']}｜{p['phone']}｜{p['hr']}" for p in picked],
                      'skipped': skipped[:20]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
