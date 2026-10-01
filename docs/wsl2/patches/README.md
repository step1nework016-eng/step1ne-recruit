# deliver_wsl2.patch（基準：origin/main `ac87c52`；套用：`git apply docs/wsl2/patches/deliver_wsl2.patch`）

1. **改了什麼**：`_find_chrome()` 多找 `/mnt/c/Program Files*/Google/Chrome/.../chrome.exe` 兩個路徑；`html_to_pdf()` 在 Chrome 路徑以 `.exe` 結尾時（WSL2 借用 Windows 的 Chrome），改把暫存 HTML 與輸出 PDF 放在 `/mnt/c/Windows/Temp/step1ne-chrome-pdf`，用 `wslpath -w` 把路徑轉成 `C:\...` 給 Chrome，印完再複製回呼叫端要的 `out_path`（另加 `import shutil`）。
2. **為什麼只有 WSL2 需要**：WSL2 沒裝 Linux 版 Chrome，只能叫 Windows 的 `chrome.exe`；它是 Windows 行程，看不到 WSL2 的 `/tmp` 與專案路徑，直接 `--print-to-pdf=/home/jack/...` 會印不出來，所以要走 Windows 看得到的資料夾再複製回來。
3. **Mac 上套用會不會有影響**：不會。Mac 的 Chrome 是 `/Applications/Google Chrome.app/...`，不以 `.exe` 結尾，`is_win_chrome=False`，新分支全部不執行；非 `.exe` 時 `win_out_path` 就等於 `out_path`，Chrome 指令列、暫存檔位置、輪詢的檔案都跟原版一樣（已用模擬 Mac 路徑實測：`--user-data-dir=/tmp/step1ne-chrome-pdf`、`--print-to-pdf=<out_path>`、一般 `file://` 網址）；唯一差別是多一次對 `out_path` 的 exists→remove（原本下一行就會做）。
