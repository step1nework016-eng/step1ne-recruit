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

const ORIGINS = ['https://step1ne.com', 'https://www.step1ne.com'];

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
};
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
    `這封信由系統自動發送，請勿直接回覆——有問題請走上面的 LINE，我們會盡快回覆您。` +
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

const cors = (req) => {
  const o = req.headers.get('origin') || '';
  return {
    'access-control-allow-origin': ORIGINS.includes(o) ? o : ORIGINS[0],
    'access-control-allow-methods': 'GET,POST,OPTIONS',
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

async function guardCompany(env, company) {
  const c = String(company || '').trim();
  if (!c) return null;
  const cv = nameVariants(c, '');
  const { results } = await env.DB.prepare(
    `SELECT name, aliases, relation, blocked_reason, via_client FROM clients`).all();
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
  return { filename: f.filename || 'attachment.pdf', content: b64 };
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
    await fetch('https://api.line.me/v2/bot/message/reply', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      },
      // LINE 單則文字上限 5000 字，這裡的回覆不可能逼近，不特別處理截斷
      body: JSON.stringify({ replyToken, messages: [{ type: 'text', text }] }),
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    // 回覆失敗不該讓 webhook 整支掛掉——LINE 沒收到訊息頂多是候選人再按一次
  }
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
    // 2026-08-13 Jacky 要求：要看得出是誰、哪一場、現在到哪一步，加 emoji 分段——
    // 候選人可能同時應徵過不只一個職缺，沒有名字跟日期會看不出這則在講哪一次。
    const text = `📋 ${prog.name} 您好\n\n`
      + `🗓 面試日期：${prog.interviewDate || '尚未面談'}\n`
      + `💼 應徵職缺：${prog.jobTitle}\n\n`
      + `📈 目前進度：\n${prog.message}`;
    for (const b of hits) await linePush(env, b.line_user_id, text);
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

// 單筆應徵的「候選人看得懂的現況」。
//
// ⚠️ 判斷邏輯直接複用 /admin/jobs 與 /admin/funnel 那一套漏斗分類
//    （applicants / screening / client_stage / offered / onboard / closed），
//    不要自己另外發明一套——不然候選人在 LINE 看到的階段會跟顧問後台看到的對不起來。
async function deriveApplicationProgress(env, appId) {
  const app = await env.DB.prepare(
    `SELECT id, name, job_slug, job_title, interview_state, handled_note,
            interview_started_at, created_at
       FROM applications WHERE id = ?`
  ).bind(appId).first();
  if (!app) return null;

  const report = await env.DB.prepare(
    `SELECT consultant_decision FROM reports
      WHERE application_id = ? ORDER BY created_at DESC LIMIT 1`
  ).bind(appId).first();

  const placement = await env.DB.prepare(
    `SELECT stage, onboard_date FROM placements
      WHERE application_id = ? ORDER BY updated_at DESC LIMIT 1`
  ).bind(appId).first();

  const jobTitle = app.job_title || app.job_slug || '這個職缺';
  const stageUp = placement ? String(placement.stage || '').toUpperCase() : '';
  let message;

  if (placement && placement.onboard_date) {
    // 到職——恭喜語氣
    message = '恭喜您已經到職了！之後有任何問題歡迎隨時透過選單聯繫顧問。';
  } else if (placement && stageUp === 'CLOSED_LOST') {
    // 已婉拒／結案——委婉但誠實，不用「淘汰」這種字眼
    // （用詞分寸參考顧問版／客戶版報告的既有規則：不確定的不編、但也不用傷人的說法）
    message = '這個職缺這次很可惜沒有繼續往下走，很抱歉沒有帶來好消息。'
      + '您的資料我們會留著，未來有更合適的職缺會再與您聯繫，也歡迎透過選單直接找顧問聊聊。';
  } else if (placement && ['OFFER', 'OFFER_ACCEPTED', 'HIRED'].includes(stageUp)) {
    message = '好消息，客戶已經決定邀請您加入，目前在談後續入職事宜，顧問會盡快與您確認細節。';
  } else if (placement && ['SUBMITTED', 'CLIENT_INTERVIEW', 'INTERVIEWING', 'INTERVIEW'].includes(stageUp)) {
    message = '您的資料已經送到客戶那邊，目前正在安排／進行客戶面試。'
      + (app.handled_note ? `顧問補充：${app.handled_note}` : '有進一步消息顧問會盡快與您聯繫。');
  } else if (report && report.consultant_decision === 'rejected') {
    message = '顧問審閱後，這次沒有把您推薦給這個職缺，很抱歉沒有帶來好消息。'
      + '歡迎持續留意我們之後釋出的其他職缺。';
  } else if (report && report.consultant_decision === 'need_more') {
    message = '顧問審閱後想再跟您確認一些細節，會盡快主動聯繫您，麻煩留意來電或訊息。';
  } else if (app.interview_state === 'done' || app.interview_state === 'paused') {
    message = '您的初談阿財已經收到了，顧問正在審閱中，通常 1-2 個工作天內會有進一步消息。';
  } else {
    message = '您的面談還沒有完成，建議點選單裡的「AI阿財面試」繼續完成初談。';
  }

  // 面試日期：有實際面談時間就用那個，還沒面談過就用應徵時間，
  // 讓候選人知道這則訊息是在講「哪一次」——他可能同時應徵過不只一個職缺。
  const dateRaw = app.interview_started_at || app.created_at || '';
  const interviewDate = dateRaw ? dateRaw.slice(5, 10).replace('-', '/') : '';

  return { jobTitle, message, name: app.name || '', interviewDate };
}

async function buildProgressReply(env, applicationIds) {
  const ids = Array.isArray(applicationIds) ? applicationIds : [];
  if (!ids.length) {
    return '目前查不到您的應徵紀錄，想直接找顧問，歡迎透過下方選單聯繫我們。';
  }
  const rows = [];
  for (const id of ids) {
    const row = await deriveApplicationProgress(env, id);
    if (row) rows.push(row);
  }
  if (!rows.length) {
    return '目前查不到您的應徵紀錄，想直接找顧問，歡迎透過下方選單聯繫我們。';
  }
  // 一個人可能同時應徵不只一個職缺，逐筆列出來，不要含糊帶過是哪一筆。
  // 格式跟 notifyLineProgress() 的主動推播同一套，候選人不會覺得兩邊講法不一樣。
  return rows.map((r) =>
    `📋 ${r.name} 您好\n\n🗓 面試日期：${r.interviewDate || '尚未面談'}\n`
    + `💼 應徵職缺：${r.jobTitle}\n\n📈 目前進度：\n${r.message}`
  ).join('\n\n───\n\n');
}

const LINE_ASK_PHONE_TEXT = '請提供您應徵時留的手機號碼，我幫您查詢目前的面試進度。';
const LINE_NO_MATCH_TEXT =
  '這支手機號碼查不到應徵紀錄，可能是留了別的號碼，或我們還沒收到您的申請。'
  + '想直接找顧問，歡迎透過下方選單聯繫我們。';

async function handleLineEvent(env, ev) {
  if (ev.type !== 'message' || !ev.message || ev.message.type !== 'text') return;
  const userId = ev.source && ev.source.userId;
  const replyToken = ev.replyToken;
  if (!userId || !replyToken) return;
  const text = String(ev.message.text || '').trim();
  const now = nowTaipei();

  const binding = await env.DB.prepare(
    `SELECT * FROM line_bindings WHERE line_user_id = ?`
  ).bind(userId).first();

  // 第一次出現：只在候選人主動觸發（按鈕／打字）時才介入，
  // 其他訊息不搶著回——這支只負責「查進度」這一件事，不要變成搶走
  // Jacky 之後可能在 LINE Official Account Manager 設的其他自動回覆的地盤。
  if (!binding) {
    if (text !== LINE_PROGRESS_TRIGGER) return;
    await env.DB.prepare(
      `INSERT INTO line_bindings (line_user_id, state, created_at, updated_at)
       VALUES (?, 'pending_phone', ?, ?)`
    ).bind(userId, now, now).run();
    return lineReply(env, replyToken, LINE_ASK_PHONE_TEXT);
  }

  if (binding.state === 'bound') {
    if (text !== LINE_PROGRESS_TRIGGER) return; // 已綁定的人閒聊不接手
    const msg = await buildProgressReply(env, safeJsonArray(binding.application_ids));
    return lineReply(env, replyToken, msg);
  }

  // state === 'pending_phone'：把這則訊息當手機號碼比對
  if (text === LINE_PROGRESS_TRIGGER) {
    // 已經在等他回手機號碼了，同樣的提示再說一次，不用另外講「你已經問過了」
    return lineReply(env, replyToken, LINE_ASK_PHONE_TEXT);
  }

  const norm = normalizePhone(text);
  if (norm.length < 8) {
    // 看起來不像手機號碼——不當一次有效嘗試，也不逼問，讓他自己決定要不要重打
    return;
  }

  // 應徵量對這間公司的規模來說不大，直接掃全表在 JS 裡正規化比對，
  // 比在 SQL 裡處理各種手機號碼格式（有無 -、+886）簡單也不容易漏比對。
  const { results } = await env.DB.prepare(
    `SELECT id, name, phone FROM applications
      WHERE superseded_by IS NULL AND phone IS NOT NULL AND phone != ''`
  ).all();
  const matched = (results || []).filter((r) => normalizePhone(r.phone) === norm);

  if (!matched.length) {
    await env.DB.prepare(
      `UPDATE line_bindings SET updated_at = ? WHERE line_user_id = ?`
    ).bind(now, userId).run();
    return lineReply(env, replyToken, LINE_NO_MATCH_TEXT);
  }

  const ids = matched.map((m) => m.id);
  await env.DB.prepare(
    `UPDATE line_bindings SET state='bound', phone=?, application_ids=?, bound_at=?, updated_at=?
      WHERE line_user_id = ?`
  ).bind(text, JSON.stringify(ids), now, now, userId).run();

  // 配對紀錄要讓顧問在後台看得到，這裡先推一則 Telegram 通知——
  // 跟「顧問人選回報區」不是同一件事，放系統回報主題就好，不用麻煩顧問回應。
  await notify(env,
    `🔗 LINE 查詢進度：${matched[0].name} 完成手機號碼綁定（${matched.length} 筆應徵紀錄）`,
    { message_thread_id: THREAD.system });

  const msg = await buildProgressReply(env, ids);
  return lineReply(env, replyToken, msg);
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
           `您應徵的「${app.job_title || app.job_slug}」，顧問評估後想進一步了解您的狀況。`,
           `下面的連結是您的專屬面談室，AI 面談助理阿財會先跟您做初步了解，大約 20 到 30 分鐘。`,
           `請把這封信留著——中途離開的話，用同一個連結就能回到原本的對話。`]
        : [`${app.name} 您好，`,
           `您應徵的「${app.job_title || app.job_slug}」，顧問評估後想進一步了解您的狀況。`,
           `目前面談室有其他候選人正在進行中，請用下面的連結選一個方便的時段，`,
           `我們會在時間到之前提醒您。請把這封信留著。`],
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
       `感謝您應徵「${app.job_title || app.job_slug}」。顧問看過您的資料後，`,
       `覺得「${target.title}」這個機會可能更適合您目前的狀況。`,
       `有興趣的話歡迎透過下面的連結應徵，會由顧問優先為您安排。`],
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === 'OPTIONS') return new Response(null, { headers: cors(request) });
    if (p === '/health') return json(request, { ok: true });

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
                a.remind_at, a.interview_state,
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

      // 已經有約定時間的人，交卷不等於現在就要談。
      // ⚠️ 2026-08-10 加：原本這裡無條件把人設成 ready/now，等於把他自己（或顧問）
      // 排好的時段直接洗掉，然後在他方便填測驗的那個下午把他拉進面談室。
      // 「先填測驗、晚點再談」是很正常的安排，不該被系統當成錯誤。
      if (app.interview_state !== 'active' && app.status === 'scheduled' && app.remind_at) {
        const stillFuture = await env.DB.prepare(
          `SELECT 1 AS f WHERE datetime(?) > datetime('now','+8 hours')`
        ).bind(app.remind_at).first();
        if (stillFuture) {
          return json(request, {
            ok: true, route: 'scheduled',
            remind_at: app.remind_at, chat_token: app.chat_token,
          });
        }
      }

      const load = await liveLoad(env);
      if (load.active + load.pending < LIVE_LIMIT) {
        await env.DB.prepare(`UPDATE applications SET status='ready', interview_mode='now' WHERE id=?`)
          .bind(app.id).run();
        // ⚠️ 時段也要一起回。2026-08-11 Jacky 反應「測驗做完怎麼沒有可以選
        //    直接開始面試或預約」——原因就在這裡：面談室有空時只回 route:'now'，
        //    前端只長得出「進入面談室」一顆按鈕。現在不方便的人沒有第二條路，
        //    只能把視窗關掉，而關掉的人多半不會再回來。
        //    有空 ≠ 他現在方便。兩條路都要給他，讓他自己選。
        return json(request, { ok: true, route: 'now', chat_token: app.chat_token,
                               slots: await openSlots(env) });
      }
      // 額滿 → 只能預約。誠實告訴他現在有幾個人在談，不要只說「請稍後」
      return json(request, {
        ok: true, route: 'book',
        busy: load.active + load.pending,
        slots: await openSlots(env),
      });
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
        if (rm && Number(rm.message_thread_id) === THREAD.report &&
            String(rm.text || '').trim()) {
          const who = (rm.from && (rm.from.username || rm.from.first_name)) || '顧問';
          // 自己發的確認訊息不要再收一次
          if (!(rm.from && rm.from.is_bot)) {
            await env.DB.prepare(
              `INSERT INTO consultant_reports (id, created_at, tg_message_id, sender, raw_text, status, updated_at)
               VALUES (?,?,?,?,?, 'new', ?)`
            ).bind(uid(), nowTaipei(), rm.message_id, who, rm.text.trim(), nowTaipei()).run();
          }
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
            if (stage) await notifyLineProgress(env, app.id);
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

      return json(request, { ok: true });
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

      // DISC 是表單裡的量表算出來的分數（前端算好才送過來），不是這裡現算
      const discOk = ['disc_d', 'disc_i', 'disc_s', 'disc_c']
        .every((k) => Number.isInteger(b[k]));

      await env.DB.prepare(
        `INSERT INTO applications
         (id, created_at, job_slug, job_title, name, email, phone,
          expected_salary, available_date, location_ok,
          resume_file_id, resume_url, note,
          utm_source, utm_medium, utm_campaign, referrer, status, consent_at,
          disc_d, disc_i, disc_s, disc_c, disc_primary)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?, ?,?,?,?,?)`
      ).bind(
        id, now, b.job_slug, b.job_title || null, b.name, b.email, b.phone || null,
        b.expected_salary || null, b.available_date || null, b.location_ok || null,
        fileId, b.resume_url || null, b.note || null,
        b.utm_source || null, b.utm_medium || null, b.utm_campaign || null,
        b.referrer || null, now,
        discOk ? b.disc_d : null, discOk ? b.disc_i : null,
        discOk ? b.disc_s : null, discOk ? b.disc_c : null,
        discOk ? (b.disc_primary || null) : null
      ).run();

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
      await sendMail(
        env, b.email,
        `已收到您的應徵：${b.job_title || b.job_slug}`,
        [`${b.name} 您好，`,
         `我們已經收到您應徵「${b.job_title || b.job_slug}」的資料。`,
         `接下來分成兩步，兩個連結都在下面。請把這封信留著，這是您回來繼續的唯一入口。`],
        // ⚠️ 兩步都要有按鈕。之前這封信只給「開始測驗」一顆，候選人不知道
        // 還有第二步，有人直接點面談連結、被系統導回測驗頁，以為是當機就走了。
        [
          { title: '第一步・工作風格測驗（5–7 分鐘）',
            body: '沒有標準答案，照直覺選就好，做完會馬上給您一份個人的工作風格報告。'
                + '做完不一定要馬上面談——畫面會告訴您下一步怎麼走。',
            url: `https://step1ne.com/assessment/?t=${chatToken}`, text: '開始測驗' },
          { title: '第二步・初步面談（約 20–30 分鐘）',
            body: '由 AI 面談助理「阿財」進行，用打字的就可以。'
                + '這個連結要先完成第一步才會開啟；如果是約定時間的面談，時間到我們也會再寄一次提醒。'
                + '過程中有任何狀況，面談室右上角有「回報問題」按鈕，我們會即時收到。',
            url: `https://step1ne.com/interview/?t=${chatToken}`, text: '進入面談室' },
        ]
      );

      return json(request, {
        ok: true, id, chat_token: chatToken,
        next: 'assessment',
        assessment_url: `/assessment/?t=${chatToken}`,
      });
    }


    // ── 面談室 ──
    // token 放在網址就是權限本身：候選人不必註冊帳號（多一道就少一半完成率），
    // 但也代表這個網址等於逐字稿的鑰匙，所以 token 要夠長且不可推導。
    if (p.startsWith('/chat/')) {
      const seg = p.split('/').filter(Boolean); // ['chat', token, action?]
      const token = seg[1] || '';
      const action = seg[2] || '';
      if (token.length < 32) return json(request, { ok: false, error: 'bad token' }, 400);

      const app = await env.DB.prepare(
        `SELECT a.id, a.name, a.job_slug, a.job_title, a.interview_state, a.status,
                a.interview_started_at, a.interview_ended_at,
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
        if (app.interview_state === 'done') {
          return json(request, { ok: false, error: '這場面談已經結束了' }, 409);
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
        return json(request, {
          ok: true,
          name: app.name,
          job_title: app.job_full_title || app.job_title || app.job_slug,
          state: app.interview_state,
          messages: results || [],
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

      // LINE「查詢面試進度」的配對紀錄清單。Jacky 明確要求：這件事顧問要看得到，
      // 不能只是候選人自己在 LINE 上查完就沒有任何紀錄留在後台。
      if (p === '/admin/line-bindings' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT line_user_id, state, phone, application_ids, created_at, bound_at, updated_at
             FROM line_bindings ORDER BY updated_at DESC LIMIT 500`
        ).all();
        const rows = [];
        for (const r of results || []) {
          const ids = safeJsonArray(r.application_ids);
          let apps = [];
          if (ids.length) {
            const placeholders = ids.map(() => '?').join(',');
            const { results: appRows } = await env.DB.prepare(
              `SELECT id, name, job_slug, job_title FROM applications WHERE id IN (${placeholders})`
            ).bind(...ids).all();
            apps = appRows || [];
          }
          rows.push({
            line_user_id: r.line_user_id,
            state: r.state,
            phone: r.phone,
            candidate_name: apps[0] ? apps[0].name : null,
            bound_at: r.bound_at,
            created_at: r.created_at,
            updated_at: r.updated_at,
            applications: apps.map((a) => ({ id: a.id, job_slug: a.job_slug, job_title: a.job_title })),
          });
        }
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
      if (p === '/admin/clients' && request.method === 'GET') {
        const { results } = await env.DB.prepare(
          `SELECT id, name, aliases, relation, blocked_reason, via_client, owner, note,
                  created_at, updated_at
             FROM clients ORDER BY
               CASE relation WHEN 'end_client' THEN 0 WHEN 'negotiating' THEN 1
                             WHEN 'signed' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
               name`
        ).all();
        return json(request, { ok: true, rows: results || [] });
      }

      if (p === '/admin/clients' && request.method === 'POST') {
        let b;
        try { b = await request.json(); } catch { return json(request, { ok: false, error: '格式錯誤' }, 400); }
        const now = nowTaipei();
        if (b.delete && b.id) {
          await env.DB.prepare(`DELETE FROM clients WHERE id = ?`).bind(b.id).run();
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
        const id = String(b.id || '').trim() || uid();
        await env.DB.prepare(
          `INSERT INTO clients (id,name,aliases,relation,blocked_reason,via_client,owner,note,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, aliases=excluded.aliases, relation=excluded.relation,
             blocked_reason=excluded.blocked_reason, via_client=excluded.via_client,
             owner=excluded.owner, note=excluded.note, updated_at=excluded.updated_at`
        ).bind(id, name, b.aliases || null, b.relation, b.blocked_reason || null,
               b.via_client || null, b.owner || null, b.note || null, now, now).run();
        return json(request, { ok: true, id });
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
               `看到您的面談中途結束了，我們已經把先前談到的內容記錄下來。`,
               `如果只是臨時有事，隨時可以用原本的連結回來繼續，我們會接著談。`,
               `若想改由真人顧問直接與您聯繫，也可以透過下方 LINE 告訴我們。`]
            : [`${app.name} 您好，`,
               `謝謝您撥空完成初步面談，內容我們都收到了。`,
               `接下來會由我們的顧問看過這份紀錄，如果合適會盡快與您聯繫安排下一步。`,
               `不管結果如何，我們都會通知您，不會讓您空等。`],
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
             `我是 Step1ne 德仁管理顧問的招募團隊。我們看到您的背景，覺得跟「${job ? job.title : b.job_slug}」這個機會蠻符合的。`,
             `想先請您花 20 到 30 分鐘，跟我們的 AI 面談助理阿財做一次初步了解——他會先看過您的資料，聊聊經歷與您在意的條件。`,
             `談完之後由真人顧問接手，不管有沒有下一步都會通知您。`,
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
          `SELECT j.slug, j.title, j.service_line, j.client_relation, j.seniority,
                  j.client_named, j.ai_disclosure, j.client_code, j.status,
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
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug
                      AND UPPER(p.stage) IN ('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW')
                      AND p.onboard_date IS NULL) AS client_stage,
                  -- ④ 錄取：客戶決定要了、還沒到職（談offer、辦離職、等報到都在這）
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug
                      AND UPPER(p.stage) IN ('OFFER','OFFER_ACCEPTED','HIRED')
                      AND p.onboard_date IS NULL) AS offered,
                  -- ⑤ 到職：真的上工了。這一關才算成案。
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug AND p.onboard_date IS NOT NULL) AS onboard,
                  -- ⑥ 結案：送出去之後客戶不要或人選拒絕，案子停在這裡不會再動。
                  --    2026-08-12 加：在這之前 CLOSED_LOST 的人不落在任何一關，
                  --    畫面上就是憑空消失——顧問完全看不出「送了多少、掉了多少」。
                  (SELECT COUNT(DISTINCT p.application_id) FROM placements p
                    WHERE p.job_slug = j.slug AND UPPER(p.stage) = 'CLOSED_LOST') AS closed
             FROM jobs j
            ORDER BY (j.status = 'closed'), j.service_line, j.slug`
        ).all();
        return json(request, { ok: true, jobs: results || [] });
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
          const st = stage === 'onboard'
            ? `p2.onboard_date IS NOT NULL`
            : (stage === 'offered'
                ? `UPPER(p2.stage) IN ('OFFER','OFFER_ACCEPTED','HIRED') AND p2.onboard_date IS NULL`
                : (stage === 'closed'
                    ? `UPPER(p2.stage) = 'CLOSED_LOST'`
                    : `UPPER(p2.stage) IN ('SUBMITTED','CLIENT_INTERVIEW','INTERVIEWING','INTERVIEW') AND p2.onboard_date IS NULL`));
          where = ` AND EXISTS (SELECT 1 FROM placements p2
                                 WHERE p2.application_id = a.id AND ${st})`;
        }
        const { results } = await env.DB.prepare(base + where + ' ORDER BY a.created_at ASC')
          .bind(slug).all();
        return json(request, { ok: true, stage, slug, candidates: results || [] });
      }

      // 更新分類。⚠️ 客戶對象一改，三個連動欄位要一起改，
      // 不能讓顧問一個一個設——漏設一個就是隱私外洩。
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
        if (!sets.length) return json(request, { ok: false, error: '沒有要更新的欄位' }, 400);

        await env.DB.prepare(`UPDATE jobs SET ${sets.join(', ')} WHERE slug = ?`)
          .bind(...bind, slug).run();
        const row = await env.DB.prepare(
          `SELECT slug, service_line, client_relation, seniority, client_named, ai_disclosure, client_code
             FROM jobs WHERE slug = ?`).bind(slug).first();
        if (!row) return json(request, { ok: false, error: '找不到這個職缺' }, 404);
        return json(request, { ok: true, job: row });
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

        // 面談連結要給顧問看得到。
        // 原本刻意移除 chat_token，但那造成一個沒有出路的情況：
        // 顧問建完檔沒複製就關掉視窗，那個連結就永遠找不回來了。
        // 這個端點本來就要 ADMIN_TOKEN，顧問看得到自己的候選人連結是合理的。
        const interviewUrl = app.chat_token
          ? `https://step1ne.com/interview/?t=${app.chat_token}`
          : null;
        delete app.chat_token;
        return json(request, { ok: true, application: app, interview_url: interviewUrl,
                               transcript: results || [], report: rep || null,
                               flags: flags.results || [], feedback: fb || null });
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
        if (!id && state === 'undecided') where.push('r.consultant_decision IS NULL');
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
                  a.interview_started_at, a.interview_ended_at, a.resume_file_id, a.resume_url
             FROM reports r JOIN applications a ON a.id = r.application_id
            WHERE r.id = ?`
        ).bind(rid).first();
        if (!r) return json(request, { ok: false, error: '找不到這份報告' }, 404);

        const { results } = await env.DB.prepare(
          `SELECT role, content, created_at FROM messages
            WHERE application_id = ? ORDER BY id ASC LIMIT 400`
        ).bind(r.application_id).all();

        return json(request, { ok: true, report: r, transcript: results || [] });
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
              `SELECT a.name, a.job_slug, a.job_title, j.client_name
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
          }
        }
        if (b.app_id) await notifyLineProgress(env, b.app_id);
        return json(request, { ok: true, decided_at: now, placement_id });
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
         `這是您先前選擇的時間，我們的 AI 面談助理阿財已經準備好了。`,
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
         `感謝您應徵「${r.job_title || ''}」，顧問已經看過您的資料。`,
         `這次的職缺條件與您目前的狀況還有一些落差，這次暫時不會安排進一步面談。`,
         `您的資料我們會保留，未來若有更適合的機會，會再主動與您聯繫。`,
         `再次謝謝您撥空應徵，也祝您求職順利。`]
      );
      await notify(env, `📧 已寄出婉拒通知：${r.name}　${r.job_title || ''}` +
        (sent ? '' : '\n⚠️ 信件寄送失敗，請手動聯繫'),
        { message_thread_id: THREAD.system });
      await env.DB.prepare(`UPDATE applications SET decline_email_sent_at=? WHERE id=?`)
        .bind(nowTaipei(), r.id).run();
    }

    await pipelineReminders(env);
  },
};

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
      WHERE stage NOT IN ('CLOSED_WON','CLOSED_LOST')`
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
