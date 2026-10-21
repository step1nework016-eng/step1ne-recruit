"""2026-10-21 Jacky：無經驗／培訓的 BIM、繪圖職缺改走快速應徵（/apply-quick/），資深與其他職缺照舊（/apply/）。
官網職缺頁的「我要應徵」按鈕、LINE 回覆都看這份名單；要加減職缺改這裡（public-worker 的 QUICK_JOBS 也要一起改）。"""
QUICK_APPLY_SLUGS = {
    'bim-mep-drafting-engineer-hsinchu',
    'semiconductor-bim-mep-drafting-trainee-hsinchu',
    'bim-engineer-houli',
    'trainee-engineering-design-engineer',
}


def apply_path(slug):
    return '/apply-quick/' if slug in QUICK_APPLY_SLUGS else '/apply/'
