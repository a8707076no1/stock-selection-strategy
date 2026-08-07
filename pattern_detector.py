"""
36種K線型態識別模組
基於朱家泓36種型態 × 量價關係
"""
import numpy as np
import pandas as pd

def find_peaks_valleys(closes, window=3):
    """找出高點和低點"""
    peaks = []
    valleys = []
    n = len(closes)
    for i in range(window, n-window):
        if all(closes[i] >= closes[i-j] for j in range(1, window+1)) and \
           all(closes[i] >= closes[i+j] for j in range(1, window+1)):
            peaks.append((i, closes[i]))
        if all(closes[i] <= closes[i-j] for j in range(1, window+1)) and \
           all(closes[i] <= closes[i+j] for j in range(1, window+1)):
            valleys.append((i, closes[i]))
    return peaks, valleys

def vol_trend(vols, start, end):
    """量能趨勢：縮/增/持平"""
    if end <= start or end > len(vols): return "持平"
    v = vols[start:end]
    if len(v) < 2: return "持平"
    first_half = np.mean(v[:len(v)//2])
    second_half = np.mean(v[len(v)//2:])
    if second_half > first_half * 1.2: return "增"
    if second_half < first_half * 0.8: return "縮"
    return "持平"

def last_vol_vs_avg(vols, lookback=5, avg_window=20):
    """最近量能 vs 均量"""
    if len(vols) < avg_window: return 1.0
    avg = np.mean(vols[-avg_window:])
    recent = np.mean(vols[-lookback:])
    return recent / avg if avg > 0 else 1.0

def detect_pattern(df, n_bars=30):
    """
    主要型態識別函式
    回傳：{pattern_name, pattern_en, description, confidence, vol_price_relation, category}
    """
    if df is None or len(df) < 20:
        return None

    df = df.copy().tail(n_bars).reset_index(drop=True)
    df = df.tail(30).reset_index(drop=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
    n = len(df)
    if n < 15: return None

    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values
    vols   = df["volume"].values
    opens  = df["open"].values

    # 基本指標
    recent_high = np.max(highs[-10:])
    recent_low  = np.min(lows[-10:])
    full_high   = np.max(highs)
    full_low    = np.min(lows)
    ma5  = np.mean(closes[-5:])
    ma20 = np.mean(closes[-20:]) if n >= 20 else np.mean(closes)
    last_close  = closes[-1]
    last_vol    = vols[-1]
    avg_vol20   = np.mean(vols[-20:]) if n >= 20 else np.mean(vols)
    vol_ratio   = last_vol / avg_vol20 if avg_vol20 > 0 else 1

    # 找高低點
    peaks, valleys = find_peaks_valleys(closes, window=2)

    # 趨勢判斷（前半段）
    half = n // 2
    trend_slope = (np.mean(closes[half:]) - np.mean(closes[:half])) / np.mean(closes[:half]) if np.mean(closes[:half]) > 0 else 0

    # 量價關係
    price_change = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 else 0
    if price_change > 0.005:
        if vol_ratio >= 1.2: vp_rel = "價漲量增"
        elif vol_ratio <= 0.8: vp_rel = "價漲量縮"
        else: vp_rel = "價漲量平"
    elif price_change < -0.005:
        if vol_ratio >= 1.2: vp_rel = "價跌量增"
        elif vol_ratio <= 0.8: vp_rel = "價跌量縮"
        else: vp_rel = "價跌量平"
    else:
        if vol_ratio >= 1.2: vp_rel = "價平量增"
        elif vol_ratio <= 0.8: vp_rel = "價平量縮"
        else: vp_rel = "價平量平"

    results = []

    # ── 底部型態檢測 ──────────────────────────────────

    # 1. 頭肩底
    if len(valleys) >= 3 and trend_slope > -0.02:
        v3 = valleys[-3:]
        left_sh, head, right_sh = v3[0][1], v3[1][1], v3[2][1]
        if head < left_sh and head < right_sh and abs(left_sh - right_sh)/right_sh < 0.08:
            neckline = min(closes[v3[0][0]:v3[1][0]].max(), closes[v3[1][0]:v3[2][0]].max())
            neckline_hs = min(closes[v3[0][0]:v3[1][0]].max(), closes[v3[1][0]:v3[2][0]].max()) if v3[1][0]>v3[0][0] and v3[2][0]>v3[1][0] else closes[-1]
            hs_broken = closes[-1] > neckline_hs * 0.995
            conf = 75 + (10 if vol_ratio > 1.2 else 0)
            if hs_broken:
                results.append(("頭肩底✅", "head_shoulder_bottom", "三低點，中間最低，已突破頸線確認", conf+5, "底部反轉"))
            else:
                results.append(("頭肩底成形中⏳", "head_shoulder_bottom", "三低點結構，頸線尚未突破", conf-10, "底部反轉"))

    # 2. W底（雙平腳）- 底1底2都必須在最近15天（n=30中後半段）
    if len(valleys) >= 2:
        # 只用最近15天內的谷底（idx >= n-15）
        recent_v = [(i,p) for i,p in valleys if i >= n-15]
        # 底2必須在最近10天
        recent_v2 = [(i,p) for i,p in valleys if i >= n-10]
        if len(recent_v) >= 2 and len(recent_v2) >= 1:
            vi1, v1 = recent_v[-2]
            vi2, v2 = recent_v[-1]
            if vi2 >= n-10 and abs(v1 - v2) / max(v1, v2) < 0.06 and trend_slope > -0.05:
                if vi2 > vi1 and closes[vi1:vi2+1].size > 0:
                    mid_high = float(np.max(closes[vi1:vi2]))
                    neck_mid = float(np.max(closes[vi1:vi2+1]))
                else:
                    mid_high = neck_mid = float(closes[-1])
                if last_close > mid_high * 0.97:
                    conf = 80 + (10 if vol_ratio > 1.2 else 0)
                    neck_ok = closes[-1] > neck_mid * 0.995
                    if neck_ok:
                        results.append(("雙平腳底(W底)✅", "w_bottom", "兩相近低點，已突破頸線，確認反轉", conf+5, "底部反轉"))
                    else:
                        results.append(("W底成形中⏳", "w_bottom_forming", "兩相近低點，頸線尚未突破，持續觀察", conf-10, "底部反轉"))

    # 3. V形底
    mid = n // 2
    if lows.argmin() > n*0.3 and lows.argmin() < n*0.7:
        left_trend  = closes[lows.argmin()] - closes[0]
        right_trend = closes[-1] - closes[lows.argmin()]
        if left_trend < 0 and right_trend > abs(left_trend) * 0.7:
            conf = 65 + (15 if vol_ratio > 1.5 else 0)
            results.append(("V形底", "v_bottom", "快速下跌後急速反彈，呈現V字結構", conf, "底部反轉"))

    # 4. 圓弧底
    if n >= 30:
        third = n // 3
        seg1 = np.mean(closes[:third])
        seg2 = np.mean(closes[third:2*third])
        seg3 = np.mean(closes[2*third:])
        if seg2 < seg1 * 0.97 and seg2 < seg3 * 0.97 and seg3 > seg1 * 0.95:
            vol1 = np.mean(vols[:third])
            vol3 = np.mean(vols[2*third:])
            vol_ok = vol3 > vol1 * 0.9
            conf = 70 + (10 if vol_ok else 0)
            results.append(("圓弧底", "round_bottom", "底部呈平滑U形，右側逐步抬升", conf, "底部反轉"))

    # 5. 下降楔形（底部）
    if n >= 20 and trend_slope < 0:
        high_slope = (highs[-1] - highs[0]) / n
        low_slope  = (lows[-1] - lows[0]) / n
        if high_slope < 0 and low_slope < 0 and low_slope > high_slope:
            range_start = highs[0] - lows[0]
            range_end   = highs[-1] - lows[-1]
            if range_end < range_start * 0.7:
                conf = 72 + (12 if vol_ratio > 1.2 and price_change > 0 else 0)
                results.append(("下降楔形", "falling_wedge", "高低點同步下移但區間收斂，跌勢衰竭", conf, "底部反轉"))

    # 6. 三重底
    if len(valleys) >= 3:
        v3 = valleys[-3:]
        avg_v = np.mean([v[1] for v in v3])
        if all(abs(v[1] - avg_v) / avg_v < 0.04 for v in v3) and trend_slope > -0.03:
            neck_t = float(np.max(closes[valleys[-3][0]:valleys[-1][0]+1])) if valleys[-1][0]>valleys[-3][0] else closes[-1]
            t_broken = closes[-1] > neck_t * 0.995
            conf = 78 + (10 if vol_ratio > 1.2 else 0)
            if t_broken:
                results.append(("三重底✅", "triple_bottom", "三次測試支撐，已突破頸線確認反轉", conf+5, "底部反轉"))
            else:
                results.append(("三重底成形中⏳", "triple_bottom", "三次相近低點，頸線尚未突破", conf-10, "底部反轉"))

    # 7. 上升直角三角形底部
    if len(valleys) >= 2 and len(peaks) >= 2:
        recent_peaks = peaks[-3:]
        recent_valls = valleys[-3:]
        if len(recent_peaks) >= 2 and len(recent_valls) >= 2:
            peak_var = np.std([p[1] for p in recent_peaks]) / np.mean([p[1] for p in recent_peaks])
            valley_slope = (recent_valls[-1][1] - recent_valls[0][1]) / max(1, recent_valls[-1][0] - recent_valls[0][0])
            if peak_var < 0.03 and valley_slope > 0 and trend_slope > 0:
                conf = 73 + (12 if vol_ratio > 1.3 else 0)
                results.append(("上升直角三角形底", "ascending_triangle_bottom", "上方壓力水平，低點逐步墊高，多頭積累", conf, "底部反轉"))

    # ── 頭部型態檢測 ──────────────────────────────────

    # 8. 頭肩頂
    if len(peaks) >= 3 and trend_slope < 0.02:
        p3 = peaks[-3:]
        left_sh, head, right_sh = p3[0][1], p3[1][1], p3[2][1]
        if head > left_sh and head > right_sh and abs(left_sh - right_sh)/left_sh < 0.08:
            conf = 75 + (10 if vol_ratio > 1.2 and price_change < 0 else 0)
            results.append(("頭肩頂", "head_shoulder_top", "三高點，中間最高，右肩弱化後跌破頸線", conf, "頭部反轉"))

    # 9. M頭（雙重頂）
    if len(peaks) >= 2:
        p1, p2 = peaks[-2][1], peaks[-1][1]
        if abs(p1 - p2) / max(p1, p2) < 0.05 and trend_slope < 0.02:
            mid_low = np.min(closes[peaks[-2][0]:peaks[-1][0]])
            if last_close < mid_low * 1.03:
                conf = 80 + (10 if vol_ratio > 1.2 and price_change < 0 else 0)
                results.append(("雙重頂(M頭)", "m_top", "兩相近高點，跌破中間頸線確認轉弱", conf, "頭部反轉"))

    # 10. 圓弧頂
    if n >= 30 and trend_slope < 0:
        third = n // 3
        seg1 = np.mean(closes[:third])
        seg2 = np.mean(closes[third:2*third])
        seg3 = np.mean(closes[2*third:])
        if seg2 > seg1 * 1.02 and seg2 > seg3 * 1.02:
            conf = 68 + (10 if vol_ratio > 1.0 and price_change < 0 else 0)
            results.append(("圓弧頂", "round_top", "高位呈倒U形，動能逐步衰退", conf, "頭部反轉"))

    # 11. 上升楔形頂
    if n >= 20 and trend_slope > 0:
        high_slope = (highs[-1] - highs[0]) / n
        low_slope  = (lows[-1] - lows[0]) / n
        if high_slope > 0 and low_slope > 0 and high_slope < low_slope:
            range_start = highs[0] - lows[0]
            range_end   = highs[-1] - lows[-1]
            if range_end < range_start * 0.7 and last_close < ma5:
                conf = 70 + (10 if vol_ratio > 1.2 and price_change < 0 else 0)
                results.append(("上升楔形頂", "rising_wedge_top", "高低點同步上移但區間收斂，漲勢衰竭警訊", conf, "頭部反轉"))

    # ── 中繼續勢型態檢測 ──────────────────────────────

    # 12. 箱形整理
    if n >= 15:
        box_high = np.percentile(highs[-15:], 85)
        box_low  = np.percentile(lows[-15:], 15)
        box_range = (box_high - box_low) / box_low
        crossings = sum(1 for i in range(1, len(closes[-15:])) if
                       (closes[-15:][i-1] < (box_high+box_low)/2) != (closes[-15:][i] < (box_high+box_low)/2))
        if box_range < 0.12 and crossings >= 3:
            vt = vol_trend(vols, -15, len(vols))
            conf = 72 + (10 if last_close > box_high * 0.97 and vol_ratio > 1.2 else 0)
            results.append(("箱形整理", "box_consolidation", f"價格在明確上下邊界震盪，整理中（量{vt}）", conf, "中繼整理"))

    # 13. 旗形（快漲下降）
    if n >= 20:
        surge_start = max(0, n-20)
        pre_surge = closes[surge_start:surge_start+5]
        post_surge = closes[surge_start+5:]
        if len(pre_surge) >= 3 and len(post_surge) >= 5:
            surge = (pre_surge[-1] - pre_surge[0]) / pre_surge[0]
            flag_slope = (post_surge[-1] - post_surge[0]) / post_surge[0]
            if surge > 0.08 and -0.05 < flag_slope < 0:
                flag_vt = vol_trend(vols, surge_start+5, len(vols))
                conf = 74 + (12 if flag_vt == "縮" and vol_ratio > 1.2 else 0)
                results.append(("快漲下降旗形", "bull_flag", "急漲後小幅下降整理，量縮蓄積後續漲", conf, "中繼整理"))

    # 14. 收斂三角形
    if n >= 20:
        high_slope = np.polyfit(range(n), highs, 1)[0]
        low_slope  = np.polyfit(range(n), lows, 1)[0]
        if high_slope < 0 and low_slope > 0:
            conf = 70 + (10 if vol_ratio < 0.8 else 0)
            results.append(("收斂三角形", "symmetrical_triangle", "高點下降低點上升，波動收斂等待突破", conf, "中繼整理"))

    # 15. 高檔橫盤（一字形頭/底）
    range_pct = (full_high - full_low) / full_low if full_low > 0 else 1
    if range_pct < 0.08 and n >= 20:
        if trend_slope > 0.01 or last_close > ma20 * 1.02:
            conf = 60
            results.append(("高檔橫盤整理", "high_consolidation", "高位水平整理，等待突破方向確認", conf, "中繼整理"))
        elif trend_slope < -0.01:
            conf = 60
            results.append(("低檔橫盤整理", "low_consolidation", "低位水平整理，等待突破方向確認", conf, "底部反轉"))

    # 沒有符合型態 → 依趨勢給基本判斷
    if not results:
        if trend_slope > 0.05 and last_close > ma20:
            results.append(("上升趨勢", "uptrend", "股價持續上揚，多頭格局", 55, "中繼整理"))
        elif trend_slope < -0.05 and last_close < ma20:
            results.append(("下降趨勢", "downtrend", "股價持續下跌，空頭格局", 55, "頭部反轉"))
        else:
            results.append(("盤整整理", "sideways", "暫無明確型態，持續觀察", 40, "中繼整理"))

    # 取信心度最高的
    results.sort(key=lambda x: -x[3])
    best = results[0]

    return {
        "pattern_name": best[0],
        "pattern_en":   best[1],
        "description":  best[2],
        "confidence":   best[3],
        "category":     best[4],
        "vol_price":    vp_rel,
        "all_patterns": [(r[0], r[3]) for r in results[:3]],  # 前3候選
    }



def get_pattern_drawing(df, pattern_en, n_bars=30):
    """
    回傳在K線圖上要畫的線段、標記座標
    回傳格式：{
        lines: [{x1,y1,x2,y2,color,dash,label}],
        marks: [{x,y,color,text,shape}]  shape: triangle_up/down/circle/diamond
    }
    """
    if df is None or len(df) < 15: return {"lines":[], "marks":[]}
    df = df.copy().tail(n_bars).reset_index(drop=True)
    df = df.tail(30).reset_index(drop=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
    n = len(df)
    closes = df["close"].values
    highs  = df["high"].values
    lows   = df["low"].values

    lines = []
    marks = []

    # 找高低點
    peaks, valleys = find_peaks_valleys(closes, window=2)

    if pattern_en == "w_bottom" and len(valleys) >= 2:
        # 找最佳底1底2：底2必須在最近8天，底1在底2前5~18根
        best_pair = None
        best_score = 0
        for bi in range(len(valleys)):
            v2i, v2p = valleys[bi]
            if v2i < n - 8: continue          # 底2必須在最近8根
            for ai in range(bi):
                v1i, v1p = valleys[ai]
                dist = v2i - v1i
                if dist < 5 or dist > 18: continue  # 兩底相距5~18根
                similarity = 1 - abs(v1p-v2p)/max(v1p,v2p)
                if similarity < 0.90: continue
                score = similarity * 10 + (v2i / n)  # 越靠近右側越好
                if score > best_score:
                    best_score = score
                    best_pair = (v1i, v1p, v2i, v2p)

        if best_pair:
            v1i, v1p, v2i, v2p = best_pair
            # 頸線 = 兩谷之間最高收盤
            mid_closes = closes[v1i:v2i+1]
            neck_idx = v1i + int(np.argmax(mid_closes))
            neck = float(np.max(mid_closes))
            # 判斷是否已突破頸線
            neck_broken = closes[-1] > neck * 0.995
            # 頸線延伸到右側
            neck_x1 = max(0, v1i-2)
            neck_x2 = min(n-1, v2i+8)
            # 頸線顏色：已突破金色，未突破灰色
            neck_color = "#ffd700" if neck_broken else "#aaaaaa"
            neck_label = "頸線✅" if neck_broken else "頸線(未突破)"
            lines.append({"x1":neck_x1,"y1":neck,"x2":neck_x2,"y2":neck,
                          "color":neck_color,"dash":True,"label":neck_label,"width":2})
            # W形：用 lows 畫真實的 W 形狀
            # 左腳下探（從底1前幾根到底1低點）
            left_start = max(0, v1i-3)
            # 找底1到頸之間的實際最高點
            # 從底1後一根到底2前一根找中間峰頂（排除底1和底2本身）
            mid_start = v1i + 1
            mid_end   = v2i      # 不含底2
            if mid_end > mid_start:
                mid_section_highs = highs[mid_start:mid_end]
                mid_peak_offset = int(np.argmax(mid_section_highs))
                mid_peak_i = mid_start + mid_peak_offset
            else:
                mid_peak_i = mid_start  # 只有一根時取底1後一根
            mid_peak_p = float(highs[mid_peak_i])

            # W 形四段連線（用 lows/highs 貼合實際形狀）
            # 段1：底1 → 中間峰頂
            lines.append({"x1":v1i,"y1":v1p,"x2":mid_peak_i,"y2":mid_peak_p,
                          "color":"#00ff88","dash":False,"label":"","width":2})
            # 段2：中間峰頂 → 底2
            lines.append({"x1":mid_peak_i,"y1":mid_peak_p,"x2":v2i,"y2":v2p,
                          "color":"#00ff88","dash":False,"label":"","width":2})
            # 段3：底2 → 右側（向上突破）
            right_end = min(n-1, v2i+6)
            lines.append({"x1":v2i,"y1":v2p,"x2":right_end,"y2":float(highs[right_end]),
                          "color":"#00ff88","dash":False,"label":"","width":2})

            # 標出兩個低點（大圓點）
            marks.append({"x":v1i,"y":v1p,"color":"#00ff88","text":"底1","shape":"circle","size":8})
            marks.append({"x":v2i,"y":v2p,"color":"#00ff88","text":"底2","shape":"circle","size":8})
            # 標出中間峰頂
            marks.append({"x":mid_peak_i,"y":mid_peak_p,"color":"#ffd700","text":"頸","shape":"circle","size":5})

    elif pattern_en == "head_shoulder_bottom" and len(valleys) >= 3:
        v3 = valleys[-3:]
        li, lp = v3[0]  # 左肩
        hi, hp = v3[1]  # 頭
        ri, rp = v3[2]  # 右肩
        neck = float(np.mean([np.max(closes[li:hi+1]), np.max(closes[hi:ri+1])]))
        lines.append({"x1":li,"y1":neck,"x2":min(n-1,ri+5),"y2":neck,
                      "color":"#ffd700","dash":True,"label":"頸線"})
        marks.append({"x":li,"y":lp,"color":"#00ff88","text":"左肩","shape":"circle"})
        marks.append({"x":hi,"y":hp,"color":"#ff4488","text":"頭","shape":"diamond"})
        marks.append({"x":ri,"y":rp,"color":"#00ff88","text":"右肩","shape":"circle"})

    elif pattern_en == "m_top" and len(peaks) >= 2:
        p1i, p1p = peaks[-2]
        p2i, p2p = peaks[-1]
        neck = float(np.min(closes[p1i:p2i+1])) if p2i > p1i else float(np.min(closes[p1i:]))
        neck_x1, neck_x2 = max(0, p1i-3), min(n-1, p2i+5)
        lines.append({"x1":neck_x1,"y1":neck,"x2":neck_x2,"y2":neck,
                      "color":"#ff4444","dash":True,"label":"頸線"})
        marks.append({"x":p1i,"y":p1p,"color":"#ff4444","text":"頂1","shape":"circle"})
        marks.append({"x":p2i,"y":p2p,"color":"#ff4444","text":"頂2","shape":"circle"})

    elif pattern_en == "head_shoulder_top" and len(peaks) >= 3:
        p3 = peaks[-3:]
        li, lp = p3[0]
        hi, hp = p3[1]
        ri, rp = p3[2]
        neck = float(np.mean([np.min(closes[li:hi+1]), np.min(closes[hi:ri+1])]))
        lines.append({"x1":li,"y1":neck,"x2":min(n-1,ri+5),"y2":neck,
                      "color":"#ff4444","dash":True,"label":"頸線"})
        marks.append({"x":li,"y":lp,"color":"#ffaa00","text":"左肩","shape":"circle"})
        marks.append({"x":hi,"y":hp,"color":"#ff4444","text":"頭","shape":"diamond"})
        marks.append({"x":ri,"y":rp,"color":"#ffaa00","text":"右肩","shape":"circle"})

    elif pattern_en == "v_bottom":
        vi = int(np.argmin(lows))
        vp = float(lows[vi])
        marks.append({"x":vi,"y":vp,"color":"#00ff88","text":"V底","shape":"diamond"})
        # 畫左下和右上趨勢線
        if vi > 3 and vi < n-3:
            lines.append({"x1":0,"y1":float(closes[0]),"x2":vi,"y2":vp,
                          "color":"#ff6666","dash":False,"label":""})
            lines.append({"x1":vi,"y1":vp,"x2":n-1,"y2":float(closes[-1]),
                          "color":"#00ff88","dash":False,"label":""})

    elif pattern_en in ("falling_wedge", "rising_wedge_top"):
        # 畫上下趨勢線
        xs = list(range(n))
        high_fit = np.polyfit(xs, highs, 1)
        low_fit  = np.polyfit(xs, lows, 1)
        color = "#00ff88" if pattern_en == "falling_wedge" else "#ff4444"
        lines.append({"x1":0,"y1":float(np.polyval(high_fit,0)),"x2":n-1,"y2":float(np.polyval(high_fit,n-1)),
                      "color":color,"dash":False,"label":"上緣"})
        lines.append({"x1":0,"y1":float(np.polyval(low_fit,0)),"x2":n-1,"y2":float(np.polyval(low_fit,n-1)),
                      "color":color,"dash":False,"label":"下緣"})

    elif pattern_en == "symmetrical_triangle":
        xs = list(range(n))
        high_fit = np.polyfit(xs, highs, 1)
        low_fit  = np.polyfit(xs, lows, 1)
        lines.append({"x1":0,"y1":float(np.polyval(high_fit,0)),"x2":n-1,"y2":float(np.polyval(high_fit,n-1)),
                      "color":"#58a6ff","dash":False,"label":"壓力"})
        lines.append({"x1":0,"y1":float(np.polyval(low_fit,0)),"x2":n-1,"y2":float(np.polyval(low_fit,n-1)),
                      "color":"#58a6ff","dash":False,"label":"支撐"})

    elif pattern_en == "box_consolidation":
        box_high = float(np.percentile(highs[-20:], 85))
        box_low  = float(np.percentile(lows[-20:], 15))
        start_x  = max(0, n-20)
        lines.append({"x1":start_x,"y1":box_high,"x2":n-1,"y2":box_high,
                      "color":"#ff6600","dash":True,"label":"箱頂"})
        lines.append({"x1":start_x,"y1":box_low,"x2":n-1,"y2":box_low,
                      "color":"#00aaff","dash":True,"label":"箱底"})

    elif pattern_en in ("triple_bottom",):
        if len(valleys) >= 3:
            for i, (vi, vp) in enumerate(valleys[-3:]):
                marks.append({"x":vi,"y":vp,"color":"#00ff88","text":f"底{i+1}","shape":"circle"})
            neck = float(np.max(closes[valleys[-3][0]:valleys[-1][0]+1]))
            lines.append({"x1":valleys[-3][0],"y1":neck,"x2":min(n-1,valleys[-1][0]+5),"y2":neck,
                          "color":"#ffd700","dash":True,"label":"頸線"})

    elif pattern_en == "round_bottom":
        # 畫U形底部支撐線
        low_idx = int(np.argmin(closes))
        lines.append({"x1":0,"y1":float(closes[0]),"x2":low_idx,"y2":float(closes[low_idx]),
                      "color":"#00ff88","dash":True,"label":""})
        lines.append({"x1":low_idx,"y1":float(closes[low_idx]),"x2":n-1,"y2":float(closes[-1]),
                      "color":"#00ff88","dash":True,"label":""})

    elif pattern_en == "bull_flag":
        # 旗桿 + 旗形通道
        flag_start = max(0, n-15)
        pole_end   = max(0, n-20)
        if pole_end > 0:
            marks.append({"x":pole_end,"y":float(closes[pole_end]),"text":"旗桿","color":"#ffd700","shape":"circle"})
        xs_flag = list(range(flag_start, n))
        if len(xs_flag) >= 3:
            h_fit = np.polyfit(xs_flag, highs[flag_start:], 1)
            l_fit = np.polyfit(xs_flag, lows[flag_start:], 1)
            lines.append({"x1":flag_start,"y1":float(np.polyval(h_fit,flag_start)),"x2":n-1,"y2":float(np.polyval(h_fit,n-1)),
                          "color":"#ffd700","dash":False,"label":"旗上緣"})
            lines.append({"x1":flag_start,"y1":float(np.polyval(l_fit,flag_start)),"x2":n-1,"y2":float(np.polyval(l_fit,n-1)),
                          "color":"#ffd700","dash":False,"label":"旗下緣"})

    elif pattern_en == "ascending_triangle_bottom":
        resist = float(np.percentile(highs[-15:], 90))
        xs = list(range(n))
        low_fit = np.polyfit(xs, lows, 1)
        lines.append({"x1":0,"y1":resist,"x2":n-1,"y2":resist,
                      "color":"#ff6600","dash":True,"label":"壓力"})
        lines.append({"x1":0,"y1":float(np.polyval(low_fit,0)),
                      "x2":n-1,"y2":float(np.polyval(low_fit,n-1)),
                      "color":"#00ff88","dash":False,"label":"支撐↗"})
        if len(valleys) >= 2:
            for vi, vp in valleys[-2:]:
                marks.append({"x":vi,"y":vp,"color":"#00ff88","text":"低","shape":"circle","size":6})

    elif pattern_en == "round_top":
        peak_i = int(np.argmax(highs))
        peak_p = float(highs[peak_i])
        marks.append({"x":peak_i,"y":peak_p,"color":"#ff4444","text":"頂","shape":"diamond","size":8})
        if peak_i > 3:
            lines.append({"x1":0,"y1":float(closes[0]),"x2":peak_i,"y2":peak_p,
                          "color":"#ffaa00","dash":False,"label":"上升"})
        if peak_i < n-3:
            lines.append({"x1":peak_i,"y1":peak_p,"x2":n-1,"y2":float(closes[-1]),
                          "color":"#ff4444","dash":False,"label":"下降"})

    elif pattern_en in ("high_consolidation","low_consolidation"):
        color = "#ffaa00" if pattern_en == "high_consolidation" else "#58a6ff"
        h_line = float(np.percentile(highs[-20:], 85))
        l_line = float(np.percentile(lows[-20:], 15))
        start_x = max(0, n-20)
        lines.append({"x1":start_x,"y1":h_line,"x2":n-1,"y2":h_line,
                      "color":color,"dash":True,"label":"上界"})
        lines.append({"x1":start_x,"y1":l_line,"x2":n-1,"y2":l_line,
                      "color":color,"dash":True,"label":"下界"})

    elif pattern_en in ("uptrend","downtrend"):
        color = "#00ff88" if pattern_en == "uptrend" else "#ff4444"
        xs = list(range(n))
        close_fit = np.polyfit(xs, closes, 1)
        lines.append({"x1":0,"y1":float(np.polyval(close_fit,0)),
                      "x2":n-1,"y2":float(np.polyval(close_fit,n-1)),
                      "color":color,"dash":False,"label":"趨勢"})
        marks.append({"x":n-1,"y":float(closes[-1]),"color":color,
                      "text":"▲" if pattern_en=="uptrend" else "▼","shape":"circle","size":6})

    return {"lines": lines, "marks": marks}


def get_support_resistance(df, n_bars=30):
    """
    自動計算支撐線和壓力線
    回傳：{
        support: [{price, strength, x_start, x_end}],
        resistance: [{price, strength, x_start, x_end}]
    }
    """
    if df is None or len(df) == 0: return {"support":[], "resistance":[]}
    if df is None or len(df) < 15: return {"support":[], "resistance":[]}
    df = df.copy().tail(n_bars).reset_index(drop=True)
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
    n = len(df)
    if n == 0: return {"support":[], "resistance":[]}
    highs  = df["high"].values
    lows   = df["low"].values
    closes = df["close"].values

    # 找高低點（多組 window）
    all_peaks   = []
    all_valleys = []
    for w in [2, 3, 5]:
        p, v = find_peaks_valleys(closes, window=w)
        all_peaks.extend(p)
        all_valleys.extend(v)

    # 去重
    seen_p = set(); unique_peaks = []
    for i, p in all_peaks:
        if i not in seen_p:
            seen_p.add(i); unique_peaks.append((i, p))

    seen_v = set(); unique_valleys = []
    for i, v in all_valleys:
        if i not in seen_v:
            seen_v.add(i); unique_valleys.append((i, v))

    last_close = closes[-1]
    price_range = (np.max(highs) - np.min(lows))
    cluster_thresh = price_range * 0.02  # 2% 範圍內視為同一區

    def cluster_levels(points, is_support):
        """把相近的高低點聚類成一條線"""
        if not points: return []
        prices = [p for _, p in points]
        indices = [i for i, _ in points]
        levels = []
        used = set()
        for i in range(len(prices)):
            if i in used: continue
            cluster_prices = [prices[i]]
            cluster_idxs   = [indices[i]]
            for j in range(i+1, len(prices)):
                if j not in used and abs(prices[j]-prices[i]) < cluster_thresh:
                    cluster_prices.append(prices[j])
                    cluster_idxs.append(indices[j])
                    used.add(j)
            used.add(i)
            if len(cluster_prices) >= 1:
                avg_price = float(np.mean(cluster_prices))
                strength  = len(cluster_prices)  # 觸及次數
                x_start   = max(0, min(cluster_idxs)-2)
                x_end     = min(n-1, max(cluster_idxs)+5)
                # 只取有效的支撐（在收盤價下方）或壓力（在收盤價上方）
                if is_support and avg_price < last_close * 1.02:
                    levels.append({"price":avg_price,"strength":strength,
                                   "x_start":x_start,"x_end":x_end})
                elif not is_support and avg_price > last_close * 0.98:
                    levels.append({"price":avg_price,"strength":strength,
                                   "x_start":x_start,"x_end":x_end})
        # 按強度排序，取最強的3條
        levels.sort(key=lambda x: -x["strength"])
        return levels[:3]

    supports    = cluster_levels(unique_valleys, is_support=True)
    resistances = cluster_levels(unique_peaks,   is_support=False)

    # 加入整數關卡（心理價位）
    round_levels = []
    min_p = np.min(lows)
    max_p = np.max(highs)
    step = price_range / 10
    if step > 0:
        base = round(min_p / step) * step
        for i in range(15):
            level = base + i * step
            if min_p <= level <= max_p:
                round_levels.append(level)

    return {
        "support":    supports,
        "resistance": resistances,
    }
