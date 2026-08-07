#!/bin/bash
# 每週六 12:30 自動跑林漢偉週末特別版分析
BASE="${STOCK_BASE_DIR:-$HOME/Desktop/Stock Selection Strategy}"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
LOG="$BASE/logs/lin_hanwei.log"
mkdir -p "$BASE/logs"

cd "$BASE" || exit 1
echo "═══════════════════════════════════" >> "$LOG"
echo "[$(date '+%F %T')] 開始林漢偉週末特別版分析" >> "$LOG"
"$PY" weekly_lin_hanwei_analyzer.py >> "$LOG" 2>&1
echo "[$(date '+%F %T')] 結束" >> "$LOG"
