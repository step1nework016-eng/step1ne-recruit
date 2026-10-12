#!/usr/bin/env python3
"""阿財每場面談花多少 token／多少錢／每輪多慢。

2026-10-08 Jacky：「阿財可能要轉成用 API 看能不能更快，先監控一場下來會花多少 token，
建置好後再改成 API 跑」。只有阿財要改，其他照舊用 CLI。
2026-10-12 E27b：加入 source='api' 的列、每位人選的美元＋台幣費用、每天 21:30 TG 推播。

資料來源：token_usage 裡 source in ('cli_json','api') 的列（interview_daemon 每次呼叫 AI 自己記，
不是估算；api 的金額用 claude_api 的牌價算，cli_json 的金額是 CLI 自己回報的牌價金額）。
舊的列（source 空）是事後猜 session 檔記的，數字不可靠，這裡不用。

區塊：
  ① 每一場（CLI）：實際用量、CLI 牌價、改 API 推估、每輪等待秒數（原本的報表）
  ② 每位人選：人選、職缺、面談日期、呼叫次數、token（輸入／輸出／快取）、美元、台幣
     台幣用執行當下抓的即時匯率，印出匯率、來源與時間；抓不到只給美元並註明「匯率未取得」。

用法：
    python3 acai_token_report.py              # 最近 7 天
    python3 acai_token_report.py --days 30
    python3 acai_token_report.py --app <application_id>
    python3 acai_token_report.py --daily-push # 今日 API 花費 TG 私訊 Jacky（沒有 API 花費就不發）
"""
import argparse, datetime, json, os, subprocess, sys, time, shutil, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claude_api  # noqa: E402

# 單價統一放在 claude_api.PRICES（E27b）：輸入 2／輸出 10／1 小時快取寫入 4／快取讀 0.2（美元／百萬 token，claude-sonnet-5）
_P, _ = claude_api.price_for('claude-sonnet-5')
PRICE = {'input': _P[0], 'output': _P[1], 'cache_write_1h': _P[2], 'cache_read': _P[3]}
BASELINE_CACHE = os.path.expanduser('~/.config/workflow-os/acai-cli-baseline.json')
FX_URL = 'https://open.er-api.com/v6/latest/USD'


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def rows(sql):
    import d1_http
    return d1_http.query(sql).get('results') or []


def fetch_fx(opener=None):
    """即時 USD→TWD。回 {'rate','source','at'}；抓不到回 None（呼叫端只給美元）。不准寫死數字。"""
    try:
        with (opener or urllib.request.urlopen)(FX_URL, timeout=15) as f:
            d = json.loads(f.read().decode('utf-8'))
        rate = float((d.get('rates') or {}).get('TWD'))
        if d.get('result') not in (None, 'success') or rate <= 0:
            return None
        return {'rate': rate, 'source': 'open.er-api.com', 'at': d.get('time_last_update_utc') or ''}
    except Exception:
        return None


def cli_baseline():
    """CLI 每次呼叫固定附帶多少 token（跟 interview_daemon 一樣的禁用工具設定）。"""
    try:
        c = json.load(open(BASELINE_CACHE))
        if time.time() - c['at'] < 7 * 86400:
            return c['tokens']
    except Exception:
        pass
    import importlib.util
    spec = importlib.util.spec_from_file_location('idm', os.path.join(HERE, 'interview_daemon.py'))
    m = importlib.util.module_from_spec(spec)
    sys.argv = sys.argv[:1]
    spec.loader.exec_module(m)
    r = subprocess.run([shutil.which('claude') or 'claude', '-p', '--model', m.TALK_MODEL, *m.NO_TOOLS,
                        '--output-format', 'json'], input='回 ok', capture_output=True, text=True, timeout=120)
    us = json.loads(r.stdout)['usage']
    tok = sum(int(us.get(k) or 0) for k in ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')) - 5
    os.makedirs(os.path.dirname(BASELINE_CACHE), exist_ok=True)
    json.dump({'at': time.time(), 'tokens': tok}, open(BASELINE_CACHE, 'w'))
    return tok


# ── ② 每位人選 ──────────────────────────────────────────────
def build_people(data):
    """data：token_usage 列（要帶 name、job_title）。回 list，依面談日期排序。"""
    per = {}
    for r in data:
        p = per.setdefault(r['application_id'], {
            'app': r['application_id'], 'name': r.get('name') or '（未知）', 'job': r.get('job_title') or r.get('job_slug') or '（未知職缺）',
            'date': str(r['created_at'])[:10], 'calls': 0, 'in': 0, 'out': 0, 'cw': 0, 'cr': 0, 'usd': 0.0, 'sources': set(),
            'unknown_price': False})
        p['calls'] += int(r.get('calls') or 1)
        p['in'] += int(r.get('input_tokens') or 0)
        p['out'] += int(r.get('output_tokens') or 0)
        p['cw'] += int(r.get('cache_creation_input_tokens') or 0)
        p['cr'] += int(r.get('cache_read_input_tokens') or 0)
        p['usd'] += float(r.get('cost_usd') or 0)
        p['sources'].add(r.get('source') or '?')
        if r.get('source') == 'api' and not claude_api.price_for(r.get('model'))[1]:
            p['unknown_price'] = True
        p['date'] = min(p['date'], str(r['created_at'])[:10])
    return sorted(per.values(), key=lambda p: (p['date'], p['name']))


def fmt_twd(usd, fx):
    return f"NT${usd * fx['rate']:,.0f}" if fx else 'NT$（匯率未取得）'


def fx_line(fx):
    if not fx:
        return '匯率未取得（只列美元）'
    return f"匯率 1 USD = {fx['rate']:.4f} TWD（來源 {fx['source']}，{fx['at'] or '時間未提供'}；抓取於 {time.strftime('%Y-%m-%d %H:%M')}）"


def format_people(people, fx):
    out = [f'■ 每位人選的費用（{fx_line(fx)}）',
           '  註：source=api 的金額是我們按牌價算的；source=cli_json 是 CLI 回報的牌價金額（現在是訂閱，沒有真的付這筆）']
    for p in people:
        src = '+'.join(sorted(p['sources']))
        out.append(f"  {p['name']}｜{p['job']}｜{p['date']}｜呼叫 {p['calls']} 次｜"
                   f"輸入 {p['in']:,}／輸出 {p['out']:,}／快取寫 {p['cw']:,}／快取讀 {p['cr']:,}｜"
                   f"US${p['usd']:.4f}｜{fmt_twd(p['usd'], fx)}｜{src}"
                   + ('｜⚠️模型單價未確認' if p['unknown_price'] else ''))
    tot = sum(p['usd'] for p in people)
    out.append(f"  合計 US${tot:.4f}｜{fmt_twd(tot, fx)}")
    return '\n'.join(out)


def daily_text(people_api_today, fx, spent, budget, day):
    """今日 API 花費 TG 訊息；沒有 API 花費回 None（不推）。"""
    if not people_api_today:
        return None
    lines = [f'💵 今日阿財 API 花費（{day}）']
    for p in people_api_today:
        lines.append(f"・{p['name']}｜{p['job']}｜US${p['usd']:.2f}｜{fmt_twd(p['usd'], fx)}")
    tot = sum(p['usd'] for p in people_api_today)
    lines.append(f"今日合計 US${tot:.2f}｜{fmt_twd(tot, fx)}")
    lines.append(f"累計 US${spent:.2f} / 預算 US${budget:g}，剩約 US${max(0, budget - spent):.2f}（{fmt_twd(max(0, budget - spent), fx)}）")
    lines.append(fx_line(fx))
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--app')
    ap.add_argument('--daily-push', action='store_true')
    a = ap.parse_args()
    fx = fetch_fx()
    base_sql = ("SELECT t.*, ap.name, ap.job_title, ap.job_slug FROM token_usage t "
                "LEFT JOIN applications ap ON ap.id=t.application_id WHERE ")
    if a.daily_push:
        import acai_budget
        day = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
        data = rows(base_sql + f"t.source='api' AND substr(t.created_at,1,10)={q(day)} ORDER BY t.created_at")
        cfg = acai_budget.load_config()
        txt = daily_text(build_people(data), fx, acai_budget.spent_usd(cfg), float(cfg['budget_usd']), day)
        if not txt:
            print('今天沒有 API 花費，不推播')
            return
        print(txt)
        print('已送出' if acai_budget.dm(txt) else '⚠️ TG 沒送成')
        return
    where = f"t.application_id={q(a.app)}" if a.app else f"t.created_at > datetime('now','+8 hours','-{a.days} days')"
    data = rows(base_sql + f"t.source IN ('cli_json','api') AND {where} ORDER BY t.created_at")
    if not data:
        print('還沒有新版紀錄（阿財重啟後的面談才會有）')
        return
    cli = [r for r in data if r.get('source') == 'cli_json']
    if cli:
        base = cli_baseline()
        per = {}
        for r in cli:
            per.setdefault(r['application_id'], []).append(r)
        print(f'CLI 每次呼叫固定附帶約 {base:,} token（實測）\n')
        for app, rs in per.items():
            tin = sum((r['input_tokens'] or 0) + (r['cache_creation_input_tokens'] or 0) + (r['cache_read_input_tokens'] or 0) for r in rs)
            tout = sum(r['output_tokens'] or 0 for r in rs)
            calls = sum(r['calls'] or 1 for r in rs)
            cli_usd = sum(r['cost_usd'] or 0 for r in rs)
            api_in = max(0, tin - base * calls)
            api_usd = (api_in * PRICE['input'] + tout * PRICE['output']) / 1e6
            talks = [r for r in rs if r['call_type'] in ('talk', 'talk_retry')]
            avg = lambda k: sum(r[k] or 0 for r in talks) / len(talks) / 1000 if talks else 0
            print(f"■ {rs[0]['name'] or app}（{rs[0]['host'] or '?'}，{rs[0]['created_at'][:16]} 起）")
            print(f"  呼叫 {calls} 次（對話 {len(talks)} 輪）")
            print(f"  實際用量：讀入 {tin:,}／寫出 {tout:,} token；CLI 牌價 ${cli_usd:.2f}")
            print(f"  改 API 推估：讀入約 {api_in:,} token，約 ${api_usd:.2f}（沒算 API 快取折扣，是上限）")
            if talks:
                print(f"  每輪等待：候選人實際等 {avg('wall_ms'):.1f} 秒，其中模型本身 {avg('api_ms'):.1f} 秒"
                      f"（差的 {avg('wall_ms') - avg('api_ms'):.1f} 秒是 CLI 自己開機、處理的時間，改 API 可以省掉）")
            print()
    print(format_people(build_people(data), fx))


if __name__ == '__main__':
    main()
