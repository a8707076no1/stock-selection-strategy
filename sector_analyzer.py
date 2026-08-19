"""
族群輪動分析模組（Sector Rotation Analyzer）— 細分子族群版
============================================================
基於用戶提供的台股實戰族群分類（7 大類、30+ 子族群）

設計理念：
  台股資金輪動是在「子族群」層級（例如「散熱液冷」「AI 伺服器 ODM」「重電四雄」）
  而非 TWSE 官方 13 個產業大類。法人/投信通常一次重押一個子族群。
  例如：2024 散熱液冷 → 2025 上半 ABF 載板 → 2025 下半 矽光子 CPO → 2026 重電。

提供：
  1. SUBSECTORS — 30+ 子族群定義（含代表股 + 圖示）
  2. classify_stock(sid, industry_str) — 將股票分類到子族群
  3. compute_subsector_strength(pc, classifications, cutoff)
  4. sector_filter_v42(v42_hits, classifications, ranking)
"""
import os, json, statistics
from datetime import datetime
import pandas as pd
import numpy as np

# 確保此模組已 import statistics（compute_subsector_strength 內用）


# ─────────────────────────────────────────────────────────────
# 46 個微觀子族群 — 法人桌（Trading Desk）級分類
# ═══════════════════════════════════════════════════════════════
# 8 大領域：
#   一、半導體精密產業鏈（11 類）
#   二、PCB 與電子零組件（7 類）
#   三、AI 基礎設施（8 類）
#   四、車用電子（6 類）
#   五、光學影像與防禦（4 類）
#   六、重電綠能（4 類）
#   七、先進通訊與工業（3 類）
#   八、傳產民生（3 類）
# ─────────────────────────────────────────────────────────────
SUBSECTORS = {
    # ═══ 一、半導體精密產業鏈（11 類）═══
    "矽智財_ASIC": {
        "parent": "半導體", "alias": "矽智財/ASIC", "icon": "🧠",
        "key_stocks": {"3661":"世芯-KY","3443":"創意","3035":"智原","6643":"M31",
                       "3529":"力旺","8227":"巨有科技"},
    },
    "高速傳輸IC": {
        "parent": "半導體", "alias": "高速傳輸/介面 IC", "icon": "🔌",
        "key_stocks": {"4966":"譜瑞-KY","5269":"祥碩","6104":"創惟","5274":"信驊"},
    },
    "驅動IC_TDDI": {
        "parent": "半導體", "alias": "顯示器驅動 IC", "icon": "📺",
        "key_stocks": {"3034":"聯詠","8016":"矽創","4961":"天鈺","3545":"敦泰","3592":"瑞鼎"},
    },
    "電源管理IC_PMIC": {
        "parent": "半導體", "alias": "電源管理 IC（PMIC）", "icon": "🔋",
        "key_stocks": {"6415":"矽力*-KY","8081":"致新","6138":"茂達","6719":"力智"},
    },
    "晶圓代工_先進製程": {
        "parent": "半導體", "alias": "晶圓代工（先進製程）", "icon": "💎",
        "key_stocks": {"2330":"台積電"},
    },
    "晶圓代工_成熟製程": {
        "parent": "半導體", "alias": "晶圓代工（成熟製程）", "icon": "⚙️",
        "key_stocks": {"2303":"聯電","5347":"世界先進","6770":"力積電"},
    },
    "半導體檢測_MAFA": {
        "parent": "半導體", "alias": "半導體檢測 MA/FA/RA", "icon": "🔬",
        "key_stocks": {"3587":"閎康","6830":"汎銓","3289":"宜特"},
    },
    "先進封裝_CoWoS設備": {
        "parent": "半導體", "alias": "先進封裝/CoWoS設備", "icon": "🛠️",
        "key_stocks": {"3131":"弘塑","3583":"辛耘","6187":"萬潤","2467":"志聖","5443":"均豪"},
    },
    "晶圓設備_廠務": {
        "parent": "半導體", "alias": "晶圓前段設備/廠務", "icon": "🏭",
        "key_stocks": {"3680":"家登","3413":"京鼎","2404":"漢唐","5536":"聖暉*","6532":"瑞耘"},
    },
    "探針卡_測試介面": {
        "parent": "半導體", "alias": "探針卡/測試介面", "icon": "🪡",
        "key_stocks": {"6223":"旺矽","6515":"穎崴","6510":"精測","6683":"雍智科技"},
    },
    "IC封測_OSAT": {
        "parent": "半導體", "alias": "IC封測 OSAT", "icon": "🔲",
        "key_stocks": {"3711":"日月光投控","2449":"京元電子","2441":"超豐","3264":"欣銓","6257":"矽格"},
    },

    # ═══ 二、PCB 與電子零組件（7 類）═══
    "ABF載板": {
        "parent": "PCB", "alias": "ABF載板（載板三雄）", "icon": "🟦",
        "key_stocks": {"3037":"欣興","8046":"南電","3189":"景碩"},
    },
    # BT 載板代表股與 ABF 重疊（3189, 3037 主業是 ABF），合併到 ABF 載板分析
    # 保留分類但不指定代表股，依 industry 字串 fallback 即可
    "CCL銅箔基板": {
        "parent": "PCB", "alias": "高速 CCL（CCL三雄）", "icon": "🧱",
        "key_stocks": {"2383":"台光電","6213":"聯茂","6274":"台燿"},
    },
    "AI_PCB硬板": {
        "parent": "PCB", "alias": "AI伺服器高階硬板（20層+）", "icon": "💽",
        "key_stocks": {"2368":"金像電","3044":"健鼎","5469":"瀚宇博"},
    },
    "軟板_HDI": {
        "parent": "PCB", "alias": "軟板 FPC / HDI", "icon": "🟪",
        "key_stocks": {"2313":"華通","4958":"臻鼎-KY","6269":"台郡"},
    },
    "被動元件_MLCC": {
        "parent": "PCB", "alias": "被動元件 MLCC", "icon": "⚛️",
        "key_stocks": {"2327":"國巨","2492":"華新科"},
    },
    "被動元件_電感電阻": {
        "parent": "PCB", "alias": "被動元件 電感/精密電阻", "icon": "🌀",
        "key_stocks": {"3357":"臺慶科","2478":"大毅","3011":"今皓"},
    },

    # ═══ 三、AI 基礎設施（8 類）═══
    "AI伺服器_ODM": {
        "parent": "AI伺服器", "alias": "AI伺服器ODM", "icon": "🖥️",
        "key_stocks": {"2382":"廣達","3231":"緯創","6669":"緯穎","2356":"英業達","2315":"神達"},
    },
    "散熱_水冷": {
        "parent": "AI伺服器", "alias": "水冷/氣冷散熱", "icon": "❄️",
        "key_stocks": {"3017":"奇鋐","3324":"雙鴻","8996":"高力","6230":"尼得科超眾"},
    },
    "散熱風扇_導熱材料": {
        "parent": "AI伺服器", "alias": "散熱風扇/導熱材料", "icon": "💨",
        "key_stocks": {"2421":"建準","6591":"動力-KY"},
    },
    "晶片扣具_ILM": {
        "parent": "AI伺服器", "alias": "晶片扣具/ILM", "icon": "🔧",
        "key_stocks": {"3653":"健策"},
    },
    "伺服器高階機殼": {
        "parent": "AI伺服器", "alias": "伺服器機殼", "icon": "📦",
        "key_stocks": {"8210":"勤誠","3013":"晟銘電","6117":"迎廣"},
    },
    "伺服器導軌": {
        "parent": "AI伺服器", "alias": "伺服器高階導軌", "icon": "🛤️",
        "key_stocks": {"2059":"川湖","6584":"南俊國際"},
    },
    "伺服器BBU電池": {
        "parent": "AI伺服器", "alias": "BBU備援電池", "icon": "🔋",
        "key_stocks": {"6121":"新普","3211":"順達","6781":"AES-KY"},
    },
    "矽光子_CPO": {
        "parent": "AI伺服器", "alias": "矽光子/CPO", "icon": "✨",
        "key_stocks": {"3081":"聯亞","3363":"上詮","6442":"光聖","3163":"波若威","4908":"前鼎"},
    },

    # ═══ 四、車用電子（6 類）═══
    "車用連接器": {
        "parent": "車用", "alias": "車用連接器/高壓線束", "icon": "🚗",
        "key_stocks": {"6279":"胡連","3665":"貿聯-KY","6197":"佳必琪","6205":"詮欣"},
    },
    "車用功率元件": {
        "parent": "車用", "alias": "車用功率元件/二極體", "icon": "⚡",
        "key_stocks": {"8255":"朋程","5425":"台半","2481":"強茂","3675":"德微"},
    },
    "車用PCB": {
        "parent": "車用", "alias": "車用專屬電路板", "icon": "🟫",
        "key_stocks": {"2355":"敬鵬","3715":"定穎投控","3044":"健鼎"},
    },
    "車載資通訊_HUD": {
        "parent": "車用", "alias": "車載資通訊/HUD/智慧座艙", "icon": "🪟",
        "key_stocks": {"2497":"怡利電","1533":"車王電","3701":"大眾控"},
    },
    "汽車AM售後": {
        "parent": "車用", "alias": "汽車外觀件/碰撞件AM", "icon": "🔧",
        "key_stocks": {"1319":"東陽","1524":"耿鼎","1339":"昭輝"},
    },
    "車用燈具": {
        "parent": "車用", "alias": "車用燈具/LED 模組", "icon": "💡",
        "key_stocks": {"6605":"帝寶","1522":"堤維西","5244":"麗清"},
    },

    # ═══ 五、光學影像與核心防禦（4 類）═══
    "光學_高階手機鏡頭": {
        "parent": "光電", "alias": "智慧手機高階光學鏡頭", "icon": "📷",
        "key_stocks": {"3008":"大立光","3406":"玉晶光"},
    },
    "光學_ADAS無人機": {
        "parent": "光電", "alias": "車用ADAS/無人機鏡頭", "icon": "🛸",
        "key_stocks": {"3362":"先進光","2374":"佳能","3019":"亞光","4976":"佳凌"},
    },
    "CMOS影像感測_CIS": {
        "parent": "光電", "alias": "CMOS 影像感測 CIS", "icon": "📸",
        "key_stocks": {"6271":"同欣電","6789":"采鈺"},
    },
    "記憶體_DRAM_NAND": {
        "parent": "光電", "alias": "記憶體 DRAM/Flash", "icon": "💾",
        "key_stocks": {"2408":"南亞科","2344":"華邦電","3260":"威剛","2451":"創見"},
    },

    # ═══ 六、強韌電網、重電與綠能（4 類）═══
    "高壓GIS": {
        "parent": "重電綠能", "alias": "高壓 GIS（氣體絕緣開關）", "icon": "⚡",
        "key_stocks": {"1513":"中興電"},
    },
    "超高壓變壓器": {
        "parent": "重電綠能", "alias": "超高壓電力變壓器", "icon": "🔌",
        "key_stocks": {"1519":"華城","1503":"士電"},
    },
    "中低壓配電盤": {
        "parent": "重電綠能", "alias": "中低壓配電盤/電機組", "icon": "🔧",
        "key_stocks": {"1514":"亞力","1504":"東元"},
    },
    "綠能_EPC_IPP": {
        "parent": "重電綠能", "alias": "綠能 EPC/IPP（風電/儲能）", "icon": "🌱",
        "key_stocks": {"9958":"世紀鋼","6806":"森崴能源","6869":"雲豹能源","6873":"泓德能源"},
    },

    # ═══ 七、先進通訊與工業自動化（3 類）═══
    "低軌衛星": {
        "parent": "通信", "alias": "低軌衛星地面站元件", "icon": "🛰️",
        "key_stocks": {"3491":"昇達科","3446":"耀登"},
    },
    "資料中心交換器": {
        "parent": "通信", "alias": "資料中心核心交換器", "icon": "📡",
        "key_stocks": {"2345":"智邦"},
    },
    "工業電腦_IPC": {
        "parent": "通信", "alias": "工業電腦/邊緣 AI", "icon": "🤖",
        "key_stocks": {"2395":"研華","6414":"樺漢","2359":"所羅門","8234":"新漢"},
    },

    # ═══ 八、基礎傳產與民生原物料（3 類）═══
    "玻璃陶瓷": {
        "parent": "傳產", "alias": "玻璃陶瓷/光電基板", "icon": "🪟",
        "key_stocks": {"1802":"台玻"},
    },
    "鋼鐵_建材": {
        "parent": "傳產", "alias": "鋼鐵/基礎建材", "icon": "🏗️",
        "key_stocks": {"2002":"中鋼","2006":"東和鋼鐵","2031":"新光鋼"},
    },
    "航運": {
        "parent": "傳產", "alias": "航運（貨櫃/散裝）", "icon": "🚢",
        "key_stocks": {"2603":"長榮","2609":"陽明","2605":"新興","2606":"裕民"},
    },

    # ═══ 九、化學工業細分 ═══
    "電子化學品_半導體用": {
        "parent": "化學", "alias": "電子化學品（半導體/光阻/特化）", "icon": "🧪",
        "key_stocks": {
            "1711":"永光","5234":"達興材料","4711":"永純化","1773":"勝一",
            "3645":"達邁","4725":"信昌化","6582":"申豐","6266":"泰詠",
            "4707":"磐亞","4763":"材料-KY",
        },
    },
    "基礎化工": {
        "parent": "化學", "alias": "基礎化工（PE/PP/PVC等）", "icon": "🧯",
        "key_stocks": {
            "1304":"台聚","1305":"華夏","1308":"亞聚","1310":"台苯",
            "1312":"國喬","1313":"聯成","1314":"中石化","1315":"達新",
        },
    },
    "農化_特用": {
        "parent": "化學", "alias": "農化/特用化學", "icon": "🌿",
        "key_stocks": {
            "1712":"興農","1723":"中碳","1733":"五鼎",
            "1762":"中化生","1737":"臺鹽",
        },
    },

    # ═══ 十、電子通路細分 ═══
    "IC通路_半導體": {
        "parent": "通路", "alias": "IC 通路（半導體大宗）", "icon": "🔁",
        "key_stocks": {
            "3702":"大聯大","3036":"文曄","3028":"增你強","6257":"品佳",
            "8112":"至上","6128":"上福","2493":"揚博",
            "2403":"友尚","3027":"盛達",
        },
    },
    "工業通路_儀器": {
        "parent": "通路", "alias": "工業/儀器通路", "icon": "🔌",
        "key_stocks": {
            "2360":"致茂","3047":"訊舟",
            "3551":"世禾","2491":"古聯",
        },
    },

    # ═══ 補充：常見其他大類（保留少量）═══
    "金控": {
        "parent": "金融", "alias": "大型金控", "icon": "🏦",
        "key_stocks": {"2881":"富邦金","2882":"國泰金","2886":"兆豐金","2891":"中信金","2884":"玉山金"},
    },
    "生技新藥": {
        "parent": "生技", "alias": "生技/新藥", "icon": "💊",
        "key_stocks": {"6446":"藥華藥","6472":"保瑞","1795":"美時"},
    },
}

# ─────────────────────────────────────────────────────────────
# 多重歸屬（Multi-Membership）— 一支股屬於 2-3 個子族群
# 設計：sid → [additional_subsectors]（除了 SUBSECTORS.key_stocks 已歸屬外的「也屬於」）
# ─────────────────────────────────────────────────────────────
MULTI_MEMBERSHIP = {
    # PCB 跨類
    "2313": ["低軌衛星", "AI_PCB硬板"],   # 華通：軟板 + 低軌 + AI 高階板
    "3044": ["車用PCB", "AI_PCB硬板"],     # 健鼎：AI硬板 + 車用 PCB（雙主業）
    "3037": [],                            # 欣興：主要 ABF（BT 已合併）
    "3189": [],                            # 景碩：主要 ABF（BT 已合併）
    "4958": ["AI_PCB硬板"],                # 臻鼎-KY：軟板 + AI 高階板
    "6269": [],                            # 台郡：軟板

    # 半導體跨類 / 檢測 + CPO
    "3587": ["矽光子_CPO", "先進封裝_CoWoS設備"],   # 閎康：MA/FA + CPO 測試 + 先進封裝
    "6830": ["矽光子_CPO"],                        # 汎銓：MA/FA + CPO 測試
    "3289": ["先進封裝_CoWoS設備"],                # 宜特：MA/FA + 先進封裝測試
    "2330": ["先進封裝_CoWoS設備"],                # 台積電：先進製程 + CoWoS
    "3711": ["先進封裝_CoWoS設備"],                # 日月光：OSAT + 先進封裝
    "2449": [],                                    # 京元電子：OSAT 純測
    "6488": ["半導體檢測_MAFA"],                   # 環球晶：矽晶圓 + 材料分析
    "5347": ["記憶體_DRAM_NAND"],                  # 世界先進：成熟製程 + DRAM 代工

    # IC 設計多重
    "2454": ["低軌衛星", "矽智財_ASIC"],             # 聯發科：手機 AP + Starlink 晶片
    "3034": ["電源管理IC_PMIC"],                   # 聯詠：驅動 IC + 電源管理
    "3661": ["AI伺服器_ODM"],                      # 世芯-KY：ASIC + AI 晶片設計
    "3035": ["AI伺服器_ODM"],                      # 智原：ASIC + AI 平台

    # AI 伺服器多重
    "2382": ["低軌衛星", "資料中心交換器"],          # 廣達：ODM + Starlink ODM + 部分網通
    "3231": ["車用PCB"],                            # 緯創：AI ODM + 車用伺服器
    "6669": ["矽光子_CPO"],                         # 緯穎：AI ODM + CPO 主板測試
    "2356": [],                                      # 英業達：AI ODM
    "3017": ["散熱風扇_導熱材料"],                  # 奇鋐：水冷 + 風扇散熱
    "3324": ["散熱風扇_導熱材料"],                  # 雙鴻：水冷 + 風扇散熱
    "3653": ["散熱_水冷"],                           # 健策：ILM 扣具 + 水冷板
    "8996": ["散熱風扇_導熱材料"],                  # 高力：水冷 + 風扇散熱

    # CPO 跨類
    "3081": ["半導體檢測_MAFA"],                   # 聯亞：CPO + 化合物半導體檢測
    "6442": ["低軌衛星"],                          # 光聖：CPO + 衛星光通訊
    "4908": [],                                     # 前鼎：CPO

    # 低軌衛星跨類
    "3491": ["資料中心交換器"],                    # 昇達科：低軌 + 高頻交換器零件
    "3446": [],                                     # 耀登

    # 重電多重
    "1513": ["中低壓配電盤"],                      # 中興電：高壓 GIS + 中低壓配電
    "1519": ["中低壓配電盤"],                      # 華城：超高壓變壓器 + 中低壓
    "1503": ["中低壓配電盤"],                      # 士電：超高壓 + 中低壓

    # 工業電腦多重
    "2395": ["資料中心交換器"],                    # 研華：IPC + Server平台
    "2345": ["低軌衛星"],                          # 智邦：交換器 + 衛星地面站

    # 車用多重
    "6279": ["車用功率元件"],                      # 胡連：連接器 + 部分功率
    "8255": ["車用PCB"],                            # 朋程：功率元件 + 車用 PCB
    "1319": ["車用燈具"],                          # 東陽：AM + 車燈

    # 光學多重
    "3008": [],                                     # 大立光：手機鏡頭
    "3406": ["光學_ADAS無人機"],                  # 玉晶光：手機 + ADAS
    "2374": ["CMOS影像感測_CIS"],                  # 佳能：ADAS + CIS
    "3019": [],                                     # 亞光
}


# 反向查表：sid → list[subsector] (可多重歸屬)
def _build_sid_to_subsectors():
    m = {}
    # 1) 從 SUBSECTORS.key_stocks 抓主要歸屬
    for subsect, info in SUBSECTORS.items():
        for sid in info.get("key_stocks", {}):
            m.setdefault(sid, [])
            if subsect not in m[sid]:
                m[sid].append(subsect)
    # 2) 從 MULTI_MEMBERSHIP 補額外歸屬
    for sid, extras in MULTI_MEMBERSHIP.items():
        m.setdefault(sid, [])
        for sub in extras:
            if sub not in m[sid] and sub in SUBSECTORS:
                m[sid].append(sub)
    return m

SID_TO_SUBSECTORS = _build_sid_to_subsectors()
# 保留單一歸屬版供舊程式用（取第一個）
SID_TO_SUBSECTOR = {sid: subs[0] for sid, subs in SID_TO_SUBSECTORS.items() if subs}


# ─────────────────────────────────────────────────────────────
# TWSE 產業 → 子族群 fallback
# 用於沒被 SID_TO_SUBSECTOR hardcode 的股票
# ─────────────────────────────────────────────────────────────
INDUSTRY_FALLBACK = {
    # ★ 架構決策：所有 TWSE 大類 fallback 歸到「_xxx其他」catch-all
    # 專屬子族群（半導體/AI伺服器/光電等）只留 hardcoded 代表股，避免 fallback 稀釋訊號
    "半導體業":          "_半導體其他",
    "半導體":            "_半導體其他",
    "電腦及週邊設備業":     "_電腦周邊其他",
    "電腦周邊":          "_電腦周邊其他",
    "光電業":            "_光電其他",
    "光電":             "_光電其他",
    "通信網路業":         "_通信其他",
    "通信網路":          "_通信其他",
    "電子零組件業":       "_電子零組件其他",
    "電子零組件":         "_電子零組件其他",
    "電子通路業":         "_電子通路其他",
    "電子通路":          "_電子通路其他",
    "資訊服務業":         "_資訊服務",
    "資訊服務":          "_資訊服務",
    "其他電子業":         "_其他電子",
    "其他電子":          "_其他電子",
    "電機機械":          "_電機機械其他",
    "汽車工業":          "_汽車其他",
    "化學工業":          "_化學其他",
    "生技醫療業":         "生技新藥",
    "化學生技醫療":       "生技新藥",
    "鋼鐵工業":          "鋼鐵_建材",
    "塑膠工業":          "_塑膠",
    "紡織纖維":          "_紡織",
    "水泥工業":          "_水泥",
    "食品工業":          "_食品",
    "金融保險":          "金控",
    "金融":             "金控",
    "建材營造":          "_建材營造",
    "航運業":            "航運",
    "航運":             "航運",
    "觀光餐旅":          "_觀光",
    "觀光":             "_觀光",
    "貿易百貨":          "_貿易百貨",
    "貿易":             "_貿易百貨",
    "玻璃陶瓷":          "玻璃陶瓷",
    "造紙工業":          "_造紙",
    "電器電纜":          "_電器電纜",
    "橡膠工業":          "_橡膠",
    "油電燃氣業":         "_油電燃氣",
    "農業科技業":         "_農業科技",
    "農業科技":          "_農業科技",
    "電子商務業":         "_電子商務",
    "文化創意業":         "_文創",
    "文化創意":          "_文創",
    "綜合":             "_其他",
    "其他":             "_其他",
    "其他業":            "_其他",
    "管理股票":          "_其他",
}

# 自動加入 fallback 子族群到 SUBSECTORS（避免 KeyError + 顯示用 alias）
_AUTO_SUBS = {
    # 大類 catch-all
    "_半導體其他":       "半導體其他（小型 IC 設計/零星）",
    "_電腦周邊其他":     "電腦周邊其他（NB ODM/印表機/顯示器）",
    "_光電其他":         "光電其他（小型光電）",
    "_通信其他":         "通信其他（網通邊緣）",
    "_電子零組件其他":   "電子零組件其他（連接器/小元件）",
    "_電子通路其他":     "電子通路其他",
    "_電機機械其他":     "電機機械其他（小機械/馬達）",
    "_汽車其他":         "汽車其他",
    "_化學其他":         "化學其他",
    # 原本的 fallback
    "_資訊服務": "資訊服務", "_其他電子": "其他電子",
    "_塑膠": "塑膠工業", "_紡織": "紡織纖維",
    "_水泥": "水泥工業", "_食品": "食品工業",
    "_建材營造": "建材營造", "_觀光": "觀光餐旅", "_貿易百貨": "貿易百貨",
    "_造紙": "造紙工業", "_電器電纜": "電器電纜", "_橡膠": "橡膠工業",
    "_油電燃氣": "油電燃氣業", "_農業科技": "農業科技業",
    "_電子商務": "電子商務業", "_文創": "文化創意業",
    "_其他": "其他/未分類",
}
for k, alias in _AUTO_SUBS.items():
    SUBSECTORS[k] = {"parent": "其他", "alias": alias, "icon": "🔹", "key_stocks": {}}


# ─────────────────────────────────────────────────────────────
# 分類函數
# ─────────────────────────────────────────────────────────────
def classify_stock_all(sid, industry_str=""):
    """股票 → 多重子族群清單（支援一股屬於 2-3 族群）
    優先順序：
      1. SID_TO_SUBSECTORS（hardcoded + MULTI_MEMBERSHIP）
      2. INDUSTRY_FALLBACK（依 TWSE 產業，補單一族群）
      3. ["_未分類"]
    """
    if sid in SID_TO_SUBSECTORS and SID_TO_SUBSECTORS[sid]:
        return list(SID_TO_SUBSECTORS[sid])
    if industry_str:
        s = str(industry_str).strip()
        if s in INDUSTRY_FALLBACK:
            return [INDUSTRY_FALLBACK[s]]
        for k, v in INDUSTRY_FALLBACK.items():
            if k in s or s in k:
                return [v]
    return ["_未分類"]


def classify_stock(sid, industry_str=""):
    """單一子族群版（向下相容）— 取多重歸屬中的第一個（主要族群）"""
    subs = classify_stock_all(sid, industry_str)
    return subs[0] if subs else "_未分類"


# 注入 _未分類
SUBSECTORS["_未分類"] = {"parent": "其他", "alias": "未分類", "icon": "❓", "key_stocks": {}}


# ─────────────────────────────────────────────────────────────
# 抓全市場產業資料
# ─────────────────────────────────────────────────────────────
def fetch_all_industries(force=False):
    """從 TWSE/TPEX 抓全部公司產業別，回傳 {sid: {industry, sector, market, name}}"""
    cache_path = os.path.join((os.environ.get("STOCK_BASE_DIR") or os.path.expanduser("~/Desktop/Stock Selection Strategy")), "cache", "stock_industry.json")
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                d = json.load(f)
            if d.get("date"):
                age = (datetime.today() - datetime.strptime(d["date"], "%Y-%m-%d")).days
                if age < 30:
                    return d.get("industries", {})
        except Exception:
            pass

    import requests, urllib3
    urllib3.disable_warnings()
    sess = requests.Session(); sess.verify = False
    sess.headers.update({"User-Agent": "Mozilla/5.0"})

    # TWSE 新版產業代碼（反推驗證）
    industry_map_code = {
        "01":"水泥工業","02":"食品工業","03":"塑膠工業","04":"紡織纖維",
        "05":"電機機械","06":"電器電纜","07":"化學生技醫療","08":"玻璃陶瓷",
        "09":"造紙工業","10":"鋼鐵工業","11":"橡膠工業","12":"汽車工業",
        "13":"建材營造","14":"建材營造","15":"航運業","16":"觀光餐旅",
        "17":"金融保險","18":"貿易百貨","19":"綜合","20":"其他",
        "21":"化學工業","22":"生技醫療業","23":"油電燃氣業",
        "24":"半導體業","25":"電腦及週邊設備業","26":"光電業",
        "27":"通信網路業","28":"電子零組件業","29":"電子通路業",
        "30":"資訊服務業","31":"其他電子業","32":"文化創意業",
        "33":"農業科技業","34":"電子商務業","80":"管理股票","00":"其他",
    }
    # TPEX 上櫃產業代碼
    tpex_ind_code_map = {
        "01":"水泥工業","02":"食品工業","03":"塑膠工業","04":"紡織纖維",
        "05":"電機機械","06":"電器電纜","08":"玻璃陶瓷","09":"造紙工業",
        "10":"鋼鐵工業","11":"橡膠工業","12":"汽車工業","13":"建材營造",
        "14":"航運業","15":"觀光事業","16":"金融保險","17":"貿易百貨",
        "20":"化學工業","21":"生技醫療業","22":"油電燃氣業","23":"半導體業",
        "24":"電腦及週邊設備業","25":"光電業","26":"通信網路業",
        "27":"電子零組件業","28":"電子通路業","29":"資訊服務業","30":"其他電子業",
        "31":"文化創意業","32":"農業科技業","33":"電子商務業","34":"觀光餐旅",
        "80":"管理股票","00":"其他",
    }

    industries = {}
    # 上市
    try:
        r = sess.get("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", timeout=12)
        for item in r.json():
            sid = item.get("公司代號","")
            if not (sid.isdigit() and len(sid)==4): continue
            ind_code = str(item.get("產業別","")).strip().zfill(2)
            ind_name = industry_map_code.get(ind_code, ind_code)
            industries[sid] = {"industry": ind_name, "code": ind_code, "market": "twse",
                                "name": item.get("公司簡稱","")}
        print(f"  上市：{sum(1 for v in industries.values() if v['market']=='twse')} 支")
    except Exception as e:
        print(f"  上市失敗：{e}")
    # 上櫃（mopsfin_t187ap03_O 含產業代碼）
    try:
        r = sess.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", timeout=15)
        for item in r.json():
            sid = str(item.get("SecuritiesCompanyCode","")).strip()
            if not (sid.isdigit() and len(sid)==4): continue
            ind_code = str(item.get("SecuritiesIndustryCode","")).strip().zfill(2)
            ind_name = tpex_ind_code_map.get(ind_code, ind_code)
            if sid not in industries:
                industries[sid] = {"industry": ind_name, "code": ind_code, "market": "tpex",
                                    "name": item.get("CompanyAbbreviation","")}
        print(f"  上櫃補：{sum(1 for v in industries.values() if v['market']=='tpex')} 支")
    except Exception as e:
        print(f"  上櫃失敗：{e}")

    # 每支股加 subsector
    for sid, d in industries.items():
        d["subsector"] = classify_stock(sid, d.get("industry",""))

    out = {"date": datetime.today().strftime("%Y-%m-%d"), "industries": industries}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"  ✅ 共 {len(industries)} 支產業資料")
    return industries


# ─────────────────────────────────────────────────────────────
# 資金輪動階段判定（基於 20 日每日累積軌跡）
# ─────────────────────────────────────────────────────────────
def _classify_rotation_stage(daily_cum):
    """根據 20 日每日累積漲幅軌跡，判定族群處於哪個資金輪動階段
    回傳：{stage, icon, color, advice, peak_day, drawdown}

    7 個階段：
      🚀 起漲期 — 前段平 + 後段加速向上（資金剛切入）
      💪 主升段 — 持續上漲、回檔有限（最佳跟單期）
      ⭐ 加速段 — 後段斜率明顯比前段陡（資金加碼）
      🏔️ 高原期 — 接近頂部、橫盤、量縮（待變盤）
      ⚠️ 衰退期 — 高點回落 3-8%（資金開始撤離）
      🔻 反轉期 — 高點回落 > 8%（資金已撤、空頭確立）
      ⏸️ 整理期 — 無明顯方向（觀望）
    """
    n = len(daily_cum)
    if n < 10:
        return {"stage": "整理", "icon": "⏸️", "color": "#888",
                "advice": "資料不足", "peak_day": 0, "drawdown": 0}

    # 高點位置與回檔幅度
    peak_val = max(daily_cum)
    peak_idx = daily_cum.index(peak_val)
    last_val = daily_cum[-1]
    drawdown = peak_val - last_val   # 從高點回落多少 percentage points

    # 前半段平均 vs 後半段平均
    half = n // 2
    first_half_avg = sum(daily_cum[:half]) / half
    second_half_avg = sum(daily_cum[half:]) / (n - half)
    first_half_slope = (daily_cum[half-1] - daily_cum[0]) if half >= 2 else 0
    second_half_slope = (daily_cum[-1] - daily_cum[half]) if (n - half) >= 2 else 0

    # 近 5 日 vs 前 5 日
    last5_avg = sum(daily_cum[-5:]) / 5
    prev5_avg = sum(daily_cum[-10:-5]) / 5

    # 末端標準差（高原期低波動）
    last5_std = statistics.stdev(daily_cum[-5:]) if len(daily_cum[-5:]) >= 2 else 0

    # 判斷邏輯
    if drawdown >= 8:
        return {"stage": "反轉", "icon": "🔻", "color": "#f85149",
                "advice": f"高點回落 {drawdown:.1f}pt → 資金已撤、空頭確立。空手或反向操作",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if drawdown >= 3 and peak_idx < n - 3:
        return {"stage": "衰退", "icon": "⚠️", "color": "#f0a500",
                "advice": f"第 {peak_idx+1} 日達高點 {peak_val:+.1f}%，已回落 {drawdown:.1f}pt → 資金撤離中，減碼觀望",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if peak_idx >= n - 3 and last5_std < 1.5 and peak_val > 5:
        return {"stage": "高原", "icon": "🏔️", "color": "#56a4ff",
                "advice": f"位於 {peak_val:+.1f}% 高位橫盤，量縮 → 待變盤訊號出現，不追高",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if first_half_avg <= 0 and second_half_avg >= 3 and last_val > 0:
        return {"stage": "起漲", "icon": "🚀", "color": "#3fb950",
                "advice": f"前 10 日整理（均 {first_half_avg:+.1f}%）→ 後 10 日加速（均 {second_half_avg:+.1f}%）→ 真起漲、最佳進場時機",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if second_half_slope > first_half_slope * 1.5 and last5_avg > prev5_avg and last_val > 5:
        return {"stage": "加速", "icon": "⭐", "color": "#58d364",
                "advice": f"後段斜率（{second_half_slope:+.1f}pt）明顯比前段（{first_half_slope:+.1f}pt）陡 → 資金加碼，可順勢加倉",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if last_val > 10 and drawdown < 3 and second_half_avg > first_half_avg:
        return {"stage": "主升", "icon": "💪", "color": "#3fb950",
                "advice": f"持續上漲（累積 {last_val:+.1f}%），回檔有限（{drawdown:.1f}pt） → 主升段，可繼續持有/分批加碼",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if abs(last_val) < 3 and last5_std < 1.5:
        return {"stage": "整理", "icon": "⏸️", "color": "#8b949e",
                "advice": "20 日累積接近 0、波動低 → 等待突破或跌破方向",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    if last_val < -3:
        return {"stage": "弱勢", "icon": "🔻", "color": "#f85149",
                "advice": f"累積 {last_val:+.1f}% → 跌勢中，空手",
                "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}

    return {"stage": "整理", "icon": "⏸️", "color": "#8b949e",
            "advice": "無明顯方向，觀望", "peak_day": peak_idx + 1, "drawdown": round(drawdown, 1)}


# ─────────────────────────────────────────────────────────────
# 計算各子族群即時強度（含短週期 + 資金流偵測）
# ─────────────────────────────────────────────────────────────
def compute_subsector_strength(pc, industries, cutoff_date=None, lookback=20, min_members=2):
    """對每個子族群算多時間尺度動能：
       3日（新進場）、5日（週度）、10日（雙週）、20日（月度）
       + 量比（爆量偵測）+ 動能變化（資金流入流出）"""
    cutoff_dt = (datetime.strptime(cutoff_date, "%Y-%m-%d") if cutoff_date
                 else datetime.today())

    # 多重歸屬：一支股可同時計入多個子族群
    by_sub = {}
    for sid in pc:
        info = industries.get(sid, {})
        # 拿這支股的所有子族群（可能 2-3 個）
        subs = classify_stock_all(sid, info.get("industry", ""))
        for sub in subs:
            by_sub.setdefault(sub, []).append(sid)

    # 保險：再從 SUBSECTORS.key_stocks 補（即使 industry 沒抓到）
    for sub, info in SUBSECTORS.items():
        for sid in info.get("key_stocks", {}):
            if sid in pc and sid not in by_sub.get(sub, []):
                by_sub.setdefault(sub, []).append(sid)
    # 補 MULTI_MEMBERSHIP 額外歸屬
    for sid, extras in MULTI_MEMBERSHIP.items():
        if sid not in pc: continue
        for sub in extras:
            if sub in SUBSECTORS and sid not in by_sub.get(sub, []):
                by_sub.setdefault(sub, []).append(sid)

    stats = {}
    for sub, sids in by_sub.items():
        rets_3d, rets_5d, rets_10d, rets_20d = [], [], [], []
        prev_5d_rets = []
        vol_bursts = []
        member_details = []
        # ★ 每日累積報酬：每支股的「從 20 日前到第 N 日」累積漲幅
        # 結構：daily_cumulative[stock_idx][day 0..19]
        per_stock_cumulative = []
        for sid in sids:
            df = pc.get(sid)
            if df is None or df.empty or len(df) < lookback + 8: continue
            df_cut = df[df["date"] <= cutoff_dt.strftime("%Y-%m-%d")]
            if len(df_cut) < lookback + 8: continue
            close = pd.to_numeric(df_cut["close"], errors="coerce").dropna().values
            vol = pd.to_numeric(df_cut["volume"], errors="coerce").dropna().values
            if len(close) < lookback + 8 or len(vol) < lookback + 8: continue
            r3 = (close[-1]/close[-4] - 1) * 100
            r5 = (close[-1]/close[-6] - 1) * 100
            r10 = (close[-1]/close[-11] - 1) * 100
            r20 = (close[-1]/close[-lookback-1] - 1) * 100
            rets_3d.append(r3); rets_5d.append(r5); rets_10d.append(r10); rets_20d.append(r20)
            prev_5d_rets.append((close[-6]/close[-11] - 1) * 100)
            recent_vol = float(np.mean(vol[-3:]))
            avg_vol = float(np.mean(vol[-21:-1]))
            vol_bursts.append(recent_vol / avg_vol if avg_vol > 0 else 1)
            chg_1d = (close[-1]/close[-2] - 1) * 100 if len(close) >= 2 else 0
            # 每日累積：從 D-20 為基準，到第 N 日累積漲幅
            base = float(close[-(lookback+1)])
            daily_cum = [(float(close[-(lookback - i)]) / base - 1) * 100 for i in range(lookback)]
            per_stock_cumulative.append(daily_cum)
            member_details.append({
                "sid": sid,
                "name": industries.get(sid, {}).get("name", sid),
                "close": round(float(close[-1]), 2),
                "chg_1d": round(chg_1d, 2),
                "chg_5d": round(r5, 2),
                "chg_20d": round(r20, 2),
            })
        if len(rets_20d) < min_members: continue
        member_details.sort(key=lambda x: -x["chg_5d"])

        # ★ 族群每日累積中位數軌跡（20 個點）
        daily_cumulative = []
        for day in range(lookback):
            day_values = [s[day] for s in per_stock_cumulative if day < len(s)]
            daily_cumulative.append(round(statistics.median(day_values), 2) if day_values else 0)

        # ★ 資金輪動階段判定（7 個階段 + 專業建議）
        stage_info = _classify_rotation_stage(daily_cumulative)

        med3   = statistics.median(rets_3d)
        med5   = statistics.median(rets_5d)
        med10  = statistics.median(rets_10d)
        med20  = statistics.median(rets_20d)
        prev_med5 = statistics.median(prev_5d_rets)
        vol_burst = statistics.median(vol_bursts)

        # 動能變化（資金流入/流出核心訊號）
        momentum_change = med5 - prev_med5    # 本週中位 - 上週中位
        # 加速度：本週速度 vs 上週速度
        accel_5 = (med5 / 5) / max(abs(prev_med5 / 5), 0.05)
        # 短長期動能比（3 日均速 vs 20 日均速）
        accel_long = (med3 / 3) / max(abs(med20 / 20), 0.05)

        # 資金流動標籤（核心輪動偵測）
        if momentum_change > 2 and vol_burst > 1.3 and med3 > 0:
            flow = "🔥 資金切入"          # 量價齊揚 + 動能反轉
        elif med5 > 2 and momentum_change > 0:
            flow = "🚀 加速流入"
        elif med20 > 5 and med5 < -2:
            flow = "💸 資金跑路"          # 整體強但本週急殺
        elif med20 > 3 and momentum_change < -2:
            flow = "⚠️ 動能衰退"
        elif med20 > 0 and med5 > 0:
            flow = "📈 穩定上漲"
        elif med5 < -3:
            flow = "🔻 急殺"
        else:
            flow = "⏸️ 整理"

        info = SUBSECTORS.get(sub, {})
        stats[sub] = {
            "subsector": sub,
            "alias":     info.get("alias", sub),
            "icon":      info.get("icon", "📊"),
            "parent":    info.get("parent", "其他"),
            "members":   len(rets_20d),
            "median_ret_3d":  round(med3, 2),
            "median_ret_5d":  round(med5, 2),
            "median_ret_10d": round(med10, 2),
            "median_ret_20d": round(med20, 2),
            "prev_ret_5d":    round(prev_med5, 2),
            "momentum_change": round(momentum_change, 2),
            "vol_burst":      round(vol_burst, 2),
            "acceleration":   round(accel_5, 2),
            "accel_long":     round(accel_long, 2),
            "max_ret_20d":    round(max(rets_20d), 2),
            "rotation":       flow,
            "key_stocks":     list(SUBSECTORS.get(sub, {}).get("key_stocks", {}).items())[:5],
            "member_details": member_details[:30],
            "daily_cumulative": daily_cumulative,    # 20 個累積點
            "stage":           stage_info["stage"],
            "stage_icon":      stage_info["icon"],
            "stage_color":     stage_info["color"],
            "advice":          stage_info["advice"],
            "peak_day":        stage_info["peak_day"],   # 高點在第幾天（1-20）
            "drawdown_from_peak": stage_info["drawdown"],   # 距高點回落 %
        }

    # ★ 用「綜合分數」排名：短中長動能 + 量比 + 動能變化
    # 設計：本週進場機會 = 0.4×med5 + 0.3×med10 + 0.2×momentum_change + 0.1×vol_burst*10
    for s in stats.values():
        s["flow_score"] = round(
            0.4 * s["median_ret_5d"] + 0.3 * s["median_ret_10d"]
            + 0.2 * s["momentum_change"] + 0.1 * (s["vol_burst"] - 1) * 10, 2
        )

    # 主排名：用 flow_score（短週期 + 動能變化）
    ranked = sorted(stats.values(), key=lambda x: -x["flow_score"])
    for i, s in enumerate(ranked):
        s["rank"] = i + 1
        s["is_top5"] = (i < 5)
        s["is_top10"] = (i < 10)
    # 副排名：保留 20 日中位排名給對比
    ret20_sorted = sorted(stats.values(), key=lambda x: -x["median_ret_20d"])
    for i, s in enumerate(ret20_sorted):
        s["rank_20d"] = i + 1

    return ranked


# ─────────────────────────────────────────────────────────────
# V42 + 子族群整合（多重歸屬：取最佳族群）
# ─────────────────────────────────────────────────────────────
def sector_filter_v42(v42_hits, industries, sub_ranking):
    """把 V42 hits 加上 subsector + quality_tier 標記
    多重歸屬：一支股可能同時在 ABF + AI PCB + 低軌衛星，
    取「排名最佳」的族群作為品質判斷依據，並列出所有族群"""
    rank_map = {s["subsector"]: s for s in sub_ranking}
    enriched = []
    for h in v42_hits:
        sid = h["sid"]
        all_subs = classify_stock_all(sid, industries.get(sid, {}).get("industry", ""))
        # 找排名最佳的子族群
        best_sub = None
        best_rank = 9999
        all_membership_info = []
        for sub in all_subs:
            info = rank_map.get(sub, {})
            r = info.get("rank", 9999)
            all_membership_info.append({
                "sub": sub,
                "alias": info.get("alias", sub),
                "icon": info.get("icon", ""),
                "rank": r,
                "ret20": info.get("median_ret_20d", 0),
                "rotation": info.get("rotation", "—"),
            })
            if r < best_rank:
                best_rank = r
                best_sub = sub
        sub = best_sub or (all_subs[0] if all_subs else "_未分類")
        s_info = rank_map.get(sub, {})
        h2 = dict(h)
        h2["subsector"]        = sub
        h2["subsector_alias"]  = s_info.get("alias", sub)
        h2["subsector_icon"]   = s_info.get("icon", "📊")
        h2["subsector_parent"] = s_info.get("parent", "其他")
        h2["subsector_rank"]   = s_info.get("rank", 99)
        h2["subsector_ret20"]  = s_info.get("median_ret_20d", 0)
        h2["subsector_ret5"]   = s_info.get("median_ret_5d", 0)
        h2["subsector_accel"]  = s_info.get("acceleration", 0)
        h2["subsector_rotation"] = s_info.get("rotation", "—")
        h2["subsector_members"] = s_info.get("members", 0)
        h2["is_top5_subsector"] = s_info.get("is_top5", False)
        h2["is_top10_subsector"] = s_info.get("is_top10", False)
        # 多重歸屬清單
        h2["all_memberships"] = all_membership_info
        h2["memberships_text"] = " / ".join(
            f"{m['icon']}{m['alias']}(#{m['rank']})" for m in all_membership_info
        )
        # 品質 tier
        if h2["is_top5_subsector"] and h2["subsector_accel"] > 1.2 and h2["subsector_ret5"] > 0:
            h2["quality_tier"] = "AAA"
            h2["quality_note"] = "Top 5 子族群 + 資金加速流入 + V42（最高品質）"
        elif h2["is_top5_subsector"]:
            h2["quality_tier"] = "AA"
            h2["quality_note"] = "Top 5 強勢子族群 + V42"
        elif h2["is_top10_subsector"]:
            h2["quality_tier"] = "A"
            h2["quality_note"] = "Top 10 中強子族群 + V42"
        else:
            h2["quality_tier"] = "B"
            h2["quality_note"] = "弱勢子族群中的 V42（可能假突破，謹慎）"
        enriched.append(h2)
    tier_order = {"AAA":0, "AA":1, "A":2, "B":3}
    enriched.sort(key=lambda x: (tier_order.get(x["quality_tier"], 9),
                                  -x.get("flash_score", 0)))
    return enriched


# 相容舊介面
def compute_sector_strength(pc, industries, cutoff_date=None):
    """別名 — 給 generate_chart.py 舊呼叫用"""
    return compute_subsector_strength(pc, industries, cutoff_date)
