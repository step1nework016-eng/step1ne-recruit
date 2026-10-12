#!/usr/bin/env python3
"""E32：面談技巧四項改進（_technique_hints）——用張景勛 10/6 那場的情境（很短、連續「沒有」、「面議」）驗證。
離線、不呼叫模型。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.getcwd())
import interview_daemon as D

bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)

def conv(*pairs):
    out = []
    for r, t in pairs:
        out.append({'role': 'assistant' if r == 'A' else 'candidate', 'content': t})
    return {'conversation': out}

H = D._technique_hints

chk('對話太短（<2 個問題）→ 只帶「問了就要等回答」，沒有其他提示', H(conv(('A', '您好，先從這段問起：您在龍蓁淳做了什麼？'))) == D.ASK_WAIT_RULE)

# A. 同一題重問：預告期問了兩次
c = conv(('A', '您目前公司的離職預告期大概多久？'), ('C', '提離職到交接需要2個月'),
         ('A', '方便把預告期跟交接拆開講嗎？'), ('C', '不知道'))
h = H(c)
chk('A：預告期被列為已問 2 次、不准再問', '預告期／到職時間×2（已重複，不准再問）' in h and '同義句重問' in h, h[:200])
c1 = conv(('A', '您目前公司的離職預告期大概多久？'), ('C', '兩個月'), ('A', '那您最近一份工作做了多久？'))
chk('A：只問過一次的主題顯示 ×1，沒有「已重複」', '預告期／到職時間×1' in H(c1) and '已重複' not in H(c1))

# B. 數據分析連三層都答「沒有」
c = conv(('A', '您有沒有獨立做過一次數據分析？'), ('C', '沒有'),
         ('A', '那有沒有整理過報表給主管？'), ('C', '沒有'))
h = H(c)
chk('B：數據分析連兩次否定 → 結案、換下一題、不追第三層', '連續兩次否定' in h and '數據分析' in h and '直接換下一題' in h and '本人表示沒有' in h, h[:300])
c = conv(('A', '您有沒有獨立做過一次數據分析？'), ('C', '沒有'), ('A', '那您怎麼看銷售數字？'), ('C', '我每週會看達成率，跟去年同期比，差太多就找原因'))
chk('B：只有一次否定、之後正常回答 → 不結案', '連續兩次否定' not in H(c))
c = conv(('A', '您有沒有獨立做過一次數據分析？'), ('C', '沒有。'), ('A', '有主導過跨部門專案嗎？'), ('C', '沒有'))
chk('B：兩次否定但是不同主題 → 不誤判成同一主題結案', '連續兩次否定' not in H(c), H(c)[:200])

# C. 動機與搬遷必問（不受 acai_v2 限制）
c = conv(('A', '您目前負責哪些產品線？'), ('C', '營運與供應鏈'), ('A', '那團隊有幾個人？'), ('C', '8個'), ('A', '有沒有做過成本管控？'), ('C', '有'))
h = H(c)
chk('C：問了 3 題還沒問動機 → 提醒必問，且優先於專業追問', '離職原因／動機' in h and '推力' in h and '優先於「專業分級往上追問」' in h, h[:300])
chk('C：還沒問搬遷 → 提醒，且說明居住地不同才問搬、同區問通勤', '搬遷可行性' in h and '能不能搬過來' in h)
c = conv(('A', '您目前負責哪些產品線？'), ('C', '營運'), ('A', '為什麼想離開現在的公司？'), ('C', '公司方向調整'),
         ('A', '您現在住在哪裡？通勤過來方便嗎？'), ('C', '新北，40分鐘'))
h = H(c)
chk('C：動機、搬遷／通勤問過 → 不再提醒', '還沒問到的必問項' not in h, h[:200])
c = conv(('A', '您目前負責哪些產品線？'), ('C', '營運'), ('A', '那團隊有幾個人？'), ('C', '8個'))
chk('C：問題還少於 3 題 → 先不催（避免一開口就塞必問）', '還沒問到的必問項' not in H(c))

# D. 薪資最多兩次
c = conv(('A', '您期望的年薪大概是多少？'), ('C', '面議'), ('A', '方便給個大概的數字嗎？'), ('C', '面議'))
h = H(c)
chk('D：薪資已問 2 次 → 不准第三次、記本人不願透露', '薪資已經問過 2 次' in h and '不准第三次' in h and '本人不願透露' in h, h[:300])
c = conv(('A', '您期望的年薪大概是多少？'), ('C', '面議'), ('A', '那您為什麼想換工作？'), ('C', '想要新挑戰'))
h = H(c)
chk('D：第一次答面議 → 第二次換說法（區間選項或現職待遇）', '第二次**換說法**' in h and '現職大概待遇' in h, h[:300])
c = conv(('A', '您期望的年薪大概是多少？'), ('C', '大概250萬'), ('A', '那您為什麼想換工作？'), ('C', '想要新挑戰'))
chk('D：第一次就給了數字 → 不提示薪資換說法／上限', '薪資第一次答得很模糊' not in H(c) and '薪資已經問過 2 次' not in H(c))

# E. 英文測驗後的用語
c = conv(('A', '先看一段英文：Could you walk me through how you built the Semarang branch office from scratch and what you owned?'),
         ('C', 'I started with 1 sales and 2 technicians'), ('A', '接著問您的期望待遇？'), ('C', '55K'))
chk('E：出過英文題 → 提醒收尾不說「可以清楚表達」這類結論', '英文可以清楚表達' in H(c) and '我記下來了' in H(c))
c = conv(('A', '您目前負責哪些產品線？'), ('C', '營運'), ('A', '那團隊有幾個人？'), ('C', '8個'))
chk('E：沒出過英文題 → 不提', '英文' not in H(c))

# 張景勛式完整對話：重問預告期、數據分析三個沒有、薪資三次——到最後一輪應該一次講清楚
c = conv(('A', '您目前公司的離職預告期大概多久？'), ('C', '提離職到交接需要2個月'),
         ('A', '方便把預告期跟交接拆開講嗎？'), ('C', '不知道'),
         ('A', '您有沒有獨立做過一次數據分析？'), ('C', '沒有'),
         ('A', '那有沒有整理過報表給主管？'), ('C', '沒有'),
         ('A', '您期望的年薪大概是多少？'), ('C', '面議'),
         ('A', '方便給個大概的數字嗎？'), ('C', '面議'))
h = H(c)
chk('張景勛情境：同時帶出「預告期不准再問」「數據分析結案」「薪資不准第三次」「動機必問」',
    all(k in h for k in ('預告期／到職時間×2（已重複', '連續兩次否定', '薪資已經問過 2 次', '離職原因／動機')), h[:400])


# ══ E32-F（楊策淳 10/12）：問了要等回答；候選人要改時間就停 ══
# 1. 候選人要改時間的偵測
for t in ['可以，但面談時間需要更改', '我現在不方便，可以改天嗎', '晚點再聊可以嗎', '不好意思今天沒辦法，想改約', '現在有事，可以換個時間嗎']:
    chk(f'改時間偵測：{t}', D._reschedule_requested([{'role': 'assistant', 'content': '以上這些方便嗎？'}, {'role': 'candidate', 'content': t}]), t)
for t in ['我晚點再想想薪資', '到職時間可以再談', '好，可以', '專案時間更改過兩次，但最後都有如期交付，我當時負責跟客戶溝通新的時程並重新排定里程碑，整個過程大約三個月完成']:
    chk(f'不誤判成改時間：{t[:20]}', not D._reschedule_requested([{'role': 'assistant', 'content': '請問？'}, {'role': 'candidate', 'content': t}]), t)
chk('已經回過「我請顧問跟您約時間」就不再走快速路徑',
    not D._reschedule_requested([{'role': 'assistant', 'content': D.RESCHED_REPLY}, {'role': 'candidate', 'content': '還是想改天'}]))

# 2. 問句後面接「假設對方答案」的氣泡 → 丟掉（楊策淳那輪的原句）
m, cut = D._stop_after_question(['好的，方便說一下是時間不夠、還是想約其他時段嗎？', '如果現在時間比較緊，我們可以先聊精簡一點，重點的部分問完就好。'])
chk('問句後的「如果…先聊精簡」被丟掉', cut == 1 and m == ['好的，方便說一下是時間不夠、還是想約其他時段嗎？'], (m, cut))
m, cut = D._stop_after_question(['您預告期多久？', '加上交接，預估多久能到職？'])
chk('兩個都是問句 → 不動', cut == 0 and len(m) == 2)
m, cut = D._stop_after_question(['了解，您是主導過新店招募。', '想請教留任率怎麼拉到71%？'])
chk('先陳述再問 → 不動', cut == 0 and len(m) == 2)
m, cut = D._stop_after_question(['您預告期多久？', '（例如一個月或兩個月都可以說）'])
chk('問句後只是補充說明（不是假設答案）→ 不丟', cut == 0 and len(m) == 2, (m, cut))

# 3. 提示：永遠帶「問了就要等回答」，即使對話很短；改期後提醒不開新題
chk('對話很短也帶「問了就要等回答」', '問了就要等回答' in H(conv(('A', '您好，先問一題？'))))
c = conv(('A', '以上這些方便嗎？'), ('C', '可以，但面談時間需要更改'), ('A', D.RESCHED_REPLY), ('C', '好'))
chk('走過改期流程 → 提示不要開新的面談題目', '不要開新的面談題目' in H(c), H(c)[:200])

# 4. handle()：過時待回清單 → 略過；改時間 → 快速路徑（假依賴，不呼叫模型）
import types
def run_handle(last_role, conversation):
    calls = {'insert': [], 'tg': [], 'claude': 0}
    def fake_d1(sql, *a, **k):
        if sql.startswith('SELECT role FROM messages'):
            return [{'role': last_role}]
        if sql.startswith('INSERT INTO messages'):
            calls['insert'].append(sql)
        return []
    class T:
        def __init__(s, *a, **k): pass
        def start(s): pass
        def close(s): pass
    D.d1 = fake_d1; D._mark_host = lambda *a, **k: None; D._Transition = T
    D._post_prewarmed_opening = lambda *a, **k: False
    D._max_msg_id = lambda a: 1; D.context_for = lambda a: {'conversation': conversation}
    D.snap_signals = lambda *a, **k: None; D.log = lambda *a, **k: None
    D.tg = lambda text, *a, **k: calls['tg'].append(text)
    def no_claude(*a, **k):
        calls['claude'] += 1; raise AssertionError('不該呼叫模型')
    D.run_claude = no_claude; D._notify_cross_job_interest = lambda *a, **k: None
    D.build_prompt = lambda *a, **k: 'x'; D.talk_skill = lambda c: ''
    D.release_lock = lambda a: None
    D._busy.add('app-x')
    D.handle({'id': 'app-x', 'name': '測試', 'job_slug': 'j', 'job_title': '職缺'})
    return calls
cs = run_handle('assistant', [{'role': 'candidate', 'content': '可以，但面談時間需要更改'}])
chk('過時待回清單（最後一則已是阿財）→ 略過，不呼叫模型、不寫訊息', cs['claude'] == 0 and not cs['insert'] and not cs['tg'], cs)
cs = run_handle('candidate', [{'role': 'assistant', 'content': '以上這些方便嗎？'}, {'role': 'candidate', 'content': '可以，但面談時間需要更改'}])
chk('改面談時間 → 回固定句、不呼叫模型、TG 通知顧問',
    cs['claude'] == 0 and len(cs['insert']) == 1 and D.RESCHED_REPLY in cs['insert'][0] and len(cs['tg']) == 1 and '改面談時間' in cs['tg'][0], cs)
src2 = open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read()
chk('handle 事後擋有接上 _stop_after_question', 'msgs, _cut = _stop_after_question(msgs)' in src2)

# 實際接進 talk_prompt 的位置存在
src = open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read()
chk('talk_prompt 有接上 _technique_hints', 'talk_prompt += _technique_hints(ctx)' in src)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
