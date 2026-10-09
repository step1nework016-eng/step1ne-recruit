#!/usr/bin/env python3
"""acai_watch 的判斷邏輯測試（全部假資料，不連 D1、不發 TG）。"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acai_watch as W  # noqa: E402

T0 = datetime.datetime(2026, 10, 9, 15, 0, 0)
F = lambda dt: dt.strftime(W.FMT)
M = lambda mins: T0 + datetime.timedelta(minutes=mins)
calls = []
FIX_OK = {'daemon_active': lambda: True, 'restart': lambda: calls.append('restart') or True,
          'clear_lock': lambda aid, now: calls.append('clear:' + aid)}
FIX_DEAD = dict(FIX_OK, daemon_active=lambda: False)
app = lambda **k: dict({'id': 'A1', 'name': '假人選', 'job_title': '假職缺', 'phone': '0912', 'interview_state': 'active',
                        'interview_ended_at': None, 'lock_expires_at': None}, **k)
mid = [0]


def msg(role, text, at):
    mid[0] += 1
    return {'id': mid[0], 'role': role, 'content': text, 'created_at': F(at)}


fails = []


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


kinds = lambda acts: [a[0] for a in acts]

# 1 進入面談室標記不算開口；真的講話才 🟢，且只一次
ms = [msg('candidate', W.ENTRY_MARKER, M(0)), msg('assistant', '您好', M(0))]
acts, st = W.decide(M(1), [app()], {'A1': ms}, {}, FIX_OK)
check('進入面談室標記不觸發開始', 'start' not in kinds(acts))
ms.append(msg('candidate', '你好', M(1)))
acts, st = W.decide(M(1), [app()], {'A1': ms}, st, FIX_OK)
check('第一次開口 → 🟢 一則', kinds(acts) == ['start'])
ms.append(msg('assistant', '請問', M(2)))
acts, st = W.decide(M(3), [app()], {'A1': ms}, st, FIX_OK)
check('再跑一次不重複通知', acts == [])

# 2 太久沒回：<3 分不報、≥3 分報、10 分內不重複、10 分後再提醒、阿財回了就停
ms.append(msg('candidate', '薪資多少？', M(10)))
acts, st = W.decide(M(12), [app()], {'A1': ms}, st, FIX_OK)
check('2 分鐘不報', acts == [])
acts, st = W.decide(M(13), [app()], {'A1': ms}, st, FIX_OK)
check('3 分鐘 → 🚨', kinds(acts) == ['slow'] and acts[0][3] == 3)
txt = W.render(acts[0])
check('警報含姓名／職缺／電話／卡片連結', all(s in txt for s in ('假人選', '假職缺', '0912', 'app=A1')))
acts, st = W.decide(M(15), [app()], {'A1': ms}, st, FIX_OK)
check('10 分鐘內不重複', acts == [])
acts, st = W.decide(M(23), [app()], {'A1': ms}, st, FIX_OK)
check('滿 10 分鐘再提醒（第 2 次）', kinds(acts) == ['slow'] and acts[0][4] == 2)
ms.append(msg('assistant', '好的', M(24)))
acts, st = W.decide(M(34), [app()], {'A1': ms}, st, FIX_OK)
check('阿財回了就停', acts == [])

# 3 最多 MAX_ALERTS 次
st2 = {}
ms2 = [msg('candidate', '哈囉', M(0))]
n = 0
for k in range(0, 200, 2):
    acts, st2 = W.decide(M(k), [app(id='B1')], {'B1': ms2}, st2, FIX_OK)
    n += sum(1 for a in acts if a[0] == 'slow')
check(f'最多提醒 {W.MAX_ALERTS} 次（實際 {n}）', n == W.MAX_ALERTS)

# 4 自動修：阿財沒在跑 → 重啟；鎖過期 → 清；通知有寫；只修一次
calls.clear()
ms3 = [msg('candidate', '在嗎', M(0))]
a3 = app(id='C1', lock_expires_at=F(M(-5)))
acts, st3 = W.decide(M(4), [a3], {'C1': ms3}, {}, FIX_DEAD)
sl = [a for a in acts if a[0] == 'slow']
check('重啟＋清鎖', calls == ['restart', 'clear:C1'] and len(sl[0][5]) == 2)
check('通知說明已自動處理', '🔧' in W.render(sl[0]))
calls.clear()
acts, st3 = W.decide(M(15), [a3], {'C1': ms3}, st3, FIX_DEAD)
check('第二次提醒不再重複修', calls == [])
# 鎖沒過期不清
calls.clear()
acts, _ = W.decide(M(4), [app(id='C2', lock_expires_at=F(M(5)))], {'C2': [msg('candidate', 'x', M(0))]}, {}, FIX_OK)
check('鎖還有效不清', calls == [])

# 5 過渡語不算正式回覆（若 interview_markers 存在）
if W.MK:
    t = W.MK.TRANSITION_TEXTS[0]
    ms5 = [msg('candidate', '請問', M(0)), msg('assistant', t, M(1))]
    acts, _ = W.decide(M(5), [app(id='D1')], {'D1': ms5}, {}, FIX_OK)
    check('過渡語不算回覆，仍然警報', kinds(acts) == ['slow'])
else:
    print('SKIP 過渡語（interview_markers 尚未上線；邏輯已用 is_transition 預留）')

# 6 結束：active→done 通知一次；→paused 也通知；第一次看到就是 done 的舊面談不通知
ms6 = [msg('candidate', 'hi', M(0)), msg('assistant', 'ok', M(1))]
acts, st6 = W.decide(M(2), [app(id='E1')], {'E1': ms6}, {}, FIX_OK)
acts, st6 = W.decide(M(4), [app(id='E1', interview_state='done', interview_ended_at=F(M(3)))], {'E1': ms6}, st6, FIX_OK)
check('active→done → ✅', 'end' in kinds(acts) and '報告正在產生' in W.render(acts[-1]))
acts, st6 = W.decide(M(6), [app(id='E1', interview_state='done', interview_ended_at=F(M(3)))], {'E1': ms6}, st6, FIX_OK)
check('結束只通知一次', 'end' not in kinds(acts))
old = [msg('candidate', 'hi', M(-300)), msg('assistant', 'ok', M(-299))]
acts, _ = W.decide(M(0), [app(id='F1', interview_state='done', interview_ended_at=F(M(-280)))], {'F1': old}, {}, FIX_OK)
check('舊的已結束面談不補報', acts == [])
acts, st7 = W.decide(M(2), [app(id='G1')], {'G1': ms6}, {}, FIX_OK)
acts, _ = W.decide(M(4), [app(id='G1', interview_state='paused')], {'G1': ms6}, st7, FIX_OK)
check('active→paused 也報結束', 'end' in kinds(acts) and '離開' in W.render(acts[-1]))

# 7 第一次上線時，早就開始的舊面談不補報「開始」
old2 = [msg('candidate', 'hi', M(-100)), msg('assistant', 'ok', M(-99))]
acts, _ = W.decide(M(0), [app(id='H1')], {'H1': old2}, {}, FIX_OK)
check('舊面談不補報開始', 'start' not in kinds(acts))

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
