#!/usr/bin/env python3
"""面談結束後的交付：把 reports.content_json 套進樣板，產出顧問版／客戶版 PDF。

為什麼獨立成一支檔案而不是寫在 interview_daemon.py 裡：
1. export_pdf.py 會 `import interview_daemon`，如果交付邏輯也放在 daemon 裡，
   任何 import 迴圈都會炸到面談本身。這支**不 import 任何專案內模組**，
   誰都可以安全地拿去用。
2. 這支只做「資料 → HTML → PDF」的純轉換，**不碰 D1、不推 Telegram**。
   查資料與推播留在 daemon（它本來就有 d1()／tg()／tg_doc()）。
   純函式才驗證得起來——不然每測一次就得碰線上資料庫。

⚠️ 客戶版的過濾規則是法遵與客戶隱私問題，不是排版偏好。
   完整規格見 ~/.claude/projects/-Users-user-----/memory/report_two_versions_spec.md。
   絕對不能出現在客戶版的：候選人現有／接案收入、手上其他機會、
   我們對他的懷疑、人格測驗分數、內部追問清單、內部評分。

用法（測試用，不推播）：
    python3 deliver.py <content_json 檔> <meta json 檔> <輸出目錄>
"""
import html as _html
import json
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, 'reporttpl')

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


# ─────────────────────────────────────────────────────────────
# 極小樣板引擎
#
# 不用 jinja2 之類的第三方套件：這台機器上的 daemon 是常駐的，多一個相依
# 就多一個「哪天 pip 環境壞掉，面談交付就停擺」的風險。需要的功能只有兩個。
def _render(tpl, variables, flags):
    """{{VAR}} 代換；<!--@IF:key@-->…<!--@ENDIF:key@--> 在 flags[key] 為假時整段刪掉。

    先處理 IF 再代換變數——反過來的話，被刪掉那段裡的 {{VAR}} 會先被填進去，
    白做一場；而且若變數內容剛好含 <!--@ENDIF...@--> 會把結構弄壞。
    """
    def cut(m):
        return m.group(2) if flags.get(m.group(1)) else ''

    tpl = re.sub(r'<!--@IF:(\w+)@-->(.*?)<!--@ENDIF:\1@-->', cut, tpl, flags=re.S)
    for k, v in variables.items():
        tpl = tpl.replace('{{%s}}' % k, v)
    # 沒被填到的變數一律清成空字串，免得 PDF 上印出 {{XXX}}
    return re.sub(r'\{\{[A-Z_]+\}\}', '', tpl)


def e(v):
    """HTML 逸出。所有進到樣板的資料都要過這一關——
    候選人的自述是外部輸入，裡面出現 < 或 & 會把版面弄壞。"""
    return _html.escape(str(v or ''), quote=True)


# ─────────────────────────────────────────────────────────────
# 客戶版的內容過濾
#
# 兩道防線，因為 content_json 是模型產的，欄位語意不保證乾淨：
#   第一道：整個欄位不搬（例如 consultant_followups、observations.assessment 的分數）。
#   第二道：搬過去的字串再逐句掃一次——實際踩過的坑是
#          hard_conditions 的 detail 裡混進了「目前接案收入 75–85K」，
#          那個欄位本身是客戶要看的，不能整欄丟掉，只能把那一句挑掉。
CLIENT_BANNED_RE = re.compile(
    r'('
    r'(接案|目前|現有|現在|現職|原本|每月|月|年|實際)?收入'      # 現有／接案收入
    r'|現領|現薪|目前薪(資|水)|原薪'
    r'|其他(在談的)?(機會|offer|Offer|OFFER)'                    # 手上其他機會
    r'|手上(還有|有)(其他|別的)'
    r'|同時(在談|面試)'
    r'|(DISC|disc)'                                              # 人格測驗分數
    r'|人格(測驗|量表)'
    r'|測驗分數'
    r'|沒有正面回(答|應)|未正面回(答|應)|問(了)?兩次'            # 我們對他的懷疑
    r'|避而不(答|談)|閃避|說詞反覆|避重就輕'
    # 「說法籠統」這一類是我們對他的評價，不是可查證的事實。
    # 實測踩過：for_client.risks_to_disclose 寫「有 3 年空窗，候選人說法籠統」——
    # 空窗是事實要揭露，「說法籠統」是我們的懷疑不能給客戶。
    # 逗號層的再切會把前半留下、後半丟掉，剛好是要的結果。
    r'|籠統|含糊|交代不清|說不清楚|存疑|可信度|真實性'
    r'|建議(客戶)?(面試時)?(可以)?追問|追問清單'                 # 內部追問清單
    r'|內部評分|BARS'
    r')')

# 逐句切開用的標點。中英文都要，候選人的自述兩種都會出現。
_CLAUSE_SPLIT = re.compile(r'(?<=[。；;\n])')


def scrub_for_client(text):
    """把一段文字裡「不可以給客戶看」的句子挑掉，其餘保留。

    為什麼是挑句子不是挑詞：把詞遮成 ●●● 反而更醒目，客戶會問那是什麼；
    整欄丟掉又會讓客戶版少掉他該知道的條件說明。挑句子是唯一兩邊都成立的做法。
    """
    if not text:
        return ''
    keep = []
    for c in _CLAUSE_SPLIT.split(str(text)):
        if not c.strip():
            continue
        if not CLIENT_BANNED_RE.search(c):
            keep.append(c)
            continue
        # 這一句有禁字，但整句丟掉常常會連客戶該知道的條件一起丟掉
        # （實例：「候選人期望60K，目前接案收入75–85K，60K為全職最低期望」——
        #  中間那段不能給，前後兩段是客戶談薪的必要資訊）。
        # 所以再用逗號切一層，只丟掉真的有問題的那一小段。
        subs = [s for s in re.split(r'(?<=[，、])', c)
                if s.strip() and not CLIENT_BANNED_RE.search(s)]
        if subs:
            keep.append(''.join(subs).rstrip('，、'))
    out = ''.join(keep).strip()
    # 只剩標點就當作沒東西，不要留一個孤零零的「；」在報告上
    return '' if not re.search(r'[\w一-鿿]', out) else out


def clean_url(url):
    """把追蹤參數清掉。規格要求「網址直接印在報告上，追蹤參數要清掉」——
    fbclid 那種東西印在給客戶的 PDF 上又長又不專業。"""
    if not url:
        return ''
    u = re.sub(r'[?&](fbclid|gclid|msclkid|utm_[a-z_]+)=[^&]*', '', str(url).strip())
    return u.rstrip('?&')


def anon_name(full_name):
    """匿名版的稱呼：姓＋先生／小姐。

    ⚠️ 只有 jobs.client_named = 0（尚未簽約）時才用。已簽約就寫全名，
    不要自作聰明縮成「王先生」——規格明文寫的。
    性別判斷不出來就寫「先生／小姐」，猜錯比不猜更失禮。
    """
    n = (full_name or '').strip()
    if not n:
        return '候選人'
    # 複姓先處理，其餘中文名取第一個字；英文名取最後一段（姓）
    for sur in ('歐陽', '司馬', '諸葛', '上官', '皇甫', '尉遲', '公孫', '夏侯', '端木'):
        if n.startswith(sur):
            return sur + '先生／小姐'
    if re.match(r'^[一-鿿]', n):
        return n[0] + '先生／小姐'
    return n.split()[-1] + '先生／小姐'


def _strip_name(text, full_name, replacement):
    """匿名時把內文裡的全名換掉。

    只做客戶版。名字常常會出現在 summary／one_liner 裡（模型會寫「王雁群現為…」），
    只換標題那一格等於沒匿名。
    """
    if not text or not full_name:
        return text or ''
    out = str(text).replace(full_name, replacement)
    # 「王雁群（Murr）」這種括號英文名也一起處理：中文全名去掉後剩下的英文暱稱不動，
    # 因為那不是可識別的法定姓名，而且客戶面試時本來就會知道怎麼稱呼他。
    return out


# ─────────────────────────────────────────────────────────────
# 光譜圖（客戶版的四軸）
#
# 規格：四軸、**不給數字**、下方註明「非標準化測驗結果」。
# 理由是自填問卷給客戶看分數＝假裝有科學根據，客戶還可能拿去當篩選依據。
#
# 資料來源優先序：applications 的 disc_d/i/s/c（表單原始分數）＞
# content_json 的 observations.assessment（模型從報告抄回來的）。
# 兩邊都沒有就整張圖不畫——寧可沒有，也不要畫一張四軸都在正中間的假圖。
SPECTRUM_AXES = [
    ('內斂寡言', '外向健談'),
    ('重執行細節', '重整體策略'),
    ('需要明確指示', '自主推進'),
    ('條件導向', '認同導向'),
]


def _disc_from(data, meta):
    d = (meta or {}).get('disc') or {}
    got = {k: d.get(k) for k in 'disc'}
    if any(isinstance(v, (int, float)) for v in got.values()):
        return {k: float(got.get(k) or 0) for k in 'disc'}
    # 退而求其次：從 assessment 的 trait 字串裡認 D／I／S／C
    out = {}
    for a in ((data.get('observations') or {}).get('assessment') or []):
        t, s = str(a.get('trait') or ''), a.get('score')
        if not isinstance(s, (int, float)):
            continue
        for letter in 'DISC':
            if t.upper().startswith(letter):
                out[letter.lower()] = float(s)
    return out if out else None


def _pos(a, b):
    """兩邊權重換成 0–100 的位置，夾在 10–90 之間。

    夾住是刻意的：貼到 0% 或 100% 會讓客戶讀成「極端人格」，
    但這本來就只是一份自填問卷的傾向，撐不起那種斷言。
    """
    if a + b <= 0:
        return 50
    return max(10, min(90, round(50 + 50 * (a - b) / (a + b))))


def spectrum(data, meta):
    """回傳 [(左標籤, 右標籤, 百分比), ...]；資料不足回 None。"""
    disc = _disc_from(data, meta)
    if not disc:
        return None
    d, i, s, c = (disc.get(k, 0) or 0 for k in 'disc')
    if d + i + s + c <= 0:
        return None

    # 前三軸從 DISC 推。每一軸用哪兩碼都寫在下面，不要憑印象改：
    #   內斂↔外向：I（表達）對 C（分析內斂）
    #   細節↔策略：C+S（流程與穩定）對 D+I（大方向與帶動）
    #   指示↔自主：S（順著走）對 D（自己推）
    rows = [
        _pos(i, c),
        _pos(d + i, c + s),
        _pos(d, s),
    ]

    # 第四軸 DISC 推不出來，改看他自己講的動機——
    # 講品牌／認同／有興趣的往「認同導向」，講薪資／條件／制度的往「條件導向」。
    mot = data.get('motivation') or {}
    txt = ' '.join([str(mot.get('why_this_role') or ''), ' '.join(data.get('values') or []),
                    str(data.get('values_basis') or '')])
    cond_txt = ' '.join([str(mot.get('salary_gap') or ''), str(mot.get('blockers') or '')])
    ident = len(re.findall(r'認同|品牌|喜歡|熱愛|興趣|理念|價值', txt))
    cond = len(re.findall(r'薪|待遇|條件|福利|制度|通勤|工時', cond_txt))
    rows.append(_pos(ident, cond) if (ident + cond) else 50)

    return [(SPECTRUM_AXES[n][0], SPECTRUM_AXES[n][1], p) for n, p in enumerate(rows)]


# ─────────────────────────────────────────────────────────────
# 共用小片段
_VERDICT_ICON = {'符合': '✅', '不符': '❌', '待確認': '⚠️'}


def _branding(meta, phrase):
    """依 jobs.client_relation 決定報告掛不掛 Step1ne 品牌。

    private（朋友認識・私人協助）→ 全部拿掉：標題、標頭、頁尾、制式聲明裡的公司名。
    其餘（signed／unsigned）→ 照原本掛品牌。
    """
    private = (meta or {}).get('client_relation') == 'private'
    if private:
        return {
            'DOC_TITLE': '人選推薦',
            'BRAND_LABEL': '人選推薦',
            'STAMP_TEXT': (f'本人選已完成<b>{phrase}</b>，上述內容為面談紀錄與應徵資料之摘要整理。'
                           '<br>本摘要不取代候選人本人之履歷與後續面試查證。'),
            'FOOTER': '',
        }
    return {
        'DOC_TITLE': '人選推薦｜Step1ne',
        'BRAND_LABEL': 'STEP1NE 人選推薦',
        'STAMP_TEXT': (f'本人選已完成 Step1ne 的<b>{phrase}</b>，上述內容為面談紀錄與應徵資料之摘要整理。'
                       '<br>本摘要不取代候選人本人之作品集與後續面試查證。'),
        'FOOTER': ('<p class="foot">德仁管理顧問有限公司（Step1ne）　統一編號 85046127<br>'
                   '就業服務許可證：北市就服字第 0363 號</p>'),
    }


def _nums(meta, current_state):
    """主視覺下面那排數字。沒填的照實寫「未提供」，**不准編**。"""
    items = [('期望待遇', meta.get('expected_salary')),
             ('可到職', meta.get('available_date')),
             ('工作地點', meta.get('location_ok')),
             ('目前狀態', current_state)]
    return ''.join(f'<div>{e(k)}<b>{e(v or "未提供")}</b></div>' for k, v in items)


def _current_state(data):
    """從最後一段工作經歷推「目前狀態」。推不出來就回 None（會顯示未提供）。"""
    wh = data.get('work_history') or []
    if not wh:
        return None
    last = wh[-1]
    if str(last.get('nature') or '') == '接案客戶':
        return '接案合作中'
    return '在職中' if last.get('employer') else None


def _track(work_history):
    """時間軸。**期間一定要寫**，沒有就照實標灰字「期間未提供」，不准編。"""
    out = []
    for w in work_history:
        dur = (w.get('duration') or '').strip()
        s = f'<s>{e(dur)}</s>' if dur else '<s class="unk">期間未提供</s>'
        dim = '' if dur else ' class="dim"'
        src = w.get('source') or '未提供'
        out.append(f'<div{dim}>{s}<b>{e(w.get("employer"))}</b>'
                   f'<i>{e(w.get("role") or "職稱未提供")}</i>'
                   f'<u>{e("來源：" + src)}</u></div>')
    return ''.join(out)


def _jobs(work_history, scrub=False):
    """各段實際做了什麼。**最近的排最上面**（規格明文），所以這裡要倒著跑。"""
    out = []
    for w in reversed(work_history):
        dur = (w.get('duration') or '').strip()
        s = (f'<s>{e(dur)}</s>' if dur else '<s class="unk">期間未提供</s>')
        note = w.get('note') or ''
        if scrub:
            note = scrub_for_client(note)
        role = w.get('role') or ''
        head = e(w.get('employer')) + (f' · {e(role)}' if role else '')
        body = f'<p>{e(note)}</p>' if note else ''
        out.append(f'<div><b>{head}</b>{s}{body}</div>')
    return ''.join(out)


def _fit(data):
    """六維度適配評分表（**只進顧問版**）。

    為什麼要有：verdict 只有四種值，同一個「值得轉給顧問」底下三個人，
    顧問要先打給誰只能自己重讀三份報告。這張表就是拿來排序的。

    ⚠️ 三個刻意的設計，改之前先想清楚：
      1. 沒有依據的維度顯示「未評估」而不是 0 分——把沒問到的題目當 0 分，
         會系統性地懲罰話少的場次，那個排序是假的。
      2. 每一維都印出證據原話。分數沒有原話撐著，顧問就沒辦法覆核，
         那它就只是一個看起來很客觀的數字。
      3. 標題與結尾都寫明「排序用，不是錄用建議」。這是初篩分數，
         不是給客戶看的評價，更不是淘汰依據。
    """
    fs = data.get('fit_scores') or {}
    dims = fs.get('dimensions') or []
    if not dims:
        return ''
    total, grade = fs.get('total'), fs.get('grade') or ''
    head = ('<div style="display:flex;align-items:baseline;gap:14px;margin:0 0 10px">'
            + (f'<span style="font-size:30px;font-weight:800;color:#0b3d5c">{total}</span>'
               f'<span style="font-size:15px;font-weight:700">等第 {e(grade)}</span>'
               if total is not None else
               f'<span style="font-size:16px;font-weight:700;color:#a32b21">{e(grade)}</span>')
            + f'<span style="font-size:12px;color:#5d6672">{e(fs.get("basis") or "")}</span></div>')
    rows = []
    for d in dims:
        sc = d.get('score')
        bar = ('<span style="color:#c4ccd4">未評估</span>' if sc is None else
               f'<b style="font-size:15px">{sc}</b><span style="color:#5d6672">/10</span>')
        ev = e(d.get('evidence') or '')
        rows.append(
            '<tr>'
            f'<td style="border-top:1px solid #e3e7ec;padding:6px 8px;vertical-align:top;white-space:nowrap">{e(d.get("name"))}'
            f'<span style="color:#8b95a1;font-size:11px"> ×{d.get("weight")}%</span></td>'
            f'<td style="border-top:1px solid #e3e7ec;padding:6px 8px;vertical-align:top;text-align:right;white-space:nowrap">{bar}</td>'
            f'<td style="border-top:1px solid #e3e7ec;padding:6px 8px;vertical-align:top;">{("「"+ev+"」") if ev else ""}'
            f'<div style="color:#5d6672;font-size:11.5px">{e(d.get("note") or "")}</div></td>'
            '</tr>')
    return (head + '<table style="width:100%;border-collapse:collapse;font-size:12.5px">'
            + ''.join(rows) + '</table>'
            + '<p style="font-size:11.5px;color:#5d6672;margin:8px 0 0">'
              '這是初篩排序用的分數，不是錄用建議，也不會出現在客戶版。'
              '分數旁邊沒有原話的維度代表面談中沒有依據，不要拿來當淘汰理由。</p>')


def _expertise(data, for_client=False):
    """專業問答的逐題結果。

    這一段是「用人單位不用自己再面談一次就能判斷」的關鍵——
    但關鍵不在我們給了什麼評語，而在**候選人自己講的原話**。
    主管看一句他親口說的「我那時候是把族群參數重設，因為圖層對不齊」，
    比看任何分數都清楚這個人有沒有真的做過。

    ⚠️ 兩版的差別只有一個：客戶版不搬 `note`（那是我們的觀察，屬於內部判斷），
       其餘照搬。`depth` 保留是因為它描述的是「他講得多具體」這個事實，
       不是我們對他專業程度的評價——而「哪一題他答不出來」正是主管要知道的。
    """
    rows = [f for f in (data.get('expertise_findings') or []) if f.get('topic') or f.get('asked')]
    if not rows:
        return ''
    DOT = {'具體': ('#14733f', '講得具體'), '籠統': ('#8a6100', '講得籠統'), '未談到': ('#8b95a1', '沒談到')}
    out = []
    for f in rows:
        color, label = DOT.get(f.get('depth') or '未談到', DOT['未談到'])
        out.append(
            '<div style="border-top:1px solid #e3e7ec;padding:10px 0">'
            f'<div style="font-size:13px;font-weight:700">{e(f.get("topic"))}'
            f'<span style="font-weight:600;font-size:11.5px;color:{color};margin-left:8px">{label}</span></div>'
            + (f'<div style="font-size:12.5px;color:#5d6672;margin-top:2px">問：{e(f.get("asked"))}</div>'
               if f.get('asked') else '')
            + (f'<div style="font-size:13px;margin-top:4px">{e(f.get("answered"))}</div>'
               if f.get('answered') else '')
            + (f'<div style="font-size:12.5px;margin-top:4px;padding-left:10px;'
               f'border-left:3px solid #e3e7ec;color:#333">「{e(f.get("evidence"))}」</div>'
               if f.get('evidence') else '')
            + (('' if for_client else
                (f'<div style="font-size:11.5px;color:#5d6672;margin-top:4px">{e(f.get("note"))}</div>'
                 if f.get('note') else '')))
            + '</div>')
    return ''.join(out)


def _cond(hard_conditions, with_evidence):
    """硬條件逐條。客戶版不帶 evidence_source——那是我們內部怎麼查證的紀錄，
    印給客戶看只會讓他去質疑每一條的可信度。"""
    out = []
    for h in hard_conditions:
        v = h.get('verdict') or '待確認'
        detail = h.get('detail') or ''
        if not with_evidence:
            detail = scrub_for_client(detail)
        ev = (f'<u>依據：{e(h.get("evidence_source"))}</u>'
              if with_evidence and h.get('evidence_source') else '')
        out.append(f'<div><span>{_VERDICT_ICON.get(v, "⚠️")}</span><span>'
                   f'<b>{e(h.get("item"))}</b><em>{e(detail)}</em>{ev}</span></div>')
    return ''.join(out)


# ─────────────────────────────────────────────────────────────

def _basics(data, meta, for_client=False):
    """基本資料列（居住地、年齡、學歷、語言、證照…）。

    ⚠️ 2026-08-11 Jacky 要求兩版都要有。原本沒有這一區，
    顧問拿到報告還要自己去翻履歷才知道人住哪、幾歲。

    ⚠️ 就業服務法第 5 條的分寸：**禁止的是拿它當僱用判斷依據，不是禁止記錄**。
    而且履歷本來就會跟報告一起寄給客戶（日式履歷書第一頁就印著生年月日），
    報告刻意不寫等於自欺欺人。所以規則是：
      · 只寫履歷／表單上他自己填的（來源那一行會印出來）
      · 不准從畢業年份推算
      · 只做事實揭露，不出現在任何判斷句裡
    客戶版**匿名時不印年齡**——匿名的用意就是不讓對方在見面前鎖定特定個人，
    年齡加上經歷組合起來辨識度很高。
    """
    b = (data.get('basics') or {}) if isinstance(data.get('basics'), dict) else {}
    anon = for_client and is_anonymous(meta)
    items = [('居住地', b.get('residence')),
             ('通勤評估', b.get('commute_note')),
             ('年齡', None if anon else b.get('age')),
             ('學歷', b.get('education')),
             ('語言', b.get('languages')),
             ('證照', b.get('certificates')),
             ('兵役', b.get('military'))]
    items = [(k, v) for k, v in items if str(v or '').strip() and str(v).lower() != 'none']
    if not items:
        return ''
    rows = ''.join(f'<div><i>{e(k)}</i><b>{e(v)}</b></div>' for k, v in items)
    src = b.get('source') or '履歷／應徵表單，非面談詢問'
    return (f'<div class="basics"><h3>基本資料</h3><div class="bgrid">{rows}</div>'
            f'<p class="bsrc">來源：{e(src)}</p></div>')


def build_client_html(data, meta):
    """客戶版（可轉給用人企業）。

    這個函式的每一個 return 值都會被印出去給第三方看，
    所以「哪些欄位有搬」比「排版好不好看」重要得多。有搬的只有：
      one_liner / for_client.reasons / work_history / hard_conditions /
      for_client.risks_to_disclose / candidate_questions / 光譜。
    **沒有搬的**（規格禁止）：summary（含我們的懷疑）、motivation（含收入與其他機會）、
    values、resume_vs_spoken、observations 的分數與 evidence、
    for_client.suggested_questions、consultant_followups。
    """
    anonymous = is_anonymous(meta)
    name = client_display_name(meta)

    def txt(v):
        """客戶版所有自由文字的共同出口：先過禁字，再過匿名。"""
        v = scrub_for_client(v)
        return _strip_name(v, meta.get('name'), name) if anonymous else v

    fc = data.get('for_client') or {}
    wh = data.get('work_history') or []

    reasons = ''.join(f'<li>{e(txt(r))}</li>' for r in (fc.get('reasons') or []) if txt(r))
    risks = [txt(r) for r in (fc.get('risks_to_disclose') or [])]
    risks = [r for r in risks if r]
    risks_html = ''
    if risks:
        lines = '<br>'.join(f'{"①②③④⑤⑥⑦⑧⑨"[n:n+1] or "・"} {e(r)}'
                            for n, r in enumerate(risks))
        risks_html = f'<div class="risk"><b>我們要先跟您說明的事</b><br>{lines}</div>'

    # 作品集連結：規格是「人選給連結 → 網址直接印在報告上（清掉追蹤參數）；
    # 人選給 PDF → 不放連結，註明『履歷另附』」。
    facts = []
    links = [clean_url(u) for u in (meta.get('portfolio_urls') or []) if clean_url(u)]
    resume = meta.get('resume') or {}
    if resume.get('kind') == 'url' and clean_url(resume.get('url')):
        links.insert(0, clean_url(resume.get('url')))
    if links:
        facts.append('<dt>作品集／履歷連結</dt><dd>' + '<br>'.join(
            f'<a href="{e(u)}" style="color:var(--gold);word-break:break-all">{e(u)}</a>'
            for u in links) + '</dd>')
    if resume.get('kind') == 'file':
        facts.append('<dt>履歷</dt><dd>履歷另附（與本報告一併提供）</dd>')
    if not any((w.get('duration') or '').strip() for w in wh):
        facts.append('<dt>任職期間</dt><dd><span style="font-size:12.5px;color:var(--ink3)">'
                     '候選人未提供各段起訖年月，上方順序依其口述整理，'
                     '<b>各段期間與總年資尚待面試確認</b>。</span></dd>')

    spec_rows = spectrum(data, meta)
    spec_html = ''.join(
        f'<div><s>{e(l)}</s><i style="--p:{p}%"></i><u>{e(r)}</u></div>'
        for l, r, p in (spec_rows or []))
    # ⚠️ observations.communication_style **不搬進客戶版**。
    # 那是模型自由書寫的觀察欄，實測內容是「被追問細節時傾向以保密協議帶過而非精確回答」——
    # 那就是規格明文禁止的「我們對他的懷疑」。這種欄位沒有任何正規表示式擋得乾淨，
    # 唯一安全的做法是整欄不搬。顧問版有，顧問看得到。
    spec_note = ''

    asked = ''.join(f'<div class="ask">{e(txt(c.get("question")))}</div>'
                    for c in (data.get('candidate_questions') or []) if txt(c.get('question')))

    # AI 揭露看職缺設定。never＝不主動說（不是否認），用中性但屬實的描述。
    phrase = ('結構化初步面談'
              if str(meta.get('ai_disclosure') or '').lower() == 'never'
              else 'AI 結構化初步面談')

    cond_html = _cond(data.get('hard_conditions') or [], with_evidence=False)
    tpl = open(os.path.join(TPL_DIR, 'client.html'), encoding='utf-8').read()
    return _render(tpl, {
        'NAME': e(name),
        'JOB': e(meta.get('job_title') or meta.get('job_slug') or ''),
        'POSITIONING': e(txt(data.get('one_liner'))),
        'NUMS': _nums(meta, _current_state(data)),
        'BASICS': _basics(data, meta, for_client=True),
        'REASONS': reasons,
        'EXPERTISE': _expertise(data, for_client=True),
        'TRACK': _track(wh),
        'JOBS': _jobs(wh, scrub=True),
        'FACTS': ''.join(facts),
        'COND': cond_html,
        'RISKS': risks_html,
        'ASKED': asked,
        'SPEC': spec_html,
        'SPEC_NOTE': spec_note,
        'INTERVIEW_PHRASE': e(phrase),
        # 客戶對象＝朋友私人協助時，報告不可以有任何 Step1ne 痕跡。
        # Jacky 2026-08-10：「不會有任何 step1ne logo、頁尾德仁管理顧問的標記，
        # 也不會寫 STEP1NE 人選推薦，只會寫人選推薦。」
        # 理由：靠朋友關係幫忙介紹，掛公司品牌等於把私人幫忙變成商業委託。
        **_branding(meta, phrase),
    }, {
        'reasons': bool(reasons),
        'expertise': bool(data.get('expertise_findings')),
        'history': bool(wh),
        'cond': bool(cond_html),
        'asked': bool(asked),
        'spectrum': bool(spec_rows),
    })


def build_consultant_html(data, meta):
    """顧問版（內部）。客戶版全部 ＋ 動機、價值觀、落差、人格分數、追問、系統紀錄。

    這一版**不做任何過濾**：顧問要看到模型原本寫了什麼，才知道這份能不能信。
    """
    wh = data.get('work_history') or []
    mot = data.get('motivation') or {}
    MOT_LABEL = [('why_leaving', '為什麼想動'), ('why_this_role', '為什麼是這裡'),
                 ('salary_gap', '薪資落差'), ('notice_period', '可到職'),
                 ('other_offers', '其他在談的機會'), ('blockers', '卡點')]
    # 沒問到的用紅字（.gap）標出來——顧問要一眼看出這場漏了什麼，
    # 那比看到一格空白重要得多。
    motivation = ''
    for k, lab in MOT_LABEL:
        v = mot.get(k)
        cls = '' if v else ' class="gap"'
        motivation += f'<dt>{e(lab)}</dt><dd{cls}>{e(v or "未問到／未提供")}</dd>'

    gaps = ''.join(
        f'<tr><td style="padding:4px 0;color:#a8392b;width:88px">{e(r.get("item"))}</td>'
        f'<td style="padding:4px 0">履歷：{e(r.get("resume_says") or "—")}　vs　'
        f'口述：{e(r.get("candidate_says") or "—")}'
        + (f'<br><span style="font-size:12.5px">{e(r.get("explanation"))}</span>'
           if r.get('explanation') else '')
        + f'　<b>{e(r.get("status"))}</b></td></tr>'
        for r in (data.get('resume_vs_spoken') or [])
        if (r.get('resume_says') or r.get('candidate_says') or r.get('explanation')))

    bars = ''
    for a in ((data.get('observations') or {}).get('assessment') or []):
        s = a.get('score')
        pct = max(0, min(100, int(round((s or 0) / 20 * 100))))
        cls = 'ok' if a.get('verified') == '面談印證' else 'non'
        mark = {'面談印證': '✅ 印證', '不一致': '⚠️ 不一致'}.get(a.get('verified'), '— 未觀察到')
        bars += (f'<div class="bar"><div class="bl"><b>{e(a.get("trait"))}</b>'
                 f'<span>{e("—" if s is None else s)}</span></div>'
                 f'<div class="bt"><i style="width:{pct}%"></i></div>'
                 f'<div class="chk {cls}">{mark}'
                 + (f'——{e(a.get("evidence"))}' if a.get('evidence') else '') + '</div></div>')
    if not bars:
        bars = '<p style="font-size:13.5px;color:var(--ink3);margin:0">沒有可用的量表資料。</p>'

    asked = ''.join(
        f'<div class="ask" style="background:var(--soft);border-left:3px solid var(--gold);'
        f'border-radius:0 10px 10px 0;padding:11px 14px;margin-bottom:9px;font-size:13.5px">'
        f'{e(c.get("question"))}'
        + (f'<br><span style="color:var(--ink3);font-size:12.5px">{e(c.get("note"))}</span>'
           if c.get('note') else '') + '</div>'
        for c in (data.get('candidate_questions') or []) if c.get('question'))
    if not asked:
        asked = ('<div class="miss" style="margin-top:0"><b>他一題都沒問。</b>'
                 '這件事本身是訊號——電訪時可以直接問他「對這個職缺有什麼想了解的嗎」。</div>')

    fc = data.get('for_client') or {}
    reasons = ''.join(f'<li>{e(r)}</li>' for r in (fc.get('reasons') or []))
    risks = (fc.get('risks_to_disclose') or [])
    risks_html = ('<div class="risk"><b>要主動跟客戶揭露</b><br>'
                  + '<br>'.join(f'・{e(r)}' for r in risks) + '</div>') if risks else ''

    # 內部追問清單：顧問版才有。規格明文「這整段不進客戶版」。
    fups = ''.join(f'<li>{e(x)}</li>' for x in (data.get('consultant_followups') or []))
    sug = fc.get('suggested_questions') or []
    if sug:
        fups += ''.join(f'<li>{e(x)}<span style="color:var(--ink3)">'
                        f'（原本標為「建議客戶面試追問」——規格要求送出前自己先補齊，'
                        f'不要列給客戶）</span></li>' for x in sug)

    resume = meta.get('resume') or {}
    files = []
    if resume.get('kind') == 'file':
        files.append(f'<a>履歷原檔已一併推送：{e(resume.get("filename") or "履歷")}</a>')
    elif resume.get('kind') == 'url' and clean_url(resume.get('url')):
        u = clean_url(resume.get('url'))
        files.append(f'<a href="{e(u)}">履歷連結：{e(u)}</a>')
    else:
        files.append('<a>沒有履歷檔，也沒有履歷連結</a>')
    for u in (meta.get('portfolio_urls') or []):
        if clean_url(u):
            files.append(f'<a href="{e(clean_url(u))}">作品集：{e(clean_url(u))}</a>')

    spec_rows = spectrum(data, meta)
    spec_html = ''.join(f'<div><s>{e(l)}</s><i style="--p:{p}%"></i><u>{e(r)}</u></div>'
                        for l, r, p in (spec_rows or []))

    sysrec = ''.join(f'<dt>{e(k)}</dt><dd>{e(v)}</dd>'
                     for k, v in (meta.get('system_record') or {}).items())

    alert = ''
    if meta.get('abandoned'):
        alert = ('<div class="alert"><b>⚠️ 面談未完成</b>　候選人在最後一則之後沒有再回應，'
                 '推測是關掉視窗離開。以下內容只涵蓋談到的部分。</div>')

    tpl = open(os.path.join(TPL_DIR, 'consultant.html'), encoding='utf-8').read()
    return _render(tpl, {
        'NAME': e(meta.get('name') or ''),
        'JOB': e(meta.get('job_title') or meta.get('job_slug') or ''),
        'VERDICT': e(data.get('verdict') or '待顧問判斷'),
        'ONE_LINER': e(data.get('one_liner')),
        'SELLING_POINT': e(data.get('top_selling_point') or '（未填）'),
        'TOP_RISK': e(data.get('top_risk') or '（未填）'),
        'NUMS': _nums(meta, _current_state(data)),
        'BASICS': _basics(data, meta, for_client=False),
        'ABANDONED_ALERT': alert,
        'SUMMARY': e(data.get('summary')),
        'MOTIVATION': motivation,
        'VALUES': ''.join(f'<span>{e(v)}</span>' for v in (data.get('values') or [])),
        'VALUES_BASIS': e('依據：' + (data.get('values_basis') or '')),
        'TRACK': _track(wh),
        'JOBS': _jobs(wh),
        'FACTS': '',
        'GAPS': gaps,
        'FILES': ''.join(files),
        'COND': _cond(data.get('hard_conditions') or [], with_evidence=True),
        'FIT': _fit(data),
        'EXPERTISE': _expertise(data),
        'ASSESSMENT': bars,
        'ASKED': asked,
        'STYLE': e((data.get('observations') or {}).get('communication_style')),
        'REASONS': reasons,
        'RISKS': risks_html,
        'SPEC': spec_html,
        'SPEC_NOTE': '',
        'FOLLOWUPS': fups,
        'SYSREC': sysrec,
    }, {
        'values': bool(data.get('values')),
        'history': bool(wh),
        'gaps': bool(gaps),
        'fit': bool((data.get('fit_scores') or {}).get('dimensions')),
        'expertise': bool(data.get('expertise_findings')),
        'cond': bool(data.get('hard_conditions')),
        'style': bool((data.get('observations') or {}).get('communication_style')),
        'forclient': bool(reasons or risks_html),
        'spectrum': bool(spec_rows),
        'followups': bool(fups),
    })


# ─────────────────────────────────────────────────────────────
def html_to_pdf(html_text, out_path):
    """headless Chrome 轉 PDF。成功回 True，失敗回 False（**不丟例外**）。

    ⚠️ 交付流程的每一步都不可以讓面談收尾掛掉，所以這裡把錯誤吃掉只回布林。
    --no-pdf-header-footer 是必要的：預設會在每頁印上網址與日期，
    那份 PDF 是要轉給用人企業的，頁尾出現 file:///tmp/... 很難看。
    """
    tmp_html = None
    try:
        fd, tmp_html = tempfile.mkstemp(suffix='.html')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(html_text)
        r = subprocess.run(
            [CHROME, '--headless', '--disable-gpu', '--no-pdf-header-footer',
             f'--print-to-pdf={out_path}', f'file://{tmp_html}'],
            capture_output=True, text=True, timeout=120)
        ok = os.path.exists(out_path) and os.path.getsize(out_path) > 1000
        if not ok:
            print(f'[deliver] PDF 產生失敗：{(r.stderr or r.stdout or "")[-300:]}')
        return ok
    except Exception as ex:
        print(f'[deliver] PDF 例外：{ex}')
        return False
    finally:
        if tmp_html and os.path.exists(tmp_html):
            os.remove(tmp_html)


def pdf_filename(name, kind):
    """檔名要好認——顧問是在手機的 Telegram 上翻這些附件的。

    ⚠️ 客戶版在匿名職缺時，檔名也要用匿名稱呼。顧問常常是把這個檔案直接
    轉給客戶的，內文匿名了、檔名寫著全名，等於沒匿名。
    """
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', str(name or '候選人')).strip('_') or '候選人'
    return f'{safe}_面談報告_顧問版.pdf' if kind == 'consultant' else f'{safe}_人選推薦_客戶版.pdf'


def is_anonymous(meta):
    """客戶版是否必須匿名。jobs.client_named：1＝可具名；0 或 NULL＝匿名。

    NULL 從嚴當成匿名——那代表「還沒決定有沒有簽約」，
    猜錯的代價是把未簽約客戶的人選資料具名送出去，不對稱。
    """
    named = (meta or {}).get('client_named')
    return not (named == 1 or named == '1')


def client_display_name(meta):
    return anon_name(meta.get('name')) if is_anonymous(meta) else (meta.get('name') or '候選人')


def closing_message(data, meta, degraded=False, reason=''):
    """第 4 件事：簡短結語 ＋ 顧問要協助的事項，結尾固定那一句。

    為什麼結語要短：顧問在手機上看通知，長訊息會被 Telegram 折起來，
    折起來就等於沒看到。判斷細節本來就在 PDF 裡。
    """
    name = meta.get('name') or '候選人'
    job = meta.get('job_title') or meta.get('job_slug') or ''
    head = '⚠️ 面談中斷（候選人未收尾）' if meta.get('abandoned') else '✅ 面談完成'
    lines = [f'{head}：{name}（{job}）']

    if degraded:
        # 2026-08-19 加原因：原本只說「產生失敗」，顧問無從判斷是偶發還是壞了，
        # 也沒辦法告訴我要查哪裡。降級有好幾種成因，講清楚是哪一種才有用。
        lines += ['', '⚠️ 結構化報告產生失敗，PDF 未附。純文字報告與履歷照常附上，'
                      '請至後台看完整內容。']
        if reason:
            lines.append(f'原因：{reason}')
    else:
        lines += ['', f'判定：{data.get("verdict") or "待顧問判斷"}',
                  f'定位：{data.get("one_liner") or "—"}']
        if data.get('top_risk'):
            lines.append(f'最大風險：{data["top_risk"]}')

    todo = [x for x in (data.get('consultant_followups') or []) if x][:3]
    if todo:
        lines += ['', '需要你協助的事：']
        lines += [f'{n}. {t}' for n, t in enumerate(todo, 1)]
    elif not degraded:
        lines += ['', '需要你協助的事：報告沒有列出待追問項目，請自行確認。']

    lines += ['', '詳細請參閱內部報告。']
    return '\n'.join(lines)


def build_pdfs(data, meta, out_dir):
    """產出兩份 PDF，回傳 [(kind, 絕對路徑, 檔名), ...]。產不出來的那份就不在清單裡。"""
    made = []
    for kind, builder in (('consultant', build_consultant_html),
                          ('client', build_client_html)):
        try:
            html_text = builder(data, meta)
        except Exception as ex:
            print(f'[deliver] {kind} HTML 組裝失敗：{ex}')
            continue
        # 客戶版用「對客戶顯示的名字」當檔名（匿名職缺時就是「王先生／小姐」）
        fn = pdf_filename(meta.get('name') if kind == 'consultant'
                          else client_display_name(meta), kind)
        path = os.path.join(out_dir, fn)
        if html_to_pdf(html_text, path):
            made.append((kind, path, fn))
    return made


if __name__ == '__main__':
    import sys
    data = json.load(open(sys.argv[1], encoding='utf-8'))
    meta = json.load(open(sys.argv[2], encoding='utf-8'))
    out_dir = sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    # 順手把 HTML 也留一份，出問題時可以直接開來看是資料還是版面的問題
    open(os.path.join(out_dir, 'consultant.html'), 'w', encoding='utf-8').write(
        build_consultant_html(data, meta))
    open(os.path.join(out_dir, 'client.html'), 'w', encoding='utf-8').write(
        build_client_html(data, meta))
    for kind, path, fn in build_pdfs(data, meta, out_dir):
        print(f'{kind}\t{path}\t{os.path.getsize(path)} bytes')
    print('--- 要推的訊息 ---')
    print(closing_message(data, meta))
