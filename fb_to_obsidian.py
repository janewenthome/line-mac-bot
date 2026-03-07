"""
fb_to_obsidian.py  v2.0
========================
將 Facebook 匯出的 JSON 貼文，轉換成 Obsidian Markdown 筆記。
v2.0 新增：雜訊過濾器 + 內嵌留言萃取（### 💬 當時的迴響）

執行方式：
    .venv/bin/python fb_to_obsidian.py

⚠️  如果有多個 JSON 分卷（_1, _2, _3...），在 INPUT_FILES 清單追加即可。
"""

import json
import os
import re
from datetime import datetime, timezone

# ── 路徑設定 ──────────────────────────────────────────────────────────────────

INPUT_FILES = [
    "/Users/wenhung/Downloads/your_facebook_activity/posts/"
    "your_posts__check_ins__photos_and_videos_1.json",
]

OUTPUT_DIR = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain/"
    "文章存檔/FB貼文"
)

# ── 雜訊過濾器【v2.0 新增】───────────────────────────────────────────────────
# 只要內文包含以下任一關鍵字，該篇直接跳過

IGNORE_KEYWORDS = [
    "生日快樂",
    "Happy Birthday",
]

# ── FB 專屬編碼修復（latin1 誤讀 → 還原 UTF-8）───────────────────────────────

def fix_fb_encoding(text: str) -> str:
    """修復 Facebook JSON 對中文的錯誤編碼。"""
    if not text:
        return ""
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# ── 檔名安全化 ────────────────────────────────────────────────────────────────

def sanitize(text: str, max_len: int = 15) -> str:
    """取前 max_len 個字，去除換行與特殊符號，用於組合檔名。"""
    text = re.sub(r"[\r\n\t]", " ", text)
    text = re.sub(r'[\\/*?:"<>|#%&{}$!\'@`=+\[\]]', "", text)
    text = text.strip().replace(" ", "_")
    return text[:max_len]


# ── 雙通道內文萃取 ─────────────────────────────────────────────────────────────

def extract_content(item: dict) -> str:
    """
    通道 A：item['data'][0]['post']          → 早期純文字貼文
    通道 B：item['attachments'][*]['data'][*]['media']['title']
                                             → 照片描述 / 心得文字
    兩通道結果合併並去重，以換行分隔。
    """
    parts = []

    # 通道 A
    post_a = item.get("data", [{}])
    if post_a and isinstance(post_a, list):
        text_a = post_a[0].get("post", "")
        if text_a:
            parts.append(fix_fb_encoding(text_a))

    # 通道 B
    for attach in item.get("attachments", []):
        for data_item in attach.get("data", []):
            title = data_item.get("media", {}).get("title", "")
            if title:
                fixed = fix_fb_encoding(title)
                if fixed not in parts:
                    parts.append(fixed)

    return "\n\n".join(parts)


# ── 留言萃取【v2.0 新增】────────────────────────────────────────────────────

def extract_comments(item: dict) -> str:
    """
    從 item['comments'] 萃取留言，格式化為 Markdown 列表。
    回傳空字串表示無留言。
    """
    comments = item.get("comments", [])
    if not comments:
        return ""

    lines = ["", "### 💬 當時的迴響", ""]
    for c in comments:
        author  = fix_fb_encoding(c.get("author", "匿名"))
        comment = fix_fb_encoding(c.get("comment", ""))
        if comment.strip():
            lines.append(f"- **{author}**：{comment}")

    # 若只有標題沒有實際留言行，不輸出
    if len(lines) <= 3:
        return ""

    return "\n".join(lines) + "\n"


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    saved = skipped_empty = skipped_noise = skipped_exists = 0

    for json_path in INPUT_FILES:
        if not os.path.isfile(json_path):
            print(f"[警告] 找不到輸入檔案：{json_path}")
            continue

        print(f"[FB轉換] 讀取：{json_path}")
        with open(json_path, "r", encoding="utf-8") as f:
            posts = json.load(f)

        # Facebook 匯出格式：最外層可能是 list 或 dict{"posts": [...]}
        if isinstance(posts, dict):
            posts = posts.get("posts", [])

        for item in posts:
            # 1. 萃取內文（雙通道）
            content = extract_content(item)
            if not content.strip():
                skipped_empty += 1
                continue

            # 2. 雜訊過濾器【v2.0】
            if any(kw in content for kw in IGNORE_KEYWORDS):
                print(f"[FB轉換] 🔇 雜訊過濾，跳過：{content[:30].strip()!r}")
                skipped_noise += 1
                continue

            # 3. 日期
            ts = item.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
            date_str = dt.strftime("%Y-%m-%d")

            # 4. 組合檔名
            title_slug = sanitize(content)
            filename   = f"{date_str}_{title_slug}.md"
            filepath   = os.path.join(OUTPUT_DIR, filename)

            # 5. 增量更新：已存在則跳過
            if os.path.exists(filepath):
                skipped_exists += 1
                continue

            # 6. 萃取留言【v2.0】
            comments_md = extract_comments(item)

            # 7. 寫入 Markdown（含 YAML Frontmatter）
            md = (
                "---\n"
                f"date: {date_str}\n"
                "tags: [FB備份, 歷史迴音]\n"
                "---\n\n"
                f"# {date_str} 臉書紀錄\n\n"
                f"{content}\n"
                f"{comments_md}"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md)

            saved += 1
            print(f"[FB轉換] ➕ {filename}")

    print(
        f"\n[FB轉換] 🎉 完成！\n"
        f"  ✅ 新增       {saved} 篇\n"
        f"  🔇 雜訊過濾  {skipped_noise} 篇\n"
        f"  ⬜ 空內容    {skipped_empty} 篇\n"
        f"  ⏭  已存在   {skipped_exists} 篇"
    )
    print(f"[FB轉換] 存放路徑：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
