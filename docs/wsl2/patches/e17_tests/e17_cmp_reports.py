import os, re, sys, glob, json, difflib
D = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out/replay')[0]
names = sorted({os.path.basename(p).rsplit('_', 1)[0] for p in glob.glob(D + '/*_after.md')})
show = sys.argv[1:] or names
def heads(t): return [l.strip() for l in t.split('\n') if l.startswith('## ')]
def section(t, key='這次沒問到的地方'):
    m = re.search(r'^## ' + key + r'.*?$(.*?)(?=^## |\Z)', t, re.S | re.M)
    return m.group(1).strip() if m else None
for n in names:
    a = open(f'{D}/{n}_after.md', encoding='utf-8').read()
    bp = f'{D}/{n}_before.md'
    b = open(bp, encoding='utf-8').read() if os.path.exists(bp) else None
    ma = json.load(open(f'{D}/{n}_after.md.meta'));
    print(f'\n=== {n}｜改後 {ma["secs"]:.0f}s {ma["chars"]}字' + (f'｜改前 {json.load(open(bp + ".meta"))["secs"]:.0f}s {len(b)}字' if b else '｜（無改前）'))
    ha, hb = heads(a), heads(b) if b else []
    if b:
        only_a = [h for h in ha if h not in hb]; only_b = [h for h in hb if h not in ha]
        print('  標題差異：改後多', only_a, '｜改後少', only_b)
        # 非新增段落的逐段相似度（模型本身有隨機性，這只是看有沒有被擠壞）
        def secs(t):
            parts = re.split(r'^(## .*)$', t, flags=re.M); return {parts[i].strip(): parts[i+1].strip() for i in range(1, len(parts) - 1, 2)}
        sa, sb = secs(a), secs(b)
        sims = {h: difflib.SequenceMatcher(None, sa[h], sb[h]).ratio() for h in sa if h in sb}
        low = {h[:18]: round(v, 2) for h, v in sims.items() if v < 0.5}
        print(f'  共同段落 {len(sims)} 個，平均相似度 {sum(sims.values())/max(1,len(sims)):.2f}；相似度<0.5 的：{low or "無"}')
    sec = section(a)
    print('  【改後新增段落】\n' + ('\n'.join('    ' + l for l in sec.split('\n')) if sec else '    ⚠️ 沒有這一段'))
