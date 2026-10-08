"""E17-1 回放：證明「加了等待過渡語之後，餵給阿財 LLM 的提示詞逐字不變」。
對 5 場真實面談的每一個人選回話點（共約數十個）：
  (甲) 改前程式（origin/main）＋沒有過渡語            → 提示詞 X
  (乙) 改後程式＋沒有過渡語                         → 必須 = X     （證明 patch 沒改變原有行為）
  (丙) 改後程式＋在人選那句話之後插入一句過渡語       → 必須 = X     （證明過渡語不會進 LLM 的歷史）
全程唯讀：真實訊息從 D1 讀出來放進記憶體 sqlite，不寫任何資料，也不呼叫 claude。"""
import os, sys, sqlite3, importlib.util, glob, json, hashlib
os.environ.setdefault('INTERVIEW_HOST', 'test')

def load(path, name):
    sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(name, os.path.join(path, 'interview_daemon.py'))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    sys.path.remove(path)
    return m

BASE = load('/tmp/wt_e17_base', 'daemon_base')
NEW = load(os.getcwd(), 'daemon_new')       # 從「已套用三個 patch 的 worktree」目錄執行
REAL_D1 = NEW.d1                       # 唯讀查 D1 用
REPO = glob.glob(os.path.expanduser('~/claude-projects/*/step1ne-recruit'))[0]
for m in (BASE, NEW):
    m.HERE = REPO
    m.log = lambda *_a, **_k: None
import interview_markers as MK        # 新 worktree 在 sys.path 先前載入過

TR = MK.TRANSITION_TEXTS[0]
NAMES = ['楊政翰', '張景勛', '葉筱屏', '林博祥', '江逸泓']
tot = same_new = same_tr = 0
bad = []

for name in NAMES:
    a = REAL_D1(f"SELECT id, name, job_slug, interview_plan_json FROM applications WHERE name={NEW.q(name)} "
                f"AND interview_state IN ('done','paused') ORDER BY interview_started_at DESC LIMIT 1")[0]
    app_id = a['id']
    msgs = REAL_D1(f"SELECT role, content, created_at FROM messages WHERE application_id={NEW.q(app_id)} ORDER BY id")
    NEW.d1 = REAL_D1; BASE.d1 = REAL_D1               # 上一輪把 d1 換成記憶體 sqlite 了，這裡要換回來才能讀真實 D1
    static = NEW._fetch_static(app_id)               # 真實的履歷／職缺／初篩（唯讀）
    jobrow = REAL_D1(f"SELECT slug, interview_language, seniority FROM jobs WHERE slug={NEW.q(a['job_slug'])}")
    cand_idx = [i for i, m in enumerate(msgs) if m['role'] == 'candidate' and m['content'] != MK.ENTRY_MARKER]
    pick = cand_idx[::max(1, len(cand_idx) // 8)][:9]       # 每場取約 8 個回話點
    for i in pick:
        upto = msgs[:i + 1]

        def make_db(with_tr):
            db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, application_id TEXT, role TEXT, content TEXT, created_at TEXT)")
            db.execute("CREATE TABLE applications (id TEXT, interview_plan_json TEXT)")
            db.execute("CREATE TABLE jobs (slug TEXT, interview_language TEXT, seniority TEXT)")
            db.execute("INSERT INTO applications VALUES (?,?)", (app_id, a.get('interview_plan_json')))
            for j in jobrow: db.execute("INSERT INTO jobs VALUES (?,?,?)", (j['slug'], j['interview_language'], j['seniority']))
            for m in upto: db.execute("INSERT INTO messages (application_id,role,content,created_at) VALUES (?,?,?,?)", (app_id, m['role'], m['content'], m['created_at']))
            if with_tr:
                db.execute("INSERT INTO messages (application_id,role,content,created_at) VALUES (?,?,?,?)", (app_id, 'assistant', TR, upto[-1]['created_at']))
            db.commit(); return db

        def prompt_with(mod, db):
            def d1(sql, *_a, **_k):
                cur = db.execute(sql); return [dict(r) for r in cur.fetchall()] if cur.description else []
            mod.d1 = d1
            mod._STATIC_CACHE.clear(); mod._STATIC_CACHE[app_id] = static
            ctx = mod.context_for(app_id)
            return mod.build_prompt(ctx, mod.talk_skill(ctx))

        x = prompt_with(BASE, make_db(False))
        y = prompt_with(NEW, make_db(False))
        z = prompt_with(NEW, make_db(True))
        tot += 1
        same_new += (x == y); same_tr += (x == z)
        if x != y or x != z:
            bad.append((name, i, len(x), len(y), len(z)))
    print(f"{name}: 取 {len(pick)} 個回話點，提示詞約 {len(x)} 字")

print(f"\n共 {tot} 個回話點")
print(f"  改後（無過渡語）= 改前：{same_new}/{tot}")
print(f"  改後（有過渡語）= 改前：{same_tr}/{tot}")
if bad: print('  不一致：', bad[:5])
print('全部相同' if same_new == tot and same_tr == tot else '有不同')
sys.exit(0 if same_new == tot and same_tr == tot else 1)
