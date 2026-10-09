"""E17c：題目計畫有沒有用到「初篩結果」？（交卷前就擬 = 沒有測驗結果、通常也還沒有初篩）
對同一位人選：
  A1／A2 = 完整素材（有測驗、有初篩；各跑一次，兩次差異＝模型本身的隨機雜訊）
  B      = 拿掉測驗結果＋初篩結果（模擬「剛同意、還沒交卷就先擬」）
比較：A1 vs A2（雜訊基準）、A1 vs B。只讀 D1；claude 在獨立目錄跑；有人面談時等。"""
import os, sys, json, time, glob, subprocess
os.environ.setdefault('INTERVIEW_HOST', 'test')
HERE = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]
os.chdir(HERE); sys.path.insert(0, HERE)
import interview_daemon as D
D.HERE = HERE
D.log = lambda m: None
OUT = sys.argv[1]; NAMES = sys.argv[2].split(',')
os.makedirs(OUT, exist_ok=True)
os.makedirs('/tmp/e17c_cwd', exist_ok=True)
LOG = open(os.path.join(OUT, 'progress.log'), 'a', encoding='utf-8')
def say(m): LOG.write(f'[{time.strftime("%H:%M:%S")}] {m}\n'); LOG.flush()

def wait_idle():
    t0 = time.time()
    while time.time() - t0 < 3600:
        r = D.d1("SELECT COUNT(*) n FROM applications WHERE interview_state='active'")
        if not r or r[0]['n'] == 0: return True
        time.sleep(30)
    return False

def run_plan(material):
    t0 = time.time()
    r = subprocess.run([D.CLAUDE_BIN, '-p', '--model', D.TALK_MODEL, *D.NO_TOOLS, '--output-format', 'json'],
                       input=D.sanitize(material + '\n\n─────────────\n' + D.PLAN_PROMPT), capture_output=True, text=True,
                       env=D.env_with_cf(), timeout=400, cwd='/tmp/e17c_cwd')
    D._cli_unwrap(r, material, t0)
    obj = D._extract_json(r.stdout)
    return obj, time.time() - t0

for name in NAMES:
    a = D.d1(f"SELECT id, name FROM applications WHERE name={D.q(name)} AND interview_state IN ('done','paused') ORDER BY interview_started_at DESC LIMIT 1")[0]
    ctx = D.context_for(a['id'])
    say(f'{name}：有測驗結果={bool(ctx.get("assessment"))} 有初篩={bool(ctx.get("screening"))}')
    ctx_no = dict(ctx); ctx_no.pop('assessment', None); ctx_no.pop('screening', None)
    mat_a = D.build_prompt(ctx, '（面談規範這裡不需要，你只是在擬題目）')
    mat_b = D.build_prompt(ctx_no, '（面談規範這裡不需要，你只是在擬題目）')
    say(f'  素材差：完整 {len(mat_a)} 字 / 無測驗無初篩 {len(mat_b)} 字（差 {len(mat_a) - len(mat_b)} 字）')
    for tag, mat in (('A1', mat_a), ('A2', mat_a), ('B', mat_b)):
        path = os.path.join(OUT, f'{name}_{tag}.json')
        if os.path.exists(path): continue
        if not wait_idle(): sys.exit(1)
        say(f'  {name} {tag}：開始')
        try:
            obj, sec = run_plan(mat)
            json.dump(obj, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            say(f'  {name} {tag}：完成 {sec:.0f} 秒，{len((obj or {}).get("questions") or [])} 題')
        except Exception as e:
            say(f'  {name} {tag}：失敗 {type(e).__name__} {str(e)[:120]}')
say('全部完成')
