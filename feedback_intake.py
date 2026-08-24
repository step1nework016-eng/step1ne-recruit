#!/usr/bin/env python3
"""最小、獨立的 Feedback 入口 —— Step1ne Phase 1.1。

為什麼跟 report_tick.py 分開，不修它：
    Phase 0 查到 report_tick.py 有 D1 逾時紀錄。這次（2026-08-21）重新確認：
    D1 現在是通的（單次查詢 4.3 秒，慢但不逾時），report.log 從 8/20 18:15
    之後沒有新內容——但查證後發現根因不只是 D1，是 `consultant_reports`
    這張表本身從 **2026-08-12** 之後就沒有新資料進來，也就是九天沒有顧問
    在 Telegram 打任何回報。這是人的行為斷層，不是純粹的系統故障。

    report_tick.py 的深層問題是架構性的：靠 LLM 把自由文字硬塞進 5 個粗代碼
    （SUBMITTED/INTERVIEWING/OFFER_ACCEPTED/ONBOARDED/CLOSED_LOST），
    跟 Phase 1 新的 14 值狀態機混在一起會更亂。修好它要動的是整個 report
    系統，不是「這條 feedback path」——按規則，這種不做，改成最小獨立入口。

這支就是那個最小入口。不猜、不腦補，只做一件事：
把一個事實寫進 feedback_intake 表，並且**用嚴格規則**判斷這個事實能不能讓
placements.stage 往前推、推到哪裡——不是每一個事實都能直接變成一個新階段。

────────────────────────────────────────
硬性規則（不得放寬）
────────────────────────────────────────
    「可以約下週二下午」  → INTERVIEW_REQUESTED（有意願安排，但沒有明確日期）
                            不得標 INTERVIEW_SCHEDULED
    有明確日期＋候選人確認 → 才能標 INTERVIEW_SCHEDULED
    「有興趣」            → 不是 OFFER
    OFFER                → 不等於 CANDIDATE_ACCEPTED
    CANDIDATE_ACCEPTED   → 不等於 PLACED（PLACED 要有實際到職證據）
    沒有證據的事一律不寫，寧可留白，不可腦補。

用法：
    python3 feedback_intake.py --placement 7 --source telegram \\
        --raw "律准科技回電說下週二下午可以面試" --actor Phoebe --confidence high
    # 程式會自己判斷這句話能不能推進階段、推到哪裡，並印出理由，
    # 不會不明就裡地照單全收。
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def log(m):
    print(f'[{datetime.datetime.now():%H:%M:%S}] {m}', flush=True)


def _daemon():
    import importlib.util
    spec = importlib.util.spec_from_file_location('_idm', os.path.join(HERE, 'interview_daemon.py'))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


D = _daemon()
d1, q = D.d1, D.q

VALID_STAGES = {
    'SUBMITTED', 'AWAITING_CLIENT_FEEDBACK', 'INTERVIEW_REQUESTED', 'INTERVIEW_SCHEDULED',
    'INTERVIEW_COMPLETED', 'CLIENT_DECISION_PENDING', 'OFFER', 'CANDIDATE_ACCEPTED',
    'ONBOARDING', 'PLACED', 'REJECTED_BY_CLIENT', 'WITHDRAWN_BY_CANDIDATE', 'ON_HOLD', 'CLOSED',
}

# 事實類型 → 能不能推進、推到哪裡。這是規則表，不是模型自由判斷。
FACT_RULES = {
    'CLIENT_REPLIED':          {'next': 'CLIENT_DECISION_PENDING', 'needs': []},
    'INTERVIEW_REQUESTED':     {'next': 'INTERVIEW_REQUESTED', 'needs': []},
    'INTERVIEW_SCHEDULED':     {'next': 'INTERVIEW_SCHEDULED', 'needs': ['date', 'candidate_confirmed']},
    'INTERVIEW_COMPLETED':     {'next': 'INTERVIEW_COMPLETED', 'needs': []},
    'CLIENT_REJECTED':         {'next': 'REJECTED_BY_CLIENT', 'needs': []},
    'OFFER_RECEIVED':          {'next': 'OFFER', 'needs': []},
    # candidate 接受 offer 是完全不同的事實，不能跟「客戶發了 offer」共用一個代碼，
    # 不然候選人接受的訊息會被誤判成「客戶剛發 offer」，把已經往前的階段往回蓋掉。
    'CANDIDATE_ACCEPTED':      {'next': 'CANDIDATE_ACCEPTED', 'needs': []},
    'ON_HOLD':                 {'next': 'ON_HOLD', 'needs': []},
}


def classify(raw_text):
    """從一段自由文字判斷是哪一種事實類型。

    ⚠️ 這不是自由發揮的 NLP——關鍵字沒對到就回 None，交給人工判斷，
       不硬猜一個分類出來。日期偵測也故意寫得保守：只認得出明確日期格式，
       「下週二」「這幾天」這種相對時間**不算**明確日期，因為那不足以
       支撐 INTERVIEW_SCHEDULED 需要的「有明確日期」門檻。
    """
    t = raw_text
    has_explicit_date = bool(re.search(r'\d{1,2}[/月]\d{1,2}|\d{4}-\d{2}-\d{2}', t))
    candidate_confirmed = ('候選人確認' in t or '人選確認' in t or '已確認' in t)

    # ⚠️ 順序很重要：候選人「接受」offer 要先判斷，不然「offer」這個字
    #    會先被下面的 OFFER_RECEIVED 規則吃掉，變成「客戶剛發 offer」。
    #    兩件事完全相反，順序判斷一定要走在前面。
    if any(k in t for k in ('offer', 'Offer', 'OFFER', '錄取')) and \
            any(k in t for k in ('接受', '同意', '答應')):
        return 'CANDIDATE_ACCEPTED', {}
    if any(k in t for k in ('拒絕', '不考慮', '不要這個人選', '婉拒', '不合適')):
        return 'CLIENT_REJECTED', {}
    # ⚠️ 實測抓到的真實錯誤：「後續有錄取消息會通知我們」被誤判成「現在收到 offer 了」——
    #    那句話是未來式／條件句，不是現在式的事實。單看「錄取」「offer」這兩個字
    #    不夠，還要看有沒有「已經發生」的語氣（發了/收到/拿到），
    #    而且只要句子裡出現「後續」「之後」「如果」「若」這種條件／未來語氣詞，
    #    就不該觸發——寧可不分類、留給人工看，也不要把「將來會怎樣」講成「現在已經怎樣」。
    future_or_conditional = any(k in t for k in ('後續', '之後', '如果', '若', '未來'))
    happened_already = any(k in t for k in ('發', '收到', '拿到', '給', '已發', '確定'))
    if any(k in t for k in ('錄取', 'offer', 'Offer', 'OFFER')) and happened_already and not future_or_conditional:
        return 'OFFER_RECEIVED', {}
    if any(k in t for k in ('面試完', '面談完', '面試結束', '已完成面試', '面談過了', '面試過了', '已經面談', '已面談')):
        return 'INTERVIEW_COMPLETED', {}
    # ⚠️ 只要提到「面試/面談」且同時有明確日期，就要能判斷是不是 SCHEDULED，
    #    不能限定「約面試」「安排面試」這種固定講法——「8/25 14:00 面試，人選確認」
    #    這種直接講結果的句子第一版判斷不出來，是實測抓到的真實漏洞。
    if '面試' in t or '面談' in t:
        if has_explicit_date and candidate_confirmed:
            return 'INTERVIEW_SCHEDULED', {'date': True, 'candidate_confirmed': True}
        return 'INTERVIEW_REQUESTED', {}
    if any(k in t for k in ('暫緩', '先擱著', 'on hold', '之後再說')):
        return 'ON_HOLD', {}
    if any(k in t for k in ('回電', '回覆', '客戶說', '對方表示', '有回應')):
        return 'CLIENT_REPLIED', {}
    return None, {}


def apply_fact(placement_id, source, raw_evidence, actor, confidence, source_timestamp=None):
    row = d1(f"SELECT id, stage, candidate_name FROM placements WHERE id={placement_id}")
    if not row:
        log(f'❌ 找不到 placement #{placement_id}')
        return None
    row = row[0]

    fact_type, meta = classify(raw_evidence)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stage_change = None

    if fact_type is None:
        log(f'⚠️ 「{raw_evidence}」看不出是哪一種明確事實，只記錄不改階段，交給人工判斷')
    else:
        rule = FACT_RULES[fact_type]
        missing = [k for k in rule['needs'] if not meta.get(k)]
        if missing:
            # 規則要求的條件沒湊齊——例如「約面試」但沒有明確日期，
            # 這時候只能停在 INTERVIEW_REQUESTED，不能因為關鍵字裡有
            # 「排面談」就直接跳去 INTERVIEW_SCHEDULED。
            log(f'　「{raw_evidence}」判斷為 {fact_type}，但缺 {missing}，'
               f'降級為 INTERVIEW_REQUESTED（不足以標 SCHEDULED）')
            stage_change = 'INTERVIEW_REQUESTED'
        else:
            stage_change = rule['next']

        if stage_change and confidence == 'low':
            log(f'　信心度 low，只記錄事實，不自動改階段——低信心不該推動狀態機')
            stage_change = None

    d1(f"""INSERT INTO feedback_intake
           (id, placement_id, created_at, source, source_timestamp, raw_evidence,
            parsed_fact, actor, confidence, applied_stage_change)
           VALUES (lower(hex(randomblob(8))), {placement_id}, {q(now)}, {q(source)},
           {q(source_timestamp)}, {q(raw_evidence)}, {q(fact_type or 'UNCLASSIFIED')},
           {q(actor)}, {q(confidence)}, {q(stage_change)})""")

    if stage_change:
        d1(f"UPDATE placements SET stage={q(stage_change)}, stage_since={q(now[:10])}, "
           f"updated_at={q(now)} WHERE id={placement_id}")
        log(f'✅ {row["candidate_name"]}：{row["stage"]} → {stage_change}（依據：{fact_type}）')
    else:
        log(f'　{row["candidate_name"]}：階段維持 {row["stage"]}（事實已記錄，未改變階段）')
    return stage_change


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--placement', type=int, required=True)
    ap.add_argument('--source', required=True, help='telegram / phone_call / email / line 等')
    ap.add_argument('--timestamp', default=None)
    ap.add_argument('--raw', required=True, help='原始這句話，照抄不要摘要')
    ap.add_argument('--actor', required=True, help='誰輸入的（顧問姓名或帳號）')
    ap.add_argument('--confidence', choices=['high', 'medium', 'low'], default='medium')
    a = ap.parse_args()
    apply_fact(a.placement, a.source, a.raw, a.actor, a.confidence, a.timestamp)


if __name__ == '__main__':
    main()
