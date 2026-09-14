#!/usr/bin/env python3
"""顧問版社群發文 agent：把還沒發過文的職缺，轉成「全民獵才」風格的招募
文案，推到 Telegram 給顧問核准。

這支只負責「產草稿→推 Telegram 給顧問審核」。實際呼叫平台 API 發文的
那一步在 Worker 裡（src/index.js 的 soc_approve callback），不是在這支
腳本——顧問在 Telegram 按下「確認發布」的當下，Worker 直接用
THREADS_ACCESS_TOKEN／THREADS_USER_ID（wrangler secret）呼叫 Threads
API 貼出去。這支腳本完全不碰平台 API、也不需要拿到那兩把金鑰。
LinkedIn 還沒申請，之後金鑰到位後一樣是加在 Worker 那個 callback 裡。

跟 interview_daemon.py／checkup_daemon.py 不是同一種東西：那兩支是要
即時回候選人訊息的常駐輪詢程式；這支沒有「候選人在等」的急迫性，設計成
**一次性腳本**，用 launchd 排程（例如每天早上跑一次）就夠，不需要常駐。

用法：
    python3 social_post_agent.py           # 掃全部還沒產過草稿的職缺
    python3 social_post_agent.py <slug>    # 只處理指定職缺（測試用）
    python3 social_post_agent.py --repost <slug>   # 職缺內容改過，重新產一次草稿
"""
import ast, json, os, re, subprocess, sys, time, datetime, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'

SKILL_PATH = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/social-recruiting-post/SKILL.md')

# 貼文用的模型——這是一次性、非即時的工作，用跟阿財/阿福一樣的模型即可，
# 不需要另外挑更貴或更快的。
POST_MODEL = 'claude-sonnet-5'
CLAUDE_TIMEOUT = 180

# ⚠️ 2026-08-27：這個值同時也是「Jacky - Threads」那個帳號的專屬 topic。
# 後果：任何帳號忘了設 tg_thread_id，通知就會安靜地流進 Jacky 的房間，
# 而且看起來完全像正常運作——三個 LinkedIn 帳號就是這樣，EYLISE 的貼文
# 審核通知跑到 Jacky 那邊，Jacky 以為是自己的貼文、去自己的 LinkedIn 找，
# 當然找不到。帳號的 topic 已經補齊，這裡再加一道：沒設的不要猜，明著講。
TG_THREAD_SOCIAL = 3306  # 共用的社群發文主題（同時是 Jacky 的個人主題，見上）


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def env_with_cf():
    env = dict(os.environ)
    for conf in ('~/.config/workflow-os/cf.env',):
        p = os.path.expanduser(conf)
        if os.path.exists(p):
            for line in open(p, encoding='utf-8'):
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k] = v.strip().strip("'\"")
    return env


def d1_raw(sql):
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', DB, '--remote', '--json', f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f'd1 失敗：{(r.stderr or r.stdout)[-500:]}')
    out = r.stdout[r.stdout.index('['):]
    data = json.loads(out)
    return data[0] if data else {'results': [], 'meta': {}}


def d1(sql):
    return d1_raw(sql).get('results', [])


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def sanitize(t):
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)


def skill(account_id=None):
    """每個顧問的貼文語氣／格式可能完全不同（2026-08-17 EYLISE 那份跟 Jacky
    原本的技能包結構就不一樣）——指定帳號有自己的 skill_prompt 就用那份，
    沒有的（沒指定帳號的舊職缺，或帳號沒填自訂提示詞的）退回預設檔案，
    行為跟改之前一樣。"""
    if account_id:
        rows = d1(f"SELECT skill_prompt FROM social_accounts WHERE id={q(account_id)}")
        if rows and rows[0].get('skill_prompt'):
            return rows[0]['skill_prompt']
    return open(SKILL_PATH, encoding='utf-8').read()


SITE = 'https://step1ne.com'


def public_page_text(slug):
    """抓公開職缺頁的實際文字，當作產稿素材。

    ⚠️ 2026-08-19 事故的真正原因就在這裡：jobs 資料表**沒有「工作內容」欄位**
       （欄位清單裡真的沒有 description），工作內容只長在網站職缺頁上。
       所以白名單餵給模型的東西是「職稱＋地點＋薪資＋必備技能」，
       模型看不到這個缺實際在做什麼，只好整篇寫「［待補］」發出去。

    為什麼引用公開頁是安全的：那一份就是我們自己對外刊出的文案，
    已經過保密與就服法把關；不像 notes／talking_points 混著內部指示。
    抓不到頁面就回空字串，讓流程照舊——素材少總比拿錯素材好。
    """
    try:
        req = urllib.request.Request(f'{SITE}/jobs/{slug}/',
                                     headers={'user-agent': 'step1ne-social/1.0'})
        html = urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'replace')
    except Exception as e:
        log(f'（抓不到公開職缺頁，只用資料庫欄位產稿：{e}）')
        return ''
    body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'\s+', ' ', body).strip()
    return body[:5000]


_CLIENT_NAMES_CACHE = None


def client_name_terms():
    """所有不該出現在社群貼文裡的客戶識別字：正式名、別名、以及職缺自己
    記的 client_name。

    ⚠️ 2026-08-26 加。在這之前這支只擋了「不要把 jobs.client_name 這個欄位
    餵進 prompt」，但真正的漏洞在 public_page_text()——它把**整個公開職缺頁**
    的文字倒進 prompt，而那些頁面是 client_named=1、網站上本來就具名的，
    「律准專注於高科技廠房…」「海德生貿易是 Indian Motorcycle 台灣總代理」
    整段都在裡面。模型當然照抄。網站可以具名跟社群可以具名是兩件事，
    這條規則從頭到尾只在「欄位」那一層守，頁面文字那條路完全沒守。
    """
    global _CLIENT_NAMES_CACHE
    if _CLIENT_NAMES_CACHE is not None:
        return _CLIENT_NAMES_CACHE
    terms = set()
    try:
        for r in d1("SELECT display_name, aliases FROM client_companies") or []:
            for v in [r.get('display_name')] + str(r.get('aliases') or '').split('\n'):
                v = (v or '').strip()
                if len(v) >= 2:
                    terms.add(v)
                    # 「律准科技股份有限公司」要連「律准」都擋，不然去掉後綴就漏了
                    base = re.sub(r'(股份有限公司|有限公司|集團|公司|科技|國際開發)$', '', v).strip()
                    if len(base) >= 2:
                        terms.add(base)
        for r in d1("SELECT DISTINCT client_name FROM jobs WHERE client_name IS NOT NULL") or []:
            v = (r.get('client_name') or '').strip()
            # 只取真正像公司名的短字串；「苗栗銅鑼建廠專案廠區用人單位」那種
            # 本來就是遮蔽後的說法，拿去比對只會誤殺
            if 2 <= len(v) <= 20 and '（' not in v:
                terms.add(v)
    except Exception as e:
        log(f'⚠️ 讀不到客戶名單，這次無法做客戶名稱稽核：{e}')
        return []
    # ⚠️ 三個字以內的純英文別名一律不用（例：帆宣的別名「MIC」）。
    # 那種字在正常文案裡本來就會出現——影音編輯的稿子寫「MIC 收音」會被
    # 當成洩漏客戶名擋下來，擋久了顧問就不看警告了，那才是真正的風險。
    # 這些客戶都還有中文名在名單裡（帆宣、帆宣系統…），不會因此漏掉。
    dropped = sorted(t for t in terms if re.fullmatch(r'[A-Za-z0-9]{1,3}', t))
    if dropped:
        log(f'（別名太短、容易誤判，不列入客戶名稱稽核：{"、".join(dropped)}）')
    terms -= set(dropped)
    _CLIENT_NAMES_CACHE = sorted(terms, key=len, reverse=True)
    return _CLIENT_NAMES_CACHE


def _term_pattern(t):
    """英文短字（MIC、Micron…）要加單字邊界，不然「MIC 收音」「microphone」
    這種正常內容會被誤判成客戶名稱——影音編輯的稿子就很可能出現。
    中文沒有單字邊界的概念，直接比對；誤判的代價只是重產一次＋人工看一眼，
    比漏掉一次把客戶名字公開貼出去輕得多。"""
    if re.fullmatch(r'[A-Za-z0-9 .&-]+', t):
        return re.compile(r'(?<![A-Za-z0-9])' + re.escape(t) + r'(?![A-Za-z0-9])', re.I)
    return re.compile(re.escape(t))


def mask_client_names(text):
    """把公開頁文字裡的客戶識別字換掉再餵給模型。
    模型看不到名字，就寫不出名字——這比事後叫它「不要寫」可靠。"""
    if not text:
        return text
    for t in client_name_terms():
        text = _term_pattern(t).sub('〔客戶名稱・社群不揭露〕', text)
    return text


def audit_client_names(text):
    """產出來的稿子再掃一次。輸入遮蔽是主要防線，這是第二道——
    模型可能從別的欄位拼出名字，或我們的名單漏了某個寫法。"""
    t = text or ''
    return [x for x in client_name_terms() if _term_pattern(x).search(t)]


# 2026-09-03 加：社群貼文是對外發布的出口，跟網站發布、阿財面談一樣要自動
# 擋掉就業服務法第5條的歧視性條件（年齡/性別/婚育/國籍/宗教等）——不是靠
# 顧問審核時人工看出來，是在草稿產生後就自動掃、掃到就擋下不送審，
# 跟 audit_client_names() 同一個防線層級、同一套 block→通知顧問 的處理方式。
LAW5_WORDS = [
    '性別', '男性', '女性', '男生', '女生', '限男', '限女',
    '幾歲', '年齡', '歲以下', '歲以上',
    '已婚', '未婚', '懷孕', '生育',
    '國籍', '外籍', '原住民',
    '身心障礙', '殘障', '宗教', '政黨', '容貌', '長相', '星座', '血型',
]


def audit_law5(text):
    t = text or ''
    return [w for w in LAW5_WORDS if w in t]


# 標記「以下只給顧問看」的寫法。salary_note 這一欄實務上被當成
# 「顧問對人選的完整口徑說明」在用，裡面同時裝著可以講的話跟絕對不能講的
# 數字（例如 BIM 那筆：對外一律面議，但備註裡完整寫著 40,833–50,167 與
# 分級表）。整段餵給模型＝那些數字一定會被寫進貼文。
_INTERNAL_MARKS = ('⚠️', '內部限閱', '限閱', '不得對外', '顧問內部參考',
                   '不得寫入', '不得對候選人', '絕對不對候選人')


def public_part(text):
    """把自由文字欄位裡「只給顧問看」的段落切掉，只留可以對外講的部分。

    規則刻意保守：看到任何一個內部標記就從那裡整段截斷，寧可少講也不要漏。
    這一欄本來就不是設計給機器讀的，用關鍵字判斷一定有誤差，
    而誤差的兩個方向代價差很多——少講只是貼文不夠豐富，多講是把客戶不准
    對外的數字公開貼出去，撤不回來。
    """
    if not text:
        return text
    out = []
    for line in str(text).split('\n'):
        if any(m in line for m in _INTERNAL_MARKS):
            break                      # 從這一行起全部不要
        out.append(line)
    return '\n'.join(out).strip() or None


def shorten_location(loc):
    """社群貼文的地點只寫到路名就好，不要完整門牌／樓層。

    ⚠️ 2026-08-xx 真實案例：DR 那則復健科診所護理師貼文把完整地址
    「台北市內湖區瑞光路337號7樓」整段寫進去公開發布了——診所的精確門牌號
    對外曝光，Jacky 事後要求「內湖區瑞光路」這個顆粒度即可，之後一律照這個
    規則，不要再逐字帶入 jobs.locations 的完整地址。

    只在程式層做，不能只靠 prompt 指示模型「地址寫短一點」——模型不會每次
    都穩定照做，門牌這種可以曝光地點精確度的資訊，要用確定性的規則擋掉。
    """
    if not loc:
        return loc
    out = []
    for part in re.split(r'[、，,／/]', str(loc)):
        s = part.strip()
        if not s:
            continue
        s = re.sub(r'^[^市]{1,3}市', '', s)          # 去掉最前面的「OO市」
        m = re.match(r'^(.*?(?:路|街|大道|道))', s)   # 保留到路名為止，門牌號以下丟棄
        out.append(m.group(1) if m else s)
    return '、'.join(out) if out else loc


# ⚠️ 2026-09-03 加：光縮短 jobs.locations 這個結構化欄位不夠——真實事故顯示
# 模型會從 public_page_text() 整段掃過去的公開頁文字裡，另外撿到完整地址
# （含門牌／樓層）寫進貼文，因為網站頁面本來就是給候選人看完整地址的，
# 那段文字原封不動被當素材餵給模型。這支直接在「文字」層面找出「行政區+
# 路名+門牌號」這個模式整段替換掉，只留「行政區+路名」，跟 mask_client_names()
# 抓客戶名稱同一個做法——不能只在結構化欄位擋，來源文字沒擋住的話，模型永遠
# 有機會從別的地方撿到不該寫的細節。
_TW_CITY = ('台北市|臺北市|新北市|桃園市|台中市|臺中市|台南市|臺南市|高雄市|基隆市|新竹市|嘉義市|'
            '新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣')
_ADDR_NUM_RE = re.compile(
    rf'(?:{_TW_CITY})?'
    r'([^\s，,。、／/：:（）()]{1,6}(?:區|鄉|鎮)[^\s，,。、／/：:（）()]{1,12}(?:路|街|大道|道))'
    # ⚠️ 網站頁面 HTML 轉純文字後，數字跟「號／樓」之間常常留有空白
    # （來源 HTML 是分開的標籤，例如「337」「號」各自一個 <span>），
    # 每個間隔都要容許 \s*，不然像「瑞光路 337 號 7 樓」這種真實格式會漏網。
    r'\s*(?:[0-9一二三四五六七八九十]{1,3}\s*段)?\s*(?:\d+\s*巷)?\s*(?:\d+\s*弄)?'
    r'\s*\d+\s*號\s*(?:\d+\s*樓)?\s*(?:之\s*\d+)?'
)


def strip_address_numbers(text):
    """把文字裡任何「OO市OO區OO路N號N樓」型態的完整地址，整段換成只留
    「OO區OO路」——用在 public_page_text() 的輸出（來源端過濾）跟最終產出
    的貼文文字（輸出端再擋一次，防模型從別的地方撿到或憑常識腦補出完整地址）。"""
    if not text:
        return text
    return _ADDR_NUM_RE.sub(r'\1', text)


def format_job_requirement(job):
    """把 jobs 資料表的欄位轉成技能包要的「用人需求」文字塊。

    ⚠️ 2026-08-14 白名單制，只放「結構化、確定對外安全」的欄位。
    第一次測試（bim-engineer）就抓到問題：notes／talking_points／
    interview_rounds／interview_who／faq_notes 這些自由文字欄位，
    實際內容混了大量內部保密資訊（客戶名稱、廠區地名、顧問之間的
    往來紀錄、要不要揭露公司名稱的指示），這個 BIM 案的 notes 裡就
    明寫「客戶名稱與廠區地名屬於客戶隱私，不對外揭露」——結果原本
    的寫法把整段連同真實客戶線索一起餵給模型，等於繞過保密規則。
    自由文字欄位一律不放進來，只用下面這些結構化欄位；要放
    talking_points 的話，得先由人工把裡面的「講給顧問聽的指示」
    跟「真的可以對外講的話」分開，這支腳本不做這個判斷。
    技能包的禁止事項第 1 條也要求不准編造沒提供的資訊，這裡如果塞一堆
    None 進去，模型看到空值反而可能自己腦補，所以沒值的欄位整行不放。
    """
    lines = []

    def add(label, val):
        if val not in (None, '', 0):
            lines.append(f'{label}：{val}')

    def as_text(val):
        """employment 這類欄位存的是 JSON 陣列字串，直接印出來會是
        ['TEMPORARY', 'FULL_TIME'] 這種給人看很奇怪的格式，轉成頓號分隔。"""
        if not val:
            return val
        if isinstance(val, list):
            return '、'.join(str(x) for x in val)
        # 資料庫裡實際存的是 Python str(list) 格式（單引號、不是合法 JSON），
        # 例如 "['TEMPORARY', 'FULL_TIME']"，json.loads 會直接噴錯，
        # 用 ast.literal_eval 才吃得下這種格式。
        try:
            parsed = ast.literal_eval(val)
            if isinstance(parsed, list):
                return '、'.join(str(x) for x in parsed)
        except Exception:
            pass
        return val

    # ⚠️ 2026-08-14 修：原本照 confidential_client 決定要不要放 client_name，
    # 但那個欄位只反映「網站頁面」能不能具名，跟社群發文是兩件事——
    # Jacky 明確要求社群一律不提客戶名稱，不管網站頁面設定是什麼
    # （client_named=1 的案子網站頁面可以具名，社群還是不行）。
    # 所以這裡整條規則拿掉，job.get('client_name') 永遠不會進到這個函式的輸出。
    add('職稱', job.get('title'))
    add('工作地點', shorten_location(job.get('locations')))
    add('聘僱性質', as_text(job.get('employment')))
    # 2026-08-19 加：這個缺的貼文切角。
    # ⚠️ 這是白名單裡唯一的自由文字欄位，所以定義要很窄：
    #    **只寫「這則貼文要怎麼寫」，不准寫客戶名稱、廠區地名或講給顧問聽的指示**。
    #    加它的原因：雲端維運那則貼文用「遊戲產業集團」當開頭，老闆看了覺得
    #    像灰產——但同樣用產業開頭，「國營保險集團財務部」反而是賣點。
    #    也就是說切角是逐案不同的，沒有通則可以寫死在 prompt 裡。
    add('這則貼文的切角（務必照這個方向寫）', job.get('social_angle'))
    add('資歷要求', f"{job['years_min']} 年以上" if job.get('years_min') else None)
    add('必備技能', job.get('must_skills'))
    if job.get('salary_min') or job.get('salary_max'):
        # 2026-09-10 改：預設字眼原本是「月薪」，AI 會照抄成「月薪X起」，
        # 跟今天訂的規則（只有下限、沒上限時要講「平均薪資X起」，不要講
        # 「月薪X以上/起」，因為那個下限常常是就業服務法揭露門檻，不是
        # 客戶真的開的價）互相矛盾。只有下限沒上限時改用「平均薪資」；
        # 有完整區間時保留原字眼，區間本身已經夠具體，不算誤導。
        unit = job.get('salary_unit') or '月薪'
        lo, hi = job.get('salary_min'), job.get('salary_max')
        if lo and hi:
            add('薪資', f"{unit} {lo}–{hi}")
        else:
            add('薪資', f"平均薪資 {lo or hi} 起")
    add('薪資備註', public_part(job.get('salary_note')))
    add('團隊規模', job.get('team_size'))
    # ⚠️ 一定要先遮蔽再放進 prompt。這一段是整支腳本唯一會把客戶名稱帶進來的
    #    路徑——公開頁是 client_named=1、網站上本來就具名的，社群不行。
    page = strip_address_numbers(mask_client_names(public_page_text(job.get('slug') or '')))
    if page:
        lines.append('\n【公開職缺頁上已經寫出來的內容——這是我們自己對外刊的文字，'
                     '可以直接引用、改寫，工作內容與福利請以這裡為準。'
                     '⚠️ 裡面標成〔客戶名稱・社群不揭露〕的地方是客戶公司名，'
                     '社群貼文一律不准寫出來，也不要試圖從其他線索推回去，'
                     '改用產業或職務性質描述（例：高科技廠房工程專案、進口車品牌總代理）】\n' + page)
    # ⚠️ 最後整段再遮一次客戶名。原本只遮 public_page_text 的輸出，
    # 但結構化欄位裡也會出現客戶名（實例：backend-engineer-game 的薪資備註
    # 寫著「客戶端（遊戲橘子集團）沒有提供薪資範圍」），那條路完全沒守。
    return mask_client_names('\n'.join(lines)) if lines else '（這個職缺目前結構化資料很少，請顧問補充後再產文案，或直接手動撰寫）'


def run_claude(prompt):
    r = subprocess.run(
        ['claude', '-p', sanitize(prompt), '--model', POST_MODEL,
         '--disallowed-tools', 'Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
                                'AskUserQuestion,TodoWrite,BashOutput,KillShell,SlashCommand,Skill,'
                                'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList',
         '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
         '--setting-sources', '', '--output-format', 'text'],
        capture_output=True, text=True, env=env_with_cf(), timeout=CLAUDE_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    return r.stdout.strip()


def tg_with_buttons(text, buttons, thread=None, retries=3):
    """跟 interview_daemon.py 的 tg() 一樣讀設定檔，但這裡要帶 inline
    keyboard（核准／略過按鈕），tg() 本身沒有這個參數，不改共用函式、
    這支自己組。

    ⚠️ 2026-09-01 真實事故：舊版遇到網路瞬斷（例如 Connection reset by
    peer）就直接放棄回傳 None，呼叫端卻不管有沒有拿到 msg_id 都印
    「✅ 草稿已送出審核」——顧問完全看不出這篇到底有沒有真的推播到
    Telegram，Jacky 那天就是等不到通知、按不了審核。這種網路瞬斷通常
    重試就過了，先加 3 次重試（間隔漸長）；真的重試完還是失敗，把完整
    錯誤丟回去讓呼叫端印出明確的失敗訊息，不能再裝作成功。"""
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
    except Exception as ex:
        log(f'Telegram 推播失敗（讀設定檔失敗，不會重試）：{ex}')
        return None
    body = {
        'chat_id': e['TG_CHAT_ID'], 'text': text,
        'reply_markup': json.dumps({'inline_keyboard': [buttons]}),
    }
    tid = thread if thread is not None else e.get('TG_THREAD_ID')
    if tid:
        body['message_thread_id'] = tid
    req_data = json.dumps(body).encode()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                data=req_data, headers={'content-type': 'application/json'})
            r = json.loads(urllib.request.urlopen(req, timeout=20).read())
            return r.get('result', {}).get('message_id')
        except Exception as ex:
            last_err = ex
            if attempt < retries:
                time.sleep(3 * attempt)  # 3s、6s
    log(f'Telegram 推播失敗（重試 {retries} 次都失敗）：{last_err}')
    return None


POST_START = '<<<POST_START>>>'
POST_END = '<<<POST_END>>>'

# ⚠️ 2026-08-14 真實事故：第一版直接把 claude 回的整段文字（文案＋合規檢查＋
# 附加輸出）當成 social_post_draft 去發布——附加輸出裡的「發文前硬阻礙」
# 「必須向業主確認」這些是給顧問看的內部判斷，結果整段被公開貼到 Threads
# 上，Jacky 事後刪文。技能包本身的文字不能動（Jacky 要求逐字收錄），所以
# 這個「文案跟內部分析要分開」的指令用額外包一層 system 提示做，不改
# SKILL.md 一個字——請模型把真正要發布的文案用固定標記包起來，之後只取
# 標記中間那段當作 social_post_draft，標記本身跟後面的合規檢查／附加輸出
# 都不會進到發文內容，但完整原文還是會貼進 Telegram 讓顧問看得到分析。
WRAP_INSTRUCTION = (
    f'\n\n---\n\n【格式要求，不算在技能包規則內，只是方便程式解析】\n'
    f'🚨 **全篇一律使用繁體中文（台灣用語），不准出現任何簡體字**——'
    f'不管上面技能包的公式內容本身是用什麼字打的，輸出一律轉成繁體中文。\n'
    f'完成上面技能包要求的所有內容後，請額外做一件事：把「可以直接發布的文案」'
    f'（也就是「輸出格式」那一段，從「職缺情報｜」開始到收尾段落結束，不含合規檢查'
    f'跟附加輸出）用這兩行標記整段包起來：\n'
    f'{POST_START}\n'
    f'（文案內容）\n'
    f'{POST_END}\n'
    f'標記本身跟文案之間不要有多餘的說明文字。文案內文只用純文字，'
    f'不要用 Markdown（不加 #、*、**、`、---）——這是純文字社群貼文，'
    f'\n\n🚨 **文案裡不准出現任何佔位符**。\n'
    f'不准寫「待補」「待確認」「［…］」「TBD」「XXX」這類字樣——\n'
    f'2026-08-19 真實事故：一則 VIP 接待的貼文以「🔥【徵】［案件亮點待補］」開頭\n'
    f'公開發布，整篇工作內容、福利、公司特色全是「［待補］」，等於告訴候選人\n'
    f'我們對這個職缺一無所知。回頭掃描發現三則已發布的貼文都帶著「待補」字樣。\n'
    f'**資料不足的段落請整段不要寫**，寧可短，不要有洞。\n'
    f'如果連職稱與地點以外幾乎什麼都沒有，或是某個公式規則要求的素材（例如真實對話、\n'
    f'真實候選人提問）你手上完全沒有，就在標記外面說明「資料不足以產出貼文」，\n'
    f'不要硬生一篇出來——那種稿發出去比不發更傷。\n'
    f'🚨 這句「資料不足以產出貼文」的說明一定要寫在 {POST_START}／{POST_END}\n'
    f'標記外面，絕對不要包進標記裡面——標記裡面的東西會被當成正式貼文推去審核，\n'
    f'包進去等於讓這句說明被誤判成貼文本體。這種情況下，你的整段回覆裡根本不應該\n'
    f'出現 {POST_START}／{POST_END} 這兩個標記。\n'
    f'Markdown 符號不會被平台轉成粗體或標題，只會照字面被貼出去、變成亂碼。\n\n'
    f'🚨 **文末如果要邀請候選人互動，一律說「找 AI阿財 聊聊」，不要說「找 Step1ne 聊聊」**。\n'
    f'2026-09-09 真實事故：多篇貼文結尾寫「有興趣歡迎找Step1ne聊聊」已經公開發布——\n'
    f'Step1ne 是公司品牌名，不是互動窗口，候選人看了不知道要去哪裡「找」，\n'
    f'AI阿財才是候選人實際會互動的對象（面談小幫手）。沒有要邀請互動的貼文不受影響。'
)


def strip_markdown(text):
    """防呆用：就算模型沒完全照上面的要求，還是把常見 Markdown 符號濾掉，
    不要讓 # ** ` 這些符號原樣出現在真的貼出去的內容裡。"""
    import re
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'\1', text)
    text = text.replace('`', '')
    text = re.sub(r'^-{3,}\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


def extract_post(raw):
    """從 claude 的完整回覆裡，只挖出 POST_START／POST_END 中間那段當作
    真的要發布的文案。

    ⚠️ 2026-09-11 改：舊版「抓不到標記就整段當文案」這個保底行為，
    真實事故直接證明是錯的——「真實轉述」公式的規則要求模型在缺真實
    素材時「不准自己編，改成回頭問人」，模型照規則拒寫、只回一段解釋，
    這段解釋沒有被包進 POST_START/POST_END（因為根本沒有文案可以包），
    舊的保底邏輯卻把這整段解釋文字當成文案，直接推到 TG 標成
    「以下會被公開發布」——顧問差點把 AI 的提問當成真的貼文稿。
    WRAP_INSTRUCTION 明講「只包可以直接發布的文案」，所以沒標記＝
    沒有文案可用，是可靠訊號，不該再猜、更不該整段代打。
    """
    i = raw.find(POST_START)
    j = raw.find(POST_END)
    if i >= 0 and j > i:
        post = strip_markdown(raw[i + len(POST_START):j])
        # ⚠️ 2026-09-11 加：真實事故第二例——模型這次有乖乖放標記，但把
        # WRAP_INSTRUCTION 自己定義的拒寫句「資料不足以產出貼文」包*進*
        # 標記裡面，等於把拒寫說明當成貼文本體。這句是系統自己教的固定
        # 措辭（見下面「資料不足的段落」那條規則），出現在標記內一律視為
        # 拒寫，不是貼文，不管前後还写了什么。
        if '資料不足以產出貼文' in post:
            log('⚠️ 標記內出現「資料不足以產出貼文」——模型把拒寫說明包進了標記裡，視為沒有產出文案。')
            return None
        return post
    log('⚠️ 沒抓到 POST_START/POST_END 標記——模型沒有產出可發布的文案（常見原因：'
        '公式要求的素材不夠，模型改成回頭問人），不再拿整段回覆頂替，視為失敗。')
    return None


def line_community_link_suffix(account_id):
    """2026-09-11 加：Jacky 明確要求「LINE 社群」這個管道，不限文案類型
    （職缺文／通用文／AI阿財話題）、不限公式（純CTA型／對話討論型／原始格式），
    結尾一律要附 LINE 連結——只限這個平台，其他平台不受影響。
    寫成一律事後補在 post 尾巴，不靠 AI 照 prompt 指示自己加——三種文案
    生成路徑（generate_draft／generate_draft_job_styled／generate_draft_topic）
    用的 prompt 完全不同，靠 AI 自己記得會有漏放的風險，補在存檔前這一個
    點才能保證『不限類型不限公式』都一定有，不用三邊分別改 prompt。
    連結存在帳號自己的 line_link 欄位，換連結改資料庫就好，不用重新部署。"""
    if not account_id:
        return ''
    acc = d1(f"SELECT platform, line_link FROM social_accounts WHERE id={q(account_id)}")
    if acc and acc[0].get('platform') == 'line_community' and acc[0].get('line_link'):
        # 2026-09-11 再改：Jacky 看到光禿禿一個網址就退回——LINE 社群裡的人
        # 不知道點進去要幹嘛，要有一句「有興趣就點這裡聯繫顧問」帶著點進去。
        return '\n\n👉 有興趣歡迎點進「全民獵才」LINE官方帳號聯繫顧問：\n' + acc[0]['line_link']
    return ''


def job_raw_format_line_suffix(account_id, style_row):
    """2026-09-14 加：職缺文「原始格式」（沒選純CTA型／對話討論型公式，
    style_row 是 None）要一律附上顧問自己的 LINE OA 連結。查完現況發現
    這件事之前完全交給各顧問自己的 skill_prompt 記得寫——結果 DR 的
    prompt 甚至寫死「不放連結」，Phoebe 的連結是舊的（跟她卡片上現在
    的 line_link 對不上），Bob／Anna／Dan H／Eileen S／法蘭克／宥恩的
    CTA 段落全部只有「私訊我」這種話術、沒有真的網址。跟
    line_community_link_suffix() 同一個理由：補在存檔前這一點，
    不用回頭改七八份 prompt，也不怕以後又有人漏寫。
    只管「原始格式」——純CTA型／對話討論型公式本身的連結／CTA 規則
    照舊，不在這裡動。"""
    if style_row or not account_id:
        return ''
    acc = d1(f"SELECT platform, line_link FROM social_accounts WHERE id={q(account_id)}")
    if acc and acc[0].get('platform') == 'threads' and acc[0].get('line_link'):
        return '\n\n👉 有興趣歡迎點進「全民獵才」LINE官方帳號聯繫顧問：\n' + acc[0]['line_link']
    return ''


def generate_draft(job, account_id):
    prompt = skill(account_id) + '\n\n' + format_job_requirement(job) + WRAP_INSTRUCTION
    raw = run_claude(prompt)
    post = extract_post(raw) if raw else None
    # 輸出端再擋一次完整地址——來源端（format_job_requirement）已經擋過，
    # 這裡是保底：萬一模型從其他管道（自己的常識、標題裡的地名）拼出完整
    # 門牌，還是要在真正存進 draft 之前擋下來。
    return raw, strip_address_numbers(post) if post else None


# ⚠️ 2026-09-03 加：職缺文的「純CTA型／對話討論型」不是 SKILL.md 那套
# 「職缺情報｜」固定欄位格式——是 BIM_THREADS_PLAYBOOK.md 定案的公式化寫法
# （數字反直覺／身份切割／真實轉述…），結構完全不同，不能套 skill(account_id)
# 那條路。這裡另外組一版 prompt：用「一鍵發文」選的那個 style_prompts 當
# 主要寫作指示，職缺事實資料附在後面當「素材」而不是硬性欄位清單。
#
# CTA_KEYWORD 目前還沒有職缺專屬的關鍵字欄位（下一步才接），先讓模型自己
# 挑一個好記、跟這個職缺有關的詞（通常是地點或職稱關鍵字），不要空著或
# 留下未替換的 {{CTA_KEYWORD}} 字樣。
def generate_draft_job_styled(job, style_row):
    facts = format_job_requirement(job)
    style_body = style_row['body'].replace(
        '{{CTA_KEYWORD}}',
        '（你自己想一個好記、跟這個職缺有關的關鍵字，通常是地點或職稱裡的一個詞，例如地名或職稱簡稱）'
    )
    prompt = (
        style_body
        + '\n\n【這個職缺的原始資料——自由運用，挑對這篇公式有幫助的部分即可，'
          '不用照抄成清單格式，也不用全部用到】\n' + facts
        + WRAP_INSTRUCTION
    )
    raw = run_claude(prompt)
    post = extract_post(raw) if raw else None
    return raw, strip_address_numbers(post) if post else None


# ⚠️ 2026-09-03 加：話題／時事討論類貼文，跟職缺招募文是兩種東西——SKILL.md
# 整份是「職缺情報｜」這個固定格式的招募文模板，套在話題文上完全文不對題
# （模型會被迫硬套「工作內容」「招募資訊」那些欄位）。話題類型改用這份更
# 開放的角色框架，實際切入方向來自 topic_prompts.body（Jacky／Phoebe 自己
# 寫的話題簡報），語氣則來自顧問在「顧問與話題設定」指派的 style_prompts。
TOPIC_BASE_PROMPT = (
    '你是資深獵頭顧問，同時具備社群行銷總監的內容判斷力，正在幫「全民獵才」'
    '的社群帳號寫一則 Threads 貼文。\n\n'
    '這次不是職缺招募文，是一般性的話題／時事討論貼文，目的是引發追蹤者討論、'
    '建立帳號的專業形象，不是直接導向應徵。\n\n'
    '硬性規則：\n'
    '1. 全篇繁體中文（台灣用語）。\n'
    '2. 不准提及任何客戶公司名稱——除非下面的話題切入方向裡明確提供了一個'
    '「已公開報導、可以引用的案例」（例如新聞報導過的公司名），否則一律不要'
    '自己編造或帶入任何公司名稱。\n'
    '3. 不用「超棒」「絕佳機會」這類推銷語氣，維持專業但不生硬的口吻。\n'
    '4. 篇幅比照 Threads 一般貼文長度（不是長文章），抓重點講，不要寫成完整的部落格文章。\n'
)


def _topic_by_id(topic_id):
    rows = d1(f"SELECT * FROM topic_prompts WHERE id={q(topic_id)}")
    return rows[0] if rows else None


def _style_by_id(style_id):
    if not style_id:
        return None
    rows = d1(f"SELECT * FROM style_prompts WHERE id={q(style_id)}")
    return rows[0] if rows else None


def _consultant_style_for_topic(account_id):
    """依帳號查出人名（跟前端「顧問社群」頁面 nm()／personName() 同一套
    「取 dash 前面」邏輯），再查這位顧問在「顧問與話題設定」裡幫話題類型
    指定的風格。查不到就回 None，退回 TOPIC_BASE_PROMPT 本身的語氣，
    不會因為沒設定就整支失敗。"""
    if not account_id:
        return None
    acc = d1(f"SELECT label FROM social_accounts WHERE id={q(account_id)}")
    if not acc:
        return None
    name = re.split(r'[-–—]', acc[0]['label'])[0].strip()
    rows = d1(f"SELECT topic_style_id FROM consultant_content_settings WHERE consultant={q(name)}")
    if not rows or not rows[0].get('topic_style_id'):
        return None
    return _style_by_id(rows[0]['topic_style_id'])


def _recent_posts_for_topic(topic_id, limit=5):
    """這個話題 id 之前實際產出過的貼文內容——不分有沒有真的發布，
    只要是這個話題產過的草稿都算，因為就算沒發布，角度也已經被用掉了。
    2026-09-04 加：解決「同一顧問同一話題重複用，每次都長一樣」的問題。"""
    if not topic_id:
        return []
    rows = d1(
        f"SELECT draft FROM social_post_queue "
        f"WHERE topic_id={q(topic_id)} AND draft IS NOT NULL AND draft != '' "
        f"ORDER BY requested_at DESC LIMIT {int(limit)}"
    )
    return [r['draft'] for r in rows if r.get('draft')]


def format_topic_requirement(topic, style_row):
    """話題內容是 Jacky／Phoebe 自己寫的策略簡報，不是使用者輸入或客戶欄位，
    不用像 format_job_requirement() 那樣白名單過濾——但風格提示詞跟話題內容
    一樣要接在 TOPIC_BASE_PROMPT 後面，當成這次產稿的明確指示。"""
    parts = [f"【這次話題的切入方向】\n{topic['body']}"]
    if style_row:
        parts.append(f"【這則貼文要用的語氣風格：{style_row['name']}】\n{style_row['body']}")

    # ⚠️ 2026-09-04 加：同一個話題提示詞被重複選用時，原本每次都從同一份
    # body 重新產，角度骨架幾乎一樣，只是措辭不同——對「要靠 Threads 反覆
    # 曝光同一個主題（例如AI阿財）建立信任」這種用法完全不夠。查這個話題
    # 之前實際產過的內容，附進去叫它換角度，不要重複同一個切入點或例子。
    past = _recent_posts_for_topic(topic.get('id'))
    if past:
        listed = '\n\n'.join(f'（第{i+1}篇）{p}' for i, p in enumerate(past))
        parts.append(
            '【這個話題之前已經產過的貼文——這次要換一個新角度，不要重複下面的切入點、'
            '比喻或舉例，就算主題一樣也要找沒講過的面向】\n' + listed
        )
    return '\n\n'.join(parts)


def generate_draft_topic(topic, style_row):
    prompt = TOPIC_BASE_PROMPT + '\n\n' + format_topic_requirement(topic, style_row) + WRAP_INSTRUCTION
    raw = run_claude(prompt)
    post = extract_post(raw) if raw else None
    return raw, strip_address_numbers(post) if post else None


def process_topic(queue_row, topic):
    """跟 process_job() 對齊的話題類型版本——沒有職缺欄位可以填，草稿產出、
    客戶名稱稽核、Telegram 審核通知、tg_message_id 回寫整套流程一致，只差
    在素材來源跟基底 prompt。刻意不跟 process_job() 共用同一份稽核／重產
    邏輯——兩邊各自獨立一份，改一邊不會不小心動到另一邊已經穩定在跑的流程。"""
    qid = queue_row['id']
    title = topic['name']
    account_id = queue_row.get('account_id')
    style_row = _consultant_style_for_topic(account_id)
    try:
        log(f'{title}（話題）：產生貼文草稿中…')
        raw, post = generate_draft_topic(topic, style_row)
        if not raw or not post:
            # 2026-09-11 加：同 process_job() 的修法——不再靜默，也不再讓
            # AI 的「缺素材」說明被誤標成「以下會被公開發布」的草稿。
            log(f'❌ {title}（話題）：AI 沒有產出可發布的草稿（常見原因：這個公式要求的素材不足）')
            d1(f"UPDATE social_post_queue SET status='needs_material' WHERE id={qid}")
            tg_with_buttons(
                f'⚠️ <b>{title}</b>（{style_row["name"] if style_row else "原始格式"}）沒有產出可發布的草稿\n\n'
                f'—— AI 的說明 ——\n{(raw or "（沒有任何回覆）")[:1200]}\n\n'
                f'這一則不會被公開發布。要嘛照上面說明補素材後按「🔄 再產一次」，要嘛換一個不需要真實對話素材的公式。',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        hits = audit_client_names(post)
        if hits:
            log(f'⚠️ {title}：草稿出現客戶名稱 {hits}，重產一次')
            raw2, post2 = generate_draft_topic(topic, style_row)
            hits2 = audit_client_names(post2 or '')
            if post2 and not hits2:
                raw, post, hits = raw2, post2, []
            else:
                hits = hits2 or hits
                if post2:
                    raw, post = raw2, post2
        if hits:
            log(f'🚫 {title}：重產後仍有客戶名稱 {hits}，不自動送審')
            d1(f"UPDATE social_post_queue SET draft={q(post)}, status='blocked' WHERE id={qid}")
            tg_with_buttons(
                f'🚫 <b>{title}</b>（話題）的社群草稿出現客戶公司名稱，已擋下來沒有送審。\n'
                f'命中：{"、".join(hits)}\n\n'
                f'社群一律不提客戶名稱。下面這份要用的話請自己改掉再發：\n\n{post}',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        law5 = audit_law5(post)
        if law5:
            log(f'⚠️ {title}（話題）：草稿出現禁刊字眼 {law5}，重產一次')
            raw2, post2 = generate_draft_topic(topic, style_row)
            law5_2 = audit_law5(post2 or '')
            if post2 and not law5_2:
                raw, post, law5 = raw2, post2, []
            else:
                law5 = law5_2 or law5
                if post2:
                    raw, post = raw2, post2
        if law5:
            log(f'🚫 {title}（話題）：重產後仍有禁刊字眼 {law5}，不自動送審')
            d1(f"UPDATE social_post_queue SET draft={q(post)}, status='blocked' WHERE id={qid}")
            tg_with_buttons(
                f'🚫 <b>{title}</b>（話題）的社群草稿出現就業服務法第5條禁刊字眼，已擋下來沒有送審。\n'
                f'命中：{"、".join(law5)}\n\n'
                f'下面這份要用的話請自己改掉再發：\n\n{post}',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        # 2026-09-04 加：Threads觀察系統要比較「話題成效」，得先知道每篇話題文
        # 屬於哪種角度（category：ai＝AI阿財信任建立／general＝一般互動），
        # 沒有這個標記，儀表板的分類比較就永遠是空的。
        post = post + line_community_link_suffix(account_id)
        mission_tag = {'ai': 'trust_building', 'general': 'engagement'}.get(topic.get('category'), 'general')
        length_tag = 'short' if len(post) < 300 else ('long' if len(post) > 600 else 'medium')
        d1(f"UPDATE social_post_queue SET draft={q(post)}, status='drafted', "
           f"content_mission={q(mission_tag)}, content_length={q(length_tag)} WHERE id={qid}")

        end_idx = raw.find(POST_END)
        analysis = raw[end_idx + len(POST_END):].strip() if end_idx >= 0 else ''
        thread = TG_THREAD_SOCIAL
        acct_label = ''
        if account_id:
            acc = d1(f"SELECT label, tg_thread_id FROM social_accounts WHERE id={q(account_id)}")
            if acc:
                acct_label = acc[0].get('label') or ''
                if acc[0].get('tg_thread_id'):
                    thread = int(acc[0]['tg_thread_id'])
                else:
                    log(f'⚠️ 帳號「{acct_label}」沒有設定 tg_thread_id，'
                        f'這則通知會送到共用主題 {TG_THREAD_SOCIAL}')
        msg_id = tg_with_buttons(
            f"📱 全民獵才貼文草稿（💬 話題）\n"
            f"帳號：{acct_label or '（未指定帳號）'}\n"
            f"話題：{title}\n"
            + (f"風格：{style_row['name']}\n" if style_row else '')
            + f"\n── 以下會被公開發布 ──\n{post}\n\n"
            f"── 以下只有你看得到，不會發布 ──\n{analysis or '（無額外分析）'}",
            [
                {'text': '✅ 確認發布', 'callback_data': f'soc_approve:{qid}'},
                {'text': '🔄 重新產一次', 'callback_data': f'soc_regen:{qid}'},
                {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'},
            ],
            thread,
        )
        if msg_id:
            d1(f"UPDATE social_post_queue SET tg_message_id={q(str(msg_id))} WHERE id={qid}")
            log(f'✅ {title}：草稿已送出審核')
        else:
            log(f'❌ {title}：草稿已產生但 Telegram 通知沒送出，'
                f'請去 consultant/social-post/ 頁面手動審核（queue id={qid}）')
    except Exception as e:
        log(f'❌ {title}（話題）產生草稿失敗：{e}')


def process_job(queue_row, job, repost=False):
    """2026-08-18 改：一個職缺可以對應多筆排隊紀錄（social_post_queue），
    不再是 jobs 表上唯一一組欄位——顧問要讓四個人各自對同一個職缺發一篇，
    原本的做法（狀態存在 jobs 本身）後排的帳號會直接覆蓋前一個人排的，
    真實案例已經撞到。現在每一筆排隊紀錄各自獨立，用 queue id 當按鈕的
    識別碼，互不影響。"""
    qid = queue_row['id']
    slug, title = job['slug'], job['title']
    account_id = queue_row.get('account_id')
    # 2026-09-03 加：一鍵發文選了「純CTA型／對話討論型」哪個公式，會存
    # style_id——有的話用那套公式化寫法（generate_draft_job_styled），沒有
    # （原始格式，或排程/舊資料沒選）就退回原本的 skill(account_id)/SKILL.md。
    style_id = queue_row.get('style_id')
    style_row = _style_by_id(style_id) if style_id else None

    def gen():
        return generate_draft_job_styled(job, style_row) if style_row else generate_draft(job, account_id)

    try:
        log(f'{title}（{slug}）：產生貼文草稿中…' + (f'（套用「{style_row["name"]}」）' if style_row else ''))
        raw, post = gen()
        if not raw or not post:
            # 2026-09-11 加：以前這裡完全靜默——顧問看不到任何結果，這則
            # 排隊紀錄也會卡在 status=NULL，每 2 分鐘被重新掃到、重跑一次，
            # 白白浪費 API 額度。現在把 AI 的原始回覆（通常是「缺素材，
            # 需要提供真實對話」這類說明）明確標成「沒有產出草稿」通知顧問，
            # 不能標成「以下會被公開發布」——那句話曾經差點把 AI 的提問
            # 當成真的貼文稿推去發布。
            log(f'❌ {title}：AI 沒有產出可發布的草稿（常見原因：這個公式要求的素材不足）')
            d1(f"UPDATE social_post_queue SET status='needs_material' WHERE id={qid}")
            tg_with_buttons(
                f'⚠️ <b>{title}</b>（{style_row["name"] if style_row else "原始格式"}）沒有產出可發布的草稿\n\n'
                f'—— AI 的說明 ——\n{(raw or "（沒有任何回覆）")[:1200]}\n\n'
                f'這一則不會被公開發布。要嘛照上面說明補素材後按「🔄 再產一次」，要嘛換一個不需要真實對話素材的公式。',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        # ── 客戶名稱稽核（第二道防線）──
        # 輸入端已經遮蔽過，這裡再掃一次產出。漏一次的代價是客戶名字被公開
        # 貼到社群，撤不回來——寧可重產一次也不要送出去。
        # 重產一次還是漏，就不自動送審，改成明著警告顧問，讓人來決定。
        hits = audit_client_names(post)
        if hits:
            log(f'⚠️ {title}：草稿出現客戶名稱 {hits}，重產一次')
            raw2, post2 = gen()
            hits2 = audit_client_names(post2 or '')
            if post2 and not hits2:
                raw, post, hits = raw2, post2, []
            else:
                hits = hits2 or hits
                if post2:
                    raw, post = raw2, post2
        if hits:
            log(f'🚫 {title}：重產後仍有客戶名稱 {hits}，不自動送審')
            d1(f"UPDATE social_post_queue SET draft={q(post)}, status='blocked' WHERE id={qid}")
            tg_with_buttons(
                f'🚫 <b>{title}</b> 的社群草稿出現客戶公司名稱，已擋下來沒有送審。\n'
                f'命中：{"、".join(hits)}\n\n'
                f'社群一律不提客戶名稱（網站頁面可以具名是另一回事）。'
                f'下面這份要用的話請自己改掉再發：\n\n{post}',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        # ── 就業服務法第5條稽核（第三道防線）──
        # 邏輯跟上面客戶名稱那段一致：重產一次，還有就擋下不送審，交給顧問處理。
        law5 = audit_law5(post)
        if law5:
            log(f'⚠️ {title}：草稿出現禁刊字眼 {law5}，重產一次')
            raw2, post2 = gen()
            law5_2 = audit_law5(post2 or '')
            if post2 and not law5_2:
                raw, post, law5 = raw2, post2, []
            else:
                law5 = law5_2 or law5
                if post2:
                    raw, post = raw2, post2
        if law5:
            log(f'🚫 {title}：重產後仍有禁刊字眼 {law5}，不自動送審')
            d1(f"UPDATE social_post_queue SET draft={q(post)}, status='blocked' WHERE id={qid}")
            tg_with_buttons(
                f'🚫 <b>{title}</b> 的社群草稿出現就業服務法第5條禁刊字眼，已擋下來沒有送審。\n'
                f'命中：{"、".join(law5)}\n\n'
                f'下面這份要用的話請自己改掉再發：\n\n{post}',
                [{'text': '🔄 再產一次', 'callback_data': f'soc_regen:{qid}'},
                 {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'}],
                TG_THREAD_SOCIAL,
            )
            return

        # draft 只存乾淨的文案（會被拿去真的發布）；完整原文（含合規檢查／
        # 附加輸出）只送進 Telegram 給顧問看，不落地存表，顧問要留紀錄的話
        # 自己在 Telegram 裡搜。
        # 2026-09-04 加：Threads觀察系統要比較「哪個公式表現好」，得先知道每篇
        # 職缺文是用哪套公式寫的（style_row 的 subtype，沒選公式就是預設寫法）。
        post = post + line_community_link_suffix(account_id) + job_raw_format_line_suffix(account_id, style_row)
        formula_tag = (style_row.get('subtype') or style_row.get('name')) if style_row else 'default'
        length_tag = 'short' if len(post) < 300 else ('long' if len(post) > 600 else 'medium')
        d1(f"UPDATE social_post_queue SET draft={q(post)}, status='drafted', "
           f"content_formula={q(formula_tag)}, content_length={q(length_tag)} WHERE id={qid}")

        end_idx = raw.find(POST_END)
        analysis = raw[end_idx + len(POST_END):].strip() if end_idx >= 0 else ''
        # 2026-08-17 加：多顧問各自審核——這筆排隊紀錄如果指定過帳號，就送到
        # 那個帳號綁定的專屬主題（EYLISE／PHOEBE／DR 各自一個），沒指定的
        # （排程自動掃到、沒人特別指名的舊職缺）維持送到共用的 TG_THREAD_SOCIAL。
        thread = TG_THREAD_SOCIAL
        acct_label = ''
        if account_id:
            acc = d1(f"SELECT label, tg_thread_id FROM social_accounts WHERE id={q(account_id)}")
            if acc:
                acct_label = acc[0].get('label') or ''
                if acc[0].get('tg_thread_id'):
                    thread = int(acc[0]['tg_thread_id'])
                else:
                    # 沒設 topic 就會掉進共用主題，而共用主題是 Jacky 的房間——
                    # 不講的話沒有人會發現通知跑錯地方。
                    log(f'⚠️ 帳號「{acct_label}」沒有設定 tg_thread_id，'
                        f'這則通知會送到共用主題 {TG_THREAD_SOCIAL}')
        msg_id = tg_with_buttons(
            f"📱 全民獵才貼文草稿\n"
            f"帳號：{acct_label or '（未指定帳號）'}\n"
            f"職缺：{title}{'　（重新產出）' if repost else ''}\n"
            + (f"公式：{style_row['name']}\n" if style_row else '')
            + f"\n── 以下會被公開發布 ──\n{post}\n\n"
            f"── 以下只有你看得到，不會發布 ──\n{analysis or '（無額外分析）'}",
            [
                {'text': '✅ 確認發布', 'callback_data': f'soc_approve:{qid}'},
                {'text': '🔄 重新產一次', 'callback_data': f'soc_regen:{qid}'},
                {'text': '❌ 不發這篇', 'callback_data': f'soc_skip:{qid}'},
            ],
            thread,
        )
        if msg_id:
            d1(f"UPDATE social_post_queue SET tg_message_id={q(str(msg_id))} WHERE id={qid}")
            log(f'✅ {title}：草稿已送出審核')
        else:
            # 2026-09-01 改：不能再跟成功印一樣的訊息——草稿本身已經存進 D1
            # （status='drafted'），只是沒有 Telegram 通知，顧問要自己去
            # consultant/social-post/ 頁面手動找到這篇審核，不然會一直卡著
            # 沒人知道。
            log(f'❌ {title}：草稿已產生但 Telegram 通知沒送出，'
                f'請去 consultant/social-post/ 頁面手動審核（queue id={qid}）')
    except Exception as e:
        log(f'❌ {title}（{slug}）產生草稿失敗：{e}')


def _job_by_slug(slug):
    rows = d1(f"SELECT * FROM jobs WHERE slug={q(slug)}")
    return rows[0] if rows else None


def main():
    if '--repost' in sys.argv:
        idx = sys.argv.index('--repost')
        slug = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else None
        if not slug:
            print('用法：social_post_agent.py --repost <slug>'); sys.exit(1)
        job = _job_by_slug(slug)
        if not job:
            print(f'找不到職缺 {slug}'); sys.exit(1)
        # --repost 重新產一次最新那筆排隊紀錄的草稿（沒有排隊紀錄的話先幫它排一筆）。
        rows = d1(f"SELECT * FROM social_post_queue WHERE job_slug={q(slug)} ORDER BY requested_at DESC LIMIT 1")
        if not rows:
            d1(f"INSERT INTO social_post_queue (job_slug, account_id, requested_at) "
               f"VALUES ({q(slug)}, NULL, datetime('now','+8 hours'))")
            rows = d1(f"SELECT * FROM social_post_queue WHERE job_slug={q(slug)} ORDER BY id DESC LIMIT 1")
        process_job(rows[0], job, repost=True)
        return

    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args:
        slug = args[0]
        job = _job_by_slug(slug)
        if not job:
            print(f'找不到職缺 {slug}'); sys.exit(1)
        # 手動測試模式：直接排一筆沒指定帳號的紀錄。
        d1(f"INSERT INTO social_post_queue (job_slug, account_id, requested_at) "
           f"VALUES ({q(slug)}, NULL, datetime('now','+8 hours'))")
        rows = d1(f"SELECT * FROM social_post_queue WHERE job_slug={q(slug)} ORDER BY id DESC LIMIT 1")
        process_job(rows[0], job)
        return

    # 2026-08-18 改：一個職缺可以對應多筆排隊紀錄——先撿「一鍵發文」頁面
    # 排進來、還沒產過草稿的紀錄；同時維持原本「自動掃描」的行為：開放中
    # 但從來沒被排過（不論哪個帳號）的舊職缺，自動補一筆沒指定帳號的紀錄
    # （退回 Jacky／預設帳號），不用顧問手動一個一個排。
    # ── 自動補排隊：2026-09-08 停用 ──
    # 原本這裡會把「開放中但從沒排過的職缺」自動補一筆**沒指定帳號**的排隊紀錄，
    # 產出草稿推到 TG 等人認領。Jacky 2026-09-08 明確指示：沒有透過後台手動
    # 設定的自動產文都要停，顧問手動按發文的才留。
    #
    # ⚠️ 而且它跟「草稿放超過 3 天自動刪除」會互咬成無限迴圈：
    #    這裡判斷「從沒排過」用的是 social_post_queue 有沒有那一列，
    #    清理把列刪掉之後，它就又當成新職缺重新產一次。
    #    2026-09-08 當天真的發生：早上刪掉 25 筆沒人認領的草稿，
    #    13:21 這支就一口氣把 9 個職缺重新產了一輪，全部一樣沒人認領。
    #
    # 要重開的話，**必須先改成不會跟清理互咬的判斷**（例如清理改成標記
    # status='expired' 而不是真的刪列），否則同樣的迴圈會再來一次。
    never_queued = []
    for r in never_queued:
        d1(f"INSERT INTO social_post_queue (job_slug, account_id, requested_at) "
           f"VALUES ({q(r['slug'])}, NULL, datetime('now','+8 hours'))")

    tick()


MAX_CONCURRENT = 3


def tick():
    # ⚠️ 2026-09-10 改：原本一筆一筆循序處理，一個顧問的職缺卡在客戶名稱
    # 稽核重產（要2-3分鐘），排在後面的其他顧問就得乾等。Jacky 當場抱怨
    # 「不然每次都這樣」——改成最多同時處理 MAX_CONCURRENT 筆，各顧問
    # 的請求互不卡隊。3 這個數字是刻意壓低的：機器是 8GB、今天才因為
    # 多個 Claude session 同時跑撞過資源緊張的問題，不要為了發文順暢
    # 又把同一台機器榨乾。
    queue_rows = d1("SELECT * FROM social_post_queue WHERE status IS NULL ORDER BY requested_at ASC")
    if not queue_rows:
        log('沒有需要產貼文的新職缺／話題')
        return
    jobs_cache = {}
    topics_cache = {}
    tasks = []
    for qrow in queue_rows:
        # 2026-09-03 加：話題類型的排隊紀錄靠 topic_id 分辨。job_slug 這時候
        # 存的是「💬 描述文字」，只給列表顯示跟 /go/ 點擊歸因用，不是真職缺
        # slug，不能拿去查 jobs 表（查了一定落空，之前就是這樣被完全略過）。
        if qrow.get('topic_id'):
            tid = qrow['topic_id']
            if tid not in topics_cache:
                topics_cache[tid] = _topic_by_id(tid)
            topic = topics_cache[tid]
            if not topic:
                log(f'⚠️ 排隊紀錄 {qrow["id"]} 指向不存在的話題 id={tid}，跳過')
                continue
            tasks.append((process_topic, qrow, topic))
            continue
        slug = qrow['job_slug']
        if slug not in jobs_cache:
            jobs_cache[slug] = _job_by_slug(slug)
        job = jobs_cache[slug]
        if not job:
            log(f'⚠️ 排隊紀錄 {qrow["id"]} 指向不存在的職缺 {slug}，跳過')
            continue
        tasks.append((process_job, qrow, job))

    if not tasks:
        return

    def _run(t):
        fn, qrow, arg = t
        try:
            fn(qrow, arg)
        except Exception as e:
            log(f'⚠️ 排隊紀錄 {qrow["id"]} 處理時出錯：{str(e)[:200]}')

    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT, len(tasks))) as ex:
        list(ex.map(_run, tasks))


# ⚠️ 2026-09-10 改：原本靠 launchd StartInterval（每10分鐘）觸發一次性腳本，
# 今天連續撞到 interview／client_report_tick／這支自己，三個 StartInterval
# 排程都無聲無息停止跳動（launchd 的 runs 計數卡住，不知道為什麼），
# 顧問「一鍵發文」超過 10 小時完全沒反應，靜靜卡死沒有任何錯誤訊息。
# Jacky 明確要求發文優先做穩——改成跟 ai_worker.py／interview_daemon.py
# 同一套常駐迴圈，不再依賴 launchd 的計時器；plist 改 RunAtLoad+KeepAlive，
# 程式自己活著就會一直跑，就算真的當掉 launchd 的 KeepAlive 也會直接重開，
# 不用再靠人工發現「怎麼10小時沒動靜」才知道壞了。
if __name__ == '__main__':
    if '--once' in sys.argv:
        main()
    elif '--repost' in sys.argv or (len(sys.argv) > 1 and not sys.argv[1].startswith('--')):
        main()
    else:
        # 2026-09-10 改：原本600秒(10分鐘)，Jacky反應顧問剛按完要乾等太久。
        # 改成90秒——阿財面談那支daemon是每8秒查一次D1都撐得住，90秒負擔
        # 小很多；比原本快6倍多，顧問按下去最多等1-2分鐘就有動靜。
        # 如果之後發現D1負載真的被推高（查D1 timeout變頻繁），要退回更慢
        # 的間隔，或改做「Worker直接喚醒本機」這種事件觸發式設計。
        log('社群發文 agent 啟動（常駐，每 90 秒掃一次）')
        while True:
            try:
                tick()
            except Exception as e:
                log(f'⚠️ 這一輪出錯（不影響下一輪）：{str(e)[:200]}')
            time.sleep(90)
