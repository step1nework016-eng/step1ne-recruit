#!/usr/bin/env python3
"""AI 主動開發人選的兩道「寫進人才池之前」的關卡（2026-10-08 Jacky 核准）。

給 talent_sourcing_agent.py（白天找人／挖角）、nightly_pipeline/d1q.py（夜間找人）、
regrade_sourced.py（職缺改版後重評）共用，三邊用同一套規則，不各寫一份。

關卡一：個人頁
    10/8 實測：status=new、A/B 級、開放職缺的 366 位裡，約 170 位只有新聞／公告／公司官網／
    企業名錄連結（finance.vietstock.vn、eurocham 會員頁、zoominfo…），點進去看不到本人資料，
    顧問沒辦法判斷、也沒辦法聯絡。→ 至少要有一個「看得到本人經歷」的個人頁才收成人選。

關卡二：評等
    舊做法是技能包 full-prompt-v3.4.md 第六步的加權總分（技能30／產業20／職務20／年資10／
    地點10／證據完整度10），60 分就是 B。地點、年資、證據完整度這些「軟分數」就能湊到 30 分，
    再加一點相近職務就過 60 → 大量 B。必要條件沒對上也能是 B。
    新做法：AI 逐條對照職缺必要條件（met／unmet／unknown＋證據），由程式決定等第上限：
      - 任何一條必要條件 unmet → 最高 C
      - 職能（實際做的事）對不上 → 最高 C
      - 職缺要求產業、產業對不上 → 最高 C
      - 「看經歷就能判斷」的必要條件（required_conditions）只要有一條 unknown → 最高 C
        （「要電話問」的條件，例如接受外派／輪班／到班／語言程度，可以 unknown）
      - 必備技能（must_skills）有經歷證據的不到一半、而且必要條件也沒全部對上 → 最高 C；
        其餘沒寫在公開資料上的技能 → 最高 B（電話確認）
      - 職缺沒填必要條件 → 用「實際做過這個職缺的核心工作（職稱＋主要工作）」當唯一一條
      - A 要全部對上；而且照 10/6 的規則，沒人核對過完整履歷（verify_status!='verified'）最高 B
    分數夾在等第區間內（A 80+、B 60–79、C 40–59、D 39 以下），理由寫成「對上哪條／缺哪條」。

⚠️ 這裡不碰年齡、性別等保護項目：條件清單只取職缺的工作條件欄位，不讀 client_screen_conditions。
"""
import hashlib
import json
import re
from urllib.parse import urlparse, parse_qs

# ── 關卡一：個人頁 ───────────────────────────────────────────────

# 每一類為什麼算：都是「本人自己建立、內容是本人經歷／作品」的頁面，打開就能判斷這個人做過什麼。
PROFILE_KINDS = {
    'linkedin': 'LinkedIn 個人頁（/in/）',
    'cake': 'Cake 公開履歷／作品集',
    'github': 'GitHub 個人頁',
    '104': '104 個人檔案（本人公開的履歷頁）',
    'behance': 'Behance 個人作品集',
    'dribbble': 'Dribbble 個人作品集',
    'orcid': 'ORCID 研究者頁',
    'scholar': 'Google Scholar 個人學術頁',
    'wantedly': 'Wantedly 個人檔案（/id/）',
}
# 滑卡頁（backoffice worker /admin/pick/deck）目前只認這三類；其他類別存得進去，但要改那一行才會出現在滑卡頁
DECK_KINDS = {'linkedin', 'cake', 'github'}

_CAKE_RESERVED = {'companies', 'company', 'jobs', 'job-search', 'campaigns', 'events', 'blog', 'resources',
                  'explore', 'talent-search', 'about', 'pricing', 'login', 'signup', 'users', 'search',
                  'job', 'salary', 'templates', 'resume-templates', 'portfolio-templates', 'courses', 'zh-TW', 'en'}
_GH_RESERVED = {'orgs', 'topics', 'features', 'about', 'enterprise', 'marketplace', 'sponsors', 'collections',
                'trending', 'search', 'login', 'join', 'pricing', 'settings', 'explore', 'apps', 'site',
                'customer-stories', 'readme', 'events', 'team', 'security', 'contact', 'pulls', 'issues'}


def profile_kind(url):
    """一個網址是不是「看得到本人資料的個人頁」。是就回傳類別代碼，不是回傳 None。"""
    u = str(url or '').strip()
    if not u:
        return None
    if not re.match(r'^https?://', u, re.I):
        m = re.search(r'https?://\S+', u)          # other_links 常是「作品集: https://…」
        if not m:
            return None
        u = m.group(0)
    try:
        p = urlparse(u.split('#')[0])
    except ValueError:
        return None
    host = (p.hostname or '').lower()
    segs = [s for s in p.path.split('/') if s]
    if host.endswith('linkedin.com'):
        return 'linkedin' if len(segs) >= 2 and segs[0].lower() == 'in' else None
    if host.endswith('cake.me') or host.endswith('cakeresume.com'):
        if not segs:
            return None
        if segs[0].lower() in ('me', 'resumes', 'portfolios', 's') and len(segs) >= 2:
            return 'cake'
        if len(segs) == 1 and segs[0] not in _CAKE_RESERVED and segs[0].lower() not in _CAKE_RESERVED:
            return 'cake'
        return None
    if host in ('github.com', 'www.github.com'):
        return 'github' if len(segs) == 1 and segs[0].lower() not in _GH_RESERVED else None
    if host.endswith('.github.io'):
        return 'github'
    if host == 'pda.104.com.tw':
        return '104' if len(segs) >= 2 and segs[0] == 'profile' and segs[1] not in ('intro',) else None
    if host == 'plus.104.com.tw':
        return '104' if len(segs) >= 2 and segs[0] == 'profile' else None
    if host.endswith('behance.net'):
        return 'behance' if len(segs) == 1 and segs[0] not in ('search', 'galleries', 'joblist', 'assets') else None
    if host.endswith('dribbble.com'):
        return 'dribbble' if len(segs) == 1 and segs[0] not in ('shots', 'jobs', 'designers', 'search', 'tags') else None
    if host.endswith('orcid.org'):
        return 'orcid' if segs and re.fullmatch(r'\d{4}-\d{4}-\d{4}-\d{3}[\dX]', segs[0]) else None
    if host.endswith('wantedly.com'):
        return 'wantedly' if len(segs) >= 2 and segs[0] == 'id' else None
    if host.startswith('scholar.google.'):
        return 'scholar' if segs[:1] == ['citations'] and parse_qs(p.query).get('user') else None
    return None


def _all_urls(c):
    out = []
    for k in ('linkedin_url', 'github_url', 'source_url', 'profile_url'):
        if c.get(k):
            out.append(str(c[k]))
    other = c.get('other_links')
    if isinstance(other, list):
        out += [str(x) for x in other]
    elif other:
        out += re.findall(r'https?://\S+', str(other))
    return out


def personal_profiles(c):
    """回傳 [(類別, 網址)]：這位人選身上所有算數的個人頁。"""
    seen, out = set(), []
    for u in _all_urls(c):
        k = profile_kind(u)
        key = u.split('#')[0].rstrip('/').lower()
        if k and key not in seen:
            seen.add(key)
            out.append((k, u.split('#')[0]))
    return out


def place_profile_links(c):
    """把找到的個人頁放到滑卡頁讀得到的欄位：LinkedIn → linkedin_url、GitHub → github_url；
    Cake／104 等若 source_url 不是個人頁，就把個人頁補進 other_links（source_url 保留原始發現來源）。
    回傳 personal_profiles 結果。"""
    profs = personal_profiles(c)
    for k, u in profs:
        if k == 'linkedin' and profile_kind(c.get('linkedin_url')) != 'linkedin':
            c['linkedin_url'] = u
        if k == 'github' and not c.get('github_url'):
            c['github_url'] = u
    if profs and not profile_kind(c.get('source_url')) and not profile_kind(c.get('linkedin_url')):
        first = profs[0][1]
        # 滑卡頁只看 linkedin_url／source_url／github_url：Cake 這類沒有專屬欄位的，
        # 讓 source_url 指向個人頁，原本的發現來源移到 other_links，兩個都不丟
        orig = c.get('source_url')
        c['source_url'] = first
        extra = str(c.get('other_links') or '')
        if orig and orig.split('#')[0] not in extra:
            extra = (extra + '\n' if extra else '') + f'發現來源：{orig}'
        c['other_links'] = extra or None
    return profs


# ── 關卡二：評等 ─────────────────────────────────────────────────

PHONE_ONLY = re.compile(r'接受|配合|願意|外派|派駐|駐點|輪班|夜班|大夜|加班|出差|到班|通勤|住宿|簽證|在留|良民|刑事紀錄|'
                        r'保密|NDA|到職|報到|長期海外|常駐|搬遷|移居|假日|排班|健康檢查|體檢|'
                        r'宿舍|提供.{0,6}(宿舍|住宿|交通|津貼)|現居|居住|現為|文書能力|駕照|翻譯審核')   # 10/10 E22：宿舍是福利不是人選條件；現居地、文件翻譯審核公開資料看不到
# 2026-10-10 Jacky「找人選都沒在找」：其實有找到（例：台玻／友達會計部經理十幾年），卻因為下面這類
# 「LinkedIn 本來就不會寫、一通電話就問得到」的條件找不到證據而被壓成 C、進不了滑卡。
# 歸成電話確認：學歷科系、證照資格、特定軟體／系統品牌、「熟悉…法規／系統操作」這種抽象描述、人格特質。
# ⚠️ 職能、產業、年資、職級這些「看經歷就該判斷得出來」的仍然要證據，不放寬。
PHONE_SOFT = re.compile(r'學歷|大學|碩士|博士|專科|高中|科系|相關科系|畢業|'
                        r'證照|證書|執照|資格|高考|會計師|CPA|CMA|PMP|'
                        r'鼎新|T100|TIPTOP|Workflow|SAP|Oracle|NetSuite|正航|文中|凌越|ERP\s*系統|系統操作|'
                        r'熟悉.{0,12}(法規|法令|規定|系統|操作|流程)|'
                        r'責任心|細心|耐心|抗壓|積極|主動|溝通|親和|穩定度|配合度|學習')
COND_FIELDS = ('required_conditions', 'must_skills', 'language_requirement')
JOB_FP_FIELDS = ('title', 'main_duties', 'required_conditions', 'must_skills', 'nice_to_have_skills',
                 'preferred_background', 'language_requirement', 'locations', 'seniority', 'years_min', 'education_level')


def _split(text):
    parts = re.split(r'[；;。\n]+|(?<=\S)\s*[0-9０-９]+[\.、．)]\s*', str(text or ''))
    return [p.strip(' \t-・•*，,') for p in parts if p and len(p.strip(' \t-・•*，,')) >= 2]


def job_conditions(job):
    """職缺的必要條件清單：[{'no':1,'text':…,'kind':'evidence'|'phone'}]。
    來源：required_conditions、must_skills、language_requirement，以及 preferred_background 裡標「必要」的那句。
    kind=phone 的是只能電話問的（接受外派、輪班…），其他都是看經歷就該判斷得出來的。"""
    items = []   # (文字, 來源)：req＝必要條件、skill＝必備技能、lang＝語言
    src_of = {'required_conditions': 'req', 'must_skills': 'skill', 'language_requirement': 'lang'}
    for f in COND_FIELDS:
        items += [(t, src_of[f]) for t in _split(job.get(f))]
    for p in _split(job.get('preferred_background')):
        if '必要' in p or '必備' in p:
            items.append((re.sub(r'[（(]?必要[）)]?|[（(]?必備[）)]?', '', p).strip() or p, 'req'))
    if not items and (job.get('title') or job.get('main_duties')):
        # 職缺沒填必要條件（例如 bim-engineer-houli）：至少要有「實際做過這份工作的核心任務」的證據
        items.append((f"實際做過這個職缺的核心工作：{job.get('title') or ''}｜{str(job.get('main_duties') or '')[:120]}", 'core'))
    seen, out = set(), []
    for t, src in items:
        is_lang = src == 'lang'
        key = re.sub(r'\s+', '', t)
        if key in seen or re.search(r'非必要|不限|不拘|加分|尤佳|為佳|優先|不需要|不要求', t) or re.fullmatch(r'[無皆可/／\s]*', t):
            continue   # 加分項不是必要條件
        seen.add(key)
        # 語言程度公開資料多半看不出來、顧問電話一講就知道 → 歸「電話確認」，不因為 unknown 就降到 C
        kind = 'phone' if (is_lang or (src != 'core' and (PHONE_ONLY.search(t) or PHONE_SOFT.search(t)))) else 'evidence'
        out.append({'no': len(out) + 1, 'text': t[:200], 'kind': kind, 'src': src})
    return out


def job_fingerprint(job):
    """職缺條件的指紋：這幾個欄位任何一個改了，指紋就變 → 用舊指紋評的人要重評。"""
    raw = json.dumps({k: str(job.get(k) or '').strip() for k in JOB_FP_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]


def conditions_prompt(conds):
    if not conds:
        return '（這個職缺沒有填必要條件欄位，請只依職稱與主要工作內容判斷職能是否對上，條件逐條對照可留空陣列）'
    return '\n'.join(f"{c['no']}. {c['text']}" + ('（只能電話確認，公開資料看不到可以填 unknown）' if c['kind'] == 'phone' else '')
                     for c in conds)


GRADE_RULES = """【評等規則｜2026-10-08 起，取代舊的加權總分；程式會依你的逐條對照重算等第上限】
- 只能拿「工作經歷／專案經歷裡實際做過的事」當證據；技能清單、自評、證照、公司產業別都不算證據。
- 每一條的 status：
  met＝經歷裡有對得上的事實。經歷本身的情境就能證明的也算（例：在成衣廠當財會主管＝具製造業廠務經驗；
       2008 年起當會計主管到現在＝10 年以上財會經驗；在營造廠做 Revit 建模＝BIM 建模經驗）。
       **職稱＋公司＋任職期間本身就是證據**（2026-10-10 加）：LinkedIn 常擋住工作內容，只看得到職稱和年份——
       「2009 起任友達光電會計部經理」就足以證明：15 年以上年資、會計主管職、上市公司會計作業（財報、結帳、帳務）；
       「上市公司會計處經理暨代理發言人」就足以證明上市櫃申報與公告經驗。**不要因為沒有逐條工作內容描述就判 unknown**，
       該職稱「本來就一定會做的核心工作」算 met；只有該職稱不一定會做的（例如成本會計之於一般會計、特定 ERP 品牌）才算 unknown。
  unmet＝有證據顯示「不符合」（例：年資明確不到、做的是別的工作、明確寫不接受外派）。
  unknown＝公開資料沒寫到。**沒寫到不是 unmet**。
- 用「職缺必要條件」逐條比對，不是用職稱比對：職稱相近但必要條件拿不出證據的，最高 C。
- 不能用「他待的公司是做這行的」推論他本人做過（Company Fit ≠ Candidate Fit）；同事、同公司其他人也要各自逐條對照。
- A＝每一條必要條件都有具體證據對上、職能與產業都對。
- B＝看經歷就能判斷的必要條件全部對上（必備技能至少一半有證據，或必要條件全對上時技能可留給電話確認）、職能與產業都對；
  其餘只剩「只能電話問」的（接受外派、輪班、到班、語言程度…）或沒寫在公開資料的技能待確認。
- C＝有任何一條條件明確沒對上、必要條件查不到證據、必備技能有證據的不到一半，或職能／產業對不上（例如相鄰職務、只有同產業但做的是別的事）。
- D＝明顯不適合（做的是完全不同的工作，或層級差兩級以上）。只是資料不足、看不到證據的給 C，不要給 D。
- function_match 只看「他做的是不是同一種工作」，跟證據多寡無關：例如 Finance Manager／會計長 對 財務主管＝同一種（true）；
  都市計畫規劃師 對 BIM 工程師、業務開發 對 客戶成功經理＝不同種（false）。
  職缺條件明文接受的相關經驗算同一種（例如條件寫「助理／秘書／口筆譯相關經驗」，口譯員就算 true）。
- industry_required 只有在必要條件明寫要某產業時才是 true；「加分／尤佳」的產業背景不算。
- 年齡、性別、婚育、國籍、外貌等一律不得當理由。"""

GRADE_JSON_SPEC = ('"must_check": [{"no": 條件編號, "status": "met 或 unmet 或 unknown", "evidence": "對上的經歷原文（公司＋職稱＋做的事），沒有就寫缺什麼"}], '
                   '"function_match": true 或 false（他做的工作跟這個職缺是不是同一種職務；只看職務類別，不看證據多寡）, '
                   '"industry_required": true 或 false（職缺條件有沒有要求特定產業）, '
                   '"industry_match": true 或 false, '
                   '"grade": "A 或 B 或 C 或 D", '
                   '"grade_reason": "對上哪幾條、缺哪幾條（60字內）"')

BANDS = {'A': (80, 100), 'B': (60, 79), 'C': (40, 59), 'D': (0, 39)}
ORDER = 'ABCD'


def _worse(a, b):
    return a if ORDER.index(a) >= ORDER.index(b) else b


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v or '').strip().lower() in ('true', 'yes', '1', 'y', '是', '對')


def enforce_grade(c, conds, verified=False):
    """依 AI 的逐條對照算出等第上限，回傳 (grade, score, reason, detail)。
    c 需要有 must_check / function_match / industry_required / industry_match / grade / fit_score|score。
    沒有附逐條對照（舊流程的輸出）→ 最高 C：沒有證據就不能是 A/B。"""
    model_g = str(c.get('grade') or '').strip().upper()[:1]
    try:
        score = int(float(c.get('fit_score') if c.get('fit_score') is not None else c.get('score') or 0))
    except (TypeError, ValueError):
        score = 0
    if model_g not in ORDER:
        model_g = 'A' if score >= 80 else 'B' if score >= 60 else 'C' if score >= 40 else 'D'
    checks = c.get('must_check')
    cap, why = 'A', []
    status = {}
    if isinstance(checks, list):
        for x in checks:
            try:
                status[int(x.get('no'))] = (str(x.get('status') or '').lower(), str(x.get('evidence') or ''))
            except (TypeError, ValueError, AttributeError):
                continue
    met, unmet, unk_ev, unk_ph, unk_skill = [], [], [], [], []
    n_skill = met_skill = 0
    for cd in conds:
        st = status.get(cd['no'], ('unknown', ''))[0]
        short = cd['text'][:18]
        is_skill = cd.get('src') == 'skill' and cd['kind'] == 'evidence'
        n_skill += is_skill
        if st == 'met':
            met.append(short)
            met_skill += is_skill
        elif st == 'unmet':
            unmet.append(short)
        elif cd['kind'] == 'phone':
            unk_ph.append(short)
        elif is_skill:
            unk_skill.append(short)
        else:
            unk_ev.append(short)
    if not isinstance(checks, list):
        cap = 'C'; why.append('沒有逐條對照必要條件')
    if unmet:
        cap = _worse(cap, 'C'); why.append('沒對上：' + '、'.join(unmet))
    if unk_ev:
        cap = _worse(cap, 'C'); why.append('查不到證據：' + '、'.join(unk_ev))
    # 必備技能（must_skills）：A 要全部對上。B：至少一半有經歷證據；或「必要條件（required_conditions）
    # 全部有證據對上」時，技能沒寫在公開資料上的可以留給電話問（例：CPA＋22 年製造業財務主管，履歷沒寫 ERP）
    n_req_ev = sum(1 for cd in conds if cd.get('src') in ('req', 'core') and cd['kind'] == 'evidence')
    reqs_all_met = n_req_ev > 0 and not unk_ev and not unmet
    if n_skill and met_skill * 2 < n_skill and not reqs_all_met:
        cap = _worse(cap, 'C'); why.append(f'必備技能只對上 {met_skill}/{n_skill}')
    elif unk_skill:
        cap = _worse(cap, 'B'); unk_ph = unk_ph + unk_skill
    if 'function_match' in c and not _truthy(c.get('function_match')):
        cap = _worse(cap, 'C'); why.append('實際職能對不上')
    if _truthy(c.get('industry_required')) and not _truthy(c.get('industry_match')):
        cap = _worse(cap, 'C'); why.append('產業對不上')
    if unk_ph and cap == 'A':
        cap = 'B'
    if not verified and cap == 'A':
        cap = 'B'          # 10/6：還沒人看過完整履歷，最高 B
    g = _worse(model_g, cap)
    lo, hi = BANDS[g]
    score = max(lo, min(hi, score))
    parts = []
    if met:
        parts.append('對上：' + '、'.join(met))
    parts += why
    if unk_ph:
        parts.append('電話確認：' + '、'.join(unk_ph))
    reason = '；'.join(parts)
    if c.get('grade_reason'):
        reason = f"{str(c['grade_reason'])[:120]}｜{reason}" if reason else str(c['grade_reason'])[:120]
    return g, score, reason, {'cap': cap, 'model_grade': model_g, 'met': met, 'unmet': unmet,
                              'unknown_evidence': unk_ev, 'unknown_phone': unk_ph, 'why': why}


def grade_note(c, grade, det, tag=''):
    """寫進 sourced_candidates.note 的那一行。格式配合滑卡頁（backoffice pickAiSourced）：
    「符合原因：…」當卡片的「為什麼」、「但…需電話確認」當「先確認」；後面接評等依據。"""
    why = str(c.get('grade_reason') or '').strip() or ('對上：' + '、'.join(det['met']) if det['met'] else '必要條件沒有對上的證據')
    s = f'符合原因：{why[:120]}'
    if det['unknown_phone']:
        s += f"，但{'、'.join(det['unknown_phone'])}需電話確認"
    basis = '；'.join(det['why']) or ('必要條件都對上' if det['met'] else '')
    s += f"｜{grade} 級依據：{basis or '—'}"
    if tag:
        s += f'｜{tag}'
    return s[:1000]
