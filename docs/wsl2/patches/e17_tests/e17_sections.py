import os, re, sys, glob
B = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out')[0]
d = sys.argv[1]; names = sys.argv[2:]
for n in names:
    t = open(f'{B}/{d}/{n}_after.md', encoding='utf-8').read()
    m = re.search(r'(?:^|\n)#*\s*這次沒問到的地方.*?\n(.*?)(?=\n#{1,3} |\n建議|\n【|\Z)', t, re.S)
    print(f'\n===== {n}（{d}）=====')
    print(m.group(1).strip()[:2500] if m else '⚠️ 找不到新段落')
