// 候選人信件新版設計（2026-10-21 Jacky：「信件 UIUX 也要改」）。
//
// 樣式照 Jacky 核准的面談感謝信（~/下載項目/面談感謝信_樣板_20261021.html、
// step1ne-recruit/interview_thanks_mail.py）：淺灰藍底、白卡 16px 圓角、左上小 logo（28px）、
// 主按鈕 #0F5F87、LINE 按鈕 #06C755、頁尾公司名＋official@＋就服許可證。
//
// ⚠️ 這個檔案在三個 Worker 各放一份、內容一模一樣（Worker 各自部署，不能跨 repo import）：
//      step1ne-public-worker/src/mail_v2.js（正本）
//      step1ne-backoffice-worker/src/mail_v2.js
//      step1ne-recruit/src/mail_v2.js
//    改一份就三份一起改；step1ne-recruit/tests/mail_v2_test.mjs 會比對三份是否相同。
//    Python 那邊（interview_daemon、doc_forward）用 step1ne-recruit/mail_layout.py，同一套樣式。
//
// 只換「長相」，不換字：每封信的文字照原本的，純文字版也照原本的。
//
// 開關：D1 site_settings key='mail_design_v2'，值 off／test／on（沒設＝off）。
//   test＝只有姓名含「測試」的應徵（或收件信箱屬於姓名含「測試」的應徵）才用新版；on＝全部候選人信件。
//   企業／客戶端的信（開發信、推薦信、用人需求表、招募評估工具）不走這裡。

export const MAIL_V2_KEY = 'mail_design_v2';

const SITE = 'https://step1ne.com';
const LOGO = `${SITE}/assets/step1ne-logo.png`;
const AVATAR = `${SITE}/assets/acai-avatar-192.png`;
const LINE_URL = 'https://lin.ee/XcSWPzM';

const C = {
  bg: '#F4F6F8', card: '#FFFFFF', ink: '#0D1B24', mu: '#4A5A66', line: '#D9E0E6',
  soft: '#EEF3F7', btn: '#0F5F87', lineGreen: '#06C755', ok: '#2F7A4D',
};
const FONT = "'Noto Sans TC','PingFang TC','Microsoft JhengHei',Arial,sans-serif";

const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// 文字裡的網址變成可點的連結（其餘照原樣跳脫）
function linkify(t) {
  const s = String(t ?? '');
  let out = '';
  let last = 0;
  const re = /https?:\/\/[^\s<>"'，。）)」]+/g;
  let m;
  while ((m = re.exec(s))) {
    out += esc(s.slice(last, m.index));
    out += `<a href="${esc(m[0])}" style="color:${C.btn};font-weight:700;word-break:break-all;">${esc(m[0])}</a>`;
    last = m.index + m[0].length;
  }
  return out + esc(s.slice(last));
}

// ── 開關 ──
let _modeCache = { at: 0, v: 'off' };
export async function mailV2Mode(env) {
  if (Date.now() - _modeCache.at < 60000) return _modeCache.v;
  let v = 'off';
  try {
    const r = await env.DB.prepare(`SELECT value FROM site_settings WHERE key = ?`).bind(MAIL_V2_KEY).first();
    const x = String((r && r.value) || '').trim().toLowerCase();
    if (x === 'on' || x === 'test') v = x;
  } catch { v = 'off'; }
  _modeCache = { at: Date.now(), v };
  return v;
}

// 這封信要不要用新版。test 模式：姓名含「測試」才用。
// name：已知的人選姓名（或信的第一行「XXX 您好，」也行）；appId：applications.id；email：收件信箱。
export async function mailV2For(env, { name, appId, email } = {}) {
  const mode = await mailV2Mode(env);
  if (mode === 'on') return true;
  if (mode !== 'test') return false;
  if (String(name || '').includes('測試')) return true;
  try {
    if (appId) {
      const r = await env.DB.prepare(`SELECT name FROM applications WHERE id = ?`).bind(String(appId)).first();
      if (r && String(r.name || '').includes('測試')) return true;
    }
    if (email) {
      const r = await env.DB.prepare(
        `SELECT 1 FROM applications WHERE lower(email) = lower(?) AND name LIKE '%測試%' LIMIT 1`).bind(String(email).trim()).first();
      if (r) return true;
    }
  } catch { /* 查不到就當不是測試 */ }
  return false;
}

// ── 零件 ──
export function buttonV2(url, text, bg = C.btn) {
  return `<a href="${esc(url)}" style="display:inline-block;background:${bg};color:#FFFFFF;text-decoration:none;`
    + `font-weight:700;font-size:15px;line-height:1.3;padding:13px 24px;border-radius:10px;">${esc(text)}</a>`;
}
const fallbackUrl = (url, label = '按鈕打不開的話，複製這個網址：') =>
  `<div style="font-size:12px;line-height:1.6;color:${C.mu};margin-top:8px;">${esc(label)}<br>`
  + `<span style="color:${C.btn};word-break:break-all;">${esc(url)}</span></div>`;
const P = (html, style = '') => `<p style="margin:0 0 14px;font-size:15px;line-height:1.75;color:${C.ink};${style}">${html}</p>`;

function renderBlock(b) {
  if (!b) return '';
  if (typeof b === 'string') return b.trim() ? P(linkify(b)) : '';
  switch (b.type) {
    case 'p': return String(b.text || '').trim() ? P(linkify(b.text).replace(/\n/g, '<br>')) : '';
    case 'note': return `<p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:${C.mu};">${linkify(b.text)}</p>`;
    case 'facts':
      return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${C.soft};border-radius:12px;margin:4px 0 18px;">`
        + `<tr><td style="padding:8px 4px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">`
        + (b.rows || []).map(([k, v]) => `<tr><td valign="top" style="padding:7px 12px;font-size:13px;color:${C.mu};white-space:nowrap;width:1%;">${esc(k)}</td>`
          + `<td style="padding:7px 12px 7px 0;font-size:15px;line-height:1.6;color:${C.ink};">${linkify(v)}</td></tr>`).join('')
        + `</table></td></tr></table>`;
    case 'list': {
      const tag = b.ordered ? 'ol' : 'ul';
      const items = (b.items || []).map((x) => `<li style="margin:4px 0;">${linkify(x)}</li>`).join('');
      const list = `<${tag} style="margin:0;padding:0 0 0 22px;font-size:15px;line-height:1.7;color:${C.ink};">${items}</${tag}>`;
      return b.panel
        ? `<div style="background:${C.soft};border-radius:12px;padding:12px 16px;margin:0 0 18px;">${list}</div>`
        : `<div style="margin:0 0 16px;">${list}</div>`;
    }
    case 'button':
      return `<div style="margin:6px 0 20px;">${buttonV2(b.url, b.text)}${b.fallback === false ? '' : fallbackUrl(b.url)}</div>`;
    case 'link':
      return `<p style="margin:0 0 16px;font-size:14px;"><a href="${esc(b.url)}" style="color:${C.btn};font-weight:700;">${esc(b.text)} →</a></p>`;
    case 'steps':
      return (b.steps || []).map((s, i) =>
        `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${C.soft};border-radius:12px;margin:0 0 12px;">`
        + `<tr><td width="44" valign="top" style="padding:16px 0 16px 16px;">`
        + `<div style="width:26px;height:26px;border-radius:50%;background:${i === 0 ? C.btn : C.card};color:${i === 0 ? '#FFFFFF' : C.btn};`
        + `border:1.5px solid ${C.btn};font-size:13px;font-weight:700;line-height:26px;text-align:center;">${i + 1}</div></td>`
        + `<td style="padding:16px 16px 16px 4px;">`
        + `<div style="font-size:15.5px;font-weight:700;line-height:1.5;color:${C.ink};">${esc(s.title)}</div>`
        + (s.body ? `<div style="font-size:14px;line-height:1.7;color:${C.mu};margin:4px 0 12px;">${linkify(s.body)}</div>` : '<div style="height:10px;"></div>')
        + (s.url ? buttonV2(s.url, s.text) + fallbackUrl(s.url, '按鈕打不開再用這個網址：') : '')
        + `</td></tr></table>`).join('') + '<div style="height:6px;"></div>';
    case 'html': return b.html || '';
    default: return '';
  }
}

/**
 * 組一封新版信的 HTML。
 *  subject   ：<title> 用
 *  headline  ：卡片上方的大標（可不給；給的話必須是信裡原本就有的字，例如主旨）
 *  avatar    ：true＝阿財說話的信，上方放阿財頭像
 *  blocks    ：內文（字串＝一段；或 {type:'p'|'note'|'facts'|'list'|'button'|'link'|'steps'|'html', ...}）
 *  sign      ：署名行（內文最後，灰字）
 *  line      ：{ text, button } 加 LINE 區塊；沒給就不放
 *  footer    ：頁尾多加的行（統編、地址、自動發送說明…）
 *  footerBase：false＝不放預設頁尾那兩行（公司名｜official@、就服許可證）——署名已經有就不重複
 */
export function renderMailV2({ subject = '', headline = '', avatar = false, blocks = [], sign = [], line = null, footer = [], footerBase = true } = {}) {
  const signLines = (sign || []).filter((x) => x != null && String(x).trim() !== '');
  const base = ['Step1ne 德仁管理顧問｜official@step1ne.com', '就業服務許可證：北市就服字第 0363 號'];
  const signText = signLines.join('\n');
  // 署名已經寫了就服許可證就不在頁尾重複一次
  const footLines = [...(footerBase ? base.filter((l) => !signText.includes(l)) : []), ...(footer || [])]
    .filter((l, i, a) => l && a.indexOf(l) === i);
  return '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + `<title>${esc(subject)}</title></head>`
    + `<body style="margin:0;padding:0;background:${C.bg};">`
    + `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:${C.bg};">`
    + '<tr><td align="center" style="padding:24px 12px;">'
    + `<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:${C.card};`
    + `border-radius:16px;overflow:hidden;font-family:${FONT};color:${C.ink};">`
    // 抬頭：左上小 logo＋底線
    + `<tr><td style="padding:18px 24px;border-bottom:1px solid ${C.line};">`
    + `<img src="${LOGO}" alt="Step1ne 德仁管理顧問" height="28" style="height:28px;width:auto;display:block;border:0;"></td></tr>`
    // 大標／阿財頭像
    + (avatar || headline
      ? `<tr><td style="padding:26px 24px 0;">`
        + (avatar ? `<img src="${AVATAR}" alt="阿財" width="56" height="56" style="width:56px;height:56px;border-radius:50%;display:block;margin:0 0 12px;border:0;">` : '')
        + (headline ? `<h1 style="margin:0;font-size:21px;line-height:1.45;font-weight:700;color:${C.ink};">${esc(headline)}</h1>` : '')
        + '</td></tr>'
      : '')
    // 內文
    + `<tr><td style="padding:${avatar || headline ? '18px' : '26px'} 24px 6px;">`
    + (blocks || []).map(renderBlock).join('')
    + (signLines.length ? `<p style="margin:22px 0 4px;font-size:14px;line-height:1.75;color:${C.mu};">${signLines.map(esc).join('<br>')}</p>` : '')
    + '</td></tr>'
    // 加 LINE
    + (line
      ? `<tr><td style="padding:10px 24px 22px;">`
        + `<div style="background:${C.soft};border-radius:12px;padding:16px;">`
        + (line.text ? `<div style="font-size:14px;line-height:1.7;color:${C.ink};margin:0 0 12px;">${esc(line.text)}</div>` : '')
        + buttonV2(line.url || LINE_URL, line.button || '加入 LINE 官方帳號', C.lineGreen) + '</div></td></tr>'
      : '')
    // 頁尾
    + `<tr><td style="padding:18px 24px 22px;border-top:1px solid ${C.line};font-size:12px;line-height:1.75;color:${C.mu};text-align:center;">`
    + footLines.map(esc).join('<br>') + '</td></tr>'
    + '</table></td></tr></table></body></html>';
}

// 舊版 sendMail(lines, cta, …) 的內容換新版長相（字一字不改）。
// 頁尾那段（祝 順心／招募團隊 敬上／LINE／統編／地址／系統自動發送）是 sendMail 原本就有的字。
export function renderSendMailV2(subject, lines, cta, { avatar = false } = {}) {
  const steps = Array.isArray(cta) ? cta : null;
  // 連續的空行／純文字行照原樣；「・」開頭的連續行（例如面談時段）收成條列
  const blocks = [];
  let bullets = [];
  const flush = () => { if (bullets.length) { blocks.push({ type: 'list', items: bullets, panel: true }); bullets = []; } };
  for (const raw of lines || []) {
    for (const l of String(raw ?? '').split('\n')) {
      if (/^・/.test(l)) { bullets.push(l.replace(/^・\s*/, '')); continue; }
      flush();
      if (l.trim()) blocks.push({ type: 'p', text: l });
    }
  }
  flush();
  if (steps) blocks.push({ type: 'steps', steps });
  else if (cta && cta.url) blocks.push({ type: 'button', url: cta.url, text: cta.text });
  return renderMailV2({
    subject, headline: subject, avatar, blocks,
    sign: ['祝 順心', 'Step1ne 德仁管理顧問　招募團隊 敬上'],
    line: { text: '面談過程有任何問題，或想直接找顧問聊聊，歡迎透過 LINE 與我們聯繫：', button: '加入 LINE 官方帳號' },
    footerBase: false,
    footer: ['德仁管理顧問有限公司（Step1ne）｜official@step1ne.com', '統一編號：85046127', '就業服務許可證：北市就服字第 0363 號',
      '地址：臺北市內湖區康寧路三段 54 之 7 號 3 樓', '這封信由系統自動發送，請勿直接回覆——有問題請走上面的 LINE 留言，會盡快回覆您。'],
  });
}

// 純文字信（顧問一對一的信：約電話、確認時間、婉拒、自由信件）→ 新版 HTML。
// 段落用空行分；「・」「- 」開頭的行是條列、「1. 」開頭的是編號條列；
// 最後一段如果是署名（Step1ne 德仁管理顧問…／Jacky…）就放到署名位置。字一字不改。
export function textToBlocks(text) {
  const paras = String(text || '').replace(/\r/g, '').split(/\n{2,}/).map((p) => p.replace(/^\n+|\n+$/g, '')).filter((p) => p.trim());
  let sign = [];
  if (paras.length > 1 && /^(Step1ne|德仁|Jacky|Phoebe)/.test(paras[paras.length - 1].trim())) sign = paras.pop().split('\n');
  const blocks = [];
  for (const p of paras) {
    const ls = p.split('\n');
    let buf = [];
    let list = null;
    const flushP = () => { if (buf.length) { blocks.push({ type: 'p', text: buf.join('\n') }); buf = []; } };
    const flushL = () => { if (list) { blocks.push(list); list = null; } };
    for (const l of ls) {
      const ul = l.match(/^\s*(?:・|-\s|•\s?)(.*)$/);
      const ol = l.match(/^\s*\d+[.、]\s*(.*)$/);
      if (ul || ol) {
        flushP();
        const ordered = !!ol && !ul;
        if (!list || list.ordered !== ordered) { flushL(); list = { type: 'list', ordered, items: [], panel: ordered }; }
        list.items.push((ul ? ul[1] : ol[1]).trim());
      } else { flushL(); buf.push(l); }
    }
    flushL(); flushP();
  }
  return { blocks, sign };
}

export function renderTextMailV2(subject, text, opts = {}) {
  const { blocks, sign } = textToBlocks(text);
  return renderMailV2({ subject, blocks, sign, ...opts });
}
