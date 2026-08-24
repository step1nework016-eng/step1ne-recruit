#!/bin/bash
# 職缺文案閉環：稽核 → 修 → 套用上線 → 重新稽核驗證
#
# 為什麼要串成一條（2026-08-21 Jacky 要求「這個要做閉環」）：
#   8/19 稽核跑完，六個職缺被標「不可上架」，兩天後**一個字都沒改、六個還全開著**。
#   問題不在稽核不準，在於「發現問題」跟「問題被解決」是兩件事，中間沒有人接。
#   把它們接成一條，跑完就是修好的，不是待辦清單又多六筆。
#
# ⚠️ 稽核一定要重跑，不能沿用上次的結果。
#   實測踩過：拿 8/19 的稽核去修 8/21 的頁面，報告裡說矛盾的那兩段文字
#   在現在的頁面上根本不存在（已經改過了），整輪白跑。
#
# 事實互相矛盾、客戶要不要具名這種，agent 不會自己猜，會列進「要人決定」推 TG。
# 那一段刻意不進閉環——猜錯一次就是把錯的變成唯一版本，之後沒人看得出來。

set -u
cd "$HOME/工作流程技能包/step1ne-recruit" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
LOG="$HOME/工作流程技能包/step1ne-recruit/logs/job_copy_loop-$(date +%Y-%m-%d).log"
mkdir -p "$(dirname "$LOG")"

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

# 2026-08-21 修：原本分兩步（① 全部稽核 → ② 全部修），中間如果有任何一次
# 手動或其他排程插進來重新稽核，fix 那步讀到的就是「別次」的結果，不是自己
# 剛稽核出來的——實測踩到：維運工程師的薪資揭露問題從 8/19 抓到，
# 中間有一次稽核把它稽核成「沒問題」，fix 讀到那次的空清單，
# 真正的問題就這樣被跳過，兩天沒人發現。
# 改成「一個職缺稽核完立刻修」，同一份資料進出，不會再有時間差。
say "① 逐一稽核＋修文案（同一個職缺一次做完，不分開跑）"
python3 - <<'EOF' >> "$LOG" 2>&1
import importlib.util, os
sp=importlib.util.spec_from_file_location('d', os.path.expanduser('~/工作流程技能包/step1ne-recruit/interview_daemon.py'))
m=importlib.util.module_from_spec(sp)
try: sp.loader.exec_module(m)
except SystemExit: pass
rows=m.d1("SELECT slug FROM jobs WHERE COALESCE(status,'open') != 'closed'")
print(' '.join(r['slug'] for r in rows))
EOF
JOBS=$(tail -1 "$LOG")
for slug in $JOBS; do
  python3 audit_job_copy.py "$slug" >> "$LOG" 2>&1
  python3 fix_job_copy.py "$slug" >> "$LOG" 2>&1
done

say "③ 等頁面部署完成"
sleep 90

say "④ 只驗證剛才改過的那幾個（不要全部重跑，那會多花一小時）"
CHANGED=$(python3 - <<'EOF'
import importlib.util, os
sp=importlib.util.spec_from_file_location('d', os.path.expanduser('~/工作流程技能包/step1ne-recruit/interview_daemon.py'))
m=importlib.util.module_from_spec(sp)
try: sp.loader.exec_module(m)
except SystemExit: pass
rows=m.d1("SELECT DISTINCT job_slug FROM job_audits WHERE verdict='已產修正版' "
          "AND audited_at >= datetime('now','+8 hours','-3 hours')")
print(' '.join(r['job_slug'] for r in rows))
EOF
)
if [ -z "$CHANGED" ]; then
  say "　這一輪沒有改動任何職缺，不用驗證"
else
  for slug in $CHANGED; do
    say "　驗證 $slug"
    python3 audit_job_copy.py "$slug" >> "$LOG" 2>&1
  done
fi

say "完成，紀錄在 $LOG"
