#!/usr/bin/env python3
"""step1ne_medic.py — Step1ne 系統修復員（2026-08-26）

為什麼要這支：
    阿財（面談）跟阿福（健檢）是常駐程序，掛掉了沒有任何人會知道，
    直到有候選人反映「連結點了沒反應」。今天已經實際踩到兩次：
      · job-intake 的 AI 擬稿 8/19 崩潰，卡在 drafting 狀態整整 7 天沒人發現
      · 候選人的測驗連結被路徑衝突擋掉回 404，也是候選人講了才知道
    這兩次都是「系統靜靜壞掉、看起來像正常」——最難查的那種。

它跟一般監控的差別：**能修的直接修，不要只會叫。**

  ① 自己能修的直接修（SELF_HEALED）
     判準：可逆、不涉及對外、不涉及金錢、失敗了也只是回到原狀。
     例如重啟掛掉的常駐程序、把卡住的工單退回可重試的狀態。

  ② 修不了的才推 Telegram 找人（NEEDS_HUMAN）
     例如候選人履歷根本讀不到（要請他重傳，只有人能做這件事）。

刻意不做的事：
  · 不碰對外寄信、不代替顧問做決定
  · 不改候選人的面談內容或報告
  · 不推論、不假裝修好——修不了就誠實標 CANNOT_FIX
  · 沒事就安靜（不是每天都推一則「今天很好」洗版）

用法：
    python3 step1ne_medic.py           # 正常跑（會真的修）
    python3 step1ne_medic.py --dry     # 只檢查印出來，不動任何東西
"""
from __future__ import annotations
import argparse
import importlib.util
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

THREAD_SYSTEM = 1360   # #系統通知

# 常駐程序：掛了就重啟（launchd 的 KeepAlive 理論上會自己拉起來，
# 但 8/19 那次證明「程序活著但卡死」launchd 看不出來，所以這裡自己判斷）
DAEMONS = [
    ('阿財（面談）', 'interview_daemon.py', 'com.aijob.interview'),
    ('阿福（履歷健檢）', 'checkup_daemon.py', 'com.step1ne.checkup'),
]

# 排程：改成直接問 launchctl「這支還在不在」，不看 log 時間。
# ⚠️ 為什麼不看 log：這幾支都是「沒事就不寫 log」的設計（沒有新收件單、
# 沒有待解析履歷就安靜結束），log 很久沒更新是正常的，拿它當健康指標
# 會一直誤報「排程停擺」——2026-08-26 第一次跑就誤報了兩支。
# 真正該擔心的是「排程根本沒被 launchd 載入」，那個 launchctl 查得到。
SCHEDULED_LABELS = [
    ('初篩', 'com.aijob.screening'),
    ('履歷解析', 'com.aijob.resumeparse'),
    ('用人需求表匯入', 'com.step1ne.portalimport'),
    ('職缺擬稿', 'com.aijob.jobintake'),
    ('人選配對', 'com.aijob.matchworker'),
]
LOGDIR = os.path.expanduser('~/aijob-automation/logs')

FIXED, ALERTS = [], []


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def fixed(what, how):
    FIXED.append((what, how))
    log(f'🔧 修好了：{what} → {how}')


def alert(what, why, todo):
    ALERTS.append((what, why, todo))
    log(f'⚠️ 需要人處理：{what}｜{why}')


# ── ① 常駐程序活著嗎 ────────────────────────────────────────────
def check_daemons(dry):
    for name, script, label in DAEMONS:
        r = subprocess.run(['pgrep', '-f', script], capture_output=True, text=True)
        if r.stdout.strip():
            continue
        if dry:
            log(f'（--dry）{name} 沒在跑，正常情況會重啟 {label}')
            continue
        subprocess.run(['launchctl', 'kickstart', '-k', f'gui/{os.getuid()}/{label}'],
                       capture_output=True)
        time.sleep(3)
        r2 = subprocess.run(['pgrep', '-f', script], capture_output=True, text=True)
        if r2.stdout.strip():
            fixed(f'{name} 沒在跑', f'已重啟（PID {r2.stdout.strip().splitlines()[0]}）')
        else:
            alert(f'{name} 掛了而且重啟失敗', 'launchctl kickstart 之後程序還是沒起來',
                  f'到終端機跑：launchctl kickstart -k gui/{os.getuid()}/{label}，看錯誤訊息')


# ── ② 職缺擬稿有沒有卡在 drafting ──────────────────────────────
def check_stuck_intakes(dry):
    """8/19 撞過：擬稿逾時崩潰，狀態停在 drafting，而排程只認得
    new/rewrite/approved，這筆就永遠不會被再撿起來。程式碼那邊已經加了
    例外處理，但萬一又用別的方式崩潰（例如整台機器斷電），這裡當第二道保險。"""
    rows = D.d1("SELECT id, client_name, updated_at FROM job_intakes WHERE status='drafting'")
    for r in rows:
        ts = r.get('updated_at') or ''
        try:
            age_min = (datetime.now() - datetime.strptime(ts[:19], '%Y-%m-%d %H:%M:%S')).total_seconds() / 60
        except Exception:
            age_min = 999999
        if age_min < 30:      # 正常擬稿要幾分鐘，30 分鐘內算還在跑
            continue
        who = r.get('client_name') or r['id'][:8]
        if dry:
            log(f'（--dry）{who} 卡在擬稿 {int(age_min)} 分鐘，正常情況會退回 new 重試')
            continue
        D.d1(f"UPDATE job_intakes SET status='new' WHERE id={D.q(r['id'])}")
        fixed(f'職缺擬稿卡住：{who}', f'卡了 {int(age_min//60)} 小時，已退回待處理，下一輪排程會重試')


# ── ③ 面談鎖有沒有卡住 ────────────────────────────────────────
def check_stuck_locks(dry):
    """阿財處理一輪對話時會上鎖，正常幾十秒就解開。鎖過期了還在，
    代表那一輪中途死掉——鎖不清掉的話這個候選人再打字阿財不會回應。"""
    now_s = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    rows = D.d1(f"SELECT id, name FROM applications WHERE lock_expires_at IS NOT NULL "
                f"AND lock_expires_at < '{now_s}'")
    for r in rows:
        if dry:
            log(f'（--dry）{r["name"]} 的面談鎖過期沒清掉，正常情況會清掉')
            continue
        D.d1(f"UPDATE applications SET lock_expires_at=NULL WHERE id={D.q(r['id'])}")
        fixed(f'面談鎖卡住：{r["name"]}', '鎖已過期但沒清掉，已清除，候選人可以繼續對話')


# ── ④ 排程有沒有很久沒動 ──────────────────────────────────────
def check_schedules(dry):
    """排程有沒有被 launchd 載入。掉出清單＝這支從此不會再跑，
    而且不會有任何錯誤訊息——最安靜的故障。重新載入是可逆的，可以自己修。"""
    r = subprocess.run(['launchctl', 'list'], capture_output=True, text=True)
    loaded = r.stdout
    for name, label in SCHEDULED_LABELS:
        if label in loaded:
            continue
        plist = os.path.expanduser(f'~/Library/LaunchAgents/{label}.plist')
        if not os.path.exists(plist):
            alert(f'{name} 的排程設定檔不見了', f'找不到 {label}.plist',
                  '這支排程等於被刪掉了，要確認是不是刻意停用的')
            continue
        if dry:
            log(f'（--dry）{name} 沒被載入，正常情況會重新載入 {label}')
            continue
        subprocess.run(['launchctl', 'load', plist], capture_output=True)
        r2 = subprocess.run(['launchctl', 'list'], capture_output=True, text=True)
        if label in r2.stdout:
            fixed(f'{name} 排程沒被載入', '已重新載入，之後會照原本的時間跑')
        else:
            alert(f'{name} 排程載入失敗', f'launchctl load {label}.plist 之後還是沒出現',
                  '到終端機手動跑一次看錯誤訊息')


# ── ⑤ 有沒有候選人的履歷讀不到 ────────────────────────────────
def check_unreadable_resumes(dry):
    """這個修不了——履歷是候選人自己傳的，只有請他重傳一途。
    但至少要主動講，不要等阿財面談當下才發現看不到履歷。"""
    # 同一個人重複投遞會有好幾筆，只報最新那筆——不然同一個人洗版好幾則
    rows = D.d1("SELECT a.id, a.name, MAX(a.created_at) AS latest FROM applications a "
                "WHERE a.resume_file_id IS NULL AND a.resume_url IS NOT NULL "
                "AND a.resume_url LIKE '%drive.google.com/drive/folders%' "
                "AND a.interview_state='not_started' AND a.superseded_by IS NULL "
                "GROUP BY a.name")
    for r in rows:
        alert(f'{r["name"]} 的履歷讀不到',
              '貼的是 Google Drive「資料夾」連結，系統只能讀單一檔案',
              '請他改上傳 PDF 檔，或改貼單一檔案的分享連結')


# ── ⑥ 資料庫說開著的職缺，官網上真的看得到嗎 ──────────────────
def check_site_drift(dry):
    """官網的職缺頁是實體 HTML 檔（為了速度跟 SEO），不是即時從資料庫讀的。
    所以「資料庫改了」跟「官網更新了」是兩件事，中間要靠人手動跑發布腳本。
    漏跑的下場：系統認為職缺開著（阿財願意收應徵、客戶 portal 顯示招募中），
    但求職者在官網上根本找不到它——2026-08-26 律准的資深職業安全衛生工程師
    就是這樣，資料填完了、狀態是開的，官網上卻完全沒有這個職缺。

    ⚠️ status 有 open 跟 active 兩個值意思一樣（歷史遺留，兩個都有程式在讀），
    這裡兩個都當成「應該要在官網上看得到」。
    """
    site = os.path.expanduser('~/下載項目/step1ne-stopgap-site')
    if not os.path.isdir(site):
        return
    rows = D.d1("SELECT slug, title FROM jobs WHERE COALESCE(status,'open') IN ('open','active')")
    live = {r['slug']: (r.get('title') or r['slug']) for r in rows}

    jobs_dir = os.path.join(site, 'jobs')
    pages = {p for p in os.listdir(jobs_dir)
             if os.path.isfile(os.path.join(jobs_dir, p, 'index.html'))}
    try:
        import json as _json
        apply_slugs = {j['slug'] for j in _json.load(open(os.path.join(site, 'apply', 'jobs.json')))}
    except Exception:
        apply_slugs = set()
    idx = open(os.path.join(jobs_dir, 'index.html'), encoding='utf-8').read()

    for slug, title in live.items():
        missing = []
        if slug not in pages:
            # 職缺頁根本不存在——這個修不了，產生一個職缺頁需要完整的 JD 內容
            # 與禁刊過濾流程，不是搬資料而已，亂生一頁比沒有更糟。
            alert(f'「{title}」在官網上不存在',
                  '資料庫狀態是開放招募，但官網沒有這個職缺頁，求職者看不到也應徵不了',
                  f'跑：cd ~/工作流程技能包/step1ne-recruit && python3 publish_job.py <職缺規格.json>')
            continue
        if slug not in apply_slugs:
            missing.append('應徵表單下拉')
        if f'/jobs/{slug}/" data-track' not in idx:
            missing.append('職缺列表卡片')
        if missing:
            # 頁面已經存在，只是沒被列進清單——這是純資料同步，可以自己修
            alert(f'「{title}」沒出現在{"、".join(missing)}',
                  '職缺頁存在但沒被列進清單，求職者要有直接網址才找得到',
                  '跑一次 publish_job.py 重新產生清單，或手動補進 apply/jobs.json')

    # 反過來：官網有頁面，但資料庫不認為它是開的
    for slug in sorted(pages - set(live)):
        row = D.d1(f"SELECT status FROM jobs WHERE slug={D.q(slug)}")
        st = row[0]['status'] if row else '（資料庫沒有這筆）'
        if st == 'closed':
            continue   # 關閉的職缺官網會自動隱藏，這是正常的
        alert(f'官網有「{slug}」的頁面，但資料庫狀態是 {st}',
              '頁面對外看得到，但系統不認為它在招募中，兩邊講的不一樣',
              '確認這個職缺到底還在不在招募，把狀態改對或把頁面下架')


# 這幾家公司的員工一律不得列為候選人。理由不是「不合適」，是商業關係——
# 他們是我方案子的要派單位或客戶的客戶，去挖他們的人等於在自己的客戶身上動手。
#
# ⚠️ 為什麼放在修復員而不是爬蟲裡：爬蟲不只一支（talent_sourcing_agent.py、
#    舊的 sourcing_engine.py、以後還會有），而且正在被別的工作階段同時修改。
#    在每一支裡各加一次過濾，遲早有一支漏掉——而漏掉的代價是顧問真的去
#    接觸了不該接觸的人，那是撤不回來的。放在這裡是最後一道網：不管誰撈進來的，
#    半小時內一定會被攔下。爬蟲端的過濾還是該做，這裡不取代它。
BLOCKED_EMPLOYERS = [
    # (比對關鍵字, 為什麼)
    ('帆宣', 'bim-engineer-tongluo 案的要派單位，2026-08-26 顧問裁示一律不得接觸'),
    ('Marketech', '同上（帆宣系統科技英文名）'),
    ('台灣美光', '律准科技的終端客戶，去敲等於跟自己的客戶搶人'),
    ('美光科技', '同上'),
    # ⚠️ 2026-08-26 首跑就漏掉 5 人：池子裡同一家公司同時存在中文名（美光科技）
    #    與英文名（Micron Technology），只列中文等於只擋一半。
    #    以後加封鎖公司，中英文名一定要同時列。
    ('Micron', '同上（美光英文名）'),
]


def check_blocked_employers(dry):
    """封鎖公司的員工被撈進人才池 → 自動標為不合適並註明原因。

    自動修，不只通知——這是可逆的（改一個 status 欄位），而且留在池子裡
    每多一天，就多一次被顧問撈出來接觸的機會。
    """
    for kw, why in BLOCKED_EMPLOYERS:
        rows = D.d1(
            "SELECT id, name, company FROM sourced_candidates "
            f"WHERE company LIKE {D.q('%' + kw + '%')} AND status <> 'rejected'"
        ) or []
        if not rows:
            continue
        names = '、'.join((r.get('name') or '?') for r in rows[:5])
        more = f'…等 {len(rows)} 人' if len(rows) > 5 else ''
        if dry:
            log(f'（--dry）會排除：{kw} 的 {names}{more}')
            continue
        D.d1(
            "UPDATE sourced_candidates SET status='rejected', reject_reason='其他', "
            f"note = COALESCE(note || ' / ', '') || {D.q('封鎖公司自動排除：' + why)} "
            f"WHERE company LIKE {D.q('%' + kw + '%')} AND status <> 'rejected'"
        )
        FIXED.append((f'人才池撈到封鎖公司「{kw}」的人：{names}{more}',
                      f'已自動標為不合適並註明原因（{why}）。'
                      f'⚠️ 同時代表某一支爬蟲沒有過濾這家，請補在來源端。'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只檢查不修')
    a = ap.parse_args()

    for fn in (check_daemons, check_stuck_intakes, check_stuck_locks,
               check_schedules, check_unreadable_resumes, check_site_drift,
               check_blocked_employers):
        try:
            fn(a.dry)
        except Exception as e:
            log(f'❌ {fn.__name__} 檢查本身出錯：{e}')

    if not FIXED and not ALERTS:
        log('✅ 一切正常，不推播')
        return

    lines = ['🩺 Step1ne 系統巡檢']
    if FIXED:
        lines.append(f'\n已自動修復 {len(FIXED)} 項')
        for what, how in FIXED:
            lines.append(f'· {what}\n  → {how}')
    if ALERTS:
        lines.append(f'\n⚠️ 需要人處理 {len(ALERTS)} 項')
        for what, why, todo in ALERTS:
            lines.append(f'· {what}\n  原因：{why}\n  怎麼辦：{todo}')

    msg = '\n'.join(lines)
    print('\n' + msg)
    if not a.dry:
        D.tg(msg, THREAD_SYSTEM)


if __name__ == '__main__':
    main()
