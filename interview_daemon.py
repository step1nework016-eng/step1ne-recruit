#!/usr/bin/env python3
"""面談室的回話引擎：輪詢 D1，看到候選人講話就跑一次 claude，把回覆寫回去。

為什麼是本機常駐而不是在 Worker 裡呼叫 API：
使用者的決定——用本機 claude CLI，不另外開 Anthropic API 金鑰。
代價是回覆會慢幾十秒，所以前端一定要有「正在輸入」的指示，
否則候選人會以為當掉了。

平行處理：每位候選人一條執行緒，同時最多 MAX_PARALLEL 場。
超過就排隊——寧可讓第 6 個人多等，也不要六場一起變慢到全部逾時。

用法：
    python3 interview_daemon.py           # 常駐
    python3 interview_daemon.py --once    # 跑一輪就結束（測試用）
"""
import json, os, subprocess, sys, threading, time, datetime, urllib.parse, urllib.request
import base64, mimetypes, uuid, re   # 推報告 PDF 與履歷附件用
import shutil, tempfile              # 交付時產 PDF 的暫存目錄（deliver.py）

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
POLL_SEC = 8
MAX_PARALLEL = 3        # 這台是 8GB／4 核，每個 claude 程序約 200–400MB。
                        # 設 5 會在剩 2.3GB 可用記憶體時開始 swap，
                        # 那會讓「所有」進行中的面談一起變慢，不只排隊的。
                        # 寧可讓第 4 個人排隊，也不要三個人一起卡住。
CLAUDE_TIMEOUT = 240
# ⚠️ 2026-08-19 加：報告轉 JSON 要另外給時間，不能跟對話共用 240 秒。
# 全筱琪那場（直播主）就是這樣掛的——當天報告規格加了到職障礙、專業問答、
# 六維度評分三個區塊，SPEC 變長、要產的 JSON 也變長，240 秒不夠，
# 結果純文字報告有了、結構化那份是 NULL，顧問拿不到兩版 PDF。
# 對話要快（候選人在等），產報告可以慢（沒有人在等那一秒）。
REPORT_TIMEOUT = 600
MAX_TURNS = 90          # 防跑不完：超過就強制收尾
                        # ⚠️ 2026-08-19 從 40 調高：加了職缺專業題庫之後，光是專業題
                        # 就可能 8–14 題，每題還要追問——40 輪會在專業段中間被硬切掉。
STALE_MIN = 30          # ⚠️ 2026-08-13 從 15 分鐘調高：徐先生這場實測，只要他回覆間隔
                        # 超過 15 分鐘（打字慢、在想怎麼回答），系統就會自動觸發
                        # wrap_up()，在對話中間插入一段「看您這邊暫時沒有回覆⋯」的
                        # 道別訊息，跳針感很重——他明明還在認真回答，卻一直被系統
                        # 講「謝謝您今天撥出時間」。15 分鐘對認真回答中高階問題
                        # 的人來說太短，改成 30 分鐘。
# 中途離開後，房間還要留多久給他回來（分鐘）。
# 顧問常常在忙，15 分鐘就關掉等於誰都來不及反應。
# ⚠️ 2026-08-28 從 3 小時放寬到 24 小時（Jacky 指定）。
# 3 小時的問題是：人選常常是在上班時間偷空談，被叫走、開會、下班通勤，
# 一晃就過了；回來時房間已經關了，要重新開始——而重新開始的人多半就不談了。
# 周丞恩 8/28 就是這樣：09:23 被問到「住桃園大溪要怎麼配合苗栗銅鑼」，
# 答不出來就先擱著，09:53 房間就自動收了。
# 保留 24 小時對容量沒有影響：MAX_PARALLEL 只算 active，paused 不佔位。
STALE_HOLD_MIN = 1440         # 候選人多久沒回就關掉房間（閒置判定，跟下面的硬上限是兩件事）
SOFT_TARGET_MIN = 60    # 阿財應該盡量在這個時間內收集完必問項並主動收尾（軟目標，寫進 prompt 讓它自己抓節奏）
# ⚠️ 2026-08-19 從 60 分鐘放寬到 2 小時（Jacky 指定）。
# 放寬的理由不是「聊久一點比較好」——是專業題庫上線後，一場要問完通用必問項
# ＋該職缺 8–14 題專業題＋外語情境題，60 分鐘會在中途被硬切。
# 但這仍然是**上限不是目標**：軟目標還是 60 分鐘，阿財要自己抓節奏，
# 拖到 2 小時的面談對候選人是折磨，對顧問也不會產出更好的判斷。
ROOM_HARD_LIMIT_MIN = 120

# Step1ne LINE 官方帳號。沒履歷的候選人收尾時要給他這個，
# 讓他接得上真人顧問、也有地方可以把履歷傳過來。
# ── Telegram 主題分工（2026-08-10 跟 Jacky 對齊）──
#
# 原本什麼都往 2855 塞，變成大雜燴：要按按鈕的跟純告知的混在一起，
# 重要的被洗掉。判準是「顧問要不要動手」：
#
#   2855 面試通知確認 ← 只放需要人決定／回應的（SOS、審核卡、停滯提醒）
#    304 #4 履歷池   ← 面談報告與 PDF。那是資料不是決策，有空再看
#   1360 系統回報     ← 排程結果、錯誤、逾時失敗。出事才看
#      4 #3履歷進件   ← 新應徵、開始面談（Worker 那邊在用）
THREAD_DECIDE = 2855
THREAD_POOL = 304
THREAD_SYSTEM = 1360

LINE_OA_URL = 'https://lin.ee/XcSWPzM'

# ── 阿財一律不帶任何工具 ──
# 2026-08-07 實測發現的兩個問題，這一組參數同時解決：
#
# 1. **安全**：原本是 `--permission-mode acceptEdits`，而且內建工具與所有 MCP
#    伺服器都會載入——實際輸出裡出現過「Skipping the accounting tool」，
#    代表模型真的看得到 agentacct 這個工具並試圖呼叫它。
#    阿財面對的是**外部候選人**，候選人打的字會整段進到 prompt 裡，
#    等於把 Bash／Edit／Read 與一堆 MCP 工具暴露在一個可被注入的介面後面。
#    阿財的工作只是產生一段 JSON 文字，一個工具都不需要。
#
# 2. **成本**：工具定義與 MCP schema 每一輪都要重送。實測同一段 prompt：
#    帶工具 45,166 token／$0.1808，不帶工具 20,911 token／$0.1090——
#    輸入少 54%、成本少 40%，回覆品質沒有差別。
#
# ⚠️ 不要為了「讓阿財可以自己查資料」把工具加回來。要給它資料就先查好、
#    放進 prompt，不要讓外部輸入有機會驅動工具。
#
# 3. **`--setting-sources ''` 一定要一起帶。** 使用者的全域 ~/.claude/CLAUDE.md
#    規定「第一個工具呼叫之前要先開 agentacct section」——那條是給互動 session 用的，
#    但它對每一個 `claude -p` 都生效。工具還在時模型會照做（白燒 token）；
#    把工具關掉之後模型會卡住，只吐出「agentacct_record_section」這幾個字就結束，
#    **整個回覆變成空的**。2026-08-07 實測：初篩因為 prompt 只有 2,900 字，
#    直接被那條規則蓋過去，輸出 0 字元；阿財因為 prompt 有 15,000 字才沒被壓垮。
#    這三個參數是一組的，不要只加一半。
# 🚨 產報告的模型不該有任何工具。
#
# ⚠️ 2026-08-11 實測發現：`--tools ''` **完全沒有作用**。
#    實際問它「你有哪些工具」，回答是 Task / Bash / Glob / Grep / Read /
#    **Edit / Write** / AskUserQuestion——也就是說，這一年來產報告與產 JSON 的
#    那個模型一直握有讀寫檔案與執行指令的權限。
#    當天就出過事：把「請把報告轉成 JSON」的請求當成寫程式任務，
#    跑去 Search interview_daemon.py，結果輸出不是 JSON，content_json 存成 NULL。
#
#    改用 --disallowed-tools 逐一列名（實測有效，只剩下無害的 plan/worktree 類）。
#    ⚠️ 之後 Claude Code 新增工具時要回來補這份清單。
_BAN_TOOLS = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
              'AskUserQuestion,TodoWrite,BashOutput,KillShell,SlashCommand,Skill,'
              'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN_TOOLS,
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--setting-sources', '']

# 對談與報告用不同模型：
# 對談要的是「快」——真人電訪是一問一答，等 40 秒沒有人受得了。
# 報告要的是「準」——那是顧問拿來決定推不推的依據，慢一分鐘沒差。
TALK_MODEL = 'claude-sonnet-5'
REPORT_MODEL = 'claude-sonnet-5'

_busy = set()           # 正在處理的 application_id，避免同一場被跑兩次
_lock = threading.Lock()

# ⚠️ 2026-08-14 加：這台機器上跟 checkup_daemon.py 共用同一個本機 claude 登入
# 額度，額度打滿時 claude -p 不一定乾脆地失敗——有時候會回一段文字說「這段對話
# 結尾怪怪的，我不該亂編」，不是預期的 JSON，被當成一般錯誤處理。一般錯誤的
# 處理方式是塞一句道歉訊息給候選人，但這樣會把 last_role 從 'candidate' 翻成
# 'assistant'，STALE_MIN 分鐘後 stale() 就會誤判候選人閒置、提早收尾產報告——
# 額度用盡不該算在候選人頭上：不插入道歉訊息（讓 last_role 留在 'candidate'，
# 下一輪自動重試），改用長一點的鎖節流，額度重置後自然接上，候選人不用重講一次。
RATE_LIMIT_RETRY_SEC = 300      # 額度打滿時，同一場隔多久才重試一次
RATE_LIMIT_NOTIFY_COOLDOWN_SEC = 1800  # 同一次額度事故只提醒 Jacky 一次，不要洗版
_rate_limit_notified_at = 0.0


def _is_rate_limit_error(e):
    s = str(e).lower()
    return 'hit your limit' in s or 'usage limit' in s


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
                    env[k] = v
    # claude CLI 在巢狀 session 裡會拒跑，這個變數一定要拿掉
    env.pop('CLAUDECODE', None)
    return env


# ── D1 走 HTTP，不再每次查詢都開一個 node ──
# 2026-09-08 加。原本每查一次就 subprocess 一個 `npx wrangler d1 execute`，
# npx 再拉起 node，一次約 100MB＋冷啟動。這支 daemon 每 8 秒輪詢一次，
# 三支 daemon 加起來一分鐘要開快 30 次 node——8GB 的機器負載衝到 17、
# 交換檔吃掉 5GB。改成直接打 D1 REST API，同樣的查詢不開任何子行程。
# HTTP 失敗一律退回原本的 wrangler：面談是候選人正在等的即時流程，
# 寧可慢也不能斷。
try:
    import d1_http as _D1H
except Exception:
    _D1H = None
_D1H_WARNED = False


def _d1_http_try(sql):
    """成功回傳結果 dict，不能用就回 None（讓呼叫端走 wrangler）。"""
    global _D1H_WARNED
    if not (_D1H and _D1H.available()):
        return None
    try:
        return _D1H.query(sql)
    except Exception as e:
        if not _D1H_WARNED:
            _D1H_WARNED = True
            try:
                log(f'D1 HTTP 失敗，改用 wrangler（只提醒這一次）：{e}')
            except Exception:
                pass
        return None


def d1_raw(sql):
    """回傳整包結果（含 meta.changes），鎖機制要看 changes 才知道有沒有搶到。"""
    _h = _d1_http_try(sql)
    if _h is not None:
        return _h
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', DB, '--remote', '--json', f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-300:])
    out = r.stdout[r.stdout.index('['):]
    return json.loads(out)[0]


def d1_file(sql):
    """跟 d1_raw 一樣，但把 SQL 寫進暫存檔用 --file 執行，不走 --command。

    2026-08-12 加。d1_raw 把整段 SQL（含值）塞進單一 CLI 參數，
    報告內容（content_md／content_json）長一點就會撞到
    `statement too long: SQLITE_TOOBIG`——尹緯正那場報告就是這樣整個沒存進去、
    顧問拿到「請接手」卻沒有任何報告內容。
    --file 走檔案讀取，不受 CLI 參數長度限制，行為跟 --command 完全一樣。
    只有真的可能很長的寫入（目前就是 reports 表）才需要用這個，
    其他短查詢繼續用 d1_raw／d1 就好。
    """
    with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False, encoding='utf-8') as f:
        f.write(sql)
        path = f.name
    try:
        r = subprocess.run(
            ['npx', '--yes', 'wrangler', 'd1', 'execute', DB, '--remote', '--json', f'--file={path}'],
            cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=180)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout)[-300:])
        out = r.stdout[r.stdout.index('['):]
        return json.loads(out)[0]
    finally:
        os.unlink(path)



RUNLOG = os.path.expanduser('~/aijob-automation/run-log.jsonl')


def runlog(task, status, summary, metrics=None):
    """把一次執行記進 run-log.jsonl，讓儀表板與 Telegram 的「今日執行」看得到。

    2026-08-06 加。在此之前**只有 aijob 那批排程在寫**，招募這邊
    （阿財、初篩、履歷池、履歷解析）一筆都沒有——
    Step1ne 助手按「今日執行」永遠是「今天還沒有排程跑過」，
    看起來像系統沒在跑，實際上一直在跑。

    ⚠️ **沒事就不要記。** resumeparse 每 15 分、screening 每 20 分跑一次，
    每次都記的話一天會塞進 168 筆「今天沒事」，那份紀錄就沒人看了。
    只在真的處理了東西、或出錯的時候呼叫。
    """
    try:
        os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)
        with open(RUNLOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                'task': task, 'status': status, 'summary': summary,
                'metrics': metrics or {}}, ensure_ascii=False) + '\n')
    except Exception as e:
        log(f'runlog 寫入失敗（不影響主流程）：{e}')

def d1(sql):
    return d1_raw(sql).get('results', [])


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


# ── token 用量記錄 ──
# 2026-08-14 加：Jacky 要跟老闆提買 API，需要每場面談實際花多少 token／多少錢
# 的真實數字，不能只靠估算。
#
# ⚠️ 做法刻意不去動 subprocess.run() 那三支呼叫本身（不改 --output-format、
# 不改任何既有的解析邏輯）——面談是候選人正在等的即時流程，任何一行新程式碼
# 只要猜錯格式就會讓阿財當場斷線。改成事後讀 claude CLI 自己寫的 session
# jsonl（跟 agent_token_breakdown.py 讀的是同一批檔案，schema 已經驗證過），
# 純粹是「多讀一份存證」，記錄失敗最多就是這一筆沒有 token 數字，
# 不會影響訊息有沒有送出去。
CLAUDE_PROJECTS_DIR = os.path.expanduser(
    '~/.claude/projects/-Users-user---------step1ne-recruit')


def _snapshot_session_files():
    try:
        return set(os.listdir(CLAUDE_PROJECTS_DIR))
    except Exception:
        return set()


def _sum_session_usage(path, expect_prefix=None):
    """讀一個 claude -p 呼叫產生的 session jsonl，加總 usage。
    expect_prefix 有給的話，要求第一則 user 訊息開頭吻合，才不會在併發時
    （最多 MAX_PARALLEL 場同時在跑）撿到別場面談剛好同時寫完的檔案。"""
    inp = outp = cw = cr = 0
    model = None
    matched = expect_prefix is None
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get('message') or {}
                if (not matched and d.get('type') == 'user'
                        and isinstance(msg.get('content'), str)):
                    matched = msg['content'][:60] == expect_prefix[:60]
                us = msg.get('usage')
                if not us:
                    continue
                model = model or msg.get('model')
                inp += us.get('input_tokens') or 0
                outp += us.get('output_tokens') or 0
                cw += us.get('cache_creation_input_tokens') or 0
                cr += us.get('cache_read_input_tokens') or 0
    except Exception:
        return None
    if not matched or not (inp or outp):
        return None
    return {'model': model, 'input_tokens': inp, 'output_tokens': outp,
            'cache_creation_input_tokens': cw, 'cache_read_input_tokens': cr}


# ── 就業服務法第 5 條紅線（阿財說出口的話）──
# 2026-08-19 加。SKILL.md 早就寫著「不准問」，報告端也擋了，但**阿財即時說出去
# 的話沒有任何程式端檢查**——規範寫在 prompt 裡，模型守不守是機率問題，
# 而候選人看到的是已經送出去的字。
#
# ⚠️ 這裡刻意**不硬擋**（跟後台結案訊息不同）。硬擋會讓對話當場中斷，
#    候選人乾等一則永遠不會來的回覆，那個體驗比誤講一句更糟。
#    作法是：命中就重生成一次，仍命中才用安全句替代，並且一律通知顧問。
#
# ⚠️ 只檢查**阿財自己講的話**。候選人主動提到自己已婚、有小孩、幾歲，
#    那是他的自由，阿財把它記進報告也是應該的（basics 那一區就是為此存在）。
LAW5_WORDS = [
    '性別', '男性', '女性', '男生', '女生', '限男', '限女',
    '幾歲', '年齡', '歲以下', '歲以上', '年紀多大',
    '已婚', '未婚', '結婚了嗎', '懷孕', '生育', '打算生',
    '國籍', '外籍', '哪一國人', '原住民',
    '身心障礙', '殘障', '宗教', '政黨', '容貌', '長相', '星座', '血型',
]


def law5_hits(text):
    t = str(text or '')
    return [w for w in LAW5_WORDS if w in t]


def log_token_usage(app_id, call_type, prompt, before_files):
    """在對應的 claude -p subprocess.run() 呼叫「之後」呼叫，
    before_files 是呼叫「之前」的 _snapshot_session_files()。
    這支不准往外丟例外——記錄是加值功能，不是面談流程的一部分。"""
    if not app_id:
        return
    try:
        after_files = set(os.listdir(CLAUDE_PROJECTS_DIR))
        new_files = [f for f in (after_files - before_files) if f.endswith('.jsonl')]
        if not new_files:
            return
        usage = None
        if len(new_files) == 1:
            usage = _sum_session_usage(os.path.join(CLAUDE_PROJECTS_DIR, new_files[0]))
        else:
            # 罕見：剛好撞到另一場面談同時完成，用 prompt 開頭比對挑出真正是這通的檔案
            prefix = sanitize(prompt)[:60]
            for f in new_files:
                usage = _sum_session_usage(os.path.join(CLAUDE_PROJECTS_DIR, f), expect_prefix=prefix)
                if usage:
                    break
        if not usage:
            return
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        d1(f"INSERT INTO token_usage "
           f"(application_id, call_type, model, input_tokens, output_tokens, "
           f"cache_creation_input_tokens, cache_read_input_tokens, created_at) VALUES "
           f"({q(app_id)}, {q(call_type)}, {q(usage['model'])}, "
           f"{usage['input_tokens']}, {usage['output_tokens']}, "
           f"{usage['cache_creation_input_tokens']}, {usage['cache_read_input_tokens']}, {q(now)})")
    except Exception:
        pass


LOCK_TTL_SEC = 90   # 比一次 claude 呼叫（240s 逾時）短沒關係——
                    # 這只防「同時搶著寫同一場」，不是防慢；過期就當作那次處理已經死掉，可以重搶


def acquire_lock(app_id):
    """跨行程的鎖：daemon 跟 force_close.py 都要用這個才不會同時動同一場的訊息。

    2026-07-30 實際發生過：force_close.py 手動收尾的同時，daemon 剛好也在處理
    同一位候選人，兩邊各自基於自己讀到的舊對話產生訊息，寫進去的順序交錯，
    整場面談被搞壞兩次才發現。_busy 那個記憶體集合只防得住同一個 process 裡的重複，
    防不住兩個各自獨立執行的 python 程序。

    做法是一次原子性的 UPDATE：只有在鎖是空的或已經過期時才寫得進去，
    寫入是否成功看 meta.changes（不是 0 就是搶到了）。
    """
    now = datetime.datetime.now()
    now_s = now.strftime('%Y-%m-%d %H:%M:%S')
    expires = (now + datetime.timedelta(seconds=LOCK_TTL_SEC)).strftime('%Y-%m-%d %H:%M:%S')
    meta = d1_raw(
        f"UPDATE applications SET lock_expires_at='{expires}' "
        f"WHERE id={q(app_id)} AND (lock_expires_at IS NULL OR lock_expires_at < '{now_s}')"
    ).get('meta', {})
    return bool(meta.get('changes'))


def release_lock(app_id):
    d1(f"UPDATE applications SET lock_expires_at=NULL WHERE id={q(app_id)}")


def notify_candidate(app_id, abandoned):
    """請 Worker 寄面談結束通知給候選人。

    為什麼繞一圈走 Worker：Resend 金鑰只放在 Cloudflare 的 secret 裡，
    本機不留第二份。憑證存在一個地方就少一個外洩的點。
    """
    try:
        tok = None
        for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
            if l.startswith('RECRUIT_ADMIN_TOKEN='):
                tok = l.strip().split('=', 1)[1].strip().strip("'\"")
        if not tok:
            return log('找不到 RECRUIT_ADMIN_TOKEN，跳過候選人通知信')
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/interview-done',
            data=json.dumps({'id': app_id, 'abandoned': bool(abandoned)}).encode(),
            headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                     # Cloudflare 會擋掉 Python-urllib 的預設 UA，一定要換掉
                     'user-agent': 'step1ne-interview-daemon/1.0'})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        log(f'候選人通知信：{"已寄出" if r.get("ok") else "寄送失敗"}')
    except Exception as e:
        log(f'候選人通知信失敗：{e}')   # 寄不出去不該影響報告與顧問通知


def _admin_token():
    for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
        if l.startswith('RECRUIT_ADMIN_TOKEN='):
            return l.strip().split('=', 1)[1].strip().strip("'\"")
    return None


def save_report(app_id, content_md, content_json):
    """把初篩報告寫進 D1——2026-08-13 改走 Worker 的 /admin/report-ingest，
    不再用 wrangler d1 execute（連 --file= 都躲不過 D1 本身的單值大小上限，
    徐振倫那場報告就是這樣整個沒存進去）。原生 D1 binding 走的是不同路徑，
    跟 PDF 存檔（saveUpload）同一個道理。回傳報告 id，失敗回傳 None。
    """
    try:
        tok = _admin_token()
        if not tok:
            log('找不到 RECRUIT_ADMIN_TOKEN，報告存不進去')
            return None
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/report-ingest',
            data=json.dumps({'application_id': app_id, 'content_md': content_md,
                              'content_json': content_json}).encode(),
            headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                     'user-agent': 'step1ne-interview-daemon/1.0'})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        if not r.get('ok'):
            log(f'報告寫入失敗：{r}')
            return None
        return r.get('id')
    except Exception as e:
        log(f'報告寫入失敗：{e}')
        return None


def tg(text, thread=None):
    # 這支用獨立的設定檔（step1ne-tg.env），不要跟總指揮 yuqi 共用的 tg.env 混在一起——
    # 2026-07-31 差點把 yuqi 的 bot token 換成這個 bot，那樣 yuqi 會整個換身分。
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        body = {'chat_id': e['TG_CHAT_ID'], 'text': text}
        tid = thread if thread is not None else e.get('TG_THREAD_ID')
        if tid:
            body['message_thread_id'] = tid
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode(body).encode(),
            timeout=20)
    except Exception as ex:
        log(f'Telegram 推播失敗：{ex}')


def tg_buttons(text, buttons, thread=None):
    """跟 tg() 一樣，但帶 inline keyboard。

    buttons 是 [[{'text':..,'callback_data':..}, ...], ...]（一列一個 list）。
    按下去由 Worker 的 callback handler 處理，這支不負責接。
    """
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        body = {'chat_id': e['TG_CHAT_ID'], 'text': text,
                'reply_markup': json.dumps({'inline_keyboard': buttons})}
        tid = thread if thread is not None else e.get('TG_THREAD_ID')
        if tid:
            body['message_thread_id'] = tid
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
            data=urllib.parse.urlencode(body).encode(), timeout=20)
    except Exception as ex:
        log(f'Telegram 按鈕推播失敗：{ex}')


def tg_doc(data, filename, caption='', thread=None):
    """把檔案當附件推到同一個 Telegram 群組。

    為什麼要有這支：原本只推一行「報告在後台」＋連結。但顧問多半是在外面用手機
    收到通知的，點進去還要輸入 ADMIN_TOKEN，等於當下看不了。
    附件直接點開就能讀，不用登入、不用電腦。

    標準庫沒有 multipart encoder，手動組。
    """
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        b = '----s1' + uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{e["TG_CHAT_ID"]}\r\n'.encode()]
        if thread is not None:
            e = dict(e, TG_THREAD_ID=str(thread))
        if e.get('TG_THREAD_ID'):
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="message_thread_id"'
                         f'\r\n\r\n{e["TG_THREAD_ID"]}\r\n'.encode())
        if caption:
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'.encode())
        ctype = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        parts.append((f'--{b}\r\nContent-Disposition: form-data; name="document"; '
                      f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n').encode())
        parts.append(data)
        parts.append(f'\r\n--{b}--\r\n'.encode())
        body = b''.join(parts)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendDocument",
            data=body, method='POST',
            headers={'Content-Type': f'multipart/form-data; boundary={b}',
                     'Content-Length': str(len(body))})
        urllib.request.urlopen(req, timeout=60)
        return True
    except Exception as ex:
        log(f'Telegram 附件推播失敗（{filename}）：{ex}')
        return False


def push_report_files(app_id, name, job_slug):
    """面談結束後把「報告 PDF」與「履歷原檔」一起推給顧問。

    ⚠️ 任何一步失敗都只記 log，不要往外拋——通知是附加價值，
    不能因為 PDF 產不出來就讓整個面談收尾流程掛掉。
    """
    # 1) 報告 PDF。export_pdf.py 會 import 這支檔案，所以要開子行程跑，不能直接 import。
    try:
        out = f'/tmp/step1ne_report_{app_id[:8]}.pdf'
        r = subprocess.run(['python3', os.path.join(HERE, 'export_pdf.py'), app_id, '--out', out],
                           capture_output=True, text=True, env=env_with_cf(), timeout=120)
        if r.returncode == 0 and os.path.exists(out):
            with open(out, 'rb') as f:
                tg_doc(f.read(), f'初篩報告_{name}_{job_slug}.pdf',
                       f'📄 {name} 的初篩報告與逐字稿', THREAD_POOL)
            os.remove(out)
        else:
            log(f'報告 PDF 產生失敗：{(r.stderr or r.stdout or "")[-200:]}')
    except Exception as ex:
        log(f'報告 PDF 例外：{ex}')

    # 2) 履歷原檔。顧問要判斷推不推，光看報告不夠，常常要翻回履歷對細節。
    try:
        rows = d1(f"SELECT f.id AS fid, f.filename, f.content_b64, f.chunks FROM applications a "
                  f"JOIN files f ON f.id = a.resume_file_id WHERE a.id = {q(app_id)}")
        if not rows:
            # 沒上傳檔案不代表沒履歷——很多人是貼雲端連結或個人作品集網站。
            # 2026-08-07：原本這裡就只印一行「沒有履歷檔可推」，顧問在 Telegram 上
            # 什麼都收不到，得自己回後台翻，等於這個推播對這類候選人形同沒有。
            u = d1(f"SELECT resume_url, resume_url_note, LENGTH(COALESCE(resume_url_text,'')) L "
                   f"FROM applications WHERE id = {q(app_id)}")
            url = (u[0].get('resume_url') if u else None) or ''
            # 從 FB 複製過來的連結會拖著 fbclid，佔掉整行還看不出是什麼站
            url = re.sub(r'[?&](fbclid|gclid|utm_[a-z]+)=[^&]*', '', url).rstrip('?&')
            if url:
                note = (u[0].get('resume_url_note') or '')
                ok = note == 'ok' and (u[0].get('L') or 0) > 0
                tg(f'🔗 {name} 沒有上傳履歷檔，他留的是連結：\n{url}\n'
                   + ('（內容有抓到，阿財面談時看得到）' if ok
                      else f'⚠️ 這個連結抓不到內容（{note or "尚未解析"}），阿財面談時看不到，'
                           '報告只根據對話內容'), THREAD_POOL)
            else:
                log(f'{name} 沒有履歷檔也沒有履歷連結')
        else:
            f = rows[0]
            b64 = f.get('content_b64')
            # ⚠️ 大檔案不是存在 content_b64，是切段存在 file_chunks。
            # 2026-08-05 第一版只讀 content_b64，范博翔的履歷就被判定成「沒有」——
            # 明明有檔案。Worker 那邊本來就有處理分段，這裡漏掉了。
            if not b64 and f.get('chunks'):
                seg = d1(f"SELECT b64 FROM file_chunks WHERE file_id = {q(f['fid'])} ORDER BY idx ASC")
                b64 = ''.join(x['b64'] for x in seg)
            if b64:
                fn = f.get('filename') or 'resume.pdf'
                # 候選人上傳的檔名常常已經含自己的名字（104 下載的就是），
                # 再加一次會變成「履歷_周承緯_周承緯.pdf」。含了就不重複加。
                stem = fn.rsplit('.', 1)[0]
                out = fn if name in stem else f'履歷_{name}_{fn}'
                tg_doc(base64.b64decode(b64), out, f'📎 {name} 的履歷原檔', THREAD_POOL)
            else:
                log(f'{name} 的履歷檔沒有內容（content_b64 與 file_chunks 都是空的）')
    except Exception as ex:
        log(f'履歷推播例外：{ex}')


# ─────────────────────────────────────────────────────────────
# 面談結束後的交付（2026-08-10 加）
#
# 在此之前，面談結束只推一行「面談完成 + 後台連結」。顧問人在外面、
# 手機上點連結還要輸入 ADMIN_TOKEN，等於當下什麼都看不到。
# 現在一次推四件事：
#   ① 內部報告 PDF（顧問版）② 外部報告 PDF（客戶版，可直接轉給用人企業）
#   ③ 人選原始履歷（有檔案推檔案，只有連結就把連結寫在訊息裡）＋作品集
#   ④ 簡短結語 ＋ 顧問要協助的事項，結尾固定「詳細請參閱內部報告」
#
# ⚠️ 整段是「附加價值」，不是面談的一部分。任何一步失敗都只記 log，
#    絕對不可以讓面談收尾流程掛掉——finish() 那邊也再包一層 try/except。
def _client_named_from_relation(relation):
    """把 client_companies.relation（客戶關係，公司層級的單一事實來源）換算成
    deliver.py 要的 client_named／client_relation。

    ⚠️ 2026-09-04 抓到的坑：jobs.client_named／jobs.client_relation 是每個
    職缺各自存一份的「複本」，跟 client_companies.relation 這個真正的事實
    來源脫鉤——同一個客戶（築樂國際開發）底下4個職缺，只有1個複本正確標成
    已簽約，其餘3個還是預設的匿名值，孫悅推薦到其中一個漏標的職缺，報告就
    被錯誤匿名化了。正確做法是永遠拿 client_companies.relation 換算，
    不要相信 jobs 表上那份可能沒同步更新的複本。
    """
    r = relation or ''
    if r == 'signed':
        return 1, 'signed'
    if r == 'private':
        return 0, 'private'
    return 0, 'unsigned'  # negotiating / prospect / end_client / 空值，一律當作還沒簽約


def _delivery_meta(app_id, name, job_slug, abandoned):
    """把樣板需要、但 content_json 裡沒有的欄位補齊。

    刻意直接查 D1 而不是從 ctx 拿：ctx 是給模型看的快取，欄位會隨 prompt 需求變動；
    交付要用的這幾欄（尤其 client_named／ai_disclosure 這兩個法遵開關）
    必須每次讀當下的真值，不能吃到面談開始時的快照。
    """
    meta = {'name': name, 'job_slug': job_slug, 'abandoned': bool(abandoned),
            'resume': {'kind': 'none'}, 'portfolio_urls': [], 'system_record': {}}
    rows = d1(
        f"SELECT a.expected_salary, a.available_date, a.location_ok, a.note, a.social_links, "
        f"a.resume_url, a.resume_url_note, a.resume_file_id, "
        f"a.disc_d, a.disc_i, a.disc_s, a.disc_c, "
        f"a.interview_started_at, a.interview_ended_at, "
        f"a.call_summary_client_md, a.call_summary_client_at, "
        f"j.title AS job_title, j.ai_disclosure, cc.relation AS client_company_relation, "
        f"j.hard_filters, j.must_check_items "
        f"FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug "
        f"LEFT JOIN client_companies cc ON cc.id = j.company_id "
        f"WHERE a.id = {q(app_id)}")
    if not rows:
        return meta
    r = rows[0]
    client_named, client_relation = _client_named_from_relation(r.get('client_company_relation'))
    meta.update({
        'job_title': r.get('job_title') or job_slug,
        # 應徵表單填的社群連結。報告只放連結本身、不做任何評價——
        # 阿財看不到內容，要顧問或用人主管自己點開看。
        'social_links': r.get('social_links'),
        # ⚠️ 這兩個是客戶隱私與法遵開關，不要給預設值。
        # deliver.is_anonymous() 對 NULL 一律從嚴當匿名處理。
        # 2026-09-04 改：一律從 client_companies.relation 換算，不要相信
        # jobs 表上可能沒同步更新的複本（見 _client_named_from_relation 註解）。
        'client_named': client_named,
        'ai_disclosure': r.get('ai_disclosure'),
        # 客戶對象（signed／unsigned／private）。private＝朋友私人協助，
        # 客戶版報告不可以有任何 Step1ne 品牌痕跡，見 deliver._branding()。
        'client_relation': client_relation,
        'expected_salary': r.get('expected_salary'),
        'available_date': r.get('available_date'),
        'location_ok': r.get('location_ok'),
        'hard_filters': r.get('hard_filters'),
        'must_check_items': r.get('must_check_items'),
        'disc': {'d': r.get('disc_d'), 'i': r.get('disc_i'),
                 's': r.get('disc_s'), 'c': r.get('disc_c')},
        # 顧問電洽補充：顧問確認過的客戶安全版（deliver._callup() 用）。
        # 面談剛結束當下這兩欄一定是空的（還沒打電話），自動投遞那次
        # 本來就不該有這段，跟規格「不會有電洽版，保留不動」一致。
        'call_summary_client': r.get('call_summary_client_md'),
        'call_summary_at': r.get('call_summary_client_at'),
        # 2026-09-04 加：interview_started_at 是空的代表這個人選從沒真的跟AI
        # 阿財開始過面談——Jacky確認過這是允許的正常流程（純顧問電洽也可以），
        # 但報告樣板原本無條件都寫「已完成AI結構化初步面談」，會把顧問電洽
        # 問到的內容誤標成AI面談問到的。deliver.py要靠這個欄位挑對的說法。
        'has_real_interview': bool(r.get('interview_started_at')),
    })
    if r.get('resume_file_id'):
        meta['resume'] = {'kind': 'file', 'filename': None}
    elif r.get('resume_url'):
        meta['resume'] = {'kind': 'url', 'url': r['resume_url'],
                          'note': r.get('resume_url_note')}
    # 作品集沒有獨立欄位，候選人多半貼在備註裡。抓得到就印在報告上（規格：連結直接印）。
    for u in re.findall(r'https?://\S+', r.get('note') or ''):
        if u != (r.get('resume_url') or ''):
            meta['portfolio_urls'].append(u)

    n = d1(f"SELECT COUNT(*) AS n FROM messages WHERE application_id = {q(app_id)}")
    meta['system_record'] = {
        '面談時間': f"{r.get('interview_started_at') or '—'} – {r.get('interview_ended_at') or '（尚未關閉）'}",
        '訊息數': f"{(n[0]['n'] if n else '—')} 則",
        '結束方式': '⚠️ 候選人中途離開' if abandoned else '正常收尾',
        'application_id': app_id,
    }
    return meta


def _resume_attachment(app_id):
    """履歷原檔的位元組內容。沒有檔案就回 (None, None)。

    大檔案不是存在 content_b64，是切段存在 file_chunks——
    2026-08-05 第一版漏了這段，有履歷的人被判成「沒有」。
    """
    rows = d1(f"SELECT f.id AS fid, f.filename, f.content_b64, f.chunks FROM applications a "
              f"JOIN files f ON f.id = a.resume_file_id WHERE a.id = {q(app_id)}")
    if not rows:
        return None, None
    f = rows[0]
    b64 = f.get('content_b64')
    if not b64 and f.get('chunks'):
        seg = d1(f"SELECT b64 FROM file_chunks WHERE file_id = {q(f['fid'])} ORDER BY idx ASC")
        b64 = ''.join(x['b64'] for x in seg)
    if not b64:
        return None, f.get('filename')
    # 檔名是從瀏覽器上傳時帶進來的，中文常常是 percent-encoded
    # （實際存到的是「%E5%91%A8%E6%89%BF%E7%B7%AF.pdf」）。
    # 推到 Telegram 給人看的東西不該長這樣，解回中文。
    fn = f.get('filename') or 'resume.pdf'
    try:
        if '%' in fn:
            fn = urllib.parse.unquote(fn)
    except Exception:
        pass
    return base64.b64decode(b64), fn


def deliver_after_interview(app_id, name, job_slug, report_json, abandoned):
    """推四件事。降級路徑：content_json 是 NULL 時只推純文字報告與履歷。"""
    import deliver   # 放在函式內 import：這支檔案壞掉時不要連 daemon 都起不來

    meta = _delivery_meta(app_id, name, job_slug, abandoned)

    data, degrade_reason = None, '這場沒有產出結構化報告（模型沒吐出合法 JSON，或產報告時逾時）'
    if report_json:
        try:
            data = json.loads(report_json)
            # ⚠️ 2026-08-19 加：孙悦那場（08-18）存進去的是「JSON 字串的字串」——
            # 解析一次只會得到 str，不是 dict，後面 data.get() 就全炸，
            # 顧問收到的是「結構化報告產生失敗、PDF 未附」但資料其實好好的。
            # 十筆報告只有那一筆這樣，根因追不出來（當下沒有留下相關 log），
            # 所以兩端都加防線：這裡多解一次，寫入端也擋一次。
            if isinstance(data, str):
                log(f'⚠️ {name} content_json 多包了一層，已自動解開')
                data = json.loads(data)
            if not isinstance(data, dict):
                log(f'⚠️ {name} content_json 不是物件（{type(data).__name__}），改走降級路徑')
                degrade_reason = f'報告存成了非預期的格式（{type(data).__name__}）'
                data = None
        except Exception as ex:
            log(f'⚠️ {name} content_json 解析失敗，改走降級路徑：{ex}')
            degrade_reason = f'報告內容解析失敗（{type(ex).__name__}）'
            data = None

    # ── 降級：沒有結構化報告就產不出兩版 PDF ──
    # 這種情況必須「還是有東西給顧問」，而且要講清楚為什麼少了 PDF，
    # 不然顧問會以為系統掉東西。
    if data is None:
        push_report_files(app_id, name, job_slug)   # 舊路徑：純文字報告 PDF ＋ 履歷
        tg(deliver.closing_message({}, meta, degraded=True, reason=degrade_reason), THREAD_POOL)
        return

    # ① ② 兩版 PDF
    made = []
    tmp = tempfile.mkdtemp(prefix='step1ne_deliver_')
    try:
        made = deliver.build_pdfs(data, meta, tmp)
        for kind, path, fn in made:
            cap = (f'📄 {name}｜顧問版・面談評估（內部）' if kind == 'consultant'
                   else f'📄 {deliver.client_display_name(meta)}｜客戶版・可轉給用人企業')
            with open(path, 'rb') as fh:
                tg_doc(fh.read(), fn, cap, THREAD_POOL)
    except Exception as ex:
        log(f'⚠️ {name} 報告 PDF 產生／推送失敗（不影響面談）：{ex}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ③ 履歷原檔／連結 ＋ 作品集
    try:
        blob, fn = _resume_attachment(app_id)
        if blob:
            meta['resume']['filename'] = fn
            tg_doc(blob, f'履歷_{name}_{fn}', f'📎 {name} 的履歷原檔', THREAD_POOL)
        else:
            # 只有連結的情況：規格要求把連結直接寫在訊息裡（PDF 裡的按鈕按不下去）
            lines = []
            res = meta.get('resume') or {}
            if res.get('kind') == 'url':
                u = deliver.clean_url(res.get('url'))
                ok = (res.get('note') == 'ok')
                lines.append(f'🔗 {name} 沒有上傳履歷檔，他留的是連結：\n{u}'
                             + ('\n（內容有抓到，阿財面談時看得到）' if ok
                                else f'\n⚠️ 這個連結抓不到內容（{res.get("note") or "尚未解析"}），'
                                     '報告只根據對話內容'))
            else:
                lines.append(f'⚠️ {name} 沒有履歷檔，也沒有履歷連結。')
            for u in meta.get('portfolio_urls') or []:
                lines.append(f'🎨 作品集：{deliver.clean_url(u)}')
            tg('\n\n'.join(lines), THREAD_POOL)
    except Exception as ex:
        log(f'⚠️ {name} 履歷推送失敗（不影響面談）：{ex}')

    # ④ 結語 ＋ 顧問要協助的事項
    msg = deliver.closing_message(data, meta)
    if len(made) < 2:
        msg += f'\n\n⚠️ 這次只產出 {len(made)} 份 PDF，另一份產生失敗，請至後台查看。'
    # 2026-08-19 加：顧問的真實判斷回填。
    # 這是整套 KPI 唯一需要人動手的一步——系統知道阿財判了什麼，但不知道顧問
    # 最後同不同意。累積起來才能算出「阿財說值得轉的人，顧問真的推了幾成」，
    # 那個數字就是對外要拿來證明「AI 沒把人看錯」的憑據。
    # 刻意只做三顆按鈕、不問原因：多問一個欄位就會少一半的人願意按。
    msg += '\n\n👇 讀完報告後按一下，這是系統唯一需要你動手的地方'
    tg_buttons(msg, [
        [{'text': '✅ 我也會推', 'callback_data': f'kpi:ag:{app_id}'},
         {'text': '✋ 我不推', 'callback_data': f'kpi:no:{app_id}'}],
        [{'text': '🤔 還要再看', 'callback_data': f'kpi:hold:{app_id}'}],
    ], THREAD_POOL)


def active_sessions():
    """所有進行中的面談，附上最後一則的角色與時間。"""
    return d1("""
        SELECT a.id, a.name, a.job_slug, a.interview_started_at,
               (SELECT m.role FROM messages m WHERE m.application_id = a.id
                 ORDER BY m.id DESC LIMIT 1) AS last_role,
               (SELECT m.created_at FROM messages m WHERE m.application_id = a.id
                 ORDER BY m.id DESC LIMIT 1) AS last_at,
               (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id) AS n,
               a.start_notified_at, a.job_title
          FROM applications a
         WHERE a.interview_state = 'active'
    """)


def notify_started(rows):
    """候選人進面談室的當下推一則給顧問。

    為什麼要有：原本只有「面談結束、報告好了」會推。但候選人常常不是照約定
    時間進來（約 11:00、實際下午才點連結），顧問要嘛一直手動查、要嘛乾脆不管，
    等報告出來才知道人來過。進場推一則，顧問就能決定要不要在旁邊看著。

    只推一次，靠 applications.start_notified_at 記錄；推播失敗不寫時間，
    下一輪會再試一次（漏推比重複推糟）。
    """
    for r in rows:
        if r.get('start_notified_at') or not r.get('n'):
            continue
        try:
            tg(f'🎙 {r.get("name")} 進面談室了'
               f'\n職缺：{r.get("job_title") or r.get("job_slug")}'
               f'\n開始時間：{r.get("interview_started_at") or "剛剛"}'
               f'\n\n面談跑完會自動把報告推過來，不用盯著。')
            d1(f"UPDATE applications SET start_notified_at = datetime('now','+8 hours') "
               f"WHERE id = {q(r['id'])}")
        except Exception as e:
            log(f'進場通知失敗（下一輪會再試）：{e}')


def pending(rows):
    """正在等阿財回話：最後一則是候選人講的。"""
    return [r for r in rows if r.get('last_role') == 'candidate']


def stale(rows):
    """候選人關掉視窗就走了——多數人本來就是這樣結束的。

    沒有這一段的話，那些面談會永遠停在 active，報告永遠不會產生，
    顧問也永遠不知道談過什麼。逾時自動收尾比等他回來務實得多。
    """
    now = datetime.datetime.now()
    out = []
    for r in rows:
        if r.get('last_role') != 'assistant' or not r.get('last_at'):
            continue        # 還輪到我們回話，不算閒置
        try:
            last = datetime.datetime.strptime(r['last_at'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if (now - last).total_seconds() >= STALE_MIN * 60:
            out.append(r)
    return out


def expired(rows):
    """房間開了滿一小時的强制關閉——不管候選人還在不在、聊到哪。

    跟 stale() 是兩件事：stale 是「他不見了」，這裡是「不管有沒有在聊，
    這是對候選人的時間承諾上限」。阿財被要求盡量在 SOFT_TARGET_MIN（40 分鐘）
    內主動收尾，這裡是萬一它沒抓好節奏時的最後防線。
    """
    now = datetime.datetime.now()
    out = []
    for r in rows:
        if not r.get('interview_started_at'):
            continue
        try:
            started = datetime.datetime.strptime(
                r['interview_started_at'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if (now - started).total_seconds() >= ROOM_HARD_LIMIT_MIN * 60:
            out.append(r)
    return out


# ── 單場面談的靜態上下文快取 ──
# 2026-08-07 顧問指出：context_for() 每一輪都整組重撈，但履歷、職缺資料、
# 初篩結果整場面談根本不會變，只有「對話紀錄」每輪真的有新內容。
# 原本的寫法是每輪拉一個子程序（fetch_application.py），裡面又跑好幾次
# `wrangler d1 execute`——每次呼叫 wrangler CLI 本身就有啟動開銷，
# 實測光這段（不含真正呼叫模型）就要 ~7 秒，一場 40 分鐘的面談會來回十幾輪，
# 等於同樣的履歷、同樣的職缺資料被重複下載十幾次。
#
# 改法：履歷／職缺／初篩只在這場面談的第一輪抓一次，存進記憶體；
# 之後每一輪只重撈真的會變的「對話紀錄」。
# ⚠️ 快取的 key 是 app_id，一場面談對應一個 key，面談結束（finish()）
# 一定要把它清掉，不然常駐程序跑久了記憶體會一直長。
_STATIC_CACHE = {}
_static_lock = threading.Lock()


def _fetch_static(app_id):
    r = subprocess.run(['python3', 'fetch_application.py', app_id],
                       cwd=HERE, capture_output=True, text=True,
                       env=env_with_cf(), timeout=200)
    if r.returncode != 0:
        raise RuntimeError(f'fetch_application 失敗：{(r.stderr or r.stdout)[-200:]}')
    static = json.loads(r.stdout[r.stdout.index('{'):])

    # 初篩已經算過分、也生好「該問哪幾題」了——阿財不用自己重想一遍。
    # 2026-08-05 加：在此之前初篩與面談是兩條互不相干的線，
    # 初篩辛苦生出來的追問沒有人用，等於白做。
    asr = d1(f"SELECT b5_o, b5_c, b5_e, b5_a, b5_n, grit, grit_interest, grit_effort, "
             f"quality_flag FROM assessments WHERE application_id = {q(app_id)} "
             f"ORDER BY id DESC LIMIT 1")
    if asr:
        static['assessment'] = asr[0]

    sc = d1(f"SELECT score, summary, risks, questions, hard_fail FROM screenings "
            f"WHERE application_id = {q(app_id)} ORDER BY id DESC LIMIT 1")
    if sc:
        x = sc[0]
        try:
            static['screening'] = {
                'score': x.get('score'),
                'summary': x.get('summary'),
                'risks': json.loads(x.get('risks') or '[]'),
                'questions': json.loads(x.get('questions') or '[]'),
                'hard_fail': x.get('hard_fail') or '',
            }
        except Exception:
            pass

    # 2026-08-26 加（Jacky 交辦：讓阿財有「記性」）：這位候選人如果之前用過
    # 阿福的履歷健檢，阿財應該要知道，就像真人顧問記得「這個人之前聊過」一樣，
    # 不用每次都從零開始。用 email／電話比對 checkups 表——兩個系統原本完全
    # 不知道對方存在，現在讓阿財單向讀阿福那邊已經問到的東西（不逆向：
    # 阿福不需要知道候選人後來去面談了，那是阿福產品本身的事，範圍不擴大）。
    # 只在 checkups 真的問完（status 到 reported/delivered/closed，代表資料
    # 是可信的）才算數，還在對談中途的不採用——半成品資料比沒有更容易誤導。
    try:
        email = (static.get('application') or {}).get('email')
        phone = (static.get('application') or {}).get('phone')
        ck = None
        if email:
            r = d1(f"SELECT current_title, current_industry, led_headcount, budget_scale, "
                   f"note, created_at FROM checkups WHERE email = {q(email)} "
                   f"AND status IN ('reported','delivered','closed') ORDER BY created_at DESC LIMIT 1")
            if r:
                ck = r[0]
        if not ck and phone:
            r = d1(f"SELECT current_title, current_industry, led_headcount, budget_scale, "
                   f"note, created_at FROM checkups WHERE phone = {q(phone)} "
                   f"AND status IN ('reported','delivered','closed') ORDER BY created_at DESC LIMIT 1")
            if r:
                ck = r[0]
        if ck:
            static['prior_checkup'] = ck
    except Exception:
        pass  # 查不到／查詢失敗都不擋面談，這是加分資訊不是必要資訊

    # 2026-08-19 加：這個職缺的專業題庫（build_expertise.py 事先產好存在 D1）。
    # 這是「讓阿財變成該領域行家」的關鍵——沒有它，阿財只問得出動機、經歷、
    # 穩定度這類通用題，用人主管真正想知道的「他到底會不會做」完全沒碰到。
    # 職缺還沒建題庫就是沒有，面談照常進行（只是少了專業段），不擋流程。
    try:
        ex = d1(f"SELECT domain, topics_json, questions_json, blockers_json FROM job_expertise "
                f"WHERE job_slug = {q(static.get('job', {}).get('slug') or static.get('job_slug'))}")
        if ex:
            static['expertise'] = {
                'domain': ex[0].get('domain'),
                'topics': json.loads(ex[0].get('topics_json') or '[]'),
                'questions': json.loads(ex[0].get('questions_json') or '[]'),
            }
            static['blockers'] = json.loads(ex[0].get('blockers_json') or '[]')
    except Exception as e:
        log(f'⚠️ 專業題庫載入失敗（面談照常，只是少了專業段）：{e}')
    return static


def clear_static_cache(app_id):
    with _static_lock:
        _STATIC_CACHE.pop(app_id, None)


def context_for(app_id):
    """把阿財開口前該知道的全部撈齊——履歷、表單、職缺（快取）＋目前對話（每輪重撈）。"""
    with _static_lock:
        static = _STATIC_CACHE.get(app_id)
    if static is None:
        static = _fetch_static(app_id)
        with _static_lock:
            _STATIC_CACHE[app_id] = static

    ctx = dict(static)  # 淺拷貝，不要讓下面塞 conversation 汙染到快取本體
    ctx['conversation'] = d1(f"SELECT role, content, created_at FROM messages "
                             f"WHERE application_id = {q(app_id)} ORDER BY id ASC LIMIT 200")
    return ctx


SCHEMA_HINT = """
只輸出一段 JSON，不要有其他文字、不要包在程式碼區塊裡：

{"messages": ["第一則", "第二則"], "end": false, "note": "給顧問的一句話（可省略）"}

- messages：要發給候選人的訊息，**每則三行以內**，最多三則。
- end：這場面談是否結束（收尾講完、或候選人明確表示要離開）。
- 面談結束時 end 設 true，並在 messages 放收尾的話。
"""


SKILL_PATH = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/interview-conductor/SKILL.md')


def skill(section, job=None):
    """讀技能規範。

    section='talk' 只取到 Phase 6 為止——「怎麼寫報告」那一大段對談時用不到，
    但它佔了整份的四分之一，每一則都送等於每一則都多等好幾秒。
    section='report' 則整份送，因為產報告時前面的護欄與判準都還要用。

    ⚠️ 2026-08-13 加：「基層／派遣／正職代招（10 題）」跟「中高階（不套用九題預算）」
    這兩章是互斥的——一場面談只會套用其中一種。兩份都照樣送等於逼阿財同時記兩套
    彼此不適用的節奏規則。徐振倫那場（中高階＋外語雙重負載）就是在這麼長的提示詞裡，
    外語驗證那一步被忘掉。這裡用 `job.seniority`（已經是既有的權威欄位，
    line ~850 SEN 那段就在用，不是另外發明一套判斷）決定留哪一半，
    減少阿財同時要顧的規則量，不改變任何規則本身的內容。

    ⚠️ 2026-08-13 再加：「🚨 職缺要求外語」整章同理——原本不管職缺要不要驗外語，
    每一場都照樣送這一整章，讓阿財自己從 must_skills 長文字裡判斷「這次要不要做」。
    徐振倫那場就是這個判斷被漏掉的。現在改成看 `job.interview_language`
    （顧問在 /consultant/jobs 直接設，跟 seniority 同一個做法，不用阿財自己猜）：
    沒設就整章拿掉（面談自然不會憑空冒出語言驗證），有設就整章保留，
    而且會在【職缺與客戶】那段明講要驗證哪個語言（見 build_prompt）。
    """
    t = open(SKILL_PATH, encoding='utf-8').read()
    if section == 'talk':
        cut = t.find('## Phase 7')
        t = t[:cut].rstrip() if cut > 0 else t
        sen = None
        lang = None
        if job:
            sen = job.get('seniority') or ('senior' if job.get('service_line') == 'executive' else 'mid')
            lang = (job.get('interview_language') or '').strip()
        if sen == 'senior':
            start = t.find('### 基層／派遣／正職代招（10 題）')
            end = t.find('### 中高階 executive')
            if start > 0 and end > start:
                t = t[:start] + t[end:]
        elif sen is not None:
            start = t.find('## 🚨 中高階（seniority = senior）')
            end = t.find('## Phase 0')
            if start > 0 and end > start:
                t = t[:start] + t[end:]
        if job is not None and not lang:
            start = t.find('## 🚨 職缺要求外語')
            end = t.find('## 🚨 中高階（seniority = senior）')
            if end <= 0:   # 中高階整章可能已經在上面被拿掉了
                end = t.find('## Phase 0')
            if start > 0 and end > start:
                t = t[:start] + t[end:]
            # Phase 1 開場那段「職缺要求外語就要先預告」是另外加的子章，
            # 不在上面那一段範圍內——沒設語言的職缺留著它會變成叫阿財
            # 開場預告一個根本不存在的外語驗證，一樣要拿掉。
            start2 = t.find('### 🚨 職缺要求外語，開場就要先預告')
            end2 = t.find('## Phase 2')
            if start2 > 0 and end2 > start2:
                t = t[:start2] + t[end2:]
        return t
    return t


def build_prompt(ctx, skill_md):
    app = ctx['application']
    job = ctx.get('job') or {}
    conv = ctx.get('conversation') or []

    lines = []
    lines.append('你是「阿財」，正在跟一位候選人進行即時文字面談。以下是你的作業規範：\n')
    lines.append(skill_md)
    lines.append('\n\n─────────  本場資料  ─────────\n')

    # ── 到職障礙（這個職缺特有的必問項）──
    # 2026-08-19 加。專業題庫回答「他會不會做這份工作」，這一段回答
    # 「他到底能不能來上班」——兩件事都漏過，但漏的原因不同。
    #
    # 為什麼會漏：通用題庫問的動機、經歷、穩定度每個缺都一樣，所以問得到；
    # 但「簽證換雇主要多久」「願不願意跨廠調派」「每月最低開播時數做不做得到」
    # 是各案獨有的，題庫沒有就漏了。而它們偏偏是決定成敗的那一題——
    # 人再好，簽證下不來就是不能到職。
    # ⚠️ 2026-08-19 加：只問「該問候選人」的。
    # 直播主那場的教訓：清單裡混進了「跨境金流／外幣帳戶對接」，阿財拿去問候選人，
    # 結果她反問「台灣人要怎麼申請大陸銀行帳號」——我們答不出來。
    # 問一個自己答不出來的問題，只會讓候選人覺得我們沒搞清楚就在招人。
    # 判準是「他知不知道答案」，不是「重不重要」。
    bl = [b for b in (ctx.get('blockers') or []) if b.get('ask_who') != '用人單位']
    if bl:
        crit = [b for b in bl if b.get('critical')]
        lines.append('\n【到職障礙：這幾題沒問到，這場就是白跑】')
        lines.append('  🚨 這一段比專業題更前面。專業能力再好，這幾條過不了就是到不了職。'
                     '**不要拖到收尾才問**，確認完硬條件就接著問。')
        for i, b in enumerate(bl, 1):
            mark = '🚨 ' if b.get('critical') else ''
            lines.append(f'   {i}. {mark}{b.get("item")}')
            lines.append(f'      問法：{b.get("ask")}')
            if b.get('acceptable'):
                lines.append(f'      什麼樣的回答算過關：{b["acceptable"]}')
            if b.get('why'):
                lines.append(f'      沒問到會：{b["why"]}')
        if crit:
            lines.append(f'  ⚠️ 上面標 🚨 的 {len(crit)} 條是「不過就不用談」的，'
                         '**問到明確答案為止**；對方迴避或給不出時間點，就記下他的原話，'
                         '不要自己幫他圓場、也不要當作問過了。')

    # ── 這位候選人之前用過阿福履歷健檢 ──
    # 2026-08-26 加：就像真人顧問「記得這個人來聊過」一樣，讓阿財知道有這回事，
    # 可以自然銜接、不用重問已經問過的基本背景，但**不是**硬性劇本，話題沒
    # 自然帶到就不用主動提起，更不要讓候選人覺得被監控。
    pck = ctx.get('prior_checkup')
    if pck:
        lines.append(f'\n【這位候選人之前用過我們的履歷健檢服務（{pck.get("created_at", "")[:10]}）】')
        facts = []
        if pck.get('current_title'): facts.append(f'當時職稱：{pck["current_title"]}')
        if pck.get('current_industry'): facts.append(f'當時產業：{pck["current_industry"]}')
        if pck.get('led_headcount'): facts.append(f'帶人規模：{pck["led_headcount"]}')
        if pck.get('budget_scale'): facts.append(f'負責預算／營收規模：{pck["budget_scale"]}')
        if pck.get('note'): facts.append(f'他當時想讓顧問知道的事：{pck["note"]}')
        for f in facts:
            lines.append(f'  · {f}')
        lines.append('  ⚠️ 這是背景參考，不是拿來考他或對質的——如果話題自然聊到相關經歷，'
                     '可以順著這個脈絡往下問、不用重新從頭問一次；如果他這次講的跟當時不一樣，'
                     '不用當面點破，記下差異讓顧問自己判斷。不要主動說「我看到你之前用過健檢」，'
                     '除非他自己先提起。')

    # ── 這個職缺的專業題庫 ──
    # 2026-08-19 加（Jacky 指定：「要讓阿財成為每一個職缺該領域的專家」）。
    #
    # 為什麼一定要有：孙悦那場面談後，報告的「需要你協助的事」寫著
    # 「補測英文向業主報告與供應商議價兩情境」「實測日文口說」——那些是
    # 用人主管真正想知道的事，卻變成顧問的功課。面談等於只做了一半。
    #
    # 題庫由 build_expertise.py 事先產好（會上網查該職務的專業內涵與真實面試題），
    # 存在 D1，這裡直接載入。⚠️ 不在面談當下查資料——候選人在等，
    # 查一次幾十秒，那個延遲會毀掉對話。
    ex = ctx.get('expertise')
    if ex and ex.get('questions'):
        lines.append(f'\n【這個職缺的專業題庫（領域：{ex.get("domain") or "—"}）】')
        lines.append('  🎯 這一段是這場面談的重點。用人主管最想知道的是「他到底會不會做這份工作」，'
                     '而那只有在面談當下問得出來，事後補問等於這場白跑。')
        lines.append('  ⚠️ 怎麼用這些題目：')
        lines.append('   ① **不要照稿念**。用你自己的話問，順著對話接進去；'
                     '候選人剛好自己講到某一題，就順勢追問，不要等輪到那一題才問。')
        lines.append('   ② 每題都附了「真的做過的人會提到什麼」與「沒做過的人會怎麼含糊帶過」。'
                     '對到含糊那一欄就用附的追問往下挖一層，不要放過。')
        lines.append('   ③ **你不是這個領域的專家，不要判斷答案對不對。** '
                     '你的工作是問對問題、把他的原話記下來、標出哪裡答得含糊，'
                     '專業對錯留給顧問跟用人主管判斷。'
                     '⚠️ 不要對候選人說「這個答案不對」或表現出評價。')
        lines.append('   ④ 標「情境」或「外語」的題目要當場請他做，不是問他會不會。')
        lines.append('   ⑤ 這些題目不能取代原本的必問項（動機、薪資、到職日、硬條件）——'
                     '那些照樣要問完，專業題是加上去的。')
        for i, x in enumerate(ex['questions'], 1):
            lines.append(f'   {i}. [{x.get("kind") or "經驗"}｜{x.get("topic") or ""}] {x.get("q")}')
            if x.get('why'):
                lines.append(f'      想確認：{x["why"]}')
            if x.get('good_signs'):
                lines.append(f'      真做過會提到：{"；".join(x["good_signs"][:3])}')
            if x.get('red_flags'):
                lines.append(f'      含糊的訊號：{"；".join(x["red_flags"][:3])}')
            if x.get('followup'):
                lines.append(f'      含糊就追問：{x["followup"]}')

    # 初篩已經算過分、也生好該問哪幾題了。放在最前面，讓阿財開口前就知道要往哪挖。
    # 2026-08-05 加：在此之前初篩與面談互不相干，初篩生的追問沒人用，等於白做。
    sc = ctx.get('screening')
    if sc:
        lines.append('【初篩結果（AI 事先看過履歷）】')
        lines.append(f'  匹配 {sc.get("score")}／100：{sc.get("summary") or ""}')
        if sc.get('hard_fail'):
            lines.append(f'  🚨 硬性不符：{sc["hard_fail"]}')
        if sc.get('risks'):
            lines.append('  要釐清的：' + '；'.join(sc['risks'][:4]))
        if sc.get('questions'):
            lines.append('  ⚠️ 這幾題初篩已經想好了，這場一定要問到（可以改成你自己的講法）：')
            for i, qq in enumerate(sc['questions'][:5], 1):
                lines.append(f'    {i}. {qq}')
        lines.append('  ⚠️ 分數不要跟候選人講，也不要暗示他被評分過——'
                     '那是給顧問看的內部資訊。')
        lines.append('')

    lines.append('【應徵表單】')
    for k in ('name', 'job_title', 'expected_salary', 'available_date',
              'location_ok', 'note', 'created_at'):
        if app.get(k):
            lines.append(f'  {k}：{app[k]}')

    if app.get('disc_primary'):
        lines.append(
            f"  DISC 傾向：{app['disc_primary']}"
            f"（D{app.get('disc_d', 0)} I{app.get('disc_i', 0)}"
            f" S{app.get('disc_s', 0)} C{app.get('disc_c', 0)}，滿分 20）"
            "　— 這是表單填寫時測的，只用來對照面談中的實際表現，不要在面談中提起或解釋。")

    # ── 人格測驗（Big Five ＋ Grit）──
    # 2026-08-07 加。用途是**對照**，不是評分：看他自己填的樣子跟他講話的樣子是不是同一個人。
    #
    # ⚠️ 這不是測謊。人格測驗測不出說謊，不要往那個方向解讀。
    #    能做的只有「自述與行為證據不一致」——那是**要追問的線索**，不是「他在騙人」的證據。
    if ctx.get('assessment'):
        a = ctx['assessment']
        def band(v):
            if v is None: return '—'
            return '偏高' if v >= 3.8 else ('偏低' if v <= 2.5 else '中等')
        lines.append('\n【人格測驗（他填表時做的，滿分 5）】')
        lines.append(f"  盡責性 {a.get('b5_c')}（{band(a.get('b5_c'))}）"
                     '　＝做事有沒有條理、收不收得了尾')
        lines.append(f"  情緒起伏 {a.get('b5_n')}（{band(a.get('b5_n'))}）"
                     '　＝分數高代表容易心煩，跟抗壓有關')
        lines.append(f"  開放性 {a.get('b5_o')}（{band(a.get('b5_o'))}）　＝學新東西的意願")
        lines.append(f"  外向性 {a.get('b5_e')}（{band(a.get('b5_e'))}）"
                     f"　親和性 {a.get('b5_a')}（{band(a.get('b5_a'))}）")
        lines.append(f"  恆毅力 {a.get('grit')}（{band(a.get('grit'))}）"
                     f"　興趣持續 {a.get('grit_interest')}／努力持續 {a.get('grit_effort')}"
                     '　＝遇到挫折會不會走、長期做得下去嗎')
        if a.get('quality_flag'):
            lines.append(f"  ⚠️ 作答品質警訊：{a['quality_flag']}"
                         '（straight_line＝全選同一格、too_fast＝快到不可能讀完題目）'
                         '——**這份分數不可靠，不要拿來當判斷依據**，但也不要因此質疑他的人格，'
                         '那只代表他填問卷時不認真。')
        lines.append('  ⚠️ 怎麼用這幾個數字：')
        lines.append('     - **不要在面談中提起測驗結果、不要解釋分數、不要問他為什麼這樣填。**'
                     '那會讓他開始揣測「正確答案」，後面講的話就不能用了。')
        lines.append('     - 拿它當**追問的方向**。例如盡責性偏高但講不出具體把事情做完的例子，'
                     '就多問一個實際案例；恆毅力偏低就多確認他過去換工作的節奏與原因。')
        lines.append('     - **不一致不等於說謊。** 可能是他不會描述自己、也可能是測驗沒測準。'
                     '報告裡只寫「自述與行為證據不一致，建議顧問確認」，'
                     '**不准寫「他說謊」「他造假」這類判斷。**')

    lines.append('\n【職缺與客戶（內部資料，可依規範對候選人說明）】')
    if job:
        # 🚨 服務線一定要送進去，而且要放在最前面。
        #    2026-08-11 呂書帆（主管特助・中高階）中途退出面談，說
        #    「問的都有履歷上面寫過的問題很沒效率」，改成直接跟顧問談。
        #    事後查：這個缺的 service_line 早就是 executive，但**這個欄位從來沒進過 prompt**，
        #    所以阿財對一位 41 歲、九年日本旅宿、N1、帶過團隊的人選，
        #    跑了跟基層一模一樣的流程（請你自我介紹、慢慢聊 30-40 分鐘）。
        #    中高階人選同時也在評估我們——流程沒效率，他退出是正常反應，不是他難搞。
        if job.get('service_line'):
            SL = {'executive': '中高階獵才', 'direct': '正職代招', 'dispatch': '人力派遣'}
            lines.append(f'  🎯 service_line：{job["service_line"]}'
                         f'（{SL.get(job["service_line"], job["service_line"])}）')
        # 🚨 職級跟服務線是兩件事，不要綁在一起判斷。
        #    2026-08-11 Jacky 指出：**派遣也會有中高階職缺**。
        #    如果用 service_line == 'executive' 當短版的開關，
        #    一個派遣的廠長缺就會被當成基層跑 30 分鐘的標準流程，重蹈呂書帆那一場。
        #    所以另外用 seniority 欄位，由顧問在 /consultant/jobs 自己設。
        sen = job.get('seniority') or ('senior' if job.get('service_line') == 'executive' else 'mid')
        SEN = {'senior': '中高階', 'mid': '一般', 'junior': '基層／無經驗可'}
        lines.append(f'  🎯 seniority：{sen}（{SEN.get(sen, sen)}）')
        # 🚨 外語驗證要不要做，不再讓阿財自己從 must_skills 長文字判斷——
        #    2026-08-13 加，跟 seniority 同一個做法：顧問在後台明講，這裡直接下指令。
        lang = (job.get('interview_language') or '').strip()
        if lang:
            lines.append(f'  🚨 **這個職缺要驗證{lang}——開場說明時就要預告，'
                         f'照 SKILL.md「職缺要求外語」那一整章的步驟①–④執行，'
                         f'收尾前務必檢查有沒有真的做過。**')
        if sen == 'senior':
            # ⚠️ 2026-08-13 修：這裡原本寫「10 分鐘、最多 5 題」，
            # 但 SKILL.md 那一章 2026-08-11 就已經改成「20-30 分鐘、沒有題數上限」
            # （呂書帆、尹緯正兩場事故換來的），這裡沒跟著改，等於**同一個提示詞
            # 裡塞了兩個互相矛盾的時長／題數指示**——SKILL.md 說不設上限，
            # 這裡卻明講「最多 5 題」。徐振倫這場感覺被趕、缺乏深挖，這也是原因之一。
            lines.append('  🚨 **這是中高階職缺，一律走 SKILL.md 的「中高階」那一章**'
                         '（深挖但不重複履歷，20–30 分鐘，沒有題數上限），不要跑標準流程。**')
        for k in ('title', 'client_name', 'client_intro', 'team_size', 'interview_rounds',
                  'interview_who', 'has_test', 'onboard_by', 'must_skills',
                  'salary_min', 'salary_max', 'locations', 'employment', 'faq_notes'):
            if job.get(k):
                lines.append(f'  {k}：{job[k]}')

        # ── 客戶身分保密 ──
        # 2026-08-07 顧問指示：BIM 工程師這個缺的客戶名稱與廠區地名是客戶隱私，
        # 已經把 client_name／client_intro／faq_notes 改成不指名的講法，
        # 但候選人很可能直接問「是哪一間公司」「是不是美光」——
        # 這條規則不寫清楚，阿財會照著「盡量回答候選人問題」的預設去猜或補完，等於還是講出去。
        if job.get('confidential_client'):
            lines.append('  🔒 這個職缺的客戶名稱與廠區地名不可以講。'
                         '候選人問公司名稱、問是不是某間知名廠商（包含用猜的、用「是不是 XX」套話）：'
                         '一律回「這部分顧問錄取後會說明，面談這階段先聚焦在您的經歷跟這個職缺合不合適」。'
                         '不要迴避到讓對方覺得可疑，但絕對不要證實或否認任何具體公司名稱。')

        # ── 這個職缺怎麼推銷 ──
        # 2026-08-07 顧問指示：BIM 這個缺不要再拿「轉正」當話術賣點。
        # 跟保密規則不一樣的地方：轉正這件事**不是不能提**，是不能由你主動當賣點推銷；
        # 候選人自己問還是要誠實回答，不能為了促成應徵而迴避風險揭露。
        if job.get('talking_points'):
            lines.append(f'  💬 這個缺的推銷方式：{job["talking_points"]}')

        # ── 薪資怎麼講 ──
        # 2026-08-07：阿財對王雁群說「這個職缺目前開的是 40K 起，您期望 60K，差了不少」，
        # 拿一個他以為是行情的數字去壓對方的期望。但那個 40000 根本不是客戶開的價——
        # 104 上是「待遇面議（經常性薪資達 4 萬元或以上）」。
        #
        # 4 萬這個數字是**就業服務法的揭露門檻**：月薪未達 4 萬的職缺必須公開薪資範圍，
        # 只有 4 萬以上才可以寫「面議」。所以「4 萬以上」的意思是
        # 「這個缺至少 4 萬，上限沒說」，不是「這個缺開 4 萬」。
        # 阿財把法定下限讀成客戶的出價，方向剛好相反——
        # 這會讓真正有行情的候選人以為自己開太高而退場。
        if job.get('salary_note'):
            lines.append(f'  💰 薪資的正式說法（要照這個講，不要自己換算成數字）：{job["salary_note"]}')
        lines.append('  ⚠️ 薪資規則：')
        lines.append('     - salary_min 是「至少」，不是「開這個價」。'
                     '只有 salary_max 也有值的時候，才可以講成一個區間。')
        lines.append('     - 只有 salary_min、沒有 salary_max 時，一律講「X 萬以上，實際依經驗面談決定」，'
                     '**絕對不要說「開的是 X 萬」或「起薪 X 萬」**。')
        lines.append('     - 候選人期望比較高時，不要說「差了不少」這種話去壓他。'
                     '改成問清楚他的期望怎麼來的、有沒有彈性，把數字跟理由記下來給顧問判斷。'
                     '你不是談判的人，你是收集資訊的人。')

        if job.get('notes'):
            lines.append(f'  （顧問備註，不要對候選人講）：{job["notes"]}')
    else:
        lines.append('  （這個職缺在 jobs 表裡沒有資料，公司相關問題一律說會由顧問說明）')

    # 2026-08-20 加：候選人在應徵表單填的社群連結。
    # ⚠️ 阿財**看不到這些連結的內容**——它沒有瀏覽器，平台也擋外部抓取。
    #    所以規則是「知道他有給，但不准假裝看過」。
    #    Jacky 的原則：人選提供的東西都要問，但**要先看過再問**。
    #    看不到就不能問「你 IG 都發什麼」——那是我們自己該先做的功課，
    #    問出口只會讓候選人覺得我們連他給的連結都沒點開。
    #    改成問「看連結看不出來、只有他知道」的事（頻率、規劃、遇過什麼狀況）。
    social = ctx.get('application', {}).get('social_links')
    if social:
        try:
            sd = json.loads(social) if isinstance(social, str) else social
        except Exception:
            sd = None
        if sd:
            NAME = {'instagram': 'Instagram', 'tiktok': 'TikTok', 'facebook': 'Facebook',
                    'threads': 'Threads', 'youtube': 'YouTube', 'other': '其他平台'}
            lines.append('\n【他在應徵表單提供的社群連結】')
            for k, v in sd.items():
                lines.append(f'  {NAME.get(k, k)}：{v}')
            lines.append('  🚨 你**沒有看過**這些連結的內容，顧問會自己點開看。')
            lines.append('  ⚠️ 所以**不准問**「你 IG 平常都發什麼」「你的內容風格是什麼」'
                         '這種點開就知道的問題——那是我們該自己做的功課，'
                         '問出口等於告訴他我們連他給的連結都沒看。')
            lines.append('  ✅ 要問的是**看連結看不出來、只有他本人知道的事**，例如：'
                         '目前一週固定經營幾天、花多少時間、'
                         '有沒有接過合作或業配、後續想往哪個方向做、'
                         '曾經遇過最難處理的狀況是什麼。')
            # 2026-08-25 加：這幾題是「這職缺本來就該收集的資訊」，
            # 對**每一位**提供社群連結的候選人都要問，跟年齡、外貌無關，
            # 不分年齡都問一樣的問題、用一樣的標準——不要因為某個人選看起來
            # 比較年輕或比較資深就跳過或加問。
            lines.append('  ✅ 另外這幾題**每個人都要問**（不分年齡、不分外表，一律問一樣的）：'
                         '有沒有特別擅長的才藝或表演項目（唱歌、跳舞、樂器、口才、遊戲實況等，'
                         '不限直播本業）、過去經營下來大概的業績或流水表現（月均訂單量、'
                         '业配報價、粉絲互動數據等他方便講的具體數字）、'
                         '有沒有品牌方或平台主動找過他合作。'
                         '這些是了解他商業價值的正常問題，跟履歷上的工作經歷一樣，'
                         '一視同仁地問，不要用不同的語氣或當成年齡的替代篩選。')

    lines.append('\n【履歷】')
    if ctx.get('resume_readable'):
        lines.append(f'  來源：{ctx.get("resume_source")}')
        lines.append(ctx['resume_text'][:12000])
        # ⚠️ 2026-08-19 加：履歷時間軸的斷點要自己找出來。
        # 2026-08-19 回頭看十份報告，四位人選的履歷空窗都是**報告產出後**才被發現的
        # （吳秉洋 7 個月、鍾欣修兩段、周承緯 3 年多）——顧問拿到報告才知道有這件事，
        # 等於要再打一次電話。空窗是履歷上算得出來的客觀事實，不該等到事後才問。
        #
        # 為什麼不用程式算：履歷格式差太多（2020/01-2022/03、2020年1月～、只寫年份、
        # 甚至沒寫），正則抓出來的斷點錯誤率高，反而製造假的必問項。
        # 讀非結構化文字本來就是模型擅長的事，交給它做、但要求它先做。
        lines.append(
            '\n  ⚠️ 開口之前先做這件事：把上面履歷的工作時間軸依序排出來，'
            '找出**中斷超過 3 個月**的區間，還有**待不到一年就離開**的段落。'
            '這些是這場的必問項，跟硬條件一樣重要。\n'
            '  問法要中性——「這段時間您主要在做什麼？」「當時是什麼原因離開的？」，'
            '不要用審問的語氣，也不要預設空窗是壞事（進修、照顧家人、疫情、'
            '接案都很常見）。重點是把事實問出來寫進報告，讓顧問自己判斷。\n'
            '  ⚠️ 履歷沒寫年月、或寫得不完整而算不出來的，就直接請他口頭把'
            '時間順序講一次，不要跳過。')
    else:
        lines.append('  ⚠️ 讀不到履歷內容：' + str(ctx.get('resume_note') or '未提供'))
        lines.append('  ⚠️ 絕對不要說「您的履歷我看過了」。改成請對方口頭介紹經歷。')

    lines.append('\n【目前對話】')
    if not conv or all(m['content'] == '（候選人已進入面談室）' for m in conv):
        lines.append('  （還沒開始。這是開場，請你先開口。）')
    else:
        for m in conv:
            if m['content'] == '（候選人已進入面談室）':
                continue
            who = '你' if m['role'] == 'assistant' else '候選人'
            lines.append(f'  {who}：{m["content"]}')

    lines.append(f'\n  （已經來回 {len(conv)} 則。超過 {MAX_TURNS} 則就要收尾。）')

    started, mins = app.get('interview_started_at'), None
    if started:
        try:
            mins = int((datetime.datetime.now()
                        - datetime.datetime.strptime(started, '%Y-%m-%d %H:%M:%S')).total_seconds() / 60)
            lines.append(
                f'  （面談已進行約 {mins} 分鐘。目標是 {SOFT_TARGET_MIN} 分鐘內主動收尾——'
                f'快到時就開始往 Phase 5/6 收，不要等被硬性中斷。'
                f'房間滿 {ROOM_HARD_LIMIT_MIN} 分鐘會被系統強制關閉，不要拖到那時候。）')
        except Exception:
            pass
    # ── 讀不到履歷時的收尾交代 ──
    # 2026-08-07 加。顧問的決定：**沒履歷不擋面談，照樣談完**，
    # 但不能就這樣讓人走掉——談完了我們手上還是只有一份逐字稿，
    # 顧問要推件給客戶時沒有東西可以附。
    # 所以改成在快結束時把「補履歷」當成必辦事項，並給 LINE OA 讓他接得上真人顧問。
    # 放在 prompt 最後面，因為越後面的指令越不會被前面那一大段規範蓋過去。
    if not ctx.get('resume_readable'):
        near_end = (len(conv) >= MAX_TURNS - 12) or (mins is not None and mins >= SOFT_TARGET_MIN - 12)
        lines.append('\n─────────  這場沒有履歷，收尾前一定要做的事  ─────────')
        lines.append(f'  這位候選人沒有可讀的履歷（原因：{ctx.get("resume_note") or "未提供"}）。')
        lines.append('  談完之後顧問手上只會有這份逐字稿，沒有東西可以送件給客戶。')
        if near_end:
            lines.append('  ⚠️ 現在已經接近尾聲，**收尾的時候一定要講這兩件事**（用你自己的話，不要照唸）：')
            lines.append('     1. 請他面談結束後補一份履歷——PDF、Word 或作品集連結都可以。')
            lines.append(f'     2. 給他 LINE 官方帳號 {LINE_OA_URL}，'
                         '說明加了之後可以直接跟負責這個案子的顧問聯繫，'
                         '履歷也可以直接傳到那裡，有問題也在那邊問。')
            lines.append('  講的時候要說明為什麼需要：顧問要把他推薦給客戶時，'
                         '客戶端一定會要書面資料，沒有履歷這一步就卡住。')
            lines.append('  ⚠️ 不要把這件事講成「你資料沒交」的責備語氣，'
                         '這是我們在幫他把後面的路鋪好。')
        else:
            lines.append('  現在還在中段，先專心把經歷問清楚，'
                         '補履歷與 LINE 的事等接近收尾時再講，不要現在打斷節奏。')

    lines.append('\n─────────  輸出格式  ─────────')
    lines.append(SCHEMA_HINT)
    return '\n'.join(lines)


def sanitize(t):
    """清掉控制字元。

    ⚠️ 這不是潔癖，是必要的：prompt 是用命令列參數傳給 claude 的，
    參數裡只要有一個 \x00，subprocess 就直接丟 "embedded null byte"，整場面談掛掉。
    2026-07-30 實際發生過——前端 pdf.js 對某些 PDF 字型抽出 1756 個空位元組。
    """
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)


def run_claude(prompt):
    r = subprocess.run(
        ['claude', '-p', sanitize(prompt), '--model', TALK_MODEL,
         *NO_TOOLS, '--output-format', 'text'],
        capture_output=True, text=True, env=env_with_cf(), timeout=CLAUDE_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    out = r.stdout.strip()
    # 模型偶爾還是會包程式碼區塊或前後多講一句，抓最外層的 JSON 就好
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'回覆裡沒有 JSON：{out[:200]}')
    return json.loads(out[i:j + 1])


def snap_signals(app_id, at):
    """阿財每發一次話就照一張行為快照。

    為什麼要逐輪存：engagement 只有整場總計（離開幾次、貼上幾次），
    但外語驗證真正要問的是「**在那一題**他有沒有切出去查翻譯」。
    總計答不了這個問題——他可能是在別題離開的。
    有了逐輪快照，用相鄰兩輪的差值就知道那一題發生了什麼。

    ⚠️ 存不進去不能影響面談。這只是輔助訊號，不是流程的一環。
    """
    try:
        eg = d1(f"SELECT * FROM engagement WHERE application_id={q(app_id)}")
        e = eg[0] if eg else {}
        n = d1(f"SELECT COUNT(*) c FROM turn_signals WHERE application_id={q(app_id)}")
        seq = int((n[0]['c'] if n else 0)) + 1
        d1(f"INSERT OR REPLACE INTO turn_signals "
           f"(application_id, seq, at, away_count, away_seconds, paste_count, paste_chars, active_seconds) "
           f"VALUES ({q(app_id)}, {seq}, {q(at)}, "
           f"{int(e.get('away_count') or 0)}, {int(e.get('away_seconds') or 0)}, "
           f"{int(e.get('paste_count') or 0)}, {int(e.get('paste_chars') or 0)}, "
           f"{int(e.get('active_seconds') or 0)})")
    except Exception as ex:
        log(f'（行為快照沒存成，不影響面談：{ex}）')


def answer_timing(app_id):
    """每一則回答花了多久、期間有沒有切走或貼上。

    ⚠️ 這一段的用途很窄：**驗外語那一題**。
    2026-08-11 Jacky 問「怎麼防止人選去 Google 翻譯」——
    文字面談擋不住翻譯，但可以讓它留下痕跡：
    翻譯來回一定要切出視窗、要花時間、常常是貼上的。

    ⚠️ 只呈現事實，不下判斷。有人切出去是查自己的舊資料，那是認真不是作弊。
    """
    msgs = d1(f"SELECT role, content, created_at FROM messages "
              f"WHERE application_id={q(app_id)} ORDER BY id ASC")
    snaps = d1(f"SELECT seq, at, away_count, away_seconds, paste_count, paste_chars "
               f"FROM turn_signals WHERE application_id={q(app_id)} ORDER BY seq ASC")
    if not msgs:
        return ''
    fmt = '%Y-%m-%d %H:%M:%S'

    def t(s):
        try:
            return datetime.datetime.strptime(s, fmt)
        except Exception:
            return None

    out, last_q = [], None
    for m in msgs:
        if m['role'] == 'assistant':
            last_q = m
            continue
        if not last_q or '候選人已進入' in (m['content'] or ''):
            continue
        a, b = t(last_q['created_at']), t(m['created_at'])
        if not a or not b:
            continue
        sec = int((b - a).total_seconds())
        # 這段時間內的行為差值：找出落在 [問, 答] 之間的快照
        before = [s for s in snaps if t(s['at']) and t(s['at']) <= a]
        after = [s for s in snaps if t(s['at']) and t(s['at']) >= b]
        d = ''
        if before and after:
            x, y = before[-1], after[0]
            da = int(y['away_count'] or 0) - int(x['away_count'] or 0)
            ds = int(y['away_seconds'] or 0) - int(x['away_seconds'] or 0)
            dp = int(y['paste_count'] or 0) - int(x['paste_count'] or 0)
            bits = []
            if da:
                bits.append(f'期間切走 {da} 次共 {ds} 秒')
            if dp:
                bits.append(f'貼上 {dp} 次')
            d = ('　' + '、'.join(bits)) if bits else '　期間沒有切走也沒有貼上'
        out.append(f'  「{(last_q["content"] or "")[:34]}…」→ 回覆花了 {sec} 秒'
                   f'（{len(m["content"] or "")} 字）{d}')
        last_q = None
    if not out:
        return ''
    return ('【每一題的回覆時間與行為（只呈現事實，不下判斷）】\n' + '\n'.join(out)
            + '\n  ⚠️ 判讀外語那一題時才特別看這一段：'
              '翻譯來回一定要切出視窗、要花時間，而且常常是貼上的。'
              '其他題目切走可能只是去查自己的舊資料，那是認真不是作弊。')


def engagement_block(app_id):
    """算這場面談的投入度，回傳一段給報告用的文字（沒資料就回空字串）。

    為什麼要有：2026-08-06 顧問說「就像打電話過去、但他在另一端不知道在幹嘛，
    有沒有認真很難判斷」。用手上 7 筆真實資料驗過兩個訊號：
      ✅ 候選人平均回覆字數——7 個人 7 個準（≥37 字全部活著、≤13 字全部掉了）
      ❌ 回覆速度——分不出來（掉的人 188/252 秒，活著的呂皓宇 222 秒夾在中間；
         而且每個人平均都要 2–4 分鐘，代表大家都在一邊做別的事）
    所以字數進報告，速度不進。切走次數則是 2026-08-06 才開始收，早期面談沒有。

    ⚠️ 只呈現事實，**不下「這個人不認真」的結論**，也不給分。
    有人開另一個視窗查資料再回答，那是認真不是分心——判斷交給顧問。
    """
    rows = d1(f"SELECT role, LENGTH(content) L FROM messages WHERE application_id={q(app_id)}")
    cand = [r['L'] for r in rows if r['role'] == 'candidate']
    if not cand:
        return ''
    avg, mx = round(sum(cand) / len(cand)), max(cand)
    lines = ['【投入度（系統量測，僅供參考）】',
             f'  候選人回覆 {len(cand)} 則，平均 {avg} 字，最長一則 {mx} 字。']
    # 目前手上的對照組：活著的人平均 37–87 字、最長 97–253 字；
    # 掉的人平均 11–13 字、最長沒有超過 21 字。樣本只有 7 筆，寫進報告時要標明。
    if avg <= 15 or mx <= 25:
        lines.append('  ⚠️ 這個回覆長度明顯偏短。目前 7 筆歷史資料裡，平均 ≤13 字的三位'
                     '後續都沒有進展（原因是「無意願」或「不接受條件」）——'
                     '**樣本很小，只能當提醒，不能當結論。**')
    eg = d1(f"SELECT * FROM engagement WHERE application_id={q(app_id)}")
    if eg:
        e = eg[0]
        if e['away_count']:
            m2, s2 = divmod(int(e['away_seconds']), 60)
            lm, ls = divmod(int(e['longest_away']), 60)
            lines.append(f"  面談期間切換到其他分頁／視窗 {e['away_count']} 次，"
                         f"累計離開 {m2} 分 {s2} 秒，最久一次 {lm} 分 {ls} 秒。")
        else:
            lines.append('  面談期間沒有切換到其他分頁。')
        if e['paste_count']:
            lines.append(f"  有 {e['paste_count']} 次貼上，共 {e['paste_chars']} 字"
                         f"——可能是準備好的稿或從別處複製，值得在複試時追問細節。")
    else:
        lines.append('  （切換分頁的紀錄從 2026-08-06 才開始收，這場面談可能沒有。）')
    lines.append('  ⚠️ 這幾個數字只是行為事實，**不要據此判定人選不認真**——'
                 '有人開另一個視窗查資料再回答。請和逐字稿內容一起看。')
    return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────
# 結構化報告（reports.content_json）
#
# 為什麼要有：顧問後台要做的視覺化報告、以及之後要產的「客戶版」，
# 都需要逐欄取用。純文字的 content_md 拆不可靠——標題會被模型微調、
# 條列符號會變、缺欄位時整段消失。所以另外存一份 JSON。
#
# ⚠️ **content_md 是主的，JSON 是附的。**
# 後台現在讀的是 content_md，這裡任何失敗都不可以影響它。
# 做法固定是「兩段式」：先照原本的流程產純文字（那段一個字都沒動），
# 成功之後再拿那份文字去換 JSON。不做「一次要模型吐兩段」——
# 那會讓 JSON 的格式壓力回頭影響純文字的品質，而純文字才是顧問在看的。
REPORT_JSON_SPEC = r'''
{
  "verdict": "值得轉給顧問|資訊不足建議補問|硬條件不符|待顧問判斷",
  "basics": {
    "residence": "居住地，縣市＋區。履歷或表單沒寫就填 null",
    "commute_note": "從居住地到職缺地點的距離／通勤可行性，一句話。算不出來就 null",
    "age": "年齡，例如「41 歲（1985/2 生）」。⚠️ 只抄履歷上寫的，不准從畢業年份推算，沒寫填 null",
    "gender": "性別，只抄履歷／表單上他自己填的，沒寫填 null",
    "education": "最高學歷（學校＋科系），沒寫填 null",
    "languages": "語言能力，含證照等級，沒寫填 null",
    "certificates": "證照／駕照，沒寫填 null",
    "military": "兵役，履歷有寫才填，沒有填 null",
    "source": "basics 這一區的來源，固定寫「履歷／應徵表單，非面談詢問」"
  },
  "one_liner": "一句話定位，30字內",
  "top_selling_point": "一句",
  "top_risk": "一句",
  "summary": "面談結果總結，150字內敘事",
  "motivation": {
    "why_leaving": "", "why_this_role": "", "salary_gap": "",
    "notice_period": "", "other_offers": "", "blockers": ""
  },
  "values": ["標籤", "..."],
  "values_basis": "推論依據一句",
  "work_history": [
    {"employer": "公司名或（未具名）", "role": "", "duration": "",
     "source": "履歷|口述|未提供", "nature": "雇主|接案客戶",
     "note": "面談中針對這段經歷實際問到、確認到的內容，沒問到就空字串。⚠️ 開頭要依內容加對應標籤：離職原因該段是本人目前這份工作，且面談問到想動機時填「離職原因：...」；這段之前有空窗且面談問到原因時填「入職前空窗說明：...」；面談中確認/驗證到這段工作實際做過什麼（技能、經驗、案例）時填「過往經歷與技能驗證：...」；都不屬於這幾類但面談有問到相關內容，才不加標籤直接寫。沒問到的不要編。"}
  ],
  "resume_vs_spoken": [
    {"item": "年資", "resume_says": "", "candidate_says": "",
     "explanation": "他的說明，沒問到就空字串", "status": "已確認|待釐清"}
  ],
  "hard_conditions": [
    {"item": "", "verdict": "符合|不符|待確認", "detail": "",
     "evidence_source": "履歷|應徵表單|他親口說|未確認"}
  ],
  "blocker_findings": [
    {"item": "到職障礙名稱（照題目給的）",
     "answer": "他實際怎麼回答，用他的說法",
     "status": "沒問題|有風險|還沒問到|他答不出來",
     "evidence": "他的原話一句"}
  ],
  "expertise_findings": [
    {"topic": "考點名稱", "asked": "你實際問了什麼",
     "answered": "他回答的重點，用他自己的說法整理，不要美化",
     "evidence": "他的原話直接引用一句（最能代表他真實程度的那句）",
     "depth": "具體|籠統|未談到",
     "note": "只寫可查證的事實，例如「說得出專案規模與工具」「講不出遇過的問題」"}
  ],
  "language_verification": {
    "required_language": "職缺要求驗證的語言，例如「日文」；職缺沒有要求就填 null",
    "tested": true,
    "verdict": "通過|不通過|未測試",
    "how": "口說|文字|null",
    "evidence": "他當場用該語言回答的原話一句，沒測就空字串"
  },
  "fit_scores": {
    "dimensions": [
      {"name": "硬條件符合度", "score": 0, "evidence": "", "note": ""},
      {"name": "相關經驗深度", "score": 0, "evidence": "", "note": ""},
      {"name": "案例具體度",   "score": 0, "evidence": "", "note": ""},
      {"name": "動機明確度",   "score": 0, "evidence": "", "note": ""},
      {"name": "溝通清晰度",   "score": 0, "evidence": "", "note": ""},
      {"name": "工作穩定度",   "score": 0, "evidence": "", "note": ""}
    ]
  },
  "observations": {
    "assessment": [{"trait": "S 穩定型", "score": 9,
                    "verified": "面談印證|未觀察到|不一致", "evidence": ""}],
    "communication_style": "",
    "engagement": "一句人話，例如「全程專注，沒有中途離開」",
    "axis_notes": [
      "內斂↔外向這一軸的判斷依據，一句話，引用他實際的說話方式或原話，不要用形容詞空泛帶過",
      "重執行細節↔重整體策略這一軸的判斷依據，一句話",
      "需要明確指示↔自主推進這一軸的判斷依據，一句話",
      "條件導向↔認同導向這一軸的判斷依據，一句話（通常從離職動機或選擇這份工作的原因來判斷）"
    ]
  },
  "candidate_questions": [{"question": "", "answered": true, "note": ""}],
  "for_client": {
    "reasons": ["三點"], "risks_to_disclose": ["要主動揭露的"],
    "suggested_questions": ["建議客戶面試追問"],
    "job_fit_pros": ["針對『這個職缺』的優勢，2~3點，每點一句話，要跟職缺內容掛勾，不是泛泛的優點"],
    "job_fit_cons": ["針對『這個職缺』需要留意的地方，2~3點，每點一句話，同樣要跟職缺內容掛勾"],
    "trait_one_liner": "把特質與風格四軸的整體結論濃縮成一句話，講清楚這個人適合怎樣的工作方式、跟這個職缺搭不搭"
  },
  "consultant_followups": ["顧問還要自己追問的事，有幾件寫幾件"]
}
'''

# 這幾條是欄位語意上的硬規則，不是格式偏好。每一條後面都有踩過的坑。
REPORT_JSON_RULES = (
    '⚠️ 硬規則，每一條都要遵守：\n'
    '1. `resume_vs_spoken` **不可以是空陣列**。沒有任何落差時也要輸出一筆：\n'
    '   {"item":"整體","resume_says":"","candidate_says":"",'
    '"explanation":"履歷與口述一致","status":"已確認"}\n'
    '   空陣列會被顧問誤讀成「這場沒有做比對」。\n'
    '2. **不准在任何欄位寫「他說謊」「造假」「灌水」「誇大」這類判斷。**\n'
    '   只記雙方各自的說法，判斷是顧問的事，不是你的。\n'
    '3. `hard_conditions` 每一筆的 `evidence_source` 一定要填，不可留空——\n'
    '   顧問要知道每一條依據是哪裡來的。真的不知道就填「未確認」。\n'
    '4. 就業服務法第 5 條的保護特徵（年齡、性別、婚姻、生育、國籍…）：\n'
    '   **年齡、性別、居住地寫進 `basics`（2026-09-04 Jacky 決定客戶版也要有性別），\n'
    '   其餘（婚姻、生育、國籍…）一律不要出現在任何欄位。**\n'
    '   ⚠️ `basics` 只抄履歷或應徵表單上他自己填的，**不准從畢業年份推算年齡、不准用姓名猜性別**，\n'
    '   沒寫就填 null。這一區只做事實揭露，**不准出現在任何判斷句裡**——\n'
    '   不准寫「年齡偏大可能不適合」這種。\n'
    '   純文字報告裡若有「需顧問評估的客戶條件」那一段，該段的內容不要搬進 JSON。\n'
    '5. 報告裡沒有的資訊就留空字串或空陣列，**不要自己補**。\n'
    '6. ⚠️ `basics` 是唯一的例外：它的來源是【履歷全文】與【應徵表單】，\n'
    '   **不是報告本文**。報告沒寫沒關係，直接從履歷／表單抄進來。\n'
    '   `commute_note` 要自己算：拿 basics.residence 跟職缺的 locations 比，\n'
    '   寫成「距離約 X 公里／同縣市／人已在當地」這種一句話。算不出來才填 null。\n'
    '7. `fit_scores` 的六個維度**名稱與順序固定**，不可增刪改名。每一維：\n'
    '   - `score` 給 0–10 的整數。**面談中沒有談到、無從判斷的，score 一律填 null**，\n'
    '     不要用 5 分之類的中間值頂替——顧問要看得出哪幾維是真的沒資料。\n'
    '   - `evidence` **必須是候選人的原話或履歷原文的直接引用**，不是你的轉述或總結。\n'
    '     引不到原話就代表這一維沒有依據，`score` 就該是 null。\n'
    '   - `note` 寫一句話說明這個分數怎麼來的，或為什麼無法評估。\n'
    '8. ⚠️ 評分只准依據「這個人能不能做好這份工作」的證據。\n'
    '   年齡、性別、婚姻、生育、國籍、外貌、口音**一律不得影響任何一維的分數**，\n'
    '   也不得出現在 `evidence` 或 `note` 裡。這是就業服務法第 5 條，不是風格偏好。\n'
    '9. 不要自己算總分或等第——那是系統用固定權重算的，你只要給六個維度的分數。\n'
    '10. `expertise_findings`：這場如果有問到職缺專業題庫的題目，**每一題都要留一筆**，\n'
    '    包含他答不出來的（`depth` 填「未談到」）——顧問要知道哪些問了沒結果，\n'
    '    那跟「沒問」是兩件完全不同的事。\n'
    '11. `evidence` 一定要是候選人的原話，不是你的轉述。用人單位主管會直接看這一段來\n'
    '    判斷這個人的專業程度，轉述過的話就失去判斷價值了。\n'
    '12. `depth` 只描述「他講得多具體」，不是評價他專業好不好——\n'
    '    你不是這個領域的專家，不要下那種判斷。\n'
    '13. ⚠️ `consultant_followups` **有幾件寫幾件，不要湊數**。\n'
    '    2026-08-19 檢查十份報告，每一份都剛好三條——那是照格式湊出來的，\n'
    '    不是真的判斷有三件事要追。湊出來的第三條會擠掉真正該追的第四條。\n'
    '    這場已經問清楚的不要再寫進來；真的沒有就給空陣列，那是好事不是漏寫。\n'
    '    ⚠️ 尤其專業題庫上線後，很多原本要顧問補問的專業問題阿財當場就問完了，\n'
    '    這一欄本來就該變短。\n'
    '14. `observations.axis_notes` **一定要四句都填**，依序對應：內斂↔外向、\n'
    '    重執行細節↔重整體策略、需要明確指示↔自主推進、條件導向↔認同導向。\n'
    '    每句是「你為什麼覺得他落在這一軸的哪一邊」的具體依據，盡量引用他\n'
    '    實際的說話方式或原話（例如「回答普遍簡短，平均一句15字內」），\n'
    '    不要寫「他看起來比較內向」這種你自己的形容詞，那是結論不是依據。\n'
    '    這四句會直接印在客戶版報告的光譜圖底下，是唯一佐證那個點為什麼\n'
    '    落在那裡的文字，四軸缺一句，那一軸在客戶版就會是啞巴的一個點。\n'
    '15. `for_client.job_fit_pros`／`job_fit_cons`／`trait_one_liner` 都是針對\n'
    '    「這個人＋這個職缺」的組合寫的，不是泛用的人格優缺點——同一個人格特質\n'
    '    放到不同職缺，優勢劣勢會不一樣。要具體點出跟這個職缺的哪個要求有關，\n'
    '    不要寫「個性穩定」這種放哪個職缺都成立的空話。\n'
    '16. ⚠️ `language_verification`：職缺有要求外語時（見【職缺與客戶】那段），\n'
    '    這欄一定要填。2026-09-07 Jacky 明確拍板：**用打字回答外語一樣算通過**，\n'
    '    不是只有語音才算——判斷標準是「他有沒有在那一題當場、獨立用該語言給出\n'
    '    有實質內容的回答」（不是照抄題目、不是查完翻譯軟體才回），不看他是打字\n'
    '    還是錄音回的。符合就 `verdict` 填「通過」、`how` 據實填「口說」或「文字」；\n'
    '    他答不出來或明顯迴避才填「不通過」；那一題因為故障、跳過、忘記問而根本\n'
    '    沒發生，才填「未測試」——這三種是完全不同的情況，不要混著判斷。\n'
    '    職缺沒有外語要求，整欄照 SPEC 的 null／預設值處理，不要自己編一個語言出來測。\n'
)

_VERDICTS = ('值得轉給顧問', '資訊不足建議補問', '硬條件不符', '待顧問判斷')
# 只用來記 log 提醒人去看，不自動改寫內容——擅自刪字會把顧問要看的原文弄壞
_BANNED_WORDS = ('說謊', '造假', '灌水', '誇大不實')
# 否定詞。2026-08-12 鍾欣修那份報告寫「逐段加總與履歷自寫大致相符，**無灌水跡象**」，
# 被當成「出現不該有的判斷字眼」報警——意思正好相反。
# 這種誤報比漏報更糟：警告一多，真正該看的那則就會被當成雜訊略過。
_NEGATIONS = ('無', '沒有', '未見', '未發現', '不是', '非', '未有', '查無')


def _accuses(text, word):
    """這個字是拿來指控候選人的，還是拿來說「沒有這件事」的？

    只看緊鄰在前面的幾個字。「無灌水跡象」放行，「有灌水嫌疑」照樣示警。
    """
    i = 0
    while True:
        i = text.find(word, i)
        if i < 0:
            return False
        before = text[max(0, i - 6):i]
        if not any(n in before for n in _NEGATIONS):
            return True
        i += len(word)


def _extract_json(text):
    """從模型回覆裡挖出最外層的 JSON。挖不到或不合法就回 None（不丟例外）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = s.split('\n', 1)[-1]
        if s.rstrip().endswith('```'):
            s = s.rstrip()[:-3]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_report_json(obj):
    """把模型的輸出補成規格形狀。

    模型少給一兩個欄位是常態，為此整份丟掉不划算——補好比丟掉有用。
    但**不編造內容**：補進去的一律是空字串／空陣列，不是猜出來的值。
    """
    def s(v):
        return v if isinstance(v, str) else ''

    def arr(v):
        return v if isinstance(v, list) else []

    out = {
        'verdict': obj.get('verdict') if obj.get('verdict') in _VERDICTS else '待顧問判斷',
        'one_liner': s(obj.get('one_liner')),
        'top_selling_point': s(obj.get('top_selling_point')),
        'top_risk': s(obj.get('top_risk')),
        'summary': s(obj.get('summary')),
    }

    # 基本資料（居住地、年齡、學歷、語言、證照…）。
    # ⚠️ 這個函式是白名單——沒列進來的欄位會被整個丟掉。
    #    2026-08-11 加 basics 時就踩過一次：SPEC 加了、模型也產了，
    #    但這裡沒接，結果 content_json 裡永遠是 null。加欄位時兩邊都要改。
    bsc = obj.get('basics') if isinstance(obj.get('basics'), dict) else {}
    out['basics'] = {k: (s(bsc.get(k)) or None) for k in
                     ('residence', 'commute_note', 'age', 'gender', 'education',
                      'languages', 'certificates', 'military')}
    out['basics']['source'] = s(bsc.get('source')) or '履歷／應徵表單，非面談詢問'

    mot = obj.get('motivation') if isinstance(obj.get('motivation'), dict) else {}
    out['motivation'] = {k: s(mot.get(k)) for k in
                         ('why_leaving', 'why_this_role', 'salary_gap',
                          'notice_period', 'other_offers', 'blockers')}

    out['values'] = [s(v) for v in arr(obj.get('values')) if s(v)]
    out['values_basis'] = s(obj.get('values_basis'))

    out['work_history'] = [
        {'employer': s(w.get('employer')) or '（未具名）', 'role': s(w.get('role')),
         'duration': s(w.get('duration')), 'source': s(w.get('source')) or '未提供',
         'nature': s(w.get('nature')), 'note': s(w.get('note'))}
        for w in arr(obj.get('work_history')) if isinstance(w, dict)]

    rvs = [{'item': s(r.get('item')), 'resume_says': s(r.get('resume_says')),
            'candidate_says': s(r.get('candidate_says')),
            'explanation': s(r.get('explanation')),
            'status': s(r.get('status')) or '待釐清'}
           for r in arr(obj.get('resume_vs_spoken')) if isinstance(r, dict)]
    if not rvs:
        # 空陣列＝「沒比對過」，那是誤讀。規範要求沒落差也要留一筆。
        rvs = [{'item': '整體', 'resume_says': '', 'candidate_says': '',
                'explanation': '履歷與口述一致', 'status': '已確認'}]
    out['resume_vs_spoken'] = rvs

    out['hard_conditions'] = [
        {'item': s(h.get('item')), 'verdict': s(h.get('verdict')) or '待確認',
         'detail': s(h.get('detail')),
         # 留空的話顧問不知道這條是哪來的，一律補成「未確認」
         'evidence_source': s(h.get('evidence_source')) or '未確認'}
        for h in arr(obj.get('hard_conditions')) if isinstance(h, dict)]

    ob = obj.get('observations') if isinstance(obj.get('observations'), dict) else {}
    assess = []
    for a in arr(ob.get('assessment')):
        if not isinstance(a, dict):
            continue
        try:
            score = int(a.get('score'))
        except (TypeError, ValueError):
            score = None
        assess.append({'trait': s(a.get('trait')), 'score': score,
                       'verified': s(a.get('verified')) or '未觀察到',
                       'evidence': s(a.get('evidence'))})
    axis_notes_raw = arr(ob.get('axis_notes'))
    axis_notes = [s(x) or None for x in axis_notes_raw[:4]]
    while len(axis_notes) < 4:
        axis_notes.append(None)
    out['observations'] = {'assessment': assess,
                           'communication_style': s(ob.get('communication_style')),
                           'engagement': s(ob.get('engagement')),
                           'axis_notes': axis_notes}

    out['candidate_questions'] = [
        {'question': s(c.get('question')), 'answered': bool(c.get('answered')),
         'note': s(c.get('note'))}
        for c in arr(obj.get('candidate_questions')) if isinstance(c, dict)]

    fc = obj.get('for_client') if isinstance(obj.get('for_client'), dict) else {}
    out['for_client'] = {k: [s(x) for x in arr(fc.get(k)) if s(x)]
                         for k in ('reasons', 'risks_to_disclose', 'suggested_questions',
                                   'job_fit_pros', 'job_fit_cons')}
    out['for_client']['trait_one_liner'] = s(fc.get('trait_one_liner'))

    # 到職障礙的逐條結果。這一段跟專業能力無關，是「他到底能不能來上班」——
    # 顧問看報告時最常被燙到的就是這裡：人選很好、談完了，才發現簽證下不來。
    out['blocker_findings'] = [
        {'item': s(f.get('item')), 'answer': s(f.get('answer')),
         'status': s(f.get('status')) if s(f.get('status')) in ('沒問題', '有風險', '還沒問到', '他答不出來') else '還沒問到',
         'evidence': s(f.get('evidence'))}
        for f in arr(obj.get('blocker_findings')) if isinstance(f, dict)]

    # 專業題的逐題結果。這一段是「用人單位不用自己面談就能判斷」的關鍵：
    # 它給的不是我們的評價，是候選人講過的原話——主管看原話比看分數有用得多。
    out['expertise_findings'] = [
        {'topic': s(f.get('topic')), 'asked': s(f.get('asked')),
         'answered': s(f.get('answered')), 'evidence': s(f.get('evidence')),
         'depth': s(f.get('depth')) if s(f.get('depth')) in ('具體', '籠統', '未談到') else '未談到',
         'note': s(f.get('note'))}
        for f in arr(obj.get('expertise_findings')) if isinstance(f, dict)]

    # 2026-09-07 加：職缺要求外語時，這欄是「有沒有真的驗過」的唯一結構化依據。
    # ⚠️ 這個欄位存在的理由：finish() 會直接讀這裡的 verdict 決定要不要把
    # applications.lang_verified_at 標記成已驗證——欄位名/值域改了，finish()
    # 那段判斷邏輯要一起改，不然又會變回「怎麼測都測不出通過」的舊 bug。
    lv = obj.get('language_verification') if isinstance(obj.get('language_verification'), dict) else {}
    out['language_verification'] = {
        'required_language': s(lv.get('required_language')) or None,
        'tested': bool(lv.get('tested')),
        'verdict': s(lv.get('verdict')) if s(lv.get('verdict')) in ('通過', '不通過', '未測試') else '未測試',
        'how': s(lv.get('how')) if s(lv.get('how')) in ('口說', '文字') else None,
        'evidence': s(lv.get('evidence')),
    }

    # ── 六維度適配評分 ──
    # 2026-08-19 加。原本報告只有 verdict 四選一（值得轉／資訊不足／硬條件不符／
    # 待顧問判斷），同一個 verdict 底下的人沒辦法排序——顧問手上三個「值得轉給顧問」
    # 要先聯絡誰，只能自己重讀三份報告。
    #
    # ⚠️ 總分**在這裡用固定權重算**，不交給模型。模型算加權平均常出錯，
    #    而且同一份報告重跑兩次會給出不同總分，那顧問就不能拿它排序了。
    #    模型只負責給六個維度的分數與證據，算術是程式的事。
    #
    # ⚠️ score 是 null 的維度（面談沒談到）**不是 0 分**，是「不列入計算」——
    #    把沒問到的題目當 0 分會系統性地懲罰話少的場次。作法是把該維的權重
    #    從分母移除，並在 basis 裡標明是用幾維算的，顧問才知道這個分數多可信。
    dims_spec = [('硬條件符合度', 30), ('相關經驗深度', 25), ('案例具體度', 15),
                 ('動機明確度', 15), ('溝通清晰度', 10), ('工作穩定度', 5)]
    fs = obj.get('fit_scores') if isinstance(obj.get('fit_scores'), dict) else {}
    by_name = {s(d.get('name')): d for d in arr(fs.get('dimensions')) if isinstance(d, dict)}
    dims, got, used_w = [], 0.0, 0
    for name, w in dims_spec:
        d = by_name.get(name) or {}
        try:
            sc = int(d.get('score'))
            sc = sc if 0 <= sc <= 10 else None
        except (TypeError, ValueError):
            sc = None
        ev = s(d.get('evidence'))
        # 沒有原話當證據就不算分——這條跟 prompt 裡的規則是同一件事，
        # 在程式端再擋一次，模型忘記時才不會混進沒有依據的分數。
        if sc is not None and not ev:
            sc = None
        if sc is not None:
            got += sc / 10 * w
            used_w += w
        dims.append({'name': name, 'weight': w, 'score': sc, 'evidence': ev,
                     'note': s(d.get('note')) or ('面談中未涉及' if sc is None else '')})
    if used_w >= 50:   # 至少要有一半的權重有依據，總分才有意義
        total = round(got / used_w * 100)
        grade = 'A' if total >= 80 else 'B' if total >= 65 else 'C' if total >= 50 else 'D'
        # ⚠️ 只有一半權重有依據也能算出 80 分＝A，但那個 A 跟六維都問到的 A
        # 不是同一回事。等第後面掛一句話，顧問才不會把半份資料當完整評估。
        if used_w < 75:
            grade += '（依據不足，僅供參考）'
        basis = f'以 {len(dims_spec)} 維中有依據的 {sum(1 for d in dims if d["score"] is not None)} 維計算（權重 {used_w}/100）'
    else:
        total, grade = None, '資料不足無法評分'
        basis = f'有依據的維度權重僅 {used_w}/100，低於 50 就不給總分，避免用半份資料排序候選人'
    out['fit_scores'] = {'dimensions': dims, 'total': total, 'grade': grade, 'basis': basis}

    out['consultant_followups'] = [s(x) for x in arr(obj.get('consultant_followups')) if s(x)]
    return out


def report_to_json(report, ctx, name='', app_id=None):
    """把已經產好的純文字報告轉成結構化 JSON，回傳字串；任何失敗都回 None。

    ⚠️ 這個函式**不准往外丟例外**。它失敗只代表 content_json 存 NULL，
    純文字報告照存、通知照發、面談流程完全不受影響。
    """
    if not report or report.startswith('（報告產生失敗'):
        return None
    prompt = (
        '以下是一份已經產好的初篩報告（純文字）。請把它轉成結構化 JSON。\n'
        '**只做搬運與整理，不要重新判斷、不要加報告裡沒有的東西。**\n\n'
        '【JSON 結構，欄位名稱與層級照這個，不要自己發明】\n' + REPORT_JSON_SPEC + '\n'
        + REPORT_JSON_RULES
        + '\n【職缺硬條件】\n' + json.dumps(ctx.get('job') or {}, ensure_ascii=False, indent=1)
        + '\n\n【應徵表單】\n' + json.dumps(ctx.get('application') or {}, ensure_ascii=False, indent=1)
        # ⚠️ 履歷一定要送。basics（居住地、年齡、學歷、語言、證照）的來源就是這裡，
        #    而規則寫「報告裡沒有的不要自己補」——不送履歷就永遠是 null。
        #    2026-08-11 這個漏送在 finish() 的報告 prompt 出過一次，這裡是第二次。
        + ('\n\n【履歷全文】\n' + (ctx.get('resume_text') or '')[:20000]
           if (ctx.get('resume_text') or '').strip() else '\n\n【履歷】無可讀的履歷檔案')
        + '\n\n【初篩報告全文】\n' + report
        + '\n\n只輸出那一個 JSON 物件，不要有任何其他文字、不要包程式碼區塊。')
    _before_files = _snapshot_session_files()
    try:
        r = subprocess.run(['claude', '-p', sanitize(prompt), '--model', REPORT_MODEL,
                            # NO_TOOLS 是安全與成本設定（見檔頭說明），不要拿掉
                            *NO_TOOLS, '--output-format', 'text'],
                           capture_output=True, text=True, env=env_with_cf(),
                           timeout=REPORT_TIMEOUT)
        log_token_usage(app_id, 'report_json', prompt, _before_files)
        obj = _extract_json(r.stdout)
        if obj is None:
            log(f'⚠️ {name} 結構化報告解析失敗，content_json 存 NULL'
                f'（純文字報告不受影響）：{(r.stdout or r.stderr or "")[:200]}')
            return None
        data = _normalize_report_json(obj)
        blob = json.dumps(data, ensure_ascii=False)
        # 存進去之前先驗一次：解回來一定要是物件。多包一層的字串在後台看起來
        # 一切正常（欄位都在），只有產 PDF 那一刻才會炸掉。
        if not isinstance(json.loads(blob), dict):
            log(f'⚠️ {name} 結構化報告序列化異常，content_json 存 NULL')
            return None
        hit = [w for w in _BANNED_WORDS if _accuses(blob, w)]
        if hit:
            # 不自動改寫——顧問要看到模型原本寫了什麼，才知道這份能不能信
            log(f'⚠️ {name} 結構化報告出現不該有的判斷字眼 {hit}，請人工看一下 content_json')
        # 2026-09-03 加：這裡的 prompt 把整個 job dict（含顧問備註 notes）
        # 原文塞進去，law5_hits() 原本只掃阿財對候選人講的話（見上面
        # handle() 那段），沒掃過這份結構化報告——報告本身雖然是顧問先看，
        # 但走的是客戶版/顧問版兩份輸出（見report_two_versions_spec），
        # 第5條字眼不該從這裡漏進客戶版。同樣不自動改寫，只警告。
        law5 = law5_hits(blob)
        if law5:
            log(f'⚠️ {name} 結構化報告出現就業服務法第5條字眼 {law5}，請人工看一下 content_json')
        return blob
    except Exception as e:
        log(f'⚠️ {name} 結構化報告產生失敗，content_json 存 NULL'
            f'（純文字報告不受影響）：{e}')
        return None


def finish(app_id, name, job_slug, ctx, abandoned=False, close=True):
    """面談結束：產報告、寫回 D1、通知顧問。

    abandoned=True 代表候選人中途離開，沒有正式收尾。
    報告照樣要產——談到一半的內容也是資訊，而且顧問要知道他是在哪一題走的。

    close=False 代表**只產報告、不關房間**（狀態存成 paused）。
    ⚠️ 2026-08-10 加：原本候選人離開 15 分鐘就直接關房，顧問還在忙、
    根本來不及看到通知，等他要處理時房間已經關了，候選人回來只看到
    「面談已結束」。報告要早點給顧問（那是他判斷的依據），
    但房間要留著給候選人回來——這是兩件事，不該綁在一起。
    """
    clear_static_cache(app_id)  # 面談結束，這場的快取沒用了，清掉避免常駐程序記憶體一直長
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if close:
        d1(f"UPDATE applications SET interview_state='done', interview_ended_at='{now}', "
           f"status='interviewed' WHERE id={q(app_id)}")
    else:
        # paused：房間還開著，候選人用原連結回來就能接續（Worker 的 /chat/send
        # 看到非 active 會自動轉回 active）。容量計算只算 active，不會卡住別人。
        d1(f"UPDATE applications SET interview_state='paused', "
           f"status='interviewed' WHERE id={q(app_id)}")

    conv = d1(f"SELECT role, content FROM messages WHERE application_id={q(app_id)} ORDER BY id ASC")
    transcript = '\n'.join(
        f'{"阿財" if m["role"] == "assistant" else "候選人"}：{m["content"]}'
        for m in conv if m['content'] != '（候選人已進入面談室）')

    # 🚨 履歷全文一定要送進報告。
    #    2026-08-11 呂書帆那份報告寫「resume_url 為空，無履歷檔案可交叉核對」、
    #    工作經歷全部標「時間未提及」——但他的日文履歷書一直都在，2012 字、
    #    七段職歷每段都有起訖年月，而且對談時阿財讀得到（開場說了「履歷我看過了」）。
    #    原因就是這裡：report 的 prompt 送了職缺、表單、逐字稿，**唯獨漏了履歷**。
    #    後果很具體：顧問拿到的報告說「僅為口述、未經文件核實」，
    #    還要他自己去翻履歷補年月——這場面談等於做了一半。
    resume_text = (ctx.get('resume_text') or '').strip()
    resume_block = (
        ('\n\n【履歷全文（面談時阿財看到的就是這份，報告要拿它跟逐字稿交叉比對）】\n'
         + resume_text[:20000])
        if resume_text else
        '\n\n【履歷】這位候選人沒有可讀的履歷檔案，'
        '報告裡的經歷一律標「來源：口述」。')

    prompt = (
        '以下是一場已經結束的初步面談。請依規範的 Phase 7 產出初篩報告。\n\n'
        + skill('report')
        + '\n\n【職缺硬條件】\n' + json.dumps(ctx.get('job') or {}, ensure_ascii=False, indent=1)
        + '\n\n【應徵表單】\n' + json.dumps(ctx.get('application') or {}, ensure_ascii=False, indent=1)
        + resume_block
        + '\n\n【逐字稿】\n' + transcript
        + (('\n\n' + engagement_block(app_id)) if engagement_block(app_id) else '')
        + (('\n\n' + answer_timing(app_id)) if answer_timing(app_id) else '')
        + ('\n\n⚠️ 這場面談沒有正式收尾——候選人在最後一則之後就沒有再回應，'
           '推測是關掉視窗離開。請在報告開頭註明「面談未完成（候選人中途離開）」，'
           '並在建議欄說明是在哪一個環節斷的。不要因為資料不全就給空泛的結論。'
           if abandoned else '')
        + '\n\n只輸出報告本文（Markdown），不要有其他說明。')
    _before_files = _snapshot_session_files()
    try:
        r = subprocess.run(['claude', '-p', sanitize(prompt), '--model', REPORT_MODEL,
                            *NO_TOOLS, '--output-format', 'text'],
                           capture_output=True, text=True, env=env_with_cf(), timeout=REPORT_TIMEOUT)
        report = r.stdout.strip() or '（報告產生失敗，請看逐字稿）'
        # 模型常把整份報告包在程式碼區塊裡，推到 Telegram 會多出兩行反引號
        if report.startswith('```'):
            report = report.split('\n', 1)[-1]
            if report.rstrip().endswith('```'):
                report = report.rstrip()[:-3].rstrip()
    except subprocess.TimeoutExpired:
        # ⚠️ 2026-08-18 修：實測孫悅這場真的撞到——subprocess.TimeoutExpired
        # 的 str(e) 會把整包 cmd 陣列印出來，包含 claude -p 的整個 prompt
        # （整份 interview-conductor 技能包＋候選人逐字稿），直接把這串當
        # 報告內容存進資料庫，等於把內部提示詞外洩給看報告的人，報告內容
        # 也完全不是真正的初篩結果（5 萬字，其實是失敗訊息本身）。
        # 逾時要講清楚「逾時」，不能把失敗的原始指令內容當報告存下去。
        report = f'（報告產生失敗：claude 逾時未回應，超過 {CLAUDE_TIMEOUT} 秒。請看下方逐字稿手動評估，或請顧問重新觸發產報告。）'
    except Exception as e:
        # 其他例外（例如 CalledProcessError）同樣可能把完整 cmd 塞進 str(e)，
        # 一律只記錯誤類型，不要把例外內容整包存進報告。
        report = f'（報告產生失敗：{type(e).__name__}。請看下方逐字稿手動評估，或請顧問重新觸發產報告。）'
    log_token_usage(app_id, 'report', prompt, _before_files)

    # 額外產一份結構化 JSON 給後台視覺化／客戶版用。
    # 失敗就是 None → 存 NULL，純文字報告照存，不影響下面任何一步。
    report_json = report_to_json(report, ctx, name, app_id=app_id)

    # 2026-09-07 加：這是 lang_verified_at 唯一會被寫入的地方——之前整支
    # 系統只會「讀」這個欄位（收尾時拿來決定要不要跳警告），但完全沒有任何
    # 程式碼會「寫」它，所以只要職缺有外語要求，警告一定會跳，不管候選人
    # 面談中表現得多好（真實案例：林怡瑩用打字回了兩段完整日文問答，阿財
    # 當場也認可了，但因為沒人寫這個欄位，警告照樣誤報成「沒收到驗證紀錄」）。
    # Jacky 2026-09-07 拍板：打字回答跟語音一樣算數，判斷交給
    # report_json.language_verification（阿財自己在收尾產報告時填的欄位），
    # 不是看有沒有語音檔。
    try:
        lv = (report_json or {}).get('language_verification') or {}
        if lv.get('verdict') == '通過':
            d1(f"UPDATE applications SET lang_verified_at=datetime('now','+8 hours') WHERE id={q(app_id)}")
    except Exception as ex:
        log(f'（寫入 lang_verified_at 失敗，不影響交付：{ex}）')

    rid = save_report(app_id, report, report_json)
    if not rid:
        tg(f'⚠️ {name} 的初篩報告產生了，但存進資料庫失敗——請直接跟顧問確認，'
           f'必要時我可以把報告內容貼進這個對話讓你手動處理。')

    notify_candidate(app_id, abandoned)

    head = '⚠️ 面談中斷（候選人未收尾）' if abandoned else '✅ 面談完成'
    rec = ''
    for line in report.splitlines():
        if line.startswith('建議：'):
            rec = line.strip(); break
    # 一場面談結束就記一筆——這是招募這邊最有代表性的「今天做了什麼」
    runlog('step1ne-interview', 'success',
           f'{name} 完成 AI 面談並產出初篩報告'
           + ('（候選人中途離開）' if abandoned else ''),
           {'candidate': name, 'job': job_slug, 'abandoned': bool(abandoned)})
    log(f'{name} 面談{"中斷" if abandoned else "結束"}，報告已存 {rid}')

    # 連結給不了在外面的人。把兩版 PDF、履歷、結語直接推過去，手機上點開就能讀。
    #
    # ⚠️ 這一整段是面談之後的「交付」，不是面談本身。
    #    包 try/except 是刻意的：報告已經寫進 D1 了（上面那段），
    #    交付失敗頂多是顧問要自己回後台看，不可以讓 finish() 拋例外——
    #    那會讓 wrap_up()／timeout_close() 走進錯誤分支，面談狀態變得不可預期。
    # ⚠️ 2026-08-19 加：該驗外語卻沒驗成，收尾時一定要吵。
    # 徐振倫那場（主管特助・日文是這個缺唯一的硬門檻）因為系統故障跳過驗證，
    # 報告只在追問事項寫了一句「建議顧問親自驗證」，然後就沒有然後了——
    # 硬門檻沒驗的人選被當成一般人選送出去。
    # 這種事不能靠報告裡的一行字，要在顧問的通知裡站出來擋。
    try:
        jl = d1(f"SELECT j.interview_language, a.lang_verified_at, a.chat_token FROM applications a "
                f"LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id = {q(app_id)}")
        if jl and (jl[0].get('interview_language') or '').strip() and not jl[0].get('lang_verified_at'):
            lang = jl[0]['interview_language']
            # 2026-09-07 改：這裡不再假設「一定是語音沒收到」——現在的判斷依據是
            # report_json.language_verification，區分「不通過」（真的答不出來／
            # 迴避）跟「未測試」（那一題根本沒發生，故障/跳過/忘記問）是兩種
            # 完全不同的狀況，訊息要講清楚是哪一種，不要都講成「沒收到驗證紀錄」。
            lv = (report_json or {}).get('language_verification') or {}
            verdict = lv.get('verdict')
            if verdict == '不通過':
                detail = (f'這場**有測**，但候選人{lang}回答不出來或明顯迴避'
                          f'{"（" + lv.get("evidence") + "）" if lv.get("evidence") else ""}。')
            else:
                detail = (f'這場**沒有測到**（可能是故障、跳過，或忘記問），'
                          f'不是候選人答得不好——報告裡的{lang}相關描述只有證照與自述，沒有實測佐證。')
            tg(f'🚨 {name}（{job_slug}）這場的 **{lang}實測沒有通過驗證**。\n\n'
               f'{detail}\n'
               f'⚠️ 送客戶前請先補驗或在推薦時講清楚。\n\n'
               f'補驗連結（候選人單獨錄一段，不用重開整場面談；打字回答也算數）：\n'
               f'https://step1ne.com/interview/?t={jl[0].get("chat_token") or ""}',
               THREAD_POOL)
    except Exception as ex:
        log(f'（外語驗證檢查失敗，不影響交付：{ex}）')

    try:
        deliver_after_interview(app_id, name, job_slug, report_json, abandoned)
    except Exception as ex:
        log(f'❌ {name} 面談交付失敗（報告已存 D1，不影響面談）：{ex}')
        # 保底：交付整個掛掉時，至少還原成舊的最小通知，顧問才知道有這場要看
        tg(f'{head}：{name}（{job_slug}）\n'
           f'{rec or "（報告已產出）"}\n'
           f'⚠️ 報告 PDF 與履歷推送失敗，請至後台查看。\n\n'
           f'報告與逐字稿：https://step1ne.com/consultant/reports/',
       THREAD_POOL)


def wrap_up(app):
    """逾時收尾。

    ⚠️ 一定要先在對話裡留一句話再結束。
    直接把狀態改成 done 的話，候選人（如果還開著頁面）會看到面談突然結束、
    畫面跳出「面談已結束」卻不知道為什麼——那很莫名其妙，而且他會覺得被放棄。
    留一句話至少讓他知道發生什麼、還能不能回來。
    """
    app_id, name = app['id'], app['name']
    try:
        log(f'{name}：閒置超過 {STALE_MIN} 分鐘，判定離開，開始收尾')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        bye = [
            '看您這邊暫時沒有回覆，我先把今天談到的內容整理給顧問。',
            # 講「明天這個時間前」比「24 小時內」好懂——後者要人自己換算。
            f'這個連結我會幫您留著，{"明天這個時間前" if STALE_HOLD_MIN >= 1440 else f"接下來 {STALE_HOLD_MIN // 60} 小時內"}'
            f'隨時回來都可以接著談，不用重頭開始。',
            '想直接跟真人顧問聊也沒問題，透過下方的 LINE 告訴我們就可以。謝謝您今天撥出時間 🙏',
        ]
        vals = ','.join(f"({q(app_id)},'assistant',{q(m)},'{now}')" for m in bye)
        d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES {vals}")
        finish(app_id, name, app['job_slug'], context_for(app_id),
               abandoned=True, close=False)   # 只產報告，房間留著
    except Exception as e:
        log(f'❌ {name} 逾時收尾失敗：{e}')
        tg(f'⚠️ 面談逾時收尾失敗：{name}（{app_id}）\n{str(e)[:300]}', THREAD_SYSTEM)
    finally:
        release_lock(app_id)
        with _lock:
            _busy.discard(app_id)


def timeout_close(app):
    """房間滿一小時的強制收尾——不管候選人還在不在、談到哪裡。

    跟 wrap_up() 的差別只在講法：那邊是「你不見了」，這裡是「時間到了」，
    不是候選人的問題，訊息不能用同一套，不然明明還在打字卻被講成不見了。
    """
    app_id, name = app['id'], app['name']
    try:
        log(f'{name}：房間已滿 {ROOM_HARD_LIMIT_MIN} 分鐘，強制收尾')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        bye = [
            '不好意思，我們今天的面談時間到了，先在這裡跟您告一段落。',
            '目前談到的內容我都會整理給顧問，不管有沒有下一步都會通知您，謝謝您今天撥空 🙏',
        ]
        vals = ','.join(f"({q(app_id)},'assistant',{q(m)},'{now}')" for m in bye)
        d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES {vals}")
        finish(app_id, name, app['job_slug'], context_for(app_id),
               abandoned=True, close=False)   # 只產報告，房間留著
    except Exception as e:
        log(f'❌ {name} 逾時強制收尾失敗：{e}')
        tg(f'⚠️ 面談滿一小時強制收尾失敗：{name}（{app_id}）\n{str(e)[:300]}', THREAD_SYSTEM)
    finally:
        release_lock(app_id)
        with _lock:
            _busy.discard(app_id)


def handle(app):
    app_id, name = app['id'], app['name']
    rate_limited = False
    try:
        ctx = context_for(app_id)
        n = len(ctx.get('conversation') or [])
        talk_prompt = build_prompt(ctx, skill('talk', ctx.get('job')))
        _before_files = _snapshot_session_files()
        result = run_claude(talk_prompt)
        log_token_usage(app_id, 'talk', talk_prompt, _before_files)

        msgs = [m for m in (result.get('messages') or []) if str(m).strip()][:3]
        if not msgs:
            msgs = ['不好意思，我這邊剛剛沒接上，方便再說一次嗎？']

        # 就服法紅線：阿財說出口的話送出去之前擋一次。
        hits = sorted({w for m in msgs for w in law5_hits(m)})
        if hits:
            log(f'⚠️ {name}：阿財這輪出現保護特徵字眼 {hits}，重生成一次')
            retry = run_claude(
                talk_prompt +
                f'\n\n⚠️ 你剛剛那則回覆裡出現了「{"、".join(hits)}」。'
                '就業服務法第 5 條禁止以性別、年齡、婚姻、生育、國籍、身心障礙、'
                '宗教、容貌等條件對求職者為差別待遇——**不要問、不要提、也不要轉述'
                '用人單位的這類偏好**。請重寫這一輪，改問跟工作本身有關的事。')
            r2 = [m for m in (retry.get('messages') or []) if str(m).strip()][:3]
            if r2 and not any(law5_hits(m) for m in r2):
                msgs, result = r2, retry
                log(f'{name}：重生成後已無問題')
            else:
                msgs = ['了解，那我們接著談工作內容的部分。']
                log(f'⚠️ {name}：重生成仍命中，改用安全句')
            tg(f'⚠️ 阿財差點對 {name} 講到保護特徵：{"、".join(hits)}\n'
               f'已攔下並改寫，候選人沒有看到。\n'
               f'職缺：{app.get("job_slug")}　·　這通常代表職缺資料裡混進了用人單位的歧視性偏好，'
               f'值得回頭看一下那個職缺的備註怎麼寫的。', THREAD_SYSTEM)

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        vals = ','.join(f"({q(app_id)},'assistant',{q(m)},'{now}')" for m in msgs)
        d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES {vals}")
        snap_signals(app_id, now)
        log(f'{name}：回了 {len(msgs)} 則')

        if result.get('end') or n >= MAX_TURNS:
            finish(app_id, name, app['job_slug'], ctx)

    except Exception as e:
        log(f'❌ {name}（{app_id}）處理失敗：{e}')
        if _is_rate_limit_error(e):
            rate_limited = True
            log(f'⏳ {name}：額度打滿，安靜重試，不插道歉訊息')
            global _rate_limit_notified_at
            now_ts = time.time()
            if now_ts - _rate_limit_notified_at > RATE_LIMIT_NOTIFY_COOLDOWN_SEC:
                _rate_limit_notified_at = now_ts
                tg(f'⏳ claude 額度用盡：{name}（面談）這場先安靜排隊重試，不用手動處理，'
                   f'額度重置後會自動接上。', THREAD_DECIDE)
        else:
            # 候選人不該乾等。給一句話讓他知道發生什麼，並通知顧問接手。
            try:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                d1(f"INSERT INTO messages (application_id, role, content, created_at) VALUES "
                   f"({q(app_id)},'assistant',"
                   f"{q('不好意思，我這邊系統出了點狀況。我們的顧問會直接與您聯繫，很抱歉耽誤您的時間。')},"
                   f"'{now}')")
            except Exception:
                pass
            tg(f'⚠️ 面談出錯：{name}（{app_id}）\n{str(e)[:400]}\n候選人已被告知顧問會聯繫，請接手。',
               THREAD_DECIDE)
    finally:
        # 額度打滿：不釋放鎖，改成延長鎖到 RATE_LIMIT_RETRY_SEC 之後才能再搶——
        # 節流用，不要每 8 秒就打一次注定失敗的 claude。一般錯誤照舊立刻放鎖。
        if rate_limited:
            try:
                expires = (datetime.datetime.now()
                           + datetime.timedelta(seconds=RATE_LIMIT_RETRY_SEC)).strftime('%Y-%m-%d %H:%M:%S')
                d1(f"UPDATE applications SET lock_expires_at={q(expires)} WHERE id={q(app_id)}")
            except Exception:
                pass
        else:
            release_lock(app_id)
        with _lock:
            _busy.discard(app_id)


def prewarm_candidates():
    """核准後、還沒點進面談室的候選人：趁空檔先幫他們把開場白生成好存起來，
    候選人真的點進來時就能秒收到第一句，不用等 daemon 下一輪輪詢＋現場生成。

    條件跟 Worker 的 needAssessment 閘門一致（中高階免測驗、其他人要先交卷），
    否則會浪費一次 token 去預熱一個候選人根本還進不了房間的開場白。
    """
    return d1("""
        SELECT a.id, a.name, a.job_slug FROM applications a
         LEFT JOIN jobs j ON j.slug = a.job_slug
         WHERE a.status = 'ready'
           AND (a.interview_state IS NULL OR a.interview_state = 'not_started')
           AND a.prewarmed_opening IS NULL
           AND (COALESCE(j.seniority, 'mid') = 'senior'
                OR EXISTS(SELECT 1 FROM assessments s WHERE s.application_id = a.id))
         ORDER BY a.created_at DESC LIMIT 5
    """)


def do_prewarm(app):
    """幫一位還沒進房間的候選人預先生成開場白。

    ⚠️ 這支絕對不能讓候選人等——失敗就算了，反正沒有預熱結果時
    候選人進房間會照舊走現場生成那條路，不會卡住任何人。
    """
    app_id, name = app['id'], app['name']
    try:
        ctx = context_for(app_id)
        talk_prompt = build_prompt(ctx, skill('talk', ctx.get('job')))
        _before_files = _snapshot_session_files()
        result = run_claude(talk_prompt)
        log_token_usage(app_id, 'prewarm', talk_prompt, _before_files)

        msgs = [m for m in (result.get('messages') or []) if str(m).strip()][:3]
        if msgs:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            d1(f"UPDATE applications SET prewarmed_opening={q(json.dumps(msgs, ensure_ascii=False))}, "
               f"prewarmed_at={q(now)} WHERE id={q(app_id)} AND prewarmed_opening IS NULL")
            log(f'{name}：開場白已預熱')
    except Exception as e:
        log(f'⚠️ {name}（{app_id}）預熱開場白失敗（不影響正常面談流程）：{e}')
    finally:
        release_lock(app_id)
        with _lock:
            _busy.discard(app_id)


def paused_sessions():
    """已經產過報告、但房間還留著的場次。"""
    return d1("""
        SELECT a.id, a.name, a.job_slug, a.hold_until,
               (SELECT m.created_at FROM messages m WHERE m.application_id = a.id
                 ORDER BY m.id DESC LIMIT 1) AS last_at
          FROM applications a
         WHERE a.interview_state = 'paused'
    """)


def close_paused(app):
    """留了 STALE_HOLD_MIN 還是沒回來，才真的關掉。

    這裡不再產報告——wrap_up() 早就產過了，重複產只會讓顧問看到兩份。
    """
    app_id, name = app['id'], app['name']
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        d1(f"UPDATE applications SET interview_state='done', interview_ended_at='{now}' "
           f"WHERE id={q(app_id)} AND interview_state='paused'")
        log(f'{name}：保留 {STALE_HOLD_MIN} 分鐘仍未回來，關閉面談室')
    except Exception as e:
        log(f'❌ {name} 關閉保留中的面談室失敗：{e}')
    finally:
        release_lock(app_id)
        with _lock:
            _busy.discard(app_id)


def held_too_long(rows):
    """⚠️ 2026-08-13 真實事故換來的欄位：徐先生的面談因為系統撞到用量上限
    被迫中斷，我們回信說「明天上午再進來即可」，但這支函式原本只認
    STALE_HOLD_MIN（3 小時）這個固定時鐘，完全不知道顧問已經承諾了
    「不確定哪時候」的更長時間——結果他隔天點連結，房間早就被這裡關了。

    現在多認 applications.hold_until：顧問／後台在還不確定人選什麼時候
    回得來時，可以把這個欄位設成很久以後的日期，這裡就不會按 3 小時的
    固定時鐘關房間，直到 hold_until 到期或顧問手動收掉為止。
    沒有設 hold_until 的維持原本 3 小時的行為（這是多數「聊到一半離開」
    的正常情況，不需要每筆都手動处理）。
    """
    now = datetime.datetime.now()
    out = []
    for r in rows:
        if not r.get('last_at'):
            continue
        hu = r.get('hold_until')
        if hu:
            try:
                if now < datetime.datetime.strptime(hu, '%Y-%m-%d %H:%M:%S'):
                    continue   # 顧問特別交代要留久一點，還沒到期，不關
            except Exception:
                pass
        try:
            last = datetime.datetime.strptime(r['last_at'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if (now - last).total_seconds() >= STALE_HOLD_MIN * 60:
            out.append(r)
    return out


# ── 阿財掛掉要主動說 ──
# 2026-09-08 加。2026-09-07 晚上 21:35–21:37 阿財連續兩分鐘查不到資料
# （搬家後排程還指著舊路徑 /Users/user/工作流程技能包/…，資料夾已經不在了），
# log 每 8 秒噴一次錯，但**沒有任何人知道**——是隔天翻 log 才發現的。
# 當下若有候選人在面談，阿財就是不回話，候選人只會覺得系統壞了。
#
# ⚠️ 不是失敗一次就叫。網路瞬斷、D1 偶發逾時都很正常，叫了會變狼來了。
#    連續 3 次（約 24 秒）都失敗才推，而且同一次故障只推一則。
#    恢復時再推一則，才知道要不要繼續處理。
_POLL_FAILS = 0
_POLL_ALERTED = False
POLL_FAIL_ALERT_AT = 3


def _poll_failed(e):
    global _POLL_FAILS, _POLL_ALERTED
    _POLL_FAILS += 1
    log(f'查詢進行中面談失敗（連續第 {_POLL_FAILS} 次）：{e}')
    if _POLL_FAILS >= POLL_FAIL_ALERT_AT and not _POLL_ALERTED:
        _POLL_ALERTED = True
        try:
            tg(f'🔴 阿財連不上資料庫，已經連續失敗 {_POLL_FAILS} 次\n\n'
               f'錯誤：{str(e)[:200]}\n\n'
               f'現在如果有候選人在面談，阿財不會回話。\n'
               f'恢復的話我會再推一則。', THREAD_SYSTEM)
        except Exception:
            pass


def _poll_ok():
    global _POLL_FAILS, _POLL_ALERTED
    if _POLL_ALERTED:
        try:
            tg(f'🟢 阿財恢復正常了（中間失敗了 {_POLL_FAILS} 次）\n\n'
               f'那段期間有候選人在面談的話，請去看一下他有沒有卡住。', THREAD_SYSTEM)
        except Exception:
            pass
    _POLL_FAILS = 0
    _POLL_ALERTED = False


def tick():
    try:
        rows = active_sessions()
    except Exception as e:
        _poll_failed(e)
        return
    _poll_ok()

    # 進場通知放最前面：這件事跟回話、收尾都無關，而且顧問越早知道越有用。
    notify_started(rows)

    # 滿一小時的優先權最高——就算候選人剛好回話了，也不要再讓阿財多聊一輪，
    # 直接強制收尾，不然「硬上限」就變成「軟上限」了
    expired_rows = expired(rows)
    expired_ids = {r['id'] for r in expired_rows}
    remaining = [r for r in rows if r['id'] not in expired_ids]

    try:
        held = held_too_long(paused_sessions() or [])
    except Exception as e:
        log(f'查詢保留中面談失敗：{e}')
        held = []

    # 預熱排在最後——優先權最低，只在真人面談都排開了、還有空的 slot
    # 才會被下面的迴圈撿去跑，絕對不跟真正在等阿財回話的候選人搶名額。
    try:
        prewarm_rows = prewarm_candidates()
    except Exception as e:
        log(f'查詢待預熱名單失敗：{e}')
        prewarm_rows = []

    jobs = ([(a, timeout_close) for a in expired_rows]
            + [(a, handle) for a in pending(remaining)]
            + [(a, wrap_up) for a in stale(remaining)]
            + [(a, close_paused) for a in held]
            + [(a, do_prewarm) for a in prewarm_rows])
    for app, fn in jobs:
        with _lock:
            if app['id'] in _busy or len(_busy) >= MAX_PARALLEL:
                continue
            _busy.add(app['id'])
        # DB 鎖：force_close.py 手動收尾時也搶同一把鎖，搶不到就跳過這輪，
        # 下次輪詢再試——不會跟它同時寫同一場的訊息
        if not acquire_lock(app['id']):
            with _lock:
                _busy.discard(app['id'])
            continue
        threading.Thread(target=fn, args=(app,), daemon=True).start()


def main():
    once = '--once' in sys.argv
    log(f'面談引擎啟動（輪詢 {POLL_SEC}s，同時最多 {MAX_PARALLEL} 場）')
    while True:
        tick()
        if once:
            time.sleep(1)
            while True:
                with _lock:
                    if not _busy:
                        break
                time.sleep(1)
            return
        time.sleep(POLL_SEC)


if __name__ == '__main__':
    main()
