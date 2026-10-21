#!/usr/bin/env python3
"""channel_call_summary.py — 招募管道電話逐字稿 → AI 摘要（2026-10-21 Jacky）

流程：
  顧問在後台「招募管道」卡片 → 新增紀錄（電話）→ 貼上或上傳逐字稿 → 按「AI 整理摘要」
  → backoffice 排 ai_jobs(kind='channel_call_summary', payload={channel_id, transcript})
  → 這支（ai_worker.py 接手）讀管道資料＋最近紀錄＋逐字稿，整理成五件事：
     談了什麼／對方答應幫什麼／窗口是誰／下一步要做什麼／哪天前做
  → 回傳 JSON 寫回 ai_jobs.result_text，頁面把結果填進表單，顧問看過、改過再按「存這筆紀錄」
  **這支程式不會寫任何紀錄，也不會寄信。**

手動測試：python3 channel_call_summary.py <channel_id> <逐字稿檔>
"""
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _ops():
    import consultant_ops
    return consultant_ops


def _load(channel_id):
    c = _ops()
    r = c.rows("SELECT id, name, kind, contact_name, phone, email FROM recruit_channels WHERE id=?", [int(channel_id)])
    if not r:
        raise RuntimeError(f'找不到招募管道 #{channel_id}')
    logs = c.rows("SELECT event_at, by, kind, text, contact_name, next_step, next_due FROM recruit_channel_logs "
                  "WHERE channel_id=? ORDER BY COALESCE(event_at, at) DESC LIMIT 6", [int(channel_id)])
    return r[0], logs


def build_prompt(ch, logs, transcript, today):
    hist = '\n'.join(f"- {l.get('event_at') or ''} {l.get('by') or ''}：{(l.get('text') or '')[:160]}"
                     + (f"（下一步：{l['next_step']}）" if l.get('next_step') else '') for l in logs) or '（沒有）'
    return f"""你是獵頭公司 Step1ne 的助理。顧問剛跟招募管道「{ch['name']}」（{ch.get('kind') or ''}）通完電話，
下面是通話逐字稿（自動聽寫，可能有錯字、講者標示不準）。請整理成顧問要記的五件事。

今天日期：{today}
這家目前登記的窗口：{ch.get('contact_name') or '（沒有）'}
之前的紀錄：
{hist}

【逐字稿】
{transcript}

只輸出 JSON：
{{"talked":"談了什麼","promised":"對方答應幫什麼","contact":"窗口是誰","next_step":"下一步要做什麼","next_due":"YYYY-MM-DD 或空字串"}}

規則：
- talked：2～4 句，講重點（對方的回應、能不能合作、有什麼限制或流程）。
- promised：對方明確答應的事（例：轉發職缺給 AutoCAD 班學員、提供邀請函、介紹老師）。沒有答應任何事就寫「沒有明確答應」。
- contact：窗口姓名＋職稱／分機／電話／Email，逐字稿有提到才寫；沒提到就寫空字串，不要猜。
- next_step：我方下一步具體要做什麼，一句話（例：寄 BIM 職缺說明給王小姐並附報名連結）。
- next_due：逐字稿有講日期或「下週一」這類就換算成日期；沒講就依事情輕重給合理日期（通常 1～3 個工作天內），不要給週末。
- 逐字稿聽不出來的不要編；繁體中文；不要寫費用、不要寫客戶公司名稱。"""


DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _clean(d):
    out = {k: str(d.get(k) or '').strip() for k in ('talked', 'promised', 'contact', 'next_step', 'next_due')}
    if out['next_due'] and not DATE.match(out['next_due']):
        out['next_due'] = ''
    if out['next_due']:
        dd = datetime.date.fromisoformat(out['next_due'])
        while dd.weekday() >= 5:  # 不給週末
            dd += datetime.timedelta(days=1)
        out['next_due'] = dd.isoformat()
    out['contact'] = out['contact'][:60]
    out['next_step'] = out['next_step'][:200]
    return out


def run(payload, run_claude=None):
    transcript = (payload.get('transcript') or '').strip()[:40000]
    if not transcript:
        raise RuntimeError('沒有逐字稿')
    ch, logs = _load(payload.get('channel_id'))
    if run_claude is None:
        from ai_worker import run_claude as rc
        run_claude = rc
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date().isoformat()
    prompt = build_prompt(ch, logs, transcript, today)
    err = ''
    for _ in range(2):
        out = run_claude(prompt + (f'\n\n⚠️ 上一次不合格：{err}，請重寫。' if err else ''), want_json=True, timeout=300)
        try:
            data = json.loads(out)
        except Exception:
            m = re.search(r'\{.*\}', out or '', re.S)
            data = json.loads(m.group(0)) if m else None
        if not data or not (data.get('talked') or '').strip():
            err = '缺 talked'
            continue
        return json.dumps(_clean(data), ensure_ascii=False)
    raise RuntimeError(f'AI 摘要不合格：{err or "沒有輸出"}')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    print(run({'channel_id': sys.argv[1], 'transcript': open(sys.argv[2], encoding='utf-8').read()}))
