# E11｜WSL2：查這台叫 AI 為什麼比 Mac 慢一倍以上（2026-10-02 總指揮）

## 結論先講
同一句「回一個字：好」，**Mac 7.5 秒，這台 15～17 秒**（你之前測 --effort 時的數字）。
真正出事的是忙的時候：阿財 10/2 15:49 等了 240 秒逾時、電洽前卡片／客戶版履歷 8 分鐘逾時，**都在這台**。
`--effort` 已經測過不是原因。這次只要**量數字、找原因**，能安全修的就修。

## 要做的事（每一步把原樣輸出貼進回報）
1. 基本：`claude --version`、`node --version`、`which claude`、`nproc`、`free -h`、`uptime`
2. 空閒時量 3 次（每次分開跑）：
   ```
   time (echo "回一個字：好" | claude -p --model claude-sonnet-5 --strict-mcp-config --mcp-config '{"mcpServers":{}}' --setting-sources '' --output-format text)
   ```
3. 拆開看慢在哪：
   - 只啟動不呼叫：`time claude --version`（超過 2 秒＝啟動本身慢，查是不是裝在 /mnt/c 底下、或 node 跑在 Windows 那側）
   - 網路：`time curl -s -o /dev/null -w "%{time_connect} %{time_starttransfer}\n" https://api.anthropic.com`，跑 3 次
   - DNS：`cat /etc/resolv.conf`、`time getent hosts api.anthropic.com`
4. 忙的時候有幾個 AI 同時在跑：每 10 秒記一次、記 10 分鐘
   ```
   for i in $(seq 60); do echo "$(date +%T) $(pgrep -fc 'claude -p') $(cut -d' ' -f1 /proc/loadavg)"; sleep 10; done
   ```
   順便列出這台所有會叫 claude 的排程／常駐程式（阿財、阿福、ai_worker、社群、jobintake、夜間找人…）跟各自的同時上限。
5. 看 log：過去 24 小時每次 claude 呼叫花幾秒（interview_daemon、ai_worker 的 log 有記就統計：中位數、最慢 5 筆、逾時幾次、發生時間點）。逾時的時間點同時有哪些別的工作在跑。

## 可以自己修的（修了要寫前後數字）
- claude 裝在 `/mnt/c` 或用 Windows 的 node → 改裝在 Linux 這側
- DNS 很慢 → 修 resolv.conf（寫清楚改了什麼）
- 同時跑太多個 → 先**只寫建議的上限**，不要自己改上限（會影響面談，總指揮決定）

## 不要做的事
- 有人在面談（`interview_state='active'`）時不要重啟阿財
- ai_worker 有工作在跑時不要重啟
- 不要改 CLAUDE_TIMEOUT、不要換模型

## 回報
寫 `docs/wsl2/回報/E11_AI慢_日期時間.md` 並 push：每步原樣輸出＋一句話原因＋證據；沒做到的寫「沒做到」。
