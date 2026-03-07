"""
scrape_pixnet_playwright.py
============================
使用 Playwright 無頭瀏覽器爬取痞客邦（JavaScript 渲染網站）。
策略：讓 Chromium 真正執行 JS 後，再掃描 DOM 取得文章連結。

【套件需求】首次使用請先執行：
    .venv/bin/pip install playwright markdownify beautifulsoup4
    .venv/bin/playwright install chromium

執行方式：
    .venv/bin/python scrape_pixnet_playwright.py
"""

import os
import re
import time

from bs4 import BeautifulSoup
from markdownify import markdownify as md2
from playwright.sync_api import sync_playwright

# ── 設定區 ────────────────────────────────────────────────────────────────────

BLOG_ID   = "wenthome"
BLOG_HOME = f"https://{BLOG_ID}.pixnet.net/blog"

OUTPUT_DIR = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain/"
    "文章存檔/Pixnet貼文"
)

SLEEP_BETWEEN_ARTICLES = 1.5   # 每篇文章間隔（秒）
MAX_PAGES = 50                 # 列表頁最大翻頁數（安全上限）

POST_RE = re.compile(
    rf"https?://{BLOG_ID}\.pixnet\.net/blog/post/(\d+)"
)

# ── 工具函數 ──────────────────────────────────────────────────────────────────

def sanitize_filename(text, max_len=60):
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+\[\]]', "", text)
    return text.strip().replace(" ", "_")[:max_len]


def extract_post_urls(html):
    """從 HTML 中萃取所有 /blog/post/XXXXXX 連結（不含 query string）。"""
    urls = set()
    for m in POST_RE.finditer(html):
        urls.add(
            f"https://{BLOG_ID}.pixnet.net/blog/post/{m.group(1)}"
        )
    return urls


# ── Step 1：用 Playwright 掃列表頁，收集所有文章 URL ─────────────────────────

def collect_article_urls(page):
    """
    逐頁掃描 /blog?page=N，待 JS 渲染完成後抓取文章連結。
    回傳不重複的 URL 清單。
    """
    seen = set()
    urls = []

    for p in range(1, MAX_PAGES + 1):
        list_url = BLOG_HOME if p == 1 else f"{BLOG_HOME}?page={p}"
        print(f"[Pixnet] 掃描第 {p} 頁：{list_url}")

        page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        # 等待 JS 渲染（最多 5 秒，找到文章連結就繼續）
        try:
            page.wait_for_selector("a[href*='/blog/post/']", timeout=5000)
        except Exception:
            pass  # 找不到也繼續，後面 regex 會掃

        html = page.content()
        new_urls = extract_post_urls(html) - seen

        if not new_urls:
            print(f"[Pixnet] 第 {p} 頁沒有新文章，掃描完畢")
            break

        seen.update(new_urls)
        urls.extend(sorted(new_urls))
        print(f"  → 本頁新增 {len(new_urls)} 篇（累計 {len(seen)} 篇）")

        # 確認下一頁是否存在
        next_link = page.query_selector(
            f"a[href*='?page={p + 1}'], a[href*=\"page={p + 1}\"]"
        )
        if not next_link:
            print(f"[Pixnet] 找不到第 {p + 1} 頁連結，掃描完畢")
            break

        time.sleep(1.0)

    print(f"[Pixnet] 共找到 {len(urls)} 篇文章連結")
    return urls


# ── Step 2：爬單篇文章並轉 Markdown ─────────────────────────────────────────

def scrape_article(page, url):
    """
    進入文章頁面（JS 渲染後），萃取標題、日期、article-content-inner。
    回傳 dict 或 None。
    """
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_selector(".article-content-inner", timeout=8000)
    except Exception:
        print(f"  [警告] 等待 article-content-inner 超時：{url}")

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

    # ── 內文 ──
    content_div = soup.find("div", class_="article-content-inner")
    if not content_div:
        print(f"  [警告] 找不到 article-content-inner：{url}")
        return None

    for tag in content_div(["style", "script", "ins", "iframe"]):
        tag.decompose()

    content_md = md2(str(content_div), heading_style="ATX").strip()
    if not content_md:
        print(f"  [警告] 內容轉換後為空：{url}")
        return None

    content_md = re.sub(r"\n{3,}", "\n\n", content_md)
    return {"title": title, "date": date_str, "content_md": content_md}


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    saved = skipped_exists = failed = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-TW",
        )
        page = context.new_page()

        # ── 掃描所有文章 URL ──
        article_urls = collect_article_urls(page)
        if not article_urls:
            print("[Pixnet] ❌ 找不到文章，請確認部落格網址是否正確。")
            browser.close()
            return

        # ── 逐篇爬取 ──
        for i, url in enumerate(article_urls, 1):
            post_id = url.rstrip("/").split("/")[-1]
            print(f"[Pixnet] ({i}/{len(article_urls)}) {url}")

            # 增量更新：以 post_id 為 key
            existing = [
                f for f in os.listdir(OUTPUT_DIR)
                if f.endswith(".md") and post_id in f
            ]
            if existing:
                print(f"  [跳過] 已存在（{existing[0]}）")
                skipped_exists += 1
                continue

            data = scrape_article(page, url)
            time.sleep(SLEEP_BETWEEN_ARTICLES)

            if not data:
                failed += 1
                continue

            title_safe = sanitize_filename(data["title"])
            filename   = f"{data['date']}_{title_safe}_{post_id}.md"
            filepath   = os.path.join(OUTPUT_DIR, filename)

            if os.path.exists(filepath):
                skipped_exists += 1
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
        f"  ⏭  已存在  {skipped_exists} 篇\n"
        f"  ❌ 失敗     {failed} 篇\n"
        f"[Pixnet] 存放路徑：{OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
