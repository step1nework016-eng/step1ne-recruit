#!/usr/bin/env python3
"""阿財 P3-A 安全閥測試（2026-09-18）

這裡測的是**程式端的決定性邏輯**，不是 AI 的判斷品質——
AI 判斷得準不準要靠上線後量「顧問採用率」，測試測不出來。
這裡要保證的是：**就算 AI 判斷錯了，程式也不會把明顯不該推的東西推出去。**

跑法：python3 tests/test_p3_rematch.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import recommendation_service as rs  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}{('  ← ' + detail) if detail and not cond else ''}")


JOBS = {
    'bim-engineer':      {'slug': 'bim-engineer', 'title': 'BIM 工程師', 'status': 'open',
                          'locations': '苗栗縣銅鑼鄉', 'salary_max': 60000, 'employment': '正職'},
    'semi-pm':           {'slug': 'semi-pm', 'title': '半導體專案工程師', 'status': 'open',
                          'locations': '桃竹苗地區、台中', 'salary_max': 70000, 'employment': '正職'},
    'cambodia-fin':      {'slug': 'cambodia-fin', 'title': '柬埔寨廠財務', 'status': 'open',
                          'locations': '柬埔寨（長期外派）', 'salary_max': 90000, 'employment': '正職'},
    'night-qc':          {'slug': 'night-qc', 'title': '夜班品管', 'status': 'open',
                          'locations': '台中市', 'salary_max': 45000, 'employment': '正職・夜班'},
    'closed-job':        {'slug': 'closed-job', 'title': '已關閉的職缺', 'status': 'closed',
                          'locations': '台北市', 'salary_max': 80000, 'employment': '正職'},
}
SNAP = {'applied_job_slug': 'bim-engineer', 'name': '測試人選'}


def rec(slug, status='POSSIBLE_MATCH'):
    return {'job_slug': slug, 'match_status': status, 'confidence': 'medium',
            'reasons': ['有工程背景'], 'evidence': ['逐字稿：我做過現場協調']}


# ── CASE 1：工程背景可轉的職缺，安全閥不該擋 ────────────────────────
kept, dropped = rs.hard_safety_filter([rec('semi-pm')], SNAP, {}, JOBS)
check('CASE1 可轉職的職缺不會被誤擋', len(kept) == 1 and not dropped, str(dropped))

# ── CASE 2：候選人明確拒絕苗栗 → 苗栗的職缺不得出現 ──────────────────
# ⚠️ 這一題要用「不是他原本應徵的」職缺測，否則會先被 same_as_applied_job 擋掉，
# 測不到地點過濾到底有沒有作用（2026-09-18 第一版測試就踩到這個假通過）。
MIAOLI_JOBS = dict(JOBS, other_miaoli={'slug': 'other-miaoli', 'title': '苗栗某職缺',
                                       'status': 'open', 'locations': '苗栗縣竹南鎮',
                                       'salary_max': 60000, 'employment': '正職'})
MIAOLI_JOBS['other-miaoli'] = MIAOLI_JOBS.pop('other_miaoli')
llm = {'explicit_rejections': [{'code': 'location_miaoli', 'label': '不接受苗栗'}]}
kept, dropped = rs.hard_safety_filter([rec('other-miaoli'), rec('semi-pm')], SNAP, llm, MIAOLI_JOBS)
check('CASE2 明確拒絕的地點被擋掉',
      all(k['job_slug'] != 'other-miaoli' for k in kept) and any('苗栗' in d['reason'] for d in dropped),
      f'kept={[k["job_slug"] for k in kept]} dropped={dropped}')

# ── CASE 2b：職缺寫「桃竹苗地區」，候選人說不要「苗栗」也要擋得到 ────
# 真實資料裡 locations 大量使用區域縮寫，純字面比對會漏擋（2026-09-18 實測發現）。
ALIAS_JOBS = dict(JOBS, **{'tkm-job': {'slug': 'tkm-job', 'title': '桃竹苗某職缺', 'status': 'open',
                                       'locations': '桃竹苗地區', 'salary_max': 70000,
                                       'employment': '正職'}})
kept, dropped = rs.hard_safety_filter([rec('tkm-job')], SNAP, llm, ALIAS_JOBS)
check('CASE2b 區域縮寫（桃竹苗地區 vs 苗栗）也擋得到', not kept, f'kept={[k["job_slug"] for k in kept]}')

# ── CASE 3：薪資明顯低於底線 → 不得留在推薦裡 ──────────────────────
llm = {'minimum_salary_monthly': '65000'}
kept, dropped = rs.hard_safety_filter([rec('night-qc'), rec('semi-pm')], SNAP, llm, JOBS)
check('CASE3 薪資上限低於底線被擋掉',
      all(k['job_slug'] != 'night-qc' for k in kept) and any('salary_below_floor' in d['reason'] for d in dropped),
      str(dropped))
check('CASE3b 薪資高於底線的不受影響', any(k['job_slug'] == 'semi-pm' for k in kept))

# ── CASE 4：已關閉的職缺永遠不出現 ──────────────────────────────────
kept, dropped = rs.hard_safety_filter([rec('closed-job')], SNAP, {}, JOBS)
check('CASE4 已關閉職缺被擋掉', not kept and 'closed' in (dropped[0]['reason'] if dropped else ''), str(dropped))

# ── CASE 5：候選人原本應徵的職缺不得被當成「替代方案」 ──────────────
kept, dropped = rs.hard_safety_filter([rec('bim-engineer')], SNAP, {}, JOBS)
check('CASE5 原應徵職缺被擋掉', not kept and 'same_as_applied' in (dropped[0]['reason'] if dropped else ''), str(dropped))

# ── CASE 6：已經應徵過／已經推薦過的不重複推 ────────────────────────
kept, dropped = rs.hard_safety_filter([rec('semi-pm')], SNAP, {}, JOBS, existing_slugs={'semi-pm'})
check('CASE6 重複推薦被擋掉', not kept and 'already' in (dropped[0]['reason'] if dropped else ''), str(dropped))

# ── CASE 7：拒絕外派 → 外派職缺被擋 ────────────────────────────────
llm = {'explicit_rejections': [{'code': 'solo_overseas_assignment', 'label': '不接受單獨長期外派柬埔寨'}]}
kept, dropped = rs.hard_safety_filter([rec('cambodia-fin')], SNAP, llm, JOBS)
check('CASE7 拒絕外派→外派職缺被擋', not kept, str(kept))

# ── CASE 8：資料不足時不亂擋（寧可交給顧問判斷，也不要靜默漏掉機會）──
kept, dropped = rs.hard_safety_filter([rec('semi-pm')], SNAP, {'minimum_salary_monthly': None}, JOBS)
check('CASE8 沒有薪資底線資料時不做薪資過濾', len(kept) == 1, str(dropped))

# ── CASE 9：AI 亂填一個不存在的職缺 → 被擋 ──────────────────────────
kept, dropped = rs.hard_safety_filter([rec('this-job-does-not-exist')], SNAP, {}, JOBS)
check('CASE9 虛構的職缺被擋掉', not kept and 'not_in_matchable_list' in (dropped[0]['reason'] if dropped else ''))

# ── CASE 10：list_matchable_jobs 的狀態定義 ─────────────────────────
check('CASE10 可推薦狀態只認 open/active', rs.MATCHABLE_STATUSES == ('open', 'active'))
check('CASE10b closed/draft/client_draft/pending_review 都在排除清單',
      set(rs.EXCLUDED_STATUSES) == {'closed', 'draft', 'client_draft', 'pending_review'})

# ── CASE 11：AI 輸出格式壞掉時 validator 要擋下來 ───────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai_worker  # noqa: E402

payload = {'matchable_jobs': [{'slug': 'semi-pm'}], 'candidate_snapshot': SNAP}


def rejects(data, label):
    try:
        ai_worker._validate_rematch(data, payload)
        check(label, False, '應該要擋下來卻放行了')
    except ValueError:
        check(label, True)


rejects({'recommendations': [{'job_slug': 'semi-pm', 'match_status': 'GREAT',
                              'confidence': 'medium'}]}, 'CASE11 非法 match_status 被擋')
rejects({'recommendations': [{'job_slug': 'not-in-list', 'match_status': 'POSSIBLE_MATCH',
                              'confidence': 'low'}]}, 'CASE11b 清單外職缺被擋')
rejects({'recommendations': [{'job_slug': 'bim-engineer', 'match_status': 'POSSIBLE_MATCH',
                              'confidence': 'low'}]}, 'CASE11c 推薦原應徵職缺被擋')
rejects({'recommendations': [{'job_slug': 'semi-pm', 'match_status': 'MATCH_CANDIDATE',
                              'confidence': 'high', 'reasons': ['很適合'], 'evidence': []}]},
        'CASE11d 說適合卻沒附佐證被擋')
rejects({'recommendations': [rec('semi-pm')] * 4}, 'CASE11e 超過 3 個被擋')

try:
    ai_worker._validate_rematch({'recommendations': []}, payload)
    check('CASE11f 空推薦是合法的（沒有更適合的就是沒有）', True)
except ValueError as e:
    check('CASE11f 空推薦是合法的（沒有更適合的就是沒有）', False, str(e))

# ── CASE 12：功能旗標預設關閉 ───────────────────────────────────────
check('CASE12 P3_REMATCH_ENABLED 預設關閉',
      ai_worker.P3_REMATCH_ENABLED is False or os.environ.get('P3_REMATCH_ENABLED') == '1')

print(f'\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項')
if FAIL:
    print('失敗：', FAIL)
sys.exit(1 if FAIL else 0)
