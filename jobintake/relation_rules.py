#!/usr/bin/env python3
"""客戶對象／服務線的自動帶入規則。

⚠️ 這是法遵與客戶隱私規則，不是可調的偏好設定。
規格全文：~/.claude/projects/-Users-user-----/memory/client_relation_types.md

顧問在表單上只選一個「客戶對象」，下面三個欄位就自動決定——
不讓顧問一個一個設，是因為漏設一個的後果是：
  ・未簽約客戶被具名 → 客戶還沒簽約就被公開，等於幫同業指路
  ・私人協助掛了 Step1ne 品牌 → 把朋友幫忙變成商業委託，關係性質改變
  ・ai_disclosure 設錯 → 對客戶的揭露義務沒履行

⚠️ src/index.js 的 RELATION 常數是這份規則的 JavaScript 複本。
   改這裡就要一起改那裡，兩邊不一致等於這套規則沒有效力。
"""

RELATION = {
    'signed': {
        'label': '已簽約客戶',
        'client_named': 1,          # 具名：職缺頁可以寫客戶公司名
        'ai_disclosure': 'always',  # 一律對客戶揭露初步面談由 AI 進行
        'brand_mode': 'step1ne',    # 報告掛 Step1ne 品牌
        'note': '職缺頁可具名客戶。',
    },
    'unsigned': {
        'label': '未簽約客戶',
        'client_named': 0,          # 匿名：一律改用產業描述
        'ai_disclosure': 'never',
        'brand_mode': 'step1ne',
        'note': '⚠️ 尚未簽約，職缺頁必須匿名——用產業描述取代公司名。'
                '用哪個描述請顧問決定（牽涉客戶敏感度與招募吸引力）。',
    },
    'private': {
        'label': '朋友認識・私人協助',
        'client_named': 1,          # 具名
        'ai_disclosure': 'never',
        'brand_mode': 'none',       # 報告完全不掛品牌
        'note': '⚠️ 私人協助：履歷具名，但客戶版報告不可有任何 Step1ne 痕跡'
                '（不寫「STEP1NE 人選推薦」只寫「人選推薦」、不放 logo、'
                '拿掉頁尾德仁管理顧問／統編／就服許可證、聲明裡不提公司名）。',
    },
}

SERVICE_LINE = {
    'dispatch': {
        'label': '人力派遣',
        'tag': '人力派遣',
        'track': 'dispatch',
        'note': '頁面標籤「人力派遣」；必加「派遣權益跟正職一樣嗎？」FAQ，'
                '用勞基法角度寫，不要寫成內部作業說明。',
    },
    'direct': {
        'label': '正職代招',
        'tag': '正職',
        'track': 'fulltime',
        'note': '標準版頁面。',
    },
    'executive': {
        'label': '中高階獵才',
        'tag': '高階獵才',
        'track': 'executive',
        'note': '應徵流程寫「顧問保密初談」；必加「我還在職，投遞會不會曝光？」FAQ。',
    },
}


def apply_relation(client_relation):
    """回傳該客戶對象要自動寫入的欄位。認不得的值直接爆，不要靜默給預設值。"""
    if client_relation not in RELATION:
        raise ValueError(f'不認得的客戶對象：{client_relation!r}（只能是 '
                         f'{"／".join(RELATION)}）')
    r = RELATION[client_relation]
    return {
        'client_relation': client_relation,
        'client_named': r['client_named'],
        'ai_disclosure': r['ai_disclosure'],
        'brand_mode': r['brand_mode'],
    }


def apply_service_line(service_line):
    if service_line not in SERVICE_LINE:
        raise ValueError(f'不認得的服務線：{service_line!r}（只能是 '
                         f'{"／".join(SERVICE_LINE)}）')
    return SERVICE_LINE[service_line]


if __name__ == '__main__':
    for k in RELATION:
        print(k, apply_relation(k))
    for k in SERVICE_LINE:
        print(k, apply_service_line(k)['label'])
