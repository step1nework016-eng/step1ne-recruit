"""把一則 Threads 貼文的完整內文抓下來，給公式拆解用。

兩條路，選哪條取決於「這篇是不是我們自己顧問發的」：

  自己的 → 走官方 API。快、精準、拿得到串文的每一則，不用開瀏覽器。
  別人的 → 開一顆無頭瀏覽器把頁面跑完再讀。

為什麼別人的一定要開瀏覽器（2026-09-23 逐一實測過）：
  ・純 HTTP 抓公開頁面，只拿得到 og:description ＝ **串文的第一則**。
    把整頁 58 萬字的原始碼翻遍，後續那幾則的任何一個字都不存在。
  ・同一個網址用真瀏覽器打開，後續每一則都讀得到（Threads 會標「串文」「作者」）。
    差別在那段內容是 JavaScript 跑完才載入的。
  所以「別人的串文抓不到」是**抓法的問題**，不是平台不給。

⚠️ 這支只能在本機跑（Cloudflare Worker 沒有瀏覽器）。呼叫端是 ai_worker.py。
"""
import json
import re
import urllib.parse
import urllib.request

GRAPH = 'https://graph.threads.net/v1.0'
# 頁面渲染等待：4 秒是實測值。Threads 的串文是第二波才載入的，
# 只等 domcontentloaded 會只拿到第一則。
RENDER_WAIT_MS = 4000
BROWSER_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')


def parse_permalink(url):
    """任何 Threads 網址 → (帳號, 貼文代碼)。認不出來就回 (None, None)。"""
    m = re.search(r'/@([^/?#]+)/post/([^/?#]+)', url or '')
    return (m.group(1), m.group(2)) if m else (None, None)


def resolve_share_url(url):
    """分享短連結 → 正式永久連結。

    ⚠️ User-Agent 決定成敗，而且不能用瀏覽器的：不帶 UA 會被導去
    facebook.com/unsupportedbrowser；帶完整 Chrome UA 會回 200 不轉址；
    帶任何非瀏覽器 UA 才會給真正的網址。跟 step1ne-social-worker 同一個坑。
    """
    if '/share/' not in (url or ''):
        return url

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    req = urllib.request.Request(url, headers={'User-Agent': 'step1ne-style-source/1.0'})
    try:
        urllib.request.build_opener(_NoRedirect).open(req, timeout=20)
    except urllib.error.HTTPError as e:
        loc = e.headers.get('Location') or ''
        if loc and 'unsupportedbrowser' not in loc:
            return loc.split('?')[0]
    except Exception:
        pass
    return url


def fetch_own(url, access_token, platform_user_id):
    """自己顧問的貼文：官方 API 拿主文＋串文後續每一則。

    回 None 代表「這個網址不在這個帳號底下」——呼叫端要當成
    『這其實是別人的文』改走瀏覽器，不要當成失敗。
    """
    handle, _ = parse_permalink(url)
    want = (url or '').split('?')[0].rstrip('/')

    def _get(u):
        return json.load(urllib.request.urlopen(u, timeout=25))

    nxt = (f"{GRAPH}/{platform_user_id}/threads?"
           + urllib.parse.urlencode({'fields': 'id,permalink,text',
                                     'limit': '100', 'access_token': access_token}))
    hit = None
    for _ in range(5):
        if not hit and nxt:
            d = _get(nxt)
            for p in d.get('data', []):
                if (p.get('permalink') or '').split('?')[0].rstrip('/') == want:
                    hit = p
                    break
            nxt = (d.get('paging') or {}).get('next')
        else:
            break
    if not hit:
        return None

    parts = [(hit.get('text') or '').strip()]
    # 串文的後續是「作者自己的回覆」。別人的留言也在這支裡，所以要比對 username，
    # 不然會把路人留言當成串文的一部分拆解進公式。
    try:
        r = _get(f"{GRAPH}/{hit['id']}/replies?"
                 + urllib.parse.urlencode({'fields': 'id,text,username',
                                           'access_token': access_token}))
        for x in r.get('data', []):
            if handle and x.get('username') and x['username'].lower() != handle.lower():
                continue
            t = (x.get('text') or '').strip()
            if t:
                parts.append(t)
    except Exception:
        pass   # 拿不到後續不該讓主文一起作廢
    return {'author': handle, 'text': '\n\n---\n\n'.join(parts), 'parts': len(parts), 'via': 'api'}


def fetch_public(url):
    """別人的貼文：無頭瀏覽器跑完 JS 再讀，這樣串文後續才看得到。"""
    from playwright.sync_api import sync_playwright
    handle, _ = parse_permalink(url)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=BROWSER_UA)
            page.goto(url, timeout=45000, wait_until='domcontentloaded')
            page.wait_for_timeout(RENDER_WAIT_MS)
            body = page.inner_text('body')
        finally:
            browser.close()
    return {'author': handle, 'text': _clean_public_text(body, handle),
            'parts': None, 'via': 'browser'}


def _clean_public_text(body, handle):
    """把渲染後的整頁文字砍成「只剩這則貼文與作者自己的串文」。

    頁面下半部有「相關串文」（完全不相干的別人貼文）與登入提示，
    一起丟給 AI 拆解會拆出一套根本不存在的寫法。
    """
    txt = body or ''
    # ⚠️ 頁面語言會變（同一個網址有時回中文、有時回英文），所以中英文的切點都要列。
    # 2026-09-23 實測：只列中文的話，遇到英文版會把整段登入提示與條款一起送去拆解。
    # 取「最早出現的那個切點」，不是逐個切——後面還有內容的話才不會被前一個切點漏掉。
    cut = len(txt)
    for marker in ('相關串文', '登入或註冊 Threads', '透過 Threads 暢所欲言',
                   'Related threads', 'Log in or sign up', 'Say more with Threads',
                   'Continue with Instagram', '© 20'):
        i = txt.find(marker)
        if i > 0:
            cut = min(cut, i)
    txt = txt[:cut]
    lines = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # 介面雜訊：瀏覽數、相對時間、作者標籤、按讚數那種純數字
        if re.fullmatch(r'\d+次瀏覽|\d+\s*(小時|分鐘|天|週)|串文|作者|·|翻譯|\d+', ln):
            continue
        if handle and ln == handle:
            continue
        lines.append(ln)
    return '\n'.join(lines).strip()


def fetch(url, account=None):
    """統一入口。account 是 social_accounts 那一列（有 access_token 就先試 API）。

    回 {author, text, parts, via}；抓不到內文就 raise，讓呼叫端記成失敗，
    不要回一個空字串讓 AI 去拆解一篇空文章。
    """
    url = resolve_share_url(url)
    if account and account.get('access_token') and account.get('platform_user_id'):
        try:
            got = fetch_own(url, account['access_token'], account['platform_user_id'])
            if got and got['text']:
                return got
        except Exception:
            pass   # API 失敗就退回瀏覽器，別因為 token 過期就整條斷掉
    got = fetch_public(url)
    if not got['text']:
        raise RuntimeError('抓不到這篇的內文（可能是私人帳號、貼文已刪除，或網址不對）')
    return got
