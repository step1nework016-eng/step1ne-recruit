#!/usr/bin/env python3
"""2026-10-24 Phoebe 電洽補的 5 題＋公司名稱設定。離線、不呼叫模型。"""
import os, sys, time
sys.path.insert(0, os.getcwd())
import interview_daemon as D
bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)
D._EXTRAS_CACHE.update(at=time.time(), val={'bim-engineer-houli': {'company': '沒問就說弘昌', 'sites': '新竹科學園區、台中后里'}})
def ctx(job, app, *pairs):
    return {'job': job, 'application': app,
            'conversation': [{'role': 'assistant' if r == 'A' else 'candidate', 'content': t} for r, t in pairs]}
base = [('A', '您先前做什麼工作呢？'), ('C', '工廠'), ('A', '為什麼想轉做BIM？'), ('C', '有興趣'),
        ('A', '您住在哪裡？通勤方便嗎？'), ('C', '彰化，會租屋')]
h = D._technique_hints(ctx({'slug': 'bim-engineer-houli', 'seniority': 'junior'}, {'origin': 'quick_apply'}, *base))
for k in ('意願分數', '地點偏好', '別家面試', '最低可接受月薪', '面試時段'):
    chk(f'無經驗：提醒 {k}', k in h)
chk('無經驗：不問前職薪資', '目前／上一份薪資' not in h)
h2 = D._technique_hints(ctx({'slug': 'x', 'seniority': 'mid'}, {}, *base))
chk('一般職缺：問前職薪資、不問最低', '目前／上一份薪資' in h2 and '最低可接受月薪' not in h2)
chk('沒設多地點 → 不問地點偏好', '地點偏好' not in h2)
done = base + [('A', '意願大概幾分（1到10分）？'), ('C', '8'), ('A', '目前還有在面試其他公司嗎？'), ('C', '沒有'),
               ('A', '新竹跟台中比較想去哪？'), ('C', '台中'), ('A', '月薪最低可以接受多少？'), ('C', '38000'),
               ('A', '如果要約面試，哪幾天比較方便面試？'), ('C', '週三')]
h3 = D._technique_hints(ctx({'slug': 'bim-engineer-houli', 'seniority': 'junior'}, {'origin': 'quick_apply'}, *done))
chk('5 題都問過 → 不再提醒', not any(k in h3 for k in ('**意願分數**', '**地點偏好**', '**別家面試**', '**最低可接受月薪**', '**面試時段**')), h3[:400])
print('全部通過' if not bad else f'{bad} 項失敗'); sys.exit(1 if bad else 0)
