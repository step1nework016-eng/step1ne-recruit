#!/usr/bin/env python3
"""夜間工作的早安摘要：每天 08:30 推一則到 TG「📬 開發信 開信・回信」主題（bot_topics.bd_signals）。

內容（全部從資料庫照實算，不估算）：
- 昨晚新增幾家合格客戶、分給誰（bd_outreach，batch_id 以 nightly-bd- 開頭）
- 幾家不合格、各缺什麼（guard_json.missing）
- 各職缺新增幾位人選（sourced_candidates.source='AI夜間找人'）
- AI 找人精準度 = 顧問標 fit 的人數 ÷ 已評分（fit＋unfit）人數（所有 AI夜間找人 開頭的來源累計）
- 昨晚兩個工作有沒有跑成功、有沒有留下瀏覽器（~/aijob-automation/logs/nightly_runs.jsonl）

「昨晚」＝昨天 18:00 到現在（台灣時間）。

用法：python3 morning_summary.py [--dry-run]
"""
import datetime
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import d1_http as D  # noqa: E402

os.environ.setdefault('TZ', 'Asia/Taipei')
try:
    import time
    time.tzset()
except AttributeError:
    pass

RUNS = os.path.expanduser('~/aijob-automation/logs/nightly_runs.jsonl')
TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')


def q(sql):
    r = D.query(sql)
    return (r.get('results') if isinstance(r, dict) else r) or []


def esc(s):
    return str(s).replace("'", "''")


def run_status(since):
    """讀夜間工作紀錄，回傳每種工作最後一次的結果。"""
    last = {}
    try:
        with open(RUNS, encoding='utf-8') as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if r.get('ts', '') >= since:
                    last[r.get('kind')] = r
    except OSError:
        pass
    return last


def build_text():
    now = datetime.datetime.now()
    since = (now - datetime.timedelta(days=1)).replace(hour=18, minute=0, second=0).strftime('%Y-%m-%d %H:%M:%S')
    today = now.strftime('%Y-%m-%d')
    lines = [f'🌙 夜間 AI 工作早報｜{today}', '']

    # ── 開發客戶 ──
    bd = q("SELECT company, assigned_to, guard_json FROM bd_outreach "
           f"WHERE batch_id LIKE 'nightly-bd-%' AND created_at >= '{esc(since)}'")
    ok = [r for r in bd if r.get('assigned_to')]
    bad = [r for r in bd if not r.get('assigned_to')]
    by_who = {}
    for r in ok:
        by_who[r['assigned_to']] = by_who.get(r['assigned_to'], 0) + 1
    lines.append(f'🏢 開發客戶：昨晚新增 {len(bd)} 家，合格 {len(ok)} 家'
                 + ('（' + '、'.join(f'{k} {v} 家' for k, v in sorted(by_who.items(), key=lambda x: -x[1])) + '）' if by_who else ''))
    if bad:
        lines.append(f'　不合格 {len(bad)} 家（沒指派）：')
        for r in bad[:10]:
            miss = []
            try:
                miss = (json.loads(r.get('guard_json') or '{}') or {}).get('missing') or []
            except (json.JSONDecodeError, AttributeError):
                pass
            lines.append(f"　・{r['company']}：缺{'、'.join(miss) if miss else '（沒寫原因）'}")
        if len(bad) > 10:
            lines.append(f'　…還有 {len(bad) - 10} 家')
    if ok:
        prof = q("SELECT COUNT(*) n FROM bd_company_profiles WHERE updated_by='AI夜間客戶研究' "
                 f"AND updated_at >= '{esc(since)}'")[0]['n']
        hr = q("SELECT COUNT(*) n FROM bd_hr_contacts WHERE source LIKE '%AI夜間%' "
               f"AND found_at >= '{esc(since)}'")[0]['n']
        lines.append(f'　認識客戶介紹 {prof} 筆、LinkedIn 人資窗口 {hr} 位')
    lines.append('')

    # ── 外商 ──
    fx = q("SELECT company, assigned_to FROM bd_outreach WHERE batch_id LIKE 'nightly-bd-%' "
           f"AND industry LIKE '外商｜%' AND created_at >= '{esc(since)}'")
    lines.append(f'🌏 外商：昨晚新增 {len(fx)} 家（合格 {sum(1 for r in fx if r.get("assigned_to"))} 家）')
    watch = q("SELECT company, country, location, note FROM bd_foreign_watch "
              f"WHERE created_at >= '{esc(since)}' ORDER BY news_date DESC")
    if watch:
        lines.append(f'　外商來台動態（還不能打電話，先追蹤）{len(watch)} 則：')
        for r in watch[:8]:
            where = '／'.join(x for x in (r.get('country'), r.get('location')) if x)
            lines.append(f"　・{r['company']}（{where}）：{r.get('note') or ''}")
        if len(watch) > 8:
            lines.append(f'　…還有 {len(watch) - 8} 則')
    else:
        lines.append('　外商來台動態：昨晚沒有新消息')
    lines.append('')

    # ── 找人選 ──
    src = q("SELECT s.job_slug, COALESCE(j.title, s.job_slug) title, "
            "SUM(CASE WHEN s.grade='A' THEN 1 ELSE 0 END) a, COUNT(*) n "
            "FROM sourced_candidates s LEFT JOIN jobs j ON j.slug=s.job_slug "
            f"WHERE s.source='AI夜間找人' AND s.created_at >= '{esc(since)}' "
            "GROUP BY s.job_slug ORDER BY n DESC")
    total = sum(r['n'] for r in src)
    lines.append(f'👤 找人選：昨晚新增 {total} 位，分布在 {len(src)} 個職缺')
    for r in src:
        lines.append(f"　・{r['title']}：{r['n']} 位（A 級 {r['a']}）")
    lines.append('')

    # ── 精準度 ──
    p = q("SELECT SUM(CASE WHEN fit='fit' THEN 1 ELSE 0 END) f, "
          "SUM(CASE WHEN fit IN ('fit','unfit') THEN 1 ELSE 0 END) g, COUNT(*) n "
          "FROM sourced_candidates WHERE source LIKE 'AI夜間找人%'")[0]
    f, g, n = p.get('f') or 0, p.get('g') or 0, p.get('n') or 0
    if g:
        lines.append(f'🎯 AI 找人精準度：{f}／{g} = {f * 100 // g}%（顧問已評分 {g} 位，累計找了 {n} 位）')
    else:
        lines.append(f'🎯 AI 找人精準度：還沒有顧問評分（累計找了 {n} 位）。在人選卡片標「合適／不合適」，AI 下一輪就會照著調。')
    lines.append('')

    # ── 跑得順不順 ──
    st = run_status(since)
    names = {'bd': '開發客戶', 'sourcing': '找人選'}
    for kind in ('bd', 'sourcing'):
        r = st.get(kind)
        if not r:
            lines.append(f'⚠️ {names[kind]}：昨晚沒有執行紀錄（排程沒跑，或這台機器不是跑夜間工作的那台）')
        elif r.get('exit') != 0:
            lines.append(f"❌ {names[kind]}：沒有跑成功（結束代碼 {r.get('exit')}{'，' + r['note'] if r.get('note') else ''}），請看 nightly_{kind}.log")
        elif r.get('leftover_browsers'):
            lines.append(f"⚠️ {names[kind]}：跑完但留下 {r['leftover_browsers']} 個瀏覽器，程式已自動關掉")
        else:
            lines.append(f"✅ {names[kind]}：正常跑完（{int(r.get('secs', 0)) // 60} 分鐘）")
    return '\n'.join(lines)


def send(text):
    env = {}
    with open(TG_ENV, encoding='utf-8') as fh:
        for ln in fh:
            k, _, v = ln.strip().partition('=')
            if k and not k.startswith('#'):
                env[k] = v.strip('"\'')
    topic = (q("SELECT topic_id FROM bot_topics WHERE key='bd_signals'") or [{}])[0].get('topic_id')
    chat = env['TG_CHAT_ID']
    try:   # 2026-10-02：搬到「step1ne客戶」群組 ☎️ 電訪・日報；沒設照舊
        import tg_route
        _c, _t = tg_route.route('client_calls')
        if _c and _t:
            chat, topic = _c, _t
    except Exception:
        pass
    body = {'chat_id': chat, 'text': text}
    if topic:
        body['message_thread_id'] = topic
    req = urllib.request.Request(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
                                 data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    ok = json.load(urllib.request.urlopen(req, timeout=20)).get('ok')
    print('TG 發送：', ok)
    return ok


def main():
    text = build_text()
    if '--dry-run' in sys.argv:
        print(text)
        print('\n（--dry-run：沒有發送到 TG）')
        return
    if not send(text):
        sys.exit(1)


if __name__ == '__main__':
    main()
