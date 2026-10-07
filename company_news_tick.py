#!/usr/bin/env python3
"""客戶公司近期動態：每週查一次，寫進 client_companies.news_brief。

2026-10-07 Jacky：GPT 幫他查到美德在推 AI／自動化，問「這種資訊能不能定期更新到職缺備註，
讓顧問跟人選談的時候可以補充，阿財也能問，但不列入評分」。

規則：
- 只寫有來源網址、而且來源是公司官網／年報／正式新聞稿／可信媒體的事；查不到來源的不寫。
- 文字裡不寫公司名稱（一律寫「集團」「公司」）——阿財會讀到，很多客戶要求正式面談前不揭露。
- 這是背景資料，不是篩選條件。

用法：
    python3 company_news_tick.py                # 有開放職缺的客戶，7 天內沒更新過的才查
    python3 company_news_tick.py --company co_x # 只查這一家（不管多久前查過）
    python3 company_news_tick.py --dry          # 只印結果不寫入
"""
import os, sys, json, time, uuid, argparse, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

MODEL = 'claude-sonnet-5'
TIMEOUT_SEC = 900
STALE_DAYS = 7


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def rows(sql):
    return d1_http.query(sql).get('results') or []


def targets(company_id=None):
    if company_id:
        return rows(f"SELECT id, display_name, aliases FROM client_companies WHERE id={q(company_id)}")
    return rows(
        "SELECT DISTINCT c.id, c.display_name, c.aliases FROM client_companies c JOIN jobs j ON j.company_id=c.id "
        "WHERE j.status='open' AND COALESCE(c.hidden_from_consultants,0)=0 AND COALESCE(c.relation,'')<>'private' "
        f"AND (c.news_updated_at IS NULL OR c.news_updated_at < datetime('now','+8 hours','-{STALE_DAYS} days'))")


def build_prompt(c, jobs):
    names = [c['display_name']] + [x.strip() for x in str(c.get('aliases') or '').splitlines() if x.strip()]
    return f"""你是獵頭顧問的研究助理。請用網路搜尋查這家公司最近 12 個月的公開動態（「營運策略與轉型方向」這類可以放寬到 24 個月），給顧問跟人選聊天時當背景。

公司名稱（含別名）：{'、'.join(names)}
目前在招的職缺：{'、'.join(jobs) or '（無）'}

只收這幾類：營運策略與轉型方向（例如 AI、自動化、數位化）、新廠／擴產／新市場、組織或高層異動、重要合作、財報重點。
跟上面職缺有關的優先。

硬規則：
1. 每一條都要有來源網址，來源必須是公司官網、年報、正式新聞稿或可信媒體。找不到來源的不要寫。
2. 不要推測、不要加形容詞誇大。寫「年報提到…」「新聞稿表示…」這種講得出出處的句子。
3. 內文一律不寫公司名稱，用「集團」「公司」代稱（這段會給 AI 面談官看，客戶要求正式面談前不揭露名稱）。
4. 最多 6 條，每條一行、40 字以內，前面標年月。
5. 繁體中文。

只輸出一個 JSON，不要其他文字：
{{"brief": "• 2025/xx 年報：…\\n• …", "sources": [{{"title": "來源標題", "url": "https://..."}}], "nothing_found": false}}
查不到任何有來源的動態就輸出 {{"brief": "", "sources": [], "nothing_found": true}}"""


def run_claude(prompt):
    env = dict(os.environ)
    env.pop('CLAUDE_CODE_ENTRYPOINT', None)
    cmd = ['claude', '-p', '--model', MODEL, '--output-format', 'text',
           '--permission-mode', 'bypassPermissions', '--setting-sources', '',
           '--allowedTools', 'WebSearch,WebFetch', '--session-id', str(uuid.uuid4()), prompt]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env)
    return r.returncode == 0, (r.stdout or r.stderr or '')


def extract(text):
    dec = json.JSONDecoder()
    i = text.find('{')
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(text, i)
            if isinstance(obj, dict) and 'brief' in obj:
                return obj
        except Exception:
            pass
        i = text.find('{', i + 1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--company')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    for c in targets(a.company):
        jobs = [r['title'] for r in rows(f"SELECT title FROM jobs WHERE company_id={q(c['id'])} AND status='open'")]
        print(f"[{time.strftime('%H:%M:%S')}] 查 {c['display_name']}（{len(jobs)} 個開放職缺）", flush=True)
        ok, out = run_claude(build_prompt(c, jobs))
        data = extract(out) if ok else None
        if not data:
            print(f"  ❌ 沒拿到可用結果：{out[-300:]}", flush=True)
            continue
        brief = str(data.get('brief') or '').strip()
        # 名稱外洩防線：內文出現公司名稱或別名就把那段拿掉，不讓阿財看到
        for n in [c['display_name']] + str(c.get('aliases') or '').splitlines():
            n = n.strip()
            for part in [n] + [x for x in n.replace('（', '(').replace('）', ')').replace(')', '(').split('(') if len(x.strip()) >= 2]:
                if part.strip():
                    brief = brief.replace(part.strip(), '集團')
        srcs = [s for s in (data.get('sources') or []) if isinstance(s, dict) and str(s.get('url', '')).startswith('http')][:8]
        if brief and not srcs:
            print('  ⚠️ 有內容但沒有來源，不寫入', flush=True)
            continue
        print(brief or '  （查不到有來源的動態）', flush=True)
        if a.dry:
            continue
        d1_http.query(
            f"UPDATE client_companies SET news_brief={q(brief or None)}, news_sources={q(json.dumps(srcs, ensure_ascii=False))}, "
            f"news_updated_at=datetime('now','+8 hours') WHERE id={q(c['id'])}")
        print('  ✅ 已更新', flush=True)


if __name__ == '__main__':
    main()
