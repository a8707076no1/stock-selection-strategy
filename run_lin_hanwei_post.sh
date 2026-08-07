#!/bin/bash
# 每日盤後分析（16:30）
BASE="/Users/a8707076/Desktop/Stock Selection Strategy"
cd "$BASE"
export PATH="/Users/a8707076/stock_env/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
/Users/a8707076/stock_env/bin/python3 "$BASE/lin_hanwei_daily.py" --mode postmarket
