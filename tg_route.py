"""TG 通知要發去哪個群組／主題（2026-10-02 Jacky：TG 拆成社群／人選／客戶三個群組）。

查 D1 的 tg_routes（Mac／WSL2 讀同一份，不用改設定檔）。查不到或出錯一律「照舊」，
搬家只能是「設定一切換就整批搬」，不能因為查表失敗讓通知消失。

兩種用法：
  route(key)            指名要某一類（例：client_calls）→ (chat_id, thread_id) 或 (None, None)
  remap(chat, thread)   人選群組用：原本要發到「舊群組的某個主題」的通知，查 key='remap:<chat>:<thread>'
                        有對應就換成新群組的主題，沒有就原樣回傳。這樣十幾支程式不用一支一支改去向。

常駐程式（阿財、阿福…）也會讀到最新設定：結果只快取 60 秒。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_cache = {}
_TTL = 60


def _lookup(key):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    val = (None, None)
    try:
        import d1_http
        k = str(key).replace("'", "''")
        rows = d1_http.query(f"SELECT chat_id, thread_id FROM tg_routes WHERE key = '{k}'")['results']
        if rows and rows[0].get('chat_id'):
            val = (str(rows[0]['chat_id']), int(rows[0]['thread_id']) if rows[0].get('thread_id') else None)
    except Exception:
        val = hit[1] if hit else (None, None)   # 查表失敗沿用上一次的結果
    _cache[key] = (time.time(), val)
    return val


def route(key):
    return _lookup(key)


def remap(chat, thread):
    if chat is None or thread is None:
        return chat, thread
    c, t = _lookup(f'remap:{chat}:{thread}')
    if c and t:
        return c, t
    return chat, thread
