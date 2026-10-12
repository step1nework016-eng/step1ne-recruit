#!/usr/bin/env python3
"""找「現在可以直接用註冊價買」的 .com 老網域（不是拍賣、不是賣場）。

做法：
  1. 拿 Common Crawl 一年前的全網連結圖排名（當時有很多網站連過去的網域）
  2. 只留 .com，查 DNS，留下「現在已經不存在」的
  3. 向 .com 登記處（Verisign RDAP）確認真的沒人註冊 = GoDaddy / Cloudflare 買得到
  4. 有 DR 金鑰就查 Ahrefs DR 並篩 10–40；沒有就先輸出候選清單

用法：
  python3 find_domains.py                    # 預設掃排名前 300 萬
  python3 find_domains.py --top 8000000      # 掃更深（候選更多、權重更低）
  python3 find_domains.py --sample 20000     # 只抽樣試跑
  python3 find_domains.py --dr-min 10 --dr-max 40
DR 金鑰：在資料夾（DOMAIN_FINDER_HOME）放 dr_key.txt（RapidAPI 金鑰），或設環境變數 RAPIDAPI_KEY。
"""
import argparse, asyncio, csv, gzip, json, os, random, struct, sys, time, urllib.request, urllib.error
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from df_paths import HOME, CACHE            # 資料（repo 外面，見 df_paths.py）
OUT = HOME / "output"
GRAPH = "cc-main-2025-jul-aug-sep"
RANKS_URL = f"https://data.commoncrawl.org/projects/hyperlinkgraph/{GRAPH}/domain/{GRAPH}-domain-ranks.txt.gz"
RESOLVERS = ["1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "208.67.222.222"]
RDAP = "https://rdap.verisign.com/com/v1/domain/"
DR_HOST = "domain-metrics-check.p.rapidapi.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def load_com_ranks(top):
    """回傳 [(排名, 網域)]，只含 .com 主網域。排名檔已照權重排序，只讀前 top 行。"""
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / f"{GRAPH}-com-top{top}.tsv"
    if cached.exists():
        return [(int(a), b) for a, b in (l.rstrip("\n").split("\t") for l in cached.open())]
    print(f"下載排名檔前 {top:,} 行（只抓開頭，不會整包 3GB 下載）…", flush=True)
    rows = []
    req = urllib.request.Request(RANKS_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp, gzip.GzipFile(fileobj=resp) as gz:
        for i, raw in enumerate(gz):
            if i == 0:
                continue
            if i > top:
                break
            p = raw.decode("utf-8", "replace").split("\t")
            rev = p[4]
            if rev.startswith("com.") and rev.count(".") == 1:
                rows.append((int(p[0]), rev[4:] + ".com"))
    with cached.open("w") as f:
        f.writelines(f"{r}\t{d}\n" for r, d in rows)
    return rows


def dns_query(txid, name):
    q = b"".join(bytes([len(x)]) + x.encode("ascii") for x in name.split(".")) + b"\0"
    return struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", 2, 1)  # NS IN


class Dns(asyncio.DatagramProtocol):
    def __init__(self):
        self.waiting = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if len(data) < 12:
            return
        txid, flags = struct.unpack(">HH", data[:4])
        fut = self.waiting.pop((txid, addr[0]), None)
        if fut and not fut.done():
            fut.set_result(flags & 0xF)

    async def rcode(self, name, tries=3):
        loop = asyncio.get_running_loop()
        for _ in range(tries):
            txid, ip = random.randrange(65536), random.choice(RESOLVERS)
            if (txid, ip) in self.waiting:
                continue
            fut = loop.create_future()
            self.waiting[(txid, ip)] = fut
            self.transport.sendto(dns_query(txid, name), (ip, 53))
            try:
                return await asyncio.wait_for(fut, 3)
            except asyncio.TimeoutError:
                self.waiting.pop((txid, ip), None)
        return None


async def find_nxdomain(domains, qps):
    """DNS 回「查無此網域」的才留下。逾時或其他錯誤一律當作還在（寧可漏、不要誤報）。"""
    loop = asyncio.get_running_loop()
    _, proto = await loop.create_datagram_endpoint(Dns, local_addr=("0.0.0.0", 0))
    sem = asyncio.Semaphore(qps * 2)
    gone, unknown, done = [], 0, 0
    t0 = time.time()

    async def one(item):
        nonlocal unknown, done
        async with sem:
            try:
                item[1].encode("ascii")
                rc = await proto.rcode(item[1])
            except (UnicodeError, ValueError):
                rc = None
        done += 1
        if rc == 3:
            gone.append(item)
        elif rc is None:
            unknown += 1
        if done % 50000 == 0:
            print(f"  DNS {done:,}/{len(domains):,}  已不存在 {len(gone):,}  無回應 {unknown:,}  {time.time()-t0:.0f}s", flush=True)

    tasks = []
    for i, item in enumerate(domains):
        tasks.append(asyncio.create_task(one(item)))
        if i % qps == qps - 1:
            await asyncio.sleep(1)
    await asyncio.gather(*tasks)
    proto.transport.close()
    return gone, unknown


def rdap_available(domain):
    """True=登記處查無此網域（可註冊）；False=還有人持有；None=查不到。"""
    for attempt in range(3):
        try:
            req = urllib.request.Request(RDAP + domain, headers={"User-Agent": UA})
            urllib.request.urlopen(req, timeout=15).close()
            return False
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1)
    return None


async def confirm_available(cands, workers=8):
    sem = asyncio.Semaphore(workers)
    ok, taken, unk = [], 0, 0

    async def one(item):
        nonlocal taken, unk
        async with sem:
            r = await asyncio.to_thread(rdap_available, item[1])
        if r is True:
            ok.append(item)
        elif r is False:
            taken += 1
        else:
            unk += 1

    await asyncio.gather(*(one(c) for c in cands))
    return sorted(ok), taken, unk


def dr_key():
    k = os.environ.get("RAPIDAPI_KEY", "").strip()
    f = HOME / "dr_key.txt"
    return k or (f.read_text().strip() if f.exists() else "")


def fetch_dr(domain, key):
    """回傳 dict(dr, da, rd) 或 None。資料來源：RapidAPI「Domain Metrics Check」。"""
    req = urllib.request.Request(
        f"https://{DR_HOST}/domain-metrics/{domain}/",
        headers={"x-rapidapi-key": key, "x-rapidapi-host": DR_HOST, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        print(f"  DR 查詢失敗 {domain}: {e}", flush=True)
        return None
    num = lambda v: float(v) if str(v).replace(".", "", 1).isdigit() else None
    return {"dr": num(d.get("ahrefsDR")), "da": num(d.get("mozDA")), "rd": num(d.get("ahrefsRefDomains"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=3_000_000, help="掃排名前幾名（全部 TLD 合計）")
    ap.add_argument("--sample", type=int, default=0, help="只隨機抽這麼多個 .com 試跑")
    ap.add_argument("--qps", type=int, default=600)
    ap.add_argument("--rdap-limit", type=int, default=0, help="只向登記處確認排名最前面的這麼多個（0=全部）")
    ap.add_argument("--dr-min", type=float, default=10)
    ap.add_argument("--dr-max", type=float, default=40)
    ap.add_argument("--dr-limit", type=int, default=200, help="最多查幾個 DR（照排名由高到低），省額度")
    a = ap.parse_args()

    rows = load_com_ranks(a.top)
    print(f".com 候選 {len(rows):,} 個", flush=True)
    if a.sample:
        rows = random.sample(rows, min(a.sample, len(rows)))

    gone, unknown = asyncio.run(find_nxdomain(rows, a.qps))
    print(f"DNS 已不存在 {len(gone):,} 個（無回應 {unknown:,} 個未列入）", flush=True)

    gone.sort()
    CACHE.mkdir(exist_ok=True)
    (CACHE / "dns查無.tsv").write_text("".join(f"{r}\t{d}\n" for r, d in gone))
    if a.rdap_limit:
        gone = gone[:a.rdap_limit]

    avail, taken, unk = asyncio.run(confirm_available(gone))
    print(f"登記處確認可註冊 {len(avail):,} 個（仍有人持有/贖回期 {taken:,}、查不到 {unk:,}）", flush=True)

    key = dr_key()
    out = []
    for n, (rank, dom) in enumerate(avail):
        m = fetch_dr(dom, key) if key and n < a.dr_limit else None
        out.append({"domain": dom, "cc_rank": rank, "dr": m and m["dr"], "da": m and m["da"], "rd": m and m["rd"]})
    if key:
        checked = [r for r in out if r["dr"] is not None]
        out = [r for r in checked if a.dr_min <= r["dr"] <= a.dr_max]
        print(f"查到 DR {len(checked)} 個，落在 {a.dr_min:g}–{a.dr_max:g} 的有 {len(out)} 個", flush=True)
    else:
        print("沒有 DR 金鑰：只輸出候選清單，DR 欄留空", flush=True)

    OUT.mkdir(exist_ok=True)
    path = OUT / f"可註冊com_{date.today()}{'_DR已篩' if key else '_候選'}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["網域", "Ahrefs DR", "Moz DA", "連入網域數", "一年前全網排名", "GoDaddy 購買", "Cloudflare 購買", "查前世"])
        for r in out:
            d = r["domain"]
            w.writerow([d, r["dr"] or "", r["da"] or "", r["rd"] or "", r["cc_rank"],
                        f"https://www.godaddy.com/domainsearch/find?domainToCheck={d}",
                        f"https://domains.cloudflare.com/?domain={d}",
                        f"https://web.archive.org/web/2020*/{d}"])
    print(f"輸出 {len(out)} 筆 → {path}")


if __name__ == "__main__":
    sys.exit(main())
