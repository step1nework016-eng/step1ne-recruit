"""阿財快速回覆按鈕（2026-10-21）測試。不連 D1、不碰真人選、不開任何 /chat/ 連結。

  python3 tests/acai_quick_replies_test.py

① 開關：test 只給姓名含「測試」、on 全部、off 都不給
② 封閉題（通勤／搬遷、到職日、是非確認、外語人選工作許可）→ 2～4 個按鈕
③ 開放題、薪資題、保護特徵、金額、客戶名、太長、只剩 1 個 → 一律不給
④ 接進 daemon：開關有開才在 prompt 加說明、存檔時 after_id 在阿財訊息之前、開關沒開 prompt 逐字不變
"""
import importlib.util, json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import acai_quick_replies as Q

fail = 0
def check(cond, msg):
    global fail
    print(('PASS ' if cond else 'FAIL ') + msg); fail += (not cond)


class FakeGuard:
    def hits(self, t):
        return ['某客戶'] if '某客戶' in str(t) else []

G = FakeGuard()

# ① 開關
check(Q.eligible('test', '王測試') and not Q.eligible('test', '王小明') and Q.eligible('on', '王小明')
      and not Q.eligible('off', '王測試') and not Q.eligible('weird', '王測試'), '開關：test／on／off')

# ② 封閉題 → 有按鈕
cases_ok = [
    ('您住新北中和，到內湖上班通勤可以嗎？', ['可以', '要看交通', '不太方便']),
    ('如果要搬到台中工作，您會考慮嗎？', ['會考慮', '要再想想', '不考慮搬家']),
    ('最快什麼時候可以到職呢？', ['隨時可以', '兩週內', '一個月內', '要再確認']),
    ('那我先幫您記下來，這樣理解對嗎？', ['對', '不太對，我補充']),
    ('目前在台灣的工作許可是什麼狀態呢？', ['已有工作許可', '申請中', '還沒有']),
]
for q, opts in cases_ok:
    out, why = Q.sanitize(opts, q, G)
    check(out == opts, f'封閉題給按鈕：{q} → {out}')

# 多於 4 個只留 4 個；重複、引號、空白清掉
out, _ = Q.sanitize(['「可以」', '可以', '  要看交通 ', '不太方便', '要再想想', '不行'], '通勤可以嗎？', G)
check(out == ['可以', '要看交通', '不太方便', '要再想想'], f'最多 4 個、去重去引號：{out}')
# AI 自己加的「我自己打字／其他」拿掉（前端會固定放一個虛線的）
out, _ = Q.sanitize(['可以', '不行', '我自己打字', '其他'], '通勤可以嗎？', G)
check(out == ['可以', '不行'], f'「我自己打字」「其他」由前端放，不重複：{out}')

# ③ 不給的情況
cases_no = [
    (['還好', '很好'], '可以聊聊您上一份工作主要在做什麼嗎', '不是問句（開放敘述題）'),
    (['五萬', '六萬', '七萬'], '您期望的月薪大概多少呢？', '薪資金額題'),
    (['可以', '不行'], '這個職缺薪資是 4 萬起，您可以接受嗎？', '薪資相關題（就算是是非題也不給）'),
    (['3萬', '4萬'], '大概多少比較合適？', '選項是金額'),
    (['已婚', '未婚'], '請問您目前的狀況？', '選項碰婚姻'),
    (['男', '女'], '方便問一下嗎？', '選項碰性別'),
    (['25歲以下', '26～35歲'], '方便問一下嗎？', '選項碰年齡'),
    (['本國籍', '外籍'], '方便問一下嗎？', '選項碰國籍'),
    (['有信仰', '沒有'], '方便問一下宗教嗎？', '題目碰宗教'),
    (['可以', '不行'], '您幾歲了呢？', '題目碰年齡'),
    (['去過某客戶', '沒去過'], '您有去過那邊嗎？', '選項出現客戶名'),
    (['可以'], '通勤可以嗎？', '只剩 1 個'),
    (['這是一個非常非常非常非常長的選項文字超過限制', '可以'], '通勤可以嗎？', '太長的選項整個不用'),
    ('可以', '通勤可以嗎？', '格式不是清單'),
    (None, '通勤可以嗎？', '沒給'),
    (['可以', '不行'], '您覺得自己最大的優點是什麼？', '開放題（什麼／為什麼／怎麼）'),
    (['很有熱忱', '普通'], '您對這份工作有熱忱嗎？', '自我評價誘導題'),
]
for opts, q, label in cases_no:
    out, why = Q.sanitize(opts, q, G)
    check(out == [], f'不給：{label}（{why}）')

# 結構化輸出擷取
check(Q.extract({'messages': ['a'], 'quick_replies': ['可以', '不行']}) == ['可以', '不行'], 'extract 讀 quick_replies')
check(Q.extract({'messages': ['a']}) is None and Q.extract(None) is None, 'extract 沒有就 None')
check('quick_replies' in Q.PROMPT_HINT and '薪' in Q.PROMPT_HINT and '年齡' in Q.PROMPT_HINT, 'prompt 說明有寫欄位與禁區')

# 存檔 SQL：after_id、JSON、單引號跳脫
sqls = []
Q.save('APP-1', 41, ["可以", "要看'交通"], lambda s: sqls.append(s) or [])
joined = '\n'.join(sqls)
check('acai_quick_replies' in joined and ', 41,' in joined and "要看''交通" in joined, '存檔 SQL 有 after_id 與跳脫')

# ④ 接進 daemon
spec = importlib.util.spec_from_file_location('daemon_qr', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)
D.d1 = lambda sql: []
D.d1_raw = lambda sql: {'meta': {}}
Q._flag_cache.update({'v': 'test', 't': 9e18})     # 不讀 D1
D._QR_GUARD.update({'g': G, 't': 9e18})            # 客戶名名單用假的，不讀 D1
app_t = {'id': 'TEST-QR', 'name': '王測試'}
app_n = {'id': 'REAL-QR', 'name': '王小明'}
check(D._quick_hint(app_t) == Q.PROMPT_HINT and D._quick_hint(app_n) == '', 'prompt 說明只加給符合開關的應徵')
Q._flag_cache.update({'v': 'off', 't': 9e18})
check(D._quick_hint(app_t) == '', '開關 off 時 prompt 不加任何字')
Q._flag_cache.update({'v': 'test', 't': 9e18})

calls = []
D.d1 = lambda sql: (calls.append(sql) or ([{'m': 57}] if 'MAX(id)' in sql else []))
Q._table_ok = False
n = D._save_quick_replies(app_t, {'messages': ['通勤可以嗎？'], 'quick_replies': ['可以', '不行'], 'end': False},
                          ['通勤可以嗎？'], already_reported=False)
check(n == 2 and any('CREATE TABLE IF NOT EXISTS acai_quick_replies' in s for s in calls)
      and any(', 57,' in s for s in calls), f'daemon 存按鈕，after_id＝寫入阿財訊息前的最後一則（{n}）')
calls.clear()
check(D._save_quick_replies(app_t, {'messages': ['謝謝您'], 'quick_replies': ['好'], 'end': True}, ['謝謝您'], False) == 0
      and not calls, '面談結束那輪不給')
check(D._save_quick_replies(app_t, {'messages': ['通勤可以嗎？'], 'quick_replies': ['可以', '不行']}, ['通勤可以嗎？'], True) == 0,
      '收尾後追問不給')
check(D._save_quick_replies(app_n, {'messages': ['通勤可以嗎？'], 'quick_replies': ['可以', '不行']}, ['通勤可以嗎？'], False) == 0,
      '不符合開關的應徵不給')
check(D._save_quick_replies(app_t, {'messages': ['原本的問題？'], 'quick_replies': ['可以', '不行']},
                            ['了解，那我們接著談工作內容的部分。'], False) == 0,
      '訊息被安全句換掉（不是 AI 原本那句）就不給')

src = open(os.path.join(HERE, 'interview_daemon.py'), encoding='utf-8').read()
check('_quick_hint(app)' in src and '_save_quick_replies(app, result, msgs' in src, 'handle() 有接上提示與存檔')

print(f'\n{"全部通過" if not fail else f"失敗 {fail} 項"}')
sys.exit(1 if fail else 0)
