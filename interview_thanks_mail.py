"""面談後感謝信（HTML 設計版＋純文字版）。2026-10-21 Jacky 核准的樣板：~/下載項目/面談感謝信_樣板_20261021.html

只負責「組信」，不查資料庫、不寄信：呼叫端（interview_daemon.invite_assessment_after_interview／
send_normal_thanks）把資料準備好丟進來，拿回 (subject, text, html) 放進 mail_outbox，由 mail_outbox_send.py
用 multipart/alternative 寄出（純文字＋HTML 兩份）。

兩個版本：
  - 快速應徵：照樣板——上傳履歷（按鈕）→ 工作風格測驗（選填）→ 1～2 個工作天內聯絡 → 推薦給企業
  - 原本流程：履歷那步顯示「✓ 履歷已收到」沒按鈕；測驗做過了就不出現測驗那步
推薦職缺（acai_job_recs 的公開欄位：職稱、地點、公開薪資、為什麼、連結）沒有就整塊不出現。

候選人文字規則（feedback_candidate_facing_copy_rules）：不寫「客戶」（用企業／用人單位）、
不用顧問／我們當主詞對他做事、內部備註不外流。樣板文字已經照這個寫，改字時一樣要守。
"""
import html as _h
import re

SITE = 'https://step1ne.com'
LOGO = f'{SITE}/assets/step1ne-logo.png'
AVATAR = f'{SITE}/assets/acai-avatar-192.png'    # webp 有些信箱不顯示，用 PNG
LINE_URL = 'https://lin.ee/XcSWPzM'
FOOTER_TEXT = ['Step1ne 德仁管理顧問', 'Email：official@step1ne.com', '就業服務許可證：北市就服字第 0363 號']

C_BG, C_TX, C_MU, C_LINE = '#F4F6F8', '#0D1B24', '#4A5A66', '#D9E0E6'
C_OK, C_NOW, C_NOW_DK = '#2F7A4D', '#167EB2', '#0F5F87'
FONT = "'Noto Sans TC','PingFang TC','Microsoft JhengHei',Arial,sans-serif"


def e(t):
    return _h.escape(str(t or ''), quote=True)


def resume_url(token):
    return f'{SITE}/resume/?t={token}'


def assessment_url(token):
    return f'{SITE}/assessment/?t={token}&after=1'


def _rec_meta(r):
    """地點｜薪資（有具體數字才放薪資，不然放僱用型態）——跟樣板一樣。只用公開欄位。"""
    loc = str(r.get('location') or '').strip()
    sal = str(r.get('salary') or '').strip()
    emp = str(r.get('employment') or '').strip()
    second = sal if re.search(r'\d', sal) else emp
    return '｜'.join(x for x in (loc, second) if x)


def _dot_done():
    return (f'<div style="width:22px;height:22px;border-radius:50%;background:{C_OK};color:#fff;font-size:13px;'
            f'line-height:22px;text-align:center;">✓</div>')


def _dot_now():
    return f'<div style="width:22px;height:22px;border-radius:50%;background:{C_NOW};"></div>'


def _dot_todo():
    return f'<div style="width:18px;height:18px;border-radius:50%;border:2px solid {C_LINE};"></div>'


def _step(dot, label, label_color, title, body_html='', last=False):
    pad = '0 0 6px' if last else '0 0 16px'
    return (f'<tr><td width="34" valign="top" style="padding-top:2px;">{dot}</td>'
            f'<td style="padding:{pad};"><div style="font-size:12px;font-weight:700;color:{label_color};">{e(label)}</div>'
            f'<div style="font-size:15.5px;font-weight:700;">{e(title)}</div>{body_html}</td></tr>')


def _button(url, text, bg=C_NOW_DK, pad='12px 22px'):
    return (f'<a href="{e(url)}" style="display:inline-block;background:{bg};color:#FFFFFF;text-decoration:none;'
            f'font-weight:700;font-size:15px;padding:{pad};border-radius:10px;">{e(text)}</a>')


def build(*, name, job_title, token, has_resume, show_assessment, recs=None, done_label='剛剛'):
    """回傳 (subject, text, html)。

    has_resume：True → 履歷那步顯示「✓ 履歷已收到」不放按鈕；False → 放上傳履歷按鈕（/resume/?t=…）
    show_assessment：True → 放「工作風格測驗（選填）」那步（/assessment/?t=…&after=1）
    recs：acai_job_recs 的公開 view（title/location/salary/employment/why/url）；空的就整塊不出現
    done_label：「完成線上面談」那步上面的小字；房間關了一段時間才寄的（中途離開）用「已完成」
    """
    name = str(name or '').strip()
    job = str(job_title or '').strip()
    recs = [r for r in (recs or []) if r and r.get('title') and r.get('url')][:3]
    r_url, a_url = resume_url(token), assessment_url(token)

    subject = f'感謝您參加「{job}」線上面談' + ('' if has_resume else '｜上傳履歷讓顧問幫您推薦')

    # ── 時間軸 ──
    steps = [_step(_dot_done(), done_label, C_OK, '完成線上面談')]
    if has_resume:
        steps.append(_step(_dot_done(), '已完成', C_OK, '✓ 履歷已收到'))
    else:
        steps.append(_step(_dot_now(), '現在就能做', C_NOW_DK, '上傳履歷',
                           f'<div style="font-size:13.5px;color:{C_MU};margin:2px 0 10px;">有履歷，才能把您推薦給企業。'
                           f'手機拍照、截圖、PDF、Word 都可以。</div>' + _button(r_url, '上傳履歷')))
    if show_assessment:
        steps.append(_step(_dot_todo(), '選填', C_MU, '工作風格測驗（約 5 分鐘）',
                           f'<div style="font-size:13.5px;color:{C_MU};margin-top:2px;">做完可以看到您的工作風格報告。'
                           f'<a href="{e(a_url)}" style="color:{C_NOW_DK};font-weight:700;">做測驗 →</a></div>'))
    steps.append(_step(_dot_todo(), '1～2 個工作天內', C_MU, '顧問跟您聯絡',
                       f'<div style="font-size:13.5px;color:{C_MU};margin-top:2px;">確認細節、回答您的問題</div>'))
    steps.append(_step(_dot_todo(), '確認後', C_MU, '推薦給企業，安排面試', last=True))

    # ── 推薦職缺 ──
    recs_html = ''
    if recs:
        cards = []
        for i, r in enumerate(recs):
            mb = '6px' if i == len(recs) - 1 else '8px'
            why = str(r.get('why') or '').strip()
            cards.append(
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#fff;'
                f'border:1px solid {C_LINE};border-radius:10px;margin:0 0 {mb};"><tr><td style="padding:12px 14px;">'
                f'<div style="font-size:15px;font-weight:700;">{e(r["title"])}</div>'
                f'<div style="font-size:12.5px;color:{C_MU};margin:2px 0 6px;">{e(_rec_meta(r))}</div>'
                + (f'<div style="font-size:13px;background:#E3F1E8;color:#1E4F33;border-radius:6px;padding:6px 8px;'
                   f'margin:0 0 8px;">為什麼可能適合：{e(why)}</div>' if why else '')
                + f'<a href="{e(r["url"])}" style="font-size:13.5px;color:{C_NOW_DK};font-weight:700;'
                  f'text-decoration:none;">看職缺 →</a></td></tr></table>')
        recs_html = (
            f'<tr><td style="padding:16px 24px 6px;"><div style="background:#EEF3F7;border-radius:14px;padding:16px;">'
            f'<div style="font-size:16.5px;font-weight:700;margin:0 0 2px;">您可能也適合這些職缺</div>'
            f'<div style="font-size:12.5px;color:{C_MU};margin:0 0 12px;">依您今天聊的內容挑的，方向接近，顧問會再確認</div>'
            + ''.join(cards) +
            f'<div style="font-size:11.5px;color:{C_MU};margin-top:6px;">這是依面談內容初步挑的，不代表一定會推薦；'
            f'想多了解哪一個，回覆這封信或在 LINE 告訴我們就好。</div></div></td></tr>')

    html = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{e(subject)}</title></head>'
        f'<body style="margin:0;padding:0;background:{C_BG};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C_BG};">'
        '<tr><td align="center" style="padding:24px 12px;">'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;'
        f'background:#FFFFFF;border-radius:16px;overflow:hidden;font-family:{FONT};color:{C_TX};">'
        f'<tr><td style="padding:18px 24px;border-bottom:1px solid {C_LINE};">'
        f'<img src="{LOGO}" alt="Step1ne" height="28" style="height:28px;display:block;"></td></tr>'
        '<tr><td style="padding:28px 24px 8px;text-align:center;">'
        f'<img src="{AVATAR}" alt="" width="72" height="72" style="width:72px;height:72px;border-radius:50%;display:inline-block;">'
        '<h1 style="margin:12px 0 6px;font-size:22px;line-height:1.4;">感謝您參加線上面談！</h1>'
        f'<p style="margin:0;font-size:15px;line-height:1.7;color:{C_MU};">{e(name)} 您好，謝謝您花時間跟阿財聊「{e(job)}」。'
        '<br>您的回答已經整理好，接下來會這樣進行：</p></td></tr>'
        '<tr><td style="padding:16px 24px 4px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + ''.join(steps) + '</table></td></tr>'
        + recs_html +
        '<tr><td style="padding:18px 24px 8px;text-align:center;">'
        + _button(LINE_URL, '加 LINE，進度第一時間通知您', bg='#06C755', pad='12px 26px') + '</td></tr>'
        f'<tr><td style="padding:18px 24px 22px;border-top:1px solid {C_LINE};font-size:12px;line-height:1.7;'
        f'color:{C_MU};text-align:center;">Step1ne 德仁管理顧問｜official@step1ne.com<br>'
        '就業服務許可證：北市就服字第 0363 號<br>有任何問題，直接回覆這封信就可以。</td></tr>'
        '</table></td></tr></table></body></html>')

    # ── 純文字版（收不到 HTML 的信箱看這份；內容跟 HTML 一樣） ──
    t = [f'{name} 您好，', '', f'謝謝您花時間跟阿財聊「{job}」。您的回答已經整理好，接下來會這樣進行：', '',
         f'✓ 完成線上面談（{done_label}）']
    if has_resume:
        t.append('✓ 履歷已收到')
    else:
        t += ['→ 現在就能做：上傳履歷', '  有履歷，才能把您推薦給企業。手機拍照、截圖、PDF、Word 都可以：', f'  {r_url}']
    if show_assessment:
        t += ['○ 選填：工作風格測驗（約 5 分鐘），做完可以看到您的工作風格報告：', f'  {a_url}']
    t += ['○ 1～2 個工作天內：顧問跟您聯絡，確認細節、回答您的問題', '○ 確認後：推薦給企業，安排面試']
    if recs:
        t += ['', '【您可能也適合這些職缺】依您今天聊的內容挑的，方向接近，顧問會再確認']
        for i, r in enumerate(recs, 1):
            t.append(f'{i}. {r["title"]}｜{_rec_meta(r)}')
            if r.get('why'):
                t.append(f'   為什麼可能適合：{r["why"]}')
            t.append(f'   {r["url"]}')
        t.append('這是依面談內容初步挑的，不代表一定會推薦；想多了解哪一個，回覆這封信或在 LINE 告訴我們就好。')
    t += ['', f'加 LINE，進度第一時間通知您：{LINE_URL}', '', *FOOTER_TEXT, '有任何問題，直接回覆這封信就可以。']
    return subject, '\n'.join(t), html
