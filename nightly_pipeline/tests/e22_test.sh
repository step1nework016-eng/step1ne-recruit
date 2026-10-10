#!/usr/bin/env bash
# E22 測試：sourcing_parallel.sh（一職缺一場、最多同時 2 場）＋ d1q 漏斗計數。
# 全部用假的 claude／假的職缺清單／假的面談數，不連資料庫（d1q 那段只走離線路徑），不呼叫真的 claude。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPE_DIR="$(dirname "$HERE")"
T="$(mktemp -d /tmp/e22test.XXXXXX)"
export TZ=Asia/Taipei
FAILS=0
ok()   { echo "  ✅ $1"; }
bad()  { echo "  ❌ $1"; FAILS=$((FAILS+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1  ｜$2"; fi; }

# ── 假 claude：從提示詞找出是哪個職缺，睡 FAKE_SLEEP 秒，記開始／結束時間，印 FUNNEL ──
cat > "$T/fake_claude" <<'EOF'
#!/usr/bin/env bash
prompt=""; while [ $# -gt 0 ]; do if [ "$1" = "-p" ]; then prompt="$2"; shift 2; else shift; fi; done
slug="$(grep -o '本場\*\*只做這一個職缺\*\*：`[^`]*`' <<<"$prompt" | sed 's/.*`\(.*\)`/\1/')"
printf '%s\n' "$prompt" > "$FAKE_DIR/prompt_$slug.txt"
echo "$(date +%s.%N) start $slug" >> "$FAKE_DIR/events.log"
sleep "${FAKE_SLEEP:-1}"
echo "$(date +%s.%N) end $slug" >> "$FAKE_DIR/events.log"
if grep -q '換搜尋方向' <<<"$prompt"; then echo "STRATEGY $slug｜改搜同業公司名單"; fi
python3 - "$slug" <<'PY'
import json, os, sys
slug = sys.argv[1]
ff = os.environ.get('D1Q_FUNNEL_FILE')
if ff:
    for o in ('written_ab', 'no_page'):
        open(ff, 'a').write(json.dumps({'run': os.environ.get('D1Q_RUN_ID', ''), 'job_slug': slug, 'outcome': o, 'name': 'x'}) + '\n')
print('FUNNEL ' + json.dumps({'slug': slug, 'searches': 7, 'seen': 5, 'no_page': 1, 'cd_skipped': 0, 'dup': 1, 'written': 1}))
PY
EOF
chmod +x "$T/fake_claude"

# 共用環境（模擬 run_nightly.sh 裡的變數）
setup() {
  rm -rf "$T/run"; mkdir -p "$T/run/logs" "$T/run/state"
  export FAKE_DIR="$T/run"; : > "$FAKE_DIR/events.log"
  export LOG_DIR="$T/run/logs" LOG="$T/run/logs/nightly_sourcing.log" ROTATION_FILE="$T/run/state/rotation.tsv"
  export PSJ_YIELD_FILE="$T/run/state/yield.tsv" PSJ_FUNNEL_OUT="$T/run/logs/sourcing_funnel.jsonl"
  export D1Q_FUNNEL_FILE="$T/run/logs/d1_funnel.jsonl" D1Q_RUN_ID="test-batch" BATCH_ID="test-batch"
  export PIPE_DIR MODEL=fake CLAUDE_BIN="$T/fake_claude" MAX_SECS=600
  export NIGHTLY_POLL_SECS=0.2 NIGHTLY_SEARCH_LIMIT=20 FAKE_SLEEP=1
  export PSJ_ACTIVE_CMD='echo 0'
  unset NIGHTLY_DEADLINE_EPOCH NIGHTLY_SESSION_SECS NIGHTLY_MIN_START_SECS NIGHTLY_MAX_CONCURRENT
  : > "$ROTATION_FILE"
  PROMPT_BASE="$(sed -e 's|{{SEARCH_LIMIT}}|20|g' -e 's|{{PER_JOB}}|10|g' -e 's|{{TODAY}}|2026-10-10|g' -e 's|{{BATCH_ID}}|test-batch|g' \
                    -e "s|{{PIPE_DIR}}|$PIPE_DIR|g" -e 's|{{REPO_DIR}}|/repo|g' -e 's|{{LIMIT}}|30|g' "$PIPE_DIR/prompts/sourcing.md")"
  export PROMPT_BASE
}
jobs_file() {  # n [zero2_slug]
  python3 - "$1" "${2:-}" > "$T/jobs.json" <<'PY'
import json, sys
n, z = int(sys.argv[1]), sys.argv[2]
js = [{'slug': f'job-{i}', 'title': f'職缺{i} & Co. \'quote\'', 'client': f'co_{i}', 'last': f'2026-10-0{i % 9 + 1} 09:00:00', 'zero2': f'job-{i}' == z} for i in range(1, n + 1)]
print(json.dumps(js, ensure_ascii=False))
PY
  export PSJ_JOBS_JSON_FILE="$T/jobs.json"
}
run_it() { ( set +e; . "$PIPE_DIR/sourcing_parallel.sh"; run_sourcing_per_job ) > "$T/run/stdout.txt" 2>&1; echo $? > "$T/run/exit.txt"; }
maxconc() { python3 - "$FAKE_DIR/events.log" <<'PY'
import sys
ev = sorted((float(l.split()[0]), 1 if l.split()[1] == 'start' else -1) for l in open(sys.argv[1]) if l.strip())
c = m = 0
for _, d in ev:
    c += d; m = max(m, c)
print(m)
PY
}

echo "[A] 5 個職缺、最多同時 2 場"
setup; jobs_file 5; export FAKE_SLEEP=1.5
run_it
check "5 個職缺都開過場" "[ \$(grep -c ' start ' \$FAKE_DIR/events.log) = 5 ]"
check "同時最多 2 場（實測最大並行 = $(maxconc)）" "[ \$(maxconc) = 2 ]"
check "依清單順序開場（job-1 先於 job-5）" "[ \"\$(grep ' start ' \$FAKE_DIR/events.log | head -1 | awk '{print \$3}')\" = job-1 ]"
check "rotation 檔每個職缺都有記" "[ \$(awk '{print \$3}' \$ROTATION_FILE | sort -u | wc -l) = 5 ]"
check "漏斗 jsonl 5 行，程式數（寫入 AB／沒個人頁）與模型自報都有" "[ \$(wc -l < \$PSJ_FUNNEL_OUT) = 5 ] && python3 -c \"import json;r=[json.loads(l) for l in open('\$PSJ_FUNNEL_OUT')];assert all(x['gate']['written_ab']==1 and x['gate']['no_page']==1 and x['agent']['searches']==7 for x in r)\""
check "yield 檔記了每個職缺的寫入 AB 數" "[ \$(wc -l < \$PSJ_YIELD_FILE) = 5 ] && [ \"\$(awk '{print \$4}' \$PSJ_YIELD_FILE | sort -u)\" = 1 ]"
check "每場的 log 都收進主 log（含職缺標頭）" "[ \$(grep -c '這一場結束' \$LOG) = 5 ]"
check "結尾印了漏斗表" "grep -q '本輪漏斗' \$T/run/stdout.txt && grep -q '合計：做了 5 個職缺' \$T/run/stdout.txt"
check "提示詞：只做這一個職缺／搜尋上限 20／不開子代理／FUNNEL 格式／職缺名稱含 & 與引號沒壞" \
  "grep -q '搜尋上限是 \*\*20 次' \$FAKE_DIR/prompt_job-3.txt && grep -q '不要開子代理' \$FAKE_DIR/prompt_job-3.txt && grep -q 'FUNNEL {' \$FAKE_DIR/prompt_job-3.txt && grep -q '職缺3 & Co. .quote.' \$FAKE_DIR/prompt_job-3.txt"
check "提示詞沒有留下沒換掉的 {{…}}" "! grep -q '{{' \$FAKE_DIR/prompt_job-3.txt"
check "沒有 zero2 標記的職缺，提示詞不含「換搜尋方向」" "! grep -q '換搜尋方向' \$FAKE_DIR/prompt_job-2.txt"

echo; echo "[B] 阿財面談優先：有面談進行中就不開新場"
setup; jobs_file 3; export FAKE_SLEEP=0.5
echo 1 > "$T/active"; export PSJ_ACTIVE_CMD="cat $T/active"
( sleep 3; echo 0 > "$T/active"; date +%s.%N > "$T/released" ) &
run_it; wait
first="$(head -1 "$FAKE_DIR/events.log" | awk '{print $1}')"; rel="$(cat "$T/released")"
check "面談進行中沒有開任何一場，面談結束後才開（開場 ${first%.*} ≥ 釋放 ${rel%.*}）" "python3 -c \"import sys;sys.exit(0 if float('$first')>=float('$rel')-0.05 else 1)\""
check "之後 3 個都做完" "[ \$(grep -c ' end ' \$FAKE_DIR/events.log) = 3 ]"
check "log 有記「阿財優先，不開新場」" "grep -q '阿財優先' \$T/run/stdout.txt"

echo; echo "[C] 截止時間：離截止不到 MIN_START 就不再開新場，剩下的留給下一輪"
setup; jobs_file 8; export FAKE_SLEEP=2.5
export NIGHTLY_DEADLINE_EPOCH=$(( $(date +%s) + 10 )) NIGHTLY_MIN_START_SECS=6
run_it
n=$(grep -c ' start ' "$FAKE_DIR/events.log")
check "只開了一部分（$n/8），沒有全開" "[ $n -ge 2 ] && [ $n -lt 8 ]"
check "log 有記剩幾個留給下一輪" "grep -q '留給下一輪' \$T/run/stdout.txt"
check "沒開的職缺沒有被記進 rotation（下一輪會優先做）" "[ \$(awk '{print \$3}' \$ROTATION_FILE | sort -u | wc -l) = $n ]"

echo; echo "[D] 連續兩輪 0 筆 → 這一場換搜尋方向，並記下換了什麼"
setup; jobs_file 3 job-2; export FAKE_SLEEP=0.3
run_it
check "zero2 職缺的提示詞有「換搜尋方向」與 STRATEGY 指示" "grep -q '換搜尋方向' \$FAKE_DIR/prompt_job-2.txt && grep -q 'STRATEGY job-2' \$FAKE_DIR/prompt_job-2.txt"
check "其他職缺沒有" "! grep -q '換搜尋方向' \$FAKE_DIR/prompt_job-1.txt"
check "換了什麼被記進漏斗 jsonl 與結尾報表" "grep -q '改搜同業公司名單' \$PSJ_FUNNEL_OUT && grep -q '換方向：job-2' \$T/run/stdout.txt"

echo; echo "[E] 單場逾時：被砍掉也要收尾、記 rotation，其他場不受影響"
setup; jobs_file 3; export FAKE_SLEEP=10 NIGHTLY_SESSION_SECS=2
run_it
check "3 場都因逾時結束（exit 124 或 137）" "[ \$(python3 -c \"import json;print(sum(json.loads(l)['exit'] in (124,137) for l in open('\$PSJ_FUNNEL_OUT')))\") = 3 ]"
check "逾時的職缺仍記了 rotation（不會一直卡在隊首）" "[ \$(awk '{print \$3}' \$ROTATION_FILE | sort -u | wc -l) = 3 ]"
check "回傳非 0（讓上層知道有場次逾時）" "[ \$(cat \$T/run/exit.txt) != 0 ]"

echo; echo "[F] 連兩輪 0 筆的判斷（yield 檔）與排序（rotation）"
setup; unset PSJ_JOBS_JSON_FILE; rm -f "$PSJ_YIELD_FILE"
# 真的呼叫 psj_jobs_json（對正式資料庫只做 1 次 SELECT，唯讀）：先拿目前的優先職缺，再用假的 yield／rotation 檔驗證規則
real="$( . "$PIPE_DIR/sourcing_parallel.sh"; psj_jobs_json )"
read -r s1 s2 s3 < <(python3 -c 'import json,sys; j=json.loads(sys.argv[1]); print(*[x["slug"] for x in j[:3]])' "$real")
cat > "$PSJ_YIELD_FILE" <<EOF
2026-10-09 09:30:00 $s1 0
2026-10-09 14:30:00 $s1 0
2026-10-09 09:30:00 $s2 0
2026-10-09 14:30:00 $s2 2
2026-10-10 09:30:00 $s3 0
EOF
echo "2026-10-10 15:00:00 $s1" > "$ROTATION_FILE"      # s1 剛做過 → 應該排到最後面
out="$( . "$PIPE_DIR/sourcing_parallel.sh"; psj_jobs_json )"
python3 - "$out" "$s1" "$s2" "$s3" <<'PY'
import json, sys
js = json.loads(sys.argv[1]); s1, s2, s3 = sys.argv[2:5]
z = {j['slug']: j['zero2'] for j in js}
assert len(js) >= 3 and all({'slug', 'title', 'client', 'last', 'zero2'} <= set(j) for j in js)
assert z[s1] is True and z[s2] is False and z[s3] is False, z
assert js[-1]['slug'] == s1, [j['slug'] for j in js][-3:]          # 剛做過的在最後
print('  ✅ 真實優先職缺 %d 個；只有最近兩筆都是 0 才標 zero2（s1 是、s2 中間有 2 筆不是、s3 只有一筆不是）；剛做過的排最後' % len(js))
PY
[ $? = 0 ] || FAILS=$((FAILS+1))

echo; echo "[G] 截止時間解析：systemctl 的「CST」不能直接丟給 date -d（GNU date 把 CST 當 UTC-6）"
wrong="$(TZ=Asia/Taipei date -d 'Sat 2026-10-10 20:00:00 CST' +%s)"; right="$(TZ=Asia/Taipei date -d "$(echo 'Sat 2026-10-10 20:00:00 CST' | sed 's/ [A-Z]*$//')" +%s)"
check "直接解析會差 14 小時（$(( (wrong - right) / 3600 )) 小時）→ 程式拿掉時區縮寫再解析" "[ \$(( (wrong - right) / 3600 )) != 0 ] && [ \"\$(date -d @$right '+%F %T')\" = '2026-10-10 20:00:00' ]"
check "psj_deadline：指定 NIGHTLY_DEADLINE_EPOCH 時原樣回傳" "[ \"\$(. \$PIPE_DIR/sourcing_parallel.sh; MAX_SECS=600 NIGHTLY_DEADLINE_EPOCH=12345 psj_deadline)\" = 12345 ]"
check "psj_deadline：不指定時 ≥ 現在+15 分、≤ 現在+MAX_SECS" "d=\$(. \$PIPE_DIR/sourcing_parallel.sh; MAX_SECS=18000 psj_deadline); n=\$(date +%s); [ \$d -ge \$((n+890)) ] && [ \$d -le \$((n+18000)) ]"

echo; echo "[H] d1q 漏斗計數（離線：沒個人頁的路徑不連資料庫）"
export D1Q_FUNNEL_FILE="$T/f.jsonl" D1Q_RUN_ID="r1"; rm -f "$T/f.jsonl"
python3 - "$PIPE_DIR" <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, os.path.dirname(sys.argv[1]))
import d1q
out = d1q._sourced_gate([{'name': '甲', 'job_slug': 'job-x', 'source_url': 'https://news.example.com/a', 'grade': 'B'}])
assert out == [], out
d1q._funnel('job-x', 'written_ab', '乙'); d1q._funnel('job-y', 'client_blocked', '丙')
rows = [json.loads(l) for l in open(os.environ['D1Q_FUNNEL_FILE'])]
got = [(r['job_slug'], r['outcome'], r['run']) for r in rows]
assert got == [('job-x', 'no_page', 'r1'), ('job-x', 'written_ab', 'r1'), ('job-y', 'client_blocked', 'r1')], got
os.environ.pop('D1Q_FUNNEL_FILE'); d1q._funnel('z', 'x')      # 沒設環境變數就完全不動作
print('  ✅ 沒個人頁／寫入／客戶公司擋下都記到漏斗檔，帶 run 與職缺；沒設環境變數不動作')
PY
[ $? = 0 ] || FAILS=$((FAILS+1))

rm -rf "$T"
echo; echo "失敗 $FAILS 項"
exit $(( FAILS > 0 ))
