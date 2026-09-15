/**
 * Step1ne 招募表單 API。
 *
 * step1ne.com 是純靜態站（Worker 在另一個 Cloudflare 帳號），
 * 所以表單頁放在那邊、後端放在這邊，用 CORS 串起來。
 *
 * 端點：
 *   POST /apply                 收表單（公開）
 *   POST /checkup               AI 履歷健檢申請表（公開）——阿福，跟阿財完全分開
 *   GET  /admin/checkups        健檢收件清單（需 ADMIN_TOKEN）
 *   GET  /admin/checkup/<id>    單筆健檢：附件、連結、對談紀錄（需 ADMIN_TOKEN）
 *   GET  /checkup-chat/<token>         阿福對談室：讀取狀態與歷史（公開，token 即權限）
 *   POST /checkup-chat/<token>/send    本人送出一句話
 *   GET  /checkup-chat/<token>/poll    取阿福的新訊息（前端輪詢）
 *   GET  /checkup-chat/<token>/report  健檢報告本文（公開，token 即權限，報告產出後才有內容）
 *   GET  /export/applications   給 pm.aijob.com.tw 增量拉資料（需 SYNC_TOKEN）
 *   GET  /export/resume/<id>    取履歷檔（需 SYNC_TOKEN）
 *   GET  /admin/resume/<id>     下載履歷（需 ADMIN_TOKEN，供顧問後台按鈕用）
 *   GET  /admin/list            顧問後台用（需 ADMIN_TOKEN）
 *   GET  /admin/reports         初篩報告清單，可搜尋與篩選（需 ADMIN_TOKEN）
 *   GET  /admin/sessions        所有應徵與面談紀錄（含還沒產出報告的）（需 ADMIN_TOKEN）
 *   POST /admin/interview-done  面談結束後寄信給候選人（本機引擎呼叫，需 ADMIN_TOKEN）
 *   POST /admin/create-candidate 顧問自己找到的人：建檔＋上傳履歷＋取得面談連結（需 ADMIN_TOKEN）
 *   GET  /admin/session/<id>    單筆應徵的完整逐字稿（需 ADMIN_TOKEN）
 *   POST /admin/delete-session  刪除一整筆應徵與其所有紀錄（需 ADMIN_TOKEN）
 *   GET  /admin/report/<id>     單份報告＋逐字稿（需 ADMIN_TOKEN）
 *   POST /admin/decide-report   顧問處置回寫（需 ADMIN_TOKEN）
 *   POST /admin/decide   回寫處置（需 ADMIN_TOKEN）
 *   POST /admin/job-intake      顧問自己新增職缺：收 PDF／文字／圖片／連結（需 ADMIN_TOKEN）
 *   GET  /admin/job-intakes     收件單清單與進度（需 ADMIN_TOKEN）
 *   GET  /chat/<token>          面談室：讀取狀態與歷史（公開，token 即權限）
 *   POST /chat/<token>/send     候選人送出一句話
 *   GET  /chat/<token>/poll     取阿財的新訊息（前端輪詢）
 *   POST /chat/<token>/report   候選人回報問題（公開，token 即權限）
 *   POST /chat/<token>/feedback 面談結束後的體驗評分（公開，token 即權限）
 *   POST /line-webhook    LINE OA「查詢面試進度」（公開，用 x-line-signature 驗證）
 *   GET  /admin/line-bindings   LINE 綁定清單：候選人姓名、手機、綁定時間（需 ADMIN_TOKEN）
 *   GET  /health         公開
 */

// 2026-08-24 加後兩個：招募形式評估工具住在自己的子網域＋自己的 Pages 專案
// （step1ne-enterprise），跟主網域不同源，不加進來瀏覽器會直接把回應擋掉。
// 自訂網域的 DNS 在另一個 Cloudflare 帳號底下，還沒接起來之前 .pages.dev
// 那個位址就是正式入口，兩個都要留著。
const ORIGINS = [
  'https://step1ne.com',
  'https://www.step1ne.com',
  'https://enterprise.step1ne.com',
  'https://step1ne-enterprise.pages.dev',
];

// ── Telegram 主題分工（2026-08-10 跟 Jacky 對齊）──
// 判準是「顧問要不要動手」。原本什麼都往 2855 塞，變成大雜燴，重要的被洗掉。
//
// 🚨 這個一定要放在模組層級。原本它被寫在 fetch() 裡面、而且卡在 /apply 那段路由的
//    區塊內，於是**除了 /apply 以外每一支引用 THREAD.* 的路由都會炸**
//    （ReferenceError: THREAD is not defined），而且是「資料已經寫進 D1、
//    回應卻發不出去」——瀏覽器只看得到 Failed to fetch。
//    2026-08-11 PH 送職缺就是踩到這個。改動這裡之前先想清楚作用域。
const THREAD = {
  decide: 2855,   // 面試通知確認：只放需要人決定／回應的
  intake: 4,      // #3履歷進件：新應徵、預約、開始面談
  pool: 304,      // #4 履歷池：面談報告與 PDF（資料，不是決策）
  system: 1360,   // 系統回報：排程結果、寄送失敗、刪除紀錄
  report: 3161,   // 顧問人選回報區：顧問用人話回報進度，總指揮翻成漏斗狀態
  sourced: 3477,  // 🔍 主動開發人才池：爬蟲／履歷解析撈到的人
                  // 2026-08-20 從 intake 分出來——那個主題是「有人來應徵了」，
                  // 是要有人去回應的事；主動撈到的人還沒被接觸、也沒表達意願，
                  // 混在一起會讓真正要回應的應徵通知被淹掉。
};
// 顧問標記「不合適」時要選的原因。**固定選項，不開放自由輸入**——
// 因為這份資料的用途是「累積起來看出該調什麼」，不是給人抒發。
// 每一個原因對應到一個不同的調整動作，這是它們被這樣切分的理由：
//   地點不行   → 搜尋要加地區限制
//   資歷差太多 → 調分數門檻
//   職類不對   → 改搜尋詞（這條最嚴重，代表整批都白撈）
//   找不到聯絡方式 → 換來源
//   已有工作／沒意願 → 這是正常耗損，不需要調整策略
const REJECT_REASONS = ['地點不行', '資歷差太多', '職類根本不對',
                        '找不到聯絡方式', '已有工作或沒意願', '其他'];
const CHECKUP_THREAD = THREAD.intake;
const INTAKE_THREAD = THREAD.intake;


// ── 寄信 ──
// 候選人拿不到面談室連結就等於流失：關掉分頁、選「稍後提醒」、面談中斷，
// 三種情況都需要一封信把他帶回來。信寄不出去不能讓表單失敗，所以全程吞例外。
const FROM = 'Step1ne 德仁管理顧問 <noreply@step1ne.com>';
const LINE_URL = 'https://lin.ee/XcSWPzM';

// LINE 圖文選單「追蹤面試進度」按鈕固定送出的文字（message-type action）。
// 2026-08-12 加。Jacky 之後要換字，改這裡就好，不用去 LINE 後台跟 Worker 兩邊對。
const LINE_PROGRESS_TRIGGER = '查詢我的面試進度';

// BIM 工程師校園招募加好友連結的預填訊息（2026-09-04 加）。
// 三張海報素材各自帶不同的字尾（A/B/C），讓後台分得出「這個人是掃哪張海報來的」，
// 之後才能比較哪張素材換到比較多人加好友——不是三張都用同一句，那樣就白做了 A/B 測試。
// 連結：https://line.me/R/oaMessage/@930yldgp/?我要應徵BIM工程師A（B、C同理）
// 改字要連 LINE 連結裡的預填文字一起改，三邊都要一致才對得起來。
const LINE_BIM_CAMPUS_TRIGGERS = {
  '🙋我要應徵BIM工程師A': 'campus_bim_v1', // 淺色版
  '🙋我要應徵BIM工程師B': 'campus_bim_v2', // 科技風
  '🙋我要應徵BIM工程師C': 'campus_bim_v3', // 原版風格
};

// 內容定案於 2026-08-14（line_faq_content.md），求職者跟企業窗口分開兩份。
// 存成陣列而不是去讀 line_faq_content.md：Worker 執行環境沒有檔案系統，
// 內容改了就直接改這裡，兩邊本來就要保持一致。
const FAQ_CANDIDATE = [
  { q: '面談的「阿財」是真人嗎？',
    a: '是 AI 面談顧問，這場對談就是正式面談；結束後獵頭顧問會依面談狀況評估，決定是否推薦給用人單位進行面試。' },
  { q: '薪水怎麼算，是不是面議？',
    a: '依職缺頁面列出的範圍為準，面談時會再確認您的條件。建議您先提供期望薪資，方便後續顧問協助您向用人單位爭取。' },
  { q: '阿財面談大概多久、要準備什麼？',
    a: '約 20–40 分鐘，找一個能專心作答的環境即可。這場面談會視為正式面談，獵頭顧問會依面談狀況評估，決定是否將您推薦給用人單位進行面試，請以正式面試的心情看待，謝謝。' },
  { q: '應徵後多久會有回覆？',
    a: '約 1–2 天內會有初步回覆，也可以隨時透過下方圖文選單「追蹤面談進度」查詢。與阿財面談結束後，如果內容沒有問題，會立即送出給用人單位；後續會依用人單位的審核進度持續追蹤，有新進度會通知您。' },
  { q: '沒有相關經驗可以應徵嗎？',
    a: '職缺頁面上會清楚標示是否接受無經驗——如果沒有特別標示「無經驗可」，代表這個職缺需要相關經驗。' },
  { q: '派遣是什麼意思，跟正職差在哪？',
    a: '派遣是由人力派遣公司（Step1ne）與您簽訂勞動契約，再指派您到用人單位的工作現場提供服務；勞健保、薪資由派遣公司處理，工作內容與環境則跟用人單位溝通。是否為派遣，職缺頁面都會清楚標示。' },
  { q: '我要補件或更新履歷，該怎麼做？',
    a: '直接在 LINE 官方帳號傳送新檔案，並告知顧問是要補件還是更新履歷，顧問會協助處理。' },
  { q: '如果沒有錄取，會通知我嗎？',
    a: '會。不論在哪個階段沒有通過，都會收到通知，不會讓您一直等著沒有下文。' },
  { q: '我的資料會外流嗎？',
    a: '您的履歷與面談內容僅提供給您應徵的用人單位評估使用，不會另作他用。' },
];
const FAQ_COMPANY = [
  { q: '委託找人的流程是怎樣？',
    a: '與貴司簽訂委託合約後，提供職缺內容與需求條件即可啟動搜尋與媒合。候選人會先完成工作風格測驗、再由 AI 面談顧問阿財進行正式面談，顧問確認內容沒問題後才會把人選推薦給您——等於先幫您做過一輪面試。' },
  { q: '收費怎麼算？',
    a: '依服務類型（中高階獵才、正職代招、派遣）收費方式不同，詳細費用歡迎聯繫顧問為您說明，謝謝！' },
  { q: '平均多久能安排到候選人？',
    a: '依職缺類型與市場人才供給而定，多數職缺會在委託後 1–2 週內看到第一批人選，實際時間會依討論的搜尋策略調整。' },
  { q: '你們怎麼做初篩？',
    a: '候選人先完成工作風格測驗，再由 AI 面談顧問阿財進行約 20–30 分鐘的正式面談；面談結束後產出報告，由真人顧問評估後才會送到您這邊。' },
  { q: '可以做派遣，還是只能常聘？',
    a: '兩種都可以，會依職缺性質與您的用人需求討論適合的僱用模式。' },
  { q: '保證期內離職怎麼處理？',
    a: '保證期內若候選人離職，會依合約約定提供補聘服務，詳細條款會在委託前跟您說明清楚。' },
  { q: '怎麼開始委託，需要準備什麼資料？',
    a: '與 Step1ne（德仁管理顧問公司）進一步討論需求並簽約後，提供職缺名稱、工作內容、條件與薪資範圍即可開始，顧問會協助您把 JD 補齊到方便對外搜尋的程度。' },
  { q: '雙方的資料保密嗎？',
    a: '候選人與企業的資料都僅用於本次媒合，不會提供給第三方或另作他用。' },
];

// 2026-09-02 加：求職者圖文選單改版，最下面「第一次使用流程」5步驟圖
// (找職缺→阿財自助面試→顧問媒合→企業面試→錄取通知) Jacky 要做成點下去
// 會跳出自動教學訊息，不是純示意圖——跟「常見問題」按鈕同一套 postback
// 手法，不用連到網頁。
const HOWTO_STEPS = [
  ['🔍', '找職缺', '到職缺專區看目前開放的機會，找到想投的就直接應徵'],
  ['🤖', '阿財自助面試', '線上跟 AI 顧問阿財聊一聊，了解你的背景、初步初篩，不用等真人時間'],
  ['🤝', '顧問媒合', '面談內容會給真人顧問看過，覺得適合就會幫你推薦給企業'],
  ['🏢', '企業面試', '企業有興趣的話，會由顧問幫你安排面試時間、對接細節'],
  ['✅', '錄取通知', '確定錄取後，顧問會協助你跟企業對齊到職日與相關安排'],
];
function howtoFlex() {
  return { type: 'flex', altText: '第一次使用 Step1ne？5 步驟帶你看', contents: { type: 'bubble',
    body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'md',
      contents: [
        { type: 'text', text: '第一次使用 Step1ne？', weight: 'bold', size: 'md' },
        { type: 'text', text: '從找職缺到錄取，大概是這樣的流程：', size: 'sm', color: '#8993a8', margin: 'sm' },
        ...HOWTO_STEPS.map(([ic, title, desc], i) => ({
          type: 'box', layout: 'horizontal', margin: 'md', spacing: 'sm',
          contents: [
            { type: 'text', text: ic, flex: 0, size: 'lg' },
            { type: 'box', layout: 'vertical', flex: 1,
              contents: [
                { type: 'text', text: (i + 1) + '. ' + title, weight: 'bold', size: 'sm', wrap: true },
                { type: 'text', text: desc, size: 'xs', color: '#8993a8', wrap: true, margin: 'xs' },
              ] },
          ],
        })),
      ] },
    footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
      contents: [
        { type: 'button', style: 'primary', color: '#a67c3d', height: 'sm',
          action: { type: 'uri', label: '去找職缺', uri: 'https://step1ne.com/jobs/?utm_source=line&utm_medium=richmenu&utm_campaign=citizen-recruiter-menu&utm_content=howto' } },
      ] } } };
}

function faqRoleFlex() {
  return { type: 'flex', altText: '請問您是求職者還是企業窗口？', contents: { type: 'bubble',
    body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
      contents: [
        { type: 'text', text: '常見問題', weight: 'bold', size: 'md' },
        { type: 'text', text: '請問您是？', size: 'sm', color: '#8993a8', margin: 'md' },
      ] },
    footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
      contents: [
        { type: 'button', style: 'primary', color: '#a67c3d', height: 'sm',
          action: { type: 'postback', label: '求職者', data: 'faq_role:candidate', displayText: '求職者' } },
        { type: 'button', style: 'secondary', height: 'sm',
          action: { type: 'postback', label: '企業窗口', data: 'faq_role:company', displayText: '企業窗口' } },
      ] } } };
}

function faqListFlex(role) {
  const list = role === 'company' ? FAQ_COMPANY : FAQ_CANDIDATE;
  const label = role === 'company' ? '企業窗口常見問題' : '求職者常見問題';
  return { type: 'flex', altText: label, contents: { type: 'bubble',
    body: { type: 'box', layout: 'vertical', paddingAll: '16px',
      contents: [{ type: 'text', text: label, weight: 'bold', size: 'md' }] },
    footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
      contents: list.map((item, i) => ({
        type: 'button', style: 'secondary', height: 'sm',
        action: { type: 'postback', label: item.q.slice(0, 20), data: `faq_q:${role}:${i}`, displayText: item.q },
      })) } } };
}

function faqAnswerMessages(role, idx) {
  const list = role === 'company' ? FAQ_COMPANY : FAQ_CANDIDATE;
  const item = list[Number(idx)];
  if (!item) return [{ type: 'text', text: '不好意思，這題不見了，麻煩重新選一次。' }];
  return [
    { type: 'text', text: `${item.q}\n\n${item.a}` },
    { type: 'flex', altText: '看其他問題', contents: { type: 'bubble',
      body: { type: 'box', layout: 'vertical', paddingAll: '12px',
        contents: [{ type: 'button', style: 'secondary', height: 'sm',
          action: { type: 'postback', label: '⬅️ 看其他問題', data: `faq_role:${role}`, displayText: '看其他問題' } }] } } },
  ];
}


// 履歷存檔：base64 切塊寫進 file_chunks，files 只留 metadata。
//
// 為什麼要切：D1 單值上限約 2MB，而真實的 104 履歷 base64 之後常常 2.6MB 以上。
// 2026-07-30 先試過在瀏覽器用 pdf.js 抽文字避開這個限制，但中文 PDF 的字型
// 對應不完整，抽出來夾了 1756 個空位元組、內容殘缺——本機 pdftotext 抽同一份
// 卻是乾淨的。所以正確做法是把原檔完整存下來，讓本機用 pdftotext 抽。
async function saveResume(env, b, now) {
  if (!b.resume_b64) return null;
  const b64 = String(b.resume_b64);
  const bytes = Math.floor((b64.length * 3) / 4);
  if (bytes > MAX_RESUME_BYTES) return { tooBig: true };

  // 2026-09-03改：新檔案一律存R2，不再切塊塞進D1的file_chunks——
  // 履歷這種幾MB的二進位檔案本來就不該佔SQLite的行空間。
  // 舊資料留在D1不動，fileB64()讀取時會看files.storage決定要去哪拿，
  // 兩種格式並存，不用做資料搬遷。
  const fileId = uid();
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  await env.FILES.put(fileId, bin, {
    httpMetadata: { contentType: b.resume_mime || 'application/pdf' },
  });
  await env.DB.prepare(
    `INSERT INTO files (id, created_at, filename, mime, size, chunks, storage) VALUES (?,?,?,?,?,0,'r2')`
  ).bind(fileId, now, b.resume_name || 'resume', b.resume_mime || 'application/pdf', bytes).run();
  return { fileId, chunks: 0 };
}

// 通用上傳存檔：跟 saveResume 同一套切塊機制，但不限定是履歷。
//
// 為什麼要另開一支：顧問新增職缺時上傳的是 JD 的 PDF 或客戶傳來的截圖，
// 一張收件單可能同時有好幾個檔，而 saveResume 綁死了 b.resume_b64 這個欄位名
// 且一次只存一份。存檔機制（files + file_chunks 切塊）是共用的，包一層就好。
//
// ⚠️ 顧問上傳的原始檔一律保留，不隨職缺被拒絕而刪除——
//    事後要回溯「客戶當初給的到底是什麼」，靠的就是這份底稿。
// 不該出現在對外貼文裡的客戶識別字。跟 social_post_agent.py 的
// client_name_terms() 同一套規則：正式名＋別名＋去後綴的字根，
// 三個字以內的純英文別名不用（帆宣的別名「MIC」會把「MIC 收音」誤判）。
async function clientNameTerms(env) {
  const terms = new Set();
  try {
    const { results } = await env.DB.prepare(
      `SELECT display_name, aliases FROM client_companies`).all();
    for (const r of results || []) {
      const vals = [r.display_name, ...String(r.aliases || '').split('\n')];
      for (let v of vals) {
        v = String(v || '').trim();
        if (v.length < 2) continue;
        terms.add(v);
        const base = v.replace(/(股份有限公司|有限公司|集團|公司|科技|國際開發)$/, '').trim();
        if (base.length >= 2) terms.add(base);
      }
    }
  } catch { return []; }
  return [...terms].filter((t) => !/^[A-Za-z0-9]{1,3}$/.test(t))
                   .sort((a, b) => b.length - a.length);
}
function hitsClientNames(text, terms) {
  const t = String(text || '');
  return terms.filter((x) => {
    if (/^[A-Za-z0-9 .&-]+$/.test(x)) {
      return new RegExp(`(?<![A-Za-z0-9])${x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![A-Za-z0-9])`, 'i').test(t);
    }
    return t.includes(x);
  });
}

async function saveUpload(env, file, now) {
  const b64 = String(file.b64 || '');
  if (!b64) return null;
  const bytes = Math.floor((b64.length * 3) / 4);
  if (bytes > MAX_RESUME_BYTES) return { tooBig: true, name: file.name };

  // 2026-09-03改：同saveResume()，新檔案存R2不進D1切塊。
  const fileId = uid();
  const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  await env.FILES.put(fileId, bin, {
    httpMetadata: { contentType: file.mime || 'application/octet-stream' },
  });
  await env.DB.prepare(
    `INSERT INTO files (id, created_at, filename, mime, size, chunks, storage) VALUES (?,?,?,?,?,0,'r2')`
  ).bind(fileId, now, file.name || 'upload', file.mime || 'application/octet-stream', bytes).run();
  return { fileId, chunks: 0, name: file.name || 'upload' };
}

// 客戶對象 → 自動帶入的欄位。
//
// ⚠️ 這是 jobintake/relation_rules.py 的 JavaScript 複本，兩邊必須一致。
//    改一邊不改另一邊，等於這套法遵規則失效——後台寫進去的值跟
//    本機擬 JD 時用的值會不一樣，而且不會有任何錯誤訊息。
//
// 規格全文：~/.claude/projects/-Users-user-----/memory/client_relation_types.md
const RELATION = {
  signed:   { label: '已簽約客戶',        client_named: 1, ai_disclosure: 'always', brand_mode: 'step1ne' },
  unsigned: { label: '未簽約客戶',        client_named: 0, ai_disclosure: 'never',  brand_mode: 'step1ne' },
  private:  { label: '朋友認識・私人協助', client_named: 1, ai_disclosure: 'never',  brand_mode: 'none' },
};
const SERVICE_LINE = { dispatch: '人力派遣', direct: '正職代招', executive: '中高階獵才' };

/**
 * 寄信。
 *
 * `cta` 可以是兩種形狀：
 *   - 單一動作：{ url, text }
 *   - 多步驟：  [{ title, body, url, text }, ...]
 *
 * 多步驟是 2026-08-10 加的，起因是一位候選人在面談室按了送出、被系統導去
 * 測驗頁，他以為是「阿財沒有回應」就走了。查下來是信裡只給了一顆「開始測驗」
 * 的按鈕，完全沒交代測驗之後面談要怎麼進去——他不知道還有第二步。
 *
 * ⚠️ 每一步都要有自己的按鈕。純網址只是按鈕被信箱擋掉時的備用，
 * 所以壓成灰色小字——把一串網址丟給候選人不算給了行動點。
 */
// attachments（可選，2026-09-02 加）：[{ filename, content }]，content 是
// base64 字串——目前只給人選客製表單用（連結是「上傳填完檔案」的入口，
// 但人選要先拿到「空白表單長什麼樣子」才能填，不附檔案等於叫人選填一份
// 他們沒看過的東西）。Resend 原生支援 attachments 欄位，不用自己組 MIME。
async function sendMail(env, to, subject, lines, cta, attachments) {
  if (!env.RESEND_API_KEY || !to) return false;
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const steps = Array.isArray(cta) ? cta : null;

  // 步驟區塊：Gmail 會拆掉 flex/grid，所以用 table + 行內樣式
  const stepsHtml = steps
    ? `<table role="presentation" width="100%" style="border-collapse:collapse;margin:20px 0 22px;">` +
      steps.map((s, i) => {
        const accent = i === 0 ? '#a67c3d' : '#23262d';
        return (i ? `<tr><td style="height:12px;"></td></tr>` : '') +
          `<tr><td style="background:#faf8f3;border-left:3px solid ${accent};padding:18px;">` +
          `<p style="margin:0 0 8px;font-size:15px;font-weight:700;color:#23262d;">${esc(s.title)}</p>` +
          (s.body ? `<p style="margin:0 0 14px;font-size:14px;color:#3a3d44;">${esc(s.body)}</p>` : '') +
          `<p style="margin:0 0 8px;"><a href="${s.url}" style="display:inline-block;background:${accent};` +
          `color:#ffffff;font-weight:700;font-size:15px;text-decoration:none;padding:13px 28px;` +
          `border-radius:999px;">${esc(s.text)}</a></p>` +
          `<p style="margin:0;font-size:12px;color:#9a9da5;">按鈕打不開再用這個網址：<br>` +
          `<span style="color:#b9bcc4;word-break:break-all;">${s.url}</span></p>` +
          `</td></tr>`;
      }).join('') +
      `</table>`
    : '';
  // 信件的 HTML 要用最保守的寫法：Gmail 會拆掉 flex、grid 與大部分現代 CSS。
  // 所以這裡只用 inline-block、table 與行內樣式。
  const html =
    `<div style="background:#f4f1ea;padding:26px 14px;">` +
    `<div style="font-family:-apple-system,'Noto Sans TC',sans-serif;line-height:1.8;color:#23262d;` +
    `max-width:540px;margin:0 auto;background:#ffffff;border-radius:14px;padding:30px 28px;">` +

    // 抬頭 logo。圖被信箱擋掉時 alt 文字要讀得通，所以寫公司全名。
    `<p style="margin:0 0 22px;"><img src="https://step1ne.com/assets/step1ne-logo.png" ` +
    `alt="Step1ne 德仁管理顧問" width="132" style="height:auto;border:0;display:block;"></p>` +

    lines.map((l) => `<p style="margin:0 0 14px;font-size:15px;">${esc(l)}</p>`).join('') +

    stepsHtml +

    (cta && !steps
      ? `<p style="margin:26px 0;"><a href="${cta.url}" style="display:inline-block;background:#a67c3d;` +
        `color:#ffffff;font-weight:700;font-size:15px;text-decoration:none;padding:14px 30px;` +
        `border-radius:999px;">${esc(cta.text)}</a></p>` +
        `<p style="margin:0 0 14px;font-size:13px;color:#8a8d95;">按鈕打不開的話，複製這個網址：<br>` +
        `<span style="color:#a67c3d;word-break:break-all;">${cta.url}</span></p>`
      : '') +

    // 署名
    `<p style="margin:26px 0 0;font-size:15px;">祝 順心<br>` +
    `<span style="color:#8a8d95;font-size:14px;">Step1ne 德仁管理顧問　招募團隊 敬上</span></p>` +

    `<hr style="border:0;border-top:1px solid #eee7db;margin:24px 0;">` +

    `<p style="margin:0 0 12px;font-size:14px;color:#3a3d44;">` +
    `面談過程有任何問題，或想直接找顧問聊聊，歡迎透過 LINE 與我們聯繫：</p>` +
    // inline-flex 在 Gmail 會被拿掉導致按鈕變純文字，一定要用 inline-block
    `<p style="margin:0 0 22px;"><a href="${LINE_URL}" style="display:inline-block;background:#06c755;` +
    `color:#ffffff;font-weight:700;font-size:14px;text-decoration:none;padding:12px 26px;` +
    `border-radius:999px;">加入 LINE 官方帳號</a></p>` +

    `<p style="margin:0;font-size:12px;color:#9a9da5;line-height:1.9;">` +
    `<b style="color:#6b6e77;">德仁管理顧問有限公司</b>（Step1ne）<br>` +
    `統一編號：85046127<br>` +
    `就業服務許可證：北市就服字第 0363 號<br>` +
    `地址：臺北市內湖區康寧路三段 54 之 7 號 3 樓<br><br>` +
    `這封信由系統自動發送，請勿直接回覆——有問題請走上面的 LINE 留言，會盡快回覆您。` +
    `</p></div></div>`;

  try {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        from: FROM, to: [to], subject,
        text: lines.join('\n') +
          (steps
            ? steps.map((s) => `\n\n【${s.title}】` + (s.body ? `\n${s.body}` : '') +
                               `\n${s.text}：${s.url}`).join('')
            : (cta ? `\n\n${cta.text}：${cta.url}` : '')) +
          `\n\n祝 順心\nStep1ne 德仁管理顧問　招募團隊 敬上` +
          `\n\n────────────────` +
          `\n面談過程有任何問題，或想直接找顧問聊聊，請加 LINE：${LINE_URL}` +
          `\n\n德仁管理顧問有限公司（Step1ne）` +
          `\n統一編號：85046127` +
          `\n就業服務許可證：北市就服字第 0363 號` +
          `\n地址：臺北市內湖區康寧路三段 54 之 7 號 3 樓` +
          `\n（這封信由系統自動發送，請勿直接回覆）`,
        html,
        ...(attachments && attachments.length ? { attachments } : {}),
      }),
    });
    return r.ok;
  } catch (e) {
    return false;   // 寄信失敗不該讓應徵流程掛掉
  }
}

// ── 就業服務法第 5 條紅線 ──
// 2026-08-19 加。真實事故：兩位候選人的結案訊息裡寫著「傾向尋找男性人選」，
// 而 close_reason 不是內部備註——它就是直接送給候選人的訊息本身
// （見 /line 那段：顧問在後台編輯什麼，系統就原封不動送什麼）。
//
// ⚠️ 這裡刻意做成「硬擋」而不是「警告後可繼續」。做成可略過的警告，
//    趕時間的人就是會按過去，那等於沒擋。寧可讓顧問多改一次字，
//    也不要讓歧視性文字送到候選人手上。
//
// 擋的是「送給候選人的文字」，不是內部紀錄——客戶原始要求該記還是要記，
// 記在內部欄位，只是不能出現在對外訊息裡。
const LAW5_TERMS = [
  '男性', '女性', '男生', '女生', '限男', '限女', '性別',
  '年齡', '歲以下', '歲以上', '太年輕', '年紀',
  '已婚', '未婚', '婚姻', '懷孕', '生育', '小孩',
  '國籍', '外籍', '本國籍', '原住民', '族群',
  '身心障礙', '殘障', '身障', '宗教', '政黨', '容貌', '長相', '星座', '血型',
];
// ── 佔位符（草稿沒填完就發出去）──
// 2026-08-20 加。真實事故：VIP 接待那則貼文以「🔥【徵】［案件亮點待補］」發出去，
// 而且不是第一次——回頭掃描發現直播主(8/17)、財會派遣(8/18)、VIP 接待(8/19)
// 三則都帶著「待補」字樣公開發布了。
//
// 為什麼審核沒擋住：草稿是三十行的長文，審核的人看的是「內容對不對」，
// 中間夾一個方括號很容易滑過去——這種錯誤靠人眼盯不住，要用程式擋。
//
// ⚠️ 只擋明確的佔位符字樣，不擋英文中括號——貼文裡出現 [] 的正常用法不算少，
//    誤擋會讓顧問發不出去、然後開始想辦法繞過檢查，那比不擋更糟。
const PLACEHOLDER_WORDS = ['待補', '待確認', '待填', '待補充', '請補', 'TBD', 'XXX', 'xxx'];
function placeholderHits(text) {
  const t = String(text || '');
  return PLACEHOLDER_WORDS.filter((w) => t.includes(w));
}

function law5Hits(text) {
  const t = String(text || '');
  return LAW5_TERMS.filter((w) => t.includes(w));
}

const cors = (req) => {
  const o = req.headers.get('origin') || '';
  return {
    'access-control-allow-origin': ORIGINS.includes(o) ? o : ORIGINS[0],
    // PUT／DELETE 是 2026-08-24 為評估工具的 /assessment/cases/:id 加的。
    // 純粹放行動詞，既有路由一個都沒改。
    'access-control-allow-methods': 'GET,POST,PUT,DELETE,OPTIONS',
    'access-control-allow-headers': 'content-type,authorization',
  };
};
const json = (req, body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...cors(req) },
  });

// 固定時間比較。逐字元比較會因為提早 return 洩漏長度與相符位置。
function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

const uid = () => crypto.randomUUID();
const nowTaipei = () =>
  new Date(Date.now() + 8 * 3600e3).toISOString().replace('T', ' ').slice(0, 19);

// LINE datetimepicker 的 initial/min——2026-08-13 加。原本寫死 '2026-01-01'，
// 候選人打開選擇器會停在一個莫名其妙的過去日期，體驗很差。改成停在「今天」，
// min 也用今天，選不到過去的日期。
const candidatePickerInitial = () => `${nowTaipei().slice(0, 10)}T09:00`;
const candidatePickerMin = () => `${nowTaipei().slice(0, 10)}T00:00`;

// 「都不方便」多選時間流程把挑好的時間存進 postback data 裡帶著走（不開表存暫存狀態）。
// datetime 值本身含冒號會被 data.split(':') 切壞，存的時候拿掉冒號，這裡再插回去。
// enc 格式：'YYYY-MM-DDTHHMM' → 'YYYY-MM-DD HH:MM'
function decodeDT(enc) {
  const [date, hhmm] = String(enc || '').split('T');
  if (!hhmm || hhmm.length < 4) return enc || '';
  return `${date}T${hhmm.slice(0, 2)}:${hhmm.slice(2, 4)}`;
}

// 履歷存 D1 的上限。
//
// 2026-07-30 實測 D1 單值的天花板：原始檔 1.4MB（base64 1.82MB）寫得進去，
// 1.8MB（base64 2.34MB）就 500——D1 單值上限約 2MB，而 base64 會膨脹 33%。
// 原本設 600KB 太保守，真實的 104 履歷常常 1MB 以上，顧問會直接被擋下來。
//
// 超過就要求改貼雲端連結（那條路的上限是 8MB，見 parse_resumes.py），
// 而不是靜默截斷——截斷的履歷比沒有履歷更糟，因為沒人會發現。
const MAX_RESUME_BYTES = 8 * 1024 * 1024;
// D1 單值上限約 2MB，所以 base64 要切塊存。900KB 一塊留足夠餘裕。
const CHUNK = 900 * 1024;

// Telegram 訊息用 parse_mode: 'HTML'，& < > 沒轉義的話候選人姓名裡剛好有這幾個字元就會整則訊息壞掉
function escHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ⚠️ 這是 jobintake/client_guard.py 的 JavaScript 複本，兩邊必須一致。
//    Python 那份在擬稿當下擋；這份在「顧問按下核准寄出」的當下再擋一次。
//    要兩道的原因：客戶名單是活的。擬稿當天可以敲的公司，
//    顧問三天後才按核准，中間可能已經簽約了。
const CLIENT_BLOCK = {
  signed: '已簽約客戶。我們是他的乙方，不會反過來推人選給他當開發案',
  negotiating: '正在談簽約中，這時候寄開發信會讓對方以為我們在亂槍打鳥',
  end_client: '是我們某個客戶的終端客戶。去敲等於跟自己的客戶搶案子',
  blocked: '顧問明確標記不可接觸',
};
const _SUFFIX = /(股份)?有限公司$|公司$|集團$|企業社$|工程行$|科技$|系統$/;

function nameVariants(name, aliases) {
  const out = new Set();
  const list = [name].concat(
    String(aliases || '').split('\n').map((s) => s.trim()).filter(Boolean));
  for (const raw of list) {
    const s = String(raw || '').trim();
    if (!s) continue;
    out.add(s);
    let stem = s;
    for (let i = 0; i < 3; i++) {
      const nxt = stem.replace(_SUFFIX, '').trim();
      if (nxt === stem) break;
      stem = nxt;
      if (stem.length >= 2) out.add(stem);
    }
  }
  // 一個字的主體不比對，會誤殺
  return [...out].filter((v) => v.length >= 2);
}

// 2026-08-26 改：原本讀獨立的 clients 表（陌生開發用的關係黑名單），跟
// portal 客戶帳號（client_companies）是兩本互不相通的名冊——律准科技在
// 兩邊都各存一筆，改一邊不會同步到另一邊。Jacky 確認這其實是同一件事
// （客戶關係狀態），已經把 clients 的 9 筆資料併進 client_companies
// 的 relation／relation_note／via_client／aliases 欄位，這裡改讀同一張表，
// 不再有兩本名冊。
async function guardCompany(env, company) {
  const c = String(company || '').trim();
  if (!c) return null;
  const cv = nameVariants(c, '');
  const { results } = await env.DB.prepare(
    `SELECT display_name AS name, aliases, relation, relation_note AS blocked_reason, via_client
       FROM client_companies WHERE relation IS NOT NULL`).all();
  for (const row of results || []) {
    if (!CLIENT_BLOCK[row.relation]) continue;
    for (const v of nameVariants(row.name, row.aliases)) {
      if (cv.some((x) => x.includes(v) || v.includes(x))) {
        let why = row.blocked_reason || CLIENT_BLOCK[row.relation];
        if (row.relation === 'end_client' && row.via_client) why += `（透過 ${row.via_client}）`;
        return { company: c, matched: row.name, relation: row.relation, why };
      }
    }
  }
  return null;
}

// 開發信跟給候選人的信不是同一種東西：
// 這是 Jacky 用自己的名義寄給企業窗口的商務信，對方要能直接回信，
// 所以不掛「請勿直接回覆」那套系統頁尾，也不放求職者用的 LINE。
// 檔案是切成 chunk 存的（D1 單筆有長度上限），要拼回來才能當附件。
const PROFILE_FILE_ID = 'step1ne-profile-2026';   // 公司簡介 PDF，每封開發信都附

// 32KB一段轉base64，避免直接String.fromCharCode(...bytes)在履歷這種
// 幾MB大檔案上把call stack撐爆（spread太多參數）。
function bytesToBase64(bytes) {
  const STEP = 0x8000;
  let binary = '';
  for (let i = 0; i < bytes.length; i += STEP) {
    binary += String.fromCharCode(...bytes.subarray(i, i + STEP));
  }
  return btoa(binary);
}

async function fileB64(env, fileId) {
  if (!fileId) return null;
  const f = await env.DB.prepare(
    `SELECT filename, mime, content_b64, chunks, storage FROM files WHERE id = ?`).bind(fileId).first();
  if (!f) return null;

  // 2026-09-03加：storage='r2'的是新檔案，去R2拿；沒有這個標記（NULL/'d1'）
  // 的是搬R2之前的舊資料，繼續走原本D1的路徑，兩種並存不用搬資料。
  if (f.storage === 'r2') {
    const obj = await env.FILES.get(fileId);
    if (!obj) return null;
    const buf = await obj.arrayBuffer();
    return { filename: f.filename || 'attachment.pdf', content: bytesToBase64(new Uint8Array(buf)),
             mime: f.mime || 'application/octet-stream' };
  }

  let b64 = f.content_b64 || '';
  if (!b64 && f.chunks) {
    const { results } = await env.DB.prepare(
      `SELECT b64 FROM file_chunks WHERE file_id = ? ORDER BY idx`).bind(fileId).all();
    b64 = (results || []).map((r) => r.b64).join('');
  }
  if (!b64) return null;
  return { filename: f.filename || 'attachment.pdf', content: b64, mime: f.mime || 'application/octet-stream' };
}

// 開發信跟給候選人的信不是同一種東西：
// 這是 Jacky 用自己的名義寄給企業窗口的商務信，對方要能直接回信，
// 所以不掛「請勿直接回覆」那套系統頁尾，也不放求職者用的 LINE。
//
// ⚠️ 一定要帶兩個附件（2026-08-11 Jacky 定）：匿名履歷 PDF ＋ 公司簡介 PDF。
//    信裡只寫 3-4 條重點精華，完整經歷放在附件——
//    對方要的是「這個人能不能用」，那要看履歷，不是看信裡的形容詞。
async function sendBdMail(env, to, subject, body, cvFileId) {
  if (!env.RESEND_API_KEY || !to) return false;
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const html =
    `<div style="font-family:-apple-system,'Noto Sans TC',sans-serif;font-size:15px;` +
    `line-height:1.9;color:#23262d;max-width:620px;white-space:pre-wrap;">` +
    esc(body) + `</div>`;
  const attachments = [];
  for (const id of [cvFileId, PROFILE_FILE_ID]) {
    const a = await fileB64(env, id);
    if (a) attachments.push(a);
  }
  try {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        from: 'Jacky Chen <official@step1ne.com>',
        to: [to], subject, text: body, html,
        // 回信要能被系統讀到才追蹤得了。
        // ⚠️ 不能用 Cloudflare Email Routing 接管 official@step1ne.com——
        //    step1ne.com 的信箱在 GoDaddy（MX 指向 secureserver.net），
        //    開 Email Routing 會取代 apex 的 MX，整個公司信箱直接收不到信。
        //    改用子網域：BD_REPLY_TO 設成 reply@bd.step1ne.com，
        //    由 bd.step1ne.com 這個子網域的 Email Routing 轉寄進來，主信箱完全不動。
        //    沒設定就退回 official@，行為跟以前一樣。
        reply_to: env.BD_REPLY_TO || 'official@step1ne.com',
        ...(attachments.length ? { attachments } : {}),
      }),
      signal: AbortSignal.timeout(20000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// 用人需求表補件邀請信——寄給企業客戶窗口，跟 sendBdMail 一樣是能直接回信的
// 商務信，不是 sendMail() 那套候選人專用、掛系統頁尾／LINE的格式。
async function sendPortalMail(env, to, contactName, companyName, portalUrl) {
  if (!env.RESEND_API_KEY || !to) return false;
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const greeting = contactName ? `${contactName} 您好，` : '您好，';
  const subject = `【STEP1NE】${companyName}的用人需求表，麻煩協助補齊`;
  const bodyText =
    `${greeting}\n\n` +
    `這是貴司目前開出職缺的用人需求表連結，我們已經先把已知的資訊填上，麻煩協助補齊剩餘欄位，` +
    `這樣候選人媒合與面談安排都能更準確、更有效率。\n\n` +
    `連結：${portalUrl}\n\n` +
    `這個連結會持續有效，之後如果有新職缺或既有內容要調整，都可以直接回到這個連結處理。\n\n` +
    `若有任何問題，歡迎直接回覆這封信，或加 LINE 與我們聯繫：${LINE_URL}`;
  const html =
    `<div style="background:#f4f1ea;padding:26px 14px;">` +
    `<div style="font-family:-apple-system,'Noto Sans TC',sans-serif;line-height:1.9;color:#23262d;` +
    `max-width:540px;margin:0 auto;background:#ffffff;border-radius:14px;padding:30px 28px;">` +
    `<p style="margin:0 0 22px;"><img src="https://step1ne.com/assets/step1ne-logo.png" ` +
    `alt="Step1ne 德仁管理顧問" width="132" style="height:auto;border:0;display:block;"></p>` +
    `<p style="margin:0 0 14px;font-size:15px;">${esc(greeting)}</p>` +
    `<p style="margin:0 0 14px;font-size:15px;">這是貴司目前開出職缺的用人需求表連結，我們已經先把已知的資訊填上，` +
    `麻煩協助補齊剩餘欄位，這樣候選人媒合與面談安排都能更準確、更有效率。</p>` +
    `<p style="margin:26px 0;"><a href="${portalUrl}" style="display:inline-block;background:#a67c3d;` +
    `color:#ffffff;font-weight:700;font-size:15px;text-decoration:none;padding:14px 30px;` +
    `border-radius:999px;">前往用人需求表</a></p>` +
    `<p style="margin:0 0 14px;font-size:13px;color:#8a8d95;">按鈕打不開的話，複製這個網址：<br>` +
    `<span style="color:#a67c3d;word-break:break-all;">${portalUrl}</span></p>` +
    `<p style="margin:0 0 14px;font-size:15px;">這個連結會持續有效，之後如果有新職缺或既有內容要調整，` +
    `都可以直接回到這個連結處理。</p>` +
    `<p style="margin:26px 0 0;font-size:15px;">若有任何問題，歡迎直接回覆這封信，或加 LINE 與我們聯繫：</p>` +
    `<p style="margin:12px 0 0;"><a href="${LINE_URL}" style="display:inline-block;background:#06c755;` +
    `color:#ffffff;font-weight:700;font-size:14px;text-decoration:none;padding:12px 26px;` +
    `border-radius:999px;">加入 LINE 官方帳號</a></p>` +
    `<hr style="border:0;border-top:1px solid #eee7db;margin:24px 0;">` +
    `<p style="margin:0;font-size:12px;color:#9a9da5;line-height:1.9;">` +
    `<b style="color:#6b6e77;">德仁管理顧問有限公司</b>（Step1ne）<br>` +
    `統一編號：85046127<br>就業服務許可證：北市就服字第 0363 號<br>` +
    `地址：臺北市內湖區康寧路三段 54 之 7 號 3 樓</p></div></div>`;
  try {
    const r = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
      body: JSON.stringify({
        from: 'Jacky Chen <official@step1ne.com>',
        to: [to], subject, text: bodyText, html,
        reply_to: env.BD_REPLY_TO || 'official@step1ne.com',
      }),
      signal: AbortSignal.timeout(20000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

async function notify(env, text, extra) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        chat_id: env.TG_CHAT_ID, text, disable_web_page_preview: true,
        // 群組是 forum（有分主題），不帶 thread id 訊息會落到 General。
        // 2026-08-05 起分兩個主題：需要顧問決定的進「面試通知確認」(預設 TG_THREAD_ID)，
        // 純粹告知的進「#3履歷進件」(THREAD.intake)。混在一起的話，
        // 真正要處置的會被日常進件淹掉。
        ...((extra && extra.message_thread_id) ? {}
            : (env.TG_THREAD_ID ? { message_thread_id: Number(env.TG_THREAD_ID) } : {})),
        ...(extra || {}),
      }),
      // ⚠️ 一定要有逾時。2026-08-11 PH 送職缺時看到「Failed to fetch」，
      //    但資料其實已經寫進 D1 了——因為這支是「寫完 DB → 等 Telegram 回來 →
      //    才回應瀏覽器」，Telegram 一慢就把整個連線拖到被砍。
      //    對顧問來說那等於「不知道到底送出去沒有」，然後他會再按一次。
      signal: AbortSignal.timeout(6000),
    });
  } catch {
    // 通知失敗不能影響應徵送出——人已經填完了，資料進 DB 才是重點
  }
}

// 跟 notify() 幾乎一樣，差別是這支會解析 Telegram 回應、回傳 message_id——
// 給需要事後比對「顧問回覆的是哪一則」的呼叫端用（目前是客戶 portal 訊息串）。
// notify()/notifyScreening() 都沒做這件事，唯一有先例的是 social_post_queue
// 那段（用同一種 r.json() 解析法），這支照抄那個寫法，不要自己發明格式。
async function notifyAndCaptureId(env, text, extra) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return null;
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        chat_id: env.TG_CHAT_ID, text, disable_web_page_preview: true,
        ...(extra || {}),
      }),
      signal: AbortSignal.timeout(6000),
    });
    const rd = await r.json().catch(() => ({}));
    return rd.ok ? rd.result.message_id : null;
  } catch {
    return null;
  }
}

// 每家簽約客戶在 Telegram 群組裡有自己專屬的主題（topic），不是全部客戶擠在
// 同一個「面試通知確認」裡——2026-08-31 客戶數還少的時候就先做，之後量大
// 再改不划算。第一次呼叫才真的去 createForumTopic 建，建過就存在
// client_companies.tg_topic_id，之後都發去同一個 topic。
async function getOrCreateClientTopic(env, company) {
  if (company.tg_topic_id) return company.tg_topic_id;
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return null;
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/createForumTopic`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: env.TG_CHAT_ID, name: `🏢 ${company.display_name}` }),
      signal: AbortSignal.timeout(6000),
    });
    const rd = await r.json().catch(() => ({}));
    if (!rd.ok) return null;
    const topicId = rd.result.message_thread_id;
    await env.DB.prepare(`UPDATE client_companies SET tg_topic_id=? WHERE id=?`)
      .bind(topicId, company.id).run();
    return topicId;
  } catch {
    return null;
  }
}

// 2026-09-01 加：電洽新增人選整套 TG bot 對話用的小工具。
// 跟 getOrCreateClientTopic() 是同一個模式，但存進通用的 bot_topics 表
// （key/topic_id），不專屬客戶——這支 topic 是「顧問電洽新增人選」用的，
// 只建一次，之後都固定用同一個。
async function getOrCreateTopic(env, key, name) {
  const row = await env.DB.prepare(`SELECT topic_id FROM bot_topics WHERE key=?`).bind(key).first();
  if (row) return row.topic_id;
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return null;
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/createForumTopic`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: env.TG_CHAT_ID, name }),
      signal: AbortSignal.timeout(6000),
    });
    const rd = await r.json().catch(() => ({}));
    if (!rd.ok) return null;
    const topicId = rd.result.message_thread_id;
    await env.DB.prepare(`INSERT INTO bot_topics (key, topic_id) VALUES (?,?)`).bind(key, topicId).run();
    return topicId;
  } catch {
    return null;
  }
}

// Worker 每次呼叫都是全新的、沒有記憶——「現在對話走到哪一步」存在
// tg_bot_sessions（chat_id+user_id 當 key），每一則新訊息進來都要重新查一次。
async function ncSession(env, chatId, userId) {
  const row = await env.DB.prepare(
    `SELECT step, data FROM tg_bot_sessions WHERE chat_id=? AND user_id=?`
  ).bind(String(chatId), String(userId)).first();
  if (!row) return null;
  let data = {};
  try { data = JSON.parse(row.data || '{}'); } catch {}
  return { step: row.step, data };
}
async function ncSetSession(env, chatId, userId, step, data) {
  const now = nowTaipei();
  await env.DB.prepare(
    `INSERT INTO tg_bot_sessions (chat_id, user_id, step, data, updated_at) VALUES (?,?,?,?,?)
     ON CONFLICT(chat_id, user_id) DO UPDATE SET step=excluded.step, data=excluded.data, updated_at=excluded.updated_at`
  ).bind(String(chatId), String(userId), step, JSON.stringify(data || {}), now).run();
}
async function ncClearSession(env, chatId, userId) {
  await env.DB.prepare(`DELETE FROM tg_bot_sessions WHERE chat_id=? AND user_id=?`)
    .bind(String(chatId), String(userId)).run();
}
async function ncSend(env, chatId, threadId, text, replyMarkup) {
  await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chat_id: chatId, message_thread_id: threadId, text,
      ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
    }),
  }).catch(() => {});
}

// 2026-09-01 加：同名舊紀錄確認過是同一人之後，走這支——呼叫既有的
// /admin/application/call-note（附加不覆蓋，時間戳記自動記錄「現在」），
// 不是 manual-forward（那支是建新卡片，會重複）。
async function ncSubmitMerge(env, sess, chatId, threadId, who) {
  // 2026-09-02 修：原本用 fetch() 打自己這個 Worker 的公開網址（self-fetch），
  // 會被 Cloudflare error 1042 擋掉，回應不是合法 JSON，crd.error 永遠是
  // undefined，顧問只看到「❌ 整合失敗：不知道為什麼」（Phoebe 上傳廖若辰履歷
  // 準備整合進舊紀錄時實測撞到）。改成跟 doManualForward 同一套做法，直接
  // in-process 呼叫 doCallNote()，不再繞出去打自己的網址。
  try {
    const r = await doCallNote(env, {
      application_id: sess.data.existing_application_id,
      text: sess.data.transcript || '',
      by: who,
      ...(sess.data.resume_b64
        ? { file_b64: sess.data.resume_b64, file_name: sess.data.resume_name, file_mime: sess.data.resume_mime }
        : {}),
    });
    if (r.body.ok) {
      await ncSend(env, chatId, threadId,
        `✅ 已整合進 ${sess.data.name} 的既有紀錄，這次電洽內容記錄在現在這個時間點，不會蓋掉之前的。正式報告會重新產生（約5分鐘，需本機排程在跑）。`);
    } else {
      await ncSend(env, chatId, threadId, `❌ 整合失敗：${r.body.error || '不知道為什麼'}`);
    }
  } catch (e) {
    await ncSend(env, chatId, threadId, '❌ 連線失敗，麻煩按「🆕 開始新增人選」重新來一次。');
  }
}

// 2026-09-02 加：「電話或 Email 填好了」跟「按了跳過」兩條路徑，接下來都是
// 問要指派給哪位顧問，抽成共用函式，兩邊都呼叫同一份，不用各自維護一份
// consultants 查詢跟按鈕組字。
async function ncAskOwner(env, chatId, threadId, userId, sessData) {
  const { results: consultants } = await env.DB.prepare(
    `SELECT id, display_name FROM consultants WHERE is_active=1 ORDER BY display_name`).all();
  await ncSetSession(env, chatId, userId, 'owner', sessData);
  const ownerRows = (consultants || []).map((c) => ([{ text: c.display_name, callback_data: 'nc_owner:' + c.id }]));
  ownerRows.push([{ text: '未指派', callback_data: 'nc_owner:' }]);
  await ncSend(env, chatId, threadId, '這位人選要指派給哪位顧問？', { inline_keyboard: ownerRows });
}

// 待審核通知：附履歷 + 按鈕，讓顧問在 Telegram 上直接核准/婉拒，不用開網頁後台。
// 回傳送出的 Telegram message_id，之後 callback 要編輯同一則訊息把按鈕拿掉。
async function notifyScreening(env, app, job) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return;

  const lines = [
    `🔎 <b>待審核應徵</b>`,
    `姓名：${escHtml(app.name)}`,
    `職缺：${escHtml(job?.title || app.job_slug)}`,
    app.expected_salary ? `期望待遇：${escHtml(app.expected_salary)}` : null,
    app.available_date ? `可到職：${escHtml(app.available_date)}` : null,
    job?.client_screen_conditions
      ? `\n⚠️ <b>客戶硬性條件（系統不判斷，需你評估）</b>\n${escHtml(job.client_screen_conditions)}`
      : null,
  ].filter(Boolean).join('\n');

  const buttons = {
    inline_keyboard: [[
      { text: '✅ 核准，發面談連結', callback_data: `scr_approve:${app.id}` },
      { text: '❌ 婉拒', callback_data: `scr_decline:${app.id}` },
    ], [
      { text: '🌐 推薦其他職缺（後台操作）', url: 'https://step1ne.com/consultant/reports/' },
    ]],
  };

  try {
    // 有履歷檔就用 sendDocument（附件本身就是說明），沒有就退回純文字訊息
    // 2026-09-03改：原本這裡自己重複一份「查files→查file_chunks」的邏輯，
    // R2上線後這份沒跟著改，新履歷會找不到附件、通知變成純文字沒附履歷，
    // 顧問看不出來——改叫共用的fileB64()，R2/D1兩種來源它都認得。
    let resumeBuf = null, resumeName = null;
    if (app.resume_file_id) {
      const f = await fileB64(env, app.resume_file_id);
      if (f) { resumeBuf = Uint8Array.from(atob(f.content), (c) => c.charCodeAt(0)); resumeName = f.filename || 'resume.pdf'; }
    }

    if (resumeBuf) {
      const form = new FormData();
      form.append('chat_id', env.TG_CHAT_ID);
      if (env.TG_THREAD_ID) form.append('message_thread_id', env.TG_THREAD_ID);
      form.append('caption', lines);
      form.append('parse_mode', 'HTML');
      form.append('reply_markup', JSON.stringify(buttons));
      form.append('document', new Blob([resumeBuf]), resumeName);
      await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendDocument`, { method: 'POST', body: form });
    } else {
      const resumeLine = app.resume_url ? `\n履歷連結：${app.resume_url}` : '\n（無履歷）';
      await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          chat_id: env.TG_CHAT_ID,
          ...(env.TG_THREAD_ID ? { message_thread_id: Number(env.TG_THREAD_ID) } : {}),
          text: lines + resumeLine, parse_mode: 'HTML',
          reply_markup: buttons, disable_web_page_preview: true,
        }),
      });
    }
  } catch (e) {
    // 這則失敗不能擋住應徵流程；退回原本純文字 notify() 至少讓顧問知道有人待審核
    await notify(env, `⚠️ 待審核通知（含履歷/按鈕）寄送失敗：${app.name}　${job?.title || app.job_slug}\n改用網頁後台「待審核」處理。`,
        { message_thread_id: THREAD.system });
  }
}

// ── LINE OA「查詢面試進度」──────────────────────────────────
//
// 2026-08-12 加。候選人在 LINE 官方帳號（全民獵才）按選單裡的「追蹤面試進度」
// 或直接打字送出 LINE_PROGRESS_TRIGGER，系統要能自己回答「你現在到哪一關」，
// 不用每次都靠顧問手動回。
//
// 狀態機（存在 line_bindings，跟阿財面談用的 interview_state 是兩件事）：
//   沒有這個 line_user_id 的紀錄 → 只在收到觸發文字時才開始問手機號碼
//   pending_phone → 下一則訊息當手機號碼比對；比對失敗維持 pending_phone（可以再試，但不主動催）
//   bound         → 之後收到觸發文字直接查目前狀態回覆，不用再問一次

// LINE 簽章驗證：x-line-signature 是 HMAC-SHA256(channel secret, raw body) 的 base64。
// 一定要用 request.text() 拿到的原始字串去算，不能先 JSON.parse 再字串化——
// 字串化之後的空白／欄位順序跟 LINE 原本送來的不會完全一樣，簽章會對不起來。
async function verifyLineSignature(secret, rawBody, signatureB64) {
  if (!secret || !signatureB64) return false;
  try {
    const key = await crypto.subtle.importKey(
      'raw', new TextEncoder().encode(secret),
      { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    const sigBuf = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(rawBody));
    const computed = btoa(String.fromCharCode(...new Uint8Array(sigBuf)));
    return safeEqual(computed, signatureB64);
  } catch {
    return false;
  }
}

// ── 已關閉職缺的候選人回覆 ──
// 2026-09-09 Jacky 定調：不能只講「已關閉」就結束，那等於把人推走。
// 要做三件事：誠實說已結束招募、推薦相關職缺、給一個直接找顧問的入口。
// 相關職缺的挑法：先同一個用人企業（最準，人選看的通常是同一家的職缺群），
// 沒有才退到同一條服務線，再沒有就只給職缺列表。
function jobLocText(raw) {
  if (!raw) return '';
  const t = String(raw).replace(/[[\]'"]/g, '').replace(/,\s*/g, '、').trim();
  return t.length > 18 ? t.slice(0, 18) + '…' : t;
}

async function closedJobMessages(env, job) {
  let rel = [];
  try {
    if (job.company_id) {
      rel = (await env.DB.prepare(
        `SELECT slug, title, locations FROM jobs
          WHERE company_id=? AND status='open' AND slug<>? LIMIT 3`
      ).bind(job.company_id, job.slug).all()).results || [];
    }
    if (!rel.length && job.service_line) {
      rel = (await env.DB.prepare(
        `SELECT slug, title, locations FROM jobs
          WHERE service_line=? AND status='open' AND slug<>? LIMIT 3`
      ).bind(job.service_line, job.slug).all()).results || [];
    }
  } catch (e) { console.error('closedJobMessages 找相關職缺失敗', String(e)); }

  const head = { type: 'text',
    text: `嗨嗨 👋 感謝您的詢問！\n\n「${job.title}」已經結束招募了 🙏` };

  const body = [{ type: 'text', weight: 'bold', size: 'md',
    text: rel.length ? '這幾個職缺可能也適合您' : '目前開放中的職缺' , wrap: true }];
  for (const r of rel) {
    const loc = jobLocText(r.locations);
    body.push({ type: 'separator', margin: 'md' });
    body.push({ type: 'box', layout: 'vertical', margin: 'md', spacing: 'xs', contents: [
      { type: 'text', text: r.title, size: 'sm', weight: 'bold', wrap: true },
      ...(loc ? [{ type: 'text', text: loc, size: 'xs', color: '#8a8a8a', wrap: true }] : []),
      { type: 'button', style: 'link', height: 'sm', action: { type: 'uri', label: '看這個職缺',
        uri: `https://step1ne.com/jobs/${encodeURIComponent(r.slug)}/` } },
    ] });
  }

  const flex = { type: 'flex', altText: `「${job.title}」已結束招募，這裡有其他機會`,
    contents: { type: 'bubble',
      body: { type: 'box', layout: 'vertical', paddingAll: '16px', contents: body },
      footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px', contents: [
        { type: 'button', style: 'secondary', height: 'sm',
          action: { type: 'uri', label: '看全部職缺', uri: 'https://step1ne.com/jobs/' } },
        { type: 'button', style: 'primary', height: 'sm', color: '#1c2f6b',
          action: { type: 'postback', label: '跟顧問聊聊',
                    data: `contact_closed_job:${job.slug}`,
                    displayText: '我想跟顧問聊聊其他機會' } },
      ] } } };
  return [head, flex];
}

async function lineReply(env, replyToken, text) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN || !replyToken) return;
  try {
    const r = await fetch('https://api.line.me/v2/bot/message/reply', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      },
      // LINE 單則文字上限 5000 字，這裡的回覆不可能逼近，不特別處理截斷
      body: JSON.stringify({ replyToken, messages: [{ type: 'text', text }] }),
      signal: AbortSignal.timeout(8000),
    });
    // ⚠️ 2026-08-14 補：原本失敗完全吞掉，連 log 都沒有——候選人回報「沒收到訊息」
    // 時完全查不出是 replyToken 過期、Flex 格式錯，還是真的沒送出。跟
    // linePushMessages 用同一套做法，至少留得下錯誤原因。
    if (!r.ok) console.error('lineReply failed', r.status, await r.text());
  } catch (e) {
    console.error('lineReply threw', String(e));
  }
}

// 2026-08-13 加：進度卡片、時段選擇都要用 Flex Message（卡片式版面），
// 不是純文字——這兩支包成通用的「傳訊息陣列」版本，text 版留著給既有呼叫端相容。
async function linePushMessages(env, lineUserId, messages) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN || !lineUserId) return false;
  try {
    const r = await fetch('https://api.line.me/v2/bot/message/push', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      },
      body: JSON.stringify({ to: lineUserId, messages }),
      signal: AbortSignal.timeout(8000),
    });
    // ⚠️ 暫時加：錄取卡沒送達，原本這裡錯誤被吞掉看不到——先印出失敗原因，
    // 抓到問題就拿掉，不要留在正式版。
    if (!r.ok) console.error('linePushMessages failed', r.status, await r.text());
    return r.ok;
  } catch (e) {
    console.error('linePushMessages threw', String(e));
    return false;
  }
}

async function lineReplyMessages(env, replyToken, messages) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN || !replyToken) return;
  try {
    const r = await fetch('https://api.line.me/v2/bot/message/reply', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      },
      body: JSON.stringify({ replyToken, messages }),
      signal: AbortSignal.timeout(8000),
    });
    // ⚠️ 2026-08-14 補：同 lineReply——之前完全沒 log，Flex 卡片格式錯或
    // replyToken 過期時候選人完全收不到訊息、後台也看不出原因。
    if (!r.ok) console.error('lineReplyMessages failed', r.status, await r.text());
  } catch (e) {
    console.error('lineReplyMessages threw', String(e));
  }
}

// 進度卡片：職缺名稱當標題、進度當內文，底下「查看職缺頁」「聯繫顧問」兩顆按鈕。
// ⚠️ 「查看報告」原本想接候選人自己看得到的面談報告，但目前阿財的報告只給
// 顧問看，沒有候選人版——2026-08-13 跟 Jacky 確認過，先連到他應徵的職缺頁。
function progressFlexBubble(prog, jobSlug) {
  // ⚠️ 2026-08-14 加：結案不續跟一般進度更新用不同顏色區分——橘色代表「案子結束」，
  // 跟平常那些「還在往前走」的藍色進度卡一眼就能分開，不用點開才知道是壞消息。
  // 顧問後台「標記結案不續」彈窗的預覽卡用的是完全一樣的橘色（#a3450f），
  // 兩邊配色改動要一起改，不然預覽會跟真的送出去的卡片對不起來。
  const headBg = prog.closed ? '#a3450f' : '#1c2f6b';
  const tagColor = prog.closed ? '#ffd7b0' : '#8fb0ff';
  // 2026-08-14 改：Jacky 要求候選人卡片不要直接寫「結案通知」，
  // 改成跟一般進度卡一樣的中性說法，不要一眼就被貼上「結案」標籤。
  const tagText = prog.closed ? '📋 面談進度通知' : '📈 進度更新';
  const btnColor = prog.closed ? '#c2560c' : '#2f6fed';
  return {
    type: 'bubble',
    header: {
      type: 'box', layout: 'vertical', backgroundColor: headBg, paddingAll: '16px',
      contents: [
        { type: 'text', text: tagText, color: tagColor, size: 'xs', weight: 'bold' },
        { type: 'text', text: prog.jobTitle, color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
      ],
    },
    body: {
      type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
      contents: [
        { type: 'text', text: `👤 ${prog.name} 您好`, size: 'sm', color: '#8993a8' },
        { type: 'text', text: `🗓 初審面試日期：${prog.interviewDate || '尚未面談'}`, size: 'sm', color: '#8993a8' },
        ...(prog.secondStageDate
          ? [{ type: 'text', text: `🗓 第二階段面試日期：${prog.secondStageDate}`, size: 'sm', color: '#8993a8' }]
          : []),
        { type: 'separator', margin: 'md' },
        { type: 'text', text: prog.message, size: 'md', wrap: true, margin: 'md', color: '#16202e' },
      ],
    },
    footer: {
      type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
      contents: [
        // ⚠️ 2026-08-13 加：初審審閱超過 2 天沒進展時，跟 1-1 主動推播那張卡
        // 用同一個 showNudgeButton 判斷（see pendingReviewMessage），主動查詢
        // 跟被動收到推播看到的按鈕要一致，不是只有推播卡才有這顆按鈕。
        ...(prog.showNudgeButton
          ? [{ type: 'button', style: 'primary', color: btnColor, height: 'sm',
              action: { type: 'postback', label: '🔔 提醒顧問看一下', data: `nudge_consultant:${prog.appId}`, displayText: '請顧問更新一下我的進度' } }]
          : [{ type: 'button', style: 'primary', color: btnColor, height: 'sm',
              action: { type: 'uri', label: '查看職缺頁', uri: `https://step1ne.com/jobs/${jobSlug || ''}/` } }]),
        { type: 'button', style: 'secondary', height: 'sm',
          // 2026-08-13 改：原本是 message 型按鈕，按了只是把文字丟進對話框，
          // 沒有任何自動處理，候選人會覺得「按了沒反應」。改成 postback，
          // 統一由 contact_consultant: 處理——直接通知你 Telegram，也回候選人一句。
          action: { type: 'postback', label: '聯繫顧問', data: `contact_consultant:${prog.appId}`, displayText: '我有問題想請顧問協助' } },
      ],
    },
  };
}

const STAGE_LABEL_APPT = { 1: '第一階段', 2: '第二階段', 3: '第三階段', 4: '第四階段' };

const WEEKDAY_LB = ['日', '一', '二', '三', '四', '五', '六'];
// ⚠️ 2026-08-14 修：純日期字串（'YYYY-MM-DD'）要算星期幾，不能用
// new Date(d+'T00:00:00+08:00').getUTCDay()——那個寫法會先換算成 UTC 時間，
// 凌晨時段跨日就少算一天，算出來的星期幾永遠差一天（真實案例：LINE 卡片上
// 2026-08-16 明明是週日，一直顯示成週六）。直接把年月日拆成整數用
// Date.UTC() 建構，不牽扯任何時區轉換，才不會有這個問題。同一個 bug
// 原本分別複製在 appointmentFlexCarousel／appointmentConfirmedFlexCard／
// notifyOnboardDateFlex 三個地方，收斂成這支共用函式，以後只要改一處。
function weekdayLabel(d) {
  const [y, m, dd] = d.split('-').map(Number);
  return WEEKDAY_LB[new Date(Date.UTC(y, m - 1, dd)).getUTCDay()];
}

// 面談時段選擇：用 Carousel（一張卡一個時段），比純文字連結直接很多——
// 人選在 LINE 對話框裡就能點按鈕選，不用跳出去網頁。按鈕用 postback，
// 不是 uri，這樣選了之後系統能立刻在同一個對話裡回覆「已確認」，體驗更順。
// LINE Carousel 最多 10 張卡，appointment 目前限制顧問最多開 5 個時段，最後再加一張
// 「都不方便」的卡，用不到 10 張的上限。
function appointmentFlexCarousel(appt, token) {
  const slots = safeJsonArray(appt.slots);
  const stageLabel = STAGE_LABEL_APPT[appt.stage] || '第一階段';
  const bubbles = slots.map((s) => {
    const [d, t] = String(s.slot_at).split(' ');
    let dayLabel = d;
    try { dayLabel = `${d}（週${weekdayLabel(d)}）`; } catch {}
    return {
      type: 'bubble', size: 'kilo',
      body: {
        type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
        contents: [
          { type: 'text', text: dayLabel, size: 'sm', color: '#8993a8' },
          { type: 'text', text: t || '', size: 'xxl', weight: 'bold', color: '#16202e' },
          { type: 'text', text: s.format || '', size: 'sm', color: '#2f6fed', margin: 'sm' },
          // 視訊面試才會有連結，選時段前就先讓人選知道是視訊，不是選完才發現
          ...(s.meeting_url
            ? [{ type: 'text', text: '📹 視訊連結（選定後仍會再給一次）', size: 'xs', color: '#8993a8', margin: 'sm', wrap: true }]
            : []),
        ],
      },
      footer: {
        type: 'box', layout: 'vertical', paddingAll: '12px',
        contents: [{
          type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
          action: {
            type: 'postback', label: '選這個時段',
            data: `confirm_appt:${token}:${s.slot_at}`,
            displayText: `我選 ${dayLabel} ${t}`,
          },
        }],
      },
    };
  });

  // 2026-08-13 加：如果排出來的時段都不方便，要讓人選有地方回覆自己方便的時間。
  // ⚠️ 原本用 message 型按鈕預填文字，但 Jacky 實測發現 LINE 的 message action
  // 是「按下去就直接送出」，不是「預填讓你編輯」——候選人根本沒機會打字，
  // 系統就已經收到那句半成品文字並自動回覆了。改用 datetimepicker，
  // 點下去會跳 LINE 原生的日期時間選擇器，選完直接回傳結構化的時間，
  // 比等他自己打字更準、體驗也更好，不用再猜他打的到底是不是一個時間。
  bubbles.push({
    type: 'bubble', size: 'kilo',
    body: {
      type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm', justifyContent: 'center',
      contents: [
        { type: 'text', text: '😣', size: 'xxl', align: 'center' },
        { type: 'text', text: '以上時間都不方便', size: 'sm', weight: 'bold', align: 'center', margin: 'md', wrap: true },
        // 2026-08-13 加：Jacky 要求先講清楚要提供 3 個時間，不要點下去才發現
        // 要選好幾次、不知道要選到什麼時候。
        { type: 'text', text: '請提供 3 個方便的面談時間，方便安排面試', size: 'xs', color: '#8993a8', align: 'center', margin: 'sm', wrap: true },
      ],
    },
    footer: {
      type: 'box', layout: 'vertical', paddingAll: '12px',
      contents: [{
        type: 'button', style: 'secondary', height: 'sm',
        action: {
          type: 'datetimepicker', label: '選第 1 個方便的時間', data: `alt_time_appt:${token}:${stageLabel}:1:`,
          // 2026-08-13 改：原本寫死 initial='2026-01-01'，打開來停在很奇怪的日期。
          // 改成停在「今天」——candidatePickerInitial() 算的是台北時間的今天，
          // min 也用今天，不然選得到過去的日期。
          mode: 'datetime', initial: candidatePickerInitial(), min: candidatePickerMin(), max: '2027-12-31T23:59',
        },
      }],
    },
  });

  return { type: 'carousel', contents: bubbles };
}

// 確認面談時段——網頁版（/appointment/<token>/confirm）跟 LINE 版（postback）
// 共用同一套邏輯，避免兩邊各寫一份、之後改規則漏改一邊。
async function confirmAppointmentByToken(env, token, slotAt) {
  const appt = await env.DB.prepare(
    `SELECT ia.*, a.name, a.job_title, a.job_slug
       FROM interview_appointments ia
       JOIN applications a ON a.id = ia.application_id
      WHERE ia.token = ?`
  ).bind(token).first();
  if (!appt) return { ok: false, error: '找不到這場面談安排', status: 404 };
  if (appt.status === 'confirmed') {
    return { ok: false, error: '這場面談時間已經確認過了', confirmed_slot: appt.confirmed_slot, status: 409 };
  }
  const slots = safeJsonArray(appt.slots);
  const picked = slots.find((s) => s.slot_at === slotAt);
  if (!picked) return { ok: false, error: '這個時段不在選項裡', status: 400 };

  const now = nowTaipei();
  await env.DB.prepare(
    `UPDATE interview_appointments SET status='confirmed', confirmed_slot=?, confirmed_at=? WHERE id=?`
  ).bind(slotAt, now, appt.id).run();

  await notify(env,
    `📅 面談時間已確認\n${appt.name}（${appt.job_title || appt.job_slug}）\n選了：${slotAt}（${picked.format || ''}）`
    + (picked.meeting_url ? `\n📹 視訊連結：${picked.meeting_url}` : ''),
    { message_thread_id: THREAD.decide });

  return { ok: true, appt, picked };
}

// 時段確認後的通知卡——面談對象／地點／視訊連結／顧問備註都在這裡揭露，
// 底下加「確認收到」讓候選人回應（未讀的話面談前一天提醒時會再問一次）。
// 2026-08-13 加，取代原本只回一句純文字的做法。
function appointmentConfirmedFlexCard(appt, picked) {
  const [d, t] = String(picked.slot_at).split(' ');
  let dayLabel = d;
  try { dayLabel = `${d}（週${weekdayLabel(d)}）`; } catch {}
  const stageLabel = STAGE_LABEL_APPT[appt.stage] || '第一階段';

  const rows = [
    { type: 'text', text: `🗓 面試時間：${dayLabel} ${t || ''}`, size: 'sm', color: '#8993a8', wrap: true },
  ];
  if (appt.interviewer) rows.push({ type: 'text', text: `🧑‍💼 面談對象：${appt.interviewer}`, size: 'sm', color: '#8993a8', wrap: true });
  rows.push({ type: 'separator', margin: 'md' });
  if (picked.format === '視訊面談' && picked.meeting_url) {
    rows.push({ type: 'text', text: `📹 視訊連結：${picked.meeting_url}，屆時點連結加入即可`, size: 'sm', color: '#16202e', wrap: true, margin: 'md' });
  } else if (picked.format === '現場面談' && appt.location) {
    rows.push({ type: 'text', text: `📍 面談地點：${appt.location}`, size: 'sm', color: '#16202e', wrap: true, margin: 'md' });
  } else if (picked.format === '電話面談') {
    rows.push({ type: 'text', text: '📞 這是電話面談，屆時用人單位會主動撥打您應徵時留的號碼，請留意接聽', size: 'sm', color: '#16202e', wrap: true, margin: 'md' });
  }
  if (appt.note) {
    rows.push({ type: 'separator', margin: 'md' });
    rows.push({ type: 'text', text: `📝 顧問備註：${appt.note}`, size: 'sm', color: '#4c5568', wrap: true, margin: 'md' });
  }
  rows.push({ type: 'separator', margin: 'md' });
  rows.push({ type: 'text', text: '收到這則面試通知，麻煩按下方按鈕確認 🙏', size: 'xs', color: '#8993a8', wrap: true, margin: 'md' });

  return {
    type: 'bubble',
    header: {
      type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
      contents: [
        { type: 'text', text: `📅 面談已確認・${stageLabel}`, color: '#8fb0ff', size: 'xs', weight: 'bold' },
        { type: 'text', text: appt.job_title || appt.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
      ],
    },
    body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm', contents: rows },
    footer: {
      type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
      contents: [
        { type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
          action: { type: 'postback', label: '✅ 確認收到', data: `ack_appt:${appt.id}`, displayText: '我已確認收到面試通知' } },
        { type: 'button', style: 'secondary', height: 'sm',
          action: { type: 'postback', label: '聯繫顧問', data: `contact_consultant:${appt.application_id}`, displayText: '我有問題想請顧問協助' } },
      ],
    },
  };
}

// 主動推播（跟 lineReply 不同——沒有 replyToken 這種「回應對方訊息」的情境，
// 是系統自己找候選人講話，要用 push 這個端點，而且要能對同一個人一次推多則）。
async function linePush(env, lineUserId, text) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN || !lineUserId) return false;
  try {
    const r = await fetch('https://api.line.me/v2/bot/message/push', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      },
      body: JSON.stringify({ to: lineUserId, messages: [{ type: 'text', text }] }),
      signal: AbortSignal.timeout(8000),
    });
    return r.ok;
  } catch {
    return false;   // 推播失敗不能讓顧問處置報告的主流程掛掉
  }
}

// 2026-08-13 加：顧問處置報告／回報進度時，如果這個人選已經跟 LINE 綁定過
// （line_bindings 表，見昨天做的「查詢面試進度」），主動推一則更新給他，
// 不用等他自己想到要來問。跟 deriveApplicationProgress 共用同一套判斷邏輯，
// 保證他在 LINE 主動收到的訊息，跟他自己來問時看到的說法一致。
async function notifyLineProgress(env, applicationId) {
  if (!applicationId) return;
  try {
    const { results } = await env.DB.prepare(
      `SELECT line_user_id, application_ids FROM line_bindings
        WHERE state = 'bound'`
    ).all();
    const hits = (results || []).filter((b) =>
      safeJsonArray(b.application_ids).includes(applicationId));
    if (!hits.length) return;   // 這個人選沒綁過 LINE，沒地方可以推

    const prog = await deriveApplicationProgress(env, applicationId);
    if (!prog) return;
    // 2026-08-13 從純文字升級成 Flex 卡片——職缺當標題、進度當內文，
    // 底下「查看職缺頁」「聯繫顧問」兩顆按鈕，比純文字更好懂、也能直接互動。
    const flex = {
      type: 'flex',
      altText: `【進度更新】${prog.jobTitle}：${prog.message}`.slice(0, 400),
      contents: progressFlexBubble(prog, prog.jobSlug),
    };
    for (const b of hits) await linePushMessages(env, b.line_user_id, [flex]);
  } catch {
    // 推播是加值功能，出錯不該影響顧問處置報告這個主流程
  }
}

// 2026-08-13 加：錄取這一刻要有情緒價值，不能跟其他階段共用同一句「好消息」帶過。
// Jacky 原話：「要改成恭喜等文案要有情緒價值，真心恭喜他們入群」。
// 另外加「我願意接受／我需要再考慮」按鈕——候選人不管選哪個都要通知顧問，
// 讓顧問能主動接下去談，不是晾著等他自己回訊息。
async function notifyOfferFlex(env, applicationId) {
  if (!applicationId) return;
  try {
    const { results } = await env.DB.prepare(
      `SELECT line_user_id, application_ids FROM line_bindings WHERE state = 'bound'`
    ).all();
    const hits = (results || []).filter((b) => safeJsonArray(b.application_ids).includes(applicationId));
    if (!hits.length) return;

    const app = await env.DB.prepare(
      `SELECT id, name, job_slug, job_title FROM applications WHERE id = ?`
    ).bind(applicationId).first();
    if (!app) return;

    const flex = {
      type: 'flex',
      altText: `恭喜您！${app.job_title || app.job_slug} 已經正式錄取了`,
      contents: {
        type: 'bubble',
        header: { type: 'box', layout: 'vertical', backgroundColor: '#1f8f5f', paddingAll: '16px',
          contents: [
            { type: 'text', text: '🎉🎊 恭喜錄取', color: '#d9f2e6', size: 'xs', weight: 'bold' },
            { type: 'text', text: app.job_title || app.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
          ] },
        body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
          contents: [
            { type: 'text', text: `👤 ${app.name} 您好`, size: 'sm', color: '#8993a8' },
            { type: 'separator', margin: 'md' },
            { type: 'text', size: 'sm', color: '#16202e', wrap: true, margin: 'md',
              text: '恭喜您！🎉 用人單位很喜歡您在面談中展現的經歷與態度，決定正式發出錄取通知！\n\n這是您這段時間努力的成果，也很榮幸能陪您走到這一步。期待您展開新的旅程 🌟' },
          ] },
        footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
          contents: [
            { type: 'box', layout: 'horizontal', spacing: 'sm', contents: [
              { type: 'button', style: 'primary', color: '#1f8f5f', height: 'sm',
                action: { type: 'postback', label: '✅ 願意接受', data: `offer_resp:accepted:${app.id}`, displayText: '我願意接受這個 offer' } },
              { type: 'button', style: 'secondary', height: 'sm',
                action: { type: 'postback', label: '🤔 需要考慮', data: `offer_resp:considering:${app.id}`, displayText: '我需要再考慮一下' } },
            ] },
          ] },
      },
    };
    for (const b of hits) await linePushMessages(env, b.line_user_id, [flex]);
  } catch {
    // 推播是加值功能，出錯不該影響顧問處置報告這個主流程
  }
}

// 2026-08-13 加：報到確認拆兩張卡——這是 Ⓐ，顧問問到企業給的報到日期後發，
// 不是「已經到職」的恭喜卡（那個併進到職關懷第 1 天，等真的到了那天才發，
// 見 candidateCareTick）。這裡單純是「這個日期你 OK 嗎」的確認。
async function notifyOnboardDateFlex(env, applicationId, onboardDate) {
  if (!applicationId || !onboardDate) return;
  try {
    const { results } = await env.DB.prepare(
      `SELECT line_user_id, application_ids FROM line_bindings WHERE state = 'bound'`
    ).all();
    const hits = (results || []).filter((b) => safeJsonArray(b.application_ids).includes(applicationId));
    if (!hits.length) return;

    const app = await env.DB.prepare(
      `SELECT id, name, job_slug, job_title FROM applications WHERE id = ?`
    ).bind(applicationId).first();
    if (!app) return;

    const [d, t] = String(onboardDate).split(' ');
    let dayLabel = d;
    try { dayLabel = `${d}（週${weekdayLabel(d)}）`; } catch {}

    const flex = {
      type: 'flex',
      altText: `企業回覆的報到日期：${dayLabel} ${t || ''}，麻煩確認`,
      contents: {
        type: 'bubble',
        header: { type: 'box', layout: 'vertical', backgroundColor: '#1f8f5f', paddingAll: '16px',
          contents: [
            { type: 'text', text: '📅 報到日期確認', color: '#d9f2e6', size: 'xs', weight: 'bold' },
            { type: 'text', text: app.job_title || app.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
          ] },
        body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
          contents: [
            { type: 'text', text: `👤 ${app.name} 您好`, size: 'sm', color: '#8993a8' },
            { type: 'text', text: `🗓 預計報到日：${dayLabel}${t ? ' ' + t : ''}`, size: 'sm', color: '#8993a8', wrap: true },
            { type: 'separator', margin: 'md' },
            { type: 'text', text: '企業回覆的報到日期如上，麻煩您確認一下方便嗎？', size: 'sm', color: '#16202e', wrap: true, margin: 'md' },
          ] },
        footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
          contents: [
            { type: 'box', layout: 'horizontal', spacing: 'sm', contents: [
              { type: 'button', style: 'primary', color: '#1f8f5f', height: 'sm',
                action: { type: 'postback', label: '✅ 願意接受', data: `onboard_resp:accepted:${app.id}`, displayText: '我確認這個報到日期' } },
              { type: 'button', style: 'secondary', height: 'sm',
                action: { type: 'postback', label: '🤔 需要考慮', data: `onboard_resp:considering:${app.id}`, displayText: '我需要再考慮一下' } },
            ] },
            { type: 'button', style: 'secondary', height: 'sm',
              action: { type: 'postback', label: '❓ 我有其他問題', data: `onboard_resp:question:${app.id}`, displayText: '我對報到有其他問題' } },
          ] },
      },
    };
    for (const b of hits) await linePushMessages(env, b.line_user_id, [flex]);
  } catch {
    // 推播是加值功能，出錯不該影響顧問處置報告這個主流程
  }
}

// 台灣手機號碼正規化：只留數字，886 開頭換成 0 開頭，方便跟 applications.phone
// 裡各種格式（有無 -、有無空格、有無 +886）比對。
function normalizePhone(raw) {
  let d = String(raw || '').replace(/\D/g, '');
  if (!d) return '';
  if (d.startsWith('886')) d = '0' + d.slice(3);
  // 2026-08-13 加：台日兩地職缺常有人選人在日本、留的是日本電話——
  // 呂書帆那筆存的是 070 開頭的境內格式，剛好跟他填的一致所以能綁定成功，
  // 但如果哪次存的是 +81 開頭的國際格式、他打的是境內格式（或反過來），
  // 兩邊就會對不起來。比照台灣 886 的做法，把日本國碼 81 也轉成 0 開頭。
  // 長度限制 ≥12 避免誤判——日本手機是 0 開頭 11 碼，去掉 0 換成 81 國碼後正好 12 碼，
  // 台灣門號不會巧合湊出這個長度＋81 開頭的組合。
  else if (d.startsWith('81') && d.length >= 12) d = '0' + d.slice(2);
  if (d.length === 9 && d[0] !== '0') d = '0' + d;
  return d;
}

function safeJsonArray(s) {
  try {
    const v = JSON.parse(s || '[]');
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

// 2026-08-13 加：後台階段列（consultant/reports）跟候選人 LINE 查進度，本來各算各的一套邏輯。
// 顧問手動指定候選人卡在哪一關時，如果兩邊各自判斷，會兜不起來——例如顧問在後台按了
// 「標記錄取」，但底層 placements 資料還沒真的寫上 OFFER_ACCEPTED，後台跟 LINE 看到的
// 就會不一樣。收斂成這一個函式，兩邊都呼叫它，才能保證看到的階段永遠對齊。
//
// 高水位規則：manual_stage（顧問手動指定）跟自動判斷，取兩者中「較後面」的那個。
// 手動指定只能讓畫面往前跳，不能用來隱藏自動資料已經證明發生過的階段；一旦自動資料
// 追上或超過手動指定的位置，畫面自然由自動接手，不用顧問自己記得清除。
const STAGE_ORDER = [
  { key: 'screening', lb: '阿財初審' },
  { key: 'confirm', lb: '顧問確認' },
  { key: 'stage1', lb: '第一階段' },
  { key: 'stage2', lb: '第二階段' },
  { key: 'stage3', lb: '第三階段' },
  { key: 'stage4', lb: '第四階段' },
  { key: 'offer', lb: '錄取' },
  { key: 'onboard', lb: '報到' },
  { key: 'care', lb: '到職關懷' },
  // 2026-08-18 加：備選——跟以上九個「線性往前推進」的關卡不一樣，這是一個
  // 平行狀態（用人單位優先看別人，這位先保留），不代表比前面哪一關更後面。
  // 刻意不放進 resolveStage() 的 steps 陣列，所以選了它不會讓進度條的圓點
  // 往前跳；只在 deriveApplicationProgress() 裡當成優先顯示的特殊狀態處理，
  // 跟「結案不續」是同一種做法（見下面該區塊的註解）。
  { key: 'backup', lb: '備選' },
];
const STAGE_INDEX = Object.fromEntries(STAGE_ORDER.map((s, i) => [s.key, i]));
// 2026-09-14 修：doManualForward() 一直在引用 MANUAL_STAGE_OPTS 驗證
// b.initial_stage，但這個變數從來沒有在任何地方宣告過——只要顧問手動新增
// 人選時帶了初始階段，就會直接丟 ReferenceError、整支功能炸掉。doManualForward
// 是顧問後台用的內部函式（不是客戶 portal），比照 /admin/set-manual-stage
// 「顧問仍然可以設全部 9 個 key」的規則，直接用完整的 STAGE_INDEX 鍵值。
const MANUAL_STAGE_OPTS = Object.keys(STAGE_INDEX);

// 2026-09-01 加：面試輪數（第一～第四階段哪幾關「存在」）是職缺的屬性，不是
// 候選人的——同一個職缺不管推給哪個人選，關數都一樣。存在 jobs.interview_stage_config
// （JSON 陣列，例如 ["stage1","stage2"]）。沒設定過就預設只有第一階段（Jacky
// 2026-09-01 定案：「預設他們至少只有第一階段面談」）。
// ⚠️ 這是全新欄位，跟舊有的 jobs.interview_rounds（自由文字，例如「共面試2次：
// 先HR後主管」，被 set_job.py／draft_thresholds.py／interview_daemon.py／
// social_post_agent.py／portal 的「面試次數」標籤等好幾支既有程式當文案在讀）
// 是兩個不同欄位，不能共用同一格，否則會撞壞那幾支既有的消費者。
function jobStageKeysFromConfig(raw) {
  let arr;
  try { arr = JSON.parse(raw || 'null'); } catch { arr = null; }
  if (!Array.isArray(arr) || !arr.length) return ['stage1'];
  const cleaned = arr.filter((k) => ['stage1', 'stage2', 'stage3', 'stage4'].includes(k));
  return cleaned.length ? cleaned : ['stage1'];
}
async function getJobStageKeys(env, jobSlug) {
  if (!jobSlug) return ['stage1'];
  const row = await env.DB.prepare(`SELECT interview_stage_config FROM jobs WHERE slug = ?`).bind(jobSlug).first();
  return jobStageKeysFromConfig(row && row.interview_stage_config);
}
// 一次查多個職缺（列表頁用，避免 N 位候選人 N 次查詢）——回傳 { slug: [key,...] }。
async function getJobStageKeysMap(env, jobSlugs) {
  const slugs = [...new Set((jobSlugs || []).filter(Boolean))];
  const map = {};
  if (!slugs.length) return map;
  const placeholders = slugs.map(() => '?').join(',');
  const { results } = await env.DB.prepare(
    `SELECT slug, interview_stage_config FROM jobs WHERE slug IN (${placeholders})`
  ).bind(...slugs).all();
  (results || []).forEach((r) => { map[r.slug] = jobStageKeysFromConfig(r.interview_stage_config); });
  slugs.forEach((s) => { if (!map[s]) map[s] = ['stage1']; });
  return map;
}

// app: applications 那一列（要含 manual_stage/manual_stage_note/manual_stage_by/manual_stage_at）；
// report: 最新一份報告（要有 consultant_decision）；
// appts: interview_appointments 全部列（見 /admin/session、/admin/report 的查詢，不是只取最新一筆）；
// placement: 最新一筆 placements；
// roundLabels: 可選，applications.client_interview_labels 解析後的物件（{stage1:'人資面談',...}）——
// 客戶在 portal 自己選的面談輪次類型，蓋掉預設的「第N階段」空泛標籤。顧問後台跟客戶 portal
// 都吃這裡的輸出，兩邊標籤才會永遠對得上，不用各自維護一份文案。
// jobStageKeys: 可選，這個職缺實際設定的面試關數（見 getJobStageKeys()）。不傳就當
// 只有第一階段（最保守的預設，不會平白多長出關卡）。
function resolveStage(app, report, appts, placement, roundLabels, jobStageKeys) {
  roundLabels = roundLabels || {};
  const allowedStage234 = (jobStageKeys && jobStageKeys.length ? jobStageKeys : ['stage1']);
  const latestByStage = (stg) => (appts || []).filter((x) => x.stage === stg).slice(-1)[0];
  let careLen = 0;
  try { careLen = JSON.parse((placement && placement.candidate_care_log) || '[]').length; } catch { careLen = 0; }
  const stageUp = placement ? String(placement.stage || '').toUpperCase() : '';
  const manualIdx = (app && app.manual_stage && STAGE_INDEX[app.manual_stage] !== undefined)
    ? STAGE_INDEX[app.manual_stage] : -1;

  const steps = [
    { key: 'screening', lb: '阿財初審', ok: !!(app && (app.interview_started_at || report)) },
    // ⚠️ 2026-08-14 修：原本只有「顧問已下決定」才算這關過了——面談已經結束、
    // 報告也生成了，但顧問還沒審閱的這段空窗期，effIdx 停在 screening（index 0），
    // 候選人查進度看到的變成 default 分支「您的初審面談還沒有完成」，
    // 但面談其實早就做完了，訊息完全講反。改成「面談已結束」就算這關到了，
    // 不用等顧問下決定——尹緯正這個真實案例撞到的（面談已結束 2 天，訊息卻叫他
    // 去完成面談）。
    // 2026-09-02 改：Jacky 重新定義「顧問階段」三態：
    //   阿財面談結束、或顧問下「需補問」→ 進行中（維持原狀，不算過關）
    //   顧問下「推薦給客戶」→ 已完成（唯一讓這關真的算過的動作）
    //   顧問下「婉拒／不推薦」→ 不推進（獨立的第三態，不是「進行中」也不是
    //     「已完成」，這關本身不算過——是在下面 confirm.decision 這個原始值
    //     另外標記，讓客戶端 render 三態，這裡的 ok 只負責「有沒有過關」）
    // ⚠️ 2026-09-02 再改：已經正式推薦（consultant_decision='forwarded'）之後，
    // 顧問還有第二個「不推薦」動作——針對「這個人選＋這家客戶」這組合按
    // 「標不推薦（這家客戶這個職缺）」（/admin/pipeline/forward-decision，寫
    // candidate_forwards.advisor_not_recommended_at），不是整份報告重新決定。
    // 這個標記如果有，要蓋過原本的 forwarded，一樣顯示不推進——不然客戶
    // portal 會一直顯示「已完成」，即使顧問後來已經表態不推薦這個配對。
    { key: 'confirm', lb: '顧問確認',
      ok: !!(report && report.consultant_decision === 'forwarded') && !(app && app.advisor_not_recommended_at),
      decision: (app && app.advisor_not_recommended_at) ? 'rejected' : ((report && report.consultant_decision) || null) },
  ];
  const s1 = latestByStage(1);
  steps.push({ key: 'stage1', lb: '第一階段', ok: !!(s1 && s1.status === 'confirmed') });
  const STAGE_LB2 = { 2: '第二階段', 3: '第三階段', 4: '第四階段' };
  [2, 3, 4].forEach((stg) => {
    const a = latestByStage(stg);
    const key = 'stage' + stg;
    // 2026-09-01 改：原本「manualIdx >= STAGE_INDEX[key]」會讓 manual_stage 只要
    // 設到更後面的關（例如顧問把人選標成「錄取」），第二、三、四階段就全部被當成
    // 「已完成」顯示，即使這個職缺根本沒有那些輪次、也沒有真的排過那些面談——
    // 陳其寬案就是這樣被誤判成經歷了四階段面試。改成：這一關要不要出現，看這個
    // 職缺實際有沒有設定到這一關（allowedStage234，來自 jobs.interview_stage_config），
    // 或者手動指定「剛好就是」這一關本身（保留舊資料相容，不會因為這次改動讓
    // 已經手動標到第二/三/四階段的候選人畫面上憑空消失這一格）。
    if (a || allowedStage234.includes(key) || (app && app.manual_stage === key)) {
      steps.push({ key, lb: STAGE_LB2[stg], ok: !!(a && a.status === 'confirmed') });
    }
  });
  steps.push({ key: 'offer', lb: '錄取', ok: !!(placement && ['OFFER', 'OFFER_ACCEPTED', 'HIRED'].includes(stageUp)) });
  steps.push({ key: 'onboard', lb: '報到', ok: !!(placement && placement.onboard_date) });
  steps.push({ key: 'care', lb: '到職關懷', ok: careLen > 0 });

  let autoIdx = -1;
  steps.forEach((s, i) => { if (s.ok) autoIdx = i; });
  const manualStepIdx = steps.findIndex((s) => s.key === (app && app.manual_stage));
  const effIdx = Math.max(autoIdx, manualStepIdx);

  return {
    // decision 只有 confirm 這一關會帶到，其餘關卡是 undefined，不影響既有消費者
    // （不會多一個 key 出來，JSON 序列化時 undefined 欄位本來就會被跳過）。
    steps: steps.map((s, i) => ({ key: s.key, lb: roundLabels[s.key] || s.lb, done: i < effIdx, now: i === effIdx, decision: s.decision })),
    effective_key: effIdx >= 0 ? steps[effIdx].key : null,
    effective_index: effIdx,
    manual_active: manualStepIdx > autoIdx,   // 手動指定目前正在「頂著」畫面，自動資料還沒追上
    manual_stage: (app && app.manual_stage) || null,
    manual_note: (app && app.manual_stage_note) || null,
    manual_by: (app && app.manual_stage_by) || null,
    manual_at: (app && app.manual_stage_at) || null,
  };
}

// 客戶 portal 可以直接用這支通用端點設的階段子集。
// 2026-09-01 改：Jacky 要求「人選進度」表格從「用人單位」欄位往後（面試階段／
// 錄取／報到）都要能讓企業自己在表格上用下拉選單直接操作，不用另外點開人選
// 彈窗——stage1~stage3、offer、onboard 都補進來。add-interview-round（記面談
// 類型、自動累加輪次）繼續保留給彈窗裡想細記「這輪是誰談的」的人用，兩條路
// 殊途同歸都是呼叫 applyManualStage()，不會有兩套進度打架。
// screening／confirm 是 Step1ne 推薦給客戶「之前」的內部流程，客戶看不到意義，
// 繼續不開放；care（到職關懷）是報到後的內部追蹤，也不開放。
// 跟 /admin/set-manual-stage 給顧問用的完整 STAGE_INDEX 不同，顧問仍然可以設
// 全部 9 個 key。
const CLIENT_SETTABLE_STAGES = ['stage1', 'stage2', 'stage3', 'offer', 'onboard', 'backup'];

// 面談輪次類型的顯示名稱——客戶在 portal 每新增一輪面試時選其中一種。
const INTERVIEW_ROUND_TYPE_LABEL = { hr: '人資面談', manager: '用人單位主管面談' };

// 寫入 manual_stage 的共用邏輯——/admin/set-manual-stage（顧問）跟 portal 的
// 客戶回填端點都呼叫這裡，保證兩邊行為（含推播候選人 LINE）不會走鐘。
//
// ⚠️ 2026-08-26 加 company_id：同一位人選可以推給好幾家客戶，每一家的面試
// 進度是各自獨立的（A 家談到第二關、B 家還沒約）。有 company_id 就只更新
// 那一家的 candidate_forwards；沒有的話（顧問在後台總覽操作、或這位人選
// 還沒推給任何人）才落回 applications 那份「整體進度」。
// applications.manual_stage 保留當作候選人自己在 LINE 查進度時看到的版本——
// 候選人不該知道自己同時被推給幾家，看到的是「最靠前的那一關」。
// 2026-08-31 加：/admin/jobs 招募職缺頁的漏斗統計（應徵／初審／客戶面談／
// 錄取／到職／結案）只認 placements 表，但 applyManualStage() 一直以來只寫
// candidate_forwards.manual_stage 跟 applications.manual_stage，從來不碰
// placements——結果是任何靠手動指定階段推進到「錄取」「到職」的人選，
// 完全不會被算進漏斗的錄取/到職數字。這支把兩邊接起來，只在 stage 落在
// 「面試中／錄取／到職」這幾個會影響漏斗的值時才動 placements；
// care／backup／screening／confirm／清除（null）都是這次刻意不處理的狀態
// （分別是到職後續、平行保留、發布前內部關卡、清除動作本身），維持原樣。
//
// placements 沒有 (application_id, client_id) 的唯一鍵，一個 application
// 本來就可能被推給多家客戶、各自一筆 placements——這裡用這兩欄手動查找
// 既有列，找不到才新開一筆，不會誤觸到別家客戶的紀錄。
async function syncPlacementForManualStage(env, { application_id, company_id, stage, onboard_date }) {
  if (!company_id) return;
  let mapped = null;
  if (['stage1', 'stage2', 'stage3', 'stage4'].includes(stage)) mapped = 'INTERVIEWING';
  else if (stage === 'offer' || stage === 'onboard') mapped = 'OFFER';
  else return; // care / backup / screening / confirm / null（清除）—— 不動 placements

  const now = nowTaipei();
  // 2026-09-01 加：報到日期原本一律填「今天」，但用人單位回填報到通常是先講好
  // 「預計幾號到職」，不是今天才報到——改成優先吃呼叫端傳進來的日期，沒帶才退回
  // 今天（顧問後台舊的呼叫方式沒帶這個參數，行為不變）。
  const onboardDate = stage === 'onboard' ? (onboard_date || now.slice(0, 10)) : null;
  let existing = await env.DB.prepare(
    `SELECT id FROM placements WHERE application_id=? AND client_id=? LIMIT 1`
  ).bind(application_id, company_id).first();

  if (!existing) {
    // 2026-08-31 實測發現：不少既有 placements 是舊寫入路徑留下的，client_id
    // 是空的（那時候還沒有「一個人推給多家客戶」這個概念）。這種情況下，
    // 這個 application 只有唯一一筆 placements、只是沒有標客戶——直接幫它
    // 補上 client_id，不要另開一筆，不然同一個人在同一個職缺底下會出現
    // 兩筆看起來像重複送件的紀錄（漏斗數字本身用 DISTINCT 不會重複計數，
    // 但顧問後台跟候選人查詢頁會看到兩筆，造成混淆）。
    const { results: legacy } = await env.DB.prepare(
      `SELECT id, client_id FROM placements WHERE application_id=?`
    ).bind(application_id).all();
    if ((legacy || []).length === 1 && !legacy[0].client_id) {
      await env.DB.prepare(`UPDATE placements SET client_id=? WHERE id=?`)
        .bind(company_id, legacy[0].id).run();
      existing = { id: legacy[0].id };
    }
  }

  if (existing) {
    if (onboardDate) {
      await env.DB.prepare(`UPDATE placements SET stage=?, onboard_date=?, updated_at=? WHERE id=?`)
        .bind(mapped, onboardDate, now, existing.id).run();
    } else {
      await env.DB.prepare(`UPDATE placements SET stage=?, updated_at=? WHERE id=?`)
        .bind(mapped, now, existing.id).run();
    }
    return;
  }

  const app = await env.DB.prepare(`SELECT name, job_slug, job_title FROM applications WHERE id=?`).bind(application_id).first();
  if (!app) return;
  const company = await env.DB.prepare(`SELECT display_name FROM client_companies WHERE id=?`).bind(company_id).first();
  await env.DB.prepare(
    `INSERT INTO placements (application_id, candidate_name, job_slug, job_title, client_name, stage, client_id, onboard_date)
     VALUES (?,?,?,?,?,?,?,?)`
  ).bind(application_id, app.name || '未命名', app.job_slug || null,
         app.job_title || app.job_slug || '未命名職缺',
         (company && company.display_name) || '未命名客戶', mapped, company_id, onboardDate).run();
}

async function applyManualStage(env, { application_id, stage, note, by, company_id, onboard_date }) {
  const now = nowTaipei();
  if (company_id) {
    await env.DB.prepare(
      `UPDATE candidate_forwards SET manual_stage=?, manual_stage_note=?, manual_stage_by=?, manual_stage_at=?
        WHERE application_id=? AND company_id=?`
    ).bind(stage, stage ? (note || null) : null, stage ? (by || null) : null,
           stage ? now : null, application_id, company_id).run();
    await syncPlacementForManualStage(env, { application_id, company_id, stage, onboard_date });
    // 候選人 LINE 看到的是「所有客戶裡最靠前的那一關」——他不需要知道
    // 自己同時在幾家手上，但也不該看到比實際落後的進度。
    const { results: all } = await env.DB.prepare(
      `SELECT manual_stage FROM candidate_forwards WHERE application_id=? AND manual_stage IS NOT NULL`
    ).bind(application_id).all();
    let best = null, bestIdx = -1;
    for (const r of all || []) {
      const i = STAGE_INDEX[r.manual_stage];
      if (i !== undefined && i > bestIdx) { bestIdx = i; best = r.manual_stage; }
    }
    await env.DB.prepare(
      `UPDATE applications SET manual_stage=?, manual_stage_by=?, manual_stage_at=? WHERE id=?`
    ).bind(best, best ? (by || null) : null, best ? now : null, application_id).run();
  } else {
    await env.DB.prepare(
      `UPDATE applications SET manual_stage=?, manual_stage_note=?, manual_stage_by=?, manual_stage_at=? WHERE id=?`
    ).bind(stage, stage ? (note || null) : null, stage ? (by || null) : null,
           stage ? now : null, application_id).run();
  }
  // 手動指定可能讓候選人的進度往前跳（例如提前標成錄取），跟其他會改變進度的
  // 動作（decide-report／mark-offer）一樣，順手推播更新，不用等自動資料追上才通知。
  await notifyLineProgress(env, application_id);
  return { manual_stage: stage, manual_stage_at: stage ? now : null };
}

// 2026-09-02 加：人選客製表單——起點是弘昌 BIM 工程師案要收「用人事資料表」，
// 做成通用機制：職缺底下掛表單範本（job_forms），顧問把人選推薦給某家客戶時
// （不管是走 doManualForward、/admin/decide-report 的「轉給客戶」、還是
// /admin/forward-candidate 的「追加推薦」，三個地方都會建立 candidate_forwards
// 這筆關係），如果這個職缺有掛表單，自動生一筆填寫紀錄＋寄一封帶專屬連結的信
// 給人選。三個呼叫端都在「candidate_forwards 這筆關係真的是新建立的」之後
// （INSERT OR IGNORE 的 changes>0）才呼叫這裡，避免同一家公司按第二次推薦、
// 或系統重試時重複寄信。刻意包一層 try/catch、永遠不拋出——這是錦上添花的
// 通知功能，不能因為寄信失敗就讓「推薦給客戶」這個核心動作跟著失敗。
// onlyFormId（2026-09-02 加）：不傳就照舊「這個職缺掛的表單全部觸發」
// （推薦給客戶那三個入口用）；傳了就只處理這一份——給人選卡片「立即寄送」
// 按鈕用，因為那些人選是這個表單功能上線「之前」就已經推薦過去的舊資料，
// 不會再有新的 candidate_forwards INSERT 事件可以掛，需要一個手動補觸發
// 的入口，但又不想每次補觸發就把這個職缺底下所有表單全部重寄一次。
async function triggerJobForms(env, { applicationId, companyId, jobSlug, by, onlyFormId }) {
  if (!jobSlug) return;
  try {
    const { results: allForms } = await env.DB.prepare(
      `SELECT id, name, template_file_id FROM job_forms WHERE job_slug=?`
    ).bind(jobSlug).all();
    const forms = onlyFormId ? (allForms || []).filter((f) => f.id === onlyFormId) : allForms;
    if (!forms || !forms.length) return;
    const app = await env.DB.prepare(`SELECT name, email FROM applications WHERE id=?`).bind(applicationId).first();
    if (!app) return;
    const company = await env.DB.prepare(`SELECT display_name FROM client_companies WHERE id=?`).bind(companyId).first();
    const job = await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(jobSlug).first();
    const hasRealEmail = !!(app.email && !/@no-email\.step1ne\.local$/i.test(String(app.email).trim()));
    const now = nowTaipei();
    for (const form of forms) {
      // 同一份表單、同一位人選、同一家客戶只會生一筆——重複呼叫（例如同一家
      // 公司被重複觸發）不會疊出好幾筆填寫紀錄跟好幾封信。
      const existing = await env.DB.prepare(
        `SELECT id FROM candidate_form_submissions WHERE job_form_id=? AND application_id=? AND company_id=?`
      ).bind(form.id, applicationId, companyId).first();
      if (existing) continue;
      const token = [...crypto.getRandomValues(new Uint8Array(24))]
        .map((x) => x.toString(16).padStart(2, '0')).join('');
      const subId = uid();
      await env.DB.prepare(
        `INSERT INTO candidate_form_submissions
           (id, job_form_id, application_id, company_id, token, status, sent_at, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      ).bind(subId, form.id, applicationId, companyId, token,
             hasRealEmail ? 'sent' : 'pending', hasRealEmail ? now : null, now).run();
      if (!hasRealEmail) continue; // 沒有真的 email，顧問卡片上會顯示「待補聯絡方式」，之後補了 email 再手動重寄
      const link = `https://step1ne.com/form/?t=${token}`;
      // 2026-09-02 修：Jacky 實測發現信裡沒附空白表單本體——人選只收到一個
      // 「上傳填完檔案」的連結，但根本沒看過表單長什麼樣子，沒辦法填。
      // 有掛範本檔的話當附件一起寄出去，而且要拆成兩個明確步驟講清楚
      // 「先下載填寫、填完再上傳」，不能只丟一顆按鈕讓人選自己猜順序。
      let attachments = null;
      let templateLink = null;
      if (form.template_file_id) {
        const tf = await fileB64(env, form.template_file_id);
        if (tf) { attachments = [{ filename: tf.filename, content: tf.content }]; templateLink = `https://step1ne-recruit-api.aiagentg888.workers.dev/form/template?t=${token}`; }
      }
      const cta = templateLink
        ? [
            { title: '第一步：下載並填寫表單', body: '這封信已經附上「' + form.name + '」，打不開附件的話也可以點下方按鈕下載。', url: templateLink, text: '下載空白表單' },
            { title: '第二步：填寫完成後上傳', body: '表單填好之後，點下方按鈕上傳給我們。', url: link, text: `上傳填完的${form.name}` },
          ]
        : { url: link, text: `上傳填完的${form.name}` };
      await sendMail(env, app.email,
        `請填寫「${form.name}」－ ${job ? job.title : ''}`,
        [
          `${app.name || '您好'}：`,
          `恭喜進入「${company ? company.display_name : '用人單位'}」${job ? job.title : ''}這個職缺的用人單位審核階段，請依下面步驟完成「${form.name}」，請於 2 天內完成，謝謝配合！`,
        ],
        cta,
        attachments
      ).catch(() => {});
    }
  } catch (e) { /* 表單觸發是附加功能，失敗不影響推薦給客戶這個核心動作 */ }
}

// 電洽內容補在既有人選卡片上的核心邏輯——抽成獨立函式讓「/admin/application/
// call-note」這支 HTTP 路由跟 Telegram webhook（ncSubmitMerge，「同名舊紀錄，
// 整合進去」那條路徑）都能直接呼叫。2026-09-02 修：跟 doManualForward 同一個
// 理由——ncSubmitMerge 原本用 fetch() 打自己這個 Worker 的公開網址，被
// Cloudflare error 1042（self-fetch 防迴圈機制）擋掉，導致「整合進舊紀錄」
// 一直失敗、還只顯示看不懂的「❌ 整合失敗：不知道為什麼」（Phoebe 實測撞到）。
// 改成直接 in-process 呼叫這個函式。回傳 { status, body }，跟 doManualForward
// 同一套慣例。
async function doCallNote(env, b) {
  if (!b.application_id) return { status: 400, body: { ok: false, error: '缺 application_id' } };
  const text = String(b.text || '').trim();
  if (!text && !b.file_b64) return { status: 400, body: { ok: false, error: '請至少填文字或附一個檔案' } };
  const app = await env.DB.prepare(`SELECT note FROM applications WHERE id=?`).bind(b.application_id).first();
  if (!app) return { status: 404, body: { ok: false, error: '找不到這位人選' } };
  const now = nowTaipei();
  let fileId = null;
  if (b.file_b64) {
    const saved = await saveUpload(env, { b64: b.file_b64, name: b.file_name, mime: b.file_mime }, now);
    if (saved && saved.tooBig) return { status: 400, body: { ok: false, error: '檔案太大，請壓縮後再上傳' } };
    if (saved) fileId = saved.fileId;
  }
  // 累積進 note 欄位當看得到的歷史（跟 Phase 1 demo 的「時間軸」概念一致），
  // 真正餵給 AI 的是 consultant_call_notes（排程處理完會被清空重填下一輪）。
  const stamp = `[${now} 電洽內容・${b.by || '顧問'}]\n${text || '（附檔，內容由 AI 整理中）'}`;
  const newNote = app.note ? app.note + '\n\n' + stamp : stamp;

  // ⚠️ 2026-09-01 加：Jacky 送出電洽內容後在畫面上完全看不到東西——正式的
  // 初篩報告要等本機排程（~5分鐘，還得本機常駐程式在跑）才會出現，
  // 顧問送出後乾等看不到任何回饋，會以為系統壞了（周丞恩那次真實發生過）。
  // 這裡用 Workers AI（env.AI，秒級回應，不用等本機）先產一份「重點彙整」
  // 存起來，送出當下就有東西可以看；正式報告還是走本機那套完整流程
  // （要交叉比對職缺/履歷），兩條路徑並行，不是取代關係。
  // ⚠️ 2026-09-01 補：上面那句「只有純文字才能做」原本沒做完——顧問單純拖檔案
  // 上傳（沒打字）時 text 是空的，即時彙整整段跳過，畫面上只留「附檔，內容由
  // AI整理中」這句空話，顧問等到天荒地老都不會出現東西（Jacky 這裡實際卡住的
  // 就是這個情境）。用 env.AI.toMarkdown 把附件先轉成文字再餵給同一支彙整，
  // 這樣拖檔案跟打字兩條路都能秒出重點；toMarkdown 認不得的格式或轉換失敗就
  // 放棄即時彙整（不擋主流程），本機排程那份完整報告一樣會照跑。
  let textForAi = text;
  if (!textForAi && b.file_b64) {
    try {
      const bytes = Uint8Array.from(atob(b.file_b64), (c) => c.charCodeAt(0));
      const md = await env.AI.toMarkdown([
        { name: b.file_name || 'upload', blob: new Blob([bytes], { type: b.file_mime || 'application/octet-stream' }) },
      ]);
      const extracted = (md && md[0] && md[0].data) ? String(md[0].data).trim() : '';
      if (extracted) textForAi = extracted;
    } catch (e) {
      textForAi = '';
    }
  }
  const callSummaryMd = await summarizeCallNotes(env, textForAi);

  await env.DB.prepare(
    `UPDATE applications SET note=?, consultant_call_notes=?, call_report_pending=1, call_notes_file_id=?,
            call_summary_md=COALESCE(?, call_summary_md)
       WHERE id=?`
  ).bind(newNote.slice(0, 4000), text || null, fileId, callSummaryMd, b.application_id).run();
  // 2026-09-01 加：同時記一筆進 candidate_notes（type=電洽紀錄），純粹附加，
  // 不影響上面那套既有的 AI 產報告流程——這筆只是給人選卡片的④電洽內容區塊
  // 跟⑥歷史時間軸用，讓每一次電洽都是一筆獨立、可篩選類型的紀錄，不用去解析
  // note 欄位裡累積的長文字。
  await env.DB.prepare(
    `INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by)
     VALUES (?, ?, '電洽紀錄', ?, ?, ?)`
  ).bind(uid(), b.application_id, callSummaryMd || text || '（附檔，內容由 AI 整理中）', now, b.by || null).run();
  return { status: 200, body: { ok: true, report_queued: true, call_summary_md: callSummaryMd } };
}

// 手動新增應徵紀錄的核心邏輯——抽成獨立函式讓「/admin/pipeline/manual-forward」
// 這支 HTTP 路由跟 Telegram webhook 都能直接呼叫。2026-09-01 修：TG bot 原本是
// 用 fetch() 打自己這個 Worker 的公開網址（self-fetch）來呼叫這支端點，結果被
// Cloudflare error 1042（Worker 呼叫自己會被當成防迴圈機制擋下）擋掉，導致
// 「電洽新增人選」功能整個失敗。改成直接 in-process 呼叫這個函式，不再繞出去
// 打自己的網址。回傳 { status, body }，body 的形狀跟原本 json() 回應完全一樣，
// 呼叫端（HTTP 路由或 TG webhook）自己決定怎麼包裝。
async function doManualForward(env, b) {
  const sourceKind = b.source_kind === 'sourced' ? 'sourced'
    : b.source_kind === 'new' ? 'new' : 'application';
  const sourceId = String(b.source_id || '').trim();
  const jobSlug = String(b.job_slug || '').trim();
  if (!jobSlug) return { status: 400, body: { ok: false, error: '缺職缺' } };
  if (sourceKind !== 'new' && !sourceId) return { status: 400, body: { ok: false, error: '缺人選' } };
  if (!b.consent_confirmed) {
    return { status: 400, body: { ok: false, error: '請先確認已經取得本人同意，才能幫他建立應徵紀錄' } };
  }
  if (b.initial_stage && !MANUAL_STAGE_OPTS.includes(b.initial_stage)) {
    return { status: 400, body: { ok: false, error: '初始階段不合法' } };
  }

  const job = await env.DB.prepare(
    `SELECT slug, title, company_id FROM jobs WHERE slug=?`).bind(jobSlug).first();
  if (!job) return { status: 404, body: { ok: false, error: '找不到這個職缺' } };

  let src;
  if (sourceKind === 'new') {
    const newName = String(b.name || '').trim();
    let newEmail = String(b.email || '').trim();
    const newPhone = String(b.phone || '').trim();
    if (!newName) return { status: 400, body: { ok: false, error: '請填姓名' } };
    // 2026-09-02 改：Jacky 要求 Email／電話都改選填——顧問電洽當下可能對方
    // 沒空講、或忘了問，不該卡住整個建檔流程。都沒填的話用一個帶亂數的
    // 佔位 email（跟原本「只有電話沒 email」時的佔位寫法同一套慣例），人選
    // 卡片那邊改用「有沒有真的電話／有沒有非佔位 email」判斷要不要顯示
    // 「記得填寫聯絡方式」提醒，不會因為卡在這裡建不了檔。
    if (!newEmail) {
      newEmail = newPhone
        ? `phone-${newPhone.replace(/\D/g, '')}@no-email.step1ne.local`
        : `no-contact-${uid()}@no-email.step1ne.local`;
    }
    src = { name: newName, email: newEmail, phone: newPhone, resume_file_id: null, resume_url: null };
  } else if (sourceKind === 'sourced') {
    src = await env.DB.prepare(`SELECT * FROM sourced_candidates WHERE id=?`).bind(sourceId).first();
  } else {
    src = await env.DB.prepare(`SELECT * FROM applications WHERE id=?`).bind(sourceId).first();
  }
  if (!src) return { status: 404, body: { ok: false, error: '找不到這個人選' } };
  const email = String(src.email || '').trim();
  const phone = String(src.phone || '').trim();
  // 2026-09-02 改：拿掉「沒有 email 也沒有電話就整個擋下來」的硬性檢查——
  // 這裡的 src 通常是舊有人選庫/主動開發的紀錄，本來就可能還沒補齊聯絡方式，
  // 一樣改成放行、靠人選卡片的提醒讓顧問之後補。
  if (!String(src.name || '').trim()) {
    return { status: 400, body: { ok: false, error: '這個人選沒有姓名，請先在人選卡片補上姓名再建應徵紀錄' } };
  }

  const now = nowTaipei();
  const phoneTail = phone.replace(/\D/g, '').slice(-9);
  let appId = email ? (await env.DB.prepare(
    `SELECT id FROM applications WHERE LOWER(TRIM(email))=? AND job_slug=? LIMIT 1`
  ).bind(email.toLowerCase(), jobSlug).first() || {}).id : null;
  if (!appId && phoneTail) {
    appId = (await env.DB.prepare(
      `SELECT id FROM applications WHERE phone LIKE ? AND job_slug=? LIMIT 1`
    ).bind('%' + phoneTail, jobSlug).first() || {}).id;
  }

  let created = false;
  let reportQueued = false;
  if (!appId) {
    appId = uid();
    created = true;
    const consentNote = `[${now}] 顧問手動新增應徵紀錄：${b.consent_note ? String(b.consent_note).slice(0, 300) : '（未填說明）'}`;
    let resumeFileId = src.resume_file_id || null;
    if ((sourceKind === 'new' || sourceKind === 'sourced') && b.resume_b64) {
      const saved = await saveResume(env, b, now);
      if (saved && saved.tooBig) {
        return { status: 400, body: { ok: false, error: '履歷檔案太大，請壓縮後再上傳' } };
      }
      if (saved) resumeFileId = saved.fileId;
    }
    const callNotes = (sourceKind === 'new' || sourceKind === 'sourced') && b.call_notes ? String(b.call_notes).trim() : '';
    let callNotesFileId = null;
    if ((sourceKind === 'new' || sourceKind === 'sourced') && b.call_notes_file_b64) {
      const saved = await saveUpload(env, {
        b64: b.call_notes_file_b64, name: b.call_notes_file_name, mime: b.call_notes_file_mime,
      }, now);
      if (saved && saved.tooBig) {
        return { status: 400, body: { ok: false, error: '面談內容檔案太大，請壓縮後再上傳' } };
      }
      if (saved) callNotesFileId = saved.fileId;
    }
    reportQueued = !!(callNotes || callNotesFileId);
    const sourceChannel = (sourceKind === 'new' || sourceKind === 'sourced') && b.source_channel ? String(b.source_channel).trim() : null;
    const resumeSource = (sourceKind === 'new' || sourceKind === 'sourced') && b.resume_source ? String(b.resume_source).trim() : null;
    const callSummaryMd = await summarizeCallNotes(env, callNotes);
    await env.DB.prepare(
      `INSERT INTO applications
         (id, created_at, job_slug, job_title, name, email, phone,
          resume_file_id, resume_url, note, status, consent_at,
          interview_state, handled_by, handled_note, owner,
          consultant_call_notes, call_report_pending, call_notes_file_id, source_channel, resume_source, call_summary_md)
       VALUES (?,?,?,?,?,?,?,?,?,?,'new',?,'not_started',?,?,?,?,?,?,?,?,?)`
    ).bind(appId, now, jobSlug, job.title, src.name || null, email || null, phone || null,
           resumeFileId, src.resume_url || null, consentNote, now,
           b.by || null, `顧問手動送出，未透過阿財面談（${b.by || '顧問'}）`, b.owner || null,
           callNotes || null, reportQueued ? 1 : 0, callNotesFileId, sourceChannel, resumeSource, callSummaryMd).run();
    if (callSummaryMd) {
      await env.DB.prepare(
        `INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by)
         VALUES (?, ?, '電洽紀錄', ?, ?, ?)`
      ).bind(uid(), appId, callSummaryMd, now, b.by || null).run();
    }
    if (sourceKind === 'sourced' && !src.converted_application_id) {
      await env.DB.prepare(
        `UPDATE sourced_candidates SET converted_application_id=? WHERE id=?`
      ).bind(appId, sourceId).run();
    }
  }

  if (job.company_id) {
    const cfr = await env.DB.prepare(
      `INSERT OR IGNORE INTO candidate_forwards (id, application_id, company_id, forwarded_by, forwarded_at, note)
       VALUES (?,?,?,?,?,?)`
    ).bind(uid(), appId, job.company_id, b.by || null, now, '顧問手動新增（未透過阿財面談）').run();
    if (cfr.meta && cfr.meta.changes) {
      await triggerJobForms(env, { applicationId: appId, companyId: job.company_id, jobSlug, by: b.by });
    }
    const existingPl = await env.DB.prepare(
      `SELECT id FROM placements WHERE application_id=? AND client_id=? LIMIT 1`
    ).bind(appId, job.company_id).first();
    if (!existingPl) {
      await env.DB.prepare(
        `INSERT INTO placements (application_id, candidate_name, job_slug, job_title, client_name, stage, client_id, stage_since, created_at, updated_at)
         VALUES (?,?,?,?,?, 'SUBMITTED', ?, ?, ?, ?)`
      ).bind(appId, src.name || null, jobSlug, job.title,
             (await env.DB.prepare(`SELECT display_name FROM client_companies WHERE id=?`).bind(job.company_id).first() || {}).display_name || '未命名客戶',
             job.company_id, now.slice(0, 10), now, now).run();
    }
  }

  if (b.initial_stage) {
    await applyManualStage(env, { application_id: appId, stage: b.initial_stage,
      note: '手動新增時直接設定', by: b.by || null, company_id: job.company_id || null });
  }

  return { status: 200, body: { ok: true, application_id: appId, created, report_queued: reportQueued,
    company_linked: !!job.company_id,
    message: job.company_id ? null : '這個職缺還沒連到客戶名單，這筆紀錄目前只會在內部看得到，不會出現在客戶 portal' } };
}

// 內部結案／標記不推薦的核心邏輯——同樣抽成獨立函式（理由跟 doManualForward
// 一樣：TG bot 的 nc_pool 流程會在建立人選後立刻鏈式呼叫這支，原本用 self-fetch
// 一樣會撞 Cloudflare error 1042）。
async function doMarkClosed(env, b) {
  if (!b.application_id) return { status: 400, body: { ok: false, error: '缺 application_id' } };
  const notify = b.notify !== false;
  const reason = String(b.reason || '').trim().slice(0, 500);
  // 🚨 就服法紅線：這段文字會原封不動送給候選人，不是內部備註。
  const hits = law5Hits(reason);
  if (hits.length) {
    return { status: 422, body: {
      ok: false, error: 'law5_blocked',
      hits,
      message: `這段訊息會直接送給候選人，裡面出現了「${hits.join('」「')}」——`
        + '就業服務法第 5 條禁止以性別、年齡、婚育、國籍、身心障礙、宗教、'
        + '容貌等條件對求職者為差別待遇。\n\n'
        + '請改成不涉及這些條件的說法（例如「這次的職務條件與您的經歷方向不同」）。\n'
        + '⚠️ 客戶的原始要求該記還是要記，但記在顧問備註，不要寫進要送出去的訊息。',
    } };
  }
  const stageVal = notify ? 'CLOSED_LOST' : 'CLOSED_INTERNAL';
  const app = await env.DB.prepare(
    `SELECT a.name, a.job_slug, a.job_title, j.client_name FROM applications a
       LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id = ?`
  ).bind(b.application_id).first();
  if (!app) return { status: 404, body: { ok: false, error: '找不到這筆應徵' } };

  const now = nowTaipei();
  const exist = await env.DB.prepare(`SELECT id FROM placements WHERE application_id = ?`).bind(b.application_id).first();
  if (exist) {
    await env.DB.prepare(`UPDATE placements SET stage=?, close_reason=?, updated_at=? WHERE id=?`)
      .bind(stageVal, reason || null, now, exist.id).run();
  } else {
    await env.DB.prepare(
      `INSERT INTO placements (application_id, candidate_name, job_slug, job_title, client_name, stage, stage_since, close_reason, created_at, updated_at)
       VALUES (?,?,?,?,?, ?, ?, ?, ?, ?)`
    ).bind(b.application_id, app.name, app.job_slug, app.job_title,
           app.client_name || '（職缺未綁客戶）', stageVal, now.slice(0, 10), reason || null, now, now).run();
  }
  if (notify) {
    const rep = await env.DB.prepare(
      `SELECT id, consultant_decision FROM reports WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
    ).bind(b.application_id).first();
    if (rep && !rep.consultant_decision) {
      await env.DB.prepare(`UPDATE reports SET consultant_decision='rejected', decided_at=? WHERE id=?`)
        .bind(now, rep.id).run();
    }
    await notifyLineProgress(env, b.application_id);
  }
  return { status: 200, body: { ok: true } };
}

// ─────────────────────────────────────────────────────────────
// 2026-09-01 加：客戶版履歷（表格＋大頭貼版）。
// ⚠️ 這裡的 CLIENT_BANNED_RE／scrubForClient／isAnonymous／clientDisplayName
// 是從 deliver.py（本機 Python，既有客戶版 client.html 樣板用的過濾邏輯）
// 逐字 1:1 搬過來的——這是唯一一份「什麼字不能給客戶看」的規則，兩邊分別
// 維護一定會走鐘，寧可重複程式碼也不能重寫邏輯。deliver.py 改規則時這裡也要
// 跟著改。
const CLIENT_BANNED_RE = new RegExp(
  '(' +
  '(接案|目前|現有|現在|現職|原本|每月|月|年|實際)?收入' +
  '|現領|現薪|目前薪(資|水)|原薪' +
  '|其他(在談的)?(機會|offer|Offer|OFFER)' +
  '|手上(還有|有)(其他|別的)' +
  '|同時(在談|面試)' +
  '|(DISC|disc)' +
  '|人格(測驗|量表)' +
  '|測驗分數' +
  '|沒有正面回(答|應)|未正面回(答|應)|問(了)?兩次' +
  '|避而不(答|談)|閃避|說詞反覆|避重就輕' +
  '|籠統|含糊|交代不清|說不清楚|存疑|可信度|真實性' +
  '|建議(客戶)?(面試時)?(可以)?追問|追問清單' +
  '|內部評分|BARS' +
  ')'
);
function scrubForClient(text) {
  if (!text) return '';
  const clauses = String(text).split(/(?<=[。；;\n])/);
  const keep = [];
  for (const c of clauses) {
    if (!c.trim()) continue;
    if (!CLIENT_BANNED_RE.test(c)) { keep.push(c); continue; }
    const subs = c.split(/(?<=[，、])/).filter((s) => s.trim() && !CLIENT_BANNED_RE.test(s));
    if (subs.length) keep.push(subs.join('').replace(/[，、]+$/, ''));
  }
  const out = keep.join('').trim();
  return /[\w一-鿿]/.test(out) ? out : '';
}
function isAnonymous(clientNamed) {
  return !(clientNamed === 1 || clientNamed === '1');
}
function anonName(fullName) {
  const n = String(fullName || '').trim();
  if (!n) return '候選人';
  for (const sur of ['歐陽', '司馬', '諸葛', '上官', '皇甫', '尉遲', '公孫', '夏侯', '端木']) {
    if (n.startsWith(sur)) return sur + '先生／小姐';
  }
  if (/^[一-鿿]/.test(n)) return n[0] + '先生／小姐';
  const parts = n.split(/\s+/);
  return parts[parts.length - 1] + '先生／小姐';
}
function clientDisplayName(fullName, clientNamed) {
  return isAnonymous(clientNamed) ? anonName(fullName) : (fullName || '候選人');
}

// 電訪筆記即時彙整——抽成共用函式，讓「顧問後台📞記錄新的通話」跟「TG電洽
// 新增人選」兩條路都能用同一套邏輯即時產生 call_summary_md（人選卡片④電洽
// 內容區塊顯示的AI整理重點）。2026-09-02 發現：TG那條路只存了原始逐字稿、
// 設 call_report_pending=1 讓本機排程之後產完整報告，但完全沒呼叫這段AI摘要，
// 導致 TG 新增的人選就算完整報告已經跑完，電洽內容那格還是顯示「尚未記錄過
// 通話」——因為前端那格讀的是 call_summary_md，不是 report_md。
async function summarizeCallNotes(env, text) {
  if (!text) return null;
  try {
    const ai = await env.AI.run('@cf/meta/llama-3.3-70b-instruct-fp8-fast', {
      messages: [
        { role: 'system', content: '你是獵頭顧問的助理，把顧問打的電訪筆記整理成重點條列，不要新增筆記裡沒提到的資訊，沒提到的欄位就寫「未提及」。用繁體中文回答，直接輸出，不要開場白。' },
        { role: 'user', content: `請把下面這段電訪筆記整理成這六個標題各一段（每段2-3行以內）：\n重點狀況\n求職需求\n期望薪資\n離職原因\n優勢與劣勢\n顧問可再確認／可主動告知客戶的部分\n\n電訪筆記：\n${text.slice(0, 4000)}` },
      ],
    });
    return (ai && (ai.response || ai.result)) ? String(ai.response || ai.result).trim() : null;
  } catch (e) {
    return null; // 即時彙整失敗不擋主流程，本機排程的正式報告照樣會跑
  }
}

// 客戶版履歷 HTML 組裝——抽成共用函式，讓「正式應徵／顧問履歷庫」的人選
// (從 applications+reports 取資料) 跟「主動開發」的人選 (從 sourced_candidates
// 取資料，通常還沒面談過、報告內容多半是空的) 共用同一份排版邏輯，只是
// 餵進來的欄位來源不同。
// 2026-09-02 改版，比照參考範本（陳其寬_人選推薦報告.pdf）：
// - 配色從金色改藍色
// - 核心條件對應改成「編號＋粗體小標＋段落」（不是單純條列一句話）
// - 新增「補充說明」「我方建議」兩個獨立區塊
// - 檔名規則：{應徵職缺}-{人選姓名}-{客戶公司名稱}-德仁管理顧問公司
//   （HTML 的 <title> 就是瀏覽器「另存為PDF」預設檔名，設對這裡就好，
//   不用額外處理下載邏輯）
function buildClientFormalHtml({ name, jobTitle, clientName, rows, coreFit, supplementary, recommendation, photoB64 }) {
  const eHtml = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const photoHtml = photoB64 ? `<img src="${photoB64}" alt="大頭貼">` : '';
  const now = nowTaipei().slice(0, 10);
  const watermark = `Step1ne 德仁管理顧問｜${eHtml(name)} 人選推薦報告｜內部整理，經人選同意後提供貴公司參考，未經同意不得轉予第三方`;
  const fileTitle = [jobTitle, name, clientName, '德仁管理顧問公司'].filter(Boolean).join('-');
  return `<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>${eHtml(fileTitle)}</title>
<style>
@page{size:A4;margin:14mm}
body{font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;color:#23262d;font-size:13px;line-height:1.7;margin:0}
.watermark{font-size:10px;color:#8a8d95;border-bottom:1px solid #eee;padding-bottom:6px;margin-bottom:14px}
h1{font-size:20px;text-align:center;letter-spacing:.3em;margin:0 0 6px;color:#1a5c96}
.jobtitle{text-align:center;font-weight:700;font-size:14px;margin:0 0 4px;color:#1a5c96}
.meta{text-align:center;color:#6b6f78;font-size:12px;margin:0 0 18px}
h2{font-size:14px;border-bottom:2px solid #1a5c96;color:#1a5c96;padding-bottom:4px;margin:22px 0 10px}
table.basics{width:100%;border-collapse:collapse;table-layout:fixed}
table.basics td{border:1px solid #ccd9e5;padding:8px 12px;vertical-align:top}
table.basics td.k{width:110px;font-weight:700;background:#eef4fa;color:#1a5c96}
.photobox{float:right;width:110px;height:140px;border:1px solid #ccd9e5;margin-left:10px;overflow:hidden;background:#eef4fa}
.photobox img{width:100%;height:100%;object-fit:cover}
ol.cond{padding-left:20px;list-style:none;counter-reset:cond}
ol.cond li{margin-bottom:12px;counter-increment:cond}
ol.cond li b{display:block;color:#1a5c96;margin-bottom:2px}
ol.cond li b:before{content:counter(cond) ". "}
ul.notes{padding-left:20px}
ul.notes li{margin-bottom:6px}
.rec{white-space:pre-wrap}
</style>
<body>
<div class="watermark">${watermark}</div>
<h1>人 選 推 薦 報 告</h1>
<div class="jobtitle">${eHtml(jobTitle || '')}</div>
<div class="meta">${eHtml(clientName || '')} ｜ Step1ne 德仁管理顧問有限公司 ｜ ${now}</div>
<h2>人選基本資料</h2>
${photoHtml ? `<div class="photobox">${photoHtml}</div>` : ''}
<table class="basics">${rows.map(([k, v]) => `<tr><td class="k">${eHtml(k)}</td><td>${eHtml(v)}</td></tr>`).join('')}</table>
<div style="clear:both"></div>
${(coreFit && coreFit.length) ? `<h2>核心條件對應</h2><ol class="cond">${coreFit.map((c) => `<li><b>${eHtml(c.title)}</b>${eHtml(c.body)}</li>`).join('')}</ol>` : ''}
${(supplementary && supplementary.length) ? `<h2>補充說明（履歷未載之正面資訊）</h2><ul class="notes">${supplementary.map((r) => `<li>${eHtml(r)}</li>`).join('')}</ul>` : ''}
${recommendation ? `<h2>我方建議</h2><p class="rec">${eHtml(recommendation)}</p>` : ''}
<div class="watermark" style="border-top:1px solid #eee;border-bottom:none;margin-top:24px;padding-top:6px">${watermark}</div>
</body></html>`;
}

// 2026-09-02 加：AI 讀電洽逐字稿＋履歷全文，合併整理成客戶版履歷需要的
// 深度內容（核心條件對應／補充說明／我方建議），比照參考範本
// （陳其寬_人選推薦報告.pdf）的寫法跟深度。之前是直接抄阿財面談報告
// 的 content_json（那是不同用途產生的資料，很多人選根本沒有這份、或內容
// 太單薄），現在改成每次產生客戶版履歷都重新讀原始素材現場整理，資料
// 來源更直接、也不受「有沒有跟阿財面談過」限制。
async function synthesizeClientReport(env, { name, callNotes, resumeText, existingReportMd }) {
  const source = [
    resumeText ? `【履歷全文】\n${resumeText.slice(0, 6000)}` : '',
    callNotes ? `【電洽逐字稿／筆記】\n${callNotes.slice(0, 6000)}` : '',
    existingReportMd ? `【既有初篩報告（可能是阿財面談產出，供補充參考）】\n${existingReportMd.slice(0, 3000)}` : '',
  ].filter(Boolean).join('\n\n');
  if (!source) return null;
  const prompt = `你是獵頭顧問的助理，要把下面這位人選的履歷跟電洽逐字稿整理成一份「給用人企業客戶看」的正式人選推薦報告內容。

規則：
- 只寫查得到根據的內容，不要編造履歷或逐字稿裡沒提到的事實。
- 語氣正面、客觀陳述事實，不要出現「不推薦」「顧問懷疑」這類內部判斷用語。
- 不要出現候選人目前/接案收入、其他機會/offer細節、人格測驗分數這類不該給客戶看的內容。
- 「核心條件對應」要像績效面談摘要一樣，依電訪跟履歷實際談到的重點分成 4~7 點，每點一個簡短小標＋一段 100~200 字的敘述（可以包含：學習意願與職務理解、轉職動機、穩定度與抗壓力、學經歷背景、薪資接受度、到職彈性、工作模式接受度等面向，只寫有談到的，沒談到的不要硬湊）。
- 「補充說明」是履歷本身沒寫、但電訪過程中觀察到的正面資訊（例如跨文化溝通能力、職涯決策成熟度等），列 2~4 點，每點一句話。
- 「我方建議」是顧問對客戶的整體推薦結論，2~3 段，總結人選適合度跟後續建議（例如儘速安排面談）。
- 「現況」「相關經驗」「交通工具」各是履歷基本資料表格要用的一行文字（現況＝目前工作狀態一句話；相關經驗＝跟這個職缺相關的經驗程度一句話；交通工具＝有寫才填，沒有就空字串）。

用下面這個 JSON 格式直接輸出，不要加任何說明文字、不要用 markdown code block 包起來：
{"current_state":"...","related_experience":"...","transportation":"...","core_fit":[{"title":"...","body":"..."}],"supplementary":["...","..."],"recommendation":"..."}

人選姓名：${name}

${source}`;
  let ai = null;
  try {
    // ⚠️ 2026-09-02 修：預設 max_tokens 太小，4~7點核心條件對應+補充說明+
    // 我方建議這種篇幅的JSON常常輸出到一半被截斷變成不合法JSON
    // （實測噴 "Unterminated string in JSON"）。拉高到 3000。
    ai = await env.AI.run('@cf/meta/llama-3.3-70b-instruct-fp8-fast', {
      messages: [
        { role: 'system', content: '你是專業獵頭顧問助理，只輸出繁體中文，只輸出合法JSON，不要加任何前後說明文字。' },
        { role: 'user', content: prompt },
      ],
      max_tokens: 3000,
    });
    const respField = ai && (ai.response !== undefined ? ai.response : ai.result);
    const raw = typeof respField === 'string' ? respField.trim() : JSON.stringify(respField);
    const jsonText = raw.replace(/^```json\s*/i, '').replace(/^```\s*/, '').replace(/```\s*$/, '').trim();
    const parsed = typeof respField === 'object' && respField !== null ? respField : JSON.parse(jsonText);
    return {
      current_state: parsed.current_state || null,
      related_experience: parsed.related_experience || null,
      transportation: parsed.transportation || null,
      core_fit: Array.isArray(parsed.core_fit) ? parsed.core_fit.filter((c) => c && c.title && c.body) : [],
      supplementary: Array.isArray(parsed.supplementary) ? parsed.supplementary.filter(Boolean) : [],
      recommendation: parsed.recommendation || null,
    };
  } catch (e) {
    return null; // AI整理失敗不擋主流程，退回顧問手動填的欄位
  }
}


// 開關職缺的招募狀態——顧問後台既有的 open/closed 切換（/admin/jobs/:slug）
// 跟客戶 portal 的「結束招募／重新招募」都呼叫這裡，行為（含誰關的／原因／
// 時間戳）不會兩邊各自維護出不同步的版本。關閉時記錄 closed_by／closed_reason／
// closed_at；重新打開時清空這三個欄位（該次招募已經結束了），reason 存進
// reopened_reason 純供顧問參考，不是必填也不影響狀態機。
async function setJobOpenState(env, { slug, status, by, reason }) {
  const now = nowTaipei();
  if (status === 'closed') {
    await env.DB.prepare(
      `UPDATE jobs SET status='closed', closed_by=?, closed_reason=?, closed_at=? WHERE slug=?`
    ).bind(by || null, reason || null, now, slug).run();
  } else {
    await env.DB.prepare(
      `UPDATE jobs SET status='open', closed_by=NULL, closed_reason=NULL, closed_at=NULL, reopened_reason=? WHERE slug=?`
    ).bind(reason || null, slug).run();
  }
  return { status, closed_by: status === 'closed' ? (by || null) : null, closed_at: status === 'closed' ? now : null };
}

// 2026-08-25 加：職缺建立目前有 4 條互不相通的入口（Telegram 自建職缺流程／
// 用人需求表 portal／招募形式評估工具／顧問直接改資料庫），沒有一個會檢查
// 「這個職缺是不是已經存在了」——律准的「資深職業安全衛生工程師」就是這樣
// 撞出兩筆。這裡收斂成一顆共用函式，凡是會新建 jobs 列的地方都先查一次：
// 同一家客戶（company_id 相符，或沒有 company_id 就比對 client_name 文字）
// ＋同樣的職稱（去頭尾空白後完全相同），排除已關閉的職缺（那可能是真的結案
// 過的舊職缺，不該擋新職缺重新開）。抓到就回傳既有那筆，讓呼叫端決定要
// 擋下來還是提醒顧問，不要自己偷偷合併內容（合併判斷該讓人做，不該自動猜）。
async function findDuplicateJob(env, { companyId, clientName, title, excludeSlug }) {
  const normTitle = String(title || '').trim();
  if (!normTitle) return null;
  if (companyId) {
    const row = await env.DB.prepare(
      `SELECT slug, title, status FROM jobs
        WHERE company_id = ? AND trim(title) = ? AND status != 'closed' AND slug != ?`
    ).bind(companyId, normTitle, excludeSlug || '').first();
    if (row) return row;
  }
  if (clientName) {
    const row = await env.DB.prepare(
      `SELECT slug, title, status FROM jobs
        WHERE trim(client_name) = ? AND trim(title) = ? AND status != 'closed' AND slug != ?`
    ).bind(String(clientName).trim(), normTitle, excludeSlug || '').first();
    if (row) return row;
  }
  return null;
}

// 單筆應徵的「候選人看得懂的現況」。
//
// ⚠️ 判斷邏輯直接複用 /admin/jobs 與 /admin/funnel 那一套漏斗分類
//    （applicants / screening / client_stage / offered / onboard / closed），
//    不要自己另外發明一套——不然候選人在 LINE 看到的階段會跟顧問後台看到的對不起來。
// ⚠️ 2026-08-13 Jacky 明確要求：跟人選講話絕對不能出現「客戶」這個字——
// 對他們來說，那家公司不是「我們的客戶」，是他們要去工作的地方。一律用
// 「業主」「用人單位」，跟 interview-conductor SKILL.md 裡阿財對談時
// 已經在用的詞一致，不要自己另外發明一套。

// 兩個時間點都算的天數差——interview_ended_at 跟 nowTaipei() 存的都是
// 「YYYY-MM-DD HH:MM:SS」這種不含時區標記的台北在地時間字串，直接當同一個
// 時區算差值即可，不用另外轉時區。
function daysSinceTaipei(ts) {
  if (!ts) return 0;
  const then = Date.parse(String(ts).replace(' ', 'T'));
  const now = Date.parse(nowTaipei().replace(' ', 'T'));
  if (Number.isNaN(then) || Number.isNaN(now)) return 0;
  return (now - then) / 86400000;
}

// 阿財初審完成、等顧問審閱這段期間要跟候選人說的話——2026-08-13 加。
// ⚠️ 這句話有兩個地方會用到：① scheduled() 的 1-1 主動推播（面談後5分鐘／隔2天）
// ② 候選人自己點「追蹤面試進度」查詢時（deriveApplicationProgress）。
// 兩邊各寫一份文案的話，候選人會看到系統主動推的話跟自己查到的話對不起來——
// Jacky 自己測試時就抓到這個落差，所以在這裡收斂成同一個函式，只寫一次。
function pendingReviewMessage(daysSince, hasAppointment, hasHandledNote) {
  if (!hasAppointment && !hasHandledNote && daysSince >= 2) {
    return {
      text: '您的初審已經完成 2 天了，還在審閱中。想主動催一下進度嗎？',
      escalated: true,
    };
  }
  // 2026-08-24 修：hasHandledNote 代表顧問已經有跟進紀錄——最常見就是已經把
  // 資料送給用人單位了。原本這裡不分青紅皂白都講「接下來會協助送審」，
  // 會讓已經送出去好幾週的人選一直以為自己還沒被送審，是假訊息。
  // 這裡只根據「有沒有跟進紀錄」判斷，不去猜 placements.stage 實際代表什麼
  // （那個欄位目前有好幾套互不相通的字串，貿然照字面判斷「已結案」風險更高，
  // candidate-facing copy 這塊寧可保守，不確定的不要猜）。
  if (hasHandledNote) {
    return {
      text: '您的資料已經送到用人單位那邊了，目前在等候對方回覆是否安排進一步面試。'
        + '有進度會盡快通知您；如果等候的時間比較久，也歡迎直接訊息詢問顧問目前的狀況。',
      escalated: false,
    };
  }
  return {
    text: '1-2 天內會審閱完成，若有任何需要確認的地方會主動通知您；有任何問題也歡迎直接訊息告知。\n\n'
      + '如果確認符合條件，接下來會協助送審給用人單位，確認是否安排進一步面談。',
    escalated: false,
  };
}
async function deriveApplicationProgress(env, appId) {
  const app = await env.DB.prepare(
    `SELECT id, name, job_slug, job_title, interview_state, handled_note,
            interview_started_at, interview_ended_at, created_at,
            manual_stage, manual_stage_note, manual_stage_by, manual_stage_at,
            client_interview_labels
       FROM applications WHERE id = ?`
  ).bind(appId).first();
  if (!app) return null;
  let roundLabels = {};
  try { roundLabels = JSON.parse(app.client_interview_labels || '{}'); } catch { roundLabels = {}; }

  const report = await env.DB.prepare(
    `SELECT consultant_decision FROM reports
      WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
  ).bind(appId).first();

  const placement = await env.DB.prepare(
    `SELECT stage, onboard_date, candidate_care_log, close_reason FROM placements
      WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
  ).bind(appId).first();

  const { results: appts } = await env.DB.prepare(
    `SELECT stage, status, confirmed_slot FROM interview_appointments
      WHERE application_id = ? ORDER BY stage ASC, created_at ASC`
  ).bind(appId).all();
  const confirmedAt = (stg) => (appts || []).filter((x) => x.stage === stg && x.status === 'confirmed').slice(-1)[0];

  // 2026-08-13 全面改：候選人查進度看到的文字，完全交給跟後台階段列共用的
  // resolveStage() 判斷結果決定——不管候選人現在卡在哪一關（含顧問手動指定的
  // 任何一關，不只「錄取」），這裡跟後台階段列講的永遠是同一件事。
  const jobStageKeys = await getJobStageKeys(env, app.job_slug);
  const stageInfo = resolveStage(app, report, appts || [], placement, roundLabels, jobStageKeys);
  const stageUp = placement ? String(placement.stage || '').toUpperCase() : '';

  const jobTitle = app.job_title || app.job_slug || '這個職缺';
  let message;
  let showNudgeButton = false;   // 卡片要不要多顯示「提醒顧問看一下」按鈕
  let closed = false;   // 結案不續——卡片要換成橘色，跟一般進度更新的藍色卡區分開

  // 2026-08-18 加：備選是平行狀態，不是線性階段的其中一關——優先於下面的
  // switch 顯示，跟結案不續同一個做法。候選人不用被告知「你是備選」這種
  // 直接說法，講「用人單位優先看別人、資料保留」——誠實但不讓人洩氣。
  if (app.manual_stage === 'backup') {
    message = '這個職缺目前用人單位優先安排了其他人選面談，您的資料仍保留在候選名單中，'
      + '如果後續有調整會盡快通知您。';
  } else if (placement && (stageUp === 'CLOSED_LOST' || stageUp === 'CLOSED')) {
    // 2026-08-24 加：'CLOSED' 是 placement_tracker.py 自己的結案字串（跟這裡
    // 原本認的 'CLOSED_LOST' 是不同來源、不同時期寫的兩套詞彙）。之前這裡沒認，
    // 候選人已經被業主婉拒了，卻還在收到「還在審閱中」的訊息——湯豐銘那筆
    // 已經跟 Jacky 當面確認過，'CLOSED' 在這個案子裡真的就是婉拒，不是誤判。
    // 只加這一個值，'CLOSED' 以外 placement_tracker.py 的其他結案字串
    // （REJECTED_BY_CLIENT／WITHDRAWN_BY_CANDIDATE／ON_HOLD／PLACED）語意不同，
    // 沒有一併確認過就不要跟著猜。
    // 已婉拒／結案——委婉但誠實，不用「淘汰」這種字眼
    // （用詞分寸參考顧問版／客戶版報告的既有規則：不確定的不編、但也不用傷人的說法）
    // ⚠️ 2026-08-14 改：close_reason 原本只是塞進固定模板中間的一句話片段，
    // Jacky 反應「以為改文案是整段都能改」——後台彈窗現在讓顧問直接編輯完整訊息，
    // 這裡收到的就是完整訊息本身，不再自己組模板。沒填就用制式的完整版當保底。
    // 客戶→用人單位的置換還是留著，防止顧問手誤打了不該出現的字眼
    // （候選人文字三條硬規則的第一條，之前真的出過事故）。
    const rawMsg = String(placement.close_reason || '').trim();
    message = (rawMsg || '這個職缺這次很可惜沒有繼續往下走，很抱歉沒有帶來好消息。'
        + '您的資料會留著，未來有更合適的職缺會再與您聯繫，也歡迎透過選單直接找顧問聊聊。')
      .replace(/客戶/g, '用人單位');
    closed = true;
  } else if (report && report.consultant_decision === 'rejected') {
    message = '審閱後，這次沒有把您推薦給這個職缺，很抱歉沒有帶來好消息。'
      + '歡迎持續留意我們之後釋出的其他職缺。';
  } else if (report && report.consultant_decision === 'need_more') {
    message = '審閱後想再跟您確認一些細節，會盡快主動聯繫您，麻煩留意來電或訊息。';
  } else {
    // 🚨 2026-08-13 提醒：這個 switch 裡任何一支文案都不能直接 echo 顧問內部欄位
    // （handled_note 等）——呂皓宇那次事故的教訓，見 feedback_candidate_facing_copy_rules。
    switch (stageInfo.effective_key) {
      case 'care':
      case 'onboard':
        // 「報到」「到職關懷」這兩關目前共用同一句——候選人角度沒有必要分兩套講法，
        // 都是「已經確定/已經到職」的狀態。
        message = '恭喜您已經到職了！之後有任何問題歡迎隨時透過選單聯繫顧問。';
        break;
      case 'offer':
        message = '好消息，用人單位已經決定邀請您加入，目前在談後續入職事宜，很快會有人跟您確認細節。';
        break;
      case 'stage1':
      case 'stage2':
      case 'stage3':
      case 'stage4': {
        const stgNum = Number(stageInfo.effective_key.slice(-1));
        const lb = STAGE_LABEL_APPT[stgNum] || '面試';
        const a = confirmedAt(stgNum);
        message = (a && a.confirmed_slot)
          ? `您的${lb}面試時間已經確認囉，記得準時參加。`
          : `您的資料已經送到用人單位那邊，目前正在安排${lb}面試。有進一步消息會盡快與您聯繫。`;
        break;
      }
      case 'confirm': {
        // ⚠️ 跟 1-1 主動推播卡（scheduled() 裡的 postinterview／progress2day）共用
        // pendingReviewMessage()，不管是候選人自己來查、還是系統主動推，講的都是同一句話。
        const hasAppt = (appts || []).length > 0;
        const daysSince = daysSinceTaipei(app.interview_ended_at || app.interview_started_at || app.created_at);
        // 2026-08-24 修：判斷「已經送出去了」不能只看 handled_note 有沒有人手動打字——
        // placement_tracker.py 是另一支程式，會自己把 placements.stage 往前推
        // （SUBMITTED → AWAITING_CLIENT_FEEDBACK → INTERVIEW_REQUESTED → …），
        // 不會順便去補一句 handled_note。只認 handled_note 的話，以後只要是
        // placement_tracker.py 自己推進度、沒有顧問手動留言的案子，還是會卡回
        // 「1-2天內審閱中」這句舊話，同一個問題會在張博州、呂皓宇之後的人選身上
        // 重複發生。這裡改成兩個訊號只要有一個成立就算「已經送出去了」。
        // 用允許清單、不用「排除已知的結案字樣」——placement_tracker.py 自己
        // 定義的結案字串（REJECTED_BY_CLIENT／WITHDRAWN_BY_CANDIDATE／ON_HOLD／
        // CLOSED／PLACED，見 placement_config.json 的 stages_terminal_no_aging）
        // 一旦有新增或改名，排除法會漏接、把已經被拒的人講成「還在等回覆」，
        // 比原本那句舊話更糟。只認「明確還在進行中」的這幾個值才算數。
        const placementStage = String((placement && placement.stage) || '').toUpperCase();
        const IN_PROGRESS_WITH_CLIENT = new Set([
          'AWAITING_CLIENT_FEEDBACK', 'INTERVIEW_REQUESTED', 'INTERVIEW_SCHEDULED',
          'CLIENT_DECISION_PENDING', 'INTERVIEWING', 'OFFER_PENDING',
        ]);
        const hasClientStageSignal = IN_PROGRESS_WITH_CLIENT.has(placementStage);
        const prm = pendingReviewMessage(
          daysSince,
          hasAppt,
          !!(app.handled_note && app.handled_note.trim()) || hasClientStageSignal,
        );
        message = prm.text;
        showNudgeButton = prm.escalated;
        break;
      }
      default:   // 'screening'：初審還沒完成
        message = '您的初審面談還沒有完成，建議點選單裡的「AI阿財面試」繼續完成初審。';
    }
  }

  // 初審面試日期：有實際面談時間就用那個，還沒面談過就用應徵時間，
  // 讓候選人知道這則訊息是在講「哪一次」——他可能同時應徵過不只一個職缺。
  const dateRaw = app.interview_started_at || app.created_at || '';
  const interviewDate = dateRaw ? dateRaw.slice(5, 10).replace('-', '/') : '';
  // secondStageDate：目前卡在第一到第四階段、且該階段已確認時段時的面試時間——
  // 欄位名稱沿用舊的（progressFlexBubble 直接讀這個欄位顯示），但內容泛化成
  // 「目前這一關」的時間，不再固定寫死是第二階段。
  let secondStageDate = '';
  if (['stage1', 'stage2', 'stage3', 'stage4'].includes(stageInfo.effective_key)) {
    const a = confirmedAt(Number(stageInfo.effective_key.slice(-1)));
    if (a && a.confirmed_slot) {
      secondStageDate = a.confirmed_slot.slice(5, 10).replace('-', '/') + ' ' + a.confirmed_slot.slice(11, 16);
    }
  }

  return { jobTitle, message, name: app.name || '', interviewDate, secondStageDate, jobSlug: app.job_slug || '',
    showNudgeButton, appId, closed };
}

// 2026-08-13 從純文字升級成 Flex——跟 notifyLineProgress() 的主動推播共用
// progressFlexBubble()，人選主動來查跟系統主動推播看到的卡片長一樣。
// 同時應徵多個職缺時用 Carousel（一張卡一筆），不要合併成一大段文字。
async function buildProgressMessages(env, applicationIds) {
  const ids = Array.isArray(applicationIds) ? applicationIds : [];
  const rows = [];
  for (const id of ids) {
    const row = await deriveApplicationProgress(env, id);
    if (row) rows.push(row);
  }
  if (!rows.length) {
    return [{ type: 'text', text: '目前查不到您的應徵紀錄，想直接找顧問，歡迎透過下方選單聯繫。' }];
  }
  if (rows.length === 1) {
    const r = rows[0];
    return [{
      type: 'flex',
      altText: `【進度查詢】${r.jobTitle}：${r.message}`.slice(0, 400),
      contents: progressFlexBubble(r, r.jobSlug),
    }];
  }
  return [{
    type: 'flex',
    altText: `您目前有 ${rows.length} 筆應徵進度，點開查看`,
    contents: { type: 'carousel', contents: rows.map((r) => progressFlexBubble(r, r.jobSlug)) },
  }];
}

// ⚠️ 2026-08-13 改：原本只比對手機號碼就綁定，Jacky 要求改成姓名＋信箱＋手機
// 三項一起交叉比對——手機號碼容易重複、打錯一碼、或借用家人號碼應徵，
// 單一欄位比對不到人；三項都跟應徵表單對得上才綁，避免認錯人（同一支手機
// 查到別人的面試進度，比查不到還糟）。「只要綁定一次終身綁定」＝這道驗證
// 只在第一次做，state='bound' 之後永遠不再問，見下面 'bound' 分支。
//
// ⚠️ 2026-08-13 再改：一開始做成「三項一次打在同一則」，Jacky 要求改成
// 一次只問一項——先講清楚要問三個，然後一則一則來（姓名→信箱→手機），
// 對話節奏比較像真人客服，也不會因為使用者漏打一項就整則作廢要重打。
const LINE_ASK_NAME_TEXT =
  '請提供三項資料幫您核對身分並查詢面試進度：姓名、信箱、手機號碼。\n\n'
  + '一項一項來，麻煩先輸入您的姓名：';
const LINE_ASK_EMAIL_TEXT = '謝謝，接著請輸入您應徵時留的信箱：';
const LINE_ASK_PHONE_TEXT = '好，最後請輸入您應徵時留的手機號碼：';
const LINE_BAD_EMAIL_TEXT = '這個看起來不像信箱，麻煩再輸入一次：';
const LINE_BAD_PHONE_TEXT = '這個看起來不像手機號碼，麻煩再輸入一次：';
const LINE_NO_MATCH_TEXT =
  '這三項資料（姓名、信箱、手機號碼）沒有辦法完全對到應徵紀錄，'
  + '請確認是不是跟應徵當時留的資料一字不差（信箱大小寫沒關係，其他請照原本填的）。'
  + '想再試一次，請重新輸入姓名；想直接找顧問，歡迎透過下方選單聯繫我們。';

function normalizeNameForMatch(s) {
  return String(s || '').replace(/\s+/g, '');
}

async function handleLineEvent(env, ev) {
  // 面談時段 Carousel 卡片上的「選這個時段」按鈕——postback 事件，不是 message，
  // 要在最上面單獨處理，跟查進度那條 message 流程完全分開。
  if (ev.type === 'postback' && ev.postback && ev.replyToken) {
    const data = String(ev.postback.data || '');
    if (data.startsWith('confirm_appt:')) {
      const [, token, ...rest] = data.split(':');
      const slotAt = rest.join(':');   // slot_at 本身含冒號（HH:MM），要接回去
      const r = await confirmAppointmentByToken(env, token, slotAt);
      if (!r.ok) return lineReply(env, ev.replyToken, `不好意思，${r.error || '確認失敗'}。`);
      const flex = { type: 'flex', altText: `✅ 已確認面談時間：${r.picked.slot_at}`,
        contents: appointmentConfirmedFlexCard(r.appt, r.picked) };
      return lineReplyMessages(env, ev.replyToken, [flex]);
    }
    // 2026-08-13 加：選時段卡「都不方便」用 datetimepicker——點下去跳 LINE 原生的
    // 日期時間選擇器，選完 LINE 會把值放在 ev.postback.params.datetime，
    // 不用再讓候選人自己打字、也不用猜他打的是不是一個看得懂的時間。
    // ⚠️ 2026-08-13 再改（Jacky 三輪回饋收斂後的版本）：
    //   ① 要滿 3 個才停，中途不給退路
    //   ② 選完 3 個不要逐一丟給顧問，要先給候選人看一次總覽，他按確認才一次送出
    //   ③ 總覽要有「改時間」——選錯了可以整個重選，不是只能將錯就錯送出去
    // 3 個時間就用逗號累積在 postback data 裡帶著走，不用另外開表存暫存狀態。
    // ⚠️ datetime 值本身含冒號（HH:MM），會被 data.split(':') 切壞，所以先把
    // 冒號拿掉存進 data，顯示的時候再插回去（encodeDT／decodeDT）。
    if (data.startsWith('alt_time_appt:')) {
      const [, token, stageLabel, attemptStr, accumulated] = data.split(':');
      const attempt = Number(attemptStr) || 1;
      const picked = ev.postback.params && ev.postback.params.datetime;
      if (!picked) return lineReply(env, ev.replyToken, '不好意思，沒有收到您選的時間，麻煩再試一次。');
      const encodedPicked = picked.replace(':', '');
      const newAccumulated = accumulated ? `${accumulated},${encodedPicked}` : encodedPicked;

      if (attempt < 3) {
        const need = 3 - attempt;
        const [d, t] = picked.split('T');
        const flex = { type: 'flex', altText: '請再多提供一個方便的時間', contents: { type: 'bubble',
            body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
              contents: [
                { type: 'text', text: `✅ 已收到第 ${attempt} 個方便的時間`, size: 'sm', weight: 'bold', color: '#16202e', wrap: true },
                { type: 'text', text: `${d} ${t}`, size: 'md', color: '#2f6fed', weight: 'bold' },
                { type: 'text', text: `為了讓顧問比較好安排，麻煩至少再提供 ${need} 個方便的時間 🙏`, size: 'xs', color: '#8993a8', wrap: true, margin: 'md' },
              ] },
            footer: { type: 'box', layout: 'vertical', paddingAll: '12px',
              contents: [{ type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
                action: { type: 'datetimepicker', label: `選第 ${attempt + 1} 個方便的時間`,
                  data: `alt_time_appt:${token}:${stageLabel}:${attempt + 1}:${newAccumulated}`,
                  mode: 'datetime', initial: candidatePickerInitial(), min: candidatePickerMin(), max: '2027-12-31T23:59' } }] } } };
        return lineReplyMessages(env, ev.replyToken, [flex]);
      }

      // 滿 3 個了——不直接送出，先給候選人看一次總覽讓他確認或整個重選
      const times = newAccumulated.split(',').map(decodeDT);
      const summaryRows = times.map((dt, i) => ({
        type: 'text', size: 'sm', color: '#16202e', wrap: true,
        text: `${i + 1}. ${dt.replace('T', ' ')}`,
      }));
      const flex = { type: 'flex', altText: '請確認您提供的 3 個時間', contents: { type: 'bubble',
          header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
            contents: [{ type: 'text', text: '📋 請確認您提供的時間', color: '#ffffff', size: 'md', weight: 'bold', wrap: true }] },
          body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
            contents: [
              ...summaryRows,
              { type: 'separator', margin: 'md' },
              { type: 'text', text: '確定後會一次送給顧問；選錯的話可以整個重選。', size: 'xs', color: '#8993a8', wrap: true, margin: 'md' },
            ] },
          footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
            contents: [
              { type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
                action: { type: 'postback', label: '✅ 確認送出', data: `alt_time_confirm:${token}:${stageLabel}:${newAccumulated}`, displayText: '確認送出這 3 個時間' } },
              { type: 'button', style: 'secondary', height: 'sm',
                action: { type: 'datetimepicker', label: '🔄 改時間（重選第 1 個）', data: `alt_time_appt:${token}:${stageLabel}:1:`,
                  mode: 'datetime', initial: candidatePickerInitial(), min: candidatePickerMin(), max: '2027-12-31T23:59' } },
            ] } } };
      return lineReplyMessages(env, ev.replyToken, [flex]);
    }
    // 候選人在總覽卡按「確認送出」——這裡才真的一次通知顧問，不是選一個丟一個。
    if (data.startsWith('alt_time_confirm:')) {
      const [, token, stageLabel, accumulated] = data.split(':');
      const times = (accumulated || '').split(',').filter(Boolean).map(decodeDT);
      const appt = await env.DB.prepare(
        `SELECT ia.id, a.name FROM interview_appointments ia JOIN applications a ON a.id = ia.application_id WHERE ia.token = ?`
      ).bind(token).first();
      const linesText = times.map((dt, i) => `第 ${i + 1} 個：${dt.replace('T', ' ')}`).join('\n');
      await notify(env,
        `🗣️ ${appt ? appt.name : '候選人'}覺得${stageLabel || '面試'}排的時間都不方便，提供了以下方便的時間：\n${linesText}\n麻煩跟他重新約時間。`,
        { message_thread_id: THREAD.decide });
      return lineReply(env, ev.replyToken, '已經把這 3 個時間一起送出去了，確認後會再跟您約時間，謝謝您 🙏');
    }
    // 2026-08-13 加：確認卡上的「確認收到」按鈕——單純標記已讀，讓顧問知道
    // 這則真的被看到了，不影響面談狀態本身。
    if (data.startsWith('ack_appt:')) {
      const apptId = data.slice('ack_appt:'.length);
      const appt = await env.DB.prepare(
        `SELECT ia.*, a.name FROM interview_appointments ia JOIN applications a ON a.id = ia.application_id WHERE ia.id = ?`
      ).bind(apptId).first();
      if (appt && !appt.ack_at) {
        await env.DB.prepare(`UPDATE interview_appointments SET ack_at = ? WHERE id = ?`)
          .bind(nowTaipei(), apptId).run();
        await notify(env, `👀 ${appt.name} 已確認收到面試通知（${appt.confirmed_slot || ''}）`,
          { message_thread_id: THREAD.system });
      }
      return lineReply(env, ev.replyToken, '好的，已經幫您記錄囉，面試當天見 🙌');
    }
    // 2026-08-13 加：「1-1 顧問初審確認」隔 2 天提醒卡上的按鈕——直接推播通知你，
    // 你在「顧問人選回報區」回一句話，既有的 report_tick.py 就會照舊解析成狀態更新，
    // 自動再推一則進度給候選人（沿用 notifyLineProgress 那條線），不用另外做一套。
    if (data.startsWith('nudge_consultant:')) {
      const appId = data.slice('nudge_consultant:'.length);
      const app = await env.DB.prepare(`SELECT name, job_title, job_slug FROM applications WHERE id = ?`).bind(appId).first();
      if (app) {
        await notify(env,
          `🔔 ${app.name}（${app.job_title || app.job_slug}）在 LINE 上催進度了，麻煩更新一下——`
          + `直接在這個主題回一句就好，例如「還在等企業回覆」，我會幫你更新狀態並回覆給候選人。`,
          { message_thread_id: THREAD.report });
      }
      return lineReply(env, ev.replyToken, '已經送出提醒了，麻煩再耐心等一下 🙏');
    }
    // 2026-08-13 加：錄取卡「我願意接受／我需要再考慮」——不管選哪個都要讓顧問知道，
    // 才能主動接下去談，不是等候選人自己回訊息。
    if (data.startsWith('offer_resp:')) {
      const [, resp, appId] = data.split(':');
      const app = await env.DB.prepare(`SELECT name, job_title, job_slug FROM applications WHERE id = ?`).bind(appId).first();
      const placement = await env.DB.prepare(
        `SELECT id FROM placements WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
      ).bind(appId).first();
      if (placement) {
        await env.DB.prepare(`UPDATE placements SET offer_response = ?, offer_response_at = ? WHERE id = ?`)
          .bind(resp, nowTaipei(), placement.id).run();
      }
      const label = resp === 'accepted' ? '✅ 願意接受' : '🤔 需要再考慮';
      await notify(env,
        `${label}：${app ? app.name : ''}（${app ? (app.job_title || app.job_slug) : ''}）對錄取通知的回覆\n`
        + (resp === 'accepted' ? '麻煩主動聯繫談後續入職事宜。' : '麻煩主動聯繫了解他的考量，看能不能留住。'),
        { message_thread_id: THREAD.decide });
      const msg = resp === 'accepted'
        ? '太好了，恭喜您！很快會主動聯繫您，說明後續入職的細節 🎉'
        : '了解，不用著急，會再主動跟您聊聊，看有什麼可以協助的地方。';
      return lineReply(env, ev.replyToken, msg);
    }
    // 2026-08-13 加：報到日期確認卡的三顆按鈕
    if (data.startsWith('onboard_resp:')) {
      const [, resp, appId] = data.split(':');
      const app = await env.DB.prepare(`SELECT name, job_title, job_slug FROM applications WHERE id = ?`).bind(appId).first();
      const placement = await env.DB.prepare(
        `SELECT id FROM placements WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
      ).bind(appId).first();
      if (placement) {
        await env.DB.prepare(`UPDATE placements SET onboard_confirm_status = ?, onboard_confirm_at = ? WHERE id = ?`)
          .bind(resp, nowTaipei(), placement.id).run();
      }
      const label = { accepted: '✅ 願意接受', considering: '🤔 需要考慮', question: '❓ 有其他問題' }[resp] || resp;
      await notify(env,
        `${label}：${app ? app.name : ''}（${app ? (app.job_title || app.job_slug) : ''}）對報到日期的回覆`
        + (resp === 'question' ? '，麻煩主動聯繫了解他的問題。' : '。'),
        { message_thread_id: THREAD.decide });
      const msg = resp === 'accepted'
        ? '收到，謝謝確認！報到前會再提醒您準備事項 😊'
        : resp === 'question' ? '好的，看到會主動跟您聯繫說明。' : '好的，會再跟您確認細節。';
      return lineReply(env, ev.replyToken, msg);
    }
    // 2026-08-13 加：到職關懷卡「一切順利／想聊聊」——想聊聊才通知顧問，
    // 一切順利的話系統自己收著就好，不用每次都吵顧問。
    if (data.startsWith('care_resp:')) {
      const [, resp, appId, point] = data.split(':');
      if (resp === 'talk') {
        const app = await env.DB.prepare(`SELECT name, job_title, job_slug FROM applications WHERE id = ?`).bind(appId).first();
        await notify(env,
          `💬 ${app ? app.name : ''}（${app ? (app.job_title || app.job_slug) : ''}）到職第 ${point} 天關懷卡按了「想聊聊」，麻煩主動聯繫。`,
          { message_thread_id: THREAD.report });
      }
      const msg = resp === 'talk'
        ? '好的，已經送出通知了，會盡快跟您聯繫 🙌'
        : '太好了，謝謝分享！有任何狀況隨時都可以跟顧問說 😊';
      return lineReply(env, ev.replyToken, msg);
    }
    // 2026-08-13 加：各張卡片上原本共用的「聯繫顧問」按鈕統一改成 postback，
    // 不再是 message 型（那種按了只是把文字丟進對話框，沒有任何自動處理，
    // 候選人會覺得「按了沒反應」）。這裡統一通知你 Telegram，也回候選人一句。
    // 從「已關閉職缺」卡片按的——這個人還沒有應徵紀錄，用職缺代號當上下文，
    // 不要硬套 contact_consultant:（那支要 application_id，查不到會通知一則空白訊息）。
    if (data.startsWith('contact_closed_job:')) {
      const slug = data.slice('contact_closed_job:'.length);
      const j = await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(slug).first();
      await notify(env,
        `💬 有人在 LINE 詢問已結束招募的「${j ? j.title : slug}」，按了「跟顧問聊聊」。`
        + `他還沒有應徵紀錄，麻煩主動聯繫看看有沒有適合的其他職缺。`,
        { message_thread_id: THREAD.decide });
      return lineReply(env, ev.replyToken,
        '好的，已經通知顧問了 🙏 顧問會跟您聊聊有沒有其他適合的機會，謝謝您！');
    }
    if (data.startsWith('contact_consultant:')) {
      const appId = data.slice('contact_consultant:'.length);
      const app = await env.DB.prepare(`SELECT name, job_title, job_slug FROM applications WHERE id = ?`).bind(appId).first();
      await notify(env,
        `💬 ${app ? app.name : ''}（${app ? (app.job_title || app.job_slug) : ''}）在 LINE 上按了「聯繫顧問」，麻煩主動聯繫了解他的問題。`,
        { message_thread_id: THREAD.decide });
      return lineReply(env, ev.replyToken, '好的，已經通知顧問了，會盡快跟您聯繫，謝謝您 🙏');
    }
    // 2026-08-14 加：常見問題分層選單——圖文選單「常見問題」按鈕直接是這個
    // postback（不用打字），先選身分再選題目，回答完給一顆「看其他問題」
    // 按鈕繞回同一份清單。
    if (data === 'faq_start') {
      return lineReplyMessages(env, ev.replyToken, [faqRoleFlex()]);
    }
    // 2026-09-02 加：求職者圖文選單「第一次使用流程」按鈕，同一套 postback
    // 手法，跳出教學卡片，不連到網頁。
    if (data === 'howto_start') {
      return lineReplyMessages(env, ev.replyToken, [howtoFlex()]);
    }
    if (data.startsWith('faq_role:')) {
      const role = data.slice('faq_role:'.length);
      return lineReplyMessages(env, ev.replyToken, [faqListFlex(role)]);
    }
    if (data.startsWith('faq_q:')) {
      const [, role, idx] = data.split(':');
      return lineReplyMessages(env, ev.replyToken, faqAnswerMessages(role, idx));
    }
    return;
  }

  if (ev.type !== 'message' || !ev.message || ev.message.type !== 'text') return;
  const userId = ev.source && ev.source.userId;
  const replyToken = ev.replyToken;
  if (!userId || !replyToken) return;
  let text = String(ev.message.text || '').trim();
  const now = nowTaipei();

  let binding = await env.DB.prepare(
    `SELECT * FROM line_bindings WHERE line_user_id = ?`
  ).bind(userId).first();

  // ── 校園招募：加好友連結預填這句話（line.me/R/oaMessage/@930yldgp/?...）──
  // 2026-09-04 加。跟查進度那套 ask_name/ask_email/ask_phone 狀態機是兩件事，
  // 獨立處理、不佔用 line_bindings.state，只借用同一張表的 source 欄位做來源歸因。
  // 三張海報各帶不同字尾（A/B/C），才能比較哪張素材換到比較多人。
  if (Object.hasOwn(LINE_BIM_CAMPUS_TRIGGERS, text)) {
    const campusSource = LINE_BIM_CAMPUS_TRIGGERS[text];
    if (!binding) {
      await env.DB.prepare(
        `INSERT INTO line_bindings (line_user_id, state, source, created_at, updated_at)
         VALUES (?, 'pending_phone', ?, ?, ?)`
      ).bind(userId, campusSource, now, now).run();
    } else if (!binding.source) {
      await env.DB.prepare(
        `UPDATE line_bindings SET source=?, updated_at=? WHERE line_user_id=?`
      ).bind(campusSource, now, userId).run();
    }
    const applyUrl = `https://step1ne.com/apply/?job=bim-engineer-tongluo&title=${encodeURIComponent('BIM工程師')}`
      + `&utm_source=line_campus&utm_medium=line`;
    const msg = `嗨嗨 👋 這是 BIM 工程師的應徵入口 🏗️\n\n`
      + `流程是：\n1️⃣ 填寫應徵表單 📝\n2️⃣ 跟 AI 阿財完成初步面談 🤖\n3️⃣ 顧問審核通過後會進一步聯繫您 📞\n\n`
      + `應徵表單連結 👇\n${applyUrl}`;
    return lineReply(env, replyToken, msg);
  }

  // ── Threads／LinkedIn 貼文＋職缺社群回覆的 LINE 歸因 ──
  // 2026-09-04 加。/go/resolve 轉址時會把 [LC<click_id>] 藏進候選人加好友的
  // 預填訊息尾巴，候選人如果照樣按送出，這則訊息文字裡就帶著這組代碼，從這裡
  // 回頭查 link_clicks 就知道「這個人是被哪一則貼文／哪次回覆帶進來的」。
  // 只要文字裡「有」這個代碼就算數，不要求完全比對——候選人常會刪改預填文字，
  // 前後可能被加了別的字。跟校園招募那組一樣借用 line_bindings.source 記來源，
  // 兩者用不同代碼前綴（campus_ vs lc）不會互相蓋掉。
  const lcMatch = text.match(/\[LC(\d+)\]/);
  if (lcMatch) {
    try {
      // ⚠️ 2026-09-09 修：原本只用 lc.job_slug 直接比對 jobs.slug。真實事故：
      // 11:04 有候選人帶 [LC351] 進來，那筆點擊記的是 pending-co_802bdd6b-3b807a75
      // 這種暫存代號，jobs 表查不到 → 回了「這則貼文的職缺連結有點問題」。
      // job_slug_aliases 早就有這組對應（/go/resolve 已經在查），這裡漏掉了，
      // 補上同一套退回：先直接查，查不到再走別名表。
      const click = await env.DB.prepare(
        `SELECT lc.id, lc.account_id, lc.job_slug, lc.ctx,
                COALESCE(j.title, ja.title) AS job_title,
                COALESCE(j.slug,  ja.slug)  AS resolved_slug,
                COALESCE(j.status, ja.status) AS job_status
           FROM link_clicks lc
           LEFT JOIN jobs j ON j.slug = lc.job_slug
           LEFT JOIN job_slug_aliases a ON a.old_slug = lc.job_slug
           LEFT JOIN jobs ja ON ja.slug = a.new_slug
          WHERE lc.id=?`
      ).bind(Number(lcMatch[1])).first();
      // 後面的應徵連結要用「查得到的那個代號」，不能用貼文上的暫存代號，
      // 不然表單送出去一樣對不到職缺。
      if (click && click.resolved_slug) click.job_slug = click.resolved_slug;
      if (click) {
        const source = `lc${click.id}:${click.account_id}:${click.job_slug}`;
        if (!binding) {
          await env.DB.prepare(
            `INSERT INTO line_bindings (line_user_id, state, source, created_at, updated_at)
             VALUES (?, 'pending_phone', ?, ?, ?)`
          ).bind(userId, source, now, now).run();
        } else if (!binding.source) {
          await env.DB.prepare(
            `UPDATE line_bindings SET source=?, updated_at=? WHERE line_user_id=?`
          ).bind(source, now, userId).run();
        }
        await env.DB.prepare(
          `UPDATE link_clicks SET line_user_id=?, attributed_at=? WHERE id=? AND line_user_id IS NULL`
        ).bind(userId, now, click.id).run();
        // 只在「全新候選人」且點的是真的職缺（不是話題貼文那種借位的 job_slug）
        // 才主動回覆應徵流程；舊候選人或話題貼文一律不打斷，只默默記歸因。
        // ⚠️ 2026-09-09 加：職缺被顧問關閉之後，社群舊貼文還在外面流傳，
        // 人選照樣點得進來。原本完全不看 status，會熱情地推一個已經徵到人的
        // 職缺的應徵連結給他——比不回話還糟。關閉的一律誠實講，並導去列表。
        if (!binding && click.job_title && click.job_status === 'closed') {
          const closed = await env.DB.prepare(
            `SELECT slug, title, company_id, service_line FROM jobs WHERE slug=?`
          ).bind(click.job_slug).first();
          return lineReplyMessages(env, replyToken,
            await closedJobMessages(env, closed || { slug: click.job_slug, title: click.job_title }));
        }
        if (!binding && click.job_title) {
          const applyUrl = `https://step1ne.com/apply/?job=${encodeURIComponent(click.job_slug)}`
            + `&title=${encodeURIComponent(click.job_title)}&utm_source=line_click&utm_medium=line`;
          const msg = `嗨嗨 👋 這是「${click.job_title}」的應徵入口\n\n`
            + `流程是：\n1️⃣ 填寫應徵表單 📝\n2️⃣ 跟 AI 阿財完成初步面談 🤖\n3️⃣ 顧問審核通過後會進一步聯繫您 📞\n\n`
            + `應徵表單連結 👇\n${applyUrl}`;
          return lineReply(env, replyToken, msg);
        }
        // ⚠️ 2026-09-08 修：上面那個 job_title 判斷原本是「查不到職稱就完全
        // 不回話」。真實事故：候選人 18:55 從 pending-co_802bdd6b-9868182d
        // 那篇貼文進來、送出預填訊息，那個 slug 是重複匯入產生的孤兒代號、
        // jobs 表裡查不到，於是系統一個字都沒回，人就這樣晾在那裡。
        // 候選人主動來問而系統靜默是最糟的結果，所以改成分三種情況：
        //   ① 真職缺（查得到 job_title）→ 上面那段，照原本推應徵流程
        //   ② 話題貼文（job_slug 以 💬 開頭，是借位不是真職缺）→ 維持原行為，
        //      不主動回覆、只默默記歸因，不要對著看話題文的人推應徵
        //   ③ 查不到職缺、也不是話題貼文 → 這裡：一定要回一則通用訊息，
        //      把人導去職缺列表，不能卡死
        const isTopicPost = String(click.job_slug || '').startsWith('💬');
        if (!binding && !isTopicPost) {
          const msg = `嗨嗨 👋 感謝您的詢問！\n\n`
            + `這則貼文的職缺連結有點問題，我這邊沒有抓到對應的職缺 🙏\n\n`
            + `您可以直接看目前所有開放中的職缺 👇\nhttps://step1ne.com/jobs/\n\n`
            + `或是直接在這裡回覆您有興趣的職缺名稱，顧問會盡快跟您聯繫 📞`;
          return lineReply(env, replyToken, msg);
        }
        text = text.replace(lcMatch[0], '').trim();
      }
    } catch { /* 歸因失敗不能擋掉候選人原本的對話 */ }
  }

  // ⚠️ 2026-08-17 修：三步驟核對卡在 ask_name/ask_email/ask_phone 時，
  // 這支原本會把候選人接下來「任何」訊息都當成核對答案硬吃——真實案例：
  // 顧問這段時間在用 LINE Official Account Manager 手動跟候選人聊別的事，
  // 候選人隨口回的「你好」「好的」被當成信箱/姓名去驗證，越吃越亂
  // （范博翔、謝仁豪都撞過）。這支完全不知道顧問正在手動接手——LINE 不會
  // 把顧問手動發的訊息回報給這支 webhook，沒有訊號可以偵測「現在是不是
  // 真人在聊」。退而求其次：核對卡住太久沒有下一步動作，很可能代表當下
  // 已經不是候選人在專心走這個流程了（不論是被打斷、還是顧問接手），
  // 直接砍掉這次卡住的核對，讓這則訊息當普通訊息處理、不搶著回——
  // 不能完全擋掉「顧問跟候選人同時在對話」這種真的撞在一起的情況，
  // 但可以擋掉像這兩次「隔了十幾分鐘才回」的多數情況。
  if (binding && ['ask_name', 'ask_email', 'ask_phone'].includes(binding.state)) {
    const STALE_MS = 3 * 60 * 1000;
    const staleFor = binding.updated_at
      ? new Date(now.replace(' ', 'T')) - new Date(binding.updated_at.replace(' ', 'T'))
      : 0;
    if (staleFor > STALE_MS) {
      await env.DB.prepare(`DELETE FROM line_bindings WHERE line_user_id = ?`).bind(userId).run();
      // 砍掉之後當作「從沒查過」處理——如果這則剛好就是觸發字，
      // 應該讓他乾淨重新開始一次，不是連這次也吃掉不回應。
      binding = null;
    }
  }

  // 第一次出現：只在候選人主動觸發（按鈕／打字）時才介入，
  // 其他訊息不搶著回——這支只負責「查進度」這一件事，不要變成搶走
  // Jacky 之後可能在 LINE Official Account Manager 設的其他自動回覆的地盤。
  if (!binding) {
    if (text !== LINE_PROGRESS_TRIGGER) return;
    await env.DB.prepare(
      `INSERT INTO line_bindings (line_user_id, state, created_at, updated_at)
       VALUES (?, 'ask_name', ?, ?)`
    ).bind(userId, now, now).run();
    return lineReply(env, replyToken, LINE_ASK_NAME_TEXT);
  }

  if (binding.state === 'bound') {
    if (text !== LINE_PROGRESS_TRIGGER) return; // 已綁定的人閒聊不接手

    // ⚠️ 2026-08-13 補：application_ids 原本只在「第一次綁定」那一刻掃過
    // 手機號碼算好、之後存死不再更新——如果他後來又應徵了別的職缺，
    // 不會自動出現在這裡。改成每次查詢都順手重掃一次手機號碼，
    // 有新的應徵記錄就併進去，讓他隨時查都是最新的，不用重新走一次綁定流程。
    let ids = safeJsonArray(binding.application_ids);
    if (binding.phone) {
      const norm = normalizePhone(binding.phone);
      const { results } = await env.DB.prepare(
        `SELECT id FROM applications
          WHERE superseded_by IS NULL AND phone IS NOT NULL AND phone != ''`
      ).all();
      const matched = (results || []).filter((r) => normalizePhone(r.phone) === norm).map((r) => r.id);
      const merged = Array.from(new Set([...ids, ...matched]));
      if (merged.length !== ids.length) {
        ids = merged;
        await env.DB.prepare(
          `UPDATE line_bindings SET application_ids=?, updated_at=? WHERE line_user_id=?`
        ).bind(JSON.stringify(ids), now, userId).run();
      }
    }

    const msgs = await buildProgressMessages(env, ids);
    return lineReplyMessages(env, replyToken, msgs);
  }

  // 三步驟驗證：姓名 → 信箱 → 手機。一步一則，不是一次打完——
  // 對話節奏比較像真人客服，漏打一項也只要補那一項，不用整段重打。
  if (text === LINE_PROGRESS_TRIGGER) {
    // 已經在流程裡了，把「現在卡在哪一步」的提示再說一次，不用另外講「你已經問過了」
    const resend = { ask_name: LINE_ASK_NAME_TEXT, ask_email: LINE_ASK_EMAIL_TEXT, ask_phone: LINE_ASK_PHONE_TEXT };
    return lineReply(env, replyToken, resend[binding.state] || LINE_ASK_NAME_TEXT);
  }

  if (binding.state === 'ask_name') {
    if (!text) return lineReply(env, replyToken, LINE_ASK_NAME_TEXT);
    await env.DB.prepare(
      `UPDATE line_bindings SET pending_name=?, state='ask_email', updated_at=? WHERE line_user_id=?`
    ).bind(text, now, userId).run();
    return lineReply(env, replyToken, LINE_ASK_EMAIL_TEXT);
  }

  if (binding.state === 'ask_email') {
    if (!text.includes('@')) return lineReply(env, replyToken, LINE_BAD_EMAIL_TEXT);
    await env.DB.prepare(
      `UPDATE line_bindings SET pending_email=?, state='ask_phone', updated_at=? WHERE line_user_id=?`
    ).bind(text, now, userId).run();
    return lineReply(env, replyToken, LINE_ASK_PHONE_TEXT);
  }

  if (binding.state === 'ask_phone') {
    const norm = normalizePhone(text);
    if (norm.length < 8) return lineReply(env, replyToken, LINE_BAD_PHONE_TEXT);

    const nameNorm = normalizeNameForMatch(binding.pending_name);
    const emailNorm = String(binding.pending_email || '').trim().toLowerCase();

    // 應徵量對這間公司的規模來說不大，直接掃全表在 JS 裡正規化比對，
    // 比在 SQL 裡處理各種手機號碼格式（有無 -、+886）簡單也不容易漏比對。
    const { results } = await env.DB.prepare(
      `SELECT id, name, phone, email FROM applications
        WHERE superseded_by IS NULL AND phone IS NOT NULL AND phone != '' AND email IS NOT NULL AND email != ''`
    ).all();
    const matched = (results || []).filter((r) =>
      normalizePhone(r.phone) === norm
      && (r.email || '').trim().toLowerCase() === emailNorm
      && normalizeNameForMatch(r.name) === nameNorm);

    if (!matched.length) {
      // 三項對不起來——不知道是哪一項錯，整段重來比只重問一項可靠
      await env.DB.prepare(
        `UPDATE line_bindings SET state='ask_name', pending_name=NULL, pending_email=NULL, updated_at=?
          WHERE line_user_id=?`
      ).bind(now, userId).run();
      return lineReply(env, replyToken, LINE_NO_MATCH_TEXT);
    }

    const ids = matched.map((m) => m.id);
    await env.DB.prepare(
      `UPDATE line_bindings SET state='bound', phone=?, email=?, application_ids=?, bound_at=?, updated_at=?,
              pending_name=NULL, pending_email=NULL
        WHERE line_user_id = ?`
    ).bind(text, binding.pending_email, JSON.stringify(ids), now, now, userId).run();

    // 配對紀錄要讓顧問在後台看得到，這裡先推一則 Telegram 通知——
    // 跟「顧問人選回報區」不是同一件事，放系統回報主題就好，不用麻煩顧問回應。
    await notify(env,
      `🔗 LINE 查詢進度：${matched[0].name} 完成三項驗證綁定（${matched.length} 筆應徵紀錄）`,
      { message_thread_id: THREAD.system });

    // ⚠️ 2026-08-13 補：原本綁定成功直接跳進度卡，候選人這邊沒有任何
    // 「你已經綁定成功」的明講——Jacky 自己測試時就覺得像什麼都沒發生
    // （顧問那邊靠 Telegram 通知知道，候選人這邊完全沒有對應的訊息）。
    const bindOk = { type: 'text',
      text: `✅ 綁定成功！以後您可以隨時點選單「追蹤面試進度」查詢，不用再重新輸入資料。\n\n以下是您目前的進度：` };
    const msgs = await buildProgressMessages(env, ids);
    return lineReplyMessages(env, replyToken, [bindOk, ...msgs]);
  }

  // ⚠️ 2026-08-13 事故：這支流程改版過（原本一次問完三項，改成一步一問），
  // 改版前建立的綁定紀錄留著舊的 state 值（例如 'pending_phone'），
  // 沒有任何分支認得那個值，訊息就這樣被吃掉、候選人已讀不回——
  // Jacky 自己測試時就撞到。任何不認得的 state 一律當成「重新開始」，
  // 不要讓 LINE OA 對候選人沉默，那比走錯流程還糟。
  await env.DB.prepare(
    `UPDATE line_bindings SET state='ask_name', pending_name=NULL, pending_email=NULL, updated_at=?
      WHERE line_user_id=?`
  ).bind(now, userId).run();
  return lineReply(env, replyToken, LINE_ASK_NAME_TEXT);
}

// 待審核處置的共用邏輯：/admin/screen-decide（網頁後台）跟 /telegram/webhook
// （Telegram 按鈕）都呼叫這支，不要各寫一份，不然兩邊會慢慢長出不同的行為。
async function applyScreenDecision(env, applicationId, decision, opts = {}) {
  const app = await env.DB.prepare(
    `SELECT id, name, email, job_title, job_slug, chat_token FROM applications WHERE id = ?`
  ).bind(applicationId || '').first();
  if (!app) return { ok: false, error: '找不到這筆應徵', status: 404 };

  const now = nowTaipei();

  if (decision === 'approved') {
    // ⚠️ 核准之後也要過容量閘門，不能直接發面談連結。
    // 2026-08-07 顧問指出的漏洞：原本核准就直接寄面談室連結，
    // 等於繞過分流——顧問一次核准 5 個人，5 個人同時衝進阿財，
    // 那分流做了等於沒做（而且這批還是「顧問已經認可」的人，更不該被塞在一起）。
    const load = await liveLoad(env);
    const roomy = load.active + load.pending < LIVE_LIMIT;

    await env.DB.prepare(
      `UPDATE applications SET screen_decision='approved', screen_decided_at=?,
              screen_note=?, status=? WHERE id=?`
    ).bind(now, opts.note || null, roomy ? 'ready' : 'awaiting_booking', app.id).run();

    await sendMail(
      env, app.email,
      `面談安排通知：${app.job_title || app.job_slug}`,
      roomy
        ? [`${app.name} 您好，`,
           `您應徵的「${app.job_title || app.job_slug}」，資料評估後想進一步了解您的狀況。`,
           `下面的連結是您的專屬面談室，AI 面談顧問阿財會與您進行約 20 到 30 分鐘的正式面談，`,
           `結束後獵頭顧問會評估是否推薦給用人單位面試。`,
           `請把這封信留著——中途離開的話，用同一個連結就能回到原本的對話。`]
        : [`${app.name} 您好，`,
           `您應徵的「${app.job_title || app.job_slug}」，資料評估後想進一步了解您的狀況。`,
           `目前面談室有其他候選人正在進行中，請用下面的連結選一個方便的時段，`,
           `時間到之前會提醒您。請把這封信留著。`],
      roomy
        ? { url: `https://step1ne.com/interview/?t=${app.chat_token}`, text: '開始面談' }
        : { url: `https://step1ne.com/book/?t=${app.chat_token}`, text: '選擇面談時段' }
    );
    return { ok: true, app, routed: roomy ? 'now' : 'book' };
  }

  if (decision === 'redirect') {
    const target = await env.DB.prepare(
      `SELECT slug, title FROM jobs WHERE slug = ?`
    ).bind(opts.redirectJobSlug || '').first();
    if (!target) return { ok: false, error: '找不到要推薦的職缺', status: 404 };
    await env.DB.prepare(
      `UPDATE applications SET screen_decision='redirect', screen_decided_at=?,
              screen_note=?, redirect_job_slug=?, status='redirected' WHERE id=?`
    ).bind(now, opts.note || null, target.slug, app.id).run();
    const applyUrl = `https://step1ne.com/apply/?job=${encodeURIComponent(target.slug)}` +
      `&title=${encodeURIComponent(target.title)}&utm_source=email&utm_medium=redirect`;
    await sendMail(
      env, app.email,
      `為您推薦更適合的機會：${target.title}`,
      [`${app.name} 您好，`,
       `感謝您應徵「${app.job_title || app.job_slug}」。看過您的資料後，`,
       `覺得「${target.title}」這個機會可能更適合您目前的狀況。`,
       `有興趣的話歡迎透過下面的連結應徵，會優先為您安排。`],
      { url: applyUrl, text: `應徵「${target.title}」` }
    );
    return { ok: true, app, target };
  }

  if (decision === 'declined') {
    // 不在這裡立刻寄信——3 小時後由 scheduled() 排程統一寄出委婉信，
    // 讓候選人感覺是資料被認真看過，不是送出後秒拒。
    await env.DB.prepare(
      `UPDATE applications SET screen_decision='declined', screen_decided_at=?,
              screen_note=?, status='rejected' WHERE id=?`
    ).bind(now, opts.note || null, app.id).run();
    return { ok: true, app };
  }

  return { ok: false, error: '不認得的處置：' + decision, status: 400 };
}

// ── 面談容量與時段 ──
//
// 2026-08-07 加。在此之前完全沒有分流：候選人自己在表單上點「現在談」或「約時間」，
// 系統一眼都不看阿財目前的負載。20 個職缺共用同一個阿財，
// 一支廣告一衝，護理師、遊戲客服的應徵者會跟 BIM 的人一起卡住。
//
// 名額怎麼定：阿財的 MAX_PARALLEL=3（同時最多生成 3 則回覆），一場 40–60 分鐘。
// 所以一個 30 分鐘的時段開 2 個名額，讓相鄰時段的面談重疊時仍有餘裕。
const SLOT_CAPACITY = 2;      // 每個 30 分鐘時段的名額
const LIVE_LIMIT = 3;         // 同時進行中的面談超過這個數，就不再給「現在就談」
const SLOT_START_HOUR = 10;   // 每天最早的時段
const SLOT_END_HOUR = 21;     // 每天最晚的時段（21:30 是最後一個）
const SLOT_DAYS = 7;          // 往後開放幾天

function tpeNow() {
  return new Date(Date.now() + 8 * 3600 * 1000);
}

function pad(n) { return String(n).padStart(2, '0'); }

/** 目前有幾場面談真的在跑（含已發連結但還沒進場的，那些人隨時會走進來） */
async function liveLoad(env) {
  const r = await env.DB.prepare(
    `SELECT
       SUM(CASE WHEN interview_state='active' THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN interview_state='not_started' AND status='ready'
                 AND created_at >= datetime('now','+8 hours','-45 minutes') THEN 1 ELSE 0 END) AS pending
     FROM applications`
  ).first();
  return { active: r?.active || 0, pending: r?.pending || 0 };
}

/** 未來 SLOT_DAYS 天的時段，附上剩餘名額；額滿的不回傳 */
async function openSlots(env) {
  const { results } = await env.DB.prepare(
    `SELECT slot_at, COUNT(*) n FROM interview_bookings
      WHERE cancelled_at IS NULL AND slot_at >= datetime('now','+8 hours')
      GROUP BY slot_at`
  ).all();
  const booked = Object.fromEntries((results || []).map((r) => [r.slot_at, r.n]));

  const now = tpeNow();
  const out = [];
  for (let d = 0; d < SLOT_DAYS; d++) {
    const day = new Date(now.getTime() + d * 86400000);
    const ymd = `${day.getUTCFullYear()}-${pad(day.getUTCMonth() + 1)}-${pad(day.getUTCDate())}`;
    for (let h = SLOT_START_HOUR; h <= SLOT_END_HOUR; h++) {
      for (const m of [0, 30]) {
        const key = `${ymd} ${pad(h)}:${pad(m)}`;
        // 至少要在 30 分鐘後，不然等於「現在」，那條路已經有即時面談了
        const slotMs = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h, m);
        if (slotMs < now.getTime() + 30 * 60000) continue;
        const left = SLOT_CAPACITY - (booked[key] || 0);
        if (left > 0) out.push({ slot_at: key, left });
      }
    }
  }
  return out;
}

// 2026-08-13 加：立即面談／預約的容量判斷，原本只在測驗交卷
// （POST /assessment/:token）算一次，之後「送出應徵」（POST /apply/update/:token）
// 直接沿用那個舊結果——但填表審核資料這段期間可能過了好幾分鐘，房間可能已經
// 被填滿或空出來，人選看到的按鈕會跟當下真的容量不符。抽成共用函式，兩個端點
// 都呼叫、每次都用「這一刻」重新判斷，不吃舊快取。
//
// 顧問審核（pending_screen）不在這裡處理——那條路完全交給 /admin/screen-decide，
// 這裡遇到就原樣回報 route:'screening'，不重複通知顧問也不動它的狀態。
//
// ready_notified_at 擋重複寄信：不管在哪個端點被判定成「立即面談」，
// 面談連結備援信只寄一次，靠這個欄位記錄「已經寄過」。
//
// app 需要的欄位：id, name, email, job_slug, job_title, job_full_title,
// status, chat_token, remind_at, interview_state, ready_notified_at。
async function decideRouteFresh(env, app) {
  if (app.status === 'pending_screen') {
    return { route: 'screening' };
  }

  // 已經有約定時間的人，重新判斷不等於現在就要談。
  if (app.interview_state !== 'active' && app.status === 'scheduled' && app.remind_at) {
    const stillFuture = await env.DB.prepare(
      `SELECT 1 AS f WHERE datetime(?) > datetime('now','+8 hours')`
    ).bind(app.remind_at).first();
    if (stillFuture) {
      return { route: 'scheduled', remind_at: app.remind_at, chat_token: app.chat_token };
    }
  }

  const load = await liveLoad(env);
  if (load.active + load.pending < LIVE_LIMIT) {
    if (app.status !== 'ready') {
      await env.DB.prepare(`UPDATE applications SET status='ready', interview_mode='now' WHERE id=?`)
        .bind(app.id).run();
    }
    // ⚠️ 這條路（立即面談）原本完全沒有備援信，人選只能靠畫面上那顆按鈕，
    // 關掉視窗或分頁弄丟就找不回連結了。跟「預約成功」那封信一樣的邏輯，
    // 補一封帶連結的信——但只寄一次，用 ready_notified_at 擋重複。
    // 失敗不擋流程：寄信是加值功能，不能因為信箱掛了讓他進不了面談室。
    if (!app.ready_notified_at && app.email && app.chat_token) {
      try {
        await sendMail(
          env, app.email,
          `面談連結：${app.job_full_title || app.job_title || app.job_slug}`,
          [`${app.name} 您好，`,
           `工作風格測驗已經收到，目前可以直接開始面談。`,
           `如果現在不方便，這封信可以先留著，準備好了再點下面的連結進入面談室即可。`],
          { url: `https://step1ne.com/interview/?t=${app.chat_token}`, text: '進入面談室' }
        );
        await env.DB.prepare(`UPDATE applications SET ready_notified_at=? WHERE id=?`)
          .bind(nowTaipei(), app.id).run();
      } catch { /* 寄信失敗不影響進面談室 */ }
    }
    return { route: 'now', chat_token: app.chat_token, slots: await openSlots(env) };
  }

  // 額滿 → 只能預約。誠實告訴他現在有幾個人在談，不要只說「請稍後」
  if (app.status !== 'awaiting_booking' && app.status !== 'scheduled') {
    await env.DB.prepare(`UPDATE applications SET status='awaiting_booking' WHERE id=?`)
      .bind(app.id).run();
  }
  return { route: 'book', busy: load.active + load.pending, slots: await openSlots(env) };
}

// 2026-08-14 加：阿福（健檢）也要有跟阿財一樣的容量分流＋預約機制，不然
// checkup_daemon.py 的 MAX_PARALLEL=2 只是後端悄悄排隊，前台完全沒告訴本人
// 「現在要等」，體驗上就是卡住沒反應。做法、命名、判斷順序都照抄上面
// applications 那一整套，只換成 checkups／checkup_bookings，兩邊完全獨立
// 的資料表，不共用一格。
const CHECKUP_LIVE_LIMIT = 2;      // 跟 checkup_daemon.py 的 MAX_PARALLEL 對齊
const CHECKUP_SLOT_CAPACITY = 2;   // 每個 30 分鐘時段的名額

async function checkupLiveLoad(env) {
  const r = await env.DB.prepare(
    `SELECT
       SUM(CASE WHEN chat_state='active' THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN chat_state='not_started' AND status='ready'
                 AND created_at >= datetime('now','+8 hours','-45 minutes') THEN 1 ELSE 0 END) AS pending
     FROM checkups`
  ).first();
  return { active: r?.active || 0, pending: r?.pending || 0 };
}

async function checkupOpenSlots(env) {
  const { results } = await env.DB.prepare(
    `SELECT slot_at, COUNT(*) n FROM checkup_bookings
      WHERE cancelled_at IS NULL AND slot_at >= datetime('now','+8 hours')
      GROUP BY slot_at`
  ).all();
  const booked = Object.fromEntries((results || []).map((r) => [r.slot_at, r.n]));

  const now = tpeNow();
  const out = [];
  for (let d = 0; d < SLOT_DAYS; d++) {
    const day = new Date(now.getTime() + d * 86400000);
    const ymd = `${day.getUTCFullYear()}-${pad(day.getUTCMonth() + 1)}-${pad(day.getUTCDate())}`;
    for (let h = SLOT_START_HOUR; h <= SLOT_END_HOUR; h++) {
      for (const m of [0, 30]) {
        const key = `${ymd} ${pad(h)}:${pad(m)}`;
        const slotMs = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h, m);
        if (slotMs < now.getTime() + 30 * 60000) continue;
        const left = CHECKUP_SLOT_CAPACITY - (booked[key] || 0);
        if (left > 0) out.push({ slot_at: key, left });
      }
    }
  }
  return out;
}

// c 需要的欄位：id, name, email, chat_token, remind_at, chat_state, status, ready_notified_at
async function decideCheckupRouteFresh(env, c) {
  if (c.chat_state !== 'active' && c.status === 'scheduled' && c.remind_at) {
    const stillFuture = await env.DB.prepare(
      `SELECT 1 AS f WHERE datetime(?) > datetime('now','+8 hours')`
    ).bind(c.remind_at).first();
    if (stillFuture) {
      return { route: 'scheduled', remind_at: c.remind_at, chat_token: c.chat_token };
    }
  }

  const load = await checkupLiveLoad(env);
  if (load.active + load.pending < CHECKUP_LIVE_LIMIT) {
    if (c.status !== 'ready') {
      await env.DB.prepare(`UPDATE checkups SET status='ready' WHERE id=?`).bind(c.id).run();
    }
    if (!c.ready_notified_at && c.email && c.chat_token) {
      try {
        await sendMail(
          env, c.email, `健檢對談連結已經可以使用`,
          [`${c.name} 您好，`,
           `工作風格測驗已經收到，目前可以直接開始跟阿福的健檢對談。`,
           `如果現在不方便，這封信可以先留著，準備好了再點下面的連結進入對談室即可。`],
          { url: `https://step1ne.com/checkup-chat/?t=${c.chat_token}`, text: '進入健檢對談室' }
        );
        await env.DB.prepare(`UPDATE checkups SET ready_notified_at=? WHERE id=?`)
          .bind(nowTaipei(), c.id).run();
      } catch { /* 寄信失敗不影響進對談室 */ }
    }
    return { route: 'now', chat_token: c.chat_token, slots: await checkupOpenSlots(env) };
  }

  if (c.status !== 'awaiting_booking' && c.status !== 'scheduled') {
    await env.DB.prepare(`UPDATE checkups SET status='awaiting_booking' WHERE id=?`).bind(c.id).run();
  }
  return { route: 'book', busy: load.active + load.pending, slots: await checkupOpenSlots(env) };
}


// 2026-09-14 加：Step1ne 週文章草稿審核（art_approve／art_skip）。跟
// soc_approve 不同的地方——這裡按確認**不會真的發文**，Worker 沒有本機
// git 環境可以建頁面/push/deploy。這裡只負責記「人已經核准了」，實際套用
// 上線靠本機 article_publish_tick.py 輪詢 status='approved' 的列去做，
// 那支才有 repo 可以動、才能跑 git push。兩段分開是因為發布這個動作本質上
// 只能在本機做，Worker 這層只是核准的窗口。
async function handleArticleAction(env, cq) {
  const [action, idRaw] = String(cq.data).split(':');
  const id = Number(idRaw);
  const answer = async (text, alert) => {
    await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ callback_query_id: cq.id, text, show_alert: !!alert }),
    }).catch(() => {});
  };
  const row = await env.DB.prepare(
    `SELECT id, title, status FROM article_drafts WHERE id = ?`
  ).bind(id).first();
  if (!row) { await answer('❌ 找不到這篇草稿（可能太舊或已被清除）'); return; }
  const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';

  const editButtons = async (label) => {
    await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        chat_id: cq.message.chat.id, message_id: cq.message.message_id,
        reply_markup: { inline_keyboard: [[{ text: label, callback_data: 'noop' }]] },
      }),
    }).catch(() => {});
  };

  if (action === 'art_approve') {
    if (row.status === 'published') { await answer('這篇已經上線了，不用再按'); return; }
    await env.DB.prepare(
      `UPDATE article_drafts SET status='approved', approved_at=datetime('now','+8 hours'), approved_by=? WHERE id=?`
    ).bind(who, id).run();
    await answer('✅ 已核准，稍後本機排程會實際套用上線（幾分鐘內），完成會再通知');
    await editButtons(`✅ ${who} 已核准，等待上線中`);
  } else if (action === 'art_skip') {
    await env.DB.prepare(`UPDATE article_drafts SET status='skipped' WHERE id=?`).bind(id).run();
    await answer('已標記不發這篇');
    await editButtons(`❌ ${who} 選擇不發這篇`);
  } else {
    await answer('未知的操作');
  }
}

// 2026-09-11 抽出來：soc_approve/soc_skip/soc_regen 原本直接寫在 webhook 收到
// callback_query 那條路徑裡，只能被真的 Telegram 按鈕點擊觸發。排程要做到
// 「先核准、時間到才真的發文」，得讓同一段發文邏輯也能被 cron 用重建出來的
// 假 cq 觸發——內容完全不動，只是包成函式讓兩條路都能叫用。
async function handleSocAction(env, cq) {
        const [action, qidRaw] = String(cq.data).split(':');
        const qid = Number(qidRaw);
        const answer = async (text, alert) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text, show_alert: !!alert }),
          }).catch(() => {});
        };
        // ⚠️ 2026-08-18 改：原本「一個職缺對應一個發文狀態」直接存在 jobs 表
        // 的單一欄位上，同一職缺換一個顧問排就會直接覆蓋前一個人排的——
        // 真實案例撞到（顧問要讓四個人各自對同一個職缺發一篇）。改成一個
        // 職缺可以對應多筆 social_post_queue 排隊紀錄，每筆各自獨立，
        // 按鈕直接認排隊紀錄的 id，不會再互相蓋掉。
        // ⚠️ 2026-09-03 改 LEFT JOIN：話題類型的排隊紀錄 job_slug 存的是
        // 「💬 話題描述」文字，不是真職缺 slug，INNER JOIN 查不到會讓這一整段
        // approve/regen/skip 全部誤判成「找不到這筆排隊紀錄」，三顆按鈕都按不動。
        // COALESCE 讓 row.title 對職缺類型／話題類型都能正常顯示，不用動下面任何一處。
        const row = await env.DB.prepare(
          `SELECT q.id, q.job_slug, q.account_id, q.status, q.draft, q.requested_at, q.topic_id, q.scheduled_at,
                  COALESCE(j.title, q.job_slug) AS title,
                  COALESCE(sa.force_link_on_posts, 0) AS force_link_on_posts
             FROM social_post_queue q LEFT JOIN jobs j ON j.slug = q.job_slug
             LEFT JOIN social_accounts sa ON sa.id = q.account_id WHERE q.id = ?`
        ).bind(qid).first();
        if (!row) { await answer('❌ 找不到這筆排隊紀錄（可能太舊或已被清除）'); return; }
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';

        if (action === 'soc_approve') {
          if (!row.draft) { await answer('❌ 這則沒有草稿內容，沒辦法發文'); return; }

          // 2026-09-11 加：排程貼文「先核准、時間到才真的發」——顧問按確認
          // 這一刻，如果排定時間還沒到，不要往下跑發文邏輯，先記住「已核准」
          // 這個決定，時間到了由排程（見下方 scheduled()）重建這個 cq 再跑
          // 同一套邏輯。
          // ⚠️ 2026-09-11 修：真實事故——原本用「row.status !== 'approved_scheduled'」
          // 判斷要不要檢查時間，結果顧問對同一則按第二次確認（或 Telegram 重送
          // 同一個 callback_query，這是它已知的行為），這時 status 已經是
          // approved_scheduled，直接跳過時間檢查、立刻真的發文——排定 14:55，
          // 14:49 就發出去了。改成永遠檢查「現在有沒有超過排定時間」，不管
          // 目前狀態是什麼；排程觸發時本來就是時間真的到了才會呼叫，這個檢查
          // 自然會是 false、直接放行，不需要靠狀態去特案判斷。
          if (row.scheduled_at) {
            const schedMs = Date.parse(String(row.scheduled_at).replace(' ', 'T') + '+08:00');
            if (!Number.isNaN(schedMs) && schedMs > Date.now()) {
              await env.DB.prepare(
                `UPDATE social_post_queue SET status='approved_scheduled', approved_at=?, approved_by=?,
                        tg_message_id=?, tg_thread_id=? WHERE id=?`
              ).bind(nowTaipei(), who, String(cq.message.message_id),
                     cq.message.message_thread_id ? String(cq.message.message_thread_id) : null, qid).run();
              await answer(`✅ 已核准，會在 ${row.scheduled_at} 準時發布，不用再按`, true);
              await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
                method: 'POST', headers: { 'content-type': 'application/json' },
                body: JSON.stringify({
                  chat_id: cq.message.chat.id, message_id: cq.message.message_id,
                  reply_markup: { inline_keyboard: [[{ text: `✅ ${who} 已核准，會在 ${row.scheduled_at} 準時發布`, callback_data: 'noop' }]] },
                }),
              }).catch(() => {});
              return;
            }
          }

          // 🚨 2026-08-27 加：草稿放太久就不准直接發。
          // 職缺內容會變——BIM 的薪資 8/27 改成面議之後，8/21 產的那批草稿裡
          // 還完整寫著「月薪 40,833–50,167（已含 2 個月年終攤提）」；
          // 那時的按鈕還躺在 Telegram 裡，按下去就把已經撤下的內容公開發出去。
          // 實測 45 筆待確認草稿有 20 筆含現在不能講的內容。
          // 重產一次會重跑現行的所有過濾，比在這裡逐項列黑名單可靠。
          const ageDays = row.requested_at
            ? Math.floor((Date.now() - Date.parse(String(row.requested_at).replace(' ', 'T') + '+08:00')) / 86400000)
            : 0;
          if (ageDays >= 3) {
            await env.DB.prepare(`UPDATE social_post_queue SET status='expired' WHERE id=?`).bind(row.id).run();
            await answer(`❌ 這則草稿是 ${ageDays} 天前產的，職缺內容可能已經改過（薪資、客戶名稱等），不能直接發。請按「🔄 重新產一次」。`, true);
            return;
          }

          // 🚨 客戶名稱稽核。產稿時已經擋過一次，但草稿是存下來的，
          // 而客戶名單會新增（今天就補了台灣美光與帆宣兩筆）——
          // 發出去那一刻用最新的名單再掃一次才算數。
          const terms = await clientNameTerms(env);
          const nameHits = hitsClientNames(row.draft, terms);
          if (nameHits.length) {
            await env.DB.prepare(`UPDATE social_post_queue SET status='blocked_compliance' WHERE id=?`).bind(row.id).run();
            await answer(`🚫 這則出現客戶公司名稱「${nameHits.join('」「')}」，社群一律不提客戶名，已擋下。請按「🔄 重新產一次」。`, true);
            return;
          }

          // 🚨 草稿沒填完就不准發。這是公開貼文，發出去才發現要自己去刪。
          const ph = placeholderHits(row.draft);
          if (ph.length) {
            await answer(`❌ 這則草稿裡還有「${ph.join('」「')}」，沒填完不能發`, true);
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: cq.message.chat.id,
                ...(cq.message.message_thread_id ? { message_thread_id: cq.message.message_thread_id } : {}),
                reply_to_message_id: cq.message.message_id,
                text: `🚨 這則沒有發出去——草稿裡還有「${ph.join('」「')}」\n\n`
                  + `這通常代表職缺資料缺了東西，模型就用佔位符先頂著。\n`
                  + `請先把該職缺的欄位補齊，再按「🔄 重新產一次」。`,
              }),
            }).catch(() => {});
            return;
          }

          // ⚠️ 2026-08-17 修：真實案例抓到同一則職缺連續發兩篇一模一樣的文——
          // 這支發文流程每則貼文都要 sleep(5000) 等 container 就緒，一次審核
          // 常常要跑 10~20 秒才回得了 Telegram，Telegram 覺得太久沒回應就會
          // 重送同一個 callback_query，這支又沒有防重複機制，整套流程就跑兩次。
          // 修法：進來第一件事就搶鎖（UPDATE ... WHERE status 不是
          // posting/posted 才會生效），搶不到鎖代表已經在發或發過了，
          // 直接短路擋掉，不再跑一次 Threads API。
          const claim = await env.DB.prepare(
            `UPDATE social_post_queue SET status='posting', posting_at=datetime('now','+8 hours') WHERE id=? AND (status IS NULL OR status NOT IN ('posting','posted'))`
          ).bind(qid).run();
          if (!claim.meta || !claim.meta.changes) {
            await answer(row.status === 'posted' ? '✅ 這則已經發過了，沒有重複發' : '⏳ 正在發文中，請稍等，不要重複按', true);
            return;
          }
          // 搶到鎖之後先回應 Telegram，避免它因為接下來 Threads API 那段
          // 太慢而判定沒回應、又重送一次同一個按鈕事件。
          await answer('⏳ 收到，發文中…');

          // 2026-08-14 加：多顧問各自帳號——這則排隊紀錄如果在「一鍵發文」
          // 頁面指定過帳號，就用 social_accounts 存的那組金鑰；沒指定的
          // （例如排程自動掃到的舊職缺）退回 wrangler secret 那組（目前就是
          // Jacky 自己的帳號），行為跟改之前一樣，不會突然發不出去。
          let threadsToken = env.THREADS_ACCESS_TOKEN, threadsUserId = env.THREADS_USER_ID;
          let platform = 'threads';   // 沒指定帳號的舊職缺一律當 Threads
          let accLabel = '';
          // 2026-08-18 加：串文最後那則「應徵了解窗口」原本寫死同一個 LINE 連結，
          // 四位顧問各自發文卻都導去同一個人身上，候選人的來源就分不清是誰帶來的。
          // 改成每個帳號各自的連結；沒指定帳號的舊職缺退回 Jacky 那組（跟金鑰退回邏輯一致）。
          let lineLink = 'https://lin.ee/RR4nQqm';
          if (row.account_id) {
            const acc = await env.DB.prepare(
              `SELECT access_token, platform_user_id, line_link, platform, label FROM social_accounts WHERE id = ? AND is_active = 1`
            ).bind(row.account_id).first();
            if (!acc) { await answer('❌ 指定的發文帳號找不到或已停用'); return; }
            threadsToken = acc.access_token; threadsUserId = acc.platform_user_id;
            accLabel = acc.label || '';
            if (acc.line_link) lineLink = acc.line_link;
            platform = acc.platform || 'threads';
          }
          // ── LINE 社群：不自動發，產好稿交給人手動貼 ──
          // 2026-08-21 Jacky 要求。LINE 社群沒有可以自動發文的 API
          //（官方帳號的推播 API 是推給好友，不是發到社群），
          // 所以這個管道的價值不在自動化，是在「版型固定、內容產好」——
          // 顧問收到就直接複製貼上，不用每次自己重寫一遍。
          // 按核准只代表「這篇可以用了」，不代表已經發出去。
          if (platform === 'line_community') {
            await env.DB.prepare(
              `UPDATE social_post_queue SET status='ready_manual', posted_at=datetime('now','+8 hours') WHERE id=?`
            ).bind(qid).run();
            await answer('✅ 已產好，複製下面那則貼到 LINE 社群');
            // ⚠️ 2026-09-11 修：原本寫死送去 THREAD.decide（面試通知確認，是
            // 候選人決策用的主題，跟社群發文完全無關），顧問在那邊根本找不到。
            // 改成回在「按確認」那個按鈕原本所在的同一個聊天室、同一個主題——
            // 跟 Threads/LinkedIn 發布成功那則訊息的做法一致，不用另外猜地方。
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: cq.message.chat.id,
                ...(cq.message.message_thread_id ? { message_thread_id: cq.message.message_thread_id } : {}),
                reply_to_message_id: cq.message.message_id,
                text: `📋 LINE 社群貼文已產好（${accLabel || '手動'}）\n` +
                  `職缺：${row.job_slug}\n\n` +
                  `⚠️ 這一則不會自動發出去，請自己複製貼到社群：\n` +
                  `━━━━━━━━━━━━\n${row.draft}\n━━━━━━━━━━━━`,
              }),
            }).catch(() => {});
            return;
          }

          if (!threadsToken || !threadsUserId) {
            await answer('⚠️ Threads 金鑰還沒設定，先標記核准，不會真的發出去。', true);
            await env.DB.prepare(`UPDATE social_post_queue SET status='approved' WHERE id=?`).bind(qid).run();
            return;
          }
          try {
            // ── LinkedIn ──
            // 2026-08-19 加。LinkedIn 跟 Threads 差在三件事，所以不共用同一段：
            //   ① 單則上限 3000 字，這個長度的招募文完全放得下，不用切串文
            //   ② 沒有「回覆自己」的串接概念，窗口連結直接接在本文最後
            //   ③ 發文是一次呼叫，沒有 Threads 那種「先建 container 再 publish」
            if (platform === 'linkedin') {
              // ⚠️ 一定要帶 q=<queue_id>：只有 c（顧問）+ j（職缺）的話，同一個人
              // 發同一個缺發過多次時，/go/resolve 只能猜「最新那一篇」，舊貼文
              // 帶來的點擊會全部被算到新貼文頭上。總點擊數是準的，但各篇排名不準。
              const goLink = `https://step1ne.com/go/?c=${row.account_id}&j=${encodeURIComponent(row.job_slug)}&q=${qid}`;
              const body = `${row.draft}\n\n▪️ 應徵了解窗口：\n${goLink}`;
              const pr = await fetch('https://api.linkedin.com/v2/ugcPosts', {
                method: 'POST',
                headers: {
                  authorization: `Bearer ${threadsToken}`,
                  'content-type': 'application/json',
                  'x-restli-protocol-version': '2.0.0',
                },
                body: JSON.stringify({
                  author: `urn:li:person:${threadsUserId}`,
                  lifecycleState: 'PUBLISHED',
                  specificContent: {
                    'com.linkedin.ugc.ShareContent': {
                      shareCommentary: { text: body.slice(0, 2900) },
                      shareMediaCategory: 'NONE',
                    },
                  },
                  visibility: { 'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC' },
                }),
              });
              const pd = await pr.json().catch(() => ({}));
              const postUrn = pd.id || pr.headers.get('x-restli-id');
              if (!pr.ok || !postUrn) {
                throw new Error('LinkedIn 發文失敗：' + JSON.stringify(pd).slice(0, 300));
              }
              const permalink = `https://www.linkedin.com/feed/update/${postUrn}`;
              await env.DB.prepare(
                `UPDATE social_post_queue SET status='posted', url=?, posting_at=NULL, posted_at=datetime('now','+8 hours') WHERE id=?`
              ).bind(permalink, qid).run();
              await answer('✅ 已經發到 LinkedIn 上了', true);
              await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
                method: 'POST', headers: { 'content-type': 'application/json' },
                body: JSON.stringify({
                  chat_id: cq.message.chat.id,
                  ...(cq.message.message_thread_id ? { message_thread_id: cq.message.message_thread_id } : {}),
                  reply_to_message_id: cq.message.message_id,
                  text: `✅ ${who} 已核准，已發到 LinkedIn\n${permalink}`,
                }),
              }).catch(() => {});
              await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
                method: 'POST', headers: { 'content-type': 'application/json' },
                body: JSON.stringify({
                  chat_id: cq.message.chat.id, message_id: cq.message.message_id,
                  reply_markup: { inline_keyboard: [[{ text: `✅ ${who} 已核准，已發到 LinkedIn`, callback_data: 'noop' }]] },
                }),
              }).catch(() => {});
              return;
            }

            // 2026-08-14 改：Jacky 要的是「串文」——不是單則貼文，是主文
            // 後面接一則自己回覆自己的貼文，最後一則固定放應徵了解窗口的
            // LINE 連結。Threads 每一則都是「建立 container 拿 creation_id
            // →threads_publish 才真的貼出去」兩段式；第二則要串在第一則
            // 底下，靠 reply_to_id 帶第一則「已發布」的 id。
            const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

            // ⚠️ 實測撞到「Media not found」：container 建立完不能馬上發布，
            // Threads／Instagram 這類容器式發文 API 都一樣——container 是
            // 非同步處理的，太快呼叫 publish 會抓不到還沒就緒的 container。
            // 官方文件建議發布前等幾秒，這裡固定等 5 秒再 publish，
            // 三則串文連續發也一樣，每一則都各自等。
            const postOne = async (text, replyToId) => {
              const params = { media_type: 'TEXT', text, access_token: threadsToken };
              if (replyToId) params.reply_to_id = replyToId;
              const createR = await fetch(
                `https://graph.threads.net/v1.0/${threadsUserId}/threads?` + new URLSearchParams(params),
                { method: 'POST' }
              );
              const created = await createR.json();
              if (!createR.ok || !created.id) throw new Error('建立草稿失敗：' + JSON.stringify(created));

              await sleep(5000);

              const pubR = await fetch(
                `https://graph.threads.net/v1.0/${threadsUserId}/threads_publish?` +
                new URLSearchParams({ creation_id: created.id, access_token: threadsToken }),
                { method: 'POST' }
              );
              const published = await pubR.json();
              if (!pubR.ok || !published.id) throw new Error('發布失敗：' + JSON.stringify(published));
              return published.id;
            };

            // ⚠️ 實測抓到：Threads 單則貼文上限 500 字，這個格式的招募文案
            // （開場＋工作內容＋招募資訊＋收尾）常態性會超過，要自動切成
            // 多則串接，不能整包當一則送出去——2026-08-14 真實測試撞到
            // 「Param text must be at most 500 characters long」才發現。
            // ⚠️ 2026-08-14 再改：原本逐「行」硬湊，切點很隨便，常常把
            // 「招募資訊」那一整塊從中間切斷，讀起來支離破碎。這個文案格式
            // 本身就是用空白行分段（開場／工作內容／招募資訊／收尾），改成
            // 優先照這些段落切，一段一段湊，同一段裡的行不會被拆開；只有
            // 單一段落本身就超過上限這種極端情況，才退回逐行硬切。
            const splitForThreads = (text, maxLen = 480) => {
              const paras = text.split(/\n\s*\n/); // 空白行＝段落邊界
              const chunks = [];
              let cur = '';
              const flush = () => { if (cur) { chunks.push(cur); cur = ''; } };
              for (const para of paras) {
                const candidate = cur ? cur + '\n\n' + para : para;
                if (candidate.length <= maxLen) { cur = candidate; continue; }
                flush();
                if (para.length <= maxLen) { cur = para; continue; }
                // 單一段落本身就太長（極端情況）：退回逐行湊，行內不再硬切。
                const lines = para.split('\n');
                for (const line of lines) {
                  const c2 = cur ? cur + '\n' + line : line;
                  if (c2.length <= maxLen) { cur = c2; continue; }
                  flush();
                  if (line.length <= maxLen) { cur = line; continue; }
                  for (let i = 0; i < line.length; i += maxLen) chunks.push(line.slice(i, i + maxLen));
                }
              }
              flush();
              return chunks;
            };

            const chunks = splitForThreads(row.draft);
            let firstId = null, lastId = null;
            for (const chunk of chunks) {
              lastId = await postOne(chunk, lastId);
              if (!firstId) firstId = lastId;
            }
            // 2026-09-03 改：純職缺貼文（topic_id 為空）不再自動加發連結回覆——
            // Jacky 要求改成留言制 CTA（見 SKILL.md／style_prompts 的
            // {{CTA_KEYWORD}} 機制），連結由發布端硬加等於把留言制架空。
            // 話題類型（topic_id 有值）維持原行為不動，範圍只限「純職缺貼文」。
            // 2026-09-04 加：DR 是唯一例外——這隻帳號的職缺文仍要放連結，
            // 用 social_accounts.force_link_on_posts 這個帳號層級開關控制，
            // 不寫死帳號 id，之後要幫別的帳號開一樣的行為只要改這個欄位。
            if (row.topic_id || row.force_link_on_posts) {
              // 2026-08-19 改：不再直接貼 lin.ee，改走自家轉址頁。
              // 直接放 LINE 連結的話，候選人一加進去就斷線——LINE 不會告訴我們
              // 他是從誰的哪則貼文來的，發文成效永遠只能看瀏覽數，看不到帶進幾個人。
              // 轉址頁會記下點擊再把人送去同一個 LINE，候選人那端多不到半秒。
              // ⚠️ q=<queue_id> 一定要帶——沒帶的話同帳號同職缺發過多次時，
              // 點擊只能猜最新那一篇，舊貼文的成效會被吃掉。
              const goLink = row.account_id
                ? `https://step1ne.com/go/?c=${row.account_id}&j=${encodeURIComponent(row.job_slug)}&q=${qid}`
                : lineLink;   // 沒指定帳號的舊職缺照舊，不要為了統計改變既有行為
              await postOne(`▪️ 應徵了解窗口：\n${goLink}`, lastId);
              await env.DB.prepare(`UPDATE social_post_queue SET has_external_link=1 WHERE id=?`).bind(qid).run();
            }

            // 拿第一則（主文）的公開連結存起來備查——顧問要留紀錄用。
            let permalink = null;
            try {
              const linkR = await fetch(
                `https://graph.threads.net/v1.0/${firstId}?` +
                new URLSearchParams({ fields: 'permalink', access_token: threadsToken })
              );
              const linkD = await linkR.json();
              permalink = linkD.permalink || null;
            } catch { /* 查連結失敗不影響已經發出去這件事 */ }

            await env.DB.prepare(`UPDATE social_post_queue SET status='posted', url=?, posting_at=NULL, posted_at=datetime('now','+8 hours') WHERE id=?`)
              .bind(permalink, qid).run();
            await answer('✅ 已經發到 Threads 上了', true);
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: cq.message.chat.id,
                ...(cq.message.message_thread_id ? { message_thread_id: cq.message.message_thread_id } : {}),
                reply_to_message_id: cq.message.message_id,
                text: `✅ ${who} 已核准，已發到 Threads（串文兩則）` + (permalink ? `\n${permalink}` : '\n（連結查詢失敗，請自行到 Threads 上確認）'),
              }),
            }).catch(() => {});
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: cq.message.chat.id, message_id: cq.message.message_id,
                reply_markup: { inline_keyboard: [[{ text: `✅ ${who} 已核准，已發到 Threads`, callback_data: 'noop' }]] },
              }),
            }).catch(() => {});
          } catch (e) {
            // 失敗要解鎖，不然這篇就卡在 posting 狀態，之後永遠按不動、也重發不了。
            await env.DB.prepare(`UPDATE social_post_queue SET status=NULL, posting_at=NULL WHERE id=? AND status='posting'`).bind(qid).run();
            await answer('❌ 發文失敗：' + String(e).slice(0, 150), true);
            // ⚠️ 2026-08-19 改：原本寫死「Threads 發文失敗」，也沒帶是哪一則、哪個帳號。
            // 加了 LinkedIn 之後這則通知會誤導——顧問看到「Threads 失敗」卻是
            // LinkedIn 那則掛掉，會往錯的方向查。而且沒有 queue id 就無法回頭比對。
            const transient = /is_transient|"code":\s*2|rate limit|try again/i.test(String(e));
            await notify(env,
              `⚠️ ${platform === 'linkedin' ? 'LinkedIn' : 'Threads'} 發文失敗`
              + `\n職缺：${row.title}`
              + `\n帳號：${accLabel || '（未指定）'}　·　排隊編號 #${qid}`
              + (transient ? '\n\n🔄 對方系統回報這是**暫時性錯誤**，通常直接重按一次就會成功。'
                           : '\n\n這不是暫時性錯誤，重按大概還是會失敗，可能要看金鑰或內容。')
              + `\n\n${String(e).slice(0, 300)}`,
              { message_thread_id: THREAD.system });
          }
        } else if (action === 'soc_skip') {
          await env.DB.prepare(`UPDATE social_post_queue SET status='skipped' WHERE id=?`).bind(qid).run();
          await answer('已標記不發這篇');
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: cq.message.chat.id, message_id: cq.message.message_id,
              reply_markup: { inline_keyboard: [[{ text: `❌ ${who} 決定不發這篇`, callback_data: 'noop' }]] },
            }),
          }).catch(() => {});
        } else if (action === 'soc_regen') {
          // 重新產一次交回本機腳本做（要重跑 claude），這裡只清狀態讓它下次
          // 掃描時重新撿到，不在 Worker 裡呼叫 claude（同一個理由：本機
          // claude CLI 帳號登入，Worker 連不到）。
          await env.DB.prepare(`UPDATE social_post_queue SET status=NULL, draft=NULL WHERE id=?`).bind(qid).run();
          await answer('已清掉舊草稿，下次排程跑到時會重新產一份');
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: cq.message.chat.id, message_id: cq.message.message_id,
              reply_markup: { inline_keyboard: [[{ text: `🔄 ${who} 要求重新產一次`, callback_data: 'noop' }]] },
            }),
          }).catch(() => {});
        } else {
          await answer('未知的操作');
        }
        return;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === 'OPTIONS') return new Response(null, { headers: cors(request) });

    // 2026-09-02補：架構拆分Phase3把/health搬去了step1ne-public-worker，
    // 但這裡沒查清楚有沒有外部監控（uptime monitor之類）在打這支網址檢查
    // 這個Worker還活著——刪掉風險不明，成本又是0，兩邊都留一份最安全。
    if (p === '/health') return json(request, { ok: true });

    // ── 招募形式評估工具（enterprise.step1ne.com）──
    //
    // 前端是一個獨立的 Cloudflare Pages 專案（step1ne-enterprise），純靜態、
    // 沒有自己的 Function、沒有自己的 D1 binding，資料一律走這幾支。
    //
    // 為什麼掛在這個 Worker 而不是讓那個 Pages 專案自己綁 D1：
    // 同一個 D1 已經被面談系統、人選配對、主動開發共用，多一個地方持有 binding
    // 只會讓「誰改了資料」查不清楚。寫入口統一在這裡，稽核才有意義。
    //
    // 資料表全部是 hm_ 前綴（hm_cases／hm_todos／hm_audit_logs），
    // 跟 applications／assessments／jobs 那些既有表完全不重疊。
    // ⚠️ 2026-08-26：這套（企業客戶用）跟候選人的「工作風格測驗」原本共用
    // /assessment/ 這個網址開頭，結果這個區塊把候選人的測驗連結
    // （/assessment/<一長串隨機碼>）全部吃掉回 404，候選人根本填不了測驗——
    // 廖若辰那筆就是這樣卡住的。改成專屬的 /hiring-mode/ 網址，兩套永久分開。
    // 舊的 /assessment/ 開頭保留成相容別名，但只認得下面那幾個固定子路徑
    // （health／lead-email／submit／submissions／cases／fetch-url），
    // 候選人的隨機碼 token 一定落不進來，不會再撞第二次。
    const HM_SUBS = ['/health', '/lead-email', '/submit', '/submissions', '/cases', '/fetch-url'];
    const isHmPath = p === '/hiring-mode' || p.startsWith('/hiring-mode/')
      || ((p === '/assessment' || p.startsWith('/assessment/'))
          && HM_SUBS.some((s) => p === '/assessment' + s || p.startsWith('/assessment' + s + '/')));
    if (isHmPath) {
      const sub = (p.startsWith('/hiring-mode')
        ? p.slice('/hiring-mode'.length)
        : p.slice('/assessment'.length)) || '/';
      const auth = request.headers.get('authorization') || '';
      const isConsultant = !!env.ADMIN_TOKEN && safeEqual(auth, `Bearer ${env.ADMIN_TOKEN}`);

      // 前端啟動時打這支決定資料要存正式後端還是退回瀏覽器本機。
      // 表沒建起來就誠實回 d1:false，讓前端顯示 localStorage——不假裝有後端。
      if (sub === '/health' && request.method === 'GET') {
        let ready = false;
        try {
          const row = await env.DB.prepare(
            `SELECT name FROM sqlite_master WHERE type='table' AND name='hm_cases'`
          ).first();
          ready = !!row;
        } catch { ready = false; }
        return json(request, { ok: true, d1: ready });
      }

      // 企業客戶在 step1ne.com 首頁／委託招募頁留信箱，系統自動寄一封信
      // 附評估工具連結（帶 UTM）。2026-08-25 加：之前只有「點了直接跳轉」
      // 這條路，沒有「留信箱、之後收信才點進去」這條——這支補上後者，
      // 讓沒有立刻填表意願、但願意留信箱的人也進得了名單。
      if (sub === '/lead-email' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const email = String(b.email || '').trim();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
          return json(request, { ok: false, error: 'Email 格式看起來不正確' }, 400);
        }
        const companyName = String(b.companyName || '').trim().slice(0, 200);
        const source = String(b.source || '').trim().slice(0, 60) || 'unknown';

        // 2026-08-25 加 email query string：讓真正的評估工具頁面能讀到這個人
        // 是誰、自動帶入送出的紀錄裡，不然信箱留過就跟後面的填表紀錄斷線。
        const link = `https://enterprise.step1ne.com/?utm_source=step1ne&utm_medium=email&utm_campaign=hiring_mode_lead&email=${encodeURIComponent(email)}`;
        const sent = await sendMail(env, email,
          '您的「招募形式快速評估工具」連結',
          [
            `您好${companyName ? `，${companyName}` : ''}：`,
            '感謝您對 STEP1NE 招募服務的關注。點擊下方按鈕，1 分鐘內完成評估，立即取得建議的招募形式，並可另存或列印 PDF。',
          ],
          { url: link, text: '前往招募形式評估工具' }
        );
        if (!sent) return json(request, { ok: false, error: '寄送失敗，可能是 RESEND_API_KEY 沒設或信箱格式問題' }, 500);

        // 通知顧問——這是主動留信箱的商機線索，跟阿財新應徵通知放同一個主題，
        // 判準一樣：都是「有人需要有人去跟進」的訊號。
        await notify(env,
          `📧 新的招募形式評估留信箱\nEmail：${email}\n` +
          `${companyName ? `公司：${companyName}\n` : ''}來源：${source}\n` +
          `已自動寄出評估工具連結，等對方填完會出現在「招募形式評估」後台。`,
          { message_thread_id: THREAD.intake }
        ).catch(() => {});

        return json(request, { ok: true });
      }

      // 招募形式快速評估工具（2026-08-25 簡化重做後）的提交紀錄。
      //
      // 這是全新客戶自己填的表單，不帶顧問權杖——公開可寫，但只存四個欄位
      // ＋三題答案＋判斷結果，沒有任何客戶機密（跟 /cases 那批含公司名/薪資/
      // Email 的舊案件不是同一等級的敏感資料，但一樣不開成可公開讀取）。
      if (sub === '/submit' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const need = ['companyName', 'industry', 'jobTitle', 'salaryText', 'duration', 'employer', 'talentType', 'mode', 'modeLabel'];
        for (const k of need) {
          if (!String(b[k] || '').trim()) {
            return json(request, { ok: false, error: `缺少欄位：${k}` }, 400);
          }
        }
        const id = `asm_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        const now = nowTaipei();
        // 2026-08-25 加 email：之前留信箱（/lead-email）跟真正填表送出（這裡）
        // 是兩支完全不相干的 API，信箱只拿去寄信、Telegram 通知一次，沒存進
        // 任何資料表，顧問後台這份提交紀錄永遠看不到對方的信箱。改成信箱從
        // 寄出的連結帶 query string 過來（見 /lead-email），前端讀出來後跟著
        // 這次送出一起存，不是必填（有些人是從舊連結直接進來，沒有信箱）。
        const email = String(b.email || '').trim().slice(0, 200) || null;
        await env.DB.prepare(
          `INSERT INTO assessment_submissions
             (id, company_name, industry, job_title, salary_text,
              duration, employer, talent_type, mode, mode_label, status, created_at, email)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'new', ?, ?)`
        ).bind(
          id,
          String(b.companyName).slice(0, 200), String(b.industry).slice(0, 100),
          String(b.jobTitle).slice(0, 200), String(b.salaryText).slice(0, 100),
          String(b.duration).slice(0, 40), String(b.employer).slice(0, 40),
          String(b.talentType).slice(0, 40), String(b.mode).slice(0, 40),
          String(b.modeLabel).slice(0, 40), now, email
        ).run();

        // Telegram 通知顧問——不擋回應，通知失敗也不影響前端拿到結果。
        // 放 intake（#3履歷進件）：跟「有新應徵」同一類，是需要有人去跟進的新訊號。
        await notify(env,
          `📋 新的招募形式評估提交\n` +
          `公司：${b.companyName}\n產業：${b.industry}\n` +
          `職稱：${b.jobTitle}　薪資：${b.salaryText}\n` +
          `建議形式：${b.modeLabel}\n` +
          (email ? `聯絡信箱：${email}\n` : '') +
          `後台查看：https://step1ne.com/consultant/hiring-assessments/`,
          { message_thread_id: THREAD.intake }
        ).catch(() => {});

        return json(request, { ok: true, id });
      }

      // 顧問後台看這批提交。跟 /cases 一樣的權杖規則：只有帶 ADMIN_TOKEN 的人拿得到清單。
      if (sub === '/submissions' && request.method === 'GET') {
        if (!isConsultant) return json(request, { ok: false, error: '需要顧問權杖' }, 401);
        const r = await env.DB.prepare(
          `SELECT * FROM assessment_submissions ORDER BY created_at DESC LIMIT 500`
        ).all();
        return json(request, { ok: true, submissions: r.results || [] });
      }

      if (sub.startsWith('/submissions/') && request.method === 'PATCH') {
        if (!isConsultant) return json(request, { ok: false, error: '需要顧問權杖' }, 401);
        const subId = decodeURIComponent(sub.slice('/submissions/'.length));
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const status = String(b.status || '').trim();
        if (!['new', 'contacted', 'closed'].includes(status)) {
          return json(request, { ok: false, error: 'status 必須是 new / contacted / closed' }, 400);
        }
        const r = await env.DB.prepare(
          `UPDATE assessment_submissions SET status = ? WHERE id = ?`
        ).bind(status, subId).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這筆提交' }, 404);
        return json(request, { ok: true });
      }

      // 案件清單。
      //
      // 🚨 案件內容含客戶公司名、薪資帶、聯絡人 Email——不能開成「打一下就
      //    全部撈走」的公開 API。所以分兩條：
      //    顧問（帶 ADMIN_TOKEN）拿得到全部；沒有權杖的人只能用 ?ids= 指名，
      //    而 id 是前端自己建案時記在本機的，別人猜不到。
      if (sub === '/cases' && request.method === 'GET') {
        const idsParam = (url.searchParams.get('ids') || '').split(',')
          .map((s) => s.trim()).filter(Boolean).slice(0, 200);

        if (!isConsultant && !idsParam.length) {
          return json(request, { ok: false, error: '請帶顧問權杖，或用 ?ids= 指名要哪幾筆案件' }, 401);
        }

        let rows;
        if (isConsultant && !idsParam.length) {
          const r = await env.DB.prepare(
            `SELECT payload FROM hm_cases ORDER BY updated_at DESC LIMIT 500`
          ).all();
          rows = r.results || [];
        } else {
          const ph = idsParam.map(() => '?').join(',');
          const r = await env.DB.prepare(
            `SELECT payload FROM hm_cases WHERE id IN (${ph}) ORDER BY updated_at DESC`
          ).bind(...idsParam).all();
          rows = r.results || [];
        }
        const cases = [];
        for (const r of rows) {
          try { cases.push(JSON.parse(r.payload)); } catch { /* 壞掉的那一筆跳過，不讓整份清單掛掉 */ }
        }
        return json(request, { ok: true, cases });
      }

      if (sub.startsWith('/cases/')) {
        // 🚨 2026-09-02補：這三支（單筆讀取／整包存檔／刪除）原本完全沒有權杖檢查，
        // 只要知道或猜到caseId就能讀取/竄改/刪除——跟上面/cases清單那支「沒權杖
        // 只能用?ids=指名」的設計初衷不一致，是漏補的洞。查證過目前沒有任何上線
        // 中的前端（含新版enterprise-assessment的src/）在呼叫這三支，舊資料庫裡
        // 卻躺著10筆真實案件（含客戶公司名/薪資/聯絡人Email），一律要求顧問權杖，
        // 不會擋到任何現行功能。
        if (!isConsultant) return json(request, { ok: false, error: '需要顧問權杖' }, 401);
        const caseId = decodeURIComponent(sub.slice('/cases/'.length));
        if (!caseId || caseId.includes('/')) {
          return json(request, { ok: false, error: '案件編號格式不正確' }, 400);
        }

        if (request.method === 'GET') {
          const row = await env.DB.prepare(`SELECT payload FROM hm_cases WHERE id = ?`)
            .bind(caseId).first();
          if (!row) return json(request, { ok: false, error: '找不到這件案子' }, 404);
          let parsed;
          try { parsed = JSON.parse(row.payload); }
          catch { return json(request, { ok: false, error: '案件資料損毀，無法解析' }, 500); }
          return json(request, { ok: true, case: parsed });
        }

        if (request.method === 'PUT') {
          let b;
          try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
          if (!b || b.id !== caseId) {
            return json(request, { ok: false, error: '網址與內容的案件編號不一致' }, 400);
          }
          const payload = JSON.stringify(b);
          // 案件會夾帶來源文件的全文，不設上限的話一筆就能把 D1 撐爆。
          if (payload.length > 4_000_000) {
            return json(request, { ok: false, error: '案件資料超過 4MB，請減少匯入的來源文件' }, 413);
          }

          const lead = b.lead || {};
          const now = nowTaipei();
          await env.DB.prepare(
            `INSERT INTO hm_cases
               (id, organization_id, title, status, audience, primary_mode,
                lead_email, lead_contact_name, lead_phone,
                utm_source, utm_medium, utm_campaign, referrer, lead_origin,
                payload, created_at, updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(id) DO UPDATE SET
               organization_id = excluded.organization_id,
               title           = excluded.title,
               status          = excluded.status,
               audience        = excluded.audience,
               primary_mode    = excluded.primary_mode,
               lead_email      = excluded.lead_email,
               lead_contact_name = excluded.lead_contact_name,
               lead_phone      = excluded.lead_phone,
               utm_source      = excluded.utm_source,
               utm_medium      = excluded.utm_medium,
               utm_campaign    = excluded.utm_campaign,
               referrer        = excluded.referrer,
               lead_origin     = excluded.lead_origin,
               payload         = excluded.payload,
               updated_at      = excluded.updated_at`
          ).bind(
            caseId, b.organizationId || null, String(b.title || '未命名案件'),
            String(b.status || 'draft'), String(b.audience || 'internal'),
            (b.review && b.review.consultantVersion && b.review.consultantVersion.primary)
              || (b.assessment && b.assessment.primary) || null,
            lead.email || null, lead.contactName || null, lead.phone || null,
            lead.utmSource || null, lead.utmMedium || null, lead.utmCampaign || null,
            lead.referrer || null, lead.origin || 'consultant',
            payload, b.createdAt || now, b.updatedAt || now
          ).run();

          // 稽核軌跡攤平存一份，不解 JSON 也查得到誰改了什麼。
          // id 是前端產的，重存同一件案子不會重複寫入。
          for (const log of Array.isArray(b.auditLogs) ? b.auditLogs.slice(-200) : []) {
            if (!log || !log.id) continue;
            await env.DB.prepare(
              `INSERT OR IGNORE INTO hm_audit_logs
                 (id, case_id, action, actor, before_json, after_json, created_at)
               VALUES (?,?,?,?,?,?,?)`
            ).bind(
              String(log.id), caseId, String(log.action || ''), String(log.actor || ''),
              log.before === undefined ? null : JSON.stringify(log.before),
              log.after === undefined ? null : JSON.stringify(log.after),
              log.createdAt || now
            ).run();
          }

          // 顧問覆核後產生的待辦。
          for (const t of Array.isArray(b.todos) ? b.todos.slice(0, 100) : []) {
            if (!t || !t.id) continue;
            await env.DB.prepare(
              `INSERT OR IGNORE INTO hm_todos
                 (id, case_id, kind, title, detail, owner, status, created_at)
               VALUES (?,?, 'case', ?,?,?,?,?)`
            ).bind(
              String(t.id), caseId, String(t.title || ''), String(t.detail || ''),
              String(t.owner || '顧問'), String(t.status || 'open'), t.createdAt || now
            ).run();
          }

          // ── CRM 歸因 ──
          // 主網域（step1ne.com）的留 Email 入口頁把人導進這個工具時，
          // 網址會帶 ?email=…&utm_source=…。留下來的 Email 如果只是躺在案件裡，
          // 不會有人去跟——所以自動開一條 CRM 建檔待辦。
          // id 用 crm_<案件編號> 是固定的，同一件案子重存幾次都只會有一條。
          if (lead.email && (lead.origin === 'inbound' || lead.utmSource)) {
            await env.DB.prepare(
              `INSERT OR IGNORE INTO hm_todos
                 (id, case_id, kind, title, detail, owner, status, created_at)
               VALUES (?,?, 'crm_intake', ?,?, '顧問', 'open', ?)`
            ).bind(
              `crm_${caseId}`, caseId,
              `CRM 建檔：${lead.email}`,
              `從${lead.utmSource ? ` ${lead.utmSource} ` : '外部連結'}進來的評估案件「${String(b.title || '未命名案件')}」。` +
              `聯絡人：${lead.contactName || '未提供'}　電話：${lead.phone || '未提供'}　` +
              `來源頁：${lead.referrer || '未提供'}`,
              now
            ).run();
          }

          // 2026-08-26 拿掉：這裡原本有一段「案件核准/匯出後自動同步進 jobs 表」的
          // 邏輯。查證過沒有任何現行前端會呼叫這支 /assessment/cases/:id 端點
          // （enterprise.step1ne.com 現在的簡化版走的是 /assessment/submit，
          // 寫進完全不同的 assessment_submissions 表）——這是「2026-08-25 簡化
          // 重做」之前的舊版評估工具遺留的孤兒程式碼。Jacky 確認這個工具本質是
          // 留潛在客戶名單開發用，不是職缺建立工具，不該碰 jobs 表：律准那筆
          // 「資深職業安全衛生工程師」重複職缺就是這段孤兒程式碼自動寫出來的。
          return json(request, { ok: true, id: caseId, updated_at: b.updatedAt || now });
        }

        if (request.method === 'DELETE') {
          const r = await env.DB.prepare(`DELETE FROM hm_cases WHERE id = ?`).bind(caseId).run();
          if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這件案子' }, 404);
          return json(request, { ok: true, id: caseId });
        }
      }

      // 職缺網址代抓。瀏覽器直接抓會被對方站台的 CORS 擋，所以由 Worker 代勞。
      //
      // ⚠️ 104 等平台的職缺內容是前端渲染的，純 HTML 常常拿不到內文。
      //    拿不到就明說拿不到、請顧問改貼文字——絕不編一段內容出來。
      if (sub === '/fetch-url' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        let target;
        try { target = new URL(String(b.url || '')); }
        catch { return json(request, { ok: false, error: '網址格式不正確' }, 400); }
        if (target.protocol !== 'http:' && target.protocol !== 'https:') {
          return json(request, { ok: false, error: '只接受 http/https 網址' }, 400);
        }
        // 擋 SSRF：私有網段、localhost、雲端 metadata 端點一律拒絕。
        const h = target.hostname.toLowerCase();
        const blocked =
          h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.internal') ||
          h === '169.254.169.254' || h === 'metadata.google.internal' ||
          /^(127|10)\./.test(h) || /^192\.168\./.test(h) ||
          /^172\.(1[6-9]|2\d|3[01])\./.test(h) ||
          h === '0.0.0.0' || h === '::1' || h === '[::1]';
        if (blocked) return json(request, { ok: false, error: '不允許讀取內部網路位址' }, 400);

        try {
          const res = await fetch(target.toString(), {
            headers: {
              'User-Agent': 'Mozilla/5.0 (compatible; STEP1NE-HiringModeBot/1.0)',
              Accept: 'text/html,application/xhtml+xml',
            },
            redirect: 'follow',
            cf: { cacheTtl: 300 },
          });
          if (!res.ok) return json(request, { ok: true, error: `來源網站回應 ${res.status}` });
          const ct = res.headers.get('content-type') || '';
          if (!ct.includes('text/html') && !ct.includes('text/plain')) {
            return json(request, { ok: true, error: `來源不是網頁內容（${ct}）` });
          }
          const text = (await res.text())
            .replace(/<script[\s\S]*?<\/script>/gi, ' ')
            .replace(/<style[\s\S]*?<\/style>/gi, ' ')
            .replace(/<\/(p|div|li|tr|h[1-6]|section|article)>/gi, '\n')
            .replace(/<br\s*\/?>/gi, '\n')
            .replace(/<[^>]+>/g, ' ')
            .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
            .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
            .replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n')
            .trim()
            .slice(0, 60000);
          if (text.length < 200) {
            return json(request, { ok: true,
              error: '這個職缺頁的內容是由前端動態載入的，代抓拿不到內文。請改用「貼上文字」把職缺內容貼進來。' });
          }
          return json(request, { ok: true, text, host: target.hostname });
        } catch (err) {
          return json(request, { ok: true, error: `讀取失敗：${err instanceof Error ? err.message : String(err)}` });
        }
      }

      // 顧問待辦清單（含自動產生的 CRM 建檔）。只給顧問看。
      if (sub === '/todos' && request.method === 'GET') {
        if (!isConsultant) return json(request, { ok: false, error: 'unauthorized' }, 401);
        const { results } = await env.DB.prepare(
          `SELECT t.id, t.case_id, t.kind, t.title, t.detail, t.owner, t.status, t.created_at,
                  c.title AS case_title, c.lead_email
             FROM hm_todos t LEFT JOIN hm_cases c ON c.id = t.case_id
            WHERE t.status != 'done'
            ORDER BY t.created_at DESC LIMIT 300`
        ).all();
        return json(request, { ok: true, todos: results || [] });
      }

      return json(request, { ok: false, error: 'not found' }, 404);
    }


    // LINE OA「查詢面試進度」webhook（全民獵才帳號）。公開端點，
    // LINE 從外面呼叫沒辦法帶 ADMIN_TOKEN，改用 x-line-signature 驗證。
    //
    // ⚠️ 2026-09-02 更新：用 LINE 官方 API（GET /v2/bot/channel/webhook/endpoint）
    //    重新查證，目前實際生效的 webhook 網址並不是 linehook.step1ne.com，
    //    而是直接指到 Worker（Worker 拆分後已改指到 step1ne-messaging-relay）。
    //    linehook.step1ne.com 這個網址背後查到是一台空的預設 nginx（無內容、
    //    非任何已知 Worker），來源與是否還有其他用途待查證中，詳見
    //    [[project_step1ne_linehook_dns_orphan]] 記憶。舊版（2026-08-12）
    //    comment 說「正式網址是 linehook.step1ne.com」，那是當時查證的結果，
    //    但目前已不是事實——不要照舊 comment 去改路由設定。
    // ⚠️ 2026-09-02 補：這支route本身仍留在step1ne-recruit-api（架構拆分Phase2
    //    只是在前面加了一個relay Worker接手LINE/Telegram官方webhook設定，
    //    實際的業務邏輯處理完全沒搬，還是這裡在跑）。
    if (p === '/line-webhook' && request.method === 'POST') {
      const rawBody = await request.text();
      const sig = request.headers.get('x-line-signature') || '';
      if (!(await verifyLineSignature(env.LINE_CHANNEL_SECRET, rawBody, sig))) {
        return new Response('forbidden', { status: 403 });
      }
      let body;
      try { body = JSON.parse(rawBody); } catch { return new Response('ok'); }
      const events = Array.isArray(body.events) ? body.events : [];
      for (const ev of events) {
        try {
          await handleLineEvent(env, ev);
        } catch (e) {
          await notify(env, `⚠️ LINE 事件處理失敗：${String(e && e.message || e).slice(0, 200)}`,
            { message_thread_id: THREAD.system });
        }
      }
      // LINE 只要求 200，內容不重要
      return new Response('ok');
    }

    // Telegram 按鈕回呼。這支是公開端點（Telegram 從外面呼叫，沒辦法帶 ADMIN_TOKEN），
    // 改用 Telegram 設定 webhook 時給的 secret_token 驗證，跟 ADMIN_TOKEN 是兩件事。
    if (p === '/telegram/webhook' && request.method === 'POST') {
      if (env.TG_WEBHOOK_SECRET &&
          request.headers.get('x-telegram-bot-api-secret-token') !== env.TG_WEBHOOK_SECRET) {
        return new Response('forbidden', { status: 403 });
      }
      let update;
      try { update = await request.json(); } catch { return new Response('ok'); }

      // ── 電洽新增人選：TG bot 多輪對話（2026-09-01 加）──
      // 顧問電話洽談完，不用開網頁後台，直接在這個獨立 topic 走完整套：
      // /new 開始 → 貼逐字稿 → 問履歷（有就傳檔案）→ 選客戶按鈕 → 選職缺按鈕
      // （客戶/職缺清單即時查資料庫，新增的會自動出現，不是寫死的）。
      // 收齊後呼叫既有的 /admin/pipeline/manual-forward——跟網頁「新增人選」
      // 按鈕完全同一支端點，不重寫一份新邏輯：AI 初篩報告顧問版沿用本機
      // consultant_call_report_tick.py 排程，不用另外做。
      // 放在最前面處理，跟其他 topic 的邏輯（顧問人選回報區等）互不影響。
      {
        const callIntakeTopic = await getOrCreateTopic(env, 'call_intake', '📞 電洽新增人選');
        const rm2 = update.message;
        const cq2 = update.callback_query;

        if (rm2 && callIntakeTopic && Number(rm2.message_thread_id) === callIntakeTopic
            && !(rm2.from && rm2.from.is_bot)) {
          const sess = await ncSession(env, rm2.chat.id, rm2.from.id);
          // 2026-09-01 改：拿掉 `/new` 文字指令——這個群組另一支通用機器人
          // （commander/bot.py，「總指揮」）把任何開頭是 `/` 的訊息都當成
          // 在叫它，`/new` 會讓兩隻同時回應，顧問分不清誰在做事。改成純按鈕
          // （callback_query），總指揮那套規則只認文字訊息，按鈕完全不會誤觸發，
          // 也剛好符合最早的需求（「顧問點擊 tg bot」）。
          // 沒有進行中的對話時，任何一則訊息都先回一個「開始」按鈕當入口。
          if (!sess) {
            await ncSend(env, rm2.chat.id, callIntakeTopic, '要幫這位人選建立卡片嗎？', {
              inline_keyboard: [[{ text: '🆕 開始新增人選', callback_data: 'nc_begin' }]],
            });
            return new Response('ok');
          }
          if (sess) {
            // 2026-09-02 加：原本只認 rm2.text，顧問直接傳 PDF/圖片附件（例如已經
            // 整理好的電洽逐字稿檔案）時 rm2.text 是空的，整個 if 直接跳過，
            // 顧問看起來就是「傳了沒反應」（Phoebe 實測撞到的情境）。改成文字跟
            // 附件都收，附件用 env.AI.toMarkdown 轉成文字，跟 /admin/application/
            // call-note 那支既有的附件轉文字邏輯同一套做法，不重新發明。
            if (sess.step === 'transcript' && (String(rm2.text || '').trim() || rm2.document)) {
              let transcriptText = String(rm2.text || '').trim();
              if (!transcriptText && rm2.document) {
                try {
                  const fr = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/getFile?file_id=${rm2.document.file_id}`);
                  const fd = await fr.json();
                  if (fd.ok) {
                    const fileUrl = `https://api.telegram.org/file/bot${env.TG_BOT_TOKEN}/${fd.result.file_path}`;
                    const fileResp = await fetch(fileUrl);
                    const buf = await fileResp.arrayBuffer();
                    const md = await env.AI.toMarkdown([
                      { name: rm2.document.file_name || 'upload', blob: new Blob([buf], { type: rm2.document.mime_type || 'application/octet-stream' }) },
                    ]);
                    transcriptText = (md && md[0] && md[0].data) ? String(md[0].data).trim() : '';
                  }
                } catch (e) { transcriptText = ''; }
                if (!transcriptText) {
                  await ncSend(env, rm2.chat.id, callIntakeTopic, '這個檔案讀不出文字內容，麻煩直接貼逐字稿文字，或換一個檔案再試一次。');
                  return new Response('ok');
                }
              }
              sess.data.transcript = transcriptText;
              await ncSetSession(env, rm2.chat.id, rm2.from.id, 'name', sess.data);
              await ncSend(env, rm2.chat.id, callIntakeTopic, '收到逐字稿了。這位人選姓名？（先問名字是為了查有沒有舊紀錄，同一個人不會建重複）');
              return new Response('ok');
            }
            // 2026-09-01 加：問完姓名先查有沒有同名舊紀錄，避免同一個人被重複
            // 建成兩筆卡片（這正是之前「周丞恩／吳丞恩」事件的同類風險）。
            // 找到就秀出來給顧問確認是不是同一人，是的話後面直接整合進舊紀錄
            // （呼叫 /admin/application/call-note，附加不覆蓋），不是就照原流程建新卡片。
            if (sess.step === 'name' && String(rm2.text || '').trim()) {
              sess.data.name = rm2.text.trim();
              const { results: dups } = await env.DB.prepare(
                `SELECT id, job_slug, job_title, created_at FROM applications
                  WHERE name = ? ORDER BY created_at DESC LIMIT 5`
              ).bind(sess.data.name).all();
              if (dups && dups.length) {
                await ncSetSession(env, rm2.chat.id, rm2.from.id, 'dup_confirm', sess.data);
                const lines = dups.map((d) => `・${d.job_title || d.job_slug}（${String(d.created_at).slice(0, 10)}）`).join('\n');
                const rows = dups.slice(0, 3).map((d) => ([{
                  text: '是同一人，整合進「' + (d.job_title || d.job_slug) + '」那筆',
                  callback_data: 'nc_dup_yes:' + d.id,
                }]));
                rows.push([{ text: '不是，這是新的人選', callback_data: 'nc_dup_no' }]);
                await ncSend(env, rm2.chat.id, callIntakeTopic,
                  `找到同名的舊紀錄：\n${lines}\n\n是同一個人選嗎？是的話這次電洽內容會整合進舊紀錄（不會覆蓋，時間會記這一刻）；不是的話就當新人選繼續建。`,
                  { inline_keyboard: rows });
                return new Response('ok');
              }
              await ncSetSession(env, rm2.chat.id, rm2.from.id, 'resume_ask', sess.data);
              await ncSend(env, rm2.chat.id, callIntakeTopic, '沒有找到同名舊紀錄，當新人選處理。有履歷要一起附上嗎？', {
                inline_keyboard: [[{ text: '有，我上傳', callback_data: 'nc_resume_yes' },
                                    { text: '沒有，跳過', callback_data: 'nc_resume_no' }]],
              });
              return new Response('ok');
            }
            if (sess.step === 'resume_file' && rm2.document) {
              try {
                const fr = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/getFile?file_id=${rm2.document.file_id}`);
                const fd = await fr.json();
                if (fd.ok) {
                  const fileUrl = `https://api.telegram.org/file/bot${env.TG_BOT_TOKEN}/${fd.result.file_path}`;
                  const fileResp = await fetch(fileUrl);
                  const buf = await fileResp.arrayBuffer();
                  let bin = '';
                  const bytes = new Uint8Array(buf);
                  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                  sess.data.resume_b64 = btoa(bin);
                  sess.data.resume_name = rm2.document.file_name || 'resume';
                  sess.data.resume_mime = rm2.document.mime_type || 'application/octet-stream';
                }
              } catch (e) { /* 履歷抓不到不擋主流程，之後可以在網頁補傳 */ }
              const who = (rm2.from && (rm2.from.username || rm2.from.first_name)) || '顧問';
              if (sess.data.existing_application_id) {
                await ncSubmitMerge(env, sess, rm2.chat.id, callIntakeTopic, who);
                await ncClearSession(env, rm2.chat.id, rm2.from.id);
                return new Response('ok');
              }
              await ncSetSession(env, rm2.chat.id, rm2.from.id, 'contact', sess.data);
              // 2026-09-02 改：電話／Email 改選填——顧問電洽當下對方可能沒空講、
              // 或忘了問，不該卡住整個建檔流程，加一顆跳過按鈕。之後人選卡片會
              // 提醒補聯絡方式（見 doManualForward 的佔位 email 判斷）。
              await ncSend(env, rm2.chat.id, callIntakeTopic, '履歷收到了。電話或 Email（填一個就好，沒有也可以先跳過）？', {
                inline_keyboard: [[{ text: '沒有聯絡方式，先跳過', callback_data: 'nc_contact_skip' }]],
              });
              return new Response('ok');
            }
            if (sess.step === 'contact' && String(rm2.text || '').trim()) {
              const v = rm2.text.trim();
              if (v.includes('@')) sess.data.email = v; else sess.data.phone = v;
              await ncAskOwner(env, rm2.chat.id, callIntakeTopic, rm2.from.id, sess.data);
              return new Response('ok');
            }
          }
        }

        if (cq2 && String(cq2.data || '').startsWith('nc_')) {
          const ans2 = async (t) => {
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ callback_query_id: cq2.id, text: t || '' }),
            }).catch(() => {});
          };
          const chatId = cq2.message.chat.id;
          const threadId = cq2.message.message_thread_id;

          if (cq2.data === 'nc_begin') {
            await ans2();
            await ncSetSession(env, chatId, cq2.from.id, 'transcript', {});
            await ncSend(env, chatId, threadId, '請貼上這通電洽的逐字稿（一大串文字都可以，直接貼上來），或直接傳一個檔案（PDF／圖片／Word 都可以）。');
            return new Response('ok');
          }

          const sess = await ncSession(env, chatId, cq2.from.id);
          if (!sess) { await ans2('這個流程已經過期了，按「🆕 開始新增人選」重新開始'); return new Response('ok'); }

          if (cq2.data.startsWith('nc_dup_yes:')) {
            const existingId = cq2.data.slice('nc_dup_yes:'.length);
            await ans2();
            sess.data.existing_application_id = existingId;
            await ncSetSession(env, chatId, cq2.from.id, 'resume_ask', sess.data);
            await ncSend(env, chatId, threadId, '好，這次會整合進舊紀錄。有履歷要一起附上嗎？', {
              inline_keyboard: [[{ text: '有，我上傳', callback_data: 'nc_resume_yes' },
                                  { text: '沒有，跳過', callback_data: 'nc_resume_no' }]],
            });
            return new Response('ok');
          }
          if (cq2.data === 'nc_dup_no') {
            await ans2();
            await ncSetSession(env, chatId, cq2.from.id, 'resume_ask', sess.data);
            await ncSend(env, chatId, threadId, '好，當新人選處理。有履歷要一起附上嗎？', {
              inline_keyboard: [[{ text: '有，我上傳', callback_data: 'nc_resume_yes' },
                                  { text: '沒有，跳過', callback_data: 'nc_resume_no' }]],
            });
            return new Response('ok');
          }
          if (cq2.data === 'nc_resume_yes') {
            await ans2();
            await ncSetSession(env, chatId, cq2.from.id, 'resume_file', sess.data);
            await ncSend(env, chatId, threadId, '請上傳履歷檔案（PDF/Word 都可以）。');
            return new Response('ok');
          }
          if (cq2.data === 'nc_resume_no') {
            await ans2();
            const who = (cq2.from && (cq2.from.username || cq2.from.first_name)) || '顧問';
            if (sess.data.existing_application_id) {
              await ncSubmitMerge(env, sess, chatId, threadId, who);
              await ncClearSession(env, chatId, cq2.from.id);
              return new Response('ok');
            }
            await ncSetSession(env, chatId, cq2.from.id, 'contact', sess.data);
            await ncSend(env, chatId, threadId, '好，電話或 Email（填一個就好，沒有也可以先跳過）？', {
              inline_keyboard: [[{ text: '沒有聯絡方式，先跳過', callback_data: 'nc_contact_skip' }]],
            });
            return new Response('ok');
          }
          if (cq2.data === 'nc_contact_skip') {
            await ans2();
            await ncAskOwner(env, chatId, threadId, cq2.from.id, sess.data);
            return new Response('ok');
          }
          if (cq2.data.startsWith('nc_owner:')) {
            const ownerId = cq2.data.slice('nc_owner:'.length);
            await ans2();
            sess.data.owner = ownerId || null;
            sess.data.owner_name = ownerId
              ? (await env.DB.prepare(`SELECT display_name FROM consultants WHERE id=?`).bind(ownerId).first() || {}).display_name || null
              : '未指派';
            const { results: companies } = await env.DB.prepare(
              `SELECT id, display_name FROM client_companies ORDER BY display_name LIMIT 20`).all();
            if (!companies || !companies.length) {
              await ncSend(env, chatId, threadId, '目前系統裡沒有任何客戶資料，沒辦法選——先跟顧問後台確認客戶名單。');
              await ncClearSession(env, chatId, cq2.from.id);
              return new Response('ok');
            }
            await ncSetSession(env, chatId, cq2.from.id, 'client', sess.data);
            const rows = [];
            for (let i = 0; i < companies.length; i += 2) {
              rows.push(companies.slice(i, i + 2).map((c) => ({ text: c.display_name, callback_data: 'nc_client:' + c.id })));
            }
            rows.push([{ text: '❓ 暫時沒有推薦的職缺', callback_data: 'nc_pool' }]);
            await ncSend(env, chatId, threadId, '這位人選要應徵哪個客戶？', { inline_keyboard: rows });
            return new Response('ok');
          }
          if (cq2.data === 'nc_pool') {
            await ans2('處理中…');
            const who2 = (cq2.from && (cq2.from.username || cq2.from.first_name)) || '顧問';
            const payload2 = {
              source_kind: 'new', job_slug: 'unspecified', consent_confirmed: true,
              name: sess.data.name, email: sess.data.email || '', phone: sess.data.phone || '',
              call_notes: sess.data.transcript || '', by: who2, owner: sess.data.owner || null,
              source_channel: 'TG 電洽新增',
            };
            if (sess.data.resume_b64) {
              payload2.resume_b64 = sess.data.resume_b64;
              payload2.resume_name = sess.data.resume_name;
              payload2.resume_mime = sess.data.resume_mime;
            }
            try {
              // 2026-09-01 修：原本用 fetch() 打自己這個 Worker 的公開網址，會被
              // Cloudflare error 1042（self-fetch 防迴圈機制）擋掉，這是「電洽新增
              // 人選」一直失敗的真正原因。改成直接 in-process 呼叫共用函式。
              const r2 = await doManualForward(env, payload2);
              if (r2.body.ok) {
                // 建好之後立刻標記內部結案（不通知候選人，因為根本還沒有推薦
                // 給任何客戶）——讓他直接落進「顧問履歷庫」分頁，不會卡在
                // 「等我決定」看起來像有事要處理。
                await doMarkClosed(env, { application_id: r2.body.application_id, notify: false, reason: '電洽新增時暫無適合職缺，先存進顧問履歷庫' }).catch(() => {});
                await ncSend(env, chatId, threadId, `✅ 建好了：${sess.data.name}（指派給：${sess.data.owner_name || '未指派'}）\n先存進「顧問履歷庫」，之後有適合的職缺再到顧問人選追蹤手動推薦給客戶。`);
              } else {
                await ncSend(env, chatId, threadId, `❌ 失敗：${r2.body.error || '未知錯誤'}`);
              }
            } catch (e) {
              await ncSend(env, chatId, threadId, `❌ 系統錯誤：${e && e.message ? e.message : String(e)}`);
            }
            await ncClearSession(env, chatId, cq2.from.id);
            return new Response('ok');
          }
          if (cq2.data.startsWith('nc_client:')) {
            const companyId = cq2.data.slice('nc_client:'.length);
            await ans2();
            sess.data.company_id = companyId;
            const { results: jobs } = await env.DB.prepare(
              `SELECT slug, title FROM jobs WHERE company_id=? AND status NOT IN ('closed','client_draft','pending_review') ORDER BY title LIMIT 20`
            ).bind(companyId).all();
            if (!jobs || !jobs.length) {
              await ncSend(env, chatId, threadId, '這家客戶目前沒有開放中的職缺，先跟顧問後台確認。');
              await ncClearSession(env, chatId, cq2.from.id);
              return new Response('ok');
            }
            await ncSetSession(env, chatId, cq2.from.id, 'job', sess.data);
            const rows = jobs.map((j) => [{ text: j.title, callback_data: 'nc_job:' + j.slug }]);
            await ncSend(env, chatId, threadId, '哪個職缺？', { inline_keyboard: rows });
            return new Response('ok');
          }
          if (cq2.data.startsWith('nc_job:')) {
            const jobSlug = cq2.data.slice('nc_job:'.length);
            await ans2('處理中…');
            const who = (cq2.from && (cq2.from.username || cq2.from.first_name)) || '顧問';
            const payload = {
              source_kind: 'new', job_slug: jobSlug, consent_confirmed: true,
              name: sess.data.name, email: sess.data.email || '', phone: sess.data.phone || '',
              call_notes: sess.data.transcript || '', by: who, owner: sess.data.owner || null,
              source_channel: 'TG 電洽新增',
            };
            if (sess.data.resume_b64) {
              payload.resume_b64 = sess.data.resume_b64;
              payload.resume_name = sess.data.resume_name;
              payload.resume_mime = sess.data.resume_mime;
            }
            try {
              // 2026-09-01 修：改成直接 in-process 呼叫，理由同上（error 1042）。
              const r = await doManualForward(env, payload);
              if (r.body.ok) {
                await ncSend(env, chatId, threadId, `✅ 建好了：${sess.data.name}（指派給：${sess.data.owner_name || '未指派'}）\n初篩報告大約 5 分鐘後會出現在人選卡片上（需要本機排程在跑）。`);
              } else {
                await ncSend(env, chatId, threadId, `❌ 失敗：${r.body.error || '未知錯誤'}`);
              }
            } catch (e) {
              await ncSend(env, chatId, threadId, `❌ 系統錯誤：${e && e.message ? e.message : String(e)}`);
            }
            await ncClearSession(env, chatId, cq2.from.id);
            return new Response('ok');
          }
        }
      }

      // ── 顧問手動發文匯入追蹤（2026-09-03加）──
      // 顧問自己在Threads上發的文（不是走「一鍵發文」那條自動產稿的路），
      // 原本要Jacky手動貼網址給我、我再手動查職缺、手動INSERT——現在讓顧問
      // 自己在自己的審核房間（例如「法蘭克 - Threads」）貼網址就能自助完成。
      //
      // 判斷「這個房間是誰的」：查social_accounts.tg_thread_id，跟現有
      // 一鍵發文審核通知用的是同一個對應關係，不是另外發明一套。
      //
      // ⚠️ Jacky明確要求：不是只有職缺文，之後也會有話題／時事討論類的貼文。
      // 這種貼文沒有真正的職缺可以對應，job_slug欄位改存一段話題描述文字
      // （不是真的slug）——/admin/social-post-queue那支的JOIN已經改成LEFT JOIN，
      // 前端本來就有title||slug的fallback寫法，話題描述會直接顯示出來，不用改前端。
      {
        const spRow = update.message;
        const spCq = update.callback_query;
        const THREADS_URL_RE = /https?:\/\/(www\.)?threads\.(com|net)\/[^\s]+/i;

        // 從網址反查是哪個顧問帳號在發——比對這則訊息所在的topic。
        const findAccountByThread = async (threadId) => {
          if (!threadId) return null;
          return await env.DB.prepare(
            `SELECT id, label FROM social_accounts WHERE tg_thread_id = ? AND platform = 'threads'`
          ).bind(threadId).first();
        };
        // 分享連結（/share/xxx）要展開成正式網址（@帳號/post/xxx）才能长期保存查證，
        // 分享連結本身可能會過期或變動。
        //
        // ⚠️ 2026-09-03實測踩到：如果網址本來就已經是正式格式（@帳號/post/xxx），
        // 拿去fetch()展開反而會被threads.com導去
        // facebook.com/unsupportedbrowser（Meta對非瀏覽器request的擋帖），
        // 存進去的網址整個是錯的。正式網址不需要展開，直接跳過這一步；
        // 只有真的是短網址（/share/xxx）才需要展開，而且展開失敗要保留原始
        // 網址，不能把「導錯」的網址存進去。
        const resolveThreadsUrl = async (rawUrl) => {
          const clean = (u) => u.split('?')[0];
          if (/threads\.(com|net)\/@[^/]+\/post\//i.test(rawUrl)) return clean(rawUrl);
          try {
            // ⚠️ 2026-09-08 加 User-Agent：不帶 UA 的話 threads.com 直接回 200
    // 不給 302，fetch 拿不到真網址，就會落到下面的「保留原始網址」，
    // 結果 /share/ 短網址被存進資料庫——成效回填抓不到貼文 ID，
    // 那則貼文的瀏覽數永遠是空的（2026-09-03 有 3 篇就是這樣漏掉的）。
    const r = await fetch(rawUrl, {
      redirect: 'follow',
      headers: { 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36' },
      signal: AbortSignal.timeout(8000),
    });
            const finalUrl = r.url || rawUrl;
            if (finalUrl.includes('facebook.com/unsupportedbrowser')) return rawUrl;
            return clean(finalUrl);
          } catch { return rawUrl; }
        };

        if (spRow && !(spRow.from && spRow.from.is_bot)) {
          const spSess = await ncSession(env, spRow.chat.id, spRow.from.id);
          const spText = String(spRow.text || '').trim();
          const spThreadId = spRow.message_thread_id;
          const urlMatch = spText.match(THREADS_URL_RE);

          // ⚠️ 2026-09-03 修：貼錯網址、緊接著貼對的網址糾正——原本這裡要求
          // 「沒有流程正在進行中」才會回應，貼第一則問完「這篇是哪一種？」
          // 還沒點按鈕時，第二則網址會被完全無視，顧問看起來像 bot 不理人
          // （真實案例：Anna 帳號那筆，先貼到帳號頁網址，緊接著貼對的貼文
          // 網址，後面那則完全沒反應）。改成：新網址一律蓋掉舊流程重新問，
          // 不管原本卡在哪一步——「貼新網址」本身就是最明確的糾正意圖，
          // 不需要顧問先手動取消上一筆。
          if (urlMatch) {
            const account = await findAccountByThread(spThreadId);
            if (account) {
              const resolvedUrl = await resolveThreadsUrl(urlMatch[0]);
              const replacedNote = spSess ? '（換成這則，剛剛那則不處理了）\n' : '';
              await ncSetSession(env, spRow.chat.id, spRow.from.id, 'sp_choose_type',
                { url: resolvedUrl, accountId: account.id, accountLabel: account.label });
              await ncSend(env, spRow.chat.id, spThreadId, `${replacedNote}這篇是哪一種？（帳號：${account.label}）`, {
                inline_keyboard: [[
                  { text: '📋 這是職缺文', callback_data: 'sp_type_job' },
                  { text: '💬 這是話題／時事文', callback_data: 'sp_type_topic' },
                ]],
              });
              return new Response('ok');
            }
          }

          // 話題文最後一步：等顧問打字回覆主題描述（不是網址才會走到這裡）
          if (spSess && spSess.step === 'sp_await_label' && spText) {
            // 2026-09-08 改：打完主題不再直接寫進去。原本匯入只存網址跟職缺／主題，
            // 完全沒問寫法，結果 110 篇 Threads 貼文裡有 9 篇是「未標記」——
            // 顧問自己在 App 上發完才貼網址回來的那些，系統不知道用了什麼寫法，
            // 成效比較表裡就變成一格比不了的空白。現在比照後台「一鍵發文」的
            // 三層選擇，匯入時一樣問完再存。
            const label = spText.slice(0, 200);
            await ncSetSession(env, spRow.chat.id, spRow.from.id, 'sp_pick_mission',
              { ...spSess.data, label });
            await ncSend(env, spRow.chat.id, spThreadId, `這篇話題是哪一種？（${label}）`, {
              inline_keyboard: [[
                { text: '💼 通用文', callback_data: 'sp_mis:general' },
                { text: '🤖 AI阿財話題', callback_data: 'sp_mis:ai' },
              ]],
            });
            return new Response('ok');
          }
        }

        if (spCq && String(spCq.data || '').startsWith('sp_')) {
          const spAns = async (t) => {
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ callback_query_id: spCq.id, text: t || '' }),
            }).catch(() => {});
          };
          const spChatId = spCq.message.chat.id;
          const spThreadId2 = spCq.message.message_thread_id;
          const sess = await ncSession(env, spChatId, spCq.from.id);
          if (!sess) { await spAns('這個流程已經過期了，重新貼一次網址'); return new Response('ok'); }

          if (spCq.data === 'sp_type_topic') {
            await spAns();
            await ncSetSession(env, spChatId, spCq.from.id, 'sp_await_label', sess.data);
            await ncSend(env, spChatId, spThreadId2, '請打字回覆這篇在談什麼主題（例如：AI取代營造業討論）');
            return new Response('ok');
          }

          if (spCq.data === 'sp_type_job') {
            // 2026-09-03 修：① status 原本只認 'open'，漏了 'active'（例如日本主管特助
            // 那筆），跟客戶匯入 job_slug 一樣要對齊 social_post_agent.py 已經在用的
            // 「open/active 都算開放中」這個判斷，不要各自維護一份標準。
            // ② 24 個職缺全部平鋪成一排按鈕滑到底才找得到，改成先選客戶收斂範圍，
            // 遊戲橘子集團一家就佔10個，混在一起很難找。
            const { results: companies } = await env.DB.prepare(
              `SELECT c.id, c.display_name, COUNT(*) as n FROM jobs j
                 JOIN client_companies c ON c.id = j.company_id
                WHERE j.status IN ('open','active') GROUP BY c.id ORDER BY c.display_name`
            ).all();
            if (!companies || !companies.length) {
              await spAns();
              await ncSend(env, spChatId, spThreadId2, '目前沒有開放中的職缺，改用話題方式匯入：請打字回覆這篇在談什麼主題。');
              await ncSetSession(env, spChatId, spCq.from.id, 'sp_await_label', sess.data);
              return new Response('ok');
            }
            await spAns();
            await ncSetSession(env, spChatId, spCq.from.id, 'sp_pick_company', sess.data);
            const coRows = companies.map((c) => ([{ text: `${c.display_name}（${c.n}）`, callback_data: `sp_co:${c.id}` }]));
            await ncSend(env, spChatId, spThreadId2, '這篇是哪個客戶？', { inline_keyboard: coRows });
            return new Response('ok');
          }

          if (spCq.data.startsWith('sp_co:')) {
            const companyId = spCq.data.slice('sp_co:'.length);
            const { results: coJobs } = await env.DB.prepare(
              `SELECT slug, title FROM jobs WHERE company_id=? AND status IN ('open','active') ORDER BY title`
            ).bind(companyId).all();
            await spAns();
            await ncSetSession(env, spChatId, spCq.from.id, 'sp_pick_job', sess.data);
            const rows = (coJobs || []).map((j) => ([{ text: j.title || j.slug, callback_data: `sp_job:${j.slug}` }]));
            await ncSend(env, spChatId, spThreadId2, '這篇是哪個職缺？', { inline_keyboard: rows });
            return new Response('ok');
          }

          // 匯入的最後一步共用：真正寫進 social_post_queue。
          // ⚠️ style_id／content_formula／content_mission 一定要一起寫，
          //    少寫就會變成成效表上比不了的「未標記」。
          const spFinish = async (row) => {
            await env.DB.prepare(
              `INSERT INTO social_post_queue
                 (job_slug, account_id, status, requested_at, posted_at, url,
                  style_id, content_formula, content_mission)
               VALUES (?,?,?,?,?,?,?,?,?)`
            ).bind(row.jobSlug, sess.data.accountId, 'posted', nowTaipei(), nowTaipei(),
                   sess.data.url, row.styleId || null, row.formula || null, row.mission || null).run();
            await ncClearSession(env, spChatId, spCq.from.id);
            await ncSend(env, spChatId, spThreadId2, `✅ 已匯入成效追蹤\n${row.summary}`);
          };

          if (spCq.data.startsWith('sp_job:')) {
            const slug = spCq.data.slice('sp_job:'.length);
            const job = await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(slug).first();
            await spAns();
            await ncSetSession(env, spChatId, spCq.from.id, 'sp_pick_way',
              { ...sess.data, slug, jobTitle: (job && job.title) || slug });
            await ncSend(env, spChatId, spThreadId2,
              `這篇職缺文用哪一種寫法？（${(job && job.title) || slug}）`, {
                inline_keyboard: [[
                  { text: '純CTA型', callback_data: 'sp_way:pure' },
                  { text: '對話討論型', callback_data: 'sp_way:dialog' },
                ], [
                  { text: '原始格式（沒特別套公式）', callback_data: 'sp_way:default' },
                ]],
              });
            return new Response('ok');
          }

          // 職缺文｜選寫法類型。純CTA／原始格式選完就結束，
          // 對話討論型還要再挑是哪一個公式（跟後台表單同一套 style_prompts）。
          if (spCq.data.startsWith('sp_way:')) {
            const way = spCq.data.slice('sp_way:'.length);
            await spAns();
            if (way === 'dialog') {
              const { results: styles } = await env.DB.prepare(
                `SELECT id, name FROM style_prompts WHERE subtype='dialog' ORDER BY id`).all();
              if (styles && styles.length) {
                await ncSetSession(env, spChatId, spCq.from.id, 'sp_pick_style', sess.data);
                await ncSend(env, spChatId, spThreadId2, '是哪一個公式？', {
                  inline_keyboard: styles.map((x) => ([{ text: x.name, callback_data: `sp_sty:${x.id}` }])),
                });
                return new Response('ok');
              }
              // 公式表空的就不要卡住顧問，當成沒指定公式的對話討論型存下去
              await spFinish({ jobSlug: sess.data.slug, formula: 'dialog',
                summary: `職缺：${sess.data.jobTitle}\n寫法：對話討論型（沒有可選的公式）` });
              return new Response('ok');
            }
            if (way === 'pure') {
              const pure = await env.DB.prepare(
                `SELECT id FROM style_prompts WHERE subtype='pure' LIMIT 1`).first();
              await spFinish({ jobSlug: sess.data.slug, styleId: pure && pure.id, formula: 'pure',
                summary: `職缺：${sess.data.jobTitle}\n寫法：純CTA型` });
              return new Response('ok');
            }
            await spFinish({ jobSlug: sess.data.slug, formula: 'default',
              summary: `職缺：${sess.data.jobTitle}\n寫法：原始格式` });
            return new Response('ok');
          }

          if (spCq.data.startsWith('sp_sty:')) {
            const styleId = spCq.data.slice('sp_sty:'.length);
            const st = await env.DB.prepare(`SELECT name FROM style_prompts WHERE id=?`).bind(styleId).first();
            await spAns();
            await spFinish({ jobSlug: sess.data.slug, styleId, formula: 'dialog',
              summary: `職缺：${sess.data.jobTitle}\n寫法：${(st && st.name) || '對話討論型'}` });
            return new Response('ok');
          }

          // 話題文｜通用文 vs AI阿財話題。存 content_mission，
          // 對應顧問後台成效頁面 classify() 的判斷（engagement／trust_building）。
          if (spCq.data.startsWith('sp_mis:')) {
            const kind = spCq.data.slice('sp_mis:'.length);
            await spAns();
            await spFinish({
              jobSlug: `💬 ${sess.data.label}`,
              mission: kind === 'ai' ? 'trust_building' : 'engagement',
              summary: `話題：${sess.data.label}\n種類：${kind === 'ai' ? 'AI阿財話題' : '通用文'}`,
            });
            return new Response('ok');
          }
        }
      }

      // ── 顧問在「顧問人選回報區」講的話 ──
      //
      // 漏斗的後三關（客戶面談→錄取→到職）在這之前沒有任何入口，
      // 所以 placements 只有 2 筆、還是事後補登的。顧問不會為了填表單開後台，
      // 但一定會用 Telegram——入口就做在他本來待的地方。
      //
      // 這裡**只負責收下來**。翻譯成漏斗狀態是本機 report_tick.py 的事
      // （Worker 跑不了 claude），而且一律先問過顧問才寫入。
      {
        const rm = update.message;
        // ⚠️ 不可以用 !rm.reply_to_message 來排除。
        //    論壇主題裡的每一則訊息**本身就帶著 reply_to_message**（指向主題根訊息），
        //    加了那個條件等於全部擋掉——2026-08-12 第一次上線就是這樣，
        //    Jacky 打了「湯豐銘客戶約週四下午面試」完全沒進資料庫。
        //    主題 id 已經夠精確了，其他主題不會落到這裡。
        // 2026-08-26 停用：Jacky 明確要求只留一條路——顧問直接在系統的階段條
        // 上調整，不要再有「Telegram 打字→AI 解析→問過才寫」這條平行路徑。
        // 這裡不再寫進 consultant_reports（report_tick.py 這支背景腳本也已經
        // 停用，就算漏寫了訊息也不會有東西去處理），改成回一句話引導顧問
        // 去系統操作，不要讓訊息看起來「有送出但沒反應」。
        if (rm && Number(rm.message_thread_id) === THREAD.report &&
            String(rm.text || '').trim() && !(rm.from && rm.from.is_bot)) {
          await notify(env,
            '這裡已經不會自動處理進度回報了——請直接到顧問後台「初篩報告」頁的階段條調整，客戶跟候選人那邊會自動同步，不用再打字回報。',
            { message_thread_id: THREAD.report }).catch(() => {});
          return new Response('ok');
        }
      }

      // ── 客戶推薦履歷｜人工確認關卡 ──
      // 2026-09-10 加。草稿貼進「客戶履歷人工確認」topic 時帶兩顆按鈕：
      // ✅確認→狀態改confirmed，本機client_report_tick.py下一輪就會真的產PDF；
      // ✏️要修改→只回一句引導，真正的修改內容靠「直接回覆那則訊息打文字」，
      // 跟job_intakes「重寫」那套完全同一個模式（按鈕收不到自由文字）。
      if (update.callback_query && String(update.callback_query.data || '').match(/^(crconfirm|credit):/)) {
        const cqc = update.callback_query;
        const [action, reqId] = String(cqc.data).split(':');
        const ansCb = async (t) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cqc.id, text: t }),
          }).catch(() => {});
        };
        // 2026-09-10 修：原本只靠 answerCallbackQuery 的小提示（Telegram
        // 頂部彈出的toast，2秒左右就消失、容易漏看），按完鈕原本那則草稿
        // 訊息完全沒變化，兩顆按鈕還留在那裡——Jacky反映「按了確認怎麼
        // 沒顯示已確認」「按了要修改怎麼沒有別的按鈕可以點」，都是同一個
        // 根因：狀態只在toast裡講一次，訊息本身沒有留下痕跡。
        // 改成直接編輯原本那則訊息：拿掉兩顆按鈕（避免手滑連點兩次），
        // 並且在文字最上面補一行狀態；「要修改」額外補發一則新訊息把
        // 引導文字留在對話串裡，不會像toast那樣消失就找不到了。
        const chatId = cqc.message && cqc.message.chat && cqc.message.chat.id;
        const msgId = cqc.message && cqc.message.message_id;
        const origText = (cqc.message && cqc.message.text) || '';
        if (action === 'crconfirm') {
          await env.DB.prepare(`UPDATE client_report_requests SET status='confirmed' WHERE id=?`).bind(reqId).run();
          await ansCb('已確認，PDF產出中…');
          if (chatId && msgId) {
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageText`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: chatId, message_id: msgId,
                text: `✅ 已確認，PDF產出中（約1-2分鐘，完成後會在這個topic收到檔案）\n\n${origText}`,
                reply_markup: { inline_keyboard: [] },
              }),
            }).catch(() => {});
          }
        } else {
          await ansCb('請直接回覆這則草稿訊息，打你要怎麼改');
          if (chatId && msgId) {
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageText`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: chatId, message_id: msgId,
                text: `✏️ 等待修改意見——請直接回覆這則訊息，打你想怎麼改\n\n${origText}`,
              }),
            }).catch(() => {});
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: chatId,
                ...(cqc.message.message_thread_id ? { message_thread_id: cqc.message.message_thread_id } : {}),
                reply_to_message_id: msgId,
                text: '👆 直接回覆上面那則草稿訊息，打你想怎麼改就可以了（不用按鈕）',
              }),
            }).catch(() => {});
          }
        }
        return new Response('ok');
      }

      // 顧問（Jacky）直接回覆客戶推薦履歷草稿訊息打修改意見——不透過按鈕，
      // 靠 tg_confirm_message_id 比對。收到意見後不是自己改文字，是重新排一次
      // ai_jobs（kind='client_report_synthesize'），把Jacky的意見＋前一版內容
      // 一起餵給claude CLI重新整理，整理完 client_report_tick.py 的
      // promote_synthesized() 會自動再貼一版新草稿出來，一樣兩顆按鈕，可以
      // 反覆修改到滿意為止；狀態改回awaiting_synthesis，不會提早產PDF。
      {
        const crmsg = update.message;
        if (crmsg && crmsg.reply_to_message && String(crmsg.text || '').trim()) {
          const crRow = await env.DB.prepare(
            `SELECT id, application_id, ai_job_id, synthetic_content_json FROM client_report_requests
              WHERE tg_confirm_message_id = ? AND status = 'awaiting_confirm'`
          ).bind(String(crmsg.reply_to_message.message_id)).first();
          if (crRow) {
            const oldJob = crRow.ai_job_id
              ? await env.DB.prepare(`SELECT payload_json FROM ai_jobs WHERE id=?`).bind(crRow.ai_job_id).first()
              : null;
            let payload = {};
            try { payload = oldJob ? JSON.parse(oldJob.payload_json) : {}; } catch { payload = {}; }
            payload.edit_instruction = crmsg.text.trim();
            try { payload.previous_output = JSON.parse(crRow.synthetic_content_json || '{}'); } catch { payload.previous_output = null; }
            const newJobId = crypto.randomUUID();
            await env.DB.prepare(
              `INSERT INTO ai_jobs (id, kind, payload_json, status, created_at) VALUES (?, 'client_report_synthesize', ?, 'pending', ?)`
            ).bind(newJobId, JSON.stringify(payload), nowTaipei()).run();
            await env.DB.prepare(
              `UPDATE client_report_requests SET status='awaiting_synthesis', ai_job_id=? WHERE id=?`
            ).bind(newJobId, crRow.id).run();
            await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
              method: 'POST', headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                chat_id: crmsg.chat.id,
                ...(crmsg.message_thread_id ? { message_thread_id: crmsg.message_thread_id } : {}),
                reply_to_message_id: crmsg.message_id,
                text: '✏️ 收到，重新整理中（約1-2分鐘），好了會貼新版草稿給你確認。',
              }),
            }).catch(() => {});
            return new Response('ok');
          }
        }
      }

      // ── 顧問按「重寫」之後，直接回覆那則訊息補意見 ──
      // Telegram 的 inline 按鈕收不到自由文字，所以按鈕只負責把狀態改成 rewrite，
      // 意見靠「回覆同一則訊息」收。不做這一段的話，顧問按了重寫之後
      // 總指揮只知道「要重寫」，不知道要改什麼，重擬出來多半還是一樣。
      const rmsg = update.message;
      if (rmsg && rmsg.reply_to_message && String(rmsg.text || '').trim()) {
        const row = await env.DB.prepare(
          `SELECT id, status FROM job_intakes WHERE tg_message_id = ?`
        ).bind(rmsg.reply_to_message.message_id).first();
        if (row) {
          await env.DB.prepare(
            `UPDATE job_intakes SET rewrite_note = ?, status = 'rewrite',
                    updated_at = datetime('now','+8 hours') WHERE id = ?`
          ).bind(rmsg.text.trim(), row.id).run();
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: rmsg.chat.id,
              ...(rmsg.message_thread_id ? { message_thread_id: rmsg.message_thread_id } : {}),
              reply_to_message_id: rmsg.message_id,
              text: '✏️ 收到，已記下你的意見，總指揮下次擬稿會照著改。網站目前沒有任何改動。',
            }),
          }).catch(() => {});
          return new Response('ok');
        }
      }

      // ── 顧問回覆用人單位在 portal 傳來的訊息 ──
      // 同樣靠 tg_message_id 比對到具體是哪個人選的訊息串，不能只看
      // 「有沒有 reply_to_message」（理由同上面 job_intakes 那段的警告）。
      if (rmsg && rmsg.reply_to_message && String(rmsg.text || '').trim()) {
        const cm = await env.DB.prepare(
          `SELECT application_id, company_id FROM candidate_messages WHERE tg_message_id = ?`
        ).bind(rmsg.reply_to_message.message_id).first();
        if (cm) {
          // 用人單位要看到「是哪位顧問回的」，不是統一顯示「STEP1NE 顧問」——
          // 系統從沒有任何地方記過「這個人選歸哪位顧問」的真實姓名（forwarded_by
          // 只存過 'consultant'／'backfill' 這種佔位字串），與其瞎猜，直接抓
          // Telegram 回覆當下那個人的真實姓名最準，誰回的就是誰。
          const replierName = (rmsg.from && (rmsg.from.first_name || rmsg.from.username)) || null;
          await env.DB.prepare(
            `INSERT INTO candidate_messages (application_id, company_id, from_role, content, from_name, created_at)
             VALUES (?, ?, 'consultant', ?, ?, datetime('now','+8 hours'))`
          ).bind(cm.application_id, cm.company_id, rmsg.text.trim(), replierName).run();
          await env.DB.prepare(
            `UPDATE candidate_forwards SET client_unread_count = COALESCE(client_unread_count,0) + 1
              WHERE application_id = ? AND company_id = ?`
          ).bind(cm.application_id, cm.company_id).run();
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: rmsg.chat.id,
              ...(rmsg.message_thread_id ? { message_thread_id: rmsg.message_thread_id } : {}),
              reply_to_message_id: rmsg.message_id,
              text: '✅ 已同步給用人單位',
            }),
          }).catch(() => {});
          return new Response('ok');
        }
      }

      const cq = update.callback_query;


      // ── 顧問回報的確認按鈕 ──
      // 🚨 這裡是唯一會把顧問的話寫進 placements 的地方。
      //    總指揮只負責看懂與提議，寫入一定要顧問按過「對」——
      //    認錯人的話，把 A 的面試日期寫到 B 身上，顧問要花更久才會發現，
      //    而且中間可能已經照著錯的資料去跟客戶講話。
      if (cq && cq.data && String(cq.data).startsWith('cr_')) {
        const [action, rid] = String(cq.data).split(':');
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';
        const ans = async (t) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text: t }),
          }).catch(() => {});
        };
        const row = await env.DB.prepare(
          `SELECT * FROM consultant_reports WHERE id = ?`).bind(rid || '').first();
        if (!row) { await ans('找不到這則回報'); return new Response('ok'); }
        if (row.status === 'applied') { await ans('這則已經寫進去了'); return new Response('ok'); }
        const now = nowTaipei();
        let label = '';

        if (action === 'cr_no') {
          await env.DB.prepare(
            `UPDATE consultant_reports SET status='rejected', decided_by=?, decided_at=?, updated_at=? WHERE id=?`
          ).bind(who, now, now, rid).run();
          label = `❌ ${who} 說不對，沒有寫入`;
          await ans('好，沒有寫進去。直接再講一次就可以');
        } else if (action === 'cr_ok') {
          // ⚠️ 一則訊息可能同時回報好幾位。2026-08-12 ph 的第一則就是——
          //    她引用整批提醒、在每個人下面標一句，一次講了四位。
          //    原本假設「一則＝一位」，那則直接 failed。
          //
          // 🚨 stage 一定要是既有的英文代碼（SUBMITTED/INTERVIEWING/OFFER_ACCEPTED/
          //    ONBOARDED/CLOSED_LOST），不可以是中文。漏斗查詢比對的是
          //    UPPER(stage) IN ('SUBMITTED', ...) 這種英文碼——2026-08-12 第一版
          //    讓總指揮直接吐中文（「客戶面談」「結案」），寫進去之後漏斗完全看不到，
          //    等於白寫。這裡再做一次白名單防呆，report_tick.py 那邊已經正規化過，
          //    但 Worker 不該假設上游一定乾淨。
          const STAGE_OK = new Set(['SUBMITTED', 'INTERVIEWING', 'OFFER_ACCEPTED', 'ONBOARDED', 'CLOSED_LOST']);
          const STAGE_LABEL = { SUBMITTED: '已送件', INTERVIEWING: '客戶面談',
                                OFFER_ACCEPTED: '錄取', ONBOARDED: '到職', CLOSED_LOST: '結案' };
          let items = [];
          try {
            const pj = JSON.parse(row.parse_json || '{}');
            items = (pj.items || []).filter(
              (i) => i && i.application_id && i.confidence === 'high');
          } catch { items = []; }
          if (!items.length) { await ans('這則沒有可以寫入的人選'); return new Response('ok'); }

          const done = [];
          for (const it of items) {
            const app = await env.DB.prepare(
              `SELECT a.id, a.name, a.job_slug, a.job_title, j.client_name
                 FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug
                WHERE a.id = ?`).bind(it.application_id).first();
            if (!app) continue;
            const stage = STAGE_OK.has(String(it.stage || '').toUpperCase())
              ? String(it.stage).toUpperCase() : null;
            const noteTxt = (it.note || '') + `（${now.slice(0, 10)} ${who} 於群組回報）`;
            // 2026-09-14 修：since 原本宣告在下面 if (stage) {} 區塊裡，但後面
            // 「ONBOARDED 發報到日期確認卡」那段是另一個獨立的 if/else if 區塊，
            // 看不到它，一 ONBOARDED 就直接 ReferenceError 炸掉整支寫入流程。
            // 提升到跟 noteTxt 同一層（for 迴圈本體），兩個區塊才共用得到。
            const since = it.date || now.slice(0, 10);
            // 有狀態變化才動 placements；純備註只留在 consultant_reports 裡。
            // ⚠️ 一位人選在同一個職缺只留一筆 placements，用 stage 往前推——
            //    每次插新的會讓漏斗把同一個人算好幾次
            //    （8/11 那個「應徵 4、初審 5」就是這樣來的）。
            if (stage) {
              // ONBOARDED 不是漏斗直接比對的代碼——「到職」那一關看的是
              // onboard_date IS NOT NULL，不是 stage 字串。到職之後案子
              // 進入保證期關懷（CARE_POINTS 邏輯要求 stage==='GUARANTEE'），
              // 所以這裡要落地成 stage='GUARANTEE' + onboard_date，不能照字面寫 'ONBOARDED'。
              const writeStage = stage === 'ONBOARDED' ? 'GUARANTEE' : stage;
              const onboardSql = stage === 'ONBOARDED' ? ', onboard_date = ?' : '';
              const exist = await env.DB.prepare(
                `SELECT id FROM placements WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
              ).bind(app.id).first();
              if (exist) {
                const sql = `UPDATE placements SET stage=?, stage_since=?, owner=?, note=?, updated_at=?${onboardSql} WHERE id=?`;
                const binds = [writeStage, since, who, noteTxt, now];
                if (stage === 'ONBOARDED') binds.push(since);
                binds.push(exist.id);
                await env.DB.prepare(sql).bind(...binds).run();
              } else {
                const sql = `INSERT INTO placements (application_id, candidate_name, job_slug, job_title,
                                         client_name, stage, stage_since, owner, note, created_at, updated_at${stage === 'ONBOARDED' ? ', onboard_date' : ''})
                 VALUES (?,?,?,?,?,?,?,?,?,?,?${stage === 'ONBOARDED' ? ',?' : ''})`;
                const binds = [app.id, app.name, app.job_slug, app.job_title, app.client_name || null,
                               writeStage, since, who, noteTxt, now, now];
                if (stage === 'ONBOARDED') binds.push(since);
                await env.DB.prepare(sql).bind(...binds).run();
              }
            }
            // 🚨 有狀態變化時，順便把報告的處置狀態補上。
            //    2026-08-12 真實事故：ph 在群組回報「林均緯 pass 結案」，
            //    寫進了 placements.stage=CLOSED_LOST，但「初審」那格看的是
            //    reports.consultant_decision（顧問在報告頁按過推薦/需補問/婉拒才算）——
            //    這支只碰了 placements，於是他同時卡在「未處置」跟「結案」兩邊，
            //    顧問看畫面會覺得系統壞了。
            //    只補「還沒處置過」的報告，已經按過的不要動——尊重顧問原本的判斷。
            if (stage) {
              const decision = stage === 'CLOSED_LOST' ? 'rejected' : 'forwarded';
              await env.DB.prepare(
                `UPDATE reports SET consultant_decision = ?, decided_at = ?
                   WHERE id = (SELECT id FROM reports WHERE application_id = ?
                                ORDER BY created_at DESC LIMIT 1)
                     AND consultant_decision IS NULL`
              ).bind(decision, now, app.id).run();
            }
            // 沒有狀態變化的也要留痕跡——顧問回報「客戶還在考慮」也是資訊
            await env.DB.prepare(
              `UPDATE applications SET handled_note = ? WHERE id = ?`
            ).bind(noteTxt, app.id).run();
            // 狀態真的變了才推播——純備註（客戶還在考慮之類）候選人不需要知道，
            // 每次顧問打字都推播會變成騷擾，只在漏斗階段真的往前/往後動的時候推。
            if (stage === 'OFFER_ACCEPTED') await notifyOfferFlex(env, app.id);
            else if (stage === 'ONBOARDED') {
              // 2026-08-13 改：這裡原本是「已經到職」的恭喜卡，但 Jacky 要求
              // 拆開——顧問這個時候通常是剛問到企業給的報到日期（可能還有好幾天），
              // 不是人已經去上班了。改發「報到日期確認」卡，真正的到職恭喜
              // 併進 candidateCareTick() 的到職關懷第 1 天，等真的到了那天才發。
              await notifyOnboardDateFlex(env, app.id, since);
            } else if (stage) await notifyLineProgress(env, app.id);
            done.push(`${app.name}${stage ? ' → ' + (STAGE_LABEL[stage] || stage) : '（備註）'}`);
          }
          await env.DB.prepare(
            `UPDATE consultant_reports SET status='applied', decided_by=?, decided_at=?, updated_at=? WHERE id=?`
          ).bind(who, now, now, rid).run();
          label = `✅ ${who} 確認：${done.join('、')}`.slice(0, 120);
          await ans(`寫進去了（${done.length} 位）`);
        } else {
          await ans('未知的操作');
          return new Response('ok');
        }

        await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            chat_id: cq.message.chat.id, message_id: cq.message.message_id,
            reply_markup: { inline_keyboard: [[{ text: label, callback_data: 'noop' }]] },
          }),
        }).catch(() => {});
        return new Response('ok');
      }

      // ── 反向開發開發信的三顆按鈕 ──
      // 🚨 「核准寄出」是這整套系統裡唯一會真的把信寄出去的地方。
      //    agent 產生信件、但拿不到寄信權限；顧問按下去之前一封都不會寄。
      if (cq && cq.data && String(cq.data).startsWith('bd_')) {
        const [action, bid] = String(cq.data).split(':');
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';
        const ans = async (text) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text, show_alert: true }),
          }).catch(() => {});
        };
        const row = await env.DB.prepare(`SELECT * FROM bd_outreach WHERE id = ?`).bind(bid || '').first();
        if (!row) { await ans('❌ 找不到這封'); return new Response('ok'); }
        if (row.status === 'sent') { await ans('這封已經寄出去了，不重複寄'); return new Response('ok'); }
        const now = nowTaipei();
        let label = '';

        if (action === 'bd_no') {
          await env.DB.prepare(
            `UPDATE bd_outreach SET status='rejected', decided_by=?, decided_at=?, updated_at=? WHERE id=?`
          ).bind(who, now, now, bid).run();
          label = `❌ ${who} 決定不寄`;
          await ans('❌ 不寄了');
        } else if (action === 'bd_rw') {
          await env.DB.prepare(
            `UPDATE bd_outreach SET status='draft', decided_by=?, decided_at=?, updated_at=? WHERE id=?`
          ).bind(who, now, now, bid).run();
          label = `✏️ ${who} 退回重寫`;
          await ans('✏️ 退回了。直接「回覆」這則訊息告訴總指揮要改什麼');
        } else if (action === 'bd_ok') {
          // 寄之前再比對一次客戶名單——名單是活的，擬稿當天可以敲的，
          // 顧問三天後才按核准，中間可能已經簽約了
          const hit = await guardCompany(env, row.company);
          if (hit) {
            await env.DB.prepare(
              `UPDATE bd_outreach SET status='blocked', guard_json=?, updated_at=? WHERE id=?`
            ).bind(JSON.stringify(hit), now, bid).run();
            await ans(`⛔ 沒有寄出：${row.company} 在客戶名單上（${hit.relation}）`);
            label = `⛔ 客戶名單擋下：${hit.why}`;
          } else if (!row.contact_email) {
            await ans('這封還沒有收件人 email，先去後台補上再核准');
            return new Response('ok');
          } else {
            const ok2 = await sendBdMail(env, row.contact_email, row.subject, row.body, row.cv_file_id);
            if (!ok2) { await ans('⚠️ 寄送失敗，信沒有送出去'); return new Response('ok'); }
            await env.DB.prepare(
              `UPDATE bd_outreach SET status='sent', decided_by=?, decided_at=?, sent_at=?, updated_at=? WHERE id=?`
            ).bind(who, now, now, now, bid).run();
            label = `📤 ${who} 已核准，信已寄至 ${row.contact_email}`;
            await ans('📤 寄出去了');
          }
        } else {
          await ans('未知的操作');
          return new Response('ok');
        }

        // 把按鈕換成結果，免得有人再按一次
        await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            chat_id: cq.message.chat.id, message_id: cq.message.message_id,
            reply_markup: { inline_keyboard: [[{ text: label, callback_data: 'noop' }]] },
          }),
        }).catch(() => {});
        return new Response('ok');
      }

      // ── 職缺送審的三顆按鈕 ──
      // ⚠️ 這裡**只改狀態**，不會產生任何頁面。真正動網站的是本機的
      //    jobintake/publish_approved.py，而它只撿 status='approved' 的單子。
      //    Worker 跑不了 python 也 push 不了 git，這個分工是實體限制，不是選擇。
      if (cq && cq.data && String(cq.data).startsWith('job_')) {
        const [action, intakeId] = String(cq.data).split(':');
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';
        const answer = async (text) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text, show_alert: false }),
          });
        };
        const MAP = {
          job_ok: { status: 'approved', toast: '✅ 已核准，稍後會產生職缺頁', label: '✅ 已由 %s 核准發布' },
          job_rw: { status: 'rewrite',  toast: '✏️ 已退回重寫。直接「回覆」這則訊息告訴我要改什麼', label: '✏️ 已由 %s 退回重寫' },
          job_no: { status: 'rejected', toast: '❌ 已拒絕，不會發布', label: '❌ 已由 %s 拒絕發布' },
        };
        const m = MAP[action];
        if (!m) { await answer('未知的操作'); return new Response('ok'); }

        const row = await env.DB.prepare(
          `SELECT id, status FROM job_intakes WHERE id = ?`
        ).bind(intakeId || '').first();
        if (!row) { await answer('❌ 找不到這張收件單'); return new Response('ok'); }
        // 已經處置過的就不要再改一次——兩個人各按一顆會讓狀態來回跳
        if (['approved', 'published', 'rejected'].includes(row.status)) {
          await answer(`這張單子已經是「${row.status}」，不重複處理`);
          return new Response('ok');
        }

        await env.DB.prepare(
          `UPDATE job_intakes SET status=?, decided_by=?, decided_at=datetime('now','+8 hours'),
                  updated_at=datetime('now','+8 hours') WHERE id=?`
        ).bind(m.status, who, row.id).run();
        await answer(m.toast);
        await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            chat_id: cq.message.chat.id, message_id: cq.message.message_id,
            reply_markup: { inline_keyboard: [[{ text: m.label.replace('%s', who), callback_data: 'noop' }]] },
          }),
        }).catch(() => {});
        return new Response('ok');
      }

      // ── 顧問版社群發文：核准／重新產一次／不發 ──
      // 2026-08-14 加。social_post_agent.py 把草稿推到這裡的按鈕就是打這支。
      // Threads 金鑰已經設定好了（THREADS_ACCESS_TOKEN／THREADS_USER_ID，
      // wrangler secret），「確認發布」會真的呼叫 Threads API 貼出去。
      // LinkedIn 還沒申請，approve 時如果只有 Threads 金鑰，就只發 Threads。
      // ── 顧問對阿財判斷的回填（KPI 用）──
      // 2026-08-19 加。系統知道阿財判了什麼，但不知道顧問最後同不同意；
      // 沒有這一欄就永遠算不出「阿財說值得轉的人，顧問真的推了幾成」——
      // 而那正是對外要證明「AI 沒把人看錯」時，客戶唯一會信的數字。
      // 只記一次判斷，不問原因：多問一個欄位就會少一半的人願意按。
      if (cq && cq.data && String(cq.data).startsWith('kpi:')) {
        const [, verdict, appId] = String(cq.data).split(':');
        const LABEL = { ag: '會推', no: '不推', hold: '再看看' };
        const answer = async (text) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text }),
          }).catch(() => {});
        };
        if (!LABEL[verdict]) { await answer('❌ 認不得這個選項'); return new Response('ok'); }
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';
        const r = await env.DB.prepare(
          `UPDATE applications SET consultant_call=?, consultant_call_at=datetime('now','+8 hours'),
                  consultant_call_by=? WHERE id=?`
        ).bind(LABEL[verdict], who, appId).run();
        if (!r.meta || !r.meta.changes) { await answer('❌ 找不到這筆應徵'); return new Response('ok'); }
        await answer(`已記錄：${LABEL[verdict]}`);
        // 按鈕換成結果，讓其他顧問看得到誰判了什麼，也避免重複按
        await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            chat_id: cq.message.chat.id, message_id: cq.message.message_id,
            reply_markup: { inline_keyboard: [[{ text: `📌 ${who} 判定：${LABEL[verdict]}`, callback_data: 'noop' }]] },
          }),
        }).catch(() => {});
        return new Response('ok');
      }

      if (cq && cq.data && String(cq.data).startsWith('soc_')) {
        await handleSocAction(env, cq);
        return new Response('ok');
      }

      if (cq && cq.data && String(cq.data).startsWith('art_')) {
        await handleArticleAction(env, cq);
        return new Response('ok');
      }

      if (cq && cq.data) {
        const [action, appId] = String(cq.data).split(':');
        const decisionMap = { scr_approve: 'approved', scr_decline: 'declined' };
        const decision = decisionMap[action];
        const answer = async (text) => {
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/answerCallbackQuery`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ callback_query_id: cq.id, text, show_alert: false }),
          });
        };
        if (!decision) { await answer('未知的操作'); return new Response('ok'); }

        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';
        const r = await applyScreenDecision(env, appId, decision);
        if (!r.ok) { await answer('❌ ' + (r.error || '處理失敗')); return new Response('ok'); }

        await answer(decision === 'approved' ? '✅ 已核准，面談連結已寄出' : '已婉拒，3 小時後會自動通知候選人');

        // 把按鈕拿掉、標示是誰處理的，避免同一則被兩個人重複點
        const label = decision === 'approved' ? `✅ 已由 ${who} 核准` : `❌ 已由 ${who} 婉拒`;
        const editBody = {
          chat_id: cq.message.chat.id, message_id: cq.message.message_id,
          reply_markup: { inline_keyboard: [[{ text: label, callback_data: 'noop' }]] },
        };
        const editEndpoint = cq.message.text !== undefined ? 'editMessageReplyMarkup' : 'editMessageReplyMarkup';
        await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${editEndpoint}`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify(editBody),
        }).catch(() => {});
      }
      return new Response('ok');
    }

    // ── LinkedIn 授權（顧問各自綁自己的個人帳號）──
    // 2026-08-19 加。LinkedIn 只開放「以個人身分發文」（w_member_social）；
    // 用公司頁發文屬於 Community Management API，要審核而且幾乎只給合作夥伴。
    // 對獵頭來說個人身分反而更好——人看人比人看公司頁有效。
    //
    // state 用 HMAC 簽時間戳，避免有人誘導顧問點到偽造的 callback、
    // 把別人的 LinkedIn 綁進我們系統。10 分鐘內有效。
    if (p === '/linkedin/auth' || p === '/linkedin/callback') {
      const enc = new TextEncoder();
      const sign = async (msg) => {
        const key = await crypto.subtle.importKey('raw', enc.encode(env.LINKEDIN_CLIENT_SECRET || 'x'),
          { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
        const sig = await crypto.subtle.sign('HMAC', key, enc.encode(msg));
        return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
      };
      const REDIRECT = 'https://step1ne-recruit-api.aiagentg888.workers.dev/linkedin/callback';

      if (p === '/linkedin/auth') {
        if (!env.LINKEDIN_CLIENT_ID) return new Response('LinkedIn 尚未設定', { status: 503 });
        // 顧問要綁哪個帳號：?label=Jacky，之後發文才知道是誰的
        const label = url.searchParams.get('label') || '未命名';
        const ts = String(Date.now());
        const state = `${ts}.${encodeURIComponent(label)}.${await sign(ts + label)}`;
        const auth = 'https://www.linkedin.com/oauth/v2/authorization?' + new URLSearchParams({
          response_type: 'code', client_id: env.LINKEDIN_CLIENT_ID, redirect_uri: REDIRECT,
          state, scope: 'openid profile email w_member_social',
        });
        return Response.redirect(auth, 302);
      }

      // ── callback ──
      const code = url.searchParams.get('code');
      const state = url.searchParams.get('state') || '';
      const err = url.searchParams.get('error_description') || url.searchParams.get('error');
      const page = (title, body) => new Response(
        `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">`
        + `<title>${title}</title><body style="font-family:-apple-system,'PingFang TC',sans-serif;`
        + `max-width:620px;margin:60px auto;padding:0 20px;line-height:1.8;color:#12151a">`
        + `<h2 style="font-size:19px">${title}</h2>${body}</body>`,
        { headers: { 'content-type': 'text/html; charset=utf-8' } });

      if (err) return page('❌ 授權沒有完成', `<p>LinkedIn 回報：${err}</p><p>回去重按一次授權連結即可。</p>`);
      if (!code) return page('❌ 缺少授權碼', '<p>網址不完整，請重新從授權連結進來。</p>');

      const [ts, labelRaw, sig] = state.split('.');
      const label = decodeURIComponent(labelRaw || '');
      if (!ts || !sig || sig !== await sign(ts + label)) {
        return page('❌ 這個授權連結不是我們發出的', '<p>為了安全起見已擋下。請從後台重新取得授權連結。</p>');
      }
      if (Date.now() - Number(ts) > 10 * 60 * 1000) {
        return page('⌛ 授權連結過期了', '<p>超過 10 分鐘。回去重新按一次就好。</p>');
      }

      try {
        const tr = await fetch('https://www.linkedin.com/oauth/v2/accessToken', {
          method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            grant_type: 'authorization_code', code, redirect_uri: REDIRECT,
            client_id: env.LINKEDIN_CLIENT_ID, client_secret: env.LINKEDIN_CLIENT_SECRET,
          }),
        });
        const td = await tr.json();
        if (!td.access_token) {
          return page('❌ 換取權杖失敗', `<pre style="white-space:pre-wrap;font-size:12px">${
            JSON.stringify(td).slice(0, 400)}</pre>`);
        }
        // 拿本人 id，發文時 author 欄位要用
        const ur = await fetch('https://api.linkedin.com/v2/userinfo',
          { headers: { authorization: `Bearer ${td.access_token}` } });
        const ud = await ur.json();
        const sub = ud.sub || '';
        const expAt = new Date(Date.now() + (Number(td.expires_in) || 5184000) * 1000 + 8 * 3600 * 1000)
          .toISOString().replace('T', ' ').slice(0, 19);

        const exist = await env.DB.prepare(
          `SELECT id FROM social_accounts WHERE platform='linkedin' AND platform_user_id=?`
        ).bind(sub).first();

        // 2026-09-11 加：顧問自助綁 LinkedIn 時打的 label 常常跟他 Threads
        // 帳號的名字不一致（真實案例：Anna 打「Anna Wu」、Threads 那邊存的是
        // 「Anna」；Dan 打「H Dan」、Threads 存「Dan H」）——這會導致「一鍵
        // 發文」的顧問下拉選單把同一個人拆成兩筆看起來不相干的項目。
        // 用「分詞後有沒有共同字」抓可能是同一人的既有帳號，抓到就沿用
        // 那筆的 consultant_name，抓不到才用這次打的 label 當新的分組名。
        const words = (s) => String(s || '').toLowerCase().split(/[\s\-–]+/).filter(Boolean);
        const labelWords = words(label);
        const { results: others } = await env.DB.prepare(
          `SELECT DISTINCT consultant_name FROM social_accounts WHERE consultant_name IS NOT NULL`
        ).all();
        let consultantName = label;
        for (const o of others || []) {
          if (words(o.consultant_name).some((w) => labelWords.includes(w))) { consultantName = o.consultant_name; break; }
        }

        // 2026-09-14 加：顧問社群卡片（後台表單）會先建一筆「佔位」的 LinkedIn
        // 卡片（platform_user_id 還是空的，因為那個時候還沒授權），存好風格
        // 提示詞／LINE 連結／TG 主題。授權完成如果只用 platform_user_id 比對，
        // 一定找不到這筆佔位資料，就會照舊插入一筆全新的，佔位那筆的設定
        // 全部白填。這裡改成先找「同一個顧問、還沒授權過的佔位卡片」，
        // 找到就補齊 token 資訊，不要另外新建。
        const placeholder = exist ? null : await env.DB.prepare(
          `SELECT id FROM social_accounts WHERE platform='linkedin' AND consultant_name=?
             AND (platform_user_id IS NULL OR platform_user_id='') LIMIT 1`
        ).bind(consultantName).first();

        if (exist) {
          await env.DB.prepare(
            `UPDATE social_accounts SET access_token=?, refresh_token=?, token_expires_at=?,
                    label=?, consultant_name=?, is_active=1 WHERE id=?`
          ).bind(td.access_token, td.refresh_token || null, expAt,
                 `${label} – LinkedIn`, consultantName, exist.id).run();
        } else if (placeholder) {
          await env.DB.prepare(
            `UPDATE social_accounts SET platform_user_id=?, access_token=?, refresh_token=?,
                    token_expires_at=?, is_active=1 WHERE id=?`
          ).bind(sub, td.access_token, td.refresh_token || null, expAt, placeholder.id).run();
        } else {
          await env.DB.prepare(
            // created_at 是 NOT NULL 且沒有預設值——2026-08-19 第一次綁定就撞到
            `INSERT INTO social_accounts (platform, platform_user_id, access_token, refresh_token,
                                          token_expires_at, label, consultant_name, is_active, created_at)
             VALUES ('linkedin', ?, ?, ?, ?, ?, ?, 1, datetime('now','+8 hours'))`
          ).bind(sub, td.access_token, td.refresh_token || null, expAt, `${label} – LinkedIn`, consultantName).run();
        }

        const hasRefresh = !!td.refresh_token;
        await notify(env, `✅ LinkedIn 已綁定：${ud.name || label}\n`
          + `到期：${expAt.slice(0, 10)}\n`
          + (hasRefresh
              ? '這個 app 有拿到 refresh token，之後系統會自動續期，你不用管。'
              : '⚠️ 這個 app 沒有 refresh token，到期前要再授權一次（我會提前提醒）。'),
          { message_thread_id: THREAD.system });

        return page('✅ LinkedIn 綁定成功', `
          <p><b>${ud.name || label}</b> 已經可以用來發文了。</p>
          <p>權杖到期日：<b>${expAt.slice(0, 10)}</b></p>
          <p>${hasRefresh
              ? '✅ <b>這個 app 有給 refresh token</b>，之後系統會自動續期，你完全不用管。'
              : '⚠️ <b>這個 app 沒有給 refresh token</b>，到期前我會推 Telegram 提醒你重按一次授權。'}</p>
          <p style="color:#5d6672;font-size:13px">可以關掉這個分頁了。</p>`);
      } catch (e) {
        return page('❌ 綁定過程出錯', `<pre style="white-space:pre-wrap;font-size:12px">${
          String(e).slice(0, 300)}</pre>`);
      }
    }

    // ── 對外同步：給 pm.aijob.com.tw 拉資料用 ──
    // 用「他們來拉」而不是「我們推」：我們不知道對方的認證與資料結構，
    // 拉的一方自己控制時機、重試與欄位對應，出錯時也在他們那邊看得到。
    if (p.startsWith('/export/')) {
      const auth = request.headers.get('authorization') || '';
      if (!env.SYNC_TOKEN || !safeEqual(auth, `Bearer ${env.SYNC_TOKEN}`)) {
        return json(request, { ok: false, error: 'unauthorized' }, 401);
      }

      if (p === '/export/applications') {
        // 游標式增量：帶上次拿到的最大 created_at，只取更新的。
        // 用時間當游標而不是頁碼——頁碼會因為新資料插入而錯位。
        const since = url.searchParams.get('since') || '1970-01-01 00:00:00';
        const limit = Math.min(parseInt(url.searchParams.get('limit') || '100', 10), 500);
        const { results } = await env.DB.prepare(
          `SELECT id, created_at, job_slug, job_title, name, email, phone,
                  expected_salary, available_date, location_ok,
                  resume_url, note, utm_source, utm_medium, utm_campaign, referrer,
                  interview_mode, remind_at, status, consent_at,
                  CASE WHEN resume_file_id IS NULL THEN 0 ELSE 1 END AS has_resume_file
             FROM applications
            WHERE created_at > ?
            ORDER BY created_at ASC LIMIT ?`
        ).bind(since, limit).all();
        return json(request, {
          ok: true,
          count: results.length,
          // 下次帶這個值回來就接得上，不會漏也不會重複
          next_since: results.length ? results[results.length - 1].created_at : since,
          applications: results,
        });
      }

      // 履歷檔另外拿。夾在列表裡會讓每次同步都傳一堆用不到的大檔案。
      const m = p.match(/^\/export\/resume\/([\w-]+)$/);
      if (m) {
        // 2026-09-03改：原本自己重複一份查files/file_chunks的邏輯，
        // R2上線後沒跟著改會找不到新履歷——改叫共用的fileB64()。
        const app = await env.DB.prepare(`SELECT resume_file_id FROM applications WHERE id = ?`)
          .bind(m[1]).first();
        if (!app || !app.resume_file_id) return json(request, { ok: false, error: '沒有這份履歷' }, 404);
        const f = await fileB64(env, app.resume_file_id);
        if (!f) return json(request, { ok: false, error: '這份履歷沒有檔案內容' }, 404);
        return new Response(Uint8Array.from(atob(f.content), (c) => c.charCodeAt(0)), {
          headers: {
            'content-type': f.mime || 'application/octet-stream',
            'content-disposition': `attachment; filename="${encodeURIComponent(f.filename || 'resume')}"`,
          },
        });
      }
    }


    return json(request, { ok: false, error: 'not found' }, 404);
  },

  /** 排程：到了候選人自己選的時間就提醒他回來完成面談。 */
  async scheduled(_evt, env) {
    // 2026-09-02 拆分Phase 1：這裡原本有4段社群發文／LinkedIn相關的背景排程
    // （排程行事曆轉發文、LinkedIn權杖續期、發文成效回填、Threads貼文卡住對帳），
    // 已經搬到獨立的 step1ne-social-worker 專案，用它自己的cron跑，共用同一個D1。
    // 搬走的理由：這4段本來就跟這裡其餘的候選人/顧問通知邏輯完全不相關，
    // 卻擠在同一個cron裡搶CPU／D1讀取量。程式碼在
    // ~/工作流程技能包/step1ne-social-worker/src/index.js，要改那4段邏輯要去那邊改，
    // 不要在這裡加回來。

    // 2026-09-11 加：排程貼文「先核准、時間到才真的發」的第二段——顧問按確認
    // 那一刻如果還沒到排定時間（見上面 handleSocAction 裡的攔截），會停在
    // status='approved_scheduled'；這裡負責時間到了真的觸發發文。
    // ⚠️ 這段刻意留在這支 Worker，沒有搬去 step1ne-social-worker：真正發文
    // 用到的 TG_BOT_TOKEN／THREADS_ACCESS_TOKEN／handleSocAction() 都只在這裡，
    // 搬過去要嘛重複存一份金鑰，要嘛跨 Worker 呼叫，兩個都不划算。
    try {
      const { results: dueApproved } = await env.DB.prepare(
        `SELECT id, tg_message_id, tg_thread_id, approved_by FROM social_post_queue
          WHERE status='approved_scheduled'
            AND datetime(scheduled_at) <= datetime('now','+8 hours')
          LIMIT 20`
      ).all();
      for (const r of dueApproved || []) {
        // 重建一個假的 callback_query——handleSocAction() 只會用到這幾個欄位。
        // answer() 會打一次 answerCallbackQuery，cq.id 是假的所以那次呼叫必失敗，
        // 但那個 fetch 本來就包在 try/catch 裡，失敗不影響後面真正發文那段。
        const fakeCq = {
          id: `sched_${r.id}`,
          data: `soc_approve:${r.id}`,
          from: { username: r.approved_by || '排程' },
          message: {
            chat: { id: env.TG_CHAT_ID },
            message_id: r.tg_message_id,
            message_thread_id: r.tg_thread_id || undefined,
          },
        };
        await handleSocAction(env, fakeCq);
      }
    } catch (e) {
      await notify(env, `⚠️ 排程貼文到時間發布失敗：${String(e).slice(0, 200)}`, { message_thread_id: THREAD.system }).catch(() => {});
    }

    // 2026-08-13 加：阿財初審結束後的「1-1 顧問初審確認」，改成兩則不同時機的訊息
    // （Jacky 明確要求，不要用單一個「3 天」預設值）：
    //   ① 面談結束後 5 分鐘：說明接下來會怎樣，安撫「怎麼沒下文」的焦慮
    //   ② 隔 2 天，如果還沒有面談邀約、顧問也還沒回報過進度：主動問要不要催一下
    // cron 每 15 分鐘跑一次，5 分鐘的精準度做不到，但用「距今 ≥5 分鐘」當條件、
    // 搭配 postinterview_notified_at 防重複，最壞情況也就晚個幾分鐘，可以接受。
    try {
      const { results: freshDone } = await env.DB.prepare(
        `SELECT id, name, job_slug, job_title FROM applications
          WHERE interview_state IN ('done','paused')
            AND interview_ended_at IS NOT NULL
            AND interview_ended_at <= datetime('now','+8 hours','-5 minutes')
            AND postinterview_notified_at IS NULL
          LIMIT 30`
      ).all();
      if (freshDone && freshDone.length) {
        const { results: allBound } = await env.DB.prepare(
          `SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`
        ).all();
        for (const a of freshDone) {
          const bound = (allBound || []).filter((row) => safeJsonArray(row.application_ids).includes(a.id));
          const flex = {
            type: 'flex',
            altText: '您的初審已經完成，1-2 天內會有進一步消息',
            contents: {
              type: 'bubble',
              header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                contents: [
                  { type: 'text', text: '📋 初審已完成', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                  { type: 'text', text: a.job_title || a.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
                ] },
              body: { type: 'box', layout: 'vertical', paddingAll: '16px',
                contents: [{ type: 'text', size: 'sm', color: '#16202e', wrap: true,
                  text: '1-2 天內會審閱完成，若有任何需要確認的地方會主動通知您；有任何問題也歡迎直接訊息告知。\n\n如果確認符合條件，接下來會協助送審給用人單位，確認是否安排進一步面談。' }] },
            },
          };
          for (const b of bound) await linePushMessages(env, b.line_user_id, [flex]);
          await env.DB.prepare(`UPDATE applications SET postinterview_notified_at = ? WHERE id = ?`)
            .bind(nowTaipei(), a.id).run();
        }
      }
    } catch (e) {
      // 這則失敗不能擋到下面的提醒
    }

    try {
      // 2026-08-14 加：「已收到您的應徵」信——原本在 POST /apply 當下立刻寄，
      // 改成這裡延遲寄。條件：應徵滿 10 分鐘（cron 15 分鐘跑一次，精準度做不到
      // 到分鐘，但足夠讓「一口氣填完測驗」跟「中途離開」分開）、還沒寄過這封、
      // 而且還沒收到「面談連結」信（ready_notified_at）——如果已經收到面談連結信，
      // 代表測驗很快就填完了、系統也判定可以立即面談，這封「已收到」信只是
      // 重複資訊，跳過不寄。interview_state 已經不是 not_started 的也跳過，
      // 那種代表面談已經在進行或做完了，這封「開始測驗」的舊信對他來說是錯的資訊。
      const { results: freshApplied } = await env.DB.prepare(
        `SELECT id, name, email, job_slug, job_title, chat_token FROM applications
          WHERE superseded_by IS NULL
            AND applied_notified_at IS NULL
            AND ready_notified_at IS NULL
            AND (interview_state IS NULL OR interview_state = 'not_started')
            AND chat_token IS NOT NULL AND email IS NOT NULL AND email != ''
            AND created_at <= datetime('now','+8 hours','-10 minutes')
          LIMIT 30`
      ).all();
      for (const a of (freshApplied || [])) {
        try {
          await sendMail(
            env, a.email,
            `已收到您的應徵：${a.job_title || a.job_slug}`,
            [`${a.name} 您好，`,
             `已經收到您應徵「${a.job_title || a.job_slug}」的資料。`,
             `接下來分成兩步，兩個連結都在下面。請把這封信留著，這是您回來繼續的唯一入口。`],
            [
              { title: '第一步・工作風格測驗（5–7 分鐘）',
                body: '沒有標準答案，照直覺選就好，做完會馬上給您一份個人的工作風格報告。'
                    + '做完不一定要馬上面談——畫面會告訴您下一步怎麼走。',
                url: `https://step1ne.com/assessment/?t=${a.chat_token}`, text: '開始測驗' },
              { title: '第二步・初步面談（約 20–30 分鐘）',
                body: '由 AI 面談助理「阿財」進行，用打字的就可以。'
                    + '這個連結要先完成第一步才會開啟；如果是約定時間的面談，時間到我們也會再寄一次提醒。'
                    + '過程中有任何狀況，面談室右上角有「回報問題」按鈕，我們會即時收到。',
                url: `https://step1ne.com/interview/?t=${a.chat_token}`, text: '進入面談室' },
            ]
          );
          await env.DB.prepare(`UPDATE applications SET applied_notified_at = ? WHERE id = ?`)
            .bind(nowTaipei(), a.id).run();
        } catch { /* 單筆寄信失敗不擋其他人 */ }
      }
    } catch (e) {
      // 這則失敗不能擋到下面的提醒
    }

    try {
      // 條件：初審結束 ≥2 天、還沒有任何面談邀約（interview_appointments 一筆都沒有）、
      // 顧問也還沒透過「顧問人選回報區」回報過進度（handled_note 還是空的）——
      // 兩個都沒有才問，已經在安排中的人不要多問一次。
      const { results: stale2d } = await env.DB.prepare(
        `SELECT a.id, a.name, a.job_slug, a.job_title FROM applications a
          WHERE a.interview_state IN ('done','paused')
            AND a.interview_ended_at IS NOT NULL
            AND a.interview_ended_at <= datetime('now','+8 hours','-2 days')
            AND a.progress2day_pinged_at IS NULL
            AND (a.handled_note IS NULL OR a.handled_note = '')
            AND NOT EXISTS (SELECT 1 FROM interview_appointments ia WHERE ia.application_id = a.id)
          LIMIT 30`
      ).all();
      if (stale2d && stale2d.length) {
        const { results: allBound } = await env.DB.prepare(
          `SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`
        ).all();
        for (const a of stale2d) {
          const bound = (allBound || []).filter((row) => safeJsonArray(row.application_ids).includes(a.id));
          const flex = {
            type: 'flex',
            altText: '您的初審已經完成 2 天了，想更新一下進度嗎？',
            contents: {
              type: 'bubble',
              header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                contents: [
                  { type: 'text', text: '📋 進度提醒', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                  { type: 'text', text: a.job_title || a.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
                ] },
              body: { type: 'box', layout: 'vertical', paddingAll: '16px',
                contents: [
                  { type: 'text', text: `👤 ${a.name} 您好`, size: 'sm', color: '#8993a8' },
                  { type: 'separator', margin: 'md' },
                  { type: 'text', text: '您的初審已經完成 2 天了，還在審閱中。想主動催一下進度嗎？',
                    size: 'sm', color: '#16202e', wrap: true, margin: 'md' },
                ] },
              footer: { type: 'box', layout: 'vertical', paddingAll: '12px',
                contents: [{ type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
                  action: { type: 'postback', label: '🔔 提醒顧問看一下', data: `nudge_consultant:${a.id}`, displayText: '請顧問更新一下我的進度' } }] },
            },
          };
          for (const b of bound) await linePushMessages(env, b.line_user_id, [flex]);
          await env.DB.prepare(`UPDATE applications SET progress2day_pinged_at = ? WHERE id = ?`)
            .bind(nowTaipei(), a.id).run();
        }
      }
    } catch (e) {
      // 這則失敗不能擋到下面的提醒
    }

    // 報告產出超過 24 小時卻沒人處置 → 每天提醒一次。
    //
    // ⚠️ 2026-08-10 查出來的真實問題：VIP貴賓接待有 4 位談完，其中湯豐銘、范博翔
    // 阿財在 8/4 就判「值得轉給顧問」，但 consultant_decision 一直是 NULL，
    // 躺了六天沒人碰——而那是朋友私人協助的案子，對方在等人。
    // 根因是阿財推完報告就結束了，顧問當下沒空看，訊息就被後面的蓋掉，沒有任何機制再提醒。
    // 一天只吵一次（用 remind_at 存最後提醒日），不然會變成雜訊反而更沒人看。
    try {
      const today = nowTaipei().slice(0, 10);
      const { results: stalled } = await env.DB.prepare(
        `SELECT a.id, a.name, a.job_slug, j.title AS job_title, j.client_relation,
                r.created_at AS report_at,
                CAST((julianday('now','+8 hours') - julianday(r.created_at)) AS INT) AS days
           FROM applications a
           JOIN jobs j ON j.slug = a.job_slug
           JOIN reports r ON r.id = (SELECT r2.id FROM reports r2
                                      WHERE r2.application_id = a.id
                                      ORDER BY r2.created_at DESC LIMIT 1)
          WHERE r.consultant_decision IS NULL
            AND a.superseded_by IS NULL
            AND r.created_at <= datetime('now','+8 hours','-24 hours')
            AND COALESCE(a.stale_pinged_on,'') <> ?
          ORDER BY r.created_at ASC LIMIT 20`
      ).bind('stale_ping_' + today).all();

      if (stalled && stalled.length) {
        const REL = { private: '朋友私人協助', unsigned: '未簽約', signed: '已簽約' };
        const lines = stalled.map((s) =>
          `· ${s.name}　${s.job_title || s.job_slug}` +
          `${s.client_relation === 'private' ? '（朋友私人協助）' : ''}` +
          `\n　 報告產出 ${s.days} 天，還沒有人處置`);
        await notify(env,
          `⏳ 有 ${stalled.length} 位談完了但沒人處置\n\n${lines.join('\n')}\n\n` +
          `到後台按「推薦給客戶／備取／婉拒」就不會再提醒：\nhttps://step1ne.com/consultant/reports/`,
          // 2026-08-12 Jacky：跟 Pipeline 提醒一起搬到「顧問人選回報區」。
          // 提醒與回報在同一個主題，顧問不用切來切去。
          // ⚠️ 這裡原本寫死 2855，不是用 THREAD.decide——改動時很容易漏掉。
          { message_thread_id: THREAD.report });
        // 標記今天已提醒過，同一天不重複吵
        for (const s of stalled) {
          await env.DB.prepare(`UPDATE applications SET stale_pinged_on = ? WHERE id = ?`)
            .bind('stale_ping_' + today, s.id).run();
        }
      }
    } catch (e) {
      // 提醒失敗不可以影響下面候選人的面談提醒
    }

    // 到了候選人自己選的時間就提醒他回來做面談。
    // 這不是顧問的行程——AI 面談不需要顧問在場。
    const due = await env.DB.prepare(
      `SELECT id, name, email, job_title, remind_at, chat_token FROM applications
        WHERE status='scheduled' AND remind_at IS NOT NULL
          AND remind_at <= datetime('now','+8 hours')
          AND reminded_at IS NULL`
    ).all();
    for (const r of due.results) {
      // 之前這裡只通知顧問，候選人什麼都收不到——他自己選的時間到了卻沒人理他
      const sent = await sendMail(
        env, r.email,
        `提醒您：${r.job_title || ''} 初步面談`,
        [`${r.name} 您好，`,
         `這是您先前選擇的時間，AI 面談助理阿財已經準備好了。`,
         `點下面的連結就能開始，大約 20 到 30 分鐘。`,
         `如果現在不方便也沒關係，這個連結隨時都有效。`],
        { url: `https://step1ne.com/interview/?t=${r.chat_token}`, text: '開始面談' }
      );
      await notify(env, `⏰ 已提醒候選人：${r.name}　${r.job_title || ''}　（原訂 ${r.remind_at}）` +
        (sent ? '' : '\n⚠️ 提醒信寄送失敗，請手動聯繫'),
        { message_thread_id: THREAD.intake });
      await env.DB.prepare(`UPDATE applications SET reminded_at=? WHERE id=?`)
        .bind(nowTaipei(), r.id).run();
    }

    // 2026-08-14 加：阿福健檢預約的提醒，跟上面阿財那段一模一樣的邏輯，
    // 換成 checkups 這張表。
    const checkupDue = await env.DB.prepare(
      `SELECT id, name, email, remind_at, chat_token FROM checkups
        WHERE status='scheduled' AND remind_at IS NOT NULL
          AND remind_at <= datetime('now','+8 hours')
          AND reminded_at IS NULL`
    ).all();
    for (const r of checkupDue.results) {
      const sent = await sendMail(
        env, r.email,
        `提醒您：AI 履歷健檢對談`,
        [`${r.name} 您好，`,
         `這是您先前選擇的時間，阿福已經準備好了。`,
         `點下面的連結就能開始，大約 20 到 30 分鐘。`,
         `如果現在不方便也沒關係，這個連結隨時都有效。`],
        { url: `https://step1ne.com/checkup-chat/?t=${r.chat_token}`, text: '開始健檢對談' }
      );
      await notify(env, `⏰ 已提醒健檢預約：${r.name}（原訂 ${r.remind_at}）` +
        (sent ? '' : '\n⚠️ 提醒信寄送失敗，請手動聯繫'),
        { message_thread_id: CHECKUP_THREAD });
      await env.DB.prepare(`UPDATE checkups SET reminded_at=? WHERE id=?`)
        .bind(nowTaipei(), r.id).run();
    }

    // 婉拒的候選人：不在顧問按下去的當下就寄信，晚 3 小時再寄一封委婉的信。
    // 為什麼要延遲：秒回的婉拒信讓人覺得根本沒有人看過資料，只是機器蓋章拒絕；
    // 隔幾小時寄出，觀感上比較像「有人真的評估過」。
    // screen_decided_at 存的是台北時間，這裡 +5 小時等於「台北時間過了 3 小時」
    // （scheduled() 的 datetime('now') 是 UTC，UTC+8=台北，台北再 -3 小時＝UTC+5）。
    const declined = await env.DB.prepare(
      `SELECT id, name, email, job_title FROM applications
        WHERE screen_decision='declined' AND decline_email_sent_at IS NULL
          AND screen_decided_at IS NOT NULL
          AND screen_decided_at <= datetime('now','+5 hours')`
    ).all();
    for (const r of declined.results) {
      const sent = await sendMail(
        env, r.email,
        `應徵進度通知：${r.job_title || ''}`,
        [`${r.name} 您好，`,
         `感謝您應徵「${r.job_title || ''}」，資料已經看過了。`,
         `這次的職缺條件與您目前的狀況還有一些落差，這次暫時不會安排進一步面談。`,
         `您的資料會保留，未來若有更適合的機會，會再主動與您聯繫。`,
         `再次謝謝您撥空應徵，也祝您求職順利。`]
      );
      await notify(env, `📧 已寄出婉拒通知：${r.name}　${r.job_title || ''}` +
        (sent ? '' : '\n⚠️ 信件寄送失敗，請手動聯繫'),
        { message_thread_id: THREAD.system });
      await env.DB.prepare(`UPDATE applications SET decline_email_sent_at=? WHERE id=?`)
        .bind(nowTaipei(), r.id).run();
    }

    // 第二階段面試（顧問安排面談時段那個功能）前一天提醒候選人——
    // 2026-08-13 Jacky 要求。跟阿財初審那組提醒是完全不同的表／不同的邏輯，
    // 不要混在一起改。用台北時間 09:00–09:14 這格當窗口，一天只提醒一次；
    // reminded_at 存了就不會重複推播。
    try {
      const tpe2 = new Date(Date.now() + 8 * 3600 * 1000);
      if (tpe2.getUTCHours() === 9 && tpe2.getUTCMinutes() < 15) {
        const tomorrow = new Date(tpe2.getTime() + 24 * 3600 * 1000).toISOString().slice(0, 10);
        const { results: tmr } = await env.DB.prepare(
          `SELECT ia.id, ia.confirmed_slot, ia.application_id,
                  a.name, a.job_title, a.job_slug
             FROM interview_appointments ia
             JOIN applications a ON a.id = ia.application_id
            WHERE ia.status = 'confirmed' AND ia.reminded_at IS NULL
              AND substr(ia.confirmed_slot, 1, 10) = ?`
        ).bind(tomorrow).all();
        for (const r of (tmr || [])) {
          const timeStr = (r.confirmed_slot || '').slice(11, 16);
          const { results: hits } = await env.DB.prepare(
            `SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`
          ).all();
          const bound = (hits || []).filter((row) => safeJsonArray(row.application_ids).includes(r.application_id));
          for (const b of bound) {
            await linePush(env, b.line_user_id,
              `⏰ 提醒您，明天（${tomorrow.slice(5).replace('-', '/')}）${timeStr} 有一場「${r.job_title || r.job_slug}」的第二階段面試，別忘記囉！`);
          }
          await notify(env,
            `⏰ 已提醒候選人明天面試：${r.name}（${r.job_title || r.job_slug}）${tomorrow.slice(5).replace('-', '/')} ${timeStr}`
            + (bound.length ? '' : '\n⚠️ 這位沒綁 LINE，系統推不到他，麻煩你自己聯繫一下'),
            { message_thread_id: THREAD.system });
          await env.DB.prepare(`UPDATE interview_appointments SET reminded_at = ? WHERE id = ?`)
            .bind(nowTaipei(), r.id).run();
        }
      }
    } catch (e) {
      // 提醒失敗不可以影響下面的 pipeline 提醒
    }

    await pipelineReminders(env);
    await candidateCareTick(env);
  },
};

// 2026-08-13 加：報到前準備事項（7b）＋到職關懷第 1/3/7/28 天（8）。
// ⚠️ 這是 candidate_care_log，不是 pipelineReminders 那個 care_log——
// care_log 是提醒「顧問」去關心（保證期關懷，7/20/30…天點），
// 這支是系統直接推播「候選人」本人的到職關懷卡，完全不同的機制、不同的收件人。
async function candidateCareTick(env) {
  // ── 7b：報到前準備事項 ──
  // 距離報到 ≤7 天才發（不到 7 天內定的，第一次檢查到就會直接落在這個窗口內，
  // 等於實際發送時間就是「提前 3 天內」，跟 Jacky 的兩種情境自然對得上，
  // 不用另外寫一套「原本有沒有滿一週」的判斷）。
  try {
    const { results: prepDue } = await env.DB.prepare(
      `SELECT p.id, p.application_id, p.job_slug, p.job_title, p.candidate_name, p.onboard_date
         FROM placements p
        WHERE p.stage = 'GUARANTEE' AND p.onboard_date IS NOT NULL
          AND p.prep_reminded_at IS NULL
          AND julianday(p.onboard_date) - julianday('now','+8 hours') <= 7
          AND julianday(p.onboard_date) - julianday('now','+8 hours') > 0
        LIMIT 30`
    ).all();
    for (const p of prepDue || []) {
      if (!p.application_id) continue;
      const job = await env.DB.prepare(`SELECT onboarding_prep_note FROM jobs WHERE slug = ?`).bind(p.job_slug).first();
      const prepNote = job && job.onboarding_prep_note;
      if (!prepNote) {
        // 沒填就不硬發一張空白卡，但要讓顧問知道去補——不然候選人這則會直接漏掉
        await notify(env,
          `⚠️ ${p.candidate_name}（${p.job_title}）快到職了，但這個職缺還沒填「報到前準備事項」，`
          + `請到 https://step1ne.com/consultant/jobs/ 補上，不然候選人收不到這則提醒`,
          { message_thread_id: THREAD.system });
        await env.DB.prepare(`UPDATE placements SET prep_reminded_at = ? WHERE id = ?`).bind(nowTaipei(), p.id).run();
        continue;
      }
      const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
      const hits = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(p.application_id));
      const [d, t] = String(p.onboard_date).split(' ');
      const flex = {
        type: 'flex', altText: `報到前準備事項：${p.job_title}`,
        contents: {
          type: 'bubble',
          header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
            contents: [
              { type: 'text', text: '📋 報到前準備事項', color: '#8fb0ff', size: 'xs', weight: 'bold' },
              { type: 'text', text: p.job_title || p.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
            ] },
          body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
            contents: [
              { type: 'text', text: `👤 ${p.candidate_name} 您好`, size: 'sm', color: '#8993a8' },
              { type: 'text', text: `🗓 報到日：${d}${t ? ' ' + t : ''}`, size: 'sm', color: '#8993a8', wrap: true },
              { type: 'separator', margin: 'md' },
              { type: 'text', text: prepNote, size: 'sm', color: '#16202e', wrap: true, margin: 'md' },
            ] },
          footer: { type: 'box', layout: 'vertical', paddingAll: '12px',
            contents: [{ type: 'button', style: 'secondary', height: 'sm',
              action: { type: 'postback', label: '聯繫顧問', data: `contact_consultant:${p.application_id}`, displayText: '我對報到有問題想請顧問協助' } }] },
        },
      };
      for (const b of hits) await linePushMessages(env, b.line_user_id, [flex]);
      await env.DB.prepare(`UPDATE placements SET prep_reminded_at = ? WHERE id = ?`).bind(nowTaipei(), p.id).run();
    }
  } catch (e) {
    // 這則失敗不能擋到下面的到職關懷
  }

  // ── 8：到職關懷 第 1／3／7／28 天 ──
  // 只在傍晚 18:30–18:44 這格窗口發（Jacky 確認過的「下班後」時間點），
  // 一天只會命中一次；candidate_care_log 記錄已經發過哪些天數，避免重複。
  const tpe = new Date(Date.now() + 8 * 3600 * 1000);
  if (tpe.getUTCHours() !== 18 || tpe.getUTCMinutes() < 30 || tpe.getUTCMinutes() >= 45) return;

  const CARE_TEXT = {
    1: '第一天上班辛苦了！環境跟同事還算好相處嗎？剛開始難免會有點生疏，有任何狀況都可以直接跟顧問說 😊',
    3: '到職滿 3 天了，這幾天下來還適應嗎？有任何狀況都可以直接跟顧問說，不用不好意思 😊',
    7: '到職滿一週了，工作內容跟一開始想的差不多嗎？如果有落差或想聊的，顧問都在 🙌',
    28: '到職滿一個月了，恭喜順利度過剛開始最需要適應的階段！之後有任何狀況，顧問還是隨時都在 😊',
  };
  const CARE_HEADER = { 1: '💚 到職第一天', 3: '💚 到職關懷', 7: '💚 到職關懷', 28: '💚 到職關懷' };
  const POINTS = [1, 3, 7, 28];

  try {
    const { results: rows } = await env.DB.prepare(
      `SELECT p.id, p.application_id, p.job_slug, p.job_title, p.candidate_name, p.candidate_care_log,
              CAST(julianday('now','+8 hours') - julianday(p.onboard_date) AS INT) AS days_since
         FROM placements p
        WHERE p.stage = 'GUARANTEE' AND p.onboard_date IS NOT NULL
          AND julianday('now','+8 hours') >= julianday(p.onboard_date)
        LIMIT 60`
    ).all();
    for (const p of rows || []) {
      if (!p.application_id) continue;
      let done = [];
      try { done = JSON.parse(p.candidate_care_log || '[]'); } catch { done = []; }
      const hit = POINTS.filter((pt) => p.days_since >= pt && !done.includes(pt));
      if (!hit.length) continue;
      const point = Math.max(...hit);

      const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
      const bound = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(p.application_id));
      const flex = {
        type: 'flex', altText: CARE_TEXT[point],
        contents: {
          type: 'bubble',
          header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
            contents: [
              { type: 'text', text: CARE_HEADER[point], color: '#8fb0ff', size: 'xs', weight: 'bold' },
              { type: 'text', text: p.job_title || p.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
            ] },
          body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
            contents: [
              { type: 'text', text: `👤 ${p.candidate_name} 您好`, size: 'sm', color: '#8993a8' },
              { type: 'separator', margin: 'md' },
              { type: 'text', text: CARE_TEXT[point], size: 'sm', color: '#16202e', wrap: true, margin: 'md' },
            ] },
          footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
            contents: [
              { type: 'box', layout: 'horizontal', spacing: 'sm', contents: [
                { type: 'button', style: 'primary', color: '#1f8f5f', height: 'sm',
                  action: { type: 'postback', label: '👍 一切順利', data: `care_resp:ok:${p.application_id}:${point}`, displayText: '一切順利' } },
                { type: 'button', style: 'secondary', height: 'sm',
                  action: { type: 'postback', label: '💬 想聊聊', data: `care_resp:talk:${p.application_id}:${point}`, displayText: '我想聊聊' } },
              ] },
            ] },
        },
      };
      for (const b of bound) await linePushMessages(env, b.line_user_id, [flex]);

      const merged = Array.from(new Set([...done, ...hit])).sort((a, b) => a - b);
      await env.DB.prepare(`UPDATE placements SET candidate_care_log = ? WHERE id = ?`)
        .bind(JSON.stringify(merged), p.id).run();
    }
  } catch (e) {
    // 靜默失敗——這是加值功能，不影響其他排程
  }
}

// ── 送件之後的提醒（P5–P8）─────────────────────────────────────────
//
// 兩種提醒的邏輯完全不同，reminder_rules.md 明說不能混在一起判斷：
//   停滯偵測    = 異常才提醒（卡太久）
//   保證期關懷  = 照表操課，正常也要提醒（預防性維繫，不是異常偵測）
// 混在一起會讓保證期關懷變成「沒事就不提醒」，違背主動維繫的目的。
//
// ⚠️ 這裡只提醒顧問去跟進，**不會**代替顧問聯繫候選人或客戶——關係維繫是人的事。

// 閾值來源：pipeline-care-tracker/references/reminder_rules.md 的預設值。
// 注意 data_model.md 的「正常時間感」講法（OFFER_PENDING 一週、ONBOARDING 兩三週）
// 跟這裡不同——那份是描述性的，這份才是閾值定義。以這份為準，儀表板也已對齊。
const STALL_LIMIT = { SUBMITTED: 5, INTERVIEWING: 3, OFFER_PENDING: 3, ONBOARDING: 7 };

// 保證期關懷時間點。規律：第一週一定要關懷一次（最容易發現到職後的落差感），
// 之後大約每 30 天一次，結束前留 10–15 天緩衝（真的要補位時間才來得及）。
const CARE_POINTS = { 30: [7, 20], 60: [7, 30, 50], 90: [7, 30, 60, 80], 120: [7, 30, 60, 90, 110] };

async function pipelineReminders(env) {
  // 排程是每 15 分鐘一次（為了面談提醒），但這兩種提醒一天只該講一次。
  // 用台北時間的 09:00–09:14 這一格當窗口，剛好只會命中一次。
  const tpe = new Date(Date.now() + 8 * 3600 * 1000);
  if (tpe.getUTCHours() !== 9 || tpe.getUTCMinutes() >= 15) return;

  const { results } = await env.DB.prepare(
    `SELECT id, application_id, client_id, candidate_name, job_title, client_name, stage, onboard_date,
            guarantee_days, care_log,
            CAST(julianday('now','+8 hours') - julianday(stage_since) AS INTEGER)  AS days_in_stage,
            CAST(julianday('now','+8 hours') - julianday(onboard_date) AS INTEGER) AS days_since_onboard
       FROM placements
      WHERE stage NOT IN ('CLOSED_WON','CLOSED_LOST','CLOSED_INTERNAL')`
  ).all();
  const rows = results || [];
  if (!rows.length) return;

  // 2026-09-04 加：這套提醒完全不知道「用人單位在portal按不推進」或
  // 「顧問自己在初篩報告判不推薦」這兩件事——呂皓宇真實案例：顧問7/30就
  // 判定不推薦了，這裡完全沒發現，卡在AWAITING_CLIENT_FEEDBACK提醒了31天。
  // 查一次這兩種「已經有結論」的來源，命中的直接排除，不進停滯偵測。
  const { results: rejFwd } = await env.DB.prepare(
    `SELECT application_id, company_id FROM candidate_forwards WHERE client_rejected_at IS NOT NULL`
  ).all();
  const rejectedSet = new Set((rejFwd || []).map((r) => r.application_id + '|' + r.company_id));
  const { results: allReports } = await env.DB.prepare(
    `SELECT application_id, consultant_decision FROM reports ORDER BY application_id, created_at DESC`
  ).all();
  const latestDecision = new Map();
  for (const r of (allReports || [])) {
    if (!latestDecision.has(r.application_id)) latestDecision.set(r.application_id, r.consultant_decision);
  }
  const isAlreadyDecided = (r) =>
    rejectedSet.has(r.application_id + '|' + r.client_id) || latestDecision.get(r.application_id) === 'rejected';

  // ── 一、停滯偵測。只列需要注意的，正常進行中的不列——
  //     全部列出來只會讓真正該關心的被淹沒。
  const over = [], near = [];
  for (const r of rows) {
    if (isAlreadyDecided(r)) continue;        // 已經有結論（顧問不推薦／客戶婉拒），不是卡住
    const lim = STALL_LIMIT[r.stage];
    if (!lim) continue;                       // GUARANTEE 不做停滯偵測，它走下面的關懷排程
    const d = r.days_in_stage ?? 0;
    const line = `${r.candidate_name}｜${r.job_title}｜${r.client_name}　卡 ${d} 天`;
    if (d > lim) over.push(line);
    else if (d >= lim * 0.8) near.push(line);
  }

  // ── 二、保證期關懷。到了時間點就提醒，即使一切正常。
  const care = [];
  for (const r of rows) {
    if (r.stage !== 'GUARANTEE' || !r.onboard_date) continue;
    const pts = CARE_POINTS[r.guarantee_days] || CARE_POINTS[90];
    let done = [];
    try { done = JSON.parse(r.care_log || '[]'); } catch { done = []; }
    const d = r.days_since_onboard ?? 0;
    // 命中的時間點：已經到了、而且還沒關懷過。用 >= 才不會因為那天剛好沒跑到就永遠錯過。
    const hit = pts.filter(p => d >= p && !done.includes(p));
    if (!hit.length) continue;
    const p = Math.max(...hit);
    const nth = pts.indexOf(p) + 1;
    const last = done.length ? `上次第 ${Math.max(...done)} 天` : '還沒關懷過';
    const isLast = nth === pts.length;
    care.push(`${r.candidate_name}｜${r.client_name}　入職第 ${d} 天` +
      `\n   第 ${nth}/${pts.length} 次關懷${isLast ? '（保證期最後一次，語氣要不一樣）' : ''}　${last}`);
    await env.DB.prepare(`UPDATE placements SET care_log=?, updated_at=datetime('now','+8 hours') WHERE id=?`)
      .bind(JSON.stringify([...new Set([...done, ...hit])].sort((a, b) => a - b)), r.id).run();
  }

  if (!over.length && !near.length && !care.length) return;   // 沒事就不要吵

  const msg = [];
  if (over.length) msg.push(`🔴 已逾期，該去跟進\n${over.map(x => '・' + x).join('\n')}`);
  if (near.length) msg.push(`🟡 快到期\n${near.map(x => '・' + x).join('\n')}`);
  if (care.length) msg.push(`💚 該關懷了（正常也要問）\n${care.map(x => '・' + x).join('\n')}`);
  msg.push('https://workflow-os-due.pages.dev/');
  // 2026-08-12 Jacky：這則改發「顧問人選回報區」。
  // 理由是動線——提醒在哪裡跳出來，顧問就在哪裡回報，不用切主題。
  // 「該去跟進誰」跟「跟進完了回報一句」本來就是同一件事的兩端。
  await notify(env, '📋 招募 Pipeline 提醒\n\n' + msg.join('\n\n')
        + '\n\n跟進完直接在這個主題講一句就好，例如「張博州客戶約週四面試」，我會幫你更新狀態。',
        { message_thread_id: THREAD.report });
}
