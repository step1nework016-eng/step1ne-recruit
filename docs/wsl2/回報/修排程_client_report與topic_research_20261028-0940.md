# 回報：修 client_report_tick、topic_research_tick（WSL2 / DESKTOP-L5E8NAE）

- 時間：2026-10-28 09:40
- 沒動其他排程、沒碰真人選資料（client_report 只讀 D1 待處理清單；topic 只寫 topic_prompts）。

## 【1】client_report_tick

**原因：沒壞，是沒工作可做，而且沒工作時完全不寫 log。**
- pid 381 開機後一直在跑，每 60 秒掃 `client_report_requests` 的 pending／confirmed。
- 10/21 之後該表只剩一筆 `awaiting_confirm`（d52f9cd5，10/24 13:55，在等 Jacky 在 TG 按確認），沒有 pending／confirmed，所以整支沒東西可撈，log 就沒輸出。
- 沒卡迴圈（行程 sleep 中）、沒連不上 D1（心跳證明連得上）、沒被 Mac 搶鎖（搶鎖用原子 UPDATE，沒搶到只是 continue，且這段時間表裡根本沒有 pending）。
- 補充：Mac 看的 `client_formal_reports` 10/27 15:52 那筆，不是走這張請求表的流程，兩者不要混著看。
- 10/21 16:34 那次是最後一次有工作（含 D1 429 例外），之後只有「程式已更新，重新啟動」。

**改了什麼**
- `client_report_tick.py`：`tick()` 回傳本輪撈到的筆數；常駐迴圈每 30 輪（約 30 分鐘）寫一行心跳「已掃 N 輪，撈到待處理 N 筆」。其餘邏輯不變。
- 常駐行程偵測到檔案變更後自己重啟（仍是 pid 381，啟動參數不變，看門狗不會多開）。

**驗收證據**：log 出現 `[心跳 10-28 09:21:59] 已掃 1 輪，撈到待處理 0 筆`。
我沒有硬造一筆測試請求：真實處理一筆會產 PDF 並推 TG 給顧問。下一筆真的進來時，log 會出現「✅ 某某（id）客戶版履歷已產出並推送」。

**最後一次成功（有工作的那次）**：log 內最後一次 ✅ 是 10/21 以前（log 無日期，檔案最後寫入 10/21 16:34）；心跳最新成功 10/28 09:21:59。

## 【2】topic_research_tick

**原因：`claude -p`（含網路研究）寫死 600 秒逾時，正常一次就要 8.5～9.8 分鐘，跟 ai_worker／社群產稿搶 claude 時超過 10 分鐘被砍。**
- log：`subprocess.TimeoutExpired`（不是登入、不是網路）。
- 失敗日 10/23、10/28 都是剛好 10 分鐘（10:01、10:02）。10/27 的 systemd 紀錄其實是成功（08:39:51 Finished），log 也有「✅ 已生成」。
- 10/28 今天手動跑實測 **11 分 55 秒**（09:22:10→09:34:05），舊的 600 秒一定會被砍。

**改了什麼**
- `jobintake/topic_research_tick.py`：`TIMEOUT = 600` → `1500`（25 分鐘）。其他不動。這支本身不推 TG（只寫 topic_prompts），所以手動跑不會重發審核訊息。

**驗收**：手動跑 exit=0，log `[09:34:05] ✅ 已生成今日話題菜單：10 題`，D1 `topic_prompts` 新增 id 496～505（enterprise 5、jobseeker 5，created_at 09:33:50～09:34:05，來源 daily_menu）。

**注意**：今天的 daily_menu 因此有兩批（Mac 的 487～495 區段 08:37 那批＋我這批 496～505），後台「今日話題」會看到重複。我沒刪，要不要刪我這批（id 496～505）請總指揮決定。

**最後一次成功**：2026-10-28 09:34:05（本機手動）。排程版明天 08:30 會用新逾時跑。

（本報告不含機密值。）
