// 2026-10-23 資安 F002 Stage A：後台 API 從「一把萬用鑰匙」改成「每支程式一把具名權杖」。
//
// - 舊的 ADMIN_TOKEN 照樣能用（新舊並行，阿財不會斷），記成 caller='legacy-admin'。
// - 新的具名權杖放在 secret API_TOKENS_JSON：{"mac-acai":"<sha256 hex>", ...}
//   只存雜湊、不存原值；比對用固定時間比較，而且每一把都比完才回答（不提早跳出）。
// - 每次開門都記是誰（console.log，wrangler tail 看得到）；
//   另外每個 isolate 每把權杖每小時最多寫一次 D1 api_caller_seen（抽樣，不會吃爆額度），
//   Stage B 要靠這張表確認「一週都沒人再用舊鑰匙」才關掉舊的。

const enc = new TextEncoder();
let parsedRaw = null;
let parsed = [];

function tokenTable(env) {
  const raw = env.API_TOKENS_JSON || '';
  if (raw !== parsedRaw) {
    parsedRaw = raw;
    parsed = [];
    try {
      const o = raw ? JSON.parse(raw) : {};
      for (const [name, hash] of Object.entries(o)) {
        if (typeof hash === 'string' && /^[0-9a-f]{64}$/i.test(hash)) parsed.push([String(name).slice(0, 40), hash.toLowerCase()]);
      }
    } catch (e) {
      console.error('[auth] API_TOKENS_JSON 格式錯誤', e && e.message);
    }
  }
  return parsed;
}

function ctEq(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function sha256hex(s) {
  const buf = await crypto.subtle.digest('SHA-256', enc.encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

// 回傳開門的人是誰（'legacy-admin'、'mac-acai'…），認不得就回 null。
export async function apiCaller(env, authHeader) {
  const auth = String(authHeader || '');
  let who = null;
  if (env.ADMIN_TOKEN && ctEq(auth, `Bearer ${env.ADMIN_TOKEN}`)) who = 'legacy-admin';
  const table = tokenTable(env);
  if (table.length && auth.startsWith('Bearer ') && auth.length > 7 + 20) {
    const h = await sha256hex(auth.slice(7));
    for (const [name, hash] of table) {
      if (ctEq(h, hash) && !who) who = name;   // 全部比完，不提早跳出
    }
  }
  return who;
}

const lastSeen = new Map();   // isolate 內：caller → 上次寫 D1 的時間
const SEEN_EVERY_MS = 60 * 60 * 1000;

// 記錄誰開了門。D1 一小時最多一筆／每把權杖／每個 isolate。
export function auditCaller(env, ctx, who, request, path, worker) {
  const ua = (request.headers.get('user-agent') || '').slice(0, 80);
  console.log(`[auth] worker=${worker} caller=${who} ${request.method} ${path} ua=${ua}`);
  const now = Date.now();
  if (now - (lastSeen.get(who) || 0) < SEEN_EVERY_MS) return;
  lastSeen.set(who, now);
  if (!env.DB || !ctx) return;
  const day = new Date(now + 8 * 3600 * 1000).toISOString().slice(0, 10);   // 台灣日期
  ctx.waitUntil(env.DB.prepare(
    `INSERT INTO api_caller_seen (caller, day, worker, first_seen, last_seen, last_path, last_ua, samples)
     VALUES (?, ?, ?, datetime('now'), datetime('now'), ?, ?, 1)
     ON CONFLICT(caller, day, worker) DO UPDATE SET last_seen = datetime('now'), last_path = excluded.last_path,
       last_ua = excluded.last_ua, samples = samples + 1`
  ).bind(who, day, worker, String(path).slice(0, 120), ua).run().catch((e) => console.error('[auth] seen', e && e.message)));
}
