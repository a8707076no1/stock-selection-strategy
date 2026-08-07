#!/bin/bash
# 每日 15:30 排程：增量更新 + 全市場篩選
# 步驟 1：用 yfinance 抓今日新 K 棒（永久累積，不會破壞歷史）
# 步驟 2：跑 screener_v3 篩選（V42 命中會獨立列出）

BASE="$HOME/Desktop/Stock Selection Strategy"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
cd "$BASE"
mkdir -p logs

LOGDATE=$(date '+%Y%m%d')
echo "═══════════════════════════════════" >> "logs/screener_${LOGDATE}.log"
echo "[$(date '+%F %T')] 開始 daily update + screener" >> "logs/screener_${LOGDATE}.log"

# 1) Yahoo 增量更新（只抓「最後一天之後」的 K 棒）
echo "📡 步驟 1：每日 K 線增量更新..." >> "logs/screener_${LOGDATE}.log"
"$PY" daily_yahoo_update.py >> "logs/screener_${LOGDATE}.log" 2>&1

# 2) 跑 screener
echo "🔍 步驟 2：跑 screener_v3..." >> "logs/screener_${LOGDATE}.log"
"$PY" taiwan_stock_screener_v3.py 2>&1 | tee -a "logs/screener_${LOGDATE}.log"

echo "[$(date '+%F %T')] 結束" >> "logs/screener_${LOGDATE}.log"
