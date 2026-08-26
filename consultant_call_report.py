#!/usr/bin/env python3
"""顧問電訪 → 初篩報告

用途：有些人選是顧問自己打電話談的，沒有走阿財那條線。以前這種人在
系統裡就只是一筆「未開始面談」的應徵紀錄，沒有報告可以看、也不能推
給客戶——顧問明明談過了，資料卻卡在他自己的筆記本裡。

這支做的事跟 interview_daemon.finish() 完全一樣，只有一個差別：
**「逐字稿」換成顧問的電訪筆記**。報告格式、職缺硬條件比對、履歷交叉
核對、結構化 JSON、寫進 D1 的路徑全部沿用同一套，所以產出來的報告
在後台跟阿財談的那些長得一模一樣，不會變成另一種格式的東西。

用法：
    python3 consultant_call_report.py --app <application_id> --notes 電訪筆記.txt
    python3 consultant_call_report.py --app <application_id> --notes - < 筆記.txt
    # 先看產出來長怎樣、先不要寫進資料庫：
    python3 consultant_call_report.py --app <id> --notes n.txt --dry

筆記怎麼寫都可以——條列、流水帳、錄音轉的逐字都行。寫得越細報告越
有東西；沒問到的項目報告會標「未詢問」，不會自己編。
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('idaemon', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


def build(app_id, notes, by):
    rows = D.d1(f"SELECT id, name, job_slug, interview_state FROM applications WHERE id={D.q(app_id)}")
    if not rows:
        sys.exit(f'找不到這筆應徵紀錄：{app_id}')
    app = rows[0]
    ctx = D.context_for(app_id)

    resume_text = (ctx.get('resume_text') or '').strip()
    resume_block = (
        ('\n\n【履歷全文（報告要拿它跟電訪內容交叉比對）】\n' + resume_text[:20000])
        if resume_text else
        '\n\n【履歷】這位候選人沒有可讀的履歷檔案，報告裡的經歷一律標「來源：口述」。')

    prompt = (
        '以下是一場**由獵頭顧問親自電話訪談**的紀錄。請依規範的 Phase 7 產出初篩報告。\n\n'
        + D.skill('report')
        + '\n\n【職缺硬條件】\n' + json.dumps(ctx.get('job') or {}, ensure_ascii=False, indent=1)
        + '\n\n【應徵表單】\n' + json.dumps(ctx.get('application') or {}, ensure_ascii=False, indent=1)
        + resume_block
        + '\n\n【顧問電訪紀錄】\n' + notes
        # 這一段是這支跟阿財那條線唯一真正不同的地方，一定要講清楚，
        # 不然模型會照「AI 面談」的假設去寫，報告裡出現根本沒發生過的問答。
        + '\n\n⚠️ 這場不是 AI 面談，是顧問本人打電話談的，紀錄是顧問事後整理的重點，'
          '不是逐字稿。因此：\n'
          '1. 報告開頭要註明「資料來源：顧問電訪（'+by+'）」。\n'
          '2. 電訪紀錄裡沒有提到的項目一律標「未詢問」，**絕對不要推測、不要補寫**。'
          '顧問看報告是要知道「還有哪些沒問到」，自己補上去的內容會讓他以為問過了。\n'
          '3. 不要引用不存在的問答對話，也不要評論候選人的回答速度或投入程度'
          '（那些訊號只有 AI 面談才有）。\n'
        + '\n\n只輸出報告本文（Markdown），不要有其他說明。')

    r = subprocess.run(['claude', '-p', D.sanitize(prompt), '--model', D.REPORT_MODEL,
                        *D.NO_TOOLS, '--output-format', 'text'],
                       capture_output=True, text=True, env=D.env_with_cf(), timeout=D.REPORT_TIMEOUT)
    report = (r.stdout or '').strip()
    if not report:
        sys.exit('報告產生失敗（claude 沒有輸出）。原始錯誤：' + (r.stderr or '')[:400])
    if report.startswith('```'):
        report = report.split('\n', 1)[-1]
        if report.rstrip().endswith('```'):
            report = report.rstrip()[:-3].rstrip()
    return app, ctx, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', required=True, help='application_id')
    ap.add_argument('--notes', required=True, help='電訪筆記檔案路徑，用 - 代表從 stdin 讀')
    ap.add_argument('--by', default='Phoebe', help='誰做的電訪，會寫進報告開頭')
    ap.add_argument('--dry', action='store_true', help='只印出報告，不寫進資料庫')
    a = ap.parse_args()

    notes = sys.stdin.read() if a.notes == '-' else open(a.notes, encoding='utf-8').read()
    notes = notes.strip()
    if len(notes) < 30:
        sys.exit('電訪筆記太短（少於 30 字），這樣產出來的報告會整份都是「未詢問」，沒有意義。')

    app, ctx, report = build(a.app, notes, a.by)
    print(report)
    if a.dry:
        print('\n--- --dry：沒有寫進資料庫 ---', file=sys.stderr)
        return

    report_json = D.report_to_json(report, ctx, app.get('name') or '', app_id=a.app)
    rid = D.save_report(a.app, report, report_json)
    if not rid:
        sys.exit('報告產好了但寫進資料庫失敗，上面的內容請手動保存。')

    # 電訪也是一場面談。狀態要跟著走，不然這個人會一直卡在「未開始面談」，
    # 顧問後台的「等我看報告」看不到他，客戶也推不出去。
    now = D.datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    D.d1(f"UPDATE applications SET interview_state='done', interview_ended_at={D.q(now)}, "
         f"status='interviewed', interview_mode='consultant_call' WHERE id={D.q(a.app)}")
    print(f'\n✅ 已存入：report id = {rid}，{app.get("name")} 狀態改為「已面談」。', file=sys.stderr)
    D.tg(f'📄 {app.get("name")} 的初篩報告已產出（來源：顧問電訪／{a.by}）。'
         f'到「初篩報告」頁看內容並決定要不要推給客戶。')


if __name__ == '__main__':
    main()
