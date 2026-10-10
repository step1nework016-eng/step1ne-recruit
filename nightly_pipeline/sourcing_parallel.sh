#!/usr/bin/env bash
# E22（2026-10-10 Jacky「AI 找的人太少」）：夜間找人「一個職缺一場 AI 對話、最多同時 2 場」。
# 由 run_nightly.sh 在 `sourcing --priority-only` 時 source 進來；不能單獨執行。
#
# 為什麼：以前一場 AI 對話（再自己開子代理）一次處理全部優先職缺，各職缺的搜尋、查證、對照、寫入擠在同一個
#         對話裡，且每個職缺只有 12 次找名單搜尋。改成每個職缺各開一場獨立對話：只專心這一個職缺、搜尋上限 20 次，
#         用「最久沒做」排序，最多同時 2 場；阿財（面談）優先——有面談進行中就不開新場，場子也用低優先權（nice）跑。
#
# 規則（Jacky 核准的 E22）：
#   1. PER_JOB 維持 10；2. 同時最多 NIGHTLY_MAX_CONCURRENT=2 場，開跑前與每場開始前都看阿財面談（有就等）；
#   3. 每輪總時間不超過「下一輪開始前 30 分鐘」（取下一次 sourcing／bd 排程較早者）；做不完的下一輪接續（rotation 檔）；
#   4. 每職缺找名單搜尋 20 次、每位查證 3 次不變；5. 連續兩輪 0 筆的職缺，這一場先換搜尋方向並在 log 記下換了什麼；
#   6. 每輪結束在 log 尾端印漏斗（每職缺一行），並寫進 sourcing_funnel.jsonl 給 morning_summary 讀。
#
# 可覆蓋的環境變數（測試用）：
#   NIGHTLY_MAX_CONCURRENT（預設 2）  NIGHTLY_SEARCH_LIMIT（預設 20，由 run_nightly.sh 換進提示詞）
#   NIGHTLY_SESSION_SECS（單場上限，預設 2400＝40 分）  NIGHTLY_MIN_START_SECS（離截止不到這麼久就不再開新場，預設 600）
#   NIGHTLY_POLL_SECS（排程迴圈間隔，預設 15）  NIGHTLY_DEADLINE_EPOCH（直接指定截止時間，測試用）
#   PSJ_JOBS_JSON_FILE（用檔案代替查資料庫的職缺清單）  PSJ_ACTIVE_CMD（用指令代替查進行中面談數）

PSJ_MAX_CONCURRENT="${NIGHTLY_MAX_CONCURRENT:-2}"
PSJ_SESSION_SECS="${NIGHTLY_SESSION_SECS:-2400}"
PSJ_MIN_START_SECS="${NIGHTLY_MIN_START_SECS:-600}"
PSJ_POLL_SECS="${NIGHTLY_POLL_SECS:-15}"
PSJ_YIELD_FILE="${PSJ_YIELD_FILE:-$HOME/aijob-automation/state/sourcing_yield.tsv}"
PSJ_FUNNEL_OUT="${PSJ_FUNNEL_OUT:-$LOG_DIR/sourcing_funnel.jsonl}"

psj_log() { echo "[$(date '+%F %T')] $*"; }

# 職缺清單（JSON）：最久沒做的在最前面；連續兩輪 0 筆的標 zero2
psj_jobs_json() {
  if [ -n "${PSJ_JOBS_JSON_FILE:-}" ]; then cat "$PSJ_JOBS_JSON_FILE"; return; fi
  python3 - "$PIPE_DIR" "$ROTATION_FILE" "$PSJ_YIELD_FILE" <<'PY'
import json, os, subprocess, sys
pipe, rot, yf = sys.argv[1], sys.argv[2], sys.argv[3]
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
        p = line.strip().rsplit(' ', 1)
        if len(p) == 2 and p[1]:
            last[p[1]] = max(last.get(p[1], ''), p[0])
except FileNotFoundError:
    pass
hist = {}
try:
    for line in open(yf, encoding='utf-8'):
        p = line.split()
        if len(p) >= 4:                       # 「日期 時間 職缺代號 寫入A/B筆數」
            hist.setdefault(p[2], []).append((p[0] + ' ' + p[1], p[3]))
except FileNotFoundError:
    pass
for j in jobs:
    j['last'] = max(last.get(j['slug'], ''), j.get('last_sourced') or '')
    h = sorted(hist.get(j['slug'], []))[-2:]
    j['zero2'] = len(h) == 2 and all(x[1] == '0' for x in h)
jobs.sort(key=lambda j: (j['last'] != '', j['last'], j['client'], j['slug']))
print(json.dumps(jobs, ensure_ascii=False))
PY
}

psj_active_interviews() {
  if [ -n "${PSJ_ACTIVE_CMD:-}" ]; then eval "$PSJ_ACTIVE_CMD"; return; fi
  python3 "$PIPE_DIR/d1q.py" q "SELECT COUNT(*) AS n FROM applications WHERE interview_state='active'" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["n"])' 2>/dev/null || echo 0
}

# 截止時間（epoch）＝下一次 sourcing／bd 排程較早者 −30 分；至少不超過 MAX_SECS
psj_deadline() {
  local now cap min t u
  now="$(date +%s)"; cap=$(( now + MAX_SECS )); min="$cap"
  if [ -n "${NIGHTLY_DEADLINE_EPOCH:-}" ]; then echo "$NIGHTLY_DEADLINE_EPOCH"; return; fi
  for u in step1ne-nightly-sourcing.timer step1ne-nightly-bd.timer; do
    t="$(systemctl --user show "$u" -p NextElapseUSecRealtime --value 2>/dev/null)"
    # systemctl 印的是「Sat 2026-10-10 20:00:00 CST」。不能直接丟給 date -d：GNU date 把 CST 當成美國中部標準時間（UTC-6），
    # 會差 14 小時。拿掉最後的時區縮寫，用本機時區（上面已 export TZ=Asia/Taipei）解讀。
    [ -n "$t" ] && t="$(date -d "${t% *}" +%s 2>/dev/null)" || continue
    # 排程剛好就是現在這一輪（誤差 5 分內）不算「下一輪」
    [ "$t" -gt $(( now + 300 )) ] && [ "$t" -lt "$min" ] && min="$t"
  done
  # min 是下一輪開始；再扣 30 分。但至少留 15 分鐘讓這一輪能做點事
  local d=$(( min - 1800 ))
  [ "$d" -lt $(( now + 900 )) ] && d=$(( now + 900 ))
  echo "$d"
}

# 每個職缺的專屬提示詞段落
psj_note() {  # slug title client last zero2
  local slug="$1" title="$2" client="$3" last="$4" zero2="$5"
  cat <<EOF
## ⚠️ 本場模式：一場只做一個職缺（E22，Jacky 2026-10-10）
- 本場**只做這一個職缺**：\`$slug\`｜$title｜客戶 ${client:-—}｜上次做：${last:-從沒做過}
- 第一步「挑今晚要找的職缺」整段**跳過**（程式已經幫你挑好了）；**不要做任何其他職缺**，**不要開子代理**，自己一路做完：讀條件和顧問回饋、搜尋、查證、對照、去重、寫入。
- 本職缺「找名單」搜尋上限是 **${NIGHTLY_SEARCH_LIMIT:-20} 次**（不是 12），每位人選查證仍是最多 3 次。其他規則（只收有個人頁的、不聯絡任何人、只新增資料、年齡性別外貌不判斷、off_limits 公司不收）完全不變。
- 做完（不管新增幾位、甚至 0 位）先跑這一行，讓下一輪知道輪到誰：
  \`echo "\$(date '+%F %T') $slug" >> $ROTATION_FILE\`
- **最後一行**一定要印漏斗（自己數、不確定的數字寫 -1，不要估），格式固定、單獨一行：
  \`FUNNEL {"slug":"$slug","searches":找名單搜尋次數,"seen":查證過幾位不同的人,"no_page":沒個人頁丟掉幾位,"cd_skipped":自評C或D沒寫入幾位,"dup":重複略過幾位,"written":寫入A或B幾位}\`
EOF
  if [ "$zero2" = "True" ] || [ "$zero2" = "true" ] || [ "$zero2" = "1" ]; then
    cat <<EOF
- 🔁 **這個職缺連續兩輪寫入 0 筆**：這一場**先換搜尋方向**再找，不要重複前兩輪的做法。可以換的方向（挑幾個試）：
  同業／競爭對手的公司名單（從公司官網團隊頁、年報、新聞稿找人名再補個人頁）、職稱換說法（同義職稱、上下一級）、
  日文／英文職稱搜尋、Cake 公開履歷、GitHub（技術職）、PTT／Dcard／專業社群的自介文。
  並在漏斗那一行**前面**多印一行：\`STRATEGY $slug｜換了什麼（一句話）\`。
EOF
  fi
}

psj_prompt() {  # slug title client last zero2 → 印出完整提示詞
  local note_file; note_file="$(mktemp)"
  psj_note "$1" "$2" "$3" "$4" "$5" > "$note_file"
  printf '%s\n' "$PROMPT_BASE" | sed -e '/{{PRIORITY_NOTE}}/{' -e "r $note_file" -e 'd' -e '}'
  rm -f "$note_file"
}

# 一場結束後：收 log、記 rotation／yield、寫漏斗
psj_finalize() {  # slug exit secs
  local slug="$1" code="$2" secs="$3" out="$PSJ_SESS_DIR/$1.out"
  {
    echo
    echo "──── [$(date '+%F %T')] 職缺 $slug 這一場結束 exit=$code　${secs}s ────"
    cat "$out" 2>/dev/null
  } >> "$LOG"
  echo "$(date '+%F %T') $slug" >> "$ROTATION_FILE"       # 保險：模型沒記也會記（取最大值，重複無害）
  python3 - "$slug" "$code" "$secs" "$out" "${D1Q_FUNNEL_FILE:-}" "$BATCH_ID" "$PSJ_FUNNEL_OUT" "$PSJ_YIELD_FILE" <<'PY'
import json, re, sys, datetime
slug, code, secs, out, ff, batch, fout, yf = sys.argv[1:9]
agent, strategy = None, None
try:
    for line in open(out, encoding='utf-8', errors='replace'):
        if line.startswith('FUNNEL '):
            try: agent = json.loads(line[7:].strip())
            except ValueError: agent = {'parse_error': line[7:].strip()[:200]}
        elif line.startswith('STRATEGY '):
            strategy = line[9:].strip()[:300]
except OSError:
    pass
gate = {'written_ab': 0, 'written_cd': 0, 'no_page': 0, 'must_check_rejected': 0, 'client_blocked': 0}
try:
    for line in open(ff, encoding='utf-8'):
        try: d = json.loads(line)
        except ValueError: continue
        if d.get('run') == batch and d.get('job_slug') == slug:
            k = d.get('outcome')
            if k in gate: gate[k] += 1
except OSError:
    pass
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
rec = {'ts': now, 'batch': batch, 'slug': slug, 'exit': int(code), 'secs': int(secs), 'gate': gate, 'agent': agent, 'strategy': strategy}
open(fout, 'a', encoding='utf-8').write(json.dumps(rec, ensure_ascii=False) + '\n')
open(yf, 'a', encoding='utf-8').write(f"{now} {slug} {gate['written_ab']}\n")
PY
}

psj_funnel_report() {  # 本輪每職缺一行
  python3 - "$PSJ_FUNNEL_OUT" "$BATCH_ID" <<'PY'
import json, sys
rows = []
try:
    for line in open(sys.argv[1], encoding='utf-8'):
        try: d = json.loads(line)
        except ValueError: continue
        if d.get('batch') == sys.argv[2]: rows.append(d)
except OSError:
    pass
print('📊 本輪漏斗（每職缺一行）。程式數＝d1q 寫入閘門實際記到的；模型自報＝FUNNEL 那行（-1／空＝沒數）')
print('%-46s %5s | %6s %7s %6s | %4s %5s %6s %4s %4s | %s' % ('職缺', '秒', '寫入AB', '寫入CD', '沒個人頁', '搜尋', '查證', '自評CD', '重複', '寫入', 'exit'))
tot = {'ab': 0, 'cd': 0, 'np': 0}
for d in rows:
    g, a = d['gate'], d.get('agent') or {}
    f = lambda k: str(a.get(k, '-'))
    print('%-46s %5d | %6d %7d %8d | %4s %5s %6s %4s %4s | %d' % (d['slug'][:46], d['secs'], g['written_ab'], g['written_cd'], g['no_page'],
          f('searches'), f('seen'), f('cd_skipped'), f('dup'), f('written'), d['exit']))
    tot['ab'] += g['written_ab']; tot['cd'] += g['written_cd']; tot['np'] += g['no_page']
    if d.get('strategy'): print('    🔁 換方向：' + d['strategy'])
print('合計：做了 %d 個職缺，寫入 A/B %d 位、C/D %d 位，沒個人頁丟掉 %d 位' % (len(rows), tot['ab'], tot['cd'], tot['np']))
PY
}

run_sourcing_per_job() {
  local jobs_json deadline now start_all
  PSJ_SESS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/psj.XXXXXX")"
  mkdir -p "$(dirname "$PSJ_YIELD_FILE")"
  export D1Q_FUNNEL_FILE="${D1Q_FUNNEL_FILE:-$LOG_DIR/d1_funnel.jsonl}"
  jobs_json="$(psj_jobs_json)"
  local -a QUEUE=() RUNNING=()
  declare -A J_TITLE J_CLIENT J_LAST J_ZERO J_PID J_START
  local slug
  while IFS=$'\t' read -r slug title client last zero2; do
    [ -z "$slug" ] && continue
    QUEUE+=("$slug"); J_TITLE[$slug]="$title"; J_CLIENT[$slug]="$client"; J_LAST[$slug]="$last"; J_ZERO[$slug]="$zero2"
  done < <(python3 -c '
import json,sys
for j in json.load(sys.stdin):
    print("\t".join([j["slug"], j.get("title") or "", j.get("client") or "", j.get("last") or "", str(j.get("zero2"))]))' <<<"$jobs_json")
  deadline="$(psj_deadline)"
  start_all="$(date +%s)"
  psj_log "▶ 一職缺一場：共 ${#QUEUE[@]} 個優先職缺，同時最多 $PSJ_MAX_CONCURRENT 場，單場上限 ${PSJ_SESSION_SECS}s，截止 $(date -d "@$deadline" '+%F %T')（下一輪前 30 分）"
  psj_log "順序：${QUEUE[*]}"
  [ "${#QUEUE[@]}" -eq 0 ] && { psj_log "（沒有優先職缺）"; return 0; }

  local last_wait=0 worst=0
  while [ "${#QUEUE[@]}" -gt 0 ] || [ "${#RUNNING[@]}" -gt 0 ]; do
    # 收完成的
    local -a STILL=()
    local s
    for s in "${RUNNING[@]}"; do
      if [ -f "$PSJ_SESS_DIR/$s.done" ]; then
        read -r code secs < "$PSJ_SESS_DIR/$s.done"
        psj_log "■ 結束 $s exit=$code ${secs}s"
        [ "$code" -ne 0 ] && [ "$code" -gt "$worst" ] && worst="$code"
        psj_finalize "$s" "$code" "$secs"
      else
        STILL+=("$s")
      fi
    done
    RUNNING=("${STILL[@]}")
    now="$(date +%s)"
    # 開新場
    if [ "${#QUEUE[@]}" -gt 0 ] && [ "${#RUNNING[@]}" -lt "$PSJ_MAX_CONCURRENT" ]; then
      if [ $(( deadline - now )) -lt "$PSJ_MIN_START_SECS" ]; then
        psj_log "⏰ 離截止不到 $(( PSJ_MIN_START_SECS / 60 )) 分鐘，不再開新場；剩 ${#QUEUE[@]} 個職缺留給下一輪：${QUEUE[*]}"
        QUEUE=()
      else
        local act; act="$(psj_active_interviews)"
        if [ "${act:-0}" != "0" ]; then
          if [ $(( now - last_wait )) -ge 60 ]; then psj_log "⏳ 有 $act 場面談進行中，阿財優先，不開新場（已開 ${#RUNNING[@]} 場在跑）"; last_wait="$now"; fi
        else
          slug="${QUEUE[0]}"; QUEUE=("${QUEUE[@]:1}")
          local secs_left=$(( deadline - now )); local lim="$PSJ_SESSION_SECS"
          [ "$secs_left" -lt "$lim" ] && lim="$secs_left"
          local prompt; prompt="$(psj_prompt "$slug" "${J_TITLE[$slug]}" "${J_CLIENT[$slug]}" "${J_LAST[$slug]}" "${J_ZERO[$slug]}")"
          local zmark=""; [ "${J_ZERO[$slug]}" = "True" ] && zmark="，🔁連兩輪0筆→換搜尋方向"
          psj_log "▶ 開場 $slug（上限 ${lim}s${zmark}）｜進行中 $(( ${#RUNNING[@]} + 1 )) 場、排隊 ${#QUEUE[@]} 個"
          (
            st="$(date +%s)"
            if command -v timeout >/dev/null 2>&1; then
              nice -n 10 timeout --kill-after=60 "$lim" "$CLAUDE_BIN" -p "$prompt" --model "$MODEL" --permission-mode bypassPermissions \
                --setting-sources '' --strict-mcp-config --mcp-config "$PIPE_DIR/mcp_playwright.json" --output-format text \
                > "$PSJ_SESS_DIR/$slug.out" 2>&1
            else
              nice -n 10 "$CLAUDE_BIN" -p "$prompt" --model "$MODEL" --permission-mode bypassPermissions \
                --setting-sources '' --strict-mcp-config --mcp-config "$PIPE_DIR/mcp_playwright.json" --output-format text \
                > "$PSJ_SESS_DIR/$slug.out" 2>&1
            fi
            c=$?
            echo "$c $(( $(date +%s) - st ))" > "$PSJ_SESS_DIR/$slug.done"
          ) &
          J_PID[$slug]=$!; J_START[$slug]="$now"
          RUNNING+=("$slug")
          continue          # 還有空位的話馬上看下一個，不用等
        fi
      fi
    fi
    sleep "$PSJ_POLL_SECS"
  done
  psj_log "──── 全部場次結束，共花 $(( $(date +%s) - start_all ))s ────"
  psj_funnel_report
  rm -rf "$PSJ_SESS_DIR"
  return "$worst"
}

# --dry-run：印順序、截止、第一個職缺的提示詞開頭，不呼叫 claude
psj_dry_run() {
  local jobs_json deadline
  PSJ_SESS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/psj.XXXXXX")"
  jobs_json="$(psj_jobs_json)"
  deadline="$(psj_deadline)"
  echo "=== 一職缺一場：同時最多 $PSJ_MAX_CONCURRENT 場、單場上限 ${PSJ_SESSION_SECS}s、搜尋上限 ${NIGHTLY_SEARCH_LIMIT:-20}、截止 $(date -d "@$deadline" '+%F %T')"
  python3 -c '
import json,sys
js = json.load(sys.stdin)
print("=== 順序（共 %d 個）：" % len(js))
for i, j in enumerate(js, 1):
    print("%2d. %s｜%s｜上次做：%s%s" % (i, j["slug"], j.get("title"), j.get("last") or "從沒做過", "｜🔁連兩輪0筆" if j.get("zero2") else ""))' <<<"$jobs_json"
  local first
  first="$(python3 -c '
import json,sys
js = json.load(sys.stdin)
if js:
    j = js[0]; print("\t".join([j["slug"], j.get("title") or "", j.get("client") or "", j.get("last") or "", str(j.get("zero2"))]))' <<<"$jobs_json")"
  if [ -n "$first" ]; then
    IFS=$'\t' read -r s t c l z <<<"$first"
    echo "=== 第一場提示詞（只印『本場模式』段）："
    psj_note "$s" "$t" "$c" "$l" "$z"
    psj_prompt "$s" "$t" "$c" "$l" "$z" | grep -c '{{' | sed 's/^/提示詞裡剩下沒換掉的 {{…}} 數量：/'
  fi
  rm -rf "$PSJ_SESS_DIR"
}
