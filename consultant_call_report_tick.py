#!/usr/bin/env python3
"""顧問新增人選時附的「面談內容」→ 自動產初篩報告，定期輪詢版。

consultant_call_report.py 那支是「顧問自己在終端機跑一次」的手動工具；
這支是它的排程版——consultant/pipeline「新增人選」表單存的 consultant_call_notes
只是先躺在 D1 裡（applications.call_report_pending=1），要有東西把它真的
變成報告，不然就是「填了等於沒填」。

跟 consultant_call_report.py 共用同一顆 build()，報告格式、履歷交叉核對、
寫入 D1 的路徑完全一樣，不會變成第二種格式。

用法：
    python3 consultant_call_report_tick.py           # 常駐，每 5 分鐘掃一次
    python3 consultant_call_report_tick.py --once     # 跑一輪就結束（測試用）
"""
import argparse
import base64
import datetime
import importlib.util
import os
import re
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('ccr', os.path.join(HERE, 'consultant_call_report.py'))
CCR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CCR)
D = CCR.D  # interview_daemon 模組（d1/q/tg/report_to_json/save_report 都在這裡）

# 2026-09-10 加：多裝置協作用——搶到才算你的，搶不到跳過，避免兩台裝置同時
# 處理同一筆 call_report_pending，重複產報告、重複發 TG 通知。
WORKER_ID = os.environ.get('STEP1NE_WORKER_NAME') or socket.gethostname()

POLL_SECONDS = 300


def fetch_uploaded_file(file_id):
    """把 files/file_chunks 存的檔案組回原始 bytes。跟 /admin/file 那支
    Worker 端點同一套切塊機制，只是這裡用 Python 組。"""
    rows = D.d1(f"SELECT filename, mime, content_b64, chunks FROM files WHERE id={D.q(file_id)}")
    if not rows:
        return None, None, None
    f = rows[0]
    b64 = f.get('content_b64') or ''
    if not b64 and f.get('chunks'):
        parts = D.d1(f"SELECT b64 FROM file_chunks WHERE file_id={D.q(file_id)} ORDER BY idx ASC")
        b64 = ''.join(p['b64'] for p in parts)
    if not b64:
        return None, None, None
    return base64.b64decode(b64), f.get('filename') or '', f.get('mime') or ''


def extract_notes_text(raw, filename, mime):
    """面談內容檔案（doc/docx/txt/md/html/pdf/excel）轉純文字。
    跟 fetch_application.py 的 extract_text() 同一套邏輯延伸——履歷那支只認
    pdf/doc/docx，這裡的檔案來源更雜（顧問打完字直接存檔、Excel整理表都有）
    所以多補 txt/md/html/xlsx。抽不出來要明講，不能回空字串裝作沒事。"""
    ext = (os.path.splitext(filename or '')[1] or '').lower()
    if ext in ('.txt', '.md'):
        return raw.decode('utf-8', errors='replace')
    if ext in ('.html', '.htm'):
        text = raw.decode('utf-8', errors='replace')
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', text, flags=re.S | re.I)
        text = re.sub(r'<[^>]+>', '\n', text)
        return re.sub(r'\n{3,}', '\n\n', text).strip()
    if ext in ('.xlsx', '.xls'):
        try:
            import openpyxl
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                f.write(raw); path = f.name
            try:
                wb = openpyxl.load_workbook(path, data_only=True)
                lines = []
                for ws in wb.worksheets:
                    lines.append(f'【工作表：{ws.title}】')
                    for row in ws.iter_rows(values_only=True):
                        cells = [str(c) for c in row if c is not None]
                        if cells:
                            lines.append('\t'.join(cells))
                return '\n'.join(lines)
            finally:
                os.unlink(path)
        except Exception as e:
            return f'【無法抽取文字】Excel 檔案讀取失敗：{e}'
    with tempfile.NamedTemporaryFile(suffix=ext or '.bin', delete=False) as f:
        f.write(raw); path = f.name
    try:
        if ext == '.pdf' or 'pdf' in (mime or ''):
            r = subprocess.run(['pdftotext', '-layout', path, '-'],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
            try:
                import pdfplumber
                with pdfplumber.open(path) as pdf:
                    t = '\n'.join((p.extract_text() or '') for p in pdf.pages)
                if t.strip():
                    return t
            except Exception:
                pass
            return '【無法抽取文字】這份 PDF 可能是掃描影像，請改貼文字或用其他格式重傳。'
        if ext in ('.docx', '.doc'):
            r = subprocess.run(['textutil', '-convert', 'txt', '-stdout', path],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        return f'【無法抽取文字】不支援的檔案格式：{ext or mime or "未知"}'
    finally:
        os.unlink(path)


def process_one(row):
    app_id = row['id']
    name = row.get('name') or app_id
    notes = (row.get('consultant_call_notes') or '').strip()
    file_id = row.get('call_notes_file_id')
    if file_id:
        raw, filename, mime = fetch_uploaded_file(file_id)
        if raw is None:
            print(f'[警告] {name}（{app_id}）面談內容檔案讀取失敗（file_id={file_id}）', file=sys.stderr)
        else:
            file_text = extract_notes_text(raw, filename, mime).strip()
            # 打字內容跟上傳檔案都有的話兩個都給，AI 自己合併判斷；
            # 檔案抽取失敗的錯誤訊息也一起附上，讓顧問看報告時知道要補文字。
            notes = (notes + '\n\n【顧問上傳的面談紀錄檔案內容】\n' + file_text).strip() if notes else file_text
    if len(notes) < 30:
        # 太短的筆記硬跑只會整份「未詢問」，沒有意義——清掉 flag 但不產報告，
        # 顧問自己會發現這個人一直沒有報告，去補筆記或直接手動跑
        # consultant_call_report.py 帶更長的內容。
        D.d1(f"UPDATE applications SET call_report_pending=0 WHERE id={D.q(app_id)}")
        D.tg(f'⚠️ {name} 的顧問面談內容太短（少於 30 字），沒有自動產報告。'
             f'如果要補，請用 consultant_call_report.py 手動跑一次帶更完整的內容。')
        return

    try:
        app, ctx, report = CCR.build(app_id, notes, by='顧問')
    except Exception as e:
        print(f'[錯誤] {name}（{app_id}）產報告失敗：{e}', file=sys.stderr)
        # 失敗不清 flag，下一輪還會重試；同一筆一直失敗的話 tg 訊息會一直提醒，
        # 這是刻意的——比默默卡住好。⚠️ 2026-09-10 加：tick() 現在會先把
        # call_report_pending 搶成 2（多裝置保護鎖），失敗要退回 1 才會重試，
        # 不然會卡在 2 永遠不會再被撈到。
        D.d1(f"UPDATE applications SET call_report_pending=1 WHERE id={D.q(app_id)}")
        D.tg(f'❌ {name} 的顧問電訪報告自動產出失敗，稍後會重試。錯誤：{str(e)[:200]}')
        return

    report_json = D.report_to_json(report, ctx, app.get('name') or '', app_id=app_id)
    rid = D.save_report(app_id, report, report_json)
    if not rid:
        print(f'[錯誤] {name}（{app_id}）報告產好但寫入 D1 失敗', file=sys.stderr)
        D.d1(f"UPDATE applications SET call_report_pending=1 WHERE id={D.q(app_id)}")
        D.tg(f'❌ {name} 的顧問電訪報告產好了但存檔失敗，需要人工處理。')
        return

    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    D.d1(f"UPDATE applications SET interview_state='done', interview_ended_at={D.q(now)}, "
         f"status='interviewed', interview_mode='consultant_call', call_report_pending=0 "
         f"WHERE id={D.q(app_id)}")
    print(f'✅ {name}（{app_id}）報告已產出，report id = {rid}')
    D.tg(f'📄 {name} 的初篩報告已產出（來源：顧問新增人選時附的面談內容）。'
         f'到「初篩報告」頁看內容並決定要不要推給客戶。')


def tick():
    rows = D.d1("SELECT id, name, consultant_call_notes, call_notes_file_id FROM applications "
                "WHERE call_report_pending=1 LIMIT 5")
    for row in rows:
        claim = D.d1_raw(f"UPDATE applications SET call_report_pending=2, worker_id={D.q(WORKER_ID)} "
                          f"WHERE id={D.q(row['id'])} AND call_report_pending=1")
        if not claim.get('meta', {}).get('changes'):
            continue  # 已經被別台裝置搶走
        process_one(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    a = ap.parse_args()
    if a.once:
        tick()
        return
    while True:
        try:
            tick()
        except Exception as e:
            print(f'[tick 例外] {e}', file=sys.stderr)
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
