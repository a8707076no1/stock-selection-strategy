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
    h = {"count": 0, "total_pl": 0, "total_pct": 0, "worst": None, "details": []}
    m = re.search(r'市值\s*([\d,]+)', txt)
    if m: h["total_mv"] = int(m.group(1).replace(",",""))
    m = re.search(r'損益\s*(-?[\d,+]+)\s*（(-?[\d.+]+)%）', txt)
    if m:
        h["total_pl"] = int(m.group(1).replace(",","").replace("+",""))
        h["total_pct"] = float(m.group(2))
    m = re.search(r'共\s*(\d+)\s*支', txt)
    if m: h["count"] = int(m.group(1))

    m = re.search(r'const holdings\s*=\s*(\[.*?\])\s*;\s*const', txt, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
            h["count"] = len(arr)
            non_etf = [x for x in arr if not x.get("is_etf")]
            if non_etf:
                worst = min(non_etf, key=lambda x: x.get("pl_pct", 0))
                h["worst"] = {"sid": worst.get("sid"), "name": worst.get("name"), "pl_pct": worst.get("pl_pct")}
            # 每支持股簡摘：sid/name/pl_pct/action
            for x in arr:
                h["details"].append({
                    "sid": x.get("sid"), "name": x.get("name",""),
                    "is_etf": x.get("is_etf", False),
                    "current": x.get("current", 0),
                    "pl_pct": x.get("pl_pct", 0),
                    "pl_amt": x.get("pl_amt", 0),
                    "action": x.get("action") or x.get("strat_action", ""),
                    "commentary": (x.get("commentary","") or "")[:200],
                })
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
            v["details"] = [{
                "sid": x.get("sid"), "name": x.get("name",""),
                "tier": x.get("tier",""),
                "sector": x.get("sector","") or x.get("subsector",""),
                "score": x.get("score", 0),
                "current": x.get("current", 0),
                "chg_pct": x.get("chg_pct", 0),
            } for x in arr]
        except: pass
    summary["flash"] = v

    # 4. 突破 / 拉回 / 併購
    for jsvar, key in [("breakouts","breakouts"), ("pullbacks","pullbacks"), ("mergerPicks","merger")]:
        m = re.search(rf'const {jsvar}\s*=\s*(\[.*?\])\s*;', txt, re.DOTALL)
        if m:
            try:
                arr = json.loads(m.group(1))
                summary[key] = {
                    "count": len(arr),
                    "top": [f"{x.get('sid')} {x.get('name','')}" for x in arr[:3]],
                    "details": [{
                        "sid": x.get("sid"), "name": x.get("name",""),
                        "current": x.get("current", 0),
                        "chg_pct": x.get("chg_pct", 0),
                        "signal": x.get("sig","") or x.get("action",""),
                        "note": (x.get("note","") or x.get("commentary","") or "")[:150],
                    } for x in arr]
                }
            except:
                summary[key] = {"count": 0, "top": [], "details": []}
        else:
            summary[key] = {"count": 0, "top": [], "details": []}

    # 5. 族群輪動 — 掃 sector rotation table 內主 row（含「N 支」的族群 row）
    sector_section = ""
    idx = txt.find("子族群輪動")
    if idx > -1:
        sector_section = txt[idx:idx+80000]
    tops = []
    details = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', sector_section, re.DOTALL)
    for row in rows:
        if not re.search(r'#\d+', row): continue
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) < 5: continue
        clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        # 族群 row：cell[0] 含 # 排名，cell[2] 含「支」(成員數)
        if '#' not in clean[0] or '支' not in clean[2]: continue
        rank_m = re.search(r'#(\d+)', clean[0])
        rank = int(rank_m.group(1)) if rank_m else 0
        name = clean[1]
        chg_20d = clean[4] if len(clean) > 4 else ""
        chg_5d  = clean[5] if len(clean) > 5 else ""
        stage   = clean[6] if len(clean) > 6 else ""
        if name not in tops:
            tops.append(name)
        details.append({
            "rank": rank, "name": name,
            "chg_20d": chg_20d, "chg_5d": chg_5d, "stage": stage
        })
    summary["sector"] = {
        "top": tops[:6] if tops else [],
        "details": details[:15] if details else []
    }

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
