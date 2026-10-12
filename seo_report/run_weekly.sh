#!/usr/bin/env bash
# Step1ne 搜尋成效週報：每週一自動更新（E33，WSL2 systemd timer 叫它）
#   git pull → auto_notes（這週做了什麼／回收日）→ build_weekly（GSC＋GA4）→ 寫 D1 → TG 摘要
# 用法：run_weekly.sh [--dry-run] [--date YYYY-MM-DD]
#   --dry-run：資料照抓、產出寫到暫存目錄，不寫 D1、不發 TG（第一次裝好先跑這個）
set -uo pipefail
export TZ=Asia/Taipei
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DRY=""; DATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --date) DATE="$2"; shift ;;
  esac
  shift
done
PY="${SEO_PY:-}"
[ -z "$PY" ] && { [ -x "$HOME/.venvs/seo_report/bin/python" ] && PY="$HOME/.venvs/seo_report/bin/python" || PY=python3; }
# 報告日一律是「這週的週一」：電腦週一關著、週二開機補跑（Persistent=true）時，日期還是週一那天
[ -z "$DATE" ] && DATE="$("$PY" -c 'from datetime import date,timedelta;d=date.today();print(d-timedelta(days=d.weekday()))')"
NOTG=""; [ "${SEO_NO_TG:-}" = "1" ] && NOTG="--no-tg"   # SEO_NO_TG=1：真的寫 D1 但不發 TG（驗收用）
if [ -n "$DRY" ]; then
  export SEO_REPORT_OUT="${SEO_REPORT_OUT:-$(mktemp -d /tmp/seo_report_dry.XXXX)}"
else
  export SEO_REPORT_OUT="${SEO_REPORT_OUT:-$HOME/aijob-automation/state/seo_report}"
fi
mkdir -p "$SEO_REPORT_OUT"
echo "== $(date '+%F %T') 週報 $DATE ${DRY:+(dry-run)} 產出→$SEO_REPORT_OUT"

fail() {
  echo "!! $1"
  "$PY" "$HERE/publish.py" --date "$DATE" ${DRY:+--dry-run} $NOTG --fail "$1"
  exit 1
}

git -C "$REPO" pull -q --ff-only origin main || echo "⚠️ git pull 失敗，用本機現有版本繼續"

"$PY" "$HERE/auto_notes.py" --date "$DATE" || echo "⚠️ 自動產生「這週做了什麼」失敗，週報會沿用上一份"

ok=""
for i in 1 2; do
  if "$PY" "$HERE/build_weekly.py" --date "$DATE"; then ok=1; break; fi
  echo "build_weekly 第 $i 次失敗"; sleep 60
done
[ -n "$ok" ] || fail "抓 Google 搜尋／GA4 數字失敗（常見原因：金鑰檔不在 ~/.config/workflow-os/aijobseo-sa.json）"

"$PY" "$HERE/publish.py" --date "$DATE" ${DRY:+--dry-run} $NOTG || fail "寫進後台資料庫失敗"
echo "== 完成"
