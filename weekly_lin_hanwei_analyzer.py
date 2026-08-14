"""
週度自動分析：林漢偉「決勝關鍵周末特別版」
==========================================================
每週六 12:30 自動執行：
  1. 抓 @ps1788 頻道（林漢偉分析師-摩爾證券投顧）最新影片
  2. 篩選標題含「決勝關鍵」「周末特別版」+ 本週發布的
  3. yt-dlp 下載音訊
  4. faster-whisper 轉錄
  5. 用關鍵字 + Regex 抽出：
     - 下週大盤判斷
     - 選股冠軍清單
     - 看好族群 / 警告族群
     - 不能追高的個股
  6. 推送 Telegram（爸 + 兒子）
"""
import os, sys, re, json, time, subprocess
from datetime import datetime, timedelta

BASE = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
LOG_DIR = os.path.join(BASE, "logs")
CACHE_DIR = os.path.join(BASE, "cache")
TMP_DIR = "/tmp/lin_hanwei"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

CHANNEL_HANDLE = "@ps1788"
TITLE_KEYWORDS = ["決勝關鍵", "周末特別版"]
HISTORY_CACHE = os.path.join(CACHE_DIR, "lin_hanwei_history.json")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get_latest_video():
    """從 @ps1788 抓最新「決勝關鍵周末特別版」影片"""
    log(f"📡 查詢 {CHANNEL_HANDLE} 最近 10 支影片...")
    try:
        out = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--playlist-end", "10",
             "--no-warnings",
             "--print", "%(id)s|%(title)s",
             f"https://www.youtube.com/{CHANNEL_HANDLE}/videos"],
            capture_output=True, text=True, timeout=60,
        )
        for line in out.stdout.strip().split("\n"):
            if "|" not in line: continue
            vid, title = line.split("|", 1)
            if not all(kw in title for kw in TITLE_KEYWORDS):
                continue
            # 從標題抓日期（格式：2026.06.20）
            m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", title)
            if not m:
                log(f"  ⚠️ 標題無日期：{title[:60]}")
                continue
            y, mo, d = m.groups()
            date_str = f"{y}{int(mo):02d}{int(d):02d}"
            try:
                pub_dt = datetime.strptime(date_str, "%Y%m%d")
                age_days = (datetime.today() - pub_dt).days
                if age_days <= 8:
                    log(f"  ✅ 找到：{title[:80]}")
                    log(f"     vid={vid}, 標題日期 {date_str}, {age_days}天前")
                    return {"vid": vid, "title": title, "date": date_str}
            except Exception:
                pass
        log("  ❌ 沒找到 8 天內的「決勝關鍵周末特別版」影片")
        return None
    except Exception as e:
        log(f"❌ yt-dlp 失敗：{e}")
        return None


def already_processed(vid):
    """檢查是否已處理過該影片"""
    if not os.path.exists(HISTORY_CACHE):
        return False
    try:
        with open(HISTORY_CACHE) as f:
            hist = json.load(f)
        return vid in hist.get("processed_vids", [])
    except Exception:
        return False


def mark_processed(vid, summary):
    """記錄已處理"""
    hist = {"processed_vids": [], "latest_summary": {}}
    if os.path.exists(HISTORY_CACHE):
        try:
            with open(HISTORY_CACHE) as f:
                hist = json.load(f)
        except Exception: pass
    if vid not in hist.get("processed_vids", []):
        hist.setdefault("processed_vids", []).append(vid)
    hist["latest_summary"] = summary
    hist["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(HISTORY_CACHE, "w") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def download_audio(vid):
    """下載 mp3"""
    audio_path = os.path.join(TMP_DIR, f"{vid}.mp3")
    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 100_000:
        log(f"  ⏭️  音訊已存在：{audio_path}")
        return audio_path
    log(f"  📥 下載音訊 {vid}...")
    try:
        # 先清舊檔
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
            log(f"  ✅ 音訊下載完成 {sz:.1f} MB")
            return audio_path
    except Exception as e:
        log(f"❌ 下載失敗：{e}")
    return None


def transcribe(audio_path):
    """faster-whisper 轉錄"""
    log("🎙️  Whisper 轉錄中...")
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    t0 = time.time()
    segments, info = model.transcribe(
        audio_path,
        language="zh",
        beam_size=1,
        vad_filter=True,
        initial_prompt="台股、林漢偉、摩爾證券、選股冠軍、半導體、PCB、AI、記憶體、台積電、輝達、黃仁勳、聯發科、追高、賣點、利多、群聯、華邦電、鈺創",
    )
    text = []
    for seg in segments:
        text.append(seg.text.strip())
    full = "\n".join(text)
    log(f"  ✅ {time.time()-t0:.0f}s 轉錄完成（{len(full)} 字元）")
    return full


def extract_summary(transcript, title):
    """從轉錄文字抽出重點"""
    # ★ Whisper 常見錯字 normalize（同音/形近字還原）
    typo_map = {
        # 記憶體
        "記憶鐵": "記憶體", "記題": "記憶體", "計憶體": "記憶體",
        # 矽相關
        "細金圓": "矽晶圓", "細光子": "矽光子", "細品圓": "矽晶圓",
        # 端午
        "動武節": "端午節", "段午節": "端午節",
        # 期貨結算
        "伺服節算": "期貨結算", "伺服結算": "期貨結算", "伺服節": "期貨季結算",
        # 五大標股
        "舞蹈標股": "五檔標股", "舞檔標股": "五檔標股", "舞檔": "五檔",
        # 拉回
        "拉迴": "拉回", "壓迴": "壓回",
        # 押寶
        "壓寶": "壓寶", "押寶": "壓寶", "鴨寶": "壓寶",
        # 半導體
        "辦會": "費半", "辦": "費半",
        # 細節常見錯字
        "里巴嫩": "黎巴嫩",
    }
    for wrong, right in typo_map.items():
        transcript = transcript.replace(wrong, right)

    summary = {
        "title": title,
        "transcript_len": len(transcript),
        "market_view": "",
        "bull_sectors": [],
        "warn_sectors": [],
        "stock_picks": [],
        "avoid_stocks": [],
        "key_quotes": [],
        "title_sectors": [],  # 從標題抽的族群
    }

    # ★ 從標題抽族群（最準確）
    title_sector_map = {
        "記憶體": ["記憶體", "DRAM", "Flash"],
        "矽晶圓": ["矽晶圓", "晶圓"],
        "面板": ["面板"],
        "載板": ["載板", "ABF", "BT"],
        "CCL": ["CCL", "銅箔基板"],
        "矽光子": ["矽光子", "CPO"],
        "AI伺服器": ["AI 伺服器", "AI伺服器"],
        "散熱": ["散熱", "液冷"],
        "車用": ["車用", "電動車", "特斯拉"],
        "機器人": ["機器人"],
        "低軌衛星": ["低軌", "衛星"],
        "重電": ["重電", "電網"],
        "PCB": ["PCB"],
        "封裝": ["封裝", "CoWoS"],
        "光電": ["光電", "光學"],
        "金融": ["金控", "銀行"],
        "生技": ["生技"],
    }
    for sect, kws in title_sector_map.items():
        for kw in kws:
            if kw in title:
                if sect not in summary["title_sectors"]:
                    summary["title_sectors"].append(sect)
                break

    # 1. 大盤看法（找含「下週」「下禮拜」+ 看法的句子）
    market_patterns = [
        r"下個?[週禮]拜[^。\n]{5,80}[。\n]",
        r"下週[^。\n]{5,80}[。\n]",
        r"預估[^。\n]{5,60}[。\n]",
        r"大盤[^。\n]{5,60}[。\n]",
        r"[預][期計][^。\n]{5,60}[。\n]",
    ]
    found_market = []
    for pat in market_patterns:
        for m in re.findall(pat, transcript)[:3]:
            m = m.strip()
            if 8 < len(m) < 100:
                found_market.append(m)
    summary["market_view"] = "｜".join(list(dict.fromkeys(found_market))[:5])

    # 2. 選股冠軍清單（找「選股冠軍」「壓寶」「五檔」附近的股票名）
    stock_names = re.findall(r"[一-龥]{2,6}(?=\s*[（(]\s*\d{4,5}\s*[）)])", transcript)
    sids = re.findall(r"\b(\d{4,5})\b", transcript)
    # 篩出可能是股名的（含「股」「電」「光」「金」「科」常見字）
    stocks_mentioned = []
    for sid in set(sids):
        # 查 industry 名稱
        try:
            with open(os.path.join(CACHE_DIR, "stock_industry.json")) as f:
                ind = json.load(f).get("industries", {})
            if sid in ind:
                name = ind[sid].get("name", "")
                if name and name in transcript:
                    cnt = transcript.count(name)
                    stocks_mentioned.append((sid, name, cnt))
        except Exception:
            pass
    stocks_mentioned.sort(key=lambda x: -x[2])

    # 3. 用關鍵字判斷推薦 vs 警告
    warn_keywords = ["不能追高", "不要追", "賣點", "獲利了結", "出場", "減碼", "高檔", "見高"]
    bull_keywords = ["選股冠軍", "壓寶", "看好", "標的", "佈局", "鎖定", "強勢"]

    for sid, name, cnt in stocks_mentioned[:30]:
        # 看該股名前後 50 字
        context_count_warn = 0
        context_count_bull = 0
        for m in re.finditer(re.escape(name), transcript):
            ctx_start = max(0, m.start() - 80)
            ctx_end = min(len(transcript), m.end() + 80)
            ctx = transcript[ctx_start:ctx_end]
            for kw in warn_keywords:
                if kw in ctx:
                    context_count_warn += 1
            for kw in bull_keywords:
                if kw in ctx:
                    context_count_bull += 1
        if context_count_warn >= 2 and context_count_warn > context_count_bull:
            summary["avoid_stocks"].append(f"{sid} {name}（提及 {cnt} 次，含「不追高」上下文 {context_count_warn} 次）")
        elif context_count_bull >= 1 or cnt >= 3:
            summary["stock_picks"].append(f"{sid} {name}（提及 {cnt} 次）")

    # 4. 族群關鍵字統計
    sectors_map = {
        "記憶體": ["記憶體", "DRAM", "Flash", "NAND"],
        "PCB/載板": ["PCB", "載板", "ABF", "CCL"],
        "AI伺服器": ["AI伺服器", "AI 伺服器", "GB200", "GB300", "推論"],
        "矽光子CPO": ["矽光子", "CPO"],
        "散熱": ["散熱", "液冷"],
        "車用/特斯拉": ["車用", "特斯拉", "電動車"],
        "機器人": ["機器人", "人形機器人"],
        "低軌衛星": ["低軌", "Starlink", "星鏈"],
        "重電": ["重電", "GIS", "變壓器"],
        "面板": ["面板", "群創", "友達"],
        "矽晶圓": ["矽晶圓", "環球晶"],
        "光學鏡頭": ["大立光", "光學鏡頭"],
        "金融": ["金控", "銀行"],
        "生技": ["生技", "新藥"],
    }
    for sect, kws in sectors_map.items():
        total_count = sum(transcript.count(k) for k in kws)
        if total_count == 0: continue
        # 看「該族群被怎麼描述」
        warn_in_sect = 0; bull_in_sect = 0
        for kw in kws:
            for m in re.finditer(re.escape(kw), transcript):
                ctx_start = max(0, m.start() - 100)
                ctx_end = min(len(transcript), m.end() + 100)
                ctx = transcript[ctx_start:ctx_end]
                for w in warn_keywords:
                    if w in ctx: warn_in_sect += 1
                for w in bull_keywords:
                    if w in ctx: bull_in_sect += 1
        # ★ 放寬到 2 次（whisper 常漏字）
        if total_count >= 2:
            if warn_in_sect > bull_in_sect and warn_in_sect >= 2:
                summary["warn_sectors"].append(f"{sect}（提及 {total_count} 次，含警示）")
            else:
                summary["bull_sectors"].append(f"{sect}（提及 {total_count} 次）")

    # 5. 關鍵金句（找含「我認為」「建議」等開頭的句子）
    quote_patterns = [
        r"我[認覺]為[^。\n]{10,80}[。\n]",
        r"建議[^。\n]{10,80}[。\n]",
        r"反而[^。\n]{10,80}[。\n]",
        r"千萬不[要能][^。\n]{5,60}[。\n]",
    ]
    quotes = []
    for pat in quote_patterns:
        for m in re.findall(pat, transcript)[:5]:
            m = m.strip()
            if 15 < len(m) < 120:
                quotes.append(m)
    summary["key_quotes"] = list(dict.fromkeys(quotes))[:5]

    return summary


def format_telegram(summary, vid, title):
    """格式化 Telegram HTML"""
    today = datetime.today().strftime("%Y-%m-%d %a")
    lines = [
        f"📺 <b>林漢偉「決勝關鍵周末特別版」自動整理</b>",
        f"📅 {today}｜🎬 <a href='https://youtube.com/watch?v={vid}'>{title[:80]}</a>",
        f"<i>📊 AI 自動轉錄 + 關鍵字分析</i>",
        "",
    ]

    # ★ 標題明點的族群（最直接）
    if summary.get("title_sectors"):
        lines.append("🎯 <b>標題明點族群（影片主軸）</b>")
        lines.append("  " + " / ".join(summary["title_sectors"]))
        lines.append("")

    if summary.get("market_view"):
        lines.append("📋 <b>下週大盤看法</b>")
        for line in summary["market_view"].split("｜")[:4]:
            lines.append(f"  • {line}")
        lines.append("")

    if summary.get("bull_sectors"):
        lines.append("🚀 <b>內文看好族群</b>")
        for s in summary["bull_sectors"][:8]:
            lines.append(f"  ✅ {s}")
        lines.append("")

    if summary.get("warn_sectors"):
        lines.append("⚠️ <b>警示族群</b>")
        for s in summary["warn_sectors"][:4]:
            lines.append(f"  ⚠️ {s}")
        lines.append("")

    if summary.get("stock_picks"):
        lines.append("🎯 <b>提及次數較多 / 含推薦語的個股</b>")
        for s in summary["stock_picks"][:10]:
            lines.append(f"  • {s}")
        lines.append("")

    if summary.get("avoid_stocks"):
        lines.append("🔴 <b>警告「不能追高」的個股</b>")
        for s in summary["avoid_stocks"][:6]:
            lines.append(f"  🚫 {s}")
        lines.append("")

    if summary.get("key_quotes"):
        lines.append("💬 <b>關鍵金句</b>")
        for q in summary["key_quotes"][:4]:
            lines.append(f"  「{q.strip()}」")
        lines.append("")

    lines.append("🔗 配合您的飆股圖表 + V42 訊號雙重驗證")
    lines.append(f"📜 全文長度 {summary['transcript_len']:,} 字（已存系統）")
    return "\n".join(lines)


def push_telegram(msg):
    import requests
    tok = os.environ.get("STOCK_TG_TOKEN", "")
    chat = os.environ.get("STOCK_TG_CHAT", "")
    extra = os.environ.get("STOCK_TG_CHAT_EXTRA", "")
    if not tok or not chat:
        try:
            ap = os.path.join(BASE, "stock_agent.py")
            txt = open(ap, encoding="utf-8").read()
            if not tok:
                m = re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', txt)
                if m: tok = m.group(1)
            if not chat:
                m = re.search(r'TG_CHAT\s*=\s*"([^"]+)"', txt)
                if m: chat = m.group(1)
        except Exception: pass
    if not tok or not chat:
        log("⚠️ Telegram 未設定")
        return False

    chats = [chat]
    if extra:
        for c in extra.split(","):
            c = c.strip()
            if c and c not in chats:
                chats.append(c)

    ok = 0
    for c in chats:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": c, "text": msg, "parse_mode": "HTML",
                      "disable_web_page_preview": False},
                timeout=15,
            )
            log(f"📨 推送 chat={c}: {r.status_code}")
            if r.status_code == 200:
                ok += 1
        except Exception as e:
            log(f"⚠️ chat={c} 失敗：{e}")
    return ok == len(chats)


def main():
    log("=" * 60)
    log("林漢偉週末特別版 自動分析")
    log("=" * 60)

    video = get_latest_video()
    if not video:
        log("❌ 沒找到本週影片，結束")
        return

    if already_processed(video["vid"]):
        log(f"  ⏭️  影片 {video['vid']} 已處理過，跳過")
        return

    audio = download_audio(video["vid"])
    if not audio:
        log("❌ 下載音訊失敗，結束")
        return

    transcript = transcribe(audio)
    if not transcript:
        log("❌ 轉錄失敗")
        return

    # 存全文
    txt_path = os.path.join(LOG_DIR, f"lin_hanwei_{video['date']}_{video['vid']}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    log(f"📁 全文存：{txt_path}")

    summary = extract_summary(transcript, video["title"])
    log(f"📊 抽取結果：")
    log(f"   - 看好族群 {len(summary['bull_sectors'])}, 警示族群 {len(summary['warn_sectors'])}")
    log(f"   - 推薦個股 {len(summary['stock_picks'])}, 警告個股 {len(summary['avoid_stocks'])}")
    log(f"   - 金句 {len(summary['key_quotes'])}")

    msg = format_telegram(summary, video["vid"], video["title"])
    push_telegram(msg)

    mark_processed(video["vid"], summary)
    log("✅ 完成")


if __name__ == "__main__":
    main()
