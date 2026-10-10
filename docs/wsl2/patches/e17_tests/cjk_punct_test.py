"""cjk_punct 單元測試（E24）。"""
import os, sys
sys.path.insert(0, os.getcwd())
from cjk_punct import fix_cjk_punct as F, deep_fix, count_halfwidth

fails = []
def check(name, got, want):
    ok = got == want
    print(('  ✅ ' if ok else '  ❌ ') + name + ('' if ok else f'  ｜得到 {got!r}，應為 {want!r}'))
    if not ok: fails.append(name)

# 該換的
check('逗號', F('好,了解,這部分您還沒有實際參與過。'), '好，了解，這部分您還沒有實際參與過。')
check('問號', F('您通勤方便嗎?大概要花多久時間?'), '您通勤方便嗎？大概要花多久時間？')
check('冒號', F('先確認一個比較正式的問題:您目前這份工作'), '先確認一個比較正式的問題：您目前這份工作')
check('分號與驚嘆號', F('可以;沒問題!'), '可以；沒問題！')
check('逗號後面多一個空白＋中文 → 一併拿掉空白', F('好, 了解'), '好，了解')
check('前面是英文、後面是中文也換', F('OK,好的'), 'OK，好的')
check('45K,可談', F('期望45K,可談'), '期望45K，可談')
check('結尾引號後的問號', F('您說的「競業」?'), '您說的「競業」？')
check('表情符號前的問號', F('好嗎?👍'), '好嗎？👍')
check('換行混合', F('第一句,\n第二句?'), '第一句，\n第二句？')
# 不該動的
check('時間 10:30', F('我們約10:30見面'), '我們約10:30見面')
check('比例 5:3', F('比例是5:3'), '比例是5:3')
check('小數／千分位', F('約3.5年、預算1,000萬'), '約3.5年、預算1,000萬')
check('網址整段不動', F('請看 https://step1ne.com/apply?job=bim&x=1 這個頁面'), '請看 https://step1ne.com/apply?job=bim&x=1 這個頁面')
check('網址後接中文逗號照常', F('網址是https://a.com/x,好嗎?'), '網址是https://a.com/x,好嗎？')
check('整句英文不動', F('Hello, how are you? Fine: thanks!'), 'Hello, how are you? Fine: thanks!')
check('已經是全形不動', F('好，了解。您方便嗎？'), '好，了解。您方便嗎？')
check('空字串／None／非字串', (F(''), F(None), F(5)), ('', None, 5))
check('冪等', F(F('好,了解?')), '好，了解？')
# 深層
check('deep_fix：dict／list 裡的字串', deep_fix({'q': ['您好,請問?', {'a': '可以:是'}], 'n': 3}), {'q': ['您好，請問？', {'a': '可以：是'}], 'n': 3})
check('count_halfwidth', (count_halfwidth('好,了解?'), count_halfwidth('好，了解？'), count_halfwidth('10:30 https://a.com/x?y')), (2, 0, 0))

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
