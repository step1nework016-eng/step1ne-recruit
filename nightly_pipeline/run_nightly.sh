#!/usr/bin/env bash
# Step1ne 夜間工作：開發客戶名單（bd）／幫職缺找人選（sourcing）。
#
# 用法：
#   run_nightly.sh bd        [--limit 30]      # 每晚新增最多 N 家潛在客戶（預設 30）
#   run_nightly.sh sourcing  [--per-job 10]    # 每個職缺最多新增 N 位人選（預設 10）
#   run_nightly.sh sourcing --priority-only    # E19（2026-10-09）：只做後台設成「優先」的職缺，依「最久沒做」排序，一輪做不完下一輪接著做
#     E22（2026-10-10）：--priority-only 現在是「一個職缺一場 AI 對話、最多同時 2 場」，邏輯在 sourcing_parallel.sh；
#     有面談進行中就不開新場、截止＝下一輪（sourcing／bd）開始前 30 分、每職缺找名單搜尋 20 次、連兩輪 0 筆換搜尋方向、
#     結尾印漏斗並寫 ~/aijob-automation/logs/sourcing_funnel.jsonl。NIGHTLY_ONE_JOB_PER_SESSION=0 退回舊做法（一場做全部）。
#   環境變數 NIGHTLY_WAIT_IDLE_MIN=N：開跑前若有面談進行中，最多等 N 分鐘（每分鐘看一次）再開始；預設 0＝不等
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
PRIORITY_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --limit)   LIMIT="${2:?--limit 要接數字}"; shift 2 ;;
    --per-job) PER_JOB="${2:?--per-job 要接數字}"; shift 2 ;;
    --priority-only) PRIORITY_ONLY=1; shift ;;
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
# 一天跑三輪時，批次代號要分得開（用量統計、資料庫裡的批次標記都靠它）
[ "$PRIORITY_ONLY" = 1 ] && BATCH_ID="${BATCH_ID}-$(date '+%H%M')"
# E22（2026-10-10）：優先職缺一輪改成「一個職缺一場 AI 對話、最多同時 2 場」（見 sourcing_parallel.sh）。
# NIGHTLY_ONE_JOB_PER_SESSION=0 可退回原本「一場對話做全部」的做法。
PER_JOB_SESSIONS=0
if [ "$KIND" = sourcing ] && [ "$PRIORITY_ONLY" = 1 ] && [ "${NIGHTLY_ONE_JOB_PER_SESSION:-1}" = 1 ]; then PER_JOB_SESSIONS=1; fi
ROTATION_FILE="$HOME/aijob-automation/state/sourcing_rotation.tsv"
mkdir -p "$(dirname "$ROTATION_FILE")"

# E19：priority-only 模式的提示詞段落——程式先把「最久沒做的優先職缺」排好給 claude，照順序做
build_priority_note() {
  python3 - "$PIPE_DIR" "$ROTATION_FILE" <<'PY'
import json, os, subprocess, sys
pipe, rot = sys.argv[1], sys.argv[2]
sql = ("SELECT j.slug, j.title, COALESCE(j.company_id, j.client_name, '') AS client, "
       "(SELECT MAX(s.created_at) FROM sourced_candidates s WHERE s.job_slug=j.slug AND s.source='AI夜間找人') AS last_sourced "
       "FROM jobs j JOIN job_sourcing_settings ss ON ss.job_slug=j.slug "
       "WHERE j.status='open' AND j.slug <> 'unspecified' AND ss.mode='priority'")
r = subprocess.run(['python3', os.path.join(pipe, 'd1q.py'), 'q', sql], capture_output=True, text=True)
try:
    jobs = json.loads(r.stdout)
except ValueError:
    jobs = []
last = {}
try:
    for line in open(rot, encoding='utf-8'):
        p = line.strip().rsplit(' ', 1)          # 「2026-10-09 09:30:00 職缺代號」：最後一個空白前是時間、後面是代號
        if len(p) == 2 and p[1]:
            last[p[1]] = max(last.get(p[1], ''), p[0])
except FileNotFoundError:
    pass
for j in jobs:
    j['last'] = max(last.get(j['slug'], ''), j.get('last_sourced') or '')
jobs.sort(key=lambda j: (j['last'] != '', j['last'], j['client'], j['slug']))   # 從沒做過的最前面，其次是最久沒做的
print('## ⚠️ 本輪模式：只做「優先」職缺（E19，Jacky 2026-10-08）')
print('- **這一輪只找後台設成「優先」（mode=priority）的職缺**。一般職缺（沒設定／auto）一律不做，「不找」（off）更不做。不要自己擴充名單。')
print('- **第一步「挑今晚要找的職缺」不用再查**——下面這份清單程式已經排好了（最久沒做的在最前面），照順序一個一個做；')
print('  第二步以後的規則（讀顧問回饋、逐條對照、去重、寫入格式）照舊。')
print('- 一輪做不完沒關係：時間或搜尋次數用完就停，下一輪會從「最久沒做」的接著做。**不要為了做完而放寬品質或硬湊人選。**')
print('- **每個職缺做完（不管新增幾位、甚至 0 位）就跑這一行**，讓下一輪知道輪到誰：')
print("  `echo \"$(date '+%F %T') 職缺代號\" >> " + rot + '`    （把「職缺代號」換成這個職缺的 slug）')
print('')
if jobs:
    print('今天的順序（共 %d 個優先職缺）：' % len(jobs))
    for i, j in enumerate(jobs, 1):
        print('%d. `%s`｜%s｜客戶 %s｜上次做：%s' % (i, j['slug'], j['title'], j['client'] or '—', j['last'] or '從沒做過'))
else:
    print('（程式查不到優先職缺清單——請自己查：mode=priority 且 status=open 的職缺，依 sourced_candidates 最近一筆時間由舊到新排序。）')
PY
}

# 提示詞：把 {{…}} 換成這次的值
export NIGHTLY_SEARCH_LIMIT="${NIGHTLY_SEARCH_LIMIT:-20}"      # E22：每職缺「找名單」搜尋上限 12 → 20
PROMPT="$(sed -e "s|{{LIMIT}}|$LIMIT|g" -e "s|{{PER_JOB}}|$PER_JOB|g" -e "s|{{TODAY}}|$TODAY|g" \
              -e "s|{{SEARCH_LIMIT}}|$NIGHTLY_SEARCH_LIMIT|g" \
              -e "s|{{BATCH_ID}}|$BATCH_ID|g" -e "s|{{PIPE_DIR}}|$PIPE_DIR|g" -e "s|{{REPO_DIR}}|$REPO_DIR|g" \
              "$PROMPT_FILE")" || { echo "讀不到提示詞 $PROMPT_FILE" >&2; exit 1; }
PROMPT_BASE="$PROMPT"      # E22：一職缺一場時，每個職缺各自把 {{PRIORITY_NOTE}} 換成自己的段落（見 sourcing_parallel.sh）
if [ "$PER_JOB_SESSIONS" = 1 ]; then
  # shellcheck source=sourcing_parallel.sh
  . "$PIPE_DIR/sourcing_parallel.sh"
else
  PRIORITY_NOTE=""
  if [ "$PRIORITY_ONLY" = 1 ] && [ "$KIND" = sourcing ]; then PRIORITY_NOTE="$(build_priority_note)"; fi
  NOTE_FILE="$(mktemp)"; printf '%s\n' "$PRIORITY_NOTE" > "$NOTE_FILE"
  # 不用 bash 的 ${//} 取代：bash 5.2 起取代字串裡的 & 會被當成「比對到的文字」，職缺名稱有 & 就會壞掉
  PROMPT="$(printf '%s\n' "$PROMPT" | sed -e '/{{PRIORITY_NOTE}}/{' -e "r $NOTE_FILE" -e 'd' -e '}')"
  rm -f "$NOTE_FILE"
fi

CMD=("$CLAUDE_BIN" -p "$PROMPT"
     --model "$MODEL"
     --permission-mode bypassPermissions
     --setting-sources ''
     --strict-mcp-config --mcp-config "$PIPE_DIR/mcp_playwright.json"
     --output-format text)

if [ "$DRY" = 1 ]; then
  echo "=== 種類：$KIND　批次：$BATCH_ID　上限：bd=$LIMIT 家／sourcing=$PER_JOB 位每職缺　逾時：${MAX_SECS}s　只做優先職缺：$PRIORITY_ONLY　一職缺一場：$PER_JOB_SESSIONS"
  if [ "$PER_JOB_SESSIONS" = 1 ]; then
    psj_dry_run
    # 基底提示詞裡只允許剩下 {{PRIORITY_NOTE}}（每個職缺各自換）
    LEFT_MARK="$(grep -o '{{[A-Z_]*}}' <<<"$PROMPT_BASE" | grep -v '{{PRIORITY_NOTE}}' || true)"
    [ -n "$LEFT_MARK" ] && { echo "❌ 還有沒換掉的 $LEFT_MARK" >&2; exit 1; }
  else
    echo "=== 指令：${CMD[0]} -p <提示詞 ${#PROMPT} 字> ${CMD[*]:3}"
    echo "=== 提示詞前 40 行："
    printf '%s\n' "$PROMPT" | head -40
    grep -o '{{[A-Z_]*}}' <<<"$PROMPT" && { echo "❌ 還有沒換掉的 {{…}}" >&2; exit 1; }
  fi
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

# E19：開跑前若有面談進行中，先等一等（找人會同時開 claude 和瀏覽器，跟面談搶資源）。預設不等；等滿了照跑。
WAIT_MIN="${NIGHTLY_WAIT_IDLE_MIN:-0}"
if [ "$KIND" = sourcing ] && [ "$WAIT_MIN" -gt 0 ] 2>/dev/null; then
  for i in $(seq 1 "$WAIT_MIN"); do
    N="$(python3 "$PIPE_DIR/d1q.py" q "SELECT COUNT(*) AS n FROM applications WHERE interview_state='active'" 2>/dev/null \
         | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["n"])' 2>/dev/null || echo 0)"
    [ "${N:-0}" = "0" ] && break
    echo "[$(date '+%F %T')] ⏳ 有 $N 場面談進行中，等 60 秒再開始（$i/$WAIT_MIN）"; sleep 60
  done
fi
export D1Q_STATS_FILE="$LOG_DIR/d1_usage.jsonl" D1Q_RUN_ID="$BATCH_ID"

echo
echo "════════ [$(date '+%F %T')] 開始 $KIND　批次 $BATCH_ID　模型 $MODEL ════════"
START=$(date +%s)
if [ "$PER_JOB_SESSIONS" = 1 ]; then
  export D1Q_FUNNEL_FILE="$LOG_DIR/d1_funnel.jsonl"      # d1q 每次寫入閘門的結果（每職缺漏斗的「程式數」）
  run_sourcing_per_job
  CODE=$?
elif command -v timeout >/dev/null 2>&1; then
  timeout --kill-after=60 "$MAX_SECS" "${CMD[@]}"
  CODE=$?
else
  "${CMD[@]}"
  CODE=$?
fi
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

# E19：這一輪讀寫了多少 D1 列（只算 d1q 這支工具；常駐程式的輪詢不在內）
read -r D1_CALLS D1_READ D1_WRITE < <(python3 - "$LOG_DIR/d1_usage.jsonl" "$BATCH_ID" <<'PY'
import json, sys
c = r = w = 0
try:
    for line in open(sys.argv[1], encoding='utf-8'):
        try: d = json.loads(line)
        except ValueError: continue
        if d.get('run') == sys.argv[2]:
            c += d.get('calls', 0); r += d.get('rows_read', 0); w += d.get('rows_written', 0)
except OSError:
    pass
print(c, r, w)
PY
)
echo "📊 這一輪 d1q 的 D1 用量：${D1_CALLS:-0} 次查詢，讀 ${D1_READ:-0} 列、寫 ${D1_WRITE:-0} 列"
printf '{"ts":"%s","kind":"%s","batch":"%s","exit":%d,"secs":%d,"leftover_browsers":%d,"d1_calls":%d,"d1_rows_read":%d,"d1_rows_written":%d}\n' \
  "$(date '+%F %T')" "$KIND" "$BATCH_ID" "$CODE" "$SECS" "$LEFT" "${D1_CALLS:-0}" "${D1_READ:-0}" "${D1_WRITE:-0}" >>"$RUNS"
exit "$CODE"
