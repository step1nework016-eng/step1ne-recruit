#!/bin/bash
# E54b（2026-11-02 Jacky）：step1ne.com 內容成效月報，每月 1 號 09:00 由 systemd timer 跑（WSL2）。
# 只回報、不改網站檔案、不部署（規則寫在 content_review/prompt.md）。
# WSL2 的 Claude 被權限分類器擋下不能自己建這支（無人看管的 claude -p），由 Mac 寫好進 repo、Jacky 親手裝 timer。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p ~/aijob-automation/state/step1ne-seo-records ~/aijob-automation/logs
git pull -q origin main || true
echo "=== $(date '+%F %T') 內容成效月報開始"
claude -p --model claude-sonnet-5-5 --setting-sources '' --strict-mcp-config \
  --permission-mode bypassPermissions --output-format text \
  "$(cat content_review/prompt.md)" 2>&1 | tail -40
echo "=== $(date '+%F %T') 結束"
