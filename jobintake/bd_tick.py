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
# ⚠️ ROOT 也要進 sys.path。下面用 importlib 載 interview_daemon.py 是「照路徑載」，
# 不會順便把它旁邊的模組（autoupdate 等）加進搜尋路徑——interview_daemon 在
# 2026-09 加了 `import autoupdate` 之後，這支就每次啟動都 ModuleNotFoundError，
# 而且錯誤只寫進 log 沒有任何通知，開發客戶那條線靜默停擺超過兩週才被發現。
sys.path.insert(0, ROOT)

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


# ── 為什麼拆成兩支 prompt ──
# 2026-08-11 第一版是一支大 prompt：挑人＋挑 5 家＋每家查 5 個求職管道＋寫 5 封信。
# 結果 30 分鐘逾時，什麼都沒產出。查職缺是 IO 密集的工作，一家就要好幾次 WebSearch，
# 五家串在同一次對話裡必然爆。
# 拆開之後：第一支只做判斷（不上網，很快），第二支一家一次（各自可控）。
# 任何一家查失敗也不會拖垮整批。

PICK_PROMPT = """你是 Step1ne（德仁管理顧問）的業務開發。

## 顧問要開發什麼

{ask}

## 人才庫（面談過、還沒到職的人）

{pool}

## 你要做兩件事

### ① 從人才庫挑一個人

配不上就說配不上（picked 填 null）。寄一封明顯不對題的信，那家公司以後就不會再看我們的信。

### ② 列出 {n} 家目標公司

- 要真的可能用到這種人的公司。寧可少而準。
- 不要挑同業（人力銀行、獵頭、派遣公司）。
- 不要挑我們客戶的終端客戶（例：客戶是設備商，它的終端晶圓廠不要挑）。
- **這一步不要上網查職缺**，下一關會一家一家查。你只要給名單與理由。

## 輸出

只輸出 JSON：

{{
  "picked": "挑中的人選 id（上面 [id=xxx]），挑不到填 null",
  "skip_reason": "挑不到的話一句話說明",
  "candidate_ref": "內部代號，例如 MEP-0811-A。不可以是姓名",
  "candidate_card": "匿名人選卡 5-8 行，給顧問看的內部摘要。
    ⛔ 不可有姓名、現職公司名、前東家全名、學校全名、電話、Email。
    ⛔ 也不可以寫年齡、性別、役別、婚育、國籍、身心障礙——就業服務法第 5 條
       禁止這些成為任用考量，我們主動寫進去等於把客戶推向違法。",
  "highlights": ["給對外信件用的重點精華 3-4 條，對著這個職能寫，同樣不可有可辨識資訊"],
  "companies": [{{"company": "公司全名", "why": "為什麼是這家，一句話"}}]
}}
"""


LETTER_PROMPT = """你是 Step1ne（德仁管理顧問）的業務開發。目標：**把這家公司簽下來當客戶。**

不是「介紹一個人」，是「用一個對得上的人選當敲門磚，換到一次對話」。

## 這一家

公司：{company}
為什麼挑他：{why}
顧問想開發的職能：{role}

## 我們手上的人選（對外可用的重點，已去識別化）

{highlights}

## 第一步：查這家公司現在在招什麼

管道全部都要試，不要查一個沒有就放棄：

  104人力銀行　·　1111　·　CakeResume　·　公司官網的人才招募頁　·　LinkedIn Jobs

用 WebSearch／WebFetch 實際去查，重點是找到跟上面這位人選對得上的職缺。
查到要記下：**職缺名稱、地點、關鍵條件、你在哪個管道看到的、網址**。

查不到公開職缺時分兩種：

- **預設不開發**：`has_job=false`、`probe=false`。沒有錨點的信就是廣告信。
- **例外，值得問問看**：`has_job=false`、`probe=true`。當這家明顯會用到這種人
  （產業對、規模夠、近期有擴廠或得標新聞），主動問一句「近期有沒有這方面的人力需求」
  本身就是開發，而且有機會問出沒公開的缺。信改成探詢語氣，**不可以假裝看到職缺**。

⛔ **絕對不可以編造職缺。** 寫錯職缺名稱，這家公司就永遠不會回我們了。

## 第二步：寫信

**主旨**（決定會不會被打開）：

- 要有具體的東西：職缺名 ＋ 人選最值錢的那一點
- 12–24 字，手機看得完
- 不要用「合作提案」「敬啟者」「毛遂自薦」「自我推薦」
- 好例子：「貴公司廠務工程師｜一位消防＋空調＋電力都獨立扛過的人選」
- 探詢版：「貴公司近期有廠務人力需求嗎？我這邊有一位剛釋出」

**內文照這個順序，不要多加段落：**

1. 一句自我介紹（一句，不是一段）：我是德仁管理顧問（Step1ne）的 Jacky
2. **我在哪裡看到你們在徵什麼**，並複述一兩個他們寫的條件，證明你真的看過
   （探詢版改成：為什麼想到這家公司）
3. 「我這邊有一位配得上的人選」+ **3–4 條**重點精華，對著他們的條件寫
4. 附件說明：匿名履歷（完整經歷、隱去可辨識資訊）與公司簡介
5. 收尾：如果對這位人選有興趣可以進一步討論；不需要的話回一聲就不再打擾

⛔ 不要推銷服務線（派遣／正職代招／中高階獵才），不要寫「想與貴公司合作」。
   這封只做一件事：讓對方對這個人選有興趣而願意回信。合作是回信之後的事。

- 全文 200–280 字。
- 署名固定：
  Jacky Chen｜德仁管理顧問有限公司（Step1ne）
  電話／LINE ID：0958616744
  就業服務許可：北市就服字第 0363 號｜臺北市政府勞動局 114 年度評鑑 A 級
  https://step1ne.com/about/

## 第三步：找收件窗口

人資信箱、招募窗口 email。查不到填 null，顧問可能自己有人脈。

## 輸出

只輸出 JSON：

{{
  "company": "{company}",
  "has_job": true,
  "probe": false,
  "job_title": "查到的職缺名稱，沒有填 null",
  "job_source": "在哪個管道看到的，沒有填 null",
  "job_url": "職缺網址，沒有填 null",
  "job_requirements": "他們寫的關鍵條件，沒有填 null",
  "contact_name": "窗口，查不到填 null",
  "contact_email": "查不到填 null",
  "subject": "信件主旨",
  "body": "信件全文"
}}

⚠️ 如果 has_job=false 而且 probe=false，subject 與 body 都填 null，這家會被捨棄。
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


def run_commander(prompt, workdir, tag='prompt', timeout=900):
    os.makedirs(workdir, exist_ok=True)
    open(os.path.join(workdir, f'{tag}.txt'), 'w', encoding='utf-8').write(prompt)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=timeout)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, f'{tag}.raw.txt'), 'w', encoding='utf-8').write(out)
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



def make_anon_pdf(app_id, workdir):
    """產匿名履歷 PDF 並存進 files 表，回傳 file_id。

    ⚠️ 一定要用 anon 模式：對方是還沒簽約的陌生公司，
       給具名履歷等於未經同意把人選資料交出去。
    """
    import base64
    out = os.path.join(workdir, f'anon_{app_id[:8]}.pdf')
    r = subprocess.run(
        ['python3', os.path.join(ROOT, 'make_anon_cv.py'), app_id, '--mode', 'anon', '--out', out],
        capture_output=True, text=True, env=D.env_with_cf(), timeout=900)
    if not os.path.exists(out):
        log(f'⚠️ 匿名履歷產不出來，這批信不會有履歷附件：{(r.stderr or r.stdout)[-300:]}')
        return None
    b = open(out, 'rb').read()
    b64 = base64.b64encode(b).decode()
    fid = f'bdcv-{app_id[:8]}-{uuid.uuid4().hex[:6]}'
    chunks = (len(b64) + 89999) // 90000
    D.d1(f"""INSERT INTO files (id, created_at, filename, mime, size, chunks)
             VALUES ({D.q(fid)}, datetime('now','+8 hours'),
                     {D.q(os.path.basename(out).replace('anon_', '匿名履歷_'))},
                     'application/pdf', {len(b)}, {chunks})""")
    for i in range(chunks):
        D.d1(f"INSERT INTO file_chunks (file_id, idx, b64) VALUES "
             f"({D.q(fid)}, {i}, {D.q(b64[i * 90000:(i + 1) * 90000])})")
    log(f'✅ 匿名履歷 PDF 已存（{len(b)//1024} KB，{chunks} chunk）')
    return fid


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

    # ── 第一階段：挑人 ＋ 列名單（不上網，快）──
    spec = run_commander(
        PICK_PROMPT.format(ask=ask_text(req), pool=pool_text(ps),
                           n=req.get('target_count') or 6),
        workdir, tag='pick', timeout=900)
    json.dump(spec, open(os.path.join(workdir, 'pick.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    if not spec.get('picked') or not spec.get('companies'):
        why = spec.get('skip_reason') or '總指揮沒有挑到配得上的人選'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 開發需求「{req['role_family']}」沒有出信\n原因：{why}\n"
                        f"（配不上就不硬湊——寄一封不對題的信，那家公司以後就不看我們的信了）")
        return

    # ── 客戶名單比對要在查職缺之前 ──
    # 先擋掉不能碰的，免得白花十分鐘去查一家本來就不該敲的公司。
    clients = G.load_clients(D.d1)
    ok_co, blocked, warned = G.filter_targets(spec['companies'], clients)
    for b in blocked:
        log(f'  ⛔ 移除 {b["company"]}：{b["why"]}（命中名單上的「{b["matched"]}」）')

    # ── 第二階段：一家一次，各自去查職缺＋寫信 ──
    hl = '\n'.join('・' + h for h in (spec.get('highlights') or []))
    targets, no_anchor = [], []
    for i, c in enumerate(ok_co):
        name = c.get('company') if isinstance(c, dict) else str(c)
        log(f'  [{i + 1}/{len(ok_co)}] 查 {name} 在招什麼⋯')
        try:
            t = run_commander(
                LETTER_PROMPT.format(company=name, why=(c.get('why') if isinstance(c, dict) else ''),
                                     role=req['role_family'], highlights=hl),
                workdir, tag=f'letter{i}', timeout=900)
        except Exception as e:
            log(f'      ✗ 查不下去（{str(e)[:80]}），跳過這家')
            continue
        if not t.get('subject') or not t.get('body'):
            no_anchor.append(name)
            log(f'      · 查不到職缺、也不值得問問看 → 捨棄')
            continue
        anchor = ('探詢' if t.get('probe') else (t.get('job_title') or '無錨點'))
        log(f'      ✓ {anchor}　窗口：{t.get("contact_email") or "待補"}')
        targets.append(t)

    spec['targets'] = targets
    json.dump(spec, open(os.path.join(workdir, 'spec.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    if not targets:
        why = f'{len(ok_co)} 家都查不到對得上的職缺，也不值得探詢'
        D.d1(f"UPDATE bd_requests SET status='failed', result_note={D.q(why)}, "
             f"updated_at=datetime('now','+8 hours') WHERE id={D.q(rid)}")
        tg_bd.send_head(f"🟡 開發需求「{req['role_family']}」沒有出信\n原因：{why}")
        return

    # 就服法第 5 條掃描（客戶名單那一關在上面已經做過）
    law = scrub(spec)
    for lb, w in law:
        log(f'⚠️ 就服法第 5 條保護特徵出現在{lb}：「{w}」')

    batch = str(uuid.uuid4())[:8]
    auto = auto_mode() and not law     # 有法遵疑慮就一定要人看過，不自動寄
    cv_fid = make_anon_pdf(spec['picked'], workdir)
    rows = []
    for t in targets:
        bid = str(uuid.uuid4())
        rows.append((bid, t))
        D.d1(f"""INSERT INTO bd_outreach
          (id, created_at, batch_id, request_id, candidate_ref, candidate_card, company,
           why_company, contact_name, contact_email, subject, body, status, updated_at,
           cv_file_id, job_title, job_source, job_url, probe)
          VALUES ({D.q(bid)}, datetime('now','+8 hours'), {D.q(batch)}, {D.q(rid)},
                  {D.q(spec.get('candidate_ref') or batch)}, {D.q(spec.get('candidate_card'))},
                  {D.q(t.get('company'))}, {D.q(t.get('why'))}, {D.q(t.get('contact_name'))},
                  {D.q(t.get('contact_email'))}, {D.q(t.get('subject'))}, {D.q(t.get('body'))},
                  'pending', datetime('now','+8 hours'),
                  {D.q(cv_fid)}, {D.q(t.get('job_title'))}, {D.q(t.get('job_source'))},
                  {D.q(t.get('job_url'))}, {1 if t.get('probe') else 0})""")

    head = (f"🎯 開發需求：{req['role_family']}\n"
            f"配對到人選 {spec.get('candidate_ref')}　·　{len(targets)} 家出信"
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
        mid = tg_bd.send_letter(bid, t, has_cv=bool(cv_fid))
        if mid:
            D.d1(f"UPDATE bd_outreach SET tg_message_id={mid} WHERE id={D.q(bid)}")

    D.d1(f"UPDATE bd_requests SET status='done', batch_id={D.q(batch)}, "
         f"result_note={D.q(f'{len(targets)} 家送審，{len(blocked)} 家被客戶名單擋，{len(no_anchor)} 家沒錨點')}, "
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
