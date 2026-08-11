# 104 爬蟲（從舊系統救出來的）

2026-08-05 從 `~/clawd/skills/headhunter/scripts/` 搬過來。原本有 6 個版本並存
（`scraper-104.py` → `v2` → `v3` → `v4` → `fixed`），那是改壞了重寫、沒人清的結果。
留下兩支，其餘已刪。

| 檔案 | 原名 | 做什麼 |
|---|---|---|
| `job-search.py` | `scraper-104-v4.py` | 搜尋 104 職缺，抓公司與職位。v4 是最終版：用完整 snapshot、加了隨機延遲防偵測 |
| `company-contact.py` | `scraper-104-company-contact.py` | 職缺頁 → 公司頁 → 抓聯絡人、電話、email。**用途不同**，是開發客戶用的，不是重複 |

## ⚠️ 兩支現在都跑不起來

它們靠一個叫 `agent-browser` 的命令列工具：

```python
subprocess.run(['agent-browser', 'open', 'https://www.104.com.tw'...
```

那個工具在這台機器上**已經不存在**（舊系統留下的）。要用的話得先改成現有的
瀏覽器工具（Claude Code 的 browser MCP，或 Playwright）。

## ⚠️ 用之前想清楚

`company-contact.py` 抓的是**公司聯絡人的姓名、電話、email**。
拿來做陌生開發前，先確認：
- 104 的服務條款允許嗎
- 個資法上，這些資料的蒐集與利用目的站得住腳嗎

職缺搜尋（`job-search.py`）是公開資訊，風險低很多。兩者不要混為一談。
