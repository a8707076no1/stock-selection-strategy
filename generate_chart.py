"""
台股飆股 K 線圖 HTML 產生器
每天執行後自動產生 飆股圖表_YYYYMMDD.html
包含：蠟燭線 K 線 + 成交量長條圖
"""
import pickle, json, os, sys, pandas as pd, numpy as np
from datetime import datetime

# 載入型態識別模組
_BASE = os.path.expanduser("~/Desktop/Stock Selection Strategy")
sys.path.insert(0, _BASE)

# ── 股票基本資料（產業、產品、月營收）──────────────────
_META_FILE = os.path.join(_BASE, "cache", "stock_meta.json")
_REV_CACHE  = os.path.join(_BASE, "cache", "stock_revenue.json")

def load_stock_meta():
    if os.path.exists(_META_FILE):
        with open(_META_FILE,"r",encoding="utf-8") as f:
            return json.load(f)
    return {}

_NEWS_CACHE = os.path.join(_BASE, "cache", "stock_news.json")
NEWS_TTL = 4 * 3600   # 新聞快取 4 小時（之前 24 小時太久）
REV_TTL  = 24 * 3600  # 月營收快取 24 小時（不變）


def _fetch_google_news(sid, name="", max_items=10):
    """從 Google News RSS 抓股票新聞，covers 比 FinMind 好太多"""
    import urllib.request, urllib.parse
    import xml.etree.ElementTree as _ET
    from email.utils import parsedate_to_datetime
    q_terms = [sid]
    if name and name.strip() and name.strip() != sid:
        q_terms.append(name.strip())
    q = urllib.parse.quote(" ".join(q_terms))
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        xml_text = urllib.request.urlopen(req, timeout=12).read()
        root = _ET.fromstring(xml_text)
    except Exception:
        return []
    out = []
    for it in root.findall(".//item")[:max_items]:
        title = (it.findtext("title") or "").strip()
        link  = (it.findtext("link") or "").strip()
        pub   = it.findtext("pubDate") or ""
        date_str = ""
        try:
            dt = parsedate_to_datetime(pub)
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        if title:
            out.append({"title": title, "link": link, "date": date_str})
    return out


def _dedupe_and_sort_news(raw_rows, max_age_days=7, max_items=10):
    """新聞去重 + 按日期排序（新→舊）+ 過濾超過 max_age_days 的舊聞"""
    if not raw_rows:
        return []
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=max_age_days)).strftime("%Y-%m-%d")
    # 1) 排序：date 降冪（'YYYY-MM-DD HH:MM:SS' 格式）
    sorted_rows = sorted(raw_rows, key=lambda x: x.get("date",""), reverse=True)
    seen = set()
    out = []
    for n in sorted_rows:
        title = (n.get("title") or "").strip()
        link  = (n.get("link") or "").strip()
        date  = (n.get("date") or "")[:10]
        if not title:
            continue
        # 過濾過舊（無日期當作不夠新，丟掉）
        if not date or date < cutoff:
            continue
        # 標題正規化：移除股號前綴、結尾的來源名
        norm = _re.sub(r"^\d{4,6}[A-Za-z]?\s+\S+\s*[-－—]\s*", "", title)
        norm = _re.sub(r"\s*[-－—]\s*[^-—－]+$", "", norm)
        norm = _re.sub(r"\s+", "", norm)
        key = norm[:30]
        if key in seen:
            continue
        seen.add(key)
        out.append({"t": title, "u": link, "d": date})
        if len(out) >= max_items:
            break
    return out


def fetch_revenue_all(sids, name_map=None):
    """抓所有股票的最新月營收（快取 24h）+ 新聞（快取 4h，去重排序）。
    營收：FinMind；新聞：Google News RSS（主）+ FinMind（備援）。
    name_map：{sid: 名稱} 給 Google News 加強查詢精準度。
    """
    name_map = name_map or {}
    import time
    cache = {}
    news_cache = {}
    now = time.time()

    # 載入舊快取（如果還在 TTL 內）
    rev_age = (now - os.path.getmtime(_REV_CACHE)) if os.path.exists(_REV_CACHE) else 1e9
    if rev_age < REV_TTL:
        try:
            with open(_REV_CACHE,"r",encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    news_age = (now - os.path.getmtime(_NEWS_CACHE)) if os.path.exists(_NEWS_CACHE) else 1e9
    if news_age < NEWS_TTL:
        try:
            with open(_NEWS_CACHE,"r",encoding="utf-8") as f:
                news_cache = json.load(f)
        except Exception:
            news_cache = {}

    rev_need_fetch  = [s for s in sids if cache.get(s, {}).get("rev","—") == "—" or rev_age >= REV_TTL]
    news_need_fetch = [s for s in sids if s not in news_cache or news_age >= NEWS_TTL]
    if not rev_need_fetch and not news_need_fetch:
        # 全部都有最新資料，合併新聞回 cache 後直接回傳
        for sid in sids:
            cache.setdefault(sid, {"rev":"—","yoy":"—","month":"—"})
            cache[sid]["news"] = news_cache.get(sid, [])
        return cache

    print(f"  下載 FinMind：營收 {len(rev_need_fetch)} 支，新聞 {len(news_need_fetch)} 支")
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        s = requests.Session(); s.verify = False
        s.headers.update({"User-Agent":"Mozilla/5.0"})
        from datetime import datetime as _dt, timedelta as _td
        start_rev  = (_dt.today() - _td(days=450)).strftime("%Y-%m-%d")
        news_start = (_dt.today() - _td(days=14)).strftime("%Y-%m-%d")  # 抓 14 天比較不會漏

        for sid in sids:
            cache.setdefault(sid, {"rev":"—","yoy":"—","month":"—","news":[]})
            # ── 月營收 ──
            if sid in rev_need_fetch:
                try:
                    url = (f"https://api.finmindtrade.com/api/v4/data"
                           f"?dataset=TaiwanStockMonthRevenue&data_id={sid}"
                           f"&start_date={start_rev}")
                    r = s.get(url, timeout=12)
                    if r.status_code == 200:
                        rows = r.json().get("data", [])
                        if rows:
                            latest = rows[-1]
                            rev_val = latest.get("revenue", 0)
                            rev_month = latest.get("revenue_month", 0)
                            rev_year  = latest.get("revenue_year", 0)
                            if rev_val >= 1e8:
                                rev_str = f"{rev_val/1e8:.2f}億"
                            elif rev_val >= 1e4:
                                rev_str = f"{rev_val/1e4:.0f}萬"
                            else:
                                rev_str = str(int(rev_val))
                            yoy_str = "—"
                            for prev in rows:
                                if prev.get("revenue_month") == rev_month and prev.get("revenue_year") == rev_year - 1:
                                    prev_rev = prev.get("revenue", 0)
                                    if prev_rev > 0:
                                        yoy_pct = (rev_val - prev_rev) / prev_rev * 100
                                        yoy_str = f"{yoy_pct:+.1f}"
                                    break
                            cache[sid]["rev"]   = rev_str
                            cache[sid]["yoy"]   = yoy_str
                            cache[sid]["month"] = f"{rev_year}年{rev_month}月"
                except Exception:
                    pass
            # ── 新聞（每 4 小時更新一次）──
            # 主源：Google News RSS（覆蓋率最好），備援：FinMind
            if sid in news_need_fetch:
                merged = []
                # 1) Google News RSS（用 sid + 名稱查詢更精準）
                try:
                    name = name_map.get(sid, "")
                    g_rows = _fetch_google_news(sid, name=name, max_items=15)
                    merged.extend(g_rows)
                except Exception as _e:
                    pass
                # 2) FinMind 備援（合併進去再去重）
                try:
                    url2 = (f"https://api.finmindtrade.com/api/v4/data"
                            f"?dataset=TaiwanStockNews&data_id={sid}"
                            f"&start_date={news_start}")
                    r2 = s.get(url2, timeout=8)
                    if r2.status_code == 200:
                        merged.extend(r2.json().get("data", []))
                except Exception:
                    pass
                # 排序 + 去重 + 取前 5 則
                news_cache[sid] = _dedupe_and_sort_news(merged)
            # 合併新聞到 cache
            cache[sid]["news"] = news_cache.get(sid, cache[sid].get("news", []))

        with open(_REV_CACHE,"w",encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        with open(_NEWS_CACHE,"w",encoding="utf-8") as f:
            json.dump(news_cache, f, ensure_ascii=False)
        ok_rev  = sum(1 for v in cache.values() if v.get("rev","—") != "—")
        ok_news = sum(1 for v in cache.values() if v.get("news"))
        print(f"  ✅ 營收 {ok_rev}/{len(sids)} 支，新聞 {ok_news}/{len(sids)} 支（已去重）")
    except Exception as e:
        print(f"  月營收/新聞抓取失敗：{e}")
    return cache

# ── 集保持股分級資料 ──────────────────────────────────
_TDCC_CACHE = os.path.join(_BASE, "cache", "tdcc_holding.json")

_TDCC_HISTORY = os.path.join(_BASE, "cache", "tdcc_history.json")

def _save_tdcc_history(result, data_date):
    """把本週持股摘要存入歷史檔，並嘗試補齊歷史資料"""
    history = {}
    if os.path.exists(_TDCC_HISTORY):
        with open(_TDCC_HISTORY,"r",encoding="utf-8") as f:
            history = json.load(f)
    if data_date in history:
        return  # 本週已存過
    week_data = {}
    for sid, grades in result.items():
        major = sum(grades.get(g,{}).get("ratio",0) for g in range(12,16))
        mid   = sum(grades.get(g,{}).get("ratio",0) for g in range(10,12))
        small = sum(grades.get(g,{}).get("ratio",0) for g in range(1,10))
        whale = grades.get(15,{}).get("ratio",0)
        total_persons = 0
        try:
            persons_str = grades.get(17,{}).get("persons","0")
            total_persons = int(str(persons_str).replace(",",""))
        except: pass
        week_data[sid] = {
            "major": round(major,1), "mid": round(mid,1),
            "small": round(small,1), "whale": round(whale,1),
            "persons": total_persons,
        }
    history[data_date] = week_data
    all_dates = sorted(history.keys(), reverse=True)
    for old_d in all_dates[26:]: del history[old_d]
    with open(_TDCC_HISTORY,"w",encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    print(f"  歷史記錄已更新：共 {len(history)} 週")

def fetch_tdcc_history_from_web(sids, dates=None):
    """從 TDCC 網站抓指定日期的歷史持股資料"""
    import re as _re
    history = {}
    if os.path.exists(_TDCC_HISTORY):
        with open(_TDCC_HISTORY,"r",encoding="utf-8") as f:
            history = json.load(f)

    # 若沒指定日期，抓最近6週（從選單取得）
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        s = requests.Session(); s.verify = False
        s.headers.update({"User-Agent":"Mozilla/5.0"})

        # 取得可用日期清單
        r0 = s.get("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", timeout=15)
        token_m = _re.search(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"', r0.text)
        token = token_m.group(1) if token_m else ""
        if not dates:
            avail = _re.findall(r'<option[^>]*value="(\d{8})"', r0.text)
            dates = avail[:6]  # 最近6週

        print(f"  補充TDCC歷史週資料：{dates}")
        for date in dates:
            if date in history and len(history[date]) >= len(sids):
                continue  # 已有完整資料
            if date not in history:
                history[date] = {}
            for sid in sids:
                if sid in history[date]:
                    continue
                try:
                    params = {
                        "SYNCHRONIZER_TOKEN": token,
                        "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                        "method": "submit",
                        "firDate": date, "scaDate": date,
                        "sqlMethod": "StockNo",
                        "stockNo": sid, "stockName": ""
                    }
                    r2 = s.post("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
                                data=params, timeout=15)
                    # 更新 token
                    tm = _re.search(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"', r2.text)
                    if tm: token = tm.group(1)

                    rows = _re.findall(r'<tr[^>]*>(.*?)</tr>', r2.text, _re.DOTALL)
                    major=0; whale=0; persons=0
                    for row in rows:
                        tds = _re.findall(r'<td[^>]*>(.*?)</td>', row, _re.DOTALL)
                        tds = [_re.sub(r'<[^>]+>','',t).strip().replace(',','') for t in tds]
                        if len(tds) < 5: continue
                        try:
                            seq = int(tds[0])
                            pct = float(tds[4])
                            p   = int(float(tds[2])) if tds[2] else 0
                            if 12 <= seq <= 15: major += pct
                            if seq == 15: whale = pct
                            if seq == 17: persons = p
                        except: pass
                    if major > 0:
                        history[date][sid] = {
                            "major": round(major,2), "mid":0, "small":0,
                            "whale": round(whale,2), "persons": persons
                        }
                except Exception as e:
                    pass

        with open(_TDCC_HISTORY,"w",encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False)
        print(f"  TDCC歷史補充完成：共 {len(history)} 週")
    except Exception as e:
        print(f"  TDCC歷史補充失敗：{e}")
    return history

def get_holding_history(sid):
    """取得某股票的持股歷史趨勢（最近8週）"""
    if not os.path.exists(_TDCC_HISTORY): return []
    with open(_TDCC_HISTORY,"r",encoding="utf-8") as f:
        history = json.load(f)
    weeks = sorted(history.keys())[-8:]
    result = []
    for date in weeks:
        if sid in history[date]:
            d = history[date][sid]
            result.append({
                "date":    date,
                "major":   d.get("major",0),
                "mid":     d.get("mid",0),
                "small":   d.get("small",0),
                "whale":   d.get("whale",0),
                "persons": d.get("persons",0),
            })
    return result

def fetch_tdcc_all():
    """抓取所有股票的持股分級資料（每週更新一次）"""
    import time
    if os.path.exists(_TDCC_CACHE):
        age = time.time() - os.path.getmtime(_TDCC_CACHE)
        if age < 7 * 86400:
            with open(_TDCC_CACHE,"r",encoding="utf-8") as f:
                return json.load(f)
    print("  下載集保持股分級資料...")
    try:
        import requests, urllib3
        urllib3.disable_warnings()
        s = requests.Session()
        s.verify = False
        s.headers.update({"User-Agent":"Mozilla/5.0"})
        r = s.get("https://openapi.tdcc.com.tw/v1/opendata/1-5", timeout=30)
        data = r.json()
        result = {}
        data_date = ""
        for d in data:
            sid = d.get("證券代號","").strip()
            if not sid: continue
            grade = int(d.get("持股分級","0"))
            if grade == 0: continue
            date_val = d.get("﻿資料日期", d.get("資料日期",""))
            if date_val: data_date = date_val
            if sid not in result: result[sid] = {}
            result[sid][grade] = {
                "persons": d.get("人數","0"),
                "ratio":   float(d.get("占集保庫存數比例%","0")),
                "date":    date_val
            }
        with open(_TDCC_CACHE,"w",encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        if data_date:
            _save_tdcc_history(result, data_date)
        print(f"  集保資料快取完成：{len(result)} 支股票，日期：{data_date}")
        return result
    except Exception as e:
        print(f"  集保資料抓取失敗：{e}")
        return {}

def get_holding_summary(tdcc_data, sid):
    """計算大戶/中實戶/散戶持股比例"""
    if not tdcc_data or sid not in tdcc_data:
        return None
    d = tdcc_data[sid]
    # JSON 讀取後 key 可能是字串，統一轉換
    def gv(grade):
        return d.get(grade, d.get(str(grade), {}))
    # 大戶：400張以上（分級12-15）
    major = sum(gv(g).get("ratio",0) for g in range(12,16))
    # 中實戶：100-400張（分級10-11）
    mid   = sum(gv(g).get("ratio",0) for g in range(10,12))
    # 小戶：1-100張（分級1-9）
    small = sum(gv(g).get("ratio",0) for g in range(1,10))
    # 取得日期
    date = ""
    for g in range(15,0,-1):
        info = gv(g)
        if info.get("date"):
            date = info["date"]; break
    # 千張以上超大戶
    whale = gv(15).get("ratio",0)
    # 總股東人數（分級17）
    total_persons = 0
    try:
        persons_str = gv(17).get("persons","0")
        total_persons = int(str(persons_str).replace(",",""))
    except: pass
    return {
        "major":   round(major,1),
        "mid":     round(mid,1),
        "small":   round(small,1),
        "whale":   round(whale,1),
        "date":    date,
        "persons": total_persons,
    }

def enrich_holding_for_chart(holding_summary, sid, df, pc):
    """
    為單支股票的籌碼資料補上 history、chart_points、cost_zone，
    讓持股 / 突破候選的卡片能用跟飆股區一樣的籌碼分析區塊（連N增、成本區）。
    """
    if not holding_summary:
        return holding_summary
    try:
        hist = get_holding_history(sid)
    except Exception:
        hist = []
    if not hist:
        holding_summary.setdefault("history", [])
        holding_summary.setdefault("chart_points", [])
        holding_summary.setdefault("cost_zone", None)
        return holding_summary

    holding_summary["history"] = hist

    # chart_points：把週資料對齊到 K 線日期
    chart_points = []
    if df is not None and len(df) > 0:
        for hw in hist:
            try:
                hdate = hw["date"][:4]+"-"+hw["date"][4:6]+"-"+hw["date"][6:8]
            except Exception:
                continue
            closest_idx = None
            min_diff = 999
            for ki, krow in enumerate(df.itertuples()):
                try:
                    diff = abs((pd.Timestamp(krow.date) - pd.Timestamp(hdate)).days)
                    if diff < min_diff:
                        min_diff = diff
                        closest_idx = ki
                except Exception:
                    pass
            if closest_idx is not None and min_diff <= 7:
                chart_points.append({
                    "idx":   closest_idx,
                    "major": hw["major"],
                    "date":  hw["date"],
                })
    holding_summary["chart_points"] = chart_points

    # cost_zone：找最近一段連續上升期，取對應 K 線的成本區
    cost_zone = None
    if len(hist) >= 1:
        consec_weeks = 1
        for i in range(len(hist)-1, 0, -1):
            if hist[i]["major"] >= hist[i-1]["major"]:
                consec_weeks += 1
            else:
                break
        start_date = hist[-min(consec_weeks, len(hist))]["date"]
        end_date   = hist[-1]["date"]
        # 優先用傳入的 df（持股可能來自 Yahoo，不在 pc 中）；fallback 到 pc
        df_sid = df if (df is not None and not df.empty) else (pc.get(sid) if pc else None)
        if df_sid is not None and not df_sid.empty:
            try:
                df_sid = df_sid.copy()
                df_sid["close"] = pd.to_numeric(df_sid["close"], errors="coerce")
                df_sid["high"]  = pd.to_numeric(df_sid["high"],  errors="coerce")
                df_sid["low"]   = pd.to_numeric(df_sid["low"],   errors="coerce")
                sd = start_date[:4]+"-"+start_date[4:6]+"-"+start_date[6:8]
                ed = end_date[:4]+"-"+end_date[4:6]+"-"+end_date[6:8]
                mask = (df_sid["date"] >= sd) & (df_sid["date"] <= ed)
                period_df = df_sid[mask]
                if period_df.empty:
                    period_df = df_sid.tail(5)
                if not period_df.empty:
                    avg_price  = round(period_df["close"].mean(), 2)
                    low_price  = round(period_df["low"].min(), 2)
                    high_price = round(period_df["high"].max(), 2)
                    curr_price = float(df_sid["close"].iloc[-1])
                    dist_pct   = round((curr_price - avg_price) / avg_price * 100, 1) if avg_price > 0 else 0
                    cost_zone  = {
                        "avg":   avg_price,
                        "low":   low_price,
                        "high":  high_price,
                        "weeks": consec_weeks,
                        "dist":  dist_pct,
                    }
            except Exception:
                pass
    holding_summary["cost_zone"] = cost_zone
    return holding_summary
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    from pattern_detector import detect_pattern, get_pattern_drawing, get_support_resistance
    from strategy_matcher import match_strategies, summarize_action, EPISODE_INDEX
    PATTERN_OK = True
    print("✅ pattern_detector 載入成功")
except Exception as _e:
    print(f"❌ pattern_detector 載入失敗: {_e}")
    PATTERN_OK = False
    def detect_pattern(df, n_bars=60): return None
    def get_pattern_drawing(df, pattern_en, n_bars=60): return {"lines":[], "marks":[]}
    def get_support_resistance(df, n_bars=60): return {"support":[], "resistance":[]}
    def match_strategies(df, pattern_info=None): return []
    def summarize_action(matches): return {"action":"觀望","color":"#888","score":0}
    EPISODE_INDEX = {}

# ── 我的持股清單 ─────────────────────────────────────
# (代碼, 名稱, 張數, 成本價或None, 是否ETF)
# 優先從「資產與持股明細更新案夾/*.xlsx」讀最新版本，沒有則用下方預設
# 空 fallback — 真實持股由 資產與持股明細更新案夾/*.xlsx 讀（gitignore 排除）
_DEFAULT_MY_HOLDINGS = []

try:
    from holdings_loader import get_holdings as _load_h
    _h, _src, _date = _load_h()
    if _h:
        MY_HOLDINGS = _h
        if _src:
            print(f"📋 持股清單從檔案載入：{os.path.basename(_src)}（共 {len(_h)} 支）")
    else:
        MY_HOLDINGS = _DEFAULT_MY_HOLDINGS
except Exception as _e:
    print(f"⚠️ holdings_loader 載入失敗：{_e}（改用內建預設）")
    MY_HOLDINGS = _DEFAULT_MY_HOLDINGS

def fetch_yahoo_history(sid, days=120):
    """從 Yahoo Finance 抓歷史 K 線（自動嘗試 .TW / .TWO）

    使用 yf.Ticker().history()（單檔路徑），避免 yf.download() 的批次
    下載管理器在連續呼叫時會發生「不同 ticker 拿到他股資料」的
    交叉污染問題（曾導致 6279 胡連 K 線被掛到 3587 閎康 的卡片）。
    並加入重試 + 基本驗證。
    """
    try:
        import yfinance as yf
    except ImportError:
        return None
    import time
    last_err = None
    for suffix in [".TW", ".TWO"]:
        full = sid + suffix
        for attempt in range(2):  # 每個 suffix 最多重試 1 次
            try:
                tk = yf.Ticker(full)
                ydf = tk.history(period=f"{days}d", auto_adjust=False)
                if ydf is None or len(ydf) == 0:
                    break  # 該 suffix 沒資料，不重試，換下一個
                # 攤平多層欄位（單檔通常單層，但保險起見）
                if hasattr(ydf.columns, "nlevels") and ydf.columns.nlevels > 1:
                    ydf.columns = [c[0] for c in ydf.columns]
                # 基本健全性檢查：必要欄位都在
                need = {"Open","High","Low","Close","Volume"}
                if not need.issubset(set(ydf.columns)):
                    break
                df = pd.DataFrame({
                    "date":   [d.strftime("%Y-%m-%d") for d in ydf.index],
                    "open":   ydf["Open"].values,
                    "high":   ydf["High"].values,
                    "low":    ydf["Low"].values,
                    "close":  ydf["Close"].values,
                    "volume": ydf["Volume"].values,
                })
                # 標記來源（給呼叫者驗證用）
                df.attrs["yahoo_symbol"] = full
                return df
            except Exception as e:
                last_err = e
                time.sleep(0.5)
                continue
    if last_err:
        print(f"  ⚠️ fetch_yahoo_history({sid}) 最後錯誤：{last_err}")
    return None

def fetch_upcoming_dividends(sid, manual_override=None):
    """從 FinMind 抓「已宣布但除權息日尚未到達」的配股配息。
    若 manual_override 有給 (xlsx 手填)，會優先使用手填值，再 fallback FinMind。
    manual_override 格式：{'cash': 元/股, 'stock': 元/股, 'ex_date': 'YYYY-MM-DD' or None}
    回傳：{
        cash_per_share, stock_per_share,
        events: [{kind, amount, ex_date, pay_date, year, source: 'manual'|'finmind'}],
        next_ex_date, source: 'manual'|'finmind'|'mixed',
    }"""
    try:
        import requests
    except ImportError:
        requests = None
    today_str = datetime.today().strftime("%Y-%m-%d")
    start_year = datetime.today().year - 1   # 抓近 1-2 年的宣告，含尚未除權息

    # 1) FinMind 自動抓
    rows = []
    if requests is not None:
        try:
            r = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={
                    "dataset": "TaiwanStockDividend",
                    "data_id": sid,
                    "start_date": f"{start_year}-01-01",
                },
                timeout=15,
            )
            if r.ok:
                rows = r.json().get("data", []) or []
        except Exception:
            rows = []

    cash_total = 0.0
    stock_total = 0.0
    events = []
    next_ex = None
    for row in rows:
        cash_ex  = (row.get("CashExDividendTradingDate")  or "").strip()
        stock_ex = (row.get("StockExDividendTradingDate") or "").strip()
        cash_amt = float(row.get("CashEarningsDistribution") or 0) + \
                   float(row.get("CashStatutorySurplus")    or 0)
        stock_amt = float(row.get("StockEarningsDistribution") or 0) + \
                    float(row.get("StockStatutorySurplus")    or 0)
        # 只計入除權息日 ≥ 今天（已宣布尚未派發）
        if cash_ex and cash_ex >= today_str and cash_amt > 0:
            cash_total += cash_amt
            events.append({
                "kind": "cash", "amount": round(cash_amt, 4),
                "ex_date": cash_ex,
                "pay_date": (row.get("CashDividendPaymentDate") or "").strip(),
                "year": (row.get("year") or "").strip(),
                "source": "finmind",
            })
            if next_ex is None or cash_ex < next_ex: next_ex = cash_ex
        if stock_ex and stock_ex >= today_str and stock_amt > 0:
            stock_total += stock_amt
            events.append({
                "kind": "stock", "amount": round(stock_amt, 4),
                "ex_date": stock_ex,
                "pay_date": "",
                "year": (row.get("year") or "").strip(),
                "source": "finmind",
            })
            if next_ex is None or stock_ex < next_ex: next_ex = stock_ex

    # 2) xlsx 手填 override — 蓋過 FinMind（用戶版優先）
    source = "finmind" if events else "none"
    if manual_override:
        m_cash    = float(manual_override.get("cash")    or 0)
        m_stock   = float(manual_override.get("stock")   or 0)
        m_ex_date = (manual_override.get("ex_date") or "").strip() or None
        if m_cash > 0 or m_stock > 0:
            # 用 xlsx 取代 FinMind（手填代表用戶從公告抓到的最新）
            cash_total  = m_cash
            stock_total = m_stock
            events = []
            if m_cash > 0:
                events.append({"kind":"cash","amount":round(m_cash,4),
                               "ex_date":m_ex_date or "公告中","pay_date":"","year":str(datetime.today().year),
                               "source":"manual"})
            if m_stock > 0:
                events.append({"kind":"stock","amount":round(m_stock,4),
                               "ex_date":m_ex_date or "公告中","pay_date":"","year":str(datetime.today().year),
                               "source":"manual"})
            next_ex = m_ex_date  # 可能是 None（公告中尚未排定）
            source = "manual"
    return {
        "cash_per_share":  round(cash_total, 4),
        "stock_per_share": round(stock_total, 4),
        "events": events,
        "next_ex_date": next_ex,
        "source": source,
    }

BASE_DIR    = os.path.expanduser("~/Desktop/Stock Selection Strategy")
CACHE_DIR   = os.path.join(BASE_DIR, "cache")
PRICE_CACHE = os.path.join(CACHE_DIR, "price_data.pkl")
OUTPUT_DIR  = BASE_DIR

def load_stock_data():
    with open(PRICE_CACHE, "rb") as f:
        return pickle.load(f)

def get_today_results():
    """讀取今天最新的 CSV 結果"""
    today = datetime.today().strftime("%Y%m%d")
    csv_path = os.path.join(OUTPUT_DIR, f"飆股日報_{today}.csv")
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path, dtype={"股票代碼": str})
    # 找最近的 CSV
    import glob
    files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "飆股日報_*.csv")), reverse=True)
    if files:
        return pd.read_csv(files[0], dtype={"股票代碼": str})
    return pd.DataFrame()

def prepare_chart_data(sid, pc, days=60):
    """準備單支股票的 K 線資料"""
    df = pc.get(sid)
    if df is None or df.empty: return None
    df = df.copy().tail(days)
    for c in ["open","high","low","close","volume"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open","high","low","close","volume"])
    if len(df) < 5: return None
    return df

_STOCK_LIST_CACHE = os.path.join(os.path.expanduser("~/Desktop/Stock Selection Strategy"),
                                  "cache", "stock_list_cache.json")
def load_stock_names():
    """從 stock_list_cache.json 取得 {代碼: 中文名} 字典"""
    if not os.path.exists(_STOCK_LIST_CACHE):
        return {}
    try:
        with open(_STOCK_LIST_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        data = d.get("data", d)  # 兼容兩種結構
        return {sid: info.get("name", sid) for sid, info in data.items() if isinstance(info, dict)}
    except Exception:
        return {}

def build_breakout_data(pc, name_map=None, tdcc=None):
    """
    找「即將突破」候選股，條件：
    1. 型態：底部反轉（w_bottom / triple_bottom / head_shoulder_bottom / ascending_triangle_bottom）
    2. 距頸線 3% 以內：last_close >= neckline × 0.97
    3. 量能放大：今日量 > MA20 量 × 2
    依據與頸線的距離絕對值排序，最多回傳 10 支。
    """
    BOTTOM_PATTERNS = {"w_bottom", "triple_bottom", "head_shoulder_bottom", "ascending_triangle_bottom"}
    name_map = name_map or {}
    print("⚡ 掃描即將突破候選股...")
    candidates = []
    scanned = 0
    for sid, df in pc.items():
        if df is None or df.empty:
            continue
        df = df.copy().tail(60).reset_index(drop=True)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
        if len(df) < 25:
            continue
        scanned += 1

        # 量能門檻（先過濾，比型態便宜）
        last_vol = float(df["volume"].iloc[-1])
        ma20_vol = float(df["volume"].rolling(20).mean().iloc[-1])
        if pd.isna(ma20_vol) or ma20_vol <= 0:
            continue
        if last_vol <= ma20_vol * 2:
            continue

        # 型態識別
        pat = detect_pattern(df, n_bars=30)
        if not pat or pat.get("pattern_en") not in BOTTOM_PATTERNS:
            continue

        # 取頸線
        drawing = get_pattern_drawing(df, pat["pattern_en"], n_bars=30)
        neckline = None
        for line in drawing.get("lines", []):
            if "頸線" in (line.get("label") or ""):
                neckline = float(line.get("y1") or 0)
                break
        if not neckline or neckline <= 0:
            continue

        last_close = float(df["close"].iloc[-1])
        # 距頸線範圍：0.93 × neckline ≤ last_close ≤ 1.1 × neckline
        if last_close < neckline * 0.93 or last_close > neckline * 1.1:
            continue

        dist_pct = (last_close - neckline) / neckline * 100

        # 補 offset 讓畫線對齊 60 根畫布
        offset = max(0, len(df) - 30)
        for line in drawing.get("lines", []):
            line["x1"] += offset
            line["x2"] += offset
        for mark in drawing.get("marks", []):
            mark["x"] += offset

        dates  = df["date"].tolist()
        opens  = df["open"].tolist()
        highs  = df["high"].tolist()
        lows   = df["low"].tolist()
        closes = df["close"].tolist()
        vols   = df["volume"].tolist()
        ma5    = df["close"].rolling(5).mean().round(2).tolist()
        ma20   = df["close"].rolling(20).mean().round(2).tolist()
        vma20  = [v if pd.notna(v) else None
                  for v in df["volume"].rolling(20).mean().round(0).tolist()]

        # 飆股在線等 61 集策略匹配
        b_strategies = match_strategies(df, {"pattern_en": pat["pattern_en"], "name": pat["pattern_name"], "cat": pat.get("category","")})
        b_summary = summarize_action(b_strategies)

        # 籌碼分析（含 history、chart_points、cost_zone）
        b_holding = None
        if tdcc:
            b_holding = get_holding_summary(tdcc, sid)
            if b_holding:
                enrich_holding_for_chart(b_holding, sid, df, pc)

        candidates.append({
            "sid": sid,
            "name": name_map.get(sid, sid),
            "pattern_name": pat["pattern_name"],
            "pattern_en":   pat["pattern_en"],
            "neckline":  round(neckline, 2),
            "current":   round(last_close, 2),
            "dist_pct":  round(dist_pct, 2),
            "vol_ratio": round(last_vol / ma20_vol, 2),
            "_sort":     abs(dist_pct),
            "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": closes, "vols": vols,
            "ma5": ma5, "ma20": ma20, "vma20": vma20,
            "lines": drawing.get("lines", []),
            "marks": drawing.get("marks", []),
            "strategies":   b_strategies,
            "strat_action": b_summary["action"],
            "strat_color":  b_summary["color"],
            "strat_score":  b_summary["score"],
            "holding":      b_holding,
        })

    candidates.sort(key=lambda x: x["_sort"])
    top = candidates[:10]
    print(f"  掃描 {scanned} 支，命中 {len(candidates)} 支，取前 {len(top)} 支")
    for c in top:
        print(f"  ⚡ {c['sid']} {c['name']}：{c['pattern_name']}，"
              f"距頸線 {c['dist_pct']:+.2f}%（現價 {c['current']} / 頸線 {c['neckline']}），量比 {c['vol_ratio']}x")
    # 移除排序鍵
    for c in top:
        c.pop("_sort", None)
    return top

# ── 法人目標價（yfinance）+ 24 小時快取 ───────────
_ANALYST_CACHE = os.path.join(os.path.expanduser("~/Desktop/Stock Selection Strategy"),
                               "cache", "analyst_targets.json")

def fetch_analyst_targets(sid, is_etf=False):
    """抓今年法人預估股價（高/中/低 + 分析師家數 + 評等）。ETF 沒有，回傳 None。"""
    if is_etf:
        return None
    # 24 小時快取
    cache = {}
    if os.path.exists(_ANALYST_CACHE):
        try:
            import time as _t
            if _t.time() - os.path.getmtime(_ANALYST_CACHE) < 86400:
                with open(_ANALYST_CACHE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if sid in cache:
                    return cache[sid]
        except Exception:
            pass
    try:
        import yfinance as yf
    except ImportError:
        return None
    info = None
    for suffix in [".TW", ".TWO"]:
        try:
            t = yf.Ticker(sid + suffix)
            info = t.info
            if info and info.get("targetMeanPrice") is not None:
                break
            info = None
        except Exception:
            info = None
    if not info:
        # 即使抓不到也存 None 避免重複嘗試
        cache[sid] = None
        try:
            with open(_ANALYST_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
        except Exception: pass
        return None
    rec = info.get("recommendationKey", "none")
    rec_label_map = {
        "strong_buy": "強力買進", "buy": "買進", "hold": "持有",
        "underperform": "減碼",  "sell": "賣出",  "strong_sell": "強烈賣出",
        "none": "無評等",
    }
    targets = {
        "high":     info.get("targetHighPrice"),
        "median":   info.get("targetMedianPrice") or info.get("targetMeanPrice"),
        "low":      info.get("targetLowPrice"),
        "mean":     info.get("targetMeanPrice"),
        "analysts": info.get("numberOfAnalystOpinions"),
        "rec_key":  rec,
        "rec_label": rec_label_map.get(rec, rec),
        "rec_mean": info.get("recommendationMean"),
    }
    cache[sid] = targets
    try:
        with open(_ANALYST_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception: pass
    return targets

def make_holding_commentary(h, targets):
    """
    全面性持股分析（飆股在線等多集策略整合版）

    產出五大面向（道氏理論 / 葛蘭碧 / 型態學 / 籌碼 / 法人）+ 飆股在線等策略匹配 +
    具體進出場價位（加碼點 / 停損點 / 目標價）+ 操作建議。
    """
    import numpy as _np
    import pandas as _pd

    is_etf  = h.get("is_etf", False)
    current = h["current"]
    closes  = h.get("closes", []) or []
    highs   = h.get("highs", []) or []
    lows    = h.get("lows", []) or []
    vols    = h.get("vols", []) or []
    ma5_arr = h.get("ma5", []) or []
    ma20_arr= h.get("ma20", []) or []
    pat     = h.get("pattern") or {}
    holding = h.get("holding") or {}
    cz      = (holding.get("cost_zone") if isinstance(holding, dict) else None) or h.get("cost_zone")
    hist    = (holding.get("history") if isinstance(holding, dict) else []) or []
    strategies = h.get("strategies", []) or []

    pat_name = pat.get("name", "") if isinstance(pat, dict) else ""
    pat_cat  = pat.get("cat", "")  if isinstance(pat, dict) else ""

    # ── 1. 趨勢判讀（道氏理論 Ep40/41 + 均線 Ep4）──
    last_ma5  = next((v for v in reversed(ma5_arr)  if v is not None and not (isinstance(v,float) and v!=v)), None)
    last_ma20 = next((v for v in reversed(ma20_arr) if v is not None and not (isinstance(v,float) and v!=v)), None)
    ma20_3ago = ma20_arr[-4] if len(ma20_arr) >= 4 else None

    # 月線方向
    ma20_dir = "盤整"
    if last_ma20 and ma20_3ago and not (isinstance(ma20_3ago,float) and ma20_3ago!=ma20_3ago):
        if last_ma20 > ma20_3ago * 1.005:
            ma20_dir = "上揚"
        elif last_ma20 < ma20_3ago * 0.995:
            ma20_dir = "下彎"

    # 價 vs 月線
    price_vs_ma20 = "—"
    if last_ma20:
        if current >= last_ma20 * 1.05:    price_vs_ma20 = "強勢站穩月線"
        elif current >= last_ma20:          price_vs_ma20 = "站穩月線"
        elif current >= last_ma20 * 0.97:   price_vs_ma20 = "貼近月線"
        else:                                price_vs_ma20 = "跌破月線"

    # 5/20 排列
    ma_align = "—"
    if last_ma5 and last_ma20:
        if last_ma5 > last_ma20 * 1.005:    ma_align = "多頭（5>20）"
        elif last_ma5 < last_ma20 * 0.995:  ma_align = "空頭（5<20）"
        else:                                ma_align = "糾結"

    # 趨勢階段判讀（道氏理論）
    if ma20_dir == "上揚" and price_vs_ma20 in ("站穩月線", "強勢站穩月線") and ma_align.startswith("多頭"):
        trend_stage = "🟢 主升段"
    elif ma20_dir == "上揚" and price_vs_ma20 == "貼近月線":
        trend_stage = "🟢 多頭回檔（健康）"
    elif ma20_dir == "盤整":
        trend_stage = "🟡 盤整待突破"
    elif ma20_dir == "下彎" and price_vs_ma20 == "跌破月線":
        trend_stage = "🔴 空頭走勢"
    else:
        trend_stage = "🟡 趨勢混沌"

    # ── 2. 動能 / 報酬 ──
    ret5  = ((closes[-1] - closes[-6])  / closes[-6]  * 100) if len(closes) >= 6  else None
    ret20 = ((closes[-1] - closes[-21]) / closes[-21] * 100) if len(closes) >= 21 else None

    # KD（用簡化公式）
    kd_status = ""
    if len(closes) >= 15:
        try:
            high9 = _pd.Series(highs).rolling(9).max()
            low9  = _pd.Series(lows).rolling(9).min()
            rsv = ((_pd.Series(closes) - low9) / (high9 - low9) * 100).fillna(50)
            k_arr = rsv.ewm(com=2, adjust=False).mean().values
            d_arr = _pd.Series(k_arr).ewm(com=2, adjust=False).mean().values
            k_now, d_now = k_arr[-1], d_arr[-1]
            k_pre, d_pre = k_arr[-2], d_arr[-2]
            if k_pre < d_pre and k_now > d_now:
                kd_status = f"KD 黃金交叉（K={k_now:.0f}）"
            elif k_pre > d_pre and k_now < d_now:
                kd_status = f"KD 死亡交叉（K={k_now:.0f}）"
            elif k_now >= 80:
                kd_status = f"KD 高檔鈍化（K={k_now:.0f}）"
            elif k_now <= 20:
                kd_status = f"KD 低檔鈍化（K={k_now:.0f}）"
            elif k_now > d_now:
                kd_status = f"KD 多頭中（K={k_now:.0f}）"
            else:
                kd_status = f"KD 空頭中（K={k_now:.0f}）"
        except Exception:
            pass

    # 乖離率（Ep52）
    bias_pct = None
    bias_note = ""
    if last_ma20:
        bias_pct = (current - last_ma20) / last_ma20 * 100
        if bias_pct >= 15:    bias_note = f"+{bias_pct:.1f}% 嚴重超漲"
        elif bias_pct >= 10:  bias_note = f"+{bias_pct:.1f}% 偏高警覺"
        elif bias_pct <= -15: bias_note = f"{bias_pct:.1f}% 嚴重超跌"
        elif bias_pct <= -10: bias_note = f"{bias_pct:.1f}% 偏低可承接"
        else:                 bias_note = f"{bias_pct:+.1f}% 正常"

    # ── 3. 籌碼結構（飆股在線等：朱家泓常講的集保分析）──
    chip_text = ""
    chip_score = 0  # 籌碼面加分
    if not is_etf and holding:
        major = holding.get("major", 0)
        whale = holding.get("whale", 0)
        small = holding.get("small", 0)
        persons = holding.get("persons", 0)
        # 大戶連 N 增/減
        consec, consec_dir = 0, 0
        if len(hist) >= 2:
            for i in range(1, len(hist)):
                diff = hist[i].get("major", 0) - hist[i-1].get("major", 0)
                if diff > 0:
                    if consec_dir >= 0: consec += 1
                    else: consec = 1
                    consec_dir = 1
                elif diff < 0:
                    if consec_dir <= 0: consec += 1
                    else: consec = 1
                    consec_dir = -1
                else:
                    consec, consec_dir = 0, 0
        # 股東人數連 N 減（散戶退出 = 籌碼集中）
        persons_down_weeks = 0
        if len(hist) >= 2:
            for i in range(1, len(hist)):
                p_now  = hist[i].get("persons", 0)
                p_pre  = hist[i-1].get("persons", 0)
                if p_now > 0 and p_pre > 0 and p_now < p_pre:
                    persons_down_weeks += 1
                else:
                    break  # 從最後往前算連續

        chip_parts = []
        if whale >= 70:
            chip_parts.append(f"千張大戶持股 {whale:.0f}%（極度集中，主力鎖籌）")
            chip_score += 2
        elif whale >= 50:
            chip_parts.append(f"千張大戶持股 {whale:.0f}%（高度集中）")
            chip_score += 1
        elif major >= 60:
            chip_parts.append(f"大戶 {major:.0f}%（偏高，籌碼安定）")
            chip_score += 1
        elif major <= 35:
            chip_parts.append(f"大戶 {major:.0f}%（散戶持有為主，需警覺）")
            chip_score -= 1

        if consec >= 3 and consec_dir > 0:
            chip_parts.append(f"📈 大戶連{consec}增（明顯吸籌）")
            chip_score += 2
        elif consec >= 2 and consec_dir > 0:
            chip_parts.append(f"📈 大戶連{consec}增")
            chip_score += 1
        elif consec >= 2 and consec_dir < 0:
            chip_parts.append(f"📉 大戶連{consec}減（出貨警覺）")
            chip_score -= 2

        if persons_down_weeks >= 2 and consec_dir > 0:
            chip_parts.append(f"股東人數連{persons_down_weeks}週減（籌碼向大戶集中）")
            chip_score += 1

        if cz and isinstance(cz, dict) and cz.get("low") is not None:
            cz_low = cz["low"]; cz_high = cz["high"]; cz_dist = cz["dist"]
            if abs(cz_dist) <= 5:
                chip_parts.append(f"📍 主力成本區 {cz_low}~{cz_high}（現價貼近 {cz_dist:+.1f}%，風險報酬佳 ✅）")
                chip_score += 1
            elif cz_dist > 20:
                chip_parts.append(f"📍 主力成本區 {cz_low}~{cz_high}（現價已 +{cz_dist:.1f}% 偏離 ⚠️）")
                chip_score -= 1
            else:
                chip_parts.append(f"📍 主力成本區 {cz_low}~{cz_high}（{cz_dist:+.1f}%）")

        chip_text = "；".join(chip_parts) if chip_parts else "—"

    # ── 4. 法人面 ──
    upside_pct = None
    target_text = ""
    rec_label = ""
    target_high = target_low = target_med = None
    if targets and targets.get("median"):
        target_med  = targets["median"]
        target_high = targets.get("high")
        target_low  = targets.get("low")
        upside_pct = (target_med - current) / current * 100
        rec_label = targets.get("rec_label", "無評等")
        analysts = targets.get("analysts") or 0
        target_text = (f"{analysts} 位分析師：中位 {target_med:.1f}（高 {target_high:.1f} / 低 {target_low:.1f}），"
                       f"潛在 {upside_pct:+.1f}%，評等「{rec_label}」")

    # ── 4.5. 消息面分析（近 7 天新聞）──
    news_sentiment = None
    rev_for_news = h.get("rev") or {}
    news_list = rev_for_news.get("news") or []
    if news_list:
        try:
            from news_sentiment import analyze_news_sentiment
            news_sentiment = analyze_news_sentiment(news_list)
        except Exception:
            news_sentiment = None

    # ── 5. 飆股在線等策略匹配摘要 ──
    buy_eps  = sorted({m.get("ep") for m in strategies if m.get("type") == "buy"})
    sell_eps = sorted({m.get("ep") for m in strategies if m.get("type") == "sell"})
    warn_eps = sorted({m.get("ep") for m in strategies if m.get("type") == "warning"})
    strat_text = ""
    if strategies:
        parts = []
        if buy_eps:  parts.append(f"買訊 {len(buy_eps)} 條（Ep{','.join(map(str,buy_eps[:5]))}{'...' if len(buy_eps)>5 else ''}）")
        if sell_eps: parts.append(f"賣訊 {len(sell_eps)} 條（Ep{','.join(map(str,sell_eps[:5]))}{'...' if len(sell_eps)>5 else ''}）")
        if warn_eps: parts.append(f"警示 {len(warn_eps)} 條")
        strat_text = "／".join(parts) if parts else "無顯著訊號"

    # ── 6. 計算具體價位（加碼 / 停損 / 目標）──
    entry_price = None    # 加碼點：拉回月線
    stop_loss   = None    # 停損點
    target_price = None   # 目標價
    if last_ma20:
        entry_price = round(last_ma20, 2)
    # 停損：月線下方 5%、cost_zone 下緣、近期低點 取較高者作為停損（多頭適用）
    candidates_stop = []
    if last_ma20: candidates_stop.append(last_ma20 * 0.95)
    if cz and cz.get("low"): candidates_stop.append(cz["low"] * 0.95)
    if len(lows) >= 10: candidates_stop.append(min(lows[-10:]) * 0.98)
    if candidates_stop:
        stop_loss = round(max(candidates_stop), 2)
    # 目標價：法人中位 / 近期高點 / 1.2 倍取最低（保守）
    candidates_tgt = []
    if target_med:                candidates_tgt.append(target_med)
    if len(highs) >= 60:           candidates_tgt.append(max(highs[-60:]) * 1.05)
    candidates_tgt.append(current * 1.20)
    if candidates_tgt:
        # 用「中位數」當目標
        candidates_tgt.sort()
        target_price = round(candidates_tgt[len(candidates_tgt)//2], 2)

    # ── 6.5. 進階訊號（變盤線 / 回後買 / 起漲點 / 過熱）──
    adv = None
    try:
        from advanced_signals import combined_action
        # 用完整 OHLCV 重建一個小 df
        _df_adv = _pd.DataFrame({
            "open":   h.get("opens", []) or [],
            "high":   h.get("highs", []) or [],
            "low":    h.get("lows", []) or [],
            "close":  h.get("closes", []) or [],
            "volume": h.get("vols", []) or [],
        })
        if len(_df_adv) >= 20 and not is_etf:
            adv = combined_action(_df_adv)
    except Exception as _e:
        adv = None

    # ── 7. 綜合評分 / 操作建議 ──
    bull = 0; bear = 0
    # 趨勢
    if "主升段" in trend_stage:        bull += 3
    elif "多頭回檔" in trend_stage:    bull += 1
    elif "盤整" in trend_stage:        bull += 0
    elif "空頭" in trend_stage:        bear += 3
    elif "混沌" in trend_stage:        bear += 1
    # 月線位置
    if price_vs_ma20 == "強勢站穩月線": bull += 1
    if price_vs_ma20 == "站穩月線":      bull += 1
    if price_vs_ma20 == "跌破月線":      bear += 2
    # 短期動能
    if ma_align.startswith("多頭"):    bull += 1
    if ma_align.startswith("空頭"):    bear += 1
    # KD
    if "黃金交叉" in kd_status:        bull += 1
    if "死亡交叉" in kd_status:        bear += 1
    # 乖離
    if bias_pct is not None:
        if bias_pct >= 15:             bear += 1
        elif bias_pct <= -15:          bull += 1
    # 型態
    if pat_cat == "底部反轉":           bull += 2
    if pat_cat == "頭部反轉":           bear += 2
    # 籌碼
    bull += max(0,  chip_score)
    bear += max(0, -chip_score)
    # 法人
    rec_key = (targets or {}).get("rec_key", "")
    if rec_key == "strong_buy":  bull += 2
    elif rec_key == "buy":        bull += 1
    elif rec_key in ("sell","strong_sell","underperform"): bear += 2
    if upside_pct is not None:
        if upside_pct >= 20:    bull += 2
        elif upside_pct >= 10:  bull += 1
        elif upside_pct <= -10: bear += 1
    # 飆股在線等策略 net
    bull += min(3, len(buy_eps))   # 買訊最多加 3
    bear += min(3, len(sell_eps))

    # ── 進階訊號加權（最重要：直接影響操作建議）──
    adv_summary_text = ""
    if adv:
        s = adv["signals"]
        if s["doji_reversal"]["signal"] == "top":
            bear += 3   # 高檔變盤 → 強烈賣訊
        elif s["doji_reversal"]["signal"] == "bottom":
            bull += 3
        if s["cons_down"]["signal"]:
            bear += 2
        if s["pullback"]["signal"]:  # True 表示「續漲突破」
            bull += 4
        elif s["pullback"]["stage"] == "確認站穩":
            bull += 1   # 等待確認，輕度看多
        if s["breakout_start"]["signal"]:
            bull += 4
        if s["overext"]["extended"]:
            bear += 2   # 過熱 → 不該追
        adv_summary_text = adv["summary"]

    net = bull - bear

    # ETF：固定建議
    if is_etf:
        action = "按計畫續抱（被動追蹤）"
        action_color = "#58a6ff"
        position_advice = "ETF 採被動配置，無需主動操作"
    else:
        # 加上「進場後嚴設 -3% 停損」（5/4-5/15 回測顯示 -3% 停損
        # 讓平均報酬從 -1.0% 提升到 +1.0%，並把 56 支中的 44 支虧損股
        # 自動清出，飆股仍能完整持有到 +30% 以上）
        tight_sl = round(current * 0.97, 2) if current else stop_loss
        if net >= 7:
            action, action_color = "🚀 強力買進 / 加碼至 30% 部位", "#3fb950"
            position_advice = f"可在現價或回測 {entry_price or '月線'} 加碼至 30%；嚴設停損 {tight_sl}（-3%）；目標 {target_price}"
        elif net >= 4:
            action, action_color = "📈 逢低承接 / 加碼至 20% 部位", "#56d364"
            position_advice = f"分批承接，回測 {entry_price or '月線'} 是好進場點；嚴設停損 {tight_sl}（-3%）"
        elif net >= 2:
            action, action_color = "✅ 續抱為主 / 拉回加碼", "#7ee787"
            position_advice = f"維持目前部位；若回測 {entry_price or '月線'} 不破可加碼；停損 {stop_loss}"
        elif net >= -1:
            action, action_color = "👀 中性觀望", "#f0c040"
            position_advice = f"訊號不明，維持現有部位觀察；若跌破 {stop_loss} 減碼"
        elif net >= -3:
            action, action_color = "⚠️ 減碼 30% / 設停損", "#f0a500"
            position_advice = f"先減碼 30%；跌破 {stop_loss} 再減碼 50%"
        else:
            action, action_color = "🔴 建議出清 / 規避", "#f85149"
            position_advice = f"逢反彈分批出清，不可凹單；若仍持有，停損嚴設於 {stop_loss}"

    # ── 8. 結構化評語（給 UI）──
    if is_etf:
        sections = {
            "trend":       f"{trend_stage}｜月線{ma20_dir}｜{price_vs_ma20}",
            "technical":   f"{kd_status or '—'}｜乖離 {bias_note}｜近 5 日 {ret5:+.1f}%" if ret5 is not None else "—",
            "chip":        "ETF 採被動配置，無集保資料",
            "fundamental": "ETF 無分析師覆蓋",
            "strategy":    strat_text or "—",
        }
    else:
        # 進階訊號文字（變盤線 / 回後買 / 起漲點 / 過熱）
        adv_lines = []
        if adv:
            for w in adv["warnings"]:
                adv_lines.append(w)
            for o in adv["opportunities"]:
                adv_lines.append(o)
        adv_text = "；".join(adv_lines) if adv_lines else "目前無顯著進階訊號"

        sections = {
            "trend":       f"{trend_stage}｜月線{ma20_dir}｜{price_vs_ma20}｜短中期 {ma_align}",
            "technical":   "；".join(filter(None, [
                f"型態「{pat_name}」（{pat_cat}）" if pat_name and pat_name != "觀察中" else "",
                kd_status,
                f"乖離 {bias_note}" if bias_note else "",
                f"近 5 日 {ret5:+.1f}%" if ret5 is not None else "",
                f"近 20 日 {ret20:+.1f}%" if ret20 is not None else "",
            ])) or "—",
            "chip":        chip_text or "—",
            "fundamental": target_text or "暫無分析師覆蓋",
            "strategy":    strat_text or "無顯著策略訊號",
            "advanced":    adv_text,   # ← 新增：精細訊號
        }

    # 一句話摘要（保留向下相容，把進階訊號併入最後一段）
    extras = []
    if not is_etf and sections.get("advanced") and sections["advanced"] != "目前無顯著進階訊號":
        extras.append(sections["advanced"])
    commentary = "；".join([
        sections["trend"], sections["technical"],
    ] + ([sections["chip"]] if not is_etf else []) + ([sections["fundamental"]] if target_text else []) + extras) + "。"

    return {
        "commentary":     commentary,
        "action":         action,
        "action_color":   action_color,
        "score":          net,
        "upside_pct":     None if upside_pct is None else round(upside_pct, 1),
        "target_summary": target_text,
        "sections":       sections,
        "entry_price":    entry_price,
        "stop_loss":      stop_loss,
        "target_price":   target_price,
        "position_advice": position_advice,
        "news_sentiment":  news_sentiment,
        "buy_signals":    len(buy_eps),
        "sell_signals":   len(sell_eps),
        "warn_signals":   len(warn_eps),
        "advanced":       adv,    # 完整訊號物件（給 UI 細節展示用）
        "advanced_summary": adv_summary_text,
    }

def build_pullback_data(pc, tdcc=None, rev_data=None, name_map=None,
                         exclude_sids=None, max_picks=10):
    """
    🎯 拉回月線買點選股
    條件：
      ① 趨勢向上：月線(MA20) 向上 + 季線(MA60) 向上
      ② 短線強勢：MA5 > MA20
      ③ 拉回貼近月線：MA20 < 收盤 ≤ MA20 × 1.03（0% ~ +3%）
      ④ 拉回量縮：近 3 日均量 < 20 日均量 × 1.0
    回傳 list of dict（shape 跟 holdings 一樣，可直接用 holdings 卡片渲染）
    """
    print("🎯 掃描拉回月線買點...")
    name_map = name_map or {}
    rev_data = rev_data or {}
    exclude  = set(exclude_sids or [])
    picks = []
    scanned = 0

    for sid, df_raw in pc.items():
        if sid in exclude:
            continue
        if df_raw is None or df_raw.empty:
            continue
        df = df_raw.copy().tail(80).reset_index(drop=True)
        for c in ["open","high","low","close","volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
        if len(df) < 65:   # 至少 65 根才有 MA60
            continue
        scanned += 1

        closes = df["close"].values
        vols   = df["volume"].values
        ma5_s  = df["close"].rolling(5).mean()
        ma20_s = df["close"].rolling(20).mean()
        ma60_s = df["close"].rolling(60).mean()
        vma20_s = df["volume"].rolling(20).mean()

        last_close = float(closes[-1])
        last_ma5   = float(ma5_s.iloc[-1])
        last_ma20  = float(ma20_s.iloc[-1])
        last_ma60  = float(ma60_s.iloc[-1])
        ma20_5ago  = float(ma20_s.iloc[-6])  if len(df) >= 26 else None
        ma60_10ago = float(ma60_s.iloc[-11]) if len(df) >= 71 else None

        if any(v is None or (isinstance(v,float) and v != v)
               for v in [last_ma5, last_ma20, last_ma60, ma20_5ago, ma60_10ago]):
            continue

        # 條件 1：趨勢向上
        if not (last_ma20 > ma20_5ago and last_ma60 > ma60_10ago):
            continue
        # 條件 2：短線強勢
        if not (last_ma5 > last_ma20):
            continue
        # 條件 3：拉回貼近月線
        if not (last_ma20 < last_close <= last_ma20 * 1.03):
            continue
        dist_pct = (last_close - last_ma20) / last_ma20 * 100   # 0% ~ +3%
        # 條件 4：拉回量縮
        recent3_avg = float(np.mean(vols[-3:]))
        vma20_last  = float(vma20_s.iloc[-1])
        if not (recent3_avg < vma20_last):
            continue
        vol_shrink = recent3_avg / vma20_last  # 越小越好（量縮越明顯）

        # ── 通過全部條件 → 做完整 enrichment ──
        # K 線資料（畫圖用）
        df60 = df.tail(60).reset_index(drop=True)
        dates  = df60["date"].tolist()
        opens  = df60["open"].tolist()
        highs  = df60["high"].tolist()
        lows   = df60["low"].tolist()
        cls    = df60["close"].tolist()
        vs     = df60["volume"].tolist()
        ma5_arr  = df60["close"].rolling(5).mean().round(2).tolist()
        ma20_arr = df60["close"].rolling(20).mean().round(2).tolist()
        vma20_arr = [v if pd.notna(v) else None
                     for v in df60["volume"].rolling(20).mean().round(0).tolist()]

        # 型態識別
        pattern_info = None
        sr = {"support": [], "resistance": []}
        try:
            pat = detect_pattern(df60, n_bars=30)
            pat_en = pat["pattern_en"] if pat else ""
            drawing = get_pattern_drawing(df60, pat_en, n_bars=30) if pat else {"lines":[], "marks":[]}
            offset = max(0, len(df60) - 30)
            for line in drawing.get("lines",[]):
                line["x1"] += offset; line["x2"] += offset
            for mark in drawing.get("marks",[]):
                mark["x"] += offset
            pattern_info = {
                "name": pat["pattern_name"] if pat else "觀察中",
                "desc": pat["description"]  if pat else "",
                "cat":  pat["category"]     if pat else "",
                "vp":   pat["vol_price"]    if pat else "",
                "lines": drawing["lines"], "marks": drawing["marks"],
            }
            sr = get_support_resistance(df60, n_bars=60)
        except Exception:
            pass

        # 籌碼（含 cost_zone / chart_points）
        holding_summary = None
        if tdcc:
            holding_summary = get_holding_summary(tdcc, sid)
            if holding_summary:
                enrich_holding_for_chart(holding_summary, sid, df60, pc)

        # 法人目標
        targets = fetch_analyst_targets(sid, is_etf=False)

        # 模擬 holding card 結構
        card = {
            "sid":  sid,
            "name": name_map.get(sid, sid),
            "is_etf": False,
            "shares": 0,           # 非實際持股
            "cost":   None,
            "current": round(last_close, 2),
            "market_value": 0, "cost_value": 0, "pl_amt": 0, "pl_pct": 0,
            "dividend_cash_per_share": 0, "dividend_stock_per_share": 0,
            "dividend_cash_amount": 0, "dividend_stock_amount": 0,
            "dividend_new_shares": 0, "dividend_amount": 0,
            "dividend_events": [], "dividend_next_ex_date": None,
            "total_return_amt": 0, "total_return_pct": 0,
            "src": "快取",
            "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": cls, "vols": vs,
            "ma5": ma5_arr, "ma20": ma20_arr, "vma20": vma20_arr,
            "pattern": pattern_info,
            "support": sr.get("support", []),
            "resistance": sr.get("resistance", []),
            "holding": holding_summary,
            "targets": targets,
            "is_pullback": True,
            "pullback_dist_pct": round(dist_pct, 2),
            "pullback_vol_ratio": round(vol_shrink, 2),
            "pullback_ma20": round(last_ma20, 2),
            "pullback_ma60": round(last_ma60, 2),
        }
        # 飆股在線等策略
        strategies = match_strategies(df60, pattern_info)
        strat_summary = summarize_action(strategies)
        card["strategies"]   = strategies
        card["strat_action"] = strat_summary["action"]
        card["strat_color"]  = strat_summary["color"]
        card["strat_score"]  = strat_summary["score"]
        # 評語 + 價位建議
        commentary_pack = make_holding_commentary(card, targets)
        card.update(commentary_pack)
        # 新聞（從 rev_data 取）
        card["rev"] = rev_data.get(sid, {})
        # 消息面分析
        try:
            from news_sentiment import analyze_news_sentiment
            news_list = (card["rev"] or {}).get("news") or []
            card["news_sentiment"] = analyze_news_sentiment(news_list) if news_list else None
        except Exception:
            card["news_sentiment"] = None
        # 排序分數：距月線越近越好，量縮越明顯越好；分數越小越前面
        card["_sort"] = dist_pct + vol_shrink * 2
        picks.append(card)

    picks.sort(key=lambda x: x["_sort"])
    top = picks[:max_picks]
    print(f"  掃描 {scanned} 支，命中 {len(picks)} 支，取前 {len(top)} 支")
    for c in top:
        print(f"  🎯 {c['sid']} {c['name']}：距月線 +{c['pullback_dist_pct']}%"
              f"，3日量比 {c['pullback_vol_ratio']:.2f}x，策略「{c['strat_action']}」")
    for c in top:
        c.pop("_sort", None)
    return top


def build_flash_picks(pc, tdcc=None, rev_data=None, name_map=None, idf=None, max_picks=30):
    """
    🌟 飆股區（V42_R6 冠軍策略，矩陣回測 +10% 命中率 32.4%）
    條件（10 條 + 族群雙重確認）：
      1) MA5 > MA20
      2) MA20 > MA60
      3) MA20 斜率 > 1.5%（從 2.5% 放寬）
      4) 量比 >= 1.0（從 1.3 放寬）
      5) 不是高檔變盤線
      6) 不是連跌轉空
      7) RSI14 < 80
      8) K 棒實體 > 40%
      9) RS20 > 5%（從 10% 放寬，配合族群過濾）
     10) 收盤在 MA20 × 1.02 ~ 1.30（從 1.10 放寬到 1.30）
     ★ 族群過濾在 main() 處用 sector_filter_v42 + S6 Top 7 過濾
    回傳 list of dict（shape 跟 holdings 一樣，可直接用 holdings 卡片渲染）
    """
    print("🌟 掃描 V42 飆股訊號...")
    name_map = dict(name_map or {})
    # 清空字串 + 從 industries fallback
    name_map = {k: v for k, v in name_map.items() if v and str(v).strip()}
    try:
        from sector_analyzer import fetch_all_industries as _fai_inline
        _ind_inline = _fai_inline()
        for _sid_i, _info_i in _ind_inline.items():
            existing = name_map.get(_sid_i, "")
            if (not existing or not str(existing).strip()) and _info_i.get("name"):
                name_map[_sid_i] = _info_i["name"]
    except Exception:
        pass
    rev_data = rev_data or {}
    picks = []
    scanned = 0

    # 大盤收盤序列（給 RS20 用）
    idx_closes = None
    if idf is not None and hasattr(idf, "empty") and not idf.empty:
        idx_closes = idf["close"].dropna().tolist()

    for sid, df_raw in pc.items():
        if df_raw is None or df_raw.empty:
            continue
        df = df_raw.copy().tail(120).reset_index(drop=True)
        for c in ["open","high","low","close","volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
        if len(df) < 65:
            continue
        scanned += 1

        t = len(df) - 1
        O = float(df["open"].iloc[t]); H = float(df["high"].iloc[t])
        L = float(df["low"].iloc[t]); C = float(df["close"].iloc[t])
        V = float(df["volume"].iloc[t])
        MA5  = float(df["close"].iloc[t-4:t+1].mean())
        MA20 = float(df["close"].iloc[t-19:t+1].mean())
        MA60 = float(df["close"].iloc[t-59:t+1].mean())
        VMA20 = float(df["volume"].iloc[t-19:t+1].mean())
        VB = V/VMA20 if VMA20 > 0 else 0
        Rng = H - L
        BR = abs(C-O)/Rng if Rng > 0 else 0

        # MA20 斜率
        ma20_5ago = float(df["close"].iloc[t-24:t-4].mean()) if t >= 24 else 0
        ma20_slope = (MA20 - ma20_5ago) / ma20_5ago if ma20_5ago > 0 else 0

        # RS20（相對大盤 20 日強度）
        RS = 0
        if idx_closes and t >= 20 and len(idx_closes) >= 21:
            s20 = C/float(df["close"].iloc[t-20]) - 1 if float(df["close"].iloc[t-20]) > 0 else 0
            RS = s20 - (idx_closes[-1]/idx_closes[-21] - 1)

        # RSI14
        delta_arr = df["close"].diff()
        up14 = delta_arr.clip(lower=0).iloc[t-13:t+1].mean()
        dn14 = (-delta_arr.clip(upper=0)).iloc[t-13:t+1].mean()
        rsi14 = 100 - 100/(1 + (up14/dn14)) if dn14 > 0 else 50

        # 變盤線 / 連跌轉空
        is_doji_top = False; is_cons_down = False
        try:
            from advanced_signals import doji_reversal as _dr, consecutive_down_after_up as _cd
            _drr = _dr(df); _cdr = _cd(df)
            is_doji_top = (_drr.get("signal") == "top")
            is_cons_down = _cdr.get("signal", False)
        except Exception:
            pass

        # V42_R6 條件（放寬版，搭配族群 Top 7 過濾）
        if not (MA5 > MA20
                and MA20 > MA60
                and ma20_slope > 0.015      # 放寬到 1.5%
                and VB >= 1.0                # 放寬到 1.0
                and not is_doji_top
                and not is_cons_down
                and rsi14 < 80
                and BR > 0.4
                and RS > 0.05                # 放寬到 5%
                and C > MA20 * 1.02
                and C < MA20 * 1.30):        # 放寬到 1.30
            continue

        # ── 命中 → 完整 enrichment（K 線、型態、籌碼、評語）──
        df60 = df.tail(60).reset_index(drop=True)
        dates  = df60["date"].tolist()
        opens  = df60["open"].tolist()
        highs  = df60["high"].tolist()
        lows   = df60["low"].tolist()
        cls    = df60["close"].tolist()
        vs     = df60["volume"].tolist()
        ma5_arr  = df60["close"].rolling(5).mean().round(2).tolist()
        ma20_arr = df60["close"].rolling(20).mean().round(2).tolist()
        vma20_arr = [v if pd.notna(v) else None
                     for v in df60["volume"].rolling(20).mean().round(0).tolist()]

        pattern_info = None
        sr = {"support": [], "resistance": []}
        try:
            pat = detect_pattern(df60, n_bars=30)
            pat_en = pat["pattern_en"] if pat else ""
            drawing = get_pattern_drawing(df60, pat_en, n_bars=30) if pat else {"lines":[], "marks":[]}
            offset = max(0, len(df60) - 30)
            for line in drawing.get("lines",[]):
                line["x1"] += offset; line["x2"] += offset
            for mark in drawing.get("marks",[]):
                mark["x"] += offset
            pattern_info = {
                "name": pat["pattern_name"] if pat else "觀察中",
                "desc": pat["description"]  if pat else "",
                "cat":  pat["category"]     if pat else "",
                "vp":   pat["vol_price"]    if pat else "",
                "lines": drawing["lines"], "marks": drawing["marks"],
            }
            sr = get_support_resistance(df60, n_bars=60)
        except Exception:
            pass

        holding_summary = None
        if tdcc:
            holding_summary = get_holding_summary(tdcc, sid)
            if holding_summary:
                enrich_holding_for_chart(holding_summary, sid, df60, pc)

        targets = fetch_analyst_targets(sid, is_etf=False)

        card = {
            "sid": sid, "name": name_map.get(sid, sid),
            "is_etf": False,
            "shares": 0, "cost": None,
            "current": round(C, 2),
            "market_value": 0, "cost_value": 0, "pl_amt": 0, "pl_pct": 0,
            "dividend_cash_per_share": 0, "dividend_stock_per_share": 0,
            "dividend_cash_amount": 0, "dividend_stock_amount": 0,
            "dividend_new_shares": 0, "dividend_amount": 0,
            "dividend_events": [], "dividend_next_ex_date": None,
            "total_return_amt": 0, "total_return_pct": 0,
            "src": "快取",
            "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": cls, "vols": vs,
            "ma5": ma5_arr, "ma20": ma20_arr, "vma20": vma20_arr,
            "pattern": pattern_info,
            "support": sr.get("support", []),
            "resistance": sr.get("resistance", []),
            "holding": holding_summary,
            "targets": targets,
            "is_flash": True,                              # 🌟 標記
            "flash_score": round((MA20-ma20_5ago)/ma20_5ago*100 + RS*100, 1),
            "flash_metrics": {
                "ma20_slope_pct": round(ma20_slope*100, 2),
                "rs20_pct": round(RS*100, 1),
                "vol_burst": round(VB, 2),
                "rsi14": round(rsi14, 0),
                "c_over_ma20": round(C/MA20, 3),
            },
        }
        strategies = match_strategies(df60, pattern_info)
        strat_summary = summarize_action(strategies)
        card["strategies"] = strategies
        card["strat_action"] = strat_summary["action"]
        card["strat_color"]  = strat_summary["color"]
        card["strat_score"]  = strat_summary["score"]

        commentary_pack = make_holding_commentary(card, targets)
        card.update(commentary_pack)
        card["rev"] = rev_data.get(sid, {})
        try:
            from news_sentiment import analyze_news_sentiment
            news_list = (card["rev"] or {}).get("news") or []
            card["news_sentiment"] = analyze_news_sentiment(news_list) if news_list else None
        except Exception:
            card["news_sentiment"] = None
        picks.append(card)

    # 排序：flash_score（月斜+RS）越高越前
    picks.sort(key=lambda x: -x.get("flash_score", 0))
    top = picks[:max_picks]
    print(f"  掃描 {scanned} 支，🌟 V42 命中 {len(picks)} 支，取前 {len(top)} 支")
    for c in top:
        m = c["flash_metrics"]
        print(f"  🌟 {c['sid']} {c['name']}：月斜 {m['ma20_slope_pct']:+.2f}%，RS {m['rs20_pct']:+.1f}%，"
              f"量比 {m['vol_burst']}x，RSI {m['rsi14']:.0f}，C/MA20 {m['c_over_ma20']}x")
    return top


def build_merger_picks(pc, rev_data, tdcc=None, name_map=None, max_picks=30):
    """
    🤝 併購/收購/合併案相關股票區塊
    掃描所有股票的新聞，含以下關鍵字即列入：
      合併、併購、收購、入股、取得XX股權、股權交易、整併、借殼、
      換股、公開收購、M&A、merger、acquisition、整合、子公司收購
    回傳 list of dict（shape 跟 holdings 一樣，可直接用 holdings 卡片渲染）
    """
    print("🤝 掃描併購/收購相關新聞...")
    name_map = dict(name_map or {})
    # 名稱補齊
    try:
        from sector_analyzer import fetch_all_industries as _fai_m
        ind_m = _fai_m()
        for _sid, _info in ind_m.items():
            existing = name_map.get(_sid, "")
            if (not existing or not str(existing).strip()) and _info.get("name"):
                name_map[_sid] = _info["name"]
    except Exception:
        ind_m = {}

    # 併購類關鍵字（依命中強度）
    MA_KEYWORDS = [
        "公開收購", "併購", "合併案", "合併方案", "現金合併", "換股合併", "股份轉換",
        "整併", "借殼", "敵意收購", "收購股權", "收購計畫", "收購案",
        "取得股權", "取得控制權", "增加持股", "策略入股", "策略合作",
        "整合", "子公司收購", "子公司併購", "母公司收購", "私有化",
        "合資", "成立合資公司", "M&A", "merger", "acquisition", "buyout",
    ]

    picks = []
    seen_sids = set()
    for sid, rev in (rev_data or {}).items():
        if not isinstance(rev, dict): continue
        news_list = rev.get("news") or []
        if not news_list: continue
        # 找含關鍵字的新聞
        matched_news = []
        for n in news_list:
            title = (n.get("t") or "") if isinstance(n, dict) else str(n)
            for kw in MA_KEYWORDS:
                if kw in title:
                    matched_news.append({"title": title, "url": n.get("u","") if isinstance(n,dict) else "",
                                          "keyword": kw, "date": n.get("d","") if isinstance(n,dict) else ""})
                    break
        if not matched_news: continue

        # 篩出有 K 線資料的
        df = pc.get(sid)
        if df is None or df.empty: continue
        df = df.copy().tail(80).reset_index(drop=True)
        for c in ["open","high","low","close","volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
        if len(df) < 20: continue

        df60 = df.tail(60).reset_index(drop=True)
        last_close = float(df60["close"].iloc[-1])
        ma5_arr  = df60["close"].rolling(5).mean().round(2).tolist()
        ma20_arr = df60["close"].rolling(20).mean().round(2).tolist()
        vma20_arr = [v if pd.notna(v) else None
                     for v in df60["volume"].rolling(20).mean().round(0).tolist()]

        # 子族群
        sub_alias = ""; sub_icon = ""
        try:
            from sector_analyzer import classify_stock_all, SUBSECTORS
            subs = classify_stock_all(sid, ind_m.get(sid, {}).get("industry", ""))
            if subs and subs[0] != "_未分類":
                info_ = SUBSECTORS.get(subs[0], {})
                sub_alias = info_.get("alias", subs[0])
                sub_icon = info_.get("icon", "📊")
        except Exception:
            pass

        card = {
            "sid": sid, "name": name_map.get(sid, sid),
            "is_etf": False,
            "shares": 0, "cost": None,
            "current": round(last_close, 2),
            "market_value": 0, "cost_value": 0, "pl_amt": 0, "pl_pct": 0,
            "dividend_cash_per_share": 0, "dividend_stock_per_share": 0,
            "dividend_cash_amount": 0, "dividend_stock_amount": 0,
            "dividend_new_shares": 0, "dividend_amount": 0,
            "dividend_events": [], "dividend_next_ex_date": None,
            "total_return_amt": 0, "total_return_pct": 0,
            "src": "併購新聞",
            "dates":  df60["date"].tolist(),
            "opens":  df60["open"].tolist(),
            "highs":  df60["high"].tolist(),
            "lows":   df60["low"].tolist(),
            "closes": df60["close"].tolist(),
            "vols":   df60["volume"].tolist(),
            "ma5": ma5_arr, "ma20": ma20_arr, "vma20": vma20_arr,
            "pattern": None,
            "support": [], "resistance": [],
            "holding": None, "targets": None,
            "rev": rev,
            "strategies": [], "strat_action": "併購題材", "strat_color": "#ffaa00", "strat_score": 0,
            "merger_news": matched_news[:5],   # 最多顯示 5 則併購新聞
            "merger_count": len(matched_news),
            "subsector_alias": sub_alias,
            "subsector_icon":  sub_icon,
        }
        # 評語
        kws = list({m["keyword"] for m in matched_news})[:3]
        card["commentary"] = f"近期新聞含 {'/'.join(kws)} 關鍵字（{len(matched_news)} 則），值得追蹤"
        card["action"] = "🤝 併購題材"
        card["action_color"] = "#ffaa00"

        picks.append(card)
        seen_sids.add(sid)
        if len(picks) >= max_picks: break

    # 按命中新聞數降序
    picks.sort(key=lambda x: -x.get("merger_count", 0))
    print(f"  🤝 併購相關 {len(picks)} 支股票")
    for c in picks[:10]:
        kws_ = list({m["keyword"] for m in c["merger_news"]})[:3]
        print(f"  🤝 {c['sid']} {c['name']}（{c['merger_count']} 則）關鍵字：{'/'.join(kws_)}")
    return picks


def build_holdings_data(pc, tdcc=None):
    """建構「我的持股」分析資料"""
    print("📊 建構我的持股分析資料...")
    holdings = []
    today_str = datetime.today().strftime("%Y-%m-%d")
    # ★ 讀「當月券商目標價」快取（analyst_targets_scraper.py 產出）
    _monthly_targets = {}
    try:
        _mt_path = os.path.join(BASE_DIR, "cache", "analyst_targets_monthly.json")
        if os.path.exists(_mt_path):
            _mt_all = json.load(open(_mt_path, encoding="utf-8"))
            _key = f"{datetime.today().year}-{datetime.today().month:02d}"
            _monthly_targets = (_mt_all.get(_key) or {})
            print(f"  📊 {_key} 券商目標價快取：{len(_monthly_targets)} 支")
    except Exception as _e:
        print(f"  ⚠️ 目標價快取讀取失敗：{_e}")
    # 從 xlsx 載入手填配股配息
    manual_divs = {}
    try:
        from holdings_loader import find_latest_holdings_xlsx, load_manual_dividends_from_xlsx
        _xpath, _ = find_latest_holdings_xlsx()
        if _xpath:
            manual_divs = load_manual_dividends_from_xlsx(_xpath) or {}
        if manual_divs:
            print(f"  📋 xlsx 手填配股配息：{len(manual_divs)} 支 → {', '.join(manual_divs.keys())}")
    except Exception as _e:
        print(f"  ⚠️ 手填配股配息載入失敗：{_e}")
    for sid, name, shares, cost, is_etf in MY_HOLDINGS:
        # 1. 取 K 線資料：先快取，但快取若不是今天則改抓 Yahoo（持股需即時現價）
        df = pc.get(sid)
        src = "快取"
        cache_last = None
        if df is not None and not df.empty:
            try:
                cache_last = str(df["date"].iloc[-1])
            except Exception:
                cache_last = None
        if df is None or df.empty or (cache_last and cache_last < today_str):
            reason = "快取無資料" if (df is None or df.empty) else f"快取最新 {cache_last} 已過時"
            print(f"  {sid} {name}：{reason}，改抓 Yahoo Finance...")
            ydf = fetch_yahoo_history(sid, days=120)
            if ydf is not None and not ydf.empty:
                df = ydf
                src = "Yahoo"
            elif df is None or df.empty:
                print(f"  ❌ {sid} {name}：Yahoo 也抓不到，補佔位卡片以避免清單被「吃掉」")
                # 插入佔位 holding，避免使用者誤以為持股消失
                holdings.append({
                    "sid": sid, "name": name, "is_etf": is_etf,
                    "shares": shares, "cost": cost or 0,
                    "current": 0, "market_value": 0,
                    "cost_value":   round((cost or 0) * shares * 1000, 0),
                    "pl_amt": 0, "pl_pct": 0,
                    "dividend_cash_per_share": 0, "dividend_stock_per_share": 0,
                    "dividend_cash_amount": 0, "dividend_stock_amount": 0,
                    "dividend_new_shares": 0, "dividend_amount": 0,
                    "dividend_events": [], "dividend_next_ex_date": None,
                    "dividend_source": "none",
                    "total_return_amt": 0, "total_return_pct": 0,
                    "src": "❌資料抓取失敗",
                    "dates": [], "opens": [], "highs": [], "lows": [],
                    "closes": [], "vols": [], "ma5": [], "ma20": [], "vma20": [],
                    "pattern": None, "support": [], "resistance": [],
                    "holding": None, "targets": None,
                    "strategies": [], "strat_action": "資料缺失",
                    "strat_color": "#d33", "strat_score": 0,
                    "action": "資料缺失", "commentary": "Yahoo Finance 無法取得 K 線，請手動確認",
                    "fetch_failed": True,
                })
                continue
            else:
                # 快取過時但 Yahoo 失敗 → 仍用快取，提示一下
                print(f"  ⚠️ {sid} {name}：Yahoo 取不到，仍用過時快取 {cache_last}")
        # 統一處理
        df = df.copy().tail(60).reset_index(drop=True)
        for c in ["open","high","low","close","volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open","high","low","close","volume"]).reset_index(drop=True)
        if len(df) < 5:
            print(f"  ⚠️ {sid} {name}：有效 K 線過少（{len(df)}），跳過")
            continue

        current = float(df["close"].iloc[-1])
        # 2. 損益估算
        actual_cost = cost if cost else current
        # 張數 → 股數（ETF/個股皆 1 張 = 1000 股）
        total_shares = shares * 1000
        market_value = current * total_shares
        cost_value   = actual_cost * total_shares
        pl_amt = market_value - cost_value
        pl_pct = (current - actual_cost) / actual_cost * 100 if actual_cost > 0 else 0

        dates  = df["date"].tolist()
        opens  = df["open"].tolist()
        highs  = df["high"].tolist()
        lows   = df["low"].tolist()
        closes = df["close"].tolist()
        vols   = df["volume"].tolist()
        ma5    = df["close"].rolling(5).mean().round(2).tolist()
        ma20   = df["close"].rolling(20).mean().round(2).tolist()
        vma20  = [v if pd.notna(v) else None
                  for v in df["volume"].rolling(20).mean().round(0).tolist()]

        # 3. 型態識別（ETF 跳過）
        pattern_info = None
        sr = {"support": [], "resistance": []}
        if not is_etf and len(df) >= 20:
            try:
                pat = detect_pattern(df, n_bars=30)
                pat_en = pat["pattern_en"] if pat else ""
                drawing = get_pattern_drawing(df, pat_en, n_bars=30) if pat else {"lines":[], "marks":[]}
                offset = max(0, len(df) - 30)
                for line in drawing.get("lines",[]):
                    line["x1"] += offset; line["x2"] += offset
                for mark in drawing.get("marks",[]):
                    mark["x"] += offset
                pattern_info = {
                    "name": pat["pattern_name"] if pat else "觀察中",
                    "desc": pat["description"] if pat else "",
                    "cat":  pat["category"] if pat else "",
                    "vp":   pat["vol_price"] if pat else "",
                    "lines": drawing["lines"],
                    "marks": drawing["marks"],
                }
                sr = get_support_resistance(df, n_bars=60)
            except Exception as _e:
                print(f"  ⚠️ {sid} {name} 型態識別失敗：{_e}")

        # 4. 籌碼（個股才有，ETF 沒有集保資料）
        holding_summary = None
        if not is_etf and tdcc:
            holding_summary = get_holding_summary(tdcc, sid)
            # 補 history / chart_points / cost_zone（讓籌碼分析區塊能完整顯示）
            if holding_summary:
                enrich_holding_for_chart(holding_summary, sid, df, pc)

        # 5. 法人預估 + 專家評語
        targets = fetch_analyst_targets(sid, is_etf=is_etf)
        # 6. 已宣布尚未派發的配股配息（xlsx 手填優先，FinMind 自動 fallback）
        div_info = fetch_upcoming_dividends(sid, manual_override=manual_divs.get(sid))
        cash_ps  = div_info["cash_per_share"]
        stock_ps = div_info["stock_per_share"]
        # 現金金額 = 每股現金股利 × 總股數
        cash_amount  = round(cash_ps * total_shares, 0)
        # 股票股利：每股 X 元 (面額 10) → 每股配 X/10 股 → 配股股數 × 現價
        new_shares   = round((stock_ps / 10.0) * total_shares, 2)
        stock_amount = round(new_shares * current, 0)
        div_amount   = cash_amount + stock_amount
        total_return_amt = round(pl_amt + div_amount, 0)
        total_return_pct = round((total_return_amt / cost_value * 100) if cost_value > 0 else 0, 2)
        h_dict = {
            "sid": sid, "name": name,
            "is_etf": is_etf,
            "shares": shares,
            "cost": cost,
            "current": round(current, 2),
            "market_value": round(market_value, 0),
            "cost_value":   round(cost_value, 0),
            "pl_amt": round(pl_amt, 0),
            "pl_pct": round(pl_pct, 2),
            "dividend_cash_per_share":  cash_ps,
            "dividend_stock_per_share": stock_ps,
            "dividend_cash_amount":     cash_amount,
            "dividend_stock_amount":    stock_amount,
            "dividend_new_shares":      new_shares,
            "dividend_amount":          div_amount,
            "dividend_events":          div_info["events"],
            "dividend_next_ex_date":    div_info["next_ex_date"],
            "dividend_source":          div_info.get("source", "finmind"),
            "total_return_amt":         total_return_amt,
            "total_return_pct":         total_return_pct,
            "src":  src,
            "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": closes, "vols": vols,
            "ma5": ma5, "ma20": ma20, "vma20": vma20,
            "pattern": pattern_info,
            "support": sr["support"],
            "resistance": sr["resistance"],
            "holding": holding_summary,
            "targets": targets,
            "monthly_targets": (_monthly_targets.get(sid) or {}).get("targets", []),
            "monthly_targets_month": (_monthly_targets.get(sid) or {}).get("month", ""),
        }
        # 飆股在線等 61 集策略匹配（先計算，給 commentary 用）
        if not is_etf:
            strategies = match_strategies(df, pattern_info)
            strat_summary = summarize_action(strategies)
            h_dict["strategies"]   = strategies
            h_dict["strat_action"] = strat_summary["action"]
            h_dict["strat_color"]  = strat_summary["color"]
            h_dict["strat_score"]  = strat_summary["score"]
        else:
            h_dict["strategies"] = []
            h_dict["strat_action"] = ""
            h_dict["strat_color"]  = "#888"
            h_dict["strat_score"]  = 0
        # 評語必須在 strategies 之後計算（含整合策略訊號）
        commentary_pack = make_holding_commentary(h_dict, targets)
        h_dict.update(commentary_pack)
        holdings.append(h_dict)
        tg_txt = ""
        if targets and targets.get("median"):
            tg_txt = f"｜目標 高{targets.get('high')}/中{targets.get('median')}/低{targets.get('low')}（{targets.get('analysts')} 位）"
        print(f"  ✅ {sid} {name}（{src}）：現價 {current} / 損益 {pl_amt:+.0f}（{pl_pct:+.2f}%）{tg_txt}")
        print(f"     建議：{commentary_pack['action']} ｜ {commentary_pack['commentary']}")
    # 計算總計
    total_mv = sum(h["market_value"] for h in holdings)
    total_cv = sum(h["cost_value"]   for h in holdings)
    total_pl = total_mv - total_cv
    total_pct = (total_pl / total_cv * 100) if total_cv > 0 else 0
    print(f"📊 我的持股總計：市值 {total_mv:,.0f} / 損益 {total_pl:+,.0f}（{total_pct:+.2f}%）")
    return holdings, {
        "total_mv":  round(total_mv, 0),
        "total_cv":  round(total_cv, 0),
        "total_pl":  round(total_pl, 0),
        "total_pct": round(total_pct, 2),
    }

def generate_html(results_df, pc, tdcc=None, stock_meta=None, rev_data=None,
                  holdings=None, holdings_summary=None, breakouts=None, pullbacks=None,
                  flash_picks=None, sector_ranking=None, merger_picks=None):
    today_str = datetime.today().strftime("%Y年%m月%d日")

    # 準備所有股票資料
    stocks_data = []
    for _, row in results_df.iterrows():
        sid  = str(row["股票代碼"]).zfill(4)
        name = row.get("股票名稱", sid)
        sig  = row.get("訊號", "")
        score = row.get("評分", 0)
        risk = row.get("風險警示", "")

        df = prepare_chart_data(sid, pc, days=60)
        if df is None: continue

        dates  = df["date"].tolist()
        opens  = df["open"].tolist()
        highs  = df["high"].tolist()
        lows   = df["low"].tolist()
        closes = df["close"].tolist()
        vols   = df["volume"].tolist()

        # MA5, MA10, MA20
        ma5  = df["close"].rolling(5).mean().round(2).tolist()
        ma10 = df["close"].rolling(10).mean().round(2).tolist()
        ma20 = df["close"].rolling(20).mean().round(2).tolist()

        # KD (9,3,3)
        low9  = df["low"].rolling(9).min()
        high9 = df["high"].rolling(9).max()
        rsv   = ((df["close"] - low9) / (high9 - low9) * 100).fillna(50)
        k_vals = rsv.ewm(com=2, adjust=False).mean().round(2)
        d_vals = k_vals.ewm(com=2, adjust=False).mean().round(2)

        # 型態識別：統一用最後60根，pattern只看最後30根
        df60 = df.tail(60).reset_index(drop=True)
        pat = detect_pattern(df60, n_bars=30)
        pat_en = pat["pattern_en"] if pat else ""
        drawing = get_pattern_drawing(df60, pat_en, n_bars=30) if pat else {"lines":[], "marks":[]}
        # offset：30根的index對應到60根canvas
        offset = max(0, len(df60) - 30)
        for line in drawing.get("lines",[]):
            line["x1"] += offset
            line["x2"] += offset
        for mark in drawing.get("marks",[]):
            mark["x"] += offset
        pattern_info = {
            "name": pat["pattern_name"] if pat else "觀察中",
            "desc": pat["description"] if pat else "",
            "conf": pat["confidence"] if pat else 0,
            "cat":  pat["category"] if pat else "",
            "vp":   pat["vol_price"] if pat else "",
            "all":  pat["all_patterns"] if pat else [],
            "lines": drawing["lines"],
            "marks": drawing["marks"],
        } if pat else {"name":"觀察中","desc":"","conf":0,"cat":"","vp":"","all":[],"lines":[],"marks":[]}

        # 支撐壓力線
        sr = get_support_resistance(df, n_bars=60)

        # 股票基本資料
        meta = (stock_meta or {}).get(sid, {})
        rev  = (rev_data or {}).get(sid, {})

        # 股票基本資料
        meta = (stock_meta or {}).get(sid, {})
        rev  = (rev_data or {}).get(sid, {})
        # 持股分佈
        holding = get_holding_summary(tdcc, sid) if tdcc else None
        holding_history = get_holding_history(sid)
        if holding and holding_history:
            holding["history"] = holding_history
            # 把集保日期對應到 K 線的 index（用於 canvas 對齊）
            holding_chart_points = []
            for hw in holding_history:
                hdate = hw["date"][:4]+"-"+hw["date"][4:6]+"-"+hw["date"][6:8]
                # 找最接近的 K 線 index
                closest_idx = None
                min_diff = 999
                for ki, krow in enumerate(df.itertuples()):
                    try:
                        diff = abs((pd.Timestamp(krow.date) - pd.Timestamp(hdate)).days)
                        if diff < min_diff:
                            min_diff = diff
                            closest_idx = ki
                    except: pass
                if closest_idx is not None and min_diff <= 7:
                    holding_chart_points.append({
                        "idx":    closest_idx,
                        "major":  hw["major"],
                        "date":   hw["date"],
                    })
            holding["chart_points"] = holding_chart_points

            # 步驟2：計算大戶成本區
            # 即使只有1週資料，也用該週對應的K線計算成本區
            cost_zone = None
            if len(holding_history) >= 1:
                # 找最近一段連續上升期（1週也算）
                consec_weeks = 1
                for i in range(len(holding_history)-1, 0, -1):
                    if holding_history[i]["major"] >= holding_history[i-1]["major"]:
                        consec_weeks += 1
                    else:
                        break
                # 取對應期間的K線
                start_date = holding_history[-min(consec_weeks, len(holding_history))]["date"]
                end_date   = holding_history[-1]["date"]
                df_sid = pc.get(sid)
                if df_sid is not None and not df_sid.empty:
                    df_sid = df_sid.copy()
                    df_sid["close"] = pd.to_numeric(df_sid["close"], errors="coerce")
                    df_sid["high"]  = pd.to_numeric(df_sid["high"],  errors="coerce")
                    df_sid["low"]   = pd.to_numeric(df_sid["low"],   errors="coerce")
                    sd = start_date[:4]+"-"+start_date[4:6]+"-"+start_date[6:8]
                    ed = end_date[:4]+"-"+end_date[4:6]+"-"+end_date[6:8]
                    # 取該週前後各5個交易日（週資料對應約5根K棒）
                    mask = (df_sid["date"] >= sd) & (df_sid["date"] <= ed)
                    period_df = df_sid[mask]
                    # 若該週沒有K線，取最近5根
                    if period_df.empty:
                        period_df = df_sid.tail(5)
                    if not period_df.empty:
                        avg_price  = round(period_df["close"].mean(), 2)
                        low_price  = round(period_df["low"].min(), 2)
                        high_price = round(period_df["high"].max(), 2)
                        curr_price = float(df_sid["close"].iloc[-1])
                        dist_pct   = round((curr_price - avg_price) / avg_price * 100, 1) if avg_price > 0 else 0
                        cost_zone  = {
                            "avg":   avg_price,
                            "low":   low_price,
                            "high":  high_price,
                            "weeks": consec_weeks,
                            "dist":  dist_pct,
                        }
            holding["cost_zone"] = cost_zone

        # 飆股在線等 61 集策略匹配
        strategies = match_strategies(df, pattern_info)
        strat_summary = summarize_action(strategies)

        # ★ 子族群分類（補上類股資訊）
        sub_alias = ""; sub_icon = ""; sub_rank = None
        try:
            from sector_analyzer import classify_stock_all, SUBSECTORS
            inds = None
            try:
                from sector_analyzer import fetch_all_industries as _fai_c
                inds = _fai_c()
            except Exception:
                inds = {}
            subs = classify_stock_all(sid, inds.get(sid, {}).get("industry", ""))
            if subs and subs[0] != "_未分類":
                # 取最佳排名族群（從 sector_ranking）
                best_sub = subs[0]
                if sector_ranking:
                    rank_map = {s["subsector"]: s for s in sector_ranking}
                    best_rank = 9999
                    for s_ in subs:
                        r = rank_map.get(s_, {}).get("rank", 9999)
                        if r < best_rank:
                            best_rank = r; best_sub = s_
                    sub_rank = best_rank if best_rank < 9999 else None
                info_ = SUBSECTORS.get(best_sub, {})
                sub_alias = info_.get("alias", best_sub)
                sub_icon = info_.get("icon", "📊")
        except Exception:
            pass

        stocks_data.append({
            "sid": sid, "name": name, "sig": sig,
            "score": score, "risk": risk,
            "dates": dates, "opens": opens, "highs": highs,
            "lows": lows, "closes": closes, "vols": vols,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "vma20": [v if pd.notna(v) else None for v in df["volume"].rolling(20).mean().round(0).tolist()],
            "k": [v if pd.notna(v) else None for v in k_vals.tolist()],
            "d": [v if pd.notna(v) else None for v in d_vals.tolist()],
            "pattern": pattern_info,
            "support": sr["support"],
            "resistance": sr["resistance"],
            "holding": holding,
            "meta": meta,
            "rev":  rev,
            "strategies": strategies,
            "strat_action": strat_summary["action"],
            "strat_color":  strat_summary["color"],
            "strat_score":  strat_summary["score"],
            "subsector_alias": sub_alias,
            "subsector_icon":  sub_icon,
            "subsector_rank":  sub_rank,
        })

    sig_order = {"🚀 初始起漲": 0, "👀 起漲觀察": 1, "📈 續漲": 2, "🔥 過熱警示": 3}
    stocks_data.sort(key=lambda x: (sig_order.get(x["sig"], 9), -x["score"]))

    # 計算各類數量
    ti = len([s for s in stocks_data if s["sig"]=="🚀 初始起漲"])
    tw = len([s for s in stocks_data if s["sig"]=="👀 起漲觀察"])
    tc = len([s for s in stocks_data if s["sig"]=="📈 續漲"])
    to = len([s for s in stocks_data if s["sig"]=="🔥 過熱警示"])

    stocks_json = json.dumps(stocks_data, ensure_ascii=False)
    holdings_json   = json.dumps(holdings  or [], ensure_ascii=False)
    breakouts_json  = json.dumps(breakouts or [], ensure_ascii=False)
    pullbacks_json  = json.dumps(pullbacks or [], ensure_ascii=False)
    flash_json      = json.dumps(flash_picks or [], ensure_ascii=False)
    merger_json     = json.dumps(merger_picks or [], ensure_ascii=False)
    sector_json     = json.dumps(sector_ranking or [], ensure_ascii=False)

    # 今日大盤資金總判決（基於 flow_score Top 5 + V42 命中分佈）
    flash_tier_counts = {"AAA":0,"AA":0,"A":0,"B":0}
    for h in (flash_picks or []):
        t = h.get("quality_tier","B")
        flash_tier_counts[t] = flash_tier_counts.get(t, 0) + 1
    aaa_aa = flash_tier_counts["AAA"] + flash_tier_counts["AA"]
    total_v42 = sum(flash_tier_counts.values())

    if aaa_aa >= 3:
        market_verdict = "🟢 進場機會多"
        verdict_color = "#3fb950"
        verdict_note = f"AAA+AA 級共 {aaa_aa} 支，族群有資金 + V42 形態好，可分散買進"
    elif aaa_aa >= 1:
        market_verdict = "🟡 部分機會"
        verdict_color = "#f0c040"
        verdict_note = f"僅 {aaa_aa} 支高品質候選，謹慎挑選"
    elif total_v42 >= 5:
        market_verdict = "🔴 警告：族群已撤離"
        verdict_color = "#f85149"
        verdict_note = f"{total_v42} 支 V42 命中但全在弱勢族群（B 級）→ 個股形態好但族群被拋售，可能是假突破，建議空手或極輕倉"
    else:
        market_verdict = "⚪ 無明顯訊號"
        verdict_color = "#8b949e"
        verdict_note = "V42 命中數少且族群均冷，多看少動"

    # 子族群排行 HTML（給 Top 15 + 標明 V42 命中數量）
    sector_html_rows = ""
    if sector_ranking:
        # 算每個子族群 V42 命中數
        sect_hits = {}
        for h in (flash_picks or []):
            key = h.get("subsector", "?")
            sect_hits[key] = sect_hits.get(key, 0) + 1
        for s in sector_ranking[:15]:
            tag = "🚀" if s["is_top5"] else ("✅" if s["is_top10"] else "  ")
            color = "#3fb950" if s["median_ret_20d"] > 0 else "#f85149"
            color_5d = "#3fb950" if s["median_ret_5d"] > 0 else "#f85149"
            hit_count = sect_hits.get(s["subsector"], 0)
            hit_badge = f'<span style="color:#ffaadd;font-weight:bold;margin-left:6px">🌟 {hit_count}</span>' if hit_count else ""
            row_id = f"sect_{s['subsector'].replace('_','')}_{s['rank']}"
            # 成員 mini table (前 15 強)
            members = s.get("member_details", [])[:15]
            members_html = ""
            if members:
                rows = ""
                for m in members:
                    c1d = "#3fb950" if m["chg_1d"] >= 0 else "#f85149"
                    c5d = "#3fb950" if m["chg_5d"] >= 0 else "#f85149"
                    c20 = "#3fb950" if m["chg_20d"] >= 0 else "#f85149"
                    rows += (
                        f'<tr><td style="padding:2px 8px;color:#8b949e">{m["sid"]}</td>'
                        f'<td style="padding:2px 8px;color:#e6edf3">{m["name"]}</td>'
                        f'<td style="padding:2px 8px;color:#e6edf3;text-align:right">{m["close"]}</td>'
                        f'<td style="padding:2px 8px;color:{c1d};text-align:right">{m["chg_1d"]:+.2f}%</td>'
                        f'<td style="padding:2px 8px;color:{c5d};text-align:right">{m["chg_5d"]:+.2f}%</td>'
                        f'<td style="padding:2px 8px;color:{c20};text-align:right">{m["chg_20d"]:+.2f}%</td></tr>'
                    )
                members_html = f"""<tr id="{row_id}_detail" style="display:none;background:rgba(0,0,0,0.3)">
                  <td colspan="6" style="padding:8px 12px">
                    <table style="width:100%;font-size:11px;border-collapse:collapse">
                      <thead><tr style="color:#666;border-bottom:1px solid #21262d">
                        <th style="text-align:left;padding:3px 8px">代號</th>
                        <th style="text-align:left;padding:3px 8px">名稱</th>
                        <th style="text-align:right;padding:3px 8px">收盤</th>
                        <th style="text-align:right;padding:3px 8px">1日</th>
                        <th style="text-align:right;padding:3px 8px">5日</th>
                        <th style="text-align:right;padding:3px 8px">20日</th>
                      </tr></thead>
                      <tbody>{rows}</tbody>
                    </table>
                  </td>
                </tr>"""
            # ★ SVG Sparkline：1-20 日累積資金軌跡
            cum = s.get("daily_cumulative", [])
            spark_svg = ""
            if cum and len(cum) >= 2:
                W, H = 160, 36
                vmin = min(min(cum), 0)
                vmax = max(max(cum), 0)
                vrange = max(vmax - vmin, 0.1)
                # 計算路徑點
                points = []
                for i, v in enumerate(cum):
                    x = i * (W / (len(cum) - 1))
                    y = H - ((v - vmin) / vrange * H)
                    points.append(f"{x:.1f},{y:.1f}")
                path_d = "M " + " L ".join(points)
                # 0 軸位置
                zero_y = H - ((0 - vmin) / vrange * H)
                # 線色：最後一日 > 0 = 綠，< 0 = 紅
                line_color = "#3fb950" if cum[-1] >= 0 else "#f85149"
                # 填色區域
                fill_d = f"M 0,{zero_y} L " + " L ".join(points) + f" L {W:.1f},{zero_y} Z"
                fill_color = "rgba(63,185,80,0.15)" if cum[-1] >= 0 else "rgba(248,81,73,0.15)"
                # tooltip 全部 20 日數值（顯示日期 D-19, D-18, ..., D）
                tip_text = "｜".join(f"D-{19-i}:{v:+.1f}%" for i, v in enumerate(cum))
                spark_svg = f"""<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" style="display:block" title="{tip_text}">
                    <line x1="0" y1="{zero_y:.1f}" x2="{W}" y2="{zero_y:.1f}" stroke="#444" stroke-width="0.5" stroke-dasharray="2,2"/>
                    <path d="{fill_d}" fill="{fill_color}" stroke="none"/>
                    <path d="{path_d}" fill="none" stroke="{line_color}" stroke-width="1.5"/>
                    <circle cx="{points[-1].split(',')[0]}" cy="{points[-1].split(',')[1]}" r="2.5" fill="{line_color}"/>
                </svg>"""
            stage_html = f'<span style="color:{s.get("stage_color","#888")};font-weight:bold;font-size:12px">{s.get("stage_icon","")} {s.get("stage","—")}</span>'
            advice_text = s.get("advice", "—")
            peak_info = f"高點 D-{20-s.get('peak_day',1)}日" if s.get("peak_day",0) > 0 else ""

            sector_html_rows += f"""<tr style="cursor:pointer" onclick="document.getElementById('{row_id}_detail').style.display=document.getElementById('{row_id}_detail').style.display==='none'?'table-row':'none'">
              <td style="padding:4px 8px;color:#8b949e;vertical-align:middle">{tag} #{s['rank']}</td>
              <td style="padding:4px 8px;color:#e6edf3;vertical-align:middle"><span style="font-size:16px">{s['icon']}</span> {s['alias']}{hit_badge}</td>
              <td style="padding:4px 8px;color:#8b949e;vertical-align:middle">{s['members']} 支 ▼</td>
              <td style="padding:4px 8px;vertical-align:middle" title="{advice_text}">{spark_svg}</td>
              <td style="padding:4px 8px;color:{color};font-weight:bold;vertical-align:middle">{s['median_ret_20d']:+.2f}%</td>
              <td style="padding:4px 8px;color:{color_5d};vertical-align:middle">{s['median_ret_5d']:+.2f}%</td>
              <td style="padding:4px 8px;vertical-align:middle;line-height:1.3">{stage_html}<br><span style="color:#666;font-size:10px">{peak_info}</span></td>
            </tr>{members_html}
            <tr style="background:rgba(255,255,255,0.02)"><td colspan="7" style="padding:2px 12px 8px 12px;font-size:11px;color:#9aa9b8;border-bottom:1px solid #21262d">
              💡 <b>{advice_text}</b>
            </td></tr>"""
    h_summary = holdings_summary or {"total_mv":0,"total_cv":0,"total_pl":0,"total_pct":0}

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>台股飆股選股日報 {today_str}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, sans-serif; background: #0d1117; color: #e6edf3; }}
.header {{ background: linear-gradient(135deg, #1f497d, #2e75b6); padding: 20px 30px; }}
.header h1 {{ font-size: 24px; font-weight: bold; }}
.header .summary {{ margin-top: 8px; font-size: 14px; color: #cce; }}
.summary-badges {{ display: flex; gap: 12px; margin-top: 12px; flex-wrap: wrap; }}
.badge {{ padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; }}
.badge.initial {{ background: #1f497d; }}
.badge.watch {{ background: #2e75b6; }}
.badge.cont {{ background: #217346; }}
.badge.hot {{ background: #c00000; }}
.controls {{ padding: 15px 30px; background: #161b22; border-bottom: 1px solid #30363d; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
.filter-btn {{ padding: 6px 16px; border-radius: 20px; border: 1px solid #30363d; background: #21262d; color: #e6edf3; cursor: pointer; font-size: 13px; transition: all 0.2s; }}
.filter-btn:hover, .filter-btn.active {{ background: #1f497d; border-color: #2e75b6; }}
.search {{ padding: 6px 14px; border-radius: 20px; border: 1px solid #30363d; background: #21262d; color: #e6edf3; font-size: 13px; width: 200px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(600px, 1fr)); gap: 20px; padding: 20px 30px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; }}
.card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.4); }}
.card-header {{ padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; }}
.stock-info {{ display: flex; align-items: center; gap: 10px; }}
.stock-code {{ font-size: 20px; font-weight: bold; color: #fff; }}
.stock-name {{ font-size: 14px; color: #8b949e; }}
.sig-badge {{ padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
.score-info {{ text-align: right; }}
.score-val {{ font-size: 22px; font-weight: bold; color: #f0a500; }}
.score-label {{ font-size: 11px; color: #8b949e; }}
.risk-tag {{ padding: 2px 8px; border-radius: 8px; font-size: 11px; font-weight: bold; margin-top: 3px; display: inline-block; }}
.pattern-bar {{ padding: 8px 16px; background: rgba(255,255,255,0.04); border-bottom: 1px solid #21262d; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.pattern-name {{ font-size: 13px; font-weight: bold; color: #f0c040; }}
.pattern-desc {{ font-size: 11px; color: #8b949e; }}
.pattern-vp {{ padding: 2px 8px; border-radius: 8px; font-size: 11px; background: rgba(88,166,255,0.15); color: #58a6ff; }}
.pattern-conf {{ font-size: 11px; color: #8b949e; }}
.pattern-cat-bottom {{ color: #28a745; }}
.pattern-cat-top {{ color: #dc3545; }}
.pattern-cat-cont {{ color: #f0a500; }}
.chart-container {{ padding: 0 8px 8px; }}
canvas {{ width: 100% !important; }}
.tooltip {{ position: fixed; background: rgba(22,27,34,0.95); border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px; font-size: 12px; pointer-events: none; z-index: 1000; display: none; min-width: 160px; }}
.tooltip .t-date {{ color: #8b949e; margin-bottom: 6px; font-weight: bold; }}
.tooltip .t-up {{ color: #ff4444; }}
.tooltip .t-down {{ color: #00aa44; }}
</style>
</head>
<body>
<a href="/" style="position:fixed;top:calc(env(safe-area-inset-top) + 8px);left:12px;z-index:99999;background:rgba(13,20,36,0.92);color:#f0c040;padding:9px 16px;border-radius:22px;text-decoration:none;font-size:14px;font-weight:bold;border:1px solid rgba(240,196,64,0.4);box-shadow:0 4px 12px rgba(0,0,0,0.4);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);">← 回首頁</a>
<script>
(function(){{
  const HEADER_KWS = {{
    "即將突破":"breakouts-grid","拉回月線":"pullbacks-grid","子族群輪動":"sector-rotation",
    "V42 飆股":"flash-grid","我的持股":"holdings-grid","併購/收購":"merger-grid"
  }};
  const KEEP_CLASS = new Set(['header','controls','tooltip']);
  function applySection(hash){{
    for (const el of Array.from(document.body.children)) {{
      const tag = el.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'A') continue;
      if (el.className && Array.from(el.classList).some(c => KEEP_CLASS.has(c))) continue;
      let sec = null;
      if (el.id === 'grid') sec = 'top-flash';
      else if (el.id) sec = el.id;
      else {{
        const h2 = el.querySelector && el.querySelector('h2');
        if (h2) for (const [kw, key] of Object.entries(HEADER_KWS)) {{
          if (h2.textContent.includes(kw)) {{ sec = key; break; }}
        }}
      }}
      if (sec) el.dataset.section = sec;
    }}
    document.body.querySelectorAll('[data-section]').forEach(el => {{
      if (el.dataset.section !== hash) el.style.display = 'none';
    }});
    document.querySelectorAll('.header, .controls').forEach(el => el.style.display = 'none');
  }}
  function applySid(sid){{
    const tryOnce = () => {{
      const canvas = document.getElementById(`hc_${{sid}}`) || document.getElementById(`c_${{sid}}`);
      if (!canvas) return false;
      const card = canvas.closest('.card');
      if (!card) return false;
      const grid = card.parentElement;
      Array.from(grid.children).forEach(c => {{ if (c !== card && c.classList && c.classList.contains('card')) c.style.display = 'none'; }});
      const banner = document.createElement('div');
      banner.style.cssText = 'padding:12px 20px;font-size:13px;color:#f0c040;background:rgba(240,196,64,0.08);border-bottom:1px solid rgba(240,196,64,0.2)';
      banner.innerHTML = `🔍 深入單支：<b>${{sid}}</b>（其他已隱藏）`;
      card.parentElement.parentElement.insertBefore(banner, card.parentElement);
      return true;
    }};
    let n = 0;
    const timer = setInterval(() => {{ if (tryOnce() || ++n > 40) clearInterval(timer); }}, 200);
  }}
  function apply(){{
    const hash = (location.hash || '').replace('#','');
    const sid = new URLSearchParams(location.search).get('sid');
    if (hash) applySection(hash);
    if (sid) applySid(sid);
  }}
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();
  window.addEventListener('hashchange', () => location.reload());
}})();
</script>
<div class="header">
  <h1>📈 台股飆股選股日報　{today_str}</h1>
  <div class="summary-badges">
    <span class="badge initial">🚀 初始起漲 {ti} 支</span>
    <span class="badge watch">👀 起漲觀察 {tw} 支</span>
    <span class="badge cont">📈 續漲 {tc} 支</span>
    <span class="badge hot">🔥 過熱 {to} 支</span>
    <span class="badge" style="background:#333">共 {len(stocks_data)} 支</span>
  </div>
</div>
<div class="controls">
  <button class="filter-btn active" onclick="filter('all')">全部</button>
  <button class="filter-btn" onclick="filter('INITIAL_BREAKOUT')">🚀 初始起漲</button>
  <button class="filter-btn" onclick="filter('BREAKOUT_WATCH')">👀 起漲觀察</button>
  <button class="filter-btn" onclick="filter('CONTINUATION')">📈 續漲</button>
  <button class="filter-btn" onclick="filter('OVERHEATED')">🔥 過熱警示</button>
  <input class="search" type="text" placeholder="搜尋代碼或名稱..." oninput="search(this.value)">
</div>
<div class="tooltip" id="tooltip"></div>
<div class="grid" id="grid"></div>

<!-- ── 即將突破區塊 ──────────────────────────────── -->
<div style="background:linear-gradient(135deg,#0d3a2a,#1f5c4a); padding:20px 30px; margin-top:30px; border-top:3px solid #3fb950;">
  <h2 style="font-size:22px;font-weight:bold;color:#56d364;margin:0">⚡ 即將突破</h2>
  <div style="margin-top:6px;font-size:12px;color:#adbac7">
    底部反轉型態 + 距頸線 −7% ～ +10% + 今日量 &gt; MA20 量 × 2，最多前 10 支（依距頸線最近排序）
  </div>
  <div style="margin-top:10px;display:flex;gap:18px;flex-wrap:wrap;font-size:14px">
    <span class="badge" style="background:#217346">候選 {len(breakouts or [])} 支</span>
  </div>
</div>
<div class="grid" id="breakouts-grid"></div>

<!-- ── 🎯 拉回月線買點區塊 ─────────────────────────── -->
<div style="background:linear-gradient(135deg,#0d3a2a,#1a5c44); padding:20px 30px; margin-top:30px; border-top:3px solid #28a745;">
  <h2 style="font-size:22px;font-weight:bold;color:#56d364;margin:0">🎯 拉回月線買點</h2>
  <div style="margin-top:6px;font-size:12px;color:#adbac7">
    趨勢向上（季月線都上揚）+ 短線強勢（5MA &gt; 20MA）+ 拉回貼近月線（0%~+3%）+ 拉回量縮，最多前 10 支
  </div>
  <div style="margin-top:10px;display:flex;gap:18px;flex-wrap:wrap;font-size:14px">
    <span class="badge" style="background:#217346">候選 {len(pullbacks or [])} 支</span>
  </div>
</div>
<div class="grid" id="pullbacks-grid"></div>

<!-- ── 🏭 子族群輪動排行（最上面）─────────────────── -->
<div style="background:linear-gradient(135deg,#1d2e3a,#2f4a5c); padding:20px 30px; margin-top:30px; border-top:3px solid #66ccff;">
  <h2 style="font-size:22px;font-weight:bold;color:#aaddff;margin:0">🏭 子族群輪動排行（資金實際停泊單位）</h2>
  <div style="margin-top:6px;color:#c5e0e0;font-size:12px;">
    30+ 細分族群（如散熱液冷/ABF載板/矽智財ASIC/CCL/重電四雄等）｜依「20 日中位漲跌幅」排名｜🚀 = Top 5｜✅ = Top 10｜🌟 = 該族群內有 V42 命中
  </div>
  <table style="width:100%;max-width:1100px;margin-top:14px;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="color:#8b949e;border-bottom:1px solid #21262d">
        <th style="text-align:left;padding:6px 8px">排名</th>
        <th style="text-align:left;padding:6px 8px">族群</th>
        <th style="text-align:left;padding:6px 8px">成員</th>
        <th style="text-align:left;padding:6px 8px">📈 1-20 日資金軌跡</th>
        <th style="text-align:left;padding:6px 8px">20 日累積</th>
        <th style="text-align:left;padding:6px 8px">5 日</th>
        <th style="text-align:left;padding:6px 8px">🎯 階段</th>
      </tr>
    </thead>
    <tbody>{sector_html_rows}</tbody>
  </table>
</div>

<!-- ── 🌟 V42 飆股區 ─────────────────────────────── -->
<div style="background:linear-gradient(135deg,#3a1d2e,#5c2f4a); padding:20px 30px; margin-top:30px; border-top:3px solid #ff66cc;">
  <h2 style="font-size:22px;font-weight:bold;color:#ffaadd;margin:0">🌟 V42 飆股訊號（族群輪動雙過濾）</h2>
  <div style="margin-top:6px;color:#e0c5e0;font-size:12px;">
    🏆 AAA = Top 5 強勢族群+資金加速+V42｜🥇 AA = Top 5 強勢族群中｜🥈 A = Top 10 中強｜⚠️ B = 弱勢族群中（族群已撤離→個股形態好可能是假突破）
  </div>
  <div style="margin-top:14px;padding:12px 18px;background:rgba(0,0,0,0.4);border-left:4px solid {verdict_color};border-radius:6px;">
    <div style="font-size:18px;font-weight:bold;color:{verdict_color};">📋 今日大盤資金判決：{market_verdict}</div>
    <div style="margin-top:6px;font-size:13px;color:#cccccc;">{verdict_note}</div>
    <div style="margin-top:8px;font-size:12px;color:#999;">
      命中分佈：🏆AAA <b>{flash_tier_counts['AAA']}</b> ｜🥇AA <b>{flash_tier_counts['AA']}</b> ｜🥈A <b>{flash_tier_counts['A']}</b> ｜⚠️B <b>{flash_tier_counts['B']}</b>（共 {total_v42} 支）
    </div>
  </div>
</div>
<div class="grid" id="flash-grid"></div>

<!-- ── 我的持股分析區塊 ────────────────────────────── -->
<div style="background:linear-gradient(135deg,#3a2a0d,#5c4a1f); padding:20px 30px; margin-top:30px; border-top:3px solid #f0a500;">
  <h2 style="font-size:22px;font-weight:bold;color:#f0c040;margin:0">📊 我的持股</h2>
  <div style="margin-top:10px;display:flex;gap:18px;flex-wrap:wrap;font-size:14px">
    <span class="badge" style="background:#1f497d">市值 {h_summary['total_mv']:,.0f}</span>
    <span class="badge" style="background:#444">成本 {h_summary['total_cv']:,.0f}</span>
    <span class="badge" style="background:{'#217346' if h_summary['total_pl']>=0 else '#c00000'}">
      損益 {h_summary['total_pl']:+,.0f}（{h_summary['total_pct']:+.2f}%）
    </span>
    <span class="badge" style="background:#333">共 {len(holdings or [])} 支</span>
  </div>
</div>
<div class="grid" id="holdings-grid"></div>

<!-- ── 🤝 併購/收購相關股票區塊（在我的持股後面）─────── -->
<div style="background:linear-gradient(135deg,#3a2d0a,#5c4a18); padding:20px 30px; margin-top:30px; border-top:3px solid #ffaa00;">
  <h2 style="font-size:22px;font-weight:bold;color:#ffcc66;margin:0">🤝 併購/收購相關股票</h2>
  <div style="margin-top:6px;color:#e0c060;font-size:12px;">
    新聞掃描含關鍵字：合併案 / 併購 / 收購 / 公開收購 / 取得股權 / 借殼 / 換股合併 / 私有化 / M&amp;A
  </div>
  <div style="margin-top:10px;display:flex;gap:18px;flex-wrap:wrap;font-size:14px">
    <span class="badge" style="background:#ffaa00;color:#1a1a1a;font-weight:bold">命中 {len(merger_picks or [])} 支</span>
  </div>
</div>
<div class="grid" id="merger-grid"></div>

<script>
const stocks = {stocks_json};
const holdings  = {holdings_json};
const breakouts = {breakouts_json};
const pullbacks = {pullbacks_json};
const flashPicks = {flash_json};
const mergerPicks = {merger_json};
const sigMap = {{
  "🚀 初始起漲": {{key:"INITIAL_BREAKOUT", color:"#2e75b6", bg:"rgba(46,117,182,0.15)"}},
  "👀 起漲觀察": {{key:"BREAKOUT_WATCH",   color:"#4a9ede", bg:"rgba(74,158,222,0.15)"}},
  "📈 續漲":     {{key:"CONTINUATION",     color:"#28a745", bg:"rgba(40,167,69,0.15)"}},
  "🔥 過熱警示": {{key:"OVERHEATED",       color:"#dc3545", bg:"rgba(220,53,69,0.15)"}},
}};
const keyToLabel = {{
  "INITIAL_BREAKOUT": "🚀 初始起漲",
  "BREAKOUT_WATCH":   "👀 起漲觀察",
  "CONTINUATION":     "📈 續漲",
  "OVERHEATED":       "🔥 過熱警示",
}};
function getSigInfo(sig) {{
  if (sigMap[sig]) return sigMap[sig];
  const label = keyToLabel[sig] || sig;
  return sigMap[label] || {{color:"#888", bg:"rgba(128,128,128,0.1)"}};
}}
const riskColors = {{"🟢 正常":"#28a745","🟡 注意":"#e36c09","🔴 高風險":"#c00000"}};

let currentFilter = "all";

function getSigKey(sig) {{
  return sigMap[sig]?.key || "";
}}

function filter(type) {{
  currentFilter = type;
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  event.target.classList.add("active");
  renderCards(document.querySelector(".search").value);
}}

function search(val) {{
  renderCards(val);
}}

function renderCards(searchVal="") {{
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  const filtered = stocks.filter(s => {{
    const matchFilter = currentFilter==="all" || getSigKey(s.sig)===currentFilter;
    const matchSearch = !searchVal || s.sid.includes(searchVal) || s.name.includes(searchVal);
    return matchFilter && matchSearch;
  }});
  filtered.forEach(s => {{
    const card = createCard(s);
    grid.appendChild(card);
  }});
  // Draw charts after DOM is ready
  setTimeout(() => filtered.forEach(s => drawChart(s)), 50);
}}

// ── 共用：籌碼分析區塊（給飆股、持股、突破候選用）────
function renderChipBlock(holding, sid, dates) {{
  const h = holding;
  if (!h) return "";
  const total = h.major + h.mid + h.small;
  const majPct = total>0 ? (h.major/total*100).toFixed(0) : 0;
  const midPct = total>0 ? (h.mid/total*100).toFixed(0) : 0;
  const smlPct = total>0 ? (h.small/total*100).toFixed(0) : 0;
  const whaleTag = h.whale > 50 ? `<span style="color:#ff4444;font-weight:bold;margin-left:6px">⚠️超大戶集中 ${{h.whale}}%</span>` : "";
  const hist = h.history || [];

  let signal = "";
  let majorTrend = 0, personsTrend = 0;
  if (hist.length >= 2) {{
    for (let i=1; i<hist.length; i++) {{
      if (hist[i].major > hist[i-1].major) majorTrend++; else majorTrend = 0;
      if (hist[i].persons > 0 && hist[i-1].persons > 0 && hist[i].persons < hist[i-1].persons) personsTrend++;
      else personsTrend = 0;
    }}
    const lastMajorDiff = hist.length>=2 ? (hist[hist.length-1].major - hist[hist.length-2].major) : 0;
    const lastPersonsDiff = hist.length>=2 && hist[hist.length-1].persons>0 ?
      (hist[hist.length-1].persons - hist[hist.length-2].persons) : 0;
    if (majorTrend >= 2 && personsTrend >= 2) {{
      signal = `<span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:11px">🚀 籌碼集中！大戶↑${{majorTrend}}週 股東人數↓${{personsTrend}}週</span>`;
    }} else if (majorTrend >= 1 && lastPersonsDiff < 0) {{
      signal = `<span style="background:rgba(255,68,68,0.2);color:#ff8888;padding:2px 8px;border-radius:4px;font-size:11px">📈 大戶增加中 股東人數下降</span>`;
    }} else if (lastMajorDiff < -1) {{
      signal = `<span style="background:rgba(255,100,0,0.2);color:#ff8800;padding:2px 8px;border-radius:4px;font-size:11px">⚠️ 大戶持股下滑 注意出貨</span>`;
    }}
  }}

  const cz = h.cost_zone;
  const mopsUrl = `https://mops.twse.com.tw/mops/web/t05st10_q1?co_id=${{sid}}`;

  // 連增/連減判讀 + 週柱狀圖（400張大戶每週%）
  let chartHtml = "";
  let weeklyBarsHtml = "";
  if (hist.length >= 1) {{
    let consec = 0; let consecDir = 0;
    for (let i=1; i<hist.length; i++) {{
      const diff = hist[i].major - hist[i-1].major;
      if (diff > 0) {{
        if (consecDir >= 0) consec++; else consec=1;
        consecDir = 1;
      }} else if (diff < 0) {{
        if (consecDir <= 0) consec++; else consec=1;
        consecDir = -1;
      }} else {{ consec = 0; consecDir = 0; }}
    }}
    const consecLabel = consec >= 2 ?
      `<span style="font-size:10px;font-weight:bold;color:${{consecDir>0?'#ff4444':'#00cc88'}};margin-left:6px">連${{consec}}${{consecDir>0?'增':'減'}}</span>` : "";

    chartHtml = `
      <div style="padding:4px 0;border-top:1px solid #21262d;margin-top:4px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          ${{consecLabel}}
          ${{cz ? `<span style="font-size:10px;color:#8b949e">📍成本區：<span style="color:#ffd700;font-weight:bold">${{cz.low}}~${{cz.high}}元</span><span style="color:${{Math.abs(cz.dist)<=10?"#ffd700":cz.dist>10?"#ff6666":"#00cc88"}};margin-left:4px">${{cz.dist>0?"+":""}}${{cz.dist}}%${{Math.abs(cz.dist)<=5?" ✅":cz.dist>20?" ⚠️":""}}</span></span>` : ""}}
        </div>
        <a href="${{mopsUrl}}" target="_blank" style="color:#58a6ff;font-size:11px;text-decoration:none">📋 月營收YoY →</a>
      </div>`;

    // 400 張大戶每週% 柱狀圖（最近 8 週）
    const showHist = hist.slice(-8);
    if (showHist.length >= 1) {{
      const majorVals = showHist.map(x => x.major);
      const maxMajor = Math.max(...majorVals, 1);
      const minMajor = Math.min(...majorVals);
      const range = Math.max(maxMajor - minMajor, 0.5);
      const barH = 50;
      const bars = showHist.map((w, i) => {{
        const isLast = i === showHist.length - 1;
        const prev = i > 0 ? showHist[i-1].major : null;
        const diff = prev !== null ? (w.major - prev) : 0;
        const heightPx = Math.max(8, Math.round((w.major - minMajor + 0.5) / range * barH));
        const barColor = isLast ? "#ff4444"
          : diff > 0 ? "rgba(255,68,68,0.75)"
          : diff < 0 ? "rgba(0,200,100,0.75)"
          : "rgba(255,255,255,0.3)";
        const diffStr = prev !== null
          ? `<span style="font-size:9px;color:${{diff>0?'#ff8888':diff<0?'#00cc88':'#888'}};white-space:nowrap">${{diff>0?'+':''}}${{diff.toFixed(1)}}%</span>`
          : `<span style="font-size:9px;color:#888">—</span>`;
        const dateStr = (w.date || "").length >= 8 ?
          (w.date.slice(4,6) + "/" + w.date.slice(6,8)) : "";
        // 散戶 ratio
        const smallPct = w.small || 0;
        // 散戶位置 (相對位置 dot)
        return `<div style="display:flex;flex-direction:column;align-items:center;gap:1px;flex:1;min-width:36px">
          <span style="font-size:10px;color:#ff8888;font-weight:${{isLast?'bold':'normal'}};white-space:nowrap">${{w.major}}%</span>
          ${{diffStr}}
          <div style="width:100%;max-width:24px;height:${{barH}}px;display:flex;align-items:flex-end;position:relative">
            <div style="width:100%;height:${{heightPx}}px;background:${{barColor}};border-radius:2px 2px 0 0;"></div>
          </div>
          <span style="font-size:9px;color:#8b949e;white-space:nowrap">${{dateStr}}</span>
        </div>`;
      }}).join("");

      weeklyBarsHtml = `
        <div style="padding:6px 0 4px;border-top:1px solid #21262d;margin-top:4px">
          <div style="display:flex;align-items:center;gap:8px;font-size:10px;color:#8b949e;margin-bottom:3px">
            <span style="color:#ff6666;font-weight:bold">📊 400張大戶每週%</span>
            <span>（最近 ${{showHist.length}} 週｜🔴上升 🟢下降）</span>
          </div>
          <div style="display:flex;align-items:flex-end;gap:2px;width:100%">${{bars}}</div>
        </div>`;
    }}
  }}

  return `<div class="holding-bar" style="flex-direction:column;align-items:flex-start;gap:4px">
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;width:100%">
      <span style="font-weight:bold;font-size:12px;color:#e6edf3">📊 籌碼分析</span>
      <div class="holding-track" style="flex:1;min-width:100px;max-width:180px">
        <div class="holding-major" style="width:${{majPct}}%"></div>
        <div class="holding-mid" style="width:${{midPct}}%"></div>
        <div class="holding-small" style="width:${{smlPct}}%"></div>
      </div>
      <span class="holding-lbl-major">🔴大戶 ${{h.major}}%</span>
      <span class="holding-lbl-mid">🟡中實戶 ${{h.mid}}%</span>
      <span class="holding-lbl-small">🔵散戶 ${{h.small}}%</span>
      <span style="color:#f0a500;font-size:11px">千張 ${{h.whale}}%</span>
      ${{whaleTag}}
      <span style="color:#8b949e;font-size:11px">股東 ${{h.persons>0?h.persons.toLocaleString():"—"}}人</span>
    </div>
    ${{signal ? `<div style="margin:2px 0">${{signal}}</div>` : ""}}
    ${{weeklyBarsHtml}}
    ${{chartHtml}}
  </div>`;
}}

function createCard(s) {{
  const sigInfo = getSigInfo(s.sig);
  const sigLabel = keyToLabel[s.sig] || s.sig || "";
  const riskColor = riskColors[s.risk] || "#888";
  const div = document.createElement("div");
  div.className = "card";
  div.dataset.sid = s.sid;
  div.innerHTML = `
    <div class="card-header" style="background:${{sigInfo.bg}}; border-bottom:2px solid ${{sigInfo.color}}">
      <div class="stock-info" style="flex:1;min-width:0">
        <div style="flex:1">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="stock-code">${{s.sid}}</span>
            <span class="stock-name">${{s.name}}</span>
            <span class="sig-badge" style="background:${{sigInfo.color}}">${{sigLabel}}</span>
            ${{s.subsector_alias?`<span style="font-size:10px;background:rgba(255,170,221,0.15);color:#ffaadd;padding:1px 6px;border-radius:10px;font-weight:600" title="子族群輪動排名 #${{s.subsector_rank||'?'}}">${{s.subsector_icon}} ${{s.subsector_alias}}${{s.subsector_rank?` #${{s.subsector_rank}}`:""}}</span>`:""}}
            ${{s.meta&&s.meta.industry?`<span style="font-size:10px;background:rgba(88,166,255,0.15);color:#58a6ff;padding:1px 6px;border-radius:10px">${{s.meta.industry}}</span>`:""}}
          </div>
          ${{s.meta&&s.meta.product?`<div style="font-size:10px;color:#8b949e;margin-top:2px">📦 ${{s.meta.product}}　${{s.meta.biz||""}}</div>`:""}}
          ${{s.rev&&s.rev.rev?`<div style="font-size:10px;margin-top:2px"><span style="color:#8b949e">月營收：</span><span style="color:#e6edf3;font-weight:bold">${{s.rev.rev}}</span>${{s.rev.yoy?`<span style="color:${{parseFloat(s.rev.yoy)>0?"#3fb950":"#f85149"}};margin-left:6px">YoY ${{s.rev.yoy}}%</span>`:""}}<span style="color:#8b949e;margin-left:6px">${{s.rev.month||""}}</span></div>`:""}}
        </div>
      </div>
      ${{(s.rev&&s.rev.news&&s.rev.news.length)?`<div style="flex:1;padding:0 12px;border-left:1px solid #30363d;display:flex;flex-direction:column;justify-content:center;gap:3px;min-width:0;max-width:300px">
        ${{s.rev.news.slice(0,3).map(n=>`<a href="${{n.u||"#"}}" target="_blank" style="display:block;font-size:11px;color:#adbac7;line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none" title="${{n.t}}" onmouseover="this.style.color='#58a6ff'" onmouseout="this.style.color='#adbac7'">📰 ${{n.t}}</a>`).join("")}}
      </div>`:""}}
      <div class="score-info">
        <div class="score-val">${{s.score}}</div>
        <div class="score-label">評分</div>
        <div class="risk-tag" style="background:${{riskColor}}20;color:${{riskColor}}">${{s.risk}}</div>
      </div>
    </div>
    ${{(() => {{
      const p = s.pattern || {{}};
      const pname = p.name || '';
      if ((pname.indexOf('W底')>=0||pname.indexOf('頭肩底')>=0||pname.indexOf('三重底')>=0) && pname.indexOf('✅')>=0) {{
        return `<div data-blink="green" style="background:linear-gradient(90deg,#1a3a1a,#0d2a0d);border-left:4px solid #3fb950;padding:6px 14px;display:flex;align-items:center;gap:8px;font-size:12px">
          <span style="font-size:16px">🚀</span>
          <span style="color:#3fb950;font-weight:bold">頸線突破確認！</span>
          <span style="color:#adbac7">${{pname.replace('✅','').trim()}} 已突破頸線，反轉訊號確認</span>
          <span style="background:#3fb950;color:#000;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:bold;margin-left:4px">做多訊號</span>
        </div>`;
      }}
      if ((pname.indexOf('M頭')>=0||pname.indexOf('頭肩頂')>=0) && pname.indexOf('⚠️')>=0) {{
        return `<div data-blink="red" style="background:linear-gradient(90deg,#3a1a1a,#2a0d0d);border-left:4px solid #f85149;padding:6px 14px;display:flex;align-items:center;gap:8px;font-size:12px">
          <span style="font-size:16px">⚠️</span>
          <span style="color:#f85149;font-weight:bold">頸線跌破確認！</span>
          <span style="color:#adbac7">${{pname.replace('⚠️','').trim()}} 已跌破頸線</span>
          <span style="background:#f85149;color:#fff;padding:1px 8px;border-radius:10px;font-size:11px;margin-left:4px">注意風險</span>
        </div>`;
      }}
      return '';
    }})()}}
    <div class="pattern-bar">
      ${{(() => {{
        const p = s.pattern || {{}};
        const catMap = {{'底部反轉':'pattern-cat-bottom','頭部反轉':'pattern-cat-top','中繼整理':'pattern-cat-cont'}};
        const catClass = catMap[p.cat] || '';
        const catIcon = p.cat==='底部反轉'?'📈':p.cat==='頭部反轉'?'📉':p.cat==='中繼整理'?'➡️':'🔍';
        const otherPats = (p.all||[]).slice(1).map(x=>`${{x[0]}}(${{x[1]}})`).join(' / ');
        return `<span class="pattern-name ${{catClass}}">${{catIcon}} ${{p.name||'觀察中'}}</span>
                <span class="pattern-vp">${{p.vp||''}}</span>
                <span class="pattern-desc">${{p.desc||''}}</span>
                ${{otherPats ? `<span class="pattern-conf">候選：${{otherPats}}</span>` : ''}}`;
      }})()}}
    </div>
    <div class="chart-container">
      <canvas id="c_${{s.sid}}" height="380"></canvas>
    </div>
    ${{(() => {{
      const ss = s.strategies || [];
      if (ss.length === 0) return "";
      const typeColor = {{buy:"#3fb950",sell:"#f85149",warning:"#f0a500",info:"#58a6ff"}};
      const badges = ss.slice(0,12).map(m =>
        `<span title="${{m.signal}}" style="display:inline-block;background:${{typeColor[m.type]||"#888"}}20;color:${{typeColor[m.type]||"#888"}};border:1px solid ${{typeColor[m.type]||"#888"}}40;padding:2px 8px;border-radius:10px;font-size:10px;margin:2px 3px 2px 0;white-space:nowrap">Ep${{m.ep}} ${{m.name}}</span>`
      ).join("");
      const more = ss.length > 12 ? `<span style="color:#8b949e;font-size:10px">+${{ss.length-12}}</span>` : "";
      return `<div style="padding:8px 14px;background:linear-gradient(90deg,#0d2a3a,#1a3a4a);border-top:1px solid #21262d">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;margin-bottom:4px">
          <span style="color:#58a6ff;font-weight:bold">📚 飆股在線等策略匹配</span>
          <span style="background:${{s.strat_color}};color:#000;padding:2px 10px;border-radius:12px;font-weight:bold;font-size:11px">${{s.strat_action}}（分數 ${{s.strat_score}}）</span>
          <span style="color:#8b949e;font-size:10px">${{ss.length}} 個訊號</span>
        </div>
        <div style="line-height:1.8">${{badges}}${{more}}</div>
      </div>`;
    }})()}}
    ${{(() => {{
      const h = s.holding;
      if (!h) return "";
      const total = h.major + h.mid + h.small;
      const majPct = total>0 ? (h.major/total*100).toFixed(0) : 0;
      const midPct = total>0 ? (h.mid/total*100).toFixed(0) : 0;
      const smlPct = total>0 ? (h.small/total*100).toFixed(0) : 0;
      const whaleTag = h.whale > 50 ? `<span style="color:#ff4444;font-weight:bold;margin-left:6px">⚠️超大戶集中 ${{h.whale}}%</span>` : "";
      const hist = h.history || [];

      // ── 籌碼訊號計算 ──
      let signal = ""; // 強力買進/警示/觀察
      let majorTrend = 0; // 大戶持續上升週數
      let personsTrend = 0; // 股東人數持續下降週數
      if (hist.length >= 2) {{
        for (let i=1; i<hist.length; i++) {{
          if (hist[i].major > hist[i-1].major) majorTrend++;
          else majorTrend = 0;
          if (hist[i].persons > 0 && hist[i-1].persons > 0 && hist[i].persons < hist[i-1].persons) personsTrend++;
          else personsTrend = 0;
        }}
        const lastMajorDiff = hist.length>=2 ? (hist[hist.length-1].major - hist[hist.length-2].major) : 0;
        const lastPersonsDiff = hist.length>=2 && hist[hist.length-1].persons>0 ?
          (hist[hist.length-1].persons - hist[hist.length-2].persons) : 0;
        // 強力籌碼集中訊號
        if (majorTrend >= 2 && personsTrend >= 2) {{
          signal = `<span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:4px;font-weight:bold;font-size:11px">🚀 籌碼集中！大戶↑${{majorTrend}}週 股東人數↓${{personsTrend}}週</span>`;
        }} else if (majorTrend >= 1 && lastPersonsDiff < 0) {{
          signal = `<span style="background:rgba(255,68,68,0.2);color:#ff8888;padding:2px 8px;border-radius:4px;font-size:11px">📈 大戶增加中 股東人數下降</span>`;
        }} else if (lastMajorDiff < -1) {{
          signal = `<span style="background:rgba(255,100,0,0.2);color:#ff8800;padding:2px 8px;border-radius:4px;font-size:11px">⚠️ 大戶持股下滑 注意出貨</span>`;
        }}
      }}

      // ── 週歷史表格 ──
      let histTable = "";
      if (hist.length >= 1) {{
        const rows = hist.map((w,i) => {{
          const isLast = i === hist.length-1;
          const prevMajor = i>0 ? hist[i-1].major : w.major;
          const prevPersons = i>0 ? hist[i-1].persons : w.persons;
          const majorDiff = i>0 ? (w.major - prevMajor).toFixed(1) : "-";
          const personsDiff = i>0 && w.persons>0 ? (w.persons - prevPersons) : null;
          const majorColor = majorDiff>0 ? "#ff4444" : majorDiff<0 ? "#00aa44" : "#888";
          const personsColor = personsDiff!==null ? (personsDiff<0 ? "#ff4444" : "#00aa44") : "#888";
          const majorArrow = majorDiff>0 ? "↑" : majorDiff<0 ? "↓" : "";
          const personsArrow = personsDiff!==null ? (personsDiff<0 ? "↓" : "↑") : "";
          const bg = isLast ? "rgba(255,255,255,0.06)" : "transparent";
          const dateStr = w.date.slice(0,4)+"/"+w.date.slice(4,6)+"/"+w.date.slice(6,8);
          return `<tr style="background:${{bg}}">
            <td style="padding:2px 8px;color:#8b949e;font-size:10px">${{dateStr}}</td>
            <td style="padding:2px 8px;text-align:right;color:#ff6666;font-size:11px;font-weight:${{isLast?"bold":"normal"}}">${{w.major}}%</td>
            <td style="padding:2px 8px;text-align:right;color:${{majorColor}};font-size:10px">${{i>0?majorArrow+(majorDiff>0?"+":"")+majorDiff:"—"}}</td>
            <td style="padding:2px 8px;text-align:right;color:#f0a500;font-size:11px">${{w.whale}}%</td>
            <td style="padding:2px 8px;text-align:right;color:#58a6ff;font-size:10px">${{w.persons>0?w.persons.toLocaleString():"—"}}</td>
            <td style="padding:2px 8px;text-align:right;color:${{personsColor}};font-size:10px">${{personsDiff!==null && w.persons>0 ? personsArrow+(personsDiff>0?"+":"")+personsDiff : "—"}}</td>
          </tr>`;
        }}).join("");
        histTable = `<table style="width:100%;border-collapse:collapse;margin-top:4px">
          <thead><tr style="border-bottom:1px solid #30363d">
            <th style="padding:2px 8px;text-align:left;color:#8b949e;font-size:10px;font-weight:normal">日期</th>
            <th style="padding:2px 8px;text-align:right;color:#ff6666;font-size:10px;font-weight:normal">大戶400張+</th>
            <th style="padding:2px 8px;text-align:right;color:#8b949e;font-size:10px;font-weight:normal">週變化</th>
            <th style="padding:2px 8px;text-align:right;color:#f0a500;font-size:10px;font-weight:normal">千張大戶</th>
            <th style="padding:2px 8px;text-align:right;color:#58a6ff;font-size:10px;font-weight:normal">總股東人數</th>
            <th style="padding:2px 8px;text-align:right;color:#8b949e;font-size:10px;font-weight:normal">人數變化</th>
          </tr></thead>
          <tbody>${{rows}}</tbody>
        </table>`;
      }}

      // 步驟2：大戶成本區
      const cz = h.cost_zone;
      const mopsUrl = `https://mops.twse.com.tw/mops/web/t05st10_q1?co_id=${{s.sid}}`;

      // ── 週柱狀圖（仿集保大戶圖）──
      let chartHtml = "";
      if (hist.length >= 1) {{
        const maxMajor = Math.max(...hist.map(x=>x.major), 1);
        const minMajor = Math.min(...hist.map(x=>x.major));
        const chartH = 60; // 柱子最大高度px

        // 計算連增/連減標示
        let consec = 0; let consecDir = 0;
        for (let i=1; i<hist.length; i++) {{
          const diff = hist[i].major - hist[i-1].major;
          if (diff > 0) {{
            if (consecDir >= 0) consec++; else consec=1;
            consecDir = 1;
          }} else if (diff < 0) {{
            if (consecDir <= 0) consec++; else consec=1;
            consecDir = -1;
          }} else {{
            consec = 0; consecDir = 0;
          }}
        }}
        const consecLabel = consec >= 2 ?
          `<span style="font-size:10px;font-weight:bold;color:${{consecDir>0?'#ff4444':'#00cc88'}};margin-left:6px">連${{consec}}${{consecDir>0?'增':'減'}}</span>` : "";

        const bars = hist.map((w,i) => {{
          const isLast = i === hist.length-1;
          const barH = Math.max(8, Math.round((w.major - minMajor + 1) / (maxMajor - minMajor + 1) * chartH));
          const diff = i>0 ? w.major - hist[i-1].major : 0;
          const barColor = isLast ? "#ff4444" : diff >= 0 ? "rgba(255,68,68,0.7)" : "rgba(0,200,100,0.7)";
          const dateStr = w.date.slice(4,6)+"/"+w.date.slice(6,8);
          const diffStr = i>0 ? (diff>=0?"+":"")+diff.toFixed(1)+"%" : "";
          const diffColor = diff > 0 ? "#ff8888" : diff < 0 ? "#00cc88" : "#888";
          return `<div style="display:flex;flex-direction:column;align-items:center;gap:1px;flex:1;min-width:30px">
            <span style="font-size:9px;color:#ff8888;font-weight:${{isLast?'bold':'normal'}}">${{w.major}}%</span>
            <span style="font-size:9px;color:${{diffColor}}">${{diffStr}}</span>
            <div style="width:100%;max-width:22px;height:${{barH}}px;background:${{barColor}};border-radius:2px 2px 0 0;margin:0 auto"></div>
            <span style="font-size:9px;color:#8b949e">${{dateStr}}</span>
          </div>`;
        }}).join("");

        // 散戶趨勢
        const smallBars = hist.map((w,i) => {{
          const isLast = i === hist.length-1;
          const barH = Math.max(4, Math.round(w.small / 100 * 30));
          const diff = i>0 ? w.small - hist[i-1].small : 0;
          const barColor = diff > 0 ? "rgba(88,166,255,0.8)" : "rgba(88,166,255,0.4)";
          return `<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:30px">
            <div style="width:100%;max-width:22px;height:${{barH}}px;background:${{barColor}};border-radius:2px 2px 0 0;margin:0 auto"></div>
          </div>`;
        }}).join("");

        // 成本區資訊
        let costHtml = "";
        if (cz && cz.weeks >= 1) {{
          const distColor = Math.abs(cz.dist) <= 10 ? "#ffd700" : cz.dist > 10 ? "#ff6666" : "#00cc88";
          const riskLabel = Math.abs(cz.dist) <= 5 ? "✅ 風險報酬佳" : cz.dist > 20 ? "⚠️ 偏高" : "";
          costHtml = `<span style="font-size:11px;color:#8b949e">📍成本區：</span>
            <span style="font-size:11px;color:#ffd700;font-weight:bold">${{cz.low}}~${{cz.high}}元</span>
            <span style="font-size:10px;color:${{distColor}};margin-left:4px">${{cz.dist>0?"+":""}}${{cz.dist}}% ${{riskLabel}}</span>`;
        }}

        // 大戶趨勢圖（週柱狀 + X軸對齊K線日期）
        const totalDays = s.dates.length;
        const pts = (s.holding && s.holding.chart_points) ? s.holding.chart_points : [];
        const allMajorV = hist.map(x=>x.major);
        const maxMajorV = Math.max(...allMajorV, 1);
        const minMajorV = Math.min(...allMajorV, 0);

        const barsHtml = pts.map((p, i) => {{
          const leftPct = ((p.idx + 0.5) / totalDays * 100).toFixed(2);
          const widthPct = (1 / totalDays * 100 * 0.7).toFixed(2);
          const heightPct = Math.max(10, Math.round((p.major - minMajorV) / (maxMajorV - minMajorV + 1) * 85));
          const prev = i>0 ? pts[i-1] : null;
          const diff = prev ? (p.major - prev.major).toFixed(1) : null;
          const isLast = i===pts.length-1;
          const barColor = isLast ? "#ff4444" : (diff!==null && diff>0) ? "rgba(255,68,68,0.75)" : "rgba(0,200,100,0.75)";
          const diffStr = diff!==null ? `<div style="font-size:8px;color:${{diff>0?'#ff8888':'#00cc88'}};text-align:center">${{diff>0?"+":""}}${{diff}}%</div>` : "";
          const dateStr = p.date.slice(4,6)+"/"+p.date.slice(6,8);

          // 散戶點
          const hw = hist.find(h=>h.date===p.date);
          const smallH = hw ? Math.max(5, Math.round(hw.small / maxMajorV * 85)) : 0;
          const smallDot = hw ? `<div style="position:absolute;bottom:${{smallH}}%;left:50%;transform:translateX(-50%);width:6px;height:6px;border-radius:50%;background:#58a6ff;"></div>` : "";

          return `<div style="position:absolute;left:${{leftPct}}%;width:${{widthPct}}%;bottom:18px;display:flex;flex-direction:column;align-items:center">
            <div style="font-size:8px;color:${{isLast?'#ff4444':'#ffaaaa'}};text-align:center;white-space:nowrap">${{p.major}}%</div>
            ${{diffStr}}
            <div style="position:relative;width:100%">
              <div style="width:100%;height:${{heightPct}}px;background:${{barColor}};border-radius:2px 2px 0 0;min-height:6px"></div>
              ${{smallDot}}
            </div>
            <div style="font-size:8px;color:#8b949e;text-align:center;margin-top:2px">${{dateStr}}</div>
          </div>`;
        }}).join("");

        chartHtml = `
        <div style="padding:4px 0;border-top:1px solid #21262d;margin-top:4px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            ${{consecLabel}}
            ${{cz ? `<span style="font-size:10px;color:#8b949e">📍成本區：<span style="color:#ffd700;font-weight:bold">${{cz.low}}~${{cz.high}}元</span><span style="color:${{Math.abs(cz.dist)<=10?"#ffd700":cz.dist>10?"#ff6666":"#00cc88"}};margin-left:4px">${{cz.dist>0?"+":""}}${{cz.dist}}%${{Math.abs(cz.dist)<=5?" ✅":cz.dist>20?" ⚠️":""}}</span></span>` : ""}}
            <span style="font-size:9px;color:#555e6b">（大戶趨勢已整合至上方K線圖第四區）</span>
          </div>
          <a href="${{mopsUrl}}" target="_blank" style="color:#58a6ff;font-size:11px;text-decoration:none">📋 月營收YoY →</a>
        </div>`;
      }}

      return `<div class="holding-bar" style="flex-direction:column;align-items:flex-start;gap:4px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;width:100%">
          <span style="font-weight:bold;font-size:12px;color:#e6edf3">📊 籌碼分析</span>
          <div class="holding-track" style="flex:1;min-width:100px;max-width:180px">
            <div class="holding-major" style="width:${{majPct}}%"></div>
            <div class="holding-mid" style="width:${{midPct}}%"></div>
            <div class="holding-small" style="width:${{smlPct}}%"></div>
          </div>
          <span class="holding-lbl-major">🔴大戶 ${{h.major}}%</span>
          <span class="holding-lbl-mid">🟡中實戶 ${{h.mid}}%</span>
          <span class="holding-lbl-small">🔵散戶 ${{h.small}}%</span>
          <span style="color:#f0a500;font-size:11px">千張 ${{h.whale}}%</span>
          ${{whaleTag}}
          <span style="color:#8b949e;font-size:11px">股東 ${{h.persons>0?h.persons.toLocaleString():"—"}}人</span>
        </div>
        ${{signal ? `<div style="margin:2px 0">${{signal}}</div>` : ""}}
        ${{chartHtml}}
      </div>`;
    }})()}}`;
  return div;
}}

function drawChart(s) {{
  const canvas = document.getElementById("c_"+s.sid);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth * dpr;
  const H = 480 * dpr;
  canvas.width = W; canvas.height = H;
  ctx.scale(dpr, dpr);
  const w = W/dpr, h = H/dpr;

  const PAD = {{top:20, right:10, bottom:20, left:55}};
  const chartH = h * 0.42;
  const volH   = h * 0.13;
  const volTop = chartH + 15;
  const kdH    = h * 0.13;
  const kdTop  = volTop + volH + 15;
  const holdH  = h * 0.13;
  const holdTop = kdTop + kdH + 15;
  const n = s.dates.length;
  if (n === 0) return;

  // Colors
  ctx.fillStyle = "#161b22";
  ctx.fillRect(0, 0, w, h);

  // Price range
  const allH = s.highs, allL = s.lows;
  const maxP = Math.max(...allH) * 1.01;
  const minP = Math.min(...allL) * 0.99;
  const maxV = Math.max(...s.vols) * 1.1;

  const px = i => PAD.left + (i + 0.5) * (w - PAD.left - PAD.right) / n;
  const py = p => PAD.top + (1 - (p - minP)/(maxP - minP)) * (chartH - PAD.top - PAD.bottom);
  const pv = v => volTop + volH * (1 - v/maxV);
  const cw = Math.max(1, (w - PAD.left - PAD.right) / n * 0.6);

  // Grid lines
  ctx.strokeStyle = "#21262d"; ctx.lineWidth = 0.5;
  for (let i=0; i<=4; i++) {{
    const y = PAD.top + i * (chartH - PAD.top - PAD.bottom) / 4;
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
    const price = maxP - i * (maxP - minP) / 4;
    ctx.fillStyle = "#8b949e"; ctx.font = "10px Arial"; ctx.textAlign = "right";
    ctx.fillText(price.toFixed(1), PAD.left-3, y+4);
  }}

  // MA lines
  const maColors = {{"ma5":"#f0a500","ma10":"#58a6ff","ma20":"#bc8cff"}};
  [["ma5",s.ma5],["ma10",s.ma10],["ma20",s.ma20]].forEach(([k,ma]) => {{
    ctx.strokeStyle = maColors[k]; ctx.lineWidth = 1; ctx.beginPath();
    let started = false;
    ma.forEach((v,i) => {{
      if (v == null || isNaN(v)) return;
      if (!started) {{ ctx.moveTo(px(i), py(v)); started=true; }}
      else ctx.lineTo(px(i), py(v));
    }});
    ctx.stroke();
  }});

  // Candles
  s.dates.forEach((d,i) => {{
    const o=s.opens[i], h2=s.highs[i], l=s.lows[i], c=s.closes[i];
    const isUp = c >= o;
    const color = isUp ? "#ff4444" : "#00aa44";
    const x = px(i);
    // Wick
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, py(h2)); ctx.lineTo(x, py(l)); ctx.stroke();
    // Body
    const bodyTop = py(Math.max(o,c));
    const bodyBot = py(Math.min(o,c));
    const bh = Math.max(1, bodyBot - bodyTop);
    ctx.fillStyle = color;
    ctx.fillRect(x - cw/2, bodyTop, cw, bh);
  }});

  // Support & Resistance lines
  const srSupport    = s.support    || [];
  const srResistance = s.resistance || [];

  // Draw support lines (blue)
  srSupport.forEach((sr, idx) => {{
    const y = py(sr.price);
    const x1 = PAD.left;
    const x2 = w - PAD.right;
    const alpha = sr.strength >= 2 ? 0.7 : 0.4;
    const lw    = sr.strength >= 2 ? 1.5 : 1;
    ctx.save();
    ctx.strokeStyle = "#00aaff";
    ctx.lineWidth = lw;
    ctx.globalAlpha = alpha;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // Label
    ctx.font = "bold 9px Arial"; ctx.textAlign = "right";
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(x2-36, y-10, 35, 12);
    ctx.fillStyle = "#00aaff";
    ctx.fillText("支 "+sr.price.toFixed(1), x2, y);
    ctx.restore();
  }});

  // Draw resistance lines (red/orange)
  srResistance.forEach((sr, idx) => {{
    const y = py(sr.price);
    const x1 = PAD.left;
    const x2 = w - PAD.right;
    const alpha = sr.strength >= 2 ? 0.7 : 0.4;
    const lw    = sr.strength >= 2 ? 1.5 : 1;
    ctx.save();
    ctx.strokeStyle = "#ff6600";
    ctx.lineWidth = lw;
    ctx.globalAlpha = alpha;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // Label
    ctx.font = "bold 9px Arial"; ctx.textAlign = "right";
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.fillRect(x2-36, y-10, 35, 12);
    ctx.fillStyle = "#ff6600";
    ctx.fillText("壓 "+sr.price.toFixed(1), x2, y);
    ctx.restore();
  }});

  // Pattern drawing (lines & marks on price chart)
  const patLines = (s.pattern && s.pattern.lines) ? s.pattern.lines : [];
  const patMarks = (s.pattern && s.pattern.marks) ? s.pattern.marks : [];

  // Draw pattern lines
  patLines.forEach(line => {{
    if (line.x1==null || line.y1==null) return;
    ctx.save();
    ctx.strokeStyle = line.color || "#ffd700";
    ctx.lineWidth = line.width || 1.5;
    if (line.dash) ctx.setLineDash([6,3]);
    ctx.globalAlpha = 0.85;
    ctx.beginPath();
    ctx.moveTo(px(line.x1), py(line.y1));
    ctx.lineTo(px(line.x2), py(line.y2));
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
    // Label
    if (line.label) {{
      ctx.fillStyle = line.color || "#ffd700";
      ctx.font = "bold 9px Arial";
      ctx.textAlign = "center";
      const lx = px((line.x1+line.x2)/2);
      const ly = py((line.y1+line.y2)/2) - 6;
      // Background for label
      const tw = ctx.measureText(line.label).width;
      ctx.fillStyle = "rgba(0,0,0,0.6)";
      ctx.fillRect(lx-tw/2-2, ly-9, tw+4, 12);
      ctx.fillStyle = line.color || "#ffd700";
      ctx.fillText(line.label, lx, ly);
    }}
    ctx.restore();
  }});

  // Draw pattern marks
  patMarks.forEach(mark => {{
    if (mark.x==null || mark.y==null) return;
    const mx = px(mark.x);
    const my = py(mark.y);
    const sz = mark.size || 5;
    ctx.save();
    ctx.fillStyle = mark.color || "#ffd700";
    ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.lineWidth = 1;
    if (mark.shape === "diamond") {{
      ctx.beginPath();
      ctx.moveTo(mx, my-sz-2); ctx.lineTo(mx+sz, my);
      ctx.lineTo(mx, my+sz+2); ctx.lineTo(mx-sz, my);
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }} else {{
      ctx.beginPath();
      ctx.arc(mx, my, sz, 0, Math.PI*2);
      ctx.fill(); ctx.stroke();
    }}
    // Text label with background
    if (mark.text) {{
      ctx.font = "bold 9px Arial";
      ctx.textAlign = "center";
      const tw = ctx.measureText(mark.text).width;
      // 底類標籤放下方，頸/肩/頭放上方
      const isBottom = mark.text.includes("底") || mark.text.includes("左肩") || mark.text.includes("右肩");
      const ty = isBottom ? my + sz + 14 : my - sz - 4;
      ctx.fillStyle = "rgba(0,0,0,0.7)";
      ctx.fillRect(mx-tw/2-3, ty-9, tw+6, 12);
      ctx.fillStyle = mark.color || "#ffd700";
      ctx.fillText(mark.text, mx, ty);
    }}
    ctx.restore();
  }});

  // Volume bars
  s.dates.forEach((d,i) => {{
    const isUp = s.closes[i] >= s.opens[i];
    ctx.fillStyle = isUp ? "rgba(255,68,68,0.6)" : "rgba(0,170,68,0.6)";
    const x = px(i);
    const top = pv(s.vols[i]);
    ctx.fillRect(x - cw/2, top, cw, volTop + volH - top);
  }});

  // VMA20 line on volume chart
  const vma20arr = (s.vma20 || []).map(v => (v==null||isNaN(v)) ? null : v);
  const validVma = vma20arr.filter(v => v!=null && v>0);
  if (validVma.length > 0 && maxV > 0) {{
    ctx.save();
    ctx.strokeStyle = "#ffee00";
    ctx.lineWidth = 2.5;
    ctx.setLineDash([6, 2]);
    ctx.beginPath();
    let vStarted = false;
    vma20arr.forEach((v,i) => {{
      if (v==null || v<=0) return;
      const x = px(i);
      const ratio = Math.min(0.98, Math.max(0.02, v/maxV));
      const y = volTop + volH*(1-ratio);
      if (!vStarted) {{ ctx.moveTo(x,y); vStarted=true; }}
      else ctx.lineTo(x,y);
    }});
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();
    const lastV = validVma[validVma.length-1];
    ctx.fillStyle = "#ffee00";
    ctx.font = "bold 10px Arial";
    ctx.textAlign = "left";
    ctx.fillText("VMA20: "+Math.round(lastV/1000)+"張", PAD.left, volTop+12);
  }}

  // Divider vol/KD
  ctx.strokeStyle = "#30363d"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.left, kdTop); ctx.lineTo(w-PAD.right, kdTop); ctx.stroke();

  // KD chart
  const pk = v => kdTop + kdH * (1 - v/100);
  // 過熱區（>80）填色紅
  ctx.fillStyle = "rgba(220,53,69,0.12)";
  ctx.fillRect(PAD.left, kdTop, w-PAD.left-PAD.right, kdH*(1-80/100));
  // 超賣區（<20）填色綠
  ctx.fillStyle = "rgba(40,167,69,0.12)";
  ctx.fillRect(PAD.left, kdTop+kdH*(1-20/100), w-PAD.left-PAD.right, kdH*20/100);

  // 80/50/20 reference lines
  ctx.strokeStyle = "#333d47"; ctx.lineWidth = 0.5; ctx.setLineDash([3,3]);
  [[80,"過熱"],[50,""],[20,"超賣"]].forEach(([lvl,label]) => {{
    const y = pk(lvl);
    ctx.beginPath(); ctx.moveTo(PAD.left,y); ctx.lineTo(w-PAD.right,y); ctx.stroke();
    ctx.fillStyle="#555e6b"; ctx.font="9px Arial"; ctx.textAlign="right";
    ctx.fillText(lvl, PAD.left-2, y+3);
  }});
  ctx.setLineDash([]);

  // K line (blue)
  ctx.strokeStyle="#58a6ff"; ctx.lineWidth=1.5; ctx.beginPath();
  let kStarted=false;
  (s.k||[]).forEach((v,i) => {{
    if(v==null||isNaN(v)) return;
    const x=px(i), y=pk(v);
    if(!kStarted){{ctx.moveTo(x,y);kStarted=true;}}
    else ctx.lineTo(x,y);
  }});
  ctx.stroke();

  // D line (orange)
  ctx.strokeStyle="#f0a500"; ctx.lineWidth=1.5; ctx.beginPath();
  let dStarted=false;
  (s.d||[]).forEach((v,i) => {{
    if(v==null||isNaN(v)) return;
    const x=px(i), y=pk(v);
    if(!dStarted){{ctx.moveTo(x,y);dStarted=true;}}
    else ctx.lineTo(x,y);
  }});
  ctx.stroke();

  // 黃金交叉 / 死亡交叉 標記
  const karr = s.k||[], darr = s.d||[];
  for (let i=1; i<n; i++) {{
    const k0=karr[i-1], d0=darr[i-1], k1=karr[i], d1=darr[i];
    if (k0==null||d0==null||k1==null||d1==null) continue;
    const x = px(i);

    // 黃金交叉：K 從下往上穿越 D
    if (k0 <= d0 && k1 > d1) {{
      const y = pk((k1+d1)/2);
      // 金色三角形向上
      ctx.beginPath();
      ctx.moveTo(x, y-12); ctx.lineTo(x-6, y); ctx.lineTo(x+6, y);
      ctx.closePath();
      ctx.fillStyle = "#ffd700";
      ctx.fill();
      ctx.fillStyle = "#ffd700"; ctx.font = "bold 8px Arial"; ctx.textAlign = "center";
      ctx.fillText("金", x, y-14);
    }}

    // 死亡交叉：K 從上往下穿越 D
    if (k0 >= d0 && k1 < d1) {{
      const y = pk((k1+d1)/2);
      // 紅色三角形向下
      ctx.beginPath();
      ctx.moveTo(x, y+12); ctx.lineTo(x-6, y); ctx.lineTo(x+6, y);
      ctx.closePath();
      ctx.fillStyle = "#ff4444";
      ctx.fill();
      ctx.fillStyle = "#ff4444"; ctx.font = "bold 8px Arial"; ctx.textAlign = "center";
      ctx.fillText("死", x, y+22);
    }}
  }}

  // 鈍化標記（K>80 連續3根以上 或 K<20 連續3根以上）
  let overboughtCount=0, oversoldCount=0;
  for (let i=0; i<n; i++) {{
    const kv = karr[i];
    if (kv==null) {{ overboughtCount=0; oversoldCount=0; continue; }}
    if (kv>=80) {{
      overboughtCount++;
      if (overboughtCount>=3) {{
        // 過熱鈍化：頂部加紅點
        ctx.beginPath(); ctx.arc(px(i), kdTop+4, 2.5, 0, Math.PI*2);
        ctx.fillStyle="rgba(255,68,68,0.9)"; ctx.fill();
      }}
    }} else {{ overboughtCount=0; }}
    if (kv<=20) {{
      oversoldCount++;
      if (oversoldCount>=3) {{
        // 超賣鈍化：底部加綠點
        ctx.beginPath(); ctx.arc(px(i), kdTop+kdH-4, 2.5, 0, Math.PI*2);
        ctx.fillStyle="rgba(40,167,69,0.9)"; ctx.fill();
      }}
    }} else {{ oversoldCount=0; }}
  }}

  // KD 標籤
  ctx.font="bold 10px Arial"; ctx.textAlign="left";
  const lastK = karr.filter(v=>v!=null).slice(-1)[0];
  const lastD = darr.filter(v=>v!=null).slice(-1)[0];
  const kdStatus = lastK!=null && lastD!=null ? (
    lastK>=80 ? " 🔴過熱" : lastK<=20 ? " 🟢超賣" :
    lastK>lastD ? " ▲多方" : " ▼空方") : "";
  ctx.fillStyle="#58a6ff"; ctx.fillText("K:"+( lastK!=null?lastK.toFixed(1):""), PAD.left, kdTop+12);
  ctx.fillStyle="#f0a500"; ctx.fillText("D:"+(lastD!=null?lastD.toFixed(1):""), PAD.left+52, kdTop+12);
  ctx.fillStyle="#e6edf3"; ctx.fillText(kdStatus, PAD.left+104, kdTop+12);

  // ── 第四區：大戶持股週趨勢（對齊K線X軸）──
  const holdingPts = (s.holding && s.holding.chart_points) ? s.holding.chart_points : [];
  const holdingHist = (s.holding && s.holding.history) ? s.holding.history : [];

  // 分隔線
  ctx.strokeStyle = "#30363d"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.left, holdTop); ctx.lineTo(w-PAD.right, holdTop); ctx.stroke();

  if (holdingPts.length > 0) {{
    const allMaj = holdingHist.map(x=>x.major);
    const allSml = holdingHist.map(x=>x.small);
    const maxMaj = Math.max(...allMaj, 1);
    const minMaj = Math.max(0, Math.min(...allMaj) - 5);
    const phY = v => holdTop + holdH * (1 - (v - minMaj) / (maxMaj - minMaj + 0.1));
    const barW3 = Math.max(6, (w - PAD.left - PAD.right) / n * 0.5);

    // Y軸格線
    ctx.strokeStyle = "#21262d"; ctx.lineWidth = 0.5; ctx.setLineDash([2,2]);
    [minMaj, (minMaj+maxMaj)/2, maxMaj].forEach(v => {{
      const y = phY(v);
      ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(w-PAD.right, y); ctx.stroke();
      ctx.fillStyle = "#555e6b"; ctx.font = "9px Arial"; ctx.textAlign = "right";
      ctx.fillText(Math.round(v)+"%", PAD.left-2, y+3);
    }});
    ctx.setLineDash([]);

    // 大戶柱狀（紅色）
    holdingPts.forEach((p, i) => {{
      const x = px(p.idx);
      const prev = i>0 ? holdingPts[i-1] : null;
      const diff = prev ? p.major - prev.major : 0;
      const isLast = i === holdingPts.length-1;
      const barColor = isLast ? "#ff4444" : diff >= 0 ? "rgba(255,68,68,0.75)" : "rgba(0,200,100,0.75)";
      const top = phY(p.major);
      const bot = phY(minMaj);
      ctx.fillStyle = barColor;
      ctx.fillRect(x - barW3/2, top, barW3, bot - top);

      // 數值標籤
      ctx.fillStyle = isLast ? "#ff4444" : "#ffaaaa";
      ctx.font = "bold 9px Arial"; ctx.textAlign = "center";
      ctx.fillText(p.major+"%", x, top - 3);

      // 週變化標籤
      if (prev) {{
        const diffStr = (diff>=0?"+":"")+diff.toFixed(1)+"%";
        const dc = diff>=0 ? "#ff8888" : "#00cc88";
        ctx.fillStyle = dc; ctx.font = "8px Arial";
        ctx.fillText(diffStr, x, top - 13);
      }}

      // 散戶藍點
      const hw = holdingHist.find(h=>h.date===p.date);
      if (hw && hw.small > 0) {{
        const sy = phY(hw.small);
        ctx.beginPath(); ctx.arc(x, sy, 4, 0, Math.PI*2);
        ctx.fillStyle = "#58a6ff"; ctx.fill();
        ctx.strokeStyle = "#000"; ctx.lineWidth = 0.5; ctx.stroke();
      }}
    }});

    // 折線連接大戶點
    if (holdingPts.length >= 2) {{
      ctx.strokeStyle = "#ff8888"; ctx.lineWidth = 1.5;
      ctx.beginPath();
      holdingPts.forEach((p,i) => {{
        const x = px(p.idx);
        const y = phY(p.major);
        if (i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      }});
      ctx.stroke();
    }}

    // 標題
    ctx.fillStyle = "#ff8888"; ctx.font = "bold 10px Arial"; ctx.textAlign = "left";
    ctx.fillText("大戶%", PAD.left, holdTop + 12);
    ctx.fillStyle = "#58a6ff";
    ctx.fillText("  散戶%", PAD.left + 30, holdTop + 12);
  }} else {{
    ctx.fillStyle = "#555e6b"; ctx.font = "10px Arial"; ctx.textAlign = "center";
    ctx.fillText("大戶持股趨勢（週資料累積中）", w/2, holdTop + holdH/2);
  }}

  // MA legend
  ctx.font = "10px Arial"; ctx.textAlign = "left";
  [["MA5",maColors.ma5],["MA10",maColors.ma10],["MA20",maColors.ma20]].forEach(([label,color],i) => {{
    ctx.fillStyle = color;
    ctx.fillText(label, PAD.left + i*50, PAD.top - 6);
  }});

  // Latest price label
  const lastClose = s.closes[n-1];
  ctx.fillStyle = "#fff"; ctx.font = "bold 11px Arial"; ctx.textAlign = "left";
  ctx.fillText("收："+lastClose.toFixed(2), PAD.left, h-4);

  // Tooltip on hover
  canvas._chartData = s;
  canvas._chartFns = {{px, py, n, PAD, cw}};
}}

// Tooltip — 顯示完整 K 棒資訊 + 智慧位置（避免被截掉）
document.addEventListener("mousemove", function(e) {{
  const canvas = e.target;
  if (!canvas.tagName || canvas.tagName!=="CANVAS" || !canvas._chartData) return;
  const s = canvas._chartData;
  const {{px, n, PAD}} = canvas._chartFns;
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const barW = (rect.width - PAD.left - PAD.right) / n;
  const idx = Math.floor((mx - PAD.left) / barW);
  if (idx < 0 || idx >= n) {{ document.getElementById("tooltip").style.display="none"; return; }}

  const op = s.opens[idx], hi = s.highs[idx], lo = s.lows[idx], cl = s.closes[idx];
  const vol = s.vols[idx];
  const isUp = cl >= op;
  const clr = isUp ? "t-up" : "t-down";

  // 漲跌幅（與前一根比）
  const prev = idx > 0 ? s.closes[idx-1] : op;
  const chg = cl - prev;
  const chgPct = prev > 0 ? (chg / prev * 100) : 0;
  const chgClr = chg >= 0 ? "#ff4444" : "#00aa44";
  const chgSign = chg >= 0 ? "▲" : "▼";

  // 量比（與前 5 日均量）
  let volMa5 = null;
  if (idx >= 5) {{
    let sum = 0; for (let k=idx-5; k<idx; k++) sum += s.vols[k];
    volMa5 = sum / 5;
  }}
  const volRatioStr = volMa5 ? `（${{(vol/volMa5).toFixed(1)}}x 5 日均量）` : "";

  // K / D
  let kdHtml = "";
  if (s.k && s.d && s.k[idx] != null && s.d[idx] != null) {{
    kdHtml = `<div style="margin-top:4px;color:#f0c040">K：${{s.k[idx].toFixed(1)}} ／ <span style="color:#58a6ff">D：${{s.d[idx].toFixed(1)}}</span></div>`;
  }}

  // MA5 / MA20
  let maHtml = "";
  if (s.ma5 && s.ma20) {{
    const m5  = s.ma5[idx],  m20 = s.ma20[idx];
    const m5s  = (m5  != null && !isNaN(m5))  ? m5.toFixed(2)  : "—";
    const m20s = (m20 != null && !isNaN(m20)) ? m20.toFixed(2) : "—";
    maHtml = `<div style="color:#8b949e;font-size:11px;margin-top:2px">MA5：<span style="color:#f0c040">${{m5s}}</span>　MA20：<span style="color:#58a6ff">${{m20s}}</span></div>`;
  }}

  // 支撐 / 壓力
  let srHtml = "";
  const sup = s.support || [];
  const res = s.resistance || [];
  if (sup.length > 0 || res.length > 0) {{
    const supStr = sup.slice(0,3).map(x => `<span style="color:#3fb950">${{x.price.toFixed(2)}}</span>`).join("／");
    const resStr = res.slice(0,3).map(x => `<span style="color:#f85149">${{x.price.toFixed(2)}}</span>`).join("／");
    srHtml = `<div style="color:#8b949e;font-size:11px;margin-top:4px;border-top:1px solid #30363d;padding-top:4px">
      ${{sup.length>0 ? `🟢 支撐：${{supStr}}` : ""}}
      ${{(sup.length>0 && res.length>0) ? "<br>" : ""}}
      ${{res.length>0 ? `🔴 壓力：${{resStr}}` : ""}}
    </div>`;
  }}

  // 頸線（從 pattern.lines 抓）
  let neckHtml = "";
  const patLines = (s.pattern && s.pattern.lines) ? s.pattern.lines : (s.lines || []);
  if (patLines && patLines.length > 0) {{
    const necks = patLines.filter(l => (l.label||"").indexOf("頸線") >= 0).map(l => l.y1.toFixed(2));
    // 若是突破候選，s.neckline 也直接可用
    if (typeof s.neckline === "number") {{
      necks.push(s.neckline.toFixed(2));
    }}
    const uniq = [...new Set(necks)];
    if (uniq.length > 0) {{
      neckHtml = `<div style="color:#ffd700;font-size:11px;margin-top:2px">📐 頸線：${{uniq.join("／")}}</div>`;
    }}
  }} else if (typeof s.neckline === "number") {{
    neckHtml = `<div style="color:#ffd700;font-size:11px;margin-top:2px">📐 頸線：${{s.neckline.toFixed(2)}}</div>`;
  }}

  const tip = document.getElementById("tooltip");
  tip.innerHTML = `
    <div class="t-date">${{s.dates[idx]}} ${{(s.sid||"")}} ${{(s.name||"")}}</div>
    <div class="${{clr}}">開：${{op.toFixed(2)}}</div>
    <div class="${{clr}}">高：${{hi.toFixed(2)}}</div>
    <div class="${{clr}}">低：${{lo.toFixed(2)}}</div>
    <div class="${{clr}}">收：${{cl.toFixed(2)}}</div>
    <div style="color:${{chgClr}};font-weight:bold;margin-top:2px">
      ${{chgSign}} ${{chg>=0?"+":""}}${{chg.toFixed(2)}}（${{chgPct>=0?"+":""}}${{chgPct.toFixed(2)}}%）
    </div>
    <div style="color:#8b949e;margin-top:4px">量：${{(vol/1000).toFixed(0)}}張 ${{volRatioStr}}</div>
    ${{maHtml}}
    ${{kdHtml}}
    ${{neckHtml}}
    ${{srHtml}}`;

  // 智慧定位：右側 / 下方超出視窗時自動翻到左 / 上
  tip.style.display = "block";
  tip.style.left = "0px"; tip.style.top = "0px";  // 先重設才能量到正確尺寸
  const tipW = tip.offsetWidth;
  const tipH = tip.offsetHeight;
  const winW = window.innerWidth;
  const winH = window.innerHeight;
  let left = e.clientX + 15;
  let top  = e.clientY - 10;
  if (left + tipW + 10 > winW) left = e.clientX - tipW - 15;   // 翻到游標左側
  if (top  + tipH + 10 > winH) top  = winH - tipH - 10;         // 上推
  if (top  < 10) top = 10;
  if (left < 10) left = 10;
  tip.style.left = left + "px";
  tip.style.top  = top  + "px";
}});
document.addEventListener("mouseleave", () => document.getElementById("tooltip").style.display="none");

// ── 我的持股 渲染 ──────────────────────────────
function renderHoldings(items, gridId, canvasPrefix) {{
  items = items || holdings;
  gridId = gridId || "holdings-grid";
  canvasPrefix = canvasPrefix || "hc_";
  const grid = document.getElementById(gridId);
  if (!grid) return;
  grid.innerHTML = "";
  items.forEach(h => {{
    const div = document.createElement("div");
    div.className = "card";
    const plColor = h.pl_amt >= 0 ? "#3fb950" : "#f85149";
    const plSign  = h.pl_amt >= 0 ? "📈" : "📉";
    const sharesText = h.shares < 1
      ? `${{Math.round(h.shares*1000)}} 股`
      : `${{h.shares}} 張`;
    const costText = h.cost
      ? `成本 ${{h.cost}}`
      : `<span style="color:#8b949e" title="未提供成本，以現價估算">成本以現價估算</span>`;

    // 籌碼摘要（個股才顯示）
    let holdHtml = "";
    if (h.holding && !h.is_etf) {{
      const ho = h.holding;
      const total = ho.major + ho.mid + ho.small;
      const majPct = total>0 ? (ho.major/total*100).toFixed(0) : 0;
      const midPct = total>0 ? (ho.mid/total*100).toFixed(0) : 0;
      const smlPct = total>0 ? (ho.small/total*100).toFixed(0) : 0;
      holdHtml = `<div style="padding:6px 14px;background:rgba(255,255,255,0.03);font-size:11px;color:#adbac7;border-top:1px solid #21262d">
        🏦 籌碼：大戶 ${{ho.major}}% / 中實戶 ${{ho.mid}}% / 散戶 ${{ho.small}}%
        <span style="color:#8b949e">（千張以上 ${{ho.whale}}%）</span>
      </div>`;
    }}

    // 型態（ETF 不顯示）
    let patHtml = "";
    if (h.pattern && !h.is_etf) {{
      const p = h.pattern;
      const catMap = {{'底部反轉':'pattern-cat-bottom','頭部反轉':'pattern-cat-top','中繼整理':'pattern-cat-cont'}};
      const catClass = catMap[p.cat] || '';
      const catIcon = p.cat==='底部反轉'?'📈':p.cat==='頭部反轉'?'📉':p.cat==='中繼整理'?'➡️':'🔍';
      patHtml = `<div class="pattern-bar">
        <span class="pattern-name ${{catClass}}">${{catIcon}} ${{p.name||'觀察中'}}</span>
        <span class="pattern-vp">${{p.vp||''}}</span>
        <span class="pattern-desc">${{p.desc||''}}</span>
      </div>`;
    }}

    div.innerHTML = `
      <div class="card-header" style="background:rgba(240,165,0,0.1);border-bottom:2px solid #f0a500">
        <div class="stock-info" style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="stock-code">${{h.sid}}</span>
            <span class="stock-name">${{h.name}}</span>
            ${{h.is_etf?`<span class="sig-badge" style="background:#7e57c2">ETF</span>`:`<span class="sig-badge" style="background:#1f497d">個股</span>`}}
            <span style="font-size:11px;background:rgba(88,166,255,0.15);color:#58a6ff;padding:2px 8px;border-radius:10px">${{sharesText}}</span>
            <span style="font-size:10px;color:#8b949e">資料：${{h.src}}</span>
          </div>
          <div style="font-size:11px;margin-top:4px">
            <span style="color:#8b949e">現價：</span><span style="color:#fff;font-weight:bold">${{h.current}}</span>
            <span style="color:#8b949e">${{costText}}</span>
          </div>
        </div>
        ${{(h.rev&&h.rev.news&&h.rev.news.length)?`<div style="flex:1;padding:0 12px;border-left:1px solid #30363d;display:flex;flex-direction:column;justify-content:center;gap:2px;min-width:0;max-width:340px">
          ${{h.rev.news.slice(0,7).map(n=>`<a href="${{n.u||"#"}}" target="_blank" style="display:block;font-size:11px;color:#adbac7;line-height:1.4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none" title="${{n.t}}" onmouseover="this.style.color='#58a6ff'" onmouseout="this.style.color='#adbac7'">📰 ${{n.t}}</a>`).join("")}}
        </div>`:""}}
        <div class="score-info">
          <div style="font-size:18px;font-weight:bold;color:${{plColor}}">${{plSign}} ${{h.pl_pct>=0?'+':''}}${{h.pl_pct}}%</div>
          <div style="font-size:11px;color:${{plColor}}">${{h.pl_amt>=0?'+':''}}${{Math.round(h.pl_amt).toLocaleString()}}</div>
          <div style="font-size:10px;color:#8b949e">市值 ${{Math.round(h.market_value).toLocaleString()}}</div>
        </div>
      </div>
      ${{patHtml}}
      <div class="chart-container">
        <canvas id="${{canvasPrefix}}${{h.sid}}" height="280"></canvas>
      </div>
      ${{holdHtml}}
      ${{(() => {{
        const t = h.targets;
        if (!t || t.median == null) {{
          return h.is_etf
            ? `<div style="padding:6px 14px;font-size:11px;color:#8b949e;border-top:1px solid #21262d">📊 ETF 無分析師覆蓋</div>`
            : `<div style="padding:6px 14px;font-size:11px;color:#8b949e;border-top:1px solid #21262d">📊 暫無法人預估目標價</div>`;
        }}
        const upPct = h.upside_pct;
        const upColor = upPct >= 10 ? "#3fb950" : upPct >= 0 ? "#56d364" : upPct >= -10 ? "#f0a500" : "#f85149";
        const recColors = {{
          "強力買進":"#3fb950","買進":"#56d364","持有":"#f0c040",
          "減碼":"#f0a500","賣出":"#f85149","強烈賣出":"#c00000"
        }};
        const recColor = recColors[t.rec_label] || "#8b949e";
        return `
          <div style="padding:8px 14px;background:linear-gradient(90deg,#1a2332,#0d1424);border-top:1px solid #21262d;border-bottom:1px solid #21262d">
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px">
              <span style="color:#58a6ff;font-weight:bold">📊 法人目標價</span>
              <span style="color:#8b949e">${{t.analysts || 0}} 位分析師</span>
              <span style="background:${{recColor}};color:#000;padding:1px 8px;border-radius:10px;font-weight:bold">${{t.rec_label}}</span>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-top:6px;font-size:11px">
              <div><span style="color:#8b949e">最高</span><div style="color:#3fb950;font-weight:bold">${{(t.high||0).toFixed(1)}}</div></div>
              <div><span style="color:#8b949e">中位數</span><div style="color:#f0c040;font-weight:bold">${{(t.median||0).toFixed(1)}}</div></div>
              <div><span style="color:#8b949e">最低</span><div style="color:#f85149;font-weight:bold">${{(t.low||0).toFixed(1)}}</div></div>
              <div><span style="color:#8b949e">潛在</span><div style="color:${{upColor}};font-weight:bold">${{upPct>=0?'+':''}}${{upPct}}%</div></div>
            </div>
          </div>
        `;
      }})()}}
      ${{(() => {{
        // ★ 當月各券商目標價明細（Google News 抓）
        const mt = h.monthly_targets || [];
        if (!mt.length) return "";
        const month = h.monthly_targets_month || "";
        const rows = mt.slice(0,30).map(x => {{
          const diff = h.current ? ((x.price - h.current) / h.current * 100) : 0;
          const diffColor = diff >= 10 ? "#3fb950" : diff >= 0 ? "#56d364" : diff >= -10 ? "#f0a500" : "#f85149";
          const diffTxt = (diff>=0?'+':'') + diff.toFixed(1) + '%';
          return `<tr>
            <td style="padding:2px 6px;color:#adbac7">${{x.date}}</td>
            <td style="padding:2px 6px;color:#58a6ff;font-weight:bold">${{x.broker}}</td>
            <td style="padding:2px 6px;color:#f0c040;font-weight:bold;text-align:right">${{x.price}}</td>
            <td style="padding:2px 6px;text-align:right;color:${{diffColor}}">${{diffTxt}}</td>
            <td style="padding:2px 6px;color:#8b949e;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px" title="${{(x.title||'').replace(/\"/g,'&quot;')}}">
              ${{x.url ? `<a href="${{x.url}}" target="_blank" style="color:#8b949e;text-decoration:none">${{x.title||''}}</a>` : (x.title||'')}}
            </td>
          </tr>`;
        }}).join("");
        return `
          <div style="padding:8px 14px;background:linear-gradient(90deg,#0d1424,#1a2332);border-top:1px solid #21262d">
            <div style="font-size:11px;color:#58a6ff;font-weight:bold;margin-bottom:4px">
              📅 ${{month}} 券商目標價明細（${{mt.length}} 筆｜Google News）
            </div>
            <table style="width:100%;font-size:11px;border-collapse:collapse">
              <thead><tr style="color:#8b949e;border-bottom:1px solid #21262d">
                <th style="padding:2px 6px;text-align:left">日期</th>
                <th style="padding:2px 6px;text-align:left">券商</th>
                <th style="padding:2px 6px;text-align:right">目標價</th>
                <th style="padding:2px 6px;text-align:right">vs 現價</th>
                <th style="padding:2px 6px;text-align:left">標題</th>
              </tr></thead>
              <tbody>${{rows}}</tbody>
            </table>
          </div>
        `;
      }})()}}
      ${{(() => {{
        const sec = h.sections || {{}};
        const ep   = h.entry_price, sl = h.stop_loss, tp = h.target_price;
        const score = (h.score !== undefined) ? h.score : 0;
        const buyN = h.buy_signals||0, sellN = h.sell_signals||0, warnN = h.warn_signals||0;
        const sigSummary = `<span style="color:#3fb950">買訊 ${{buyN}}</span> ／ <span style="color:#f85149">賣訊 ${{sellN}}</span> ／ <span style="color:#f0a500">警示 ${{warnN}}</span>`;
        // 估算停損 / 目標的損益
        const slPct = sl ? (((sl - h.current) / h.current) * 100).toFixed(1) : null;
        const tpPct = tp ? (((tp - h.current) / h.current) * 100).toFixed(1) : null;
        return `<div style="padding:10px 14px;background:linear-gradient(180deg,rgba(255,255,255,0.04),rgba(255,255,255,0.015));border-bottom:1px solid #21262d">
          <!-- 行 1：操作建議 + 綜合分數 -->
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
            <span style="font-size:13px;color:#f0c040;font-weight:bold">🎯 操作建議</span>
            <span style="background:${{h.action_color}};color:#000;padding:3px 14px;border-radius:14px;font-size:13px;font-weight:bold">${{h.action}}</span>
            <span style="font-size:11px;color:#8b949e">綜合分數 <b style="color:${{score>=4?'#3fb950':score>=0?'#f0c040':'#f85149'}}">${{score>=0?'+':''}}${{score}}</b></span>
            <span style="font-size:11px;color:#8b949e">${{sigSummary}}</span>
          </div>
          <!-- 行 2：價位建議 -->
          ${{(ep||sl||tp) ? `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px;padding:6px 10px;background:rgba(0,0,0,0.25);border-radius:6px;font-size:11px">
            ${{ep ? `<div><span style="color:#8b949e">📍加碼點：</span><b style="color:#56d364">${{ep}}</b></div>` : ""}}
            ${{sl ? `<div><span style="color:#8b949e">🛑停損點：</span><b style="color:#f85149">${{sl}}</b><span style="color:#f85149;margin-left:4px">(${{slPct}}%)</span></div>` : ""}}
            ${{tp ? `<div><span style="color:#8b949e">🎯目標價：</span><b style="color:#3fb950">${{tp}}</b><span style="color:#3fb950;margin-left:4px">(+${{tpPct}}%)</span></div>` : ""}}
          </div>` : ""}}
          ${{h.position_advice ? `<div style="font-size:11px;color:#adbac7;line-height:1.6;margin-bottom:8px;padding:6px 10px;background:rgba(63,185,80,0.07);border-left:3px solid #56d364;border-radius:3px">📌 ${{h.position_advice}}</div>` : ""}}
          <!-- 行 3-7：五大面向 -->
          <div style="display:grid;grid-template-columns:auto 1fr;gap:4px 10px;font-size:11px;line-height:1.5">
            <span style="color:#8b949e;font-weight:bold">📈 趨勢</span><span style="color:#adbac7">${{sec.trend || "—"}}</span>
            <span style="color:#8b949e;font-weight:bold">📊 技術</span><span style="color:#adbac7">${{sec.technical || "—"}}</span>
            <span style="color:#8b949e;font-weight:bold">🏦 籌碼</span><span style="color:#adbac7">${{sec.chip || "—"}}</span>
            <span style="color:#8b949e;font-weight:bold">🏛️ 法人</span><span style="color:#adbac7">${{sec.fundamental || "—"}}</span>
            <span style="color:#8b949e;font-weight:bold">📚 策略</span><span style="color:#adbac7">${{sec.strategy || "—"}}</span>
            ${{sec.advanced ? `<span style="color:#f0a500;font-weight:bold">🎯 飆股訊號</span><span style="color:#ffd479;font-weight:600">${{sec.advanced}}</span>` : ""}}
            ${{h.memberships_text ? `<span style="color:#66ccff;font-weight:bold">🏷️ 族群歸屬</span><span style="color:#aaddff;font-weight:600">${{h.memberships_text}}${{h.quality_tier ? ` 【${{h.quality_tier}}】` : ""}}</span>` : ""}}
          </div>
        </div>`;
      }})()}}
      ${{(() => {{
        const ss = h.strategies || [];
        if (ss.length === 0) return "";
        const typeColor = {{buy:"#3fb950",sell:"#f85149",warning:"#f0a500",info:"#58a6ff"}};
        const badges = ss.slice(0,12).map(m =>
          `<span title="${{m.signal}}" style="display:inline-block;background:${{typeColor[m.type]||"#888"}}20;color:${{typeColor[m.type]||"#888"}};border:1px solid ${{typeColor[m.type]||"#888"}}40;padding:2px 8px;border-radius:10px;font-size:10px;margin:2px 3px 2px 0;white-space:nowrap">Ep${{m.ep}} ${{m.name}}</span>`
        ).join("");
        return `<div style="padding:8px 14px;background:linear-gradient(90deg,#0d2a3a,#1a3a4a);border-top:1px solid #21262d">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;margin-bottom:4px">
            <span style="color:#58a6ff;font-weight:bold">📚 飆股在線等策略匹配</span>
            <span style="background:${{h.strat_color||"#888"}};color:#000;padding:2px 10px;border-radius:12px;font-weight:bold;font-size:11px">${{h.strat_action||"觀望"}}</span>
            <span style="color:#8b949e;font-size:10px">${{ss.length}} 個訊號</span>
          </div>
          <div style="line-height:1.8">${{badges}}</div>
        </div>`;
      }})()}}
      ${{(() => {{
        const ns = h.news_sentiment;
        if (!ns) return "";
        const c = ns.counts || {{}};
        const evList = (ns.events_dated || ns.events || []).slice(0,6);
        const evIcons = {{strong_pos:"🚀", pos:"🟢", warning:"🟡", neg:"🔴", strong_neg:"📉", neutral:"⚪"}};
        const evHtml = evList.map(e => {{
          if (typeof e === "string") return `<div style="font-size:11px;color:#adbac7;margin:2px 0">${{e}}</div>`;
          const ico = evIcons[e.type] || "";
          return `<div style="font-size:11px;color:#adbac7;margin:2px 0">${{e.text}} <span style="color:#666;font-size:9px">${{e.date||""}}</span></div>`;
        }}).join("");
        return `<div style="padding:10px 14px;background:linear-gradient(180deg,rgba(255,200,80,0.06),rgba(255,165,0,0.02));border-top:1px solid #21262d;border-bottom:1px solid #21262d">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px">
            <span style="font-size:12px;color:#f0c040;font-weight:bold">🗞️ 消息面建議</span>
            <span style="background:${{ns.color}};color:#000;padding:2px 10px;border-radius:12px;font-weight:bold;font-size:11px">${{ns.label}}</span>
            <span style="font-size:10px;color:#8b949e">近 7 天 ${{c.total||0}} 則 — 利多 <b style="color:#3fb950">${{c.positive||0}}</b> ／ 利空 <b style="color:#f85149">${{c.negative||0}}</b> ／ 警示 <b style="color:#f0a500">${{c.warning||0}}</b></span>
          </div>
          <div style="font-size:11px;color:#e6edf3;line-height:1.6;margin-bottom:6px">📰 ${{ns.summary||""}}</div>
          ${{evList.length ? `<div style="margin-bottom:6px">${{evHtml}}</div>` : ""}}
          <div style="font-size:11px;color:#adbac7;line-height:1.6;padding:6px 10px;background:rgba(63,185,80,0.07);border-left:3px solid #56d364;border-radius:3px">${{ns.action||""}}</div>
        </div>`;
      }})()}}
      ${{renderChipBlock(h.holding, h.sid, h.dates)}}
    `;
    grid.appendChild(div);
  }});
  setTimeout(() => items.forEach(h => drawHoldingChart(h, canvasPrefix)), 50);
}}

function drawHoldingChart(h) {{
  const canvas = document.getElementById((arguments[1]||"hc_")+h.sid);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth * dpr;
  const H = 280 * dpr;
  canvas.width = W; canvas.height = H;
  ctx.scale(dpr, dpr);
  const w = W/dpr, ht = H/dpr;
  const PAD = {{top:15, right:10, bottom:18, left:55}};
  const chartH = ht * 0.72;
  const volH   = ht * 0.18;
  const volTop = chartH + 10;
  const n = h.dates.length;
  if (n === 0) return;

  ctx.fillStyle = "#161b22"; ctx.fillRect(0, 0, w, ht);

  // 價格範圍
  const allPrices = h.highs.concat(h.lows);
  const maxP = Math.max(...allPrices);
  const minP = Math.min(...allPrices);
  const padP = (maxP-minP) * 0.08 || 1;
  const yMax = maxP + padP;
  const yMin = Math.max(0, minP - padP);

  const cw = (w - PAD.left - PAD.right) / n * 0.7;
  const px = i => PAD.left + (i + 0.5) * (w - PAD.left - PAD.right) / n;
  const py = p => PAD.top + (yMax - p) / (yMax - yMin) * (chartH - PAD.top);

  // Y 軸標籤
  ctx.fillStyle = "#8b949e"; ctx.font = "10px Arial"; ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {{
    const p = yMax - (yMax - yMin) * i / 4;
    const y = py(p);
    ctx.fillText(p.toFixed(2), PAD.left - 4, y + 3);
    ctx.strokeStyle = "rgba(139,148,158,0.1)"; ctx.beginPath();
    ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
  }}

  // K 棒
  for (let i = 0; i < n; i++) {{
    const isUp = h.closes[i] >= h.opens[i];
    ctx.fillStyle = isUp ? "#ff4444" : "#00aa44";
    ctx.strokeStyle = ctx.fillStyle;
    const x = px(i);
    ctx.beginPath();
    ctx.moveTo(x, py(h.highs[i])); ctx.lineTo(x, py(h.lows[i])); ctx.stroke();
    const top = py(Math.max(h.opens[i], h.closes[i]));
    const bot = py(Math.min(h.opens[i], h.closes[i]));
    ctx.fillRect(x - cw/2, top, cw, Math.max(1, bot - top));
  }}

  // MA5、MA20
  function drawMA(arr, color) {{
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {{
      if (v == null || isNaN(v)) return;
      const x = px(i), y = py(v);
      if (!started) {{ ctx.moveTo(x, y); started = true; }}
      else ctx.lineTo(x, y);
    }});
    ctx.stroke();
  }}
  drawMA(h.ma5, "#f0c040");
  drawMA(h.ma20, "#58a6ff");

  // 型態線（個股）
  if (h.pattern && h.pattern.lines) {{
    h.pattern.lines.forEach(line => {{
      if (line.x1==null || line.y1==null) return;
      ctx.save();
      ctx.strokeStyle = line.color || "#ffd700"; ctx.lineWidth = 1.5;
      if (line.dash) ctx.setLineDash([6,3]);
      ctx.globalAlpha = 0.85; ctx.beginPath();
      ctx.moveTo(px(line.x1), py(line.y1));
      ctx.lineTo(px(line.x2), py(line.y2));
      ctx.stroke(); ctx.setLineDash([]); ctx.restore();
    }});
  }}
  if (h.pattern && h.pattern.marks) {{
    h.pattern.marks.forEach(m => {{
      if (m.x==null || m.y==null) return;
      const mx = px(m.x), my = py(m.y), sz = m.size || 5;
      ctx.fillStyle = m.color || "#ffd700"; ctx.strokeStyle = "rgba(0,0,0,0.7)";
      ctx.beginPath(); ctx.arc(mx, my, sz, 0, Math.PI*2); ctx.fill(); ctx.stroke();
      if (m.text) {{
        ctx.font = "bold 9px Arial"; ctx.textAlign = "center";
        const tw = ctx.measureText(m.text).width;
        const ty = my - sz - 4;
        ctx.fillStyle = "rgba(0,0,0,0.7)";
        ctx.fillRect(mx-tw/2-3, ty-9, tw+6, 12);
        ctx.fillStyle = m.color || "#ffd700"; ctx.fillText(m.text, mx, ty);
      }}
    }});
  }}

  // 成交量
  const maxVol = Math.max(...h.vols);
  const pv = v => volTop + volH - (v / maxVol) * volH;
  for (let i = 0; i < n; i++) {{
    const isUp = h.closes[i] >= h.opens[i];
    ctx.fillStyle = isUp ? "rgba(255,68,68,0.6)" : "rgba(0,170,68,0.6)";
    const x = px(i);
    ctx.fillRect(x - cw/2, pv(h.vols[i]), cw, volTop + volH - pv(h.vols[i]));
  }}

  // 圖例
  ctx.font = "10px Arial"; ctx.textAlign = "left";
  ctx.fillStyle = "#f0c040"; ctx.fillText("MA5", PAD.left + 4, 12);
  ctx.fillStyle = "#58a6ff"; ctx.fillText("MA20", PAD.left + 38, 12);

  // 儲存供 tooltip 使用
  canvas._chartData = h;
  canvas._chartFns  = {{px, py, n, PAD, cw}};
}}

// ── 即將突破 渲染 ────────────────────────────
function renderBreakouts() {{
  const grid = document.getElementById("breakouts-grid");
  if (!grid) return;
  grid.innerHTML = "";
  if (!breakouts || breakouts.length === 0) {{
    grid.innerHTML = `<div style="padding:20px;color:#8b949e;grid-column:1/-1;text-align:center">
      今日無符合條件的候選股（底部型態 + 量能放大 + 接近頸線）
    </div>`;
    return;
  }}
  breakouts.forEach(b => {{
    const div = document.createElement("div");
    div.className = "card";
    const distColor = b.dist_pct >= 0 ? "#3fb950" : "#f0a500";
    const distLabel = b.dist_pct >= 0 ? "已過頸線" : "距頸線";
    const catMap = {{
      "w_bottom": "W底", "triple_bottom": "三重底",
      "head_shoulder_bottom": "頭肩底", "ascending_triangle_bottom": "上升三角"
    }};
    const patShort = catMap[b.pattern_en] || b.pattern_name;
    div.innerHTML = `
      <div class="card-header" style="background:rgba(63,185,80,0.1);border-bottom:2px solid #3fb950">
        <div class="stock-info" style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="stock-code">${{b.sid}}</span>
            <span class="stock-name">${{b.name}}</span>
            <span class="sig-badge" style="background:#3fb950">⚡ ${{patShort}}</span>
            <span style="font-size:11px;background:rgba(240,165,0,0.2);color:#f0a500;padding:2px 8px;border-radius:10px">
              量比 ${{b.vol_ratio}}x
            </span>
          </div>
          <div style="font-size:11px;margin-top:4px">
            <span style="color:#8b949e">現價：</span><span style="color:#fff;font-weight:bold">${{b.current}}</span>
            <span style="color:#8b949e;margin-left:10px">頸線：</span><span style="color:#f0c040;font-weight:bold">${{b.neckline}}</span>
          </div>
        </div>
        ${{(b.rev&&b.rev.news&&b.rev.news.length)?`<div style="flex:1;padding:0 12px;border-left:1px solid #30363d;display:flex;flex-direction:column;justify-content:center;gap:3px;min-width:0;max-width:300px">
          ${{b.rev.news.slice(0,3).map(n=>`<a href="${{n.u||"#"}}" target="_blank" style="display:block;font-size:11px;color:#adbac7;line-height:1.5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-decoration:none" title="${{n.t}}" onmouseover="this.style.color='#58a6ff'" onmouseout="this.style.color='#adbac7'">📰 ${{n.t}}</a>`).join("")}}
        </div>`:""}}
        <div class="score-info">
          <div style="font-size:18px;font-weight:bold;color:${{distColor}}">${{b.dist_pct>=0?'+':''}}${{b.dist_pct}}%</div>
          <div style="font-size:10px;color:#8b949e">${{distLabel}}</div>
          <div style="font-size:10px;color:#8b949e;margin-top:2px">${{b.pattern_name}}</div>
        </div>
      </div>
      <div class="chart-container">
        <canvas id="bc_${{b.sid}}" height="280"></canvas>
      </div>
      ${{(() => {{
        const ss = b.strategies || [];
        if (ss.length === 0) return "";
        const typeColor = {{buy:"#3fb950",sell:"#f85149",warning:"#f0a500",info:"#58a6ff"}};
        const badges = ss.slice(0,10).map(m =>
          `<span title="${{m.signal}}" style="display:inline-block;background:${{typeColor[m.type]||"#888"}}20;color:${{typeColor[m.type]||"#888"}};border:1px solid ${{typeColor[m.type]||"#888"}}40;padding:2px 8px;border-radius:10px;font-size:10px;margin:2px 3px 2px 0;white-space:nowrap">Ep${{m.ep}} ${{m.name}}</span>`
        ).join("");
        return `<div style="padding:8px 14px;background:linear-gradient(90deg,#0d2a3a,#1a3a4a);border-top:1px solid #21262d">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;margin-bottom:4px">
            <span style="color:#58a6ff;font-weight:bold">📚 飆股在線等策略匹配</span>
            <span style="background:${{b.strat_color||"#888"}};color:#000;padding:2px 10px;border-radius:12px;font-weight:bold;font-size:11px">${{b.strat_action||"觀望"}}</span>
            <span style="color:#8b949e;font-size:10px">${{ss.length}} 個訊號</span>
          </div>
          <div style="line-height:1.8">${{badges}}</div>
        </div>`;
      }})()}}
      ${{renderChipBlock(b.holding, b.sid, b.dates)}}
    `;
    grid.appendChild(div);
  }});
  setTimeout(() => breakouts.forEach(b => drawBreakoutChart(b)), 50);
}}

function drawBreakoutChart(b) {{
  const canvas = document.getElementById("bc_"+b.sid);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.offsetWidth * dpr;
  const H = 280 * dpr;
  canvas.width = W; canvas.height = H;
  ctx.scale(dpr, dpr);
  const w = W/dpr, ht = H/dpr;
  const PAD = {{top:15, right:10, bottom:18, left:55}};
  const chartH = ht * 0.72;
  const volH   = ht * 0.18;
  const volTop = chartH + 10;
  const n = b.dates.length;
  if (n === 0) return;

  ctx.fillStyle = "#161b22"; ctx.fillRect(0, 0, w, ht);

  const allPrices = b.highs.concat(b.lows).concat([b.neckline]);
  const maxP = Math.max(...allPrices);
  const minP = Math.min(...allPrices);
  const padP = (maxP-minP) * 0.08 || 1;
  const yMax = maxP + padP;
  const yMin = Math.max(0, minP - padP);

  const cw = (w - PAD.left - PAD.right) / n * 0.7;
  const px = i => PAD.left + (i + 0.5) * (w - PAD.left - PAD.right) / n;
  const py = p => PAD.top + (yMax - p) / (yMax - yMin) * (chartH - PAD.top);

  // Y 軸
  ctx.fillStyle = "#8b949e"; ctx.font = "10px Arial"; ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {{
    const p = yMax - (yMax - yMin) * i / 4;
    const y = py(p);
    ctx.fillText(p.toFixed(2), PAD.left - 4, y + 3);
    ctx.strokeStyle = "rgba(139,148,158,0.1)"; ctx.beginPath();
    ctx.moveTo(PAD.left, y); ctx.lineTo(w - PAD.right, y); ctx.stroke();
  }}

  // K 棒
  for (let i = 0; i < n; i++) {{
    const isUp = b.closes[i] >= b.opens[i];
    ctx.fillStyle = isUp ? "#ff4444" : "#00aa44";
    ctx.strokeStyle = ctx.fillStyle;
    const x = px(i);
    ctx.beginPath();
    ctx.moveTo(x, py(b.highs[i])); ctx.lineTo(x, py(b.lows[i])); ctx.stroke();
    const top = py(Math.max(b.opens[i], b.closes[i]));
    const bot = py(Math.min(b.opens[i], b.closes[i]));
    ctx.fillRect(x - cw/2, top, cw, Math.max(1, bot - top));
  }}

  // MA
  function drawMA(arr, color) {{
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {{
      if (v == null || isNaN(v)) return;
      const x = px(i), y = py(v);
      if (!started) {{ ctx.moveTo(x, y); started = true; }}
      else ctx.lineTo(x, y);
    }});
    ctx.stroke();
  }}
  drawMA(b.ma5, "#f0c040");
  drawMA(b.ma20, "#58a6ff");

  // 頸線（高亮）
  const yNeck = py(b.neckline);
  ctx.save();
  ctx.strokeStyle = "#3fb950"; ctx.lineWidth = 2; ctx.setLineDash([6,3]);
  ctx.globalAlpha = 0.95;
  ctx.beginPath();
  ctx.moveTo(PAD.left, yNeck); ctx.lineTo(w - PAD.right, yNeck);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#3fb950"; ctx.font = "bold 10px Arial"; ctx.textAlign = "right";
  ctx.fillText("頸線 " + b.neckline, w - PAD.right - 4, yNeck - 3);
  ctx.restore();

  // 型態其他線（已含 offset 對齊）
  (b.lines || []).forEach(line => {{
    if (line.x1==null || line.y1==null) return;
    if ((line.label||"").indexOf("頸線") >= 0) return;  // 頸線已單獨畫
    ctx.save();
    ctx.strokeStyle = line.color || "#ffd700"; ctx.lineWidth = 1.2;
    if (line.dash) ctx.setLineDash([4,3]);
    ctx.globalAlpha = 0.7; ctx.beginPath();
    ctx.moveTo(px(line.x1), py(line.y1));
    ctx.lineTo(px(line.x2), py(line.y2));
    ctx.stroke(); ctx.setLineDash([]); ctx.restore();
  }});
  (b.marks || []).forEach(m => {{
    if (m.x==null || m.y==null) return;
    const mx = px(m.x), my = py(m.y), sz = m.size || 4;
    ctx.fillStyle = m.color || "#ffd700"; ctx.strokeStyle = "rgba(0,0,0,0.7)";
    ctx.beginPath(); ctx.arc(mx, my, sz, 0, Math.PI*2); ctx.fill(); ctx.stroke();
  }});

  // 成交量
  const maxVol = Math.max(...b.vols);
  const pv = v => volTop + volH - (v / maxVol) * volH;
  for (let i = 0; i < n; i++) {{
    const isUp = b.closes[i] >= b.opens[i];
    ctx.fillStyle = isUp ? "rgba(255,68,68,0.6)" : "rgba(0,170,68,0.6)";
    const x = px(i);
    ctx.fillRect(x - cw/2, pv(b.vols[i]), cw, volTop + volH - pv(b.vols[i]));
  }}
  // 高亮今日量（量比 > 2）
  const lastIdx = n - 1;
  ctx.fillStyle = "rgba(240,165,0,0.85)";
  ctx.fillRect(px(lastIdx) - cw/2, pv(b.vols[lastIdx]), cw, volTop + volH - pv(b.vols[lastIdx]));

  // 圖例
  ctx.font = "10px Arial"; ctx.textAlign = "left";
  ctx.fillStyle = "#f0c040"; ctx.fillText("MA5", PAD.left + 4, 12);
  ctx.fillStyle = "#58a6ff"; ctx.fillText("MA20", PAD.left + 38, 12);
  ctx.fillStyle = "#3fb950"; ctx.fillText("頸線", PAD.left + 78, 12);

  // 儲存供 tooltip 使用
  canvas._chartData = b;
  canvas._chartFns  = {{px, py, n, PAD, cw}};
}}

// Init
renderCards();
renderBreakouts();
renderHoldings(flashPicks, "flash-grid", "fc_");
renderHoldings(pullbacks, "pullbacks-grid", "pc_");
renderHoldings();
renderHoldings(mergerPicks, "merger-grid", "mc_");
</script>
</body>
</html>"""
    return html

def main():
    print("="*55)
    print("  台股飆股 K 線圖 HTML 產生器")
    print("  執行時間："+datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("="*55)

    results_df = get_today_results()
    if results_df.empty:
        print("❌ 找不到今日選股結果，請先執行 taiwan_stock_screener_v3.py")
        return
    print(f"✅ 讀取選股結果：{len(results_df)} 支")

    pc = load_stock_data()
    print(f"✅ 讀取快取：{len(pc)} 支")
    tdcc = fetch_tdcc_all()
    print(f"✅ 集保持股資料：{len(tdcc)} 支")
    stock_meta = load_stock_meta()
    sids_list = results_df["股票代碼"].astype(str).tolist()

    # 自動補充缺少的股票基本資料
    missing = [s for s in sids_list if s not in stock_meta]
    if missing:
        print(f"  補充基本資料：{len(missing)} 支...")
        try:
            import requests, urllib3
            urllib3.disable_warnings()
            sess = requests.Session(); sess.verify=False
            sess.headers.update({"User-Agent":"Mozilla/5.0"})
            # 從證交所抓上市公司基本資料
            r = sess.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=8)
            twse_list = {item["公司代號"]: item for item in r.json() if item.get("公司代號")}
            # 從櫃買抓上櫃公司基本資料
            r2 = sess.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=8)
            tpex_list = {str(item.get("SecuritiesCompanyCode","")).strip(): item for item in r2.json()}
            # 產業別代碼對照表
            industry_map = {
                "01":"水泥工業","02":"食品工業","03":"塑膠工業","04":"紡織纖維",
                "05":"電機機械","06":"電器電纜","07":"化學生技醫療","08":"玻璃陶瓷",
                "09":"造紙工業","10":"鋼鐵工業","11":"橡膠工業","12":"汽車工業",
                "13":"建材營造","14":"航運業","15":"觀光事業","16":"金融保險",
                "17":"貿易百貨","18":"綜合","19":"其他","20":"文化創意",
                "21":"農業科技","22":"電子商務","23":"觀光餐旅","24":"網路",
                "25":"其他電子","26":"半導體","27":"電腦周邊","28":"光電業",
                "29":"通信網路","30":"電子零組件","31":"電子通路","32":"資訊服務",
                "33":"其他電子業","80":"管理股票"
            }
            for sid in missing:
                if sid in twse_list:
                    item = twse_list[sid]
                    ind_code = str(item.get("產業別","—")).strip()
                    industry = industry_map.get(ind_code, ind_code)
                    stock_meta[sid] = {
                        "industry": industry,
                        "product": item.get("公司簡稱","—"),
                        "biz": item.get("公司名稱","—")[:30]
                    }
                elif sid in tpex_list:
                    item = tpex_list[sid]
                    stock_meta[sid] = {
                        "industry": item.get("Industry","—"),
                        "product": item.get("CompanyAbbreviation","—"),
                        "biz": item.get("CompanyAbbreviation","—")
                    }
            # 存回快取
            with open(_META_FILE,"w",encoding="utf-8") as f:
                json.dump(stock_meta, f, ensure_ascii=False)
            print(f"  補充完成：{len(stock_meta)} 支")
        except Exception as e:
            print(f"  補充失敗：{e}")

    # 載入代碼→名稱字典（給 Google News 加強查詢精準度）
    name_map  = load_stock_names()
    # ★ 過濾掉空字串名稱（上櫃股在 stock_list_cache 內名稱常為 ''）
    name_map = {k: v for k, v in name_map.items() if v and str(v).strip()}
    # 持股名稱也要納入 name_map
    for t in MY_HOLDINGS:
        if t[0] not in name_map:
            name_map[t[0]] = t[1]
    # ★ 從 stock_industry.json 補齊（含被空字串占位的）
    try:
        from sector_analyzer import fetch_all_industries as _fai
        _ind = _fai()
        added = 0
        for _sid, _info in _ind.items():
            existing = name_map.get(_sid, "")
            if (not existing or not str(existing).strip()) and _info.get("name"):
                name_map[_sid] = _info["name"]; added += 1
        print(f"  📋 name_map 從 industries 補名：補 {added} 支（總 {len(name_map)} 支）")
    except Exception as _e:
        print(f"  ⚠️ industries 名稱 fallback 失敗：{_e}")

    rev_data = fetch_revenue_all(sids_list, name_map=name_map)
    print(f"✅ 股票基本資料：{len(stock_meta)} 支，月營收：{len(rev_data)} 支")

    # 把「我的持股」 + 飆股前 30 支一起補 TDCC 6 週歷史
    holding_sids = [t[0] for t in MY_HOLDINGS if not t[4]]   # 排除 ETF
    tdcc_targets = list(dict.fromkeys(holding_sids + sids_list[:30]))
    print(f"  TDCC 歷史補抓範圍：{len(tdcc_targets)} 支（含 {len(holding_sids)} 支持股）")
    fetch_tdcc_history_from_web(tdcc_targets)

    # 即將突破候選股（先建構，取得 sid 後再補抓新聞）
    breakouts = build_breakout_data(pc, name_map=name_map, tdcc=tdcc)

    # 突破候選股若不在飆股 30 名內，也補抓 TDCC 歷史
    breakout_sids = [b["sid"] for b in breakouts if b["sid"] not in tdcc_targets]
    if breakout_sids:
        print(f"  TDCC 補抓突破候選：{len(breakout_sids)} 支")
        fetch_tdcc_history_from_web(breakout_sids)

    # 建構我的持股分析資料
    holdings, holdings_summary = build_holdings_data(pc, tdcc)

    # 為持股 + 即將突破補抓月營收/新聞（fetch_revenue_all 內建 24 小時快取）
    extra_sids = [h["sid"] for h in holdings if not h.get("is_etf")]
    extra_sids += [b["sid"] for b in breakouts]
    extra_sids = [s for s in set(extra_sids) if s not in rev_data]
    if extra_sids:
        print(f"  補抓持股 + 突破候選的營收/新聞：{len(extra_sids)} 支")
        extra_rev = fetch_revenue_all(extra_sids, name_map=name_map)
        rev_data.update(extra_rev)

    # 把 rev_data 注入到 holdings / breakouts 中（給 HTML 用）
    for h in holdings:
        h["rev"] = rev_data.get(h["sid"], {}) if not h.get("is_etf") else {}
    for b in breakouts:
        b["rev"] = rev_data.get(b["sid"], {})

    # 注入 rev 後再做消息面分析（commentary 在 build 時 rev 還沒設，所以這裡補做）
    try:
        from news_sentiment import analyze_news_sentiment
        for h in holdings:
            news_list = (h.get("rev") or {}).get("news") or []
            h["news_sentiment"] = analyze_news_sentiment(news_list) if news_list else None
        for b in breakouts:
            news_list = (b.get("rev") or {}).get("news") or []
            b["news_sentiment"] = analyze_news_sentiment(news_list) if news_list else None
    except Exception as _e:
        print(f"  ⚠️ 消息面分析失敗：{_e}")

    # 🤝 併購/收購相關股票（新聞掃描）
    merger_picks = build_merger_picks(pc, rev_data, tdcc=tdcc, name_map=name_map, max_picks=30)

    # 🎯 拉回月線買點選股（排除我的持股 + 突破候選，避免重複）
    holding_sid_set = {h["sid"] for h in holdings}
    breakout_sid_set = {b["sid"] for b in breakouts}
    pullbacks = build_pullback_data(
        pc, tdcc=tdcc, rev_data=rev_data, name_map=name_map,
        exclude_sids=holding_sid_set | breakout_sid_set, max_picks=10,
    )
    # 拉回候選若新聞沒在 rev_data → 補抓
    new_sids = [p["sid"] for p in pullbacks if p["sid"] not in rev_data]
    if new_sids:
        print(f"  補抓拉回候選新聞：{len(new_sids)} 支")
        extra2 = fetch_revenue_all(new_sids, name_map=name_map)
        rev_data.update(extra2)
        for p in pullbacks:
            if not p.get("rev"):
                p["rev"] = rev_data.get(p["sid"], {})
        try:
            from news_sentiment import analyze_news_sentiment
            for p in pullbacks:
                news_list = (p.get("rev") or {}).get("news") or []
                p["news_sentiment"] = analyze_news_sentiment(news_list) if news_list else p.get("news_sentiment")
        except Exception:
            pass

    # 🌟 V42 飆股掃描（全市場）
    # 載入大盤指數給 RS20 計算用
    idf_for_flash = None
    try:
        import pickle as _pkl
        _idx_path = os.path.join(CACHE_DIR, "index_data.pkl")
        if os.path.exists(_idx_path):
            with open(_idx_path, "rb") as _f:
                _idx_raw = _pkl.load(_f)
            idf_for_flash = next(iter(_idx_raw.values())) if isinstance(_idx_raw, dict) else _idx_raw
    except Exception as _e:
        print(f"  ⚠️ 載入指數失敗：{_e}")

    flash_picks = build_flash_picks(
        pc, tdcc=tdcc, rev_data=rev_data,
        name_map=name_map, idf=idf_for_flash, max_picks=30,
    )

    # 🏭 子族群輪動分析（30+ 細分子族群 = 資金實際輪動單位）
    sector_ranking = []
    try:
        from sector_analyzer import (
            fetch_all_industries, compute_subsector_strength, sector_filter_v42
        )
        print("🏭 子族群輪動分析...")
        industries = fetch_all_industries()
        sector_ranking = compute_subsector_strength(pc, industries,
                                                     cutoff_date=datetime.today().strftime("%Y-%m-%d"))
        print(f"  🚀 Top 5 強勢子族群：")
        for s in sector_ranking[:5]:
            print(f"    #{s['rank']} {s['icon']} {s['alias']}：20日 {s['median_ret_20d']:+.2f}% / 5日 {s['median_ret_5d']:+.2f}% {s['rotation']}")
        print(f"  🔻 Bottom 3 弱勢子族群：")
        for s in sector_ranking[-3:]:
            print(f"    #{s['rank']} {s['icon']} {s['alias']}：20日 {s['median_ret_20d']:+.2f}%")

        flash_picks = sector_filter_v42(flash_picks, industries, sector_ranking)
        for h in flash_picks:
            mem_text = h.get('memberships_text','')
            print(f"  {h.get('quality_tier','?')} {h['sid']} {h['name']} 最佳→ {h.get('subsector_icon','')}{h.get('subsector_alias','?')} (#{h.get('subsector_rank')}/{len(sector_ranking)})")
            if mem_text and '/' in mem_text:
                print(f"           多重歸屬：{mem_text}")
    except Exception as _e:
        print(f"  ⚠️ 子族群分析失敗：{_e}")
        import traceback as _tb; _tb.print_exc()

    # 推送 V42 命中 → Telegram
    if flash_picks:
        try:
            from telegram_helpers import _esc as _telegram_esc
            esc = _telegram_esc
        except Exception:
            def esc(s): return str(s).replace("<","&lt;").replace(">","&gt;").replace("&","&amp;")
        try:
            tg_tok = os.environ.get("STOCK_TG_TOKEN", "")
            tg_chat = os.environ.get("STOCK_TG_CHAT", "")
            # Fallback：如果環境變數沒有，從 stock_agent.py 抓 hardcoded
            if not tg_tok or not tg_chat:
                try:
                    _agent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_agent.py")
                    if os.path.exists(_agent_path):
                        import re as _re
                        _txt = open(_agent_path, encoding="utf-8").read()
                        if not tg_tok:
                            _m = _re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', _txt)
                            if _m: tg_tok = _m.group(1)
                        if not tg_chat:
                            _m = _re.search(r'TG_CHAT\s*=\s*"([^"]+)"', _txt)
                            if _m: tg_chat = _m.group(1)
                except Exception:
                    pass
            if tg_tok and tg_chat:
                import requests as _req
                today_dt = datetime.today().strftime('%Y-%m-%d')

                # 大盤資金判決
                aaa_count = sum(1 for h in flash_picks if h.get("quality_tier") == "AAA")
                aa_count = sum(1 for h in flash_picks if h.get("quality_tier") == "AA")
                aaa_aa_total = aaa_count + aa_count
                total_v42_tg = len(flash_picks)
                if aaa_aa_total >= 3:
                    verdict_tg = "🟢 <b>進場機會多</b>"
                    verdict_note_tg = f"高品質 V42 共 {aaa_aa_total} 支，可分散買進"
                elif aaa_aa_total >= 1:
                    verdict_tg = "🟡 <b>部分機會</b>"
                    verdict_note_tg = f"僅 {aaa_aa_total} 支高品質，謹慎挑選"
                elif total_v42_tg >= 5:
                    verdict_tg = "🔴 <b>警告：族群資金撤離</b>"
                    verdict_note_tg = f"{total_v42_tg} 支 V42 但全部在弱勢族群 → 個股形態好可能是假突破，建議空手或極輕倉"
                else:
                    verdict_tg = "⚪ <b>無明顯訊號</b>"
                    verdict_note_tg = "多看少動"

                lines = [f"🌟 <b>V42 + 族群輪動 雙過濾</b>（{today_dt}）",
                         f"📋 {verdict_tg}",
                         f"<i>{verdict_note_tg}</i>",
                         ""]

                # 1) 子族群輪動排行
                if sector_ranking:
                    lines.append("📊 <b>子族群輪動 Top 8</b>（資金實際停泊單位）")
                    for s in sector_ranking[:8]:
                        tag = "🚀" if s["is_top5"] else "  "
                        lines.append(
                            f"  {tag} {esc(s['icon'])} {esc(s['alias'])}：20日 {s['median_ret_20d']:+.2f}% / 5日 {s['median_ret_5d']:+.2f}% {esc(s['rotation'])}"
                        )
                    lines.append("")

                # 2) V42 命中（按品質分組）
                aaa = [h for h in flash_picks if h.get("quality_tier") == "AAA"]
                aa = [h for h in flash_picks if h.get("quality_tier") == "AA"]
                a = [h for h in flash_picks if h.get("quality_tier") == "A"]
                b = [h for h in flash_picks if h.get("quality_tier") == "B"]
                lines.append(f"🌟 <b>V42 命中 {len(flash_picks)} 支</b>（AAA {len(aaa)}/AA {len(aa)}/A {len(a)}/B {len(b)}）")

                for group_name, group in [
                    ("🏆 AAA 級（Top 5 子族群+資金加速+V42）", aaa),
                    ("🥇 AA 級（Top 5 強勢子族群中的 V42）", aa),
                    ("🥈 A 級（Top 10 中強子族群）", a),
                    ("⚠️ B 級（弱勢族群中的 V42，謹慎）", b),
                ]:
                    if not group: continue
                    lines.append("")
                    lines.append(f"<b>{group_name}</b>")
                    for c in group[:8]:
                        m = c.get("flash_metrics", {})
                        mem_text = c.get("memberships_text","")
                        line = (
                            f"• <b>{esc(c['sid'])} {esc(c['name'])}</b>  {esc(c.get('subsector_icon',''))}{esc(c.get('subsector_alias','?'))}"
                            f"\n   現價 {c['current']}｜月斜 {m.get('ma20_slope_pct'):+.1f}%"
                            f" / RS {m.get('rs20_pct'):+.1f}% / 量比 {m.get('vol_burst')}x"
                            f"\n   最佳族群 #{c.get('subsector_rank')}/{len(sector_ranking)}，"
                            f"20日 {c.get('subsector_ret20'):+.1f}% / 5日 {c.get('subsector_ret5'):+.1f}%"
                        )
                        # 多重歸屬時加上「也屬於」
                        if mem_text and "/" in mem_text:
                            line += f"\n   <i>也屬於：{esc(mem_text)}</i>"
                        lines.append(line)
                msg = "\n".join(lines)
                _r = _req.post(f"https://api.telegram.org/bot{tg_tok}/sendMessage",
                               json={"chat_id": tg_chat, "text": msg, "parse_mode": "HTML"},
                               timeout=15)
                print(f"  📨 Telegram V42 推送：{_r.status_code}")
            else:
                print(f"  💡 Telegram 未設定（環境變數 STOCK_TG_TOKEN/CHAT 未提供），跳過推送")
        except Exception as _e:
            print(f"  ⚠️ Telegram 推送失敗：{_e}")

    html = generate_html(results_df, pc, tdcc, stock_meta, rev_data,
                         holdings=holdings, holdings_summary=holdings_summary,
                         breakouts=breakouts, pullbacks=pullbacks,
                         flash_picks=flash_picks, sector_ranking=sector_ranking,
                         merger_picks=merger_picks)

    ds = datetime.today().strftime("%Y%m%d")
    output_path = os.path.join(OUTPUT_DIR, f"飆股圖表_{ds}.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ HTML 報表已產生：{output_path}")
    print("   用瀏覽器開啟即可查看所有 K 線圖！")

    # ★ Telegram attach HTML 檔案（iPhone 可直接點開）
    try:
        tg_tok_doc = os.environ.get("STOCK_TG_TOKEN", "")
        tg_chat_doc = os.environ.get("STOCK_TG_CHAT", "")
        if not tg_tok_doc or not tg_chat_doc:
            try:
                import re as _re_d
                _ap = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_agent.py")
                _txt = open(_ap, encoding="utf-8").read()
                if not tg_tok_doc:
                    _m = _re_d.search(r'TG_TOKEN\s*=\s*"([^"]+)"', _txt)
                    if _m: tg_tok_doc = _m.group(1)
                if not tg_chat_doc:
                    _m = _re_d.search(r'TG_CHAT\s*=\s*"([^"]+)"', _txt)
                    if _m: tg_chat_doc = _m.group(1)
            except Exception:
                pass
        if tg_tok_doc and tg_chat_doc:
            import requests as _req_d
            file_size_mb = os.path.getsize(output_path) / (1024*1024)
            n_flash = len(flash_picks or [])
            top_sect = (sector_ranking[0]["alias"] if sector_ranking else "—")
            caption = (
                f"📊 <b>飆股圖表 {ds[:4]}-{ds[4:6]}-{ds[6:8]}</b>\n"
                f"💎 V42 命中：{n_flash} 支\n"
                f"🚀 強勢 Top 1：{top_sect}\n"
                f"📱 iPhone 點此檔案 → 選 Safari 開啟\n"
                f"💡 建議「加入主畫面」變 app icon"
            )
            with open(output_path, "rb") as fp:
                r_doc = _req_d.post(
                    f"https://api.telegram.org/bot{tg_tok_doc}/sendDocument",
                    data={
                        "chat_id": tg_chat_doc,
                        "caption": caption,
                        "parse_mode": "HTML",
                    },
                    files={
                        "document": (f"飆股圖表_{ds}.html", fp, "text/html"),
                    },
                    timeout=60,
                )
            print(f"  📎 Telegram 檔案推送：{r_doc.status_code}（檔案 {file_size_mb:.1f} MB）")
    except Exception as _e:
        print(f"  ⚠️ Telegram 檔案推送失敗：{_e}")

if __name__ == "__main__":
    main()
