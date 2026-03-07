"""
scrape_pixnet_from_list.py
==========================
從 pixnet_urls.txt 讀取文章 URL，用 Playwright 逐篇爬取並轉成 Obsidian Markdown。

【使用方式】
1. 用真實瀏覽器開 https://wenthome.pixnet.net/blog
2. 將每篇文章 URL 貼入 pixnet_urls.txt（一行一個）
3. 執行：.venv/bin/python scrape_pixnet_from_list.py
"""

import os
import re
import time

from bs4 import BeautifulSoup
from markdownify import markdownify as md2
from playwright.sync_api import sync_playwright

# ── 設定區 ────────────────────────────────────────────────────────────────────

URLS_FILE = os.path.join(os.path.dirname(__file__), "pixnet_urls.txt")

OUTPUT_DIR = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain/"
    "文章存檔/Pixnet貼文"
)

SLEEP_SEC = 2.0

# ── 工具函數 ──────────────────────────────────────────────────────────────────

def sanitize_filename(text, max_len=60):
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+\[\]]', "", text)
    return text.strip().replace(" ", "_")[:max_len]


def load_urls():
    """從 pixnet_urls.txt 讀取 URL，去除空行與重複。"""
    if not os.path.isfile(URLS_FILE):
        print(f"[錯誤] 找不到 {URLS_FILE}")
        print("請建立此檔案，每行一個文章 URL，例如：")
        print("  https://wenthome.pixnet.net/blog/post/XXXXXXXX")
        return []

    with open(URLS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    seen = set()
    urls = []
    for line in lines:
        url = line.strip()
        if url and not url.startswith("#") and url not in seen:
            seen.add(url)
            urls.append(url)

    print(f"[Pixnet] 讀取 {len(urls)} 個 URL（來自 pixnet_urls.txt）")
    return urls


# ── 爬取單篇文章 ───────────────────────────────────────────────────────────

def scrape_article(page, url):
    """
    用 Playwright（有反偵測設定）載入文章頁，萃取：
    - 標題（og:title 優先）
    - 日期（article:published_time 優先）
    - 內文（.article-content-inner）
    回傳 dict 或 None。
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 等待內文區塊出現（最多 10 秒）
        try:
            page.wait_for_selector(
                ".article-content-inner, [class*='article-content']",
                timeout=10000
            )
        except Exception:
            pass
        time.sleep(1.0)  # 讓剩餘 JS 完成
    except Exception as e:
        print(f"  [錯誤] 頁面載入失敗：{e}")
        return None

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # ── 標題 ──
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title:
        title = soup.title.get_text(strip=True)
    else:
        title = "無標題"
    title = re.sub(r"\s*[|\-–—]\s*痞客邦.*$", "", title).strip()

    # ── 日期 ──
    date_str = ""
    pub = soup.find("meta", property="article:published_time")
    if pub and pub.get("content"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", pub["content"])
        if m:
            date_str = m.group(1)
    if not date_str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", html)
        date_str = m.group(1) if m else "0000-00-00"

    # ── 內文（多種 selector 防守）──
    content_div = (
        soup.find("div", class_="article-content-inner")
        or soup.find("div", class_=re.compile(r"article.content"))
        or soup.find("article")
    )
    if not content_div:
        print(f"  [警告] 找不到內文區塊：{url}")
        return None

    for tag in content_div(["style", "script", "ins", "iframe"]):
        tag.decompose()

    content_md = md2(str(content_div), heading_style="ATX").strip()
    if not content_md:
        print(f"  [警告] 內文轉換後為空：{url}")
        return None

    content_md = re.sub(r"\n{3,}", "\n\n", content_md)
    return {"title": title, "date": date_str, "content_md": content_md}


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    urls = load_urls()
    if not urls:
        return

    saved = skipped = failed = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
            viewport={"width": 1280, "height": 800},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        for i, url in enumerate(urls, 1):
            # 從 URL 擷取 post ID（支援新舊格式）
            m = re.search(r"/blog/posts?/(\d+)", url)
            post_id = m.group(1) if m else str(i)

            print(f"[Pixnet] ({i}/{len(urls)}) {url}")

            # 增量更新：已存在就跳過
            existing = [
                f for f in os.listdir(OUTPUT_DIR)
                if f.endswith(".md") and post_id in f
            ]
            if existing:
                print(f"  [跳過] 已存在（{existing[0]}）")
                skipped += 1
                continue

            data = scrape_article(page, url)
            time.sleep(SLEEP_SEC)

            if not data:
                failed += 1
                continue

            title_safe = sanitize_filename(data["title"])
            filename   = f"{data['date']}_{title_safe}_{post_id}.md"
            filepath   = os.path.join(OUTPUT_DIR, filename)

            if os.path.exists(filepath):
                skipped += 1
                continue

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

        browser.close()

    print(
        f"\n[Pixnet] 🎉 完成！\n"
        f"  ✅ 新增      {saved} 篇\n"
        f"  ⏭  已存在  {skipped} 篇\n"
        f"  ❌ 失敗     {failed} 篇\n"
        f"[Pixnet] 存放路徑：{OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
