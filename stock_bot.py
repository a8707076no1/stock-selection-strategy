#!/usr/bin/env python3
"""
台股 Telegram 互動機器人
長輪詢監聽訊息，使用者輸入股票代號就回傳詳細分析。

支援指令：
- 4 位數字 / 帶字母代碼（如 2330、00640L）→ 詳細分析
- 持股 / 我的持股 → 持股總覽
- 突破 / 即將突破 → 突破候選總覽
- 飆股 / 名單 → 今日飆股名單（前 10）
- 幫助 / help / ? → 顯示指令說明
"""
import os, sys, re, time, json, traceback, threading, subprocess
from datetime import datetime, date

# ── 設定 ──
BASE_DIR = os.path.expanduser(os.environ.get("STOCK_BASE_DIR", "~/Desktop/Stock Selection Strategy"))
LOG_FILE = os.path.expanduser(os.environ.get("STOCK_BOT_LOG",
    "~/Desktop/Stock Selection Strategy/logs/stock_bot.log"))
OFFSET_FILE = os.path.expanduser("~/Desktop/Stock Selection Strategy/logs/stock_bot_offset.json")

# 優先環境變數，否則沿用 stock_agent 的硬編 token
TG_TOKEN = os.environ.get("STOCK_TG_TOKEN", "")
TG_CHAT  = os.environ.get("STOCK_TG_CHAT",  "")

if not TG_TOKEN:
    # 從 stock_agent.py 讀 token（向下相容）
    try:
        agent_path = os.path.join(BASE_DIR, "stock_agent.py")
        if os.path.exists(agent_path):
            with open(agent_path) as f:
                txt = f.read()
            m1 = re.search(r'TG_TOKEN\s*=\s*"([^"]+)"', txt)
            m2 = re.search(r'TG_CHAT\s*=\s*"([^"]+)"', txt)
            if m1 and m1.group(1):
                TG_TOKEN = m1.group(1)
            if m2 and m2.group(1):
                TG_CHAT = m2.group(1)
    except Exception:
        pass

sys.path.insert(0, BASE_DIR)
import requests
from telegram_helpers import (
    find_latest_chart_html, parse_chart_html, find_in_data,
    fmt_holding_detail, fmt_breakout_detail, fmt_flagship_detail,
    fmt_holdings_summary, fmt_holdings_dividends, fmt_breakouts_summary,
    fmt_help, split_message,
)


def log(msg):
    """只 print；LaunchAgent 已把 stdout 重導到 log 檔"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def tg_send(chat_id, text, parse_mode="HTML"):
    """送訊息到指定 chat。HTML 解析失敗時自動 fallback 到純文字。"""
    if not TG_TOKEN:
        log("❌ 沒有 TG_TOKEN，跳過發送")
        return
    try:
        for part in split_message(text, limit=4000):
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if not r.ok:
                err = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
                desc = err.get("description","")
                log(f"⚠️ 發送失敗 {r.status_code}: {desc[:200]}")
                # HTML 解析錯 → 移除 tag 後重送
                if "parse entities" in desc or "Bad Request" in desc:
                    plain = re.sub(r"<[^>]+>", "", part)
                    r2 = requests.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": plain, "disable_web_page_preview": True},
                        timeout=15)
                    if r2.ok:
                        log("  ✓ 改純文字發送成功")
                    else:
                        log(f"  ✗ 純文字也失敗：{r2.text[:200]}")
    except Exception as e:
        log(f"❌ 發送例外：{e}")


def load_offset():
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE) as f:
                return json.load(f).get("offset", 0)
        except Exception:
            return 0
    return 0


def save_offset(offset):
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception:
        pass


def get_updates(offset, timeout=25):
    """長輪詢取訊息"""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(["message"])},
            timeout=timeout + 10,
        )
        if r.ok:
            return r.json().get("result", [])
    except requests.exceptions.Timeout:
        return []
    except Exception as e:
        log(f"⚠️ getUpdates 失敗：{e}")
    return []


# ── 指令處理 ───────────────────────────────────────

STOCK_CODE_RE = re.compile(r"^[0-9]{4,6}[A-Za-z]?$")
EXTRACT_CODE_RE = re.compile(r"\b([0-9]{4,6}[A-Za-z]?)\b")


def _send_latest_chart_doc():
    """Bot 指令：把最新飆股圖表 HTML 推到 Telegram（給 iPhone 用）"""
    import glob, os as _os
    try:
        files = sorted(glob.glob(f"{BASE_DIR}/飆股圖表_*.html"))
        if not files:
            tg("❌ 找不到任何飆股圖表 HTML")
            return
        latest = files[-1]
        fname = _os.path.basename(latest)
        ds = fname.replace("飆股圖表_","").replace(".html","")
        file_size_mb = _os.path.getsize(latest) / (1024*1024)
        caption = (
            f"📊 <b>最新飆股圖表 {ds[:4]}-{ds[4:6]}-{ds[6:8]}</b>\n"
            f"📦 檔案 {file_size_mb:.1f} MB\n"
            f"📱 點此檔案 → 選 Safari 開啟\n"
            f"💡 在 Safari → 分享 → 加入主畫面 → 變 app icon"
        )
        with open(latest, "rb") as fp:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                data={"chat_id": TG_CHAT, "caption": caption, "parse_mode": "HTML"},
                files={"document": (fname, fp, "text/html")},
                timeout=60,
            )
        log(f"📎 圖表檔推送 {r.status_code}: {fname}")
    except Exception as e:
        log(f"❌ _send_latest_chart_doc: {e}")
        tg(f"❌ 推送失敗：{e}")


def _refresh_holdings_and_chart():
    """偵測最新持股 xlsx + 重跑 generate_chart.py，回傳結果訊息"""
    import subprocess
    try:
        from holdings_loader import find_latest_holdings_xlsx, load_holdings_from_xlsx
    except Exception as e:
        return f"⚠️ 載入 holdings_loader 失敗：{e}"

    path, file_date = find_latest_holdings_xlsx()
    if not path:
        return "⚠️ 找不到「資產與持股明細更新案夾」內的 xlsx 檔。"
    h_list = load_holdings_from_xlsx(path)
    if not h_list:
        return f"⚠️ 持股 xlsx 解析失敗：{os.path.basename(path)}"

    log(f"🔄 收到「持股更新」指令，重跑 generate_chart.py")
    py = "/Users/a8707076/stock_env/bin/python3"
    if not os.path.exists(py):
        py = sys.executable or "python3"
    try:
        proc = subprocess.run(
            [py, os.path.join(BASE_DIR, "generate_chart.py")],
            capture_output=True, text=True, timeout=600, cwd=BASE_DIR,
        )
        ok = "HTML 報表已產生" in (proc.stdout or "")
    except subprocess.TimeoutExpired:
        return "⚠️ 重跑超時（>10 分鐘）"
    except Exception as e:
        return f"⚠️ 重跑失敗：{e}"

    # 摘要持股
    lines = [
        f"✅ <b>持股已更新並重跑圖表</b>" if ok else "⚠️ 圖表重跑似乎未成功，請看 log",
        f"來源：<code>{os.path.basename(path)}</code>",
        f"日期：{file_date or '—'}",
        f"共 {len(h_list)} 支：",
    ]
    for sid, name, shares, cost, is_etf in h_list:
        tag = "🟣" if is_etf else "🔵"
        cost_s = f"成本 {cost}" if cost else "(無成本)"
        lines.append(f"　{tag} <code>{sid}</code> {name} {shares} 張 {cost_s}")
    if not ok:
        last_err = (proc.stderr or "")[-300:]
        lines.append(f"\n錯誤輸出：<code>{last_err}</code>")
    return "\n".join(lines)


def _render_item(item, src):
    """把找到的 item 格式化成 Telegram 訊息"""
    if src == "holding":
        return fmt_holding_detail(item, header=True)
    if src == "breakout":
        if item.get("strategies"):
            item2 = dict(item)
            item2["pl_amt"] = 0; item2["pl_pct"] = 0
            item2["is_etf"] = False
            return fmt_breakout_detail(item) + "\n\n" + fmt_holding_detail(item2, header=False)
        return fmt_breakout_detail(item)
    if src == "flagship":
        base = fmt_flagship_detail(item)
        ss = item.get("strategies", []) or []
        if ss:
            lines = ["", "📚 飆股在線等策略匹配"]
            for m in ss[:8]:
                ico = {"buy":"🟢","sell":"🔴","warning":"🟡","info":"🔵"}.get(m.get("type"), "⚪")
                lines.append(f"　{ico} Ep{m.get('ep')} {m.get('name')}")
            base += "\n" + "\n".join(lines)
        return base
    return "（無法格式化資料）"


def handle_query(text):
    """解析使用者輸入，回傳要送的訊息字串。
    支援：代碼、名稱（部分比對）、從句子中抽取代碼、忽略空白與標點。
    """
    text = (text or "").strip()
    if not text:
        return fmt_help()
    low = text.lower()
    # 移除常見標點/空白/換行（用於指令匹配）
    norm = re.sub(r"[\s,，。、\.\?？!！]+", "", text)

    if low in ("help","?","/start","/help") or norm in ("幫助","說明","指令","help"):
        return fmt_help()

    # 載入當日資料
    html_path = find_latest_chart_html(BASE_DIR)
    if not html_path:
        return "⚠️ 找不到當日飆股圖表，請等 15:35 自動產生後再查詢。"
    stocks, holdings, breakouts = parse_chart_html(html_path)

    # 命令匹配（看正規化後的字串是否包含關鍵字）
    if norm in ("持股","我的持股") or low == "/holdings":
        return fmt_holdings_summary(holdings)
    if norm in ("持股配息","持股配股配息","配股配息","配息明細","股利明細") or low in ("/dividends","/dividend"):
        return fmt_holdings_dividends(holdings)
    if norm in ("持股更新","更新持股","重新計算") or low == "/refresh":
        return _refresh_holdings_and_chart()
    if norm in ("飆股","圖表","飆股圖表","今日圖表") or low in ("/chart","/today"):
        # 推送最新飆股圖表 HTML 檔案到 Telegram
        threading.Thread(target=_send_latest_chart_doc, daemon=True).start()
        return "📊 正在傳送今日飆股圖表 HTML 檔案，請稍候..."
    if norm in ("watchdog","檢查","健檢","狀態") or low == "/watchdog":
        # 強制觸發 watchdog 檢查（不寫入「已執行」狀態，下次正常時間還會跑）
        threading.Thread(target=lambda: watchdog_check_and_notify(force=True), daemon=True).start()
        return "🔧 已觸發 watchdog 檢查，結果會另外傳訊。"
    if norm in ("總資產","資產","total","totalassets") or low == "/assets":
        from telegram_helpers import fmt_total_assets
        return fmt_total_assets(holdings)
    # 「設定 X 1500000」
    set_match = re.match(r"^(?:設定|/set)\s+(AIA|保誠|勞退|勞保|aia|prudential)\s+([\d,，.]+)$", text.strip())
    if set_match:
        from assets_extras import set_value
        ok, msg = set_value(set_match.group(1), set_match.group(2).replace(",","").replace("，",""))
        return msg
    if norm in ("突破","即將突破") or low == "/breakouts":
        return fmt_breakouts_summary(breakouts)
    if norm in ("飆股","名單","今日飆股") or low == "/flagship":
        if not stocks: return "今日無飆股名單"
        out = [f"📈 <b>今日飆股名單（共 {len(stocks)} 支）— 顯示前 10</b>", ""]
        for s in stocks[:10]:
            out.append(f"・<b>{s.get('sid','')}</b> {s.get('name','')}　{s.get('sig','')}　評分 {s.get('score',0)}")
        out.append("")
        out.append("輸入個別代碼可查詳細分析。")
        return "\n".join(out)

    # 1) 完全比對代碼
    if STOCK_CODE_RE.match(text):
        result = find_in_data(text, stocks, holdings, breakouts)
        if isinstance(result, tuple) and len(result) == 2:
            item, src = result
            if item: return _render_item(item, src)

    # 2) 從句子抽取代碼（例：「3587 沒有出現」「3587的資料」）
    m = EXTRACT_CODE_RE.search(text)
    if m:
        code = m.group(1)
        result = find_in_data(code, stocks, holdings, breakouts)
        if isinstance(result, tuple) and len(result) == 2:
            item, src = result
            if item:
                return f"💡 從訊息抽出代碼 <code>{code}</code>：\n\n" + _render_item(item, src)

    # 3) 名稱查詢（含部分比對）
    result = find_in_data(text, stocks, holdings, breakouts)
    if isinstance(result, tuple) and len(result) == 2:
        item, src = result
        if item: return _render_item(item, src)
        if result[0] == "MULTI":
            cands = result[1]
            lines = [f"🔍 找到 {len(cands)} 個包含「{text}」的候選，請輸入完整代碼："]
            for c in cands[:10]:
                lines.append(f"　・<code>{c.get('sid','')}</code> {c.get('name','')}")
            return "\n".join(lines)

    # 4) 不認得
    return (
        f"⚠️ 看不懂指令「{text}」，也找不到對應股票。\n"
        "可輸入：\n"
        "・股票<b>代碼</b>（如 <code>2330</code>）\n"
        "・股票<b>名稱</b>（如 <code>台積電</code>）\n"
        "・<code>持股</code>／<code>突破</code>／<code>飆股</code>／<code>幫助</code>"
    )


### ── Watchdog：每天 16:00 後檢查是否產出今日報告，沒有就通知並自救 ──
WATCHDOG_STATE_FILE = os.path.expanduser("~/.stock_watchdog_state.json")


def _read_watchdog_date():
    try:
        with open(WATCHDOG_STATE_FILE) as f:
            return json.load(f).get("last_run_date", "")
    except Exception:
        return ""


def _write_watchdog_date(d):
    try:
        with open(WATCHDOG_STATE_FILE, "w") as f:
            json.dump({"last_run_date": d, "ts": time.time()}, f)
    except Exception:
        pass


def _agent_log_paths():
    """所有可能的 agent log 路徑（用戶可能移動過）"""
    candidates = [
        os.path.expanduser(os.environ.get("STOCK_REPORT_FILE", "~/agent_report.log")),
        os.path.expanduser("~/agent_report.log"),
        os.path.join(BASE_DIR, "logs", "agent_report.log"),
        os.path.join(BASE_DIR, "agent_report.log"),
    ]
    return [p for p in dict.fromkeys(candidates) if p]


def watchdog_check_and_notify(force=False):
    """檢查今日報告 + agent 狀態，異常時通知並嘗試自救。"""
    today_dt   = datetime.today()
    today_str  = today_dt.strftime("%Y%m%d")
    today_dash = today_dt.strftime("%Y-%m-%d")

    # 非交易日不檢查
    if today_dt.weekday() >= 5 and not force:
        log(f"watchdog: {today_dash} 非交易日，跳過")
        return

    xlsx = os.path.join(BASE_DIR, f"飆股日報_{today_str}.xlsx")
    html = os.path.join(BASE_DIR, f"飆股圖表_{today_str}.html")
    missing = []
    if not os.path.exists(xlsx):
        missing.append(f"飆股日報_{today_str}.xlsx")
    if not os.path.exists(html):
        missing.append(f"飆股圖表_{today_str}.html")

    # 檢查 agent 今天是否寫過 log
    agent_ran_today = False
    for p in _agent_log_paths():
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(max(0, os.path.getsize(p) - 8000))
                tail = f.read()
            if today_dash in tail and "Agent 啟動" in tail:
                agent_ran_today = True
                break
        except Exception:
            pass

    # 檢查 LaunchAgent 是否異常（exit code 非 0）
    bad_launchagents = []
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            for tag in ("com.stock.screener", "com.stock.chart", "com.stock.agent"):
                if tag in line:
                    parts = line.split()
                    if len(parts) >= 2:
                        last_exit = parts[1]
                        if last_exit not in ("0", "-"):
                            bad_launchagents.append(f"{tag} (exit {last_exit})")
    except Exception:
        pass

    log(f"watchdog: missing={missing} agent_ran={agent_ran_today} bad_launchagents={bad_launchagents}")

    if not missing and agent_ran_today and not bad_launchagents:
        # 一切正常，只送一個極簡心跳（每日 16:00 後）
        log("watchdog: 一切正常")
        return

    # 異常 → 通知
    msg_lines = [f"🚨 <b>Watchdog 警示 {today_dash} {today_dt.strftime('%H:%M')}</b>", ""]
    if missing:
        msg_lines.append("❌ <b>缺少今日報告</b>")
        for m in missing:
            msg_lines.append(f"　・{m}")
        msg_lines.append("")
    if not agent_ran_today:
        msg_lines.append("❌ AI Agent 今天還沒跑（LaunchAgent 可能掛了）")
        msg_lines.append("")
    if bad_launchagents:
        msg_lines.append("⚠️ <b>LaunchAgent 異常</b>")
        for b in bad_launchagents:
            msg_lines.append(f"　・{b}")
        msg_lines.append("")
    msg_lines.append("🔧 Bot 開始自救：重新載入 LaunchAgent + 手動跑 agent...")
    tg_send(TG_CHAT, "\n".join(msg_lines))

    # ── 自救 1：重新 load LaunchAgent ──
    if sys.platform == "darwin":
        for name in ("screener", "chart", "agent"):
            plist = os.path.expanduser(f"~/Library/LaunchAgents/com.stock.{name}.plist")
            if os.path.exists(plist):
                try:
                    subprocess.run(["launchctl", "unload", plist], capture_output=True, timeout=10)
                    subprocess.run(["launchctl", "load", "-w", plist], capture_output=True, timeout=10)
                    log(f"  ✓ reload {name}")
                except Exception as e:
                    log(f"  ✗ reload {name}: {e}")

    # ── 自救 2：手動跑缺的程式 ──
    fix_msgs = []
    if not os.path.exists(xlsx):
        log("watchdog: 手動跑 screener")
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(BASE_DIR, "taiwan_stock_screener_v3.py")],
                capture_output=True, text=True, timeout=900, cwd=BASE_DIR)
            ok = os.path.exists(xlsx) or "篩選完成" in (r.stdout or "")
            fix_msgs.append(f"{'✅' if ok else '❌'} 手動跑 screener")
        except Exception as e:
            fix_msgs.append(f"❌ screener 失敗：{e}")
    if not os.path.exists(html):
        log("watchdog: 手動跑 chart")
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(BASE_DIR, "generate_chart.py")],
                capture_output=True, text=True, timeout=600, cwd=BASE_DIR)
            ok = os.path.exists(html) or "HTML 報表已產生" in (r.stdout or "")
            fix_msgs.append(f"{'✅' if ok else '❌'} 手動跑 chart")
        except Exception as e:
            fix_msgs.append(f"❌ chart 失敗：{e}")

    # 跑 agent 補通知
    log("watchdog: 跑 agent 補正常推播")
    try:
        subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, "stock_agent.py")],
            capture_output=True, text=True, timeout=900, cwd=BASE_DIR)
        fix_msgs.append("✅ 已執行 agent 補推播")
    except Exception as e:
        fix_msgs.append(f"❌ agent 失敗：{e}")

    tg_send(TG_CHAT, "🔧 <b>自救結果</b>\n\n" + "\n".join(fix_msgs))


def watchdog_loop():
    """每 5 分鐘檢查一次，每天 16:00 後執行一次 check"""
    log("🐕 watchdog 已啟動（每天 16:00 後檢查）")
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            # 每天 16:00 ~ 23:59 之間執行（之後跑就算太晚也要通知）
            if now.hour >= 16 and _read_watchdog_date() != today_str:
                log(f"watchdog: 進行今日（{today_str}）檢查")
                try:
                    watchdog_check_and_notify()
                except Exception as e:
                    log(f"watchdog check 失敗：{e}\n{traceback.format_exc()}")
                _write_watchdog_date(today_str)
        except Exception as e:
            log(f"watchdog loop 例外：{e}")
        time.sleep(300)  # 5 分鐘


def main():
    if not TG_TOKEN or not TG_CHAT:
        log("❌ STOCK_TG_TOKEN / STOCK_TG_CHAT 未設定，bot 無法啟動")
        return
    log(f"🤖 stock_bot 啟動，long polling on chat={TG_CHAT}")
    # 啟動 watchdog 背景執行緒
    t = threading.Thread(target=watchdog_loop, daemon=True)
    t.start()
    offset = load_offset()
    while True:
        try:
            updates = get_updates(offset, timeout=25)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                text = msg.get("text", "")
                if not chat_id or not text:
                    continue
                # 只回應指定 chat（避免外人騷擾）
                if str(chat_id) != str(TG_CHAT):
                    log(f"忽略陌生 chat {chat_id}")
                    continue
                log(f"💬 收到：{text!r} from {chat_id}")
                try:
                    reply = handle_query(text)
                except Exception as e:
                    log(f"❌ 處理失敗：{e}\n{traceback.format_exc()}")
                    reply = f"⚠️ 處理失敗：{e}"
                tg_send(chat_id, reply)
            save_offset(offset)
        except KeyboardInterrupt:
            log("收到 Ctrl-C，結束")
            break
        except Exception as e:
            log(f"❌ 主迴圈例外：{e}\n{traceback.format_exc()}")
            time.sleep(5)


if __name__ == "__main__":
    main()
