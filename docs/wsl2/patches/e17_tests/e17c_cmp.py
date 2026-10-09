"""E17c 回放比較：完整素材 vs 拿掉測驗＋初篩（= 交卷前／初篩前就擬）。
雜訊基準＝同樣素材跑兩次（A1 vs A2）。另外看：初篩事先想好的追問題，在有初篩版／無初篩版的計畫裡各被覆蓋幾題。"""
import json, glob, difflib, os, sys, subprocess
B = sys.argv[1]
TH = 0.45
os.chdir(os.path.expanduser('~/claude-projects/工作流程技能包/step1ne-recruit/nightly_pipeline'))
def dq(sql): return json.loads(subprocess.run(['python3', 'd1q.py', 'q', sql], capture_output=True, text=True).stdout)
def qs(path): return [x['q'] for x in json.load(open(path, encoding='utf-8')).get('questions', []) if x.get('q')]
def best(q, pool): return max((difflib.SequenceMatcher(None, q, p).ratio() for p in pool), default=0)
def overlap(a, b): return sum(1 for q in a if best(q, b) >= TH)
names = sorted({os.path.basename(p).rsplit('_', 1)[0] for p in glob.glob(B + '/*_B.json')})
tot = {'noise': [], 'diff': [], 'scr_a': [], 'scr_b': []}
for n in names:
    a1, a2, b = (qs(f'{B}/{n}_{t}.json') for t in ('A1', 'A2', 'B'))
    nn = overlap(a1, a2) / len(a1); d1_ = overlap(a1, b) / len(a1); d2_ = overlap(a2, b) / len(a2)
    tot['noise'].append(nn); tot['diff'] += [d1_, d2_]
    print(f'\n=== {n}：完整素材 {len(a1)}/{len(a2)} 題；拿掉測驗＋初篩 {len(b)} 題')
    print(f'  同素材跑兩次（雜訊基準）：{overlap(a1, a2)}/{len(a1)}（{nn:.0%}）')
    print(f'  拿掉測驗＋初篩：A1 的 {overlap(a1, b)}/{len(a1)}（{d1_:.0%}）、A2 的 {overlap(a2, b)}/{len(a2)}（{d2_:.0%}）在無版找得到相近題')
    only_a = [q for q in a1 if best(q, b) < TH and best(q, a2) >= TH]
    only_b = [q for q in b if best(q, a1) < TH and best(q, a2) < TH]
    print(f'  兩次完整版都有、拿掉後沒有的題 {len(only_a)} 題（這些才是真的「少了初篩／測驗」的差異）：')
    for q in only_a: print('    -', q[:100])
    print(f'  只有拿掉版才有的題 {len(only_b)} 題')
    # 初篩原本想好的追問
    r = dq(f"SELECT s.questions FROM screenings s JOIN applications a ON a.id=s.application_id WHERE a.name='{n}' ORDER BY s.id DESC LIMIT 1")
    try: sq = json.loads(r[0]['questions'] or '[]') if r else []
    except Exception: sq = []
    if sq:
        ca = (overlap(sq, a1) + overlap(sq, a2)) / 2; cb = overlap(sq, b)
        tot['scr_a'].append(ca / len(sq)); tot['scr_b'].append(cb / len(sq))
        print(f'  初篩事先想好的 {len(sq)} 題追問，被計畫覆蓋：完整版平均 {ca:.1f} 題、拿掉版 {cb} 題')
        for q in sq: print('     · ', q[:80])
print(f"\n平均：雜訊基準 {sum(tot['noise'])/len(tot['noise']):.0%}；拿掉測驗＋初篩 {sum(tot['diff'])/len(tot['diff']):.0%}")
if tot['scr_a']:
    print(f"初篩追問被覆蓋比例：完整版 {sum(tot['scr_a'])/len(tot['scr_a']):.0%}；拿掉版 {sum(tot['scr_b'])/len(tot['scr_b']):.0%}")
