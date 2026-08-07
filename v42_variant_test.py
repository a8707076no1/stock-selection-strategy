"""
V42 變體測試 + 族群過濾互動分析
============================================
方向 A：放寬 V42 進場條件，讓 V42 命中股能落在強勢族群內

測試矩陣：
  6 個 V42 變體 × 4 個族群過濾（含 V42 alone） = 24 個組合
  全部用 2 年歷史回測（98 週、約 200+ 個 V42 命中股）

目標：找到「V42 變體 + 族群過濾」**真正能提升**命中率的組合
"""
import os, sys, pickle, json, statistics
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sector_analyzer import (
    fetch_all_industries, compute_subsector_strength, classify_stock_all
)
from backtest_harness import evaluate_stock_offline, forward_return

CACHE_PRICE = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/price_data.pkl")
CACHE_INDEX = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache/index_data.pkl")
SECTOR_HISTORY = "/tmp/sector_history.json"

# ─────────────────────────────────────────────────────────────
# V42 變體（放寬條件）
# ─────────────────────────────────────────────────────────────
def make_v42(c_upper, rs_min=0.10, slope_min=0.025, vb_min=1.3):
    """產生 V42 變體"""
    def match(e):
        return (e["MA5"] > e["MA20"]
                and e["MA20"] > (e["MA60"] or 0)
                and e["ma20_slope"] > slope_min
                and e["VB"] >= vb_min
                and not e["doji_top"]
                and not e["cons_down"]
                and e["rsi14"] < 80
                and e["BR"] > 0.4
                and e["RS"] > rs_min
                and e["C"] > e["MA20"] * 1.02
                and e["C"] < e["MA20"] * c_upper)
    return match


V42_VARIANTS = {
    "V42_orig (1.02-1.10)":         make_v42(1.10),
    "V42_R1 (1.02-1.15)":           make_v42(1.15),
    "V42_R2 (1.02-1.20)":           make_v42(1.20),
    "V42_R3 (1.02-1.30)":           make_v42(1.30),
    "V42_R4 (R3+RS≥5%)":            make_v42(1.30, rs_min=0.05),
    "V42_R5 (R4+slope≥1.5%)":       make_v42(1.30, rs_min=0.05, slope_min=0.015),
    "V42_R6 (R5+VB≥1.0)":           make_v42(1.30, rs_min=0.05, slope_min=0.015, vb_min=1.0),
    "V42_R7 noUpper (>1.02)":       make_v42(99, rs_min=0.05, slope_min=0.015),
}

# ─────────────────────────────────────────────────────────────
# 族群過濾（5 個從寬到嚴）
# ─────────────────────────────────────────────────────────────
SECTOR_FILTERS = {
    "no_filter":         lambda s: True,
    "S9_not_falling":    lambda s: s.get("rotation","") not in ("🔻 急殺", "💸 資金跑路"),
    "S11_ret20_pos":     lambda s: s.get("ret20", 0) > 0,
    "S12_ret10_pos":     lambda s: s.get("ret10", 0) > 0,
    "S6_top7":           lambda s: s.get("rank", 99) <= 7,
    "S10_top15":         lambda s: s.get("rank", 99) <= 15,
    "S14_top20":         lambda s: s.get("rank", 99) <= 20,
}


def precompute_evals(test_dates, pc, idf, hold_days=14):
    """一次性把所有 (date, sid) 的 evaluate 結果 + forward return + multi-membership cache 起來
    這樣 56 個組合測試只需用 cache filter，不用重算"""
    print("⚡ Precompute all evaluations (one-time)...")
    cache = {}   # (date, sid) -> {ev, fr, subs}
    total = len(test_dates) * len(pc)
    done = 0
    industries_local = fetch_all_industries()
    for td in test_dates:
        for sid, df in pc.items():
            done += 1
            if done % 5000 == 0:
                print(f"   {done}/{total} ({done*100//total}%)", flush=True)
            if df is None or df.empty or len(df) < 60: continue
            ev = evaluate_stock_offline(sid, df, idf, td)
            if not ev: continue
            fr = forward_return(df, td, hold_days)
            if not fr: continue
            subs = classify_stock_all(sid, industries_local.get(sid, {}).get("industry", ""))
            cache[(td, sid)] = (ev, fr["ret_pct"], subs)
    print(f"✅ Cache: {len(cache)} entries")
    return cache


def run_backtest_cached(v42_fn, sector_filter_name, sector_filter_fn,
                         test_dates, eval_cache, history):
    """用 cache 跑單一組合 — 快 100 倍"""
    rets = []
    for td in test_dates:
        if td not in history: continue
        d_today = history[td]
        passed_subs = {sub for sub, s in d_today.items() if sector_filter_fn(s)}
        for sid in (s for (d, s) in eval_cache if d == td):
            ev, ret_pct, subs = eval_cache[(td, sid)]
            if not v42_fn(ev): continue
            if sector_filter_name == "no_filter" or any(s in passed_subs for s in subs):
                rets.append(ret_pct)
    if not rets:
        return None
    n = len(rets)
    return {
        "n": n,
        "hit10": sum(1 for r in rets if r >= 10) / n * 100,
        "hit20": sum(1 for r in rets if r >= 20) / n * 100,
        "hit30": sum(1 for r in rets if r >= 30) / n * 100,
        "avg":   statistics.mean(rets),
        "neg":   sum(1 for r in rets if r < 0) / n * 100,
    }


def main():
    print("=" * 90)
    print("V42 變體 × 族群過濾 完整矩陣回測（2 年 / 98 週）")
    print("=" * 90)

    today = datetime.today()
    test_dates = []
    cutoff = today - timedelta(days=14)
    d = datetime(2024, 6, 17)
    while d <= cutoff:
        test_dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=7)

    print(f"📅 {len(test_dates)} 個週度測試點")

    # 載入資料
    print("📂 載入 ...")
    with open(CACHE_PRICE, "rb") as f: pc = pickle.load(f)
    with open(CACHE_INDEX, "rb") as f: idx_raw = pickle.load(f)
    idf = next(iter(idx_raw.values())) if isinstance(idx_raw, dict) else idx_raw
    industries = fetch_all_industries()
    with open(SECTOR_HISTORY) as f:
        history = json.load(f)
    print(f"  pc={len(pc)} stocks, history={len(history)} weeks\n")

    # 一次 precompute
    eval_cache = precompute_evals(test_dates, pc, idf, hold_days=14)

    # 跑矩陣（cache 版本，快 100 倍）
    print()
    print("⚡ Matrix backtest (56 combos)...")
    results = []
    for v42_name, v42_fn in V42_VARIANTS.items():
        print(f"  {v42_name} ...", flush=True)
        for filt_name, filt_fn in SECTOR_FILTERS.items():
            r = run_backtest_cached(v42_fn, filt_name, filt_fn,
                                     test_dates, eval_cache, history)
            if r is None or r["n"] < 5: continue
            results.append({
                "v42": v42_name, "filt": filt_name, **r,
            })

    # 印出
    print(f"{'V42 變體':>26} | {'族群過濾':>16} | {'樣本':>5} | {'+10%':>6} | {'+20%':>6} | {'+30%':>6} | {'平均':>7} | {'虧損':>6}")
    print("-" * 105)
    # 排序：先按 hit10 降序
    results.sort(key=lambda x: -x["hit10"])

    # 印出每個 V42 變體的最佳組合
    seen_v42 = set()
    print("\n🏆 各 V42 變體的最佳組合：")
    print("-" * 105)
    for r in results:
        if r["v42"] in seen_v42: continue
        seen_v42.add(r["v42"])
        flag = "🚀" if r["hit10"] >= 40 else ("✅" if r["hit10"] >= 30 else "⚠️")
        print(f"{flag} {r['v42']:>26} | {r['filt']:>16} | {r['n']:>5} | {r['hit10']:>5.1f}% | {r['hit20']:>5.1f}% | {r['hit30']:>5.1f}% | {r['avg']:>+5.1f}% | {r['neg']:>5.1f}%")

    # 印出全部 top 15
    print("\n📊 整體 Top 15 組合（按 +10% 命中率）：")
    print("-" * 105)
    for r in results[:15]:
        flag = "🚀" if r["hit10"] >= 40 else ("✅" if r["hit10"] >= 30 else "⚠️")
        print(f"{flag} {r['v42']:>26} | {r['filt']:>16} | {r['n']:>5} | {r['hit10']:>5.1f}% | {r['hit20']:>5.1f}% | {r['hit30']:>5.1f}% | {r['avg']:>+5.1f}% | {r['neg']:>5.1f}%")

    # 找冠軍
    if results:
        best = results[0]
        print()
        print("=" * 90)
        print(f"🏆 冠軍組合：{best['v42']} + {best['filt']}")
        print(f"   +10% 命中率：{best['hit10']:.1f}%（樣本 {best['n']}）")
        print(f"   +20% 命中率：{best['hit20']:.1f}%")
        print(f"   +30% 命中率：{best['hit30']:.1f}%")
        print(f"   平均報酬：{best['avg']:+.2f}%，虧損率：{best['neg']:.1f}%")

    # 存報告
    with open("/tmp/v42_variant_report.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📁 報告存：/tmp/v42_variant_report.json")


if __name__ == "__main__":
    main()
