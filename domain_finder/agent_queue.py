#!/usr/bin/env python3
"""給每日排程用的小工具。所有狀態都在 cache/，重跑安全。
  python3 agent_queue.py next 11                  → 印出接下來 11 個還沒查分數的網域（照佇列順序）
  python3 agent_queue.py record 網域 分數 連入網站 連結數 follow nofollow   → 記一筆結果（查不到資料分數填 NODATA）
  python3 agent_queue.py status                   → 今天查了幾個、佇列還剩幾個
  python3 agent_queue.py finish                   → 把 20–40 分且仍可註冊的加進口袋名單並重產網頁
  python3 agent_queue.py prelife 網域 ...          → 抓 Wayback 三個時間點的標題與摘要，給你判斷前世
  python3 agent_queue.py setprelife 網域 "乾淨｜說明"  → 把判斷寫進口袋名單的「前世」欄
"""
import csv, datetime, html, os, re, subprocess, sys, time
from pathlib import Path

H = Path(__file__).resolve().parent          # 程式
sys.path.insert(0, str(H))
from df_paths import HOME, CACHE, CSVF       # 資料（repo 外面，見 df_paths.py）
Q, RES = CACHE / "佇列.tsv", CACHE / "分數結果.tsv"
LOG = CACHE / "每日紀錄.tsv"
TODAY = str(datetime.date.today())


def done():
    s = set()
    if RES.exists():
        s |= {l.split("\t")[0] for l in RES.open(encoding="utf-8") if l.strip()}
    return s


def queue():
    d = done()
    return [l.rstrip("\n").split("\t") for l in Q.open(encoding="utf-8") if l.strip() and l.split("\t")[0] not in d]


def today_count():
    return sum(1 for l in LOG.open(encoding="utf-8") if l.startswith(TODAY)) if LOG.exists() else 0


cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
if cmd == "next":
    for d, tag in queue()[:int(sys.argv[2])]:
        print(f"{d}\t{tag}")
elif cmd == "record":
    d, vals = sys.argv[2], sys.argv[3:8]
    if d in done():
        sys.exit(f"{d} 已經記過，略過")
    with RES.open("a", encoding="utf-8") as f:
        f.write("\t".join([d] + vals) + "\n")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{TODAY}\t{d}\t{vals[0]}\n")
    print("ok", d, vals[0])
elif cmd == "status":
    print(f"今天已查 {today_count()} 個；佇列還剩 {len(queue())} 個")
elif cmd == "finish":
    print(subprocess.run([sys.executable, str(H / "add_scored.py")], capture_output=True, text=True).stdout.strip())
    print(subprocess.run([sys.executable, str(H / "make_html.py")], capture_output=True, text=True).stdout.strip())
    # 同步到內部看板（step1ne.com/seo-report/#domains）。失敗不影響名單本身，只印提醒。
    if os.environ.get("DF_NO_SYNC") != "1":
        r = subprocess.run([sys.executable, str(H / "sync_dashboard.py")], capture_output=True, text=True)
        print((r.stdout.strip() or r.stderr.strip()[-300:]) if r.returncode == 0 else "⚠️ 看板同步失敗：" + r.stderr.strip()[-300:])
elif cmd == "prelife":
    for d in sys.argv[2:]:
        print("==", d)
        for y in ("2016", "2020", "2025"):
            for attempt in range(3):
                p = subprocess.run(["curl", "-sL", "--compressed", "-m", "35", "-A",
                                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/126 Safari/537.36",
                                    "-w", "\n@@%{http_code} %{url_effective}", f"https://web.archive.org/web/{y}id_/http://{d}/"], capture_output=True)
                t = p.stdout.decode("utf-8", "replace")
                tail = t.rsplit("\n@@", 1)[-1]
                if tail.startswith("429") or not tail.strip():
                    time.sleep(40)
                    continue
                break
            page = t.rsplit("\n@@", 1)[0]
            ti = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
            lang = re.search(r'<html[^>]*lang=["\']?([a-zA-Z\-]+)', page)
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[\s\S]*?</\1>", "", page, flags=re.I)))
            snap = re.search(r"/web/(\d{8})", tail)
            print(f"  要 {y} → 實際 {snap.group(1) if snap else '?'} [{lang.group(1) if lang else '?'}] 標題：{html.unescape(ti.group(1).strip())[:90] if ti else '（無）'}｜{html.unescape(body[:260])}")
            time.sleep(3)
elif cmd == "setprelife":
    d, text = sys.argv[2], sys.argv[3]
    rows = list(csv.reader(CSVF.open(encoding="utf-8-sig")))
    i = rows[0].index("前世")
    hit = [r for r in rows[1:] if r[0] == d]
    if not hit:
        sys.exit(f"{d} 不在口袋名單（要先跑 finish）")
    hit[0][i] = text
    csv.writer(CSVF.open("w", newline="", encoding="utf-8-sig")).writerows(rows)
    print("ok", d, text)
