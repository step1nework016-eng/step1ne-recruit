#!/usr/bin/env python3
"""職缺配對引擎 —— 候選人描述自己，我們告訴他哪個職缺適合、為什麼、缺什麼。

為什麼是本機常駐而不是寫在 Worker 裡：
    Worker 端沒有 Claude 可用（沒有 API key，Workers AI 的中文寫不到要的水準）。
    Jacky 2026-08-19 決定用本機這台已經在付的 Claude Code 額度跑，
    代價是候選人要等十幾秒——這個取捨的理由是：規則配得對但講不出人話
    （實測理由會寫成「您提到的『望月收』跟這個職缺對得上」），
    而這是候選人對我們的第一印象，講錯話比慢十秒嚴重得多。

    所以流程是兩段：
      Worker 用規則初選 → 候選人立刻看到結果（不會盯著轉圈圈）
      這支再用模型判斷 → 前端輪詢到就自動換成更好的版本

⚠️ 模型只能從**規則初選出來的職缺**裡挑，不准自己想出職缺。
   這是硬規則：配錯職缺會讓候選人白投一次履歷，那個代價由他承擔。

花費照記（call_type='match'），後台看得到；也用來估算「未來改用 API 要多少錢」。
"""
import datetime
import json
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
MODEL = 'claude-sonnet-5'
TIMEOUT = 180
POLL_SEC = 6

_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']


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


PROMPT = '''你是獵頭顧問，一位求職者剛剛描述了他自己與想找的工作。
你要從**下面這幾個職缺裡**挑出真的適合他的，並且用人話告訴他為什麼。

【他說的】
{text}

【可以挑的職缺（只能從這裡面挑，不准自己想出別的職缺）】
{jobs}

【怎麼判斷】
1. 先問自己：**他投這個缺，會不會白投？** 條件差太多就不要推薦，
   推一個他一定不會上的缺，比什麼都不推還糟。
2. 適合就講清楚「哪一段經歷對得上這個職缺的哪個需求」，不要只說「您的背景很符合」。
3. 有落差也要講——但講成「還缺什麼」而不是「你不夠格」。
   例：「這個缺希望有三年以上，您目前兩年，可以在應徵時補充您做過的專案規模」。
4. 如果全部都不太適合，`jobs` 就給空陣列，並在 `note` 老實說「目前沒有很吻合的」。
   **不要為了有東西交而硬推**。

【語氣】
· 像一個看過他履歷的顧問在跟他講話，不是系統回覆
· 不要用「根據您的描述」「系統為您推薦」這種句型
· 繁體中文，每個職缺的理由 2 句以內

⚠️ 不准提到年齡、性別、婚姻、生育、國籍、外貌等條件（就業服務法第 5 條）。
   就算他自己講了，你也不要拿來當推薦或不推薦的理由。

【只輸出這個 JSON】
{{
  "jobs": [
    {{"slug": "職缺代號（照上面給的）",
      "fit": "為什麼適合他，2 句以內，要具體指出對得上的地方",
      "gap": "還缺什麼或要注意什麼，沒有就空字串"}}
  ],
  "note": "給他的一句話。全部不適合時要老實講。"
}}'''


def build_prompt(req, jobs):
    lines = []
    for j in jobs:
        bits = [f"代號：{j['slug']}", f"職稱：{j['title']}"]
        for k, label in (('locations', '地點'), ('employment', '僱傭型態'),
                         ('must_skills', '需要的能力'), ('years_min', '年資門檻'),
                         ('salary_note', '待遇說明')):
            if j.get(k):
                bits.append(f"{label}：{str(j[k])[:300]}")
        if j.get('salary_min') and j.get('salary_max'):
            bits.append(f"薪資：{j['salary_min']}–{j['salary_max']}")
        lines.append('\n'.join(bits))
    return PROMPT.format(text=req['input_text'][:2000], jobs='\n\n──────\n'.join(lines))


def handle(req):
    slugs = json.loads(req.get('shortlist_json') or '[]')
    if not slugs:
        d1(f"UPDATE match_requests SET status='done', "
           f"result_json={q(json.dumps({'jobs': [], 'note': '目前站上的職缺跟您的方向沒有很吻合。留下聯絡方式，有合適的我們直接通知您。'}, ensure_ascii=False))}, "
           f"done_at=datetime('now','+8 hours') WHERE id={q(req['id'])}")
        return
    inq = ','.join(q(s) for s in slugs)
    jobs = d1(f"SELECT slug,title,locations,employment,must_skills,years_min,"
              f"salary_min,salary_max,salary_note FROM jobs WHERE slug IN ({inq})")
    prompt = build_prompt(req, jobs)

    before = D._snapshot_session_files()
    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=D.env_with_cf(), timeout=TIMEOUT)
    # 花費要記，後台才能算「未來改用 API 要多少錢」
    D.log_token_usage(f'match:{req["id"]}', 'match', prompt, before)

    obj = _extract_json(r.stdout)
    if not obj or 'jobs' not in obj:
        log(f"❌ {req['id']}：模型沒吐出可用結果，維持規則版")
        d1(f"UPDATE match_requests SET status='failed', done_at=datetime('now','+8 hours') "
           f"WHERE id={q(req['id'])}")
        return
    # 只留真的存在的職缺——模型偶爾會自己編一個代號出來
    ok_slugs = {j['slug'] for j in jobs}
    obj['jobs'] = [x for x in obj['jobs'] if x.get('slug') in ok_slugs]
    d1(f"UPDATE match_requests SET status='done', result_json={q(json.dumps(obj, ensure_ascii=False))}, "
       f"done_at=datetime('now','+8 hours') WHERE id={q(req['id'])}")
    log(f"✅ {req['id']}：推薦 {len(obj['jobs'])} 個職缺")


def main():
    log(f'職缺配對引擎啟動（每 {POLL_SEC} 秒檢查一次）')
    while True:
        try:
            rows = d1("SELECT id, input_text, shortlist_json FROM match_requests "
                      "WHERE status='pending' ORDER BY created_at LIMIT 3")
            for req in rows:
                # 先搶下來，避免同時跑兩個實例時重複處理
                d1(f"UPDATE match_requests SET status='working' "
                   f"WHERE id={q(req['id'])} AND status='pending'")
                try:
                    handle(req)
                except Exception as e:
                    log(f"❌ {req['id']}：{e}")
                    d1(f"UPDATE match_requests SET status='failed' WHERE id={q(req['id'])}")
        except Exception as e:
            log(f'輪詢失敗（下一輪再試）：{e}')
        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
