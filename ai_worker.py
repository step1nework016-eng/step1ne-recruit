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
import socket
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

MODEL = 'claude-sonnet-5'
# 2026-09-10 加：客戶推薦履歷的提示詞今天加了很多新規則＋5個新schema欄位，
# 變長很多，遇到電洽逐字稿本來就長的人選（劉尚義那份），claude CLI跑到
# 4分41秒還沒完，撞300秒逾時被砍掉重來——不是卡死，是原本的逾時值對現在
# 這份提示詞的份量來說太緊。拉高留餘裕，不要每次都靠重試硬撐。
TIMEOUT = 480
POLL_SEC = 20
MAX_ATTEMPTS = 3
NO_TOOLS = ['--disallowed-tools', 'Bash,Edit,Write,Read,WebFetch,WebSearch,Task']
# 2026-09-10 加：多裝置分擔工作之後，Jacky問「怎麼知道這筆是哪台裝置處理的」——
# 原本完全沒記錄。用主機名稱當識別（可用 STEP1NE_WORKER_NAME 環境變數覆蓋，
# 給每台裝置取好記的名字，不設就用系統主機名稱，不用額外設定也能區分）。
WORKER_ID = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()


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
    # ⚠️ 2026-09-10 防呆：transcript 理論上該是 Worker 端已經組好的一段文字，
    # 但撞過一次傳成物件陣列（list）害這支直接噴 TypeError、call_prep_md
    # 永遠是 null、顧問按「產生電洽前準備」完全沒反應。這裡多一層防呆，
    # 不管上游傳什麼形狀都轉成字串，不會再整支掛掉。
    transcript = p.get('transcript')
    if isinstance(transcript, list):
        transcript = '\n'.join(
            f"{(m.get('role') or '')}：{m.get('content') or ''}" if isinstance(m, dict) else str(m)
            for m in transcript
        )
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

{('電洽逐字稿：' + chr(10) + transcript) if transcript else ''}
"""



def prompt_sourced_client_report_synthesize(p):
    """主動開發（sourced_candidates）人選的客戶版履歷整理——跟
    prompt_client_report_synthesize 是兩條不同路徑：這支沒有 hard_filters
    清單、沒有職缺結構化資料，只有履歷全文＋顧問電洽逐字稿／備註（bio/note）。
    2026-09-09 遷移自 Worker 端原本同步呼叫 Llama 的 synthesizeClientReport()，
    輸出格式原封不動照搬，不要改欄位名稱（Worker 端組 HTML 的程式碼直接讀這些欄位）。
    """
    source_parts = []
    if p.get('resume_text'):
        source_parts.append(f"【履歷全文】\n{p['resume_text'][:6000]}")
    if p.get('call_notes'):
        source_parts.append(f"【電洽逐字稿／筆記】\n{p['call_notes'][:6000]}")
    source = '\n\n'.join(source_parts)
    return f"""你是獵頭顧問的助理，要把下面這位人選的履歷跟電洽逐字稿整理成一份「給用人企業客戶看」的正式人選推薦報告內容。

{TERM_FIX}

規則：
- 只寫查得到根據的內容，不要編造履歷或逐字稿裡沒提到的事實。
- 語氣正面、客觀陳述事實，不要出現「不推薦」「顧問懷疑」這類內部判斷用語。
- 不要出現候選人目前/接案收入、其他機會/offer細節、人格測驗分數這類不該給客戶看的內容。
- 「核心條件對應」要像績效面談摘要一樣，依電訪跟履歷實際談到的重點分成 4~7 點，每點一個簡短小標＋一段 100~200 字的敘述（可以包含：學習意願與職務理解、轉職動機、穩定度與抗壓力、學經歷背景、薪資接受度、到職彈性、工作模式接受度等面向，只寫有談到的，沒談到的不要硬湊）。
- 「補充說明」是履歷本身沒寫、但電訪過程中觀察到的正面資訊（例如跨文化溝通能力、職涯決策成熟度等），列 2~4 點，每點一句話。
- 「我方建議」是顧問對客戶的整體推薦結論，2~3 段，總結人選適合度跟後續建議（例如儘速安排面談）。
- 「現況」「相關經驗」「交通工具」各是履歷基本資料表格要用的一行文字（現況＝目前工作狀態一句話；相關經驗＝跟這個職缺相關的經驗程度一句話；交通工具＝有寫才填，沒有就空字串）。
- 資料不足（沒有履歷或沒有電洽紀錄）就誠實留空，不要硬編。

用下面這個 JSON 格式直接輸出，不要加任何說明文字、不要用 markdown code block 包起來：
{{"current_state":"...","related_experience":"...","transportation":"...","core_fit":[{{"title":"...","body":"..."}}],"supplementary":["...","..."],"recommendation":"..."}}

人選姓名：{p.get('name') or ''}

{source or '（沒有履歷全文也沒有電洽紀錄，只能盡量依人選姓名判斷，多數欄位應留空）'}"""


def prompt_client_report_synthesize(p):
    job = p.get('job') or {}
    checklist = p.get('hard_filters_template') or []
    checklist_lines = '\n'.join(
        f'{i+1}. {c.get("label")}' for i, c in enumerate(checklist)
    ) or '（這個職缺目前沒有設定到職可行性清單，就不用產出 hard_filters）'

    # 2026-09-09 加：must_check_items 是另一份清單（用人單位指定、客戶端會看到
    # 狀態），跟 hard_filters 分開評估、分開輸出——不要混在一起，欄位名稱要對得上
    # client_report_tick.py 的覆蓋邏輯（meta['must_check_items'] = data['must_check_items']）。
    mustcheck = p.get('must_check_items_template') or []
    mustcheck_lines = '\n'.join(
        f'{i+1}. {c.get("label")}' for i, c in enumerate(mustcheck)
    ) or '（這個職缺沒有設定必要評估項目，就不用產出 must_check_items）'

    # 2026-09-09 改：Jacky 明確要求統一管線——阿財面談逐字稿／顧問電洽逐字稿
    # 擇一或都有，加上履歷、可選的風格測驗，都要能兜出一份完整報告，不是只有
    # 「真的做過阿財結構化面談」才算數。這裡把兩種對話來源都攤開給AI看，
    # AI 自己判斷哪句是誰說的、綜合兩邊資訊，不再假設只有電洽這一種來源。
    convo_parts = []
    if p.get('interview_transcript'):
        convo_parts.append(f"【阿財AI面談逐字稿】\n{p['interview_transcript'][:8000]}")
    if p.get('call_notes'):
        convo_parts.append(f"【顧問電洽逐字稿／筆記】\n{p['call_notes'][:6000]}")
    convo_text = '\n\n'.join(convo_parts) or '（沒有面談逐字稿也沒有電洽紀錄，只能依履歷判斷，多數欄位應誠實留白）'

    pt = p.get('personality_test') or {}
    pt_lines = []
    if pt.get('big5'):
        b5 = pt['big5']
        pt_lines.append(f"Big Five：盡責性{b5.get('c')}、情緒起伏{b5.get('n')}、開放性{b5.get('o')}、外向性{b5.get('e')}、親和性{b5.get('a')}（滿分5）")
    if pt.get('grit') is not None:
        pt_lines.append(f"恆毅力 Grit：{pt.get('grit')}（滿分5）")
    if pt.get('disc'):
        d = pt['disc']
        pt_lines.append(f"DISC：D{d.get('d')} I{d.get('i')} S{d.get('s')} C{d.get('c')}（滿分20）")
    pt_block = ('\n\n【風格測驗結果——內部參考，只能拿來校準 trait_one_liner 的用詞判斷，'
                '絕對不要把任何分數、測驗名稱、「人格測驗」「Big Five」「DISC」「Grit」這些字眼寫進任何輸出欄位】\n'
                + '\n'.join(pt_lines)) if pt_lines else ''

    # 2026-09-10 加：人工確認關卡的修改回合——Jacky在TG回覆意見時帶著。
    # previous_output 是上一版完整JSON，讓模型「調整」而不是「重新猜一次」，
    # 沒被Jacky提到要改的欄位應該盡量保留，不要整份跟著重寫。
    edit_instruction = p.get('edit_instruction')
    previous_output = p.get('previous_output')
    edit_block = ''
    if edit_instruction:
        edit_block = f"""
⚠️ 這是修改回合，不是第一次產出。上一版輸出內容如下：
{json.dumps(previous_output, ensure_ascii=False) if previous_output else '（沒有上一版內容）'}

Jacky 針對上一版提出的修改意見：
{edit_instruction}

請針對這個意見調整輸出，其餘沒提到要改的部分盡量沿用上一版（除非上一版本身就違反
最上面的規則）。一樣輸出完整的 JSON（不是只輸出差異的部分），格式不變。
"""

    return f"""你是獵頭顧問的助理，要把一位候選人的履歷、對話紀錄（可能是阿財AI面談逐字稿、
顧問電洽逐字稿，或兩者都有），整理成一份給用人企業客戶看的人選推薦報告內容。

{TERM_FIX}

🚨 最重要的規則——這是你唯一、也是最容易犯的錯：
- **對話紀錄裡沒有提到、答不出來的，一律誠實寫「還沒問到」或「未提及」，絕對不要
  猜、不要用「表示了解，沒有特別疑慮」這種聽起來合理但其實是編出來的話帶過。**
  2026-09-09 真實事故：上一版模型（Llama）在候選人明明主動問了 4 個問題的情況下，
  寫出「面談過程中沒有主動提問」這種假話，已經差點送到客戶手上。你不是在猜
  一個合理答案，是在做一份會影響真人前途、影響客戶決策的正式文件，寧可留白
  也不要編。
- 電洽逐字稿是語音轉文字，沒有標記說話者，你要自己判斷哪句是候選人說的、哪句是
  顧問說的——顧問通常是在問問題、解釋條件；候選人通常是在回答，或用「我還有
  問題」「所以是不是」這類語氣主動確認。阿財面談逐字稿有標記角色，直接看。
  抓 candidate_questions 只抓你有把握是候選人自己主動問的，不確定就不要放進去，
  寧可少放。
- 只寫履歷／對話紀錄裡有根據的內容，不要編造。
- 不要出現候選人目前/接案收入、其他機會/offer細節、人格測驗分數或測驗名稱。
- 不要出現任何社群連結、作品集連結——除非履歷或對話紀錄裡真的有提到網址，
  不要自己生一個看起來像的連結。
- 2026-09-10 加：**任何輸出欄位都不准出現「阿財」這個名字**——那是我們內部
  對AI面談助理的暱稱，客戶看到會不知道是什麼東西。統一講「AI初篩」或
  「本次面談」，不要寫「阿財問了」「阿財追問」這種寫法。
- 2026-09-10 加：**不要用「仍待進一步釐清」「有待確認」「需進一步確認」這種
  結尾**——這種寫法聽起來像我們這份報告本身沒做完功課。有問到就直接陳述
  問到的內容跟候選人的回答；沒問到就照規則寫「還沒問到」，不要兩者都想講
  又寫成模稜兩可的句子。
- 2026-09-10 加：**同一個疑慮不要在 hard_filters/must_check_items 的 detail
  跟 job_fit_cons 裡用近乎一樣的長句子重複寫兩次**——job_fit_cons 只需要
  用一句話點出重點（例如「近三段工作任期偏短，候選人已說明原因，建議企業
  自行評估」），細節留給 hard_filters 那邊的具體陳述，不要兩邊都寫一整段。
- 2026-09-10 加（Jacky提供的客戶推薦履歷提示詞規格，補進來的規則）：
  1. **不要自己推算總年資／平均年資**，也不要沿用履歷自填欄位裡的「總年資X年」
     （那種欄位常常把工讀／實習也算進去，不可靠）——只描述各段工作各自的
     起訖時間，不要加總下結論。
  2. **人選目前/期望待遇**：不相關產業的前職／現職薪資一律不寫；只有
     「期望薪資＝前職薪資」且屬於相關產業時，才可以寫期望薪資這個數字。
     不確定產業是否相關就不要寫。
  3. **對前雇主的負面細節一律改寫成中性說法**——例如原話是「派系鬥爭」
     「被針對」，要改寫成「團隊相處氛圍因素」這種不會引戰的描述，不要
     照抄候選人原話裡的負面用詞。
  4. **不要用推銷式用語**——「極佳」「非常適合」「強力推薦」「不可多得」
     這類形容詞，用具體事實陳述代替。
  5. **候選人自傳裡的自我形容詞（例如「高抗壓性」「積極主動」）不能直接
     當成事實寫進推薦理由**——除非有具體事例佐證，否則不要寫成推薦理由；
     沒有事例就不寫這一點，不要用候選人自己的形容詞頂替事實。
  6. **不要暴露內部資訊**：內部評級（A/B/C/D）、內部評估分析、AI初篩本身
     的品質問題（例如問得不夠深）、顧問電洽當下的口誤或失誤、Step1ne的
     內部招募策略，這些都不能出現在任何輸出欄位。

到職可行性清單（這個職缺原本就要問的項目，逐項核對對話紀錄裡有沒有問到、
答案是什麼，答對/合理給 pass，有疑慮給 partial，沒問到給 unknown）：
{checklist_lines}

必要評估項目（用人單位指定要確認、客戶會直接看到狀態的項目，一樣逐項核對
對話紀錄裡有沒有問到，跟上面的到職可行性清單分開評估、分開輸出）：
{mustcheck_lines}

職缺條件：
{('必要條件：' + job.get('required_conditions')) if job.get('required_conditions') else ''}
{('主要職責：' + job.get('main_duties')) if job.get('main_duties') else ''}
{('用人單位篩選重點：' + job.get('client_screen_conditions')) if job.get('client_screen_conditions') else ''}

人選姓名：{p.get('name') or ''}
人選履歷：
{(p.get('resume_text') or '')[:8000]}

{convo_text}{pt_block}
{edit_block}
用這個 JSON 格式直接輸出，不要加任何說明文字、不要用 markdown code block 包起來：
{{"one_liner":"一句話定位30字內，不要寫年齡／性別",
"overview":"開頭概述3-4句：目前狀態（在職/待業）、主要經歷方向、有無相關經驗、居住地與通勤方式、可到職日、對這個職缺的意願，沒有把握的項目就不提那一句，不要用「未提供」湊句子",
"basics":{{"residence":null,"age":null,"gender":null,"education":null,"languages":null,"certificates":null,"license":null,"military":null,"source":"履歷／應徵表單，非面談詢問"}},
"for_client":{{"reasons":["3點推薦理由，要跟職缺條件掛勾"],"job_fit_pros":["2-3點，指超出到職可行性清單以外、讓這個人選比及格線更出色的地方——已經寫進 hard_filters 的項目（機車駕照、能接受到班等）不要在這裡重複講一次，那些是門檻不是優點"],"job_fit_cons":["1-2點"],"trait_one_liner":"依電洽語氣跟應答方式寫一句對這個人特質的觀察，沒有足夠根據就留空字串"}},
"work_history":[{{"employer":"","role":"","duration":"","source":"履歷","nature":"雇主|工讀（求學期間工讀／短期打工，跟職缺專業無關的舊經歷才標這個，正式全職工作一律標「雇主」）","note":"電洽有補充相關內容才填，沒有就空字串","detail_bullets":["這段工作的具體內容，逐點列，只有履歷/逐字稿有寫才列，1-3點，沒有具體內容就空陣列——nature是工讀的話這格留空，不用列細節"],"leave_reason":"離職原因，中性描述，沒問到就空字串"}}],
"hard_filters":[{{"label":"清單上的項目名稱，逐項照上面清單的順序跟數量","status":"pass|partial|unknown","detail":"依據逐字稿或履歷的具體理由，unknown就寫這場還沒問到"}}],
"must_check_items":[{{"label":"必要評估項目清單上的項目名稱，逐項照順序跟數量，沒有清單就給空陣列","status":"pass|partial|unknown","detail":"依據逐字稿或履歷的具體理由，unknown就寫這場還沒問到"}}],
"expertise_findings":[{{"topic":"","asked":"逐字稿裡的問題，沒有就留空","answered":"候選人怎麼回答的重點","depth":"具體|籠統|未談到"}}],
"candidate_questions":[{{"question":"候選人自己主動問的問題，逐字或接近逐字，沒把握是候選人問的就不要放"}}],
"motivation_notes":["動機與意願，條列——轉職原因、對這個職務的理解、想待多久，只寫對話紀錄或履歷自傳裡有根據的，沒有就空陣列"],
"condition_acceptance":[{{"topic":"通勤／加班／調派／班別等，只寫有實際問到的項目","detail":"候選人的回答，具體陳述"}}],
"consultant_observations":{{"approach":"做事方法的觀察，需要有逐字稿具體事例佐證，沒有就空字串","communication":"溝通方式的觀察，同上","preparation":"準備程度的觀察，同上"}},
"things_to_flag":["需要客戶評估、用溫和口吻寫的提醒，例如年資短/待遇高於核薪/異動頻繁——每一點都要講清楚事實，不要下判斷說「不建議」，只講「請企業自行評估」，沒有需要提醒的就空陣列"],
"hard_conditions":[{{"item":"職缺條件裡的一項具體要求（例如學歷門檻、駕照、工作地點、班別、到職時程），從上面『職缺條件』欄位逐項拆出來，沒有職缺條件資料就給空陣列","verdict":"符合|不符|待確認","detail":"依履歷或對話紀錄具體說明候選人這一項的實際狀況，待確認就寫還沒問到"}}],
"call_summary_client_md":"一段 150-250 字、可以直接給用人企業看的電洽摘要，第三人稱敘述（候選人表示…），不要出現候選人現在領多少錢（只能寫期望），不要出現其他機會/測驗分數，沒有電洽紀錄就給空字串"}}

⚠️ overview／motivation_notes／condition_acceptance／consultant_observations／things_to_flag 這五個欄位
都是新加的——一樣適用最上面的規則：沒有根據就誠實留空（字串留空字串、陣列留空陣列），
不要為了讓報告看起來完整就硬湊內容。"""


HANDLERS = {
    'call_summary_client': (prompt_call_summary_client, False),
    'call_notes_summary': (prompt_call_notes_summary, False),
    'call_prep': (prompt_call_prep, True),
    'client_report_synthesize': (prompt_client_report_synthesize, True),
    'sourced_client_report_synthesize': (prompt_sourced_client_report_synthesize, True),
}


def process(job):
    kind = job['kind']
    if kind not in HANDLERS:
        raise RuntimeError(f'未知的工作類型：{kind}')
    builder, want_json = HANDLERS[kind]
    payload = json.loads(job.get('payload_json') or '{}')
    return run_claude(builder(payload), want_json=want_json)


def _build_call_prep_md(prep):
    return ('人選狀況快速摘要\n' + '\n'.join(f'・{s}' for s in prep.get('summary') or [])
            + '\n\n建議電洽問題\n' + '\n'.join(f'{i+1}. {q_}' for i, q_ in enumerate(prep.get('questions') or []))
            + '\n\n工作介紹重點\n' + '\n'.join(f'・{s}' for s in prep.get('talkingPoints') or [])
            + '\n\n人選可能問的問題與建議回覆\n'
            + '\n'.join(f'{i+1}. Q：{x.get("q")}\n   A：{x.get("a")}' for i, x in enumerate(prep.get('anticipatedQna') or [])))


def promote_writebacks():
    """撈 call_notes_summary／call_prep 這兩種工作跑完的結果，寫回 applications，
    寫完就刪掉那筆 ai_jobs（用完即丟，不佔位置）。

    2026-09-09 加：Worker 那邊改成排隊後不等結果（doCallNote／手動新增人選／
    call-prep-generate 三個呼叫點都已改成 queueAiJob，不再當場呼叫 Llama），
    這支負責把結果接回去——包含手動新增人選那邊原本「電洽摘要一存好就順手
    寫一筆 candidate_notes『電洽紀錄』時間軸」的行為，用 payload 裡的
    also_note 旗標保留。
    """
    rows = d1_http.query(
        "SELECT * FROM ai_jobs WHERE kind IN ('call_notes_summary','call_prep') "
        "AND status IN ('done','failed') ORDER BY created_at LIMIT 20")['results']
    for job in rows:
        jid = job['id']
        try:
            payload = json.loads(job.get('payload_json') or '{}')
        except Exception:
            payload = {}
        app_id = payload.get('application_id')
        target = payload.get('target')
        if not app_id or not target:
            d1_http.query(f"DELETE FROM ai_jobs WHERE id={q(jid)}")
            continue
        if job['status'] == 'failed':
            log(f'  ⚠️ {job["kind"]}（{jid[:8]}）重試 3 次都失敗，放棄寫回：{str(job.get("error") or "")[:120]}')
            d1_http.query(f"DELETE FROM ai_jobs WHERE id={q(jid)}")
            continue
        try:
            if job['kind'] == 'call_notes_summary':
                summary_md = job['result_text']
                d1_http.query(f"UPDATE applications SET {target}={q(summary_md)} WHERE id={q(app_id)}")
                if payload.get('also_note'):
                    d1_http.query(
                        "INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by) "
                        f"VALUES ({q(str(uuid.uuid4()))}, {q(app_id)}, '電洽紀錄', {q(summary_md)}, "
                        f"datetime('now','+8 hours'), {q(payload.get('note_by'))})")
            elif job['kind'] == 'call_prep':
                prep = json.loads(job['result_text'])
                md = _build_call_prep_md(prep)
                d1_http.query(f"UPDATE applications SET {target}={q(md)} WHERE id={q(app_id)}")
            d1_http.query(f"DELETE FROM ai_jobs WHERE id={q(jid)}")
            log(f'  ↩️ 寫回 {job["kind"]}（{jid[:8]}）→ applications.{target}')
        except Exception as e:
            log(f'  ⚠️ 寫回 {job["kind"]}（{jid[:8]}）失敗，先留著：{str(e)[:150]}')


def tick():
    promote_writebacks()
    rows = d1_http.query(
        "SELECT * FROM ai_jobs WHERE status='pending' AND attempts < %d "
        "ORDER BY created_at LIMIT 3" % MAX_ATTEMPTS)['results']
    if not rows:
        return 0
    for job in rows:
        jid = job['id']
        # 2026-09-10 加：多台機器（不同裝置，同一個claude帳號各跑一份這支）
        # 同時搶同一批 pending 工作時，原本這個 UPDATE 沒有 WHERE status='pending'，
        # 兩台機器都會「成功」把同一筆改成running、各自跑一次claude CLI——
        # 同一份工作被處理兩次，浪費用量還可能兩邊都寫結果互相覆蓋。
        # 改成帶條件的UPDATE，用 meta.changes 判斷「這次是不是真的搶到」，
        # 這是 d1_http.py 檔頭註解裡講的設計，本來就是設計來做這件事的。
        claim = d1_http.query(
            f"UPDATE ai_jobs SET status='running', started_at=datetime('now','+8 hours'), "
            f"attempts=attempts+1, worker_id={q(WORKER_ID)} WHERE id={q(jid)} AND status='pending'")
        if not claim.get('meta', {}).get('changes'):
            log(f'  ⏭️ {job["kind"]}（{jid[:8]}）已被其他裝置搶走，跳過')
            continue
        log(f'處理 {job["kind"]}（{jid[:8]}），裝置：{WORKER_ID}')
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
