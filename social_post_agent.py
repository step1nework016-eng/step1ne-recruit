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
import ast, json, os, re, subprocess, sys, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'

SKILL_PATH = os.path.expanduser(
    '~/工作流程技能包/recruiting-workflow/social-recruiting-post/SKILL.md')

# 貼文用的模型——這是一次性、非即時的工作，用跟阿財/阿福一樣的模型即可，
# 不需要另外挑更貴或更快的。
POST_MODEL = 'claude-sonnet-5'
CLAUDE_TIMEOUT = 180

TG_THREAD_SOCIAL = 3306  # 2026-08-14 加：獨立的「社群發文審核」主題，不要跟 #4 履歷池混在一起


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
    add('工作地點', job.get('locations'))
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
        unit = job.get('salary_unit') or '月薪'
        lo, hi = job.get('salary_min'), job.get('salary_max')
        add('薪資', f"{unit} {lo}–{hi}" if lo and hi else f"{unit} {lo or hi}")
    add('薪資備註', job.get('salary_note'))
    add('團隊規模', job.get('team_size'))
    page = public_page_text(job.get('slug') or '')
    if page:
        lines.append('\n【公開職缺頁上已經寫出來的內容——這是我們自己對外刊的文字，'
                     '可以直接引用、改寫，工作內容與福利請以這裡為準】\n' + page)
    return '\n'.join(lines) if lines else '（這個職缺目前結構化資料很少，請顧問補充後再產文案，或直接手動撰寫）'


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


def tg_with_buttons(text, buttons, thread=None):
    """跟 interview_daemon.py 的 tg() 一樣讀設定檔，但這裡要帶 inline
    keyboard（核准／略過按鈕），tg() 本身沒有這個參數，不改共用函式、
    這支自己組。"""
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        body = {
            'chat_id': e['TG_CHAT_ID'], 'text': text,
            'reply_markup': json.dumps({'inline_keyboard': [buttons]}),
        }
        tid = thread if thread is not None else e.get('TG_THREAD_ID')
        if tid:
            body['message_thread_id'] = tid
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=json.dumps(body).encode(), headers={'content-type': 'application/json'})
        r = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return r.get('result', {}).get('message_id')
    except Exception as ex:
        log(f'Telegram 推播失敗：{ex}')
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
    f'如果連職稱與地點以外幾乎什麼都沒有，就在標記外面說明「資料不足以產出貼文」，\n'
    f'不要硬生一篇出來——那種稿發出去比不發更傷。\n'
    f'Markdown 符號不會被平台轉成粗體或標題，只會照字面被貼出去、變成亂碼。'
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
    真的要發布的文案。抓不到標記就整段當文案（保底行為，至少不會直接壞掉，
    但這種情況應該去看 log 確認是不是模型沒照格式回）。"""
    i = raw.find(POST_START)
    j = raw.find(POST_END)
    if i >= 0 and j > i:
        post = raw[i + len(POST_START):j]
    else:
        log('⚠️ 沒抓到 POST_START/POST_END 標記，整段當文案用，麻煩檢查一下原始回覆')
        post = raw
    return strip_markdown(post)


def generate_draft(job, account_id):
    prompt = skill(account_id) + '\n\n' + format_job_requirement(job) + WRAP_INSTRUCTION
    raw = run_claude(prompt)
    return raw, extract_post(raw) if raw else None


def process_job(queue_row, job, repost=False):
    """2026-08-18 改：一個職缺可以對應多筆排隊紀錄（social_post_queue），
    不再是 jobs 表上唯一一組欄位——顧問要讓四個人各自對同一個職缺發一篇，
    原本的做法（狀態存在 jobs 本身）後排的帳號會直接覆蓋前一個人排的，
    真實案例已經撞到。現在每一筆排隊紀錄各自獨立，用 queue id 當按鈕的
    識別碼，互不影響。"""
    qid = queue_row['id']
    slug, title = job['slug'], job['title']
    account_id = queue_row.get('account_id')
    try:
        log(f'{title}（{slug}）：產生貼文草稿中…')
        raw, post = generate_draft(job, account_id)
        if not raw or not post:
            log(f'❌ {title}：claude 沒有回東西')
            return

        # draft 只存乾淨的文案（會被拿去真的發布）；完整原文（含合規檢查／
        # 附加輸出）只送進 Telegram 給顧問看，不落地存表，顧問要留紀錄的話
        # 自己在 Telegram 裡搜。
        d1(f"UPDATE social_post_queue SET draft={q(post)}, status='drafted' WHERE id={qid}")

        end_idx = raw.find(POST_END)
        analysis = raw[end_idx + len(POST_END):].strip() if end_idx >= 0 else ''
        # 2026-08-17 加：多顧問各自審核——這筆排隊紀錄如果指定過帳號，就送到
        # 那個帳號綁定的專屬主題（EYLISE／PHOEBE／DR 各自一個），沒指定的
        # （排程自動掃到、沒人特別指名的舊職缺）維持送到共用的 TG_THREAD_SOCIAL。
        thread = TG_THREAD_SOCIAL
        if account_id:
            acc = d1(f"SELECT tg_thread_id FROM social_accounts WHERE id={q(account_id)}")
            if acc and acc[0].get('tg_thread_id'):
                thread = int(acc[0]['tg_thread_id'])
        msg_id = tg_with_buttons(
            f"📱 全民獵才貼文草稿：{title}\n{'（重新產出）' if repost else ''}\n\n"
            f"── 以下會被公開發布 ──\n{post}\n\n"
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
    never_queued = d1(
        "SELECT j.slug FROM jobs j "
        "LEFT JOIN social_post_queue q ON q.job_slug = j.slug "
        "WHERE j.status IN ('open','active') AND q.id IS NULL"
    )
    for r in never_queued:
        d1(f"INSERT INTO social_post_queue (job_slug, account_id, requested_at) "
           f"VALUES ({q(r['slug'])}, NULL, datetime('now','+8 hours'))")

    queue_rows = d1("SELECT * FROM social_post_queue WHERE status IS NULL ORDER BY requested_at ASC")
    if not queue_rows:
        log('沒有需要產貼文的新職缺')
        return
    jobs_cache = {}
    for qrow in queue_rows:
        slug = qrow['job_slug']
        if slug not in jobs_cache:
            jobs_cache[slug] = _job_by_slug(slug)
        job = jobs_cache[slug]
        if not job:
            log(f'⚠️ 排隊紀錄 {qrow["id"]} 指向不存在的職缺 {slug}，跳過')
            continue
        process_job(qrow, job)


if __name__ == '__main__':
    main()
