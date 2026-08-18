# ── 職缺頁內文產生器 ──
# 2026-08-06 建。原本每一頁都是手刻的 HTML，新增一個職缺要動六個地方
# （頁面、列表卡、sitemap、apply/jobs.json、D1、部署），改一次 JD 也一樣。
# 這支把「內文」從資料生成，樣式沿用現有職缺頁的 class，不另開一套。

BOX = 'padding:18px 20px;background:#fff;border:1px solid #e6dfd1;border-radius:14px;'
CARD = 'padding:24px 22px;border-radius:14px;background:#fff;border:1px solid #e6dfd1;'
LBL = "font-family:'Space Mono',monospace;font-size:11px;letter-spacing:1.4px;color:#a67c3d;margin-bottom:8px;"


def _spec(rows):
    out = []
    for i, r in enumerate(rows):
        k, v = r[0], r[1]
        sub = r[2] if len(r) > 2 else ''
        subhtml = (f'<span style="display:block;font-weight:400;font-size:14px;'
                   f'color:#55585f;margin-top:4px;">{sub}</span>') if sub else ''
        # 第一列預設反白。其餘要反白就在該列尾端多給一個 true——
        # 直播主那頁把「抽成／分潤」「應徵條件」也反白，是刻意要人看見的風險項，
        # 產生器只認第一列的話，重產一次就會把那個強調洗掉（2026-08-12）。
        hl = i == 0 or (len(r) > 3 and r[3])
        cls = 'spec-row highlight' if hl else 'spec-row'
        out.append(f'<div class="{cls}"><dt>{k}</dt><dd>{v}{subhtml}</dd></div>')
    return f'<dl class="spec">{"".join(out)}</dl>'


def build_body(j):
    """j 是一份職缺資料 dict。缺的區塊自動略過，不要為了填滿而編。"""
    E = lambda x: (x or '')
    tags = ''.join(f'<span class="tag">{t}</span>' for t in j.get('tags', []))
    apply_url = (f"/apply/?job={j['slug']}&title={j['title_enc']}"
                 f"&utm_source=website&utm_medium=job_page&utm_campaign={j['slug']}")

    duties = ''
    for grp in j.get('duties', []):
        li = ''.join(f'<li>{x}</li>' for x in grp.get('items', []))
        duties += f'<h3>{grp["h"]}</h3><ul class="duties">{li}</ul>'

    def req_card(label, items):
        li = ''.join(f'<li>{x}</li>' for x in items)
        return (f'<div style="{CARD}"><div style="{LBL}">{label}</div>'
                f'<ul class="duties">{li}</ul></div>')
    reqs = ''
    if j.get('must'):
        reqs += req_card('必要條件', j['must'])
    if j.get('plus'):
        reqs += req_card('加分條件', j['plus'])

    why = ''.join(
        f'<div style="{BOX}"><h3 style="font-size:16.5px;font-weight:700;margin:0 0 4px;">{w["h"]}</h3>'
        f'<p style="color:#6b6e77;font-size:14.5px;margin:0;">{w["p"]}</p></div>'
        for w in j.get('why', []))

    steps = ''.join(
        f'<div style="{BOX}"><div style="font-family:\'Space Mono\',monospace;color:#a67c3d;'
        f'font-size:13px;margin-bottom:6px;">{i:02d}</div>'
        f'<div style="font-weight:700;">{s}</div></div>'
        for i, s in enumerate(j.get('steps', ['填寫應徵表單', '顧問保密初談', '安排面試、談定條件']), 1))

    faq = ''.join(
        f'<details style="border:1px solid #e6dfd1;border-radius:14px;background:#fff;overflow:hidden;">'
        f'<summary style="padding:18px 20px;display:flex;align-items:center;'
        f'justify-content:space-between;gap:16px;font-weight:600;font-size:16px;">'
        f'<span>{q}</span><span class="faq-icon" style="font-family:\'Space Mono\',monospace;'
        f'font-size:20px;color:#a67c3d;">+</span></summary>'
        f'<p style="margin:0;padding:0 20px 18px;color:#55585f;font-size:14.5px;">{a}</p></details>'
        for q, a in j.get('faq', []))

    sec = lambda t, b: f'<h2>{t}</h2>{b}' if b and b.strip() else ''
    return f"""<main>
<section style="padding:36px 0 8px;"><div class="wrap">
<a href="/jobs/" style="font-size:14px;color:#8a8d95;">← 返回職缺專區</a>
<div style="display:flex;flex-wrap:wrap;gap:8px;margin:16px 0;">{tags}</div>
<h1 style="font-family:'Noto Serif TC',serif;font-weight:700;font-size:clamp(27px,4.5vw,40px);
line-height:1.3;margin:0 0 16px;color:#1c1f26;">{E(j.get('title'))}<span class="h1-sub">{E(j.get('subtitle'))}</span></h1>
<p style="font-size:16.5px;color:#3a3d44;margin:0;">{E(j.get('intro'))}</p>
</div></section>

<section style="padding:26px 0;"><div class="wrap">
<h2>職缺條件一覽</h2>{_spec(j.get('spec', []))}
<p style="font-size:13.5px;color:#8a8d95;margin-top:10px;">※ 上述條件為目前規劃，實際待遇與工作條件依面試後雙方確認為準。</p>
{sec('工作內容', duties)}
{sec('應徵條件', f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;">{reqs}</div>' if reqs else '')}
{sec('為什麼選擇這個機會', f'<div style="display:flex;flex-direction:column;gap:12px;">{why}</div>' if why else '')}
{sec('應徵流程', f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;">{steps}</div>')}
<h2>立即應徵</h2>
<p style="color:#55585f;font-size:15px;margin:0 0 22px;">線上填寫約 2 分鐘，Step1ne 顧問會盡快與你聯繫。</p>
<a href="{apply_url}" style="display:inline-flex;align-items:center;gap:10px;background:#a67c3d;
color:#fff;font-weight:700;font-size:16px;padding:15px 34px;border-radius:999px;">填寫應徵表單</a>
<span style="margin-left:14px;"><a href="https://lin.ee/XcSWPzM" target="_blank" rel="noopener"
style="color:#a67c3d;font-weight:600;">或用 LINE 直接洽詢 →</a></span>
{sec('常見問題', f'<div style="display:flex;flex-direction:column;gap:12px;">{faq}</div>' if faq else '')}
</div></section>"""
# ⚠️ 這裡不要收 </main>——<main> 是 chrome_top 開的，chrome_bot 第一行就會收。
# 兩邊都收會讓每一頁多出一個 </main>（2026-08-12 重產職缺頁時比對發現）。
