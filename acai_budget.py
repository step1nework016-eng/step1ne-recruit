#!/usr/bin/env python3
"""阿財 API 額度保護（E27b，2026-10-12）。

Jacky 只儲了 US$20。三件事：
  1. 累計 API 花費（token_usage source='api'）到 US$10、US$16 各發一次 TG 提醒；
     到 US$19 主動把模式寫回 cli（~/.config/workflow-os/acai_llm）並通知——
     避免最後一場面談在中途被切斷。
  2. API 回「額度不足」→ 當天第一次發 TG，並把模式寫回 cli（不自動切回來）。
  3. 恢復 API 是人決定的：Jacky 儲值後在 TG 說「阿財改回 API」、或 Mac／我改設定：
         python3 acai_budget.py --resume-api [--budget 40]
     （--budget 同時把預算線改成新儲值後的總額、並從現在起重新累計，提醒也重新武裝）

設定：~/.config/workflow-os/acai_budget.json
    {"budget_usd": 20, "warn_usd": [10, 16], "cutoff_usd": 19, "since": ""}
  since 空白＝從第一筆 API 花費起算；儲值後改 budget_usd（與 since）即可，狀態會自動重置。

⚠️ 這支程式絕不能影響面談：所有動作都包 try/except，從背景執行緒呼叫，不丟例外。
⚠️ 提醒狀態存在這台的 acai_budget.state.json；目前只有 WSL2 走 API，Mac 也開 API 的話要各自提醒（同一份花費是共用 D1 加總，數字一致，只是提醒各發一次）。
"""
import datetime
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CONF_DIR = os.path.expanduser('~/.config/workflow-os')
CONFIG_FILE = os.path.join(CONF_DIR, 'acai_budget.json')
STATE_FILE = os.path.join(CONF_DIR, 'acai_budget.state.json')
MODE_FILE = os.path.join(CONF_DIR, 'acai_llm')
DEFAULT_CONFIG = {'budget_usd': 20.0, 'warn_usd': [10.0, 16.0], 'cutoff_usd': 19.0, 'since': ''}
CHECK_MIN_INTERVAL = 60          # 秒；每次 API 呼叫後最多每分鐘查一次 D1

_lock = threading.Lock()
_last_check = 0.0


def _log(m):
    print(f'[{time.strftime("%H:%M:%S")}] 💵 {m}', flush=True)


def _read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)  # 資安 E44：設定檔只給本人讀
    except OSError:
        pass
    os.replace(tmp, path)


def load_config():
    c = dict(DEFAULT_CONFIG)
    c.update(_read_json(CONFIG_FILE, {}) or {})
    return c


def _sig(cfg):
    return json.dumps([cfg['budget_usd'], cfg['since']])


def load_state(cfg):
    st = _read_json(STATE_FILE, {}) or {}
    if st.get('cfg_sig') != _sig(cfg):        # 儲值後改了預算／起算點 → 提醒重新武裝
        st = {'cfg_sig': _sig(cfg), 'warned': [], 'cutoff_notified': False, 'credit_alert_date': ''}
    return st


def dm(text):
    try:
        import acai_watch
        return bool(acai_watch.dm(text))
    except Exception as e:
        _log(f'TG 通知失敗：{str(e)[:100]}')
        return False


def set_mode(mode):
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(MODE_FILE, 'w', encoding='utf-8') as f:
        f.write(mode + '\n')


def spent_usd(cfg=None, d1fn=None):
    cfg = cfg or load_config()
    if d1fn is None:
        import d1_http
        d1fn = lambda sql: d1_http.query(sql).get('results') or []
    since = cfg.get('since') or ''
    where = "source='api'" + (f" AND created_at >= '{since}'" if since else '')
    r = d1fn(f"SELECT COALESCE(SUM(cost_usd),0) AS usd FROM token_usage WHERE {where}")
    return float((r[0]['usd'] if r else 0) or 0)


def check(d1fn=None, dm_fn=None, mode_fn=None, force=False):
    """累計花費對預算線。回傳做了哪些動作（測試用）。永遠不丟例外。"""
    global _last_check
    acts = []
    try:
        with _lock:
            if not force and time.time() - _last_check < CHECK_MIN_INTERVAL:
                return acts
            _last_check = time.time()
        dm_fn = dm_fn or dm
        mode_fn = mode_fn or set_mode
        cfg = load_config()
        st = load_state(cfg)
        spent = spent_usd(cfg, d1fn)
        budget = float(cfg['budget_usd'])
        for w in sorted(float(x) for x in cfg['warn_usd']):
            if spent >= w and w not in st['warned']:
                st['warned'].append(w)
                dm_fn(f'💵 阿財 API 花費提醒：累計已 US${spent:.2f}（提醒線 US${w:g}，預算 US${budget:g}，剩約 US${max(0, budget - spent):.2f}）。'
                      f'到 US${cfg["cutoff_usd"]:g} 會自動改回 CLI。')
                acts.append(('warn', w))
        if spent >= float(cfg['cutoff_usd']) and not st['cutoff_notified']:
            st['cutoff_notified'] = True
            mode_fn('cli')
            dm_fn(f'💵 阿財 API 花費已 US${spent:.2f}，達切換線 US${cfg["cutoff_usd"]:g}：已自動改回 CLI（避免面談中途額度用完被切斷）。'
                  f'儲值後回我「阿財改回 API」才會切回去。')
            acts.append(('cutoff', spent))
        _write_json(STATE_FILE, st)
    except Exception as e:
        _log(f'預算檢查失敗（不影響面談）：{str(e)[:120]}')
    return acts


def check_async():
    threading.Thread(target=check, daemon=True).start()


def on_credit_exhausted(reason='', dm_fn=None, mode_fn=None, today=None):
    """claude_api 偵測到額度不足時呼叫。當天只發一次 TG，並把模式寫回 cli。"""
    try:
        dm_fn = dm_fn or dm
        mode_fn = mode_fn or set_mode
        cfg = load_config()
        st = load_state(cfg)
        mode_fn('cli')
        day = today or datetime.date.today().isoformat()
        if st.get('credit_alert_date') != day:
            st['credit_alert_date'] = day
            _write_json(STATE_FILE, st)
            dm_fn('💵 阿財 API 額度用完，已自動改回 CLI，面談不受影響。要繼續用 API 請到 Console 儲值，再回我「阿財改回 API」。')
            return True
    except Exception as e:
        _log(f'額度不足通知失敗：{str(e)[:120]}')
    return False


def resume_api(new_budget=None):
    cfg = load_config()
    if new_budget:
        cfg['budget_usd'] = float(new_budget)
        cfg['cutoff_usd'] = round(float(new_budget) - 1, 2)
        cfg['warn_usd'] = [round(float(new_budget) * 0.5, 2), round(float(new_budget) * 0.8, 2)]
        cfg['since'] = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
        _write_json(CONFIG_FILE, cfg)
    set_mode('api')
    print('已設為 api；預算設定：', json.dumps(cfg, ensure_ascii=False))


if __name__ == '__main__':
    if '--resume-api' in sys.argv:
        b = sys.argv[sys.argv.index('--budget') + 1] if '--budget' in sys.argv else None
        resume_api(b)
    else:
        cfg = load_config()
        print(f'設定：{json.dumps(cfg, ensure_ascii=False)}')
        print(f'累計 API 花費：US${spent_usd(cfg):.4f}')
