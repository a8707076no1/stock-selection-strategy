"""
新聞情緒分析 + 重點事件抽取
從近 7 天的新聞列表，產生「消息面建議」給每支持股使用。
"""
import re

# ── 關鍵字字典 ─────────────────────────────────

# 強烈利多
STRONG_POSITIVE = [
    "漲停", "亮燈漲停", "急拉漲停", "創新高", "歷史新高", "同期新高",
    "翻倍", "暴增", "飆增", "創高", "破紀錄", "強勢", "強力買進",
    "目標價調升", "獲利倍增", "獲利翻倍", "突破前高",
]

# 一般利多
POSITIVE = [
    "年增", "月增", "成長", "看多", "看好", "受惠", "受益", "利多",
    "上漲", "上揚", "走高", "彈升", "進駐", "獲利",
    "新訂單", "訂單暢旺", "在手訂單", "需求強勁",
    "外資買超", "投信買超", "法人買超", "買超",
    "推升", "拉抬", "上修", "增持", "EPS創高",
    "Q1獲利", "Q2獲利", "Q3獲利", "Q4獲利",
    "首季獲利", "次季獲利", "三季獲利", "全年獲利",
]

# 強烈利空
STRONG_NEGATIVE = [
    "跌停", "亮燈跌停", "崩跌", "重挫", "血崩", "崩盤", "下修",
    "目標價調降", "獲利衰退", "年衰退", "巨虧", "失血",
    "套牢", "下車", "警示股", "注意股", "處置股", "下市",
]

# 一般利空
NEGATIVE = [
    "下跌", "下殺", "跌破", "走低", "新低", "歷史新低",
    "減少", "衰退", "下滑", "減幅", "收斂",
    "外資賣超", "投信賣超", "法人賣超", "賣超",
    "減碼", "看空", "失利", "虧損", "出貨",
    "風險", "警告", "下調",
]

# 中性 / 警示
NEUTRAL_WARNING = [
    "盤整", "拉回", "回檔", "震盪", "持平", "區間",
    "私募", "現金增資", "減資", "停牌", "停止交易",
    "鈍化", "高檔", "過熱", "區間整理",
]


def _classify_one(title):
    """單則新聞分類，回傳 ('strong_pos'/'pos'/'strong_neg'/'neg'/'warning'/'neutral')"""
    for k in STRONG_POSITIVE:
        if k in title: return "strong_pos"
    for k in STRONG_NEGATIVE:
        if k in title: return "strong_neg"
    for k in POSITIVE:
        if k in title: return "pos"
    for k in NEGATIVE:
        if k in title: return "neg"
    for k in NEUTRAL_WARNING:
        if k in title: return "warning"
    return "neutral"


def _extract_events(news_list):
    """從新聞抽取結構化事件"""
    events = []
    seen = set()

    for n in news_list:
        t = n.get("t", "")
        d = n.get("d", "")

        # 1) 月營收公告
        m = re.search(r"([0-9]+月)\s*營收\s*([\d.,]+)\s*([億萬])\s*元?[，,].*?(年增|月增|年衰退|月衰退)\s*([\d.]+)\s*%?", t)
        if m:
            ev = f"📊 {m.group(1)}營收 {m.group(2)}{m.group(3)} {m.group(4)} {m.group(5)}%"
            if ev not in seen: events.append((ev, d, "pos" if "增" in m.group(4) else "neg")); seen.add(ev)
            continue
        m = re.search(r"營收\s*([\d.,]+)\s*([億萬])\s*元?.*?年增\s*([\d.]+)\s*%?", t)
        if m:
            ev = f"📊 月營收 {m.group(1)}{m.group(2)}元 年增 {m.group(3)}%"
            if ev not in seen: events.append((ev, d, "pos")); seen.add(ev)
            continue

        # 2) 財報公告
        m = re.search(r"(Q[1-4]|首季|次季|第[一二三四]季|全年)\s*(獲利|稅後純益|稅後淨利|EPS|每股純益).*?(年增|增長|暴增|翻倍|倍增|衰退|減少)\s*([\d.]+)\s*[%倍]?", t)
        if m:
            ev = f"💰 {m.group(1)}{m.group(2)} {m.group(3)} {m.group(4)}"
            if ev not in seen: events.append((ev, d, "pos" if any(k in m.group(3) for k in ["增","倍","翻"]) else "neg")); seen.add(ev)
            continue
        m = re.search(r"EPS\s*([0-9.]+)\s*元?", t)
        if m:
            ev = f"💰 EPS {m.group(1)} 元"
            if ev not in seen: events.append((ev, d, "pos")); seen.add(ev)
            continue

        # 3) 券商目標價/評等
        m = re.search(r"目標價(?:調(升|降)至|為|預估目標價為)?\s*([0-9.]+)\s*元", t)
        if m:
            arrow = "↑" if (m.group(1) == "升") else ("↓" if m.group(1) == "降" else "")
            ev = f"🎯 券商目標價 {arrow}{m.group(2)} 元"
            if ev not in seen: events.append((ev, d, "pos" if m.group(1) != "降" else "neg")); seen.add(ev)
            continue
        m = re.search(r"評等[為「]?(強力買進|買進|看多|增持|持有|減碼|看空|賣出|強烈賣出)", t)
        if m:
            cls = "pos" if m.group(1) in ("強力買進","買進","看多","增持") else \
                  ("neg" if m.group(1) in ("減碼","看空","賣出","強烈賣出") else "neutral")
            ev = f"🏛️ 評等：{m.group(1)}"
            if ev not in seen: events.append((ev, d, cls)); seen.add(ev)
            continue

        # 4) 漲跌停
        if "漲停" in t:
            ev = f"🚀 出現漲停"
            if ev not in seen: events.append((ev, d, "strong_pos")); seen.add(ev)
            continue
        if "跌停" in t:
            ev = f"📉 出現跌停"
            if ev not in seen: events.append((ev, d, "strong_neg")); seen.add(ev)
            continue

        # 5) 訂單 / 私募 / 重大事件
        if any(k in t for k in ["新訂單", "訂單暢旺", "在手訂單", "訂單衝破", "訂單破"]):
            ev = "🤝 訂單利多"
            if ev not in seen: events.append((ev, d, "pos")); seen.add(ev)
            continue
        if "私募" in t and ("普通股" in t or "現增" in t or "辦理" in t):
            ev = "💼 私募 / 現金增資（稀釋警覺）"
            if ev not in seen: events.append((ev, d, "warning")); seen.add(ev)
            continue
        if "減資" in t:
            ev = "💼 減資"
            if ev not in seen: events.append((ev, d, "warning")); seen.add(ev)
            continue
        if any(k in t for k in ["處置股", "注意股", "警示股"]):
            ev = "⚠️ 列警示／注意股"
            if ev not in seen: events.append((ev, d, "warning")); seen.add(ev)
            continue

    return events[:6]   # 最多取 6 條最具代表性


def _build_recommendation(score, pos_n, neg_n, warn_n, events):
    """根據分數與事件構造一句建議"""
    has_strong_pos = any(e[2] == "strong_pos" for e in events)
    has_strong_neg = any(e[2] == "strong_neg" for e in events)
    has_warning   = any(e[2] == "warning"   for e in events)
    has_revenue   = any("📊" in e[0] for e in events)
    has_earnings  = any("💰" in e[0] for e in events)

    parts = []
    # 趨勢評斷
    if score >= 0.5:
        parts.append("近 7 天消息面強勁偏多")
    elif score >= 0.2:
        parts.append("近 7 天消息面整體正面")
    elif score >= -0.1:
        parts.append("近 7 天消息面中性")
    elif score >= -0.3:
        parts.append("近 7 天消息面轉弱")
    else:
        parts.append("近 7 天消息面顯著偏空")

    # 重點事件描述
    bullets = []
    if has_revenue:  bullets.append("月營收公告利多")
    if has_earnings: bullets.append("財報亮眼")
    if has_strong_pos: bullets.append("出現漲停 / 創新高訊號")
    if has_strong_neg: bullets.append("出現跌停 / 重挫訊號")
    if has_warning:    bullets.append("有警示事件需注意")
    if bullets:
        parts.append("（" + "、".join(bullets) + "）")

    # 操作建議
    if score >= 0.5 and not has_strong_neg:
        action = "👉 建議：強勢題材延續，可加碼但避免追高，回測支撐再進"
        sug, color = "🟢 強烈偏多", "#3fb950"
    elif score >= 0.2:
        action = "👉 建議：基本面 / 題材正向，續抱為主，跌破近期低點再減碼"
        sug, color = "🟢 偏多", "#56d364"
    elif score >= -0.1:
        action = "👉 建議：消息面平淡，按技術面與既定策略操作"
        sug, color = "🟡 中性", "#f0c040"
    elif score >= -0.3:
        action = "👉 建議：消息面轉弱，先停利或減碼 1/3，等止跌訊號再加回"
        sug, color = "🟠 偏空警覺", "#f0a500"
    else:
        action = "👉 建議：利空訊號明確，建議出清或設嚴格停損，不要凹單"
        sug, color = "🔴 強烈偏空", "#f85149"

    if has_warning and score >= 0:
        action += "（注意私募/現增稀釋風險）"

    return {
        "summary":  "；".join(parts) + "。",
        "action":   action,
        "label":    sug,
        "color":    color,
    }


def analyze_news_sentiment(news_list):
    """主入口：對單支股票的 7 天新聞做情緒分析。
    回傳 dict（若 news_list 為空則回傳 None）。
    """
    if not news_list:
        return None

    # 統計
    counts = {"strong_pos":0, "pos":0, "strong_neg":0, "neg":0, "warning":0, "neutral":0}
    for n in news_list:
        c = _classify_one(n.get("t",""))
        counts[c] = counts.get(c, 0) + 1

    pos_n  = counts["strong_pos"] * 2 + counts["pos"]
    neg_n  = counts["strong_neg"] * 2 + counts["neg"]
    warn_n = counts["warning"]
    total  = len(news_list)

    score = (pos_n - neg_n) / max(1, pos_n + neg_n + warn_n)
    score = round(score, 2)

    events = _extract_events(news_list)
    rec = _build_recommendation(score, pos_n, neg_n, warn_n, events)

    return {
        "score":          score,
        "label":          rec["label"],
        "color":          rec["color"],
        "summary":        rec["summary"],
        "action":         rec["action"],
        "events":         [e[0] for e in events],
        "events_dated":   [{"text": e[0], "date": e[1], "type": e[2]} for e in events],
        "counts":         {
            "total": total,
            "positive": counts["strong_pos"] + counts["pos"],
            "negative": counts["strong_neg"] + counts["neg"],
            "warning":  counts["warning"],
            "neutral":  counts["neutral"],
        },
    }


if __name__ == "__main__":
    sample = [
        {"t":"閎康財報｜Q1獲利飆近3倍、矽光子業務暴增85%","d":"2026-05-10"},
        {"t":"【2026/04月營收公告】閎康(3587) 四月營收5.41億元，年增22.53%","d":"2026-05-08"},
        {"t":"閎康董事會決議辦理私募普通股案，上限500萬股","d":"2026-05-08"},
    ]
    print(analyze_news_sentiment(sample))
