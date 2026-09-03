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
import os, sys, json, re, subprocess, time, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
LOCK = '/tmp/step1ne-jd-ai-draft.lock'
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


def d1(sql):
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', 'step1ne-recruit',
         '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env(), capture_output=True, text=True, timeout=180)
    try:
        return json.loads(r.stdout)[0]['results']
    except Exception:
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return []


def q(v):
    if v is None:
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


FACT_FIELDS = [
    'client_name', 'title', 'client_intro', 'hiring_manager', 'years_min', 'must_skills',
    'client_screen_conditions', 'faq_notes', 'salary_note',
    'salary_min', 'salary_max', 'salary_unit', 'locations', 'employment', 'onboard_by',
    'team_size', 'interview_rounds', 'interview_stage_config', 'interview_who', 'has_test',
    'client_contact_name', 'client_contact_phone', 'headcount', 'work_mode',
    'work_hours', 'leave_policy', 'employment_period', 'overtime_policy',
    'hiring_reason', 'urgency', 'main_duties', 'reports_to', 'leads_team',
    'education_level', 'required_conditions', 'language_requirement',
    'nice_to_have_skills', 'preferred_background', 'personality_traits',
    'salary_tier_table', 'salary_structure_note', 'benefits_detail',
    'dispatch_to_permanent_policy', 'off_limits_note',
    'dispatch_client', 'department', 'work_environment_ratio', 'attendance_method',
    'interview_process', 'dispatch_range', 'contract_terms_note', 'overtime_detail',
    'onboarding_prep_note',
]


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
        cmd = ['claude', '-p', '--model', MODEL, '--output-format', 'text',
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
    spec['slug'] = slug
    spec_json = json.dumps(spec, ensure_ascii=False)
    d1(f"UPDATE jobs SET jd_spec_json={q(spec_json)}, jd_regen_pending=1, "
       f"jd_needs_ai_draft=0, jd_updated_at=datetime('now','+8 hours'), "
       f"jd_updated_by='AI自動生成' WHERE slug={q(slug)}")
    log(f'✅ 完成，已交給重產排程：{slug}')


def main():
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 600:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        cols = ', '.join(FACT_FIELDS)
        rows = d1(f"SELECT slug, client_named, {cols} FROM jobs "
                  f"WHERE jd_needs_ai_draft = 1 ORDER BY jd_updated_at ASC LIMIT 1")
        if not rows:
            return
        process_one(rows[0])
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
