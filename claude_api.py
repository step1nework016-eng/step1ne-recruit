#!/usr/bin/env python3
"""阿財對談改走 Anthropic API（E27，2026-10-12）。

為什麼：CLI 版每次呼叫要開一個 claude 程序（開機約數秒）、用量要事後猜；API 版直接 HTTPS，
回覆有真實 usage，也不吃 Claude Code 訂閱額度。

⚠️ 金鑰只放 ~/.config/workflow-os/anthropic.env（ANTHROPIC_API_KEY），這支程式只在記憶體裡讀，
   不印、不寫 log、不放進例外訊息（錯誤內文一律把 sk-ant-… 遮掉）。
⚠️ 一鍵切換：
     export ACAI_LLM=cli|api            （環境變數，優先）
     ~/.config/workflow-os/acai_llm     （檔案，一行 api 或 cli）
   預設 cli——沒明確設成 api 就完全維持舊行為。
⚠️ API 不可用（額度不足／金鑰錯／模型不存在／連線失敗）時，**這一輪自動改走 CLI**，面談不中斷；
   不可用類的錯誤會冷卻 10 分鐘不再試，避免每一輪都白打一次。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
ENV_FILE = os.path.expanduser('~/.config/workflow-os/anthropic.env')
MODE_FILE = os.path.expanduser('~/.config/workflow-os/acai_llm')
COOLDOWN_SEC = 600

# 牌價（美元 / 百萬 token）：輸入、輸出、快取寫入、快取讀取。
# 2026-10-12 E27b 統一：由 10/8 CLI 回報的 costUSD 反推（claude-sonnet-5：輸入 2／輸出 10／1 小時快取寫入 4／快取讀 0.2）。
# 以 API 回應實際使用的模型為準（前綴比對）；對不到的模型用預設價並標示「未確認」。
# ⚠️ 打通後拿一場的 Console 帳單對一次。目前 API 版沒有用 cache_control，快取寫入量是 0。
PRICES = {'claude-sonnet-5': (2.0, 10.0, 4.0, 0.2)}
DEFAULT_PRICE = (2.0, 10.0, 4.0, 0.2)

# 額度不足時的回呼（由 interview_daemon 掛上 acai_budget.on_credit_exhausted）
on_credit_exhausted = None

_down_until = 0.0
_down_reason = ''


class ApiTimeout(Exception):
    """請求逾時——跟 CLI 逾時同一種處理（重跑）。"""


class ApiFallback(Exception):
    """API 這次不能用，呼叫端請改走 CLI。"""


def _redact(s):
    return re.sub(r'sk-ant-[A-Za-z0-9_\-]+', 'sk-ant-***', str(s))


def mode():
    m = (os.environ.get('ACAI_LLM') or '').strip().lower()
    if not m:
        try:
            with open(MODE_FILE, encoding='utf-8') as f:
                m = f.read().strip().lower()
        except OSError:
            m = ''
    return 'api' if m == 'api' else 'cli'


def _load_env():
    """回傳 (key, workspace_id)；讀不到回 (None, None)。"""
    key = ws = None
    try:
        with open(ENV_FILE, encoding='utf-8-sig') as f:
            for line in f:
                m = re.match(r'\s*(?:export\s+)?([A-Z_]+)=(.*?)\s*$', line)
                if not m:
                    continue
                v = m.group(2).strip().strip('"\'')
                if m.group(1) == 'ANTHROPIC_API_KEY':
                    key = v or None
                elif m.group(1) == 'ANTHROPIC_WORKSPACE_ID':
                    ws = v or None
    except OSError:
        pass
    return key, ws


def use_api():
    """這一輪該不該走 API。"""
    if mode() != 'api':
        return False
    if time.time() < _down_until:
        return False
    return True


def cooldown_info():
    return _down_reason, max(0, int(_down_until - time.time()))


def _mark_down(reason):
    global _down_until, _down_reason
    _down_until = time.time() + COOLDOWN_SEC
    _down_reason = reason


def price_for(model):
    """(價格, 是否確認過)。前綴最長者優先。"""
    best = None
    for k in PRICES:
        if str(model or '').startswith(k) and (best is None or len(k) > len(best)):
            best = k
    return (PRICES[best], True) if best else (DEFAULT_PRICE, False)


def cost_usd(usage, model=None):
    (pi, po, pcw, pcr), _ = price_for(model)
    return (int(usage.get('input_tokens') or 0) * pi
            + int(usage.get('output_tokens') or 0) * po
            + int(usage.get('cache_creation_input_tokens') or 0) * pcw
            + int(usage.get('cache_read_input_tokens') or 0) * pcr) / 1e6


def call(prompt, model, timeout, max_tokens=4096, opener=None):
    """送一則 user 訊息，回 {'text','usage','cost_usd','ms','model'}。
    逾時 → ApiTimeout；其他不能用 → ApiFallback（並進入冷卻）。opener 供測試注入。"""
    key, ws = _load_env()
    if not key:
        _mark_down('金鑰檔讀不到或沒有 ANTHROPIC_API_KEY')
        raise ApiFallback(_down_reason)
    headers = {'x-api-key': key, 'anthropic-version': API_VERSION, 'content-type': 'application/json'}
    if ws:
        headers['anthropic-workspace-id'] = ws
    body = json.dumps({'model': model, 'max_tokens': max_tokens,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
    req = urllib.request.Request(API_URL, data=body, headers=headers, method='POST')
    t0 = time.time()
    try:
        with (opener or urllib.request.urlopen)(req, timeout=max(5, timeout)) as f:
            data = json.loads(f.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try:
            txt = _redact(e.read().decode('utf-8', 'replace'))[:300]
        except Exception:
            txt = ''
        low = txt.lower()
        if e.code in (401, 403):
            _mark_down(f'金鑰被拒（HTTP {e.code}）')
        elif e.code == 400 and ('credit' in low or 'balance' in low):
            _mark_down('API 額度不足（Console 要 Add funds）')
            if on_credit_exhausted:
                try:
                    on_credit_exhausted(_down_reason)
                except Exception:
                    pass
        elif e.code == 400 and 'workspace' in low:
            _mark_down('金鑰沒綁工作區（要設 ANTHROPIC_WORKSPACE_ID 或重建金鑰）')
        elif e.code in (400, 404):
            _mark_down(f'請求被拒或模型不存在（HTTP {e.code}）：{txt[:120]}')
        elif e.code in (429, 500, 502, 503, 529):
            raise ApiFallback(f'API 暫時不可用（HTTP {e.code}），這一輪改走 CLI')
        else:
            _mark_down(f'HTTP {e.code}：{txt[:120]}')
        raise ApiFallback(_down_reason or f'HTTP {e.code}')
    except TimeoutError as e:
        raise ApiTimeout(str(e))
    except urllib.error.URLError as e:
        if isinstance(getattr(e, 'reason', None), TimeoutError) or 'timed out' in str(e):
            raise ApiTimeout(_redact(e))
        raise ApiFallback(f'連不上 API：{_redact(e)}')
    except (json.JSONDecodeError, OSError) as e:
        raise ApiFallback(f'API 回應讀取失敗：{_redact(e)}')
    parts = [c.get('text', '') for c in (data.get('content') or []) if c.get('type') == 'text']
    usage = data.get('usage') or {}
    return {'text': ''.join(parts), 'usage': usage, 'model': data.get('model') or model,
            'cost_usd': cost_usd(usage, data.get('model') or model),
            'ms': int((time.time() - t0) * 1000), 'stop_reason': data.get('stop_reason')}
