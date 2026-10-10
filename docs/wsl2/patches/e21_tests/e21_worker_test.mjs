// E21 Worker 補丁測試：用假的 TG update 餵 /telegram/webhook，檢查有沒有寫 ai_jobs、回了什麼話。
// 不連正式 D1／Telegram（全部假的），不用真錄音。執行：node tests/e21_worker_test.mjs
import worker from '../src/index.js';

const sent = [];            // 假 Telegram 收到的呼叫
const inserted = [];        // 假 D1 收到的 ai_jobs 寫入
globalThis.fetch = async (url, init) => {
  sent.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
  return new Response(JSON.stringify({ ok: true, result: {} }), { headers: { 'content-type': 'application/json' } });
};
function mkEnv(routes) {
  return {
    TG_BOT_TOKEN: 'TEST', TG_CHAT_ID: '-100', ADMIN_TOKEN: 'x',
    DB: {
      prepare(sql) {
        let args = [];
        const st = {
          bind: (...a) => { args = a; return st; },
          first: async () => {
            if (/FROM tg_routes WHERE key/.test(sql)) { const r = routes[args[0]]; return r ? { chat_id: r.chat_id, thread_id: r.thread_id } : null; }
            return null;
          },
          all: async () => ({ results: [] }),
          run: async () => { if (/INSERT INTO ai_jobs/.test(sql)) inserted.push({ sql, args }); return {}; },
        };
        return st;
      },
    },
  };
}
const ROUTES = { call_audio_intake: { chat_id: '-1003', thread_id: 77 } };
const post = (env, update) => worker.fetch(new Request('https://x/telegram/webhook', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(update) }), env, { waitUntil() {} });
const base = (over) => ({ update_id: 1, message: { message_id: 10, chat: { id: -1003, type: 'supergroup' }, message_thread_id: 77, from: { id: 5, username: 'jackyyuqi', first_name: 'J' }, ...over } });
const texts = () => sent.filter((s) => /sendMessage/.test(s.url)).map((s) => s.body.text);
let fails = 0;
const check = (name, cond, detail = '') => { console.log((cond ? '  ✅ ' : '  ❌ ') + name + (detail ? `  ｜${detail}` : '')); if (!cond) fails++; };
const reset = () => { sent.length = 0; inserted.length = 0; };

// 1 voice + caption → 排隊
reset();
let r = await post(mkEnv(ROUTES), base({ voice: { file_id: 'FID1', file_size: 800000, mime_type: 'audio/ogg', duration: 120 }, caption: '林小安' }));
check('voice＋人選名字 → 寫一筆 ai_jobs(call_audio)', inserted.length === 1 && /'call_audio'/.test(inserted[0].sql));
const pl = inserted[0] && JSON.parse(inserted[0].args[1]);
check('payload：file_id、caption、chat、thread、誰傳的', pl && pl.file_id === 'FID1' && pl.caption === '林小安' && pl.chat_id === '-1003' && pl.thread_id === 77 && pl.from.name === 'Jacky' && pl.from.username === 'jackyyuqi', JSON.stringify(pl));
check('payload 沒有音檔內容（只有 file_id）', !JSON.stringify(pl).includes('base64') && JSON.stringify(inserted[0].args).length < 800);
check('回「收到錄音，轉文字中」', texts().length === 1 && /收到「林小安」的錄音.*轉文字中/.test(texts()[0]), texts()[0]);
check('沒有下載檔案（Worker 不碰音檔）', !sent.some((s) => /getFile|\/file\//.test(s.url)));

// 2 document m4a（iPhone 錄音）
reset();
await post(mkEnv(ROUTES), base({ document: { file_id: 'FID2', file_size: 5000000, mime_type: 'audio/x-m4a', file_name: '新錄音.m4a' }, caption: '陳大華' }));
check('document audio/x-m4a → 排隊', inserted.length === 1 && JSON.parse(inserted[0].args[1]).file_id === 'FID2');
// 3 audio
reset(); await post(mkEnv(ROUTES), base({ audio: { file_id: 'FID3', file_size: 100, mime_type: 'audio/mpeg' }, caption: 'abc' }));
check('audio 也排隊', inserted.length === 1);
// 4 > 20MB
reset(); await post(mkEnv(ROUTES), base({ voice: { file_id: 'BIG', file_size: 25 * 1048576 }, caption: '林小安' }));
check('> 20MB → 不排隊、請改傳較短的段落', inserted.length === 0 && /太大.*20MB.*較短/s.test(texts()[0] || ''), texts()[0]);
// 5 沒打人選名字
reset(); await post(mkEnv(ROUTES), base({ voice: { file_id: 'F5', file_size: 100 } }));
check('沒有說明欄 → 不排隊、提醒打名字', inserted.length === 0 && /說明.*人選的名字/.test(texts()[0] || ''));
// 6 不是 Jacky／Phoebe
reset(); await post(mkEnv(ROUTES), base({ from: { id: 9, username: 'someoneelse' }, voice: { file_id: 'F6', file_size: 100 }, caption: '林小安' }));
check('別人傳 → 不排隊、只回只有 Jacky／Phoebe', inserted.length === 0 && /只有 Jacky、Phoebe/.test(texts()[0] || ''));
// 7 Phoebe
reset(); await post(mkEnv(ROUTES), base({ from: { id: 6, username: 'BeHe10' }, voice: { file_id: 'F7', file_size: 100 }, caption: '林小安' }));
check('Phoebe（大小寫不拘）→ 排隊且 from.name=Phoebe', inserted.length === 1 && JSON.parse(inserted[0].args[1]).from.name === 'Phoebe');
// 8 匿名發言
reset(); await post(mkEnv(ROUTES), base({ sender_chat: { id: -1003 }, from: { id: 1087968824, username: 'GroupAnonymousBot', is_bot: true }, voice: { file_id: 'F8', file_size: 100 }, caption: '林小安' }));
check('群組匿名發言 → 當 Jacky 排隊', inserted.length === 1 && JSON.parse(inserted[0].args[1]).from.name === 'Jacky');
// 9 別的主題
reset(); await post(mkEnv(ROUTES), base({ message_thread_id: 999, voice: { file_id: 'F9', file_size: 100 }, caption: '林小安' }));
check('別的主題傳的 → 完全不處理', inserted.length === 0 && texts().length === 0);
// 10 沒設路由
reset(); await post(mkEnv({}), base({ voice: { file_id: 'F10', file_size: 100 }, caption: '林小安' }));
check('tg_routes 沒設 → 完全不處理（不影響其他功能）', inserted.length === 0 && texts().length === 0);
// 11 一般文字訊息
reset(); await post(mkEnv(ROUTES), base({ text: '你好' }));
check('一般文字訊息 → 不排隊', inserted.length === 0);
// 12 機器人自己
reset(); await post(mkEnv(ROUTES), base({ from: { id: 7, username: 'somebot', is_bot: true }, voice: { file_id: 'F12', file_size: 100 }, caption: '林小安' }));
check('機器人的訊息 → 不處理', inserted.length === 0);
// 13 consultant_assistant 備援路由
reset(); await post(mkEnv({ consultant_assistant: { chat_id: '-1003', thread_id: 77 } }), base({ voice: { file_id: 'F13', file_size: 100 }, caption: '林小安' }));
check('沒有 call_audio_intake 時改用 consultant_assistant 路由', inserted.length === 1);

console.log('\n失敗 ' + fails + ' 項');
process.exit(fails ? 1 : 0);
