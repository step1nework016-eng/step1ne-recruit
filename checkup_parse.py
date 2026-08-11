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
    """檔案內容：舊資料放 content_b64，新資料切塊存 file_chunks。"""
    fid = str(file_id).replace("'", "''")
    rows = d1(f"SELECT content_b64, chunks FROM files WHERE id='{fid}'")
    if not rows:
        return None
    if rows[0].get('content_b64'):
        return rows[0]['content_b64']
    if rows[0].get('chunks'):
        # 依 idx 排序組回來，順序錯了整份檔案就壞掉
        parts = d1(f"SELECT b64 FROM file_chunks WHERE file_id='{fid}' ORDER BY idx ASC")
        return ''.join(p['b64'] for p in parts)
    return None


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
            d1(f"UPDATE checkups SET parsed_at='{now}', resume_readable=0, "
               f"resume_note='沒有上傳履歷檔（只有連結或作品集）', status='parsed', "
               f"updated_at='{now}' WHERE id='{cid}'")
            print(f"  ⚠️ {c['name']}　沒有履歷檔")
            continue

        f = fs[0]
        try:
            b64 = file_b64(f['file_id'])
            if not b64:
                raise RuntimeError('這份檔案沒有內容')
            text, note = extract(base64.b64decode(b64), f.get('filename'), f.get('mime'))
        except Exception as e:
            text, note = None, f'解析例外：{str(e)[:120]}'

        if text:
            # 控制字元會讓後面把文字當命令列參數傳給 claude 時整個炸掉
            text = ''.join(ch for ch in text if ch in '\n\t' or ord(ch) >= 32)

        d1(f"UPDATE checkups SET resume_text={q(text)}, resume_note={q(note)}, "
           f"resume_readable={1 if text else 0}, parsed_at='{now}', status='parsed', "
           f"updated_at='{now}' WHERE id='{cid}'")
        print(f"  {'✅' if text else '⚠️'} {c['name']}　"
              + (f'{len(text)} 字' if text else str(note)))


if __name__ == '__main__':
    main()
