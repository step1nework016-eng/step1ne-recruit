// LINE webhook 去重（src/line_dedupe.js）單元測試。node 內建 SQLite 假裝 D1，不會呼叫 LINE。
//   node --experimental-sqlite tests/line_dedupe_test.mjs
import { DatabaseSync } from 'node:sqlite';
import { ensureLineEventsTable, claimLineEvent, markLineReplySent, finishLineEvent, purgeOldLineEvents, isInvalidReplyToken } from '../src/line_dedupe.js';

let fail = 0;
const check = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fail++; };

function makeDB() {
  const db = new DatabaseSync(':memory:');
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => { const r = db.prepare(sql).run(...args); return { meta: { changes: Number(r.changes) } }; },
  });
  return { raw: db, prepare: (sql) => stmt(sql) };
}
// 時間要用「現在」：卡住判斷是跟 SQLite 的 datetime('now') 比
const NOW = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 19).replace('T', ' ');
const ev = (id, redelivery = false) => ({ type: 'message', webhookEventId: id, deliveryContext: { isRedelivery: redelivery },
  source: { userId: 'Utest' }, replyToken: 'rt', message: { type: 'text', text: '想了解無經驗培訓' } });

const db = makeDB();
await ensureLineEventsTable(db);
await ensureLineEventsTable(db);   // 重複建表不能報錯

// 1) 22:00 事故重現：第一次處理、之後 7 次重送全部略過
let c = await claimLineEvent(db, ev('E1'), NOW);
check(c.action === 'process', '第一次收到 → 處理');
await markLineReplySent(db, 'E1', NOW);
await finishLineEvent(db, 'E1', true, NOW);
let skipped = 0;
for (let i = 0; i < 7; i++) if ((await claimLineEvent(db, ev('E1', true), NOW)).action === 'skip') skipped++;
check(skipped === 7, '同一個 webhookEventId 重送 7 次 → 7 次都略過');
check(db.raw.prepare(`SELECT redeliveries FROM line_webhook_events WHERE event_id='E1'`).get().redeliveries === 7, '記下被擋掉的重送次數');

// 2) 處理中（還沒結束）又收到重送 → 略過，不會兩邊同時跑
await claimLineEvent(db, ev('E2'), NOW);
check((await claimLineEvent(db, ev('E2', true), NOW)).action === 'skip', '處理中的事件被重送 → 略過');

// 3) 上次失敗且沒送出任何回覆 → 再處理一次（只准一次）
await claimLineEvent(db, ev('E3'), NOW);
await finishLineEvent(db, 'E3', false, NOW);
check((await claimLineEvent(db, ev('E3', true), NOW)).action === 'reprocess', '失敗＋沒送出 → 重送時再處理一次');
await finishLineEvent(db, 'E3', false, NOW);
check((await claimLineEvent(db, ev('E3', true), NOW)).action === 'skip', '第二次又失敗 → 不再無限重試');

// 4) 上次失敗但回覆其實已送出 → 不再處理（避免重複發訊息）
await claimLineEvent(db, ev('E4'), NOW);
await markLineReplySent(db, 'E4', NOW);
await finishLineEvent(db, 'E4', false, NOW);
check((await claimLineEvent(db, ev('E4', true), NOW)).action === 'skip', '失敗但回覆已送出 → 略過，不重送');

// 5) 卡在處理中超過 5 分鐘（背景工作被砍）且沒送出 → 可以再處理
await claimLineEvent(db, ev('E5'), NOW);
db.raw.prepare(`UPDATE line_webhook_events SET updated_at=datetime('now','+8 hours','-10 minutes') WHERE event_id='E5'`).run();
check((await claimLineEvent(db, ev('E5', true), NOW)).action === 'reprocess', '卡住 10 分鐘且沒送出 → 再處理一次');

// 6) 第一次就是以「重送」身分抵達（之前那次根本沒到）→ 照常處理
check((await claimLineEvent(db, ev('E6', true), NOW)).action === 'process', '沒看過的重送事件 → 照常處理');

// 7) 沒有 webhookEventId → 照常處理
check((await claimLineEvent(db, { type: 'message', source: { userId: 'U' } }, NOW)).action === 'process', '沒有事件編號 → 照常處理');

// 8) 清舊紀錄
db.raw.prepare(`UPDATE line_webhook_events SET first_seen_at=datetime('now','+8 hours','-3 days') WHERE event_id='E1'`).run();
await purgeOldLineEvents(db);
check(!db.raw.prepare(`SELECT 1 FROM line_webhook_events WHERE event_id='E1'`).get(), '超過 2 天的紀錄會被清掉');
check(!!db.raw.prepare(`SELECT 1 FROM line_webhook_events WHERE event_id='E2'`).get(), '新的紀錄保留');

// 9) 判斷 reply token 失效
check(isInvalidReplyToken(400, '{"message":"Invalid reply token"}'), 'LINE 400 Invalid reply token → 判定失效');
check(!isInvalidReplyToken(400, '{"message":"The request body has 1 error(s)"}'), '格式錯不算 token 失效（不推播補送）');
check(!isInvalidReplyToken(500, 'Invalid reply token'), '非 400 不算');

console.log(fail ? `\n${fail} FAILED` : '\nALL PASS');
process.exit(fail ? 1 : 0);
