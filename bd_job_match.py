#!/usr/bin/env python3
"""bd_job_match.py — 客戶卡片「從現有人選配對」＋「針對這個職缺的匿名摘要」（2026-10-14 Jacky）

兩種 ai_jobs（由 ai_worker.py 呼叫）：
  bd_job_match  拿客戶的一個職缺，到現有人選（applications）裡找最適合的 5 位，列出符合與落差
  bd_job_brief  替勾選的人選各做一份「針對這個職缺」的一頁匿名摘要 PDF，存回後台，
                並放進這家客戶的匿名履歷清單（bd_contract_files），之後在「寄公司介紹信」分頁勾選附上

規則（寫死在程式，不靠提示詞自律）：
- 配對只看「有跟我們談過的人選」（applications）；結果裡有真名，是內部畫面，不會寄給客戶
- 匿名摘要：不寫姓名、公司全名、學校名、電話、Email、地址、薪資、年齡；寫了就視為失敗重做
- 資料沒寫的不寫，不編造；落差用「需確認」這類中性字眼，不貶低人選
- 匿名摘要只放進「這家客戶」的匿名履歷清單，不會自動寄出（寄信仍要 TG 核准）
"""
import base64
import datetime
import html as H
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
import d1_http as D  # noqa: E402

BASE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def _token():
    for f in ('~/.config/workflow-os/tokens.env', '~/.config/workflow-os/recruit.env'):
        try:
            for line in open(os.path.expanduser(f), encoding='utf-8'):
                if line.startswith('RECRUIT_ADMIN_TOKEN='):
                    return line.strip().split('=', 1)[1].strip().strip("'\"")
        except Exception:
            continue
    return None


def _post(path, body):
    tok = _token()
    if not tok:
        raise RuntimeError('這台機器沒有後台金鑰')
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={'authorization': 'Bearer ' + tok, 'content-type': 'application/json', 'user-agent': 'bd_job_match/1.0'}, method='POST')
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def _json(out):
    out = (out or '').strip()
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError('AI 回覆裡沒有 JSON：' + out[:120])
    return json.loads(out[i:j + 1])


def ensure():
    D.query("""CREATE TABLE IF NOT EXISTS bd_job_match_runs (id TEXT PRIMARY KEY, company TEXT, job_ref TEXT, job_title TEXT, job_json TEXT,
        status TEXT DEFAULT 'queued', result_json TEXT, error TEXT, created_by TEXT, created_at TEXT, done_at TEXT)""")
    D.query("""CREATE TABLE IF NOT EXISTS bd_job_briefs (id TEXT PRIMARY KEY, run_id TEXT, company TEXT, application_id TEXT, code TEXT,
        status TEXT DEFAULT 'queued', file_id TEXT, filename TEXT, contract_file_id TEXT, error TEXT, created_at TEXT, done_at TEXT)""")


def _job_text(job):
    return '\n'.join(x for x in [
        f"職缺：{job.get('title') or ''}", f"地點：{job.get('locations') or ''}", f"薪資：{job.get('pay_text') or ''}",
        f"工作內容：\n{job.get('main_duties') or ''}", f"條件需求：\n{job.get('required_conditions') or ''}"] if x.strip())[:6000]


# ───────────────── 配對 ─────────────────

def run_match(payload, run_claude):
    run_id = payload['run_id']
    ensure()
    D.query(f"UPDATE bd_job_match_runs SET status='working', error=NULL WHERE id={q(run_id)}")
    try:
        job = payload['job']
        jt = _job_text(job)
        kw = _json(run_claude(f"""下面是客戶的一個職缺。請列出用來在人選履歷文字裡「搜尋」的關鍵字。
{jt}

只輸出 JSON：{{"must":["必備條件的關鍵字，6～10 個，含同義寫法（例：T100、鼎新、ERP）"],"nice":["加分條件的關鍵字，4～8 個"]}}
關鍵字要短（1～6 字或常見縮寫），履歷裡真的會出現的寫法。""", want_json=True, timeout=300))
        must = [str(x).strip() for x in (kw.get('must') or []) if str(x).strip()][:12]
        nice = [str(x).strip() for x in (kw.get('nice') or []) if str(x).strip()][:10]
        rows = D.query(
            "SELECT a.id, a.seq_no, a.name, a.job_title, a.status, COALESCE(a.expected_salary,'') es, COALESCE(a.available_date,'') av, "
            "COALESCE(substr(f.text_content,1,6000), substr(a.resume_url_text,1,6000), '') rt "
            "FROM applications a LEFT JOIN files f ON f.id=a.resume_file_id "
            "WHERE a.superseded_by IS NULL AND COALESCE(a.status,'') NOT IN ('closed','rejected','duplicate') "
            "ORDER BY a.created_at DESC LIMIT 500")['results']
        reps = {}
        for r in D.query(
                "SELECT application_id, substr(json_extract(content_json,'$.one_liner'),1,200) ol, substr(json_extract(content_json,'$.top_selling_point'),1,160) ts, "
                "substr(json_extract(content_json,'$.top_risk'),1,160) tr FROM reports WHERE content_json IS NOT NULL ORDER BY created_at")['results']:
            reps[r['application_id']] = r
        scored = []
        for r in rows:
            txt = (r.get('rt') or '') + ' ' + (r.get('job_title') or '') + ' ' + ' '.join(str(v or '') for v in (reps.get(r['id']) or {}).values())
            low = txt.lower()
            s = sum(3 for k in must if k.lower() in low) + sum(1 for k in nice if k.lower() in low)
            if s > 0:
                scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        pool = [r for _, r in scored[:20]]
        if not pool:
            D.query(f"UPDATE bd_job_match_runs SET status='done', result_json={q(json.dumps({'picks': [], 'note': '現有人選裡沒有關鍵字對得上的人', 'keywords': {'must': must, 'nice': nice}}, ensure_ascii=False))}, done_at=datetime('now','+8 hours') WHERE id={q(run_id)}")
            return 'bd_job_match: 0 位'
        cards = []
        for i, r in enumerate(pool):
            rp = reps.get(r['id']) or {}
            cards.append(f"[{i}] S-{int(r['seq_no'] or 0):03d}｜應徵：{r.get('job_title') or ''}｜期望：{r.get('es') or '未填'}｜到職：{r.get('av') or '未填'}\n"
                         f"一句話：{rp.get('ol') or '無'}｜亮點：{rp.get('ts') or '無'}｜風險：{rp.get('tr') or '無'}\n履歷：{(r.get('rt') or '')[:1500]}")
        rk = _json(run_claude(f"""你是獵頭顧問。下面是客戶的職缺，以及我們現有人選的資料。請挑出最適合這個職缺的前 5 位（最多 5 位；真的沒有適合的就少挑，不要湊數）。

{jt}

=== 人選 ===
{chr(10).join(cards)}

規則：
- 只依資料裡有的內容判斷，沒寫的就是不知道，不准補猜。
- 對每位列出「符合」（對上職缺必備或加分的具體證據）與「落差」（職缺要求但資料看不出來、或不符的地方）。落差要具體，例如「履歷沒寫 MRP」。
- fit：高＝必備條件大致都對上；中＝缺一兩項要確認；低＝落差大。
- note：一句話給顧問的建議（先問什麼、要不要推）。
只輸出 JSON：{{"picks":[{{"idx":人選編號數字,"fit":"高|中|低","matches":["…","…"],"gaps":["…","…"],"note":"…"}}]}}（依適合度由高到低）""", want_json=True, timeout=600))
        picks = []
        for p in (rk.get('picks') or [])[:5]:
            try:
                r = pool[int(p.get('idx'))]
            except Exception:
                continue
            picks.append({'application_id': r['id'], 'code': f"S-{int(r['seq_no'] or 0):03d}", 'name': r['name'], 'applied_for': r.get('job_title') or '',
                          'status': r.get('status') or '', 'expected': r.get('es') or '', 'available': r.get('av') or '',
                          'fit': p.get('fit') or '中', 'matches': [str(x)[:120] for x in (p.get('matches') or [])][:6],
                          'gaps': [str(x)[:120] for x in (p.get('gaps') or [])][:6], 'note': str(p.get('note') or '')[:200]})
        res = {'picks': picks, 'keywords': {'must': must, 'nice': nice}, 'pool_size': len(rows), 'matched_pool': len(scored)}
        D.query(f"UPDATE bd_job_match_runs SET status='done', result_json={q(json.dumps(res, ensure_ascii=False))}, error=NULL, done_at=datetime('now','+8 hours') WHERE id={q(run_id)}")
        return f'bd_job_match {run_id[:8]}: {len(picks)} 位'
    except Exception as e:  # noqa: BLE001
        D.query(f"UPDATE bd_job_match_runs SET status='error', error={q(str(e)[:300])}, done_at=datetime('now','+8 hours') WHERE id={q(run_id)}")
        raise


# ───────────────── 匿名摘要 ─────────────────

BRIEF_RULES = """這是要給「還沒合作的客戶」看的匿名人選摘要，規則（違反就整份重做）：
- 全文不准出現人選的真實姓名（中文、英文、暱稱、縮寫都不行），指稱他一律寫「人選」。
- 待過的公司一律不寫公司名，改寫成「產業＋公司類型／規模」，例如「上市半導體封測廠」「連鎖餐飲集團」「籌備公開發行的科技業公司」。客戶、品牌、合作夥伴名稱也改成泛稱。
- 不寫學校名稱（只寫學歷層級與科系）、不寫電話、Email、LINE、地址、個人網站、LinkedIn。
- 不寫任何薪資數字（期望或過去都不寫）、不寫年齡。
- 履歷沒寫的就不寫，不准編造、不准推測年資總和。
- 不准出現「阿財」。不寫婚育、宗教、政黨、國籍等就業服務法第 5 條不能寫的內容。
- 與職缺的對照（fit）：只列「符合」和「部分符合」、以及中性的「需確認」；每一條都要有履歷依據，不貶低人選。
"""

BRIEF_SCHEMA = """{
 "direction": "推薦方向，例：財務經理（集團資金管理、銀行融資）",
 "tagline": "一行：年資輪廓與現況（不寫年齡、不寫公司名），例：財務與經營管理約 7 年以上｜現任籌備公開發行之科技業公司財務長",
 "summary": "2～3 句，直接針對這個職缺講人選的重點與對上的地方",
 "fit": [{"req":"職缺要求（短）","level":"符合|部分符合|需確認","evidence":"履歷依據（短）"}],
 "highlights": ["3～5 條與職缺最相關的具體經歷或成果"],
 "experience": [{"period":"2016.11 ～ 2023","role":"職稱｜產業與規模（不寫公司名）","points":["1～3 條，與職缺相關的優先"]}],
 "skills": ["與職缺相關的專長／系統／證照"],
 "education": "學歷層級與科系（不寫學校名）",
 "languages": "語言能力（有寫才寫）"
}"""


def _employers(src):
    pat = re.compile(r'[一-鿿A-Za-z0-9·&.\-（）() ]{2,24}?(?:股份有限公司|有限公司|Co\.,? ?Ltd\.?|Inc\.?|Corp\.?|Corporation|Group|集團|Limited|LLC|GmbH)')
    out = set()
    for m in pat.findall(src or ''):
        n = re.sub(r'(股份有限公司|有限公司|Co\.,? ?Ltd\.?|Inc\.?|Corp\.?|Corporation|Limited|LLC|GmbH)$', '', m.strip(' （）()')).strip()
        if len(n) >= 3 and n not in ('Group', '集團'):
            out.add(n)
    return out


def _leaks(brief_json, name, src):
    body = json.dumps(brief_json, ensure_ascii=False)
    bad = []
    for part in {name, (name or '')[1:], (name or '')[:1] + '先生', (name or '')[:1] + '小姐'}:
        if part and len(part) >= 2 and part in body and part not in ('先生', '小姐'):
            bad.append(part)
    for e in _employers(src):
        if e and e in body:
            bad.append(e)
    if re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', body) or re.search(r'09\d{2}[- ]?\d{3}[- ]?\d{3}', body):
        bad.append('Email 或電話')
    if '阿財' in body:
        bad.append('阿財')
    return bad


def _render(code, b):
    e = H.escape
    fit = ''.join(f"<tr><td>{e(x.get('req',''))}</td><td class='lv {'ok' if x.get('level')=='符合' else 'part'}'>{e(x.get('level',''))}</td><td>{e(x.get('evidence',''))}</td></tr>" for x in (b.get('fit') or []))
    exp = ''.join(f"<div class='row'><div class='d'>{e(x.get('period',''))}</div><div><b>{e(x.get('role',''))}</b><ul>{''.join('<li>'+e(p)+'</li>' for p in (x.get('points') or []))}</ul></div></div>" for x in (b.get('experience') or []))
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><style>
@page{{size:A4;margin:14mm}}
body{{font:12.5px/1.6 "Noto Sans CJK TC","PingFang TC","Microsoft JhengHei",sans-serif;color:#14232e;margin:0}}
.h{{border-bottom:3px solid #0b5f73;padding-bottom:6px;margin-bottom:10px}}.h b{{font-size:11px;letter-spacing:.12em;color:#0b5f73}}
h1{{font-size:19px;margin:3px 0 2px}}.sub{{color:#586a77;font-size:12px}}
h2{{font-size:13.5px;color:#0b5f73;margin:12px 0 4px;border-left:4px solid #0b5f73;padding-left:7px}}
.sum{{background:#eef6f8;border-radius:5px;padding:8px 11px}}
table{{width:100%;border-collapse:collapse;font-size:11.8px}}td{{padding:4px 6px;border-top:1px solid #d9e2e8;vertical-align:top}}
.lv{{white-space:nowrap;font-weight:700;width:62px}}.ok{{color:#1d5a33}}.part{{color:#7a4b00}}
ul{{margin:2px 0;padding-left:17px}}li{{margin:2px 0}}.row{{display:flex;gap:9px;margin:5px 0}}.d{{min-width:105px;color:#586a77;font-size:11.5px}}
.f{{margin-top:14px;color:#586a77;font-size:10.5px;border-top:1px solid #d9e2e8;padding-top:6px}}
</style></head><body>
<div class="h"><b>STEP1NE　人選推薦</b><h1>人選代號 {e(code)}｜{e(b.get('direction',''))}</h1><div class="sub">{e(b.get('tagline',''))}</div></div>
<div class="sum">{e(b.get('summary',''))}</div>
<h2>與職缺的對照</h2><table>{fit}</table>
<h2>重點經歷與成果</h2><ul>{''.join('<li>'+e(x)+'</li>' for x in (b.get('highlights') or []))}</ul>
<h2>工作經歷</h2>{exp}
<h2>專長</h2><ul>{''.join('<li>'+e(x)+'</li>' for x in (b.get('skills') or []))}</ul>
<h2>基本資料</h2><div class="row"><div class="d">學歷</div><div>{e(b.get('education',''))}</div></div>{('<div class="row"><div class="d">語言</div><div>'+e(b.get('languages',''))+'</div></div>') if b.get('languages') else ''}
<div class="f">德仁管理顧問有限公司｜STEP1NE　02-6605-7111　official@step1ne.com<br>本摘要為匿名版本，依職缺整理，更多細節請洽顧問。</div>
</body></html>"""


def run_brief(payload, run_claude):
    bid = payload['brief_id']
    ensure()
    D.query(f"UPDATE bd_job_briefs SET status='working', error=NULL WHERE id={q(bid)}")
    try:
        if not shutil.which('weasyprint'):
            raise RuntimeError('這台機器沒有 weasyprint，做不出 PDF')
        a = D.query("SELECT a.id, a.seq_no, a.name FROM applications a WHERE a.id=" + q(payload['application_id']))['results']
        if not a:
            raise RuntimeError('找不到這位人選')
        a = a[0]
        code = f"S-{int(a['seq_no'] or 0):03d}"
        job = payload['job']
        # 2026-10-14 Jacky：匿名履歷只有一種標準版（make_anon_cv.py 的 anon 模式：姓＋先生／小姐、
        # 公司寫產業＋規模、不寫學校、不放「與職缺對照／需確認」這類內部評估）。以前這裡自己做了一版
        # 「針對職缺對照」，會把人選的弱項寫給還沒簽約的客戶看，作廢。
        tmp = tempfile.mkdtemp(prefix='brief_')
        try:
            pp = os.path.join(tmp, 'b.pdf')
            r = subprocess.run([sys.executable, os.path.join(HERE, 'make_anon_cv.py'), a['id'], '--mode', 'anon', '--out', pp],
                               capture_output=True, text=True, timeout=900)
            if r.returncode != 0 or not os.path.exists(pp):
                raise RuntimeError('匿名履歷產生失敗：' + ((r.stdout or '') + (r.stderr or ''))[-200:])
            data = open(pp, 'rb').read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        job_short = re.sub(r'[\\/:*?"<>|\s]+', '', str(job.get('title') or '職缺'))[:20]
        fname = f"匿名人選推薦_{code}_{job_short}.pdf"
        res = _post('/admin/bd/job-brief/attach', {'brief_id': bid, 'filename': fname, 'pdf_b64': base64.b64encode(data).decode()})
        if not res.get('ok'):
            raise RuntimeError('存回後台失敗：' + str(res.get('error'))[:120])
        return f'bd_job_brief {bid[:8]}: {fname}'
    except Exception as e:  # noqa: BLE001
        D.query(f"UPDATE bd_job_briefs SET status='error', error={q(str(e)[:300])}, done_at=datetime('now','+8 hours') WHERE id={q(bid)}")
        raise
