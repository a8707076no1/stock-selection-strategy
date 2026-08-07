"""
林漢偉每日影片自動分析（通用版）
==========================================================
支援三種模式：
  --mode premarket   → 抓「台股歐嗨唷! ... 盤前解盤」（8:30 前推）
  --mode postmarket  → 抓「#決勝關鍵」（週一~週五盤後 16:30 前推，排除週末特別版）
  --mode weekend     → 抓「決勝關鍵周末特別版」（週六 12:30 後）

執行邏輯：
  1. yt-dlp 抓 @ps1788 最近 10 支影片
  2. 依 mode 篩選標題
  3. 檢查 history 是否已處理過 → 跳過
  4. 下載音訊 → faster-whisper 轉錄
  5. 抽取重點 + 推 Telegram（雙推）
"""
import os, sys, re, json, time, subprocess, argparse
from datetime import datetime, timedelta

BASE = os.path.expanduser("~/Desktop/Stock Selection Strategy")
LOG_DIR = os.path.join(BASE, "logs")
CACHE_DIR = os.path.join(BASE, "cache")
TMP_DIR = "/tmp/lin_hanwei"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

CHANNEL_HANDLE = "@ps1788"

# 三種模式的篩選規則
MODE_CONFIG = {
    "premarket": {
        "must_have":  ["台股歐嗨唷", "盤前解盤"],
        "must_not":   [],
        "label":      "🌅 盤前解盤",
        "max_age_hours": 20,
        "history_file": "lin_hanwei_premarket_history.json",
    },
    "postmarket": {
        "must_have":  ["#決勝關鍵"],
        "must_not":   ["周末特別版"],
        "label":      "🌇 盤後解盤",
        "max_age_hours": 16,
        "history_file": "lin_hanwei_postmarket_history.json",
    },
    "weekend": {
        "must_have":  ["決勝關鍵", "周末特別版"],
        "must_not":   [],
        "label":      "🏖️ 週末特別版",
        "max_age_hours": 8*24,
        "history_file": "lin_hanwei_history.json",
    },
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_title_date(title):
    """從標題抓日期 e.g. 2026.07.13"""
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", title)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime.strptime(f"{y}{int(mo):02d}{int(d):02d}", "%Y%m%d")
        except: pass
    return None


def get_latest_video(mode_cfg):
    log(f"📡 查詢 {CHANNEL_HANDLE} 最近 10 支影片... (mode filter)")
    try:
        out = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--playlist-end", "10", "--no-warnings",
             "--print", "%(id)s|%(title)s",
             f"https://www.youtube.com/{CHANNEL_HANDLE}/videos"],
            capture_output=True, text=True, timeout=60,
        )
        for line in out.stdout.strip().split("\n"):
            if "|" not in line: continue
            vid, title = line.split("|", 1)
            # must have all
            if not all(kw in title for kw in mode_cfg["must_have"]):
                continue
            # must not have any
            if any(kw in title for kw in mode_cfg["must_not"]):
                continue
            # 檢查發布時間
            pub_dt = parse_title_date(title)
            if not pub_dt:
                continue
            age_hours = (datetime.now() - pub_dt).total_seconds() / 3600
            if age_hours > mode_cfg["max_age_hours"]:
                log(f"  ⏭️  太舊：{title[:60]}（{age_hours:.0f} 小時前）")
                continue
            log(f"  ✅ 找到：{title[:80]}")
            log(f"     vid={vid}, {age_hours:.1f} 小時前")
            return {"vid": vid, "title": title, "date": pub_dt.strftime("%Y%m%d")}
        log(f"  ❌ 沒找到符合條件的影片")
        return None
    except Exception as e:
        log(f"❌ yt-dlp 失敗：{e}")
        return None


def already_processed(vid, history_file):
    hpath = os.path.join(CACHE_DIR, history_file)
    if not os.path.exists(hpath): return False
    try:
        with open(hpath) as f:
            return vid in json.load(f).get("processed_vids", [])
    except: return False


def mark_processed(vid, summary, history_file):
    hpath = os.path.join(CACHE_DIR, history_file)
    hist = {"processed_vids": [], "latest_summary": {}}
    if os.path.exists(hpath):
        try: hist = json.load(open(hpath))
        except: pass
    if vid not in hist.get("processed_vids", []):
        hist.setdefault("processed_vids", []).append(vid)
        # 保留最近 100 筆
        hist["processed_vids"] = hist["processed_vids"][-100:]
    hist["latest_summary"] = summary
    hist["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(hpath, "w") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def download_audio(vid):
    audio_path = os.path.join(TMP_DIR, f"{vid}.mp3")
    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 100_000:
        log(f"  ⏭️  音訊已存在：{os.path.basename(audio_path)}")
        return audio_path
    log(f"  📥 下載音訊 {vid}...")
    for f_ in os.listdir(TMP_DIR):
        if f_.startswith(vid):
            os.remove(os.path.join(TMP_DIR, f_))
    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
         "--no-warnings", "-o", os.path.join(TMP_DIR, f"{vid}.%(ext)s"),
         f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True, timeout=300, check=False,
    )
    if os.path.exists(audio_path):
        sz = os.path.getsize(audio_path) / (1024*1024)
        log(f"  ✅ 音訊 {sz:.1f} MB")
        return audio_path
    return None


def transcribe(audio_path):
    log("🎙️  Whisper 轉錄中...")
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    t0 = time.time()
    segments, _ = model.transcribe(
        audio_path, language="zh", beam_size=1, vad_filter=True,
        initial_prompt="台股、林漢偉、摩爾證券、半導體、PCB、記憶體、台積電、輝達、聯發科、群聯、華邦電、選股冠軍、追高、賣點",
    )
    text = "\n".join(seg.text.strip() for seg in segments)
    log(f"  ✅ {time.time()-t0:.0f}s 完成（{len(text)} 字）")
    return text


def extract_summary(transcript, title):
    """抽出重點（複用 weekly 邏輯 + 錯字 normalize）"""
    typo_map = {
        "記憶鐵": "記憶體", "記題": "記憶體", "計憶體": "記憶體",
        "細金圓": "矽晶圓", "細光子": "矽光子",
        "動武節": "端午節", "段午節": "端午節",
        "舞蹈標股": "五檔標股", "鴨寶": "壓寶",
    }
    for w, r in typo_map.items():
        transcript = transcript.replace(w, r)

    summary = {
        "title": title, "transcript_len": len(transcript),
        "market_view": "", "bull_sectors": [], "warn_sectors": [],
        "stock_picks": [], "avoid_stocks": [], "key_quotes": [],
        "title_sectors": [],
    }

    title_sector_map = {
        "記憶體": ["記憶體", "DRAM", "Flash"], "矽晶圓": ["矽晶圓", "晶圓"],
        "面板": ["面板"], "載板": ["載板", "ABF"], "CCL": ["CCL"],
        "矽光子": ["矽光子", "CPO"], "AI伺服器": ["AI 伺服器", "AI伺服器"],
        "散熱": ["散熱", "液冷"], "車用": ["車用", "電動車", "特斯拉"],
        "機器人": ["機器人"], "低軌衛星": ["低軌", "衛星"],
        "重電": ["重電", "電網"], "PCB": ["PCB"],
        "封裝": ["封裝", "CoWoS"], "光電": ["光電", "光學"],
        "金融": ["金控", "銀行"], "生技": ["生技"],
    }
    for sect, kws in title_sector_map.items():
        for kw in kws:
            if kw in title:
                if sect not in summary["title_sectors"]:
                    summary["title_sectors"].append(sect)
                break

    # 大盤看法
    market_patterns = [r"下[個一]?[週禮]拜[^。\n]{5,80}[。\n]", r"預估[^。\n]{5,60}[。\n]",
                       r"大盤[^。\n]{5,60}[。\n]", r"今[天日][^。\n]{5,60}[。\n]"]
    found = []
    for pat in market_patterns:
        for m in re.findall(pat, transcript)[:3]:
            m = m.strip()
            if 8 < len(m) < 100: found.append(m)
    summary["market_view"] = "｜".join(list(dict.fromkeys(found))[:5])

    # 族群關鍵字 - 內文提及次數
    sectors_map = {
        "記憶體": ["記憶體", "DRAM", "Flash", "NAND"],
        "PCB/載板": ["PCB", "載板", "ABF", "CCL"],
        "AI伺服器": ["AI伺服器", "AI 伺服器", "GB200", "GB300", "推論"],
        "矽光子CPO": ["矽光子", "CPO"], "散熱": ["散熱", "液冷"],
        "車用/特斯拉": ["車用", "特斯拉", "電動車"], "機器人": ["機器人"],
        "低軌衛星": ["低軌", "Starlink"], "重電": ["重電", "GIS"],
        "面板": ["面板", "群創", "友達"], "矽晶圓": ["矽晶圓", "環球晶"],
        "光學鏡頭": ["大立光", "光學鏡頭"], "金融": ["金控"], "生技": ["生技"],
    }
    warn_kw = ["不能追高", "不要追", "賣點", "獲利了結", "出場", "減碼", "見高"]
    bull_kw = ["選股冠軍", "壓寶", "看好", "標的", "佈局", "鎖定", "強勢"]

    for sect, kws in sectors_map.items():
        total = sum(transcript.count(k) for k in kws)
        if total < 2: continue
        w_ctx = b_ctx = 0
        for kw in kws:
            for m in re.finditer(re.escape(kw), transcript):
                ctx = transcript[max(0,m.start()-80):min(len(transcript),m.end()+80)]
                for w in warn_kw:
                    if w in ctx: w_ctx += 1
                for b in bull_kw:
                    if b in ctx: b_ctx += 1
        if w_ctx > b_ctx and w_ctx >= 2:
            summary["warn_sectors"].append(f"{sect}（提及 {total} 次，含警示）")
        else:
            summary["bull_sectors"].append(f"{sect}（提及 {total} 次）")

    # 金句
    for pat in [r"我[認覺]為[^。\n]{10,80}[。\n]", r"建議[^。\n]{10,80}[。\n]",
                 r"反而[^。\n]{10,80}[。\n]", r"千萬不[要能][^。\n]{5,60}[。\n]"]:
        for m in re.findall(pat, transcript)[:5]:
            m = m.strip()
            if 15 < len(m) < 120:
                summary["key_quotes"].append(m)
    summary["key_quotes"] = list(dict.fromkeys(summary["key_quotes"]))[:5]

    return summary


def extract_ma_teaching(transcript):
    """抽出月線/季線教學段落（重要金句 + 判斷法）"""
    # normalize
    t = transcript
    for w, r in [("20 日", "20日"), ("60 日", "60日"), ("120 日", "120日"),
                 ("MA20", "月線"), ("MA60", "季線"), ("MA120", "半年線")]:
        t = t.replace(w, r)

    # 找含月線/季線關鍵字的完整句子
    key_terms = ["月線", "季線", "20日", "60日", "黃金交叉", "死亡交叉", "站上", "跌破"]
    sentences = re.split(r"[。！？\n]", t)
    picked = []
    seen = set()
    for s in sentences:
        s = s.strip()
        if len(s) < 8 or len(s) > 120: continue
        if not any(k in s for k in ["月線", "季線"]): continue
        # 至少含一個「操作/技術動詞」才算教學段
        if not any(k in s for k in key_terms + ["關卡", "反彈", "壓力", "支撐",
                    "斜率", "下彎", "上揚", "重新站", "轉強", "轉弱", "破了", "站回"]):
            continue
        # dedupe
        sig = s[:20]
        if sig in seen: continue
        seen.add(sig)
        picked.append(s)
    # 排序：優先取含「黃金交叉/死亡交叉/站上/跌破/斜率」的
    def rank(s):
        pri = 0
        for k in ["黃金交叉","死亡交叉","站上","跌破","斜率","轉","反彈","支撐"]:
            if k in s: pri -= 1
        return pri
    picked.sort(key=rank)
    return picked[:8]


def analyze_holdings_by_ma():
    """對持股跑月線/季線技術分析"""
    try:
        sys.path.insert(0, BASE)
        from holdings_loader import get_holdings
        import pickle, pandas as pd
    except Exception as e:
        log(f"⚠️ 載持股/pandas 失敗：{e}")
        return []

    try:
        holdings, _, _ = get_holdings()
    except Exception as e:
        log(f"⚠️ get_holdings 失敗：{e}")
        return []
    if not holdings:
        return []

    pc_path = os.path.join(CACHE_DIR, "price_data.pkl")
    if not os.path.exists(pc_path):
        return []
    with open(pc_path, "rb") as f:
        pc = pickle.load(f)

    results = []
    for tup in holdings:
        sid, name = tup[0], tup[1]
        df = pc.get(sid)
        if df is None or len(df) < 65:
            continue
        closes = df["close"].astype(float).tail(65).tolist()
        c = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        ma20_5 = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma20
        ma60_5 = sum(closes[-65:-5]) / 60 if len(closes) >= 65 else ma60
        ma20_slope = (ma20 - ma20_5) / ma20_5 * 100 if ma20_5 else 0
        ma60_slope = (ma60 - ma60_5) / ma60_5 * 100 if ma60_5 else 0

        # 交叉檢測（近 6 日內）
        gc = dc = False
        try:
            for i in range(-6, 0):
                if i-1 < -len(closes): break
                prev20 = sum(closes[i-20:i]) / 20
                prev60 = sum(closes[i-60:i]) / 60
                cur20  = sum(closes[i-19:i+1]) / 20 if i+1 else sum(closes[i-19:]) / 20
                cur60  = sum(closes[i-59:i+1]) / 60 if i+1 else sum(closes[i-59:]) / 60
                if prev20 < prev60 and cur20 >= cur60: gc = True
                if prev20 > prev60 and cur20 <= cur60: dc = True
        except: pass

        # 狀態判斷
        state, advice = [], []
        above_ma20 = c > ma20
        above_ma60 = c > ma60
        if above_ma20 and above_ma60:
            state.append("🟢 站上月線+季線")
        elif above_ma20 and not above_ma60:
            state.append("🟡 站月線/季線下")
        elif not above_ma20 and above_ma60:
            state.append("🟡 跌破月線/季線上")
        else:
            state.append("🔴 月/季線雙破")

        if ma20_slope > 0.3: state.append(f"月線↑{ma20_slope:+.1f}%")
        elif ma20_slope < -0.3: state.append(f"月線↓{ma20_slope:+.1f}%")
        if ma60_slope > 0.2: state.append(f"季線↑{ma60_slope:+.1f}%")
        elif ma60_slope < -0.2: state.append(f"季線↓{ma60_slope:+.1f}%")

        if gc: state.append("✨ 近日黃金交叉")
        if dc: state.append("💀 近日死亡交叉")

        # 專業建議
        if above_ma20 and above_ma60 and ma20_slope > 0 and ma60_slope > 0:
            advice.append("多方結構完整 → 續抱")
        elif not above_ma20 and not above_ma60 and ma20_slope < -0.5:
            advice.append("空方結構 → 建議減碼")
        elif above_ma60 and not above_ma20:
            advice.append("回測季線觀察 → 站上月線再加碼")
        elif above_ma20 and not above_ma60:
            advice.append("反彈波 → 未站季線先減量")
        elif dc:
            advice.append("死叉警訊 → 出場為宜")
        elif gc and above_ma20:
            advice.append("黃金交叉+月線上 → 積極布局")
        else:
            advice.append("整理待變 → 觀望")

        results.append({
            "sid": sid, "name": name, "close": c,
            "ma20": ma20, "ma60": ma60,
            "state": state, "advice": advice[0],
        })

    return results


def format_telegram(summary, vid, title, mode_label):
    today = datetime.today().strftime("%Y-%m-%d %A")
    lines = [
        f"{mode_label} <b>林漢偉 自動整理</b>",
        f"📅 {today}",
        f"🎬 <a href='https://youtube.com/watch?v={vid}'>{title[:80]}</a>",
        "<i>📊 AI 自動轉錄 + 關鍵字分析</i>",
        "",
    ]
    if summary.get("title_sectors"):
        lines.append("🎯 <b>標題明點族群</b>")
        lines.append("  " + " / ".join(summary["title_sectors"]))
        lines.append("")
    if summary.get("market_view"):
        lines.append("📋 <b>大盤看法</b>")
        for l in summary["market_view"].split("｜")[:4]:
            lines.append(f"  • {l}")
        lines.append("")
    if summary.get("bull_sectors"):
        lines.append("🚀 <b>看好族群</b>")
        for s in summary["bull_sectors"][:6]:
            lines.append(f"  ✅ {s}")
        lines.append("")
    if summary.get("warn_sectors"):
        lines.append("⚠️ <b>警示族群</b>")
        for s in summary["warn_sectors"][:4]:
            lines.append(f"  ⚠️ {s}")
        lines.append("")
    if summary.get("key_quotes"):
        lines.append("💬 <b>關鍵金句</b>")
        for q in summary["key_quotes"][:3]:
            lines.append(f"  「{q.strip()}」")

    # ★ 週末版：附月線/季線教學 + 持股 MA 分析
    if summary.get("ma_teaching"):
        lines.append("")
        lines.append("📐 <b>本集月/季線技術要點</b>")
        for q in summary["ma_teaching"][:6]:
            lines.append(f"  • {q}")

    if summary.get("holdings_ma"):
        lines.append("")
        lines.append("💼 <b>我的持股 月/季線體檢</b>")
        for h in summary["holdings_ma"]:
            lines.append(f"  <b>{h['sid']} {h['name']}</b> 收{h['close']:.1f}")
            lines.append(f"    月{h['ma20']:.1f} 季{h['ma60']:.1f} {' / '.join(h['state'])}")
            lines.append(f"    → {h['advice']}")

    return "\n".join(lines)


def push_telegram(msg):
    import requests
    tok = os.environ.get("STOCK_TG_TOKEN", "")
    chat = os.environ.get("STOCK_TG_CHAT", "")
    extra = os.environ.get("STOCK_TG_CHAT_EXTRA", "")
    if not tok or not chat:
        try:
            txt = open(os.path.join(BASE, "stock_agent.py"), encoding="utf-8").read()
            if not tok:
                m = re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', txt);  tok = m.group(1) if m else ""
            if not chat:
                m = re.search(r'TG_CHAT\s*=\s*"([^"]+)"', txt);  chat = m.group(1) if m else ""
        except: pass
    if not tok or not chat:
        log("⚠️ Telegram 未設定")
        return False
    chats = [chat]
    if extra:
        for c in extra.split(","):
            c = c.strip()
            if c and c not in chats: chats.append(c)
    ok = 0
    for c in chats:
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": c, "text": msg, "parse_mode": "HTML",
                                    "disable_web_page_preview": False}, timeout=15)
            log(f"📨 推送 chat={c}: {r.status_code}")
            if r.status_code == 200: ok += 1
        except Exception as e:
            log(f"⚠️ chat={c} 失敗：{e}")
    return ok == len(chats)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=list(MODE_CONFIG.keys()))
    args = ap.parse_args()
    cfg = MODE_CONFIG[args.mode]

    log("=" * 60)
    log(f"林漢偉 {cfg['label']} 自動分析 (mode={args.mode})")
    log("=" * 60)

    v = get_latest_video(cfg)
    if not v:
        log("❌ 沒找到符合條件的影片，結束")
        return
    if already_processed(v["vid"], cfg["history_file"]):
        log(f"  ⏭️  {v['vid']} 已處理過，跳過")
        return

    audio = download_audio(v["vid"])
    if not audio:
        log("❌ 下載失敗")
        return
    transcript = transcribe(audio)
    if not transcript:
        log("❌ 轉錄失敗")
        return
    with open(os.path.join(LOG_DIR, f"lin_hanwei_{args.mode}_{v['date']}_{v['vid']}.txt"),
              "w", encoding="utf-8") as f:
        f.write(transcript)

    summary = extract_summary(transcript, v["title"])
    log(f"📊 抽取：族群{len(summary['bull_sectors'])}看好/{len(summary['warn_sectors'])}警示，金句 {len(summary['key_quotes'])}")

    # ★ 週末版：抽月/季線教學 + 持股 MA 體檢
    if args.mode == "weekend":
        ma_teaching = extract_ma_teaching(transcript)
        summary["ma_teaching"] = ma_teaching
        log(f"📐 月/季線教學段落：{len(ma_teaching)} 句")
        if ma_teaching:
            holdings_ma = analyze_holdings_by_ma()
            summary["holdings_ma"] = holdings_ma
            log(f"💼 持股 MA 分析：{len(holdings_ma)} 支")

    msg = format_telegram(summary, v["vid"], v["title"], cfg["label"])
    push_telegram(msg)
    mark_processed(v["vid"], summary, cfg["history_file"])
    log("✅ 完成")


if __name__ == "__main__":
    main()
