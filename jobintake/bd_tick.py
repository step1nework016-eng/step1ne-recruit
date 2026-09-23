#!/usr/bin/env python3
"""反向開發的排程：撿顧問的開發需求，跑完整條龍。

    顧問填「我要開發做 MEP 的客戶」
            │
            ▼
    ① 去匿名人才庫撈「沒到職、有履歷」的人
            │
            ▼
    ② 總指揮：挑得上用場的人 → 產匿名人選卡 → 挑目標公司 → 寫開發信
            │
            ▼
    ③ 🚨 客戶名單比對（已簽約／洽談中／終端客戶 → 整家移除）
            │
            ▼
    ④ 就服法第 5 條保護特徵掃描（年齡／性別／役別…）
            │
            ▼
    ⑤ 前 AUTO_AFTER 批要顧問核准；顧問連續核准都沒改動之後，自動切成直接寄

⚠️ 人選同意不在這條流程裡。顧問面談時本來就會確認，
   而且開發信一律匿名（不含姓名與現職公司）。不要再加同意關卡。
"""

import importlib.util
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# ⚠️ ROOT 也要進 sys.path。下面用 importlib 載 interview_daemon.py 是「照路徑載」，
# 不會順便把它旁邊的模組（autoupdate 等）加進搜尋路徑——interview_daemon 在
# 2026-09 加了 `import autoupdate` 之後，這支就每次啟動都 ModuleNotFoundError，
# 而且錯誤只寫進 log 沒有任何通知，開發客戶那條線靜默停擺超過兩週才被發現。
sys.path.insert(0, ROOT)

import client_guard as G          # noqa: E402
import tg_bd                      # noqa: E402

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

from draft_bd import scrub        # noqa: E402  （就服法第 5 條掃描，共用同一份）

MODEL = 'claude-opus-5'
WORK = os.path.join(HERE, 'bd_work')

# 顧問連續核准這麼多批、而且都沒有按過重寫，之後就自動寄。
# 2026-08-11 Jacky 選的是「第一批要我看，順了就放手」。
AUTO_AFTER = 2


def log(m):
    print(m, flush=True)


def pool(limit=12):
    """匿名人才庫：有履歷、而且沒有走到到職的人。

    ⚠️ 撈進來的履歷全文會交給總指揮判斷配不配得上，
       但**產出的人選卡不可以有姓名與現職公司**——那是寄給外部企業的東西。
    """
    rows = D.d1(f"""
      SELECT a.id, a.job_title, a.expected_salary, a.available_date,
             a.interview_state, f.text_content AS cv,
             (SELECT p.stage FROM placements p WHERE p.application_id=a.id
               ORDER BY p.updated_at DESC LIMIT 1) AS stage
        FROM applications a
        JOIN files f ON f.id = a.resume_file_id
       WHERE a.superseded_by IS NULL
         AND f.text_content IS NOT NULL AND length(f.text_content) > 200
       ORDER BY a.created_at DESC LIMIT {int(limit)}""")
    return [r for r in rows if str(r.get('stage') or '') not in ('onboard', 'placed', '到職')]


# ── 為什麼拆成兩支 prompt ──
# 2026-08-11 第一版是一支大 prompt：挑人＋挑 5 家＋每家查 5 個求職管道＋寫 5 封信。
# 結果 30 分鐘逾時，什麼都沒產出。查職缺是 IO 密集的工作，一家就要好幾次 WebSearch，
# 五家串在同一次對話裡必然爆。
# 拆開之後：第一支只做判斷（不上網，很快），第二支一家一次（各自可控）。
# 任何一家查失敗也不會拖垮整批。

PICK_PROMPT = """你是 Step1ne（德仁管理顧問）的業務開發。

## 顧問要開發什麼

{ask}

## 人才庫（面談過、還沒到職的人）

{pool}

## 你要做兩件事

### ① 從人才庫挑一個人

配不上就說配不上（picked 填 null）。寄一封明顯不對題的信，那家公司以後就不會再看我們的信。

### ② 列出 {n} 家目標公司

- 要真的可能用到這種人的公司。寧可少而準。
- 不要挑同業（人力銀行、獵頭、派遣公司）。
- 不要挑我們客戶的終端客戶（例：客戶是設備商，它的終端晶圓廠不要挑）。
- **這一步不要上網查職缺**，下一關會一家一家查。你只要給名單與理由。

## 輸出

只輸出 JSON：

{{
  "picked": "挑中的人選 id（上面 [id=xxx]），挑不到填 null",
  "skip_reason": "挑不到的話一句話說明",
  "candidate_ref": "內部代號，例如 MEP-0811-A。不可以是姓名",
  "candidate_card": "匿名人選卡 5-8 行，給顧問看的內部摘要。
    ⛔ 不可有姓名、現職公司名、前東家全名、學校全名、電話、Email。
    ⛔ 也不可以寫年齡、性別、役別、婚育、國籍、身心障礙——就業服務法第 5 條
       禁止這些成為任用考量，我們主動寫進去等於把客戶推向違法。",
  "highlights": ["給對外信件用的重點精華 3-4 條，對著這個職能寫，同樣不可有可辨識資訊"],
  "companies": [{{"company": "公司全名", "why": "為什麼是這家，一句話"}}]
}}
"""


LETTER_PROMPT = """你是 Step1ne（德仁管理顧問）的業務開發。目標：**把這家公司簽下來當客戶。**

不是「介紹一個人」，是「用一個對得上的人選當敲門磚，換到一次對話」。

## 這一家

公司：{company}
為什麼挑他：{why}
顧問想開發的職能：{role}

## 我們手上的人選（對外可用的重點，已去識別化）

{highlights}

## 第一步：查這家公司現在在招什麼

管道全部都要試，不要查一個沒有就放棄：

  104人力銀行　·　1111　·　CakeResume　·　公司官網的人才招募頁　·　LinkedIn Jobs

用 WebSearch／WebFetch 實際去查，重點是找到跟上面這位人選對得上的職缺。
查到要記下：**職缺名稱、地點、關鍵條件、你在哪個管道看到的、網址**。

查不到公開職缺時分兩種：

- **預設不開發**：`has_job=false`、`probe=false`。沒有錨點的信就是廣告信。
- **例外，值得問問看**：`has_job=false`、`probe=true`。當這家明顯會用到這種人
  （產業對、規模夠、近期有擴廠或得標新聞），主動問一句「近期有沒有這方面的人力需求」
  本身就是開發，而且有機會問出沒公開的缺。信改成探詢語氣，**不可以假裝看到職缺**。

⛔ **絕對不可以編造職缺。** 寫錯職缺名稱，這家公司就永遠不會回我們了。

## 第二步：寫信

**主旨**（決定會不會被打開）：

- 要有具體的東西：職缺名 ＋ 人選最值錢的那一點
- 12–24 字，手機看得完
- 不要用「合作提案」「敬啟者」「毛遂自薦」「自我推薦」
- 好例子：「貴公司廠務工程師｜一位消防＋空調＋電力都獨立扛過的人選」
- 探詢版：「貴公司近期有廠務人力需求嗎？我這邊有一位剛釋出」

**內文照這個順序，不要多加段落：**

1. 一句自我介紹（一句，不是一段）：我是德仁管理顧問（Step1ne）的 Jacky
2. **我在哪裡看到你們在徵什麼**，並複述一兩個他們寫的條件，證明你真的看過
   （探詢版改成：為什麼想到這家公司）
3. 「我這邊有一位配得上的人選」+ **3–4 條**重點精華，對著他們的條件寫
4. 附件說明：匿名履歷（完整經歷、隱去可辨識資訊）與公司簡介
5. 收尾：如果對這位人選有興趣可以進一步討論；不需要的話回一聲就不再打擾

⛔ 不要推銷服務線（派遣／正職代招／中高階獵才），不要寫「想與貴公司合作」。
   這封只做一件事：讓對方對這個人選有興趣而願意回信。合作是回信之後的事。

- 全文 200–280 字。
- 署名固定：
  Jacky Chen｜德仁管理顧問有限公司（Step1ne）
  電話／LINE ID：0958616744
  就業服務許可：北市就服字第 0363 號｜臺北市政府勞動局 114 年度評鑑 A 級
  https://step1ne.com/about/

## 第三步：找收件窗口

人資信箱、招募窗口 email。查不到填 null，顧問可能自己有人脈。

## 輸出

只輸出 JSON：

{{
  "company": "{company}",
  "has_job": true,
  "probe": false,
  "job_title": "查到的職缺名稱，沒有填 null",
  "job_source": "在哪個管道看到的，沒有填 null",
  "job_url": "職缺網址，沒有填 null",
  "job_requirements": "他們寫的關鍵條件，沒有填 null",
  "contact_name": "窗口，查不到填 null",
  "contact_email": "查不到填 null",
  "subject": "信件主旨",
  "body": "信件全文"
}}

⚠️ 如果 has_job=false 而且 probe=false，subject 與 body 都填 null，這家會被捨棄。
"""


# ⚠️ 這段的內容規則來自 references/開發信框架.md —— Jacky 2026-09-23 親自定的。
# 改任何一條之前先去讀那份。之前 AI 自己發揮的版本被整批退回，
# 原話：「幹不行啦 最後一句很測 哪有人這麼沒禮貌」。
FORWARD_PROMPT = """你是資深企業招募顧問與 B2B 商務開發文案專家，替台灣招募品牌 Step1ne 寫企業開發信。

# 一、先搞清楚我們是誰

Step1ne **不是以中高階獵頭為主要定位**。我們協助企業處理一般正職、專業職、工程、
業務、行政、財會、IT、客服、門市、營運等各種職務的招募需求。

做的是「從開始找人，到值得安排面試」中間那一段：**找 → 篩 → 談 → 追**
- 找：增加人才來源、接觸主動投遞以外的人、社群招募、提升職缺曝光
- 篩：履歷初篩、確認基本條件、排除明顯不合適的
- 談：確認求職意願、薪資期待、轉職原因、到職時間、初步面談
- 追：推薦後進度追蹤、面試安排、意願確認、Offer 前後溝通

**要讓對方感受到的是**：「這個人理解我們正在招募的問題，而且真的能幫我減少工作。」
不是「這家公司想賣我服務」。

# 二、先找目標公司

**正在大量、持續徵人的連鎖品牌或企業。** 判斷依據一定要是你查得到的事實：
- 104／1111 企業頁同時開著多個同類職缺
- 同一批職缺長期掛著或反覆重新上架
- 多點展店、或近期有擴編新聞

# 三、每一家先判斷情境，再決定切角

## ⚠️ 判斷完情境，還要判斷「這個缺難在哪」——這決定你賣什麼

| 難在哪 | 怎麼認出來 | 賣什麼 |
|---|---|---|
| **來源** | 有這種人但他沒在投履歷（偏區門市、長期晚班、新店周邊、現場工程） | **賣「找」**：主動接觸沒在投的人、在地觸及 |
| **篩選** | **履歷爆多但篩不完、篩不準**（行銷、人資、行政、客服、業務助理） | **賣「篩＋談」**：把履歷海砍到只剩值得面試的 |
| 稀缺 | 有照才能做、市場上就是沒有（藥師、特定技師） | ⛔ **不要主打這種缺**，誰都做不到 |
| 條件 | 薪資低於行情、要求不合理 | ⛔ 不是招募問題，可以點出來但不要承諾解決 |

判斷的那一句話：**「市場上沒有這種人」，還是「有這種人但他沒看到你」，還是「人很多但篩不完」？**

**B 類（難在篩選）額外能賣的**：行銷是職稱通膨最嚴重的職類，「行銷專員」可能是小編、
投廣告、做品牌、做成長——企業常常自己沒想清楚要哪一種，JD 寫得四不像，
收到一堆不對的履歷再怪招募難。**幫他問清楚「到底要哪一種」本身就是價值。**
人資缺同理（招募／薪酬／勞務／HRBP 是四種不同的人）。

A｜一般正職招募 → 協助人才搜尋、篩選與初步溝通
B｜長期開缺 → ⛔ 不要說「看到你們一直找不到人」，要說「留意到這個職務目前仍持續招募」，
              然後給市場人才狀況／薪資觀察／招募難點
C｜大量招募擴編 → **不是**「我們可以幫你找人」，而是**幫 HR 分擔前段工作量**
D｜急徵 → 縮短找到人的時間。⛔ 禁止承諾「一定找到」「幾天找到」「保證履歷數」「保證錄取」
E｜看不到明確需求 → 不要假設對方缺人，改成建立關係＋提供人才市場資訊

# 四、信怎麼寫（五段）

1. **為什麼找你** —— 先講聯絡原因，講你實際查到的招募跡象
2. **表達理解** —— ⛔ 不要假裝知道對方內部狀況。用「如果目前…」「這類職缺通常…」
   ⛔ 禁止斷言「你們一定很難招」「你們 HR 應該忙不過來」
3. **能提供什麼** —— 依情境**只挑 2～4 個**最相關的，**不要列十項、不要整段自我介紹**
4. **降低合作壓力** —— 「不一定需要馬上談合作」「可以先從一個比較不好找的職缺開始」
5. **低門檻 CTA** —— ⛔ 第一封不要要求「安排 30 分鐘會議」「約 15 分鐘」
   ⛔ **也不要寫「先給我一個最不好補的缺」**——對方最願意丟出來的「最難的缺」，
      九成是稀缺型或條件型，我們也做不出來。接了交不出人就沒有第二次機會。
   ✅ 要這樣問：「如果有哪個缺是『**應該有人，但一直沒人來投**』，
      或者『**履歷收很多但篩不完**』，那種最適合先試。」
   ✅ 後面可以再加一句，它會讓我們更可信：
      「市場上真的沒有那種人的（像有證照門檻的），我也做不出來，不會浪費您時間。」
      **敢說自己做不到什麼，比說什麼都能做更容易被相信。**

**全文 250～450 字。手機上要能快速讀完。**

# 四之二、2026-09-23 加的四條（用真實成稿檢討出來的，違反的話等於白寫）

## 1. 必須處理「為什麼要多找一家」

會被我們鎖定的公司（104 上掛幾百上千筆職缺）**一定已經有配合的人力供應商**。
信裡不提這件事，等於要對方自己想理由——他不會想，他會直接關掉。

⛔ **絕對不要貶低或影射現有廠商**（「如果現在的廠商補不上」這種），那顯得我們在挑撥。
✅ 用**補位**的角度，把門檻降到「只給我一個最難的缺」：
   「這類量體的公司通常都已經有配合的夥伴，我想提的不是取代，是補最難的那幾個點。」
   「不用一次全給，先給一個最不好補的就好。」
**核心：不要求換供應商，只要求一個缺。決策成本從『採購案』降到『試一次』。**

## 2. 要有可驗證的憑據，⛔但不准編

HR 心裡真正的三個問題：**你跟我現在用的那家差在哪？多少錢？你做過誰？**
至少要回答前兩個。可以寫的只有這些（全部是官網已公開或法定公開的）：
- 臺北市政府勞動局 114 年度評鑑 A 級｜就業服務許可：北市就服字第 0363 號
- 成功後才收費，人選沒到職原則上不收費｜保固期內離職免費替補
- 正職招募平均約 1.5 個月薪起，依職務彈性，實際費率依委任合約約定

⛔ 絕對不可以寫：**任何客戶名稱**（全公司硬規則）、沒有出處的數字
（「成功率 90%」「平均 14 天到職」）、「業界最低價」「保證錄取」「保證幾天找到」。
**寧可只有評鑑等級跟收費方式，也不要編一個案例。**

## 3. 收件人是承辦層級時，要開一條轉介的路

我會告訴你 contact_level。如果是 `staff`（專員／承辦），
信裡要自然地加一句：「如果這部分是由其他同仁負責，也麻煩幫我轉一下。」
理由：承辦的 KPI 就是自己補到人，委外等於承認補不到——他有動機不回。
給他一個「轉出去」的台階，比逼他自己決定容易得多。

## 4. 不要重複對方已經知道的事

「貴公司 104 上有 545 筆職缺」——**HR 自己最清楚**，講這個沒有新資訊。
數字只能當「我做過功課」的引子，**一句話帶過**，
重點立刻轉到「這代表什麼、我看到什麼別人沒看到的」。

**每封信至少要有一句『只有真的懂這行才寫得出來』的話。** 例如前一輪最值錢的兩句：
- 「難的不是找到願意來的人，是三個月後還在的人。」
- 「藥師談排班與執業環境，彩妝師看品牌客層獎金，門市計時看離家多近——三類不在同一個人才池。」
這種句子不可被取代；其他內容任何一家人力公司都寫得出來。

# 五、⛔ 絕對禁止

- 過度業務化：「誠摯希望有機會為貴公司服務」「相信我們一定能創造卓越價值」「期待與貴司攜手合作」
- 過度吹捧：「貴公司是業界領先企業」（除非有資料支持）
- 沒證據的數字：「提升 50% 招募效率」「縮短 70% 招募時間」
- 過度強調 AI：不要寫「最先進 AI 招募技術」。要轉譯成結果——
  「前段會先協助蒐集整理人選的求職意願、薪資期待與基本面談資訊，讓 HR 可以直接從較適合的人選開始評估」
- 失禮的收尾：「就當我沒說」「當我沒提過」
- 裝熟、太多 emoji、制式 EDM 口氣

# 六、🚨 寫完一定要做這個檢查

**把「Step1ne」換成任何一家人力公司，這封信是不是仍然完全成立？**
如果是 → 內容還太制式，**重寫前 20%**，讓它真正跟這家公司目前的招募情境有關。

最終目標不是讓對方記得「這是一家獵頭公司」，而是讓他想到：
**「我有一個職缺不知道怎麼找，可以先丟給 Jacky 看看。」**

# 七、只輸出這個 JSON，不要有其他文字

{{
  "segment_note": "這一批鎖定的族群，一句話",
  "targets": [
    {{
      "company": "公司全名",
      "why": "為什麼是這家。**必須講出你實際查到的徵才跡象**（例：104 企業頁同時開著 12 個門市職缺、分布 6 縣市）。只寫『規模大』『知名品牌』一律不接受",
      "evidence_url": "你看到那個跡象的網址，沒有寫 null",
      "open_roles": "他們現在在徵什麼，一句話",
      "scenario": "A|B|C|D|E",
      "difficulty_type": "source|screening|scarcity|conditions —— 這家主要的缺難在哪，決定你賣『找』還是賣『篩＋談』",
      "scenario_reason": "為什麼判這個情境，1-3 句",
      "angle": "一句話：這封信應該從＿＿切入，而不是＿＿",
      "contact_name": "招募窗口姓名或稱呼，查不到寫 null",
      "contact_level": "manager|staff|dept_mailbox|unknown",
      "contact_email": "查到才寫。⛔ 絕對不可以猜或套公式（hr@、recruit@）——寄到不存在的信箱比沒寄更糟",
      "subject_alts": ["5 個主旨。⛔ 不要驚嘆號、促銷、免費、限時、『業務合作邀請』、『獵頭合作提案』"],
      "subject": "從上面 5 個裡挑最好的那個",
      "body": "可以直接寄出的完整信，含稱謂與署名，250-450 字",
      "followup1": "第一封後 4-5 個工作天寄。⛔ 不可以只問『有沒有看到上一封』，**必須補一個新的價值點**",
      "followup2": "再隔約一週，語氣更輕、不施壓。讓對方知道未來有需求仍可直接聯絡",
      "crm": {{"企業類型":"", "招募情境":"", "優先程度":"高|中|低", "下一步":"", "建議追信日期":""}}
    }}
  ]
}}

# 八、署名

**不要寫署名，系統會自動接上。** body 寫到最後一句話就停。
（2026-09-23：連續兩版都漏掉署名裡的電話與就服字號，改成程式接，
不要再交給你記。固定不變的東西就不該由 AI 產。）

# 九、挑公司的規矩

- 挑 5-8 家，寧可少而準
- ⛔ 不要挑同業（人力銀行、獵頭、派遣公司、人資顧問）
- 不要挑既有客戶（系統之後會再比對，但你能判斷的先排除）
- 同一個集團底下不要重複挑多家
"""


def forward_ask(r):
    """正向開發的需求描述。跟反向開發不同——這裡沒有人選，只有『要打哪一群公司』。"""
    parts = [f"要開發的族群：{r.get('role_family') or '（未指定）'}"]
    for k, lb in (('industry', '產業'), ('region', '地區'),
                  ('service_line', '主打的委託方式'), ('note', '顧問交代')):
        if r.get(k):
            parts.append(f'{lb}：{r[k]}')
    parts.append(f"要幾家：{r.get('target_count') or 6}")
    return '\n'.join(parts)


def ask_text(r):
    parts = [f"職缺類型：{r['role_family']}"]
    for k, lb in (('industry', '產業'), ('region', '地區'), ('note', '顧問交代')):
        if r.get(k):
            parts.append(f'{lb}：{r[k]}')
    return '\n'.join(parts)


def pool_text(ps):
    out = []
    for p in ps:
        out.append(f"[id={p['id']}] 應徵過：{p.get('job_title') or '—'}　"
                   f"期望：{p.get('expected_salary') or '—'}\n"
                   f"{(p.get('cv') or '')[:2200]}\n---")
    return '\n'.join(out)


def run_commander(prompt, workdir, tag='prompt', timeout=900):
    os.makedirs(workdir, exist_ok=True)
    open(os.path.join(workdir, f'{tag}.txt'), 'w', encoding='utf-8').write(prompt)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=timeout)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, f'{tag}.raw.txt'), 'w', encoding='utf-8').write(out)
    if not out:
        raise RuntimeError(f'總指揮沒有回應：{(r.stderr or "")[:300]}')
    s, e = out.find('{'), out.rfind('}')
    if s < 0:
        raise RuntimeError('回應裡找不到 JSON')
    return json.loads(out[s:e + 1])


def auto_mode():
    """顧問已經核准過幾批、而且沒有退回重寫過？夠了就自動寄。

    判準用「批」不是「封」——一批六封全按核准只證明這一次的品質，
    要連續幾批都沒問題才算穩。
    """
    rows = D.d1("""SELECT batch_id,
                     SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) sent,
                     SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) rewritten
                   FROM bd_outreach WHERE batch_id IS NOT NULL GROUP BY batch_id""")
    clean = [r for r in rows if (r['sent'] or 0) > 0 and not (r['rewritten'] or 0)]
    return len(clean) >= AUTO_AFTER



def make_anon_pdf(app_id, workdir):
    """產匿名履歷 PDF 並存進 files 表，回傳 file_id。

    ⚠️ 一定要用 anon 模式：對方是還沒簽約的陌生公司，
       給具名履歷等於未經同意把人選資料交出去。
    """
    import base64
    out = os.path.join(workdir, f'anon_{app_id[:8]}.pdf')
    r = subprocess.run(
        ['python3', os.path.join(ROOT, 'make_anon_cv.py'), app_id, '--mode', 'anon', '--out', out],
        capture_output=True, text=True, env=D.env_with_cf(), timeout=900)
    if not os.path.exists(out):
        log(f'⚠️ 匿名履歷產不出來，這批信不會有履歷附件：{(r.stderr or r.stdout)[-300:]}')
        return None
    b = open(out, 'rb').read()
    b64 = base64.b64encode(b).decode()
    fid = f'bdcv-{app_id[:8]}-{uuid.uuid4().hex[:6]}'
    chunks = (len(b64) + 89999) // 90000
    D.d1(f"""INSERT INTO files (id, created_at, filename, mime, size, chunks)
             VALUES ({D.q(fid)}, datetime('now','+8 hours'),
                     {D.q(os.path.basename(out).replace('anon_', '匿名履歷_'))},
                     'application/pdf', {len(b)}, {chunks})""")
    for i in range(chunks):
        D.d1(f"INSERT INTO file_chunks (file_id, idx, b64) VALUES "
             f"({D.q(fid)}, {i}, {D.q(b64[i * 90000:(i + 1) * 90000])})")
    log(f'✅ 匿名履歷 PDF 已存（{len(b)//1024} KB，{chunks} chunk）')
    return fid


def handle_forward(req):
    """正向開發：不帶人選，直接用委託招募的服務去敲正在大量徵人的公司。

    跟 handle()（反向開發）刻意共用同一批把關與同一張 bd_outreach 表——
    客戶名單比對、就服法掃描、顧問核准、送達追蹤都是一模一樣的流程，
    不要為了新模式另開一條平行的路，那條一定會在某次改動之後跟主線走偏。

    唯一的差別是「拿什麼去敲門」：反向拿人選，正向拿服務。
    """
    rid = req['id']
    D.d1(f"UPDATE bd_requests SET status='working', updated_at=datetime('now','+8 hours') "
         f"WHERE id={D.q(rid)}")
    workdir = os.path.join(WORK, rid[:8])

    prompt = FORWARD_PROMPT + '\n\n## 這次要開發的\n\n' + forward_ask(req) + '\n'
    try:
        spec = run_commander(prompt, workdir, tag='forward', timeout=1800)
    except Exception as e:
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(str(e)[:300])}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"⚠️ 正向開發「{req.get('role_family')}」跑不下去：{str(e)[:200]}")
        return

    targets = spec.get('targets') or []
    if not targets:
        why = spec.get('reason') or '總指揮沒有挑出任何目標公司'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 正向開發「{req.get('role_family')}」沒有出信\n原因：{why}")
        return

    # 客戶名單比對：已簽約／洽談中／終端客戶一律整家移除（跟反向開發同一份名單、
    # 同一支 filter_targets，不要自己再寫一套比對邏輯）
    clients = G.load_clients(D.d1)
    targets, blocked, warned = G.filter_targets(targets, clients)
    for w in warned:
        log(f"⚠️ {w.get('company')}：{w.get('why')}（沒擋，但顧問要留意）")
    if not targets:
        D.d1(f"UPDATE bd_requests SET status='failed', result_note='目標全部在客戶名單上', "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 正向開發「{req.get('role_family')}」沒有出信\n"
                        f"原因：挑出來的公司全部已經是我們的客戶或洽談中")
        return

    law = scrub(spec)
    for lb, w in law:
        log(f'⚠️ 就服法第 5 條保護特徵出現在{lb}：「{w}」')

    # 沒信箱的先自動跑一次窗口查找再說。
    # ⚠️ 2026-09-23：這一關原本沒接進來，結果 B 類第一次跑 6 家有 5 家因為
    #    「查不到信箱」被丟掉，只出了 1 封——公司找得很準卻寄不出去，
    #    等於前面的功全白做。瓶頸從來不是找公司，是找人。
    missing = [t for t in targets if not (t.get('contact_email') or '').strip()]
    if missing:
        try:
            import contact_lookup
            found, _sum = contact_lookup.lookup([t['company'] for t in missing], workdir)
            for t in missing:
                c = found.get(t['company']) or {}
                mail = (c.get('contact_email') or '').strip()
                if contact_lookup.is_recruiting_mail(mail):
                    t['contact_email'] = mail
                    t['contact_name'] = c.get('contact_name') or t.get('contact_name')
                    t['contact_level'] = c.get('contact_level')
                    log(f"  🔎 補到窗口：{t['company']} → {mail}")
        except Exception as e:
            log(f'⚠️ 窗口查找失敗（不影響已有信箱的）：{str(e)[:150]}')

    # 還是沒有信箱的不送審——顧問按了也寄不出去，只是製造一則白按的通知。
    # ⛔ 也不要幫它補一個猜的信箱：寄到不存在的地方比沒寄更糟，而且沒人會發現。
    no_mail = [t for t in targets if not (t.get('contact_email') or '').strip()]
    targets = [t for t in targets if (t.get('contact_email') or '').strip()]

    batch = str(uuid.uuid4())[:8]
    rows = []
    for t in targets:
        bid = str(uuid.uuid4())
        rows.append((bid, t))
        import bd_sig
        import json as _json
        # ⚠️ scenario／difficulty_type／angle／subject_alts／followup／crm 一定要一起存。
        #    2026-09-23 第一次跑 B 類時這些全掉了，等於判斷做了但沒人看得到，
        #    追信也沒東西可寄。
        D.d1(f"""INSERT INTO bd_outreach
          (id, created_at, batch_id, request_id, candidate_ref, candidate_card, company,
           why_company, contact_name, contact_email, contact_level, subject, body,
           status, updated_at, job_title, job_source, job_url,
           scenario, difficulty_type, angle, subject_alts, followup1, followup2, crm_json)
          VALUES ({D.q(bid)}, datetime('now','+8 hours'), {D.q(batch)}, {D.q(rid)},
                  {D.q('SVC-' + batch)}, NULL,
                  {D.q(t.get('company'))}, {D.q(t.get('why'))}, {D.q(t.get('contact_name'))},
                  {D.q(t.get('contact_email'))}, {D.q(t.get('contact_level'))},
                  {D.q(t.get('subject'))}, {D.q(bd_sig.ensure(t.get('body')))},
                  'pending', datetime('now','+8 hours'),
                  {D.q(t.get('open_roles'))}, 'forward_bd', {D.q(t.get('evidence_url'))},
                  {D.q(t.get('scenario'))}, {D.q(t.get('difficulty_type'))}, {D.q(t.get('angle'))},
                  {D.q(_json.dumps(t.get('subject_alts') or [], ensure_ascii=False))},
                  {D.q(bd_sig.ensure(t.get('followup1')) if t.get('followup1') else None)},
                  {D.q(bd_sig.ensure(t.get('followup2')) if t.get('followup2') else None)},
                  {D.q(_json.dumps(t.get('crm') or {}, ensure_ascii=False))})""")

    head = (f"🎯 正向開發（賣委託招募服務）：{req.get('role_family')}\n"
            f"{spec.get('segment_note') or ''}\n\n"
            f"{len(rows)} 家出信"
            + (f"　·　⛔ {len(blocked)} 家已是客戶" if blocked else '')
            + (f"　·　📭 {len(no_mail)} 家查不到窗口信箱" if no_mail else ''))
    if blocked:
        head += '\n\n【客戶名單擋下，不會寄】\n' + '\n'.join(
            f"・{b.get('company')}：{b.get('why')}" for b in blocked)
    if warned:
        head += '\n\n【有關聯但沒擋，請顧問確認】\n' + '\n'.join(
            f"・{w.get('company')}：{w.get('why')}" for w in warned)
    if no_mail:
        # 這幾家不是沒價值，是缺一塊資料——講出來顧問才有機會自己去補
        head += '\n\n【查不到窗口信箱，沒有送審】\n' + '\n'.join(
            f"・{t.get('company')}：{t.get('open_roles') or ''}" for t in no_mail)
    if law:
        head += '\n\n⚠️ 就服法保護特徵：' + '、'.join(w for _, w in law)

    tg_bd.send_head(head)
    for bid, t in rows:
        mid = tg_bd.send_letter(bid, t, has_cv=False)
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(bid)}")

    D.d1(f"UPDATE bd_requests SET status='done', batch_id={D.q(batch)}, "
         f"result_note={D.q(f'出信 {len(rows)} 家；客戶名單擋 {len(blocked)}；無信箱 {len(no_mail)}')}, "
         f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
    log(f'✅ 正向開發送審 {len(rows)} 封，顧問核准前一封都不會寄。')


def handle(req):
    rid = req['id']
    D.d1(f"UPDATE bd_requests SET status='working', updated_at=datetime('now','+8 hours') "
         f"WHERE id={D.q(rid)}")
    workdir = os.path.join(WORK, rid[:8])
    ps = pool()
    if not ps:
        D.d1(f"UPDATE bd_requests SET status='failed', result_note='人才庫是空的' "
             f"WHERE id={D.q(rid)}")
        tg_bd.send_head(f"⚠️ 開發需求「{req['role_family']}」跑不下去：人才庫裡沒有可用的履歷。")
        return

    # ── 第一階段：挑人 ＋ 列名單（不上網，快）──
    spec = run_commander(
        PICK_PROMPT.format(ask=ask_text(req), pool=pool_text(ps),
                           n=req.get('target_count') or 6),
        workdir, tag='pick', timeout=900)
    json.dump(spec, open(os.path.join(workdir, 'pick.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    if not spec.get('picked') or not spec.get('companies'):
        why = spec.get('skip_reason') or '總指揮沒有挑到配得上的人選'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 開發需求「{req['role_family']}」沒有出信\n原因：{why}\n"
                        f"（配不上就不硬湊——寄一封不對題的信，那家公司以後就不看我們的信了）")
        return

    # ── 客戶名單比對要在查職缺之前 ──
    # 先擋掉不能碰的，免得白花十分鐘去查一家本來就不該敲的公司。
    clients = G.load_clients(D.d1)
    ok_co, blocked, warned = G.filter_targets(spec['companies'], clients)
    for b in blocked:
        log(f'  ⛔ 移除 {b["company"]}：{b["why"]}（命中名單上的「{b["matched"]}」）')

    # ── 第二階段：一家一次，各自去查職缺＋寫信 ──
    hl = '\n'.join('・' + h for h in (spec.get('highlights') or []))
    targets, no_anchor = [], []
    for i, c in enumerate(ok_co):
        name = c.get('company') if isinstance(c, dict) else str(c)
        log(f'  [{i + 1}/{len(ok_co)}] 查 {name} 在招什麼⋯')
        try:
            t = run_commander(
                LETTER_PROMPT.format(company=name, why=(c.get('why') if isinstance(c, dict) else ''),
                                     role=req['role_family'], highlights=hl),
                workdir, tag=f'letter{i}', timeout=900)
        except Exception as e:
            log(f'      ✗ 查不下去（{str(e)[:80]}），跳過這家')
            continue
        if not t.get('subject') or not t.get('body'):
            no_anchor.append(name)
            log(f'      · 查不到職缺、也不值得問問看 → 捨棄')
            continue
        anchor = ('探詢' if t.get('probe') else (t.get('job_title') or '無錨點'))
        log(f'      ✓ {anchor}　窗口：{t.get("contact_email") or "待補"}')
        targets.append(t)

    spec['targets'] = targets
    json.dump(spec, open(os.path.join(workdir, 'spec.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    if not targets:
        why = f'{len(ok_co)} 家都查不到對得上的職缺，也不值得探詢'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 開發需求「{req['role_family']}」沒有出信\n原因：{why}")
        return

    # 就服法第 5 條掃描（客戶名單那一關在上面已經做過）
    law = scrub(spec)
    for lb, w in law:
        log(f'⚠️ 就服法第 5 條保護特徵出現在{lb}：「{w}」')

    batch = str(uuid.uuid4())[:8]
    auto = auto_mode() and not law     # 有法遵疑慮就一定要人看過，不自動寄
    cv_fid = make_anon_pdf(spec['picked'], workdir)
    rows = []
    for t in targets:
        bid = str(uuid.uuid4())
        rows.append((bid, t))
        D.d1(f"""INSERT INTO bd_outreach
          (id, created_at, batch_id, request_id, candidate_ref, candidate_card, company,
           why_company, contact_name, contact_email, subject, body, status, updated_at,
           cv_file_id, job_title, job_source, job_url, probe)
          VALUES ({D.q(bid)}, datetime('now','+8 hours'), {D.q(batch)}, {D.q(rid)},
                  {D.q(spec.get('candidate_ref') or batch)}, {D.q(spec.get('candidate_card'))},
                  {D.q(t.get('company'))}, {D.q(t.get('why'))}, {D.q(t.get('contact_name'))},
                  {D.q(t.get('contact_email'))}, {D.q(t.get('subject'))}, {D.q(t.get('body'))},
                  'pending', datetime('now','+8 hours'),
                  {D.q(cv_fid)}, {D.q(t.get('job_title'))}, {D.q(t.get('job_source'))},
                  {D.q(t.get('job_url'))}, {1 if t.get('probe') else 0})""")

    head = (f"🎯 開發需求：{req['role_family']}\n"
            f"配對到人選 {spec.get('candidate_ref')}　·　{len(targets)} 家出信"
            + (f"　·　⛔ {len(blocked)} 家被客戶名單擋下" if blocked else '')
            + '\n\n' + (spec.get('candidate_card') or ''))
    if blocked:
        head += '\n\n【已自動移除，不會寄】\n' + '\n'.join(
            f'・{b["company"]}：{b["why"]}' for b in blocked)
    if law:
        head += ('\n\n⚠️ 內容出現就服法第 5 條的保護特徵：'
                 + '、'.join(w for _, w in law) + '\n核准之前請先刪掉，這批不會自動寄。')
    if auto:
        head += '\n\n🤖 你前面幾批都直接核准沒改動，這批系統會自動寄出。要停就按下面任何一封的「不寄」。'
    tg_bd.send_head(head)

    for bid, t in rows:
        mid = tg_bd.send_letter(bid, t, has_cv=bool(cv_fid))
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(bid)}")

    D.d1(f"UPDATE bd_requests SET status='done', batch_id={D.q(batch)}, "
         f"result_note={D.q(f'{len(targets)} 家送審，{len(blocked)} 家被客戶名單擋，{len(no_anchor)} 家沒錨點')}, "
         f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
    log(f'✅ 需求 {rid[:8]}：送審 {len(rows)} 封，擋下 {len(blocked)} 家')


def main():
    reqs = D.d1("SELECT * FROM bd_requests WHERE status='new' ORDER BY created_at LIMIT 3")
    if not reqs:
        return
    log(f'撿到 {len(reqs)} 張開發需求')
    for r in reqs:
        try:
            # mode 決定拿什麼去敲門：reverse＝帶人選（預設，舊資料沒有這欄）、
            # forward＝不帶人選，直接賣委託招募服務。兩邊之後的把關完全一樣。
            if (r.get('mode') or 'reverse') == 'forward':
                handle_forward(r)
            else:
                handle(r)
        except Exception as e:
            log(f'❌ 需求 {r["id"][:8]} 失敗：{e}')
            D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(str(e)[:300])}, "
                 f"updated_at=datetime('now','+8 hours') WHERE id={D.q(r['id'])}")
            tg_bd.send_head(f"❌ 開發需求「{r.get('role_family')}」處理失敗：{str(e)[:200]}")


if __name__ == '__main__':
    main()
