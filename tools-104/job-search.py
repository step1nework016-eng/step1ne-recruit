#!/usr/bin/env python3
"""
104人力銀行職缺爬蟲 v4 - 使用完整 snapshot（不加 -i）
加入隨機延遲防反爬蟲機制
"""

import subprocess
import re
import json
import sys
import time
import random
from datetime import datetime

def log_debug(message):
    """寫入 debug 日誌"""
    with open("/tmp/104-scraper-v4-debug.log", 'a', encoding='utf-8') as log:
        log.write(f"[{datetime.now()}] {message}\n")

def search_104_jobs(keyword, max_results=20):
    """搜尋 104 職缺"""
    log_debug(f"🔍 搜尋: {keyword}, 數量: {max_results}")
    
    # 步驟 1：訪問首頁建立 Session
    subprocess.run(['agent-browser', 'open', 'https://www.104.com.tw'], check=True, capture_output=True)
    subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '10000'], check=True, capture_output=True)
    
    # 隨機延遲 2-5 秒（防反爬蟲）
    delay = random.uniform(2, 5)
    log_debug(f"  ⏳ 延遲 {delay:.1f} 秒")
    time.sleep(delay)
    
    # 步驟 2：導航到搜尋頁
    url = f"https://www.104.com.tw/jobs/search/?keyword={keyword}"
    subprocess.run(['agent-browser', 'navigate', url], check=True, capture_output=True)
    subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '15000'], check=True, capture_output=True)
    
    # 隨機延遲 2-5 秒
    delay = random.uniform(2, 5)
    log_debug(f"  ⏳ 延遲 {delay:.1f} 秒")
    time.sleep(delay)
    
    # 步驟 3：取得完整 snapshot（不加 -i）
    result = subprocess.run(['agent-browser', 'snapshot'], capture_output=True, text=True)
    
    # 關閉瀏覽器
    subprocess.run(['agent-browser', 'close'], check=True, capture_output=True)
    
    # 步驟 4：解析 snapshot
    lines = result.stdout.split('\n')
    jobs = []
    
    i = 0
    while i < len(lines) and len(jobs) < max_results:
        line = lines[i].strip()
        
        # 找 heading level=2（職位名稱）
        if 'heading' in line and '[level=2]' in line:
            # 提取職位名稱
            match = re.search(r'- heading "([^"]+)"', line)
            if not match:
                i += 1
                continue
            
            job_title = match.group(1)
            
            # 下一行應該是職缺連結（  - link "..." [ref=...]:）
            # 再下一行是 URL（    - /url: https://...）
            if i+2 < len(lines):
                link_line = lines[i+1].strip()
                url_line = lines[i+2].strip()
                
                job_url_match = re.search(r'- /url: (https://www\.104\.com\.tw/job/[^\?\s]+)', url_line)
                
                if job_url_match:
                    job_url = job_url_match.group(1)
                    
                    # 再往下找公司連結（  - link "公司名稱" [ref=...]:）
                    company_name = "N/A"
                    if i+3 < len(lines):
                        company_line = lines[i+3].strip()
                        company_name_match = re.search(r'- link "([^"]+)"', company_line)
                        if company_name_match:
                            company_name = company_name_match.group(1)
                    
                    # 往後找地點和薪資（通常在後面 10 行內）
                    location = "N/A"
                    salary = "N/A"
                    
                    for j in range(i+4, min(i+20, len(lines))):
                        check_line = lines[j].strip()
                        
                        # 地點（包含 "市" 或 "區" 的 link）
                        if location == "N/A" and '- link "' in check_line and ('市' in check_line or '區' in check_line or '縣' in check_line):
                            loc_match = re.search(r'- link "([^"]+(?:市|區|縣)[^"]*)"', check_line)
                            if loc_match:
                                location = loc_match.group(1)
                        
                        # 薪資（包含 "元" 或 "薪" 的 link）
                        if salary == "N/A" and '- link "' in check_line and ('元' in check_line or '薪' in check_line or '待遇' in check_line):
                            salary_match = re.search(r'- link "([^"]+(?:元|薪|待遇)[^"]*)"', check_line)
                            if salary_match:
                                salary = salary_match.group(1)
                        
                        # 如果都找到了就停止
                        if location != "N/A" and salary != "N/A":
                            break
                    
                    jobs.append({
                        "company": company_name,
                        "job_title": job_title,
                        "location": location,
                        "salary": salary,
                        "url": job_url
                    })
                    
                    log_debug(f"  ✅ {company_name}: {job_title}")
        
        i += 1
    
    log_debug(f"✅ 找到 {len(jobs)} 個職缺")
    return jobs

def main():
    """主程式"""
    keyword = sys.argv[1] if len(sys.argv) > 1 else "AI工程師"
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    log_debug(f"開始處理: {keyword}, 數量: {max_results}")
    
    jobs = search_104_jobs(keyword, max_results)
    
    # 輸出 JSON
    print(json.dumps(jobs, ensure_ascii=False, indent=2))
    
    log_debug(f"✅ 全部完成，共處理 {len(jobs)} 家公司")

if __name__ == "__main__":
    main()
