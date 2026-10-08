import json, glob, difflib
B = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out/planab')[0]
def qs(p): return [x['q'] for x in json.load(open(p, encoding='utf-8')).get('questions', []) if x.get('q')]
best = lambda q, pool: max(difflib.SequenceMatcher(None, q, p).ratio() for p in pool)
for n in ('楊政翰', '林博祥'):
    a1, a2, b = (qs(f'{B}/{n}_{t}.json') for t in ('A1', 'A2', 'B'))
    print(f'\n== {n}：有測驗版（A1、A2 都有）但無測驗版沒有的題')
    for q in a1:
        if best(q, b) < 0.45 and best(q, a2) >= 0.45: print('  -', q[:100])
    # 測驗提示詞段落是否影響措辭：看 why 欄位有沒有提到測驗／人格
    whys = [x.get('why', '') for t in ('A1', 'A2') for x in json.load(open(f'{B}/{n}_{t}.json', encoding='utf-8')).get('questions', [])]
    hit = [w for w in whys if any(k in w for k in ('測驗', '人格', '盡責', '恆毅', 'Big', 'grit', 'Grit'))]
    print(f'  有測驗版的「想確認什麼」裡提到測驗／人格的有 {len(hit)}/{len(whys)} 題', hit[:3])
    bw = [x.get('why', '') for x in json.load(open(f'{B}/{n}_B.json', encoding='utf-8')).get('questions', [])]
    print(f'  無測驗版同樣提到的有 {sum(any(k in w for k in ("測驗","人格","盡責","恆毅","Big","grit","Grit")) for w in bw)}/{len(bw)} 題')
