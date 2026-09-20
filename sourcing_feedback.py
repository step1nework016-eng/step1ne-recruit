#!/usr/bin/env python3
"""主動開發回饋迴圈 —— 顧問說「不合適」之後，下一次要怎麼找得不一樣。

為什麼要有這支（2026-08-20 Jacky 交辦）：
    後台原本就有「不合適」這個按鈕，但**全系統沒有任何程式讀它**。
    顧問按十次，下一次搜尋的條件跟第一次一模一樣——那個按鈕只是把人藏起來。

    而且更前面就斷了：原本按下去不會問為什麼。沒有原因，就算有程式去讀也學不到——
    「不合適」可能是地點太遠、資歷差太多、或根本不是這個職類，
    這三種要做的調整**完全相反**。所以先在後台加了固定選項，這支才有東西可讀。

它做什麼、不做什麼：
    ✅ 讀不合適的原因 → 判斷該調什麼 → **發提案到 TG 等人核准**
    ❌ 不自己改搜尋策略。

    為什麼保留人的關卡：讓它自動改自己的搜尋條件，出錯時很難查出是哪一次改壞的。
    策略是會累積影響的東西，改錯一次會污染之後每一次搜尋，
    而顧問要到很久以後才會發現「怎麼最近撈的都怪怪的」。

原因對應的調整方向（這是這支的核心判斷，寫死不讓模型自由發揮）：
    地點不行       → 搜尋要加地區限制，或這個缺的地點本來就難找，該跟業主談
    資歷差太多     → 調分數門檻或年資條件
    職類根本不對   → **改搜尋詞**。這條最嚴重，代表整批都白撈，要優先處理
    找不到聯絡方式 → 換來源（這個來源撈得到人但拿不到聯絡方式，等於沒用）
    已有工作或沒意願 → 這是正常耗損，**不需要調整策略**，不要為了這個改東西
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = 'claude-sonnet-5'
TIMEOUT = 240
MIN_SAMPLES = 5          # 同一個職缺累積幾筆才值得分析
API = 'https://step1ne-recruit-api.aiagentg888.workers.dev'
TG_THREAD_SOURCED = 3477

_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']

# 這幾個原因不代表策略有問題——人家已經有工作、沒意願，是正常耗損。
# 不排除的話，一個熱門職缺會因為「大家都有工作」被判定成要改搜尋詞，越調越偏。
NOISE = {'已有工作或沒意願'}


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


D = _daemon()
d1, q = D.d1, D.q


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


PROMPT = '''顧問在人才池裡把一批人標成「不合適」，並選了原因。
你要判斷：**下一次找人要改什麼**。

【職缺】
{job}

【這次撈到的人被退掉的原因統計】
{stats}

【被退掉的人長什麼樣（抽樣）】
{samples}

【目前的搜尋設定】
{search}

━━━━━━━━━━━━━━━━━

【原因對應的調整方向（照這個判斷，不要自己發明）】
· 地點不行 → 搜尋加地區限制；若這個缺的地點本來就難找，要指出「該跟業主談」
· 資歷差太多 → 調分數門檻或年資條件（要說調高還是調低）
· 職類根本不對 → **改搜尋詞**。這條最嚴重，代表整批都白撈，優先處理
· 找不到聯絡方式 → 換來源。這個來源撈得到人但拿不到聯絡方式，等於沒用
· 已有工作或沒意願 → 正常耗損，**不要為這個改任何東西**

【硬性要求】
1. **講得出具體改成什麼**。「優化搜尋條件」這種話不要寫——
   要寫「搜尋詞從 X 改成 Y」「年資門檻從 2 年降到 1 年」「這個缺不要再用 GitHub 撈」。
2. 樣本少就老實說信心不足。**不要為了有東西交而硬給建議**——
   改錯策略會污染之後每一次搜尋，而且顧問要很久以後才會發現。
3. 如果統計顯示的其實是「正常耗損」，就直接說**不需要調整**。這是正確答案之一。
4. 繁體中文。

【只輸出這個 JSON】
{{
  "confidence": "高|中|低",
  "diagnosis": "一句話：這批人為什麼不對",
  "adjustments": [
    {{"what": "要改什麼（搜尋詞／地區／年資門檻／來源）",
      "from": "現在是什麼", "to": "改成什麼", "why": "根據哪個原因"}}
  ],
  "no_change_needed": false,
  "to_consultant": "給顧問的一句話。如果問題不在搜尋而在職缺本身（例如薪資或地點根本沒競爭力），要直說。"
}}'''


def analyse(job_slug, rows):
    job = d1(f"SELECT slug,title,locations,must_skills,years_min,salary_min,salary_max,"
             f"employment,seniority FROM jobs WHERE slug={q(job_slug)}")
    if not job:
        log(f'{job_slug}：找不到這個職缺')
        return None
    job = job[0]
    jd = '\n'.join(f'{k}：{v}' for k, v in job.items() if v not in (None, '', 0))

    stats = {}
    for r in rows:
        stats[r['reject_reason']] = stats.get(r['reject_reason'], 0) + 1
    real = {k: v for k, v in stats.items() if k not in NOISE}
    if not real:
        log(f'{job_slug}：退掉的原因全是「已有工作或沒意願」，這是正常耗損，不需要調整')
        return None

    samples = d1(f"SELECT name,headline,company,location,source,reject_reason "
                 f"FROM sourced_candidates WHERE job_slug={q(job_slug)} "
                 f"AND status='rejected' AND reject_reason IS NOT NULL LIMIT 12")
    stxt = '\n'.join(f"・{s.get('name')}｜{s.get('headline') or s.get('company') or '（無職稱）'}"
                     f"｜{s.get('location') or '地點不明'}｜來源 {s.get('source')}"
                     f"　→ 退掉原因：{s.get('reject_reason')}" for s in samples)
    search = '\n'.join(f'{k}：{v}' for k, v in
                       (('來源', ', '.join(sorted({s['source'] for s in samples}))),
                        ('年資門檻', job.get('years_min')),
                        ('必備條件', (job.get('must_skills') or '')[:300])) if v)

    prompt = PROMPT.format(job=jd, stats='\n'.join(f'{k}：{v} 位' for k, v in stats.items()),
                           samples=stxt, search=search)
    before = D._snapshot_session_files()
    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=D.env_with_cf(), timeout=TIMEOUT)
    D.log_token_usage(f'sourcing_feedback:{job_slug}', 'sourcing_feedback', prompt, before)
    obj = _extract_json(r.stdout)
    if not obj:
        log(f'{job_slug}：模型沒吐出可用結果')
        return None
    return obj, stats, len(rows)


def notify(job_slug, obj, stats, n):
    if obj.get('no_change_needed'):
        log(f'{job_slug}：判定不需要調整——{obj.get("diagnosis")}')
        return
    title = d1(f"SELECT title FROM jobs WHERE slug={q(job_slug)}")
    title = title[0]['title'] if title else job_slug
    lines = [f'🔧 主動開發策略建議｜{title}',
             f'（依據 {n} 位被退掉的人選，信心：{obj.get("confidence")}）', '',
             f'📉 {obj.get("diagnosis")}', '',
             '退件原因：' + '、'.join(f'{k} {v}' for k, v in stats.items()), '']
    for a in obj.get('adjustments') or []:
        lines.append(f'▸ {a.get("what")}')
        lines.append(f'　現在：{a.get("from")}')
        lines.append(f'　建議：{a.get("to")}')
        lines.append(f'　理由：{a.get("why")}')
        lines.append('')
    if obj.get('to_consultant'):
        lines.append(f'💬 {obj["to_consultant"]}')
    lines.append('')
    lines.append('⚠️ 這只是建議，系統不會自己改。要套用請直接跟我說。')
    text = '\n'.join(lines)

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1("INSERT INTO sourcing_adjustments (id, created_at, job_slug, based_on, findings, proposal, status) "
       f"VALUES (lower(hex(randomblob(8))), {q(now)}, {q(job_slug)}, {n}, "
       f"{q(json.dumps(stats, ensure_ascii=False))}, {q(json.dumps(obj, ensure_ascii=False))}, 'pending')")
    try:
        e = dict(l.strip().split('=', 1) for l in
                 open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        tok = e['TG_BOT_TOKEN'].strip('"\''); chat = e['TG_CHAT_ID'].strip('"\'')
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text,
                                       'message_thread_id': TG_THREAD_SOURCED}).encode()
        urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/sendMessage', data, timeout=20)
        log(f'{job_slug}：建議已送到 TG')
    except Exception as ex:
        log(f'{job_slug}：TG 發送失敗（建議已存進資料庫）：{ex}')
    print(text)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
    rows = d1("SELECT job_slug, reject_reason FROM sourced_candidates "
              "WHERE status='rejected' AND reject_reason IS NOT NULL AND job_slug IS NOT NULL")
    by = {}
    for r in rows:
        by.setdefault(r['job_slug'], []).append(r)
    if only:
        by = {k: v for k, v in by.items() if k == only}
    if not by:
        log('目前沒有帶原因的退件紀錄，沒有東西可以分析')
        return
    for slug, rs in by.items():
        if len(rs) < MIN_SAMPLES and '--force' not in sys.argv:
            log(f'{slug}：只有 {len(rs)} 筆，不到 {MIN_SAMPLES} 筆先不分析'
                f'（樣本太少的結論會害人改錯方向，要硬跑加 --force）')
            continue
        log(f'{slug}：分析 {len(rs)} 筆退件…')
        try:
            res = analyse(slug, rs)
            if res:
                notify(slug, *res)
        except Exception as e:
            log(f'{slug}：{e}')


if __name__ == '__main__':
    import urllib.parse
    main()
