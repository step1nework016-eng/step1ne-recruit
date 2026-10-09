"""阿財對話紀錄裡的「系統過渡語」標記（E17 第 1 件，2026-10-08）。

為什麼有這支小模組：
人選講完話、阿財超過 N 秒還沒回，由**程式**（不是 LLM）送一句固定的話，
讓人選知道有人在處理，不用乾等（也不用傳「你還好？」，那會讓阿財多算一輪、甚至重問）。

這句話必須存進 messages（人選的畫面靠 messages 顯示，而且這張表是法律證據），
但 messages 沒有「類型」欄位、也不能改正式資料庫的結構，所以用「內容完全相同」來認它。
認得出來之後，所有「判斷誰在等誰」「餵給 LLM 的對話歷史」「報告」都要把它排除：

  ・pending()／stale()／post_wrap／close_paused 用的「最後一則」——不排除的話，
    送了過渡語之後最後一則變成 assistant，阿財會以為人選已經被回了，**永遠不再回他**。
  ・給 LLM 的對話歷史、報告逐字稿、報告的訊息數——不排除的話阿財會以為自己講過話。
  ・出錯時寫道歉訊息前的「最後一則是不是 assistant」檢查。

只改文字要同步改這裡的 TRANSITION_TEXTS；舊文字要留著（歷史紀錄裡已經存在的那句還是要認得）。
"""

ENTRY_MARKER = '（候選人已進入面談室）'

# E17b（2026-10-09 Mac 審核）：三句輪替，**一字不差**——網站面談頁（interview/index.html）用這三句
# 判斷「正在輸入」要不要繼續亮，改字要兩邊同步。
TRANSITION_TEXTS = (
    '收到，我整理一下您剛剛說的，稍等我一下。',
    '好的，我看一下您剛剛提到的內容，馬上回您。',
    '了解，我想一下接著要聊什麼，請稍等一下。',
)


def is_transition(text):
    return (text or '').strip() in TRANSITION_TEXTS


def pick_transition(last_text=None, rng=None):
    """挑下一句：隨機，但不跟這一場上一次送的同一句（last_text）。"""
    import random
    pool = [t for t in TRANSITION_TEXTS if t != (last_text or '').strip()] or list(TRANSITION_TEXTS)
    return (rng or random).choice(pool)


def _sql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def sql_not_transition(col='content'):
    """SQL 片段：這一欄不是過渡語。例：f"... WHERE application_id=... AND {sql_not_transition('m.content')}"。"""
    return f"{col} NOT IN (" + ", ".join(_sql_str(t) for t in TRANSITION_TEXTS) + ")"


def sql_send_transition(app_id_sql, now_sql, text=None):
    """一句 INSERT ... SELECT ... WHERE：把「該不該送」的判斷和寫入放在**同一個原子操作**裡，
    兩個程序（主力機＋備援機）同時撞到同一場，也只會有一個寫得進去。

    只有同時成立才會寫入：
      1. 這場最後一則訊息是人選講的（不是已經被回了、也不是別的過渡語）
      2. 最後一則不是「進入面談室」的標記（那時他還沒講過話，「您剛剛說的」不通）
      3. 上一則真正的 assistant 訊息之後，還沒有送過這句（每一輪最多一次）
    app_id_sql / now_sql 是呼叫端已經加過引號的 SQL 字串（daemon 的 q()）。
    """
    txt = _sql_str(text or TRANSITION_TEXTS[0])
    entry = _sql_str(ENTRY_MARKER)
    real_asst = sql_not_transition('content')
    return (
        "INSERT INTO messages (application_id, role, content, created_at) "
        f"SELECT {app_id_sql}, 'assistant', {txt}, {now_sql} "
        f"WHERE (SELECT role FROM messages WHERE application_id = {app_id_sql} ORDER BY id DESC LIMIT 1) = 'candidate' "
        f"AND (SELECT content FROM messages WHERE application_id = {app_id_sql} ORDER BY id DESC LIMIT 1) != {entry} "
        f"AND NOT EXISTS (SELECT 1 FROM messages WHERE application_id = {app_id_sql} AND NOT ({real_asst}) "
        f"AND id > COALESCE((SELECT MAX(id) FROM messages WHERE application_id = {app_id_sql} "
        f"AND role = 'assistant' AND {real_asst}), 0))"
    )
