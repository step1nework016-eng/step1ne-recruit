#!/usr/bin/env python3
"""履歷 PDF → 人才池 —— 把一份履歷變成阿財看得到的人選。

為什麼要有這支（2026-08-20 重寫）：
    原本這件事由 ~/clawd/tools/linkedin-pdf/ 底下三支腳本做，
    但它們把資料 PATCH 到 `api-hr.step1ne.com`（舊的 step1ne-headhunter-system）。
    那套系統的對外通道已經不存在（530）、本機也找不到原始碼，
    更關鍵的是：**它跟阿財完全不相通**——履歷存進去也進不了面談流程。
    所以不是修通道，是把落地點換成現行的 D1 人才池。

⚠️ 這支**不碰 LinkedIn 帳號、不自動下載任何東西**。
   Jacky 2026-03-17 明令禁止用 LinkedIn 帳號做自動化，
   舊的 linkedin_batch_v3.sh 第一行就是永久停用。
   這支只處理「已經在你手上的 PDF」——候選人寄來的、顧問自己存的、
   人力銀行下載的都可以。要餵什麼進來由人決定。

🚨 進的是「人才池」不是「應徵者」：
   跟爬蟲同一個原則——沒投履歷給我們、沒同意個資利用的人，
   存在 sourced_candidates，不混進 applications。
   （候選人自己寄履歷來應徵的，走 /apply，不要用這支。）

用法：
    python3 resume_to_pool.py 履歷.pdf
    python3 resume_to_pool.py ~/Downloads/履歷資料夾/ --job finance-accounting-insurance-dispatch
    python3 resume_to_pool.py 履歷.pdf --dry        # 只解析看結果，不寫進去
"""
import datetime
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = 'claude-sonnet-5'
TIMEOUT = 240
API = 'https://step1ne-recruit-api.aiagentg888.workers.dev'

_BAN = ('Task,Bash,Glob,Grep,Read,Edit,Write,NotebookEdit,WebFetch,WebSearch,'
        'AskUserQuestion,TodoWrite,BashOutput,KillShell,SlashCommand,Skill,'
        'Agent,Artifact,Monitor,CronCreate,CronDelete,CronList')
NO_TOOLS = ['--disallowed-tools', _BAN, '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


D = _daemon()
d1, q = D.d1, D.q


def admin_token():
    for name in ('recruit.env', 'cf.env', 'tg.env'):
        p = os.path.expanduser(f'~/.config/workflow-os/{name}')
        if not os.path.exists(p):
            continue
        for line in open(p, encoding='utf-8'):
            if line.startswith(('RECRUIT_ADMIN_TOKEN=', 'ADMIN_TOKEN=')):
                return line.strip().split('=', 1)[1].strip().strip('"\'')
    raise RuntimeError('找不到後台權杖（RECRUIT_ADMIN_TOKEN）')


def pdf_text(path):
    """pdftotext 抽文字。-layout 保留欄位排版，履歷的公司／日期常是分欄的，
    不保留排版會把「2020-2023」跟公司名接成一團，年資就算錯了。"""
    r = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        r = subprocess.run(['pdftotext', path, '-'], capture_output=True, text=True)
    return r.stdout


PROMPT = '''你要把一份履歷的純文字內容整理成結構化資料，給獵頭顧問使用。

【履歷內容】
{text}

【硬性要求】
1. **只寫履歷上真的有的**。沒寫的欄位就給空字串或空陣列，
   **不准推測、不准補完**——顧問會拿這份資料去跟候選人對話，
   猜錯一個公司名或年資，對話當場就會露餡。
2. 年份請照履歷原文抄，不要自己換算民國／西元。
3. `summary` 寫給顧問看的三句話：他現在在哪做什麼、最值得注意的一段經歷、
   有沒有明顯的疑問（例如空窗、頻繁轉職、職稱與內容不符）。
4. ⚠️ 不准寫年齡、性別、婚育、國籍、外貌、照片描述（就業服務法第 5 條）。
   就算履歷上有，也不要抄進來——這份資料會進系統，寫進去就會被拿去用。
5. 全部繁體中文（公司名、技術名詞維持原文）。

【只輸出這個 JSON，不要有其他文字】
{{
  "name": "姓名",
  "current_position": "現職職稱",
  "current_company": "現職公司",
  "location": "居住地或工作地，履歷沒寫就空字串",
  "email": "履歷上的 email，沒有就空字串",
  "phone": "履歷上的電話，沒有就空字串",
  "linkedin_url": "履歷上的 LinkedIn 網址，沒有就空字串",
  "skills": ["技能1", "技能2"],
  "education": [{{"school": "", "degree": "", "major": "", "years": ""}}],
  "work_history": [
    {{"company": "", "title": "", "start": "YYYY-MM 或 YYYY", "end": "YYYY-MM／至今",
      "highlights": "這段在做什麼，一句話"}}
  ],
  "languages": "外語能力，履歷有寫才填",
  "summary": "給顧問看的三句話"
}}'''


def _extract_json(text):
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


def _year(v):
    m = re.search(r'(19|20)\d{2}', str(v or ''))
    return int(m.group(0)) if m else None


def stability(work_history):
    """穩定度：平均每份工作待多久、有沒有短期跳槽。

    ⚠️ 這個**用 Python 算，不叫模型算**。模型算數字會出錯，而且錯了看不出來；
       年資與平均年限是顧問拿去跟客戶談的數字，錯一次就是信任問題。
       算不出來就回 None——寧可沒有，不要給一個錯的。
    """
    # 「迄今」「目前」「在職」都要算成到今年——2026-08-20 實測漏掉「迄今」，
    # 黃志遠 6 年年資被算成 2 年，而且 bio 裡同時出現「約 4 年」與「2 年」自打嘴巴。
    spans, short = [], 0
    now = datetime.date.today().year
    for w in work_history or []:
        a = _year(w.get('start'))
        # 有寫年份就以年份為準；沒年份才看是不是「迄今」這類還在職的寫法。
        # 反過來寫會讓「2021 -」這種結尾被當成還在職，年資直接多算五年。
        endtxt = str(w.get('end') or '').strip()
        b = _year(endtxt)
        if not b and (not endtxt or re.search(
                r'至今|迄今|現在|目前|在職|present|current|now', endtxt, re.I)):
            b = now
        if a and b and b >= a:
            spans.append(b - a)
            if b - a < 2:
                short += 1
    if not spans:
        return None
    avg = round(sum(spans) / len(spans), 1)
    return {'jobs': len(spans), 'avg_years': avg, 'short_stints': short,
            'total_years': sum(spans),
            'note': ('平均每份工作不到兩年，轉職頻率偏高，面談要問原因'
                     if avg < 2 else '')}



# 明顯是測試／範例資料的特徵。踩到就不寫進人才池。
# 2026-08-20 真實事故：測試新工具時隨手拿 ~/Downloads 裡一份 PDF 來跑，
# 那是 7/29 為了測 BIM 流程用 ReportLab 生的假履歷（信箱 @example.com、
# 電話編的）。它就這樣進了人才池，跟真人選長得一模一樣，
# Jacky 看到還說「感覺可以聯繫看看」——差一步就真的打了那支不存在的電話。
# 池子的價值就在於「裡面每個人都是真的」，混一筆假的進去，整池都不能信。
FAKE_MAIL = ('@example.com', '@example.org', '@example.net', '@test.com',
             '@sample.com', '@mail.com', '@email.com')
FAKE_NAME = ('測試', 'test', 'sample', '範例', '王小明', '陳大明', 'John Doe')


def looks_fake(info, path):
    hits = []
    mail = (info.get('email') or '').lower()
    if any(mail.endswith(d) for d in FAKE_MAIL):
        hits.append(f'信箱是保留給範例用的網域（{mail}）')
    name = (info.get('name') or '')
    if any(k.lower() in name.lower() for k in FAKE_NAME):
        hits.append(f'姓名看起來是範例（{name}）')
    try:
        r = subprocess.run(['pdfinfo', path], capture_output=True, text=True, timeout=15)
        # ReportLab／FPDF 是程式生成 PDF 的套件。真人的履歷來自 Word、Pages、
        # Canva 或人力銀行匯出，不會是這些。
        for producer in ('ReportLab', 'FPDF', 'wkhtmltopdf'):
            if producer.lower() in r.stdout.lower():
                hits.append(f'PDF 是程式生成的（{producer}），不是真人匯出的履歷')
                break
    except Exception:
        pass
    return hits


def to_payload(info, path, job_slug):
    li = (info.get('linkedin_url') or '').strip()
    if li and not li.startswith('http'):
        li = ''
    # 沒有 LinkedIn 就用「姓名＋檔案內容」做一個穩定的識別碼——
    # 後端用 (source, source_url) 去重，沒有網址的人會被直接跳過。
    src_url = li or ('resume://' + hashlib.sha1(
        ((info.get('name') or '') + os.path.basename(path)).encode()).hexdigest()[:16])
    st = stability(info.get('work_history'))
    bio = []
    if info.get('summary'):
        bio.append(info['summary'])
    if st:
        bio.append(f"年資約 {st['total_years']} 年／{st['jobs']} 份工作／"
                   f"平均 {st['avg_years']} 年" + (f"／{st['note']}" if st['note'] else ''))
    if info.get('languages'):
        bio.append(f"外語：{info['languages']}")
    return {
        'source': 'resume',
        'source_url': src_url,
        'name': info.get('name') or '',
        'title': info.get('current_position') or '',
        'company': info.get('current_company') or '',
        'location': info.get('location') or '',
        'email': info.get('email') or '',
        'linkedin_url': li,
        'skills': info.get('skills') or [],
        'bio': ' ｜ '.join(bio),
        'job_slug': job_slug or '',
        # 下面這些後端不會建欄位，但整包會存進 raw_json，顧問點開看得到
        'work_history': info.get('work_history') or [],
        'education': info.get('education') or [],
        'stability': st,
        'phone': info.get('phone') or '',
        'resume_file': os.path.basename(path),
    }


def push(payload):
    """先查有沒有這個人，有就補資料、沒有才新增。

    為什麼不直接丟匯入端點：那支只會 INSERT，撞到唯一索引就當成重複跳過。
    可是履歷解析出來的東西（工作經歷、學歷、穩定度）比爬蟲抓到的完整得多——
    爬蟲先撈到的那筆只有 GitHub bio，被跳過就等於這份履歷白解析了。
    """
    rows = d1(f"SELECT id, bio FROM sourced_candidates "
              f"WHERE source_url = {q(payload['source_url'])} LIMIT 1")
    if rows:
        cid = rows[0]['id']
        d1(f"UPDATE sourced_candidates SET "
           f"name = COALESCE(NULLIF(name,''), {q(payload['name'])}), "
           f"headline = {q(payload['title'])}, "
           f"company = {q(payload['company'])}, "
           f"location = COALESCE(NULLIF(location,''), {q(payload['location'])}), "
           f"email = COALESCE(NULLIF(email,''), {q(payload['email'])}), "
           f"skills = {q(', '.join(payload['skills']) if isinstance(payload['skills'], list) else payload['skills'])}, "
           f"bio = {q(payload['bio'])}, "
           f"raw_json = {q(json.dumps(payload, ensure_ascii=False)[:12000])} "
           f"WHERE id = {q(cid)}")
        return 'updated'
    req = urllib.request.Request(
        f'{API}/admin/sourced/import',
        data=json.dumps({'candidates': [payload], 'job_slug': payload.get('job_slug') or ''},
                        ensure_ascii=False).encode('utf-8'),
        # ⚠️ 一定要帶 user-agent：Cloudflare 會用 error code 1010 擋掉
        #    Python 預設的 Python-urllib UA，同一個權杖用 curl 卻是 200，
        #    很容易誤判成權杖過期而白繞一圈。
        headers={'content-type': 'application/json',
                 'authorization': f'Bearer {admin_token()}',
                 'user-agent': 'step1ne-resume/1.0'})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return 'added' if r.get('added') else ('dup' if r.get('dup') else 'failed')


def handle(path, job_slug, dry=False):
    text = pdf_text(path)
    if len(text.strip()) < 80:
        log(f'❌ {os.path.basename(path)}：抽不到文字（可能是掃描檔或圖片 PDF），跳過')
        return None
    prompt = PROMPT.format(text=text[:18000])
    before = D._snapshot_session_files()
    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', MODEL,
                        *NO_TOOLS, '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True,
                       env=D.env_with_cf(), timeout=TIMEOUT)
    D.log_token_usage(f'resume:{os.path.basename(path)}', 'resume', prompt, before)
    info = _extract_json(r.stdout)
    if not info or not info.get('name'):
        log(f'❌ {os.path.basename(path)}：解析不出來。回覆開頭：{(r.stdout or r.stderr)[:150]}')
        return None
    fake = looks_fake(info, path)
    if fake and '--force' not in sys.argv:
        log(f'⛔ {os.path.basename(path)}：看起來是測試／範例履歷，不寫進人才池')
        for h in fake:
            log(f'    ・{h}')
        log('    確定要收就加 --force')
        return None
    payload = to_payload(info, path, job_slug)
    st = payload.get('stability')
    log(f"✅ {payload['name']}｜{payload['title'] or '（無現職職稱）'}"
        f"｜{len(payload['work_history'])} 段經歷"
        + (f"｜平均 {st['avg_years']} 年" if st else '｜年資算不出來'))
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return payload
    res = push(payload)
    log({'added': '　→ 已新增進人才池', 'updated': '　→ 池子裡已有這個人，已補上履歷資料',
         'dup': '　→ 重複', 'failed': '　→ ⚠️ 寫入失敗'}[res])
    return payload


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--dry' in sys.argv
    job = ''
    if '--job' in sys.argv:
        i = sys.argv.index('--job')
        if i + 1 < len(sys.argv):
            job = sys.argv[i + 1]
            args = [a for a in args if a != job]
    if not args:
        print(__doc__)
        return
    paths = []
    for a in args:
        if os.path.isdir(a):
            paths += sorted(glob.glob(os.path.join(a, '*.pdf')))
        elif a.lower().endswith('.pdf'):
            paths.append(a)
    if not paths:
        log('沒有找到 PDF')
        return
    log(f'要處理 {len(paths)} 份履歷' + (f'（歸到職缺 {job}）' if job else '') + ('（試跑，不寫入）' if dry else ''))
    ok = 0
    for p in paths:
        try:
            if handle(p, job, dry):
                ok += 1
        except Exception as e:
            log(f'❌ {os.path.basename(p)}：{e}')
    log(f'完成：{ok}/{len(paths)}')


if __name__ == '__main__':
    main()
