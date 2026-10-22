"""無經驗通用面談：「先跟阿財聊，再幫你選職缺」（2026-10-22，Jacky 核准設計；測試版）。

一般面談是「先選一個職缺 → 阿財照那個職缺的必要條件問」。這裡反過來：
人選從無經驗專區（測試版 /training-beta/ → /apply-general-beta/）進來時還沒選職缺，
應徵掛在系統用的空殼職缺 SENTINEL_SLUG（jobs.status='draft'、沒有工作內容，不會上官網／
職缺排序／AI 配對）。阿財改問「分辨得出適合哪個無經驗職缺」的題目，收尾時挑 2～4 個，
畫在面談室的職缺卡片上，人選最多選 2 個「我要應徵這個」——public-worker 的
POST /chat/<token>/general-pick 會替他建好那個職缺的應徵，逐字稿跟報告一起帶過去，不用重談。

開關（D1 site_settings key=FLAG_KEY）：off／test／on，沒設＝off。
  test＝只有姓名含「測試」或「ZZ-TEST」的應徵才走這個模式；on＝全部。
  開關是 off 時，就算應徵掛在空殼職缺上，阿財也照舊用一般流程（不會壞，只是沒有通用模式）。

候選職缺＝開著（status='open'）、職級是 entry／junior、有公開職缺頁、而且**不在 EXCLUDE_SLUGS**。
EXCLUDE_SLUGS 是「需要專業證照才能做」的無經驗職缺（例：護理師要護理師執照），要加減就改這一行。

給人選看的字只用 acai_job_recs 的白名單欄位（職稱／地點縮到縣市區／公開薪資／工作內容），
每一句都過客戶名守門員與保護特徵字眼；職缺清單給阿財看時也先濾掉客戶名、不給 dispatch_range
（裡面常有廠區真名）。年齡／性別／婚育／國籍不問、不當判斷依據（硬規則）。
"""
import datetime
import json
import re
import time

import d1_http
import acai_job_recs as AJR
import recommendation_service as RS

SENTINEL_SLUG = 'entry-general-match'
SENTINEL_TITLE = '無經驗通用面談（先聊再選職缺）'
FLAG_KEY = 'entry_general_match'
# 需要專業證照才能做的無經驗職缺，不放進通用面談的候選清單。
EXCLUDE_SLUGS = ('rehab-clinic-nurse-multi-site',)
ENTRY_SENIORITY = ('entry', 'junior')
MAX_RECS = 4
MAX_PICKS = 2
PROMPT_VERSION = 'entry-general-2026-10-22'
LINKS_TABLE = 'general_match_links'

_q = AJR._q


def log(msg):
    RS.log(f'[無經驗通用面談] {msg}')


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ── 開關 ──────────────────────────────────────────────────────────
_FLAG_CACHE = {'v': None, 't': 0.0}


def get_flag():
    if _FLAG_CACHE['v'] is not None and time.time() - _FLAG_CACHE['t'] < 60:
        return _FLAG_CACHE['v']
    v = 'off'
    try:
        r = d1_http.query(f"SELECT value FROM site_settings WHERE key={_q(FLAG_KEY)}")['results']
        v = str((r[0].get('value') if r else '') or '').strip().lower()
        v = v if v in ('off', 'test', 'on') else 'off'
    except Exception as e:
        log(f'讀開關失敗，當作關閉：{e}')
        v = 'off'
    _FLAG_CACHE.update(v=v, t=time.time())
    return v


def name_is_test(name):
    n = str(name or '')
    return '測試' in n or 'ZZ-TEST' in n.upper()


def is_general(app, flag=None):
    """這場要不要走無經驗通用面談：應徵掛在空殼職缺上、而且開關允許這個人。"""
    app = app or {}
    if app.get('job_slug') != SENTINEL_SLUG:
        return False
    flag = flag or get_flag()
    return flag == 'on' or (flag == 'test' and name_is_test(app.get('name')))


# ── 候選職缺 ──────────────────────────────────────────────────────
POOL_COLS = AJR.JOB_COLS + ', required_conditions, main_duties'


def load_pool():
    sen = ', '.join(_q(s) for s in ENTRY_SENIORITY)
    rows = d1_http.query(f"SELECT {POOL_COLS} FROM jobs WHERE status='open' AND seniority IN ({sen}) "
                         f"ORDER BY updated_at DESC LIMIT 30")['results'] or []
    out = []
    for j in rows:
        s = j.get('slug')
        if not s or s == SENTINEL_SLUG or s in EXCLUDE_SLUGS or s in AJR.SKIP_SLUGS:
            continue
        if not AJR.has_public_page(s):
            continue
        out.append(j)
    return out


# 客戶名單（client_companies）抓不到的廠區／品牌簡稱：職缺條件裡常寫「新竹台積電廠區駐點」這種，
# 守門員比的是正式名稱，所以另外列一份（2026-10-22 實測 semiconductor-bim-mep-drafting-trainee-hsinchu 就有）。
SITE_PAT = re.compile(r'台積|TSMC|美光|Micron|聯電|UMC|力積電|世界先進|華邦|南亞科|日月光|帆宣|漢唐|亞翔|'
                      r'[Ff]ab\s*\d+|[一二三四五六七八九十\d]+廠', re.I)


def _site_hit(text):
    return bool(SITE_PAT.search(str(text or '')))


def _clean(text, guard, n=160):
    """給阿財看的職缺文字：一段一段過客戶名守門員＋廠區名，命中的那段整段拿掉。"""
    segs = [x.strip(' ・-•') for x in re.split(r'[\n；;。]|\s(?=\d+[.、)])', str(text or '')) if x.strip()]
    keep = [x for x in segs if not guard.hits(x) and not _site_hit(x)]
    t = '；'.join(keep)
    return t if len(t) <= n else t[:n - 1] + '…'


def pool_brief(pool, guard=None):
    """給阿財（面談中）與報告產生器看的職缺清單——沒有公司名、沒有派駐廠區名。"""
    guard = guard or AJR.ClientGuard.from_d1()
    out = []
    for j in pool:
        v = AJR.public_job_view(j)
        cond = _clean(j.get('required_conditions'), guard)
        must = _clean(j.get('must_skills'), guard, 100)
        if must and cond and (must[:20] in cond or AJR._squash(must)[:20] in AJR._squash(cond)):
            must = ''   # 很多職缺兩欄寫的是同一段，不要重複給
        duty = v['duty'] if not _site_hit(v['duty']) else ''
        out.append({
            'slug': v['slug'], '職稱': v['title'], '地點': v['location'], '薪資公開說法': v['salary'],
            '類型': v['employment'], '做什麼': duty or _clean(j.get('main_duties'), guard, 80),
            '條件': cond, '必備': must,
            '學歷': _clean(j.get('education_level'), guard, 40),
        })
    return out


# ── 面談提示詞（interview_daemon.build_prompt 在通用模式時用這段取代【職缺與客戶】）──
def prompt_lines(ctx):
    gm = ctx.get('general_match') or {}
    brief = gm.get('brief') or []
    L = ['\n【這場是「無經驗通用面談」：不是針對單一職缺】',
         '  🚨 這一段的規則優先於上面規範裡「開場介紹職缺」「職缺必要條件逐條問」「到職障礙」這幾部分——'
         '這場人選還沒選職缺，沒有單一職缺可以介紹或逐條比對。',
         '  你的任務：用 8～10 題聊出他的基本條件與興趣，面談結束後系統會依今天的內容，'
         '從下面的無經驗職缺挑 2～4 個比較適合的給他看，**他自己決定要應徵哪幾個（最多 2 個）**。',
         '  開場：歡迎＋一句話說明「今天不是針對單一職缺，會先問幾個關於條件和興趣的問題，'
         '最後依聊的內容挑幾個比較適合的無經驗職缺給您看，您再自己決定要不要應徵」。'
         '開場不要列出所有職缺、不要報薪資。',
         '  要問到的（一題只問一件事，順著對話調順序；應徵表單已經有的用確認語氣帶過，不算一題）：',
         '   1. 住在哪個縣市／區；能不能接受外縣市或跨區調派；需要的話住宿舍可不可以',
         '   2. 有沒有機車駕照（有些廠區要騎車進出）、汽車駕照',
         '   3. 最高學歷與科系',
         '   4. 比較喜歡哪一種工作：坐在電腦前畫圖（AutoCAD／Revit 這類電腦繪圖）、'
         '在工地或現場跑、跟人協調，還是辦公室或櫃檯服務、行政——問他為什麼',
         '   5. 如果偏畫圖：有沒有用過繪圖軟體、學校作品或課程（不會也沒關係，問清楚程度就好）',
         '   6. 為什麼想找無經驗可的工作／想轉換跑道的原因',
         '   7. 最快什麼時候可以上班',
         '   8. 期望待遇的最低底線（他講數字就記下來，不要拿職缺薪資去壓他）',
         '  🚫 硬規則：不准問、也不准拿來判斷年齡、性別、婚姻、家庭、懷孕、國籍、宗教、外貌；'
         '他自己提到也不要追問、不要評論。',
         '  🚫 不要說出任何公司名稱、廠區名稱（含他用猜的）。也不要說「客戶」，一律說「企業」「用人單位」。',
         '  🚫 面談中**不要自己替他挑職缺、不要說「您適合 X」**，也不要承諾一定錄取或一定推薦；'
         '他問某一類工作在做什麼，可以用下面清單的公開資料簡短回答（職稱／做什麼／地點／薪資公開說法），回完拉回來繼續問。',
         '  收尾：謝謝他，說明「等一下畫面上會出現幾個依今天聊的內容挑的職缺，可以點開看，'
         '想應徵的按「我要應徵這個」，最多選 2 個；都不想選也沒關係，之後會有專人跟您聯絡」。'
         '🚨 收尾前逐項檢查上面 1～8，**特別是 7 最快上班時間、8 最低期望薪資**，沒問到的先補問再收尾'
         '（2026-10-22 測試場漏問到職日）。',
         '  ⏱ 節奏跟一般無經驗職缺面談一樣短（約 10 分鐘、8～10 題），不要變成長篇訪談。',
         '  下面是這次的無經驗職缺（只給你判斷要追問什麼，**不要一個一個介紹給他聽**）：']
    for b in brief:
        L.append(f"  · {b['職稱']}（{b['地點'] or '地點未提供'}）：{b['做什麼'] or '—'}｜薪資公開說法：{b['薪資公開說法']}")
        cond = '；'.join(x for x in (b.get('條件'), b.get('必備'), b.get('學歷')) if x)
        if cond:
            L.append(f'    條件：{cond}')
    if not brief:
        L.append('  （目前讀不到開放中的無經驗職缺——照樣把 1～8 問完，收尾說之後會有專人跟您聯絡）')
    return L


# ── 報告提示詞補充 ────────────────────────────────────────────────
def report_prompt_block(ctx):
    if not ctx.get('general_match'):
        return ''
    brief = (ctx.get('general_match') or {}).get('brief') or []
    return ('\n\n🧭 **這場是「無經驗通用面談（先聊再選職缺）」，不是針對單一職缺的面談。**'
            '報告第一行寫「面談類型：無經驗通用面談（先聊再選職缺）」。'
            '下面【職缺硬條件】是系統用的空殼職缺，**不要拿它比對硬條件、不要寫「不符合職缺條件」**；'
            '改成整理這幾項（每項附人選原話或標「未問到」）：居住地與可接受的工作地點／宿舍、機車與汽車駕照、'
            '學歷與科系、興趣方向（畫圖／現場協調／辦公室服務）、繪圖軟體程度、轉職動機、可到職日、最低期望薪資。'
            '最後一段「適合哪些無經驗職缺」依下面清單逐一寫適合／不適合與一句理由。'
            '不准用年齡、性別、婚姻、家庭、國籍當理由。\n【這次的無經驗職缺】\n'
            + json.dumps(brief, ensure_ascii=False, indent=1))


# ── 收尾：挑 2～4 個職缺 ──────────────────────────────────────────
def build_rank_prompt(ctx, brief):
    app = ctx.get('application') or {}
    form = {'期望工作地點': RS._cut(app.get('location_ok'), 120), '期望待遇': RS._cut(app.get('expected_salary'), 60),
            '可到職': RS._cut(app.get('available_date'), 40)}
    return f"""你是獵頭公司的 AI 面談助理「阿財」。剛結束一場「無經驗通用面談」：人選還沒選職缺，
請從下面的無經驗職缺裡，依今天的面談內容排出**最適合的 2～{MAX_RECS} 個**（真的只有 1 個對得上就給 1 個，都對不上就回空陣列），
讓他在畫面上自己選要應徵哪幾個。

規則（全部都要遵守）：
1. 只能從清單裡挑，slug 照抄。依適合程度排序，最適合的放第一個。
2. fit 只能填「高」或「中」：高＝地點／交通、興趣方向、學歷條件都對得上；中＝大方向對但有一項要確認。
   人選明確說不考慮的地區、或清單寫必備而他明確沒有的條件（例：一定要機車駕照、他說沒有也不考），不要列。
3. why：一句白話理由，30 字以內，用「您」稱呼，只能根據人選今天自己講過的話或表單內容。
   confirm：一句還要確認的事，30 字以內。
4. evidence 要填人選**原話**的一小段（照抄逐字稿，6～30 字），用來證明 why 不是編的。
5. 不准提公司或廠區名稱，只講職務。不准用年齡、性別、國籍、婚姻、家庭、宗教當理由或寫進任何一句。
6. 不准承諾：不可寫「一定」「保證」「會幫您推薦」「錄取」。

【人選表單】
{json.dumps(form, ensure_ascii=False, indent=1)}

【今天的面談逐字稿】
{AJR._candidate_text(ctx)}

【無經驗職缺清單】
{json.dumps(brief, ensure_ascii=False, indent=1)}

只輸出一個 JSON 物件，不要其他文字：
{{"picks": [{{"slug": "...", "fit": "高", "why": "...", "confirm": "...", "evidence": "人選原話"}}]}}"""


def validate_picks(out, jobs_by_slug, guard, said_text):
    """跟 acai_job_recs.validate_picks 同一套關卡，只差最多留 MAX_RECS 個、而且保留 AI 給的排序。"""
    picks = (out or {}).get('picks') if isinstance(out, dict) else None
    kept, dropped, seen = [], [], set()
    said = AJR._squash(said_text)
    for p in (picks if isinstance(picks, list) else []):
        if not isinstance(p, dict):
            continue
        slug = str(p.get('slug') or '').strip()
        why, confirm = str(p.get('why') or '').strip(), str(p.get('confirm') or '').strip()
        ev, fit = str(p.get('evidence') or '').strip(), str(p.get('fit') or '').strip()
        if slug not in jobs_by_slug or slug in seen:
            dropped.append({'slug': slug, 'reason': '不在清單／重複'}); continue
        if fit not in ('高', '中'):
            dropped.append({'slug': slug, 'reason': f'適配度 {fit or "空"}'}); continue
        if not why or not confirm or len(why) > 45 or len(confirm) > 45:
            dropped.append({'slug': slug, 'reason': '理由空白或太長'}); continue
        evs = AJR._squash(ev)
        if len(evs) < 4 or evs not in said:
            dropped.append({'slug': slug, 'reason': f'原話對不上逐字稿：{ev[:30]}'}); continue
        view = AJR.public_job_view(jobs_by_slug[slug])
        view.update({'why': why, 'confirm': confirm, 'fit': fit})
        shown = ' '.join(str(view[k]) for k in ('title', 'location', 'salary', 'employment', 'duty', 'why', 'confirm'))
        if guard.hits(shown) or _site_hit(shown):
            dropped.append({'slug': slug, 'reason': f'客戶名守門員命中：{guard.hits(shown)[:2] or "廠區名"}'}); continue
        if AJR.sensitive_hit(why + confirm):
            dropped.append({'slug': slug, 'reason': '理由出現保護特徵字眼'}); continue
        if AJR.promise_hit(why + confirm):
            dropped.append({'slug': slug, 'reason': '理由出現承諾字眼'}); continue
        seen.add(slug)
        view['evidence'] = ev[:60]   # 只存著給顧問看，不回給人選
        kept.append(view)
    return kept[:MAX_RECS], dropped


def generate(app_id, ctx, llm):
    """面談正常收尾時呼叫（取代一般的 acai_job_recs.generate）。存進 acai_job_recs／acai_job_rec_runs，
    run.mode='general'，面談頁靠這個欄位畫「我要應徵這個（最多 2 個）」的卡片。任何失敗都不往外丟。"""
    name = (ctx.get('application') or {}).get('name') or ''
    try:
        AJR.ensure_tables()
        saved = AJR.fetch_saved(app_id)
        if saved:
            return [d for d, _ in saved]
        AJR._set_run(app_id, 'pending', 'general')
        pool = load_pool()
        if not pool:
            AJR._set_run(app_id, 'none', 'general', '沒有開放中的無經驗職缺')
            return []
        guard = AJR.ClientGuard.from_d1()
        brief = pool_brief(pool, guard)
        out = llm(build_rank_prompt(ctx, brief))
        recs, dropped = validate_picks(out, {j['slug']: j for j in pool}, guard, AJR._said_text(ctx))
        detail = json.dumps({'pool': len(pool), 'dropped': dropped, 'prompt_version': PROMPT_VERSION}, ensure_ascii=False)
        if not recs:
            AJR._set_run(app_id, 'none', 'general', detail)
            log(f'{name}：沒有夠接近的無經驗職缺（{len(pool)} 個候選，丟掉 {len(dropped)}）')
            return []
        AJR.save(app_id, recs)
        AJR._set_run(app_id, 'ready', 'general', detail)
        log(f"{name}：通用面談挑了 {len(recs)} 個：{'、'.join(r['title'] for r in recs)}")
        return recs
    except Exception as e:
        log(f'{name}：挑職缺失敗（不影響面談與報告）：{e}')
        try:
            AJR._set_run(app_id, 'error', 'general', str(e)[:300])
        except Exception:
            pass
        return []


# ── 報告（顧問看）───────────────────────────────────────────────
def report_md(app_id, recs):
    wanted = {d.get('slug'): w for d, w in AJR.fetch_saved(app_id) if w}
    lines = ['', '## 無經驗通用面談：阿財挑的職缺與人選的選擇',
             '（這場是「無經驗通用面談（先聊再選職缺）」，不是針對單一職缺。面談結尾畫面上給他看下面這幾個，'
             f'他最多可選 {MAX_PICKS} 個「我要應徵這個」——選了會自動建立該職缺的應徵，逐字稿與這份報告一起帶過去。）']
    if not recs:
        lines.append('- 這場沒有挑出夠接近的無經驗職缺（畫面上沒有職缺卡片），請顧問跟他聯絡再看。')
        return '\n'.join(lines)
    for i, r in enumerate(recs, 1):
        mark = '　✅ 人選選了「我要應徵這個」' if r['slug'] in wanted else ''
        lines.append(f"{i}. {r['title']}（{r['slug']}）適配：{r.get('fit') or '—'}{mark}")
        lines.append(f"   為什麼：{r.get('why')}｜還要確認：{r.get('confirm')}｜人選原話：「{r.get('evidence') or ''}」")
    picked = [r['title'] for r in recs if r['slug'] in wanted]
    lines.append('')
    lines.append(f"人選的選擇（產報告當下）：{'、'.join(picked) if picked else '還沒選（之後選了會在下面補一行）'}")
    return '\n'.join(lines)


def ensure_links_table():
    d1_http.query(f'CREATE TABLE IF NOT EXISTS {LINKS_TABLE} ('
                  ' source_app_id TEXT NOT NULL, job_slug TEXT NOT NULL, target_app_id TEXT NOT NULL,'
                  ' created_at TEXT NOT NULL, report_copied_at TEXT, PRIMARY KEY (source_app_id, job_slug))')


REPORT_HEAD = ('【無經驗通用面談轉入】這份報告來自「無經驗通用面談（先聊再選職缺）」，'
               '人選在面談結尾自己選了應徵這個職缺。原始應徵：{src}\n\n')


def sync_linked_reports(app_id):
    """報告存好之後呼叫：人選在報告產生前就選了職缺的話，那些新應徵還沒有報告——這裡補複製過去。
    （報告產生後才選的，public-worker 在選的當下就直接複製。）"""
    try:
        ensure_links_table()
        rows = d1_http.query(
            f"SELECT l.target_app_id FROM {LINKS_TABLE} l WHERE l.source_app_id={_q(app_id)} "
            f"AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.application_id=l.target_app_id)")['results'] or []
        for r in rows:
            tgt = r['target_app_id']
            head = REPORT_HEAD.format(src=app_id)
            d1_http.query(
                'INSERT INTO reports (id, application_id, created_at, content_md, content_json, route_code, route_label) '
                f"SELECT {_q('r_gm_' + tgt[:8] + '_' + str(int(time.time())))}, {_q(tgt)}, {_q(_now())}, "
                f"{_q(head)} || content_md, content_json, route_code, route_label FROM reports "
                f"WHERE application_id={_q(app_id)} ORDER BY created_at DESC LIMIT 1")
            d1_http.query(f"UPDATE {LINKS_TABLE} SET report_copied_at={_q(_now())} "
                          f"WHERE source_app_id={_q(app_id)} AND target_app_id={_q(tgt)}")
            log(f'報告已複製到選的職缺應徵 {tgt}')
    except Exception as e:
        log(f'複製報告到選的職缺失敗（{app_id}）：{e}')
