# 職缺頁版型與必備元素

## 區塊順序（實測定案）

硬條件必須在最前面。早期版本把薪資、地點、年資散在 FAQ 折疊區裡，
使用者實測後明確反饋「這樣寫讓人無法接受」——求職者是用掃的，不是用讀的。

```
Header（sticky，含手機漢堡選單）
  ↓
Hero
  ├ 麵包屑：← 返回職缺專區
  ├ 標籤列：服務線／急徵／地點特性（例：高階獵才・年薪百萬・海外外派）
  ├ H1：職稱（＋括號補充，例：柬埔寨長期外派）
  └ 導言：2-3 句，講清楚「這是什麼機會、適合誰」
  ↓
職缺條件一覽（規格表）← 最關鍵
  ↓
工作內容（依職責分類，每類 2-4 條）
  ↓
應徵條件（必要條件／加分條件 兩張卡並排）
  ↓
為什麼選擇這個機會（3-4 張卡）
  ↓
應徵流程（3 步驟）
  ↓
常見問題（details，5 題上下）
  ↓
CTA（LINE）
  ↓
Footer（雙向連結：所有職缺／首頁）
```

## 規格表寫法

桌機左右兩欄、手機自動堆疊成單欄。薪資列加 `highlight` 樣式讓它最顯眼。

```html
<dl class="spec">
  <div class="spec-row highlight">
    <dt>薪資待遇</dt>
    <dd>月薪 35,000 – 53,000 元
      <span style="display:block;font-weight:400;font-size:14px;color:#55585f;margin-top:4px;">依學歷與科系相關性核定，另有固定年終獎金 2 個月</span>
    </dd>
  </div>
  <!-- 其餘列不加 highlight -->
</dl>
```

```css
.spec-row{display:grid;grid-template-columns:132px 1fr;gap:18px;padding:16px 22px;border-bottom:1px solid #f0ebe0;}
@media (max-width:600px){
  .spec-row{grid-template-columns:1fr;gap:4px;padding:14px 18px;}
  .spec-row dt{font-size:12.5px;letter-spacing:0.5px;}
}
```

常用欄位：薪資待遇／職務性質／工作地點／需求人數／學歷要求／經驗要求／
產業背景／系統能力／語言能力／出差需求／加班情形／福利／辦公型態

**只放有資料的欄位**，來源沒寫或標「待確認」的直接省略。

規格表底下固定加這行：
> ※ 上述條件為目前規劃，實際待遇與工作條件依面試後雙方確認為準。

## Schema（JSON-LD）

只放 `JobPosting`。**不要加 `FAQPage`**——Google 2026/5/7 起已全面移除 FAQ
豐富結果，掛了不會有任何視覺效果，還可能在 Search Console 產生雜訊。

```json
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "職稱（服務線・專案性質）",
  "description": "3-5 句完整描述，含職責、資格與性質",
  "datePosted": "YYYY-MM-DD",
  "validThrough": "YYYY-MM-DD",
  "employmentType": ["FULL_TIME"],
  "hiringOrganization": {
    "@type": "Organization",
    "name": "德仁管理顧問有限公司（Step1ne）",
    "sameAs": "https://step1ne.com/",
    "logo": "https://step1ne.com/assets/step1ne-logo.png"
  },
  "jobLocation": [{ "@type": "Place", "address": { "@type": "PostalAddress", "addressLocality": "…", "addressRegion": "…", "addressCountry": "TW" } }],
  "baseSalary": { "@type": "MonetaryAmount", "currency": "TWD", "value": { "@type": "QuantitativeValue", "minValue": 0, "maxValue": 0, "unitText": "MONTH" } },
  "jobBenefits": "…",
  "experienceRequirements": "…",
  "occupationalCategory": "…",
  "industry": "…"
}
```

- 派遣職缺 `employmentType` 用 `["TEMPORARY", "FULL_TIME"]`
- `unitText` 用 `MONTH` 或 `YEAR`，要跟數字單位一致（年薪就填年薪數字）
- 純面議時整個拿掉 `baseSalary`，不要填 0 或猜測值

## 服務線差異

| | 高階獵才 | 人力派遣 | 正職招募 |
|---|---|---|---|
| 標籤 | 高階獵才 | 人力派遣 | 正職 |
| 應徵流程第 2 步 | 顧問**保密**初談 | 顧問聯繫確認條件 | 顧問聯繫確認條件 |
| CTA 文案 | 「加入 LINE 與顧問保密聊聊，先了解再決定要不要投」 | 「留下資料，顧問會盡快與你聯繫」 | 同派遣 |
| 必備 FAQ | 我還在職，投遞會不會曝光？ | 派遣權益跟正職一樣嗎？ | — |

## 每頁都要有的 FAQ

**信任題**（所有職缺頁共用）。第一次使用時把角括號換成貴公司的實際資料，
之後每個職缺頁直接沿用同一段：

> **〈品牌名〉是什麼公司？可信嗎？**
> 〈品牌名〉是〈法人全名〉旗下品牌，統一編號 〈統編〉，並持有〈主管機關〉
> 核發的就業服務許可證（〈就服字號〉），登記地址〈公司登記地址〉，可至經濟部
> 商工登記公示資料查詢系統核對公司狀態。專注〈服務項目〉，〈可佐證的實績數字〉。

這題很重要：求職者從搜尋或 AI 引擎進來時完全不認識這個品牌，**可驗證的統編與
就業服務許可證字號**是把「這會不會是詐騙」的疑慮擋下來的關鍵——尤其人力仲介
產業本來就是詐騙話術愛用的包裝，能被查證的登記資訊比任何形容詞都有效。

（就業服務許可證是台灣《就業服務法》對私立就業服務機構的法定要求，
非台灣地區請改為當地對應的人力仲介執照字號。）

## 行動裝置必備

- 手機漢堡選單（`.s1ne-show-m` 按鈕 + `.s1ne-mobile-menu`），點連結後自動關閉
- 手機隱藏頁首 LINE 按鈕（`.s1ne-hide-m`），避免跟漢堡鈕擠在一起
- 所有連結觸控目標 ≥ 44px
- **注意 inline style 蓋掉 class padding 的坑**：`<article class="wrap" style="padding:40px 0 24px;">`
  會讓左右 padding 變成 0，文字貼齊螢幕邊緣。要寫成 `padding:40px 24px 24px;`
- 卡片型區塊不要直接掛 `class="wrap"`，會讓卡片貼齊螢幕邊；外面再包一層 `.wrap`

## 職缺專區卡片

新增職缺時同步在 `/jobs/index.html` 加一張卡：

```html
<a class="job" href="/jobs/<slug>/" data-track="senior" data-cat="finance overseas">
  <div class="job-industry">產業・領域</div>
  <h2 class="job-title">職稱</h2>
  <div class="job-meta">
    <span class="salary">年薪 100–200 萬</span>
    <span class="track">高階獵才</span>
    <span>正職</span>
    <span>地點</span>
    <span>年資門檻</span>
  </div>
  <p class="job-desc">2-3 句摘要</p>
  <span class="job-more">查看職缺詳情 →</span>
</a>
```

`data-track` 用 `senior`（中高階）／`general`（一般）／`dispatch`（派遣）；
`data-cat` 可放多個產業，空白分隔。
