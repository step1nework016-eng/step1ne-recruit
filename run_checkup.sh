#!/bin/bash
# 健檢對談引擎（阿福）。KeepAlive 常駐——本人隨時可能進來，不能等排程。
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$HOME/claude-projects/工作流程技能包/step1ne-recruit" || exit 1
exec python3 checkup_daemon.py
