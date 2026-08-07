"""
台股選股程式 v2 - 增量更新加速版
第一次執行：完整下載所有資料（約 40 分鐘）
之後每次：只更新當天資料（約 3~5 分鐘）

快取位置：~/Desktop/Stock Selection Strategy/cache/
"""

import requests
import pandas as pd
import time
import json
import subprocess
import os
import pickle
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 設定 ──────────────────────────────────────────────
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
TOKEN       = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMy0yOCAxMToxMjowNiIsInVzZXJfaWQiOiJhODcwNzA3NiIsImVtYWlsIjoiYTg3MDcwNzZAeWFob28uY29tLnR3IiwiaXAiOiIyMTAuMjQ0Ljg3LjYyIn0.9ud739ptCL3uJb1TQQTY1DJx9pVLg8dFinNb-p6yMoU"

BASE_DIR    = os.path.expanduser("~/Desktop/Stock Selection Strategy")
CACHE_DIR   = os.path.join(BASE_DIR, "cache")
OUTPUT_DIR  = BASE_DIR
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

PRICE_CACHE    = os.path.join(CACHE_DIR, "price_data.pkl")
INST_CACHE     = os.path.join(CACHE_DIR, "inst_data.pkl")
META_FILE      = os.path.join(CACHE_DIR, "meta.json")

HISTORY_DAYS   = 180   # 歷史資料天數
MAX_WORKERS    = 5     # 平行下載數（免費 API 建議 3~5）

# ── API 工具 ──────────────────────────────────────────

def api_fetch(dataset, stock_id, start_date, end_date, retries=3):
    params = {
        "dataset":    dataset,
        "data_id":    stock_id,
        "start_date": start_date,
        "end_date":   end_date,
    }
    if TOKEN:
        params["token"] = TOKEN

    for i in range(retries):
        try:
            r = requests.get(FINMIND_API, params=params, verify=False, timeout=20)
            data = r.json()
            if data.get("status") == 200 and data.get("data"):
                return pd.DataFrame(data["data"])
            if data.get("status") == 402:
                print(f"\n⚠️  FinMind API 次數已達上限，建議申請免費 token：finmindtrade.com")
            return pd.DataFrame()
        except Exception:
            if i < retries - 1:
                time.sleep(2)
    return pd.DataFrame()


def get_stock_list():
    """取得股票清單，優先用 FinMind，失敗則改用證交所 + 櫃買中心"""

    # 方法一：FinMind
    try:
        params = {"dataset": "TaiwanStockInfo"}
        if TOKEN:
            params["token"] = TOKEN
        r = requests.get(FINMIND_API, params=params, verify=False, timeout=15)
        data = r.json()
        if data.get("status") == 200 and data.get("data"):
            df = pd.DataFrame(data["data"])
            df = df[df["stock_id"].str.match(r"^\d{4}$")]
            result = df[["stock_id", "stock_name"]].set_index("stock_id")["stock_name"].to_dict()
            if result:
                return result
        print(f"⚠️  FinMind 股票清單回傳異常：{data.get('msg', data)}")
    except Exception as e:
        print(f"⚠️  FinMind 股票清單失敗：{e}")

    # 方法二：備用 - 證交所上市清單
    print("🔄 改用證交所備用 API 取得股票清單...")
    stock_dict = {}
    try:
        # 上市
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        r = requests.get(url, verify=False, timeout=15)
        for item in r.json():
            sid = item.get("公司代號", "")
            name = item.get("公司簡稱", "")
            if sid.isdigit() and len(sid) == 4:
                stock_dict[sid] = name
    except Exception as e:
        print(f"⚠️  證交所上市清單失敗：{e}")

    try:
        # 上櫃
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_R"
        r = requests.get(url, verify=False, timeout=15)
        for item in r.json():
            sid = item.get("公司代號", "")
            name = item.get("公司簡稱", "")
            if sid.isdigit() and len(sid) == 4:
                stock_dict[sid] = name
    except Exception as e:
        print(f"⚠️  證交所上櫃清單失敗：{e}")

    if not stock_dict:
        raise RuntimeError("❌ 無法取得股票清單，請確認網路連線或 FinMind token")

    print(f"✅ 備用 API 取得 {len(stock_dict)} 支股票")
    return stock_dict


# ── 快取管理 ──────────────────────────────────────────

def load_cache():
    price_cache = {}
    inst_cache  = {}
    meta        = {"last_update": None, "stock_list": []}

    if os.path.exists(PRICE_CACHE):
        with open(PRICE_CACHE, "rb") as f:
            price_cache = pickle.load(f)
    if os.path.exists(INST_CACHE):
        with open(INST_CACHE, "rb") as f:
            inst_cache = pickle.load(f)
    if os.path.exists(META_FILE):
        with open(META_FILE, "r") as f:
            meta = json.load(f)

    return price_cache, inst_cache, meta


def save_cache(price_cache, inst_cache, meta):
    with open(PRICE_CACHE, "wb") as f:
        pickle.dump(price_cache, f)
    with open(INST_CACHE, "wb") as f:
        pickle.dump(inst_cache, f)
    with open(META_FILE, "w") as f:
        json.dump(meta, f, ensure_ascii=False)


# ── 下載單支股票（供平行執行使用）────────────────────

def fetch_one_stock(sid, start_date, end_date):
    price_df = api_fetch("TaiwanStockPrice", sid, start_date, end_date)
    inst_df  = api_fetch("TaiwanStockInstitutionalInvestorsBuySell", sid, start_date, end_date)

    if not price_df.empty:
        price_df = price_df.sort_values("date").reset_index(drop=True)
    if not inst_df.empty:
        inst_df = inst_df[inst_df["name"] == "投信"].sort_values("date").reset_index(drop=True)

    return sid, price_df, inst_df


# ── 完整初始化（第一次執行，支援斷點續傳）────────────

def full_init(stock_dict):
    end_date   = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    stock_list = list(stock_dict.keys())
    total      = len(stock_list)

    # 載入已有快取（支援斷點續傳）
    price_cache, inst_cache, _ = load_cache()
    already_done = len(price_cache)

    if already_done > 0:
        print(f"\n🔄 繼續上次下載（已完成 {already_done} 支，剩餘 {total - already_done} 支）...")
    else:
        print(f"\n🔄 首次執行，開始完整下載歷史資料（共 {total} 支）...")
        print(f"   預計需要約 {total // 60 + 1} 分鐘，之後每天只需 3~5 分鐘")
    print("=" * 55)

    done        = 0
    saved_count = 0
    SAVE_EVERY  = 100  # 每 100 支自動儲存，防止中斷遺失

    for sid in stock_list:
        done += 1

        # 已有快取則跳過（斷點續傳）
        if sid in price_cache:
            print(f"  進度：{done}/{total} ({int(done/total*100)}%) 已快取：{len(price_cache)} 支", end="\r")
            continue

        price_df = api_fetch("TaiwanStockPrice", sid, start_date, end_date)
        inst_df  = api_fetch("TaiwanStockInstitutionalInvestorsBuySell", sid, start_date, end_date)

        if not price_df.empty:
            price_cache[sid] = price_df.sort_values("date").reset_index(drop=True)
        if not inst_df.empty:
            inst_cache[sid] = inst_df[inst_df["name"] == "投信"].sort_values("date").reset_index(drop=True)

        saved_count += 1
        print(f"  進度：{done}/{total} ({int(done/total*100)}%) - {sid} | 已快取：{len(price_cache)} 支", end="\r")

        # 定期儲存進度
        if saved_count % SAVE_EVERY == 0:
            meta_tmp = {"last_update": None, "start_date": start_date, "stock_list": stock_list}
            save_cache(price_cache, inst_cache, meta_tmp)
            print(f"\n  💾 自動儲存進度（{len(price_cache)} 支）...")

        time.sleep(0.8)  # 間隔 0.8 秒，避免超出 API 速率限制

    meta = {
        "last_update": end_date,
        "start_date":  start_date,
        "stock_list":  stock_list,
    }
    save_cache(price_cache, inst_cache, meta)
    print(f"\n✅ 資料下載完成！共 {len(price_cache)} 支股票已快取")
    return price_cache, inst_cache, meta


# ── 增量更新（第二天起）──────────────────────────────

def incremental_update(price_cache, inst_cache, meta, stock_dict):
    last_date  = meta["last_update"]
    today      = datetime.today().strftime("%Y-%m-%d")

    if last_date == today:
        print(f"✅ 資料已是最新（{today}），直接使用快取")
        return price_cache, inst_cache

    # 只抓上次更新後的新資料
    start_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"\n🔄 增量更新：{start_date} ~ {today}")

    stock_list = list(stock_dict.keys())
    total      = len(stock_list)
    updated    = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_one_stock, sid, start_date, today): sid
            for sid in stock_list
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            sid, new_price, new_inst = future.result()

            # 合併新舊資料
            if not new_price.empty:
                old = price_cache.get(sid, pd.DataFrame())
                combined = pd.concat([old, new_price]).drop_duplicates("date").sort_values("date")
                # 只保留最近 HISTORY_DAYS 天
                cutoff = (datetime.today() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
                price_cache[sid] = combined[combined["date"] >= cutoff].reset_index(drop=True)
                updated += 1

            if not new_inst.empty:
                old = inst_cache.get(sid, pd.DataFrame())
                combined = pd.concat([old, new_inst]).drop_duplicates("date").sort_values("date")
                cutoff = (datetime.today() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
                inst_cache[sid] = combined[combined["date"] >= cutoff].reset_index(drop=True)

            print(f"  更新進度：{done}/{total}", end="\r")
            time.sleep(0.2)

    meta["last_update"] = today
    save_cache(price_cache, inst_cache, meta)
    print(f"\n✅ 增量更新完成！更新了 {updated} 支股票的資料")
    return price_cache, inst_cache


# ── 選股篩選 ──────────────────────────────────────────

def check_stock_from_cache(sid, price_df, inst_df):
    if price_df is None or price_df.empty or len(price_df) < 130:
        return False, None

    df = price_df.copy()
    df["close"]  = pd.to_numeric(df.get("Close", df.get("close", 0)), errors="coerce")
    df["volume"] = pd.to_numeric(df.get("Trading_Volume", 0), errors="coerce")

    df["ma10"]     = df["close"].rolling(10).mean()
    df["ma120"]    = df["close"].rolling(120).mean()
    df["ma20_vol"] = df["volume"].rolling(20).mean()

    latest = df.iloc[-1]

    if pd.isna(latest["ma10"]) or pd.isna(latest["ma120"]) or latest["ma120"] == 0:
        return False, None

    ratio = latest["ma10"] / latest["ma120"]
    if not (0.95 <= ratio <= 1.05):
        return False, None

    if pd.isna(latest["ma20_vol"]) or latest["ma20_vol"] == 0:
        return False, None

    vol_ratio = latest["volume"] / latest["ma20_vol"]
    if vol_ratio < 2.0:
        return False, None

    if inst_df is None or inst_df.empty or len(inst_df) < 2:
        return False, None

    last2 = inst_df.tail(2).copy()
    last2["net"] = pd.to_numeric(last2["buy"], errors="coerce") - pd.to_numeric(last2["sell"], errors="coerce")
    if not (last2["net"] > 0).all():
        return False, None

    return True, {
        "股票代碼":          sid,
        "10日線/半年線":     round(ratio, 4),
        "量比":              round(vol_ratio, 2),
        "投信近2日買超(張)":  int(last2["net"].sum()),
        "今日收盤(元)":      round(float(latest["close"]), 2),
    }


# ── 產生 Word 報告 ─────────────────────────────────────

def generate_word(results, stock_names, output_path):
    today_str = datetime.today().strftime("%Y年%m月%d日")
    rows_js   = json.dumps(results, ensure_ascii=False)
    names_js  = json.dumps(stock_names, ensure_ascii=False)

    js_code = f"""
const {{ Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
         AlignmentType, BorderStyle, WidthType, ShadingType, VerticalAlign }} = require('docx');
const fs = require('fs');

const results    = {rows_js};
const names      = {names_js};
const today      = "{today_str}";
const outputPath = {json.dumps(output_path)};

const border  = {{ style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" }};
const borders = {{ top: border, bottom: border, left: border, right: border }};
const colWidths = [1200, 1800, 1500, 1200, 1800, 1800];

function makeRow(cells, isHeader) {{
  return new TableRow({{
    tableHeader: isHeader,
    children: cells.map((c, i) => new TableCell({{
      borders,
      width: {{ size: colWidths[i], type: WidthType.DXA }},
      shading: {{ fill: isHeader ? "1F497D" : "F9F9F9", type: ShadingType.CLEAR }},
      margins: {{ top: 80, bottom: 80, left: 120, right: 120 }},
      verticalAlign: VerticalAlign.CENTER,
      children: [new Paragraph({{
        alignment: AlignmentType.CENTER,
        children: [new TextRun({{
          text: String(c), bold: isHeader,
          color: isHeader ? "FFFFFF" : "333333",
          size: isHeader ? 20 : 18, font: "Arial"
        }})]
      }})]
    }}))
  }});
}}

const headers  = ["股票代碼","股票名稱","10日/半年線","量比(倍)","投信買超(張)","收盤價(元)"];
const dataRows = results.map(r => makeRow([
  r["股票代碼"], names[r["股票代碼"]] || "-",
  r["10日線/半年線"], r["量比"] + "x",
  r["投信近2日買超(張)"], r["今日收盤(元)"]
], false));

const doc = new Document({{
  styles: {{ default: {{ document: {{ run: {{ font: "Arial", size: 22 }} }} }} }},
  sections: [{{
    properties: {{ page: {{ size: {{ width: 12240, height: 15840 }},
      margin: {{ top: 1440, right: 1440, bottom: 1440, left: 1440 }} }} }},
    children: [
      new Paragraph({{ alignment: AlignmentType.CENTER, spacing: {{ after: 100 }},
        children: [new TextRun({{ text: "📈 台股每日選股報告", bold: true, size: 36, font: "Arial", color: "1F497D" }})] }}),
      new Paragraph({{ alignment: AlignmentType.CENTER, spacing: {{ after: 400 }},
        children: [new TextRun({{ text: "報告日期：" + today, size: 22, color: "666666", font: "Arial" }})] }}),
      new Paragraph({{ spacing: {{ after: 200 }},
        children: [new TextRun({{ text: "📋 篩選條件", bold: true, size: 26, font: "Arial", color: "2E75B6" }})] }}),
      new Paragraph({{ spacing: {{ after: 100 }},
        children: [new TextRun({{ text: "• 10日均線 ÷ 半年線 介於 0.95 ~ 1.05（盤整蓄積）", size: 20, font: "Arial" }})] }}),
      new Paragraph({{ spacing: {{ after: 100 }},
        children: [new TextRun({{ text: "• 近兩天投信連續買超（法人開始佈局）", size: 20, font: "Arial" }})] }}),
      new Paragraph({{ spacing: {{ after: 400 }},
        children: [new TextRun({{ text: "• 今日成交量 ≥ 月均量（20日）× 2（異常放量）", size: 20, font: "Arial" }})] }}),
      new Paragraph({{ spacing: {{ after: 200 }},
        children: [new TextRun({{ text: results.length > 0
          ? "✅ 符合條件股票：共 " + results.length + " 支"
          : "❌ 今日無符合條件的股票",
          bold: true, size: 24, font: "Arial",
          color: results.length > 0 ? "217346" : "CC0000" }})] }}),
      ...(results.length > 0 ? [new Table({{
        width: {{ size: 9300, type: WidthType.DXA }},
        columnWidths: colWidths,
        rows: [makeRow(headers, true), ...dataRows]
      }})] : []),
      new Paragraph({{ spacing: {{ before: 400 }},
        children: [new TextRun({{ text: "⚠️ 本報告僅供參考，投資請自行評估風險。",
          size: 18, color: "999999", font: "Arial", italics: true }})] }})
    ]
  }}]
}});

Packer.toBuffer(doc).then(buf => {{
  fs.writeFileSync(outputPath, buf);
  console.log("✅ Word 報告已產生：" + outputPath);
}});
"""

    js_path = "/tmp/gen_report.js"
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(js_code)

    env = os.environ.copy()
    env["NODE_PATH"] = os.path.expanduser("/opt/homebrew/lib/node_modules")
    result = subprocess.run(["node", js_path], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        print(f"❌ Word 產生失敗：{result.stderr}")
    else:
        print(result.stdout.strip())


# ── 主程式 ────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  台股選股程式 v2（增量更新加速版）")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 55)

    # 取得股票清單
    stock_dict = get_stock_list()
    print(f"✅ 股票清單：{len(stock_dict)} 支")

    # 載入快取
    price_cache, inst_cache, meta = load_cache()

    # 判斷首次執行 or 斷點續傳 or 增量更新
    if not meta["last_update"]:
        # 首次執行 或 上次中斷未完成
        price_cache, inst_cache, meta = full_init(stock_dict)
    else:
        price_cache, inst_cache = incremental_update(price_cache, inst_cache, meta, stock_dict)

    # 從快取資料中進行篩選（速度極快）
    print("\n🔍 開始篩選...")
    results = []
    for sid in stock_dict:
        try:
            passed, info = check_stock_from_cache(
                sid,
                price_cache.get(sid),
                inst_cache.get(sid)
            )
            if passed:
                results.append(info)
                name = stock_dict.get(sid, "")
                print(f"  ✅ {sid} {name} | 比值={info['10日線/半年線']} | 量比={info['量比']}x | 投信={info['投信近2日買超(張)']}張")
        except Exception:
            pass

    results.sort(key=lambda x: x["量比"], reverse=True)

    print("\n" + "=" * 55)
    print(f"✅ 篩選完成！共 {len(results)} 支符合條件")
    print("=" * 55)

    # 輸出報告
    date_str    = datetime.today().strftime("%Y%m%d")
    output_path = os.path.join(OUTPUT_DIR, f"選股報告_{date_str}.docx")
    generate_word(results, stock_dict, output_path)

    if results:
        df = pd.DataFrame(results)
        df.insert(1, "股票名稱", df["股票代碼"].map(stock_dict))
        df.to_csv(os.path.join(OUTPUT_DIR, f"選股結果_{date_str}.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
