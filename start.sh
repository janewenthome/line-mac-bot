#!/bin/bash
# 啟動 LINE Mac Bot

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
    echo "❌ 找不到 .env 檔案！請先執行："
    echo "   cp .env.example .env"
    echo "   然後填入你的 API Keys"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "❌ 找不到虛擬環境！請先執行：bash setup.sh"
    exit 1
fi

echo "🤖 啟動 LINE Mac Bot..."
source .venv/bin/activate
python app.py
