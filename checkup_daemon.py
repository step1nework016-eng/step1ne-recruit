#!/usr/bin/env python3
"""履歷健檢（阿福）的回話與產報告引擎：輪詢 D1，看到本人講話就跑一次 claude。

跟 interview_daemon.py（阿財）是**同一套架構模式**（本機常駐、輪詢、跨行程鎖、
用本機 claude CLI 不開額外 API 金鑰），但完全獨立：
  · 讀 checkups / checkup_messages，一個欄位都不碰 applications / messages
  · 大腦讀 recruiting-workflow/resume-checkup-fu/SKILL.md，不是 interview-conductor
  · 產出的是給本人看的六段健檢報告，不是給顧問看的初篩報告

2026-08-12 加。這支只**讀**interview_daemon.py 參考架構怎麼寫，
不 import 它、不改它一行——兩邊的大腦要分開，壞掉也要各自壞掉。

用法：
    python3 checkup_daemon.py           # 常駐
    python3 checkup_daemon.py --once    # 跑一輪就結束（測試用）
"""
import json, os, subprocess, sys, threading, time, datetime, tempfile, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
POLL_SEC = 8
MAX_PARALLEL = 2        # 這台機器同時還跑著 interview_daemon.py，兩邊加起來的 claude
                        # 行程數要控制住，健檢的急迫性也比面談低，2 場足夠。
CLAUDE_TIMEOUT = 240
MAX_TURNS = 30          # 健檢對談通常比面談短，防跑不完的上限也設低一點
STALE_MIN = 15          # 本人多久沒回就當他離開了（先轉 paused，不是直接關）
STALE_HOLD_MIN = 120    # 房間留著給他回來的時間，比面談的 3 小時短——健檢的急迫性較低
ROOM_HARD_LIMIT_MIN = 60  # 2026-08-12 Jacky 定：健檢一人最多 1 小時，不管聊到哪都強制收尾產報告
WARN_BEFORE_MIN = 10     # 快到上限前幾分鐘先提醒一次，不要無預警被切斷
TIME_WARN_MARK = '⏰'    # 用來判斷這句提醒有沒有發過，避免同一場重複提醒

TALK_MODEL = 'claude-sonnet-5'
REPORT_MODEL = 'claude-sonnet-5'

# Telegram 主題：跟 Worker 端 THREAD.pool（304）用同一個——
# 報告是資料不是決策，不要塞進「面試通知確認」洗掉真正要顧問處置的東西。
THREAD_POOL = 304
THREAD_SYSTEM = 1360

SKILL_PATH = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/resume-checkup-fu/SKILL.md')

# ── 阿福一律不帶任何工具（理由跟阿財一樣，見 interview_daemon.py 檔頭：
#    安全——本人打的字會整段進 prompt，不能讓外部輸入驅動任何工具；
#    成本——工具定義每輪都要重送。--tools '' 實測無效，改用 --disallowed-tools 逐一列名，
#    並且一定要帶 --setting-sources ''，否則使用者全域 CLAUDE.md 的 agentacct 規則
#    會在工具被關掉之後把輸出整個吃掉。）
_BAN_TOOLS = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
              'AskUserQuestion,TodoWrite,BashOutput,KillShell,SlashCommand,Skill,'
              'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN_TOOLS,
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--setting-sources', '']

_busy = set()
_lock = threading.Lock()

# ⚠️ 2026-08-14 加：這台機器上 interview_daemon.py 跟這支共用同一個本機 claude
# 登入額度，額度打滿時 claude -p 不一定乾脆地失敗——有時候會回一段文字說「這段
# 對話結尾怪怪的，我不該亂編」，不是預期的 JSON，直接被當成一般錯誤處理。
# 一般錯誤的處理方式是塞一句道歉訊息給本人，但這樣會把 last_role 從
# 'user' 翻成 'afu'，15 分鐘後 stale() 就會誤判本人閒置、提早收尾產報告——
# 陳厚瑞那場就是這樣被腰斬的，他其實還在認真回答。
# 額度用盡不該算在本人頭上：不插入道歉訊息（讓 last_role 留在 'user'，
# 下一輪自動重試），改用長一點的鎖節流（不要每 8 秒打一次注定失敗的 claude），
# 額度重置後自然接上，本人不用重講一次。
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
    p = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v
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
    """跟 d1_raw 一樣，但用 --file 執行，不受 CLI 參數長度限制。

    ⚠️ 2026-08-12 早上 interview_daemon.py 才因為用 --command 塞長字串
    撞到 `statement too long: SQLITE_TOOBIG`（尹緯正那場報告沒存進去）。
    這支的報告本文長度跟阿財的報告同一量級（健檢報告 spec JSON 實測約 5-8KB，
    report_html 約 10KB），一樣可能撞到，所以凡是寫入報告內容／spec JSON／
    對話文字這類「不確定多長」的欄位，一律走這支，不要圖方便用 d1_raw。
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


def d1(sql):
    return d1_raw(sql).get('results', [])


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


RUNLOG = os.path.expanduser('~/aijob-automation/run-log.jsonl')


def runlog(task, status, summary, metrics=None):
    try:
        os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)
        with open(RUNLOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                'task': task, 'status': status, 'summary': summary,
                'metrics': metrics or {}}, ensure_ascii=False) + '\n')
    except Exception as e:
        log(f'runlog 寫入失敗（不影響主流程）：{e}')


def tg(text, thread=None):
    # 跟 interview_daemon.py 一樣讀 step1ne-tg.env，不要跟總指揮 yuqi 共用的 tg.env 混在一起。
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
            data=urllib.parse.urlencode(body).encode(), timeout=20)
    except Exception as ex:
        log(f'Telegram 推播失敗：{ex}')


def tg_doc(data, filename, caption='', thread=None):
    try:
        e = dict(l.strip().split('=', 1)
                 for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
                 if '=' in l and not l.startswith('#'))
        import uuid, mimetypes
        b = '----s1c' + uuid.uuid4().hex
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


LOCK_TTL_SEC = 90


def acquire_lock(cid):
    now = datetime.datetime.now()
    now_s = now.strftime('%Y-%m-%d %H:%M:%S')
    expires = (now + datetime.timedelta(seconds=LOCK_TTL_SEC)).strftime('%Y-%m-%d %H:%M:%S')
    meta = d1_raw(
        f"UPDATE checkups SET lock_expires_at='{expires}' "
        f"WHERE id={q(cid)} AND (lock_expires_at IS NULL OR lock_expires_at < '{now_s}')"
    ).get('meta', {})
    return bool(meta.get('changes'))


def release_lock(cid):
    d1(f"UPDATE checkups SET lock_expires_at=NULL WHERE id={q(cid)}")


def sanitize(t):
    """清掉控制字元，避免 prompt 當命令列參數傳給 claude 時因為 \\x00 整個掛掉。"""
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)


# ── 花費記錄 ──
# ⚠️ 2026-08-26 加。在這之前阿福這條線**完全沒有記過任何 token 花費**——
# 後台「Token 用量」看到的金額只有阿財那邊，阿福談了幾十場、產了幾份報告
# 的錢一毛都沒進帳，等於整頁的成本是低估的，看了會做出錯的判斷。
# 邏輯照抄 interview_daemon.py 的 log_token_usage()（同一台機器、同一個
# claude CLI、同一個 session jsonl 目錄），差別只在 call_type 前面加
# checkup_ 前綴，後台才分得出這筆是阿福還是阿財。
CLAUDE_PROJECTS_DIR = os.path.expanduser(
    '~/.claude/projects/-Users-user---------step1ne-recruit')


def _snapshot_session_files():
    try:
        return set(os.listdir(CLAUDE_PROJECTS_DIR))
    except Exception:
        return set()


def _sum_session_usage(path, expect_prefix=None):
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


def log_token_usage(checkup_id, call_type, prompt, before_files):
    """這支不准往外丟例外——記錄是加值功能，健檢流程不能因為記帳失敗而中斷。"""
    if not checkup_id:
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
           f"({q(checkup_id)}, {q(call_type)}, {q(usage['model'])}, "
           f"{usage['input_tokens']}, {usage['output_tokens']}, "
           f"{usage['cache_creation_input_tokens']}, {usage['cache_read_input_tokens']}, {q(now)})")
    except Exception:
        pass


def run_claude(prompt, model, checkup_id=None, call_type=None):
    before = _snapshot_session_files() if checkup_id else None
    r = subprocess.run(
        ['claude', '-p', sanitize(prompt), '--model', model,
         *NO_TOOLS, '--output-format', 'text'],
        capture_output=True, text=True, env=env_with_cf(), timeout=CLAUDE_TIMEOUT)
    if checkup_id:
        log_token_usage(checkup_id, call_type or 'checkup_talk', prompt, before)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    return r.stdout.strip()


def extract_json(text):
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
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def skill(section):
    """讀 resume-checkup-fu/SKILL.md。

    section='talk' 只取到「## Phase 2 — 產報告」之前——產報告那段對談時用不到，
    但佔了不小篇幅，每輪都送等於每輪多等好幾秒（跟 interview_daemon.skill() 同理由）。
    section='report' 整份送，前面的護欄跟數字規則產報告時還要用。
    """
    t = open(SKILL_PATH, encoding='utf-8').read()
    if section == 'talk':
        cut = t.find('## Phase 2 — 產報告')
        return t[:cut].rstrip() if cut > 0 else t
    return t


SCHEMA_HINT_TALK = """
只輸出一段 JSON，不要有其他文字、不要包在程式碼區塊裡：

{"messages": ["第一則", "第二則"], "end": false}

- messages：要發給本人的訊息，每則三行以內，最多三則。
- end：這場對談是否結束（收尾講完、或本人明確表示要結束）。
"""


def fetch_checkup(cid):
    r = subprocess.run(['python3', 'fetch_checkup.py', cid],
                       cwd=HERE, capture_output=True, text=True,
                       env=env_with_cf(), timeout=200)
    if r.returncode != 0:
        raise RuntimeError(f'fetch_checkup 失敗：{(r.stderr or r.stdout)[-200:]}')
    return json.loads(r.stdout[r.stdout.index('{'):])


def parse_pending():
    """先幫還沒解析履歷的健檢單跑一次 checkup_parse.py。

    每一輪 tick() 開頭都跑，成本很低（沒有待解析的就立刻回傳），
    確保阿福開口前手上一定是解析過的版本。
    """
    try:
        r = subprocess.run(['python3', 'checkup_parse.py'], cwd=HERE,
                           capture_output=True, text=True, env=env_with_cf(), timeout=120)
        if r.returncode != 0:
            log(f'⚠️ checkup_parse.py 失敗：{(r.stderr or r.stdout)[-200:]}')
    except Exception as e:
        log(f'⚠️ checkup_parse.py 例外：{e}')


SENTINEL = '（本人已進入健檢對談室）'


def build_talk_prompt(ctx):
    c = ctx['checkup']
    conv = ctx.get('messages') or []
    lines = []
    lines.append('你是「阿福」，正在跟一位主動來做履歷健檢的人即時文字對談。以下是你的作業規範：\n')
    lines.append(skill('talk'))
    lines.append('\n\n─────────  本場資料  ─────────\n')

    lines.append('【基本資料】')
    for k in ('name', 'current_title', 'current_industry', 'note'):
        if c.get(k):
            lines.append(f'  {k}：{c[k]}')
    lines.append('  三個關鍵數字目前狀態（NULL 代表還沒問到，是你這場的重點）：')
    lines.append(f"    led_headcount（帶過幾人）：{c.get('led_headcount') or '未取得'}")
    lines.append(f"    budget_scale（預算/營收規模）：{c.get('budget_scale') or '未取得'}")
    lines.append(f"    crowdfunding_raised（募資金額）：{c.get('crowdfunding_raised') or '未取得'}")

    lines.append('\n【履歷】')
    if ctx.get('resume_readable'):
        lines.append(f'  {(ctx.get("resume_text") or "")[:12000]}')
    else:
        lines.append('  ⚠️ 讀不到履歷內容：' + str(c.get('resume_note') or '未提供'))
        lines.append('  ⚠️ 絕對不要說「您的履歷我看過了」。改成請對方口頭介紹經歷。')

    links = ctx.get('links') or []
    if links:
        lines.append('\n【本人留的連結】')
        for l in links:
            lines.append(f"  {l.get('label') or '連結'}：{l.get('url')}")

    lines.append('\n【目前對話】')
    real = [m for m in conv if m.get('content') != SENTINEL]
    if not real:
        lines.append('  （還沒開始。這是開場，請你先開口。）')
    else:
        for m in real:
            who = '你' if m['role'] == 'afu' else '本人'
            lines.append(f'  {who}：{m["content"]}')

    lines.append(f'\n  （已經來回 {len(real)} 則。超過 {MAX_TURNS} 則就要收尾。）')
    lines.append('\n─────────  輸出格式  ─────────')
    lines.append(SCHEMA_HINT_TALK)
    return '\n'.join(lines)


def build_report_prompt(ctx, transcript, abandoned):
    c = ctx['checkup']
    resume_text = (ctx.get('resume_text') or '').strip()
    resume_block = (
        ('\n\n【履歷全文】\n' + resume_text[:20000]) if resume_text else
        '\n\n【履歷】這位使用者沒有可讀的履歷檔案，報告只能依對話內容產出，'
        '第二、三段要如實反映資料有限，不要硬湊。')
    return (
        '以下是一場已經結束的履歷健檢對談。請依規範的「Phase 2 — 產報告」產出報告 JSON。\n\n'
        + skill('report')
        + '\n\n【基本資料】\n' + json.dumps({
            k: c.get(k) for k in ('name', 'current_title', 'current_industry', 'note')
        }, ensure_ascii=False)
        + resume_block
        + '\n\n【對談逐字稿】\n' + transcript
        + (('\n\n⚠️ 這場對談沒有正式收尾（本人閒置過久或房間逾時），'
            '請依已經聊到的內容產出報告，資料不足的地方誠實反映，不要硬湊。')
           if abandoned else '')
        + '\n\n只輸出那一個 JSON 物件，不要有其他文字、不要包程式碼區塊。'
    )


def upload_report_pdf(cid, name, pdf_path):
    """把本機產出的健檢報告 PDF 傳給 Worker 存進 D1（走 files/file_chunks 切塊）。
    需要 RECRUIT_ADMIN_TOKEN，跟 notify_candidate_checkup() 同一組。"""
    import base64
    try:
        tok = None
        for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
            if l.startswith('RECRUIT_ADMIN_TOKEN='):
                tok = l.strip().split('=', 1)[1].strip().strip("'\"")
        if not tok:
            return log('找不到 RECRUIT_ADMIN_TOKEN，跳過健檢報告 PDF 上傳')
        pdf_b64 = base64.b64encode(open(pdf_path, 'rb').read()).decode('ascii')
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/checkup-pdf',
            data=json.dumps({'id': cid, 'name': name, 'pdf_b64': pdf_b64}).encode(),
            headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                     'user-agent': 'step1ne-checkup-daemon/1.0'})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())
        log(f'{name} 健檢報告 PDF 上傳：{"成功" if r.get("ok") else "失敗 " + str(r.get("error"))}')
    except Exception as e:
        log(f'❌ {name} 健檢報告 PDF 上傳失敗：{e}')   # 不影響報告本身（HTML 已經存好）


def notify_candidate_system_error(cid):
    """2026-08-14 加：本人因為系統錯誤（不是他自己閒置）被腰斬時，寄一封道歉信，
    附上原本的聊天室連結請他再進來——跟 notify_candidate_checkup() 寄的是
    「報告完成」信不一樣，這封是「抱歉、麻煩你回來」。失敗不影響報告本身。"""
    try:
        tok = None
        for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
            if l.startswith('RECRUIT_ADMIN_TOKEN='):
                tok = l.strip().split('=', 1)[1].strip().strip("'\"")
        if not tok:
            return log('找不到 RECRUIT_ADMIN_TOKEN，跳過系統錯誤道歉信')
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/checkup-system-error',
            data=json.dumps({'id': cid}).encode(),
            headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                     'user-agent': 'step1ne-checkup-daemon/1.0'})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        log(f'系統錯誤道歉信：{"已寄出" if r.get("ok") else "寄送失敗"}')
    except Exception as e:
        log(f'系統錯誤道歉信失敗：{e}')


def notify_candidate_checkup(cid):
    """請 Worker 寄健檢報告連結給本人。金鑰只放在 Cloudflare secret，本機不留第二份，
    理由跟 interview_daemon.py 的 notify_candidate() 一樣。"""
    try:
        tok = None
        for l in open(os.path.expanduser('~/.config/workflow-os/tokens.env'), encoding='utf-8'):
            if l.startswith('RECRUIT_ADMIN_TOKEN='):
                tok = l.strip().split('=', 1)[1].strip().strip("'\"")
        if not tok:
            return log('找不到 RECRUIT_ADMIN_TOKEN，跳過健檢報告通知信')
        req = urllib.request.Request(
            'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/checkup-done',
            data=json.dumps({'id': cid}).encode(),
            headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                     'user-agent': 'step1ne-checkup-daemon/1.0'})
        r = json.loads(urllib.request.urlopen(req, timeout=30).read())
        log(f'健檢報告通知信：{"已寄出" if r.get("ok") else "寄送失敗"}')
    except Exception as e:
        log(f'健檢報告通知信失敗：{e}')   # 寄不出去不該影響報告本身


def finish(cid, name, ctx, abandoned=False):
    """對談結束：產報告、存 D1、通知顧問。"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    d1(f"UPDATE checkups SET chat_state='done', chat_ended_at='{now}', "
       f"status='talking' WHERE id={q(cid)}")

    msgs = d1(f"SELECT role, content FROM checkup_messages WHERE checkup_id={q(cid)} ORDER BY id ASC")
    transcript = '\n'.join(
        f'{"阿福" if m["role"] == "afu" else "本人"}：{m["content"]}'
        for m in msgs if m['content'] != SENTINEL)

    try:
        raw = run_claude(build_report_prompt(ctx, transcript, abandoned), REPORT_MODEL,
                         checkup_id=cid, call_type='checkup_report')
        spec = extract_json(raw)
    except Exception as e:
        spec = None
        log(f'❌ {name} 報告產生失敗：{e}')

    if spec is None:
        # ⚠️ 2026-08-12 E2E 測試時發現：這裡原本沒呼叫 log()，report 產生失敗時
        # daemon.log 完全看不出發生過什麼事，只能靠 D1 裡 status 停在 talking
        # 才猜得到——那不該是唯一的線索。
        log(f'❌ {name} 報告 JSON 解析失敗（extract_json 回 None 或 run_claude 例外），'
            f'status 退回 talking，健檢單號 {cid}')
        tg(f'⚠️ {name} 的健檢報告產生失敗，請人工查看對談紀錄（{cid}）。', THREAD_SYSTEM)
        d1(f"UPDATE checkups SET status='talking', updated_at='{now}' WHERE id={q(cid)}")
        return

    spec.setdefault('checkup_id', cid)
    spec.setdefault('name', name)
    numbers = spec.get('extracted_numbers') or {}

    # 把 spec 寫進暫存檔，交給既有的 make_checkup_report.py 排版——
    # 這支腳本已經有薪資數字把關與去識別化檢查，不要重新發明。
    out_dir = os.path.join(HERE, 'checkup_reports')
    os.makedirs(out_dir, exist_ok=True)
    out_base = os.path.join(out_dir, cid[:8])
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(spec, f, ensure_ascii=False)
        spec_path = f.name

    try:
        r = subprocess.run(['python3', 'make_checkup_report.py', spec_path, '--out', out_base],
                           cwd=HERE, capture_output=True, text=True, timeout=120)
        ok = (r.returncode == 0)
        if not ok:
            log(f'❌ {name} 報告排版失敗：{(r.stderr or r.stdout)[-400:]}')
    except Exception as e:
        ok = False
        log(f'❌ {name} 報告排版例外：{e}')
    finally:
        os.unlink(spec_path)

    if not ok:
        tg(f'⚠️ {name} 的健檢報告 JSON 產出了，但排版失敗（可能是薪資數字把關擋下），'
           f'請人工查看：{cid}', THREAD_SYSTEM)
        d1(f"UPDATE checkups SET status='talking', updated_at='{now}' WHERE id={q(cid)}")
        return

    html_path, pdf_path = out_base + '.html', out_base + '.pdf'
    html_content = ''
    if os.path.exists(html_path):
        html_content = open(html_path, encoding='utf-8').read()

    # 匿名人選卡：接 candidate-led-bd 的出口。失敗不影響報告本身。
    # spec_path 在上面已經被刪了（排版那步用完即丟），這裡另開一份暫存副本。
    anon_json, anon_ok = None, False
    try:
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(spec, f, ensure_ascii=False)
            anon_spec_path = f.name
        r = subprocess.run(['python3', 'make_checkup_report.py', anon_spec_path, '--anon-card'],
                           cwd=HERE, capture_output=True, text=True, timeout=60)
        os.unlink(anon_spec_path)
        if r.returncode == 0:
            anon_json = r.stdout.strip()
            anon_ok = True
        else:
            log(f'⚠️ {name} 匿名人選卡沒過檢查（不影響報告本身）：{(r.stderr or "")[:300]}')
    except Exception as e:
        log(f'⚠️ {name} 匿名人選卡產生例外：{e}')

    # report_html／content_json 這類長文字一律走 d1_file，理由見 d1_file() 的註解。
    fields = [f"report_html_path={q(html_path)}",
              f"report_pdf_path={q(pdf_path)}",
              f"report_at='{now}'", f"status='reported'",
              f"led_headcount={q(numbers.get('led_headcount'))}",
              f"budget_scale={q(numbers.get('budget_scale'))}",
              f"crowdfunding_raised={q(numbers.get('crowdfunding_raised'))}",
              f"updated_at='{now}'"]
    if anon_ok and anon_json:
        fields.append(f"anon_card_json={q(anon_json)}")
        fields.append(f"anon_card_at='{now}'")
    d1_file(f"UPDATE checkups SET {', '.join(fields)} WHERE id={q(cid)}")
    if html_content:
        d1_file(f"UPDATE checkups SET report_html={q(html_content)} WHERE id={q(cid)}")
    # PDF 也存進 D1，讓本人能直接下載帶走，不是只能看網頁版。
    # ⚠️ 2026-08-12 實測：base64（~1.8MB）直接塞單一欄位撞到 SQLITE_TOOBIG，
    # 即使走 d1_file() 的 --file 匯入也一樣——那只解決 CLI 參數長度問題，
    # 解決不了 D1 本身的單值/單陳述式長度上限。改成打 Worker 的
    # /admin/checkup-pdf，讓它走履歷附件同一套 files/file_chunks 切塊機制存。
    if os.path.exists(pdf_path):
        upload_report_pdf(cid, name, pdf_path)

    runlog('step1ne-checkup', 'success', f'{name} 完成健檢對談並產出報告',
           {'name': name, 'abandoned': bool(abandoned)})
    log(f'{name} 健檢{"中斷" if abandoned else "完成"}，報告已存 {html_path}')

    # 2026-08-12 Jacky 要求：報告不能只留在對談連結裡等本人自己回去點，
    # 要主動寄到信箱。中途離開（abandoned）的場次先不寄——那種情況報告內容
    # 通常不完整，等本人真的聊完再寄比較不會讓他覺得「怎麼才聊一半就結束」。
    #
    # ⚠️ 2026-08-14 加：但如果是「系統出錯」害他被腰斬（不是他自己不回），
    # 完全不寄信會讓他一頭霧水、以為對談莫名其妙斷了。這種情況改寄道歉信
    # 附連結請他回來，不是寄（可能不完整的）報告。
    if not abandoned:
        notify_candidate_checkup(cid)
    elif ctx.get('checkup', {}).get('system_error_at'):
        notify_candidate_system_error(cid)
        d1(f"UPDATE checkups SET system_error_at=NULL WHERE id={q(cid)}")

    # 推給顧問：PDF + 結語。跟阿財那條線一樣，連結給不了在外面的人，直接推附件。
    try:
        if os.path.exists(pdf_path):
            with open(pdf_path, 'rb') as fh:
                tg_doc(fh.read(), f'健檢報告_{name}.pdf', f'🩺 {name} 的 AI 履歷健檢報告', THREAD_POOL)
        tg(f'✅ {name} 的健檢對談完成，報告已產出。\n'
           f'本人可用同一組對談連結查看報告（/checkup-chat/<token>/report）。\n'
           + (f'⚠️ 匿名人選卡未過去識別化檢查，需人工確認才能拿去做陌生開發。\n' if not anon_ok else
              f'匿名人選卡已產出，可用於 candidate-led-bd 陌生開發。\n')
           + f'健檢單號：{cid}', THREAD_POOL)
    except Exception as e:
        log(f'⚠️ {name} 報告推播失敗（報告已存 D1，不影響）：{e}')


def active_checkups():
    return d1("""
        SELECT c.id, c.name,
               (SELECT m.role FROM checkup_messages m WHERE m.checkup_id = c.id
                 ORDER BY m.id DESC LIMIT 1) AS last_role,
               (SELECT m.created_at FROM checkup_messages m WHERE m.checkup_id = c.id
                 ORDER BY m.id DESC LIMIT 1) AS last_at
          FROM checkups c
         WHERE c.chat_state = 'active'
    """)


def pending(rows):
    return [r for r in rows if r.get('last_role') == 'user']


def stale(rows):
    now = datetime.datetime.now()
    out = []
    for r in rows:
        if r.get('last_role') != 'afu' or not r.get('last_at'):
            continue
        try:
            last = datetime.datetime.strptime(r['last_at'], '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if (now - last).total_seconds() >= STALE_MIN * 60:
            out.append(r)
    return out


def nearing_limit(rows):
    """離 1 小時上限還剩 WARN_BEFORE_MIN 分鐘內的房間，且還沒提醒過。"""
    now = datetime.datetime.now()
    out = []
    for r in rows:
        started = d1(f"SELECT chat_started_at FROM checkups WHERE id={q(r['id'])}")
        st = started[0].get('chat_started_at') if started else None
        if not st:
            continue
        try:
            t0 = datetime.datetime.strptime(st, '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        elapsed_min = (now - t0).total_seconds() / 60
        if not (ROOM_HARD_LIMIT_MIN - WARN_BEFORE_MIN <= elapsed_min < ROOM_HARD_LIMIT_MIN):
            continue
        already = d1(
            f"SELECT id FROM checkup_messages WHERE checkup_id={q(r['id'])} "
            f"AND role='afu' AND content LIKE {q('%' + TIME_WARN_MARK + '%')} LIMIT 1")
        if already:
            continue
        out.append(r)
    return out


def warn_soon(c):
    """快到 1 小時上限，先提醒一句，不要無預警被切斷。"""
    cid, name = c['id'], c['name']
    try:
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        msg = (f'{TIME_WARN_MARK} 提醒一下，這場健檢對談最長進行 {ROOM_HARD_LIMIT_MIN} 分鐘，'
               f'還剩不到 {WARN_BEFORE_MIN} 分鐘就會自動收尾整理報告——'
               f'如果有還想確認的事，麻煩把握這段時間喔。')
        d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES "
           f"({q(cid)},'afu',{q(msg)},'{now}')")
        log(f'{name}：快到 1 小時上限，已提醒')
    except Exception as e:
        log(f'❌ {name} 時間提醒失敗：{e}')
    finally:
        release_lock(cid)
        with _lock:
            _busy.discard(cid)


def expired(rows):
    now = datetime.datetime.now()
    out = []
    for r in rows:
        started = d1(f"SELECT chat_started_at FROM checkups WHERE id={q(r['id'])}")
        st = started[0].get('chat_started_at') if started else None
        if not st:
            continue
        try:
            t0 = datetime.datetime.strptime(st, '%Y-%m-%d %H:%M:%S')
        except Exception:
            continue
        if (now - t0).total_seconds() >= ROOM_HARD_LIMIT_MIN * 60:
            out.append(r)
    return out


def handle(c):
    cid, name = c['id'], c['name']
    rate_limited = False
    try:
        ctx = fetch_checkup(cid)
        conv = ctx.get('messages') or []
        n = len([m for m in conv if m.get('content') != SENTINEL])
        raw = run_claude(build_talk_prompt(ctx), TALK_MODEL,
                         checkup_id=cid, call_type='checkup_talk')
        result = extract_json(raw)
        if not result:
            raise RuntimeError(f'回覆裡沒有 JSON：{raw[:200]}')

        msgs = [m for m in (result.get('messages') or []) if str(m).strip()][:3]
        if not msgs:
            msgs = ['不好意思，我這邊剛剛沒接上，方便再說一次嗎？']

        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        vals = ','.join(f"({q(cid)},'afu',{q(m)},'{now}')" for m in msgs)
        d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES {vals}")
        log(f'{name}：回了 {len(msgs)} 則')

        if result.get('end') or n >= MAX_TURNS:
            finish(cid, name, ctx)
    except Exception as e:
        log(f'❌ {name}（{cid}）處理失敗：{e}')
        if _is_rate_limit_error(e):
            rate_limited = True
            log(f'⏳ {name}：額度打滿，安靜重試，不插道歉訊息')
            global _rate_limit_notified_at
            now_ts = time.time()
            if now_ts - _rate_limit_notified_at > RATE_LIMIT_NOTIFY_COOLDOWN_SEC:
                _rate_limit_notified_at = now_ts
                tg(f'⏳ claude 額度用盡：{name}（健檢）這場先安靜排隊重試，不用手動處理，'
                   f'額度重置後會自動接上。', THREAD_SYSTEM)
        else:
            try:
                now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES "
                   f"({q(cid)},'afu',"
                   f"{q('不好意思，我這邊系統出了點狀況，稍後會請顧問直接跟您聯繫，很抱歉耽誤您的時間。')},"
                   f"'{now}')")
                # 2026-08-14 加：這句道歉訊息會讓 last_role 變成 'afu'，等一下有可能
                # 被 stale() 誤判成本人閒置、提早收尾——陳厚瑞那場就是這樣被腰斬，
                # 而且因為收尾時 abandoned=True，finish() 原本會直接跳過寄信，
                # 本人完全不知道發生什麼事、也拿不到重新進來的連結。
                # 記一個時間戳，finish() 看到這個時間戳就知道「這場是被系統錯誤
                # 腰斬的」，改寄一封道歉信附連結，而不是靜悄悄不寄。
                d1(f"UPDATE checkups SET system_error_at={q(now)} WHERE id={q(cid)}")
            except Exception:
                pass
            tg(f'⚠️ 健檢對談出錯：{name}（{cid}）\n{str(e)[:400]}', THREAD_SYSTEM)
    finally:
        # 額度打滿：不釋放鎖，改成延長鎖到 RATE_LIMIT_RETRY_SEC 之後才能再搶——
        # 節流用，不要每 8 秒就打一次注定失敗的 claude。一般錯誤照舊立刻放鎖，
        # 下一輪照常搶。
        if rate_limited:
            try:
                expires = (datetime.datetime.now()
                           + datetime.timedelta(seconds=RATE_LIMIT_RETRY_SEC)).strftime('%Y-%m-%d %H:%M:%S')
                d1(f"UPDATE checkups SET lock_expires_at={q(expires)} WHERE id={q(cid)}")
            except Exception:
                pass
        else:
            release_lock(cid)
        with _lock:
            _busy.discard(cid)


def wrap_up(c):
    """本人閒置過久：留一句話，轉 paused，並先產一份報告（跟阿財同樣的理由——
    報告要早點給顧問，但房間留著讓本人回來)。"""
    cid, name = c['id'], c['name']
    try:
        log(f'{name}：閒置超過 {STALE_MIN} 分鐘，先整理目前談到的內容')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        bye = [
            '看您這邊暫時沒有回覆，我先把目前聊到的整理成一份報告。',
            f'這個連結我會幫您留著，{STALE_HOLD_MIN // 60} 小時內回來都可以繼續聊、報告也會更新。',
        ]
        vals = ','.join(f"({q(cid)},'afu',{q(m)},'{now}')" for m in bye)
        d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES {vals}")
        d1(f"UPDATE checkups SET chat_state='paused' WHERE id={q(cid)}")
        ctx = fetch_checkup(cid)
        finish(cid, name, ctx, abandoned=True)
        # finish() 會把 chat_state 設成 done——但房間應該留給本人回來，改回 paused。
        d1(f"UPDATE checkups SET chat_state='paused', chat_ended_at=NULL WHERE id={q(cid)}")
    except Exception as e:
        log(f'❌ {name} 閒置收尾失敗：{e}')
        tg(f'⚠️ 健檢閒置收尾失敗：{name}（{cid}）\n{str(e)[:300]}', THREAD_SYSTEM)
    finally:
        release_lock(cid)
        with _lock:
            _busy.discard(cid)


def timeout_close(c):
    cid, name = c['id'], c['name']
    try:
        log(f'{name}：房間已滿 {ROOM_HARD_LIMIT_MIN} 分鐘，強制收尾')
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        bye = ['不好意思，這次的對談時間到了，我先在這裡幫您整理報告。',
               '報告完成後會通知顧問，不管有沒有後續都會讓您知道，謝謝您今天撥空 🙏']
        vals = ','.join(f"({q(cid)},'afu',{q(m)},'{now}')" for m in bye)
        d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES {vals}")
        ctx = fetch_checkup(cid)
        finish(cid, name, ctx, abandoned=True)
    except Exception as e:
        log(f'❌ {name} 逾時強制收尾失敗：{e}')
        tg(f'⚠️ 健檢滿 {ROOM_HARD_LIMIT_MIN} 分鐘強制收尾失敗：{name}（{cid}）\n{str(e)[:300]}', THREAD_SYSTEM)
    finally:
        release_lock(cid)
        with _lock:
            _busy.discard(cid)


# ── 阿福掛掉要主動說 ──
# 2026-09-08 加，跟 interview_daemon 同一套理由：阿財 2026-09-07 晚上連續
# 兩分鐘查不到資料（搬家後指著已經不存在的舊路徑），log 一直噴錯但沒人知道，
# 隔天翻 log 才發現。健檢也是候選人在線上等回話的即時流程，同樣要會喊。
#
# ⚠️ 連續 3 次（約 24 秒）才推，同一次故障只推一則，恢復再推一則。
_POLL_FAILS = 0
_POLL_ALERTED = False
POLL_FAIL_ALERT_AT = 3


def _poll_failed(e):
    global _POLL_FAILS, _POLL_ALERTED
    _POLL_FAILS += 1
    log(f'查詢進行中健檢對談失敗（連續第 {_POLL_FAILS} 次）：{e}')
    if _POLL_FAILS >= POLL_FAIL_ALERT_AT and not _POLL_ALERTED:
        _POLL_ALERTED = True
        try:
            tg(f'🔴 阿福連不上資料庫，已經連續失敗 {_POLL_FAILS} 次\n\n'
               f'錯誤：{str(e)[:200]}\n\n'
               f'現在如果有人在做履歷健檢，阿福不會回話。\n'
               f'恢復的話我會再推一則。', THREAD_SYSTEM)
        except Exception:
            pass


def _poll_ok():
    global _POLL_FAILS, _POLL_ALERTED
    if _POLL_ALERTED:
        try:
            tg(f'🟢 阿福恢復正常了（中間失敗了 {_POLL_FAILS} 次）\n\n'
               f'那段期間有人在健檢的話，請去看一下他有沒有卡住。', THREAD_SYSTEM)
        except Exception:
            pass
    _POLL_FAILS = 0
    _POLL_ALERTED = False


def tick():
    parse_pending()
    try:
        rows = active_checkups()
    except Exception as e:
        _poll_failed(e)
        return
    _poll_ok()

    expired_rows = expired(rows)
    expired_ids = {r['id'] for r in expired_rows}
    remaining = [r for r in rows if r['id'] not in expired_ids]

    jobs = ([(c, timeout_close) for c in expired_rows]
            + [(c, handle) for c in pending(remaining)]
            + [(c, wrap_up) for c in stale(remaining)]
            + [(c, warn_soon) for c in nearing_limit(remaining)])
    for c, fn in jobs:
        with _lock:
            if c['id'] in _busy or len(_busy) >= MAX_PARALLEL:
                continue
            _busy.add(c['id'])
        if not acquire_lock(c['id']):
            with _lock:
                _busy.discard(c['id'])
            continue
        threading.Thread(target=fn, args=(c,), daemon=True).start()


def main():
    once = '--once' in sys.argv
    log(f'健檢對談引擎（阿福）啟動（輪詢 {POLL_SEC}s，同時最多 {MAX_PARALLEL} 場）')
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
