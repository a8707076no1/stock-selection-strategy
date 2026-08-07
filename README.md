# 台股飆股系統

自動化台股選股 + 每日圖表 + Telegram 通知 + 影片轉錄分析。

## 系統概覽

- **每日排程**（原 macOS launchd → 遷移中 → GitHub Actions）
  - 06:00 美股盤前分析（`us_premarket_analyzer.py`）
  - 08:30 林漢偉盤前解盤（`lin_hanwei_daily.py --mode premarket`）
  - 09:00 鎧俠 Kioxia 領先指標（`kioxia_leader.py`）
  - 15:40 台股飆股圖表（`generate_chart.py` + `taiwan_stock_screener_v3.py`）
  - 16:30 林漢偉盤後解盤（`lin_hanwei_daily.py --mode postmarket`）
  - 週六 12:30 林漢偉週末特別版（`lin_hanwei_daily.py --mode weekend`）

- **核心模組**
  - `taiwan_stock_screener_v3.py` — 全市場 1966 支 K 線抓取 + V42 篩選
  - `generate_chart.py` — HTML 圖表（含籌碼/型態/新聞/月營收/持股分析）
  - `sector_analyzer.py` — 46 子族群輪動 + flow_score + 資金階段
  - `analyst_targets_scraper.py` — Google News 抓當月券商目標價
  - `advanced_signals.py` — 變盤線/回後買/飆股起漲
  - `pattern_detector.py` — 台股型態辨識
  - `lin_hanwei_daily.py` — YouTube whisper 轉錄 + 重點抽取
  - `stock_bot.py` — Telegram 24/7 互動 bot

## 環境變數

複製 `.env.example` 為 `.env`，填入實際值。

## 授權

Private — 本 repo 為個人專案。
