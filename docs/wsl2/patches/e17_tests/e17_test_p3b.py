"""E17-3 補強：證明 with_not_covered=False 產生的報告 prompt，和「改動前真正的 finish()」送給 claude 的 prompt 逐字相同。
做法：在改動前的程式（origin/main）上，把 subprocess.run 換成「抓下 input 就中止」，D1 的寫入一律攔下；
再用改動後的 build_report_prompt(with_not_covered=False) 組一次，兩邊比。唯讀、不呼叫 claude。"""
import os, sys, glob, importlib.util
os.environ.setdefault('INTERVIEW_HOST', 'test')

def load(path, name):
    sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(name, os.path.join(path, 'interview_daemon.py'))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    sys.path.remove(path); return m

BASE = load('/tmp/wt_e17_base', 'daemon_base')
NEW = load(os.getcwd(), 'daemon_new')
REPO = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]
for m in (BASE, NEW):
    m.HERE = REPO; m.log = lambda *a, **k: None
REAL_D1 = BASE.d1
writes = []
def ro(sql, *a, **k):
    if sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE', 'REPLACE')):
        writes.append(sql[:60]); return []
    return REAL_D1(sql, *a, **k)
BASE.d1 = ro; NEW.d1 = ro

class Stop(BaseException): pass
captured = {}
import subprocess
real_run = subprocess.run
def fake_run(cmd, **kw):
    captured['input'] = kw.get('input'); raise Stop()

ok = True
for name in ('楊政翰', '張景勛', '葉筱屏', '林博祥', '江逸泓', '周亦宣', '王美日'):
    a = REAL_D1(f"SELECT id, name, job_slug FROM applications WHERE name={BASE.q(name)} AND interview_state IN ('done','paused') "
                f"ORDER BY interview_started_at DESC LIMIT 1")[0]
    ctx = BASE.context_for(a['id'])
    captured.clear(); subprocess.run = fake_run
    try:
        BASE.finish(a['id'], a['name'], a['job_slug'], ctx, abandoned=False, close=True)
    except Stop:
        pass
    finally:
        subprocess.run = real_run
    mine = NEW.sanitize(NEW.build_report_prompt(a['id'], ctx, a['job_slug'], False, with_not_covered=False))
    same = captured.get('input') == mine
    ok &= same
    print(('  ✅ ' if same else '  ❌ ') + f"{name}：改動前 finish() 送出的 prompt（{len(captured.get('input') or '')} 字）= 改後 with_not_covered=False（{len(mine)} 字）")
print(f'\n（過程中攔下的寫入 {len(writes)} 筆，都沒有真的執行）')
print('全部相同' if ok else '有不同')
sys.exit(0 if ok else 1)
