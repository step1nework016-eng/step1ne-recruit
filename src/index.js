/**
 * Step1ne 招募表單 API。
 *
 * step1ne.com 是純靜態站（Worker 在另一個 Cloudflare 帳號），
 * 所以表單頁放在那邊、後端放在這邊，用 CORS 串起來。
 *
 * 端點：
 *   POST /apply          收表單（公開）
 *   GET  /admin/list     顧問後台用（需 ADMIN_TOKEN）
 *   POST /admin/decide   回寫處置（需 ADMIN_TOKEN）
 *   GET  /health         公開
 */

const ORIGINS = ['https://step1ne.com', 'https://www.step1ne.com'];

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

// 履歷存 D1 的上限。D1 單列有大小限制，超過就要求改貼連結，
// 而不是靜默截斷——截斷的履歷比沒有履歷更糟，因為沒人會發現。
const MAX_RESUME_BYTES = 600 * 1024;

async function notify(env, text) {
  if (!env.TG_BOT_TOKEN || !env.TG_CHAT_ID) return;
  try {
    await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: env.TG_CHAT_ID, text, disable_web_page_preview: true }),
    });
  } catch {
    // 通知失敗不能影響應徵送出——人已經填完了，資料進 DB 才是重點
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === 'OPTIONS') return new Response(null, { headers: cors(request) });
    if (p === '/health') return json(request, { ok: true });

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

      const id = uid();
      const now = nowTaipei();
      let fileId = null;

      if (b.resume_b64) {
        const bytes = Math.floor((b.resume_b64.length * 3) / 4);
        if (bytes > MAX_RESUME_BYTES) {
          return json(
            request,
            { ok: false, error: '履歷檔超過 600KB，請改用「履歷連結」欄位貼雲端連結' },
            400
          );
        }
        fileId = uid();
        await env.DB.prepare(
          `INSERT INTO files (id, created_at, filename, mime, size, content_b64)
           VALUES (?,?,?,?,?,?)`
        ).bind(fileId, now, b.resume_name || 'resume', b.resume_mime || 'application/pdf',
               bytes, b.resume_b64).run();
      }

      await env.DB.prepare(
        `INSERT INTO applications
         (id, created_at, job_slug, job_title, name, email, phone,
          expected_salary, available_date, location_ok,
          resume_file_id, resume_url, note,
          utm_source, utm_medium, utm_campaign, referrer, status, consent_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'new', ?)`
      ).bind(
        id, now, b.job_slug, b.job_title || null, b.name, b.email, b.phone || null,
        b.expected_salary || null, b.available_date || null, b.location_ok || null,
        fileId, b.resume_url || null, b.note || null,
        b.utm_source || null, b.utm_medium || null, b.utm_campaign || null,
        b.referrer || null, now
      ).run();

      // AI 面談不需要顧問在場，所以這裡不是「預約顧問時段」而是
      // 「現在做」或「晚點提醒自己回來做」。不查任何人的行事曆。
      const mode = b.mode === 'later' ? 'later' : 'now';
      const remindAt = mode === 'later' && b.remind_at
        ? String(b.remind_at).replace('T', ' ') + ':00'
        : null;
      await env.DB.prepare(
        `UPDATE applications SET interview_mode=?, remind_at=?, status=? WHERE id=?`
      ).bind(mode, remindAt, mode === 'now' ? 'ready' : 'scheduled', id).run();

      await notify(
        env,
        `📥 新應徵：${b.name}\n職缺：${b.job_title || b.job_slug}\n` +
          `Email：${b.email}\n` +
          (mode === 'now' ? '要求現在就面談\n' : `預約提醒：${remindAt || '未填'}\n`) +
          (b.expected_salary ? `期望：${b.expected_salary}　` : '') +
          (b.available_date ? `可到職：${b.available_date}\n` : '\n') +
          `來源：${b.utm_source || b.referrer || '直接進入'}`
      );

      return json(request, { ok: true, id, mode });
    }

    // ── 顧問後台 ──
    if (p.startsWith('/admin/')) {
      const auth = request.headers.get('authorization') || '';
      if (!env.ADMIN_TOKEN || !safeEqual(auth, `Bearer ${env.ADMIN_TOKEN}`)) {
        return json(request, { ok: false, error: 'unauthorized' }, 401);
      }

      if (p === '/admin/list') {
        const { results } = await env.DB.prepare(
          `SELECT * FROM applications ORDER BY created_at DESC LIMIT 200`
        ).all();
        return json(request, { applications: results });
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
    // 到了候選人自己選的時間就提醒他回來做面談。
    // 這不是顧問的行程——AI 面談不需要顧問在場。
    const due = await env.DB.prepare(
      `SELECT id, name, email, job_title, remind_at FROM applications
        WHERE status='scheduled' AND remind_at IS NOT NULL
          AND remind_at <= datetime('now','+8 hours')
          AND reminded_at IS NULL`
    ).all();
    for (const r of due.results) {
      await notify(env, `⏰ 該提醒候選人回來面談：${r.name}　${r.job_title || ''}　（原訂 ${r.remind_at}）`);
      await env.DB.prepare(`UPDATE applications SET reminded_at=? WHERE id=?`)
        .bind(nowTaipei(), r.id).run();
    }
  },
};
