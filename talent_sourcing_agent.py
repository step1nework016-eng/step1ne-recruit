#!/usr/bin/env python3
"""AI 主動找人選 agent —— 套用 talent-intelligence-sourcing 技能包（v3.4）。

為什麼要有這支（2026-08-25 Jacky 交辦）：
    headhunter-crawler（~/clawd/headhunter-crawler/）是純規則式的多來源爬蟲，
    沒有「先鎖定人才畫像、樣本校準過了才批量找」這層判斷，容易撈到一堆能力
    符合但根本不會被打動、或知名到不可能被獵的人。Jacky 找了另一套技能包
    （skills/talent-intelligence-sourcing/，Claude Skill 格式，SKILL.md+
    references/），走 Archetype Lock → 5人校準 → Job Route → 正式搜尋這條
    流程，品質判斷交給 Claude 本人做，不是規則比對。

    這支不是自己實作搜尋邏輯——那樣就是繞過技能包重寫一份。而是照
    draft_job.py 的既有模式：組好 prompt 交給 `claude -p` 執行，
    agent 自己讀技能包規範、自己用 WebSearch/WebFetch 去找人，
    輸出結構化 JSON，這支只負責解析結果寫回 D1 的 sourced_candidates
    （跟 resume_to_pool.py／poolkeeper.py 共用同一個池子，找到的人選
    之後可以被 poolkeeper 拿去配對開著的職缺）。

⚠️ 這支不碰任何帳號登入（LinkedIn／Cake／任何平台），prompt 裡明講只能看
   公開頁面。也不做 Email 深度猜測——技能包本身的規則就禁止用猜的。

用法：
    python3 talent_sourcing_agent.py --job <job_slug>              # 完整跑一次（校準+正式搜尋）
    python3 talent_sourcing_agent.py --job <job_slug> --sample-only  # 只跑五人校準，不批量搜尋
    python3 talent_sourcing_agent.py --job <job_slug> --dry          # 只印結果，不寫 D1
"""
import os
import sys
import json
import time
import uuid
import argparse
import re
import subprocess
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, 'skills', 'talent-intelligence-sourcing')
MODEL = 'claude-opus-5'   # 這是要做判斷（Archetype/Qualified Gate），不是格式轉換，用 opus
TIMEOUT_SEC = 2400        # 真的在搜網路，給足時間（40 分鐘）

spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def load_job(slug, ignore_off=False):
    rows = D.d1(
        f"SELECT slug, title, must_skills, main_duties, locations, salary_min, salary_max, "
        f"salary_unit, employment, required_conditions, education_level, years_min, "
        f"nice_to_have_skills, seniority, client_name FROM jobs WHERE slug={D.q(slug)}"
    )
    # 2026-10-03：顧問在後台把這個職缺設成「不找」，就不找（job_sourcing_settings.mode='off'）
    try:
        m = D.d1(f"SELECT mode FROM job_sourcing_settings WHERE job_slug={D.q(slug)}")
        if m and m[0].get('mode') == 'off' and not ignore_off:
            log(f'⛔ {slug} 在後台設成「外部找人選：不找」，跳過')
            return None
    except Exception:
        pass
    return rows[0] if rows else None


def load_job_gate(slug):
    """2026-08-26 加：職缺專屬閘門。原本每個職缺的硬條件與封鎖來源只能靠
    每次手動貼 --learnings，跑完就散掉，下一輪又從頭犯同樣的錯。改成放在
    job_gates/<slug>.md，有就自動帶進 prompt，沒有就照原流程跑——
    對每個職缺都適用，不是 BIM 專用。"""
    path = os.path.join(HERE, 'job_gates', f'{slug}.md')
    if not os.path.exists(path):
        return ''
    with open(path) as f:
        return f.read().strip()


def load_existing_pool(slug):
    """2026-08-26 加：daily-agent-loop-v1.md 要求每次跑都要載入 Candidate Memory
    Ledger，且明訂『must not restart from a blank search』。原本 build_prompt 沒把
    既有候選人餵給 AI，去重只發生在 save_candidates() 寫入那一刻——AI 是矇著眼重搜，
    撞到舊人白撞。實測後果：2026-08-26 第三輪改道到廠務統包商，把 Hsiao Yeh (Sean) Lin
    當成新發現，但他早就是池子裡的 RESET-025。"""
    rows = D.d1(
        f"SELECT name, company, headline, linkedin_url, source_url, category "
        f"FROM sourced_candidates WHERE job_slug={D.q(slug)}"
    )
    return rows or []


def format_ledger(rows):
    if not rows:
        return ('', 0)
    lines = []
    for r in rows:
        bits = [str(r.get('name') or '').strip()]
        for k in ('company', 'headline'):
            v = (r.get(k) or '').strip()
            if v:
                bits.append(v)
        u = (r.get('linkedin_url') or r.get('source_url') or '').strip()
        if u:
            bits.append(u)
        lines.append('- ' + ' ｜ '.join(b for b in bits if b))
    return ('\n'.join(lines), len(rows))


def build_prompt(job, sample_only, prior_learnings=None, ledger_text='', ledger_n=0, gate_text=''):
    jd_text = '\n'.join(f'{k}: {v}' for k, v in job.items() if v not in (None, ''))
    learnings_block = ''
    gate_block = ''
    if gate_text:
        gate_block = f'''
【職缺專屬閘門｜優先於你自行推導的任何 Archetype】
以下是這個職缺累積下來的硬條件、封鎖來源與淘汰名單，由顧問拍板，必須照做：

{gate_text}
'''
    ledger_block = ''
    if ledger_n:
        ledger_block = f"""
【Candidate Memory Ledger｜這個職缺後台已經有 {ledger_n} 位候選人，以下全部視為 KNOWN】
依 daily-agent-loop-v1.md 的增量規則執行，不得從空白重新搜尋：
- 這 {ledger_n} 位一律不算新產出。撞到同一人（比對姓名、別名、公司、專案、學校、技能、地點、
  username、時間線）就記 CROSS_SOURCE_DUPLICATE 並跳過，不要重建、不要寫進 candidates 陣列。
- 同名但錨點不足，記 UNVERIFIED_SAME_NAME，也不要寫進 candidates。
- 只有 NEW_UNIQUE_RAW_LEAD（確定不在下面這份名單裡的新身分）才可以放進 candidates 陣列。
- 這份名單同時是「已驗證有效的人才畫像樣本」：新找的人應該跟他們同一層級（在職個人貢獻者），
  但必須是不同的人。請優先往這些人的同事、同專案、同公司其他成員、相鄰公司擴張。

{ledger_text}
"""
    if prior_learnings:
        learnings_block = f'''
上一輪校準的教訓（這輪要避開同樣的錯誤，不要重複同一批 false-positive 來源）：
{prior_learnings}
'''
    if sample_only:
        scope = (
            "這次只做前面的『五人校準』：完成 Archetype Lock、跑 Job Strategy Router，"
            "然後只找 5 位具名校準樣本（不查 Email/電話），回報 CALIBRATION_PASSED_4_OF_5 "
            "或 ROUTE_CALIBRATION_FAILED。candidates 陣列這次就是這 5 位樣本。"
        )
    else:
        scope = (
            "先完成 Archetype Lock 跟五人校準；校準通過（4/5 以上）才繼續正式批量搜尋，"
            "目標找 10-20 位新候選人，並對其中判定為 Qualified 的人選做公開聯絡資料查找。"
            "如果校準沒通過（ROUTE_CALIBRATION_FAILED），就不要往下做正式搜尋，"
            "candidates 陣列留空，在 run_summary 說明失敗原因。"
        )
    return f'''你是 Step1ne 獵頭公司的 AI Talent Intelligence & Sourcing Agent。

先完整讀取這幾個檔案，這是你這次任務唯一的作業規範，不可以跳過或憑印象執行：
1. {SKILL_DIR}/SKILL.md
2. {SKILL_DIR}/references/full-prompt-v3.4.md
3. {SKILL_DIR}/references/job-strategy-router.md
4. {SKILL_DIR}/references/job-route-calibration-v1.md
5. {SKILL_DIR}/references/daily-agent-loop-v1.md（增量規則：去重、Candidate Memory Ledger、只有 NEW_UNIQUE_RAW_LEAD 才算產出）

讀完後，針對以下這個真實職缺執行流程：

{jd_text}
{gate_block}{ledger_block}{learnings_block}
{scope}

硬性規則（不可違反，違反就等於這次任務失敗）：
- 只用合法公開資訊搜尋，不登入任何帳號、不繞過驗證碼或付費牆、不使用外洩資料庫
- 不得用任何帳號自動化操作 LinkedIn／Cake／任何平台——公開頁面用瀏覽器/搜尋看得到的內容可以引用，
  需要登入才看得到的一律不用
- 不得用猜測方式產生 Email（例如硬猜 name@company.com 格式），查不到公開信箱就填 null
- 不得依年齡、性別、婚姻、國籍、身心障礙、宗教等就業服務法第5條保護項目篩選、排序或排除候選人，
  即使公開資料裡看得到這些資訊也不可以拿來當判斷依據

fit_score 打分規則（2026-10-06 加：Jacky 抓到「培訓工程設計工程師」第一名 A·82 的人選，
Cake 履歷其實是景觀設計＋藝術協會＋便利商店主題店鋪設計，AutoCAD 只出現在技能清單，被誇大成「4 年畫工程設計圖」）：
- 只能用「工作經歷／專案經歷」裡**實際做過的事**當依據；技能清單、自評、證照只能加分，不能單獨撐起 60 分以上
- 職缺的核心任務（例如看得懂土木／建築／機電／水電工程圖）要在經歷裡找到對應的具體工作，找不到就不准給 70 分以上
- 最近一份工作跟職缺領域無關（例如藝術、行銷、教育推廣），最高 60 分（C/B 邊緣），evidence 要寫出最近一份做什麼
- recruitability_class 是 REFERRAL_ONLY 或 LONG_TERM_POOL 的，fit_score 最高 59
- 找不到本人聯絡方式沒關係，顧問可以打公司總機請轉：phone 填公司總機並寫明「公司總機」，evidence 寫要請轉的部門與職稱
- 年資、做過的事要照履歷原文寫，不准誇大或改寫成更接近職缺的說法；evidence 必須引用經歷裡的公司＋職稱＋做的事

完成後，只輸出一個 JSON 物件（不要有其他文字說明、不要用 ```json 包起來、不要在 JSON 前後加任何字），格式：
{{
  "route": "從 job-strategy-router.md 選出的代碼，例如 ENG_CONSTRUCTION",
  "archetype_locked": true 或 false,
  "calibration_result": "CALIBRATION_PASSED_4_OF_5 或 ROUTE_CALIBRATION_FAILED",
  "candidates": [
    {{
      "name": "姓名", "headline": "職稱或一行描述", "company": "目前或最近公司",
      "location": "地點", "email": "查到的公開Email，查不到填null", "phone": "查到的公開電話，查不到填null",
      "linkedin_url": "查得到就填，查不到填null", "source_url": "找到這個人的來源網址",
      "fit_score": 0到100的整數, "recruitability_class": "DIRECT_TARGET 或 CONTACT_AFTER_CONFIRMATION 或 ADJACENT_TARGET 或 REFERRAL_ONLY 或 LONG_TERM_POOL",
      "evidence": "為什麼判斷這個人符合這個職缺，附身分錨點（公司/專案/技能/地點等至少兩項）"
    }}
  ],
  "run_summary": "這次搜尋過程的簡短摘要：用了哪些來源、找到幾位、停在哪裡、為什麼"
}}'''


def run_claude(prompt):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    cmd = ['claude', '-p', '--model', MODEL, '--output-format', 'text',
           '--permission-mode', 'bypassPermissions', '--setting-sources', '',
           '--session-id', str(uuid.uuid4())]
    r = subprocess.run(cmd + [prompt], capture_output=True, text=True,
                        timeout=TIMEOUT_SEC, env=env)
    return r.returncode == 0, (r.stdout or r.stderr or '')


def extract_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.startswith('json'):
            text = text[4:]
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        pass
    # 2026-10-07：金邊那輪 AI 找到 6 位，但 JSON 後面又寫了一段說明（含反引號、大括號），
    # 「第一個 { 到最後一個 }」切出來不是合法 JSON，整批 0 筆。改成逐一嘗試每個 {，
    # 取第一個能完整解析、而且帶 candidates 的物件。
    dec = json.JSONDecoder()
    i = text.find('{')
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(text, i)
            if isinstance(obj, dict) and 'candidates' in obj:
                return obj
        except Exception:
            pass
        i = text.find('{', i + 1)
    return None


def save_candidates(job_slug, candidates, dry, source='AI獵頭顧問專員'):
    saved = 0
    # 2026-10-06：不能從客戶公司挖人（夜間找人把 Medtecs＝美德的副總排成美德職缺 A 級）。
    try:
        sys.path.insert(0, os.path.join(HERE, 'jobintake'))
        import client_guard as G
        _clients = G.load_clients(lambda sql: D.d1(sql) or [])
    except Exception as e:
        log(f'⚠️ 讀不到客戶名單，這次無法擋客戶公司的人：{e}')
        G, _clients = None, []
    for c in candidates:
        name = (c.get('name') or '').strip()
        if not name:
            continue
        company = c.get('company') or ''
        if G:
            hit = next((h for h in (G.check(str(c.get(f) or ''), _clients, strict=True) for f in ('company', 'headline') if c.get(f))
                        if h and h.get('verdict') == 'block'), None)
            if hit:
                log(f'⛔ {name}（{company}）：現職對到客戶「{hit["matched"]}」，不能從客戶公司挖人，跳過')
                continue
        # 去重：同職缺、同姓名、同公司算重複，不要每次跑都塞一樣的人進去
        dup = D.d1(
            f"SELECT id FROM sourced_candidates WHERE job_slug={D.q(job_slug)} "
            f"AND name={D.q(name)} AND company={D.q(company)} LIMIT 1"
        )
        if dup:
            log(f'⏭️  {name}（{company}）：已經在池子裡，跳過')
            continue
        if dry:
            log(f"（--dry 不寫入）會新增：{name}　{company}　{c.get('recruitability_class')}")
            saved += 1
            continue
        cid = str(uuid.uuid4())
        note = f"電話：{c.get('phone')}" if c.get('phone') else None
        # 2026-08-26：sourced_candidates 有 UNIQUE(source, source_url)，原本假設
        # 「一個來源網址＝一個人」。改成 graph expansion 之後，一份期刊 PDF／
        # 一份得獎名單會一次挖出十幾位同組同事，全部共用同一個 source_url，
        # 第二個人開始就整批 INSERT 失敗（實測 2026-08-26 16:46 只存進 3/20 位）。
        # 這裡給每筆網址補上人名 fragment：瀏覽器會忽略 #，網址照樣能點，
        # 但每個人各佔一列，UNIQUE 的原意（同一個人同一個來源不重複收）也還在。
        # 2026-08-26：grade 原本被塞了 recruitability_class，跟舊資料的 A/B/C/D 混在
        # 同一欄，導致這一欄篩不出任何東西（篩 A 只撈得到舊資料，篩招募分類只撈得到新資料）。
        # 規範第六步明訂 Fit Score / Recruitability Class / Contactability 三者分開，
        # 現在 grade 只放等第、招募分類寫進 recruitability_class 欄。
        # 等第門檻同 full-prompt-v3.4.md：A 80-100、B 60-79、C 40-59、D 39 以下。
        fit = int(c.get('fit_score') or 0)
        # 2026-10-06：只能引薦／長期觀察、或完全沒有本人聯絡管道的，程式再擋一次最高 C（林沛宏被排成 B）
        # 沒有個人聯絡方式不扣分——Jacky：可以打公司總機請轉（10/6）。只擋「只能引薦／長期觀察」。
        if c.get('recruitability_class') in ('REFERRAL_ONLY', 'LONG_TERM_POOL'):
            fit = min(fit, 59)
        # 2026-10-06：還沒人看過完整履歷，最高 B（A 只給已核對的）
        fit = min(fit, 79)
        grade = 'A' if fit >= 80 else 'B' if fit >= 60 else 'C' if fit >= 40 else 'D'
        src_url = (c.get('source_url') or '').strip() or None
        if src_url and '#' not in src_url:
            src_url = f'{src_url}#{name}'
        try:
          D.d1(
            f"INSERT INTO sourced_candidates "
            f"(id, created_at, source, source_url, name, headline, company, location, "
            f"email, linkedin_url, bio, raw_json, job_slug, score, grade, "
            f"recruitability_class, status, note) "
            f"VALUES ({D.q(cid)}, datetime('now','+8 hours'), {D.q(source)}, "
            f"{D.q(src_url)}, {D.q(name)}, {D.q(c.get('headline'))}, {D.q(company)}, "
            f"{D.q(c.get('location'))}, {D.q(c.get('email'))}, {D.q(c.get('linkedin_url'))}, "
            f"{D.q(c.get('evidence'))}, {D.q(json.dumps(c, ensure_ascii=False))}, {D.q(job_slug)}, "
            f"{fit}, {D.q(grade)}, {D.q(c.get('recruitability_class'))}, 'new', {D.q(note)})"
        )
        except Exception as e:
            log(f'⚠️  {name}（{company}）寫入失敗，跳過不影響其他人：{str(e)[:160]}')
            continue
        saved += 1
        log(f'✅ {name}（{company}）→ {c.get("recruitability_class")}')
    return saved




# ── 聯絡資料查找（--contact-only）────────────────────────────────
# 2026-08-26 加：原本這支只會「找到人」，找到之後 email/phone 一律 null，
# 池子裡 50 個人沒有一個能實際聯繫，等於卡在最後一哩。這個模式接
# email-ready-layer-v1.md 的 Email-first contact waterfall，只對已經在池子裡、
# 且還沒做過聯絡查找的人跑，不重新搜尋人選。
CONTACT_ROUTES = ['QUALIFIED_EMAIL_READY', 'QUALIFIED_DUAL_CHANNEL_READY',
                  'EMAIL_VALIDATION_REQUIRED', 'LINKEDIN_MANUAL_QUEUE',
                  'ORG_REFERRAL_QUEUE', 'NO_AUTOMATABLE_CONTACT']


def load_contact_targets(slug, since, limit):
    where = [f"job_slug={D.q(slug)}", "email IS NULL",
             "(note IS NULL OR note NOT LIKE '%[聯絡查找]%')"]
    if since:
        where.append(f"created_at >= {D.q(since)}")
    return D.d1(
        f"SELECT id, name, company, headline, location, linkedin_url, source_url, score "
        f"FROM sourced_candidates WHERE {' AND '.join(where)} "
        f"ORDER BY score DESC LIMIT {int(limit)}"
    ) or []


def build_contact_prompt(job, targets):
    people = '\n'.join(
        f"- id={t['id']}｜{t['name']}｜{t.get('headline') or ''}｜{t.get('company') or ''}"
        f"｜{t.get('location') or ''}｜已知LinkedIn：{t.get('linkedin_url') or '無'}"
        f"｜發現來源：{t.get('source_url') or '無'}"
        for t in targets)
    return f'''你是 Step1ne 獵頭公司的 AI Talent Intelligence & Sourcing Agent。
這次**不要搜尋新人選**，只做既有候選人的公開聯絡資料查找。

先完整讀取這幾份規範，這是你這次任務唯一的作業依據：
1. {SKILL_DIR}/SKILL.md
2. {SKILL_DIR}/references/email-ready-layer-v1.md（本次主規範：Email-first contact waterfall 與 routing）
3. {SKILL_DIR}/references/full-prompt-v3.4.md

職缺脈絡（只用來判斷這個人值不值得繼續深查，不要重新評 Fit）：
{job.get('title')}／{job.get('locations')}／月薪 {job.get('salary_min')}-{job.get('salary_max')}

以下 {len(targets)} 位已經在候選人池裡，請逐一依 waterfall 順序查公開聯絡管道：
{people}

Email-first contact waterfall（照順序，不要跳）：
1. 目前／最近公司的官方網域與官網
2. 官方團隊頁、作者頁、專案頁、案例頁、得獎頁、講者頁、公開 PDF 與簡報的作者資訊區塊
3. Cake／CakeResume 公開履歷、作品集、About 與 Contact 區
4. LinkedIn 公開 Profile 的 Contact Info、About、Featured 與其中連出去的網站
5. 個人網站、作品集、GitHub 公開 Profile/README、YouTube About、已驗證社群 Bio
6. 本人公開的專業電話與已驗證社群帳號

【本輪重點：Username X-ray（waterfall 第 5 步）】
上面每位都附了已知的 LinkedIn 網址。請把網址尾端的 username 字串抽出來
（例如 tw.linkedin.com/in/shihwei-ku 的 username 是 shihwei-ku），
然後拿這個字串本身去查 GitHub、個人網站、作品集、Behance、Medium、
Notion 公開頁、YouTube、已驗證社群 Bio。獨特的 username 常常跨平台重複使用，
這條路不需要登入，也不必碰 LinkedIn 本體。

注意：先前那輪是用「姓名＋公司＋職稱」查的，本輪要用 username 這條新路徑，
不要重複跑等價查詢。username 命中的頁面必須另有第二錨點（公司／學校／專案／
技能／地點）才可採用，只有字串相同不算，記 UNVERIFIED_SAME_NAME。

【聯絡管道優先序（2026-08-26 Jacky 指定，覆蓋規範原本的順序）】
Email ＞ 社群連結（個人網站／GitHub／作品集／Cake） ＞ 電話。
規範原文是 Email＞電話＞社群，本輪社群與電話對調。
理由：Email 用於自動化聯繫，社群連結可自助查看且能再導出聯絡方式，
電話屬人工且干擾性最高。

硬性規則（違反等於任務失敗）：
- 不登入任何帳號、不繞驗證碼或付費牆、不使用外洩資料庫、不用帳號還原機制
- **絕對不可以猜 Email**：不得用 name@company.com 這類公司格式推導、不得猜 Gmail、
  不得使用遮罩資料（例如 a***@gmail.com）、不得因同名就採用
- 公司總機或 info@／hr@ 這種組織信箱不算本人管道，只能歸到 ORG_REFERRAL_QUEUE
- 查不到就誠實填 null 並歸到 NO_AUTOMATABLE_CONTACT，不要為了交差硬湊
- 找到的聯絡管道不得回頭加減 Fit 分數
- 不依年齡、性別、婚姻、國籍、身心障礙、宗教等就服法第5條保護項目做任何判斷

完成後只輸出一個 JSON 物件（前後不要加任何文字，不要用 ``` 包起來）：
{{
  "contacts": [
    {{
      "id": "上面給的 id，原樣填回",
      "name": "姓名",
      "email": "查到的本人公開Email，查不到填null",
      "phone": "查到的本人公開電話，查不到填null",
      "linkedin_url": "查到或沿用的LinkedIn，沒有填null",
      "other_channel": "個人網站／GitHub／作品集／已驗證社群等第二管道，沒有填null",
      "contact_route": "{' 或 '.join(CONTACT_ROUTES)}",
      "evidence": "在哪個網址、用哪一步 waterfall 找到的；查不到就寫實際查過哪些路徑",
      "contact_source_url": "取得聯絡資料的網址，沒有填null"
    }}
  ],
  "run_summary": "這次查了幾位、實際打通哪幾類來源、Email-ready 幾位、缺口在哪"
}}'''


def save_contacts(rows, dry):
    n_email = n_any = 0
    for c in rows:
        cid = (c.get('id') or '').strip()
        if not cid:
            continue
        email = (c.get('email') or None)
        phone = (c.get('phone') or None)
        route = c.get('contact_route') if c.get('contact_route') in CONTACT_ROUTES else 'NO_AUTOMATABLE_CONTACT'
        parts = [f"[聯絡查找] {route}"]
        if phone:
            parts.append(f"電話：{phone}")
        if c.get('other_channel'):
            parts.append(f"第二管道：{c['other_channel']}")
        if c.get('contact_source_url'):
            parts.append(f"聯絡來源：{c['contact_source_url']}")
        if c.get('evidence'):
            parts.append(str(c['evidence'])[:300])
        tag = '；'.join(parts)
        if email:
            n_email += 1
        if email or phone or c.get('other_channel'):
            n_any += 1
        mark = '📧' if email else ('📞' if phone else '—')
        if dry:
            log(f"（--dry 不寫入）{mark} {c.get('name')}｜{route}｜{email or '無Email'}")
            continue
        sets = [f"note = {D.q(tag)} || COALESCE(char(10)||note,'')"]
        if email:
            sets.append(f"email = {D.q(email)}")
        if c.get('linkedin_url'):
            sets.append(f"linkedin_url = COALESCE(linkedin_url, {D.q(c['linkedin_url'])})")
        try:
            D.d1(f"UPDATE sourced_candidates SET {', '.join(sets)} WHERE id={D.q(cid)}")
        except Exception as e:
            log(f"⚠️  {c.get('name')} 寫回失敗：{str(e)[:140]}")
            continue
        log(f"{mark} {c.get('name')}｜{route}｜{email or '無Email'}")
    return n_email, n_any


def push_tg_review_cards(dry):
    """2026-10-01 加：找人跑完，有新的「值得寄信」的人就推卡到 TG「🔍 外部人選待判斷」。
    只推卡給 Jacky／Phoebe 判斷，不會自己寄信。門檻、一次 10 張、一天 20 張都在後台
    （step1ne-backoffice-worker 的 sourcedTgPush）控管，這裡只負責敲門；失敗不影響找人結果。"""
    if dry:
        return
    import urllib.request
    tok = os.environ.get('RECRUIT_ADMIN_TOKEN', '')
    if not tok:
        try:
            for line in open(os.path.expanduser('~/.config/workflow-os/recruit.env'), encoding='utf-8'):
                if line.startswith('RECRUIT_ADMIN_TOKEN='):
                    tok = line.strip().split('=', 1)[1]
        except OSError:
            pass
    if not tok:
        log('（沒有 RECRUIT_ADMIN_TOKEN，略過 TG 待判斷卡推送）')
        return
    try:
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/sourced/tg-push',
            data=json.dumps({'trigger': 'sourcing_run'}).encode(),
            headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json',
                     # Cloudflare 會擋 urllib 預設的 User-Agent（403 code 1010）
                     'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36'})
        r = json.load(urllib.request.urlopen(req, timeout=60))
        log(f"TG 待判斷卡：推了 {r.get('pushed', 0)} 張" + (f"（{r.get('note')}）" if r.get('note') else ''))
    except Exception as e:  # noqa: BLE001
        log(f'（TG 待判斷卡推送失敗，不影響找人結果：{e}）')


# ── AI 挖角（--poach）─────────────────────────────────────────────
# 2026-10-07 Jacky 交辦：原本 AI 自己上網找人，找到的人很散。改成顧問在後台
# 每個職缺填「挖角公司（一行一家）」＋「目標職稱／職級」，AI 只找現在／最近
# 在這些公司、這個層級的人，並幫每個人寫好兩段 LinkedIn 訊息（邀請連結的附註、
# 對方接受後的第一則私訊），顧問自己複製貼上送出——系統絕不自動操作 LinkedIn。
CONNECT_NOTE_MAX = 180
# 對人選的訊息不能出現這些字（就服法第5條保護項目＋「客戶」這個詞；見 feedback_candidate_facing_copy_rules）
PROTECTED_WORDS = ['客戶', '年齡', '幾歲', '歲以下', '歲以上', '年輕', '年紀', '性別', '男性', '女性',
                   '已婚', '未婚', '婚姻', '婚育', '懷孕', '國籍', '宗教', '身心障礙', '血型', '星座']


def load_poach_settings(slug):
    try:
        rows = D.d1(f"SELECT target_companies, target_titles FROM job_sourcing_settings WHERE job_slug={D.q(slug)}")
    except Exception as e:  # 欄位還沒加好
        log(f'⚠️ 讀不到挖角設定：{str(e)[:150]}')
        return [], ''
    if not rows:
        return [], ''
    comps = [x.strip(' \t-・•、,，') for x in str(rows[0].get('target_companies') or '').splitlines()]
    comps = [x for x in comps if x]
    return comps, str(rows[0].get('target_titles') or '').strip()


def load_job_public(slug):
    """訊息要用的對外資訊：雇主一般描述、是否保密、公司別名、對外薪資寫法。"""
    rows = D.d1(f"SELECT client_name, client_intro, confidential_client, company_id, salary_min, salary_max, "
                f"salary_unit, title FROM jobs WHERE slug={D.q(slug)}") or [{}]
    j = rows[0]
    names = [j.get('client_name') or '']
    if j.get('company_id'):
        cc = D.d1(f"SELECT display_name, aliases FROM client_companies WHERE id={D.q(j['company_id'])}") or []
        if cc:
            names.append(cc[0].get('display_name') or '')
            names += str(cc[0].get('aliases') or '').splitlines()
    return j, client_terms(names)


def client_terms(names):
    """客戶名的所有寫法（含括號內外、去掉『醫療／集團／股份有限公司』的字根、中文前兩字）。
    寧可多擋：擋錯只是那則訊息重寫，漏擋就是把保密客戶講出去。"""
    out = set()
    raw = [n.strip() for n in names if n and n.strip()]
    for n in list(raw):
        m = re.match(r'^\s*(.+?)\s*[（(](.+?)[)）]\s*$', n)
        if m:
            raw += [m.group(1).strip(), m.group(2).strip()]
    try:
        sys.path.insert(0, os.path.join(HERE, 'jobintake'))
        import client_guard as G
        for n in raw:
            out.update(G.variants(n))
    except Exception:
        out.update(raw)
    for n in raw:
        out.add(n)
        stem = re.sub(r'(股份)?有限公司$|公司$|集團$|醫療$|科技$|企業$|國際$', '', n).strip()
        if len(stem) >= 2:
            out.add(stem)
        cjk = re.match(r'^[一-鿿]{2,}', n)
        if cjk:
            out.add(cjk.group(0)[:2])
    return sorted({t for t in out if len(t) >= 2}, key=len, reverse=True)


def public_salary_text(j):
    """只講官網職缺頁上寫的。jobs.salary_unit 有時填錯（美德寫 MONTH 但其實是年薪 250 萬），
    所以金額 ≥ 30 萬一律當年薪。沒有下限就不提薪資。"""
    lo = j.get('salary_min')
    try:
        lo = int(lo) if lo else 0
    except (TypeError, ValueError):
        lo = 0
    if not lo:
        return ''
    if str(j.get('salary_unit') or '').upper() == 'YEAR' or lo >= 300000:
        return f'年薪 {lo // 10000} 萬起'
    return f'月薪 {lo // 1000 / 10:g} 萬起'


def build_poach_prompt(job, companies, titles, pub, salary_text, ledger_text='', ledger_n=0, gate_text=''):
    jd_text = '\n'.join(f'{k}: {v}' for k, v in job.items() if v not in (None, '') and k != 'client_name')
    job_url = f"https://step1ne.com/jobs/{job['slug']}/"
    employer = (pub.get('client_intro') or '').strip() or '一家正在擴編的集團'
    comp_list = '\n'.join(f'- {c}' for c in companies)
    ledger_block = ''
    if ledger_n:
        ledger_block = f"""
【已知名單｜這個職缺後台已經有 {ledger_n} 位，全部不算新產出】
撞到同一人（姓名、別名、公司、職稱、LinkedIn 網址任兩項對得上）就跳過，不要放進 candidates。
{ledger_text}
"""
    gate_block = f'\n【職缺專屬閘門｜顧問拍板，必須照做】\n{gate_text}\n' if gate_text else ''
    return f'''你是 Step1ne 獵頭公司的 AI Talent Intelligence & Sourcing Agent，這次是「指定公司挖角」任務。

先讀這兩份規範的硬規則與評分方式（只讀規則，不要照裡面的五人校準流程走）：
1. {SKILL_DIR}/SKILL.md
2. {SKILL_DIR}/references/full-prompt-v3.4.md

職缺（內部資料，只給你判斷用）：
{jd_text}
{gate_block}{ledger_block}
【這次的搜尋範圍｜只能找這些公司的人】
顧問指定的挖角公司（含它們列出的子公司、品牌、海外廠）：
{comp_list}

目標職稱／職級：{titles or '跟職缺同層級或高一層的主管'}

做法：
- **跳過 Archetype 五人校準，直接開始正式搜尋。**
- 只收「現職或最近一份工作」在上面清單公司（含其子公司／品牌／海外據點）的人；
  已經離開這些公司超過一份工作的不要收。
- 職級要符合上面的目標職稱／職級；明顯低於（例如專員、組長）的不要收。
- 優先用公開的 LinkedIn 個人頁（用搜尋引擎 site:linkedin.com/in 加公司名、職稱的中英文寫法），
  也可用公司官網經營團隊頁、年報、新聞稿、產業媒體報導、演講名單。
  找得到 LinkedIn 個人頁就一定要填 linkedin_url；真的找不到才只填 source_url。
- 目標 10～20 位新的人。平均分散在不同公司，不要全擠在同一家。
- 公司名的中英文都要搜（例如 聚陽／Makalot、寶成／Pou Chen、裕元／Yue Yuen）。

硬性規則（不可違反，違反就等於這次任務失敗）：
- 只用合法公開資訊搜尋，不登入任何帳號、不繞過驗證碼或付費牆、不使用外洩資料庫
- 不得用任何帳號自動化操作 LinkedIn／任何平台，需要登入才看得到的內容一律不用
- 不得用猜測方式產生 Email，查不到公開信箱就填 null
- 不得依年齡、性別、婚姻、國籍、身心障礙、宗教等就業服務法第5條保護項目篩選、排序或排除候選人，
  即使公開資料裡看得到這些資訊也不可以拿來當判斷依據
- fit_score 只能依「工作經歷裡實際做過的事」打分；年資、職稱照原文寫，不准誇大

【每個人都要寫兩段 LinkedIn 訊息｜顧問會自己複製貼上，系統不會自動送出】
1. connect_note：送出「連結邀請」時附的短訊，繁體中文，**最多 180 個字（含標點）**。
   要用他真實經歷裡的一個具體點開頭（例如他負責的事業部、做過的海外產線、帶過的品牌客戶），
   說明我們是獵頭顧問、手上有一個跟他經歷相關的高階機會，邀請他加為聯絡人。不要放網址。
2. followup_msg：對方接受邀請後的第一則私訊，繁體中文，約 150～300 字。
   內容：簡短介紹職位（職稱、這個職位要做什麼、地點）、為什麼想到他（一句、扣他的經歷）、
   邀請他這週或下週撥 10～15 分鐘電話聊聊，最後附上職缺頁 {job_url}
   {f"可以提薪資，但只能照官網寫法：「{salary_text}」，不要自己加碼或編其他數字。" if salary_text else "不要提任何薪資數字。"}

訊息的禁止事項（違反的訊息會被系統整段丟掉）：
- **絕對不能出現雇主的公司名稱、品牌、簡稱或英文名**。雇主只能用這句一般描述來講：
  「{employer}」（可以縮短改寫，但不能加回公司名、國家以外的可辨識細節）
- 不能出現「客戶」這兩個字（改說「這家公司」「這個團隊」「我們合作的企業」）
- 不能提年齡、性別、婚姻、國籍、宗教、身心障礙等任何個人屬性
- 不能說資料是從哪裡找到的（不要寫「我在 LinkedIn 上看到你」以外的來源；也不要說「AI 幫我找到你」）
- 署名用「Step1ne 獵頭顧問」即可，不要寫人名

完成後，只輸出一個 JSON 物件（不要有其他文字說明、不要用 ```json 包起來、不要在 JSON 前後加任何字），格式：
{{
  "route": "POACH_TARGET_COMPANIES",
  "archetype_locked": true,
  "calibration_result": "SKIPPED_POACH_MODE",
  "candidates": [
    {{
      "name": "姓名", "headline": "職稱或一行描述", "company": "目前或最近公司（要是清單裡的公司或其子公司）",
      "location": "地點", "email": "查到的公開Email，查不到填null", "phone": "查到的公開電話，查不到填null",
      "linkedin_url": "查得到就一定要填，查不到填null", "source_url": "找到這個人的來源網址",
      "fit_score": 0到100的整數, "recruitability_class": "DIRECT_TARGET 或 CONTACT_AFTER_CONFIRMATION 或 ADJACENT_TARGET 或 REFERRAL_ONLY 或 LONG_TERM_POOL",
      "evidence": "為什麼判斷這個人符合，附身分錨點（公司/職稱/負責事項/地點等至少兩項）",
      "connect_note": "連結邀請附註，180字以內",
      "followup_msg": "接受後的第一則私訊，含 {job_url}"
    }}
  ],
  "run_summary": "這次搜尋了哪些公司、每家找到幾位、哪些公司找不到人、為什麼"
}}'''


def outreach_problems(c, terms):
    probs = []
    for k in ('connect_note', 'followup_msg'):
        v = str(c.get(k) or '')
        if not v.strip():
            probs.append(f'{k} 是空的')
            continue
        low = v.lower()
        for t in terms:
            if t.lower() in low:
                probs.append(f'{k} 出現雇主名稱「{t}」')
                break
        for w in PROTECTED_WORDS:
            if w in v:
                probs.append(f'{k} 出現不能寫的字「{w}」')
                break
    return probs


def fix_outreach(c, job_url):
    for k in ('connect_note', 'followup_msg'):
        if isinstance(c.get(k), str):
            c[k] = c[k].strip()
    if c.get('connect_note') and len(c['connect_note']) > CONNECT_NOTE_MAX:
        c['connect_note'] = c['connect_note'][:CONNECT_NOTE_MAX - 1].rstrip('，、, ') + '…'
    fm = c.get('followup_msg')
    if fm and job_url not in fm:
        c['followup_msg'] = fm.rstrip() + f'\n\n職缺介紹：{job_url}'


def guard_outreach(cands, terms, job, pub, salary_text):
    """寫進資料庫之前最後一道：有公司名／保護項目字眼的訊息先請 AI 重寫一次，
    還是不行就整段拿掉（人照樣存，顧問看得到「訊息被拿掉」的原因自己寫）。"""
    job_url = f"https://step1ne.com/jobs/{job['slug']}/"
    for c in cands:
        fix_outreach(c, job_url)
    bad = [c for c in cands if outreach_problems(c, terms)]
    if bad:
        log(f'⚠️ {len(bad)} 位的訊息沒過檢查，請 AI 重寫一次：' +
            '；'.join(f"{c.get('name')}（{'、'.join(outreach_problems(c, terms))}）" for c in bad))
        people = '\n'.join(
            f"- name={c.get('name')}｜{c.get('headline') or ''}｜{c.get('company') or ''}｜佐證：{str(c.get('evidence') or '')[:300]}"
            f"\n  問題：{'、'.join(outreach_problems(c, terms))}"
            for c in bad)
        employer = (pub.get('client_intro') or '').strip() or '一家正在擴編的集團'
        prompt = f'''幫這幾位人選重寫兩段 LinkedIn 訊息（不要上網、不要用任何工具，直接寫）。
職位：{job.get('title')}；職缺頁 {job_url}
雇主只能這樣描述（不能寫公司名稱、品牌、簡稱、英文名）：「{employer}」
{f"薪資只能寫：「{salary_text}」" if salary_text else "不要提薪資。"}
規則：繁體中文；connect_note 最多 180 字、用他的真實經歷開頭、邀請加聯絡人、不放網址；
followup_msg 150～300 字、介紹職位、邀請這週或下週通 10～15 分鐘電話、最後附職缺頁網址；
不能出現「客戶」、年齡、性別、婚姻、國籍、宗教等字眼；署名「Step1ne 獵頭顧問」。
絕對不能出現這些字：{'、'.join(terms)}

{people}

只輸出 JSON：{{"messages": [{{"name": "姓名", "connect_note": "...", "followup_msg": "..."}}]}}'''
        ok, out = run_claude(prompt)
        fixed = {}
        if ok:
            txt = out.strip()
            dec = json.JSONDecoder()
            i = txt.find('{')
            while i >= 0:
                try:
                    obj, _ = dec.raw_decode(txt, i)
                    if isinstance(obj, dict) and 'messages' in obj:
                        fixed = {str(m.get('name') or '').strip(): m for m in obj.get('messages') or []}
                        break
                except Exception:
                    pass
                i = txt.find('{', i + 1)
        for c in bad:
            m = fixed.get(str(c.get('name') or '').strip())
            if m:
                c['connect_note'], c['followup_msg'] = m.get('connect_note'), m.get('followup_msg')
                fix_outreach(c, job_url)
            probs = outreach_problems(c, terms)
            if probs:
                c['connect_note'] = c['followup_msg'] = None
                c['outreach_blocked'] = '訊息被系統拿掉（' + '、'.join(probs) + '），請顧問自己寫'
                log(f"⛔ {c.get('name')}：重寫後還是不行，訊息拿掉（{'、'.join(probs)}）")
            else:
                log(f"✅ {c.get('name')}：訊息重寫後通過")
    return cands


def run_poach(a, job):
    companies, titles = load_poach_settings(a.job)
    if not companies:
        sys.exit(f'❌ {a.job} 還沒設定挖角公司。請到顧問後台 → 職缺 →「AI 找的人」→ 挖角公司，一行填一家再按「儲存」。')
    pub, terms = load_job_public(a.job)
    salary_text = public_salary_text(pub)
    log(f'開始挖角：{job["title"]}（{a.job}），{len(companies)} 家公司，目標職級：{titles or "（沒填）"}')
    log(f'訊息禁用字（雇主名稱）：{"、".join(terms)}　對外薪資寫法：{salary_text or "不提"}')
    pool = load_existing_pool(a.job)
    ledger_text, ledger_n = format_ledger(pool)
    if ledger_n:
        log(f'已載入既有名單：{ledger_n} 位，本輪只算新的人')
    prompt = build_poach_prompt(job, companies, titles, pub, salary_text, ledger_text, ledger_n, load_job_gate(a.job))
    ok, out = run_claude(prompt)
    run_dir = os.path.join(HERE, 'sourcing_runs')
    os.makedirs(run_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(run_dir, f'{a.job}_{stamp}_poach_raw.txt')
    with open(raw_path, 'w', encoding='utf-8') as f:
        f.write(out)
    if not ok:
        sys.exit(f'❌ claude -p 執行失敗（原始輸出在 {raw_path}）：{out[-1000:]}')
    parsed = extract_json(out)
    if not parsed:
        sys.exit(f'❌ 沒有解析出有效 JSON。原始輸出已保留在：{raw_path}\n末段：\n{out[-1500:]}')
    log(f"摘要：{parsed.get('run_summary')}")
    candidates = parsed.get('candidates') or []
    if not candidates:
        log('這次沒有找到候選人')
        return
    candidates = guard_outreach(candidates, terms, job, pub, salary_text)
    for c in candidates:
        c['poach_targets'] = companies
        c['poach_titles'] = titles
    json_path = os.path.join(run_dir, f'{a.job}_{stamp}_poach.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    log(f'完整結果存到：{json_path}')
    for i, c in enumerate(candidates, 1):
        log(f"{i}. {c.get('name')}｜{c.get('headline')}｜{c.get('company')}｜LinkedIn：{c.get('linkedin_url')}")
    saved = save_candidates(a.job, candidates, a.dry, source='AI挖角')
    log(f'完成，共 {len(candidates)} 位候選人，新增 {saved} 位進池子' + ('（--dry 沒有真的寫入）' if a.dry else ''))
    if saved:
        push_tg_review_cards(a.dry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', required=True, help='職缺 slug')
    ap.add_argument('--sample-only', action='store_true', help='只跑五人校準，不做正式批量搜尋')
    ap.add_argument('--dry', action='store_true', help='只印結果，不寫入 D1')
    ap.add_argument('--contact-only', action='store_true', help='不找新人，只對池子裡已有的人做公開聯絡資料查找')
    ap.add_argument('--since', default=None, help='只處理這個時間之後加入池子的人，例如 "2026-08-26 16:00"')
    ap.add_argument('--limit', type=int, default=20, help='聯絡查找一次處理幾位')
    ap.add_argument('--learnings', default=None, help='上一輪的教訓文字（直接傳字串），避開同樣的 false-positive 來源')
    ap.add_argument('--poach', action='store_true', help='挖角模式：只找後台設定的挖角公司，並幫每個人寫 LinkedIn 訊息')
    a = ap.parse_args()

    # 挖角是顧問特地按的，就算「外部找人選」設成不找也照跑（那個設定管的是夜間自動找人）
    job = load_job(a.job, ignore_off=a.poach)
    if not job:
        sys.exit(f'找不到職缺：{a.job}')

    if a.poach:
        run_poach(a, job)
        return

    if a.contact_only:
        targets = load_contact_targets(a.job, a.since, a.limit)
        if not targets:
            sys.exit('沒有需要做聯絡查找的候選人（都查過了，或 email 已經有值）')
        log(f'開始聯絡資料查找：{job["title"]}（{a.job}），本輪 {len(targets)} 位')
        ok, out = run_claude(build_contact_prompt(job, targets))
        if not ok:
            sys.exit(f'❌ claude -p 執行失敗：{out[-1000:]}')
        parsed = extract_json(out)
        if not parsed:
            sys.exit(f'❌ 沒有解析出有效 JSON，原始輸出（截斷）：\n{out[-2000:]}')
        run_dir = os.path.join(HERE, 'sourcing_runs')
        os.makedirs(run_dir, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        with open(os.path.join(run_dir, f'{a.job}_{stamp}_contact.json'), 'w') as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
        log('摘要：' + str(parsed.get('run_summary', ''))[:3000])
        log('── 聯絡查找結果 ──')
        ne, na = save_contacts(parsed.get('contacts') or [], a.dry)
        log(f'完成：{len(targets)} 位中，取得 Email {ne} 位、至少一個管道 {na} 位'
            + ('（--dry 沒有真的寫入）' if a.dry else ''))
        if ne:
            push_tg_review_cards(a.dry)
        return

    log(f'開始 sourcing：{job["title"]}（{a.job}）' + ('（僅五人校準）' if a.sample_only else ''))
    pool = load_existing_pool(a.job)
    ledger_text, ledger_n = format_ledger(pool)
    if ledger_n:
        log(f'已載入 Candidate Memory Ledger：後台既有 {ledger_n} 位，本輪只算新身分')
    gate_text = load_job_gate(a.job)
    if gate_text:
        log(f'已載入職缺專屬閘門：job_gates/{a.job}.md（{len(gate_text)} 字）')
    prompt = build_prompt(job, a.sample_only, a.learnings, ledger_text, ledger_n, gate_text)

    ok, out = run_claude(prompt)
    if not ok:
        sys.exit(f'❌ claude -p 執行失敗：{out[-1000:]}')

    # 2026-08-26：原本解析失敗就直接 sys.exit，而且只印最後 2000 字，
    # 原始輸出沒存檔——實測 engineering-design-engineer-hsinchu 那輪找到 20 位人選，
    # 因為 JSON 解析失敗整批蒸發，20 分鐘的搜尋結果救不回來。
    # 改成先無條件把原始輸出寫檔，再解析；失敗時至少人還在檔案裡，可以手動撈。
    run_dir = os.path.join(HERE, 'sourcing_runs')
    os.makedirs(run_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(run_dir, f'{a.job}_{stamp}_raw.txt')
    with open(raw_path, 'w') as f:
        f.write(out)
    parsed = extract_json(out)
    if not parsed:
        sys.exit(f'❌ 沒有解析出有效 JSON。原始輸出已保留在：{raw_path}\n'
                 f'（{len(out)} 字元，可手動撈出 candidates 再用 save_candidates 寫入）\n'
                 f'末段：\n{out[-1500:]}')

    # 2026-08-25 加：之前只印姓名/公司/分類到終端機，佐證（evidence）、來源網址、
    # fit_score 這些 Jacky 要親自判斷「這個人選對不對」的關鍵資訊完全沒留下來，
    # 等於顧問只能相信 agent 自己講的結論，沒辦法自己核對。每次跑完整包原始輸出
    # 跟解析後的 JSON 都存成檔案，跟這次搜尋過程一起留底。
    json_path = os.path.join(run_dir, f'{a.job}_{stamp}.json')
    with open(raw_path, 'w', encoding='utf-8') as f:
        f.write(out)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    log(f'完整結果存到：{json_path}')

    log(f"Route：{parsed.get('route')}　校準結果：{parsed.get('calibration_result')}")
    log(f"摘要：{parsed.get('run_summary')}")

    candidates = parsed.get('candidates') or []
    if not candidates:
        log('這次沒有找到候選人（可能校準沒過，或正式搜尋沒有結果）')
        return

    log('── 候選人明細（給顧問核對用）──')
    for i, c in enumerate(candidates, 1):
        log(f"{i}. {c.get('name')}｜{c.get('headline')}｜{c.get('company')}｜{c.get('location')}")
        log(f"   分類：{c.get('recruitability_class')}　Fit：{c.get('fit_score')}　來源：{c.get('source_url')}")
        log(f"   佐證：{c.get('evidence')}")
        log(f"   Email：{c.get('email')}　電話：{c.get('phone')}　LinkedIn：{c.get('linkedin_url')}")

    saved = save_candidates(a.job, candidates, a.dry)
    log(f'完成，共 {len(candidates)} 位候選人，新增 {saved} 位進池子' + ('（--dry 沒有真的寫入）' if a.dry else ''))
    if saved:
        push_tg_review_cards(a.dry)


if __name__ == '__main__':
    main()
