# Example Discovery Run

## Input

Campaign:
- target: Taiwan beauty KOC
- platform: Instagram
- follower range: 5K–30K
- preferred signals: product review, brand collaboration
- excluded: brand/store accounts
- current gap: 12

## Derived target

Gap 12 + buffer 3 = aim for 15 qualified reports.

## Backend start response

Search intents:

1. `美妝 開箱`
2. `保養 開團`
3. `敏感肌 保養 推薦`

## Search process

### Intent 1

Query:

`site:instagram.com 美妝 開箱`

Inspect profile results.

Example accepted profile:

- handle: `hannah.daily`
- creator type: `personal_koc`
- followers: 12,400
- bio: lifestyle/beauty review language
- email visible
- evidence: repeated beauty/skincare review content + collaboration contact

Report creator, then campaign candidate immediately.

### Intent 2

Continue until per-intent first-pass limit or enough qualified creators are found.

### Intent 3

Same process.

## Progress update example

> 目前完成 2/3 組搜尋方向：已檢查 24 個不重複帳號，成功回報 10 位候選人；4 位為品牌/商店帳號，2 位私人帳號，1 位無法讀取。

## Final

Once 15 qualified reports are accepted or search effort is exhausted:

Call finish API.

Final user summary:

> 外部搜尋完成 ✅ 已檢查 36 個不重複帳號，回報 15 位符合條件的創作者；排除 7 位品牌/商店帳號，3 位無法可靠讀取。已通知後端重新統計候選人池並進入人工審核。
