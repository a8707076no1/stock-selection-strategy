"""
台股選股程式
條件：
1. 10日均線 / 半年線（120日均線）介於 0.95 ~ 1.05（股價在盤整區）
2. 近兩天投信買超（連續買入）
3. 今日成交量 >= 月均量（20日均量）× 2

使用方式：
1. 安裝套件：pip install requests pandas
2. 執行：python3 taiwan_stock_screener.py
"""

import requests
import pandas as pd
import time
from datetime import datetime, timedelta

# ── 設定 ──────────────────────────────────────────────
START_DATE = (datetime.today() - timedelta(days=180)).strftime("%Y-%m-%d")
END_DATE   = datetime.today().strftime("%Y-%m-%d")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

# 若有 FinMind 帳號可填入 token 提升 API 限制
# 免費版每天有次數限制，建議申請免費帳號取得 token
# 申請網址：https://finmindtrade.com/
TOKEN = ""  # 填入你的 token，或留空使用匿名模式


# ── 工具函式 ──────────────────────────────────────────

def get_params(dataset, stock_id=None):
    params = {
        "dataset": dataset,
        "start_date": START_DATE,
        "end_date": END_DATE,
    }
    if stock_id:
        params["data_id"] = stock_id
    if TOKEN:
        params["token"] = TOKEN
    return params


def fetch(dataset, stock_id=None, retries=3):
    """呼叫 FinMind API，失敗自動重試"""
    for i in range(retries):
        try:
            r = requests.get(FINMIND_API, params=get_params(dataset, stock_id), timeout=15)
            data = r.json()
            if data.get("status") == 200 and data.get("data"):
                return pd.DataFrame(data["data"])
        except Exception as e:
            print(f"  [{stock_id}] 第{i+1}次失敗：{e}")
            time.sleep(2)
    return pd.DataFrame()


# ── 取得上市櫃股票清單 ────────────────────────────────

def get_stock_list():
    """取得台灣上市＋上櫃股票清單（只取一般股票，排除 ETF、特別股）"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockInfo"}
    if TOKEN:
        params["token"] = TOKEN

    r = requests.get(url, params=params, timeout=15)
    data = r.json()
    df = pd.DataFrame(data["data"])

    # 只保留4碼股票代碼（一般股票）
    df = df[df["stock_id"].str.match(r"^\d{4}$")]
    print(f"✅ 取得股票清單：共 {len(df)} 支")
    return df["stock_id"].tolist()


# ── 主要篩選邏輯 ──────────────────────────────────────

def check_stock(stock_id):
    """
    對單一股票進行三項條件篩選
    回傳 True/False 及說明
    """
    # 1. 抓取股價資料
    price_df = fetch("TaiwanStockPrice", stock_id)
    if price_df.empty or len(price_df) < 130:
        return False, None

    price_df = price_df.sort_values("date").reset_index(drop=True)
    price_df["close"] = pd.to_numeric(price_df["Trading_Money"], errors="coerce") / \
                        pd.to_numeric(price_df["Trading_Volume"], errors="coerce")

    # 用收盤價欄位（FinMind 欄位名稱為 close）
    if "close" not in price_df.columns:
        price_df["close"] = pd.to_numeric(price_df.get("Close", 0), errors="coerce")

    price_df["volume"] = pd.to_numeric(price_df.get("Trading_Volume", 0), errors="coerce")

    # 計算均線
    price_df["ma10"]  = price_df["close"].rolling(10).mean()
    price_df["ma120"] = price_df["close"].rolling(120).mean()
    price_df["ma20_vol"] = price_df["volume"].rolling(20).mean()

    latest = price_df.iloc[-1]

    # 條件一：10日線 / 半年線 在 0.95 ~ 1.05
    if pd.isna(latest["ma10"]) or pd.isna(latest["ma120"]) or latest["ma120"] == 0:
        return False, None

    ratio = latest["ma10"] / latest["ma120"]
    if not (0.95 <= ratio <= 1.05):
        return False, None

    # 條件三：今日量 >= 月均量 × 2
    if pd.isna(latest["ma20_vol"]) or latest["ma20_vol"] == 0:
        return False, None

    vol_ratio = latest["volume"] / latest["ma20_vol"]
    if vol_ratio < 2.0:
        return False, None

    # 2. 抓取投信買賣資料
    inst_df = fetch("TaiwanStockInstitutionalInvestorsBuySell", stock_id)
    if inst_df.empty:
        return False, None

    inst_df = inst_df[inst_df["name"] == "投信"]
    inst_df = inst_df.sort_values("date").reset_index(drop=True)

    if len(inst_df) < 2:
        return False, None

    # 條件二：近兩天投信買超（buy > sell）
    last2 = inst_df.tail(2)
    last2["net"] = pd.to_numeric(last2["buy"], errors="coerce") - \
                   pd.to_numeric(last2["sell"], errors="coerce")

    if not (last2["net"] > 0).all():
        return False, None

    # 全部通過！
    info = {
        "股票代碼": stock_id,
        "10日線/半年線比值": round(ratio, 4),
        "量比(今日/月均)": round(vol_ratio, 2),
        "投信近2日買超(張)": int(last2["net"].sum()),
        "今日收盤": round(float(latest["close"]), 2),
    }
    return True, info


# ── 主程式 ────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  台股選股程式啟動")
    print(f"  日期範圍：{START_DATE} ~ {END_DATE}")
    print("=" * 50)

    stock_list = get_stock_list()
    results = []
    total = len(stock_list)

    for i, sid in enumerate(stock_list):
        print(f"  檢查 {sid} ({i+1}/{total})...", end="\r")
        try:
            passed, info = check_stock(sid)
            if passed:
                results.append(info)
                print(f"\n✅ 符合條件：{sid} | 比值={info['10日線/半年線比值']} | 量比={info['量比(今日/月均)']}x | 投信={info['投信近2日買超(張)']}張")
        except Exception as e:
            pass

        # 避免 API 請求過快被限制
        time.sleep(0.5)

    print("\n" + "=" * 50)
    print(f"✅ 篩選完成！共找到 {len(results)} 支符合條件的股票")
    print("=" * 50)

    if results:
        df_result = pd.DataFrame(results)
        df_result = df_result.sort_values("量比(今日/月均)", ascending=False)
        print("\n📊 符合條件的股票：")
        print(df_result.to_string(index=False))

        # 儲存結果
        output_file = f"選股結果_{datetime.today().strftime('%Y%m%d')}.csv"
        df_result.to_csv(output_file, index=False, encoding="utf-8-sig")
        print(f"\n💾 結果已儲存至：{output_file}")
    else:
        print("\n今日無符合條件的股票。")


if __name__ == "__main__":
    main()
