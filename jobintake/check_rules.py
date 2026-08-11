#!/usr/bin/env python3
"""比對「客戶對象連動規則」在三個地方有沒有一致。

## 為什麼要有這支

同一套規則目前寫在三份檔案裡：

  1. `jobintake/relation_rules.py`      — Python 端（擬 JD、發布）
  2. `src/index.js` 的 `RELATION`        — Worker 端（表單收件、後台改分類）
  3. `consultant/job-intake/index.html`  — 顧問看到的選項

**不一致不會報錯，只會安靜地做錯事。** 2026-08-10 就真的發生過：
Worker 的服務線寫 `permanent`，資料庫與職缺分類頁用的是 `direct`，
結果新職缺建出來分類欄位會是空的，而且沒有任何錯誤訊息。

這套規則決定「履歷具名或匿名、報告掛不掛品牌、要不要揭露 AI」——
弄錯就是客戶隱私外洩，所以寧可吵也不要安靜。

由 `tick.py` 每分鐘順手跑一次；不一致就寫進 log 並回傳非 0。

    python3 check_rules.py        # 自己手動跑
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECRUIT = os.path.dirname(HERE)
SITE = os.path.expanduser('~/下載項目/step1ne-stopgap-site')

KEYS = ('client_named', 'ai_disclosure', 'brand_mode')


def from_python():
    sys.path.insert(0, HERE)
    import relation_rules
    src = relation_rules.RELATION
    return {k: {f: v.get(f) for f in KEYS} for k, v in src.items()}


def from_worker():
    js = open(os.path.join(RECRUIT, 'src', 'index.js'), encoding='utf-8').read()
    m = re.search(r'const RELATION = \{(.*?)\n\};', js, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        km = re.match(r"\s*(\w+):\s*\{(.*)\},?\s*$", line)
        if not km:
            continue
        body = km.group(2)
        row = {}
        for f in KEYS:
            fm = re.search(rf"{f}:\s*'?([\w]+)'?", body)
            if fm:
                v = fm.group(1)
                row[f] = int(v) if v.isdigit() else v
        out[km.group(1)] = row
    return out


def form_options():
    p = os.path.join(SITE, 'consultant', 'job-intake', 'index.html')
    if not os.path.exists(p):
        return None
    h = open(p, encoding='utf-8').read()
    return sorted(set(re.findall(r'value="(signed|unsigned|private)"', h)))


def main():
    py, js = from_python(), from_worker()
    problems = []
    if js is None:
        problems.append('讀不到 Worker 的 RELATION 常數')
    else:
        for k in set(py) | set(js):
            a, b = py.get(k), js.get(k)
            if a != b:
                problems.append(f'{k}：Python={a} ／ Worker={b}')
    opts = form_options()
    if opts is not None and sorted(py) != opts:
        problems.append(f'表單選項 {opts} 跟規則 {sorted(py)} 對不上')

    if problems:
        print('❌ 客戶對象規則不一致，會導致履歷具名／品牌／AI 揭露設錯：')
        for p in problems:
            print('   ·', p)
        return 1
    print(f'✅ 三邊一致（{", ".join(sorted(py))}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
