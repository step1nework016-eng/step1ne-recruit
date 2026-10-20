#!/usr/bin/env python3
"""client_gate.py — 推給客戶之前，先對客戶自己講過的推薦門檻（2026-10-20 Jacky 拍板）

為什麼有這支：美德這兩週婉拒的人選，理由幾乎都是客戶一開始就講清楚的條件
（產業要紡織成衣、年薪 200 萬以上很難推進、學歷／外派年資不夠、不熟 T100／MRP），
推之前沒先擋，客戶耐心被消耗，推薦→面試只剩 7%。

門檻存在 D1 site_settings key＝client_push_gates：
  {"<company_id>": {"name": "美德", "source": "出處", "rules": {"*": [...], "<job_slug>": [...]}}}
"*" 是這家客戶所有職缺共用，其餘依職缺。只寫客戶在信裡／電話裡講過的條件。
⚠️ 不准放年齡、性別、婚姻這類條件（就服法禁止；客戶講了也只能記內部備註），載入時會自動濾掉。

接在哪：client_report_tick.py 產「客戶推薦履歷草稿」貼 TG 確認前會呼叫 check()，
不符合的話草稿最上面寫「🚦 不建議推」並列出哪幾條不符；顧問仍可自己決定要不要送。

手動檢查（例如要直接寫 Email 推之前）：
  python3 client_gate.py <application_id> [job_slug]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

BANNED = re.compile(r'年齡|歲|性別|男性|女性|婚姻|已婚|未婚|懷孕|生育')


def _ops():
    import consultant_ops
    return consultant_ops


def load_rules(company_id, job_slug):
    c = _ops()
    r = c.rows("SELECT value FROM site_settings WHERE key='client_push_gates'", [])
    if not r or not company_id:
        return None, []
    g = (json.loads(r[0]['value']) or {}).get(company_id)
    if not g:
        return None, []
    rules = list((g.get('rules') or {}).get('*', [])) + list((g.get('rules') or {}).get(job_slug or '', []))
    return g.get('name') or company_id, [x for x in rules if x and not BANNED.search(x)]


def profile_from_synth(synth):
    """客戶推薦履歷的結構化資料 → 給判斷用的人選摘要（不含年齡性別）。"""
    s = dict(synth or {})
    for k in ('basics',):
        b = dict(s.get(k) or {})
        for kk in ('age', 'gender', 'marital', 'birth'):
            b.pop(kk, None)
        s[k] = b
    keep = {k: s.get(k) for k in ('one_liner', 'basics', 'summary_row', 'work_history', 'education',
                                  'skills', 'for_client', 'hard_filters', 'call_summary_client_md') if s.get(k)}
    return json.dumps(keep, ensure_ascii=False)[:9000]


def profile_from_db(app_id):
    c = _ops()
    a = c.rows("SELECT name,job_slug,call_summary_md,call_summary_client_md,resume_file_id FROM applications WHERE id=?", [app_id])
    if not a:
        raise SystemExit('找不到人選')
    a = a[0]
    parts = [f"電洽摘要：{a.get('call_summary_client_md') or a.get('call_summary_md') or '（無）'}"]
    if a.get('resume_file_id'):
        f = c.rows("SELECT text_content FROM files WHERE id=?", [a['resume_file_id']])
        if f and f[0].get('text_content'):
            parts.append('履歷原文：' + f[0]['text_content'][:6000])
    rep = c.rows("SELECT content_json FROM reports WHERE application_id=? ORDER BY created_at DESC LIMIT 1", [app_id])
    if rep and rep[0].get('content_json'):
        parts.append('阿財面談報告：' + rep[0]['content_json'][:4000])
    txt = '\n\n'.join(parts)
    return a, re.sub(r'\d{2,3}\s*歲|年齡[:：]?\s*\d+', '', txt)


def check(company_id, job_slug, profile_text, run_claude=None):
    """回傳 None（這家客戶沒設門檻）或 {'client','verdict':'pass|fail|unsure','items':[{'rule','result','why'}]}"""
    name, rules = load_rules(company_id, job_slug)
    if not rules:
        return None
    if run_claude is None:
        from ai_worker import run_claude as rc
        run_claude = rc
    numbered = '\n'.join(f'{i + 1}. {r}' for i, r in enumerate(rules))
    prompt = f"""你是獵頭公司的推薦前檢查員。客戶「{name}」明確講過的推薦門檻如下，逐條對照人選資料判斷。

【門檻】
{numbered}

【人選資料】
{profile_text}

只輸出 JSON：{{"items":[{{"rule":"門檻原文","result":"符合|不符|資料不足","why":"一句話，引用人選資料裡的具體事實"}}]}}
規則：
- 每一條門檻都要有一筆，順序照上面。
- 資料裡找不到證據就寫「資料不足」，不要猜、不要往好的方向推論。
- 不要評論年齡、性別、婚姻，也不要把它們當理由。
- why 用繁體中文，30 字內。"""
    out = run_claude(prompt, want_json=True, timeout=240)
    try:
        data = json.loads(out)
    except Exception:
        m = re.search(r'\{.*\}', out or '', re.S)
        data = json.loads(m.group(0)) if m else {'items': []}
    items = data.get('items') or []
    res = [i.get('result') for i in items]
    verdict = 'fail' if '不符' in res else ('unsure' if '資料不足' in res else 'pass')
    return {'client': name, 'verdict': verdict, 'items': items}


def format_block(g):
    """給 TG 草稿用的文字區塊。"""
    if not g:
        return []
    head = {'fail': f"🚦 {g['client']}推薦門檻：❌ 不建議推（有條件不符）",
            'unsure': f"🚦 {g['client']}推薦門檻：⚠️ 有條件資料不足，送前先跟人選確認",
            'pass': f"🚦 {g['client']}推薦門檻：✅ 都符合"}[g['verdict']]
    mark = {'符合': '✅', '不符': '❌', '資料不足': '❔'}
    lines = [head]
    for i in g['items']:
        lines.append(f"{mark.get(i.get('result'), '・')} {i.get('rule', '')}｜{i.get('why', '')}")
    return lines + ['']


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    c = _ops()
    app, text = profile_from_db(sys.argv[1])
    job = sys.argv[2] if len(sys.argv) > 2 else app['job_slug']
    co = c.rows("SELECT company_id FROM jobs WHERE slug=?", [job])
    g = check(co[0]['company_id'] if co else None, job, text)
    print('\n'.join(format_block(g)) if g else '這家客戶沒有設定推薦門檻')
