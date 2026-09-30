#!/usr/bin/env bash
# Step1ne 夜間工作：開發客戶名單（bd）／幫職缺找人選（sourcing）。
#
# 用法：
#   run_nightly.sh bd        [--limit 30]      # 每晚新增最多 N 家潛在客戶（預設 30）
#   run_nightly.sh sourcing  [--per-job 10]    # 每個職缺最多新增 N 位人選（預設 10）
#   加 --dry-run：只印出這次會用的提示詞與指令，不呼叫 claude（本機檢查用）
#
# 由 systemd timer 觸發（見 systemd/）。同一種工作同時只會跑一個（鎖檔）。
# log：~/aijob-automation/logs/nightly_<種類>.log
# 每次跑完在 ~/aijob-automation/logs/nightly_runs.jsonl 記一行（早上摘要會讀）。
#
# ⚠️ 背景跑 claude -p 一定要 --setting-sources ''（不然全域 CLAUDE.md 會蓋掉任務），
#    需要工具所以要配 --permission-mode bypassPermissions。模型一律寫全名。
set -uo pipefail

KIND="${1:-}"
shift || true
LIMIT=30
PER_JOB=10
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --limit)   LIMIT="${2:?--limit 要接數字}"; shift 2 ;;
    --per-job) PER_JOB="${2:?--per-job 要接數字}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "看不懂的參數：$1" >&2; exit 2 ;;
  esac
done
case "$KIND" in
  bd|sourcing) ;;
  *) echo "用法：$0 bd|sourcing [--limit N] [--per-job N] [--dry-run]" >&2; exit 2 ;;
esac
[[ "$LIMIT" =~ ^[0-9]+$ && "$PER_JOB" =~ ^[0-9]+$ ]] || { echo "上限要是數字" >&2; exit 2; }

export TZ="${TZ:-Asia/Taipei}"
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/current/bin:$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

PIPE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$PIPE_DIR")"
MODEL="${NIGHTLY_MODEL:-claude-sonnet-5}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
LOG_DIR="$HOME/aijob-automation/logs"
LOCK_DIR="$HOME/aijob-automation/locks"
LOG="$LOG_DIR/nightly_${KIND}.log"
RUNS="$LOG_DIR/nightly_runs.jsonl"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

if [ "$KIND" = bd ]; then
  PROMPT_FILE="$PIPE_DIR/prompts/bd_leads.md"; MAX_SECS="${NIGHTLY_TIMEOUT:-10200}"   # 2 小時 50 分：22:00 開跑，01:00 找人選開跑前一定結束
else
  PROMPT_FILE="$PIPE_DIR/prompts/sourcing.md";  MAX_SECS="${NIGHTLY_TIMEOUT:-18000}"  # 5 小時
fi
TODAY="$(date '+%Y-%m-%d')"
BATCH_ID="nightly-${KIND}-$(date '+%Y%m%d')"

# 提示詞：把 {{…}} 換成這次的值
PROMPT="$(sed -e "s|{{LIMIT}}|$LIMIT|g" -e "s|{{PER_JOB}}|$PER_JOB|g" -e "s|{{TODAY}}|$TODAY|g" \
              -e "s|{{BATCH_ID}}|$BATCH_ID|g" -e "s|{{PIPE_DIR}}|$PIPE_DIR|g" -e "s|{{REPO_DIR}}|$REPO_DIR|g" \
              "$PROMPT_FILE")" || { echo "讀不到提示詞 $PROMPT_FILE" >&2; exit 1; }

CMD=("$CLAUDE_BIN" -p "$PROMPT"
     --model "$MODEL"
     --permission-mode bypassPermissions
     --setting-sources ''
     --strict-mcp-config --mcp-config "$PIPE_DIR/mcp_playwright.json"
     --output-format text)

if [ "$DRY" = 1 ]; then
  echo "=== 種類：$KIND　批次：$BATCH_ID　上限：bd=$LIMIT 家／sourcing=$PER_JOB 位每職缺　逾時：${MAX_SECS}s"
  echo "=== 指令：${CMD[0]} -p <提示詞 ${#PROMPT} 字> ${CMD[*]:3}"
  echo "=== 提示詞前 40 行："
  printf '%s\n' "$PROMPT" | head -40
  grep -o '{{[A-Z_]*}}' <<<"$PROMPT" && { echo "❌ 還有沒換掉的 {{…}}" >&2; exit 1; }
  echo "=== dry-run 結束（沒有呼叫 claude）"
  exit 0
fi

# log 太大就輪替（留一份舊的）
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 20000000 ]; then mv -f "$LOG" "$LOG.1"; fi
exec >>"$LOG" 2>&1

# ── 同一種工作一次只能跑一個 ──
LOCK="$LOCK_DIR/nightly_${KIND}.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  if ! flock -n 9; then echo "[$(date '+%F %T')] ⏭ 上一輪 $KIND 還在跑，這次跳過"; exit 0; fi
else  # macOS 沒有 flock，退回用資料夾當鎖
  if ! mkdir "$LOCK.d" 2>/dev/null; then echo "[$(date '+%F %T')] ⏭ 上一輪 $KIND 還在跑，這次跳過"; exit 0; fi
  trap 'rmdir "$LOCK.d" 2>/dev/null' EXIT
fi

# ── 前提檢查 ──
fail_pre() { echo "[$(date '+%F %T')] ❌ 前提不過：$1"
  printf '{"ts":"%s","kind":"%s","batch":"%s","exit":-1,"secs":0,"leftover_browsers":0,"note":"%s"}\n' \
    "$(date '+%F %T')" "$KIND" "$BATCH_ID" "$1" >>"$RUNS"; exit 1; }
command -v "$CLAUDE_BIN" >/dev/null 2>&1 || fail_pre "找不到 claude 指令"
[ -f "$HOME/.config/workflow-os/cf.env" ] || fail_pre "缺 ~/.config/workflow-os/cf.env"
python3 "$PIPE_DIR/d1q.py" q "SELECT 1 AS ok" >/dev/null || fail_pre "資料庫連不上"

# 找瀏覽器程序：用「程序名稱」比對（不用 -f 整行比對——claude 的提示詞裡就有 chromium 這個字，會誤砍）。
# 在 systemd 底下跑時，只看「這個服務自己的程序群組」裡的，保證不會碰到別的服務開的瀏覽器。
CG_PROCS=""
if [ -r /proc/self/cgroup ]; then
  CG_PATH="$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup 2>/dev/null)"
  case "$CG_PATH" in *.service) [ -r "/sys/fs/cgroup$CG_PATH/cgroup.procs" ] && CG_PROCS="/sys/fs/cgroup$CG_PATH/cgroup.procs" ;; esac
fi
browser_pids() {
  local all; all="$(pgrep -i 'chrom|headless_shell' 2>/dev/null | sort -u)"
  if [ -n "$CG_PROCS" ]; then grep -xF -f "$CG_PROCS" <<<"$all" 2>/dev/null
  else  # 不在 systemd 底下（例如手動跑驗收）：只算 playwright 開的無頭瀏覽器，不碰一般的 Chrome
    local pw; pw="$(pgrep -f 'ms-playwright|--headless' 2>/dev/null | sort -u)"
    [ -n "$all" ] && [ -n "$pw" ] && grep -xF -f <(printf '%s\n' "$pw") <<<"$all" 2>/dev/null
  fi
}
BEFORE="$(browser_pids)"

echo
echo "════════ [$(date '+%F %T')] 開始 $KIND　批次 $BATCH_ID　模型 $MODEL ════════"
START=$(date +%s)
if command -v timeout >/dev/null 2>&1; then
  timeout --kill-after=60 "$MAX_SECS" "${CMD[@]}"
else
  "${CMD[@]}"
fi
CODE=$?
SECS=$(( $(date +%s) - START ))
echo "──────── [$(date '+%F %T')] claude 結束 exit=$CODE　花了 ${SECS}s"

# ── 結束前確認沒有留下瀏覽器（只砍這一輪新開的，不碰開跑前就在的）──
sleep 3
LEFT=0
for pid in $(browser_pids); do
  if ! grep -qx "$pid" <<<"$BEFORE"; then LEFT=$((LEFT+1)); kill "$pid" 2>/dev/null; fi
done
if [ "$LEFT" -gt 0 ]; then
  sleep 5
  for pid in $(browser_pids); do grep -qx "$pid" <<<"$BEFORE" || kill -9 "$pid" 2>/dev/null; done
  echo "⚠️ 發現這一輪留下 $LEFT 個瀏覽器程序，已經關掉（提示詞規定要自己關，下次要查為什麼沒關）"
else
  echo "✅ 沒有留下瀏覽器"
fi

printf '{"ts":"%s","kind":"%s","batch":"%s","exit":%d,"secs":%d,"leftover_browsers":%d}\n' \
  "$(date '+%F %T')" "$KIND" "$BATCH_ID" "$CODE" "$SECS" "$LEFT" >>"$RUNS"
exit "$CODE"
