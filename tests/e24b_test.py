#!/usr/bin/env python3
"""E24b：轉場句併成同一個起手式鍵（「那我們換個方向」連用 3 次要抓得到）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c):
    global bad
    print(('✅' if c else '❌'), n); bad += (not c)
for t in ['那我們換個方向，聊聊薪資', '好，那我們換個方向來看看', '換個主題吧', '我們換一下角度，']:
    chk('轉場句併鍵：' + t, D._opening_of(t) == '換個方向')
chk('一般起手式不受影響', D._opening_of('嗯嗯，了解') == '嗯嗯' and D._opening_of('OK，繼續') == 'OK')
conv = [{'role': 'assistant', 'content': c} for c in ['你好，先自我介紹', '那我們換個方向，談薪資？', '好，那我們換個方向，聊離職動機', '那我們換個主題，說說團隊？']]
h = D._pace_hints({'conversation': conv, 'job': {'seniority': 'senior'}})
chk('連用 3 次轉場句 → 提示', '換個方向' in h and '×3' in h)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
