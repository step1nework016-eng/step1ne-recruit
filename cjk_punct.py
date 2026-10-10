"""中文句子裡的半形標點 → 全形（E24，2026-10-10）。

為什麼：Jacky 測試場（林小安）阿財 48 則裡有 42 則是半形 `, ? : ;`（「好,了解,這部分您還沒有實際參與過。」），
對外很不專業。查到的原因：那個職缺的專業題庫（job_expertise）本身就是半形標點，題目計畫照抄、阿財再照抄計畫的題目。
其他職缺的題庫、計畫都是全形，所以其他場次幾乎沒事。模型會被素材的標點風格帶著走，所以「寫出去之前由程式確定性清洗」才是根治，
prompt 提醒只是輔助。

規則（只動「中文字旁邊」的半形，其餘一律不碰）：
  ・目標：`,` `?` `:` `;` `!` → `，` `？` `：` `；` `！`
  ・條件：該標點「前一個字是中日韓字」，或「後一個字（跳過一個空白）是中日韓字」。
  ・不動：網址（https://、?x=1）、數字（10:30、3.5、1,000、45K）、整句英文（A, B）、程式碼樣式。
  ・半形標點後面緊接一個空白、再接中文字時，一併拿掉那個空白（「好, 了解」→「好，了解」）。
"""
import re

_CJK = r'㐀-䶿一-鿿豈-﫿'
_MAP = {',': '，', '?': '？', ':': '：', ';': '；', '!': '！'}
# 前一個字是中日韓字（含常見的結尾符號「」』）》〉】」之後接的情形）
_AFTER_CJK = re.compile(r'(?<=[' + _CJK + r'」』）》〉】])([,?:;!])( ?)')
# 後一個字（可隔一個空白）是中日韓字，而前一個字不是中日韓字（例：「OK,好的」「45K,可談」）
_BEFORE_CJK = re.compile(r'(?<![' + _CJK + r'」』）》〉】])([,?:;!]) ?(?=[' + _CJK + r'])')
# 網址：整段保護起來不動
_URL = re.compile(r'https?://[^\s㐀-鿿，。！？、；：」』）》]+', re.I)


def fix_cjk_punct(text):
    if not isinstance(text, str) or not text:
        return text
    holes = []

    def stash(m):
        holes.append(m.group(0))
        return '\x00%d\x00' % (len(holes) - 1)
    t = _URL.sub(stash, text)

    def sub_after(m):
        # 後面是「空白＋非中文」就保留原本的空白（例：「好嗎? OK」→「好嗎？ OK」），後面是中文才拿掉空白
        nxt = t_ref[0][m.end():m.end() + 1]
        keep = m.group(2) if not (m.group(2) and re.match('[' + _CJK + ']', nxt or '')) else ''
        return _MAP[m.group(1)] + keep
    t_ref = [t]
    t = _AFTER_CJK.sub(sub_after, t)
    t = _BEFORE_CJK.sub(lambda m: _MAP[m.group(1)], t)
    return re.sub('\x00(\\d+)\x00', lambda m: holes[int(m.group(1))], t)


def deep_fix(obj):
    """遞迴處理 dict／list／字串（題庫、題目計畫這種 JSON 結構用）。"""
    if isinstance(obj, str):
        return fix_cjk_punct(obj)
    if isinstance(obj, list):
        return [deep_fix(x) for x in obj]
    if isinstance(obj, dict):
        return {k: deep_fix(v) for k, v in obj.items()}
    return obj


def count_halfwidth(text):
    """中文旁邊還有幾個半形標點（驗證用）。"""
    return len(re.findall(r'(?<=[' + _CJK + r'])[,?:;!]|[,?:;!](?=[' + _CJK + r'])', fix_url_free(text or '')))


def fix_url_free(text):
    return _URL.sub('', text)
