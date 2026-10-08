import os, re, glob, sys
B = glob.glob('/mnt/c/Users/haoli/AppData/Local/Temp/claude/*/ba608703-7b36-4d2a-bfd8-d5af8c2c7d76/scratchpad/e17_out')[0]
# 報告有兩種版式：## 標題（markdown）與純文字小標。必要段落改用「整份文字有沒有出現」判斷。
REQ = {'職缺匹配總結': '職缺匹配總結', '適合的職務方向': '適合的職務方向', '其他推薦職缺': '其他推薦職缺', '建議': '建議',
       '基本資料': '基本資料', '硬條件': '硬條件', '履歷vs口述': '履歷 vs 口述', '他問了什麼': '他問了什麼',
       '阿財問過的方向': '阿財問過的方向', '追問事項': '追問'}
for d in (sys.argv[1:] or ['replay', 'replay_v2']):
    print(f'\n##### {d}')
    for p in sorted(glob.glob(f'{B}/{d}/*.md')):
        t = open(p, encoding='utf-8').read()
        miss = [k for k, v in REQ.items() if v not in t]
        fmt = 'markdown標題' if len(re.findall(r'^## ', t, re.M)) >= 8 else '純文字小標'
        nc = '有' if '這次沒問到的地方' in t else '無'
        print(f"{os.path.basename(p):<20}{len(t):>6}字｜版式：{fmt}｜新段落：{nc}｜缺：{miss or '無'}")
