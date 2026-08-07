"""
管理 asset_extras.json：使用者透過 Telegram 設定的保險/勞退/勞保預估值。

xlsx 沒有這些項目的當日金額，所以由使用者隨時用「設定 AIA 1500000」更新。
"""
import os, json, re
from datetime import datetime

CACHE_DIR = os.path.expanduser("~/Desktop/Stock Selection Strategy/cache")
EXTRAS_FILE = os.path.join(CACHE_DIR, "asset_extras.json")

# 標準 key → 顯示名稱（也是 xlsx 對應名）
KEY_LABEL = {
    "AIA":     "AIA 充裕未來1",
    "保誠":    "保誠雋升",
    "勞退":    "勞工退休金",
    "勞保":    "勞工保險",
}

# 反向：使用者輸入字串 → 標準 key
ALIASES = {
    "AIA": ["aia", "AIA", "充裕未來", "充裕未來1"],
    "保誠": ["保誠", "保誠雋升", "雋升", "prudential"],
    "勞退": ["勞退", "勞工退休", "勞工退休金", "退休金"],
    "勞保": ["勞保", "勞工保險"],
}


def normalize_key(token):
    """把使用者輸入的關鍵字（如「AIA」「保誠」「勞退」）轉成標準 key"""
    t = token.strip()
    for std, names in ALIASES.items():
        if t == std or t.lower() in [n.lower() for n in names]:
            return std
    return None


def load_extras():
    if os.path.exists(EXTRAS_FILE):
        try:
            with open(EXTRAS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_extras(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(EXTRAS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_value(key, value):
    """設定某項的預估金額。回傳 (ok, msg)"""
    std = normalize_key(key)
    if not std:
        return False, f"未知項目「{key}」，可用：AIA、保誠、勞退、勞保"
    try:
        v = float(str(value).replace(",", "").replace("元", "").strip())
    except (ValueError, TypeError):
        return False, f"金額「{value}」無效"
    if v < 0:
        return False, "金額必須 ≥ 0"

    extras = load_extras()
    extras[std] = v
    extras.setdefault("_updated", {})[std] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_extras(extras)
    return True, f"✅ {KEY_LABEL[std]} 已更新為 <b>{v:,.0f}</b> 元"


def get_value(key):
    """取得某項的當前金額（含時間戳）"""
    extras = load_extras()
    std = normalize_key(key) or key
    v = extras.get(std)
    upd = extras.get("_updated", {}).get(std)
    return v, upd


def parse_set_command(text):
    """解析「設定 AIA 1500000」格式，回傳 (key, value) 或 (None, None)"""
    # 中英文空白皆可
    m = re.match(r"^(?:設定|/set)\s+([一-鿿A-Za-z]+)\s+([\d,，]+\.?\d*)$", text.strip())
    if m:
        return m.group(1), m.group(2).replace(",", "").replace("，", "")
    return None, None
