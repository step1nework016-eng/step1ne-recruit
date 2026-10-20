#!/usr/bin/env python3
"""channel_mail.py — 招募管道（學校／就業中心／訓練機構）AI 擬信（2026-10-20 Jacky 拍板 A 案）

流程：
  顧問在後台「招募管道」卡片 → 寄信 → 按「AI 幫我擬信」
  → backoffice 排 ai_jobs(kind='channel_mail', payload={channel_id, note, by})
  → 這支（ai_worker.py 接手）：讀管道資料＋最近紀錄＋顧問備註 → claude 只寫主旨和開頭段落
  → 職缺清單、轉貼文字、簽名是固定樣板（從系統帶，不讓 AI 編職缺內容）
  → 回傳 JSON {to, subject, text} 寫回 ai_jobs.result_text，顧問在頁面上看、可改，按「寄出」才寄
  **這支程式不會寄任何信。**

手動測試：python3 channel_mail.py <channel_id> ["備註"] [顧問名]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PHONE = '02-6605-7111'
LICENSE = '北市就服字第 0363 號'
TRAINING_URL = 'https://step1ne.com/training/'
SHARE_TEXT = (
    '半導體建廠・BIM 培訓職缺（無經驗可）\n'
    '適合：工程、設計、建築、土木相關科系同學，以及想轉職的社會人士\n'
    '地點：新竹、苗栗、台中、雲林等地\n'
    '特色：無經驗可、公司提供培訓、正職僱用\n'
    f'職缺與報名：{TRAINING_URL}\n'
    f'（Step1ne 德仁管理顧問，{LICENSE}）')
# AI 寫的段落不准出現：費用、規模誇大、客戶公司名（另外 mail_outbox_send 寄出前還會再擋一次客戶名）
BAD = re.compile(r'費用|收費|服務費|最大|第一|唯一|保證錄取|保證')


def _ops():
    import consultant_ops
    return consultant_ops


def _load(channel_id):
    c = _ops()
    r = c.rows("SELECT * FROM recruit_channels WHERE id=?", [int(channel_id)])
    if not r:
        raise RuntimeError(f'找不到招募管道 #{channel_id}')
    ch = r[0]
    logs = c.rows("SELECT event_at, by, kind, text, result, contact_name FROM recruit_channel_logs "
                  "WHERE channel_id=? ORDER BY id DESC LIMIT 6", [int(channel_id)])
    try:
        slugs = json.loads(ch.get('jobs') or '[]')
    except Exception:
        slugs = []
    jobs = []
    for s in slugs:
        j = c.rows("SELECT slug, title, status, locations FROM jobs WHERE slug=?", [s])
        if j and j[0]['status'] == 'open':
            jobs.append(j[0])
    # 管道沒綁職缺（或綁的都關了）→ 帶培訓職缺專區那幾個
    if not jobs:
        jobs = c.rows("SELECT slug, title, status, locations FROM jobs WHERE status='open' AND slug IN "
                      "('bim-engineer-houli','semiconductor-bim-mep-drafting-trainee-hsinchu','bim-mep-drafting-engineer-hsinchu',"
                      "'trainee-engineering-design-engineer','trainee-semiconductor-project-engineer')", [])
    return ch, logs, jobs


def _loc(j):
    v = j.get('locations') or ''
    if isinstance(v, str) and v.strip().startswith('['):
        import ast
        for parse in (json.loads, ast.literal_eval):   # 有些職缺存成 ['新竹'] 這種單引號格式
            try:
                x = parse(v)
                if isinstance(x, list):
                    return '、'.join(str(i) for i in x)
            except Exception:
                pass
    return str(v).strip()


def _title(t):
    return (t or '').replace('(', '（').replace(')', '）')


def build_prompt(ch, logs, jobs, note, files=''):
    hist = '\n'.join(f"- {l.get('event_at') or ''} {l.get('by') or ''}（{l.get('kind')}）：{(l.get('text') or '')[:200]}"
                     + (f"｜窗口：{l['contact_name']}" if l.get('contact_name') else '') for l in logs) or '（還沒有紀錄）'
    jl = '\n'.join(f"- {_title(j['title'])}（{_loc(j)}）" for j in jobs)
    return f"""你是台灣獵頭公司 Step1ne 德仁管理顧問的顧問，要寫一封信給招募合作管道（學校職涯中心、系辦、就業服務站、職訓機構），
請對方把我們的「無經驗可、公司提供培訓」工程職缺轉給學員或求職民眾。

【管道】{ch.get('name')}（類型：{ch.get('kind') or '—'}）
【窗口】{ch.get('contact_name') or '（不知道名字）'}
【管道備註】{ch.get('note') or '—'}
【最近聯絡紀錄（新到舊）】
{hist}
【顧問這次想說的（這封信的目的，最重要，照這個寫）】{note or '（沒寫，就寫成第一次寄職缺資訊）'}
【這封信會附上的檔案】{files or '（沒有附件）'}
【要推的職缺（信件後面會自動附完整清單，你不用列）】
{jl}

只輸出 JSON：{{"subject":"主旨","opening":"信的本文","include_jobs":true}}
🚨 最重要：信的目的完全照「顧問這次想說的」。管道備註和聯絡紀錄只是背景，用來稱呼對方、接續前情，
   **不要自己把背景裡的其他事情（合作條件、簽約、問問題）加進信裡**，除非顧問備註有要求。
   例：備註「我要提供填寫好的招募 BIM 工程師表單」→ 這封就是「回傳填好的表單，請查收附件」，簡短即可。
include_jobs：信的目的是「提供職缺資訊請對方轉發」才給 true（後面會自動接職缺清單和轉貼文字）；
   回傳表單、回覆問題、約時間這類就給 false（只會接一行培訓職缺頁連結）。
規則：
1. subject：20～35 字，只講這封信提供什麼（例：「半導體建廠 BIM 培訓職缺｜可轉給學員參考」），不要在主旨向對方要東西（信箱、電話），不要驚嘆號。
2. opening：1～4 段、合計 60～260 字（回傳表單這類可以很短）。稱呼用窗口名字（「林小姐您好」），不知道就用「{ch.get('name')} 承辦人您好」。
   如果紀錄裡通過電話，開頭接「延續先前電話中提到的」；第一次聯絡才需要一句自我介紹（Step1ne 德仁管理顧問，協助半導體產業招募，也與多家營造相關企業合作）。
   有附件就明確寫出附件是什麼、請對方查收（例：「附件是我們填寫好的招募需求表，再麻煩您查收」）。
   如果目的是提供職缺請對方轉發：說明這批職缺接受無經驗、公司提供培訓，適合工程、設計、建築、土木相關科系同學與想轉職的人。
3. 不要列職缺名稱清單、不要寫連結、不要寫簽名——這些會自動接在後面。
4. 不寫費用、不寫「最大／第一」、不保證錄取、不寫任何客戶公司名稱；繁體中文、客氣精簡，不要行銷話術。"""


def run(payload, run_claude=None):
    channel_id = payload.get('channel_id')
    note = (payload.get('note') or '').strip()
    by = (payload.get('by') or '').strip() or 'Phoebe'
    ch, logs, jobs = _load(channel_id)
    if run_claude is None:
        from ai_worker import run_claude as rc
        run_claude = rc
    files = '、'.join(str(x) for x in (payload.get('files') or []) if x)[:300]
    prompt = build_prompt(ch, logs, jobs, note, files)
    data = None
    err = ''
    for _ in range(2):
        out = run_claude(prompt + (f'\n\n⚠️ 上一次不合格：{err}，請重寫。' if err else ''), want_json=True, timeout=300)
        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r'\{.*\}', out or '', re.S)
            data = json.loads(m.group(0)) if m else None
        if not data or not data.get('subject') or not data.get('opening'):
            err = '缺 subject 或 opening'
            continue
        hit = BAD.search(data['subject'] + data['opening'])
        if hit:
            err = f'出現不准寫的字「{hit.group(0)}」'
            continue
        err = ''
        break
    if err or not data:
        raise RuntimeError(f'AI 擬信不合格：{err or "沒有輸出"}')
    joblines = '\n'.join(f"・{_title(j['title'])}｜{_loc(j)}\n  https://step1ne.com/jobs/{j['slug']}/" for j in jobs)
    if data.get('include_jobs', True) is not False:
        mid = ('目前開放的職缺：\n' + joblines + '\n\n'
               + f'全部培訓職缺與報名：{TRAINING_URL}\n\n'
               + '以下這段可以直接轉貼給學員：\n'
               + '────────────\n' + SHARE_TEXT + '\n────────────\n\n'
               + '如果需要職缺說明會或其他資料，歡迎直接回信或來電。謝謝您！\n\n')
    else:
        mid = f'培訓職缺與報名頁面：{TRAINING_URL}\n\n有任何問題歡迎直接回信或來電，謝謝您！\n\n'
    text = (data['opening'].strip() + '\n\n' + mid
            + f'Step1ne 德仁管理顧問\n顧問：{by}\n電話：{PHONE}\nEmail：official@step1ne.com\n就業服務許可證：{LICENSE}')
    return json.dumps({'to': ch.get('email') or '', 'subject': data['subject'].strip(), 'text': text}, ensure_ascii=False)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    print(json.loads(run({'channel_id': sys.argv[1], 'note': sys.argv[2] if len(sys.argv) > 2 else '',
                          'by': sys.argv[3] if len(sys.argv) > 3 else 'Jacky'}))['text'])
