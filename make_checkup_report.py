#!/usr/bin/env python3
"""履歷健檢報告（阿福）：把一份 JSON 稿子排成 HTML，再印成 PDF。

版面是 Jacky 2026-08-10 看過並認可的那一版（scratchpad/checkup_mary.html），
**六段結構與樣式照抄，不要重新設計**。這支只負責排版與把關，
內容由阿福依技能包 recruiting-workflow/resume-checkup/SKILL.md 產出。

用法：
    python3 make_checkup_report.py 稿子.json --out ~/Desktop/報告
        → 產出 報告.html 與 報告.pdf
    python3 make_checkup_report.py 稿子.json --out X --no-pdf
    python3 make_checkup_report.py 稿子.json --anon-card   # 只印匿名人選卡

🚨 內建的薪資數字把關（--strict，預設開啟）
   這個產品的核心賣點是「我不給你一個數字，因為照你目前的履歷給的任何數字
   都會是錯的」。所以排版時會掃過全文，**只要出現薪資形狀的數字就拒絕產出**：
     · 薪資字眼（年薪／月薪／待遇／開價／行情⋯）後面 15 字內出現阿拉伯數字
     · 55K 這種寫法
     · 「80 萬元」「50 萬起」這種金額寫法
   要寫金額區間就用 ○（例：「專案預算 ○ 萬」）——那是**要他去補的空格**，
   不是我們幫他填的數字。
   真的有必要（例如引用他自己講的募資金額）時，把那段字放進
   `"quoted_facts"` 清單裡逐句列出來，代表「這是他親口說的事實」而不是我們的估價。

稿子 JSON 的形狀（六段，順序固定）：
{
  "checkup_id": "…", "name": "Mary Lee",
  "header": {"headline": "…（可用 <br>）", "lead": "…",
             "nums": [{"k": "總年資", "v": "約 11 年"}]},
  "s1_classification": ["段落一", "段落二"],     // 你現在被歸類成什麼
  "s2_strengths":  [{"title","body","why"}],      // 最有力的三件事
  "s3_undervalued":[{"title","body","before","after"}],  // 讓你被低估的地方
  "s4_market": {"lead","factors":[{"name","proven"}],"note"},  // 你問的行情
  "s5_directions": {"items":[{"title","body"}], "note"},
  "s6_next": {"items":[{"title","body"}], "note"},
  "quoted_facts": ["他親口說的數字，逐句列"],
  "anon_card": {...}   // 見 --anon-card
}
"""
import argparse, html, json, os, re, subprocess, sys, tempfile, urllib.parse

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

# ── 薪資數字把關 ──────────────────────────────────────────────
SALARY_WORDS = ('年薪', '月薪', '薪水', '薪資', '待遇', '開價', '報酬', '年收',
                '時薪', '行情', '身價', 'offer', 'package')
FORBID = [
    (re.compile('(' + '|'.join(SALARY_WORDS) + r')[^\n。]{0,15}?\d'), '薪資字眼附近出現阿拉伯數字'),
    (re.compile(r'\d+\s*[Kk](?![a-zA-Z0-9])'), '出現「NNK」這種薪資寫法'),
    (re.compile(r'\d[\d,\.]*\s*(萬|萬元)\s*(元|以上|以下|起|上下|左右|之間|~|～|-|—)'), '出現金額區間'),
]


def _norm_spacing(s):
    """拿掉數字與單位間可能出現的空白差異（「500 萬」vs「500萬」），
    避免 quoted_facts 裡的原話跟報告正文用字略有出入就比對不到。"""
    return re.sub(r'(?<=\d)\s+(?=[萬元KkOo])', '', s)


def salary_hits(text, allowed):
    """回傳所有疑似憑空捏造的薪資數字。allowed 是本人親口說過的事實，逐句排除。

    ⚠️ 2026-08-12 實測發現：同一份逐字稿重跑兩次，quoted_facts 內容一字不差時
    可以通過，但模型偶爾會在正文把同一個數字重新斷句／加空格／加「元」，
    導致精準子字串比對不到、整份報告被誤擋。這裡對「排除詞」跟「待檢查全文」
    都先做一次寬鬆的空白正規化，只放寬到這個程度——完全不同的數字（例如
    quoted_facts 說 500 萬、正文寫 800 萬）還是會被擋下來，這才是把關真正
    該擋的情況。
    """
    scrubbed = _norm_spacing(text)
    for a in allowed:
        scrubbed = scrubbed.replace(_norm_spacing(a), '〔本人陳述〕')
    hits = []
    for rx, why in FORBID:
        for m in rx.finditer(scrubbed):
            s = max(0, m.start() - 20)
            hits.append(f'{why}：…{scrubbed[s:m.end() + 12]}…')
    return hits


# ── 排版 ─────────────────────────────────────────────────────
E = lambda s: html.escape(str(s or ''), quote=False)


def rich(s):
    """允許稿子裡用 <b> 與 <br>，其餘一律跳脫——報告是要寄給本人的，不能被內容注入。"""
    out = E(s)
    for tag in ('b', 'br'):
        out = out.replace(f'&lt;{tag}&gt;', f'<{tag}>').replace(f'&lt;/{tag}&gt;', f'</{tag}>')
    return out.replace('&lt;br/&gt;', '<br>').replace('&lt;br /&gt;', '<br>')


# ⚠️ 2026-08-12 改版：原本這份跟顧問內部用的 reporttpl/consultant.html／
# client.html 用的是同一組 --gold 金棕色調色盤，Jacky 反應「跟內部的一樣，
# 很難分辨」——這份是要寄給本人看的付費/體驗產品，混在一起會讓人以為
# 收到的是內部文件外流。改用藍色（呼應 STEP1NE 品牌識別跟阿福自己的視覺），
# 版面結構不變，純換色＋加阿福署名，一眼就要能認出「這是阿福的報告」。
CSS = """
:root{--brand:#2f6fed;--brand2:#5b8cff;--ink:#1c2438;--ink2:#4d5a73;--ink3:#8993a8;
 --line:#e3e9f5;--soft:#f3f6fd;--ok:#2f8f5b;--warn:#c98a1e;--bad:#c0392b;--bg:#eef2fb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.75 -apple-system,"Noto Sans TC",sans-serif;padding:26px 14px}
.wrap{max-width:820px;margin:0 auto}
.brandbar{display:flex;align-items:center;gap:8px;margin-bottom:14px;font-size:12.5px;
 color:var(--ink3);font-weight:700;letter-spacing:.04em}
.brandbar .dot{width:7px;height:7px;border-radius:50%;background:var(--brand)}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:14px}
h2{font-size:14px;margin:0 0 15px;letter-spacing:.06em;color:var(--ink3);font-weight:700}
h3{font-size:14px;margin:20px 0 9px;color:var(--brand)}
.hero{background:linear-gradient(160deg,#16204a,#1c2f6b);color:#fff;border-radius:18px;padding:26px;margin-bottom:14px}
.hero .tag{font-size:11px;letter-spacing:.16em;color:var(--brand2);font-weight:700}
.hero h1{font-size:23px;margin:14px 0 10px;line-height:1.5}
.hero p{margin:0;color:#c7cfe8;font-size:14.5px;line-height:1.8}
.nums{display:flex;gap:26px;flex-wrap:wrap;border-top:1px solid #34406e;margin-top:18px;padding-top:15px}
.nums div{font-size:12px;color:#a9b2cf}
.nums b{display:block;color:#fff;font-size:17px;font-weight:700;margin-top:2px}
.big{font-size:16.5px;color:var(--ink);line-height:1.85;margin:0}
.big b{color:var(--brand);}
.item{background:var(--soft);border-radius:11px;padding:14px 16px;margin-bottom:10px}
/* 只有『卡片標題』那顆 b 是整行；內文裡的 <b> 要維持行內，
   不然「是<b>真的在那些市場站過攤</b>。」會被拆成三行 */
.item>b{display:block;font-size:15px;margin-bottom:4px}
.item p{margin:0;font-size:13.5px;color:var(--ink2);line-height:1.75}
.item .why{color:var(--brand);font-size:12.5px;margin-top:6px;display:block}
.fix{border-left:3px solid var(--bad);background:#fdf7f6}
.fix b{color:#8a2b20}
.strong{border-left:3px solid var(--ok);background:#f2f9f5}
.strong b{color:#1f5c39}
.ba{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:9px}
@media(max-width:640px){.ba{grid-template-columns:1fr}}
.ba div{border-radius:10px;padding:11px 13px;font-size:13px;line-height:1.7}
.ba .b1{background:#eef1f8;color:var(--ink3)}
.ba .b2{background:#eef3ff;border:1px solid #c9d8ff;color:#2a4fae}
.ba span{display:block;font-size:11px;letter-spacing:.08em;font-weight:700;margin-bottom:5px}
.ba .b1 span{color:#a2a9bd}.ba .b2 span{color:var(--brand)}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
th,td{border-bottom:1px solid var(--line);padding:9px 8px;text-align:left;vertical-align:top}
th{color:var(--ink3);font-size:12.5px;font-weight:700}
.q{background:var(--soft);border-left:3px solid var(--brand);border-radius:0 10px 10px 0;
 padding:12px 15px;margin-bottom:9px;font-size:14px}
.q b{display:block;margin-bottom:3px}
.q span{font-size:12.5px;color:var(--ink3)}
.note{font-size:12.5px;color:var(--ink3);line-height:1.8;margin:12px 0 0}
.foot{font-size:12px;color:var(--ink3);text-align:center;line-height:1.9;margin-top:22px}
/* 列印：卡片不要被切成兩半，那會讓「現在寫的 vs 可以改成」的對照跨頁 */
@page{margin:12mm 10mm}
@media print{body{background:#fff;padding:0}
 .card,.hero,.item{break-inside:avoid;page-break-inside:avoid}}
"""


def render(spec):
    h = spec.get('header', {})
    nums = ''.join(f'<div>{E(n.get("k"))}<b>{E(n.get("v"))}</b></div>' for n in h.get('nums', []))
    P = [f'''<!doctype html>
<meta charset="utf-8">
<title>阿福・AI 履歷健檢{"｜" + E(spec.get("name")) if spec.get("name") else ""}</title>
<style>{CSS}</style>
<div class="wrap">

<div class="brandbar"><span class="dot"></span>阿福・AI 履歷健檢｜STEP1NE</div>

<div class="hero">
  <span class="tag">阿福・AI 履歷健檢報告</span>
  <h1>{rich(h.get("headline"))}</h1>
  <p>{rich(h.get("lead"))}</p>
  {f'<div class="nums">{nums}</div>' if nums else ''}
</div>''']

    # 一、你現在被歸類成什麼
    body = '<br><br>'.join(rich(x) for x in spec.get('s1_classification', []))
    P.append(f'<div class="card"><h2>一、你現在被歸類成什麼</h2><p class="big">{body}</p></div>')

    # 二、履歷上最有力的三件事
    items = ''.join(
        f'<div class="item strong"><b>{rich(i.get("title"))}</b>'
        f'<p>{rich(i.get("body"))}</p>'
        + (f'<span class="why">為什麼有力：{rich(i.get("why"))}</span>' if i.get('why') else '')
        + '</div>'
        for i in spec.get('s2_strengths', []))
    P.append(f'<div class="card"><h2>二、履歷上最有力的三件事</h2>{items}</div>')

    # 三、讓你被低估的地方——「現在寫的 vs 可以改成」是這一段的核心，不要省
    fixes = ''
    for n, i in enumerate(spec.get('s3_undervalued', []), 1):
        ba = ''
        if i.get('before') or i.get('after'):
            ba = (f'<div class="ba"><div class="b1"><span>現在寫的</span>{rich(i.get("before"))}</div>'
                  f'<div class="b2"><span>可以改成</span>{rich(i.get("after"))}</div></div>')
        fixes += (f'<div class="item fix"><b>{n}. {rich(i.get("title"))}</b>'
                  f'<p>{rich(i.get("body"))}</p>{ba}</div>')
    # 標題用中文數字（「被低估的四個地方」），不要寫成「被低估的4 個地方」——
    # 阿拉伯數字夾在中文句子裡會讓標題看起來像系統輸出，不像人寫的
    cnt = len(spec.get('s3_undervalued', []))
    zh = '〇一二三四五六七八九十'
    n_txt = zh[cnt] if 0 < cnt <= 10 else str(cnt)
    P.append(f'<div class="card"><h2>三、讓你被低估的{n_txt + "個" if cnt else ""}地方（可以改）</h2>{fixes}</div>')

    # 四、你問的行情——⚠️ 刻意不給數字
    m = spec.get('s4_market', {})
    rows = ''.join(f'<tr><td><b>{rich(f.get("name"))}</b></td><td>{rich(f.get("proven"))}</td></tr>'
                   for f in m.get('factors', []))
    P.append(
        '<div class="card"><h2>四、你問的「市場行情」</h2>'
        f'<p class="big" style="font-size:15.5px">{rich(m.get("lead"))}</p>'
        + (f'<p style="font-size:14px;color:var(--ink2);margin:14px 0 0">{rich(m.get("intro"))}</p>'
           if m.get('intro') else '')
        + (f'<table><tr><th>決定因素</th><th>你的履歷現在能證明的</th></tr>{rows}</table>' if rows else '')
        + (f'<p class="note">{rich(m.get("note"))}</p>' if m.get('note') else '')
        + '</div>')

    # 五、建議方向（不是職缺，是方向）
    d = spec.get('s5_directions', {})
    items = ''.join(f'<div class="item"><b>{rich(i.get("title"))}</b><p>{rich(i.get("body"))}</p></div>'
                    for i in d.get('items', []))
    P.append('<div class="card"><h2>五、我會建議你往哪裡走</h2>' + items
             + (f'<p class="note">{rich(d.get("note"))}</p>' if d.get('note') else '') + '</div>')

    # 六、下一步
    nx = spec.get('s6_next', {})
    items = ''.join(f'<div class="q"><b>{rich(i.get("title"))}</b><span>{rich(i.get("body"))}</span></div>'
                    for i in nx.get('items', []))
    P.append('<div class="card"><h2>六、下一步</h2>' + items
             + (f'<p class="note">{rich(nx.get("note"))}</p>' if nx.get('note') else '') + '</div>')

    P.append('<p class="foot">這份健檢僅依據你提供的資料產出，不構成錄用承諾或薪資保證。<br>'
             '你的資料不會對外公開，僅供我們內部顧問查閱。<br>'
             '由阿福（STEP1NE AI 履歷健檢助理）為你整理。</p></div>')
    return '\n'.join(P)


def to_pdf(html_path, pdf_path):
    """headless Chrome 轉 PDF。成功回 True。

    ⚠️ 路徑一定要轉成絕對路徑再組 file:// URL。
       相對路徑會變成 file://checkup_reports/x.html——瀏覽器會把 checkup_reports
       當成**主機名稱**，載不到檔案，然後**安靜地印出一張空白 PDF**（有大小、
       有頁數，只是沒有內容）。2026-08-10 實際踩到，是驗證時抽文字抽到空的才發現。
    """
    if not os.path.exists(CHROME):
        print('  ⚠️ 找不到 Chrome，跳過 PDF', file=sys.stderr)
        return False
    html_path = os.path.abspath(html_path)
    pdf_path = os.path.abspath(pdf_path)
    subprocess.run(
        [CHROME, '--headless', '--disable-gpu', '--no-pdf-header-footer',
         f'--print-to-pdf={pdf_path}', 'file://' + urllib.parse.quote(html_path)],
        capture_output=True, text=True, timeout=120)
    if not (os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0):
        return False
    # 空白 PDF 也有大小，所以再抽一次文字確認真的印到內容。
    # 印出一份空白報告寄給本人，比印不出來更糟——沒有人會發現。
    try:
        t = subprocess.run(['pdftotext', pdf_path, '-'], capture_output=True,
                           text=True, timeout=60).stdout
        if len(t.strip()) < 200:
            print('  ⚠️ PDF 抽不到內文（很可能是空白頁），請檢查 HTML 路徑', file=sys.stderr)
            return False
    except Exception:
        pass    # 沒有 pdftotext 就跳過這一步，不要因為檢查工具缺席就判定失敗
    return True


# ── 出口：匿名人選卡（餵給 candidate-led-bd）────────────────
def anon_card(spec):
    """產出不含姓名、現職公司、學校的人選卡。

    ⚠️ 介面已經定好，但**去識別化不是這支腳本猜出來的**：
       稿子必須在 anon_card.strip 裡逐字列出要拿掉的識別字（人名、公司、學校、
       產品名），這支只負責檢查那些字真的沒有出現在卡片裡。
       猜不出來就寧可擋下來——洩漏一次就沒有第二次機會。
    """
    c = dict(spec.get('anon_card') or {})
    if not c:
        return None, ['稿子裡沒有 anon_card 區塊']
    strip = c.pop('strip', [])
    blob = json.dumps(c, ensure_ascii=False)
    leaks = [s for s in strip if s and s in blob]
    for k in ('name', 'company', 'school', 'email', 'phone'):
        if c.get(k):
            leaks.append(f'匿名卡不該有 {k} 欄位')
    return c, leaks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('spec')
    ap.add_argument('--out', help='輸出路徑（不含副檔名）')
    ap.add_argument('--no-pdf', action='store_true')
    ap.add_argument('--anon-card', action='store_true')
    ap.add_argument('--no-strict', action='store_true', help='關掉薪資數字把關（不建議）')
    a = ap.parse_args()

    spec = json.load(open(a.spec, encoding='utf-8'))

    if a.anon_card:
        card, leaks = anon_card(spec)
        if leaks:
            print('❌ 匿名人選卡有問題：', file=sys.stderr)
            for l in leaks:
                print('   ' + l, file=sys.stderr)
            sys.exit(2)
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return

    doc = render(spec)

    hits = salary_hits(re.sub('<[^>]+>', '', doc), spec.get('quoted_facts', []))
    if hits and not a.no_strict:
        print('❌ 報告裡出現薪資形狀的數字，拒絕產出：', file=sys.stderr)
        for h in hits:
            print('   ' + h, file=sys.stderr)
        print('   （本人親口講過的數字請放進 quoted_facts 逐句列出）', file=sys.stderr)
        sys.exit(2)

    out = a.out or os.path.join(tempfile.gettempdir(), 'checkup_report')
    out = os.path.expanduser(out)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    hp = out + '.html'
    open(hp, 'w', encoding='utf-8').write(doc)
    print(f'  ✅ HTML {hp}　{os.path.getsize(hp):,} bytes')
    if not a.no_pdf:
        pp = out + '.pdf'
        if to_pdf(hp, pp):
            print(f'  ✅ PDF  {pp}　{os.path.getsize(pp):,} bytes')
        else:
            print('  ⚠️ PDF 產出失敗', file=sys.stderr)


if __name__ == '__main__':
    main()
