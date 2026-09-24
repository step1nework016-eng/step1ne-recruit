#!/usr/bin/env python3
"""收件 → 擬 JD → 產出送審訊息（Telegram 三顆按鈕）。

這一支是「顧問自己新增職缺」流程的中段。整條路長這樣：

  ① 顧問在後台表單放資料（PDF／文字／圖片／連結）＋選服務線＋選客戶對象
     → step1ne-recruit Worker 的 /admin/job-intake 收下，原始檔存進 files/file_chunks
  ② ★ 這一支 ★：把收件單交給 step1ne 總指揮擬 JD，跑禁刊過濾器，
     產出送審訊息推到 Telegram，附三顆按鈕
  ③ 顧問在 Telegram 按「✅ 核准發布」→ Worker 的 /telegram/webhook 把狀態改成 approved
  ④ publish_approved.py 才真的產生職缺頁、更新職缺專區與 sitemap、寫 D1、通知阿財

⚠️ 這一支**永遠不會發布任何東西**。它最多把訊息推到 Telegram。
   會動到網站的只有第 ④ 步，而第 ④ 步只處理 status='approved' 的收件單。

用法：
    # 離線測試（不碰 D1，用本機 JSON 當收件單，訊息只印出來不推 Telegram）
    python3 draft_job.py --local 收件單.json --dry

    # 正式：處理 D1 裡某一筆收件單，擬完推到 Telegram 等顧問決定
    python3 draft_job.py --intake <intake_id>

    # 顧問按了「重寫」之後，帶著他的意見重擬
    python3 draft_job.py --intake <intake_id> --rewrite-note "薪資帶寫錯，是 4.5 萬起"

    # 一次處理所有還沒擬的收件單
    python3 draft_job.py --all
"""
import os, sys, re, json, uuid, argparse, subprocess, datetime, importlib.util, base64, textwrap
import shutil
import urllib.request, urllib.parse   # ⚠️ 要在模組層級，函式內 import 的話別的函式抓不到

# Windows 上 claude CLI 是 claude.cmd，subprocess.run(['claude',...]) 不帶副檔名
# 會 FileNotFoundError，先解出實際路徑（macOS/Linux 不受影響）。
CLAUDE_BIN = shutil.which('claude') or 'claude'

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# 2026-09-24：擬稿搬到 WSL2。WSL2 沒有 Mac 的技能包資料夾，所以 repo 裡放一份快照
# （jobintake/job-posting-skill/）。Mac 有原版就用原版（改技能包立刻生效），
# 沒有才用快照。⚠️ 改了原版技能包要記得同步快照：
#   cp ~/claude-projects/工作流程技能包/recruiting-workflow/step1ne-job-posting/SKILL.md jobintake/job-posting-skill/
_SKILL_MAC = os.path.expanduser(
    '~/claude-projects/工作流程技能包/recruiting-workflow/step1ne-job-posting/SKILL.md')
SKILL = _SKILL_MAC if os.path.exists(_SKILL_MAC) else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'job-posting-skill', 'SKILL.md')
WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work')   # 跟著 repo 走，兩台都對

sys.path.insert(0, HERE)
# ⚠️ 2026-09-24 加：interview_daemon.py 開頭 import autoupdate（在上一層），
#    用 `python3 這支.py` 跑時上一層不在搜尋路徑，一載入就 ModuleNotFoundError——
#    顧問新增職缺的收件單因此全部卡在「排隊中」沒人擬。
sys.path.insert(0, ROOT)
import publishing_filters as PF          # noqa: E402
import relation_rules as RR              # noqa: E402

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)              # 借用它的 d1() / q() / env_with_cf()

TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')

# 擬 JD 用 opus——這份東西要上公開網站、而且要過就服法這一關，
# 不是隨便寫寫。對談類才用 sonnet。
# ⚠️ 一定要寫全名：這台機器上 `--model opus` 會解析成舊的 opus-4-6。
MODEL = 'claude-opus-5'
TIMEOUT_SEC = 1800


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


# ── Telegram ──
def tg_conf():
    conf = {}
    if os.path.exists(TG_ENV):
        for line in open(TG_ENV, encoding='utf-8'):
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                conf[k] = v
    return conf


def tg_send(text, buttons, intake_id):
    """把送審訊息推到 step1ne 群組。回傳 message_id（推失敗回 None）。"""
    import urllib.request
    c = tg_conf()
    if not c.get('TG_BOT_TOKEN') or not c.get('TG_CHAT_ID'):
        log('⚠️ 找不到 Telegram 設定，訊息沒有推出去')
        return None
    body = {
        'chat_id': c['TG_CHAT_ID'],
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
        'reply_markup': {'inline_keyboard': buttons},
    }
    if c.get('TG_THREAD_ID'):
        body['message_thread_id'] = int(c['TG_THREAD_ID'])
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{c["TG_BOT_TOKEN"]}/sendMessage',
        data=json.dumps(body).encode('utf-8'),
        headers={'content-type': 'application/json'})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
        return (r.get('result') or {}).get('message_id')
    except Exception as e:
        log(f'⚠️ Telegram 推送失敗：{e}')
        return None


def buttons_for(intake_id):
    """三顆按鈕。callback_data 上限 64 bytes，所以只帶動作與 id。"""
    return [
        [{'text': '✅ 核准發布', 'callback_data': f'job_ok:{intake_id}'}],
        [{'text': '✏️ 重寫', 'callback_data': f'job_rw:{intake_id}'},
         {'text': '❌ 拒絕發布', 'callback_data': f'job_no:{intake_id}'}],
    ]


# ── 收件單 ──
def load_intake_local(path):
    return json.load(open(path, encoding='utf-8'))


def load_intake_d1(intake_id):
    rows = D.d1(f"SELECT * FROM job_intakes WHERE id = {D.q(intake_id)}")
    if not rows:
        sys.exit(f'找不到收件單：{intake_id}')
    intake = rows[0]
    intake['files'] = D.d1(
        f"""SELECT jf.file_id, jf.kind, f.filename, f.mime, f.size, f.chunks
              FROM job_intake_files jf JOIN files f ON f.id = jf.file_id
             WHERE jf.intake_id = {D.q(intake_id)}""")
    return intake


def fetch_files(intake, workdir):
    """把顧問上傳的原始檔從 D1 取回本機，交給總指揮讀。

    ⚠️ 只是「取一份副本來讀」——D1 裡的原始檔不動、不刪。
    顧問事後要回溯「客戶當初給的是什麼」，靠的就是那份原始檔。
    """
    out = []
    for f in intake.get('files') or []:
        if f.get('local_path'):                 # 離線測試用：收件單直接給本機路徑
            out.append(f['local_path'])
            continue
        # ⚠️ 2026-09-24 修：2026-09-03 起新上傳的檔案存 R2（files.storage='r2'），
        #    file_chunks 是空的——原本這裡直接查 file_chunks，9/3 之後顧問上傳的
        #    JD 檔全部會被「取不到內容，跳過」，總指揮等於沒看到檔案。
        #    改打 Worker 的 /admin/file/:id（裡面走 fileB64()，R2／D1 兩種都認得），
        #    跟 interview_daemon._resume_attachment 同一個解法。
        content = None
        tok = D._admin_token() if hasattr(D, '_admin_token') else None
        if tok:
            try:
                req = urllib.request.Request(
                    'https://step1ne-backoffice-worker.aiagentg888.workers.dev/admin/file/'
                    + urllib.parse.quote(f['file_id']),
                    headers={'authorization': f'Bearer {tok}', 'user-agent': 'step1ne-jobintake/1.0'})
                with urllib.request.urlopen(req, timeout=60) as r:
                    content = r.read()
            except Exception as e:
                log(f'⚠️ {f.get("filename")} 從 Worker 下載失敗：{e}，改查舊的切塊')
        if not content:
            rows = D.d1(f"SELECT b64 FROM file_chunks WHERE file_id = {D.q(f['file_id'])} ORDER BY idx ASC")
            b64 = ''.join(r['b64'] for r in rows)
            content = base64.b64decode(b64) if b64 else None
        if not content:
            log(f'⚠️ {f.get("filename")} 取不到內容，跳過')
            continue
        name = os.path.basename(f.get('filename') or f['file_id'])
        p = os.path.join(workdir, name)
        open(p, 'wb').write(content)
        out.append(p)
        log(f'   取回原始檔：{name}（{f.get("size")} bytes）')
    return out


def office_to_text(path):
    """Word／Excel 轉純文字，讓總指揮擬 JD 讀得到。

    2026-09-24 加：顧問要能直接丟客戶給的 Word／Excel JD。總指揮的 Read 工具
    讀得懂 PDF、圖片、md、txt、csv，但 docx／xlsx 是壓縮檔，讀進去是亂碼。
    ⚠️ 只用 Python 內建模組拆（docx／xlsx 本質是 zip＋xml），
       因為這支同時在 Mac 跟 WSL2 跑，不能假設哪台有裝 python-docx／openpyxl。
    舊版 .doc／.xls 是二進位格式，只有 Mac 內建的 textutil 轉得了 .doc；
    轉不了就回 None，呼叫端照實記 log，不要假裝讀到了。
    """
    import zipfile, re as _re, html as _h
    low = path.lower()
    try:
        if low.endswith('.docx'):
            with zipfile.ZipFile(path) as z:
                x = z.read('word/document.xml').decode('utf-8', 'replace')
            x = _re.sub(r'</w:p>', '\n', x)
            x = _re.sub(r'<w:tab/>', '\t', x)
            return _h.unescape(_re.sub(r'<[^>]+>', '', x)).strip()
        if low.endswith('.xlsx'):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                shared = []
                if 'xl/sharedStrings.xml' in names:
                    sx = z.read('xl/sharedStrings.xml').decode('utf-8', 'replace')
                    for si in _re.findall(r'<si>(.*?)</si>', sx, _re.S):
                        shared.append(_h.unescape(''.join(_re.findall(r'<t[^>]*>(.*?)</t>', si, _re.S))))
                out = []
                for n in sorted(x for x in names if _re.match(r'xl/worksheets/sheet\d+\.xml$', x)):
                    sx = z.read(n).decode('utf-8', 'replace')
                    out.append(f'【{n.split("/")[-1][:-4]}】')
                    for row in _re.findall(r'<row[^>]*>(.*?)</row>', sx, _re.S):
                        cells = []
                        for attrs, body in _re.findall(r'<c([^>]*)>(.*?)</c>', row, _re.S):
                            v = _re.search(r'<v>(.*?)</v>', body, _re.S)
                            t = _re.search(r'<t[^>]*>(.*?)</t>', body, _re.S)
                            if 't="s"' in attrs and v:
                                i = int(v.group(1))
                                cells.append(shared[i] if i < len(shared) else '')
                            elif t:
                                cells.append(_h.unescape(t.group(1)))
                            elif v:
                                cells.append(v.group(1))
                        if any(c.strip() for c in cells):
                            out.append('\t'.join(cells))
            return '\n'.join(out).strip()
        if low.endswith('.doc') and shutil.which('textutil'):
            r = subprocess.run(['textutil', '-convert', 'txt', '-stdout', path],
                               capture_output=True, text=True, timeout=60)
            return (r.stdout or '').strip() or None
    except Exception as e:
        log(f'⚠️ {os.path.basename(path)} 轉文字失敗：{e}')
    return None


def expand_office_files(files):
    """把 Word／Excel 旁邊各放一份 .txt 給總指揮讀，原檔保留。回 (新清單, 抽出的文字)。"""
    out, texts = [], []
    for p in files:
        out.append(p)
        if not p.lower().endswith(('.docx', '.xlsx', '.doc', '.xls')):
            continue
        t = office_to_text(p)
        if t:
            tp = p + '.txt'
            open(tp, 'w', encoding='utf-8').write(t)
            out.append(tp)
            texts.append(f'【{os.path.basename(p)} 的內容】\n{t}')
            log(f'   {os.path.basename(p)} 已轉成文字（{len(t):,} 字）')
        else:
            log(f'⚠️ {os.path.basename(p)} 讀不出文字（舊版 .xls／.doc 請另存成 .xlsx／.docx 或 PDF）')
    return out, '\n\n'.join(texts)


def source_text(intake):
    """把顧問給的所有文字型來源串起來——禁刊過濾器要掃的就是這一份。"""
    parts = []
    if intake.get('raw_text'):
        parts.append('【顧問輸入的文字】\n' + intake['raw_text'])
    if intake.get('source_url'):
        parts.append('【職缺連結】\n' + intake['source_url'])
    if intake.get('note'):
        parts.append('【顧問補充交代】\n' + intake['note'])
    return '\n\n'.join(parts)


# ── 擬 JD ──
JSON_SHAPE = '''{
  "slug": "英數與連字號，格式 <領域>-<職務>-<地點>-<區隔>，要能自我說明",
  "title": "對外職稱（同站已有同名職缺時，用班別／薪資帶／僱傭型態／外語加給／地點區隔）",
  "subtitle": "一行副標",
  "page_title": "<title> 標籤用，結尾接｜Step1ne",
  "description": "meta description，120–160 字",
  "keywords": "逗號分隔",
  "og_title": "", "og_desc": "",
  "intro": "導言一段",
  "tags": ["標籤"],
  "locality": "區", "region": "縣市",
  "locations": "完整地址或地區",
  "client_name": "真實公司名（只寫進 D1 內部欄位；client_named=0 時不得出現在任何對外欄位）",
  "employment": ["FULL_TIME"],
  "salary_min": 0, "salary_max": 0,
  "salary_unit": "MONTH 或 YEAR——salary_min/max 是月薪還是年薪（2026-09-24：以前寫死 MONTH，年薪 400 萬被存成月薪 400 萬）",
  "salary_note": "薪資補充（會給阿財跟社群發文看）；不是台幣時一定要寫幣別。只給顧問看的內容一律用「⚠️ 內部限閱：」開頭，系統才會擋掉不外流",
  "years_min": 0,
  "must_skills": "分號分隔",
  "benefits": "",
  "notes": "內部備註",
  "spec": [["欄位", "值", "補充（可省略）"]],
  "duties": [{"h": "小標", "items": ["條目"]}],
  "must": ["必要條件"],
  "plus": ["加分條件"],
  "why": [{"h": "小標", "p": "說明"}],
  "faq": [["問題", "回答"]],
  "industry": "產業別（職缺卡用）",
  "card_meta": ["卡片上的短標"],
  "card_desc": "卡片說明",
  "track": "dispatch / fulltime / executive",
  "cat": "職缺專區的分類鍵"
}'''


def build_prompt(intake, files, src, source_hits, rewrite_note, workdir):
    rel = RR.apply_relation(intake['client_relation'])
    sl = RR.apply_service_line(intake['service_line'])
    skill = open(SKILL, encoding='utf-8').read()

    named_rule = (
        '客戶已簽約，**可以**在頁面上具名寫出公司名稱。'
        if rel['client_named'] == 1 else
        '⚠️ 客戶尚未簽約，**絕對不可以**在任何對外欄位出現公司名稱、'
        '公司簡稱、集團名或任何足以指認的線索（統編、地址門牌、官網、產品名）。'
        '一律改用產業描述。client_name 欄位仍要填真實公司名——那一欄只進內部資料庫，不進頁面。')

    hits_txt = PF.format_report(source_hits, title='原始資料的禁刊掃描') if source_hits else '（原始資料沒有命中禁刊規則）'

    # 2026-09-24 提速：Word／Excel 只給轉好的 .txt，不給原檔——
    # 給了原檔，總指揮會自己解壓縮比對一遍，白花一兩分鐘（築樂那張就是這樣）。
    show = [p for p in files if not (p.lower().endswith(('.docx', '.xlsx', '.doc', '.xls')) and (p + '.txt') in files)]
    files_txt = ('顧問上傳的原始檔（請用 Read 工具逐一讀完，PDF 用 pages 參數，圖片直接讀；'
                 'Word／Excel 已經轉成 .txt，內容完整，不用再解壓縮原檔）：\n'
                 + '\n'.join(f'  - {p}' for p in show)) if files else '（顧問沒有上傳檔案）'

    # 2026-09-24 提速：同名職缺比對要的站上職缺清單，直接查好給它。
    # 原本是總指揮自己跑 wrangler 查 D1，每次多花一兩分鐘。
    try:
        existing = D.d1("SELECT slug, title, locations, status FROM jobs ORDER BY title")
        jobs_txt = '\n'.join(f"  - {j.get('title')}｜{j.get('locations') or ''}｜{j.get('slug')}｜{j.get('status')}"
                              for j in existing) or '（站上目前沒有職缺）'
    except Exception:
        jobs_txt = '（查不到，請你自己用 wrangler 查 D1 的 jobs 表）'

    # 顧問填的職缺名稱（2026-09-24 加）。先組好字串，不要把含引號的運算式塞進 f-string——
    # WSL2 的 Python 可能比 3.12 舊，舊版不允許。
    title_line = ('- 顧問填的職缺名稱：' + str(intake['title_hint'])
                  + '（對外標題以它為準；可以加地點、班別等區隔字，但不要換成別的職務）\n') if intake.get('title_hint') else ''

    rewrite_txt = (f'\n\n## ⚠️ 這是重寫\n顧問看過上一版之後的意見，**這一版一定要照著改**：\n{rewrite_note}\n'
                   if rewrite_note else '')

    return f'''你現在要做的事：把顧問送進來的一份職缺原始資料，擬成可以上 step1ne.com 的職缺規格 JSON。

完整規範在下面的「職缺上架工作流程」技能文件裡，**照它跑，不要自己發明流程**。
特別是 Phase 2 禁刊過濾器、Phase 3 判服務線、Phase 4 改寫 JD、Phase 4.5 同名職缺區隔。

## 顧問已經選好的（不要自己改，也不要問）
{title_line}
- 服務線：{sl['label']}（{sl['note']}）
- 客戶對象：{RR.RELATION[intake['client_relation']]['label']}
- client_named = {rel['client_named']}　→　{named_rule}
- ai_disclosure = {rel['ai_disclosure']}
- 報告品牌：{rel['brand_mode']}　{RR.RELATION[intake['client_relation']]['note']}

## 原始資料

{files_txt}

顧問輸入的文字：
```
{src or '（沒有文字，資料全在上面的檔案裡）'}
```

## 站上現有的職缺（已經幫你從資料庫查好，Phase 4.5 同名比對直接用這份，不要再自己查資料庫）

{jobs_txt}

## 規則式禁刊掃描已經先跑過一遍，結果如下

{hits_txt}

上面每一條你都要處理掉——**不是刪掉整句，是改寫成合法且對求職者有用的說法**。
（例：「限 35 歲以下」不是刪掉了事，而是想清楚客戶真正在意什麼，
如實描述工作型態讓求職者自己判斷。）
規則掃不到但你覺得有問題的，也一併處理，並在報告裡講。
{rewrite_txt}

## 產出

寫兩個檔案，**不要輸出到終端機以外的地方，也不要做任何部署動作**：

1. `{{WORK}}/spec.json`　—　職缺規格 JSON，形狀如下（沒有的欄位就省略，
   **絕對不要用合理猜測把欄位填滿**，寧可頁面短一點）：

{JSON_SHAPE}

2. `{{WORK}}/report.md`　—　給顧問看的說明，三段：
   - 「我擋掉了什麼」：每條禁刊命中 ＋ 你改寫成什麼 ＋ 為什麼
   - 「待顧問確認」：來源沒給、但求職者一定會問的欄位
   - 「服務線與同名職缺」：判定理由；站上若已有同職稱，你用什麼維度區隔

3. `{{WORK}}/review.json`　—　給老闆在手機上**一眼看完**的摘要（他有 ADHD，這份要短）：

{{
  "decisions": [
    {{"q": "一個問句，老闆回答是／否或二選一就能決定", "why": "一句話：不決定的話頁面會錯在哪"}}
  ],
  "changes": [
    {{"what": "幾個字的標題，例：國籍門檻", "from": "原文（最多 40 字）", "to": "改成什麼（最多 40 字）"}}
  ]
}}

   - decisions **最多 3 件**，只挑「不處理就不能上線、或上線會錯」的事（例：幣別、客戶要不要具名、薪資沒給）。
     次要的待確認事項留在 report.md，不要塞進來。沒有就給空陣列。
   - changes 列你把原文改掉的地方，一條一行，不要解釋法條。沒有就給空陣列。

## 硬規則

- 不要 git push、不要跑 publish_job.py、不要動 {os.path.expanduser('~/claude-projects/step1ne-stopgap-site')} 底下任何檔案。
  你的工作到寫出上面兩個檔案為止。
- 不要加 FAQPage schema。
- **薪資的組成細節不上公開頁面**（例：月薪內含幾小時固定加班費、年終攤提進月薪、獎金怎麼算）。
  頁面只寫總額區間；組成細節寫進 `notes`，標註「對外不寫，顧問電洽說明」。
  FAQ 問到實際拿多少，就回答「薪資怎麼組成，顧問在電話初談時會一次說清楚」。（2026-09-24 Jacky 指示）
- **薪資不是台幣時**（日圓、美元…），`salary_note` 一定要寫「以○○計價：月薪 X～Y 萬○○（不是台幣）」。
  資料庫的 salary_min/max 只有數字沒有幣別，阿財跟社群發文讀的是資料庫，沒寫就會被當成台幣講出去。
- client_code 不得出現在任何對外欄位。
- 全部繁體中文。

---

# 職缺上架工作流程（技能文件全文）

{skill}
'''.replace('{WORK}', workdir)


def run_commander(prompt, workdir):
    """交給 step1ne 總指揮跑一次。

    --setting-sources '' 是必要的：全域 CLAUDE.md 只有「先開 agentacct section」
    那幾行，對 claude -p 一樣生效，短 prompt 會被它整個蓋過去（2026-08-07 實測）。
    但拿掉 setting-sources 就連 permissions.allow 一起沒了，所以要配
    bypassPermissions，否則 Read／Write 會被擋。
    """
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)          # 巢狀 session 時 claude 會拒跑
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    # prompt 當 argv 傳在 Windows 上會撞到命令列長度上限（WinError 206），改用 stdin。
    cmd = [CLAUDE_BIN, '-p', '--model', MODEL, '--output-format', 'text',
           '--permission-mode', 'bypassPermissions',
           '--setting-sources', '',
           '--session-id', str(uuid.uuid4())]
    r = subprocess.run(cmd, input=prompt, cwd=workdir, capture_output=True,
                       text=True, timeout=TIMEOUT_SEC, env=env)
    return r.returncode == 0, (r.stdout or r.stderr or '')[-4000:]


# ── 送審訊息 ──
def short_review(intake, spec, source_hits, output_hits, missing):
    """Telegram 訊息本體：只放「要決定什麼」，細節在附件裡。"""
    e = _esc
    rel = RR.RELATION[intake['client_relation']]
    sl = RR.apply_service_line(intake['service_line'])
    L = [f'📋 <b>新職缺待審核</b>　{e(spec.get("title") or "（無標題）")}',
         '',
         f'{e(sl["label"])}　｜　{e(rel["label"])}　｜　'
         f'{"具名" if rel["client_named"] else "⚠️ 匿名"}',
         f'薪資 {_salary(spec)}　｜　{e(spec.get("locations") or "地點未提供")}',
         '']
    if output_hits:
        L += [f'⛔ <b>成品仍有 {len(output_hits)} 處禁刊命中，不可以發布</b>',
              '　請按「✏️ 重寫」，不要按核准。', '']
    else:
        L += [f'🚫 原稿擋下 {len(source_hits)} 處，成品已清乾淨', '']
    if missing:
        L += [f'❓ 待你確認：{e("、".join(str(m) for m in missing))}', '']
    L += ['📎 <b>改了什麼、為什麼</b>——打開上面的 HTML 檔看',
          '按下面的按鈕決定。沒按「核准發布」不會動到網站。']
    return '\n'.join(L)


def build_review_html(intake, spec, report_md, source_hits, output_hits, missing):
    """顧問說明做成一份 HTML 檔，用附件推出去。

    ⚠️ 2026-08-10 Jacky 明講：「不要用文字，直接傳 HTML 讓我們看。」
    原本把整份改寫說明塞進 Telegram 訊息裡，在手機上是一大坨字，
    要判斷「這樣改對不對」根本讀不下去。訊息只留要決定的事，細節進附件。
    """
    import html as _h
    rel = RR.RELATION[intake['client_relation']]
    sl = RR.apply_service_line(intake['service_line'])

    def rows(hits, tone):
        # ⚠️ 這裡原本寫 x.get("text", x)，但命中的欄位叫 context／why／fix，
        #    沒有 text，於是 fallback 把整個 dict 印到畫面上
        #    （顧問看到的是 {'kind': ..., 'matched': ...}）。顧問要判斷的是
        #    「這句話該不該出現在頁面上」，所以要給他原句與處置建議。
        if not hits:
            return f'<p class="none">（沒有命中）</p>'
        out = ['<ul class="hits ' + tone + '">']
        for x in hits:
            if not isinstance(x, dict):
                out.append(f'<li><span>{_h.escape(str(x))[:160]}</span></li>')
                continue
            ctx = str(x.get('context') or x.get('matched') or '')
            why = str(x.get('why') or '')
            fix = str(x.get('fix') or '')
            out.append(
                f'<li><b>{_h.escape(str(x.get("kind", "")))}</b>'
                f'<span>{_h.escape(ctx)[:160]}</span>'
                + (f'<em>為什麼：{_h.escape(why)}</em>' if why else '')
                + (f'<em>怎麼處理：{_h.escape(fix)}</em>' if fix else '')
                + '</li>')
        return ''.join(out) + '</ul>'

    # 顧問說明是 Markdown，這裡只做最低限度的轉換：標題、表格、粗體、清單
    md = report_md or ''
    body = []
    in_tbl = False
    for line in md.splitlines():
        s = line.rstrip()
        if s.startswith('|'):
            cells = [c.strip() for c in s.strip('|').split('|')]
            if set(''.join(cells)) <= set('-: '):
                continue
            tag = 'th' if not in_tbl else 'td'
            if not in_tbl:
                body.append('<table>')
                in_tbl = True
            body.append('<tr>' + ''.join(f'<{tag}>{_h.escape(c)}</{tag}>' for c in cells) + '</tr>')
            continue
        if in_tbl:
            body.append('</table>')
            in_tbl = False
        if s.startswith('###'):
            body.append(f'<h3>{_h.escape(s.lstrip("# "))}</h3>')
        elif s.startswith('##'):
            body.append(f'<h2>{_h.escape(s.lstrip("# "))}</h2>')
        elif s.startswith('#'):
            body.append(f'<h1>{_h.escape(s.lstrip("# "))}</h1>')
        elif s.startswith('> '):
            body.append(f'<blockquote>{_h.escape(s[2:])}</blockquote>')
        elif s.startswith(('- ', '* ')):
            body.append(f'<li>{_h.escape(s[2:])}</li>')
        elif s:
            body.append(f'<p>{_h.escape(s)}</p>')
    if in_tbl:
        body.append('</table>')
    txt = '\n'.join(body).replace('&amp;gt;', '&gt;')
    for a, b in (('**', ''), ('`', '')):
        txt = txt.replace(a, b)

    blocked = ('<div class="warn"><b>⛔ 成品仍有 %d 處命中，不可以發布</b><br>'
               '請按「✏️ 重寫」，不要按核准。</div>' % len(output_hits)) if output_hits else ''
    miss = ('<div class="miss"><b>❓ 待你確認</b>（來源沒給，頁面上暫時不會出現）<br>'
            + '、'.join(_h.escape(str(m)) for m in missing) + '</div>') if missing else ''

    return f"""<!doctype html><meta charset="utf-8">
<title>{_h.escape(spec.get('title') or '新職缺')}｜上架擬稿說明</title>
<style>
:root{{--gold:#a67c3d;--ink:#23262d;--ink2:#4d5563;--ink3:#8a8d95;--line:#eee7db;--soft:#faf8f3;--bad:#c0392b}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f4f1ea;color:var(--ink);font:15px/1.75 -apple-system,"Noto Sans TC",sans-serif;padding:22px 14px}}
.wrap{{max-width:820px;margin:0 auto}}
.card{{background:#fff;border:1px solid var(--line);border-radius:15px;padding:20px 22px;margin-bottom:13px}}
.hero{{background:var(--ink);color:#fff;border-radius:17px;padding:22px 24px;margin-bottom:13px}}
.hero h1{{font-size:21px;margin:0 0 4px}}
.hero p{{margin:0;color:#a8adb8;font-size:13.5px}}
.meta{{display:flex;gap:22px;flex-wrap:wrap;border-top:1px solid #3a3f49;margin-top:14px;padding-top:13px;font-size:12px;color:#a8adb8}}
.meta b{{display:block;color:#fff;font-size:14px;font-weight:600;margin-top:2px}}
h2{{font-size:15px;margin:22px 0 10px;color:var(--gold)}}
h1,h3{{font-size:15.5px;margin:18px 0 8px}}
table{{width:100%;border-collapse:collapse;margin:10px 0;font-size:13.5px}}
th,td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}}
th{{background:var(--soft);color:var(--ink3);font-size:12.5px}}
blockquote{{margin:10px 0;padding:11px 14px;background:var(--soft);border-left:3px solid var(--gold);border-radius:0 9px 9px 0;font-size:13.5px;color:var(--ink2)}}
li{{margin-bottom:5px;color:var(--ink2)}}
p{{margin:8px 0;color:var(--ink2)}}
.hits{{list-style:none;padding:0;margin:8px 0}}
.hits li{{background:var(--soft);border-radius:9px;padding:9px 12px;margin-bottom:6px;font-size:13.5px}}
.hits li b{{color:var(--bad);margin-right:9px}}
.hits.ok li b{{color:var(--gold)}}
.hits li em{{display:block;font-style:normal;font-size:12.5px;color:var(--mut);margin-top:4px}}
.warn{{background:#fdecea;border:1px solid #f3c7c1;border-radius:11px;padding:13px 16px;color:#8a2b20;font-size:14px;margin-bottom:13px}}
.miss{{background:#fdf6e9;border:1px solid #e8d5ab;border-radius:11px;padding:13px 16px;color:#8a5a1a;font-size:14px;margin-bottom:13px}}
.none{{color:var(--ink3);font-size:13.5px}}
</style>
<div class="wrap">
<div class="hero">
  <h1>{_h.escape(spec.get('title') or '（無標題）')}</h1>
  <p>/jobs/{_h.escape(spec.get('slug') or '?')}/</p>
  <div class="meta">
    <div>服務線<b>{_h.escape(sl['label'])}</b></div>
    <div>客戶對象<b>{_h.escape(rel['label'])}</b></div>
    <div>履歷<b>{'具名' if rel['client_named'] else '匿名'}</b></div>
    <div>AI 揭露<b>{_h.escape(rel['ai_disclosure'])}</b></div>
    <div>報告品牌<b>{'不掛品牌' if rel.get('brand_mode') == 'none' else 'Step1ne'}</b></div>
  </div>
</div>
{blocked}{miss}
<div class="card">
  <h2>🚫 原稿擋下 {len(source_hits)} 處</h2>
  {rows(source_hits, 'bad')}
  <h2>🔎 成品再掃一次</h2>
  {rows(output_hits, 'ok')}
</div>
<div class="card">{txt}</div>
</div>"""


def tg_send_doc(html_text, filename, caption, buttons, iid):
    """把說明當 HTML 附件推出去，按鈕掛在附件上。"""
    import uuid as _uuid
    c = tg_conf()
    if not c.get('TG_BOT_TOKEN') or not c.get('TG_CHAT_ID'):
        log('⚠️ 找不到 Telegram 設定，附件沒有推出去')
        return None
    b = '----' + _uuid.uuid4().hex
    parts = []
    fields = {'chat_id': c['TG_CHAT_ID'], 'caption': caption, 'parse_mode': 'HTML',
              'reply_markup': json.dumps({'inline_keyboard': buttons})}
    if c.get('TG_THREAD_ID'):
        fields['message_thread_id'] = str(int(c['TG_THREAD_ID']))
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{b}\r\nContent-Disposition: form-data; name="document"; '
                  f'filename="{filename}"\r\nContent-Type: text/html\r\n\r\n').encode()
                 + html_text.encode('utf-8') + b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{c["TG_BOT_TOKEN"]}/sendDocument',
        data=b''.join(parts), headers={'content-type': f'multipart/form-data; boundary={b}'})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=120))
        return (r.get('result') or {}).get('message_id') if r.get('ok') else None
    except Exception as ex:
        log(f'⚠️ 附件推送失敗：{ex}')
        return None


def build_review(intake, spec, report_md, source_hits, output_hits, missing):
    """顧問在手機上看到的那一則。

    寫法原則（使用者有 ADHD）：先給要決定的事，細節壓在後面。
    但**擋掉了什麼、待確認什麼一定要出現**——那正是他要判斷的依據，
    不能因為「太長」就省略。
    """
    rel = RR.RELATION[intake['client_relation']]
    sl = RR.apply_service_line(intake['service_line'])
    e = _esc

    L = [f'📋 <b>新職缺待審核</b>　{e(spec.get("title") or "（無標題）")}',
         '',
         f'服務線：{e(sl["label"])}　｜　客戶對象：{e(rel["label"])}',
         f'網址：/jobs/{e(spec.get("slug") or "?")}/',
         f'薪資：{_salary(spec)}　｜　地點：{e(spec.get("locations") or "未提供")}',
         f'具名：{"具名" if rel["client_named"] else "⚠️ 匿名（未簽約）"}'
         f'　｜　AI 揭露：{e(rel["ai_disclosure"])}'
         f'　｜　報告品牌：{"Step1ne" if rel["brand_mode"] == "step1ne" else "⚠️ 不掛品牌"}']

    if source_hits:
        kinds = {}
        for h in source_hits:
            kinds.setdefault(h['kind'], []).append(h['matched'])
        L += ['', f'🚫 <b>禁刊過濾器擋下 {len(source_hits)} 處</b>']
        for k, v in kinds.items():
            L.append(f'・{e(k)}：{e("、".join(sorted(set(v))[:4]))}')
        L.append('（已在 JD 裡改寫或移除，改法見下面的顧問說明）')
    else:
        L += ['', '✅ 禁刊過濾器：原始資料沒有命中']

    if output_hits:
        L += ['', f'⛔ <b>成品仍有 {len(output_hits)} 處命中，不可以發布</b>']
        for h in output_hits[:6]:
            L.append(f'・{e(h["kind"])}：「{e(h["matched"])}」')
        L.append('請按「✏️ 重寫」，不要按核准。')

    if missing:
        L += ['', '❓ <b>待你確認</b>（來源沒給，頁面上暫時不會出現）',
              '・' + e('、'.join(missing))]

    if report_md:
        L += ['', '📝 <b>顧問說明</b>', e(_clip(report_md, 900))]

    L += ['', '按下面的按鈕決定。<b>沒按「核准發布」就不會動到網站。</b>']
    return '\n'.join(L)


def _esc(s):
    return (str('' if s is None else s)
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n] + '…（完整版在後台）'


PREVIEW_BASE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev/preview/job/'


def load_review_json(workdir, missing):
    """AI 寫的一眼摘要。沒寫或壞掉就退回用待確認清單，不要讓整張單卡住。"""
    try:
        r = json.load(open(os.path.join(workdir, 'review.json'), encoding='utf-8'))
        dec = [d for d in (r.get('decisions') or []) if isinstance(d, dict) and d.get('q')][:3]
        chg = [c for c in (r.get('changes') or []) if isinstance(c, dict) and c.get('what')]
        return {'decisions': dec, 'changes': chg}
    except Exception:
        return {'decisions': [{'q': f'「{m}」沒有資料，要補嗎？', 'why': ''} for m in (missing or [])[:3]],
                'changes': []}


def build_preview_html(intake, spec, rv, report_md, output_hits):
    """上線同一套 render_page 產出的整頁，加上頂端「要你決定的」與底部「AI 改了什麼」。

    2026-09-24 Jacky：「太多文字不知道要看什麼、要決定什麼、放到網站上會是什麼」。
    所以預覽頁 = 網站實際長相，決定事項釘在最上面，解釋全部收在最下面。
    """
    import html as _h
    import importlib.util as _iu
    e = lambda x: _h.escape(str('' if x is None else x))
    try:
        sp = _iu.spec_from_file_location('pj', os.path.join(ROOT, 'publish_job.py'))
        PJ = _iu.module_from_spec(sp); sp.loader.exec_module(PJ)
        page = PJ.render_page(spec)
    except Exception as ex:
        page = (f'<!doctype html><html><head><meta charset="utf-8"></head><body>'
                f'<p style="padding:20px">頁面產生失敗：{e(ex)}（通常是 AI 少給了必填欄位，請按「要改」）</p></body></html>')
    sl = RR.apply_service_line(intake['service_line'])['label']
    dec = rv['decisions']
    top = ('<div style="position:sticky;top:0;z-index:9999;background:#fff7d6;border-bottom:2px solid #e0b400;'
           'padding:14px 16px;font:15px/1.6 -apple-system,\'PingFang TC\',sans-serif;color:#222">'
           '<div style="font-weight:700;margin-bottom:6px">🟡 預覽・尚未上線　｜　'
           f'{e(intake.get("client_name") or "")}　{e(sl)}</div>')
    if output_hits:
        top += f'<div style="color:#b00020;font-weight:700">⛔ 成品還有 {len(output_hits)} 處禁刊字，這版不能發，請在 TG 按「要改」</div>'
    if dec:
        top += '<div style="font-weight:700;margin-top:4px">你要決定的：</div><ol style="margin:4px 0 0 20px;padding:0">'
        top += ''.join(f'<li>{e(d["q"])}' + (f'<div style="color:#666;font-size:13px">{e(d.get("why"))}</div>' if d.get('why') else '') + '</li>' for d in dec)
        top += '</ol>'
    else:
        top += '<div>沒有需要你決定的事，內容沒問題就在 TG 按「核准發布」。</div>'
    top += '<div style="color:#666;font-size:13px;margin-top:6px">往下捲就是上線後的樣子；最下面可以看 AI 改了什麼。</div></div>'
    chg = rv['changes']
    bottom = ('<div style="max-width:880px;margin:40px auto;padding:16px;border-top:3px dashed #ccc;'
              'font:14px/1.7 -apple-system,\'PingFang TC\',sans-serif;color:#222">')
    bottom += f'<details open><summary style="font-weight:700;cursor:pointer">✂️ AI 改掉的地方（{len(chg)} 處）</summary>'
    bottom += ''.join(f'<div style="margin:10px 0"><b>{e(c["what"])}</b><br><span style="color:#b00020">原文：{e(c.get("from"))}</span><br>'
                      f'<span style="color:#0a7a32">改成：{e(c.get("to"))}</span></div>' for c in chg) or '<p>沒有改動原文。</p>'
    bottom += '</details>'
    bottom += ('<details style="margin-top:14px"><summary style="font-weight:700;cursor:pointer">📄 AI 的完整說明（要細看再打開）</summary>'
               f'<pre style="white-space:pre-wrap;font:13px/1.6 inherit">{e(report_md)}</pre></details>')
    bottom += ('<details style="margin-top:14px"><summary style="font-weight:700;cursor:pointer">⚙️ 內部欄位</summary>'
               f'<div>網址：/jobs/{e(spec.get("slug"))}/　薪資欄位：{e(_salary(spec))}　客戶內部名稱：{e(spec.get("client_name"))}</div></details></div>')
    page = page.replace('<head>', '<head><base href="https://step1ne.com/"><meta name="robots" content="noindex,nofollow">', 1)
    page = re.sub(r'(<body[^>]*>)', lambda m: m.group(1) + top, page, count=1)
    page = page.replace('</body>', bottom + '</body>', 1) if '</body>' in page else page + bottom
    return page


def short_review_v2(intake, spec, rv, output_hits, preview_url):
    """TG 那則：這是什麼缺 → 預覽連結 → 要決定的 → 改了什麼（一行一條）→ 按鈕。"""
    e = _esc
    sl = RR.apply_service_line(intake['service_line'])['label']
    L = [f'📋 <b>新職缺：{e(spec.get("title") or "（無標題）")}</b>',
         f'{e(intake.get("client_name") or "")}｜{e(sl)}', '']
    if preview_url:
        L += [f'👉 <a href="{e(preview_url)}">看網站上會長怎樣</a>', '']
    if output_hits:
        L += [f'⛔ <b>成品還有 {len(output_hits)} 處禁刊字，這版不能發</b>，請按「✏️ 要改」', '']
    dec = rv['decisions']
    if dec:
        L += [f'🟡 <b>你要決定的（{len(dec)} 件）</b>'] + [f'{i}. {e(d["q"])}' for i, d in enumerate(dec, 1)] + ['']
    else:
        L += ['🟢 沒有需要你決定的事', '']
    chg = rv['changes']
    if chg:
        L += [f'✂️ AI 幫你改掉的（{len(chg)} 處，預覽頁有原文對照）'] + [f'・{e(c["what"])}' for c in chg[:5]]
        if len(chg) > 5:
            L.append(f'・…還有 {len(chg) - 5} 處')
        L.append('')
    L.append('沒按「核准發布」不會動到網站。')
    return '\n'.join(L)


def save_preview(iid, token, html_text, rv):
    """整頁 HTML 很長，走 CF API 帶參數寫（不塞進 SQL 字串，避開 D1 單句長度上限）。"""
    import d1_http
    tok, acc = d1_http._cfg()
    body = json.dumps({'sql': 'UPDATE job_intakes SET preview_token=?, preview_html=?, review_json=? WHERE id=?',
                       'params': [token, html_text, json.dumps(rv, ensure_ascii=False), iid]}).encode('utf-8')
    req = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{d1_http.DB_ID}/query',
        data=body, headers={'authorization': f'Bearer {tok}', 'content-type': 'application/json'}, method='POST')
    r = json.load(urllib.request.urlopen(req, timeout=60))
    if not r.get('success'):
        raise RuntimeError(str(r.get('errors'))[:200])


def _salary(spec):
    lo, hi = spec.get('salary_min'), spec.get('salary_max')
    if lo and hi:
        return f'{lo:,}–{hi:,}'
    if lo:
        return f'{lo:,} 起'
    return '面議／未提供'


# ── 主流程 ──
def process(intake, dry=False, rewrite_note=None, keep=False):
    iid = intake.get('id') or ('local-' + datetime.datetime.now().strftime('%m%d%H%M'))
    workdir = os.path.join(WORK, iid)
    os.makedirs(workdir, exist_ok=True)
    log(f'收件單 {iid}｜{RR.RELATION[intake["client_relation"]]["label"]}'
        f'｜{RR.apply_service_line(intake["service_line"])["label"]}')

    files = fetch_files(intake, workdir)
    files, office_txt = expand_office_files(files)
    src = source_text(intake)
    if office_txt:
        # 禁刊過濾器也要掃得到 Word／Excel 裡的字，不然客戶名藏在檔案裡就漏掉
        src = (src + '\n\n' + office_txt).strip()

    # ① 掃原始資料
    source_hits = PF.scan(src, client_name=intake.get('client_name'),
                          client_code=intake.get('client_code'))
    log(f'禁刊過濾器（原始資料）：命中 {len(source_hits)} 處')

    # ② 交給總指揮擬 JD
    if not dry or os.environ.get('JOBINTAKE_RUN_LLM') == '1':
        prompt = build_prompt(intake, files, src, source_hits, rewrite_note, workdir)
        open(os.path.join(workdir, 'prompt.txt'), 'w', encoding='utf-8').write(prompt)
        log(f'交給 step1ne 總指揮擬 JD（{MODEL}，prompt {len(prompt):,} 字）…')
        ok, out = run_commander(prompt, workdir)
        open(os.path.join(workdir, 'commander.log'), 'w', encoding='utf-8').write(out)
        if not ok:
            log(f'❌ 擬 JD 失敗：{out[-500:]}')
            _mark(intake, 'failed')
            return None
    else:
        log('（--dry 且未設 JOBINTAKE_RUN_LLM=1，跳過擬 JD，直接讀既有的 spec.json）')

    spec_path = os.path.join(workdir, 'spec.json')
    if not os.path.exists(spec_path):
        log(f'❌ 總指揮沒有寫出 {spec_path}')
        _mark(intake, 'failed')
        return None
    spec = json.load(open(spec_path, encoding='utf-8'))

    # 顧問選的分類永遠蓋過模型判斷——那兩個是顧問的決定，不是模型的
    rel = RR.apply_relation(intake['client_relation'])
    sl = RR.apply_service_line(intake['service_line'])
    spec.update(rel)
    spec['service_line'] = intake['service_line']
    spec.setdefault('track', sl['track'])
    if intake.get('client_code'):
        spec['client_code'] = intake['client_code']
    spec['intake_id'] = iid

    # ③ 掃成品。這一關任何一條命中都代表不可以發布。
    #    對外欄位才掃——client_name/notes/client_code 是內部欄位，本來就會有客戶名。
    PUBLIC_KEYS = ('title', 'subtitle', 'page_title', 'description', 'keywords',
                   'og_title', 'og_desc', 'intro', 'tags', 'locations', 'locality',
                   'region', 'must_skills', 'benefits', 'spec', 'duties', 'must',
                   'plus', 'why', 'faq', 'industry', 'card_meta', 'card_desc', 'slug')
    public_blob = json.dumps({k: spec.get(k) for k in PUBLIC_KEYS},
                             ensure_ascii=False, indent=1)
    output_hits = PF.scan_output(
        public_blob,
        # 未簽約才把客戶名列為禁字——已簽約本來就可以具名
        client_name=intake.get('client_name') if rel['client_named'] == 0 else None,
        client_code=intake.get('client_code'))
    log(f'禁刊過濾器（成品對外欄位）：命中 {len(output_hits)} 處'
        + ('　⛔ 不可發布' if output_hits else ''))

    missing = PF.pending_fields(spec)
    report_md = ''
    rp = os.path.join(workdir, 'report.md')
    if os.path.exists(rp):
        report_md = open(rp, encoding='utf-8').read()

    json.dump(spec, open(spec_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

    review = build_review(intake, spec, report_md, source_hits, output_hits, missing)
    open(os.path.join(workdir, 'review.txt'), 'w', encoding='utf-8').write(review)
    # ⚠️ 顧問說明改用 HTML 附件（Jacky 2026-08-10：「不要用文字，直接傳 HTML 讓我們看」）。
    # 手機上一大坨字沒辦法判斷「這樣改對不對」。訊息只留要決定的事。
    review_html = build_review_html(intake, spec, report_md, source_hits, output_hits, missing)
    html_path = os.path.join(workdir, 'review.html')
    open(html_path, 'w', encoding='utf-8').write(review_html)
    short = short_review(intake, spec, source_hits, output_hits, missing)

    filter_json = json.dumps({'source_hits': source_hits, 'output_hits': output_hits,
                              'missing': missing}, ensure_ascii=False)

    if dry:
        print('\n' + '=' * 62)
        print('送審訊息（--dry：只印出來，沒有推到 Telegram）')
        print('=' * 62)
        print(review)
        print('-' * 62)
        print('按鈕：' + '　'.join(b['text'] for row in buttons_for(iid) for b in row))
        print('=' * 62 + f'\n產出目錄：{workdir}')
        return spec

    # 2026-09-24 新格式：短訊息＋網站預覽連結。預覽做不出來才退回舊的 HTML 附件。
    mid = None
    try:
        rv = load_review_json(workdir, missing)
        token = uuid.uuid4().hex
        save_preview(iid, token, build_preview_html(intake, spec, rv, report_md, output_hits), rv)
        mid = tg_send(short_review_v2(intake, spec, rv, output_hits, PREVIEW_BASE + token), buttons_for(iid), iid)
    except Exception as ex:
        log(f'⚠️ 預覽頁做不出來，改用舊格式：{ex}')
    if not mid:
        fname = f"{(spec.get('slug') or 'job')}_擬稿說明.html"
        mid = tg_send_doc(review_html, fname, short, buttons_for(iid), iid)
    if not mid:      # 附件推不出去就退回純文字，不要讓顧問什麼都收不到
        mid = tg_send(review, buttons_for(iid), iid)
    D.d1(f"""UPDATE job_intakes SET status='pending', draft_json={D.q(json.dumps(spec, ensure_ascii=False))},
             filter_json={D.q(filter_json)}, review_text={D.q(review)},
             tg_message_id={mid if mid else 'NULL'}, updated_at=datetime('now','+8 hours')
             WHERE id={D.q(iid)}""")
    log(f'✅ 已送審（Telegram message_id={mid}）。等顧問按按鈕，網站目前沒有任何改動。')
    return spec


def _mark(intake, status):
    if intake.get('id') and not intake.get('_local'):
        D.d1(f"UPDATE job_intakes SET status={D.q(status)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(intake['id'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--intake', help='D1 裡的收件單 id')
    ap.add_argument('--local', help='本機收件單 JSON（離線測試用，不碰 D1）')
    ap.add_argument('--all', action='store_true', help='處理所有 status=new/rewrite 的收件單')
    ap.add_argument('--dry', action='store_true', help='只印送審訊息，不推 Telegram、不寫 D1')
    ap.add_argument('--rewrite-note', help='顧問按「重寫」之後補的意見')
    a = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)

    if a.local:
        intake = load_intake_local(a.local)
        intake['_local'] = True
        process(intake, dry=a.dry, rewrite_note=a.rewrite_note)
        return

    if a.all:
        rows = D.d1("SELECT id FROM job_intakes WHERE status IN ('new','rewrite') ORDER BY created_at")
        if not rows:
            log('沒有待處理的收件單')
            return
        for r in rows:
            it = load_intake_d1(r['id'])
            D.d1(f"UPDATE job_intakes SET status='drafting' WHERE id={D.q(r['id'])}")
            try:
                process(it, dry=a.dry, rewrite_note=it.get('rewrite_note'))
            except Exception as e:
                # 2026-08-26 修：process() 炸掉（例如 claude -p 逾時，2026-08-19
                # 撞過一次）之前，狀態已經先設成 drafting——但 tick.py 的排程
                # 只認得 new/rewrite/approved 這三種狀態，drafting 不在裡面，
                # 一旦這裡沒接住例外，這筆就會卡死在 drafting，7 天都不會再被
                # 撿起來重試（撞過的真實案例：8/19 之後整條排程看起來像沒在跑，
                # 其實是這一筆卡住讓人誤以為排程壞了）。退回原本狀態，下一輪
                # tick 才會重新撿到。
                log(f'❌ {r["id"]} 擬稿失敗，退回原狀態重試：{e}')
                D.d1(f"UPDATE job_intakes SET status={D.q(it['status'])} WHERE id={D.q(r['id'])}")
        return

    if not a.intake:
        sys.exit('要給 --intake、--local 或 --all')
    it = load_intake_d1(a.intake)
    orig_status = it['status']
    D.d1(f"UPDATE job_intakes SET status='drafting' WHERE id={D.q(a.intake)}")
    try:
        process(it, dry=a.dry, rewrite_note=a.rewrite_note or it.get('rewrite_note'))
    except Exception as e:
        log(f'❌ {a.intake} 擬稿失敗，退回原狀態重試：{e}')
        D.d1(f"UPDATE job_intakes SET status={D.q(orig_status)} WHERE id={D.q(a.intake)}")
        raise


if __name__ == '__main__':
    main()
