// 單元測試：LINE 履歷收件（step1ne-recruit）＋代碼接上應徵（step1ne-public-worker）
// 從兩支 Worker 原始碼直接抽出那段函式來跑，D1 用 node:sqlite 模擬，LINE API 用假 fetch。
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import assert from 'node:assert/strict';

const ROOT = '/Users/user/claude-projects/工作流程技能包';
const rc = readFileSync(ROOT + '/step1ne-recruit/src/index.js', 'utf8');
const pw = readFileSync(ROOT + '/step1ne-public-worker/src/index.js', 'utf8');
const cut = (s, a, b) => { const i = s.indexOf(a); const j = s.indexOf(b, i); assert(i >= 0 && j > i, a); return s.slice(i, j); };
const rcCode = cut(rc, '// ── 2026-10-12 應徵表單「我用 LINE 傳」履歷收件 ──', 'async function handleLineEvent(');
const pwCode = cut(pw, '// ── 2026-10-12 應徵表單「履歷三條路」', '// 通用上傳存檔');

const db = new DatabaseSync(':memory:');
db.exec(`
CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, job_slug TEXT, job_title TEXT, created_at TEXT,
  resume_file_id TEXT, resume_url TEXT, resume_104_url TEXT, resume_extra_files TEXT,
  interview_state TEXT DEFAULT 'not_started', superseded_by TEXT,
  prewarmed_opening TEXT, prewarmed_at TEXT, interview_plan_json TEXT, interview_plan_at TEXT);
CREATE TABLE files (id TEXT PRIMARY KEY, created_at TEXT, filename TEXT, mime TEXT, size INTEGER, chunks INTEGER, storage TEXT, text_content TEXT);
CREATE TABLE line_bindings (line_user_id TEXT PRIMARY KEY, state TEXT, application_ids TEXT, bound_at TEXT, source TEXT, created_at TEXT, updated_at TEXT);
` + readFileSync(ROOT + '/step1ne-recruit/schema_resume_intake.sql', 'utf8'));
const D1 = {
  prepare(sql) {
    let args = [];
    const st = { bind(...a) { args = a; return st; },
      first() { return db.prepare(sql).get(...args) || null; },
      all() { return { results: db.prepare(sql).all(...args) }; },
      run() { const r = db.prepare(sql).run(...args); return { meta: { changes: r.changes } }; } };
    return st;
  },
};
const r2 = new Map();
const replies = [], notes = [];
let fetchPlan = null;
const env = { DB: D1, FILES: { put: async (k, v) => { r2.set(k, v); } }, LINE_CHANNEL_ACCESS_TOKEN: 'x' };
let NOW = '2026-10-12 15:00:00';
const scope = {
  safeJsonArray: (s) => { try { const v = JSON.parse(s || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } },
  nowTaipei: () => NOW, uid: () => crypto.randomUUID(),
  lineReply: async (e, tok, text) => { replies.push(text); },
  notify: async (e, text) => { notes.push(text); },
  THREAD: { system: 1 }, INTAKE_THREAD: 4,
  fetch: async (url) => fetchPlan(url),
  AbortSignal,
};
const mk = (code) => new Function(...Object.keys(scope),
  code + '\nreturn { handleLineResumeCodeText, handleLineResumeFile, newResumeCode, linkResumeCode, appHasResume, buildQuickResume, cleanPastedResume, lineResumeUrl, ensureResumeCodeForApp, getResumeCode };'
    .replace(/handleLineResumeCodeText, handleLineResumeFile, /, (m) => m));
// 兩段分開包，各自只回傳自己有的函式
const R = new Function(...Object.keys(scope), rcCode + '\nreturn { handleLineResumeCodeText, handleLineResumeFile, lineResumeAppHasResume };')(...Object.values(scope));
const P = new Function(...Object.keys(scope), pwCode + '\nreturn { newResumeCode, linkResumeCode, appHasResume, buildQuickResume, cleanPastedResume, lineResumeUrl, ensureResumeCodeForApp, getResumeCode, saveTextResumeFromBody };')(...Object.values(scope));

const okImage = async () => ({ ok: true, headers: new Headers({ 'content-type': 'image/jpeg' }), arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer });
const ev = (uid, message) => ({ type: 'message', replyToken: 'rt', source: { userId: uid }, message });
const last = () => replies[replies.length - 1];
let n = 0; const ok = (name) => { n++; console.log('  ✓', name); };

// 1. 表單發代碼（應徵還沒建立）
const code = await P.newResumeCode(env, null);
assert.match(code, /^[A-HJ-NP-Z2-9]{5}$/);
assert.ok(P.lineResumeUrl(code).startsWith('https://line.me/R/oaMessage/@930yldgp/?'));
assert.ok(decodeURIComponent(P.lineResumeUrl(code).split('?')[1]) === '我要傳履歷 #' + code);
ok('表單發代碼＋LINE 預填網址格式正確');

// 2. 候選人在 LINE 送出預填那句
assert.equal(await R.handleLineResumeCodeText(env, 'U1', 'rt', '我要傳履歷 #' + code.toLowerCase(), NOW), true);
assert.match(last(), /請直接把履歷檔案或照片傳過來/);
assert.equal(db.prepare('SELECT line_user_id FROM resume_intake_codes WHERE code=?').get(code).line_user_id, 'U1');
ok('送出「我要傳履歷 #代碼」→ 綁上 LINE 帳號並回覆（大小寫都認）');

// 3. 傳第一張照片（應徵紀錄還沒建立）
fetchPlan = async (url) => { assert.ok(url.startsWith('https://api-data.line.me/v2/bot/message/')); return okImage(); };
await R.handleLineResumeFile(env, ev('U1', { type: 'image', id: 'm1001' }));
assert.match(last(), /履歷收到了 ✅/);
const c1 = db.prepare('SELECT * FROM resume_intake_codes WHERE code=?').get(code);
assert.ok(c1.file_id && r2.has(c1.file_id));
assert.equal(db.prepare('SELECT mime FROM files WHERE id=?').get(c1.file_id).mime, 'image/jpeg');
ok('第一張照片 → 存 R2＋files，代碼記下檔案，回覆「履歷收到了」');

// 4. 第二張
await R.handleLineResumeFile(env, ev('U1', { type: 'image', id: 'm1002' }));
assert.match(last(), /這一份也收到了/);
assert.equal(JSON.parse(db.prepare('SELECT extra_files FROM resume_intake_codes WHERE code=?').get(code).extra_files).length, 1);
ok('第二張 → 記成補充頁');

// 5. 表單送出 /apply 時接上代碼
db.prepare(`INSERT INTO applications (id,name,job_slug,created_at) VALUES ('app1','測試甲','job-a',?)`).run(NOW);
const linked = await P.linkResumeCode(env, code, 'app1', NOW);
assert.ok(linked);
const a1 = db.prepare('SELECT * FROM applications WHERE id=?').get('app1');
assert.equal(a1.resume_file_id, c1.file_id);
assert.equal(JSON.parse(a1.resume_extra_files).length, 1);
assert.deepEqual(JSON.parse(db.prepare('SELECT application_ids FROM line_bindings WHERE line_user_id=?').get('U1').application_ids), ['app1']);
assert.equal(P.appHasResume(a1), true);
ok('接上應徵 → 履歷檔、補充頁、LINE 綁定一起接過來');

// 6. 已接上之後再傳第三張 → 直接進應徵的補充檔
await R.handleLineResumeFile(env, ev('U1', { type: 'image', id: 'm1003' }));
assert.equal(JSON.parse(db.prepare('SELECT resume_extra_files FROM applications WHERE id=?').get('app1').resume_extra_files).length, 2);
ok('接上之後再傳 → 補充檔 +1');

// 7. 陌生人傳檔案 → 不存，回應徵連結
const before = db.prepare('SELECT count(*) n FROM files').get().n;
await R.handleLineResumeFile(env, ev('U2', { type: 'file', id: 'm2', fileName: 'cv.pdf', fileSize: 1000 }));
assert.match(last(), /step1ne\.com\/apply\//);
assert.equal(db.prepare('SELECT count(*) n FROM files').get().n, before);
ok('沒綁定的人傳檔 → 不存檔，回應徵連結');

// 8. 已綁定、有一筆缺履歷的應徵 → 收下
db.prepare(`INSERT INTO applications (id,name,job_slug,created_at,prewarmed_opening,prewarmed_at,interview_plan_json,interview_plan_at) VALUES ('app3','測試丙','job-c',?,'["沒有履歷的開場"]','2026-10-12 14:00:00','{}','2026-10-12 14:00:00')`).run(NOW);
db.prepare(`INSERT INTO line_bindings VALUES ('U3','bound','["app3"]',?, 'liff_chat', ?, ?)`).run(NOW, NOW, NOW);
fetchPlan = async () => ({ ok: true, headers: new Headers({ 'content-type': 'application/pdf' }), arrayBuffer: async () => new Uint8Array([37, 80, 68, 70]).buffer });
await R.handleLineResumeFile(env, ev('U3', { type: 'file', id: 'm3', fileName: '我的履歷.pdf', fileSize: 4 }));
const a3 = db.prepare('SELECT resume_file_id FROM applications WHERE id=?').get('app3');
assert.ok(a3.resume_file_id);
assert.equal(db.prepare('SELECT filename FROM files WHERE id=?').get(a3.resume_file_id).filename, '我的履歷.pdf');
assert.match(last(), /履歷收到了/);
const p3 = db.prepare('SELECT prewarmed_at, interview_plan_at FROM applications WHERE id=?').get('app3');
assert.equal(p3.prewarmed_at, '2000-01-01 00:00:00'); assert.equal(p3.interview_plan_at, '2000-01-01 00:00:00');
ok('已綁定＋有缺履歷的應徵 → PDF 收下當履歷，事先擬好的題目／開場白標成過期');

// 9. 已綁定、但應徵都有履歷了 → 不回話
const cnt = replies.length;
await R.handleLineResumeFile(env, ev('U3', { type: 'image', id: 'm4' }));
assert.equal(replies.length, cnt);
ok('已綁定但沒有缺履歷的應徵 → 不搶著回（多半在跟顧問聊別的）');

// 10. HEIC 檔案、不支援格式、太大
const code2 = await P.newResumeCode(env, null);
await R.handleLineResumeCodeText(env, 'U5', 'rt', '我要傳履歷 #' + code2, NOW);
await R.handleLineResumeFile(env, ev('U5', { type: 'file', id: 'm5', fileName: 'IMG_1.HEIC', fileSize: 10 }));
assert.match(last(), /HEIC/);
await R.handleLineResumeFile(env, ev('U5', { type: 'file', id: 'm6', fileName: 'a.zip', fileSize: 10 }));
assert.match(last(), /格式系統讀不到/);
await R.handleLineResumeFile(env, ev('U5', { type: 'file', id: 'm7', fileName: 'a.pdf', fileSize: 30 * 1024 * 1024 }));
assert.match(last(), /20MB/);
assert.equal(db.prepare('SELECT file_id FROM resume_intake_codes WHERE code=?').get(code2).file_id, null);
ok('HEIC／不支援格式／超過 20MB → 各自回清楚的說明，不存');

// 11. 代碼被別人用過、過期、查不到
assert.equal(await R.handleLineResumeCodeText(env, 'U9', 'rt', '我要傳履歷 #' + code2, NOW), true);
assert.match(last(), /另一個 LINE 帳號/);
db.prepare(`UPDATE resume_intake_codes SET expires_at='2026-01-01 00:00:00' WHERE code=?`).run(code2);
assert.equal(await R.handleLineResumeCodeText(env, 'U5', 'rt', '#' + code2, NOW), true);
assert.match(last(), /過期/);
assert.equal(await R.handleLineResumeCodeText(env, 'U5', 'rt', '我要傳履歷 #ZZZZZ', NOW), false);
assert.equal(await R.handleLineResumeCodeText(env, 'U5', 'rt', '我想查進度 [LC351]', NOW), false);
ok('代碼被別人用過／過期 → 說明；查不到的代碼、[LC] 歸因 → 不攔截');

// 12. 下載失敗
const code3 = await P.newResumeCode(env, null);
await R.handleLineResumeCodeText(env, 'U6', 'rt', '我要傳履歷 #' + code3, NOW);
fetchPlan = async () => ({ ok: false, status: 500, headers: new Headers() });
await R.handleLineResumeFile(env, ev('U6', { type: 'image', id: 'm8' }));
assert.match(last(), /麻煩再傳一次/);
ok('LINE 下載失敗 → 請他重傳＋系統通知');

// 13. 快速履歷／貼上文字
assert.equal(P.buildQuickResume({ company: 'A', title: 'B', years: '1–3 年', duty: '', edu: '大學' }), null);
const qt = P.buildQuickResume({ company: '某某公司', title: '行政助理', years: '1–3 年', duty: '處理報表與採購', edu: '大學' });
assert.match(qt, /快速履歷/); assert.match(qt, /行政助理/);
assert.equal(P.cleanPastedResume('太短'), null);
const fid = await P.saveTextResumeFromBody(env, { resume_method: 'quick', quick_resume: { company: '某某公司', title: '行政助理', years: '1–3 年', duty: '處理報表與採購', edu: '大學' } }, NOW);
const fr = db.prepare('SELECT filename, mime, text_content FROM files WHERE id=?').get(fid);
assert.equal(fr.mime, 'text/plain'); assert.match(fr.text_content, /處理報表與採購/); assert.ok(r2.has(fid));
ok('快速履歷 5 格缺一不收；存成文字檔＋text_content（阿財讀得到）');

// 14. 面談入口判準
assert.equal(P.appHasResume({}), false);
assert.equal(P.appHasResume({ resume_url: 'https://drive.google.com/x' }), true);
assert.equal(P.appHasResume({ resume_extra_files: '[]' }), false);
const ec1 = await P.ensureResumeCodeForApp(env, 'app9'); const ec2 = await P.ensureResumeCodeForApp(env, 'app9');
assert.equal(ec1, ec2);
ok('面談入口「有沒有履歷」判準；同一筆應徵重整不換代碼');

console.log(`\n全部 ${n} 組通過`);
