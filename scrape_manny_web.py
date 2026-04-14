"""
scrape_manny_web.py
====================
抓取曼報 Pro 需要 Google 登入的文章頁，轉成 Markdown 存入 Obsidian。

【使用說明】
1. 第一次執行時請保持 headless=False（預設），讓瀏覽器視窗彈出，
   手動完成 Google 登入。登入後 Cookies 會儲存至 ./playwright_profile/。
2. 登入成功後，未來可將 HEADLESS = False 改成 HEADLESS = True，
   讓程式在背景默默執行，不再需要人工介入。

執行方式：
    .venv/bin/python scrape_manny_web.py
"""

import os
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ── 設定區 ────────────────────────────────────────────────────────────────────

# 【切換點】第一次登入成功後，改成 True 可背景執行
HEADLESS = False

TARGET_URL   = "https://pro.manny-li.com/posts?tag=cmg4h4b8m009j01v96umu5gj2"
PROFILE_DIR  = str(Path(__file__).parent / "playwright_profile")
OBSIDIAN_VAULT_PATH = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain"
)
SAVE_DIR = os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "Wiki", "曼報Pro_商業解碼")

# ── 工具函數 ──────────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """移除檔名不允許的字元，截短至 80 字"""
    name = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+]', "", name)
    name = name.strip().replace(" ", "_")
    return name[:80]


def html_to_markdown(html: str) -> str:
    """清洗 HTML 並轉成 Markdown"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["style", "script", "head", "meta"]):
        tag.extract()
    result = md(str(soup), heading_style="ATX")
    result = re.sub(r'\n{3,}', '\n\n', result).strip()
    return result


def wait_for_login(page) -> None:
    """
    手動終端機確認策略：使用者亲眼確認已登入後再按 Enter 続行。
    """
    input(
        "\n[Scraper] 🟢 請在彈出的瀏覽器中確認已登入並看到文章列表。"
        "確認後，請回到此終端機按下 [Enter] 鍵讓程式繼續..."
    )
    print("[Scraper] ✅ 繼續抓取")


def collect_article_links(page) -> list:
    """
    收集列表頁所有文章連結。
    策略：用最寬鬆的 page.locator('a').all() 抓所有連結，
    再對 href 做最寬鬆的過濾（包含網站域名 + /posts/ 路徑），
    不依賴任何特定 CSS class。
    """
    links = set()
    page_num = 1

    while True:
        print(f"[Scraper] 📄 揃描第 {page_num} 頁...")

        # 等頁面載入穩定
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass

        # 用最寬鬆的方式抓取頁面上所有 <a> 的 href
        all_hrefs = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href)"
        )

        before = len(links)
        for href in all_hrefs:
            if not href:
                continue
            # 保留條件：屬於目標網站 且 URL 包含 /posts/ （單篇文章特徵）
            if "pro.manny-li.com" in href and "/posts/" in href:
                # 排除列表頁本身和分頁網址（包含 ?tag= 的為列表）
                if "?" not in href.split("/posts/", 1)[-1]:
                    links.add(href)

        print(f"[Scraper]   本頁新增 {len(links) - before} 篇")

        # 嘗試找》下一頁「按鈕
        next_btn = page.query_selector(
            "a[rel='next'], .pagination-next, "
            "a:has-text('Older Posts'), a:has-text('下一頁'), "
            "a:has-text('Load more'), nav a[href*='page']"
        )
        if next_btn:
            next_btn.click()
            page.wait_for_load_state("networkidle")
            page_num += 1
        else:
            break

    print(f"[Scraper] 🎯 共找到 {len(links)} 篇文章連結")
    return list(links)


def scrape_article(page, url: str) -> tuple:
    """
    進入單篇文章頁，萃取標題與 HTML 內文。
    回傳 (title, markdown_content)。
    """
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(1)   # 稍等 JS 渲染

    # 標題
    title = page.title().split("|")[0].strip()
    try:
        title = page.inner_text("h1.post-full-title, h1.article-title, h1",
                                timeout=5000).strip().split("\n")[0]
    except Exception:
        pass

    # 內文 HTML（優先 <article> 或 Ghost 慣用 class）
    content_html = ""
    for selector in [
        "article .gh-content",
        "article .post-content",
        "article",
        ".gh-content",
        ".post-content",
        "main",
    ]:
        try:
            content_html = page.inner_html(selector, timeout=3000)
            if len(content_html) > 200:
                break
        except Exception:
            continue

    if not content_html:
        print(f"[Scraper] ⚠️  找不到內文：{url}")
        return title, ""

    return title, html_to_markdown(content_html)


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    print(f"[Scraper] 存放路徑：{SAVE_DIR}")
    print(f"[Scraper] Profile 路徑：{PROFILE_DIR}")

    with sync_playwright() as p:
        # 永續 Context：Cookies 跨次執行保留
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=HEADLESS,
            viewport={"width": 1280, "height": 900},
            locale="zh-TW",
        )
        page = context.pages[0] if context.pages else context.new_page()

        # 前往目標列表頁
        print(f"[Scraper] 🌐 前往：{TARGET_URL}")
        page.goto(TARGET_URL, wait_until="domcontentloaded")

        # 智慧登入等待
        wait_for_login(page)

        # 收集文章連結
        links = collect_article_links(page)
        if not links:
            print("[Scraper] ❌ 未找到任何文章連結，程式結束")
            context.close()
            return

        saved   = 0
        skipped = 0

        for url in links:
            try:
                title, content = scrape_article(page, url)

                if not content.strip():
                    print(f"[Scraper] ⚠️  內容為空，跳過：{title}")
                    continue

                # 從 URL 擷取日期（Ghost 文章常含日期段）
                date_match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
                date_str = (
                    f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
                    if date_match else ""
                )
                safe_title = sanitize_filename(title)
                filename   = f"{date_str + '_' if date_str else ''}{safe_title}.md"
                filepath   = os.path.join(SAVE_DIR, filename)

                # 增量更新：已存在就跳過
                if os.path.exists(filepath):
                    skipped += 1
                    continue

                # 寫入 Markdown（含 frontmatter）
                front = (
                    "---\n"
                    f"title: \"{title}\"\n"
                    f"{'date: ' + date_str + chr(10) if date_str else ''}"
                    "source: manny-li.com\n"
                    "tags: [曼報, 商業解碼, 電子報]\n"
                    "---\n\n"
                    f"# {title}\n\n"
                )
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(front + content + "\n")

                saved += 1
                print(f"[Scraper] ➕ 已存：{filename}")

            except Exception as e:
                print(f"[Scraper] ❌ 處理失敗：{url} — {e}")

        print(f"\n[Scraper] 完成！新增 {saved} 篇 | 跳過（已存在）{skipped} 篇")
        print(f"[Scraper] 存放路徑：{SAVE_DIR}")

        context.close()


if __name__ == "__main__":
    main()
