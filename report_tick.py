#!/usr/bin/env python3
"""顧問在「顧問人選回報區」講的話 → 翻成漏斗狀態。

    顧問打人話：「張博州客戶約週四下午面試」
            │
            ▼
    總指揮：認人選、認動作、認日期
            │
            ▼
    🚨 先問再寫：送出確認訊息＋兩顆按鈕，顧問按「對」才動資料庫
            │
            ▼
    更新 placements，並回一句確認

⚠️ **一律先問再寫**（2026-08-12 Jacky 選的 A）。
   認錯人比沒認出來糟——把 A 的面試日期寫到 B 身上，
   顧問要花更多時間才會發現，而且中間可能已經照著錯的資料去跟客戶講話。

⚠️ 這支只讀 consultant_reports 裡 status='new' 的，寫入由 Worker 的按鈕回呼做。
   分工的理由跟職缺上架一樣：Worker 跑不了 claude，本機推不了按鈕回呼。
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

# 2026-09-10 加：多裝置協作用——同一筆 status='new' 的回報，兩台裝置同時撈到
# 會各自跑一次 handle()，重複送 TG 確認訊息給顧問。搶到才算你的，搶不到跳過。
WORKER_ID = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()

MODEL = 'claude-sonnet-5'   # 這是分類與比對，不是判斷；要的是快
THREAD_REPORT = 3161


def log(m):
    print(m, flush=True)


def roster():
    """目前有可能被回報到的人。

    ⚠️ 範圍要夠寬：顧問可能回報三個月前談的人（客戶終於回覆了）。
       但也不能全撈——名單越長，總指揮認錯人的機會越大。
       折衷：談過的、還沒到職的，全部帶上。
    """
    return D.d1("""
      SELECT a.id, a.name, a.job_title, a.job_slug, a.interview_state,
             (SELECT p.stage FROM placements p WHERE p.application_id=a.id
               ORDER BY p.updated_at DESC LIMIT 1) AS stage
        FROM applications a
       WHERE a.superseded_by IS NULL
       ORDER BY a.created_at DESC LIMIT 120""")


PROMPT = """顧問在群組裡回報人選進度。把它翻成系統看得懂的動作。

⚠️ **一則訊息可能同時講好幾位。** 2026-08-12 ph 的第一則回報就是：
她引用整批「有 4 位談完了但沒人處置」的提醒，在每個人下面用 * 標一句：

```
· 湯豐銘 VIP貴賓接待服務員（日班）
  報告產出 7 天，還沒有人處置
* 約顧問洽談未回覆          ← 這行才是她的回報
· 范博翔 ...
*安排實體面試 確認意願中     ← 這行才是她的回報
```

**引用的部分是系統自己發的提醒，不是她講的話。** 她的回報是 * 或標記後面那幾句。
每一位各自輸出一筆。

## 顧問說的話

```
{text}
```

## 目前手上的人選（只能從這裡面挑，不可以自己造）

{roster}

## 你要判斷三件事

### ① 他在講誰

只用姓名或明顯的線索比對。**比對不到、或有兩個以上像的，`application_id` 填 null**，
並在 `question` 寫出你要問顧問的話。⛔ **不要猜。** 認錯人的代價比多問一句大得多。

### ② 他在講什麼動作

⚠️ **`stage` 一定要用下面這幾個英文代碼，不准自己發明或翻成中文。**
   2026-08-12 第一版讓總指揮直接輸出中文（「客戶面談」「結案」），
   寫進 `placements.stage` 之後漏斗完全看不到——漏斗查詢比對的是
   `UPPER(stage) IN ('SUBMITTED','CLIENT_INTERVIEW',...)` 這種既有英文碼，
   中文字串永遠不會命中，等於白寫。這是既有系統的資料格式，不是這裡新訂的。

| stage 代碼 | 什麼時候用 |
|---|---|
| `SUBMITTED` | 履歷剛送給客戶，還沒約到面試 |
| `INTERVIEWING` | 客戶已經安排面試、正在面試、或面試完等結果——這整段都算這個代碼 |
| `OFFER_ACCEPTED` | 客戶決定要了、在談 offer、等報到 |
| `ONBOARDED` | 真的到職上工了（這個代碼會觸發系統自動記錄到職日並轉入保證期，你不用管細節） |
| `CLOSED_LOST` | 人選拒絕、客戶不要、案子停掉、不接受這個職缺 |
| `null` | 純備註，沒有狀態變化（例：「他說下週回覆」「客戶還在考慮」「約了洽談但未回覆」） |

⚠️ **不確定就填 null 當純備註。** 備註記錯只是多一行字，
狀態改錯會讓漏斗數字錯、讓顧問以為案子在推進。

### ③ 有沒有日期

「週四」「下週三」「8/15」都要換算成 YYYY-MM-DD。今天是 {today}（{weekday}）。
沒提到日期就填 null。**不要自己補一個合理的日期。**

## 輸出

只輸出 JSON，不要其他文字。**一位人選一筆**：

{{
  "items": [
    {{
      "application_id": "對到的人選 id，不確定填 null",
      "matched_name": "對到的姓名，不確定填 null",
      "confidence": "high|low",
      "stage": "SUBMITTED|INTERVIEWING|OFFER_ACCEPTED|ONBOARDED|CLOSED_LOST|null",
      "date": "YYYY-MM-DD 或 null",
      "note": "把顧問對這一位講的話整理成一句給日後查的紀錄，保留關鍵事實",
      "question": "認不出這一位時要問的話；沒問題填 null"
    }}
  ]
}}

⚠️ 整則都看不懂、或完全沒提到任何人選時，`items` 給空陣列。
⚠️ **只輸出顧問真的有講到的人**。引用區塊裡出現但他沒加註解的，不要輸出。
"""

# stage 顯示用的中文標籤（給群組確認訊息與後台用，資料庫仍存英文代碼）。
STAGE_LABEL = {
    'SUBMITTED': '已送件', 'INTERVIEWING': '客戶面談',
    'OFFER_ACCEPTED': '錄取', 'ONBOARDED': '到職', 'CLOSED_LOST': '結案',
}
_ALLOWED_STAGES = set(STAGE_LABEL) | {None}


def normalize_stage(v):
    """防呆：模型偶爾還是會吐出不在清單裡的字。不在清單裡就當純備註，
    不要讓垃圾值流進 placements.stage——那比完全不寫更難查。"""
    v = (v or '').strip().upper() if isinstance(v, str) else v
    return v if v in _ALLOWED_STAGES else None


def parse(text):
    ros = '\n'.join(
        f"[id={r['id']}] {r['name']}　應徵：{r.get('job_title') or r['job_slug']}"
        f"　目前：{r.get('stage') or ('面談' + (r.get('interview_state') or '-'))}"
        for r in roster())
    import datetime
    now = datetime.datetime.now()
    wd = '一二三四五六日'[now.weekday()]
    p = PROMPT.format(text=text, roster=ros,
                      today=now.strftime('%Y-%m-%d'), weekday=f'週{wd}')
    r = subprocess.run(
        ['claude', '-p', D.sanitize(p), '--model', MODEL, *D.NO_TOOLS, '--output-format', 'text'],
        capture_output=True, text=True, env=D.env_with_cf(), timeout=300)
    out = (r.stdout or '').strip()
    s, e = out.find('{'), out.rfind('}')
    if s < 0:
        raise RuntimeError(f'看不懂總指揮的回應：{out[:200]}')
    return json.loads(out[s:e + 1])


def tg(text, buttons=None, reply_to=None):
    import urllib.request
    c = {}
    for line in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env'), encoding='utf-8'):
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            c[k.strip()] = v.strip().strip('\'"')
    payload = {'chat_id': c['TG_CHAT_ID'], 'message_thread_id': THREAD_REPORT,
               'text': text, 'disable_web_page_preview': True}
    if buttons:
        payload['reply_markup'] = buttons
    if reply_to:
        payload['reply_to_message_id'] = reply_to
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{c['TG_BOT_TOKEN']}/sendMessage",
        data=json.dumps(payload).encode(),
        headers={'content-type': 'application/json', 'user-agent': 'step1ne-report/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get('result', {}).get('message_id')
    except Exception as e:
        log(f'⚠️ Telegram 推送失敗：{e}')
        return None


def handle(row):
    rid = row['id']
    try:
        spec = parse(row['raw_text'])
    except Exception as e:
        D.d1(f"UPDATE consultant_reports SET status='failed', proposed_note={D.q(str(e)[:200])}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg(f'⚠️ 這則我看不懂，麻煩你直接跟我說是哪一位、發生什麼事：\n「{row["raw_text"][:60]}」',
           reply_to=row['tg_message_id'])
        return

    items = spec.get('items') or []
    ok, unclear = [], []
    for it in items:
        it['stage'] = normalize_stage(it.get('stage'))
        # 🚨 認不出人就問，不猜（Jacky 2026-08-12 選的 A）
        if it.get('application_id') and it.get('confidence') == 'high':
            ok.append(it)
        else:
            unclear.append(it)

    if not items:
        D.d1(f"UPDATE consultant_reports SET status='unclear', "
             f"parse_json={D.q(json.dumps(spec, ensure_ascii=False))}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg('❓ 這則我沒看出是在講哪一位人選，方便再說一次嗎？', reply_to=row['tg_message_id'])
        log(f'  ❓ 沒認出任何人選：{row["raw_text"][:40]}')
        return

    lines = []
    for it in ok:
        line = f'📌 {it.get("matched_name")}'
        if it['stage']:
            line += f'　→ {STAGE_LABEL.get(it["stage"], it["stage"])}' + (f'（{it.get("date")}）' if it.get('date') else '')
        else:
            line += '　（只記備註，不改狀態）'
        lines.append(line)
        lines.append(f'　　{it.get("note") or ""}')
    if unclear:
        lines.append('')
        lines.append('❓ 這幾位我認不出來，麻煩再說一次全名：')
        for it in unclear:
            lines.append('　・' + (it.get('question') or it.get('note') or '（不確定是誰）'))

    D.d1(f"""UPDATE consultant_reports SET parse_json={D.q(json.dumps(spec, ensure_ascii=False))},
              matched_name={D.q('、'.join(str(i.get('matched_name')) for i in ok) or None)},
              proposed_stage={D.q('、'.join(str(i['stage']) for i in ok if i['stage']) or None)},
              updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}""")

    if not ok:
        D.d1(f"UPDATE consultant_reports SET status='unclear' WHERE id={D.q(rid)}")
        tg('\n'.join(lines), reply_to=row['tg_message_id'])
        log('  ❓ 全部認不出來，已回問')
        return

    lines.append('')
    lines.append('這樣對嗎？')
    buttons = {'inline_keyboard': [[
        {'text': f'✅ 對，寫進去（{len(ok)} 位）', 'callback_data': f'cr_ok:{rid}'},
        {'text': '❌ 不對', 'callback_data': f'cr_no:{rid}'},
    ]]}
    mid = tg('\n'.join(lines), buttons=buttons, reply_to=row['tg_message_id'])
    D.d1(f"UPDATE consultant_reports SET status='asked', ask_message_id={mid or 'NULL'} "
         f"WHERE id={D.q(rid)}")
    log(f'  ✅ 已送確認：{len(ok)} 位可寫入' + (f'，{len(unclear)} 位待釐清' if unclear else ''))


def main():
    rows = D.d1("SELECT * FROM consultant_reports WHERE status='new' ORDER BY created_at LIMIT 5")
    if not rows:
        return
    log(f'撿到 {len(rows)} 則顧問回報')
    for r in rows:
        claim = D.d1_raw(f"UPDATE consultant_reports SET status='claimed', worker_id={D.q(WORKER_ID)} "
                          f"WHERE id={D.q(r['id'])} AND status='new'")
        if not claim.get('meta', {}).get('changes'):
            continue  # 已經被別台裝置搶走
        try:
            handle(r)
        except Exception as e:
            log(f'❌ {r["id"][:8]} 失敗：{e}')
            D.d1(f"UPDATE consultant_reports SET status='failed', updated_at=datetime('now','+8 hours') "
                 f"WHERE id={D.q(r['id'])}")


if __name__ == '__main__':
    main()
