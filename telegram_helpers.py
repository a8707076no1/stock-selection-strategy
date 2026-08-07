"""
Telegram 訊息格式化 + HTML 報告解析 共用模組
給 stock_agent.py（每日推播）與 stock_bot.py（互動查詢）共用
"""
import os, re, json, glob
from html import escape as _html_escape
from datetime import datetime

BASE_DIR = os.path.expanduser("~/Desktop/Stock Selection Strategy")


def _esc(v):
    """HTML escape — 避免 5>20、< 等符號被 Telegram 當成 tag"""
    if v is None: return ""
    return _html_escape(str(v), quote=False)


def find_latest_chart_html(base_dir=BASE_DIR):
    """找最近一個飆股圖表 html"""
    files = sorted(glob.glob(os.path.join(base_dir, "飆股圖表_*.html")), reverse=True)
    return files[0] if files else None


def parse_chart_html(html_path):
    """從飆股圖表 HTML 解析三組 JSON：stocks / holdings / breakouts"""
    if not html_path or not os.path.exists(html_path):
        return None, None, None
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    out = {"stocks": [], "holdings": [], "breakouts": []}
    for key, pat in [
        ("stocks",    r'const stocks = (\[.*?\]);\n'),
        ("holdings",  r'const holdings  = (\[.*?\]);'),
        ("breakouts", r'const breakouts = (\[.*?\]);'),
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                out[key] = json.loads(m.group(1))
            except Exception:
                pass
    return out["stocks"], out["holdings"], out["breakouts"]


def find_in_data(query, stocks, holdings, breakouts):
    """依股票代碼或名稱在三組資料中找，回傳 (item, source) 或 (None, None)"""
    if not query: return None, None
    q = query.strip().upper()
    # 1) 完全比對代碼
    for src, lst in [("flagship", stocks), ("holding", holdings), ("breakout", breakouts)]:
        for it in (lst or []):
            if str(it.get("sid","")).upper() == q:
                return it, src
    # 2) 完全比對名稱
    for src, lst in [("holding", holdings), ("flagship", stocks), ("breakout", breakouts)]:
        for it in (lst or []):
            if str(it.get("name","")) == query.strip():
                return it, src
    # 3) 名稱含關鍵字（部分比對）
    matches = []
    for src, lst in [("holding", holdings), ("flagship", stocks), ("breakout", breakouts)]:
        for it in (lst or []):
            if query.strip() and query.strip() in str(it.get("name","")):
                matches.append((it, src))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # 回傳 list 讓呼叫方提示「多個匹配」
        return ("MULTI", [m[0] for m in matches])
    return None, None


# ── 訊息格式化 ─────────────────────────────────────

def _fmt_price(v):
    if v is None: return "—"
    try:    return f"{float(v):.2f}"
    except: return str(v)


def fmt_holding_detail(h, header=True):
    """單支持股的詳細分析訊息（HTML 格式給 Telegram，所有動態字串都 HTML escape）"""
    sid    = h.get("sid", "")
    name   = h.get("name", "")
    cur    = h.get("current", 0)
    is_etf = h.get("is_etf", False)
    pl_amt = h.get("pl_amt", 0)
    pl_pct = h.get("pl_pct", 0)
    shares = h.get("shares", 0)
    cash_ps   = h.get("dividend_cash_per_share", 0) or 0
    stock_ps  = h.get("dividend_stock_per_share", 0) or 0
    cash_amt  = h.get("dividend_cash_amount", 0) or 0
    stock_amt = h.get("dividend_stock_amount", 0) or 0
    new_shrs  = h.get("dividend_new_shares", 0) or 0
    div_amt   = h.get("dividend_amount", 0) or 0
    div_events= h.get("dividend_events", []) or []
    next_ex   = h.get("dividend_next_ex_date") or ""
    div_src   = h.get("dividend_source", "finmind")
    tr_amt    = h.get("total_return_amt", pl_amt) or 0
    tr_pct    = h.get("total_return_pct", pl_pct) or 0
    action = h.get("action", "—")
    score  = h.get("score", "—")
    ep     = h.get("entry_price")
    sl     = h.get("stop_loss")
    tp     = h.get("target_price")
    pos    = h.get("position_advice", "")
    sec    = h.get("sections", {}) or {}
    buy_n  = h.get("buy_signals", 0)
    sell_n = h.get("sell_signals", 0)
    warn_n = h.get("warn_signals", 0)
    targets = h.get("targets") or {}
    strategies = h.get("strategies", []) or []
    holding = h.get("holding") or {}

    lines = []
    if header:
        tag = "[ETF]" if is_etf else "[個股]"
        pl_sign = "📈" if pl_amt >= 0 else "📉"
        lines.append(f"<b>{_esc(sid)} {_esc(name)}</b> {tag}")
        lines.append(f"現價 <b>{_esc(cur)}</b>　{pl_sign} {pl_pct:+.2f}% ({pl_amt:+,.0f})")
        if div_amt > 0:
            tr_sign = "📈" if tr_amt >= 0 else "📉"
            src_tag = "📝 手填" if div_src == "manual" else "🔎 自動"
            status = "公告中" if div_src == "manual" and not next_ex else "已排定"
            bits = []
            if cash_amt > 0:
                bits.append(f"現金 <b>{cash_amt:,.0f}</b>（每股 {cash_ps:.4f}）")
            if stock_amt > 0:
                bits.append(f"股票 <b>{stock_amt:,.0f}</b>（每股 {stock_ps:.4f} → 配股 {new_shrs:,.2f} 股）")
            lines.append(f"📢 {status} 配股配息（{src_tag}）：" + " ／ ".join(bits))
            for ev in div_events:
                ex = ev.get("ex_date","")
                if ev.get("kind") == "cash":
                    lines.append(f"　・現金除息 {ex} → 發放 {ev.get('pay_date','—') or '—'}：{ev.get('amount',0):.4f} 元/股")
                else:
                    lines.append(f"　・股票除權 {ex}：{ev.get('amount',0):.4f} 元/股")
            lines.append(f"{tr_sign} 總報酬（含未來股利）<b>{tr_amt:+,.0f}</b>　({tr_pct:+.2f}%)")
        lines.append("")

    # 操作建議
    lines.append(f"🎯 <b>{_esc(action)}</b>　綜合分數 {_esc(score)}")
    if buy_n or sell_n or warn_n:
        lines.append(f"訊號：買 {buy_n} ／ 賣 {sell_n} ／ 警示 {warn_n}")

    # 價位
    if ep or sl or tp:
        ep_pct = (((ep - cur) / cur) * 100) if (ep and cur) else None
        sl_pct = (((sl - cur) / cur) * 100) if (sl and cur) else None
        tp_pct = (((tp - cur) / cur) * 100) if (tp and cur) else None
        bits = []
        if ep: bits.append(f"📍加碼 {_fmt_price(ep)}" + (f" ({ep_pct:+.1f}%)" if ep_pct is not None else ""))
        if sl: bits.append(f"🛑停損 {_fmt_price(sl)}" + (f" ({sl_pct:+.1f}%)" if sl_pct is not None else ""))
        if tp: bits.append(f"🎯目標 {_fmt_price(tp)}" + (f" ({tp_pct:+.1f}%)" if tp_pct is not None else ""))
        lines.append("　".join(bits))

    if pos:
        lines.append(f"📌 {_esc(pos)}")

    # 五大面向（含 5>20、5<20 等符號要 escape）
    if sec:
        lines.append("")
        if sec.get("trend"):       lines.append(f"📈 <b>趨勢</b>：{_esc(sec['trend'])}")
        if sec.get("technical"):   lines.append(f"📊 <b>技術</b>：{_esc(sec['technical'])}")
        if sec.get("chip"):        lines.append(f"🏦 <b>籌碼</b>：{_esc(sec['chip'])}")
        if sec.get("fundamental"): lines.append(f"🏛️ <b>法人</b>：{_esc(sec['fundamental'])}")
        if sec.get("strategy"):    lines.append(f"📚 <b>策略</b>：{_esc(sec['strategy'])}")

    # 策略徽章詳列
    if strategies:
        lines.append("")
        lines.append("📚 <b>飆股在線等策略匹配</b>")
        for m in strategies[:8]:
            ico = {"buy":"🟢","sell":"🔴","warning":"🟡","info":"🔵"}.get(m.get("type"), "⚪")
            lines.append(f"　{ico} Ep{m.get('ep')} {_esc(m.get('name'))}：{_esc(m.get('signal',''))}")
        if len(strategies) > 8:
            lines.append(f"　...（共 {len(strategies)} 條）")

    # 法人目標價
    if targets and targets.get("median") is not None:
        lines.append("")
        lines.append(
            f"🏛️ 分析師 {targets.get('analysts',0)} 位｜評等 {_esc(targets.get('rec_label','—'))}\n"
            f"　目標價：高 {_fmt_price(targets.get('high'))} ／ "
            f"中 {_fmt_price(targets.get('median'))} ／ "
            f"低 {_fmt_price(targets.get('low'))}"
        )

    # 籌碼摘要
    if holding and not is_etf:
        cz = holding.get("cost_zone")
        cz_str = ""
        if cz:
            cz_str = f"｜📍成本區 {_fmt_price(cz.get('low'))}~{_fmt_price(cz.get('high'))}（{cz.get('dist',0):+.1f}%）"
        lines.append(
            f"🏦 大戶 {holding.get('major',0)}%｜千張 {holding.get('whale',0)}%｜"
            f"散戶 {holding.get('small',0)}%｜股東 {holding.get('persons',0):,}人{cz_str}"
        )

    return "\n".join(lines)


def fmt_breakout_detail(b):
    """突破候選的訊息格式"""
    sid  = b.get("sid",""); name = b.get("name","")
    cur  = b.get("current"); neck = b.get("neckline")
    dist = b.get("dist_pct"); vr  = b.get("vol_ratio")
    pat  = b.get("pattern_name","")
    sec_lines = [
        f"<b>{_esc(sid)} {_esc(name)}</b> ⚡ 即將突破",
        f"型態：{_esc(pat)}",
        f"現價 {cur}　頸線 {neck}　距離 {dist:+.2f}%　量比 {vr}x",
    ]
    return "\n".join(sec_lines)


def fmt_flagship_detail(s):
    """飆股區候選的簡化訊息"""
    sid  = s.get("sid",""); name = s.get("name","")
    sig  = s.get("sig","")
    risk = s.get("risk","")
    score = s.get("score", "")
    sec  = s.get("sections", {}) or {}
    pat  = s.get("pattern", {}) or {}

    lines = [
        f"<b>{_esc(sid)} {_esc(name)}</b> {_esc(sig)}",
        f"評分 {score}　{_esc(risk)}",
    ]
    if pat.get("name") and pat.get("name") != "觀察中":
        lines.append(f"型態：{_esc(pat.get('name'))}（{_esc(pat.get('cat',''))}）")
    if sec.get("trend"):     lines.append(f"📈 {_esc(sec['trend'])}")
    if sec.get("technical"): lines.append(f"📊 {_esc(sec['technical'])}")
    if s.get("strat_action"):
        lines.append(f"🎯 {_esc(s.get('strat_action'))}")
    return "\n".join(lines)


def fmt_holdings_dividends(holdings):
    """持股配股配息明細：列出每支持股的現金/股票股利 + 除息日 + 來源"""
    if not holdings: return "目前沒有持股資料"
    has_any = any((h.get("dividend_amount") or 0) > 0 for h in holdings)
    if not has_any:
        return "ℹ️ 目前所有持股皆無已宣布的配股配息資料。\n（公司股東會通過後可於 xlsx 的「2026 現金股利」欄手填，或等 FinMind 抓到 TWSE 公告）"

    tot_cash = tot_stock = 0.0
    lines = [f"💰 <b>我的持股配股配息明細</b>", ""]
    for h in holdings:
        sid    = h.get("sid", "")
        name   = h.get("name", "")
        is_etf = h.get("is_etf", False)
        shares = h.get("shares", 0) or 0
        cash_ps  = h.get("dividend_cash_per_share", 0) or 0
        stock_ps = h.get("dividend_stock_per_share", 0) or 0
        cash_amt  = h.get("dividend_cash_amount", 0) or 0
        stock_amt = h.get("dividend_stock_amount", 0) or 0
        new_shrs  = h.get("dividend_new_shares", 0) or 0
        div_amt   = cash_amt + stock_amt
        next_ex   = h.get("dividend_next_ex_date") or ""
        src       = h.get("dividend_source", "finmind")
        events    = h.get("dividend_events", []) or []

        tot_cash += cash_amt; tot_stock += stock_amt
        tag      = "🟣" if is_etf else "🔵"
        if div_amt <= 0:
            lines.append(f"{tag} <b>{_esc(sid)}</b> {_esc(name)}　<i>—（未公告）</i>")
            continue
        src_tag  = "📝手填" if src == "manual" else "🔎FinMind"
        status   = "公告中" if (src == "manual" and not next_ex) else f"除息 {next_ex}"
        # 主行
        lines.append(f"{tag} <b>{_esc(sid)}</b> {_esc(name)}　<i>{src_tag}・{status}</i>")
        # 現金股利
        if cash_amt > 0:
            total_shr_int = int(round(shares * 1000))
            lines.append(f"　💵 現金 <b>{cash_ps:.4f}</b> 元/股 × {total_shr_int:,} 股 = <b>{cash_amt:,.0f}</b>")
        # 股票股利
        if stock_amt > 0:
            lines.append(f"　📦 股票 <b>{stock_ps:.4f}</b> 元/股 → 配股 {new_shrs:,.2f} 股 × 現價 = <b>{stock_amt:,.0f}</b>")
        # 事件明細（若 FinMind 有發放日 / 多筆）
        if src == "finmind" and events:
            for ev in events:
                kind = "現金" if ev.get("kind") == "cash" else "股票"
                pay  = ev.get("pay_date","") or "—"
                lines.append(f"　・{kind}除權息 {ev.get('ex_date','')} → 發放 {pay}：{ev.get('amount',0):.4f} 元/股")

    tot_div = tot_cash + tot_stock
    lines += ["", "━━━━━━━━━━━━━━",
              f"💎 <b>合計：現金 {tot_cash:,.0f}　股票等值 {tot_stock:,.0f}　共 {tot_div:,.0f}</b>"]
    return "\n".join(lines)


def fmt_holdings_summary(holdings):
    """持股總覽（一張訊息）"""
    if not holdings: return "目前沒有持股資料"
    total_mv  = sum(h.get("market_value", 0) for h in holdings)
    total_pl  = sum(h.get("pl_amt", 0) for h in holdings)
    total_div = sum(h.get("dividend_amount", 0) or 0 for h in holdings)
    total_tr  = total_pl + total_div

    head = [f"📊 <b>我的持股總覽（{len(holdings)} 支）</b>",
            f"市值 {total_mv:,.0f}　損益 {total_pl:+,.0f}"]
    if total_div > 0:
        head.append(f"📢 已宣布配股配息 {total_div:+,.0f}　總報酬 {total_tr:+,.0f}")
    lines = head + [""]
    for h in holdings:
        sid = h.get("sid",""); name = h.get("name","")
        cur = h.get("current",0); pct = h.get("pl_pct",0)
        action = h.get("action","")
        is_etf = h.get("is_etf", False)
        tag = "🟣" if is_etf else "🔵"
        lines.append(f"{tag} <b>{_esc(sid)}</b> {_esc(name)}　{cur}　{pct:+.1f}%　<i>{_esc(action)}</i>")
    return "\n".join(lines)


def fmt_breakouts_summary(breakouts):
    if not breakouts: return "今日無即將突破候選"
    lines = [f"⚡ <b>即將突破候選（{len(breakouts)} 支）</b>", ""]
    for b in breakouts:
        sid = b.get("sid",""); name = b.get("name","")
        pat = b.get("pattern_name","")
        dist = b.get("dist_pct",0); vr = b.get("vol_ratio",0)
        lines.append(f"⚡ <b>{_esc(sid)}</b> {_esc(name)}　{_esc(pat)}　距頸線 {dist:+.1f}%　量比 {vr}x")
    return "\n".join(lines)


def fmt_total_assets(holdings):
    """總資產彙總：持股市值 + 現金 + 保險（USD 自動換匯）+ 勞退/勞保"""
    try:
        from holdings_loader import load_holdings_and_assets
        from assets_extras import load_extras
        from insurance_estimator import (aia_estimate, pru_estimate,
                                          labor_pension_default, labor_insurance_estimate)
    except Exception as e:
        return f"⚠️ 載入失敗：{e}"

    # 持股市值
    stock_mv  = sum(h.get("market_value", 0) for h in (holdings or []))
    stock_cv  = sum(h.get("cost_value", 0)   for h in (holdings or []))
    stock_pl  = stock_mv - stock_cv if stock_cv else 0

    # 從 xlsx 讀取現金
    _, others, src_path, file_date = load_holdings_and_assets()
    cash_items = []
    for it in others:
        if "現金" in it["name"]:
            cash_items.append((it["name"], it.get("value"), it.get("note","")))

    # ── 保險 / 勞退 / 勞保 ──
    # 用戶可以用「設定 AIA 1234567」覆蓋自動估算
    extras = load_extras()

    def get_with_override(std_key, auto_fn):
        """若用戶有手動設定（且非 0）就用手動值；否則用自動估算"""
        manual = extras.get(std_key)
        if manual:
            return {
                "value_twd": float(manual),
                "source":    "manual",
                "updated":   extras.get("_updated", {}).get(std_key, ""),
                "note":      "",
            }
        try:
            est = auto_fn()
            est["source"] = "auto"
            return est
        except Exception as e:
            return {"value_twd": 0, "source": "error", "note": str(e)}

    aia = get_with_override("AIA", aia_estimate)
    pru = get_with_override("保誠", pru_estimate)
    lab = get_with_override("勞退", labor_pension_default)
    lib = get_with_override("勞保", labor_insurance_estimate)

    # 計算總計
    total = stock_mv
    for _, v, _ in cash_items:
        if v: total += v
    for d in (aia, pru, lab, lib):
        total += d.get("value_twd", 0)

    lines = [
        f"💎 <b>總資產彙總</b>　{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if stock_cv:
        lines.append(f"📈 <b>持股市值</b>　<b>{stock_mv:,.0f}</b>　"
                     f"({'+' if stock_pl>=0 else ''}{stock_pl:,.0f}　{stock_pl/stock_cv*100:+.2f}% vs 成本)")
    else:
        lines.append(f"📈 <b>持股市值</b>　<b>{stock_mv:,.0f}</b>")

    # 已宣布尚未派發的配股配息（cash + 股票股利等值）
    stock_div_cash  = sum((h.get("dividend_cash_amount") or 0)  for h in (holdings or []))
    stock_div_stock = sum((h.get("dividend_stock_amount") or 0) for h in (holdings or []))
    stock_div = stock_div_cash + stock_div_stock
    if stock_div > 0:
        total_return = stock_pl + stock_div
        bits = []
        if stock_div_cash  > 0: bits.append(f"現金 {stock_div_cash:,.0f}")
        if stock_div_stock > 0: bits.append(f"股票等值 {stock_div_stock:,.0f}")
        lines.append(f"📢 <b>已宣布配股配息</b>　<b>{stock_div:,.0f}</b>　"
                     f"<i>（{' + '.join(bits)}；總報酬 {total_return:+,.0f}）</i>")

    lines.append("")
    lines.append("<b>💵 現金</b>")
    for nm, v, note in cash_items:
        if v:
            lines.append(f"　・{_esc(nm)}：<b>{v:,.0f}</b>")
        else:
            lines.append(f"　・{_esc(nm)}：—（無金額）")

    lines += ["", "<b>🛡️ 保險（USD 自動換匯）</b>"]
    # AIA
    if aia.get("source") == "auto":
        lines.append(f"　・AIA 充裕未來1：<b>{aia['value_twd']:,.0f}</b>")
        lines.append(f"　　<i>USD ${aia.get('value_usd',0):,.0f} × {aia.get('rate',0):.3f}（保單第 {aia.get('policy_year','?')} 年）</i>")
    elif aia.get("source") == "manual":
        lines.append(f"　・AIA 充裕未來1：<b>{aia['value_twd']:,.0f}</b>　<i>（手動設定 {aia.get('updated','')}）</i>")
    else:
        lines.append(f"　・AIA 充裕未來1：⚠️ 估算失敗")
    # 保誠
    if pru.get("source") == "auto":
        lines.append(f"　・保誠雋升：<b>{pru['value_twd']:,.0f}</b>")
        lines.append(f"　　<i>USD ${pru.get('value_usd',0):,.0f} × {pru.get('rate',0):.3f}（保單第 {pru.get('policy_year','?')} 年）</i>")
    elif pru.get("source") == "manual":
        lines.append(f"　・保誠雋升：<b>{pru['value_twd']:,.0f}</b>　<i>（手動設定 {pru.get('updated','')}）</i>")
    else:
        lines.append(f"　・保誠雋升：⚠️ 估算失敗")

    lines += ["", "<b>👷 退休 / 社會保險</b>"]
    # 勞退
    if lab.get("source") == "auto":
        lines.append(f"　・勞工退休金：<b>{lab['value_twd']:,.0f}</b>　<i>（{lab.get('note','')}）</i>")
    elif lab.get("source") == "manual":
        lines.append(f"　・勞工退休金：<b>{lab['value_twd']:,.0f}</b>　<i>（手動設定 {lab.get('updated','')}）</i>")
    # 勞保
    if lib.get("source") == "auto":
        lines.append(f"　・勞工保險（一次請領）：<b>{lib['value_twd']:,.0f}</b>")
        lines.append(f"　　<i>{lib.get('note','')}</i>")
        if lib.get("monthly_note"):
            lines.append(f"　　<i>{lib['monthly_note']}</i>")
    elif lib.get("source") == "manual":
        lines.append(f"　・勞工保險：<b>{lib['value_twd']:,.0f}</b>　<i>（手動設定 {lib.get('updated','')}）</i>")
    else:
        lines.append(f"　・勞工保險：⚠️ 估算失敗")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━",
        f"💎 <b>總價值估算：{total:,.0f}</b>",
        "",
        "<b>更新方式：</b>",
        "　<code>設定 AIA 5000000</code>（覆蓋自動估算）",
        "　<code>設定 保誠 900000</code>",
        "　<code>設定 勞退 2546977</code>",
        "　<code>設定 勞保 300000</code>",
    ]
    return "\n".join(lines)


def fmt_help():
    return (
        "🤖 <b>台股查詢機器人　全部指令</b>\n"
        "\n"
        "<b>🔍 個股查詢</b>\n"
        "・輸入股票<b>代碼</b>（如 <code>2330</code>、<code>00708L</code>）\n"
        "・輸入股票<b>名稱</b>（如 <code>台積電</code>）\n"
        "・支援從句子抽代碼（如 <code>2330怎樣</code>）\n"
        "　→ 回詳細分析（K 線、籌碼、法人、策略、配息、總報酬）\n"
        "\n"
        "<b>📊 持股相關</b>\n"
        "・<code>持股</code> ／ <code>我的持股</code> ／ <code>/holdings</code>\n"
        "　→ 持股總覽（市值、損益、配息、總報酬）\n"
        "・<code>持股配息</code> ／ <code>配股配息</code> ／ <code>配息明細</code> ／ <code>股利明細</code> ／ <code>/dividends</code>\n"
        "　→ 每支持股的配股配息明細 💰\n"
        "・<code>持股更新</code> ／ <code>更新持股</code> ／ <code>/refresh</code>\n"
        "　→ 偵測最新 xlsx 並重跑圖表 🔄\n"
        "\n"
        "<b>📈 行情</b>\n"
        "・<code>突破</code> ／ <code>即將突破</code> ／ <code>/breakouts</code>\n"
        "　→ 今日即將突破候選\n"
        "・<code>飆股</code> ／ <code>名單</code> ／ <code>今日飆股</code> ／ <code>/flagship</code>\n"
        "　→ 今日飆股名單（前 10）\n"
        "\n"
        "<b>💎 總資產</b>\n"
        "・<code>總資產</code> ／ <code>資產</code> ／ <code>/assets</code>\n"
        "　→ 持股 + 現金 + 保險 + 勞退/勞保 + 已宣布股利 彙總\n"
        "\n"
        "<b>⚙️ 設定（覆蓋自動估算）</b>\n"
        "・<code>設定 AIA 1500000</code>\n"
        "・<code>設定 保誠 800000</code>\n"
        "・<code>設定 勞退 2546977</code>\n"
        "・<code>設定 勞保 870200</code>\n"
        "\n"
        "<b>❓ 幫助</b>\n"
        "・<code>幫助</code> ／ <code>說明</code> ／ <code>指令</code> ／ <code>?</code> ／ <code>/help</code>\n"
        "　→ 顯示本指令清單\n"
        "\n"
        "<b>📝 資料來源</b>\n"
        "・行情：當日飆股圖表 HTML（15:35 自動產生）\n"
        "・持股清單：「資產與持股明細更新案夾」內最新 xlsx\n"
        "・現金：xlsx 第 B 欄；保險/勞退/勞保由「設定」指令更新\n"
        "・配股配息：xlsx 手填「2026 現金股利 / 股票股利 / 除息日」優先，FinMind 自動補"
    )


def split_message(text, limit=4000):
    """把長訊息切成多段（Telegram 4096 字元上限）"""
    if len(text) <= limit:
        return [text]
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts
