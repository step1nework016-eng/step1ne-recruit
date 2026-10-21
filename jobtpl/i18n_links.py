# -*- coding: utf-8 -*-
"""職缺頁的多語言連結：hreflang 標籤＋右上角小語言切換。

為什麼是一支獨立的小模組：職缺頁是 publish_job.py（含 jd_regen_tick.py 重新產生）從 D1 產出來的，
手改會在下一次重新產生時被洗掉。所以改成「產頁面的最後一步」呼叫 inject()——
只要 站台根目錄/id/jobs/<slug>/index.html 或 vi/jobs/<slug>/index.html 存在，
就自動加上；沒有翻譯的職缺不會多出任何東西。重複執行結果一樣（用標記包起來，先清掉再加）。

⚠️ 切換列放在 <header> 之後、<main> 之前：job-detail-m.js 會把 <main> 整個重排，放裡面會被吃掉。
"""
import os, re

BASE = 'https://step1ne.com'
HEAD_A, HEAD_B = '<!--i18n-hreflang-->', '<!--/i18n-hreflang-->'
SW_A, SW_B = '<!--i18n-switch-->', '<!--/i18n-switch-->'
LANGS = [('zh-Hant', '中文', '/jobs/%s/'), ('id', 'Bahasa Indonesia', '/id/jobs/%s/'), ('vi', 'Tiếng Việt', '/vi/jobs/%s/')]

SWITCH_CSS = ('.s1-lang{max-width:820px;margin:0 auto;padding:10px 24px 0;display:flex;flex-wrap:wrap;gap:6px;align-items:center}'
              '.s1-lang a,.s1-lang b{display:inline-flex;align-items:center;min-height:44px;padding:0 12px;border-radius:999px;'
              'border:1px solid #e2e0d8;background:rgba(255,255,255,.7);font-size:13px;font-weight:600;color:#6b6e77;text-decoration:none}'
              '.s1-lang b{background:#23262d;border-color:#23262d;color:#fff}'
              '.s1-lang a:hover{color:#23262d}.s1-lang a:focus-visible{outline:3px solid #1880B4;outline-offset:2px}')


def available(slug, site):
    """回傳這個職缺有哪些語言：一定有 zh-Hant，加上有檔案的翻譯。"""
    out = ['zh-Hant']
    for code, _, path in LANGS[1:]:
        if os.path.exists(os.path.join(site, path.strip('/') % slug, 'index.html')):
            out.append(code)
    return out


def _strip(html):
    html = re.sub(re.escape(HEAD_A) + r'.*?' + re.escape(HEAD_B) + r'\n?', '', html, flags=re.S)
    html = re.sub(r'\n?' + re.escape(SW_A) + r'.*?' + re.escape(SW_B) + r'\n?', '', html, flags=re.S)
    return html


def hreflang_block(slug, langs):
    lines = [HEAD_A]
    for code, _, path in LANGS:
        if code in langs:
            lines.append('<link rel="alternate" hreflang="%s" href="%s%s">' % (code, BASE, path % slug))
    lines.append('<link rel="alternate" hreflang="x-default" href="%s%s">' % (BASE, LANGS[0][2] % slug))
    lines.append(HEAD_B)
    return '\n'.join(lines) + '\n'


def switch_block(slug, langs, current='zh-Hant'):
    items = []
    for code, label, path in LANGS:
        if code not in langs:
            continue
        if code == current:
            items.append('<b aria-current="page" lang="%s">%s</b>' % (code, label))
        else:
            items.append('<a href="%s" lang="%s" hreflang="%s">%s</a>' % (path % slug, code, code, label))
    return '%s<style>%s</style><nav class="s1-lang" aria-label="Language">%s</nav>%s\n' % (SW_A, SWITCH_CSS, ''.join(items), SW_B)


def inject(html, slug, site, with_switch=True, current='zh-Hant'):
    """給中文職缺頁用：加 hreflang（放在 canonical 後面）與切換列（放在 </header> 後面）。"""
    html = _strip(html)
    langs = available(slug, site)
    if len(langs) < 2:
        return html
    m = re.search(r'<link rel="canonical"[^>]*>\n?', html)
    if m:
        html = html[:m.end()] + hreflang_block(slug, langs) + html[m.end():]
    if with_switch:
        i = html.find('</header>')
        if i >= 0:
            i += len('</header>')
            html = html[:i] + '\n' + switch_block(slug, langs, current) + html[i:]
    return html
