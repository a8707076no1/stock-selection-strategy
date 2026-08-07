"""
飆股進階訊號模組
基於回測（5/4-5/9 → 5/15 命中率分析）+ 朱家泓上校 / 量化通 / Wantgoo / 豹投資 等多方研究

提供：
  - is_doji(bar)                      → 十字線/變盤線判定
  - doji_reversal(df)                 → 高/低檔變盤訊號（含信心分數）
  - pullback_buy(df)                  → 回後買上漲（朱家泓核心戰法）
  - is_breakout_starter(df)           → 真正起漲點（過濾末升段）
  - overextension_score(df)           → 過熱度（離年線、近 60 日漲幅）
  - combined_action(df)               → 整合建議（最重要的對外介面）

設計依據（重質不重量的數值門檻）：
  - Doji body / range <= 10%（台股社群實務）
  - 拉回 5-12% 為飆股回檔區間
  - 距年線 > 30% 或近 60 日漲幅 > 20% 視為「已漲多」
  - 連 2 黑且前段連紅 → 轉空
"""
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────
# 基本 K 棒型態判定
# ─────────────────────────────────────────────────────────────
def is_doji(o, h, l, c, body_ratio=0.15, min_range_pct=0.015):
    """十字線/變盤線（含 spinning top 小實體）

    放寬到 body/range ≤ 15%（含 spinning top）或 body/收盤 ≤ 0.5%（極小實體絕對值）
    任一達標即視為變盤線。理由：台股社群常把「實體極小」也歸為變盤訊號，
    嚴格 5% 會漏掉如胡連 5/8 這種 11% 但實體只占股價 0.4% 的關鍵變盤。
    """
    rng = h - l
    if rng <= 0 or c <= 0:
        return False
    if rng / c < min_range_pct:
        return False
    body = abs(c - o)
    if (body / rng) <= body_ratio:
        return True
    if (body / c) <= 0.005:  # 實體 < 收盤 0.5%
        return True
    return False


def doji_kind(o, h, l, c):
    """變盤線變形：standard / dragonfly（蜻蜓-多）/ gravestone（墓碑-空）/ none"""
    if not is_doji(o, h, l, c):
        return "none"
    upper = h - max(o, c)
    lower = min(o, c) - l
    if upper <= 0 and lower <= 0:
        return "standard"
    if upper >= 2 * max(lower, 1e-9) and lower / max(h - l, 1e-9) < 0.15:
        return "gravestone"   # 高檔看空
    if lower >= 2 * max(upper, 1e-9) and upper / max(h - l, 1e-9) < 0.15:
        return "dragonfly"    # 低檔看多
    return "standard"


# ─────────────────────────────────────────────────────────────
# 1. 變盤線（doji）反轉訊號
# ─────────────────────────────────────────────────────────────
def doji_reversal(df, n_lookback=5):
    """
    回傳：{"signal": "top"|"bottom"|"none",
           "confidence": 0-100,
           "kind": "standard"|"gravestone"|"dragonfly"|"none",
           "reason": str}
    判斷規則：
      - 出現十字線
      - 前 5 日累積漲 > 5% → 高檔變盤（top）
      - 前 5 日累積跌 > 5% → 低檔變盤（bottom）
      - 在近 20 日高/低區（95%/105%）才算「位置正確」
      - 墓碑變形 +15 分，蜻蜓變形 +15 分
    """
    if df is None or len(df) < n_lookback + 1:
        return {"signal": "none", "confidence": 0, "kind": "none", "reason": "資料不足"}
    last = df.iloc[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    kind = doji_kind(o, h, l, c)
    if kind == "none":
        return {"signal": "none", "confidence": 0, "kind": "none", "reason": "非十字線"}

    prior = df.iloc[-(n_lookback + 1):-1]
    if len(prior) == 0:
        return {"signal": "none", "confidence": 0, "kind": kind, "reason": "前段資料不足"}
    trend_pct = (prior["close"].iloc[-1] / prior["close"].iloc[0] - 1) * 100

    win = df.tail(20)
    high20 = float(win["high"].max())
    low20 = float(win["low"].min())
    near_high = c >= high20 * 0.95
    near_low = c <= low20 * 1.05

    conf = 60
    if trend_pct > 5 and near_high:
        conf += 20
        if kind == "gravestone":
            conf += 15
        return {
            "signal": "top",
            "confidence": min(conf, 95),
            "kind": kind,
            "reason": f"前{n_lookback}日漲{trend_pct:+.1f}%、近20日高檔（{c}/{high20}）→ 高檔變盤"
        }
    if trend_pct < -5 and near_low:
        conf += 20
        if kind == "dragonfly":
            conf += 15
        return {
            "signal": "bottom",
            "confidence": min(conf, 95),
            "kind": kind,
            "reason": f"前{n_lookback}日跌{trend_pct:+.1f}%、近20日低檔（{c}/{low20}）→ 低檔變盤"
        }
    return {
        "signal": "none",
        "confidence": 30,
        "kind": kind,
        "reason": f"十字線出現但位置非高/低檔（前{n_lookback}日{trend_pct:+.1f}%），盤整中變盤無預測力"
    }


# ─────────────────────────────────────────────────────────────
# 連跌轉空判定（補充）
# ─────────────────────────────────────────────────────────────
def consecutive_down_after_up(df, n_up=5, n_down=2):
    """
    連 n_down 黑且前 n_up 日為上漲段 → 轉空訊號
    回傳：{"signal": bool, "reason": str}
    """
    if df is None or len(df) < n_up + n_down + 1:
        return {"signal": False, "reason": "資料不足"}
    tail = df.tail(n_down)
    if not all(tail["close"].values < tail["open"].values):
        return {"signal": False, "reason": f"近{n_down}日非連黑"}
    prior = df.iloc[-(n_up + n_down):-n_down]
    up_pct = (prior["close"].iloc[-1] / prior["close"].iloc[0] - 1) * 100
    if up_pct <= 5:
        return {"signal": False, "reason": f"前{n_up}日漲幅僅{up_pct:+.1f}%，不夠"}
    # 量配合（連黑量增 = 出貨）
    last_v = float(df["volume"].iloc[-1])
    avg5 = float(df["volume"].tail(5).mean())
    vol_note = "量增（出貨警覺）" if last_v > avg5 * 1.2 else "量縮（弱勢）"
    return {
        "signal": True,
        "reason": f"前{n_up}日漲{up_pct:+.1f}% 後連{n_down}黑 + {vol_note} → 轉空訊號"
    }


# ─────────────────────────────────────────────────────────────
# 2. 回後買上漲（朱家泓核心戰法）
# ─────────────────────────────────────────────────────────────
def pullback_buy(df, n_lookback=20):
    """
    回後買上漲訊號（朱家泓戰法 + mirrormedia + gorich.com.tw）

    必要條件：
      1) 月線（MA20）仍上揚
      2) 從近 20 日高拉回 5% ~ 12%
      3) 收盤站回月線（盤中可破，收盤必須站回）
      4) 確認紅 K 實體 ≥ 60% 全長（**禁追十字變盤**）
      5) 量 ≥ 5 日均量 × 1.3
      6) 突破前 3 日高

    回傳 stage：
      "未進入回檔區" / "回中" / "確認站穩" / "續漲突破" / "趨勢未確立"
    """
    if df is None or len(df) < max(60, n_lookback + 5):
        return {"signal": False, "stage": "資料不足", "reason": "", "score": 0}
    close = df["close"]
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma20_5d_ago = float(close.rolling(20).mean().iloc[-5])
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(df) >= 60 else None
    ma20_slope = ma20 - ma20_5d_ago

    if ma20_slope <= 0:
        return {"signal": False, "stage": "趨勢未確立", "reason": "月線未上揚（飆股戰法不適用）", "score": 0}

    recent_high = float(df["high"].tail(n_lookback).max())
    recent_low5 = float(df["low"].tail(5).min())
    pullback = (recent_high - recent_low5) / recent_high

    if pullback < 0.05:
        return {"signal": False, "stage": "未進入回檔區", "reason": f"近期回幅僅 {pullback:.1%}（須 5-12%）", "score": 20}
    if pullback > 0.18:
        return {"signal": False, "stage": "回過頭", "reason": f"回幅 {pullback:.1%} 過深，趨勢恐轉弱", "score": 10}

    last = df.iloc[-1]
    o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
    body = abs(c - o)
    rng = h - l
    is_red = c > o
    body_ratio = body / rng if rng > 0 else 0
    last_v = float(last["volume"])
    avg5v = float(df["volume"].tail(6).iloc[:-1].mean())
    vol_surge = last_v >= avg5v * 1.3
    break_3d_high = c > float(df["high"].iloc[-4:-1].max())
    hold_ma20 = c >= ma20

    # 變盤線檢查（朱家泓最強警告：拉回後出十字 = 再等）
    is_var = is_doji(o, h, l, c)

    if not hold_ma20:
        return {
            "signal": False, "stage": "回中",
            "reason": f"尚未站回月線（{c} < MA20 {ma20:.2f}），等收盤站回",
            "score": 30
        }

    if is_var:
        return {
            "signal": False, "stage": "確認站穩",
            "reason": "站回月線但今日出十字變盤 → 朱家泓戰法明確警告：勿追，等紅K實體",
            "score": 50
        }

    if not (is_red and body_ratio >= 0.6):
        return {
            "signal": False, "stage": "確認站穩",
            "reason": f"站回月線但無紅K實體棒（紅K={is_red}，實體比{body_ratio:.0%}<60%）",
            "score": 55
        }

    if not vol_surge:
        return {
            "signal": False, "stage": "確認站穩",
            "reason": f"紅K但量未到位（量={last_v:.0f} / 5日均量 {avg5v:.0f}，需 ×1.3）",
            "score": 60
        }

    if not break_3d_high:
        return {
            "signal": False, "stage": "確認站穩",
            "reason": "量價皆到位但未破前 3 日高",
            "score": 65
        }

    # 全部條件齊備
    score = 75
    if c > float(df["high"].tail(n_lookback).iloc[:-1].max()):
        score += 15  # 破近 20 日高
    if ma60 and ma20 > ma60:
        score += 10  # 多頭排列
    return {
        "signal": True, "stage": "續漲突破",
        "reason": f"回 {pullback:.1%} 站回月線 + 紅K實體{body_ratio:.0%} + 量增{last_v/avg5v:.1f}x + 破前高",
        "score": min(score, 95)
    }


# ─────────────────────────────────────────────────────────────
# 3. 過熱度（過濾末升段）
# ─────────────────────────────────────────────────────────────
def overextension_score(df):
    """
    過熱度評估（0=未發動 / 100=極度過熱）
    依據：
      - 距 MA240（年線）漲幅
      - 近 60 日漲幅
      - RSI(14)
    """
    if df is None or len(df) < 20:
        return {"score": 0, "extended": False, "factors": []}
    c = float(df["close"].iloc[-1])
    factors = []
    score = 0

    # 距年線
    if len(df) >= 240:
        ma240 = float(df["close"].rolling(240).mean().iloc[-1])
        gap240 = (c / ma240 - 1) * 100 if ma240 > 0 else 0
        if gap240 > 50:
            score += 40; factors.append(f"距年線+{gap240:.0f}%（末升段）")
        elif gap240 > 30:
            score += 25; factors.append(f"距年線+{gap240:.0f}%（已起漲一段）")
        elif gap240 > 15:
            score += 10; factors.append(f"距年線+{gap240:.0f}%（中段）")

    # 近 60 日漲幅
    if len(df) >= 60:
        gain60 = (c / float(df["close"].iloc[-60]) - 1) * 100
        if gain60 > 50:
            score += 30; factors.append(f"60日漲{gain60:+.0f}%（極熱）")
        elif gain60 > 25:
            score += 15; factors.append(f"60日漲{gain60:+.0f}%（已漲多）")

    # RSI
    delta = df["close"].diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = -delta.clip(upper=0).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    cur_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
    if cur_rsi > 75:
        score += 20; factors.append(f"RSI {cur_rsi:.0f}（超買區）")
    elif cur_rsi > 70:
        score += 10; factors.append(f"RSI {cur_rsi:.0f}（偏熱）")
    elif cur_rsi < 30:
        factors.append(f"RSI {cur_rsi:.0f}（超賣，可能機會）")

    return {
        "score": min(score, 100),
        "extended": score >= 40,
        "factors": factors,
        "rsi": round(cur_rsi, 1),
    }


# ─────────────────────────────────────────────────────────────
# 4. 真正起漲點（飆股起點）
# ─────────────────────────────────────────────────────────────
def is_breakout_starter(df, n_consol=60):
    """
    飆股起漲點偵測 — 從「打底 → 量增 → 突破整理區」抓起點

    必要條件：
      1) 近 60 日窄幅整理（std/mean ≤ 8%）
      2) 未過熱（距年線 ≤ 30%、60日漲幅 ≤ 20%）
      3) 當日收盤突破近 60 日箱頂
      4) 當日量 ≥ 20 日均量 × 2
      5) 當日紅 K

    回傳：{"signal": bool, "score": 0-100, "reason": str}
    """
    if df is None or len(df) < n_consol + 5:
        return {"signal": False, "score": 0, "reason": "資料不足"}

    closes60 = df["close"].iloc[-n_consol:]
    consol = closes60.std() / closes60.mean()

    last = df.iloc[-1]
    o, c = float(last["open"]), float(last["close"])
    box_top = float(df["high"].iloc[-n_consol:-1].max())
    breakout = c > box_top
    is_red = c > o
    last_v = float(last["volume"])
    avg20v = float(df["volume"].tail(21).iloc[:-1].mean())
    vol_2x = last_v >= avg20v * 2

    over = overextension_score(df)
    not_hot = not over["extended"]

    score = 0
    reasons = []
    if consol <= 0.08:
        score += 25; reasons.append(f"打底夠久（波動{consol:.1%}）")
    if breakout:
        score += 25; reasons.append(f"突破整理區頂 {box_top:.2f}")
    if vol_2x:
        score += 25; reasons.append(f"量爆 {last_v/avg20v:.1f}x")
    if is_red:
        score += 10; reasons.append("紅K")
    if not_hot:
        score += 15; reasons.append("未過熱")

    signal = score >= 70 and breakout and vol_2x and not_hot
    return {
        "signal": signal,
        "score": score,
        "reason": "; ".join(reasons) if reasons else "未符任何起漲條件",
        "consol_ratio": round(consol, 4),
        "box_top": round(box_top, 2),
        "vol_x": round(last_v / avg20v, 2) if avg20v > 0 else 0,
    }


# ─────────────────────────────────────────────────────────────
# 5. 整合建議（給 chart commentary 用）
# ─────────────────────────────────────────────────────────────
def combined_action(df):
    """
    把全部訊號整合成一段給「飆股圖表」評語區用的精細建議
    回傳：{
        "signals": {doji_reversal, pullback, breakout_start, overext, cons_down},
        "summary": "一句話建議",
        "warnings": [...],
        "opportunities": [...]
    }
    """
    doj = doji_reversal(df)
    pull = pullback_buy(df)
    start = is_breakout_starter(df)
    over = overextension_score(df)
    cdown = consecutive_down_after_up(df)

    warnings = []
    opps = []

    # 警告
    if doj["signal"] == "top":
        warnings.append(f"🔻 高檔變盤線（{doj['kind']}，信心 {doj['confidence']}）：{doj['reason']}")
    if cdown["signal"]:
        warnings.append(f"🔻 連跌轉空：{cdown['reason']}")
    if over["extended"]:
        warnings.append(f"🔥 過熱（{over['score']}）：" + "、".join(over["factors"]))

    # 機會
    if doj["signal"] == "bottom":
        opps.append(f"🟢 低檔變盤線（{doj['kind']}，信心 {doj['confidence']}）：{doj['reason']}")
    if pull["signal"]:
        opps.append(f"🚀 回後買上漲 [續漲突破]（{pull['score']}）：{pull['reason']}")
    elif pull["stage"] in ("確認站穩",):
        opps.append(f"👀 回後買上漲 [{pull['stage']}]（{pull['score']}）：{pull['reason']}")
    if start["signal"]:
        opps.append(f"🚀 飆股起漲點（{start['score']}）：{start['reason']}")

    # 總結
    if warnings and not opps:
        summary = "⚠️ 多項警示，建議停利/避開"
    elif opps and not warnings:
        summary = "✅ 多項利多訊號，可積極"
    elif opps and warnings:
        summary = "🟡 利多 vs 警示並存，分批 / 設停損"
    else:
        summary = "👀 中性，無顯著訊號"

    return {
        "signals": {
            "doji_reversal": doj,
            "pullback": pull,
            "breakout_start": start,
            "overext": over,
            "cons_down": cdown,
        },
        "summary": summary,
        "warnings": warnings,
        "opportunities": opps,
    }
