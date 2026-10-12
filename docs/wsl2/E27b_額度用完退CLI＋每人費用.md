# E27b｜Jacky 10/12：API 額度只儲 20 美元——用完要自動退回 CLI；每位人選要算花多少

## 先處理：金鑰沒綁工作區
你回報的「要帶 anthropic-workspace-id」：Jacky 會在 Console 重建一把綁 **Default workspace** 的金鑰（或給你 Workspace ID）。在那之前維持 CLI，不要動。

## 1. 額度保護（Jacky 只儲了 US$20）
- 額度不足 → 自動改走 CLI（你已做）。另外加：**第一次偵測到額度不足時發一則 TG 給 Jacky**（「API 額度用完，阿財已自動改回 CLI，要繼續用 API 請到 Console 儲值」），同一天只發一次。
- **預算線**：累計 API 花費（token_usage source='api' 加總）達 US$10、US$16 各發一次 TG 提醒；達 **US$19** 就主動切回 CLI（寫 `acai_llm=cli`）並通知，避免最後一場面談中途被切斷。預算數字放設定檔，之後儲值可改。
- 恢復：Jacky 回 TG「阿財改回 API」或 Mac 改設定才切回，不要自動切。

## 2. 每位人選的用量與費用
- `acai_token_report.py` 加入 `source='api'` 的列；新增「每位人選」彙總：人選、職缺、面談日期、呼叫次數、token（輸入／輸出／快取）、**美元**、**台幣**。
- 台幣換算：報表執行當下抓即時匯率（例如 open.er-api.com 的 USD→TWD），**報表上印出匯率數字、來源與時間**；抓不到就只給美元並註明「匯率未取得」，不准寫死一個數字。
- 單價：`claude_api.py` 現在用 $3／$15，但 10/8 用 CLI 回報反推 claude-sonnet-5 是 輸入 $2／輸出 $10／快取讀 $0.2／快取寫(1h) $4（見 acai_token_report.py 的 PRICE）。**以 API 回應實際使用的模型為準**，兩邊統一；打通後拿一場的 Console 帳單對一次。
- 每天 21:30 TG 推一則「今日阿財 API 花費」：每位人選一行（姓名｜職缺｜US$｜NT$），加總與目前累計／剩餘預算。沒有 API 花費的日子不推。
- 後台面談卡片之後要顯示（Mac 做），你先確保 token_usage 每列都有 application_id。

## 3. 補件
10/8 的交接檔已放進 repo：`docs/wsl2/交接_阿財用量量測_20261008.md`，照裡面方案核對你做的有沒有漏。

## 回報
`docs/wsl2/回報/E27b_日期時間.md`：預算線怎麼設、TG 範例訊息、每人報表範例（可用 CLI 舊資料示範）、單價統一後的數字。
