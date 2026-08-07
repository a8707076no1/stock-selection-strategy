"""
從 chart HTML 抽當日重點摘要 → summary_YYYYMMDD.json
==========================================
給 PWA 首頁 tile 顯示今日重點（不用開 chart 就能看到）

輸出格式：
  {
    "date": "2026-08-07",
    "market_verdict": "🔴 警告：族群已撤離",
    "holdings":  {"total_pl": -414092, "total_pct": -3.89, "count": 10, "top_alert": "..."},
    "flash":     {"count": 8, "aaa":0, "aa":0, "a":1, "b":7, "top": ["2395 研華", ...]},
    "breakouts": {"count": 10, "top": ["9941 裕融", ...]},
    "pullbacks": {"count": 10, "top": ["4987 ...", ...]},
    "merger":    {"count": 0, "top": []},
    "sector":    {"note": "..."}
  }
"""
import os, sys, re, json, glob
from datetime import datetime

BASE = os.path.expanduser("~/Desktop/Stock Selection Strategy")


def parse_chart(html_path):
    txt = open(html_path, encoding="utf-8").read()
    date_m = re.search(r"(\d{8})", os.path.basename(html_path))
    if not date_m:
        return None
    ymd = date_m.group(1)
    date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"

    summary = {"date": date_str, "ymd": ymd}

    # 1. 我的持股
    h = {"count": 0, "total_pl": 0, "total_pct": 0, "worst": None}
    m = re.search(r'市值\s*([\d,]+)', txt)
    if m: h["total_mv"] = int(m.group(1).replace(",",""))
    m = re.search(r'損益\s*(-?[\d,+]+)\s*（(-?[\d.+]+)%）', txt)
    if m:
        h["total_pl"] = int(m.group(1).replace(",","").replace("+",""))
        h["total_pct"] = float(m.group(2))
    m = re.search(r'共\s*(\d+)\s*支', txt)
    if m: h["count"] = int(m.group(1))

    # 從 holdings JS array 抓最慘的股
    m = re.search(r'const holdings\s*=\s*(\[.*?\])\s*;\s*const', txt, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
            h["count"] = len(arr)
            # 找最慘的個股（排除 ETF）
            non_etf = [x for x in arr if not x.get("is_etf")]
            if non_etf:
                worst = min(non_etf, key=lambda x: x.get("pl_pct", 0))
                h["worst"] = {
                    "sid": worst.get("sid"), "name": worst.get("name"),
                    "pl_pct": worst.get("pl_pct")
                }
        except Exception as e:
            print(f"  ⚠️ holdings parse: {e}")
    summary["holdings"] = h

    # 2. 大盤判決
    m = re.search(r'今日大盤資金判決：([^<]+)', txt)
    if m: summary["market_verdict"] = m.group(1).strip()

    # 3. V42
    v = {"count": 0, "aaa": 0, "aa": 0, "a": 0, "b": 0, "top": []}
    m = re.search(r"AAA <b>(\d+)</b>.*?AA <b>(\d+)</b>.*?A <b>(\d+)</b>.*?B <b>(\d+)</b>.*?共 (\d+)", txt, re.DOTALL)
    if m:
        v["aaa"] = int(m.group(1))
        v["aa"]  = int(m.group(2))
        v["a"]   = int(m.group(3))
        v["b"]   = int(m.group(4))
        v["count"] = int(m.group(5))
    m = re.search(r'const flashPicks\s*=\s*(\[.*?\])\s*;\s*const', txt, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
            v["top"] = [f"{x.get('sid')} {x.get('name','')}" for x in arr[:3]]
        except: pass
    summary["flash"] = v

    # 4. 突破 / 拉回 / 併購 — 從 JS 數陣列
    for jsvar, key in [("breakouts","breakouts"), ("pullbacks","pullbacks"), ("mergerPicks","merger")]:
        m = re.search(rf'const {jsvar}\s*=\s*(\[.*?\])\s*;', txt, re.DOTALL)
        if m:
            try:
                arr = json.loads(m.group(1))
                summary[key] = {
                    "count": len(arr),
                    "top": [f"{x.get('sid')} {x.get('name','')}" for x in arr[:3]]
                }
            except:
                summary[key] = {"count": 0, "top": []}
        else:
            summary[key] = {"count": 0, "top": []}

    # 5. 族群輪動 — 從 h2 附近抓 top 3 族群名（大致）
    # 簡易：抓 <b>族群名</b>（前置 emoji）在 sector rotation 區塊
    sector_section = ""
    idx = txt.find("子族群輪動")
    if idx > -1:
        sector_section = txt[idx:idx+30000]
    tops = re.findall(r'<td[^>]*style="[^"]*font-weight:bold[^"]*"[^>]*>([🔹🚀💡🏭🔥⚙️🌐💻📱🎮🎯💎🌱🎨💊🧬🏗️🚗🎨💼🍔🏢⚛️🌊][^<]{3,20})</td>', sector_section)
    if not tops:
        # 更寬鬆：抓 emoji + 中文
        tops = re.findall(r'([🔹🚀💡🏭🔥⚙️🌐💻📱🎮🎯💎🌱🎨][^\s<>]{2,15})', sector_section[:5000])
    summary["sector"] = {"top": tops[:3] if tops else []}

    return summary


def main():
    out_dir = os.path.join(BASE, "web")
    os.makedirs(out_dir, exist_ok=True)
    charts = sorted(glob.glob(os.path.join(BASE, "飆股圖表_*.html")))
    for f in charts[-10:]:  # 最近 10 個
        s = parse_chart(f)
        if not s: continue
        out_f = os.path.join(out_dir, f"summary_{s['ymd']}.json")
        with open(out_f, "w", encoding="utf-8") as g:
            json.dump(s, g, ensure_ascii=False, indent=2)
        print(f"✅ {os.path.basename(out_f)}")


if __name__ == "__main__":
    main()
