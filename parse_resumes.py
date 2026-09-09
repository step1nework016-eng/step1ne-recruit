#!/usr/bin/env python3
"""把還沒解析的履歷檔抽成純文字寫回 D1。由本機排程每 15 分鐘跑一次。

為什麼要先抽好，而不是面談當下才解析：

1. **候選人不用等。** 表單送出到他真的開始面談中間有空檔，那時候處理不佔用他的時間。
2. **省 token。** 面談每個回合都會帶著 context，PDF 塞進去等於同一份履歷被送很多次。
   純文字小得多，而且只需要抽一次。
3. **抽不出來要提早知道。** 掃描影像式的 PDF 現在就標記起來，
   面談時才不會說出「您的履歷我看過了」這種做不到的話。

用法：
    python3 parse_resumes.py          # 處理所有未解析的
    python3 parse_resumes.py --force  # 全部重抽（改了抽取邏輯時用）
"""
import base64, io, json, os, re, subprocess, sys, tempfile, datetime
import urllib.parse, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
MAX_CHARS = 12000   # 履歷再長也不會超過這個；超過通常是抽到雜訊


def env_with_cf():
    env = dict(os.environ)
    conf = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(conf):
        for line in open(conf, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v
    return env


# ── D1 走 HTTP，不再每次查詢都開一個 node ──
# 2026-09-08 加。原本每查一次就 subprocess 一個 `npx wrangler d1 execute`，
# npx 再拉起 node，一次約 100MB＋冷啟動。這支 daemon 每 8 秒輪詢一次，
# 三支 daemon 加起來一分鐘要開快 30 次 node——8GB 的機器負載衝到 17、
# 交換檔吃掉 5GB。改成直接打 D1 REST API，同樣的查詢不開任何子行程。
# HTTP 失敗一律退回原本的 wrangler：面談是候選人正在等的即時流程，
# 寧可慢也不能斷。
try:
    import d1_http as _D1H
except Exception:
    _D1H = None
_D1H_WARNED = False


def _d1_http_try(sql):
    """成功回傳結果 dict，不能用就回 None（讓呼叫端走 wrangler）。"""
    global _D1H_WARNED
    if not (_D1H and _D1H.available()):
        return None
    try:
        return _D1H.query(sql)
    except Exception as e:
        if not _D1H_WARNED:
            _D1H_WARNED = True
            try:
                log(f'D1 HTTP 失敗，改用 wrangler（只提醒這一次）：{e}')
            except Exception:
                pass
        return None


def d1(sql):
    _h = _d1_http_try(sql)
    if _h is not None:
        return _h.get('results', [])
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', DB, '--remote', '--json',
         f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-400:])
    out = r.stdout[r.stdout.index('['):]
    return json.loads(out)[0].get('results', [])


def _admin_token():
    try:
        for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
            if l.startswith('RECRUIT_ADMIN_TOKEN='):
                return l.strip().split('=', 1)[1].strip().strip("'\"")
    except Exception:
        return None
    return None


def _fetch_r2_b64(file_id):
    """新檔案（2026-09-03 之後）存在 R2，D1 的 files 表只有 metadata，
    沒有內容。跟瀏覽器下載按鈕走同一支既有端點（/admin/file/:id，
    fileB64() 的 HTTP 版本），不用另外接 R2 的 S3 相容 API。
    """
    tok = _admin_token()
    if not tok:
        return None
    req = urllib.request.Request(
        f'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/file/{urllib.parse.quote(file_id)}',
        headers={'authorization': f'Bearer {tok}', 'user-agent': 'parse_resumes/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception:
        return None
    return base64.b64encode(raw).decode()


def meaningful_ratio(text):
    """文字裡真的算「內容」的字元比例（去掉數字、空白、標點符號後）。

    2026-07-31 實測發現：一份 93 頁的作品集型履歷 PDF，pdftotext 回傳了
    1315 個字元、`.strip()` 非空、照原本邏輯算「抽取成功」——但那些字元
    有 99.7% 是頁碼跟目錄數字，去掉雜訊後只剩「個人頻道」4 個字。
    這種履歷內容其實是排版成圖片，pdftotext 抽到的只是頁碼層。
    不擋住這種情況的話，阿財會說「您的履歷我看過了」，但其實什麼都沒看到。
    """
    meaningful = re.sub(r'[\d\s\W]', '', text, flags=re.UNICODE)
    return len(meaningful) / max(len(text), 1)


def extract(raw, filename, mime):
    """回傳 (文字, 說明)。抽不出來時文字為 None，說明寫清楚原因。"""
    ext = (os.path.splitext(filename or '')[1] or '').lower()
    with tempfile.NamedTemporaryFile(suffix=ext or '.bin', delete=False) as f:
        f.write(raw); path = f.name
    try:
        if ext == '.pdf' or 'pdf' in (mime or ''):
            r = subprocess.run(['pdftotext', '-layout', path, '-'],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                text = r.stdout.strip()
                if meaningful_ratio(text) < 0.05:
                    return None, ('PDF 主要是圖片或版面排版（抽到的幾乎都是頁碼／數字），'
                                  '像是作品集或圖像化履歷，抽不到有意義的文字內容')
                return text[:MAX_CHARS], None
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    t = '\n'.join((p.extract_text() or '') for p in pdf.pages)
                if t.strip():
                    if meaningful_ratio(t.strip()) < 0.05:
                        return None, ('PDF 主要是圖片或版面排版（抽到的幾乎都是頁碼／數字），'
                                      '像是作品集或圖像化履歷，抽不到有意義的文字內容')
                    return t.strip()[:MAX_CHARS], None
            except Exception as e:
                return None, f'pdfplumber 失敗：{str(e)[:100]}'
            return None, '掃描影像式 PDF，抽不到文字層'

        if ext in ('.docx', '.doc', '.rtf'):
            r = subprocess.run(['textutil', '-convert', 'txt', '-stdout', path],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()[:MAX_CHARS], None
            return None, 'textutil 轉換失敗'

        if ext in ('.txt', '.md'):
            return raw.decode('utf-8', 'replace')[:MAX_CHARS], None

        if ext == '.xlsx':
            # 日式履歴書（日本的標準履歷表格）很多是用 Excel 範本填寫的，
            # 不是單一巧合——2026-08-12 台日兩地那個職缺已經第二次遇到。
            # 逐列把非空儲存格接起來，不重建版面，只求文字讀得到。
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
                lines = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows():
                        cells = [str(c.value).strip() for c in row
                                if c.value is not None and str(c.value).strip()]
                        if cells:
                            lines.append('　'.join(cells))
                text = '\n'.join(lines).strip()
                if not text:
                    return None, '.xlsx 檔案是空的或抽不到內容'
                if meaningful_ratio(text) < 0.05:
                    return None, '.xlsx 內容像是圖片或格式異常，抽到的文字沒有意義'
                return text[:MAX_CHARS], None
            except Exception as e:
                return None, f'.xlsx 讀取失敗：{str(e)[:100]}'

        return None, f'不支援的格式：{ext or mime or "未知"}'
    finally:
        os.unlink(path)


# 這些主機貼過來的是「檔案」，走下載＋pdftotext 那條路。
FILE_HOSTS = (
    'drive.google.com', 'docs.google.com', 'dropbox.com', 'www.dropbox.com',
    'dl.dropboxusercontent.com', '1drv.ms', 'onedrive.live.com',
    'mega.nz', 'box.com', 'app.box.com', 'icloud.com', 'www.icloud.com',
)
ALLOWED_HOSTS = FILE_HOSTS   # 舊名字還有別的地方在用，留著

# ── 2026-08-07：不在 FILE_HOSTS 的網址不再直接拒絕，改走「作品集網頁」那條路 ──
#
# 為什麼改：影音編輯、設計、行銷這類職缺，作品集網站**就是**履歷，
# 候選人不會另外做一份 PDF。原本一律擋掉，等於這幾類職缺的履歷永遠是空的，
# 阿財就在沒有資料的情況下面談（王雁群那場的實況）。
#
# ⚠️ **原本擋的理由沒有消失**：開放任意網址 = 讓填表單的人指定我們去打哪裡，
# 可以拿來探測內網或當流量跳板。所以主機白名單被換成下面這組防護，不是被拿掉：
#   1. 只走 http / https（擋掉 file:// gopher:// 之類）
#   2. 只走標準埠（擋掉指向內部服務的怪埠）
#   3. 解析出來的每一個 IP 都不能是私有／回送／link-local／保留位址
#   4. **每一次轉址都要重驗**——只驗第一跳等於沒驗，公開網址可以 302 到 169.254.169.254
#   5. 不執行 JS（headless 補抓那條例外，見 render_dom）、不送 cookie、大小上限
GUARD_SUFFIXES = ('.local', '.internal', '.lan', '.home', '.corp')
WEB_MAX_BYTES = 3 * 1024 * 1024
WEB_MIN_CHARS = 300      # 靜態抓到的字少於這個，就當它是 JS 畫出來的，補一次 headless
WEB_MAX_PAGES = 6        # 作品集的內容通常在子頁，首頁只是一張封面


def guard_url(url):
    """回傳 None 表示可以打，否則回傳擋下來的原因。"""
    import ipaddress, socket
    p = urllib.parse.urlparse(url)
    if p.scheme not in ('http', 'https'):
        return f'只接受 http/https 網址（收到 {p.scheme or "空"}）'
    if p.port not in (None, 80, 443):
        return f'不接受非標準埠：{p.port}'
    host = (p.hostname or '').lower()
    if not host:
        return '網址格式有誤，看不出主機名稱'
    if host == 'localhost' or host.endswith(GUARD_SUFFIXES):
        return f'指向內部網路，不抓：{host}'
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == 'https' else 80))
    except Exception as e:
        return f'網域解析不到：{str(e)[:80]}'
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return f'解析出無法辨識的位址：{info[4][0]}'
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return f'指向內部或保留位址（{ip}），不抓'
    return None


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """每一跳轉址都重新過 guard_url。

    只驗第一跳等於沒驗——一個看起來正常的公開網址，
    可以 302 到 http://169.254.169.254/ 這種雲端中繼資料端點。
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        err = guard_url(newurl)
        if err:
            raise urllib.error.URLError(f'轉址被擋下：{err}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def direct_download_url(url):
    """把分享連結轉成可直接下載的網址。

    分享連結打開是一個 HTML 預覽頁，不是檔案本身——
    直接抓會拿到一頁 JavaScript，然後 pdftotext 說「這不是 PDF」。

    回傳 (網址, 錯誤)。網址為 None 且錯誤為 None ＝「這不是檔案主機，
    請改走 fetch_webpage」——三態，呼叫端要分清楚。
    """
    host = urllib.parse.urlparse(url).netloc.lower()
    if host not in FILE_HOSTS:
        return None, None

    if 'google.com' in host:
        # 線上 Google 文件沒有檔案本體，要請 Google 匯出成 PDF
        m = re.search(r'/document/d/([\w-]{20,})', url)
        if m:
            return f'https://docs.google.com/document/d/{m.group(1)}/export?format=pdf', None
        m = (re.search(r'/file/d/([\w-]{20,})', url)
             or re.search(r'[?&]id=([\w-]{20,})', url))
        if m:
            return f'https://drive.google.com/uc?export=download&id={m.group(1)}', None
        return None, 'Google 連結認不出檔案 ID，請改貼「檔案」的分享連結'

    if 'dropbox' in host:
        base = re.sub(r'[?&]dl=\d', '', url)
        return base + ('&' if '?' in base else '?') + 'dl=1', None

    return url, None


UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 Chrome/120 Safari/537.36')


def html_to_text(h):
    """把 HTML 壓成純文字。不解析、不執行，就是砍標籤。"""
    import html as _html
    # 這三種標籤裡的東西不是給人看的，混進來會把有意義比例算歪
    b = re.sub(r'(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>', ' ', h)
    b = re.sub(r'(?is)<!--.*?-->', ' ', b)
    b = re.sub(r'(?i)<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>', '\n', b)
    t = _html.unescape(re.sub(r'(?s)<[^>]+>', ' ', b))
    # ⚠️ 一定要清控制字元。網頁 bytes 用 'replace' 解碼會把 \x00 留下來，
    # 那個字元帶進 wrangler 的 --command 會讓整個 subprocess 掛在
    # 「ValueError: embedded null byte」，而且錯誤訊息完全看不出跟履歷有關。
    t = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', t)
    t = re.sub(r'[ \t]+', ' ', t)
    return re.sub(r'\n{3,}', '\n\n', t).strip()


def http_get(url, limit=WEB_MAX_BYTES):
    """過完 guard 再打。回傳 (bytes, content-type)。"""
    err = guard_url(url)
    if err:
        raise RuntimeError(err)
    op = urllib.request.build_opener(_GuardedRedirect)
    req = urllib.request.Request(url, headers={'user-agent': UA})
    with op.open(req, timeout=45) as r:
        return r.read(limit), (r.headers.get('content-type') or '').lower()


def render_dom(url):
    """靜態抓不到字時，用 headless Chrome 跑完 JS 再抓一次。

    Wix / Notion / Framer 這些站台的內容是 JS 畫出來的，靜態 HTML 只有骨架。
    ⚠️ 這裡確實會執行 JS，是上面「不執行 JS」原則的**唯一例外**：
    所以只在 guard_url 已經放行、而且靜態文字太少時才走，
    而且用 --incognito，不帶使用者的 Chrome profile 與 cookie。

    ⚠️ 2026-08-10 修一個**靜默失敗**：原本用 subprocess.run(timeout=60)，但 Chrome
    把 DOM 印出來之後**不會自己結束**，於是每次都逾時丟例外、被 except 吃掉回 ''。
    這個函式等於寫了從來沒生效過，而且完全沒有錯誤訊息——候選人王雁群的 Wix 作品集
    因此只抓到 808 字（實際有 4,496 字、四個分頁），報告就寫成「履歷資訊不足」。
    現在改用 _system/webview.py，它邊讀邊存、DOM 印完就收工。
    """
    try:
        sys.path.insert(0, os.path.expanduser('~/claude-projects/工作流程技能包/_system'))
        from webview import _render
    except Exception:
        return ''
    try:
        html, _ = _render(url)
        return html_to_text(html or '')
    except Exception:
        return ''


def dedupe_chrome(pages):
    """把每一頁都出現的導覽列與頁尾拿掉。

    抓 6 個子頁的話，「Home / Portfolio / About / Contact」「Proudly created with Wix.com」
    會重複 6 次。留著會把 12000 字的額度吃掉一大半，
    而且阿財讀到滿滿的重複字串會誤以為這人的履歷很空。
    """
    from collections import Counter
    real = [p for p in pages if p]
    if len(real) < 3:
        return real
    cnt = Counter()
    for p in real:
        for line in set(x.strip() for x in p.split('\n') if x.strip()):
            cnt[line] += 1
    # 一半以上的頁面都有的那行 = 版面，不是內容
    boiler = {ln for ln, c in cnt.items() if c >= max(3, len(real) // 2 + 1)}
    out = []
    for p in real:
        kept = [x for x in p.split('\n') if x.strip() and x.strip() not in boiler]
        if kept:
            out.append('\n'.join(kept))
    return out or real


def fetch_webpage(url):
    """作品集網站當履歷用：抓首頁＋同站子頁的文字。

    回傳 (文字, 說明)。抓不到內容時文字為 None。
    """
    try:
        raw, ct = http_get(url)
    except Exception as e:
        return None, f'網頁抓取失敗：{str(e)[:140]}'
    if 'html' not in ct and not raw[:512].lower().lstrip().startswith(b'<'):
        return None, f'這個網址回的不是網頁（{ct or "型別不明"}）'

    h = raw.decode('utf-8', 'replace')
    text = html_to_text(h)
    if len(text) < WEB_MIN_CHARS:
        text = render_dom(url) or text

    # 作品集的首頁常常只是一張封面（王雁群那個首頁跑完 JS 也只有 285 字），
    # 真正的經歷在 /about /portfolio /works 這些子頁，所以要跟著同站連結走一層。
    base = urllib.parse.urlparse(url)
    norm = lambda u: u.split('?')[0].split('#')[0].rstrip('/')
    seen, parts = {norm(url)}, [text]
    links = re.findall(r'(?i)<a[^>]+href=["\']([^"\'#]+)', h)
    for href in links:
        if len(seen) >= WEB_MAX_PAGES:
            break
        nxt = urllib.parse.urljoin(url, href)
        pn = urllib.parse.urlparse(nxt)
        # 只跟同一個主機、http(s)、沒看過的
        if pn.hostname != base.hostname or pn.scheme not in ('http', 'https'):
            continue
        key = norm(nxt)
        if key in seen:
            continue
        seen.add(key)
        try:
            sub, sct = http_get(key)
            if 'html' not in sct:
                continue
            st = html_to_text(sub.decode('utf-8', 'replace'))
            if len(st) < WEB_MIN_CHARS:
                st = render_dom(key) or st
            parts.append(st)
        except Exception:
            continue      # 單一子頁抓失敗不該讓整份履歷變成 None

    joined = '\n\n'.join(dedupe_chrome(parts)).strip()
    if len(joined) < WEB_MIN_CHARS:
        return None, (f'這個網站抓到的文字太少（{len(joined)} 字，看了 {len(seen)} 頁），'
                      '內容多半是圖片或影片，沒有可讀的經歷描述——需要請他補一份文字履歷')
    if meaningful_ratio(joined) < 0.05:
        return None, '網站抓到的幾乎都是數字與符號，不是履歷內容'
    return joined[:MAX_CHARS], None


def fetch_from_url(url):
    """回傳 (bytes, 檔名, 說明)。抓不到時 bytes 為 None。"""
    direct, err = direct_download_url(url)
    if err:
        return None, None, err
    if not direct:
        # 不是檔案主機。呼叫端應該改走 fetch_webpage，走到這裡代表漏了分流。
        return None, None, '這不是檔案連結，應該走網頁解析'
    try:
        req = urllib.request.Request(direct, headers={
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 Chrome/120 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=45) as r:
            ct = (r.headers.get('content-type') or '').lower()
            cd = r.headers.get('content-disposition') or ''
            raw = r.read(8 * 1024 * 1024)          # 上限 8MB，避免被塞大檔
    except urllib.error.HTTPError as e:
        return None, None, f'下載失敗 HTTP {e.code}（多半是權限沒開成「知道連結的人可讀」）'
    except Exception as e:
        return None, None, f'下載失敗：{str(e)[:120]}'

    # 拿到一頁 HTML 通常代表需要登入，或是 Google 的病毒掃描確認頁
    if b'<html' in raw[:2000].lower() and b'%PDF' not in raw[:2000]:
        return None, None, '下載到的是網頁不是檔案，通常代表連結需要登入才能看'

    name = 'resume.pdf'
    m = re.search(r'filename\*?=(?:UTF-8\'\'|")?([^";]+)', cd)
    if m:
        name = urllib.parse.unquote(m.group(1).strip('"'))
    elif 'pdf' in ct:
        name = 'resume.pdf'
    elif 'word' in ct or 'officedocument' in ct:
        name = 'resume.docx'
    return raw, name, None


def parse_urls():
    """處理只貼了雲端連結、沒有上傳檔案的應徵者。"""
    rows = d1("SELECT id, name, resume_url FROM applications "
              "WHERE resume_url IS NOT NULL AND resume_url != '' "
              "AND resume_file_id IS NULL AND resume_url_note IS NULL LIMIT 10")
    if not rows:
        return 0, 0, []
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    q = lambda v: 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"
    ok, fail, failed_names = 0, 0, []
    for r in rows:
        url = r['resume_url']
        host = urllib.parse.urlparse(url).netloc.lower()
        text = note = None
        if host in FILE_HOSTS:
            kind = '雲端連結'
            raw, fname, err = fetch_from_url(url)
            if raw:
                text, note = extract(raw, fname, None)
            else:
                note = err
        else:
            # 不是雲端硬碟 → 當作品集網站處理。
            # guard_url 在 http_get 裡面把關，這裡不需要再擋一次主機。
            kind = '作品集網站'
            text, note = fetch_webpage(url)
        if text:
            ok += 1
            print(f"  ✅ {r['name']}（{kind}）　{len(text)} 字")
        else:
            fail += 1
            failed_names.append(f"{r['name']}（{kind}）　{note}")
            print(f"  ⚠️ {r['name']}（{kind}）　{note}")
        d1(f"UPDATE applications SET resume_url_text={q(text)}, "
           f"resume_url_note={q(note or 'ok')}, resume_url_parsed_at='{now}' "
           f"WHERE id='{r['id']}'")
    return ok, fail, failed_names


def main():
    force = '--force' in sys.argv
    # --force 只重抽「還有檔案可以重抽」的。
    # 早期有些資料只存了文字沒存檔案（前端抽取時期），對那些做 --force
    # 等於把唯一的一份文字清成 NULL——那是資料損毀，不是重新解析。
    # ⚠️ files 表不是只有候選人履歷。2026-08-11 把公司簡介 PDF 存進去當開發信附件，
    #    這支就把它當履歷解析了，還推了一則「✅ 已解析」到群組——
    #    顧問看到「阿財在解析公司簡介」只會覺得系統壞了。
    #    判準：這個檔案有沒有人拿它當履歷（applications / checkups 指向它）。
    not_resume = (
        ' AND (EXISTS (SELECT 1 FROM applications a WHERE a.resume_file_id = f.id)'
        ' OR EXISTS (SELECT 1 FROM checkups c WHERE c.resume_file_id = f.id))')
    cond = ("WHERE (f.content_b64 IS NOT NULL OR f.chunks IS NOT NULL OR f.storage='r2')" + not_resume if force
            else 'WHERE f.parsed_at IS NULL' + not_resume)
    rows = d1(f"SELECT f.id, f.filename, f.mime, f.content_b64, f.chunks, f.storage FROM files f {cond} LIMIT 20")
    url_ok, url_fail, failed = parse_urls()
    if not rows and not (url_ok or url_fail):
        print('  沒有待解析的履歷')
        return

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ok = fail = 0
    for r in rows:
        try:
            b64 = r.get('content_b64')
            if not b64 and r.get('chunks'):
                # 新資料是切塊存的：D1 單值上限約 2MB，履歷 base64 常常超過。
                # 依 idx 排序組回來，順序錯了整份檔案就壞掉。
                fid = str(r['id']).replace("'", "''")
                parts = d1(f"SELECT b64 FROM file_chunks WHERE file_id='{fid}' ORDER BY idx ASC")
                b64 = ''.join(p['b64'] for p in parts)
            # ⚠️ 2026-09-09 修：這裡原本只認 content_b64／file_chunks 這兩種
            # 舊式 D1 儲存，完全不知道 2026-09-03 之後新檔案改存 R2
            # （見 saveResume() 的 storage='r2' 分支）。結果是**所有新上傳的
            # 履歷**都被這裡判定「這份履歷沒有檔案內容」——不是真的沒有，
            # 是這支腳本找錯地方。真實案例：蘇微閔、郭鑑宸的履歷都確實存進
            # R2（size 有正常數字），卻被推播「請他重傳履歷」的假警報。
            # 修法：R2 檔案改打 /admin/file/:id 這支既有端點（fileB64() 的
            # HTTP 版本，本來就是給瀏覽器下載用的，兩種儲存方式它都認）。
            if not b64 and r.get('storage') == 'r2':
                b64 = _fetch_r2_b64(r['id'])
            if not b64:
                raise RuntimeError('這份履歷沒有檔案內容')
            text, note = extract(base64.b64decode(b64), r['filename'], r['mime'])
        except Exception as e:
            text, note = None, f'解析例外：{str(e)[:120]}'
        # 控制字元會讓後面把文字當命令列參數傳給 claude 時整個炸掉
        if text:
            text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)
        q = lambda v: 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"
        d1(f"UPDATE files SET text_content={q(text)}, parse_note={q(note)}, "
           f"parsed_at='{now}' WHERE id='{r['id']}'")
        if text:
            ok += 1
            print(f"  ✅ {r['filename']}　{len(text)} 字")
        else:
            fail += 1
            # 檔名對顧問沒意義，要找出是誰才聯絡得到人
            who = d1(f"SELECT name FROM applications WHERE resume_file_id='{r['id']}'")
            failed.append(f"{who[0]['name'] if who else r['filename']}　{note}")
            print(f"  ⚠️ {r['filename']}　{note}")

    ok += url_ok; fail += url_fail
    print(f'\n  成功 {ok}　失敗 {fail}')
    # ⚠️ 每 15 分鐘跑一次，沒東西可解析就不要記——不然一天 96 筆「今天沒事」。
    if ok or fail:
        try:
            import importlib.util as _iu, os as _os
            _sp = _iu.spec_from_file_location('d', _os.path.join(
                _os.path.dirname(_os.path.abspath(__file__)), 'interview_daemon.py'))
            _D = _iu.module_from_spec(_sp); _sp.loader.exec_module(_D)
            _D.runlog('step1ne-resume-parse', 'success' if not fail else 'partial',
                      f'解析履歷：成功 {ok} 份'
                      + (f'、失敗 {fail} 份（阿財面談時會看不到內容）' if fail else ''),
                      {'parsed': ok, 'failed': fail})
        except Exception as _e:
            print(f'  （執行紀錄寫入失敗，不影響解析：{_e}）')
    # 失敗的要讓人知道——履歷抽不出來，面談品質會直接掉一個檔次
    if fail:
        try:
            # 獨立設定檔，不要跟總指揮 yuqi 共用的 tg.env 混在一起
            conf = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
            e = dict(l.strip().split('=', 1) for l in open(conf, encoding='utf-8')
                     if '=' in l and not l.startswith('#'))
            import urllib.parse, urllib.request
            body = {
                'chat_id': e['TG_CHAT_ID'],
                'text': f'⚠️ {fail} 份履歷讀不到，阿財面談時會看不到內容\n\n'
                        + '\n'.join(f'• {n}' for n in failed)
                        + '\n\n這幾位需要請他重傳履歷。'}
            if e.get('TG_THREAD_ID'):
                body['message_thread_id'] = e['TG_THREAD_ID']
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                data=urllib.parse.urlencode(body).encode(), timeout=20)
        except Exception:
            pass


if __name__ == '__main__':
    main()
