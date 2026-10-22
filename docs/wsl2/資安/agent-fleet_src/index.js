/**
 * Step1ne AI Agent 領取站 — 獨立部署，跟顧問後台（step1ne-backoffice-worker）
 * 完全分開：分開網址、分開 Worker、分開金鑰。FLEET_TOKEN 不是 RECRUIT_ADMIN_TOKEN，
 * 這裡的機密外洩不牽連顧問後台，反過來也一樣。
 *
 * 2026-10-22（F006）起：領取站只發腳本，不發任何金鑰值；AGENTS 的 env 定義只用來
 * 顯示「需要哪些金鑰的名字」。
 * 腳本本身（.py）不含任何機密，存在專屬的 R2 桶（跟其他 Worker 的檔案儲存分開）。
 */

const AGENTS = {
  ai_worker: {
    group: 'step1ne',
    char: { color: '#4f6df5', icon: 'chat', persona: '阿財' },
    name: '阿財面談 AI Worker',
    desc: '候選人跟 AI 阿財進行初步面談、履歷解析等常駐工作，輪詢共用的 ai_jobs 任務佇列。',
    workflow: ['候選人觸發面談／履歷解析', '排入共用 ai_jobs 佇列', '常駐程式每 20 秒撿件（搶鎖）', 'AI 處理，結果寫回 D1'],
    files: [
      { path: 'ai_worker.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
    ],
  },
  client_report_tick: {
    group: 'step1ne',
    char: { color: '#f5893d', icon: 'doc', persona: '小函' },
    name: '客戶推薦履歷確認 Worker',
    desc: 'AI 草稿客戶版履歷 → 顧問在 TG 人工確認／要求修改 → 產出 PDF 並寄送的常駐流程。',
    workflow: ['顧問按「製作客戶履歷」', 'AI 整理內容（ai_worker 合成）', '送 TG 草稿給顧問確認／改稿', '顧問按確認才真的產 PDF 寄出'],
    files: [
      { path: 'client_report_tick.py' },
      { path: 'd1_http.py' },
      { path: 'deliver.py' },
      { path: 'interview_daemon.py' },
      { path: 'reporttpl/client.html' },
      { path: 'reporttpl/consultant.html' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
        { key: 'TG_THREAD_ID', secret: 'TG_THREAD_ID' },
      ] },
      { file: 'tokens.env', lines: [
        { key: 'RECRUIT_ADMIN_TOKEN', secret: 'RECRUIT_ADMIN_TOKEN' },
      ] },
    ],
  },
  report_tick: {
    group: 'step1ne',
    char: { color: '#a855f7', icon: 'check', persona: '小報' },
    name: '阿財面談初篩報告 Worker',
    desc: '顧問在「顧問人選回報區」用人話講進度，AI 翻成漏斗狀態、送確認訊息。2026-09-10 補上多裝置搶鎖保護。',
    workflow: ['顧問在 TG 用人話回報進度', 'AI 翻譯成漏斗狀態＋摘要', '送出確認訊息＋按鈕', '顧問按「對」才真的寫入資料庫'],
    files: [
      { path: 'report_tick.py' },
      { path: 'interview_daemon.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
      ] },
    ],
  },
  consultant_call_report_tick: {
    group: 'step1ne',
    char: { color: '#f4437a', icon: 'phone', persona: '小電' },
    name: '顧問電訪報告 Worker',
    desc: '顧問新增人選時附的面談內容，自動產成初篩報告。2026-09-10 補上多裝置搶鎖保護。',
    workflow: ['顧問新增人選時附電訪內容', '標記 call_report_pending，排入等候', '常駐程式搶鎖處理（防重複）', 'AI 整理成正式初篩報告寫入 D1'],
    files: [
      { path: 'consultant_call_report_tick.py' },
      { path: 'consultant_call_report.py' },
      { path: 'interview_daemon.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
        { key: 'TG_THREAD_ID', secret: 'TG_THREAD_ID' },
      ] },
      { file: 'tokens.env', lines: [
        { key: 'RECRUIT_ADMIN_TOKEN', secret: 'RECRUIT_ADMIN_TOKEN' },
      ] },
    ],
  },
  jd_ai_draft_tick: {
    group: 'step1ne',
    char: { color: '#14b8a6', icon: 'pencil', persona: '小稿' },
    name: 'JD 文案 AI 草稿 Worker',
    desc: '職缺事實欄位存檔後，AI 自動草擬職缺文案（只寫 D1，不會 git push，可放心多裝置協作）。2026-09-10 補上多裝置搶鎖保護。',
    workflow: ['顧問存檔職缺事實欄位', '標記 jd_needs_ai_draft=1', '常駐程式搶鎖（原子 UPDATE）', 'AI 寫文案，結果存回 D1'],
    files: [
      { path: 'jd_ai_draft_tick.py' },
      { path: 'draft_job.py' },
      { path: 'publishing_filters.py' },
      { path: 'relation_rules.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
        { key: 'TG_THREAD_ID', secret: 'TG_THREAD_ID' },
      ] },
    ],
  },
  portal_import_tick: {
    group: 'step1ne',
    char: { color: '#22c55e', icon: 'inbox', persona: '小匯' },
    name: '用人需求表匯入 Worker',
    desc: '企業客戶在 portal 頁一鍵匯入的用人需求表（檔案／網址），AI 解析成結構化欄位。只寫 D1，不會 git push。2026-09-10 補上多裝置搶鎖保護。',
    workflow: ['客戶在 portal 上傳檔案／網址', '排入匯入佇列（status=pending）', '常駐程式搶鎖處理', 'AI 解析成結構化欄位存回 D1'],
    files: [
      { path: 'portal_import_tick.py' },
      { path: 'parse_resumes.py' },
      { path: 'interview_daemon.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
    ],
  },
  candidate_summary_tick: {
    group: 'step1ne',
    char: { color: '#06b6d4', icon: 'id', persona: '小卡' },
    name: '職缺候選人摘要卡 Worker',
    desc: '幫每個開放中職缺預先產一份候選人版摘要，顧問回覆社群留言時直接複製貼上。用 UPSERT 寫入，本身就對多裝置安全。',
    workflow: ['批次掃描開放中職缺', '找出還沒有摘要的職缺', 'AI 寫候選人版摘要', 'UPSERT 存入 D1 備用（顧問直接複製）'],
    files: [
      { path: 'candidate_summary_tick.py' },
      { path: 'social_post_agent.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
    ],
  },
  social_post_agent: {
    group: 'step1ne',
    char: { color: '#6366f1', icon: 'send', persona: '小推' },
    name: '社群貼文 AI Worker',
    desc: '顧問「一鍵發文」或排程貼文，AI 寫 Threads／LinkedIn 草稿送 TG 確認。本機只負責寫草稿，真正發文動作在雲端 Worker 那邊用獨立金鑰執行，這支腳本本身不需要 Threads/LinkedIn 金鑰。⚠️ SKILL.md 要放在跟腳本同一層的 recruiting-workflow/social-recruiting-post/ 子資料夾，不是隨便放。',
    workflow: ['顧問一鍵發文／設定排程時間', 'Worker 每 2 分鐘把到期排程轉檔', '本機程式每 90 秒撿件', 'AI 寫草稿，送 TG 給顧問確認', '顧問確認＋（有排程則等到時間）才真的發布'],
    files: [
      { path: 'social_post_agent.py' },
      { path: 'SKILL.md' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
      ] },
    ],
  },
  checkup: {
    group: 'step1ne',
    char: { color: '#f97316', icon: 'heart', persona: '阿福' },
    name: '阿福履歷健檢 AI Worker',
    desc: '求職者跟 AI 阿福進行履歷健檢對談的常駐工作，架構跟阿財（interview_daemon.py）同一套模式，本機常駐、輪詢、跨行程鎖。KeepAlive 常駐——本人隨時可能進來，不能等排程。',
    workflow: ['求職者觸發履歷健檢對談', '常駐程式輪詢佇列', 'AI 進行對談＋分析', '結果寫回 D1，產健檢報告'],
    files: [
      { path: 'checkup_daemon.py' },
      { path: 'd1_http.py' },
      { path: 'SKILL.md' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
        { key: 'TG_THREAD_ID', secret: 'TG_THREAD_ID' },
      ] },
    ],
  },
  placement_tracker: {
    group: 'step1ne',
    char: { color: '#0ea5e9', icon: 'flag', persona: '小蹤' },
    name: 'Submission → Placement 追蹤 Worker',
    desc: '人送出去給客戶之後追蹤到到職為止，發現停滯就建待辦、推播異常，不會自己判定候選人已到職或產生費用——那兩個值只能人工依真實證據寫入，程式本身沒有這個能力。',
    workflow: ['掃描所有進行中的送件案', '依 14 個狀態值的狀態機算 aging（停滯天數）', '停滯超過門檻就建待辦＋推播 TG', '顧問人工確認狀態才更新'],
    files: [
      { path: 'placement_tracker.py' },
      { path: 'placement_config.json' },
      { path: 'interview_daemon.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
      ] },
    ],
  },
  talent_sourcing_tg_worker: {
    group: 'step1ne',
    char: { color: '#8b5cf6', icon: 'search', persona: '小獵' },
    name: 'AI 獵頭顧問找人 Worker（TG 即時觸發＋排程）',
    desc: '顧問在 TG 專屬頻道打職缺關鍵字，或每天排程自動輪流幫在職顧問找人——實際搜尋交給 talent_sourcing_agent.py（開 WebSearch/WebFetch 真的去找，跑一次 15-40 分鐘），這支負責排隊、搶鎖、TG 觸發／回覆，避免跟排程版同時跑燒兩份 API 額度。⚠️ 需要本機 claude CLI 已登入且有 WebSearch 權限，這不是環境變數，要在新機器上另外用 `claude` 指令登入一次。',
    workflow: ['顧問在 TG 打職缺關鍵字，或排程每天觸發', '寫進 talent_sourcing_requests 佇列＋搶鎖（避免重複燒額度）', '呼叫 talent_sourcing_agent.py（claude -p 開 WebSearch）', '找到的人選寫入 sourced_candidates，結果回覆 TG'],
    files: [
      { path: 'talent_sourcing_tg_worker.py' },
      { path: 'talent_sourcing_agent.py' },
      { path: 'talent_sourcing_daily.py' },
      { path: 'interview_daemon.py' },
      { path: 'd1_http.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'CF_ACCOUNT_ID' },
      ] },
      { file: 'step1ne-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'TG_BOT_TOKEN' },
        { key: 'TG_CHAT_ID', secret: 'TG_CHAT_ID' },
        { key: 'TG_THREAD_ID', secret: 'TG_THREAD_ID' },
      ] },
    ],
  },

  // ── AIJOB（跟上面 Step1ne 那批完全分開的品牌／帳號，不共用任何金鑰）──
  seo_bottleneck: {
    group: 'aijob',
    char: { color: '#16a34a', icon: 'chart', persona: '小診' },
    name: 'SEO 斷點診斷 AI Worker（八環模型）',
    desc: '把 seo-bottleneck-diagnosis 技能包包成背景程式：拉 GSC 查詢字/頁面資料＋互搶掃描，交給 claude 純判斷寫報告（刻意不給 AI 任何工具權限，資料由腳本先備好，不讓 AI 自己現查）。⚠️ 除了下面這幾把金鑰，還要手動把 GSC service account JSON 放到 <code>~/Downloads/資料/aijobseo-c57ff314ac70.json</code>（這個檔案本身不透過這裡發放，太敏感、也不是簡單的 token）。目前只認得 aijob.com.tw，要診斷別的網站要先幫那個網站接好 GSC 存取權限。',
    workflow: ['（排程或手動）觸發診斷', '拉近 28 天 GSC 查詢字/頁面級資料', '跑互搶掃描（query×page 交叉）', 'claude 純判斷八環斷點，寫結構化報告', '存檔＋推播 TG'],
    files: [
      { path: 'seo_bottleneck_agent.py' },
      { path: 'SKILL.md' },
      { path: 'cannibalization_scan.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'AIJOB_CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'AIJOB_CF_ACCOUNT_ID' },
      ] },
      { file: 'tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_TG_BOT_TOKEN' },
        { key: 'TG_ALLOWED_CHATS', secret: 'AIJOB_TG_ALLOWED_CHATS' },
      ] },
    ],
  },
  seo_track: {
    group: 'aijob',
    char: { color: '#15803d', icon: 'chart', persona: '小追' },
    name: 'SEO 每日數據收集器',
    desc: '每天拉最近幾天的 GSC＋GA4 逐日資料＋即時在線人數，upsert 進 Cloudflare D1（aijob-seo-data），是其他 SEO agent（斷點診斷、站台細節、索引複查、頁面巡查）共用的資料底層。缺 <code>CLOUDFLARE_API_TOKEN</code>／<code>CLOUDFLARE_ACCOUNT_ID</code> 會直接中止（腳本自己檢查）。另外要手動放 GSC/GA4 service account JSON（跟 seo_bottleneck 同一支，這裡不重複發）。',
    workflow: ['排程每天觸發', '拉 GSC + GA4 逐日資料 + 即時在線人數', 'upsert 進 D1 aijob-seo-data'],
    files: [
      { path: 'seo_track.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'AIJOB_CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'AIJOB_CF_ACCOUNT_ID' },
      ] },
    ],
  },
  seo_sitedetail: {
    group: 'aijob',
    char: { color: '#0d9488', icon: 'doc', persona: '小詳' },
    name: 'SEO 站台細節收集器',
    desc: '每站抓 28 天的 GSC 查詢字前 100、GA4 路徑成效、CTA 轉換事件，比 seo_track 的全站數字更細，是站台等級的補充資料。⚠️ 品牌邊界注意：程式碼裡至少一個站台設定用的是 <code>cf-step1ne.env</code>（Step1ne 專屬憑證）而不是這裡發放的 <code>AIJOB_CF_*</code>——代表這支腳本本身橫跨兩個品牌的憑證，不是單純 AIJOB 專用。這裡只發 AIJOB 那組金鑰，Step1ne 那組要另外處理，不要把兩邊憑證混進同一個 env 檔。',
    workflow: ['依站台設定逐站觸發', '拉 28 天 GSC 查詢字 Top100', '拉 GA4 路徑成效 + CTA 轉換事件', '寫回 D1'],
    files: [
      { path: 'collect_site_detail.py' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'AIJOB_CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'AIJOB_CF_ACCOUNT_ID' },
      ] },
    ],
  },
  seo_indexrecheck: {
    group: 'aijob',
    char: { color: '#0369a1', icon: 'check', persona: '小驗' },
    name: 'SEO 索引複查追蹤',
    desc: '之前用 Indexing API 推過的頁面，到了驗收日回頭真的去查有沒有被收錄，不是推了就當作沒事——原本就是因為「沒有人回頭看」才補這支。⚠️ 透過本機已登入的 <code>npx wrangler</code>（在 <code>~/工作流程技能包/_system/worker</code> 目錄下執行 D1 指令）存取 D1，不是直接讀 <code>CLOUDFLARE_API_TOKEN</code> 環境變數——搬到新機器要嘛讓 wrangler 在那台機器重新登入，要嘛在執行前手動把 <code>CLOUDFLARE_API_TOKEN</code>／<code>CLOUDFLARE_ACCOUNT_ID</code> 匯出成環境變數（wrangler 會自動吃到），純發 cf.env 檔案不會自動生效。',
    workflow: ['讀出之前推送過索引的頁面清單', '逐一查詢目前的索引狀態', '判斷有沒有卡住', '推播 TG（總指揮 bot 私訊）'],
    files: [
      { path: 'recheck_index.py' },
    ],
    env: [
      { file: 'tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_TG_BOT_TOKEN' },
        { key: 'TG_ALLOWED_CHATS', secret: 'AIJOB_TG_ALLOWED_CHATS' },
      ] },
    ],
  },
  seo_reviewpatrol: {
    group: 'aijob',
    char: { color: '#b45309', icon: 'flag', persona: '小巡' },
    name: 'SEO 頁面改動巡查',
    desc: '巡 D1 的 page_fixes 表（頁面修正的改動紀錄），原本改完寫進表裡就沒人再看，這支負責回頭巡一遍、看改動有沒有真的生效或需不需要後續處理。⚠️ 跟 seo_indexrecheck 一樣，D1 存取靠本機已登入的 <code>npx wrangler</code>（同一個 <code>~/工作流程技能包/_system/worker</code> 目錄），不是吃 cf.env 裡的 token——搬機器前提同上。',
    workflow: ['讀出 page_fixes 表裡的改動紀錄', '逐筆核對現況', '判斷是否需要後續動作', '推播 TG（總指揮 bot 私訊）'],
    files: [
      { path: 'patrol_reviews.py' },
    ],
    env: [
      { file: 'tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_TG_BOT_TOKEN' },
        { key: 'TG_ALLOWED_CHATS', secret: 'AIJOB_TG_ALLOWED_CHATS' },
      ] },
    ],
  },
  seo_pagefix: {
    group: 'aijob',
    char: { color: '#7c2d12', icon: 'pencil', persona: '小改' },
    name: 'SEO 頁面修正草稿產生器（安全：只產草稿）',
    desc: '針對已發現的頁面問題（標題／meta／內容）產生修正草稿。✅ 自我認定是安全的：只產草稿，不自動套用——要實際套用，一定要顧問在 Telegram 上回覆確認後，由對話中手動執行，這支本身不會動到線上內容。用 <code>claude -p --model claude-sonnet-5 --permission-mode bypassPermissions</code> 執行。',
    workflow: ['讀入待修正的頁面問題清單', 'claude 依 prompt-page-fix.md 規範產出修正草稿', '推播草稿到 TG 等顧問確認', '確認後才由對話中手動套用（這支不自動套用）'],
    files: [
      { path: 'run-page-fix.sh' },
      { path: 'prompt-page-fix.md' },
    ],
    env: [],
  },
  seo_geotest: {
    group: 'aijob',
    char: { color: '#dc2626', icon: 'search', persona: '小測' },
    name: 'GEO／AI 引擎引用實測（⚠️ 風險較高：會自動 commit+deploy 上線）',
    desc: '⚠️ 跟這個 SEO 家族其他支不一樣，風險明顯更高：這支直接對「正在線上的」aijobnewwebsite 網站 repo 呼叫 <code>claude -p</code> 做修改，雖然用 <code>--disallowedTools "Bash(git push:*)"</code> 擋掉 AI 自己 push，但外層 wrapper 腳本會偵測 HEAD SHA 有沒有變化，如果 AI commit 了東西，wrapper 會自動幫它 push，push 完還會自動重建＋部署一個 Cloudflare Pages 儀表板（aijob-automation-status）。等於「AI 自己改網站程式碼 → 自動上線」全程沒有人工卡點。發這支之前務必想清楚：接手的人要能接受這個自動 commit+push+deploy 的行為，不是單純的唯讀診斷工具。',
    workflow: ['claude -p 依 prompt-geo-test.md 對線上網站 repo 做 GEO 實測/修正', '（AI 不能自己 push，但）wrapper 偵測到 commit 就自動 push', '自動重建＋部署狀態儀表板'],
    files: [
      { path: 'run-geo-test.sh' },
      { path: 'prompt-geo-test.md' },
    ],
    env: [],
  },

  // ── AIJOB 其他業務（2026-09-16 加，一樣照真實程式碼寫，沒查到的地方留白不用猜）──
  aijob_dailydigest: {
    group: 'aijob',
    char: { color: '#4d7c0f', icon: 'inbox', persona: '小彙' },
    name: '每日總結推播（安全：純彙整，不呼叫模型）',
    desc: '讀今天所有排程留下的執行紀錄（run-log.jsonl），算數字彙整成一則「今天做了什麼」推到 Telegram。不呼叫任何 AI 模型，純讀檔案+算數字，幾乎不會失敗——就算當天所有任務都掛了，這則總結還是會送到（而且會照實說掛了）。用的是 <code>boss-tg.env</code>，跟其他支用的 <code>tg.env</code>／<code>aijob-tg.env</code> 不是同一份。',
    workflow: ['固定時間觸發', '讀 run-log.jsonl + state.json', '比對「今天應該出現的任務」清單抓漏跑', '推播彙整到 Telegram'],
    files: [
      { path: 'daily-digest.py' },
    ],
    env: [
      { file: 'boss-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_BOSS_TG_BOT_TOKEN' },
        { key: 'TG_ALLOWED_CHATS', secret: 'AIJOB_BOSS_TG_ALLOWED_CHATS' },
      ] },
    ],
  },
  aijob_weeklyarticle: {
    group: 'aijob',
    char: { color: '#b91c1c', icon: 'pencil', persona: '小刊' },
    name: '每週文章寫作＋自動上線（⚠️ 寫完直接發布，無人工卡點）',
    desc: '⚠️ 2026-08-25 改成寫完直接上線——舊版只產草稿，結果連續三週草稿都沒人去發，網站停更 20 天，Owner 決定「直接發就好」。現在第 5 步會呼叫 <code>publish_article.py</code> 做上線前檢查與部署查證，腳本本身還會比對發文前後文章數確認真的多了一篇（防止 AI 回報「已上線」但其實卡關）。等於「AI 自己寫文章 → 自動上線」全程沒有人工確認，接手前要清楚這一點。',
    workflow: ['claude -p 依 prompt-weekly-article.md 寫一篇文章', '呼叫 publish_article.py 做上線前檢查＋部署', '腳本自行比對發文前後 posts.ts 文章數確認真的上線'],
    files: [
      { path: 'run-weekly-article.sh' },
      { path: 'prompt-weekly-article.md' },
    ],
    env: [],
  },
  aijob_weeklymetrics: {
    group: 'aijob',
    char: { color: '#0e7490', icon: 'chart', persona: '小析' },
    name: '官網 /pricing 實測數據更新（安全：只抓數字，不自動部署）',
    desc: '每週一更新官網 /pricing 頁要用的實測數據，只抓數字寫進 metrics.json＋推播通知，**不自動部署**——官網對外，數字抓錯就直接對外錯，抓完由 Jacky 決定要不要上線。依賴 aijobnewwebsite repo 裡的 <code>scripts/refresh-metrics.mjs</code>（這支本身不帶那個 node 腳本，要跟目標網站 repo 一起才動得了）。',
    workflow: ['跑 aijobnewwebsite repo 裡的 refresh-metrics.mjs 抓最新數字', '比對跑之前跑之後的 metrics.json 有沒有變', '推播「數字有沒有變、要不要更新」到 Telegram'],
    files: [
      { path: 'weekly-metrics-refresh.sh' },
    ],
    env: [
      { file: 'aijob-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_TG_BOT_TOKEN' },
      ] },
    ],
  },
  aijob_weeklyreview: {
    group: 'aijob',
    char: { color: '#dc2626', icon: 'search', persona: '小審' },
    name: '每週 SEO 總覽＋修正（⚠️ 風險較高：會自動 commit+deploy 上線）',
    desc: '⚠️ 跟 seo_geotest 同一種風險等級：直接對正在線上的 aijobnewwebsite repo 呼叫 <code>claude -p</code>，用 <code>--disallowedTools "Bash(git push:*)"</code> 擋掉 AI 自己 push，但外層 wrapper 偵測到 HEAD SHA 變化就會自動幫它 push（工單 #256：擋得住裸 git push，擋不住 subprocess 間接呼叫，所以整個搬出 prompt 由 wrapper 接手）。等於「AI 改網站程式碼 → 自動上線」沒有人工卡點，接手前務必清楚這點。',
    workflow: ['claude -p 依 prompt-weekly-review.md 對線上網站做每週 SEO 總覽＋修正', 'wrapper 偵測 commit 就自動 push', '（跟 seo_geotest 共用同一套自動部署收尾）'],
    files: [
      { path: 'run-weekly-review.sh' },
      { path: 'prompt-weekly-review.md' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'AIJOB_CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'AIJOB_CF_ACCOUNT_ID' },
      ] },
    ],
  },
  aijob_grantradar: {
    group: 'aijob',
    char: { color: '#a16207', icon: 'flag', persona: '小補' },
    name: '政府補助雷達（每天，安靜是預設值）',
    desc: '每天早上比老闆上工還早掃一次補助狀態。2026-08-25 從每週改成每天——補助的致命傷是「錯過」不是「太晚知道」，之前每週掃一次曾經漏過已經標出來但沒人查的稅籍登記問題。沒有新東西就不推播只寫紀錄；例外是截止倒數（30/14/7/3/1 天），那個寧可吵。有一段純字串比對（查財政部稅籍登記 CSV）完全不用 AI，另外才呼叫 claude -p 做補助掃描判斷。',
    workflow: ['先查財政部稅籍登記 CSV 有沒有生效（純字串比對，不用 AI）', 'claude -p 依 prompt-grant-radar.md 掃補助狀態', '沒新東西就安靜；截止倒數就推播'],
    files: [
      { path: 'run-grant-radar.sh' },
      { path: 'prompt-grant-radar.md' },
    ],
    env: [],
  },
  aijob_inboundtriage: {
    group: 'aijob',
    char: { color: '#7c3aed', icon: 'inbox', persona: '小收' },
    name: '收件匣分流（需要 Gmail 授權）',
    desc: '每天掃收件匣做分流判斷（哪些是 lead、哪些要回覆、哪些可以略過）。⚠️ prompt 裡明確依賴 Gmail——目標機器要有 Gmail 的授權通道（MCP 連接器或已設定好的憑證），不是單純裝 claude CLI 就能動，這裡不發放 Gmail 授權本身。',
    workflow: ['排在儀表板同步之後觸發，讓儀表板先有最新狀態', 'claude -p 依 prompt-inbound-triage.md 掃 Gmail 收件匣', '分流判斷：lead／提案／要處理／可略過', '技能包自己寫執行紀錄，wrapper 只補「連 claude 都沒跑起來」的情況'],
    files: [
      { path: 'run-inbound-triage.sh' },
      { path: 'prompt-inbound-triage.md' },
    ],
    env: [],
  },
  aijob_outreachwatch: {
    group: 'aijob',
    char: { color: '#be123c', icon: 'search', persona: '小盯' },
    name: '陌生開發回信追蹤（⚠️ 依賴整套 aijob-ops／ops.db 系統）',
    desc: '追蹤陌生開發信有沒有人回、有沒有該追的。⚠️ 這支不是單一腳本：實際跑的是 <code>aijob-ops</code> 這一整套系統（ops.db + gmail_telemetry.py + 多個互相依賴的 ops/*.py 模組），這裡只發主入口腳本，沒有把整套系統一起打包——要接手這支等於要接手整個 aijob-ops 資料夾，不是複製一個檔案就能跑。有一段 2026-09-11 修過的真實事故：Gmail 檢查失敗時用字串比對誤判成功（`DATA_SOURCE_UNAVAILABLE` 裡剛好包含 `AVAILABLE` 子字串），已改用結束代碼判斷。',
    workflow: ['查 Gmail 通不通（結束代碼判斷，不是字串比對）', '抓回信', '算該不該追', '有事才推播，沒事不吵'],
    files: [
      { path: 'outreach-reply-watch.sh' },
    ],
    env: [],
  },
  aijob_salesfollowup: {
    group: 'aijob',
    char: { color: '#0891b2', icon: 'phone', persona: '小銷' },
    name: '業務追蹤（新 lead／卡住的案子）',
    desc: '掃新進的 lead 跟卡住沒進度的案子，判斷該怎麼追。標準 claude -p wrapper 模式，跟 client-bd／trend-scout 同一套骨架。',
    workflow: ['claude -p 依 prompt-sales-followup.md 掃業務狀態', '判斷新 lead／該追的卡住案子', '寫執行紀錄'],
    files: [
      { path: 'run-sales-followup.sh' },
      { path: 'prompt-sales-followup.md' },
    ],
    env: [],
  },
  aijob_meetingreminder: {
    group: 'aijob',
    char: { color: '#ea580c', icon: 'check', persona: '小提' },
    name: '會議提醒（不靠 Google Calendar，2026-09-07 建）',
    desc: '⚠️ 起因：Google Calendar 授權 2026-09-04 被撤銷，永久解法要 Owner 本人手動點一個等了超過 264 小時還沒點的按鈕。在那之前，這支直接讀 ops.db 的 meetings 表推 Telegram——**不是** Calendar 的替代品，不會建立事件、看不到 Owner 自己在 Calendar 上排的其他事，只保證「ops.db 記過的會，時間到之前手機會響」。每場會在 T-24h／T-2h／T-15m 各推一次，靠 ops.db 的 meeting_reminders 表去重，所以可以每 5 分鐘跑一次不會洗版。同樣依賴 ops.db，不是完全獨立的單一腳本。',
    workflow: ['每 5 分鐘輪詢 ops.db 的 meetings 表', '算每場會現在是哪個提醒時點（T-24h/T-2h/T-15m）', '查 meeting_reminders 表去重', '推播 Telegram（附 Meet 連結）'],
    files: [
      { path: 'meeting_reminder.py' },
    ],
    env: [
      { file: 'aijob-tg.env', lines: [
        { key: 'TG_BOT_TOKEN', secret: 'AIJOB_TG_BOT_TOKEN' },
      ] },
    ],
  },
  aijob_trendscout: {
    group: 'aijob',
    char: { color: '#65a30d', icon: 'search', persona: '小趨' },
    name: 'AI 趨勢選題（每日）',
    desc: '每天掃 AI 相關趨勢/新聞，選出適合寫成文章的題目候選。跟 weeklyarticle 是接力關係：這支選題，weeklyarticle 負責寫成文章上線。標準 claude -p wrapper 模式。',
    workflow: ['claude -p 依 prompt-trend-scout.md 掃當天 AI 趨勢', '篩出候選題目', '寫執行紀錄（candidates/passed 數字）'],
    files: [
      { path: 'run-trend-scout.sh' },
      { path: 'prompt-trend-scout.md' },
    ],
    env: [],
  },
  aijob_crawlerrevival: {
    group: 'aijob',
    char: { color: '#059669', icon: 'heart', persona: '小活' },
    name: '爬蟲系統復活偵測',
    desc: '定期檢查 headhunter-crawler（見下面 aijob_crawler）有沒有「復活」——沒事安靜，活了才出聲。是獨立於爬蟲本體之外的守望腳本，本身只需要 claude CLI + cf.env，不需要整套爬蟲系統也能跑。',
    workflow: ['claude -p 依 prompt-crawler-revival-check.md 檢查爬蟲系統狀態', '沒事不推播', '偵測到復活才出聲'],
    files: [
      { path: 'run-crawler-revival-check.sh' },
      { path: 'prompt-crawler-revival-check.md' },
    ],
    env: [
      { file: 'cf.env', lines: [
        { key: 'CLOUDFLARE_API_TOKEN', secret: 'AIJOB_CF_API_TOKEN' },
        { key: 'CLOUDFLARE_ACCOUNT_ID', secret: 'AIJOB_CF_ACCOUNT_ID' },
      ] },
    ],
  },
  aijob_crawler: {
    group: 'aijob',
    char: { color: '#334155', icon: 'id', persona: '小蟲' },
    name: '人才爬蟲系統（⚠️ 完整 Flask 應用，不是單一腳本——這裡只登記，不透過這裡發放）',
    desc: '⚠️ 跟這個領取站其他所有 agent 不一樣：這不是一支可以單檔案+env 搬走的腳本，而是一整個 Flask 應用（<code>~/clawd/headhunter-crawler/</code>），有自己的 venv、yaml 設定、獨立 .env（API keys）、多來源爬取邏輯。這裡只誠實登記它的存在跟用途，不透過這個領取站的單檔案 R2 發放機制搬——真的要搬這支，要整個資料夾＋venv 一起處理，不是點一下下載。',
    workflow: ['Flask 應用常駐', '多來源掃描候選人（CakeResume 等）', '寫入候選人資料庫'],
    files: [],
    env: [],
  },
  aijob_businessreview: {
    group: 'aijob',
    char: { color: '#9333ea', icon: 'check', persona: '小效' },
    name: '商業結果審查（⚠️ 依賴整套 aijob-ops／ops.db 系統）',
    desc: '每天 08:50（Owner 09:00 上工前）判斷「做的事有沒有效」，寫成待辦讓 Owner 一上工就看到。刻意獨立排程、不掛在 preflight.sh 底下——preflight 檢查沒過會整支 exit 1，後面全部不跑，2026-08-24 的鑑識抓到「邏輯存在但不保證會執行」這個問題，掛在會提前結束的腳本裡等於再犯一次。⚠️ 跟 outreachwatch／meetingreminder 一樣，依賴整套 aijob-ops 系統（先跑 owner_inbox.py 把 Owner 在儀表板上的回覆拉進 ops.db，再跑 business_review.py 判斷），不是單一腳本可以獨立搬走。',
    workflow: ['先跑 owner_inbox.py 把 Owner 昨天的儀表板回覆拉進 ops.db（順序重要，避免「不做了」的單又被算進待辦）', '跑 business_review.py 判斷做的事有沒有效', '寫成待辦'],
    files: [
      { path: 'business-review.sh' },
    ],
    env: [],
  },
  // ── 通用工具包（scope:'generic'，2026-09-16 加）──────────────────────
  // 跟上面所有 agent 不一樣：這幾支刻意不綁死 Jacky 自己的任何帳號/資料庫，
  // 給的是方法本身，不是「接手這個業務」。發放給不相關的人時要用
  // GENERIC_FLEET_TOKEN，不是 FLEET_TOKEN——兩組權杖互不相通，見 checkToken()。
  generic_bg_scanner: {
    group: 'generic', scope: 'generic',
    char: { color: '#475569', icon: 'search', persona: '阿掃' },
    name: '跨平台背景程式監控掃描器',
    desc: '掃這台機器上有哪些背景/排程程式在跑——不限 macOS launchd，Linux/WSL2（cron、systemd --user）跟 Windows（schtasks）都認得，輸出統一格式。純 Python 標準庫，不依賴任何額外套件（刻意不用 psutil），不需要任何帳號、金鑰、資料庫——下載下來就能跑，跟這個領取站上其他任何一支都無關。',
    workflow: ['偵測目前作業系統', 'macOS 掃 launchd plist；Linux 掃 cron+systemd；Windows 掃 schtasks', '同時掃目前正在跑的行程（關鍵字比對）', '輸出文字報告或 JSON'],
    files: [
      { path: 'agent_process_scan.py' },
    ],
    env: [],
  },
  generic_seo_bottleneck: {
    group: 'generic', scope: 'generic',
    char: { color: '#15803d', icon: 'chart', persona: '阿診' },
    name: 'SEO 斷點診斷方法論（八環模型）',
    desc: '從「有沒有需求」到「有沒有成交」拆成八個環節，先找出第一個拿不出證據說它通過的環節，再決定要做什麼——而不是看到什麼數字異常就動手改。附一支互搶掃描腳本（同一個關鍵字被自己好幾頁瓜分排名）跟一支範例 agent（怎麼把這套方法包成排程自動跑）。要接這套方法到你自己的網站，換成你自己的 GSC service account 憑證跟自己的 Telegram bot 就能動，不需要任何跟這個專案有關的帳號。',
    workflow: ['讀 SKILL.md 的八環診斷規範', '拉近 28 天 GSC 查詢字/頁面級資料（換成你自己的 GSC 憑證）', '跑互搶掃描', '寫出「斷在第幾環」的結構化報告'],
    files: [
      { path: 'SKILL.md' },
      { path: 'seo_bottleneck_agent.py' },
      { path: 'cannibalization_scan.py' },
    ],
    env: [],
  },
  generic_daily_digest: {
    group: 'generic', scope: 'generic',
    char: { color: '#4d7c0f', icon: 'inbox', persona: '阿彙' },
    name: '每日執行摘要彙整範本',
    desc: '如果你手上有好幾支各自獨立的排程，各自跑完會往同一份紀錄檔追加結果，這支負責讀檔案算數字彙整成一則「今天做了什麼」推播出去。這是範本版——把 EXPECTED_DAILY 換成你自己的任務清單、RUN_LOG 換成你自己的記錄檔路徑、TG_ENV 換成你自己的 Telegram bot 設定即可。純讀寫不呼叫任何 AI 模型，幾乎不會失敗，就算所有任務都掛了這則總結還是送得出去。',
    workflow: ['讀今天的 JSONL 執行紀錄', '比對「今天應該出現的任務」清單抓漏跑', '彙整成一則總結', '推播到你自己的 Telegram'],
    files: [
      { path: 'generic_daily_digest.py' },
    ],
    env: [],
  },
  generic_talent_sourcing: {
    group: 'generic', scope: 'generic',
    char: { color: '#be185d', icon: 'search', persona: '阿獵' },
    name: '獵頭開發方法論（台灣在地）',
    desc: '兩支技能包打包成一份 zip：talent-research-strategy（單一職缺的研究流程——公司/產業地圖、分層人才畫像、分波搜尋時程、接觸話術、BARS/STAR 評分、薪酬對標、派遣合規判定，以及陌生開發的目標公司掃描/決策窗口盤點）＋ talent-intelligence-sourcing（把模糊職缺校準成候選人畫像，分類進搜尋生態系，建立/擴充/評分/去重可稽核的候選人池）。純方法論，沒有綁任何客戶名單或帳號，下載解壓縮就是一套完整的獵頭工作流可以直接照著做。',
    workflow: ['職缺校準：拆解成可搜尋的候選人畫像', '公司/產業地圖 + 分層人才畫像', '分波搜尋 + 接觸話術 + 薪酬對標', '陌生開發：目標公司掃描 + 決策窗口盤點'],
    files: [
      { path: 'talent-sourcing-skillpack.zip' },
    ],
    env: [],
  },
  generic_grant_radar: {
    group: 'generic', scope: 'generic',
    char: { color: '#a16207', icon: 'flag', persona: '阿補' },
    name: '政府補助追蹤方法論（三支技能接力）',
    desc: '三支技能包打包成一份 zip，接力運作：grant-profile（建立你自己的公司資格檔案，對話式收集，缺什麼追問什麼）→ grant-scan（掃官方補助來源，擷取欄位、去重、偵測變更）→ grant-match（逐條比對資格、算適合度分數、產出通知文字）。⚠️ 這裡<b>沒有</b>附任何真實公司資料——原本 grant-scan/grant-match 讀的 <code>company-profile.json</code> 是你自己的公司資訊，要用 grant-profile 這支重新對話建立你自己的檔案，不是套用別人的。',
    workflow: ['grant-profile 對話式建立你自己的公司資格檔案', 'grant-scan 掃官方補助來源，去重+偵測變更', 'grant-match 逐條比對資格，算適合度分數，產通知文字'],
    files: [
      { path: 'grant-radar-skillpack.zip' },
    ],
    env: [],
  },
  generic_seo_pro: {
    group: 'generic', scope: 'generic',
    char: { color: '#0f766e', icon: 'chart', persona: '阿域' },
    name: 'SEO／AEO／GEO 全站服務方法論',
    desc: '三支技能包打包成一份 zip，比 generic_seo_bottleneck（單純斷點診斷）更完整的全站服務流程：whitehat-site-seo-audit（技術稽核——schema、內鏈孤兒頁修復、sitemap 缺口、Core Web Vitals、GSC 內容分級決策表）＋ seo-aeo-geo-strike（把技術稽核跟內容機會整合成一份可調參數的客戶服務方案，分「新站建置」跟「既有站追蹤優化」兩種模式）＋ content-opportunity-brief（GSC+Trends+搜尋意圖驅動的選題策略）。⚠️ 已經拿掉 content-opportunity-brief 原本 <code>sites/</code> 資料夾裡 Jacky 自己客戶的設定檔，要用自己的網站資料重新建。',
    workflow: ['whitehat-site-seo-audit 做技術/schema/內鏈稽核', 'content-opportunity-brief 用 GSC+Trends 找選題機會', 'seo-aeo-geo-strike 整合成一份可調參數的服務方案（新站建置/既有站追蹤兩種模式）'],
    files: [
      { path: 'seo-pro-skillpack.zip' },
    ],
    env: [],
  },
  generic_safe_ai_pattern: {
    group: 'generic', scope: 'generic',
    char: { color: '#7c2d12', icon: 'flag', persona: '阿範' },
    name: '安全的 AI 背景自動化範式（文件，非程式碼）',
    desc: '不是可執行程式，是從實際踩過的坑整理出來的架構寫法：草稿優先＋人工卡點（安全）、把 git push 這類危險動作搬出 AI 的 session（更高自主權但要清楚知道風險）、失敗不能無聲（cron 掛了要有人知道）。這個領取站上凡是標「安全」或標「⚠️ 風險較高」的 agent，用的都是這份文件裡講的其中一種寫法，這裡把架構抽出來單獨講清楚，讓你套在自己任何場景。',
    workflow: ['判斷這個自動化動作影響的是不是對外看得到的東西', '選草稿優先或高自主權兩種寫法之一', '不管哪種都加上失敗不無聲的保險'],
    files: [
      { path: 'safe_ai_wrapper_pattern.md' },
    ],
    env: [],
  },

  aijob_clientbd: {
    group: 'aijob',
    char: { color: '#c026d3', icon: 'search', persona: '小拓' },
    name: '客戶開發掃描（沒職缺可推的候選人／公司）',
    desc: '掃「有候選人但沒對應職缺可推」的情況，反過來找有沒有適合開發的公司/客戶。標準 claude -p wrapper 模式。',
    workflow: ['claude -p 依 prompt-client-bd.md 掃候選人池跟公司', '找沒職缺可推的候選人對應的潛在客戶', '寫執行紀錄（candidates_without_job/companies_scanned 數字）'],
    files: [
      { path: 'run-client-bd.sh' },
      { path: 'prompt-client-bd.md' },
    ],
    env: [],
  },

  // ── KOC/KOL（另一個品牌，同樣完全分開的金鑰／帳號）──
  // ⚠️ 這五支是 2026-09-16 從這台機器現有的 launchd 排程反查回來的，不是我在
  // koc-kol-matching 那個對話裡從頭設計的——描述照程式碼跟腳本裡的註解如實寫，
  // 沒有查到的地方寧可留白也不要用猜的。
  kockol_ai_text_proxy: {
    group: 'kockol',
    char: { color: '#db2777', icon: 'chat', persona: '小代' },
    name: 'KOC/KOL AI 文字代理 Worker（常駐）',
    desc: '本機常駐的 HTTP 代理，接住 campaign-summary／outreach-email／performance-insights 這幾種 AI 文字產生請求，轉呼叫本機 claude CLI 處理。KeepAlive 常駐。不需要任何 API 金鑰／帳密——後端只認 workspace header，不是機密。',
    workflow: ['後端 Worker 打進這支本機代理', '轉呼叫本機 claude CLI', '結果回傳給後端 Worker'],
    files: [
      { path: 'ai_text_proxy.py' },
    ],
    env: [
      { file: 'koc-kol.env', lines: [
        { key: 'KOC_KOL_BACKEND_URL', secret: 'KOCKOL_BACKEND_URL' },
        { key: 'KOC_KOL_WORKSPACE_ID', secret: 'KOCKOL_WORKSPACE_ID' },
      ] },
    ],
  },
  kockol_brief_parser: {
    group: 'kockol',
    char: { color: '#a21caf', icon: 'doc', persona: '小析' },
    name: 'KOC/KOL 品牌簡報解析 Daemon（常駐）',
    desc: '常駐輪詢，把品牌方丟進來的 brief（brand brief）解析成結構化欄位存回後端。不需要 API 金鑰／帳密，只認 workspace header。',
    workflow: ['品牌方上傳 brief', '常駐程式輪詢撿件', 'claude 解析成結構化欄位', '寫回後端 API'],
    files: [
      { path: 'brand_brief_parser_daemon.py' },
    ],
    env: [
      { file: 'koc-kol.env', lines: [
        { key: 'KOC_KOL_BACKEND_URL', secret: 'KOCKOL_BACKEND_URL' },
        { key: 'KOC_KOL_WORKSPACE_ID', secret: 'KOCKOL_WORKSPACE_ID' },
      ] },
    ],
  },
  kockol_report_narrative: {
    group: 'kockol',
    char: { color: '#7c3aed', icon: 'check', persona: '小敘' },
    name: 'KOC/KOL 成效報告敘事 Daemon（常駐）',
    desc: '常駐輪詢，把成效數字轉成給客戶看的白話敘事段落。不需要 API 金鑰／帳密，只認 workspace header。',
    workflow: ['成效數字備妥，排入等候', '常駐程式輪詢撿件', 'claude 寫成白話敘事', '寫回後端 API'],
    files: [
      { path: 'report_narrative_daemon.py' },
    ],
    env: [
      { file: 'koc-kol.env', lines: [
        { key: 'KOC_KOL_BACKEND_URL', secret: 'KOCKOL_BACKEND_URL' },
        { key: 'KOC_KOL_WORKSPACE_ID', secret: 'KOCKOL_WORKSPACE_ID' },
      ] },
    ],
  },
  kockol_night_discovery: {
    group: 'kockol',
    char: { color: '#0891b2', icon: 'search', persona: '小夜' },
    name: 'KOC/KOL 夜間創作者搜尋（排程，需瀏覽器能力）',
    desc: '⚠️ 跟其他 KOC/KOL agent 不一樣、依賴更重：這支會透過 npx 啟動 Playwright MCP 做真實瀏覽器操作（登入牆、IG/Dcard 這類擋 WebFetch 的平台要靠它才進得去），目標機器除了 claude CLI，還要有 Node/npx 且能跑 Playwright，不是單純裝 claude CLI 就能動。目前針對單一活動（UXKB JellyLine Q4）寫死，換活動要改腳本裡的 CAMPAIGN_ID。',
    workflow: ['launchd 排程每隔約 2 小時觸發一次（23:00–10:00）', 'claude 開 Playwright MCP 做真實瀏覽器操作找創作者', '寫入候選人池', '整夜跑完由夜間彙整那支收尾'],
    files: [
      { path: 'run_scheduled_night_discovery.sh' },
    ],
    env: [
      { file: 'koc-kol.env', lines: [
        { key: 'KOC_KOL_BACKEND_URL', secret: 'KOCKOL_BACKEND_URL' },
        { key: 'KOC_KOL_WORKSPACE_ID', secret: 'KOCKOL_WORKSPACE_ID' },
      ] },
    ],
  },
  kockol_night_digest: {
    group: 'kockol',
    char: { color: '#0d9488', icon: 'inbox', persona: '小結' },
    name: 'KOC/KOL 夜間搜尋彙整（排程）',
    desc: '每天固定時間跑一次，把整夜幾輪 kockol_night_discovery 留下的原始 stream-json log 彙整成一份人看得懂的文字摘要。純文字處理，不需要瀏覽器能力，只要有 claude CLI。',
    workflow: ['固定時間觸發（晚一輪夜間搜尋跑完之後）', '讀取當晚所有 stream-json log', '抽出每輪的摘要文字彙整成一份', '輸出彙整檔'],
    files: [
      { path: 'run_scheduled_night_digest.sh' },
    ],
    env: [],
  },
};

function cors(body, status, extraHeaders) {
  return new Response(body, {
    status: status || 200,
    headers: Object.assign({ 'access-control-allow-origin': '*' }, extraHeaders || {}),
  });
}
function json(obj, status) {
  return cors(JSON.stringify(obj), status || 200, { 'content-type': 'application/json; charset=utf-8' });
}

function tokenEq(given, secret) {
  if (!secret || !given) return false;
  if (given.length !== secret.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ secret.charCodeAt(i);
  return diff === 0;
}

// 2026-09-16 加：分兩組權杖——FLEET_TOKEN 給「真的要接手運營這個專案」的人（看得到
// Step1ne/AIJOB/KOC-KOL 那些綁死自家帳號金鑰的 agent）；GENERIC_FLEET_TOKEN 給
// 「只想拿方法/工具去用在別的專案」的人（只看得到 scope:'generic' 那幾支，
// 檔案本身也不含任何 Jacky 自己的密鑰值）。兩組權杖互不相通：project 權杖進不去
// generic 專屬的內容以外沒差別（generic 本來就沒機密），但 generic 權杖絕對不能
// 看到 project 那批——那批帶著真的密鑰。
function checkToken(request, env) {
  const auth = request.headers.get('authorization') || '';
  const m = /^Bearer\s+(.+)$/.exec(auth.trim());
  const given = m ? m[1] : '';
  if (!given) return null;
  if (tokenEq(given, env.FLEET_TOKEN)) return 'project';
  if (tokenEq(given, env.GENERIC_FLEET_TOKEN)) return 'generic';
  return null;
}

async function agentStats(env) {
  const stats = {};
  try {
    const { results } = await env.DB.prepare(
      `SELECT worker_id, MAX(started_at) AS last_seen, COUNT(*) AS jobs_24h
         FROM ai_jobs
        WHERE worker_id IS NOT NULL AND started_at >= datetime('now','+8 hours','-1 day')
        GROUP BY worker_id`
    ).all();
    stats.ai_worker = { devices: (results || []).length, detail: results || [] };
  } catch (e) { stats.ai_worker = { devices: null, error: String(e) }; }
  try {
    const row = await env.DB.prepare(
      `SELECT MAX(done_at) AS last_done, COUNT(*) AS done_24h
         FROM client_report_requests
        WHERE done_at >= datetime('now','+8 hours','-1 day')`
    ).first();
    stats.client_report_tick = { last_done: row ? row.last_done : null, done_24h: row ? row.done_24h : 0 };
  } catch (e) { stats.client_report_tick = { error: String(e) }; }
  try {
    const { results } = await env.DB.prepare(
      `SELECT agent_id, COUNT(*) AS total, COUNT(DISTINCT name) AS people, MAX(downloaded_at) AS last_at
         FROM fleet_downloads GROUP BY agent_id`
    ).all();
    for (const r of (results || [])) {
      stats[r.agent_id] = Object.assign({}, stats[r.agent_id] || {}, {
        downloads: r.total, downloaders: r.people, last_download: r.last_at,
      });
    }
  } catch (e) { /* 表可能還沒建，不擋主流程 */ }
  return stats;
}

// 2026-10-22 F006 修補：領取站不再發任何金鑰值。以前 GET /api/agents/:id/env 會用
// Worker secret 現組 .env 內容回傳——拿到 FLEET_TOKEN 的人就能換到 Cloudflare 金鑰、
// TG bot 金鑰、甚至顧問後台萬用鑰匙（RECRUIT_ADMIN_TOKEN）。現在只回「需要哪些金鑰的
// 名字」，值一律由裝置主人從自己本機 ~/.config/workflow-os/ 帶過去；Worker 上那些
// secret 也已刪除，就算程式被改回去也拿不到。
function envNeeds(agent) {
  return (agent.env || []).map((f) => ({ file: f.file, keys: f.lines.map((l) => l.key) }));
}

const nowTaipei = () => new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 19).replace('T', ' ');

const PAGE = `<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>AI Agent 領取站</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#eef1f6; --surface:#ffffff; --surface2:#f4f6fb;
  --ink:#1b1f2e; --ink2:#565f78; --ink3:#8890a6;
  --line:#dfe4f0; --line2:#eef1f8;
  --accent:#4f6df5; --ok:#1f9d63; --bad:#e0446b;
  --shadow:0 1px 2px rgba(20,24,40,.04), 0 10px 24px -12px rgba(20,24,40,.16);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,"Noto Sans TC",sans-serif;-webkit-font-smoothing:antialiased}
.disp{font-family:"Fraunces",Georgia,"Noto Serif TC",serif}
.mono{font-family:"JetBrains Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.wrap{max-width:1080px;margin:0 auto;padding:0 20px}

/* ── gate ── */
.gate{max-width:380px;margin:18vh auto;background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:30px;box-shadow:var(--shadow)}
.gate b{font-family:"Fraunces",serif;font-size:20px;font-weight:600}
.gate input{width:100%;font:inherit;padding:12px 14px;border:1px solid var(--line);border-radius:12px;margin:14px 0;background:var(--surface2)}
.gate input:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.gate button{width:100%;font:inherit;font-weight:700;background:var(--ink);color:#fff;border:0;border-radius:999px;padding:13px;cursor:pointer}
.gate button:hover{background:#000}
.mut{color:var(--ink3);font-size:13px}

/* ── header / hero ── */
header{padding:44px 0 30px}
header h1{font-family:"Fraunces",serif;font-size:clamp(26px,4vw,34px);font-weight:600;margin:0 0 8px;letter-spacing:-.01em}
header p{margin:0;color:var(--ink2);font-size:14.5px;max-width:64ch;line-height:1.8}
header p code{background:var(--surface2);border:1px solid var(--line);padding:1px 6px;border-radius:5px;font-size:12.5px}

main{padding:0 0 70px}

/* ── roster grid ── */
/* ── group tabs ── */
.tabs{display:flex;gap:6px;margin:22px 0 20px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tab{font:inherit;font-weight:700;font-size:13.5px;color:var(--ink3);background:none;border:0;
  padding:10px 4px;margin-right:18px;cursor:pointer;border-bottom:2px solid transparent;
  display:flex;align-items:center;gap:7px;transition:color .15s ease}
.tab:hover{color:var(--ink2)}
.tab.active{color:var(--ink);border-bottom-color:var(--accent)}
.tab .n{font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:700;background:var(--surface2);
  border:1px solid var(--line);border-radius:999px;padding:1px 7px;color:var(--ink3)}
.tab.active .n{color:var(--accent);border-color:var(--accent)}

.roster{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px;margin-top:8px}
.chip{
  background:var(--surface);border:1px solid var(--line);border-radius:18px;
  padding:20px 16px 16px;text-align:center;cursor:pointer;
  box-shadow:var(--shadow);transition:transform .15s ease, box-shadow .15s ease;
  display:flex;flex-direction:column;align-items:center;gap:10px;
}
.chip:hover{transform:translateY(-3px);box-shadow:0 4px 8px rgba(20,24,40,.06),0 18px 30px -14px rgba(20,24,40,.22)}
.chip:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.chip svg{width:88px;height:88px}
.chip .pname{font-family:"Fraunces",serif;font-weight:600;font-size:17px}
.chip .role{font-size:12px;color:var(--ink3);line-height:1.5;min-height:2.6em}
.chip .badge{font-family:"JetBrains Mono",monospace;font-size:11px;font-weight:700;color:var(--ink2);background:var(--surface2);border:1px solid var(--line);border-radius:999px;padding:2px 10px}

/* ── modal ── */
.overlay{position:fixed;inset:0;background:rgba(20,24,40,.44);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;padding:20px;z-index:50}
.overlay[hidden]{display:none}
.modal{background:var(--surface);border-radius:22px;max-width:520px;width:100%;max-height:88vh;overflow-y:auto;box-shadow:0 30px 60px -20px rgba(10,14,30,.35)}
.modal-head{padding:26px 26px 4px;display:flex;gap:16px;align-items:flex-start}
.modal-head svg{width:64px;height:64px;flex:none}
.modal-head h2{font-family:"Fraunces",serif;font-weight:600;font-size:22px;margin:2px 0 2px}
.modal-head .role{color:var(--ink3);font-size:13px}
.modal-x{margin-left:auto;border:0;background:var(--surface2);width:32px;height:32px;border-radius:50%;cursor:pointer;font-size:16px;color:var(--ink2);line-height:1}
.modal-x:hover{background:var(--line)}
.modal-body{padding:14px 26px 26px}
.modal-body .desc{color:var(--ink2);font-size:14.5px;line-height:1.85;margin:6px 0 18px}
.statgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:20px}
.statbox{background:var(--surface2);border:1px solid var(--line2);border-radius:12px;padding:12px 14px}
.statbox .n{font-family:"JetBrains Mono",monospace;font-weight:700;font-size:20px;line-height:1.2}
.statbox .l{font-size:11.5px;color:var(--ink3);margin-top:2px}
.sec-label{font-size:11.5px;font-weight:700;color:var(--ink3);letter-spacing:.04em;margin:0 0 8px;text-transform:uppercase}
.flow{margin:0 0 20px 11px;padding-left:16px;border-left:2px solid var(--line2)}
.flow .step{position:relative;padding-bottom:16px}
.flow .step:last-child{padding-bottom:0}
.flow .dot{position:absolute;left:-23px;top:1px;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:"JetBrains Mono",monospace;font-size:10.5px;font-weight:700;color:#fff}
.flow .txt{font-size:13px;line-height:1.55;color:var(--ink)}
.filelist{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}
.filelist code{background:var(--surface2);border:1px solid var(--line2);padding:3px 8px;border-radius:6px;font-size:12px;font-family:"JetBrains Mono",monospace}
.row{display:flex;gap:10px;flex-wrap:wrap}
.btn{font:inherit;font-size:13.5px;font-weight:700;border-radius:999px;padding:11px 18px;cursor:pointer;border:1px solid var(--line);background:var(--surface2);color:var(--ink);flex:1;min-width:140px}
.btn.primary{background:var(--ink);color:#fff;border-color:var(--ink)}
.btn.primary:hover{background:#000}
.btn.danger{background:#fdecf0;color:var(--bad);border-color:#f7d3dd}
.btn.danger:hover{background:#fbdde6}
.btn:disabled{opacity:.55;cursor:default}
.namegate{background:var(--surface2);border:1px dashed var(--line);border-radius:12px;padding:14px;margin-bottom:18px}
.namegate label{display:block;font-size:12.5px;color:var(--ink2);margin-bottom:8px}
.namegate .r{display:flex;gap:8px}
.namegate input{flex:1;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:10px}
.namegate button{font:inherit;font-weight:700;background:var(--ink);color:#fff;border:0;border-radius:10px;padding:0 16px;cursor:pointer}
.whoami{font-size:12px;color:var(--ink3);margin:-8px 0 18px}
.whoami button{border:0;background:none;color:var(--accent);cursor:pointer;font:inherit;padding:0;text-decoration:underline}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:var(--ink);color:#fff;padding:10px 18px;border-radius:999px;font-size:13px;box-shadow:var(--shadow);opacity:0;pointer-events:none;transition:opacity .2s}
.toast.on{opacity:1}

@media (max-width:560px){
  .roster{grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px}
  .chip svg{width:64px;height:64px}
  .chip .pname{font-size:15px}
  .modal{border-radius:18px 18px 0 0;max-height:92vh;align-self:flex-end}
  .overlay{align-items:flex-end;padding:0}
  .btn{min-width:0}
}
@media (prefers-reduced-motion:reduce){ *{transition:none!important} }
.shell{display:flex;min-height:100vh}
.sidenav{width:200px;flex:none;background:var(--surface2);border-right:1px solid var(--line);padding:24px 0;display:flex;flex-direction:column;gap:4px}
.sidenav .sn-h{font-size:11px;color:var(--ink3);text-transform:uppercase;letter-spacing:.06em;padding:0 20px;margin:14px 0 6px}
.sidenav .sn-h:first-child{margin-top:0}
.sidenav button{all:unset;box-sizing:border-box;cursor:pointer;padding:10px 20px;font:inherit;font-size:14px;color:var(--ink2);border-left:3px solid transparent}
.sidenav button:hover{background:var(--surface)}
.sidenav button.active{color:var(--ink);font-weight:700;border-left-color:var(--accent);background:var(--surface)}
.content{flex:1;min-width:0}
@media (max-width:720px){
  .shell{flex-direction:column}
  .sidenav{width:100%;flex-direction:row;overflow-x:auto;padding:10px 8px;border-right:0;border-bottom:1px solid var(--line)}
  .sidenav .sn-h{display:none}
  .sidenav button{border-left:0;border-bottom:3px solid transparent;white-space:nowrap}
  .sidenav button.active{border-bottom-color:var(--accent)}
}
</style>

<div class="shell">
  <nav class="sidenav" id="sidenav">
    <div class="sn-h">領取站</div>
    <button type="button" data-section="project" id="sn-project">專案 Agent</button>
    <button type="button" data-section="generic" id="sn-generic">通用工具包</button>
  </nav>
  <div class="content">
    <div id="gate" class="gate">
      <b class="disp" id="gate-title">Step1ne AI Agent 領取站</b>
      <p class="mut" id="gate-desc" style="margin:8px 0 0">需要存取權杖才能進入，這組權杖只用在這個站，跟顧問後台的權杖不是同一組。</p>
      <input id="tok" type="password" placeholder="權杖" autocomplete="off">
      <button id="enter" type="button">進入</button>
      <p id="gerr" class="mut" style="color:var(--bad);margin:12px 0 0" hidden></p>
    </div>

    <div id="app" hidden>
      <header><div class="wrap">
        <h1 id="app-title">AI Agent 小隊</h1>
        <p id="app-desc">每一隻都是一支常駐在某台裝置上做事的 AI，依所屬品牌分頁瀏覽——分頁只是同一個頁面方便切換看，
        <b>各品牌的金鑰完全分開，不會混用</b>。點角色看牠實際在做什麼、要怎麼把牠帶回自己的電腦常駐運作。
        金鑰不從這裡發——每張卡片只列那支 agent 需要哪幾把金鑰的名字，請從已經在跑的電腦 <code>~/.config/workflow-os/</code> 複製過去。</p>
      </div></header>
      <main class="wrap">
        <div class="tabs" id="tabs"></div>
        <div class="roster" id="roster"></div>
      </main>
    </div>
  </div>
</div>

<div class="overlay" id="overlay" hidden>
  <div class="modal" id="modal" role="dialog" aria-modal="true"></div>
</div>
<div class="toast" id="toast"></div>

<script>
var API = location.origin;
var NAMEKEY = 's1ne_fleet_name';
var SECKEY = 's1ne_fleet_section';
var TOK_KEYS = { project: 's1ne_fleet_tok', generic: 's1ne_fleet_tok_generic' };
var SECTION_META = {
  project: {
    title: 'Step1ne AI Agent 領取站', gateDesc: '需要存取權杖才能進入，這組權杖只用在這個站，跟顧問後台的權杖不是同一組。', placeholder: 'FLEET_TOKEN',
    appTitle: 'AI Agent 小隊', appDesc: '每一隻都是一支常駐在某台裝置上做事的 AI，依所屬品牌分頁瀏覽——分頁只是同一個頁面方便切換看，<b>各品牌的金鑰完全分開，不會混用</b>。點角色看牠實際在做什麼、要怎麼把牠帶回自己的電腦常駐運作。金鑰不從這裡發——每張卡片只列那支 agent 需要哪幾把金鑰的名字，請從已經在跑的電腦 <code>~/.config/workflow-os/</code> 複製過去。',
  },
  generic: {
    title: '通用工具包', gateDesc: '這區跟上面的專案 agent 是分開的——不綁任何帳號/資料庫，方法本身可以直接拿去用在你自己的專案。要用另一組權杖，不是專案那組。', placeholder: 'GENERIC_FLEET_TOKEN',
    appTitle: '通用工具包', appDesc: '這幾支刻意不綁死任何人的帳號或資料庫——給的是<b>方法本身</b>，不是「接手這個業務」。下載下來換成你自己的憑證/設定就能用在任何專案。',
  },
};
var SECTION = localStorage.getItem(SECKEY) || 'project';
var tok = localStorage.getItem(TOK_KEYS[SECTION]) || '';
var AGENTS_CACHE = {};
var $ = function (i) { return document.getElementById(i); };

function api(path, opt) {
  opt = opt || {};
  opt.headers = Object.assign({ authorization: 'Bearer ' + tok }, opt.headers || {});
  return fetch(API + path, opt);
}

function toast(msg) {
  var t = $('toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toast._t); toast._t = setTimeout(function () { t.classList.remove('on'); }, 2600);
}

function dl(filename, content) {
  var blob = (content instanceof Blob) ? content : new Blob([content], { type: 'text/plain;charset=utf-8' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
}

/* ── 角色插畫：同一套骨架，換色＋換胸口圖示就是不同 agent ── */
var ICONS = {
  chat:  '<path d="M6 9h12M6 13h8" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>',
  doc:   '<path d="M8 6h8v12H8z" fill="none" stroke="#fff" stroke-width="1.5"/><path d="M9.5 9h5M9.5 12h5M9.5 15h3" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
  check: '<path d="M7 12.5l3 3 7-7" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  phone: '<path d="M8 6.5c0-.6.5-1 1-1h1.3c.4 0 .8.3.9.7l.6 2c.1.4 0 .8-.3 1l-1 .9c.7 1.7 2 3 3.7 3.7l.9-1c.3-.3.7-.4 1-.3l2 .6c.4.1.7.5.7.9V15c0 .6-.4 1-1 1-4.9 0-9-4.1-9-9z" fill="#fff"/>',
  pencil:'<path d="M7 17l.7-2.8L14.6 7.3a1 1 0 011.4 0l1 1a1 1 0 010 1.4L10.1 16.6 7 17z" fill="none" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/>',
  inbox: '<path d="M6 13V8h12v5" fill="none" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/><path d="M6 13l2.2 3h7.6l2.2-3" fill="none" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/><path d="M6 13H9.2a.5.5 0 01.45.28l.5 1a.5.5 0 00.45.28h2.8a.5.5 0 00.45-.28l.5-1a.5.5 0 01.45-.28H18" fill="none" stroke="#fff" stroke-width="1.3"/>',
  id:    '<rect x="6" y="8" width="12" height="8" rx="1.6" fill="none" stroke="#fff" stroke-width="1.4"/><circle cx="10" cy="12" r="1.3" fill="#fff"/><path d="M12.6 11h3M12.6 13h3" stroke="#fff" stroke-width="1.1" stroke-linecap="round"/>',
  send:  '<path d="M7 17l10-9-5 9-1.5-3.5L7 17z" fill="#fff"/>',
  heart: '<path d="M12 17.5s-6-3.7-6-8.1a3.4 3.4 0 016-2.3 3.4 3.4 0 016 2.3c0 4.4-6 8.1-6 8.1z" fill="#fff"/>',
  flag:  '<path d="M8 6.5v11" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="M8 7c1.5-1 3-1 4.5 0s3 1 4.5 0v5c-1.5 1-3 1-4.5 0s-3-1-4.5 0z" fill="#fff"/>',
  search:'<circle cx="11" cy="11" r="4.3" fill="none" stroke="#fff" stroke-width="1.6"/><path d="M14.5 14.5L18 18" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>',
  chart: '<path d="M7 17V11M12 17V7M17 17v-5" stroke="#fff" stroke-width="1.8" stroke-linecap="round"/>',
};
function shade(hex, amt) {
  var n = parseInt(hex.slice(1), 16), r = (n>>16)+amt, g = (n>>8&255)+amt, b=(n&255)+amt;
  r = Math.max(0,Math.min(255,r)); g = Math.max(0,Math.min(255,g)); b = Math.max(0,Math.min(255,b));
  return '#' + (1<<24 | r<<16 | g<<8 | b).toString(16).slice(1);
}
function creature(color, icon, size) {
  size = size || 88;
  var dark = shade(color, -28);
  return '<svg viewBox="0 0 88 88" xmlns="http://www.w3.org/2000/svg" style="width:' + size + 'px;height:' + size + 'px">'
    + '<ellipse cx="44" cy="78" rx="22" ry="4" fill="' + color + '" opacity=".14"/>'
    + '<circle cx="44" cy="40" r="30" fill="' + color + '"/>'
    + '<circle cx="34" cy="37" r="4.2" fill="#fff"/><circle cx="54" cy="37" r="4.2" fill="#fff"/>'
    + '<circle cx="34.8" cy="37.8" r="2" fill="' + dark + '"/><circle cx="54.8" cy="37.8" r="2" fill="' + dark + '"/>'
    + '<path d="M35 49q9 7 18 0" stroke="#fff" stroke-width="2.4" fill="none" stroke-linecap="round"/>'
    + '<circle cx="44" cy="66" r="13" fill="' + dark + '"/>'
    + (ICONS[icon] || '') + '</svg>';
}

function statLine(id, stat) {
  var rows = [];
  if (id === 'ai_worker') rows.push({ n: (stat && stat.devices != null) ? stat.devices : '—', l: '目前上線裝置' });
  if (id === 'client_report_tick') rows.push({ n: (stat && stat.done_24h) || 0, l: '近24h完成份數' });
  rows.push({ n: (stat && stat.downloads) || 0, l: '累計下載次數' });
  rows.push({ n: (stat && stat.downloaders) || 0, l: '不同顧問領過' });
  return rows;
}

function openModal(id) {
  var a = AGENTS_CACHE.agents[id], stat = AGENTS_CACHE.stats[id] || {};
  var files = a.files.map(function (f) { return '<code>' + f.path + '</code>'; }).join('');
  var needs = (a.env_needs || []).map(function (f) { return '<code>' + f.file + '：' + f.keys.join('、') + '</code>'; }).join('');
  var savedName = localStorage.getItem(NAMEKEY) || '';
  var stats = statLine(id, stat).map(function (s) {
    return '<div class="statbox"><div class="n mono">' + s.n + '</div><div class="l">' + s.l + '</div></div>';
  }).join('');

  var flowHtml = (a.workflow || []).map(function (step, i) {
    return '<div class="step"><div class="dot" style="background:' + a.char.color + '">' + (i + 1) + '</div>'
      + '<div class="txt">' + step + '</div></div>';
  }).join('');

  $('modal').innerHTML =
    '<div class="modal-head">' + creature(a.char.color, a.char.icon, 64)
    + '<div><div class="role">' + a.char.persona + '</div><h2>' + a.name + '</h2></div>'
    + '<button class="modal-x" id="mx" aria-label="關閉">✕</button></div>'
    + '<div class="modal-body">'
    + '<p class="desc">' + a.desc + '</p>'
    + (flowHtml ? '<p class="sec-label">運作流程</p><div class="flow">' + flowHtml + '</div>' : '')
    + '<div class="statgrid">' + stats + '</div>'
    + (savedName
        ? '<p class="whoami">以「' + savedName + '」的身分下載 · <button id="chname">不是我，換一個</button></p>'
        : '<div class="namegate"><label>下載前留一下你的名字（只記錄用，方便追蹤是誰在用哪個 agent）</label>'
          + '<div class="r"><input id="nmin" placeholder="例如：Jacky" autocomplete="off"><button id="nmok">確定</button></div></div>')
    + '<p class="sec-label">下載</p>'
    + '<div class="row">'
    + '<button class="btn primary" data-act="scripts">下載腳本</button>'
    + '</div>'
    + (needs ? '<p class="sec-label" style="margin-top:20px">這支需要的金鑰（只列名字，值不從這裡發）</p>'
        + '<div class="filelist">' + needs + '</div>'
        + '<p class="mut" style="margin:0 0 16px">金鑰請從已經在跑的那台電腦 <code>~/.config/workflow-os/</code> 複製同名檔案過去，不要用通訊軟體傳。</p>' : '')
    + '<p class="sec-label" style="margin-top:20px">包含檔案</p>'
    + '<div class="filelist">' + files + '</div>'
    + '</div>';

  function currentName() { return localStorage.getItem(NAMEKEY) || ''; }
  function ensureName(cb) {
    var n = currentName();
    if (n) return cb(n);
    var nmin = $('nmin');
    n = (nmin && nmin.value || '').trim();
    if (!n) { if (nmin) nmin.focus(); toast('先留個名字才能下載'); return; }
    localStorage.setItem(NAMEKEY, n);
    cb(n);
  }
  var nmok = $('nmok');
  if (nmok) nmok.onclick = function () { ensureName(function () { openModal(id); }); };
  var chname = $('chname');
  if (chname) chname.onclick = function () { localStorage.removeItem(NAMEKEY); openModal(id); };

  $('mx').onclick = closeModal;
  $('modal').querySelector('[data-act="scripts"]').onclick = function (e) {
    ensureName(function (name) {
      var btn = e.target; btn.disabled = true; btn.textContent = '下載中⋯';
      api('/api/agents/' + id + '/log', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: name, kind: 'scripts' }) }).catch(function () {});
      Promise.all(a.files.map(function (f) {
        var isZip = f.path.endsWith('.zip');
        return api('/api/agents/' + id + '/files/' + encodeURIComponent(f.path)).then(function (r) {
          return (isZip ? r.blob() : r.text()).then(function (t) { return { path: f.path, text: t }; });
        });
      })).then(function (results) {
        results.forEach(function (r) { dl(r.path.split('/').pop(), r.text); });
        btn.disabled = false; btn.textContent = '下載腳本';
        toast('腳本下載完成');
      }).catch(function () { btn.disabled = false; btn.textContent = '下載失敗，重試'; });
    });
  };
  $('overlay').hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeModal() { $('overlay').hidden = true; document.body.style.overflow = ''; }
$('overlay').addEventListener('click', function (e) { if (e.target === $('overlay')) closeModal(); });
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });

function chip(id, a) {
  var el = document.createElement('button');
  el.type = 'button'; el.className = 'chip';
  el.innerHTML = creature(a.char.color, a.char.icon) + '<div class="pname disp">' + a.char.persona + '</div>'
    + '<div class="role">' + a.name + '</div>'
    + '<span class="badge mono">查看</span>';
  el.onclick = function () { openModal(id); };
  return el;
}

// 品牌互相分開（不共用金鑰／名單），這裡只是同一個頁面用分頁切換瀏覽，
// 不代表底層資料或機密混在一起——每個 agent 實際發的金鑰仍然各自獨立
// （見 AGENTS 清單裡每個 agent 自己的 env 定義）。
var GROUP_LABELS = { step1ne: 'Step1ne', aijob: 'AIJOB', kockol: 'KOC/KOL' };
var GROUP_ORDER = ['step1ne', 'aijob', 'kockol'];
var TABKEY = 's1ne_fleet_tab';
var ACTIVE_GROUP = localStorage.getItem(TABKEY) || 'step1ne';

function renderTabs(agents) {
  var tabs = $('tabs');
  // 通用工具包只有一類，不需要品牌分頁——這裡只在 project 分區顯示分頁列。
  if (SECTION !== 'project') { tabs.innerHTML = ''; tabs.hidden = true; return; }
  tabs.hidden = false;
  var counts = {};
  Object.keys(agents).forEach(function (id) {
    var g = agents[id].group || 'step1ne';
    counts[g] = (counts[g] || 0) + 1;
  });
  var groups = GROUP_ORDER.filter(function (g) { return counts[g]; });
  Object.keys(counts).forEach(function (g) { if (groups.indexOf(g) === -1) groups.push(g); });
  if (groups.indexOf(ACTIVE_GROUP) === -1) ACTIVE_GROUP = groups[0];
  tabs.innerHTML = '';
  groups.forEach(function (g) {
    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'tab' + (g === ACTIVE_GROUP ? ' active' : '');
    btn.innerHTML = (GROUP_LABELS[g] || g) + '<span class="n mono">' + counts[g] + '</span>';
    btn.onclick = function () {
      ACTIVE_GROUP = g; localStorage.setItem(TABKEY, g);
      renderTabs(agents); renderRoster(agents);
    };
    tabs.appendChild(btn);
  });
}

function renderRoster(agents) {
  var roster = $('roster'); roster.innerHTML = '';
  Object.keys(agents).forEach(function (id) {
    if (SECTION === 'project' && (agents[id].group || 'step1ne') !== ACTIVE_GROUP) return;
    roster.appendChild(chip(id, agents[id]));
  });
}

function renderSidenav() {
  Object.keys(TOK_KEYS).forEach(function (sec) {
    var btn = $('sn-' + sec);
    btn.className = sec === SECTION ? 'active' : '';
  });
}

function switchSection(sec) {
  SECTION = sec; localStorage.setItem(SECKEY, sec);
  tok = localStorage.getItem(TOK_KEYS[SECTION]) || '';
  renderSidenav();
  var m = SECTION_META[SECTION];
  $('gate-title').textContent = m.title;
  $('gate-desc').innerHTML = m.gateDesc;
  $('tok').placeholder = m.placeholder;
  $('app-title').textContent = m.appTitle;
  $('app-desc').innerHTML = m.appDesc;
  $('gerr').hidden = true;
  boot();
}

function load() {
  api('/api/agents').then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) { localStorage.removeItem(TOK_KEYS[SECTION]); tok = ''; return boot(); }
    AGENTS_CACHE = d;
    $('gate').hidden = true; $('app').hidden = false;
    renderTabs(d.agents);
    renderRoster(d.agents);
  });
}
function boot() {
  renderSidenav();
  var m = SECTION_META[SECTION];
  $('gate-title').textContent = m.title;
  $('gate-desc').innerHTML = m.gateDesc;
  $('tok').placeholder = m.placeholder;
  $('app-title').textContent = m.appTitle;
  $('app-desc').innerHTML = m.appDesc;
  if (tok) return load();
  $('gate').hidden = false; $('app').hidden = true;
  $('tok').value = '';
  $('enter').onclick = function () {
    var v = $('tok').value.trim();
    if (!v) return;
    var prevTok = tok; tok = v;
    api('/api/agents').then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok || d.scope !== SECTION) {
        tok = prevTok;
        $('gerr').hidden = false;
        $('gerr').textContent = (d.scope && d.scope !== SECTION) ? '這組權杖不是這個分區的，請用「' + SECTION_META[SECTION].placeholder + '」' : '權杖不正確';
        return;
      }
      localStorage.setItem(TOK_KEYS[SECTION], tok); load();
    });
  };
  $('tok').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('enter').click(); });
}
Object.keys(TOK_KEYS).forEach(function (sec) {
  $('sn-' + sec).onclick = function () { switchSection(sec); };
});
boot();
</script>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === 'OPTIONS') return cors('', 204, { 'access-control-allow-headers': 'authorization,content-type' });

    if (p === '/' && request.method === 'GET') {
      return new Response(PAGE, { headers: { 'content-type': 'text/html; charset=utf-8' } });
    }

    if (!p.startsWith('/api/')) return cors('not found', 404);
    const scope = checkToken(request, env);
    if (!scope) return json({ ok: false, error: '權杖不正確' }, 401);
    function agentScope(a) { return a.scope === 'generic' ? 'generic' : 'project'; }

    if (p === '/api/agents' && request.method === 'GET') {
      const stats = scope === 'project' ? await agentStats(env) : {};
      const agents = {};
      for (const id of Object.keys(AGENTS)) {
        const a = AGENTS[id];
        if (agentScope(a) !== scope) continue;
        agents[id] = { name: a.name, desc: a.desc, files: a.files, char: a.char, workflow: a.workflow, group: a.group || 'step1ne', env_needs: envNeeds(a) };
      }
      return json({ ok: true, scope, agents, stats });
    }

    const fileM = /^\/api\/agents\/([a-z_]+)\/files\/(.+)$/.exec(p);
    if (fileM && request.method === 'GET') {
      const [, id, filePath] = fileM;
      const agent = AGENTS[id];
      if (!agent || agentScope(agent) !== scope) return json({ ok: false, error: '找不到這個 agent' }, 404);
      const wanted = decodeURIComponent(filePath);
      if (!agent.files.some((f) => f.path === wanted)) return json({ ok: false, error: '這個 agent 沒有這個檔案' }, 404);
      const obj = await env.FLEET_FILES.get(`${id}/${wanted}`);
      if (!obj) return json({ ok: false, error: '檔案不存在' }, 404);
      // .zip 是二進位，用 text/plain 送出去在瀏覽器端會被當文字重新編碼、壞檔；
      // 其他都是純文字腳本/文件，維持原本的 text/plain 方便直接在瀏覽器預覽。
      const ct = wanted.endsWith('.zip') ? 'application/zip' : 'text/plain; charset=utf-8';
      return new Response(obj.body, { headers: { 'content-type': ct } });
    }

    const envM = /^\/api\/agents\/([a-z_]+)\/env$/.exec(p);
    if (envM && request.method === 'GET') {
      const id = envM[1];
      const agent = AGENTS[id];
      if (!agent || agentScope(agent) !== scope) return json({ ok: false, error: '找不到這個 agent' }, 404);
      console.warn('F006: blocked env download attempt', id, scope);
      return json({ ok: false, error: '金鑰已不從領取站發放，請從你自己電腦的 ~/.config/workflow-os/ 複製對應檔案', env_needs: envNeeds(agent) }, 403);
    }

    const logM = /^\/api\/agents\/([a-z_]+)\/log$/.exec(p);
    if (logM && request.method === 'POST') {
      const id = logM[1];
      if (!AGENTS[id] || agentScope(AGENTS[id]) !== scope) return json({ ok: false, error: '找不到這個 agent' }, 404);
      const b = await request.json().catch(() => ({}));
      const name = String(b.name || '').trim().slice(0, 60);
      const kind = ['scripts', 'env'].includes(b.kind) ? b.kind : null;
      if (!name || !kind) return json({ ok: false, error: '缺 name 或 kind' }, 400);
      await env.DB.prepare(
        `INSERT INTO fleet_downloads (agent_id, name, kind, downloaded_at) VALUES (?,?,?,?)`
      ).bind(id, name, kind, nowTaipei()).run();
      return json({ ok: true });
    }

    return json({ ok: false, error: 'not found' }, 404);
  },
};
