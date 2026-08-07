"""
美股盤後 → 台股盤前 資金輪動預測（每日 06:00 自動執行）
==========================================================
邏輯（基於林漢偉 5/30 影片觀察）：
  美股關鍵族群代表股的漲跌會在當天直接反應到台股對應族群
  → 06:00（台股開盤 3.5 小時前）抓昨日美股收盤
  → 對應到台股 30+ 個子族群
  → 預估今日資金輪動方向 + 具體個股建議
  → 推送 Telegram

輸出：
  1. 美股 50+ 代表股漲跌
  2. 強度評等：🚀 強漲 / 📈 漲 / ➡️ 平 / 📉 跌 / 🔻 重挫
  3. 對應台股族群預估方向 + 強度
  4. 具體進場標的（用 SUBSECTORS.key_stocks）
  5. 大盤整體判讀
"""
import os, sys, json
from datetime import datetime, timedelta
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sector_analyzer import SUBSECTORS

# ═══ 美股代表股 → 台股子族群映射 ═══════════════════════════
US_TO_TW = {
    # ─── AI 半導體核心 ───
    "NVDA":  {"name": "輝達",      "tw_subs": ["矽智財_ASIC", "AI伺服器_ODM", "先進封裝_CoWoS設備"]},
    "AVGO":  {"name": "博通",      "tw_subs": ["矽智財_ASIC", "矽光子_CPO", "資料中心交換器"]},
    "AMD":   {"name": "超微",      "tw_subs": ["矽智財_ASIC", "高速傳輸IC"]},
    "MRVL":  {"name": "邁威爾",    "tw_subs": ["矽智財_ASIC", "矽光子_CPO"]},
    "TSM":   {"name": "台積 ADR",  "tw_subs": ["晶圓代工_先進製程"]},
    "INTC":  {"name": "英特爾",    "tw_subs": ["晶圓代工_成熟製程"]},
    "QCOM":  {"name": "高通",      "tw_subs": ["IC設計_手機AP"]},
    "ARM":   {"name": "ARM",       "tw_subs": ["矽智財_ASIC", "IC設計_手機AP"]},

    # ─── 記憶體 ───
    "MU":    {"name": "美光",      "tw_subs": ["記憶體_DRAM_NAND"]},
    "STX":   {"name": "希捷",      "tw_subs": ["記憶體_DRAM_NAND"]},
    "WDC":   {"name": "西部數據",  "tw_subs": ["記憶體_DRAM_NAND"]},

    # ─── 半導體設備 ───
    "ASML":  {"name": "艾司摩爾",  "tw_subs": ["先進封裝_CoWoS設備", "晶圓設備_廠務"]},
    "AMAT":  {"name": "應用材料",  "tw_subs": ["晶圓設備_廠務", "先進封裝_CoWoS設備"]},
    "KLAC":  {"name": "科林研發",  "tw_subs": ["晶圓設備_廠務", "探針卡_測試介面"]},
    "LRCX":  {"name": "林研",      "tw_subs": ["晶圓設備_廠務"]},

    # ─── 類比/PMIC ───
    "TXN":   {"name": "德州儀器",  "tw_subs": ["電源管理IC_PMIC"]},
    "ON":    {"name": "安森美",    "tw_subs": ["車用功率元件", "電源管理IC_PMIC"]},
    "ADI":   {"name": "亞德諾",    "tw_subs": ["電源管理IC_PMIC", "高速傳輸IC"]},
    "MCHP":  {"name": "微芯",      "tw_subs": ["電源管理IC_PMIC"]},

    # ─── AI 伺服器與硬體 ───
    "DELL":  {"name": "戴爾",      "tw_subs": ["AI伺服器_ODM"]},
    "HPE":   {"name": "HP 企業",   "tw_subs": ["AI伺服器_ODM"]},
    "SMCI":  {"name": "美超微",    "tw_subs": ["AI伺服器_ODM", "伺服器高階機殼"]},
    "VRT":   {"name": "Vertiv",    "tw_subs": ["伺服器電源", "散熱_水冷"]},

    # ─── 雲端 / 大客戶（CSP）───
    "MSFT":  {"name": "微軟",      "tw_subs": ["AI伺服器_ODM"]},
    "GOOGL": {"name": "Google",    "tw_subs": ["矽智財_ASIC", "AI伺服器_ODM"]},
    "AMZN":  {"name": "亞馬遜",    "tw_subs": ["AI伺服器_ODM"]},
    "META":  {"name": "Meta",      "tw_subs": ["矽智財_ASIC", "AI伺服器_ODM"]},
    "ORCL":  {"name": "甲骨文",    "tw_subs": ["AI伺服器_ODM", "資料中心交換器"]},

    # ─── 網通 / 矽光子 / CPO ───
    "ANET":  {"name": "Arista",    "tw_subs": ["資料中心交換器", "網通_交換器"]},
    "CSCO":  {"name": "思科",      "tw_subs": ["網通_交換器", "資料中心交換器"]},
    "COHR":  {"name": "Coherent",  "tw_subs": ["矽光子_CPO"]},
    "LITE":  {"name": "Lumentum",  "tw_subs": ["矽光子_CPO"]},

    # ─── 手機 / 消費電子 ───
    "AAPL":  {"name": "蘋果",      "tw_subs": ["光學_高階手機鏡頭", "AI伺服器_ODM"]},

    # ─── 電動車 / 機器人 ───
    "TSLA":  {"name": "特斯拉",    "tw_subs": ["車用連接器", "車用功率元件"]},
    "RIVN":  {"name": "Rivian",    "tw_subs": ["車用連接器"]},
    "F":     {"name": "福特",      "tw_subs": ["汽車AM售後"]},
    "GM":    {"name": "通用汽車",  "tw_subs": ["汽車AM售後"]},

    # ─── 重電 / 工業 ───
    "ETN":   {"name": "Eaton",     "tw_subs": ["重電四雄", "中低壓配電盤", "伺服器電源"]},
    "EMR":   {"name": "Emerson",   "tw_subs": ["伺服器電源", "中低壓配電盤"]},
    "GE":    {"name": "GE 航太",   "tw_subs": ["重電四雄", "超高壓變壓器"]},

    # ─── 軟體 / SaaS ───
    "CRM":   {"name": "Salesforce","tw_subs": ["工業電腦_IPC"]},
    "ADBE":  {"name": "Adobe",     "tw_subs": []},

    # ─── 大盤指標 ───
    "^GSPC": {"name": "S&P 500",   "tw_subs": [], "is_index": True},
    "^IXIC": {"name": "那斯達克",  "tw_subs": [], "is_index": True},
    "^SOX":  {"name": "費城半導體","tw_subs": ["晶圓代工_先進製程", "矽智財_ASIC"], "is_index": True},
    "^DJI":  {"name": "道瓊",      "tw_subs": [], "is_index": True},
}


def fetch_us_quotes():
    """抓 US 股票最新收盤 + 日漲跌幅"""
    import yfinance as yf
    results = {}
    print(f"📡 抓美股 {len(US_TO_TW)} 個代號...")
    for ticker, info in US_TO_TW.items():
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="5d", auto_adjust=False)
            if hist is None or len(hist) < 2:
                continue
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            chg_pct = (close / prev - 1) * 100
            results[ticker] = {
                "name": info["name"],
                "close": round(close, 2),
                "chg_pct": round(chg_pct, 2),
                "date": hist.index[-1].strftime("%Y-%m-%d"),
                "tw_subs": info.get("tw_subs", []),
                "is_index": info.get("is_index", False),
            }
        except Exception as e:
            continue
    print(f"   ✓ 取得 {len(results)} 個")
    return results


def aggregate_to_tw_sectors(us_quotes):
    """把美股漲跌聚合到台股子族群"""
    sector_signals = {}
    for ticker, q in us_quotes.items():
        for sub in q.get("tw_subs", []):
            sector_signals.setdefault(sub, []).append({
                "us_ticker": ticker,
                "us_name": q["name"],
                "chg_pct": q["chg_pct"],
            })
    # 計算每族群的「美股訊號分數」
    result = {}
    for sub, signals in sector_signals.items():
        chgs = [s["chg_pct"] for s in signals]
        avg = statistics.mean(chgs)
        median = statistics.median(chgs)
        strongest = max(signals, key=lambda x: x["chg_pct"])
        weakest = min(signals, key=lambda x: x["chg_pct"])
        # 強度評等
        if avg >= 3:    tag, color = "🚀 強漲", "#3fb950"
        elif avg >= 1:  tag, color = "📈 漲", "#56d364"
        elif avg >= -1: tag, color = "➡️ 平", "#8b949e"
        elif avg >= -3: tag, color = "📉 跌", "#f0a500"
        else:           tag, color = "🔻 重挫", "#f85149"
        result[sub] = {
            "subsector": sub,
            "alias": SUBSECTORS.get(sub, {}).get("alias", sub),
            "icon":  SUBSECTORS.get(sub, {}).get("icon", "📊"),
            "us_signals": signals,
            "us_avg_chg": round(avg, 2),
            "us_median_chg": round(median, 2),
            "strongest": strongest,
            "weakest": weakest,
            "tag": tag, "color": color,
            "tw_picks": list(SUBSECTORS.get(sub, {}).get("key_stocks", {}).items())[:3],
        }
    # 排序：依美股平均漲幅
    ranked = sorted(result.values(), key=lambda x: -x["us_avg_chg"])
    return ranked


def overall_market_view(us_quotes):
    """大盤整體判讀"""
    indices = {k: v for k, v in us_quotes.items() if v.get("is_index")}
    if not indices:
        return {"verdict": "資料不足", "color": "#8b949e", "advice": "—"}
    sox = indices.get("^SOX", {}).get("chg_pct", 0)
    ixic = indices.get("^IXIC", {}).get("chg_pct", 0)
    gspc = indices.get("^GSPC", {}).get("chg_pct", 0)
    avg_idx = (sox + ixic + gspc) / 3

    if avg_idx >= 2 and sox >= 2:
        return {
            "verdict": "🚀 開高機率大 + 半導體領漲",
            "color": "#3fb950",
            "advice": "今日開盤可能跳空向上，留意 9:30 量能。若量增續攻 → 持股續抱；量縮反高 → 警覺出貨"
        }
    if avg_idx >= 1:
        return {
            "verdict": "📈 開高 + 多頭氛圍",
            "color": "#56d364",
            "advice": "盤前期貨應為正價差。優先選漲幅最大的美股族群對應台股"
        }
    if avg_idx >= -1:
        return {
            "verdict": "➡️ 開平整理為主",
            "color": "#8b949e",
            "advice": "美股無方向，台股需自身題材帶動。觀察強勢族群是否仍維持"
        }
    if avg_idx >= -2:
        return {
            "verdict": "📉 開低、有壓力",
            "color": "#f0a500",
            "advice": "持股建議：強勢股先續抱、弱勢股嚴設停損。空手不急進"
        }
    return {
        "verdict": "🔻 重挫、留意國安基金訊號",
        "color": "#f85149",
        "advice": "開盤可能殺低、注意是否出現 V 轉。除非看到強勢族群逆勢拉抬，否則高檔減碼"
    }


def format_telegram_message(us_quotes, sector_ranking, market_view):
    """格式化 Telegram HTML 訊息"""
    today = datetime.today().strftime("%Y-%m-%d %A")
    indices = {k: v for k, v in us_quotes.items() if v.get("is_index")}
    lines = [
        f"🌅 <b>美股盤後 → 台股盤前預估</b>",
        f"📅 {today}",
        "",
        f"📋 <b>大盤判讀：{market_view['verdict']}</b>",
        f"<i>{market_view['advice']}</i>",
        "",
        "📊 <b>美股四大指數</b>",
    ]
    for ticker in ["^GSPC", "^IXIC", "^SOX", "^DJI"]:
        if ticker in indices:
            q = indices[ticker]
            arrow = "📈" if q["chg_pct"] >= 0 else "📉"
            lines.append(f"  {arrow} {q['name']}：{q['chg_pct']:+.2f}%")
    lines.append("")

    # 強勢族群（前 5）
    lines.append("🚀 <b>美股強漲族群（台股今日可能跟漲）</b>")
    for s in sector_ranking[:5]:
        if s["us_avg_chg"] < 0.5: break
        picks = "、".join(f"{sid} {name}" for sid, name in s["tw_picks"][:3])
        lines.append(
            f"  {s['tag']} {s['icon']} <b>{s['alias']}</b>"
            f"\n     美股均漲 {s['us_avg_chg']:+.2f}%（最強：{s['strongest']['us_name']} {s['strongest']['chg_pct']:+.2f}%）"
            f"\n     🇹🇼 台股對應：{picks}"
        )

    lines.append("")
    lines.append("🔻 <b>美股弱勢族群（台股今日可能拉回）</b>")
    weak = [s for s in sector_ranking if s["us_avg_chg"] < -1]
    if weak:
        for s in weak[:3]:
            picks = "、".join(f"{sid} {name}" for sid, name in s["tw_picks"][:3])
            lines.append(
                f"  {s['tag']} {s['icon']} <b>{s['alias']}</b>"
                f"\n     美股均跌 {s['us_avg_chg']:+.2f}%（最弱：{s['weakest']['us_name']} {s['weakest']['chg_pct']:+.2f}%）"
                f"\n     🇹🇼 台股對應：{picks}"
            )
    else:
        lines.append("  （無顯著弱勢族群）")

    lines.append("")
    lines.append("💡 <b>實戰建議</b>")
    if market_view["verdict"].startswith("🚀") or market_view["verdict"].startswith("📈"):
        if sector_ranking and sector_ranking[0]["us_avg_chg"] >= 2:
            top = sector_ranking[0]
            lines.append(f"  ① 開盤鎖定 <b>{top['icon']} {top['alias']}</b>（美股 {top['us_avg_chg']:+.2f}%）")
            lines.append(f"     建議標的：{', '.join(sid for sid,_ in top['tw_picks'])}")
        lines.append("  ② 量增續攻 → 抱緊；量縮反高 → 警覺")
        lines.append("  ③ 配 -3% 停損 / +10% 停利")
    else:
        lines.append("  ① 觀察 9:30 開盤量能（量增反彈才是真好）")
        lines.append("  ② 強勢族群續抱、弱勢族群嚴設停損")
        lines.append("  ③ 空手者不急進，等台股自身強勢族群表態")

    lines.append("")
    lines.append("🔗 配合 V42 飆股圖表 + 子族群輪動排行雙重確認")

    return "\n".join(lines)


def push_telegram(msg):
    tok = os.environ.get("STOCK_TG_TOKEN", "")
    chat = os.environ.get("STOCK_TG_CHAT", "")
    # ★ 額外收件人（多個 chat_id 用逗號分隔）
    extra = os.environ.get("STOCK_TG_CHAT_EXTRA", "")
    if not tok or not chat:
        try:
            import re
            ap = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_agent.py")
            txt = open(ap, encoding="utf-8").read()
            if not tok:
                m = re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', txt)
                if m: tok = m.group(1)
            if not chat:
                m = re.search(r'TG_CHAT\s*=\s*"([^"]+)"', txt)
                if m: chat = m.group(1)
        except Exception:
            pass
    if not tok or not chat:
        print("⚠️ Telegram 未設定，僅輸出本機")
        return False

    # 組所有收件人
    chats = [chat]
    if extra:
        for c in extra.split(","):
            c = c.strip()
            if c and c not in chats:
                chats.append(c)

    import requests
    ok_count = 0
    for c in chats:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": c, "text": msg, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
            print(f"📨 Telegram 推送 chat={c}: {r.status_code}")
            if r.status_code == 200:
                ok_count += 1
        except Exception as e:
            print(f"⚠️ Telegram chat={c} 失敗：{e}")
    return ok_count == len(chats)


def main():
    print("=" * 60)
    print(f"🌅 美股盤後 → 台股盤前預估")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    us_quotes = fetch_us_quotes()
    if not us_quotes:
        print("❌ 無法取得美股報價")
        return
    sector_ranking = aggregate_to_tw_sectors(us_quotes)
    market_view = overall_market_view(us_quotes)
    print()
    print(f"大盤判讀：{market_view['verdict']}")
    print(f"建議：{market_view['advice']}")
    print()
    print(f"📊 強勢族群 Top 5：")
    for s in sector_ranking[:5]:
        if s["us_avg_chg"] < 0: break
        print(f"  {s['tag']} {s['icon']} {s['alias']}：均漲 {s['us_avg_chg']:+.2f}%")
        print(f"     台股代表：{', '.join(sid for sid,_ in s['tw_picks'])}")
    print(f"\n🔻 弱勢族群（< -1%）：")
    weak = [s for s in sector_ranking if s["us_avg_chg"] < -1]
    if weak:
        for s in weak[:3]:
            print(f"  {s['tag']} {s['icon']} {s['alias']}：均跌 {s['us_avg_chg']:+.2f}%")
    else:
        print("  （無）")

    # 存報告
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    out = {
        "date": datetime.today().strftime("%Y-%m-%d"),
        "us_quotes": us_quotes,
        "sector_ranking": sector_ranking,
        "market_view": market_view,
    }
    with open(os.path.join(log_dir, f"us_premarket_{datetime.today().strftime('%Y%m%d')}.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 推送 Telegram
    msg = format_telegram_message(us_quotes, sector_ranking, market_view)
    print()
    print(msg)
    print()
    push_telegram(msg)


if __name__ == "__main__":
    main()
