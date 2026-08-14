"""
擴大監測池：把 price_data.pkl 從 102 支擴到全 TWSE+TPEX 1080 支
========================================================
策略：
  1) 取得官方上市+上櫃清單（合計約 1080 支）
  2) 用 Yahoo Finance 抓每支 200 日歷史 K 線（單檔 .TW/.TWO）
  3) 已存在 price cache 的跳過（斷點續傳）
  4) 每 30 支存檔一次（避免中斷重來）
  5) 失敗的不算進池子，下次再試

用法：
  python3 expand_universe.py            # 全市場
  python3 expand_universe.py --limit 50 # 只下載前 50 支（測試用）

執行時間預估：1080 支 × 1.5 秒/支 ≈ 27 分鐘
"""
import os, sys, pickle, json, time, argparse
import pandas as pd
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR  = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "price_data.pkl")
STOCK_LIST  = os.path.join(CACHE_DIR, "stock_list_cache.json")
PROGRESS    = os.path.join(CACHE_DIR, "expand_progress.json")


def load_stock_list():
    """從 stock_list_cache.json 載入 TWSE+TPEX 清單"""
    if not os.path.exists(STOCK_LIST):
        raise FileNotFoundError("沒有 stock_list_cache.json，請先跑 run_screener.sh 至少一次")
    with open(STOCK_LIST) as f:
        data = json.load(f)
    sd = data.get("data", {})

    # 補上櫃（如果 TPEX endpoint 之前失敗）
    SESSION = requests.Session()
    SESSION.verify = False
    SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
    try:
        r = SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10)
        added = 0
        for item in r.json():
            s = str(item.get("SecuritiesCompanyCode", "")).strip()
            n = item.get("CompanyAbbreviation", "")
            if s.isdigit() and len(s) == 4 and s not in sd:
                sd[s] = {"name": n, "market": "tpex"}
                added += 1
        if added:
            print(f"  ➕ 從 TPEX 補進 {added} 支上櫃股")
    except Exception as e:
        print(f"  ⚠️ TPEX endpoint 失敗：{e}（用備援）")
        # 備援：常見上櫃股代碼前綴 3、4、5、6、8（許多上櫃）
        # 也可以從 yfinance.utils.get_taiwan_stocks 等套件取，先略

    return sd


def fetch_yahoo_single(sid, days=200):
    """抓單一支 .TW 或 .TWO"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    for suffix in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(sid + suffix)
            ydf = tk.history(period=f"{days}d", auto_adjust=False)
            if ydf is None or len(ydf) < 20:
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
    ap.add_argument("--limit", type=int, default=None, help="只下載前 N 支")
    ap.add_argument("--retry-failed", action="store_true", help="重試之前失敗的")
    args = ap.parse_args()

    print("=" * 60)
    print("📡 擴大監測池 — 全 TWSE+TPEX 下載")
    print("=" * 60)

    sd = load_stock_list()
    print(f"📊 全市場：{len(sd)} 支")

    pc = {}
    if os.path.exists(PRICE_CACHE):
        with open(PRICE_CACHE, "rb") as f:
            pc = pickle.load(f)
    print(f"📦 已快取：{len(pc)} 支")

    # 載入進度（記錄失敗的 sid，避免重複嘗試）
    progress = {"failed": [], "completed": []}
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            progress = json.load(f)

    failed_set = set(progress.get("failed", []))

    # 篩出要下載的 sid
    to_fetch = []
    for sid in sorted(sd.keys()):
        if sid in pc:
            continue
        if sid in failed_set and not args.retry_failed:
            continue
        to_fetch.append(sid)

    if args.limit:
        to_fetch = to_fetch[:args.limit]

    print(f"⏬ 待下載：{len(to_fetch)} 支")
    print(f"⏱️ 預估時間：{len(to_fetch) * 1.5 / 60:.1f} 分鐘")
    print()

    t_start = time.time()
    n_ok = 0
    n_fail = 0
    for i, sid in enumerate(to_fetch, 1):
        name = sd[sid].get("name", "")
        df = fetch_yahoo_single(sid, days=200)
        if df is not None and len(df) >= 20:
            pc[sid] = df
            n_ok += 1
            mark = "✅"
        else:
            failed_set.add(sid)
            n_fail += 1
            mark = "❌"

        elapsed = time.time() - t_start
        eta = (elapsed / i) * (len(to_fetch) - i)
        print(f"  [{i:>4}/{len(to_fetch)}] {mark} {sid} {name}（已成 {n_ok} 失 {n_fail}，ETA {eta/60:.1f} min）",
              end="\r", flush=True)

        # 每 30 支存一次，避免中斷重來
        if i % 30 == 0:
            with open(PRICE_CACHE, "wb") as f:
                pickle.dump(pc, f)
            progress["failed"] = sorted(failed_set)
            with open(PROGRESS, "w") as f:
                json.dump(progress, f)

    # 最終存檔
    with open(PRICE_CACHE, "wb") as f:
        pickle.dump(pc, f)
    progress["failed"] = sorted(failed_set)
    with open(PROGRESS, "w") as f:
        json.dump(progress, f)

    print()
    print()
    print("=" * 60)
    print(f"✅ 完成！cache 從 {len(pc) - n_ok} → {len(pc)} 支")
    print(f"   本次成功 {n_ok}、失敗 {n_fail}")
    print(f"   累計失敗 (Yahoo 抓不到): {len(failed_set)}")
    print(f"   總耗時：{(time.time() - t_start)/60:.1f} 分鐘")
    print("=" * 60)


if __name__ == "__main__":
    main()
