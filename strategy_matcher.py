"""
飆股在線等 61 集策略匹配器
依朱家泓老師教的技術分析策略，對單支股票偵測符合的策略訊號。
回傳 list of dict，每筆含 ep / name / signal / type / strength
"""
import numpy as np
import pandas as pd

# 集數對照（給 UI 顯示）
EPISODE_INDEX = {
    1:  "KD 三招",
    3:  "MACD",
    4:  "均線多頭排列",
    11: "N 字底",
    12: "頭肩底",
    13: "跳空缺口",
    14: "圓弧底",
    16: "趨勢切線",
    17: "量價關係",
    19: "K 棒組合（母子/吞噬）",
    20: "葛蘭碧買點",
    21: "葛蘭碧賣點",
    22: "影線判讀",
    23: "晨星/夜星",
    26: "致命黑 K 組合",
    27: "M 頭做頭",
    28: "三角型態突破",
    30: "回後買上漲",
    31: "W 底",
    32: "爆大量",
    33: "飄旗型態",
    34: "落底股 3 訊號",
    35: "多空三明治",
    36: "箱型突破",
    42: "MACD 背離",
    47: "RSI 背離",
    49: "假突破警示",
    52: "乖離率超漲超跌",
    53: "布林通道",
    57: "V 型反轉",
    58: "守株待兔買點",
    59: "攻擊量 vs 出貨量",
}

# ── 訊號類型對應的顏色（給 UI）
TYPE_COLOR = {
    "buy":     "#3fb950",   # 綠（買進）
    "sell":    "#f85149",   # 紅（賣出/做空）
    "warning": "#f0a500",   # 橘（警示）
    "info":    "#58a6ff",   # 藍（資訊）
}


def _safe(arr, i, default=np.nan):
    try:
        v = arr[i]
        return v if not (isinstance(v, float) and np.isnan(v)) else default
    except (IndexError, KeyError):
        return default


def _find_recent_extremes(values, lookback=20, window=2):
    """找最近 lookback 根的局部高低點"""
    n = len(values)
    start = max(0, n - lookback)
    peaks, valleys = [], []
    for i in range(start + window, n - window):
        if all(values[i] >= values[i - j] for j in range(1, window + 1)) and \
           all(values[i] >= values[i + j] for j in range(1, window + 1)):
            peaks.append((i, values[i]))
        if all(values[i] <= values[i - j] for j in range(1, window + 1)) and \
           all(values[i] <= values[i + j] for j in range(1, window + 1)):
            valleys.append((i, values[i]))
    return peaks, valleys


def match_strategies(df, pattern_info=None):
    """
    對單支股票檢測符合哪些飆股在線等策略。
    df: 含 open/high/low/close/volume 欄位的 DataFrame，至少 30 根
    pattern_info: 來自 pattern_detector 的 {"name", "pattern_en", "cat"} dict
    回傳：list of {"ep", "name", "signal", "type"}
    """
    matches = []
    if df is None or len(df) < 25:
        return matches

    closes = df["close"].values.astype(float)
    opens  = df["open"].values.astype(float)
    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    vols   = df["volume"].values.astype(float)

    # 均線
    ma5  = pd.Series(closes).rolling(5).mean().values
    ma10 = pd.Series(closes).rolling(10).mean().values
    ma20 = pd.Series(closes).rolling(20).mean().values
    ma60 = pd.Series(closes).rolling(60).mean().values
    vma5  = pd.Series(vols).rolling(5).mean().values
    vma20 = pd.Series(vols).rolling(20).mean().values

    # KD（9, 3, 3）
    s_low9  = pd.Series(lows).rolling(9).min()
    s_high9 = pd.Series(highs).rolling(9).max()
    rsv = ((pd.Series(closes) - s_low9) / (s_high9 - s_low9) * 100).fillna(50)
    k_arr = rsv.ewm(com=2, adjust=False).mean().values
    d_arr = pd.Series(k_arr).ewm(com=2, adjust=False).mean().values

    # MACD
    ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().values
    dif   = ema12 - ema26
    macd  = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    osc   = dif - macd

    # RSI（14）
    diff = pd.Series(closes).diff()
    gain = diff.where(diff > 0, 0).rolling(14).mean()
    loss = (-diff.where(diff < 0, 0)).rolling(14).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(50).values

    # 布林通道
    boll_mid = ma20
    boll_std = pd.Series(closes).rolling(20).std().values
    boll_up  = boll_mid + 2 * boll_std
    boll_lo  = boll_mid - 2 * boll_std

    # 乖離率（月線）
    bias_ma20 = (closes[-1] - ma20[-1]) / ma20[-1] * 100 if not np.isnan(ma20[-1]) else 0

    n = len(closes)
    L  = n - 1   # 最後一根（正向索引）
    L1 = n - 2   # 前一根
    last_c, prev_c = closes[L], closes[L1]
    last_o = opens[L]
    last_h, last_l = highs[L], lows[L]
    last_v = vols[L]

    # ── Ep 1：KD 三招（搭配趨勢）─────────
    if not np.isnan(k_arr[L1]) and not np.isnan(d_arr[L1]):
        if k_arr[L1] < d_arr[L1] and k_arr[L] > d_arr[L]:
            if not np.isnan(ma20[L]) and last_c > ma20[L]:
                matches.append({"ep": 1, "name": "KD 三招", "signal": "多頭中黃金交叉買進", "type": "buy"})
            elif not np.isnan(ma20[L]) and last_c < ma20[L]:
                matches.append({"ep": 1, "name": "KD 三招", "signal": "空頭反彈訊號（觀察）", "type": "info"})
        if k_arr[L1] > d_arr[L1] and k_arr[L] < d_arr[L]:
            if not np.isnan(ma20[L]) and last_c < ma20[L]:
                matches.append({"ep": 1, "name": "KD 三招", "signal": "空頭中死亡交叉做空", "type": "sell"})
            elif not np.isnan(ma20[L]) and last_c > ma20[L]:
                matches.append({"ep": 1, "name": "KD 三招", "signal": "多頭回檔（參考）", "type": "info"})

    # ── Ep 3：MACD 黃金/死亡交叉 ─────────
    if not np.isnan(dif[L1]) and not np.isnan(macd[L1]):
        if dif[L1] < macd[L1] and dif[L] > macd[L]:
            tag = "（紅柱啟動）" if osc[L] > 0 else ""
            matches.append({"ep": 3, "name": "MACD", "signal": f"黃金交叉買進{tag}", "type": "buy"})
        if dif[L1] > macd[L1] and dif[L] < macd[L]:
            matches.append({"ep": 3, "name": "MACD", "signal": "死亡交叉賣出", "type": "sell"})

    # ── Ep 4：均線多頭排列／空頭排列 ──────
    if not (np.isnan(ma5[L]) or np.isnan(ma10[L]) or np.isnan(ma20[L])):
        if ma5[L] > ma10[L] > ma20[L] and ma20[L] > ma20[L-3]:
            matches.append({"ep": 4, "name": "均線多頭排列", "signal": "三線多排（5>10>20）", "type": "buy"})
        elif ma5[L] < ma10[L] < ma20[L] and ma20[L] < ma20[L-3]:
            matches.append({"ep": 4, "name": "均線空頭排列", "signal": "三線空排（5<10<20）", "type": "sell"})

    # ── Ep 11/12/14/27/31：型態學（從 pattern_detector）─────
    if pattern_info:
        pen = (pattern_info.get("pattern_en") or "") if isinstance(pattern_info, dict) else ""
        pcat = (pattern_info.get("cat") or "") if isinstance(pattern_info, dict) else ""
        pname = (pattern_info.get("name") or pattern_info.get("pattern_name") or "") if isinstance(pattern_info, dict) else ""
        if pen.startswith("w_bottom"):
            matches.append({"ep": 31, "name": "W 底", "signal": pname, "type": "buy"})
        elif pen == "head_shoulder_bottom":
            matches.append({"ep": 12, "name": "頭肩底", "signal": pname, "type": "buy"})
        elif pen == "triple_bottom":
            matches.append({"ep": 11, "name": "N 字底/三重底", "signal": pname, "type": "buy"})
        elif pen == "rounded_bottom" or "圓弧底" in pname:
            matches.append({"ep": 14, "name": "圓弧底", "signal": "95% 高勝率反轉型態", "type": "buy"})
        elif pen in ("m_top", "head_shoulder_top"):
            matches.append({"ep": 27, "name": "M 頭/頭肩頂", "signal": pname, "type": "sell"})
        elif pen == "ascending_triangle_bottom":
            matches.append({"ep": 28, "name": "上升三角", "signal": pname, "type": "buy"})

    # ── Ep 13：跳空缺口 ─────────────
    if last_l > highs[L1] * 1.005:  # 向上跳空 0.5% 以上
        matches.append({"ep": 13, "name": "跳空缺口", "signal": "向上跳空（多頭啟動）", "type": "buy"})
    elif last_h < lows[L1] * 0.995:
        matches.append({"ep": 13, "name": "跳空缺口", "signal": "向下跳空（空頭警示）", "type": "sell"})

    # ── Ep 17：量價關係 ─────────────
    if not np.isnan(vma5[L]):
        if last_c > prev_c and last_v > vma5[L] * 1.5:
            matches.append({"ep": 17, "name": "量價關係", "signal": "量增價漲（健康多頭）", "type": "buy"})
        elif last_c < prev_c and last_v > vma5[L] * 1.5:
            matches.append({"ep": 17, "name": "量價關係", "signal": "量增價跌（賣壓警示）", "type": "warning"})

    # ── Ep 19：K 棒組合（母子懷抱、吞噬）─────
    body_now  = abs(last_c - last_o)
    body_prev = abs(closes[L1] - opens[L1])
    high_now, low_now = max(last_o, last_c), min(last_o, last_c)
    high_pr, low_pr = max(opens[L1], closes[L1]), min(opens[L1], closes[L1])
    if body_now < body_prev * 0.6 and high_now <= high_pr and low_now >= low_pr:
        # 母子懷抱
        is_high = last_c > ma20[L] if not np.isnan(ma20[L]) else False
        matches.append({"ep": 19, "name": "母子懷抱", "signal": "高檔變盤警示" if is_high else "低檔變盤訊號", "type": "warning" if is_high else "info"})
    if body_now > body_prev * 1.0 and ((last_c > opens[L1] and last_o < closes[L1]) or (last_c < opens[L1] and last_o > closes[L1])):
        # 吞噬
        if last_c > last_o and last_c > opens[L1] and last_o < closes[L1] and closes[L1] < opens[L1]:
            matches.append({"ep": 19, "name": "紅 K 吞噬", "signal": "低檔反轉訊號", "type": "buy"})
        elif last_c < last_o and last_c < opens[L1] and last_o > closes[L1] and closes[L1] > opens[L1]:
            matches.append({"ep": 19, "name": "黑 K 吞噬", "signal": "高檔反轉警示", "type": "sell"})

    # ── Ep 20：葛蘭碧 4 個買點 ─────────
    if not np.isnan(ma20[L]) and last_c > ma20[L] and ma20[L] > ma20[L-3]:
        # 買點1：站上月線+月線翻揚
        if last_c < ma20[L] * 1.05 and any(closes[i] < ma20[i] for i in range(max(0, L-3), L) if not np.isnan(ma20[i])):
            matches.append({"ep": 20, "name": "葛蘭碧買點 1", "signal": "站上月線+月線翻揚", "type": "buy"})
        # 買點2：拉回月線支撐
        if min(lows[max(0,len(lows)-3):len(lows)]) <= ma20[L] * 1.02 and last_c > ma20[L]:
            matches.append({"ep": 20, "name": "葛蘭碧買點 2", "signal": "拉回月線不破支撐", "type": "buy"})

    # ── Ep 21：葛蘭碧賣點 ─────────────
    if not np.isnan(ma20[L]) and last_c < ma20[L] and ma20[L] < ma20[L-3]:
        if max(highs[max(0,len(highs)-3):len(highs)]) >= ma20[L] * 0.98 and last_c < ma20[L]:
            matches.append({"ep": 21, "name": "葛蘭碧賣點 2", "signal": "反彈月線壓回", "type": "sell"})

    # ── Ep 22：影線（避雷針/吊人線/鎚子）────
    upper_sh = last_h - high_now
    lower_sh = low_now - last_l
    full = last_h - last_l
    if full > 0:
        if upper_sh > body_now * 1.5 and upper_sh > full * 0.5:
            tag = "高檔避雷針（警示）" if (not np.isnan(ma20[L]) and last_c > ma20[L]) else "盤整變盤線"
            matches.append({"ep": 22, "name": "長上影線", "signal": tag, "type": "warning"})
        if lower_sh > body_now * 1.5 and lower_sh > full * 0.5:
            tag = "低檔鎚子線（買訊）" if (not np.isnan(ma20[L]) and last_c < ma20[L]) else "高檔吊人線"
            matches.append({"ep": 22, "name": "長下影線", "signal": tag, "type": "buy" if "鎚子" in tag else "warning"})

    # ── Ep 23：晨星/夜星 ─────────────
    if len(closes) >= 3:
        c0, c1, c2 = closes[L-2], closes[L-1], closes[L]
        o0, o1, o2 = opens[L-2], opens[L-1], opens[L]
        b0 = abs(c0 - o0); b1 = abs(c1 - o1); b2 = abs(c2 - o2)
        is_low = (not np.isnan(ma20[L])) and last_c < ma20[L] * 1.02
        is_high = (not np.isnan(ma20[L])) and last_c > ma20[L] * 0.98
        if c0 < o0 and b0 > b1 and c2 > o2 and b2 > b1 and c2 > (c0 + o0) / 2 and is_low:
            matches.append({"ep": 23, "name": "晨星", "signal": "低檔反轉做多", "type": "buy"})
        if c0 > o0 and b0 > b1 and c2 < o2 and b2 > b1 and c2 < (c0 + o0) / 2 and is_high:
            matches.append({"ep": 23, "name": "夜星", "signal": "高檔反轉做空", "type": "sell"})

    # ── Ep 26：致命黑 K（高檔黑 K 跌破前根 1/2 + 量增）─────
    if not np.isnan(ma20[L]) and last_c > ma20[L]:
        if last_c < last_o and last_c < (opens[L1] + closes[L1]) / 2 and not np.isnan(vma20[L]) and last_v > vma20[L] * 2:
            matches.append({"ep": 26, "name": "致命黑 K", "signal": "高檔爆量黑 K（出貨警示）", "type": "sell"})

    # ── Ep 28：三角型態突破（盤整突破）─────
    last10_high = max(highs[L-10:L])
    last10_low  = min(lows[L-10:L])
    box_height = (last10_high - last10_low) / last10_low if last10_low > 0 else 1
    if box_height < 0.08 and last_c > last10_high and not np.isnan(vma20[L]) and last_v > vma20[L] * 1.5:
        matches.append({"ep": 28, "name": "三角型態突破", "signal": "盤整突破上緣 + 量增", "type": "buy"})
    elif box_height < 0.08 and last_c < last10_low and not np.isnan(vma20[L]) and last_v > vma20[L] * 1.5:
        matches.append({"ep": 28, "name": "三角型態突破", "signal": "盤整跌破下緣 + 量增", "type": "sell"})

    # ── Ep 30：回後買上漲（多頭中回檔結束）─────
    if not np.isnan(ma20[L]) and last_c > ma20[L] and ma20[L] > ma20[L-3]:
        # 找近 5 天是否有黑 K 回檔，再出現紅 K 突破
        recent_blacks = sum(1 for i in range(L-4, L) if closes[i] < opens[i])
        if recent_blacks >= 2 and last_c > last_o and last_c > max(closes[L-3:L]):
            matches.append({"ep": 30, "name": "回後買上漲", "signal": "多頭回檔後紅 K 突破", "type": "buy"})

    # ── Ep 32：爆大量（≥ 20 日均量 3 倍）─────
    if not np.isnan(vma20[L]) and last_v > vma20[L] * 3:
        if last_c > last_o:
            tag = "高檔爆量（換手 / 出貨警覺）" if (not np.isnan(ma20[L]) and last_c > ma20[L] * 1.05) else "低檔爆量（主力進貨）"
            matches.append({"ep": 32, "name": "爆大量", "signal": tag, "type": "warning" if "出貨" in tag else "buy"})
        else:
            matches.append({"ep": 32, "name": "爆大量", "signal": "黑 K 爆量（出貨警示）", "type": "sell"})

    # ── Ep 34：落底股 3 訊號 ─────────────
    last5_low = min(lows[L-4:L+1])
    prev5_low = min(lows[L-9:L-4]) if L >= 9 else last5_low
    if last5_low >= prev5_low and last_v > vma5[L] * 2 and last_c > last_o and not np.isnan(ma5[L]) and last_c > ma5[L]:
        if not np.isnan(ma20[L]) and last_c < ma20[L]:
            matches.append({"ep": 34, "name": "落底股 3 訊號", "signal": "低檔爆量+不破低+站上 5 日線", "type": "buy"})

    # ── Ep 42：MACD 背離 ─────────────
    if len(dif) >= 30:
        # 找近 30 根 K 棒中，股價兩個高點對應的 MACD
        peaks_p, _ = _find_recent_extremes(closes, lookback=30, window=3)
        if len(peaks_p) >= 2:
            p1, p2 = peaks_p[-2], peaks_p[-1]
            if p2[1] > p1[1] and dif[p2[0]] < dif[p1[0]]:
                matches.append({"ep": 42, "name": "MACD 頂背離", "signal": "股價創高 + MACD 不創高", "type": "warning"})
        _, valleys_p = _find_recent_extremes(closes, lookback=30, window=3)
        if len(valleys_p) >= 2:
            v1, v2 = valleys_p[-2], valleys_p[-1]
            if v2[1] < v1[1] and dif[v2[0]] > dif[v1[0]]:
                matches.append({"ep": 42, "name": "MACD 底背離", "signal": "股價創低 + MACD 不創低", "type": "buy"})

    # ── Ep 47：RSI 背離 + 超買超賣 ─────
    if rsi[L] >= 70:
        matches.append({"ep": 47, "name": "RSI 超買", "signal": f"RSI {rsi[L]:.0f}（高檔警覺）", "type": "warning"})
    elif rsi[L] <= 30:
        matches.append({"ep": 47, "name": "RSI 超賣", "signal": f"RSI {rsi[L]:.0f}（低檔可承接）", "type": "buy"})

    # ── Ep 49：假突破警示（突破當天無量 / 長上影 / 隔天黑 K 跌破）─────
    if last_c > closes[L1] and last_c > max(closes[L-5:L1]):
        if not np.isnan(vma5[L]) and last_v < vma5[L]:
            matches.append({"ep": 49, "name": "假突破警示", "signal": "突破無量（假突破機率高）", "type": "warning"})
        if upper_sh > body_now and full > 0:
            matches.append({"ep": 49, "name": "假突破警示", "signal": "突破出現長上影", "type": "warning"})

    # ── Ep 52：乖離率 ─────────────
    if bias_ma20 >= 15:
        matches.append({"ep": 52, "name": "乖離率超漲", "signal": f"+{bias_ma20:.1f}%（拉橡皮筋警戒）", "type": "warning"})
    elif bias_ma20 >= 10:
        matches.append({"ep": 52, "name": "乖離率偏高", "signal": f"+{bias_ma20:.1f}%（注意回檔）", "type": "info"})
    elif bias_ma20 <= -15:
        matches.append({"ep": 52, "name": "乖離率超跌", "signal": f"{bias_ma20:.1f}%（可分批承接）", "type": "buy"})
    elif bias_ma20 <= -10:
        matches.append({"ep": 52, "name": "乖離率偏低", "signal": f"{bias_ma20:.1f}%（注意反彈）", "type": "info"})

    # ── Ep 53：布林通道 ─────────────
    if not np.isnan(boll_lo[L]) and not np.isnan(boll_up[L]):
        if last_c <= boll_lo[L] * 1.02 and last_c > prev_c:
            matches.append({"ep": 53, "name": "布林通道", "signal": "觸下軌反彈（買訊 1）", "type": "buy"})
        elif last_c >= boll_up[L] * 0.98:
            matches.append({"ep": 53, "name": "布林通道", "signal": "觸上軌（高檔警覺）", "type": "warning"})
        # 收斂後突破
        bw_now = boll_up[L] - boll_lo[L]
        bw_avg = np.nanmean(boll_up[L-10:L] - boll_lo[L-10:L])
        if bw_now < bw_avg * 0.7 and last_c > boll_up[L1]:
            matches.append({"ep": 53, "name": "布林通道", "signal": "收斂後突破上軌（買訊 3）", "type": "buy"})

    # ── Ep 57：V 型反轉搶反彈 ─────────
    # 過去 10 天跌幅 ≥ 15% + 今天紅 K 帶量站上前根高點
    last10_open = closes[L-10] if len(closes) >= 11 else closes[0]
    drop_pct = (last10_open - min(lows[L-10:L])) / last10_open * 100 if last10_open > 0 else 0
    if drop_pct >= 15 and last_c > last_o and last_c > highs[L1] and last_v > vma5[L] * 1.5:
        matches.append({"ep": 57, "name": "V 型反轉", "signal": "急跌後紅 K 帶量反轉", "type": "buy"})

    # ── Ep 58：守株待兔 4 買點（綜合判斷）────
    # 此處只標示「目前出現多個買點訊號齊全 ≥ 3 → 重倉候選」
    buy_count = sum(1 for m in matches if m["type"] == "buy")
    if buy_count >= 3:
        matches.append({"ep": 58, "name": "守株待兔（重倉候選）", "signal": f"齊備 {buy_count} 個買點訊號", "type": "buy"})

    # ── Ep 59：攻擊量 vs 出貨量 ─────────
    if not np.isnan(vma20[L]):
        if last_v > vma20[L] * 2 and last_c > last_o and last_c > max(closes[L-5:L1]):
            matches.append({"ep": 59, "name": "攻擊量", "signal": "突破關鍵位 + 量增", "type": "buy"})
        if last_v > vma20[L] * 3 and last_c < last_o and not np.isnan(ma20[L]) and last_c > ma20[L]:
            matches.append({"ep": 59, "name": "出貨量", "signal": "高檔爆量黑 K", "type": "sell"})

    # 去重（同集只保留首個）
    seen_eps = set()
    unique = []
    for m in matches:
        key = (m["ep"], m["name"])
        if key in seen_eps:
            continue
        seen_eps.add(key)
        unique.append(m)
    return unique


def summarize_action(matches):
    """根據匹配的策略產生「綜合操作建議」"""
    buys = [m for m in matches if m["type"] == "buy"]
    sells = [m for m in matches if m["type"] == "sell"]
    warnings = [m for m in matches if m["type"] == "warning"]
    score = len(buys) - len(sells) - 0.5 * len(warnings)
    if score >= 4:
        return {"action": "強力買進", "color": "#3fb950", "score": round(score, 1)}
    if score >= 2:
        return {"action": "逢低買進", "color": "#56d364", "score": round(score, 1)}
    if score >= 0.5:
        return {"action": "可進場觀察", "color": "#7ee787", "score": round(score, 1)}
    if score >= -0.5:
        return {"action": "中性觀望", "color": "#f0c040", "score": round(score, 1)}
    if score >= -2:
        return {"action": "減碼警覺", "color": "#f0a500", "score": round(score, 1)}
    return {"action": "建議出清", "color": "#f85149", "score": round(score, 1)}
