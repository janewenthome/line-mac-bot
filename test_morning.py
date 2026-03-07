"""臨時測試腳本：直接執行 morning_briefing()"""
from dotenv import load_dotenv
load_dotenv()

# 匯入後會啟動 APScheduler（正常現象，測試結束後即停止）
from app import morning_briefing

if __name__ == "__main__":
    print("=== 開始測試 morning_briefing() ===")
    morning_briefing()
    print("=== 測試結束 ===")
