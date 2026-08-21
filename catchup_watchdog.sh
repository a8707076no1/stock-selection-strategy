#!/bin/bash
# 永遠醒著的 catchup watchdog（v2 - 移除時間視窗限制）
# 每 15 分鐘檢查：
#   1. 平日 15:40 之後沒有當日飆股圖表 → 立即補跑（不論幾點）
#   2. 平日 / 週六 06:30 之後沒有 us_premarket json → 立即補跑
#   3. 啟動時若 Mac 剛醒（前 X 天/Y 小時沒紀錄）→ 補跑昨日 + 前日的交易日報告

BASE="${STOCK_BASE_DIR:-$HOME/Desktop/Stock Selection Strategy}"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"
LOG="$BASE/logs/catchup_watchdog.log"
mkdir -p "$BASE/logs"

# env 由 ~/.zlogin 或 LaunchAgent plist 提供（見 .env.example）
export STOCK_TG_TOKEN
export STOCK_TG_CHAT
export STOCK_TG_CHAT_EXTRA
export STOCK_BASE_DIR="$BASE"

cd "$BASE"

log() {
  echo "[$(date '+%F %T')] $1" >> "$LOG"
}

tg() {
  curl -s "https://api.telegram.org/bot${STOCK_TG_TOKEN}/sendMessage" \
    -d "chat_id=${STOCK_TG_CHAT}" --data-urlencode "text=$1" \
    -d "parse_mode=HTML" > /dev/null 2>&1
}

is_weekday_for_date() {
  # 接受 YYYYMMDD 格式日期，回 0 代表平日，1 代表週末
  local d=$1
  local wday=$(date -j -f "%Y%m%d" "$d" "+%u" 2>/dev/null)
  [ "$wday" -ge 1 ] && [ "$wday" -le 5 ]
}

is_weekday() {
  d=$(date '+%u')
  [ "$d" -ge 1 ] && [ "$d" -le 5 ]
}

run_full_pipeline() {
  local ds=$1
  log "🔄 補跑 ${ds} 完整流程: daily_update → screener → analyst_targets → chart → cloud_sync"
  "$PY" daily_yahoo_update.py >> "$LOG" 2>&1
  "$PY" taiwan_stock_screener_v3.py >> "$LOG" 2>&1
  "$PY" analyst_targets_scraper.py >> "$LOG" 2>&1
  "$PY" generate_chart.py >> "$LOG" 2>&1
  /bin/bash "$BASE/sync_to_cloud.sh" >> "$LOG" 2>&1
  if [ -f "$BASE/飆股圖表_${ds}.html" ]; then
    log "✅ ${ds} 補跑成功"
    return 0
  else
    log "❌ ${ds} 補跑失敗"
    return 1
  fi
}

log "═══ catchup_watchdog v2 啟動 PID=$$ ═══"

# ── 啟動時掃描：補跑「過去」交易日的缺漏，不含今日 ──
log "🔍 啟動掃描：檢查過去 5 個交易日缺漏（跳過今日 + 週末）"
TODAY_HM=$(date '+%H%M')
TODAY=$(date '+%Y%m%d')
MISSING=()
# i=0 是今天，i=1 是昨天... 跳過 i=0（今天）除非已過 15:40 收盤後
START_I=1
if is_weekday && [ "$TODAY_HM" -ge "1540" ]; then
  START_I=0   # 平日 15:40 後，今天也算
fi
for i in $(seq $START_I 7); do
  D=$(date -v-${i}d '+%Y%m%d')
  if is_weekday_for_date "$D"; then
    if [ ! -f "$BASE/飆股圖表_${D}.html" ]; then
      MISSING+=("$D")
    fi
  fi
  [ "${#MISSING[@]}" -ge 3 ] && break
done

if [ "${#MISSING[@]}" -gt 0 ]; then
  log "📋 發現 ${#MISSING[@]} 個過去交易日缺報告：${MISSING[*]}"
  tg "🔄 <b>watchdog 啟動掃描</b>%0A過去交易日缺報告：${MISSING[*]}%0A<i>（不含今日 — 盤中不補跑）</i>"
  # 注意：補跑只能用當前 cache，無法回到過去日期。所以這裡只記錄不執行。
  # 真正的補跑由「主循環」在 15:40 後執行（用該日收盤資料）。
else
  log "✅ 啟動掃描：過去 5 個交易日報告齊全（或皆為週末/今日盤中）"
fi

# ── 主循環：每 15 分鐘檢查 ──
while true; do
  TODAY=$(date '+%Y%m%d')
  HM=$(date '+%H%M')

  # ── 1. 美股盤前（06:30 之後）──
  if [ "$HM" -ge "0630" ] && [ "$HM" -lt "1000" ]; then
    if [ ! -f "$BASE/logs/us_premarket_${TODAY}.json" ]; then
      log "⚠️ 缺 us_premarket_${TODAY}，補跑"
      "$PY" us_premarket_analyzer.py >> "$LOG" 2>&1
    fi
  fi

  # ── 1.5 林漢偉分析 (weekend/premarket/postmarket) ──
  WDAY=$(date '+%u')                     # 1=Mon...6=Sat,7=Sun
  TODAY_DASH=$(date '+%Y-%m-%d')

  # helper：檢查 cache/<file> 的 last_updated 是不是今天，若不是就跑 mode
  check_lin_hanwei() {
    local mode=$1 file=$2 label=$3
    local hpath="$BASE/cache/$file"
    local last_up=""
    if [ -f "$hpath" ]; then
      last_up=$(grep -oE '"last_updated": *"[^"]+"' "$hpath" | head -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')
    fi
    if [ "$last_up" != "$TODAY_DASH" ]; then
      log "⚠️ lin_hanwei $label 今日尚未處理（last=$last_up），啟動 mode=$mode"
      "$PY" lin_hanwei_daily.py --mode "$mode" >> "$LOG" 2>&1 &
    fi
  }

  # 週末特別版：週六 12:30 後 / 週日整天
  if { [ "$WDAY" = "6" ] && [ "$HM" -ge "1230" ]; } || [ "$WDAY" = "7" ]; then
    check_lin_hanwei weekend lin_hanwei_history.json 週末特別版
  fi

  # 盤前解盤：平日 08:30 之後
  if [ "$WDAY" -ge "1" ] && [ "$WDAY" -le "5" ] && [ "$HM" -ge "0830" ]; then
    check_lin_hanwei premarket lin_hanwei_premarket_history.json 盤前解盤
  fi

  # 盤後解盤：平日 16:30 之後
  if [ "$WDAY" -ge "1" ] && [ "$WDAY" -le "5" ] && [ "$HM" -ge "1630" ]; then
    check_lin_hanwei postmarket lin_hanwei_postmarket_history.json 盤後解盤
  fi

  # ── 1.6 理財達人秀（平日 21:15 之後，開機後補跑）──
  if [ "$WDAY" -ge "1" ] && [ "$WDAY" -le "5" ] && [ "$HM" -ge "2115" ]; then
    HPATH="$BASE/cache/financial_expert_history.json"
    NEED_RUN=1
    if [ -f "$HPATH" ]; then
      # 檢查 history[TODAY_DASH] 是否存在
      if grep -q "\"$TODAY\"" "$HPATH" 2>/dev/null; then
        NEED_RUN=0
      fi
    fi
    if [ "$NEED_RUN" = "1" ]; then
      log "⚠️ 理財達人秀 $TODAY 未處理，啟動抓取"
      "$PY" financial_expert_daily.py >> "$LOG" 2>&1 &
    fi
  fi
  # 假日開機時檢查前一工作日是否漏抓（週六/日開機 → 補週五的）
  if [ "$WDAY" -ge "6" ] || [ "$WDAY" = "7" ]; then
    for i in 1 2 3; do
      D=$(date -v-${i}d '+%Y%m%d')
      WD=$(date -j -f "%Y%m%d" "$D" "+%u" 2>/dev/null)
      [ "$WD" -ge "1" ] && [ "$WD" -le "5" ] || continue
      HPATH="$BASE/cache/financial_expert_history.json"
      if [ -f "$HPATH" ] && grep -q "\"$D\"" "$HPATH" 2>/dev/null; then
        continue
      fi
      log "⚠️ 理財達人秀 補跑 $D（週末開機補漏）"
      "$PY" financial_expert_daily.py --date $D >> "$LOG" 2>&1 &
      break   # 一次補一日避免同時多支 Whisper 塞爆
    done
  fi

  # ── 2. 平日盤後（15:40 後檢查，移除 19:00 上限！）──
  if is_weekday && [ "$HM" -ge "1540" ]; then
    if [ ! -f "$BASE/飆股圖表_${TODAY}.html" ]; then
      log "⚠️ 缺飆股圖表_${TODAY}（現在 ${HM}），補跑"
      tg "⚠️ <b>catchup_watchdog 補跑</b>%0A偵測到今日 (${TODAY}) 缺飆股圖表，立即補跑..."
      run_full_pipeline "$TODAY"
      if [ -f "$BASE/飆股圖表_${TODAY}.html" ]; then
        tg "✅ <b>補跑成功</b>%0A飆股圖表_${TODAY}.html 已產生"
      else
        tg "❌ <b>補跑失敗</b>%0A請手動檢查！"
      fi
    fi
  fi

  sleep 900
done
