"""
併購新聞反向掃描器（覆蓋全市場 M&A）
==========================================
現況：stock_news.json 只覆蓋 219/1966 股，中小型併購漏抓
新法：從 Google News 反向搜「併購/收購」→ 找到 title → 提股號 → 補進 news cache

Query 集合：
  - "台股 併購 2026"
  - "上市 收購案"
  - "上市公司 併購"
  - "股權 收購"
  - "M&A 台灣"
  - etc.

執行方式：
  python3 merger_news_scanner.py           # 標準模式
  python3 merger_news_scanner.py --days 7  # 只保留 7 天內
"""
import os, sys, re, json, time, argparse
import urllib.parse, urllib.request
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

BASE = os.path.expanduser("~/Desktop/Stock Selection Strategy")
CACHE = os.path.join(BASE, "cache")
NEWS_CACHE = os.path.join(CACHE, "stock_news.json")
MA_CACHE   = os.path.join(CACHE, "merger_news.json")

# 主動搜這些 query
QUERIES = [
    "台股 併購",
    "上市公司 收購",
    "上櫃 併購案",
    "股權 收購 台灣",
    "現金合併 上市",
    "換股合併 上市",
    "公開收購 上市",
    "台灣 M&A",
    "取得控制權 上市",
    "私有化 台灣",
    "海外併購 台灣",
    "策略入股 上市",
]

MA_KEYWORDS = [
    "收購", "併購", "合併案", "併購案", "整併", "借殼", "私有化",
    "取得股權", "取得控制權", "取得100%", "取得經營權",
    "公開收購", "現金合併", "換股合併", "股份轉換",
    "策略入股", "增資入股", "海外併購", "海外收購",
    "M&A", "merger", "acquisition", "buyout", "takeover",
]

EXCLUDE_KEYWORDS = ["非收購", "否認收購", "拒絕收購", "無收購計畫",
                    "非併購", "非合併", "營收合併"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def google_news(query, max_items=50):
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            xml = r.read().decode()
    except Exception as e:
        log(f"  ⚠️ query fail «{query}»: {e}")
        return []
    root = ET.fromstring(xml)
    items = []
    for it in root.findall(".//item")[:max_items]:
        title = (it.find("title").text or "").strip()
        pub   = (it.find("pubDate").text or "").strip()
        link  = (it.find("link").text or "").strip()
        items.append({"t": title, "u": link, "d": pub})
    return items


MONTH_ABBR = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
              "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}

def parse_pub(pub):
    try:
        parts = pub.split()
        d = int(parts[1]); m = MONTH_ABBR.get(parts[2], 0); y = int(parts[3])
        return datetime(y, m, d)
    except: return None


def extract_sids(title, name_map):
    """從標題抽股號 — 找 (\\d{4}) 或已知股名"""
    sids = set()
    # 1. 找 4 位數字（前後不接更多數字）
    for m in re.finditer(r'(?<!\d)(\d{4})(?!\d)', title):
        sid = m.group(1)
        if sid in name_map:
            sids.add(sid)
    # 2. 找 5 位含 KY / L / A 等（如 00981A、5347L）
    for m in re.finditer(r'(00\d{3}[A-Z]?|\d{4}-?KY?)', title):
        sid = m.group(1).replace("-", "")
        if sid in name_map:
            sids.add(sid)
    # 3. 從 name_map 反向找股名（過濾常見雙字誤中）
    for sid, name in name_map.items():
        name = str(name or "").strip()
        if not name or len(name) < 2: continue
        if len(name) == 2:
            # 兩字名要嚴格 — 前後要有邊界（避免 "台泥" 誤中「台灣水泥」）
            if re.search(rf"[^\w]{re.escape(name)}[^\w]", " " + title + " "):
                sids.add(sid)
        elif name in title:
            sids.add(sid)
    return sids


def load_name_map():
    """從 stock_list_cache + fetch_all_industries 拼出 name_map"""
    name_map = {}
    p = os.path.join(CACHE, "stock_list_cache.json")
    if os.path.exists(p):
        try:
            data = json.load(open(p))
            for sid, info in data.items():
                if isinstance(info, dict) and info.get("name"):
                    name_map[sid] = info["name"]
                elif isinstance(info, str):
                    name_map[sid] = info
        except: pass
    # 也從 sector_analyzer 補齊
    try:
        sys.path.insert(0, BASE)
        from sector_analyzer import fetch_all_industries
        for sid, info in (fetch_all_industries() or {}).items():
            if info.get("name") and sid not in name_map:
                name_map[sid] = info["name"]
    except Exception as e:
        log(f"  ⚠️ industries load fail: {e}")
    # 也從 price_data.pkl 補股號存在性（有股就 keep）
    import pickle
    try:
        pc = pickle.load(open(os.path.join(CACHE, "price_data.pkl"), "rb"))
        for sid in pc.keys():
            if sid not in name_map:
                name_map[sid] = ""
    except: pass
    return name_map


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="保留最近 N 天內的新聞")
    args = ap.parse_args()

    log("🔍 併購新聞反向掃描器啟動")
    name_map = load_name_map()
    log(f"  📋 name_map {len(name_map)} 支股")

    cutoff = datetime.now() - timedelta(days=args.days)
    seen_urls = set()
    all_hits = []   # (sid, title, url, keyword, pub_str)

    for q in QUERIES:
        log(f"🔎 query: {q}")
        items = google_news(q)
        log(f"    抓到 {len(items)} 則")
        for it in items:
            title = it["t"]
            url = it["u"]
            if url in seen_urls: continue
            seen_urls.add(url)
            # 日期過濾
            dt = parse_pub(it["d"])
            if dt and dt < cutoff: continue
            # 排除 false positive
            if any(ex in title for ex in EXCLUDE_KEYWORDS): continue
            # 關鍵字命中
            matched_kw = next((kw for kw in MA_KEYWORDS if kw in title), None)
            if not matched_kw: continue
            # 抽股號
            sids = extract_sids(title, name_map)
            for sid in sids:
                all_hits.append({
                    "sid": sid, "title": title[:200], "url": url,
                    "keyword": matched_kw,
                    "date": dt.strftime("%Y-%m-%d") if dt else it["d"][:16],
                })
        time.sleep(1.5)

    # 去重 by (sid, url)
    unique = {}
    for h in all_hits:
        key = (h["sid"], h["url"])
        if key not in unique:
            unique[key] = h
    hits = list(unique.values())
    log(f"\n✅ 總命中 {len(hits)} 則（{len(set(h['sid'] for h in hits))} 支股）")

    # 依股分組印出
    by_sid = {}
    for h in hits:
        by_sid.setdefault(h["sid"], []).append(h)
    for sid, lst in sorted(by_sid.items(), key=lambda x: -len(x[1]))[:20]:
        name = name_map.get(sid, "")
        log(f"  {sid} {name}: {len(lst)} 則")
        for h in lst[:2]:
            log(f"    「{h['keyword']}」{h['title'][:60]}")

    # 存 merger_news.json（單獨用，不動 stock_news.json）
    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hits_count": len(hits),
        "stocks_count": len(by_sid),
        "by_sid": {sid: hits_for_sid for sid, hits_for_sid in by_sid.items()},
    }
    with open(MA_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"💾 已存 {MA_CACHE}")

    # 同時「補進」stock_news.json（讓 generate_chart 的 build_merger_picks 能用）
    try:
        existing = {}
        if os.path.exists(NEWS_CACHE):
            existing = json.load(open(NEWS_CACHE))
        for sid, lst in by_sid.items():
            merged = existing.get(sid, []) or []
            urls_seen = {n.get("u","") for n in merged if isinstance(n, dict)}
            for h in lst:
                if h["url"] not in urls_seen:
                    merged.insert(0, {"t": h["title"], "u": h["url"], "d": h["date"]})
                    urls_seen.add(h["url"])
            existing[sid] = merged[:15]  # 每股保留最新 15 則
        with open(NEWS_CACHE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        log(f"💾 已補入 {NEWS_CACHE}（{len(by_sid)} 支）")
    except Exception as e:
        log(f"⚠️ 補 news cache fail: {e}")


if __name__ == "__main__":
    main()
