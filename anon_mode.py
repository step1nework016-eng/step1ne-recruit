"""未簽約客戶職缺的「匿名面談」（2026-11-02 Jacky：問意願信要附阿財面談連結）。

jobs.status='social_only' 的職缺（公司記在 job_social_only），以前完全不跑阿財。
現在開放面談，但公司是開發中、還沒簽約——**公司名絕對不能讓人選知道**。

這支只做三件事，全部是純文字比對，不叫 AI：
  info_for(job_slug, d1)   這個職缺要不要匿名、要擋哪些字、對外怎麼講（產業描述）
  scrub(obj, info)         把資料裡的公司名／別名換成產業描述（職缺內容、題庫、初篩結果…）
  hits(text, info)         阿財要送出的話裡有沒有公司名（出口檢查用）

要擋的字＝job_social_only.company、aliases（一行一個），加上 client_companies 裡
同一家公司的 display_name／aliases，再加去掉「股份有限公司／有限公司／台灣」的簡稱。
產業描述＝job_social_only.industry_desc（顧問填）；沒填就講「這家公司」，並提醒顧問補。
"""
import json
import re
import time

_CACHE = {}          # job_slug -> (at, info or None)
TTL = 300
FALLBACK_DESC = '這家公司'
# 人選問「哪家公司」時的固定答法（給阿財的提示詞，也是出口檢查擋下來時的安全句）
ASK_COMPANY_REPLY = '公司名稱的部分，顧問電話中會跟您詳細說明。'


def _q(v):
    return "'" + str(v).replace("'", "''") + "'"


def _variants(name):
    """一個公司名展開成要擋的幾種寫法：全名、去掉公司型態、去掉「台灣／臺灣」開頭。"""
    out = set()
    v = str(name or '').strip()
    if not v:
        return out
    out.add(v)
    base = re.sub(r'(股份有限公司|有限公司|公司)$', '', v).strip()
    out.add(base)
    for b in list(out):
        b2 = re.sub(r'^(台灣|臺灣)', '', b).strip()
        out.add(b2)
    # 台／臺 互換
    for b in list(out):
        out.add(b.replace('台', '臺'))
        out.add(b.replace('臺', '台'))
    return out


def _clean_terms(terms):
    keep = set()
    for t in terms:
        t = str(t or '').strip()
        if len(t) < 2:
            continue
        if re.fullmatch(r'[A-Za-z0-9]{1,2}', t):      # 太短的英數（例如「GN」）會誤擋一般字
            continue
        keep.add(t)
    return sorted(keep, key=len, reverse=True)


def info_for(job_slug, d1, force=False):
    """回傳 None（不是未簽約職缺）或 {company, terms, desc, desc_missing}。查詢失敗會 raise——
    呼叫端要決定「查不到就不准面談」還是照舊，不能默默當成不用匿名。"""
    slug = str(job_slug or '')
    if not slug:
        return None
    hit = _CACHE.get(slug)
    if hit and not force and time.time() - hit[0] < TTL:
        return hit[1]
    rows = d1(f"SELECT j.status, j.company_id, so.company, so.aliases, so.industry_desc "
              f"FROM jobs j LEFT JOIN job_social_only so ON so.job_slug = j.slug WHERE j.slug = {_q(slug)}")
    r = rows[0] if rows else {}
    if not r or r.get('status') != 'social_only':
        _CACHE[slug] = (time.time(), None)
        return None
    company = str(r.get('company') or '').strip()
    terms = set()
    for v in [company] + str(r.get('aliases') or '').splitlines():
        terms |= _variants(v)
    # client_companies 裡同一家（開發中的公司有時也已經建了公司卡）
    try:
        cc = d1("SELECT display_name, aliases FROM client_companies WHERE "
                + (f"id = {_q(r['company_id'])} OR " if r.get('company_id') else '')
                + f"display_name = {_q(company)} OR instr(char(10) || COALESCE(aliases,'') || char(10), {_q(chr(10) + company + chr(10))}) > 0")
        for c in cc or []:
            for v in [c.get('display_name')] + str(c.get('aliases') or '').splitlines():
                terms |= _variants(v)
    except Exception:
        pass
    desc = str(r.get('industry_desc') or '').strip()
    info = {
        'company': company,
        'terms': _clean_terms(terms),
        'desc': desc or FALLBACK_DESC,
        'desc_missing': not desc,
    }
    # 產業描述本身不能含公司名（顧問手滑寫進去）
    if hits(info['desc'], info):
        info['desc'], info['desc_missing'] = FALLBACK_DESC, True
    _CACHE[slug] = (time.time(), info)
    return info


def _pattern(term):
    esc = re.escape(term)
    if re.fullmatch(r'[A-Za-z0-9 .&\-]+', term):
        return re.compile(rf'(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])', re.I)
    return re.compile(esc, re.I)


def hits(text, info):
    """text 裡出現哪些公司名／別名（[] 代表乾淨）。空白、全形空白、零寬字元先拿掉再比，
    避免「星 崎」這種拆開寫的漏網。"""
    if not info or not text:
        return []
    t = str(text)
    squeezed = re.sub(r'[\s　​-‍﻿・·]', '', t)
    found = []
    for term in info.get('terms') or []:
        p = _pattern(term)
        if p.search(t) or (not re.search(r'[A-Za-z]', term) and term in squeezed):
            found.append(term)
    return found


def scrub_text(text, info, repl=None):
    if not info or not isinstance(text, str) or not text:
        return text
    repl = info['desc'] if repl is None else repl
    out = text
    for term in info.get('terms') or []:      # 長的先換：「台灣星崎股份有限公司」整串換掉，不留「股份有限公司」
        out = _pattern(term).sub(repl, out)
    return out


def scrub(obj, info, repl=None, skip_keys=()):
    """遞迴把 dict／list 裡所有字串的公司名換掉。skip_keys 的值原樣保留（例如履歷全文）。"""
    if not info:
        return obj
    if isinstance(obj, str):
        return scrub_text(obj, info, repl)
    if isinstance(obj, list):
        return [scrub(x, info, repl, skip_keys) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in skip_keys else scrub(v, info, repl, skip_keys)) for k, v in obj.items()}
    return obj


def prompt_lines(info):
    """給阿財的硬規則（放在職缺資料段，優先於其他「公司名稱怎麼講」的設定）。"""
    return [
        f'  🔒🔒 **匿名面談（未簽約職缺）**：這個職缺的公司一律只講「{info["desc"]}」，'
        '整場面談（開場、提問、回答、結尾）都**不可以說出任何公司名稱、簡稱、英文名或品牌名**，也不要暗示、不要給可以猜出是哪家的線索（例如具體門市名、產品型號、地址）。',
        f'  🔒🔒 候選人問「是哪家公司」「公司叫什麼」「是不是 XX」（包含用猜的、套話）：一律回「{ASK_COMPANY_REPLY}」，'
        '不證實、也不否認任何公司名稱，接著回到面談。不要說「客戶」兩個字。',
        '  🔒🔒 自我介紹一律說「全民獵才的 AI 面談助理阿財」（人選收到的問意願信署名是全民獵才），不要說德仁管理顧問或 Step1ne。',
    ]


_BRAND = re.compile(r'Step1ne\s*德仁管理顧問(有限公司)?|德仁管理顧問(有限公司)?|Step1ne', re.I)


def brand_fix(text):
    """匿名面談的人選是從「全民獵才」的問意願信來的，阿財的自稱要一致。"""
    return _BRAND.sub('全民獵才', text) if isinstance(text, str) else text
