#!/usr/bin/env python3
"""反向開發的排程：撿顧問的開發需求，跑完整條龍。

    顧問填「我要開發做 MEP 的客戶」
            │
            ▼
    ① 去匿名人才庫撈「沒到職、有履歷」的人
            │
            ▼
    ② 總指揮：挑得上用場的人 → 產匿名人選卡 → 挑目標公司 → 寫開發信
            │
            ▼
    ③ 🚨 客戶名單比對（已簽約／洽談中／終端客戶 → 整家移除）
            │
            ▼
    ④ 就服法第 5 條保護特徵掃描（年齡／性別／役別…）
            │
            ▼
    ⑤ 前 AUTO_AFTER 批要顧問核准；顧問連續核准都沒改動之後，自動切成直接寄

⚠️ 人選同意不在這條流程裡。顧問面談時本來就會確認，
   而且開發信一律匿名（不含姓名與現職公司）。不要再加同意關卡。
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
import tg_bd                      # noqa: E402

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

from draft_bd import scrub        # noqa: E402  （就服法第 5 條掃描，共用同一份）

MODEL = 'claude-opus-5'
WORK = os.path.join(HERE, 'bd_work')

# 顧問連續核准這麼多批、而且都沒有按過重寫，之後就自動寄。
# 2026-08-11 Jacky 選的是「第一批要我看，順了就放手」。
AUTO_AFTER = 2


def log(m):
    print(m, flush=True)


def pool(limit=12):
    """匿名人才庫：有履歷、而且沒有走到到職的人。

    ⚠️ 撈進來的履歷全文會交給總指揮判斷配不配得上，
       但**產出的人選卡不可以有姓名與現職公司**——那是寄給外部企業的東西。
    """
    rows = D.d1(f"""
      SELECT a.id, a.job_title, a.expected_salary, a.available_date,
             a.interview_state, f.text_content AS cv,
             (SELECT p.stage FROM placements p WHERE p.application_id=a.id
               ORDER BY p.updated_at DESC LIMIT 1) AS stage
        FROM applications a
        JOIN files f ON f.id = a.resume_file_id
       WHERE a.superseded_by IS NULL
         AND f.text_content IS NOT NULL AND length(f.text_content) > 200
       ORDER BY a.created_at DESC LIMIT {int(limit)}""")
    return [r for r in rows if str(r.get('stage') or '') not in ('onboard', 'placed', '到職')]


PROMPT = """你要做「反向開發」（MPC）：顧問想開發某一類客戶，
你要從我們手上的人才庫裡挑出**真的配得上**的人，用他去敲還沒合作的公司。

## 顧問要開發什麼

{ask}

## 人才庫（都是已經面談過、但還沒到職的人）

{pool}

## 你要產出

只輸出 JSON，不要有其他文字：

{{
  "picked": "你挑中的人選 id（上面 [id=xxx] 那個）。挑不到就填 null",
  "skip_reason": "如果挑不到，一句話說為什麼（例：庫裡沒有 MEP 背景的人）",
  "candidate_ref": "內部代號，例如 MEP-0811-A。不可以是姓名",
  "candidate_card": "匿名人選卡，5-8 行。寫背景、年資、能獨立處理什麼、稀缺在哪。
    ⛔ 不可出現：姓名、現職公司名、前東家全名、學校全名、電話、Email。
    ⛔ 也不可以寫年齡、性別、役別、婚育、國籍、身心障礙——就業服務法第 5 條
       禁止這些成為任用考量，我們主動寫進去等於把客戶推向違法。
       『25 歲男性』要改成『工程資歷 1~2 年』這種只講能力的寫法。
    產業可以寫（例：大型公用系統維運單位）。",
  "targets": [
    {{"company": "公司全名",
      "why": "為什麼是這家。要連到人選的具體經歷，不能只寫『規模大』",
      "contact_name": "窗口職稱或姓名，查不到寫 null",
      "contact_email": "查到才寫，查不到寫 null",
      "subject": "信件主旨",
      "body": "信件全文，用 Jacky 的第一人稱"}}
  ]
}}

## 挑人的標準

- **配不上就說配不上。** picked 填 null 比硬湊一個人去敲客戶好——
  寄一封明顯不對題的信，那家公司以後就不會再看我們的信了。
- 面談過的人優先（我們對他有第一手判斷）。

## 挑公司

- 挑 {n} 家。寧可少而準。
- 不要挑同業（人力銀行、獵頭、派遣公司）。
- 不要挑我們客戶的終端客戶。你不一定查得到，系統之後會再比對一次客戶名單。

## 信怎麼寫

- **不提人選姓名、現職公司**，一個字都不行。
- 第一段直接講「我手上有一位什麼樣的人」，不要先自我介紹三行。
- 第二段講為什麼想到這家，要具體。
- 結尾給對方好退場：「不合適就當我沒說」。
- 全文 200-300 字。
- 署名固定：
  Jacky Chen｜德仁管理顧問有限公司（Step1ne）
  電話／LINE ID：0958616744
  就業服務許可：北市就服字第 0363 號｜臺北市政府勞動局 114 年度評鑑 A 級
  https://step1ne.com/about/
"""


def ask_text(r):
    parts = [f"職缺類型：{r['role_family']}"]
    for k, lb in (('industry', '產業'), ('region', '地區'), ('note', '顧問交代')):
        if r.get(k):
            parts.append(f'{lb}：{r[k]}')
    return '\n'.join(parts)


def pool_text(ps):
    out = []
    for p in ps:
        out.append(f"[id={p['id']}] 應徵過：{p.get('job_title') or '—'}　"
                   f"期望：{p.get('expected_salary') or '—'}\n"
                   f"{(p.get('cv') or '')[:2200]}\n---")
    return '\n'.join(out)


def run_commander(prompt, workdir):
    os.makedirs(workdir, exist_ok=True)
    open(os.path.join(workdir, 'prompt.txt'), 'w', encoding='utf-8').write(prompt)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=1800)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, 'raw.txt'), 'w', encoding='utf-8').write(out)
    if not out:
        raise RuntimeError(f'總指揮沒有回應：{(r.stderr or "")[:300]}')
    s, e = out.find('{'), out.rfind('}')
    if s < 0:
        raise RuntimeError('回應裡找不到 JSON')
    return json.loads(out[s:e + 1])


def auto_mode():
    """顧問已經核准過幾批、而且沒有退回重寫過？夠了就自動寄。

    判準用「批」不是「封」——一批六封全按核准只證明這一次的品質，
    要連續幾批都沒問題才算穩。
    """
    rows = D.d1("""SELECT batch_id,
                     SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) sent,
                     SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) rewritten
                   FROM bd_outreach WHERE batch_id IS NOT NULL GROUP BY batch_id""")
    clean = [r for r in rows if (r['sent'] or 0) > 0 and not (r['rewritten'] or 0)]
    return len(clean) >= AUTO_AFTER


def handle(req):
    rid = req['id']
    D.d1(f"UPDATE bd_requests SET status='working', updated_at=datetime('now','+8 hours') "
         f"WHERE id={D.q(rid)}")
    workdir = os.path.join(WORK, rid[:8])
    ps = pool()
    if not ps:
        D.d1(f"UPDATE bd_requests SET status='failed', result_note='人才庫是空的' "
             f"WHERE id={D.q(rid)}")
        tg_bd.send_head(f"⚠️ 開發需求「{req['role_family']}」跑不下去：人才庫裡沒有可用的履歷。")
        return

    spec = run_commander(
        PROMPT.format(ask=ask_text(req), pool=pool_text(ps), n=req.get('target_count') or 6),
        workdir)
    json.dump(spec, open(os.path.join(workdir, 'spec.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    if not spec.get('picked') or not spec.get('targets'):
        why = spec.get('skip_reason') or '總指揮沒有挑到配得上的人選'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 開發需求「{req['role_family']}」沒有出信\n原因：{why}\n"
                        f"（配不上就不硬湊——寄一封不對題的信，那家公司以後就不看我們的信了）")
        return

    # 🚨 兩道關卡，順序不能換：先擋客戶，再掃就服法
    clients = G.load_clients(D.d1)
    ok, blocked, warned = G.filter_targets(spec['targets'], clients)
    spec['targets'] = ok
    law = scrub(spec)
    for lb, w in law:
        log(f'⚠️ 就服法第 5 條保護特徵出現在{lb}：「{w}」')

    batch = str(uuid.uuid4())[:8]
    auto = auto_mode() and not law     # 有法遵疑慮就一定要人看過，不自動寄
    rows = []
    for t in spec['targets']:
        bid = str(uuid.uuid4())
        rows.append((bid, t))
        D.d1(f"""INSERT INTO bd_outreach
          (id, created_at, batch_id, request_id, candidate_ref, candidate_card, company,
           why_company, contact_name, contact_email, subject, body, status, updated_at)
          VALUES ({D.q(bid)}, datetime('now','+8 hours'), {D.q(batch)}, {D.q(rid)},
                  {D.q(spec.get('candidate_ref') or batch)}, {D.q(spec.get('candidate_card'))},
                  {D.q(t.get('company'))}, {D.q(t.get('why'))}, {D.q(t.get('contact_name'))},
                  {D.q(t.get('contact_email'))}, {D.q(t.get('subject'))}, {D.q(t.get('body'))},
                  'pending', datetime('now','+8 hours'))""")

    head = (f"🎯 開發需求：{req['role_family']}\n"
            f"配對到人選 {spec.get('candidate_ref')}　·　{len(ok)} 家可敲"
            + (f"　·　⛔ {len(blocked)} 家被客戶名單擋下" if blocked else '')
            + '\n\n' + (spec.get('candidate_card') or ''))
    if blocked:
        head += '\n\n【已自動移除，不會寄】\n' + '\n'.join(
            f'・{b["company"]}：{b["why"]}' for b in blocked)
    if law:
        head += ('\n\n⚠️ 內容出現就服法第 5 條的保護特徵：'
                 + '、'.join(w for _, w in law) + '\n核准之前請先刪掉，這批不會自動寄。')
    if auto:
        head += '\n\n🤖 你前面幾批都直接核准沒改動，這批系統會自動寄出。要停就按下面任何一封的「不寄」。'
    tg_bd.send_head(head)

    for bid, t in rows:
        mid = tg_bd.send_letter(bid, t)
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(bid)}")

    D.d1(f"UPDATE bd_requests SET status='done', batch_id={D.q(batch)}, "
         f"result_note={D.q(f'{len(ok)} 家送審，{len(blocked)} 家被擋')}, "
         f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
    log(f'✅ 需求 {rid[:8]}：送審 {len(rows)} 封，擋下 {len(blocked)} 家')


def main():
    reqs = D.d1("SELECT * FROM bd_requests WHERE status='new' ORDER BY created_at LIMIT 3")
    if not reqs:
        return
    log(f'撿到 {len(reqs)} 張開發需求')
    for r in reqs:
        try:
            handle(r)
        except Exception as e:
            log(f'❌ 需求 {r["id"][:8]} 失敗：{e}')
            D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(str(e)[:300])}, "
                 f"updated_at=datetime('now','+8 hours') WHERE id={D.q(r['id'])}")
            tg_bd.send_head(f"❌ 開發需求「{r.get('role_family')}」處理失敗：{str(e)[:200]}")


if __name__ == '__main__':
    main()
