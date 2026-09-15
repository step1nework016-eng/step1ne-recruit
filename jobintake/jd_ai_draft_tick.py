#!/usr/bin/env python3
"""資料模型合併 Phase 1 的自動同步引擎。

客戶用人需求表／顧問招募職缺編輯器現在寫的是同一批「事實」欄位
（jobs 表的 JOB_FACT_FIELDS，跟 Worker 那邊 index.js 裡的同名常數保持
一致）。事實存檔後 Worker 會把 jd_needs_ai_draft 設成 1，這支腳本
每分鐘掃一次，把事實交給 AI 重新生成頁面要的「行銷包裝」欄位
（duties/must/plus/why/benefits/faq 這些結構化陣列、subtitle、SEO
描述），寫回 jd_spec_json，順便把 jd_regen_pending 設成 1——
交給既有的 jd_regen_tick.py 去真的重產靜態頁、部署上線。

一路自動接完，顧問不用再手動把客戶填的內容謄一次。

⚠️ 「為什麼選擇這個機會」「常見問題」這類純行銷/說服文案本來就是
AI 代筆草稿，不是逐字照抄客戶原始文字——JD 內容一定要改寫這條硬規則
在這裡是靠 AI 重新表達達成的，不是靠人工檢查。禁刊字眼（年齡/性別/
宗教/國籍等歧視性條件）跟客戶匿名這兩條，prompt 裡明講，發布前
jd_regen_tick.py／publish_job.py 那邊原本就有的防線也還在，這裡是
多一層，不是取代。
"""
import os, sys, json, re, socket, subprocess, time, tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
# 原本寫死 /tmp/...，Windows 上會解析成當前磁碟根目錄下的 \tmp\...，
# 若該資料夾不存在 open() 會直接失敗，改用 tempfile.gettempdir()。
LOCK = os.path.join(tempfile.gettempdir(), 'step1ne-jd-ai-draft.lock')
# Windows 上 claude CLI 是 claude.cmd，subprocess.run(['claude',...]) 不帶副檔名
# 會 FileNotFoundError，先解出實際路徑（macOS/Linux 不受影響）。
CLAUDE_BIN = shutil.which('claude') or 'claude'
# 同理，npx 在 Windows 是 npx.cmd，不帶副檔名會 FileNotFoundError。
NPX_BIN = shutil.which('npx') or 'npx'
MODEL = 'claude-sonnet-5'
TIMEOUT_SEC = 180

sys.path.insert(0, HERE)
import draft_job as DJ  # noqa: E402  借用 tg_send／_esc


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def env():
    e = dict(os.environ)
    for f in ('cf.env', 'tokens.env'):
        p = os.path.expanduser(f'~/.config/workflow-os/{f}')
        if not os.path.exists(p):
            continue
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                e[k.strip()] = v.strip().strip('\'"')
    e.pop('CLAUDECODE', None)
    e.pop('CLAUDE_CODE_ENTRYPOINT', None)
    return e


def d1_raw(sql):
    r = subprocess.run(
        [NPX_BIN, '--yes', 'wrangler', 'd1', 'execute', 'step1ne-recruit',
         '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env(), capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout)[0]
    except Exception:
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return {}


def d1(sql):
    return d1_raw(sql).get('results', [])


def q(v):
    if v is None:
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


# ── 什麼可以印在公開網頁上，什麼只給顧問口頭講 ──
# 2026-09-08 加。在此之前這裡是「客戶用人需求表的欄位整包丟給 AI，
# 讓它自己決定要寫什麼」——包含客戶窗口的姓名與電話、用人主管、
# 保護名單、合約條款。全站 34 個職缺頁實際掃過沒有洩漏，但那是靠 AI
# 每次剛好沒寫出來，不是設計上擋住的。
#
# Jacky 2026-09-08：「這種東西本來就不該顯示，而是有顧問在電話去講」。
# 分界就照這句話：**候選人決定要不要投遞需要知道的**才公開，
# **屬於談判、內部作業、合約層級的**一律留給顧問講。
#
# ⚠️ 加新欄位時要主動歸類。不確定就放 CONSULTANT_ONLY——
#    少寫一句話頂多資訊不足，寫錯一句是把客戶的窗口電話公開在網路上。
CONSULTANT_ONLY = [
    'client_contact_name',      # 客戶窗口姓名
    'client_contact_phone',     # 客戶窗口電話
    'hiring_manager',           # 用人主管
    'off_limits_note',          # 保護名單／禁挖
    'client_screen_conditions',  # 客戶私下的篩選條件（常含年齡、性別等不能寫的偏好）
    'contract_terms_note',      # 合約條款——寫在網頁上就成了對候選人的承諾
    'hiring_reason',            # 徵人原因（常常是「誰離職了」）
    'dispatch_client',          # 派遣的真正用人客戶
]

FACT_FIELDS = [
    'client_name', 'title', 'client_intro', 'years_min', 'must_skills',
    'faq_notes', 'salary_note',
    'salary_min', 'salary_max', 'salary_unit', 'locations', 'employment', 'onboard_by',
    'team_size', 'interview_rounds', 'interview_stage_config', 'interview_who', 'has_test',
    'headcount', 'work_mode',
    'work_hours', 'leave_policy', 'employment_period', 'overtime_policy',
    'urgency', 'main_duties', 'reports_to', 'leads_team',
    'education_level', 'required_conditions', 'language_requirement',
    'nice_to_have_skills', 'preferred_background', 'personality_traits',
    'salary_tier_table', 'salary_structure_note', 'benefits_detail',
    'dispatch_to_permanent_policy',
    'department', 'work_environment_ratio', 'attendance_method',
    'interview_process', 'dispatch_range', 'overtime_detail',
    'onboarding_prep_note',
]

# 防呆：兩張清單不可以有交集，改動時漏刪一邊就會被擋下來
assert not (set(FACT_FIELDS) & set(CONSULTANT_ONLY)), \
    '同一個欄位不能同時是「可公開」與「只給顧問」'



def build_prompt(facts, client_named):
    facts_text = '\n'.join(f'{k}：{v}' for k, v in facts.items() if v not in (None, ''))
    anon_rule = (
        '⚠️ 這個客戶對外要匿名——絕對不能把公司名稱、可以反推出客戶身分的廠區地名'
        '寫進任何欄位，改用產業或職務性質描述。\n' if str(client_named) == '0' else ''
    )
    return f'''你是德仁管理顧問（品牌 Step1ne）的職缺文案編輯。下面是一個職缺的「事實資料」
（顧問或客戶填的原始資訊，不是對外文案），請幫我改寫成職缺頁要的公開文案，
輸出純 JSON，不要有 ```json 這種包裹符號。

{anon_rule}
硬性規則：
1. 不准出現任何就業服務法第5條禁止的歧視性條件——年齡、性別、婚育、國籍、
   身心障礙、宗教、容貌。事實資料裡如果有這類內容，直接略過不要寫進輸出。
2. 不是逐字照抄事實資料——要改寫成通順、對外可以講的文案語氣，但不能捏造
   事實資料裡沒有的內容。
3. 資料不足的欄位就不要輸出那個 key，不要編造。

輸出 JSON 的欄位（能填的才填，缺資料就不要那個 key）：
- slug：這個職缺的網址代號。**一定要有**，只能用小寫英文字母、數字與連字號
  （例如 ehs-engineer-yunlin、engineering-design-engineer-hsinchu）。
  寫法是「職務英文 + 工作地點英文」，3～60 個字元，不要放公司名稱，
  不要用底線、不要中文、不要 pending 開頭。
- industry：產業別短標籤，兩段以內用「・」分隔（例如「營建工程・高科技廠房」）。
  這會顯示在職缺列表卡上，看得出是哪個產業就好，不要寫成一句話。
- track：職缺分類，只能是 senior（中高階，年薪百萬等級的主管與專業職）、
  general（一般正職僱用）、dispatch（人力派遣）三選一。判斷不出來就不要輸出這個 key。
- cat：產業領域代號，只能從這個清單選一個：finance（財務會計）、it（資訊軟體）、
  operations（營運供應鏈）、construction（營建工程）、semiconductor（半導體電子）、
  overseas（海外外派）、service（客服行政）、hospitality（餐旅接待）。
  ⚠️ 選不出來就不要輸出這個 key——填錯會把職缺歸到錯誤的篩選分類，
  比留空更糟（職安衛工程師被歸到「客服・行政」就是這樣來的）。
- title：職缺名稱
- subtitle：一句話副標
- description：SEO用一段話簡述（100字內）
- duties：陣列，每項 {{"h":"分組標題","items":["工作項目",...]}}，從 main_duties 整理分組
- must：陣列，必要條件，每項一句話
- plus：陣列，加分條件
- why：陣列，每項 {{"h":"賣點標題","p":"一段說明"}}，3-5點，從薪資/福利/招募原因等
  事實資料裡找出對求職者有吸引力的地方，用說服的語氣寫，不是條列事實
- benefits：陣列，福利項目，每項一句話
- faq：陣列，每項 ["問題","回答"]，從 faq_notes 整理成候選人常見問題的口吻
- salary_min, salary_max, employment, locations, must_skills：照事實資料原樣帶出來

事實資料：
{facts_text}
'''


def run_claude(prompt):
    e = env()
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as f:
        f.write(prompt)
        prompt_path = f.name
    try:
        cmd = [CLAUDE_BIN, '-p', '--model', MODEL, '--output-format', 'text',
               '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--setting-sources', '']
        r = subprocess.run(cmd + [open(prompt_path, encoding='utf-8').read()],
                            cwd=RECRUIT, env=e, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    finally:
        os.unlink(prompt_path)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or '').strip()
    return True, (r.stdout or '').strip()


def extract_json(text):
    m = re.search(r'\{[\s\S]*\}', text or '')
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def process_one(row):
    slug = row['slug']
    log(f'處理：{row.get("title") or slug}（{slug}）')
    facts = {k: row.get(k) for k in FACT_FIELDS}
    ok, out = run_claude(build_prompt(facts, row.get('client_named')))
    if not ok:
        log(f'❌ AI 生成失敗：{out[-300:]}')
        d1(f"UPDATE jobs SET jd_needs_ai_draft=0 WHERE slug={q(slug)}")
        DJ.tg_send(f'❌ <b>職缺文案自動生成失敗</b>　{DJ._esc(row.get("title") or slug)}\n'
                   f'{DJ._esc(out[-300:])}\n需要人工處理。', [], slug)
        return
    spec = extract_json(out)
    if not spec:
        log(f'❌ AI 回傳格式不是合法 JSON：{out[:200]}')
        d1(f"UPDATE jobs SET jd_needs_ai_draft=0 WHERE slug={q(slug)}")
        return
    # AI 給的 slug 是「建議」不是「決定」——這一輪還不能改網址，
    # 真正轉正在發布前由 jd_regen_tick.py 做（要先確認沒有對外過、沒撞名）。
    # 這裡只把它存起來，slug 欄位維持現況，免得這支自己把資料改壞。
    suggestion = spec.pop('slug', None)
    if suggestion:
        spec['slug_suggestion'] = suggestion
    spec['slug'] = slug
    spec, leaks = scrub_consultant_only(spec, row)
    if leaks:
        # 有攔到就一定要讓人知道——不然會以為 AI 本來就很乖
        log(f'⚠️ {slug}：AI 產出裡出現只給顧問的內容，已移除 {len(leaks)} 段')
        for k, v in leaks:
            log(f'    {k} = {v}')
    spec_json = json.dumps(spec, ensure_ascii=False)
    d1(f"UPDATE jobs SET jd_spec_json={q(spec_json)}, jd_regen_pending=1, "
       f"jd_needs_ai_draft=0, jd_updated_at=datetime('now','+8 hours'), "
       f"jd_updated_by='AI自動生成' WHERE slug={q(slug)}")
    log(f'✅ 完成，已交給重產排程：{slug}')



def _leaves(node):
    """數一個節點底下有幾段文字，用來判斷清理後有沒有缺角。"""
    if isinstance(node, dict):
        return sum(_leaves(v) for v in node.values())
    if isinstance(node, list):
        return sum(_leaves(x) for x in node)
    return 1 if isinstance(node, str) else 0


def scrub_consultant_only(spec, row):
    """輸出端攔截：只給顧問的內容不准出現在成品裡。

    白名單只擋「不餵給 AI」，擋不住 AI 從別的欄位推出來，或客戶把窗口電話
    順手打在 faq_notes 裡。這裡拿實際欄位值比對 AI 的產出，命中就拿掉。

    ⚠️ 只拿掉「中招的那一小段」，不要整個容器砍掉——初版把整份
       why 陣列都刪了（因為其中一則含窗口姓名），連乾淨的六個賣點一起消失。
       是自己寫測試才發現的。
    ⚠️ 只比對「夠長、夠特別」的值。hiring_manager 填「無」這種拿去全文比對，
       會把所有含「無」的句子都殺光。
    """
    hits = []

    def dirty(text):
        t = str(text)
        for k in CONSULTANT_ONLY:
            v = str(row.get(k) or '').strip()
            if len(v) < 4 or v in ('無', 'N/A', 'na', '不限', 'None'):
                continue
            if v in t:
                hits.append((k, v[:30]))
                return True
        return False

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(v, str):
                    if dirty(v):
                        continue          # 只丟這一個欄位
                    out[k] = v
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            out = []
            for x in node:
                if isinstance(x, str):
                    if dirty(x):
                        continue          # 只丟這一則
                    out.append(x)
                else:
                    cleaned = walk(x)
                    # ⚠️ 一則裡面只要有任何一段被拿掉，整則就丟掉。
                    #    留半截會變成「只有標題沒有內文」「只有問題沒有答案」，
                    #    看起來像頁面壞了，比少一則更糟。
                    if cleaned and _leaves(cleaned) == _leaves(x):
                        out.append(cleaned)
            return out
        return node

    return walk(spec), hits


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 600:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        # ⚠️ 查詢要連 CONSULTANT_ONLY 一起撈：那些欄位不會餵給 AI（只給
        #    FACT_FIELDS），但輸出端的攔截需要拿實際值去比對 AI 的產出。
        cols = ', '.join(FACT_FIELDS + CONSULTANT_ONLY)
        rows = d1(f"SELECT slug, client_named, {cols} FROM jobs "
                  f"WHERE jd_needs_ai_draft = 1 ORDER BY jd_updated_at ASC LIMIT 1")
        if not rows:
            return
        # 2026-09-10 加：多裝置協作保護鎖——本機的 LOCK 檔只防同一台重複跑，
        # 防不了另一台裝置同時搶到同一個 slug。搶到才處理，搶不到跳過。
        worker_id = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()
        claim = d1_raw(f"UPDATE jobs SET jd_needs_ai_draft=0, worker_id={q(worker_id)} "
                        f"WHERE slug={q(rows[0]['slug'])} AND jd_needs_ai_draft=1")
        if not claim.get('meta', {}).get('changes'):
            return
        process_one(rows[0])
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
