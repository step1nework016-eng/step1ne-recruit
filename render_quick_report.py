#!/usr/bin/env python3
"""render_quick_report.py — 快速應徵版初篩報告（給顧問看的 HTML）。2026-10-21 Jacky：原本報告太流水帳，要表格、要區塊。
資料全部來自 reports.content_json（阿財產的結構化報告）＋ applications 的快速應徵表單（note）。
用法：python3 render_quick_report.py <application_id> [輸出檔]"""
import html, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consultant_ops as c

E = lambda x: html.escape(str(x if x is not None else ''))
BADGE = {'符合': ('ok', '符合'), '部分符合': ('wa', '部分符合'), '缺': ('bad', '不符'), '未確認': ('mu', '未確認'), '待確認': ('mu', '待確認')}


def quick_fields(note):
    out = {}
    for line in str(note or '').splitlines():
        if '：' in line and not line.startswith('【'):
            k, v = line.split('：', 1); out[k.strip()] = v.strip()
    return out


def render(app_id):
    a = c.rows("SELECT name, job_title, job_slug, note, created_at, interview_started_at, interview_ended_at, resume_file_id, resume_url FROM applications WHERE id=?", [app_id])[0]
    r = c.rows("SELECT content_json, created_at FROM reports WHERE application_id=? ORDER BY created_at LIMIT 1", [app_id])[0]
    d = json.loads(r['content_json'] or '{}')
    q = quick_fields(a['note'])
    has_resume = bool(a['resume_file_id'] or (a['resume_url'] or '').strip())
    has_test = bool(c.rows("SELECT 1 FROM assessments WHERE application_id=? LIMIT 1", [app_id]))
    mins = ''
    try:
        import datetime as dt
        f = '%Y-%m-%d %H:%M:%S'
        mins = int((dt.datetime.strptime(a['interview_ended_at'], f) - dt.datetime.strptime(a['interview_started_at'], f)).total_seconds() // 60)
    except Exception:
        pass
    route = d.get('route') or {}
    mot = d.get('motivation') or {}
    jm = (d.get('job_match') or {}).get('items') or []
    sk = d.get('skill_ladder') or {}

    def pill(v):
        cls, t = BADGE.get(v, ('mu', v or '—')); return f'<span class="pill {cls}">{E(t)}</span>'

    basics = [('最高學歷', q.get('最高學歷') or '（表單未填）'), ('學校與科系', q.get('學校與科系') or q.get('科系') or '—'),
              ('相關經驗', q.get('相關經驗')), ('會用的軟體', q.get('會用的軟體')), ('相關證照', q.get('相關證照')),
              ('機車駕照', q.get('機車駕照')), ('期望工作城市', q.get('期望工作城市') or q.get('居住縣市')),
              ('期望待遇', (mot.get('salary_gap') or '').split('，')[0] or '—'), ('可到職', mot.get('notice_period') or '—')]
    rows_basic = ''.join(f'<tr><th>{E(k)}</th><td>{E(v or "—")}</td></tr>' for k, v in basics)
    rows_match = ''.join(f'<tr><td>{E(i.get("requirement"))}</td><td class="vd">{pill(i.get("verdict"))}</td><td>{E(i.get("evidence"))}</td></tr>' for i in jm)
    exp_lv = sk.get('expected') or 0
    rows_ladder = ''.join(
        f'<tr><td>{E(t.get("topic"))}</td><td><div class="lv">' + ''.join(f'<i class="{"on" if (t.get("level") or 0) >= n else ""}{" exp" if n == exp_lv else ""}"></i>' for n in range(1, 6))
        + f'</div></td><td>{E(t.get("level_name") or ("沒問到" if not t.get("level") else ""))}</td></tr>' for t in (sk.get('topics') or []))
    follow = list(d.get('consultant_followups') or [])
    for x in (d.get('not_covered') or []):   # 沒問到的項目補進來，但跟上面重複的不再列一次
        it = str(x.get('item') or '').strip()
        bi = lambda t: {t[i:i + 2] for i in range(len(t) - 1)}
        sim = lambda a_, b_: len(bi(a_) & bi(b_)) / max(1, min(len(bi(a_)), len(bi(b_))))
        if it and not any(it in f or f.split('（')[0] in it or sim(it, f.split('（')[0]) >= 0.5 for f in follow):
            follow.append(f'{it}（{x.get("kind")}）')
    fc = d.get('for_client') or {}
    dirs = d.get('career_directions') or []
    vcls = 'ok' if '推' in str(d.get('verdict')) and '不' not in str(d.get('verdict')) else ('bad' if '不符' in str(d.get('verdict')) else 'wa')
    css = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'quick_report.css'), encoding='utf-8').read() if False else CSS
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>初篩報告 {E(a["name"])}</title><style>{css}</style></head><body><div class="w">
<header><div><span class="tag">快速應徵</span><h1>{E(a["name"])}</h1><p class="sub">{E(a["job_title"] or a["job_slug"])}｜面談 {E((a["interview_started_at"] or "")[:16])}{f"｜約 {mins} 分鐘" if mins != "" else ""}</p></div>
<div class="status"><span class="pill {'ok' if has_resume else 'bad'}">履歷 {'已交' if has_resume else '未交'}</span><span class="pill {'ok' if has_test else 'mu'}">測驗 {'已做' if has_test else '未做'}</span></div></header>

<section class="hero {vcls}"><div class="v">{E(d.get("verdict"))}</div><p class="one">{E(d.get("one_liner"))}</p>
<div class="two"><div><b>最大亮點</b><p>{E(d.get("top_selling_point"))}</p></div><div><b>最大風險</b><p>{E(d.get("top_risk"))}</p></div></div>
<p class="route">系統分流：{E(route.get("label"))}（{E(route.get("reason"))}）</p></section>

<div class="grid">
<section class="card"><h2>快速應徵資料</h2><table class="kv">{rows_basic}</table></section>
<section class="card"><h2>顧問要補問</h2><ol class="chk">{"".join(f"<li>{E(x)}</li>" for x in follow) or "<li>無</li>"}</ol></section>
</div>

<section class="card"><h2>職缺條件逐條比對</h2><div class="tw"><table class="tb"><tr><th>職缺條件</th><th>判定</th><th>根據</th></tr>{rows_match}</table></div></section>

<div class="grid">
<section class="card"><h2>專業程度（1～5 級，框線＝職缺期待）</h2><table class="tb">{rows_ladder}</table></section>
<section class="card"><h2>動機與意願</h2><table class="kv"><tr><th>想做這份工作的原因</th><td>{E(mot.get("why_this_role"))}</td></tr>
<tr><th>職涯方向</th><td>{E((d.get("motive") or {}).get("direction"))}</td></tr><tr><th>猶豫點</th><td>{E(mot.get("blockers") or (d.get("move_risk") or {}).get("hesitation") or "—")}</td></tr>
<tr><th>薪資</th><td>{E(mot.get("salary_gap"))}</td></tr></table></section>
</div>

<div class="grid">
<section class="card"><h2>推給客戶的理由</h2><ul>{"".join(f"<li>{E(x)}</li>" for x in fc.get("reasons") or [])}</ul></section>
<section class="card"><h2>推之前要先講的風險</h2><ul>{"".join(f"<li>{E(x)}</li>" for x in fc.get("risks_to_disclose") or [])}</ul></section>
</div>

<section class="card"><h2>適合的職務方向</h2><div class="dirs">{"".join(f"<div class='dir'><h3>{E(x.get('direction'))}</h3><p><b>為什麼</b>{E(x.get('why'))}</p><p><b>要注意</b>{E(x.get('watch_out'))}</p></div>" for x in dirs) or "<p>無</p>"}</div></section>

<section class="card"><h2>面談觀察</h2><p>{E((d.get("observations") or {}).get("communication_style"))}</p><p>{E((d.get("observations") or {}).get("engagement"))}</p></section>
<p class="foot">資料來源：快速應徵表單＋AI 阿財面談（{E(r["created_at"][:16])} 產出）。判定標準：每項條件拆小項，全部有證據＝符合、部分有＝部分符合、明確沒有＝不符、沒問到＝未確認。</p>
</div></body></html>'''


CSS = '''
:root{--bg:#F3F6F7;--s:#fff;--ink:#13232B;--mu:#5C6E78;--ln:#D7E0E4;--ac:#0F6B78;--ok:#1E7A46;--okb:#E3F3EA;--wa:#8A5300;--wab:#FFF2DB;--bad:#B3261E;--badb:#FDE8E6;--mub:#EEF2F4}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0F181C;--s:#16232A;--ink:#E4ECEF;--mu:#93A7B0;--ln:#2A3A42;--ac:#5BC0CC;--ok:#6FD39A;--okb:#163126;--wa:#F2B35B;--wab:#33280F;--bad:#FF8A80;--badb:#3A1A18;--mub:#22313A}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,"Noto Sans TC",sans-serif;line-height:1.6}
.w{max-width:1040px;margin:0 auto;padding:20px 16px 48px}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;margin-bottom:14px}
h1{font-size:24px;margin:4px 0 0}.sub{color:var(--mu);margin:2px 0 0;font-size:14px}
.tag{display:inline-block;font-size:12px;font-weight:700;color:var(--ac);background:var(--mub);border-radius:99px;padding:2px 10px}
.status{display:flex;gap:6px}
.pill{display:inline-block;font-size:12.5px;font-weight:700;border-radius:99px;padding:2px 10px;white-space:nowrap}
.pill.ok{background:var(--okb);color:var(--ok)}.pill.wa{background:var(--wab);color:var(--wa)}.pill.bad{background:var(--badb);color:var(--bad)}.pill.mu{background:var(--mub);color:var(--mu)}
.hero{border-radius:14px;padding:16px 18px;margin-bottom:14px;border:1px solid var(--ln);background:var(--s);border-left:6px solid var(--wa)}
.hero.ok{border-left-color:var(--ok)}.hero.bad{border-left-color:var(--bad)}
.hero .v{font-size:20px;font-weight:800}.hero .one{margin:4px 0 10px;font-size:15px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}.two b{font-size:13px;color:var(--mu)}.two p{margin:2px 0 0;font-size:14px}
.route{margin:10px 0 0;font-size:13px;color:var(--mu)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.card{background:var(--s);border:1px solid var(--ln);border-radius:14px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:15px;margin:0 0 10px;color:var(--ac)}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
.tb th,.tb td{border-bottom:1px solid var(--ln);padding:8px 8px;text-align:left;vertical-align:top}.tb th{font-size:12.5px;color:var(--mu);font-weight:600}
.tb td.vd{white-space:nowrap}
.kv th{width:34%;text-align:left;font-size:13px;color:var(--mu);font-weight:600;padding:6px 8px 6px 0;vertical-align:top}.kv td{padding:6px 0;border-bottom:1px solid var(--ln)}
.lv{display:flex;gap:4px}.lv i{width:22px;height:10px;border-radius:3px;background:var(--mub);display:block}.lv i.on{background:var(--ac)}.lv i.exp{outline:2px solid var(--wa);outline-offset:1px}
ol.chk,ul{margin:0;padding-left:20px;font-size:14px}ol.chk li,ul li{margin:4px 0}
.foot{font-size:12.5px;color:var(--mu)}
.dirs{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.dir{border:1px solid var(--ln);border-radius:10px;padding:10px 12px}.dir h3{font-size:14.5px;margin:0 0 6px}
.dir p{margin:4px 0;font-size:13.5px}.dir b{display:block;font-size:12px;color:var(--mu)}
@media (max-width:720px){.grid,.two{grid-template-columns:1fr}}
@media print{body{background:#fff}.card,.hero{break-inside:avoid}}
'''

if __name__ == '__main__':
    aid = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else f'/Users/user/下載項目/初篩報告_快速應徵版_{aid[:8]}.html'
    open(out, 'w', encoding='utf-8').write(render(aid)); print(out)
