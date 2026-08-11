#!/usr/bin/env python3
"""反向開發的 Telegram 送審：一家公司一則訊息，三顆按鈕。

為什麼一家一則、不是整批一則：顧問的決定是「這一家要不要寄」，
不是「這批要不要寄」。整批一則的話，五家裡有一家不想寄，
他只能整批退回，然後另外四家白等。
"""

import json
import os
import urllib.error
import urllib.request

TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')
# 🚨 開發信不可以丟「面試通知確認」(2855)。
#    2026-08-11 第一批試跑丟進去，一次 6 則直接把面試的訊息洗掉——
#    Jacky 當場反應「為什麼你要一直發在面試確認」。
#    反向開發一批就好幾則，一定要自己一個主題。
#    2026-08-11 Jacky 定：改丟「系統回報」(1360)。
#    可用 TG_THREAD_BD 覆寫（之後如果另開「客戶開發」主題的話）。
THREAD_BD_ENV = 'TG_THREAD_BD'
THREAD_BD_DEFAULT = 1360   # 系統回報
API = 'https://api.telegram.org/bot{}/{}'


def conf():
    c = {}
    try:
        for line in open(TG_ENV, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            c[k.strip()] = v.strip().strip('\'"')
    except FileNotFoundError:
        pass
    return c


def _post(method, payload):
    c = conf()
    tok, chat = c.get('TG_BOT_TOKEN'), c.get('TG_CHAT_ID')
    if not tok or not chat:
        print('⚠️ 找不到 Telegram 設定，訊息沒有推出去')
        return None
    thread = c.get(THREAD_BD_ENV) or THREAD_BD_DEFAULT
    payload.setdefault('chat_id', chat)
    payload.setdefault('message_thread_id', int(thread))
    req = urllib.request.Request(
        API.format(tok, method),
        data=json.dumps(payload).encode(),
        headers={'content-type': 'application/json',
                 # Cloudflare 會擋 Python-urllib 的預設 UA
                 'user-agent': 'step1ne-bd/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get('result', {}).get('message_id')
    except urllib.error.HTTPError as e:
        print(f'⚠️ Telegram 回 {e.code}：{e.read()[:200]}')
    except Exception as e:
        print(f'⚠️ Telegram 推送失敗：{e}')
    return None


def send_head(text):
    return _post('sendMessage', {'text': text, 'disable_web_page_preview': True})


def buttons(bid):
    return {'inline_keyboard': [[
        {'text': '📤 核准寄出', 'callback_data': f'bd_ok:{bid}'},
        {'text': '✏️ 重寫',     'callback_data': f'bd_rw:{bid}'},
        {'text': '❌ 不寄',     'callback_data': f'bd_no:{bid}'},
    ]]}


def send_letter(bid, t, has_cv=True):
    """一封信一則訊息。

    顧問要判斷的第一件事是「這封有沒有錨點」——
    有查到對方在徵什麼，這封的回覆率跟沒查到差很多。所以錨點放在最上面。
    """
    to = t.get('contact_email') or '（窗口待補，核准前要先填）'
    if t.get('probe'):
        anchor = '🔍 探詢版（查不到公開職缺，主動問需求）'
    elif t.get('job_title'):
        anchor = (f"🎯 對到職缺：{_esc(t.get('job_title'))}"
                  f"（{_esc(t.get('job_source') or '來源未填')}）")
    else:
        anchor = '⚠️ 沒有職缺錨點'
    att = '📎 匿名履歷 ＋ 公司簡介' if has_cv else '⚠️ 只有公司簡介，匿名履歷產製失敗'
    text = (f"✉️ <b>{_esc(t.get('company'))}</b>\n"
            f"{anchor}\n"
            f"收件：{_esc(to)}"
            + (f"　·　{_esc(t.get('contact_name'))}" if t.get('contact_name') else '') + '\n'
            f"{att}\n"
            f"────────────\n"
            f"<b>{_esc(t.get('subject'))}</b>\n\n"
            f"{_esc((t.get('body') or '')[:2400])}")
    return _post('sendMessage', {
        'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True,
        'reply_markup': buttons(bid)})


def _esc(s):
    return (str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))
