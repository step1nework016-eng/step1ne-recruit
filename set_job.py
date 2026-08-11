#!/usr/bin/env python3
"""填寫職缺的內部欄位——客戶身分，以及候選人在「反問時間」最常問的那些。

為什麼要有這支：這些欄位空著的話，阿財在面談時只能一直說
「這部分顧問會跟你說明」。連問三題都是這個答案，候選人會覺得
這場面談沒有意義，那 no-show 率跟後續配合度都會掉。

⚠️ client_name 會被講給候選人聽。不要填猜的。

用法：
    python3 set_job.py bim-engineer --client "某某科技" --intro "半導體廠務統包，員工約 300 人"
    python3 set_job.py bim-engineer --rounds "2 次" --who "一面人資＋用人主管，二面協理"
    python3 set_job.py --show bim-engineer      # 看目前填了什麼、還缺什麼
"""
import json, os, subprocess, sys, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = 'step1ne-recruit'

# 參數名 → 資料庫欄位。順序就是報表顯示順序。
FIELDS = [
    ('client',   'client_name',      '客戶真實名稱（會講給候選人聽）'),
    ('intro',    'client_intro',     '一兩句公司介紹：產業、規模、在做什麼'),
    ('manager',  'hiring_manager',   '用人主管'),
    ('team',     'team_size',        '目前編制，例：BIM 團隊 6 人，這次補 2 位'),
    ('rounds',   'interview_rounds', '用人單位要面幾次'),
    ('who',      'interview_who',    '分別跟誰面'),
    ('test',     'has_test',         '有無測驗與內容'),
    ('onboard',  'onboard_by',       '客戶希望到職日'),
    ('skills',   'must_skills',      '必備技能，逗號分隔'),
    ('faq',      'faq_notes',        '其他可以直接回答候選人的事'),
    ('notes',    'notes',            '顧問備註：這客戶在意什麼、踩過什麼雷（不對候選人講）'),
]

# 這幾欄空著，面談品質會直接看得出來
CRITICAL = {'client_name', 'client_intro', 'interview_rounds', 'team_size'}


def d1(sql):
    env = dict(os.environ)
    conf = os.path.expanduser('~/.config/workflow-os/cf.env')
    if os.path.exists(conf):
        for line in open(conf, encoding='utf-8'):
            if '=' in line and not line.startswith('#'):
                k, v = line.strip().split('=', 1)
                env[k] = v
    r = subprocess.run(
        ['npx', '--yes', 'wrangler', 'd1', 'execute', DB, '--remote', '--json',
         f'--command={sql}'],
        cwd=HERE, capture_output=True, text=True, env=env, timeout=180)
    if r.returncode != 0:
        sys.exit(f'D1 失敗：{(r.stderr or r.stdout)[-400:]}')
    out = r.stdout[r.stdout.index('['):]
    return json.loads(out)[0].get('results', [])


def show(slug):
    rows = d1(f"SELECT * FROM jobs WHERE slug = '{slug}'")
    if not rows:
        sys.exit(f'jobs 表裡沒有 {slug}')
    r = rows[0]
    print(f"\n  {r['title']}　（{slug}）\n")
    missing = []
    for arg, col, desc in FIELDS:
        v = r.get(col)
        if v:
            print(f"  ✅ {desc}\n       {v}")
        else:
            mark = '❌' if col in CRITICAL else '　'
            print(f"  {mark} {desc}　（空）")
            if col in CRITICAL:
                missing.append((arg, desc))
    if missing:
        print(f"\n  ⚠️ 這 {len(missing)} 欄空著，阿財只能回「顧問會跟你說明」：")
        for arg, desc in missing:
            print(f"       --{arg}  {desc}")
    print()


def main():
    if '--show' in sys.argv:
        i = sys.argv.index('--show')
        return show(sys.argv[i + 1] if len(sys.argv) > i + 1 else 'bim-engineer')

    p = argparse.ArgumentParser()
    p.add_argument('slug')
    for arg, col, desc in FIELDS:
        p.add_argument(f'--{arg}', help=desc)
    a = p.parse_args()

    sets = []
    for arg, col, _ in FIELDS:
        v = getattr(a, arg)
        if v is not None:
            sets.append(f"{col} = '" + v.replace("'", "''") + "'")
    if not sets:
        sys.exit('沒有給任何欄位。用 --show <slug> 看目前缺什麼。')

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sets.append(f"updated_at = '{now}'")
    d1(f"UPDATE jobs SET {', '.join(sets)} WHERE slug = '{a.slug}'")
    print(f'  ✅ 已更新 {len(sets) - 1} 欄')
    show(a.slug)


if __name__ == '__main__':
    main()
