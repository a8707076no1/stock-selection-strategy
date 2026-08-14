"""
持股 7 月目標價自動抓取（Google News）
==========================================
針對每支持股搜「<sid> <name> 目標價」→ 過濾指定月份 → regex 抽券商 + 目標價
產出 cache/analyst_targets_monthly.json：
  {
    "2330": {
       "name": "台積電",
       "month": "2026-07",
       "targets": [
         {"date": "2026-07-17", "broker": "大摩", "price": 2888, "title": "...", "url": "..."},
         ...
       ]
    }, ...
  }
"""
import os, sys, re, json, time, urllib.parse, urllib.request
from datetime import datetime
from xml.etree import ElementTree as ET

BASE = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
CACHE = os.path.join(BASE, "cache", "analyst_targets_monthly.json")

BROKER_PATTERNS = [
    # 順序：長字串先匹配，避免「小摩」被「摩」吃掉
    ("摩根士丹利", "大摩"), ("大摩", "大摩"),
    ("摩根大通", "小摩"), ("小摩", "小摩"),
    ("高盛", "高盛"),
    ("瑞銀", "瑞銀"), ("瑞穗", "瑞穗"), ("瑞信", "瑞信"),
    ("里昂", "里昂"), ("野村", "野村"), ("大和", "大和"),
    ("麥格理", "麥格理"), ("花旗", "花旗"),
    ("巴克萊", "巴克萊"), ("美銀美林", "美銀"), ("美林", "美銀"),
    ("摩根", "大摩"),  # 泛「摩根」通常指大摩
    ("Factset", "Factset"), ("factset", "Factset"),
    ("富邦", "富邦投顧"), ("元大", "元大投顧"),
    ("凱基", "凱基投顧"), ("群益", "群益投顧"),
    ("統一", "統一投顧"), ("大華", "大華投顧"),
    ("永豐", "永豐投顧"),
]

# 目標價 regex — 「目標價 XXX 元」「上看 XXX 元」「喊 XXX 元」「喊到 XXX」「調升...XXXX元」
PRICE_RES = [
    re.compile(r"目標價[^0-9]{0,8}([\d,]{2,6})\s*元?"),
    re.compile(r"上看\s*([\d,]{2,6})\s*元"),
    re.compile(r"喊(?:到|至|買|上|升|進)?\s*([\d,]{2,6})\s*元"),
    re.compile(r"上修至?\s*([\d,]{2,6})\s*元"),
    re.compile(r"上調至?\s*([\d,]{2,6})\s*元"),
    re.compile(r"調升(?:至|到)?\s*([\d,]{2,6})\s*元"),
    re.compile(r"至\s*([\d,]{2,6})\s*(?:新台幣|元)"),
]

MONTH_ABBR = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
              "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}


def parse_pub_date(pub):
    """"Fri, 17 Jul 2026 08:30:00 GMT" → datetime"""
    try:
        parts = pub.split()
        d = int(parts[1]); mo = MONTH_ABBR.get(parts[2], 0); y = int(parts[3])
        return datetime(y, mo, d)
    except Exception:
        return None


def detect_broker(title):
    """回傳第一個匹配到的券商名（規範化）"""
    for pat, canon in BROKER_PATTERNS:
        if pat in title:
            return canon
    # 泛稱兜底
    if "外資" in title: return "外資（未指名）"
    if "投信" in title: return "投信（未指名）"
    if "券商" in title or "法人" in title or "研究" in title:
        return "券商（未指名）"
    # 只要標題含明確目標價數字就給「未指名」而不是丟掉
    if "目標價" in title or "上看" in title or "喊" in title or "調升" in title:
        return "未指名"
    return None


def detect_price(title):
    """從標題抽目標價（含價格合理性 check：50~99999）"""
    for pat in PRICE_RES:
        for m in pat.finditer(title):
            try:
                p = int(m.group(1).replace(",", ""))
                if 20 <= p <= 99999:
                    return p
            except: pass
    return None


def search_google_news(sid, name):
    q = urllib.parse.quote(f"{sid} {name} 目標價")
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            xml = r.read().decode()
    except Exception as e:
        print(f"  ⚠️ Google News 失敗 {sid}: {e}")
        return []
    root = ET.fromstring(xml)
    return root.findall(".//item")


def collect_month_targets(sid, name, year, month):
    items = search_google_news(sid, name)
    print(f"  {sid} {name}: Google News {len(items)} 則")
    targets = []
    seen = set()
    for it in items:
        title = (it.find("title").text or "").strip()
        pub = (it.find("pubDate").text or "").strip()
        url = (it.find("link").text or "").strip()
        dt = parse_pub_date(pub)
        if not dt or dt.year != year or dt.month != month:
            continue
        broker = detect_broker(title)
        price = detect_price(title)
        if not broker or not price:
            continue
        # dedupe: same broker + same price + same day
        key = (broker, price, dt.strftime("%Y-%m-%d"))
        if key in seen: continue
        seen.add(key)
        targets.append({
            "date": dt.strftime("%Y-%m-%d"),
            "broker": broker,
            "price": price,
            "title": title[:120],
            "url": url,
        })
    # 依日期新→舊排
    targets.sort(key=lambda x: x["date"], reverse=True)
    return targets


def main():
    year = datetime.today().year
    month = datetime.today().month
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        month = int(sys.argv[1])

    sys.path.insert(0, BASE)
    from holdings_loader import get_holdings
    holdings, _, _ = get_holdings()
    if not holdings:
        print("❌ 無持股資料"); return

    print(f"📊 抓 {year}-{month:02d} 每支持股目標價...")
    result = {}
    for tup in holdings:
        sid, name = tup[0], tup[1]
        if len(sid) < 3: continue
        is_etf = tup[4] if len(tup) > 4 else False
        if is_etf:
            print(f"  {sid} {name}: ETF 跳過"); continue
        targets = collect_month_targets(sid, name, year, month)
        print(f"    → 匹配 {len(targets)} 筆")
        result[sid] = {
            "name": name,
            "month": f"{year}-{month:02d}",
            "targets": targets,
        }
        time.sleep(1.5)  # 避免 rate limit

    # 存檔（含所有月份 — key = sid, 內含最新月的 targets）
    all_data = {}
    if os.path.exists(CACHE):
        try: all_data = json.load(open(CACHE))
        except: pass
    all_data[f"{year}-{month:02d}"] = result
    all_data["_last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 存到 {CACHE}")
    print(f"總計 {sum(len(v['targets']) for v in result.values())} 筆目標價")


if __name__ == "__main__":
    main()
