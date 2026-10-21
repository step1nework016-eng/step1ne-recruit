"""阿財快速回覆按鈕（2026-10-21，Jacky 核准示意圖第 3 頁：~/下載項目/面談室體驗優化_示意圖_20261021.html）。

阿財問封閉題／選擇題時（能不能通勤、要不要搬、最快何時到職、是非確認、外語人選的工作許可身分），
在那則訊息下面放 2～4 個可以點的按鈕，外加前端固定放的虛線「我自己打字」。點了就等於人選自己打那句話送出。

流程：
  1. 開關：D1 site_settings key='acai_quick_replies'，值 off／test／on（沒設＝off）。
     test＝只有姓名含「測試」的應徵；on＝全部。跟 acai_job_recs 同一套。
  2. 開關有開，interview_daemon.handle() 才在 prompt 後面加 PROMPT_HINT，AI 輸出多一個 quick_replies 欄位。
     開關沒開，prompt 一個字都不變。
  3. sanitize() 再擋一次（AI 寫的不算數）：不是問句、開放題、薪資題、碰保護特徵（年齡／性別／國籍／婚育／宗教）、
     自我評價誘導題、選項有金額或客戶名、太長、少於 2 個 → 整組不給。
  4. 存進 acai_quick_replies（不寫進訊息本文，逐字稿、報告、阿財自己讀的對話都看不到按鈕）。
     after_id＝寫入阿財這輪訊息「之前」的最後一則 id。public-worker 的 GET /chat/<token>/quick
     只在「after_id 之後有阿財訊息、而且人選還沒講話」時回按鈕，所以人選一送出（打字或點按鈕）按鈕就失效。
  5. 前端：step1ne-stopgap-site/assets/interview-quick.js 畫按鈕。
"""
import datetime
import json
import re
import time

FLAG_KEY = 'acai_quick_replies'
MAX_OPTS = 4
MAX_LEN = 12      # 一顆按鈕最多幾個字（中文）
_flag_cache = {'v': None, 't': 0.0}
_table_ok = False

PROMPT_HINT = (
    '\n\n---\n\n【快速回覆按鈕】JSON 多一個欄位 "quick_replies"（字串清單）：\n'
    '・只有這輪最後一則是「封閉題或選擇題」時才給，例如：能不能通勤、願不願意搬遷、最快什麼時候可以到職、'
    '工作許可目前的狀態、「這樣理解對嗎」這類是非確認。給 2～4 個選項，每個 12 個字以內，'
    '要涵蓋各種可能的回答（例如肯定、看情況、否定都要有），用字中性，不要暗示哪個答案比較好。\n'
    '・開放題（請他描述經驗、為什麼、怎麼做）不給，輸出 "quick_replies": []。\n'
    '・**薪資、待遇、金額相關的題目一律不給**；任何預設答案可能左右他回答的題目也不給。\n'
    '・選項絕對不能碰年齡、性別、國籍、婚姻、生育、宗教，也不要出現公司名稱。\n'
    '・不用放「我自己打字」或「其他」，畫面會自動放。選項一律中文。\n'
)

# 就服法第 5 條保護特徵（跟 interview_daemon.LAW5_WORDS 同一份，再加上按鈕常見的短字）
LAW5_WORDS = [
    '性別', '男性', '女性', '男生', '女生', '限男', '限女',
    '幾歲', '年齡', '歲以下', '歲以上', '年紀多大',
    '已婚', '未婚', '結婚了嗎', '懷孕', '生育', '打算生',
    '國籍', '外籍', '哪一國人', '原住民',
    '身心障礙', '殘障', '宗教', '政黨', '容貌', '長相', '星座', '血型',
]
PROTECTED = re.compile(r'男|女|歲|年紀|婚|孕|生小孩|小孩|子女|國籍|籍|國人|族群|宗教|信仰|教會|佛|基督|政黨|身障|障礙|容貌|長相')
SALARY = re.compile(r'薪|待遇|報酬|酬勞|獎金|分紅|年終|津貼|加給|預算|多少錢|收入|價碼|期望.{0,4}(?:多少|金額)')
MONEY = re.compile(r'\d[\d,\.]*\s*(?:萬|千|元|塊|[kK])|[一二三四五六七八九十兩百千]+\s*(?:萬|千元|塊)')
OPEN_Q = re.compile(r'為什麼|為何|怎麼|如何|聊聊|說說|談談|介紹|分享|舉例|舉個例|描述|哪些|優點|缺點|印象最深')
SELF_EVAL = re.compile(r'熱忱|熱情|抗壓|優秀|能力如何|自我評價|自評|表現好|積極|認真嗎|上進')
SELF_TYPE = re.compile(r'^(?:我自己打字|自己打字|其他|其它|以上皆非|我想自己說)$')


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _q(s):
    return "'" + str(s).replace("'", "''") + "'"


# ── 開關 ──────────────────────────────────────────────────────────
def get_flag(query=None):
    """讀開關，快取 60 秒（每輪對話都會問，不要每次打 D1）。讀失敗當作 off。"""
    if _flag_cache['v'] is not None and time.time() - _flag_cache['t'] < 60:
        return _flag_cache['v']
    v = 'off'
    try:
        if query is None:
            import d1_http
            query = lambda sql: d1_http.query(sql)['results']
        r = query(f"SELECT value FROM site_settings WHERE key={_q(FLAG_KEY)}")
        v = str((r[0].get('value') if r else '') or '').strip().lower()
        v = v if v in ('off', 'test', 'on') else 'off'
    except Exception:
        v = 'off'
    _flag_cache.update({'v': v, 't': time.time()})
    return v


def eligible(flag, name):
    if flag == 'on':
        return True
    if flag == 'test':
        return '測試' in str(name or '')
    return False


# ── 擷取與檢查 ──────────────────────────────────────────────────────
def extract(result):
    if not isinstance(result, dict):
        return None
    return result.get('quick_replies')


def _is_question(t):
    return '？' in t or '?' in t


def _protected(t):
    return any(w in t for w in LAW5_WORDS) or bool(PROTECTED.search(t))


def sanitize(options, question, guard=None):
    """回傳 (按鈕清單, 原因)。任何一項不合格就整組不給（寧可沒有按鈕，也不要給有問題的按鈕）。"""
    q = str(question or '').strip()
    if not isinstance(options, list) or not options:
        return [], '沒有選項'
    if not _is_question(q):
        return [], '最後一則不是問句'
    if OPEN_Q.search(q):
        return [], '開放題'
    if SALARY.search(q) or MONEY.search(q):
        return [], '薪資／金額題'
    if _protected(q):
        return [], '題目碰保護特徵'
    if SELF_EVAL.search(q):
        return [], '自我評價誘導題'
    out = []
    for o in options:
        if not isinstance(o, str):
            return [], '選項格式不對'
        t = re.sub(r'^[\s「『"“”\'‘’]+|[\s」』"“”\'‘’。！!]+$', '', o).strip()
        if not t or SELF_TYPE.match(t) or t in out:
            continue
        if len(t) > MAX_LEN:
            return [], f'選項太長：{t[:20]}'
        if SALARY.search(t) or MONEY.search(t):
            return [], f'選項有金額：{t}'
        if _protected(t):
            return [], f'選項碰保護特徵：{t}'
        if SELF_EVAL.search(t):
            return [], f'選項是自我評價：{t}'
        if guard is not None:
            try:
                if guard.hits(t):
                    return [], '選項出現公司名稱'
            except Exception:
                return [], '客戶名檢查失敗'
        out.append(t)
    out = out[:MAX_OPTS]
    if len(out) < 2:
        return [], '少於 2 個'
    return out, 'ok'


# ── 存檔 ──────────────────────────────────────────────────────────
TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS acai_quick_replies ("
    " application_id TEXT NOT NULL,"
    " after_id INTEGER NOT NULL,"       # 寫入阿財這輪訊息之前的最後一則 id；之後有人選訊息＝按鈕失效
    " options_json TEXT NOT NULL,"
    " created_at TEXT NOT NULL,"
    " PRIMARY KEY (application_id, after_id))"
)


def save(app_id, after_id, options, d1fn):
    global _table_ok
    if not _table_ok:
        d1fn(TABLE_SQL)
        _table_ok = True
    d1fn("INSERT OR REPLACE INTO acai_quick_replies (application_id, after_id, options_json, created_at) VALUES "
         f"({_q(app_id)}, {int(after_id)}, {_q(json.dumps(options, ensure_ascii=False))}, {_q(_now())})")
