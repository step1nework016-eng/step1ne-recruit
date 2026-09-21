#!/usr/bin/env python3
"""推薦降級閘門——用 2026-09-21 真實誤判案例當測資

那天 Jacky 把 18 筆積壓的推薦一次決定完，事後拿決定回頭跑閘門，
抓到 4 筆、放過 5 筆，還誤殺 2 筆。這支把當天修掉的四個根因鎖住。

每一條都對應一位真實人選，改壞了會直接回到當天的錯誤行為。

跑法：python3 tests/test_recommendation_gates_real_cases.py
不碰資料庫、不呼叫 AI。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import recommendation_service as R  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ''))


def salary_gap(snapshot, job):
    """複製 filter 裡那段判斷，單獨驗它會不會觸發。"""
    want = R._expected_salary_monthly(snapshot) or R._salary_floor_from_interview(snapshot)
    try:
        jmax = float(job.get('salary_max') or job.get('salary_min') or 0)
    except (TypeError, ValueError):
        jmax = 0
    return bool(want and jmax and jmax <= want * (1 - R.EXPECTED_SALARY_GAP_LIMIT))


# ── 1. 王仁君：落差剛好 25.0%，卡在邊界被放過 ──────────────────
print('\n【1】薪資落差邊界（王仁君）')

wang = {'form': {'expected_salary': '60k'}}
job_trainee = {'salary_min': 35000, 'salary_max': 45000}
check('1a 期望 60K、職缺上限 45K（落差整整 25%）→ 要觸發',
      salary_gap(wang, job_trainee), '邊界用 < 會漏掉，要用 <=')
check('1b 期望 60K、職缺上限 50K（落差 17%）→ 不要觸發',
      not salary_gap(wang, {'salary_max': 50000}))


# ── 2. 郭鑑宸：表單沒填，門檻寫在面談摘要裡 ────────────────────
print('\n【2】表單沒填期望薪資時，要看面談摘要（郭鑑宸）')

guo = {'form': {'expected_salary': None}, 'interview': {'summary':
       '候選人現職中華紙漿工安室副理，具12年以上職安經驗。惟現職年薪約90萬，'
       '轉職門檻約110–120萬，遠高於JD月薪50–60K；學歷為高職。'}}
floor = R._salary_floor_from_interview(guo)
check('2a 抓得到「轉職門檻約110–120萬」',
      floor is not None and 88000 < floor < 100000, f'抓到 {floor}')
check('2b 不可以抓成同一句裡的「現職年薪約90萬」',
      floor is None or floor > 80000, f'抓到 {floor}（90萬/12=75000）')
check('2c 整條閘門會觸發（職缺月薪 50–60K）',
      salary_gap(guo, {'salary_min': 50000, 'salary_max': 60000}))

now_only = {'form': {}, 'interview': {'summary': '現職年薪約90萬，沒有特別提到期望。'}}
check('2d 只講現職、沒講期望 → 不要亂抓',
      R._salary_floor_from_interview(now_only) is None,
      f'抓到 {R._salary_floor_from_interview(now_only)}')

check('2e 表單有填就以表單為準，不去翻面談',
      R._expected_salary_monthly(wang) == 60000, R._expected_salary_monthly(wang))


# ── 3. 蘇微閔：正職職缺被誤標成派遣 ────────────────────────────
print('\n【3】內部備註寫「原為派遣」不等於現在是派遣（蘇微閔）')

bim_job = {'employment': '"FULL_TIME"', 'title': 'BIM 工程師',
           'salary_note': '【2026-08-26 已釐清】本案原為派遣，2026-08-21 起改為正職僱用，'
                          '錄取後直接是用人單位的正職員工，不是派遣、不是約聘。'}
check('3a employment 是 FULL_TIME → 不算派遣，不管備註寫什麼',
      not R._is_temporary(bim_job))
check('3b salary_note 完全不參與判斷（那是顧問內部備註）',
      not R._is_temporary({'employment': 'FULL_TIME', 'salary_note': '派遣 約聘 定期'}))

real_dispatch = {'employment': 'DISPATCH', 'title': '財務會計專員'}
check('3c 真的是派遣的職缺還是要抓到', R._is_temporary(real_dispatch))
check('3d 標題寫「15個月專案」也要抓到',
      R._is_temporary({'employment': '', 'title': '財務會計專員（15個月專案型）'}))


# ── 4. 呂書帆：現職就在白馬，卻被標「地點不符」 ────────────────
print('\n【4】海外是一個家族，不是一個地名（呂書帆）')

lu = {'form': {'location_ok': '台北市・新北市、桃園・新竹、苗栗・台中・彰化、'
                              '高雄・屏東、花蓮・台東、海外外派　｜　想先了解細節再決定'}}
hakuba = {'locations': '日本・長野縣北安曇郡白馬村'}
check('4a 表單勾「海外外派」、職缺在日本 → 不算地點不符',
      not R._location_outside_form(lu, hakuba))

check('4b 表單寫「日本」、職缺在日本 → 也對得上',
      not R._location_outside_form({'form': {'location_ok': '只考慮日本'}}, hakuba))

north_only = {'form': {'location_ok': '台北、新北、桃園'}}
check('4c 只勾北部、職缺在苗栗 → 仍要判定不符（不可以因為改海外邏輯就全放過）',
      R._location_outside_form(north_only, {'locations': '苗栗縣銅鑼鄉（調派範圍：苗栗、台中）'}))
check('4d 只勾北部、職缺在日本 → 也要判定不符',
      R._location_outside_form(north_only, hakuba))


print(f"\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    print('失敗清單：' + '、'.join(FAIL))
sys.exit(1 if FAIL else 0)
