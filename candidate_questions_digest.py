#!/usr/bin/env python3
"""人選在面談裡問阿財什麼——每天整理一次發 TG（2026-10-03 Jacky：「持續觀察一下人選都問什麼」）。

為什麼要有這支：
    人選問的問題是最直接的市場回饋：大家最在意什麼（薪資、班別、公司是哪家…）、
    哪些職缺資料沒寫清楚、阿財回不出來只能說「幫您問顧問」的是哪些。
    回不出來的那些，補進職缺資料（faq_notes 等）之後，下一位人選就能當場得到答案。

做法：
    1. 撈最近 N 天人選的訊息裡「像問題」的那幾則（有問號、請問、想問、會不會、可以…嗎）
    2. 每一則配上阿財緊接著的回覆，判斷有沒有答出來（回覆裡有「問顧問／轉給顧問／沒有確切」＝沒答出來）
    3. 交給 AI 依主題歸類，列出「阿財答不出來、建議補進職缺資料」的清單
    4. 發到 TG「step1ne人選 → 📊 面談報告」主題；沒有新問題就不發

用法：
    python3 candidate_questions_digest.py --days 1 --send     # 每日排程
    python3 candidate_questions_digest.py --days 14           # 手動看，只印不發
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_expertise as BE  # d1()／q()／claude 設定
try:
    import tg_route
except Exception:
    tg_route = None

Q_PAT = re.compile(r'[？?]|請問|想問|想了解|會不會|可不可以|能不能|有沒有|是不是|多少|怎麼|嗎')
NOT_ANSWERED = re.compile(r'問顧問|轉給顧問|轉給負責|沒有確切|手上沒有|顧問確認|顧問會再|顧問跟您|再跟您說明')


def fetch(days):
    rows = BE.d1(f"""
        SELECT m.application_id, m.role, m.content, m.created_at, a.name, a.job_title
          FROM messages m JOIN applications a ON a.id = m.application_id
         WHERE m.created_at >= datetime('now','+8 hours','-{int(days)} days')
         ORDER BY m.application_id, m.created_at""")
    out, by_app = [], {}
    for r in rows:
        by_app.setdefault(r['application_id'], []).append(r)
    for msgs in by_app.values():
        msgs = [x for x in msgs if not __import__('interview_markers').is_transition(x.get('content'))]   # E17：等待過渡語不算阿財的回答
        for i, m in enumerate(msgs):
            c = (m.get('content') or '').strip()
            if m['role'] != 'candidate' or c.startswith('（') or not Q_PAT.search(c):
                continue
            reply = ' '.join((x.get('content') or '') for x in msgs[i + 1:i + 3] if x['role'] == 'assistant')[:400]
            out.append({'job': m.get('job_title') or '—', 'q': c[:300], 'reply': reply,
                        'answered': not NOT_ANSWERED.search(reply), 'at': m['created_at']})
    return out


PROMPT = '''下面是最近 {days} 天，人選在 AI 面談（阿財）中問的問題，以及阿財的回覆摘要。
請整理成給獵頭顧問看的觀察，全部繁體中文、白話、不要術語。

規則：
- 不要寫出任何人選姓名。
- 「問題」只算人選真的在問的（有些是陳述或回答，不是在問，略過）。
- 依主題歸類（例：薪資福利、班別工時、公司是哪家、面試流程、工作內容、地點通勤、其他），每類寫有幾則、代表性問法 1～2 句。
- 列出「阿財答不出來（轉給顧問）」的問題，並建議顧問把答案補進哪個職缺的資料，下次阿財就能直接回答。
- 最後一句：這段期間人選最在意的是什麼。

只輸出這個 JSON：
{{"total": 0,
  "themes": [{{"name": "主題", "count": 0, "examples": ["…"]}}],
  "unanswered": [{{"job": "職缺名", "question": "人選問的（去掉姓名）", "suggest": "建議補什麼資料"}}],
  "takeaway": "一句話"}}

資料：
{data}'''


def summarize(items, days):
    data = '\n'.join(f"- [{x['job']}] 問：{x['q']}｜阿財：{x['reply'][:200]}｜{'有答' if x['answered'] else '轉顧問'}"
                     for x in items)
    r = subprocess.run(['claude', '-p', PROMPT.format(days=days, data=data), '--model', BE.MODEL,
                        '--disallowed-tools', BE._BAN + ',WebSearch,WebFetch', '--setting-sources', '',
                        '--output-format', 'text'], cwd=HERE, capture_output=True, text=True,
                       env=BE.env_with_cf(), timeout=300)
    return BE._extract_json(r.stdout)


def render(s, items, days):
    n_un = sum(1 for x in items if not x['answered'])
    L = [f'🗣️ 人選問了阿財什麼（最近 {days} 天）',
         f'共 {s.get("total") or len(items)} 則提問，其中 {n_un} 則阿財答不出來、轉給顧問', '']
    for t in s.get('themes') or []:
        L.append(f'▪️ {t.get("name")}（{t.get("count")}）')
        for e in (t.get('examples') or [])[:2]:
            L.append(f'   「{e}」')
    un = s.get('unanswered') or []
    if un:
        L += ['', '📌 阿財答不出來，建議補進職缺資料：']
        for u in un[:8]:
            L.append(f'・{u.get("job")}：{u.get("question")}\n   → {u.get("suggest")}')
    if s.get('takeaway'):
        L += ['', f'💡 {s["takeaway"]}']
    return '\n'.join(L)[:3900]


def send(text):
    env = {}
    for l in open(os.path.expanduser('~/.config/workflow-os/step1ne-tg.env')):
        k, _, v = l.strip().partition('=')
        env[k] = v.strip('"\'')
    chat, thread = '-1003967585448', 6   # step1ne人選 → 📊 面談報告
    if tg_route:
        try:
            # 跟面談報告同一個主題：舊群組 304 → 新人選群組（tg_routes remap）
            c, t = tg_route.remap('-1003231629634', 304)
            if c and t:
                chat, thread = c, t
        except Exception:
            pass
    d = urllib.parse.urlencode({'chat_id': chat, 'message_thread_id': thread, 'text': text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{env['TG_BOT_TOKEN']}/sendMessage", d, timeout=30)


def main():
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 1
    items = fetch(days)
    if not items:
        BE.log(f'最近 {days} 天沒有人選提問，不發')
        return
    s = summarize(items, days)
    if not isinstance(s, dict):
        BE.log('❌ 整理失敗')
        return
    text = render(s, items, days)
    print(text)
    if '--send' in sys.argv:
        send(text)


if __name__ == '__main__':
    main()
