#!/usr/bin/env python3
"""手刻專區頁（/training/、/construction/）的職缺標題跟系統同步。

為什麼要有這支：2026-10-20 Jacky 在顧問後台把「半導體專案工程師（全額免費培訓）」
改成「半導體專案工程師(儲備培訓)」，職缺頁會被 jd_regen_tick 重產，但這兩頁是手刻的，
標題永遠停在舊的。這支照卡片連到的 /jobs/<slug>/ 去系統查最新標題，換掉卡片上的字。

只換標題，不動其他內容；同名職缺（兩個「BIM 工程師(儲備培訓)」）加地區區分。

用法：
    python3 sync_landing_titles.py            # 只印出會改什麼，不動檔案
    python3 sync_landing_titles.py --write    # 改檔案但不上線
    python3 sync_landing_titles.py --deploy   # 改完跑檢查、commit、推上線
"""
import os, re, sys, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import consultant_ops as C  # noqa: E402

SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
PAGES = ['training/index.html', 'construction/index.html']
# 標題本身沒寫地點、又跟別的職缺同名時，卡片上補地區
REGION = {'bim-engineer-houli': '台中后里', 'semiconductor-bim-mep-drafting-trainee-hsinchu': '新竹科學園區',
          'bim-engineer-tongluo': '苗栗銅鑼'}


def nice(slug, title):
    t = title.strip().replace('(', '（').replace(')', '）')
    t = re.sub(r'(?<=[A-Za-z])(?=[一-鿿])', ' ', t)  # BIM工程師 → BIM 工程師
    reg = REGION.get(slug)
    if reg and reg not in t:
        t = t[:-1] + f'・{reg}）' if t.endswith('）') else f'{t}（{reg}）'
    return t


def titles(slugs):
    out = {}
    for s in slugs:
        r = C.rows("SELECT title, status FROM jobs WHERE slug=?", [s])
        if r and r[0]['status'] in ('open', 'closed') and r[0]['title']:  # 額滿（closed）卡片也照系統名稱
            out[s] = nice(s, r[0]['title'])
    return out


def sync_page(path):
    html = open(path, encoding='utf-8').read()
    changes = []
    # /training/：<article class="tc" ...> <h3>標題</h3> ... <a class="go" href="/jobs/slug/">
    # /construction/：<a class="job" href="/jobs/slug/"> ... <h3 class="job-title">標題</h3>
    pats = [
        re.compile(r'(<article class="tc[^"]*"[^>]*>(?:(?!</article>).)*?<h3>)([^<]*)(</h3>(?:(?!</article>).)*?href="/jobs/([^/"]+)/"(?:(?!</article>).)*?</article>)', re.S),
        re.compile(r'(<a class="job[^"]*" href="/jobs/([^/"]+)/">(?:(?!</a>).)*?<h3 class="job-title">)([^<]*)(</h3>)', re.S),
    ]
    slugs = set(re.findall(r'href="/jobs/([^/"]+)/"', html))
    tt = titles(slugs)

    def rep_tc(m):
        slug, old = m.group(4), m.group(2)
        new = tt.get(slug)
        if new and new != old:
            changes.append((old, new))
            return m.group(1) + new + m.group(3)
        return m.group(0)

    def rep_job(m):
        slug, old = m.group(2), m.group(3)
        new = tt.get(slug)
        if new and new != old:
            changes.append((old, new))
            return m.group(1) + new + m.group(4)
        return m.group(0)

    html = pats[0].sub(rep_tc, html)
    html = pats[1].sub(rep_job, html)
    if changes and WRITE:
        open(path, 'w', encoding='utf-8').write(html)
    return changes


WRITE = '--deploy' in sys.argv or '--write' in sys.argv


def main():
    allc = []
    for p in PAGES:
        for old, new in sync_page(os.path.join(SITE, p)):
            allc.append(f'{p}：{old} → {new}')
    for c in allc:
        print(c)
    if not allc:
        print('標題都一致，不用改')
        return
    if '--deploy' in sys.argv:
        for cmd in (['python3', 'scripts/check_pages.py'], ['./scripts/predeploy.sh']):
            r = subprocess.run(cmd, cwd=SITE, capture_output=True, text=True)
            if r.returncode != 0:
                print('❌ 檢查沒過，不上線：', (r.stdout + r.stderr)[-400:])
                subprocess.run(['git', 'checkout', '--'] + PAGES, cwd=SITE)
                sys.exit(1)
        subprocess.run(['git', 'commit', '-qm', '專區頁職缺標題跟系統同步', '--'] + PAGES, cwd=SITE, check=True)
        subprocess.run(['git', 'push', '-q', 'deploy', 'HEAD:main'], cwd=SITE, check=True)
        print('已上線')


if __name__ == '__main__':
    main()
