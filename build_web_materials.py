#!/usr/bin/env python3
"""
雙語自學教材自動化（轉換與排版總裝）
=====================================
讀取 Obsidian SKILL 設定檔中的 YouTube 連結與本地資料夾，
透過兩階段 AI 管線產出雙層 Obsidian 教材：
  階段一：Gemini 提煉家長版深度知識
  階段二：Claude 轉譯兒童版 + 排版組裝

LINE 指令：「製作網頁教材！」
"""

import os
import re
import sys
import signal
import hashlib
import sqlite3
import time
import argparse
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# ── 載入 .env ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    print("❌ 請在 .env 設定 OPENROUTER_API_KEY")
    sys.exit(1)

from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

import requests

# ── 路徑設定 ────────────────────────────────────────────────────────────────────
SKILL_CONFIG_PATH = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"
    "/系統設定/SKILL_雙語自學教材自動化（轉換與排版總裝！.md"
)
DB_PATH = os.path.join(SCRIPT_DIR, "web_materials_processed.db")

# ── Graceful stop ──────────────────────────────────────────────────────────────
_stop_requested = False

def _handle_signal(signum, frame):
    global _stop_requested
    _stop_requested = True
    print("\n⚠️  收到停止訊號，將在當前任務完成後停止...")

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ══════════════════════════════════════════════════════════════════════════════
#  SKILL 設定檔解析
# ══════════════════════════════════════════════════════════════════════════════

def load_skill_config() -> dict:
    """解析 SKILL 設定檔：兩階段 model/prompt + 來源 + 輸出"""
    config = {
        # 階段一
        "stage1_model": "google/gemini-3.1-pro-preview",
        "stage1_prompt": "請總結此文",
        # 階段二
        "stage2_model": "anthropic/claude-opus-4.7",
        "stage2_prompt": "請改寫成兒童版",
        # 來源
        "youtube_links": [],
        "source_dirs": [],
        "whitelist": [".pdf", ".md", ".txt"],
        # 輸出
        "output_dir": "",
        "tags": ["AI自動整理", "雙語自學教材", "十歲教材", "已轉換"],
    }

    if not os.path.isfile(SKILL_CONFIG_PATH):
        print(f"⚠️  找不到 SKILL 設定檔: {SKILL_CONFIG_PATH}")
        return config

    with open(SKILL_CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # ── 解析階段一/二的 model 和 prompt ─────────────────────────────────────
    # 尋找 ### 【階段一 和 ### 【階段二 之間的區段
    stage1_match = re.search(
        r'###\s*【階段一[^】]*】(.*?)(?=###\s*【階段二|$)', content, re.DOTALL
    )
    stage2_match = re.search(
        r'###\s*【階段二[^】]*】(.*?)(?=##\s|$)', content, re.DOTALL
    )

    if stage1_match:
        section = stage1_match.group(1)
        model_m = re.search(r'model:\s*(\S+)', section)
        if model_m:
            config["stage1_model"] = model_m.group(1)
        prompt_m = re.search(r'```prompt\n(.*?)```', section, re.DOTALL)
        if prompt_m:
            config["stage1_prompt"] = prompt_m.group(1).strip()

    if stage2_match:
        section = stage2_match.group(1)
        model_m = re.search(r'model:\s*(\S+)', section)
        if model_m:
            config["stage2_model"] = model_m.group(1)
        prompt_m = re.search(r'```prompt\n(.*?)```', section, re.DOTALL)
        if prompt_m:
            config["stage2_prompt"] = prompt_m.group(1).strip()

    # ── 解析 yaml 區塊中的 youtube_links, source_dirs, whitelist ──────────
    # youtube_links（在 YAML 區塊外直接解析）
    yt_section = re.search(r'youtube_links:\s*\n((?:\s+-\s+.*\n)*)', content)
    if yt_section:
        links = re.findall(r"-\s+'([^']+)'", yt_section.group(1))
        if not links:
            links = re.findall(r'-\s+"([^"]+)"', yt_section.group(1))
        if not links:
            links = re.findall(r'-\s+(\S+)', yt_section.group(1))
        config["youtube_links"] = [l for l in links if '範例' not in l and 'example' not in l.lower()]

    sd_section = re.search(r'source_dirs:\s*\n((?:\s+-\s+.*\n)*)', content)
    if sd_section:
        dirs = re.findall(r"-\s+'([^']+)'", sd_section.group(1))
        if not dirs:
            dirs = re.findall(r'-\s+"([^"]+)"', sd_section.group(1))
        config["source_dirs"] = dirs

    wl_section = re.search(r'whitelist:\s*\n((?:\s+-\s+.*\n)*)', content)
    if wl_section:
        exts = re.findall(r'-\s+(\.\w+)', wl_section.group(1))
        if exts:
            config["whitelist"] = exts

    # ── 解析 output_dir（在 ```yaml 區塊中）──────────────────────────────────
    yaml_blocks = re.findall(r'```yaml\n(.*?)```', content, re.DOTALL)
    for block in yaml_blocks:
        od_m = re.search(r'output_dir:\s*(.+)', block)
        if od_m:
            config["output_dir"] = od_m.group(1).strip()

    # ── 解析 tags（在 ```tags 區塊中）────────────────────────────────────────
    tags_blocks = re.findall(r'```tags\n(.*?)```', content, re.DOTALL)
    for block in tags_blocks:
        tags = re.findall(r'-\s+(.+)', block)
        if tags:
            config["tags"] = [t.strip() for t in tags]

    return config


# ══════════════════════════════════════════════════════════════════════════════
#  SQLite 追蹤（防止重複處理）
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_items (
            item_key TEXT PRIMARY KEY,
            item_hash TEXT,
            processed_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def is_processed(item_key: str, item_hash: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT item_hash FROM processed_items WHERE item_key=?", (item_key,))
    row = cur.fetchone()
    conn.close()
    return row is not None and row[0] == item_hash


def mark_processed(item_key: str, item_hash: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO processed_items (item_key, item_hash, processed_at)
        VALUES (?, ?, ?)
        ON CONFLICT(item_key) DO UPDATE SET
            item_hash=excluded.item_hash, processed_at=excluded.processed_at
    """, (item_key, item_hash, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_file_hash(filepath: str) -> str:
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
    except Exception:
        return ""
    return hasher.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
#  內容擷取
# ══════════════════════════════════════════════════════════════════════════════

def get_youtube_video_id(url: str) -> str | None:
    match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11}).*", url)
    return match.group(1) if match else None


def fetch_youtube_content(url: str) -> tuple[str, str] | None:
    """取得 YouTube 影片標題與字幕逐字稿，失敗回傳 None"""
    if not YouTubeTranscriptApi:
        print("  ❌ 未安裝 youtube-transcript-api")
        return None

    video_id = get_youtube_video_id(url)
    if not video_id:
        print(f"  ❌ 無效的 YouTube 網址: {url}")
        return None

    # 取得標題
    title = f"YouTube_{video_id}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200 and BeautifulSoup:
            soup = BeautifulSoup(res.text, 'html.parser')
            if soup.title:
                title = soup.title.string.replace(" - YouTube", "").strip()
                title = re.sub(r'[\\/*?:"<>|]', "", title)
    except Exception:
        pass

    # 取得字幕
    try:
        yt_api = YouTubeTranscriptApi()
        transcript = yt_api.list(video_id).find_transcript(
            ['zh-TW', 'zh-Hant', 'zh-Hans', 'zh-HK', 'zh-CN', 'zh', 'en']
        )
        transcript_data = transcript.fetch()
        text_content = "\n".join([t.text for t in transcript_data])
    except Exception as e:
        print(f"  ❌ 無法取得 YouTube 字幕: {e}")
        return None

    return title, text_content


def read_local_file(filepath: str) -> str | None:
    """讀取本地文件內容"""
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".pdf":
            if not PDF_SUPPORT:
                print(f"  ❌ 未安裝 pypdf，跳過 {filepath}")
                return None
            reader = PdfReader(filepath)
            text = ""
            for i in range(min(len(reader.pages), 100)):
                page_text = reader.pages[i].extract_text()
                if page_text:
                    text += page_text + "\n"
            return text[:300000]
        elif ext in [".md", ".txt"]:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        else:
            print(f"  ⏭️  不支援的格式 {ext}，跳過")
            return None
    except Exception as e:
        print(f"  ❌ 讀取失敗: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  兩階段 AI 管線
# ══════════════════════════════════════════════════════════════════════════════

def safe_api_call(model: str, messages: list, max_retries: int = 3) -> str:
    """呼叫 OpenRouter API，失敗自動重試"""
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            result = resp.choices[0].message.content or ""
            return result.strip()
        except Exception as e:
            wait = 2 ** (attempt + 1) * 5
            print(f"    ⚠️  API 錯誤（重試 {attempt+1}/{max_retries}）: {e}")
            if attempt < max_retries - 1:
                time.sleep(wait)
    return ""


def stage1_parent_version(raw_text: str, title: str, config: dict) -> str:
    """階段一：Gemini 提煉家長版深度知識"""
    model = config["stage1_model"]
    prompt = config["stage1_prompt"]

    print(f"  🧠 階段一：Gemini 提煉家長版 ({model})...")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"文章/影片標題：{title}\n\n原始內容：\n{raw_text}"}
    ]
    result = safe_api_call(model, messages)

    # 清理 markdown 包裹
    if result.startswith("```markdown"):
        result = result[11:]
    if result.startswith("```"):
        result = result[3:]
    if result.endswith("```"):
        result = result[:-3]
    result = result.strip()

    if not result:
        print("  ❌ 階段一回傳空值")
    else:
        print(f"  ✅ 階段一完成（{len(result)} 字）")

    return result


def stage2_children_and_assemble(raw_text: str, parent_content: str,
                                  title: str, source_url_or_name: str,
                                  config: dict) -> str:
    """階段二：Claude 轉譯兒童版 + 排版組裝"""
    model = config["stage2_model"]
    prompt = config["stage2_prompt"]

    # 將 prompt 中的模板變數替換
    actual_prompt = prompt.replace("{{來源網址或檔名}}", source_url_or_name)

    print(f"  🎨 階段二：Claude 兒童版+組裝 ({model})...")
    user_content = (
        f"【原始文章/影片標題】\n{title}\n\n"
        f"【原始文本摘要（供你理解主題用）】\n{raw_text[:5000]}\n\n"
        f"【階段一 Gemini 生成的家長版內容】\n{parent_content}\n\n"
        f"【來源網址或檔名】\n{source_url_or_name}"
    )

    messages = [
        {"role": "system", "content": actual_prompt},
        {"role": "user", "content": user_content}
    ]
    result = safe_api_call(model, messages)

    # 清理 markdown 包裹
    if result.startswith("```markdown"):
        result = result[11:]
    if result.startswith("```"):
        result = result[3:]
    if result.endswith("```"):
        result = result[:-3]
    result = result.strip()

    if not result:
        print("  ❌ 階段二回傳空值")
    else:
        print(f"  ✅ 階段二完成（{len(result)} 字）")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  輸出
# ══════════════════════════════════════════════════════════════════════════════

def save_material(final_content: str, title: str, source: str,
                  config: dict) -> str:
    """將最終教材存入 output_dir/YYYY/MM/"""
    output_dir = os.path.expanduser(config["output_dir"])
    now = datetime.now()
    target_dir = os.path.join(output_dir, now.strftime("%Y"), now.strftime("%m"))
    os.makedirs(target_dir, exist_ok=True)

    # Frontmatter
    tags_yaml = "\n".join(f"  - {t}" for t in config.get("tags", []))
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    final_md = f"""---
tags:
{tags_yaml}
source: "{source}"
processed_at: "{now_str}"
publish: true
---

{final_content}
"""

    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)
    safe_title = safe_title.replace(" ", "_")[:80]
    out_filename = f"{safe_title}.md"
    out_path = os.path.join(target_dir, out_filename)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(final_md)

    return out_path


# ══════════════════════════════════════════════════════════════════════════════
#  主程式
# ══════════════════════════════════════════════════════════════════════════════

def process_single_item(title: str, raw_text: str,
                        source_url_or_name: str, config: dict) -> bool:
    """處理單一來源（YouTube 或本地文件）"""
    # 階段一：Gemini 家長版
    parent_content = stage1_parent_version(raw_text, title, config)
    if not parent_content:
        return False

    # 階段二：Claude 兒童版 + 組裝
    final_content = stage2_children_and_assemble(
        raw_text, parent_content, title, source_url_or_name, config
    )
    if not final_content:
        return False

    # 儲存
    out_path = save_material(final_content, title, source_url_or_name, config)
    print(f"  📁 已儲存: {out_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="雙語自學教材自動化")
    parser.add_argument("--force", action="store_true", help="強制重新處理所有項目")
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════╗
║   📚 雙語自學教材自動化（轉換與排版總裝）        ║
║   Gemini 家長版 → Claude 兒童版 → Obsidian 組裝  ║
╚══════════════════════════════════════════════════╝
    """)

    # 1. 載入設定
    print("⚙️  讀取 SKILL 設定檔...")
    config = load_skill_config()
    init_db()

    if not config["output_dir"]:
        print("❌ SKILL 設定檔中未指定 output_dir")
        sys.exit(1)

    print(f"   階段一模型: {config['stage1_model']}")
    print(f"   階段二模型: {config['stage2_model']}")
    print(f"   輸出目錄: {config['output_dir']}")
    print(f"   YouTube 連結: {len(config['youtube_links'])} 個")
    print(f"   本地資料夾: {len(config['source_dirs'])} 個")
    print(f"   白名單: {config['whitelist']}")
    print(f"   標籤: {config['tags']}")

    processed_count = 0
    skipped_count = 0
    failed_count = 0

    # 2. 處理 YouTube 影片
    if config["youtube_links"]:
        print(f"\n🎬 開始處理 YouTube 影片（共 {len(config['youtube_links'])} 部）")
        for idx, url in enumerate(config["youtube_links"], 1):
            if _stop_requested:
                print("🛑 收到停止訊號，中止處理")
                break

            print(f"\n{'='*60}")
            print(f"[{idx}/{len(config['youtube_links'])}] ▶️  {url}")

            item_hash = "YT_URL"
            if not args.force and is_processed(url, item_hash):
                print("  ⏭️  已處理過，跳過")
                skipped_count += 1
                continue

            result = fetch_youtube_content(url)
            if not result:
                failed_count += 1
                continue

            yt_title, yt_text = result
            success = process_single_item(yt_title, yt_text, url, config)

            if success:
                mark_processed(url, item_hash)
                processed_count += 1
            else:
                failed_count += 1

    # 3. 處理本地文件
    if config["source_dirs"] and not _stop_requested:
        print(f"\n📂 開始處理本地資料夾（共 {len(config['source_dirs'])} 個）")
        for s_dir in config["source_dirs"]:
            if _stop_requested:
                break

            s_dir_exp = os.path.expanduser(s_dir)
            if not os.path.isdir(s_dir_exp):
                print(f"\n⚠️  目錄不存在或外接硬碟未連接: {s_dir_exp}，跳過")
                continue

            print(f"\n🚀 掃描目錄: {s_dir_exp}")
            for root, dirs, files in os.walk(s_dir_exp):
                if _stop_requested:
                    break

                # 排除隱藏目錄
                dirs[:] = [d for d in dirs if not d.startswith(".")]

                for fname in sorted(files):
                    if _stop_requested:
                        break

                    if fname.startswith("."):
                        continue

                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in config["whitelist"]:
                        continue

                    filepath = os.path.join(root, fname)
                    f_hash = get_file_hash(filepath)

                    if not args.force and is_processed(filepath, f_hash):
                        skipped_count += 1
                        continue

                    title = os.path.splitext(fname)[0]
                    print(f"\n{'='*60}")
                    print(f"📄 {title}")

                    raw_text = read_local_file(filepath)
                    if not raw_text:
                        failed_count += 1
                        continue

                    success = process_single_item(
                        title, raw_text, fname, config
                    )

                    if success:
                        mark_processed(filepath, f_hash)
                        processed_count += 1
                    else:
                        failed_count += 1

    # 4. 完成
    stop_reason = "（使用者中止）" if _stop_requested else ""
    print(f"""
╔══════════════════════════════════════════════════╗
║              ✅ 執行完畢 {stop_reason:20s}       ║
║  成功處理: {processed_count:3d} 篇                          ║
║  略過重複: {skipped_count:3d} 篇                          ║
║  處理失敗: {failed_count:3d} 篇                          ║
╚══════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()
