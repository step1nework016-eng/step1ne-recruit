"""TG 通知要發去哪個群組／主題（2026-10-02 Jacky：TG 拆成社群／人選／客戶三個群組）。

查 D1 的 tg_routes（Mac／WSL2 讀同一份，不用改設定檔）。查不到或出錯一律回 (None, None)，
呼叫端就照舊發原本的大群組——搬家只能是「設定一切換就整批搬」，不能因為查表失敗讓通知消失。

key 一覽（客戶群組）：
  client_approve  📎 開發信待核准（公司介紹、收尾信、夜間開發信草稿）
  client_signals  📬 開信・回信（開信、回信、退信）
  client_calls    ☎️ 電訪・日報（電話結果、今天要打的清單、開發日報、夜間開發摘要）
  client_inbound  🏢 官網詢問
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_cache = {}


def route(key):
    if key in _cache:
        return _cache[key]
    chat, thread = None, None
    try:
        import d1_http
        k = str(key).replace("'", "''")
        rows = d1_http.query(f"SELECT chat_id, thread_id FROM tg_routes WHERE key = '{k}'")['results']
        if rows and rows[0].get('chat_id'):
            chat = str(rows[0]['chat_id'])
            thread = int(rows[0]['thread_id']) if rows[0].get('thread_id') else None
    except Exception:
        pass
    _cache[key] = (chat, thread)
    return chat, thread
