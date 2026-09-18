#!/usr/bin/env python3
"""ai_jobs 卡住工作回收機制測試（2026-09-18，QA BUG 10）

這裡測的是「AI Worker 半夜掛掉／重啟／被更新之後，工作不會永遠消失，
也不會一直重複燒額度」。

⚠️ 會對 production D1 寫入測試資料，但全部用 `__test__` 前綴的 id，
跑完自動刪乾淨，不碰任何真實工作。

跑法：python3 tests/test_ai_jobs_recovery.py
"""
import os
import sys
import uuid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import d1_http  # noqa: E402
import ai_worker as W  # noqa: E402

PASS, FAIL = [], []
TEST_PREFIX = '__test__'


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}{('  ← ' + detail) if detail and not cond else ''}")


def mk(kind, status, minutes_ago, attempts=0):
    """建一筆測試工作，started_at 設在 N 分鐘前"""
    jid = TEST_PREFIX + str(uuid.uuid4())
    d1_http.query(
        "INSERT INTO ai_jobs (id, kind, payload_json, status, created_at, started_at, attempts) VALUES ("
        f"{W.q(jid)}, {W.q(kind)}, '{{}}', {W.q(status)}, "
        f"datetime('now','+8 hours'), datetime('now','+8 hours','-{minutes_ago} minutes'), {attempts})")
    return jid


def status_of(jid):
    r = d1_http.query(f"SELECT status, attempts, error FROM ai_jobs WHERE id={W.q(jid)}")['results']
    return r[0] if r else None


def cleanup():
    d1_http.query(f"DELETE FROM ai_jobs WHERE id LIKE '{TEST_PREFIX}%'")


cleanup()
try:
    # ── CASE 2：才跑 2 分鐘的工作不可以被搶走 ──────────────────────
    j = mk('post_interview_rematch', 'running', 2)
    W.recover_stale_jobs()
    check('CASE2 才跑 2 分鐘不會被回收（避免搶走還在正常跑的工作）',
          status_of(j)['status'] == 'running', status_of(j)['status'])

    # ── CASE 2b：剛好在門檻內（19 分鐘）也不動 ─────────────────────
    j2 = mk('post_interview_rematch', 'running', 19)
    W.recover_stale_jobs()
    check('CASE2b 19 分鐘（門檻 20）仍不回收', status_of(j2)['status'] == 'running')

    # ── CASE 3：超過門檻 → 退回 pending ───────────────────────────
    j3 = mk('post_interview_rematch', 'running', 45, attempts=1)
    W.recover_stale_jobs()
    s3 = status_of(j3)
    check('CASE3 卡超過 20 分鐘 → 退回 pending 可被重新認領', s3['status'] == 'pending', s3['status'])

    # ── CASE 5：已達重試上限 → 標 failed，不再無限重跑 ─────────────
    j5 = mk('post_interview_rematch', 'running', 45, attempts=W.MAX_ATTEMPTS)
    W.recover_stale_jobs()
    s5 = status_of(j5)
    check('CASE5 達重試上限 → 標 failed 不再重試', s5['status'] == 'failed', s5['status'])
    check('CASE5b 失敗時有留下原因（方便事後查）', bool(s5['error']), str(s5['error']))

    # ── 白名單：不在名單上的 kind 絕不自動回收 ─────────────────────
    j6 = mk('call_notes_summary', 'running', 120)
    W.recover_stale_jobs()
    check('CASE6 call_notes_summary 卡 2 小時也不自動回收'
          '（重跑會 INSERT 重複的 candidate_notes）',
          status_of(j6)['status'] == 'running', status_of(j6)['status'])

    j7 = mk('client_report_synthesize', 'running', 120)
    W.recover_stale_jobs()
    check('CASE6b client_report_synthesize 不自動回收（由另一支 daemon 消費、成本高）',
          status_of(j7)['status'] == 'running')

    # ── CASE 7：兩台 Worker 同時搶 → 只有一台搶得到 ────────────────
    j8 = mk('post_interview_rematch', 'pending', 0)
    claims = []
    for worker in ('MachineA', 'MachineB'):
        r = d1_http.query(
            f"UPDATE ai_jobs SET status='running', worker_id={W.q(worker)} "
            f"WHERE id={W.q(j8)} AND status='pending'")
        claims.append((r.get('meta') or {}).get('changes') or 0)
    check('CASE7 兩台機器同時認領同一個工作 → 只有一台成功',
          claims == [1, 0], f'changes={claims}')

    # ── CASE 8：回收後的工作可以被正常認領（完整走一輪）────────────
    j9 = mk('post_interview_rematch', 'running', 45, attempts=1)
    W.recover_stale_jobs()
    r = d1_http.query(
        f"UPDATE ai_jobs SET status='running', worker_id='後來的機器' "
        f"WHERE id={W.q(j9)} AND status='pending'")
    check('CASE8 重啟後卡住的工作最終可以被另一台救起來執行',
          ((r.get('meta') or {}).get('changes') or 0) == 1)

    # ── 門檻是依真實資料設定，不是憑感覺 ──────────────────────────
    check('門檻 20 分鐘 > 實測最長執行時間（client_report 437 秒）＋ CLI 逾時（480 秒）',
          W.STALE_RUNNING_MINUTES * 60 > 480 * 2, f'{W.STALE_RUNNING_MINUTES} 分鐘')
    check('白名單只含已驗證可安全重跑的 kind',
          set(W.RECOVERABLE_KINDS) == {'post_interview_rematch', 'precall_card',
                                       'postcall_result', 'call_prep'},
          str(W.RECOVERABLE_KINDS))
finally:
    cleanup()

print(f'\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項')
if FAIL:
    print('失敗：', FAIL)
sys.exit(1 if FAIL else 0)
