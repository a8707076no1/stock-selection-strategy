#!/bin/bash
# Bot 永遠重啟 wrapper（即使 KeepAlive 失效也能自救）

BASE="${STOCK_BASE_DIR:-$HOME/Desktop/Stock Selection Strategy}"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

LOG="$BASE/logs/stock_bot.log"
mkdir -p "$BASE/logs"

echo "[$(date '+%F %T')] === run_bot.sh 啟動 (BASE=$BASE PY=$PY) ===" >> "$LOG"

cd "$BASE" || { echo "[$(date '+%F %T')] cd $BASE failed" >> "$LOG"; exit 1; }

while true; do
  echo "[$(date '+%F %T')] 啟動 stock_bot.py" >> "$LOG"
  "$PY" stock_bot.py >> "$LOG" 2>&1
  rc=$?
  echo "[$(date '+%F %T')] bot exited (exit=$rc), restart in 10s..." >> "$LOG"
  sleep 10
done
