// Step1ne 帶案儀表板：通行碼門禁＋單頁。改內容：重跑 build 後重新產 worker.js 再 wrangler deploy
import HTML from "./index.html";
// 通行碼門禁：輸對一次記 30 天 cookie（cookie 存的是通行碼雜湊，不是明碼）
async function sha(s){const d=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join('')}
const page=(err)=>new Response(`<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>Step1ne 帶案儀表板</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#F3F6F7;color:#13232B;font-family:system-ui,"Noto Sans TC",sans-serif;padding:16px}form{background:#fff;border:1px solid #D7E0E4;border-radius:14px;padding:24px;width:min(340px,100%);display:grid;gap:12px}h1{font-size:20px;margin:0}input,button{font:inherit;font-size:18px;min-height:48px;border-radius:10px;padding:0 14px}input{border:1px solid #D7E0E4}button{border:0;background:#0F6B78;color:#fff;font-weight:700}p{margin:0;color:#B5621B;font-size:14px}</style></head>
<body><form method="post"><h1>Step1ne 帶案儀表板</h1><input name="p" type="password" inputmode="numeric" autocomplete="off" placeholder="輸入通行碼" required autofocus>${err?'<p>通行碼不對，再試一次</p>':''}<button>進入</button></form></body></html>`,{status:401,headers:{'content-type':'text/html;charset=utf-8','x-robots-tag':'noindex','cache-control':'no-store'}});
async function handle(request,env){
  const want=await sha(env.DASH_PASS||'');
  if(request.method==='POST'){const f=await request.formData();
    if(await sha(String(f.get('p')||''))===want) return new Response(null,{status:303,headers:{location:new URL(request.url).pathname,'set-cookie':`pd=${want}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax`}});
    return page(true)}
  const ck=(request.headers.get('cookie')||'').match(/(?:^|;\s*)pd=([a-f0-9]+)/);
  if(!env.DASH_PASS||!ck||ck[1]!==want) return page(false);
  return new Response(HTML,{headers:{'content-type':'text/html;charset=utf-8','x-robots-tag':'noindex','cache-control':'no-store'}});
}

export default {async fetch(request,env){const u=new URL(request.url);
  if(u.pathname==='/robots.txt') return new Response('User-agent: *\nDisallow: /\n',{headers:{'content-type':'text/plain'}});
  return handle(request,env)}};
