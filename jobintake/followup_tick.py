#!/usr/bin/env python3
"""開發信的追信提醒。到期就把寫好的追信推到 Telegram 讓顧問按一下寄出。

## 為什麼要有這支

B2B 開發的回覆**大多發生在第二、第三封**。第一封沒回就放掉，等於前面找公司、
查窗口、寫信的工都白做。但人不會記得「五天前那封要追了」——所以一定要系統記。

追信的內容在寄第一封的時候就已經寫好存進 `bd_outreach.followup1/followup2`
（依 references/開發信框架.md 的規則產的），這支只負責**到期提醒**，不重新產文。

## ⛔ 停止追信的四種情況（一定要擋，追下去會很失禮）

1. **對方回信了** → bd_replies 有紀錄
2. **信根本沒送到** → delivery_status 是 bounced／complained
3. **顧問手動喊停** → followup_stopped 有值
4. **第二封追完了** → 框架明訂「Follow-up 2 後原則上停止主動追信」

## 節奏（Jacky 定的，見框架文件）

第一封寄出 → 5 個工作天 → 追信 1 → 再約 7 天 → 追信 2 → 停
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

import tg_bd  # noqa: E402


def log(m):
    print(m, flush=True)


# 「還可以追」的共同條件。寫成一段共用，不要在兩個查詢裡各寫一份——
# 寫兩份的那天一定會有一邊漏掉停止條件，然後追信就寄給已經回過信的人。
_ALIVE = """
      o.status='sent'
  AND COALESCE(o.followup_stopped,'')=''
  AND COALESCE(o.delivery_status,'') NOT IN ('bounced','complained')
  AND NOT EXISTS (SELECT 1 FROM bd_replies r WHERE r.outreach_id = o.id)
"""


def due_followups():
    """回 [(第幾封, 那一列)]，到期且還沒寄的。"""
    rows1 = D.d1(f"""
        SELECT o.* FROM bd_outreach o
         WHERE {_ALIVE}
           AND o.followup1_due IS NOT NULL
           AND o.followup1_sent_at IS NULL
           AND o.followup1_due <= datetime('now','+8 hours')
         ORDER BY o.followup1_due LIMIT 20""")
    rows2 = D.d1(f"""
        SELECT o.* FROM bd_outreach o
         WHERE {_ALIVE}
           AND o.followup1_sent_at IS NOT NULL
           AND o.followup2_due IS NOT NULL
           AND o.followup2_sent_at IS NULL
           AND o.followup2_due <= datetime('now','+8 hours')
         ORDER BY o.followup2_due LIMIT 20""")
    return [(1, r) for r in rows1] + [(2, r) for r in rows2]


def main():
    items = due_followups()
    if not items:
        log('沒有到期的追信')
        return

    log(f'到期 {len(items)} 封追信')
    for n, r in items:
        body = r.get(f'followup{n}')
        if not (body or '').strip():
            # 沒有寫好的追信內容就不要硬推——推一則空的提醒只會讓顧問按了發現沒東西。
            # 標記停止並講清楚原因，不要每天重複提醒同一筆。
            D.d1(f"UPDATE bd_outreach SET followup_stopped='沒有預先寫好的追信內容' "
                 f"WHERE id={D.q(r['id'])}")
            log(f"⚠️ {r['company']}：沒有追信{n}的內容，已停止追信")
            continue

        days = '5 個工作天' if n == 1 else '約一週'
        head = (f"📮 該追信了（第 {n} 封）\n\n"
                f"公司：{r['company']}\n"
                f"窗口：{r.get('contact_name') or '—'}　{r.get('contact_email')}\n"
                f"第一封寄出：{str(r.get('sent_at') or '')[:16]}"
                + (f"（已送達）" if r.get('delivery_status') == 'delivered' else '')
                + (f"（對方開過 {r.get('open_count')} 次）" if (r.get('open_count') or 0) else '')
                + f"\n距今：{days}\n\n"
                  f"內容已經寫好了，看一下沒問題就按寄出。")
        tg_bd.send_head(head)
        mid = tg_bd.send_letter(
            r['id'],
            {'company': r['company'], 'contact_name': r.get('contact_name'),
             'contact_email': r.get('contact_email'),
             'subject': f"Re: {r.get('subject') or ''}", 'body': body},
            has_cv=False,
            prefix=f'fu{n}')
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(r['id'])}")
        log(f"📨 已提醒：{r['company']} 追信{n}")


if __name__ == '__main__':
    main()
