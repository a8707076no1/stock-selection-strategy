"""
飆股選股策略 自動回測 + 自動迭代 框架
========================================
目標：找出 +10% 命中率 ≥ 70% 的選股策略（14 個日曆日內）
標準：分段判定 +10% / +20% / +30% 三個門檻

流程：
  1) 載入 price_data.pkl 歷史價（102 支，回溯到 ~2025/06）
  2) 對多個歷史測試日 (test_date)，把每支股的 K 線截至 test_date
  3) 對每支股跑 evaluate_stock_offline()，產生 raw 訊號/評分/特徵
  4) 計算 test_date + 14 calendar days 的收盤價（forward return）
  5) 套用「策略變體」過濾選股 → 計算命中率
  6) 自動迭代策略，直到 +10% 命中率 ≥ 70% 或試完所有變體

執行：
  python3 backtest_harness.py
"""
import os, sys, json, pickle, statistics
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from advanced_signals import (
    doji_reversal, pullback_buy, is_breakout_starter,
    overextension_score, consecutive_down_after_up
)

CACHE_PRICE = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/price_data.pkl")
CACHE_INDEX = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/index_data.pkl")
OUTPUT_JSON = "/tmp/backtest_full_report.json"

# CFG（複製 screener 的 CFG，offline 版本）
CFG = {
    "vol_multiple_normal": 2.2, "vol_multiple_gapup": 1.8,
    "bias_limit_initial": 0.08, "bias_limit_overheated": 0.15,
    "pre_break_volume_limit": 1.2, "body_ratio_min": 0.5,
    "upper_shadow_ratio_max": 0.3, "continuation_vol_multiple": 1.5,
    "overheated_vol_multiple": 4.0, "top_warn_vol_multiple": 3.0,
    "score_breakout": 75, "score_watch": 60,
    "liquidity_threshold": 100_000_000, "rs20_threshold": 0.0,
}

# ─────────────────────────────────────────────────────────────
# Offline evaluate（複製 v3 的核心邏輯，可吃任意 cutoff）
# ─────────────────────────────────────────────────────────────
def evaluate_stock_offline(sid, df_full, idf_full, cutoff_date):
    """以 cutoff_date 當天為「今天」評估這支股"""
    df = df_full[df_full["date"] <= cutoff_date].reset_index(drop=True)
    if len(df) < 22:
        return None
    for c in ["open","high","low","close","volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close","volume"]).reset_index(drop=True)
    if len(df) < 22:
        return None

    t = len(df) - 1
    O, H, L, C, V = df["open"].iloc[t], df["high"].iloc[t], df["low"].iloc[t], df["close"].iloc[t], df["volume"].iloc[t]
    MA5  = df["close"].iloc[t-4:t+1].mean()
    MA10 = df["close"].iloc[t-9:t+1].mean()
    MA20 = df["close"].iloc[t-19:t+1].mean()
    MA60 = df["close"].iloc[t-59:t+1].mean() if t >= 59 else None
    VMA20 = df["volume"].iloc[t-19:t+1].mean()
    H20 = df["high"].iloc[max(0, t-20):t].max()
    Rng = H - L
    B20 = C/MA20 - 1 if MA20 > 0 else 0
    VB = V/VMA20 if VMA20 > 0 else 0
    BP = C/H20 - 1 if H20 > 0 else 0
    BR = abs(C-O)/Rng if Rng > 0 else 0
    US = (H - max(O, C))/Rng if Rng > 0 else 1
    GU = L > df["high"].iloc[t-1] if t > 0 else False
    A20 = (df["close"]*df["volume"]).iloc[t-19:t+1].mean()
    pv = df["volume"].iloc[max(0, t-10):t]
    DR = (pv < CFG["pre_break_volume_limit"]*VMA20).sum() / max(1, len(pv))

    # RS20
    RS = 0
    if idf_full is not None and not idf_full.empty:
        idf = idf_full[idf_full["date"] <= cutoff_date].reset_index(drop=True)
        if t >= 20 and len(idf) >= 21:
            s20 = C/df["close"].iloc[t-20] - 1 if df["close"].iloc[t-20] > 0 else 0
            ic = idf["close"].dropna()
            if len(ic) >= 21:
                RS = s20 - (ic.iloc[-1]/ic.iloc[-21] - 1)

    RV = CFG["vol_multiple_gapup"] if GU else CFG["vol_multiple_normal"]
    G1 = VB >= RV
    G2 = C > H20
    G3 = B20 < CFG["bias_limit_initial"]
    G4 = df["volume"].iloc[max(0, t-5):t].max() < CFG["pre_break_volume_limit"]*VMA20 if t >= 5 else True
    G5 = BR > CFG["body_ratio_min"] and US < CFG["upper_shadow_ratio_max"]
    G6 = A20 >= CFG["liquidity_threshold"]
    G7 = RS > CFG["rs20_threshold"]
    Gate = G1 and G2 and G3 and G4 and G5 and G6 and G7

    S1 = 20*min(1, VB/3)
    S2 = 20*min(1, max(0, BP)/0.05)
    S3 = 15*DR
    S4 = 10 if B20 <= 0.08 else max(0, 10*(1 - (B20-0.08)/0.07))
    S5 = 10*((1 if MA5 > MA10 else 0) + (1 if MA10 > MA20 else 0))/2
    S6 = 15*min(1, BR/0.7)*(1 - min(1, US/0.5))
    S7 = 10 if RS >= 0.10 else max(0, 10*RS/0.10)
    Sc = S1+S2+S3+S4+S5+S6+S7

    # 訊號分類
    if Gate and Sc >= CFG["score_breakout"]:
        Sig = "INITIAL_BREAKOUT"
    elif Gate and Sc >= CFG["score_watch"]:
        Sig = "BREAKOUT_WATCH"
    elif C > MA5 and B20 >= 0.08 and VB > CFG["continuation_vol_multiple"]:
        Sig = "CONTINUATION"
    elif B20 > CFG["bias_limit_overheated"] and VB > CFG["overheated_vol_multiple"]:
        Sig = "OVERHEATED"
    elif B20 > CFG["bias_limit_overheated"] and C > MA5:
        Sig = "CONTINUATION"
    elif VB > CFG["overheated_vol_multiple"] and B20 <= CFG["bias_limit_overheated"]:
        Sig = "BREAKOUT_WATCH"
    else:
        Sig = "NONE"

    # 進階訊號
    adv = {
        "doji_top": False, "doji_bot": False,
        "cons_down": False,
        "starter": False, "starter_score": 0,
        "overext_score": 0,
        "rsi14": 50.0,
        "ma20_slope": 0,
        "consolidation": 1.0,
        "gain60": 0,
    }
    try:
        d = doji_reversal(df)
        adv["doji_top"] = d["signal"] == "top"
        adv["doji_bot"] = d["signal"] == "bottom"
    except Exception: pass
    try:
        c = consecutive_down_after_up(df)
        adv["cons_down"] = c["signal"]
    except Exception: pass
    try:
        b = is_breakout_starter(df)
        adv["starter"] = b["signal"]
        adv["starter_score"] = b["score"]
    except Exception: pass
    try:
        o = overextension_score(df)
        adv["overext_score"] = o["score"]
        adv["rsi14"] = o.get("rsi", 50.0)
    except Exception: pass

    if MA20 and t >= 24:
        ma20_5ago = df["close"].iloc[t-24:t-4].mean()
        adv["ma20_slope"] = (MA20 - ma20_5ago) / ma20_5ago if ma20_5ago > 0 else 0
    if t >= 60:
        c60 = df["close"].iloc[t-59:t+1]
        adv["consolidation"] = c60.std()/c60.mean() if c60.mean() > 0 else 1
        adv["gain60"] = (C/df["close"].iloc[t-59] - 1) if df["close"].iloc[t-59] > 0 else 0

    return {
        "sid": sid, "Sig": Sig, "C": C, "Sc": Sc, "VB": VB,
        "B20": B20, "BP": BP, "BR": BR, "US": US, "RS": RS,
        "MA5": MA5, "MA20": MA20, "MA60": MA60, "VMA20": VMA20,
        "Gate": Gate, **adv,
    }


# ─────────────────────────────────────────────────────────────
# 計算未來 N 個日曆日的報酬率
# ─────────────────────────────────────────────────────────────
def forward_return(df_full, cutoff_date, hold_days=14):
    """從 cutoff_date 收盤起算，到 cutoff_date + hold_days 之間的：
       close-to-close return / max gain / min gain

       自動 snap 到最近的交易日（非交易日往前取）"""
    cutoff_dt = datetime.strptime(cutoff_date, "%Y-%m-%d")
    target_dt = cutoff_dt + timedelta(days=hold_days)
    target_str = target_dt.strftime("%Y-%m-%d")

    # entry: 取 cutoff_date 當天或之前最近的交易日收盤
    entry_rows = df_full[df_full["date"] <= cutoff_date]
    if entry_rows.empty: return None
    entry_row = entry_rows.iloc[-1]
    entry = float(entry_row["close"])
    entry_date = entry_row["date"]
    if entry <= 0: return None

    # 從 entry 隔日到 target 的視窗
    win = df_full[(df_full["date"] > entry_date) & (df_full["date"] <= target_str)]
    if win.empty: return None

    win = win.copy()
    win["close"] = pd.to_numeric(win["close"], errors="coerce")
    win["high"]  = pd.to_numeric(win["high"], errors="coerce")
    win["low"]   = pd.to_numeric(win["low"], errors="coerce")
    final_close = float(win["close"].iloc[-1])
    max_high = float(win["high"].max())
    min_low = float(win["low"].min())
    return {
        "entry": entry,
        "final": final_close,
        "ret_pct": (final_close - entry) / entry * 100,
        "max_gain_pct": (max_high - entry) / entry * 100,
        "max_drawdown_pct": (min_low - entry) / entry * 100,
        "days_actual": len(win),
    }


# ─────────────────────────────────────────────────────────────
# 載入資料 + 跑所有 (test_date, sid) 評估
# ─────────────────────────────────────────────────────────────
def market_regime(idf, cutoff_date):
    """判斷大盤環境：MA20 上揚 + 收盤 > MA20 = 多頭；反之熊"""
    if idf is None or idf.empty:
        return "unknown"
    win = idf[idf["date"] <= cutoff_date].reset_index(drop=True)
    if len(win) < 25:
        return "unknown"
    c = float(win["close"].iloc[-1])
    ma20 = float(win["close"].iloc[-20:].mean())
    ma20_5ago = float(win["close"].iloc[-25:-5].mean())
    slope = (ma20 - ma20_5ago) / ma20_5ago if ma20_5ago > 0 else 0
    if slope > 0.005 and c > ma20:
        return "bull"     # 多頭
    if slope < -0.005 and c < ma20:
        return "bear"     # 空頭
    return "neutral"


def precompute_all_evals(test_dates, hold_days=14):
    print(f"📂 載入 price_data.pkl ...")
    with open(CACHE_PRICE, "rb") as f:
        pc = pickle.load(f)
    idf = None
    if os.path.exists(CACHE_INDEX):
        with open(CACHE_INDEX, "rb") as f:
            idx_raw = pickle.load(f)
        if isinstance(idx_raw, dict):
            idf = next(iter(idx_raw.values())) if idx_raw else None
        else:
            idf = idx_raw

    print(f"   共 {len(pc)} 支股的歷史資料")
    print(f"   大盤環境分佈：")
    regime_count = {"bull": 0, "bear": 0, "neutral": 0, "unknown": 0}
    for d in test_dates:
        r = market_regime(idf, d)
        regime_count[r] += 1
        print(f"     {d} → {r}")
    print(f"   多頭 {regime_count['bull']} 個 / 空頭 {regime_count['bear']} 個 / 盤整 {regime_count['neutral']} 個 / 無法判定 {regime_count['unknown']} 個")

    all_evals = {}   # (date, sid) -> eval_dict
    all_returns = {} # (date, sid) -> forward_return_dict
    bull_dates = {d for d in test_dates if market_regime(idf, d) == "bull"}
    print(f"   👉 多頭測試日（用於 V42 評估）：{sorted(bull_dates)}")

    for tdate in test_dates:
        n_eval = 0
        n_ret = 0
        for sid, df in pc.items():
            if df is None or df.empty: continue
            # 評估
            ev = evaluate_stock_offline(sid, df, idf, tdate)
            if ev:
                all_evals[(tdate, sid)] = ev
                n_eval += 1
            # 未來報酬
            fr = forward_return(df, tdate, hold_days)
            if fr and fr["days_actual"] >= max(5, hold_days * 0.5):
                all_returns[(tdate, sid)] = fr
                n_ret += 1
        print(f"   {tdate}: 評估 {n_eval} 支 / 有未來 {hold_days}d 報酬 {n_ret} 支")

    return all_evals, all_returns, bull_dates


# ─────────────────────────────────────────────────────────────
# 策略變體（一系列 filter 函數，回傳 True = 選此股）
# ─────────────────────────────────────────────────────────────
STRATEGIES = {
    # V1: 原始（所有訊號都收）
    "V1_baseline_all_signals":
        lambda e: e["Sig"] in ("INITIAL_BREAKOUT", "BREAKOUT_WATCH", "CONTINUATION"),
    # V2: 只取續漲（CONTINUATION）
    "V2_continuation_only":
        lambda e: e["Sig"] == "CONTINUATION",
    # V3: 只取突破訊號（最強訊號）
    "V3_breakout_only":
        lambda e: e["Sig"] in ("INITIAL_BREAKOUT", "BREAKOUT_WATCH"),
    # V4: CONTINUATION + 多頭排列（MA5>MA10>MA20，月線上揚）
    "V4_continuation_strong_trend":
        lambda e: e["Sig"] == "CONTINUATION" and e["ma20_slope"] > 0.01 and e["MA5"] > e["MA20"],
    # V5: 排除過熱（RSI<70 + gain60<25%）
    "V5_not_overheated":
        lambda e: e["Sig"] != "NONE" and e["rsi14"] < 70 and e["gain60"] < 0.25,
    # V6: RS20 中等強勢（落在 0.05-0.50）
    "V6_moderate_rs":
        lambda e: e["Sig"] != "NONE" and 0.05 < e["RS"] < 0.50,
    # V7: 中分組（避開高分末升段）
    "V7_mid_score":
        lambda e: e["Sig"] != "NONE" and 40 <= e["Sc"] <= 65,
    # V8: 起漲點明確
    "V8_starter_only":
        lambda e: e["starter"] and not e["doji_top"],
    # V9: 排除 doji_top + cons_down
    "V9_no_topreversal":
        lambda e: e["Sig"] != "NONE" and not e["doji_top"] and not e["cons_down"],
    # V10: 綜合（多頭排列 + 中分 + 不過熱）
    "V10_combo":
        lambda e: (e["Sig"] != "NONE"
                   and e["ma20_slope"] > 0.005
                   and e["MA5"] > e["MA20"]
                   and not e["doji_top"]
                   and not e["cons_down"]
                   and e["rsi14"] < 75
                   and e["gain60"] < 0.35),
    # V11: 嚴格多頭啟動（剛突破月線 + 短期動能）
    "V11_strict_uptrend":
        lambda e: (e["Sig"] == "CONTINUATION"
                   and e["ma20_slope"] > 0.01
                   and e["C"] > e["MA20"] * 1.02
                   and e["C"] < e["MA20"] * 1.15
                   and e["VB"] >= 1.5
                   and not e["doji_top"]),
    # V12: 主升段健康
    "V12_main_uptrend_healthy":
        lambda e: (e["MA5"] > e["MA20"]
                   and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3
                   and not e["doji_top"]
                   and not e["cons_down"]
                   and e["rsi14"] < 80
                   and e["BR"] > 0.4),
    # V13: 量價齊揚 + 突破近月高
    "V13_breakout_with_volume":
        lambda e: (e["VB"] >= 2.0
                   and e["BR"] > 0.5
                   and e["BP"] > 0
                   and e["ma20_slope"] > 0
                   and not e["doji_top"]),

    # ─── 迭代 2：基於 V12 進階變體 ─────────────────────────────
    # V14: V12 + 排除 RS20 過低（弱勢股）
    "V14_v12_plus_rs":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4 and e["RS"] > 0.0),
    # V15: V12 + 月線剛上揚（3 個月內 MA60 由下變上）
    "V15_v12_plus_ma60_up":
        lambda e: (e["MA5"] > e["MA20"]
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80),
    # V16: V12 + 收盤離 20 日最高還有 8% 以上（避免追高）
    "V16_v12_room_to_run":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4 and e["BP"] < -0.02),
    # V17: V12 + 月線斜率更陡 + 量更大
    "V17_v12_aggressive":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.8 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 75
                   and e["BR"] > 0.5),
    # V18: V12 + 嚴格 K 棒品質
    "V18_v12_strong_kbar":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.5 and not e["doji_top"]
                   and not e["cons_down"] and e["BR"] > 0.6
                   and e["US"] < 0.25),
    # V19: V12 + 站穩月線 +2% 以上（確認，非剛突破）
    "V19_v12_above_ma20":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V20: V12 + 過熱分數低（綜合過濾）
    "V20_v12_low_overext":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["BR"] > 0.4
                   and e["overext_score"] < 30),
    # V21: 多訊號交集（最嚴）
    "V21_multi_signal_intersect":
        lambda e: (e["MA5"] > e["MA20"]
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.020
                   and e["VB"] >= 1.5 and not e["doji_top"]
                   and not e["cons_down"] and e["BR"] > 0.5
                   and e["RS"] > 0.0 and e["rsi14"] < 78),
    # V22: V13 + 月線上揚
    "V22_v13_with_slope":
        lambda e: (e["VB"] >= 2.0 and e["BR"] > 0.5
                   and e["BP"] > 0 and e["ma20_slope"] > 0.015
                   and not e["doji_top"] and not e["cons_down"]),
    # V23: 趨勢主升段（最強）— 上升通道內加量
    "V23_strong_trend_with_volume":
        lambda e: (e["MA5"] > e["MA20"]
                   and e["ma20_slope"] > 0.02
                   and e["VB"] >= 1.5
                   and e["BR"] > 0.4
                   and not e["doji_top"]
                   and not e["cons_down"]
                   and e["C"] > e["MA20"]
                   and e["C"] < e["MA20"] * 1.20
                   and e["rsi14"] < 78),

    # ─── 迭代 3：基於 V19 冠軍變體（C 在 MA20 1.02-1.10 是關鍵）─────
    # V24: V19 收窄到 1.01-1.06（更貼月線）
    "V24_v19_tighter_band":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.01 and e["C"] < e["MA20"] * 1.06),
    # V25: V19 放寬到 1.02-1.15
    "V25_v19_wider_band":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.15),
    # V26: V19 + 加上 MA60 多頭（季線也上揚）
    "V26_v19_plus_ma60":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V27: V19 + 量更大
    "V27_v19_more_volume":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.8 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V28: V19 + RS20 > 5%
    "V28_v19_with_rs":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V29: V19 但用 MA5 vs MA10（更短期動能）
    "V29_v19_short_momentum":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.5 and e["BP"] > -0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V30: V19 + 排除過熱（max gain 衝刺型）
    "V30_v19_low_overext":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.015
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"]
                   and e["BR"] > 0.4 and e["overext_score"] < 30
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V31: V19 + 月線斜率更陡
    "V31_v19_steeper_slope":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),

    # ─── 迭代 4：V31/V28 雙王組合 ─────────────────────────────────
    # V32: V31 + RS > 0.05
    "V32_v31_with_rs":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V33: V31 + MA60 多頭排列
    "V33_v31_plus_ma60":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V34: V31 + RS + MA60（三重保險）
    "V34_v31_rs_ma60":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V35: V31 但 slope > 3%（更陡）
    "V35_v31_super_steep":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.030
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V36: V31 但放寬 C/MA20 上限到 1.12
    "V36_v31_wider_top":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.12),
    # V37: V31 + 60 日波動率（要剛打底完）
    "V37_v31_after_consolidation":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["consolidation"] < 0.12
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V38: V31 + 量更大
    "V38_v31_more_volume":
        lambda e: (e["MA5"] > e["MA20"] and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.6 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["BR"] > 0.4
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),

    # ─── 迭代 5 (1966-stock universe) ─────────────────────────────
    # V39: V34 + 必須 Gate=True（已過完整 Gate 條件）
    "V39_v34_gated":
        lambda e: (e["Gate"]
                   and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025
                   and e["VB"] >= 1.3 and not e["doji_top"]
                   and not e["cons_down"] and e["rsi14"] < 80
                   and e["RS"] > 0.05),
    # V40: V34 + 限突破訊號（INITIAL_BREAKOUT / BREAKOUT_WATCH）
    "V40_v34_breakout_only":
        lambda e: (e["Sig"] in ("INITIAL_BREAKOUT", "BREAKOUT_WATCH")
                   and e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.05),
    # V41: V34 + 評分 ≥ 55（高品質）
    "V41_v34_high_score":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10
                   and e["Sc"] >= 55),
    # V42: V34 但 RS > 10%（更強相對強度）
    "V42_v34_strong_rs":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.10
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V43: V34 + 量比 ≥ 2.0（爆量）
    "V43_v34_vol_burst":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 2.0
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V44: V34 + K 棒實體強（> 0.6）+ 上影短（< 0.2）
    "V44_v34_strong_kbar":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.6 and e["US"] < 0.20
                   and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10),
    # V45: V34 + 突破近 60 日箱頂（真起漲訊號）
    "V45_v34_breakout_box":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10
                   and e["BP"] > 0),
    # V46: V34 + 60 日波動率低（剛打底完）
    "V46_v34_consolidated":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 1.3
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.4 and e["RS"] > 0.05
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10
                   and e["consolidation"] < 0.15),
    # V47: V34 三重最強（高分 + 量爆 + 強RS）
    "V47_v34_triple_strong":
        lambda e: (e["MA5"] > e["MA20"] and e["MA20"] > (e["MA60"] or 0)
                   and e["ma20_slope"] > 0.025 and e["VB"] >= 2.0
                   and not e["doji_top"] and not e["cons_down"]
                   and e["rsi14"] < 80 and e["BR"] > 0.5 and e["RS"] > 0.10
                   and e["C"] > e["MA20"] * 1.02 and e["C"] < e["MA20"] * 1.10
                   and e["Sc"] >= 50),
}


# ─────────────────────────────────────────────────────────────
# 套用策略 + 計算命中率
# ─────────────────────────────────────────────────────────────
def evaluate_strategy(strategy_name, strategy_fn, all_evals, all_returns):
    """回傳 dict：每個 threshold 的命中數/總數/命中率"""
    picks = []  # list of (date, sid, ev, ret)
    for (date, sid), ev in all_evals.items():
        if not strategy_fn(ev): continue
        ret = all_returns.get((date, sid))
        if ret is None: continue
        picks.append({"date": date, "sid": sid, "ev": ev, "ret": ret})

    if not picks:
        return {"name": strategy_name, "n": 0, "no_picks": True}

    n = len(picks)
    hit10 = sum(1 for p in picks if p["ret"]["ret_pct"] >= 10)
    hit20 = sum(1 for p in picks if p["ret"]["ret_pct"] >= 20)
    hit30 = sum(1 for p in picks if p["ret"]["ret_pct"] >= 30)
    avg = statistics.mean(p["ret"]["ret_pct"] for p in picks)
    med = statistics.median(p["ret"]["ret_pct"] for p in picks)
    neg = sum(1 for p in picks if p["ret"]["ret_pct"] < 0)

    # 用最大漲幅版命中（曾經到過）
    mhit10 = sum(1 for p in picks if p["ret"]["max_gain_pct"] >= 10)
    mhit20 = sum(1 for p in picks if p["ret"]["max_gain_pct"] >= 20)
    mhit30 = sum(1 for p in picks if p["ret"]["max_gain_pct"] >= 30)

    return {
        "name": strategy_name,
        "n": n,
        "hit10_pct": hit10/n*100, "hit20_pct": hit20/n*100, "hit30_pct": hit30/n*100,
        "max_hit10_pct": mhit10/n*100, "max_hit20_pct": mhit20/n*100, "max_hit30_pct": mhit30/n*100,
        "avg_ret": avg, "med_ret": med, "neg_pct": neg/n*100,
        "picks_per_date": n / len(set(p["date"] for p in picks)),
        "picks": picks,
    }


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    today = datetime.today()
    # 測試日期：必須讓 test_date + 14 calendar days <= today
    # 取 5 個過去歷史日（週為間隔，間隔開）
    # 2 年回測：從 2024-04-15 起每月一個測試日
    test_dates = []
    cutoff = today - timedelta(days=14)
    y, m = 2024, 4
    while True:
        d = datetime(y, m, 15)
        if d <= cutoff:
            test_dates.append(d.strftime("%Y-%m-%d"))
        m += 1
        if m > 12: m = 1; y += 1
        if y > today.year or (y == today.year and m > today.month): break
    test_dates = sorted(set(test_dates))

    print("="*70)
    print(f"🔬 飆股策略自動回測 + 自動迭代")
    print(f"   今天：{today.strftime('%Y-%m-%d')}")
    print(f"   測試日：{test_dates}")
    print(f"   持有：14 個日曆日")
    print(f"   命中標準：+10% / +20% / +30% 三段")
    print(f"   目標：+10% 命中率 ≥ 70%")
    print("="*70)

    all_evals, all_returns, bull_dates = precompute_all_evals(test_dates, hold_days=14)
    print(f"\n✅ 共 {len(all_evals)} 筆評估、{len(all_returns)} 筆有 14 日報酬\n")

    # 過濾出多頭日的評估（給 V42-市場過濾版用）
    bull_evals = {(d,s): e for (d,s), e in all_evals.items() if d in bull_dates}
    bull_returns = {(d,s): r for (d,s), r in all_returns.items() if d in bull_dates}
    print(f"   多頭環境樣本：{len(bull_evals)} 筆\n")

    # 跑所有策略
    print("="*100)
    print(f"{'策略':35} {'選股數':>8} {'+10%':>7} {'+20%':>7} {'+30%':>7} {'平均':>7} {'中位':>7} {'虧損%':>7}")
    print("="*100)

    results = []
    print()
    print("─" * 100)
    print("📊 全期間（含多頭/空頭/盤整）")
    print("─" * 100)
    for name, fn in STRATEGIES.items():
        r = evaluate_strategy(name, fn, all_evals, all_returns)
        if r.get("no_picks"):
            print(f"{name:35} (無選股)")
            continue
        results.append(r)
        flag = "🚀" if r["hit10_pct"] >= 70 else ("✅" if r["hit10_pct"] >= 50 else "⚠️" if r["hit10_pct"] >= 30 else "❌")
        print(f"{flag} {name:33} {r['n']:>5} {r['picks_per_date']:>4.1f}/d  {r['hit10_pct']:>5.1f}%  {r['hit20_pct']:>5.1f}%  {r['hit30_pct']:>5.1f}%  {r['avg_ret']:+5.1f}%  {r['med_ret']:+5.1f}%  {r['neg_pct']:>5.1f}%")

    # 只在多頭日跑 V42 — 看是否回到 70%
    print()
    print("─" * 100)
    print(f"🐂 僅多頭環境（{len(bull_dates)} 個測試日）— V42 在大盤主升段下的真實表現")
    print("─" * 100)
    bull_results = []
    for name in ["V34_v31_rs_ma60", "V42_v34_strong_rs", "V45_v34_breakout_box",
                 "V33_v31_plus_ma60", "V35_v31_super_steep"]:
        if name not in STRATEGIES: continue
        r = evaluate_strategy(name + "_BULL", STRATEGIES[name], bull_evals, bull_returns)
        if r.get("no_picks"): continue
        bull_results.append(r)
        flag = "🚀" if r["hit10_pct"] >= 70 else ("✅" if r["hit10_pct"] >= 50 else "⚠️")
        print(f"{flag} {r['name']:33} {r['n']:>5}  +10%={r['hit10_pct']:>5.1f}%"
              f"  +20%={r['hit20_pct']:>5.1f}%  +30%={r['hit30_pct']:>5.1f}%"
              f"  avg={r['avg_ret']:+5.1f}%  虧損={r['neg_pct']:>5.1f}%"
              f"  曾摸到+10%={r['max_hit10_pct']:>5.1f}%")

    # 排序找冠軍
    results.sort(key=lambda r: (r["hit10_pct"], r["avg_ret"]), reverse=True)
    print("\n" + "="*70)
    print("🏆 命中率排名（依 +10% 命中率）：")
    for i, r in enumerate(results[:5]):
        print(f"  {i+1}. {r['name']}：+10% 命中 {r['hit10_pct']:.1f}%，平均報酬 {r['avg_ret']:+.1f}%（{r['n']} 筆）")

    best = results[0] if results else None
    print()
    if not best:
        print("⚠️ 所有策略都無選股，請檢查資料")
        return None
    if best["hit10_pct"] >= 70:
        print(f"🎯 目標達成！冠軍策略「{best['name']}」+10% 命中率 = {best['hit10_pct']:.1f}%")
    else:
        print(f"⚠️ 未達 70% 目標。最高 +10% 命中率：{best['hit10_pct']:.1f}%（{best['name']}）")
        print(f"   → 進入第二輪：用「最大漲幅版命中」+ 更激進條件再迭代")

    # 用最大漲幅版排序
    print("\n" + "="*70)
    print("🚀 「曾摸到」最大漲幅命中率排名（持有期間任一日達標即算）：")
    results.sort(key=lambda r: r["max_hit10_pct"], reverse=True)
    for i, r in enumerate(results[:5]):
        print(f"  {i+1}. {r['name']}：曾達+10% {r['max_hit10_pct']:.1f}%、曾達+20% {r['max_hit20_pct']:.1f}%、曾達+30% {r['max_hit30_pct']:.1f}%")

    # 存報告（picks 不存，避免太大）
    out = []
    for r in results:
        r2 = {k: v for k, v in r.items() if k != "picks"}
        out.append(r2)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "today": today.strftime("%Y-%m-%d"),
            "test_dates": test_dates,
            "strategies": out,
            "winner": best["name"] if best else None,
            "winner_hit10": best["hit10_pct"] if best else 0,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 報告已存：{OUTPUT_JSON}")

    return best


if __name__ == "__main__":
    main()
