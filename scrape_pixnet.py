"""
scrape_pixnet.py
================
將痞客邦 (Pixnet) 的部落格文章，透過 Sitemap 全量爬取並轉成 Obsidian Markdown。

【套件需求】首次使用請先執行：
    .venv/bin/pip install requests beautifulsoup4 markdownify lxml

執行方式：
    .venv/bin/python scrape_pixnet.py
"""

import os
import re
import time

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ── 設定區 ────────────────────────────────────────────────────────────────────

SITEMAP_URL = "https://wenthome.pixnet.net/sitemap.xml"

OUTPUT_DIR = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain/"
    "文章存檔/Pixnet貼文"
)

SLEEP_SEC = 1.5   # 爬蟲禮儀：每次請求後等待秒數

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# ── 工具函數 ──────────────────────────────────────────────────────────────────

def sanitize_filename(text: str, max_len: int = 60) -> str:
    """去除不合法的檔名字元，截短至 max_len。"""
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+\[\]]', "", text)
    text = text.strip().replace(" ", "_")
    return text[:max_len]


def fetch(url: str):
    """帶 User-Agent 的安全 GET，失敗回傳 None。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [錯誤] 抓取失敗：{url}\n         {e}")
        return None


# ── Step 1：解析 Sitemap 取得所有文章網址（遞迴 Regex 版）──────────────────────

def _extract_locs(text: str):
    """用正則暴力萃取 <loc>...</loc> 裡面的所有網址。"""
    return re.findall(r"<loc>(.*?)</loc>", text)


def _fetch_article_urls_from_sitemap(sitemap_url: str, visited: set):
    """
    遞迴挖掘 sitemap：
    - 若回傳的 <loc> 以 .xml 結尾 → 視為子地圖，遞迴繼續挖
    - 若包含 /blog/post/ → 視為文章網址，收集起來
    """
    if sitemap_url in visited:
        return []
    visited.add(sitemap_url)

    print(f"  [Sitemap] 抓取：{sitemap_url}")
    resp = fetch(sitemap_url)
    if not resp:
        return []

    locs = _extract_locs(resp.text)
    article_urls = []

    for loc in locs:
        loc = loc.strip()
        if loc.endswith(".xml"):
            # 子地圖 → 遞迴
            time.sleep(0.5)
            article_urls.extend(
                _fetch_article_urls_from_sitemap(loc, visited)
            )
        elif "/blog/post/" in loc:
            article_urls.append(loc)

    return article_urls


def get_article_urls():
    """入口：從 SITEMAP_URL 遞迴解析所有文章網址，去重後回傳。"""
    print(f"[Pixnet] 讀取 Sitemap：{SITEMAP_URL}")
    visited = set()
    urls = _fetch_article_urls_from_sitemap(SITEMAP_URL, visited)
    # 去重並維持順序
    seen = set()
    unique = [u for u in urls if not (u in seen or seen.add(u))]
    print(f"[Pixnet] 共找到 {len(unique)} 篇文章連結")
    return unique


# ── Step 2：解析單篇文章 ───────────────────────────────────────────────────────

def parse_article(url: str):
    """
    回傳 {title, date, content_md} 或 None（解析失敗）。
    解析優先順序：
      - 標題：og:title > <title>
      - 日期：article:published_time > 正則掃 HTML 中 YYYY-MM-DD
      - 內文：<div class="article-content-inner">
    """
    resp = fetch(url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.content, "html.parser")

    # ── 標題 ──
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title:
        title = soup.title.get_text(strip=True)
    else:
        title = "無標題"
    # 去掉「| 痞客邦」類後綴
    title = re.sub(r"\s*[|\-–—]\s*痞客邦.*$", "", title).strip()

    # ── 日期 ──
    date_str = ""
    pub_meta = soup.find("meta", property="article:published_time")
    if pub_meta and pub_meta.get("content"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", pub_meta["content"])
        if m:
            date_str = m.group(1)

    if not date_str:
        # Fallback：HTML 原始碼中搜尋日期格式
        m = re.search(r"(\d{4}-\d{2}-\d{2})", resp.text)
        date_str = m.group(1) if m else "0000-00-00"

    # ── 內文 ──
    content_div = soup.find("div", class_="article-content-inner")
    if not content_div:
        print(f"  [警告] 找不到 article-content-inner：{url}")
        return None

    # 移除廣告、腳本、樣式
    for tag in content_div(["style", "script", "ins", "iframe"]):
        tag.decompose()

    content_md = md(str(content_div), heading_style="ATX").strip()
    if not content_md:
        print(f"  [警告] 轉換後內容為空：{url}")
        return None

    content_md = re.sub(r"\n{3,}", "\n\n", content_md)

    return {"title": title, "date": date_str, "content_md": content_md}


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    urls = get_article_urls()
    if not urls:
        print("[Pixnet] 未找到任何文章，結束。")
        return

    saved = skipped_exists = failed = 0

    for i, url in enumerate(urls, 1):
        print(f"[Pixnet] ({i}/{len(urls)}) {url}")

        # 增量更新：以 URL slug 預判檔名是否已存在
        slug_preview = url.rstrip("/").split("/")[-1][:20]
        existing = [
            f for f in os.listdir(OUTPUT_DIR)
            if f.endswith(".md") and slug_preview in f
        ]
        if existing:
            print(f"  [跳過] 似乎已存在（{existing[0]}）")
            skipped_exists += 1
            time.sleep(0.2)
            continue

        # 解析文章
        data = parse_article(url)
        time.sleep(SLEEP_SEC)

        if not data:
            failed += 1
            continue

        title_safe = sanitize_filename(data["title"])
        filename   = f"{data['date']}_{title_safe}.md"
        filepath   = os.path.join(OUTPUT_DIR, filename)

        # 精確的增量更新（以最終檔名再確認一次）
        if os.path.exists(filepath):
            print(f"  [跳過] 已存在：{filename}")
            skipped_exists += 1
            continue

        # 寫入 Markdown
        front = (
            "---\n"
            f"date: {data['date']}\n"
            "tags: [Pixnet備份, 歷史迴音]\n"
            f"source: {url}\n"
            "---\n\n"
            f"# {data['title']}\n\n"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(front + data["content_md"] + "\n")

        saved += 1
        print(f"  [✅] 已存：{filename}")

    print(
        f"\n[Pixnet] 🎉 完成！\n"
        f"  ✅ 新增      {saved} 篇\n"
        f"  ⏭  已存在  {skipped_exists} 篇\n"
        f"  ❌ 失敗     {failed} 篇\n"
        f"[Pixnet] 存放路徑：{OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
