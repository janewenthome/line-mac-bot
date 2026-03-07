"""
scrape_pixnet_v2.py
====================
改用『列表頁掃描法』以解決 Sitemap 失敗的問題。
策略：逐頁掃描 /blog/listall/N，抓出文章 URL，再逐篇爬取內文。

【套件需求】首次使用請先執行：
    .venv/bin/pip install requests beautifulsoup4 markdownify

執行方式：
    .venv/bin/python scrape_pixnet_v2.py
"""

import os
import re
import time

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ── 設定區 ────────────────────────────────────────────────────────────────────

BLOG_ID      = "wenthome"
LIST_BASE    = f"https://{BLOG_ID}.pixnet.net/blog/listall"
POST_PATTERN = re.compile(
    rf"https://{BLOG_ID}\.pixnet\.net/blog/post/\d+"
)

OUTPUT_DIR = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain/"
    "文章存檔/Pixnet貼文"
)

SLEEP_SEC = 1.5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── 工具函數 ──────────────────────────────────────────────────────────────────

def sanitize_filename(text, max_len=60):
    """去除不合法檔名字元，截短至 max_len。"""
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+\[\]]', "", text)
    text = text.strip().replace(" ", "_")
    return text[:max_len]


def fetch(url):
    """帶 User-Agent 的安全 GET，失敗回傳 None（相容 Python 3.9）。"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  [錯誤] 抓取失敗：{url}\n         {e}")
        return None


# ── Step 1：首頁分頁掃描，收集所有文章 URL ─────────────────────────────────────

BLOG_HOME = f"https://{BLOG_ID}.pixnet.net/blog"

# 文章 URL 的兩種格式（絕對路徑 & 相對路徑）
ABS_POST = re.compile(
    rf"https?://{BLOG_ID}\.pixnet\.net/blog/post/(\d+)"
)
REL_POST = re.compile(r"/blog/post/(\d+)")


def _collect_posts_from_page(html: str):
    """從單頁 HTML 中萃取所有文章 URL（絕對 + 相對都抓）。"""
    found = set()
    # 方法 A：正則掃全頁絕對路徑
    for m in ABS_POST.finditer(html):
        found.add(
            f"https://{BLOG_ID}.pixnet.net/blog/post/{m.group(1)}"
        )
    # 方法 B：正則掃全頁相對路徑
    for m in REL_POST.finditer(html):
        found.add(
            f"https://{BLOG_ID}.pixnet.net/blog/post/{m.group(1)}"
        )
    return found


def get_article_urls():
    """
    從 /blog 首頁開始，逐頁（?page=N）掃描所有文章連結。
    自動翻頁：當頁面中出現 ?page=N 的連結就繼續抓，直到沒有新文章為止。
    """
    seen   = set()
    urls   = []
    page   = 1
    MAX_PAGES = 200   # 安全上限，通常不會跑滿

    while page <= MAX_PAGES:
        page_url = BLOG_HOME if page == 1 else f"{BLOG_HOME}?page={page}"
        print(f"[Pixnet] 掃描第 {page} 頁：{page_url}")

        resp = fetch(page_url)
        if not resp:
            print(f"[Pixnet] 第 {page} 頁請求失敗，停止")
            break
        time.sleep(SLEEP_SEC)

        posts = _collect_posts_from_page(resp.text)
        new_posts = posts - seen
        if not new_posts:
            print(f"[Pixnet] 第 {page} 頁沒有新文章，掃描完畢")
            break

        seen.update(new_posts)
        urls.extend(sorted(new_posts))   # 維持可重現的順序
        print(f"  → 本頁新增 {len(new_posts)} 篇（累計 {len(seen)} 篇）")

        # 確認下一頁真的存在（next page link 或 page+1 link）
        next_page = page + 1
        next_pattern = re.compile(
            rf'["\']({re.escape(BLOG_HOME)}\?page={next_page}|/blog\?page={next_page})["\']'
        )
        if not next_pattern.search(resp.text):
            print(f"[Pixnet] 找不到第 {next_page} 頁的連結，掃描完畢")
            break

        page += 1

    print(f"[Pixnet] 共找到 {len(urls)} 篇文章連結")
    return urls



# ── Step 2：解析單篇文章 ───────────────────────────────────────────────────────

def parse_article(url):
    """
    抓取標題、日期、<div class="article-content-inner"> 並轉成 Markdown。
    回傳 dict 或 None。
    """
    resp = fetch(url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── 標題 ──
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif soup.title:
        title = soup.title.get_text(strip=True)
    else:
        title = "無標題"
    title = re.sub(r"\s*[|\-–—]\s*痞客邦.*$", "", title).strip()

    # ── 日期 ──
    date_str = ""
    pub_meta = soup.find("meta", property="article:published_time")
    if pub_meta and pub_meta.get("content"):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", pub_meta["content"])
        if m:
            date_str = m.group(1)

    if not date_str:
        # Fallback：掃 HTML 中的日期
        m = re.search(r"(\d{4}-\d{2}-\d{2})", resp.text)
        date_str = m.group(1) if m else "0000-00-00"

    # ── 內文 ──
    content_div = soup.find("div", class_="article-content-inner")
    if not content_div:
        print(f"  [警告] 找不到 article-content-inner：{url}")
        return None

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
        print("[Pixnet] ❌ 未找到任何文章網址，請確認部落格 ID 與網路連線。")
        return

    saved = skipped_exists = failed = 0

    for i, url in enumerate(urls, 1):
        print(f"[Pixnet] ({i}/{len(urls)}) {url}")

        # 精確增量更新：以文章 ID 為 key 預判是否已爬過
        post_id = url.rstrip("/").split("/")[-1]
        existing = [
            f for f in os.listdir(OUTPUT_DIR)
            if f.endswith(".md") and post_id in f
        ]
        if existing:
            print(f"  [跳過] 已存在（{existing[0]}）")
            skipped_exists += 1
            time.sleep(0.2)
            continue

        data = parse_article(url)
        time.sleep(SLEEP_SEC)

        if not data:
            failed += 1
            continue

        title_safe = sanitize_filename(data["title"])
        filename   = f"{data['date']}_{title_safe}_{post_id}.md"
        filepath   = os.path.join(OUTPUT_DIR, filename)

        if os.path.exists(filepath):
            print(f"  [跳過] 已存在：{filename}")
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

    print(
        f"\n[Pixnet] 🎉 完成！\n"
        f"  ✅ 新增      {saved} 篇\n"
        f"  ⏭  已存在  {skipped_exists} 篇\n"
        f"  ❌ 失敗     {failed} 篇\n"
        f"[Pixnet] 存放路徑：{OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
