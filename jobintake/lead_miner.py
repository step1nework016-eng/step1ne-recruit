#!/usr/bin/env python3
"""從候選人講過的話裡挖出「哪些公司在徵人」，變成開發客戶的線索。

## 為什麼做這個

2026-09-23 盤點：11 家簽約客戶裡**只有 1 家真的有人到職**，官網近 90 天只進來
3 筆詢問。所以「找既有客戶要介紹」這條路現在幾乎是空的——你沒交付過，開不了口。

但有一個被完全忽略的來源：**候選人自己**。

每一位候選人都知道：
- 他前公司為什麼一直跑人
- 他現在待的公司哪個部門在擴編
- 同業哪幾家最近在大舉徵人

這些是**內線情報**，品質遠高於從 104 掃出來的公開職缺——而且我們的面談與電洽
逐字稿裡**早就有了**，只是從來沒有人去撈。

Jacky 自己寫過的規則反過來用就是這件事：
> 「找到一個人選＝同時拿到一張『去哪找同類人』的公司地圖」

## ⛔ 三條不可以踩的線

1. **不可以把候選人的個資或身分外洩到開發信裡**。線索只記「哪家公司有什麼跡象」，
   `source_person` 只留內部追溯用，**絕對不會出現在對外文件**。
2. **不可以把現任雇主當成挖角目標寫進開發信**——那是拿候選人的信任去換生意。
   只記錄「這家公司在徵人」這個事實本身。
3. **已經是我們客戶、或正在洽談的公司要濾掉**，不要重複開發。
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('d', os.path.join(ROOT, 'interview_daemon.py'))
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)

import re  # noqa: E402

import client_guard as G  # noqa: E402


def clean_company(name):
    """把 AI 塞進公司名裡的註解清掉再拿去比對客戶名單。

    ⚠️ 只寫在 prompt 裡不夠——2026-09-23 實際發生：AI 回
    「弘昌管理顧問（逐字稿亦作「紅昌」）」，括號讓 client_guard 比對不到
    「弘昌管理顧問有限公司」，兩家已簽約客戶就這樣被當成開發目標留下來。
    出口再洗一次，跟 is_recruiting_mail 同一個道理。
    """
    n = (name or '').strip()
    n = re.sub(r'[（(\[].*?[)）\]]', '', n)          # 去掉括號與裡面的註解
    n = re.split(r'[，,／/｜|]', n)[0]                # 「A／B」只取第一個
    return n.strip()

MODEL = 'claude-opus-5'   # ⚠️ 一定要寫全名


def log(m):
    print(m, flush=True)


PROMPT = """你要從候選人的面談與電洽紀錄裡，挖出「**哪些公司正在徵人或有人力缺口**」，
當作我們開發企業客戶的線索。

## 你要找的訊號

- 「我前公司那個部門一直在找人／一直跑人」
- 「我現在這家最近在擴編／開新廠／開新店」
- 「同業某某家最近大舉徵人」
- 「我之前待的地方缺很久了都補不到」
- 面試過哪幾家、對方急不急

## ⛔ 絕對不要做的事

1. **不要把候選人的姓名或可辨識資訊寫進 signal 那段文字。**
   signal 只描述「這家公司發生什麼事」，不要寫「某某說…」。
2. **不要把候選人「目前任職」的公司標成挖角目標。**
   只記錄客觀事實（那家在徵人），不要加任何「可以去挖他同事」的暗示。
3. **不要推測。** 逐字稿沒講的不要補。只記錄真的被講出來的。
4. 公司名要寫**全名或足以辨識的名稱**，「一家科技公司」這種沒有用，不要記。
5. ⚠️ **company 這一格只能放公司名本身，不可以加任何括號註解**。
   寫成「弘昌管理顧問（逐字稿亦作「紅昌」）」會害系統比對不到客戶名單，
   結果就是**去開發自己的客戶**——2026-09-23 真的發生過。
   逐字稿有別的寫法要講，寫進 signal 或 quote，不要寫進 company。

## 我們現有的客戶與洽談中的公司

{client_names}

⚠️ **逐字稿是電洽錄音轉出來的，公司名常被轉成同音別字**
（真實案例：弘昌→紅昌、帆宣→凡宣／樊宣）。
逐字稿裡的公司名只要跟上面任何一家**讀音相近**，一律當成那一家，
company 寫上面那個正確的名字。這些公司不是新線索，系統之後會自動濾掉。

## 只輸出這個 JSON

{{"leads":[{{
  "company":"公司名，要具體",
  "signal":"這家公司的人力狀況，一到兩句，**不可出現候選人身分**",
  "signal_type":"擴編|長期補不到|人員流動高|新據點|面試中|其他",
  "confidence":"high|medium|low —— high 只給『逐字稿裡明確講出來』的",
  "quote":"逐字稿裡支持這個判斷的原文片段，最多 40 字"
}}],
"run_summary":"看了幾份、挖出幾條、大部分卡在哪"}}

沒有任何可用線索就回 {{"leads":[],"run_summary":"原因"}}。**寧可空手也不要編。**

## 要看的紀錄

{records}
"""


def fetch_records(limit=40):
    rows = D.d1(
        "SELECT id, name, job_slug, call_summary_md, consultant_call_notes "
        "FROM applications "
        "WHERE (call_summary_md IS NOT NULL AND call_summary_md<>'') "
        "   OR (consultant_call_notes IS NOT NULL AND consultant_call_notes<>'') "
        f"ORDER BY created_at DESC LIMIT {int(limit)}")
    out = []
    for r in rows:
        txt = ((r.get('call_summary_md') or '') + '\n' + (r.get('consultant_call_notes') or '')).strip()
        if len(txt) < 80:
            continue   # 太短的沒有情報量，丟進去只是浪費 token
        out.append((r['id'], r.get('name'), txt[:4000]))
    return out


def run_claude(prompt, workdir):
    os.makedirs(workdir, exist_ok=True)
    open(os.path.join(workdir, 'leads.prompt.txt'), 'w', encoding='utf-8').write(prompt)
    r = subprocess.run(
        ['claude', '--print', '--model', MODEL,
         '--setting-sources', '', '--permission-mode', 'bypassPermissions'],
        input=prompt, capture_output=True, text=True, cwd=workdir,
        env=D.env_with_cf(), timeout=1800)
    out = (r.stdout or '').strip()
    open(os.path.join(workdir, 'leads.raw.txt'), 'w', encoding='utf-8').write(out)
    if not out:
        raise RuntimeError('總指揮沒有回應')
    s = out.find('{')
    obj, _ = json.JSONDecoder().raw_decode(out[s:])
    return obj


def main():
    recs = fetch_records()
    if not recs:
        log('沒有可以挖的紀錄')
        return
    log(f'要看 {len(recs)} 份紀錄')

    blocks = []
    for aid, name, txt in recs:
        # candidate id 只寫在標頭給我們自己追溯，AI 被明令不可把身分寫進 signal
        blocks.append(f'--- [內部代號 {aid[:8]}]\n{txt}\n')

    wd = os.path.join(HERE, 'bd_work', 'leads')
    # 第一層：把客戶名單交給 AI，讓它在源頭就把同音別字對回正確名字。
    # 第二層在 client_guard.check() 的讀音比對——AI 漏掉的，程式再擋一次。
    names = sorted({c['name'] for c in G.load_clients(D.d1)})
    spec = run_claude(PROMPT.format(records='\n'.join(blocks),
                                    client_names='\n'.join(f'- {n}' for n in names)), wd)
    leads = spec.get('leads') or []
    log(f"AI 挖出 {len(leads)} 條｜{spec.get('run_summary','')[:120]}")

    clients = G.load_clients(D.d1)
    kept = 0
    for x in leads:
        co = (x.get('company') or '').strip()
        if not co:
            continue
        # 已經是客戶／洽談中的濾掉，不要重複開發
        # 比對用洗過的名字，存進資料庫的也用洗過的——
        # 留著括號註解的話，之後每一次比對都會再漏一次
        co = clean_company(co) or co
        hit = G.check(co, clients)
        if hit:
            log(f"  ⏭ {co}：{hit.get('why')}")
            continue
        # 同一家公司已經記過就不要重複（同一個訊號來源可能被多位候選人提到）
        dup = D.d1(f"SELECT id FROM company_leads WHERE company={D.q(co)} "
                   "AND status IN ('new','working')")
        if dup:
            log(f"  ⏭ {co}：已經記錄過")
            continue
        D.d1(f"""INSERT INTO company_leads
              (company, signal, signal_type, source_kind, note, status, created_at, updated_at)
              VALUES ({D.q(co)}, {D.q(x.get('signal'))}, {D.q(x.get('signal_type'))},
                      'candidate_interview', {D.q('可信度 ' + str(x.get('confidence')) + '｜原文：' + str(x.get('quote'))[:120])},
                      'new', datetime('now','+8 hours'), datetime('now','+8 hours'))""")
        kept += 1
        log(f"  ✅ {co}｜{x.get('signal_type')}｜{str(x.get('signal'))[:50]}")

    log(f'\n寫入 {kept} 條新線索')
    if kept:
        try:
            import tg_bd
            rows = D.d1("SELECT company, signal, signal_type FROM company_leads "
                        "WHERE status='new' ORDER BY id DESC LIMIT 10")
            body = '\n\n'.join(f"・{r['company']}（{r['signal_type']}）\n　{r['signal']}" for r in rows)
            tg_bd.send_head(f'🕵️ 從候選人講過的話挖到 {kept} 家公司線索\n\n{body}\n\n'
                            '這些是候選人親口講的內線情報，不是從 104 掃出來的公開職缺。\n'
                            '要開發哪一家跟我說，我把它排進開發需求。')
        except Exception as e:
            log(f'通知失敗（不影響結果）：{e}')


if __name__ == '__main__':
    main()
