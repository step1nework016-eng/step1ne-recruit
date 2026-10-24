#!/usr/bin/env python3
"""2026-10-24 謝佳恩：問了薪資→人選沒回就離開→回到面談室，阿財要先再問一次那題，不能當成「已問 2 次」跳過。離線、不呼叫模型。"""
import os, sys
sys.path.insert(0, os.getcwd())
import interview_daemon as D

bad = 0
def chk(n, c, d=''):
    global bad
    print(('✅' if c else '❌'), n, ('' if c else f'← {d}')); bad += (not c)

def conv(*pairs):
    return {'conversation': [{'role': 'assistant' if r == 'A' else 'candidate', 'content': t} for r, t in pairs]}

H, M = D._technique_hints, D.ENTER_MARK
SAL2 = '薪資的部分，學士級距是40,833～50,167元。您期望的數字大概落在這個區間的哪個位置呢？'

# 謝佳恩實際情況
c = conv(('A', '您先前在補習班主要負責哪些工作？'), ('C', '協助學生寫備審'),
         ('A', '您的期望薪資大概多少呢？'), ('C', '有大概了解職位一般薪資區間，我學習能力好'),
         ('A', SAL2),
         ('A', '看您這邊暫時沒有回覆，我先把今天談到的內容整理給顧問。'),
         ('C', M))
h = H(c)
chk('回來時有「剛回到面談室」提示', '剛回到面談室' in h, h[:300])
chk('提示帶出沒回答的那一題', '40,833' in h)
chk('沒回答那次不算數 → 不出現「薪資不准第三次」', '不准第三次' not in h, h)
chk('明講不要留給顧問跳過', '留給顧問' in h)

# 回來後已經回答 → 不再提示
c2 = conv(*[(r, t) for r, t in [('A', '您先前在補習班主要負責哪些工作？'), ('C', '協助學生寫備審'), ('A', SAL2), ('C', M), ('C', '大概 45000')]])
chk('回來後已回答 → 沒有回來提示', '剛回到面談室' not in H(c2))

# 一般進房（還沒問任何題）→ 不提示
chk('一進房還沒問題 → 沒有回來提示', '剛回到面談室' not in H(conv(('C', M))))

# 薪資真的問了兩次都有回答 → 還是不准第三次（原規則不受影響）
c3 = conv(('A', '您先前做什麼？'), ('C', '業務'), ('A', '您的期望薪資大概多少呢？'), ('C', '面議'),
          ('A', SAL2), ('C', '看公司'), ('A', '那您目前通勤方式？'))
chk('兩次都有回答 → 原本「不准第三次」照舊', '不准第三次' in H(c3))
print('全部通過' if not bad else f'{bad} 項失敗'); sys.exit(1 if bad else 0)
