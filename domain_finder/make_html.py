#!/usr/bin/env python3
"""把 網域口袋名單.csv 轉成 網域口袋名單.html（卡片式，可篩選排序）。改完 CSV 重跑一次即可。"""
import csv, html, json, sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from df_paths import CSVF, HTMLF            # 資料（repo 外面）；正式看板是 step1ne.com/seo-report/#domains
rows = list(csv.DictReader(CSVF.open(encoding="utf-8-sig")))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


data = [{
    "d": r["網域"], "batch": r["批次"], "us": num(r["Ubersuggest權重分數"]), "rd": num(r["連入網站數"]),
    "bl": num(r["連結總數"]), "nf": num(r["nofollow連結數"]), "dr": num(r["Ahrefs DR"]),
    "free": r["可註冊(查核日)"], "gd": r["GoDaddy 購買"], "cf": r["Cloudflare 購買"], "wb": r["查前世"],
    "st": r["狀態"], "pl": r.get("前世", ""), "pcf": r.get("Cloudflare 價格", ""), "pgd": r.get("GoDaddy 價格", ""), "w": {"Cloudflare": "cf", "GoDaddy": "gd"}.get(r.get("購買平台", ""), ""),
} for r in rows]

PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>網域口袋名單</title>
<style>
:root{--got:#1c4fb8;--gotbg:#e6eeff;--bg:#f4f1ea;--card:#fffdf8;--ink:#1d1b16;--mute:#6f6a5e;--line:#ddd6c6;--pick:#0b6e4f;--pickbg:#e3f3ea;--warn:#a4530a;--low:#b3261e;--btn:#1d1b16}
@media (prefers-color-scheme:dark){:root{--got:#8fb4ff;--gotbg:#14233f;--bg:#15140f;--card:#1f1d17;--ink:#f0ece0;--mute:#a39c8b;--line:#38342a;--pick:#5fd0a3;--pickbg:#16302a;--warn:#f0a35b;--low:#ff8a80;--btn:#f0ece0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"PingFang TC","Noto Sans TC",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mute);font-size:13px;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.stat b{display:block;font-size:26px;line-height:1.1}
.stat span{color:var(--mute);font-size:12px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:16px}
.bar button,.bar select,.bar input{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:7px 14px;cursor:pointer}
.bar input{cursor:text;flex:1;min-width:150px}
.bar button.on{background:var(--btn);color:var(--bg);border-color:var(--btn)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.c{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column;gap:10px}
.c.pick{border-color:var(--pick);box-shadow:inset 0 0 0 1px var(--pick)}
.top{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.name{font-weight:700;font-size:16px;word-break:break-all}
.tag{font-size:11px;border-radius:999px;padding:2px 8px;white-space:nowrap;background:var(--line);color:var(--mute)}
.pick .tag{background:var(--pickbg);color:var(--pick)}
.nums{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px}
.n{border:1px solid var(--line);border-radius:10px;padding:6px 8px}
.n b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
.n span{font-size:11px;color:var(--mute)}
.n.dr b.ok{color:var(--pick)}.n.dr b.mid{color:var(--warn)}.n.dr b.low{color:var(--low)}.n.dr b.na{color:var(--mute);font-size:14px;line-height:1.75}
.meta{font-size:12px;color:var(--mute)}
.btns{display:flex;gap:6px;flex-wrap:wrap;margin-top:auto}
.btns a{flex:1;text-align:center;text-decoration:none;font-size:13px;border-radius:9px;padding:7px 6px;border:1px solid var(--line);color:var(--ink);white-space:nowrap}
.btns a.go{background:var(--btn);color:var(--bg);border-color:var(--btn)}
.view{margin-left:auto;display:flex;gap:0}
.view button{border-radius:0}.view button:first-child{border-radius:999px 0 0 999px}.view button:last-child{border-radius:0 999px 999px 0;border-left:0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
th{font-size:12px;color:var(--mute);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
td.r,th.r{text-align:right}
td.who{font-weight:700;white-space:nowrap}td.st{white-space:nowrap}
@media (min-width:761px) and (max-width:1250px){.hm{display:none}.prin{width:70px}.bought{font-size:11px;padding:1px 5px}th,td{padding:9px 8px}.wh button{padding:4px 7px}td.act a{margin-right:7px}}
tr.pick td.who{box-shadow:inset 3px 0 0 var(--pick)}
td.dr b.ok{color:var(--pick)}td.dr b.mid{color:var(--warn)}td.dr b.low{color:var(--low)}td.dr b.na{color:var(--mute);font-weight:400}
td.act{white-space:nowrap}
td.act a{font-size:13px;margin-right:10px;color:var(--ink)}
@media (max-width:760px){
 table,tbody,tr,td{display:block;width:100%}thead{display:none}
 table{border:0;background:none}
 tr{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:10px;padding:6px 0}
 tr.pick{border-color:var(--pick)}
 td,td.r{border:0;padding:5px 14px;text-align:left;display:flex;justify-content:space-between;gap:12px}
 td::before{content:attr(data-l);color:var(--mute);font-size:12px}
 td.who{font-size:16px;box-shadow:none!important;white-space:normal;word-break:break-all}td.who::before{content:none}
 .view{margin-left:0}
}
.prin{width:104px;font:inherit;font-size:13px;background:transparent;color:var(--ink);border:1px dashed var(--line);border-radius:7px;padding:3px 6px}
.prin:focus{outline:2px solid var(--pick);border-style:solid}
.prin::placeholder{color:var(--mute);font-size:12px}
.prices{display:grid;grid-template-columns:1fr 1fr;gap:6px}.prices .prin{width:100%}
.prices label{font-size:11px;color:var(--mute);display:flex;flex-direction:column;gap:2px}
.drin{width:64px;font:inherit;font-weight:700;font-size:16px;text-align:right;background:transparent;color:var(--ink);border:1px dashed var(--line);border-radius:7px;padding:2px 6px;font-variant-numeric:tabular-nums}
.drin:focus{outline:2px solid var(--pick);border-style:solid}
.drin.ok{color:var(--pick)}.drin.mid{color:var(--warn)}.drin.low{color:var(--low)}
.drin::placeholder{font-weight:400;font-size:13px;color:var(--mute)}
.n.dr .drin{width:100%;text-align:left;font-size:19px}
.save{font-size:12px;color:var(--mute);margin:-8px 0 14px}
.save button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer;margin-left:6px}
.bought{font-size:12px;font-weight:700;background:var(--got);color:var(--bg);border-radius:6px;padding:2px 8px;flex:none;white-space:nowrap}
tr.got td{background:var(--gotbg)}tr.got td.who{box-shadow:inset 4px 0 0 var(--got)}tr.got td.st{color:var(--got);font-weight:700}
.c.got{border-color:var(--got);background:var(--gotbg)}.c.got .tag{background:var(--got);color:var(--bg)}
.stat.got{border-color:var(--got)}.stat.got b{color:var(--got)}
.ck{width:20px;height:20px;accent-color:var(--pick);cursor:pointer;flex:none;margin:0}
label.pk{display:flex;align-items:center;gap:10px;cursor:pointer}
.tot{background:var(--card);border:1px solid var(--pick);border-radius:12px;padding:12px 14px;margin-bottom:16px;display:flex;flex-wrap:wrap;gap:8px 28px;align-items:baseline}
.tot h2{font-size:14px;margin:0;width:100%}
.tot div b{font-size:20px;font-variant-numeric:tabular-nums}
.tot div span{display:block;font-size:12px;color:var(--mute)}
.tot .rate{font-size:12px;color:var(--mute);width:100%}
.tot .rate input{font:inherit;width:70px;background:transparent;color:var(--ink);border:1px dashed var(--line);border-radius:7px;padding:2px 6px}
.wh{display:inline-flex;white-space:nowrap}
.wh button{font:inherit;font-size:12px;border:1px solid var(--line);background:transparent;color:var(--mute);padding:4px 9px;cursor:pointer}
.wh button:first-child{border-radius:999px 0 0 999px}.wh button:last-child{border-radius:0 999px 999px 0;border-left:0}
.wh button.on{background:var(--pick);border-color:var(--pick);color:var(--bg);font-weight:700}
.sec{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin:22px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--pick)}
.sec:first-child{margin-top:0}.sec h2{font-size:17px;margin:0}.sec span{color:var(--mute);font-size:13px}.sec i{font-style:normal}.sec b{font-size:17px;margin-left:auto;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;border-radius:12px}
@media (max-width:760px){.tw{overflow:visible}}
.pl{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:1px 7px;white-space:nowrap}
.pl.ok{background:var(--pickbg);color:var(--pick)}.pl.no{background:#fde8e6;color:var(--low)}.pl.q{background:var(--line);color:var(--mute)}
@media (prefers-color-scheme:dark){.pl.no{background:#3b1a17}}
td.plc{white-space:normal;min-width:120px;max-width:230px;font-size:12.5px;color:var(--mute);line-height:1.45}
.empty{color:var(--mute);padding:30px 0;text-align:center}
</style></head><body><div class="wrap">
<h1>網域口袋名單</h1>
<div class="sub">全部是 .com、目前無人註冊，可在 GoDaddy 或 Cloudflare 用註冊價直接買。更新日 __DATE__。DR 是你自己到 Ahrefs 查的數字；「參考分數」是 Ubersuggest 的權重分數，兩者不能互換。</div>
<div class="stats" id="stats"></div>
<div class="tot"><h2>預計購買的總額</h2><span id="tot" style="display:contents"></span>
<div class="rate">1 澳幣 = <input id="rate" type="number" step="0.001" min="0" inputmode="decimal" placeholder="例 0.65"> 美元（填了才會把澳幣換成美元算「挑便宜的買」）</div></div>
<div class="bar">
<button data-f="all" class="on">全部</button><button data-f="dr10">DR 10 以上</button><button data-f="pick">預計購買</button><button data-f="bought">已購買</button><button data-f="nodr">還沒查 DR</button>
<select id="sort"><option value="dr">依 DR 高到低</option><option value="us">依參考分數高到低</option><option value="rd">依連入網站數多到少</option><option value="d">依名稱</option></select>
<input id="q" placeholder="搜尋網域…">
<span class="view"><button data-v="table">表格</button><button data-v="card">卡片</button></span>
</div>
<div class="save" id="save"></div>
<div id="grid"></div>
</div>
<script>
const D=__DATA__;
const fmt=v=>v==null?"—":v.toLocaleString("en-US");
const F={pick:x=>x.st==="預計購買",bought:x=>x.st==="已買",dr10:x=>x.dr!=null&&x.dr>=10,nodr:x=>x.dr==null,all:x=>true};
let f="all",v="table";try{v=localStorage.getItem("dv")||"table"}catch(e){}
let mine={};try{mine=JSON.parse(localStorage.getItem("mydr")||"{}")}catch(e){}
let myprice={};try{myprice=JSON.parse(localStorage.getItem("myprice")||"{}")}catch(e){}
const esc=t=>String(t??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const prin=(x,k)=>`<input class="prin" data-d="${x.d}" data-k="${k}" placeholder="填價格" value="${esc(x[k])}">`;
let mywhere={};try{mywhere=JSON.parse(localStorage.getItem("mywhere")||"{}")}catch(e){}
const wh=x=>`<span class="wh" data-d="${x.d}"><button data-w="cf" class="${x.w==="cf"?"on":""}">Cloudflare</button><button data-w="gd" class="${x.w==="gd"?"on":""}">GoDaddy</button></span>`;
let mypick={};try{mypick=JSON.parse(localStorage.getItem("mypick2")||"{}")}catch(e){}
D.forEach(x=>{x.csv=x.dr;if(mine[x.d]===x.dr)delete mine[x.d];if(x.d in mine)x.dr=mine[x.d];x.csvst=x.st;x.cw=x.w;if(x.d in mywhere)x.w=mywhere[x.d];const mp=myprice[x.d]||{};x.cpcf=x.pcf;x.cpgd=x.pgd;if("pcf" in mp)x.pcf=mp.pcf;if("pgd" in mp)x.pgd=mp.pgd;if(x.d in mypick&&x.st!=="已買")x.st=mypick[x.d]?"預計購買":"未買"});
const plH=(x,full)=>{if(!x.pl)return full?"":`<span class="pl q">沒查</span>`;const [v,...r]=x.pl.split("｜");const c=v==="乾淨"?"ok":(v==="查不到"?"q":"no");return `<span class="pl ${c}">${esc(v)}</span>`+(r.length?` <span>${esc(r.join("；"))}</span>`:"")};
const ck=x=>x.st==="已買"?`<span class="bought">✓ 已購買</span>`:`<input type="checkbox" class="ck" data-d="${x.d}" ${x.st==="預計購買"?"checked":""} aria-label="預計購買 ${x.d}">`;
const dcls=v=>v==null?"na":v>=10?"ok":v>=5?"mid":"low";
const drin=x=>`<input class="drin ${dcls(x.dr)}" type="number" step="0.1" min="0" max="100" inputmode="decimal" placeholder="填 DR" data-d="${x.d}" value="${x.dr??""}">`;
const pp=t=>{const m=String(t||"").replace(/,/g,"").match(/(\\d+(?:\\.\\d+)?)\\s*([A-Za-z]{3})?/);return m?{v:parseFloat(m[1]),c:(m[2]||"USD").toUpperCase()}:null};
const money=o=>Object.keys(o).length?Object.entries(o).map(([c,v])=>v.toFixed(2)+" "+c).join(" + "):"—";
let rate=null;try{rate=parseFloat(localStorage.getItem("audusd"))||null}catch(e){}
function totals(){const P=D.filter(F.pick),cf={},gd={};let ncf=0,ngd=0,best=0,nb=0,miss=0;
 P.forEach(x=>{const a=pp(x.pcf),b=pp(x.pgd);
  if(a){cf[a.c]=(cf[a.c]||0)+a.v;ncf++}if(b){gd[b.c]=(gd[b.c]||0)+b.v;ngd++}
  const u=o=>!o?null:o.c==="USD"?o.v:o.c==="AUD"&&rate?o.v*rate:null,c=[u(a),u(b)].filter(v=>v!=null);
  if(c.length){best+=Math.min(...c);nb++}else miss++});
 const ch={};let nch=0,nsel=0;P.forEach(x=>{if(!x.w)return;nsel++;const o=pp(x.w==="cf"?x.pcf:x.pgd);if(o){ch[o.c]=(ch[o.c]||0)+o.v;nch++}});
 document.getElementById("tot").innerHTML=`<div><b>${money(ch)}</b><span>照你選的平台買（已選 ${nsel}/${P.length} 個，其中 ${nch} 個有價格）</span></div><div><b>${money(cf)}</b><span>全在 Cloudflare 買（已填 ${ncf}/${P.length} 個）</span></div><div><b>${money(gd)}</b><span>全在 GoDaddy 買（已填 ${ngd}/${P.length} 個）</span></div><div><b>${nb?best.toFixed(2)+" USD":"—"}</b><span>每個挑便宜的那家（算得出 ${nb}/${P.length} 個${miss?"，"+miss+" 個缺價格或缺匯率":""}）</span></div>`}
function stats(){totals();const picks=D.filter(F.pick);
const n=Object.keys(mine).length+Object.keys(mypick).length+Object.keys(myprice).length+Object.keys(mywhere).length;
document.getElementById("save").innerHTML=n?`你在這頁改了 ${n} 處（勾選、DR、價格或購買平台），已存在這個瀏覽器裡。<button id="cp">複製給 Claude</button><button id="dl">下載成 CSV</button>`:"打勾＝預計購買；DR 欄可以直接點進去填。兩者都會自動存在這個瀏覽器裡。";
document.getElementById("stats").innerHTML=[
 [D.length,"名單總數"],[D.filter(F.bought).length,"已購買","got"],[picks.length,"預計購買"],[D.filter(x=>x.dr!=null).length,"已查過 DR"],[D.filter(F.dr10).length,"DR 10 以上"],[D.filter(x=>x.st==="預計購買"&&x.dr==null).length,"預計購買但 DR 未填"]
].map(([n,l,c])=>`<div class="stat ${c||""}"><b>${n}</b><span>${l}</span></div>`).join("");}
stats();
function sub(list,k){const o={};let n=0;list.forEach(x=>{const m=pp(x[k]);if(m){o[m.c]=(o[m.c]||0)+m.v;n++}});return{t:money(o),n}}
function rowH(x){const nf=x.bl?Math.round(100*x.nf/x.bl)+"%":"—";
 return `<tr class="${x.st==="預計購買"?"pick":x.st==="已買"?"got":""}"><td class="who"><label class="pk">${ck(x)}<span>${x.d}</span></label></td><td class="r dr" data-l="Ahrefs DR">${drin(x)}</td><td class="r hm" data-l="參考分數">${fmt(x.us)}</td><td class="r" data-l="連入網站">${fmt(x.rd)}</td><td class="r hm" data-l="連結數">${fmt(x.bl)}</td><td class="r hm" data-l="nofollow">${nf}</td><td data-l="前世" class="plc">${plH(x)}</td><td data-l="狀態" class="st hm">${x.st==="已買"?"已購買":x.st}</td><td data-l="Cloudflare 價">${prin(x,"pcf")}</td><td data-l="GoDaddy 價">${prin(x,"pgd")}</td><td data-l="在哪買">${wh(x)}</td><td class="act" data-l=""><a href="${x.gd}" target="_blank" rel="noopener">GoDaddy</a><a href="${x.cf}" target="_blank" rel="noopener">Cloudflare</a><a href="${x.wb}" target="_blank" rel="noopener">前世</a></td></tr>`}
function cardH(x){const nf=x.bl?Math.round(100*x.nf/x.bl):null;
 return `<div class="c ${x.st==="預計購買"?"pick":x.st==="已買"?"got":""}"><div class="top"><label class="pk name">${ck(x)}<span>${x.d}</span></label><span class="tag st">${x.st==="已買"?"已購買":x.st}</span></div>
  <div class="nums"><div class="n dr">${drin(x)}<span>Ahrefs DR</span></div><div class="n"><b>${fmt(x.us)}</b><span>參考分數</span></div><div class="n"><b>${fmt(x.rd)}</b><span>連入網站</span></div></div>
  <div class="meta">連結 ${fmt(x.bl)} 條${nf==null?"":"，nofollow 約 "+nf+"%"}　·　${x.batch}　·　可註冊：${x.free}</div>
  <div class="meta">前世　${plH(x)}</div>
  <div class="prices"><label>Cloudflare 價${prin(x,"pcf")}</label><label>GoDaddy 價${prin(x,"pgd")}</label></div>
  <div class="meta">在哪買　${wh(x)}</div>
  <div class="btns"><a class="go" href="${x.gd}" target="_blank" rel="noopener">GoDaddy 買</a><a href="${x.cf}" target="_blank" rel="noopener">Cloudflare 買</a><a href="${x.wb}" target="_blank" rel="noopener">查前世</a></div></div>`}
function listH(r){return v==="table"?`<div class="tw"><table><thead><tr><th>網域</th><th class="r">Ahrefs DR</th><th class="r hm">參考分數</th><th class="r">連入網站</th><th class="r hm">連結數</th><th class="r hm">nofollow</th><th>前世</th><th class="hm">狀態</th><th>Cloudflare 價</th><th>GoDaddy 價</th><th>在哪買</th><th>購買 / 查前世</th></tr></thead><tbody>${r.map(rowH).join("")}</tbody></table></div>`:`<div class="grid">${r.map(cardH).join("")}</div>`}
function draw(){
 const q=document.getElementById("q").value.trim().toLowerCase(),s=document.getElementById("sort").value;
 const r=D.filter(F[f]).filter(x=>!q||x.d.includes(q));
 r.sort((a,b)=>s==="d"?a.d.localeCompare(b.d):((b[s]??-1)-(a[s]??-1))||((b.rd??0)-(a.rd??0)));
 const g=document.getElementById("grid");g.className="";
 document.querySelectorAll(".view button").forEach(b=>b.classList.toggle("on",b.dataset.v===v));
 if(!r.length){g.innerHTML='<div class="empty">沒有符合的網域</div>';return}
 if(f==="all"&&!q){const got=r.filter(F.bought),rest=r.filter(x=>x.st!=="已買");
  const spent=sub(got,"pcf");
  g.innerHTML=(got.length?`<div class="sec" style="border-color:var(--got)"><h2>已購買</h2><span>${got.length} 個</span><b>已花 ${spent.t}</b></div>`+listH(got):"")+`<div class="sec"><h2>還沒買</h2><span>${rest.length} 個</span></div>`+listH(rest);return}
 if(f!=="pick"){g.innerHTML=listH(r);return}
 g.innerHTML=[["cf","在 Cloudflare 買","pcf"],["gd","在 GoDaddy 買","pgd"],["","還沒選在哪買",null]].map(([w,t,k])=>{
  const l=r.filter(x=>x.w===w);if(!l.length&&!w)return"";const u=k?sub(l,k):null;
  return `<div class="sec" data-w="${w}" data-k="${k||""}"><h2>${t}</h2><span>${l.length} 個${u?"，<i>"+u.n+"</i> 個有價格":"　點每列的 Cloudflare / GoDaddy 來分配"}</span>${u?"<b>小計 "+u.t+"</b>":""}</div>`+(l.length?listH(l):'<div class="empty">這一區還沒有網域</div>')}).join("");
}
document.querySelectorAll(".bar button[data-f]").forEach(b=>b.onclick=()=>{f=b.dataset.f;document.querySelectorAll(".bar button[data-f]").forEach(x=>x.classList.toggle("on",x===b));draw()});
document.querySelectorAll(".view button").forEach(b=>b.onclick=()=>{v=b.dataset.v;try{localStorage.setItem("dv",v)}catch(e){}draw()});
document.getElementById("grid").addEventListener("click",e=>{
 const b=e.target.closest(".wh button");if(!b)return;
 const x=D.find(y=>y.d===b.parentNode.dataset.d);x.w=x.w===b.dataset.w?"":b.dataset.w;
 if(x.w===x.cw)delete mywhere[x.d];else mywhere[x.d]=x.w;
 try{localStorage.setItem("mywhere",JSON.stringify(mywhere))}catch(err){alert("這個瀏覽器不允許儲存，請用「複製給 Claude」帶走")}
 const y=window.scrollY;draw();stats();window.scrollTo(0,y);
});
document.getElementById("grid").addEventListener("change",e=>{
 const i=e.target;
 if(i.classList.contains("ck")){const x=D.find(y=>y.d===i.dataset.d);x.st=i.checked?"預計購買":"未買";
  if(x.st===x.csvst)delete mypick[x.d];else mypick[x.d]=i.checked;
  try{localStorage.setItem("mypick2",JSON.stringify(mypick))}catch(err){alert("這個瀏覽器不允許儲存，請用「複製給 Claude」帶走")}
  const box=i.closest("tr,.c");box.classList.toggle("pick",i.checked);const tg=box.querySelector(".st");if(tg)tg.textContent=x.st;stats();if(f==="pick"){const y=window.scrollY;draw();window.scrollTo(0,y)}return}
 if(i.classList.contains("prin")){const x=D.find(y=>y.d===i.dataset.d),k=i.dataset.k;x[k]=i.value.trim();
  const mp=myprice[x.d]||{};if(x[k]===x["c"+k])delete mp[k];else mp[k]=x[k];
  if(Object.keys(mp).length)myprice[x.d]=mp;else delete myprice[x.d];
  try{localStorage.setItem("myprice",JSON.stringify(myprice))}catch(err){alert("這個瀏覽器不允許儲存，請用「複製給 Claude」帶走")}
  stats();document.querySelectorAll(".sec[data-k]").forEach(h=>{if(!h.dataset.k)return;const u=sub(D.filter(F.pick).filter(y=>y.w===h.dataset.w),h.dataset.k);h.querySelector("b").textContent="小計 "+u.t;h.querySelector("i").textContent=u.n});return}
 if(!i.classList.contains("drin"))return;
 const x=D.find(y=>y.d===i.dataset.d),val=i.value.trim()===""?null:Math.max(0,Math.min(100,parseFloat(i.value)));
 x.dr=Number.isNaN(val)?null:val;
 if(x.dr===x.csv)delete mine[x.d];else mine[x.d]=x.dr;
 try{localStorage.setItem("mydr",JSON.stringify(mine))}catch(err){alert("這個瀏覽器不允許儲存，請用「複製給 Claude」把數字帶走")}
 i.className="drin "+dcls(x.dr);stats();
});
document.getElementById("save").addEventListener("click",e=>{
 if(e.target.id==="cp"){const t=Object.entries(mine).map(([d,v])=>d+" dr "+(v??"清空")).concat(Object.entries(mypick).map(([d,v])=>d+(v?" 預計購買":" 取消預計購買"))).concat(Object.entries(mywhere).map(([d,w])=>d+(w==="cf"?" 在 cloudflare 買":w==="gd"?" 在 godaddy 買":" 取消購買平台"))).concat(Object.entries(myprice).flatMap(([d,m])=>Object.entries(m).map(([k,v])=>d+(k==="pcf"?" cloudflare ":" godaddy ")+(v||"清空")))).join("\\n");navigator.clipboard.writeText(t).then(()=>{e.target.textContent="已複製"},()=>prompt("複製下面這段",t))}
 if(e.target.id==="dl"){const q=v=>'"'+String(v??"").replace(/"/g,'""')+'"';
  const t="\\ufeff網域,Ahrefs DR,Ubersuggest權重分數,連入網站數,連結總數,nofollow連結數,狀態\\n"+D.map(x=>[x.d,x.dr,x.us,x.rd,x.bl,x.nf,x.st].map(q).join(",")).join("\\n");
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([t],{type:"text/csv"}));a.download="網域口袋名單_含我填的DR.csv";a.click()}
});
const ri=document.getElementById("rate");if(rate)ri.value=rate;
ri.oninput=()=>{rate=parseFloat(ri.value)||null;try{rate?localStorage.setItem("audusd",rate):localStorage.removeItem("audusd")}catch(e){}totals()};
document.getElementById("sort").onchange=draw;document.getElementById("q").oninput=draw;draw();
</script></body></html>"""

out = HTMLF
out.write_text(PAGE.replace("__DATE__", str(date.today())).replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")), encoding="utf-8")
print(f"{len(data)} 筆 → {out}")
