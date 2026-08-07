"""
資金輪動預測驗證 + V42 命中率提升回測
==========================================
科學步驟：
  Step 1：對 2 年資料每週計算子族群 ranking → 建立輪動歷史
  Step 2：定義 5+ 種輪動預測訊號（動能延續/均值回歸/排名跳升等）
  Step 3：對每個訊號回測「預測下週強勢族群」的準確率
  Step 4：用最佳預測訊號 + V42 雙重過濾，回測 14 日 +10% 命中率
  Step 5：對比 V42 單獨 vs V42+族群過濾，看命中率是否真提升

目標：證明「族群輪動預測 + V42」比「V42 單獨」命中率更高
"""
import os, sys, pickle, json, statistics, calendar
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sector_analyzer import (
    fetch_all_industries, compute_subsector_strength, SUBSECTORS
)
from backtest_harness import evaluate_stock_offline, forward_return

CACHE_PRICE = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/price_data.pkl")
CACHE_INDEX = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/index_data.pkl")
SECTOR_HISTORY = "/tmp/sector_history.json"


# ─────────────────────────────────────────────────────────────
# Step 1：建立 2 年子族群歷史
# ─────────────────────────────────────────────────────────────
def build_sector_history(test_dates, pc, industries):
    """對每個日期計算子族群 ranking，存成歷史 dict"""
    history = {}
    for i, date in enumerate(test_dates):
        ranking = compute_subsector_strength(pc, industries, cutoff_date=date)
        # 簡化儲存：只存關鍵欄位
        simple = {}
        for s in ranking:
            simple[s["subsector"]] = {
                "rank": s["rank"],
                "ret3":  s.get("median_ret_3d", 0),
                "ret5":  s["median_ret_5d"],
                "ret10": s.get("median_ret_10d", 0),
                "ret20": s["median_ret_20d"],
                "accel": s["acceleration"],
                "momentum_change": s.get("momentum_change", 0),
                "vol_burst": s.get("vol_burst", 1),
                "rotation": s.get("rotation", ""),
                "members": s["members"],
                "max_ret20": s.get("max_ret_20d", 0),
            }
        history[date] = simple
        print(f"  [{i+1}/{len(test_dates)}] {date}：{len(simple)} 子族群")
    with open(SECTOR_HISTORY, "w") as f:
        json.dump(history, f, ensure_ascii=False)
    print(f"📁 已存 {SECTOR_HISTORY}")
    return history


# ─────────────────────────────────────────────────────────────
# Step 2：定義輪動預測訊號（在日期 D 預測 D+5 強勢族群）
# ─────────────────────────────────────────────────────────────
PREDICTION_SIGNALS = {
    # 訊號 1：動能延續 — Top 5 + 加速度高 → 下週繼續強
    "S1_momentum_top5_accelerating": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["rank"] <= 5 and s["accel"] > 1.0
    ],
    # 訊號 2：排名急升 — 5 日內排名跳升 > 3 名
    "S2_rank_jump": lambda d_today, d_5dago: [
        sub for sub in d_today
        if d_5dago and sub in d_5dago and (d_5dago[sub]["rank"] - d_today[sub]["rank"]) >= 3
    ],
    # 訊號 3：絕對強勢 — 20日 > +5% 且 5日 > 0
    "S3_strong_absolute": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["ret20"] > 5 and s["ret5"] > 0
    ],
    # 訊號 4：加速反轉 — 上週弱 + 本週急升（資金切換進場）
    "S4_reversal_entry": lambda d_today, d_5dago: [
        sub for sub in d_today
        if d_5dago and sub in d_5dago
        and d_5dago[sub]["rank"] >= 20 and d_today[sub]["rank"] <= 10
    ],
    # 訊號 5：頂端鞏固 — Top 3 + 5d 動能 > 20d 動能
    "S5_top3_strengthening": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["rank"] <= 3 and s["ret5"] / 5 > s["ret20"] / 20
    ],
    # 訊號 6：穩定 Top 7 (no acceleration filter)
    "S6_stable_top7": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["rank"] <= 7
    ],
    # 訊號 7：均值回歸 — 連續弱勢 (上週 bottom + 本週 bottom) 預計反彈
    "S7_oversold_bounce": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["rank"] >= 40 and s["ret5"] > -2  # 跌不動了
    ],
    # 訊號 8：雙重確認 — Top 5 + 加速度 + 5d 也是正
    "S8_double_confirm": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s["rank"] <= 5 and s["accel"] > 0.8 and s["ret5"] > 0
    ],
    # ─── 寬鬆過濾（V42 進場多在中段族群，避免要求太嚴）─────
    # S9：排除真正壞的（資金跑路/急殺）— 最少限制
    "S9_not_falling": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items()
        if s.get("rotation","") not in ("🔻 急殺", "💸 資金跑路")
    ],
    # S10：Top 15 (前 30% 族群)
    "S10_top15": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items() if s["rank"] <= 15
    ],
    # S11：20日中位 > 0
    "S11_ret20_positive": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items() if s.get("ret20", 0) > 0
    ],
    # S12：10日中位 > 0
    "S12_ret10_positive": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items() if s.get("ret10", 0) > 0
    ],
    # S13：5日中位 > -2%（不嚴重失血）
    "S13_not_bleeding": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items() if s.get("ret5", 0) > -2
    ],
    # S14：Top 20（更寬）
    "S14_top20": lambda d_today, d_5dago: [
        sub for sub, s in d_today.items() if s["rank"] <= 20
    ],
}


# ─────────────────────────────────────────────────────────────
# Step 3：驗證每個訊號的「下週命中率」
# ─────────────────────────────────────────────────────────────
def validate_signal(signal_name, signal_fn, history, dates_sorted):
    """
    對每對連續週 (D_today, D_next_week)：
      - 用 D_today 資料預測強勢族群
      - 看 D_next_week 時這些族群的「實際 5 日 ret」是否 > 0
    回傳：命中率、平均報酬、樣本數
    """
    results = []
    for i in range(len(dates_sorted) - 1):
        d_today = dates_sorted[i]
        d_next  = dates_sorted[i + 1]
        d_5dago = dates_sorted[i - 1] if i >= 1 else None

        today_sectors = history[d_today]
        next_sectors  = history[d_next]
        prev_sectors  = history.get(d_5dago) if d_5dago else None

        predicted = signal_fn(today_sectors, prev_sectors)
        if not predicted: continue

        # 看 next_week 該族群實際 5 日 ret（也就是「下週的」5日 ret）
        for sub in predicted:
            if sub in next_sectors:
                actual_5d = next_sectors[sub]["ret5"]
                results.append(actual_5d)

    if not results:
        return {"name": signal_name, "n": 0, "no_data": True}
    n = len(results)
    hit = sum(1 for r in results if r > 0)
    hit_strong = sum(1 for r in results if r > 2)
    avg = statistics.mean(results)
    med = statistics.median(results)
    return {
        "name": signal_name,
        "n": n,
        "hit_rate_positive": hit / n * 100,
        "hit_rate_strong":   hit_strong / n * 100,
        "avg_5d_ret":  round(avg, 2),
        "med_5d_ret":  round(med, 2),
    }


# ─────────────────────────────────────────────────────────────
# Step 4-5：V42 + 最佳預測訊號 → 真實命中率提升
# ─────────────────────────────────────────────────────────────
def v42_match(e):
    return (e["MA5"] > e["MA20"]
            and e["MA20"] > (e["MA60"] or 0)
            and e["ma20_slope"] > 0.025
            and e["VB"] >= 1.3
            and not e["doji_top"]
            and not e["cons_down"]
            and e["rsi14"] < 80
            and e["BR"] > 0.4
            and e["RS"] > 0.10
            and e["C"] > e["MA20"] * 1.02
            and e["C"] < e["MA20"] * 1.10)


def backtest_v42_alone_vs_v42_plus_sector(test_dates, pc, industries, idf,
                                            history, signal_fn,
                                            hold_days=14):
    """
    對每個 test_date：
      - 抓所有 V42 命中股
      - V42 alone：紀錄全部
      - V42 + sector filter：只留下「在預測強勢族群內」的
      - 看 14 日後的報酬
    回傳對比統計
    """
    print()
    dates_sorted = sorted(test_dates)

    v42_alone_rets = []
    v42_filtered_rets = []
    v42_filtered_count_by_date = {}

    for i, td in enumerate(dates_sorted):
        if td not in history: continue
        d_today = history[td]
        d_5dago = history.get(dates_sorted[i-1]) if i >= 1 else None
        predicted_strong = set(signal_fn(d_today, d_5dago))

        # 對 pc 內每支股算 V42 + 取 14 日 forward return
        n_v42_alone = 0
        n_v42_filt = 0
        for sid, df in pc.items():
            if df is None or df.empty or len(df) < 60: continue
            ev = evaluate_stock_offline(sid, df, idf, td)
            if not ev: continue
            if not v42_match(ev): continue
            n_v42_alone += 1
            fr = forward_return(df, td, hold_days)
            if not fr: continue
            v42_alone_rets.append(fr["ret_pct"])
            # 多重歸屬：任一族群在預測強勢清單中即算過濾通過
            from sector_analyzer import classify_stock_all
            all_subs = classify_stock_all(sid, industries.get(sid, {}).get("industry", ""))
            if any(s in predicted_strong for s in all_subs):
                v42_filtered_rets.append(fr["ret_pct"])
                n_v42_filt += 1
        v42_filtered_count_by_date[td] = (n_v42_alone, n_v42_filt)

    def stats(rets, label):
        if not rets:
            return f"  {label}: 無樣本"
        n = len(rets)
        hit10 = sum(1 for r in rets if r >= 10) / n * 100
        hit20 = sum(1 for r in rets if r >= 20) / n * 100
        hit30 = sum(1 for r in rets if r >= 30) / n * 100
        avg = statistics.mean(rets)
        neg = sum(1 for r in rets if r < 0) / n * 100
        return (f"  {label}: n={n}, +10%={hit10:.1f}%, +20%={hit20:.1f}%, +30%={hit30:.1f}%, "
                f"avg={avg:+.2f}%, 虧損={neg:.1f}%")

    print(f"📊 V42 vs V42+族群過濾 命中率對比")
    print(stats(v42_alone_rets, "V42 單獨"))
    print(stats(v42_filtered_rets, "V42+族群過濾"))
    return v42_alone_rets, v42_filtered_rets, v42_filtered_count_by_date


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    today = datetime.today()
    print("=" * 70)
    print("🔬 資金輪動預測 + V42 命中率提升驗證")
    print("=" * 70)

    # 載入資料
    print("📂 載入 price + index + industries ...")
    with open(CACHE_PRICE, "rb") as f: pc = pickle.load(f)
    with open(CACHE_INDEX, "rb") as f: idx_raw = pickle.load(f)
    idf = next(iter(idx_raw.values())) if isinstance(idx_raw, dict) else idx_raw
    industries = fetch_all_industries()

    # 測試日期：每週一個（過去 2 年）
    test_dates = []
    cutoff = today - timedelta(days=14)
    d = datetime(2024, 6, 17)   # 從 2024/6 開始
    while d <= cutoff:
        test_dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=7)   # 週

    print(f"📅 共 {len(test_dates)} 個週度測試點：{test_dates[0]} ~ {test_dates[-1]}")
    print()

    # Step 1：建立子族群歷史
    print("─" * 70)
    print("Step 1：建立 2 年子族群歷史")
    print("─" * 70)
    if os.path.exists(SECTOR_HISTORY):
        try:
            with open(SECTOR_HISTORY) as f:
                history = json.load(f)
            if set(test_dates).issubset(set(history.keys())):
                print(f"  ✅ 用快取：{len(history)} 個日期")
            else:
                missing = set(test_dates) - set(history.keys())
                print(f"  ⚠️ 補抓 {len(missing)} 個缺失日期...")
                more = build_sector_history(sorted(missing), pc, industries)
                history.update(more)
        except Exception:
            history = build_sector_history(test_dates, pc, industries)
    else:
        history = build_sector_history(test_dates, pc, industries)

    # Step 2-3：驗證每個訊號
    print()
    print("─" * 70)
    print("Step 2-3：驗證 8 種輪動預測訊號的下週命中率")
    print("─" * 70)
    signal_results = []
    for name, fn in PREDICTION_SIGNALS.items():
        r = validate_signal(name, fn, history, sorted(test_dates))
        if r.get("no_data"): continue
        signal_results.append(r)
        flag = "🚀" if r["hit_rate_positive"] >= 65 else ("✅" if r["hit_rate_positive"] >= 55 else "⚠️")
        print(f"{flag} {name:32}  n={r['n']:>4}  正報酬率={r['hit_rate_positive']:>5.1f}%"
              f"  強漲>2%率={r['hit_rate_strong']:>5.1f}%  平均5日={r['avg_5d_ret']:+.2f}%")

    # 找最佳訊號
    signal_results.sort(key=lambda r: r["hit_rate_positive"], reverse=True)
    if not signal_results:
        print("⚠️ 無可用訊號樣本")
        return
    best = signal_results[0]
    print()
    print(f"🏆 最佳預測訊號：{best['name']}（下週正報酬率 {best['hit_rate_positive']:.1f}%）")

    # Step 4：V42 alone vs V42 + 多訊號對比
    print()
    print("─" * 70)
    print(f"Step 4：V42 單獨 vs 各種族群過濾 命中率對比")
    print("─" * 70)

    # 先跑 V42 alone（一次，重用）
    print()
    print("📊 先計算 V42 單獨基準...")
    alone_fn = lambda d_today, d_5dago: list(d_today.keys())  # 不過濾 = 所有族群都允許
    alone_rets, _, _ = backtest_v42_alone_vs_v42_plus_sector(
        test_dates, pc, industries, idf, history, alone_fn, hold_days=14
    )

    # 測寬嚴各種訊號（V42 進場條件多在中段族群，嚴過濾交集太少）
    test_signals = [
        # 嚴
        "S5_top3_strengthening", "S1_momentum_top5_accelerating",
        "S8_double_confirm", "S6_stable_top7",
        # 寬
        "S9_not_falling", "S10_top15", "S11_ret20_positive",
        "S12_ret10_positive", "S13_not_bleeding", "S14_top20",
    ]
    print()
    print("📊 各訊號對 V42 命中率影響：")
    for sig_name in test_signals:
        sig_fn = PREDICTION_SIGNALS[sig_name]
        _, filt_rets, _ = backtest_v42_alone_vs_v42_plus_sector(
            test_dates, pc, industries, idf, history, sig_fn, hold_days=14
        )
        if not filt_rets:
            print(f"  ❌ {sig_name}：無交集（過濾太嚴）")
            continue
        alone_n = len(alone_rets) if alone_rets else 0
        alone_hit10 = sum(1 for r in alone_rets if r >= 10) / alone_n * 100 if alone_n else 0
        filt_n = len(filt_rets)
        filt_hit10  = sum(1 for r in filt_rets  if r >= 10) / filt_n  * 100
        filt_avg = statistics.mean(filt_rets)
        improvement = filt_hit10 - alone_hit10
        flag = "🚀" if improvement >= 5 else ("✅" if improvement > 0 else "❌")
        print(f"  {flag} {sig_name}: n={filt_n}/{alone_n} ({filt_n/alone_n*100:.0f}%)"
              f"  +10%={filt_hit10:.1f}% vs alone {alone_hit10:.1f}%"
              f"  ({improvement:+.1f}pt)  avg={filt_avg:+.2f}%")


if __name__ == "__main__":
    main()
