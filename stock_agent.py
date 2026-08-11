#!/usr/bin/env python3
"""
台股報告監控 AI Agent
每天 15:50 自動檢查報告是否正確產生，如果沒有則自動修正並通知
"""
import os, sys, json, subprocess, requests
from datetime import datetime, timedelta

# ── 設定 ──────────────────────────────────────────
BASE_DIR    = "/Users/a8707076/Desktop/Stock Selection Strategy"
SCREENER    = f"{BASE_DIR}/run_screener.sh"
CHART       = f"{BASE_DIR}/run_chart.sh"
LOG_FILE    = f"{BASE_DIR}/logs/stock_screener.log"
REPORT_FILE = f"{BASE_DIR}/logs/agent_report.log"

TG_TOKEN    = os.environ.get("STOCK_TG_TOKEN", "")
TG_CHAT     = os.environ.get("STOCK_TG_CHAT", "")

def tg(msg):
    """發送 Telegram 通知"""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram 發送失敗: {e}")

def log(msg):
    """寫入 agent log"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}")

def get_today():
    """取得今天日期字串"""
    return datetime.today().strftime("%Y%m%d")

def is_trading_day():
    """判斷今天是否為交易日（週一到週五）"""
    return datetime.today().weekday() < 5

def check_chart_content(html_path):
    """深度檢查飆股圖表內容：新聞、K線、線型"""
    import re
    issues = []
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            raw = f.read()
        m = re.search(r'const stocks = (\[.*?\]);\n', raw, re.DOTALL)
        if not m:
            issues.append("❌ 圖表 HTML 解析失敗：找不到 stocks JSON")
            return issues
        stocks = json.loads(m.group(1))
        total = len(stocks)
        if total == 0:
            issues.append("❌ 圖表內無任何股票資料")
            return issues

        # 1. K線資料是否有今天的日期（僅在收盤後 15:30 以後才檢查）
        today_dash = datetime.today().strftime("%Y-%m-%d")
        now_hour = datetime.now().hour
        now_min  = datetime.now().minute
        if now_hour > 15 or (now_hour == 15 and now_min >= 30):
            kline_ok = 0
            for s in stocks:
                if s.get("dates") and today_dash in s["dates"]:
                    kline_ok += 1
            # ★ 改用比例判定：有 50% 的股票包含今日就算正常
            # （Yahoo 抓 1966 支總會有少數小型股延遲到隔天才更新）
            if total > 0:
                ratio = kline_ok / total
                if ratio < 0.30:
                    issues.append(
                        f"⚠️ K線資料含今日比例過低（{kline_ok}/{total} = {ratio*100:.0f}%），可能價格快取未更新"
                    )
                elif ratio < 0.70:
                    log(f"⚠️ K線資料含今日：{kline_ok}/{total} ({ratio*100:.0f}%)，部分股延遲（仍視為正常）")
                else:
                    log(f"✅ K線資料含今日：{kline_ok}/{total} ({ratio*100:.0f}%)")
        else:
            # 盤中/盤前，檢查最新日期是否在3天內
            latest_dates = set()
            for s in stocks:
                if s.get("dates"):
                    latest_dates.add(s["dates"][-1])
            if latest_dates:
                newest = max(latest_dates)
                log(f"✅ K線最新日期：{newest}（尚未收盤，跳過今日日期檢查）")

        # 2. 月營收是否有效（非全部 —）
        rev_ok = sum(1 for s in stocks if s.get("rev",{}).get("rev","—") != "—")
        if rev_ok == 0:
            issues.append(f"❌ 月營收全部為空（0/{total}），revenue API 可能異常")
        elif rev_ok < total * 0.5:
            issues.append(f"⚠️ 月營收僅 {rev_ok}/{total} 支有資料，部分抓取失敗")
        else:
            log(f"✅ 月營收正常：{rev_ok}/{total} 支")

        # 3. 新聞是否有更新
        news_ok = sum(1 for s in stocks if s.get("rev",{}).get("news") and len(s["rev"]["news"]) > 0)
        if news_ok == 0:
            issues.append(f"❌ 新聞全部為空（0/{total}），news API 可能異常")
        elif news_ok < total * 0.3:
            issues.append(f"⚠️ 新聞僅 {news_ok}/{total} 支有資料")
        else:
            log(f"✅ 新聞正常：{news_ok}/{total} 支")

        # 4. 線型識別是否有效（非全部「觀察中」）
        pat_ok = sum(1 for s in stocks if s.get("pattern",{}).get("name","觀察中") != "觀察中")
        sr_ok  = sum(1 for s in stocks if s.get("support") or s.get("resistance"))
        if pat_ok == 0 and sr_ok == 0:
            issues.append(f"❌ 線型全部為「觀察中」且無支撐壓力線（0/{total}），pattern_detector 可能異常")
        else:
            log(f"✅ 線型識別：{pat_ok}/{total} 支有型態，{sr_ok}/{total} 支有支撐壓力")

        # 5. 集保持股是否有資料
        hold_ok = sum(1 for s in stocks if s.get("holding") and s["holding"].get("major",0) > 0)
        if hold_ok == 0:
            issues.append(f"⚠️ 集保持股全部為空，TDCC 資料可能未更新")
        else:
            log(f"✅ 集保持股：{hold_ok}/{total} 支")

    except Exception as e:
        issues.append(f"❌ 圖表內容檢查異常：{e}")
    return issues

def check_report():
    """檢查今天的報告是否存在且為最新"""
    today = get_today()
    issues = []

    # 檢查選股報告
    xlsx = f"{BASE_DIR}/飆股日報_{today}.xlsx"
    csv  = f"{BASE_DIR}/飆股日報_{today}.csv"
    html = f"{BASE_DIR}/飆股圖表_{today}.html"

    if not os.path.exists(xlsx):
        issues.append(f"❌ 選股報告不存在：飆股日報_{today}.xlsx")
    else:
        mtime = datetime.fromtimestamp(os.path.getmtime(xlsx))
        if mtime.strftime("%Y%m%d") != today:
            issues.append(f"❌ 選股報告日期不符：最後修改 {mtime.strftime('%Y-%m-%d %H:%M')}")
        else:
            log(f"✅ 選股報告正常：{mtime.strftime('%H:%M')} 產生")

    if not os.path.exists(html):
        issues.append(f"❌ K線圖報告不存在：飆股圖表_{today}.html")
    else:
        mtime = datetime.fromtimestamp(os.path.getmtime(html))
        if mtime.strftime("%Y%m%d") != today:
            issues.append(f"❌ K線圖日期不符：最後修改 {mtime.strftime('%Y-%m-%d %H:%M')}")
        else:
            log(f"✅ K線圖報告正常：{mtime.strftime('%H:%M')} 產生")
            # ── 深度內容檢查 ──
            content_issues = check_chart_content(html)
            issues.extend(content_issues)

    # 檢查快取資料日期
    try:
        import pickle
        with open(f"{BASE_DIR}/cache/price_data.pkl", "rb") as f:
            pc = pickle.load(f)
        sample = list(pc.values())[0]
        last_date = sample["date"].iloc[-1]
        # 允許落後3天（週末+假日+收盤後更新延遲）
        threshold = (datetime.today()-timedelta(days=3)).strftime("%Y-%m-%d")
        if last_date < threshold:
            issues.append(f"⚠️ 快取資料最新日期：{last_date}（落後超過2天，需要更新）")
        else:
            log(f"✅ 快取資料最新：{last_date}")
    except Exception as e:
        issues.append(f"⚠️ 無法讀取快取：{e}")

    return issues

def run_cmd(cmd, timeout=1500):
    """執行指令並回傳輸出"""
    try:
        result = subprocess.run(
            ["bash", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return "❌ 執行超時"
    except Exception as e:
        return f"❌ 執行錯誤: {e}"

def auto_fix(issues):
    """自動修正問題"""
    fixes = []
    today = get_today()

    screener_needed = any("選股報告" in i for i in issues)
    chart_needed    = any("K線圖" in i for i in issues)
    content_needed  = any(k in "".join(issues) for k in ["月營收","新聞","線型","pattern","revenue","news"])

    if screener_needed:
        log("🔧 開始執行選股程式...")
        tg("🔧 <b>自動修正中</b>\n正在重新執行選股程式，請稍候...")
        out = run_cmd(SCREENER, timeout=1500)
        if f"飆股日報_{today}" in out or "篩選完成" in out:
            fixes.append("✅ 選股程式執行成功")
            log("✅ 選股程式執行成功")
        else:
            fixes.append(f"❌ 選股程式執行失敗：{out[-200:]}")
            log(f"❌ 選股程式執行失敗")

    if chart_needed or screener_needed or content_needed:
        # 內容異常時先清快取再重跑
        if content_needed and not chart_needed:
            rev_cache = f"{BASE_DIR}/cache/stock_revenue.json"
            if os.path.exists(rev_cache):
                os.remove(rev_cache)
                log("🗑️ 已清除營收/新聞快取")
                fixes.append("🗑️ 已清除營收/新聞快取")
        log("🔧 開始執行圖表程式...")
        out = run_cmd(CHART, timeout=900)
        if f"飆股圖表_{today}" in out or "HTML 報表已產生" in out:
            fixes.append("✅ 圖表程式執行成功")
            log("✅ 圖表程式執行成功")
            # ☁️ 自動上傳 Cloudflare Pages（PWA App）
            try:
                sync_out = run_cmd(f"{BASE_DIR}/sync_to_cloud.sh", timeout=180)
                if "部署成功" in sync_out or "Deployment complete" in sync_out:
                    log("✅ Cloudflare Pages 已同步更新")
                    fixes.append("☁️ 已同步至 PWA App")
                else:
                    log(f"⚠️ CF sync 未成功: {sync_out[-200:]}")
                    fixes.append("⚠️ PWA 同步失敗（請看 logs/cloud_sync.log）")
            except Exception as _e:
                log(f"⚠️ CF sync 異常: {_e}")
        else:
            fixes.append(f"❌ 圖表程式執行失敗：{out[-200:]}")
            log(f"❌ 圖表程式執行失敗")

    return fixes

def push_detailed_holdings_report(today_str, now_str):
    """推播每支持股的詳細分析（操作建議 / 加碼點 / 停損 / 目標 / 趨勢 / 技術 / 籌碼 / 法人 / 策略）"""
    try:
        sys.path.insert(0, BASE_DIR)
        from telegram_helpers import (
            find_latest_chart_html, parse_chart_html,
            fmt_holding_detail, fmt_breakouts_summary, split_message
        )
    except Exception as e:
        log(f"⚠️ 載入 telegram_helpers 失敗：{e}")
        return

    html_path = find_latest_chart_html(BASE_DIR)
    if not html_path:
        log("⚠️ 找不到飆股圖表 HTML，跳過詳細推播")
        return
    stocks, holdings, breakouts = parse_chart_html(html_path)
    if not holdings:
        log("⚠️ HTML 內無持股資料，跳過詳細推播")
        return

    # 持股總覽 header
    total_mv = sum(h.get("market_value", 0) for h in holdings)
    total_pl = sum(h.get("pl_amt", 0) for h in holdings)
    header = (f"📊 <b>每日持股分析 {today_str} {now_str}</b>\n"
              f"共 {len(holdings)} 支｜市值 {total_mv:,.0f}｜損益 {total_pl:+,.0f}")
    tg(header)
    log("📤 已推播持股總覽 header")

    # 每支持股一則訊息（避免單則太長）
    for h in holdings:
        msg = fmt_holding_detail(h, header=True)
        for part in split_message(msg):
            tg(part)
        log(f"  📤 已推播 {h.get('sid')} {h.get('name')}")

    # 即將突破彙總
    if breakouts:
        tg(fmt_breakouts_summary(breakouts))
        log(f"📤 已推播即將突破彙總（{len(breakouts)} 支）")

    # 互動提示
    tip = ("💬 <b>互動查詢</b>\n"
           "輸入股票代號（如 <code>2330</code>）即可查詢詳細分析\n"
           "輸入 <code>幫助</code> 查看完整指令")
    tg(tip)


def main():
    today_str = datetime.today().strftime("%Y-%m-%d")
    now_str   = datetime.now().strftime("%H:%M")

    log("="*50)
    log(f"🤖 Agent 啟動 {today_str} {now_str}")

    if not is_trading_day():
        log("📅 今天非交易日，跳過檢查")
        return

    # 第一次檢查
    issues = check_report()

    if not issues:
        msg = f"✅ <b>台股報告監控 {today_str}</b>\n\n所有報告正常產生！\n🕒 檢查時間：{now_str}"
        tg(msg)
        log("✅ 所有報告正常，通知已發送")
        # 推播詳細持股分析
        push_detailed_holdings_report(today_str, now_str)
        return

    # 有問題，通知並自動修正
    issue_text = "\n".join(issues)
    tg(f"⚠️ <b>台股報告異常 {today_str}</b>\n\n{issue_text}\n\n🔧 正在自動修正中...")
    log(f"發現 {len(issues)} 個問題，開始修正")

    fixes = auto_fix(issues)

    # 修正後再次檢查
    issues_after = check_report()

    if not issues_after:
        fix_text = "\n".join(fixes)
        report = (
            f"✅ <b>自動修正成功 {today_str}</b>\n\n"
            f"<b>原始問題：</b>\n{issue_text}\n\n"
            f"<b>修正動作：</b>\n{fix_text}\n\n"
            f"<b>結果：</b>報告已正常產生 🎉"
        )
        tg(report)
        log("✅ 修正成功，報告已正常")
        # 修正成功後也推送詳細持股分析
        push_detailed_holdings_report(today_str, now_str)
    else:
        remaining = "\n".join(issues_after)
        fix_text  = "\n".join(fixes)
        report = (
            f"❌ <b>自動修正失敗 {today_str}</b>\n\n"
            f"<b>原始問題：</b>\n{issue_text}\n\n"
            f"<b>修正嘗試：</b>\n{fix_text}\n\n"
            f"<b>仍有問題：</b>\n{remaining}\n\n"
            f"⚠️ 請手動檢查！"
        )
        tg(report)
        log("❌ 修正後仍有問題，需要手動處理")

    log("="*50)

if __name__ == "__main__":
    main()
