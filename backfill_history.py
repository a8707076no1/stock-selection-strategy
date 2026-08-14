"""
歷史回溯（一次性）— 把 cache 內所有股票的歷史補到指定起始日
========================================
邏輯：
  - 對每支股，若 cache 最早日 > 目標起始日 → 補抓 [目標日, cache最早日]
  - 既有資料保留，新抓的接在前面（pd.concat + 排序 + 去重）

用法：
  python3 backfill_history.py --start 2025-01-01   # 預設
  python3 backfill_history.py --start 2024-01-01   # 兩年回溯
  python3 backfill_history.py --start 2024-01-01 --limit 100  # 測試

執行時間：1966 支 × ~0.8 秒/支 ≈ 25 分鐘
建議：跑一次，之後不用再跑（用 daily_yahoo_update.py 增量即可）
"""
import os, sys, pickle, time, argparse
from datetime import datetime, timedelta
import pandas as pd

BASE_DIR    = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
CACHE_DIR   = os.path.join(BASE_DIR, "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "price_data.pkl")


def fetch_range(sid, start_str, end_str):
    """抓 [start, end) 之間的 K 棒"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(sid + suffix)
            ydf = tk.history(start=start_str, end=end_str, auto_adjust=False)
            if ydf is None or len(ydf) == 0:
                continue
            if hasattr(ydf.columns, "nlevels") and ydf.columns.nlevels > 1:
                ydf.columns = [c[0] for c in ydf.columns]
            need = {"Open","High","Low","Close","Volume"}
            if not need.issubset(set(ydf.columns)):
                continue
            df = pd.DataFrame({
                "date":   [d.strftime("%Y-%m-%d") for d in ydf.index],
                "open":   ydf["Open"].values,
                "high":   ydf["High"].values,
                "low":    ydf["Low"].values,
                "close":  ydf["Close"].values,
                "volume": ydf["Volume"].values,
            })
            return df
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01",
                    help="回溯起始日（預設 2025-01-01）")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    print("=" * 60)
    print(f"📡 歷史回溯到 {args.start}")
    print("=" * 60)

    with open(PRICE_CACHE, "rb") as f:
        pc = pickle.load(f)
    print(f"📦 cache 內有 {len(pc)} 支")

    sids = list(pc.keys())
    if args.limit:
        sids = sids[:args.limit]

    # 找出需要回溯的 sid
    need_backfill = []
    for sid in sids:
        df = pc.get(sid)
        if df is None or df.empty: continue
        earliest = df["date"].iloc[0]
        if earliest > args.start:
            need_backfill.append((sid, earliest))

    print(f"⏬ 需回溯 {len(need_backfill)} 支（其餘已涵蓋 {args.start}）")

    t_start = time.time()
    n_ok = 0; n_fail = 0; n_new = 0
    for i, (sid, earliest) in enumerate(need_backfill, 1):
        # 抓 [args.start, earliest)
        df_new = fetch_range(sid, args.start, earliest)
        if df_new is None or df_new.empty:
            n_fail += 1
        else:
            existing = pc[sid]
            existing_dates = set(existing["date"].astype(str).tolist())
            df_new = df_new[~df_new["date"].isin(existing_dates)]
            if not df_new.empty:
                pc[sid] = pd.concat([df_new, existing], ignore_index=True)
                pc[sid] = pc[sid].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                n_ok += 1
                n_new += len(df_new)

        elapsed = time.time() - t_start
        eta = (elapsed / i) * (len(need_backfill) - i)
        print(f"  [{i:>4}/{len(need_backfill)}] {sid} 回溯 ✓{n_ok} ✗{n_fail}，新增 {n_new} 根 K 棒，ETA {eta/60:.1f}min",
              end="\r")

        if i % 100 == 0:
            with open(PRICE_CACHE, "wb") as f:
                pickle.dump(pc, f)

    with open(PRICE_CACHE, "wb") as f:
        pickle.dump(pc, f)

    print()
    print()
    print("=" * 60)
    print(f"✅ 完成！耗時 {(time.time()-t_start)/60:.1f} 分鐘")
    print(f"   ✓ 成功回溯 {n_ok} 支（共新增 {n_new} 根 K 棒）")
    print(f"   ✗ 失敗 {n_fail} 支")
    print()
    # 統計新的範圍
    earliest = min((d["date"].iloc[0] for d in pc.values() if d is not None and not d.empty), default="")
    latest = max((d["date"].iloc[-1] for d in pc.values() if d is not None and not d.empty), default="")
    print(f"📊 cache 新範圍：{earliest} ~ {latest}")
    print("=" * 60)


if __name__ == "__main__":
    main()
