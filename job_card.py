#!/usr/bin/env python3
"""職缺卡 —— 讓阿財越評越準。Jacky 交辦，2026-09-24。

問題：企業給的 JD 常常不準，顧問是靠「送人選、聽客戶回饋」才知道客戶真正要什麼——
這些判斷標準一直留在顧問腦袋裡，阿財讀不到，所以每次面談都照同一套通用標準打分。

作法：
    ① 顧問把客戶回饋（文字或截圖）貼進職缺卡，AI 整理成「技能深淺／特質／加分／
       常見落選原因」，顧問確認後存進 job_card_profile。
    ② 顧問把人選推薦給客戶、人選被客戶錄取／婉拒——這些事件本來就會被
       `placements` 表記下來（顧問在後台填、或客戶自己在 portal 操作、或
       feedback_intake.py 記顧問電洽回報，三條路都寫同一張表）。這支只需要
       對這張表「對帳」：找出還沒記過經驗值的事件，照阿財那次面談的等第
       （A-E，從 reports.content_json 讀）給對應的經驗值，寫進 job_card_events。
    ③ 從 job_card_events 現算經驗值總和與等級；從 placements 現算命中率／
       婉拒率／Lv.10 的品質門檻（阿財評A的人選裡，多少比例真的進面試、
       多少比例真的錄取——經驗值堆再多，這兩個沒到，卡在 Lv.9）。
    ④ interview_daemon.py 面談前讀 job_card_profile.masked_summary_text——
       濃縮成幾行的乾淨版，不含客戶名／薪資／內部評語，避免洩密也避免拖慢面談。

⚠️ job_card_events 是只增不改的帳本，真相來源。job_card_profile 是可以
   整張重算重寫的衍生結果，不是真相——壞了、算錯了，重跑 --sync 就對了，
   不用去改 profile 本身。

用法：
    python3 job_card.py --sync                 # 掃全部職缺的 placements，補經驗值事件、重算卡片
    python3 job_card.py --sync <job_slug>       # 只掃一個職缺
    python3 job_card.py --check [job_slug]      # 唯讀，印目前的等級／經驗值／命中率
    python3 job_card.py --import-feedback <job_slug> --text "客戶說..." --actor Ariel
    python3 job_card.py --import-feedback <job_slug> --image /path/to/screenshot.png --actor Ariel
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

CLAUDE_BIN = shutil.which('claude') or 'claude'
MODEL = 'claude-sonnet-5'
TIMEOUT = 180

# ── 等級曲線（Jacky 2026-09-24 定案）──
# 累積經驗值門檻，越後面步幅越大——早期餵幾筆就有感，衝到頂要長期反覆招募
# 才到得了，不是隨便兩三筆資料就滿級。
LEVELS = [
    (100, 1, '剛入門'), (250, 2, '有點概念'), (450, 3, '摸到皮毛'),
    (700, 4, '抓到方向'), (1000, 5, '摸出脈絡'), (1350, 6, '抓到重點'),
    (1750, 7, '得心應手'), (2200, 8, '深諳門道'), (2700, 9, '該職缺老手'),
    (3250, 10, '顧問的分身'),
]
# Lv.10 品質門檻：經驗值到了只是資格到了，還要「真的準」才能封頂——
# 不然「餵得多」跟「阿財真的懂」會被混為一談。樣本數不足（案例太少）時
# 不檢查這道門檻，卡片就停在經驗值對應的等級，不會提早給「已經封頂」的錯誤信號。
LV10_GATE_MIN_SAMPLE = 10
LV10_GATE_ADVANCE_RATE = 0.85
LV10_GATE_HIRE_RATE = 0.70

RECOMMEND_XP = {'A': 20, 'B': 15, 'C': 10, 'D': 5, 'E': 5}
PLACED_XP = {'A': 100, 'B': 60, 'C': 30, 'D': 10, 'E': 10}
FEEDBACK_XP = 30

_ROUTE_CACHE = {}


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def qn(v):
    """數字或 NULL——經驗值/等級一定有值可以直接塞數字，但命中率這類比例
    樣本不足時要能寫 NULL，不能塞 0（0 跟「不知道」是兩回事）。"""
    return 'NULL' if v is None else repr(v)


def d1(sql):
    return d1_http.query(sql).get('results', [])


def sanitize(t):
    """prompt 是用 stdin/命令列傳給 claude CLI，字串裡有 \\x00（截圖 OCR／
    語音轉文字常見雜訊）就會讓 subprocess 直接丟例外，整個匯入失敗——
    跟 interview_daemon.py／ai_worker.py 用同一道防護，見 CLAUDE.md。"""
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)


def run_claude_json(prompt, extra_args=None):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    args = [CLAUDE_BIN, '-p', '--model', MODEL, '--output-format', 'text']
    args += extra_args or ['--disallowed-tools', 'Bash,Edit,Write,WebFetch,WebSearch,Task']
    r = subprocess.run(args, input=sanitize(prompt), capture_output=True, text=True,
                       env=env, timeout=TIMEOUT, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    out = r.stdout.strip()
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'回覆裡沒有 JSON：{out[:200]}')
    return json.loads(out[i:j + 1])


# ── 等級 ──

def level_for_xp(xp):
    """回傳 (等級, 稱號, 距下一級還差多少[None=已達本曲線最高])。"""
    level, name, remain = 0, '新手（還沒有回饋）', LEVELS[0][0] - xp
    for threshold, lv, nm in LEVELS:
        if xp >= threshold:
            level, name, remain = lv, nm, None
        else:
            remain = threshold - xp
            break
    return level, name, remain


# ── 從面談報告讀阿財那次的分流等第（A-E）──
# 2026-09-21 之後的報告 content_json 已經直接存 route.code；在那之前的報告
# 沒有這個欄位，但算分流要用的原始材料（fit_scores.total／verdict／
# hard_conditions）都還在，用同一套規則（interview_daemon.py 約2515-2551行）
# 現場算一次——不是另訂一套規則，是同一份規則的第二個讀取點。
def route_from_content(obj):
    route = obj.get('route')
    if isinstance(route, dict) and route.get('code'):
        return route['code']
    fs = obj.get('fit_scores') or {}
    grade = fs.get('grade')
    # ⚠️ 2026-09-24 總指揮抓到：2026-09-21 前的舊報告，grade 是英文字母
    # （A-D，純看總分），跟現在的分流字母（A-E，人才分流）是完全不同的
    # 兩套東西共用同一批字母，不能拿新的 hard_fail/verdict/total 演算法去
    # 重新詮釋舊資料——陳其寬那筆舊報告 verdict 寫「值得轉給顧問」但總分只有
    # 47，這種新舊欄位互相矛盾的舊資料本來就不夠乾淨，硬套新演算法只會產生
    # 另一種看似合理、其實對不上當時真人／模型定案分類的答案。
    # 判斷「是不是舊報告」用 grade 的形狀（英文字母 vs 現在的中文「優/良/中/
    # 待加強」），比看日期可靠——報告本身沒有存「用哪一版邏輯」這種欄位。
    # 舊等第粗分兩級退回：A/B 當年就是「看好」，直接對應新制的 A/B；
    # C/D 當年是「不看好」，統一退到 D（新制的人才池），不細分 C/D/E。
    if grade in ('A', 'B', 'C', 'D'):
        return grade if grade in ('A', 'B') else 'D'
    total = fs.get('total')
    verdict = obj.get('verdict')
    hard_conditions = obj.get('hard_conditions') or []
    hard_fail = (verdict == '硬條件不符') or any(
        (h or {}).get('verdict') == '不符' for h in hard_conditions)
    if hard_fail:
        return 'C' if (total is not None and total >= 50) else 'E'
    if total is None:
        return 'B'
    if verdict == '資訊不足建議補問':
        return 'B'
    if total >= 65:
        return 'A'
    if total >= 50:
        return 'B'
    return 'D'


def latest_route_for_application(app_id):
    if app_id in _ROUTE_CACHE:
        return _ROUTE_CACHE[app_id]
    rows = d1(f"SELECT content_json FROM reports WHERE application_id={q(app_id)} "
              f"AND content_json IS NOT NULL ORDER BY created_at DESC LIMIT 1")
    grade = None
    if rows and rows[0].get('content_json'):
        try:
            grade = route_from_content(json.loads(rows[0]['content_json']))
        except Exception as e:
            log(f'  ⚠️ application {app_id} 的報告解析失敗，跳過不猜：{str(e)[:100]}')
    _ROUTE_CACHE[app_id] = grade
    return grade


# ⚠️ 2026-09-24 總指揮抓到：原本只認 placement_status='PLACED' 或有
# onboard_date，bim-engineer-tongluo 有人選 stage 已經到 OFFER（客戶已經
# 發 offer，是錄取方向的結果），完全沒被算進命中率——OFFER 之後到真的
# 報到中間可能還要走幾週，但「客戶要不要」這件事在發 offer 那刻就已經
# 有答案了，不用等到職才算數。
_HIRE_DIRECTION_STAGES = ('OFFER', 'OFFER_ACCEPTED', 'CANDIDATE_ACCEPTED', 'ONBOARDING', 'PLACED', 'HIRED')
# 我方結案／失聯——不是客戶做的判斷（不是客戶要或不要），不能拿來算阿財
# 準不準，但要在卡片上讓顧問看得到「還有這些筆不計在內」，不是憑空消失。
_EXCLUDED_STAGES = ('CLOSED_INTERNAL', 'CLOSED_LOST')


def _is_placed(r):
    if (r.get('placement_status') == 'PLACED') or bool(r.get('onboard_date')):
        return True
    return (r.get('stage') or '').upper() in _HIRE_DIRECTION_STAGES


def _is_declined(r):
    return (r.get('stage') or '').upper() == 'REJECTED_BY_CLIENT'


def _is_excluded(r):
    return (r.get('stage') or '').upper() in _EXCLUDED_STAGES


def _is_advanced(r):
    """有沒有真的送進客戶端面試——不是只送出去等回覆。用 interview_at 這種
    具體證據，或 stage 已經走出「剛送出／等客戶回覆」這兩格，兩條路任一成立
    都算，不管是走哪一套寫入路徑（客戶 portal／feedback_intake／顧問手動）進來的。"""
    if r.get('interview_at'):
        return True
    stage = (r.get('stage') or '').upper()
    return stage not in ('', 'SUBMITTED', 'AWAITING_CLIENT_FEEDBACK')


# ── 對帳：從 placements 補經驗值事件 ──

def insert_event(job_slug, event_type, xp, app_id, placement_id, grade, note, dedupe):
    d1_http.query(
        f"INSERT OR IGNORE INTO job_card_events "
        f"(job_slug, event_type, xp_delta, application_id, placement_id, grade, note, "
        f" created_by, dedupe_key) VALUES "
        f"({q(job_slug)}, {q(event_type)}, {xp}, {q(app_id)}, "
        f"{placement_id if placement_id is not None else 'NULL'}, {q(grade)}, {q(note)}, "
        f"'system', {q(dedupe)})")


def sync_events(job_slug=None):
    where = f"WHERE p.job_slug={q(job_slug)}" if job_slug else "WHERE p.job_slug IS NOT NULL AND p.job_slug != ''"
    rows = d1(f"SELECT p.id as placement_id, p.application_id, p.job_slug, p.stage, "
              f"p.placement_status, p.onboard_date FROM placements p {where}")
    touched = set()
    for r in rows:
        slug = r.get('job_slug')
        app_id = r.get('application_id')
        if not slug or not app_id:
            continue
        grade = latest_route_for_application(app_id)
        if not grade:
            continue  # 沒有面談報告可對等第，這筆先跳過，不猜一個分數出來
        touched.add(slug)
        pid = r['placement_id']
        insert_event(slug, 'recommend', RECOMMEND_XP.get(grade, 5), app_id, pid, grade,
                     f'阿財評{grade}，顧問推薦給客戶', f'recommend:{pid}')
        if _is_placed(r):
            # ⚠️ 2026-09-24 修（總指揮抓到）：原本不分等第一律寫「判斷準加碼」——
            # 陳其寬那筆阿財評 D 卻被錄取，note 卻寫著「判斷準」，跟 Jacky 定的
            # 精神（評分高又錄取＝準；評分低卻錄取＝不準）完全講反。經驗值數字
            # 本來就是對的（低分錄取只加一點點），純粹是文字說反話。
            note = (f'阿財評{grade}的人選錄取，判斷準' if grade in ('A', 'B')
                   else f'阿財評{grade}卻錄取，判斷有落差，請補一筆回饋看看阿財看漏了什麼')
            insert_event(slug, 'placed', PLACED_XP.get(grade, 10), app_id, pid, grade,
                         note, f'placed:{pid}')
        elif _is_declined(r):
            insert_event(slug, 'client_declined', 0, app_id, pid, grade,
                         f'阿財評{grade}的人選被客戶婉拒', f'declined:{pid}')
    for slug in touched:
        recompute_profile(slug)
    return touched


def recompute_profile(job_slug):
    ev = d1(f"SELECT event_type, xp_delta FROM job_card_events WHERE job_slug={q(job_slug)}")
    xp_total = sum(e['xp_delta'] for e in ev)
    feedback_count = sum(1 for e in ev if e['event_type'] == 'feedback_import')
    level, _name, _remain = level_for_xp(xp_total)

    placements = d1(f"SELECT stage, placement_status, onboard_date, interview_at, application_id "
                    f"FROM placements WHERE job_slug={q(job_slug)}")
    submitted_n = len(placements)
    # 面談過幾人：有 reports 的 applications，不是 placements——顧問可能面談過
    # 一個人但還沒送給任何客戶，這個數字要能反映「阿財實際談過多少人」。
    interviewed_n = (d1(f"SELECT COUNT(DISTINCT a.id) n FROM applications a "
                        f"JOIN reports r ON r.application_id = a.id "
                        f"WHERE a.job_slug = {q(job_slug)}") or [{'n': 0}])[0]['n']

    a_settled = a_advance = a_hire = 0
    hires = declines = settled = excluded = 0
    graded_settled = correct_calls = 0
    for r in placements:
        if _is_excluded(r):
            excluded += 1
            continue
        placed = _is_placed(r)
        declined = _is_declined(r)
        if placed:
            hires += 1; settled += 1
        elif declined:
            declines += 1; settled += 1
        else:
            continue
        app_id = r.get('application_id')
        grade = latest_route_for_application(app_id) if app_id else None

        # ⚠️ 2026-09-24 修（總指揮抓到）：命中率原本是「錄取數／已有結果數」，
        # 不管等第——一個職缺只要有一筆阿財評D卻被錄取，命中率照樣衝到100%，
        # 因為公式壓根沒看等第對不對。這完全不是 Jacky 定的「命中率」：
        # 命中率要問的是「阿財的等第跟結果對不對得上」，不是「有沒有錄取」。
        # 改法：只算阿財真的評過的（沒等第就是阿財沒判斷過，不能拿來說他準不準，
        # 不進這個分母——跟上面 sync_events()「沒等第就跳過」是同一個原則）。
        # 「猜對」＝評A/B而且真的錄取，或評C/D/E而且真的被婉拒；兩種都算猜對，
        # 評A/B卻婉拒、評C/D/E卻錄取，都算猜錯。
        if grade:
            graded_settled += 1
            is_top = grade in ('A', 'B')
            if (placed and is_top) or (declined and not is_top):
                correct_calls += 1

        if grade != 'A':
            continue
        a_settled += 1
        if placed or _is_advanced(r):
            a_advance += 1
        if placed:
            a_hire += 1

    advance_rate = (a_advance / a_settled) if a_settled else None
    hire_rate = (a_hire / a_settled) if a_settled else None
    accuracy_rate = (correct_calls / graded_settled) if graded_settled else None
    decline_rate = (declines / settled) if settled else None

    capped = 0
    if level >= 10:
        gate_ok = (a_settled >= LV10_GATE_MIN_SAMPLE and advance_rate is not None
                   and advance_rate >= LV10_GATE_ADVANCE_RATE
                   and hire_rate is not None and hire_rate >= LV10_GATE_HIRE_RATE)
        if not gate_ok:
            level, capped = 9, 1

    d1_http.query(
        f"INSERT INTO job_card_profile (job_slug, feedback_count, xp_total, level, "
        f" level_capped_by_gate, advance_rate, hire_rate, accuracy_rate, decline_rate, "
        f" a_grade_settled_n, interviewed_n, submitted_n, settled_n, excluded_n, "
        f" updated_at) VALUES "
        f"({q(job_slug)}, {feedback_count}, {xp_total}, {level}, {capped}, "
        f" {qn(advance_rate)}, {qn(hire_rate)}, {qn(accuracy_rate)}, {qn(decline_rate)}, "
        f" {a_settled}, {interviewed_n}, {submitted_n}, {settled}, {excluded}, "
        f" datetime('now','+8 hours')) "
        f"ON CONFLICT(job_slug) DO UPDATE SET feedback_count=excluded.feedback_count, "
        f" xp_total=excluded.xp_total, level=excluded.level, "
        f" level_capped_by_gate=excluded.level_capped_by_gate, "
        f" advance_rate=excluded.advance_rate, hire_rate=excluded.hire_rate, "
        f" accuracy_rate=excluded.accuracy_rate, decline_rate=excluded.decline_rate, "
        f" a_grade_settled_n=excluded.a_grade_settled_n, "
        f" interviewed_n=excluded.interviewed_n, submitted_n=excluded.submitted_n, "
        f" settled_n=excluded.settled_n, excluded_n=excluded.excluded_n, "
        f" updated_at=excluded.updated_at")

    recompute_acai_view(job_slug)


# ── 「阿財的理解」卡片快照 ──
# 2026-09-24（Jacky 交辦）：顧問要確認「阿財理解得對不對」，卡片內容一定要是
# 阿財面談前實際讀到的東西，不能另外叫 AI 生一份摘要——所以這裡直接呼叫
# interview_daemon.py 撈資料／組結構用的同一支函式（fetch_job_understanding_sources／
# job_understanding），跟面談是同一個資料來源，不會走鐘。
# Worker（Cloudflare）跑不了這支 Python，所以本機算好存成快照，Worker 只負責讀。
_DAEMON = None


def _daemon():
    global _DAEMON
    if _DAEMON is not None:
        return _DAEMON
    import importlib.util
    spec = importlib.util.spec_from_file_location('_interview_daemon_for_job_card',
                                                   os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    _DAEMON = mod
    return mod


def recompute_acai_view(job_slug):
    try:
        D = _daemon()
        jobs = d1(f"SELECT * FROM jobs WHERE slug={q(job_slug)}")
        if not jobs:
            return
        src = D.fetch_job_understanding_sources(job_slug)
        view = D.job_understanding(jobs[0], src.get('blockers'), src.get('expertise'),
                                   src.get('job_card_summary'))
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # 這張卡不需要職缺卡有任何回饋/經驗值才存在——阿財對職缺的理解來自
        # jobs／job_expertise，跟職缺卡的匯入紀錄是兩件獨立的事，用
        # INSERT..ON CONFLICT 保證就算 job_card_profile 這列還沒被
        # recompute_profile() 建過，這張「理解卡」也能先算出來。
        d1_http.query(
            f"INSERT INTO job_card_profile (job_slug, acai_view_json, acai_view_updated_at) "
            f"VALUES ({q(job_slug)}, {q(json.dumps(view, ensure_ascii=False))}, {q(now)}) "
            f"ON CONFLICT(job_slug) DO UPDATE SET acai_view_json=excluded.acai_view_json, "
            f"acai_view_updated_at=excluded.acai_view_updated_at")
    except Exception as e:
        log(f'⚠️ {job_slug}：「阿財的理解」快照重算失敗（不影響經驗值/等級，只是這張卡沒更新）：{str(e)[:200]}')


# ── 匯入客戶回饋（文字或截圖）──
# 2026-09-24：顧問後台按「匯入」→ Worker 把工作丟進 ai_jobs（kind='job_card_feedback'）
# →這台機器的 ai_worker.py 撿起來呼叫這裡。Worker 本身跑不了 claude CLI，
# 所以整條「讀圖/讀文字→AI整理→寫job_card_events/profile」都在這支做完，
# 不是「產一段文字寫回某欄位」那種簡單 writeback（跟 expertise_build 同一類）。

FEEDBACK_PROMPT = """你是獵頭顧問的助理。顧問剛聽完客戶對某個職缺人選的回饋，要把這段話
整理成「這個職缺客戶真正要什麼」的判斷標準，讓下次面談這個職缺的 AI 用得上。

{term_fix}

已經累積的舊版畫像（可能是空的）：
{prior}

這次顧問新貼的回饋原文：
{raw}

請把新回饋「併入」舊版畫像（不是取代、也不是單純疊加成重複的兩條）：同一件事
只留一條、講得更清楚的版本；新增的洞察才新增一條；矛盾的地方保留較新的說法，
但可以在該條目附註「這點客戶前後講法不一致」。

⚠️ 絕對規則：如果客戶回饋裡提到年齡、性別、婚姻／生育狀況、外貌這類條件
（就算客戶原話真的這樣講），**完全不要寫進 full 或 masked 的任何欄位**——
這個職缺卡只能記「職務相關、客觀可驗證」的判斷標準，這類條件兩份都不能出現，
不是「masked 濾掉、full 保留」，是兩邊都不能有。

輸出兩份版本：
- full：顧問在後台看的完整版，可以包含客戶名、薪資、內部評語、人名這類細節
- masked：**阿財面談時會直接讀的版本**，這是最重要的規則——绝对不可以出現
  任何客戶名稱、薪資數字、內部評語、候選人姓名、公司名稱；只能留「技能要
  多深」「什麼特質」「常見加分/落選原因」這類跟「怎麼評估這個職缺的人」
  直接相關的內容。寧可少寫，也不可以讓機密資訊流進 masked。

只輸出這個 JSON，不要有其他文字：
{{
  "full": {{
    "skills": ["技能深淺，可含細節來源"],
    "traits": ["特質"],
    "plus": ["加分項"],
    "fail_reasons": ["常見落選原因"]
  }},
  "masked": {{
    "skills": ["同上，但已濾掉所有機密資訊"],
    "traits": ["..."],
    "plus": ["..."],
    "fail_reasons": ["..."]
  }}
}}
"""

TERM_FIX = ("這段可能是語音轉文字或截圖辨識，專有名詞或人名如果明顯是辨識錯誤，"
            "可以合理修正；不確定的保留原文，不要用猜的內容當成事實。")


def _masked_summary_text(masked, job_title, feedback_count, updated_at):
    lines = [f'【這個職缺客戶重視什麼】（已根據 {feedback_count} 次回饋整理，最後更新 {updated_at}）']
    sections = [('技能深淺', masked.get('skills')), ('特質', masked.get('traits')),
               ('加分', masked.get('plus')), ('常見落選原因', masked.get('fail_reasons'))]
    for label, items in sections:
        for it in (items or []):
            it = str(it).strip()
            if it:
                lines.append(f'· ［{label}］{it}')
    return '\n'.join(lines)


def import_feedback(job_slug, raw_text=None, image_path=None, actor=None, event_key=None):
    """event_key：這筆匯入的去重鍵。ai_worker.py 呼叫時會帶 ai_jobs.id 進來——
    同一個 ai_jobs 工作萬一被重跑（例如逾時後救回），同一個 id 只會記一次
    +30 經驗值，不會因為重試就重複加分。CLI 手動測試沒有 ai_jobs.id 可帶，
    留空時退回目前時間戳記當 key（人工操作不會無限重試，風險可接受）。"""
    if not raw_text and not image_path:
        raise ValueError('要有文字或截圖其中一個')
    existing = d1(f"SELECT full_profile_json FROM job_card_profile WHERE job_slug={q(job_slug)}")
    prior = existing[0]['full_profile_json'] if existing and existing[0].get('full_profile_json') else '（還沒有任何回饋，這是第一筆）'

    if image_path:
        # 截圖走的是「只開放 Read 工具讀這一張圖」的受限模式——不是整台機器開放，
        # --add-dir 只讓它看得到這張圖所在的暫存目錄，讀完這次呼叫就結束，
        # 不會留在背景、也碰不到其他檔案。
        img_dir = os.path.dirname(os.path.abspath(image_path))
        prompt = FEEDBACK_PROMPT.format(
            term_fix=TERM_FIX, prior=prior,
            raw=f'（顧問貼的是一張截圖，路徑：{image_path}，請先讀圖再照上面規則整理）')
        out = run_claude_json(prompt, extra_args=[
            '--allowed-tools', 'Read', '--add-dir', img_dir,
            '--disallowed-tools', 'Bash,Edit,Write,WebFetch,WebSearch,Task',
            '--permission-mode', 'bypassPermissions'])
    else:
        prompt = FEEDBACK_PROMPT.format(term_fix=TERM_FIX, prior=prior, raw=raw_text)
        out = run_claude_json(prompt)

    full = out.get('full') or {}
    masked = out.get('masked') or {}
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    title_row = d1(f"SELECT title FROM jobs WHERE slug={q(job_slug)}")
    job_title = title_row[0]['title'] if title_row else job_slug

    # 這次匯入本身先記一筆事件（+30），順手把 feedback_count 往上推一——
    # recompute_profile 之後仍然會用 job_card_events 現算一次 feedback_count，
    # 這裡先估一個數字純粹是為了算 masked_summary_text 的「第 N 次回饋」文案，
    # 不是真相來源。
    prior_count = d1(f"SELECT COUNT(*) n FROM job_card_events WHERE job_slug={q(job_slug)} "
                     f"AND event_type='feedback_import'")[0]['n']
    masked_text = _masked_summary_text(masked, job_title, prior_count + 1, now)

    d1_http.query(
        f"INSERT INTO job_card_profile (job_slug, full_profile_json, masked_summary_text, updated_at) "
        f"VALUES ({q(job_slug)}, {q(json.dumps(full, ensure_ascii=False))}, {q(masked_text)}, "
        f" datetime('now','+8 hours')) "
        f"ON CONFLICT(job_slug) DO UPDATE SET full_profile_json=excluded.full_profile_json, "
        f" masked_summary_text=excluded.masked_summary_text, updated_at=excluded.updated_at")

    dedupe = f'feedback:{job_slug}:{event_key or now}'
    insert_event(job_slug, 'feedback_import', FEEDBACK_XP, None, None, None,
                f'顧問{actor or ""}匯入客戶回饋', dedupe)
    recompute_profile(job_slug)
    return {'full': full, 'masked': masked, 'masked_summary_text': masked_text}


# ── CLI ──

def _print_profile(job_slug):
    rows = d1(f"SELECT * FROM job_card_profile WHERE job_slug={q(job_slug)}")
    if not rows:
        log(f'{job_slug}：還沒有職缺卡資料')
        return
    p = rows[0]
    lv, name, remain = level_for_xp(p['xp_total'])
    cap = '（卡在Lv.9，品質門檻未達）' if p.get('level_capped_by_gate') else ''
    log(f'{job_slug}：Lv.{p["level"]} {name}{cap}　經驗值 {p["xp_total"]}　'
        f'回饋 {p["feedback_count"]} 筆　命中率 {p.get("accuracy_rate")}　'
        f'婉拒率 {p.get("decline_rate")}　A級樣本數 {p.get("a_grade_settled_n")}')
    log(f'  面談 {p.get("interviewed_n", 0)} 人・送客戶 {p.get("submitted_n", 0)} 人・'
        f'有結果 {p.get("settled_n", 0)} 筆・我方結案/失聯不計 {p.get("excluded_n", 0)} 筆')


def recompute_all_acai_views():
    """「阿財的理解」卡、跟「面談N人／送客戶N人」這類量級數字，都可能在
    完全沒有新的回饋／推薦／錄取事件時就過期——顧問編輯職缺欄位、題庫、
    或單純是 placements 的 stage 被客戶 portal／feedback_intake 改了但
    還沒觸發過 job_card_events（例如這個職缺從來沒有阿財評過分的人選），
    這些都不會被 sync_events() 摸到。
    ⚠️ 2026-09-24 修：原本這裡只呼叫 recompute_acai_view()，職缺卡本身的
    面談人數／送客戶人數／命中率這些數字，對「從來沒有任何 job_card_events」
    的職缺永遠停在 0——改呼叫 recompute_profile()（本來就會在最後順便呼叫
    recompute_acai_view()，兩件事一次做完，不用各自維護一份迴圈）。
    --sync 每輪都會把所有招募中的職缺重算一次，職缺/題庫/客戶回覆的編輯
    最長等下一輪（現在排程是每 2 小時）就會反映。"""
    rows = d1("SELECT slug FROM jobs WHERE status='open'")
    for r in rows:
        recompute_profile(r['slug'])
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sync', nargs='?', const='__all__', default=None)
    ap.add_argument('--check', nargs='?', const='__all__', default=None)
    ap.add_argument('--import-feedback', dest='import_slug', default=None)
    ap.add_argument('--text', default=None)
    ap.add_argument('--image', default=None)
    ap.add_argument('--actor', default=None)
    ap.add_argument('--recompute-acai', dest='recompute_acai_slug', nargs='?', const='__all__', default=None)
    a = ap.parse_args()

    if a.import_slug:
        res = import_feedback(a.import_slug, raw_text=a.text, image_path=a.image, actor=a.actor)
        log(f'✅ {a.import_slug}：回饋已匯入')
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if a.recompute_acai_slug is not None:
        if a.recompute_acai_slug == '__all__':
            n = recompute_all_acai_views()
            log(f'✅ 「阿財的理解」卡重算完成，共 {n} 個招募中的職缺')
        else:
            recompute_acai_view(a.recompute_acai_slug)
            log(f'✅ {a.recompute_acai_slug}：「阿財的理解」卡重算完成')
        return

    if a.sync is not None:
        slug = None if a.sync == '__all__' else a.sync
        touched = sync_events(slug)
        log(f'✅ 對帳完成，更新了 {len(touched)} 個職缺卡：{", ".join(sorted(touched)) or "（沒有新事件）"}')
        if slug is None:
            n = recompute_all_acai_views()
            log(f'✅ 「阿財的理解」卡也一併重算，共 {n} 個招募中的職缺')
        return

    if a.check is not None:
        if a.check == '__all__':
            for r in d1('SELECT job_slug FROM job_card_profile ORDER BY xp_total DESC'):
                _print_profile(r['job_slug'])
        else:
            _print_profile(a.check)
        return

    ap.print_help()


if __name__ == '__main__':
    main()
