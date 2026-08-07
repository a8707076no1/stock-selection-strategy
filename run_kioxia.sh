#!/bin/bash
BASE="/Users/a8707076/Desktop/Stock Selection Strategy"
cd "$BASE"
export PATH="/Users/a8707076/stock_env/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
/Users/a8707076/stock_env/bin/python3 "$BASE/kioxia_leader.py" >> "$BASE/logs/kioxia.log" 2>&1
