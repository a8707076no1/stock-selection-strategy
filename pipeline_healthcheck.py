"""
Pipeline 健康檢查：跑完 chart 後檢查 summary 是否含關鍵資料
如果有異常空缺 → 立即發 Telegram 警示
"""
import os, sys, json, glob, requests
from datetime import datetime

BASE = (os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy"))
WEB  = os.path.join(BASE, "web")

# 每個區塊的最低期望
EXPECTED = {
    "holdings": {"min": 5,  "label": "我的持股", "hint": "檢查 資產與持股明細更新案夾/*.xlsx 是否存在（GHA 上要 Secret decode）"},
    "flash":    {"min": 1,  "label": "V42 飆股", "hint": "檢查 taiwan_stock_screener_v3.py 是否有 run，或今日確實 0 命中"},
    "breakouts":{"min": 3,  "label": "即將突破", "hint": "檢查 generate_chart build_breakouts 是否 run"},
    "pullbacks":{"min": 3,  "label": "拉回月線", "hint": "檢查 generate_chart build_pullbacks 是否 run"},
    "merger":   {"min": 0,  "label": "併購案",   "hint": "0 支可接受"},   # 併購允許 0
    "sector":   {"min": 5,  "label": "族群輪動", "hint": "檢查 sector_analyzer + summary regex", "detail_field": "details"},
}


def send_tg(msg):
    tok  = os.environ.get("STOCK_TG_TOKEN", "")
    chat = os.environ.get("STOCK_TG_CHAT",  "")
    extra = os.environ.get("STOCK_TG_CHAT_EXTRA", "")
    if not tok or not chat:
        print("⚠️ Telegram 未設定，改印 stdout"); print(msg); return
    for c in [chat] + [x.strip() for x in extra.split(",") if x.strip() and x.strip() != chat]:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": c, "text": msg, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        except Exception as e:
            print(f"⚠️ TG {c}: {e}")


def main():
    today = datetime.today().strftime("%Y%m%d")
    summary_path = os.path.join(WEB, f"summary_{today}.json")

    if not os.path.exists(summary_path):
        # 找最新一份
        files = sorted(glob.glob(os.path.join(WEB, "summary_*.json")))
        if not files:
            send_tg("🚨 <b>Pipeline HealthCheck</b>\n找不到任何 summary.json → chart pipeline 完全沒跑！")
            sys.exit(1)
        summary_path = files[-1]

    d = json.load(open(summary_path, encoding="utf-8"))
    ymd = d.get("ymd", "?")

    issues = []
    for key, cfg in EXPECTED.items():
        section = d.get(key) or {}
        # 用 count 或 details 長度
        if cfg.get("detail_field"):
            n = len(section.get(cfg["detail_field"]) or [])
        else:
            n = section.get("count", 0)
        if n < cfg["min"]:
            issues.append({
                "key": key, "label": cfg["label"],
                "actual": n, "expected": cfg["min"],
                "hint": cfg["hint"],
            })

    if not issues:
        print(f"✅ HealthCheck {ymd} PASS：所有區塊皆有資料")
        return

    # 有問題 → 送 alert
    lines = [
        f"🚨 <b>Pipeline HealthCheck 警示</b>",
        f"📅 資料日 {ymd}",
        f"⚠️ 發現 {len(issues)} 個區塊異常空缺：",
        "",
    ]
    for i in issues:
        lines.append(f"❌ <b>{i['label']}</b>：{i['actual']} 支（期望 ≥{i['expected']}）")
        lines.append(f"   💡 {i['hint']}")
    lines.append("")
    lines.append("<i>請盡快檢查 pipeline log</i>")
    lines.append(f"<i>https://stock-selection.pages.dev/</i>")

    send_tg("\n".join(lines))
    print("\n".join(lines))
    # exit code non-zero 讓 workflow 標示為 partial success
    sys.exit(1)


if __name__ == "__main__":
    main()
