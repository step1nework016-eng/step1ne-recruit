#!/usr/bin/env python3
"""阿財評分升級測試（2026-09-20）

驗的是四件事：
  1. 專業題的 skill_level 真的會影響總分（升級前是零貢獻）
  2. 沒問到的維度不當 0 分，分母按比例縮（原本就對，這裡上鎖避免以後改壞）
  3. 「依據不足」警語不會因為少一個硬條件就必定觸發
  4. 總分與推薦結論矛盾時會標出來

跑法：python3 tests/test_fit_scores.py
不碰資料庫、不呼叫 AI。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import interview_daemon as D  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  ← {detail}" if detail and not cond else ''))


def dim(name, score, ev='他的原話'):
    return {'name': name, 'score': score, 'evidence': ev, 'note': ''}


def build(dims, expertise=None, verdict='值得轉給顧問'):
    return D._normalize_report_json({
        'verdict': verdict,
        'fit_scores': {'dimensions': dims},
        'expertise_findings': expertise or [],
    }, name='測試人選')


def fs(out):
    return out['fit_scores']


# ── 1. 專業題會影響總分 ────────────────────────────────────────────
base = [dim('硬條件符合度', 8), dim('相關經驗深度', 8), dim('案例具體度', 8),
        dim('動機明確度', 8), dim('溝通清晰度', 8), dim('工作穩定度', 8)]

weak = build(base, [{'topic': 'AutoCAD', 'skill': 'AutoCAD', 'skill_level': 1,
                     'evidence': '就…我會用啊', 'depth': '籠統'}])
strong = build(base, [{'topic': 'AutoCAD', 'skill': 'AutoCAD', 'skill_level': 5,
                       'evidence': '那時候為了趕圖我們放棄了圖層標準，代價是後面重畫兩週',
                       'depth': '具體'}])

check('1a 專業題答得淺 vs 答得深，總分不一樣（升級前完全一樣）',
      fs(weak)['total'] != fs(strong)['total'],
      f"淺={fs(weak)['total']} 深={fs(strong)['total']}")
check('1b 答得深的分數比較高', fs(strong)['total'] > fs(weak)['total'],
      f"淺={fs(weak)['total']} 深={fs(strong)['total']}")
check('1c 1 級 → 專業技能深度 2/10',
      next(d for d in fs(weak)['dimensions'] if d['name'] == '專業技能深度')['score'] == 2)
check('1d 5 級 → 專業技能深度 10/10',
      next(d for d in fs(strong)['dimensions'] if d['name'] == '專業技能深度')['score'] == 10)

multi = build(base, [
    {'topic': 'AutoCAD', 'skill': 'AutoCAD', 'skill_level': 2, 'evidence': 'a'},
    {'topic': 'Revit', 'skill': 'Revit', 'skill_level': 4, 'evidence': 'b'},
])
sk = next(d for d in fs(multi)['dimensions'] if d['name'] == '專業技能深度')
check('1e 多題取平均（2級與4級 → 平均3級 → 6/10）', sk['score'] == 6, str(sk['score']))
check('1f note 要列出每個技能各幾級（顧問要看得到細項）',
      'AutoCAD' in sk['note'] and 'Revit' in sk['note'], sk['note'])

# ⚠️ 「沒問到」不可以被當成 1 級——那是把沒問到的技能誣賴成不會
notasked = build(base, [{'topic': 'Revit', 'skill': 'Revit', 'skill_level': None,
                         'evidence': ''}])
check('1g 沒問到的題目不計入，專業技能深度維持未評分',
      next(d for d in fs(notasked)['dimensions'] if d['name'] == '專業技能深度')['score'] is None)

# ── 2. 缺的維度不當 0 分 ──────────────────────────────────────────
full = build([dim(n, 6) for n, _ in
              [('硬條件符合度', 0), ('專業技能深度', 0), ('相關經驗深度', 0), ('案例具體度', 0),
               ('動機明確度', 0), ('溝通清晰度', 0), ('工作穩定度', 0)]])
partial = build([dim('相關經驗深度', 6), dim('案例具體度', 6), dim('動機明確度', 6),
                 dim('溝通清晰度', 6), dim('工作穩定度', 6)])
check('2a 每維都 6 分 → 總分 60', fs(full)['total'] == 60, str(fs(full)['total']))
check('2b 缺兩維但其餘一樣 6 分 → 仍是 60，不被當 0 分拖累',
      fs(partial)['total'] == 60, str(fs(partial)['total']))
check('2c basis 要指名缺了哪幾維（顧問才判斷得出重不重要）',
      '硬條件符合度' in fs(partial)['basis'] and '專業技能深度' in fs(partial)['basis'],
      fs(partial)['basis'])

# 沒有 evidence 的分數不算數
noev = build([dim('硬條件符合度', 9, ev=''), dim('相關經驗深度', 6)])
check('2d 沒有引用當證據的分數一律不算',
      next(d for d in fs(noev)['dimensions'] if d['name'] == '硬條件符合度')['score'] is None)

# ── 3. 警語不再因為少一個硬條件就必定觸發 ───────────────────────────
# 周亦宣的真實情境：未綁定職缺 → 硬條件無法比對，其餘六維都有
no_hard = build([dim('專業技能深度', 6), dim('相關經驗深度', 6), dim('案例具體度', 6),
                 dim('動機明確度', 6), dim('溝通清晰度', 6), dim('工作穩定度', 6)])
check('3a 只缺硬條件（周亦宣情境）→ 不再掛「依據不足」',
      '依據不足' not in fs(no_hard)['grade'], fs(no_hard)['grade'])
check('3b 但 basis 仍要講清楚硬條件沒評',
      '硬條件符合度' in fs(no_hard)['basis'], fs(no_hard)['basis'])

# 真的只有一半資料時還是要示警
half = build([dim('硬條件符合度', 8), dim('專業技能深度', 8), dim('相關經驗深度', 8)])
check('3c 真的只剩一半資料 → 仍要掛「依據不足」',
      '依據不足' in fs(half)['grade'], f"used={fs(half)['basis']} grade={fs(half)['grade']}")

# 低於 50 權重不給分
scarce = build([dim('相關經驗深度', 8), dim('案例具體度', 8)])
check('3d 依據權重低於 50 → 不給總分',
      fs(scarce)['total'] is None and fs(scarce)['grade'] == '資料不足無法評分')

# ── 4. 分數與結論矛盾要標出來 ──────────────────────────────────────
bad1 = build([dim(n, 2) for n in ('硬條件符合度', '專業技能深度', '相關經驗深度',
                                  '案例具體度', '動機明確度')], verdict='值得轉給顧問')
check('4a 結論說值得轉但分數很低 → 標記矛盾',
      bool(fs(bad1)['score_verdict_conflict']), str(fs(bad1)['score_verdict_conflict']))

bad2 = build([dim(n, 9) for n in ('硬條件符合度', '專業技能深度', '相關經驗深度',
                                  '案例具體度', '動機明確度')], verdict='硬條件不符')
check('4b 結論說硬條件不符但分數很高 → 標記矛盾',
      bool(fs(bad2)['score_verdict_conflict']), str(fs(bad2)['score_verdict_conflict']))

bad3 = build([dim('相關經驗深度', 8)], verdict='值得轉給顧問')
check('4c 算不出總分卻說值得轉 → 也要標記',
      bool(fs(bad3)['score_verdict_conflict']), str(fs(bad3)['score_verdict_conflict']))

ok = build(base, [{'topic': 'x', 'skill': 'x', 'skill_level': 4, 'evidence': 'e'}],
           verdict='值得轉給顧問')
check('4d 分數高且結論一致 → 不標記',
      fs(ok)['score_verdict_conflict'] is None, str(fs(ok)['score_verdict_conflict']))

# ── 5. 權重總和必須是 100，不然總分會失真 ──────────────────────────
check('5a 七維權重加起來是 100',
      sum(d['weight'] for d in fs(full)['dimensions']) == 100,
      str(sum(d['weight'] for d in fs(full)['dimensions'])))

print(f'\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項')
if FAIL:
    print('失敗：', FAIL)
sys.exit(1 if FAIL else 0)
