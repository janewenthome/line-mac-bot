#!/bin/bash
# ================================================
# WorldMonitor 啟動器
# 雙擊此檔案即可啟動 WorldMonitor
# ================================================

cd "$(dirname "$0")/worldmonitor_repo" || exit 1

echo "🌍 正在啟動 WorldMonitor..."
echo "   請稍候，伺服器啟動後會自動開啟瀏覽器"
echo ""

# 啟動 dev server（背景執行）
npm run dev &
DEV_PID=$!

# 等待伺服器就緒
echo "⏳ 等待伺服器啟動..."
for i in $(seq 1 15); do
  if curl -s http://localhost:3000 > /dev/null 2>&1; then
    echo "✅ 伺服器已就緒！"
    break
  fi
  sleep 1
done

# 自動開啟瀏覽器
open http://localhost:3000

echo ""
echo "🌍 WorldMonitor 正在運行於 http://localhost:3000"
echo "   按 Ctrl+C 停止伺服器"
echo ""

# 等待 dev server 結束
wait $DEV_PID
