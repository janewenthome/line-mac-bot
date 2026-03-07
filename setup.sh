#!/bin/bash
# LINE Mac Bot 安裝腳本

set -e
cd "$(dirname "$0")"

echo "=== LINE Mac Bot 安裝中 ==="

# 建立虛擬環境
echo "建立 Python 虛擬環境..."
python3 -m venv .venv

# 啟動虛擬環境並安裝套件
echo "安裝套件..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt

echo ""
echo "✅ 安裝完成！"
echo ""
echo "下一步："
echo "1. 複製設定檔：cp .env.example .env"
echo "2. 編輯 .env 填入你的 API Keys"
echo "3. 啟動 Bot：bash start.sh"
