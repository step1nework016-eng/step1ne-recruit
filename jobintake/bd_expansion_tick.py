#!/usr/bin/env python3
"""既有客戶擴單提醒：已簽約客戶太久沒有新職缺，定期提醒去問一下。

## 為什麼不能直接拿 jobs.updated_at 判斷

2026-09-24 建這支之前查過：jobs.updated_at 在 2026-09-21 21:19-21:22
被一支批次腳本逐筆改過 32 筆（欄位補值之類的維護動作），不是客戶真的
來了新職缺。照它判斷會整批誤判成「客戶最近才有新職缺」。

改用兩個「真正的動作時間」：
- portal_imports.created_at —— 客戶自己在後台送單的時間
- job_intakes.created_at —— 顧問手動建單的時間
這兩張表沒被那次批次動作碰過，日期才可信（confidence='reliable'）。

只有 jobs 表裡查得到職缺、但兩張時間表都查不到的，只能確認「這個客戶
系統裡至少有過職缺」，不知道正確日期，標記 confidence='estimated'，
**不能**拿去跟 60 天比，只能用「系統裡完全沒有職缺紀錄」這件事本身來提醒。

## 別名比對是手動維護的

client_companies.aliases 目前只有幾家填了，而且 jobs.client_name 常常跟
client_companies.display_name 用字不完全一樣（例：客戶登記全名裡「相邦」
在 jobs 表被打成「向邦」）。下面 ALIAS_HINTS 是 2026-09-24 人工比對過一次
的結果，之後客戶名單增加要記得補，不會自動長出新的對應關係。
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import d1_http as D  # noqa: E402
import tg_bd  # noqa: E402

REMIND_AFTER_DAYS = 60          # 這麼久沒有可信的新職缺紀錄 → 提醒
RENOTIFY_AFTER_DAYS = 14        # 提醒過的，至少隔這麼久才會再提醒一次（避免洗版）

# client_companies.id -> 在 jobs.client_name / job_intakes.client_name 裡實際會用到的字串（不含 client_companies.display_name 本身，那個一定會試）
ALIAS_HINTS = {
    'co_7bdfa41b': ['美德向邦（美德醫療集團）', '美德向邦股份有限公司'],  # 美德醫療（美德相邦集團）——jobs 表打成「向邦」
    'co_3a53d52b': ['海德生貿易（Indian Motorcycle 台灣總代理）'],
    'co_2373154e': ['內湖恆新復健科診所'],
    'co_a732b59e': ['遊戲橘子集團', '遊戲橘子集團旗下電子支付公司', '遊戲橘子集團旗下餐飲品牌'],
}


def now_str():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def days_since(ts):
    if not ts:
        return None
    try:
        d = datetime.datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return (datetime.datetime.now() - d).days


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def names_for(client_id, display_name):
    names = [display_name] + ALIAS_HINTS.get(client_id, [])
    return list(dict.fromkeys(n for n in names if n))


def best_signal(client_id, display_name):
    """回傳 (last_job_at, source, confidence, has_any_job)。"""
    names = names_for(client_id, display_name)
    in_clause = ','.join(q(n) for n in names)

    reliable_at = None
    source = None

    r = D.query(f"SELECT MAX(created_at) t FROM portal_imports WHERE company_id={q(client_id)}")
    t = (r['results'] or [{}])[0].get('t')
    if t:
        reliable_at, source = t, 'portal_imports'

    r = D.query(f"SELECT MAX(created_at) t FROM job_intakes WHERE client_name IN ({in_clause})")
    t = (r['results'] or [{}])[0].get('t')
    if t and (reliable_at is None or t > reliable_at):
        reliable_at, source = t, 'job_intakes'

    # ⚠️ 2026-09-24 總指揮抓到的 bug：一開始只用 client_name 對 jobs 表，
    # 結果弘昌、宇泰華這兩家明明有 open 的職缺（bim-engineer-tongluo、
    # ai-agent-designer-nangang 等），卻被判成「系統裡完全沒有職缺」——
    # 查了才發現這些職缺的 client_name 是 NULL，只有 jobs.company_id 填了。
    # 全表統計：43/46 筆有 company_id、只有 34/46 筆有 client_name，
    # company_id 才是比較完整的關聯欄位。改成兩邊都查，任一邊有算有。
    r = D.query(f"SELECT COUNT(*) c FROM jobs WHERE company_id={q(client_id)}")
    has_any_job = (r['results'] or [{}])[0].get('c', 0) > 0
    if not has_any_job:
        r = D.query(f"SELECT COUNT(*) c FROM jobs WHERE client_name IN ({in_clause})")
        has_any_job = (r['results'] or [{}])[0].get('c', 0) > 0

    if reliable_at:
        return reliable_at, source, 'reliable', has_any_job
    if has_any_job:
        return None, 'jobs_table_only', 'estimated', True
    return None, 'none', 'unknown', False


def upsert(client_id, client_name, last_job_at, source, confidence, status, note, last_reminded_at):
    D.query(f"""
        INSERT INTO bd_expansion_watch
            (client_id, client_name, last_job_at, last_job_source, confidence, status, last_reminded_at, note, updated_at)
        VALUES
            ({q(client_id)}, {q(client_name)}, {q(last_job_at)}, {q(source)}, {q(confidence)}, {q(status)}, {q(last_reminded_at)}, {q(note)}, {q(now_str())})
        ON CONFLICT(client_id) DO UPDATE SET
            client_name=excluded.client_name, last_job_at=excluded.last_job_at,
            last_job_source=excluded.last_job_source, confidence=excluded.confidence,
            status=excluded.status, note=excluded.note, updated_at=excluded.updated_at
    """)


def mark_reminded(client_id):
    D.query(f"UPDATE bd_expansion_watch SET status='reminded', last_reminded_at={q(now_str())}, updated_at={q(now_str())} WHERE client_id={q(client_id)}")


def existing_state():
    r = D.query('SELECT client_id, status, last_reminded_at FROM bd_expansion_watch')
    return {row['client_id']: row for row in r['results']}


def run(dry_run=False):
    clients = D.query("SELECT id, display_name FROM client_companies WHERE relation='signed'")['results']
    prior = existing_state()

    due_reliable = []      # (name, days, last_job_at)
    due_no_job = []        # (name,)
    watch_estimated = []   # (name,) — 有職缺但日期不可信，僅供參考不觸發提醒

    for c in clients:
        cid, name = c['id'], c['display_name']
        last_job_at, source, confidence, has_any_job = best_signal(cid, name)

        if confidence == 'reliable':
            d = days_since(last_job_at)
            status = 'due' if d is not None and d >= REMIND_AFTER_DAYS else 'ok'
        elif confidence == 'unknown':
            status = 'due'   # 系統裡從來沒有職缺紀錄，直接算 due
            d = None
        else:
            status = 'ok'    # estimated：資料不可信，先不觸發提醒，只列出來給人看
            d = None

        prev = prior.get(cid, {})
        can_renotify = True
        if prev.get('last_reminded_at'):
            rd = days_since(prev['last_reminded_at'])
            can_renotify = rd is None or rd >= RENOTIFY_AFTER_DAYS

        note = None
        if status == 'due' and can_renotify:
            if confidence == 'reliable':
                due_reliable.append((name, d, last_job_at))
            else:
                due_no_job.append((name,))
        elif confidence == 'estimated':
            watch_estimated.append((name,))

        if not dry_run:
            upsert(cid, name, last_job_at, source, confidence, status, note, prev.get('last_reminded_at'))

    lines = [f"📈 <b>既有客戶擴單提醒</b>（{datetime.date.today().isoformat()}）"]

    if due_reliable or due_no_job:
        lines.append("\n🔔 <b>該回頭問一下的客戶</b>（沒有指定負責顧問，請認領）")
        for name, d, last_at in due_reliable:
            lines.append(f"• {tg_bd._esc(name)}——最後一次有新職缺是 {last_at[:10]}，{d} 天沒新職缺了")
        for (name,) in due_no_job:
            lines.append(f"• {tg_bd._esc(name)}——系統裡查不到任何職缺紀錄，簽約後可能從來沒開過缺，或是名稱對不上系統，建議先確認")
    else:
        lines.append("\n沒有需要提醒的客戶。")

    if watch_estimated:
        names = '、'.join(tg_bd._esc(n) for (n,) in watch_estimated)
        lines.append(
            f"\n⚠️ <b>資料不足，暫不列入提醒</b>：{names}\n"
            f"系統裡查得到職缺，但因為職缺表的時間欄位在 9/21 被批次動作改過、"
            f"不可信，抓不到正確的「最後一次新職缺」日期，所以先不觸發提醒，"
            f"避免報錯的天數。之後這幾家一旦透過後台送新職缺，時間就會準確。"
        )

    text = '\n'.join(lines)

    if dry_run:
        print(text)
        return

    tg_bd.send_head(text)
    for name, d, last_at in due_reliable:
        # 找回 client_id 來更新提醒時間
        pass
    for c in clients:
        cid, name = c['id'], c['display_name']
        if name in [n for n, d, l in due_reliable] or name in [n for (n,) in due_no_job]:
            mark_reminded(cid)


if __name__ == '__main__':
    run(dry_run='--dry-run' in sys.argv)
