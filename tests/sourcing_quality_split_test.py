#!/usr/bin/env python3
"""2026-10-28 甲2：必要條件切法（硬條件不因黏到軟能力被放水、人格特質歸電話確認）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sourcing_quality as Q
bad = 0


def chk(n, c):
    global bad
    print(('✅' if c else '❌'), n)
    bad += (not c)


def kinds(job):
    return {c['text']: c['kind'] for c in Q.job_conditions(job)}


# ① ['…','…'] 清單格式要拆開；半導體監工經驗是硬條件，不能因為同一串有「溝通協調」就變電話確認
semi = {'title': '資深半導體專案工程師', 'required_conditions': '1.基礎文書能力。\n2.與公司內部及協力廠商溝通協調、團隊合作之能力。\n'
        '3.能依公司現有專案地點配合調派。\n4.具備普通重機車駕照，方便日常通勤。(外縣市同仁提供宿舍，距廠區通常不遠)',
        'must_skills': "['半導體廠房/廠辦監工、機電類型相關經驗', '基礎文書能力', '與公司內部及協力廠商溝通協調、團隊合作之能力']"}
k = kinds(semi)
chk('清單格式拆開：監工經驗是獨立一條', '半導體廠房/廠辦監工、機電類型相關經驗' in k)
chk('監工經驗＝要證據', k.get('半導體廠房/廠辦監工、機電類型相關經驗') == 'evidence')
chk('溝通協調＝電話確認', k.get('與公司內部及協力廠商溝通協調、團隊合作之能力') == 'phone')
chk('編號前綴「1.」拿掉', '基礎文書能力' in k and not any(t.startswith('1.') for t in k))
chk('「（外縣市同仁提供宿舍…）」福利說明不算條件', not any('宿舍' in t and t.startswith('(') for t in k))
chk('JSON 雙引號清單也拆', len(Q._split('["無經驗可（提供培訓）", "基礎文書能力", "自小客車駕照"]')) == 3)

# ② 同一句「硬＋軟」用「，且」黏在一起：拆成兩條
bim = {'title': 'BIM 工程師', 'required_conditions': '基本的 AutoCAD 或 Revit 繪圖能力，且有強烈的學習意願'}
k = kinds(bim)
chk('AutoCAD／Revit 繪圖能力＝要證據（不因「學習意願」被放水）', k.get('基本的 AutoCAD 或 Revit 繪圖能力') == 'evidence')
chk('學習意願＝電話確認', k.get('有強烈的學習意願') == 'phone')

# ③ 家庭司機：人格特質全部電話確認，改要求「真的開過車／當過司機」的核心工作證據
drv = {'title': '主管／家庭專屬司機', 'main_duties': '老闆、主管及家人日常接送',
       'required_conditions': '駕駛技術佳、具安全駕駛意識；能管理時間並規劃路線與行程；具基本車輛保養與維修常識；'
                              '溝通良好、服務態度佳並重視保密；遇壓力或突發狀況能冷靜應對；以中文為工作語言；可提供良民證（警察刑事紀錄證明）'}
cs = Q.job_conditions(drv)
chk('司機：除了核心工作，其他全部電話確認', all(c['kind'] == 'phone' for c in cs if c['src'] != 'core'))
chk('司機：補一條「實際做過核心工作」要證據', any(c['src'] == 'core' and c['kind'] == 'evidence' for c in cs))
# 只對到核心工作、人格特質沒寫 → 應該是 B（不是 C）
core_no = next(c['no'] for c in cs if c['src'] == 'core')
g, _, _, _ = Q.enforce_grade({'grade': 'B', 'fit_score': 70, 'function_match': True,
                              'must_check': [{'no': core_no, 'status': 'met'}]}, cs)
chk('司機：做過司機、特質沒寫 → B', g == 'B')

# ④ 儲備／無經驗可：條件全是電話問的，不補「做過核心工作」
tr = {'title': '半導體專案工程師(儲備培訓)', 'required_conditions': '1.基礎文書能力。\n2.具備普通重機車駕照'}
chk('儲備職缺不補核心工作條件', not any(c['src'] == 'core' for c in Q.job_conditions(tr)))
chk('完全沒填條件的職缺照舊補核心工作', any(c['src'] == 'core' for c in Q.job_conditions({'title': 'BIM 工程師(儲備培訓)'})))
chk('「經驗：無經驗可」不是條件', not any('無經驗' in c['text'] for c in Q.job_conditions(
    {'title': '半導體廠專案監工(無經驗可)', 'required_conditions': '經驗：無經驗可\n其他：具基礎文書能力、有自小客車駕照'})))

# ⑤ 不能放水：真的硬條件還是要證據
fin = {'title': '財務主管', 'required_conditions': '5年以上財務／會計經驗；熟悉財務報表、預算管理及資金規劃；具英文能力'}
k = kinds(fin)
chk('年資＝要證據', k.get('5年以上財務／會計經驗') == 'evidence')
chk('財報預算＝要證據', k.get('熟悉財務報表、預算管理及資金規劃') == 'evidence')
chk('英文能力＝電話確認', k.get('具英文能力') == 'phone')
chk('頓號列舉不拆（同一條的幾個選項）',
    kinds({'title': 'PM', 'required_conditions': '2年以上企業內部系統導入、數位轉型、流程自動化專案經驗'}).get(
        '2年以上企業內部系統導入、數位轉型、流程自動化專案經驗') == 'evidence')

# ⑥ 半導體工程師：沒有監工經驗證據 → 最高 C（原本任何人都能 B）
cs = Q.job_conditions(semi)
g, _, _, _ = Q.enforce_grade({'grade': 'B', 'fit_score': 70, 'function_match': True, 'must_check': []}, cs)
chk('半導體工程師：監工經驗查不到 → C', g == 'C')
print(f'\n失敗 {bad} 項')
sys.exit(1 if bad else 0)
