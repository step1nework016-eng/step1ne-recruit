#!/usr/bin/env bash
# 網域獵人每日排程（E34，WSL2 systemd timer 每天 09:30 叫它）
#   git pull → claude -p 跑「每日任務提示詞」（查分數／加名單／查前世）→ 同步看板＋有新的乾淨網域才發 TG
# 用法：run_daily.sh [--check] [--no-tg]
#   --check：只確認 claude 叫得起來、Ubersuggest 有連上（不花查詢額度），不跑任務
#   --no-tg：照跑，但最後不發 TG（第一次手動跑用）
set -uo pipefail
export TZ=Asia/Taipei
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
export DOMAIN_FINDER_HOME="${DOMAIN_FINDER_HOME:-$HOME/aijob-automation/state/domain_finder}"
MODEL="${DF_MODEL:-claude-sonnet-5}"
CHECK=""; TGFLAG="--tg"
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --no-tg) TGFLAG="" ;;
  esac
done
mkdir -p "$DOMAIN_FINDER_HOME" "$HOME/aijob-automation/logs"
echo "== $(date '+%F %T') 網域獵人 ${CHECK:+(check)} 資料→$DOMAIN_FINDER_HOME"

# --setting-sources ''：不吃全域 CLAUDE.md（agentacct 那段會蓋掉任務）；
# 也因此拿不到 settings.json 的權限清單，所以要 bypassPermissions 才能跑 python／sleep。
# Ubersuggest 是用 `claude mcp add --scope user` 加的，存在 ~/.claude.json，不受 --setting-sources 影響。
# 不載入 claude.ai 帳號上的雲端連接器：那邊的 Ubersuggest 是 Mac 那個帳號，用了會吃同一份每天額度
export ENABLE_CLAUDEAI_MCP_SERVERS=false
CLAUDE_ARGS=(--model "$MODEL" --setting-sources '' --permission-mode bypassPermissions --output-format text)

if [ -n "$CHECK" ]; then
  cd "$HERE" && claude -p "用 ToolSearch 找 ubersuggest 的工具。如果有 mcp__ubersuggest__auth_status，呼叫它（不要呼叫其他會用到額度的工具）。最後只回一行：『ubersuggest 工具：有／沒有；登入狀態：…（帳號 email 只顯示前三個字）』" "${CLAUDE_ARGS[@]}"
  exit $?
fi

git -C "$REPO" pull -q --ff-only origin main || echo "⚠️ git pull 失敗，用本機現有版本繼續"
python3 -c "import sys;sys.path.insert(0,'$HERE');import df_paths" || { echo "!! 資料夾建不起來"; exit 1; }

# 每日任務提示詞.md 裡「---」之後那段才是任務本身；把路徑填進去
PROMPT="$(python3 - "$HERE" "$DOMAIN_FINDER_HOME" <<'PY'
import sys, pathlib
code, home = sys.argv[1], sys.argv[2]
t = (pathlib.Path(code) / "每日任務提示詞.md").read_text(encoding="utf-8")
body = t.split("\n---\n", 1)[1] if "\n---\n" in t else t
print(body.replace("{{CODE}}", code).replace("{{HOME}}", home))
PY
)"
# 一輪 11 個要等 5 分鐘（sleep 290），Bash 預設 2 分鐘逾時會把 sleep 砍掉，所以放寬到 6 分鐘
export BASH_DEFAULT_TIMEOUT_MS=360000 BASH_MAX_TIMEOUT_MS=600000
# 一天 280 個 ≈ 26 輪 × 5 分鐘 ≈ 2.5 小時，上限給 4 小時
cd "$HERE" && timeout 4h claude -p "$PROMPT" "${CLAUDE_ARGS[@]}"
rc=$?
echo "-- claude 結束 rc=$rc"

# 不管 claude 跑得順不順，名單有什麼就同步什麼（看板至少反映最新狀態）
python3 "$HERE/sync_dashboard.py" $TGFLAG || echo "⚠️ 看板同步失敗"
echo "== 完成 $(date '+%F %T')"
