#!/usr/bin/env python3
"""Step1ne 獨立驗證引擎 —— 對應 VERIFICATION_RULES.md，把「做事的人不能自己說做完了」
變成可呼叫的檢查函式。

設計範本是 ~/aijob-ops/ops/verify.py（AIJOB「AI 老闆」系統已經在跑、已經驗證過真的有效
的獨立查證器）。三條黃金規則原封不動搬過來：

    1. 查證者不得是建造者本人——log_verification() 要求呼叫端傳入 verifier_run_id，
       用來跟被驗證那次執行的 run id 區分；這支程式不強制檢查兩者字串不同
       （因為執行端的 run id 命名各引擎不統一，程式層面攔不住冒名），
       但**流程上**必須由獨立啟動的 agent/session 呼叫，不能是原執行任務的同一個
       context 順手驗自己。這條是流程紀律，不是程式碼能單靠自己擋下來的。
    2. 查證器不准讀執行者自己寫的摘要/log/宣稱的結果當證據——下面每個 verify_* 函式
       只吃識別碼，自己重新去查 D1 原始表（search_audit_log／candidate_job_match／
       messages／jobs），不讀 sourcing_runs.notes 或任何「執行者說做完了」的欄位。
    3. 每個數字都要查得到原始來源，查不到就是 INSUFFICIENT_EVIDENCE，不准猜一個
       結論出來湊數。

VERIFICATION_RESULTS：
    VERIFIED               重新查了下游/原始資料，確認執行者的宣稱屬實
    VERIFY_FAILED           重新查了下游/原始資料，發現跟宣稱不一致
    INSUFFICIENT_EVIDENCE   查不到原始資料（表不存在、資料被清空、D1 連續逾時）

用法（CLI，主要給人工測試/獨立驗證 agent 用）：
    python3 verification_engine.py verify-sourcing --run STEP1NE-XXX-20260824-001
    python3 verification_engine.py verify-matching --match-id <candidate_job_match.id>
    python3 verification_engine.py verify-interview --application-id <applications.id>
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))

VERIFICATION_RESULTS = ['VERIFIED', 'VERIFY_FAILED', 'INSUFFICIENT_EVIDENCE']


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
    """帶重試的 d1() 包裝，抄 sourcing_engine.py／matching_engine.py 同一套邏輯：
    D1 常見兩種暫時性錯誤——7429（rate limit/timeout）、7009（不可用）——值得重試
    2-3 次；其他錯誤（SQL 語法錯之類）直接往外拋，重試沒意義。

    ⚠️ 這支包裝本身就是「規則 3」的體現：重試用盡還是失敗，呼叫端要老實回報
    INSUFFICIENT_EVIDENCE，不能假裝查到了。"""
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


def _table_exists(name):
    rows = d1(f"SELECT name FROM sqlite_master WHERE type='table' AND name={q(name)}")
    return len(rows) > 0


def ensure_verification_log_table():
    """先查有沒有同名表，沒有才 CREATE——CREATE TABLE IF NOT EXISTS 本身雖然是安全的，
    但先查一次可以在 CLI 輸出裡明確告訴使用者這是新建表還是既有表，避免誤以為
    這次跑出來的資料是舊資料。"""
    existed = _table_exists('verification_log')
    d1("""CREATE TABLE IF NOT EXISTS verification_log (
        id TEXT PRIMARY KEY,
        verified_at TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        verifier_run_id TEXT,
        result TEXT NOT NULL,
        evidence_checked TEXT,
        discrepancies_found TEXT,
        notes TEXT
    )""")
    if not existed:
        log('verification_log 表不存在，已新建。')
    return existed


def now_iso():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log_verification(target_type, target_id, verifier_run_id, result,
                     evidence_checked=None, discrepancies_found=None, notes=None):
    """寫入 verification_log。這是每一次獨立驗證的正式紀錄——查完就要記，
    不能查完丟掉。evidence_checked / discrepancies_found 存 JSON 字串。"""
    if result not in VERIFICATION_RESULTS:
        raise ValueError(f'result 必須是 {VERIFICATION_RESULTS} 之一，收到 {result!r}')
    ensure_verification_log_table()
    vid = str(uuid.uuid4())
    d1(f"""INSERT INTO verification_log
           (id, verified_at, target_type, target_id, verifier_run_id, result,
            evidence_checked, discrepancies_found, notes)
           VALUES ({q(vid)}, {q(now_iso())}, {q(target_type)}, {q(target_id)},
                   {q(verifier_run_id)}, {q(result)},
                   {q(json.dumps(evidence_checked, ensure_ascii=False) if evidence_checked is not None else None)},
                   {q(json.dumps(discrepancies_found, ensure_ascii=False) if discrepancies_found is not None else None)},
                   {q(notes)})""")
    return vid


# ────────────────────────────────────────────────────────────
# B-2：主動搜尋 —— verify_sourcing_run()
# ────────────────────────────────────────────────────────────

def verify_sourcing_run(sourcing_run_id, verifier_run_id='cli-manual'):
    """重新查 search_audit_log，確認 sourcing_runs 聲稱的 query_variant_count
    真的有對應的原始紀錄可回放。不讀 sourcing_runs.notes（那是執行者自己寫的摘要）。
    """
    runs = d1(f"SELECT * FROM sourcing_runs WHERE sourcing_run_id={q(sourcing_run_id)}")
    if not runs:
        vid = log_verification('sourcing_run', sourcing_run_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'sourcing_runs 查無此 run_id：{sourcing_run_id}')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': 'sourcing_runs 查無此 run_id'}
    run = runs[0]
    claimed = run.get('query_variant_count') or 0

    logs = d1(f"SELECT id, query, query_family, results_checked, candidates_discovered "
              f"FROM search_audit_log WHERE sourcing_run_id={q(sourcing_run_id)}")
    actual = len(logs)

    evidence_checked = {
        'sourcing_run_id': sourcing_run_id,
        'claimed_query_variant_count': claimed,
        'actual_search_audit_log_rows': actual,
        'query_family_count_claimed': run.get('query_family_count'),
        'sample_queries': [r.get('query') for r in logs[:5]],
    }

    if actual == 0 and claimed > 0:
        result = 'VERIFY_FAILED'
        reason = (f'sourcing_runs 聲稱跑了 {claimed} 組 query，但 search_audit_log '
                  f'完全查不到任何一筆對應紀錄——聲稱的搜尋動作沒有可回放的原始證據。')
    elif actual < claimed:
        result = 'VERIFY_FAILED'
        reason = (f'sourcing_runs 聲稱跑了 {claimed} 組 query，search_audit_log '
                  f'實際只查到 {actual} 筆——數字對不上，聲稱的搜尋量沒有全部留下紀錄。')
    elif actual > claimed:
        # 統計欄位落後於原始紀錄本身不是造假，但要標記出來讓呼叫端知道彙總欄位沒更新
        result = 'VERIFIED'
        reason = (f'search_audit_log 實際 {actual} 筆 ≥ 聲稱的 {claimed} 組——'
                  f'原始紀錄存在且數量對得上（或更多，可能是彙總欄位未即時更新），視為驗證通過。')
    else:
        result = 'VERIFIED'
        reason = f'sourcing_runs 聲稱 {claimed} 組 query，search_audit_log 實際 {actual} 筆，數字一致。'

    discrepancies = [] if result == 'VERIFIED' and actual >= claimed else [reason]
    vid = log_verification('sourcing_run', sourcing_run_id, verifier_run_id, result,
                           evidence_checked=evidence_checked,
                           discrepancies_found=discrepancies or None,
                           notes=reason)
    return {'result': result, 'verification_log_id': vid, 'reason': reason,
            'claimed': claimed, 'actual': actual}


# ────────────────────────────────────────────────────────────
# B-3：配對防呆 —— verify_matching_result()
# ────────────────────────────────────────────────────────────

def verify_matching_result(candidate_job_match_id, verifier_run_id='cli-manual'):
    """讀該筆 candidate_job_match 的 candidate_snapshot / requirement_snapshot，
    獨立重新跑一次 matching_engine.evaluate_candidate()，確認資料庫裡存的
    match_status 沒有出現「Hard Must FAIL/UNKNOWN 卻被判過關」的情況。

    這支不讀執行者對這筆紀錄寫的 reasons/blockers 文字說明，只讀兩個 snapshot
    （執行判定當下留下的原始輸入），自己重跑一次邏輯。"""
    rows = d1(f"SELECT * FROM candidate_job_match WHERE id={q(candidate_job_match_id)}")
    if not rows:
        vid = log_verification('matching_result', candidate_job_match_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'candidate_job_match 查無此 id：{candidate_job_match_id}')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': 'candidate_job_match 查無此 id'}
    row = rows[0]
    claimed_status = row.get('match_status')

    try:
        candidate_snapshot = json.loads(row['candidate_snapshot']) if row.get('candidate_snapshot') else None
        requirement_snapshot = json.loads(row['requirement_snapshot']) if row.get('requirement_snapshot') else None
    except (TypeError, ValueError) as e:
        vid = log_verification('matching_result', candidate_job_match_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'candidate_snapshot/requirement_snapshot 無法解析 JSON：{e}')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': f'snapshot 欄位無法解析：{e}'}

    if not candidate_snapshot or not requirement_snapshot:
        vid = log_verification('matching_result', candidate_job_match_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes='candidate_snapshot 或 requirement_snapshot 是空的，無法獨立重算判定')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': 'snapshot 缺其中一份，無法重算'}

    matching = _matching_module()
    recomputed = matching.evaluate_candidate(candidate_snapshot, requirement_snapshot)
    recomputed_verdict = recomputed['final_verdict']

    evidence_checked = {
        'candidate_job_match_id': candidate_job_match_id,
        'claimed_match_status': claimed_status,
        'recomputed_final_verdict': recomputed_verdict,
        'hard_must_fail': recomputed['hard_must_fail'],
        'hard_must_unknown': recomputed['hard_must_unknown'],
    }

    discrepancies = []
    # 規則核心：任一 Hard Must 是 FAIL 或 UNKNOWN，就不可能是 MATCH_CANDIDATE。
    # 如果資料庫裡存的 claimed_status 是 MATCH_CANDIDATE，但獨立重算發現有 Hard
    # Must FAIL/UNKNOWN，這是「執行者謊報/放水過關」的具體證據。
    if claimed_status == 'MATCH_CANDIDATE' and (recomputed['hard_must_fail'] or recomputed['hard_must_unknown']):
        discrepancies.append(
            f'資料庫存的 match_status 是 MATCH_CANDIDATE，但獨立重算發現 '
            f'hard_must_fail={recomputed["hard_must_fail"]}、'
            f'hard_must_unknown={recomputed["hard_must_unknown"]}——'
            f'依規則任一 Hard Must FAIL/UNKNOWN 就不可能是 MATCH_CANDIDATE，這筆是放水過關。')

    if claimed_status != recomputed_verdict:
        discrepancies.append(
            f'資料庫存的 match_status={claimed_status!r}，獨立重算得到 '
            f'final_verdict={recomputed_verdict!r}，兩者不一致。')

    if discrepancies:
        result = 'VERIFY_FAILED'
        reason = '；'.join(discrepancies)
    else:
        result = 'VERIFIED'
        reason = f'獨立重算 final_verdict={recomputed_verdict!r}，與資料庫存的 match_status 一致。'

    vid = log_verification('matching_result', candidate_job_match_id, verifier_run_id, result,
                           evidence_checked=evidence_checked,
                           discrepancies_found=discrepancies or None,
                           notes=reason)
    return {'result': result, 'verification_log_id': vid, 'reason': reason,
            'claimed_status': claimed_status, 'recomputed_verdict': recomputed_verdict,
            'recomputed_detail': recomputed}


def _matching_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_matching', os.path.join(HERE, 'matching_engine.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ────────────────────────────────────────────────────────────
# B-5：阿財初談 —— verify_interview_accuracy()
# ────────────────────────────────────────────────────────────

# 僱用型態關鍵字 → jobs.employment 正規化值的對應。jobs.employment 欄位歷史上存過
# 好幾種格式（純字串 'FULL_TIME'、JSON 陣列字串 "['FULL_TIME']"、中文長句），
# 這裡只做粗略正規化，不試圖完美解析每一種格式——這支程式的定位本來就是「抓出
# 值得真人核對的落差」，不是自動判決，粗略但誠實比精細但武斷更安全。
_EMPLOYMENT_MAP = [
    (('TEMPORARY', 'DISPATCH', '派遣'), '派遣'),
    (('CONTRACTOR', '約聘', '承攬'), '約聘/承攬'),
    (('FULL_TIME', '正職'), '正職'),
]


def _normalize_employment(raw):
    if not raw:
        return set()
    found = set()
    for keys, label in _EMPLOYMENT_MAP:
        if any(k in raw for k in keys):
            found.add(label)
    return found


def _extract_employment_mentions(text):
    found = set()
    for keys, label in _EMPLOYMENT_MAP:
        # 只比對中文詞（訊息是給候選人看的中文話術，不會出現 TEMPORARY 這種代碼）
        zh_keys = [k for k in keys if not k.isupper()]
        if any(k in text for k in zh_keys):
            found.add(label)
    return found


_SALARY_PATTERN = re.compile(r'(\d{1,3}(?:,\d{3})+|\d{2,3})\s*[Kk]?')


def _extract_salary_numbers(text):
    """從文字裡抓可能是月薪的數字。粗略規則：抓「XXK」或「三萬以上帶千分位／裸數字」，
    過濾掉明顯不是薪資的小數字（例如年資、月份）。這是啟發式，不是精確解析——
    輸出只用來提示真人去核對，不用來自動判定對錯。"""
    nums = set()
    for m in re.finditer(r'(\d{2,3})\s*[Kk]', text):
        nums.add(int(m.group(1)) * 1000)
    for m in re.finditer(r'(\d{1,3}(?:,\d{3})+)', text):
        nums.add(int(m.group(1).replace(',', '')))
    return nums


def verify_interview_accuracy(application_id, verifier_run_id='cli-manual'):
    """讀該應徵者的 messages 表逐字稿，抽出裡面提到的僱用型態／薪資數字，跟 jobs 表
    「現在」的資料比對。

    ⚠️ jobs 表沒有版本歷史，這支函式做不到「跟面談當下那個版本比對」，只能誠實地
    跟現在的版本比。不一致時標記 POTENTIAL_STALE_INFO，不自動判定阿財當初講錯——
    也可能是面談當下資料本來就是那樣，後來才改的。輸出是「值得真人核對的落差清單」，
    不是錯誤判決書。"""
    apps = d1(f"SELECT id, job_slug, job_title FROM applications WHERE id={q(application_id)}")
    if not apps:
        vid = log_verification('interview_accuracy', application_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'applications 查無此 id：{application_id}')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': 'applications 查無此 id'}
    app = apps[0]
    job_slug = app.get('job_slug')

    messages = d1(f"SELECT role, content, created_at FROM messages "
                  f"WHERE application_id={q(application_id)} AND role='assistant' ORDER BY id")
    if not messages:
        vid = log_verification('interview_accuracy', application_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'messages 查無此 application_id 的 assistant 訊息：{application_id}')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': 'messages 查無逐字稿（assistant 端）'}

    jobs = d1(f"SELECT slug, title, employment, salary_min, salary_max, salary_unit, updated_at "
              f"FROM jobs WHERE slug={q(job_slug)}")
    if not jobs:
        vid = log_verification('interview_accuracy', application_id, verifier_run_id,
                               'INSUFFICIENT_EVIDENCE',
                               notes=f'jobs 查無 slug={job_slug}，無法比對')
        return {'result': 'INSUFFICIENT_EVIDENCE', 'verification_log_id': vid,
                'reason': f'jobs 查無 slug={job_slug}'}
    job_now = jobs[0]

    full_transcript = '\n'.join(m['content'] for m in messages)
    mentioned_employment = _extract_employment_mentions(full_transcript)
    mentioned_salaries = _extract_salary_numbers(full_transcript)
    now_employment = _normalize_employment(job_now.get('employment') or '')
    now_salary_min = job_now.get('salary_min')
    now_salary_max = job_now.get('salary_max')

    discrepancies = []

    if mentioned_employment and now_employment and not (mentioned_employment & now_employment):
        discrepancies.append({
            'type': 'EMPLOYMENT_TYPE_MISMATCH',
            'mentioned_in_interview': sorted(mentioned_employment),
            'jobs_table_now': sorted(now_employment),
            'jobs_updated_at': job_now.get('updated_at'),
        })

    if mentioned_salaries and now_salary_min is not None and now_salary_max is not None:
        # 有任何一個提到的數字落在現行區間內就算「大致對得上」；完全沒有交集才標記，
        # 避免把「候選人自己提的期望薪資 50K」誤判成落差（那不是阿財講的職缺薪資）。
        tolerance = 3000  # 允許小幅出入（例如四捨五入、含不含加班費估算差異）
        in_range = any(now_salary_min - tolerance <= s <= now_salary_max + tolerance
                       for s in mentioned_salaries)
        if not in_range:
            discrepancies.append({
                'type': 'SALARY_MISMATCH',
                'mentioned_in_interview': sorted(mentioned_salaries),
                'jobs_table_now_range': [now_salary_min, now_salary_max],
                'jobs_updated_at': job_now.get('updated_at'),
            })

    evidence_checked = {
        'application_id': application_id,
        'job_slug': job_slug,
        'assistant_message_count': len(messages),
        'mentioned_employment': sorted(mentioned_employment),
        'mentioned_salary_numbers': sorted(mentioned_salaries),
        'jobs_table_employment_now': sorted(now_employment),
        'jobs_table_salary_range_now': [now_salary_min, now_salary_max],
        'jobs_updated_at': job_now.get('updated_at'),
    }

    if discrepancies:
        result = 'VERIFIED'  # 查證動作本身成功完成；發現的落差是 POTENTIAL_STALE_INFO 旗標，不代表查證失敗
        flag = 'POTENTIAL_STALE_INFO'
        reason = (f'查到 {len(discrepancies)} 項面談內容與 jobs 表現況不一致，標記 '
                  f'{flag}——不代表阿財當初講錯，也可能是面談後職缺資料被更新，'
                  f'需要真人核對是否要主動聯繫候選人更正。')
    else:
        flag = None
        reason = '面談逐字稿提到的僱用型態／薪資數字，跟 jobs 表現況一致（或訊息中未提及可比對的具體數字）。'

    vid = log_verification('interview_accuracy', application_id, verifier_run_id, result,
                           evidence_checked=evidence_checked,
                           discrepancies_found=discrepancies or None,
                           notes=reason)
    return {'result': result, 'flag': flag, 'verification_log_id': vid, 'reason': reason,
            'discrepancies': discrepancies, 'evidence_checked': evidence_checked}


# ────────────────────────────────────────────────────────────
# CLI（手動測試用）
# ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Step1ne 獨立驗證引擎')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('verify-sourcing')
    p.add_argument('--run', required=True)
    p.add_argument('--verifier-run-id', default='cli-manual')

    p = sub.add_parser('verify-matching')
    p.add_argument('--match-id', required=True)
    p.add_argument('--verifier-run-id', default='cli-manual')

    p = sub.add_parser('verify-interview')
    p.add_argument('--application-id', required=True)
    p.add_argument('--verifier-run-id', default='cli-manual')

    sub.add_parser('ensure-table')

    a = ap.parse_args()

    if a.cmd == 'ensure-table':
        ensure_verification_log_table()
    elif a.cmd == 'verify-sourcing':
        print(json.dumps(verify_sourcing_run(a.run, a.verifier_run_id), ensure_ascii=False, indent=2))
    elif a.cmd == 'verify-matching':
        print(json.dumps(verify_matching_result(a.match_id, a.verifier_run_id), ensure_ascii=False, indent=2))
    elif a.cmd == 'verify-interview':
        print(json.dumps(verify_interview_accuracy(a.application_id, a.verifier_run_id),
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
