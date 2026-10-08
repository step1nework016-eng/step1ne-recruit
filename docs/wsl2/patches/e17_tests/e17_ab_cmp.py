import json, glob, difflib, os, sys
B = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out/planab')[0]
TH = 0.45
def qs(path): return [x['q'] for x in json.load(open(path, encoding='utf-8')).get('questions', []) if x.get('q')]
def best(q, pool): return max((difflib.SequenceMatcher(None, q, p).ratio() for p in pool), default=0)
def overlap(a, b):
    """a 裡有幾題在 b 裡找得到相近的題目（ratio>=TH）"""
    return sum(1 for q in a if best(q, b) >= TH)
names = sorted({os.path.basename(p).rsplit('_', 1)[0] for p in glob.glob(B + '/*_A1.json')})
tot = {'noise': [], 'diff': []}
for n in names:
    a1, a2, b = (qs(f'{B}/{n}_{t}.json') for t in ('A1', 'A2', 'B'))
    n_noise = overlap(a1, a2) / len(a1); n_diff1 = overlap(a1, b) / len(a1); n_diff2 = overlap(a2, b) / len(a2)
    tot['noise'].append(n_noise); tot['diff'] += [n_diff1, n_diff2]
    print(f'\n=== {n}：有測驗 {len(a1)}/{len(a2)} 題；無測驗 {len(b)} 題')
    print(f'  同樣有測驗跑兩次（純雜訊）：A1 的 {overlap(a1,a2)}/{len(a1)} 題在 A2 找得到相近題（{n_noise:.0%}）')
    print(f'  拿掉測驗結果：A1 的 {overlap(a1,b)}/{len(a1)}（{n_diff1:.0%}）、A2 的 {overlap(a2,b)}/{len(a2)}（{n_diff2:.0%}）在無測驗版找得到相近題')
    only_b = [q for q in b if best(q, a1) < TH and best(q, a2) < TH]
    only_a = [q for q in a1 if best(q, b) < TH and best(q, a2) < TH]
    print(f'  只有「無測驗版」才有、兩次有測驗版都沒有的題 {len(only_b)} 題：')
    for q in only_b: print('    +', q[:90])
    print(f'  只有「有測驗版 A1」才有、A2 與無測驗版都沒有的題 {len(only_a)} 題（這些是 A1 自己的雜訊）：')
    for q in only_a: print('    -', q[:90])
    # 有沒有和人格測驗相關的題（盡責／壓力／換工作節奏／恆毅）
    kw = ('壓力', '堅持', '換工作', '穩定', '持續', '收尾', '條理', '挫折', '長期', '熱忱')
    ka, kb = sum(any(k in q for k in kw) for q in a1 + a2) / 2, sum(any(k in q for k in kw) for q in b)
    print(f'  人格相關關鍵字題數（有測驗平均 / 無測驗）：{ka:.1f} / {kb}')
print(f"\n平均：雜訊基準 {sum(tot['noise'])/len(tot['noise']):.0%}；拿掉測驗結果 {sum(tot['diff'])/len(tot['diff']):.0%}")
