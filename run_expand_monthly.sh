#!/bin/bash
# 每月補新股 — 跑 expand_universe.py 把上市/上櫃新增的股票補進 cache
BASE="$HOME/Desktop/Stock Selection Strategy"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
cd "$BASE"
mkdir -p logs
echo "[$(date '+%F %T')] === 月度 expand_universe ===" >> logs/expand_monthly.log
"$PY" expand_universe.py >> logs/expand_monthly.log 2>&1
echo "[$(date '+%F %T')] === 完成 ===" >> logs/expand_monthly.log
