"""
資金輪動脈絡 / 順序性 / 週期性 分析
============================================
科學假設：資金在子族群之間有「順序輪動」現象
  例：晶圓代工強 → ABF載板 → AI ODM → 散熱液冷 → 重電 → ...
  即 「A 領先 B」存在可預測的 lag 結構

方法：
  1. 對每對 (A, B) 子族群，計算「lag-N 相關係數」
     corr(A[t-N], B[t])，N = 1, 2, 3, 4 週
  2. 找出每對的最佳 lag 與 max_correlation
  3. 建立「輪動圖譜」：A → {B1: lag=N1, corr=C1}, ...
  4. 驗證：用過去資料預測，命中率如何

輸出：
  - rotation_chain.json: 輪動圖譜
  - rotation_validation: 預測命中率報告
"""
import os, sys, json, statistics, pickle
from datetime import datetime, timedelta
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sector_analyzer import SUBSECTORS

SECTOR_HISTORY = "/tmp/sector_history.json"
OUTPUT_CHAIN = "/tmp/rotation_chain.json"
MAX_LAG_WEEKS = 4   # 最多看 4 週的 lag


# ─────────────────────────────────────────────────────────────
# 載入歷史
# ─────────────────────────────────────────────────────────────
def load_history():
    if not os.path.exists(SECTOR_HISTORY):
        raise FileNotFoundError("先跑 sector_rotation_validator.py 建立 sector_history.json")
    with open(SECTOR_HISTORY) as f:
        return json.load(f)


def history_to_matrix(history, metric="ret20"):
    """把 history 轉成 {sector: [v_t0, v_t1, ...]} time series"""
    dates_sorted = sorted(history.keys())
    series = {}
    for date in dates_sorted:
        for sub, s in history[date].items():
            series.setdefault(sub, []).append(s.get(metric, 0))
    # 只保留長度 = dates_sorted 的（避免缺資料）
    L = len(dates_sorted)
    series = {k: v for k, v in series.items() if len(v) == L}
    return series, dates_sorted


# ─────────────────────────────────────────────────────────────
# Lag 相關性矩陣
# ─────────────────────────────────────────────────────────────
def pearson(x, y):
    if len(x) != len(y) or len(x) < 5: return 0
    mx, my = sum(x)/len(x), sum(y)/len(y)
    sxx = sum((xi-mx)**2 for xi in x)
    syy = sum((yi-my)**2 for yi in y)
    sxy = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
    denom = (sxx * syy) ** 0.5
    return sxy / denom if denom > 0 else 0


def cross_corr_lag(series_a, series_b, lag):
    """A 領先 B 多少 lag 期的相關係數
    Corr(A[t-lag], B[t])，lag > 0 = A 領先 B"""
    if lag <= 0:
        return pearson(series_a, series_b)
    if len(series_a) <= lag: return 0
    a_lagged = series_a[:-lag]
    b_now    = series_b[lag:]
    return pearson(a_lagged, b_now)


def build_rotation_chain(series, min_corr=0.3, min_members=3):
    """
    對每對 (A, B)：找最佳 lag (1-4 週) 使 Corr(A[t-lag], B[t]) 最大
    回傳：{A: [(B, best_lag, best_corr), ...]}
    """
    sectors = list(series.keys())
    chain = {}
    for a in sectors:
        edges = []
        for b in sectors:
            if a == b: continue
            best_corr = 0; best_lag = 0
            for lag in range(1, MAX_LAG_WEEKS + 1):
                c = cross_corr_lag(series[a], series[b], lag)
                if c > best_corr:
                    best_corr = c
                    best_lag = lag
            if best_corr >= min_corr:
                edges.append((b, best_lag, round(best_corr, 3)))
        # 按相關性排序，取前 5 強
        edges.sort(key=lambda x: -x[2])
        chain[a] = edges[:5]
    return chain


# ─────────────────────────────────────────────────────────────
# 預測驗證：用脈絡圖譜預測「下週領頭族群」
# ─────────────────────────────────────────────────────────────
def predict_using_chain(history, chain, dates_sorted, top_k_current=3):
    """
    對每個歷史日 D：
      - 找出當下 Top K 強勢族群
      - 用 chain 查它們的「下一棒」（B, lag=1 的 edges）
      - 預測 D+lag 時這些 B 族群會強
    驗證：D+lag 時這些 B 是否真的進入 Top 10？
    """
    results_lag1 = []
    results_lag2 = []

    for i, d_today in enumerate(dates_sorted[:-MAX_LAG_WEEKS]):
        today_sect = history[d_today]
        if not today_sect: continue
        # 當下 Top K
        top_now = sorted(today_sect.items(), key=lambda x: -x[1]["ret20"])[:top_k_current]
        top_subs = [t[0] for t in top_now]

        # 每個 top 族群用 chain 找下一棒
        for sub in top_subs:
            edges = chain.get(sub, [])
            for next_sub, lag, corr in edges[:3]:   # 取 chain 中前 3 條最強連結
                future_date = dates_sorted[i + lag] if i + lag < len(dates_sorted) else None
                if not future_date: continue
                future_sect = history.get(future_date, {})
                if next_sub not in future_sect: continue
                # 驗證：next_sub 在 future_date 是否進 Top 10？
                future_rank = future_sect[next_sub]["rank"]
                actual_ret = future_sect[next_sub]["ret20"]
                hit = future_rank <= 10
                record = {
                    "from": sub, "to": next_sub, "lag": lag,
                    "predicted_corr": corr,
                    "actual_rank": future_rank,
                    "actual_ret20": actual_ret,
                    "hit": hit,
                }
                if lag == 1: results_lag1.append(record)
                elif lag == 2: results_lag2.append(record)
    return results_lag1, results_lag2


def summarize(records, label):
    if not records:
        print(f"  {label}：無樣本")
        return
    n = len(records)
    hit = sum(1 for r in records if r["hit"])
    avg_ret = statistics.mean(r["actual_ret20"] for r in records)
    avg_rank = statistics.mean(r["actual_rank"] for r in records)
    print(f"  {label}：n={n}，命中 Top 10 率={hit/n*100:.1f}%，"
          f"平均排名={avg_rank:.1f}，平均20日報酬={avg_ret:+.2f}%")


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("🔗 資金輪動脈絡 / 順序性分析（lag cross-correlation）")
    print("=" * 70)

    history = load_history()
    dates_sorted = sorted(history.keys())
    print(f"📅 歷史快照：{len(dates_sorted)} 週（{dates_sorted[0]} ~ {dates_sorted[-1]}）")

    # ⚠️ 用 ret5 (週度) 做相關性分析（ret20 連 4 週重疊 75%，會偽相關）
    series, _ = history_to_matrix(history, metric="ret5")
    print(f"📊 完整 time series 子族群：{len(series)} 個（用週度報酬 ret5，無重疊）")

    # 建立輪動圖譜
    print()
    print("─" * 70)
    print("Step A：建立輪動圖譜（每對族群最佳 lag + 相關性）")
    print("─" * 70)
    chain = build_rotation_chain(series, min_corr=0.3)
    n_edges = sum(len(v) for v in chain.values())
    print(f"✅ 圖譜：{len(chain)} 節點，{n_edges} 條邊")

    # 印出 10 條最強連結
    all_edges = []
    for a, edges in chain.items():
        for b, lag, corr in edges:
            all_edges.append((a, b, lag, corr))
    all_edges.sort(key=lambda x: -x[3])

    print()
    print(f"🔝 全圖 Top 15 最強領先-跟隨對 (A 領先 B)：")
    for a, b, lag, corr in all_edges[:15]:
        a_alias = SUBSECTORS.get(a, {}).get("alias", a)
        b_alias = SUBSECTORS.get(b, {}).get("alias", b)
        a_icon = SUBSECTORS.get(a, {}).get("icon", "")
        b_icon = SUBSECTORS.get(b, {}).get("icon", "")
        print(f"  {a_icon}{a_alias:20} → {b_icon}{b_alias:20}  lag={lag}週  corr={corr:.3f}")

    # 從幾個關鍵族群印出脈絡
    print()
    print(f"🔍 關鍵族群的「下一棒」預測脈絡：")
    keys = ["晶圓代工_先進製程", "晶圓代工_成熟製程", "矽智財_ASIC",
            "ABF載板", "CCL銅箔基板", "AI伺服器_ODM", "散熱_液冷",
            "重電四雄", "矽光子_CPO"]
    for k in keys:
        if k not in chain: continue
        edges = chain[k]
        if not edges: continue
        a_alias = SUBSECTORS.get(k, {}).get("alias", k)
        a_icon = SUBSECTORS.get(k, {}).get("icon", "")
        print(f"\n  {a_icon} {a_alias} 領先：")
        for b, lag, corr in edges[:5]:
            b_alias = SUBSECTORS.get(b, {}).get("alias", b)
            b_icon = SUBSECTORS.get(b, {}).get("icon", "")
            print(f"     → {b_icon}{b_alias}  ({lag}週後，corr={corr:.3f})")

    # 存圖譜
    with open(OUTPUT_CHAIN, "w") as f:
        json.dump(chain, f, ensure_ascii=False, indent=2)
    print(f"\n📁 圖譜已存：{OUTPUT_CHAIN}")

    # 驗證
    print()
    print("─" * 70)
    print("Step B：驗證圖譜預測準確度")
    print("─" * 70)
    print("方法：當下 Top 3 強勢 → 用 chain 找下一棒 → 看下週/2週後是否真進 Top 10")

    r1, r2 = predict_using_chain(history, chain, dates_sorted, top_k_current=3)
    summarize(r1, "1 週後")
    summarize(r2, "2 週後")

    # 也試 Top 5 + lag=1
    r1_t5, _ = predict_using_chain(history, chain, dates_sorted, top_k_current=5)
    summarize(r1_t5, "Top 5 預測 1 週後")

    # 結論
    print()
    print("─" * 70)
    print("📊 脈絡圖譜總結")
    print("─" * 70)
    if r1:
        hit_rate = sum(1 for r in r1 if r["hit"]) / len(r1) * 100
        if hit_rate >= 60:
            print(f"✅ 1 週後命中 Top 10 率 = {hit_rate:.1f}% (有預測力)")
        elif hit_rate >= 50:
            print(f"⚠️ 1 週後命中率 = {hit_rate:.1f}% (略有預測力，但邊際)")
        else:
            print(f"❌ 1 週後命中率 = {hit_rate:.1f}% (低於拋硬幣，無預測力)")

    # 順序鏈
    print()
    print("─" * 70)
    print("🔗 高品質輪動鏈：自動偵測「3-step 順序鏈」")
    print("─" * 70)
    print("規則：A→B→C 三步皆有強相關（每步 corr ≥ 0.4）")
    chains_3step = []
    for a, edges_a in chain.items():
        for b, lag_ab, corr_ab in edges_a:
            if corr_ab < 0.4: continue
            edges_b = chain.get(b, [])
            for c, lag_bc, corr_bc in edges_b:
                if corr_bc < 0.4: continue
                if c == a: continue   # 排除迴圈
                chains_3step.append((a, b, c, lag_ab, lag_bc, corr_ab, corr_bc))
    chains_3step.sort(key=lambda x: -(x[5] + x[6]))
    for a, b, c, lag1, lag2, c1, c2 in chains_3step[:10]:
        aa = SUBSECTORS.get(a, {}).get("alias", a)
        bb = SUBSECTORS.get(b, {}).get("alias", b)
        cc = SUBSECTORS.get(c, {}).get("alias", c)
        print(f"  {aa} → {bb}（+{lag1}週 corr={c1:.2f}） → {cc}（+{lag2}週 corr={c2:.2f}） "
              f"總分 {c1+c2:.2f}")


if __name__ == "__main__":
    main()
