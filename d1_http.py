"""直接用 HTTP 打 Cloudflare D1，不再每次查詢都開一個 node 行程。

2026-09-08 加。原本每支常駐 daemon 查一次 D1 就 subprocess 起一次
`npx wrangler d1 execute`——npx 再拉起 node，一次約 100MB、還要冷啟動。
阿財面談 8 秒、阿福健檢 8 秒、職缺配對 6 秒各輪詢一次，一分鐘要冷啟動
快 30 次 node。實測那台 8GB 的機器負載衝到 17，交換檔吃掉 5GB。

改成直接打 Cloudflare 的 D1 REST API：同一個查詢不用開任何子行程。

⚠️ 回傳格式刻意做成跟 `wrangler --json` 的 `[0]` 完全一樣
（`{'results': [...], 'meta': {...}, 'success': True}`），
呼叫端一行都不用改——鎖機制在看 `meta.changes`，格式錯掉會靜默失效。

⚠️ 任何一種失敗都會 raise，讓呼叫端退回原本的 wrangler 走法。
   面談是候選人正在等的即時流程，寧可慢也不能斷。
"""
import json
import os
import urllib.request
import urllib.error

DB_ID = '67077b4b-42d9-4086-8d04-b3658a89cffd'   # step1ne-recruit
_CFG = None


def _cfg():
    """讀 ~/.config/workflow-os/cf.env 拿 token 與 account id（讀一次就快取）。"""
    global _CFG
    if _CFG is not None:
        return _CFG
    path = os.path.expanduser('~/.config/workflow-os/cf.env')
    vals = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    vals[k.strip()] = v.strip()
    except OSError:
        _CFG = (None, None)
        return _CFG
    _CFG = (vals.get('CLOUDFLARE_API_TOKEN'), vals.get('CLOUDFLARE_ACCOUNT_ID'))
    return _CFG


def available():
    tok, acc = _cfg()
    return bool(tok and acc)


def query(sql, timeout=60):
    """跑一段 SQL，回傳跟 wrangler --json 的 [0] 同樣形狀的 dict。"""
    tok, acc = _cfg()
    if not (tok and acc):
        raise RuntimeError('cf.env 裡沒有 CLOUDFLARE_API_TOKEN／ACCOUNT_ID')
    req = urllib.request.Request(
        f'https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database/{DB_ID}/query',
        data=json.dumps({'sql': sql}).encode('utf-8'),
        headers={'authorization': f'Bearer {tok}', 'content-type': 'application/json'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'D1 HTTP {e.code}: {e.read()[:300]}')
    if not body.get('success'):
        raise RuntimeError(f'D1 錯誤：{json.dumps(body.get("errors"), ensure_ascii=False)[:300]}')
    res = body.get('result') or []
    if not res:
        raise RuntimeError('D1 沒有回傳 result')
    # 多段 SQL 時 D1 每段各給一個結果；wrangler 的呼叫端只看第一個，維持一致
    return res[0]
