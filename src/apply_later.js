// 「先送出，晚點再聊」的排程那半（2026-10-21）。選的那一刻在 step1ne-public-worker/src/apply_later.js（POST /apply/later）。
//
// 這支每 15 分鐘跟著 scheduled() 跑（緊接在 lineClickNudge 後面），做兩件事：
//   1. 隔天 10:00 提醒「一次」：選了之後隔天（台北日曆日）10:00～20:59 之間，還沒開始面談（面談室裡沒有人選自己講的話）
//      → 有綁 LINE 推 Flex 卡片，沒綁（或推送失敗）寄信。先在 apply_later.reminder_at 搶到才送，所以永遠只會有一則。
//      錯過隔天（例如排程整天掛掉）就不補，不在第三天以後才突然催。
//   2. 滿 7 天還沒面談：applications.status 'later' → 'no_interview'（後台顯示「沒完成面談」），TG 告知一次。
//      面談室入口在 public worker 那邊用到期時間直接擋，不依賴這支有沒有跑到。
//
// 候選人文字規則：不寫「客戶」、不用顧問／我們當主詞、不放內部備註。

export const ENTRY_MARKER = '（候選人已進入面談室）';
const SITE = 'https://step1ne.com';
const LIFF_ROOM = 'https://liff.line.me/2011631457-YcRRVX0v?t=';
const LINE_OA_URL = 'https://lin.ee/XcSWPzM';
export const REMIND_HOUR_FROM = 10;
export const REMIND_HOUR_TO = 21;   // 不含：最晚 20:59 送

const LATER_TABLE_SQL = `CREATE TABLE IF NOT EXISTS apply_later (
  application_id TEXT PRIMARY KEY,
  chosen_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  reminder_at TEXT,
  reminder_channel TEXT,
  reminder_ok INTEGER,
  expired_marked_at TEXT)`;

const day = (ts) => String(ts || '').slice(0, 10);
const nextDay = (ymd) => new Date(Date.parse(ymd + 'T00:00:00Z') + 86400e3).toISOString().slice(0, 10);

// 現在（台北時間字串）該不該提醒這一筆？只在「選的隔天」10:00～20:59。
export function reminderDue(row, now) {
  if (!row || row.reminder_at) return false;
  if (row.status !== 'later') return false;
  if (row.interview_state && row.interview_state !== 'not_started') return false;
  if (Number(row.cand_msgs || 0) > 0) return false;
  if (String(now) >= String(row.expires_at)) return false;
  if (day(now) !== nextDay(day(row.chosen_at))) return false;
  const h = Number(String(now).slice(11, 13));
  return h >= REMIND_HOUR_FROM && h < REMIND_HOUR_TO;
}

// 「連結 N 天內有效」：剩幾天（無條件進位，最少 1）
export function daysLeft(expiresAt, now) {
  const ms = Date.parse(String(expiresAt).replace(' ', 'T') + 'Z') - Date.parse(String(now).replace(' ', 'T') + 'Z');
  return Math.max(1, Math.ceil(ms / 86400e3));
}

export function reminderFlex({ jobTitle, token, days }) {
  const job = String(jobTitle || '').trim() || '職缺';
  return {
    type: 'flex', altText: '您的面談還在等您',
    contents: {
      type: 'bubble',
      header: { type: 'box', layout: 'vertical', backgroundColor: '#0F5F87', paddingAll: '14px',
        contents: [{ type: 'text', text: '您的面談還在等您', color: '#FFFFFF', size: 'md', weight: 'bold', wrap: true }] },
      body: { type: 'box', layout: 'vertical', paddingAll: '16px', spacing: 'md',
        contents: [
          { type: 'text', text: `昨天您應徵了「${job}」，謝謝您！`, size: 'sm', color: '#0D1B24', wrap: true },
          { type: 'text', text: '只差最後一步：跟阿財聊 15～25 分鐘，聊完就能進到推薦階段。', size: 'sm', color: '#0D1B24', wrap: true },
          { type: 'text', text: `連結 ${days} 天內有效`, size: 'xs', color: '#4A5A66', wrap: true },
        ] },
      footer: { type: 'box', layout: 'vertical', paddingAll: '12px', spacing: 'sm',
        contents: [
          { type: 'button', style: 'primary', color: '#0F5F87', height: 'sm',
            action: { type: 'uri', label: '現在開始聊', uri: LIFF_ROOM + encodeURIComponent(token) } },
          { type: 'button', style: 'link', color: '#0F5F87', height: 'sm',
            action: { type: 'uri', label: '約一個時間', uri: `${SITE}/book/?t=${encodeURIComponent(token)}` } },
        ] },
    },
  };
}

const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const FONT = "'Noto Sans TC','PingFang TC','Microsoft JhengHei',Arial,sans-serif";
const btn = (url, text, bg, color = '#FFFFFF', border = bg) =>
  `<a href="${esc(url)}" style="display:inline-block;background:${bg};color:${color};border:1.5px solid ${border};text-decoration:none;font-weight:700;font-size:15px;padding:12px 22px;border-radius:10px;">${esc(text)}</a>`;

// 沒綁 LINE 的人收這封。樣式同 interview_thanks_mail.py（藍灰設計版）。
export function reminderMail({ name, jobTitle, token, days }) {
  const nm = String(name || '').trim();
  const job = String(jobTitle || '').trim();
  const room = `${SITE}/interview/?t=${encodeURIComponent(token)}`;
  const book = `${SITE}/book/?t=${encodeURIComponent(token)}`;
  const subject = `您的面談還在等您：${job}`;
  const html = '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
    + `<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(subject)}</title></head>`
    + '<body style="margin:0;padding:0;background:#F4F6F8;">'
    + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6F8;"><tr><td align="center" style="padding:24px 12px;">'
    + `<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#FFFFFF;border-radius:16px;overflow:hidden;font-family:${FONT};color:#0D1B24;">`
    + `<tr><td style="padding:18px 24px;border-bottom:1px solid #D9E0E6;"><img src="${SITE}/assets/step1ne-logo.png" alt="Step1ne" height="28" style="height:28px;display:block;"></td></tr>`
    + '<tr><td style="padding:28px 24px 8px;text-align:center;">'
    + `<img src="${SITE}/assets/acai-avatar-192.png" alt="" width="72" height="72" style="width:72px;height:72px;border-radius:50%;display:inline-block;">`
    + '<h1 style="margin:12px 0 6px;font-size:22px;line-height:1.4;">您的面談還在等您</h1>'
    + `<p style="margin:0;font-size:15px;line-height:1.7;color:#4A5A66;">${esc(nm)} 您好，昨天您應徵了「${esc(job)}」，謝謝您！<br>`
    + '只差最後一步：跟阿財聊 15～25 分鐘，聊完就能進到推薦階段。</p></td></tr>'
    + '<tr><td style="padding:18px 24px 4px;text-align:center;">' + btn(room, '現在開始聊', '#0F5F87') + '</td></tr>'
    + '<tr><td style="padding:10px 24px 4px;text-align:center;">' + btn(book, '約一個時間', '#FFFFFF', '#0F5F87', '#0F5F87') + '</td></tr>'
    + `<tr><td style="padding:12px 24px 0;text-align:center;font-size:13px;color:#4A5A66;">連結 ${days} 天內有效</td></tr>`
    + '<tr><td style="padding:18px 24px 8px;text-align:center;">' + btn(LINE_OA_URL, '加 LINE，進度第一時間通知您', '#06C755') + '</td></tr>'
    + '<tr><td style="padding:18px 24px 22px;border-top:1px solid #D9E0E6;font-size:12px;line-height:1.7;color:#4A5A66;text-align:center;">'
    + 'Step1ne 德仁管理顧問｜official@step1ne.com<br>就業服務許可證：北市就服字第 0363 號<br>有任何問題，直接回覆這封信就可以。</td></tr>'
    + '</table></td></tr></table></body></html>';
  const text = [`${nm} 您好，`, '', `昨天您應徵了「${job}」，謝謝您！`,
    '只差最後一步：跟阿財聊 15～25 分鐘，聊完就能進到推薦階段。', '',
    `現在開始聊：${room}`, `約一個時間：${book}`, `連結 ${days} 天內有效。`, '',
    `加 LINE，進度第一時間通知您：${LINE_OA_URL}`, '',
    'Step1ne 德仁管理顧問', 'Email：official@step1ne.com', '就業服務許可證：北市就服字第 0363 號', '有任何問題，直接回覆這封信就可以。'].join('\n');
  return { subject, text, html };
}

const phone9 = (s) => String(s || '').replace(/\D/g, '').slice(-9);
const safeArr = (s) => { try { const v = JSON.parse(s || '[]'); return Array.isArray(v) ? v : []; } catch { return []; } };

// 這位人選綁定的 LINE 帳號：綁定紀錄裡有這筆應徵，或同一支手機驗證過綁定
export function lineTargets(bindings, app) {
  const p = phone9(app.phone);
  return (bindings || []).filter((b) => safeArr(b.application_ids).includes(app.id) || (p.length === 9 && phone9(b.phone) === p))
    .map((b) => b.line_user_id).filter((v, i, a) => v && a.indexOf(v) === i);
}

// deps：{ nowTaipei, linePushMessages, notify, uid, THREAD }
export async function applyLaterTick(env, deps) {
  const { nowTaipei, linePushMessages, notify, uid, THREAD } = deps;
  await env.DB.prepare(LATER_TABLE_SQL).run();
  const now = nowTaipei();
  const out = { reminded: [], expired: [] };

  // ── 1. 隔天 10:00 提醒一次 ──
  const { results: cands } = await env.DB.prepare(
    `SELECT l.application_id, l.chosen_at, l.expires_at, l.reminder_at,
            a.id, a.name, a.email, a.phone, a.chat_token, a.status, a.interview_state, a.job_slug,
            COALESCE(j.title, a.job_title, a.job_slug) AS job_title, j.status AS job_status,
            (SELECT COUNT(*) FROM messages m WHERE m.application_id = a.id AND m.role = 'candidate' AND m.content != ?) AS cand_msgs
       FROM apply_later l JOIN applications a ON a.id = l.application_id
       LEFT JOIN jobs j ON j.slug = a.job_slug
      WHERE l.reminder_at IS NULL AND a.status = 'later' AND l.expires_at > ?
      LIMIT 30`
  ).bind(ENTRY_MARKER, now).all();
  const due = (cands || []).filter((r) => reminderDue(r, now));
  let bindings = null;
  for (const r of due) {
    // 先搶：同時兩個排程（或重跑）只有一個搶得到，所以永遠只會送一則
    const claim = await env.DB.prepare(`UPDATE apply_later SET reminder_at = ? WHERE application_id = ? AND reminder_at IS NULL`)
      .bind(now, r.application_id).run();
    if (!claim.meta || !claim.meta.changes) continue;
    if (r.job_status === 'closed') {
      await env.DB.prepare(`UPDATE apply_later SET reminder_channel = 'skipped_job_closed', reminder_ok = 0 WHERE application_id = ?`)
        .bind(r.application_id).run();
      continue;
    }
    const days = daysLeft(r.expires_at, now);
    if (bindings === null) {
      bindings = (await env.DB.prepare(`SELECT line_user_id, application_ids, phone FROM line_bindings WHERE state = 'bound'`).all()).results || [];
    }
    let channel = 'none', ok = false;
    const targets = lineTargets(bindings, r).slice(0, 1);   // 同一人綁兩個帳號也只推一則
    if (targets.length) {
      try { ok = !!(await linePushMessages(env, targets[0], [reminderFlex({ jobTitle: r.job_title, token: r.chat_token, days })])); }
      catch (e) { ok = false; }
      if (ok) channel = 'line';
    }
    if (!ok && r.email) {
      const m = reminderMail({ name: r.name, jobTitle: r.job_title, token: r.chat_token, days });
      try {
        await env.DB.prepare(
          `INSERT INTO mail_outbox (id, kind, from_addr, to_email, subject, text, html, ref_table, ref_id, status, created_at)
           VALUES (?, 'apply_later_reminder', 'Step1ne <official@step1ne.com>', ?, ?, ?, ?, 'applications', ?, 'pending', ?)`
        ).bind(uid(), r.email, m.subject, m.text, m.html, r.id, now).run();
        channel = 'email'; ok = true;
      } catch (e) { console.error('apply_later reminder mail', String(e)); }
    }
    await env.DB.prepare(`UPDATE apply_later SET reminder_channel = ?, reminder_ok = ? WHERE application_id = ?`)
      .bind(channel, ok ? 1 : 0, r.application_id).run();
    out.reminded.push({ id: r.id, channel, ok });
  }

  // ── 2. 滿 7 天還沒面談 → 沒完成面談 ──
  const { results: exp } = await env.DB.prepare(
    `SELECT l.application_id, a.name, COALESCE(j.title, a.job_title, a.job_slug) AS job_title
       FROM apply_later l JOIN applications a ON a.id = l.application_id
       LEFT JOIN jobs j ON j.slug = a.job_slug
      WHERE l.expired_marked_at IS NULL AND l.expires_at <= ? AND a.status = 'later'
        AND (a.interview_state IS NULL OR a.interview_state = 'not_started')
      LIMIT 30`
  ).bind(now).all();
  for (const r of exp || []) {
    const u = await env.DB.prepare(
      `UPDATE applications SET status = 'no_interview' WHERE id = ? AND status = 'later'
         AND (interview_state IS NULL OR interview_state = 'not_started')`
    ).bind(r.application_id).run();
    await env.DB.prepare(`UPDATE apply_later SET expired_marked_at = ? WHERE application_id = ?`).bind(now, r.application_id).run();
    if (u.meta && u.meta.changes) {
      out.expired.push(r.application_id);
      if (notify) {
        await notify(env, `⌛ ${r.name} 選了「先送出，晚點再聊」，7 天都沒跟阿財聊，已標「沒完成面談」\n職缺：${r.job_title}\n要不要打電話由顧問決定（後台人選卡片看得到）。`,
          THREAD ? { message_thread_id: THREAD.intake } : undefined).catch(() => {});
      }
    }
  }
  return out;
}
