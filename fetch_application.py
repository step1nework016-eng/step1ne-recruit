#!/usr/bin/env python3
"""取一份應徵資料的完整內容：表單欄位、職缺硬條件、以及**履歷全文**。

存在的理由：面談開場要說「您的履歷我看過了」，那句話決定候選人後面
願不願意講細節。這支腳本負責讓那句話是真的。

文字由 parse_resumes.py 事先抽好存在 D1，這裡只是讀出來——
不在這裡即時解析，否則候選人要等，而且同一份履歷會被解析很多次。
還沒抽到的（表單剛送出、排程還沒跑）這裡會即時補抽一次當保險。

用法：
    python3 fetch_application.py <application_id>
    python3 fetch_application.py --latest        # 最新一筆，測試用
    python3 fetch_application.py --list          # 列出可用的 id
"""
import base64, json, os, re, subprocess, sys, tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'
# npx 在 Windows 是 npx.cmd，subprocess.run(['npx',...]) 不帶副檔名會
# FileNotFoundError，先解出實際路徑（macOS/Linux 不受影響）。
NPX_BIN = shutil.which('npx') or 'npx'


def d1(sql):
    """跑一段 SQL 拿回結果。用 --json 才拿得到結構化輸出。"""
    env = dict(os.environ)
    conf = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(conf):
        for line in open(conf, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v
    r = subprocess.run(
        [NPX_BIN, '--yes', 'wrangler', 'd1', 'execute', DB, '--remote', '--json',
         f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env, timeout=180)
    if r.returncode != 0:
        sys.exit(f'D1 查詢失敗：{(r.stderr or r.stdout)[-400:]}')
    # wrangler 會在 JSON 前面印一些訊息，抓第一個 [ 開始
    out = r.stdout[r.stdout.index('['):]
    return json.loads(out)[0]['results']


def extract_text(raw, filename, mime):
    """把履歷檔轉成純文字。抽不出來要明講，不能回空字串裝作沒事。"""
    ext = (os.path.splitext(filename or '')[1] or '').lower()
    with tempfile.NamedTemporaryFile(suffix=ext or '.bin', delete=False) as f:
        f.write(raw); path = f.name
    try:
        if ext == '.pdf' or 'pdf' in (mime or ''):
            r = subprocess.run(['pdftotext', '-layout', '-enc', 'UTF-8', path, '-'],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            # pdftotext 抽不到通常代表那是掃描的圖片式 PDF
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    t = '\n'.join((p.extract_text() or '') for p in pdf.pages)
                if t.strip():
                    return t
            except Exception:
                pass
            return ('【無法抽取文字】這份 PDF 可能是掃描影像。'
                    '面談時不要說「履歷我看過了」，改成請對方口頭介紹經歷。')
        if ext in ('.docx', '.doc'):
            # textutil 是 macOS 專用指令，Windows 上不存在；.docx 改用
            # docx2txt（純 Python），.doc 沒有對應的跨平台替代。
            try:
                r = subprocess.run(['textutil', '-convert', 'txt', '-stdout', path],
                                   capture_output=True, text=True, timeout=60)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout
            except FileNotFoundError:
                if ext == '.docx':
                    try:
                        import docx2txt
                        text = docx2txt.process(path) or ''
                        if text.strip():
                            return text
                    except Exception:
                        pass
        return '【無法抽取文字】不支援的檔案格式：' + (ext or mime or '未知')
    finally:
        os.unlink(path)


def main():
    a = sys.argv[1:] or ['--latest']

    if a[0] == '--list':
        for r in d1("SELECT id, created_at, name, job_slug, status "
                    "FROM applications ORDER BY created_at DESC LIMIT 20"):
            print(f"  {r['id']}  {r['created_at']}  {r['name']}  {r['job_slug']}  {r['status']}")
        return

    if a[0] == '--latest':
        rows = d1("SELECT id FROM applications ORDER BY created_at DESC LIMIT 1")
        if not rows:
            sys.exit('目前沒有任何應徵資料')
        app_id = rows[0]['id']
    else:
        app_id = a[0]

    esc = app_id.replace("'", "''")
    apps = d1(f"SELECT * FROM applications WHERE id = '{esc}'")
    if not apps:
        sys.exit(f'找不到 {app_id}')
    app = apps[0]

    jobs = d1(f"SELECT * FROM jobs WHERE slug = '{app['job_slug'].replace(chr(39), chr(39)*2)}'")
    job = jobs[0] if jobs else None

    resume_text, resume_source, resume_note = None, None, None

    if app.get('resume_file_id'):
        fs = d1(f"SELECT id, filename, mime, content_b64, chunks, text_content, parse_note "
                f"FROM files WHERE id = '{app['resume_file_id']}'")
        if fs:
            f = fs[0]
            if f.get('text_content'):
                resume_text, resume_source = f['text_content'], '上傳檔案'
            elif f.get('parse_note'):
                resume_note = f['parse_note']
            else:
                # 排程還沒跑到。即時補抽，不要讓面談等下一輪排程。
                b64 = f.get('content_b64')
                if not b64 and f.get('chunks'):
                    parts = d1(f"SELECT b64 FROM file_chunks WHERE file_id = '{f['id']}' ORDER BY idx ASC")
                    b64 = ''.join(p['b64'] for p in parts)
                resume_text = extract_text(
                    base64.b64decode(b64), f['filename'], f['mime']) if b64 else None
                resume_source = '上傳檔案（即時抽取）'

    # 沒有上傳檔案時看候選人貼的網址
    if not resume_text and app.get('resume_url_text'):
        resume_text = app['resume_url_text']
        # 來源要講準：阿財會照著這個描述跟候選人對話。
        # 作品集網站抓下來的是「網站上寫的東西」，不是一份履歷，
        # 講成「您的履歷」會讓候選人覺得我們在唬爛。
        import urllib.parse as _up
        _host = _up.urlparse(app.get('resume_url') or '').netloc.lower()
        _file_hosts = ('drive.google.com', 'docs.google.com', 'dropbox.com',
                       'www.dropbox.com', 'dl.dropboxusercontent.com', '1drv.ms',
                       'onedrive.live.com', 'mega.nz', 'box.com', 'app.box.com',
                       'icloud.com', 'www.icloud.com')
        resume_source = '雲端連結' if _host in _file_hosts else f'個人作品集網站（{_host}）'
    elif not resume_text and app.get('resume_url_note') not in (None, 'ok'):
        resume_note = app.get('resume_url_note')

    if resume_text:
        # 空位元組會讓 subprocess 拒絕整個 prompt，一定要在這裡就清掉
        resume_text = ''.join(c for c in resume_text if c in '\n\t' or ord(c) >= 32)
    if resume_text and resume_text.startswith('【無法抽取'):
        resume_note, resume_text = resume_text, None

    out = {
        'application': {k: v for k, v in app.items()
                        if k not in ('resume_file_id', 'resume_url_text')},
        'job': job,
        'resume_text': resume_text,
        'resume_source': resume_source,
        # 這個旗標是給面談用的：False 就不准說「您的履歷我看過了」
        'resume_readable': bool(resume_text),
        'resume_note': resume_note,
        'resume_url': app.get('resume_url'),
    }
    if not resume_text:
        out['開場提醒'] = ('沒有可讀的履歷內容，不要說「您的履歷我看過了」。'
                          '改成：「您這邊的履歷我這裡看不到內容，'
                          '方便先請您介紹一下經歷嗎？」')
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
