"""候選人信件新版設計——Python 這邊的共用版面（2026-10-21 Jacky：「信件 UIUX 也要改」）。

跟 Worker 那邊的 src/mail_v2.js 同一套樣式（照 Jacky 核准的面談感謝信，interview_thanks_mail.py）：
淺灰藍底、白卡 16px 圓角、左上小 logo（28px）、主按鈕 #0F5F87、LINE 按鈕 #06C755、
頁尾公司名＋official@＋就服許可證。表格排版＋行內樣式，最寬 600px、手機自動縮。

只換「長相」，不換字：純文字版照原本的，HTML 從同樣的字組出來。

開關：D1 site_settings key='mail_design_v2'，值 off／test／on（沒設＝off）。
  test＝只有姓名含「測試」的人選才用新版；on＝全部候選人信件。
用到的地方：interview_daemon._recs_only_mail、doc_forward.send-forms。
（面談感謝信 interview_thanks_mail.py 本來就是新版，不受開關影響。）
"""
import html as _h
import re

FLAG_KEY = 'mail_design_v2'
SITE = 'https://step1ne.com'
LOGO = f'{SITE}/assets/step1ne-logo.png'
AVATAR = f'{SITE}/assets/acai-avatar-192.png'
LINE_URL = 'https://lin.ee/XcSWPzM'

C_BG, C_CARD, C_INK, C_MU, C_LINE = '#F4F6F8', '#FFFFFF', '#0D1B24', '#4A5A66', '#D9E0E6'
C_SOFT, C_BTN, C_GREEN = '#EEF3F7', '#0F5F87', '#06C755'
FONT = "'Noto Sans TC','PingFang TC','Microsoft JhengHei',Arial,sans-serif"
FOOTER_BASE = ['Step1ne 德仁管理顧問｜official@step1ne.com', '就業服務許可證：北市就服字第 0363 號']


def e(t):
    return _h.escape(str(t if t is not None else ''), quote=True)


_URL = re.compile(r'https?://[^\s<>"\'，。）)」]+')


def linkify(t):
    s = str(t if t is not None else '')
    out, last = [], 0
    for m in _URL.finditer(s):
        out.append(e(s[last:m.start()]))
        out.append(f'<a href="{e(m.group(0))}" style="color:{C_BTN};font-weight:700;word-break:break-all;">{e(m.group(0))}</a>')
        last = m.end()
    out.append(e(s[last:]))
    return ''.join(out)


# ── 開關 ──
def mode(query=None):
    """回傳 'off'｜'test'｜'on'。query：傳入 SQL 回 list[dict] 的函式（預設用 d1_http）。"""
    try:
        if query is None:
            import os
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_tools'))
            import d1_http
            query = lambda sql: d1_http.query(sql)['results']  # noqa: E731
        r = query(f"SELECT value FROM site_settings WHERE key='{FLAG_KEY}'")
        v = str((r[0].get('value') if r else '') or '').strip().lower()
        return v if v in ('on', 'test') else 'off'
    except Exception:
        return 'off'


def use_v2(name, query=None):
    m = mode(query)
    return m == 'on' or (m == 'test' and '測試' in str(name or ''))


# ── 零件 ──
def button(url, text, bg=C_BTN):
    return (f'<a href="{e(url)}" style="display:inline-block;background:{bg};color:#FFFFFF;text-decoration:none;'
            f'font-weight:700;font-size:15px;line-height:1.3;padding:13px 24px;border-radius:10px;">{e(text)}</a>')


def _p(inner):
    return f'<p style="margin:0 0 14px;font-size:15px;line-height:1.75;color:{C_INK};">{inner}</p>'


def _block(b):
    if isinstance(b, str):
        return _p(linkify(b)) if b.strip() else ''
    t = b.get('type')
    if t == 'p':
        return _p(linkify(b.get('text')).replace('\n', '<br>')) if str(b.get('text') or '').strip() else ''
    if t == 'note':
        return f'<p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:{C_MU};">{linkify(b.get("text"))}</p>'
    if t == 'list':
        tag = 'ol' if b.get('ordered') else 'ul'
        items = ''.join(f'<li style="margin:4px 0;">{linkify(x)}</li>' for x in b.get('items') or [])
        lst = f'<{tag} style="margin:0;padding:0 0 0 22px;font-size:15px;line-height:1.7;color:{C_INK};">{items}</{tag}>'
        if b.get('panel'):
            return f'<div style="background:{C_SOFT};border-radius:12px;padding:12px 16px;margin:0 0 18px;">{lst}</div>'
        return f'<div style="margin:0 0 16px;">{lst}</div>'
    if t == 'button':
        fb = '' if b.get('fallback') is False else (
            f'<div style="font-size:12px;line-height:1.6;color:{C_MU};margin-top:8px;">按鈕打不開的話，複製這個網址：<br>'
            f'<span style="color:{C_BTN};word-break:break-all;">{e(b["url"])}</span></div>')
        return f'<div style="margin:6px 0 20px;">{button(b["url"], b["text"])}{fb}</div>'
    if t == 'html':
        return b.get('html') or ''
    return ''


def render(*, subject='', headline='', avatar=False, blocks=(), sign=(), line=None, footer=(), footer_base=True):
    """組一封新版信的 HTML（參數意思同 src/mail_v2.js 的 renderMailV2）。"""
    sign = [x for x in (sign or []) if x is not None and str(x).strip()]
    sign_text = '\n'.join(sign)
    foot = [l for l in (FOOTER_BASE if footer_base else []) if l not in sign_text] + list(footer or [])
    foot = [l for i, l in enumerate(foot) if l and foot.index(l) == i]
    top = ''
    if avatar or headline:
        top = ('<tr><td style="padding:26px 24px 0;">'
               + (f'<img src="{AVATAR}" alt="阿財" width="56" height="56" style="width:56px;height:56px;border-radius:50%;'
                  f'display:block;margin:0 0 12px;border:0;">' if avatar else '')
               + (f'<h1 style="margin:0;font-size:21px;line-height:1.45;font-weight:700;color:{C_INK};">{e(headline)}</h1>' if headline else '')
               + '</td></tr>')
    pad = '18px' if top else '26px'
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:{C_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C_BG};">'
        '<tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:{C_CARD};'
        f'border-radius:16px;overflow:hidden;font-family:{FONT};color:{C_INK};">'
        f'<tr><td style="padding:18px 24px;border-bottom:1px solid {C_LINE};">'
        f'<img src="{LOGO}" alt="Step1ne 德仁管理顧問" height="28" style="height:28px;width:auto;display:block;border:0;"></td></tr>'
        + top
        + f'<tr><td style="padding:{pad} 24px 6px;">' + ''.join(_block(b) for b in blocks or [])
        + (f'<p style="margin:22px 0 4px;font-size:14px;line-height:1.75;color:{C_MU};">{"<br>".join(e(x) for x in sign)}</p>' if sign else '')
        + '</td></tr>'
        + ((f'<tr><td style="padding:10px 24px 22px;"><div style="background:{C_SOFT};border-radius:12px;padding:16px;">'
            + (f'<div style="font-size:14px;line-height:1.7;color:{C_INK};margin:0 0 12px;">{e(line.get("text"))}</div>' if line.get('text') else '')
            + button(line.get('url') or LINE_URL, line.get('button') or '加入 LINE 官方帳號', C_GREEN) + '</div></td></tr>') if line else '')
        + f'<tr><td style="padding:18px 24px 22px;border-top:1px solid {C_LINE};font-size:12px;line-height:1.75;color:{C_MU};text-align:center;">'
        + '<br>'.join(e(l) for l in foot) + '</td></tr>'
        '</table></td></tr></table></body></html>')


_SIGN_START = re.compile(r'^(Step1ne|德仁|Jacky|Phoebe)')


def text_to_blocks(text):
    """純文字信 → (blocks, sign)。段落用空行分；「・」「- 」開頭＝條列、「1. 」開頭＝編號條列；
    最後一段是署名（Step1ne 德仁管理顧問／Jacky…）就放到署名位置。字一字不改。"""
    paras = [p.strip('\n') for p in re.split(r'\n{2,}', str(text or '').replace('\r', '')) if p.strip()]
    sign = []
    if len(paras) > 1 and _SIGN_START.match(paras[-1].strip()):
        sign = paras.pop().split('\n')
    blocks = []
    for p in paras:
        buf, lst = [], None
        for l in p.split('\n'):
            ul = re.match(r'^\s*(?:・|-\s|•\s?)(.*)$', l)
            ol = re.match(r'^\s*\d+[.、]\s*(.*)$', l)
            if ul or ol:
                if buf:
                    blocks.append({'type': 'p', 'text': '\n'.join(buf)}); buf = []
                ordered = bool(ol) and not ul
                if not lst or lst['ordered'] != ordered:
                    if lst:
                        blocks.append(lst)
                    lst = {'type': 'list', 'ordered': ordered, 'items': [], 'panel': ordered}
                lst['items'].append((ul or ol).group(1).strip())
            else:
                if lst:
                    blocks.append(lst); lst = None
                buf.append(l)
        if lst:
            blocks.append(lst)
        if buf:
            blocks.append({'type': 'p', 'text': '\n'.join(buf)})
    return blocks, sign


def render_text(subject, text, **kw):
    blocks, sign = text_to_blocks(text)
    return render(subject=subject, blocks=blocks, sign=sign, **kw)
