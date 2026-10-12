# E27c｜Jacky 10/12：阿財 API 先用假人選試跑一場

## 結論
Jacky 同意：**用測試應徵跑一場 API 面談**；舊面談重播**不做**。試跑沒問題 → Mac 看過逐字稿 → 就開給真實新人選（保留額度用完／出錯自動退回 CLI）。

## 測試應徵
Mac 正在建，名字「系統測試，請勿聯繫」、職缺 headhunter-consultant、email aiagentg888+acai-test@gmail.com。
id 會寫在 `docs/wsl2/回報/總指揮回覆_E17c_E21_E27_20261012.md`；也可以直接查 D1：
`SELECT id FROM applications WHERE email='aiagentg888+acai-test@gmail.com' ORDER BY created_at DESC LIMIT 1`

## 要你做
1. 確認**當下沒有真實面談在進行**（最近 10 分鐘沒有其他應徵的新訊息）才切。
2. 只對這筆測試應徵走 API（若程式只有全域開關，就在試跑那段時間開、跑完立刻關回 CLI）。
3. 用候選人身分把整場面談跑完（含測驗、正常回答、一題答很短、一題問反問），模擬真實節奏。
4. 回報 `docs/wsl2/回報/E27c_日期時間.md`：
   - 每輪回覆秒數（中位數／最慢），跟 CLI 平常的秒數對照
   - 整場花費（美元＋台幣）
   - 有沒有出現道歉句、格式跑掉、半形標點、重複問題
   - 面談報告有沒有正常產出
   - 逐字稿路徑（Mac 要看品質）
5. 跑完維持 CLI，等 Mac 看完逐字稿說 OK 才正式切換。
