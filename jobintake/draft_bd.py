#!/usr/bin/env python3
"""反向開發（MPC）：把一位人選變成一批開發信，送進群組等顧問核准。

流程（跟職缺上架同一套骨架，刻意做成一樣，顧問只要學一次）：

    人選履歷 ──► 總指揮擬「匿名人選卡 + 目標公司 + 開發信」
                       │
                       ▼
             🚨 client_guard 比對客戶名單
                （已簽約／洽談中／終端客戶／禁止 → 整家移除）
                       │
                       ▼
              寫進 bd_outreach（status=pending）
                       │
                       ▼
              推 Telegram：一家一則，三顆按鈕
                 📤 核准寄出　✏️ 重寫　❌ 不寄
                       │
                       ▼
              顧問按核准 → Worker 才真的寄出去

⚠️ 這支**不寄任何信**。寄信只有一條路：顧問按核准，Worker 用 Resend 寄。
   agent 拿不到寄信的權限，這是刻意的。

⚠️ 人選同意的事不在這條流程裡。顧問面談時本來就會確認，
   而且開發信一律匿名（不含姓名與現職公司），不需要也不可以在系統裡再卡一道。
"""

import importlib.util
import json
import os
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import client_guard as G          # noqa: E402

# 借 interview_daemon 的 d1()/q()/env_with_cf()，跟 draft_job.py 同一套
_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

MODEL = 'claude-opus-5'   # ⚠️ 一定要寫全名，這台機器 `--model opus` 會解析成舊版


def log(m):
    print(m, flush=True)


PROMPT = """你要做的是「反向開發」（MPC，Most Placeable Candidate）：
手上有一位很好的人選但沒有對應職缺時，用這個人去敲還沒合作的公司。

## 你會拿到

一份人選履歷／面談報告。

## 你要產出

一份 JSON，欄位如下（**只輸出 JSON，不要有其他文字**）：

{
  "candidate_ref": "內部代號，例如 BIM-0811-A。不可以是姓名",
  "candidate_card": "匿名人選卡。5-8 行。寫背景、年資、能獨立處理什麼、稀缺在哪。
                     ⛔ 不可出現：姓名、現職公司名、前東家全名、學校全名、任何可辨識個資。
                     ⛔ **也不可以寫年齡、性別、役別、婚育、國籍、身心障礙**——
                        就業服務法第 5 條禁止這些成為任用考量，我們主動寫進去
                        等於把客戶推向違法，而且對人選也沒有好處。
                        『25 歲男性』要改成『工程資歷 1~2 年』這種只講能力的寫法。
                     產業可以寫（例：大型公用系統維運單位）。",
  "targets": [
    {
      "company": "公司全名",
      "why": "為什麼是這家（一句話，要能連到人選的具體經歷，不能只寫『規模大』）",
      "contact_name": "窗口職稱或姓名，查不到就寫 null",
      "contact_email": "查到才寫，查不到寫 null",
      "subject": "信件主旨",
      "body": "信件全文。用 Jacky 的第一人稱寫。"
    }
  ]
}

## 信怎麼寫（違反的話顧問會直接退回）

- **不提人選姓名、現職公司**。一個字都不行。
- 第一段就講「我手上有一位什麼樣的人」，不要先自我介紹三行。
- 第二段講「為什麼想到貴公司」，要具體連到人選的經歷，不能是罐頭句。
- 結尾給對方好退場：「不合適就當我沒說」這種。壓迫感會讓 B2B 開發信直接進垃圾桶。
- 全文 200-300 字。長信沒人看。
- 署名固定：
  Jacky Chen｜德仁管理顧問有限公司（Step1ne）
  電話／LINE ID：0958616744
  就業服務許可：北市就服字第 0363 號｜臺北市政府勞動局 114 年度評鑑 A 級
  https://step1ne.com/about/

## 挑公司的規矩

- 挑 5-8 家。寧可少而準。
- **不要挑我們客戶的終端客戶**（例：某客戶是設備商，它的終端是某晶圓廠 → 不要挑那家晶圓廠）。
  你不一定查得到，所以系統之後還會再比對一次客戶名單，被擋下不算你的錯——
  但你能判斷的要先排除。
- 不要挑同業（人力銀行、獵頭、派遣公司）。
"""


def run_commander(resume_text, workdir):
    """叫本機的 claude 去擬。跟 draft_job.py 同樣的做法。"""
    os.makedirs(workdir, exist_ok=True)
    prompt = PROMPT + '\n\n## 人選資料\n\n```\n' + resume_text[:60000] + '\n```\n'
    open(os.path.join(workdir, 'prompt.txt'), 'w', encoding='utf-8').write(prompt)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=1800)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, 'raw.txt'), 'w', encoding='utf-8').write(out)
    if not out:
        raise SystemExit(f'總指揮沒有回應：{(r.stderr or "")[:400]}')
    # 容忍它包在 ```json 裡
    s = out.find('{')
    e = out.rfind('}')
    if s < 0 or e < 0:
        raise SystemExit('回應裡找不到 JSON，看 raw.txt')
    return json.loads(out[s:e + 1])


import re  # noqa: E402

# 就服法第 5 條的保護特徵。這些字出現在人選卡或信裡＝主動把客戶推向違法。
# 2026-08-11 第一次試跑，總指揮寫出「25 歲男性」——它不是故意的，
# 履歷上就有，它照抄。所以規則不能只寫在 prompt 裡，要在出口再擋一次。
_PROTECTED = re.compile(
    r'\d{1,2}\s*歲|男性|女性|已婚|未婚|懷孕|育有|免役|服役|替代役|'
    r'身心障礙|原住民|外籍|國籍為')


def scrub(spec):
    hits = []
    def scan(label, text):
        for m in _PROTECTED.finditer(str(text or '')):
            hits.append((label, m.group(0)))
    scan('人選卡', spec.get('candidate_card'))
    for t in spec.get('targets') or []:
        scan(f'{t.get("company")} 的信', t.get('body'))
    return hits


class Law5Blocked(Exception):
    """就服法保護特徵出現在要寄出去的文案裡。

    用例外而不是回傳值，是因為 guard() 的呼叫端只解三元組
    （spec, blocked, warned）——回 False 會讓上游解包失敗、
    錯誤訊息變成看不懂的 ValueError，反而讓人以為是程式壞了。
    """


def guard(spec):
    """🚨 這一關不能跳過。被擋下的公司整家移除，不是標註。

    ⚠️ 2026-08-19 改：就服法那一項原本只是 log 一行「顧問核准前請刪掉」，
    信照樣進 pending、照樣推核准按鈕——那等於沒擋。
    2026-08-18 真實事故（候選人結案訊息寫「傾向尋找男性人選」）證明了
    「提醒」擋不住任何東西：趕時間的人就是會按過去。
    改成硬擋：命中就整份不進 pending，回傳 False 讓上游停下來重寫。
    """
    hits = scrub(spec)
    for label, word in hits:
        log(f'  ⛔ 就服法第 5 條保護特徵出現在{label}：「{word}」')
    if hits:
        log('🚨 這批不會進待核准清單，也不會出現核准按鈕。'
            '請把上面那些字眼從文案裡拿掉再重新產生。')
        log('   （客戶的原始要求該記還是要記，但記在內部欄位，不要寫進要寄出去的信）')
        raise Law5Blocked([f'{label}：{word}' for label, word in hits])
    clients = G.load_clients(D.d1)
    ok, blocked, warned = G.filter_targets(spec.get('targets') or [], clients)
    log(f'客戶名單比對：{len(clients)} 家名單　→　可敲 {len(ok)}　擋下 {len(blocked)}　注意 {len(warned)}')
    for b in blocked:
        log(f'  ⛔ 移除 {b["company"]}：{b["why"]}（命中名單上的「{b["matched"]}」）')
    for w in warned:
        log(f'  ⚠️ {w["company"]}：{w["why"]}')
    spec['targets'] = ok
    return spec, blocked, warned


def save_and_send(spec, blocked, warned, dry=False):
    import tg_bd
    batch = str(uuid.uuid4())[:8]
    card = spec.get('candidate_card') or ''
    ref = spec.get('candidate_ref') or batch
    rows = []
    for t in spec['targets']:
        bid = str(uuid.uuid4())
        rows.append((bid, t))
        if dry:
            continue
        D.d1(f"""INSERT INTO bd_outreach
          (id, created_at, batch_id, candidate_ref, candidate_card, company, why_company,
           contact_name, contact_email, subject, body, status, updated_at)
          VALUES ({D.q(bid)}, datetime('now','+8 hours'), {D.q(batch)}, {D.q(ref)},
                  {D.q(card)}, {D.q(t.get('company'))}, {D.q(t.get('why'))},
                  {D.q(t.get('contact_name'))}, {D.q(t.get('contact_email'))},
                  {D.q(t.get('subject'))}, {D.q(t.get('body'))}, 'pending',
                  datetime('now','+8 hours'))""")

    head = (f'🎯 反向開發：{ref}\n'
            f'{len(spec["targets"])} 家可敲'
            + (f'　·　⛔ {len(blocked)} 家被客戶名單擋下' if blocked else '')
            + '\n\n' + card)
    if blocked:
        head += '\n\n【已自動移除，不會寄】\n' + '\n'.join(
            f'・{b["company"]}：{b["why"]}' for b in blocked)
    if dry:
        print('=' * 60); print(head)
        for bid, t in rows:
            print('-' * 60)
            print(f'{t.get("company")}　→　{t.get("contact_email") or "（窗口待補）"}')
            print(f'主旨：{t.get("subject")}')
            print(t.get('body'))
        return

    tg_bd.send_head(head)
    for bid, t in rows:
        mid = tg_bd.send_letter(bid, t)
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(bid)}")
    log(f'✅ 已送審 {len(rows)} 封。顧問按「核准寄出」之前，一封都不會寄出去。')


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('resume', help='人選履歷／面談報告的檔案路徑（純文字或 md）')
    ap.add_argument('--dry', action='store_true', help='只印出來，不寫 DB 也不推 Telegram')
    ap.add_argument('--spec', help='跳過總指揮，直接吃現成的 JSON（重跑比對用）')
    a = ap.parse_args()

    workdir = os.path.join(HERE, 'bd_work', os.path.basename(a.resume).rsplit('.', 1)[0])
    if a.spec:
        spec = json.load(open(a.spec, encoding='utf-8'))
    else:
        spec = run_commander(open(a.resume, encoding='utf-8').read(), workdir)
        os.makedirs(workdir, exist_ok=True)
        json.dump(spec, open(os.path.join(workdir, 'spec.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)

    try:
        spec, blocked, warned = guard(spec)
    except Law5Blocked as e:
        log(f'⛔ 這批擬稿因就服法紅線整批擋下，沒有任何一封進待核准清單：{e.args[0]}')
        return
    if not spec['targets']:
        log('⛔ 全部被客戶名單擋下，沒有可以敲的公司。')
        return
    save_and_send(spec, blocked, warned, dry=a.dry)


if __name__ == '__main__':
    main()
