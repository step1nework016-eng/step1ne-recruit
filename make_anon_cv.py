#!/usr/bin/env python3
"""把一位候選人做成「匿名履歷」PDF，用來推給客戶。

去識別化照 Step1ne 現行範本的標準：姓＋先生／小姐、公司寫成「產業＋規模」、
保留年齡、居住地、語言、駕照與具體成就數字。**只有一種版本**——
不管對方簽約了沒有，給的東西一樣，顧問自己決定要不要送。

內容不是套版產生的——履歷原文與初篩報告交給 claude 讀，抽成結構化 JSON 再排版。
套版做不到「把『大型證券商』這種去識別化描述寫得像人寫的」。

用法：
    python3 make_anon_cv.py 呂皓宇
    python3 make_anon_cv.py <application_id> --out ~/Desktop/x.pdf
"""
import os, sys, json, argparse, subprocess, datetime, html, importlib.util, re

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

MODEL = 'claude-sonnet-5'   # 這份是要拿去談生意的，寫壞比慢更貴

# ── 三種履歷模式（2026-08-06 顧問定的）──
#
#   anon    尚未簽約的客戶  → 去識別化 ＋ Step1ne 抬頭   （原本唯一的那種）
#   client  已簽約的客戶    → 完整資料 ＋ Step1ne 抬頭
#   private 私人合作        → 完整資料 ＋ 無抬頭無品牌
#
# 其實是兩個獨立的開關：**要不要去識別化** × **要不要掛 Step1ne 抬頭**。
#
# ⚠️ client 與 private 會輸出候選人的全名、電話、Email、現職公司。
#    那是實質的個資揭露，**寄出去之前一定要確認人選已經同意推薦給這一家**。
#    private 連 Step1ne 抬頭都沒有——流出去追不到來源，風險最高。
# ⚠️ 就業服務法第 5 條的限制（婚育、宗教、政黨、星座、血型）**三種模式一律適用**，
#    那是法律，不是去識別化的一部分。
# ⚠️ 不提供預設值。三種給錯對象的後果差很多，一定要由人明確指定。
MODES = {
    'anon':    {'anonymize': True,  'brand': True,
                'label': '匿名履歷', 'scope': '提供致客戶進行工作職位媒合之使用'},
    'client':  {'anonymize': False, 'brand': True,
                'label': '推薦履歷', 'scope': '提供予已簽約客戶進行工作職位媒合之使用'},
    'private': {'anonymize': False, 'brand': False,
                'label': '履歷',     'scope': ''},
}

# ── 去識別化規則（照 Step1ne 現行範本，只有一套）──
RULE = """- 公司寫成「產業＋規模」，例：大型證券商（500 人以上）、連鎖餐飲集團、
     環境衛生服務業（澳洲廠務清潔）。**不要**出現公司全名。
   - 姓名只留姓＋先生／小姐；英文只留姓。
   - 居住地可到行政區、年齡可寫出生年與歲數與役別、證照可列類別明細、
     任職期間可到月——這些是說服力來源，範本本來就有，保留。
   - 學校名稱不寫，只寫學歷層級。"""

SCHEMA = """{
  "surname": "只有姓，例：呂",
  "surname_en": "英文姓，例：Lu",
  "gender_title": "先生 或 小姐（履歷判斷得出來才填，判斷不出來填「先生／小姐」）",
  "basic": [["欄位名","值"], ...],
  "summary": [["標籤","一句話說明"], ...],
  "bullets": ["條列的優勢，每條一句話，4–7 條"],
  "motivation": {"quote": "人選自己說的話，第一人稱，可從逐字稿或履歷擷取；沒有就填空字串", "note": "（本段由顧問協助整理人選應徵動機）"},
  "experiences": [{"org":"去識別化後的服務單位","period":"任職期間","title":"職稱","points":["工作內容重點"]}],
  "fit": "兩三句話：為什麼這個人適合這個職缺／這類職缺"
}"""


def build_prompt(app, job, resume_text, report_md, mode='anon'):
    m = MODES[mode]
    if True:   # 三種模式共用同一套結構與排版（2026-08-06 統一）
        # ⚠️ client 與 private 用同一套結構與排版，差別只在有沒有掛 Step1ne 抬頭。
        # 2026-08-06 踩過：client 走舊 schema、排版走新版，欄位名對不起來，
        # 產出來的履歷只剩工作經歷，姓名摘要技能學歷全是空的。
        receiver = {
            'anon':    '**還沒跟我們簽約的客戶**——目的是讓他想見這個人，進而願意委任我們',
            'client':  '**已經簽約的客戶**，對方會直接安排面試',
            'private': '**私人合作的對象**（例如私人招待所），對方會直接安排面試',
        }[mode]
        return f"""你是資深獵頭顧問，要把一位候選人整理成一份「候選人推薦履歷」，
交給{receiver}。

## 這一份的性質
{('''- **要做去識別化**，這是最重要的一條，違反等於洩漏個資：
''' + RULE + '''
- `name` 欄位只填「姓＋先生／小姐」（例：范先生），`name_en` 只填英文姓。
- `experiences` 的 `org` 寫「產業＋規模」，**不要出現公司全名**。
- `education` 不寫學校名稱，只寫學歷層級。
- `meta` 與 `conditions` 不要出現電話、Email、地址門牌。
- 寫完自己反問一次：「拿這份去 Google 或 LinkedIn 搜得到他本人嗎？」
  搜得到就把最獨特的那個細節再模糊一級。''') if m['anonymize'] else '''- **不做匿名化**：姓名、年齡、役別、現居行政區、求職條件都照實寫。'''}
{'' if m['anonymize'] else '''- **但場所名稱可以依人選意願以類別呈現**（例如「餐飲品牌（連鎖餐館業）」）——
  那是人選的隱私選擇，不是我們的去識別化規則。履歷原文寫全名的就照寫，
  原文本來就模糊的不要自己補回去。'''}
- 就業服務法第 5 條：**不要**主動寫入婚育狀況、宗教、政黨、星座、血型。
- **不要美化、不要編**。履歷與報告裡沒有的東西一個字都不能加。
  沒有的欄位就給空陣列，寧可少也不要編。

## 寫作要求
- `consultant_summary` 是**顧問視角的摘要**，2–3 段、每段一到三句。
  講資歷全貌與「這個人可以拿來做什麼」，不要變成工作經歷的流水帳。
- `plus_points` 是**針對這個職缺**的加分項，每一條要能對上職缺需求，
  格式是「標題——說明」。沒有真的對得上的就不要硬湊。
- `experiences` 由新到舊。`duration` 寫任期長度（例如「（3 年 3 個月）」或「（現職・PT）」）。
  `tags` 放班別、地點這類短標籤。短任期若履歷有交代原因，寫進 `note`。
- `skills` 是 8–14 個短詞的技能標籤。
- 數字要保留（帶幾人、幾年、幾張證照），那是說服力來源。

## 職缺
{job.get('title') or ''}／{job.get('client_name') or '（未綁客戶）'}
必備條件：{job.get('must_skills') or '（未填）'}

## 候選人履歷原文
{(resume_text or '（無履歷文字）')[:12000]}

## AI 初篩報告（顧問視角的觀察，可引用結論但不要照抄）
{(report_md or '（無報告）')[:6000]}

## 輸出
只輸出一個 JSON 物件，不要有其他文字、不要包在程式碼區塊裡。結構：
{SCHEMA_PRIVATE}
"""

    if m['anonymize']:
        ident = f"""你是資深獵頭顧問，要把一位候選人做成「匿名履歷」，交給客戶評估。
對方可能還沒跟我們簽約——目的是讓他想見這個人，進而願意委任我們。

## 去識別化規則（最重要，違反等於洩漏個資）
{RULE}

共通鐵則：
- **絕對不出現**：全名、電話、Email、現職公司全名、學校名稱、身分證字號、地址門牌。
- **不要出現可反查的獨特組合**。寫完後自己反問一次：「拿這份去 Google 或 LinkedIn
  搜得到他本人嗎？」搜得到就把最獨特的那個細節再模糊一級。"""
    else:
        ident = """你是資深獵頭顧問，要把一位候選人的履歷整理成一份好讀的推薦履歷。
對方是**已經談定合作的對象**，會直接安排面試，所以**資料要完整、不要遮蔽**。

## 這一份不做去識別化
- 姓名、聯絡電話、Email、現職公司全名、學校名稱**照實寫**，那是對方要用來聯絡與查證的。
- `surname` 欄位請填**完整姓名**（不是只有姓），`gender_title` 留空字串。
- 服務單位寫**公司全名**，後面可以補上產業與規模。
- ⚠️ 但**還是不要編**——履歷與報告裡沒有的資訊一個字都不能加。

共通鐵則："""
    return f"""{ident}
- 就業服務法第 5 條：**不要**主動寫入婚育狀況、宗教、政黨、星座、血型。
- **不要美化、不要編**。履歷與報告裡沒有的東西一個字都不能加。
  沒有的欄位就不要放進 basic／summary，寧可少也不要編。
- 成就要保留**具體數字**（帶幾人、幾年、幾張證照、幾點到幾點），那是說服力來源。

## 職缺
{job.get('title') or ''}／{job.get('client_name') or '（未綁客戶）'}
必備條件：{job.get('must_skills') or '（未填）'}

## 候選人履歷原文
{(resume_text or '（無履歷文字）')[:12000]}

## AI 初篩報告（顧問視角的觀察，可引用結論但不要照抄）
{(report_md or '（無報告）')[:6000]}

## 輸出
只輸出一個 JSON 物件，不要有其他文字、不要包在程式碼區塊裡。結構：
{SCHEMA}
"""


CSS = """
@page{size:A4;margin:14mm 13mm 16mm}
body{ /* 2026-08-06：字型順序不要改。原本 PingFang 排第一，WeasyPrint 子集化 .ttc（PingFang 是字型集合）會產出壞掉的 CID 字型——文字層是對的（pdftotext 讀得出來），但字形畫不出來，顧問收到一份亂碼履歷。Noto Sans CJK TC 是 .otf，子集化正常。注意 CSS 要寫「Noto Sans CJK TC」，不是「Noto Sans TC」——後者配對不到會退回 PingFang。*/ font-family:"Noto Sans CJK TC","PingFang TC","Heiti TC",sans-serif;font-size:10.5pt;
 color:#1c1f26;line-height:1.65}
.top{background:#1e3a5f;color:#fff;padding:9px 12px;font-size:9.5pt;line-height:1.55;font-weight:600}
.top b{letter-spacing:.06em}
.auth{font-size:9pt;color:#5a6472;font-style:italic;margin:7px 0 16px;
 display:flex;justify-content:space-between;gap:12px}
h2{background:#1e3a5f;color:#fff;font-size:11pt;padding:6px 12px;margin:20px 0 0;
 border-radius:3px 3px 0 0}
table{width:100%;border-collapse:collapse}
td{border:1px solid #d4d8de;padding:7px 11px;vertical-align:top}
td.k{width:30%;background:#f6f7f9;font-weight:700}
ul{margin:10px 0 0;padding-left:19px}
li{margin-bottom:6px}
.quote{border-left:3px solid #1e3a5f;background:#f6f7f9;padding:11px 14px;margin:11px 0 6px;
 font-style:italic;line-height:1.85}
.qnote{font-size:9pt;color:#7b8290}
.exp td.h{background:#eef1f5;font-weight:700;border-bottom:2px solid #1e3a5f}
.fit{background:#f6f7f9;border:1px solid #d4d8de;padding:11px 14px;margin-top:10px;border-radius:4px}
.foot{margin-top:22px;font-size:8.5pt;color:#8a9099;text-align:center;
 border-top:1px solid #e2e5ea;padding-top:9px}
"""


CSS_PRIVATE = """@page{size:A4;margin:13mm 12mm 14mm}
body{font-family:"Noto Sans CJK TC","PingFang TC","Heiti TC",sans-serif;
 font-size:9.6pt;color:#20242b;line-height:1.62;margin:0}
.brand{background:#1f4e79;color:#fff;padding:8px 12px;font-size:8.6pt;line-height:1.6;
 border-radius:4px;margin-bottom:12px}
.brand b{letter-spacing:.1em;margin-right:6px}
.brand i{display:block;font-style:normal;opacity:.8;margin-top:2px}
.hd{display:flex;justify-content:space-between;align-items:baseline;
 border-bottom:2px solid #1f4e79;padding-bottom:6px;margin-bottom:16px}
.hd b{color:#1f4e79;font-size:14pt;letter-spacing:.04em}
.hd span{color:#98a0ab;font-size:8pt;letter-spacing:.16em}
.nm{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:7px}
.nm .n{font-size:23pt;font-weight:800;letter-spacing:.02em}
.nm .en{font-size:11pt;color:#7b8492;font-weight:400;margin-left:7px}
.nm .ap{text-align:right;font-size:9.4pt;font-weight:700}
.nm .ap i{display:block;font-style:normal;font-weight:400;color:#7b8492;font-size:8.8pt;margin-top:2px}
.meta{font-size:8.9pt;color:#3d444e;line-height:1.9;margin:0 0 18px;
 border-bottom:1px solid #e6e9ee;padding-bottom:11px}
.meta b{font-weight:700}
.meta i{font-style:normal;color:#c3c9d1;margin:0 7px}
h2{font-size:10.4pt;font-weight:800;color:#1f4e79;margin:17px 0 8px;
 padding-left:9px;border-left:4px solid #e07b39;line-height:1.3}
p{margin:0 0 7px}
.plus{background:#f7f9fb;border:1px solid #e4e9ef;border-radius:5px;padding:11px 14px;margin:0 0 4px}
.plus ul{margin:0;padding-left:15px}
.plus li{margin-bottom:5px}
.plus li b{font-weight:700}
.note{font-size:8.2pt;color:#98a0ab;margin:5px 0 0;line-height:1.7}
table.exp{width:100%;border-collapse:collapse}
table.exp td{vertical-align:top;padding:0 0 13px}
td.dt{width:23%;font-size:8.9pt;color:#3d444e;padding-right:10px;line-height:1.7}
td.dt b{display:block;font-weight:700;color:#20242b}
td.dt span{color:#8a93a0;font-size:8.4pt}
.ttl{font-weight:800;font-size:9.9pt}
.org{color:#3d444e}
.tag{display:inline-block;font-size:8.1pt;color:#5d6672;background:#eef1f5;
 border-radius:3px;padding:1px 6px;margin:0 4px 3px 0}
ul.pt{margin:4px 0 0;padding-left:15px}
ul.pt li{margin-bottom:2px}
.chips span{display:inline-block;font-size:8.4pt;background:#eef4f9;color:#2b5b86;
 border-radius:11px;padding:2px 10px;margin:0 5px 5px 0}
.cols{display:flex;gap:26px}
.cols>div{flex:1}
.kv{font-size:9.1pt;line-height:1.85}
.kv b{font-weight:700}
.kv .sub{color:#7b8492;font-size:8.6pt}
.ft{margin-top:20px;padding-top:9px;border-top:1px solid #e6e9ee;
 text-align:center;font-size:8pt;color:#98a0ab}"""

SCHEMA_PRIVATE = """{
  "name": "完整姓名，例：李靜承",
  "name_en": "英文名，沒有就空字串",
  "apply_title": "應徵職稱",
  "apply_org": "用人單位的類別，例：私人招待所",
  "meta": ["性別・年齡・役別", "現居 行政區", "交通工具", "總年資 X 年（產業）", "現況：在職／待業", "求職方向", "可到職"],
  "consultant_summary": ["顧問摘要，2-3 段，每段一句到三句話。講資歷全貌與可用之處，不要條列式流水帳"],
  "plus_points": [["加分點標題", "說明，一到兩句"]],
  "plus_note": "＊標註哪些是自述、哪些場所名稱依隱私原則以類別呈現；沒有就空字串",
  "experiences": [{"period":"2025/06-迄今", "duration":"（現職・PT）", "title":"職稱", "org":"公司或場所（可用類別呈現）", "tags":["排班制","台北・中山"], "points":["工作內容重點"], "note":"＊補充說明，沒有就空字串"}],
  "skills": ["技能標籤，8-14 個短詞"],
  "education": [["學校或學制", "科系・學位（起訖年月）"]],
  "certs": ["證照名稱"],
  "languages": [["語言", "程度"]],
  "conditions": [["性質", "尋求正職…"], ["地點", "…"], ["待遇", "…"], ["工時", "…"], ["到職", "…"]]
}"""


def render_private(c, job, mode='private'):
    """私人合作履歷。2026-08-06 依顧問提供的範本（李靜承 Terry 那份）重做。

    跟 anon／client 那套完全不同的排版：無品牌抬頭、藍色左邊線區塊、
    左日期右內容的工作經歷、技能標籤、學歷／證照／語言／求職條件分兩欄。

    ⚠️ 這個模式**不做去識別化**——姓名、年齡、現居、聯絡條件都照實寫。
    但場所名稱仍可依人選意願以類別呈現（範本頁尾就是這樣註明的），
    那是人選的隱私選擇，不是我們的去識別化規則。
    """
    e = html.escape
    today = datetime.date.today().isoformat()
    m = MODES[mode]

    meta = '<i>｜</i>'.join(e(x) for x in (c.get('meta') or []) if x)
    summ = ''.join(f'<p>{e(x)}</p>' for x in (c.get('consultant_summary') or []))

    plus = ''
    if c.get('plus_points'):
        li = ''.join(f'<li><b>{e(k)}</b>——{e(v)}</li>' for k, v in c['plus_points'])
        plus = f'<div class="plus"><ul>{li}</ul></div>'
        if c.get('plus_note'):
            plus += f'<p class="note">{e(c["plus_note"])}</p>'

    exp = ''
    for x in (c.get('experiences') or []):
        tags = ''.join(f'<span class="tag">{e(t)}</span>' for t in (x.get('tags') or []))
        pts = ''.join(f'<li>{e(p)}</li>' for p in (x.get('points') or []))
        note = f'<p class="note">{e(x["note"])}</p>' if x.get('note') else ''
        exp += (f'<tr><td class="dt"><b>{e(x.get("period") or "")}</b>'
                f'<span>{e(x.get("duration") or "")}</span></td>'
                f'<td><span class="ttl">{e(x.get("title") or "")}</span>　'
                f'<span class="org">{e(x.get("org") or "")}</span>　{tags}'
                f'{"<ul class=pt>" + pts + "</ul>" if pts else ""}{note}</td></tr>')

    chips = ''.join(f'<span>{e(x)}</span>' for x in (c.get('skills') or []))
    edu = ''.join(f'<div class="kv"><b>{e(k)}</b><br><span class="sub">{e(v)}</span></div>'
                  for k, v in (c.get('education') or []))
    certs = ''.join(f'<div class="kv">{e(x)}</div>' for x in (c.get('certs') or []))
    langs = ''.join(f'<div class="kv"><b>{e(k)}</b>：{e(v)}</div>'
                    for k, v in (c.get('languages') or []))
    cond = ''.join(f'<div class="kv"><b>{e(k)}</b>：{e(v)}</div>'
                   for k, v in (c.get('conditions') or []))

    sec = lambda t, body: f'<h2>{t}</h2>{body}' if body.strip() else ''
    # client 掛 Step1ne 抬頭與授權聲明；private 完全不掛（流出去追不到來源是它的性質）
    if m['brand']:
        brand_top = (f'<div class="brand"><b>STEP1NE</b>　本資料由德仁管理顧問有限公司提供，'
                     f'{m["scope"]}，不得轉交第三人，請保護人選個資。'
                     f'<i>推薦日期：{today}</i></div>')
        brand_ft = 'Step1ne 德仁管理顧問有限公司　｜　'
    else:
        brand_top, brand_ft = '', '候選人推薦履歷　｜　'
    return f"""<html><head><meta charset="utf-8"><style>{CSS_PRIVATE}</style></head><body>
{brand_top}<div class="hd"><b>候選人推薦履歷</b><span>CANDIDATE PROFILE</span></div>
<div class="nm"><div><span class="n">{e(c.get('name') or '')}</span>
<span class="en">{e(c.get('name_en') or '')}</span></div>
<div class="ap">應徵職稱：{e(c.get('apply_title') or job.get('title') or '')}
<i>（{e(c.get('apply_org') or '')}）</i></div></div>
<div class="meta">{meta}</div>
{sec('顧問摘要', summ)}
{sec('為此職務加分之相關經驗', plus)}
{sec('工作經歷', f'<table class="exp">{exp}</table>' if exp else '')}
{sec('專長與技能', f'<div class="chips">{chips}</div>' if chips else '')}
<div class="cols">
 <div>{sec('學歷', edu)}{sec('語言', langs)}</div>
 <div>{sec('證照', certs)}{sec('求職條件', cond)}</div>
</div>
<div class="ft">{brand_ft}資料由人選提供，部分場所名稱依隱私原則以類別呈現</div>
</body></html>"""


def render_html(c, job, mode='anon'):
    e = html.escape
    nm = f"{e(c['surname'])}{e(c.get('gender_title') or '先生／小姐')}"
    rows = ''.join(f'<tr><td class="k">{e(k)}</td><td>{e(v)}</td></tr>' for k, v in c.get('basic', []))
    summ = ''.join(f'<tr><td class="k">{e(k)}</td><td>{e(v)}</td></tr>' for k, v in c.get('summary', []))
    bl = ''.join(f'<li>{e(x)}</li>' for x in c.get('bullets', []))
    mot = ''
    if (c.get('motivation') or {}).get('quote'):
        mot = (f'<h2>應徵動機</h2><div class="quote">「{e(c["motivation"]["quote"])}」</div>'
               f'<div class="qnote">{e(c["motivation"].get("note") or "")}</div>')
    exp = ''
    for x in c.get('experiences', []):
        pts = ''.join(f'<li>{e(p)}</li>' for p in x.get('points', []))
        exp += (f'<tr><td>{e(x.get("org") or "")}</td><td>{e(x.get("period") or "")}</td>'
                f'<td>{e(x.get("title") or "")}</td><td><ul>{pts}</ul></td></tr>')
    today = datetime.date.today().isoformat()
    m = MODES[mode]
    # 抬頭：anon／client 掛 Step1ne，private 完全不掛（私人合作，流出去追不到來源）
    if m['brand']:
        header = (f'<div class="top"><b>STEP1NE</b>　本資料由德仁管理顧問有限公司提供，'
                  f'{m["scope"]}，不得轉交第三人，請保護人選個資。</div>\n'
                  f'<div class="auth"><span>相關授權文件已由人選提供予德仁管理顧問有限公司。</span>'
                  f'<span>推薦日期：{today}</span></div>')
    else:
        header = f'<div class="auth" style="justify-content:flex-end"><span>製作日期：{today}</span></div>'
    return f"""<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
{header}

<h2>基本資料</h2><table>
<tr><td class="k">人　選</td><td>{nm}</td></tr>
<tr><td class="k">Candidate</td><td>Mr./Ms. {e(c.get('surname_en') or '')}</td></tr>
{rows}</table>

<h2>Summary 及個人特質、優勢</h2><table>{summ}</table>
<ul>{bl}</ul>
{mot}

<h2>Work Experiences</h2><table class="exp">
<tr><td class="h">服務單位（去識別化）</td><td class="h">任職期間</td>
<td class="h">職稱</td><td class="h">工作內容重點</td></tr>
{exp}</table>

{'<h2>顧問觀點</h2><div class="fit">' + e(c.get('fit') or '') + '</div>' if c.get('fit') else ''}

{'<div class="foot">Step1ne 德仁管理顧問有限公司　｜　' + e(job.get('title') or '') + '　｜　' + today + '</div>' if m['brand'] else ''}
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('who', help='候選人姓名或 application_id')
    ap.add_argument('--out')
    ap.add_argument('--mode', choices=list(MODES), default=None,
                    help='anon 未簽約客戶（去識別化）／client 已簽約客戶（完整＋Step1ne 抬頭）／'
                         'private 私人合作（完整、無品牌）')
    a = ap.parse_args()

    rows = D.d1(f"SELECT id,name,job_slug,job_title,resume_file_id,resume_url_text "
                f"FROM applications WHERE id={D.q(a.who)} OR name={D.q(a.who)} LIMIT 1")
    if not rows:
        sys.exit(f'找不到候選人：{a.who}')
    app = rows[0]

    job = (D.d1(f"SELECT title,client_name,must_skills,cv_mode FROM jobs "
                f"WHERE slug={D.q(app['job_slug'])}") or [{}])[0]

    # ⚠️ 2026-08-06 顧問要求：不要替他選。三種履歷給錯對象的後果差很多——
    # 把完整個資的版本寄給還沒簽約的客戶，那是實質的個資外洩。
    # 但**職缺本身可以先定死用哪一種**（jobs.cv_mode），設好了就不用每次問。
    # 例：VIP貴賓接待是私人合作案，一律 private。
    if not a.mode and job.get('cv_mode') in MODES:
        a.mode = job['cv_mode']
        print(f"職缺「{job.get('title') or app['job_slug']}」已固定用"
              f"「{MODES[a.mode]['label']}」（{a.mode}），不再詢問")
    if not a.mode:
        sys.exit('請指定 --mode：\n'
                 '  anon    尚未簽約的客戶　去識別化＋Step1ne 抬頭\n'
                 '  client  已簽約的客戶　　完整資料＋Step1ne 抬頭\n'
                 '  private 私人合作　　　　完整資料、無品牌抬頭\n'
                 '⚠️ client／private 會輸出全名、電話、Email、公司全名，寄出前請確認人選已同意。\n'
                 '（要讓某個職缺以後都固定用同一種，把 jobs.cv_mode 設好就不用每次指定：\n'
                 '  wrangler d1 execute step1ne-recruit --remote --command '
                 '"UPDATE jobs SET cv_mode=\'private\' WHERE slug=\'<slug>\'"）')

    # 履歷文字：優先用檔案抽出來的，沒有就用連結抓的
    txt = app.get('resume_url_text') or ''
    if app.get('resume_file_id'):
        f = D.d1(f"SELECT text_content FROM files WHERE id={D.q(app['resume_file_id'])}")
        if f and f[0].get('text_content'):
            txt = f[0]['text_content']
    rep = D.d1(f"SELECT content_md FROM reports WHERE application_id={D.q(app['id'])} "
               f"ORDER BY created_at DESC LIMIT 1")
    report_md = rep[0]['content_md'] if rep else ''

    if not txt and not report_md:
        sys.exit('這位候選人既沒有履歷文字也沒有報告，做不出匿名履歷')

    print(f"讀取 {app['name']}：履歷 {len(txt)} 字、報告 {len(report_md)} 字")
    # ⚠️ 2026-08-06 加重試：模型偶爾會吐出壞掉的 JSON（少一個逗號之類），
    # 原本一次失敗就整支掛掉，等於白等五分鐘。三次都失敗才放棄。
    c = None
    for attempt in range(1, 4):
        r = subprocess.run(['claude', '-p', build_prompt(app, job, txt, report_md, a.mode),
                            '--model', MODEL, *D.NO_TOOLS, '--output-format', 'text'],
                           capture_output=True, text=True, env=D.env_with_cf(), timeout=600)
        out = (r.stdout or '').strip()
        if out.startswith('```'):
            out = out.split('\n', 1)[-1]
            if out.rstrip().endswith('```'):
                out = out.rstrip()[:-3]
        m = re.search(r'\{.*\}', out, re.S)
        if not m:
            print(f'  第 {attempt} 次：模型沒有回傳 JSON'
                  f'（returncode={r.returncode}）{(r.stderr or "")[-160:]}')
            continue
        try:
            c = json.loads(m.group(0))
            break
        except json.JSONDecodeError as ex:
            print(f'  第 {attempt} 次：JSON 壞掉（{ex}），重試')
    if c is None:
        sys.exit('三次都拿不到可解析的 JSON，放棄。'
                 '（模型輸出問題，不是資料問題——直接重跑一次多半就好了）')

    # 最後一道防線：模型再怎麼被交代過，還是可能把全名寫回去。
    # ⚠️ 2026-08-06：這道防線**只在匿名模式生效**。
    # 第一次跑 private 模式時它照常執行，把應該要有的姓名整個砍掉，
    # 產出的檔名變成「履歷__」——保險裝在錯的地方，就變成破壞。
    full = app['name'] if MODES[a.mode]['anonymize'] else ''
    bad = [x for x in (full, full[1:]) if x and len(x) >= 2]
    body = json.dumps(c, ensure_ascii=False)
    for b in bad:
        if b in body:
            print(f'⚠️ 產出裡出現了候選人全名或名字「{b}」，已自動移除')
            body = body.replace(b, '')
    c = json.loads(body)

    # 檔名用的姓名欄位依模式不同：匿名版只有 surname，其餘是完整 name
    # 三種模式統一之後，姓名都在 name 欄位（匿名版是「范先生」，其餘是全名）
    who_name = c.get('name') or app['name']
    out_pdf = a.out or f"/tmp/{MODES[a.mode]['label']}_{who_name}_{app['job_slug']}.pdf"
    html_path = out_pdf.replace('.pdf', '.html')
    open(html_path, 'w', encoding='utf-8').write(
        render_private(c, job, a.mode))
    subprocess.run(['weasyprint', html_path, out_pdf], check=True, timeout=120)
    # ⚠️ 2026-08-06 起保留 HTML。原本產完就刪，結果字型出問題要重修時，
    # 只能連 AI 那段（約 5 分鐘）一起重跑。留著就能 `weasyprint x.html x.pdf` 幾秒重產。
    # os.remove(html_path)
    print(f'✅ {out_pdf}')
    return out_pdf


if __name__ == '__main__':
    main()
