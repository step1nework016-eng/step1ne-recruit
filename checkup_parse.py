#!/usr/bin/env python3
"""把履歷健檢（阿福）收到的履歷檔抽成純文字，寫回 checkups.resume_text。

為什麼另外寫一支，而不是改 parse_resumes.py：
  parse_resumes.py 是阿財那條線在用的，它把文字寫回 files 表、失敗時去
  applications 找人名。健檢的人**不在 applications 裡**，去改那支等於把
  兩條線的資料綁在一起——那正是這個產品被要求要避免的事。

  所以這裡只**沿用**它的抽取邏輯（extract / d1 / env_with_cf），
  一行都不改它，抽完寫進 checkups 自己的欄位。

用法：
    python3 checkup_parse.py                # 處理所有還沒解析的健檢單
    python3 checkup_parse.py <checkup_id>   # 只處理某一筆
    python3 checkup_parse.py --force ...    # 已解析過的也重抽

⚠️ 這支不會寄信、不會推 Telegram、不會重啟任何常駐程式。
"""
import base64, importlib.util, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# parse_resumes.py 匯入即用，不執行它的 main()
_spec = importlib.util.spec_from_file_location('pr', os.path.join(HERE, 'parse_resumes.py'))
PR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PR)

d1 = PR.d1
extract = PR.extract


def q(v):
    return 'NULL' if v is None else "'" + str(v).replace("'", "''") + "'"


def file_b64(file_id):
    """檔案內容：舊資料放 content_b64，再來切塊存 file_chunks，
    2026-09-03 之後的新檔案放 R2（files 表只剩 metadata，storage='r2'）。"""
    fid = str(file_id).replace("'", "''")
    rows = d1(f"SELECT content_b64, chunks, storage FROM files WHERE id='{fid}'")
    if not rows:
        return None
    if rows[0].get('content_b64'):
        return rows[0]['content_b64']
    if rows[0].get('chunks'):
        # 依 idx 排序組回來，順序錯了整份檔案就壞掉
        parts = d1(f"SELECT b64 FROM file_chunks WHERE file_id='{fid}' ORDER BY idx ASC")
        return ''.join(p['b64'] for p in parts)
    # 2026-10-20 補（履歷被誤判讀不到）：這支只會讀 content_b64／chunks，
    # 9/3 搬到 R2 之後的檔案兩個都是空的，一律被當成「這份檔案沒有內容」、
    # resume_readable=0——9/17、9/19、10/20 三筆 R2 履歷全中。
    # 改用 parse_resumes.py 既有的 R2 讀法（打後台 /admin/file/:id），不另接 R2。
    if rows[0].get('storage') == 'r2':
        return PR._fetch_r2_b64(str(file_id))
    return None


def _ready_text(file_id):
    """parse_resumes.py 已經抽好、寫在 files.text_content 的文字（含它的 OCR 結果）。
    有就直接用，跟阿財讀同一份，不用再抽一次。"""
    fid = str(file_id).replace("'", "''")
    rows = d1(f"SELECT text_content, storage, created_at FROM files WHERE id='{fid}'")
    return rows[0] if rows else {}


def link_text(url):
    """履歷連結 → (文字, 說明)。分流照 parse_resumes.parse_urls()。"""
    import urllib.parse
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        if host in PR.FILE_HOSTS:
            raw, fname, err = PR.fetch_from_url(url)
            if not raw:
                return None, err
            return extract(raw, fname, None)
        return PR.fetch_webpage(url)
    except Exception as e:
        return None, f'抓履歷連結例外：{str(e)[:120]}'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force = '--force' in sys.argv
    where = 'WHERE 1=1' if force else "WHERE c.parsed_at IS NULL"
    if args:
        where += f" AND c.id='{args[0].replace(chr(39), chr(39) * 2)}'"

    rows = d1(f"SELECT c.id, c.name FROM checkups c {where} ORDER BY c.created_at ASC LIMIT 20")
    if not rows:
        print('  沒有待解析的健檢單')
        return

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for c in rows:
        cid = str(c['id']).replace("'", "''")
        # 主履歷：kind='resume' 的第一個檔。作品集（portfolio）不進 resume_text——
        # 那是給顧問看的參考資料，混進履歷正文會讓阿福分不清哪句話是履歷寫的。
        fs = d1("SELECT cf.file_id, f.filename, f.mime FROM checkup_files cf "
                "LEFT JOIN files f ON f.id=cf.file_id "
                f"WHERE cf.checkup_id='{cid}' AND cf.kind='resume' ORDER BY cf.created_at ASC LIMIT 1")
        if not fs:
            # 2026-10-23 新版表單（測試版）：沒有檔案、但貼了「履歷連結」→ 用 parse_resumes.py 既有的
            # 抓法（雲端硬碟當檔案下載、其他網站當網頁抓），跟阿財 parse_urls() 同一套分流，不另寫。
            # 舊表單不會有 label='履歷連結' 的連結，這段不會走到。
            lk = d1("SELECT url FROM checkup_links WHERE checkup_id='" + cid + "' AND label='履歷連結' "
                    "ORDER BY idx ASC LIMIT 1")
            if lk:
                text, note = link_text(lk[0]['url'])
                if text:
                    text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)
                d1(f"UPDATE checkups SET resume_text={q(text)}, resume_note={q(note)}, "
                   f"resume_readable={1 if text else 0}, parsed_at='{now}', "
                   f"status=CASE WHEN status='new' THEN 'parsed' ELSE status END, "
                   f"updated_at='{now}' WHERE id='{cid}'")
                d1(f"UPDATE checkup_links SET fetched_at='{now}', fetch_note={q(note or 'ok')} "
                   f"WHERE checkup_id='{cid}' AND label='履歷連結'")
                print(f"  {'✅' if text else '⚠️'} {c['name']}　履歷連結　"
                      + (f'{len(text)} 字' if text else str(note)))
                continue
            d1(f"UPDATE checkups SET parsed_at='{now}', resume_readable=0, "
               f"resume_note='沒有上傳履歷檔（只有連結或作品集）', "
               f"status=CASE WHEN status='new' THEN 'parsed' ELSE status END, "
               f"updated_at='{now}' WHERE id='{cid}'")
            print(f"  ⚠️ {c['name']}　沒有履歷檔")
            continue

        f = fs[0]
        meta = _ready_text(f['file_id'])
        pre = (meta.get('text_content') or '').strip()
        if len(pre) >= 200:
            # 2026-10-20：files.text_content 已經有夠長的文字就直接用（門檻 200 字
            # 跟 parse_resumes.py 判斷「太短要重抽」同一個數字）
            text, note = pre, None
        else:
            try:
                b64 = file_b64(f['file_id'])
                if not b64:
                    raise RuntimeError('這份檔案沒有內容')
                text, note = extract(base64.b64decode(b64), f.get('filename'), f.get('mime'))
            except Exception as e:
                text, note = None, f'解析例外：{str(e)[:120]}'

        # 2026-10-20：R2 檔讀不到（後台暫時連不上／權杖沒讀到）時不要當場判死刑——
        # 先不寫 parsed_at，下一輪（daemon 每 8 秒）再試；parse_resumes.py 補好
        # files.text_content 後上面那段也會直接接手。超過 30 分鐘還是讀不到才照實記「讀不到」。
        if not text and meta.get('storage') == 'r2':
            age_ok = d1("SELECT 1 AS y FROM files WHERE id='" + str(f['file_id']).replace("'", "''") +
                        "' AND created_at >= datetime('now','+8 hours','-30 minutes')")
            if age_ok:
                print(f"  ⏳ {c['name']}　R2 履歷這輪還讀不到（{note}），下一輪再試")
                continue

        if text:
            # 控制字元會讓後面把文字當命令列參數傳給 claude 時整個炸掉
            text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)

        # 2026-10-20：status 只在還是 'new' 時才改成 'parsed'。交卷那一刻 Worker
        # 可能已經先把它分流成 ready／awaiting_booking／scheduled，這裡無條件蓋回
        # 'parsed' 會把分流結果洗掉（--force 重抽舊單時也一樣）。
        d1(f"UPDATE checkups SET resume_text={q(text)}, resume_note={q(note)}, "
           f"resume_readable={1 if text else 0}, parsed_at='{now}', "
           f"status=CASE WHEN status='new' THEN 'parsed' ELSE status END, "
           f"updated_at='{now}' WHERE id='{cid}'")
        print(f"  {'✅' if text else '⚠️'} {c['name']}　"
              + (f'{len(text)} 字' if text else str(note)))


if __name__ == '__main__':
    main()
