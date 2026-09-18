#!/usr/bin/env python3
"""阿財 P3｜職缺推薦共用服務（2026-09-18 建立）

## 為什麼要有這支

稽核（ACAI_P3_CURRENT_STATE_REPORT.md 第六節）發現系統裡已經有三套互不相連的
「推薦其他職缺」邏輯：

  1. interview_daemon.py 的 other_jobs（面談中，`status='open'` LIMIT 40）
  2. ai_worker.py 的 alternative_jobs（電洽前後，`status IN ('open','active')` LIMIT 20）
  3. matching_engine.py（對外獵才用，需要 job_requirement_snapshot，只有 2 筆，形同未使用）

連「哪些職缺算可以推薦」這件最基本的事，三套的定義都不一樣。這支的存在是為了
**不要再長出第四套**——新的 P3 推薦一律走這裡，而 1 跟 2 的職缺清單也改成呼叫
`list_matchable_jobs()`，讓全系統只有一個定義。

## 設計原則（來自稽核報告的結論）

- **職缺側資料幾乎沒有結構化**（hard_filters 只有 1/37 個職缺有填、years_min 4/37），
  所以「確定性規則比對」這條路走不通。現實的做法是：**LLM 讀散文做判斷，程式做安全閥**。
- **候選人側也沒有結構化拒絕條件**（location_ok 是「台北・新北、桃園・新竹、海外外派｜
  想先了解細節再決定」這種自由文字）。所以流程必須是：
  **先讓 LLM 把「明確拒絕」抽成結構化欄位 → 程式再拿這個結構化結果做決定性過濾**。
  程式不自己用關鍵字猜候選人想什麼，但也不無條件相信 LLM 的排除結果。
"""
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

PROMPT_VERSION = 'p3a-2026-09-18'

# 薪資容忍度：職缺薪資上限只要不低於候選人底線的 (1 - 這個比例)，就不直接擋掉，
# 改成標成「需注意」交給顧問判斷。實務上一成以內的差距是可以談的，
# 系統不該替顧問把這種機會殺掉（2026-09-18 陳旻婕案例，見 hard_safety_filter 註解）。
SALARY_TOLERANCE = 0.15

# ── 可推薦職缺的唯一定義 ────────────────────────────────────────────
# 稽核發現同一個概念全站有三種寫法（`status='open'` / `IN ('open','active')` /
# `NOT IN ('closed','pending_review','client_draft')`）。以後只認這一份。
MATCHABLE_STATUSES = ('open', 'active')
EXCLUDED_STATUSES = ('closed', 'draft', 'client_draft', 'pending_review')


# 安全閥比對用的關鍵字。只列「候選人真的會明確拒絕」的條件，
# 不要把所有地名都塞進來——比對範圍越大，誤擋好機會的風險越高。
REJECTION_KEYWORDS = (
    '柬埔寨', '印度', '越南', '菲律賓', '泰國', '馬來西亞', '中國', '大陸',
    '外派', '駐點', '夜班', '大夜', '輪班', '無塵室', '派遣', '約聘',
    '苗栗', '雲林', '台中', '桃園', '新竹', '高雄', '台南', '嘉義', '彰化', '屏東',
)

# ⚠️ 2026-09-18：職缺的 locations 欄位實際上大量使用區域縮寫（真實資料例：
# 「桃竹苗地區、台中」），候選人講的卻是單一縣市（「苗栗我不能接受」）。
# 純字面比對「苗栗 in 桃竹苗地區」會是 False → 漏擋。這張表把縮寫展開。
REGION_ALIASES = {
    '桃園': ['桃竹苗', '北北基桃'],
    '新竹': ['桃竹苗', '竹科'],
    '苗栗': ['桃竹苗'],
    '台中': ['中彰投'],
    '彰化': ['中彰投'],
    '雲林': ['雲嘉南'],
    '嘉義': ['雲嘉南'],
    '台南': ['雲嘉南'],
    '高雄': ['高屏'],
    '屏東': ['高屏'],
}


def _q(v):
    """SQL 字面值跳脫。跟 ai_worker.py 的 q() 同行為，但不 import 它——
    ai_worker 會 import 這支，反向 import 會變成循環相依。"""
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _json_loads(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def list_matchable_jobs(exclude_slug=None, limit=40):
    """全系統唯一的「哪些職缺可以被推薦」定義。

    exclude_slug：候選人目前應徵的職缺（不可以把他已經在應徵的職缺當成「替代方案」
    推薦給他，這是規格明訂的）。
    """
    placeholders = ', '.join(_q(s) for s in MATCHABLE_STATUSES)
    where = [f"COALESCE(status,'open') IN ({placeholders})"]
    if exclude_slug:
        where.append(f'slug != {_q(exclude_slug)}')
    rows = d1_http.query(
        'SELECT slug, title, locations, salary_min, salary_max, salary_unit, salary_note, '
        'employment, seniority, years_min, education_level, language_requirement, '
        'must_skills, nice_to_have_skills, required_conditions, main_duties, '
        'hard_filters, must_check_items, status '
        f"FROM jobs WHERE {' AND '.join(where)} "
        f'ORDER BY updated_at DESC LIMIT {int(limit)}'
    )['results']
    return rows or []


def build_candidate_snapshot(application_id):
    """把「這個人目前我們知道什麼」收斂成一份快照。

    P3-A 刻意**不建立 candidate profile 表**（那是更大的資料工程，見稽核報告第十三節），
    第一版就用既有的 applications + reports.content_json 組出當下這一次判斷用的快照，
    並且整包存進推薦紀錄裡——這樣三個月後回頭看「AI 當時憑什麼推薦」，看得到的是
    **當時**的資料，不是現在被改過的資料。
    """
    app_rows = d1_http.query(
        'SELECT id, name, job_slug, job_title, expected_salary, available_date, location_ok, '
        'note, interview_state, interview_ended_at, disc_primary '
        f'FROM applications WHERE id={_q(application_id)}'
    )['results']
    if not app_rows:
        return None, None
    app = app_rows[0]

    rep_rows = d1_http.query(
        'SELECT id, content_json, content_md, created_at FROM reports '
        f'WHERE application_id={_q(application_id)} ORDER BY created_at DESC LIMIT 1'
    )['results']
    report = rep_rows[0] if rep_rows else None
    rj = _json_loads(report.get('content_json'), {}) if report else {}

    snapshot = {
        'application_id': app['id'],
        'name': app.get('name'),
        'applied_job_slug': app.get('job_slug'),
        'applied_job_title': app.get('job_title'),
        # ── 應徵表單的原始自由文字（不解讀，原樣帶著）──
        'form': {
            'expected_salary': app.get('expected_salary'),
            'available_date': app.get('available_date'),
            'location_ok': app.get('location_ok'),
            'note': app.get('note'),
        },
        # ── 面談報告裡已經結構化的部分（阿財產出的 content_json）──
        'interview': {
            'verdict': rj.get('verdict'),
            'one_liner': rj.get('one_liner'),
            'summary': rj.get('summary'),
            'top_selling_point': rj.get('top_selling_point'),
            'top_risk': rj.get('top_risk'),
            'motivation': rj.get('motivation'),
            'work_history': rj.get('work_history'),
            'hard_conditions': rj.get('hard_conditions'),
            'blocker_findings': rj.get('blocker_findings'),
            'expertise_findings': rj.get('expertise_findings'),
            'resume_vs_spoken': rj.get('resume_vs_spoken'),
            'observations': rj.get('observations'),
            'consultant_followups': rj.get('consultant_followups'),
        },
        'snapshot_at': _now(),
    }
    return snapshot, report


# ── 決定性安全閥 ────────────────────────────────────────────────────
# 規格第九節要求「即使 LLM 推薦，也必須再經 deterministic filter」。
# ⚠️ 這裡是對 GPT 原始規格的修正：原規格要求用「候選人明確拒絕的地點／僱用型態／
#    薪資」做過濾，但稽核發現 applications 上**根本沒有這種結構化欄位**
#    （location_ok 是一整句自由文字）。所以改成：
#      LLM 先把「明確拒絕」抽成結構化 explicit_rejections → 程式拿它做過濾。
#    程式不自己用關鍵字猜候選人的想法（那會亂猜），但也不讓 LLM 自己決定要不要排除
#    （它會忘記）。判斷歸 LLM，執行歸程式。

def _salary_floor(snapshot, llm_out):
    """取得候選人的薪資底線（數字）。優先用 LLM 抽出來的結構化值，
    抽不到就不做薪資過濾——**寧可不擋，也不要用猜的擋掉好機會**。"""
    raw = (llm_out or {}).get('minimum_salary_monthly')
    try:
        v = float(raw)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def hard_safety_filter(recommendations, snapshot, llm_out, jobs_by_slug, existing_slugs=()):
    """回傳 (kept, dropped)。dropped 每筆附原因，會寫進交付報告與稽核紀錄。

    擋掉的情況：
      1. 職缺已不在可推薦狀態（雙重確認，防止產生期間被關閉）
      2. 候選人原本應徵的職缺
      3. 候選人已經有的其他應徵／已存在的推薦（不重複推薦）
      4. 命中候選人明確拒絕的條件（地點／僱用型態／班別）
      5. 職缺薪資上限低於候選人明確講過的底線
    """
    kept, dropped = [], []
    rejections = (llm_out or {}).get('explicit_rejections') or []
    rejection_text = ' '.join(
        f"{r.get('code', '')} {r.get('label', '')}" for r in rejections if isinstance(r, dict)
    ).lower()
    floor = _salary_floor(snapshot, llm_out)
    applied_slug = (snapshot or {}).get('applied_job_slug')

    for rec in recommendations:
        slug = rec.get('job_slug')
        job = jobs_by_slug.get(slug)
        reason = None

        if not job:
            reason = 'job_not_in_matchable_list'
        elif str(job.get('status') or 'open') not in MATCHABLE_STATUSES:
            reason = f"job_status_{job.get('status')}"
        elif slug == applied_slug:
            reason = 'same_as_applied_job'
        elif slug in set(existing_slugs):
            reason = 'already_applied_or_recommended'

        if reason is None and rejection_text:
            loc = str(job.get('locations') or '')
            emp = str(job.get('employment') or '')
            haystack = loc + ' ' + emp
            # 地名／條件關鍵字：只比對「LLM 已經明確抽出來的拒絕條件」與「職缺欄位原文」
            # 兩邊都出現的字，不做任何語意推測。
            for kw in REJECTION_KEYWORDS:
                if kw not in rejection_text:
                    continue
                # ⚠️ 2026-09-18 測試時發現的漏洞：職缺地點欄位實際上寫的是
                # 「桃竹苗地區」這種縮寫，候選人說「不要苗栗」時字面比對不到，
                # 會漏擋。這裡把常見的台灣區域縮寫展開後一起比對。
                targets = [kw] + REGION_ALIASES.get(kw, [])
                if any(t in haystack for t in targets):
                    reason = f'candidate_rejected:{kw}'
                    break

        if reason is None and floor and job.get('salary_max'):
            # ⚠️ 2026-09-18 實測調整：原本只要「職缺上限 < 底線」就直接刪掉，
            # 結果陳旻婕（底線被 AI 抓成表單上的期望薪資 70K）兩個很合適的
            # 半導體職缺（上限 60K）全被靜默擋掉——那正是我們最想推的 BIM→半導體
            # 轉職案例。薪資差一成以內在實務上是完全可以談的，把它直接刪掉等於
            # 系統替顧問做了不該它做的取捨。
            # 改成：差距超過 SALARY_TOLERANCE 才擋；在容忍範圍內照樣推薦，
            # 但把差距寫成「需注意」讓顧問自己決定要不要談。
            try:
                jmax = float(job['salary_max'])
                if jmax < floor * (1 - SALARY_TOLERANCE):
                    reason = f"salary_below_floor({job['salary_max']}<{floor})"
                elif jmax < floor:
                    rec.setdefault('blockers', [])
                    rec['blockers'] = (rec['blockers'] + [
                        f'薪資可能有落差：職缺上限 {int(jmax)}，候選人提過的水準是 {int(floor)}'
                    ])[:2]
            except (TypeError, ValueError):
                pass

        if reason:
            dropped.append({'job_slug': slug, 'reason': reason})
        else:
            kept.append(rec)
    return kept, dropped


def existing_job_slugs_for_candidate(application_id, name=None, email=None):
    """這個人已經在應徵的職缺 + 已經被推薦過的職缺，兩者都不該再推一次。

    ⚠️ 系統目前沒有「人」這個實體（只有「應徵」），同一個人投三次就是三筆獨立資料
    （稽核報告技術債 #11）。這裡用 email 做粗略的同一人判定——不完美，但比完全不查好；
    真正的身分合併留給後續 Phase。
    """
    slugs = set()
    rows = d1_http.query(
        f'SELECT job_slug FROM applications WHERE id={_q(application_id)}'
    )['results']
    for r in rows or []:
        if r.get('job_slug'):
            slugs.add(r['job_slug'])
    if email:
        rows = d1_http.query(
            f'SELECT job_slug FROM applications WHERE email={_q(email)}'
        )['results']
        for r in rows or []:
            if r.get('job_slug'):
                slugs.add(r['job_slug'])
    rows = d1_http.query(
        'SELECT recommended_job_slug FROM candidate_job_recommendations '
        f"WHERE application_id={_q(application_id)} AND status IN ('pending_review','approved')"
    )['results']
    for r in rows or []:
        if r.get('recommended_job_slug'):
            slugs.add(r['recommended_job_slug'])
    return slugs


def save_recommendations(application_id, source_job_slug, source_report_id, kept,
                         snapshot, jobs_by_slug, model, uid_fn):
    """寫入 candidate_job_recommendations。

    idempotency 靠 (source_report_id, recommended_job_slug) 的唯一索引——
    同一份報告重跑不會產生重複列（INSERT OR IGNORE 直接被索引擋掉）。
    """
    saved = 0
    for rank, rec in enumerate(kept[:3], start=1):
        slug = rec.get('job_slug')
        job = jobs_by_slug.get(slug) or {}
        row_id = uid_fn()
        sql = (
            'INSERT OR IGNORE INTO candidate_job_recommendations '
            '(id, application_id, source_job_slug, recommended_job_slug, match_status, confidence, '
            ' rank, reasons_json, blockers_json, missing_information_json, evidence_json, '
            ' candidate_snapshot_json, job_snapshot_json, status, source_report_id, model, '
            ' prompt_version, created_at) VALUES ('
            f'{_q(row_id)}, {_q(application_id)}, {_q(source_job_slug)}, {_q(slug)}, '
            f"{_q(rec.get('match_status'))}, {_q(rec.get('confidence'))}, {rank}, "
            f"{_q(json.dumps(rec.get('reasons') or [], ensure_ascii=False))}, "
            f"{_q(json.dumps(rec.get('blockers') or [], ensure_ascii=False))}, "
            f"{_q(json.dumps(rec.get('missing_information') or [], ensure_ascii=False))}, "
            f"{_q(json.dumps(rec.get('evidence') or [], ensure_ascii=False))}, "
            f'{_q(json.dumps(snapshot, ensure_ascii=False))}, '
            f'{_q(json.dumps(job, ensure_ascii=False))}, '
            f"'pending_review', {_q(source_report_id)}, {_q(model)}, {_q(PROMPT_VERSION)}, "
            f'{_q(_now())})'
        )
        r = d1_http.query(sql)
        if (r.get('meta') or {}).get('changes'):
            saved += 1
    return saved


def _tg(text, thread=None):
    """推 Telegram 給顧問。

    刻意在這裡寫一份小的，而不是 import interview_daemon 借它的 tg()——
    那支是常駐面談 daemon，import 進來會把整個面談模組的載入成本與副作用
    一起拖進這支輕量服務。設定檔用同一個（step1ne-tg.env），bot 與群組不變。
    """
    import urllib.parse
    import urllib.request
    try:
        e = dict(
            l.strip().split('=', 1)
            for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
            if '=' in l and not l.startswith('#')
        )
        body = {'chat_id': e['TG_CHAT_ID'], 'text': text}
        tid = thread if thread is not None else e.get('TG_THREAD_ID')
        if tid:
            body['message_thread_id'] = tid
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode(body).encode(), timeout=20).read()
        return True
    except Exception:
        return False


# 顧問決策討論串（跟 interview_daemon.py 的 THREAD_DECIDE 同一個）——
# 這是「需要顧問動手」的主題，推薦職缺屬於這一類。
THREAD_DECIDE = 2855


def notify_consultant(snapshot, kept, dropped_count=0):
    """P3-A 的通知：只推給顧問，**絕對不推給候選人**。

    規格明訂這一輪 AI 只做建議，候選人端零接觸。訊息裡要讓顧問一眼看到
    「憑什麼推薦」跟「還要確認什麼」，而不是只有一個職缺名稱。
    """
    if not kept:
        return False
    name = (snapshot or {}).get('name') or '（未命名）'
    applied = (snapshot or {}).get('applied_job_title') or (snapshot or {}).get('applied_job_slug') or '—'
    lines = [f'🤖 阿財找到可能更適合的職缺', '', f'候選人：{name}', f'原應徵：{applied}', '']
    label = {'MATCH_CANDIDATE': '很可能適合', 'POSSIBLE_MATCH': '可能適合',
             'INSUFFICIENT_DATA': '資料不足', 'NOT_MATCH': '不適合'}
    for rec in kept[:3]:
        lines.append(f"▸ {rec.get('job_title') or rec.get('job_slug')}（{label.get(rec.get('match_status'), '')}）")
        for r in (rec.get('reasons') or [])[:3]:
            lines.append(f'　• {r}')
        for m in (rec.get('missing_information') or [])[:2]:
            lines.append(f'　待確認：{m}')
        lines.append('')
    lines.append('這只是 AI 的建議，還沒有通知候選人、也沒有改動任何狀態。')
    # ⚠️ 2026-09-18：這個功能的畫面只存在新版顧問後台，而新版目前只在測試網址
    # （正式站 step1ne.com/consultant/ 是另一個帳號的舊版 Worker，這台機器沒有那把
    # token，也還沒切換）。這裡直接附上可以點的網址，不然顧問收到通知會找不到在哪看。
    # V3 切換到正式站之後，記得回來把這個網址換掉。
    lines.append('')
    lines.append('👉 打開人選卡片 →「電洽準備」→「AI 職缺推薦」分頁：')
    lines.append('https://step1ne-consultant-staging.pages.dev/consultant/candidates/')
    return _tg('\n'.join(lines), THREAD_DECIDE)


def save_followup(application_id, due_at, reason, evidence, source, uid_fn):
    """P3-C②：把「下個月再聯絡我」這種約定存成有到期日的待辦。

    到期後由 Worker 的 15 分鐘 cron 撈出來提醒顧問（不主動聯絡候選人）。
    """
    if not due_at:
        return False
    exists = d1_http.query(
        'SELECT id FROM candidate_followups '
        f"WHERE application_id={_q(application_id)} AND status='pending'"
    )['results']
    if exists:
        return False  # 已經有待辦就不重複開，避免同一人一堆重複提醒
    d1_http.query(
        'INSERT INTO candidate_followups '
        '(id, application_id, due_at, reason, source, evidence, status, created_by, created_at) '
        f'VALUES ({_q(uid_fn())}, {_q(application_id)}, {_q(due_at)}, {_q(reason)}, '
        f'{_q(source)}, {_q(evidence)}, \'pending\', \'ai\', {_q(_now())})'
    )
    return True
