#!/usr/bin/env python3
"""職缺「做事風格」行為題產生器（2026-10-03 Jacky 拍板：每個職缺都要有）。

為什麼要有這支：
    分級階梯題（build_ladder.py）量的是「專業到第幾級」，量不出「這個人怎麼做事」——
    遇到狀況怎麼處理、怎麼帶人、怎麼跟老闆溝通、怎麼推動改變。
    用人主管常常專業沒問題，卡在「這個人跟我們合不合」。直接問「你是什麼個性」
    人選只會挑好聽的講，所以一律用行為題：「說一個你實際遇過的狀況」，
    從他講出來的具體做法判斷風格，並留原話當證據。

每個職缺產出：
    依職級決定題數（由程式決定，不交給模型）：
        資深／主管職 → 3 個面向；一般 → 2 個；培訓／無經驗可 → 1 個（面談不能拉太長）
    每個面向：名稱、用人主管為什麼在意、一題行為題、追問、
    「做得好的人會講到」、「要留意的訊號」、以及兩種常見風格的對照（不是好壞，是合不合這個職缺）。
    顧問之後可以在職缺卡上改。

存放：job_expertise.workstyle_json／workstyle_built_at（只加欄位，其他欄位不動）。

用法：
    python3 build_workstyle.py <job_slug> --dry-run
    python3 build_workstyle.py <job_slug>
    python3 build_workstyle.py --all [--dry-run] [--missing]   # --missing＝只補還沒有的
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_expertise as BE  # 共用 d1()／q()／log()／claude 呼叫設定與花費紀錄
import build_ladder as BL     # 共用職級判斷字詞

DRY_DIR = os.path.join(HERE, 'workstyle_dryrun')

PROMPT = '''你要為一個職缺設計「做事風格」行為面談題，讓 AI 面談助理在文字面談中，
從候選人講的真實經驗判斷他「怎麼做事」，給顧問與用人主管參考。

【這個職缺】
職稱：{title}
職級：{level_desc}
工作地點：{locations}
工作內容與條件（用人單位原文）：
{jd}

【要做的】
挑出 {n} 個「這個職缺最關鍵、最會決定人做不做得久、做不做得好」的做事風格面向。
例：主管職看「推動改變的方式」「向上溝通與爭取資源」「帶人與授權」「出包時怎麼處理」；
客服看「面對情緒激動的人」「壓力下的自我調適」；業務看「被拒絕後怎麼做」「經營長期關係」；
助理／行政看「同時多件事怎麼排優先」「主動回報的習慣」。依這個職缺實際內容挑，不要套模板。

【硬性要求】
1. 每個面向一題「行為題」：請他講一個實際發生過的狀況（什麼情況、他做了什麼、結果如何），
   不准問「你是什麼樣的人」「你覺得自己 X 嗎」這種讓人挑好聽的講的題目。
   沒有正式工作經驗的人也要答得出來（可以講學校、打工、社團、生活中的例子）。
2. 附一句追問：他講得太空泛時，怎麼把他拉回具體的那一次。
3. good_signs：做得好的人會講到的具體行為（2～3 點）。watch_signs：要留意的訊號（2 點）。
4. styles：這個面向常見的兩種風格各一句，例如「先自己扛下來處理」vs「第一時間拉相關的人一起處理」——
   不是好壞，是給顧問判斷合不合這個職缺用；fit_hint 說這個職缺比較需要哪一種、為什麼。
5. 題目口語、像人在講話；全部台灣繁體中文，不可以有簡體字。
6. 絕對不可以碰年齡、性別、婚育、家庭、國籍、宗教、政黨、健康、外貌（就業服務法第 5 條），
   也不要問「抗壓性高不高」這種主觀自評。

【只輸出這個 JSON，不要有其他文字】
{{
  "dimensions": [
    {{"name": "面向名稱，10 字內",
      "why": "用人主管為什麼在意，一句話",
      "q": "行為題原句",
      "followup": "講太空泛時的追問",
      "good_signs": ["…", "…"],
      "watch_signs": ["…", "…"],
      "styles": ["風格一：一句話", "風格二：一句話"],
      "fit_hint": "這個職缺比較需要哪一種、為什麼"}}
  ]
}}'''


TIER_COUNT = {'basic': (1, '基層服務'), 'pro': (2, '一般專業'), 'manager': (3, '中階主管'), 'exec': (3, '高階主管')}


def count_for(job):
    """面向數量由程式決定：高階／中階 3、一般專業 2、基層 1——面談長度要控制住。
    優先看 job_expertise.screen_tier（build_screen_tier.py 判的，顧問可改），沒有才退回看 seniority。"""
    try:
        t = BE.d1(f"SELECT screen_tier FROM job_expertise WHERE job_slug = {BE.q(job['slug'])}")
        tier = (t[0].get('screen_tier') if t else None)
        if tier in TIER_COUNT:
            return TIER_COUNT[tier]
    except Exception:
        pass
    sen = (job.get('seniority') or '').lower()
    title = job.get('title') or ''
    if sen in ('junior', 'entry') or any(w in title for w in BL._TRAINEE_WORDS):
        return 1, '培訓／無經驗可'
    if sen == 'senior' or any(w in title for w in BL._SENIOR_WORDS):
        return 3, '資深／主管職'
    return 2, '一般職'


def _valid(obj, n):
    ds = (obj or {}).get('dimensions') or []
    if not (1 <= len(ds) <= n + 1):
        return False
    return all((d.get('name') or '').strip() and (d.get('q') or '').strip() for d in ds)


def _jd_text(job):
    try:
        spec = json.loads(job.get('jd_spec_json') or '{}') or {}
    except Exception:
        spec = {}

    def _t(v):
        return '\n'.join(f'・{x}' for x in v) if isinstance(v, list) else (v or '')
    parts = []
    # 只取公開欄位；窗口電話、保護名單、合約條款這類只給顧問的欄位不進 AI。
    for label, v in (('工作內容', job.get('main_duties') or _t(spec.get('duties')) or job.get('description')),
                     ('資格條件', job.get('required_conditions') or _t(spec.get('must')) or job.get('requirements')),
                     ('個人特質', job.get('personality_traits')),
                     ('加分條件', _t(spec.get('plus')) or job.get('nice_to_have_skills'))):
        v = _t(v).strip()
        if v:
            parts.append(f'■ {label}\n{v}')
    return '\n\n'.join(parts) or '（JD 內容不足，請依職稱推導）'


def build(job, dry=False):
    slug = job['slug']
    n, level_desc = count_for(job)
    prompt = PROMPT.format(title=job.get('title') or slug, level_desc=level_desc,
                           locations=job.get('locations') or '未提供', jd=_jd_text(job), n=n)
    BE.log(f'{slug}：產做事風格題（{level_desc}，{n} 個面向）…')
    before = BE._snapshot()
    r = subprocess.run(['claude', '-p', prompt, '--model', BE.MODEL, '--disallowed-tools', BE._BAN + ',WebSearch,WebFetch',
                        '--setting-sources', '', '--output-format', 'text'],
                       cwd=HERE, capture_output=True, text=True, env=BE.env_with_cf(), timeout=300)
    BE._log_tokens(f'job:{slug}', 'workstyle', prompt, before)
    obj = BE._extract_json(r.stdout)
    if not _valid(obj, n):
        BE.log(f'❌ {slug}：做事風格題格式不完整，不收。回覆開頭：{(r.stdout or r.stderr or "")[:200]}')
        return None
    obj['dimensions'] = obj['dimensions'][:n]
    try:
        import zh_trad
        obj = json.loads(zh_trad.to_traditional(json.dumps(obj, ensure_ascii=False)))
    except Exception:
        pass
    if dry:
        os.makedirs(DRY_DIR, exist_ok=True)
        path = os.path.join(DRY_DIR, f'{slug}.json')
        json.dump(obj, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        BE.log(f'✅ {slug}（試產，沒寫資料庫）→ {path}')
        return obj
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    BE.d1(f"INSERT INTO job_expertise (job_slug, topics_json, questions_json, built_at, workstyle_json, workstyle_built_at) "
          f"VALUES ({BE.q(slug)}, '[]', '[]', {BE.q(now)}, {BE.q(json.dumps(obj, ensure_ascii=False))}, {BE.q(now)}) "
          f"ON CONFLICT(job_slug) DO UPDATE SET workstyle_json=excluded.workstyle_json, "
          f"workstyle_built_at=excluded.workstyle_built_at")
    BE.log(f'✅ {slug}：' + '、'.join(d['name'] for d in obj['dimensions']) + '，已寫入')
    return obj


def main():
    dry = '--dry-run' in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--all' in sys.argv:
        extra = (" AND slug NOT IN (SELECT job_slug FROM job_expertise WHERE workstyle_json IS NOT NULL)"
                 if '--missing' in sys.argv else '')
        jobs = BE.d1("SELECT * FROM jobs WHERE status IN ('open','active') AND slug != 'unspecified'" + extra + " ORDER BY slug")
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
