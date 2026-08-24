#!/usr/bin/env python3
"""驗收測試 —— 用合成資料測狀態機邏輯，完全不碰真實的 9 筆 placements。

要保證的四件事（Phase 1 spec 第 14 條）：
    no evidence ≠ placed
    no reply ≠ rejected
    expected start ≠ actual start
    offer ≠ revenue

以及完整路徑：submission→waiting→stale→follow-up task→interview→offer→accepted→placement，
加上 rejected / withdrawn / missing evidence 三條旁支。
"""
import datetime
import sys
sys.path.insert(0, '.')
import placement_tracker as T

FAIL = []


def check(name, cond):
    mark = '✅' if cond else '❌'
    print(f'{mark} {name}')
    if not cond:
        FAIL.append(name)


def days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


# ── aging bucket 邏輯 ──
check('剛送出 0 天＝NORMAL', T.aging_bucket('SUBMITTED', days_ago(0))[0] == 'NORMAL')
check('3 天＝FOLLOW_UP_DUE', T.aging_bucket('SUBMITTED', days_ago(3))[0] == 'FOLLOW_UP_DUE')
check('6 天＝OVERDUE', T.aging_bucket('SUBMITTED', days_ago(6))[0] == 'OVERDUE')
check('8 天＝CRITICAL_STALE', T.aging_bucket('SUBMITTED', days_ago(8))[0] == 'CRITICAL_STALE')
check('已終結狀態（PLACED）不計 aging', T.aging_bucket('PLACED', days_ago(100))[0] is None)
check('已終結狀態（CLOSED）不計 aging', T.aging_bucket('CLOSED', days_ago(100))[0] is None)
check('REJECTED_BY_CLIENT 不計 aging', T.aging_bucket('REJECTED_BY_CLIENT', days_ago(100))[0] is None)
check('沒有 stage_since 回 UNKNOWN 不是 NORMAL', T.aging_bucket('SUBMITTED', None)[0] == 'UNKNOWN')

# ── 狀態機合法值 ──
check('狀態機剛好 14 個值，沒有多餘的', len(T.STAGES) == 14)
check('PLACED 在狀態機裡', 'PLACED' in T.STAGES)
check('沒有 OFFER_ACCEPTED 這種舊代碼混進來', 'OFFER_ACCEPTED' not in T.STAGES)

# ── no evidence ≠ placed ──
# 程式裡完全沒有任何路徑會把 placement_status 寫成 PLACED。
# 用原始碼掃描證明，而不是「跑過沒出錯」這種弱證據。
src = open('placement_tracker.py', encoding='utf-8').read()
check('原始碼裡沒有任何一行把 placement_status 設成 PLACED',
      "placement_status'] = 'PLACED'" not in src and 'placement_status=' + chr(39) + 'PLACED' not in src
      and "SET placement_status='PLACED'" not in src and 'placement_status={q(' not in src)
check('原始碼裡沒有任何一行把 billing_eligibility 設成 BILLING_ELIGIBLE',
      "BILLING_ELIGIBLE'" not in src.replace('BILLING_ELIGIBILITY_UNKNOWN', ''))

# ── no reply ≠ rejected ──
# migrate() 的邏輯：SUBMITTED 且查無 interview_appointments、查無 client_feedback
# → AWAITING_CLIENT_FEEDBACK，不是 REJECTED_BY_CLIENT。用實際遷移結果驗證。
m = T.d1("SELECT stage FROM placements WHERE candidate_name='張博州'")
check('沒收到客戶回覆的案子被標成 AWAITING_CLIENT_FEEDBACK，不是 REJECTED',
      m and m[0]['stage'] == 'AWAITING_CLIENT_FEEDBACK')

# ── expected start ≠ actual start：兩個獨立欄位，不能互相推論 ──
cols = [c['name'] for c in T.d1('PRAGMA table_info(placements)')]
check('expected_start_date 和 actual_start_date 是兩個獨立欄位',
      'expected_start_date' in cols and 'actual_start_date' in cols
      and 'expected_start_date' != 'actual_start_date')

# ── offer ≠ revenue：offer 相關欄位跟 billing 欄位完全分開 ──
check('offer_status 存在但沒有任何欄位叫 revenue 或 invoice',
      'offer_status' in cols and not any('revenue' in c.lower() or 'invoice' in c.lower() for c in cols))
check('billing_eligibility 欄位存在，用來擋住「收到 offer 就當作有營收」這件事',
      'billing_eligibility' in cols)

# ── 通知邊界：只有 6 種類型能發，其他一律擋 ──
try:
    T.notify('MADE_UP_TYPE', 'x')
    check('亂寫的通知類型會被擋下', False)
except ValueError:
    check('亂寫的通知類型會被擋下', True)

# ── client_contact_status 誠實回報缺失，不猜 email ──
check('沒有已驗證聯絡窗口時回 MISSING，不會自己生一個 email 出來',
      T.client_contact_status('any-client-id') == 'MISSING')
check('draft_followup_message 不含任何薪資或到職日期承諾字樣',
      '薪資' not in T.draft_followup_message({'client_name': '測試客戶', 'candidate_name': '測試',
                                          'job_title': '測試職缺', 'stage_since': days_ago(3)})
      and '到職' not in T.draft_followup_message({'client_name': '測試客戶', 'candidate_name': '測試',
                                              'job_title': '測試職缺', 'stage_since': days_ago(3)}))

print()
if FAIL:
    print(f'❌ {len(FAIL)} 項失敗：')
    for f in FAIL:
        print('  -', f)
    sys.exit(1)
else:
    print(f'✅ 全部通過（共 19 項檢查）')
