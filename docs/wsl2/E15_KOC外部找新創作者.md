# E15｜WSL2：幫 KOC/KOL 案件到外面找新的保養創作者（2026-10-07 重新交辦）

> 這件原本編 E12，跟「阿財 AI 呼叫卡住」那份 E12 撞號，這台做的是阿財那份，這件還沒做。現在改編 E15，**請照這份做**。

## 結論先講
韓國保養品牌案（STEP1NE MATCH）系統人才池裡有信箱、合適的人已經全部寄完開發信（10/7 累計 204 封、35 位回覆）。Mac 的夜間自動搜尋每晚只補 1～4 位，不夠用。
**這台負責到 IG／Threads／YouTube 上找「系統裡還沒有」的新創作者，加進案件名單。**
**只找人、加名單，不寄任何信。** 寄信由 Mac 那邊在 Jacky 確認名單後處理。

## 要找什麼樣的人（全部都要符合）
- 人在台灣、內容以**美妝保養**為主（保養、膚質、彩妝實測），不是彩妝師工作室、店家、品牌帳號
- 粉絲 **3,000～100,000**（系統硬性上限 10 萬，超過會被擋）
- **簡介或連結頁看得到公開 Email**（沒信箱的不用加，這次只走 Email 開發）
- 不要找人在韓國／日本／美國的、簡介寫暫停合作的
- 目標：**加進 50 位有 Email 的合格人選**就停

## 怎麼做
1. 把 `docs/wsl2/koc-kol-discovery-skill/` 整包複製到 `~/.claude/skills/koc-kol-discovery-skill/`（這包是 Mac 版技能，已去掉金鑰）。
2. 用那個技能跑，參數：
   - 後端：`https://koc-kol-backend.lizkockol1688.workers.dev/api/v1`
   - workspaceId：`ws_ebefed43f04b4a77bd853af696897be2`
   - campaignId：`camp_10e89a29677a40368f24bdf62b178f11`（狀態是 reviewing，可以直接 start-external-search）
3. 每找到一位合格的就照技能流程：`POST /creators`（**email 欄位一定要填**）→ `POST /campaigns/:id/candidates/from-creator`。同一人系統會自動合併，不用怕重複。
4. 跑完一定要呼叫 `finish-external-search`，不然案件會卡在搜尋中。

## 不要做的事
- 不要寄信、不要私訊任何創作者
- 不要改案件狀態、不要動已經寄過信的人（outreach_messages 裡 send_status 不是 draft 的）
- 不要用付費爬蟲 API、不要寫爬蟲迴圈；IG 被擋就換 Threads／YouTube／Google 搜尋

## 回報
寫 `docs/wsl2/回報/E15_KOC找人_日期時間.md` 並 push，內容：
- 搜了哪些方向、看了幾個帳號、加進幾位（列帳號＋粉絲＋Email＋一句為什麼適合）
- 排除了幾位、主要原因
- 哪些平台搜不到或被擋（**沒測到的平台要明講，不能只回報有成功的**）
