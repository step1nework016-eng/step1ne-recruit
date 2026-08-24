#!/usr/bin/env python3
"""Step1ne 精準配對防呆引擎 —— 把 SOURCING_RULES.md 第 15 節的規則變成可呼叫的檢查函式。

為什麼要有這支（2026-08-23 Jacky 交辦，接續同一天的 sourcing_engine.py）：
    sourcing_engine.py 管的是「怎麼找人」——搜尋端可以、也應該寬。
    這支管的是「找到的人怎麼判定算不算真的符合」——推薦端必須非常窄。
    一句話原則：**找人的時候可以寬，推薦人的時候必須非常窄。**

    跟 sourcing_engine.py 一樣，光靠寫在文件裡的規則沒有強制力，AI agent 讀完
    SOURCING_RULES.md 第 15 節，還是可能在實際判定時被「這個人條件真的很好」的
    直覺說服，把一項 Hard Fail 用高分蓋過去。這支模組把規則變成**程式檢查**——
    尤其是 `evaluate_candidate()`：任一 Hard Must 是 FAIL，不管總分多高，
    都不可能算出 MATCH_CANDIDATE；任一 Hard Must 是 UNKNOWN，最高只能
    INSUFFICIENT_DATA。這兩條邏輯是整支程式的靈魂，寫錯了這支就沒有意義。

    這一輪**不做任何真實候選人的重新判定**，也不動 Phase 2.1-2.5 留下的
    candidate_job_match 紀錄。這支只是基礎設施，讓未來真的要做 matching 判定的
    agent，能用這幾個函式跑判定、自我校準、事後稽核。

跟 sourcing_engine.py 怎麼搭配：
    同一個 sourcing_run_id 底下，sourcing_engine.py 負責記錄「有沒有真的搜過」，
    這支負責記錄「搜到的候選人，能不能被判定為 Match」。
    最終判定結果寫進既有的 candidate_job_match 表（Phase 2.1 建的），不另建平行表。

用法（CLI，主要給人工測試用，正式流程是被 sourcing/matching agent import）：
    python3 matching_engine.py check-ready --job business-operations-manager
    python3 matching_engine.py evaluate --job business-operations-manager --evidence-json evidence.json
"""
import argparse
import datetime
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


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
_d1_raw, q = D.d1, D.q


def d1(sql, retries=3):
    """帶重試的 d1() 包裝，抄 sourcing_engine.py 同一套邏輯——理由也一樣：
    D1 常見兩種暫時性錯誤：7429（rate limit / timeout）、7009（不可用），
    值得重試 2-3 次；其他錯誤（SQL 語法錯之類）直接往外拋，重試沒意義。
    """
    last_err = None
    for attempt in range(retries):
        try:
            return _d1_raw(sql)
        except Exception as e:
            msg = str(e)
            last_err = e
            if '7429' in msg or '7009' in msg:
                log(f'D1 暫時性錯誤（第 {attempt + 1} 次），重試中：{msg[:120]}')
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_err


# ── 常數：對應 SOURCING_RULES.md 第 15 節 ──

MATCH_DIMENSIONS = ['EXPERIENCE', 'INDUSTRY', 'FUNCTION', 'SENIORITY',
                     'ENGLISH', 'LOCATION', 'TRAVEL', 'COMPENSATION']

RECRUITABILITY_LEVELS = ['NORMAL', 'LOW', 'VERY_LOW', 'UNKNOWN']

FINAL_VERDICTS = ['MATCH_CANDIDATE', 'POSSIBLE_MATCH', 'INSUFFICIENT_DATA', 'NOT_MATCH']

DIMENSION_VERDICTS = ['PASS', 'FAIL', 'UNKNOWN']

REQUIREMENT_TIERS = ['HARD_MUST', 'SOFT_MUST', 'NICE_TO_HAVE', 'UNKNOWN', 'EXCLUSION']

# 15.8 節：False Positive Guard 要檢查的五種 Context
CONTEXT_FLAGS = ['title_context', 'industry_context', 'function_context',
                  'seniority_context', 'career_context']

# 15.9 節：Minimum Evidence Rule
MIN_INDEPENDENT_SOURCES = 2

# 15.17 節：事後稽核抽樣數與推翻率門檻
AUDIT_SAMPLE_SIZE = 5
AUDIT_OVERTURN_RATE_ALERT = 0.20

# 15.14 節：Query Precision Feedback Loop 門檻
FALSE_POSITIVE_RATE_REFINE_THRESHOLD = 0.70

# ── 第 16 節：Adaptive Precision/Recall（Broad Discovery, Strict Matching）──
#
# 三段式漏斗，接在 15.3 節「完整判定漏斗」中間，把原本 CANDIDATE_DISCOVERED →
# 直接走 Requirement Gate，拆成兩段：
#
#   SEARCH_HIT → CANDIDATE_DISCOVERED → POSSIBLE_CANDIDATE（Discovery，寬鬆）
#       → SHORTLISTED（開始嚴格驗證 experience/industry/function/seniority/
#         English/location/travel/recruitability）
#       → 走 evaluate_candidate() 的 Requirement Gate（Match，絕對嚴格）
#       → MATCH_CANDIDATE / POSSIBLE_MATCH / INSUFFICIENT_DATA / NOT_MATCH
#
# DISCOVERY_FUNNEL_STAGES 只是給呼叫端/AI agent 標記候選人目前走到漏斗哪一步用的
# 詞彙表，不是新的判定邏輯——真正的判定邏輯還是 evaluate_candidate()，
# 這個常數只是讓「這個人現在是寬鬆篩過還是已經嚴格驗證過」有個標準說法可以記錄，
# 避免不同 agent 自己發明不同的中間狀態名稱。
DISCOVERY_FUNNEL_STAGES = ['SEARCH_HIT', 'CANDIDATE_DISCOVERED', 'POSSIBLE_CANDIDATE', 'SHORTLISTED']

# SOURCING_RULES.md 第 16 節：找到的候選人太少時，依序放寬 Discovery 條件的六層順序。
# 只能照順序往下一層走，不能跳兩層（next_widening_layer() 負責擋這件事）。
# ⚠️ 這份清單只影響「要不要繼續往下搜」，完全不影響 evaluate_candidate() 的 Match 判定邏輯
# ——evaluate_candidate() 的函式簽章裡根本沒有 widening layer 這個參數，
# 它不知道、也不需要知道候選人是從第幾層搜尋找到的（詳見 evaluate_candidate() docstring）。
WIDENING_SEQUENCE = ['EXACT_TITLE', 'ADJACENT_TITLE', 'RELATED_FUNCTION',
                     'RELATED_INDUSTRY', 'RELATED_COMPANY_TYPE', 'CAREER_PATH_SIMILARITY']

# SOURCING_RULES.md 第 16.5 節：Candidate Discovered 低於這個數字，就要自動 WIDEN_SEARCH。
MIN_CANDIDATES_BEFORE_WIDENING = 10


# ── Schema 補強：candidate_job_match / sourcing_runs 需要的欄位、job_requirement_snapshot 新表 ──
#
# 這幾張表的主體已經在 Phase 2.1（candidate_job_match）跟今天稍早的
# sourcing_engine.py（sourcing_runs）建過。這支模組需要三個額外的東西：
#   1. job_requirement_snapshot：存第 15.1 節「拆解完成」的 Requirement 五分類
#   2. candidate_job_match.sourcing_run_id：讓 run_false_positive_audit() 能按 run 抽查
#      （原表沒有這個外鍵，之前的判定紀錄是獨立於 sourcing_run 存在的）
#   3. sourcing_runs.precision_alert：存 15.17 節 PRECISION_ALERT 的稽核結果
#
# 用 ALTER TABLE ADD COLUMN，不是重建表——不能動 Phase 2.1-2.5 留下的既有資料。
# D1/SQLite 對「欄位已存在」會直接報錯，所以每個 ALTER 都包一層 try/except 忽略重複執行。

def ensure_schema():
    d1("""CREATE TABLE IF NOT EXISTS job_requirement_snapshot (
        job_slug TEXT PRIMARY KEY,
        hard_must TEXT,
        soft_must TEXT,
        nice_to_have TEXT,
        unknown TEXT,
        exclusion TEXT,
        is_complete INTEGER,
        created_at TEXT,
        updated_at TEXT,
        source TEXT
    )""")
    for stmt in (
        "ALTER TABLE candidate_job_match ADD COLUMN sourcing_run_id TEXT",
        "ALTER TABLE sourcing_runs ADD COLUMN precision_alert TEXT",
    ):
        try:
            d1(stmt)
        except Exception as e:
            if 'duplicate column' not in str(e).lower():
                raise


# ── 第 16 節：Adaptive Widening（放寬搜尋，不放寬標準）──
#
# 這一段函式群只碰 sourcing_runs.notes 裡的 widening 進度，跟 sourcing_engine.py
# 的 mark_source_type() 共用同一個「notes 是一個 JSON meta dict」的存法（那支已經
# 用 notes 存 source_types_used），這裡加一個 widening_layer 鍵，不會互相打架
# ——兩邊都是讀出整份 JSON、只改自己負責的那個鍵、寫回去。
#
# ⚠️ 特別提醒（這是這一節的靈魂，不能寫錯）：
#   放寬（widening）只發生在 Discovery/Search 階段，決定「要不要繼續往下一層搜」。
#   它完全不碰、也不應該碰 evaluate_candidate() 的任何判定邏輯。
#   看 evaluate_candidate() 的函式簽章就知道：candidate_evidence + requirement_snapshot
#   兩個參數，沒有第三個 widening_layer 參數——它天生就不知道候選人是從第幾層搜尋
#   找到的，Hard Must 的 Gate 邏輯（15.2 節）不會因為搜尋層放寬到 CAREER_PATH_SIMILARITY
#   就跟著放寬。這是結構上天然成立的事，不需要額外程式碼去「防止」它被繞過。

def next_widening_layer(current_layer, requested_layer=None):
    """回傳從 current_layer 合法能放寬到的下一層。

    current_layer 為 None 代表「還沒開始放寬」，下一層就是序列第一層 EXACT_TITLE。

    如果呼叫端用 requested_layer 指定想跳去的層級，這裡要檢查它是不是「剛好下一層」
    ——不是就擋下來（allowed=False），並且清楚告訴呼叫端唯一合法的下一層是哪個，
    不准一次跳兩層（例如從 EXACT_TITLE 直接要求跳到 RELATED_INDUSTRY）。
    """
    if current_layer is None:
        proper_next = WIDENING_SEQUENCE[0]
    else:
        if current_layer not in WIDENING_SEQUENCE:
            raise ValueError(f'current_layer 必須是 {WIDENING_SEQUENCE} 之一或 None，收到 {current_layer!r}')
        idx = WIDENING_SEQUENCE.index(current_layer)
        if idx == len(WIDENING_SEQUENCE) - 1:
            return {'allowed': False, 'next_layer': None,
                    'reason': f'{current_layer} 已經是放寬序列的最後一層（CAREER_PATH_SIMILARITY），'
                              f'不能再往下放寬。這時候人還是不夠，只能照 SOURCING_RULES.md 第 10 節'
                              f'講「在目前已執行的搜尋範圍內還沒找到」，不能再繼續放寬搜尋條件。'}
        proper_next = WIDENING_SEQUENCE[idx + 1]

    if requested_layer is not None and requested_layer != proper_next:
        return {'allowed': False, 'next_layer': proper_next,
                'reason': f'不准跳層。從 {current_layer!r} 只能放寬到 {proper_next!r}，'
                          f'不能直接跳到 {requested_layer!r}。'}

    return {'allowed': True, 'next_layer': proper_next,
            'reason': f'合法放寬：{current_layer!r} → {proper_next!r}'}


def _get_widening_meta(run_id):
    rows = d1(f"SELECT notes FROM sourcing_runs WHERE sourcing_run_id={q(run_id)}")
    if not rows:
        raise ValueError(f'找不到 sourcing run {run_id}')
    notes = rows[0].get('notes') or '{}'
    try:
        meta = json.loads(notes)
    except (TypeError, ValueError):
        meta = {}
    return meta


def check_needs_widening(sourcing_run_id):
    """讀該 run 目前 candidates_discovered_count，判斷要不要 WIDEN_SEARCH。

    候選人數直接讀 sourcing_runs.candidates_discovered_count——這個欄位是
    sourcing_engine.py 的 log_query() 每記一組 query 就透過 _refresh_run_counts()
    自動累加更新的，是這個 run 目前已知的候選人發現總數，不需要在這裡重算一次。

    低於 MIN_CANDIDATES_BEFORE_WIDENING（10）就回傳 needs_widening=True，並告訴
    呼叫端目前在放寬序列第幾層、下一層該用什麼（透過 next_widening_layer()）。
    目前層級存在 sourcing_runs.notes 的 widening_layer 鍵，沒放寬過就是 None
    （代表下一步是序列第一層 EXACT_TITLE）。
    """
    rows = d1(f"SELECT candidates_discovered_count FROM sourcing_runs "
              f"WHERE sourcing_run_id={q(sourcing_run_id)}")
    if not rows:
        raise ValueError(f'找不到 sourcing run {sourcing_run_id}')
    count = rows[0].get('candidates_discovered_count') or 0

    meta = _get_widening_meta(sourcing_run_id)
    current_layer = meta.get('widening_layer')

    if count >= MIN_CANDIDATES_BEFORE_WIDENING:
        return {'needs_widening': False, 'candidates_discovered_count': count,
                'current_layer': current_layer,
                'reason': f'候選人數 {count} >= {MIN_CANDIDATES_BEFORE_WIDENING}，不需要放寬搜尋。'}

    widen = next_widening_layer(current_layer)
    return {
        'needs_widening': True,
        'candidates_discovered_count': count,
        'current_layer': current_layer,
        'next_layer': widen['next_layer'],
        'can_widen_further': widen['allowed'],
        'reason': (f'候選人數 {count} < {MIN_CANDIDATES_BEFORE_WIDENING}，需要 WIDEN_SEARCH。' +
                   (f' 下一層放寬到 {widen["next_layer"]}。' if widen['allowed']
                    else f' 但已經放寬到底：{widen["reason"]}')),
    }


def advance_widening_layer(sourcing_run_id, to_layer=None):
    """把 sourcing_run 的放寬進度往前推一層，寫回 sourcing_runs.notes.widening_layer。

    to_layer 不給的話，就照 next_widening_layer() 算出來的合法下一層推進；
    給了的話，一樣要通過 next_widening_layer() 的跳層檢查，擋下不合法的跳層請求
    （例如想從 EXACT_TITLE 直接跳到 RELATED_INDUSTRY）。
    """
    meta = _get_widening_meta(sourcing_run_id)
    current_layer = meta.get('widening_layer')
    widen = next_widening_layer(current_layer, requested_layer=to_layer)
    if not widen['allowed']:
        raise ValueError(f'拒絕推進放寬層級：{widen["reason"]}')

    meta['widening_layer'] = widen['next_layer']
    d1(f"UPDATE sourcing_runs SET notes={q(json.dumps(meta, ensure_ascii=False))} "
       f"WHERE sourcing_run_id={q(sourcing_run_id)}")
    log(f'　{sourcing_run_id} 放寬搜尋層級 → {widen["next_layer"]}（{widen["reason"]}）')
    return widen['next_layer']


# ── 15.1 節：Search Before Search，五類拆解 ──

def split_requirements(job_slug):
    """讀 jobs 表該職缺的欄位，回傳一個給呼叫端/AI agent 填的 Requirement 拆解骨架。

    這個函式**不自動判斷內容語意**——不會自己猜哪個技能是 Hard Must、哪個是 Nice
    to Have，那是需要人類顧問或 AI agent 讀過 JD 之後的判斷。這個函式只做兩件事：
      1. 把 jobs 表裡跟資格相關的欄位（must_skills / years_min / salary_min /
         salary_max / locations / employment / notes）整理成 source_fields，
         當作拆解時的參考起點，不用重新去 jobs 表撈。
      2. 給一個五分類的空骨架（HARD_MUST / SOFT_MUST / NICE_TO_HAVE / UNKNOWN /
         EXCLUSION），呼叫端/AI agent 把實際內容填進去，再呼叫
         save_requirement_snapshot() 存檔。

    ⚠️ jobs 表沒有獨立的 scoring_notes 欄位，用既有的 notes（顧問備註：這個客戶
    在意什麼、踩過什麼雷）代替——這欄位常常就是 Hard Must / Exclusion 的來源。
    """
    rows = d1(f"SELECT slug, title, years_min, must_skills, salary_min, salary_max, "
              f"salary_unit, locations, employment, notes, client_screen_conditions, status "
              f"FROM jobs WHERE slug={q(job_slug)}")
    if not rows:
        raise ValueError(f'找不到職缺 {job_slug}（jobs 表沒有這個 slug）')
    job = rows[0]

    return {
        'job_slug': job_slug,
        'source_fields': {
            'title': job.get('title'),
            'years_min': job.get('years_min'),
            'must_skills': job.get('must_skills'),
            'salary_min': job.get('salary_min'),
            'salary_max': job.get('salary_max'),
            'salary_unit': job.get('salary_unit'),
            'locations': job.get('locations'),
            'employment': job.get('employment'),
            'scoring_notes': job.get('notes'),
            'client_screen_conditions_internal_only': job.get('client_screen_conditions'),
        },
        'HARD_MUST': [],
        'SOFT_MUST': [],
        'NICE_TO_HAVE': [],
        'UNKNOWN': [],
        'EXCLUSION': [],
        'is_complete': False,
    }


def save_requirement_snapshot(job_slug, hard_must, soft_must, nice_to_have,
                               unknown, exclusion, source='manual'):
    """把已拆解完成的五分類存進 job_requirement_snapshot，供 check_requirement_ready() 讀。

    hard_must/soft_must/nice_to_have/unknown/exclusion 都是 list[dict 或 str]，
    內容格式不強制（不同職缺的條件形狀不同），存進去就是原樣 JSON 化。

    is_complete 的判斷很寬鬆：只要 HARD_MUST 這一類不是空的，就視為「拆解完成」
    ——因為 15.2 節說 HARD_MUST 是絕對 Gate，沒有 Hard Must 清單，後面的判定
    根本無從做起。SOFT_MUST/NICE_TO_HAVE 允許是空的（有些職缺真的沒有軟性條件）。
    """
    ensure_schema()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    is_complete = 1 if hard_must else 0

    existing = d1(f"SELECT job_slug FROM job_requirement_snapshot WHERE job_slug={q(job_slug)}")
    if existing:
        d1(f"""UPDATE job_requirement_snapshot SET
               hard_must={q(json.dumps(hard_must, ensure_ascii=False))},
               soft_must={q(json.dumps(soft_must, ensure_ascii=False))},
               nice_to_have={q(json.dumps(nice_to_have, ensure_ascii=False))},
               unknown={q(json.dumps(unknown, ensure_ascii=False))},
               exclusion={q(json.dumps(exclusion, ensure_ascii=False))},
               is_complete={is_complete}, updated_at={q(now)}, source={q(source)}
               WHERE job_slug={q(job_slug)}""")
    else:
        d1(f"""INSERT INTO job_requirement_snapshot
               (job_slug, hard_must, soft_must, nice_to_have, unknown, exclusion,
                is_complete, created_at, updated_at, source)
               VALUES ({q(job_slug)}, {q(json.dumps(hard_must, ensure_ascii=False))},
               {q(json.dumps(soft_must, ensure_ascii=False))},
               {q(json.dumps(nice_to_have, ensure_ascii=False))},
               {q(json.dumps(unknown, ensure_ascii=False))},
               {q(json.dumps(exclusion, ensure_ascii=False))},
               {is_complete}, {q(now)}, {q(now)}, {q(source)})""")
    log(f'{"✅" if is_complete else "⚠️"} {job_slug} requirement snapshot 已存檔'
        f'（is_complete={bool(is_complete)}，hard_must={len(hard_must)} 項）')
    return is_complete


def delete_requirement_snapshot(job_slug):
    """刪掉測試用的 requirement snapshot，避免污染正式表——自我驗證後要呼叫這個清乾淨。"""
    d1(f"DELETE FROM job_requirement_snapshot WHERE job_slug={q(job_slug)}")


def check_requirement_ready(job_slug):
    """檢查該職缺是否已有拆解完成的 Requirement snapshot。

    沒有 → 回傳 SOURCING_BLOCKED_REQUIREMENT_INCOMPLETE，不准開始判定（也不准開始 sourcing）。
    有 → 回傳 ready=True 跟還原成 dict 的五分類，直接餵給 evaluate_candidate()。
    """
    ensure_schema()
    rows = d1(f"SELECT * FROM job_requirement_snapshot WHERE job_slug={q(job_slug)}")
    if not rows or not rows[0].get('is_complete'):
        return {
            'ready': False,
            'code': 'SOURCING_BLOCKED_REQUIREMENT_INCOMPLETE',
            'reason': f'{job_slug} 還沒有拆解完成的 Requirement snapshot'
                      f'（至少要有 HARD_MUST 清單），不准開始 sourcing 或判定。',
        }
    row = rows[0]
    snapshot = {
        'job_slug': job_slug,
        'HARD_MUST': _safe_json_list(row.get('hard_must')),
        'SOFT_MUST': _safe_json_list(row.get('soft_must')),
        'NICE_TO_HAVE': _safe_json_list(row.get('nice_to_have')),
        'UNKNOWN': _safe_json_list(row.get('unknown')),
        'EXCLUSION': _safe_json_list(row.get('exclusion')),
    }
    return {'ready': True, 'requirement_snapshot': snapshot}


def _safe_json_list(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


# ── 15.1 節：requirement_snapshot 裡，每個 MATCH_DIMENSIONS 屬於哪一層（HARD/SOFT/NICE）──
#
# requirement_snapshot 的 HARD_MUST/SOFT_MUST/... 清單裡，每一項預期長這樣：
#   {"dimension": "EXPERIENCE", "detail": "5年以上營運管理經驗"}
# _dimension_tier() 從清單裡反查某個 dimension 屬於哪一層，屬於 EXCLUSION 或找不到
# 就回傳 None（代表這個維度沒有被特別要求，不參與 Hard Gate）。

def _dimension_tier(requirement_snapshot, dimension):
    for tier in ('HARD_MUST', 'SOFT_MUST', 'NICE_TO_HAVE'):
        for item in requirement_snapshot.get(tier, []):
            if isinstance(item, dict) and item.get('dimension') == dimension:
                return tier
            if isinstance(item, str) and item == dimension:
                return tier
    return None


# ── 15.8 節：False Positive Guard ──

def check_false_positive_risk(candidate_evidence):
    """檢查候選人的證據是不是主要只靠 keyword overlap，沒有 title/industry/function/
    seniority/career context 佐證。

    回傳 (risk: bool, reason: str)。

    判斷邏輯：看每個判定為 PASS 的維度，它的 evidence_quality 是不是
    'KEYWORD_ONLY'，或者它的 context_flags 五項是不是全部是 False/缺失。
    只要有任何一個 PASS 維度落入這種情況，就算風險成立——因為這代表這個 PASS
    是靠不住的，很可能是 15.7 節講的「Keyword Hit ≠ Functional Fit」陷阱。
    """
    risky_dims = []
    for dim, ev in candidate_evidence.items():
        if dim == 'RECRUITABILITY' or not isinstance(ev, dict):
            continue
        if ev.get('verdict') != 'PASS':
            continue
        quality = ev.get('evidence_quality')
        flags = ev.get('context_flags') or {}
        has_context = any(flags.get(f) for f in CONTEXT_FLAGS)
        if quality == 'KEYWORD_ONLY' or not has_context:
            risky_dims.append(dim)

    if risky_dims:
        return True, (f'以下維度判 PASS 但只有 keyword overlap、沒有 title/industry/'
                       f'function/seniority/career context 佐證，可能是關鍵字誤中：'
                       f'{risky_dims}')
    return False, '所有 PASS 維度都至少有一種 context 佐證，未偵測到 keyword-only 風險。'


# ── 15.9 節：Minimum Evidence Rule ──

def _meets_minimum_evidence(candidate_evidence):
    """至少 2 個獨立專業證據來源，或 1 份標記為完整專業 profile 的證據，才算過關。"""
    all_sources = set()
    has_full_profile = False
    for dim, ev in candidate_evidence.items():
        if dim == 'RECRUITABILITY' or not isinstance(ev, dict):
            continue
        for s in (ev.get('sources') or []):
            all_sources.add(s)
        if ev.get('evidence_quality') == 'FULL_PROFILE':
            has_full_profile = True
    if has_full_profile:
        return True, f'有標記為完整專業 profile 的證據（獨立來源數：{len(all_sources)}）'
    if len(all_sources) >= MIN_INDEPENDENT_SOURCES:
        return True, f'獨立證據來源數 {len(all_sources)} >= {MIN_INDEPENDENT_SOURCES}'
    return False, f'獨立證據來源數只有 {len(all_sources)}，未滿足 Minimum Evidence Rule'


# ── 核心判定函式：對應 SOURCING_RULES.md 15.2-15.13 節 ──

def evaluate_candidate(candidate_evidence, requirement_snapshot):
    """核心判定函式。輸入候選人的證據物件與職缺的 requirement snapshot，
    輸出逐條 PASS/FAIL/UNKNOWN 判定表 + 最終 verdict。

    candidate_evidence 格式（每個 MATCH_DIMENSIONS 一筆，可以缺，缺的視為 UNKNOWN）：
        {
          "EXPERIENCE": {
              "verdict": "PASS" | "FAIL" | "UNKNOWN",
              "evidence_quality": "KEYWORD_ONLY" | "PARTIAL_CONTEXT" | "FULL_PROFILE",
              "context_flags": {"title_context": bool, "industry_context": bool,
                                 "function_context": bool, "seniority_context": bool,
                                 "career_context": bool},
              "sources": ["linkedin_profile_url", "company_team_page_url"],
              "notes": "具體證據或為什麼是 UNKNOWN"
          },
          ...,
          "RECRUITABILITY": {"level": "NORMAL"|"LOW"|"VERY_LOW"|"UNKNOWN", "notes": "..."}
        }

    這個函式**不自己判斷語意**（不會自己去讀履歷猜 PASS/FAIL）——那個判斷由
    呼叫端（AI agent 讀證據之後）先做出來，寫進 candidate_evidence。這個函式做的是
    SOURCING_RULES.md 15.2/15.9/15.12 節那套**不可違反的聚合規則**：
        1. 任一 HARD_MUST 維度是 FAIL → 最終一定是 NOT_MATCH，分數再高都一樣。
        2. 任一 HARD_MUST 維度是 UNKNOWN（且沒有 FAIL）→ 最終最高只能 INSUFFICIENT_DATA。
        3. 全部 HARD_MUST 都 PASS，但 Minimum Evidence Rule 沒過 → INSUFFICIENT_DATA。
        4. 全部 HARD_MUST 都 PASS，但 False Positive Guard 觸發 → 最高只能 POSSIBLE_MATCH
           （不准是 MATCH_CANDIDATE——存在關鍵字誤中風險就不能算「非常窄」的最終判定）。
        5. 全部 HARD_MUST PASS + Evidence 足夠 + 無 FP 風險，但有 SOFT_MUST FAIL/UNKNOWN
           → POSSIBLE_MATCH。
        6. 全部 HARD_MUST、SOFT_MUST 都 PASS → MATCH_CANDIDATE。

    RECRUITABILITY 完全不影響 QUALIFICATION 的 verdict（15.13 節），只是附帶回報，
    由呼叫端自己決定要不要因為 VERY_LOW 而不放進 outreach。

    ⚠️ 第 16 節 Adaptive Precision/Recall 交代：這個函式**不接受、也不需要**任何
    widening layer / discovery stage 的參數。候選人是從 WIDENING_SEQUENCE
    （EXACT_TITLE ... CAREER_PATH_SIMILARITY）第幾層搜尋找到的，跟這個候選人
    最終能不能判 MATCH_CANDIDATE 完全無關——「放寬搜尋，不放寬標準」在這裡的
    落地方式就是：搜尋層放寬只決定 Discovery 階段要不要繼續往下搜、要不要把這個
    候選人納入 POSSIBLE_CANDIDATE / SHORTLISTED，但一旦進了這個函式，判定邏輯
    跟第 15 節 Precision-First Match Guard 一模一樣，Hard Must 該擋還是擋。
    """
    dim_results = {}
    for dim in MATCH_DIMENSIONS:
        ev = candidate_evidence.get(dim)
        if not isinstance(ev, dict) or 'verdict' not in ev:
            dim_results[dim] = {'verdict': 'UNKNOWN', 'tier': _dimension_tier(requirement_snapshot, dim),
                                 'notes': '呼叫端未提供這個維度的判定，視為 UNKNOWN'}
            continue
        verdict = ev.get('verdict')
        if verdict not in DIMENSION_VERDICTS:
            raise ValueError(f'{dim} 的 verdict 必須是 {DIMENSION_VERDICTS} 之一，收到 {verdict!r}')
        dim_results[dim] = {
            'verdict': verdict,
            'tier': _dimension_tier(requirement_snapshot, dim),
            'evidence_quality': ev.get('evidence_quality'),
            'notes': ev.get('notes'),
        }

    recruitability_ev = candidate_evidence.get('RECRUITABILITY') or {}
    recruitability_level = recruitability_ev.get('level', 'UNKNOWN')
    if recruitability_level not in RECRUITABILITY_LEVELS:
        raise ValueError(f'RECRUITABILITY level 必須是 {RECRUITABILITY_LEVELS} 之一，'
                          f'收到 {recruitability_level!r}')

    hard_must_dims = [d for d in MATCH_DIMENSIONS if dim_results[d]['tier'] == 'HARD_MUST']
    soft_must_dims = [d for d in MATCH_DIMENSIONS if dim_results[d]['tier'] == 'SOFT_MUST']

    hard_fail = [d for d in hard_must_dims if dim_results[d]['verdict'] == 'FAIL']
    hard_unknown = [d for d in hard_must_dims if dim_results[d]['verdict'] == 'UNKNOWN']
    soft_fail_or_unknown = [d for d in soft_must_dims if dim_results[d]['verdict'] in ('FAIL', 'UNKNOWN')]

    fp_risk, fp_reason = check_false_positive_risk(candidate_evidence)
    evidence_ok, evidence_reason = _meets_minimum_evidence(candidate_evidence)

    # 分數只作為「同一個 verdict 等級內」的參考資訊，不參與 Gate 判斷——
    # 這是 15.12 節要求的：分數永遠不能覆蓋 Hard Fail，所以分數計算完全獨立在
    # verdict 判斷邏輯之外，先算完 verdict，分數只是附加輸出。
    score = sum(1 for d in MATCH_DIMENSIONS if dim_results[d]['verdict'] == 'PASS')
    score_pct = round(100 * score / len(MATCH_DIMENSIONS), 1)

    # ── 15.2 節：HARD MUST 是絕對 Gate，這一段是整支程式最重要的邏輯 ──
    if hard_fail:
        final_verdict = 'NOT_MATCH'
        reason = (f'HARD_MUST 維度 {hard_fail} 判定為 FAIL——依 SOURCING_RULES.md 15.2/15.12 節，'
                  f'任一 Hard Must Fail，不管分數多高（本例 score_pct={score_pct}），一律 NOT_MATCH，'
                  f'分數補不回來。')
    elif hard_unknown:
        final_verdict = 'INSUFFICIENT_DATA'
        reason = (f'HARD_MUST 維度 {hard_unknown} 判定為 UNKNOWN（沒有 FAIL）——依 15.2/15.10 節，'
                  f'任一 Hard Must 是 UNKNOWN，最高只能 INSUFFICIENT_DATA，不能是 MATCH_CANDIDATE，'
                  f'即使其他維度都 PASS（本例 score_pct={score_pct}）。')
    elif not evidence_ok:
        final_verdict = 'INSUFFICIENT_DATA'
        reason = f'所有 HARD_MUST 都 PASS，但 Minimum Evidence Rule 未過關：{evidence_reason}'
    elif fp_risk:
        final_verdict = 'POSSIBLE_MATCH'
        reason = f'所有 HARD_MUST 都 PASS，但 False Positive Guard 觸發，最高只能 POSSIBLE_MATCH：{fp_reason}'
    elif soft_fail_or_unknown:
        final_verdict = 'POSSIBLE_MATCH'
        reason = f'HARD_MUST 全過，但 SOFT_MUST 維度 {soft_fail_or_unknown} 未全過，判 POSSIBLE_MATCH。'
    else:
        final_verdict = 'MATCH_CANDIDATE'
        reason = 'HARD_MUST、SOFT_MUST 均 PASS，Evidence 足夠，未觸發 False Positive Guard。'

    return {
        'dimension_results': dim_results,
        'recruitability': {'level': recruitability_level, 'notes': recruitability_ev.get('notes')},
        'hard_must_fail': hard_fail,
        'hard_must_unknown': hard_unknown,
        'soft_must_fail_or_unknown': soft_fail_or_unknown,
        'false_positive_risk': fp_risk,
        'false_positive_reason': fp_reason,
        'minimum_evidence_met': evidence_ok,
        'minimum_evidence_reason': evidence_reason,
        'score': score,
        'score_pct': score_pct,
        'final_verdict': final_verdict,
        'verdict_reason': reason,
    }


# ── 15.17 節：False Positive Audit（事後稽核）──

def run_false_positive_audit(sourcing_run_id, sample_size=AUDIT_SAMPLE_SIZE):
    """從 candidate_job_match 抽出該 run 底下 MATCH_CANDIDATE / POSSIBLE_MATCH 的紀錄，
    隨機取最多 sample_size 筆（不足就全取），回傳複核清單結構。

    ⚠️ 這裡只負責「抽樣」，不負責「重新判斷」——重新判斷要靠人或另一個獨立的
    evaluate_candidate() 呼叫（用 candidate 的原始證據重跑一次，不能參考原本的
    verdict，避免確認偏誤），那一步留給呼叫端做，因為需要候選人的完整原始證據
    物件，這支模組不保存那個。
    """
    ensure_schema()
    rows = d1(f"""SELECT id, candidate_id, job_slug, match_status, match_score,
                  matching_version, matched_at FROM candidate_job_match
                  WHERE sourcing_run_id={q(sourcing_run_id)}
                  AND match_status IN ('MATCH_CANDIDATE', 'POSSIBLE_MATCH')""")
    if not rows:
        return {'sourcing_run_id': sourcing_run_id, 'sample': [], 'total_eligible': 0,
                'note': '這個 run 底下沒有 MATCH_CANDIDATE/POSSIBLE_MATCH 紀錄可抽查'
                        '（可能是 candidate_job_match 還沒補上 sourcing_run_id 外鍵，'
                        '或這個 run 真的還沒有判定結果）。'}
    sample = random.sample(rows, min(sample_size, len(rows)))
    return {'sourcing_run_id': sourcing_run_id, 'sample': sample, 'total_eligible': len(rows)}


def record_precision_alert(sourcing_run_id, sample_size, overturned_count, notes=''):
    """呼叫端做完重新判斷之後，把「幾個被推翻」回報進來，這裡算比例、決定要不要
    觸發 PRECISION_ALERT，並寫回 sourcing_runs.precision_alert（15.17 節）。
    """
    ensure_schema()
    rate = (overturned_count / sample_size) if sample_size else 0.0
    alert = rate > AUDIT_OVERTURN_RATE_ALERT
    payload = {
        'checked_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sample_size': sample_size,
        'overturned_count': overturned_count,
        'overturn_rate': round(rate, 4),
        'status': 'PRECISION_ALERT' if alert else 'PASSED',
        'notes': notes,
    }
    d1(f"UPDATE sourcing_runs SET precision_alert={q(json.dumps(payload, ensure_ascii=False))} "
       f"WHERE sourcing_run_id={q(sourcing_run_id)}")
    if alert:
        log(f'🚨 {sourcing_run_id} False Positive Audit 推翻率 {round(rate * 100, 1)}% > 20%，'
            f'觸發 PRECISION_ALERT，此 run 名單不得直接進 Outreach。')
    else:
        log(f'✅ {sourcing_run_id} False Positive Audit 推翻率 {round(rate * 100, 1)}% <= 20%，通過。')
    return payload


# ── 15.16 節：Calibration Set（校準測試集）──

def run_calibration_check(job_slug, good_match_cases, clear_not_match_cases):
    """跑校準測試。good_match_cases / clear_not_match_cases 是 list[candidate_evidence dict]。

    通過條件：
      - 每個 GOOD MATCH 案例的 final_verdict 不是 NOT_MATCH（理想是 MATCH_CANDIDATE，
        但 SOURCING_RULES.md 15.16 節允許「至少不是 NOT_MATCH」，因為證據給得不夠完整
        時判成 POSSIBLE_MATCH/INSUFFICIENT_DATA 也算邏輯正確，只是資料不夠）
      - 每個 CLEAR NOT MATCH 案例的 final_verdict 絕對不能是 MATCH_CANDIDATE

    全部通過才回傳可以開始大量 sourcing 的許可；否則回傳 MATCHING_CALIBRATION_FAILED
    跟哪幾個案例判錯，讓呼叫端知道要修哪裡。
    """
    ready = check_requirement_ready(job_slug)
    if not ready['ready']:
        return {'passed': False, 'code': ready['code'], 'reason': ready['reason']}
    requirement_snapshot = ready['requirement_snapshot']

    failed_cases = []
    good_results = []
    for i, case in enumerate(good_match_cases):
        result = evaluate_candidate(case, requirement_snapshot)
        good_results.append(result)
        if result['final_verdict'] == 'NOT_MATCH':
            failed_cases.append({
                'set': 'GOOD_MATCH', 'index': i, 'got': result['final_verdict'],
                'expected': '非 NOT_MATCH', 'reason': result['verdict_reason'],
            })

    bad_results = []
    for i, case in enumerate(clear_not_match_cases):
        result = evaluate_candidate(case, requirement_snapshot)
        bad_results.append(result)
        if result['final_verdict'] == 'MATCH_CANDIDATE':
            failed_cases.append({
                'set': 'CLEAR_NOT_MATCH', 'index': i, 'got': result['final_verdict'],
                'expected': '非 MATCH_CANDIDATE', 'reason': result['verdict_reason'],
            })

    passed = len(failed_cases) == 0
    return {
        'passed': passed,
        'code': None if passed else 'MATCHING_CALIBRATION_FAILED',
        'job_slug': job_slug,
        'good_match_results': [r['final_verdict'] for r in good_results],
        'clear_not_match_results': [r['final_verdict'] for r in bad_results],
        'failed_cases': failed_cases,
        'permission': 'ALLOWED_TO_SCALE_SOURCING' if passed else 'BLOCKED',
    }


# ── CLI（手動測試用）──

def main():
    ap = argparse.ArgumentParser(description='Step1ne 精準配對防呆引擎')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('check-ready')
    p.add_argument('--job', required=True)

    p = sub.add_parser('split')
    p.add_argument('--job', required=True)

    p = sub.add_parser('evaluate')
    p.add_argument('--job', required=True)
    p.add_argument('--evidence-json', required=True, help='candidate_evidence 的 JSON 檔路徑')

    p = sub.add_parser('audit')
    p.add_argument('--run', required=True)

    a = ap.parse_args()

    if a.cmd == 'check-ready':
        print(json.dumps(check_requirement_ready(a.job), ensure_ascii=False, indent=2))
    elif a.cmd == 'split':
        print(json.dumps(split_requirements(a.job), ensure_ascii=False, indent=2))
    elif a.cmd == 'evaluate':
        with open(a.evidence_json, 'r', encoding='utf-8') as f:
            evidence = json.load(f)
        ready = check_requirement_ready(a.job)
        if not ready['ready']:
            print(json.dumps(ready, ensure_ascii=False, indent=2))
            sys.exit(1)
        result = evaluate_candidate(evidence, ready['requirement_snapshot'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif a.cmd == 'audit':
        print(json.dumps(run_false_positive_audit(a.run), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
