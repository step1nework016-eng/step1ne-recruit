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


def find_prior_ai_report(app_id):
    """找這個人選過去是不是已經有一份『阿財真的面談產生』的報告——用來判斷
    這次電訪要不要跟舊報告合併。判斷方式：報告內文開頭沒有「資料來源：顧問電訪」
    這個 marker（那個 marker 是 consultant_call_report.py 自己每次都會寫的，
    真正阿財面談產生的報告不會有），取最早的一份當「阿財原始報告」——不用
    interview_mode 判斷是因為那個欄位會在第一次電訪處理後被改成
    'consultant_call'，蓋掉「這人本來有沒有跟阿財談過」這個歷史事實。"""
    rows = D.d1(f"SELECT id, content_md, created_at FROM reports WHERE application_id={D.q(app_id)} "
                f"ORDER BY created_at ASC")
    for row in rows:
        content = row.get('content_md') or ''
        if '資料來源：顧問電訪' not in content and '資料來源:顧問電訪' not in content:
            return row
    return None


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

    # ⚠️ 2026-09-01 加：這個人選如果之前已經有一份阿財真的面談產生的報告，
    # 這次電訪不能當作「從零開始」重寫一份——要把阿財那份也讀進去，產出
    # 一份整合兩次接觸的新報告。阿財原本那份報告在資料庫裡完全不動
    # （報告本來就是每次新增一列，不會覆蓋），顧問兩份都看得到。
    prior = find_prior_ai_report(app_id)
    prior_block = (
        '\n\n【這個人選先前跟阿財面談產生的原始報告——這次電訪內容要跟這份對照、'
        '互相補充，不是重新寫一份，兩邊都提到的地方以更晚、更明確的說法為準】\n'
        + prior['content_md']
        if prior else '')

    merge_instruction = (
        '\n\n⚠️ 這個人選先前已經有阿財面談的報告（上面附上了）。這次是同一個人選'
        '第二次接觸，顧問又親自電訪一次。請把兩次接觸的資訊合併成一份新報告：\n'
        '  - 阿財面談問到的、電訪沒再問的，繼續保留在新報告裡，不要因為這次沒問到就刪掉。\n'
        '  - 電訪這次新問到、阿財面談沒問到的，補進去。\n'
        '  - 兩邊都問到但答案不一樣的（例如期望待遇改了），以電訪這次（比較新）為準，'
        '並註明「（電訪更新：原本 XXX，現在 XXX）」，不要默默改掉沒講。\n'
        '  - 報告開頭除了「資料來源：顧問電訪」，再加一行「本報告整合阿財面談'
        '（' + str(prior['created_at'] if prior else '') + '）＋顧問電訪（' + by + '）兩次接觸紀錄」。\n'
        if prior else '')

    prompt = (
        '以下是一場**由獵頭顧問親自電話訪談**的紀錄。請依規範的 Phase 7 產出初篩報告。\n\n'
        + D.skill('report')
        + '\n\n【職缺硬條件】\n' + json.dumps(ctx.get('job') or {}, ensure_ascii=False, indent=1)
        + '\n\n【應徵表單】\n' + json.dumps(ctx.get('application') or {}, ensure_ascii=False, indent=1)
        + resume_block
        + prior_block
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
        + merge_instruction
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
