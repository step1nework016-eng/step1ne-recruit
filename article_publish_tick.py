#!/usr/bin/env python3
"""Step1ne 週文章草稿核准後，真的把它變成網頁、上線。

跟 client_report_tick.py／social_post_agent.py 同一種分工：Worker（
handleArticleAction）收 Telegram 按鈕只負責記「核准了」，因為 Worker
沒有本機 git 環境可以建頁面、commit、push。這支常駐排程輪詢
`article_drafts` 裡 status='approved' 的列，做真正的套用上線。

用法：
    python3 article_publish_tick.py           # 常駐，每 5 分鐘掃一次
    python3 article_publish_tick.py --once     # 跑一輪就結束（測試用）
"""
import argparse
import datetime
import importlib.util
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.expanduser('~/claude-projects/step1ne-stopgap-site')
TEMPLATE_PATH = os.path.join(SITE_DIR, 'articles/bim-career-change/index.html')
ARTICLES_INDEX = os.path.join(SITE_DIR, 'articles/index.html')

spec = importlib.util.spec_from_file_location('idaemon', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

POLL_SECONDS = 300
THREAD_ID = 5110


def now_taipei_str():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


def now_date():
    return (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')


def tg_notify(text, thread_id=THREAD_ID, reply_to=None):
    env = {}
    p = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    token, chat_id = env.get('TG_BOT_TOKEN'), env.get('TG_CHAT_ID')
    if not token or not chat_id:
        return
    data = {'chat_id': chat_id, 'message_thread_id': str(thread_id), 'text': text}
    if reply_to:
        data['reply_to_message_id'] = str(reply_to)
    import urllib.parse
    # ⚠️ 2026-09-14 測試時踩到：timeout 是 urlopen() 的參數，不是 Request() 的——
    # 傳錯地方會讓 TG 通知本身變成一個會拋例外的動作。這支的呼叫端沒有把
    # 通知包在自己的 try/except 裡，通知失敗會被外層當成「整個發布失敗」，
    # 導致已經成功的 published 狀態被覆蓋回 error。通知永遠不該讓主流程失敗，
    # 所以這裡自己吞掉例外，絕對不要讓它往外拋。
    try:
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=urllib.parse.urlencode(data).encode())
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f'TG 通知失敗（不影響主流程）：{e}')


def parse_draft_body(draft_md):
    """draft_md 結構：<正文 HTML>\n### FAQ\nQ:...\nA:...\n...\n## 查證來源\n- ...

    回傳 (body_html, faq_list, sources_list)。
    """
    faq_start = draft_md.find('### FAQ')
    sources_start = draft_md.find('## 查證來源')
    if faq_start == -1:
        body = draft_md.strip()
        return body, [], []
    body = draft_md[:faq_start].strip()
    faq_end = sources_start if sources_start != -1 else len(draft_md)
    faq_block = draft_md[faq_start + len('### FAQ'):faq_end].strip()
    faq = []
    cur_q = None
    for line in faq_block.splitlines():
        line = line.strip()
        if line.startswith('Q:'):
            cur_q = line[2:].strip()
        elif line.startswith('A:') and cur_q:
            faq.append((cur_q, line[2:].strip()))
            cur_q = None
    sources = []
    if sources_start != -1:
        src_block = draft_md[sources_start + len('## 查證來源'):].strip()
        for line in src_block.splitlines():
            line = line.strip().lstrip('- ').strip()
            if line:
                sources.append(line)
    return body, faq, sources


def extract_h2_sections(body_html):
    """從正文 HTML 抓出 (id, 標題文字) 清單，用來組桌面／手機版目錄。"""
    return re.findall(r'<h2 id="(sec-\d+)">(.*?)</h2>', body_html, re.S)


def build_page(draft, body_html, faq, sources):
    tpl = open(TEMPLATE_PATH, encoding='utf-8').read()
    slug = draft['article_slug']
    title = draft['title'] or ''
    desc = draft['description'] or ''
    keywords = draft['keywords'] or ''
    tag = draft['tag'] or '職缺情報'
    og_title = draft['og_title'] or title
    og_desc = draft['og_description'] or desc
    related_job = draft['related_job_slug'] or ''
    date = now_date()

    def e(s):
        return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    sections = extract_h2_sections(body_html)
    faq_title = 'sec-%d' % (len(sections) + 1)

    # ── head 區塊置換 ──
    tpl = re.sub(r'<title>.*?</title>', f'<title>{e(title)}｜Step1ne</title>', tpl, count=1)
    tpl = re.sub(r'<meta name="description" content=".*?">',
                 f'<meta name="description" content="{e(desc)}">', tpl, count=1)
    tpl = re.sub(r'<meta name="keywords" content=".*?">',
                 f'<meta name="keywords" content="{e(keywords)}">', tpl, count=1)
    tpl = re.sub(r'<link rel="canonical" href=".*?">',
                 f'<link rel="canonical" href="https://step1ne.com/articles/{slug}/">', tpl, count=1)
    tpl = re.sub(r'<meta property="og:title" content=".*?">',
                 f'<meta property="og:title" content="{e(og_title)}">', tpl, count=1)
    tpl = re.sub(r'<meta property="og:description" content=".*?">',
                 f'<meta property="og:description" content="{e(og_desc)}">', tpl, count=1)
    tpl = re.sub(r'<meta property="og:url" content=".*?">',
                 f'<meta property="og:url" content="https://step1ne.com/articles/{slug}/">', tpl, count=1)
    # og:image／twitter:image 沿用原模板圖（沒有這篇專屬的圖，先不擋上線——
    # 之後想補圖再回來換掉這兩行，不是這支腳本的責任範圍）。

    # schema headline／description／date
    tpl = re.sub(r'"headline": ".*?"', f'"headline": "{e(title)}"', tpl, count=1)
    tpl = re.sub(r'"description": ".*?",\n  "datePublished"',
                 f'"description": "{e(desc)}",\n  "datePublished"', tpl, count=1)
    tpl = re.sub(r'"datePublished": ".*?"', f'"datePublished": "{date}"', tpl, count=1)
    tpl = re.sub(r'"dateModified": ".*?"', f'"dateModified": "{date}"', tpl, count=1)

    # ── breadcrumb ──
    tpl = re.sub(
        r'(<nav class="crumb"[^>]*>.*?</a><span>/</span>)[^<]*(</nav>)',
        lambda m: m.group(1) + e(title) + m.group(2), tpl, count=1, flags=re.S)

    # ── 桌面／手機目錄 ──
    toc_links = ''.join(
        f'<a class="tn" href="#{sid}" data-s="{sid}"><i></i><span>{t}</span></a>\n'
        for sid, t in sections)
    toc_links += f'<a class="tn" href="#{faq_title}" data-s="{faq_title}"><i></i><span>常見問題</span></a>'
    mtoc_links = '\n'.join(f'<a href="#{sid}">{t}</a>' for sid, t in sections)
    mtoc_links += f'\n<a href="#{faq_title}">常見問題</a>'
    n_sec = len(sections) + 1

    tpl = re.sub(r'<h2>本文共 \d+ 個段落</h2>', f'<h2>本文共 {n_sec} 個段落</h2>', tpl, count=1)
    tpl = re.sub(r'(<div class="tns">).*?(</div>\s*</aside>)',
                 lambda m: m.group(1) + toc_links + m.group(2), tpl, count=1, flags=re.S)
    tpl = re.sub(r'(<summary>本文目錄（)\d+( 段）</summary><div>).*?(</div></details>)',
                 lambda m: m.group(1) + str(n_sec) + m.group(2) + mtoc_links + m.group(3),
                 tpl, count=1, flags=re.S)

    # ── 正文（<article> 到 </article> 整段換掉）──
    faq_html = ''
    for q, a in faq:
        faq_html += (f'<details><summary><span>{e(q)}</span>'
                     f'<span class="faq-icon">+</span></summary><p>{e(a)}</p></details>\n')

    cta_html = ''
    if related_job:
        cta_html = (
            f'<div class="cta"><p>對這個職缺有興趣，或想先了解細節？</p>'
            f'<a class="btn-alt" href="/jobs/{related_job}/">看職缺詳情</a></div>')

    new_article = (
        f'<article>\n'
        f'      <span class="tag">{e(tag)}</span>\n'
        f'      <h1>{e(title)}</h1>\n'
        f'      <div class="by">\n'
        f'        <span>作者：<a href="/about/">Step1ne 獵才顧問團隊</a></span>\n'
        f'        <time class="d" datetime="{date}">發布於 {date}</time>\n'
        f'      </div>\n\n'
        f'      {body_html}\n\n'
        f'      <h2 id="{faq_title}">常見問題</h2>\n'
        f'      <div>\n{faq_html}      </div>\n\n'
        f'      {cta_html}\n'
        f'    </article>'
    )
    tpl = re.sub(r'<article>.*?</article>', new_article, tpl, count=1, flags=re.S)

    return tpl


def insert_index_card(draft):
    """在 articles/index.html 的 data-aud="job" 群組最上面插一張新卡，
    群組計數 +1。找不到那個群組就不動這個檔案（寧可漏一張卡，不要弄壞整頁）。
    """
    html = open(ARTICLES_INDEX, encoding='utf-8').read()
    m = re.search(r'(<section class="grp" data-aud="job">.*?<div class="list">\s*)', html, re.S)
    if not m:
        return False, html

    slug = draft['article_slug']
    title = draft['title'] or ''
    desc = draft['description'] or ''
    tag = draft['tag'] or '職缺情報'
    date = now_date()

    def e(s):
        return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    card = (
        f'<a class="row" href="/articles/{slug}/" data-t="{e(title)} {e(desc)} {e(tag)} {slug}">\n'
        f'        <div class="meta"><span class="tag">{e(tag)}</span></div>\n'
        f'        <div class="mid"><h3>{e(title)}</h3><p>{e(desc)}</p></div>\n'
        f'        <div class="when"><time datetime="{date}">{date.replace("-", ".")}</time><span>約 6 分鐘</span></div>\n'
        f'      </a>\n      '
    )
    new_html = html[:m.end()] + card + html[m.end():]

    # 群組計數 +1（只動第一個符合的 <span class="gn">）
    cm = re.search(r'(<section class="grp" data-aud="job">.*?<span class="gn">)(\d+)(</span>)', new_html, re.S)
    if cm:
        new_count = int(cm.group(2)) + 1
        new_html = new_html[:cm.start(2)] + str(new_count) + new_html[cm.end(2):]

    open(ARTICLES_INDEX, 'w', encoding='utf-8').write(new_html)
    return True, new_html


def git_publish(slug, title):
    os.chdir(SITE_DIR)
    subprocess.run(['git', 'add', f'articles/{slug}/index.html', 'articles/index.html'], check=True)
    r = subprocess.run(['git', 'diff', '--cached', '--quiet'])
    if r.returncode == 0:
        return None  # 沒有真的變更，不用 commit
    subprocess.run(['git', 'commit', '-m', f'新增文章：{title}'], check=True)
    subprocess.run(['git', 'push', 'deploy', 'HEAD:main'], check=True)
    out = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def verify_live(url, tries=6, delay=10):
    for _ in range(tries):
        try:
            r = urllib.request.urlopen(url, timeout=15)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def process_one(draft):
    did = draft['id']
    slug = draft['article_slug']
    if not slug or not re.fullmatch(r'[a-z0-9-]+', slug):
        D.d1(f"UPDATE article_drafts SET status='error', error='article_slug 不合法或缺漏' WHERE id={D.q(did)}")
        tg_notify(f'❌ 文章草稿 #{did} 上線失敗：article_slug 不合法或缺漏',
                  reply_to=draft.get('tg_message_id'))
        return

    dest_dir = os.path.join(SITE_DIR, 'articles', slug)
    if os.path.exists(dest_dir):
        D.d1(f"UPDATE article_drafts SET status='error', error='articles/{slug}/ 已存在，可能撞名' WHERE id={D.q(did)}")
        tg_notify(f'❌ 文章草稿 #{did} 上線失敗：articles/{slug}/ 已經存在，slug 撞名，需要人工處理',
                  reply_to=draft.get('tg_message_id'))
        return

    try:
        body_html, faq, sources = parse_draft_body(draft.get('draft_md') or '')
        page = build_page(draft, body_html, faq, sources)
        os.makedirs(dest_dir, exist_ok=True)
        with open(os.path.join(dest_dir, 'index.html'), 'w', encoding='utf-8') as f:
            f.write(page)

        insert_index_card(draft)

        commit = git_publish(slug, draft['title'] or slug)
        url = f'https://step1ne.com/articles/{slug}/'

        live_ok = verify_live(url)
        D.d1(f"UPDATE article_drafts SET status='published', "
             f"published_at={D.q(now_taipei_str())}, url={D.q(url)} WHERE id={D.q(did)}")
        note = '' if live_ok else '\n⚠️ 部署完但抓不到線上網址，請自己開連結確認一次。'
        tg_notify(f'✅ 已上線：{draft["title"]}\n{url}{note}', reply_to=draft.get('tg_message_id'))
    except Exception as e:
        D.d1(f"UPDATE article_drafts SET status='error', error={D.q(str(e)[:500])} WHERE id={D.q(did)}")
        tg_notify(f'❌ 文章草稿 #{did}（{draft.get("title","")}）上線失敗：{str(e)[:300]}\n'
                  f'資料庫狀態已標成 error，不會一直重跑，需要人工檢查。',
                  reply_to=draft.get('tg_message_id'))


def tick():
    rows = D.d1("SELECT * FROM article_drafts WHERE status='approved'")
    for row in rows or []:
        process_one(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    args = ap.parse_args()
    if args.once:
        tick()
        return
    while True:
        try:
            tick()
        except Exception as e:
            print(f'tick 失敗：{e}')
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
