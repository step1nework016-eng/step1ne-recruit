#!/usr/bin/env python3
"""ai_jobs 佇列的本機處理器：把 Worker 排進來的 AI 工作用 claude CLI 跑完。

2026-09-08 建立。在此之前顧問後台有 7 處直接呼叫
`env.AI.run('@cf/meta/llama-3.3-70b-instruct-fp8-fast')`——Cloudflare 內建的
免費 Llama。那是 2026-09-01 為了「顧問送出電洽後要秒看到東西」加的權宜之計
（commit 19e6097），三天內被擴散到客戶版履歷、電洽客戶安全版摘要、
職缺草稿等 7 個地方，包含會直接寄給用人企業的文件。

實際後果（Jacky 2026-09-08 指出）：電洽逐字稿裡的語音辨識錯字
（Ravit→Revit、Bricad→BricsCAD）Llama 完全沒修，照抄進客戶版報告；
而且逐字稿被 slice 到 3000–4000 字，後半段根本沒讀到，「抓不到重點」是必然。

⚠️ Worker 跑不了 claude CLI，所以改成：Worker 只把工作排進 ai_jobs，
   這支常駐排程撈出來用 claude CLI 跑完寫回。代價是從秒回變成 1–2 分鐘。

用法：
    python3 ai_worker.py            # 常駐，每 20 秒撈一次
    python3 ai_worker.py --once     # 跑一輪就結束（測試用）
"""
import argparse
import json
import os
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

MODEL = 'claude-sonnet-5'
TIMEOUT = 300
POLL_SEC = 20
MAX_ATTEMPTS = 3
NO_TOOLS = ['--disallowed-tools', 'Bash,Edit,Write,Read,WebFetch,WebSearch,Task']


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def run_claude(prompt, want_json=False):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)      # 巢狀 session 裡 claude CLI 會拒跑
    r = subprocess.run(
        ['claude', '-p', prompt, '--model', MODEL, *NO_TOOLS, '--output-format', 'text'],
        capture_output=True, text=True, env=env, timeout=TIMEOUT, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    out = r.stdout.strip()
    if not want_json:
        return out
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'回覆裡沒有 JSON：{out[:200]}')
    json.loads(out[i:j + 1])          # 先驗證，壞的就不要寫出去
    return out[i:j + 1]


# ── 各種工作的提示詞 ──
# ⚠️ 共同原則：**逐字稿不截斷**。原本 Llama 版本 slice 到 3000–4000 字，
#    一場 30 分鐘的電洽輕鬆破萬字，重點常常在沒讀到的後半段。

TERM_FIX = """
⚠️ 這份逐字稿是語音轉文字，專有名詞常被聽錯。看到明顯是辨識錯誤的技術名詞，
請直接修正成正確寫法再使用（例如 Ravit→Revit、Bricad→BricsCAD、
凱德→CAD、瑞比特→Revit）。修正過的地方不需要特別標註，
但**不確定的就保留原文**，不要猜。
"""


def prompt_call_summary_client(p):
    return f"""你是獵頭顧問的助理。把下面這段顧問電洽逐字稿，整理成一段**可以直接給用人企業看**的摘要。

{TERM_FIX}

規則：
- 只寫逐字稿裡真的講到的，沒講到就不要寫，**絕對不要補完或推測**
- **絕對不可以出現候選人現在領多少錢**：不論寫成「現職薪資」「目前薪資」「現領」
  「年薪」「月薪」還是換算過的數字都不行。只能寫他「期望」多少。
  （2026-09-08 實測踩到：模型寫了「現職薪資為 10 萬(年薪制,換算月薪)」）
- 也不可以出現：其他在談的機會／測驗分數／我們對人選的懷疑或評價
- 標點用全形（，。、），不要用半形逗號
- 用第三人稱敘述（「候選人表示…」），不要用「我」
- 一段 150-250 字，不要條列，不要標題
- 直接輸出那段文字，不要任何開場白

電洽逐字稿：
{p.get('call_notes') or ''}
"""


def prompt_call_notes_summary(p):
    return f"""你是獵頭顧問的助理。把下面這段電訪筆記整理成六個標題各一段（每段 2-3 行以內）：

重點狀況
求職需求
期望薪資
離職原因
優勢與劣勢
顧問可再確認／可主動告知客戶的部分

{TERM_FIX}

規則：不要新增筆記裡沒提到的資訊，沒提到的欄位就寫「未提及」。
用繁體中文，直接輸出，不要開場白。

電訪筆記：
{p.get('text') or ''}
"""


def prompt_call_prep(p):
    job = p.get('job') or {}
    return f"""你是獵頭顧問的助理。顧問等一下要打電話給這位人選，請幫他準備。

{TERM_FIX}

輸出 JSON（只輸出 JSON，不要任何說明文字）：
{{"summary": ["人選狀況快速摘要，3-5 點"],
  "questions": ["建議電洽問題，5-8 題，要針對這個職缺的條件缺口"],
  "talkingPoints": ["工作介紹重點，3-5 點"],
  "anticipatedQna": [{{"q": "人選可能問的問題", "a": "建議回覆"}}]}}

規則：
- 問題要具體到可以直接照著念，不要「了解一下他的經驗」這種空話
- 只根據下面的資料，**不要編造履歷上沒有的經歷**
- 資料不足以判斷的地方，就在 summary 裡寫「履歷未提及」

職缺：{job.get('title') or ''}
必要條件：{job.get('required_conditions') or job.get('must_skills') or ''}
主要工作：{job.get('main_duties') or ''}

人選姓名：{p.get('name') or ''}
履歷全文：
{p.get('resume_text') or ''}

{('電洽逐字稿：' + chr(10) + p.get('transcript')) if p.get('transcript') else ''}
"""



def prompt_client_report_synthesize(p):
    job = p.get('job') or {}
    checklist = p.get('hard_filters_template') or []
    checklist_lines = '\n'.join(
        f'{i+1}. {c.get("label")}' for i, c in enumerate(checklist)
    ) or '（這個職缺目前沒有設定到職可行性清單，就不用產出 hard_filters）'
    return f"""你是獵頭顧問的助理，要把一位候選人的履歷、顧問電洽逐字稿，整理成一份
給用人企業客戶看的人選推薦報告內容。這位人選**還沒有經過阿財AI結構化面談**，
只有履歷跟顧問電洽逐字稿可以參考。

{TERM_FIX}

🚨 最重要的規則——這是你唯一、也是最容易犯的錯：
- **逐字稿裡沒有提到、答不出來的，一律誠實寫「還沒問到」或「未提及」，絕對不要
  猜、不要用「表示了解，沒有特別疑慮」這種聽起來合理但其實是編出來的話帶過。**
  2026-09-09 真實事故：上一版模型（Llama）在候選人明明主動問了 4 個問題的情況下，
  寫出「面談過程中沒有主動提問」這種假話，已經差點送到客戶手上。你不是在猜
  一個合理答案，是在做一份會影響真人前途、影響客戶決策的正式文件，寧可留白
  也不要編。
- 逐字稿是語音轉文字，沒有標記說話者，你要自己判斷哪句是候選人說的、哪句是
  顧問說的——顧問通常是在問問題、解釋條件；候選人通常是在回答，或用「我還有
  問題」「所以是不是」這類語氣主動確認。抓 candidate_questions 只抓你有把握
  是候選人自己主動問的，不確定就不要放進去，寧可少放。
- 只寫履歷／電洽逐字稿裡有根據的內容，不要編造。
- 不要出現候選人目前/接案收入、其他機會/offer細節、人格測驗分數。
- 不要出現任何社群連結、作品集連結——除非履歷或逐字稿裡真的有提到網址，
  不要自己生一個看起來像的連結。

到職可行性清單（這個職缺原本就要問的項目，逐項核對逐字稿裡有沒有問到、
答案是什麼，答對/合理給 pass，有疑慮給 partial，沒問到給 unknown）：
{checklist_lines}

職缺條件：
{('必要條件：' + job.get('required_conditions')) if job.get('required_conditions') else ''}
{('主要職責：' + job.get('main_duties')) if job.get('main_duties') else ''}
{('用人單位篩選重點：' + job.get('client_screen_conditions')) if job.get('client_screen_conditions') else ''}

人選姓名：{p.get('name') or ''}
人選履歷：
{(p.get('resume_text') or '')[:8000]}

顧問電洽逐字稿：
{p.get('call_notes') or '（沒有電洽紀錄，只能依履歷判斷）'}

用這個 JSON 格式直接輸出，不要加任何說明文字、不要用 markdown code block 包起來：
{{"one_liner":"一句話定位30字內，不要寫年齡／性別",
"basics":{{"residence":null,"age":null,"gender":null,"education":null,"languages":null,"certificates":null,"military":null,"source":"履歷／應徵表單，非面談詢問"}},
"for_client":{{"reasons":["3點推薦理由，要跟職缺條件掛勾"],"job_fit_pros":["2-3點，指超出到職可行性清單以外、讓這個人選比及格線更出色的地方——已經寫進 hard_filters 的項目（機車駕照、能接受到班等）不要在這裡重複講一次，那些是門檻不是優點"],"job_fit_cons":["1-2點"],"trait_one_liner":"依電洽語氣跟應答方式寫一句對這個人特質的觀察，沒有足夠根據就留空字串"}},
"work_history":[{{"employer":"","role":"","duration":"","source":"履歷","nature":"雇主","note":"電洽有補充相關內容才填，沒有就空字串"}}],
"hard_filters":[{{"label":"清單上的項目名稱，逐項照上面清單的順序跟數量","status":"pass|partial|unknown","detail":"依據逐字稿或履歷的具體理由，unknown就寫這場還沒問到"}}],
"expertise_findings":[{{"topic":"","asked":"逐字稿裡的問題，沒有就留空","answered":"候選人怎麼回答的重點","depth":"具體|籠統|未談到"}}],
"candidate_questions":[{{"question":"候選人自己主動問的問題，逐字或接近逐字，沒把握是候選人問的就不要放"}}],
"call_summary_client_md":"一段 150-250 字、可以直接給用人企業看的電洽摘要，第三人稱敘述（候選人表示…），不要出現候選人現在領多少錢（只能寫期望），不要出現其他機會/測驗分數，沒有電洽紀錄就給空字串"}}"""


HANDLERS = {
    'call_summary_client': (prompt_call_summary_client, False),
    'call_notes_summary': (prompt_call_notes_summary, False),
    'call_prep': (prompt_call_prep, True),
    'client_report_synthesize': (prompt_client_report_synthesize, True),
}


def process(job):
    kind = job['kind']
    if kind not in HANDLERS:
        raise RuntimeError(f'未知的工作類型：{kind}')
    builder, want_json = HANDLERS[kind]
    payload = json.loads(job.get('payload_json') or '{}')
    return run_claude(builder(payload), want_json=want_json)


def tick():
    rows = d1_http.query(
        "SELECT * FROM ai_jobs WHERE status='pending' AND attempts < %d "
        "ORDER BY created_at LIMIT 3" % MAX_ATTEMPTS)['results']
    if not rows:
        return 0
    for job in rows:
        jid = job['id']
        d1_http.query(
            f"UPDATE ai_jobs SET status='running', started_at=datetime('now','+8 hours'), "
            f"attempts=attempts+1 WHERE id={q(jid)}")
        log(f'處理 {job["kind"]}（{jid[:8]}）')
        try:
            out = process(job)
            d1_http.query(
                f"UPDATE ai_jobs SET status='done', result_text={q(out)}, error=NULL, "
                f"done_at=datetime('now','+8 hours') WHERE id={q(jid)}")
            log(f'  ✅ 完成，{len(out)} 字')
        except Exception as e:
            msg = str(e)[:400]
            # 還有重試機會就退回 pending，用完才標 failed——暫時性失敗不該直接放棄
            attempts = (job.get('attempts') or 0) + 1
            final = attempts >= MAX_ATTEMPTS
            d1_http.query(
                f"UPDATE ai_jobs SET status={q('failed' if final else 'pending')}, "
                f"error={q(msg)} WHERE id={q(jid)}")
            log(f'  ❌ 失敗（第 {attempts} 次）：{msg[:120]}')
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()
    if a.once:
        n = tick()
        log(f'跑完一輪，處理 {n} 件')
        return
    log(f'AI 工作佇列處理器啟動（每 {POLL_SEC} 秒撈一次，模型 {MODEL}）')
    while True:
        try:
            tick()
        except Exception as e:
            log(f'⚠️ 這一輪出錯（不影響下一輪）：{str(e)[:200]}')
        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
