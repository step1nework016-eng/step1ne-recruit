#!/usr/bin/env python3
"""職缺專業「分級階梯題」產生器（2026-09-29 Jacky 拍板）。

為什麼要有這支：
    原本的專業題庫（build_expertise.py）每個職缺只有一種深度——題目太深，
    轉職者一題都答不出來、面談早早收尾；題目太淺，資深跟新手分不出來。
    Jacky 要的是阿財像考官：從簡單的問起，答得好就往上一級，答不出來就停，
    最後判斷「這個人到第幾級」，而且資深版、培訓版都用同一套能力，
    只是開始問的級數、期待的級數不同。

每個職缺產出：
    3～5 個專業主題 × 第 1～5 級，每一級一題＋「答得好的樣子」＋「含糊的訊號」＋追問。
    開始級 start_level、期待級 expected_level 由程式依職級決定（不交給模型）：
        資深（seniority=senior 或職稱含「資深／主管／經理」）→ 從第 3 級問，期待第 4 級
        培訓／無經驗可（seniority=junior/entry 或職稱含「培訓／儲備／實習」）→ 從第 1 級問，期待第 1～2 級
        其他 → 從第 2 級問，期待第 3 級
    顧問之後可以在職缺卡上改這兩個數字。

存放：job_expertise.ladder_json／start_level／expected_level（只加欄位，原本的 questions_json 不動）。

用法：
    python3 build_ladder.py <job_slug> --dry-run     # 只產出、存到本機檔案給人看，不寫資料庫
    python3 build_ladder.py <job_slug>               # 產出並寫進資料庫
    python3 build_ladder.py --all [--dry-run]        # 所有招募中職缺
"""
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_expertise as BE  # 共用 d1()／q()／log()／claude 呼叫設定與花費紀錄

DRY_DIR = os.path.join(HERE, 'ladder_dryrun')

LADDER_PROMPT = '''你要為一個職缺建立「專業分級階梯題」，讓 AI 面談助理像考官一樣：
從簡單的問起，答得好就往上一級，答不出來就停，最後判斷候選人「到第幾級」。

【這個職缺】
職稱：{title}
公司／產業背景：{client_hint}
工作地點：{locations}
這個職缺從第 {start} 級開始問，期待候選人到第 {expected} 級。
工作內容與條件（用人單位原文）：
{jd}

【先查資料】用 WebSearch 查這個職務在台灣實際的工作內容、新人跟資深者的差別在哪裡、
這一行怎麼判斷一個人是不是真的做過。不要只查職稱的字面定義。

【分級定義——每個主題都照這五級出題】
第 1 級 基礎：知道這是什麼、看得懂（例：看得懂圖、知道流程有哪幾步）
第 2 級 入門：學過、練習過、在指導下做過（例：學校專題、實習、跟著前輩做過）
第 3 級 獨立作業：能自己從頭完成一件標準的工作（例：自己畫完一張圖、自己跑完一個流程）
第 4 級 資深：能處理例外、衝突、判斷取捨（例：兩個專業打架誰讓誰、臨時狀況怎麼處理）
第 5 級 帶人：能教人、訂規範、檢查別人的成果（例：新人的圖怎麼檢查、常見錯在哪）

【硬性要求】
1. 挑 3～5 個這個職務真正關鍵的專業主題（依職務複雜度決定），每個主題第 1～5 級各一題。
2. **非技術職也要分級**：客服、接待、司機、行政、業務也一樣，主題換成實際處理狀況的能力
   （例：客訴處理、行程安排、路線與安全判斷），第 1 級是「知道基本流程」，第 5 級是「能教新人、訂 SOP」。
3. 題目要問「做過的具體經驗」或「請他當場講怎麼做」，不要問「你熟不熟 X」。
   第 1～2 級也可以問學校專題、實習、自學做過的東西——給沒有正式工作經驗的人有機會答。
4. 每一級附 good_signs（答得好會講到什麼）、red_flags（含糊的樣子）、followup（含糊時追問一句）。
   這是給 AI 判斷「要不要往上一級／要不要追問」用的，不是判對錯。
5. 題目要口語，像人在講話；全部台灣繁體中文，不可以有簡體字。
6. 不要出違反就業服務法第 5 條的題目（年齡、性別、婚育、國籍、宗教、政黨…）。

【只輸出這個 JSON，不要有其他文字】
{{
  "domain": "專業領域，10 字內",
  "topics": [
    {{
      "name": "主題名稱",
      "why": "用人主管為什麼在意，一句話",
      "levels": [
        {{"level": 1, "can_do": "到這一級代表他能做到什麼（一句話，報告會用）",
          "q": "要問的問題", "good_signs": ["…"], "red_flags": ["…"], "followup": "含糊時追問"}},
        {{"level": 2, "can_do": "…", "q": "…", "good_signs": ["…"], "red_flags": ["…"], "followup": "…"}},
        {{"level": 3, "can_do": "…", "q": "…", "good_signs": ["…"], "red_flags": ["…"], "followup": "…"}},
        {{"level": 4, "can_do": "…", "q": "…", "good_signs": ["…"], "red_flags": ["…"], "followup": "…"}},
        {{"level": 5, "can_do": "…", "q": "…", "good_signs": ["…"], "red_flags": ["…"], "followup": "…"}}
      ]
    }}
  ],
  "sources": ["實際查過並採用的網址"]
}}'''

_SENIOR_WORDS = ('資深', '主管', '經理', 'Head', 'head', 'Manager', 'Controller', '總監', '協理')
_TRAINEE_WORDS = ('培訓', '儲備', '實習', 'Trainee', 'trainee')


def levels_for(job):
    """開始級／期待級由程式決定，不問模型——同一個職缺每次產出都要一樣。"""
    sen = (job.get('seniority') or '').lower()
    title = job.get('title') or ''
    if sen in ('junior', 'entry') or any(w in title for w in _TRAINEE_WORDS):
        return 1, 2 if any(w in title for w in _TRAINEE_WORDS) and sen not in ('junior', 'entry') else 1
    if sen == 'senior' or any(w in title for w in _SENIOR_WORDS):
        return 3, 4
    return 2, 3


def _valid(obj):
    """每個主題都要剛好 1～5 級、每級都有題目，不然整份不收。"""
    tps = (obj or {}).get('topics') or []
    if not (3 <= len(tps) <= 6):
        return False
    for t in tps:
        lv = sorted(int(x.get('level') or 0) for x in (t.get('levels') or []))
        if lv != [1, 2, 3, 4, 5] or not all((x.get('q') or '').strip() for x in t['levels']):
            return False
    return True


def build(job, dry=False):
    slug = job['slug']
    start, expected = levels_for(job)
    # ⚠️ 9/29 實測：description/requirements 這兩欄在多數職缺是空的，真正的工作內容在
    # main_duties 與 jd_spec_json（職缺頁公開的那幾段）。只讀舊欄位＝只憑職稱出題。
    # 只取公開欄位；窗口電話、保護名單、合約條款這類只給顧問的欄位不進 AI。
    try:
        spec = json.loads(job.get('jd_spec_json') or '{}') or {}
    except Exception:
        spec = {}
    def _t(v):
        return '\n'.join(f'・{x}' for x in v) if isinstance(v, list) else (v or '')
    jd_parts = []
    for label, v in (('工作內容', job.get('main_duties') or _t(spec.get('duties')) or job.get('description') or spec.get('description')),
                     ('職缺說明', spec.get('description') if job.get('main_duties') else ''),
                     ('資格條件', _t(spec.get('must')) or job.get('requirements')),
                     ('必備技能', job.get('must_skills')),
                     ('加分條件', _t(spec.get('plus')) or job.get('nice_skills'))):
        v = _t(v).strip()
        if v:
            jd_parts.append(f'■ {label}\n{v}')
    prompt = LADDER_PROMPT.format(
        title=job.get('title') or slug,
        client_hint=job.get('industry') or '（未提供，請依職務內容判斷）',
        locations=job.get('locations') or '未提供',
        start=start, expected=expected,
        jd='\n\n'.join(jd_parts) or '（JD 內容不足，請依職稱與產業推導）')
    BE.log(f'{slug}：產分級階梯題（從第 {start} 級問、期待第 {expected} 級）…')
    before = BE._snapshot()
    r = subprocess.run(['claude', '-p', prompt, '--model', BE.MODEL, *BE.RESEARCH_TOOLS,
                        '--output-format', 'text'], cwd=HERE, capture_output=True, text=True,
                       env=BE.env_with_cf(), timeout=BE.TIMEOUT)
    BE._log_tokens(f'job:{slug}', 'ladder', prompt, before)
    obj = BE._extract_json(r.stdout)
    if not _valid(obj):
        BE.log(f'❌ {slug}：階梯題格式不完整，不收。回覆開頭：{(r.stdout or r.stderr or "")[:200]}')
        return None
    obj['start_level'], obj['expected_level'] = start, expected
    # 階梯題也要一律繁體（9/29 阿財出現簡體字的同一個問題）
    try:
        import zh_trad
        obj = json.loads(zh_trad.to_traditional(json.dumps(obj, ensure_ascii=False)))
    except Exception:
        pass
    if dry:
        os.makedirs(DRY_DIR, exist_ok=True)
        path = os.path.join(DRY_DIR, f'{slug}.json')
        json.dump(obj, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        BE.log(f'✅ {slug}（試產，沒寫資料庫）：{len(obj["topics"])} 個主題 → {path}')
        return obj
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    BE.d1(f"INSERT INTO job_expertise (job_slug, domain, topics_json, questions_json, built_at, "
          f"ladder_json, start_level, expected_level, ladder_built_at) VALUES ({BE.q(slug)}, "
          f"{BE.q(obj.get('domain'))}, '[]', '[]', {BE.q(now)}, "
          f"{BE.q(json.dumps(obj, ensure_ascii=False))}, {start}, {expected}, {BE.q(now)}) "
          f"ON CONFLICT(job_slug) DO UPDATE SET ladder_json=excluded.ladder_json, "
          f"start_level=excluded.start_level, expected_level=excluded.expected_level, "
          f"ladder_built_at=excluded.ladder_built_at")
    BE.log(f'✅ {slug}：{obj.get("domain")} — {len(obj["topics"])} 個主題 × 5 級，已寫入')
    return obj


def main():
    dry = '--dry-run' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--all' in sys.argv:
        jobs = BE.d1("SELECT * FROM jobs WHERE status IN ('open','active') AND slug != 'unspecified' ORDER BY slug")
    elif args:
        jobs = BE.d1(f"SELECT * FROM jobs WHERE slug = {BE.q(args[0])}")
    else:
        print(__doc__)
        return
    for j in jobs:
        try:
            build(j, dry=dry)
        except Exception as e:
            BE.log(f'❌ {j.get("slug")}：{e}')


if __name__ == '__main__':
    main()
