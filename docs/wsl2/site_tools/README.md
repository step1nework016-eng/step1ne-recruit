# 官網 repo 不上版控的工具（給 WSL2 用）

`deploy_direct.sh`：在官網 repo 裡被 .gitignore 排除（那個 repo 的內容會變成公開網站），所以 clone 下來不會有。
裝法：`cp docs/wsl2/site_tools/deploy_direct.sh ~/claude-projects/step1ne-stopgap-site/ && chmod +x ~/claude-projects/step1ne-stopgap-site/deploy_direct.sh`
它會讀 `~/.config/workflow-os/step1ne-frontend-cf.env`（Jacky 自己搬，不走版控、不經對話貼）。
這支檔案本身沒有任何密碼。
