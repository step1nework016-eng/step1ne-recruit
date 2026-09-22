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

排程：launchd 每天一次。

⚠️ 2026-09-22 Jacky 改版：從「自動挑 1 題直接發」改成「每天生一份 10 題菜單」。
    舊行為（挑最高分 1 題 → 塞進 social_post_queue → 自動產稿送審）已經拿掉，
    因為那等於系統自己決定要發什麼；Jacky 要的是自己看菜單挑。
    現在這支只負責：每天生 10 題（企業端 5＋求職端 5），寫進 topic_prompts
    （source='daily_menu'、audience_side 標 enterprise／jobseeker），**不排隊、不產稿**。
    顧問在後台「今日話題」那個 tab 看到這 10 題，挑一題、指定一位顧問，
    才會呼叫 /admin/social-post-request 走既有的產稿＋審核流程。
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
        'BashOutput,KillShell,Skill,Agent,Artifact,Monitor,'
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
research_topics + score_topics 兩個階段，只到「評完分、選好題」為止，
不要寫 Threads 文案（那是下一個階段，顧問在後台挑題後才由別人做）。

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
一共選出 10 題，分成兩組，各 5 題：

【企業端 enterprise】——說給「在找人的老闆／HR／用人主管」聽的角度：
    徵才困難、留才、薪酬行情、面試怎麼看人、用人單位常踩的雷、
    組織與管理、產業人力趨勢這一類。受眾是決定要不要花錢找人的那一方。

【求職端 jobseeker】——說給「在看機會的求職者／上班族」聽的角度：
    轉職時機、談薪、履歷、面試準備、職涯選擇、職場處境、被資遣怎麼辦
    這一類。受眾是決定要不要動的那一方。

兩組都要是近期真實話題，各自依 topic-scoring.md 評分。
如果某一組實在湊不滿 5 題夠格的即時題，可以補常青題，但要在那題的
warnings 裡標明「常青題」。企業端與求職端的題目不要重複同一個角度。

只輸出一個 JSON 物件，不要有 ```json 這種包裹符號、不要有其他文字：
{{
  "enterprise": [
    {{
      "topic": "一句話描述這個話題",
      "angle": "從獵頭視角怎麼切，2-3句話講清楚切入角度跟立場",
      "source_url": "主要引用來源網址，沒有就空字串",
      "source_note": "來源說明，例如「常青題」",
      "score_total": 0,
      "score_note": "為什麼給這個分數",
      "warnings": []
    }}
  ],
  "jobseeker": [
    {{ "同上格式": "共 5 題" }}
  ]
}}
enterprise 與 jobseeker 各放 5 個物件。'''


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
        if not data or not (data.get('enterprise') or data.get('jobseeker')):
            log(f'❌ 沒抓到有效 JSON：{(out or "")[:200]}')
            return

        def one_body(t):
            parts = [
                f"切入角度：{t.get('angle', '')}",
                f"來源：{t.get('source_url') or '（無）'}　{t.get('source_note', '')}",
                f"評分：{t.get('score_total', '')}／30　{t.get('score_note', '')}",
            ]
            if t.get('warnings'):
                parts.append('⚠️ ' + '；'.join(t['warnings']))
            return '\n'.join(parts)

        # ⚠️ category 一律 general——那是「產稿要用哪種語氣」在看的欄位
        # （general/ai），不是拿來分企業／求職的。企業端 vs 求職端存在
        # 獨立的 audience_side 欄位；「這批是每天自動生的菜單」記在 source。
        # source='daily_menu' 讓後台「今日話題」tab 撈得到今天這批。
        n_ok = 0
        for side, key in (('enterprise', 'enterprise'), ('jobseeker', 'jobseeker')):
            for t in (data.get(key) or [])[:5]:
                name = str(t.get('topic') or '').strip()[:120]
                if not name:
                    continue
                d1(f"INSERT INTO topic_prompts "
                   f"(name, body, created_at, updated_at, category, source, audience_side) "
                   f"VALUES ({q(name)}, {q(one_body(t))}, datetime('now','+8 hours'), "
                   f"datetime('now','+8 hours'), {q('general')}, {q('daily_menu')}, {q(side)})")
                n_ok += 1
        log(f'✅ 已生成今日話題菜單：{n_ok} 題（企業端＋求職端），'
            f'顧問到後台「今日話題」tab 挑題後才會產稿')
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
