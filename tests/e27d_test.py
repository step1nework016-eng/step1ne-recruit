#!/usr/bin/env python3
"""E27d：對談提示的硬規則「不准評論回答好壞」＋ _pace_hints 偵測評價詞（離線）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)
def conv(*pairs):
    return {'conversation': [{'role': 'assistant' if r == 'A' else 'candidate', 'content': t} for r, t in pairs], 'job': {'seniority': 'senior'}}

# 1. 對話一開始（assistant < 3 則）也要有硬規則
h = D._pace_hints(conv(('A', '您好，我是阿財。')))
chk('第一輪就帶「不准評論回答好壞」硬規則', '不准評論回答的好壞' in h and '禁止「問得滿完整的」' in h)
h = D._pace_hints({'conversation': []})
chk('空對話也帶規則、不丟例外', '不准評論回答的好壞' in h)

# 2. 評價詞偵測：E27c／羅文羿真實出現過的 6 句
for s in ['這幾點問得滿完整的。', '算是有抓到感覺的。', '差異講得滿清楚的。', '這個轉換率數字滿清楚的。', '很棒的經驗。', '您的想法很有條理。']:
    c = conv(('A', '想請教您的離職原因？'), ('C', '想換環境'), ('A', '了解。'), ('C', '好'), ('A', s), ('C', '嗯'))
    h = D._pace_hints(c)
    chk(f'偵測到評價句：{s}', '你上一輪出現了評價句' in h, h[-200:])

# 3. 不誤傷：重述事實、正常問句
for s in ['了解，您是主導過新店從零招募到15人。', '板橋過來40分鐘，通勤時間我記下來了。', '想請教您目前的預告期大概多久？', '好的，謝謝您撥時間。']:
    c = conv(('A', '您目前負責哪些工作？'), ('C', '營運'), ('A', '團隊有幾個人？'), ('C', '8個'), ('A', s), ('C', '嗯'))
    chk(f'不誤判：{s}', '你上一輪出現了評價句' not in D._pace_hints(c), D._pace_hints(c)[-150:])

# 4. 只看最近兩則；更早的評價不再提醒
c = conv(('A', '這幾點問得滿完整的。'), ('C', '嗯'), ('A', '您目前負責哪些工作？'), ('C', '營運'), ('A', '團隊有幾個人？'), ('C', '8個'), ('A', '想請教預告期？'), ('C', '一個月'))
chk('評價句已經是三輪前 → 不再提醒', '你上一輪出現了評價句' not in D._pace_hints(c))

# 5. 原有提示（起手式重複、問題預算）仍在
c = conv(*[x for i in range(5) for x in (('A', f'嗯嗯，了解。想請教第{i}題？'), ('C', '好'))])
h = D._pace_hints(c)
chk('原有的起手式重複提示仍在', '起手式重複' in h and '不准評論回答的好壞' in h)

# 6. 接進 talk_prompt
src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'interview_daemon.py'), encoding='utf-8').read()
chk('talk_prompt 仍接 _pace_hints', 'talk_prompt += _pace_hints(ctx)' in src)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
