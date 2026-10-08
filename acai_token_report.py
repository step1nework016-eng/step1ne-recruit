#!/usr/bin/env python3
"""阿財每場面談花多少 token／多少錢／每輪多慢——改走 API 之前的實測依據。

2026-10-08 Jacky：「阿財可能要轉成用 API 看能不能更快，先監控一場下來會花多少 token，
建置好後再改成 API 跑」。只有阿財要改，其他照舊用 CLI。

資料來源：token_usage 裡 source='cli_json' 的列（interview_daemon 每次呼叫 AI 由 CLI 直接回報，
不是估算）。舊的列是事後猜 session 檔記的，數字不可靠，這裡不用。

三種數字，意思不一樣：
  ・實際用量：現在用 CLI 跑真的花掉的 token（含 CLI 自己每次都要帶的一大段系統說明）
  ・CLI 牌價金額：CLI 自己照 API 牌價算的錢（現在是訂閱，沒有真的付這筆）
  ・改 API 推估：扣掉 CLI 固定附帶的那段之後，阿財自己的提示詞＋回覆，照 API 牌價算
    → 這是估計值。CLI 固定附帶多少 token 是用一次極短呼叫實測出來的（存在快取檔，7 天重測）

用法：
    python3 acai_token_report.py              # 最近 7 天每場
    python3 acai_token_report.py --days 30
    python3 acai_token_report.py --app <application_id>
"""
import argparse, json, os, subprocess, sys, time, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402

# 單價（美元／每百萬 token）——不是憑記憶：由 CLI 回報的 costUSD 反推（2026-10-08，claude-sonnet-5）
#   260401 個 1 小時快取寫入 = $1.041688 → 4.0；20588 快取讀取＋2291 寫入＋13 輸出 = $0.0134156 → 讀 0.2、輸出 10
PRICE = {'input': 2.0, 'output': 10.0, 'cache_read': 0.2, 'cache_write_1h': 4.0}
BASELINE_CACHE = os.path.expanduser('~/.config/workflow-os/acai-cli-baseline.json')


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def rows(sql):
    return d1_http.query(sql).get('results') or []


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--app')
    a = ap.parse_args()
    where = f"t.application_id={q(a.app)}" if a.app else f"t.created_at > datetime('now','+8 hours','-{a.days} days')"
    data = rows(f"""SELECT t.*, ap.name FROM token_usage t LEFT JOIN applications ap ON ap.id=t.application_id
                    WHERE t.source='cli_json' AND {where} ORDER BY t.created_at""")
    if not data:
        print('還沒有新版紀錄（阿財重啟後的面談才會有）')
        return
    base = cli_baseline()
    per = {}
    for r in data:
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


if __name__ == '__main__':
    main()
