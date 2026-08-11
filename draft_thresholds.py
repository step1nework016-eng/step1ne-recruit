#!/usr/bin/env python3
"""幫每個職缺草擬 scoring_notes（初篩要用的門檻）。

為什麼需要這支：2026-08-05 證明了初篩準不準不在程式，在 scoring_notes 有沒有寫。
BIM 那個缺補上之後，同一個人分數從 10 → 85，跟顧問的實際決定一致。
但 17 個職缺裡有 15 個連 must_skills 都是空的——沒有 scoring_notes 的話，
初篩等於沒有判準可以比對。

⚠️ 這支只產**草稿**，不是最終判準。真正的門檻只有顧問知道，
所以每一份都要標明「哪幾條是我從資料推的、哪幾條需要人確認」。

兩種模式（2026-08-05 使用者定的）：
  門檻制（派遣／大量／急件）：過了門檻就全送，客戶自己篩。不要排序。
  兩段式（中高階）：硬門檻先擋（過不了直接刷），過了的再用匹配度排序前 3-5 個。

用法：
    python3 draft_thresholds.py --dry     # 只印不寫
    python3 draft_thresholds.py           # 寫進 jobs.scoring_notes
    python3 draft_thresholds.py <slug>    # 只做一個
"""
import os, sys, json, re, subprocess, argparse, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('d', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

MODEL = 'claude-sonnet-5'


def build(job):
    return f"""你是資深獵頭主管。幫下面這個職缺寫一份「初篩門檻」，
給 AI 初篩程式當判準用。

## 兩種模式，先判斷這個缺是哪一種
**門檻制** —— 派遣／大量需求／急件。過了門檻就全部送，客戶自己再篩。
  特徵：TEMPORARY、要補很多人、onboard_by 寫急、薪資帶偏低、年資門檻低。
  這種缺挑三揀四反而誤事。

**兩段式** —— 中高階職缺。硬門檻先擋（過不了直接刷掉），
  過了的再比匹配度，只送前 3-5 個。
  特徵：主管職、年資要求高、薪資帶高、FULL_TIME、客戶只想看少數人。
  這種缺送錯人會傷信譽。

## 寫出來要包含
1. **模式**：門檻制 或 兩段式，一句話說為什麼
2. **硬門檻**（過不了直接刷掉）：3–5 條，每條都要能用履歷或表單判斷。
   ⚠️ 不要寫「有相關經驗」這種判斷不了的，要寫「X 年以上 Y 經驗」「能否 Z」。
3. **不准拿來扣分的**：這個缺特別容易被誤判的地方。
   例如缺人缺很急的缺，「薪資落差」「職涯目標不合」不該扣分。
4. **兩段式才要寫**：過了門檻之後，比較誰更好時看哪三件事（依重要性排序）
5. **需要人確認的**：你從資料推不出來、但會影響判斷的事，條列出來。
   ⚠️ 這一段很重要——不要為了寫滿而編。推不出來就老實說。

## 職缺資料
職稱：{job.get('title')}
客戶：{job.get('client_name') or '（未綁）'}
僱用型態：{job.get('employment') or '（未填）'}
年資門檻：{job.get('years_min') if job.get('years_min') is not None else '（未填）'}
薪資：{job.get('salary_min') or '?'} – {job.get('salary_max') or '?'} {job.get('salary_unit') or ''}
地點：{job.get('locations') or '（未填）'}
必備技能：{job.get('must_skills') or '（空的，這正是要靠你補的）'}
到職期限：{job.get('onboard_by') or '（未填）'}
面試關數：{job.get('interview_rounds') or '（未填）'}
客戶硬性條件：{job.get('client_screen_conditions') or '（無）'}

## 顧問寫的背景說明（最重要，判準多半藏在這裡）
{job.get('notes') or '（無）'}

## 輸出
直接寫成給 AI 讀的純文字，不要 JSON、不要 markdown 標題符號，
用「【模式】【硬門檻】【不准扣分】【排序看什麼】【需要人確認】」這五個中括號分段。
結尾固定加一行：（2026-08-05 由 AI 依職缺資料草擬，未經顧問確認）
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slug', nargs='?')
    ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()

    where = f"slug={D.q(a.slug)}" if a.slug else "slug<>'bim-engineer' AND (scoring_notes IS NULL OR scoring_notes='')"
    jobs = D.d1(f"""SELECT slug,title,client_name,employment,years_min,salary_min,salary_max,
                    salary_unit,locations,must_skills,onboard_by,interview_rounds,
                    client_screen_conditions,notes FROM jobs WHERE {where}""")
    print(f'要草擬 {len(jobs)} 個職缺\n')
    for j in jobs:
        r = subprocess.run(['claude', '-p', D.sanitize(build(j)), '--model', MODEL,
                            *D.NO_TOOLS, '--output-format', 'text'],
                           capture_output=True, text=True, env=D.env_with_cf(), timeout=300)
        txt = (r.stdout or '').strip()
        if not txt:
            print(f"❌ {j['title'][:24]}：模型沒回應　{(r.stderr or '')[-150:]}")
            continue
        mode = '門檻制' if '門檻制' in txt[:120] else ('兩段式' if '兩段式' in txt[:120] else '?')
        print(f"✅ {j['title'][:26]:<28}{mode}　{len(txt)} 字")
        if a.dry:
            print('   ' + txt[:200].replace('\n', '\n   ') + '…\n')
        else:
            D.d1(f"UPDATE jobs SET scoring_notes={D.q(txt)} WHERE slug={D.q(j['slug'])}")


if __name__ == '__main__':
    main()
