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

  const fileId = uid();
  const parts = [];
  for (let i = 0; i < b64.length; i += CHUNK) parts.push(b64.slice(i, i + CHUNK));

  const stmts = [env.DB.prepare(
    `INSERT INTO files (id, created_at, filename, mime, size, chunks) VALUES (?,?,?,?,?,?)`
  ).bind(fileId, now, b.resume_name || 'resume', b.resume_mime || 'application/pdf',
         bytes, parts.length)];
  parts.forEach((part, idx) => stmts.push(env.DB.prepare(
    `INSERT INTO file_chunks (file_id, idx, b64) VALUES (?,?,?)`
  ).bind(fileId, idx, part)));
  // batch 是同一個交易——中途失敗不會留下半份履歷
  await env.DB.batch(stmts);
  return { fileId, chunks: parts.length };
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

  const fileId = uid();
  const parts = [];
  for (let i = 0; i < b64.length; i += CHUNK) parts.push(b64.slice(i, i + CHUNK));

  const stmts = [env.DB.prepare(
    `INSERT INTO files (id, created_at, filename, mime, size, chunks) VALUES (?,?,?,?,?,?)`
  ).bind(fileId, now, file.name || 'upload', file.mime || 'application/octet-stream',
         bytes, parts.length)];
  parts.forEach((part, idx) => stmts.push(env.DB.prepare(
    `INSERT INTO file_chunks (file_id, idx, b64) VALUES (?,?,?)`
  ).bind(fileId, idx, part)));
  await env.DB.batch(stmts);
  return { fileId, chunks: parts.length, name: file.name || 'upload' };
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
async function sendMail(env, to, subject, lines, cta) {
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

async function fileB64(env, fileId) {
  if (!fileId) return null;
  const f = await env.DB.prepare(
    `SELECT filename, mime, content_b64, chunks FROM files WHERE id = ?`).bind(fileId).first();
  if (!f) return null;
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
    let resumeBuf = null, resumeName = null;
    if (app.resume_file_id) {
      const f = await env.DB.prepare(
        `SELECT filename, mime, content_b64, chunks, id AS fid FROM files WHERE id = ?`
      ).bind(app.resume_file_id).first();
      if (f) {
        let b64 = f.content_b64;
        if (!b64 && f.chunks) {
          const { results } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id = ? ORDER BY idx ASC`
          ).bind(f.fid).all();
          b64 = (results || []).map((r) => r.b64).join('');
        }
        if (b64) { resumeBuf = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)); resumeName = f.filename || 'resume.pdf'; }
      }
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
async function triggerJobForms(env, { applicationId, companyId, jobSlug, by }) {
  if (!jobSlug) return;
  try {
    const { results: forms } = await env.DB.prepare(
      `SELECT id, name FROM job_forms WHERE job_slug=?`
    ).bind(jobSlug).all();
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
      await sendMail(env, app.email,
        `請填寫「${form.name}」－ ${job ? job.title : ''}`,
        [
          `${app.name || '您好'}：`,
          `恭喜進入「${company ? company.display_name : '用人單位'}」${job ? job.title : ''}這個職缺的用人單位審核階段，麻煩點下方按鈕填寫「${form.name}」，請於 2 天內完成上傳，謝謝配合！`,
        ],
        { url: link, text: `填寫${form.name}` }
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
  const text = String(ev.message.text || '').trim();
  const now = nowTaipei();

  let binding = await env.DB.prepare(
    `SELECT * FROM line_bindings WHERE line_user_id = ?`
  ).bind(userId).first();

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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === 'OPTIONS') return new Response(null, { headers: cors(request) });
    if (p === '/health') return json(request, { ok: true });

    // 2026-08-17 加：公開查詢「哪些職缺目前關閉」——網站是純靜態頁面
    // （職缺列表、應徵表單的職缺下拉選單都是寫死的 HTML/JSON），顧問在後台
    // 按開關不會自動改到這些檔案，所以前端要另外打這支即時確認，才能做到
    // 「後台按一下、網站馬上反映」而不用每次改完狀態都重新部署網站。
    // 不需要權杖——這本來就是網站訪客要看到的公開資訊。
    if (p === '/jobs-closed' && request.method === 'GET') {
      const { results } = await env.DB.prepare(`SELECT slug FROM jobs WHERE status = 'closed'`).all();
      return json(request, { ok: true, closed: (results || []).map((r) => r.slug) });
    }

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

    // ── 用人需求表 One-Page 企業客戶入口 ──
    //
    // 一家企業客戶可能對到多個 jobs，「專屬長期網址」綁在公司層級（client_companies），
    // 不是綁在單一職缺。認證方式比照 applications.chat_token：portal_token 是唯一憑證，
    // 拿到連結就能填，不設過期——要收回存取權就在後台重新產生 token。
    //
    // PORTAL_FIELDS 是白名單：只有這些欄位會被回傳／允許被企業客戶寫入。
    // 刻意排除 client_screen_conditions／notes／faq_notes 這幾個顧問內部限定欄位——
    // 不能因為做了對外入口就把顧問內部備註洩漏出去。
    // 2026-08-25 修：原本把 client_screen_conditions／faq_notes／salary_note／client_name
    // 也擋掉了，理由是「內部欄位」——但這幾個其實是企業客戶自己講的職缺內容
    // （他們的用人條件、FAQ、薪資說明、公司名稱），是要拿來對焦用的，不是我們的東西，
    // 不該擋。真正該擋的只有 notes（顧問寫給顧問自己看的內部工作筆記，跟職缺內容無關）
    // 跟 scoring_notes（阿財的評分方法論，是 STEP1NE 自己的招募know-how，不是他們的職缺資料）。
    // 2026-08-26 加：禁刊過濾器，從 jobintake/publishing_filters.py 搬過來的同一套
    // 規則（純規則，不用 AI——這是法遵，不是文筆，同一份文字每次都要擋一樣的東西，
    // 不能靠 LLM 判斷，判斷不保證每次一致）。Telegram 自建職缺那條路本來就會經過
    // 這關（draft_job.py 呼叫 Python 版），但用人需求表 portal 這條路（客戶自己填）
    // 完全沒有這一關——客戶送審後，顧問核准前完全看不到有沒有違法/洩密內容。
    // 這裡在顧問查看待審核職缺時即時掃一次，把命中的地方列出來，顧問才知道
    // 「這份要改什麼才能刊」，不是盲目按核准。
    const PUBLISH_FILTER_RULES = [
      ['年齡歧視', /(限|需|須|僅限|只收|徵)?\s*\d{2}\s*歲\s*(以下|以內|(以)?上)/g, '違反就業服務法第5條（年齡歧視），刊出會被裁罰'],
      ['年齡歧視', /年齡\s*[:：]?\s*\d{2}\s*[-–~至]\s*\d{2}/g, '違反就業服務法第5條（年齡歧視）'],
      ['年齡歧視', /(未滿|不超過|不得超過)\s*\d{2}\s*歲/g, '違反就業服務法第5條（年齡歧視）'],
      ['年齡歧視', /(年輕|年紀輕|六年級|七年級|八年級|九年級)(生|後)?(佳|尤佳|優先)?/g, '間接年齡指涉，實務上同樣認定為年齡歧視'],
      ['性別歧視', /(限|僅限|只收|徵|需|以)\s*(男|女)(性|生|士)?(為主|佳|尤佳|優先)?/g, '違反就業服務法第5條（性別歧視）'],
      ['性別歧視', /(男|女)(性|生)\s*(佳|尤佳|優先|為佳)/g, '違反就業服務法第5條（性別歧視）'],
      ['婚育歧視', /(已婚|未婚|單身|需已婚|限未婚)/g, '違反就業服務法第5條（婚姻歧視）'],
      ['婚育歧視', /(無|不得有|近期無)\s*(懷孕|生育|婚育|生子)\s*(計畫|規劃|打算)?/g, '違反就業服務法第5條（懷孕歧視），這條裁罰最重'],
      ['國籍歧視', /(限|僅限|只收|需|須)\s*(具備|具|有)?\s*(本國|台灣|中華民國|國)籍/g, '違反就業服務法第5條（國籍歧視）'],
      ['國籍歧視', /(不收|不用|排除|不考慮)\s*(外籍|外勞|移工|陸籍|外國人)/g, '違反就業服務法第5條（國籍歧視）'],
      ['容貌歧視', /(五官端正|形象佳|外型佳|外貌佳|身高\s*\d{3}|體重\s*\d{2,3}|口齒清晰且外型)/g, '違反就業服務法第5條（容貌歧視）'],
      ['身心障礙歧視', /(不收|不適合|排除)\s*(身心障礙|殘障|身障)/g, '違反就業服務法第5條（身心障礙歧視）'],
      ['內部獵頭策略', /(建議鎖定|建議搜尋|搜尋職稱|目標公司|挖角|對標公司|競品名單|人選來源建議)/g, '等於把挖角名單公開給同業看'],
      ['內部溝通備註', /(客戶(最初|原本|一開始)說|建議二次確認|與確認的.{0,10}有落差|內部備註|待客戶回覆)/g, '這是我們跟客戶之間的往來，不是給求職者看的'],
      ['未確認欄位', /(待確認|待補|未確認|\?\?|待客戶確認|TBD|tbd)/g, '不刊未驗證資訊——刊錯的工作條件會害候選人白跑一趟'],
      ['內部作業口吻', /(由\s*Step1ne\s*投保|由我方投保|我司投保|派遣公司為本公司)/g, '這是內部作業說明，寫在頁面上會讓求職者覺得自己是貨物'],
    ];
    function scanPublishFilters(text, clientName) {
      text = text || '';
      const hits = [];
      const context = (i, j) => {
        const a = Math.max(0, i - 20), b = Math.min(text.length, j + 20);
        return (a > 0 ? '…' : '') + text.slice(a, b).replace(/\n/g, ' ') + (b < text.length ? '…' : '');
      };
      for (const [kind, re, why] of PUBLISH_FILTER_RULES) {
        re.lastIndex = 0;
        let m;
        while ((m = re.exec(text))) {
          hits.push({ kind, matched: m[0].trim(), context: context(m.index, m.index + m[0].length), why });
          if (m[0] === '') re.lastIndex++; // 避免零寬配對造成無窮迴圈
        }
      }
      if (clientName) {
        const stem = clientName.replace(/(股份)?有限公司$|公司$|集團$|企業社$/, '').trim();
        for (const name of new Set([clientName, stem].filter((n) => n && n.length >= 2))) {
          const re2 = new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
          let m;
          while ((m = re2.exec(text))) {
            hits.push({ kind: '委任客戶名稱', matched: m[0], context: context(m.index, m.index + m[0].length),
              why: '客戶保密義務。頁面不可出現，請改用產業描述' });
          }
        }
      }
      // 去重
      const seen = new Set(); const out = [];
      for (const h of hits) {
        const key = `${h.kind}|${h.matched}|${h.context}`;
        if (seen.has(key)) continue;
        seen.add(key); out.push(h);
      }
      return out;
    }

    const PORTAL_FIELDS = [
      'client_name', 'title', 'client_intro', 'hiring_manager', 'years_min', 'must_skills',
      'client_screen_conditions', 'faq_notes', 'salary_note',
      'salary_min', 'salary_max', 'salary_unit', 'locations', 'employment', 'onboard_by',
      'team_size', 'interview_rounds', 'interview_stage_config', 'interview_who', 'has_test',
      'client_contact_name', 'client_contact_phone', 'headcount', 'work_mode',
      'work_hours', 'leave_policy', 'employment_period', 'overtime_policy',
      'hiring_reason', 'urgency', 'main_duties', 'reports_to', 'leads_team',
      'education_level', 'required_conditions', 'language_requirement',
      'nice_to_have_skills', 'preferred_background', 'personality_traits',
      'salary_tier_table', 'salary_structure_note', 'benefits_detail',
      'dispatch_to_permanent_policy', 'off_limits_note',
      'dispatch_client', 'department', 'work_environment_ratio', 'attendance_method',
      'interview_process', 'dispatch_range', 'contract_terms_note', 'overtime_detail',
      'onboarding_prep_note',
    ];

    if (p === '/portal' || p.startsWith('/portal/')) {
      const sub = p.slice('/portal'.length) || '/';
      const parts = sub.split('/').filter(Boolean); // [token] 或 [token, 'jobs', slug]
      const token = parts[0] || '';
      if (!token) return json(request, { ok: false, error: '缺少存取連結' }, 400);

      const company = await env.DB.prepare(
        `SELECT id, display_name, contact_email, tg_topic_id FROM client_companies WHERE portal_token = ?`
      ).bind(token).first();
      if (!company) return json(request, { ok: false, error: '連結無效，請聯繫顧問重新發送' }, 404);

      // GET /portal/:token — 回傳公司資訊＋這家公司底下所有職缺（只挑白名單欄位）
      if (parts.length === 1 && request.method === 'GET') {
        const cols = ['slug', 'status', 'closed_by', 'closed_reason', ...PORTAL_FIELDS].join(', ');
        const { results } = await env.DB.prepare(
          `SELECT ${cols} FROM jobs WHERE company_id = ? ORDER BY slug`
        ).bind(company.id).all();
        return json(request, {
          ok: true,
          company: { displayName: company.display_name, contactEmail: company.contact_email },
          jobs: results || [],
          fields: PORTAL_FIELDS,
        });
      }

      // POST /portal/:token/jobs/:slug/submit — 客戶填完，正式送出審核。
      // 這一步之前（client_draft）客戶可以自己隨便存、隨便改，顧問後台完全看不到；
      // 按下這顆才鎖住、才出現在顧問的待審核清單。核心欄位沒填齊不准送——
      // 不然顧問審的還是一堆空欄位，等於沒改善。
      if (parts.length === 4 && parts[1] === 'jobs' && parts[3] === 'submit' && request.method === 'POST') {
        const slug = parts[2];
        const owned = await env.DB.prepare(
          `SELECT slug, title, status, salary_min, main_duties, locations FROM jobs
             WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        if (owned.status !== 'client_draft') {
          return json(request, { ok: false, error: '這個職缺目前不是可送審的狀態' }, 400);
        }
        const missing = [];
        if (!String(owned.title || '').trim()) missing.push('職缺名稱');
        if (!owned.salary_min) missing.push('薪資');
        if (!String(owned.main_duties || '').trim()) missing.push('工作內容');
        if (!String(owned.locations || '').trim()) missing.push('工作地點');
        if (missing.length) {
          return json(request, { ok: false, error: `還缺這些欄位才能送審：${missing.join('、')}` }, 400);
        }
        const now = nowTaipei();
        await env.DB.prepare(`UPDATE jobs SET status='pending_review', updated_at=? WHERE slug=?`)
          .bind(now, slug).run();
        notify(env,
          `🆕 ${company.display_name} 送出用人需求表審核：「${owned.title || slug}」\n` +
          `到「客戶資訊」分頁看完整內容、核准或拒絕：https://step1ne.com/consultant/client-info/`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, slug, status: 'pending_review' });
      }

      // POST /portal/:token/jobs/:slug/close-hire — 客戶自己找到人了，結束招募。
      // 效果全部沿用既有機制：GET /jobs-closed 會讓公開頁面隱藏這個職缺、
      // POST /apply 會擋新應徵，已經在談的候選人不受影響——這兩件事都不用
      // 另外寫，只要把 status 設成 closed 就白拿。這裡只負責權限檢查、
      // 要求填原因、通知顧問。
      if (parts.length === 4 && parts[1] === 'jobs' && parts[3] === 'close-hire' && request.method === 'POST') {
        const slug = parts[2];
        const b = await request.json().catch(() => ({}));
        const reason = b.reason ? String(b.reason).trim().slice(0, 300) : '';
        if (!reason) return json(request, { ok: false, error: '請填寫結束招募的原因' }, 400);
        const owned = await env.DB.prepare(
          `SELECT slug, title, status FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        if (owned.status !== 'open') {
          return json(request, { ok: false, error: '這個職缺目前不是招募中的狀態' }, 400);
        }
        await setJobOpenState(env, { slug, status: 'closed', by: 'client', reason });
        notify(env,
          `✅ ${company.display_name} 標記「結束招募」：「${owned.title || slug}」\n原因：${reason}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, slug, status: 'closed' });
      }

      // POST /portal/:token/jobs/:slug/reopen — 重新招募。
      // 2026-08-25 改：不管是客戶自己按「結束招募」關的，還是顧問後台關的，
      // 客戶都能自己重新招募——Jacky 明確要求兩種情況都要讓客戶能自己重啟，
      // 不要卡在「只有顧問關的才不給重開」這個限制上。
      if (parts.length === 4 && parts[1] === 'jobs' && parts[3] === 'reopen' && request.method === 'POST') {
        const slug = parts[2];
        const b = await request.json().catch(() => ({}));
        const reason = b.reason ? String(b.reason).trim().slice(0, 300) : '';
        if (!reason) return json(request, { ok: false, error: '請填寫重新招募的原因' }, 400);
        const owned = await env.DB.prepare(
          `SELECT slug, title, status, closed_by FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        if (owned.status !== 'closed') {
          return json(request, { ok: false, error: '這個職缺目前不是關閉狀態' }, 400);
        }
        await setJobOpenState(env, { slug, status: 'open', by: 'client', reason });
        notify(env,
          `🔄 ${company.display_name} 重新招募：「${owned.title || slug}」\n原因：${reason}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, slug, status: 'open' });
      }

      // PUT /portal/:token/jobs/:slug — 只准更新白名單欄位，且該職缺必須真的屬於這個 token 對應的公司。
      // client_draft 隨時可改；pending_review 已送審鎖住，改不了（要嘛等顧問核准/拒絕，
      // 拒絕之後會退回 client_draft，見下面 /admin/portal/pending-jobs/reject）。
      if (parts.length === 3 && parts[1] === 'jobs' && request.method === 'PUT') {
        const slug = parts[2];
        const owned = await env.DB.prepare(
          `SELECT slug, title, status FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        if (owned.status === 'pending_review') {
          return json(request, { ok: false, error: '這個職缺還在等顧問審核，審核通過前無法修改' }, 400);
        }

        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const sets = [];
        const bind = [];
        for (const key of PORTAL_FIELDS) {
          if (!(key in b)) continue;
          sets.push(`${key} = ?`);
          bind.push(b[key] === '' ? null : b[key]);
        }
        if (!sets.length) return json(request, { ok: false, error: '沒有要更新的欄位' }, 400);
        const now = nowTaipei();
        sets.push('updated_at = ?', 'requirement_form_updated_at = ?');
        bind.push(now, now, slug);

        await env.DB.prepare(`UPDATE jobs SET ${sets.join(', ')} WHERE slug = ?`).bind(...bind).run();

        // 顧問要知道企業客戶自己動手改過用人需求表，不然這件事只有客戶知道。
        // 純告知、不用顧問馬上處理，所以進「履歷進件」這個資訊性主題，不佔用決策主題。
        const changedFields = sets.filter((s) => s !== 'updated_at = ?' && s !== 'requirement_form_updated_at = ?')
          .map((s) => s.split(' =')[0]);
        notify(env,
          `📝 ${company.display_name} 剛透過用人需求表自行更新了「${owned.title || slug}」\n` +
          `改了 ${changedFields.length} 個欄位：${changedFields.join('、')}`,
          { message_thread_id: THREAD.intake }).catch(() => {});

        return json(request, { ok: true, slug, updated_at: now });
      }

      // 2026-09-02 加：DELETE /portal/:token/jobs/:slug — 客戶自己刪掉一個
      // 職缺（例如打錯字建了一筆測試用的「111」，之前只能「結束招募」，
      // 職缺還是留在清單裡）。⚠️ 安全底線：只要這個職缺已經有任何一筆
      // applications（真的有人應徵過），一律擋下來，不准客戶自己刪——
      // 那代表底下可能已經有正在跑的人選紀錄，誤刪會連帶弄丟顧問手上的
      // 進度資料，這種情況只能由顧問後台人工處理。沒有任何應徵紀錄
      // （通常是剛建立的草稿或誤建）才真的允許刪除。
      if (parts.length === 3 && parts[1] === 'jobs' && request.method === 'DELETE') {
        const slug = parts[2];
        const owned = await env.DB.prepare(
          `SELECT slug, title FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        const appCount = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM applications WHERE job_slug = ?`
        ).bind(slug).first();
        if (appCount && appCount.n > 0) {
          return json(request, {
            ok: false,
            error: `這個職缺已經有 ${appCount.n} 位人選應徵過，不能自己刪除——請聯繫顧問處理，避免弄丟人選進度紀錄。`,
          }, 400);
        }
        await env.DB.prepare(`DELETE FROM jobs WHERE slug = ?`).bind(slug).run();
        notify(env,
          `🗑️ ${company.display_name} 自己刪除了職缺「${owned.title || slug}」（刪除前確認沒有任何應徵紀錄）`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, slug });
      }

      // POST /portal/:token/jobs — 企業客戶自己新增職缺。
      // 2026-08-25 改：不再一建立就丟進 pending_review 只填得了標題——
      // 建的是 status='client_draft'，客戶可以自己把 51 個欄位填完（或用
      // 「一鍵匯入」帶入），填完按「送出審核」（見上面 /submit）才變
      // pending_review、才通知顧問。這裡建立當下不通知，不然顧問會被
      // 一堆「客戶開了新缺」但內容是空的訊息洗版。
      // status 依然不會出現在任何公開清單、不會被排進社群發文的自動排程——
      // 顧問核准後變 draft，還是要走既有的正常職缺審核流程（禁刊過濾器、
      // 內容改寫）才能真的對外刊登。
      if (parts.length === 1 && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const title = String(b.title || '').trim();
        if (!title) return json(request, { ok: false, error: '請填職缺名稱' }, 400);

        // 2026-08-25 加：同一家客戶不能建兩筆同標題的職缺——擋下來，請客戶去改
        // 既有那筆，不要生出撞名的重複資料。
        const dup = await findDuplicateJob(env, { companyId: company.id, title });
        if (dup) {
          return json(request, { ok: false, error: `已經有一筆「${dup.title}」了，請直接編輯那一筆，不要重新建立` }, 409);
        }

        const slug = `pending-${company.id}-${[...crypto.getRandomValues(new Uint8Array(4))]
          .map((x) => x.toString(16).padStart(2, '0')).join('')}`;
        const now = nowTaipei();

        const sets = ['slug', 'title', 'status', 'company_id', 'updated_at'];
        const bind = [slug, title, 'client_draft', company.id, now];
        for (const key of PORTAL_FIELDS) {
          if (key === 'title' || !(key in b)) continue;
          sets.push(key);
          bind.push(b[key] === '' ? null : b[key]);
        }
        await env.DB.prepare(
          `INSERT INTO jobs (${sets.join(', ')}) VALUES (${sets.map(() => '?').join(',')})`
        ).bind(...bind).run();

        return json(request, { ok: true, slug, status: 'client_draft' });
      }

      // ── 一鍵匯入：客戶丟檔案／連結／貼文字，本機排程解析成 51 個欄位 ──
      //
      // Worker 只負責收件跟存檔（跟 job_intakes 同一套哲學）：真正的解析
      // 交給本機 portal_import_tick.py 呼叫 Claude Code CLI 做，因為
      // Worker runtime 沒有能力可靠解析任意格式的 JD 文件，硬做只會做出
      // 一套讀中文 PDF/掃描檔會壞掉的東西（2026-07-30 已經在履歷解析踩過
      // 這個坑，見上面 saveResume() 的註解）。
      //
      // POST /portal/:token/jobs/:slug/import
      //   body: { sourceType: 'file'|'url'|'text', files?: [{b64,name,mime}], url?, text? }
      if (parts.length === 4 && parts[1] === 'jobs' && parts[3] === 'import' && request.method === 'POST') {
        const slug = parts[2];
        const owned = await env.DB.prepare(
          `SELECT slug, status FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(slug, company.id).first();
        if (!owned) return json(request, { ok: false, error: '找不到這個職缺，或不屬於這個公司入口' }, 404);
        if (owned.status !== 'client_draft') {
          return json(request, { ok: false, error: '這個職缺目前不能匯入（已送審或已核准）' }, 400);
        }
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const sourceType = String(b.sourceType || '');
        if (!['file', 'url', 'text'].includes(sourceType)) {
          return json(request, { ok: false, error: 'sourceType 必須是 file/url/text' }, 400);
        }
        const now = nowTaipei();
        const importId = uid();
        let sourceRef = null;
        let fileRows = [];

        if (sourceType === 'file') {
          const files = Array.isArray(b.files) ? b.files.slice(0, 5) : [];
          if (!files.length) return json(request, { ok: false, error: '請至少上傳一個檔案' }, 400);
          for (const f of files) {
            const saved = await saveUpload(env, f, now);
            if (!saved) continue;
            if (saved.tooBig) {
              return json(request, { ok: false,
                error: `「${saved.name}」超過 8MB，請改貼雲端連結（Google Drive／Dropbox）或分成多個檔案` }, 413);
            }
            fileRows.push(saved.fileId);
          }
          if (!fileRows.length) return json(request, { ok: false, error: '檔案存檔失敗' }, 500);
          sourceRef = fileRows.join(',');
        } else if (sourceType === 'url') {
          const url = String(b.url || '').trim();
          if (!url) return json(request, { ok: false, error: '請貼連結' }, 400);
          sourceRef = url;
        } else {
          const text = String(b.text || '').trim();
          if (!text) return json(request, { ok: false, error: '請貼文字內容' }, 400);
          sourceRef = text;
        }

        await env.DB.prepare(
          `INSERT INTO portal_imports (id, job_slug, company_id, source_type, source_ref, status, created_at, updated_at)
           VALUES (?,?,?,?,?, 'pending', ?, ?)`
        ).bind(importId, slug, company.id, sourceType, sourceRef, now, now).run();

        if (fileRows.length) {
          const stmts = fileRows.map((fid) => env.DB.prepare(
            `INSERT INTO portal_import_files (import_id, file_id, created_at) VALUES (?,?,?)`
          ).bind(importId, fid, now));
          await env.DB.batch(stmts);
        }

        notify(env,
          `📥 ${company.display_name} 對「${slug}」用了一鍵匯入（${sourceType}），等本機排程解析`,
          { message_thread_id: THREAD.system }).catch(() => {});

        return json(request, { ok: true, importId, status: 'pending' });
      }

      // GET /portal/:token/imports/:importId — 前端輪詢解析進度
      if (parts.length === 3 && parts[1] === 'imports' && request.method === 'GET') {
        const importId = parts[2];
        const row = await env.DB.prepare(
          `SELECT id, job_slug, status, parsed_json, error_note FROM portal_imports
             WHERE id = ? AND company_id = ?`
        ).bind(importId, company.id).first();
        if (!row) return json(request, { ok: false, error: '找不到這筆匯入紀錄' }, 404);
        let parsed = null;
        try { parsed = row.parsed_json ? JSON.parse(row.parsed_json) : null; } catch { parsed = null; }
        return json(request, { ok: true, status: row.status, parsed, errorNote: row.error_note });
      }

      // POST /portal/:token/imports/:importId/apply — 客戶確認匯入結果，正式寫回職缺欄位
      // 不是解析完就自動存檔：解析結果先讓客戶在表單上看到反白的「匯入建議」，
      // 客戶按確認（可能中途已經手動改過幾欄）才真的落地，避免匯錯字段沒人發現。
      if (parts.length === 4 && parts[1] === 'imports' && parts[3] === 'apply' && request.method === 'POST') {
        const importId = parts[2];
        const imp = await env.DB.prepare(
          `SELECT id, job_slug, status, parsed_json FROM portal_imports WHERE id = ? AND company_id = ?`
        ).bind(importId, company.id).first();
        if (!imp) return json(request, { ok: false, error: '找不到這筆匯入紀錄' }, 404);
        if (imp.status !== 'parsed') return json(request, { ok: false, error: '這筆匯入還沒解析完成' }, 400);
        const owned = await env.DB.prepare(
          `SELECT slug, status FROM jobs WHERE slug = ? AND company_id = ?`
        ).bind(imp.job_slug, company.id).first();
        if (!owned || owned.status !== 'client_draft') {
          return json(request, { ok: false, error: '這個職缺目前不能套用匯入結果' }, 400);
        }
        let fields;
        try { fields = JSON.parse(imp.parsed_json || '{}'); } catch { fields = {}; }
        // 允許客戶在確認畫面改過的版本蓋掉原始解析結果（body 可選）
        let b = {};
        try { b = await request.json(); } catch { b = {}; }
        if (b.fields && typeof b.fields === 'object') fields = b.fields;

        const sets = [];
        const bind = [];
        for (const key of PORTAL_FIELDS) {
          if (key === 'title' && !fields[key]) continue; // 不用空值蓋掉已有標題
          if (!(key in fields)) continue;
          sets.push(`${key} = ?`);
          bind.push(fields[key] === '' ? null : fields[key]);
        }
        const now = nowTaipei();
        if (sets.length) {
          sets.push('updated_at = ?', 'requirement_form_updated_at = ?');
          bind.push(now, now, imp.job_slug);
          await env.DB.prepare(`UPDATE jobs SET ${sets.join(', ')} WHERE slug = ?`).bind(...bind).run();
        }
        await env.DB.prepare(`UPDATE portal_imports SET status='applied', updated_at=? WHERE id=?`)
          .bind(now, importId).run();
        return json(request, { ok: true, slug: imp.job_slug });
      }

      // GET /portal/:token/candidates — 人選推送列表。只列「顧問已經按下推薦給
      // 客戶」的人選（reports.consultant_decision='forwarded'）——還沒推派的
      // 案子客戶連看都看不到。階段一律用 resolveStage()（跟顧問後台階段條、
      // 候選人 LINE 查進度共用同一顆函式），保證三邊顯示的階段絕對一致。
      if (parts.length === 2 && parts[1] === 'candidates' && request.method === 'GET') {
        // 2026-08-25 改：改成靠 candidate_forwards 判斷「這位人選有沒有明確推薦給
        // 這家公司」，不是只看 j.company_id（那樣只認得「原本應徵的那家」，一個
        // 人選被同時推薦給好幾家客戶時，其他家客戶會完全看不到，而且顧問也無從
        // 得知到底推薦給了誰）。
        // ⚠️ 2026-08-26 改：階段欄位改讀 candidate_forwards（每家客戶一筆），
        // 不是 applications（每個人選一筆）。同一位人選推給兩家客戶時，
        // 兩家的面試進度是各自獨立的——A 家談到第二關、B 家還沒約，
        // 原本共用 applications.manual_stage 會讓兩家看到同一個進度。
        const { results: rows } = await env.DB.prepare(
          `SELECT a.id AS application_id, a.name AS candidate_name,
                  COALESCE(cf.job_slug, a.job_slug) AS job_slug,
                  a.interview_started_at, a.interview_ended_at,
                  a.resume_file_id, a.resume_url,
                  cf.manual_stage, cf.manual_stage_note, cf.manual_stage_by, cf.manual_stage_at,
                  cf.client_interview_labels, cf.line_id_given, cf.client_note,
                  cf.client_rejected_at, cf.client_reject_reason, cf.client_advanced_at,
                  j.title AS job_title, j.client_named,
                  r.id AS report_id, r.content_client_md, r.consultant_decision, cf.forwarded_at,
                  cf.advisor_not_recommended_at, cf.advisor_not_recommended_reason
             FROM candidate_forwards cf
             JOIN applications a ON a.id = cf.application_id
             JOIN jobs j ON j.slug = COALESCE(cf.job_slug, a.job_slug)
             LEFT JOIN reports r ON r.id = (
                   SELECT id FROM reports WHERE application_id = a.id
                   ORDER BY created_at DESC LIMIT 1)
            WHERE cf.company_id = ?
            ORDER BY cf.forwarded_at DESC`
        ).bind(company.id).all();
        // 2026-08-31 改：拿掉「AND r.consultant_decision='forwarded'」這個過濾，
        // 改成 LEFT JOIN——candidate_forwards 這張表本身就是「有沒有推薦給這家
        // 客戶」的唯一真相：正常流程一定是先按「轉給客戶」（那個動作本身就會
        // 檢查 decision=forwarded 才准建立這筆）才會有這筆 forward row；顧問手動
        // 新增人選（/admin/pipeline/manual-forward）則是建立當下就直接寫入
        // forward，本來就不經過決定報告的流程，用 decision 過濾只會讓這些人選
        // 永遠進不了客戶 portal（INNER JOIN 甚至讓完全沒有報告的人選整列消失）。
        // 舊寫法還有一個潛在資料錯亂：如果同一人選之後又生出一份新報告
        // （decision 預設是 null），「最新那份」的 decision 一 null，這個人選
        // 就會從客戶 portal 悄悄消失，即使他早就被正式推薦過。

        // ⚠️ 2026-09-01 再改一次：上一版（2026-08-31）把「N 位人選序列查兩次」
        // 改成「N 位人選平行查兩次」，LATENCY 有改善，但 D1 是照「讀了幾列」計費／
        // 算額度，平行執行並不會減少總查詢次數——這支端點正是今天把 D1 免費方案
        // 每日讀取額度（500 萬筆）打到 2200 萬筆、整個後台斷線一整天的另一個主因
        // （這裡是客戶自己會打開的 portal 頁面，流量不比顧問後台少）。改成用
        // application_id IN (...) 一次批次撈，總查詢次數從「O(人數)×2」降到固定
        // 2 次，不管這家客戶底下有幾位人選都一樣。
        const rowAppIds = (rows || []).map((r) => r.application_id);
        // D1 單次 bind 參數上限保守抓 100，客戶底下人選數理論上沒有上限（累積
        // 久了可能超過），跟顧問後台那支一樣分批查再合併，不要賭「這家客戶
        // 人選一定不會太多」。
        const portalChunk = (arr, size) => { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; };
        const portalBatchAll = async (sql, ids) => {
          const out = [];
          for (const ids2 of portalChunk(ids, 100)) {
            if (!ids2.length) continue;
            const { results } = await env.DB.prepare(sql(ids2.length)).bind(...ids2).all();
            out.push(...(results || []));
          }
          return out;
        };
        // 表單這支要多綁一個 company.id（跟 application_id 清單分開兩種參數），
        // portalBatchAll 只支援單一種 ids 清單綁定，這裡就不硬塞進去，直接寫
        // 自己的批次迴圈，公司 id 一樣走 bind()，不做字串拼接。
        const formSubBatch = async (ids) => {
          const out = [];
          for (const ids2 of portalChunk(ids, 100)) {
            if (!ids2.length) continue;
            const ph = Array.from({ length: ids2.length }, () => '?').join(',');
            const { results } = await env.DB.prepare(
              `SELECT cfs.id, cfs.application_id, cfs.status, cfs.submitted_at, cfs.submitted_file_id, jf.name AS form_name
                 FROM candidate_form_submissions cfs JOIN job_forms jf ON jf.id = cfs.job_form_id
                WHERE cfs.company_id = ? AND cfs.application_id IN (${ph})`
            ).bind(company.id, ...ids2).all();
            out.push(...(results || []));
          }
          return out;
        };
        const [placementRowsAll, apptRowsAll, formSubRowsAll] = rowAppIds.length
          ? await Promise.all([
              portalBatchAll((n) => `SELECT application_id, stage, onboard_date, candidate_care_log, updated_at FROM placements
                  WHERE application_id IN (${Array.from({ length: n }, () => '?').join(',')})
                  ORDER BY application_id, updated_at DESC`, rowAppIds),
              portalBatchAll((n) => `SELECT application_id, stage, status, confirmed_slot FROM interview_appointments
                  WHERE application_id IN (${Array.from({ length: n }, () => '?').join(',')})
                  ORDER BY application_id, stage ASC, created_at ASC`, rowAppIds),
              // 2026-09-02 加：人選客製表單狀態，只挑這家客戶自己的（company_id 篩死），
              // 同一人選推給別家客戶的表單不該在這裡看得到。
              formSubBatch(rowAppIds),
            ])
          : [[], [], []];
        const placementByApp = new Map();
        for (const pl of placementRowsAll) if (!placementByApp.has(pl.application_id)) placementByApp.set(pl.application_id, pl);
        const apptsByApp = new Map();
        for (const ap of apptRowsAll) { if (!apptsByApp.has(ap.application_id)) apptsByApp.set(ap.application_id, []); apptsByApp.get(ap.application_id).push(ap); }
        const formsByApp = new Map();
        for (const fs of formSubRowsAll) { if (!formsByApp.has(fs.application_id)) formsByApp.set(fs.application_id, []); formsByApp.get(fs.application_id).push({
          id: fs.id, form_name: fs.form_name, status: fs.status, submitted_at: fs.submitted_at, has_file: !!fs.submitted_file_id,
        }); }
        const perRow = (rows || []).map((row) => ({
          row, placement: placementByApp.get(row.application_id) || null, appts: apptsByApp.get(row.application_id) || [],
        }));

        const jobStageKeysMap = await getJobStageKeysMap(env, (rows || []).map((r) => r.job_slug));
        const candidates = [];
        for (const { row, placement, appts } of perRow) {
          let roundLabels = {};
          try { roundLabels = JSON.parse(row.client_interview_labels || '{}'); } catch { roundLabels = {}; }
          const stageInfo = resolveStage(row, { consultant_decision: row.consultant_decision }, appts || [], placement, roundLabels, jobStageKeysMap[row.job_slug]);
          const stage1Idx = STAGE_INDEX.stage1;
          // screening 是 Step1ne 推薦給客戶「之前」的內部流程階段，客戶看不到意義；
          // care（到職關懷）是報到之後的內部追蹤，客戶也不用看。
          // ⚠️ 2026-08-31 改：confirm 原本也被收掉、跟 screening 一起藏起來，但 Jacky
          // 明確要客戶端表格看得到「顧問確認中」這一格（推送日 → 顧問確認中 → 已推薦
          // 給客戶 → 第一階段…），改成保留這一關、relabel 成客戶看得懂的說法，
          // 排在「已推薦給客戶」合成格之前。
          // 其餘（stage1~4／offer／onboard／backup）照 resolveStage() 原樣帶出——
          // 客戶跟顧問後台看的是同一份 steps，差別只在客戶看不到這幾格。
          const visibleSteps = stageInfo.steps.filter((s) => s.key !== 'screening' && s.key !== 'care');
          const confirmStep = visibleSteps.find((s) => s.key === 'confirm');
          const restSteps = visibleSteps.filter((s) => s.key !== 'confirm');
          // 2026-09-01 改：Jacky 要這兩格改名——「顧問確認中」→「顧問階段」，
          // 「已推薦給客戶」→「用人單位」（這格现在也是企業自己按「推進／不推進」
          // 的欄位，原本的名字聽起來像顧問單方面的動作紀錄，改成企業自己一看就懂
          // 這格是他們要處理的）。
          const matched = { key: 'matched', lb: '用人單位', done: stageInfo.effective_index >= stage1Idx, now: stageInfo.effective_index < stage1Idx };
          const steps = [
            confirmStep ? { ...confirmStep, lb: '顧問階段' } : null,
            matched,
            ...restSteps,
          ].filter(Boolean);
          candidates.push({
            application_id: row.application_id,
            // client_named=0（未簽約／匿名客戶）比照既有報告／履歷的匿名慣例，不放真名。
            // ⚠️ 2026-08-26 修：原本用 client_named 決定要不要顯示姓名，那是錯的——
            // client_named 的意思是「我們能不能對外公開講出這家客戶的名字」（客戶保密），
            // 跟「客戶能不能看到候選人姓名」是兩回事。這個畫面是客戶自己的 portal，
            // 列的是我們主動推薦給他們、他們要去面試的人，當然要看得到是誰，
            // 不然沒辦法安排面試也沒辦法跟顧問討論。履歷匿名（cv_mode）管的是
            // 「送評估用的履歷要不要去識別化」，不是這個進度追蹤畫面。
            candidate_name: row.candidate_name,
            job_slug: row.job_slug,
            job_title: row.job_title,
            forwarded_at: row.forwarded_at,
            steps,
            current_stage: row.client_rejected_at
              ? { key: 'rejected', lb: '不推進' }
              : stageInfo.effective_index >= stage1Idx
                ? { key: stageInfo.effective_key, lb: (steps.find((s) => s.key === stageInfo.effective_key) || {}).lb }
                : { key: null, lb: '用人單位' },
            client_rejected_at: row.client_rejected_at || null,
            client_reject_reason: row.client_reject_reason || null,
            client_advanced_at: row.client_advanced_at || null,
            interview_rounds_used: Object.keys(roundLabels).length,
            interview_round_types: INTERVIEW_ROUND_TYPE_LABEL,
            editable_stages: CLIENT_SETTABLE_STAGES.map((key) => ({
              key, lb: (STAGE_ORDER.find((s) => s.key === key) || {}).lb,
            })),
            // 弘昌／帆宣案新加：LINE ID 是顧問自己跟人選要來、手動填的，不是自動
            // 抓的；client_note 是顧問另外寫的「給客戶看」摘要，跟內部備註分開；
            // 履歷／報告一律不給原始檔案網址或內部報告全文，只給「有沒有」的旗標，
            // 真正的內容要另外打下面兩支帶擁有權驗證的端點才拿得到。
            line_id_given: row.line_id_given || null,
            client_note: row.client_note || null,
            has_resume: !!(row.resume_file_id || row.resume_url),
            has_client_report: !!row.content_client_md,
            forms: formsByApp.get(row.application_id) || [],
          });
        }
        return json(request, { ok: true, candidates });
      }

      // GET /portal/:token/candidates/:applicationId/form/:submissionId — 客戶下載
      // 人選填完的表單。跟 /resume 同一套擁有權檢查：這個 submission 一定要屬於
      // 「這個人選＋這家公司」，不能靠猜 id 打到別家客戶的檔案。
      if (parts.length === 5 && parts[1] === 'candidates' && parts[3] === 'form' && request.method === 'GET') {
        const applicationId = parts[2];
        const submissionId = parts[4];
        const sub = await env.DB.prepare(
          `SELECT submitted_file_id FROM candidate_form_submissions
            WHERE id=? AND application_id=? AND company_id=?`
        ).bind(submissionId, applicationId, company.id).first();
        if (!sub || !sub.submitted_file_id) return json(request, { ok: false, error: '還沒有填寫完成的檔案' }, 404);
        const file = await env.DB.prepare(
          `SELECT filename, mime, content_b64, chunks FROM files WHERE id=?`
        ).bind(sub.submitted_file_id).first();
        if (!file) return json(request, { ok: false, error: '找不到檔案' }, 404);
        let b64 = file.content_b64 || '';
        if (!b64 && file.chunks) {
          const { results: parts3 } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id=? ORDER BY idx ASC`
          ).bind(sub.submitted_file_id).all();
          b64 = (parts3 || []).map((c) => c.b64).join('');
        }
        if (!b64) return json(request, { ok: false, error: '檔案讀取失敗' }, 500);
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        return new Response(bin, { headers: {
          'content-type': file.mime || 'application/octet-stream',
          'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(file.filename || 'form')}`,
          'cache-control': 'private, no-store',
        } });
      }

      // POST /portal/:token/candidates/:applicationId/add-interview-round — 客戶
      // 自己回報「這一輪面試是什麼類型」（人資面談／用人單位主管面談，可加備註是
      // 哪個層級），系統自動判斷這是第幾輪（數目前已經記了幾輪＋1），組成
      // 「人資面談（第一階段面談）」這種標籤存進 client_interview_labels，
      // 再呼叫既有的 applyManualStage() 把 manual_stage 推到對應的 stageN——
      // 顧問後台階段條、候選人 LINE 進度會自動同步（都吃同一顆 resolveStage()）。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'add-interview-round' && request.method === 'POST') {
        const applicationId = parts[2];
        const b = await request.json().catch(() => ({}));
        const interviewType = b.interview_type ? String(b.interview_type) : '';
        if (!INTERVIEW_ROUND_TYPE_LABEL[interviewType]) {
          return json(request, { ok: false, error: '面談類型只能是人資面談或用人單位主管面談' }, 400);
        }
        const detailNote = b.detail_note ? String(b.detail_note).trim().slice(0, 60) : '';

        // 2026-08-25 改：擁有權檢查改看 candidate_forwards 有沒有這家公司的推薦紀錄，
        // 不是看 j.company_id（同一個人選可能被推薦給好幾家客戶，j.company_id 只認得
        // 「原本應徵的那家」）。
        // 面試輪次也是每家客戶各自獨立的——A 家談到第三輪，不代表 B 家也是。
        // ⚠️ 2026-09-01 改：這裡原本是 INNER JOIN reports——顧問手動新增、從沒
        // 產出過報告的人選（跟 /admin/forward-candidate 那次「羅生門」死路同一種
        // 情境）完全查不到列，客戶在 portal 上會被擋「找不到這位人選」。
        // candidate_forwards 這張表本身存在就是「有沒有正式推薦給這家客戶」的
        // 唯一真相（跟 /admin/forward-candidate 註解同一個原則），不用再靠
        // reports.consultant_decision 覆核一次；改成 LEFT JOIN，report 沒有就是
        // NULL，不擋流程。
        const row = await env.DB.prepare(
          `SELECT a.id AS application_id, a.name AS candidate_name,
                  COALESCE(cf.job_slug, a.job_slug) AS job_slug,
                  cf.client_interview_labels, r.consultant_decision,
                  cf.id AS forward_id
             FROM candidate_forwards cf
             JOIN applications a ON a.id = cf.application_id
             LEFT JOIN reports r ON r.id = (
                   SELECT id FROM reports WHERE application_id = a.id
                   ORDER BY created_at DESC LIMIT 1)
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(applicationId, company.id).first();
        if (!row) {
          return json(request, { ok: false, error: '找不到這位人選' }, 404);
        }

        let roundLabels = {};
        try { roundLabels = JSON.parse(row.client_interview_labels || '{}'); } catch { roundLabels = {}; }
        const usedCount = Object.keys(roundLabels).length;
        if (usedCount >= 4) {
          return json(request, { ok: false, error: '面試輪次已達上限（4 輪），如需新增請聯繫顧問' }, 400);
        }
        const nextStage = `stage${usedCount + 1}`;
        const roundLb = STAGE_ORDER.find((s) => s.key === nextStage).lb;
        const label = INTERVIEW_ROUND_TYPE_LABEL[interviewType] + (detailNote ? `（${detailNote}）` : '') + `（${roundLb}）`;
        roundLabels[nextStage] = label;

        await env.DB.prepare(`UPDATE candidate_forwards SET client_interview_labels=? WHERE id=?`)
          .bind(JSON.stringify(roundLabels), row.forward_id).run();
        const result = await applyManualStage(env, {
          application_id: applicationId, stage: nextStage, by: `client:${company.id}`,
          company_id: company.id,
        });
        notify(env,
          `📋 ${company.display_name} 新增了一輪面試：${row.candidate_name}（${row.job_slug}）→ ${label}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, stage: nextStage, label, ...result });
      }

      // POST /portal/:token/candidates/:applicationId/stage — 客戶回填這個人選
      // 目前在他們那邊面試到哪一關。寫入邏輯跟顧問用的 /admin/set-manual-stage
      // 共用 applyManualStage()，會自動推播候選人 LINE、顧問後台階段條也會
      // 同步反映（都讀同一個 resolveStage()）。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'stage' && request.method === 'POST') {
        const applicationId = parts[2];
        const b = await request.json().catch(() => ({}));
        const stage = b.stage ? String(b.stage) : '';
        if (!CLIENT_SETTABLE_STAGES.includes(stage)) {
          return json(request, { ok: false, error: '不合法的階段代碼' }, 400);
        }
        // 報到要有預計報到日期，不能含糊地套「今天」——多半是先講好日期、人還沒到。
        if (stage === 'onboard' && !/^\d{4}-\d{2}-\d{2}$/.test(String(b.onboard_date || ''))) {
          return json(request, { ok: false, error: '請填寫預計報到日期（YYYY-MM-DD）' }, 400);
        }

        // 安全檢查：這個 application 必須有 candidate_forwards 明確推薦給這家公司，
        // 不能靠猜 application_id 打到別家公司的資料——也不能只看 j.company_id，
        // 一個人選可能同時被推薦給好幾家客戶（見 /admin/forward-candidate）。
        // ⚠️ 2026-09-01 改：同 add-interview-round 那支的理由——INNER JOIN reports
        // 會把從沒產出過報告的人選（顧問手動新增、直接推薦）擋在外面，candidate_forwards
        // 存在本身就已經證明「正式推薦給這家客戶」，不需要再靠 reports 表覆核。
        const row = await env.DB.prepare(
          `SELECT a.id AS application_id, a.name AS candidate_name,
                  COALESCE(cf.job_slug, a.job_slug) AS job_slug,
                  a.interview_started_at, a.interview_ended_at,
                  cf.manual_stage, cf.manual_stage_note, cf.manual_stage_by, cf.manual_stage_at,
                  cf.client_interview_labels, r.consultant_decision
             FROM candidate_forwards cf
             JOIN applications a ON a.id = cf.application_id
             LEFT JOIN reports r ON r.id = (
                   SELECT id FROM reports WHERE application_id = a.id
                   ORDER BY created_at DESC LIMIT 1)
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(applicationId, company.id).first();
        if (!row) {
          return json(request, { ok: false, error: '找不到這位人選' }, 404);
        }

        // 防止客戶手滑倒退：新階段不能比目前實際顯示的階段還早。
        const placement = await env.DB.prepare(
          `SELECT stage, onboard_date, candidate_care_log FROM placements
            WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
        ).bind(applicationId).first();
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id = ? ORDER BY stage ASC, created_at ASC`
        ).bind(applicationId).all();
        let stageRoundLabels = {};
        try { stageRoundLabels = JSON.parse(row.client_interview_labels || '{}'); } catch { stageRoundLabels = {}; }
        const jobStageKeysForRow = await getJobStageKeys(env, row.job_slug);
        const current = resolveStage(row, { consultant_decision: row.consultant_decision }, appts || [], placement, stageRoundLabels, jobStageKeysForRow);
        const newIdx = STAGE_INDEX[stage];
        if (stage !== 'backup' && newIdx < current.effective_index) {
          return json(request, { ok: false, error: '新階段不能比目前的還早，如需訂正請聯繫顧問' }, 400);
        }
        // 2026-09-01 加：客戶（或代客戶操作的顧問）不能把階段設到這個職缺根本沒
        // 設定的輪次——例如職缺只設了第一階段，不該讓表格下拉選單選到「第三階段」。
        // stage2~4 才需要檢查；stage1／offer／onboard／backup 永遠允許（backup 是
        // 平行狀態、offer/onboard 是終點，不受輪數設定約束）。
        if (['stage2', 'stage3', 'stage4'].includes(stage) && !jobStageKeysForRow.includes(stage)) {
          return json(request, { ok: false, error: '這個職缺目前沒有設定這一輪面試，請先到職缺設定新增' }, 400);
        }

        const result = await applyManualStage(env, {
          application_id: applicationId, stage, note: b.note, by: `client:${company.id}`,
          company_id: company.id, onboard_date: b.onboard_date || null,
        });
        notify(env,
          `📋 ${company.display_name} 回填了人選進度：${row.candidate_name}（${row.job_slug}）→ ` +
          `${(STAGE_ORDER.find((s) => s.key === stage) || {}).lb || stage}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true, ...result });
      }

      // POST /portal/:token/candidates/:applicationId/reject — 用人單位婉拒這位
      // 人選，必須附原因（不能空白帶過，顧問要知道為什麼才能調整後續 sourcing
      // 方向）。只影響這家客戶自己的 candidate_forwards 那一列，不動人選本身、
      // 也不影響這位人選推薦給其他客戶的狀態。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'reject' && request.method === 'POST') {
        const applicationId = parts[2];
        const b = await request.json().catch(() => ({}));
        const reason = String(b.reason || '').trim();
        if (!reason) return json(request, { ok: false, error: '請填寫婉拒原因' }, 400);
        const row = await env.DB.prepare(
          `SELECT cf.id, a.name AS candidate_name, a.job_slug
             FROM candidate_forwards cf JOIN applications a ON a.id = cf.application_id
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(applicationId, company.id).first();
        if (!row) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const now = nowTaipei();
        await env.DB.prepare(
          `UPDATE candidate_forwards SET client_rejected_at = ?, client_reject_reason = ?
            WHERE application_id = ? AND company_id = ?`
        ).bind(now, reason, applicationId, company.id).run();
        notify(env,
          `❌ ${company.display_name} 婉拒了人選：${row.candidate_name}（${row.job_slug}）\n原因：${reason}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true });
      }

      // GET /portal/:token/candidates/:applicationId/resume — 履歷下載。
      // ⚠️ /admin/file 那支是給顧問用的，靠 ADMIN_TOKEN（Header）驗證——portal
      // 從頭到尾是「網址帶 token」的模型，兩套驗證機制不能混用，所以另開一支。
      // 網址帶 token 是 portal 一開始就接受的風險模型，這裡不是新的資安退步；
      // 但一樣要先驗證這個 application 真的有推薦給這家公司，不能靠猜 id
      // 打到別家客戶的履歷。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'resume' && request.method === 'GET') {
        const applicationId = parts[2];
        const owns = await env.DB.prepare(
          `SELECT 1 FROM candidate_forwards WHERE application_id=? AND company_id=?`
        ).bind(applicationId, company.id).first();
        if (!owns) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const app = await env.DB.prepare(
          `SELECT resume_file_id, resume_url, name FROM applications WHERE id=?`
        ).bind(applicationId).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        if (app.resume_url) {
          return Response.redirect(app.resume_url, 302);
        }
        if (!app.resume_file_id) return json(request, { ok: false, error: '這位人選還沒有履歷' }, 404);
        const file = await env.DB.prepare(
          `SELECT filename, mime, content_b64, chunks FROM files WHERE id=?`
        ).bind(app.resume_file_id).first();
        if (!file) return json(request, { ok: false, error: '找不到履歷檔案' }, 404);
        let b64 = file.content_b64 || '';
        if (!b64 && file.chunks) {
          const { results: parts2 } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id=? ORDER BY idx ASC`
          ).bind(app.resume_file_id).all();
          b64 = (parts2 || []).map((c) => c.b64).join('');
        }
        if (!b64) return json(request, { ok: false, error: '履歷檔案讀取失敗' }, 500);
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        return new Response(bin, { headers: {
          'content-type': file.mime || 'application/octet-stream',
          'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(file.filename || 'resume')}`,
          'cache-control': 'private, no-store',
        } });
      }

      // GET /portal/:token/candidates/:applicationId/report — 只回傳
      // content_client_md（顧問編輯過的客戶版），絕對不是 content_md（內部原文，
      // 可能有直白評估用語、跟其他人選的比較）。顧問還沒編輯過就明講「還沒準備好」，
      // 不要回傳空白或內部原文湊數。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'report' && request.method === 'GET') {
        const applicationId = parts[2];
        const row = await env.DB.prepare(
          `SELECT r.content_client_md
             FROM candidate_forwards cf
             JOIN reports r ON r.id = (
                   SELECT id FROM reports WHERE application_id = cf.application_id
                   ORDER BY created_at DESC LIMIT 1)
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(applicationId, company.id).first();
        if (!row) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        return json(request, { ok: true, content_client_md: row.content_client_md || null });
      }

      // GET /portal/:token/jobs/:slug/funnel — 這個職缺、這家客戶專屬的漏斗數字，
      // 讓用人單位不用問顧問就知道「量」到哪裡。跟 /admin/jobs 的內部漏斗算法
      // 分開寫：客戶只該看到跟自己這個職缺相關的數字，不是全公司總覽；
      // 「顧問手動加入」「進入AI面談」這兩格是內部才看得到的漏斗完全沒有的東西，
      // 特地為這次的需求另外查。
      if (parts.length === 4 && parts[1] === 'jobs' && parts[3] === 'funnel' && request.method === 'GET') {
        const slug = parts[2];
        const job = await env.DB.prepare(`SELECT slug, company_id FROM jobs WHERE slug=?`).bind(slug).first();
        if (!job || job.company_id !== company.id) return json(request, { ok: false, error: '找不到這個職缺' }, 404);

        const shortlisted = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM sourced_candidates WHERE job_slug=? AND status IN ('shortlisted','contacted','replied')`
        ).bind(slug).first();
        const interviewed = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM applications WHERE job_slug=? AND interview_started_at IS NOT NULL`
        ).bind(slug).first();
        const passed = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM reports r JOIN applications a ON a.id=r.application_id
            WHERE a.job_slug=? AND r.consultant_decision='forwarded'`
        ).bind(slug).first();
        const clientStage = await env.DB.prepare(
          `SELECT COUNT(DISTINCT p.application_id) AS n FROM placements p
            WHERE p.job_slug=? AND UPPER(p.stage) IN
              ('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW','AWAITING_CLIENT_FEEDBACK','INTERVIEW_COMPLETED')
              AND p.onboard_date IS NULL`
        ).bind(slug).first();
        const offered = await env.DB.prepare(
          `SELECT COUNT(DISTINCT p.application_id) AS n FROM placements p
            WHERE p.job_slug=? AND UPPER(p.stage) IN ('OFFER','OFFER_ACCEPTED','HIRED','PLACED')
              AND p.onboard_date IS NULL`
        ).bind(slug).first();
        const onboard = await env.DB.prepare(
          `SELECT COUNT(DISTINCT p.application_id) AS n FROM placements p
            WHERE p.job_slug=? AND p.onboard_date IS NOT NULL`
        ).bind(slug).first();

        return json(request, { ok: true, funnel: {
          shortlisted: shortlisted.n, interviewed: interviewed.n, passed_screening: passed.n,
          client_stage: clientStage.n, offered: offered.n, onboard: onboard.n,
        } });
      }

      // GET /portal/:token/candidates/:applicationId/messages — 這位人選跟顧問
      // 的訊息串。開啟＝已讀，直接把 client_unread_count 歸零，不用另做已讀按鈕
      // （沿用阿財聊天室「打開視窗就是看過了」的心智模型）。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'messages' && request.method === 'GET') {
        const applicationId = parts[2];
        const owns = await env.DB.prepare(
          `SELECT 1 FROM candidate_forwards WHERE application_id=? AND company_id=?`
        ).bind(applicationId, company.id).first();
        if (!owns) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const { results } = await env.DB.prepare(
          `SELECT id, from_role, content, from_name, created_at FROM candidate_messages
            WHERE application_id=? AND company_id=? ORDER BY id ASC`
        ).bind(applicationId, company.id).all();
        await env.DB.prepare(
          `UPDATE candidate_forwards SET client_unread_count=0 WHERE application_id=? AND company_id=?`
        ).bind(applicationId, company.id).run();
        return json(request, { ok: true, messages: results || [] });
      }

      // POST /portal/:token/candidates/:applicationId/messages body {content} —
      // 用人單位傳訊息給顧問。轉發到這家客戶專屬的 Telegram 主題（見
      // getOrCreateClientTopic），並且記下 Telegram 回傳的 message_id——顧問在
      // TG 直接「回覆」那則訊息，webhook 才能比對回這個人選的訊息串
      // （見 /telegram/webhook）。通知內容夾帶人選目前階段／職缺名稱／顧問寫給
      // 客戶看的摘要，都是系統既有欄位，不臨時編內容。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'messages' && request.method === 'POST') {
        const applicationId = parts[2];
        const fwd = await env.DB.prepare(
          `SELECT manual_stage, client_note FROM candidate_forwards WHERE application_id=? AND company_id=?`
        ).bind(applicationId, company.id).first();
        if (!fwd) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const b = await request.json().catch(() => ({}));
        const content = String(b.content || '').trim();
        if (!content) return json(request, { ok: false, error: '訊息不能是空的' }, 400);
        const app = await env.DB.prepare(
          `SELECT a.name AS name, a.job_slug AS job_slug, j.title AS job_title
             FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id=?`
        ).bind(applicationId).first();
        const ins = await env.DB.prepare(
          `INSERT INTO candidate_messages (application_id, company_id, from_role, content, created_at)
           VALUES (?, ?, 'client', ?, datetime('now','+8 hours'))`
        ).bind(applicationId, company.id, content).run();
        const msgId = ins.meta && ins.meta.last_row_id;
        const stageLb = fwd.manual_stage
          ? (STAGE_ORDER.find((s) => s.key === fwd.manual_stage) || {}).lb || fwd.manual_stage
          : '尚未指定階段';
        const jobLine = app && (app.job_title || app.job_slug) ? `\n職缺：${app.job_title || app.job_slug}` : '';
        const noteLine = fwd.client_note ? `\n顧問備註：${fwd.client_note}` : '';
        const topicId = await getOrCreateClientTopic(env, company);
        const tgId = await notifyAndCaptureId(env,
          `💬 ${company.display_name} 針對人選傳了訊息\n` +
          `人選：${(app && app.name) || applicationId}${jobLine}\n目前階段：${stageLb}${noteLine}\n\n` +
          `${content}\n\n─ 回覆這則訊息即可送回用人單位 ─`,
          topicId ? { message_thread_id: topicId } : { message_thread_id: THREAD.decide });
        if (msgId && tgId) {
          await env.DB.prepare(`UPDATE candidate_messages SET tg_message_id=? WHERE id=?`)
            .bind(tgId, msgId).run();
        }
        return json(request, { ok: true });
      }

      // POST /portal/:token/candidates/:applicationId/advance — 用人單位在表格
      // 「用人單位」欄選「推進」。2026-09-01 加：這格原本選了「推進」什麼事都
      // 不會發生（純粹是預設值、不算真的動作），但 Jacky 要的是：①客戶明確
      // 表態要推進，顧問要收到通知才知道可以安排面試；②面試階段／錄取／報到
      // 這三欄要等客戶真的按過「推進」才能填，不然還沒決定要不要推進，
      // 這些欄位填了也沒意義。用同一個 candidate_forwards.client_advanced_at
      // 時間戳同時滿足這兩個需求：有值＝前端解鎖後面三欄；第一次寫入才發
      // TG 通知（避免同一位人選被重複點很多次「推進」灌爆顧問的 TG）。
      if (parts.length === 4 && parts[1] === 'candidates' && parts[3] === 'advance' && request.method === 'POST') {
        const applicationId = parts[2];
        const fwd = await env.DB.prepare(
          `SELECT client_advanced_at FROM candidate_forwards WHERE application_id=? AND company_id=?`
        ).bind(applicationId, company.id).first();
        if (!fwd) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        if (fwd.client_advanced_at) return json(request, { ok: true, client_advanced_at: fwd.client_advanced_at, already: true });
        const now = nowTaipei();
        await env.DB.prepare(
          `UPDATE candidate_forwards SET client_advanced_at=? WHERE application_id=? AND company_id=?`
        ).bind(now, applicationId, company.id).run();
        const app = await env.DB.prepare(
          `SELECT a.name AS name, a.job_slug AS job_slug, j.title AS job_title
             FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id=?`
        ).bind(applicationId).first();
        const jobLine = app && (app.job_title || app.job_slug) ? `（${app.job_title || app.job_slug}）` : '';
        const topicId = await getOrCreateClientTopic(env, company);
        notify(env,
          `✅ ${company.display_name} 確認推進人選「${(app && app.name) || applicationId}」${jobLine}，可以安排接下來的面試了`,
          { message_thread_id: topicId || THREAD.decide }).catch(() => {});
        return json(request, { ok: true, client_advanced_at: now });
      }

      // GET /portal/:token/notifications — 通知鈴鐺角標，讀 candidate_forwards
      // 的 client_unread_count，不用另開一張已讀/未讀對照表。
      if (parts.length === 2 && parts[1] === 'notifications' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT cf.application_id, a.name, cf.client_unread_count AS unread_count,
                  (SELECT content FROM candidate_messages cm
                    WHERE cm.application_id = cf.application_id AND cm.company_id = cf.company_id
                    ORDER BY cm.id DESC LIMIT 1) AS last_message
             FROM candidate_forwards cf JOIN applications a ON a.id = cf.application_id
            WHERE cf.company_id = ? AND cf.client_unread_count > 0`
        ).bind(company.id).all();
        const items = results || [];
        const total = items.reduce((s, r) => s + (r.unread_count || 0), 0);
        return json(request, { ok: true, total_unread: total, items });
      }

      return json(request, { ok: false, error: 'not found' }, 404);
    }

    // ── AI 履歷健檢：收件（公開）──
    //
    // 🚨 這條路跟應徵（/apply）完全分開，寫的是 checkups 系列的表，
    //    **一個欄位都不會碰 applications**。
    //    來健檢的人沒有應徵任何職缺，混進 applications 會讓顧問後台的漏斗
    //    （應徵→談完→送客戶→客戶面試→到職）每一個數字都失真。
    //    規格：memory/project_resume_checkup.md
    //
    // 這一版**不收費**（就服法對「向求職者收費」的適用範圍還沒查清楚），
    // 所以 fee_status 一律寫 'free'，**不接受前端傳值**——
    // 前端能決定收費狀態的話，這個欄位就沒有任何意義了。
    if (p === '/checkup' && request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

      const name = String(b.name || '').trim();
      const email = String(b.email || '').trim();
      if (!name) return json(request, { ok: false, error: '請填姓名' }, 400);
      if (!email || !email.includes('@')) return json(request, { ok: false, error: '請填正確的 Email' }, 400);

      const files = Array.isArray(b.files) ? b.files : [];
      const links = (Array.isArray(b.links) ? b.links : [])
        .map((l) => (typeof l === 'string' ? { url: l } : l))
        .filter((l) => l && String(l.url || '').trim())
        .slice(0, 10);

      // 履歷是這個產品的原料。沒有履歷也沒有任何連結，健檢就無從做起——
      // 與其收下來讓他等一份做不出來的報告，不如當場講清楚。
      const hasResume = files.some((f) => (f.kind || 'resume') === 'resume' && f.b64);
      if (!hasResume && !links.length) {
        return json(request, { ok: false,
          error: '請上傳履歷檔案，或至少貼一個連結（作品集網站／LinkedIn／個人網站）' }, 400);
      }

      const id = uid();
      const now = nowTaipei();
      // 對談室的 token。跟 /apply 生 chat_token 是同一套做法（32 bytes hex），
      // 直接在送件當下就發，前端可以馬上帶本人進對談室，不用等信件往返。
      // 履歷還沒解析完也沒關係——checkup_parse.py 幾秒內就會跑到，
      // 阿福開口前一定會先讀到解析完的版本（daemon 每輪都先跑解析再處理對話）。
      const chatToken = [...crypto.getRandomValues(new Uint8Array(24))]
        .map((x) => x.toString(16).padStart(2, '0')).join('');

      // 檔案先存。存不進去就不要建單，免得留下一張沒有原料的健檢單。
      const savedFiles = [];
      for (const f of files) {
        if (!f || !f.b64) continue;
        const s = await saveUpload(env, f, now);
        if (s && s.tooBig) {
          return json(request, { ok: false,
            error: `「${s.name}」超過 8MB。改貼雲端連結（Google Drive／Dropbox）就沒有這個限制。` }, 400);
        }
        if (s) savedFiles.push({ ...s, kind: f.kind || 'other' });
      }
      const mainResume = savedFiles.find((s) => s.kind === 'resume') || null;

      await env.DB.prepare(
        `INSERT INTO checkups
           (id, created_at, name, email, phone, current_title, current_industry, note,
            resume_file_id, status, consent_at, fee_status,
            utm_source, utm_medium, utm_campaign, referrer, updated_at,
            chat_token, chat_state)
         VALUES (?,?,?,?,?,?,?,?,?,'new',?,'free',?,?,?,?,?,?,'not_started')`
      ).bind(
        id, now, name, email, b.phone || null,
        b.current_title || null, b.current_industry || null, b.note || null,
        mainResume ? mainResume.fileId : null, now,
        b.utm_source || null, b.utm_medium || null, b.utm_campaign || null,
        b.referrer || null, now, chatToken
      ).run();

      for (const s of savedFiles) {
        await env.DB.prepare(
          `INSERT INTO checkup_files (checkup_id, file_id, kind, created_at) VALUES (?,?,?,?)`
        ).bind(id, s.fileId, s.kind, now).run();
      }
      for (let i = 0; i < links.length; i++) {
        await env.DB.prepare(
          `INSERT INTO checkup_links (checkup_id, idx, url, label, created_at) VALUES (?,?,?,?,?)`
        ).bind(id, i, String(links[i].url).trim(), links[i].label || null, now).run();
      }

      // ⚠️ b.is_test 是給端到端驗證用的：資料照存，但不吵到真人。
      //    正式流量不會帶這個欄位，所以不影響顧問收到通知。
      if (!b.is_test) {
        await notify(env,
          `🩺 新的履歷健檢申請：${name}\n` +
          (b.current_title ? `目前職稱：${b.current_title}　` : '') +
          (b.current_industry ? `產業：${b.current_industry}\n` : '\n') +
          `Email：${email}\n` +
          `附件：${savedFiles.length} 個　連結：${links.length} 個\n` +
          `單號：${id}\n` +
          `接下來由阿福解析履歷並安排健檢對談（這不是應徵，沒有進漏斗）。`,
        { message_thread_id: CHECKUP_THREAD });
      }

      return json(request, {
        ok: true, checkup_id: id, files: savedFiles.length, links: links.length,
        chat_token: chatToken,
      });
    }

    // ── AI 履歷健檢：阿福對談室（跟阿財的 /chat/<token> 是同一個模式，
    //    但完全獨立的資料表——checkup_messages／checkups.chat_state，
    //    一個欄位都不會碰 messages／applications）──
    //
    // 端點只做三件事：進房間（含歷史）、送一句話、輪詢新訊息。
    // 回話的大腦是本機的 checkup_daemon.py，不在 Worker 裡呼叫模型
    // （跟阿財那條線同一個理由：不開額外的 API 金鑰，用本機 claude CLI）。
    // ── 阿福健檢：工作風格測驗（必填）──
    // 2026-08-14 加：跟阿財不一樣，這裡沒有中高階免測驗的例外——本人一律要先
    // 做完測驗才能進健檢對談室。獨立一張 checkup_assessments 表，不共用阿財的
    // assessments（那張掛在 applications.application_id，完全不同的資料世界）。
    if (p.startsWith('/checkup-assessment/') && request.method === 'GET') {
      const token = p.split('/')[2] || '';
      const c = await env.DB.prepare(
        `SELECT id, name FROM checkups WHERE chat_token = ?`
      ).bind(token).first();
      if (!c) return json(request, { ok: false, error: '連結無效' }, 404);
      const done = await env.DB.prepare(
        `SELECT id FROM checkup_assessments WHERE checkup_id = ? LIMIT 1`
      ).bind(c.id).first();
      return json(request, { ok: true, name: c.name, already_done: !!done });
    }

    if (p.startsWith('/checkup-assessment/') && request.method === 'POST') {
      const token = p.split('/')[2] || '';
      const c = await env.DB.prepare(
        `SELECT id FROM checkups WHERE chat_token = ?`
      ).bind(token).first();
      if (!c) return json(request, { ok: false, error: '連結無效' }, 404);

      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const s = b.scores || {};

      // 同一筆已經做過就不要再存第二筆——本人重整頁面重送同一份資料的情況。
      const done = await env.DB.prepare(
        `SELECT id FROM checkup_assessments WHERE checkup_id = ? LIMIT 1`
      ).bind(c.id).first();
      if (!done) {
        await env.DB.prepare(
          `INSERT INTO checkup_assessments
             (checkup_id, b5_o, b5_c, b5_e, b5_a, b5_n,
              grit, grit_interest, grit_effort,
              disc_d, disc_i, disc_s, disc_c, disc_primary,
              answers_json, quality_flag, seconds_taken, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
        ).bind(
          c.id, s.b5?.O ?? null, s.b5?.C ?? null, s.b5?.E ?? null, s.b5?.A ?? null, s.b5?.N ?? null,
          s.grit?.total ?? null, s.grit?.interest ?? null, s.grit?.effort ?? null,
          s.disc?.D ?? null, s.disc?.I ?? null, s.disc?.S ?? null, s.disc?.C ?? null, s.primary ?? null,
          JSON.stringify(b.answers || {}), s.quality ?? null, b.seconds ?? null, nowTaipei()
        ).run();
      }
      return json(request, { ok: true });
    }

    // ── 阿福健檢：容量分流＋預約（照抄阿財那一整套，換成 checkups）──
    //
    // 「送出健檢申請」那一按才是真正決定路線的地方（跟 /apply/update/:token
    // 同一個位置），不是在測驗交卷那一步就決定——資料在測驗前一步已經建檔，
    // 這裡只重新判斷「這一刻」的容量。
    if (p.startsWith('/checkup/update/') && request.method === 'POST') {
      const token = p.split('/')[2] || '';
      const c = await env.DB.prepare(
        `SELECT id, name, email, chat_token, remind_at, chat_state, status, ready_notified_at
           FROM checkups WHERE chat_token = ?`
      ).bind(token).first();
      if (!c) return json(request, { ok: false, error: '連結無效' }, 404);
      const assessed = await env.DB.prepare(
        `SELECT id FROM checkup_assessments WHERE checkup_id = ? LIMIT 1`
      ).bind(c.id).first();
      if (!assessed) {
        return json(request, {
          ok: false, error: 'assessment_required',
          assessment_url: `/checkup-assessment/?t=${token}&back=1`,
        }, 428);
      }
      const result = await decideCheckupRouteFresh(env, c);
      return json(request, { ok: true, ...result });
    }

    if (p === '/checkup-slots' && request.method === 'GET') {
      return json(request, { ok: true, slots: await checkupOpenSlots(env) });
    }

    if (p === '/checkup-book' && request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const c = await env.DB.prepare(
        `SELECT id, name, email, chat_token FROM checkups WHERE chat_token = ?`
      ).bind(b.token || '').first();
      if (!c) return json(request, { ok: false, error: '連結無效' }, 404);
      const slot = String(b.slot_at || '');
      if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(slot)) {
        return json(request, { ok: false, error: '時段格式不對' }, 400);
      }

      // 同一段競態處理：先寫再驗，超額就撤銷自己那筆，跟 /book 同一招。
      await env.DB.prepare(`UPDATE checkup_bookings SET cancelled_at=datetime('now','+8 hours')
                             WHERE checkup_id=? AND cancelled_at IS NULL`).bind(c.id).run();
      const ins = await env.DB.prepare(
        `INSERT INTO checkup_bookings (checkup_id, slot_at, created_at) VALUES (?,?,?)`
      ).bind(c.id, slot, nowTaipei()).run();
      const myId = ins.meta?.last_row_id;
      const cnt = await env.DB.prepare(
        `SELECT COUNT(*) n FROM checkup_bookings
          WHERE slot_at=? AND cancelled_at IS NULL AND id <= ?`
      ).bind(slot, myId).first();
      if ((cnt?.n || 0) > CHECKUP_SLOT_CAPACITY) {
        await env.DB.prepare(`UPDATE checkup_bookings SET cancelled_at=datetime('now','+8 hours')
                               WHERE id=?`).bind(myId).run();
        return json(request, { ok: false, error: '這個時段剛好被約滿了，請選其他時段',
                               slots: await checkupOpenSlots(env) }, 409);
      }

      await env.DB.prepare(
        `UPDATE checkups SET status='scheduled', remind_at=? WHERE id=?`
      ).bind(slot + ':00', c.id).run();

      await sendMail(
        env, c.email,
        `健檢對談時間已預約`,
        [`${c.name} 您好，`,
         `您的健檢對談已經約在 ${slot}。`,
         `時間到之前我們會再寄一次提醒，屆時用下面的連結進入對談室即可。`,
         `如果需要改時間，直接回信告訴我們就可以。`],
        { url: `https://step1ne.com/checkup-chat/?t=${c.chat_token}`, text: '進入健檢對談室' }
      );
      await notify(env, `📅 ${c.name} 預約了健檢對談：${slot}`, { message_thread_id: CHECKUP_THREAD });
      return json(request, { ok: true, slot_at: slot });
    }

    if (p.startsWith('/checkup-chat/')) {
      const seg = p.split('/').filter(Boolean); // ['checkup-chat', token, action?]
      const token = seg[1] || '';
      const action = seg[2] || '';
      if (token.length < 32) return json(request, { ok: false, error: 'bad token' }, 400);

      const c = await env.DB.prepare(
        `SELECT id, name, status, chat_state, report_html, report_pdf_file_id
           FROM checkups WHERE chat_token = ?`
      ).bind(token).first();
      if (!c) return json(request, { ok: false, error: 'not found' }, 404);

      // 健檢報告本文。本人用同一組 token 就能打開，不用另外寄連結或密碼。
      // 內容直接存在 checkups.report_html（見 schema_checkup_chat.sql 的說明：
      // Worker 連不到本機檔案系統，checkup_reports/*.html 那份是本機底稿用的）。
      if (action === 'report') {
        if (!c.report_html) return json(request, { ok: false, error: 'not_ready' }, 404);
        return new Response(c.report_html, {
          headers: { 'content-type': 'text/html; charset=utf-8', ...cors(request) },
        });
      }

      // 健檢對談結束後的體驗評分——照阿財 /chat/<token>/feedback 同一套，
      // 星星一點就送（rating 必填），意見欄選填，不填也能送出。
      if (action === 'feedback' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const rating = Number(b.rating);
        if (!(rating >= 1 && rating <= 5)) return json(request, { ok: false, error: '評分要在 1 到 5' }, 400);
        const comment = String(b.comment || '').trim().slice(0, 1000);
        await env.DB.prepare(
          `INSERT INTO checkup_feedback (checkup_id, created_at, rating, comment)
           VALUES (?,?,?,?)
           ON CONFLICT(checkup_id) DO UPDATE SET
             rating = excluded.rating, comment = excluded.comment, created_at = excluded.created_at`
        ).bind(c.id, nowTaipei(), rating, comment || null).run();
        if (rating <= 2) {
          await notify(env, `⚠️ 健檢體驗評分偏低：${rating}/5\n${c.name}\n` +
            (comment ? `他寫：${comment}\n` : '') + `健檢單號：${c.id}`,
            { message_thread_id: THREAD.system });
        }
        return json(request, { ok: true });
      }

      // PDF 版本——2026-08-12 Jacky 要求：報告要能讓本人「帶走」，不是只能在
      // 網頁上看。⚠️ 一開始直接存單一欄位 report_pdf_b64（~1.8MB base64）撞到
      // SQLITE_TOOBIG——D1 單值/單一 SQL 陳述式都有長度上限，即使走 --file 匯入
      // 一樣會被擋。改用履歷附件同一套 files／file_chunks 切塊機制（saveUpload），
      // 那套本來就是為了處理「單值存不下」這個情境設計的，不要重新發明。
      if (action === 'report.pdf') {
        const f = await fileB64(env, c.report_pdf_file_id);
        if (!f) return json(request, { ok: false, error: 'not_ready' }, 404);
        const bytes = Uint8Array.from(atob(f.content), (ch) => ch.charCodeAt(0));
        return new Response(bytes, {
          headers: {
            'content-type': 'application/pdf',
            'content-disposition': `attachment; filename="AI履歷健檢報告_${encodeURIComponent(c.name || '')}.pdf"`,
            ...cors(request),
          },
        });
      }

      // 本人送出一句話
      if (action === 'send' && request.method === 'POST') {
        if (c.chat_state === 'done') {
          return json(request, { ok: false, error: '這場對談已經結束了' }, 409);
        }
        // ⚠️ 2026-08-14 加：一律要先做完工作風格測驗才能開口——沒有阿財那種
        // 中高階免測驗的例外。對談室入口只驗 chat_token，任何拿到連結的人
        // 都能直接開始打字，只在前端擋等於沒擋，要在這裡也擋一次。
        //
        // ⚠️ 這道閘門是這次才加的，在此之前已經開始對談的場次（例如陳厚瑞那場
        // 因為額度事故被中斷、寄了道歉信請他回來繼續）不能被追溯擋下來——
        // 已經有真實訊息（排除開場暗號）就代表這場在測驗關卡出現以前就開始了，
        // 直接放行，不要讓他覺得「怎麼回來就被要求重來」。
        const hadRealMsg = await env.DB.prepare(
          `SELECT id FROM checkup_messages WHERE checkup_id = ? AND content != ? LIMIT 1`
        ).bind(c.id, '（本人已進入健檢對談室）').first();
        const assessed = hadRealMsg ? true : await env.DB.prepare(
          `SELECT id FROM checkup_assessments WHERE checkup_id = ? LIMIT 1`
        ).bind(c.id).first();
        if (!assessed) {
          return json(request, {
            ok: false, error: 'assessment_required',
            message: '開始健檢對談前需要先完成工作風格測驗（約 5–7 分鐘）',
            assessment_url: `/checkup-assessment/?t=${token}`,
          }, 428);
        }
        // ⚠️ 排隊也要在這裡擋，理由跟阿財那邊一樣：對談室入口只驗 token，
        // 被分流去預約的人只要把網址存起來，隨時可以直接走進來，分流就形同虛設。
        if (c.chat_state !== 'active' &&
            (c.status === 'awaiting_booking' || c.status === 'scheduled')) {
          const load = await checkupLiveLoad(env);
          if (load.active + load.pending >= CHECKUP_LIVE_LIMIT) {
            return json(request, {
              ok: false, error: 'queue_full',
              message: `目前有 ${load.active + load.pending} 位在進行健檢對談，請先預約一個時段`,
              book_url: `/checkup-book/?t=${token}`,
            }, 429);
          }
          await env.DB.prepare(`UPDATE checkups SET status='ready' WHERE id=?`).bind(c.id).run();
        }
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const text = String(b.text || '').trim().slice(0, 4000);
        if (!text) return json(request, { ok: false, error: '訊息是空的' }, 400);

        const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO checkup_messages (checkup_id, role, content, created_at) VALUES (?,?,?,?)`
        ).bind(c.id, 'user', text, now).run();

        // 中途離開又回來：房間時鐘歸零，不然會被「太久沒動」誤判成早就該收尾。
        if (c.chat_state === 'paused') {
          await env.DB.prepare(
            `UPDATE checkups SET chat_state='active', chat_started_at=?, chat_ended_at=NULL WHERE id=?`
          ).bind(now, c.id).run();
        } else if (c.chat_state !== 'active') {
          await env.DB.prepare(
            `UPDATE checkups SET chat_state='active', chat_started_at=COALESCE(chat_started_at,?) WHERE id=?`
          ).bind(now, c.id).run();
        }
        return json(request, { ok: true });
      }

      // 輪詢新訊息。after 是前端已經拿到的最後一個 id。
      if (action === 'poll') {
        const after = Number(url.searchParams.get('after') || 0) || 0;
        const { results } = await env.DB.prepare(
          `SELECT id, role, content, created_at FROM checkup_messages
            WHERE checkup_id = ? AND id > ? ORDER BY id ASC LIMIT 50`
        ).bind(c.id, after).all();
        return json(request, {
          ok: true, messages: results || [], state: c.chat_state,
          waiting: c.chat_state === 'active',
          report_ready: !!c.report_html,
        });
      }

      // 進入對談室：拿基本資料與全部歷史
      if (!action) {
        const { results } = await env.DB.prepare(
          `SELECT id, role, content, created_at FROM checkup_messages
            WHERE checkup_id = ? ORDER BY id ASC LIMIT 200`
        ).bind(c.id).all();
        return json(request, {
          ok: true, name: c.name, state: c.chat_state,
          // 履歷還沒解析完（checkup_parse.py 還沒跑到這筆）時，前端顯示「阿福正在讀你的履歷」。
          resume_ready: c.status !== 'new',
          messages: results || [],
          report_ready: !!c.report_html,
        });
      }

      return json(request, { ok: false, error: 'unknown action' }, 404);
    }

    // ── 人選挑面談時段的頁面——公開，token 即權限，跟 /book 同一套認證模式。
    //    ⚠️ 2026-08-13 一開始誤放在下面 `/admin/` 那個要驗證權杖的區塊裡，
    //    因為 `if (p.startsWith('/admin/'))` 本身就會擋掉不是 /admin/ 開頭
    //    的路徑，導致這支永遠進不去、外部一律收到 404——實測到才發現，
    //    搬出來變成獨立的公開區塊，跟 /checkup-chat/、/chat/ 同一個層級。
    if (p.startsWith('/appointment/')) {
      const seg = p.split('/').filter(Boolean);
      const token = seg[1] || '';
      if (token.length < 20) return json(request, { ok: false, error: 'bad token' }, 400);

      const appt = await env.DB.prepare(
        `SELECT ia.*, a.name, a.job_title, a.job_slug
           FROM interview_appointments ia
           JOIN applications a ON a.id = ia.application_id
          WHERE ia.token = ?`
      ).bind(token).first();
      if (!appt) return json(request, { ok: false, error: 'not found' }, 404);

      if (request.method === 'GET') {
        return json(request, {
          ok: true, name: appt.name, job_title: appt.job_title || appt.job_slug,
          note: appt.note, status: appt.status,
          slots: safeJsonArray(appt.slots), confirmed_slot: appt.confirmed_slot,
        });
      }

      if (seg[2] === 'confirm' && request.method === 'POST') {
        const b = await request.json();
        const r = await confirmAppointmentByToken(env, token, b.slot_at);
        // 2026-08-13 加：走網頁確認的話沒有 LINE replyToken 可以回，
        // 但如果這位候選人也綁過 LINE，還是要推一張跟 LINE 版一樣的確認卡給他，
        // 不然他在網頁上點的、跟他在 LINE 裡看到的內容會兜不起來。
        if (r.ok) {
          try {
            const { results } = await env.DB.prepare(
              `SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`
            ).all();
            const hits = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(r.appt.application_id));
            const flex = { type: 'flex', altText: `✅ 已確認面談時間：${r.picked.slot_at}`,
              contents: appointmentConfirmedFlexCard(r.appt, r.picked) };
            for (const row of hits) await linePushMessages(env, row.line_user_id, [flex]);
          } catch { /* 推播失敗不影響網頁上的確認結果 */ }
        }
        return json(request, r, r.ok ? 200 : (r.status || 400));
      }

      return json(request, { ok: false, error: 'not found' }, 404);
    }

    // ── 測驗：讀取這個人的狀態（前端進測驗頁時先問一次）──
    if (p.startsWith('/assessment/') && request.method === 'GET') {
      const token = p.split('/')[2] || '';
      const app = await env.DB.prepare(
        `SELECT id, name, job_title, job_slug, status FROM applications WHERE chat_token = ?`
      ).bind(token).first();
      if (!app) return json(request, { ok: false, error: '連結無效' }, 404);
      const done = await env.DB.prepare(
        `SELECT id FROM assessments WHERE application_id = ? LIMIT 1`
      ).bind(app.id).first();
      return json(request, {
        ok: true, name: app.name, job_title: app.job_title || app.job_slug,
        already_done: !!done,
      });
    }

    // ── 測驗：交卷 ──
    // 交卷之後才決定他走哪一條路（立即／預約／等顧問核准），
    // 因為容量是「這一刻」的狀態，填表當下算的到交卷時早就過期了。
    if (p.startsWith('/assessment/') && request.method === 'POST') {
      const token = p.split('/')[2] || '';
      const app = await env.DB.prepare(
        `SELECT a.id, a.name, a.email, a.job_slug, a.job_title, a.status, a.chat_token,
                a.remind_at, a.interview_state, a.ready_notified_at,
                a.expected_salary, a.available_date, a.resume_file_id, a.resume_url,
                j.title AS job_full_title, j.client_screen_conditions
           FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug
          WHERE a.chat_token = ?`
      ).bind(token).first();
      if (!app) return json(request, { ok: false, error: '連結無效' }, 404);

      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const s = b.scores || {};

      await env.DB.prepare(
        `INSERT INTO assessments
           (application_id, b5_o, b5_c, b5_e, b5_a, b5_n,
            grit, grit_interest, grit_effort,
            disc_d, disc_i, disc_s, disc_c, disc_primary,
            answers_json, quality_flag, seconds_taken)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
      ).bind(
        app.id, s.b5?.O ?? null, s.b5?.C ?? null, s.b5?.E ?? null, s.b5?.A ?? null, s.b5?.N ?? null,
        s.grit?.total ?? null, s.grit?.interest ?? null, s.grit?.effort ?? null,
        s.disc?.D ?? null, s.disc?.I ?? null, s.disc?.S ?? null, s.disc?.C ?? null, s.primary ?? null,
        JSON.stringify(b.answers || {}), s.quality ?? null, b.seconds ?? null
      ).run();

      // DISC 也回寫 applications，維持舊欄位相容（阿財與報告都還在讀那幾欄）
      await env.DB.prepare(
        `UPDATE applications SET disc_d=?, disc_i=?, disc_s=?, disc_c=?, disc_primary=? WHERE id=?`
      ).bind(s.disc?.D ?? null, s.disc?.I ?? null, s.disc?.S ?? null,
             s.disc?.C ?? null, s.primary ?? null, app.id).run();

      // 需要顧問核准的職缺，測驗做完也還是要等——不能讓他直接進面談室。
      // ⚠️ 2026-08-07：這裡才是真正該通知顧問的時機點。原本 notifyScreening()
      // 是在 /apply 一送出基本資料就打，導致顧問在候選人連測驗都還沒填時
      // 就收到「核准/婉拒」卡片。現在測驗都交卷了、資料齊全，才是顧問真的
      // 該被叫去看一眼的時候。
      if (app.client_screen_conditions) {
        await env.DB.prepare(`UPDATE applications SET status='pending_screen' WHERE id=?`)
          .bind(app.id).run();
        await notifyScreening(env, {
          id: app.id, name: app.name, job_slug: app.job_slug,
          expected_salary: app.expected_salary, available_date: app.available_date,
          resume_file_id: app.resume_file_id, resume_url: app.resume_url,
        }, { title: app.job_full_title, client_screen_conditions: app.client_screen_conditions });
        return json(request, { ok: true, route: 'screening' });
      }

      // ⚠️ 2026-08-14 修：真實流程是「/apply/ → /assessment/ 交卷後不show結果、
      // 直接跳回 /apply/（帶 back=1）→ 使用者在 /apply/ 按下最後的『送出應徵』
      // 才是真正決定進面談室還是約時間的地方」（見 apply/index.html 送出按鈕
      // 那段註解）。但這裡原本無條件呼叫 decideRouteFresh()，等於候選人測驗一
      // 交卷就先寄一封「面談連結」，緊接著 /apply/update/:token 又重新判斷一次
      // （容量可能已經變了）又寄一次別的信——Jacky 自己測試抓到一次應徵流程
      // 收到 2-3 封信。
      //
      // back=1（會跳回 /apply/ 再走一次送出流程）就不在這裡決定＋寄信，交給
      // /apply/update/:token 當唯一的終點；只有直接用連結進來測驗、沒有
      // apply/update 這一關可以接手的舊流程／顧問建檔連結（back 不是 1），
      // 才在這裡走完整版 decideRouteFresh()，不然那些人交完卷會沒有下一步。
      if (b.back) {
        return json(request, { ok: true });
      }
      const result = await decideRouteFresh(env, app);
      return json(request, { ok: true, ...result });
    }

    // ── 時段列表（預約頁重新整理時用）──
    if (p === '/slots' && request.method === 'GET') {
      return json(request, { ok: true, slots: await openSlots(env) });
    }

    // ── 預約時段 ──
    if (p === '/book' && request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const app = await env.DB.prepare(
        `SELECT id, name, email, job_title, job_slug, chat_token FROM applications WHERE chat_token = ?`
      ).bind(b.token || '').first();
      if (!app) return json(request, { ok: false, error: '連結無效' }, 404);
      const slot = String(b.slot_at || '');
      if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(slot)) {
        return json(request, { ok: false, error: '時段格式不對' }, 400);
      }

      // ⚠️ 這裡會有競態：兩個人同時搶最後一個名額。
      // D1 沒有交易，所以先寫再驗——寫進去之後重數一次，
      // 如果發現自己是超額的那一筆就撤銷，請他重選。
      await env.DB.prepare(`UPDATE interview_bookings SET cancelled_at=datetime('now','+8 hours')
                             WHERE application_id=? AND cancelled_at IS NULL`).bind(app.id).run();
      const ins = await env.DB.prepare(
        `INSERT INTO interview_bookings (application_id, slot_at) VALUES (?,?)`
      ).bind(app.id, slot).run();
      const myId = ins.meta?.last_row_id;
      const cnt = await env.DB.prepare(
        `SELECT COUNT(*) n FROM interview_bookings
          WHERE slot_at=? AND cancelled_at IS NULL AND id <= ?`
      ).bind(slot, myId).first();
      if ((cnt?.n || 0) > SLOT_CAPACITY) {
        await env.DB.prepare(`UPDATE interview_bookings SET cancelled_at=datetime('now','+8 hours')
                               WHERE id=?`).bind(myId).run();
        return json(request, { ok: false, error: '這個時段剛好被約滿了，請選其他時段',
                               slots: await openSlots(env) }, 409);
      }

      await env.DB.prepare(
        `UPDATE applications SET status='scheduled', interview_mode='later', remind_at=? WHERE id=?`
      ).bind(slot + ':00', app.id).run();

      await sendMail(
        env, app.email,
        `面談時間已預約：${app.job_title || app.job_slug}`,
        [`${app.name} 您好，`,
         `您的初步面談已經約在 ${slot}。`,
         `時間到之前我們會再寄一次提醒，屆時用下面的連結進入面談室即可。`,
         `如果需要改時間，直接回信告訴我們就可以。`],
        { url: `https://step1ne.com/interview/?t=${app.chat_token}`, text: '進入面談室' }
      );
      await notify(env, `📅 ${app.name} 預約了面談：${slot}\n職缺：${app.job_title || app.job_slug}`,
                   { message_thread_id: 4 });
      return json(request, { ok: true, slot_at: slot });
    }

    // LINE OA「查詢面試進度」webhook（全民獵才帳號）。公開端點，
    // LINE 從外面呼叫沒辦法帶 ADMIN_TOKEN，改用 x-line-signature 驗證。
    //
    // ⚠️ 2026-08-12 加。這支目前**沒有**被設成 LINE 頻道正式的 webhook 網址——
    //    那個網址正式指到 linehook.step1ne.com（見 Cloudflare API 查到的
    //    channel/webhook/endpoint），要不要切過來由 Jacky 決定，不在這裡自己切。
    //    這支路由先寫好等切換；也可以先用假的 LINE 事件 payload 直接打這支測。
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
              const since = it.date || now.slice(0, 10);
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
        const row = await env.DB.prepare(
          `SELECT q.id, q.job_slug, q.account_id, q.status, q.draft, q.requested_at, j.title
             FROM social_post_queue q JOIN jobs j ON j.slug = q.job_slug WHERE q.id = ?`
        ).bind(qid).first();
        if (!row) { await answer('❌ 找不到這筆排隊紀錄（可能太舊或已被清除）'); return new Response('ok'); }
        const who = (cq.from && (cq.from.username || cq.from.first_name)) || '顧問';

        if (action === 'soc_approve') {
          if (!row.draft) { await answer('❌ 這則沒有草稿內容，沒辦法發文'); return new Response('ok'); }

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
            return new Response('ok');
          }

          // 🚨 客戶名稱稽核。產稿時已經擋過一次，但草稿是存下來的，
          // 而客戶名單會新增（今天就補了台灣美光與帆宣兩筆）——
          // 發出去那一刻用最新的名單再掃一次才算數。
          const terms = await clientNameTerms(env);
          const nameHits = hitsClientNames(row.draft, terms);
          if (nameHits.length) {
            await env.DB.prepare(`UPDATE social_post_queue SET status='blocked_compliance' WHERE id=?`).bind(row.id).run();
            await answer(`🚫 這則出現客戶公司名稱「${nameHits.join('」「')}」，社群一律不提客戶名，已擋下。請按「🔄 重新產一次」。`, true);
            return new Response('ok');
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
            return new Response('ok');
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
            return new Response('ok');
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
            if (!acc) { await answer('❌ 指定的發文帳號找不到或已停用'); return new Response('ok'); }
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
            await notify(env,
              `📋 LINE 社群貼文已產好（${accLabel || '手動'}）\n` +
              `職缺：${row.job_slug}\n\n` +
              `⚠️ 這一則不會自動發出去，請自己複製貼到社群：\n` +
              `━━━━━━━━━━━━\n${row.draft}\n━━━━━━━━━━━━`,
              { message_thread_id: THREAD.decide }).catch(() => {});
            return new Response('ok');
          }

          if (!threadsToken || !threadsUserId) {
            await answer('⚠️ Threads 金鑰還沒設定，先標記核准，不會真的發出去。', true);
            await env.DB.prepare(`UPDATE social_post_queue SET status='approved' WHERE id=?`).bind(qid).run();
            return new Response('ok');
          }
          try {
            // ── LinkedIn ──
            // 2026-08-19 加。LinkedIn 跟 Threads 差在三件事，所以不共用同一段：
            //   ① 單則上限 3000 字，這個長度的招募文完全放得下，不用切串文
            //   ② 沒有「回覆自己」的串接概念，窗口連結直接接在本文最後
            //   ③ 發文是一次呼叫，沒有 Threads 那種「先建 container 再 publish」
            if (platform === 'linkedin') {
              const goLink = `https://step1ne.com/go/?c=${row.account_id}&j=${encodeURIComponent(row.job_slug)}`;
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
              return new Response('ok');
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
            // 2026-08-19 改：不再直接貼 lin.ee，改走自家轉址頁。
            // 直接放 LINE 連結的話，候選人一加進去就斷線——LINE 不會告訴我們
            // 他是從誰的哪則貼文來的，發文成效永遠只能看瀏覽數，看不到帶進幾個人。
            // 轉址頁會記下點擊再把人送去同一個 LINE，候選人那端多不到半秒。
            const goLink = row.account_id
              ? `https://step1ne.com/go/?c=${row.account_id}&j=${encodeURIComponent(row.job_slug)}`
              : lineLink;   // 沒指定帳號的舊職缺照舊，不要為了統計改變既有行為
            await postOne(`▪️ 應徵了解窗口：\n${goLink}`, lastId);

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

    // ── 應徵資料的「檢查再送出」修改 ──
    // 2026-08-07 加。候選人做完測驗跳回申請表時，看到的是自己剛才填的那份表單，
    // 不是另一個報告畫面——他可以在這裡改東西（例如發現薪資打錯字），
    // 改完按送出應徵，這裡要真的把修改存回去，不能只是畫面上看起來改了。
    // 用 chat_token 認證，跟 /assessment/:token、/book 是同一套模式。
    if (p.startsWith('/apply/update/') && request.method === 'POST') {
      const token = p.slice('/apply/update/'.length);
      const app = await env.DB.prepare(
        `SELECT id, resume_file_id, resume_url FROM applications WHERE chat_token = ?`
      ).bind(token).first();
      if (!app) return json(request, { ok: false, error: '連結無效' }, 404);

      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

      // 跟 /apply 同一套必填檢查，不要維護兩份規則
      const need = ['name', 'email', 'job_slug'];
      for (const k of need) {
        if (!String(b[k] || '').trim()) {
          return json(request, { ok: false, error: '請填寫姓名、Email 與應徵職缺' }, 400);
        }
      }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(b.email)) {
        return json(request, { ok: false, error: 'Email 格式看起來不正確' }, 400);
      }
      if (!String(b.expected_salary || '').trim() || !String(b.available_date || '').trim()) {
        return json(request, { ok: false, error: '請填寫期望待遇與最快可到職時間' }, 400);
      }
      if (!String(b.location_ok || '').trim()) {
        return json(request, { ok: false, error: '請至少選一個可接受的工作地區' }, 400);
      }
      const now = nowTaipei();
      let fileId = app.resume_file_id;
      let resumeUrl = app.resume_url;
      // 有帶新檔案才重存；沒帶就沿用原本的（不能因為這次沒重傳就把履歷清空）
      if (b.resume_b64) {
        const saved = await saveResume(env, b, now);
        if (saved && saved.tooBig) {
          return json(request, { ok: false, error: '履歷檔超過 8MB，請改用雲端連結欄位' }, 400);
        }
        if (saved) { fileId = saved.fileId; resumeUrl = null; }
      } else if (String(b.resume_url || '').trim()) {
        resumeUrl = b.resume_url;
      }
      if (!fileId && !resumeUrl) {
        return json(request, { ok: false, error: '請上傳履歷檔案，或貼上雲端連結（擇一即可）' }, 400);
      }

      await env.DB.prepare(
        `UPDATE applications SET name=?, email=?, phone=?, job_slug=?, job_title=?,
                expected_salary=?, available_date=?, location_ok=?, resume_file_id=?,
                resume_url=?, note=? WHERE id=?`
      ).bind(
        b.name, b.email, b.phone || null, b.job_slug, b.job_title || null,
        b.expected_salary, b.available_date, b.location_ok, fileId,
        resumeUrl, b.note || null, app.id
      ).run();

      // ⚠️ 2026-08-13 加：這裡才是候選人真正按下「送出應徵」的那一刻——用剛存進去
      // 的最新資料重新查一次，跟測驗交卷共用同一套 decideRouteFresh()，不要沿用
      // 前端 sessionStorage 存的舊 route（那是測驗交卷那一刻算的，填表審核資料
      // 這段期間房間可能早就滿了或空出來了）。
      const fresh = await env.DB.prepare(
        `SELECT a.id, a.name, a.email, a.job_slug, a.job_title, a.status, a.chat_token,
                a.remind_at, a.interview_state, a.ready_notified_at,
                j.title AS job_full_title
           FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug
          WHERE a.id = ?`
      ).bind(app.id).first();
      const result = await decideRouteFresh(env, fresh);
      return json(request, { ok: true, ...result });
    }

    // 人選點開邀請連結時，應徵表單拿這支去問「我是誰、要應徵什麼、履歷有沒有」。
    // ⚠️ 這支不帶 ADMIN_TOKEN，任何人拿到 token 都讀得到——所以只回傳
    //    「他自己本來就知道的東西」：他的姓名、信箱、要應徵哪個職缺。
    //    絕不回傳分數、等第、評語、負責顧問、來源網址那些內部判斷。
    if (p.startsWith('/apply/invite/') && request.method === 'GET') {
      const tk = decodeURIComponent(p.slice('/apply/invite/'.length));
      if (!tk || tk.length < 20) return json(request, { ok: false, error: '連結不正確' }, 400);
      const row = await env.DB.prepare(
        `SELECT s.id, s.name, s.email, s.invite_job_slug,
                (s.resume_file_id IS NOT NULL OR COALESCE(s.resume_url,'') <> '') AS has_resume,
                j.title AS job_title, COALESCE(j.status,'open') AS job_status
           FROM sourced_candidates s
           LEFT JOIN jobs j ON j.slug = s.invite_job_slug
          WHERE s.invite_token = ?`).bind(tk).first();
      if (!row) return json(request, { ok: false, error: '連結不正確或已失效' }, 404);
      if (row.job_status === 'closed') {
        return json(request, { ok: false, error: '這個職缺已經結束招募了，請聯繫你的顧問' }, 410);
      }
      return json(request, { ok: true,
        name: row.name || '', email: row.email || '',
        job_slug: row.invite_job_slug, job_title: row.job_title || '',
        has_resume: !!row.has_resume });
    }

    if (p === '/apply' && request.method === 'POST') {
      let b;
      try {
        b = await request.json();
      } catch {
        return json(request, { ok: false, error: '格式錯誤' }, 400);
      }

      const need = ['name', 'email', 'job_slug'];
      for (const k of need) {
        if (!String(b[k] || '').trim()) {
          return json(request, { ok: false, error: '請填寫姓名、Email 與應徵職缺' }, 400);
        }
      }
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(b.email)) {
        return json(request, { ok: false, error: 'Email 格式看起來不正確' }, 400);
      }
      // 2026-08-20 加：直播主這類職缺要看本人的社群經營狀況，光看履歷看不出來。
      // ⚠️ 哪些職缺要問由 jobs.need_social 決定，不寫死在前端——
      //    以後社群小編、影音編輯要收作品連結時，顧問在後台設一下就好。
      // ⚠️ 至少一個就好，不要求每個平台都填：有些人只經營一個平台但經營得很好，
      //    強迫填五格只會逼他亂填。
      const socialJob = await env.DB.prepare(
        `SELECT need_social FROM jobs WHERE slug = ?`).bind(b.job_slug).first();
      let socialLinks = null;
      if (socialJob && socialJob.need_social) {
        const raw = (b.social_links && typeof b.social_links === 'object') ? b.social_links : {};
        const clean = {};
        for (const k of ['instagram', 'tiktok', 'facebook', 'threads', 'youtube', 'other']) {
          const v = String(raw[k] || '').trim().slice(0, 300);
          if (v) clean[k] = v;
        }
        if (!Object.keys(clean).length) {
          return json(request, { ok: false, error: 'social_required',
            message: '這個職缺需要看您的社群經營狀況，請至少填寫一個平台的連結' }, 400);
        }
        socialLinks = JSON.stringify(clean);
      }
      // 沒有明確同意就不能存個資，這是法律要求不是流程設計
      if (!b.consent) {
        return json(request, { ok: false, error: '需要勾選同意個資使用說明' }, 400);
      }
      // ⚠️ 2026-08-07：期望待遇／可到職／地區／履歷這幾項原本只有前端擋，
      // 直接打 API 就繞得過去。跟前端用同一套規則，不要兩邊各自維護一份。
      if (!String(b.expected_salary || '').trim() || !String(b.available_date || '').trim()) {
        return json(request, { ok: false, error: '請填寫期望待遇與最快可到職時間' }, 400);
      }
      if (!String(b.location_ok || '').trim()) {
        return json(request, { ok: false, error: '請至少選一個可接受的工作地區' }, 400);
      }
      // 履歷是「上傳檔案」跟「貼連結」擇一，跟前端邏輯一致
      if (!(b.resume_b64 && String(b.resume_b64).trim()) && !String(b.resume_url || '').trim()) {
        return json(request, { ok: false, error: '請上傳履歷檔案，或貼上雲端連結（擇一即可）' }, 400);
      }
      // 2026-08-17 加：職缺被顧問標成關閉（找到人／暫停招募），這裡是真正的
      // 守門員——就算網站頁面沒即時換掉、或有人直接打 API，也不會生出新應徵、
      // 不會派給阿財面談。已經在談的人不受影響，這裡只擋「新」的一筆。
      const jobRow = await env.DB.prepare(`SELECT status FROM jobs WHERE slug = ?`).bind(b.job_slug).first();
      if (jobRow && jobRow.status === 'closed') {
        return json(request, { ok: false, error: '這個職缺目前暫停招募，請留意其他職缺，或透過官網聯繫我們。' }, 400);
      }

      const id = uid();
      const now = nowTaipei();
      let fileId = null;

      // ── 重複送件 ──
      // 2026-08-07：王雁群在 34 秒內送了兩次，資料庫就有兩個「王雁群」，
      // 一筆在面談、一筆掛在那邊「未開始」。顧問看到未開始那筆會去催一個已經面完的人。
      //
      // 30 分鐘內同 email＋同職缺一律當成「同一次應徵」，覆蓋原本那筆而不是新增。
      // 為什麼是覆蓋不是拒絕：最常見的重送情境是「第一次忘了附履歷，重填一次」，
      // 拒絕等於把他補的履歷丟掉。覆蓋則是後送的補上去，正是他想要的結果。
      //
      // 為什麼是 30 分鐘而不是永久：被婉拒的人幾週後重新應徵同一個缺是正常的，
      // 那要算新的一筆，不能跟幾週前那次混在一起。
      const dup = await env.DB.prepare(
        `SELECT id, interview_state, resume_file_id, resume_url FROM applications
          WHERE email = ? AND job_slug = ? AND superseded_by IS NULL
            AND created_at >= datetime(?, '-30 minutes')
          ORDER BY created_at DESC LIMIT 1`
      ).bind(b.email, b.job_slug, now).first();

      // 已經開始面談的那筆不能碰——覆蓋等於在面談進行中抽換他的資料。
      // 直接把他導回原本那間面談室，不要開第二間。
      if (dup && ['active', 'done', 'ended'].includes(dup.interview_state || '')) {
        const room = await env.DB.prepare(
          `SELECT chat_token FROM applications WHERE id = ?`).bind(dup.id).first();
        return json(request, { ok: true, id: dup.id, duplicate: 'in_progress',
                               chat_token: room ? room.chat_token : null });
      }

      const saved = await saveResume(env, b, now);
      if (saved && saved.tooBig) {
        return json(request, { ok: false,
          error: '履歷檔超過 8MB，請改用「履歷連結」欄位貼雲端連結' }, 400);
      }
      if (saved) fileId = saved.fileId;

      // 後送的那筆如果沒帶履歷，就把前一筆的接過來。
      // 最常見的重送情境是「第一次忘了附履歷，重填一次」，但反過來也會發生
      // （第一次附了、第二次只想改薪資期望），那時不接過來就等於把履歷弄丟。
      if (!fileId && dup && dup.resume_file_id) fileId = dup.resume_file_id;
      if (!String(b.resume_url || '').trim() && dup && dup.resume_url) {
        b.resume_url = dup.resume_url;
      }

      // ── 邀請連結帶進來的履歷 ──
      // 顧問先幫他傳好的那一份。人選走邀請連結進來時不用再傳一次——
      // 每多一個步驟就掉一批人，而履歷是最容易讓人放棄的那一步。
      let inviteSrc = null;
      if (b.invite_token) {
        inviteSrc = await env.DB.prepare(
          `SELECT id, resume_file_id, resume_url FROM sourced_candidates WHERE invite_token = ?`
        ).bind(String(b.invite_token)).first();
      }
      // 本人自己有傳就用他傳的（比較新）；沒傳才用顧問先存的那一份。
      const useFileId = fileId || (inviteSrc && inviteSrc.resume_file_id) || null;
      const useResumeUrl = b.resume_url || (inviteSrc && inviteSrc.resume_url) || null;

      // DISC 是表單裡的量表算出來的分數（前端算好才送過來），不是這裡現算
      const discOk = ['disc_d', 'disc_i', 'disc_s', 'disc_c']
        .every((k) => Number.isInteger(b[k]));

      await env.DB.prepare(
        `INSERT INTO applications
         (id, created_at, job_slug, job_title, name, email, phone,
          expected_salary, available_date, location_ok,
          resume_file_id, resume_url, resume_104_url, note,
          utm_source, utm_medium, utm_campaign, referrer, status, consent_at,
          disc_d, disc_i, disc_s, disc_c, disc_primary, social_links)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?,?,?,?,?, ?)`
      ).bind(
        id, now, b.job_slug, b.job_title || null, b.name, b.email, b.phone || null,
        b.expected_salary || null, b.available_date || null, b.location_ok || null,
        useFileId, useResumeUrl, b.resume_104_url || null, b.note || null,
        b.utm_source || null, b.utm_medium || null, b.utm_campaign || null,
        b.referrer || null, now,
        discOk ? b.disc_d : null, discOk ? b.disc_i : null,
        discOk ? b.disc_s : null, discOk ? b.disc_c : null,
        discOk ? (b.disc_primary || null) : null, socialLinks
      ).run();

      // ── 接回人才池 ──
      // 2026-08-27 加。在這之前 sourced_candidates.converted_application_id
      // 這個欄位從建表到現在**一次都沒被寫過**：顧問主動找到的人，後來自己
      // 跑來應徵、跟阿財談完，人才池那張卡片完全不知道——同一個人在系統裡
      // 變成兩筆互不相干的資料，顧問還在人才池那邊當他是「待接觸」。
      // 用 email 比對（大小寫與前後空白都吃掉），沒有 email 就退而用電話。
      try {
        const mail = String(b.email || '').trim().toLowerCase();
        const tel = String(b.phone || '').replace(/[^0-9]/g, '');
        // 走邀請連結進來的，是誰不用猜——顧問已經指定了。
        let hit = inviteSrc ? { id: inviteSrc.id } : null;
        if (!hit && mail) {
          hit = await env.DB.prepare(
            `SELECT id FROM sourced_candidates
              WHERE lower(trim(COALESCE(email,''))) = ? AND converted_application_id IS NULL
              LIMIT 1`).bind(mail).first();
        }
        // 電話比對只留末 9 碼再比——池子裡存的格式很雜（+886/0912/0912-345-678），
        // 而且電話多半是顧問打完之後寫在 note 裡的，不是獨立欄位。
        if (!hit && tel.length >= 8) {
          hit = await env.DB.prepare(
            `SELECT id FROM sourced_candidates
              WHERE converted_application_id IS NULL AND COALESCE(note,'') LIKE ?
              LIMIT 1`).bind('%' + tel.slice(-9) + '%').first();
        }
        if (hit) {
          await env.DB.prepare(
            `UPDATE sourced_candidates SET converted_application_id = ?, status = 'converted'
              WHERE id = ?`).bind(id, hit.id).run();
        }
      } catch (e) { /* 接不起來不影響應徵本身，不要因為這個擋掉求職者 */ }

      // 新的那筆已經安全寫進去了，這時才把舊的標記掉。
      // ⚠️ 標記不是刪除——刪掉就查不出「他到底送了幾次、每次填的一不一樣」。
      // superseded_by 指向新的那筆，隨時可以還原。
      if (dup) {
        await env.DB.prepare(
          `UPDATE applications SET superseded_by=?, status='duplicate',
                  interview_state='superseded' WHERE id=?`
        ).bind(id, dup.id).run();
      }

      // ⚠️ 2026-08-07：這裡原本會先查 client_screen_conditions，
      // 有硬性條件的職缺就立刻進顧問待審核清單、發核准通知——
      // 但那是「填測驗之前」的動作，導致顧問剛送出基本資料、連測驗都還沒填，
      // 顧問就收到「核准/婉拒」的通知卡。**審核判斷已經搬到測驗交卷那一刻**
      // （POST /assessment/:token 裡會查 client_screen_conditions），
      // 這裡不要再判斷一次、不要再發審核通知——這步只負責存資料、發連結。
      //
      // AI 面談不需要顧問在場，所以這裡不是「預約顧問時段」而是
      // 「現在做」或「晚點提醒自己回來做」。不查任何人的行事曆。
      const mode = b.mode === 'later' ? 'later' : 'now';
      // 面談室的網址就是憑證。用 crypto 產生，不要用 id——
      // id 會出現在同步給 pm 的資料裡，外洩了就等於面談逐字稿外洩。
      const chatToken = [...crypto.getRandomValues(new Uint8Array(24))]
        .map((x) => x.toString(16).padStart(2, '0')).join('');
      await env.DB.prepare(
        `UPDATE applications SET interview_mode=?, status='pending_assessment', chat_token=?,
                interview_state='not_started' WHERE id=?`
      ).bind(mode, chatToken, id).run();

      // 純資訊通知，不是待辦——這個人剛送出基本資料，正要去填測驗，
      // 還沒有任何需要顧問決定的事。丟一般進件那個 thread 就好。
      await notify(
        env,
        `📥 新應徵：${b.name}\n職缺：${b.job_title || b.job_slug}\n` +
          `Email：${b.email}\n正在填工作風格測驗，測驗完才知道下一步\n` +
          (b.expected_salary ? `期望：${b.expected_salary}　` : '') +
          (b.available_date ? `可到職：${b.available_date}\n` : '\n') +
          `來源：${b.utm_source || b.referrer || '直接進入'}`,
        { message_thread_id: INTAKE_THREAD }
      );

      // ⚠️ 2026-08-07 起，送出申請表之後不是直接進面談室，而是先做人格特質測驗。
      // 測驗交卷時才決定他走哪一條（立即／預約／等顧問核准）——
      // 因為容量是「那一刻」的狀態，填表當下算的到交卷時早就過期了。
      //
      // ⚠️ 2026-08-14 改：這封「已收到您的應徵」信原本在這裡立刻寄——但如果
      // 候選人一口氣把測驗也填完、系統判定可以立即面談，緊接著會再收到一封
      // 「面談連結」信，兩封信間隔太短，Jacky 自己測試時就覺得像重複寄信。
      // 改成不在這裡寄，交給 scheduled()：應徵滿 10 分鐘後還沒收到「面談連結」信
      // 才補寄這封（見 scheduled() 裡的 applied_notified_at 區塊）——
      // 一口氣填完測驗的人只會收到一封信，中途離開的人 10 分鐘後還是會收到
      // 這封當備援連結，不會音訊全無。

      return json(request, {
        ok: true, id, chat_token: chatToken,
        next: 'assessment',
        assessment_url: `/assessment/?t=${chatToken}`,
      });
    }


    // ── 面談室 ──
    // token 放在網址就是權限本身：候選人不必註冊帳號（多一道就少一半完成率），
    // 但也代表這個網址等於逐字稿的鑰匙，所以 token 要夠長且不可推導。
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
        if (exist) {
          await env.DB.prepare(
            `UPDATE social_accounts SET access_token=?, refresh_token=?, token_expires_at=?,
                    label=?, is_active=1 WHERE id=?`
          ).bind(td.access_token, td.refresh_token || null, expAt,
                 `${label} – LinkedIn`, exist.id).run();
        } else {
          await env.DB.prepare(
            // created_at 是 NOT NULL 且沒有預設值——2026-08-19 第一次綁定就撞到
            `INSERT INTO social_accounts (platform, platform_user_id, access_token, refresh_token,
                                          token_expires_at, label, is_active, created_at)
             VALUES ('linkedin', ?, ?, ?, ?, ?, 1, datetime('now','+8 hours'))`
          ).bind(sub, td.access_token, td.refresh_token || null, expAt, `${label} – LinkedIn`).run();
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

    // 哪些職缺要收社群連結（公開，給應徵表單用）。
    // 表單的職缺清單來自靜態的 /apply/jobs.json，不會即時反映後台設定，
    // 所以另外開這支——顧問在後台把某個職缺設成要收作品連結時，
    // 表單當下就會多出那個區塊，不用等靜態檔重新產生。
    if (p === '/jobs-social' && request.method === 'GET') {
      const { results } = await env.DB.prepare(
        `SELECT slug FROM jobs WHERE need_social = 1 AND COALESCE(status,'open') NOT IN ('closed','pending_review')`
      ).all();
      return json(request, { ok: true, slugs: (results || []).map((r) => r.slug) });
    }

    // ── AI 職缺配對（公開，不需驗證）──
    // 2026-08-19 加。訴求是「這個缺不適合？讓我們幫你配」——
    // 候選人看到某個職缺不合適時，不要讓他直接關掉，而是留下他、由我們配對其他機會。
    //
    // ⚠️ 配對用**規則**不用模型。三個理由：
    //   ① 候選人在等，模型要幾秒到幾十秒，那個延遲會讓人關掉
    //   ② 模型會編造不存在的職缺或條件，規則不會
    //   ③ 這個結果會直接影響他要不要投履歷，講錯比講得不漂亮嚴重得多
    // 模型只負責把配對結果寫成人話（而且失敗時有規則版的說明可以退回）。
    if (p === '/match' && request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const text = String(b.text || '').slice(0, 3000).trim();
      if (text.length < 8) {
        return json(request, { ok: false, error: '請多說一點，例如您的經歷、想找什麼樣的工作、期望待遇' }, 400);
      }

      const { results: jobs } = await env.DB.prepare(
        `SELECT slug, title, locations, must_skills, years_min, salary_min, salary_max,
                salary_note, employment, service_line, seniority, client_name, client_named,
                confidential_client
           FROM jobs WHERE COALESCE(status,'open') NOT IN ('closed','draft','pending_review')`
      ).all();

      // 斷詞：中文沒有空格，用 2–4 字的滑動視窗抓詞，再跟職缺文字比對。
      // 這比要求候選人填結構化欄位務實——他就是想用講的。
      const norm = (t) => String(t || '').toLowerCase().replace(/[\s,，、。；;()（）／/|]+/g, ' ');
      const grams = (t) => {
        const out = new Set();
        const clean = norm(t).replace(/\s+/g, '');
        for (let n = 2; n <= 4; n++) {
          for (let i = 0; i + n <= clean.length; i++) out.add(clean.slice(i, i + n));
        }
        // 英文與數字單獨抓（Revit、AWS、104 這類）
        for (const w of norm(t).split(' ')) if (w.length >= 2 && /[a-z0-9]/.test(w)) out.add(w);
        return out;
      };
      const cand = grams(text);

      // 地點：只認台灣常見的縣市與區，避免把「台北的客戶」誤判成他想在台北工作
      const AREAS = ['台北', '臺北', '新北', '桃園', '台中', '臺中', '台南', '臺南', '高雄',
                     '基隆', '新竹', '苗栗', '彰化', '南投', '雲林', '嘉義', '屏東', '宜蘭',
                     '花蓮', '台東', '臺東', '澎湖', '金門', '內湖', '南港', '松山', '信義',
                     '中和', '板橋', '銅鑼', '竹北', '日本', '東京', '白馬', '海外', '遠端', '在家'];
      const wantAreas = AREAS.filter((a) => text.includes(a));
      const wantMoney = (() => {
        const m = text.match(/(\d{2,3})\s*[kK萬]|月薪\s*(\d{4,6})|(\d{4,6})\s*元/);
        if (!m) return null;
        if (m[1]) return Number(m[1]) * (text.includes('萬') ? 10000 : 1000);
        return Number(m[2] || m[3]);
      })();
      const yrs = (() => {
        const m = text.match(/(\d{1,2})\s*年(以上|經驗|資歷)?/);
        return m ? Number(m[1]) : null;
      })();

      const scored = jobs.map((j) => {
        const hay = [j.title, j.must_skills, j.locations, j.salary_note].join(' ');
        const jg = grams(hay);
        let hits = [];
        for (const g of cand) if (g.length >= 2 && jg.has(g)) hits.push(g);
        // 長詞優先：命中「Revit」比命中「工程」有意義得多
        hits = [...new Set(hits)].sort((a, b) => b.length - a.length);
        const skillScore = Math.min(50, hits.slice(0, 12).reduce((s, h) => s + h.length * 2, 0));

        const locHit = wantAreas.some((a) => String(j.locations || '').includes(a));
        const locScore = wantAreas.length ? (locHit ? 25 : -15) : 0;

        let payScore = 0, payNote = '';
        if (wantMoney && j.salary_max) {
          if (wantMoney <= j.salary_max) payScore = 15;
          else { payScore = -20; payNote = `這個缺上限約 ${Math.round(j.salary_max / 1000)}K，低於您說的期望`; }
        }
        let yrScore = 0, yrNote = '';
        if (yrs !== null && j.years_min) {
          if (yrs >= j.years_min) yrScore = 10;
          else { yrScore = -25; yrNote = `這個缺要 ${j.years_min} 年以上，您提到 ${yrs} 年`; }
        }
        return { j, score: skillScore + locScore + payScore + yrScore,
                 hits: hits.slice(0, 6), locHit, payNote, yrNote };
      }).filter((x) => x.score > 8).sort((a, b) => b.score - a.score).slice(0, 4);

      const out = scored.map((x) => {
        const j = x.j;
        const why = [];
        if (x.hits.length) why.push(`您提到的「${x.hits.slice(0, 3).join('」「')}」跟這個職缺的需求對得上`);
        if (x.locHit) why.push('工作地點符合您說的區域');
        if (!x.yrNote && j.years_min) why.push(`年資門檻 ${j.years_min} 年，您的經歷有機會`);
        const gap = [x.payNote, x.yrNote].filter(Boolean);
        return {
          slug: j.slug, title: j.title, locations: j.locations,
          employment: j.employment, salary_note: j.salary_note,
          // 保密客戶不揭露名稱——這條規則在配對結果一樣要守
          company: (j.confidential_client || !j.client_named) ? null : j.client_name,
          why, gap,
          url: `https://step1ne.com/jobs/${j.slug}/?utm_source=match&utm_medium=ai&utm_campaign=job_match`,
        };
      });

      // 規則結果先回給候選人看（快），同時排進佇列讓本機的引擎做真正的判斷。
      // ⚠️ 為什麼要兩段：規則配得對但講不出人話（實測理由會寫成「您提到的『望月收』
      // 跟這個職缺對得上」），而模型講得好但要十幾秒。候選人盯著轉圈圈會走，
      // 所以先給他看得懂的東西，好了再自動換成更好的版本。
      const mid = uid();
      try {
        await env.DB.prepare(
          `INSERT INTO match_requests (id, created_at, input_text, shortlist_json, status, ua)
           VALUES (?, datetime('now','+8 hours'), ?, ?, 'pending', ?)`
        ).bind(mid, text, JSON.stringify(out.map((o) => o.slug)),
               (request.headers.get('user-agent') || '').slice(0, 200)).run();
      } catch { /* 排不進佇列也要讓他看到規則結果，不能整個失敗 */ }

      return json(request, {
        ok: true, id: mid, matched: out.length, jobs: out, stage: 'preliminary',
        note: out.length ? null
          : '目前站上的職缺跟您的方向沒有很吻合。留給我們您的聯絡方式，有新的機會我們會直接通知您。',
      });
    }

    // 輪詢 AI 版結果
    if (p.startsWith('/match/') && request.method === 'GET') {
      const mid = p.slice('/match/'.length);
      if (!mid || mid.length < 8) return json(request, { ok: false, error: 'bad id' }, 400);
      const r = await env.DB.prepare(
        `SELECT status, result_json FROM match_requests WHERE id = ?`).bind(mid).first();
      if (!r) return json(request, { ok: false, error: 'not found' }, 404);
      if (r.status !== 'done') return json(request, { ok: true, stage: r.status });
      let parsed = null;
      try { parsed = JSON.parse(r.result_json); } catch { /* 壞掉就當沒有，前端維持規則版 */ }
      return json(request, { ok: true, stage: 'done', result: parsed });
    }

    // 候選人留聯絡方式（配不到職缺時的退路）
    if (p === '/match/contact' && request.method === 'POST') {
      let b;
      try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
      const mid = String(b.id || '').slice(0, 60);
      const contact = String(b.contact || '').slice(0, 200).trim();
      if (!mid || !contact) return json(request, { ok: false, error: '缺少資料' }, 400);
      await env.DB.prepare(`UPDATE match_requests SET contact=? WHERE id=?`).bind(contact, mid).run();
      await notify(env, `📮 有人用職缺配對留了聯絡方式\n${contact}\n\n他的描述：\n`
        + String((await env.DB.prepare(`SELECT input_text FROM match_requests WHERE id=?`)
            .bind(mid).first() || {}).input_text || '').slice(0, 300),
        { message_thread_id: THREAD.intake }).catch(() => {});
      return json(request, { ok: true });
    }

    // ── 發文導流的轉址中繼（公開，不需驗證）──
    // 2026-08-19 加。原本貼文結尾直接放 lin.ee 連結，候選人加了 LINE 之後
    // 就完全斷線：LINE 的 webhook 不帶「他從哪個連結進來」，Insight API 也
    // 沒有分來源的端點，所以「哪個顧問的哪則貼文帶來這個人」永遠算不出來。
    //
    // 作法：貼文改放 step1ne.com/go/?c=帳號&j=職缺，那一頁打這支拿到真正的
    // LINE 連結再轉過去，點擊就記在我們自己手上。
    // ⚠️ 這支不可以擋人：查不到帳號、資料庫寫入失敗，都要照樣回一個可用的
    //    LINE 連結。統計掉一筆沒關係，把候選人卡在半路才是真的損失。
    if (p === '/go/resolve' && request.method === 'GET') {
        const accId = Number(url.searchParams.get('c')) || null;
        const slug = url.searchParams.get('j') || null;
        let target = 'https://lin.ee/RR4nQqm';   // 查不到時的退路（Jacky 那組）
        try {
          if (accId) {
            const acc = await env.DB.prepare(
              `SELECT line_link FROM social_accounts WHERE id=? AND platform='threads'`
            ).bind(accId).first();
            if (acc && acc.line_link) target = acc.line_link;
          }
        } catch { /* 查不到就用退路 */ }
        try {
          // 對應到最近一筆該帳號＋該職缺的發文，之後才能把點擊算回某一則貼文
          let qid = null;
          if (accId && slug) {
            const q = await env.DB.prepare(
              `SELECT id FROM social_post_queue WHERE account_id=? AND job_slug=? AND status='posted'
                ORDER BY posted_at DESC LIMIT 1`
            ).bind(accId, slug).first();
            qid = q ? q.id : null;
          }
          await env.DB.prepare(
            `INSERT INTO link_clicks (account_id, job_slug, queue_id, clicked_at, ua, country)
             VALUES (?, ?, ?, datetime('now','+8 hours'), ?, ?)`
          ).bind(accId, slug, qid,
                 (request.headers.get('user-agent') || '').slice(0, 200),
                 request.headers.get('cf-ipcountry') || null).run();
        } catch { /* 記不起來也要放人走 */ }
        return json(request, { ok: true, url: target });
      }

    // ── 人選客製表單填寫頁：/form/?t=<token> ──
    // 2026-09-02 加。網址帶的 token 本身就是憑證（Jacky 確認不用另外加身份
    // 驗證），公開、不用 ADMIN_TOKEN。⚠️ 這裡刻意用 query string（?t=）不是
    // /chat/:token 那種 path 型態——跟 form/index.html 前端頁面的呼叫方式
    // 對齊（同一套 URL 拼法錯了會直接讀不到 token，缺少連結，親自測過才抓到）。
    if (p === '/form' || p === '/form/') {
      const token = url.searchParams.get('t') || '';
      if (request.method === 'GET') {
        if (!token) return json(request, { ok: false, error: '缺少連結' }, 400);
        const sub = await env.DB.prepare(
          `SELECT cfs.status, cfs.submitted_at, jf.name AS form_name, a.name AS candidate_name,
                  cc.display_name AS company_name, j.title AS job_title
             FROM candidate_form_submissions cfs
             JOIN job_forms jf ON jf.id = cfs.job_form_id
             JOIN applications a ON a.id = cfs.application_id
             LEFT JOIN client_companies cc ON cc.id = cfs.company_id
             LEFT JOIN jobs j ON j.slug = jf.job_slug
            WHERE cfs.token = ?`
        ).bind(token).first();
        if (!sub) return json(request, { ok: false, error: '這個連結不存在，或已經失效' }, 404);
        return json(request, {
          ok: true, status: sub.status, submitted_at: sub.submitted_at,
          form_name: sub.form_name, candidate_name: sub.candidate_name,
          company_name: sub.company_name || '用人單位', job_title: sub.job_title || '',
        });
      }
      if (request.method === 'POST') {
        if (!token) return json(request, { ok: false, error: '缺少連結' }, 400);
        const b = await request.json().catch(() => ({}));
        if (!b.file_b64) return json(request, { ok: false, error: '請選擇要上傳的檔案' }, 400);
        const sub = await env.DB.prepare(
          `SELECT id, status FROM candidate_form_submissions WHERE token = ?`
        ).bind(token).first();
        if (!sub) return json(request, { ok: false, error: '這個連結不存在，或已經失效' }, 404);
        // 送出即失效（一次性連結，Jacky 確認）——已經填過的再點同一個連結，
        // 只會看到「已經完成」，不會讓他又送一次覆蓋掉前一份。
        if (sub.status === 'submitted') return json(request, { ok: false, error: '這份表單已經填寫完成了' }, 400);
        const now = nowTaipei();
        const saved = await saveUpload(env, { b64: b.file_b64, name: b.file_name, mime: b.file_mime }, now);
        if (saved && saved.tooBig) return json(request, { ok: false, error: '檔案太大，請壓縮後再上傳' }, 400);
        if (!saved) return json(request, { ok: false, error: '上傳失敗，請重試' }, 500);
        await env.DB.prepare(
          `UPDATE candidate_form_submissions SET status='submitted', submitted_at=?, submitted_file_id=? WHERE id=?`
        ).bind(now, saved.fileId, sub.id).run();
        return json(request, { ok: true });
      }
      return json(request, { ok: false, error: 'not found' }, 404);
    }

    if (p.startsWith('/chat/')) {
      const seg = p.split('/').filter(Boolean); // ['chat', token, action?]
      const token = seg[1] || '';
      const action = seg[2] || '';
      if (token.length < 32) return json(request, { ok: false, error: 'bad token' }, 400);

      const app = await env.DB.prepare(
        `SELECT a.id, a.name, a.job_slug, a.job_title, a.interview_state, a.status,
                a.interview_started_at, a.interview_ended_at, a.prewarmed_opening,
                j.title AS job_full_title, j.seniority
           FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug
          WHERE a.chat_token = ?`
      ).bind(token).first();
      if (!app) return json(request, { ok: false, error: 'not found' }, 404);


      // ── 語音回覆（外語口說驗證）──
      //
      // 為什麼要做：文字面談擋不住 Google 翻譯。2026-08-11 Jacky 問「怎麼防止」，
      // 結論是——文字層只能讓作弊留下痕跡，真正解法是語音：
      // 一段 60 秒的錄音同時解決三件事，翻譯貼上不可能、口說能力直接聽到、即時性看得出來。
      // 而客戶要的正是這個（日方會議即時溝通、跟海外據點開會），
      // JLPT N1 證書證明不了，一段語音可以。
      //
      // ⚠️ 只在外語驗證那一題用。中高階最忌諱冗長流程，五題都要錄會把人逼走。
      if (action === 'voice' && request.method === 'POST') {
        // ⚠️ 2026-08-19 改：面談結束後仍然放行「補驗外語」這一種情況。
        // 徐振倫那場因為系統故障沒驗到日文（那是這個缺唯一的硬門檻），
        // 面談一結束連結就等於失效，補驗要重開整場面談——沒有人會這樣做，
        // 所以就永遠沒補。開這個例外的條件很窄：這個職缺確實要驗外語、
        // 而且從來沒驗成功過。驗過一次就關回去，不讓人無限重錄。
        if (app.interview_state === 'done') {
          const jr = await env.DB.prepare(
            `SELECT j.interview_language, a.lang_verified_at FROM applications a
               LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id = ?`
          ).bind(app.id).first();
          const needMakeup = jr && (jr.interview_language || '').trim() && !jr.lang_verified_at;
          if (!needMakeup) {
            return json(request, { ok: false, error: '這場面談已經結束了' }, 409);
          }
        }
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const b64 = String(b.audio_b64 || '');
        if (!b64) return json(request, { ok: false, error: '沒有收到錄音' }, 400);
        // 60 秒的 webm/opus 大約 100–500KB。抓 8MB 當上限，超過多半是壞掉的錄音
        if (b64.length > 8 * 1024 * 1024 * 4 / 3) {
          return json(request, { ok: false, error: '錄音太長，請控制在 60 秒內' }, 400);
        }
        if (!env.AI) return json(request, { ok: false, error: '語音轉寫服務尚未啟用' }, 503);

        const now = nowTaipei();
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

        let text = '';
        try {
          const out = await env.AI.run('@cf/openai/whisper', { audio: [...bin] });
          text = String((out && (out.text || out.transcription)) || '').trim();
        } catch (e) {
          return json(request, { ok: false, error: '轉寫失敗：' + String(e).slice(0, 120) }, 500);
        }
        if (!text) return json(request, { ok: false, error: '這段錄音聽不出內容，請再錄一次' }, 422);

        // 音檔留存。顧問要能自己聽——轉寫再準，「聽起來像不像會開會的人」
        // 只有耳朵判斷得出來，而那是客戶真正在意的。
        const fid = uid();
        await env.DB.prepare(
          `INSERT INTO files (id, created_at, filename, mime, size, content_b64,
                              text_content, parsed_at, parse_note)
           VALUES (?,?,?,?,?,?,?,?,?)`
        ).bind(fid, now, `語音回覆_${app.id.slice(0, 8)}_${Date.now()}.webm`,
               b.mime || 'audio/webm', bin.length, b64, text, now,
               '面談語音回覆（Whisper 轉寫）').run();

        const secs = Math.max(1, Math.round(Number(b.seconds) || 0));
        // ⚠️ 一定要標成語音。阿財看到的是文字，如果不標，
        //    它會把「口說回答」當成「打字回答」來判斷流暢度，那是兩回事。
        const marked = `［語音回覆 ${secs} 秒・系統轉寫］${text}`;
        await env.DB.prepare(
          `INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)`
        ).bind(app.id, 'candidate', marked, now).run();
        // ⚠️ 2026-08-19 加：記下「這場的外語驗證真的做到了」。
        // 徐振倫那場（主管特助・日文是唯一硬門檻）因為系統故障跳過驗證，
        // 之後就再也沒補——報告只在追問事項裡寫了一句，人也就這樣送出去了。
        // 沒有這個時間戳，系統無法分辨「驗過了」與「本來就沒驗」，
        // 收尾時也就不可能提醒。
        await env.DB.prepare(
          `UPDATE applications SET lang_verified_at = datetime('now','+8 hours') WHERE id = ?`
        ).bind(app.id).run().catch(() => {});

        if (app.interview_state !== 'active') {
          await env.DB.prepare(
            `UPDATE applications SET interview_state='active' WHERE id = ?`).bind(app.id).run();
        }
        return json(request, { ok: true, text, file_id: fid, seconds: secs });
      }

      // 送出一句話
      if (action === 'send' && request.method === 'POST') {
        if (app.interview_state === 'done') {
          return json(request, { ok: false, error: '這場面談已經結束了' }, 409);
        }
        // ⚠️ 測驗是必填，而且必須在這裡擋。
        // 面談室的入口只驗 chat_token，任何拿到連結的人都能直接開始打字——
        // 只在前端擋等於沒擋（舊信裡的連結、重新整理、直接貼網址都繞得過）。
        // 2026-08-07 加：沒做測驗就不讓他開口，並把他導回測驗頁。
        // 🚨 中高階不擋測驗。
        //    2026-08-11 實測時撞到：規範已經寫「中高階不要要求做工作風格測驗」，
        //    但擋人的是這道閘門、不是阿財——中高階人選一開口就被踢去做 48 題。
        //    一位九年資歷、帶過團隊的人被要求先做性向測驗才能講話，
        //    那是把他當新鮮人，而中高階人選遇到這種流程就是直接離開。
        //    要不要測驗由顧問自己判斷再另外發。
        const needAssessment = (app.seniority || 'mid') !== 'senior';
        const done = needAssessment ? await env.DB.prepare(
          `SELECT id FROM assessments WHERE application_id = ? LIMIT 1`
        ).bind(app.id).first() : true;
        if (!done) {
          return json(request, {
            ok: false, error: 'assessment_required',
            message: '開始面談前需要先完成工作風格測驗（約 5–7 分鐘）',
            assessment_url: `/assessment/?t=${token}`,
          }, 428);
        }
        // ⚠️ 排隊也要在這裡擋，理由跟測驗一樣：面談室入口只驗 token，
        // 被分流去預約的人只要把網址存起來，隨時可以直接走進來，分流就形同虛設。
        //
        // 但這道閘門是「容量」不是「權限」——所以每次都重新看一次現況：
        // 如果現在剛好空出來了，就讓他進來（並把狀態轉正），不要因為他兩小時前
        // 被排到隊伍後面，就算現在沒人也硬要他等。
        if (app.interview_state !== 'active' &&
            (app.status === 'awaiting_booking' || app.status === 'scheduled')) {
          const load = await liveLoad(env);
          if (load.active + load.pending >= LIVE_LIMIT) {
            return json(request, {
              ok: false, error: 'queue_full',
              message: `目前有 ${load.active + load.pending} 位候選人正在面談中，請先預約一個時段`,
              book_url: `/book/?t=${token}`,
            }, 429);
          }
          await env.DB.prepare(`UPDATE applications SET status='ready' WHERE id=?`)
            .bind(app.id).run();
        }
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const text = String(b.text || '').trim().slice(0, 4000);
        if (!text) return json(request, { ok: false, error: '訊息是空的' }, 400);

        const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)`
        ).bind(app.id, 'candidate', text, now).run();

        if (app.interview_state === 'paused') {
          // 中途離開又回來的人。房間時鐘要歸零——沿用舊的 interview_started_at
          // 會讓他一進來就被「房間滿一小時」規則強制收尾，等於白回來一趟。
          await env.DB.prepare(
            `UPDATE applications SET interview_state='active', interview_started_at=?,
                    interview_ended_at=NULL WHERE id=?`
          ).bind(now, app.id).run();
        } else if (app.interview_state !== 'active') {
          await env.DB.prepare(
            `UPDATE applications SET interview_state='active', interview_started_at=COALESCE(interview_started_at,?)
              WHERE id=?`
          ).bind(now, app.id).run();
        }
        return json(request, { ok: true });
      }

      // 投入度回報（2026-08-06 加）。
      //
      // 存在的理由：顧問說「就像打電話過去，但他在另一端不知道在幹嘛」。
      // 逐字稿看得到「答得認不認真」（字數），看不到「人在不在」。
      // 這支收前端算好的累計值，upsert 覆蓋——**送累計不送增量**，
      // 重送或斷線重連都不會重複累加。
      //
      // ⚠️ 這是行為訊號不是評分，絕不自動刷人。有人開另一個視窗查資料再回答，
      // 那是認真不是分心。給顧問看，讓人判斷。
      if (action === 'focus' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const n = (v, max) => Math.max(0, Math.min(Number(v) || 0, max));
        await env.DB.prepare(
          `INSERT INTO engagement (application_id, away_count, away_seconds, longest_away,
                                   paste_count, paste_chars, active_seconds, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(application_id) DO UPDATE SET
             away_count=excluded.away_count, away_seconds=excluded.away_seconds,
             longest_away=excluded.longest_away, paste_count=excluded.paste_count,
             paste_chars=excluded.paste_chars, active_seconds=excluded.active_seconds,
             updated_at=excluded.updated_at`
        ).bind(app.id, n(b.away_count, 9999), n(b.away_seconds, 86400), n(b.longest_away, 86400),
               n(b.paste_count, 9999), n(b.paste_chars, 999999), n(b.active_seconds, 86400),
               nowTaipei()).run();
        return json(request, { ok: true });
      }

      // 候選人回報問題。
      //
      // 存在的理由：面談中出狀況時，候選人現在只有兩個選擇——乾等或關掉視窗。
      // 兩個都是流失。給一個按鈕讓他講，顧問當下就收到，還救得回來。
      if (action === 'report' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const reason = String(b.reason || '').trim().slice(0, 60);
        const detail = String(b.detail || '').trim().slice(0, 1000);
        if (!reason) return json(request, { ok: false, error: '缺少回報原因' }, 400);

        const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO chat_reports (application_id, created_at, reason, detail) VALUES (?,?,?,?)`
        ).bind(app.id, now, reason, detail || null).run();

        // 這個一定要即時推——候選人正在等，晚十分鐘就沒意義了
        await notify(
          env,
          `🆘 面談中回報問題\n${app.name}（${app.job_full_title || app.job_title || app.job_slug}）\n` +
            `原因：${reason}\n` + (detail ? `說明：${detail}\n` : '') +
            `對話與逐字稿：https://step1ne.com/consultant/reports/`
        ,
        { message_thread_id: THREAD.decide });
        return json(request, { ok: true });
      }

      // 面談結束後的體驗評分。
      //
      // 存在的理由：AI 面談的體感好不好，只有候選人知道。
      // 沒有這個數字，我們就只能憑「顧問覺得報告不錯」來判斷這套值不值得繼續，
      // 而那跟候選人願不願意做完是兩件事。
      // 一題一點就好——剛談完 20 分鐘的人不會填問卷。
      if (action === 'feedback' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const rating = Number(b.rating);
        if (!(rating >= 1 && rating <= 5)) return json(request, { ok: false, error: '評分要在 1 到 5' }, 400);
        const comment = String(b.comment || '').trim().slice(0, 1000);

        await env.DB.prepare(
          `INSERT INTO interview_feedback (application_id, created_at, rating, comment)
           VALUES (?,?,?,?)
           ON CONFLICT(application_id) DO UPDATE SET
             rating = excluded.rating, comment = excluded.comment, created_at = excluded.created_at`
        ).bind(app.id, nowTaipei(), rating, comment || null).run();

        // 低分要當下知道，高分不用吵人
        if (rating <= 2) {
          await notify(env, `⚠️ 面談體驗評分偏低：${rating}/5\n${app.name}（${app.job_full_title || app.job_title || app.job_slug}）\n` +
            (comment ? `他寫：${comment}\n` : '') + `逐字稿：https://step1ne.com/consultant/reports/`,
        { message_thread_id: THREAD.decide });
        }
        return json(request, { ok: true });
      }

      // 輪詢新訊息。after 是前端已經拿到的最後一個 id。
      if (action === 'poll') {
        const after = Number(url.searchParams.get('after') || 0) || 0;
        const { results } = await env.DB.prepare(
          `SELECT id, role, content, created_at FROM messages
            WHERE application_id = ? AND id > ? ORDER BY id ASC LIMIT 50`
        ).bind(app.id, after).all();
        return json(request, {
          ok: true, messages: results || [], state: app.interview_state,
          // 候選人送完話之後在等阿財，前端據此顯示「正在輸入」
          waiting: app.interview_state === 'active',
        });
      }

      // 進入面談室：拿基本資料與全部歷史
      if (!action) {
        const { results } = await env.DB.prepare(
          `SELECT id, role, content, created_at FROM messages
            WHERE application_id = ? ORDER BY id ASC LIMIT 200`
        ).bind(app.id).all();
        let messages = results || [];

        // 2026-08-14 加：開場白預熱。daemon 趁候選人核准後、還沒點進來前的
        // 空檔，已經先跑過一次 claude 把開場白生成好存進 prewarmed_opening；
        // 這裡候選人第一次進房間、房間還是空的，就直接把預熱好的內容寫進去，
        // 秒收到第一句，不用等前端送假訊息、daemon 下一輪輪詢才現場生成。
        // 前端完全不用改——它本來就是看訊息陣列有沒有內容，才決定要不要送
        // 那則「已進入面談室」的假訊息去觸發現場生成。
        //
        // ⚠️ status==='ready' 一定要重查：核准當下有空位不代表現在還有——
        // 如果名額被別人搶滿、狀態已經變成 awaiting_booking，就不能直接放行，
        // 那樣等於繞過容量限制（LIVE_LIMIT）。
        if (!messages.length && app.prewarmed_opening && app.status === 'ready') {
          try {
            const bubbles = JSON.parse(app.prewarmed_opening);
            const now = nowTaipei();
            await env.DB.prepare(
              `INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)`
            ).bind(app.id, 'candidate', '（候選人已進入面談室）', now).run();
            for (const b of bubbles) {
              await env.DB.prepare(
                `INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)`
              ).bind(app.id, 'assistant', b, now).run();
            }
            await env.DB.prepare(
              `UPDATE applications SET interview_state='active', prewarmed_opening=NULL,
                      interview_started_at=COALESCE(interview_started_at,?) WHERE id=?`
            ).bind(now, app.id).run();
            const r2 = await env.DB.prepare(
              `SELECT id, role, content, created_at FROM messages
                WHERE application_id = ? ORDER BY id ASC LIMIT 200`
            ).bind(app.id).all();
            messages = r2.results || [];
          } catch { /* 預熱資料壞掉就照舊走現場生成，不擋候選人 */ }
        }

        return json(request, {
          ok: true,
          name: app.name,
          job_title: app.job_full_title || app.job_title || app.job_slug,
          state: app.interview_state,
          messages,
        });
      }

      return json(request, { ok: false, error: 'unknown action' }, 404);
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
        const row = await env.DB.prepare(
          `SELECT f.filename, f.mime, f.content_b64, f.chunks, f.id AS fid FROM applications a
             JOIN files f ON f.id = a.resume_file_id WHERE a.id = ?`
        ).bind(m[1]).first();
        if (!row) return json(request, { ok: false, error: '沒有這份履歷' }, 404);
        // 舊資料在 content_b64，新資料切塊在 file_chunks，兩種都要能取
        let b64 = row.content_b64;
        if (!b64 && row.chunks) {
          const { results } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id = ? ORDER BY idx ASC`
          ).bind(row.fid).all();
          b64 = (results || []).map((r) => r.b64).join('');
        }
        if (!b64) return json(request, { ok: false, error: '這份履歷沒有檔案內容' }, 404);
        return new Response(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), {
          headers: {
            'content-type': row.mime || 'application/octet-stream',
            'content-disposition': `attachment; filename="${encodeURIComponent(row.filename || 'resume')}"`,
          },
        });
      }
    }

    // ── 顧問後台 ──
    if (p.startsWith('/admin/')) {
      const auth = request.headers.get('authorization') || '';
      if (!env.ADMIN_TOKEN || !safeEqual(auth, `Bearer ${env.ADMIN_TOKEN}`)) {
        return json(request, { ok: false, error: 'unauthorized' }, 401);
      }

      // 用人需求表 One-Page 入口：顧問建立公司入口／重發連結。
      // 這裡只負責產生 token 並回傳完整網址——顧問自己複製去寄信，
      // 這次刻意不做自動寄信（對外寄信是不可逆動作，不交給自動化）。
      // 客戶資訊清單：顧問後台「客戶資訊」分頁用，每家公司＋掛在底下的職缺數。
      if (p === '/admin/portal/companies' && request.method === 'GET') {
        // 2026-08-26 加 ?for_job=<slug>：顧問要把人選推給客戶時，先把「有開這個
        // 職缺的客戶」標出來方便快選，但清單仍然回傳全部——同一位人選可能適合
        // 別家客戶的類似職缺，不該因為職缺沒對上就選不到。
        const forJob = (url.searchParams.get('for_job') || '').trim();
        // 2026-09-02 加：hidden_from_consultants——Jacky 要求把特定客戶（目前是
        // 台灣美光、帆宣系統科技）先從顧問看得到的地方藏起來，這支端點是唯一
        // 來源（客戶清單、推薦給客戶下拉、報告頁客戶選單全部共用這一支），
        // 只要這裡濾掉，三個地方會一起隱藏，不用各自處理。之後要恢復能見度，
        // 把 client_companies.hidden_from_consultants 改回 0 即可。
        const { results } = await env.DB.prepare(
          `SELECT c.id, c.display_name, c.contact_email, c.contact_name, c.portal_token, c.created_at, c.last_emailed_at,
                  c.relation, c.relation_note, c.via_client, c.aliases,
                  (SELECT COUNT(*) FROM jobs WHERE company_id = c.id) AS job_count,
                  (SELECT COUNT(*) FROM jobs WHERE company_id = c.id AND status = 'pending_review') AS pending_count
             FROM client_companies c
            WHERE COALESCE(c.hidden_from_consultants, 0) = 0
            ORDER BY c.display_name`
        ).all();
        let companies = results || [];
        if (forJob) {
          // 這個職缺的標題是什麼，就找「也有開同名職缺」的其他客戶
          const thisJob = await env.DB.prepare(
            `SELECT title, company_id FROM jobs WHERE slug = ?`).bind(forJob).first();
          const relevant = new Set();
          if (thisJob) {
            if (thisJob.company_id) relevant.add(thisJob.company_id);
            const { results: same } = await env.DB.prepare(
              `SELECT DISTINCT company_id FROM jobs
                WHERE trim(title) = ? AND company_id IS NOT NULL
                  AND COALESCE(status,'open') NOT IN ('closed')`
            ).bind(String(thisJob.title || '').trim()).all();
            for (const r of same || []) relevant.add(r.company_id);
          }
          companies = companies.map((c) => ({ ...c, relevant: relevant.has(c.id) }));
        }
        return json(request, { ok: true, companies });
      }

      // 單一公司詳情：公司資訊＋底下所有職缺（含未掛公司的可選清單，方便顧問手動掛新職缺）。
      if (p.startsWith('/admin/portal/companies/') && request.method === 'GET') {
        const id = p.slice('/admin/portal/companies/'.length);
        const company = await env.DB.prepare(
          `SELECT id, display_name, contact_email, contact_name, portal_token, created_at, last_emailed_at,
                  relation, relation_note, via_client, aliases
             FROM client_companies WHERE id = ?`
        ).bind(id).first();
        if (!company) return json(request, { ok: false, error: '找不到這家公司' }, 404);
        // 2026-08-25 改：以前只撈 slug/title/status/updated_at，顧問在「客戶資訊」
        // 分頁完全看不到客戶填了什麼，核准/拒絕等於盲按。現在把 51 個欄位一起帶回去，
        // 給預覽頁用（不是把整包丟畫面上，是讓前端能組出好讀的預覽卡）。
        const cols = ['slug', 'title', 'status', 'updated_at', ...PORTAL_FIELDS.filter((f) => f !== 'title')].join(', ');
        const { results: jobs } = await env.DB.prepare(
          `SELECT ${cols} FROM jobs WHERE company_id = ? ORDER BY slug`
        ).bind(id).all();
        // 客戶自己填的內容從沒經過禁刊過濾——待審核的職缺這裡即時掃一次，
        // 顧問點開待審核清單就看得到「這份要改什麼才能刊」，不是盲目按核准。
        const jobsWithFilter = (jobs || []).map((j) => {
          if (j.status !== 'pending_review') return j;
          const text = PORTAL_FIELDS.map((f) => j[f]).filter(Boolean).join('\n');
          const hits = scanPublishFilters(text, j.client_name || company.display_name);
          return { ...j, compliance_hits: hits };
        });
        return json(request, { ok: true, company, jobs: jobsWithFilter });
      }

      if (p === '/admin/portal/companies' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const displayName = String(b.displayName || '').trim();
        if (!displayName) return json(request, { ok: false, error: '缺少公司名稱' }, 400);
        const id = String(b.id || '').trim() || `co_${uid().slice(0, 8)}`;
        const portalToken = [...crypto.getRandomValues(new Uint8Array(24))]
          .map((x) => x.toString(16).padStart(2, '0')).join('');
        const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO client_companies (id, display_name, contact_email, portal_token, created_at, updated_at)
           VALUES (?,?,?,?,?,?)`
        ).bind(id, displayName, b.contactEmail || null, portalToken, now, now).run();
        return json(request, { ok: true, id, portalToken, portalUrl: `https://step1ne.com/portal/?t=${portalToken}` });
      }

      // 重新產生 token（收回舊連結的存取權）。
      if (p === '/admin/portal/companies/regen-token' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const id = String(b.id || '').trim();
        if (!id) return json(request, { ok: false, error: '缺少公司編號' }, 400);
        const portalToken = [...crypto.getRandomValues(new Uint8Array(24))]
          .map((x) => x.toString(16).padStart(2, '0')).join('');
        const now = nowTaipei();
        const r = await env.DB.prepare(
          `UPDATE client_companies SET portal_token = ?, updated_at = ? WHERE id = ?`
        ).bind(portalToken, now, id).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這家公司' }, 404);
        return json(request, { ok: true, id, portalToken, portalUrl: `https://step1ne.com/portal/?t=${portalToken}` });
      }

      // 編輯公司的聯絡信箱／稱呼。
      if (p === '/admin/portal/companies/update' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const id = String(b.id || '').trim();
        if (!id) return json(request, { ok: false, error: '缺少公司編號' }, 400);
        const now = nowTaipei();
        // 2026-08-26 加 relation／別名／原因：這幾欄原本只有「客戶名單」那一頁
        // 能改，但那一頁跟這一頁讀寫的是同一張 client_companies——同一家公司
        // 分兩個地方維護，改了一邊另一邊不知道。合併到這一頁之後，那一頁
        // 只剩導向，不再各自維護一份。
        const REL = ['signed', 'negotiating', 'end_client', 'past', 'prospect', 'blocked', 'private'];
        const rel = REL.includes(String(b.relation || '')) ? String(b.relation) : null;
        const r = await env.DB.prepare(
          `UPDATE client_companies SET contact_email = ?, contact_name = ?,
                  relation = COALESCE(?, relation),
                  relation_note = CASE WHEN ? IS NULL THEN relation_note ELSE ? END,
                  via_client = CASE WHEN ? IS NULL THEN via_client ELSE ? END,
                  aliases = CASE WHEN ? IS NULL THEN aliases ELSE ? END,
                  updated_at = ? WHERE id = ?`
        ).bind(b.contactEmail || null, b.contactName || null, rel,
               b.relationNote === undefined ? null : (b.relationNote || ''), b.relationNote || null,
               b.viaClient === undefined ? null : (b.viaClient || ''), b.viaClient || null,
               b.aliases === undefined ? null : (b.aliases || ''), b.aliases || null,
               now, id).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這家公司' }, 404);
        return json(request, { ok: true, id });
      }

      // 一鍵寄送補件連結給企業客戶窗口。
      if (p === '/admin/portal/companies/send-email' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const id = String(b.id || '').trim();
        if (!id) return json(request, { ok: false, error: '缺少公司編號' }, 400);
        const company = await env.DB.prepare(
          `SELECT id, display_name, contact_email, contact_name, portal_token FROM client_companies WHERE id = ?`
        ).bind(id).first();
        if (!company) return json(request, { ok: false, error: '找不到這家公司' }, 404);
        if (!company.contact_email) return json(request, { ok: false, error: '這家公司還沒填聯絡信箱' }, 400);
        const portalUrl = `https://step1ne.com/portal/?t=${company.portal_token}`;
        const sent = await sendPortalMail(env, company.contact_email, company.contact_name, company.display_name, portalUrl);
        if (!sent) return json(request, { ok: false, error: '寄送失敗，可能是 RESEND_API_KEY 沒設或信箱格式問題' }, 500);
        const now = nowTaipei();
        await env.DB.prepare(`UPDATE client_companies SET last_emailed_at = ? WHERE id = ?`).bind(now, id).run();
        return json(request, { ok: true, sentAt: now });
      }

      // 企業客戶送出「等審核」的新職缺清單，顧問核准／拒絕。
      if (p === '/admin/portal/pending-jobs/approve' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.slug || '').trim();
        if (!slug) return json(request, { ok: false, error: '缺少 slug' }, 400);
        // 核准後回到 draft——這只是「這家公司真的要開這個缺」被確認了，
        // 不代表可以直接對外刊登：正式上架仍要走既有的職缺審核流程
        // （禁刊過濾器、內容改寫），不在這裡跳過。
        const r = await env.DB.prepare(
          `UPDATE jobs SET status = 'draft', updated_at = ? WHERE slug = ? AND status = 'pending_review'`
        ).bind(nowTaipei(), slug).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這筆待審核職缺' }, 404);
        return json(request, { ok: true, slug });
      }
      // 2026-08-25 改：不再直接 DELETE。現在客戶送審前已經把 51 個欄位填完
      // （不只是標題），拒絕就整筆刪掉等於逼客戶重打一次——退回 client_draft
      // 讓客戶自己回去改、改完可以再送一次審核。
      if (p === '/admin/portal/pending-jobs/reject' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.slug || '').trim();
        if (!slug) return json(request, { ok: false, error: '缺少 slug' }, 400);
        const r = await env.DB.prepare(
          `UPDATE jobs SET status = 'client_draft', updated_at = ? WHERE slug = ? AND status = 'pending_review'`
        ).bind(nowTaipei(), slug).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這筆待審核職缺' }, 404);
        return json(request, { ok: true, slug });
      }

      // 把某個既有職缺掛上公司入口（company_id），這樣它才會出現在對應的 portal 裡。
      if (p === '/admin/portal/link-job' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.slug || '').trim();
        const companyId = String(b.companyId || '').trim();
        if (!slug || !companyId) return json(request, { ok: false, error: '缺少 slug 或 companyId' }, 400);
        const r = await env.DB.prepare(`UPDATE jobs SET company_id = ? WHERE slug = ?`).bind(companyId, slug).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        return json(request, { ok: true, slug, companyId });
      }

      // LINE「查詢面試進度」的配對紀錄清單。Jacky 明確要求：這件事顧問要看得到，
      // 不能只是候選人自己在 LINE 上查完就沒有任何紀錄留在後台。
      if (p === '/admin/line-bindings' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT line_user_id, state, phone, email, application_ids, created_at, bound_at, updated_at
             FROM line_bindings ORDER BY updated_at DESC LIMIT 500`
        ).all();
        const bindingIds = (results || []).map((r) => safeJsonArray(r.application_ids));
        // ⚠️ 2026-09-01 修：原本對「每一筆」binding 各自查一次 applications，
        // 500 筆 binding 就是 500 次查詢——跟 tracker／portal candidates 同一種
        // N+1，只是規模較小、當時稽核漏掉。改成先把所有 binding 用到的
        // application_id 收集成一個去重集合，一次（分批）撈齊，再用 Map 分回
        // 各自的 binding，查詢次數從「O(binding數)」降到固定幾次。
        const allIds = Array.from(new Set(bindingIds.flat()));
        const appByIdChunk = (arr, size) => { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; };
        const appRowsAll = [];
        for (const ids2 of appByIdChunk(allIds, 100)) {
          if (!ids2.length) continue;
          const { results: appRows } = await env.DB.prepare(
            `SELECT id, name, job_slug, job_title FROM applications WHERE id IN (${ids2.map(() => '?').join(',')})`
          ).bind(...ids2).all();
          appRowsAll.push(...(appRows || []));
        }
        const appById = new Map(appRowsAll.map((a) => [a.id, a]));
        const rows = (results || []).map((r, i) => {
          const apps = bindingIds[i].map((id) => appById.get(id)).filter(Boolean);
          return {
            line_user_id: r.line_user_id,
            state: r.state,
            phone: r.phone,
            email: r.email,
            candidate_name: apps[0] ? apps[0].name : null,
            bound_at: r.bound_at,
            created_at: r.created_at,
            updated_at: r.updated_at,
            applications: apps.map((a) => ({ id: a.id, job_slug: a.job_slug, job_title: a.job_title })),
          };
        });
        return json(request, { ok: true, bindings: rows });
      }

      // ── 顧問自助新增職缺：收件 ──
      //
      // 在這之前，新增職缺只能由顧問把 JD 丟給 Claude、由 Claude 手動跑
      // publish_job.py。顧問自己在手機上沒有入口，而且他傳過來的原始檔
      // （PDF／截圖／LINE 貼的文字）沒有任何地方留存。
      //
      // ⚠️ 這支只是「收下來排隊」。它**不會**擬 JD，也**不會**發布任何東西——
      //    Worker 跑不了本機的 claude 與 publish_job.py。擬 JD 由本機的
      //    jobintake/draft_job.py 做，發布由 jobintake/publish_approved.py 做，
      //    而後者只處理顧問在 Telegram 按過「核准發布」的收件單。
      if (p === '/admin/job-intake' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const rel = RELATION[b.client_relation];
        if (!rel) return json(request, { ok: false, error: '請選擇客戶對象（已簽約／未簽約／朋友私人協助）' }, 400);
        if (!SERVICE_LINE[b.service_line]) {
          return json(request, { ok: false, error: '請選擇服務線（派遣／正職代招／中高階）' }, 400);
        }
        const files = Array.isArray(b.files) ? b.files : [];
        // 四種來源至少要有一種，不然總指揮沒有東西可以擬
        if (!String(b.raw_text || '').trim() && !String(b.source_url || '').trim() && !files.length) {
          return json(request, { ok: false, error: '請至少給一種資料：文字敘述、連結，或上傳 PDF／圖片' }, 400);
        }

        const id = uid();
        const now = nowTaipei();

        // 重送防呆：同一個人、同樣的內容、15 分鐘內——當成同一張單子。
        //
        // 為什麼需要：這支是「寫完 DB 才回應」，網路一斷顧問就會看到失敗訊息，
        // 但資料其實已經進去了（2026-08-11 PH 遇到的就是這個）。他理所當然會再按一次，
        // 於是總指揮會擬兩份 JD、群組跳兩次審核按鈕。與其教顧問先去查，
        // 不如讓重送這件事本身沒有後果。
        const dupKey = String(b.raw_text || '') + ' ' + String(b.source_url || '');
        if (dupKey.replace(/ /g, '').trim()) {
          const dup = await env.DB.prepare(
            `SELECT id FROM job_intakes
              WHERE ifnull(submitted_by,'') = ifnull(?,'')
                AND ifnull(raw_text,'')     = ifnull(?,'')
                AND ifnull(source_url,'')   = ifnull(?,'')
                AND created_at >= datetime(?, '-15 minutes')
              ORDER BY created_at DESC LIMIT 1`
          ).bind(b.submitted_by || null, b.raw_text || null, b.source_url || null, now).first();
          if (dup) {
            return json(request, { ok: true, intake_id: dup.id, files: 0, deduped: true });
          }
        }

        // 原始檔先存——存不進去就不要建收件單，免得留下一張沒有底稿的單子
        const savedFiles = [];
        for (const f of files) {
          const s = await saveUpload(env, f, now);
          if (s && s.tooBig) {
            return json(request, { ok: false,
              error: `「${s.name}」超過 8MB。改貼雲端連結（Google Drive／Dropbox）就沒有這個限制。` }, 400);
          }
          if (s) savedFiles.push({ ...s, kind: f.kind || 'other' });
        }

        // client_named／ai_disclosure／brand_mode 由客戶對象自動決定，
        // 不接受前端傳值——那三欄設錯的後果是客戶隱私外洩或揭露義務沒履行。
        await env.DB.prepare(
          `INSERT INTO job_intakes
             (id, created_at, submitted_by, service_line, client_relation,
              client_named, ai_disclosure, brand_mode, client_code, client_name,
              raw_text, source_url, note, status, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?)`
        ).bind(
          id, now, b.submitted_by || null, b.service_line, b.client_relation,
          rel.client_named, rel.ai_disclosure, rel.brand_mode,
          b.client_code || null, b.client_name || null,
          b.raw_text || null, b.source_url || null, b.note || null, now
        ).run();

        for (const s of savedFiles) {
          await env.DB.prepare(
            `INSERT INTO job_intake_files (intake_id, file_id, kind, created_at) VALUES (?,?,?,?)`
          ).bind(id, s.fileId, s.kind, now).run();
        }

        await notify(env,
          `📥 收到新職缺資料\n` +
          `服務線：${SERVICE_LINE[b.service_line]}　客戶對象：${rel.label}\n` +
          `送件人：${b.submitted_by || '未填'}\n` +
          `附件：${savedFiles.length} 個　收件單：${id}\n` +
          `接下來由 step1ne 總指揮擬 JD，擬完會回到這裡讓你決定要不要發布。`,
        { message_thread_id: THREAD.decide });

        return json(request, { ok: true, intake_id: id, files: savedFiles.length });
      }

      // ── 履歷健檢：收件清單 ──
      // 只回摘要欄位，不回 resume_text——那份很長，清單頁不需要。
      if (p === '/admin/checkups' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT c.id, c.created_at, c.name, c.email, c.current_title, c.current_industry,
                  c.status, c.fee_status, c.resume_readable, c.parsed_at, c.report_at,
                  c.led_headcount, c.budget_scale, c.crowdfunding_raised,
                  c.chat_token, c.chat_state,
                  (SELECT COUNT(*) FROM checkup_files  f WHERE f.checkup_id = c.id) AS file_count,
                  (SELECT COUNT(*) FROM checkup_links  l WHERE l.checkup_id = c.id) AS link_count,
                  (SELECT COUNT(*) FROM checkup_messages m WHERE m.checkup_id = c.id) AS msg_count
             FROM checkups c ORDER BY c.created_at DESC LIMIT 50`
        ).all();
        return json(request, { ok: true, rows: results || [] });
      }

      // ── 履歷健檢：單筆全貌（附件、連結、對談紀錄）──
      // 阿福要開口之前讀的就是這一支；本機腳本則走 fetch_checkup.py（直接查 D1）。
      if (p.startsWith('/admin/checkup/') && request.method === 'GET') {
        const cid = decodeURIComponent(p.slice('/admin/checkup/'.length));
        const c = await env.DB.prepare(`SELECT * FROM checkups WHERE id = ?`).bind(cid).first();
        if (!c) return json(request, { ok: false, error: '沒有這筆健檢' }, 404);
        const files = await env.DB.prepare(
          `SELECT cf.file_id, cf.kind, f.filename, f.mime, f.size, f.parsed_at, f.parse_note
             FROM checkup_files cf LEFT JOIN files f ON f.id = cf.file_id
            WHERE cf.checkup_id = ? ORDER BY cf.created_at ASC`
        ).bind(cid).all();
        const links = await env.DB.prepare(
          `SELECT idx, url, label, fetched_at, fetch_note FROM checkup_links
            WHERE checkup_id = ? ORDER BY idx ASC`
        ).bind(cid).all();
        const msgs = await env.DB.prepare(
          `SELECT role, content, created_at FROM checkup_messages
            WHERE checkup_id = ? ORDER BY created_at ASC`
        ).bind(cid).all();
        return json(request, {
          ok: true, checkup: c,
          files: files.results || [], links: links.results || [], messages: msgs.results || [],
        });
      }

      // ── 客戶關係名單 ──
      //
      // 🚨 這張表是反向開發的安全帶。在它之前，系統裡唯一的客戶資料是
      //    jobs.client_name（＝已經有職缺頁的客戶），而真正會出事的三種
      //    一筆都不在裡面：洽談中的、客戶的終端客戶、明確禁止接觸的。
      //    2026-08-10 agent 提議去敲台灣美光（律准的終端客戶），
      //    2026-08-11 提議去敲帆宣（顧問正在談簽約）——兩次都是因為查不到。
      // 2026-08-26 改：原本是獨立的 clients 表，跟 portal 客戶帳號
      // （client_companies）是兩本互不相通的名冊，同一家客戶在兩邊各存
      // 一筆、改一邊不會同步到另一邊。Jacky 確認這其實是同一件事（客戶
      // 關係狀態），已經把 9 筆舊資料併進 client_companies，這裡改讀寫
      // 同一張表——不管是這個分頁還是「客戶資訊」分頁，看到的都是同一份。
      if (p === '/admin/clients' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, display_name AS name, aliases, relation, relation_note AS blocked_reason,
                  via_client, owner, note, created_at, updated_at
             FROM client_companies WHERE relation IS NOT NULL ORDER BY
               CASE relation WHEN 'end_client' THEN 0 WHEN 'negotiating' THEN 1
                             WHEN 'signed' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
               display_name`
        ).all();
        return json(request, { ok: true, rows: results || [] });
      }

      if (p === '/admin/clients' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const now = nowTaipei();
        if (b.delete && b.id) {
          // 這裡的「刪除」只清掉關係狀態，不刪整個客戶帳號——這筆公司可能
          // 還有 portal 職缺掛在底下，不能整筆砍掉。
          await env.DB.prepare(
            `UPDATE client_companies SET relation=NULL, relation_note=NULL, via_client=NULL WHERE id = ?`
          ).bind(b.id).run();
          return json(request, { ok: true, deleted: b.id });
        }
        const name = String(b.name || '').trim();
        if (!name) return json(request, { ok: false, error: '請填公司名稱' }, 400);
        const REL = ['signed', 'negotiating', 'end_client', 'past', 'prospect', 'blocked'];
        if (!REL.includes(b.relation)) {
          return json(request, { ok: false, error: '關係請選：已簽約／洽談中／終端客戶／曾合作／已開發過／禁止接觸' }, 400);
        }
        // 終端客戶一定要寫是誰的，不然日後沒人知道為什麼不能碰，就會有人把它刪掉
        if (b.relation === 'end_client' && !String(b.via_client || '').trim()) {
          return json(request, { ok: false, error: '「客戶的終端客戶」請填是透過哪一家接觸到的，否則日後沒人知道為什麼不能碰' }, 400);
        }
        const id = String(b.id || '').trim();
        if (id) {
          // 更新既有客戶帳號的關係狀態
          await env.DB.prepare(
            `UPDATE client_companies SET display_name=?, aliases=?, relation=?, relation_note=?,
                    via_client=?, owner=?, note=?, updated_at=? WHERE id=?`
          ).bind(name, b.aliases || null, b.relation, b.blocked_reason || null,
                 b.via_client || null, b.owner || null, b.note || null, now, id).run();
          return json(request, { ok: true, id });
        }
        // 全新的公司（還沒有 portal 帳號）——一樣生一組 token，即使不會馬上
        // 寄給客戶，維持跟其他客戶帳號同一套資料結構，不要另外開一份格式。
        const newId = `co_${[...crypto.getRandomValues(new Uint8Array(4))].map((x) => x.toString(16).padStart(2, '0')).join('')}`;
        const token = [...crypto.getRandomValues(new Uint8Array(24))].map((x) => x.toString(16).padStart(2, '0')).join('');
        await env.DB.prepare(
          `INSERT INTO client_companies (id, display_name, aliases, relation, relation_note, via_client,
                  owner, note, portal_token, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)`
        ).bind(newId, name, b.aliases || null, b.relation, b.blocked_reason || null,
               b.via_client || null, b.owner || null, b.note || null, token, now, now).run();
        return json(request, { ok: true, id: newId });
      }

      // ── 反向開發的入口：顧問說「我要開發做這種缺的客戶」──
      //
      // ⚠️ 2026-08-11 改過方向。原本是「挑一份履歷 → 找公司」，
      //    但顧問腦子裡的順序是反的：他先想要開發什麼客戶，
      //    才由系統去人才庫撈得上用場的人。入口做錯顧問就不會用。
      if (p === '/admin/bd-request' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const role = String(b.role_family || '').trim();
        if (!role) return json(request, { ok: false, error: '請填你要開發哪一種職缺的客戶' }, 400);
        const id = uid(); const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO bd_requests (id,created_at,submitted_by,role_family,industry,region,
                                    service_line,target_count,note,status,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,'new',?)`
        ).bind(id, now, b.submitted_by || null, role, b.industry || null, b.region || null,
               b.service_line || null, Number(b.target_count) || 6, b.note || null, now).run();
        await notify(env,
          `🎯 收到開發需求\n要開發：${role}\n` +
          (b.industry ? `產業：${b.industry}\n` : '') + (b.region ? `地區：${b.region}\n` : '') +
          `送件人：${b.submitted_by || '未填'}\n` +
          `總指揮會去人才庫撈得上用場的人選，配對後寫開發信送回這裡。`,
          // 開發信一批就好幾則，丟「面試通知確認」會把面試的洗掉（2026-08-11 Jacky 反應過）
          { message_thread_id: THREAD.system });
        return json(request, { ok: true, id });
      }

      if (p === '/admin/bd-requests' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT r.*, (SELECT COUNT(*) FROM bd_outreach o WHERE o.request_id = r.id) AS letters
             FROM bd_requests r ORDER BY r.created_at DESC LIMIT 40`).all();
        return json(request, { ok: true, rows: results || [] });
      }

      // 匿名人才庫：所有「有履歷、而且沒有走到到職」的人。
      // 🚨 只回不可辨識的欄位——這支的用途是給顧問看「庫裡有什麼樣的人」，
      //    姓名與 email 在這裡沒有任何用處，回了只是多一個外洩點。
      if (p === '/admin/talent-pool' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT a.id, a.created_at, a.job_title, a.interview_state, a.expected_salary,
                  a.available_date, a.disc_primary,
                  (SELECT p.stage FROM placements p WHERE p.application_id = a.id
                    ORDER BY p.updated_at DESC LIMIT 1) AS stage,
                  (SELECT COUNT(*) FROM bd_outreach o WHERE o.candidate_ref = a.id) AS used
             FROM applications a
             LEFT JOIN files f ON f.id = a.resume_file_id
            WHERE a.superseded_by IS NULL
              AND f.text_content IS NOT NULL AND length(f.text_content) > 200
            ORDER BY a.created_at DESC`).all();
        // 到職了就不該再拿去開發——那個人已經有工作了
        const rows = (results || []).filter(
          (r) => !['onboard', 'placed', '到職'].includes(String(r.stage || '')));
        return json(request, { ok: true, rows });
      }

      // ── 反向開發：待審的開發信 ──
      if (p === '/admin/bd' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, created_at, batch_id, candidate_ref, company, why_company,
                  contact_name, contact_email, subject, status, sent_at, decided_by, guard_json
             FROM bd_outreach ORDER BY created_at DESC LIMIT 60`
        ).all();
        return json(request, { ok: true, rows: results || [] });
      }

      if (p.startsWith('/admin/bd/') && request.method === 'GET') {
        const bid = decodeURIComponent(p.slice('/admin/bd/'.length));
        const row = await env.DB.prepare(`SELECT * FROM bd_outreach WHERE id = ?`).bind(bid).first();
        if (!row) return json(request, { ok: false, error: '找不到這封' }, 404);
        return json(request, { ok: true, row });
      }

      // 補窗口／改信件內容。
      // 查不到 email 的那幾封在群組裡按核准也寄不出去，顧問需要一個地方補。
      if (p.startsWith('/admin/bd/') && p.endsWith('/patch') && request.method === 'POST') {
        const bid = decodeURIComponent(p.slice('/admin/bd/'.length, -'/patch'.length));
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const row = await env.DB.prepare(`SELECT id, status FROM bd_outreach WHERE id = ?`).bind(bid).first();
        if (!row) return json(request, { ok: false, error: '找不到這封' }, 404);
        if (row.status === 'sent') return json(request, { ok: false, error: '這封已經寄出去了，改不動' }, 400);

        const sets = [], vals = [];
        for (const k of ['contact_email', 'contact_name', 'subject', 'body']) {
          if (b[k] === undefined) continue;
          sets.push(`${k} = ?`);
          vals.push(String(b[k]).trim() || null);
        }
        if (!sets.length) return json(request, { ok: false, error: '沒有要改的欄位' }, 400);
        vals.push(nowTaipei(), bid);
        await env.DB.prepare(
          `UPDATE bd_outreach SET ${sets.join(', ')}, updated_at = ? WHERE id = ?`).bind(...vals).run();
        return json(request, { ok: true });
      }

      // 顧問決定要不要寄。⚠️ 只有這裡會真的寄出去，agent 自己不寄。
      if (p.startsWith('/admin/bd/') && p.endsWith('/decide') && request.method === 'POST') {
        const bid = decodeURIComponent(p.slice('/admin/bd/'.length, -'/decide'.length));
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const row = await env.DB.prepare(`SELECT * FROM bd_outreach WHERE id = ?`).bind(bid).first();
        if (!row) return json(request, { ok: false, error: '找不到這封' }, 404);
        if (row.status === 'sent') return json(request, { ok: false, error: '這封已經寄出去了，不重複寄' }, 400);
        if (row.status === 'blocked') return json(request, { ok: false, error: '這家在客戶名單上，不可以寄' }, 400);

        const who = String(b.decided_by || '顧問').trim();
        const now = nowTaipei();

        if (b.action === 'reject') {
          await env.DB.prepare(
            `UPDATE bd_outreach SET status='rejected', decided_by=?, decided_at=?, reply_note=?, updated_at=? WHERE id=?`
          ).bind(who, now, b.note || null, now, bid).run();
          return json(request, { ok: true, status: 'rejected' });
        }
        if (b.action === 'rewrite') {
          await env.DB.prepare(
            `UPDATE bd_outreach SET status='draft', decided_by=?, decided_at=?, reply_note=?, updated_at=? WHERE id=?`
          ).bind(who, now, String(b.note || '').trim() || null, now, bid).run();
          return json(request, { ok: true, status: 'draft' });
        }
        if (b.action !== 'send') return json(request, { ok: false, error: '未知的動作' }, 400);

        // 寄之前再擋一次客戶名單。名單是會變的——擬稿當下沒事，
        // 顧問三天後才按核准，中間可能已經跟這家簽約了。
        const hit = await guardCompany(env, row.company);
        if (hit) {
          await env.DB.prepare(
            `UPDATE bd_outreach SET status='blocked', guard_json=?, updated_at=? WHERE id=?`
          ).bind(JSON.stringify(hit), now, bid).run();
          await notify(env, `⛔ 這封沒有寄出\n對象：${row.company}\n原因：${hit.why}\n（寄出前又比對了一次客戶名單）`,
            { message_thread_id: THREAD.system });
          return json(request, { ok: false, error: `${row.company} 在客戶名單上：${hit.why}` }, 400);
        }
        if (!row.contact_email) return json(request, { ok: false, error: '這封還沒有收件人 email' }, 400);

        const sent = await sendBdMail(env, row.contact_email, row.subject, row.body, row.cv_file_id);
        if (!sent) return json(request, { ok: false, error: '寄送失敗，信件沒有送出' }, 500);
        await env.DB.prepare(
          `UPDATE bd_outreach SET status='sent', decided_by=?, decided_at=?, sent_at=?, updated_at=? WHERE id=?`
        ).bind(who, now, now, now, bid).run();
        await notify(env, `📤 已寄出開發信\n對象：${row.company}（${row.contact_email}）\n核准人：${who}`,
          { message_thread_id: THREAD.system });
        return json(request, { ok: true, status: 'sent' });
      }

      // ── 擬稿：顧問直接看／直接改 ──
      //
      // 為什麼要這支：原本顧問只有 Telegram 上三顆按鈕，要改一個字也只能按
      // 「重寫」整份重擬——重擬一次要幾分鐘，而且改回來的其他段落又要重看一遍。
      // 大部分情況顧問只是想動一兩個欄位（標題、地點、薪資、某一則 FAQ）。
      //
      // 這裡回的是 draft_json（總指揮擬好的規格），不是網站上的頁面——
      // 改完仍然要按「核准發布」才會上線，而 publish_approved.py 發布前
      // 還會再跑一次禁刊過濾器，所以顧問手改也不會把違法內容推上去。
      if (p.startsWith('/admin/job-draft/') && request.method === 'GET') {
        const iid = decodeURIComponent(p.slice('/admin/job-draft/'.length));
        const row = await env.DB.prepare(
          `SELECT id, created_at, submitted_by, service_line, client_relation,
                  client_named, ai_disclosure, brand_mode, client_name, client_code,
                  status, draft_json, review_text, filter_json, rewrite_note,
                  raw_text, source_url, note, published_slug
             FROM job_intakes WHERE id = ?`
        ).bind(iid).first();
        if (!row) return json(request, { ok: false, error: '找不到這張收件單' }, 404);
        let draft = null;
        try { draft = JSON.parse(row.draft_json || 'null'); } catch { draft = null; }
        let filters = null;
        try { filters = JSON.parse(row.filter_json || 'null'); } catch { filters = null; }
        return json(request, { ok: true, intake: { ...row, draft_json: undefined, filter_json: undefined }, draft, filters });
      }

      // 顧問改擬稿。兩種用法，靠 mode 分：
      //   mode=save  顧問自己把欄位改掉，改完還是 pending，等他按核准發布
      //   mode=ai    顧問只留一句話，交給總指揮重擬（＝ Telegram 那顆「重寫」，
      //              但可以順便帶著已經手改過的欄位一起給它當基礎）
      if (p.startsWith('/admin/job-draft/') && p.endsWith('/patch') && request.method === 'POST') {
        const iid = decodeURIComponent(p.slice('/admin/job-draft/'.length, -'/patch'.length));
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const row = await env.DB.prepare(
          `SELECT id, status, draft_json FROM job_intakes WHERE id = ?`
        ).bind(iid).first();
        if (!row) return json(request, { ok: false, error: '找不到這張收件單' }, 404);
        // 已經發布的不給從這裡改——那要走改版流程，不是改擬稿
        if (['published'].includes(row.status)) {
          return json(request, { ok: false, error: '這個職缺已經發布了，要改請走職缺頁改版，不是改擬稿' }, 400);
        }

        const mode = b.mode === 'ai' ? 'ai' : 'save';
        const note = String(b.note || '').trim();
        if (mode === 'ai' && !note) {
          return json(request, { ok: false, error: '請寫一句話告訴總指揮要改什麼，不然它重擬出來多半還是一樣' }, 400);
        }

        let draft = {};
        try { draft = JSON.parse(row.draft_json || '{}') || {}; } catch { draft = {}; }

        // 只允許動對外欄位。client_named／ai_disclosure／brand_mode／service_line
        // 是「客戶對象」連動出來的，從這裡改等於繞過那套規則，後果是客戶隱私外洩。
        const EDITABLE = new Set([
          'title', 'subtitle', 'page_title', 'description', 'intro',
          'locations', 'locality', 'region', 'employment', 'years_min',
          'salary_min', 'salary_max', 'salary_note', 'keywords', 'tags',
          'must_skills', 'benefits', 'spec', 'duties', 'must', 'plus', 'why', 'faq',
          'industry', 'card_meta', 'card_desc',
        ]);
        const changed = [];
        for (const [k, v] of Object.entries(b.fields || {})) {
          if (!EDITABLE.has(k)) continue;
          if (JSON.stringify(draft[k]) === JSON.stringify(v)) continue;
          draft[k] = v;
          changed.push(k);
        }

        const who = String(b.edited_by || '').trim() || '顧問';
        const nextStatus = mode === 'ai' ? 'rewrite' : 'pending';

        await env.DB.prepare(
          `UPDATE job_intakes
              SET draft_json = ?, status = ?, rewrite_note = ?,
                  updated_at = datetime('now','+8 hours')
            WHERE id = ?`
        ).bind(JSON.stringify(draft), nextStatus, note || null, iid).run();

        await notify(env,
          (mode === 'ai'
            ? `✏️ ${who} 把擬稿退回給總指揮重擬\n`
            : `📝 ${who} 直接改了擬稿\n`) +
          `職缺：${draft.title || iid}\n` +
          (changed.length ? `改動欄位：${changed.join('、')}\n` : '') +
          (note ? `交代：${note}\n` : '') +
          (mode === 'ai'
            ? '總指揮會照著重擬，擬完再送一次審核。'
            : '狀態仍是「待核准」，網站目前沒有任何改動。'),
        { message_thread_id: THREAD.decide });

        return json(request, { ok: true, status: nextStatus, changed, mode });
      }

      // 收件單清單（後台看進度用）。不回傳 draft_json 全文，那份很大。
      if (p === '/admin/job-intakes' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, created_at, submitted_by, service_line, client_relation,
                  client_named, ai_disclosure, brand_mode, status, published_slug,
                  decided_by, decided_at, rewrite_note,
                  substr(COALESCE(raw_text,''), 1, 120) AS raw_preview
             FROM job_intakes ORDER BY created_at DESC LIMIT 50`
        ).all();
        return json(request, { ok: true, rows: results || [] });
      }

      // 刪掉一張收件單。
      // 2026-08-21 Jacky 要求：清單裡會留下沒填完就中斷的空白單（例如
      // 8/19 那張「（無標題）總指揮擬 JD 中」），沒有辦法清掉，越積越多，
      // 顧問每次進來都要先分辨哪些是真的要處理的。
      //
      // ⚠️ 已經發布成職缺的不給刪：那張收件單是那個職缺的來源紀錄，
      //    刪掉之後就查不到「這個職缺當初是誰、依據什麼開的」。
      //    要下架職缺是另一件事（改職缺狀態），不是刪收件單。
      if (p.startsWith('/admin/job-draft/') && p.endsWith('/delete') && request.method === 'POST') {
        const iid = decodeURIComponent(p.slice('/admin/job-draft/'.length, -'/delete'.length));
        const row = await env.DB.prepare(
          `SELECT id, status, published_slug FROM job_intakes WHERE id = ?`).bind(iid).first();
        if (!row) return json(request, { ok: false, error: '找不到這張收件單' }, 404);
        if (row.published_slug) {
          return json(request, { ok: false,
            error: `這張收件單已經發布成職缺（${row.published_slug}），不能刪——刪掉就查不到這個職缺當初是依據什麼開的。要下架請去職缺管理改狀態。` }, 400);
        }
        await env.DB.prepare(`DELETE FROM job_intake_files WHERE intake_id = ?`).bind(iid).run().catch(() => {});
        await env.DB.prepare(`DELETE FROM job_intakes WHERE id = ?`).bind(iid).run();
        return json(request, { ok: true });
      }

      // 顧問後台下載履歷：跟 /export/resume/<id> 邏輯一樣（切塊重組），
      // 差別只在認證方式——這支給瀏覽器點按鈕用，走 ADMIN_TOKEN 不是 SYNC_TOKEN。
      // 沒有這支之前，履歷檔案存了但顧問後台完全叫不出來，等於白存。
      if (p.startsWith('/admin/resume/')) {
        const aid = decodeURIComponent(p.slice('/admin/resume/'.length));
        const row = await env.DB.prepare(
          `SELECT f.filename, f.mime, f.content_b64, f.chunks, f.id AS fid FROM applications a
             JOIN files f ON f.id = a.resume_file_id WHERE a.id = ?`
        ).bind(aid).first();
        if (!row) return json(request, { ok: false, error: '沒有這份履歷' }, 404);
        let b64 = row.content_b64;
        if (!b64 && row.chunks) {
          const { results } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id = ? ORDER BY idx ASC`
          ).bind(row.fid).all();
          b64 = (results || []).map((r) => r.b64).join('');
        }
        if (!b64) return json(request, { ok: false, error: '這份履歷沒有檔案內容' }, 404);
        return new Response(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), {
          headers: {
            'content-type': row.mime || 'application/octet-stream',
            'content-disposition': `attachment; filename="${encodeURIComponent(row.filename || 'resume')}"`,
            ...cors(request),
          },
        });
      }

      // 待審核清單：只列「這個職缺有客戶硬性條件」且顧問還沒處置過的應徵。
      // 一般職缺（jobs.client_screen_conditions 是空的）不會出現在這裡，
      // 因為它們從沒被擋下來過，直接就進阿財面談了。
      if (p === '/admin/screening') {
        const { results } = await env.DB.prepare(
          `SELECT a.id, a.created_at, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.expected_salary, a.available_date, a.location_ok, a.note,
                  a.resume_file_id, a.resume_url,
                  a.disc_primary, a.disc_d, a.disc_i, a.disc_s, a.disc_c,
                  j.client_screen_conditions, j.client_name, j.must_skills
             FROM applications a JOIN jobs j ON j.slug = a.job_slug
            WHERE a.status = 'pending_screen' AND a.screen_decision IS NULL
            ORDER BY a.created_at ASC LIMIT 100`
        ).all();
        return json(request, { ok: true, pending: results || [] });
      }

      // 顧問對一筆待審核應徵做出處置：approve / redirect / decline。
      if (p === '/admin/screen-decide' && request.method === 'POST') {
        const b = await request.json();
        const r = await applyScreenDecision(env, b.application_id, b.decision, {
          note: b.note, redirectJobSlug: b.redirect_job_slug,
        });
        return json(request, r, r.ok ? 200 : (r.status || 400));
      }

      if (p === '/admin/list') {
        const { results } = await env.DB.prepare(
          `SELECT * FROM applications ORDER BY created_at DESC LIMIT 200`
        ).all();
        return json(request, { applications: results });
      }


      // 報告清單。刻意不回傳報告全文——列表頁不需要，
      // 而且報告累積之後每次都傳全文會讓頁面越開越慢。

      // 所有應徵與面談紀錄。
      // 為什麼不能只看 /admin/reports：報告只有面談收尾後才有，
      // 進行中的、或報告產生失敗的，逐字稿明明在資料庫裡卻看不到——
      // 那些正是最需要有人去看一眼的。
      // 面談結束通知。由本機面談引擎在產完報告後呼叫——
      // Resend 金鑰只放在 Worker 的 secret，本機不留一份。
      // 阿福健檢報告 PDF 上傳——checkup_daemon.py 本機產出 PDF 後打這支存進 D1，
      // 用跟履歷附件同一套 files/file_chunks 切塊機制（saveUpload），避開單值長度上限。
      // ⚠️ 2026-08-13 暫時加：QA 用，手動觸發某一張卡片而不用等排程時間到——
      // 8 階段流程一次做完，真的等每個排程時間點（5分鐘/2天/18:30）逐一測完不現實。
      // 測完會拿掉，不要留在正式版。
      if (p === '/admin/test-push' && request.method === 'POST') {
        const b = await request.json();
        const appId = b.application_id;
        if (!appId) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const kind = String(b.kind || '');
        try {
          if (kind === 'postinterview') {
            const app = await env.DB.prepare(`SELECT job_slug, job_title FROM applications WHERE id=?`).bind(appId).first();
            const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
            const bound = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(appId));
            const flex = { type: 'flex', altText: '您的初審已經完成，1-2 天內會有進一步消息',
              contents: { type: 'bubble',
                header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                  contents: [{ type: 'text', text: '📋 初審已完成', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                    { type: 'text', text: app.job_title || app.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' }] },
                body: { type: 'box', layout: 'vertical', paddingAll: '16px',
                  contents: [{ type: 'text', size: 'sm', color: '#16202e', wrap: true,
                    text: '1-2 天內會審閱完成，若有任何需要確認的地方會主動通知您；有任何問題也歡迎直接訊息告知。\n\n如果確認符合條件，接下來會協助送審給用人單位，確認是否安排進一步面談。' }] } } };
            for (const bd of bound) await linePushMessages(env, bd.line_user_id, [flex]);
            return json(request, { ok: true, pushed: bound.length });
          }
          if (kind === 'progress2day') {
            const app = await env.DB.prepare(`SELECT name, job_slug, job_title FROM applications WHERE id=?`).bind(appId).first();
            const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
            const bound = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(appId));
            const flex = { type: 'flex', altText: '您的初審已經完成 2 天了，想更新一下進度嗎？',
              contents: { type: 'bubble',
                header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                  contents: [{ type: 'text', text: '📋 進度提醒', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                    { type: 'text', text: app.job_title || app.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' }] },
                body: { type: 'box', layout: 'vertical', paddingAll: '16px',
                  contents: [{ type: 'text', text: `👤 ${app.name} 您好`, size: 'sm', color: '#8993a8' },
                    { type: 'separator', margin: 'md' },
                    { type: 'text', text: '您的初審已經完成 2 天了，還在審閱中。想主動催一下進度嗎？', size: 'sm', color: '#16202e', wrap: true, margin: 'md' }] },
                footer: { type: 'box', layout: 'vertical', paddingAll: '12px',
                  contents: [{ type: 'button', style: 'primary', color: '#2f6fed', height: 'sm',
                    action: { type: 'postback', label: '🔔 提醒顧問看一下', data: `nudge_consultant:${appId}`, displayText: '請顧問更新一下我的進度' } }] } } };
            for (const bd of bound) await linePushMessages(env, bd.line_user_id, [flex]);
            return json(request, { ok: true, pushed: bound.length });
          }
          if (kind === 'offer') {
            await notifyOfferFlex(env, appId);
            return json(request, { ok: true });
          }
          if (kind === 'onboard_date') {
            await notifyOnboardDateFlex(env, appId, b.onboard_date || '2026-09-15 09:00');
            return json(request, { ok: true });
          }
          if (kind === 'care') {
            const point = Number(b.point) || 1;
            const placement = await env.DB.prepare(
              `SELECT candidate_name, job_slug, job_title FROM placements WHERE application_id=? ORDER BY updated_at DESC LIMIT 1`
            ).bind(appId).first();
            if (!placement) return json(request, { ok: false, error: '這筆測試資料還沒有 placements，先用 kind=make_placement 建一筆' }, 400);
            const CARE_TEXT = {
              1: '第一天上班辛苦了！環境跟同事還算好相處嗎？剛開始難免會有點生疏，有任何狀況都可以直接跟顧問說 😊',
              3: '到職滿 3 天了，這幾天下來還適應嗎？有任何狀況都可以直接跟顧問說，不用不好意思 😊',
              7: '到職滿一週了，工作內容跟一開始想的差不多嗎？如果有落差或想聊的，顧問都在 🙌',
              28: '到職滿一個月了，恭喜順利度過剛開始最需要適應的階段！之後有任何狀況，顧問還是隨時都在 😊',
            };
            const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
            const bound = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(appId));
            const flex = { type: 'flex', altText: CARE_TEXT[point],
              contents: { type: 'bubble',
                header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                  contents: [{ type: 'text', text: point === 1 ? '💚 到職第一天' : '💚 到職關懷', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                    { type: 'text', text: placement.job_title || placement.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' }] },
                body: { type: 'box', layout: 'vertical', paddingAll: '16px',
                  contents: [{ type: 'text', text: `👤 ${placement.candidate_name} 您好`, size: 'sm', color: '#8993a8' },
                    { type: 'separator', margin: 'md' },
                    { type: 'text', text: CARE_TEXT[point], size: 'sm', color: '#16202e', wrap: true, margin: 'md' }] },
                footer: { type: 'box', layout: 'vertical', spacing: 'sm', paddingAll: '12px',
                  contents: [{ type: 'box', layout: 'horizontal', spacing: 'sm',
                    contents: [
                      { type: 'button', style: 'primary', color: '#1f8f5f', height: 'sm',
                        action: { type: 'postback', label: '👍 一切順利', data: `care_resp:ok:${appId}:${point}`, displayText: '一切順利' } },
                      { type: 'button', style: 'secondary', height: 'sm',
                        action: { type: 'postback', label: '💬 想聊聊', data: `care_resp:talk:${appId}:${point}`, displayText: '我想聊聊' } },
                    ] }] } } };
            for (const bd of bound) await linePushMessages(env, bd.line_user_id, [flex]);
            return json(request, { ok: true, pushed: bound.length });
          }
          if (kind === 'prep') {
            const placement = await env.DB.prepare(
              `SELECT candidate_name, job_slug, job_title, onboard_date FROM placements WHERE application_id=? ORDER BY updated_at DESC LIMIT 1`
            ).bind(appId).first();
            if (!placement) return json(request, { ok: false, error: '這筆測試資料還沒有 placements' }, 400);
            const job = await env.DB.prepare(`SELECT onboarding_prep_note FROM jobs WHERE slug=?`).bind(placement.job_slug).first();
            const prepNote = (job && job.onboarding_prep_note) || '（尚未填寫，請先到 /consultant/jobs 填「報到前準備事項」）';
            const { results } = await env.DB.prepare(`SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`).all();
            const bound = (results || []).filter((row) => safeJsonArray(row.application_ids).includes(appId));
            const [d, t] = String(placement.onboard_date || '').split(' ');
            const flex = { type: 'flex', altText: `報到前準備事項：${placement.job_title}`,
              contents: { type: 'bubble',
                header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                  contents: [{ type: 'text', text: '📋 報到前準備事項', color: '#8fb0ff', size: 'xs', weight: 'bold' },
                    { type: 'text', text: placement.job_title || placement.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' }] },
                body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
                  contents: [{ type: 'text', text: `👤 ${placement.candidate_name} 您好`, size: 'sm', color: '#8993a8' },
                    { type: 'text', text: `🗓 報到日：${d || ''}${t ? ' ' + t : ''}`, size: 'sm', color: '#8993a8', wrap: true },
                    { type: 'separator', margin: 'md' },
                    { type: 'text', text: prepNote, size: 'sm', color: '#16202e', wrap: true, margin: 'md' }] },
                footer: { type: 'box', layout: 'vertical', paddingAll: '12px',
                  contents: [{ type: 'button', style: 'secondary', height: 'sm',
                    action: { type: 'postback', label: '聯繫顧問', data: `contact_consultant:${appId}`, displayText: '我對報到有問題想請顧問協助' } }] } } };
            for (const bd of bound) await linePushMessages(env, bd.line_user_id, [flex]);
            return json(request, { ok: true, pushed: bound.length });
          }
          if (kind === 'make_placement') {
            const app = await env.DB.prepare(`SELECT name, job_slug, job_title FROM applications WHERE id=?`).bind(appId).first();
            if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);
            const now = nowTaipei();
            const onboardDate = b.onboard_date || now;
            const exist = await env.DB.prepare(`SELECT id FROM placements WHERE application_id=?`).bind(appId).first();
            if (exist) {
              await env.DB.prepare(`UPDATE placements SET stage='GUARANTEE', onboard_date=?, updated_at=? WHERE id=?`)
                .bind(onboardDate, now, exist.id).run();
            } else {
              await env.DB.prepare(
                `INSERT INTO placements (application_id, candidate_name, job_slug, job_title, client_name, stage, stage_since, onboard_date, created_at, updated_at)
                 VALUES (?,?,?,?,?, 'GUARANTEE', date('now','+8 hours'), ?, ?, ?)`
              ).bind(appId, app.name, app.job_slug, app.job_title, '測試企業', onboardDate, now, now).run();
            }
            return json(request, { ok: true });
          }
          return json(request, { ok: false, error: '不認得的 kind' }, 400);
        } catch (e) {
          return json(request, { ok: false, error: String(e) }, 500);
        }
      }

      // 2026-08-13 加：通知已面談過的候選人「LINE 查進度」這個功能。
      // 單筆寄送——先給 Jacky 測試自己的信箱用，之後要對真實候選人名單發送時，
      // 由本機或後台再迴圈呼叫這支，一次一封，不在這裡做批次寄送
      // （批次寄送出錯要能知道是哪一筆，單筆呼叫比較好追蹤）。
      if (p === '/admin/notify-line-progress-feature' && request.method === 'POST') {
        const b = await request.json();
        if (!b.to || !b.name) return json(request, { ok: false, error: '缺 to 或 name' }, 400);
        const jobTitle = b.job_title || '這個職缺';
        const sent = await sendMail(
          env, b.to,
          '追蹤您的面試進度，直接用 LINE 就能查詢',
          [`${b.name} 您好，`,
           `之前您透過阿財完成了「${jobTitle}」的初步面談，謝謝您撥空參與。`,
           `跟您說明一個新功能：現在可以直接透過 LINE 官方帳號「全民獵才」查詢您目前的面試進度，不用被動等待消息、也不用自己發訊息詢問。`,
           `圖文教學在這裡，四個步驟就能完成：`],
          { text: '查看使用教學', url: 'https://step1ne.com/line-guide/' }
        );
        return json(request, { ok: sent });
      }

      if (p === '/admin/checkup-pdf' && request.method === 'POST') {
        const b = await request.json();
        if (!b.id || !b.pdf_b64) return json(request, { ok: false, error: '缺 id 或 pdf_b64' }, 400);
        const now = nowTaipei();
        const saved = await saveUpload(env, { b64: b.pdf_b64, name: `健檢報告_${b.name || b.id}.pdf`, mime: 'application/pdf' }, now);
        if (!saved || saved.tooBig) return json(request, { ok: false, error: 'PDF 太大存不下' }, 400);
        await env.DB.prepare(`UPDATE checkups SET report_pdf_file_id = ? WHERE id = ?`)
          .bind(saved.fileId, b.id).run();
        return json(request, { ok: true, fileId: saved.fileId });
      }

      // 阿財初篩報告寫入——2026-08-13 加。interview_daemon.py 原本用
      // `wrangler d1 execute --file=` 直接寫，但那條路走的是 wrangler CLI，
      // report 內容（尤其是照新版訪談規範做的 20-30 分鐘深挖）長一點還是會撞
      // `statement too long: SQLITE_TOOBIG`——徐振倫這場報告就是這樣整個沒存進去。
      // ⚠️ --file= 只解決「CLI 參數長度」，D1 本身的單值大小上限是另一回事，
      // 唯一繞得過去的路是走 Worker 的原生 D1 binding（跟 saveUpload 存 PDF
      // 同一個道理），不是再換一種 wrangler 呼叫方式。改成本機 daemon 打這支，
      // 由 Worker 用 .bind() 參數化寫入，不再經過 wrangler CLI 這條路。
      if (p === '/admin/report-ingest' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id || !b.content_md) {
          return json(request, { ok: false, error: '缺 application_id 或 content_md' }, 400);
        }
        const now = nowTaipei();
        const rid = String(b.id || `r_${b.application_id.slice(0, 8)}_${Date.now()}`);
        await env.DB.prepare(
          `INSERT INTO reports (id, application_id, created_at, content_md, content_json) VALUES (?,?,?,?,?)`
        ).bind(rid, b.application_id, now, b.content_md, b.content_json || null).run();
        return json(request, { ok: true, id: rid });
      }

      // 阿福健檢報告完成，寄信通知本人——2026-08-12 Jacky 要求：報告不能只留在
      // 對談連結裡等本人自己回去點，要主動寄到信箱。跟阿財那條線分開，
      // 因為 checkups 是獨立資料表，收件人是「來問自己市場價值的本人」，
      // 不是「應徵了某個職缺的候選人」，語氣跟連結都不一樣。
      if (p === '/admin/checkup-done' && request.method === 'POST') {
        const b = await request.json();
        const c = await env.DB.prepare(
          `SELECT name, email, chat_token, report_at FROM checkups WHERE id = ?`
        ).bind(b.id).first();
        if (!c) return json(request, { ok: false, error: '找不到這筆健檢' }, 404);
        if (!c.report_at) return json(request, { ok: false, error: '報告還沒產出' }, 400);
        const url = `https://step1ne-recruit-api.aiagentg888.workers.dev/checkup-chat/${c.chat_token}/report`;
        const ok = await sendMail(
          env, c.email,
          `您的 AI 履歷健檢報告已經完成`,
          [`${c.name} 您好，`,
           `謝謝您撥空跟阿福聊完這場履歷健檢，報告已經整理好了。`,
           `點下方連結隨時可以查看，同一個連結也能下載 PDF 留存。`],
          { text: '查看我的健檢報告', url }
        );
        return json(request, { ok });
      }

      // 2026-08-14 加：健檢對談因為系統出錯被腰斬（不是本人自己閒置不回），
      // 寄一封道歉信附「回到聊天室」的連結，跟上面 checkup-done 寄的
      // 「報告完成」信是兩種不同情境，語氣跟連結目的地都不一樣。
      if (p === '/admin/checkup-system-error' && request.method === 'POST') {
        const b = await request.json();
        const c = await env.DB.prepare(
          `SELECT name, email, chat_token FROM checkups WHERE id = ?`
        ).bind(b.id).first();
        if (!c) return json(request, { ok: false, error: '找不到這筆健檢' }, 404);
        const url = `https://step1ne.com/checkup-chat/?t=${c.chat_token}`;
        const ok = await sendMail(
          env, c.email,
          `不好意思，健檢對談中斷了`,
          [`${c.name} 您好，`,
           `剛剛跟阿福聊到一半，我們這邊系統出了點狀況，很抱歉打斷您。`,
           `原本的對話都還在，點下方連結就能回到聊天室繼續，不用重新開始。`],
          { text: '回到聊天室', url }
        );
        return json(request, { ok });
      }

      // 顧問安排客戶面談時段，讓人選自己挑——2026-08-13 加。
      // 跟 /book（阿財面談的固定時段格）不是同一件事：這裡是顧問針對
      // 「這一位人選、這一次客戶面談」手動輸入幾個候選時段，不是系統自動排的格子。
      if (p === '/admin/appointment' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id || !Array.isArray(b.slots) || !b.slots.length) {
          return json(request, { ok: false, error: '缺 application_id 或 slots' }, 400);
        }
        const app = await env.DB.prepare(
          `SELECT id, name, job_title, job_slug FROM applications WHERE id = ?`
        ).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);

        // meeting_url：視訊面試才會有，讓人選選完時段就知道要點哪個連結加入，
        // 不用等顧問另外私訊——2026-08-13 加，只接受 http(s) 開頭，避免存進奇怪的值。
        const slots = b.slots
          .map((s) => {
            const row = {
              slot_at: String(s.slot_at || '').slice(0, 16),
              format: String(s.format || '').slice(0, 20),
            };
            const mu = String(s.meeting_url || '').trim().slice(0, 500);
            if (mu && /^https?:\/\//i.test(mu)) row.meeting_url = mu;
            return row;
          })
          .filter((s) => s.slot_at);
        if (!slots.length) return json(request, { ok: false, error: 'slots 格式不對' }, 400);

        // 2026-08-13 加：階段、面談地點、面談對象——原本只有 note 這個自由文字欄位，
        // 顧問要嘛把地點/對象塞進備註裡讓格式不一致，要嘛乾脆不寫。
        const stage = Math.max(1, Math.min(4, Number(b.stage) || 1));
        const location = String(b.location || '').slice(0, 300);
        const interviewer = String(b.interviewer || '').slice(0, 100);

        const id = uid();
        const token = uid().replace(/-/g, '') + uid().replace(/-/g, '').slice(0, 16);
        const now = nowTaipei();
        const note = String(b.note || '').slice(0, 500);

        await env.DB.prepare(
          `INSERT INTO interview_appointments
             (id, application_id, created_at, created_by, slots, note, status, token, stage, location, interviewer)
           VALUES (?,?,?,?,?,?, 'pending', ?,?,?,?)`
        ).bind(id, app.id, now, b.by || null, JSON.stringify(slots), note, token, stage,
               location || null, interviewer || null).run();

        const url = `https://step1ne.com/appointment/?t=${token}`;
        const stageLabel = STAGE_LABEL_APPT[stage] || '第一階段';
        let sent = 0, pushErr = null;

        // 推播給人選（如果他綁過 LINE）——跟查進度那條線共用同一個 line_bindings 表。
        // 2026-08-13 從純文字連結升級成 Carousel：每個時段一張卡，點按鈕
        // 直接在 LINE 對話裡確認，不用跳出去網頁（網頁版連結還是留著，
        // 給沒綁 LINE 或想在瀏覽器操作的人）。
        try {
          const { results } = await env.DB.prepare(
            `SELECT line_user_id, application_ids FROM line_bindings WHERE state='bound'`
          ).all();
          const hits = (results || []).filter((row) =>
            safeJsonArray(row.application_ids).includes(app.id));
          if (hits.length) {
            const apptRow = { slots: JSON.stringify(slots), stage };
            const flex = {
              type: 'flex',
              altText: `${app.name} 您好，${stageLabel}面談時段已經安排好了，麻煩選一個方便的時間：${url}`,
              contents: appointmentFlexCarousel(apptRow, token),
            };
            // 2026-08-13 改：Jacky 明確要求「每一則面談通知都要卡片呈現」——
            // 這段原本是純文字，跟其他階段的卡片風格不一致，改成 Flex 卡。
            const intro = { type: 'flex',
              altText: `${app.name} 您好，${stageLabel}面談時段已經安排好了`,
              contents: {
                type: 'bubble',
                header: { type: 'box', layout: 'vertical', backgroundColor: '#1c2f6b', paddingAll: '16px',
                  contents: [
                    { type: 'text', text: `📅 邀約・${stageLabel}面談`, color: '#8fb0ff', size: 'xs', weight: 'bold' },
                    { type: 'text', text: app.job_title || app.job_slug || '', color: '#ffffff', size: 'lg', weight: 'bold', wrap: true, margin: 'sm' },
                  ] },
                body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'sm',
                  contents: [
                    { type: 'text', text: `👤 ${app.name} 您好`, size: 'sm', color: '#8993a8' },
                    { type: 'separator', margin: 'md' },
                    { type: 'text', text: `${stageLabel}面談時段已經安排好了，請在下一張卡片選一個方便的時間：`,
                      size: 'sm', color: '#16202e', wrap: true, margin: 'md' },
                    ...(note ? [{ type: 'text', text: `📝 ${note}`, size: 'sm', color: '#4c5568', wrap: true, margin: 'md' }] : []),
                  ] },
              } };
            for (const row of hits) await linePushMessages(env, row.line_user_id, [intro, flex]);
            sent = hits.length;
          }
        } catch (e) { pushErr = String(e).slice(0, 160); }

        // ⚠️ 2026-08-21 真實事故：顧問在後台幫孙悦排了第一階段面談，畫面顯示成功，
        //    但她沒有綁 LINE——卡片靜默地沒有送出，顧問以為發了、人選什麼都沒收到，
        //    兩邊都在等。（顧問還以為沒按成功，兩分鐘內又按了一次。）
        //    所以這裡改成把「到底有沒有送出去」誠實回報，前端會直接顯示，
        //    沒送出時要顧問自己把連結傳給人選。
        if (!sent) {
          await notify(env,
            `⚠️ 面談邀約沒有送到人選手上\n\n` +
            `${app.name}　${app.job_title || app.job_slug || ''}　${stageLabel}\n` +
            (pushErr ? `原因：LINE 推播失敗（${pushErr}）\n` : `原因：這位人選還沒有綁定 LINE\n`) +
            `\n時段已經存好了，但**要請你自己把這個連結傳給他**：\n${url}`,
            { message_thread_id: THREAD.decide }).catch(() => {});
        }

        return json(request, { ok: true, id, token, url,
          // 前端拿這兩個欄位決定要不要跳「請自己傳連結」的提示
          notified: sent > 0,
          notify_reason: sent > 0 ? null : (pushErr ? 'push_failed' : 'no_line_binding') });
      }

      // 2026-08-13 加：自動帶入用——同一間公司（同 job_slug）、同一階段上次填過的
      // 面談地點／面談對象，讓顧問不用每次從空白重打。只回傳最近一筆有填過的紀錄，
      // 前端會把這些值先放進表單，顧問確認一下或改掉再送出，不是不給看就直接沿用。
      if (p === '/admin/appointment/last' && request.method === 'GET') {
        const jobSlug = url.searchParams.get('job_slug') || '';
        const stage = Math.max(1, Math.min(4, Number(url.searchParams.get('stage')) || 1));
        if (!jobSlug) return json(request, { ok: false, error: '缺 job_slug' }, 400);
        const row = await env.DB.prepare(
          `SELECT ia.location, ia.interviewer, ia.note FROM interview_appointments ia
             JOIN applications a ON a.id = ia.application_id
            WHERE a.job_slug = ? AND ia.stage = ? AND (ia.location IS NOT NULL OR ia.interviewer IS NOT NULL)
            ORDER BY ia.created_at DESC LIMIT 1`
        ).bind(jobSlug, stage).first();
        return json(request, { ok: true, location: row?.location || '', interviewer: row?.interviewer || '', note: row?.note || '' });
      }

      // 人選挑面談時段的頁面在讀資料——公開，token 即權限，跟 /book 同一套認證模式
      // 重新開啟面談室——2026-08-13 真實事故換來的端點：候選人因為系統
      // 問題（用量上限、逾時⋯）被迫中斷，顧問承諾「之後再進來即可」，
      // 但原本只有固定 3 小時的保留時鐘，講好聽是「保留」，實際上顧問講完
      // 那句話沒多久房間就自己關了。這支端點讓顧問自己在後台重開，
      // 不用每次都回頭找工程端手動改資料庫。
      if (p === '/admin/reopen-interview' && request.method === 'POST') {
        const b = await request.json();
        const app = await env.DB.prepare(
          `SELECT id, name FROM applications WHERE id = ?`
        ).bind(b.id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);

        const days = Math.max(1, Math.min(90, Number(b.hold_days) || 30));
        const now = nowTaipei();
        const holdUntil = new Date(Date.now() + days * 86400000)
          .toISOString().slice(0, 19).replace('T', ' ');
        const note = String(b.note || '不好意思，這邊系統剛才出了點狀況，現在已經修復了——您可以直接在這裡繼續打字，我會接著談。').slice(0, 500);

        await env.DB.batch([
          env.DB.prepare(
            `INSERT INTO messages (application_id, role, content, created_at) VALUES (?,?,?,?)`
          ).bind(app.id, 'assistant', note, now),
          env.DB.prepare(
            `UPDATE applications SET interview_state='paused', interview_ended_at=NULL,
                    interview_started_at=?, hold_until=?, status='interviewing'
              WHERE id=?`
          ).bind(now, holdUntil, app.id),
        ]);
        return json(request, { ok: true, hold_until: holdUntil });
      }

      // 2026-08-13 加：Jacky 明確要求——標記「這位候選人錄取了」不該只能靠打字到
      // Telegram 讓總指揮解析（report_tick.py 那條線），後台要有一顆按鈕直接做，
      // 按下去馬上寫 placements.stage='OFFER_ACCEPTED' 並推播錄取卡給候選人。
      // 跟 report_tick.py 寫的是同一個 stage 代碼，兩條路殊途同歸，不會對不起來。
      if (p === '/admin/mark-offer' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const app = await env.DB.prepare(
          `SELECT a.name, a.job_slug, a.job_title, j.client_name FROM applications a
             LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id = ?`
        ).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);

        const now = nowTaipei();
        const exist = await env.DB.prepare(`SELECT id FROM placements WHERE application_id = ?`).bind(b.application_id).first();
        if (exist) {
          await env.DB.prepare(`UPDATE placements SET stage='OFFER_ACCEPTED', stage_since=?, updated_at=? WHERE id=?`)
            .bind(now.slice(0, 10), now, exist.id).run();
        } else {
          await env.DB.prepare(
            `INSERT INTO placements (application_id, candidate_name, job_slug, job_title, client_name, stage, stage_since, created_at, updated_at)
             VALUES (?,?,?,?,?, 'OFFER_ACCEPTED', ?, ?, ?)`
          ).bind(b.application_id, app.name, app.job_slug, app.job_title,
                 app.client_name || '（職缺未綁客戶）', now.slice(0, 10), now, now).run();
        }
        await notifyOfferFlex(env, b.application_id);
        return json(request, { ok: true });
      }

      // 2026-08-13 加：結案不續——跟上面「標記錄取」對稱的動作。之前 CLOSED_LOST
      // 只能靠顧問在 Telegram「顧問人選回報區」打字讓 report_tick.py 解析寫入，
      // 網頁後台完全沒有入口。reason 是顧問寫給候選人看的委婉說法（不是內部備註），
      // 會直接組進候選人收到的 LINE 訊息，見 deriveApplicationProgress()。
      // 2026-08-13 加：手動重寄面談連結信——通常用在顧問擔心候選人沒收到、
      // 或候選人是在這支新功能上線前就已經被分流成「立即面談」的舊資料
      // （那批人不會自動補寄，因為信是在分流那一刻才寄，不是事後補）。
      if (p === '/admin/resend-interview-link' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const app = await env.DB.prepare(
          `SELECT name, email, job_title, chat_token, status FROM applications WHERE id = ?`
        ).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);
        if (!app.chat_token) return json(request, { ok: false, error: '這筆應徵沒有面談連結' }, 400);
        const sent = await sendMail(
          env, app.email,
          `面談連結：${app.job_title || '這個職缺'}`,
          [`${app.name} 您好，`,
           `工作風格測驗已經收到，目前可以直接開始面談。`,
           `如果現在不方便，這封信可以先留著，準備好了再點下面的連結進入面談室即可。`],
          { url: `https://step1ne.com/interview/?t=${app.chat_token}`, text: '進入面談室' }
        );
        return json(request, { ok: sent, error: sent ? undefined : '寄信失敗，可能是 RESEND_API_KEY 沒設或信箱格式問題' });
      }

      if (p === '/admin/mark-closed' && request.method === 'POST') {
        const b = await request.json();
        const result = await doMarkClosed(env, b);
        return json(request, result.body, result.status);
      }

      // 2026-08-13 加：清除處置——轉給客戶／需補問／不推薦按錯了，之前完全沒有
      // 「復原成未處置」的路，只能找工程手動改資料庫（徐振倫那次就是這樣）。
      if (p === '/admin/clear-decision' && request.method === 'POST') {
        const b = await request.json();
        if (!b.id) return json(request, { ok: false, error: '缺報告 id' }, 400);
        await env.DB.prepare(`UPDATE reports SET consultant_decision=NULL, decided_at=NULL WHERE id=?`)
          .bind(b.id).run();
        return json(request, { ok: true });
      }

      // 2026-08-14 加：候選人回報「LINE 查進度沒有回應」時，用這支重推一次目前
      // 真實進度——用 push（notifyLineProgress），不是 reply，因為候選人當初那個
      // replyToken 早就用掉／過期了，沒辦法補發同一則。
      if (p === '/admin/push-progress' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const { results } = await env.DB.prepare(
          `SELECT line_user_id FROM line_bindings WHERE state='bound'`
        ).all();
        const bound = (results || []).filter((row) =>
          safeJsonArray(row.application_ids || '[]').includes(b.application_id));
        await notifyLineProgress(env, b.application_id);
        return json(request, { ok: true, pushed_to: bound.length });
      }

      if (p === '/admin/interview-done' && request.method === 'POST') {
        const b = await request.json();
        const app = await env.DB.prepare(
          `SELECT name, email, job_title, job_slug FROM applications WHERE id = ?`
        ).bind(b.id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);

        const cut = !!b.abandoned;   // 中途離開的人要用不一樣的語氣
        const ok = await sendMail(
          env, app.email,
          `面談紀錄已收到：${app.job_title || app.job_slug}`,
          cut
            ? [`${app.name} 您好，`,
               `看到您的面談中途結束了，先前談到的內容已經記錄下來。`,
               `如果只是臨時有事，隨時可以用原本的連結回來繼續，我們會接著談。`,
               `若想改由真人直接與您聯繫，也可以透過下方 LINE 留言。`]
            : [`${app.name} 您好，`,
               `謝謝您撥空完成初步面談，內容都收到了。`,
               `接下來會有人看過這份紀錄，如果合適會盡快與您聯繫安排下一步。`,
               `不管結果如何，都會通知您，不會讓您空等。`],
          null
        );
        return json(request, { ok });
      }


      // 顧問自己找到的人。
      //
      // 為什麼不叫他去填公開表單：顧問已經拿到履歷跟基本資料了，
      // 再要求候選人重填一次是白費工，而且多一道就少一半人願意做。
      // 這裡讓顧問直接建檔，拿到連結自己貼到 104 訊息或 LINE 給對方。
      if (p === '/admin/create-candidate' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        // 姓名、職缺、期望薪資、可到職日都是必填。
        // 前兩個不用解釋；後兩個是 2026-07-30 學到的——顧問建檔時留空，
        // 阿財在面談中就得花兩個來回去問，而每個來回是三分鐘。
        // 更糟的是那場被提早收尾，兩題都沒拿到，報告只能寫「未確認」。
        // 顧問手上本來就有這兩個資訊（104 履歷上有、或電話聊過），先填省的是面談時間。
        const miss = [];
        if (!b.name) miss.push('姓名');
        if (!b.job_slug) miss.push('應徵職缺');
        if (!b.expected_salary) miss.push('期望待遇');
        if (!b.available_date) miss.push('可到職日');
        if (miss.length) {
          return json(request, { ok: false, error: `這幾欄要填：${miss.join('、')}` }, 400);
        }
        // Email 是選填的——顧問可能只有對方的 104 帳號，沒有 email。
        // 但沒有 email 就不能寄信，前端要說清楚。
        if (b.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(b.email)) {
          return json(request, { ok: false, error: 'Email 格式看起來不正確' }, 400);
        }

        const id = uid();
        const now = nowTaipei();
        let fileId = null;
        const saved = await saveResume(env, b, now);
        if (saved && saved.tooBig) {
          return json(request, { ok: false,
            error: '履歷檔超過 8MB。改貼雲端連結（Google Drive／Dropbox）就沒有這個限制。' }, 400);
        }
        if (saved) fileId = saved.fileId;

        const chatToken = [...crypto.getRandomValues(new Uint8Array(24))]
          .map((x) => x.toString(16).padStart(2, '0')).join('');

        const job = await env.DB.prepare(`SELECT title FROM jobs WHERE slug = ?`)
          .bind(b.job_slug).first();

        await env.DB.prepare(
          `INSERT INTO applications
           (id, created_at, job_slug, job_title, name, email, phone,
            expected_salary, available_date, location_ok,
            resume_file_id, resume_url, note,
            utm_source, utm_medium, utm_campaign, referrer,
            status, consent_at, interview_mode, chat_token, interview_state, handled_by)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,
                   'consultant','sourced',?,NULL,'ready',?, 'now', ?, 'not_started', ?)`
        ).bind(
          id, now, b.job_slug, job ? job.title : (b.job_title || null),
          // ⚠️ applications.email 是 NOT NULL（那是給公開表單用的，那裡 email 必填）。
          // 顧問建檔時常常只有對方的 104 帳號、沒有 email，所以這裡存空字串。
          // 空字串在 JS 是 falsy，sendMail 的 `!to` 擋得掉，行為跟 null 一致。
          // 之後重建表把這欄改成可為 null 會更乾淨，但那要動到已經有資料的表。
          b.name, b.email || '', b.phone || null,
          b.expected_salary || null, b.available_date || null, b.location_ok || null,
          fileId, b.resume_url || null, b.note || null,
          b.channel || null,            // utm_campaign：從哪個管道找到的（104／LinkedIn／推薦…）
          now, chatToken, b.by || null
        ).run();

        const url = `https://step1ne.com/interview/?t=${chatToken}`;

        // 寄不寄信由顧問決定——多數情況他會自己貼到 104 私訊或 LINE，
        // 那比一封陌生的系統信更容易被讀。
        let mailed = false;
        if (b.send_mail && b.email) {
          mailed = await sendMail(
            env, b.email,
            `${b.name} 您好，關於「${job ? job.title : b.job_slug}」的初步面談`,
            [`${b.name} 您好，`,
             `我是 Step1ne 德仁管理顧問的招募團隊，看到您的背景，覺得跟「${job ? job.title : b.job_slug}」這個機會蠻符合的。`,
             `想先請您花 20 到 30 分鐘，跟 AI 面談助理阿財做一次初步了解——他會先看過您的資料，聊聊經歷與您在意的條件。`,
             `談完之後由真人接手，不管有沒有下一步都會通知您。`,
             `請把這封信留著，中途離開也能用同一個連結回來。`],
            { url, text: '開始初步面談' }
          );
        }

        await notify(
          env,
          `👤 顧問建檔：${b.name}\n職缺：${job ? job.title : b.job_slug}\n` +
            `來源：${b.channel || '未填'}\n` +
            `履歷：${fileId ? '已上傳' : (b.resume_url ? '雲端連結' : '⚠️ 無')}\n` +
            `通知信：${mailed ? '已寄出' : (b.send_mail ? '⚠️ 寄送失敗' : '未寄（顧問自行轉達）')}\n` +
            `連結請到顧問後台複製：https://step1ne.com/consultant/reports/`
        ,
        { message_thread_id: THREAD.intake });

        return json(request, { ok: true, id, chat_token: chatToken, url, mailed,
                               has_resume: !!fileId });
      }

      // 刪除一整筆應徵。
      //
      // ⚠️ 這是不可逆的，而且逐字稿在爭議時是法律證據——所以前端要二次確認，
      // 而且要把「刪了什麼」寫進通知，事後才追得回來是誰刪的、刪了幾則。
      // 個資法的「特定目的消失應刪除」也需要這個能力。
      if (p === '/admin/delete-session' && request.method === 'POST') {
        const b = await request.json();
        if (!b.id) return json(request, { ok: false, error: '缺少 id' }, 400);

        const app = await env.DB.prepare(
          `SELECT a.name, a.job_slug, a.resume_file_id,
                  (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id) AS n
             FROM applications a WHERE a.id = ?`
        ).bind(b.id).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆資料' }, 404);

        await env.DB.batch([
          env.DB.prepare(`DELETE FROM file_chunks WHERE file_id = ?`).bind(app.resume_file_id || ''),
          env.DB.prepare(`DELETE FROM files WHERE id = ?`).bind(app.resume_file_id || ''),
          env.DB.prepare(`DELETE FROM messages WHERE application_id = ?`).bind(b.id),
          env.DB.prepare(`DELETE FROM reports WHERE application_id = ?`).bind(b.id),
          env.DB.prepare(`DELETE FROM chat_reports WHERE application_id = ?`).bind(b.id),
          env.DB.prepare(`DELETE FROM interview_feedback WHERE application_id = ?`).bind(b.id),
          env.DB.prepare(`DELETE FROM bookings WHERE application_id = ?`).bind(b.id),
          env.DB.prepare(`DELETE FROM applications WHERE id = ?`).bind(b.id),
        ]);

        await notify(env, `🗑 已刪除應徵紀錄：${app.name}（${app.job_slug}）　含 ${app.n} 則對話`,
        { message_thread_id: THREAD.system });
        return json(request, { ok: true, name: app.name, messages: app.n });
      }

      if (p === '/admin/sessions') {
        const q = (url.searchParams.get('q') || '').trim();
        const st = url.searchParams.get('istate') || '';
        const limit = Math.min(Number(url.searchParams.get('limit') || 50), 200);
        const offset = Number(url.searchParams.get('offset') || 0) || 0;
        // 被後送覆蓋掉的那筆不列出來。顧問看到一筆「未開始」會去催人，
        // 但那個人其實用另一筆已經面完了——催錯人比漏看還糟。
        const where = ['a.superseded_by IS NULL']; const bind = [];
        if (q) { where.push('(a.name LIKE ? OR a.email LIKE ?)'); bind.push(`%${q}%`, `%${q}%`); }
        if (st) { where.push('a.interview_state = ?'); bind.push(st); }
        // ⚠️ 2026-08-11：職缺下拉在「所有面談紀錄」分頁沒反應，因為只有
        //    /admin/reports 收 job 參數，這支漏了。前端一直有送，後端沒接。
        const jb = url.searchParams.get('job') || '';
        if (jb) { where.push('a.job_slug = ?'); bind.push(jb); }

        const { results } = await env.DB.prepare(
          `SELECT a.id, a.created_at, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.interview_state, a.interview_mode, a.interview_started_at, a.interview_ended_at,
                  a.expected_salary, a.available_date, a.status,
                  (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id) AS turns,
                  (SELECT r.id FROM reports r WHERE r.application_id = a.id ORDER BY r.created_at DESC LIMIT 1) AS report_id,
                  (SELECT m.created_at FROM messages m WHERE m.application_id = a.id
                    ORDER BY m.id DESC LIMIT 1) AS last_at
             FROM applications a
            WHERE ${where.join(' AND ')}
            ORDER BY a.created_at DESC LIMIT ? OFFSET ?`
        ).bind(...bind, limit, offset).all();

        const cnt = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM applications a WHERE ${where.join(' AND ')}`
        ).bind(...bind).first();

        return json(request, { ok: true, total: cnt ? cnt.n : 0, sessions: results || [], limit, offset });
      }

      // 職缺清單：顧問用來調整「服務線」與「客戶對象」。
      //
      // 為什麼需要：這兩個分類會變動——未簽約的客戶簽約了、朋友介紹的案子
      // 後來變成正式委託。而它們會連動決定履歷具名或匿名、報告掛不掛品牌、
      // 要不要揭露 AI 面談，改錯或忘了改都是客戶隱私問題，不能只在建立時設一次。
      if (p === '/admin/jobs' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          // 漏斗數字一起帶出來。只看應徵數會誤判——20 個應徵但 0 個送到客戶，
          // 跟 3 個應徵送出 2 個，是完全不同的狀況，而後者才是好職缺。
          // 2026-08-25 加 cc.display_name：兩個不同客戶的職缺標題可能撞名
          // （例如弘昌跟律準都開「BIM 工程師」），只看標題分不出是哪家，
          // 一定要把客戶名稱一起帶出來顯示。
          // 2026-09-01 加 j.company_id：人選卡片「推薦給客戶／職缺」要能依選定的
          // 客戶篩出「這家客戶有哪些職缺」，之前只有客戶名稱、沒有 id 對不起來。
          `SELECT j.slug, j.title, j.service_line, j.client_relation, j.seniority,
                  j.client_named, j.ai_disclosure, j.client_code, j.status, j.interview_language, j.onboarding_prep_note,
                  j.company_id, cc.display_name AS company_display_name,
                  -- ① 應徵：填完申請表送出的人（不含重複投遞被標記取代的）
                  (SELECT COUNT(*) FROM applications a
                    WHERE a.job_slug = j.slug AND a.superseded_by IS NULL) AS applicants,
                  -- ② 初審：阿財談完、報告已產出，但顧問還沒決定要不要送客戶。
                  --    這一關卡住＝人在等我們，不是在等客戶。
                  -- ⚠️ 一個人可能有多份報告（中斷重談就會多一份），
                  --    用 COUNT(*) 會把同一個人算好幾次——實測出現過「應徵 4 人、初審 5 人」。
                  --    只看最新那份報告的處置狀態。
                  (SELECT COUNT(*) FROM applications a
                    WHERE a.job_slug = j.slug AND a.superseded_by IS NULL
                      AND a.interview_state IN ('done','paused')
                      AND (SELECT r.consultant_decision FROM reports r
                            WHERE r.application_id = a.id
                            ORDER BY r.created_at DESC LIMIT 1) IS NULL) AS screening,
                  -- ③ 客戶面談：從顧問把履歷送給客戶那一刻起算，
                  --    客戶不論面試幾關都算在這一關，直到錄取才離開。
                  -- ⚠️ 2026-08-26 補齊字彙：placements.stage 歷來由三套工具寫入，
                  --    用的詞不一樣（placement_tracker.py 寫 AWAITING_CLIENT_FEEDBACK／
                  --    INTERVIEW_COMPLETED／REJECTED_BY_CLIENT／WITHDRAWN_BY_CANDIDATE／
                  --    CLOSED，Telegram 回報流程寫 SUBMITTED／OFFER_ACCEPTED…）。
                  --    這裡原本只認 4 個詞，實測 12 筆送件有 8 筆落在任何一欄之外——
                  --    張博州、呂皓宇等客戶回覆已經 22 天，漏斗上完全看不到。
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug
                      AND UPPER(p.stage) IN ('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW',
                                             'AWAITING_CLIENT_FEEDBACK','INTERVIEW_COMPLETED')
                      AND p.onboard_date IS NULL) AS client_stage,
                  -- ④ 錄取：客戶決定要了、還沒到職（談offer、辦離職、等報到都在這）
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug
                      AND UPPER(p.stage) IN ('OFFER','OFFER_ACCEPTED','HIRED','PLACED')
                      AND p.onboard_date IS NULL) AS offered,
                  -- ⑤ 到職：真的上工了。這一關才算成案。
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug AND p.onboard_date IS NOT NULL) AS onboard,
                  -- ⑥ 結案：送出去之後客戶不要或人選拒絕，案子停在這裡不會再動。
                  --    2026-08-12 加：在這之前 CLOSED_LOST 的人不落在任何一關，
                  --    畫面上就是憑空消失——顧問完全看不出「送了多少、掉了多少」。
                  --    2026-08-26 補上另外三種結案寫法（不同工具寫的同一件事）。
                  --    已經到職的不算結案（互斥）——不然同一個人會在兩欄各出現一次，
                  --    漏斗加總會超過實際送件數。
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug
                      AND UPPER(p.stage) IN ('CLOSED_LOST','CLOSED','REJECTED_BY_CLIENT','WITHDRAWN_BY_CANDIDATE')
                      AND p.onboard_date IS NULL) AS closed
             FROM jobs j
             LEFT JOIN client_companies cc ON cc.id = j.company_id
            ORDER BY (j.status = 'closed'), j.service_line, j.slug`
        ).all();
        return json(request, { ok: true, jobs: results || [] });
      }

      // 2026-08-14 加：每場面談實際花多少 token／多少錢——interview_daemon.py
      // 事後讀 claude CLI 自己寫的 session jsonl 記進 token_usage 表（見該檔案
      // log_token_usage() 的說明），這裡只負責彙總算錢。
      //
      // ⚠️ 費率是 Sonnet 5 目前的優惠價（$2/$10 每百萬 token，到 2026-08-31），
      // cache write 用 5 分鐘快取的 1.25 倍、cache read 用 0.1 倍計算——
      // 這是「目前定價」估算，不是財務對帳用的精確帳單金額。
      if (p === '/admin/token-usage' && request.method === 'GET') {
        const COST_SQL = `(
          input_tokens * 2.0/1000000
          + output_tokens * 10.0/1000000
          + cache_creation_input_tokens * 2.5/1000000
          + cache_read_input_tokens * 0.2/1000000
        )`;
        const byApp = await env.DB.prepare(
          `SELECT application_id,
                  GROUP_CONCAT(DISTINCT call_type) AS call_types,
                  SUM(input_tokens) AS input_tokens,
                  SUM(output_tokens) AS output_tokens,
                  SUM(cache_creation_input_tokens) AS cache_creation_input_tokens,
                  SUM(cache_read_input_tokens) AS cache_read_input_tokens,
                  SUM(${COST_SQL}) AS cost_usd,
                  MAX(created_at) AS last_at
             FROM token_usage
            GROUP BY application_id`
        ).all();
        const apps = byApp.results || [];
        const totalCost = apps.reduce((s, a) => s + (a.cost_usd || 0), 0);
        const totalInput = apps.reduce((s, a) => s + (a.input_tokens || 0), 0);
        const totalOutput = apps.reduce((s, a) => s + (a.output_tokens || 0), 0);
        const n = apps.length;

        const byType = await env.DB.prepare(
          `SELECT call_type,
                  COUNT(*) AS calls,
                  SUM(input_tokens) AS input_tokens,
                  SUM(output_tokens) AS output_tokens,
                  SUM(${COST_SQL}) AS cost_usd
             FROM token_usage
            GROUP BY call_type`
        ).all();

        // 最近花在哪。分三組（阿財面談／阿福健檢／其他工作事項）——
        // 這三件事的成本要分開看：面談是每個候選人的變動成本、健檢是另一條
        // 產品線、其他是一次性的維護工，混在一起算平均沒有意義。
        // 取 60 筆而不是 10 筆，因為分完組之後每一組才有東西可看。
        const recentIds = apps
          .sort((a, b) => String(b.last_at || '').localeCompare(String(a.last_at || '')))
          .slice(0, 60)
          .map((a) => a.application_id);
        let recent = [];
        if (recentIds.length) {
          const placeholders = recentIds.map(() => '?').join(',');
          const names = await env.DB.prepare(
            `SELECT id, name, job_title, job_slug FROM applications WHERE id IN (${placeholders})`
          ).bind(...recentIds).all();
          const nameById = Object.fromEntries((names.results || []).map((r) => [r.id, r]));
          // ⚠️ 2026-08-26 修：application_id 這一欄從一開始就被當成通用的
          // 「這筆花費屬於誰」鍵在用，除了真的候選人 UUID，還塞了
          // fix_copy:<職缺>、job:<職缺>、match:<職缺>、resume:<檔>…等等。
          // 原本一律 JOIN applications，對不上就印「（已刪除）」——
          // 畫面上滿滿的「已刪除」，看起來像資料被砍光，其實那些本來就
          // 不是候選人，一個人都沒被刪。改成照鍵的前綴講出它到底是什麼。
          const KIND = { fix_copy: '職缺文案修正', job: '建職缺題庫', match: '職缺配對',
                         resume: '履歷解析', sourcing_feedback: '主動開發回饋' };
          const label = (id) => {
            const nm = nameById[id];
            if (nm) return { name: nm.name, job_title: nm.job_title || nm.job_slug || '', is_candidate: true };
            const i = String(id).indexOf(':');
            if (i > 0) {
              const pre = String(id).slice(0, i), rest = String(id).slice(i + 1);
              return { name: KIND[pre] || pre, job_title: rest, is_candidate: false };
            }
            if (String(id).startsWith('zzz-selftest')) return { name: '系統自我檢測', job_title: '', is_candidate: false };
            // 剩下的才是真的對不上任何人選的 UUID（面談紀錄被清掉了）
            return { name: '已刪除的面談', job_title: '', is_candidate: false };
          };
          recent = recentIds.map((id) => {
            const a = apps.find((x) => x.application_id === id);
            const nm = label(id);
            // 分組看 call_type，不看鍵長什麼樣——call_type 是寫入端明確標的，
            // 鍵的格式歷來換過好幾種，拿鍵來猜會錯。
            const cts = String(a.call_types || '').split(',');
            const grp = cts.some((c) => c.startsWith('checkup_')) ? 'checkup'
              : (cts.some((c) => ['talk', 'report', 'report_json', 'prewarm'].includes(c)) ? 'interview' : 'ops');
            return {
              application_id: id, name: nm.name, is_candidate: nm.is_candidate,
              group: grp, call_types: cts.filter(Boolean),
              job_title: nm.job_title,
              cost_usd: a.cost_usd || 0,
              input_tokens: a.input_tokens || 0, output_tokens: a.output_tokens || 0,
              last_at: a.last_at,
            };
          });
        }

        return json(request, {
          ok: true,
          total_interviews: n,
          total_cost_usd: totalCost,
          avg_cost_usd: n ? totalCost / n : 0,
          avg_input_tokens: n ? Math.round(totalInput / n) : 0,
          avg_output_tokens: n ? Math.round(totalOutput / n) : 0,
          by_call_type: byType.results || [],
          recent,
        });
      }

      // 漏斗某一關裡面有誰。
      //
      // 為什麼要有：漏斗只給數字，看到「初審 3」還是不知道是誰卡住、卡多久。
      // 點下去要能直接看到人、開履歷、開報告、按處置——不然那個數字只是好看。
      if (p === '/admin/funnel' && request.method === 'GET') {
        const slug = url.searchParams.get('slug') || '';
        const stage = url.searchParams.get('stage') || 'applicants';
        const base = `SELECT a.id, a.name, a.email, a.phone, a.created_at,
                             a.expected_salary, a.available_date, a.location_ok,
                             a.interview_state, a.status, a.resume_file_id, a.resume_url,
                             a.handled_note,
                             CAST((julianday('now','+8 hours') - julianday(a.created_at)) AS INT) AS days_in,
                             (SELECT r.id FROM reports r WHERE r.application_id = a.id
                               ORDER BY r.created_at DESC LIMIT 1) AS report_id,
                             (SELECT r.consultant_decision FROM reports r WHERE r.application_id = a.id
                               ORDER BY r.created_at DESC LIMIT 1) AS decision,
                             (SELECT r.created_at FROM reports r WHERE r.application_id = a.id
                               ORDER BY r.created_at DESC LIMIT 1) AS report_at,
                             (SELECT p.stage FROM placements p WHERE p.application_id = a.id
                               ORDER BY p.updated_at DESC LIMIT 1) AS pipeline_stage,
                             (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id) AS msgs
                        FROM applications a
                       WHERE a.job_slug = ? AND a.superseded_by IS NULL`;
        let where = '';
        if (stage === 'screening') {
          where = ` AND a.interview_state IN ('done','paused')
                    AND (SELECT r.consultant_decision FROM reports r WHERE r.application_id = a.id
                          ORDER BY r.created_at DESC LIMIT 1) IS NULL`;
        } else if (stage === 'client_stage' || stage === 'offered' || stage === 'onboard' || stage === 'closed') {
          // ⚠️ 這幾組字彙必須跟上面 /admin/jobs 漏斗計數用的完全一致——
          // 不一致的話會出現「漏斗顯示 3 人、點進去只看到 1 人」這種對不起來的狀況。
          const st = stage === 'onboard'
            ? `p2.onboard_date IS NOT NULL`
            : (stage === 'offered'
                ? `UPPER(p2.stage) IN ('OFFER','OFFER_ACCEPTED','HIRED','PLACED') AND p2.onboard_date IS NULL`
                : (stage === 'closed'
                    ? `UPPER(p2.stage) IN ('CLOSED_LOST','CLOSED','REJECTED_BY_CLIENT','WITHDRAWN_BY_CANDIDATE')`
                    : `UPPER(p2.stage) IN ('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW','AWAITING_CLIENT_FEEDBACK','INTERVIEW_COMPLETED') AND p2.onboard_date IS NULL`));
          where = ` AND EXISTS (SELECT 1 FROM placements p2
                                 WHERE p2.application_id = a.id AND ${st})`;
        }
        const { results } = await env.DB.prepare(base + where + ' ORDER BY a.created_at ASC')
          .bind(slug).all();
        return json(request, { ok: true, stage, slug, candidates: results || [] });
      }

      // 職缺完整內容——給顧問「預覽模式」用（2026-08-25 加）。
      // 電洽人選時要能一邊看一邊講，不是只看得到職缺名稱跟核准/拒絕兩顆按鈕。
      // 回傳的是資料庫真實欄位，還沒過禁刊過濾器/AI 改寫——這是給顧問內部看的，
      // 不是拿去對外貼的版本。
      if (p.startsWith('/admin/jobs/') && p.endsWith('/full') && request.method === 'GET') {
        const slug = decodeURIComponent(p.slice('/admin/jobs/'.length, -'/full'.length));
        const job = await env.DB.prepare(`SELECT * FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        return json(request, { ok: true, job });
      }

      // ── 招募職缺頁的「查看／編輯 JD」彈窗 ──────────────────────────
      // JD 對外文案（標題、職缺介紹、工作內容、必要條件…）跟客戶在用人需求表
      // 填的內容（client_intro／hiring_manager／main_duties／required_conditions
      // 這些欄位）是兩件不同的事：前者是候選人在網站上看到的行銷文案，後者是
      // 客戶給的原始需求（拿來簡報跟給阿財篩選用）。這兩組欄位長期以來是分開的——
      // jd_spec_json 是新開的欄位，跟 jobs 表原本那批 client_* / main_duties 等
      // 用人需求表欄位完全不共用，改這裡不會動到客戶портal 填的資料，反之亦然。
      // 對外文案原本只活在發布當下產生的靜態頁 HTML 裡，發布之後就沒有任何
      // 資料庫紀錄跟得上——這裡把它接回 jobs.jd_spec_json，往後每次改動都留得住。
      const JD_SPEC_KEYS = ['title', 'subtitle', 'page_title', 'description', 'keywords',
        'og_title', 'og_desc', 'intro', 'tags', 'locations', 'locality', 'region',
        'must_skills', 'benefits', 'spec', 'duties', 'must', 'plus', 'why', 'faq',
        'industry', 'card_meta', 'card_desc', 'employment', 'salary_min', 'salary_max',
        'years_min', 'client_name'];

      // 職缺頁不是全部都是用標準樣板產生的——有些是更早手刻、或客製版面
      // （例如某些頁面多出「適合什麼樣的人」這種標準樣板完全沒有的區塊）。
      // 這裡先掃一次線上頁面的 <h2> 段落標題，跟標準樣板會產生的那組比對，
      // 多出來的就是「這頁重新產生會被砍掉」的內容——存檔前一定要讓顧問先看到。
      const JD_STANDARD_H2 = ['職缺條件一覽', '工作內容', '應徵條件', '為什麼選擇這個機會',
        '應徵流程', '立即應徵', '常見問題'];
      const jdStripTags = (s) => String(s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

      async function scrapeLiveJobPage(slug) {
        let html;
        try {
          const res = await fetch(`https://step1ne.com/jobs/${slug}/`, { cf: { cacheTtl: 0 } });
          if (!res.ok) return null;
          html = await res.text();
        } catch { return null; }

        const h2s = [...html.matchAll(/<h2>([^<]*)<\/h2>/g)].map((m) => m[1].trim());
        const extraSections = h2s.filter((h) => !JD_STANDARD_H2.includes(h));

        const spec = {};
        const h1m = html.match(/<h1[^>]*>([\s\S]*?)<span class="h1-sub">([\s\S]*?)<\/span><\/h1>/);
        if (h1m) { spec.title = jdStripTags(h1m[1]); spec.subtitle = jdStripTags(h1m[2]); }
        const introm = html.match(/<h1[\s\S]*?<\/h1>\s*<p[^>]*>([\s\S]*?)<\/p>/);
        if (introm) spec.intro = jdStripTags(introm[1]);
        const descm = html.match(/<meta name="description" content="([^"]*)"/);
        if (descm) spec.description = descm[1];

        const dlm = html.match(/<dl class="spec">([\s\S]*?)<\/dl>/);
        if (dlm) {
          const rows = [...dlm[1].matchAll(/<dt>([\s\S]*?)<\/dt>\s*<dd>([\s\S]*?)<\/dd>/g)]
            .map((m) => [jdStripTags(m[1]), jdStripTags(m[2])]);
          if (rows.length) spec.spec = rows;
        }

        const dutiesSecM = html.match(/<h2>工作內容<\/h2>([\s\S]*?)(<h2>|<\/section>)/);
        if (dutiesSecM) {
          const chunk = dutiesSecM[1];
          const groups = [...chunk.matchAll(/<h3>([\s\S]*?)<\/h3>\s*<ul class="duties">([\s\S]*?)<\/ul>/g)]
            .map((m) => ({ h: jdStripTags(m[1]), items: [...m[2].matchAll(/<li>([\s\S]*?)<\/li>/g)].map((x) => jdStripTags(x[1])) }));
          if (groups.length) {
            spec.duties = groups;
          } else {
            const flat = [...chunk.matchAll(/<li>([\s\S]*?)<\/li>/g)].map((x) => jdStripTags(x[1]));
            if (flat.length) spec.duties = [{ h: '', items: flat }];
          }
        }

        const reqSecM = html.match(/<h2>應徵條件<\/h2>([\s\S]*?)(<h2>|<\/section>)/);
        if (reqSecM) {
          const chunk = reqSecM[1];
          const mustM = chunk.match(/必要條件<\/div>\s*<ul class="duties">([\s\S]*?)<\/ul>/);
          if (mustM) spec.must = [...mustM[1].matchAll(/<li>([\s\S]*?)<\/li>/g)].map((x) => jdStripTags(x[1]));
          const plusM = chunk.match(/加分條件<\/div>\s*<ul class="duties">([\s\S]*?)<\/ul>/);
          if (plusM) spec.plus = [...plusM[1].matchAll(/<li>([\s\S]*?)<\/li>/g)].map((x) => jdStripTags(x[1]));
        }

        const whySecM = html.match(/<h2>為什麼選擇這個機會<\/h2>([\s\S]*?)(<h2>|<\/section>)/);
        if (whySecM) {
          const items = [...whySecM[1].matchAll(/<h3[^>]*>([\s\S]*?)<\/h3>\s*<p[^>]*>([\s\S]*?)<\/p>/g)]
            .map((m) => ({ h: jdStripTags(m[1]), p: jdStripTags(m[2]) }));
          if (items.length) spec.why = items;
        }

        const faqSecM = html.match(/<h2>常見問題<\/h2>([\s\S]*?)(<h2>|<\/section>)/);
        if (faqSecM) {
          const pairs = [...faqSecM[1].matchAll(/<span>([\s\S]*?)<\/span>[\s\S]*?<p[^>]*>([\s\S]*?)<\/p>/g)]
            .map((m) => [jdStripTags(m[1]), jdStripTags(m[2])]);
          if (pairs.length) spec.faq = pairs;
        }

        return { spec, extraSections, mismatched: extraSections.length > 0 };
      }


      if (p.startsWith('/admin/jobs/') && p.endsWith('/content') && request.method === 'GET') {
        const slug = decodeURIComponent(p.slice('/admin/jobs/'.length, -'/content'.length));
        const job = await env.DB.prepare(
          `SELECT slug, title, jd_spec_json, jd_regen_pending, jd_updated_at, jd_updated_by,
                  jd_regen_last_at, jd_regen_last_error, jd_ai_request, jd_ai_requested_at,
                  jd_ai_requested_by FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        let spec = null, source = 'none';
        if (job.jd_spec_json) {
          try { spec = JSON.parse(job.jd_spec_json); source = 'saved'; } catch { spec = null; }
        }
        if (!spec) {
          // 還沒存過——找發布這個職缺時的原始收件單當底稿，讓顧問不用從零打字。
          const intake = await env.DB.prepare(
            `SELECT draft_json FROM job_intakes WHERE published_slug = ?
              ORDER BY updated_at DESC LIMIT 1`).bind(slug).first();
          if (intake && intake.draft_json) {
            try { spec = JSON.parse(intake.draft_json); source = 'backfilled_from_intake'; } catch { spec = null; }
          }
        }
        let mismatched = false, extraSections = [];
        if (!spec) {
          // 收件單也沒有——這批多半是比 job_intakes 流程更早、或用別的方式
          // 上架的職缺（人工建立、Telegram 舊流程）。直接掃線上頁面湊一份底稿，
          // 顧問至少看得到現在網站上寫什麼，不用面對一片空白重打一次。
          const scraped = await scrapeLiveJobPage(slug);
          if (scraped && Object.keys(scraped.spec).length) {
            spec = scraped.spec; source = 'scraped_live_page';
            mismatched = scraped.mismatched; extraSections = scraped.extraSections;
          }
        }
        if (!spec) { spec = { slug, title: job.title }; source = 'none'; }
        return json(request, {
          ok: true, source, spec, mismatched, extraSections,
          pending: !!job.jd_regen_pending,
          updated_at: job.jd_updated_at, updated_by: job.jd_updated_by,
          regen_last_at: job.jd_regen_last_at, regen_last_error: job.jd_regen_last_error,
          ai_request: job.jd_ai_request, ai_requested_at: job.jd_ai_requested_at,
          ai_requested_by: job.jd_ai_requested_by,
        });
      }

      // 手動改欄位——存了就直接標成待套用，本機背景腳本會撿去重新產生靜態頁
      // 並自動 git push（顧問已經確認要這個行為：改完不用再多按一次「上線」）。
      if (p.startsWith('/admin/jobs/') && p.endsWith('/content') && request.method === 'POST') {
        const slug = decodeURIComponent(p.slice('/admin/jobs/'.length, -'/content'.length));
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const job = await env.DB.prepare(`SELECT jd_spec_json FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        let spec = {}, hadSaved = !!job.jd_spec_json;
        if (job.jd_spec_json) { try { spec = JSON.parse(job.jd_spec_json) || {}; } catch { spec = {}; } }
        if (!Object.keys(spec).length) {
          const intake = await env.DB.prepare(
            `SELECT draft_json FROM job_intakes WHERE published_slug = ?
              ORDER BY updated_at DESC LIMIT 1`).bind(slug).first();
          if (intake && intake.draft_json) { try { spec = JSON.parse(intake.draft_json) || {}; } catch { spec = {}; } }
        }
        // 第一次存檔（之前從沒存過、也沒有收件單底稿）才需要檢查——這種職缺
        // 唯一的內容來源是線上頁面本身，重新產生前一定要確認頁面不是客製版面，
        // 不然「改個薪資」會把手刻的區塊整段砍掉，而且不會有任何警告。
        if (!hadSaved && !Object.keys(spec).length) {
          const scraped = await scrapeLiveJobPage(slug);
          if (scraped && scraped.mismatched && !b.confirm_mismatch) {
            return json(request, { ok: false, error: 'template_mismatch',
              mismatched: true, extraSections: scraped.extraSections,
              message: `這個職缺頁不是用標準樣板做的，多了：${scraped.extraSections.join('、')}。存檔會用標準樣板整頁重新產生，這些內容會不見。確定要繼續請再送一次並帶 confirm_mismatch。` }, 409);
          }
        }
        const fields = b.fields || {};
        const changed = [];
        for (const k of JD_SPEC_KEYS) {
          if (!(k in fields)) continue;
          if (JSON.stringify(spec[k] ?? null) === JSON.stringify(fields[k] ?? null)) continue;
          spec[k] = fields[k]; changed.push(k);
        }
        if (!changed.length) return json(request, { ok: false, error: '沒有欄位變更' }, 400);
        spec.slug = slug;
        // 未簽約客戶的匿名規則對這裡一樣適用——顧問手改文案也可能不小心打進客戶全名。
        const terms = await clientNameTerms(env);
        const blob = JSON.stringify(fields);
        const nameHits = hitsClientNames(blob, terms);
        if (nameHits.length) {
          return json(request, { ok: false,
            error: `文案裡出現了客戶名稱（${nameHits.join('、')}），這個客戶對外要匿名，請改掉再存` }, 400);
        }
        const now = nowTaipei();
        await env.DB.prepare(
          `UPDATE jobs SET jd_spec_json=?, jd_regen_pending=1, jd_updated_at=?, jd_updated_by=?
            WHERE slug=?`
        ).bind(JSON.stringify(spec), now, b.by || null, slug).run();
        await notify(env,
          `✏️ <b>JD 更新</b>　${spec.title || slug}\n改了：${changed.join('、')}\n`
          + `由 ${b.by || '顧問'} 從招募職缺頁直接修改，本機腳本最多 15 分鐘內會重新產生頁面並自動上線。`,
          { message_thread_id: THREAD.system });
        return json(request, { ok: true, changed, pending: true });
      }

      // 交給 AI 重擬——只記錄請求，不自動套用。AI 寫出來的東西沒人看過就直接
      // 上正式站風險太高，這條路一定要人確認過才會進到上面那支「存檔」端點。
      if (p.startsWith('/admin/jobs/') && p.endsWith('/ai-request') && request.method === 'POST') {
        const slug = decodeURIComponent(p.slice('/admin/jobs/'.length, -'/ai-request'.length));
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const note = String(b.note || '').trim();
        if (!note) return json(request, { ok: false, error: '要交給 AI 重擬，請先寫一句話說要改什麼' }, 400);
        const job = await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(slug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        const now = nowTaipei();
        await env.DB.prepare(
          `UPDATE jobs SET jd_ai_request=?, jd_ai_requested_at=?, jd_ai_requested_by=? WHERE slug=?`
        ).bind(note, now, b.by || null, slug).run();
        await notify(env,
          `🤖 <b>顧問要求重擬 JD</b>　${job.title || slug}\n`
          + `${b.by || '顧問'}：${note}\n`
          + `這則不會自動套用，需要有人看過重擬結果、確認沒問題再存檔上線。`,
          { message_thread_id: THREAD.system });
        return json(request, { ok: true });
      }

      // 更新分類。⚠️ 客戶對象一改，三個連動欄位要一起改，
      // 不能讓顧問一個一個設——漏設一個就是隱私外洩。
      // ── 人選客製表單：職缺底下的表單範本管理 ──
      // 2026-09-02 加。job_forms 掛在職缺（job_slug）底下，一個職缺可以掛多份。
      // 只是「上傳、列出、刪除」，真正觸發寄送是在 triggerJobForms()（推薦給
      // 客戶那三個地方共用），不是這裡。
      if (p === '/admin/job-forms' && request.method === 'GET') {
        const jobSlug = (url.searchParams.get('job_slug') || '').trim();
        if (!jobSlug) return json(request, { ok: false, error: '缺 job_slug' }, 400);
        const { results } = await env.DB.prepare(
          `SELECT id, name, template_file_id, created_by, created_at FROM job_forms
            WHERE job_slug=? ORDER BY created_at DESC`
        ).bind(jobSlug).all();
        return json(request, { ok: true, forms: results || [] });
      }
      if (p === '/admin/job-forms' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const jobSlug = String(b.job_slug || '').trim();
        const name = String(b.name || '').trim();
        if (!jobSlug) return json(request, { ok: false, error: '缺 job_slug' }, 400);
        if (!name) return json(request, { ok: false, error: '請填表單名稱' }, 400);
        const job = await env.DB.prepare(`SELECT slug FROM jobs WHERE slug=?`).bind(jobSlug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        let templateFileId = null;
        if (b.file_b64) {
          const saved = await saveUpload(env, { b64: b.file_b64, name: b.file_name, mime: b.file_mime }, nowTaipei());
          if (saved && saved.tooBig) return json(request, { ok: false, error: '檔案太大，請壓縮後再上傳' }, 400);
          if (saved) templateFileId = saved.fileId;
        }
        const id = uid();
        await env.DB.prepare(
          `INSERT INTO job_forms (id, job_slug, name, template_file_id, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?)`
        ).bind(id, jobSlug, name, templateFileId, b.by || null, nowTaipei()).run();
        return json(request, { ok: true, id });
      }
      if (p.startsWith('/admin/job-forms/') && p.endsWith('/delete') && request.method === 'POST') {
        const id = decodeURIComponent(p.slice('/admin/job-forms/'.length, -'/delete'.length));
        await env.DB.prepare(`DELETE FROM job_forms WHERE id=?`).bind(id).run();
        return json(request, { ok: true });
      }

      // 人選填寫紀錄——顧問卡片上「重新寄送」用。涵蓋兩種情境：①原本沒有
      // 真的 email（佔位信箱），顧問補上 email 後手動觸發第一次寄送；
      // ②信寄過但人選說沒收到／連結搞丟，重新寄一次同一個連結。已經填完的
      // （status='submitted'）不給重寄，避免搞混「這是舊的還是新的填寫」。
      if (p.startsWith('/admin/candidate-form-submissions/') && p.endsWith('/resend') && request.method === 'POST') {
        const id = decodeURIComponent(p.slice('/admin/candidate-form-submissions/'.length, -'/resend'.length));
        const sub = await env.DB.prepare(
          `SELECT cfs.*, jf.name AS form_name, a.name AS candidate_name, a.email, cc.display_name AS company_name, j.title AS job_title
             FROM candidate_form_submissions cfs
             JOIN job_forms jf ON jf.id = cfs.job_form_id
             JOIN applications a ON a.id = cfs.application_id
             LEFT JOIN client_companies cc ON cc.id = cfs.company_id
             LEFT JOIN jobs j ON j.slug = jf.job_slug
            WHERE cfs.id=?`
        ).bind(id).first();
        if (!sub) return json(request, { ok: false, error: '找不到這筆填寫紀錄' }, 404);
        if (sub.status === 'submitted') return json(request, { ok: false, error: '這位人選已經填完了，不能重寄' }, 400);
        const hasRealEmail = !!(sub.email && !/@no-email\.step1ne\.local$/i.test(String(sub.email).trim()));
        if (!hasRealEmail) return json(request, { ok: false, error: '這位人選還沒有可用的 Email，請先在人選卡片補上' }, 400);
        const now = nowTaipei();
        await env.DB.prepare(`UPDATE candidate_form_submissions SET status='sent', sent_at=? WHERE id=?`)
          .bind(now, id).run();
        const link = `https://step1ne.com/form/?t=${sub.token}`;
        const sent = await sendMail(env, sub.email,
          `請填寫「${sub.form_name}」－ ${sub.job_title || ''}`,
          [
            `${sub.candidate_name || '您好'}：`,
            `再次提醒您，麻煩點下方按鈕填寫「${sub.form_name}」（${sub.company_name || '用人單位'}），請於 2 天內完成上傳，謝謝配合！`,
          ],
          { url: link, text: `填寫${sub.form_name}` }
        );
        return json(request, { ok: sent, error: sent ? undefined : '寄送失敗，可能是 RESEND_API_KEY 沒設或信箱格式問題' });
      }

      // 顧問下載人選填完的表單——跟 /admin/resume/ 同一套做法。
      if (p.startsWith('/admin/candidate-form-submissions/') && p.endsWith('/download') && request.method === 'GET') {
        const id = decodeURIComponent(p.slice('/admin/candidate-form-submissions/'.length, -'/download'.length));
        const sub = await env.DB.prepare(
          `SELECT submitted_file_id FROM candidate_form_submissions WHERE id=?`).bind(id).first();
        if (!sub || !sub.submitted_file_id) return json(request, { ok: false, error: '還沒有填寫完成的檔案' }, 404);
        const file = await fileB64(env, sub.submitted_file_id);
        if (!file) return json(request, { ok: false, error: '找不到檔案' }, 404);
        return new Response(Uint8Array.from(atob(file.content), (c) => c.charCodeAt(0)), {
          headers: {
            'content-type': file.mime || 'application/octet-stream',
            'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(file.filename || 'form')}`,
            ...cors(request),
          },
        });
      }

      if (p.startsWith('/admin/jobs/') && request.method === 'POST') {
        const slug = decodeURIComponent(p.slice('/admin/jobs/'.length));
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const LINES = ['dispatch', 'direct', 'executive'];
        const RELATIONS = {
          // client_relation → [client_named, ai_disclosure]
          signed:   [1, 'always'],   // 已簽約：具名、可揭露 AI、掛 Step1ne 品牌
          unsigned: [0, 'never'],    // 未簽約：匿名
          private:  [1, 'never'],    // 朋友私人協助：具名，但報告完全不掛品牌、不提 AI
        };
        const sets = [], bind = [];
        if (b.service_line) {
          if (!LINES.includes(b.service_line)) return json(request, { ok: false, error: '服務線不合法' }, 400);
          sets.push('service_line = ?'); bind.push(b.service_line);
        }
        if (b.client_relation) {
          const rel = RELATIONS[b.client_relation];
          if (!rel) return json(request, { ok: false, error: '客戶對象不合法' }, 400);
          sets.push('client_relation = ?', 'client_named = ?', 'ai_disclosure = ?');
          bind.push(b.client_relation, rel[0], rel[1]);
        }
        // 職級跟服務線是兩件事。派遣也會有中高階（派遣的廠長、專案總監），
        // 而阿財的面談長度是看職級決定的——設錯就會用基層流程去面中高階人選。
        if (b.seniority) {
          if (!['junior', 'mid', 'senior'].includes(b.seniority)) {
            return json(request, { ok: false, error: '職級請選：基層／一般／中高階' }, 400);
          }
          sets.push('seniority = ?'); bind.push(b.seniority);
        }
        if (b.client_code !== undefined) { sets.push('client_code = ?'); bind.push(b.client_code || null); }
        // 2026-08-13 加：外語驗證原本靠阿財自己從 must_skills 長文字裡判斷要不要做——
        // 徐振倫那場（must_skills 明寫日文 N1/N2）整場沒做，就是這個判斷被漏掉。
        // 改成跟 seniority 同一個做法：顧問在後台明講，阿財不用自己猜，
        // 面談引擎也能用這個權威欄位決定要不要送外語驗證那一整章規則。
        // 留空＝這個職缺不需要外語驗證；填了就是候選人面談時要驗證的語言（例：日文）。
        if (b.interview_language !== undefined) {
          const lang = String(b.interview_language || '').trim().slice(0, 20);
          sets.push('interview_language = ?'); bind.push(lang || null);
        }
        // 2026-08-13 加：報到前準備事項——到職關懷排程（candidateCareTick）會用這個
        // 內容推給候選人，同企業同職缺通常可以沿用，所以掛在 jobs 而不是每次的 placement。
        if (b.onboarding_prep_note !== undefined) {
          const note = String(b.onboarding_prep_note || '').trim().slice(0, 1000);
          sets.push('onboarding_prep_note = ?'); bind.push(note || null);
        }
        // 2026-08-17 加：動態開關職缺——找到人了先關掉，不影響已經在談的人
        // （只擋「新」應徵，/apply 那邊會擋），隨時可以再打開，不用改網站檔案。
        // 2026-08-25 改用 setJobOpenState() 共用函式（跟客戶 portal 的「結束招募／
        // 重新招募」共用），順便記 closed_by='consultant'，讓客戶 portal 那邊能判斷
        // 「這個職缺是顧問自己關的，不能自己重開」。
        let statusChanged = false;
        if (b.status !== undefined) {
          if (!['open', 'closed'].includes(b.status)) {
            return json(request, { ok: false, error: '狀態只能是 open 或 closed' }, 400);
          }
          statusChanged = true;
        }
        if (!sets.length && !statusChanged) return json(request, { ok: false, error: '沒有要更新的欄位' }, 400);

        if (sets.length) {
          await env.DB.prepare(`UPDATE jobs SET ${sets.join(', ')} WHERE slug = ?`)
            .bind(...bind, slug).run();
        }
        if (statusChanged) {
          await setJobOpenState(env, { slug, status: b.status, by: 'consultant', reason: null });
        }
        const row = await env.DB.prepare(
          `SELECT slug, service_line, client_relation, seniority, client_named, ai_disclosure, client_code, interview_language, onboarding_prep_note, status, closed_by, closed_reason
             FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!row) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        return json(request, { ok: true, job: row });
      }

      // 2026-08-14 加：顧問在「職缺分類」後台按「產生社群貼文」——不是等
      // social_post_agent.py 每天早上排程掃到，是本人指定「這個職缺現在就要」。
      // 這裡只負責把狀態清成 NULL，讓下一輪掃描（launchd 現在改成每 15 分鐘
      // 跑一次，不是原本的一天一次）撿得到；真正呼叫 claude 產草稿的還是本機
      // 那支腳本，Worker 這裡不能也不需要碰 claude CLI。
      if (p === '/admin/social-post-request' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.slug || '');
        const job = await env.DB.prepare(`SELECT slug, title FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        // 2026-08-14 加：選帳號——多顧問各自的發文帳號存在 social_accounts，
        // 選了就記在這個職缺上，soc_approve 那時候會照這個 id 去查對應的
        // access_token／platform_user_id，不是永遠用同一組寫死的金鑰。
        let accountId = null;
        if (b.account_id) {
          const acc = await env.DB.prepare(`SELECT id FROM social_accounts WHERE id = ? AND is_active = 1`).bind(b.account_id).first();
          if (!acc) return json(request, { ok: false, error: '找不到這個發文帳號' }, 404);
          accountId = acc.id;
        }
        // 2026-08-18 改：同一個職缺要能讓不同顧問各自排一篇，不能再覆蓋寫在
        // jobs 表的單一欄位上（真實案例撞到：後排的帳號直接蓋掉前一個人排
        // 的，結果四個顧問想各自發一篇同一職缺，只有最後排的那個人發得出來）。
        // 改成每次排入都是 social_post_queue 裡新的一筆，互不影響。
        const ins = await env.DB.prepare(
          `INSERT INTO social_post_queue (job_slug, account_id, requested_at) VALUES (?, ?, datetime('now','+8 hours'))`
        ).bind(slug, accountId).run();
        return json(request, { ok: true, queue_id: ins.meta.last_row_id });
      }

      // 給「一鍵發文」頁面的帳號下拉選單用——只回標籤跟平台，絕對不回
      // access_token，那顆是後端用的，不該出現在任何前端回應裡。
      // ⚠️ 2026-09-01 改：原本只回 is_active=1 的帳號——新加的顧問（還沒去
      // 開發者後台申請 access_token）在下拉選單裡完全不存在，顧問社群看不到
      // 「這個人已經排進來但還不能發」，還以為系統忘了加。改成全部回傳＋帶
      // is_active，前端自己決定要不要讓選、要不要標「尚未設定」。
      if (p === '/admin/social-accounts' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, label, platform, is_active FROM social_accounts ORDER BY is_active DESC, id`
        ).all();
        return json(request, { ok: true, accounts: results || [] });
      }

      // 2026-08-17 加：「一鍵發文」頁面的動態排隊列表——顧問按了排入產稿之後
      // 完全看不到後續進度，要嘛跑去 Telegram 找、要嘛乾等，這裡回傳目前
      // 「有經過這條流程」的職缺（排入中／草稿等審核／已發布／不發），
      // 前端定時輪詢就能看到狀態變化，不用每次都跑去問。
      // LINE 官方帳號的整體數字，給「顧問社群 → 成效儀表板」用。
      // 2026-08-19 加。⚠️ 這裡只有得到「總量」：LINE 的 Insight API 沒有
      // 「哪個加入連結帶來幾個人」的端點——四位顧問的 lin.ee 連結雖然各自不同
      // （差在 oat__id），但那個維度只有 LINE 官方後台看得到，API 不給。
      // 所以分顧問的歸因還是得靠自家轉址，這支不要假裝做得到。
      if (p === '/admin/line-insight' && request.method === 'GET') {
        if (!env.LINE_CHANNEL_ACCESS_TOKEN) {
          return json(request, { ok: false, error: 'LINE 金鑰未設定' }, 503);
        }
        // LINE 的統計要隔天才結算，抓前天的最穩（抓今天多半回 status: unready）。
        // ⚠️ 2026-09-02 加 days_ago：followers/blocks 是「累積總數」（LINE 官方
        // 文件定義，從帳號建立以來的總量，只會增不會減），不是當天新增量。
        // 顧問想看「最近一週加了幾人、被封鎖幾人」，只要抓兩個時間點的累積數
        // 相減就是那段期間的真實新增量——這支端點本來就已經是照 date 查單一
        // 快照，直接讓呼叫端指定 days_ago 就能兩次呼叫拼出趨勢，不用另開端點。
        const daysAgo = Math.max(2, Math.min(90, parseInt(url.searchParams.get('days_ago'), 10) || 2));
        const d = new Date(Date.now() - daysAgo * 86400000);
        const ymd = d.toISOString().slice(0, 10).replace(/-/g, '');
        try {
          const r = await fetch(`https://api.line.me/v2/bot/insight/followers?date=${ymd}`,
            { headers: { authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` } });
          const f = await r.json();
          if (f.status !== 'ready') {
            return json(request, { ok: true, ready: false, note: `LINE 統計尚未結算（${ymd}）` });
          }
          return json(request, {
            ok: true, ready: true, date: ymd,
            followers: f.followers, reachable: f.targetedReaches, blocks: f.blocks,
          });
        } catch (e) {
          return json(request, { ok: false, error: String(e).slice(0, 120) }, 502);
        }
      }

      // 職缺專業題庫（顧問後台看得到阿財會問什麼）。
      // 2026-08-19 加。題庫由 build_expertise.py 事先產好，顧問原本只能從
      // 面談逐字稿反推阿財問了什麼——看不到題庫本身，就沒辦法判斷該不該調整。
      // 顧問改題庫。2026-08-19 加。
      // ⚠️ 第一次編輯時把「機器原本產的版本」另存一份（original_json）。
      // 顧問改壞了要能救回來——題庫是花了幾分鐘上網查才產出來的，
      // 改錯一次就要整份重跑，那個成本會讓人不敢改，等於功能白做。
      if (p === '/admin/expertise/save' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.job_slug || '').trim();
        const qs = Array.isArray(b.questions) ? b.questions : null;
        if (!slug || !qs) return json(request, { ok: false, error: '缺少職缺或題目' }, 400);
        if (qs.length > 40) return json(request, { ok: false, error: '題目最多 40 題' }, 400);

        const cur = await env.DB.prepare(
          `SELECT questions_json, original_json FROM job_expertise WHERE job_slug = ?`
        ).bind(slug).first();
        if (!cur) return json(request, { ok: false, error: '找不到這個職缺的題庫' }, 404);

        // 清洗：只留規格內的欄位，字串長度設上限，空題目直接丟掉
        const clean = qs.map((q) => ({
          q: String(q.q || '').slice(0, 600),
          topic: String(q.topic || '').slice(0, 80),
          why: String(q.why || '').slice(0, 300),
          kind: ['經驗', '情境', '技術', '外語'].includes(q.kind) ? q.kind : '經驗',
          good_signs: (Array.isArray(q.good_signs) ? q.good_signs : [])
            .map((x) => String(x).slice(0, 200)).filter(Boolean).slice(0, 6),
          red_flags: (Array.isArray(q.red_flags) ? q.red_flags : [])
            .map((x) => String(x).slice(0, 200)).filter(Boolean).slice(0, 6),
          followup: String(q.followup || '').slice(0, 300),
        })).filter((q) => q.q.trim());
        if (!clean.length) return json(request, { ok: false, error: '至少要留一題' }, 400);

        const who = String(b.editor || '').slice(0, 40) || '顧問';
        await env.DB.prepare(
          `UPDATE job_expertise
              SET questions_json = ?,
                  original_json = COALESCE(original_json, ?),
                  edited_by = ?, edited_at = datetime('now','+8 hours')
            WHERE job_slug = ?`
        ).bind(JSON.stringify(clean), cur.questions_json, who, slug).run();

        await notify(env, `✏️ ${who} 改了「${slug}」的面談題庫（現在 ${clean.length} 題）\n`
          + `阿財下一場該職缺的面談就會用新的題目。`, { message_thread_id: THREAD.system }).catch(() => {});
        return json(request, { ok: true, n: clean.length });
      }

      // 還原成機器原本產的版本
      if (p === '/admin/expertise/restore' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const slug = String(b.job_slug || '').trim();
        const row = await env.DB.prepare(
          `SELECT original_json FROM job_expertise WHERE job_slug = ?`
        ).bind(slug).first();
        if (!row || !row.original_json) {
          return json(request, { ok: false, error: '這份題庫沒有被改過，沒有可還原的版本' }, 400);
        }
        await env.DB.prepare(
          `UPDATE job_expertise SET questions_json = original_json, original_json = NULL,
                  edited_by = NULL, edited_at = NULL WHERE job_slug = ?`
        ).bind(slug).run();
        return json(request, { ok: true });
      }

      // 招募總覽。2026-08-19 加（Jacky：這一頁要拿來做 KPI 復盤）。
      // 三層，由上而下回答三個問題：現在手上有多少事、漏斗卡在哪、阿財準不準。
      // ⚠️ 第一層刻意放「等你處理」而不是漂亮的總數——儀表板要能催事，
      //    不然它就只是好看。
      // ── 主動開發：爬蟲把人選送進來 ──
      // 2026-08-19 加。爬蟲原本把人存 Google Sheets，跟這套系統完全斷開——
      // 爬到人也進不了阿財的面談流程，等於撈到了也用不到。
      //
      // 🚨 為什麼**不能**直接寫進 applications：
      //    爬到的人不是應徵者。他沒有投履歷、沒有同意個資利用、也沒有表達過
      //    任何意願。混進 applications 會有兩個後果：漏斗統計整個失真
      //    （分母灌水），以及把「我們單方面蒐集的公開資料」跟「他主動提供
      //    給我們的資料」混為一談——後者在個資法上是完全不同的處理基礎。
      //    所以另開 sourced_candidates，等他回覆有意願、同意個資之後，
      //    才由顧問轉成正式應徵者。
      if (p === '/admin/sourced/import' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const rows = Array.isArray(b.candidates) ? b.candidates : null;
        if (!rows) return json(request, { ok: false, error: '缺少 candidates' }, 400);
        if (rows.length > 200) return json(request, { ok: false, error: '單次最多 200 筆' }, 400);

        let added = 0, dup = 0;
        for (const c of rows) {
          const url = String(c.source_url || c.linkedin_url || c.github_url || '').slice(0, 500);
          const src = String(c.source || 'unknown').slice(0, 40);
          if (!url) continue;   // 沒有來源網址就無法去重，也無法回溯，直接跳過
          try {
            const r = await env.DB.prepare(
              `INSERT INTO sourced_candidates
                 (id, created_at, source, source_url, name, headline, company, location,
                  email, github_url, linkedin_url, skills, bio, raw_json, job_slug,
                  score, grade, status, task_id)
               VALUES (?, datetime('now','+8 hours'), ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?)`
            ).bind(
              uid(), src, url,
              String(c.name || '').slice(0, 80),
              String(c.title || c.headline || '').slice(0, 200),
              String(c.company || '').slice(0, 120),
              String(c.location || '').slice(0, 120),
              String(c.email || '').slice(0, 120),
              String(c.github_url || '').slice(0, 300),
              String(c.linkedin_url || '').slice(0, 300),
              Array.isArray(c.skills) ? c.skills.join(', ').slice(0, 500) : String(c.skills || '').slice(0, 500),
              String(c.bio || '').slice(0, 1500),
              JSON.stringify(c).slice(0, 12000),
              String(c.job_slug || '').slice(0, 80) || null,
              Number.isFinite(Number(c.score)) ? Math.round(Number(c.score)) : null,
              String(c.grade || '').slice(0, 4) || null,
              String(c.task_id || '').slice(0, 80) || null
            ).run();
            if (r.success) added++;
          } catch (e) {
            // UNIQUE(source, source_url) 撞到＝這個人已經在池子裡了，不是錯誤
            if (String(e).includes('UNIQUE')) dup++; else throw e;
          }
        }
        if (added) {
          await notify(env, `🔍 主動開發：新增 ${added} 位人選進人才池`
            + (dup ? `（${dup} 位已經在池子裡）` : '')
            + (b.job_slug ? `\n職缺：${b.job_slug}` : '')
            + `\n\n這些人**還不是應徵者**——他們還沒被接觸、也還沒同意個資利用。`
            + `\n到後台挑人：https://step1ne.com/consultant/sourced/`,
            { message_thread_id: THREAD.sourced }).catch(() => {});
        }
        return json(request, { ok: true, added, dup });
      }

      // 人才池列表
      if (p === '/admin/sourced' && request.method === 'GET') {
        // 2026-08-21 加搜尋與分類。舊人才庫匯進來之後池子有 3,456 人，
        // 原本只有一條照分數排的長列表、最多 200 筆——顧問要找「會 Android 的」
        // 只能一頁一頁翻，那等於沒有人才庫。
        const st = url.searchParams.get('status') || 'new';
        const q = (url.searchParams.get('q') || '').trim().slice(0, 60);
        const cat = (url.searchParams.get('cat') || '').trim().slice(0, 20);
        // 2026-08-26 修：原本截 20 字元，來源字串一長（例如
        // talent-intelligence-sourcing-v15-manual，40字）就被砍斷，
        // 跟資料庫存的完整值比對永遠不相等，篩選結果永遠是 0 筆。
        const src = (url.searchParams.get('source') || '').trim().slice(0, 80);
        const job = (url.searchParams.get('job') || '').trim().slice(0, 80);
        // 2026-08-27 加 grade 與 rc 篩選：
        // grade＝Fit 等第 A/B/C/D；rc＝招募分類（DIRECT_TARGET 等）。
        // 兩者是規範第六步明訂要分開的東西，篩選也該分開。
        // 實際痛點：REFERRAL_ONLY 那種只拿來當擴張節點的經理級人物
        // （例：駱素蓮／擎邦資深經理／D 級）跟真候選人混在同一份清單裡，
        // 顧問每次翻都要重新判斷一次。
        const grd = (url.searchParams.get('grade') || '').trim().slice(0, 4);
        const rc = (url.searchParams.get('rc') || '').trim().slice(0, 40);
        const has = (url.searchParams.get('has') || '').trim();   // email / linkedin / github
        const page = Math.max(0, parseInt(url.searchParams.get('page') || '0', 10) || 0);
        const SIZE = 60;

        // 2026-08-27 改寫成 faceted search：
        // 原本每個下拉的數字都是全表 GROUP BY，選了職缺之後其他下拉還是顯示
        // 全部 3,556 人的統計，顧問看到的數字跟眼前的清單對不起來。
        // 現在每個維度的統計都套用「其他所有已選條件，但排除自己」——
        // 這樣「所有職類」底下的數字才是「這個職缺裡各職類有幾人」。
        const conds = [];
        conds.push({ k: 'status', sql: `(? = 'all' OR status = ?)`, b: [st, st] });
        if (q) {
          const like = '%' + q + '%';
          conds.push({ k: 'q',
            sql: `(name LIKE ? OR headline LIKE ? OR company LIKE ? OR skills LIKE ? OR bio LIKE ?)`,
            b: [like, like, like, like, like] });
        }
        if (cat) conds.push({ k: 'cat', sql: `category = ?`, b: [cat] });
        if (src) conds.push({ k: 'src', sql: `source = ?`, b: [src] });
        if (job) conds.push({ k: 'job', sql: `job_slug = ?`, b: [job] });
        if (grd) conds.push({ k: 'grade', sql: `grade = ?`, b: [grd] });
        if (rc)  conds.push({ k: 'rc', sql: `recruitability_class = ?`, b: [rc] });
        if (has === 'email')    conds.push({ k: 'has', sql: `COALESCE(email,'') <> ''`, b: [] });
        if (has === 'linkedin') conds.push({ k: 'has', sql: `COALESCE(linkedin_url,'') <> ''`, b: [] });
        if (has === 'github')   conds.push({ k: 'has', sql: `COALESCE(github_url,'') <> ''`, b: [] });

        // except=null 代表全部套用；給某個 key 就排除該維度自己的條件
        const build = (except) => {
          const use = conds.filter((c) => c.k !== except);
          return { W: use.map((c) => c.sql).join(' AND ') || '1=1',
                   B: use.flatMap((c) => c.b) };
        };
        const { W, B: bind } = build(null);

        const { results } = await env.DB.prepare(
          `SELECT id, created_at, source, source_url, name, headline, company, location,
                  email, phone, github_url, linkedin_url, skills, job_slug, status, category, cat_src,
                  grade, recruitability_class, score,
                  contacted_at, converted_application_id, note, reject_reason, owner,
                  (SELECT display_name FROM consultants c WHERE c.id = sourced_candidates.owner) AS owner_name
             FROM sourced_candidates
            WHERE ${W}
            ORDER BY (COALESCE(email,'') <> '') DESC, (COALESCE(headline,'') <> '') DESC,
                     created_at DESC
            LIMIT ${SIZE} OFFSET ${page * SIZE}`
        ).bind(...bind).all();
        const tot = await env.DB.prepare(
          `SELECT COUNT(*) n FROM sourced_candidates WHERE ${W}`).bind(...bind).first();
        const counts = await env.DB.prepare(
          `SELECT status, COUNT(*) n FROM sourced_candidates GROUP BY status`).all();
        // 每個維度的選項數字，都套用「其他已選條件、排除自己」
        const facet = async (key, expr, extraWhere, order) => {
          const f = build(key);
          const w = [f.W].concat(extraWhere ? [extraWhere] : []).join(' AND ');
          const { results: rs } = await env.DB.prepare(
            `SELECT ${expr} k, COUNT(*) n FROM sourced_candidates
              WHERE ${w} GROUP BY k ORDER BY ${order || 'n DESC'}`).bind(...f.B).all();
          return rs || [];
        };
        // 給前端做職缺下拉選單用：只列真的有人選的職缺，附上職缺標題
        const gradesAgg = { results: await facet('grade', `grade`, `COALESCE(grade,'') <> ''`, 'k') };
        const rcsAgg = { results: await facet('rc', `recruitability_class`,
                                              `COALESCE(recruitability_class,'') <> ''`) };
        const fJob = build('job');
        const { results: jobRows } = await env.DB.prepare(
          `SELECT job_slug AS slug, COUNT(*) n FROM sourced_candidates
            WHERE ${fJob.W} AND COALESCE(job_slug,'') <> ''
            GROUP BY job_slug ORDER BY n DESC`).bind(...fJob.B).all();
        // 職缺標題另外查，避免相關子查詢拖慢
        const titleRows = (await env.DB.prepare(`SELECT slug, title FROM jobs`).all()).results || [];
        const titleMap = Object.fromEntries(titleRows.map((t) => [t.slug, t.title]));
        const jobsAgg = { results: (jobRows || []).map((j) => ({ ...j, title: titleMap[j.slug] || j.slug })) };
        // 分類清單也要跟著目前的篩選條件走，不然數字對不上會讓人以為壞了
        const cats = { results: await facet('cat', `COALESCE(category,'未分類')`) };
        const srcs = { results: await facet('src', `source`, `COALESCE(source,'') <> ''`) };
        return json(request, { ok: true, rows: results || [],
          counts: counts.results || [], cats: cats.results || [], srcs: srcs.results || [],
          jobs: jobsAgg.results || [],
          grades: gradesAgg.results || [], rcs: rcsAgg.results || [],
          total: (tot && tot.n) || 0, page, size: SIZE });
      }

      // 校園徵才／一般外聯寄信（2026-08-26 加）
      //
      // 為什麼要多這一支：Resend 金鑰只放在 Worker 的 secret，本機不留一份，
      // 所以顧問要用 step1ne.com 網域對外寄信，只能繞過來由 Worker 代發。
      // Gmail 連接器那條路寄件人會顯示個人 gmail，跟 Step1ne 品牌對不上。
      //
      // 寄件人固定 jackychen@step1ne.com——Resend 驗證的是網域不是單一地址，
      // 所以不必另外設定。但 reply_to 預設同一個地址，
      // 若該信箱在 GoDaddy 那邊沒開通，回信會掉，呼叫時要自己帶 replyTo。
      if (p === '/admin/send-outreach' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const to = String(b.to || '').trim();
        const subject = String(b.subject || '').trim();
        const body = String(b.body || '');
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
          return json(request, { ok: false, error: '收件者 Email 格式不正確' }, 400);
        }
        if (!subject || !body) return json(request, { ok: false, error: 'subject 與 body 都要有' }, 400);
        if (!env.RESEND_API_KEY) return json(request, { ok: false, error: 'RESEND_API_KEY 沒設' }, 500);
        const escOut = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;');
        const html = b.html ? String(b.html)
          : `<div style="font-family:-apple-system,'Noto Sans TC',sans-serif;font-size:15px;`
            + `line-height:1.9;color:#23262d;max-width:620px;white-space:pre-wrap;">`
            + escOut(body) + `</div>`;
        try {
          const r = await fetch('https://api.resend.com/emails', {
            method: 'POST',
            headers: { authorization: `Bearer ${env.RESEND_API_KEY}`, 'content-type': 'application/json' },
            body: JSON.stringify({
              from: 'Jacky Chen <jackychen@step1ne.com>',
              to: [to], subject, text: body, html,
              reply_to: String(b.replyTo || 'jackychen@step1ne.com'),
            }),
            signal: AbortSignal.timeout(20000),
          });
          const detail = await r.text();
          if (!r.ok) return json(request, { ok: false, error: 'Resend 回錯誤', detail: detail.slice(0, 400) }, 502);
          await notify(env, `📧 已寄出外聯信\n收件：${to}\n主旨：${subject}`);
          return json(request, { ok: true, detail: detail.slice(0, 200) });
        } catch (e) {
          return json(request, { ok: false, error: String(e).slice(0, 200) }, 500);
        }
      }

      // 更新一筆的狀態（顧問挑人、標記已接觸、標記不合適）
      if (p === '/admin/sourced/status' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        // 2026-09-01 加 'standby'（預備區）：談過、目前沒缺，先養著之後再問意願，
        // 跟 'rejected'（真的不考慮了）是不同語意，不能共用一個狀態值。
        const ok = ['new', 'shortlisted', 'contacted', 'replied', 'standby', 'rejected', 'converted'];
        if (!b.id || !ok.includes(b.status)) return json(request, { ok: false, error: '參數錯誤' }, 400);
        // 「不合適」一定要說為什麼——不然這個按鈕就只是把人藏起來，學不到東西。
        // 用固定選項而不是自由輸入：自由文字每個人寫法不同，
        // 「地點太遠」「通勤問題」「在南部」是同一件事，統計起來卻是三種，
        // 那樣累積再多也歸納不出該調什麼。
        if (b.status === 'rejected' && !REJECT_REASONS.includes(String(b.reason || ''))) {
          return json(request, { ok: false, error: '請選一個不合適的原因',
                                 reasons: REJECT_REASONS }, 400);
        }
        await env.DB.prepare(
          `UPDATE sourced_candidates SET status=?,
                  contacted_at = CASE WHEN ?='contacted' THEN datetime('now','+8 hours') ELSE contacted_at END,
                  note = COALESCE(?, note),
                  reject_reason = CASE WHEN ?='rejected' THEN ? ELSE reject_reason END
            WHERE id=?`
        ).bind(b.status, b.status, b.note ? String(b.note).slice(0, 500) : null,
               b.status, b.status === 'rejected' ? String(b.reason) : null, b.id).run();
        return json(request, { ok: true });
      }

      // ── 發文排程行事曆 ──
      // 為什麼要有：8 月實測有 16 組「同一個顧問、同一天、同一個職缺發兩次以上」，
      // 8/21 的 BIM 有四個帳號各自發了 3 則。沒有一個地方看得到「今天誰要發什麼」，
      // 每個人各自按各自的，撞在一起才發現。
      //
      // ⚠️ 只有一種情況是硬規則：同一個顧問、同一天、同一個職缺。
      //    不同顧問同一天發同一個職缺是正常操作（四個帳號一起推），不擋。
      //    第一版把它也算成重複，結果整個八月幾乎每天都在響——到處都在響的
      //    警告等於沒有警告。
      if (p === '/admin/social/schedule' && request.method === 'GET') {
        const month = (url.searchParams.get('month') || '').match(/^\d{4}-\d{2}$/)
          ? url.searchParams.get('month') : null;
        const like = (month || '') + '%';
        // 排好但還沒發的
        const { results: planned } = await env.DB.prepare(
          `SELECT s.id, s.post_date AS d, s.job_slug, s.account_id, s.status,
                  j.title AS job_title, a.label, a.platform
             FROM social_schedule s
             LEFT JOIN jobs j ON j.slug = s.job_slug
             LEFT JOIN social_accounts a ON a.id = s.account_id
            WHERE s.status <> 'cancelled'${month ? ' AND s.post_date LIKE ?' : ''}
            ORDER BY s.post_date`).bind(...(month ? [like] : [])).all();
        // 已經發過／已排隊的（真實紀錄）。日期取實際發布時間，沒有就用排入時間。
        const { results: actual } = await env.DB.prepare(
          `SELECT q.id, substr(COALESCE(q.posted_at, q.requested_at),1,10) AS d,
                  q.job_slug, q.account_id, q.status, q.url,
                  j.title AS job_title, a.label, a.platform
             FROM social_post_queue q
             LEFT JOIN jobs j ON j.slug = q.job_slug
             LEFT JOIN social_accounts a ON a.id = q.account_id
            WHERE a.id IS NOT NULL${month ? " AND substr(COALESCE(q.posted_at, q.requested_at),1,7) = ?" : ''}`
        ).bind(...(month ? [month] : [])).all();
        const { results: accounts } = await env.DB.prepare(
          `SELECT id, label, platform FROM social_accounts
            WHERE is_active = 1 AND platform <> 'line_community' ORDER BY platform, label`).all();
        const { results: jobs } = await env.DB.prepare(
          `SELECT slug, title FROM jobs WHERE COALESCE(status,'open') IN ('open','active') ORDER BY slug`).all();
        // 「這個缺最近有人選在跟阿財面談」——排程建議要照這個排優先順序，
        // 不是照發文次數。發文次數少不代表現在該推：有的缺本來就冷門，
        // 硬推也接不住人；有人選正在談，才是「現在發有機會接住他」的訊號。
        // interview_started_at 是實際開始面談的時間點，比 created_at 準——
        // 有些應徵是投了履歷但還沒開始談，那個不算「近期有人選面談」。
        const { results: recentIv } = await env.DB.prepare(
          `SELECT job_slug, COUNT(*) AS n FROM applications
            WHERE interview_started_at IS NOT NULL
              AND interview_started_at >= datetime('now','+8 hours','-14 days')
              AND (interview_mode IS NULL OR interview_mode <> 'consultant_call')
            GROUP BY job_slug`).all();
        const recentInterviews = {};
        (recentIv || []).forEach((r) => { recentInterviews[r.job_slug] = r.n; });
        return json(request, { ok: true, month, accounts, jobs, recentInterviews,
                               planned: planned || [], actual: actual || [] });
      }

      if (p === '/admin/social/schedule' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const date = String(b.post_date || '').trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return json(request, { ok: false, error: '日期格式要 YYYY-MM-DD' }, 400);
        if (!b.job_slug || !b.account_id) return json(request, { ok: false, error: '缺職缺或帳號' }, 400);
        const job = await env.DB.prepare(
          `SELECT slug, title, COALESCE(status,'open') AS status FROM jobs WHERE slug=?`).bind(String(b.job_slug)).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        if (job.status === 'closed') return json(request, { ok: false, error: '這個職缺已經結束招募了' }, 400);
        const acc = await env.DB.prepare(
          `SELECT id, label FROM social_accounts WHERE id=? AND is_active=1`).bind(b.account_id).first();
        if (!acc) return json(request, { ok: false, error: '找不到這個帳號' }, 404);

        // 硬規則：同一個顧問、同一天、同一個職缺。排程表跟已發紀錄都要查——
        // 只查排程表的話，早上手動發過、下午又排一次，還是會重複。
        const clashQ = await env.DB.prepare(
          `SELECT 1 FROM social_post_queue
            WHERE job_slug=? AND account_id=? AND status NOT IN ('skipped','deleted')
              AND substr(COALESCE(posted_at, requested_at),1,10) = ? LIMIT 1`
        ).bind(job.slug, acc.id, date).first();
        if (clashQ) {
          return json(request, { ok: false, error: `${acc.label} 這一天已經發過（或排過）同一個職缺了` }, 409);
        }
        const now = nowTaipei();
        try {
          const r = await env.DB.prepare(
            `INSERT INTO social_schedule (post_date, job_slug, account_id, status, created_by, created_at, updated_at)
             VALUES (?,?,?,'planned',?,?,?)`
          ).bind(date, job.slug, acc.id, b.by || null, now, now).run();
          return json(request, { ok: true, id: r.meta && r.meta.last_row_id });
        } catch (e) {
          if (String(e).includes('UNIQUE')) {
            return json(request, { ok: false, error: `${acc.label} 這一天已經排過同一個職缺了` }, 409);
          }
          throw e;
        }
      }

      if (p === '/admin/social/schedule/cancel' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.id) return json(request, { ok: false, error: '缺 id' }, 400);
        // 標成取消而不是刪除——之後要回答「這個月排了幾則、實際發了幾則、
        // 中途撤掉幾則」，刪掉就查不出來了。
        const r = await env.DB.prepare(
          `UPDATE social_schedule SET status='cancelled', updated_at=? WHERE id=? AND status='planned'`
        ).bind(nowTaipei(), b.id).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到、或這筆已經送去產稿了' }, 404);
        return json(request, { ok: true });
      }

      // ── 把草稿的審核通知重推一次到 Telegram ──
      // 顧問在覆蓋表上看到 ✎「草稿等你確認」，但 Telegram 裡翻不到那則訊息——
      // 舊的早就被洗掉了（最舊的是 8/14，十三天前）。點一下重推一則新的。
      //
      // ⚠️ 但不是無腦重推：草稿是存下來的，職缺內容會變。BIM 的薪資 8/27 改成
      //    面議之後，8/21 產的草稿裡還完整寫著「月薪 40,833–50,167」。
      //    所以推之前先檢查，過期或含客戶名的一律不推，改成退回重產。
      if (p === '/admin/social/renotify' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const row = await env.DB.prepare(
          `SELECT q.*, j.title, a.label AS account_label, a.tg_thread_id
             FROM social_post_queue q
             JOIN jobs j ON j.slug = q.job_slug
             LEFT JOIN social_accounts a ON a.id = q.account_id
            WHERE q.id = ?`).bind(b.id).first();
        if (!row) return json(request, { ok: false, error: '找不到這筆' }, 404);
        if (row.status === 'posted') return json(request, { ok: false, error: '這則已經發過了' }, 400);
        if (!row.draft) return json(request, { ok: false, error: '這則沒有草稿內容' }, 400);

        const ageDays = row.requested_at
          ? Math.floor((Date.now() - Date.parse(String(row.requested_at).replace(' ', 'T') + '+08:00')) / 86400000)
          : 0;
        const terms = await clientNameTerms(env);
        const nameHits = hitsClientNames(row.draft, terms);

        if (ageDays >= 3 || nameHits.length) {
          // 退回重產：把狀態清成待產稿，本機的腳本下一輪（每 2 分鐘）就會用
          // 現在的職缺內容與現行過濾重新寫一則，並推一則新的 Telegram 通知。
          await env.DB.prepare(
            `UPDATE social_post_queue SET status=NULL, draft=NULL, tg_message_id=NULL WHERE id=?`
          ).bind(row.id).run();
          return json(request, { ok: true, requeued: true, age_days: ageDays,
            reason: nameHits.length ? `草稿裡有客戶名稱「${nameHits.join('、')}」`
                                    : `草稿是 ${ageDays} 天前產的，職缺內容可能已經改過`,
            note: '已經退回重產，2 分鐘內會推一則全新的草稿到 Telegram' });
        }

        // 還新、也乾淨，就直接把同一則草稿再推一次
        const thread = row.tg_thread_id || 3306;
        const msg = `📱 全民獵才貼文草稿（重新提醒）\n`
          + `帳號：${row.account_label || '（未指定帳號）'}\n`
          + `職缺：${row.title}\n\n`
          + `── 以下會被公開發布 ──\n${row.draft}`;
        const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
          method: 'POST', headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            chat_id: env.TG_CHAT_ID, message_thread_id: thread, text: msg,
            reply_markup: { inline_keyboard: [[
              { text: '✅ 確認發布', callback_data: `soc_approve:${row.id}` },
              { text: '🔄 重新產一次', callback_data: `soc_regen:${row.id}` },
              { text: '❌ 不發這篇', callback_data: `soc_skip:${row.id}` },
            ]] },
          }),
        });
        const rd = await r.json().catch(() => ({}));
        if (!rd.ok) return json(request, { ok: false, error: 'Telegram 推送失敗：' + JSON.stringify(rd).slice(0, 200) }, 502);
        await env.DB.prepare(`UPDATE social_post_queue SET tg_message_id=? WHERE id=?`)
          .bind(String(rd.result.message_id), row.id).run();
        return json(request, { ok: true, requeued: false, thread,
                               note: '已經重推一則到 Telegram，去按確認發布' });
      }

      // ── 發文覆蓋表：哪個帳號、哪個職缺還沒發 ──
      // 2026-08-27 加。在這之前只有一條「發過什麼」的流水帳，要回答
      // 「還有哪些沒發」只能自己在腦中做交叉比對——而那正是每天要決定
      // 「今天發哪一則」時唯一需要的資訊。
      if (p === '/admin/social/coverage' && request.method === 'GET') {
        const days = Math.min(90, Math.max(1, parseInt(url.searchParams.get('days') || '14', 10)));
        const { results: accounts } = await env.DB.prepare(
          `SELECT id, label, platform FROM social_accounts
            WHERE is_active = 1 AND platform <> 'line_community'
            ORDER BY platform, label`).all();
        // 只列真的在招募的職缺——已結束或還沒核准的沒必要發文
        const { results: jobs } = await env.DB.prepare(
          `SELECT slug, title, service_line, seniority FROM jobs
            WHERE COALESCE(status,'open') IN ('open','active')
            ORDER BY slug`).all();
        // 一次撈完所有紀錄，在記憶體裡組表——職缺 × 帳號可能上百格，
        // 一格一次查會變成上百次往返。
        const { results: posts } = await env.DB.prepare(
          `SELECT id, job_slug, account_id, status, requested_at, posted_at, url, views, likes, replies
             FROM social_post_queue
            WHERE requested_at >= datetime('now','+8 hours','-' || ? || ' days')
               OR posted_at >= datetime('now','+8 hours','-' || ? || ' days')`
        ).bind(days, days).all();

        const byKey = {};
        for (const r of posts || []) {
          const k = r.job_slug + '|' + (r.account_id || 0);
          const prev = byKey[k];
          // 同一格可能有好幾筆（重發、退回重產）。留最能代表現況的那一筆：
          // 已發布 > 等你確認 > 其他；同狀態取最新。
          const rank = (x) => x.status === 'posted' ? 3 : (x.status === 'drafted' ? 2 : 1);
          if (!prev || rank(r) > rank(prev)
              || (rank(r) === rank(prev) && String(r.posted_at || '') > String(prev.posted_at || ''))) {
            byKey[k] = r;
          }
        }

        const grid = jobs.map((j) => ({
          slug: j.slug, title: j.title,
          cells: accounts.map((a) => {
            const r = byKey[j.slug + '|' + a.id];
            return { account_id: a.id, queue_id: r ? r.id : null,
                     state: r ? r.status : 'none',
                     requested_at: r ? r.requested_at : null,
                     posted_at: r ? r.posted_at : null,
                     url: r ? r.url : null,
                     views: r ? r.views : null };
          }),
        }));
        // 每個帳號還差幾個職缺沒發——顧問最常問的就是這個數字
        const gaps = accounts.map((a, i) => ({
          ...a,
          missing: grid.filter((g) => g.cells[i].state === 'none').length,
          drafted: grid.filter((g) => g.cells[i].state === 'drafted').length,
          posted: grid.filter((g) => g.cells[i].state === 'posted').length,
        }));
        return json(request, { ok: true, days, accounts, jobs, grid, gaps });
      }

      // ── 顧問名單 ──
      // 2026-08-26 加。在這之前「誰負責這個人選」是散在各表的自由文字
      //（同一個人被寫成 '同事'、'behe10'、'Phoebe' 三種），統計不起來、
      // 也沒辦法做「每個顧問看自己的表」。收斂成一張表當唯一名單來源，
      // applications.owner / placements.owner / sourced_candidates.owner
      // 一律存 consultants.id。
      if (p === '/admin/consultants' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, display_name, is_active, note FROM consultants
            ORDER BY is_active DESC, display_name ASC`).all();
        return json(request, { ok: true, consultants: results || [] });
      }

      if (p === '/admin/consultants' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const name = String(b.display_name || '').trim().slice(0, 40);
        if (!name) return json(request, { ok: false, error: '請填顧問名字' }, 400);
        const now = nowTaipei();
        if (b.id) {
          // 既有顧問：只改名字跟啟用狀態。不提供刪除——顧問離職後他手上
          // 的人選歷史還是要查得到，砍掉會讓那些紀錄變成 owner 指向空值。
          await env.DB.prepare(
            `UPDATE consultants SET display_name=?, is_active=?, note=?, updated_at=? WHERE id=?`
          ).bind(name, b.is_active === 0 ? 0 : 1, b.note ? String(b.note).slice(0, 200) : null,
                 now, String(b.id)).run();
          return json(request, { ok: true, id: String(b.id) });
        }
        // 新顧問：id 用名字轉小寫英數，撞名就加序號
        let base = name.toLowerCase().replace(/[^a-z0-9]+/g, '') || 'c';
        let id = base, n = 1;
        while (await env.DB.prepare(`SELECT 1 FROM consultants WHERE id=?`).bind(id).first()) {
          id = base + (++n);
        }
        await env.DB.prepare(
          `INSERT INTO consultants (id, display_name, is_active, note, created_at, updated_at)
           VALUES (?,?,1,?,?,?)`
        ).bind(id, name, b.note ? String(b.note).slice(0, 200) : null, now, now).run();
        return json(request, { ok: true, id });
      }

      // ── 顧問自己的 pipeline ──
      // 「每個顧問一張自己的表」。一列＝一件手上還沒收尾的事，
      // 不是一個人選——同一個人選同時被推去兩家客戶，就是兩件事。
      //
      // 分頁不照資料表切（sourced/applications/placements 是實作細節），
      // 照「現在球在誰手上」切，因為顧問早上打開這頁只想知道
      // 「哪幾件是在等我動，哪幾件是我在等別人」。
      if (p === '/admin/pipeline' && request.method === 'GET') {
        const owner = (url.searchParams.get('owner') || '').trim();
        // 'all' = 不過濾；'unassigned' = 還沒指派負責人的（人才池大宗都在這，
        // 分出來才看得出「有多少存貨根本沒人在管」）；其他 = 該顧問自己的。
        const isUn = owner === 'unassigned';
        const isFiltered = !!owner && owner !== 'all';
        const ownerSql = isUn ? ` AND owner IS NULL` : (isFiltered ? ` AND owner = ?` : '');
        const bind = (isFiltered && !isUn) ? [owner] : [];

        // ① 要我接觸：已經有人「認領」、但還沒發出第一封信
        //
        // ⚠️ 2026-08-26 修一：原本連未指派的 status='new' 也算進來，那是爬蟲剛
        // 倒進去、沒有任何人看過的人才池存貨（3,500 筆）。把它當成「今天要我
        // 接觸的」會讓數字永遠是三千多，顧問一眼就知道這頁在唬爛、直接不看。
        //
        // ⚠️ 2026-08-27 修二：只認 'shortlisted' 又太窄了。顧問在人才池把 20 個人
        // 指派給自己之後，這一關還是 0——因為那些人狀態是 'new'。
        // **指派本身就是一種篩選動作**：把人放進某個顧問名下，就是在說
        // 「這個人歸他處理」，跟按「挑出來」是同一個意思，不該再要求按第二次。
        // 所以規則是：已挑出的（不論有沒有指派）＋ 已指派但還沒挑的。
        // 沒指派又沒挑出的，才是純存貨，留在「人才池」頁。
        const toContact = (await env.DB.prepare(
          `SELECT id, name, headline, company, job_slug, grade, score, email, phone,
                  created_at AS since, status, owner, source
             FROM sourced_candidates
            WHERE (status IN ('shortlisted','replied') OR (status = 'new' AND owner IS NOT NULL))${ownerSql}
            -- ⚠️ 2026-08-27 補 'replied'。在這之前按下「他有回覆」的人**從所有分頁
            -- 消失**——replied 不在任何一關的條件裡：要我接觸只收 shortlisted/new，
            -- 等對方回只收 contacted。對方好不容易回信了，這個人反而不見了。
            -- 球回到顧問手上，所以歸在「要我接觸」，並排到最前面。
            ORDER BY (status = 'replied') DESC, COALESCE(score,0) DESC, created_at ASC LIMIT 200`
        ).bind(...bind).all()).results || [];

        // ② 等對方回：信發出去了，人還沒回
        const waiting = (await env.DB.prepare(
          `SELECT id, name, headline, company, job_slug, grade, email, phone,
                  COALESCE(contacted_at, created_at) AS since, status, owner, source
             FROM sourced_candidates
            WHERE status = 'contacted'${ownerSql}
            ORDER BY since ASC LIMIT 200`
        ).bind(...bind).all()).results || [];

        // ③ 等我看報告：談完了，顧問還沒決定推不推
        // ⚠️ 排除掉已經在等人選回覆的（PENDING_CANDIDATE）——那些球在人選身上，
        // 不是在顧問身上，混進這一關會讓「今天該我動的有幾件」灌水。
        const toReview = (await env.DB.prepare(
          `SELECT a.id, a.name, a.job_slug, a.job_title, a.email, a.phone, a.owner,
                  COALESCE(a.interview_ended_at, a.created_at) AS since
             FROM applications a
            WHERE a.interview_state IN ('done','paused')
              AND (SELECT r.consultant_decision FROM reports r
                    WHERE r.application_id = a.id
                    ORDER BY r.created_at DESC LIMIT 1) IS NULL
              AND NOT EXISTS (SELECT 1 FROM placements p3
                               WHERE p3.application_id = a.id
                                 AND UPPER(p3.stage) = 'PENDING_CANDIDATE')
              ${isUn ? ' AND a.owner IS NULL' : (isFiltered ? ' AND a.owner = ?' : '')}
            ORDER BY since ASC LIMIT 200`
        ).bind(...bind).all()).results || [];

        // ④ 客戶手上 ⑤ 快成交：都從 placements 來，差在階段
        // 字彙沿用上面漏斗那套（三套工具寫入、詞不統一，這裡必須全認）
        const CLIENT_ST = `('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW','AWAITING_CLIENT_FEEDBACK','INTERVIEW_COMPLETED')`;
        const OFFER_ST = `('OFFER','OFFER_ACCEPTED','HIRED','PLACED','OFFER_PENDING','ONBOARDING')`;
        const pl = async (stSql) => (await env.DB.prepare(
          `SELECT p.id, p.application_id, p.candidate_name AS name, p.job_slug, p.job_title,
                  p.client_name, p.client_company, p.stage, p.owner,
                  COALESCE(p.stage_since, p.created_at) AS since,
                  p.next_followup, p.onboard_date, a.email, a.phone
             FROM placements p
             LEFT JOIN applications a ON a.id = p.application_id
            WHERE ${stSql}${isUn ? ' AND p.owner IS NULL' : (isFiltered ? ' AND p.owner = ?' : '')}
            ORDER BY since ASC LIMIT 200`
        ).bind(...bind).all()).results || [];

        // ② 續：等對方回還有另一種——已經談完初篩、顧問也接觸了，
        // 但人選遲遲沒回覆條件。這種人以前只能被標成「婉拒／退出」才收得起來，
        // 結果就是明明只是沒回訊息的人被當成拒絕，整個從待辦裡消失
        //（林均緯就是這樣消失了 14 天）。PENDING_CANDIDATE 就是給這種狀況用的。
        const waitingPl = await pl(`UPPER(p.stage) = 'PENDING_CANDIDATE'`);

        const clientSide = await pl(`UPPER(p.stage) IN ${CLIENT_ST} AND p.onboard_date IS NULL`);
        const closing = await pl(`(UPPER(p.stage) IN ${OFFER_ST} OR p.onboard_date IS NOT NULL)
                                  AND UPPER(p.stage) NOT IN ('CLOSED_LOST','CLOSED','REJECTED_BY_CLIENT','WITHDRAWN_BY_CANDIDATE')`);

        // 停滯天數的紅線。這幾個數字不是猜的，是照實際資料抓出來的：
        // 目前最久的兩筆（張博州、呂皓宇）在客戶端卡了 22 天沒人動，
        // 所以客戶端 14 天就該亮燈；主動開發沒回信 7 天就該補一封或放掉；
        // 報告放著沒看超過 3 天，人選通常已經接受別家面試了。
        const STALE = { to_contact: 5, waiting_reply: 7, to_review: 3, client_side: 14, closing: 10 };
        const today = new Date(Date.now() + 8 * 3600 * 1000);
        const ageOf = (s) => {
          if (!s) return null;
          const d = new Date(String(s).replace(' ', 'T') + (String(s).length <= 10 ? 'T00:00:00' : ''));
          if (isNaN(d)) return null;
          return Math.floor((today - d) / 86400000);
        };
        const deco = (rows, key) => rows.map((r) => {
          const age = ageOf(r.since);
          return { ...r, age_days: age, stale: age !== null && age >= STALE[key] };
        });

        // ⑥ 已收尾：成交或結案的。放進來是因為顧問手上沒有進行中案子時
        // 這張表會整片空白，看起來像系統壞掉；而且要回頭查「上個月那個人
        // 後來怎麼了」也只有這裡查得到。不列入停滯計算。
        const doneRows = await pl(`(UPPER(p.stage) IN ('CLOSED_LOST','CLOSED','REJECTED_BY_CLIENT','WITHDRAWN_BY_CANDIDATE')
                                    OR p.onboard_date IS NOT NULL)`);
        // ⚠️ 2026-08-27 補：人才池標「不合適」的人本來哪一關都不在。
        // 卡片上的按鈕明寫「不合適 → 已收尾」，但已收尾只讀 placements，
        // 而主動開發的人選根本沒有送件紀錄——按下去人就從整個「我的案子」
        // 消失了。實際有 21 位卡在這個黑洞裡（Phoebe 12、Jacky 1、未指派 8）。
        const doneSourced = (await env.DB.prepare(
          `SELECT id, name, headline, company, job_slug, grade, email, phone,
                  COALESCE(contacted_at, created_at) AS since, status, owner, source,
                  reject_reason
             FROM sourced_candidates
            WHERE status = 'rejected'${ownerSql}
            ORDER BY since DESC LIMIT 200`
        ).bind(...bind).all()).results || [];

        const tabs = {
          to_contact: deco(toContact, 'to_contact'),
          waiting_reply: deco(waiting.concat(waitingPl), 'waiting_reply'),
          to_review: deco(toReview, 'to_review'),
          client_side: deco(clientSide, 'client_side'),
          closing: deco(closing, 'closing'),
          done: doneRows.concat(doneSourced)
                  .map((r) => ({ ...r, age_days: ageOf(r.since), stale: false })),
        };
        const counts = {}, stale = {};
        for (const k of Object.keys(tabs)) {
          counts[k] = tabs[k].length;
          stale[k] = tabs[k].filter((r) => r.stale).length;
        }
        return json(request, { ok: true, owner: owner || 'all', tabs, counts, stale, thresholds: STALE });
      }

      // ── 顧問人選追蹤（Phase 2）：把「初篩報告」＋「我的案子」合併成一份
      // 統一的人選卡片清單。設計沿用 Jacky 拍板的規則：一張卡＝一個人，
      // 姓名/信箱/電話任一項核對相符就併成同一張卡，卡片內用 placements
      // 陣列列出這個人被推去的每一家客戶／職缺，各自獨立進度。
      //
      // ⚠️ 資料模型跟 Phase 1 demo 假資料不同，這裡要說清楚：一個人選在系統裡
      // 「同一家客戶推派」是 candidate_forwards（一個 application 對多個
      // company_id），但「推去不同職缺」在這個系統一直是**分開的 applications
      // 列**（每個 application 綁死一個 job_slug）。所以合併分兩層：
      // ① 同一個 application 底下可能已經推給好幾家客戶（candidate_forwards
      //    多筆）；② 同一個人可能有好幾個 application（投了不同職缺），
      //    用 email／電話比對抓出來，合併進同一張卡片的 placements。
      if (p === '/admin/pipeline/tracker' && request.method === 'GET') {
        const owner = (url.searchParams.get('owner') || '').trim();
        const isUn = owner === 'unassigned';
        const isFiltered = !!owner && owner !== 'all';
        const ownerSql = isUn ? ` AND a.owner IS NULL` : (isFiltered ? ` AND a.owner = ?` : '');
        const ownerBind = (isFiltered && !isUn) ? [owner] : [];

        const { results: apps } = await env.DB.prepare(
          `SELECT a.id, a.created_at, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.owner, a.source_channel, a.resume_file_id, a.resume_extra_files, a.resume_source,
                  a.note AS consultant_note, a.interview_state, a.interview_mode,
                  a.interview_started_at, a.interview_ended_at, a.status, a.screen_decision,
                  a.expected_salary, a.available_date, a.call_summary_md,
                  j.company_id, j.title AS job_full_title,
                  cc.display_name AS client_name,
                  (SELECT display_name FROM consultants c WHERE c.id = a.owner) AS owner_name,
                  -- 這個人如果是主動開發進來的（先在人才池被找到、後來才轉成
                  -- 正式應徵），現職公司/職位/LinkedIn 原本就存在 sourced_candidates，
                  -- 不是 applications 沒有這些欄位，是查詢忘記接回去——一次 join 補齊。
                  sc.id AS sourced_link_id, sc.headline AS current_title, sc.company AS current_company, sc.linkedin_url AS linkedin
             FROM applications a
             LEFT JOIN jobs j ON j.slug = a.job_slug
             LEFT JOIN client_companies cc ON cc.id = j.company_id
             LEFT JOIN sourced_candidates sc ON sc.converted_application_id = a.id
            WHERE 1=1${ownerSql}
            ORDER BY a.created_at DESC LIMIT 500`
        ).bind(...ownerBind).all();

        // ⚠️ 2026-09-01 改：原本這裡對「每一個」application 各自平行查 4~5 次
        // （report／forwards／appts／no-company placement，再加上每筆 forward
        // 各自查一次 placement），117 個人選一次載入就是 700 次上下的查詢。
        // 這頁前端還有 30 秒自動刷新——長時間開著疊起來，是這次把 D1 免費方案
        // 每日讀取額度（500 萬筆）打到 2200 萬筆、整個後台斷線一整天的主因。
        // 改成依 application_id 批次撈（IN 子句，超過 100 筆分批以免超過 D1
        // bind 參數上限），撈回來後在記憶體裡用 Map 分組，查詢次數從「O(人數)」
        // 降到固定的幾次，不管有 10 個人選還是 500 個人選都一樣。
        const appIds = (apps || []).map((a) => a.id);
        const chunk = (arr, size) => { const out = []; for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size)); return out; };
        const inClause = (n) => `(${Array.from({ length: n }, () => '?').join(',')})`;
        const batchAll = async (sql, ids) => {
          const rows = [];
          for (const ids2 of chunk(ids, 100)) {
            if (!ids2.length) continue;
            const { results } = await env.DB.prepare(sql(ids2.length)).bind(...ids2).all();
            rows.push(...(results || []));
          }
          return rows;
        };

        const trackerJobStageKeysMap = await getJobStageKeysMap(env, (apps || []).map((a) => a.job_slug));

        const [reportRows, forwardRowsAll, apptRowsAll, noCoPlacementRows, coPlacementRows, formSubRowsAll] = appIds.length
          ? await Promise.all([
              batchAll((n) => `SELECT application_id, id, content_md, consultant_decision, created_at FROM reports
                WHERE application_id IN ${inClause(n)} ORDER BY application_id, created_at DESC`, appIds),
              batchAll((n) => `SELECT cf.application_id, cf.company_id, cf.manual_stage, cf.manual_stage_note, cf.client_note,
                      cf.line_id_given, cf.forwarded_at, cc.display_name AS company_name,
                      cf.advisor_not_recommended_at, cf.advisor_not_recommended_reason,
                      cf.client_rejected_at, cf.client_reject_reason
                 FROM candidate_forwards cf LEFT JOIN client_companies cc ON cc.id = cf.company_id
                WHERE cf.application_id IN ${inClause(n)} ORDER BY cf.application_id, cf.forwarded_at ASC`, appIds),
              batchAll((n) => `SELECT application_id, stage, status FROM interview_appointments
                WHERE application_id IN ${inClause(n)}`, appIds),
              batchAll((n) => `SELECT application_id, stage, updated_at FROM placements
                WHERE application_id IN ${inClause(n)} AND client_id IS NULL
                ORDER BY application_id, updated_at DESC`, appIds),
              batchAll((n) => `SELECT application_id, client_id, stage, onboard_date, updated_at FROM placements
                WHERE application_id IN ${inClause(n)} AND client_id IS NOT NULL
                ORDER BY application_id, client_id, updated_at DESC`, appIds),
              // 2026-09-02 加：人選客製表單狀態，一併批次撈起來，掛在各家客戶
              // 自己的 placement 卡片底下（見下面 forms: ... 那行）。
              batchAll((n) => `SELECT cfs.id, cfs.application_id, cfs.company_id, cfs.status, cfs.sent_at,
                      cfs.submitted_at, cfs.submitted_file_id, jf.name AS form_name
                 FROM candidate_form_submissions cfs JOIN job_forms jf ON jf.id = cfs.job_form_id
                WHERE cfs.application_id IN ${inClause(n)}`, appIds),
            ])
          : [[], [], [], [], [], []];

        // 「每個 application 最新一筆」的挑法統一用同一招：來源已經 ORDER BY ... DESC，
        // 用 Map 只留第一次看到的那筆（Map.set 只在 key 不存在時才寫）。
        const latestByApp = (rows) => { const m = new Map(); for (const r of rows) if (!m.has(r.application_id)) m.set(r.application_id, r); return m; };
        const reportByApp = latestByApp(reportRows);
        const noCoPlacementByApp = latestByApp(noCoPlacementRows);
        const forwardsByApp = new Map();
        for (const f of forwardRowsAll) { if (!forwardsByApp.has(f.application_id)) forwardsByApp.set(f.application_id, []); forwardsByApp.get(f.application_id).push(f); }
        const apptsByApp = new Map();
        for (const ap of apptRowsAll) { if (!apptsByApp.has(ap.application_id)) apptsByApp.set(ap.application_id, []); apptsByApp.get(ap.application_id).push(ap); }
        const placementByAppCompany = new Map();
        for (const pl of coPlacementRows) {
          const key = pl.application_id + '|' + pl.client_id;
          if (!placementByAppCompany.has(key)) placementByAppCompany.set(key, pl);
        }
        const formSubsByAppCompany = new Map();
        for (const fs of formSubRowsAll) {
          const key = fs.application_id + '|' + fs.company_id;
          if (!formSubsByAppCompany.has(key)) formSubsByAppCompany.set(key, []);
          formSubsByAppCompany.get(key).push({
            id: fs.id, form_name: fs.form_name, status: fs.status,
            sent_at: fs.sent_at, submitted_at: fs.submitted_at, has_file: !!fs.submitted_file_id,
          });
        }

        const detail = (apps || []).map((a) => {
          const report = reportByApp.get(a.id) || null;
          const appts = apptsByApp.get(a.id) || [];
          const forwardRows = forwardsByApp.get(a.id) || [];
          const noCompanyPlacement = noCoPlacementByApp.get(a.id) || null;
          // 每家客戶各自的 placements 進度要分開算——同一個 application 推給
          // A、B 兩家客戶，A 已經到第二階段、B 才剛推送，不能共用一份 stage。
          const placements = forwardRows.map((f) => {
            const placement = placementByAppCompany.get(a.id + '|' + f.company_id) || null;
            const stageInfo = resolveStage(
              { manual_stage: f.manual_stage }, report, appts, placement, {}, trackerJobStageKeysMap[a.job_slug]);
            // 2026-09-01 加：client_rejected_at 本來只會發一次 Telegram 通知，
            // 追蹤頁完全沒讀這個欄位——顧問錯過那則通知，這張卡就會一直卡在
            // 「客戶手上」看起來像還在進行中，其實客戶早就婉拒了。這裡把它也
            // 當成一種「結案」，跟 CLOSED_* 一樣會讓這張卡落進「已結案」。
            const closed = f.client_rejected_at
              ? 'CLIENT_REJECTED'
              : (placement && /^CLOSED_/.test(String(placement.stage || '').toUpperCase())
                  ? placement.stage.toUpperCase() : null);
            return {
              company_id: f.company_id, client_name: f.company_name || '（未知客戶）',
              job_title: a.job_full_title || a.job_title, forwarded_at: f.forwarded_at,
              manual_stage_note: f.manual_stage_note, client_note: f.client_note,
              line_id_given: f.line_id_given, closed,
              forms: formSubsByAppCompany.get(a.id + '|' + f.company_id) || [],
              steps: stageInfo.steps, effective_index: stageInfo.effective_index,
              advisor_not_recommended_at: f.advisor_not_recommended_at || null,
              advisor_not_recommended_reason: f.advisor_not_recommended_reason || null,
              client_rejected_at: f.client_rejected_at || null,
              client_reject_reason: f.client_reject_reason || null,
            };
          });
          const primaryPlacement = placements[0] || null;
          const aiDone = !!(a.interview_mode !== 'consultant_call' && a.interview_state === 'done');
          const evaluated = !!report;
          const pushed = placements.length > 0;
          const hardGate = a.status === 'pending_screen' && !a.screen_decision;
          const noCompanyClosed = !primaryPlacement && noCompanyPlacement
            && /^CLOSED_/.test(String(noCompanyPlacement.stage || '').toUpperCase())
            ? noCompanyPlacement.stage.toUpperCase() : null;
          return {
            application_id: a.id, name: a.name, email: a.email, phone: a.phone,
            job_slug: a.job_slug, job_title: a.job_full_title || a.job_title,
            client_name: primaryPlacement ? primaryPlacement.client_name : (a.client_name || '（未綁客戶）'),
            company_id: a.company_id || null,
            owner: a.owner, owner_name: a.owner_name, source_channel: a.source_channel,
            resume_file_id: a.resume_file_id, resume_extra_files: a.resume_extra_files,
            resume_source: a.resume_source,
            current_title: a.current_title, current_company: a.current_company, linkedin: a.linkedin,
            sourced_link_id: a.sourced_link_id || null, call_summary_md: a.call_summary_md,
            consultant_note: a.consultant_note,
            expected_salary: a.expected_salary, available_date: a.available_date,
            ai_done: aiDone, evaluated, pushed,
            closed: primaryPlacement ? primaryPlacement.closed : noCompanyClosed, hard_gate: hardGate,
            decision: report ? report.consultant_decision : null,
            report_id: report ? report.id : null, report_md: report ? report.content_md : null,
            manual_stage_note: primaryPlacement ? primaryPlacement.manual_stage_note : null,
            client_note: primaryPlacement ? primaryPlacement.client_note : null,
            line_id_given: primaryPlacement ? primaryPlacement.line_id_given : null,
            steps: primaryPlacement ? primaryPlacement.steps : [],
            effective_index: primaryPlacement ? primaryPlacement.effective_index : -1,
            placements, // 這個 application 底下全部客戶各自的進度，多於一筆時 UI 才會逐一列出
            created_at: a.created_at,
          };
        });

        // ── 合併：email 或電話任一相符就併成同一張卡，出現在同一張卡的
        // application 除了「主要」那一筆，其餘進 placements 陣列。
        // 主要那筆的挑選順序：真的跟阿財完整面談過的優先（aiDone），
        // 其次是最早建立的一筆——避免每次重新整理主要的人選跳來跳去。
        const norm = (s) => String(s || '').trim().toLowerCase();
        const phoneTail = (s) => String(s || '').replace(/\D/g, '').slice(-9);
        const keyOf = (d) => [norm(d.email) || null, phoneTail(d.phone) || null];
        const groups = [];
        const seen = new Map(); // key(email或phone) -> group index
        for (const d of detail) {
          const [e, ph] = keyOf(d);
          let gi = (e && seen.has('e:' + e)) ? seen.get('e:' + e)
                 : (ph && seen.has('p:' + ph)) ? seen.get('p:' + ph) : -1;
          if (gi === -1) { gi = groups.length; groups.push([]); }
          groups[gi].push(d);
          if (e) seen.set('e:' + e, gi);
          if (ph) seen.set('p:' + ph, gi);
        }

        const cards = groups.map((g) => {
          // 主要那筆的挑選順序：已經推給客戶的（真的在跑的流程）優先，
          // 其次有報告可看的，其次真的跟阿財完整面談過的，最後才比早晚——
          // 不能只看「誰先建立」，不然像周丞恩事件那樣，先建立但面談失敗的
          // 舊嘗試會蓋掉後來真的在跑、已經推給客戶的那一筆。
          g.sort((x, y) => (y.pushed - x.pushed) || (y.evaluated - x.evaluated) ||
            (y.ai_done - x.ai_done) || (x.created_at < y.created_at ? -1 : 1));
          const primary = g[0];
          const rest = g.slice(1);
          // 疑似同一人的警示：只有在「不是每個 email 都一樣」時才提醒——單純同一
          // 個人用同一組聯絡方式投兩個職缺，不算意外，不用特別跳警告。
          const emails = new Set(g.map((d) => norm(d.email)).filter(Boolean));
          const dupNotice = g.length > 1 && emails.size > 1
            ? '姓名/電話跟另外 ' + (g.length - 1) + ' 筆紀錄的聯絡方式相符但 email 不同，系統判斷可能是同一人——請顧問確認是否為誤植。'
            : null;
          // tab 在後端算好直接回傳，不要讓前端各自猜——這正是「初篩報告」跟
          // 「我的案子」原本各算一套才會兜不起來的老問題，這次不能重蹈覆轍。
          // 2026-09-01 改：Jacky 定案的 6 分頁架構——
          //   fill  （等人選填完）：還沒推給客戶、也還沒有報告可看——球在人選
          //          身上（阿財面談還沒結束）或系統背景處理中（電洽摘要還沒
          //          產出）。用 evaluated（有沒有報告）判斷，同時涵蓋自己面談
          //          跟顧問電洽兩種來源，不用分別檢查 interview_state。
          //   decide（等我決定）：有報告了，等顧問審閱——hard_gate（阿財初審
          //          卡關）也併進這裡，一起等顧問處理，不獨立分頁。
          //   client（客戶面談階段）：已推給客戶，原本「客戶手上」跟「快成交」
          //          兩個分頁合併成一個，不再靠 effective_index 猜要分去哪邊。
          //   pool  （候補池）：已結案／顧問明確不推薦，跟主動開發那邊的
          //          「先留著（預備區）」是同一個分頁——人選本身沒有消失，
          //          只是目前沒有進行中的媒合，隨時可以被重新推薦。
          let tab;
          if (primary.closed || primary.decision === 'rejected') tab = 'pool';
          else if (primary.hard_gate) tab = 'decide'; // 阿財初審卡關，併入等我決定
          else if (!primary.pushed) tab = primary.evaluated ? 'decide' : 'fill';
          else tab = 'client';
          return { ...primary, kind: 'application', tab, dup_notice: dupNotice, extra_placements: rest };
        });

        // ── 主動開發、還沒轉正式應徵的人選（要我接觸／等對方回）──
        // ⚠️ 這兩關本來不在這支端點裡，上線後被抓到漏了——「合併」講的是把
        // 兩個舊入口的內容都收進來，不是只收一半，缺這兩關等於顧問還是得
        // 開兩個分頁才看得到全貌，那就白做了。條件跟 /admin/pipeline 完全一致，
        // 差別只是這裡要把它們塞進同一份 cards 陣列、同一套卡片形狀。
        const srcOwnerSql = isUn ? ` AND owner IS NULL` : (isFiltered ? ` AND owner = ?` : '');
        const srcBind = (isFiltered && !isUn) ? [owner] : [];
        // 2026-09-01 加：status 補進 SELECT 跟卡片——本來只回傳 tab（待開發接觸／
        // 等對方回兩種），shortlisted 跟 replied 雖然都落在「待開發接觸」這個 tab，
        // 但對顧問來說是完全不同的兩件事（還沒聯繫 vs 對方已回信換你判斷），
        // 前端要分清楚就得看到真正的 status，不能只看 tab。
        // 同時加第三種：status='standby'（預備區）——談過但現在沒缺，先養著、
        // 之後有新職缺再回頭找，跟「不合適」不一樣，不該永久消失。
        const [toContactRes, waitingRes, standbyRes] = await Promise.all([
          env.DB.prepare(
            `SELECT id, name, email, phone, headline, job_slug, owner, source, created_at, status,
                    invite_token, invite_job_slug, invite_at
               FROM sourced_candidates
              WHERE converted_application_id IS NULL
                AND (status IN ('shortlisted','replied') OR (status = 'new' AND owner IS NOT NULL))${srcOwnerSql}
              ORDER BY created_at ASC LIMIT 200`
          ).bind(...srcBind).all(),
          env.DB.prepare(
            `SELECT id, name, email, phone, headline, job_slug, owner, source, created_at, status,
                    invite_token, invite_job_slug, invite_at
               FROM sourced_candidates
              WHERE converted_application_id IS NULL AND status = 'contacted'${srcOwnerSql}
              ORDER BY created_at ASC LIMIT 200`
          ).bind(...srcBind).all(),
          env.DB.prepare(
            `SELECT id, name, email, phone, headline, job_slug, owner, source, created_at, status,
                    invite_token, invite_job_slug, invite_at
               FROM sourced_candidates
              WHERE converted_application_id IS NULL AND status = 'standby'${srcOwnerSql}
              ORDER BY created_at DESC LIMIT 200`
          ).bind(...srcBind).all(),
        ]);
        const ownerNames = {};
        if (toContactRes.results?.length || waitingRes.results?.length || standbyRes.results?.length) {
          const { results: consRows } = await env.DB.prepare(`SELECT id, display_name FROM consultants`).all();
          (consRows || []).forEach((c) => { ownerNames[c.id] = c.display_name; });
        }
        const sourcedCard = (r, tab) => ({
          application_id: null, sourced_id: r.id, kind: 'sourced', tab, status: r.status,
          name: r.name, email: r.email, phone: r.phone,
          job_slug: r.job_slug, job_title: r.headline || r.job_slug || '（主動開發，尚未定職缺）',
          client_name: '（尚未推送客戶）', owner: r.owner, owner_name: ownerNames[r.owner] || null,
          source_channel: r.source || '主動開發', resume_file_id: null, resume_extra_files: null,
          consultant_note: null, ai_done: false, evaluated: false, pushed: false, closed: null,
          hard_gate: false, decision: null, report_id: null, report_md: null,
          steps: [], effective_index: -1, placements: [], extra_placements: [], dup_notice: null,
          invite_token: r.invite_token || null, invite_job_slug: r.invite_job_slug || null, invite_at: r.invite_at || null,
          created_at: r.created_at,
        });
        cards.push(...(toContactRes.results || []).map((r) => sourcedCard(r, 'to_contact')));
        cards.push(...(waitingRes.results || []).map((r) => sourcedCard(r, 'waiting_reply')));
        // 2026-09-01 改：預備區併進「候補池」（pool）——跟正式應徵那邊「已結案」
        // 是同一個分頁，人選本身沒有消失，只是目前沒有進行中的媒合。
        cards.push(...(standbyRes.results || []).map((r) => sourcedCard(r, 'pool')));

        return json(request, { ok: true, cards });
      }

      // 2026-09-01 加：顧問想即時看到「現在誰正在跟阿財面談」，不是只有面談
      // 結束產出報告才看得到。interview_state='active' 本來就是這個標記
      // （liveLoad() 已經在用它算「幾場面談在跑」），這裡補一支給人選清單，
      // 不是只有數字。純讀取，不寫任何東西。
      if (p === '/admin/interviews/live' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT a.id AS application_id, a.name, a.job_slug, a.interview_started_at, a.owner,
                  j.title AS job_title,
                  (SELECT display_name FROM consultants c WHERE c.id = a.owner) AS owner_name
             FROM applications a
             LEFT JOIN jobs j ON j.slug = a.job_slug
            WHERE a.interview_state = 'active'
            ORDER BY a.interview_started_at ASC LIMIT 50`
        ).all();
        return json(request, { ok: true, interviews: results || [] });
      }

      // ── 下載存在 D1 裡的檔案（履歷等） ──
      // 2026-08-26 加。在這之前後台只有「把履歷當附件推到 Telegram」這一條路，
      // 網頁上沒有任何地方能直接把檔案取出來——顧問在後台看到「有履歷」卻打不開。
      // ⚠️ 這支必須走 Authorization header（跟其他 /admin/* 一樣），不能用網址帶
      // 權杖。履歷是個資，權杖進網址就會留在瀏覽器紀錄、分頁標題、referer 裡。
      // 所以前端不能直接 <a href> 過來，要用 fetch 帶 header 取回再轉 blob 下載。
      if (p.startsWith('/admin/file/') && request.method === 'GET') {
        const fid = decodeURIComponent(p.slice('/admin/file/'.length));
        const f = await env.DB.prepare(
          `SELECT filename, mime, content_b64, chunks FROM files WHERE id = ?`).bind(fid).first();
        if (!f) return json(request, { ok: false, error: '找不到這個檔案' }, 404);
        let b64 = f.content_b64 || '';
        if (!b64 && f.chunks) {
          const { results } = await env.DB.prepare(
            `SELECT b64 FROM file_chunks WHERE file_id = ? ORDER BY idx ASC`).bind(fid).all();
          b64 = (results || []).map((r) => r.b64).join('');
        }
        if (!b64) return json(request, { ok: false, error: '檔案內容是空的' }, 404);
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        return new Response(bin, { headers: {
          'content-type': f.mime || 'application/octet-stream',
          // filename* 用 RFC5987 編碼，中文檔名才不會在下載時變亂碼或被截掉
          'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(f.filename || 'resume.pdf')}`,
          'cache-control': 'private, no-store',
        } });
      }

      // ── pipeline 卡片點開後要看到的全部東西 ──
      // 2026-08-26 加。在這之前顧問在 pipeline 上只看得到姓名跟卡幾天，
      // 要知道這人是誰得跳去「初篩報告」頁再找一次——換頁就等於中斷，
      // 而這頁的用途就是「一件一件掃過去」。所以這裡一次把該知道的都給：
      // 聯絡方式、履歷、初篩報告全文、推給了哪幾家、每一家談到哪一關。
      if (p === '/admin/pipeline/detail' && request.method === 'GET') {
        const kind = (url.searchParams.get('kind') || '').trim();
        const id = (url.searchParams.get('id') || '').trim();
        if (!id) return json(request, { ok: false, error: '缺 id' }, 400);

        if (kind === 'sourced') {
          const row = await env.DB.prepare(
            `SELECT s.*, (SELECT display_name FROM consultants c WHERE c.id = s.owner) AS owner_name
               FROM sourced_candidates s WHERE s.id = ?`).bind(id).first();
          if (!row) return json(request, { ok: false, error: '找不到' }, 404);
          // 他後來變成應徵者了嗎？有的話把面談那一側的資料一起帶回來——
          // 同一個人，顧問不該為了看他的履歷或報告再跳去另一頁找一次。
          let linked = null;
          if (row.converted_application_id) {
            linked = await env.DB.prepare(
              `SELECT a.id, a.job_slug, a.job_title, a.email, a.phone,
                      a.resume_file_id, a.resume_url, a.resume_104_url,
                      a.interview_state, a.interview_ended_at, a.chat_token,
                      (SELECT r.id FROM reports r WHERE r.application_id = a.id
                        ORDER BY r.created_at DESC LIMIT 1) AS report_id,
                      (SELECT r.consultant_decision FROM reports r WHERE r.application_id = a.id
                        ORDER BY r.created_at DESC LIMIT 1) AS consultant_decision
                 FROM applications a WHERE a.id = ?`).bind(row.converted_application_id).first();
          }
          return json(request, { ok: true, kind, sourced: row, linked });
        }

        // application / placement 都收斂成「一位人選的完整檔案」。
        // placement 的 id 是數字，先換成 application_id 再走同一條路。
        let appId = id;
        if (kind === 'placement') {
          const pr = await env.DB.prepare(
            `SELECT application_id FROM placements WHERE id = ?`).bind(id).first();
          if (!pr || !pr.application_id) {
            // 顧問自己找的人可能沒有 application（阿財沒談過），只有送件紀錄。
            const only = await env.DB.prepare(`SELECT * FROM placements WHERE id = ?`).bind(id).first();
            if (!only) return json(request, { ok: false, error: '找不到' }, 404);
            return json(request, { ok: true, kind, application: null, report: null,
                                   placements: [only], forwards: [] });
          }
          appId = pr.application_id;
        }

        const app = await env.DB.prepare(
          `SELECT a.id, a.created_at, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.expected_salary, a.available_date, a.location_ok, a.note,
                  a.resume_url, a.resume_file_id, a.resume_extra_files, a.resume_url_text, a.social_links,
                  a.status, a.interview_state, a.interview_mode,
                  a.interview_started_at, a.interview_ended_at,
                  a.disc_d, a.disc_i, a.disc_s, a.disc_c, a.disc_primary,
                  a.owner, a.utm_source, a.utm_campaign,
                  (SELECT display_name FROM consultants c WHERE c.id = a.owner) AS owner_name,
                  j.title AS job_full_title
             FROM applications a
             LEFT JOIN jobs j ON j.slug = a.job_slug
            WHERE a.id = ?`).bind(appId).first();
        if (!app) return json(request, { ok: false, error: '找不到' }, 404);

        // 初篩報告全文。顧問點卡片最想看的就是這個——不給就等於還是要跳頁。
        const report = await env.DB.prepare(
          `SELECT id, created_at, content_md, content_json, consultant_decision, decided_at
             FROM reports WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
        ).bind(appId).first();

        const { results: forwards } = await env.DB.prepare(
          `SELECT cf.id, cf.company_id, cf.forwarded_at, cf.forwarded_by, cf.note,
                  cf.manual_stage, cf.manual_stage_at, cf.manual_stage_note, cf.client_interview_labels,
                  cf.line_id_given, cf.client_note,
                  cc.display_name AS company_name
             FROM candidate_forwards cf
             LEFT JOIN client_companies cc ON cc.id = cf.company_id
            WHERE cf.application_id = ? ORDER BY cf.forwarded_at ASC`).bind(appId).all();

        const { results: placements } = await env.DB.prepare(
          `SELECT id, job_slug, job_title, client_name, stage, stage_since,
                  onboard_date, note, close_reason_internal, owner, updated_at
             FROM placements WHERE application_id = ? ORDER BY id ASC`).bind(appId).all();

        // 每一家客戶各自的進度條。跟客戶入口、候選人 LINE 走同一顆
        // resolveStage()，三邊看到的絕不會兜不起來。
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id = ? ORDER BY stage ASC, created_at ASC`).bind(appId).all();
        const stages = [];
        const singleJobStageKeys = await getJobStageKeys(env, app.job_slug);
        for (const f of (forwards || [])) {
          let lbs = {};
          try { lbs = JSON.parse(f.client_interview_labels || '{}'); } catch { lbs = {}; }
          const pm = (placements || []).find((x) => x.job_slug === app.job_slug) || (placements || [])[0] || null;
          const info = resolveStage({ ...app, manual_stage: f.manual_stage },
                                    { consultant_decision: report ? report.consultant_decision : null },
                                    appts || [], pm, lbs, singleJobStageKeys);
          const cur = info.effective_index >= 0 ? info.steps[info.effective_index] : null;
          stages.push({ company_id: f.company_id, company_name: f.company_name,
                        forwarded_at: f.forwarded_at,
                        key: cur ? cur.key : null, label: cur ? cur.lb : null,
                        manual_active: info.manual_active, steps: info.steps,
                        line_id_given: f.line_id_given, client_note: f.client_note,
                        manual_stage_note: f.manual_stage_note });
        }

        return json(request, { ok: true, kind, application: app, report: report || null,
                               forwards: forwards || [], placements: placements || [], stages });
      }

      // ── 這個人選在其他職缺／客戶的紀錄（跨 application_id）──────────
      // candidate_forwards 是綁在單一 application_id 上的——同一個人隔幾週
      // 投別的職缺，系統會開一筆全新、互不相干的 applications，兩筆之間
      // 沒有任何欄位接起來。這支端點用 email／電話比對（跟 sourced_candidates
      // 轉換應徵者時同一套邏輯）把同一個真人的其他應徵記錄找出來。
      //
      // ⚠️ 這是內部限定端點，只給 /admin/ 前綴、顧問後台用。絕對不能被
      // /portal/ 任何路徑呼叫——A 客戶不能知道這個人選也被推薦去 B 客戶。
      if (p === '/admin/pipeline/candidate-history' && request.method === 'GET') {
        const email = (url.searchParams.get('email') || '').trim().toLowerCase();
        const phoneRaw = (url.searchParams.get('phone') || '').trim();
        const excludeId = url.searchParams.get('exclude_application_id') || '';
        if (!email && !phoneRaw) return json(request, { ok: true, history: [] });

        const phoneTail = phoneRaw.replace(/\D/g, '').slice(-9);
        let rows = [];
        if (email) {
          const r = await env.DB.prepare(
            `SELECT id FROM applications WHERE LOWER(TRIM(email)) = ? AND id != ?
              ORDER BY created_at DESC LIMIT 20`).bind(email, excludeId).all();
          rows = r.results || [];
        }
        if (phoneTail && rows.length < 20) {
          const r = await env.DB.prepare(
            `SELECT id FROM applications WHERE phone LIKE ? AND id != ?
              ORDER BY created_at DESC LIMIT 20`).bind('%' + phoneTail, excludeId).all();
          for (const x of (r.results || [])) if (!rows.some((y) => y.id === x.id)) rows.push(x);
        }
        if (!rows.length) return json(request, { ok: true, history: [] });

        // ⚠️ 2026-09-01 修：原本對「每一筆」重複應徵紀錄各自序列 await 6 支查詢
        // （最多 20 筆＝約 120 次查詢，含迴圈內的 getJobStageKeys 更多），是這次
        // D1 額度稽核抓到的第三個 N+1。改成先收集這批 application_id，批次撈齊
        // 6 種資料，查詢次數從「O(重複筆數)」降到固定 6 次左右。
        const hIds = rows.slice(0, 20).map((r) => r.id);
        const inList = (n) => `(${Array.from({ length: n }, () => '?').join(',')})`;
        const [hAppRows, hReportRows, hForwardRows, hPlacementRows, hApptRows] = await Promise.all([
          env.DB.prepare(`SELECT * FROM applications WHERE id IN ${inList(hIds.length)}`).bind(...hIds).all().then((r) => r.results || []),
          env.DB.prepare(`SELECT application_id, consultant_decision, created_at FROM reports
              WHERE application_id IN ${inList(hIds.length)} ORDER BY application_id, created_at DESC`).bind(...hIds).all().then((r) => r.results || []),
          env.DB.prepare(`SELECT cf.application_id, cf.company_id, cf.forwarded_at, cf.manual_stage, cf.client_interview_labels,
                  cc.display_name AS company_name
             FROM candidate_forwards cf LEFT JOIN client_companies cc ON cc.id = cf.company_id
            WHERE cf.application_id IN ${inList(hIds.length)} ORDER BY cf.application_id, cf.forwarded_at ASC`).bind(...hIds).all().then((r) => r.results || []),
          env.DB.prepare(`SELECT * FROM placements WHERE application_id IN ${inList(hIds.length)}`).bind(...hIds).all().then((r) => r.results || []),
          env.DB.prepare(`SELECT application_id, stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id IN ${inList(hIds.length)}`).bind(...hIds).all().then((r) => r.results || []),
        ]);
        const hJobSlugs = Array.from(new Set(hAppRows.map((a) => a.job_slug).filter(Boolean)));
        const hJobRows = hJobSlugs.length
          ? (await env.DB.prepare(`SELECT slug, title FROM jobs WHERE slug IN ${inList(hJobSlugs.length)}`).bind(...hJobSlugs).all()).results || []
          : [];
        const hJobTitleBySlug = new Map(hJobRows.map((j) => [j.slug, j.title]));
        const hJobStageKeysMap = await getJobStageKeysMap(env, hJobSlugs);
        const groupByApp = (list) => { const m = new Map(); for (const x of list) { if (!m.has(x.application_id)) m.set(x.application_id, []); m.get(x.application_id).push(x); } return m; };
        const hReportByApp = new Map(); for (const r of hReportRows) if (!hReportByApp.has(r.application_id)) hReportByApp.set(r.application_id, r);
        const hForwardsByApp = groupByApp(hForwardRows);
        const hPlacementsByApp = groupByApp(hPlacementRows);
        const hApptsByApp = groupByApp(hApptRows);

        const history = [];
        for (const hApp of hAppRows) {
          const hReport = hReportByApp.get(hApp.id) || null;
          const hForwards = hForwardsByApp.get(hApp.id) || [];
          const hPlacements = hPlacementsByApp.get(hApp.id) || [];
          const hAppts = hApptsByApp.get(hApp.id) || [];
          const jobTitle = hJobTitleBySlug.get(hApp.job_slug) || hApp.job_slug;
          if (hForwards.length) {
            const hJobStageKeys = hJobStageKeysMap[hApp.job_slug];
            for (const f of hForwards) {
              let lbs = {}; try { lbs = JSON.parse(f.client_interview_labels || '{}'); } catch { lbs = {}; }
              const pm = hPlacements.find((x) => x.job_slug === hApp.job_slug) || hPlacements[0] || null;
              const info = resolveStage({ ...hApp, manual_stage: f.manual_stage },
                { consultant_decision: hReport ? hReport.consultant_decision : null }, hAppts, pm, lbs, hJobStageKeys);
              const cur = info.effective_index >= 0 ? info.steps[info.effective_index] : null;
              history.push({ application_id: hApp.id, job_slug: hApp.job_slug,
                job_title: jobTitle, applied_at: hApp.created_at,
                company_name: f.company_name, forwarded_at: f.forwarded_at,
                stage_label: cur ? cur.lb : null,
                consultant_decision: hReport ? hReport.consultant_decision : null });
            }
          } else {
            history.push({ application_id: hApp.id, job_slug: hApp.job_slug,
              job_title: jobTitle, applied_at: hApp.created_at,
              company_name: null, forwarded_at: null, stage_label: null,
              consultant_decision: hReport ? hReport.consultant_decision : null });
          }
        }
        return json(request, { ok: true, history });
      }

      // ── 顧問手動新增應徵紀錄 ──────────────────────────────────────
      // 顧問常常是自己直接把人選履歷送給客戶，那個人根本沒填過應徵表單、
      // 沒跟阿財面談過——系統原本完全看不到這件事，也沒地方設進度。
      // 這支端點讓顧問在人選卡片上補一筆：幫這個人（不論是人才池裡還沒
      // 轉應徵的，還是已經應徵過別的職缺的）建一筆送去某個職缺的紀錄，
      // 之後就能像正常應徵者一樣，用既有的 applyManualStage() 隨時調整階段、
      // 客戶 portal 也看得到。
      //
      // ⚠️ 個資合規：人才池的人選還沒同意個資利用，直接幫他建應徵紀錄、
      // 推薦給客戶，等於顧問代替本人做了這個決定。這裡強制要求
      // consent_confirmed=true，並把「怎麼取得同意」的說明存進
      // consent_note——這不是走過表單那種書面同意，但至少留下顧問自己
      // 對這件事負責任的紀錄，不是無聲無息就把個資送出去。
      const MANUAL_STAGE_OPTS = ['stage1', 'stage2', 'stage3', 'stage4', 'offer', 'onboard', 'care', 'backup'];
      if (p === '/admin/pipeline/manual-forward' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const result = await doManualForward(env, b);
        return json(request, result.body, result.status);
      }

      // ── 接觸紀錄 ──
      // 2026-08-26 接起來。outreach 這張表建好之後從來沒有任何程式寫過它、
      // 也沒有頁面讀過它——「今天寄了 10 個系所」這種事只能存在訊息裡，
      // 兩週後沒人記得寄過誰、誰回了、回覆率多少。
      //
      // ⚠️ 這裡跟 bd_outreach 是兩件不同的事，不要合併：
      //   bd_outreach ＝ 把某位人選推銷給企業（找客戶）
      //   outreach    ＝ 為了某個職缺去接觸誰（找人選的管道，含學校系所）
      // 方向相反，統計意義也不同。
      if (p === '/admin/outreach' && request.method === 'GET') {
        const job = (url.searchParams.get('job') || '').trim();
        const type = (url.searchParams.get('type') || '').trim();
        const w = [], b = [];
        if (job) { w.push('job_slug = ?'); b.push(job); }
        if (type) { w.push('target_type = ?'); b.push(type); }
        const where = w.length ? ' WHERE ' + w.join(' AND ') : '';
        const { results } = await env.DB.prepare(
          `SELECT o.*, j.title AS job_title,
                  (SELECT display_name FROM consultants c WHERE c.id = o.owner) AS owner_name
             FROM outreach o LEFT JOIN jobs j ON j.slug = o.job_slug
             ${where.replace('WHERE', 'WHERE')}
            ORDER BY o.sent_at DESC, o.id DESC LIMIT 500`
        ).bind(...b).all();
        const rows = results || [];
        // 回覆率的分母只算「發出去且已經等夠久」的。剛寄出的當天就算進分母，
        // 數字會被稀釋成沒有意義的低值。
        const replied = rows.filter((r) => r.outcome === 'replied' || r.outcome === 'meeting').length;
        const closed = rows.filter((r) => r.outcome !== 'sent').length;
        return json(request, { ok: true, rows,
          stats: { total: rows.length, replied, closed, waiting: rows.length - closed,
                   // ⚠️ 樣本不到 100 封不給回覆率——這是 Jacky 定的門檻，
                   // 10 封的 0% 跟 10 封的 20% 都不能拿來下任何結論。
                   reply_rate: rows.length >= 100 ? Math.round((replied / rows.length) * 100) : null } });
      }

      if (p === '/admin/outreach' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const TYPES = ['school', 'candidate', 'client', 'intro'];
        const tt = TYPES.includes(String(b.target_type || '')) ? String(b.target_type) : null;
        if (!tt || !b.target_name) return json(request, { ok: false, error: '缺 target_type 或 target_name' }, 400);
        const now = nowTaipei();
        const r = await env.DB.prepare(
          `INSERT INTO outreach (target_type,target_name,company,job_slug,channel,sent_at,outcome,
                                 contact_email,ext_id,region,subject,sent_by,owner,note,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
        ).bind(tt, String(b.target_name).slice(0, 120), b.company || null, b.job_slug || null,
               b.channel || 'email', b.sent_at || now, b.outcome || 'sent',
               b.contact_email || null, b.ext_id || null, b.region || null,
               b.subject || null, b.sent_by || null, b.owner || null,
               b.note || null, now).run();
        return json(request, { ok: true, id: r.meta && r.meta.last_row_id });
      }

      if (p === '/admin/outreach/outcome' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const OK = ['todo', 'sent', 'replied', 'no_reply', 'declined', 'meeting'];
        if (!b.id || !OK.includes(String(b.outcome || ''))) {
          return json(request, { ok: false, error: '參數錯誤', outcomes: OK }, 400);
        }
        await env.DB.prepare(
          `UPDATE outreach SET outcome=?,
                  replied_at = CASE WHEN ? IN ('replied','meeting') THEN COALESCE(replied_at, ?) ELSE replied_at END,
                  note = COALESCE(?, note)
            WHERE id=?`
        ).bind(String(b.outcome), String(b.outcome), nowTaipei(),
               b.note ? String(b.note).slice(0, 500) : null, b.id).run();
        return json(request, { ok: true });
      }

      // ── 指定負責顧問 ──
      // 一支端點吃三張表，因為顧問在畫面上不會分「這是 sourced 還是 placement」，
      // 他只是在某一列上選一個人名。
      if (p === '/admin/set-owner' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const TB = { sourced: 'sourced_candidates', application: 'applications', placement: 'placements' };
        const tb = TB[String(b.kind || '')];
        if (!tb || !b.id) return json(request, { ok: false, error: '參數錯誤' }, 400);
        const ow = b.owner ? String(b.owner) : null;
        if (ow) {
          const c = await env.DB.prepare(`SELECT 1 FROM consultants WHERE id=? AND is_active=1`).bind(ow).first();
          if (!c) return json(request, { ok: false, error: '找不到這位顧問（或已停用）' }, 400);
        }
        await env.DB.prepare(`UPDATE ${tb} SET owner=? WHERE id=?`).bind(ow, String(b.id)).run();
        return json(request, { ok: true });
      }

      // ── 產一條專屬應徵連結給人選 ──
      // 顧問先找到人、先幫他把履歷傳好，然後給他這條連結。人選點進去，
      // 職缺跟履歷都已經在裡面，只剩測驗要自己填，填完直接跟阿財面談。
      //
      // 為什麼不是叫他自己去官網投：那樣他要重打一次基本資料、重傳一次履歷，
      // 而且十之八九會投錯職缺（同名職缺有兩個）。每多一個步驟就掉一批人。
      if (p === '/admin/sourced/invite' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.id || !b.job_slug) return json(request, { ok: false, error: '缺 id 或 job_slug' }, 400);
        const row = await env.DB.prepare(
          `SELECT id, name, email, invite_token FROM sourced_candidates WHERE id = ?`).bind(String(b.id)).first();
        if (!row) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const job = await env.DB.prepare(
          `SELECT slug, title, COALESCE(status,'open') AS status FROM jobs WHERE slug = ?`
        ).bind(String(b.job_slug)).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        // 已關閉的職缺不給發邀請——連結寄出去了，人選點進來卻被 /apply 擋掉，
        // 那個體驗比沒收到連結還糟。
        if (job.status === 'closed') {
          return json(request, { ok: false, error: '這個職缺已經結束招募，不能發邀請連結' }, 400);
        }
        // 連結本身就是憑證，用 crypto 產生。重發時沿用同一組——
        // 換一組的話，先前傳給人選的連結會直接失效，而顧問通常不知道。
        let token = row.invite_token;
        if (!token || b.regen) {
          token = [...crypto.getRandomValues(new Uint8Array(18))]
            .map((x) => x.toString(16).padStart(2, '0')).join('');
        }
        await env.DB.prepare(
          `UPDATE sourced_candidates SET invite_token=?, invite_job_slug=?, invite_at=? WHERE id=?`
        ).bind(token, job.slug, nowTaipei(), row.id).run();
        return json(request, { ok: true, token, job_title: job.title,
                               url: `https://step1ne.com/apply/?inv=${token}` });
      }

      // ── 顧問幫人才池的人上傳履歷 ──
      // 顧問常常先拿到履歷才有機會接觸（對方 email 寄來、104 下載、介紹人給的），
      // 這時候他還不是應徵者。以檔案為主、連結為輔——104 與雲端連結會過期，
      // 檔案不會；等他真的來面談時，阿財看得到的是檔案那一份。
      if (p === '/admin/sourced/resume' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.id) return json(request, { ok: false, error: '缺 id' }, 400);
        const now = nowTaipei();
        const sets = ['resume_at = ?'], bind = [now];
        if (b.pdf_b64) {
          const saved = await saveUpload(env, {
            b64: b.pdf_b64, name: b.filename || `履歷_${b.id}.pdf`,
            mime: b.mime || 'application/pdf' }, now);
          if (!saved || saved.tooBig) return json(request, { ok: false, error: '檔案太大存不下' }, 400);
          sets.push('resume_file_id = ?'); bind.push(saved.fileId);
        }
        if (b.resume_url !== undefined) { sets.push('resume_url = ?'); bind.push(b.resume_url || null); }
        if (b.resume_note !== undefined) { sets.push('resume_note = ?'); bind.push(b.resume_note ? String(b.resume_note).slice(0, 500) : null); }
        if (sets.length === 1) return json(request, { ok: false, error: '沒有給檔案也沒有給連結' }, 400);
        bind.push(String(b.id));
        const r = await env.DB.prepare(
          `UPDATE sourced_candidates SET ${sets.join(', ')} WHERE id = ?`).bind(...bind).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        return json(request, { ok: true });
      }

      // ── 直接修人選卡片上的欄位 ──
      // 2026-08-27 加。爬回來的資料常常有錯或缺（公司名帶著括號註解、
      // 職稱是英文原文、電話要顧問打完才知道），但顧問在後台完全沒有地方能改，
      // 只能眼睜睜看著錯的資料一直躺在那裡，或是回頭叫工程師改資料庫。
      //
      // ⚠️ 白名單制。raw_json／score／grade／status／owner 這些不給從這裡改：
      //    前三個是爬蟲與評分的產出，手改會讓「AI 判斷準不準」的統計失真；
      //    status 有自己的端點（含婉拒必填原因），owner 也有自己的端點。
      if (p === '/admin/sourced/update' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.id) return json(request, { ok: false, error: '缺 id' }, 400);
        const OK = { name: 80, headline: 200, company: 120, location: 120,
                     email: 120, phone: 40,
                     linkedin_url: 300, github_url: 300, source_url: 500,
                     other_links: 2000, skills: 500, note: 2000 };
        const sets = [], bind = [];
        for (const k of Object.keys(OK)) {
          if (b[k] === undefined) continue;
          sets.push(`${k} = ?`);
          bind.push(b[k] === null || b[k] === '' ? null : String(b[k]).slice(0, OK[k]));
        }
        if (!sets.length) return json(request, { ok: false, error: '沒有要改的欄位' }, 400);
        bind.push(String(b.id));
        const r = await env.DB.prepare(
          `UPDATE sourced_candidates SET ${sets.join(', ')} WHERE id = ?`).bind(...bind).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        return json(request, { ok: true, fields: sets.length });
      }

      // 應徵者的可改欄位。比人才池窄很多——這是本人自己填的資料，
      // 顧問只該修正明顯的輸入錯誤（電話少一碼、信箱打錯字），不是重寫他的申請。
      // 期望待遇／可到職這類會影響判斷的欄位也開放，因為電訪之後常有更新。
      if (p === '/admin/application/update' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.id) return json(request, { ok: false, error: '缺 id' }, 400);
        const OK = { name: 80, email: 120, phone: 40, expected_salary: 120,
                     available_date: 120, location_ok: 200, resume_url: 500, note: 2000,
                     source_channel: 60, resume_source: 40 };
        const sets = [], bind = [];
        for (const k of Object.keys(OK)) {
          if (b[k] === undefined) continue;
          sets.push(`${k} = ?`);
          bind.push(b[k] === null || b[k] === '' ? null : String(b[k]).slice(0, OK[k]));
        }
        if (!sets.length) return json(request, { ok: false, error: '沒有要改的欄位' }, 400);
        bind.push(String(b.id));
        const r = await env.DB.prepare(
          `UPDATE applications SET ${sets.join(', ')} WHERE id = ?`).bind(...bind).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        return json(request, { ok: true, fields: sets.length });
      }

      // ── 人選履歷：最多 3 份（例如中文版＋英文版＋作品集）──
      // applications.resume_file_id 是原本唯一的「主要履歷」欄位，全站 37 處
      // 都在讀它，不能動——這裡新增的 resume_extra_files（JSON 陣列）只放
      // 「額外」的份數，第一份還是走 resume_file_id 那條舊路徑，兩者合計上限 3。
      if (p === '/admin/application/resume/add' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        if (!b.b64) return json(request, { ok: false, error: '缺檔案內容' }, 400);
        const app = await env.DB.prepare(
          `SELECT resume_file_id, resume_extra_files FROM applications WHERE id=?`).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        let extra = [];
        try { extra = JSON.parse(app.resume_extra_files || '[]'); } catch { extra = []; }
        const total = (app.resume_file_id ? 1 : 0) + extra.length;
        if (total >= 3) return json(request, { ok: false, error: '履歷最多 3 份，請先刪除一份再上傳' }, 400);
        const now = nowTaipei();
        const saved = await saveUpload(env, { b64: b.b64, name: b.name, mime: b.mime }, now);
        if (saved && saved.tooBig) return json(request, { ok: false, error: '檔案太大，請壓縮後再上傳' }, 400);
        if (!saved) return json(request, { ok: false, error: '存檔失敗' }, 500);
        if (!app.resume_file_id) {
          // 主要履歷欄位是空的，這份直接補進去，不用另外佔 resume_extra_files 的名額——
          // 跟「先選來源再上傳」那個示範版行為一致：第一份永遠走主欄位。
          await env.DB.prepare(`UPDATE applications SET resume_file_id=? WHERE id=?`)
            .bind(saved.fileId, b.application_id).run();
        } else {
          extra.push({ file_id: saved.fileId, filename: b.name || 'resume', uploaded_at: now });
          await env.DB.prepare(`UPDATE applications SET resume_extra_files=? WHERE id=?`)
            .bind(JSON.stringify(extra), b.application_id).run();
        }
        return json(request, { ok: true, file_id: saved.fileId });
      }
      if (p === '/admin/application/resume/remove' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id || !b.file_id) return json(request, { ok: false, error: '缺 application_id 或 file_id' }, 400);
        const app = await env.DB.prepare(
          `SELECT resume_file_id, resume_extra_files FROM applications WHERE id=?`).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        if (app.resume_file_id === b.file_id) {
          await env.DB.prepare(`UPDATE applications SET resume_file_id=NULL WHERE id=?`).bind(b.application_id).run();
        } else {
          let extra = [];
          try { extra = JSON.parse(app.resume_extra_files || '[]'); } catch { extra = []; }
          extra = extra.filter((f) => f.file_id !== b.file_id);
          await env.DB.prepare(`UPDATE applications SET resume_extra_files=? WHERE id=?`)
            .bind(JSON.stringify(extra), b.application_id).run();
        }
        // 不刪 files/file_chunks 那份原始檔——跟既有「顧問上傳的原始檔一律保留」
        // 慣例一致（見上面 clientNameTerms 附近註解），只是不再掛在這個人選底下。
        return json(request, { ok: true });
      }

      // ── 電洽內容：補在既有人選卡片上，走跟「新增人選」建立時完全同一條
      // pipeline（consultant_call_notes/call_report_pending/call_notes_file_id
      // 這三個欄位，本機 consultant_call_report_tick.py 排程會撈去產報告）。
      // 之前這三個欄位只有在 /admin/pipeline/manual-forward 建立新人選那一刻
      // 才寫得進去——已經存在的人選想補一次電訪紀錄完全沒有入口，這支端點補上。
      // 同一個人可能被打過好幾次電話，所以每次呼叫是「追加」，不是「覆蓋」：
      // 舊的電訪內容會保留在 note 欄位當歷史紀錄，新的一段接在後面觸發重新產報告。
      // 2026-09-02 改：核心邏輯抽成 doCallNote()（跟 doManualForward 同一個
      // 理由——TG bot 的 ncSubmitMerge() 原本用 fetch() 打自己這個 Worker 的
      // 公開網址，被 Cloudflare error 1042 擋掉，導致「同一人整合進舊紀錄」
      // 這條路徑一直失敗、還顯示看不懂的「不知道為什麼」），這裡改成呼叫共用函式。
      if (p === '/admin/application/call-note' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const r = await doCallNote(env, b);
        return json(request, r.body, r.status);
      }

      // ⚠️ 2026-09-01 加：補上面那個 bug 修好之前，已經卡在「有附件、沒有即時彙整」
      // 狀態的舊資料（例如周丞恩）——不用叫顧問重傳一次檔案（會多打一筆歷史紀錄、
      // 也會多觸發一次本機排程重跑），直接讀已存的附件重新產生 call_summary_md。
      // 一次性補資料用途，之後有其他卡住的舊案子也能重複呼叫。
      if (p === '/admin/application/call-note/backfill-summary' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const app = await env.DB.prepare(
          `SELECT call_notes_file_id, consultant_call_notes FROM applications WHERE id=?`
        ).bind(b.application_id).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        let textForAi = (app.consultant_call_notes || '').trim();
        if (!textForAi && app.call_notes_file_id) {
          const f = await fileB64(env, app.call_notes_file_id);
          if (f) {
            try {
              const bytes = Uint8Array.from(atob(f.content), (c) => c.charCodeAt(0));
              const md = await env.AI.toMarkdown([{ name: f.filename, blob: new Blob([bytes], { type: f.mime }) }]);
              textForAi = (md && md[0] && md[0].data) ? String(md[0].data).trim() : '';
            } catch (e) { textForAi = ''; }
          }
        }
        if (!textForAi) return json(request, { ok: false, error: '沒有可用的電訪文字或附件' }, 400);
        let callSummaryMd = null;
        try {
          const ai = await env.AI.run('@cf/meta/llama-3.3-70b-instruct-fp8-fast', {
            messages: [
              { role: 'system', content: '你是獵頭顧問的助理，把顧問打的電訪筆記整理成重點條列，不要新增筆記裡沒提到的資訊，沒提到的欄位就寫「未提及」。用繁體中文回答，直接輸出，不要開場白。' },
              { role: 'user', content: `請把下面這段電訪筆記整理成這六個標題各一段（每段2-3行以內）：\n重點狀況\n求職需求\n期望薪資\n離職原因\n優勢與劣勢\n顧問可再確認／可主動告知客戶的部分\n\n電訪筆記：\n${textForAi.slice(0, 4000)}` },
            ],
          });
          callSummaryMd = (ai && (ai.response || ai.result)) ? String(ai.response || ai.result).trim() : null;
        } catch (e) {
          return json(request, { ok: false, error: '彙整產生失敗' }, 500);
        }
        await env.DB.prepare(`UPDATE applications SET call_summary_md=? WHERE id=?`)
          .bind(callSummaryMd, b.application_id).run();
        return json(request, { ok: true, call_summary_md: callSummaryMd });
      }

      // 2026-09-01 加：人選卡片改版——③顧問備註區塊，顧問自己選類型（評估／
      // 聯繫紀錄／客戶回饋／其他）寫一條紀錄，不是單一文字欄位覆蓋，累積成一條
      // 一條的清單。跟④電洽內容共用同一張 candidate_notes 表，差別只在 type。
      if (p === '/admin/application/notes' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const content = String(b.content || '').trim();
        if (!content) return json(request, { ok: false, error: '請填內容' }, 400);
        const type = String(b.type || '其他').slice(0, 20);
        const now = nowTaipei();
        await env.DB.prepare(
          `INSERT INTO candidate_notes (id, application_id, type, content, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)`
        ).bind(uid(), b.application_id, type, content.slice(0, 2000), now, b.by || null).run();
        return json(request, { ok: true });
      }

      // 2026-09-01 加：人選卡片改版——整張卡一次拿齊，前端不用打好幾支 API
      // 才拼得出畫面。回傳：基本資料、推薦紀錄（各自的職缺/公司/進度）、
      // 顧問備註+電洽紀錄清單、給每個推薦客戶各自的備註、還有把這幾種來源
      // 兜起來的歷史時間軸（⑥），一次全部給前端，前端只管排版跟篩選。
      if (p.startsWith('/admin/application/') && p.endsWith('/card') && request.method === 'GET') {
        const appId = decodeURIComponent(p.slice('/admin/application/'.length, -'/card'.length));
        const app = await env.DB.prepare(`SELECT * FROM applications WHERE id=?`).bind(appId).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        delete app.chat_token;
        const report = await env.DB.prepare(
          `SELECT id, consultant_decision FROM reports WHERE application_id=? ORDER BY created_at DESC LIMIT 1`
        ).bind(appId).first();
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id=? ORDER BY stage ASC, created_at ASC`
        ).bind(appId).all();
        const { results: fwdRows } = await env.DB.prepare(
          `SELECT cf.company_id, cc.display_name AS company_name, cf.forwarded_at,
                  cf.manual_stage, cf.client_interview_labels, cf.client_note,
                  cf.client_rejected_at, cf.client_reject_reason,
                  cf.advisor_not_recommended_at, cf.advisor_not_recommended_reason,
                  COALESCE(cf.job_slug, ?) AS job_slug, j.title AS job_title
             FROM candidate_forwards cf
             JOIN client_companies cc ON cc.id = cf.company_id
             LEFT JOIN jobs j ON j.slug = COALESCE(cf.job_slug, ?)
            WHERE cf.application_id = ? ORDER BY cf.forwarded_at ASC`
        ).bind(app.job_slug, app.job_slug, appId).all();
        const jobStageKeysMap = await getJobStageKeysMap(env, (fwdRows || []).map((f) => f.job_slug));
        const forwards = (fwdRows || []).map((f) => {
          let labels = {};
          try { labels = JSON.parse(f.client_interview_labels || '{}'); } catch { labels = {}; }
          const info = resolveStage({ ...app, manual_stage: f.manual_stage }, report, appts || [], null, labels, jobStageKeysMap[f.job_slug]);
          return {
            company_id: f.company_id, company_name: f.company_name, job_slug: f.job_slug, job_title: f.job_title,
            forwarded_at: f.forwarded_at, client_note: f.client_note,
            client_rejected_at: f.client_rejected_at, client_reject_reason: f.client_reject_reason,
            advisor_not_recommended_at: f.advisor_not_recommended_at, advisor_not_recommended_reason: f.advisor_not_recommended_reason,
            stage_key: info.effective_key,
            stage_label: info.effective_key ? (info.steps.find((s) => s.key === info.effective_key) || {}).lb : '尚未安排面試',
            steps: info.steps,
          };
        });
        const { results: notes } = await env.DB.prepare(
          `SELECT id, type, content, created_at, created_by FROM candidate_notes
            WHERE application_id=? ORDER BY created_at DESC`
        ).bind(appId).all();
        // ⑥ 歷史時間軸：顧問備註/電洽紀錄（candidate_notes）＋推薦紀錄（candidate_forwards
        // 建立時間）＋客戶婉拒／顧問不推薦（candidate_forwards 的時間戳欄位）兜在一起，
        // 前端只管依 type 篩選跟排序顯示，不用自己合併好幾個來源。
        const timeline = [];
        (notes || []).forEach((n) => timeline.push({ time: n.created_at, type: n.type === '電洽紀錄' ? '電洽紀錄' : '顧問備註', detail: (n.type !== '電洽紀錄' ? '[' + n.type + '] ' : '') + n.content }));
        // 2026-09-01 加：⑥ 之前完全沒有把「跟阿財面談」這件事本身放進時間軸——
        // 一個剛完訪、還沒被顧問留備註、也還沒推薦給任何客戶的人選，時間軸會是
        // 空的，看起來像什麼都沒發生過，實際上面談紀錄整個在別的區塊（初篩報告）。
        if (app.interview_started_at) timeline.push({ time: app.interview_started_at, type: '阿財面談', detail: '開始與阿財面談' });
        if (app.interview_ended_at) timeline.push({ time: app.interview_ended_at, type: '阿財面談', detail: '完成與阿財面談' });
        if (report && app.created_at) timeline.push({ time: app.created_at, type: '阿財面談', detail: '建立應徵紀錄' });
        forwards.forEach((f) => {
          timeline.push({ time: f.forwarded_at, type: '推薦紀錄', detail: `推薦給 ${f.company_name}・${f.job_title || f.job_slug}` });
          if (f.client_rejected_at) timeline.push({ time: f.client_rejected_at, type: '客戶婉拒', detail: `${f.company_name}婉拒「${f.job_title || f.job_slug}」，原因：${f.client_reject_reason || '（未填）'}` });
          if (f.advisor_not_recommended_at) timeline.push({ time: f.advisor_not_recommended_at, type: '客戶婉拒', detail: `顧問標記不推薦給${f.company_name}「${f.job_title || f.job_slug}」，原因：${f.advisor_not_recommended_reason || '（未填）'}` });
        });
        timeline.sort((a, b2) => (a.time < b2.time ? 1 : -1));
        return json(request, { ok: true, application: app, forwards, notes: notes || [], timeline });
      }

      // 2026-09-01 加：發應徵連結給他——讓「正式應徵」／「顧問履歷庫」的人選
      // 重新跟阿財面談一次。原本的 chat_token 一旦 interview_state='done' 就
      // 永久鎖死（/chat/:token/send 會回 409），/admin/resend-interview-link
      // 只是重寄同一個已經鎖死的 token，對這種情況完全沒用。這裡仿照 /apply
      // 建立應徵紀錄那段邏輯，另外開一筆新的 applications（保留舊資料/舊報告
      // 不動），產生一個全新、乾淨的 chat_token 給顧問拿去發。
      if (p.startsWith('/admin/application/') && p.endsWith('/resend-interview-link') && request.method === 'POST') {
        const appId = decodeURIComponent(p.slice('/admin/application/'.length, -'/resend-interview-link'.length));
        const b = await request.json().catch(() => ({}));
        const orig = await env.DB.prepare(`SELECT * FROM applications WHERE id=?`).bind(appId).first();
        if (!orig) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const jobSlug = String(b.job_slug || orig.job_slug || '').trim();
        if (!jobSlug) return json(request, { ok: false, error: '缺職缺' }, 400);
        const job = await env.DB.prepare(`SELECT slug, title FROM jobs WHERE slug=?`).bind(jobSlug).first();
        if (!job) return json(request, { ok: false, error: '找不到這個職缺' }, 404);

        const now = nowTaipei();
        const newId = uid();
        const chatToken = [...crypto.getRandomValues(new Uint8Array(24))]
          .map((x) => x.toString(16).padStart(2, '0')).join('');
        await env.DB.prepare(
          `INSERT INTO applications
             (id, created_at, job_slug, job_title, name, email, phone,
              expected_salary, available_date, location_ok,
              resume_file_id, resume_url, note, status, consent_at,
              interview_mode, interview_state, chat_token, handled_by, handled_note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_assessment',?, 'now','not_started',?,?,?)`
        ).bind(newId, now, jobSlug, job.title, orig.name, orig.email, orig.phone,
               orig.expected_salary, orig.available_date, orig.location_ok,
               orig.resume_file_id, orig.resume_url,
               `顧問重新發送面談連結，沿用原應徵資料（原紀錄 ${appId}）`,
               orig.consent_at || now, chatToken, b.by || null,
               `由顧問手動重新發送面談連結（${b.by || '顧問'}）`).run();

        return json(request, {
          ok: true, application_id: newId, chat_token: chatToken,
          url: `https://step1ne.com/interview/?t=${chatToken}`,
        });
      }

      // GET /admin/application/:id/client-formal-prefill — 「製作客戶版履歷」彈窗
      // 打開時先預填的資料。只回傳已經過白名單＋逐句過濾的欄位（跟 client.html
      // 客戶版同一套規則），「相關經驗」「交通工具」「現況」目前面談規格沒有收集
      // 這三格，回傳 null 讓顧問自己在彈窗手動補——不要用其他欄位瞎猜著填。
      if (p.startsWith('/admin/application/') && p.endsWith('/client-formal-prefill') && request.method === 'GET') {
        const appId = decodeURIComponent(p.slice('/admin/application/'.length, -'/client-formal-prefill'.length));
        // ⚠️ 2026-09-02 修：原本只 JOIN a.job_slug（application 建立當下的那個，
        // TG「暫時沒有推薦的職缺」流程一律是 'unspecified'），完全沒看
        // candidate_forwards——顧問後來在①目前進度真的推薦給某家客戶／職缺之後，
        // 這裡還是顯示「尚未指定職缺」「未綁客戶」。改成優先抓 candidate_forwards
        // 的 job_slug／company_id，沒有才退回 applications 自己的。
        // ⚠️ 2026-09-02 再修：一個人選可以同時推薦給好幾家客戶，各自獨立進度——
        // 原本寫死抓「最新一筆」，顧問想幫「非最新那家」做客戶版履歷完全沒辦法。
        // 加 ?company_id= 讓前端指定要哪一筆；沒帶就退回最新一筆（維持舊行為），
        // 同時把全部候選（forwards）都回傳，前端才能畫出選單。
        const wantCompanyId = (url.searchParams.get('company_id') || '').trim();
        const { results: fwdOptions } = await env.DB.prepare(
          `SELECT cf.company_id, cf.job_slug, cc.display_name AS client_name, j.title AS job_title, cf.forwarded_at
             FROM candidate_forwards cf
             LEFT JOIN client_companies cc ON cc.id = cf.company_id
             LEFT JOIN jobs j ON j.slug = cf.job_slug
            WHERE cf.application_id = ? ORDER BY cf.forwarded_at DESC`
        ).bind(appId).all();
        const forwards = (fwdOptions || []).map((f) => ({
          company_id: f.company_id, client_name: f.client_name || '（未知客戶）',
          job_slug: f.job_slug, job_title: f.job_title || f.job_slug,
        }));
        const chosen = wantCompanyId ? forwards.find((f) => f.company_id === wantCompanyId) : forwards[0];
        const app = await env.DB.prepare(
          `SELECT a.name, a.expected_salary, a.available_date, a.job_slug, a.job_title
             FROM applications a WHERE a.id = ?`
        ).bind(appId).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const jobSlug = (chosen && chosen.job_slug) || app.job_slug;
        const job = jobSlug
          ? await env.DB.prepare(`SELECT title, client_named, company_id FROM jobs WHERE slug=?`).bind(jobSlug).first()
          : null;
        app.job_full_title = (chosen && chosen.job_title) || (job && job.title) || app.job_title;
        app.client_name = (chosen && chosen.client_name) || '（未綁客戶）';
        app.client_named = job && job.client_named;
        const report = await env.DB.prepare(
          `SELECT content_json FROM reports WHERE application_id=? ORDER BY created_at DESC LIMIT 1`
        ).bind(appId).first();
        let data = {};
        try { data = report && report.content_json ? JSON.parse(report.content_json) : {}; } catch { data = {}; }
        const basics = (data && typeof data.basics === 'object' && data.basics) || {};
        const clientNamed = app.client_named;
        const name = clientDisplayName(app.name, clientNamed);
        const anon = isAnonymous(clientNamed);
        const txt = (v) => {
          const s = scrubForClient(v);
          return anon ? String(s || '').split(String(app.name || ' ')).join(name) : s;
        };
        const fc = data.for_client || {};
        return json(request, {
          ok: true,
          name, anonymous: anon,
          job_title: app.job_full_title || app.job_title || '',
          client_name: app.client_name || '（未綁客戶）',
          residence: basics.residence || null,
          age: anon ? null : (basics.age || null),
          education: basics.education || null,
          languages: basics.languages || null,
          certificates: basics.certificates || null,
          military: basics.military || null,
          expected_salary: app.expected_salary || null,
          available_date: app.available_date || null,
          positioning: txt(data.one_liner),
          reasons: (fc.reasons || []).map(txt).filter(Boolean),
          risks: (fc.risks_to_disclose || []).map(txt).filter(Boolean),
          // 這三格面談規格沒收集，前端要顯示成必填的手動輸入格，不要留空值就直接印
          current_state: null, related_experience: null, transportation: null,
          // 這個人選全部的推薦紀錄（可能推薦給好幾家客戶），前端用來畫「幫哪家
          // 客戶做」的選單；selected_company_id 是這次回傳資料實際對應的那一筆。
          forwards, selected_company_id: chosen ? chosen.company_id : null,
        });
      }

      // POST /admin/application/:id/client-formal-report — 顧問補完手動欄位＋
      // （可選）大頭貼 base64 之後，組出完整的客戶版履歷 HTML，前端開新分頁
      // window.print() 存成 PDF（跟既有「下載報告（PDF）」同一種做法，不用等
      // 本機排程、不用另外接 headless Chrome／第三方服務）。
      if (p.startsWith('/admin/application/') && p.endsWith('/client-formal-report') && request.method === 'POST') {
        const appId = decodeURIComponent(p.slice('/admin/application/'.length, -'/client-formal-report'.length));
        const b = await request.json().catch(() => ({}));
        // 跟 prefill 同一個修法：優先抓 candidate_forwards 的 job_slug／
        // company_id，不要只看 applications 建立當下那個；body 可以帶
        // company_id 指定要幫「哪一家」客戶做（一個人選可能推薦給好幾家），
        // 沒帶就退回最新一筆。
        const wantCompanyId = String(b.company_id || '').trim();
        const fwdQuery = wantCompanyId
          ? env.DB.prepare(
              `SELECT company_id, job_slug FROM candidate_forwards WHERE application_id=? AND company_id=? LIMIT 1`
            ).bind(appId, wantCompanyId)
          : env.DB.prepare(
              `SELECT company_id, job_slug FROM candidate_forwards WHERE application_id=? ORDER BY forwarded_at DESC LIMIT 1`
            ).bind(appId);
        const chosenFwd = await fwdQuery.first();
        const appBase = await env.DB.prepare(
          `SELECT name, expected_salary, available_date, job_slug, job_title FROM applications WHERE id=?`
        ).bind(appId).first();
        if (!appBase) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const jobSlug = (chosenFwd && chosenFwd.job_slug) || appBase.job_slug;
        const job = jobSlug
          ? await env.DB.prepare(`SELECT title, client_named, company_id FROM jobs WHERE slug=?`).bind(jobSlug).first()
          : null;
        const companyId = (chosenFwd && chosenFwd.company_id) || (job && job.company_id) || null;
        const clientRow = companyId
          ? await env.DB.prepare(`SELECT display_name FROM client_companies WHERE id=?`).bind(companyId).first()
          : null;
        const app = {
          ...appBase,
          job_full_title: (job && job.title) || appBase.job_title,
          client_named: job && job.client_named,
          client_name: clientRow ? clientRow.display_name : '（未綁客戶）',
        };
        const [report, fullApp] = await Promise.all([
          env.DB.prepare(`SELECT content_json, content_md FROM reports WHERE application_id=? ORDER BY created_at DESC LIMIT 1`).bind(appId).first(),
          env.DB.prepare(`SELECT consultant_call_notes, resume_file_id FROM applications WHERE id=?`).bind(appId).first(),
        ]);
        let data = {};
        try { data = report && report.content_json ? JSON.parse(report.content_json) : {}; } catch { data = {}; }
        const basics = (data && typeof data.basics === 'object' && data.basics) || {};
        const clientNamed = app.client_named;
        const name = clientDisplayName(app.name, clientNamed);
        const anon = isAnonymous(clientNamed);

        // 2026-09-02 加：現場讀電洽逐字稿＋履歷全文，讓 AI 現整理出核心條件對應／
        // 補充說明／我方建議，不再只是照抄阿財面談報告的 content_json（那份很多
        // TG手動新增的人選根本沒有，或內容太單薄，跟參考範本的深度差很多）。
        let resumeText = '';
        if (fullApp && fullApp.resume_file_id) {
          try {
            const rf = await fileB64(env, fullApp.resume_file_id);
            if (rf) {
              const bytes = Uint8Array.from(atob(rf.content), (c) => c.charCodeAt(0));
              const md = await env.AI.toMarkdown([{ name: rf.filename, blob: new Blob([bytes], { type: rf.mime }) }]);
              resumeText = (md && md[0] && md[0].data) ? String(md[0].data).trim() : '';
            }
          } catch (e) { resumeText = ''; }
        }
        const synth = await synthesizeClientReport(env, {
          name: app.name,
          callNotes: fullApp ? fullApp.consultant_call_notes : null,
          resumeText,
          existingReportMd: report ? report.content_md : null,
        });

        const currentState = (synth && synth.current_state) || String(b.current_state || '').trim() || null;
        const relatedExp = (synth && synth.related_experience) || String(b.related_experience || '').trim() || null;
        const transport = (synth && synth.transportation) || String(b.transportation || '').trim() || null;

        const rows = [
          ['姓名', name],
          ['現況', currentState ? scrubForClient(currentState).slice(0, 300) : null],
          ['現居地', basics.residence || null],
          ['學歷', basics.education || null],
          ['出生年月', anon ? null : (basics.age || null)],
          ['兵役', basics.military || null],
          ['語言', basics.languages || null],
          ['相關經驗', relatedExp ? scrubForClient(relatedExp).slice(0, 300) : null],
          ['交通工具', transport ? scrubForClient(transport).slice(0, 200) : null],
          ['期望待遇', app.expected_salary || null],
          ['可到職', app.available_date || null],
        ].filter(([, v]) => v);

        const coreFit = (synth && synth.core_fit && synth.core_fit.length)
          ? synth.core_fit.map((c) => ({ title: scrubForClient(c.title), body: scrubForClient(c.body) })).filter((c) => c.title && c.body)
          : [];
        const supplementary = (synth && synth.supplementary && synth.supplementary.length)
          ? synth.supplementary.map((s) => scrubForClient(s)).filter(Boolean)
          : [];
        const recommendation = (synth && synth.recommendation) ? scrubForClient(synth.recommendation) : null;

        const html = buildClientFormalHtml({
          name, jobTitle: app.job_full_title || app.job_title || '', clientName: app.client_name || '',
          rows, coreFit, supplementary, recommendation, photoB64: b.photo_b64 || null,
        });
        return json(request, { ok: true, html, ai_synthesized: !!synth });
      }

      // 2026-09-02 加：「主動開發」的人選也要能做客戶版履歷。這批人選在
      // sourced_candidates 表，通常還沒被阿財面談過（沒有 reports/content_json
      // 可以抄），基本上除了姓名/現職/現居地/技能之外，其餘欄位（現況／相關
      // 經驗／交通工具／學歷／兵役／語言／期望待遇／可到職）全部要顧問手動填。
      if (p.startsWith('/admin/sourced/') && p.endsWith('/client-formal-prefill') && request.method === 'GET') {
        const srcId = decodeURIComponent(p.slice('/admin/sourced/'.length, -'/client-formal-prefill'.length));
        const src = await env.DB.prepare(
          `SELECT name, headline, company, location, skills, job_slug
             FROM sourced_candidates WHERE id = ?`
        ).bind(srcId).first();
        if (!src) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const jobTitle = src.job_slug
          ? ((await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(src.job_slug).first()) || {}).title
          : null;
        return json(request, {
          ok: true,
          name: src.name || '候選人', anonymous: false,
          job_title: jobTitle || '（尚未鎖定職缺）', client_name: '（尚未推薦給客戶）',
          residence: src.location || null,
          age: null, education: null, languages: null, certificates: null, military: null,
          expected_salary: null, available_date: null,
          positioning: [src.headline, src.company].filter(Boolean).join('・') || null,
          reasons: [], risks: src.skills ? [`技能／經驗：${src.skills}`] : [],
          current_state: null, related_experience: null, transportation: null,
        });
      }

      if (p.startsWith('/admin/sourced/') && p.endsWith('/client-formal-report') && request.method === 'POST') {
        const srcId = decodeURIComponent(p.slice('/admin/sourced/'.length, -'/client-formal-report'.length));
        const b = await request.json().catch(() => ({}));
        const src = await env.DB.prepare(
          `SELECT name, headline, company, location, skills, job_slug, bio, note, resume_file_id
             FROM sourced_candidates WHERE id = ?`
        ).bind(srcId).first();
        if (!src) return json(request, { ok: false, error: '找不到這位人選' }, 404);
        const jobTitle = src.job_slug
          ? ((await env.DB.prepare(`SELECT title FROM jobs WHERE slug=?`).bind(src.job_slug).first()) || {}).title
          : null;
        const name = src.name || '候選人';

        // 2026-09-02 加：跟正式應徵那邊同一套——現場讀履歷全文＋既有筆記
        // （bio／note，主動開發沒有電洽逐字稿，用這兩個代替），讓 AI 現整理
        // 核心條件對應／補充說明／我方建議。多半還沒面談過，內容會比較單薄，
        // 是合理的（沒收集到的東西不能硬編）。
        let resumeText = '';
        if (src.resume_file_id) {
          try {
            const rf = await fileB64(env, src.resume_file_id);
            if (rf) {
              const bytes = Uint8Array.from(atob(rf.content), (c) => c.charCodeAt(0));
              const md = await env.AI.toMarkdown([{ name: rf.filename, blob: new Blob([bytes], { type: rf.mime }) }]);
              resumeText = (md && md[0] && md[0].data) ? String(md[0].data).trim() : '';
            }
          } catch (e) { resumeText = ''; }
        }
        const callNotesEquiv = [src.bio, src.note].filter(Boolean).join('\n\n');
        const synth = await synthesizeClientReport(env, { name, callNotes: callNotesEquiv, resumeText, existingReportMd: null });

        const currentState = (synth && synth.current_state) || String(b.current_state || '').trim() || null;
        const relatedExp = (synth && synth.related_experience) || String(b.related_experience || '').trim() || (src.skills ? `技能／經驗：${src.skills}` : null);
        const transport = (synth && synth.transportation) || String(b.transportation || '').trim() || null;

        const rows = [
          ['姓名', name],
          ['現況', currentState ? scrubForClient(currentState).slice(0, 300) : null],
          ['現居地', src.location || null],
          ['現職', [src.company, src.headline].filter(Boolean).join('・') || null],
          ['相關經驗', relatedExp ? scrubForClient(relatedExp).slice(0, 300) : null],
          ['交通工具', transport ? scrubForClient(transport).slice(0, 200) : null],
          ['期望待遇', String(b.expected_salary || '').trim().slice(0, 100) || null],
          ['可到職', String(b.available_date || '').trim().slice(0, 100) || null],
        ].filter(([, v]) => v);

        const coreFit = (synth && synth.core_fit && synth.core_fit.length)
          ? synth.core_fit.map((c) => ({ title: scrubForClient(c.title), body: scrubForClient(c.body) })).filter((c) => c.title && c.body)
          : [];
        const supplementary = (synth && synth.supplementary && synth.supplementary.length)
          ? synth.supplementary.map((s) => scrubForClient(s)).filter(Boolean)
          : [];
        const recommendation = (synth && synth.recommendation) ? scrubForClient(synth.recommendation) : null;

        const html = buildClientFormalHtml({
          name, jobTitle: jobTitle || '（尚未鎖定職缺）', clientName: '（尚未推薦給客戶）',
          rows, coreFit, supplementary, recommendation, photoB64: b.photo_b64 || null,
        });
        return json(request, { ok: true, html, ai_synthesized: !!synth });
      }

      // 2026-09-01 加：人選卡片改版——⑤給推薦客戶的備註，每一筆推薦紀錄各自
      // 一格，寫的是「這位人選給這家客戶看的補充說明」，跟推薦紀錄一對一存在
      // candidate_forwards.client_note（既有欄位，本來只在客戶portal那邊顯示，
      // 現在顧問後台也能直接編輯）。
      if (p === '/admin/candidate-forwards/client-note' && request.method === 'POST') {
        const b = await request.json().catch(() => ({}));
        if (!b.application_id || !b.company_id) {
          return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        }
        const row = await env.DB.prepare(
          `SELECT id FROM candidate_forwards WHERE application_id=? AND company_id=?`
        ).bind(b.application_id, b.company_id).first();
        if (!row) return json(request, { ok: false, error: '找不到這筆推薦紀錄' }, 404);
        await env.DB.prepare(
          `UPDATE candidate_forwards SET client_note=? WHERE application_id=? AND company_id=?`
        ).bind(String(b.client_note || '').trim().slice(0, 1000) || null, b.application_id, b.company_id).run();
        return json(request, { ok: true });
      }

      // ── 人才池批次指派負責顧問 ──
      // 2026-08-26 加。池子 3,500 人、一頁 60 筆，一個一個下拉指派是不可能的事。
      // 兩種模式：
      //   ids   ＝ 勾選哪幾位就指派哪幾位（精準）
      //   filter＝ 把「目前篩選條件下的全部」一次指派（例如「這個職缺的 55 人全給 Phoebe」）
      // filter 模式的 WHERE 條件必須跟 /admin/sourced 列表**完全一致**，
      // 不然畫面上說 55 人、實際改到 3,500 人——這種錯改回來要一筆一筆比對。
      if (p === '/admin/sourced/assign-owner' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }

        const ow = b.owner ? String(b.owner) : null;   // null ＝ 取消指派
        if (ow) {
          const c = await env.DB.prepare(
            `SELECT 1 FROM consultants WHERE id=? AND is_active=1`).bind(ow).first();
          if (!c) return json(request, { ok: false, error: '找不到這位顧問（或已停用）' }, 400);
        }

        // ① 勾選模式
        if (Array.isArray(b.ids) && b.ids.length) {
          if (b.ids.length > 500) return json(request, { ok: false, error: '一次最多 500 位' }, 400);
          const ph = b.ids.map(() => '?').join(',');
          const r = await env.DB.prepare(
            `UPDATE sourced_candidates SET owner=? WHERE id IN (${ph})`
          ).bind(ow, ...b.ids.map(String)).run();
          return json(request, { ok: true, changed: (r.meta && r.meta.changes) || 0 });
        }

        // ② 條件模式——WHERE 與列表端逐字同步
        const f = b.filter || {};
        const st = String(f.status || 'new');
        const q = String(f.q || '').trim().slice(0, 60);
        const cat = String(f.cat || '').trim().slice(0, 20);
        const src = String(f.source || '').trim().slice(0, 80);
        const job = String(f.job || '').trim().slice(0, 80);
        const has = String(f.has || '').trim();
        const where = [`(? = 'all' OR status = ?)`];
        const bind = [st, st];
        if (q) {
          where.push(`(name LIKE ? OR headline LIKE ? OR company LIKE ? OR skills LIKE ? OR bio LIKE ?)`);
          const like = '%' + q + '%';
          bind.push(like, like, like, like, like);
        }
        if (cat) { where.push(`category = ?`); bind.push(cat); }
        if (job) { where.push(`job_slug = ?`); bind.push(job); }
        if (src) { where.push(`source = ?`); bind.push(src); }
        if (has === 'email') where.push(`COALESCE(email,'') <> ''`);
        if (has === 'linkedin') where.push(`COALESCE(linkedin_url,'') <> ''`);
        if (has === 'github') where.push(`COALESCE(github_url,'') <> ''`);
        const W = where.join(' AND ');

        // 先算會動到幾筆回給前端確認。expect 有帶的話要對得上才執行——
        // 顧問看到的數字跟實際改的數字不一致時，寧可整批不做。
        const cnt = await env.DB.prepare(
          `SELECT COUNT(*) n FROM sourced_candidates WHERE ${W}`).bind(...bind).first();
        const n = (cnt && cnt.n) || 0;
        if (b.dry) return json(request, { ok: true, would_change: n });
        if (b.expect !== undefined && Number(b.expect) !== n) {
          return json(request, { ok: false,
            error: `畫面上是 ${b.expect} 位，實際符合條件的是 ${n} 位，可能有人同時在改。請重新整理後再試。` }, 409);
        }
        const r = await env.DB.prepare(
          `UPDATE sourced_candidates SET owner=? WHERE ${W}`).bind(ow, ...bind).run();
        return json(request, { ok: true, changed: (r.meta && r.meta.changes) || 0 });
      }

      // ── 阿財準不準：待補填清單 ──
      // 2026-08-21 原本這裡有一個獨立的 applications.consultant_call 欄位，
      // 要顧問在這頁「另外」按一次會推／不推／再看看。
      //
      // 2026-08-25 改掉：那個按鈕跟顧問在「初篩報告」頁本來就會按的
      // 推薦給客戶／需補問／婉拒（寫進 reports.consultant_decision，
      // /admin/decide-report 那支）是同一件事問兩次。已經結案或已經標
      // 不推薦的案子，事後在這頁又冒出來要你重新按一次「會推嗎」，
      // 答案跟真正發生的事對不上——這頁的準確率因此在量錯的東西。
      //
      // 現在直接讀 reports.consultant_decision 當唯一真相：
      //   forwarded（推薦給客戶）→ 會推
      //   need_more（需補問）    → 再看看
      //   rejected（婉拒）       → 不推
      // 「還沒回填」＝ consultant_decision 還是 NULL，這頁的按鈕現在
      // 直接呼叫跟報告頁一樣的 /admin/decide-report，寫進同一個欄位，
      // 不會再有兩邊各自一份、彼此對不上的資料。
      if (p === '/admin/kpi/pending' && request.method === 'GET') {
        // ⚠️ reports.recommend 這個欄位從沒被寫入過（schema 留著但
        //    interview_daemon.py 從來沒 SET 過），阿財自己的判斷其實是
        //    存在 content_json.verdict 裡（值得轉給顧問／資訊不足建議補問／
        //    硬條件不符／待顧問判斷，定義在 interview_daemon.py 的 _VERDICTS）。
        //    用 json_extract 直接拿，不要再假裝 recommend 有資料。
        // 2026-08-26 加「有進面試／有錄取」兩個里程碑。
        // ⚠️ 刻意不新開欄位：這兩件事在系統裡已經有唯一真相，就是進度條
        //（applications.manual_stage ＋ placements.stage，resolveStage() 那一套）。
        // 另外存一份「顧問在 KPI 頁按過的里程碑」，兩邊一定會分歧——
        // 客戶在自己的入口回填了面試，這頁卻還顯示沒進面試，那這頁又在量錯東西。
        // 所以按鈕寫的是同一個 manual_stage，讀的也是同一份資料。
        const INTV = `('stage1','stage2','stage3','stage4','offer','onboard','care')`;
        const OFFR = `('offer','onboard','care')`;
        const PL_INTV = `('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW','AWAITING_CLIENT_FEEDBACK','INTERVIEW_COMPLETED','OFFER','OFFER_ACCEPTED','HIRED','PLACED','OFFER_PENDING','ONBOARDING')`;
        const PL_OFFR = `('OFFER','OFFER_ACCEPTED','HIRED','PLACED','OFFER_PENDING','ONBOARDING')`;
        const { results } = await env.DB.prepare(
          `SELECT a.id, a.name, a.job_slug, j.title AS job_title, a.manual_stage,
                  a.no_interview_at,
                  r.id AS report_id, r.created_at AS report_at,
                  json_extract(r.content_json, '$.verdict') AS ai_verdict,
                  r.consultant_decision,
                  CAST((julianday('now','+8 hours') - julianday(r.created_at)) AS INT) AS days,
                  (COALESCE(a.manual_stage,'') IN ${INTV}
                    OR EXISTS (SELECT 1 FROM placements p WHERE p.application_id = a.id
                                AND (UPPER(p.stage) IN ${PL_INTV} OR p.onboard_date IS NOT NULL))
                    OR EXISTS (SELECT 1 FROM candidate_forwards cf WHERE cf.application_id = a.id
                                AND COALESCE(cf.manual_stage,'') IN ${INTV})) AS reached_interview,
                  (COALESCE(a.manual_stage,'') IN ${OFFR}
                    OR EXISTS (SELECT 1 FROM placements p WHERE p.application_id = a.id
                                AND (UPPER(p.stage) IN ${PL_OFFR} OR p.onboard_date IS NOT NULL))
                    OR EXISTS (SELECT 1 FROM candidate_forwards cf WHERE cf.application_id = a.id
                                AND COALESCE(cf.manual_stage,'') IN ${OFFR})) AS reached_offer
             FROM applications a
             JOIN reports r ON r.id = (SELECT r2.id FROM reports r2
                                        WHERE r2.application_id = a.id
                                        ORDER BY r2.created_at DESC LIMIT 1)
             LEFT JOIN jobs j ON j.slug = a.job_slug
            WHERE a.superseded_by IS NULL
            ORDER BY (r.consultant_decision IS NOT NULL), r.created_at DESC`
        ).all();
        const DECISION_TO_VERDICT = { forwarded: '會推', need_more: '再看看', rejected: '不推' };
        const rows = (results || []).map((x) => ({
          ...x, verdict: DECISION_TO_VERDICT[x.consultant_decision] || null,
        }));
        const done = rows.filter((x) => x.verdict);
        // 準不準比的是「阿財說值得面談」跟「顧問最後真的推薦給客戶」對不對得起來——
        // 不是顧問點了幾次「會推」。這才是頁面標題「阿財判值得轉、你實際會推」
        // 實際在講的東西，之前的算法沒有真的比對阿財的判斷，算出來的數字沒有意義。
        const agree = done.filter((x) => x.ai_verdict === '值得轉給顧問' && x.verdict === '會推').length;
        const aiWorthInterview = done.filter((x) => x.ai_verdict === '值得轉給顧問').length;
        // 「顧問推了」只是第一層。真正證明阿財看對人的是後面兩層：
        // 客戶願意約面試、客戶真的要。分母都用「阿財說值得轉」的那些，
        // 一路往下看漏在哪一關，比單一個準確率有用得多。
        const aiWorth = rows.filter((x) => x.ai_verdict === '值得轉給顧問');
        const gotInterview = aiWorth.filter((x) => x.reached_interview).length;
        const gotOffer = aiWorth.filter((x) => x.reached_offer).length;
        // ⚠️ 分母改成「已經有答案的」＝進了面試 ＋ 明確標沒面試。
        // 用「阿財說值得轉的全部」當分母的話，還在等客戶回覆的人會被算成失敗，
        // 比率永遠偏低，而且每多推一個人就往下掉一次——那個數字不能拿來判斷任何事。
        // 顧問標「不推」的，本來就不會有面試了——不用再叫人手動按一次
        // 「沒面試」。這兩件事的失敗原因不一樣（不推＝阿財看錯人，
        // 推了客戶沒約＝阿財跟顧問都看錯，或客戶條件跟講的不一樣），
        // 所以分開統計；但對「有沒有進到面試」這個問題來說，兩者都是否。
        const isNoIntv = (x) => !x.reached_interview
          && (x.consultant_decision === 'rejected' || !!x.no_interview_at);
        const noInterview = aiWorth.filter(isNoIntv).length;
        const rejectedByUs = aiWorth.filter((x) => !x.reached_interview
          && x.consultant_decision === 'rejected').length;
        const clientNoShow = aiWorth.filter((x) => !x.reached_interview
          && x.consultant_decision !== 'rejected' && !!x.no_interview_at).length;
        const settled = gotInterview + noInterview;
        const unknown = aiWorth.length - settled;
        return json(request, { ok: true, rows,
          stats: { total: rows.length, done: done.length, pending: rows.length - done.length,
                   agree, aiWorthInterview,
                   gotInterview, gotOffer, noInterview, settled, unknown,
                   rejectedByUs, clientNoShow,
                   interviewRate: settled >= 5 ? Math.round((gotInterview / settled) * 100) : null,
                   offerRate: settled >= 5 ? Math.round((gotOffer / settled) * 100) : null,
                   // 樣本太少就不給百分比——與其給一個看起來很漂亮的數字，
                   // 不如老實說「還不能算」。分母是「阿財說值得面談」的那些，
                   // 不是全部已回填的，這樣才是在問「阿財說可以的，顧問真的推了幾成」。
                   rate: aiWorthInterview >= 5 ? Math.round((agree / aiWorthInterview) * 100) : null } });
      }

      // 補填一筆——直接沿用報告頁的處置邏輯，不要另開一份資料。
      if (p === '/admin/kpi/set' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const VERDICT_TO_DECISION = { '會推': 'forwarded', '再看看': 'need_more', '不推': 'rejected' };
        const decision = VERDICT_TO_DECISION[String(b.verdict || '')];
        if (!b.reportId || !decision) {
          return json(request, { ok: false, error: '參數錯誤', verdicts: Object.keys(VERDICT_TO_DECISION) }, 400);
        }
        const now = nowTaipei();
        const r = await env.DB.prepare(
          `UPDATE reports SET consultant_decision=?, decided_at=? WHERE id=?`
        ).bind(decision, now, b.reportId).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這份報告' }, 404);
        return json(request, { ok: true });
      }

      // 標記里程碑：這個人選有進到面試／有到錄取。
      // 走的是跟顧問後台進度條、客戶入口、候選人 LINE 完全同一套
      // applyManualStage()——按了這裡，三個地方會同時往前跳，不會各自一份。
      if (p === '/admin/kpi/milestone' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const MS = { interview: 'stage1', offer: 'offer' };
        const kind = String(b.milestone || '');
        const stage = MS[kind];
        const appId = String(b.applicationId || '').trim();
        if (!appId || (!stage && kind !== 'no_interview')) {
          return json(request, { ok: false, error: '參數錯誤' }, 400);
        }

        const app = await env.DB.prepare(
          `SELECT id, manual_stage, no_interview_at FROM applications WHERE id = ?`).bind(appId).first();
        if (!app) return json(request, { ok: false, error: '找不到這位人選' }, 404);

        // 「沒面試」＝顧問明確說「客戶最後沒有要約他」。這跟「還沒回填」是兩件事，
        // 沒有這一筆就分不出來，準確率的分母會一直混著還在等結果的人。
        // 它不是進度條上的一關（進度條只往前走），所以另外用一個時間戳記錄。
        if (kind === 'no_interview') {
          if (b.undo) {
            await env.DB.prepare(
              `UPDATE applications SET no_interview_at=NULL, no_interview_by=NULL WHERE id=?`
            ).bind(appId).run();
            return json(request, { ok: true, undone: true });
          }
          // 已經走到面試以後的階段，就不該再標「沒面試」——兩邊會互相矛盾。
          const cur = STAGE_INDEX[app.manual_stage];
          if (cur !== undefined && cur >= STAGE_INDEX.stage1) {
            return json(request, { ok: false,
              error: '這位人選的進度已經在面試之後了，不能標「沒面試」。要改的話先取消上面的里程碑。' }, 400);
          }
          await env.DB.prepare(
            `UPDATE applications SET no_interview_at=?, no_interview_by=? WHERE id=?`
          ).bind(nowTaipei(), 'consultant:kpi', appId).run();
          return json(request, { ok: true, marked: 'no_interview' });
        }

        // 標了有進面試／有錄取，就把「沒面試」清掉——不然同一個人兩邊都成立。
        if (!b.undo && app.no_interview_at) {
          await env.DB.prepare(
            `UPDATE applications SET no_interview_at=NULL, no_interview_by=NULL WHERE id=?`).bind(appId).run();
        }

        // 取消：把手動指定清掉，讓進度條退回系統自己算出來的位置。
        // 不是往回設一個更早的階段——那會變成「顧問手動把人往回拖」，
        // 客戶那邊看到進度倒退會以為出事了。
        if (b.undo) {
          await applyManualStage(env, { application_id: appId, stage: null,
                                        note: null, by: 'consultant:kpi' });
          return json(request, { ok: true, undone: true });
        }

        // 已經比這一關更前面了就不要往回設。按「有進面試」的人如果早就錄取了，
        // 應該維持在錄取，不是被拉回面試。
        const cur = STAGE_INDEX[app.manual_stage];
        if (cur !== undefined && cur >= STAGE_INDEX[stage]) {
          return json(request, { ok: true, skipped: '目前進度已經在這一關之後了' });
        }
        await applyManualStage(env, { application_id: appId, stage,
                                      note: null, by: 'consultant:kpi' });
        return json(request, { ok: true, stage });
      }

      // 不合適的原因統計——給策略調整那支 agent 讀的
      if (p === '/admin/sourced/rejects' && request.method === 'GET') {
        const rows = (await env.DB.prepare(
          `SELECT job_slug, reject_reason, COUNT(*) n
             FROM sourced_candidates
            WHERE status='rejected' AND reject_reason IS NOT NULL
            GROUP BY job_slug, reject_reason ORDER BY n DESC`).all()).results || [];
        return json(request, { ok: true, reasons: REJECT_REASONS, rows });
      }

      if (p === '/admin/overview' && request.method === 'GET') {
        const one = async (sql, ...b) => (await env.DB.prepare(sql).bind(...b).first()) || {};
        const all = async (sql, ...b) => ((await env.DB.prepare(sql).bind(...b).all()).results || []);

        const now = await one(
          `SELECT
             -- 2026-09-02 修：原本沒排除 client_draft（客戶還沒核准公開的職缺草稿），
             -- 跟其他頁面（/admin/jobs 的下拉、顧問社群的職缺清單）篩選邏輯對不起來，
             -- 「在辦職缺」會比實際能應徵的職缺數多算。
             (SELECT COUNT(*) FROM jobs WHERE COALESCE(status,'open') NOT IN ('closed','pending_review','client_draft')) AS jobs_open,
             (SELECT COUNT(*) FROM applications) AS cands,
             (SELECT COUNT(*) FROM applications WHERE created_at >= datetime('now','+8 hours','-7 days')) AS new7,
             (SELECT COUNT(*) FROM applications WHERE interview_state='done') AS done,
             (SELECT COUNT(*) FROM applications
               WHERE interview_state='done' AND consultant_call IS NULL) AS need_call,
             (SELECT COUNT(*) FROM applications
               WHERE (interview_state IS NULL OR interview_state='not_started')
                 AND status IN ('ready','scheduled')) AS waiting,
             (SELECT COUNT(*) FROM applications a JOIN jobs j ON j.slug=a.job_slug
               WHERE a.interview_state='done' AND COALESCE(j.interview_language,'')!=''
                 AND a.lang_verified_at IS NULL) AS lang_missing`);

        // 漏斗：以 pipeline_events 為準（顧問實際記的），不是 applications.status
        const funnel = await all(
          `SELECT stage, COUNT(DISTINCT application_id) n FROM pipeline_events
            WHERE event='pass' OR event IS NULL GROUP BY stage`);

        const verdicts = await all(
          `SELECT consultant_call AS k, COUNT(*) n FROM applications
            WHERE consultant_call IS NOT NULL GROUP BY consultant_call`);

        // 阿財判定 vs 顧問判斷——KPI 復盤的核心，目前多半還沒有資料
        const agree = await all(
          `SELECT a.consultant_call AS call, COUNT(*) n FROM applications a
            WHERE a.consultant_call IS NOT NULL GROUP BY a.consultant_call`);

        const byJob = await all(
          `SELECT j.title, a.job_slug,
                  COUNT(*) AS n,
                  SUM(CASE WHEN a.interview_state='done' THEN 1 ELSE 0 END) AS done,
                  SUM(CASE WHEN a.interview_state='done' AND a.consultant_call IS NULL THEN 1 ELSE 0 END) AS need_call
             FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug
            GROUP BY a.job_slug ORDER BY n DESC`);

        const todo = await all(
          `SELECT a.id, a.name, a.job_slug, j.title, a.interview_ended_at
             FROM applications a LEFT JOIN jobs j ON j.slug=a.job_slug
            WHERE a.interview_state='done' AND a.consultant_call IS NULL
            ORDER BY a.interview_ended_at DESC LIMIT 20`);

        return json(request, { ok: true, now, funnel, verdicts, agree, byJob, todo });
      }

      if (p === '/admin/expertise' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT e.job_slug, e.domain, e.topics_json, e.questions_json, e.sources_json,
                  e.built_at, e.edited_by, e.edited_at, (e.original_json IS NOT NULL) AS can_restore,
                  j.title,
                  (SELECT COUNT(*) FROM applications a WHERE a.job_slug = e.job_slug) AS cands
             FROM job_expertise e LEFT JOIN jobs j ON j.slug = e.job_slug
            ORDER BY cands DESC, e.built_at`
        ).all();
        // 也回「還沒有題庫的職缺」，顧問才知道缺哪些、要不要補建
        const { results: missing } = await env.DB.prepare(
          `SELECT j.slug, j.title FROM jobs j
             LEFT JOIN job_expertise e ON e.job_slug = j.slug
            WHERE e.job_slug IS NULL AND COALESCE(j.status,'open') != 'closed'
            ORDER BY j.title`
        ).all();
        return json(request, { ok: true, banks: results || [], missing: missing || [] });
      }

      if (p === '/admin/social-post-queue' && request.method === 'GET') {
        // 2026-08-18 改：來源換成 social_post_queue（一個職缺多筆排隊紀錄），
        // 欄位名稱刻意跟舊版一樣，前端「一鍵發文」頁面不用改。
        // ⚠️ 2026-09-02 加 limit 參數：原本寫死 LIMIT 30，是為了「排隊列表」
        // （8秒輪詢一次，只需要看最近在跑什麼）夠用就好，故意設小。但「成效
        // 儀表板」的顧問排行／一週七天熱力圖也是直接拿這支的資料在算——
        // 8個帳號一起發文，30筆很快就被洗掉，Phoebe這種發文量大的顧問，
        // 舊資料整個從清單消失，熱力圖上明明真的發過文的日子變成空白「—」，
        // 顧問還以為是系統漏記錄。8秒輪詢那邊維持預設30（不想讓那支變重），
        // 成效儀表板改用大上限的呼叫方式。
        const qLimit = Math.min(500, Math.max(1, parseInt(url.searchParams.get('limit'), 10) || 30));
        const { results } = await env.DB.prepare(
          `SELECT q.job_slug AS slug, j.title, q.status AS social_post_status,
                  q.requested_at AS social_post_at, q.url AS social_post_url,
                  sa.label AS account_label,
                  -- 2026-08-19 加：發文成效。顧問要判斷「這個時間發有沒有人看」，
                  -- 原本這頁只看得到狀態，看不到結果，等於發完就沒下文。
                  -- posted_at 是真正貼出去的時間（social_post_at 是草稿產生時間，兩者常差好幾小時）
                  q.posted_at, q.views, q.likes, q.replies,
                  -- 這則貼文帶來幾次點擊（走 /go/ 轉址頁的才算得到）
                  (SELECT COUNT(*) FROM link_clicks lc WHERE lc.queue_id = q.id) AS clicks
             FROM social_post_queue q
             JOIN jobs j ON j.slug = q.job_slug
             LEFT JOIN social_accounts sa ON sa.id = q.account_id
            ORDER BY q.requested_at DESC
            LIMIT ?`
        ).bind(qLimit).all();
        return json(request, { ok: true, queue: results || [] });
      }

      // 單筆的完整逐字稿——不論有沒有報告都看得到
      if (p.startsWith('/admin/session/')) {
        const aid = decodeURIComponent(p.slice('/admin/session/'.length));
        const app = await env.DB.prepare(
          `SELECT a.*, j.client_name, j.title AS job_full_title
             FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug WHERE a.id = ?`
        ).bind(aid).first();
        if (!app) return json(request, { ok: false, error: '找不到這筆應徵' }, 404);

        const { results } = await env.DB.prepare(
          `SELECT role, content, created_at FROM messages
            WHERE application_id = ? ORDER BY id ASC LIMIT 400`
        ).bind(aid).all();

        const rep = await env.DB.prepare(
          `SELECT id, content_md, consultant_decision, created_at FROM reports
            WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
        ).bind(aid).first();

        const fb = await env.DB.prepare(
          `SELECT rating, comment, created_at FROM interview_feedback WHERE application_id = ?`
        ).bind(aid).first();

        const flags = await env.DB.prepare(
          `SELECT created_at, reason, detail FROM chat_reports
            WHERE application_id = ? ORDER BY id DESC LIMIT 20`
        ).bind(aid).all();

        // 2026-08-13 加：跟 /admin/report/<id> 一樣要帶階段列的資料——
        // 「全部應徵」這條路徑（openSession）跟「初篩報告」列表（open）
        // 是同一個彈窗，兩邊進來看到的東西不能不一樣。
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id = ? ORDER BY stage ASC, created_at ASC`
        ).bind(aid).all();
        const placement = await env.DB.prepare(
          `SELECT stage, onboard_date, offer_response, onboard_confirm_status, candidate_care_log, close_reason
             FROM placements WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
        ).bind(aid).first();

        // 面談連結要給顧問看得到。
        // 原本刻意移除 chat_token，但那造成一個沒有出路的情況：
        // 顧問建完檔沒複製就關掉視窗，那個連結就永遠找不回來了。
        // 這個端點本來就要 ADMIN_TOKEN，顧問看得到自己的候選人連結是合理的。
        const interviewUrl = app.chat_token
          ? `https://step1ne.com/interview/?t=${app.chat_token}`
          : null;
        delete app.chat_token;
        let sessRoundLabels = {};
        try { sessRoundLabels = JSON.parse(app.client_interview_labels || '{}'); } catch { sessRoundLabels = {}; }
        const sessJobStageKeys = await getJobStageKeys(env, app.job_slug);
        return json(request, { ok: true, application: app, interview_url: interviewUrl,
                               transcript: results || [], report: rep || null,
                               flags: flags.results || [], feedback: fb || null,
                               appointments: appts || [], placement: placement || null,
                               stage: resolveStage(app, rep, appts || [], placement, sessRoundLabels, sessJobStageKeys) });
      }

      if (p === '/admin/reports') {
        const q = (url.searchParams.get('q') || '').trim();
        const state = url.searchParams.get('state') || '';   // 顧問處置狀態
        const job = url.searchParams.get('job') || '';
        const id = (url.searchParams.get('id') || '').trim();
        const limit = Math.min(Number(url.searchParams.get('limit') || 50), 200);
        const offset = Number(url.searchParams.get('offset') || 0) || 0;

        const where = ['1=1'];
        const bind = [];
        // 從職缺分類頁的漏斗彈窗點「處置」深連結進來時用——那邊給的是報告 id。
        // ⚠️ 有 id 就不套 state／job 篩選：這是「直接開這一份」，不是「在清單裡找」，
        //    原本沒有這個分支，深連結進來永遠套用預設的「待處置」篩選，
        //    已經處置過或篩選條件對不上的人選看起來就像連結失效、跳到別的頁面。
        if (id) { where.push('r.id = ?'); bind.push(id); }
        else if (q) {
          where.push('(a.name LIKE ? OR a.email LIKE ? OR r.content_md LIKE ?)');
          bind.push(`%${q}%`, `%${q}%`, `%${q}%`);
        }
        if (!id && job) { where.push('a.job_slug = ?'); bind.push(job); }
        // ⚠️ 這兩支都要防 id 存在的情況。前端 load() 每次都會帶預設的
        //    state=undecided 一起送，深連結雖然設了 id 但不會特地清掉 state，
        //    第二支原本沒擋，'undecided' 會被當成 consultant_decision 的值去比對——
        //    但真實值只有 forwarded/need_more/rejected，查不到任何人，深連結一樣會壞掉。
        // ⚠️ 2026-08-31 修：「待處置」原本只看 consultant_decision IS NULL，完全
        // 沒管案子本身是不是已經結案（placements.stage 是 CLOSED_*）——結案跟
        // 「有沒有決定要不要推薦」是兩件不相干的事，顧問把案子標記內部結案後，
        // 這裡卻還是會冒出來討「待處置」，看起來像結案沒生效。已結案的案子
        // 不管 consultant_decision 有沒有設，都不該再出現在待處置清單。
        if (!id && state === 'undecided') {
          where.push('r.consultant_decision IS NULL');
          where.push(`(SELECT p.stage FROM placements p WHERE p.application_id = a.id
                        ORDER BY p.updated_at DESC LIMIT 1) IS NOT NULL
                      AND (SELECT p.stage FROM placements p WHERE p.application_id = a.id
                        ORDER BY p.updated_at DESC LIMIT 1) NOT IN ('CLOSED_LOST','CLOSED_WON','CLOSED_INTERNAL')
                      OR (SELECT p.stage FROM placements p WHERE p.application_id = a.id
                        ORDER BY p.updated_at DESC LIMIT 1) IS NULL`);
        }
        else if (!id && state) { where.push('r.consultant_decision = ?'); bind.push(state); }

        const sql =
          `SELECT r.id, r.created_at, r.recommend, r.consultant_decision, r.decided_at,
                  a.id AS app_id, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.expected_salary, a.available_date, a.interview_started_at, a.interview_ended_at,
                  a.handled_note,
                  (SELECT p.stage FROM placements p WHERE p.application_id = a.id
                    ORDER BY p.updated_at DESC LIMIT 1) AS pipeline_stage,
                  (SELECT p.updated_at FROM placements p WHERE p.application_id = a.id
                    ORDER BY p.updated_at DESC LIMIT 1) AS pipeline_updated_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id) AS turns,
                  substr(r.content_md, 1, 400) AS preview
             FROM reports r JOIN applications a ON a.id = r.application_id
            WHERE ${where.join(' AND ')}
            ORDER BY r.created_at DESC LIMIT ? OFFSET ?`;
        const { results } = await env.DB.prepare(sql).bind(...bind, limit, offset).all();

        const cnt = await env.DB.prepare(
          `SELECT COUNT(*) AS n FROM reports r JOIN applications a ON a.id = r.application_id
            WHERE ${where.join(' AND ')}`
        ).bind(...bind).first();

        const jobs = await env.DB.prepare(
          `SELECT DISTINCT a.job_slug AS slug, a.job_title AS title
             FROM reports r JOIN applications a ON a.id = r.application_id
            ORDER BY a.job_slug`
        ).all();

        return json(request, {
          ok: true, total: cnt ? cnt.n : 0, reports: results || [],
          jobs: jobs.results || [], limit, offset,
        });
      }

      // 單份報告：全文 + 逐字稿。逐字稿是法律證據，不刪也不改。
      if (p.startsWith('/admin/report/')) {
        const rid = decodeURIComponent(p.slice('/admin/report/'.length));
        const r = await env.DB.prepare(
          `SELECT r.*, a.name, a.email, a.phone, a.job_slug, a.job_title,
                  a.expected_salary, a.available_date, a.location_ok, a.note,
                  a.utm_source, a.utm_medium, a.utm_campaign, a.referrer,
                  a.interview_started_at, a.interview_ended_at, a.resume_file_id, a.resume_url,
                  a.manual_stage, a.manual_stage_note, a.manual_stage_by, a.manual_stage_at,
                  a.client_interview_labels
             FROM reports r JOIN applications a ON a.id = r.application_id
            WHERE r.id = ?`
        ).bind(rid).first();
        if (!r) return json(request, { ok: false, error: '找不到這份報告' }, 404);

        const { results } = await env.DB.prepare(
          `SELECT role, content, created_at FROM messages
            WHERE application_id = ? ORDER BY id ASC LIMIT 400`
        ).bind(r.application_id).all();

        // 2026-08-13 加：顧問後台階段列要用的資料——面談安排（第一到第四階段）
        // 跟 placements（錄取／報到／到職關懷）都是階段判斷需要的，一起帶回去，
        // 前端才不用另外打好幾支 API 才能畫出這個人選卡在哪一關。
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id = ? ORDER BY stage ASC, created_at ASC`
        ).bind(r.application_id).all();
        const placement = await env.DB.prepare(
          `SELECT stage, onboard_date, offer_response, onboard_confirm_status, candidate_care_log, close_reason
             FROM placements WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
        ).bind(r.application_id).first();

        let repRoundLabels = {};
        try { repRoundLabels = JSON.parse(r.client_interview_labels || '{}'); } catch { repRoundLabels = {}; }
        const repJobStageKeys = await getJobStageKeys(env, r.job_slug);
        return json(request, { ok: true, report: r, transcript: results || [], appointments: appts || [], placement: placement || null,
                               stage: resolveStage(r, r, appts || [], placement, repRoundLabels, repJobStageKeys) });
      }

      // 顧問編輯「給客戶看的報告版本」——content_md 是內部原文（可能有直白的
      // 評估用語、跟其他人選的比較），從來沒有客戶能看到的版本存在過。
      // 這裡另開一欄，顧問從 content_md 刪修出一份給客戶看，兩份完全分開，
      // 存這欄不會動到原始報告。
      if (p.startsWith('/admin/report/') && p.endsWith('/client-version') && request.method === 'POST') {
        const rid = decodeURIComponent(p.slice('/admin/report/'.length, -'/client-version'.length));
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (b.content_client_md === undefined) return json(request, { ok: false, error: '缺 content_client_md' }, 400);
        const r = await env.DB.prepare(
          `UPDATE reports SET content_client_md=? WHERE id=?`
        ).bind(String(b.content_client_md || '').trim() || null, rid).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這份報告' }, 404);
        return json(request, { ok: true });
      }

      // 顧問處置回寫。這一步是整套流程會不會變準的關鍵——
      // 沒有實際處置可以比對，AI 的判準永遠停在「看起來合理」。
      if (p === '/admin/decide-report' && request.method === 'POST') {
        const b = await request.json();
        if (!b.id || !b.decision) return json(request, { ok: false, error: '缺 id 或 decision' }, 400);
        const now = nowTaipei();
        await env.DB.prepare(
          `UPDATE reports SET consultant_decision=?, decided_at=? WHERE id=?`
        ).bind(String(b.decision), now, b.id).run();
        if (b.app_status) {
          await env.DB.prepare(`UPDATE applications SET status=?, handled_by=?, handled_note=? WHERE id=?`)
            .bind(b.app_status, b.by || null, b.note || null, b.app_id).run();
        }

        // 2026-08-04：顧問按「轉給客戶」＝這個案子送件了。在此之前，送件之後就沒有任何
        // 狀態被記下來——案子卡在客戶那邊三週不會有人發現。這裡自動開一筆 placements，
        // 之後的面試／offer／入職／保證期都掛在那一筆上。
        // 只在 forwarded 建；need_more 與 rejected 都還沒送出去，不該進 pipeline。
        let placement_id = null;
        if (String(b.decision) === 'forwarded' && b.app_id) {
          // 同一個案子被按第二次不要開出第二筆
          const dup = await env.DB.prepare(
            `SELECT id FROM placements WHERE application_id = ?1`
          ).bind(b.app_id).first();
          if (dup) {
            placement_id = dup.id;
          } else {
            const app = await env.DB.prepare(
              `SELECT a.name, a.job_slug, a.job_title, j.client_name, j.company_id
                 FROM applications a LEFT JOIN jobs j ON j.slug = a.job_slug
                WHERE a.id = ?1`
            ).bind(b.app_id).first();
            if (app) {
              const r = await env.DB.prepare(
                `INSERT INTO placements
                   (application_id, candidate_name, job_slug, job_title, client_name,
                    stage, stage_since, owner, note)
                 VALUES (?1, ?2, ?3, ?4, ?5, 'SUBMITTED', date('now','+8 hours'), ?6, ?7)`
              ).bind(b.app_id, app.name || '（未填姓名）', app.job_slug || null,
                     app.job_title || '（未填職缺）',
                     // client_name 可能沒填（職缺沒綁客戶）。不要寫死成空字串，
                     // 之後看板上會分不出「沒填」跟「真的沒有客戶」。
                     app.client_name || '（職缺未綁客戶）',
                     b.by || null, b.note || null).run();
              placement_id = r.meta?.last_row_id ?? null;
            }
            // 2026-08-25 加：「轉給客戶」預設推薦給這個人選原本應徵的那家客戶——
            // 一個人選其實可以被推薦給好幾家客戶（同一份人才被拿去 pitch 多家
            // 相似職缺是正常的獵頭作法），單靠 job.company_id 隱含只認得「原本
            // 應徵的那家」，client portal 端無從得知「還有沒有其他客戶也收到」。
            // 這裡把「轉給客戶」的預設對象明確寫進 candidate_forwards，之後要
            // 追加推薦給其他客戶就呼叫 /admin/forward-candidate，不會互相蓋掉
            // （UNIQUE(application_id, company_id)，同一家公司按第二次不會重複）。
            if (app && app.company_id) {
              const cfr2 = await env.DB.prepare(
                `INSERT OR IGNORE INTO candidate_forwards (id, application_id, company_id, forwarded_by, forwarded_at, note)
                 VALUES (?, ?, ?, ?, ?, ?)`
              ).bind(uid(), b.app_id, app.company_id, b.by || null, now, b.note || null).run();
              if (cfr2.meta && cfr2.meta.changes) {
                await triggerJobForms(env, { applicationId: b.app_id, companyId: app.company_id, jobSlug: app.job_slug, by: b.by });
              }
            }
          }
        }
        if (b.app_id) await notifyLineProgress(env, b.app_id);
        return json(request, { ok: true, decided_at: now, placement_id });
      }

      // 2026-08-25 加：把同一個人選「追加推薦」給另一家客戶（不是原本應徵的那家）——
      // 一個人選可以同時被推薦給好幾家客戶，這裡才是明確記錄「推薦給哪家」的地方，
      // 不是只能靠 job.company_id 猜。要求 report 已經是 forwarded（品質先過關才能
      // 拿去 pitch 其他客戶，不能跳過審核直接推）。
      if (p === '/admin/forward-candidate' && request.method === 'POST') {
        const b = await request.json().catch(() => ({}));
        if (!b.application_id || !b.company_id) {
          return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        }
        const report = await env.DB.prepare(
          `SELECT id, consultant_decision FROM reports WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
        ).bind(b.application_id).first();
        // ⚠️ 2026-09-01 修：原本「沒有報告」跟「有報告但顧問決定不推」共用同一個
        // 擋法，結果變成廖若辰那種真實案例的死路——這個人根本沒有阿財面談報告
        // （職缺選的是「請顧問幫我評估」，面談中斷沒產出報告），卡片上完全沒有
        // 「決定推不推」的按鈕可以按，卻被這裡擋著說「還沒通過審核」，變成一個
        // 顧問哪裡都按不到、人選卡片動彈不得的羅生門。沒有報告＝沒有東西可以
        // 「審核」，這種情況本來就該放行（等同顧問手動推薦，跟手動新增人選
        // 那條路徑同一套信任模型）；有報告但顧問明確決定 need_more/rejected，
        // 才是真的要擋的情境。
        if (report && report.consultant_decision && report.consultant_decision !== 'forwarded') {
          return json(request, { ok: false, error: '這位人選的初篩報告顧問決定「' +
            (report.consultant_decision === 'rejected' ? '婉拒' : '需補問') + '」，不能直接推薦給其他客戶——請先去初篩報告改決定，或確認過再手動推薦' }, 400);
        }
        const company = await env.DB.prepare(`SELECT id, display_name FROM client_companies WHERE id = ?`)
          .bind(b.company_id).first();
        if (!company) return json(request, { ok: false, error: '找不到這家客戶' }, 404);
        // 2026-09-01 加：job_slug 讓每一筆推薦紀錄自己記自己的職缺，不再共用
        // application 本身唯一的那一個——同一位人選可以同時推薦給 A 客戶的甲
        // 職缺、B 客戶的乙職缺。沒傳就照舊落回 application 自己的 job_slug
        // （相容舊呼叫端，不強迫每個地方都要改）。
        const app = await env.DB.prepare(`SELECT job_slug FROM applications WHERE id = ?`)
          .bind(b.application_id).first();
        const jobSlug = String(b.job_slug || (app && app.job_slug) || '').trim() || null;
        const now = nowTaipei();
        const cfr3 = await env.DB.prepare(
          `INSERT OR IGNORE INTO candidate_forwards (id, application_id, company_id, forwarded_by, forwarded_at, note, job_slug)
           VALUES (?, ?, ?, ?, ?, ?, ?)`
        ).bind(uid(), b.application_id, b.company_id, b.by || null, now, b.note || null, jobSlug).run();
        if (cfr3.meta && cfr3.meta.changes) {
          await triggerJobForms(env, { applicationId: b.application_id, companyId: b.company_id, jobSlug, by: b.by });
        }
        // 2026-09-01 加：按下推薦本身就是顧問明確做的決定，不該還要求顧問回頭
        // 到報告畫面再按一次「推薦」——沒有報告（廖若辰那種手動新增、還沒面談的
        // 案例）或報告還沒下決定，這裡順便補上，「等我決定」分頁才會正確把這人
        // 選移走、客戶那邊的「顧問階段」才會正確顯示已完成。已經有明確決定的
        // （不管是 forwarded 還是別的）不覆蓋，尊重顧問原本在報告畫面做的決定。
        if (report && !report.consultant_decision) {
          await env.DB.prepare(`UPDATE reports SET consultant_decision='forwarded', decided_at=? WHERE id=?`)
            .bind(now, report.id).run();
        }
        return json(request, { ok: true, company: company.display_name, forwarded_at: now, job_slug: jobSlug });
      }

      // 誤按「追加推薦」給錯客戶時可以撤銷——只刪 candidate_forwards 這筆連結，
      // 不動 applications／reports，人選本身不會被刪掉，只是那家客戶的 portal
      // 不會再看到他。真的錯推出去、客戶都已經看到人的情況，這支救不了，
      // 那要跟客戶說一聲，不是系統能處理的事。
      if (p === '/admin/pipeline/unforward-candidate' && request.method === 'POST') {
        const b = await request.json().catch(() => ({}));
        if (!b.application_id || !b.company_id) {
          return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        }
        const row = await env.DB.prepare(
          `SELECT cf.id, a.name AS candidate_name, cc.display_name AS company_name
             FROM candidate_forwards cf
             JOIN applications a ON a.id = cf.application_id
             LEFT JOIN client_companies cc ON cc.id = cf.company_id
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(b.application_id, b.company_id).first();
        if (!row) return json(request, { ok: false, error: '找不到這筆推薦紀錄' }, 404);
        await env.DB.prepare(
          `DELETE FROM candidate_forwards WHERE application_id = ? AND company_id = ?`
        ).bind(b.application_id, b.company_id).run();
        notify(env, `↩️ ${b.by || '顧問'} 取消了推薦：${row.candidate_name} → ${row.company_name || '（客戶）'}`,
          { message_thread_id: THREAD.intake }).catch(() => {});
        return json(request, { ok: true });
      }

      // 顧問主動傳訊息給用人單位（不是回覆客戶先傳來的）——原本訊息串只有
      // 「客戶傳→顧問在 TG 回覆」這一條路，顧問想主動告知進度時完全沒有入口，
      // 客戶的通知鈴鐺當然不會響。這支直接寫進同一張 candidate_messages，
      // 客戶那邊會跟顧問在 TG 回的訊息看起來一樣，portal 端不用分兩種邏輯。
      if (p === '/admin/pipeline/message-client' && request.method === 'POST') {
        const b = await request.json().catch(() => ({}));
        const content = String(b.content || '').trim();
        if (!b.application_id || !b.company_id) {
          return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        }
        if (!content) return json(request, { ok: false, error: '訊息不能是空的' }, 400);
        const row = await env.DB.prepare(
          `SELECT cf.id, a.name AS candidate_name, a.job_slug, j.title AS job_title
             FROM candidate_forwards cf
             JOIN applications a ON a.id = cf.application_id
             LEFT JOIN jobs j ON j.slug = a.job_slug
            WHERE cf.application_id = ? AND cf.company_id = ?`
        ).bind(b.application_id, b.company_id).first();
        if (!row) return json(request, { ok: false, error: '找不到這筆推薦紀錄' }, 404);
        const company = await env.DB.prepare(
          `SELECT id, display_name, tg_topic_id FROM client_companies WHERE id = ?`
        ).bind(b.company_id).first();
        if (!company) return json(request, { ok: false, error: '找不到這家客戶' }, 404);
        const now = nowTaipei();
        const ins = await env.DB.prepare(
          `INSERT INTO candidate_messages (application_id, company_id, from_role, content, from_name, created_at)
           VALUES (?, ?, 'consultant', ?, ?, datetime('now','+8 hours'))`
        ).bind(b.application_id, b.company_id, content, b.by || null).run();
        await env.DB.prepare(
          `UPDATE candidate_forwards SET client_unread_count = COALESCE(client_unread_count,0) + 1
            WHERE application_id = ? AND company_id = ?`
        ).bind(b.application_id, b.company_id).run();
        const topicId = await getOrCreateClientTopic(env, company);
        const tgId = await notifyAndCaptureId(env,
          `📤 ${b.by || '顧問'} 主動傳訊息給 ${company.display_name}\n` +
          `人選：${row.candidate_name}${row.job_title ? `\n職缺：${row.job_title}` : ''}\n\n${content}`,
          topicId ? { message_thread_id: topicId } : { message_thread_id: THREAD.intake });
        if (ins.meta && ins.meta.last_row_id && tgId) {
          await env.DB.prepare(`UPDATE candidate_messages SET tg_message_id=? WHERE id=?`)
            .bind(tgId, ins.meta.last_row_id).run();
        }
        return json(request, { ok: true });
      }

      // 這位人選目前被明確推薦給了哪些客戶——顧問後台用來顯示「已推薦給：A公司、B公司」，
      // 不用再靠猜。
      if (p.startsWith('/admin/candidate-forwards/') && request.method === 'GET') {
        const appId = decodeURIComponent(p.slice('/admin/candidate-forwards/'.length));
        const app = await env.DB.prepare(
          `SELECT id, job_slug, interview_started_at, interview_ended_at FROM applications WHERE id = ?`
        ).bind(appId).first();
        const { results } = await env.DB.prepare(
          `SELECT cf.company_id, cc.display_name, cf.forwarded_at,
                  cf.manual_stage, cf.client_interview_labels,
                  COALESCE(cf.job_slug, ?) AS job_slug,
                  j.title AS job_title
             FROM candidate_forwards cf
             JOIN client_companies cc ON cc.id = cf.company_id
             LEFT JOIN jobs j ON j.slug = COALESCE(cf.job_slug, ?)
            WHERE cf.application_id = ? ORDER BY cf.forwarded_at DESC`
        ).bind(app && app.job_slug, app && app.job_slug, appId).all();
        // 2026-08-26 加：每一家客戶的面試進度是各自獨立的，這裡把每家目前
        // 走到哪一關一起帶回去，顧問後台的人選卡片才能一眼看到
        // 「律准：第二階段／弘昌：錄取」，不用一家一家點進去查。
        // 2026-09-01 改：一位人選現在可以推薦給不同客戶的不同職缺，每筆推薦
        // 各自的職缺輪數設定可能不一樣（A家職缺只有一階段、B家職缺有兩階段），
        // 不能再共用同一份 jobStageKeys，要一筆一筆各自查各自職缺的設定。
        const report = await env.DB.prepare(
          `SELECT consultant_decision FROM reports WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
        ).bind(appId).first();
        const { results: appts } = await env.DB.prepare(
          `SELECT stage, status, confirmed_slot FROM interview_appointments
            WHERE application_id = ? ORDER BY stage ASC, created_at ASC`
        ).bind(appId).all();
        const cfListJobStageKeysMap = await getJobStageKeysMap(env, (results || []).map((f) => f.job_slug));
        const forwards = (results || []).map((f) => {
          let labels = {};
          try { labels = JSON.parse(f.client_interview_labels || '{}'); } catch { labels = {}; }
          const info = resolveStage({ ...app, manual_stage: f.manual_stage }, report, appts || [], null, labels, cfListJobStageKeysMap[f.job_slug]);
          return {
            ...f,
            stage_key: info.effective_key,
            stage_label: info.effective_key
              ? (info.steps.find((s) => s.key === info.effective_key) || {}).lb
              : '尚未安排面試',
            // 完整的 steps 讓顧問後台能一家畫一條進度條，不是只有一行文字
            steps: info.steps,
            manual_active: info.manual_active,
          };
        });
        return json(request, { ok: true, forwards });
      }

      // 2026-08-13 加：顧問手動指定候選人目前卡在哪一關（跟自動判斷並存）。
      // stage 傳 STAGE_ORDER 的 key（screening/confirm/stage1/stage2/stage3/stage4/offer/onboard/care），
      // 傳空字串或不傳就是清除手動指定，交回自動判斷。高水位規則見 resolveStage()。
      // 2026-08-13 加：暫時性的唯讀檢查端點——查現有 LINE 圖文選單的實際結構
      // （按鈕動作是 uri／message／richmenu-switch 哪一種），才能判斷「企業合作」
      // 底下「我想委託招募」「成功案例」這兩個按鈕能不能直接用 API 改，還是要請
      // Jacky 自己去 LINE 官方帳號管理後台手動改。純讀取，不會動到任何現有設定。
      // 2026-08-14 加：直接問 LINE 這個頻道現在真正設定的 webhook 網址是什麼，
      // 不用再靠猜或翻舊 comment——純讀取，不改任何設定。
      if (p === '/admin/line-webhook-endpoint' && request.method === 'GET') {
        const r = await fetch('https://api.line.me/v2/bot/channel/webhook/endpoint', {
          headers: { authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` },
        });
        const body = await r.json();
        return json(request, { ok: r.ok, status: r.status, body });
      }

      if (p === '/admin/line-richmenu-list' && request.method === 'GET') {
        const r = await fetch('https://api.line.me/v2/bot/richmenu/list', {
          headers: { authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` },
        });
        const body = await r.json();
        return json(request, { ok: r.ok, status: r.status, body });
      }

      // 2026-08-13 加：把「企業合作」圖文選單裡「我想委託招募」（message 型按鈕，
      // 送出固定文字但程式碼裡沒有對應的關鍵字處理，等於按了沒反應）跟「成功案例」
      // （uri 型按鈕，但連結指到 /about/，不是真的案例頁）這兩顆按鈕，改成 uri 型
      // 直接連到新做好的 /commission-recruiting/ 與 /success-stories/。
      // LINE 的圖文選單一旦建立就不能原地修改，只能整份重建再把 alias 指過去——
      // 舊的那份先保留不刪，確認新的沒問題之後再手動清掉。
      if (p === '/admin/line-richmenu-fix-enterprise' && request.method === 'POST') {
        const OLD_ID = 'richmenu-612c8e3d962383b85e88c82171562e99';
        const ALIAS_ID = 'step1ne-enterprise-2026-08';
        const headers = { authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` };

        const listR = await fetch(`https://api.line.me/v2/bot/richmenu/${OLD_ID}`, { headers });
        const oldMenu = await listR.json();
        if (!listR.ok) return json(request, { ok: false, step: 'get-old-menu', error: oldMenu }, 500);

        const UTM = 'utm_source=line&utm_medium=richmenu&utm_campaign=citizen-recruiter-menu';
        const newAreas = oldMenu.areas.map((a) => {
          if (a.action.type === 'message' && a.action.text === '我想了解企業合作方案') {
            return { bounds: a.bounds, action: { type: 'uri', uri: `https://step1ne.com/commission-recruiting/?${UTM}&utm_content=commission-recruiting` } };
          }
          if (a.action.type === 'uri' && a.action.uri.includes('utm_content=cases')) {
            return { bounds: a.bounds, action: { type: 'uri', uri: `https://step1ne.com/success-stories/?${UTM}&utm_content=success-stories` } };
          }
          return a;
        });

        const imgR = await fetch(`https://api-data.line.me/v2/bot/richmenu/${OLD_ID}/content`, { headers });
        if (!imgR.ok) return json(request, { ok: false, step: 'download-image', status: imgR.status }, 500);
        const imgType = imgR.headers.get('content-type') || 'image/png';
        const imgBuf = await imgR.arrayBuffer();

        const createR = await fetch('https://api.line.me/v2/bot/richmenu', {
          method: 'POST',
          headers: { ...headers, 'content-type': 'application/json' },
          body: JSON.stringify({
            size: oldMenu.size, selected: oldMenu.selected,
            name: oldMenu.name.replace(/-2026-08b?$/, '') + '-2026-08c',
            chatBarText: oldMenu.chatBarText, areas: newAreas,
          }),
        });
        const created = await createR.json();
        if (!createR.ok) return json(request, { ok: false, step: 'create-menu', error: created }, 500);
        const newId = created.richMenuId;

        const uploadR = await fetch(`https://api-data.line.me/v2/bot/richmenu/${newId}/content`, {
          method: 'POST', headers: { ...headers, 'content-type': imgType }, body: imgBuf,
        });
        if (!uploadR.ok) {
          const err = await uploadR.text();
          return json(request, { ok: false, step: 'upload-image', error: err, newId }, 500);
        }

        const aliasR = await fetch(`https://api.line.me/v2/bot/richmenu/alias/${ALIAS_ID}`, {
          method: 'POST', headers: { ...headers, 'content-type': 'application/json' },
          body: JSON.stringify({ richMenuId: newId }),
        });
        if (!aliasR.ok) {
          const err = await aliasR.text();
          return json(request, { ok: false, step: 'update-alias', error: err, newId }, 500);
        }

        return json(request, { ok: true, oldRichMenuId: OLD_ID, newRichMenuId: newId, aliasId: ALIAS_ID });
      }

      // 2026-08-14 加（第二版）：Jacky 要的其實是點下去直接在 LINE 對話裡跳出
      // 分層選單（求職者／企業窗口 → 選題目 → 看答案），不是連到網頁。上一版
      // 把左邊按鈕改成 uri 連到 /faq/，這版改成 postback 直接觸發 faqRoleFlex()，
      // 網頁 /faq/ 還留著（給 SEO／分享用），但選單按鈕本身不再連過去。
      // 右邊「我有問題想請顧問協助」那顆（message 型）維持原樣，單純轉真人。
      if (p === '/admin/line-richmenu-fix-faq' && request.method === 'POST') {
        const OLD_ID = 'richmenu-4742e862dac76084df6c96d9b4b0992f';
        const ALIAS_ID = 'step1ne-faq-2026-08';
        const headers = { authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` };

        const listR = await fetch(`https://api.line.me/v2/bot/richmenu/${OLD_ID}`, { headers });
        const oldMenu = await listR.json();
        if (!listR.ok) return json(request, { ok: false, step: 'get-old-menu', error: oldMenu }, 500);

        const newAreas = oldMenu.areas.map((a) => {
          if (a.action.type === 'uri' && a.action.uri.includes('utm_content=faq')) {
            return { bounds: a.bounds, action: { type: 'postback', data: 'faq_start', displayText: '常見問題' } };
          }
          return a;
        });

        const imgR = await fetch(`https://api-data.line.me/v2/bot/richmenu/${OLD_ID}/content`, { headers });
        if (!imgR.ok) return json(request, { ok: false, step: 'download-image', status: imgR.status }, 500);
        const imgType = imgR.headers.get('content-type') || 'image/png';
        const imgBuf = await imgR.arrayBuffer();

        const createR = await fetch('https://api.line.me/v2/bot/richmenu', {
          method: 'POST',
          headers: { ...headers, 'content-type': 'application/json' },
          body: JSON.stringify({
            size: oldMenu.size, selected: oldMenu.selected,
            name: oldMenu.name.replace(/-2026-08b?$/, '') + '-2026-08c',
            chatBarText: oldMenu.chatBarText, areas: newAreas,
          }),
        });
        const created = await createR.json();
        if (!createR.ok) return json(request, { ok: false, step: 'create-menu', error: created }, 500);
        const newId = created.richMenuId;

        const uploadR = await fetch(`https://api-data.line.me/v2/bot/richmenu/${newId}/content`, {
          method: 'POST', headers: { ...headers, 'content-type': imgType }, body: imgBuf,
        });
        if (!uploadR.ok) {
          const err = await uploadR.text();
          return json(request, { ok: false, step: 'upload-image', error: err, newId }, 500);
        }

        const aliasR = await fetch(`https://api.line.me/v2/bot/richmenu/alias/${ALIAS_ID}`, {
          method: 'POST', headers: { ...headers, 'content-type': 'application/json' },
          body: JSON.stringify({ richMenuId: newId }),
        });
        if (!aliasR.ok) {
          const err = await aliasR.text();
          return json(request, { ok: false, step: 'update-alias', error: err, newId }, 500);
        }

        return json(request, { ok: true, oldRichMenuId: OLD_ID, newRichMenuId: newId, aliasId: ALIAS_ID, newAreas });
      }

      if (p === '/admin/set-manual-stage' && request.method === 'POST') {
        const b = await request.json();
        if (!b.application_id) return json(request, { ok: false, error: '缺 application_id' }, 400);
        const stage = b.stage ? String(b.stage) : null;
        if (stage && !(stage in STAGE_INDEX)) {
          return json(request, { ok: false, error: '不合法的階段代碼' }, 400);
        }
        // 2026-08-26 加 company_id：顧問在某一家客戶的進度條上調整時，只動那一家；
        // 不帶就是動「整體進度」（人選還沒推給任何人的階段，例如阿財初審/顧問確認）。
        const result = await applyManualStage(env, {
          application_id: b.application_id, stage, note: b.note, by: b.by,
          company_id: b.company_id || null, onboard_date: b.onboard_date || null,
        });
        return json(request, { ok: true, ...result });
      }

      // 弘昌／帆宣案：顧問在人選卡片上補這個人的 LINE ID（自己跟人選要來的，
      // 不是系統自動抓）跟「給客戶看的摘要」——這兩欄跟 candidate_forwards.note
      // （內部限定）完全分開，client_note 是顧問自己選擇要不要讓客戶看到的內容。
      if (p === '/admin/pipeline/forward-detail' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id || !b.company_id) return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        const sets = [], bind = [];
        if (b.line_id_given !== undefined) { sets.push('line_id_given=?'); bind.push(String(b.line_id_given || '').trim() || null); }
        if (b.client_note !== undefined) { sets.push('client_note=?'); bind.push(String(b.client_note || '').trim() || null); }
        if (!sets.length) return json(request, { ok: false, error: '沒有要更新的欄位' }, 400);
        bind.push(b.application_id, b.company_id);
        const r = await env.DB.prepare(
          `UPDATE candidate_forwards SET ${sets.join(', ')} WHERE application_id=? AND company_id=?`
        ).bind(...bind).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這筆推薦紀錄' }, 404);
        return json(request, { ok: true });
      }

      // 2026-09-01 加：顧問對「這個人選推給這家客戶這個職缺」按不推薦——
      // 掛在 candidate_forwards 這一筆（application_id + company_id），不是整個
      // 人選、也不是整份面談報告（那是 /admin/decide-report 管的，語意不一樣）。
      // 標記本身不擋這個人選之後被重新推薦（同一組合可以再開一筆新的
      // candidate_forwards，見 /admin/forward-candidate），也不擋推去別家客戶——
      // 只是誠實記錄「這一組合，顧問看過，沒推薦」，企業用人單位需求表讀到
      // 這個標記時顯示「考慮過，顧問不推薦」，不是把人選整個藏起來。
      if (p === '/admin/pipeline/forward-decision' && request.method === 'POST') {
        let b; try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        if (!b.application_id || !b.company_id) return json(request, { ok: false, error: '缺 application_id 或 company_id' }, 400);
        const notRecommended = !!b.not_recommended;
        if (notRecommended && !String(b.reason || '').trim()) {
          return json(request, { ok: false, error: '請填不推薦的原因' }, 400);
        }
        const now = nowTaipei();
        const r = await env.DB.prepare(
          `UPDATE candidate_forwards
              SET advisor_not_recommended_at = ?, advisor_not_recommended_reason = ?
            WHERE application_id = ? AND company_id = ?`
        ).bind(notRecommended ? now : null, notRecommended ? String(b.reason).trim() : null,
               b.application_id, b.company_id).run();
        if (!r.meta || !r.meta.changes) return json(request, { ok: false, error: '找不到這筆推薦紀錄' }, 404);
        return json(request, { ok: true });
      }

      if (p === '/admin/decide' && request.method === 'POST') {
        const b = await request.json();
        await env.DB.prepare(
          `UPDATE applications SET status=?, handled_by=?, handled_note=? WHERE id=?`
        ).bind(b.status, b.by || null, b.note || null, b.id).run();
        return json(request, { ok: true });
      }
    }

    return json(request, { ok: false, error: 'not found' }, 404);
  },

  /** 排程：到了候選人自己選的時間就提醒他回來完成面談。 */
  async scheduled(_evt, env) {
    // ── 排程行事曆：今天該發的，送去產稿 ──
    // 顧問在行事曆上排「8/29 由 DR 發 BIM 工程師」，到那天這裡把它塞進
    // social_post_queue（status 留 NULL），本機的 social_post_agent.py 每 2 分鐘
    // 掃一次就會撿走、產稿、推 Telegram 給顧問按確認。
    // ⚠️ 這裡只負責「把排程變成待產稿」，不會自己發文——發文一律要人按確認。
    try {
      const { results: due } = await env.DB.prepare(
        `SELECT id, post_date, job_slug, account_id FROM social_schedule
          WHERE status='planned' AND post_date <= date('now','+8 hours')
          ORDER BY post_date LIMIT 20`).all();
      for (const d of due || []) {
        // 到期這一刻再檢查一次職缺還開著。排程可能是一週前排的，
        // 中間客戶結束招募了，那就不該再發。
        const job = await env.DB.prepare(
          `SELECT COALESCE(status,'open') AS status FROM jobs WHERE slug=?`).bind(d.job_slug).first();
        if (!job || job.status === 'closed') {
          await env.DB.prepare(`UPDATE social_schedule SET status='cancelled', updated_at=? WHERE id=?`)
            .bind(nowTaipei(), d.id).run();
          continue;
        }
        const q = await env.DB.prepare(
          `INSERT INTO social_post_queue (job_slug, account_id, requested_at) VALUES (?,?,?)`
        ).bind(d.job_slug, d.account_id, nowTaipei()).run();
        await env.DB.prepare(
          `UPDATE social_schedule SET status='queued', queue_id=?, updated_at=? WHERE id=?`
        ).bind(q.meta && q.meta.last_row_id, nowTaipei(), d.id).run();
      }
    } catch (e) { /* 排程轉檔失敗不要影響下面其他排程工作 */ }

    // ⚠️ 2026-08-19 加：LinkedIn 權杖自動續期。
    // LinkedIn 的 access token 只有 60 天，過期就發不出文——而且是無聲失敗，
    // 通常等到要發文那天才發現。這裡趕在到期前 14 天就換好。
    //
    // 跟 Threads 的續期是同一個目的、不同做法：Threads 那支是本機腳本
    // （refresh_threads_token.sh，launchd 每週跑），因為它還要同步 wrangler secret；
    // LinkedIn 的權杖只存在 D1，Worker 自己就能換，不必依賴本機有沒有開機。
    //
    // ⚠️ LinkedIn 續期回來不一定會給新的 refresh_token；沒給就沿用舊的，
    //    不可以覆蓋成 null，否則下一輪就永遠續不了了。
    try {
      const { results: expiring } = await env.DB.prepare(
        `SELECT id, label, refresh_token FROM social_accounts
          WHERE platform='linkedin' AND is_active=1 AND refresh_token IS NOT NULL
            AND token_expires_at IS NOT NULL
            AND token_expires_at <= datetime('now','+8 hours','+14 days')`
      ).all();
      for (const acc of (expiring || [])) {
        try {
          const r = await fetch('https://www.linkedin.com/oauth/v2/accessToken', {
            method: 'POST', headers: { 'content-type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({
              grant_type: 'refresh_token', refresh_token: acc.refresh_token,
              client_id: env.LINKEDIN_CLIENT_ID, client_secret: env.LINKEDIN_CLIENT_SECRET,
            }),
          });
          const d = await r.json();
          if (!d.access_token) throw new Error(JSON.stringify(d).slice(0, 200));
          const expAt = new Date(Date.now() + (Number(d.expires_in) || 5184000) * 1000 + 8 * 3600 * 1000)
            .toISOString().replace('T', ' ').slice(0, 19);
          await env.DB.prepare(
            `UPDATE social_accounts SET access_token=?, refresh_token=?, token_expires_at=? WHERE id=?`
          ).bind(d.access_token, d.refresh_token || acc.refresh_token, expAt, acc.id).run();
          await notify(env, `🔄 LinkedIn 權杖已自動續期：${acc.label}\n新的到期日 ${expAt.slice(0, 10)}`,
            { message_thread_id: THREAD.system });
        } catch (e) {
          // 續期失敗要吵——refresh token 也是會過期的（一年），
          // 到那時只能請顧問重按一次授權，不講就會靜靜地壞掉。
          await notify(env,
            `⚠️ LinkedIn 權杖續期失敗：${acc.label}\n${String(e).slice(0, 200)}\n\n`
            + `請重新授權：\nhttps://step1ne-recruit-api.aiagentg888.workers.dev/linkedin/auth?label=`
            + encodeURIComponent(String(acc.label).replace(/\s*–\s*LinkedIn$/, '')),
            { message_thread_id: THREAD.system });
        }
      }
    } catch (e) {
      await notify(env, `⚠️ LinkedIn 續期檢查失敗：${String(e).slice(0, 200)}`,
        { message_thread_id: THREAD.system }).catch(() => {});
    }

    // ⚠️ 2026-08-19 加：發文成效回填（Jacky 問「我們在抓哪個時間點發布成效好」，
    // 答案是原本根本沒在抓——連實際發布時間都沒存，只存了草稿產生時間）。
    // 每則貼文在發布後的前 3 天各抓一次 views／likes／replies，之後就不再抓：
    // Threads 的觸及幾乎都發生在前 48 小時，一直抓只是浪費配額。
    // 抓回來的數字配上 posted_at，才能回答「幾點發、哪個帳號、哪種職缺有人看」。
    try {
      const { results: toMeasure } = await env.DB.prepare(
        // ⚠️ 2026-08-27 加 platform='threads' 這個條件。原本會把 LinkedIn 的貼文
        // 也撈進來，然後拿 LinkedIn 的網址去問 graph.threads.net——永遠對不上，
        // 每 15 分鐘白跑一次，而且因為找不到帳號會退回全域的 Threads 金鑰，
        // 等於拿 A 帳號的權杖去查 B 平台的貼文。
        // LinkedIn 個人檔案的貼文本來就沒有公開的成效 API（只有企業專頁的
        // organizationalEntityShareStatistics 有），所以這裡不是漏做，是做不到。
        `SELECT q.id, q.url, q.account_id, q.posted_at
           FROM social_post_queue q
           JOIN social_accounts a ON a.id = q.account_id AND a.platform = 'threads'
          WHERE q.status='posted' AND q.url IS NOT NULL
            AND q.posted_at >= datetime('now','+8 hours','-3 days')
            AND (q.insights_at IS NULL OR q.insights_at <= datetime('now','+8 hours','-6 hours'))
          LIMIT 10`
      ).all();
      for (const row of (toMeasure || [])) {
        let token = env.THREADS_ACCESS_TOKEN, userId = env.THREADS_USER_ID;
        if (row.account_id) {
          const acc = await env.DB.prepare(
            `SELECT access_token, platform_user_id FROM social_accounts WHERE id=? AND platform='threads'`
          ).bind(row.account_id).first();
          if (acc) { token = acc.access_token; userId = acc.platform_user_id; }
        }
        if (!token || !userId) continue;
        try {
          // permalink 反查貼文 id——我們存的是給人看的網址，insights 要的是 id
          const lr = await fetch(`https://graph.threads.net/v1.0/${userId}/threads?` +
            new URLSearchParams({ fields: 'id,permalink,timestamp', limit: '25', access_token: token }));
          const ld = await lr.json();
          const hit = (ld.data || []).find((p) => p.permalink === row.url);
          if (!hit) continue;
          const ir = await fetch(`https://graph.threads.net/v1.0/${hit.id}/insights?` +
            new URLSearchParams({ metric: 'views,likes,replies', access_token: token }));
          const id2 = await ir.json();
          const met = {};
          for (const m of (id2.data || [])) {
            met[m.name] = (m.values && m.values[0] ? m.values[0].value : (m.total_value || {}).value) || 0;
          }
          await env.DB.prepare(
            `UPDATE social_post_queue SET views=?, likes=?, replies=?, insights_at=datetime('now','+8 hours') WHERE id=?`
          ).bind(met.views || 0, met.likes || 0, met.replies || 0, row.id).run();
        } catch { /* 單則抓不到不要影響其他則，下一輪會再試 */ }
      }
    } catch (e) {
      await notify(env, `⚠️ 發文成效回填失敗：${String(e).slice(0, 200)}`, { message_thread_id: THREAD.system }).catch(() => {});
    }

    // ⚠️ 2026-08-18 加：Threads 發文「卡在 posting」的自動對帳。
    // 真實案例：顧問按了「確認發布」，Worker 搶到鎖改成 posting 之後，發文
    // 途中整個請求被中斷（那天是剛好在部署新版），catch 沒機會跑到、鎖沒解開，
    // 這則就永遠停在 posting——之後每次按都只回「⏳ 正在發文中」，看起來就像
    // 按了沒反應。更糟的是其中一則其實已經發到 Threads 上了，只是沒回寫 url、
    // 也沒通知，等於「發出去了但沒人知道」。
    // 修法：每次 cron 掃 posting 超過 3 分鐘的（正常串文 30 秒內跑完），拿該
    // 帳號最近的貼文去比對草稿開頭：
    //   對得上 → 補回 posted＋permalink，並補一則帶連結的通知（Jacky 要求
    //            「有發布的一定要通知、要給連結」）。
    //   對不上 → 解鎖回 drafted 並通知可以重按，不會自動重發（避免重複貼文）。
    try {
      const { results: stuck } = await env.DB.prepare(
        `SELECT q.id, q.job_slug, q.account_id, q.draft, q.tg_message_id, j.title
           FROM social_post_queue q JOIN jobs j ON j.slug = q.job_slug
          WHERE q.status = 'posting'
            AND q.posting_at IS NOT NULL
            AND q.posting_at <= datetime('now','+8 hours','-3 minutes')
          LIMIT 10`
      ).all();
      for (const row of (stuck || [])) {
        let token = env.THREADS_ACCESS_TOKEN, userId = env.THREADS_USER_ID;
        if (row.account_id) {
          const acc = await env.DB.prepare(
            `SELECT access_token, platform_user_id FROM social_accounts WHERE id = ? AND platform='threads'`
          ).bind(row.account_id).first();
          if (acc) { token = acc.access_token; userId = acc.platform_user_id; }
        }
        // 比對用：把空白換行都拿掉取前 30 字，Threads 回傳的 text 是主文全文，
        // 開頭一定跟草稿第一段一致（草稿超過 500 字會被切成串文，但第一則的
        // 開頭不變）。
        const norm = (t) => String(t || '').replace(/\s+/g, '').slice(0, 30);
        let permalink = null;
        if (token && userId) {
          try {
            const r = await fetch(
              `https://graph.threads.net/v1.0/${userId}/threads?` +
              new URLSearchParams({ fields: 'id,text,permalink', limit: '10', access_token: token })
            );
            const d = await r.json();
            const hit = (d.data || []).find((p) => norm(p.text) === norm(row.draft));
            if (hit) permalink = hit.permalink || null;
            if (hit && !permalink) permalink = '（已發布，但查不到連結，請到 Threads 上確認）';
          } catch { /* 查不到就當作沒發，走解鎖那條，不會重發 */ }
        }
        // 有原始審核訊息就回覆它（forum 群組會自動落回同一個主題）；
        // 沒有的話退到系統回報主題，才不會掉到 General 裡沒人看到。
        const tgTail = row.tg_message_id
          ? { reply_to_message_id: row.tg_message_id }
          : { message_thread_id: THREAD.system };
        if (permalink) {
          await env.DB.prepare(`UPDATE social_post_queue SET status='posted', url=?, posting_at=NULL, posted_at=datetime('now','+8 hours') WHERE id=?`)
            .bind(permalink.startsWith('http') ? permalink : null, row.id).run();
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: env.TG_CHAT_ID, ...tgTail,
              text: `✅ 補通知｜${row.title}\n\n這則其實已經發到 Threads 上了，只是當時發文流程中途被中斷，沒有回寫紀錄也沒通知。\n\n▪️ 貼文連結：\n${permalink}`,
            }),
          }).catch(() => {});
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/editMessageReplyMarkup`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: env.TG_CHAT_ID, message_id: row.tg_message_id,
              reply_markup: { inline_keyboard: [[{ text: '✅ 已發到 Threads（補登）', callback_data: 'noop' }]] },
            }),
          }).catch(() => {});
        } else {
          await env.DB.prepare(`UPDATE social_post_queue SET status='drafted', posting_at=NULL WHERE id=?`)
            .bind(row.id).run();
          await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
            method: 'POST', headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              chat_id: env.TG_CHAT_ID, ...tgTail,
              text: `⚠️ 這則發文中途斷掉了，確認沒有發出去｜${row.title}\n\n已經解鎖，可以重新按上面那顆「確認發布」。（沒有自動重發，避免重複貼文）`,
            }),
          }).catch(() => {});
        }
      }
    } catch (e) {
      await notify(env, `⚠️ 發文對帳失敗：${String(e).slice(0, 200)}`, { message_thread_id: THREAD.system }).catch(() => {});
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
    `SELECT id, candidate_name, job_title, client_name, stage, onboard_date,
            guarantee_days, care_log,
            CAST(julianday('now','+8 hours') - julianday(stage_since) AS INTEGER)  AS days_in_stage,
            CAST(julianday('now','+8 hours') - julianday(onboard_date) AS INTEGER) AS days_since_onboard
       FROM placements
      WHERE stage NOT IN ('CLOSED_WON','CLOSED_LOST','CLOSED_INTERNAL')`
  ).all();
  const rows = results || [];
  if (!rows.length) return;

  // ── 一、停滯偵測。只列需要注意的，正常進行中的不列——
  //     全部列出來只會讓真正該關心的被淹沒。
  const over = [], near = [];
  for (const r of rows) {
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
