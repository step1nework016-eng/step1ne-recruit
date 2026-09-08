#!/usr/bin/env python3
"""每日話題研究——headhunter-social-content-skill 技能包的本地執行器。

技能包本體在 ~/claude-projects/工作流程技能包/recruiting-workflow/headhunter-social-content-skill/
（2026-09-03 Jacky 給的 zip，六個 workflow 都驗收過，見該資料夾的 VALIDATION.md）。
這支只接手該技能包 references/telegram-integration-todo.md 點名要做但還沒做的
「串進既有 AI Agent 系統」那一步。

⚠️ 分工是刻意切開的，不是偷懶：
    這支只做 research_topics + score_topics 兩個階段（技能包本身設計成
    「可由主 Agent 分段呼叫」，這兩階段本來就是獨立單元）——找題、評分，
    寫成一份「話題簡報」存進 topic_prompts，跟 Jacky／Phoebe 手動寫的
    話題簡報進同一張表、同一套審核流程。
    真正把簡報寫成 Threads 文案的 write_threads 階段，交給既有的
    social_post_agent.py／process_topic()——那支已經有客戶名稱稽核、
    就業服務法第5條稽核、顧問各自的語氣風格（style_prompts）、
    Telegram 審核（✅確認發布／🔄重新產一次／❌不發這篇）整套防線，
    沒有理由另外做一套平行的產文邏輯。

    也因為這樣，references/telegram-integration-todo.md 列的「目標TG群組」
    「既有Bot/Webhook」「callback格式」這些問題全部沿用 process_topic()
    已經在用的答案（TG_THREAD_SOCIAL、soc_approve/regen/skip），不用
    另外去問。skill 本身的 write_threads／content-writing／
    hook-library／templates 這幾份在這個整合裡沒被用到，找題＋評分
    才是這支真的在跑的部分。

排程：launchd 每天一次，找 1 篇最高分話題，寫進 topic_prompts +
social_post_queue（topic_id 指過去），下一輪 social_post_agent.py
排程跑到就會自動產草稿送審——不用另外通知，跟其他排隊項目走同一條路。
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
SKILL_DIR = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/headhunter-social-content-skill')
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 600
LOCK = '/tmp/step1ne-topic-research.lock'

# 跟 build_expertise.py 同一個道理：可以查資料，但不准碰這台電腦。
# headless 模式下 WebSearch 要配 bypassPermissions 才不會卡在權限提示。
_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,AskUserQuestion,TodoWrite,'
        'BashOutput,KillShell,SlashCommand,Skill,Agent,Artifact,Monitor,'
        'CronCreate,CronDelete,CronList')
RESEARCH_TOOLS = ['--disallowed-tools', _BAN, '--setting-sources', '',
                   '--permission-mode', 'bypassPermissions']


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def env_with_cf():
    env = dict(os.environ)
    for f in ('cf.env', 'step1ne-tg.env'):
        p = os.path.expanduser(f'~/.config/workflow-os/{f}')
        if not os.path.exists(p):
            continue
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('\'"')
    return env


def d1_raw(sql):
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', DB, '--remote', '--json', '--command', sql],
        cwd=RECRUIT, env=env_with_cf(), capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)[0]
    except Exception:
        err = (r.stderr or r.stdout or '')[-300:].strip()
        if err:
            log(f'⚠️ D1 查詢失敗：{err}')
        return {'results': [], 'meta': {}}


def d1(sql):
    return d1_raw(sql).get('results', [])


def q(v):
    if v is None:
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def read(rel):
    p = os.path.join(SKILL_DIR, rel)
    return open(p, encoding='utf-8').read() if os.path.exists(p) else ''


def recent_topics():
    """避免跟最近排過的話題重複——不管是 AI 研究還是 Jacky／Phoebe 手寫的都算。"""
    rows = d1("SELECT name FROM topic_prompts ORDER BY id DESC LIMIT 20")
    return [r['name'] for r in rows if r.get('name')]


def build_prompt():
    skill_md = read('SKILL.md')
    brand = read('config/brand-profile.md')
    audience = read('config/target-audience.md')
    research_wf = read('workflows/topic-research.md')
    scoring_wf = read('workflows/topic-scoring.md')
    avoid = recent_topics()
    avoid_txt = ('\n\n【最近已經排過、不要重複的話題】\n' + '\n'.join(f'- {t}' for t in avoid)) if avoid else ''
    today = datetime.datetime.now().strftime('%Y-%m-%d')

    return f'''你現在執行 headhunter-social-content-skill 技能包（見下方內容）的
research_topics + score_topics 兩個階段，只到「評完分、選出最高分那一題」為止，
不要寫 Threads 文案（那是下一個階段，交給別人做）。

今天日期：{today}

【技能包 SKILL.md】
{skill_md}

【品牌定位 config/brand-profile.md】
{brand}

【目標受眾 config/target-audience.md】
{audience}

【workflows/topic-research.md】
{research_wf}

【workflows/topic-scoring.md】
{scoring_wf}
{avoid_txt}

請實際用 WebSearch 查近期（優先近7天內）真實的求職／招募／職場議題，
找 6～10 題候選，依 topic-scoring.md 評分，選出分數最高的 1 題——
如果按規則判斷「近期沒有夠格的即時題」，可以選一個常青題，但要在
warnings 裡明講「這題是常青題，不是今日即時話題」。

只輸出一個 JSON 物件，不要有 ```json 這種包裹符號、不要有其他文字：
{{
  "topic": "一句話描述這個話題",
  "primary_ta": "主要目標受眾",
  "angle": "這題可以怎麼從獵頭視角切入，2-3句話講清楚切入角度跟立場",
  "source_url": "主要引用來源的網址，沒有就填空字串",
  "source_note": "來源說明，例如「這是常青題，不是今日即時話題」",
  "score_total": 0,
  "score_note": "簡短說明為什麼給這個分數",
  "warnings": []
}}'''


def run_claude(prompt):
    e = env_with_cf()
    r = subprocess.run(
        ['claude', '-p', prompt, '--model', MODEL, '--output-format', 'text'] + RESEARCH_TOOLS,
        cwd=RECRUIT, env=e, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or '').strip()
    return (r.stdout or '').strip(), None


def extract_json(text):
    m = re.search(r'\{[\s\S]*\}', text or '')
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def main():
    if os.path.exists(LOCK) and __import__('time').time() - os.path.getmtime(LOCK) < 3600:
        return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        prompt = build_prompt()
        out, err = run_claude(prompt)
        if err:
            log(f'❌ 研究失敗：{err[:300]}')
            return
        data = extract_json(out)
        if not data or not data.get('topic'):
            log(f'❌ 沒抓到有效 JSON：{(out or "")[:200]}')
            return

        name = str(data['topic']).strip()[:120]
        body_parts = [
            f"切入角度：{data.get('angle', '')}",
            f"主要受眾：{data.get('primary_ta', '')}",
            f"來源：{data.get('source_url') or '（無）'}　{data.get('source_note', '')}",
            f"評分：{data.get('score_total', '')}／30　{data.get('score_note', '')}",
        ]
        if data.get('warnings'):
            body_parts.append('⚠️ ' + '；'.join(data['warnings']))
        body = '\n'.join(body_parts)

        # ⚠️ 2026-09-03 修：category 這欄本來就是「一鍵發文」話題下拉選單在用的
        # 分類（general／ai），跟「這則是不是AI自動研究產生的」是兩件事，
        # 一開始誤把 category 寫成 'AI研究'，會讓這筆話題在那個下拉選單裡
        # 完全選不到（category 對不上 general/ai 任何一個）。這裡的話題是
        # 一般時事討論（不是針對阿財面談的話題），category 用 'general' 才對；
        # 「AI自動產的」改記在獨立的 source 欄位，不跟 category 混在一起。
        ins = d1_raw(f"INSERT INTO topic_prompts (name, body, created_at, updated_at, category, source) "
                     f"VALUES ({q(name)}, {q(body)}, datetime('now','+8 hours'), datetime('now','+8 hours'), "
                     f"{q('general')}, {q('ai_research')})")
        # last_row_id 要從「這次 INSERT 呼叫自己回傳的 meta」拿，不能另外開一次
        # SELECT last_insert_rowid()——wrangler d1 execute 每次呼叫都是新連線，
        # 跨呼叫查 last_insert_rowid() 拿到的不會是剛剛那筆。
        topic_id = (ins.get('meta') or {}).get('last_row_id')
        if not topic_id:
            log('❌ topic_prompts 寫入後拿不到 id，中止排隊')
            return

        d1(f"INSERT INTO social_post_queue (job_slug, account_id, topic_id, requested_at) "
           f"VALUES ({q('💬 AI研究話題：' + name)}, NULL, {topic_id}, datetime('now','+8 hours'))")
        log(f'✅ 已排入話題：{name}（topic_id={topic_id}），下一輪 social_post_agent.py 會自動產草稿送審')
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
