#!/usr/bin/env python3
"""職缺文案稽核 —— 上架前先當一次「會挑剔的候選人」。

為什麼要有這支：
    職缺文案是顧問自己寫的，他不會看見自己的盲點。
    2026-08-19 手動查 VIP 接待那一個缺，一次抓到四個問題，每一個單看都合理：
      · 同一職缺三個薪資口徑（104 刊 40-50K、需求表 35K 起、站上卡片 3.5-4 萬）
      · 頁面同時寫「歡迎無經驗」與「月薪 40,000 起」，而無經驗實際是 35K
      · 客戶匿名寫成「雲端服務／機房營運商」，讀起來像灰產
      · 加分技能列「KTV 經驗」，與職務關聯待釐清
    這些不是誰粗心，是**寫的人看不見**——所以要有一支專門從外面看的。

Jacky 2026-08-19 定的重點順序（照這個排，不要自己改）：
    1. **最重要是吸不吸引人**。網站寫得不吸引人，再好的職缺也沒人投。
    2. 敏感或不利的條件不要很直地全部攤出來，要委婉——但**委婉不是隱瞞**。
       可以換順序、換說法、把配套跟限制寫在一起；
       不可以刪掉、不可以講得讓人誤會、不可以藏到最下面小字。
       （真實例子：BIM 那個缺把「⚠️ 無交通車，交通工具須自備」加粗放在福利中間，
       但它其實有每月上限一萬的通勤補助與免費宿舍二擇一——同樣的事實，
       從「警告你沒有交通車」改寫成「你有兩個選擇，兩個都補貼」就完全不同。）

分兩種性質，處理方式不同：
    · **法遵與事實錯誤**（就服法紅線、薪資前後不一、內部備註漏進對外文案、
      該保密的客戶名稱露出）→ 判「要修文案」，並交給 fix_job_copy.py 去改
    · **觀感與吸引力** → 只給建議，不擋。觀感是主觀的，硬擋會變成跟顧問吵架。

⚠️ 2026-08-21 Jacky 改的：原本有「不可上架」這個判定，但那是個死路——
   標了之後沒有人會去修，六個職缺被標了兩天全部照樣開著、一個字都沒動。
   判定改成「要修文案」，而且判完直接接 fix_job_copy.py 去改，
   讓「發現問題」跟「問題被解決」變成同一條線，不是兩件事。

⚠️ 對「吸引力」的建議有一條硬規則：**講不出具體理由就不准提**。
   不然它會對每個職缺都嫌一句，顧問看兩次就不看了。

用法：
    python3 audit_job_copy.py <job_slug>
    python3 audit_job_copy.py --all
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 420
SITE = 'https://step1ne.com'

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


def page_text(slug):
    """抓公開職缺頁的實際文字。

    ⚠️ 一定要抓「真的長在網路上的那一份」，不能只看資料庫。
       VIP 接待那次的三個薪資口徑，就是資料庫寫一個、頁面寫另一個、
       列表卡片再寫第三個——只讀資料庫永遠看不到這種矛盾。
    """
    try:
        req = urllib.request.Request(f'{SITE}/jobs/{slug}/',
                                     headers={'user-agent': 'step1ne-audit/1.0'})
        html = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace')
    except Exception as e:
        return '', f'（抓不到公開頁面：{e}）'
    body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'\s+', ' ', body).strip()
    return body[:12000], ''


PROMPT = '''你是獵頭公司的文案總監，要在職缺上架前做最後一次把關。
用**候選人的眼睛**讀，不是用寫的人的眼睛。

【職缺資料庫欄位】
{db}

【公開職缺頁實際文字】
{page}
{pagenote}

━━━━━━━━━━━━━━━━━━━━

你要交出三種東西，**重要性由上而下**：

■ 一、吸引力（最重要）
網站寫得不吸引人，再好的職缺也沒人投。請具體指出：
- 這篇讀完，候選人知不知道「為什麼要選這個缺而不是別的」？
- 有沒有把真正的賣點埋掉？（常見：最好的條件寫在最下面、或被夾在一堆條件中間）
- 開頭三行抓不抓得住人？標題有沒有讓人誤會或聯想到不好的東西？
⚠️ **講不出具體理由的批評不要提**。不要寫「可以更生動」這種空話——
   要指出是哪一句、為什麼那樣寫會流失人、改成什麼樣子。

■ 二、敏感條件的寫法（第二重要）
不利或敏感的條件（無交通車、需 on-call、輪班、派遣、約聘、需自備工具、
高工時、偏遠地點、無經驗薪資較低…）**不要很直白地全部攤出來，要委婉**。

⚠️ 但委婉**不是隱瞞**。分界線：
   ✅ 可以：換順序（好處先講）、換說法（「未配置交通車」而不是「⚠️ 無交通車」）、
      把配套跟限制寫在一起（「宿舍免費或通勤補助每月上限一萬，二擇一」）
   ❌ 不可以：刪掉不利條件、講得讓人誤會、藏到最下面小字
   隱瞞的代價是面談或到職後才爆掉，人就白找了。

真實案例：某職缺把「⚠️ 無交通車，交通工具須自備」加粗放在福利中間，
但它其實有「免費宿舍」與「通勤補助每公里 7 元、上限一萬」二擇一——
同樣的事實，改寫成「住或通勤兩種都有補助，二擇一」就從警告變成選擇。

■ 三、一定要修掉的問題（法遵與事實）
這一類**不是給建議，是一定要改**（會有另一支 agent 依你列的內容去改文案）：
- 就業服務法第 5 條：性別、年齡、婚姻、生育、國籍、身心障礙、宗教、容貌等
  差別待遇字眼
- 薪資口徑前後不一致（頁面／卡片／資料庫互相矛盾）
- 內部矛盾（例如寫「歡迎無經驗」但薪資只標有經驗那一段）
- 內部備註漏進對外文案（顧問給自己看的話、客戶的內部要求）
- 該保密的客戶名稱或廠區地名露出
- 就服法薪資揭露：月薪未達 4 萬必須公開範圍，不能只寫「面議」

【只輸出這個 JSON，不要有其他文字】
{{
  "verdict": "可上架|建議修改|要修文案",
  "appeal": [
    {{"issue": "吸引力上的問題，指出是哪一句", "why": "為什麼這樣寫會流失人",
      "suggest": "改成什麼樣子（寫出實際文字）"}}
  ],
  "sensitive": [
    {{"original": "原本那句", "problem": "為什麼這樣寫傷到吸引力",
      "rewrite": "改寫後的完整文字（事實一個都不能少）"}}
  ],
  "blockers": [
    {{"type": "就服法|薪資不一致|內部矛盾|內部備註外流|保密|薪資揭露",
      "detail": "問題是什麼", "evidence": "在哪裡看到的原文", "fix": "怎麼改"}}
  ],
  "summary": "一句話總評，站在候選人角度"
}}'''


def audit(job):
    slug = job['slug']
    page, note = page_text(slug)
    fields = []
    for k in ('title', 'client_name', 'client_named', 'confidential_client', 'locations',
              'employment', 'service_line', 'seniority', 'salary_min', 'salary_max',
              'salary_note', 'must_skills', 'years_min', 'talking_points', 'social_angle',
              'onboard_by', 'interview_language', 'status'):
        v = job.get(k)
        if v not in (None, ''):
            fields.append(f'{k}: {str(v)[:600]}')

    prompt = PROMPT.format(db='\n'.join(fields), page=page or '（沒有公開頁面）', pagenote=note)
    log(f'{slug}：稽核中…')
    r = subprocess.run(['claude', '-p', prompt, '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=env_with_cf(), timeout=TIMEOUT)
    obj = _extract_json(r.stdout)
    if not obj:
        log(f'❌ {slug}：沒有產出。回覆開頭：{(r.stdout or r.stderr or "")[:160]}')
        return None

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1(f"INSERT INTO job_audits (job_slug, audited_at, verdict, blockers_json, fixes_json, "
       f"appeal_json, rewrite_json, raw_len) VALUES ("
       f"{q(slug)}, {q(now)}, {q(obj.get('verdict'))}, "
       f"{q(json.dumps(obj.get('blockers') or [], ensure_ascii=False))}, "
       f"{q(json.dumps({'summary': obj.get('summary')}, ensure_ascii=False))}, "
       f"{q(json.dumps(obj.get('appeal') or [], ensure_ascii=False))}, "
       f"{q(json.dumps(obj.get('sensitive') or [], ensure_ascii=False))}, {len(page)})")
    nb = len(obj.get('blockers') or [])
    log(f"✅ {slug}：{obj.get('verdict')}／吸引力 {len(obj.get('appeal') or [])} 點、"
        f"敏感寫法 {len(obj.get('sensitive') or [])} 點、必須擋下 {nb} 點")
    return obj


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--all' in sys.argv:
        jobs = d1("SELECT * FROM jobs WHERE COALESCE(status,'open') != 'closed'")
        log(f'要稽核的職缺：{len(jobs)} 個')
        for j in jobs:
            try:
                audit(j)
            except Exception as e:
                log(f'❌ {j["slug"]}：{e}')
        return
    if not args:
        print(__doc__)
        return
    rows = d1(f"SELECT * FROM jobs WHERE slug = {q(args[0])}")
    if not rows:
        log(f'找不到職缺 {args[0]}')
        return
    audit(rows[0])


if __name__ == '__main__':
    main()
