# E27｜Jacky 10/12：阿財改用 Anthropic API（金鑰已建）

## 狀態
- Jacky 已在 Claude Console 建好 API 金鑰（名稱 step1ne-acai，Default workspace），**自己存進這台** `~/.config/workflow-os/anthropic.env`（變數 `ANTHROPIC_API_KEY`）。
- ⚠️ 金鑰只放這個檔（chmod 600），**不准印出、不准寫進 log／git／TG／任何對話**；程式用環境變數讀。
- Console 額度目前可能還是 0（Jacky 要去 Add funds）；呼叫若回「額度不足」就停下回報，不要一直重試。

## 要做
1. 先驗證檔案存在且格式對（只輸出 OK／不 OK，不輸出內容）。
2. 照你們 10/8 討論與 `交接_阿財用量量測_20261008.md` 的方案，把**阿財對談**改走 API（其他 AI 工作照舊用 CLI）；保留一鍵退回 CLI 的開關。
3. 確認**沒有面談進行中**才重啟；用測試應徵（姓名標「系統測試，請勿聯繫」、headhunter-consultant、aiagentg888+acai-test@gmail.com）跑一場驗證。
4. 回報每場面談的實際花費（token、美元）與回覆秒數，對照 CLI 版。

## 回報
`docs/wsl2/回報/E27_阿財API_日期時間.md`
