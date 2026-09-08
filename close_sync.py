#!/usr/bin/env python3
"""顧問在後台關閉職缺後，把網站上那一頁一起收尾。

2026-09-08 加。在此之前「關閉」只改 D1 的 jobs.status，網站完全不知道：
  ・列表頁與應徵下拉選單看不到了（那兩處是即時讀 D1 的）✅
  ・**但單一職缺頁還活著、應徵按鈕還能按、給 Google 的資料還寫著有效**
實測 /jobs/senior-ehs-engineer-yunlin/ 已關閉卻仍回 200、validThrough 還是
2026-12-31。候選人從兩個月前的社群貼文點進來，會看到完整職缺頁、按了應徵
才發現下拉選單裡根本沒有這個缺——不會產生錯資料，但很浪費人家時間。

這支做三件事（可以重複執行，不會疊加）：
  1. 職缺頁最上面插一條「已完成招募」橫幅
  2. JSON-LD 的 validThrough 改成關閉日期 → Google 才知道要下架
  3. 從 sitemap.xml 移除
職缺重開時全部復原（原本的 validThrough 存在橫幅標記裡）。

⚠️ 不刪頁面、不做 301。舊社群貼文與候選人書籤都指著這個網址，
   直接 404 是更糟的體驗；留著頁面但講清楚已經結束才是對的。
"""
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

MARK = 'S1NE-CLOSED-BANNER'


def log(m):
    print(m, flush=True)


def banner_html(title, closed_at, orig_valid):
    return (
        f'<!--{MARK} orig_valid="{orig_valid}"-->'
        '<div style="background:#fdf3e7;border-bottom:1px solid #e8d5b8;'
        'padding:14px 0;color:#8a4b12;">'
        '<div class="wrap" style="display:flex;flex-wrap:wrap;gap:10px;'
        'align-items:center;justify-content:space-between;">'
        f'<div style="font-size:14.5px;line-height:1.6;">'
        f'<b>這個職缺已經完成招募</b>，不再開放應徵。'
        f'<span style="opacity:.75">（{closed_at[:10]} 結束）</span></div>'
        '<a href="/jobs/" style="flex:none;background:#8a4b12;color:#fff;'
        'text-decoration:none;font-size:14px;font-weight:700;padding:9px 18px;'
        'border-radius:999px;">看其他職缺 →</a>'
        '</div></div>'
    )


def apply_closed(html, title, closed_at):
    """把一頁改成已關閉的樣子。回傳 (新內容, 有沒有改)。"""
    if MARK in html:
        return html, False
    m = re.search(r'"validThrough"\s*:\s*"([^"]*)"', html)
    orig = m.group(1) if m else ''
    out = html.replace('<main>', '<main>' + banner_html(title, closed_at, orig), 1)
    if m:
        # Google 判斷職缺還在不在，看的就是 validThrough——改成關閉當天，
        # 搜尋結果才會自己下架，不然會一直有人從 Google 點進死缺。
        out = re.sub(r'("validThrough"\s*:\s*")[^"]*(")',
                     lambda x: x.group(1) + closed_at[:10] + x.group(2), out, count=1)
    # 應徵按鈕不要留著讓人按了才發現選不到（下拉選單裡已經沒有這個缺），
    # 改成回職缺專區。
    # ⚠️ 原始的網址與按鈕文字一定要存進 data 屬性——不存的話職缺重開時
    #    按鈕還會停在「看其他職缺」，等於重開了卻沒人應徵得了。
    #    初版就是漏了這個，比對還原結果才發現。
    out = re.sub(r'<a([^>]*?)href="(/apply/\?job=[^"]*)"',
                 lambda m: f'<a{m.group(1)}href="/jobs/" data-s1ne-apply="{m.group(2)}"', out)
    for label in ('我要應徵', '立即跟 AI 阿財面談'):
        out = out.replace(f'>{label}<', f'><span data-s1ne-label="{label}">看其他職缺</span><')
    return out, out != html


def revert_open(html):
    """職缺重開：把橫幅拿掉、validThrough 還原。"""
    m = re.search(r'<!--' + MARK + r' orig_valid="([^"]*)"-->', html)
    if not m:
        return html, False
    orig = m.group(1)
    out = re.sub(r'<!--' + MARK + r' orig_valid="[^"]*"-->.*?</div></div>', '', html, count=1, flags=re.S)
    if orig:
        out = re.sub(r'("validThrough"\s*:\s*")[^"]*(")',
                     lambda x: x.group(1) + orig + x.group(2), out, count=1)
    # 應徵按鈕還原：網址與文字都要，缺一個就是重開了卻應徵不了
    out = re.sub(r'<a([^>]*?)href="/jobs/" data-s1ne-apply="([^"]*)"',
                 lambda m: f'<a{m.group(1)}href="{m.group(2)}"', out)
    out = re.sub(r'<span data-s1ne-label="([^"]*)">[^<]*</span>',
                 lambda m: m.group(1), out)
    return out, out != html


def main():
    rows = d1_http.query(
        "SELECT slug, title, COALESCE(status,'open') AS status, closed_at FROM jobs")['results']
    closed = {r['slug']: r for r in rows if r['status'] == 'closed'}
    changed = []

    for d in sorted(os.listdir(os.path.join(SITE, 'jobs'))):
        f = os.path.join(SITE, 'jobs', d, 'index.html')
        if not os.path.isfile(f):
            continue
        html = io.open(f, encoding='utf-8').read()
        if d in closed:
            r = closed[d]
            new, did = apply_closed(html, r.get('title') or d, r.get('closed_at') or '')
        else:
            new, did = revert_open(html)
        if did:
            io.open(f, 'w', encoding='utf-8').write(new)
            changed.append(('關閉' if d in closed else '重開', d))

    # sitemap：關閉的拿掉，重開的補回來由 gen_sitemap.py 負責
    sm = os.path.join(SITE, 'sitemap.xml')
    if os.path.isfile(sm) and closed:
        s = io.open(sm, encoding='utf-8').read()
        before = s
        for slug in closed:
            s = re.sub(r'\s*<url>(?:(?!</url>).)*?/jobs/' + re.escape(slug) + r'/(?:(?!</url>).)*?</url>',
                       '', s, flags=re.S)
        if s != before:
            io.open(sm, 'w', encoding='utf-8').write(s)
            changed.append(('sitemap', f'移除 {len(closed)} 個已關閉職缺'))

    if not changed:
        log('沒有需要同步的職缺')
        return

    for kind, what in changed:
        log(f'  {kind}：{what}')

    if '--deploy' in sys.argv:
        subprocess.run(['git', 'add', '-A'], cwd=SITE, check=True)
        r = subprocess.run(
            ['git', 'commit', '-m',
             '職缺關閉同步：加上「已完成招募」橫幅、validThrough 改成關閉日、移出 sitemap'],
            cwd=SITE, capture_output=True, text=True)
        if r.returncode == 0:
            subprocess.run(['git', 'push', 'deploy', 'HEAD:main'], cwd=SITE, check=True)
            log('已部署')
        else:
            log('沒有要 commit 的變更')


if __name__ == '__main__':
    main()
