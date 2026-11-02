#!/usr/bin/env python3
"""阿財 bot 私訊對話（2026-11-02 Jacky：阿財要是「有腦袋的 agent」，取代顧問直接開 Claude Code 對話視窗）。

流程：顧問私訊 @step1nechi_bot → recruit Worker /telegram/webhook 只排一筆 ai_jobs(kind='acai_chat')
→ WSL2 ai_worker 撿到 → 這支：下載附件 → 用「上鎖」的 claude -p 跑（只准用 consultant_ops.py 工具箱、
只准讀這次對話的資料夾）→ 用阿財 bot 把回覆私訊回去。

- 一個聊天室一個 session（D1 acai_chat_sessions），對話有延續性；顧問說「重新開始」就開新的。
- 身分（誰在講話）由 Worker 從 TG 帳號判斷後放進 payload（consultant_tg 名單），助理改不了；
  工具箱用 COPS_* 環境變數知道是誰、確認卡貼回這個私訊、信件署名對到本人。
- 寄信／寫確認卡照舊：只產生確認卡，真人按了才寄（Worker 的 cop_ 按鈕）。
- 人格說明在 prompts/acai_consultant_agent.md（{OPS} 換成這台的工具箱路徑）。
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
import d1_http as D  # noqa: E402

OPS = os.path.join(HERE, 'consultant_ops.py')
PROMPT_FILE = os.path.join(HERE, 'prompts', 'acai_consultant_agent.md')
WORK = os.path.expanduser('~/acai-chat')
CLAUDE_BIN = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
MODEL = 'claude-sonnet-5-5'   # 2026-11-02 Jacky：阿財 agent 一律 Sonnet 5.5
PY = shutil.which('python3') or 'python3'


def q(v):
    return "'" + str(v).replace("'", "''") + "'"


def _tg_token():
    e = dict(l.strip().split('=', 1) for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8')
             if '=' in l and not l.startswith('#'))
    return e['TG_BOT_TOKEN'].strip('"\'')


def tg(method, payload):
    req = urllib.request.Request(f'https://api.telegram.org/bot{_tg_token()}/{method}', data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:
        return {'ok': False, 'description': str(e)}


def send(chat_id, text):
    text = (text or '').strip() or '（沒有內容）'
    btns = None
    m = re.search(r'\[BUTTONS:\s*([^\]]+)\]\s*$', text)
    if m:
        text = text[:m.start()].rstrip()
        opts = [x.strip() for x in m.group(1).split('|') if x.strip()][:4]
        if opts:   # 按鈕＝幫顧問把選項打出來（ForceReply 風格的快速回覆鍵盤）
            btns = {'keyboard': [[{'text': o}] for o in opts], 'one_time_keyboard': True, 'resize_keyboard': True}
    parts = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    for i, p in enumerate(parts):
        body = {'chat_id': chat_id, 'text': p, 'disable_web_page_preview': True}
        if btns and i == len(parts) - 1:
            body['reply_markup'] = btns
        tg('sendMessage', body)


def download(file_id, name, dest_dir):
    r = tg('getFile', {'file_id': file_id})
    if not r.get('ok'):
        raise RuntimeError(f'下載檔案失敗：{r.get("description")}')
    path = r['result']['file_path']
    safe = re.sub(r'[\\/:*?"<>|]', '_', name or os.path.basename(path))
    out = os.path.join(dest_dir, f'{time.strftime("%m%d-%H%M%S")}_{safe}')
    urllib.request.urlretrieve(f'https://api.telegram.org/file/bot{_tg_token()}/{path}', out)
    return out


def _persona_ver():
    # 2026-11-02 Jacky：阿財功能更新後還拿舊對話裡自己講過的「做不到」回答——說明書一改就自動開新對話
    import hashlib
    return hashlib.md5(open(PROMPT_FILE, 'rb').read() + open(OPS, 'rb').read()).hexdigest()[:12]


def session_for(chat_id, reset):
    D.query("CREATE TABLE IF NOT EXISTS acai_chat_sessions (chat_id TEXT PRIMARY KEY, session_id TEXT, who TEXT, updated_at TEXT)")
    try:
        D.query("ALTER TABLE acai_chat_sessions ADD COLUMN ver TEXT")
    except Exception:
        pass
    if reset:
        D.query(f"DELETE FROM acai_chat_sessions WHERE chat_id={q(chat_id)}")
        return None
    r = D.query(f"SELECT session_id, ver FROM acai_chat_sessions WHERE chat_id={q(chat_id)}")
    rows = (r.get('results') if isinstance(r, dict) else r) or []
    if rows and rows[0].get('ver') != _persona_ver():
        return None      # 功能更新過：開新對話（舊對話裡的「我做不到」不再沿用）
    return rows[0]['session_id'] if rows else None


def save_session(chat_id, sid, who):
    D.query(f"""INSERT INTO acai_chat_sessions (chat_id, session_id, who, updated_at, ver) VALUES ({q(chat_id)},{q(sid)},{q(who)},datetime('now','+8 hours'),{q(_persona_ver())})
                ON CONFLICT(chat_id) DO UPDATE SET session_id=excluded.session_id, who=excluded.who, updated_at=excluded.updated_at, ver=excluded.ver""")


def selfcheck(p):
    """不發 TG：用跟對話一樣的環境跑一次工具箱，把原始輸出回傳（查 WSL2 缺什麼設定用）。"""
    who = p.get('who') or {}
    env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'PATH') if k in os.environ}
    env.setdefault('LANG', 'C.UTF-8')
    env.update({'COPS_TG_ID': str(who.get('id') or ''), 'COPS_TG_USERNAME': who.get('username') or '', 'COPS_TG_NAME': who.get('name') or '',
                'COPS_CHAT': str(p.get('chat_id') or ''), 'COPS_THREAD': '', 'TZ': 'Asia/Taipei'})
    out = []
    for args in (p.get('selfcheck') or [['find', '馬若喬']]):
        r = subprocess.run([PY, OPS, *args], capture_output=True, text=True, timeout=120, env=env, cwd=HERE)
        out.append(f'$ {" ".join(args)} → exit {r.returncode}\n{(r.stdout or "")[-800:]}\n{(r.stderr or "")[-1500:]}')
    cfg = os.path.expanduser('~/.config/workflow-os')
    out.append('config: ' + ', '.join(sorted(x for x in os.listdir(cfg) if x.endswith('.env'))) if os.path.isdir(cfg) else 'config: 沒有資料夾')
    out.append(f'claude: {CLAUDE_BIN}')
    return '\n'.join(out)


def run(p):
    if p.get('selfcheck'):
        return selfcheck(p)
    chat_id = str(p.get('chat_id') or '')
    who = p.get('who') or {}
    name = who.get('name') or '顧問'
    if not chat_id:
        return '沒有 chat_id'
    text = (p.get('text') or '').strip()
    reset = bool(re.fullmatch(r'/?(new|重新開始|換話題|/start)', text))
    tg('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})
    wdir = os.path.join(WORK, chat_id)
    os.makedirs(wdir, exist_ok=True)
    notes = []
    f = p.get('file') or {}
    if f.get('file_id'):
        try:
            path = download(f['file_id'], f.get('file_name') or ('photo.jpg' if f.get('kind') == 'photo' else 'file'), wdir)
            notes.append(f'（顧問傳了一個檔案：{f.get("file_name") or "照片"}，已存在 {path}，可以用 Read 看內容）')
        except Exception as e:
            notes.append(f'（顧問傳了一個檔案，但下載失敗：{e}。請顧問再傳一次）')
    if reset and text in ('/start',):
        text = '你好'
    rt = (p.get('reply_to') or '').strip()
    if rt:
        notes.insert(0, f'（顧問是在回覆這則訊息：「{rt[:1500]}」）')
    prompt = '\n'.join([x for x in [text, *notes] if x]) or '（顧問傳了空白訊息）'
    sid = session_for(chat_id, reset)
    persona = open(PROMPT_FILE, encoding='utf-8').read().replace('{OPS}', OPS)
    import datetime
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo('Asia/Taipei'))
    sysprompt = (persona + f'\n\n現在時間（台北）：{now.strftime("%Y-%m-%d %H:%M")}（星期{"一二三四五六日"[now.weekday()]}）'
                 f'\n正在跟你講話的是：{name}（Telegram @{who.get("username") or "?"}）。')
    env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'PATH') if k in os.environ}
    env.setdefault('LANG', 'C.UTF-8')
    env.update({'COPS_TG_ID': str(who.get('id') or ''), 'COPS_TG_USERNAME': who.get('username') or '', 'COPS_TG_NAME': name,
                'COPS_CHAT': chat_id, 'COPS_THREAD': '', 'TZ': 'Asia/Taipei'})
    cmd = [CLAUDE_BIN, '-p', '--model', MODEL, '--output-format', 'text', '--setting-sources', '', '--strict-mcp-config',
           '--permission-mode', 'dontAsk',
           '--allowedTools', f'Bash({PY} {OPS}:*)', f'Bash(python3 {OPS}:*)', f'Read({wdir}/**)',
           '--disallowedTools', 'Edit', 'Write', 'WebFetch', 'WebSearch', 'Task', 'NotebookEdit',
           '--append-system-prompt', sysprompt]
    new_sid = sid or str(uuid.uuid4())
    cmd += (['--resume', sid] if sid else ['--session-id', new_sid])
    try:
        r = subprocess.run(cmd + ['--', prompt], cwd=wdir, capture_output=True, text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        send(chat_id, '這件事我處理超過 10 分鐘還沒好，先停下來了。可以把要做的事拆小一點再跟我說一次。')
        return 'timeout'
    out = (r.stdout or '').strip()
    if r.returncode != 0 or not out:
        if sid and 'session' in (r.stderr or '').lower():   # 舊對話接不上（例如換機器）就開新的再試一次
            D.query(f"DELETE FROM acai_chat_sessions WHERE chat_id={q(chat_id)}")
            return run({**p, 'text': text})
        send(chat_id, '我這邊出了點狀況，沒辦法完成。請再說一次，還是不行的話跟 Jacky 說一聲。')
        # 不丟例外：丟了 ai_worker 會重試，顧問會收到好幾次「出了點狀況」
        return f'失敗 claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}'
    save_session(chat_id, new_sid, name)
    send(chat_id, out)
    return out[:500]
