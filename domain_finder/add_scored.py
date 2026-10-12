#!/usr/bin/env python3
"""把 cache/分數結果.tsv 裡 Ubersuggest 20–40 分、還沒在名單上的網域，確認可註冊後加進 網域口袋名單.csv（DR 留空）。可重複跑。"""
import csv, subprocess, datetime, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from df_paths import CACHE, CSVF
today = str(datetime.date.today())
rows = list(csv.reader(CSVF.open(encoding="utf-8-sig")))
have = {r[0] for r in rows}
width = len(rows[0])
tags = {}
qf = CACHE / "佇列.tsv"
if qf.exists():
    tags = dict(l.rstrip("\n").split("\t")[:2] for l in qf.open(encoding="utf-8") if "\t" in l)
added, taken = 0, []
for l in ((CACHE / "分數結果.tsv").open(encoding="utf-8") if (CACHE / "分數結果.tsv").exists() else []):
    p = l.rstrip("\n").split("\t")
    try:
        da = float(p[1])
    except (ValueError, IndexError):
        continue
    d = p[0]
    if not 20 <= da <= 40 or d in have:
        continue
    o = subprocess.run(["whois", "-h", "whois.verisign-grs.com", d], capture_output=True, text=True, timeout=30).stdout
    if "No match for" not in o:
        taken.append(d)
        continue
    rows.append([d, tags.get(d) or "一般", int(da), int(float(p[2])), int(float(p[3])), int(float(p[5])), "", "是 " + today,
                 f"https://www.godaddy.com/domainsearch/find?domainToCheck={d}", f"https://domains.cloudflare.com/?domain={d}",
                 f"https://web.archive.org/web/2020*/{d}", "未買"])
    rows[-1] += [""] * (width - len(rows[-1]))
    have.add(d); added += 1
    time.sleep(0.3)
csv.writer(CSVF.open("w", newline="", encoding="utf-8-sig")).writerows(rows)
print(f"新增 {added} 個；已被註冊或查不到 {taken}；名單共 {len(rows)-1} 個")
