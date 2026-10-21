// 候選人信件新版設計（src/mail_v2.js）測試：開關 off／test／on、字不變、三個 Worker 的副本一模一樣。
//   node tests/mail_v2_test.mjs
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { mailV2For, mailV2Mode, renderSendMailV2, renderTextMailV2, textToBlocks } from '../src/mail_v2.js';

let fail = 0;
const check = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fail++; };
const here = path.dirname(fileURLToPath(import.meta.url));

// 三份副本要一樣
const mine = readFileSync(path.join(here, '../src/mail_v2.js'), 'utf8');
for (const repo of ['step1ne-public-worker', 'step1ne-backoffice-worker']) {
  const p = path.join(here, '../..', repo, 'src/mail_v2.js');
  if (existsSync(p)) check(readFileSync(p, 'utf8') === mine, `${repo}/src/mail_v2.js 跟這份一模一樣`);
  else console.log(`SKIP ${repo} 不在旁邊`);
}

// 假 D1：site_settings 一個值、applications 兩筆
function env(flag) {
  const apps = [{ id: 'a1', name: '王測試', email: 'test@x.com' }, { id: 'a2', name: '陳真人', email: 'real@x.com' }];
  return { DB: { prepare: (sql) => ({ bind: (...b) => ({ first: async () => {
    if (/site_settings/.test(sql)) return flag == null ? null : { value: flag };
    if (/WHERE id = \?/.test(sql)) return apps.find((a) => a.id === b[0]) || null;
    if (/lower\(email\)/.test(sql)) return apps.find((a) => a.email === String(b[0]).toLowerCase() && a.name.includes('測試')) ? { 1: 1 } : null;
    return null;
  } }) }) } };
}
const fresh = async (flag) => { const m = await import('../src/mail_v2.js?' + flag + Math.random()); return m; };

for (const [flag, who, want] of [
  [null, { name: '王測試 您好，' }, false], ['off', { name: '王測試' }, false],
  ['test', { name: '王測試 您好，' }, true], ['test', { name: '陳真人 您好，' }, false],
  ['test', { appId: 'a1' }, true], ['test', { appId: 'a2' }, false],
  ['test', { email: 'TEST@x.com' }, true], ['test', { email: 'real@x.com' }, false],
  ['on', { name: '陳真人' }, true], ['ON ', { appId: 'a2' }, true],
]) {
  const m = await fresh(flag);
  check(await m.mailV2For(env(flag), who) === want, `開關 ${JSON.stringify(flag)}、${JSON.stringify(who)} → ${want ? '新版' : '舊版'}`);
}

// 字不變：每一行、按鈕、網址都在，舊的米色不在
const lines = ['王測試 您好，', '應徵資料已經收到，目前可以直接開始跟 AI 顧問「阿財」線上面談。', '如果現在不方便，這封信可以先留著。'];
const h = renderSendMailV2('面談連結：業務專員', lines, { url: 'https://liff.line.me/x?t=1', text: '進入面談室' }, { avatar: true });
for (const l of lines) check(h.includes(l), `內文保留：${l.slice(0, 16)}…`);
check(h.includes('進入面談室') && h.includes('https://liff.line.me/x?t=1'), '按鈕字與網址都在');
check(!/f4f1ea|a67c3d/i.test(h), '沒有舊的米色／金棕色');
check(/max-width:600px/.test(h) && /#0F5F87/.test(h) && /#06C755/.test(h) && /#F4F6F8/.test(h), '新版顏色與 600px 寬');
check(h.includes('acai-avatar-192.png') && h.includes('step1ne-logo.png') && /height="28"/.test(h), '阿財頭像＋28px logo');
check(h.includes('北市就服字第 0363 號') && h.includes('official@step1ne.com'), '頁尾有就服許可證與 official@');
check(!/<script/i.test(h) && !/display:\s*flex/i.test(h), '信件安全：沒有 script、沒有 flex');

// 多步驟
const hs = renderSendMailV2('s', ['a'], [{ title: '第一步', body: 'b1', url: 'https://x/1', text: '開始測驗' }, { title: '第二步', url: 'https://x/2', text: '進入面談室' }]);
check(hs.includes('第一步') && hs.includes('第二步') && hs.includes('https://x/2'), '多步驟兩步都在');

// 純文字 → 版面：條列、編號、署名
const t = ['王測試 您好，', '我是 Step1ne 的顧問 Phoebe。', '', '電話中想跟您確認：', '・意願', '・待遇', '', '方便的話，直接回覆這封信，告訴我：', '1. 時段', '2. 電話', '', '收到後會打給您。', '', 'Step1ne 德仁管理顧問', '顧問：Phoebe', '就業服務許可證：北市就服字第 0363 號'].join('\n');
const tb = textToBlocks(t);
check(tb.sign.length === 3 && tb.sign[0] === 'Step1ne 德仁管理顧問', '最後一段是署名');
check(tb.blocks.some((b) => b.type === 'list' && !b.ordered && b.items.join() === '意願,待遇'), '「・」變條列');
check(tb.blocks.some((b) => b.type === 'list' && b.ordered && b.items.join() === '時段,電話'), '「1. 」變編號條列');
const th = renderTextMailV2('想跟您約個電話時間', t);
for (const w of ['我是 Step1ne 的顧問 Phoebe。', '電話中想跟您確認：', '意願', '時段', '收到後會打給您。', '顧問：Phoebe']) check(th.includes(w), `一對一信保留：${w}`);
check((th.match(/北市就服字第 0363 號/g) || []).length === 1, '署名有許可證時頁尾不重複');

console.log(fail ? `\n${fail} 項失敗` : '\n全部通過');
process.exit(fail ? 1 : 0);
