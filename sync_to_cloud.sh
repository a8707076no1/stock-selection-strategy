#!/bin/bash
# ────────────────────────────────────────────────────
# 把最新產出的圖表 + summary 同步到 Cloudflare Pages
# 用法：./sync_to_cloud.sh
# 由 stock_agent + catchup_watchdog 自動呼叫
# ────────────────────────────────────────────────────
set -e
BASE="${STOCK_BASE_DIR:-$HOME/Desktop/Stock Selection Strategy}"
PY="$HOME/stock_env/bin/python3"
LOG="$BASE/logs/cloud_sync.log"
mkdir -p "$BASE/logs" "$BASE/web"

log() { echo "[$(date '+%F %T')] $1" | tee -a "$LOG"; }

cd "$BASE"

# 1. 複製最近 10 個交易日的 chart 到 web/
log "📋 複製最近 chart 到 web/..."
count=0
for f in $(ls -t 飆股圖表_*.html 2>/dev/null | head -10); do
    cp -u "$f" web/
    count=$((count+1))
done
log "  ✅ 複製 $count 個檔案"

# 2. 產出/更新 summary.json
log "📊 產生 summary.json..."
"$PY" generate_daily_summary.py >> "$LOG" 2>&1

# 3. 部署到 Cloudflare Pages
log "☁️  部署 Cloudflare Pages..."
DEPLOY_URL=$(wrangler pages deploy web --project-name=stock-selection --commit-dirty=true 2>&1 | grep -oE "https://[a-z0-9-]+\.stock-selection\.pages\.dev" | tail -1)

if [ -n "$DEPLOY_URL" ]; then
    log "  ✅ 部署成功：$DEPLOY_URL"
    log "  🌐 主網址：https://stock-selection.pages.dev"
else
    log "  ❌ 部署失敗，請檢查 wrangler login"
    exit 1
fi
