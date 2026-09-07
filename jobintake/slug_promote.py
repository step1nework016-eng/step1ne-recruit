#!/usr/bin/env python3
"""把客戶 portal 建立的暫存網址轉成語意 slug。

## 為什麼需要這支

企業客戶在 portal 自己開一個缺時，Worker 只能給一個暫時代號當 slug
（`pending-<company_id>-<hash>`，見 step1ne-backoffice-worker/src/index.js
的 POST /portal/:token/jobs）——當下還沒有 JD、沒有職稱以外的任何資訊，
產不出有意義的網址。

問題是**後面沒有任何一步會把它換掉**。顧問核准 → AI 擬稿 → 重產頁面 →
部署，一路帶著暫存代號跑到線上，2026-09-07 查到有 6 筆長這樣公開在架上：
`/jobs/pending-co_802bdd6b-3b807a75/`。對照人工上架的是
`/jobs/ehs-engineer-yunlin/`。

## 轉正的時機只有一次

**第一次公開之前。** 網址一旦上線就不能再改（Worker 用 job_slug 認職缺、
社群貼文與應徵連結都指向它）——那時候要改就得走 301 並且要 Jacky 點頭。
所以這裡有一條硬性守則：**已經有靜態頁、或已經有人應徵過的職缺，一律
不碰**，只回報，不自動改名。

## 名字哪裡來

AI 擬稿（jd_ai_draft_tick.py）時一起產出的 `slug_suggestion`。
產不出合法的名字就**不轉正、也不發布**——寧可讓顧問收到一則「這個缺還沒
上架」的通知，也不要再放一個暫存網址上線。
"""
import os
import re

SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')

PENDING_RE = re.compile(r'^pending-')
VALID_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

# slug 出現在網址、D1、社群貼文連結裡。這幾張表都存了 job_slug，改名要一起改，
# 不然顧問後台會看到一個查不到職缺的孤兒紀錄。
SLUG_REF_TABLES = [
    ('applications', 'job_slug'),
    ('portal_imports', 'job_slug'),
    ('job_forms', 'job_slug'),
    ('job_candidate_summaries', 'job_slug'),
    ('sourced_candidates', 'job_slug'),
    ('pipeline_events', 'job_slug'),
    ('screenings', 'job_slug'),
]


def is_pending(slug):
    return bool(PENDING_RE.match(slug or ''))


def sanitize(raw):
    """AI 給的名字要洗過才敢用——它偶爾會回中文、底線或整句話。

    洗不成合法 slug 就回 None，讓呼叫端去擋發布，不要硬湊一個出來。
    """
    s = (raw or '').strip().lower()
    s = s.replace('_', '-')
    s = re.sub(r'[^a-z0-9-]+', '-', s)
    s = re.sub(r'-{2,}', '-', s).strip('-')
    if not s or not VALID_RE.match(s):
        return None
    if PENDING_RE.match(s):
        return None
    if len(s) < 3 or len(s) > 60:
        return None
    return s


def taken(slug, d1):
    """網址已經被佔走了嗎——D1 有這筆，或網站上已經有這個資料夾。"""
    if os.path.isdir(os.path.join(SITE, 'jobs', slug)):
        return True
    return bool(d1(f"SELECT slug FROM jobs WHERE slug='{slug}' LIMIT 1"))


def unique(base, d1):
    if not taken(base, d1):
        return base
    for n in range(2, 20):
        cand = f'{base}-{n}'
        if not taken(cand, d1):
            return cand
    return None


def already_public(slug, d1):
    """已經對外過的職缺不准改名——改了就是斷連結。

    兩個判準，任一個成立就當作已公開：
    - 網站上已經有 jobs/<slug>/index.html（Google 收過、社群貼過）
    - D1 已經有人用這個 slug 應徵（應徵連結帶的就是這個網址）
    """
    if os.path.exists(os.path.join(SITE, 'jobs', slug, 'index.html')):
        return '網站上已經有這個職缺頁'
    rows = d1(f"SELECT COUNT(*) AS n FROM applications WHERE job_slug='{slug}'")
    if rows and int(rows[0].get('n') or 0) > 0:
        return f"已經有 {rows[0]['n']} 位人選用這個網址應徵過"
    return None


def promote(slug, suggestion, d1, run_sql):
    """回傳 (新slug, 錯誤說明)。錯誤不是 None 就代表**不可以發布**。

    run_sql 是真的會寫入的執行函式（跟唯讀的 d1 分開傳，
    是為了讓呼叫端在 dry-run 時可以只傳一個什麼都不做的假函式）。
    """
    if not is_pending(slug):
        return slug, None

    blocker = already_public(slug, d1)
    if blocker:
        return slug, (f'這個暫存網址已經公開了（{blocker}），不能直接改名——'
                      f'改網址要先跟 Jacky 確認並做 301，不是這支腳本該自己決定的事。')

    base = sanitize(suggestion)
    if not base:
        return slug, (f'沒有可用的語意網址（AI 給的是 {suggestion!r}），未轉正不得公開。'
                      f'請顧問在職缺編輯器補一個英文網址再重新存檔。')

    new = unique(base, d1)
    if not new:
        return slug, f'語意網址 {base} 連同編號後綴都被佔滿了，需要人工命名。'

    run_sql(f"UPDATE jobs SET slug='{new}' WHERE slug='{slug}'")
    for table, col in SLUG_REF_TABLES:
        # 表可能還沒建（不同時期的 migration），失敗就跳過——
        # 這些表在「還沒公開」的職缺上本來就不該有資料，是防禦性的。
        try:
            run_sql(f"UPDATE {table} SET {col}='{new}' WHERE {col}='{slug}'")
        except Exception:
            pass
    return new, None
