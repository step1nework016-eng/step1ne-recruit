// 「先送出，晚點再聊」排程（src/apply_later.js）單元測試：隔天 10:00 只提醒一次、7 天到期標「沒完成面談」。
// node 內建 SQLite 假裝 D1；LINE 推送與 TG 通知都是假的函式，不會送出任何東西。
//
//   node --experimental-sqlite tests/apply_later_tick_test.mjs
//   node --experimental-sqlite tests/apply_later_tick_test.mjs --dump-flex /tmp/x.json   # 給 validate_flex.sh 驗卡片格式
import { DatabaseSync } from 'node:sqlite';
import { writeFileSync } from 'node:fs';
import { applyLaterTick, reminderDue, daysLeft, reminderFlex, reminderMail, lineTargets, ENTRY_MARKER } from '../src/apply_later.js';

const dumpAt = process.argv.indexOf('--dump-flex');
if (dumpAt > 0) {
  writeFileSync(process.argv[dumpAt + 1], JSON.stringify({ messages: [reminderFlex({ jobTitle: 'BIM 工程師（儲備培訓）台中后里', token: 'x'.repeat(48), days: 6 })] }));
  console.log('wrote', process.argv[dumpAt + 1]);
  process.exit(0);
}

let fail = 0;
const check = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fail++; };

function makeDB() {
  const db = new DatabaseSync(':memory:');
  db.exec(`
    CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT, email TEXT, phone TEXT, job_slug TEXT, job_title TEXT,
      interview_state TEXT, status TEXT, chat_token TEXT);
    CREATE TABLE jobs (slug TEXT PRIMARY KEY, title TEXT, status TEXT);
    CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT);
    CREATE TABLE line_bindings (line_user_id TEXT PRIMARY KEY, state TEXT, phone TEXT, application_ids TEXT);
    CREATE TABLE mail_outbox (id TEXT PRIMARY KEY, kind TEXT, from_addr TEXT, to_email TEXT, subject TEXT, text TEXT, html TEXT,
      ref_table TEXT, ref_id TEXT, status TEXT, created_at TEXT);
  `);
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => { const r = db.prepare(sql).run(...args); return { meta: { changes: Number(r.changes) } }; },
  });
  return { raw: db, prepare: (sql) => stmt(sql) };
}

const CHOSEN = '2026-10-21 15:20:00';
const EXPIRES = '2026-10-28 15:20:00';
function setup({ status = 'later', state = 'not_started', bound = false, phoneBound = false, jobStatus = 'open', email = 'test@example.com' } = {}) {
  const DB = makeDB();
  DB.raw.prepare(`INSERT INTO jobs VALUES ('test-job','測試職缺',?)`).run(jobStatus);
  DB.raw.prepare(`INSERT INTO applications VALUES ('APP-T','王測試',?,'0912000111','test-job','測試職缺',?,?,?)`).run(email, state, status, 'k'.repeat(48));
  if (bound) DB.raw.prepare(`INSERT INTO line_bindings VALUES ('U1','bound',NULL,'["APP-T"]')`).run();
  if (phoneBound) DB.raw.prepare(`INSERT INTO line_bindings VALUES ('U2','bound','0912000111','["OTHER"]')`).run();
  return DB;
}
async function tick(DB, now, pushOk = true) {
  const pushes = [], notes = [];
  await applyLaterTick({ DB }, {
    nowTaipei: () => now,
    linePushMessages: async (_env, to, msgs) => { pushes.push({ to, msgs }); return pushOk; },
    notify: async (_env, text) => { notes.push(text); },
    uid: () => Math.random().toString(36).slice(2),
    THREAD: { intake: 4 },
  });
  return { pushes, notes };
}
const choose = (DB, chosen = CHOSEN, expires = EXPIRES) => {
  DB.raw.exec(`CREATE TABLE IF NOT EXISTS apply_later (application_id TEXT PRIMARY KEY, chosen_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    reminder_at TEXT, reminder_channel TEXT, reminder_ok INTEGER, expired_marked_at TEXT)`);
  DB.raw.prepare(`INSERT INTO apply_later (application_id, chosen_at, expires_at) VALUES ('APP-T',?,?)`).run(chosen, expires);
};
const mails = (DB) => DB.raw.prepare(`SELECT * FROM mail_outbox`).all();
const row = (DB) => DB.raw.prepare(`SELECT * FROM apply_later`).get();

// ① 沒綁 LINE：隔天 10:00 寄一封信；之後每 15 分鐘再跑也不會再寄
{
  const DB = setup(); choose(DB);
  let r = await tick(DB, '2026-10-21 23:45:00');
  check(mails(DB).length === 0, '選的當天晚上：不提醒');
  r = await tick(DB, '2026-10-22 09:45:00');
  check(mails(DB).length === 0, '隔天 09:45：還沒到 10 點，不提醒');
  r = await tick(DB, '2026-10-22 10:00:00');
  const m = mails(DB);
  check(m.length === 1 && m[0].kind === 'apply_later_reminder' && m[0].to_email === 'test@example.com' && r.pushes.length === 0, '隔天 10:00：寄一封提醒信');
  check(m[0].html.includes('您的面談還在等您') && m[0].html.includes('連結 7 天內有效') && m[0].html.includes('/book/?t='), '信裡有標題、剩幾天、約時間連結');
  check(row(DB).reminder_channel === 'email' && row(DB).reminder_ok === 1, '記下用信件提醒');
  for (const t of ['2026-10-22 10:15:00', '2026-10-22 14:00:00', '2026-10-23 10:00:00', '2026-10-24 10:00:00']) await tick(DB, t);
  check(mails(DB).length === 1, '之後再跑幾次：永遠只有一則');
}

// ② 有綁 LINE（綁定紀錄有這筆應徵）：推一張 Flex，不寄信
{
  const DB = setup({ bound: true }); choose(DB);
  const r = await tick(DB, '2026-10-22 10:00:00');
  check(r.pushes.length === 1 && r.pushes[0].to === 'U1' && r.pushes[0].msgs[0].type === 'flex' && mails(DB).length === 0, '綁 LINE：推一張卡片、不寄信');
  check(JSON.stringify(r.pushes[0].msgs[0]).includes('連結 7 天內有效'), '卡片寫剩幾天');
  const r2 = await tick(DB, '2026-10-22 10:15:00');
  check(r2.pushes.length === 0, '再跑一次：不再推');
}

// ③ 同一支手機綁過 LINE（別的應徵綁的）也算有綁
{
  const DB = setup({ phoneBound: true }); choose(DB);
  const r = await tick(DB, '2026-10-22 11:00:00');
  check(r.pushes.length === 1 && r.pushes[0].to === 'U2', '同手機綁定 → 推 LINE');
}

// ④ LINE 推送失敗 → 改寄信（還是只有一則）
{
  const DB = setup({ bound: true }); choose(DB);
  const r = await tick(DB, '2026-10-22 10:00:00', false);
  check(r.pushes.length === 1 && mails(DB).length === 1 && row(DB).reminder_channel === 'email', 'LINE 失敗 → 改寄信');
}

// ⑤ 已經開始聊（人選講過話）／狀態不是 later／職缺關了 → 不提醒
{
  const DB = setup(); choose(DB);
  DB.raw.prepare(`INSERT INTO messages (application_id, role, content) VALUES ('APP-T','candidate','你好')`).run();
  await tick(DB, '2026-10-22 10:00:00');
  check(mails(DB).length === 0, '人選講過話 → 不提醒');
}
{
  const DB = setup(); choose(DB);
  DB.raw.prepare(`INSERT INTO messages (application_id, role, content) VALUES ('APP-T','candidate',?)`).run(ENTRY_MARKER);
  await tick(DB, '2026-10-22 10:00:00');
  check(mails(DB).length === 1, '只有進房暗號（沒講話）→ 照樣提醒');
}
{
  const DB = setup({ status: 'ready' }); choose(DB);
  await tick(DB, '2026-10-22 10:00:00');
  check(mails(DB).length === 0, '回來過、狀態已轉 ready → 不提醒');
}
{
  const DB = setup({ status: 'scheduled' }); choose(DB);
  await tick(DB, '2026-10-22 10:00:00');
  check(mails(DB).length === 0, '後來約了時間（scheduled）→ 不提醒（約時間有自己的提醒）');
}
{
  const DB = setup({ jobStatus: 'closed' }); choose(DB);
  await tick(DB, '2026-10-22 10:00:00');
  check(mails(DB).length === 0 && row(DB).reminder_channel === 'skipped_job_closed', '職缺關了 → 不提醒，記下原因');
}

// ⑥ 錯過隔天（排程整天沒跑）→ 第三天以後不補
{
  const DB = setup(); choose(DB);
  await tick(DB, '2026-10-23 10:00:00');
  check(mails(DB).length === 0, '第三天才跑到 → 不補發');
}

// ⑦ 隔天晚上 21:00 之後不送
{
  const DB = setup(); choose(DB);
  await tick(DB, '2026-10-22 21:00:00');
  check(mails(DB).length === 0, '隔天 21:00 → 太晚不送');
}

// ⑧ 7 天到期：標「沒完成面談」一次、TG 告知一次
{
  const DB = setup(); choose(DB);
  let r = await tick(DB, '2026-10-28 15:19:59');
  check(DB.raw.prepare(`SELECT status FROM applications`).get().status === 'later', '到期前一秒：還是 later');
  r = await tick(DB, '2026-10-28 15:20:00');
  check(DB.raw.prepare(`SELECT status FROM applications`).get().status === 'no_interview' && row(DB).expired_marked_at, '到期：status → no_interview');
  check(r.notes.length === 1 && r.notes[0].includes('沒完成面談'), 'TG 告知一次');
  r = await tick(DB, '2026-10-28 15:35:00');
  check(r.notes.length === 0, '再跑：不重複告知');
}
{
  const DB = setup({ state: 'paused', status: 'ready' }); choose(DB);
  await tick(DB, '2026-10-29 10:00:00');
  check(DB.raw.prepare(`SELECT status FROM applications`).get().status === 'ready', '開始過的面談：不會被標沒完成面談');
}

// ⑨ 純函式
{
  check(daysLeft(EXPIRES, '2026-10-22 10:00:00') === 7 && daysLeft(EXPIRES, '2026-10-28 10:00:00') === 1, 'daysLeft 無條件進位');
  check(reminderDue({ status: 'later', chosen_at: '2026-10-31 23:59:00', expires_at: '2026-11-07 23:59:00' }, '2026-11-01 10:00:00'), '跨月：10/31 選 → 11/1 提醒');
  check(lineTargets([{ line_user_id: 'U9', phone: '', application_ids: '[]' }], { id: 'A', phone: '' }).length === 0, '沒手機不會亂配對');
  const m = reminderMail({ name: '王測試', jobTitle: '測試職缺', token: 'x'.repeat(48), days: 6 });
  const all = m.subject + m.text + m.html + JSON.stringify(reminderFlex({ jobTitle: '測試職缺', token: 'x', days: 6 }));
  check(!/客戶/.test(all), '提醒文字不出現「客戶」');
  check(!/(顧問|我們)(會|將)/.test(all), '提醒文字不用顧問／我們當主詞');
  check(!/週[一二三四五六日]|（[一二三四五六日]）/.test(all), '沒有寫星期（不用跑星期檢查）');
}

console.log(fail ? `\n${fail} 項失敗` : '\n全部通過');
process.exit(fail ? 1 : 0);
