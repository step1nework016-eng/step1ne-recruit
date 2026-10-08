"""背景 AI（claude -p）統一上鎖設定（2026-10-08 資安：Jacky 核准先修）。

起因：ai_worker／job_switch／job_card 的禁用清單只寫了 Bash,Edit,Write,Read…，漏了 Glob／Grep。
實測（scratchpad/canary）：照 ai_worker 原本的設定，AI 真的讀得到工作資料夾裡的檔案內容；
履歷、人選訊息、網頁內容裡如果藏一段指令（提示注入），就可能讓 AI 去翻檔案再寫進輸出。
同一個測試：`--tools ''` 或阿財那份完整清單都讀不到 → 兩種一起用（雙保險）。

用法：
  from ai_lockdown import NO_TOOLS            # 純文字任務：一個工具都不給
  from ai_lockdown import web_only            # 只需要上網查資料：只留 WebSearch／WebFetch
⚠️ Claude Code 新增工具時，回來把名字補進 BAN。
"""
BAN = ('Task,Agent,Bash,BashOutput,KillShell,Glob,Grep,Read,Edit,Write,NotebookEdit,'
       'WebFetch,WebSearch,AskUserQuestion,TodoWrite,Skill,ToolSearch,Artifact,Monitor,Workflow,'
       'CronCreate,CronDelete,CronList,SendMessage,ListAgents,ListMcpResourcesTool,ReadMcpResourceTool,'
       'EnterPlanMode,ExitPlanMode,EnterWorktree,ExitWorktree,PushNotification,RemoteTrigger,ScheduleWakeup,TaskStop')
_ISOLATE = ['--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']
NO_TOOLS = ['--tools', '', '--disallowed-tools', BAN] + _ISOLATE


def web_only():
    ban = ','.join(t for t in BAN.split(',') if t not in ('WebSearch', 'WebFetch'))
    return ['--tools', 'WebSearch,WebFetch', '--allowedTools', 'WebSearch,WebFetch', '--disallowed-tools', ban] + _ISOLATE
