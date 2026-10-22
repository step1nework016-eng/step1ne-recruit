# E48：F002「後台只有一把萬用鑰匙」Stage A——WSL2 與 Phoebe 換成具名權杖

日期：2026-10-23（Mac 總指揮，Jacky 核准 Gmail 登入方案並說「你先做」）

## 一句話
後台 API 現在認得「每支程式自己的一把鑰匙」，舊的萬用鑰匙**還能用**。
WSL2 只要在 env 檔**加一行**，不用刪任何東西、不用改程式（git pull 就有新程式碼）。

## Mac 已做（Stage A）
1. `step1ne-backoffice-worker` 與 `step1ne-recruit-api` 兩個 Worker 都多認一份 secret `API_TOKENS_JSON`
   （名字 → sha256 雜湊，只存雜湊，固定時間比對）。舊的 `ADMIN_TOKEN` 照樣通過，記成 `legacy-admin`。
2. 每次開後台的門都記是誰：`wrangler tail` 看得到 `[auth] caller=…`；
   D1 表 `api_caller_seen`（每把權杖每 isolate 每小時最多寫一筆，不吃額度）。
3. 發了 5 把具名權杖：`mac-acai`（Mac 阿財）、`mac-workers`（Mac 其他程式）、
   `wsl2-workers`（WSL2 全部常駐程式）、`consultant-web`（瀏覽器後台）、`phoebe-ai`（Phoebe 的 AI）。
4. Python 新增 `api_token.py`：先找 `STEP1NE_API_TOKEN`（阿財另外先找 `STEP1NE_ACAI_TOKEN`），
   找不到就照舊讀 `RECRUIT_ADMIN_TOKEN`。已接上的程式：interview_daemon、parse_resumes、mailbox_poll、
   mail_outbox_send、mail_archive、checkup_daemon、consultant_ops、job_read、billing_read、bd_job_match、
   company_research、fetch_application、resume_to_pool、talent_sourcing_agent、doc_forward、
   jobintake/bd_daily_calls、jobintake/candidate_knock。
5. 驗證：舊鑰匙 200、5 把新鑰匙各 200、亂打的 401、沒帶 401（兩個 Worker 都測）。

## WSL2 要做的
1. **權杖值不在這份文件裡。** Jacky 會用 USB／scp 把 Mac 的
   `~/.config/workflow-os/new_api_tokens_handover.env` 帶過來。
2. 在 WSL2 的 `~/.config/workflow-os/tokens.env` **加一行**：
   `STEP1NE_API_TOKEN=<handover 檔裡 STEP1NE_API_TOKEN 那一行的值>`
   - `RECRUIT_ADMIN_TOKEN` **不要刪**（Stage B 才收）。
   - 檔案權限維持 600。不要貼 TG、不要寫進 git、不要寫進回報。
   - 若 WSL2 也有 `recruit.env`，不用動（程式會先讀 tokens.env 的新變數）。
3. `git pull` 拿到新程式碼後，重啟常駐程式（systemd 的 step1ne-* 服務、WSL2 阿財）。
4. 帶過來的 handover 檔用完就刪（`shred -u` 或 `rm`）。
5. 驗收：隔天請 Mac 查 D1
   `SELECT caller, worker, day, samples, last_ua FROM api_caller_seen ORDER BY day DESC, caller`
   要看到 `wsl2-workers`。回報寫在 `docs/wsl2/回報/E48_*.md`，**只寫變數名與結果，不寫值**。

## Phoebe 的 AI
- 用 `phoebe-ai` 那把（handover 檔裡 `PHOEBE_STEP1NE_API_TOKEN`），放進她 AI 讀的設定檔，
  變數名 `STEP1NE_API_TOKEN`。不要再給她萬用鑰匙。Jacky 線下交給她。

## 瀏覽器顧問後台
- `consultant-web` 那把：後台跳出「輸入權杖」時貼這把取代舊的萬用鑰匙。Stage B 完成後不再需要。

## 還在用舊鑰匙、Stage A 沒動的地方（Stage B 要處理）
- Worker 之間：recruit → backoffice（service binding）、backoffice 自己的排程呼叫自己，都用 Worker 自己的 `ADMIN_TOKEN`。
  這兩條不出網路，Stage B 改成內部專用 secret 即可。
- `step1ne-public-worker`（審核卡補發）、`step1ne-social-worker`（x-admin-token）仍只認舊 `ADMIN_TOKEN`。
- `step1ne-linkedin-extension`（Chrome 外掛）用使用者輸入的權杖。

---

## Stage B 計畫（只寫計畫，還沒做）

**目標：瀏覽器不再拿任何權杖；共用 ADMIN_TOKEN 作廢。**

1. **後台 API 搬到 step1ne.com 底下，讓 Access 一起保護**
   - 新路徑例如 `step1ne.com/api/bo/*`（Cloudflare 帳號 673dc… 的 zone），
     用一支小的轉接 Worker（或 Pages Function）轉到 backoffice。
   - 這條路徑加進現有 Access 應用程式「Step1ne 後台（正式版）」同一原則（3 個 Gmail）。
   - 轉接層／backoffice 驗 `Cf-Access-Jwt-Assertion`（對 team `soft-snow-e0ed` 的公開金鑰驗簽、
     檢查 aud 與 email 在名單內），記 caller＝登入者 email。
   - 因為 backoffice 在另一個帳號（152230d6），轉接層呼叫 backoffice 時帶一把只有轉接層有的
     具名權杖（例如 `access-proxy`），backoffice 只對這把信任它轉過來的 email header。
2. **前端改打新路徑**：consultant-app、consultant-app-beta、pick、sec 的 API 位址改成同網域
   `/api/bo/`，cookie 自動帶上，拿掉 localStorage 的 `s1ne_admin_tok`。先在 -beta 版試一週。
3. **AI 程式**：繼續用 Stage A 的具名權杖（之後可再換成 Access service token，非必要）。
4. **Worker 之間**：service binding 改用內部專用 secret（`INTERNAL_TOKEN`），不再用 ADMIN_TOKEN。
   public-worker、social-worker 也改認具名權杖。
5. **關掉舊鑰匙的條件**：`api_caller_seen` 連續 7 天沒有 `legacy-admin`
   （同時看 `wrangler tail` 抽查）→ 刪掉兩個 Worker 的 `ADMIN_TOKEN` 比對（保留 Worker 間內部 secret）→
   Mac／WSL2 env 刪 `RECRUIT_ADMIN_TOKEN`。出事時的退路：把 ADMIN_TOKEN 比對加回來重新部署（5 分鐘）。
6. 誰要停權：Gmail 從 Access 名單刪掉＋撤銷工作階段；AI 程式則把那把的雜湊從 `API_TOKENS_JSON` 拿掉，
   其他人不受影響。
