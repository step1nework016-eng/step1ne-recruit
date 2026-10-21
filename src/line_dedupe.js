// LINE webhook 去重（2026-10-21）
//
// 事故：U7c99… 22:00 傳一次「想了解無經驗培訓」，line_inbound_log 卻記了 8 次（每 62 秒一次）。
// 原因：/line-webhook 以前把 D1、LINE 回覆、TG 通知全部做完才回 200，太慢 → LINE 判定逾時、
// 用同一個 webhookEventId（deliveryContext.isRedelivery=true）一直重送。第一次走「關鍵字卡片」、
// 第二次因為節流換走「想了解」那條，又回了一次＋又發一次 TG，reply token 已經用過 → 400。
//
// 做法：①收到就回 200，實際處理丟 ctx.waitUntil；②每個 webhookEventId 先「搶號」，
// 搶到的才處理，重送的直接略過；③只有「上次處理到一半失敗、而且確定還沒送出任何回覆」
// 才准再處理一次（那時 reply token 已失效，index.js 的 lineReply 會改用推播補送）。
// 紀錄留 2 天（LINE 重送最多一天內），每 15 分鐘的排程順手清掉舊的。

export const LINE_EVENTS_DDL = `CREATE TABLE IF NOT EXISTS line_webhook_events (
  event_id      TEXT PRIMARY KEY,
  line_user_id  TEXT,
  event_type    TEXT,
  is_redelivery INTEGER DEFAULT 0,
  status        TEXT NOT NULL,          -- processing／done／failed
  attempts      INTEGER NOT NULL DEFAULT 1,
  reply_sent    INTEGER NOT NULL DEFAULT 0,  -- 回覆或補送推播有一則成功就是 1
  redeliveries  INTEGER NOT NULL DEFAULT 0,  -- 之後又收到幾次重送（被擋掉的）
  first_seen_at TEXT NOT NULL,
  updated_at    TEXT NOT NULL
)`;

const MAX_ATTEMPTS = 2;

export async function ensureLineEventsTable(db) {
  await db.prepare(LINE_EVENTS_DDL).run();
}

// 回傳 { action: 'process' | 'reprocess' | 'skip', id, reason }
// 沒有 webhookEventId（理論上不會有）→ 照常處理，不去重。
export async function claimLineEvent(db, ev, now) {
  const id = ev && ev.webhookEventId ? String(ev.webhookEventId) : null;
  if (!id) return { action: 'process', id: null, reason: 'no_event_id' };
  const redelivery = ev.deliveryContext && ev.deliveryContext.isRedelivery === true ? 1 : 0;
  const userId = (ev.source && ev.source.userId) || null;
  const ins = await db.prepare(
    `INSERT OR IGNORE INTO line_webhook_events
       (event_id, line_user_id, event_type, is_redelivery, status, attempts, reply_sent, first_seen_at, updated_at)
     VALUES (?,?,?,?,'processing',1,0,?,?)`
  ).bind(id, userId, ev.type || null, redelivery, now, now).run();
  if (ins && ins.meta && ins.meta.changes === 1) return { action: 'process', id, reason: redelivery ? 'first_seen_redelivery' : 'new' };

  // 看過這個事件：只有「失敗或卡住（5 分鐘沒結束）＋確定沒送出回覆＋次數未滿」才再處理一次。
  // 用一句帶條件的 UPDATE 搶，避免兩個重送同時進來都搶到。
  const upd = await db.prepare(
    `UPDATE line_webhook_events SET status='processing', attempts=attempts+1, redeliveries=redeliveries+1, updated_at=?
      WHERE event_id=? AND reply_sent=0 AND attempts < ?
        AND (status='failed' OR (status='processing' AND updated_at < datetime('now','+8 hours','-5 minutes')))`
  ).bind(now, id, MAX_ATTEMPTS).run();
  if (upd && upd.meta && upd.meta.changes === 1) return { action: 'reprocess', id, reason: 'previous_attempt_failed' };

  await db.prepare(`UPDATE line_webhook_events SET redeliveries=redeliveries+1 WHERE event_id=?`).bind(id).run();
  return { action: 'skip', id, reason: 'duplicate' };
}

export async function markLineReplySent(db, id, now) {
  if (!id) return;
  await db.prepare(`UPDATE line_webhook_events SET reply_sent=1, updated_at=? WHERE event_id=?`).bind(now, id).run();
}

export async function finishLineEvent(db, id, ok, now) {
  if (!id) return;
  await db.prepare(`UPDATE line_webhook_events SET status=?, updated_at=? WHERE event_id=?`)
    .bind(ok ? 'done' : 'failed', now, id).run();
}

export async function purgeOldLineEvents(db) {
  await db.prepare(`DELETE FROM line_webhook_events WHERE first_seen_at < datetime('now','+8 hours','-2 days')`).run();
}

// reply token 失效（重送、逾時）時 LINE 回 400 {"message":"Invalid reply token"}
export function isInvalidReplyToken(status, bodyText) {
  return status === 400 && /invalid reply token/i.test(String(bodyText || ''));
}
