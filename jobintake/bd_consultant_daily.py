#!/usr/bin/env python3
"""每位顧問各一則「今日進度」（2026-09-30 Jacky：取代 17:00 的全隊開發日報）。

每天 18:00 由 launchd（com.step1ne.bddailyreport）執行，推到 TG「開發信 開信・回信」主題
（bot_topics.bd_signals，跟舊日報同一個位置）。目前只發 Jacky、Phoebe 兩位（CONSULTANTS）。

每則 = 一段精簡文字（數字＋3～5 點重點）＋ 一份手機可讀的 HTML（sendDocument，回覆在那則下面）。

數字一律從 D1 讀，不估算：
  今天開發   bd_call_logs（caller＝這位）＋ bd_outreach 今天打勾聯繫（contacted_by＝這位），以公司去重
  今天聯繫人選 sourced_call_logs（caller）＋ candidate_notes type='電洽紀錄'（created_by），
             candidate_notes 就是顧問在人選卡片「記錄新的通話」寫進去的那一筆（applications 本身沒有「誰打的」欄位）
  通話心得   bd_call_notes（author）
  本週累計   本週一起算：開發通數（bd_call_logs）、有興趣家數（result='interested' 的公司去重）、
             簽約家數（bd_outreach.manual_stage='closed'；算給 manual_stage_by，沒填就算給 assigned_to）
AI 總結素材：今天這位的 bd_call_notes、bd_call_logs.note、sourced_call_logs.note、call_transcripts.ai_json（表不存在就跳過）。
  一筆素材都沒有 → 直接寫「今天沒有記錄」，不呼叫 AI。
  AI 用本機 claude CLI，加 --setting-sources ''（見 memory：背景腳本必加 setting-sources），不給任何工具。

用法：python3 bd_consultant_daily.py [--dry-run] [--date YYYY-MM-DD] [--out 資料夾]
  --dry-run 只印兩則內容並產生 HTML，不發 TG。
"""
import datetime
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D  # noqa: E402

# 顧問顯示名 → 系統裡可能出現的寫法（caller/author/created_by 都是自由文字；TG 帳號也會出現）
CONSULTANTS = {
    'Jacky': ['jacky', 'jackyyuqi'],
    'Phoebe': ['phoebe', 'behe10', 'ph'],
}
BD_ZH = {'no_answer': '沒接', 'gatekeeper': '被總機擋', 'got_contact': '問到窗口', 'got_email': '拿到信箱',
         'interested': '有興趣', 'not_hiring': '沒在徵人', 'not_interested': '拒絕'}
SC_ZH = {'no_answer': '沒接', 'interested': '有興趣', 'not_interested': '沒興趣', 'invited': '已邀約',
         'wrong_person': '找錯人'}
MODEL = 'claude-sonnet-5'
CLAUDE_BIN = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
# 不給工具、不載 MCP、不讀全域設定（否則 CLAUDE.md 的 agentacct 指令會蓋掉任務）
NO_TOOLS = ['--disallowed-tools', 'Bash,Edit,Write,Read,WebFetch,WebSearch,Task,Glob,Grep,NotebookEdit',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']


def q(sql):
    r = D.query(sql)
    return (r.get('results') if isinstance(r, dict) else r) or []


def s(v):
    return "'" + str(v).replace("'", "''") + "'"


def who_in(col, name):
    names = [name.lower()] + CONSULTANTS[name]
    return f"LOWER(TRIM(COALESCE({col},''))) IN ({','.join(s(n) for n in names)})"


def scrub(t):
    """交給 AI／放進 HTML 前先拿掉電話與信箱（只留公司名稱）。"""
    t = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '〔信箱〕', str(t or ''))
    return re.sub(r'(?<!\d)(?:\+?886[-\s]?|0)\d[\d\-\s]{6,12}\d', '〔電話〕', t)


def call_transcripts(day, name):
    """call_transcripts 今天另一位幫手正在建；不存在或欄位對不上就回空。"""
    try:
        cols = [c['name'] for c in q("PRAGMA table_info(call_transcripts)")]
    except Exception:
        return []
    if 'ai_json' not in cols:
        return []
    who = next((c for c in ('caller', 'consultant', 'author', 'created_by', 'owner', 'uploaded_by') if c in cols), None)
    when = next((c for c in ('created_at', 'call_at', 'uploaded_at') if c in cols), None)
    if not who or not when:
        return []
    return [r['ai_json'] for r in q(f"SELECT ai_json FROM call_transcripts WHERE {who_in(who, name)} "
                                    f"AND substr({when},1,10)={s(day)} AND ai_json IS NOT NULL")]


def collect(day, monday, name):
    d = {'name': name}
    logs = q(f"SELECT company, result, note, created_at FROM bd_call_logs WHERE deleted_at IS NULL AND {who_in('caller', name)} "
             f"AND substr(created_at,1,10)={s(day)} ORDER BY created_at")
    ticked = q(f"SELECT DISTINCT company FROM bd_outreach WHERE {who_in('contacted_by', name)} "
               f"AND substr(contacted_at,1,10)={s(day)}")
    d['bd_logs'] = logs
    d['bd_results'] = {}
    for r in logs:
        d['bd_results'][r['result']] = d['bd_results'].get(r['result'], 0) + 1
    d['bd_ticked'] = [r['company'] for r in ticked]
    d['bd_companies'] = sorted({r['company'] for r in logs if r['company']} | set(d['bd_ticked']))

    sc = q(f"SELECT sourced_id, result, note FROM sourced_call_logs WHERE {who_in('caller', name)} "
           f"AND substr(created_at,1,10)={s(day)}")
    apps = q(f"SELECT DISTINCT application_id FROM candidate_notes WHERE type='電洽紀錄' "
             f"AND {who_in('created_by', name)} AND substr(created_at,1,10)={s(day)}")
    d['sc_logs'] = sc
    d['sc_results'] = {}
    for r in sc:
        d['sc_results'][r['result']] = d['sc_results'].get(r['result'], 0) + 1
    d['sc_people'] = len({r['sourced_id'] for r in sc})
    d['app_people'] = len(apps)

    d['notes'] = q(f"SELECT company, learned, next_time, problem FROM bd_call_notes WHERE deleted_at IS NULL AND {who_in('author', name)} "
                   f"AND substr(created_at,1,10)={s(day)} ORDER BY created_at")
    d['transcripts'] = call_transcripts(day, name)

    d['wk_calls'] = q(f"SELECT COUNT(*) n FROM bd_call_logs WHERE deleted_at IS NULL AND {who_in('caller', name)} "
                      f"AND substr(created_at,1,10) BETWEEN {s(monday)} AND {s(day)}")[0]['n']
    d['wk_interested'] = q(f"SELECT COUNT(DISTINCT company) n FROM bd_call_logs WHERE deleted_at IS NULL AND {who_in('caller', name)} "
                           f"AND result='interested' AND substr(created_at,1,10) BETWEEN {s(monday)} AND {s(day)}")[0]['n']
    closer = (f"CASE WHEN COALESCE(TRIM(manual_stage_by),'') IN ('','顧問') THEN assigned_to ELSE manual_stage_by END")
    d['wk_signed'] = q(f"SELECT COUNT(DISTINCT company) n FROM bd_outreach WHERE manual_stage='closed' "
                       f"AND {who_in(closer, name)} "
                       f"AND substr(COALESCE(manual_stage_at,updated_at),1,10) BETWEEN {s(monday)} AND {s(day)}")[0]['n']
    return d


def material(d):
    parts = []
    for n in d['notes']:
        seg = [f"【通話心得｜{n['company']}】"]
        for k, zh in (('learned', '學到什麼'), ('next_time', '下一通怎麼講'), ('problem', '遇到的問題')):
            if n.get(k):
                seg.append(f"{zh}：{scrub(n[k])}")
        parts.append('\n'.join(seg))
    for r in d['bd_logs']:
        if r.get('note'):
            parts.append(f"【開發電話備註｜{r['company']}｜{BD_ZH.get(r['result'], r['result'])}】{scrub(r['note'])}")
    for r in d['sc_logs']:
        if r.get('note'):
            parts.append(f"【人選電話備註｜{SC_ZH.get(r['result'], r['result'])}】{scrub(r['note'])}")
    for t in d['transcripts']:
        parts.append(f"【通話逐字稿 AI 分析】{scrub(t)[:4000]}")
    return '\n\n'.join(parts)


def ai_summary(d, mat):
    prompt = f"""你是獵頭公司的業務教練。下面是顧問 {d['name']} 今天自己寫下的開發客戶／聯繫人選紀錄。
請只根據這些紀錄，整理出「今天遇到什麼問題」和「下次可以怎麼優化」。

規則：
- 只能用紀錄裡真的寫到的事，沒寫到的不要推測、不要補
- 可以寫公司名稱；不可以寫人選姓名、任何人的電話、信箱、薪資
- 用繁體中文、白話、每點一句話講完，不要術語
- 只輸出一個 JSON，不要任何其他文字，格式：
{{"重點": ["3到5點，給 TG 訊息用，每點 30 字內"],
  "遇到的問題": [{{"問題": "...", "出處": "哪家公司或哪類紀錄"}}],
  "下次怎麼優化": [{{"建議": "...", "具體做法": "下一通電話可以怎麼講或怎麼做"}}]}}

今天的紀錄：
{mat}
"""
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    r = subprocess.run([CLAUDE_BIN, '-p', '--model', MODEL, *NO_TOOLS, '--output-format', 'text'],
                       input=''.join(c for c in prompt if c in '\n\t' or ord(c) >= 32),
                       capture_output=True, text=True, env=env, timeout=300, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-200:]}')
    out = r.stdout
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'AI 回覆裡沒有 JSON：{out[:120]}')
    return json.loads(out[i:j + 1])


def fmt_counts(counts, zh):
    return '、'.join(f'{zh.get(k, k)} {v}' for k, v in sorted(counts.items(), key=lambda x: -x[1]))


def build_text(d, day, ai):
    bd_n, calls = len(d['bd_companies']), len(d['bd_logs'])
    lines = [f"{d['name']}｜今日進度（{day}）", '']
    line = f"☎️ 開發客戶：{bd_n} 家（電話 {calls} 通"
    if d['bd_results']:
        line += '：' + fmt_counts(d['bd_results'], BD_ZH)
    line += '）'
    if d['bd_ticked']:
        line += f"，卡片打勾聯繫 {len(d['bd_ticked'])} 家"
    lines.append(line)
    line = f"👤 聯繫人選：{d['sc_people'] + d['app_people']} 位（主動開發 {d['sc_people']} 位"
    if d['sc_results']:
        line += '：' + fmt_counts(d['sc_results'], SC_ZH)
    line += f"；應徵者電洽 {d['app_people']} 位）"
    lines.append(line)
    lines.append(f"📝 通話心得：{len(d['notes'])} 則")
    lines.append(f"📅 本週累計：開發 {d['wk_calls']} 通｜有興趣 {d['wk_interested']} 家｜簽約 {d['wk_signed']} 家")
    lines.append('')
    lines.append('🤖 AI 總結：')
    if ai.get('_none'):
        lines.append('・今天沒有記錄')
    elif ai.get('_error'):
        lines.append(f"・AI 總結這次沒產出（{ai['_error'][:80]}），數字不受影響")
    else:
        for p in (ai.get('重點') or [])[:5]:
            lines.append(f'・{p}')
        lines.append('（詳細建議看下面的附件）')
    return '\n'.join(lines)


CSS = """
:root{--bg:#EEF2F6;--card:#FFFFFF;--ink:#14213D;--muted:#5B6B82;--line:#D6DEE8;--accent:#0B7A75;--warn:#B45309}
@media (prefers-color-scheme:dark){:root{--bg:#0F1722;--card:#18222F;--ink:#E6ECF3;--muted:#9AA9BC;--line:#2A3747;--accent:#3CC6BE;--warn:#F2A65A}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 -apple-system,"PingFang TC","Noto Sans TC",sans-serif}
main{max-width:680px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);font-size:14px;margin:0 0 18px}
.nums{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:18px}
.n{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.n b{display:block;font-size:30px;line-height:1.1;color:var(--accent);font-variant-numeric:tabular-nums}
.n span{font-size:13px;color:var(--muted)}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px}
h2{font-size:17px;margin:0 0 10px}ul{margin:0;padding-left:20px}li{margin:6px 0}
.src{color:var(--muted);font-size:13px}.how{display:block;color:var(--muted);font-size:14px}
.prob li::marker{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 4px;border-top:1px solid var(--line);vertical-align:top}
td:last-child{white-space:nowrap;color:var(--muted)}
.empty{color:var(--muted)}
"""


def build_html(d, day, ai):
    e = html.escape
    h = [f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>{e(d["name"])} 今日進度 {day}</title><style>{CSS}</style></head><body><main>',
         f'<h1>{e(d["name"])}｜今日進度</h1><p class="sub">{day}・數字全部從系統紀錄讀取・內部使用</p>',
         '<div class="nums">']
    for v, lab in ((len(d['bd_companies']), f"今天開發家數（電話 {len(d['bd_logs'])} 通）"),
                   (d['sc_people'] + d['app_people'], '今天聯繫人選'),
                   (len(d['notes']), '今天寫的通話心得'),
                   (d['wk_signed'], f"本週簽約（本週 {d['wk_calls']} 通、有興趣 {d['wk_interested']} 家）")):
        h.append(f'<div class="n"><b>{v}</b><span>{e(lab)}</span></div>')
    h.append('</div>')

    if ai.get('_none'):
        h.append('<section><h2>AI 總結</h2><p class="empty">今天沒有記錄。</p></section>')
    elif ai.get('_error'):
        h.append(f'<section><h2>AI 總結</h2><p class="empty">這次沒產出：{e(ai["_error"][:200])}</p></section>')
    else:
        h.append('<section class="prob"><h2>今天遇到的問題</h2><ul>')
        for p in ai.get('遇到的問題') or []:
            h.append(f'<li>{e(scrub(p.get("問題", "")))} <span class="src">— {e(scrub(p.get("出處", "")))}</span></li>')
        h.append('</ul></section><section><h2>下次可以怎麼優化</h2><ul>')
        for p in ai.get('下次怎麼優化') or []:
            h.append(f'<li>{e(scrub(p.get("建議", "")))}<span class="how">{e(scrub(p.get("具體做法", "")))}</span></li>')
        h.append('</ul></section>')

    h.append('<section><h2>今天開發的公司</h2>')
    if d['bd_logs'] or d['bd_ticked']:
        h.append('<table>')
        for r in d['bd_logs']:
            h.append(f'<tr><td>{e(r["company"] or "")}</td><td>{e(BD_ZH.get(r["result"], r["result"] or ""))}'
                     f'・{e((r["created_at"] or "")[11:16])}</td></tr>')
        logged = {r['company'] for r in d['bd_logs']}
        for c in d['bd_ticked']:
            if c not in logged:
                h.append(f'<tr><td>{e(c)}</td><td>卡片打勾聯繫</td></tr>')
        h.append('</table>')
    else:
        h.append('<p class="empty">今天沒有開發紀錄。</p>')
    h.append('</section>')
    if d['sc_results']:
        h.append(f'<section><h2>主動開發人選的電話結果</h2><p>{e(fmt_counts(d["sc_results"], SC_ZH))}</p></section>')
    h.append('</main></body></html>')
    return ''.join(h)


def tg_env():
    env = {}
    for ln in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8'):
        k, _, v = ln.strip().partition('=')
        env[k] = v.strip('"\'')
    return env


def tg_send(env, topic, text):
    body = {'chat_id': env['TG_CHAT_ID'], 'text': text}
    if topic:
        body['message_thread_id'] = topic
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=20))


def tg_document(env, topic, path, reply_to, caption):
    bd = uuid.uuid4().hex
    fields = {'chat_id': env['TG_CHAT_ID'], 'caption': caption}
    if topic:
        fields['message_thread_id'] = str(topic)
    if reply_to:
        fields['reply_to_message_id'] = str(reply_to)
    parts = []
    for k, v in fields.items():
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    fn = os.path.basename(path)
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="document"; filename="{fn}"\r\n'
                 f'Content-Type: text/html\r\n\r\n'.encode() + open(path, 'rb').read() + b'\r\n')
    parts.append(f'--{bd}--\r\n'.encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendDocument",
                                 data=b''.join(parts), headers={'Content-Type': f'multipart/form-data; boundary={bd}'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    now = datetime.datetime.now(ZoneInfo('Asia/Taipei'))
    day = args[args.index('--date') + 1] if '--date' in args else now.strftime('%Y-%m-%d')
    dday = datetime.date.fromisoformat(day)
    monday = (dday - datetime.timedelta(days=dday.weekday())).isoformat()
    out = args[args.index('--out') + 1] if '--out' in args else tempfile.mkdtemp(prefix='bd_consultant_daily_')
    os.makedirs(out, exist_ok=True)

    env = topic = None
    if not dry:
        env = tg_env()
        topic = (q("SELECT topic_id FROM bot_topics WHERE key='bd_signals'") or [{}])[0].get('topic_id')

    for name in CONSULTANTS:
        d = collect(day, monday, name)
        mat = material(d)
        if not mat.strip():
            ai = {'_none': True}
        else:
            try:
                ai = ai_summary(d, mat)
            except Exception as ex:
                ai = {'_error': str(ex)}
        text = build_text(d, day, ai)
        path = os.path.join(out, f'{name}_今日進度_{day}.html')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(build_html(d, day, ai))
        if dry:
            print(text)
            print(f'［HTML］{path}')
            print('─' * 30)
            continue
        r = tg_send(env, topic, text)
        mid = (r.get('result') or {}).get('message_id')
        r2 = tg_document(env, topic, path, mid, f"{name}｜今日進度・詳細建議")
        print(name, r.get('ok'), r2.get('ok'), flush=True)


if __name__ == '__main__':
    main()
