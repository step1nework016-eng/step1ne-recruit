執行 step1ne.com 的內容成效回顧（E54：由 Mac 搬到 WSL2，每月 1 號 09:00）。

請先完整讀這份技能包，然後依它的 Phase 0-4 執行：
/home/jack/claude-projects/工作流程技能包/seo-geo-workflow/content-performance-review/SKILL.md
（它會要你讀同資料夾的 ../content-opportunity-brief/ 底下的 scripts 與 sites；GSC 拉取用 scripts/gsc_pull.py）

**站台設定檔**：/home/jack/claude-projects/工作流程技能包/seo-geo-workflow/content-opportunity-brief/sites/step1ne.json
**紀錄輸出到**：/home/jack/aijob-automation/state/step1ne-seo-records/（刻意放在網站 repo 外面，step1ne.com 會公開 serve dotfile）
**網站原始碼**：/home/jack/claude-projects/step1ne-stopgap-site/

## 資料取得

這台已有 GSC service account（設定檔 `service_account_file` 已指向 /home/jack/.config/workflow-os/aijobseo-sa.json），請讓它直接拉 28 天與 90 天的 Page 級與 Query 級資料，**不要走「無資料版」**。
- ⚠️ 不要讀取、印出或複製金鑰檔內容，只能讓 gsc_pull.py 去用它。
- 如果 GSC 拉取失敗（權限、網路、property 不對），照實寫出錯誤原因與哪一步失敗，再退回技能包的「無資料版檢查」，並在報告開頭明講「本次無 GSC 資料，以下不是成效回顧」。不要編造數字。

## 輸出

寫成 `/home/jack/aijob-automation/state/step1ne-seo-records/<YYYY-MM-DD>-step1ne-內容成效回顧.md`，包含：
1. 摘要（共幾頁、各類幾頁、本次建議動幾頁）
2. 逐頁處置台帳（網址／類別／曝光／點擊／排名／處置／理由）——每一頁都必須有明確處置，「再觀察」不算決定
3. 本次待辦，依「改動成本低且影響大」排序
4. 跟上一份同名報告的差異：上次決定要改的，改了嗎？改完有效嗎？（找輸出資料夾裡上一份檔案比對；沒有上一份就寫「第一份，無可比對」）
5. 資料限制說明

最後另外寫一個純文字檔 `/home/jack/aijob-automation/state/step1ne-seo-records/last_summary.txt`：十行以內的結論（用繁體中文，先結論再細節），給系統轉發用。你自己不要發任何 Telegram。

## 硬性規則
- **只回報，不要修改網站 repo 的任何檔案、不要 commit、不要 push、不要部署。** 只能寫上面指定的輸出資料夾。
- 合併頁面與刪除頁面的建議一律標註「需人工確認」，不要自己做。
- 用繁體中文。查不到就標「未知」，不補 0、不猜。
