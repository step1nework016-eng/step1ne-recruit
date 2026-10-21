"""阿財面談結尾：推薦最多 3 個「其他」職缺（2026-10-21，Jacky 核准樣板：
~/下載項目/阿財推薦職缺_對話樣板_20261021.html）。

流程（interview_daemon.finish() 呼叫 generate()，面談正常收尾才做，中途離開不做）：
  1. 開關：D1 site_settings key='acai_job_recs'，值 off／test／on（沒設＝off）。
     test＝只有姓名含「測試」的應徵才產；on＝全部。
  2. 候選職缺：status='open'、有公開職缺頁（step1ne.com/jobs/<slug>/ 回 200）、
     不是這次面談的職缺、不是他已經應徵過的職缺。
     有人才標籤（candidate_tags）就先用 recommendation_service.tag_compatible 篩掉明顯不相干的。
  3. AI 只挑「方向接近」的，最多 3 個；「為什麼可能適合／還要確認」各一句，
     而且要附人選原話，原話對不上逐字稿就整個丟掉（不准編）。
  4. 每一個要給人選看的字都過兩道門：客戶名守門員（跟官網 predeploy 同一份名單規則）、
     保護特徵字眼（年齡／性別／國籍／婚育／宗教）。命中就整個職缺不推。
  5. 存進 acai_job_recs（每職缺一列）＋ acai_job_rec_runs（這場的狀態：pending／ready／none／error），
     面談頁用 public-worker 的 GET /chat/<token>/recs 讀；人選按「也幫我推這個」走
     POST /chat/<token>/recs-want，寫 wanted_at、TG 通知顧問。

給人選看的欄位只從白名單來：title、locations（縮到縣市區）、salary_note（濾掉顧問內部口徑，
跟 public-worker 的 publicSalaryNote() 同一套）、employment（publicEmployment()）、
jd_spec_json 的工作內容。jobintake/jd_ai_draft_tick.py 的 CONSULTANT_ONLY 欄位一律不讀。
"""
import datetime
import json
import os
import re
import time
import urllib.request

import d1_http
import recommendation_service as RS

FLAG_KEY = 'acai_job_recs'
MAX_RECS = 3
CARD_TITLE = '您可能也適合這些職缺'
CARD_SUB = '依您今天聊的內容挑的，方向接近，顧問會再確認。點職缺看摘要'
CARD_FOOTER = '這是依面談內容初步挑的，不代表一定會推薦；顧問確認後會再跟您說。'
JOB_URL = 'https://step1ne.com/jobs/{slug}/'
PROMPT_VERSION = 'acai-recs-2026-10-21'
# 我們自己的佔位職缺，不是真的職缺
SKIP_SLUGS = ('unspecified',)

_q = RS._q


def log(msg):
    RS.log(f'[阿財推薦職缺] {msg}')


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── 開關 ──────────────────────────────────────────────────────────
def get_flag():
    try:
        r = d1_http.query(f"SELECT value FROM site_settings WHERE key={_q(FLAG_KEY)}")['results']
        v = str((r[0].get('value') if r else '') or '').strip().lower()
        return v if v in ('off', 'test', 'on') else 'off'
    except Exception as e:
        log(f'讀開關失敗，當作關閉：{e}')
        return 'off'


def eligible(flag, name):
    if flag == 'on':
        return True
    if flag == 'test':
        return '測試' in str(name or '')
    return False


# ── 對外文字（跟 step1ne-public-worker/src/index.js 的 publicSalaryNote／publicEmployment 同一套）──
SAL_INTERNAL = re.compile(r'⚠️|僅供|不得|不要|勿對|內部|口徑|顧問口頭|對外與|只講|研判|尚未跟客戶|未確認')
SAL_FALLBACK = '依經歷面議，顧問會再跟您說明'


def public_salary_note(t):
    if not t:
        return ''
    out = []
    for line in re.sub(r'【對人選[^】]*】', '', str(t)).split('\n'):
        if re.search(r'⚠️|以下僅供', line):
            break
        for seg in re.split(r'(?<=[。；;])', line):
            x = seg.strip()
            if x and not SAL_INTERNAL.search(x):
                out.append(x)
    return ''.join(out).strip() or SAL_FALLBACK


EMP_ZH = {'FULL_TIME': '正職', 'PART_TIME': '兼職', 'CONTRACTOR': '約聘', 'TEMPORARY': '定期契約',
          'INTERN': '實習', 'PER_DIEM': '日薪', 'VOLUNTEER': '志工', 'OTHER': ''}


def public_employment(v):
    if not v:
        return ''
    x = v
    try:
        x = json.loads(v)
    except Exception:
        pass
    parts = x if isinstance(x, list) else re.split(r'[、,]', str(x))
    parts = [re.sub(r'["\[\]]', '', str(p)).strip() for p in parts]
    out = []
    for t in (p for p in parts if p):
        z = EMP_ZH[t.upper()] if t.upper() in EMP_ZH else re.split(r'[（(，,；;]', t)[0].strip()
        if z and z not in out:
            out.append(z)
    if '正職' in out and '定期契約' in out:
        return '全職・定期契約'
    return '・'.join(out)


def short_salary(job):
    """列表那一行用的短版：公開薪資說明的第一句，最多 34 字。"""
    t = public_salary_note(job.get('salary_note'))
    if not t:
        return SAL_FALLBACK
    first = re.split(r'(?<=[。；;])', t)[0].rstrip('。；; ')
    if len(first) <= 34:
        return first
    cut = first[:34]
    i = max(cut.rfind('，'), cut.rfind(','))
    return cut[:i] if i >= 10 else cut[:33] + '…'


def short_location(loc):
    """地點縮到縣市／區：拿掉括號說明、路名門牌（2026-09-03 社群文門牌外洩事故，這裡一樣擋）。"""
    if not loc:
        return ''
    s = str(loc).strip()
    if s.startswith('['):
        try:
            s = '、'.join(str(x) for x in json.loads(s.replace("'", '"')))
        except Exception:
            s = s.strip('[]').replace("'", '')
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    parts = []
    for piece in re.split(r'[、，,；;・．]', s):
        piece = piece.strip()
        if re.search(r'路|街|大道|號|樓', piece):   # 路名＋門牌：只留到「區／鎮／鄉／市」為止
            m = re.match(r'^(.*?(?:區|鎮|鄉|市))(?=[^區鎮鄉市]*(?:路|街|大道|號|樓))', piece)
            piece = m.group(1) if m else ''
        if piece and piece not in parts:
            parts.append(piece)
    s = '、'.join(parts)
    return s if len(s) <= 28 else s[:27] + '…'


def duty_text(job):
    """「做什麼」：優先用職缺頁的公開文案（jd_spec_json.duties），沒有才用 main_duties 第一段。"""
    spec = RS._json_loads(job.get('jd_spec_json'), {}) or {}
    duties = spec.get('duties')
    if isinstance(duties, str):
        try:
            import ast
            duties = ast.literal_eval(duties)
        except Exception:
            try:
                duties = json.loads(duties)
            except Exception:
                duties = None
    items = []
    if isinstance(duties, list):
        for g in duties:
            if isinstance(g, dict):
                items += [str(i) for i in (g.get('items') or [])]
            elif isinstance(g, str):
                items.append(g)
            if len(items) >= 2:
                break
    t = '、'.join(items[:2])
    if not t:
        md = str(job.get('main_duties') or '')
        md = re.sub(r'【[^】]*】|^\s*\d+[).、]\s*', '', md)
        t = re.split(r'[；;\n]|(?<=。)', md.strip())[0].strip(' 。')
    return t if len(t) <= 40 else t[:39] + '…'


# ── 客戶名守門員（規則照 step1ne-stopgap-site/scripts/check_no_client_names.py）──
ALLOWED = ('德仁管理顧問', 'Step1ne', 'step1ne', 'STEP1NE', '私人招待所', '全民獵才')
SUFFIXES = ('股份有限公司', '有限公司', '管理顧問', '科技', '集團', '公司',
            '企業社', '工程行', '事業處', '株式會社', '有限會社', '國際開發')
VARIANTS = str.maketrans({
    '楽': '樂', '国': '國', '発': '發', '会': '會', '営': '營', '売': '賣', '総': '總',
    '産': '產', '学': '學', '広': '廣', '栄': '榮', '関': '關', '区': '區', '実': '實',
    '鉄': '鐵', '気': '氣', '経': '經', '済': '濟', '証': '證', '薬': '藥', '医': '醫',
    '団': '團', '図': '圖', '豊': '豐', '沢': '澤', '浜': '濱', '辺': '邊', '県': '縣',
    '乐': '樂', '发': '發', '际': '際', '开': '開', '业': '業', '产': '產', '团': '團',
    '华': '華', '电': '電', '东': '東', '贸': '貿', '汇': '匯', '银': '銀', '药': '藥',
    '阳': '陽', '达': '達', '时': '時', '创': '創', '兴': '興', '机': '機', '车': '車',
    '龙': '龍', '丰': '豐', '宝': '寶', '联': '聯', '万': '萬', '门': '門', '网': '網',
    '传': '傳', '讯': '訊', '软': '軟', '设': '設', '计': '計', '筑': '築', '远': '遠',
})
COMPANY_PAT = re.compile(r'[一-鿿A-Za-z0-9．\.]{2,12}'
                         r'(?:股份有限公司|有限公司|企業社|工程行|實業社|株式會社|有限會社)')


def _norm(t):
    return (t or '').translate(VARIANTS)


class ClientGuard:
    def __init__(self, raw_names):
        names = set()
        for n in raw_names:
            n = _norm(str(n or '').strip())
            if len(n) < 2:
                continue
            names.add(n)
            for part in re.split(r'[（）()／/、;；,，:：「」\s]+', n):   # 括號裡的英文名／別名也要擋
                if len(part) >= 2:
                    names.add(part)
            base = re.sub(r'[（(].*?[)）]', '', n).strip()
            for suf in SUFFIXES:
                base = base.replace(suf, '')
            if len(base.strip()) >= 2:
                names.add(base.strip())
        generic = {'集團', '公司', '旗下', '總部', '台灣', '日本', '法人', '正式法人全名待顧問補',
                   '待顧問補', '新聞媒體', '支付服務組', '日本法人', '台灣總代理', '總代理'}
        generic_en = {'group', 'holdings', 'inc', 'co', 'ltd', 'corp', 'company', 'limited', 'taiwan', 'japan'}
        self.names = sorted({n for n in names if n not in generic and n.lower().strip('.') not in generic_en
                             and not any(a in n or n in a for a in ALLOWED)}, key=len, reverse=True)

    def hits(self, text):
        t = _norm(str(text or ''))
        out = [n for n in self.names if n.lower() in t.lower()]
        out += [m.group(0) for m in COMPANY_PAT.finditer(t) if not any(a in m.group(0) for a in ALLOWED)]
        return out

    @classmethod
    def from_d1(cls):
        rows = d1_http.query('SELECT display_name, aliases FROM client_companies')['results'] or []
        raw = []
        for r in rows:
            raw.append(r.get('display_name'))
            al = str(r.get('aliases') or '')
            if al.strip().startswith('['):
                try:
                    raw += [str(x) for x in json.loads(al)]
                except Exception:
                    pass
            else:
                raw += re.split(r'[\n,、;；]', al)
        raw += [r.get('client_name') for r in (d1_http.query(
            "SELECT DISTINCT client_name FROM jobs WHERE COALESCE(client_name,'')!=''")['results'] or [])]
        raw += [r.get('dispatch_client') for r in (d1_http.query(
            "SELECT DISTINCT dispatch_client FROM jobs WHERE COALESCE(dispatch_client,'')!=''")['results'] or [])]
        return cls(raw)


# ── 保護特徵／承諾字眼 ──────────────────────────────────────────────
_EXTRA_SENSITIVE = re.compile(r'年齡|年紀|歲|性別|國籍|外籍|新住民|籍貫|婚|宗教|信仰|教會|佛教|基督|回教|伊斯蘭|懷孕|生育|小孩|兵役')
_PROMISE = re.compile(r'保證|一定(?:會)?(?:錄取|推薦|上|適合)|包上|穩上|錄取|已經幫您推薦|會幫您推薦')


def sensitive_hit(text):
    t = str(text or '')
    return bool(_EXTRA_SENSITIVE.search(t)) or RS.scrub_sensitive(t) != t


def promise_hit(text):
    return bool(_PROMISE.search(str(text or '')))


# ── 候選職缺 ──────────────────────────────────────────────────────
_PAGE_CACHE = {}
PAGE_CACHE_SEC = 1800


def has_public_page(slug):
    hit = _PAGE_CACHE.get(slug)
    if hit and time.time() - hit[1] < PAGE_CACHE_SEC:
        return hit[0]
    ok = False
    try:
        req = urllib.request.Request(JOB_URL.format(slug=slug), method='HEAD',
                                     headers={'user-agent': 'step1ne-interview-daemon/1.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    _PAGE_CACHE[slug] = (ok, time.time())
    return ok


JOB_COLS = ('slug, title, locations, salary_note, employment, main_duties, jd_spec_json, must_skills, '
            'years_min, education_level, language_requirement, seniority, salary_min, salary_max, salary_unit, status')


def load_candidate_jobs(app_id, applied_slug, email=None):
    """開著、有公開頁、不是這次面談、不是他已經應徵／被推薦過的職缺。回傳 (jobs, 篩掉的原因)。"""
    rows = d1_http.query(f"SELECT {JOB_COLS} FROM jobs WHERE status='open' ORDER BY updated_at DESC LIMIT 80")['results'] or []
    skip = set(SKIP_SLUGS) | {applied_slug} | set(RS.existing_job_slugs_for_candidate(app_id, email=email) or ())
    jobs = [j for j in rows if j.get('slug') and j['slug'] not in skip and str(j.get('status')) == 'open']
    jobs = [j for j in jobs if has_public_page(j['slug'])]
    dropped = []
    try:
        jobs, dropped = RS.tag_prefilter_jobs(app_id, jobs)   # 人選有標籤才會篩；沒標籤原樣放行
    except Exception as e:
        log(f'標籤篩選失敗，略過這一步：{e}')
    try:
        jt = RS.get_job_tags_bulk([j['slug'] for j in jobs])
        for j in jobs:
            j['_tags'] = (jt.get(j['slug']) or {}).get('tags') or {}
    except Exception:
        pass
    return jobs, dropped


def public_job_view(job):
    """一個職缺「可以給人選看」的版本。只有白名單欄位。"""
    return {
        'slug': job['slug'],
        'title': str(job.get('title') or '').strip(),
        'location': short_location(job.get('locations')),
        'salary': short_salary(job),
        'employment': public_employment(job.get('employment')),
        'duty': duty_text(job),
        'url': JOB_URL.format(slug=job['slug']),
    }


# ── AI 挑選 ───────────────────────────────────────────────────────
def _candidate_text(ctx):
    conv = [m for m in (ctx.get('conversation') or [])
            if str(m.get('content') or '').strip() and m.get('content') != '（候選人已進入面談室）']
    lines = []
    for m in conv:
        who = '人選' if m.get('role') == 'candidate' else '阿財'
        lines.append(f"{who}：{RS.scrub_sensitive(str(m.get('content'))[:600])}")
    t = '\n'.join(lines)
    return t[-14000:]


def _said_text(ctx):
    return ''.join(str(m.get('content') or '') for m in (ctx.get('conversation') or [])
                   if m.get('role') == 'candidate')


def _squash(t):
    return re.sub(r'[\s，,。.！!？?、：:；;「」『』"\'（）()…~～-]', '', _norm(str(t or ''))).lower()


def build_prompt(ctx, jobs, cand_tags=None):
    app = ctx.get('application') or {}
    form = {
        '期望待遇': RS._cut(app.get('expected_salary'), 80),
        '可上班地點': RS._cut(app.get('location_ok'), 160),
    }
    note = str(app.get('note') or '')
    if note.startswith('【快速應徵'):
        keep = ('相關經驗', '會用的軟體', '相關證照', '期望工作城市', '居住縣市', '最高學歷', '學校與科系', '科系')
        form['快速應徵表單'] = [l for l in note.split('\n') if any(l.startswith(k) for k in keep)][:8]
    job_list = []
    for j in jobs:
        v = public_job_view(j)
        job_list.append({
            'slug': v['slug'], '職稱': v['title'], '地點': v['location'], '薪資': v['salary'],
            '類型': v['employment'], '做什麼': v['duty'],
            '必備': RS._cut(j.get('must_skills'), 140), '年資下限': j.get('years_min'),
            '學歷': RS._cut(j.get('education_level'), 40), '語言': RS._cut(j.get('language_requirement'), 60),
            '職務類別': j.get('_tags') or {},
        })
    tags = ''
    if cand_tags:
        tags = '\n【這位人選之前打過的職務類別】\n' + json.dumps(cand_tags.get('tags') or {}, ensure_ascii=False)
    return f"""你是獵頭公司的 AI 面談助理「阿財」。一場面談剛結束，請從下面「開放中的其他職缺」裡，
挑出跟這位人選**方向接近**的，最多 {MAX_RECS} 個，給他在面談室最後參考。

規則（全部都要遵守）：
1. 只能從清單裡挑，slug 照抄。沒有明顯接近的就回空陣列——寧可不推，不要硬湊。
2. fit 只能填「高」或「中」：高＝職務內容、地點、薪資方向都對得上；中＝職務接近但有一項要確認。
   對不上的不要列出來。
3. why（為什麼可能適合）、confirm（還要確認）各一句，各 30 字以內，用「您」稱呼，
   **只能根據人選今天自己講過的話或表單寫的內容**，不准編他沒說過的經歷、證照、意願。
4. evidence 要填人選**原話**的一小段（照抄逐字稿，6～30 字），用來證明 why 不是編的。
5. 不准提公司名稱，只講職務。不准用年齡、性別、國籍、婚姻、家庭、宗教當理由或寫進任何一句。
6. 不准承諾：不可寫「一定」「保證」「會幫您推薦」「錄取」。語氣是「方向接近，顧問會再確認」。
7. 地點：人選明確說不考慮的地區不要推；海外職缺只有人選自己提過願意去海外才推。

【人選表單】
{json.dumps(form, ensure_ascii=False, indent=1)}{tags}

【今天的面談逐字稿】
{_candidate_text(ctx)}

【開放中的其他職缺】
{json.dumps(job_list, ensure_ascii=False, indent=1)}

只輸出一個 JSON 物件，不要其他文字：
{{"picks": [{{"slug": "...", "fit": "高", "why": "...", "confirm": "...", "evidence": "人選原話"}}]}}"""


def validate_picks(out, jobs_by_slug, guard, said_text):
    """AI 結果 → 可以給人選看的推薦。任何一項不過就整個職缺丟掉，回傳 (kept, dropped)。"""
    picks = (out or {}).get('picks') if isinstance(out, dict) else None
    kept, dropped = [], []
    said = _squash(said_text)
    seen = set()
    for p in (picks if isinstance(picks, list) else []):
        if not isinstance(p, dict):
            continue
        slug = str(p.get('slug') or '').strip()
        why, confirm = str(p.get('why') or '').strip(), str(p.get('confirm') or '').strip()
        ev = str(p.get('evidence') or '').strip()
        fit = str(p.get('fit') or '').strip()

        def drop(reason):
            dropped.append({'slug': slug, 'reason': reason})
        if slug not in jobs_by_slug or slug in seen:
            drop('不在候選清單／重複'); continue
        if fit not in ('高', '中'):
            drop(f'適配度 {fit or "空"}'); continue
        if not why or not confirm or len(why) > 45 or len(confirm) > 45:
            drop('理由空白或太長'); continue
        evs = _squash(ev)
        if len(evs) < 4 or evs not in said:
            drop(f'原話對不上逐字稿：{ev[:30]}'); continue
        view = public_job_view(jobs_by_slug[slug])
        view.update({'why': why, 'confirm': confirm, 'fit': fit})
        shown = ' '.join(str(view[k]) for k in ('title', 'location', 'salary', 'employment', 'duty', 'why', 'confirm'))
        if guard.hits(shown):
            drop(f'客戶名守門員命中：{guard.hits(shown)[:2]}'); continue
        if sensitive_hit(why + confirm):
            drop('理由出現保護特徵字眼'); continue
        if promise_hit(why + confirm):
            drop('理由出現承諾字眼'); continue
        seen.add(slug)
        view['evidence'] = ev[:60]   # 只存著給顧問看，不回給人選
        kept.append(view)
    kept.sort(key=lambda v: 0 if v['fit'] == '高' else 1)
    return kept[:MAX_RECS], dropped


# ── 給人選看的三種輸出 ──────────────────────────────────────────────
CANDIDATE_FIELDS = ('slug', 'title', 'location', 'salary', 'employment', 'duty', 'why', 'confirm', 'url')


def build_card(recs):
    if not recs:
        return None
    return {'title': CARD_TITLE, 'sub': CARD_SUB, 'footer': CARD_FOOTER,
            'jobs': [{k: r.get(k) for k in CANDIDATE_FIELDS} for r in recs[:MAX_RECS]]}


def email_lines(recs):
    """感謝信裡的一段：職稱＋公開薪資＋連結。"""
    if not recs:
        return []
    out = ['另外，依您今天聊的內容，下面幾個職缺方向也接近，可以參考看看（顧問會再確認是否適合）：']
    for i, r in enumerate(recs[:MAX_RECS], 1):
        out.append(f"{i}. {r['title']}｜{r['salary']}｜{r['url']}")
    out.append('有興趣的話回覆這封信或在 LINE 告訴我們就可以。')
    return out


def report_md(recs, wanted=()):
    if not recs:
        return ''
    lines = ['', '## 阿財跟他提過的職缺', '（面談結尾在對話裡推薦；人選按「也幫我推這個」的會另外 TG 通知、顯示在人選卡片）']
    for i, r in enumerate(recs, 1):
        mark = '　✅ 人選按了「也幫我推這個」' if r['slug'] in wanted else ''
        lines.append(f"{i}. {r['title']}（{r['slug']}）適配：{r.get('fit') or '—'}{mark}")
        lines.append(f"   為什麼：{r.get('why')}｜還要確認：{r.get('confirm')}｜人選原話：「{r.get('evidence') or ''}」")
    return '\n'.join(lines)


# ── 存取 ──────────────────────────────────────────────────────────
_TABLES_READY = False


def ensure_tables():
    global _TABLES_READY
    if _TABLES_READY:
        return
    d1_http.query(
        'CREATE TABLE IF NOT EXISTS acai_job_recs ('
        ' application_id TEXT NOT NULL, job_slug TEXT NOT NULL, rank INTEGER, title TEXT,'
        ' payload_json TEXT, created_at TEXT NOT NULL, wanted_at TEXT, notified_at TEXT,'
        ' PRIMARY KEY (application_id, job_slug))')
    d1_http.query(
        'CREATE TABLE IF NOT EXISTS acai_job_rec_runs ('
        ' application_id TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT, detail TEXT,'
        ' created_at TEXT NOT NULL, updated_at TEXT)')
    _TABLES_READY = True


def _set_run(app_id, status, mode, detail=''):
    now = _now()
    d1_http.query(
        'INSERT INTO acai_job_rec_runs (application_id, status, mode, detail, created_at, updated_at) VALUES ('
        f"{_q(app_id)}, {_q(status)}, {_q(mode)}, {_q(str(detail)[:1500])}, {_q(now)}, {_q(now)}) "
        'ON CONFLICT(application_id) DO UPDATE SET status=excluded.status, mode=excluded.mode, '
        'detail=excluded.detail, updated_at=excluded.updated_at')


def save(app_id, recs):
    now = _now()
    for i, r in enumerate(recs, 1):
        d1_http.query(
            'INSERT OR IGNORE INTO acai_job_recs (application_id, job_slug, rank, title, payload_json, created_at) VALUES ('
            f"{_q(app_id)}, {_q(r['slug'])}, {i}, {_q(r['title'])}, {_q(json.dumps(r, ensure_ascii=False))}, {_q(now)})")


def fetch_saved(app_id):
    """[(rec dict, wanted_at)]，照 rank 排。"""
    try:
        ensure_tables()
        rows = d1_http.query(f'SELECT payload_json, wanted_at FROM acai_job_recs WHERE application_id={_q(app_id)} '
                             'ORDER BY rank ASC')['results'] or []
    except Exception:
        return []
    out = []
    for r in rows:
        d = RS._json_loads(r.get('payload_json'), None)
        if d:
            out.append((d, r.get('wanted_at')))
    return out


def generate(app_id, ctx, llm, flag=None):
    """面談正常收尾時呼叫。回傳存好的推薦清單（可能是空的）。任何失敗都不往外丟。
    llm(prompt) → dict（interview_daemon.run_claude）。"""
    app = ctx.get('application') or {}
    name = app.get('name') or ''
    try:
        flag = flag or get_flag()
        if not eligible(flag, name):
            return []
        ensure_tables()
        if fetch_saved(app_id):
            return [d for d, _ in fetch_saved(app_id)]   # 同一場已經產過（重跑 finish）就不再產
        _set_run(app_id, 'pending', flag)
        jobs, tag_dropped = load_candidate_jobs(app_id, app.get('job_slug') or (ctx.get('job') or {}).get('slug'),
                                                email=app.get('email'))
        if not jobs:
            _set_run(app_id, 'none', flag, '沒有可推的開放職缺')
            return []
        guard = ClientGuard.from_d1()
        cand_tags = None
        try:
            cand_tags = RS.get_candidate_tags_bulk([app_id]).get(app_id)
        except Exception:
            pass
        out = llm(build_prompt(ctx, jobs, cand_tags))
        recs, dropped = validate_picks(out, {j['slug']: j for j in jobs}, guard, _said_text(ctx))
        detail = json.dumps({'pool': len(jobs), 'tag_dropped': len(tag_dropped), 'dropped': dropped,
                             'prompt_version': PROMPT_VERSION}, ensure_ascii=False)
        if not recs:
            _set_run(app_id, 'none', flag, detail)
            log(f'{name}：沒有夠接近的職缺，不顯示卡片（{len(jobs)} 個候選，丟掉 {len(dropped)}）')
            return []
        save(app_id, recs)
        _set_run(app_id, 'ready', flag, detail)
        log(f"{name}：推薦 {len(recs)} 個職缺：{'、'.join(r['title'] for r in recs)}")
        return recs
    except Exception as e:
        log(f'{name}：產生推薦職缺失敗（不影響面談與報告）：{e}')
        try:
            _set_run(app_id, 'error', flag or '?', str(e)[:300])
        except Exception:
            pass
        return []
