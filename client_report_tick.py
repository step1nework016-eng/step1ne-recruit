#!/usr/bin/env python3
"""顧問後台「製作客戶版履歷」按鈕 → 新版報告 PDF 的排程處理。

按鈕按下去那一刻（backoffice-worker 的 /admin/application/:id/client-formal-report）
只做兩件事：把電洽筆記整理成客戶安全版存進 applications.call_summary_client_md，
然後在 client_report_requests 插一筆 pending。Worker 沒辦法跑 headless Chrome
（deliver.py 產 PDF 靠本機 Chrome），真正產生 PDF、推 TG、回填候選人卡片下載
連結，要靠這支常駐排程完成——跟 consultant_call_report_tick.py 是同一種分工
（Worker 收請求存旗標，本機daemon撈旗標做重活）。

只產「客戶版」一份PDF，不是 deliver.build_pdfs() 的顧問版＋客戶版兩份——
顧問版在阿財面談結束當下已經自動產過一次了，這顆按鈕原本設計就是給客戶看的
「客戶版履歷」，不要多推一份顧問不需要的東西。

用法：
    python3 client_report_tick.py           # 常駐，每 60 秒掃一次
    python3 client_report_tick.py --once     # 跑一輪就結束（測試用）
"""
import argparse
import base64
import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location('idaemon', os.path.join(HERE, 'interview_daemon.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)

sys.path.insert(0, HERE)
import deliver  # noqa: E402  （純函式，不 import 任何專案內模組，safe to import 這裡）

POLL_SECONDS = 60
WORKER_BASE = 'https://step1ne-backoffice-worker.aiagentg888.workers.dev'


def upload_pdf(app_id, company_id, pdf_bytes, filename):
    """把產好的 PDF 傳回 Worker 存進 R2＋files 表，回填候選人卡片。"""
    tok = D._admin_token()
    if not tok:
        return None
    req = urllib.request.Request(
        f'{WORKER_BASE}/admin/client-formal-reports/attach-pdf',
        data=json.dumps({
            'application_id': app_id, 'company_id': company_id,
            'pdf_b64': base64.b64encode(pdf_bytes).decode(), 'filename': filename,
        }).encode(),
        headers={'content-type': 'application/json', 'authorization': f'Bearer {tok}',
                 'user-agent': 'step1ne-client-report-tick/1.0'})
    r = json.loads(urllib.request.urlopen(req, timeout=60).read())
    return r.get('file_id') if r.get('ok') else None


def process_one(req_row):
    app_id = req_row['application_id']
    company_id = req_row.get('company_id')
    req_id = req_row['id']

    app = D.d1(f"SELECT name, job_slug FROM applications WHERE id={D.q(app_id)}")
    if not app:
        D.d1(f"UPDATE client_report_requests SET status='error', error='找不到人選', "
             f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
        return
    name, job_slug = app[0]['name'], app[0]['job_slug']

    # 2026-09-04 加：Worker 端沒有 content_json（沒真的做過阿財面談）時，
    # 會現場用 AI 湊一份同形狀的資料存進這個請求列的 synthetic_content_json——
    # 這裡優先用它，沒有才退回真正的阿財報告。兩種來源互斥，不會同時發生。
    synthetic_raw = req_row.get('synthetic_content_json')
    if synthetic_raw:
        try:
            data = json.loads(synthetic_raw)
        except Exception as e:
            D.d1(f"UPDATE client_report_requests SET status='error', error={D.q(f'現場湊的資料解析失敗：{e}')}, "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
            return
    else:
        report = D.d1(f"SELECT content_json FROM reports WHERE application_id={D.q(app_id)} "
                       f"ORDER BY created_at DESC LIMIT 1")
        if not report or not report[0].get('content_json'):
            D.d1(f"UPDATE client_report_requests SET status='error', "
                 f"error='這位人選沒有阿財面談的結構化報告，也沒有現場湊的資料', "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
            D.tg(f'⚠️ {name} 沒有阿財面談的結構化報告，也沒有現場湊的資料，沒辦法做客戶版履歷。', thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)
            return
        try:
            data = json.loads(report[0]['content_json'])
            if isinstance(data, str):
                data = json.loads(data)
        except Exception as e:
            D.d1(f"UPDATE client_report_requests SET status='error', error={D.q(f'報告JSON解析失敗：{e}')}, "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
            return

    # ⚠️ 2026-09-09 加：周海暉那份「一句話總結」空白事故——AI 有時會把
    # trait_one_liner 寫在最外層，沒照 schema 放進 for_client 底下，
    # deliver.py 只認 for_client.trait_one_liner，抓不到就整格空白。
    # 這裡防禦性挪回正確位置，不管 AI 這次有沒有照規矩。
    if isinstance(data, dict) and data.get('trait_one_liner') and not (data.get('for_client') or {}).get('trait_one_liner'):
        data.setdefault('for_client', {})['trait_one_liner'] = data.pop('trait_one_liner')

    # company_id 沒帶就退回這個人選最新一筆推薦紀錄，跟 Worker 端 prefill/report
    # 兩支端點同一套退回邏輯，避免顧問沒特別選公司時整批失敗。
    if not company_id:
        fwd = D.d1(f"SELECT company_id FROM candidate_forwards WHERE application_id={D.q(app_id)} "
                    f"ORDER BY forwarded_at DESC LIMIT 1")
        company_id = fwd[0]['company_id'] if fwd else None

    meta = D._delivery_meta(app_id, name, job_slug, abandoned=False)
    # ⚠️ 2026-09-09 加：meta 裡的 hard_filters／must_check_items 是職缺層級
    # 的靜態清單（哪些項目要問），沒有這位人選實際的電洽結果。真的問到答案
    # 時（不管是阿財結構化面談的 content_json，還是這裡湊資料時手動填的
    # synthetic 版本），要用那份蓋掉靜態清單——不然客戶看到的永遠是「還沒
    # 問到」，就算顧問明明已經問過。
    # 2026-09-04 加：這個人選從沒真的開始過AI阿財面談（純顧問電洽也是允許的
    # 正常流程），但報告內容格式跟真的面談產出的長得一樣——裡面的「原話引用」
    # 是套用電洽逐字稿硬塞進面談模板的欄位，內容跟模板來源本來就對不上，
    # 蘇微閔那份撞過這個坑（軟體名稱聽錯、薪資數字誤植都沒人先發現）。
    # deliver.py 那邊已經把「AI結構化初步面談」的說法改成「顧問電話初篩」，
    # 這裡另外主動提醒顧問人工核對一次內容，不要照系統產出的直接送出去。
    if not meta.get('has_real_interview'):
        D.tg(f'⚠️ {name} 沒有真的做過AI阿財面談，客戶版履歷是套用顧問電洽紀錄產生的——'
             f'裡面的「原話引用」「專業問答」等內容建議送出前先人工核對一次，'
             f'不要照系統產出的直接用。', thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)
    if company_id:
        # 一個人選可能同時推薦給好幾家客戶，各自可能是不同職缺——_delivery_meta()
        # 只認 applications.job_slug 那一個主職缺，這裡要覆蓋成「這次要做哪家
        # 客戶」實際對應的職缺與公司關係。⚠️ client_named/client_relation 一律
        # 從 client_companies.relation 換算（同一個客戶底下不同職缺各自存一份
        # 複本，曾經漏同步導致孫悅那份報告誤匿名——見 interview_daemon.py 的
        # _client_named_from_relation 註解），不要相信 jobs 表上的複本欄位。
        job = D.d1(f"SELECT j.title, j.ai_disclosure, cc.relation AS client_company_relation "
                    f"FROM candidate_forwards cf LEFT JOIN jobs j ON j.slug=cf.job_slug "
                    f"LEFT JOIN client_companies cc ON cc.id=cf.company_id "
                    f"WHERE cf.application_id={D.q(app_id)} AND cf.company_id={D.q(company_id)} LIMIT 1")
        if job:
            client_named, client_relation = D._client_named_from_relation(job[0]['client_company_relation'])
            meta.update({'job_title': job[0]['title'] or meta.get('job_title'),
                         'client_named': client_named, 'ai_disclosure': job[0]['ai_disclosure'],
                         'client_relation': client_relation})

    # 2026-09-08 加：顧問在「製作客戶版履歷」彈窗勾了哪些區塊。
    # 沒帶就是 None＝全部照預設（硬條件仍然預設關閉）。
    show = None
    if req_row.get('show_json'):
        try:
            show = json.loads(req_row['show_json'])
        except Exception:
            show = None      # 壞掉就當沒勾，不要因此整份做不出來

    # data 裡如果有這位人選實際問到的結果（真的面談的 content_json，或
    # synthetic 版本手動補的），用它蓋掉 meta 裡職缺層級的靜態清單。
    if data.get('hard_filters'):
        meta['hard_filters'] = data['hard_filters']
    if data.get('must_check_items'):
        meta['must_check_items'] = data['must_check_items']

    try:
        html = deliver.build_client_html(data, meta, show=show)
    except Exception as e:
        D.d1(f"UPDATE client_report_requests SET status='error', error={D.q(f'組HTML失敗：{e}')}, "
             f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
        D.tg(f'❌ {name} 的客戶版履歷組版失敗：{str(e)[:200]}', thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)
        return

    display_name = deliver.client_display_name(meta)
    fn = deliver.pdf_filename(display_name, 'client')
    with tempfile.TemporaryDirectory(prefix='client_report_') as tmp:
        path = os.path.join(tmp, fn)
        if not deliver.html_to_pdf(html, path):
            D.d1(f"UPDATE client_report_requests SET status='error', error='PDF產生失敗', "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
            D.tg(f'❌ {name} 的客戶版履歷PDF產生失敗，請至後台查看。', thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)
            return
        with open(path, 'rb') as fh:
            pdf_bytes = fh.read()

    file_id = upload_pdf(app_id, company_id, pdf_bytes, fn) if company_id else None
    D.tg_doc(pdf_bytes, fn, f'📄 {display_name}｜客戶版履歷（新版，可直接轉給用人企業）', thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)

    now_ok = 'error' if (company_id and not file_id) else 'done'
    err = "'PDF已產生但回填候選人卡片失敗，TG已收到檔案，可自行下載後手動附上'" if now_ok == 'error' else 'NULL'
    D.d1(f"UPDATE client_report_requests SET status={D.q(now_ok)}, error={err}, "
         f"done_at=datetime('now','+8 hours') WHERE id={D.q(req_id)}")
    print(f'✅ {name}（{app_id}）客戶版履歷已產出並推送')


CONFIRM_CHAT_ID = '-1004320100190'  # Step1ne AI 顧問室
CONFIRM_THREAD_ID = '35'            # 客戶履歷人工確認 topic


def build_draft_text(name, synth):
    """把 synth（claude CLI 產出的 JSON）整理成一份人看得懂的純文字草稿，
    貼進 TG 讓 Jacky 確認用——跟給客戶看的PDF不是同一份東西，這份是給
    Jacky自己看的，可以帶一點內部判斷的口吻，重點是「讓他一眼看出內容
    對不對，決定要不要放行」，不用照PDF版面排。
    """
    lines = [f'📋 {name} 客戶推薦履歷草稿（確認後回覆「做PDF」才會產出PDF）', '']
    if synth.get('one_liner'):
        lines.append(f'【一句話定位】{synth["one_liner"]}')
    fc = synth.get('for_client') or {}
    if fc.get('reasons'):
        lines.append('\n【推薦理由】')
        lines += [f'・{r}' for r in fc['reasons']]
    if fc.get('job_fit_pros'):
        lines.append('\n【加分項】')
        lines += [f'・{r}' for r in fc['job_fit_pros']]
    if fc.get('job_fit_cons'):
        lines.append('\n【需留意】')
        lines += [f'・{r}' for r in fc['job_fit_cons']]
    if synth.get('work_history'):
        lines.append('\n【工作經歷】')
        for w in synth['work_history']:
            lines.append(f'・{w.get("duration","")} {w.get("employer","")}｜{w.get("role","")}')
    if synth.get('hard_filters'):
        lines.append('\n【到職可行性】')
        for h in synth['hard_filters']:
            lines.append(f'・{h.get("label","")}：{h.get("status","")}－{h.get("detail","")}')
    if synth.get('must_check_items'):
        lines.append('\n【客戶指定必要評估項目】')
        for h in synth['must_check_items']:
            lines.append(f'・{h.get("label","")}：{h.get("status","")}－{h.get("detail","")}')
    if synth.get('candidate_questions'):
        lines.append('\n【候選人主動提出的問題】')
        lines += [f'・{q.get("question","")}' for q in synth['candidate_questions']]
    if synth.get('call_summary_client_md'):
        lines.append(f'\n【電洽摘要】\n{synth["call_summary_client_md"]}')
    return '\n'.join(lines)[:3800]  # TG單則訊息上限4096字，留一點餘裕


def send_confirm_draft(req_id, name, synth):
    """2026-09-10 加：人工確認關卡，互動式版本。合成結果剛落地時不直接產PDF，
    先把純文字草稿＋兩顆按鈕貼進「客戶履歷人工確認」topic：
      ✅ 確認，產出PDF → Worker把狀態改成confirmed，下一輪tick()真的產PDF
      ✏️ 要修改        → Worker回一句引導，實際修改靠「直接回覆這則訊息打
                          修改內容」——Worker收到文字回覆會拿去重新跑一次
                          claude CLI（帶著Jacky的意見＋前一版內容），跑完
                          再貼一版新草稿，一樣兩顆按鈕，可以來回改到滿意
                          為止，狀態全程留在awaiting_confirm，不會提早產PDF。
    """
    text = build_draft_text(name, synth)
    buttons = [[
        {'text': '✅ 確認，產出PDF', 'callback_data': f'crconfirm:{req_id}'},
        {'text': '✏️ 要修改', 'callback_data': f'credit:{req_id}'},
    ]]
    mid = D.tg_buttons(text, buttons, thread=CONFIRM_THREAD_ID, chat_id=CONFIRM_CHAT_ID)
    if mid is None:
        # 貼不出去就先留在原狀態，下一輪tick()再試一次，不要憑空轉成
        # awaiting_confirm又沒有對應的訊息可以回覆。
        return False
    D.d1(f"UPDATE client_report_requests SET status='awaiting_confirm', "
         f"tg_confirm_message_id={D.q(str(mid))}, tg_confirm_chat_id={D.q(CONFIRM_CHAT_ID)}, "
         f"tg_confirm_thread_id={D.q(CONFIRM_THREAD_ID)} WHERE id={D.q(req_id)}")
    return True


def promote_synthesized():
    """撈 ai_worker.py 用 claude CLI 跑完的合成結果，寫回 client_report_requests。

    2026-09-09 加：這是 Llama 遷移的第二半——Worker 那邊已經改成排 ai_jobs
    工作、狀態設成 awaiting_synthesis，不會自己變成 pending 讓下面排版流程
    接手。這支負責接手：ai_jobs 跑完了才把結果轉存進 synthetic_content_json、
    狀態改回 pending；跑失敗（重試 3 次都不行）就直接標錯誤，不要讓請求卡死
    在 awaiting_synthesis 裡永遠沒人管。
    """
    rows = D.d1("SELECT id, application_id, ai_job_id FROM client_report_requests "
                "WHERE status='awaiting_synthesis' AND ai_job_id IS NOT NULL")
    for row in rows:
        job = D.d1(f"SELECT status, result_text, error, payload_json FROM ai_jobs WHERE id={D.q(row['ai_job_id'])}")
        if not job:
            continue
        j = job[0]
        if j['status'] == 'done':
            try:
                synth = json.loads(j['result_text'])
            except Exception as e:
                D.d1(f"UPDATE client_report_requests SET status='error', "
                     f"error={D.q(f'AI 產出的 JSON 解析失敗：{e}')}, "
                     f"done_at=datetime('now','+8 hours') WHERE id={D.q(row['id'])}")
                continue
            # 2026-09-10 加：依學歷核薪是顧問手動輸入、不是AI能推算的數字，
            # 原封不動從當初送進ai_jobs的payload帶過來，合成結果不用管這格。
            try:
                orig_payload = json.loads(j.get('payload_json') or '{}')
            except Exception:
                orig_payload = {}
            if orig_payload.get('salary_band'):
                synth['salary_band'] = orig_payload['salary_band']
            call_summary = synth.get('call_summary_client_md')
            if call_summary:
                D.d1(f"UPDATE applications SET call_summary_client_md={D.q(call_summary)}, "
                     f"call_summary_client_at=datetime('now','+8 hours') WHERE id={D.q(row['application_id'])}")
            D.d1(f"UPDATE client_report_requests SET "
                 f"synthetic_content_json={D.q(json.dumps(synth, ensure_ascii=False))} "
                 f"WHERE id={D.q(row['id'])}")
            # 2026-09-10 改：不再直接轉pending讓process_one()立刻產PDF，
            # 先送人工確認草稿——name查applications表，call_summary_client_md
            # 留在synth裡一起顯示在草稿裡，不從synth裡pop掉（原本pop是因為
            # 舊流程只把它存欄位、不會再顯示，現在草稿要秀給Jacky看）。
            app_row = D.d1(f"SELECT name FROM applications WHERE id={D.q(row['application_id'])}")
            name = app_row[0]['name'] if app_row else '（人選）'
            send_confirm_draft(row['id'], name, synth)
        elif j['status'] == 'failed':
            D.d1(f"UPDATE client_report_requests SET status='error', "
                 f"error={D.q('AI 整理履歷內容失敗（重試3次都不行）：' + str(j['error'] or '')[:200])}, "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(row['id'])}")
        # 還在 pending／running 就不動它，下一輪再檢查


def tick():
    promote_synthesized()
    # 2026-09-10 改：'pending' 是「本來就不需要AI合成」那條舊路（Worker
    # INSERT時直接給pending，沒有ai_job_id，promote_synthesized()不會碰到），
    # 這條沒有AI生成內容可審，維持原樣直接產PDF。'confirmed' 才是走過人工
    # 確認關卡、Jacky在TG按了「✅確認，產出PDF」的那批。
    rows = D.d1("SELECT id, application_id, company_id, synthetic_content_json, show_json FROM client_report_requests "
                "WHERE status IN ('pending','confirmed') ORDER BY requested_at ASC LIMIT 5")
    for row in rows:
        try:
            process_one(row)
        except Exception as e:
            print(f'[錯誤] request {row["id"]} 處理失敗：{e}', file=sys.stderr)
            D.d1(f"UPDATE client_report_requests SET status='error', error={D.q(str(e)[:300])}, "
                 f"done_at=datetime('now','+8 hours') WHERE id={D.q(row['id'])}")


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
