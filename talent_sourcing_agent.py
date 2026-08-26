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


def load_job(slug):
    rows = D.d1(
        f"SELECT slug, title, must_skills, main_duties, locations, salary_min, salary_max, "
        f"salary_unit, employment, required_conditions, education_level, years_min, "
        f"nice_to_have_skills, seniority, client_name FROM jobs WHERE slug={D.q(slug)}"
    )
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
        return None


def save_candidates(job_slug, candidates, dry):
    saved = 0
    for c in candidates:
        name = (c.get('name') or '').strip()
        if not name:
            continue
        company = c.get('company') or ''
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
        src_url = (c.get('source_url') or '').strip() or None
        if src_url and '#' not in src_url:
            src_url = f'{src_url}#{name}'
        try:
          D.d1(
            f"INSERT INTO sourced_candidates "
            f"(id, created_at, source, source_url, name, headline, company, location, "
            f"email, linkedin_url, bio, raw_json, job_slug, score, grade, status, note) "
            f"VALUES ({D.q(cid)}, datetime('now','+8 hours'), 'AI獵頭顧問專員', "
            f"{D.q(src_url)}, {D.q(name)}, {D.q(c.get('headline'))}, {D.q(company)}, "
            f"{D.q(c.get('location'))}, {D.q(c.get('email'))}, {D.q(c.get('linkedin_url'))}, "
            f"{D.q(c.get('evidence'))}, {D.q(json.dumps(c, ensure_ascii=False))}, {D.q(job_slug)}, "
            f"{int(c.get('fit_score') or 0)}, {D.q(c.get('recruitability_class'))}, 'new', {D.q(note)})"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', required=True, help='職缺 slug')
    ap.add_argument('--sample-only', action='store_true', help='只跑五人校準，不做正式批量搜尋')
    ap.add_argument('--dry', action='store_true', help='只印結果，不寫入 D1')
    ap.add_argument('--contact-only', action='store_true', help='不找新人，只對池子裡已有的人做公開聯絡資料查找')
    ap.add_argument('--since', default=None, help='只處理這個時間之後加入池子的人，例如 "2026-08-26 16:00"')
    ap.add_argument('--limit', type=int, default=20, help='聯絡查找一次處理幾位')
    ap.add_argument('--learnings', default=None, help='上一輪的教訓文字（直接傳字串），避開同樣的 false-positive 來源')
    a = ap.parse_args()

    job = load_job(a.job)
    if not job:
        sys.exit(f'找不到職缺：{a.job}')

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

    parsed = extract_json(out)
    if not parsed:
        sys.exit(f'❌ 沒有解析出有效 JSON，原始輸出（截斷）：\n{out[-2000:]}')

    # 2026-08-25 加：之前只印姓名/公司/分類到終端機，佐證（evidence）、來源網址、
    # fit_score 這些 Jacky 要親自判斷「這個人選對不對」的關鍵資訊完全沒留下來，
    # 等於顧問只能相信 agent 自己講的結論，沒辦法自己核對。每次跑完整包原始輸出
    # 跟解析後的 JSON 都存成檔案，跟這次搜尋過程一起留底。
    run_dir = os.path.join(HERE, 'sourcing_runs')
    os.makedirs(run_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    raw_path = os.path.join(run_dir, f'{a.job}_{stamp}_raw.txt')
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


if __name__ == '__main__':
    main()
