#!/usr/bin/env python3
"""推薦品質五道閘門測試（2026-09-21）

每一個 case 都對應 2026-09-21 那份 16 筆推薦稽核裡的一個真實案例，
不是想像出來的情境。案例來源寫在各自的 check 名稱裡。

跑法：python3 tests/test_recommendation_quality.py
不碰資料庫、不呼叫 AI。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import recommendation_service as rs  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ''))


def snap(expected_salary=None, location_ok=None, motivation=None, applied='applied-job'):
    return {
        'application_id': 'a1', 'name': '測試', 'applied_job_slug': applied,
        'form': {'expected_salary': expected_salary, 'location_ok': location_ok},
        'interview': {'motivation': motivation},
    }


def job(slug='j1', **kw):
    base = {'slug': slug, 'title': '測試職缺', 'status': 'open',
            'locations': None, 'salary_max': None, 'employment': 'FULL_TIME'}
    base.update(kw)
    return base


def rec(slug='j1', evidence=None):
    return {'job_slug': slug, 'match_status': 'POSSIBLE_MATCH',
            'evidence': evidence if evidence is not None else ['證據一', '證據二'],
            'blockers': []}


def run(recs, s, llm, jobs):
    return rs.hard_safety_filter(recs, s, llm, {j['slug']: j for j in jobs})


# ── 1. 期望薪資落差（郭鑑宸 −45%、全筱琪 −40%）────────────────────
kept, _ = run([rec()], snap(expected_salary='50000'), {}, [job(salary_max=30000)])
check('1a 期望 5 萬 vs 職缺 3 萬（−40%）→ 降級',
      kept[0]['match_status'] == 'INSUFFICIENT_DATA', kept[0]['match_status'])
check('1b 降級時要寫出落差幾 %，顧問才判斷得出來',
      any('薪資落差' in b and '%' in b for b in kept[0]['blockers']), str(kept[0]['blockers']))

kept, _ = run([rec()], snap(expected_salary='50000'), {}, [job(salary_max=45000)])
check('1c 期望 5 萬 vs 職缺 4.5 萬（−10%）→ 不降級（實務上談得動）',
      kept[0]['match_status'] == 'POSSIBLE_MATCH', kept[0]['match_status'])

kept, _ = run([rec()], snap(expected_salary=None), {}, [job(salary_max=30000)])
check('1d 表單沒填期望薪資 → 不因薪資降級（不可以憑空判斷）',
      kept[0]['match_status'] == 'POSSIBLE_MATCH')

check('1e 年薪寫法看得懂：「年薪120萬」→ 月薪 10 萬',
      abs(rs._expected_salary_monthly(snap(expected_salary='年薪120萬')) - 100000) < 1)
check('1f 「70K」看得懂', rs._expected_salary_monthly(snap(expected_salary='70K')) == 70000)
check('1g 「41-43K」取低標 41000',
      rs._expected_salary_monthly(snap(expected_salary='41-43K')) == 41000)

# ── 2. 佐證太少（全筱琪只有 1 條，還跟另一筆是同一句話）────────────
kept, _ = run([rec(evidence=['只有這一條'])], snap(), {}, [job()])
check('2a 只有 1 條佐證 → 降級', kept[0]['match_status'] == 'INSUFFICIENT_DATA')
kept, _ = run([rec(evidence=['一', '二'])], snap(), {}, [job()])
check('2b 2 條佐證 → 不降級', kept[0]['match_status'] == 'POSSIBLE_MATCH')
kept, _ = run([rec(evidence=[])], snap(), {}, [job()])
check('2c 完全沒有佐證 → 降級', kept[0]['match_status'] == 'INSUFFICIENT_DATA')

# ── 3. 地點不在表單勾選範圍（Yi-yun Guo 連兩筆）──────────────────
s = snap(location_ok='台北・新北、基隆宜蘭、桃園新竹')
kept, _ = run([rec()], s, {}, [job(locations='苗栗縣銅鑼鄉')])
check('3a 表單勾台北新北桃竹、職缺在苗栗 → 降級',
      kept[0]['match_status'] == 'INSUFFICIENT_DATA', str(kept[0]['blockers']))
kept, _ = run([rec()], s, {}, [job(locations='新竹科學園區')])
check('3b 職缺在新竹（表單有勾）→ 不降級', kept[0]['match_status'] == 'POSSIBLE_MATCH')
kept, _ = run([rec()], s, {}, [job(locations='桃竹苗地區')])
check('3c 職缺寫縮寫「桃竹苗」、表單勾新竹 → 認得出來，不降級',
      kept[0]['match_status'] == 'POSSIBLE_MATCH')
kept, _ = run([rec()], snap(location_ok=None), {}, [job(locations='苗栗縣銅鑼鄉')])
check('3d 表單沒填地點 → 不判斷（寧可不降級也不要誤判）',
      kept[0]['match_status'] == 'POSSIBLE_MATCH')
kept, _ = run([rec()], s, {}, [job(locations='全台不限')])
check('3e 職缺地點是「全台不限」→ 不判斷', kept[0]['match_status'] == 'POSSIBLE_MATCH')

# ── 4. 重視長期穩定卻推派遣（劉柔諍：15 個月定期派遣）──────────────
s = snap(motivation={'why_leaving': '上一份是專案型的，希望找長期穩定發展的環境'})
kept, _ = run([rec()], s, {}, [job(employment='DISPATCH', title='財務會計專員（15個月專案）')])
check('4a 離職原因提「長期穩定」+ 派遣職缺 → 降級',
      kept[0]['match_status'] == 'INSUFFICIENT_DATA', str(kept[0]['blockers']))
kept, _ = run([rec()], s, {}, [job(employment='FULL_TIME', title='財務會計專員')])
check('4b 同一個人配正職 → 不降級', kept[0]['match_status'] == 'POSSIBLE_MATCH')
kept, _ = run([rec()], snap(motivation={'why_leaving': '想轉換產業'}), {},
              [job(employment='DISPATCH')])
check('4c 沒提穩定需求 → 派遣不降級（不要自己腦補）',
      kept[0]['match_status'] == 'POSSIBLE_MATCH')

# ── 5. 降級是降級，不是擋掉——顧問仍然看得到 ──────────────────────
kept, dropped = run([rec(evidence=['一條'])], snap(expected_salary='50000'), {},
                    [job(salary_max=20000, locations='苗栗')])
check('5a 同時踩到多條規則也只降級一次，不會被丟掉',
      len(kept) == 1 and len(dropped) == 0, f'kept={len(kept)} dropped={len(dropped)}')
check('5b 多個原因都要列給顧問看', len(kept[0].get('demoted_reasons') or []) >= 2,
      str(kept[0].get('demoted_reasons')))
check('5c blockers 不會無限膨脹（上限 4 條）', len(kept[0]['blockers']) <= 4)

# ── 6. 原本就該擋掉的還是要擋掉（不要被新規則蓋掉）──────────────────
kept, dropped = run([rec(slug='applied-job')], snap(applied='applied-job'), {},
                    [job(slug='applied-job')])
check('6a 自己應徵的職缺照樣擋掉', len(kept) == 0 and dropped[0]['reason'] == 'same_as_applied_job')
kept, dropped = run([rec()], snap(), {}, [job(status='closed')])
check('6b 已關閉的職缺照樣擋掉', len(kept) == 0, str(dropped))

print(f'\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項')
if FAIL:
    print('失敗：', FAIL)
sys.exit(1 if FAIL else 0)
