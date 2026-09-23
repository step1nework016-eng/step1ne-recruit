"""開發信的署名。

為什麼獨立成一支而不是寫進 prompt：2026-09-23 連續兩版，AI 都把署名漏掉，
少了電話與就服許可字號——那是陌生開發信最重要的信任證明，對方無從判斷
我們是不是合法業者。**固定不變的東西就不該由 AI 產。**
"""

SIGNATURE = """Jacky Chen｜德仁管理顧問有限公司（Step1ne）
電話／LINE ID：0958616744
就業服務許可：北市就服字第 0363 號｜臺北市政府勞動局 114 年度評鑑 A 級
https://step1ne.com/commission-recruiting/"""


def ensure(body):
    """信尾沒有署名就接上。已經有的不重複加（用就服字號當判斷依據）。"""
    b = (body or '').rstrip()
    if '0363' in b and '0958616744' in b:
        return b
    return b + '\n\n' + SIGNATURE
