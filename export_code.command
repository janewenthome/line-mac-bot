#!/usr/bin/env python3
"""
export_code.command
────────────────────────────────────────────────────────────────
掃描腳本所在資料夾的所有程式碼檔案，將內容格式化後複製到剪貼簿。
雙擊 Finder 即可執行（需先 chmod +x export_code.command）。
"""

import os
import subprocess
import sys

# ── 設定區 ────────────────────────────────────────────────────────────────────

# 只讀取這些副檔名
INCLUDE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".yaml", ".yml", ".toml",
    ".md", ".txt", ".sh", ".sql",
    ".html", ".css", ".env.example", ".cfg", ".ini",
}

# 略過這些資料夾名稱
SKIP_DIRS = {
    "venv", ".venv", "env", "__pycache__", ".git", ".idea",
    "node_modules", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".DS_Store", "worldmonitor_repo", "worldmonitor"
}

# 略過這些副檔名（二進位 / 媒體）
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".exe", ".bin", ".pyc",
    ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2", ".ttf",
}

# 單一檔案大小上限（超過則略過，避免塞入超大檔）
MAX_FILE_BYTES = 200_000  # 200 KB

# ── 核心邏輯 ──────────────────────────────────────────────────────────────────

def should_include(filepath: str) -> bool:
    """判斷此檔案是否應被納入輸出。"""
    basename = os.path.basename(filepath)

    # 略過隱藏檔
    if basename.startswith("."):
        return False

    # 略過超大檔
    try:
        if os.path.getsize(filepath) > MAX_FILE_BYTES:
            return False
    except OSError:
        return False

    _, ext = os.path.splitext(basename)
    ext = ext.lower()

    # 副檔名白名單（優先判斷）
    if ext in INCLUDE_EXTENSIONS:
        return True

    # 副檔名黑名單
    if ext in SKIP_EXTENSIONS:
        return False

    # 無副檔名的特殊檔直接略過
    return False


def collect_files(root: str) -> list[str]:
    """遞迴掃描 root 目錄，回傳所有符合條件的檔案路徑（已排序）。"""
    collected = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 就地修改 dirnames 以略過特定子目錄（os.walk 會遵守）
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]

        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if should_include(full_path):
                collected.append(full_path)

    collected.sort()
    return collected


def format_output(root: str, files: list[str]) -> str:
    """將每個檔案包裝成固定格式的區塊，組合為一個大字串。"""
    blocks = []
    for fpath in files:
        rel = os.path.relpath(fpath, root)
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as exc:
            content = f"[讀取失敗：{exc}]"

        block = (
            "========================================\n"
            f"檔案名稱：{rel}\n"
            "========================================\n"
            f"{content}\n"
        )
        blocks.append(block)

    return "\n".join(blocks)


def copy_to_clipboard(text: str) -> None:
    """使用 macOS pbcopy 將文字複製到剪貼簿。"""
    proc = subprocess.run(
        ["pbcopy"],
        input=text.encode("utf-8"),
        check=True,
    )


def main() -> None:
    # 腳本所在目錄即為掃描根目錄
    root = os.path.dirname(os.path.abspath(__file__))

    print(f"🔍  掃描目錄：{root}\n")

    files = collect_files(root)

    if not files:
        print("⚠️  找不到任何符合條件的程式碼檔案。")
        sys.exit(0)

    output = format_output(root, files)

    try:
        copy_to_clipboard(output)
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] 複製到剪貼簿失敗：{exc}")
        sys.exit(1)

    print(
        f"✅ 已經成功將 {len(files)} 個檔案的程式碼複製到剪貼簿！"
        " 可以直接貼到 Gemini 了。"
    )
    print("\n已納入的檔案：")
    root_len = len(root) + 1
    for f in files:
        print(f"   • {f[root_len:]}")


if __name__ == "__main__":
    main()
