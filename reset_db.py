import os
import shutil

BASE_DIR = os.path.expanduser("~/line-mac-bot")
CHROMA_DB_PATH = os.path.join(BASE_DIR, "chroma_db")

def reset_chroma_db():
    print("⚠️ 準備重設 ChromaDB 向量資料庫...")
    print(f"📁 資料庫路徑：{CHROMA_DB_PATH}")
    
    if os.path.exists(CHROMA_DB_PATH):
        try:
            shutil.rmtree(CHROMA_DB_PATH)
            print("✅ 舊的向量資料庫已成功刪除！")
        except Exception as e:
            print(f"❌ 刪除資料庫時發生錯誤：{e}")
            print("💡 如果遇到權限問題，請確認沒有其他程式（例如 app.py 或 chat_web.py）正在使用該資料夾。")
    else:
        print("ℹ️ 找不到存在的資料庫，可能已經清空。")
        
    print("\n🚀 下一步請執行：")
    print("python3 build_vector_db.py")
    print("或在 Web 介面點擊「重新索引」以重建新的向量資料庫。")

if __name__ == "__main__":
    reset_chroma_db()
