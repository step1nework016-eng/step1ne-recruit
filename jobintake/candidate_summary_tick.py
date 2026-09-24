#!/usr/bin/env python3
"""候選人詢問職缺｜第一輪摘要——批次預先產好，顧問回覆時直接複製。

背景：候選人在社群自動發文（招募文）下留言問職缺，顧問要私訊回覆，
但顧問／職缺都多，每次現場叫 AI 重寫一次太慢。這支批次幫每個開放中
職缺先產好一份候選人版摘要，存進 job_candidate_summaries，顧問後台
「職缺社群」分頁直接複製貼上，結尾的 LINE OA 連結由前端依當下選定
的顧問帳號動態代換（每個職缺的摘要內容不分顧問，只有 LINE 連結因人
而異，沒必要每個顧問各存一份幾乎一樣的內容）。

範本在 ~/claude-projects/工作流程技能包/recruiting-workflow/candidate-inquiry-summary/
SKILL.md（Jacky 提供，2026-09-03）——嚴格照裡面的格式、保密規則、
禁止事項執行，不自己加規則。

⚠️ 目前刻意沒有跟 jd_needs_ai_draft 那個旗標掛勾——那個旗標已經被
jd_ai_draft_tick.py 用來觸發「行銷文案」重生成，兩支搶同一個旗標會
互相吃掉對方的觸發（先跑的那支會把旗標清掉）。這支目前是獨立批次，
要重新產某個職缺的摘要就手動加 --force <slug>，之後如果要接自動
重生成，要另外開一個旗標欄位，不要共用 jd_needs_ai_draft。

⚠️ 2026-09-03 第一次實測就抓到一個真的洩漏：BIM工程師那筆
client_named=0（客戶要匿名），產出的候選人摘要卻寫出「美光相關產業
擴產案」——client_name_terms() 那套稽核只認得 client_companies 自己的
名稱／別名（弘昌），美光是「客戶服務的下游品牌」，不在那份名單裡，
標準稽核抓不到。這種第三方知名品牌洩漏跟 interview_daemon.py 的
confidential_client 處理是同一類問題（候選人會用「是不是美光」去套
話），做法也一樣：**用強指令擋，不是靠比對名單**——prompt 裡明講
「不能寫出任何可以反推真實客戶身分的具體公司/知名品牌/廠區地名」。
標準的 client_name_terms() 稽核（防漏寫出弘昌自己的名字）還是留著
當第二道防線，只是不能只靠它。

用法：
    python3 candidate_summary_tick.py           # 補齊還沒有摘要的開放中職缺
    python3 candidate_summary_tick.py --all     # 全部開放中職缺重新產一次
    python3 candidate_summary_tick.py <slug>    # 只處理指定職缺（測試用）
"""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
# ⚠️ 2026-09-24 加：social_post_agent.py 開頭 import autoupdate（在上一層），
#    不把上一層加進搜尋路徑就 ModuleNotFoundError，候選人摘要整支停擺。
sys.path.insert(0, RECRUIT)
SKILL_PATH = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/candidate-inquiry-summary/SKILL.md')
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 180

# 跟 interview_daemon.py 的 NO_TOOLS 同一個道理：純文字改寫，不需要查資料、
# 也不准碰這台電腦。
_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']

FACT_FIELDS = [
    'client_intro', 'must_skills', 'salary_note', 'salary_min', 'salary_max',
    'salary_unit', 'locations', 'employment', 'onboard_by', 'work_mode',
    'work_hours', 'leave_policy', 'employment_period', 'overtime_policy',
    'main_duties', 'education_level', 'required_conditions',
    'language_requirement', 'nice_to_have_skills', 'preferred_background',
    'benefits_detail', 'headcount', 'department',
]

# 借用 social_post_agent.py 現成的客戶名稱稽核（client_name_terms／
# audit_client_names）——防的是「漏寫出弘昌自己的名字」這一類，不是
# 萬能的，見檔頭說明。
_spec = importlib.util.spec_from_file_location(
    'social_post_agent', os.path.join(RECRUIT, 'social_post_agent.py'))
SPA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SPA)


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def env_with_cf():
    env = dict(os.environ)
    p = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('\'"')
    return env


def d1(sql):
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', DB, '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env_with_cf(), capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)[0].get('results', [])
    except Exception:
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return []


def q(v):
    if v is None:
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def build_prompt(job):
    facts = '\n'.join(f'{k}：{job[k]}' for k in FACT_FIELDS if job.get(k))
    skill = open(SKILL_PATH, encoding='utf-8').read()
    anon_rule = ''
    if str(job.get('client_named')) == '0':
        # 跟 jd_ai_draft_tick.py 的 anon_rule 同一個道理，但這裡多講一句
        # 「下游/知名品牌」——候選人問的是「這是不是幫XX大廠做的」，套的
        # 不是客戶自己的名字，是客戶服務的下游品牌，兩種都要擋。
        anon_rule = (
            '\n⚠️ 這個客戶對外要匿名——不能寫出任何可以反推真實客戶身分的內容，'
            '包含公司名稱、可以猜出地點的廠區地名、以及原始資料裡提到的任何'
            '知名下游品牌／客戶（例如「美光」這種），改用產業或專案性質描述'
            '（例如「高科技廠房擴建專案」）。原始資料裡即使明寫了這些名字，'
            '你也不能照抄進輸出。\n'
        )
    return f'''{skill}

---
{anon_rule}
⚠️ 這裡的「顧問 LINE OA 連結」欄位不要自己填任何網址，固定輸出文字
「{{{{LINE_LINK}}}}」這個佔位符原樣保留（含大括號），我會在顧問實際
複製使用時，依當下是哪位顧問動態換成他自己的連結——你不知道會是誰，
不要猜。

⚠️ 原始資料裡有些欄位（尤其 salary_note）寫的是給你的內部口徑指示，
不是要你逐條講給候選人聽的內容——那是顧問寫給你的備註（例如標了
【】的段落、「不要對候選人講」這類文字），你只需要照著那些限制去
「精簡改寫」，不能因為原始資料寫得詳細就跟著輸出很長。整篇輸出仍要
嚴格照 SKILL.md 的固定格式與長度（10-20秒可讀完），不能變成好幾段的
完整說明。

🚨 **薪資的講法只有一種：「平均薪資 X 起」／「平均薪資 X–Y」。**
**絕對不要寫成「X 萬以上」「40K 以上」「起薪 X 萬」「月薪 X 以上」。**
（2026-09-22 查到線上摘要寫的是「薪資：40K 以上，依學經歷面議」，
正是 Jacky 反映過不只一次、阿財那邊已經禁掉的講法。）
理由：這批職缺的實際核薪會依學歷、科系相關性、經歷調整。
「X 以上」聽起來像保證底薪，「平均薪資」才對得上「實際依條件核定」
這個事實。也不要自己換算年薪或把加班費加進去。

⚠️ 薪資如果原始資料沒有寫死金額或明確區間（例如寫「業主未公告」「顧問
初談了解條件後再確認」這類流程描述），輸出就只寫「依學經歷面議」或
「依經驗核定，實際待遇面談時談」這種一句話帶過，不要把「顧問會先了解
你的條件」「顧問會怎麼問」這種議薪的過程步驟寫進候選人看的訊息裡——
候選人不需要在這則訊息就知道議薪怎麼進行，講太細反而像在預告等一下
要盤問他。

⚠️ 這則訊息的目的只有一個：讓候選人秒懂職缺重點、有興趣就用 LINE
找顧問聊。**絕對不要在文案裡向候選人提出任何問題**（例如問期望薪資、
問方便聯絡時間、問有沒有相關經驗）——就算原始資料裡寫「要主動問
候選人」，那是指顧問後續電訪時要問的事，不是這則第一輪訊息的內容。
輸出結尾就是固定的面試流程說明＋LINE連結，中間不能夾雜任何提問句。

以下是這個職缺的原始資料：

職缺名稱：{job.get('title') or job.get('slug')}
{facts}

只輸出最終可以直接複製傳給候選人的文案，不要有任何額外說明、不要包程式碼區塊。'''


def run_claude(prompt):
    r = subprocess.run(
        ['claude', '-p', prompt, '--model', MODEL, '--output-format', 'text'] + NO_TOOLS,
        cwd=RECRUIT, env=env_with_cf(), capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or '').strip()
    return (r.stdout or '').strip(), None


def process_one(job):
    slug = job['slug']
    is_anon = str(job.get('client_named')) == '0'
    log(f'處理：{job.get("title") or slug}（{slug}）' + ('　[匿名客戶]' if is_anon else ''))
    out, err = run_claude(build_prompt(job))
    if err:
        log(f'❌ 產生失敗：{err[:300]}')
        return False
    if not out:
        log('❌ 沒有輸出內容')
        return False

    if is_anon:
        hits = SPA.audit_client_names(out)
        if hits:
            log(f'⚠️ 出現客戶名稱 {hits}，重產一次')
            out2, err2 = run_claude(build_prompt(job))
            hits2 = SPA.audit_client_names(out2 or '') if out2 else hits
            if out2 and not hits2:
                out = out2
            else:
                log(f'🚫 重產後仍有客戶名稱 {hits2 or hits}，不存檔，需要人工處理：{slug}')
                return False

    d1(f"INSERT INTO job_candidate_summaries (job_slug, summary_text, generated_at) "
       f"VALUES ({q(slug)}, {q(out)}, datetime('now','+8 hours')) "
       f"ON CONFLICT(job_slug) DO UPDATE SET summary_text=excluded.summary_text, "
       f"generated_at=excluded.generated_at")
    log(f'✅ 完成：{slug}')
    return True


def main():
    cols = ', '.join(FACT_FIELDS + ['client_named'])
    args = sys.argv[1:]

    if args and args[0] == '--all':
        rows = d1(f"SELECT slug, title, {cols} FROM jobs WHERE status IN ('open','active')")
    elif args and not args[0].startswith('--'):
        rows = d1(f"SELECT slug, title, {cols} FROM jobs WHERE slug={q(args[0])}")
    else:
        rows = d1(
            f"SELECT j.slug, j.title, {', '.join('j.' + c for c in FACT_FIELDS + ['client_named'])} FROM jobs j "
            f"LEFT JOIN job_candidate_summaries s ON s.job_slug = j.slug "
            f"WHERE j.status IN ('open','active') AND s.job_slug IS NULL"
        )

    if not rows:
        log('沒有需要處理的職缺')
        return

    ok, fail = 0, 0
    for job in rows:
        if process_one(job):
            ok += 1
        else:
            fail += 1
    log(f'完成：成功 {ok}，失敗 {fail}')


if __name__ == '__main__':
    main()
