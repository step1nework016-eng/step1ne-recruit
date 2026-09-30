#!/usr/bin/env python3
"""AI 整理每家開發客戶的「跟進便利貼」草稿（2026-09-30 Jacky）。

一家客戶的紀錄散在：每通電話備註（bd_call_logs）、通話心得（bd_call_notes）、逐字稿 AI 分析（call_transcripts）、
寄出的信（bd_outreach scenario=contract_pack）、顧問自己寫的跟進備註（bd_company_status.next_step）。
這支把一家的紀錄讀一遍，寫成「聊到哪／下一步／哪一天／備註」存進 bd_company_status 的 ai_* 欄位，
狀態 ai_state='new'（後台卡片顯示「AI 建議，待確認」，顧問按「採用」才會變成正式的下一步）。

規則：
- 顧問自己填的 next_action／next_due／next_step 一律不動，只寫 ai_* 欄位。
- 放進備選池（parked_at 有值）的公司不整理。
- 只整理「上次整理之後有新紀錄」的公司（第一次跑＝全部有紀錄的公司）。
- AI 只能根據紀錄，沒寫到的不推測；日期要是今天之後的工作日。
- 本機 claude CLI，加 --setting-sources ''，不給任何工具（見 memory：背景腳本必加 setting-sources）。

每天 17:30 由 WSL2 的 systemd timer（step1ne-bdfollowupai）執行，在 18:00 日報之前。
用法：python3 bd_followup_ai.py [--dry-run] [--all] [--company 公司名] [--limit N]
  --all 不管有沒有新紀錄都重新整理（已採用／已略過的也會重新給一份新的建議）
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D  # noqa: E402

MODEL = 'claude-sonnet-5'
CLAUDE_BIN = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
NO_TOOLS = ['--disallowed-tools', 'Bash,Edit,Write,Read,WebFetch,WebSearch,Task,Glob,Grep,NotebookEdit',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']
ACTS = {'call': '打電話', 'mail': '寄信', 'wait': '等對方回覆', 'meet': '約見面', 'pause': '先暫停'}
RES = {'no_answer': '沒接', 'gatekeeper': '被總機擋', 'got_contact': '問到窗口', 'got_email': '拿到信箱',
       'interested': '有興趣', 'not_hiring': '沒在徵人', 'not_interested': '拒絕'}
WEEK = '一二三四五六日'


def q(sql):
    r = D.query(sql)
    return (r.get('results') if isinstance(r, dict) else r) or []


def s(v):
    return "'" + str(v).replace("'", "''") + "'"


def now_tw():
    return datetime.datetime.now(ZoneInfo('Asia/Taipei'))


def companies(force, only, limit):
    """有紀錄、沒放備選池、上次整理後有新東西的公司"""
    if only:
        return [only]
    # D1 一次 UNION 太多段會報錯（too many terms in compound SELECT），分開查再合併
    srcs = [
        "SELECT company, MAX(created_at) t FROM bd_call_logs WHERE deleted_at IS NULL GROUP BY company",
        "SELECT company, MAX(created_at) t FROM bd_call_notes WHERE deleted_at IS NULL GROUP BY company",
        "SELECT company, MAX(created_at) t FROM call_transcripts WHERE COALESCE(company,'') <> '' GROUP BY company",
        "SELECT company, MAX(COALESCE(sent_at, created_at)) t FROM bd_outreach WHERE scenario='contract_pack' GROUP BY company",
        "SELECT company, MAX(contacted_at) t FROM bd_outreach WHERE contacted_at IS NOT NULL GROUP BY company",
        "SELECT company, updated_at t FROM bd_company_status WHERE COALESCE(next_step,'') <> ''",
    ]
    last = {}
    for sql in srcs:
        for r in q(sql):
            c = r.get('company')
            if c and str(r.get('t') or '') > last.get(c, ''):
                last[c] = str(r.get('t') or '')
    st = {r['company']: r for r in q("SELECT company, ai_at, parked_at FROM bd_company_status")}
    rows = [{'company': c, 'last_t': t, 'ai_at': (st.get(c) or {}).get('ai_at'), 'parked_at': (st.get(c) or {}).get('parked_at')}
            for c, t in last.items()]
    out = []
    for r in rows:
        if r.get('parked_at'):
            continue
        if force or not r.get('ai_at') or str(r.get('last_t') or '') > str(r['ai_at']):
            out.append(r['company'])
    out.sort()
    return out[:limit] if limit else out


def material(company):
    c = s(company)
    parts = []
    job = q(f"SELECT job_title, assigned_to FROM bd_outreach WHERE company={c} AND COALESCE(job_title,'')<>'' LIMIT 1")
    who = q(f"SELECT assigned_to FROM bd_outreach WHERE company={c} AND COALESCE(assigned_to,'')<>'' LIMIT 1")
    head = f"公司：{company}"
    if job:
        head += f"｜我們想談的職缺：{job[0]['job_title']}"
    if who:
        head += f"｜負責顧問：{who[0]['assigned_to']}"
    for r in q(f"SELECT created_at, caller, result, note, retry_at FROM bd_call_logs WHERE company={c} AND deleted_at IS NULL ORDER BY created_at"):
        parts.append(f"[{str(r['created_at'])[:16]}] 電話（{r.get('caller') or '顧問'}）結果：{RES.get(r['result'], r['result'])}"
                     + (f"；備註：{r['note']}" if r.get('note') else ''))
    for r in q(f"SELECT created_at, author, learned, next_time, problem FROM bd_call_notes WHERE company={c} AND deleted_at IS NULL ORDER BY created_at"):
        seg = [f"[{str(r['created_at'])[:16]}] 通話心得（{r.get('author') or '顧問'}）"]
        for k, zh in (('learned', '學到'), ('next_time', '下一通怎麼講'), ('problem', '問題')):
            if r.get(k):
                seg.append(f"{zh}：{r[k]}")
        parts.append('；'.join(seg))
    for r in q(f"SELECT created_at, caller, note, call_result, ai_json FROM call_transcripts WHERE company={c} AND COALESCE(is_test,0)=0 ORDER BY created_at"):
        seg = f"[{str(r['created_at'])[:16]}] 通話逐字稿（{r.get('caller') or '顧問'}）"
        if r.get('note'):
            seg += f"；顧問備註：{r['note']}"
        if r.get('ai_json'):
            seg += f"；AI 分析：{str(r['ai_json'])[:1500]}"
        parts.append(seg)
    for r in q(f"SELECT created_at, sent_at, status, subject FROM bd_outreach WHERE company={c} AND scenario='contract_pack' ORDER BY created_at"):
        st = {'sent': '已寄出', 'pending': '等核准', 'rejected': '決定不寄', 'draft': '被退回'}.get(r['status'], r['status'])
        parts.append(f"[{str(r.get('sent_at') or r['created_at'])[:16]}] 開發信「{r.get('subject') or ''}」：{st}")
    st = q(f"SELECT next_action, next_due, next_step, updated_at, updated_by FROM bd_company_status WHERE company={c}")
    if st and (st[0].get('next_step') or st[0].get('next_action')):
        x = st[0]
        parts.append(f"[{str(x.get('updated_at') or '')[:16]}] 顧問自己寫的跟進（{x.get('updated_by') or ''}）："
                     f"下一步 {ACTS.get(x.get('next_action'), '未選')}，日期 {x.get('next_due') or '未排'}，備註：{x.get('next_step') or '無'}")
    return head, parts


def ask_ai(head, parts, today):
    wd = WEEK[today.weekday()]
    prompt = f"""你是獵頭公司的業務助理。下面是一家「開發中客戶」的所有聯絡紀錄。請整理成一張跟進便利貼，讓顧問下次打開就知道要做什麼。

今天是 {today.isoformat()}（星期{wd}）。

規則：
- 只能用紀錄裡真的寫到的事，沒寫到的不要推測、不要編
- 繁體中文、白話、不要術語；不寫任何人的電話、信箱
- 過總機、找窗口的建議：用紀錄裡「真的發生過的事」當理由，例如真的寄過信就說「之前寄資料給人資，想確認有沒有收到」、有公開職缺就說「關於某某職缺想跟人資確認細節」、知道窗口名字就直接指名找他；不能編沒發生過的事（沒聯絡過卻說聯絡過），也不能假冒送貨、客戶或任何身分。紀錄裡如果有別人建議過這種講法，不要照抄，改成真實的理由
- 備註裡不寫費用、費率、月薪幾成
- 「下一步」只能從這五個選一個：call（打電話）、mail（寄信）、wait（等對方回覆）、meet（約見面）、pause（先暫停：對方明確拒絕或說不需要）
- 日期：紀錄裡對方有說時間就照他說的；沒說的話，寄信→下一個工作日、寄完信→3 個工作日後電話追、沒接或被擋→2 個工作日後再打、等回覆→5 個工作日後追。一定要是今天之後的日期，遇到週六日往後挪到週一；pause 不用日期
- 只輸出一個 JSON，不要任何其他文字：
{{"聊到哪": "一句話，40 字內", "next_action": "call|mail|wait|meet|pause", "next_due": "YYYY-MM-DD 或空字串", "備註": "要做什麼、要注意什麼，60 字內"}}

{head}
紀錄（依時間）：
""" + '\n'.join(parts)
    env = dict(os.environ)
    env.pop('CLAUDECODE', None)
    r = subprocess.run([CLAUDE_BIN, '-p', '--model', MODEL, *NO_TOOLS, '--output-format', 'text'],
                       input=''.join(ch for ch in prompt if ch in '\n\t' or ord(ch) >= 32),
                       capture_output=True, text=True, env=env, timeout=240, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f'claude exit={r.returncode}：{(r.stderr or r.stdout)[-200:]}')
    out = r.stdout
    i, j = out.find('{'), out.rfind('}')
    if i < 0 or j < 0:
        raise RuntimeError(f'AI 回覆裡沒有 JSON：{out[:120]}')
    return json.loads(out[i:j + 1])


def clean(ai, today):
    act = ai.get('next_action') if ai.get('next_action') in ACTS else ''
    due = str(ai.get('next_due') or '').strip()
    try:
        d = datetime.date.fromisoformat(due)
        if d <= today:
            d = today + datetime.timedelta(days=1)
        while d.weekday() >= 5:
            d += datetime.timedelta(days=1)
        due = d.isoformat()
    except ValueError:
        due = ''
    if act == 'pause':
        due = ''
    return {'summary': str(ai.get('聊到哪') or '').strip()[:120], 'action': act, 'due': due,
            'memo': str(ai.get('備註') or '').strip()[:300]}


def save(company, x, now):
    n = lambda v: s(v) if v else 'NULL'  # noqa: E731
    q("INSERT INTO bd_company_status (company, ai_summary, ai_action, ai_due, ai_memo, ai_at, ai_state, updated_at) "
      f"VALUES ({s(company)},{s(x['summary'])},{n(x['action'])},{n(x['due'])},{s(x['memo'])},{s(now)},'new',{s(now)}) "
      "ON CONFLICT(company) DO UPDATE SET ai_summary=excluded.ai_summary, ai_action=excluded.ai_action, "
      "ai_due=excluded.ai_due, ai_memo=excluded.ai_memo, ai_at=excluded.ai_at, ai_state='new'")


def main():
    args = sys.argv[1:]
    dry = '--dry-run' in args
    force = '--all' in args
    only = args[args.index('--company') + 1] if '--company' in args else None
    limit = int(args[args.index('--limit') + 1]) if '--limit' in args else 0
    t = now_tw()
    today = t.date()
    now = t.strftime('%Y-%m-%d %H:%M:%S')
    todo = companies(force, only, limit)
    print(f'[{now}] 要整理 {len(todo)} 家', flush=True)
    ok = fail = 0
    for company in todo:
        head, parts = material(company)
        if not parts:
            print(f'・{company}：沒有任何紀錄，跳過', flush=True)
            continue
        try:
            x = clean(ask_ai(head, parts, today), today)
        except Exception as ex:
            fail += 1
            print(f'・{company}：❌ {str(ex)[:150]}', flush=True)
            continue
        print(f"・{company}：{x['summary']}｜{ACTS.get(x['action'], '?')} {x['due']}｜{x['memo']}", flush=True)
        if not dry:
            save(company, x, now)
        ok += 1
    print(f'完成：整理 {ok} 家，失敗 {fail} 家{"（dry-run 沒寫入）" if dry else ""}', flush=True)


if __name__ == '__main__':
    main()
