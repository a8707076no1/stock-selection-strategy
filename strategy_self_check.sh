#!/bin/bash
# 飆股策略自我檢測 — 每週日跑一次，確認 V34 命中率仍 ≥ 70%
# 若連續 2 週低於 70%，會在 logs 留警告，提示要重新跑 backtest_harness 迭代

BASE="$HOME/Desktop/Stock Selection Strategy"
PY="$HOME/stock_env/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$BASE"
LOG="$BASE/logs/strategy_check.log"
mkdir -p "$BASE/logs"

echo "═════════════════════════════════════════" >> "$LOG"
echo "[$(date '+%F %T')] 飆股策略自我檢測" >> "$LOG"
echo "═════════════════════════════════════════" >> "$LOG"

# 跑 backtest_harness（會印出最佳策略命中率）
RESULT=$("$PY" backtest_harness.py 2>&1 | tail -50)
echo "$RESULT" >> "$LOG"

# 抓出 V34 的命中率
V34_HIT=$(echo "$RESULT" | grep -oE "V34_v31_rs_ma60.*?[0-9]+\.[0-9]+%" | grep -oE "[0-9]+\.[0-9]+%" | head -1 | tr -d '%')

if [ -z "$V34_HIT" ]; then
  echo "⚠️ 無法抓到 V34 命中率，可能策略已失效" >> "$LOG"
  V34_HIT=0
fi

echo "📊 V34 當前 +10% 命中率：${V34_HIT}%" >> "$LOG"

# 檢查門檻
THRESHOLD=70
HIT_INT=${V34_HIT%.*}
if [ "${HIT_INT:-0}" -lt "$THRESHOLD" ]; then
  echo "⚠️ 命中率 ${V34_HIT}% 低於 ${THRESHOLD}% 門檻！" >> "$LOG"
  # 計數連續低於門檻的次數
  COUNTER_FILE="$BASE/cache/strategy_fail_count.txt"
  COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
  COUNT=$((COUNT + 1))
  echo "$COUNT" > "$COUNTER_FILE"
  echo "   連續未達標次數：$COUNT" >> "$LOG"

  if [ "$COUNT" -ge 2 ]; then
    echo "🚨 連續 $COUNT 週未達標，建議手動執行：" >> "$LOG"
    echo "   $PY backtest_harness.py  # 看是否需新增策略變體" >> "$LOG"
    # Telegram 通知
    if [ -n "$STOCK_TG_TOKEN" ] && [ -n "$STOCK_TG_CHAT" ]; then
      MSG="⚠️ 飆股策略 V34 連續 $COUNT 週命中率 < 70%（最新 ${V34_HIT}%）。建議重跑 backtest_harness.py 看是否需要更新策略。"
      curl -s "https://api.telegram.org/bot${STOCK_TG_TOKEN}/sendMessage" \
        -d "chat_id=${STOCK_TG_CHAT}" -d "text=${MSG}" > /dev/null 2>&1
    fi
  fi
else
  echo "✅ 命中率 ${V34_HIT}% 達標！" >> "$LOG"
  # 重設計數器
  echo "0" > "$BASE/cache/strategy_fail_count.txt"
fi
