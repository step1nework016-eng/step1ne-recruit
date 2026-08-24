#!/usr/bin/env python3
"""Step1ne 免費候選人搜尋規則引擎 —— 把 SOURCING_RULES.md 的規則變成可呼叫的檢查函式。

為什麼要有這支（2026-08-23 Jacky 交辦）：
    之前幾輪 sourcing pilot（Phase 2.3 隨機搜尋、Phase 2.5 Shadow Recruiter）
    都存在一個問題：AI 搜第一頁沒有就說找不到。光靠寫在文件裡的規則沒有強制力，
    AI agent 讀完 SOURCING_RULES.md 還是可能在執行時偷懶跳過。

    這支模組把規則變成**程式檢查**——尤其是 `finish_run()`，如果
    `check_search_incomplete()` 還沒過就硬要結案，直接拋例外擋下來，
    不讓 agent 用「我覺得應該搜完了」這種主觀判斷跳過檢查。

這一輪**不做任何真實的候選人搜尋**。這支只是基礎設施：讓未來真的要做
sourcing 的 agent，能用這幾個函式記錄過程、自我檢查、避免偷懶。

用法（CLI，主要給人工測試用，正式流程是被其他 sourcing agent import）：
    python3 sourcing_engine.py new-run --job "Business Operations Manager" --slug BOM
    python3 sourcing_engine.py check-incomplete --run STEP1NE-BOM-20260823-001
    python3 sourcing_engine.py yields --run STEP1NE-BOM-20260823-001
"""
import argparse
import datetime
import json
import os
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
    """帶重試的 d1() 包裝。

    D1 常見兩種暫時性錯誤：7429（rate limit / timeout）、7009（不可用）。
    這兩種都值得重試 2-3 次；其他錯誤（SQL 語法錯之類）重試沒意義，直接往外拋。
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


# ── 常數：對應 SOURCING_RULES.md 第 3 節、第 7 節、第 11 節 ──

QUERY_FAMILIES = ['EXACT_TITLES', 'ADJACENT_TITLES', 'SKILL_RESPONSIBILITY', 'TARGET_COMPANY']

RESULT_CLASSIFICATIONS = [
    'CANDIDATE', 'POSSIBLE_CANDIDATE', 'COMPANY', 'JOB_PAGE', 'NEWS',
    'ASSOCIATION', 'CONFERENCE', 'NOISE', 'DUPLICATE', 'AUTH_REQUIRED',
]

SOURCE_STRATEGY_BY_JOB_TYPE = {
    'software': ['GitHub', 'Google', 'LINKEDIN_PUBLIC_DISCOVERY', 'Cake public discovery'],
    'product_marketing': ['Cake public discovery', 'Google', 'LINKEDIN_PUBLIC_DISCOVERY'],
    'executive_operations_finance': ['Google', 'LINKEDIN_PUBLIC_DISCOVERY', '公司官網', '新聞', '研討會', '協會'],
    'customer_service_admin': ['inbound applications', 'social', 'public job/candidate sources'],
}
# ⚠️ 上面每一組清單都只含免費/可用來源，刻意不含任何付費來源——
# SOURCING_RULES.md 第 11 節（2026-08-23 政策修正）：付費來源是加速器不是氧氣，
# 不開放在這份「開搜起點」清單裡當必要項，避免有人把付費來源當成前置條件寫進 Source Strategy。

# SOURCING_RULES.md 第 11.4 節：付費來源分級表——三者全部是 OPTIONAL_PAID_SOURCE，
# 沒有任何一個是 REQUIRED_SOURCE。沒有付費帳號，不得阻塞 sourcing 的任何一個環節。
PAID_SOURCES = {
    'LINKEDIN_RECRUITER_PAID': 'OPTIONAL_PAID_SOURCE',
    '104_PAID_RESUME_DATABASE': 'OPTIONAL_PAID_SOURCE',
    'CAKE_PAID_TALENT_SEARCH': 'OPTIONAL_PAID_SOURCE',
}

# SOURCING_RULES.md 第 11.2 節：LinkedIn 要分兩種來源，不准混在一起。
# LINKEDIN_PUBLIC_DISCOVERY 是免費、正常可用的來源（透過 Google X-Ray 進去的
# public snippet），LINKEDIN_RECRUITER_PAID 才是 optional 付費帳號。
LINKEDIN_SOURCE_TYPES = ['LINKEDIN_PUBLIC_DISCOVERY', 'LINKEDIN_RECRUITER_PAID']

# SOURCING_RULES.md 第 11.3 節：104 也分兩種來源。104_PUBLIC_JOB_INTELLIGENCE 是
# 免費的市場情報用途（target company mapping、competitor hiring signals，
# 不涉及爬候選人履歷），104_PAID_RESUME_DATABASE 才是 optional 付費履歷搜尋。
# （104 開頭不是合法 Python 識別字，所以變數名用 HR104_ 開頭，內容字串本身不受此限制。）
HR104_SOURCE_TYPES = ['104_PUBLIC_JOB_INTELLIGENCE', '104_PAID_RESUME_DATABASE']

# SOURCING_RULES.md 第 11.5 節：正確的執行狀態列舉，取代舊的 BLOCKED_BY_SOURCE_ACCESS /
# SOURCE_ACCESS_BLOCKED 邏輯——沒有付費帳號不等於 BLOCKED，只是 SOURCE_PAID_OPTIONAL。
SOURCE_STATES = [
    'SOURCE_READY_FREE', 'SOURCE_PARTIAL', 'SOURCE_AUTH_REQUIRED',
    'SOURCE_PAID_OPTIONAL', 'SEARCH_SATURATED', 'CONSIDER_PAID_SOURCE',
]

# SOURCING_RULES.md 第 8.1 節：這五項全部成立才不算 SEARCH_INCOMPLETE
MIN_QUERY_FAMILY_COUNT = 4
MIN_QUERY_VARIANT_COUNT = 12
MIN_SOURCE_TYPE_COUNT = 4

# SOURCING_RULES.md 第 8.2 節：連續幾組「零收穫」才算飽和
SATURATION_STREAK = 3

VALID_FINISH_STATUS = {'SEARCH_SATURATED', 'SEARCH_INCOMPLETE'}

# SOURCING_RULES.md 第 11.6 節：Source Escalation 門檻——
# evidence depth（證據充分候選人 / 發現候選人）低於這個比例，才算「證據不足」；
# match yield（Match 候選人 / 發現候選人）低於這個比例，才算「產出太低」。
# 兩個門檻都刻意設得寬鬆（0.3 / 0.15），因為 CONSIDER_PAID_SOURCE 是三個條件
# 同時成立才能觸發的高門檻建議，不是隨便一個數字沒到就能建議花錢。
MIN_EVIDENCE_DEPTH_RATIO = 0.3
MIN_MATCH_YIELD_RATIO = 0.15


# ── run_id ──

def new_run_id(job_slug):
    """產生符合 STEP1NE-{JOB_SLUG}-{YYYYMMDD}-{三位數序號} 格式的 run_id。

    序號怎麼決定：查 D1 這個 job_slug 今天已經有幾筆 sourcing_runs，
    不是用亂數，是為了讓同一天同一職缺重跑第二次時序號能自然遞增，
    方便日後排查「這是今天第幾次搜這個缺」。
    """
    job_slug = job_slug.strip().upper()
    today = datetime.datetime.now().strftime('%Y%m%d')
    prefix = f'STEP1NE-{job_slug}-{today}-'
    rows = d1(f"SELECT sourcing_run_id FROM sourcing_runs "
              f"WHERE sourcing_run_id LIKE {q(prefix + '%')}")
    existing_seqs = []
    for r in rows:
        rid = r['sourcing_run_id']
        tail = rid[len(prefix):]
        if tail.isdigit():
            existing_seqs.append(int(tail))
    seq = (max(existing_seqs) + 1) if existing_seqs else 1
    return f'{prefix}{seq:03d}'


# ── run 生命週期 ──

def start_run(run_id, target_job, search_strategy_version):
    """在 sourcing_runs 插入一筆 started 狀態的紀錄。"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1(f"""INSERT INTO sourcing_runs
           (sourcing_run_id, target_job, started_at, finished_at, search_strategy_version,
            query_family_count, query_variant_count, source_type_count,
            candidate_chain_attempted, diminishing_return_condition, status,
            candidates_discovered_count, evidence_sufficient_count, match_candidate_count,
            stop_reason, notes)
           VALUES ({q(run_id)}, {q(target_job)}, {q(now)}, NULL, {q(search_strategy_version)},
           0, 0, 0, 0, 0, 'STARTED', 0, 0, 0, NULL, NULL)""")
    log(f'✅ 開始 sourcing run {run_id}（{target_job}）')
    return run_id


def log_query(run_id, query, query_family, result_depth, results_checked,
              candidates_discovered, duplicates, noise, auth_required, new_evidence):
    """寫一筆 search_audit_log，回傳這筆的 id。

    candidates_discovered / new_evidence 是 list，存進去之前轉成 JSON 字串
    ——SOURCING_RULES.md 第 8.3 節要求這兩欄要是可回放的逐條清單，不是總數。
    """
    if query_family not in QUERY_FAMILIES:
        raise ValueError(f'query_family 必須是 {QUERY_FAMILIES} 其中之一，收到 {query_family!r}')

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    candidates_json = json.dumps(candidates_discovered or [], ensure_ascii=False)
    evidence_json = json.dumps(new_evidence or [], ensure_ascii=False)

    d1(f"""INSERT INTO search_audit_log
           (id, sourcing_run_id, query, query_family, result_depth, results_checked,
            candidates_discovered, duplicates, noise, auth_required, new_evidence,
            started_at, finished_at)
           VALUES (lower(hex(randomblob(8))), {q(run_id)}, {q(query)}, {q(query_family)},
           {int(result_depth)}, {int(results_checked)}, {q(candidates_json)},
           {int(duplicates)}, {int(noise)}, {int(auth_required)}, {q(evidence_json)},
           {q(now)}, {q(now)})""")

    # 拿剛寫入那筆的 id（用 query+started_at 反查，因為 D1 沒有 RETURNING）
    rows = d1(f"SELECT id FROM search_audit_log WHERE sourcing_run_id={q(run_id)} "
              f"AND query={q(query)} AND started_at={q(now)} "
              f"ORDER BY rowid DESC LIMIT 1")
    new_id = rows[0]['id'] if rows else None

    # 同步累加 sourcing_runs 的統計欄位，方便 check_search_incomplete 直接讀
    _refresh_run_counts(run_id)
    log(f'　記錄 query「{query}」（{query_family}）→ 看了 {results_checked} 筆，'
        f'新候選人 {len(candidates_discovered or [])} 個')
    return new_id


def _refresh_run_counts(run_id):
    """依 search_audit_log 目前累積的內容，重算 sourcing_runs 的統計欄位。

    ⚠️ 這裡刻意**不碰** source_type_count。來源類型是「用了哪一類來源」，
    不是從 query/candidates 資料反推得出來的東西，硬要從 new_evidence 猜
    容易猜錯又會跟 mark_source_type() 的明確登記互相打架（這支函式每記一組
    query 就會呼叫一次，如果同時重算 source_type_count，會把 mark_source_type
    已經登記好的數字蓋掉）。source_type_count 只能透過 mark_source_type() 更新。
    """
    logs = d1(f"SELECT * FROM search_audit_log WHERE sourcing_run_id={q(run_id)}")
    query_variant_count = len(logs)
    query_family_count = len({l['query_family'] for l in logs if l.get('query_family')})
    candidates_discovered_count = sum(len(_safe_json_list(l.get('candidates_discovered'))) for l in logs)

    d1(f"""UPDATE sourcing_runs SET
           query_variant_count={query_variant_count},
           query_family_count={query_family_count},
           candidates_discovered_count={candidates_discovered_count}
           WHERE sourcing_run_id={q(run_id)}""")


def _safe_json_list(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def mark_candidate_chain_attempted(run_id):
    """標記這個 run 已經做過 candidate/company chain search（第 2.4 節）。"""
    d1(f"UPDATE sourcing_runs SET candidate_chain_attempted=1 WHERE sourcing_run_id={q(run_id)}")
    log(f'✅ {run_id} 已標記做過 candidate chain search')


def mark_source_type(run_id, source_type):
    """明確登記這個 run 用過某一類來源（比 _refresh_run_counts 的推斷更可靠，建議 sourcing agent 直接呼叫這個）。"""
    row = d1(f"SELECT notes FROM sourcing_runs WHERE sourcing_run_id={q(run_id)}")
    if not row:
        raise ValueError(f'找不到 run {run_id}')
    notes = row[0].get('notes') or '{}'
    try:
        meta = json.loads(notes)
    except (TypeError, ValueError):
        meta = {}
    used = set(meta.get('source_types_used', []))
    used.add(source_type)
    meta['source_types_used'] = sorted(used)
    d1(f"""UPDATE sourcing_runs SET notes={q(json.dumps(meta, ensure_ascii=False))},
           source_type_count={len(used)} WHERE sourcing_run_id={q(run_id)}""")
    log(f'　{run_id} 登記使用來源類型：{source_type}（累計 {len(used)} 類）')


# ── 核心防偷懶檢查：對應 SOURCING_RULES.md 第 8 節 ──

def check_search_incomplete(run_id):
    """回傳 (is_incomplete: bool, missing: list[str])。

    這是規格第 22 項的核心檢查邏輯——五個條件缺哪幾項要列出來，
    不是只回一個 True/False 讓人猜。
    """
    rows = d1(f"SELECT * FROM sourcing_runs WHERE sourcing_run_id={q(run_id)}")
    if not rows:
        raise ValueError(f'找不到 sourcing run {run_id}')
    run = rows[0]

    missing = []
    if (run.get('query_family_count') or 0) < MIN_QUERY_FAMILY_COUNT:
        missing.append(f"query_family_count={run.get('query_family_count') or 0} "
                        f"< {MIN_QUERY_FAMILY_COUNT}（四個 Query Family 沒用滿）")
    if (run.get('query_variant_count') or 0) < MIN_QUERY_VARIANT_COUNT:
        missing.append(f"query_variant_count={run.get('query_variant_count') or 0} "
                        f"< {MIN_QUERY_VARIANT_COUNT}（總查詢數不足 12 組）")
    if (run.get('source_type_count') or 0) < MIN_SOURCE_TYPE_COUNT:
        missing.append(f"source_type_count={run.get('source_type_count') or 0} "
                        f"< {MIN_SOURCE_TYPE_COUNT}（來源類型不足 4 類）")
    if not run.get('candidate_chain_attempted'):
        missing.append('candidate_chain_attempted=false（還沒做 seed expansion）')
    if not run.get('diminishing_return_condition'):
        # diminishing_return_condition 由 check_search_saturated 判斷後寫回，
        # 這裡不重新計算，只讀已經記錄的狀態——避免兩個函式邏輯重複、互相打架。
        missing.append('diminishing_return_condition=false（尚未確認邊際效益遞減，'
                        '要先呼叫 check_search_saturated 判斷）')

    is_incomplete = len(missing) > 0
    return is_incomplete, missing


def check_search_saturated(run_id):
    """檢查最近 3 組 query variant 是否都是「0 新候選人 + 0 新證據 + 0 新來源」。

    符合就回傳 True，並把 sourcing_runs.diminishing_return_condition 標成 1
    ——這樣 check_search_incomplete 才讀得到最新狀態。
    """
    logs = d1(f"SELECT * FROM search_audit_log WHERE sourcing_run_id={q(run_id)} "
              f"ORDER BY rowid ASC")
    if len(logs) < SATURATION_STREAK:
        log(f'　{run_id} 目前只有 {len(logs)} 組 query，不足 {SATURATION_STREAK} 組，無法判斷飽和')
        return False

    last_n = logs[-SATURATION_STREAK:]
    saturated = True
    for l in last_n:
        new_candidates = len(_safe_json_list(l.get('candidates_discovered')))
        new_evidence = len(_safe_json_list(l.get('new_evidence')))
        # 「0 新來源」在單筆 log 層級近似為 new_evidence 也是 0
        # （新來源必然帶著新證據，見 SOURCING_RULES.md 第 8.3 節的 log 結構）
        if new_candidates > 0 or new_evidence > 0:
            saturated = False
            break

    if saturated:
        d1(f"UPDATE sourcing_runs SET diminishing_return_condition=1 WHERE sourcing_run_id={q(run_id)}")
        log(f'✅ {run_id} 連續 {SATURATION_STREAK} 組零收穫，判定 SEARCH_SATURATED 條件成立')
    else:
        log(f'　{run_id} 最近 {SATURATION_STREAK} 組還有收穫，尚未飽和')
    return saturated


def check_consider_paid_source(sourcing_run_id):
    """依 SOURCING_RULES.md 第 11.6/11.7 節的 Source Escalation 規則，判斷這個
    sourcing run 現在能不能建議 `CONSIDER_PAID_SOURCE`。

    ⚠️ 這個函式的回傳絕對不能只因為「沒開通付費帳號」就建議 CONSIDER_PAID_SOURCE
    ——這支函式根本不檢查、也不知道任何付費帳號的開通狀態，它只讀這個 run 的
    實際執行數據。三個條件必須**同時成立**才回傳 CONSIDER_PAID_SOURCE：

      1. SEARCH_SATURATED（呼叫 check_search_saturated()，連續 3 組 query 零收穫）
      2. evidence depth 不足（evidence_sufficient_count / candidates_discovered_count
         低於 MIN_EVIDENCE_DEPTH_RATIO，或根本沒發現任何候選人）
      3. match yield 太低（match_candidate_count / candidates_discovered_count
         低於 MIN_MATCH_YIELD_RATIO）

    只要缺一項，回傳 SOURCE_READY_FREE——意思是免費來源還沒用盡，不該考慮付費。
    回傳的 CONSIDER_PAID_SOURCE 一定附 roi_evidence 文字說明（第 11.7 節要求）：
    哪個職缺、搜了多少組 query、缺什麼資料、付費來源理論上能補什麼。
    """
    rows = d1(f"SELECT * FROM sourcing_runs WHERE sourcing_run_id={q(sourcing_run_id)}")
    if not rows:
        raise ValueError(f'找不到 sourcing run {sourcing_run_id}')
    run = rows[0]

    saturated = check_search_saturated(sourcing_run_id)

    candidates = run.get('candidates_discovered_count') or 0
    evidence_sufficient = run.get('evidence_sufficient_count') or 0
    match_count = run.get('match_candidate_count') or 0
    query_count = run.get('query_variant_count') or 0
    target_job = run.get('target_job')

    evidence_ratio = (evidence_sufficient / candidates) if candidates else 0.0
    match_yield = (match_count / candidates) if candidates else 0.0

    evidence_insufficient = (candidates == 0) or (evidence_ratio < MIN_EVIDENCE_DEPTH_RATIO)
    yield_low = match_yield < MIN_MATCH_YIELD_RATIO

    detail = {
        'sourcing_run_id': sourcing_run_id, 'target_job': target_job,
        'saturated': saturated, 'query_variant_count': query_count,
        'candidates_discovered_count': candidates,
        'evidence_sufficient_count': evidence_sufficient,
        'evidence_depth_ratio': round(evidence_ratio, 4),
        'match_candidate_count': match_count,
        'match_yield_ratio': round(match_yield, 4),
        'evidence_insufficient': evidence_insufficient,
        'yield_low': yield_low,
    }

    if saturated and evidence_insufficient and yield_low:
        roi_evidence = (
            f'職缺「{target_job}」（run {sourcing_run_id}）：免費來源已搜 {query_count} 組 query、'
            f'達到 SEARCH_SATURATED（連續 {SATURATION_STREAK} 組零收穫）。共發現 {candidates} 位候選人，'
            f'其中證據充分的只有 {evidence_sufficient} 位（{round(evidence_ratio * 100, 1)}%，'
            f'低於門檻 {int(MIN_EVIDENCE_DEPTH_RATIO * 100)}%），最終判定 Match 只有 {match_count} 位'
            f'（yield {round(match_yield * 100, 1)}%，低於門檻 {int(MIN_MATCH_YIELD_RATIO * 100)}%）。'
            f'缺的是「完整經歷佐證」與「主動聯繫管道」——理論上 LinkedIn Recruiter Lite 能看到完整 '
            f'profile 而不只是 public snippet，104 企業版履歷搜尋能主動搜到未投遞但公開履歷的在職者，'
            f'可以補上這兩塊缺口。基於以上三項同時成立的實際執行數據，建議 CONSIDER_PAID_SOURCE。'
        )
        detail['status'] = 'CONSIDER_PAID_SOURCE'
        detail['roi_evidence'] = roi_evidence
        log(f'⚠️ {sourcing_run_id} 三項條件同時成立，建議 CONSIDER_PAID_SOURCE')
        return detail

    reasons_not_met = []
    if not saturated:
        reasons_not_met.append('尚未 SEARCH_SATURATED（免費來源還沒搜完，continue sourcing）')
    if not evidence_insufficient:
        reasons_not_met.append(f'evidence depth 沒有真的不足（{round(evidence_ratio * 100, 1)}% '
                                f'>= 門檻 {int(MIN_EVIDENCE_DEPTH_RATIO * 100)}%）')
    if not yield_low:
        reasons_not_met.append(f'match yield 沒有真的太低（{round(match_yield * 100, 1)}% '
                                f'>= 門檻 {int(MIN_MATCH_YIELD_RATIO * 100)}%）')
    detail['status'] = 'SOURCE_READY_FREE'
    detail['reason'] = '免費來源尚未用盡，不該考慮付費：' + '；'.join(reasons_not_met)
    log(f'　{sourcing_run_id} 免費來源尚未用盡，維持 SOURCE_READY_FREE')
    return detail


def compute_yields(run_id):
    """讀該 run 的 audit log，依 query_family 分組計算 Discovery / Evidence / Match Yield。

    回傳結構化結果，方便回答 SOURCING_RULES.md 第 14 節要求的
    「哪個 Search Strategy Yield 最高」。
    """
    logs = d1(f"SELECT * FROM search_audit_log WHERE sourcing_run_id={q(run_id)}")
    by_family = {fam: {'queries': 0, 'results_checked': 0, 'candidates_discovered': 0,
                        'duplicates': 0, 'noise': 0, 'auth_required': 0, 'new_evidence': 0}
                 for fam in QUERY_FAMILIES}
    for l in logs:
        fam = l.get('query_family')
        if fam not in by_family:
            continue
        by_family[fam]['queries'] += 1
        by_family[fam]['results_checked'] += l.get('results_checked') or 0
        by_family[fam]['candidates_discovered'] += len(_safe_json_list(l.get('candidates_discovered')))
        by_family[fam]['duplicates'] += l.get('duplicates') or 0
        by_family[fam]['noise'] += l.get('noise') or 0
        by_family[fam]['auth_required'] += l.get('auth_required') or 0
        by_family[fam]['new_evidence'] += len(_safe_json_list(l.get('new_evidence')))

    for fam, stats in by_family.items():
        checked = stats['results_checked']
        stats['discovery_yield'] = round(stats['candidates_discovered'] / checked, 4) if checked else 0.0
        stats['evidence_yield'] = round(stats['new_evidence'] / checked, 4) if checked else 0.0

    best_family = max(by_family, key=lambda f: by_family[f]['discovery_yield']) if logs else None
    return {'run_id': run_id, 'by_query_family': by_family, 'highest_yield_family': best_family}


def finish_run(run_id, status, stop_reason):
    """更新 sourcing_runs 的 finished_at/status/stop_reason。

    status 只能是 SEARCH_SATURATED 或 SEARCH_INCOMPLETE。

    如果呼叫端想標 SEARCH_SATURATED，但 check_search_incomplete() 仍然是 True，
    這裡要拒絕並拋出例外——不能讓 agent 偷偷跳過檢查直接結案。
    這是整支模組裡唯一「會主動擋人」的地方，其他函式都只記錄／回報，
    只有 finish_run 有權力擋下不合格的結案。
    """
    if status not in VALID_FINISH_STATUS:
        raise ValueError(f'status 必須是 {VALID_FINISH_STATUS} 其中之一，收到 {status!r}')

    if status == 'SEARCH_SATURATED':
        is_incomplete, missing = check_search_incomplete(run_id)
        if is_incomplete:
            raise RuntimeError(
                f'拒絕將 {run_id} 標記為 SEARCH_SATURATED——check_search_incomplete 仍然是 True，'
                f'缺：{missing}。請先補齊這些項目，或誠實把 status 標成 SEARCH_INCOMPLETE。'
            )

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = d1(f"SELECT * FROM sourcing_runs WHERE sourcing_run_id={q(run_id)}")
    run = rows[0] if rows else {}
    evidence_sufficient_count = run.get('evidence_sufficient_count') or 0
    match_candidate_count = run.get('match_candidate_count') or 0

    d1(f"""UPDATE sourcing_runs SET finished_at={q(now)}, status={q(status)},
           stop_reason={q(stop_reason)} WHERE sourcing_run_id={q(run_id)}""")
    log(f'{"✅" if status == "SEARCH_SATURATED" else "⚠️"} {run_id} 結案：{status}（{stop_reason}）')
    return status


# ── CLI（手動測試用）──

def main():
    ap = argparse.ArgumentParser(description='Step1ne 候選人搜尋規則引擎')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('new-run')
    p.add_argument('--job', required=True)
    p.add_argument('--slug', required=True)
    p.add_argument('--strategy-version', default='v1')

    p = sub.add_parser('log-query')
    p.add_argument('--run', required=True)
    p.add_argument('--query', required=True)
    p.add_argument('--family', required=True, choices=QUERY_FAMILIES)
    p.add_argument('--depth', type=int, default=1)
    p.add_argument('--checked', type=int, default=0)
    p.add_argument('--candidates', default='[]', help='JSON list')
    p.add_argument('--duplicates', type=int, default=0)
    p.add_argument('--noise', type=int, default=0)
    p.add_argument('--auth-required', type=int, default=0)
    p.add_argument('--evidence', default='[]', help='JSON list')

    p = sub.add_parser('check-incomplete')
    p.add_argument('--run', required=True)

    p = sub.add_parser('check-saturated')
    p.add_argument('--run', required=True)

    p = sub.add_parser('yields')
    p.add_argument('--run', required=True)

    p = sub.add_parser('finish')
    p.add_argument('--run', required=True)
    p.add_argument('--status', required=True, choices=sorted(VALID_FINISH_STATUS))
    p.add_argument('--reason', required=True)

    p = sub.add_parser('check-paid-source')
    p.add_argument('--run', required=True)

    a = ap.parse_args()

    if a.cmd == 'new-run':
        run_id = new_run_id(a.slug)
        start_run(run_id, a.job, a.strategy_version)
        print(run_id)
    elif a.cmd == 'log-query':
        new_id = log_query(a.run, a.query, a.family, a.depth, a.checked,
                            json.loads(a.candidates), a.duplicates, a.noise,
                            a.auth_required, json.loads(a.evidence))
        print(new_id)
    elif a.cmd == 'check-incomplete':
        is_incomplete, missing = check_search_incomplete(a.run)
        print(json.dumps({'is_incomplete': is_incomplete, 'missing': missing},
                          ensure_ascii=False, indent=2))
    elif a.cmd == 'check-saturated':
        print(json.dumps({'saturated': check_search_saturated(a.run)}, ensure_ascii=False))
    elif a.cmd == 'yields':
        print(json.dumps(compute_yields(a.run), ensure_ascii=False, indent=2))
    elif a.cmd == 'finish':
        finish_run(a.run, a.status, a.reason)
    elif a.cmd == 'check-paid-source':
        print(json.dumps(check_consider_paid_source(a.run), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
