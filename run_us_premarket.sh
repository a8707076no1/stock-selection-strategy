#!/bin/bash
# 每日 06:00 美股盤後 → 台股盤前預估
BASE="${STOCK_BASE_DIR:-$HOME/Desktop/Stock Selection Strategy}"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
LOG="$BASE/logs/us_premarket.log"
mkdir -p "$BASE/logs"

cd "$BASE" || exit 1
echo "═══════════════════════════════════" >> "$LOG"
echo "[$(date '+%F %T')] 開始美股盤後分析" >> "$LOG"
"$PY" us_premarket_analyzer.py >> "$LOG" 2>&1
echo "[$(date '+%F %T')] 結束" >> "$LOG"
