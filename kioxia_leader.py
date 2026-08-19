"""
鎧俠 Kioxia (285A.T) 領先指標
==========================================
日股鎧俠 (Kioxia) 是台股記憶體族群早盤先行指標。

邏輯：
  Tokyo 開盤 = TW 08:00
  1. 抓 TW 08:00~08:30 每分鐘 K 線（= 東京開盤前 30 分）
  2. 找「最大交易量」那分鐘 → 記其收盤價 = benchmark
  3. TW 09:00（台股開盤時）比對：
       Kioxia 現價 > benchmark → 🟢 今日台股開盤強勢
       Kioxia 現價 < benchmark → 🔴 今日台股開盤弱勢
  4. 附上 Kioxia 1 分 K 圖表連結給 Telegram（點入可深入分析）

排程建議：TW 每工作日 09:00 執行一次（LaunchAgent + Watchdog 補跑）
"""
import os, sys, json, re
from datetime import datetime, timedelta

BASE = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
CACHE = os.path.join(BASE, "cache", "kioxia_leader.json")

TICKER = "285A.T"
NAME = "鎧俠 Kioxia"

# 圖表連結（開手機瀏覽即可）
CHART_URLS = {
    "TradingView 1 分 K": "https://www.tradingview.com/chart/?symbol=TSE%3A285A&interval=1",
    "Yahoo Japan 即時": "https://finance.yahoo.co.jp/quote/285A.T/chart",
    "Yahoo Finance (US)": "https://finance.yahoo.com/chart/285A.T",
    "Investing.com": "https://www.investing.com/equities/kioxia-holdings-corp-chart",
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_kioxia_1m():
    """抓今日 1 分 K，時區轉台北"""
    import yfinance as yf
    tk = yf.Ticker(TICKER)
    df = tk.history(period="1d", interval="1m", prepost=False)
    if df is None or df.empty:
        return None
    df = df.copy()
    df.index = df.index.tz_convert("Asia/Taipei")
    return df


def fetch_prev_close():
    """抓昨日收盤（用戶直覺對比）"""
    import yfinance as yf
    tk = yf.Ticker(TICKER)
    df = tk.history(period="5d", interval="1d")
    if df is None or len(df) < 2: return None
    return float(df["Close"].iloc[-2])


def find_max_vol_bar(df, start_hm=(8,0), end_hm=(8,30)):
    """在 TW HH:MM 區間找最大量 bar"""
    sh, sm = start_hm; eh, em = end_hm
    mask = (
        ((df.index.hour * 60 + df.index.minute) >= (sh * 60 + sm)) &
        ((df.index.hour * 60 + df.index.minute) <  (eh * 60 + em))
    )
    win = df[mask]
    if win.empty:
        return None
    idx = win["Volume"].idxmax()
    bar = win.loc[idx]
    return {
        "time":   idx.strftime("%Y-%m-%d %H:%M"),
        "volume": int(bar["Volume"]),
        "open":   float(bar["Open"]),
        "high":   float(bar["High"]),
        "low":    float(bar["Low"]),
        "close":  float(bar["Close"]),
        "window_bars": len(win),
    }


def current_price(df):
    if df is None or df.empty: return None
    last = df.iloc[-1]
    return {
        "time":  df.index[-1].strftime("%H:%M"),
        "price": float(last["Close"]),
        "high":  float(last["High"]),
        "low":   float(last["Low"]),
    }


def save(today, benchmark, verdict):
    all_ = {}
    if os.path.exists(CACHE):
        try: all_ = json.load(open(CACHE))
        except: pass
    all_[today] = {"benchmark": benchmark, "verdict": verdict,
                   "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    # 只留最近 40 天
    keys = sorted(all_.keys())
    if len(keys) > 40:
        for k in keys[:-40]: all_.pop(k, None)
    with open(CACHE, "w") as f:
        json.dump(all_, f, ensure_ascii=False, indent=2)


def push_tg(msg):
    import requests
    tok = os.environ.get("STOCK_TG_TOKEN", "")
    chat = os.environ.get("STOCK_TG_CHAT", "")
    extra = os.environ.get("STOCK_TG_CHAT_EXTRA", "")
    if not tok or not chat:
        try:
            t = open(os.path.join(BASE, "stock_agent.py"), encoding="utf-8").read()
            if not tok:
                m = re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', t);  tok = m.group(1) if m else ""
            if not chat:
                m = re.search(r'TG_CHAT\s*=\s*"([^"]+)"', t);  chat = m.group(1) if m else ""
        except: pass
    if not tok or not chat:
        log("⚠️ Telegram 未設定"); return False
    chats = [chat] + [c.strip() for c in extra.split(",") if c.strip() and c.strip() != chat]
    ok = 0
    for c in chats:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": c, "text": msg, "parse_mode": "HTML",
                                    "disable_web_page_preview": True}, timeout=15)
            log(f"📨 chat={c}: {r.status_code}")
            if r.status_code == 200: ok += 1
        except Exception as e:
            log(f"⚠️ chat={c} fail: {e}")
    return ok == len(chats)


def main():
    today = datetime.today().strftime("%Y-%m-%d")
    log("=" * 60)
    log(f"鎧俠 {NAME} 領先指標分析 ({today})")
    log("=" * 60)

    df = fetch_kioxia_1m()
    if df is None or df.empty:
        log("❌ 抓不到 285A.T 1 分 K"); return

    bench = find_max_vol_bar(df, (8,0), (8,30))
    if not bench:
        log("❌ 08:00-08:30 無資料（可能還沒開盤/假日）"); return

    log(f"📌 08:00-08:30 最大量 bar:")
    log(f"    時間={bench['time']}, 量={bench['volume']:,}, 收={bench['close']:.0f}, 高={bench['high']:.0f}")

    cur = current_price(df)
    if not cur:
        log("❌ 無現價"); return
    log(f"📈 yfinance 最新 bar ({cur['time']}): {cur['price']:.0f}")

    prev_close = fetch_prev_close()
    log(f"📉 昨收 (參考): {prev_close}")

    # 雙軌判定：vs 早盤最大量 bar / vs 昨收
    bench_price = bench["close"]
    diff = cur["price"] - bench_price
    diff_pct = diff / bench_price * 100
    if cur["price"] > bench_price:
        verdict_emoji = "🟢"; verdict_txt = "強勢開盤"
        strength = "🚀 台股記憶體今日有望走強！鎧俠已站上早盤最大量價"
    else:
        verdict_emoji = "🔴"; verdict_txt = "弱勢開盤"
        strength = "⚠️ 台股記憶體今日恐承壓，鎧俠跌破早盤最大量價"

    # vs 昨收（用戶直覺）
    prev_line = ""
    if prev_close and prev_close > 0:
        pd_diff = cur["price"] - prev_close
        pd_pct = pd_diff / prev_close * 100
        prev_emoji = "🟢" if pd_pct >= 0 else "🔴"
        prev_line = f"vs 昨收 {prev_close:.0f}：{'+' if pd_diff>=0 else ''}{pd_diff:.0f} 円（{prev_emoji} {pd_pct:+.2f}%）"

    verdict = {
        "current_price": cur["price"], "current_time": cur["time"],
        "bench_price": bench_price, "diff": diff, "diff_pct": round(diff_pct, 2),
        "prev_close": prev_close, "text": verdict_txt,
    }
    save(today, bench, verdict)

    tw_mem_stocks = ["2408 南亞科", "3529 力旺", "2451 創見", "3006 晶豪科",
                     "5351 鈺創", "3260 威剛", "8074 華景電", "8271 宇瞻"]

    now_hm = datetime.now().strftime("%H:%M")
    lines = [
        f"🇯🇵 <b>鎧俠 Kioxia 領先指標</b> ({today})",
        f"<i>台股記憶體族群早盤先行指標</i>",
        "",
        f"⏱ <b>訊息產出時間</b>：{now_hm}",
        f"📊 <b>資料時間</b>：{cur['time']}（yfinance 有 15-30 分鐘延遲）",
        f"⚠️ <i>實際即時價請點下方 TradingView 連結確認</i>",
        "",
        f"📌 <b>08:00-08:30 最大量 bar</b>",
        f"  ⏱ {bench['time'][-5:]}｜量 <b>{bench['volume']:,}</b> 股",
        f"  💰 收 <b>{bench['close']:.0f}</b> ／ 高 {bench['high']:.0f} ／ 低 {bench['low']:.0f}",
        "",
        f"📈 <b>{cur['time']} 時價</b>：<b>{cur['price']:.0f}</b>",
        f"     vs 早盤量價 {bench_price:.0f}：{'+' if diff>=0 else ''}{diff:.0f} 円（{diff_pct:+.2f}%）",
    ]
    if prev_line:
        lines.append(f"     {prev_line}")
    lines.extend([
        "",
        f"{verdict_emoji} <b>早盤判定：{verdict_txt}</b>",
        f"  {strength}",
        f"  <i>（此為 09:00 分析當下狀態，之後可能反轉）</i>",
        "",
        f"💡 <b>台股記憶體對應觀察股</b>",
        f"  {' / '.join(tw_mem_stocks[:6])}",
        "",
        f"📊 <b>Kioxia 即時 1 分 K</b>",
    ])
    for label, url in CHART_URLS.items():
        lines.append(f"  🔗 <a href='{url}'>{label}</a>")

    msg = "\n".join(lines)
    push_tg(msg)
    log(f"✅ 完成：{verdict_txt}")


if __name__ == "__main__":
    main()
