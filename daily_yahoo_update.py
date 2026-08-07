"""
每日 K 線增量更新（Yahoo Finance）
========================================
邏輯：
  - 對 price_data.pkl 內每支股，檢查最新日期
  - 若 < 今天，從「最後日期+1」抓到今天
  - 用 pd.concat 接續到既有資料（永久保留歷史）
  - 失敗的不影響其他股

優勢 vs screener 內建的 incremental_update：
  - 用 yfinance 統一抓（含上市/上櫃/KY股，1966 支全 covered）
  - 速度更快（單檔 API，無 TWSE/TPEX API rate limit）
  - 失敗回滾不會破壞 cache

用法：
  python3 daily_yahoo_update.py          # 全部 1966 支
  python3 daily_yahoo_update.py --limit 50

執行時間：~10-15 分鐘（1966 支 × 0.4 秒/支）
建議排程：每日 14:00 跑（台股收盤 13:30 後）
"""
import os, sys, pickle, json, time, argparse
from datetime import datetime, timedelta
import pandas as pd

BASE_DIR    = os.path.expanduser("~/Desktop/Stock Selection Strategy")
CACHE_DIR   = os.path.join(BASE_DIR, "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "price_data.pkl")


def fetch_recent(sid, since_date_str):
    """從 since_date 隔日抓到今天的 K 棒"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    since_dt = datetime.strptime(since_date_str, "%Y-%m-%d")
    start_dt = since_dt + timedelta(days=1)
    today_dt = datetime.today()
    if start_dt.date() > today_dt.date():
        return pd.DataFrame()   # 已是最新，無需 fetch

    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(sid + suffix)
            ydf = tk.history(start=start_dt.strftime("%Y-%m-%d"),
                             end=(today_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
                             auto_adjust=False)
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
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force-refetch-last", action="store_true",
                    help="強制重抓「最後一天」（如果今日盤中跑且要拿盤中價）")
    args = ap.parse_args()

    print("=" * 60)
    print(f"📡 每日 K 線增量更新（Yahoo Finance）")
    print(f"   今日：{datetime.today().strftime('%Y-%m-%d')}")
    print("=" * 60)

    if not os.path.exists(PRICE_CACHE):
        print("❌ price_data.pkl 不存在，請先跑 expand_universe.py")
        return
    with open(PRICE_CACHE, "rb") as f:
        pc = pickle.load(f)
    print(f"📦 cache 內有 {len(pc)} 支")

    sids = list(pc.keys())
    if args.limit:
        sids = sids[:args.limit]

    today_str = datetime.today().strftime("%Y-%m-%d")
    now_hm = int(datetime.now().strftime("%H%M"))
    # ★ 自動偵測「收盤後跑」（14:00 之後）→ 強制覆寫今日 K 棒
    # 解決盤中誤觸發寫入盤中價污染 cache 的問題
    auto_force = (now_hm >= 1400) and (datetime.today().weekday() < 5)
    force_today = args.force_refetch_last or auto_force
    if auto_force:
        print(f"  ⏰ 偵測為收盤後（{now_hm}），強制覆寫今日 K 棒（防止盤中價污染）")

    n_updated = 0
    n_skipped = 0
    n_failed = 0
    n_overwritten = 0   # 強制覆寫的當日 K 棒數
    n_new_bars = 0
    t_start = time.time()

    for i, sid in enumerate(sids, 1):
        df = pc.get(sid)
        if df is None or df.empty:
            n_failed += 1
            continue
        last_date = df["date"].iloc[-1]

        # ★ 收盤後強制覆寫今日：移除 cache 內今日（可能是盤中價）後重抓
        force_overwrite_today = force_today and last_date == today_str
        if force_overwrite_today:
            df = df[df["date"] < today_str].reset_index(drop=True)
            pc[sid] = df
            if not df.empty:
                last_date = df["date"].iloc[-1]
            else:
                last_date = (datetime.today() - timedelta(days=7)).strftime("%Y-%m-%d")

        since = last_date

        if not force_overwrite_today and last_date >= today_str:
            n_skipped += 1
            elapsed = time.time() - t_start
            eta = (elapsed / i) * (len(sids) - i) if i > 0 else 0
            print(f"  [{i:>4}/{len(sids)}] {sid} 已最新 ({last_date})，更新 {n_updated}, ETA {eta/60:.1f}min", end="\r")
            continue

        new_df = fetch_recent(sid, since)
        if new_df is None:
            n_failed += 1
        elif new_df.empty:
            n_skipped += 1
        else:
            existing_dates = set(df["date"].astype(str).tolist())
            new_df = new_df[~new_df["date"].isin(existing_dates)]
            if not new_df.empty:
                pc[sid] = pd.concat([df, new_df], ignore_index=True)
                pc[sid] = pc[sid].drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
                n_new_bars += len(new_df)
                n_updated += 1
                if force_overwrite_today and today_str in new_df["date"].values:
                    n_overwritten += 1

        elapsed = time.time() - t_start
        eta = (elapsed / i) * (len(sids) - i) if i > 0 else 0
        print(f"  [{i:>4}/{len(sids)}] {sid} 更新 {n_updated}, 失敗 {n_failed}, ETA {eta/60:.1f}min", end="\r")

        # 每 100 支存檔
        if i % 100 == 0:
            with open(PRICE_CACHE, "wb") as f:
                pickle.dump(pc, f)

    # 最終存檔
    with open(PRICE_CACHE, "wb") as f:
        pickle.dump(pc, f)

    # ★ 同步更新 meta.json 的 last_update（讓 screener 知道資料已新）
    META_FILE = os.path.join(CACHE_DIR, "meta.json")
    try:
        meta = {}
        if os.path.exists(META_FILE):
            with open(META_FILE, "r") as f:
                meta = json.load(f)
        # 找 cache 內最新一筆日期
        all_latest = []
        for df in pc.values():
            if df is not None and not df.empty:
                try:
                    all_latest.append(str(df["date"].iloc[-1]))
                except Exception:
                    pass
        if all_latest:
            latest = max(all_latest)
            meta["last_update"] = latest
            with open(META_FILE, "w") as f:
                json.dump(meta, f, ensure_ascii=False)
            print(f"  📝 meta.last_update 已更新為 {latest}")
    except Exception as e:
        print(f"  ⚠️ meta.json 更新失敗：{e}")

    print()
    print("=" * 60)
    print(f"✅ 完成！耗時 {(time.time()-t_start)/60:.1f} 分鐘")
    print(f"   ✓ 已更新 {n_updated} 支（共新增 {n_new_bars} 根 K 棒）")
    print(f"   • 已是最新 {n_skipped} 支")
    print(f"   ✗ 失敗 {n_failed} 支")
    print("=" * 60)


if __name__ == "__main__":
    main()
