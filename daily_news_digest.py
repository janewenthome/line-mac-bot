#!/usr/bin/env python3
"""
每日新聞摘要產生器
自動從 WorldMonitor 同源 RSS 擷取新聞，透過 Gemini AI 產出繁體中文摘要。
存入 Obsidian 的「每日新聞摘要」資料夾。

持倉與關注：
  美股: TSLA, NVDA, GOOG, CPNG
  台股: 0050, 2330, 1216(統一), 3008(大立光), 1752(南光), 2727(王品), 2881(富邦金)
  英股ETF: VWRA, IUUS, GLDM, AGGU, CSPX
  加密: BTC
  外匯: USD, TWD
  指標: VIX
關注區域：台海/東亞、中東、歐洲(俄烏)、美國(Fed)、東南亞
"""

import os
import sys
import json
import hashlib
import datetime
import socket
import logging
from pathlib import Path
from typing import Optional

import yaml

# ── 設定檔路徑 ────────────────────────────────────────────────────────
CONFIG_FILE = Path(
    os.path.expanduser(
        "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
        "Second brain/系統設定/SKILL_每日新聞摘要設定.md"
    )
)

# ── 從 YAML 載入設定（找不到則用預設值）─────────────────────────────────
_config: dict = {}


def load_config() -> dict:
    """讀取外部 YAML 設定檔。找不到檔案時 log 警告並回傳空 dict。"""
    global _config
    if _config:
        return _config
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                _config = yaml.safe_load(f) or {}
            log.info(f"✓ 已載入設定檔: {CONFIG_FILE.name}")
        except Exception as e:
            log.warning(f"設定檔解析失敗，使用預設值: {e}")
            _config = {}
    else:
        log.warning(f"設定檔不存在，使用程式內建預設值: {CONFIG_FILE}")
        _config = {}
    return _config


def _cfg(key: str, default=None):
    """從已載入的 config 取值，支援 dot notation (e.g. 'limits.max_articles_per_feed')。"""
    parts = key.split(".")
    val = _config
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return default
    return val if val is not None else default


# ── 不可外部化的設定（敏感/路徑相關）──────────────────────────────────
GEMINI_API_KEY = os.environ.get(
    "GEMINI_API_KEY", ""
)

LOG_FILE = Path(__file__).parent / "daily_news_digest.log"
CACHE_FILE = Path(__file__).parent / ".digest_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# 首次載入設定檔
load_config()

# ── 從設定檔取值（附 fallback 預設值）────────────────────────────────

OBSIDIAN_BASE = Path(
    os.path.expanduser(
        _cfg("obsidian.output_base",
             "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/"
             "Second brain/文章存檔/4. 每日新聞摘要")
    )
)

MAX_ARTICLES_PER_FEED = _cfg("limits.max_articles_per_feed", 8)
MAX_TOTAL_ARTICLES = _cfg("limits.max_total_articles", 80)
READING_TIME_MINUTES = _cfg("limits.reading_time_minutes", 5)

# Threads 設定
THREADS_APP_ID = os.environ.get("THREADS_APP_ID", _cfg("threads.app_id", ""))
THREADS_APP_SECRET = os.environ.get("THREADS_APP_SECRET", _cfg("threads.app_secret", ""))
THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "") or _cfg("threads.access_token", "")
THREADS_USER_ID = os.environ.get("THREADS_USER_ID", "") or _cfg("threads.user_id", "")
THREADS_TOKEN_FILE = Path(__file__).parent / ".threads_token.json"
ENABLE_THREADS = _cfg("threads.enable", True)

# RSS Feeds — 從 YAML 讀取，fallback 到預設清單
_DEFAULT_RSS_FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "市場"),
    ("MarketWatch", "https://news.google.com/rss/search?q=site:marketwatch.com+markets+when:1d&hl=en-US&gl=US&ceid=US:en", "市場"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "市場"),
    ("Reuters Business", "https://news.google.com/rss/search?q=site:reuters.com+business+markets&hl=en-US&gl=US&ceid=US:en", "市場"),
    ("Financial Times", "https://www.ft.com/rss/home", "市場"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "市場"),
    ("Crypto News", "https://news.google.com/rss/search?q=(Bitcoin+OR+BTC+OR+Ethereum+OR+crypto+OR+stablecoin)+when:1d&hl=en-US&gl=US&ceid=US:en", "加密貨幣"),
    ("Reuters Asia", "https://news.google.com/rss/search?q=site:reuters.com+(China+OR+Japan+OR+Taiwan+OR+Korea)+when:1d&hl=en-US&gl=US&ceid=US:en", "亞太"),
    ("BBC Asia", "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "亞太"),
    ("Nikkei Asia", "https://news.google.com/rss/search?q=site:asia.nikkei.com+when:1d&hl=en-US&gl=US&ceid=US:en", "亞太"),
    ("Taiwan News", "https://news.google.com/rss/search?q=(Taiwan+OR+TSMC+OR+台積電+OR+台海)+when:1d&hl=en-US&gl=US&ceid=US:en", "亞太"),
    ("Reuters US", "https://news.google.com/rss/search?q=site:reuters.com+US+when:1d&hl=en-US&gl=US&ceid=US:en", "美國"),
    ("AP News", "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en", "美國"),
    ("BBC Middle East", "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml", "中東"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml", "中東"),
    ("BBC Europe", "https://news.google.com/rss/search?q=(Russia+Ukraine+NATO+EU)+when:1d&hl=en-US&gl=US&ceid=US:en", "歐洲"),
    ("US Portfolio", "https://news.google.com/rss/search?q=(TSLA+OR+NVDA+OR+GOOG+OR+CPNG+OR+VIX)+when:1d&hl=en-US&gl=US&ceid=US:en", "持倉"),
    ("TW Stocks", "https://news.google.com/rss/search?q=(0050+OR+台積電+OR+統一+OR+大立光+OR+南光+OR+王品+OR+富邦金+OR+台股)+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", "持倉"),
    ("ETF & Gold", "https://news.google.com/rss/search?q=(VWRA+OR+CSPX+OR+gold+ETF+OR+GLDM+OR+aggregate+bond+OR+AGGU)+when:1d&hl=en-US&gl=US&ceid=US:en", "持倉"),
    ("CrisisWatch", "https://www.crisisgroup.org/rss", "危機"),
    ("Foreign Policy", "https://foreignpolicy.com/feed/", "智庫"),
]

_yaml_feeds = _cfg("rss_feeds")
if _yaml_feeds and isinstance(_yaml_feeds, list):
    RSS_FEEDS = [tuple(f) for f in _yaml_feeds]
else:
    RSS_FEEDS = _DEFAULT_RSS_FEEDS

# ── 工具函式 ─────────────────────────────────────────────────────────

def check_internet(host="8.8.8.8", port=53, timeout=5) -> bool:
    """快速檢查是否有網路。"""
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def fetch_rss(url: str, max_items: int = MAX_ARTICLES_PER_FEED) -> list[dict]:
    """擷取 RSS feed，回傳 [{title, link, published, source}]。"""
    import feedparser

    try:
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:max_items]:
            pub = ""
            if hasattr(entry, "published"):
                pub = entry.published
            elif hasattr(entry, "updated"):
                pub = entry.updated
            items.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "published": pub,
                "summary": (entry.get("summary") or "")[:200].strip(),
            })
        return items
    except Exception as e:
        log.warning(f"RSS 擷取失敗 ({url[:60]}): {e}")
        return []


def deduplicate(articles: list[dict]) -> list[dict]:
    """用標題 hash 去重。"""
    seen = set()
    unique = []
    for a in articles:
        h = hashlib.md5(a["title"].lower().encode()).hexdigest()[:12]
        if h not in seen:
            seen.add(h)
            unique.append(a)
    return unique


def load_cache() -> set:
    """讀取已處理過的文章 hash，避免重複。"""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            today = datetime.date.today().isoformat()
            if data.get("date") == today:
                return set(data.get("hashes", []))
        except Exception:
            pass
    return set()


def save_cache(hashes: set):
    """儲存今日已處理的文章 hash。"""
    CACHE_FILE.write_text(json.dumps({
        "date": datetime.date.today().isoformat(),
        "hashes": list(hashes),
    }))


def call_gemini(prompt: str) -> Optional[str]:
    """呼叫 Gemini API 產生摘要，含 429 重試機制。"""
    import urllib.request
    import urllib.error
    import time

    models = _cfg("gemini.models", ["gemini-2.5-flash", "gemini-2.0-flash-lite"])
    temperature = _cfg("gemini.temperature", 0.4)
    max_tokens = _cfg("gemini.max_output_tokens", 12288)

    for model in models:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }).encode()

        for attempt in range(3):
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read())
                    text = (
                        data.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [{}])[0]
                        .get("text", "")
                    )
                    if text:
                        log.info(f"  ✓ Gemini ({model}) 回應成功")
                        return text
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 15 * (2 ** attempt)
                    log.warning(f"  Gemini ({model}) 429 限流，等待 {wait}s 後重試 ({attempt+1}/3)...")
                    time.sleep(wait)
                    continue
                log.error(f"  Gemini ({model}) HTTP {e.code}: {e.reason}")
                break
            except Exception as e:
                log.error(f"  Gemini ({model}) 失敗: {e}")
                break

        log.warning(f"  模型 {model} 嘗試失敗，換下一個...")

    log.error("所有 Gemini 模型均失敗。")
    return None


# ── Obsidian 摘要 Prompt ─────────────────────────────────────────────

def build_prompt(categorized: dict[str, list[dict]]) -> str:
    """將分類新聞組成 Gemini prompt。使用設定檔中的模板。"""

    feed_text = ""
    for category, articles in categorized.items():
        feed_text += f"\n## {category}\n"
        for i, a in enumerate(articles, 1):
            # 解析發布日期，標示是今天還是昨天的新聞
            date_label = ""
            if a.get("published"):
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(a["published"])
                    today = datetime.date.today()
                    pub_date = pub_dt.date()
                    if pub_date == today:
                        date_label = "（今日）"
                    elif pub_date == today - datetime.timedelta(days=1):
                        date_label = "（昨日）"
                    else:
                        date_label = f"（{pub_date.month}/{pub_date.day}）"
                except Exception:
                    pass
            feed_text += f"{i}. [{a['source']}] {a['title']} {date_label}"
            if a["summary"]:
                feed_text += f" — {a['summary']}"
            feed_text += f"\n   連結: {a['link']}\n"

    # 從設定檔取 prompt 模板，找不到則用內建預設
    template = _cfg("prompt_obsidian")
    if template:
        return template.replace("{feed_text}", feed_text)

    # fallback: 內建預設 prompt（與 YAML 中相同）
    return f"""你是一位專業的地緣政治與財經分析師 (CFA)，為一位住在台灣的投資者撰寫每日情報摘要。

以下是今日從 Reuters、BBC、CNBC、Al Jazeera、Financial Times 等權威來源擷取的新聞：

{feed_text}

請依照以下格式，用**台灣繁體中文**撰寫一份約 **5 分鐘閱讀**的每日深度摘要。
專有名詞請附註英文原名，例如：聯準會 (Federal Reserve)、台積電 (TSMC)。
分析要具體深入，帶有專業洞見，不要泛泛而談。

## 格式要求

用 Obsidian 相容的 Markdown 格式輸出，結構如下：

### 🔥 今日焦點
（2-3 則最重要的全球事件，深入分析背景脈絡，以及對投資組合的潛在影響。每則至少 3-4 句深度分析。）

### 📊 市場動態
分四個小節報導：
#### 股市
（美股、台股、歐股的關鍵走勢與板塊輪動）
#### 加密貨幣
（BTC、重大監管動態、DeFi/穩定幣趨勢）
#### 外匯與利率
（USD/TWD、主要央行政策、殖利率曲線變化）
#### 商品
（原油、黃金、銅等，附驅動因素分析）

### 🌏 地緣政治
（台海/東亞、中東、俄烏、美國的重要發展，每個區域 2-3 句深度分析，說明對金融市場的傳導機制）

### ⚡ 持倉影響分析
**請按以下分類分段，每個標的至少 2-3 句具體分析：**

#### 🇺🇸 美股
- **TSLA (特斯拉):** ...
- **NVDA (輝達):** ...
- **GOOG (Alphabet):** ...
- **CPNG (酷澎 Coupang):** ...

#### 🇹🇼 台股
- **0050 (元大台灣50):** ...
- **2330 (台積電 TSMC):** ...
- **1216 (統一):** ...
- **3008 (大立光):** ...
- **1752 (南光):** ...
- **2727 (王品):** ...
- **2881 (富邦金):** ...

#### 🇬🇧 英股 ETF & 債券
- **VWRA (Vanguard 全球股票 ETF):** ...
- **CSPX (iShares S&P 500 ETF):** ...
- **IUUS (iShares 美國股票 ETF):** ...
- **GLDM (SPDR 黃金迷你 ETF):** ...
- **AGGU (iShares 全球綜合債券 ETF):** ...

#### ₿ 加密貨幣
- **BTC (比特幣):** ...

#### 💱 外匯 & 指標
- **USD (美元):** ...
- **TWD (新台幣):** ...
- **VIX (恐慌指數):** ...

### 🔗 原文連結
（列出最重要的 8-12 則新聞原文連結，格式：`- [標題](URL) — 來源`）

### ⚠️ 風險提醒
（今日需特別注意的 3-5 項風險事件或經濟數據公布，附影響評估）

---

注意事項：
1. 只輸出 Markdown 內文，不要加文件標題（frontmatter 由程式處理）
2. 目標閱讀時間 **5 分鐘**，內容要有深度但不冗長
3. 用繁體中文（台灣用語），專有名詞附英文
4. 如果某個分類沒有重要新聞，可以省略該分類
5. 持倉影響分析要具體，說明因果邏輯，例如「因為...所以對...的影響是...」
6. 1752 是南光（醫療器材），不是南僑
"""


# ── Threads 發布功能 ──────────────────────────────────────────────────

def get_threads_token() -> tuple[str, str]:
    """取得 Threads access token 和 user ID。優先順序：本地快取 > 環境變數/YAML 設定。"""
    if THREADS_TOKEN_FILE.exists():
        try:
            data = json.loads(THREADS_TOKEN_FILE.read_text())
            token = data.get("access_token", "")
            user_id = data.get("user_id", "")
            if token and user_id:
                return token, user_id
        except Exception:
            pass
    return THREADS_ACCESS_TOKEN, THREADS_USER_ID


def save_threads_token(token: str, user_id: str):
    """儲存 Threads token 到本地檔案。"""
    THREADS_TOKEN_FILE.write_text(json.dumps({
        "access_token": token,
        "user_id": user_id,
        "updated": datetime.datetime.now().isoformat(),
    }))


def refresh_threads_token():
    """嘗試延長 Threads token 有效期（60 天 → 再 60 天）。"""
    import urllib.request
    import urllib.error

    token, user_id = get_threads_token()
    if not token:
        return

    url = (
        f"https://graph.threads.net/refresh_access_token"
        f"?grant_type=th_refresh_token"
        f"&access_token={token}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
            new_token = data.get("access_token", token)
            save_threads_token(new_token, user_id)
            log.info("  ✓ Threads token 已續期")
    except Exception as e:
        log.warning(f"  Threads token 續期失敗: {e}")


def digest_to_threads_posts(digest: str) -> Optional[list[str]]:
    """用 Gemini 將 Obsidian 摘要轉為 5 則 Threads 串文。使用設定檔模板。"""

    # 從設定檔取 prompt 模板
    template = _cfg("prompt_threads")
    if template:
        prompt = template.replace("{digest}", digest[:3000])
    else:
        # fallback: 內建預設 prompt
        prompt = f"""你是「文史哲簡單說」的作者，一位擁有哲學家心境的財經觀察者。
你的風格是：用文史哲經典的智慧來觀照當代新聞，讓讀者在資訊洪流中找到安頓心靈的視角。

你引用的經典範圍廣泛，包括但不限於：
- 西方哲學：蘇格拉底《斐多篇》、斯多葛學派、尼采、沙特、卡繆
- 東方智慧：《論語》《道德經》《莊子》《孫子兵法》《奧義書》
- 歷史經典：《春秋左傳》《史記》、修昔底德《伯羅奔尼撒戰爭史》
- 文學：卡夫卡、村上春樹、赫塞《悉達多》

以下是今日的財經新聞摘要（Obsidian 格式）：

{digest[:3000]}

請將上述內容轉換為 **5 則 Threads 串文**，每則嚴格 **不超過 480 字**。

## 格式規則（非常重要）
1. 用純文字，不用 Markdown（Threads 不支援）
2. 用 Emoji 開頭分段，增加掃讀性
3. 繁體中文（台灣用語），專有名詞附英文
4. 輸出格式：每則之間用 `---THREAD_BREAK---` 分隔

## 5 則串文結構

### 第 1 則：哲學視角開場（Hook）
- 開頭引用一句與今日新聞主題相關的文史哲經典名言或觀點（不要每天都引同一部經典）
- 用 1-2 句話連結這個古典智慧與今日新聞
- 接著點出今日 2-3 個最重要的事件
- 語氣：沈穩、洞察、帶有哲思
- 結尾加上「🧵 以下展開分析 👇」

### 第 2 則：市場數據
- 📊 開頭
- 股市、加密貨幣、商品的關鍵數字
- 用「▲」「▼」「→」表示漲跌持平
- 重點突出，數據清晰

### 第 3 則：地緣政治
- 🌏 開頭
- 最重要的 2-3 個地緣事件
- 説明對市場的傳導路徑

### 第 4 則：持倉觀察（精選）
- ⚡ 開頭
- 從持倉中挑出今日最受影響的 4-5 檔
- 具體說明影響原因

### 第 5 則：風險提醒 + 互動
- ⚠️ 開頭
- 2-3 個需注意的風險
- 結尾用一個與今日主題相關的哲學反思問句鼓勵互動
- 最後一行加上：「⚖️ 本內容由 AI 輔助整理，僅供參考，不構成投資建議。」

請直接輸出 5 則文字，用 ---THREAD_BREAK--- 分隔。不要加任何前綴説明。
"""

    result = call_gemini(prompt)
    if not result:
        return None

    posts = [p.strip() for p in result.split("---THREAD_BREAK---") if p.strip()]

    # 確保每則不超過 500 字
    truncated = []
    for i, post in enumerate(posts):
        if len(post) > 500:
            post = post[:497] + "..."
            log.warning(f"  Threads 第 {i+1} 則超過 500 字，已截斷")
        truncated.append(post)

    return truncated


def publish_to_threads(posts: list[str], dry_run: bool = False) -> bool:
    """透過 Threads API 發布串文。"""
    import urllib.request
    import urllib.error
    import urllib.parse
    import time

    token, user_id = get_threads_token()
    if not token or not user_id:
        log.warning("  Threads token 或 user_id 未設定，跳過發布")
        return False

    if dry_run:
        log.info("  [DRY RUN] Threads 串文預覽：")
        for i, post in enumerate(posts, 1):
            log.info(f"  --- 第 {i} 則 ({len(post)} 字) ---")
            log.info(f"  {post[:100]}...")
        return True

    base_url = "https://graph.threads.net/v1.0"
    published_ids = []

    for i, text in enumerate(posts):
        try:
            # Step 1: 建立 media container
            create_url = f"{base_url}/{user_id}/threads"
            params = {
                "media_type": "TEXT",
                "text": text,
                "access_token": token,
            }
            # 第 2 則起作為回覆串接
            if published_ids:
                params["reply_to_id"] = published_ids[0]

            data = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(create_url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                container_id = result.get("id")

            if not container_id:
                log.error(f"  Threads 第 {i+1} 則容器建立失敗")
                break

            # Step 2: 發布
            time.sleep(2)  # API 建議等待
            publish_url = f"{base_url}/{user_id}/threads_publish"
            pub_params = urllib.parse.urlencode({
                "creation_id": container_id,
                "access_token": token,
            }).encode()
            pub_req = urllib.request.Request(publish_url, data=pub_params, method="POST")
            with urllib.request.urlopen(pub_req, timeout=30) as resp:
                pub_result = json.loads(resp.read())
                post_id = pub_result.get("id")

            if post_id:
                published_ids.append(post_id)
                log.info(f"  ✓ Threads 第 {i+1} 則已發布 (ID: {post_id})")
            else:
                log.error(f"  Threads 第 {i+1} 則發布失敗")
                break

            time.sleep(3)  # 避免 rate limit

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            log.error(f"  Threads 第 {i+1} 則 HTTP {e.code}: {error_body[:200]}")
            break
        except Exception as e:
            log.error(f"  Threads 第 {i+1} 則失敗: {e}")
            break

    success = len(published_ids) == len(posts)
    if success:
        log.info(f"  ✅ Threads 串文全部發布成功（{len(posts)} 則）")
    else:
        log.warning(f"  ⚠️ Threads 僅發布 {len(published_ids)}/{len(posts)} 則")
    return success


# ── 主流程 ───────────────────────────────────────────────────────────

def generate_digest():
    """主函式：擷取 → 分類 → AI 摘要 → 存檔 → 推送 Threads。"""
    log.info("=" * 50)
    log.info("開始產生每日新聞摘要")

    # 1. 網路檢查
    if not check_internet():
        log.info("無網路連線，跳過今日摘要。")
        return

    # 2. 檢查是否已產生過
    today = datetime.date.today()
    output_dir = OBSIDIAN_BASE / str(today.year) / f"{today.month:02d}"
    output_file = output_dir / f"{today.isoformat()} 每日摘要.md"

    if output_file.exists():
        log.info(f"今日摘要已存在：{output_file}")
        return

    # 3. 擷取 RSS
    log.info(f"擷取 {len(RSS_FEEDS)} 個 RSS 來源...")
    categorized: dict[str, list[dict]] = {}
    total = 0

    for source_name, url, category in RSS_FEEDS:
        articles = fetch_rss(url)
        for a in articles:
            a["source"] = source_name
            a["category"] = category

        if category not in categorized:
            categorized[category] = []
        categorized[category].extend(articles)
        total += len(articles)
        log.info(f"  ✓ {source_name}: {len(articles)} 則")

    # 4. 去重 & 限制數量
    for cat in categorized:
        categorized[cat] = deduplicate(categorized[cat])[:MAX_ARTICLES_PER_FEED * 2]

    all_articles = sum(len(v) for v in categorized.values())
    log.info(f"共擷取 {total} 則，去重後 {all_articles} 則")

    if all_articles == 0:
        log.warning("無新聞可擷取，跳過。")
        return

    # 5. 呼叫 Gemini 產生摘要
    log.info("呼叫 Gemini AI 產生繁體中文摘要...")
    prompt = build_prompt(categorized)
    digest = call_gemini(prompt)

    if not digest:
        log.error("AI 摘要產生失敗。")
        return

    # 6. 組合最終 Markdown
    weekday_zh = ["一", "二", "三", "四", "五", "六", "日"]
    wd = weekday_zh[today.weekday()]

    frontmatter = f"""---
date: {today.isoformat()}
type: daily-digest
tags:
  - 每日摘要
  - 地緣政治
  - 財經
  - WorldMonitor
---

# 📰 每日情報摘要 — {today.year}/{today.month:02d}/{today.day:02d}（{wd}）

"""
    footer = f"""
---
*自動產生於 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} · 資料來源：WorldMonitor RSS · AI: Gemini 2.5 Flash*
"""

    content = frontmatter + digest + footer
    # 清除 Gemini 回應中可能含有的 surrogate 字元（避免 UnicodeEncodeError）
    content = content.encode("utf-8", errors="replace").decode("utf-8")

    # 7. 寫入 Obsidian
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file.write_text(content, encoding="utf-8")
    log.info(f"✅ 摘要已儲存：{output_file}")
    log.info(f"   檔案大小：{output_file.stat().st_size:,} bytes")

    # 8. 產生 Threads 草稿（寫在摘要底部，等待人工審核）
    if ENABLE_THREADS:
        log.info("轉換摘要為 Threads 草稿...")
        threads_posts = digest_to_threads_posts(digest)
        if threads_posts:
            log.info(f"  產生 {len(threads_posts)} 則草稿")
            draft_section = "\n\n---\n\n## \ud83d\udcdd Threads 待發布草稿\n\n"
            draft_section += "<!-- THREADS_DRAFT_START -->\n"
            draft_section += "\n---THREAD_BREAK---\n".join(threads_posts)
            draft_section += "\n<!-- THREADS_DRAFT_END -->\n"
            draft_section += "\n> ℹ️ 審閱/修改完成後，在 LINE 傳送「脆發文❕」即可發布至 Threads\n"
            # 重新寫入檔案（加上草稿區）
            content += draft_section
            content = content.encode("utf-8", errors="replace").decode("utf-8")
            output_file.write_text(content, encoding="utf-8")
            log.info("✅ Threads 草稿已附加於摘要底部，等待人工審核")
        else:
            log.warning("Threads 草稿產生失敗")


# ── 從 Obsidian 發布 Threads（供 LINE bot 呼叫）────────────────────────

def publish_threads_from_obsidian() -> str:
    """讀取今日摘要中的 Threads 草稿，發布至 Threads。回傳結果訊息。"""
    # 重新載入設定（確保最新）
    global _config
    _config = {}
    load_config()

    today = datetime.date.today()
    output_dir = OBSIDIAN_BASE / str(today.year) / f"{today.month:02d}"
    output_file = output_dir / f"{today.isoformat()} 每日摘要.md"

    if not output_file.exists():
        return f"❌ 今日摘要尚未產生（{today.isoformat()}），請先執行每日摘要排程"

    content = output_file.read_text(encoding="utf-8")

    # 解析草稿區
    start_marker = "<!-- THREADS_DRAFT_START -->"
    end_marker = "<!-- THREADS_DRAFT_END -->"
    if start_marker not in content or end_marker not in content:
        return "❌ 今日摘要中找不到 Threads 草稿區，可能已發布過或摘要未包含草稿"

    draft_text = content.split(start_marker)[1].split(end_marker)[0].strip()
    posts = [p.strip() for p in draft_text.split("---THREAD_BREAK---") if p.strip()]

    if not posts:
        return "❌ 草稿區內容為空，請檢查 Obsidian 中的摘要檔案"

    # 發布
    token, uid = get_threads_token()
    if not token or not uid:
        return "❌ Threads token 或 user_id 未設定，無法發布"

    log.info(f"脆發文：準備發布 {len(posts)} 則 Threads 串文...")
    success = publish_to_threads(posts, dry_run=False)

    if success:
        # 續期 token
        refresh_threads_token()
        # 在摘要檔中標註已發布
        now = datetime.datetime.now().strftime("%H:%M")
        published_note = f"\n\n> ✅ 已於 {now} 發布至 Threads（{len(posts)} 則）\n"
        # 移除草稿區，替換為已發布標註
        new_content = content.split("## 📝 Threads 待發布草稿")[0].rstrip()
        new_content += published_note
        output_file.write_text(new_content, encoding="utf-8")
        return f"✅ 已成功發布 {len(posts)} 則 Threads 串文！"
    else:
        return "⚠️ Threads 發布未完全成功，請檢查 log"


# ── 入口 ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        generate_digest()
    except Exception:
        log.exception("每日摘要產生發生未預期錯誤")
        sys.exit(1)

