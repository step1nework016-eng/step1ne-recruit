<!DOCTYPE html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-DNPMMRDEC0"></script>
<script>
window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', 'G-DNPMMRDEC0');
// 全站唯一的 CTA 是 LINE 連結，把點擊送成事件才知道哪一頁真的帶得到人。
// 用事件委派掛在 document 上，之後新增頁面或改版都不用再動這段。
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[href*="lin.ee"]');
  if (!a) return;
  gtag('event', 'line_cta_click', {
    page_path: location.pathname,
    link_text: (a.textContent || '').trim().slice(0, 60)
  });
});
</script>

<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="keywords" content="{keywords}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="https://step1ne.com/jobs/{slug}/">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="icon" type="image/png" sizes="512x512" href="/assets/favicon-512.png">
<link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Step1ne 德仁管理顧問">
<meta property="og:title" content="{og_title}">
<meta property="og:description" content="{og_desc}">
<meta property="og:url" content="https://step1ne.com/jobs/{slug}/">
<meta property="og:locale" content="zh_TW">
<meta property="og:image" content="https://step1ne.com/assets/og-image.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://step1ne.com/assets/og-image.jpg">
<meta name="theme-color" content="#f4f1ea">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500;1,600&family=Noto+Sans+TC:wght@300;400;500;700;900&family=Noto+Serif+TC:wght@500;600;700;900&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">

<script type="application/ld+json">
{jsonld}
</script>

<style>
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{font-family:'Noto Sans TC',system-ui,sans-serif;color:#23262d;background:#f4f1ea;-webkit-font-smoothing:antialiased;line-height:1.7;}
a{color:#a67c3d;text-decoration:none;}
a:hover{color:#8a6531;}
details summary{cursor:pointer;list-style:none;}
details summary::-webkit-details-marker{display:none;}
details summary .faq-icon{transition:transform .3s ease;}
details[open] summary .faq-icon{transform:rotate(45deg);}
.wrap{max-width:800px;margin:0 auto;padding:0 24px;}
.tag{display:inline-block;font-size:12.5px;font-weight:700;color:#8a5a1a;background:#f3e2c2;padding:5px 12px;border-radius:999px;}
.spec{margin:0;background:#fff;border:1px solid #e6dfd1;border-radius:16px;overflow:hidden;}
.spec-row{display:grid;grid-template-columns:132px 1fr;gap:18px;padding:16px 22px;border-bottom:1px solid #f0ebe0;}
.spec-row:last-child{border-bottom:none;}
.spec-row dt{font-size:14px;font-weight:700;color:#8a8d95;margin:0;}
.spec-row dd{margin:0;font-size:15px;color:#23262d;}
.spec-row.highlight{background:#fdfaf3;}
.spec-row.highlight dd{font-weight:700;color:#8a5a1a;font-size:16.5px;}
@media (max-width:600px){
  .spec-row{grid-template-columns:1fr;gap:4px;padding:14px 18px;}
  .spec-row dt{font-size:12.5px;letter-spacing:0.5px;}
}
h2{font-family:'Noto Serif TC',serif;font-size:24px;font-weight:700;color:#1c1f26;margin:44px 0 16px;}
h3{font-size:16.5px;font-weight:700;color:#1c1f26;margin:22px 0 8px;}
ul.duties{padding-left:20px;margin:0;color:#3a3d44;font-size:15px;}
ul.duties li{margin-bottom:10px;}
@media (max-width:760px){
  .s1ne-hide-m{display:none !important;}
  .s1ne-show-m{display:flex !important;}
  footer a{min-height:44px;display:inline-flex;align-items:center;}
}
.s1ne-show-m{display:none;}
.s1ne-mobile-menu{display:none;flex-direction:column;padding:4px 24px 16px;border-top:1px solid #e6dfd1;background:rgba(244,241,234,0.98);}
.s1ne-mobile-menu.is-open{display:flex;}
.s1ne-mobile-menu a{padding:10px 0;color:#4a4d55;font-size:15px;border-bottom:1px solid #e6dfd1;min-height:44px;display:flex;align-items:center;}
.s1ne-mobile-menu a:last-child{border-bottom:none;}
.h1-sub{display:block;font-size:0.62em;font-weight:600;color:#6b6e77;margin-top:6px;letter-spacing:0;}
</style>
</head>
<body>

<header style="position:sticky;top:0;z-index:50;backdrop-filter:blur(14px);background:rgba(244,241,234,0.9);border-bottom:1px solid #e6dfd1;">
  <div class="wrap" style="display:flex;align-items:center;justify-content:space-between;gap:20px;height:68px;max-width:1180px;">
    <a href="/" style="display:flex;align-items:center;gap:12px;color:#23262d;">
      <img src="/assets/step1ne-logo.png" alt="Step1ne 德仁管理顧問" style="height:38px;width:auto;">
    </a>
    <div class="s1ne-hide-m" style="display:flex;align-items:center;gap:28px;font-size:15px;color:#4a4d55;">
      <a href="/jobs/" style="color:#4a4d55;">職缺專區</a>
      <a href="/articles/" style="color:#4a4d55;">獵才專欄</a>
      <a href="/talent/" style="color:#4a4d55;">全民獵才</a>
      <a href="/talent/ai-tools/" style="color:#4a4d55;">AI求職工具</a>
    </div>
    <a href="/apply/?job={slug}&title={title_enc}&utm_source=website&utm_medium=job_page&utm_campaign={slug}" class="s1ne-hide-m" style="background:linear-gradient(135deg,#c9a049,#a67c3d);color:#fff;font-weight:700;font-size:13.5px;padding:9px 18px;border-radius:999px;white-space:nowrap;">我要應徵</a>
    <button type="button" class="s1ne-show-m" onclick="document.getElementById('s1ne-mmenu').classList.toggle('is-open')" aria-label="開啟選單" style="align-items:center;justify-content:center;width:40px;height:40px;border:1px solid #e6dfd1;border-radius:10px;background:#fff;flex-shrink:0;">
      <span style="display:block;width:18px;height:2px;background:#23262d;box-shadow:0 6px 0 #23262d,0 -6px 0 #23262d;"></span>
    </button>
  </div>
  <div id="s1ne-mmenu" class="s1ne-mobile-menu" onclick="if(event.target.closest('a'))this.classList.remove('is-open')">
    <a href="/">首頁</a>
    <a href="/jobs/">職缺專區</a>
    <a href="/articles/">獵才專欄</a>
    <a href="/talent/">全民獵才</a>
    <a href="/talent/ai-tools/">AI求職工具</a>
    <a href="https://lin.ee/XcSWPzM" target="_blank" rel="noopener" style="color:#a67c3d;font-weight:700;">LINE 洽詢 →</a>
  </div>
</header>