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
ROOM_HARD_LIMIT_MIN = 45  # 不管聊到哪，滿 45 分鐘強制收尾產報告

TALK_MODEL = 'claude-sonnet-5'
REPORT_MODEL = 'claude-sonnet-5'

# Telegram 主題：跟 Worker 端 THREAD.pool（304）用同一個——
# 報告是資料不是決策，不要塞進「面試通知確認」洗掉真正要顧問處置的東西。
THREAD_POOL = 304
THREAD_SYSTEM = 1360

SKILL_PATH = os.path.expanduser(
    '~/工作流程技能包/recruiting-workflow/resume-checkup-fu/SKILL.md')

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


def d1_raw(sql):
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


def run_claude(prompt, model):
    r = subprocess.run(
        ['claude', '-p', sanitize(prompt), '--model', model,
         *NO_TOOLS, '--output-format', 'text'],
        capture_output=True, text=True, env=env_with_cf(), timeout=CLAUDE_TIMEOUT)
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
        raw = run_claude(build_report_prompt(ctx, transcript, abandoned), REPORT_MODEL)
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

    runlog('step1ne-checkup', 'success', f'{name} 完成健檢對談並產出報告',
           {'name': name, 'abandoned': bool(abandoned)})
    log(f'{name} 健檢{"中斷" if abandoned else "完成"}，報告已存 {html_path}')

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
    try:
        ctx = fetch_checkup(cid)
        conv = ctx.get('messages') or []
        n = len([m for m in conv if m.get('content') != SENTINEL])
        raw = run_claude(build_talk_prompt(ctx), TALK_MODEL)
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
        try:
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            d1(f"INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES "
               f"({q(cid)},'afu',"
               f"{q('不好意思，我這邊系統出了點狀況，稍後會請顧問直接跟您聯繫，很抱歉耽誤您的時間。')},"
               f"'{now}')")
        except Exception:
            pass
        tg(f'⚠️ 健檢對談出錯：{name}（{cid}）\n{str(e)[:400]}', THREAD_SYSTEM)
    finally:
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


def tick():
    parse_pending()
    try:
        rows = active_checkups()
    except Exception as e:
        log(f'查詢進行中健檢對談失敗：{e}')
        return

    expired_rows = expired(rows)
    expired_ids = {r['id'] for r in expired_rows}
    remaining = [r for r in rows if r['id'] not in expired_ids]

    jobs = ([(c, timeout_close) for c in expired_rows]
            + [(c, handle) for c in pending(remaining)]
            + [(c, wrap_up) for c in stale(remaining)])
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
