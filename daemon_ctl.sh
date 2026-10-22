#!/bin/bash
# 手動重啟／查看 WSL2 常駐程式的唯一入口（2026-10-19）
#
# 為什麼要有：10/15 手動重啟社群程式時沒帶 crontab 裡的 PATH，npx 抓到 Windows 版，
# 輸出含繁中路徑（Big5）→ 程式解碼失敗，10/19 起整天產不出稿。
# 規則：不要再手打 python3 去重啟常駐程式；一律用本腳本，環境跟 crontab 完全一致。
#
# 用法：
#   ./daemon_ctl.sh status
#   ./daemon_ctl.sh restart social|aiworker|clientreport|talentsourcing|checkup|articlepublish|jdregen
#   ./daemon_ctl.sh restart <名稱> --dry     # 只印出會執行的指令
set -u
# ↓↓ 與 crontab 第一行 PATH 完全相同（nvm 的 node 必須在最前面，不能混進 /mnt/c 的 Windows 路徑）
export PATH="$HOME/.nvm/versions/node/v22.22.0/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
# 2026-10-22：不讓 Claude 在背景自己更新（更新那幾秒 claude 執行檔不存在，呼叫它的程式會失敗）。要更新請手動 claude update
export DISABLE_AUTOUPDATER=1
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGS="$HOME/aijob-automation/logs"

# 名稱 | pgrep 關鍵字 | 執行目錄 | 指令 | log
spec() {
  case "$1" in
    social)         echo "social_post_agent.py|$HERE|SOCIALPOST_HOST=wsl2 /usr/bin/python3 -u social_post_agent.py|$LOGS/socialpost.log" ;;
    aiworker)       echo "ai_worker.py|$HERE|/usr/bin/python3 -u ai_worker.py|$LOGS/aiworker.log" ;;
    clientreport)   echo "client_report_tick.py|$HERE|/usr/bin/python3 -u client_report_tick.py|$LOGS/clientreport.log" ;;
    talentsourcing) echo "talent_sourcing_tg_worker.py|$HERE|/usr/bin/python3 -u talent_sourcing_tg_worker.py|$LOGS/talentsourcingtg.log" ;;
    checkup)        echo "checkup_daemon.py|$HERE|/usr/bin/python3 -u checkup_daemon.py|$LOGS/checkup.log" ;;
    articlepublish) echo "article_publish_tick.py|$HERE|/usr/bin/python3 -u article_publish_tick.py|$LOGS/articlepublish.log" ;;
    jdregen)        echo "jd_regen_tick.py|$HERE/jobintake|/usr/bin/python3 -u jd_regen_tick.py|$LOGS/jdregen.log" ;;
    *) return 1 ;;
  esac
}
NAMES="social aiworker clientreport talentsourcing checkup articlepublish jdregen"

case "${1:-}" in
  status)
    for n in $NAMES; do
      IFS='|' read -r pat dir cmd log < <(spec "$n")
      pid=$(pgrep -f "python3.*$pat" | head -1)
      if [ -n "$pid" ]; then
        bad=$(tr '\0' '\n' < /proc/$pid/environ | grep '^PATH=' | grep -c '/mnt/c')
        echo "✅ $n  pid=$pid  啟動於 $(ps -o lstart= -p $pid)  $( [ "$bad" != 0 ] && echo '⚠️ PATH 含 Windows 路徑，請用 restart 重啟')"
      else
        echo "❌ $n  沒在跑（crontab 每 5 分鐘會自動拉起）"
      fi
    done ;;
  restart)
    n="${2:-}"; dry="${3:-}"
    spec "$n" >/dev/null 2>&1 || { echo "名稱錯誤。可用：$NAMES"; exit 1; }
    IFS='|' read -r pat dir cmd log < <(spec "$n")
    echo "目錄: $dir"; echo "指令: $cmd"; echo "log:  $log"; echo "npx:  $(command -v npx)"
    [ "$dry" = "--dry" ] && exit 0
    pkill -f "$pat"; sleep 3
    # setsid＋nohup：脫離呼叫它的終端機／WSL session，session 一關程式也不會跟著被關
    ( cd "$dir" && exec setsid nohup env $cmd >> "$log" 2>&1 < /dev/null ) &
    sleep 5
    pgrep -f "$pat" >/dev/null && echo "✅ 已重啟 $n" || echo "❌ 沒起來，看 $log" ;;
  *) echo "用法: $0 status | restart <名稱> [--dry]"; exit 1 ;;
esac
