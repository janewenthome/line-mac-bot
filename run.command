#!/bin/bash
cd ~/line-mac-bot
source .venv/bin/activate

# 1. 強力清空：關掉舊的 Bot、ngrok 和佔位門牌
echo "正在清理環境..."
pkill -f "python3 app.py"
pkill -f "ngrok"
lsof -ti:8080 | xargs kill -9 2>/dev/null

# 2. 啟動 LINE Bot (背景執行)
echo "正在啟動 LINE Bot..."
python3 app.py > app.log 2>&1 &

# 3. 等待 3 秒讓服務熱身
sleep 3

# 4. 啟動 ngrok 隧道 (背景執行)
# 這裡預設是對應 8080 端口
echo "正在建立 ngrok 隧道..."
/opt/homebrew/bin/ngrok http 8080 > /dev/null &

echo "系統已就緒！"