#!/bin/bash
# 直接部署到 step1ne.com（Worker broad-haze-0c9b），跳過 git push 等 45秒~幾分鐘的
# 自動建置。用法：./deploy_direct.sh
#
# 原理：rsync 這個 repo（排除 .git/.wrangler/.claude 等內部檔案）到一個乾淨的
# staging 目錄，wrangler.toml 刻意放在 staging 目錄「外面」一層，避免 wrangler.toml
# 本身或它執行時建立的 .wrangler/ 暫存檔被當成網站內容一起上傳、公開曝光
# （2026-09-01 曾經真的發生過一次，已修正）。
#
# 注意：這是額外的部署管道，不是取代 git push。git push deploy HEAD:main 那條路
# 還是要繼續做（保留版本歷史、讓 Cloudflare 自己的建置紀錄也對得上）；這支腳本只是
# 讓效果立刻生效，不用等自動建置。

set -e
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE_PARENT="/private/tmp/step1ne-direct-deploy"
STAGE_DIR="$STAGE_PARENT/site"
mkdir -p "$STAGE_DIR"

rsync -a --delete \
  --exclude='.git/' --exclude='.wrangler/' --exclude='.claude/' \
  --exclude='.DS_Store' --exclude='wrangler.toml' --exclude='.assetsignore' \
  --exclude='DEPLOY.md' --exclude='.gitignore' --exclude='deploy_direct.sh' \
  "$REPO_DIR/" "$STAGE_DIR/"

cat > "$STAGE_PARENT/wrangler.toml" << 'EOF'
name = "broad-haze-0c9b"
compatibility_date = "2026-08-28"
account_id = "673dc235236747edf1c1d9c66c66ec68"

[assets]
directory = "./site"
EOF

source ~/.config/workflow-os/step1ne-frontend-cf.env
export CLOUDFLARE_API_TOKEN
cd "$STAGE_PARENT"
npx wrangler deploy --config wrangler.toml
