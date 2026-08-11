#!/usr/bin/env python3
"""
104 公司聯絡資訊爬蟲
從職缺頁面 → 公司頁面 → 提取聯絡人、電話、Email
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
    with open("/tmp/104-company-contact-debug.log", 'a', encoding='utf-8') as log:
        log.write(f"[{datetime.now()}] {message}\n")

def get_company_page_url(job_url):
    """從職缺頁面提取公司頁面 URL"""
    log_debug(f"📄 訪問職缺頁面: {job_url}")
    
    subprocess.run(['agent-browser', 'open', job_url], check=True, capture_output=True)
    subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '10000'], check=True, capture_output=True)
    
    # 隨機延遲 2-5 秒（防反爬蟲）
    delay = random.uniform(2, 5)
    log_debug(f"  ⏳ 延遲 {delay:.1f} 秒")
    time.sleep(delay)
    
    # 取得 snapshot
    result = subprocess.run(['agent-browser', 'snapshot'], capture_output=True, text=True)
    
    # 找公司頁面連結（包含 /company/）
    company_url = None
    for line in result.stdout.split('\n'):
        if '/company/' in line and '/url:' in line:
            # 支援兩種格式：https://www.104.com.tw/company/xxx 或 //www.104.com.tw/company/xxx
            match = re.search(r'/url:\s*((?:https?:)?//www\.104\.com\.tw/company/[^\s\?#]+)', line)
            if match:
                url = match.group(1)
                # 補上 https: 如果缺少
                if url.startswith('//'):
                    url = f"https:{url}"
                company_url = url
                break
    
    log_debug(f"  公司頁面: {company_url}")
    return company_url

def extract_company_contact(company_url):
    """從公司頁面提取聯絡資訊"""
    log_debug(f"📞 訪問公司頁面: {company_url}")
    
    subprocess.run(['agent-browser', 'navigate', company_url], check=True, capture_output=True)
    subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '10000'], check=True, capture_output=True)
    
    # 隨機延遲 2-5 秒（防反爬蟲）
    delay = random.uniform(2, 5)
    log_debug(f"  ⏳ 延遲 {delay:.1f} 秒")
    time.sleep(delay)
    
    # 取得 snapshot
    result = subprocess.run(['agent-browser', 'snapshot'], capture_output=True, text=True)
    lines = result.stdout.split('\n')
    
    contact_info = {
        "contact_person": None,
        "phone": None,
        "fax": None,
        "email": None,
        "address": None,
        "website": None
    }
    
    # 解析聯絡資訊（heading [level=3] + 下一行 paragraph）
    for i, line in enumerate(lines):
        # 聯絡人
        if 'heading "聯絡人"' in line and i+1 < len(lines):
            next_line = lines[i+1].strip()
            if '- paragraph:' in next_line:
                name = next_line.replace('- paragraph:', '').strip()
                if name and name != "暫不提供":
                    contact_info['contact_person'] = name
        
        # 電話
        if 'heading "電話"' in line and i+1 < len(lines):
            next_line = lines[i+1].strip()
            if '- paragraph:' in next_line:
                phone = next_line.replace('- paragraph:', '').strip()
                if phone and phone != "暫不提供":
                    contact_info['phone'] = phone
        
        # 傳真
        if 'heading "傳真"' in line and i+1 < len(lines):
            next_line = lines[i+1].strip()
            if '- paragraph:' in next_line:
                fax = next_line.replace('- paragraph:', '').strip()
                if fax and fax != "暫不提供":
                    contact_info['fax'] = fax
        
        # 地址
        if 'heading "地址"' in line and i+1 < len(lines):
            next_line = lines[i+1].strip()
            if '- paragraph:' in next_line:
                addr = next_line.replace('- paragraph:', '').strip()
                if addr and addr != "暫不提供":
                    contact_info['address'] = addr
        
        # 公司網址（格式：- link "URL" [ref=xxx]: → - /url: https://...）
        if 'heading "公司網址"' in line and i+2 < len(lines):
            # 下一行是 link，再下一行是 /url:
            url_line = lines[i+2].strip()
            if '- /url:' in url_line:
                url_match = re.search(r'- /url:\s*(https?://[^\s]+)', url_line)
                if url_match:
                    contact_info['website'] = url_match.group(1).strip()
        
        # Email（可能出現在任何地方）
        if '@' in line:
            email_match = re.search(r'([\w\.-]+@[\w\.-]+\.\w+)', line)
            if email_match:
                contact_info['email'] = email_match.group(1).strip()
    
    log_debug(f"  聯絡人: {contact_info['contact_person']}")
    log_debug(f"  電話: {contact_info['phone']}")
    log_debug(f"  Email: {contact_info['email']}")
    log_debug(f"  網址: {contact_info['website']}")
    
    return contact_info

def search_email_from_website(website_url):
    """從公司官網搜尋 Email"""
    if not website_url or not website_url.startswith('http'):
        return None
    
    log_debug(f"🌐 搜尋官網 Email: {website_url}")
    
    # 嘗試常見的聯絡頁面
    contact_pages = [
        website_url,
        f"{website_url.rstrip('/')}/contact",
        f"{website_url.rstrip('/')}/contact-us",
        f"{website_url.rstrip('/')}/about"
    ]
    
    for url in contact_pages:
        try:
            subprocess.run(['agent-browser', 'navigate', url], check=True, capture_output=True, timeout=10)
            subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '5000'], check=True, capture_output=True, timeout=10)
            
            # 隨機延遲 2-5 秒（防反爬蟲）
            delay = random.uniform(2, 5)
            time.sleep(delay)
            
            result = subprocess.run(['agent-browser', 'snapshot'], capture_output=True, text=True, timeout=5)
            
            # 搜尋 Email
            email_match = re.search(r'([\w\.-]+@[\w\.-]+\.\w+)', result.stdout)
            if email_match:
                email = email_match.group(1)
                log_debug(f"  ✅ 找到 Email: {email}")
                return email
        except:
            continue
    
    log_debug(f"  ❌ 官網未找到 Email")
    return None

def process_job(job_data):
    """處理單個職缺，提取完整聯絡資訊"""
    company_name = job_data.get('company', 'Unknown')
    job_url = job_data.get('url', '')
    
    log_debug(f"\n{'='*60}")
    log_debug(f"🏢 處理公司: {company_name}")
    
    # 步驟 1：從職缺頁面找公司頁面 URL
    company_url = get_company_page_url(job_url)
    
    if not company_url:
        log_debug(f"❌ 無法找到公司頁面")
        return {**job_data, "phone": "待查", "email": "待查", "contact_person": "待查"}
    
    # 步驟 2：從公司頁面提取聯絡資訊
    contact_info = extract_company_contact(company_url)
    
    # 步驟 3：如果沒有 Email，嘗試從官網找
    if not contact_info['email'] and contact_info['website']:
        contact_info['email'] = search_email_from_website(contact_info['website'])
    
    # 整合資料
    result = {
        **job_data,
        "phone": contact_info['phone'] or "待查",
        "email": contact_info['email'] or "待查",
        "contact_person": contact_info['contact_person'] or "您好",
        "fax": contact_info['fax'] or "",
        "address": contact_info['address'] or "",
        "website": contact_info['website'] or ""
    }
    
    log_debug(f"✅ 完成: {company_name}")
    return result

def main():
    """主程式"""
    if len(sys.argv) < 2:
        print("用法: python3 scraper-104-company-contact.py <jobs.json>", file=sys.stderr)
        sys.exit(1)
    
    input_file = sys.argv[1]
    
    # 開啟瀏覽器
    subprocess.run(['agent-browser', 'open', 'https://www.104.com.tw'], check=True, capture_output=True)
    subprocess.run(['agent-browser', 'wait', '--load', 'networkidle', '--timeout', '10000'], check=True, capture_output=True)
    
    # 讀取職缺列表
    with open(input_file, 'r', encoding='utf-8') as f:
        jobs = json.load(f)
    
    log_debug(f"開始處理 {len(jobs)} 個職缺")
    
    # 處理每個職缺
    detailed_jobs = []
    for job in jobs:
        try:
            detailed = process_job(job)
            detailed_jobs.append(detailed)
        except Exception as e:
            log_debug(f"❌ 處理失敗: {job.get('company')} - {e}")
            detailed_jobs.append({**job, "phone": "待查", "email": "待查", "contact_person": "待查"})
    
    # 關閉瀏覽器
    subprocess.run(['agent-browser', 'close'], check=True, capture_output=True)
    
    # 輸出 JSON
    print(json.dumps(detailed_jobs, ensure_ascii=False, indent=2))
    
    log_debug(f"✅ 全部完成，共處理 {len(detailed_jobs)} 家公司")

if __name__ == "__main__":
    main()
