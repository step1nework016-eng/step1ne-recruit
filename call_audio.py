#!/usr/bin/env python3
"""call_audio.py — 電話錄音檔 → 準確逐字稿 → 顧問助理的確認卡（E21，2026-10-10 Jacky 核准）

背景：Jacky 用 iPhone 錄電話，iPhone 內建轉文字錯字很多（頂薪→鼎新、上週→商周…），曾讓顧問誤判人選經歷。
流程：Jacky／Phoebe 把錄音檔分享到 TG（說明欄打人選名字）→ recruit worker 只排一筆 ai_jobs(kind='call_audio')，
      裡面只有 TG 的 file_id（**錄音檔不經過、也不存在雲端**）→ 這台 WSL2 的 ai_worker 撈到後：
      1) 有阿財面談進行中就不撈（ai_worker 的撈工作條件擋掉）
      2) 用 bot token 自己下載到暫存資料夾
      3) 本機 faster-whisper 轉文字（initial_prompt／hotwords 餵這位人選的履歷公司名、職稱、職缺的專有名詞）；**不上傳任何雲端**
      4) 用說明欄解析人選（同名列清單請 Jacky 指定）
      5) 呼叫 consultant_ops.py summary 貼「確認卡」——按了「✅ 寫進卡片」才寫進人選卡片
      6) 不管成功失敗都刪掉錄音暫存檔與逐字稿暫存檔（個資）

這支只負責「錄音檔 → 逐字稿 → 確認卡」。不寄信、不傳訊息給人選；確認卡沒按就不寫卡片。

只在這台啟用：`enabled()` 要求 (a) 是 WSL2、(b) 轉文字用的 venv（~/.venvs/call_audio）已經裝好；Mac 上 ai_worker 的撈工作條件會排除這個 kind。
轉文字在獨立子程序（nice 10）跑，不把幾 GB 的模型放進 ai_worker 本體。

用法（除錯）：
  python3 call_audio.py --transcribe <音檔> [--prompt <檔>] --out <json>   # 在 venv 的 python 底下：只做轉文字
  python3 call_audio.py --download-model                                    # 一次性：把模型下載到本機快取（需事先核准）
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MAX_BYTES = 20 * 1024 * 1024           # Telegram Bot 下載上限
MODEL_NAME = os.environ.get('CALL_AUDIO_MODEL', 'large-v3')
VENV_PY = os.path.expanduser(os.environ.get('CALL_AUDIO_PY', '~/.venvs/call_audio/bin/python'))
PROMPT_MAX_CHARS = 180                  # Whisper initial_prompt 約 224 token 上限，留餘裕
TRANSCRIBE_TIMEOUT = int(os.environ.get('CALL_AUDIO_TIMEOUT', '3600'))
IDLE_WAIT_SEC = 600                     # 撈到之後才有面談開始：最多再等這麼久


class CallAudioError(Exception):
    """可以直接告訴 Jacky 的錯誤（不用重試）。"""


class RetryLater(Exception):
    """暫時性失敗（網路等），讓 ai_worker 照一般流程重試。"""


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] call_audio: {m}', flush=True)


def enabled():
    try:
        wsl = 'microsoft' in platform.release().lower()
    except Exception:
        wsl = False
    return bool(wsl and os.path.exists(VENV_PY) and os.environ.get('CALL_AUDIO_DISABLE') != '1')


# ── TG ──────────────────────────────────────────────────────────
def _tg_token():
    for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8'):
        if l.startswith('TG_BOT_TOKEN='):
            return l.strip().split('=', 1)[1].strip().strip("'\"")
    raise CallAudioError('這台找不到 TG 機器人金鑰（step1ne-tg.env），無法下載錄音')


def notify(chat_id, thread_id, text):
    """送一則 TG 訊息到收到錄音的那個主題。失敗只記 log（不能因為通知壞掉讓整條流程壞掉）。"""
    try:
        data = {'chat_id': str(chat_id), 'text': text[:3800], 'disable_web_page_preview': 'true'}
        if thread_id is not None:
            data['message_thread_id'] = str(thread_id)
        urllib.request.urlopen(urllib.request.Request(
            f'https://api.telegram.org/bot{_tg_token()}/sendMessage', data=urllib.parse.urlencode(data).encode()), timeout=20).read()
        return True
    except Exception as e:
        log(f'TG 通知失敗：{type(e).__name__}')
        return False


def tg_download(file_id, dest_dir):
    """用 file_id 下載到 dest_dir，回傳本機路徑。超過 20MB 直接拒絕。錯誤訊息不含網址（網址裡有金鑰）。"""
    tok = _tg_token()
    try:
        info = json.load(urllib.request.urlopen(
            f'https://api.telegram.org/bot{tok}/getFile?file_id={urllib.parse.quote(file_id)}', timeout=30))
    except urllib.error.HTTPError as e:
        raise CallAudioError(f'Telegram 回報讀不到這個檔案（HTTP {e.code}）。檔案可能太大（超過 20MB）或已過期，請重傳。')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RetryLater(f'連 Telegram 失敗：{type(e).__name__}')
    if not info.get('ok'):
        raise CallAudioError('Telegram 讀不到這個檔案：' + str(info.get('description'))[:100])
    res = info['result']
    if (res.get('file_size') or 0) > MAX_BYTES:
        raise CallAudioError('錄音檔超過 20MB（Telegram 機器人下載上限），請改傳較短的段落。')
    ext = os.path.splitext(res.get('file_path') or '')[1][:8] or '.audio'
    dest = os.path.join(dest_dir, 'rec' + ext)
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/file/bot{tok}/{res["file_path"]}', timeout=120) as resp, open(dest, 'wb') as f:
            n = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_BYTES:
                    raise CallAudioError('錄音檔超過 20MB（Telegram 機器人下載上限），請改傳較短的段落。')
                f.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RetryLater(f'下載錄音失敗：{type(e).__name__}')
    return dest


# ── 人選與提示詞 ─────────────────────────────────────────────────
def resolve_candidates(caption, d1):
    """說明欄 → 人選清單（可能 0、1、多筆）。規則同 consultant_ops.resolve，但不 sys.exit。"""
    key = (caption or '').strip()
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f-]{27}', key):
        return d1(f"SELECT id,name,job_slug,job_title,created_at FROM applications WHERE id={q(key)}")
    return d1("SELECT a.id,a.name,a.job_slug,COALESCE(j.title,a.job_title) AS job_title,a.created_at FROM applications a "
              "LEFT JOIN jobs j ON j.slug=a.job_slug "
              f"WHERE a.name LIKE {q('%' + key + '%')} AND a.superseded_by IS NULL AND COALESCE(a.status,'')<>'duplicate' "
              "ORDER BY a.created_at DESC LIMIT 12")


_ORG = re.compile(r'(股份有限公司|有限公司|集團|控股|科技|生技|生醫|醫療|電子|實業|企業|工業|銀行|證券|保險|顧問|資訊|軟體|網路|傳媒|建設|營造|物流|食品|製藥|半導體|光電|精密|材料|化學|能源|零售|百貨|飯店|診所|醫院|學校|大學|事務所|工作室)$')
_TITLE = re.compile(r'(總經理|副總|執行長|總監|協理|副理|襄理|經理|主任|主管|課長|組長|廠長|店長|專員|助理|特助|工程師|分析師|設計師|顧問|會計|秘書|業務|PM|RD|QA)$')
_SPLIT = re.compile(r'[\s｜|/、，,；;。:：()（）\[\]【】「」"“”\-–—·•\t\r\n]+')
_STOP = {'THE', 'AND', 'FOR', 'WITH', 'FROM', 'THAT', 'THIS', 'HAVE', 'YOUR', 'ARE', 'NOT', 'ALL', 'ANY', 'CAN', 'WILL', 'HTTP', 'HTTPS', 'WWW', 'COM', 'PDF', 'DOC', 'NULL', 'TRUE', 'FALSE'}


def extract_terms(text, limit=40):
    """從履歷／職缺文字挑出 Whisper 容易聽錯的專有名詞：公司名、職稱、英數縮寫（T100、BOM、SAP…）。"""
    orgs, titles, ascii_t, seen = [], [], [], set()
    for tok in _SPLIT.split(str(text or '')):
        tok = tok.strip('.,;:!?')
        if not tok or tok in seen:
            continue
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9+#.]{1,19}|[A-Za-z]+[0-9][A-Za-z0-9+#.]*', tok):
            up = tok.upper()
            if up in _STOP or (tok.islower() and not any(c.isdigit() for c in tok)) or len(tok) < 2:
                continue
            ascii_t.append(tok); seen.add(tok); continue
        if 2 <= len(tok) <= 14 and re.search(r'[一-鿿]', tok):
            if _ORG.search(tok) and len(tok) >= 3:
                orgs.append(tok); seen.add(tok)
            elif _TITLE.search(tok) and len(tok) >= 3:
                titles.append(tok); seen.add(tok)
    return (orgs + ascii_t + titles)[:limit]


def build_prompt(terms, intro='以下是獵頭顧問與人選的電話訪談，提到的公司、職稱與專有名詞有：'):
    out, used = [], len(intro)
    for t in terms:
        if used + len(t) + 1 > PROMPT_MAX_CHARS:
            break
        out.append(t); used += len(t) + 1
    return (intro + '、'.join(out) + '。') if out else ''


def load_context(app_id, d1):
    """人選的履歷文字＋職缺資料 → 專有名詞清單。只讀。查不到的部分略過。"""
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    terms_src = []
    try:
        a = (d1(f"SELECT name, job_slug, job_title, resume_file_id, resume_url_text FROM applications WHERE id={q(app_id)}") or [{}])[0]
        terms_src.append(a.get('job_title') or '')
        if a.get('resume_url_text'):
            terms_src.append(a['resume_url_text'])
        if a.get('resume_file_id'):
            f = d1(f"SELECT text_content FROM files WHERE id={q(a['resume_file_id'])}")
            if f and f[0].get('text_content'):
                terms_src.append(f[0]['text_content'])
        if a.get('job_slug'):
            j = d1(f"SELECT title, must_skills, required_conditions, nice_to_have_skills FROM jobs WHERE slug={q(a['job_slug'])}")
            if j:
                terms_src += [str(j[0].get(k) or '') for k in ('title', 'must_skills', 'required_conditions', 'nice_to_have_skills')]
    except Exception as e:
        log(f'讀履歷／職缺失敗（沒有專有名詞提示，照樣轉文字）：{str(e)[:80]}')
    return extract_terms('\n'.join(terms_src))


# ── 轉文字（在子程序，venv 的 python）─────────────────────────────
def transcribe(path, terms):
    """回傳 {'text', 'duration', 'secs'}。terms 空就不給提示。子程序用 nice 10，不跟阿財搶 CPU。"""
    work = os.path.dirname(path)
    out = os.path.join(work, 'out.json')
    cmd = ['nice', '-n', '10', VENV_PY, os.path.abspath(__file__), '--transcribe', path, '--out', out]
    if terms:
        pf = os.path.join(work, 'prompt.txt')
        open(pf, 'w', encoding='utf-8').write(build_prompt(terms))
        os.chmod(pf, 0o600)
        cmd += ['--prompt', pf, '--hotwords', ' '.join(terms[:30])]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TRANSCRIBE_TIMEOUT)
    if r.returncode != 0:
        raise CallAudioError('轉文字程式失敗：' + (r.stderr or r.stdout or '')[-300:])
    return json.load(open(out, encoding='utf-8'))


def _to_trad(text):
    try:
        import zh_trad
        return zh_trad.to_traditional(text)
    except Exception:
        return text


# ── 確認卡 ──────────────────────────────────────────────────────
def post_card(app, transcript_text, payload, work_dir):
    """呼叫既有的 consultant_ops.py summary：貼確認卡，按「寫進卡片」才寫。"""
    f = os.path.join(work_dir, 'transcript.txt')
    open(f, 'w', encoding='utf-8').write(transcript_text)
    os.chmod(f, 0o600)
    frm = payload.get('from') or {}
    env = dict(os.environ, COPS_TG_ID=str(frm.get('id') or ''), COPS_TG_USERNAME=str(frm.get('username') or ''),
               COPS_TG_NAME=str(frm.get('name') or ''), COPS_CHAT=str(payload.get('chat_id') or ''),
               COPS_THREAD=str(payload.get('thread_id') or ''))
    r = subprocess.run([sys.executable, os.path.join(HERE, 'consultant_ops.py'), 'summary', app['id'], '--file', f],
                       capture_output=True, text=True, env=env, timeout=120)
    if r.returncode != 0:
        raise CallAudioError('貼確認卡失敗：' + (r.stderr or r.stdout or '')[-200:])
    return (r.stdout or '').strip()[-200:]


# ── 主流程 ──────────────────────────────────────────────────────
def interview_active_here(d1):
    host = os.environ.get('INTERVIEW_HOST') or ('wsl2' if 'microsoft' in platform.release().lower() else 'mac')
    try:
        r = d1(f"SELECT COUNT(*) n FROM applications WHERE interview_state='active' AND interview_host='{host}' "
               f"AND interview_started_at >= datetime('now','+8 hours','-3 hours')")
        return bool(r and r[0].get('n'))
    except Exception:
        return True


def wait_idle(d1, sleep=time.sleep, max_wait=IDLE_WAIT_SEC):
    t0 = time.time()
    while interview_active_here(d1):
        if time.time() - t0 > max_wait:
            raise RetryLater('阿財面談一直進行中，先不轉文字')
        sleep(30)


def run(job, d1=None, download=tg_download, transcribe_fn=transcribe, card_fn=post_card, notify_fn=notify,
        context_fn=load_context, idle_fn=wait_idle, work_root=None):
    """處理一筆 ai_jobs(kind='call_audio')。回傳給 ai_jobs.result_text 的短字串（不含逐字稿，逐字稿只在確認卡）。"""
    if d1 is None:
        import d1_http
        d1 = lambda sql: d1_http.query(sql)['results']
    payload = json.loads(job.get('payload_json') or '{}')
    chat, th = payload.get('chat_id'), payload.get('thread_id')
    caption = payload.get('caption') or ''
    say = lambda t: notify_fn(chat, th, t) if chat else None
    work = tempfile.mkdtemp(prefix='call_audio_', dir=work_root)
    os.chmod(work, 0o700)
    try:
        if (payload.get('file_size') or 0) > MAX_BYTES:
            say('這個錄音檔超過 20MB（Telegram 機器人下載上限），請改傳較短的段落。')
            return '檔案過大，已請對方改傳'
        cands = resolve_candidates(caption, d1)
        if not cands:
            say(f'查不到「{caption}」這位人選（名字可以只打一部分試試）。請重傳錄音並在說明欄改打名字。')
            return '查無人選'
        if len(cands) > 1:
            lines = '\n'.join(f'  {x["id"]}｜{x["name"]}｜{x.get("job_title") or x.get("job_slug")}｜{str(x.get("created_at"))[:10]}' for x in cands)
            say(f'「{caption}」對到 {len(cands)} 筆應徵，請重傳錄音並在說明欄貼上要的編號：\n{lines}')
            return '同名多筆，已請對方指定'
        app = cands[0]
        terms = context_fn(app['id'], d1)
        path = download(payload['file_id'], work)
        log(f'已下載（{os.path.getsize(path) / 1048576:.1f}MB），專有名詞提示 {len(terms)} 個，等阿財空檔後轉文字')
        idle_fn(d1)
        t0 = time.time()
        res = transcribe_fn(path, terms)
        text = _to_trad((res.get('text') or '').strip())
        if len(text) < 30:
            say(f'「{app["name"]}」的錄音轉出來的文字太少（{len(text)} 字），可能是靜音或音質太差，請確認錄音檔後重傳。')
            return '轉出文字太少'
        mins = (res.get('duration') or 0) / 60
        header = (f'【電話錄音轉文字｜AI 辨識，公司名、職稱、數字請對照原錄音核對｜錄音約 {mins:.0f} 分鐘｜未分講話者】\n')
        out = card_fn(app, header + text, payload, work)
        log(f'完成：{app["name"]}，{len(text)} 字，轉文字 {time.time() - t0:.0f} 秒')
        return f'已貼確認卡（{app["name"]}，{len(text)} 字）'
    except CallAudioError as e:
        say(f'⚠️ 錄音轉文字沒成功（{caption}）：{str(e)[:300]}')
        return '失敗：' + str(e)[:200]
    finally:
        shutil.rmtree(work, ignore_errors=True)       # 錄音與逐字稿暫存檔一律刪掉（個資）


def notify_failure(job, msg):
    """ai_worker 重試用完還是失敗時呼叫：告訴 Jacky。"""
    try:
        p = json.loads(job.get('payload_json') or '{}')
        if p.get('chat_id'):
            notify(p['chat_id'], p.get('thread_id'), f'⚠️ 「{p.get("caption")}」的錄音轉文字重試都失敗了：{str(msg)[:200]}\n請稍後重傳，或請工程查看。')
    except Exception:
        pass


# ── 子程序：真正跑 faster-whisper（只在 venv 的 python 底下執行）──────────
def _transcribe_main(a):
    from faster_whisper import WhisperModel
    t0 = time.time()
    model = WhisperModel(MODEL_NAME, device='cpu', compute_type=os.environ.get('CALL_AUDIO_COMPUTE', 'int8'),
                         cpu_threads=int(os.environ.get('CALL_AUDIO_THREADS', '8')),
                         local_files_only=not a.allow_download)
    prompt = open(a.prompt, encoding='utf-8').read().strip() if a.prompt else None
    kw = {}
    if a.hotwords:
        kw['hotwords'] = a.hotwords
    try:
        segs, info = model.transcribe(a.transcribe, language='zh', initial_prompt=prompt, beam_size=5, vad_filter=True, **kw)
    except TypeError:                       # 舊版 faster-whisper 沒有 hotwords
        kw.pop('hotwords', None)
        segs, info = model.transcribe(a.transcribe, language='zh', initial_prompt=prompt, beam_size=5, vad_filter=True)
    text = '\n'.join(s.text.strip() for s in segs if s.text.strip())
    json.dump({'text': text, 'duration': info.duration, 'secs': round(time.time() - t0, 1), 'model': MODEL_NAME},
              open(a.out, 'w', encoding='utf-8'), ensure_ascii=False)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--transcribe')
    ap.add_argument('--prompt')
    ap.add_argument('--hotwords')
    ap.add_argument('--out')
    ap.add_argument('--allow-download', action='store_true')
    ap.add_argument('--download-model', action='store_true')
    a = ap.parse_args()
    if a.download_model:
        from faster_whisper import WhisperModel
        WhisperModel(MODEL_NAME, device='cpu', compute_type='int8')       # 第一次會下載到 ~/.cache/huggingface
        print('模型已就緒', MODEL_NAME)
    elif a.transcribe and a.out:
        _transcribe_main(a)
    else:
        ap.print_help()
