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

chk('對話太短（<2 個問題）→ 不提示', H(conv(('A', '您好，先從這段問起：您在龍蓁淳做了什麼？'))) == '')

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

# 實際接進 talk_prompt 的位置存在
src = open(os.path.join(os.getcwd(), 'interview_daemon.py'), encoding='utf-8').read()
chk('talk_prompt 有接上 _technique_hints', 'talk_prompt += _technique_hints(ctx)' in src)
print(f'\n失敗 {bad} 項'); sys.exit(1 if bad else 0)
