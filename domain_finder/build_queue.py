#!/usr/bin/env python3
"""依 industries.json 的關鍵字，從 cache/dns查無.tsv（以前有很多網站連、現在查不到的 .com）挑出名字跟產業相關的，
排成 cache/佇列.tsv。已經查過分數的不會重排。要加新產業：編輯 industries.json 再跑一次。
用法：python3 build_queue.py            （只排有對到產業關鍵字的）
      python3 build_queue.py --all      （對不到產業的也排在最後面，當作「一般」）
"""
import json, re, sys
from pathlib import Path

H = Path(__file__).resolve().parent          # 程式（industries.json 在這）
sys.path.insert(0, str(H))
from df_paths import CACHE                  # 資料（repo 外面）
ALWAYS_SKIP = re.compile(r"porn|sex|xxx|xnxx|casino|slot|bet|poker|togel|judi|pharm|viagra|cialis|escort|adult|nude|bokep|hentai|steroid|crack|torrent|\d{4,}|xn--")
inds = json.load(open(H / "industries.json", encoding="utf-8"))
rows = sorted((int(r), d) for r, d in (l.rstrip("\n").split("\t") for l in open(CACHE / "dns查無.tsv", encoding="utf-8") if l.strip()))
res = CACHE / "分數結果.tsv"
done = {l.split("\t")[0] for l in res.open(encoding="utf-8") if l.strip()} if res.exists() else set()
ex = CACHE / "已知排除.txt"
known = {l.strip() for l in ex.open(encoding="utf-8") if l.strip() and not l.startswith("#")} if ex.exists() else set()
out, seen, count = [], set(done) | known, {}
for tag, rule in inds.items():
    want, skip = re.compile(rule["要有"]), re.compile(rule["不要"]) if rule.get("不要") else None
    for _, d in rows:
        name = d.rsplit(".", 1)[0]
        if d in seen or ALWAYS_SKIP.search(d) or not want.search(name) or (skip and skip.search(d)):
            continue
        out.append(f"{d}\t{tag}"); seen.add(d); count[tag] = count.get(tag, 0) + 1
if "--all" in sys.argv:
    for _, d in rows:
        if d not in seen and not ALWAYS_SKIP.search(d) and len(d) <= 26:
            out.append(f"{d}\t一般"); seen.add(d); count["一般"] = count.get("一般", 0) + 1
(CACHE / "佇列.tsv").write_text("\n".join(out) + "\n", encoding="utf-8")
print("佇列：", count, "共", len(out), "個（已查過的", len(done), "個不重排）")
