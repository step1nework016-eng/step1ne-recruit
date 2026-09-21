#!/usr/bin/env python3
"""阿財分流 A–E 測試（2026-09-21）

來源是 Jacky 的「阿財 × 真人顧問分工圖」第 5 步：阿財談完要把人分成
A 優先聯繫／B 需顧問判斷／C 可轉其他職缺／D 人才池／E Hard Gate Fail。

這裡鎖住的是**分流由程式推導、不由模型自由發揮**這件事——同一份報告
重跑兩次必須落在同一類，顧問才能拿它排今天要打的電話順序。

跑法：python3 tests/test_route_abcde.py
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


def dim(name, score):
    return {'name': name, 'score': score, 'evidence': '他的原話', 'note': ''}


ALL_DIMS = ['硬條件符合度', '專業技能深度', '相關經驗深度',
            '案例具體度', '動機明確度', '溝通清晰度', '工作穩定度']


def route(score=None, verdict='值得轉給顧問', hard_conditions=None, dims=None):
    """把七維都填同一個分數，做出一份可預期總分的報告。"""
    ds = dims if dims is not None else [dim(n, score) for n in ALL_DIMS]
    out = D._normalize_report_json({
        'verdict': verdict,
        'fit_scores': {'dimensions': ds},
        'hard_conditions': hard_conditions or [],
    }, name='測試人選')
    return out.get('route') or {}


def total(score=None, verdict='值得轉給顧問', dims=None):
    ds = dims if dims is not None else [dim(n, score) for n in ALL_DIMS]
    out = D._normalize_report_json(
        {'verdict': verdict, 'fit_scores': {'dimensions': ds}}, name='測試人選')
    return (out.get('fit_scores') or {}).get('total')


# ── 1. 五類都走得到 ───────────────────────────────────────────
print('\n【1】五類分流都要走得到')

r = route(score=9)
check('1a 高分且硬條件無不符 → A 優先聯繫',
      r.get('code') == 'A', f"拿到 {r}")

r = route(score=6)
check('1b 中間帶（50–64）→ B 需顧問判斷',
      r.get('code') == 'B', f"總分 {total(6)}，拿到 {r}")

r = route(score=3, verdict='硬條件不符')
check('1c 硬條件不符且分數低 → E Hard Gate 沒過',
      r.get('code') == 'E', f"總分 {total(3)}，拿到 {r}")

r = route(score=3)
check('1d 分數低但硬條件沒不符 → D 人才池',
      r.get('code') == 'D', f"總分 {total(3)}，拿到 {r}")

r = route(score=8, verdict='硬條件不符')
check('1e 硬條件不符但整體分數不差 → C 可轉其他職缺（不是直接出局）',
      r.get('code') == 'C', f"總分 {total(8, '硬條件不符')}，拿到 {r}")


# ── 2. hard_checks 也能觸發，不是只看 verdict ─────────────────
print('\n【2】hard_conditions 裡有「不符」一樣要擋下來')

r = route(score=9, hard_conditions=[{'item': '駕照', 'verdict': '不符', 'detail': ''}])
check('2a 模型說值得轉，但硬條件明細有「不符」→ 不得判 A',
      r.get('code') != 'A', f"拿到 {r}")
check('2b 且因為分數高，應該是 C 可轉其他職缺（不是 E 直接出局）',
      r.get('code') == 'C', f"拿到 {r}")


# ── 3. 資料不足不能被當成「不適合」 ───────────────────────────
print('\n【3】資料不足 → B 交給顧問，絕不可以掉進 D 人才池')

scarce = [dim('溝通清晰度', 7), dim('動機明確度', 6)]  # 權重合計 19，算不出總分
r = route(dims=scarce)
check('3a 權重不足算不出總分 → B 需顧問判斷',
      r.get('code') == 'B', f"總分 {total(dims=scarce)}，拿到 {r}")
check('3b 絕對不可以是 D（沒問到不等於不適合）',
      r.get('code') != 'D', f"拿到 {r}")

r = route(score=9, verdict='資訊不足建議補問')
check('3c 分數雖高，但阿財說還有關鍵沒問到 → B，不要直接 A 叫顧問打',
      r.get('code') == 'B', f"拿到 {r}")


# ── 4. 同一份報告要得到同一個分流 ─────────────────────────────
print('\n【4】可重現性（顧問要拿它排順序）')

codes = {route(score=9).get('code') for _ in range(5)}
check('4a 同樣輸入跑五次，分流一致', len(codes) == 1, f"拿到 {codes}")


# ── 5. 等第字母不可以再跟分流撞字 ─────────────────────────────
print('\n【5】分數等第已改中文，不得再出現 A/B/C/D')

out = D._normalize_report_json(
    {'verdict': '值得轉給顧問',
     'fit_scores': {'dimensions': [dim(n, 9) for n in ALL_DIMS]}}, name='測試人選')
g = (out.get('fit_scores') or {}).get('grade') or ''
check('5a 高分等第是「優」不是「A」', g.startswith('優'), f"拿到 {g!r}")
check('5b 等第字串裡不含 A–E 單一字母',
      not any(g.strip().startswith(x) for x in 'ABCDE'), f"拿到 {g!r}")

g_low = (D._normalize_report_json(
    {'verdict': '待顧問判斷',
     'fit_scores': {'dimensions': [dim(n, 3) for n in ALL_DIMS]}},
    name='測試人選').get('fit_scores') or {}).get('grade') or ''
check('5c 低分等第是「待加強」不是「D」', g_low.startswith('待加強'), f"拿到 {g_low!r}")


# ── 6. 每一類都要有理由，顧問看得到為什麼 ─────────────────────
print('\n【6】每一類都要附理由')

for label, r in [('A', route(score=9)), ('B', route(score=6)),
                 ('C', route(score=8, verdict='硬條件不符')),
                 ('D', route(score=3)),
                 ('E', route(score=3, verdict='硬條件不符'))]:
    check(f'6{label} {r.get("code")} 有 label 與 reason',
          bool(r.get('label')) and bool(r.get('reason')), f"拿到 {r}")


print(f"\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    print('失敗清單：' + '、'.join(FAIL))
sys.exit(1 if FAIL else 0)
