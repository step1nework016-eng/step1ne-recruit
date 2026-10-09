#!/usr/bin/env python3
"""E17b 上線前修改的測試：三句輪替、60 秒、一輪最多一句、電洽合併拿掉「這次沒問到的地方」。
用記憶體 sqlite 直接跑 interview_markers 產的 SQL（跟 daemon 送進 D1 的是同一句）。"""
import os
import random
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
sys.path.insert(0, ROOT)
import interview_markers as MK  # noqa: E402

fails = []


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


# 1 三句一字不差（網站面談頁用這三句判斷「正在輸入」）
check('三句文字一字不差', MK.TRANSITION_TEXTS == (
    '收到，我整理一下您剛剛說的，稍等我一下。',
    '好的，我看一下您剛剛提到的內容，馬上回您。',
    '了解，我想一下接著要聊什麼，請稍等一下。'))
check('三句都被認成過渡語', all(MK.is_transition(t) for t in MK.TRANSITION_TEXTS))

# 2 不連續兩次同一句
ok = True
last = None
rng = random.Random(7)
seen = set()
for _ in range(2000):
    t = MK.pick_transition(last, rng)
    if t == last:
        ok = False
    seen.add(t)
    last = t
check('2000 次挑選沒有連續重複', ok)
check('三句都有機會被挑到', len(seen) == 3)
check('第一次（沒有上一句）可挑任何一句', MK.pick_transition(None, random.Random(1)) in MK.TRANSITION_TEXTS)


# 3 一輪最多一句（不管上一句是哪一句）、進房標記不送、已回覆不送
def db():
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)')
    return c


def add(c, role, content):
    c.execute('INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)', ('A', role, content, '2026-10-09 12:00:00'))


def send(c, text=None):
    n0 = c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    c.execute(MK.sql_send_transition("'A'", "'2026-10-09 12:01:00'", text))
    return c.execute('SELECT COUNT(*) FROM messages').fetchone()[0] - n0


c = db(); add(c, 'candidate', '我想問薪資')
check('人選講完話 → 送出 1 句', send(c, MK.TRANSITION_TEXTS[1]) == 1)
check('同一輪再送（不管哪一句）→ 不送', send(c, MK.TRANSITION_TEXTS[0]) == 0 and send(c, MK.TRANSITION_TEXTS[2]) == 0)
add(c, 'assistant', '好的，薪資範圍是…'); add(c, 'candidate', '那工作地點呢')
check('正式回覆＋人選又講話（新的一輪）→ 可再送 1 句', send(c, MK.TRANSITION_TEXTS[2]) == 1)
c = db(); add(c, 'candidate', MK.ENTRY_MARKER)
check('只有進房標記 → 不送', send(c) == 0)
c = db(); add(c, 'candidate', '你好'); add(c, 'assistant', '您好，我是阿財')
check('最後一則已是正式回覆 → 不送', send(c) == 0)

# 4 daemon 設定
src = open(os.path.join(ROOT, 'interview_daemon.py'), encoding='utf-8').read()
check('daemon 門檻 60 秒', 'TRANSITION_AFTER_SEC = 60' in src)
check('daemon 送出時用 pick_transition 輪替', 'MK.pick_transition(' in src)

# 5 電洽合併拿掉「這次沒問到的地方」
sys.argv = ['x']
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location('ccr', os.path.join(ROOT, 'consultant_call_report.py'))
try:
    ccr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ccr)
    fn = ccr.strip_not_covered
except Exception as e:   # 模組載入會連 D1／環境，載不起來就單獨抽函式測
    import re as _re
    code = open(os.path.join(ROOT, 'consultant_call_report.py'), encoding='utf-8').read()
    m = _re.search(r'def strip_not_covered\(md\):.*?\n\n\ndef build', code, _re.S)
    ns = {}
    exec(m.group(0).rsplit('\n\n\ndef build', 1)[0], ns)
    fn = ns['strip_not_covered']
md = ('# 報告\n## 適合的職務方向\n- A\n## 這次沒問到的地方\n[沒問到] 管理規模\n[含糊沒追到] 業績\n### 小節\n內容\n## 建議\n錄用\n')
out = fn(md)
check('整段拿掉（含子標題）', '這次沒問到的地方' not in out and '管理規模' not in out and '小節' not in out)
check('前後段落完整保留', '## 適合的職務方向' in out and '- A' in out and '## 建議' in out and '錄用' in out)
check('沒有這段時原樣不動', fn('# 報告\n## 建議\n好\n') == '# 報告\n## 建議\n好\n')
check('這段在文末也拿得掉', '沒問到' not in fn('## 建議\n好\n## 📋 這次沒問到的地方\n[沒問到] x\n'))
check('空值安全', fn(None) == '' and fn('') == '')

print('\n失敗 %d 項' % len(fails))
sys.exit(1 if fails else 0)
