#!/usr/bin/env python3
"""面談前改談其他職缺（2026-10-07 Jacky）。

起因：陳鵬仁應徵「日本項目財務會計」，Jacky 看履歷覺得他更適合「集團財務主管」，
手動在面談前把主職缺換掉、寫一段開場指示給阿財。Jacky 要的是「顧問不用按任何東西，
阿財自己會變通」，所以整段做成自動：

  新應徵、履歷解析好、還沒開始面談
        │  （ai_worker 每 20 秒掃一次 → 排 job_switch_fit）
        ▼
  claude 比對履歷 × 所有開放中的職缺 → 只有「明顯更適合、而且很有把握」才改談
        │  （每天最多 DAILY_CAP 次；每一次判斷不管改不改都記在 job_switch_checks）
        ▼
  改 applications.job_slug／job_title ＋ 在 pre_interview_note 最前面插一段開場指示
  （阿財開場先徵求同意；不同意就談原本的職缺，原職缺摘要就在那段裡）
  ＋ 人選卡片記一筆 ＋ TG 人選群組主題 7 通知（附「取消改談」按鈕）
        │
        ▼  面談結束（interview_state='done'）→ 排 job_switch_check
  claude 讀逐字稿判斷實際談的是哪一個 → 談的是原職缺就自動改回去，兩種都發 TG

⚠️ 不動 interview_daemon.py：阿財本來就會讀 pre_interview_note（【面談前筆記】那段），
   這裡只是在那個欄位前面加一段。
⚠️ 只新增資料、不刪：改談紀錄放 application_job_switches，判斷紀錄放 job_switch_checks。
⚠️ 跟 parse_resumes.py 的關係：那支解析完履歷會「覆蓋」pre_interview_note，所以要等它寫完
   （pre_interview_note_at 有值，或解析完超過 5 分鐘）才判斷，不然改談那段會被蓋掉。

用法（測試用）：
    python3 job_switch.py fit <application_id> --dry-run [--resume-file x.txt]
    python3 job_switch.py check <switch_id> --dry-run [--transcript-file x.txt]
"""
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

MODEL = 'claude-sonnet-5'
CLAUDE_BIN = shutil.which('claude') or 'claude'
from ai_lockdown import NO_TOOLS  # 2026-10-08 資安：原清單漏 Glob／Grep，改統一上鎖
WORKER_ID = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()

# 只判斷這個時間之後進來的應徵（上線前的舊應徵不回頭掃）
LAUNCH_AT = os.environ.get('JOB_SWITCH_LAUNCH_AT', '2026-10-07 21:45:00')
DAILY_CAP = int(os.environ.get('JOB_SWITCH_DAILY_CAP', '5'))
AUTO_ENABLED = os.environ.get('JOB_SWITCH_AUTO', '1') != '0'
CAND_THREAD = 7            # 人選群組的通知主題
CAND_CHAT_FALLBACK = '-1003967585448'
MATCHABLE = ('open', 'active')

# 不能拿來當理由、也不能出現在給人選聽的那句話裡
PROTECTED_RE = re.compile(r'年齡|年紀|歲|年輕|性別|男性|女性|男生|女生|婚|生育|育兒|育嬰|懷孕|小孩|外貌|長相|身高|體重|宗教|國籍|籍貫|族群|身障|健康狀況|出生')
CLIENT_WORD_RE = re.compile(r'客戶')


def log(m):
    print(f'[job_switch] {m}', flush=True)


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def now_tw():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


def db(sql):
    return d1_http.query(sql)


def rows(sql):
    return db(sql).get('results') or []


def sanitize(t):
    if not t:
        return t
    return ''.join(c for c in str(t) if c in '\n\t' or ord(c) >= 32)


def default_run_claude(prompt, want_json=False, timeout=300):
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    r = subprocess.run([CLAUDE_BIN, '-p', '--model', MODEL, *NO_TOOLS, '--output-format', 'text'],
                       input=sanitize(prompt), capture_output=True, text=True, env=env,
                       timeout=timeout, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-300:]}')
    out = r.stdout.strip()
    if not want_json:
        return out
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'回覆裡沒有 JSON：{out[:200]}')
    json.loads(out[i:j + 1])
    return out[i:j + 1]


# ───────────────────────── 文字工具 ─────────────────────────

def short_title(t):
    """「日本項目財務會計（Finance & Accounting／Project Controller）」→「日本項目財務會計」，講給人選聽比較順。"""
    s = re.sub(r'[（(][^）)]*[）)]', '', str(t or '')).strip()
    return s or str(t or '').strip()


def client_tokens(*jobs):
    toks = []
    try:
        import recommendation_service as rs
        for j in jobs:
            if j and j.get('client_name'):
                toks += rs.client_name_tokens(j['client_name'])
    except Exception:
        pass
    return sorted(set(toks), key=len, reverse=True)


def scrub(text, tokens):
    s = str(text or '')
    for t in tokens:
        s = s.replace(t, '該公司')
    return s


def _clip(s, n):
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def salary_text(job):
    lo, hi, unit = job.get('salary_min'), job.get('salary_max'), (job.get('salary_unit') or 'MONTH').upper()
    u = {'MONTH': '月薪', 'YEAR': '年薪', 'HOUR': '時薪', 'DAY': '日薪'}.get(unit, '')
    if lo and hi and lo != hi:
        base = f'{u} {int(lo):,}–{int(hi):,}'
    elif lo:
        base = f'{u} {int(lo):,} 起'
    elif hi:
        base = f'{u} 最高 {int(hi):,}'
    else:
        base = '薪資依經歷面議'
    note = _clip(job.get('salary_note'), 70)
    return f'{base}（{note}）' if note else base


def job_summary(job, tokens):
    """原職缺的重點摘要：人選不同意改談時，阿財照這段談原本的缺。保密職缺不寫公司名。"""
    parts = [f'{job.get("title")}（{_clip(job.get("locations"), 30) or "地點面談時確認"}）']
    if job.get('client_intro'):
        parts.append('公司：' + _clip(job.get('client_intro'), 50))
    parts.append('待遇：' + salary_text(job))
    if job.get('main_duties'):
        parts.append('主要工作：' + _clip(job.get('main_duties'), 150))
    req = job.get('required_conditions') or job.get('must_skills')
    if req:
        parts.append('必要條件：' + _clip(req, 120))
    if job.get('language_requirement'):
        parts.append('語言：' + _clip(job.get('language_requirement'), 70))
    return scrub('；'.join(parts), tokens)


NOTE_HEAD = '【面談前改談其他職缺'
NOTE_TAIL = '【改談說明結束】'


def build_note_block(orig, target, reason, opening_reason, label):
    """跟 Jacky 2026-10-07 手寫給陳鵬仁那段同一個結構，只是把職缺換成參數。

    ⚠️ 開場那句是阿財講給人選聽的：不用「客戶」、不用「顧問／我們」當主詞描述人選、不講公司名。
    """
    tokens = client_tokens(orig, target)
    xs, ys = short_title(orig.get('title')), short_title(target.get('title'))
    senior = '（這是中高階職缺，照中高階流程談）' if (target.get('seniority') == 'senior') else ''
    extra = f'，{opening_reason.strip("，。 ")}' if opening_reason else ''
    block = (
        f'{NOTE_HEAD}（{label}）——這場的開場與職缺安排，照做】\n'
        f'1. 他原本應徵的是「{xs}」。看過履歷，判斷他更適合「{ys}」（本場的主職缺，資料在上面）。'
        f'判斷理由（內部參考，不要唸）：{scrub(_clip(reason, 110), tokens)}\n'
        f'2. 開場先講清楚並徵求同意：「您應徵的是{xs}，看過您的履歷，您的經歷其實更接近另一個{ys}的職位{extra}，'
        f'今天想跟您聊這個機會，您方便嗎？」公司名稱照保密規則不講。\n'
        f'3. 他說方便 → 整場照「{ys}」談{senior}。\n'
        f'4. 他說不方便／只想談原本那個 → 說「沒問題，那我們就聊您應徵的{xs}」，改照下面這個缺的內容談，不要再推{ys}：\n'
        f'   {job_summary(orig, tokens)}\n'
        f'5. 面談結束前，一句話講明今天聊的是哪個職缺（例如：「好，今天我們聊的是{ys}這個機會」），讓顧問看報告就知道。\n'
        f'{NOTE_TAIL}\n\n'
    )
    return block


# ───────────────────────── TG ─────────────────────────

def _tg_env():
    e = {}
    for line in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8'):
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            e[k.strip()] = v.strip().strip('\'"')
    return e


def tg(text, buttons=None):
    """發到人選群組主題 7。失敗回 None，不影響主流程。"""
    import urllib.request
    try:
        e = _tg_env()
        chat = None
        try:
            import tg_route
            chat, _ = tg_route.route('candidate')
        except Exception:
            pass
        payload = {'chat_id': chat or CAND_CHAT_FALLBACK, 'message_thread_id': CAND_THREAD,
                   'text': text[:3900], 'disable_web_page_preview': True}
        if buttons:
            payload['reply_markup'] = {'inline_keyboard': buttons}
        req = urllib.request.Request(f"https://api.telegram.org/bot{e['TG_BOT_TOKEN']}/sendMessage",
                                     data=json.dumps(payload).encode(),
                                     headers={'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return (json.loads(r.read()).get('result') or {}).get('message_id')
    except Exception as ex:
        log(f'TG 發送失敗：{ex}')
        return None


# ───────────────────────── 共用查詢 ─────────────────────────

def get_job(slug):
    r = rows(f'SELECT * FROM jobs WHERE slug={q(slug)}')
    return r[0] if r else None


def resume_text(app):
    if app.get('resume_file_id'):
        r = rows(f"SELECT text_content, parsed_at FROM files WHERE id={q(app['resume_file_id'])}")
        if r and r[0].get('text_content'):
            return r[0]['text_content'], r[0].get('parsed_at')
    if app.get('resume_url_text'):
        return app['resume_url_text'], app.get('resume_url_parsed_at')
    return '', None


def add_candidate_note(app_id, content, by):
    db(f"INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by) VALUES "
       f"({q(str(uuid.uuid4()))}, {q(app_id)}, '顧問備註', {q(content[:2000])}, {q(now_tw())}, {q(by)})")


def queue_job(job_id, kind, payload):
    """ai_jobs 用固定 id ＋ INSERT OR IGNORE：同一件事只會排一次（多台機器同時掃也一樣）。"""
    r = db(f"INSERT OR IGNORE INTO ai_jobs (id, kind, payload_json, status, created_at) VALUES "
           f"({q(job_id)}, {q(kind)}, {q(json.dumps(payload, ensure_ascii=False))}, 'pending', {q(now_tw())})")
    return bool((r.get('meta') or {}).get('changes'))


# ───────────────────────── ① 面談前：要不要改談 ─────────────────────────

def scan_fit():
    """找「新進、履歷解析好、還沒開始面談、還沒判斷過」的應徵，排進佇列。"""
    if not AUTO_ENABLED:
        return 0
    cand = rows(f"""
        SELECT a.id FROM applications a
         LEFT JOIN files f ON f.id = a.resume_file_id
         WHERE a.created_at >= {q(LAUNCH_AT)}
           AND a.created_at >= datetime('now','+8 hours','-2 days')
           AND COALESCE(a.interview_state,'not_started') = 'not_started'
           AND a.superseded_by IS NULL
           AND COALESCE(a.interview_mode,'') != 'consultant_call'
           AND COALESCE(a.status,'') NOT IN ('redirected','duplicate','rejected','declined','closed','interviewed')
           AND COALESCE(a.job_slug,'unspecified') != 'unspecified'
           AND (length(COALESCE(f.text_content,'')) >= 200 OR length(COALESCE(a.resume_url_text,'')) >= 200)
           AND (a.pre_interview_note_at IS NOT NULL
                OR COALESCE(f.parsed_at, a.resume_url_parsed_at) <= datetime('now','+8 hours','-5 minutes'))
           AND NOT EXISTS (SELECT 1 FROM job_switch_checks c WHERE c.application_id = a.id)
           AND NOT EXISTS (SELECT 1 FROM application_job_switches s WHERE s.application_id = a.id)
           AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.id = 'jsw-fit-' || a.id)
         ORDER BY a.created_at ASC LIMIT 5""")
    n = 0
    for r in cand:
        if queue_job('jsw-fit-' + r['id'], 'job_switch_fit', {'application_id': r['id']}):
            n += 1
            log(f'排入改談判斷：{r["id"][:8]}')
    return n


FIT_PROMPT = """你是資深獵頭顧問。一位求職者應徵了下面的「應徵職缺」，在 AI 面談開始前，
請判斷：是否有另一個開放中的職缺「明顯更適合」他，值得面談一開始就改談那個職缺（會先徵求他同意）。

## 判斷原則（非常保守）
- 預設答案是「不改」（keep）。只有同時符合下面兩點才改（switch）：
  1. 應徵職缺跟他明顯不搭：例如他的資歷／職級遠高於應徵職缺（帶過團隊、做過主管，卻應徵執行層級），
     或應徵職缺的必要條件他明顯不符、職能方向不同。
  2. 另一個職缺跟他的資歷、職級、過去職稱與專業方向「高度吻合」，而且必要條件大致都對得上。
- 兩個職缺都還可以、差不多 → keep。只是「也可以考慮」→ keep（面談後的推薦流程會處理）。
- 只能用工作相關的事實判斷：年資、職級、職稱、專業技能、產業、證照、管理幅度、成果、地點、薪資帶。
  **絕對不可以**用年齡、出生年、性別、婚姻、生育、外貌、國籍、宗教、健康當理由，履歷有寫也一律忽略，
  理由裡也不准出現這些字。稱呼一律用「他」或「人選」，不要推測性別。
- 地點或薪資明顯不合（例如在他希望地點外、薪資遠低於他的期望）的職缺不要選。

## 應徵職缺
{applied}

## 其他開放中的職缺（只能從這裡選 target_slug）
{jobs}

## 求職者表單
期望薪資：{expected_salary}
地點意願：{location_ok}
備註：{note}

## 履歷
{resume}

## 輸出
只輸出一個 JSON，不要其他文字：
{{
  "decision": "switch 或 keep",
  "target_slug": "要改談的職缺 slug；keep 就填空字串",
  "confidence": "high / medium / low（對 switch 這個判斷有多確定）",
  "applied_fit": "good / partial / mismatch（他跟應徵職缺的吻合程度）",
  "target_fit": "strong / good / partial（他跟 target 的吻合程度；keep 填空字串）",
  "reason": "給顧問看的理由，60–120 字，具體講哪幾條吻合／不吻合（不可提年齡性別等）",
  "opening_reason": "可選。給求職者聽的半句話，說明為什麼這個職缺更貼近他，15–30 字，例如「特別是您帶團隊和跟銀行談融資的經驗」；不可提公司名、「客戶」、年齡性別；沒有就填空字串"
}}"""


def _job_line(j):
    yrs = f'{j.get("years_min")}年以上' if j.get('years_min') else ''
    return (f'- slug={j["slug"]}｜{j.get("title")}｜職級={j.get("seniority") or "一般"}｜{yrs}｜'
            f'地點={_clip(j.get("locations"), 30)}｜{salary_text(j)}｜'
            f'必要={_clip(j.get("required_conditions") or j.get("must_skills"), 140)}｜'
            f'工作={_clip(j.get("main_duties"), 140)}')


def _applied_block(j):
    return (f'{j.get("title")}（slug={j["slug"]}，職級={j.get("seniority") or "一般"}，'
            f'{j.get("years_min") or "?"} 年以上）\n地點：{j.get("locations")}\n待遇：{salary_text(j)}\n'
            f'主要工作：{_clip(j.get("main_duties"), 400)}\n必要條件：{_clip(j.get("required_conditions") or j.get("must_skills"), 300)}\n'
            f'語言：{_clip(j.get("language_requirement"), 100)}')


def _record_check(app_id, status, decision, applied, target=None, conf=None, reason=None, raw=None):
    db(f"INSERT INTO job_switch_checks (application_id, status, decision, applied_job_slug, target_job_slug, confidence, reason, raw_json, created_at, decided_at, worker_id) "
       f"VALUES ({q(app_id)}, {q(status)}, {q(decision)}, {q(applied)}, {q(target)}, {q(conf)}, {q((reason or '')[:600])}, {q((raw or '')[:4000])}, {q(now_tw())}, {q(now_tw())}, {q(WORKER_ID)}) "
       f"ON CONFLICT(application_id) DO UPDATE SET status=excluded.status, decision=excluded.decision, "
       f"target_job_slug=excluded.target_job_slug, confidence=excluded.confidence, reason=excluded.reason, "
       f"raw_json=excluded.raw_json, decided_at=excluded.decided_at, worker_id=excluded.worker_id")


def auto_switches_today():
    r = rows("SELECT COUNT(*) n FROM application_job_switches WHERE source='auto' "
             "AND substr(created_at,1,10) = substr(datetime('now','+8 hours'),1,10)")
    return int(r[0]['n']) if r else 0


def run_fit(payload, run_claude=None, dry_run=False, resume_override=None):
    """判斷一位人選要不要改談；要的話直接改（dry_run 時只回傳判斷，不寫任何東西）。"""
    run_claude = run_claude or default_run_claude
    app_id = payload['application_id']
    a = rows(f'SELECT * FROM applications WHERE id={q(app_id)}')
    if not a:
        return 'skip: 找不到應徵'
    app = a[0]
    applied_slug = app.get('job_slug')
    if not dry_run and (app.get('interview_state') or 'not_started') != 'not_started':
        _record_check(app_id, 'done', 'blocked:already_started', applied_slug)
        return 'skip: 面談已開始'
    applied = get_job(applied_slug)
    if not applied:
        if not dry_run:
            _record_check(app_id, 'done', 'blocked:no_applied_job', applied_slug)
        return 'skip: 找不到應徵職缺'
    text, _ = resume_text(app)
    if resume_override:
        text = resume_override
    if len(text or '') < 200:
        if not dry_run:
            _record_check(app_id, 'done', 'blocked:no_resume', applied_slug)
        return 'skip: 沒有履歷文字'

    import recommendation_service as rs
    taken = rs.existing_job_slugs_for_candidate(app_id, email=app.get('email'))
    jobs = [j for j in rs.list_matchable_jobs(exclude_slug=applied_slug, limit=60)
            if j.get('status') in MATCHABLE and j['slug'] not in taken]
    if not jobs:
        if not dry_run:
            _record_check(app_id, 'done', 'keep', applied_slug, reason='沒有其他可改談的職缺')
        return 'keep: 沒有其他職缺'
    prompt = FIT_PROMPT.format(
        applied=_applied_block(applied), jobs='\n'.join(_job_line(j) for j in jobs),
        expected_salary=app.get('expected_salary') or '—', location_ok=app.get('location_ok') or '—',
        note=_clip(app.get('note'), 200) or '—', resume=sanitize(text)[:12000])
    raw = run_claude(prompt, want_json=True)
    d = json.loads(raw)
    decision = str(d.get('decision') or '').lower()
    target_slug = str(d.get('target_slug') or '').strip()
    reason = str(d.get('reason') or '').strip()
    opening = str(d.get('opening_reason') or '').strip()
    conf = str(d.get('confidence') or '').lower()

    # 程式再把一次關（AI 說要改也不一定改）
    verdict = 'switch'
    if decision != 'switch':
        verdict = 'keep'
    elif conf != 'high' or str(d.get('target_fit') or '').lower() != 'strong' \
            or str(d.get('applied_fit') or '').lower() == 'good':
        verdict = 'keep:not_confident'
    elif target_slug not in {j['slug'] for j in jobs}:
        verdict = 'blocked:target_not_open'
    elif PROTECTED_RE.search(reason):
        verdict = 'blocked:protected_attr'
    target = get_job(target_slug) if verdict == 'switch' else None
    if verdict == 'switch' and not target:
        verdict = 'blocked:target_missing'
    if verdict == 'switch' and target.get('status') not in MATCHABLE:
        verdict = 'blocked:target_not_open'
    if verdict == 'switch' and (target.get('seniority') != 'senior') and (applied.get('seniority') == 'senior'):
        # 中高階不用做工作風格測驗，一般職缺要——改過去會被面談室擋去做 48 題，不改
        has = rows(f'SELECT 1 x FROM assessments WHERE application_id={q(app_id)} LIMIT 1')
        if not has:
            verdict = 'blocked:needs_assessment'
    # 給人選聽的那半句：有問題就拿掉，不擋改談
    if opening and (PROTECTED_RE.search(opening) or CLIENT_WORD_RE.search(opening)
                    or any(t in opening for t in client_tokens(applied, target or {}))):
        opening = ''
    if verdict == 'switch' and not dry_run and auto_switches_today() >= DAILY_CAP:
        verdict = 'blocked:daily_cap'

    summary = f'{verdict}｜{applied_slug} → {target_slug or "-"}｜{conf}｜{reason[:80]}'
    if dry_run:
        return json.dumps({'verdict': verdict, 'ai': d, 'opening_used': opening,
                           'note_preview': build_note_block(applied, target, reason, opening, '系統自動判斷') if target else None},
                          ensure_ascii=False, indent=1)
    if verdict != 'switch':
        _record_check(app_id, 'done', verdict, applied_slug, target_slug or None, conf, reason, raw)
        if verdict.startswith('blocked:') and verdict != 'blocked:target_not_open':
            log(f'{app.get("name")}：AI 建議改談 {target_slug} 但沒改（{verdict}）')
        return summary
    res = apply_switch(app, applied, target, reason, opening, source='auto', by='阿財（自動判斷）')
    _record_check(app_id, 'done', 'switched' if res.get('ok') else 'blocked:' + res.get('error', 'apply_failed'),
                  applied_slug, target_slug, conf, reason, raw)
    return summary + ('' if res.get('ok') else f'｜套用失敗：{res.get("error")}')


def apply_switch(app, orig, target, reason, opening, source, by):
    """真正改談。只在「還沒開始面談、主職缺還是原本那個、聊天室還沒有任何訊息」時才會改到。"""
    app_id = app['id']
    label = '系統自動判斷 ' + now_tw()[:16] if source == 'auto' else f'{by} {now_tw()[:16]}'
    block = build_note_block(orig, target, reason, opening, label)
    r = db(f"""UPDATE applications SET job_slug={q(target['slug'])}, job_title={q(target['title'])},
                pre_interview_note={q(block)} || COALESCE(pre_interview_note,''),
                pre_interview_note_at={q(now_tw())},
                prewarmed_opening=NULL, prewarmed_at=NULL
              WHERE id={q(app_id)} AND job_slug={q(orig['slug'])}
                AND COALESCE(interview_state,'not_started')='not_started'
                AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.application_id={q(app_id)})
                AND NOT EXISTS (SELECT 1 FROM application_job_switches s WHERE s.application_id={q(app_id)} AND s.status='active')""")
    if not (r.get('meta') or {}).get('changes'):
        return {'ok': False, 'error': 'already_started_or_changed'}
    sid = str(uuid.uuid4())
    db(f"INSERT INTO application_job_switches (id, application_id, orig_job_slug, orig_job_title, target_job_slug, target_job_title, "
       f"source, reason, note_block, created_by, created_at, status, worker_id) VALUES ({q(sid)}, {q(app_id)}, {q(orig['slug'])}, "
       f"{q(orig['title'])}, {q(target['slug'])}, {q(target['title'])}, {q(source)}, {q(reason[:600])}, {q(block)}, {q(by)}, {q(now_tw())}, 'active', {q(WORKER_ID)})")
    add_candidate_note(app_id, f'🔁 面談前改成以「{target["title"]}」面談（原應徵：{orig["title"]}）。理由：{reason[:300]}\n'
                               f'阿財開場會徵求同意；不方便就改回談原職缺，面談結束後系統會自動判斷要不要改回原職缺。', by)
    msg = (f'🔁 阿財會改談〈{short_title(target["title"])}〉（原應徵〈{short_title(orig["title"])}〉）：{app.get("name")}\n'
           f'理由：{reason[:200]}\n人選不同意會自動回到〈{short_title(orig["title"])}〉。\n'
           f'不要改談就按下面「取消改談」（面談開始前都可以取消）。')
    mid = tg(msg, [[{'text': '↩️ 取消改談', 'callback_data': f'jsw_cancel:{sid}'}]])
    if mid:
        db(f'UPDATE application_job_switches SET tg_message_id={q(str(mid))} WHERE id={q(sid)}')
    log(f'{app.get("name")}：已改談 {orig["slug"]} → {target["slug"]}')
    return {'ok': True, 'switch_id': sid}


# ───────────────────────── ② 面談後：談的是哪一個 ─────────────────────────

def scan_post():
    sw = rows("""SELECT s.id FROM application_job_switches s JOIN applications a ON a.id = s.application_id
                  WHERE s.status='active' AND a.interview_state='done'
                    -- 等報告產完（finish() 是先標 done 再產報告），或結束超過 20 分鐘
                    AND (EXISTS (SELECT 1 FROM reports r WHERE r.application_id = a.id)
                         OR a.interview_ended_at <= datetime('now','+8 hours','-20 minutes'))
                    AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.id = 'jsw-check-' || s.id)
                  LIMIT 5""")
    n = 0
    for r in sw:
        if queue_job('jsw-check-' + r['id'], 'job_switch_check', {'switch_id': r['id']}):
            n += 1
            log(f'排入面談後判斷：{r["id"][:8]}')
    return n


CHECK_PROMPT = """下面是 AI 面談助理「阿財」跟一位求職者的面談逐字稿。
面談開始時，阿財會問求職者要不要改談另一個職缺：
- 原本應徵的職缺（ORIG）：{orig}
- 建議改談的職缺（TARGET）：{target}

請判斷這場面談「實際上主要談的是哪一個職缺」：
- 求職者同意改談、後面都在談 TARGET → TARGET
- 求職者不同意、或要求談原本的，後面談的是 ORIG → ORIG
- 逐字稿太短、還沒走到這一步、或真的看不出來 → UNKNOWN
面談結尾阿財通常會說一句「今天我們聊的是〇〇這個機會」，以那句為主要依據。

只回一個詞：ORIG、TARGET 或 UNKNOWN。

## 逐字稿
{transcript}"""


def decide_discussed(transcript, orig_title, target_title, run_claude=None):
    run_claude = run_claude or default_run_claude
    if len(transcript.strip()) < 40:
        return 'UNKNOWN'
    t = transcript if len(transcript) <= 30000 else transcript[:12000] + '\n…（中間略）…\n' + transcript[-16000:]
    out = run_claude(CHECK_PROMPT.format(orig=orig_title, target=target_title, transcript=t), want_json=False, timeout=180)
    m = re.findall(r'\b(ORIG|TARGET|UNKNOWN)\b', out.upper())
    return m[-1] if m else 'UNKNOWN'


def run_check(payload, run_claude=None, dry_run=False, transcript_override=None):
    sid = payload['switch_id']
    s = rows(f'SELECT * FROM application_job_switches WHERE id={q(sid)}')
    if not s:
        return 'skip: 找不到改談紀錄'
    sw = s[0]
    if not dry_run and sw['status'] != 'active':
        return f'skip: 狀態已是 {sw["status"]}'
    app = (rows(f"SELECT id, name, job_slug, job_title, interview_state FROM applications WHERE id={q(sw['application_id'])}") or [{}])[0]
    if transcript_override is not None:
        transcript = transcript_override
    else:
        conv = rows(f"SELECT role, content FROM messages WHERE application_id={q(sw['application_id'])} ORDER BY id ASC")
        transcript = '\n'.join(f'{"阿財" if m["role"] == "assistant" else "求職者"}：{m["content"]}'
                               for m in conv if m.get('content') != '（候選人已進入面談室）'
                               and not __import__('interview_markers').is_transition(m.get('content')))
    result = decide_discussed(transcript, sw['orig_job_title'], sw['target_job_title'], run_claude)
    if dry_run:
        return result
    claim = db(f"UPDATE application_job_switches SET status='checking', worker_id={q(WORKER_ID)} WHERE id={q(sid)} AND status='active'")
    if not (claim.get('meta') or {}).get('changes'):
        return 'skip: 已被別台處理'
    name = app.get('name') or ''
    xs, ys = short_title(sw['orig_job_title']), short_title(sw['target_job_title'])
    has_report = rows(f"SELECT COUNT(*) n FROM reports WHERE application_id={q(sw['application_id'])}")
    has_report = bool(has_report and has_report[0]['n'])
    if result == 'ORIG':
        r = db(f"UPDATE applications SET job_slug={q(sw['orig_job_slug'])}, job_title={q(sw['orig_job_title'])} "
               f"WHERE id={q(sw['application_id'])} AND job_slug={q(sw['target_job_slug'])}")
        changed = bool((r.get('meta') or {}).get('changes'))
        status = 'reverted' if changed else 'unclear'
        note = (f'面談後判斷：人選談的是原應徵的「{sw["orig_job_title"]}」，'
                + ('已把職缺改回原職缺。' if changed else '但這筆的職缺已經被改成別的，沒有動它，請顧問確認。'))
        add_candidate_note(sw['application_id'], '↩️ ' + note, '系統')
        msg = (f'↩️ {name}：面談談的是原應徵〈{xs}〉，' + ('已自動改回〈' + xs + '〉。' if changed else '但職缺已被改過，沒有自動改回，請確認。')
               + (f'\n⚠️ 阿財的報告是用〈{ys}〉的條件比對的，看報告時請留意（沒有自動重產）。' if has_report else ''))
    elif result == 'TARGET':
        status = 'kept'
        note = f'面談後判斷：人選同意改談，面談談的是「{sw["target_job_title"]}」，職缺維持不變。'
        add_candidate_note(sw['application_id'], '✅ ' + note, '系統')
        msg = f'✅ {name}：同意改談，面談談的是〈{ys}〉，職缺維持〈{ys}〉（原應徵〈{xs}〉）。'
    else:
        status = 'unclear'
        note = '面談後判斷：逐字稿看不出最後談的是哪個職缺，職缺維持不變，請顧問確認。'
        add_candidate_note(sw['application_id'], '❓ ' + note, '系統')
        msg = f'❓ {name}：看不出面談最後談的是〈{ys}〉還是原應徵〈{xs}〉，職缺先維持〈{ys}〉，請顧問看逐字稿確認。'
    db(f"UPDATE application_job_switches SET status={q(status)}, checked_at={q(now_tw())}, check_result={q(result)}, "
       f"check_note={q(note)} WHERE id={q(sid)}")
    tg(msg)
    return f'{status}｜{result}'


# ───────────────────────── 給 ai_worker 掛的入口 ─────────────────────────

def scan_all():
    n = 0
    try:
        n += scan_fit()
    except Exception as e:
        log(f'面談前改談掃描出錯（不影響其他工作）：{str(e)[:150]}')
    try:
        n += scan_post()
    except Exception as e:
        log(f'面談後判斷掃描出錯（不影響其他工作）：{str(e)[:150]}')
    return n


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['fit', 'check', 'scan'])
    ap.add_argument('id', nargs='?')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--resume-file')
    ap.add_argument('--transcript-file')
    a = ap.parse_args()
    if a.cmd == 'scan':
        print(scan_all())
    elif a.cmd == 'fit':
        ro = open(a.resume_file, encoding='utf-8').read() if a.resume_file else None
        print(run_fit({'application_id': a.id}, dry_run=a.dry_run, resume_override=ro))
    else:
        to = open(a.transcript_file, encoding='utf-8').read() if a.transcript_file else None
        print(run_check({'switch_id': a.id}, dry_run=a.dry_run, transcript_override=to))


if __name__ == '__main__':
    main()
