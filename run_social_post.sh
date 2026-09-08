#!/bin/bash
# 顧問版社群發文 agent。不是常駐——一次性腳本，掃過還沒產草稿的職缺就結束，
# 靠 launchd 排程（每天一次）觸發，跟 checkup/interview 那種要即時回人的
# 常駐 daemon 不一樣。
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v22.22.0/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
cd "$HOME/claude-projects/工作流程技能包/step1ne-recruit" || exit 1
exec python3 social_post_agent.py
