"""阿財推薦職缺（2026-10-21）測試。不寫 D1、不碰真人選、不開任何 /chat/ 連結。

  python3 tests/acai_job_recs_test.py           # 兩組測試（會「讀」D1 的開放職缺與客戶名單，不寫入）
  python3 tests/acai_job_recs_test.py --live    # 第一組改用真的 AI 挑職缺（花一次 claude 呼叫）

① 假人選（姓名含「測試」）→ 用真的開放職缺組卡片，印出卡片 JSON 與感謝信段落
② 外洩測試：客戶名、顧問內部薪資口徑、CONSULTANT_ONLY 欄位、保護特徵、承諾字眼、編造原話都擋得住
"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import acai_job_recs as A

fail = 0
def check(cond, msg):
    global fail
    print(('PASS ' if cond else 'FAIL ') + msg); fail += (not cond)

# ── ① 假人選 ──────────────────────────────────────────────
CONV = [
    {'role': 'candidate', 'content': '（候選人已進入面談室）'},
    {'role': 'assistant', 'content': '您好，先請您介紹一下最近的工作？'},
    {'role': 'candidate', 'content': '我之前在建設公司做了兩年行政，主要是處理文件、接電話跟安排會議'},
    {'role': 'assistant', 'content': '了解，那您目前住在哪個縣市？通勤方式呢？'},
    {'role': 'candidate', 'content': '我住新北中和，騎車上班，台北內湖也可以去'},
    {'role': 'assistant', 'content': '期望待遇大概多少？'},
    {'role': 'candidate', 'content': '希望月薪四萬左右'},
    {'role': 'candidate', 'content': '我也滿喜歡接待客人的工作，之前常常幫忙接待訪客'},
]
APP = {'id': 'TEST-RECS-0001', 'name': '王測試', 'job_slug': 'receptionist-neihu', 'job_title': '總機接待',
       'email': None, 'expected_salary': '月薪 40,000', 'location_ok': '台北、新北', 'note': ''}
ctx = {'application': APP, 'job': {'slug': 'receptionist-neihu'}, 'conversation': CONV}

check(A.eligible('test', '王測試') and not A.eligible('test', '王小明') and A.eligible('on', '王小明')
      and not A.eligible('off', '王測試'), '開關：test 只有姓名含「測試」、on 全部、off 都不給')

jobs, _ = A.load_candidate_jobs(APP['id'], APP['job_slug'])
check(all(j['slug'] != 'receptionist-neihu' for j in jobs), f'候選職缺不含正在面談的職缺（共 {len(jobs)} 個開放且有公開頁）')
guard = A.ClientGuard.from_d1()
by = {j['slug']: j for j in jobs}

if '--live' in sys.argv:
    import interview_daemon as D
    out = D.run_claude(A.build_prompt(ctx, jobs), first_try=90, total=150)
else:   # 模擬 AI：挑三個行政／接待類，原話照抄逐字稿
    want = [s for s in ('admin-assistant-neihu-hq', 'vip-hospitality-attendant', 'executive-secretary-assistant-neihu',
                        'rehab-clinic-front-desk-multi-site') if s in by][:3]
    ev = ['做了兩年行政，主要是處理文件', '滿喜歡接待客人的工作', '台北內湖也可以去']
    out = {'picks': [{'slug': s, 'fit': '高' if i == 0 else '中', 'why': w, 'confirm': c, 'evidence': ev[i]}
                     for i, (s, w, c) in enumerate(zip(want, ['您做過兩年行政文件與會議安排', '您說喜歡接待客人、常接待訪客',
                                                              '您說內湖可以去，也有行政經驗'],
                                                       ['輪班或加班的安排能不能配合', '晚班時段能不能配合', '主管行程協調的經驗多寡']))]}
recs, dropped = A.validate_picks(out, by, guard, A._said_text(ctx))
check(1 <= len(recs) <= 3, f'產出 {len(recs)} 個推薦（丟掉 {len(dropped)}：{dropped}）')
card = A.build_card(recs)
print('\n===== 卡片 JSON（給面談頁）=====')
print(json.dumps(card, ensure_ascii=False, indent=1))
print('\n===== 感謝信段落 =====')
print('\n'.join(A.email_lines(recs)))
print('\n===== 報告附加段落 =====')
print(A.report_md(recs, wanted={recs[0]['slug']} if recs else set()))
print()
shown = json.dumps(card, ensure_ascii=False) + '\n'.join(A.email_lines(recs))
check(not guard.hits(shown), '卡片與信件裡沒有任何客戶名')
check('evidence' not in json.dumps(card, ensure_ascii=False) and 'fit' not in (card or {}).get('jobs', [{}])[0],
      '卡片不帶「人選原話」「適配度」這種內部欄位')
check(A.build_card([]) is None and A.email_lines([]) == [], '沒有推薦 → 不出卡片、信裡不加段落')

# ── ② 外洩測試 ───────────────────────────────────────────
print('\n----- 外洩測試 -----')
note = ('【對人選：唯一口徑】平均薪資 4 萬起，實際待遇面議。\n只講候選人學歷對應的那一段數字。\n\n'
        '⚠️ 以下僅供顧問口頭說明，不得對候選人講：\n學士 40,833；碩士 45,000；客戶預算其實到 55,000')
pub = A.public_salary_note(note)
check(pub == '平均薪資 4 萬起，實際待遇面議。', f'顧問內部薪資口徑被濾掉 →「{pub}」')
check(A.public_salary_note('⚠️ 內部限閱：幣別尚未跟客戶確認。對外一律說面議') == A.SAL_FALLBACK, '整段都是內部說明 → 改用固定的面議句')
check(A.public_salary_note('月薪 33,000–35,000 元。對外與社群發文只講總額區間，不要自行拆解。') == '月薪 33,000–35,000 元。',
      '「對外與…只講…」那句被濾掉')
check(A.public_employment('["FULL_TIME", "TEMPORARY"]') == '全職・定期契約' and A.public_employment('"派遣・半年約"') == '派遣・半年約',
      '僱用型態轉中文')
check(A.short_location('台北市內湖區瑞光路337號7樓') == '台北市內湖區' and A.short_location("['桃竹苗地區', '台中']") == '桃竹苗地區、台中',
      f'地點不帶門牌：{A.short_location("台北市內湖區瑞光路337號7樓")}')

g = A.ClientGuard(['測試甲科技股份有限公司', '乙乙集團（Bravo）', '築楽国際開発株式会社'])
FAKE = {
    'j1': {'slug': 'j1', 'title': '行政助理（測試甲總部）', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職',
           'client_contact_phone': '0912-345-678', 'client_screen_conditions': '限女性', 'hiring_manager': '張經理'},
    'j2': {'slug': 'j2', 'title': '業務專員', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職',
           'main_duties': '負責 Bravo 集團的客戶開發'},
    'j3': {'slug': 'j3', 'title': '櫃檯接待', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職'},
    'j4': {'slug': 'j4', 'title': '總務', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職'},
    'j5': {'slug': 'j5', 'title': '文書', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職'},
    'j6': {'slug': 'j6', 'title': '客服', 'locations': '台北市', 'salary_note': '月薪 4 萬', 'employment': '正職'},
    'j7': {'slug': 'j7', 'title': '秘書', 'locations': '東京', 'salary_note': '築樂國際開發提供宿舍', 'employment': '正職'},
}
said = '我做了兩年行政，主要是處理文件，也喜歡接待客人'
P = lambda s, why='您做過兩年行政', conf='能否配合加班', ev='做了兩年行政': {'slug': s, 'fit': '高', 'why': why, 'confirm': conf, 'evidence': ev}
kept, dr = A.validate_picks({'picks': [
    P('j1'), P('j2'), P('j3', why='您年紀輕適合培訓'), P('j4', why='保證會幫您推薦'),
    P('j5', ev='我做過十年會計主管'), P('j6', why='您做過行政', conf='', ev='做了兩年行政'), P('j7'), P('nope')]}, FAKE, g, said)
reasons = {d['slug']: d['reason'] for d in dr}
check(not kept, f'全部該擋的都擋下（留下 {[k["slug"] for k in kept]}）')
check('客戶名' in reasons.get('j1', ''), f'職稱含客戶簡稱 → 擋（{reasons.get("j1")}）')
check('客戶名' in reasons.get('j2', ''), f'工作內容含客戶英文名 → 擋（{reasons.get("j2")}）')
check('保護特徵' in reasons.get('j3', ''), f'理由提到年紀 → 擋（{reasons.get("j3")}）')
check('承諾' in reasons.get('j4', ''), f'理由寫「保證會幫您推薦」→ 擋（{reasons.get("j4")}）')
check('原話' in reasons.get('j5', ''), f'人選沒講過的話當證據 → 擋（{reasons.get("j5")}）')
check('空白' in reasons.get('j6', ''), '「還要確認」空白 → 擋')
check('客戶名' in reasons.get('j7', ''), f'日文寫法的客戶名（楽／樂）→ 擋（{reasons.get("j7")}）')
check('nope' in reasons, '不在候選清單的職缺 → 擋')

pr = A.build_prompt({'application': {'name': '測試', 'note': ''}, 'conversation': [{'role': 'candidate', 'content': said}]},
                    list(FAKE.values()))
check(not any(x in pr for x in ('0912-345-678', '限女性', '張經理')), 'CONSULTANT_ONLY 欄位（窗口電話、客戶私下條件、用人主管）不會進 AI 提示詞')

# 真資料：每一個開放職缺的「給人選看版本」都不含內部口徑與客戶名
bad_sal, bad_name = [], []
for j in jobs:
    v = A.public_job_view(j)
    if A.SAL_INTERNAL.search(v['salary']):
        bad_sal.append((j['slug'], v['salary']))
    h = guard.hits(' '.join(str(v[k]) for k in ('title', 'location', 'salary', 'employment', 'duty')))
    if h:
        bad_name.append((j['slug'], h[:2]))
check(not bad_sal, f'{len(jobs)} 個真職缺的薪資文字都沒有內部口徑 {bad_sal}')
print(f'INFO 真職缺命中客戶名守門員、會自動不推的：{bad_name or "無"}')

# 居住地題（同一包改動）
import interview_daemon as D
c0 = {'application': {'note': '【快速應徵】\n相關經驗：無'}, 'conversation': [], 'job': {'locations': "['台中市后里區']"}}
check(not D._residence_known(c0), '快速應徵沒履歷、沒講過 → 不知道住哪')
check(D._residence_known(dict(c0, conversation=[{'role': 'candidate', 'content': '我住台中北屯'}])), '人選講過「我住台中北屯」→ 知道')
check(D._job_loc_text(c0) == '台中市后里區', '問句裡的職缺地點')
check(D._cross_county_job({'job': {'client_screen_conditions': '需可配合依專案調派至桃竹苗、台中、雲林'}}), '跨縣市調派條件認得出來')

print('\n全部通過' if not fail else f'\n{fail} 項失敗'); sys.exit(1 if fail else 0)
