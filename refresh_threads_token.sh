#!/bin/bash
# Threads 長效 token 每 60 天會過期，這支每週跑一次，把 token 續期
# （Meta 規則：token 存在滿 24 小時後就能續，續完效期重新從當下算 60 天，
# 每週跑一次遠遠夠用，不會卡在快過期才續）。
#
# 2026-08-17 改：原本只處理 Jacky 一組（存在 tokens.env + wrangler secret），
# 現在 EYLISE／Phoebe／DR 三位顧問也各自有自己的 Threads 帳號，token 存在
# D1 的 social_accounts 表裡，不是 tokens.env——這支要一次把「所有」還活著的
# threads 帳號都續過一輪，不能只顧 Jacky 那組，不然新增的三組會沒人管，
# 60 天後悄悄失效，發文才會發現。
#
# Jacky 那組比較特殊：她的 token 同時存在兩個地方（tokens.env／wrangler secret
# 給沒指定帳號的舊職缺當退回值；social_accounts 給有指定帳號的新職缺用）——
# 續完 D1 那份之後，如果這筆剛好是 Jacky，額外同步寫回 tokens.env／secret，
# 兩邊才不會兜不起來。
set -euo pipefail
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$HOME/工作流程技能包/step1ne-recruit"

TOKENS_ENV="$HOME/.config/workflow-os/tokens.env"
TG_ENV="$HOME/.config/workflow-os/step1ne-tg.env"
LOG() { echo "[$(date '+%H:%M:%S')] $1"; }

python3 <<'PYEOF'
import json, os, subprocess, sys, urllib.request

HERE = os.path.expanduser('~/工作流程技能包/step1ne-recruit')
TOKENS_ENV = os.path.expanduser('~/.config/workflow-os/tokens.env')
TG_ENV = os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')


def log(msg):
    import datetime
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def env_with_cf():
    env = dict(os.environ)
    p = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v.strip().strip("'\"")
    return env


def d1(sql):
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', 'step1ne-recruit', '--remote', '--json', f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env_with_cf(), timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-500:])
    out = r.stdout[r.stdout.index('['):]
    data = json.loads(out)
    return data[0].get('results', []) if data else []


def q(v):
    return "'" + str(v).replace("'", "''") + "'"


def tg_alert(text):
    if not os.path.exists(TG_ENV):
        return
    env = {}
    for line in open(TG_ENV, encoding='utf-8'):
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            env[k] = v.strip().strip("'\"")
    if not env.get('TG_BOT_TOKEN') or not env.get('TG_CHAT_ID'):
        return
    data = json.dumps({'chat_id': int(env['TG_CHAT_ID']), 'text': text}).encode('utf-8')
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage",
        data=data, headers={'content-type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def sync_jacky_fallback(new_token):
    """Jacky 這組同時餵給沒指定帳號的舊職缺（tokens.env + wrangler secret），
    D1 續完要順便同步這兩個地方，不然三邊會兜不起來。"""
    lines = open(TOKENS_ENV, encoding='utf-8').read().splitlines()
    out = []
    for line in lines:
        if line.startswith('THREADS_ACCESS_TOKEN='):
            out.append('THREADS_ACCESS_TOKEN=' + new_token)
        else:
            out.append(line)
    open(TOKENS_ENV, 'w', encoding='utf-8').write('\n'.join(out) + '\n')

    r = subprocess.run(
        ['npx', 'wrangler', 'secret', 'put', 'THREADS_ACCESS_TOKEN'],
        cwd=HERE, input=new_token, capture_output=True, text=True, env=env_with_cf(), timeout=60)
    return r.returncode == 0


def refresh_one(acc):
    cur_token = acc['access_token']
    label = acc['label']
    if not cur_token:
        return None  # 還沒設定的佔位帳號，跳過
    try:
        with urllib.request.urlopen(
            f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={cur_token}",
            timeout=20
        ) as r:
            resp = json.loads(r.read())
    except Exception as e:
        return f'{label}：請求失敗（{e}）'

    new_token = resp.get('access_token')
    if not new_token:
        return f'{label}：{json.dumps(resp, ensure_ascii=False)[:200]}'

    d1(f"UPDATE social_accounts SET access_token={q(new_token)} WHERE id={acc['id']}")
    log(f'✅ {label} 續期成功')

    if label == 'Jacky - Threads':
        if not sync_jacky_fallback(new_token):
            return f'{label}：D1 續期成功，但 wrangler secret 同步失敗（見 /tmp/threads_refresh_wrangler.log）'
        log('✅ Jacky 這組已同步回 tokens.env／Worker secret（給沒指定帳號的舊職缺用）')
    return None


accounts = d1("SELECT id, label, access_token FROM social_accounts WHERE platform='threads' AND is_active=1")
if not accounts:
    log('沒有找到任何啟用中的 Threads 帳號，跳過')
    sys.exit(0)

failures = []
for acc in accounts:
    err = refresh_one(acc)
    if err:
        failures.append(err)

if failures:
    log('❌ 有帳號續期失敗：' + '；'.join(failures))
    tg_alert('⚠️ Threads token 自動續期，有帳號失敗，可能快過期了，要手動去 Meta 後台重新產生：\n' + '\n'.join(failures))
    sys.exit(1)

log(f'✅ 全部 {len(accounts)} 組 Threads token 都續期成功')
PYEOF
