// 2026-11-02 每日巡檢。背景：Jacky 跟 Phoebe 一直比系統先發現東西壞掉——
//   人選名單超過 700 張被默默切掉（馬若喬搜不到）、載入中打的字被蓋掉、推薦報告沒讀到電洽紀錄。
// 目標：這類退步由系統先抓到，Jacky 10:00 上班前就收到 TG。
//
// 什麼時候跑：平日 09:30（cron）＋每次部署後（safe_deploy.py／ship.sh 打 POST /run）。
// 只讀：查 D1、用顧問後台「同一支」API 看資料、抓網頁。不寫資料、不寄信、不碰 104、不開 /chat/<token>。
// 全部通過就不吵；有問題才私訊 Jacky，白話、講誰會受影響。
//
// 手動跑：curl -X POST "https://step1ne-patrol-worker.aiagentg888.workers.dev/run?dry=1" -H "X-Patrol-Key: $PATROL_RUN_KEY"
//   dry=1     只回結果，不發 TG
//   test=fail 多加一項故意失敗的檢查（驗證警報真的發得出去），訊息會標【測試】
//   since=…   （限 dry=1）推薦報告從這個時間開始比，驗證用
//   why=xxx   觸發原因（deploy-worker / deploy-frontend / deploy-site），寫進訊息

const JACKY_TG = '8365775688';
const CONSULTANT_ORIGIN = 'https://step1ne-consultant-staging.pages.dev';   // 顧問後台網頁本體（step1ne.com/consultant/ 前面有 Gmail 登入門）
const SITE = 'https://step1ne.com';
const TRACKER_MAX_MS = 10000;
const STUCK_MIN = 30;
// 推薦報告要讀到電洽紀錄（candidate_notes）是 2026-11-02 13:54 才修好的（後台 16da1fb）；
// 之前產的報告本來就沒讀，不算這次巡檢要抓的退步。
const REPORT_CHECK_SINCE = '2026-11-02 14:00:00';
const UA = 'step1ne-patrol';

const rows = async (env, sql, ...b) => (await env.DB.prepare(sql).bind(...b).all()).results || [];
const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
const tpeNow = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace('T', ' ');

async function bo(env, path) {
  const t0 = Date.now();
  const r = await env.BO.fetch(new Request('https://backoffice' + path, {
    headers: { Authorization: `Bearer ${env.BO_TOKEN}`, 'User-Agent': UA },
  }));
  let body = null;
  try { body = await r.json(); } catch (e) { /* 不是 JSON 就當打不開 */ }
  return { status: r.status, body, ms: Date.now() - t0 };
}

// 後台回不了資料時，白話講原因
function boFail(r, what) {
  if (r.status === 401) return `${what}：後台不讓巡檢進去（巡檢用的鑰匙失效了）`;
  return `${what}：後台回錯誤（${r.status}${r.body && r.body.error ? '，' + String(r.body.error).slice(0, 60) : ''}）`;
}

// ── 1＋2. 人選名單、人選卡片 ──
async function checkTracker(env) {
  const problems = [];
  const newest = await rows(env, `SELECT id, name, created_at FROM applications ORDER BY created_at DESC LIMIT 5`);
  const total = (await rows(env, `SELECT COUNT(*) AS n FROM applications`))[0].n;
  const r = await bo(env, '/admin/pipeline/tracker');
  const info = { d1_total: total, ms: r.ms };
  if (r.status !== 200 || !r.body || !r.body.ok) {
    problems.push({ what: boFail(r, '後台人選名單打不開'), who: 'Jacky、Phoebe 打開顧問後台會看不到任何人選' });
    return { name: '人選名單', problems, info, newest: newest[0] };
  }
  // 同一個人（email／電話相同）會併成一張卡，其他筆放在 extra_placements——那些也算「看得到」
  const seen = new Set();
  for (const c of r.body.cards || []) {
    if (c.kind !== 'application') continue;
    seen.add(c.application_id);
    for (const e of c.extra_placements || []) if (e && e.application_id) seen.add(e.application_id);
  }
  info.visible = seen.size;
  const missing = newest.filter((a) => !seen.has(a.id));
  if (missing.length) {
    problems.push({
      what: `最新進來的人選在名單裡看不到：${missing.map((a) => `${a.name || '（沒名字）'}（${String(a.created_at).slice(5, 16)} 進來）`).join('、')}`,
      who: 'Jacky、Phoebe 搜尋找不到這些剛應徵的人',
    });
  }
  if (seen.size < total) {
    problems.push({
      what: `資料庫有 ${total} 位人選，後台名單只看得到 ${seen.size} 位（少了 ${total - seen.size} 位）`,
      who: 'Jacky、Phoebe 會搜不到被漏掉的人（跟 11/2 馬若喬一樣的情況）',
    });
  }
  if (r.ms > TRACKER_MAX_MS) {
    problems.push({
      what: `後台人選名單要等 ${(r.ms / 1000).toFixed(1)} 秒才出來（標準是 10 秒內）`,
      who: '顧問打開名單會一直轉圈，容易以為壞掉、或在載入中打字被蓋掉',
    });
  }
  return { name: '人選名單', problems, info, newest: newest[0] };
}

async function checkCard(env, newest) {
  const problems = [];
  if (!newest) return { name: '人選卡片', problems: [{ what: '資料庫裡找不到任何人選，沒辦法測卡片', who: '可能是資料庫連不上' }] };
  const r = await bo(env, `/admin/application/${encodeURIComponent(newest.id)}/card`);
  const ok = r.status === 200 && r.body && r.body.ok && r.body.application && r.body.application.id === newest.id;
  if (!ok) {
    problems.push({ what: boFail(r, `最新的人選「${newest.name || newest.id}」卡片打不開`), who: '顧問點開人選卡片會看到錯誤，沒辦法看資料、記電洽' });
  }
  return { name: '人選卡片', problems, info: { id: newest.id, ms: r.ms } };
}

// ── 3. 推薦報告有沒有讀到電洽紀錄 ──
async function checkReports(env, since = REPORT_CHECK_SINCE) {
  const problems = [];
  const reqs = await rows(env,
    `SELECT r.id, r.application_id, r.requested_at, r.ai_job_id, a.name
       FROM client_report_requests r LEFT JOIN applications a ON a.id = r.application_id
      WHERE r.status = 'done' AND r.requested_at >= datetime('now','+8 hours','-24 hours') AND r.requested_at >= ?
      ORDER BY r.requested_at DESC LIMIT 50`, since);
  let checked = 0;
  for (const q of reqs) {
    const note = (await rows(env,
      `SELECT content, created_at FROM candidate_notes
        WHERE application_id = ? AND type = '電洽紀錄' AND created_at < ?
        ORDER BY created_at DESC LIMIT 1`, q.application_id, q.requested_at))[0];
    if (!note || !q.ai_job_id) continue;   // 沒電洽過，或不是 AI 產的，就沒得比
    const job = (await rows(env, `SELECT payload_json FROM ai_jobs WHERE id = ?`, q.ai_job_id))[0];
    let callNotes = '';
    try { callNotes = (JSON.parse((job && job.payload_json) || '{}').call_notes) || ''; } catch (e) { /* 壞掉的 payload 當成空的 */ }
    const head = norm(note.content).slice(0, 30);
    checked++;
    if (!head) continue;
    if (!norm(callNotes).includes(head)) {
      problems.push({
        what: `${q.name || '人選'}的推薦報告（${String(q.requested_at).slice(5, 16)} 產）沒讀到 ${String(note.created_at).slice(5, 16)} 的電洽紀錄「${head.slice(0, 18)}…」`,
        who: '客戶拿到的報告缺最新通話內容（可能寫錯現況或待遇），送出前要重產',
      });
    }
  }
  return { name: '推薦報告', problems, info: { requests: reqs.length, compared: checked } };
}

// ── 4. 網頁本身打得開、JS 都抓得到 ──
async function get(url, manual) {
  try {
    const r = await fetch(url, { redirect: manual ? 'manual' : 'follow', headers: { 'User-Agent': UA } });
    return { status: r.status, text: manual ? '' : await r.text(), location: r.headers.get('location') || '' };
  } catch (e) {
    return { status: 0, text: '', error: String(e && e.message || e) };
  }
}

function assetRefs(html) {
  const out = [];
  for (const m of html.matchAll(/<script[^>]*\ssrc=["']([^"']+)["']/gi)) out.push(m[1]);
  for (const m of html.matchAll(/<link[^>]*\shref=["']([^"']+\.(?:css|m?js))(?:\?[^"']*)?["']/gi)) out.push(m[1]);
  return out;
}

function moduleRefs(js) {
  const out = [];
  for (const m of js.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s+)["'](\.{1,2}\/[^"']+|\/[^"']+)["']/g)) out.push(m[1]);
  return out;
}

async function checkPage(pageUrl, label, who) {
  const problems = [];
  const page = await get(pageUrl);
  if (page.status !== 200) {
    problems.push({ what: `${label}網頁打不開（${page.status || page.error}）`, who });
    return { problems, files: 0 };
  }
  // 從網頁引用的 JS／CSS 出發，順著 import 一路抓，任何一支 404 整個頁面就會白掉
  const queue = assetRefs(page.text).map((u) => new URL(u, pageUrl).href).filter((u) => u.startsWith(new URL(pageUrl).origin));
  const done = new Set();
  const bad = [];
  while (queue.length && done.size < 200) {
    const u = queue.shift();
    if (done.has(u)) continue;
    done.add(u);
    const r = await get(u);
    if (r.status !== 200) { bad.push(`${new URL(u).pathname}（${r.status || r.error}）`); continue; }
    if (/\.m?js(\?|$)/.test(u)) for (const s of moduleRefs(r.text)) queue.push(new URL(s, u).href);
  }
  if (bad.length) problems.push({ what: `${label}網頁有 ${bad.length} 個程式檔抓不到：${bad.slice(0, 4).join('、')}`, who });
  return { problems, files: done.size };
}

async function checkPages() {
  const problems = [];
  const consultantWho = 'Jacky、Phoebe 的顧問後台會白畫面或按鈕沒反應';
  const c = await checkPage(`${CONSULTANT_ORIGIN}/consultant/`, '顧問後台', consultantWho);
  problems.push(...c.problems);
  // step1ne.com/consultant/ 前面有 Gmail 登入門：巡檢沒登入，看到「轉去登入頁」就代表網址正常
  const gate = await get(`${SITE}/consultant/`, true);
  const gateOk = gate.status === 200 || ((gate.status === 302 || gate.status === 303) && /cloudflareaccess\.com/.test(gate.location));
  if (!gateOk) problems.push({ what: `step1ne.com/consultant/ 打開異常（${gate.status || gate.error}）`, who: consultantWho });
  const p = await checkPage(`${SITE}/portal/`, '客戶專區', '客戶登入客戶專區會打不開，看不到推薦人選');
  problems.push(...p.problems);
  return { name: '網頁', problems, info: { consultant_files: c.files, portal_files: p.files } };
}

// ── 5. 排隊的工作有沒有卡住 ──
async function checkQueues(env) {
  const problems = [];
  const cut = `-${STUCK_MIN} minutes`;
  const aj = await rows(env,
    `SELECT kind, COUNT(*) AS n, MIN(created_at) AS oldest FROM ai_jobs
      WHERE status = 'pending' AND created_at < datetime('now','+8 hours',?) GROUP BY kind ORDER BY n DESC`, cut);
  if (aj.length) {
    const n = aj.reduce((s, r) => s + r.n, 0);
    problems.push({
      what: `AI 背景工作有 ${n} 件排隊超過 ${STUCK_MIN} 分鐘沒人做（最早 ${String(aj.reduce((m, r) => (r.oldest < m ? r.oldest : m), aj[0].oldest)).slice(5, 16)}；種類：${aj.slice(0, 4).map((r) => `${r.kind}×${r.n}`).join('、')}）`,
      who: '電洽摘要、推薦報告、人選配對這些結果會一直出不來——可能是 WSL2／Mac 的 AI 師傅沒在跑',
    });
  }
  const cr = await rows(env,
    `SELECT r.requested_at, a.name FROM client_report_requests r LEFT JOIN applications a ON a.id = r.application_id
      WHERE r.status = 'awaiting_synthesis' AND r.requested_at < datetime('now','+8 hours',?) ORDER BY r.requested_at`, cut);
  if (cr.length) {
    problems.push({
      what: `推薦報告卡在「產生中」超過 ${STUCK_MIN} 分鐘：${cr.slice(0, 5).map((r) => `${r.name || '人選'}（${String(r.requested_at).slice(5, 16)} 申請）`).join('、')}`,
      who: '顧問按了產報告卻一直拿不到，客戶那邊會等不到推薦',
    });
  }
  const sp = await rows(env,
    `SELECT id, COALESCE(posting_at, claimed_at, requested_at) AS t FROM social_post_queue
      WHERE status = 'posting' AND COALESCE(posting_at, claimed_at, requested_at) < datetime('now','+8 hours',?) ORDER BY t`, cut);
  if (sp.length) {
    problems.push({
      what: `社群貼文有 ${sp.length} 篇卡在「發送中」超過 ${STUCK_MIN} 分鐘（編號 ${sp.slice(0, 5).map((r) => '#' + r.id).join('、')}）`,
      who: '這幾篇可能沒發出去、或串文只發一半，後面排的貼文也會被卡住',
    });
  }
  return { name: '排隊工作', problems, info: { ai_jobs: aj, reports: cr.length, social: sp.length } };
}

function failingTestCheck() {
  return {
    name: '測試',
    problems: [{ what: '（測試）故意找一位不存在的人選，確認巡檢發現問題時真的會通知', who: '沒有人受影響，這則是測試，不用處理' }],
    info: {},
  };
}

async function safe(name, fn) {
  try { return await fn(); } catch (e) {
    return { name, problems: [{ what: `巡檢在檢查「${name}」時自己出錯了（${String(e && e.message || e).slice(0, 80)}）`, who: '這一項今天沒檢查到，可能有問題沒被發現' }] };
  }
}

async function runPatrol(env, { send = true, test = false, why = 'daily', reportSince } = {}) {
  const t0 = Date.now();
  const tracker = await safe('人選名單', () => checkTracker(env));
  const results = [
    tracker,
    await safe('人選卡片', () => checkCard(env, tracker.newest)),
    await safe('推薦報告', () => checkReports(env, reportSince)),
    await safe('網頁', () => checkPages()),
    await safe('排隊工作', () => checkQueues(env)),
  ];
  if (test) results.push(failingTestCheck());
  for (const r of results) delete r.newest;
  const problems = results.flatMap((r) => r.problems);
  const out = { ok: problems.length === 0, at: tpeNow(), why, ms: Date.now() - t0, results };
  console.log('[patrol]', JSON.stringify(out));
  if (problems.length && send) out.tg = await sendAlert(env, problems, { test, why });
  return out;
}

const WHY_LABEL = { daily: '每日 09:30 巡檢', 'deploy-worker': '後台剛部署完的巡檢', 'deploy-frontend': '顧問後台畫面剛部署完的巡檢', 'deploy-site': '官網剛部署完的巡檢' };

async function sendAlert(env, problems, { test, why }) {
  const lines = [
    `${test ? '【測試，不用處理】' : '⚠️ '}系統巡檢發現 ${problems.length} 件事要處理`,
    `（${WHY_LABEL[why] || '手動巡檢'}，${tpeNow().slice(5)}）`,
    '',
  ];
  problems.forEach((p, i) => {
    lines.push(`${i + 1}. ${p.what}`);
    lines.push(`   影響：${p.who}`);
  });
  lines.push('', '把這則轉給 Claude 就能開始查。');
  const r = await fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: JACKY_TG, text: lines.join('\n').slice(0, 4000), disable_web_page_preview: true }),
  });
  const body = await r.json().catch(() => ({}));
  if (!body.ok) console.error('[patrol] TG 發送失敗', r.status, body.description);
  return { ok: !!body.ok, message_id: body.result && body.result.message_id };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runPatrol(env, { send: true, why: 'daily' }));
  },
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/run' || request.method !== 'POST') return new Response('step1ne patrol', { status: 404 });
    if (!env.RUN_KEY || request.headers.get('x-patrol-key') !== env.RUN_KEY) return new Response('unauthorized', { status: 401 });
    const out = await runPatrol(env, {
      send: url.searchParams.get('dry') !== '1',
      test: url.searchParams.get('test') === 'fail',
      why: (url.searchParams.get('why') || 'manual').slice(0, 30),
      // 驗證用：只在 dry=1 時可以把推薦報告的起算時間往前調（例：since=2026-11-02 00:00:00）
      reportSince: url.searchParams.get('dry') === '1' && url.searchParams.get('since') ? url.searchParams.get('since') : undefined,
    });
    return new Response(JSON.stringify(out, null, 1), { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
  },
};
