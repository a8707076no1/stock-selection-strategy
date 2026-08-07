"""
保險 / 勞退退保價值自動估算（依今天日期 vs 保單年度終結日插值）

資料來源：
- AIA 充裕未來1 — 用戶提供的「基本計劃 - 說明摘要」表（USD）
- 保誠雋升 — 用戶提供的「Future Value Illustration」表（USD）
- 勞工退休金 — 從用戶提供截圖的當前累計值（TWD）

匯率：當日 USD/TWD（yfinance 即時抓，失敗時 fallback 到預設值）
"""
import os, json, time
from datetime import date, timedelta

CACHE_DIR  = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache")
FX_CACHE   = os.path.join(CACHE_DIR, "usdtwd_rate.json")
FX_DEFAULT = 32.0   # 抓不到匯率時用這個

# ── AIA 充裕未來1（USD）─────────────────────────
# 「保單年度終結 → 退保發還金額總額（USD）」
AIA_TABLE_USD = {
    9:  132192,
    10: 158043,
    11: 166931,   # 圖上「166,931」
    12: 178467,
    13: 190836,
    14: 203847,
    15: 217797,
    16: 234018,
    17: 251391,
    18: 270258,
    19: 290373,
    20: 312147,
    21: 335832,
    22: 361072,
    23: 388625,
    24: 418278,
    25: 450181,
    26: 487980,
    27: 525252,
    28: 565417,
    29: 608282,
    30: 654403,
    35: 942085,
    40: 1359744,
    45: 1893602,
    50: 2458750,
    55: 3401405,
    60: 4404449,
    65: 5010210,  # 「@65歲」終結
}
AIA_ANCHOR_YEAR = 10
AIA_ANCHOR_DATE = date(2026, 11, 6)   # 第 10 年保單年度終結日

# ── 保誠雋升（Prudential 雋升）──────────────────
# 「保單年度終結 → Total Surrender Value（USD）」
PRU_TABLE_USD = {
    10: 27307,
    11: 29282,
    12: 30820,
    13: 32641,
    14: 34852,
    15: 37597,
    16: 39873,
    17: 42331,
    18: 45360,
    19: 48350,
    20: 51475,
    25: 73313,
    30: 104325,
    35: 355940,    # @ANB56歲
    40: 494373,    # @ANB61歲
    45: 687575,    # @ANB66歲
    50: 957306,    # @ANB71歲（957,306）
    55: 1334830,   # @ANB76歲
    60: 1863005,   # @ANB81歲
    65: 2602562,   # @ANB86歲
    70: 3637079,   # @ANB91歲
    75: 5085477,   # @ANB96歲
    80: 7109765,   # @ANB101歲
}
PRU_ANCHOR_YEAR = 10
PRU_ANCHOR_DATE = date(2026, 4, 1)


# ── 即時 USD/TWD 匯率（每日快取）────────────────
def get_usd_twd_rate():
    """抓今日 USD/TWD 匯率（yfinance），24 小時快取"""
    # 1) 看快取
    try:
        if os.path.exists(FX_CACHE):
            with open(FX_CACHE) as f:
                d = json.load(f)
            if time.time() - d.get("ts", 0) < 86400:
                return d.get("rate", FX_DEFAULT), d.get("source", "cache")
    except Exception:
        pass

    rate, source = None, "default"
    # 2) 用 yfinance 抓 TWD=X
    try:
        import yfinance as yf
        t = yf.Ticker("TWD=X")
        info = t.history(period="5d")
        if len(info) > 0:
            rate = float(info["Close"].iloc[-1])
            source = "yfinance"
    except Exception:
        pass

    # 3) fallback：用 Yahoo finance API 直接打
    if not rate:
        try:
            import urllib.request
            with urllib.request.urlopen(
                "https://query1.finance.yahoo.com/v8/finance/chart/TWD=X?interval=1d&range=5d",
                timeout=10) as r:
                d = json.loads(r.read())
                rate = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
                source = "yahoo-api"
        except Exception:
            pass

    if not rate or rate < 20 or rate > 50:   # sanity check
        rate, source = FX_DEFAULT, "default"

    # 寫入快取
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(FX_CACHE, "w") as f:
            json.dump({"rate": rate, "source": source, "ts": time.time(),
                       "date": date.today().strftime("%Y-%m-%d")}, f)
    except Exception:
        pass
    return rate, source

# ── 勞工退休金 ─────────────────────────────────────
# 用戶 2026/05/10 截圖：雇主提繳 1,204,255 + 收益 881,645
#                      個人提繳   199,896 + 收益 261,181
LABOR_PENSION_DEFAULT = 2546977  # 1,204,255 + 881,645 + 199,896 + 261,181


# ── 工具函式 ──────────────────────────────────────

def _policy_position(anchor_year, anchor_date, today=None):
    """今天在保單年度上的位置（小數）"""
    today = today or date.today()
    delta = (today - anchor_date).days
    return anchor_year + delta / 365.25


def _interpolate(table, pos):
    """線性插值 / 邊界 clamp"""
    keys = sorted(table.keys())
    if pos <= keys[0]:  return table[keys[0]]
    if pos >= keys[-1]: return table[keys[-1]]
    for i in range(len(keys) - 1):
        if keys[i] <= pos <= keys[i+1]:
            t = (pos - keys[i]) / (keys[i+1] - keys[i])
            return table[keys[i]] + t * (table[keys[i+1]] - table[keys[i]])
    return table[keys[-1]]


def aia_estimate(today=None, usd_twd=None):
    """估算 AIA 充裕未來1 今日退保總額（USD → 用即時匯率換算 TWD）"""
    pos = _policy_position(AIA_ANCHOR_YEAR, AIA_ANCHOR_DATE, today)
    val_usd = _interpolate(AIA_TABLE_USD, pos)
    if usd_twd is None:
        rate, fx_source = get_usd_twd_rate()
    else:
        rate, fx_source = usd_twd, "manual"
    val_twd = val_usd * rate
    today = today or date.today()
    days_to_year10 = (AIA_ANCHOR_DATE - today).days
    if days_to_year10 > 0:
        next_note = f"距第 10 年終結日（{AIA_ANCHOR_DATE.strftime('%Y/%m/%d')}）還有 {days_to_year10} 天"
    else:
        next_note = f"已過第 10 年終結日 {-days_to_year10} 天"
    return {
        "value_twd":   round(val_twd),
        "value_usd":   round(val_usd),
        "policy_year": round(pos, 2),
        "rate":        round(rate, 3),
        "fx_source":   fx_source,
        "note":        f"USD ${val_usd:,.0f} × {rate:.3f} TWD/USD｜{next_note}",
        "currency":    "TWD",
    }


def pru_estimate(today=None, usd_twd=None):
    """估算保誠雋升今日退保總額（USD → 用即時匯率換算 TWD）"""
    pos = _policy_position(PRU_ANCHOR_YEAR, PRU_ANCHOR_DATE, today)
    val_usd = _interpolate(PRU_TABLE_USD, pos)
    if usd_twd is None:
        rate, fx_source = get_usd_twd_rate()
    else:
        rate, fx_source = usd_twd, "manual"
    val_twd = val_usd * rate
    today = today or date.today()
    days_to_year10 = (PRU_ANCHOR_DATE - today).days
    if days_to_year10 > 0:
        next_note = f"距第 10 年終結日（{PRU_ANCHOR_DATE.strftime('%Y/%m/%d')}）還有 {days_to_year10} 天"
    else:
        next_note = f"已過第 10 年終結日 {-days_to_year10} 天"
    return {
        "value_twd":   round(val_twd),
        "value_usd":   round(val_usd),
        "policy_year": round(pos, 2),
        "rate":        round(rate, 3),
        "fx_source":   fx_source,
        "note":        f"USD ${val_usd:,.0f} × {rate:.3f} TWD/USD｜{next_note}",
        "currency":    "TWD",
    }


def labor_pension_default():
    return {"value_twd": LABOR_PENSION_DEFAULT, "note": "用戶 2026/05/10 截圖累計值"}


# ── 勞工保險（老年給付）──────────────────────
# 用戶 2026/05/10 截圖資料：
#   月投保薪資 45,800 元
#   投保年資 17 年 17 天（年資計算迄 = 2026/03/10）
#   ── 用戶持續投保，每天會累加 ──
LABOR_INS_MONTHLY_SALARY = 45800
LABOR_INS_REF_DATE       = date(2026, 3, 10)   # 年資計算迄
LABOR_INS_REF_YEARS      = 17 + 17 / 365.25     # 17 年 17 天 = 17.047 年


def labor_insurance_estimate(today=None, monthly_salary=None):
    """
    勞保老年給付（一次請領）+ 月領年金估算。

    動態計算：以用戶提供的「年資計算迄」為錨點，加上至今經過的天數，
    投保年資每天遞增（因用戶持續投保中）。

    依《勞工保險條例》：
      一次請領月數 = (年資 ≤ 15 年部分 × 1 月) + (超過 15 年部分 × 2 月)，最高 45 月
      月領年金 = max(A 式, B 式)
        A 式：平均月投保薪資 × 年資 × 0.775% + 3,000
        B 式：平均月投保薪資 × 年資 × 1.55%
    """
    today = today or date.today()
    ms = monthly_salary if monthly_salary is not None else LABOR_INS_MONTHLY_SALARY

    elapsed_days = (today - LABOR_INS_REF_DATE).days
    years_continuous = LABOR_INS_REF_YEARS + elapsed_days / 365.25  # 連續年數（含小數）
    years_complete   = int(years_continuous)                         # 完整年數（一次請領用）

    # 一次請領月數（按條例：每滿 1 年才會多計 1/2 個月）
    if years_complete <= 15:
        months = years_complete
    else:
        months = 15 + (years_complete - 15) * 2
    months = min(months, 45)
    lump_sum = ms * months

    # 月領年金（連續計算）
    a = ms * years_continuous * 0.00775 + 3000
    b = ms * years_continuous * 0.0155
    monthly_pension = max(a, b)

    # 距下一個完整年（會跳到 +2 月或 +1 月）的天數
    days_to_next = round(((years_complete + 1) - years_continuous) * 365.25)

    return {
        "value_twd":         round(lump_sum),
        "monthly_pension":   round(monthly_pension),
        "months":            months,
        "monthly_salary":    ms,
        "years_continuous":  round(years_continuous, 2),
        "years_complete":    years_complete,
        "elapsed_days":      elapsed_days,
        "next_year_days":    days_to_next,
        "note":              f"年資 {years_continuous:.2f} 年（自 {LABOR_INS_REF_DATE.strftime('%Y/%m/%d')} +{elapsed_days} 天累積）",
        "monthly_note":      f"一次請領 {months} 月 × {ms:,} = {lump_sum:,}　月領年金 {round(monthly_pension):,}/月　距下一年 {days_to_next} 天",
    }


if __name__ == "__main__":
    print("AIA：", aia_estimate())
    print("保誠：", pru_estimate())
    print("勞退：", labor_pension_default())
