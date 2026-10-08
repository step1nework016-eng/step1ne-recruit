"""E17-3 回放：對同一場面談，用「改前」與「改後」的報告 prompt 各跑一次 claude，比較報告。
只讀 D1；不寫任何資料；claude 在 /tmp/wt_e17 這個獨立目錄跑（session 檔不會跟正式阿財混在一起）；
有人正在面談時會等（不跟候選人搶 claude）。"""
import os, sys, json, time, glob, subprocess, datetime
os.environ.setdefault('INTERVIEW_HOST', 'test')
sys.path.insert(0, os.getcwd())
import interview_daemon as D
D.HERE = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]
D.log = lambda m: None
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
PICK = sys.argv[2].split(',')          # 姓名清單；前面加 ! 代表「只跑改後」（沒有計畫的場次）
LOG = open(os.path.join(OUT, 'progress.log'), 'a', encoding='utf-8')


def say(m):
    LOG.write(f'[{time.strftime("%H:%M:%S")}] {m}\n'); LOG.flush()


def wait_idle(max_wait=3600):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        r = D.d1("SELECT COUNT(*) n FROM applications WHERE interview_state='active'")
        if not r or r[0]['n'] == 0:
            return True
        say(f'有 {r[0]["n"]} 場面談進行中，等 30 秒再跑（不跟候選人搶 claude）')
        time.sleep(30)
    return False


def gen(prompt):
    t0 = time.time()
    r = subprocess.run([D.CLAUDE_BIN, '-p', '--model', D.REPORT_MODEL, *D.NO_TOOLS, '--output-format', 'json'],
                       input=D.sanitize(prompt), capture_output=True, text=True, env=D.env_with_cf(),
                       timeout=D.REPORT_TIMEOUT, cwd=os.getcwd())
    D._cli_unwrap(r, prompt, t0)
    txt = r.stdout.strip()
    if txt.startswith('```'):
        txt = txt.split('\n', 1)[-1]
        if txt.rstrip().endswith('```'):
            txt = txt.rstrip()[:-3].rstrip()
    return txt, time.time() - t0


for item in PICK:
    only_after = item.startswith('!')
    name = item.lstrip('!')
    rows = D.d1(f"SELECT a.id, a.name, a.job_slug FROM applications a WHERE a.name={D.q(name)} "
                f"AND a.interview_state IN ('done','paused') ORDER BY a.interview_started_at DESC LIMIT 1")
    if not rows:
        say(f'{name}：找不到結束的面談，略過'); continue
    a = rows[0]
    ctx = D.context_for(a['id'])
    has_plan = bool((ctx.get('plan') or {}).get('questions'))
    for variant in (('after',) if only_after else ('before', 'after')):
        path = os.path.join(OUT, f'{name}_{variant}.md')
        if os.path.exists(path):
            continue
        if not wait_idle():
            say('等不到空檔，停止'); sys.exit(1)
        prompt = D.build_report_prompt(a['id'], ctx, a['job_slug'], False, with_not_covered=(variant == 'after'))
        say(f'{name} {variant}：開始（有計畫={has_plan}，prompt {len(prompt)} 字）')
        try:
            txt, sec = gen(prompt)
        except Exception as e:
            say(f'{name} {variant}：失敗 {type(e).__name__} {str(e)[:150]}'); continue
        open(path, 'w', encoding='utf-8').write(txt)
        json.dump({'has_plan': has_plan, 'secs': round(sec, 1), 'chars': len(txt)}, open(path + '.meta', 'w'))
        say(f'{name} {variant}：完成 {sec:.0f} 秒、{len(txt)} 字')
say('全部完成')
