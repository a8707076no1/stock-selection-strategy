#!/bin/bash
BASE="/Users/a8707076/Desktop/Stock Selection Strategy"
cd "$BASE"
# 🤝 chart 產出前先掃 M&A 新聞（覆蓋 stock_news 未含的股）
/Users/a8707076/stock_env/bin/python3 merger_news_scanner.py > "$BASE/logs/merger_scan.log" 2>&1
# 📈 生成 chart
/Users/a8707076/stock_env/bin/python3 generate_chart.py
# ☁️ chart 產出後自動同步 Cloudflare Pages (PWA App)
if [ -f "$BASE/飆股圖表_$(date '+%Y%m%d').html" ]; then
  /bin/bash "$BASE/sync_to_cloud.sh"
fi
