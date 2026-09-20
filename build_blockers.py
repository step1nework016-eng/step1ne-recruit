#!/usr/bin/env python3
"""到職障礙清單產生器 —— 讓阿財不再漏掉「這個人到底能不能來上班」。

為什麼要有這支：
    2026-08-19 回頭看十份面談報告，同一種漏洞在不同職缺重複出現——
    孙悦的簽證換雇主時程沒問、鍾欣修的良民證與 NDA 完全沒問、
    莊懿璇的跨廠調派意願沒確認、黎薇恩最核心的開播時數也沒問到。

    這些不是模型不夠聰明，是**通用題庫裡沒有這些題**。動機、經歷、穩定度
    每個缺都一樣，所以題庫問得到；但「簽證換雇主要多久」「願不願意跨廠調派」
    是各案獨有的，題庫沒有就漏了。而它們偏偏是決定成敗的那一題——
    人再好，簽證下不來就是不能到職。

跟 build_expertise.py 的分工：
    專業題庫回答「他會不會做這份工作」，這支回答「他到底能不能來上班」。
    兩件事都漏過，但漏的原因不同，所以分開產。

    這支**不上網**：到職障礙是從 JD 與職缺條件推導出來的，不需要查外部資料，
    所以跑得快很多（一個職缺約 30 秒，題庫要 3 分鐘）。

用法：
    python3 build_blockers.py <job_slug>
    python3 build_blockers.py --all        # 補齊所有還沒有清單的職缺
    python3 build_blockers.py <slug> --force
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 240

# 這支不需要上網，所以工具全禁——跟產報告那支同一套標準。
_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def env_with_cf():
    env = dict(os.environ)
    p = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v.strip().strip("'\"")
    return env


def d1(sql):
    r = subprocess.run(['npx', 'wrangler', 'd1', 'execute', DB, '--remote', '--json',
                        f'--command={sql}'],
                       cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'd1 失敗：{(r.stderr or r.stdout)[-400:]}')
    return json.loads(r.stdout[r.stdout.index('['):])[0].get('results', [])


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def _extract_json(text):
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


PROMPT = '''你要為一個職缺列出「到職障礙清單」——也就是面談時**一定要問到**、
否則人選再優秀也可能到不了職的那些事。

【這個職缺】
職稱：{title}
工作地點：{locations}
僱傭型態：{employment}／服務線：{service_line}
職缺條件與說明：
{jd}
{extra}

【⚠️ 先分辨：這件事該問誰】
到職障礙有兩種，混在一起會出事：

  A. **該問候選人的**——他自己知道答案的事。
     例：你有沒有日本工作簽證？離職預告期多久？願不願意跨廠調派？

  B. **該向用人單位確認的**——候選人不知道、也不該由他回答的事。
     例：薪水怎麼匯給他？要不要開當地帳戶？公司協不協助辦簽證？排班怎麼排？

真實事故（2026-08-19 直播主那場）：清單裡混進了「跨境金流／外幣帳戶對接」，
阿財就拿去問候選人，結果候選人反問「台灣人要怎麼申請大陸銀行帳號」——
**我們答不出來**。問一個自己答不出來的問題，只會讓候選人覺得我們沒搞清楚就在招人。
而且那個職缺本來就寫「保底由公會以新台幣發放」，大陸帳戶根本不是到職門檻。

所以每一條都要標 `ask_who`：
  · "候選人" → 阿財會在面談中問，也會出現在給用人單位的報告裡
  · "用人單位" → **不會問候選人**，只列給顧問去跟客戶確認

⚠️ 判準是「**他知不知道答案**」，不是「重不重要」。
   薪資怎麼匯很重要，但候選人不知道，問他只是浪費一題。

【什麼算到職障礙】
決定「這個人能不能、什麼時候能來上班」的條件，例如：
- 簽證、居留、工作許可（含換雇主要多久、誰負責辦）
- 現職的競業條款、離職預告期、需不需要交接
- 執照／證照的有效期與換證，該職務法定必備的資格
- 良民證、體檢、保證人、NDA 這類到職前文件
- 排班、輪值、調派、駐外、出差頻率的實際配合意願
- 這個職缺特有的量化門檻（例如每月最低時數、天數、值班週期）
- 交通與住宿的可行性（通勤距離、有沒有宿舍、需不需要自備交通工具）

【硬性要求】
1. **只列這個職缺真的有的**。沒有簽證問題就不要為了湊數寫簽證。
   寧可 3 條真的，不要 8 條湊的。
2. 每一條都要寫成**可以直接問出口的問題**，不是欄位名。
   ✘「確認簽證狀態」　✔「您目前有日本的工作簽證嗎？如果要換雇主，
      您了解大概需要多久嗎？」
3. `why` 要寫清楚**沒問到會怎樣**——那是顧問判斷要不要補問的依據。
4. `critical` 標 true 的是「這條不過就不用談了」的，其餘 false。
   critical 最多 3 條，太多就失去意義。
5. 不要列違反就業服務法第 5 條的條件（年齡、性別、婚育、國籍、宗教…）。
   ⚠️ 簽證與工作許可是**法定工作資格**，可以問；但不可以問國籍本身。
6. 全部用繁體中文。

【只輸出這個 JSON，不要有其他文字】
{{
  "blockers": [
    {{
      "item": "障礙名稱，10 字內",
      "ask": "直接問出口的問題",
      "why": "沒問到會怎樣",
      "critical": true,
      "acceptable": "什麼樣的回答算過關（讓阿財知道要不要往下追）",
      "ask_who": "候選人|用人單位"
    }}
  ]
}}'''


def build(job, force=False):
    slug = job['slug']
    row = d1(f"SELECT job_slug, blockers_json FROM job_expertise WHERE job_slug = {q(slug)}")
    if not force and row and row[0].get('blockers_json'):
        log(f'{slug}：已經有到職障礙清單，跳過')
        return None

    jd = []
    for k, label in (('description', '工作內容'), ('requirements', '資格條件'),
                     ('must_skills', '必備技能'), ('salary_note', '待遇說明'),
                     ('onboarding_prep_note', '到職準備')):
        if job.get(k):
            jd.append(f'■ {label}\n{job[k]}')
    extra = []
    if job.get('onboard_by'):
        extra.append(f'客戶希望到職時間：{job["onboard_by"]}')
    if job.get('interview_language'):
        extra.append(f'需要驗證的外語：{job["interview_language"]}')

    prompt = PROMPT.format(
        title=job.get('title') or slug,
        locations=job.get('locations') or '未提供',
        employment=job.get('employment') or '未提供',
        service_line=job.get('service_line') or '未提供',
        jd='\n\n'.join(jd) or '（JD 內容不足，請依職稱與地點推導）',
        extra=('\n' + '\n'.join(extra)) if extra else '')

    log(f'{slug}：產到職障礙清單…')
    r = subprocess.run(['claude', '-p', prompt, '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=env_with_cf(), timeout=TIMEOUT)
    obj = _extract_json(r.stdout)
    if not obj or not obj.get('blockers'):
        log(f'❌ {slug}：沒產出。回覆開頭：{(r.stdout or r.stderr or "")[:160]}')
        return None

    bl = json.dumps(obj['blockers'], ensure_ascii=False)
    if row:
        d1(f"UPDATE job_expertise SET blockers_json = {q(bl)} WHERE job_slug = {q(slug)}")
    else:
        # 這個職缺還沒有專業題庫也沒關係，到職障礙可以先存
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        d1(f"INSERT INTO job_expertise (job_slug, blockers_json, built_at) "
           f"VALUES ({q(slug)}, {q(bl)}, {q(now)})")
    crit = sum(1 for b in obj['blockers'] if b.get('critical'))
    toclient = sum(1 for b in obj['blockers'] if b.get('ask_who') == '用人單位')
    log(f'✅ {slug}：{len(obj["blockers"])} 條（{crit} 條不過就不用談、'
        f'{toclient} 條是要跟用人單位確認的、不會問候選人）')
    return obj


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force = '--force' in sys.argv
    if '--all' in sys.argv:
        jobs = d1("SELECT j.* FROM jobs j LEFT JOIN job_expertise e ON e.job_slug = j.slug "
                  "WHERE COALESCE(j.status,'open') != 'closed' "
                  "AND (e.blockers_json IS NULL OR e.job_slug IS NULL)")
        log(f'還沒有到職障礙清單的職缺：{len(jobs)} 個')
        for job in jobs:
            try:
                build(job, force)
            except Exception as e:
                log(f'❌ {job["slug"]}：{e}')
        return
    if not args:
        print(__doc__)
        return
    rows = d1(f"SELECT * FROM jobs WHERE slug = {q(args[0])}")
    if not rows:
        log(f'找不到職缺 {args[0]}')
        return
    build(rows[0], force)


if __name__ == '__main__':
    main()
