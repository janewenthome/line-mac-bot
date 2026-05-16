#!/usr/bin/env python3
"""
兒童版教案轉換工具
==================
掃描 Obsidian Wiki 中帶有 #待轉兒童版 標籤的文章，
使用 Claude API 轉換成 10 歲孩子能理解的版本，
存入 Youtube兒童教材 資料夾，並自動更新原文章標籤。

LINE 指令：「轉換教案！」
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# ── 載入 .env ──────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
if not OPENROUTER_API_KEY:
    print("❌ 請在 .env 設定 OPENROUTER_API_KEY")
    sys.exit(1)

from openai import OpenAI

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

# ── 路徑設定 ────────────────────────────────────────────────────────────────────
SKILL_CONFIG_PATH = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"
    "/系統設定/SKILL_私人維基建置 (轉換教案！）.md"
)

# ── 預設值（當 SKILL 設定檔讀取失敗時使用）──────────────────────────────────────
DEFAULT_WIKI_ROOT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"
    "/文章存檔/Wiki"
)
DEFAULT_OUTPUT_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"
    "/文章存檔/Wiki/Youtube兒童教材"
)
DEFAULT_TARGET_TAG = "#待轉兒童版"
DEFAULT_DONE_TAG   = "#已轉換"
DEFAULT_MODEL      = "anthropic/claude-opus-4.7"

DEFAULT_PROMPT = """\
你是一位非常有趣的兒童科普老師，你的任務是把一篇大人看的文章，改寫成一篇 10 歲小朋友看得懂、也會喜歡看的教案。

想像你在對國小五年級的學生說話，用他們每天生活會接觸到的事情來解釋艱深的概念。

請照著下面這些規則來寫：

1. 【用身邊的事來比喻】
   把每一個難懂的概念，換成小朋友熟悉的東西來比喻。
   例如：「神經元傳遞訊號，就像 LINE 收到訊息後震動一樣」。

2. 【用故事開場】
   一開始先說一個簡短的小故事或問問題，讓小朋友覺得「咦！這跟我有關！我想繼續看！」

3. 【問問題，讓他們動腦】
   在文章中穿插 2~3 個「小朋友想想看：...？」的問題，讓他們有機會思考。

4. 【知識不能打折】
   雖然語言要簡單，但是文章核心的知識要正確，不能因為要變簡單就說錯。

5. 【最後整理學到什麼】
   用條列式「今天學到了什麼？」結尾，列出 3 到 5 個重點，每點用一句話說清楚。

6. 【用台灣的語氣】
   請用台灣小朋友說話的方式，輕鬆、有活力、溫暖，不要用大陸用語。

7. 【直接開始寫】
   不要加「這是改寫後的版本」之類的開場白，直接從故事或問題開始。
"""


# ── SKILL 設定檔讀取 ────────────────────────────────────────────────────────────

def load_skill_config() -> dict:
    """從 Obsidian SKILL 設定檔讀取轉換參數"""
    config = {
        "model":      DEFAULT_MODEL,
        "prompt":     DEFAULT_PROMPT,
        "wiki_root":  DEFAULT_WIKI_ROOT,
        "target_tag": DEFAULT_TARGET_TAG,
        "done_tag":   DEFAULT_DONE_TAG,
        "output_dir": DEFAULT_OUTPUT_DIR,
        "tags":       ["AI自動整理", "兒童教材", "十歲教材"],
    }

    if not os.path.isfile(SKILL_CONFIG_PATH):
        print(f"  ⚠️  找不到 SKILL 設定檔，使用預設值")
        print(f"     路徑: {SKILL_CONFIG_PATH}")
        return config

    with open(SKILL_CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.findall(r"```(\w*)\n(.*?)```", content, re.DOTALL)
    for lang, body in blocks:
        body = body.strip()
        if lang == "prompt":
            config["prompt"] = body
        elif lang in ("yaml", ""):
            for line in body.split("\n"):
                line = line.strip()
                if ":" in line and not line.startswith("#"):
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key == "model":       config["model"]      = val
                    elif key == "wiki_root": config["wiki_root"]  = os.path.expanduser(val)
                    elif key == "target_tag":config["target_tag"] = val
                    elif key == "done_tag":  config["done_tag"]   = val
                    elif key == "output_dir":config["output_dir"] = os.path.expanduser(val)
        elif lang == "tags":
            config["tags"] = [
                l.lstrip("- ").strip()
                for l in body.split("\n")
                if l.strip().startswith("- ")
            ]

    return config


# ── 掃描帶有目標標籤的 .md 檔案 ─────────────────────────────────────────────────

def find_tagged_files(wiki_root: str, target_tag: str, output_dir: str) -> list:
    """在 Wiki 根目錄下遞迴尋找帶有 target_tag 的 .md 檔案"""
    # 正規化輸出目錄路徑，避免掃描自己的輸出資料夾
    output_dir_norm = os.path.normpath(output_dir)
    tagged = []

    for root, dirs, files in os.walk(wiki_root):
        # 排除輸出資料夾（避免重複掃描）
        dirs[:] = [
            d for d in dirs
            if os.path.normpath(os.path.join(root, d)) != output_dir_norm
            and not d.startswith(".")
        ]

        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                if target_tag in content:
                    tagged.append(fpath)
            except Exception:
                pass

    return tagged


# ── Claude API 呼叫 ──────────────────────────────────────────────────────────────

def convert_to_children(title: str, content: str, config: dict,
                        retry: int = 3) -> str:
    """呼叫 Claude 將文章轉換成兒童版"""
    model  = config["model"]
    prompt = config["prompt"]

    for attempt in range(retry):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user",   "content": f"# {title}\n\n{content}"}
                ],
                max_tokens=4096,
            )
            result = resp.choices[0].message.content or ""
            return result.strip()
        except Exception as e:
            wait = 2 ** (attempt + 1)
            print(f"    ⚠️  API 錯誤（重試 {attempt+1}/{retry}）: {e}")
            if attempt < retry - 1:
                import time; time.sleep(wait)

    return f"[轉換失敗] 原文前 500 字：\n{content[:500]}"


# ── 標籤更新 ─────────────────────────────────────────────────────────────────────

def update_tag_in_file(filepath: str, target_tag: str, done_tag: str):
    """將文章中的 target_tag 替換成 done_tag"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    updated = content.replace(target_tag, done_tag)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(updated)


# ── 主程式 ────────────────────────────────────────────────────────────────────────

def main():
    print("""
╔══════════════════════════════════════════════╗
║         🎒 兒童版教案轉換工具                ║
║    掃描 #待轉兒童版 → 轉換 → 存入 Obsidian    ║
╚══════════════════════════════════════════════╝
    """)

    # 1. 讀取設定
    print("⚙️  讀取 SKILL 設定檔...")
    config = load_skill_config()
    print(f"   模型: {config['model']}")
    print(f"   掃描根目錄: {config['wiki_root']}")
    print(f"   目標標籤: {config['target_tag']}")
    print(f"   輸出目錄: {config['output_dir']}")

    # 2. 掃描帶標籤文章
    print(f"\n🔍 掃描中...")
    tagged_files = find_tagged_files(
        config["wiki_root"], config["target_tag"], config["output_dir"]
    )
    print(f"   找到 {len(tagged_files)} 篇帶有 {config['target_tag']} 標籤的文章")

    if not tagged_files:
        print("\n✅ 沒有需要轉換的文章，結束。")
        return

    # 3. 準備輸出目錄（依年月）
    now        = datetime.now()
    year_str   = now.strftime("%Y")
    month_str  = now.strftime("%m")
    target_dir = os.path.join(
        os.path.expanduser(config["output_dir"]),
        year_str, month_str
    )
    os.makedirs(target_dir, exist_ok=True)

    # 4. 逐篇轉換
    success = 0
    for filepath in tagged_files:
        title = os.path.splitext(os.path.basename(filepath))[0]
        print(f"\n📄 [{success+1}/{len(tagged_files)}] 轉換: {title}")

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            original_content = f.read()

        # 呼叫 API 轉換
        print(f"   🤖 呼叫 {config['model']}...")
        children_content = convert_to_children(title, original_content, config)

        # 建立 Obsidian 連結（指向原文章）
        obsidian_link = f"[[{title}]]"
        tags_yaml     = "\n".join(f"  - {t}" for t in config.get("tags", []))
        now_str       = now.strftime("%Y-%m-%d %H:%M")

        final_md = f"""---
tags:
{tags_yaml}
source_note: "{title}"
converted_at: "{now_str}"
---

> 🔗 原始文章：{obsidian_link}

# 🎒 {title}（兒童版）

{children_content}
"""

        # 儲存輸出
        safe_title  = re.sub(r'[\\/*?:"<>|]', "", title)
        out_filename = f"{safe_title}_兒童版.md"
        out_path     = os.path.join(target_dir, out_filename)

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(final_md)
        print(f"   ✅ 已儲存: {out_path}")

        # 更新原文章標籤
        update_tag_in_file(filepath, config["target_tag"], config["done_tag"])
        print(f"   🏷️  標籤已更新: {config['target_tag']} → {config['done_tag']}")
        success += 1

    # 5. 完成
    print(f"""
╔══════════════════════════════════════════════╗
║              ✅ 轉換完成！                    ║
║  成功轉換: {success:3d} 篇                          ║
║  輸出位置: Wiki/Youtube兒童教材/{year_str}/{month_str}       ║
╚══════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()
