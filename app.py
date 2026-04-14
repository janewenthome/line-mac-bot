"""
LINE Mac Bot - 透過 LINE 訊息遠端操控 Mac Mini
使用 Gemini API 理解自然語言指令並執行 Mac 操作
新增：AI 自動分類筆記 + APScheduler 週五煉金
"""
import os
import sys
import subprocess
import time
import random
from datetime import datetime, timedelta
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, AudioMessageContent
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import chromadb
from chromadb.config import Settings
from google import genai
from google.genai import types
import mlx_whisper
import gc
import mlx.core as mx
from concurrent.futures import ThreadPoolExecutor

load_dotenv()

app = Flask(__name__)

# ── 非同步處理執行緒池 (避免 STT 阻塞 Webhook) ───────────────────────────────
executor = ThreadPoolExecutor(max_workers=3)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
AUTHORIZED_USER_ID = os.environ.get("LINE_AUTHORIZED_USER_ID", "")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
CWA_API_KEY = os.getenv("CWA_API_KEY", "")
MOENV_API_KEY = os.getenv("MOENV_API_KEY", "")  # 選做：環保署 AQI 金鑰

client = genai.Client(api_key=GEMINI_API_KEY)

handler = WebhookHandler(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)

# ── 路徑設定（啟動時自動建立資料夾）────────────────────────────────────────
BASE_DIR = os.path.expanduser("~/line-mac-bot")
INBOX_PATH = os.path.join(BASE_DIR, "inbox.txt")
OBSIDIAN_INBOX_DIR = os.path.join(BASE_DIR, "Obsidian_Inbox")
os.makedirs(OBSIDIAN_INBOX_DIR, exist_ok=True)

# ── Obsidian Vault 路徑（請將 YourVaultName 改成你實際的 Vault 名稱）────────
OBSIDIAN_VAULT_PATH = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"

# ── 短期對話記憶（儲存最近 10 筆對話，供連續探討與「整理到筆記」使用）────────
CHAT_HISTORY = []

# ── 核心標籤（第一階段：每日碎語歸納）──────────────────────────────────────
CORE_TAGS = [
    "[[家醫科臨床]]", "[[皮膚病]]", "[[巡迴醫療紀錄]]", "[[糖尿病照護]]",
    "[[公共衛生]]", "[[文獻閱讀心得]]", "[[三寶爸日常]]", "[[人生哲學]]",
    "[[旅行]]", "[[亞斯伯格]]", "[[AI與自動化]]", "[[專案開發紀錄]]",
    "[[文史哲簡單說]]", "[[音樂創作]]", "[[情緒]]", "[[鐵道紀行]]",
    "[[樂高與機械原理]]", "[[芳療與自然療癒]]", "[[科技與攝影]]",
    "[[活動與社區經營]]", "[[論文構想]]",
]

# ── Mac 控制工具定義 (Gemini protos 格式) ───────────────────────────────────
MAC_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="run_shell",
                description="在 Mac Mini 上執行 shell 命令。適用於檔案管理、系統操作、安裝軟體等。",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "command": types.Schema(
                            type=types.Type.STRING,
                            description="要執行的 shell 命令"
                        )
                    },
                    required=["command"]
                )
            ),
            types.FunctionDeclaration(
                name="open_app",
                description="在 Mac 上開啟應用程式",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "app_name": types.Schema(
                            type=types.Type.STRING,
                            description="應用程式名稱，例如：Safari、Music、Finder、Terminal、VS Code"
                        )
                    },
                    required=["app_name"]
                )
            ),
            types.FunctionDeclaration(
                name="run_applescript",
                description="執行 AppleScript 進行 GUI 自動化、控制 Mac 系統設定，例如調整音量、播放音樂、顯示通知",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "script": types.Schema(
                            type=types.Type.STRING,
                            description="要執行的 AppleScript 程式碼"
                        )
                    },
                    required=["script"]
                )
            ),
            types.FunctionDeclaration(
                name="get_system_info",
                description="取得 Mac 系統資訊，包含 CPU 使用率、記憶體、磁碟空間、執行中的程序",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "info_type": types.Schema(
                            type=types.Type.STRING,
                            enum=["cpu", "memory", "disk", "processes", "network", "all"],
                            description="要查詢的資訊類型：cpu/memory/disk/processes/network/all"
                        )
                    },
                    required=["info_type"]
                )
            ),
            types.FunctionDeclaration(
                name="take_screenshot",
                description="截取目前 Mac 螢幕畫面，並將圖片儲存到指定路徑",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "save_path": types.Schema(
                            type=types.Type.STRING,
                            description="截圖儲存路徑（預設 /tmp/screenshot.png）"
                        )
                    }
                )
            ),
        ]
    )
]

# ── System Prompt（加入筆記分類指令）────────────────────────────────────────
SYSTEM_PROMPT = (
    "你是一個智慧助手，負責遠端控制主人的 Mac Mini 電腦。\n"
    "你可以執行 shell 命令、開啟應用程式、執行 AppleScript 自動化操作，以及查詢系統狀態。\n"
    "請一律使用繁體中文回覆，簡潔說明你執行了什麼操作以及結果。\n"
    "如果指令不清楚，請主動詢問確認。\n"
    "執行可能影響系統穩定的危險操作前，請先說明並等待確認。\n\n"
    "【重要】請先判斷使用者輸入的類型，再決定如何回應：\n"
    "- 如果是要求操作 Mac 的明確指令（例如開啟應用程式、執行命令、調整設定、查詢系統等），"
    "請正常呼叫工具執行。\n"
    "- 如果只是日常想法、靈感、筆記、心情或抱怨（非 Mac 操作指令），"
    "請不要呼叫任何工具，『只』回傳這十個字元：[SAVE_NOTE]\n\n"
    "【環境與預設軟體設定】以下指令必須嚴格對應到指定的執行方式，"
    "禁止嘗試開啟同名的實體 App（例如 Music.app、Photos.app）：\n"
    "1. 使用者說「聽音樂」、「YouTube Music」或「YT Music」→ "
    "呼叫 run_shell，執行：open -a Safari https://music.youtube.com\n"
    "2. 使用者說「找照片」、「看相簿」或「Google Photos」→ "
    "呼叫 run_shell，執行：open -a Safari https://photos.google.com\n"
    "3. 使用者說「行事曆」或「查行程」→ "
    "呼叫 run_shell，執行：open -a Safari https://calendar.google.com\n"
    "4. 使用者說「剪影片」或「影片編輯」→ "
    "呼叫 open_app，app_name 為：iMovie"
)


# ── 安全呼叫 Gemini（自動重試，最多 3 次）──────────────────────────────────

def safe_generate(prompt: str, model_name="gemini-2.5-flash", **kwargs) -> str:
    """呼叫 client.models.generate_content，遇到例外（含 429 Quota）自動等待 20 秒後重試，最多 3 次"""
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                **kwargs
            )
            return resp.text
        except Exception as e:
            print(f"[safe_generate] 第 {attempt + 1} 次失敗：{e}")
            if attempt < 2:
                time.sleep(20)
    return "老闆，目前 API 額度塞車中，我正在深呼吸，請您一分鐘後再問我一次！"


# ── 筆記工具函數 ─────────────────────────────────────────────────────────────

def search_obsidian(query: str) -> str:
    """向量語意搜尋：使用 ChromaDB 找最相關的 3~5 個 Chunk，並優先附上 Persona.md"""
    if not os.path.isdir(OBSIDIAN_VAULT_PATH):
        print(f"[Obsidian] 找不到 Vault 路徑：{OBSIDIAN_VAULT_PATH}")
        return ""

    parts = []

    # ── 1. 優先讀取 Persona.md 完整內容 ──────────────────────────────────────
    persona_content = ""
    persona_path = os.path.join(OBSIDIAN_VAULT_PATH, "Persona.md")
    if not os.path.isfile(persona_path):
        for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
            if "Persona.md" in files:
                persona_path = os.path.join(root, "Persona.md")
                break
    try:
        with open(persona_path, "r", encoding="utf-8") as f:
            persona_content = f.read()
        print(f"[Obsidian] 已載入 Persona.md：{persona_path}")
        parts.append(f"### 📄 Persona.md（核心個人檔案）\n{persona_content}")
    except FileNotFoundError:
        print(f"[Obsidian] 找不到 Persona.md，略過")
    except Exception as e:
        print(f"[Obsidian] 讀取 Persona.md 失敗：{e}")

    # ── 2. 向量語意搜尋 ChromaDB ──────────────────────────────────────────────
    chroma_db_path = os.path.join(BASE_DIR, "chroma_db")
    if os.path.isdir(chroma_db_path):
        try:
            client = chromadb.PersistentClient(path=chroma_db_path)
            collection = client.get_or_create_collection("obsidian_notes")
            # 使用 Gemini text-embedding-004 對查詢文字做向量化
            embed_resp = client.models.embed_content(
                model="text-embedding-004",
                contents=query,
                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
            )
            query_embedding = embed_resp.embeddings[0].values
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=5,
                include=["documents", "metadatas"]
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            for doc, meta in zip(docs, metas):
                src = meta.get("source", "未知來源")
                mtime_str = meta.get("mtime", "")
                header = f"### 📄 {src}" + (f"（最後修改：{mtime_str}）" if mtime_str else "")
                parts.append(f"{header}\n{doc}")
            print(f"[Obsidian] ChromaDB 語意搜尋找到 {len(docs)} 個相關 Chunk")
        except Exception as e:
            print(f"[Obsidian] ChromaDB 搜尋失敗，改用傳統掃描：{e}")
            # fallback：取最近修改的 3 個檔案
            md_files = []
            for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
                for fname in files:
                    if fname.endswith(".md") and fname != "Persona.md":
                        fpath = os.path.join(root, fname)
                        try:
                            mtime = os.path.getmtime(fpath)
                            md_files.append((mtime, fpath))
                        except Exception:
                            pass
            md_files.sort(reverse=True)
            for mtime, fpath in md_files[:3]:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read(1500)
                    rel_path = os.path.relpath(fpath, OBSIDIAN_VAULT_PATH)
                    mod_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                    parts.append(f"### 📄 {rel_path}（最後修改：{mod_time}）\n{content}")
                except Exception:
                    pass
    else:
        print(f"[Obsidian] chroma_db 尚未建立，改用傳統掃描（請先執行 build_vector_db.py）")
        md_files = []
        for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
            for fname in files:
                if fname.endswith(".md") and fname != "Persona.md":
                    fpath = os.path.join(root, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        md_files.append((mtime, fpath))
                    except Exception:
                        pass
        md_files.sort(reverse=True)
        for mtime, fpath in md_files[:3]:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read(1500)
                rel_path = os.path.relpath(fpath, OBSIDIAN_VAULT_PATH)
                mod_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                parts.append(f"### 📄 {rel_path}（最後修改：{mod_time}）\n{content}")
            except Exception:
                pass

    print(f"[Obsidian] 已載入 {len(parts)} 個筆記片段作為上下文")
    return "\n\n---\n\n".join(parts)


def answer_with_obsidian(user_message: str, notes_context: str) -> str:
    """將 Obsidian 筆記作為上下文，讓 Gemini 結合筆記內容與近期對話回覆使用者。"""
    global CHAT_HISTORY

    # ── 優先讀取 Persona.md 作為核心背景 ────────────────────────────────────
    persona_content = ""
    persona_path = os.path.join(OBSIDIAN_VAULT_PATH, "Persona.md")

    # 若 Vault 根目錄找不到，往子目錄搜尋一次
    if not os.path.isfile(persona_path):
        for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
            if "Persona.md" in files:
                persona_path = os.path.join(root, "Persona.md")
                break

    try:
        with open(persona_path, "r", encoding="utf-8") as f:
            persona_content = f.read()
        print(f"[Obsidian] 已載入 Persona.md：{persona_path}")
    except FileNotFoundError:
        print(f"[Obsidian] 找不到 Persona.md（路徑：{persona_path}），略過")
    except (OSError, PermissionError) as e:
        print(f"[Obsidian] 無法讀取 Persona.md：{e}")
    except Exception as e:
        print(f"[Obsidian] 讀取 Persona.md 失敗：{e}")

    # ── 準備對話歷史 ───────────────────────────────────────────────────────────
    history_text = ""
    if CHAT_HISTORY:
        history_text = "【近期對話紀錄】\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in CHAT_HISTORY]) + "\n\n"


    # ── 組合 prompt ───────────────────────────────────────────────────────────
    try:
        persona_section = (
            f"【核心個人檔案 Persona.md】\n{persona_content}\n\n"
            if persona_content else ""
        )
        notes_section = (
            f"【Obsidian 最近筆記內容】\n{notes_context}\n\n"
            if notes_context else ""
        )

        prompt = (
            "你現在是搭載『深度語境共感引擎』的數位小助手，是我的專屬思維辯證夥伴。\n"
            "請仔細閱讀我的問題與參考的筆記上下文。\n\n"
            "【核心回應原則】：\n"
            "1. 拒絕死板格式：請用自然流暢的一段（或兩段）話來回覆，絕對不要出現任何固定的標題區塊或條列符號（例如禁止出現「👁️ 脈絡共感」、「🌌 跨界洞見」、「建議行動」等標籤文字）。\n"
            "2. 優先直擊問題：如果我詢問具體內容（例如「我有提到什麼？」），請直接根據筆記給出明確的答案，不要繞圈子。\n"
            "3. 智慧化於無形：在回答的過程中，自然地融入你的同理心與『文史哲/系統架構』的洞見。就像一個充滿智慧的老朋友跟我聊天，把深邃的觀點變成日常對話。\n"
            "4. 保持精煉：總字數控制在 150~250 字左右，適合 LINE 閱讀，溫暖且一針見血。\n\n"
            f"{persona_section}"
            f"{notes_section}"
            f"{history_text}"
            f"【使用者當前問題】\n{user_message}"
        )
        ai_reply = safe_generate(prompt).strip()

        # 將本次對話加入歷史紀錄 (User 與 小助手)
        CHAT_HISTORY.append({"role": "User", "content": user_message})
        CHAT_HISTORY.append({"role": "小助手", "content": ai_reply})
        # 保持最多 10 筆 (5組問答)
        CHAT_HISTORY = CHAT_HISTORY[-10:]

        return f"{ai_reply}\n\n(如果覺得好，請回覆「整理到筆記」)"
    except Exception as e:
        print(f"[Obsidian] answer_with_obsidian 失敗：{e}")
        return "抱歉，讀取筆記時發生錯誤，無法回答您的問題。"


def expand_and_save_insight(query: str, notes_context: str) -> str:
    """將剛剛的對話脈絡，展開成完整的深度文章並存入 Obsidian 每日碎語資料夾"""
    global CHAT_HISTORY
    try:
        # 嘗試讀取 Persona
        persona_content = ""
        persona_path = os.path.join(OBSIDIAN_VAULT_PATH, "Persona.md")
        if os.path.isfile(persona_path):
            with open(persona_path, "r", encoding="utf-8") as f:
                persona_content = f.read()

        history_text = "【無近期對話紀錄】"
        if CHAT_HISTORY:
            history_text = "【近期對話紀錄】\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in CHAT_HISTORY])

        prompt = (
            "你現在是數位小助手。請將我們先前的對話脈絡（包含我的問題和你的簡短回覆），整理成一篇完整的 Obsidian 筆記。\n"
            "請根據我的個人檔案與相關筆記，將這個主題「完整、深度地展開」。\n\n"
            "文章請包含以下結構：\n"
            "1. 核心假說與跨界聯想的深度推演。\n"
            "2. 具體的行動清單與落地建議。\n"
            "3. 【重要】在文章最底部，請附上多維度標籤，格式如下：\n"
            "   關聯節點：[[標籤A]], [[標籤B]]\n"
            "   意圖與時態：#意圖標籤, #時態標籤\n\n"
            f"【個人檔案】\n{persona_content}\n\n"
            f"【參考筆記內容】\n{notes_context}\n\n"
            f"{history_text}"
        )
        expanded_content = safe_generate(prompt)

        # 存入 Obsidian 📥 LINE每日碎語/YYYY/MM/
        now = datetime.now()
        year_str  = now.strftime("%Y")
        month_str = now.strftime("%m")
        timestamp = now.strftime("%Y%m%d_%H%M%S")

        target_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📥 LINE每日碎語", year_str, month_str)
        os.makedirs(target_dir, exist_ok=True)

        # 若有近期對話，取第一句作為檔名的一部分
        title_hint = "新靈感"
        if CHAT_HISTORY:
            first_user_msg = next((msg['content'] for msg in CHAT_HISTORY if msg['role'] == 'User'), "新靈感")
            title_hint = first_user_msg[:15].replace("\n", "").replace("/", "-")

        output_path = os.path.join(target_dir, f"{timestamp}_{title_hint}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# 靈感展開：{title_hint}\n\n")
            f.write(expanded_content)

        return "✅ 已為您將靈感深度展開並整理至 Obsidian 筆記中！"
    except Exception as e:
        print(f"[展開筆記] 失敗：{e}")
        return "老闆，整理筆記時發生錯誤，請稍後再試。"


def save_note(text: str):
    """將使用者訊息加上時間戳記，附加寫入 inbox.txt"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] {text}\n"
    with open(INBOX_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"[筆記] 已收錄：{entry.strip()}")


def daily_line_summary():
    """
    每天 11:00 排程任務：
    讀取 inbox.txt → Gemini 整理（含重試）→ 存入 Obsidian → 清空 inbox → LINE 推播
    【安全防呆】：只有 API 真正成功時才寫檔與清空 inbox；失敗三次則 LINE 通知、保留原檔。
    """
    print("[每日歸納] 開始處理今日碎語...")

    # 1. 讀取 inbox.txt
    if not os.path.exists(INBOX_PATH):
        print("[每日歸納] inbox.txt 不存在，略過")
        return

    with open(INBOX_PATH, "r", encoding="utf-8") as f:
        notes_content = f.read().strip()

    if not notes_content:
        print("[每日歸納] inbox.txt 為空，略過")
        return

    # 2. 重試迴圈：最多 3 次，每次間隔 180 秒
    MAX_RETRIES = 3
    RETRY_DELAY = 180  # 秒
    markdown_content = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"[每日歸納] 呼叫 Gemini（第 {attempt}/{MAX_RETRIES} 次）...")
            
            # 讀取 SKILL_每日碎語歸納規則.md
            skill_path = os.path.join(OBSIDIAN_VAULT_PATH, "系統設定", "SKILL_每日碎語歸納規則.md")
            skill_content = "（未找到 SKILL_每日碎語歸納規則.md，請確認檔案位置）"
            if os.path.isfile(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        skill_content = f.read()
                except Exception as e:
                    print(f"[每日歸納] 讀取規則檔失敗：{e}")
            
            tags_str = "、".join(CORE_TAGS)
            summary_prompt = (
                f"{skill_content}\n\n"
                f"核心標籤清單：{tags_str}\n"
                f"筆記內容：{notes_content}"
            )
            result = safe_generate(summary_prompt)
            # 驗證回傳內容有意義
            if not result or len(result.strip()) < 30:
                raise ValueError("回傳內容過短，可能被阻擋")
            markdown_content = result
            print(f"[每日歸納] Gemini 整理成功（第 {attempt} 次）")
            break  # 成功就退出重試
        except Exception as e:
            print(f"[每日歸納] 第 {attempt} 次失敗：{e}")
            if attempt < MAX_RETRIES:
                print(f"[每日歸納] 等待 {RETRY_DELAY} 秒後重試...")
                time.sleep(RETRY_DELAY)

    # 重試全部失敗：保留 inbox，LINE 推播警示
    if markdown_content is None:
        fail_msg = (
            "老闆，今日碎語整理失敗，API 塞車中。"
            "已為您保留原始檔未刪除，請稍後輸入「測試第一階段」手動執行。"
        )
        print(f"[每日歸納] 重試 {MAX_RETRIES} 次均失敗，保留 inbox.txt 不動")
        if AUTHORIZED_USER_ID:
            try:
                with ApiClient(line_config) as api_client:
                    MessagingApi(api_client).push_message(
                        PushMessageRequest(
                            to=AUTHORIZED_USER_ID,
                            messages=[TextMessage(text=fail_msg)]
                        )
                    )
                print("[每日歸納] LINE 失敗通知已送出")
            except Exception as pe:
                print(f"[每日歸納] LINE 推播失敗：{pe}")
        return  # 不執行寫檔或清空

    # 3. 建立 Obsidian 存放資料夾 YYYY/MM/ 並儲存檔案
    now = datetime.now()
    year_str  = now.strftime("%Y")
    month_str = now.strftime("%m")
    date_str  = now.strftime("%Y-%m-%d")

    target_dir = os.path.join(
        OBSIDIAN_VAULT_PATH, "📥 LINE每日碎語", year_str, month_str
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        print(f"[每日歸納] 建立資料夾失敗：{e}")
        return

    output_path = os.path.join(target_dir, f"{date_str}_日常歸納.md")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        print(f"[每日歸納] 已儲存：{output_path}")
    except Exception as e:
        print(f"[每日歸納] 儲存失敗：{e}（inbox.txt 保留不動）")
        return  # 寫檔失敗，同樣不清空

    # 4. 寫檔成功後才清空 inbox.txt（安全限制）
    with open(INBOX_PATH, "w", encoding="utf-8") as f:
        f.write("")
    print("[每日歸納] inbox.txt 已清空")

    print("[每日歸納] 完成，Obsidian 已儲存，略過 LINE 推播（節流優化）")



# ── 晨報工具函數 ─────────────────────────────────────────────────────────────

def fetch_weather() -> dict:
    """抓取氣象署 F-C0032-001 天氣預報（雲林縣 / 嘉義縣）"""
    if not CWA_API_KEY:
        print("[晨報] 未設定 CWA_API_KEY，略過天氣抓取")
        return {}

    base_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
    # F-C0032-001 為縣市級預報；以縣名查詢涵蓋轄下各鄉鎮
    targets = {
        "古坑鄉（雲林縣）": "雲林縣",
        "梅山鄉（嘉義縣）": "嘉義縣",
    }
    results = {}
    for label, county in targets.items():
        try:
            resp = requests.get(
                base_url,
                params={"Authorization": CWA_API_KEY, "locationName": county},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if str(data.get("success")).lower() != "true":
                results[label] = {"error": "API 回應 success≠true"}
                continue

            loc = data["records"]["location"][0]
            elements = {e["elementName"]: e["time"] for e in loc["weatherElement"]}

            # 取第一個預報時段（當日白天）
            # F-C0032-001 中 Wx 用 parameterName；MaxT/MinT/PoP 也用 parameterName
            wx    = elements["Wx"][0]["parameter"].get("parameterName", "N/A")
            max_t = elements["MaxT"][0]["parameter"].get("parameterName", "N/A")
            min_t = elements["MinT"][0]["parameter"].get("parameterName", "N/A")
            pop   = elements["PoP"][0]["parameter"].get("parameterName", "N/A")
            results[label] = {
                "天氣":     wx,
                "最高溫":   f"{max_t}°C",
                "最低溫":   f"{min_t}°C",
                "降雨機率": f"{pop}%",
            }
        except Exception as e:
            print(f"[晨報] 天氣抓取失敗 {label}：{e}")
            results[label] = {"error": str(e)}
    return results


def fetch_aqi() -> dict:
    """（選做）抓取環境部環保署 AQI 資料"""
    if not MOENV_API_KEY:
        return {}

    aqi_results = {}
    stations = {"古坑": "雲林縣", "梅山": "嘉義縣"}
    for station, county in stations.items():
        try:
            resp = requests.get(
                "https://data.moenv.gov.tw/api/v2/aqx_p_432",
                params={
                    "api_key": MOENV_API_KEY,
                    "format":  "JSON",
                    "filters": f"SiteName,EQ,{station}",
                    "limit":   1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            records = resp.json().get("records", [])
            if records:
                rec = records[0]
                aqi_results[f"{station}（{county}）"] = {
                    "AQI":    rec.get("AQI", "N/A"),
                    "PM2.0":  rec.get("PM2.0", "N/A"),
                    "狀態":   rec.get("Status", "N/A"),
                }
        except Exception as e:
            print(f"[晨報] AQI 抓取失敗 {station}：{e}")
    return aqi_results


def morning_briefing():
    """每天 20:00：抓氣象 + AQI → Gemini 撰寫明日預報 → LINE 推播"""
    print("[晨報] 開始執行晚安晨報（明日預報）...")

    if not AUTHORIZED_USER_ID:
        print("[晨報] 未設定 LINE_AUTHORIZED_USER_ID，略過")
        return

    # 1. 抓天氣
    weather = fetch_weather()

    # 2. 抓 AQI（選做，無金鑰時回傳空 dict）
    aqi = fetch_aqi()

    # 3. 組合資料文字
    data_lines = ["【天氣資料】"]
    for loc, info in weather.items():
        data_lines.append(f"\n{loc}：")
        if "error" in info:
            data_lines.append(f"  資料取得失敗：{info['error']}")
        else:
            for k, v in info.items():
                data_lines.append(f"  {k}：{v}")

    if aqi:
        data_lines.append("\n【空氣品質 AQI】")
        for loc, info in aqi.items():
            data_lines.append(f"\n{loc}：")
            for k, v in info.items():
                data_lines.append(f"  {k}：{v}")

    data_text = "\n".join(data_lines)

    # 4. Gemini 生成晚安預報（明日準備）
    try:
        prompt = (
            "你是一個細心的私人管家。以下是『明天』的天氣數據，"
            "請用『明天古坑／梅山的天氣是…』的語氣，寫一段 100 字以內的晚安預報給老闆，"
            "並提醒老闆明天出門的準備建議（例如帶傘、防曬、加件外套等）。\n\n"
            f"{data_text}"
        )
        briefing_text = safe_generate(prompt)
    except Exception as e:
        print(f"[晨報] Gemini 生成失敗：{e}")
        briefing_text = f"老闆，為您準備好明天的情報了！\n\n{data_text}"

    # 5. LINE 推播
    try:
        with ApiClient(line_config) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=AUTHORIZED_USER_ID,
                    messages=[TextMessage(text=briefing_text)]
                )
            )
        print(f"[晨報] LINE 推播已送出：{briefing_text[:60]}...")
    except Exception as e:
        print(f"[晨報] LINE 推播失敗：{e}")


# ── 第二階段：每週總結 ───────────────────────────────────────────────────────

def weekly_summary_job():
    """每週五 13:00：掃描最近 7 天的 LINE每日碎語 .md → Gemini 寫週報 → 存入 Obsidian → LINE 推播"""
    print("[週報] 開始執行每週總結...")
    try:
        inbox_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📥 LINE每日碎語")
        if not os.path.isdir(inbox_dir):
            print(f"[週報] 找不到資料夾：{inbox_dir}")
            return

        # 收集最近 7 天的 .md 檔內容
        cutoff = datetime.now() - timedelta(days=7)
        collected_parts = []
        for root, _, files in os.walk(inbox_dir):
            for fname in sorted(files):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    if datetime.fromtimestamp(mtime) < cutoff:
                        continue
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content:
                        collected_parts.append(f"### {fname}\n{content}")
                except Exception as e:
                    print(f"[週報] 讀取 {fname} 失敗：{e}")

        if not collected_parts:
            print("[週報] 近 7 天無任何筆記，略過")
            return

        notes_text = "\n\n---\n\n".join(collected_parts)

        # 呼叫 Gemini 生成週報
        skill_path = os.path.join(OBSIDIAN_VAULT_PATH, "系統設定", "SKILL_每週總結規則.md")
        skill_content = "（未找到 SKILL_每週總結規則.md，請確認檔案位置）"
        if os.path.isfile(skill_path):
            try:
                with open(skill_path, "r", encoding="utf-8") as f:
                    skill_content = f.read()
            except Exception as e:
                print(f"[週報] 讀取規則檔失敗：{e}")
                
        prompt = (
            f"{skill_content}\n\n"
            f"{notes_text}"
        )
        report_content = safe_generate(prompt)

        # 存入 Obsidian/📊 每週總結/YYYY/MM/YYYY-Wxx_週報總結.md
        now = datetime.now()
        year_str = now.strftime("%Y")
        month_str = now.strftime("%m")
        week_str = now.strftime("%Y-W%W")
        summary_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📊 每週總結", year_str, month_str)
        os.makedirs(summary_dir, exist_ok=True)
        output_path = os.path.join(summary_dir, f"{week_str}_週報總結.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        print(f"[週報] 已儲存：{output_path}")

        print("[週報] 完成，Obsidian 已儲存，略過 LINE 推播（節流優化）")

    except Exception as e:
        print(f"[週報] 執行失敗：{e}")


# ── 第二階段：早晨冥想 ───────────────────────────────────────────────────────

def morning_meditation_job():
    """週一至週五 08:30：歷史迴音 + 跨界冥想 (精簡版) → 存 Obsidian + LINE 推播"""
    print("[冥想] 開始執行早晨冥想...")
    try:
        # ── 1. 抓取近期狀態與歷史迴音 ──
        inbox_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📥 LINE每日碎語")
        recent_note = "（暫無昨日紀錄）"
        historical_echo = "（暫無歷史紀錄）"

        if os.path.isdir(inbox_dir):
            all_inbox_files = []
            for root, _, files in os.walk(inbox_dir):
                for f in files:
                    if f.endswith(".md"):
                        all_inbox_files.append(os.path.join(root, f))
            all_inbox_files.sort(reverse=True)

            # 取最新的一篇作為昨日碎語
            if len(all_inbox_files) > 0:
                try:
                    with open(all_inbox_files[0], "r", encoding="utf-8") as f:
                        recent_note = f.read()[:1000]
                except: pass

            # 隨機盲抽一篇過去的碎語作為歷史迴音
            if len(all_inbox_files) > 1:
                past_file = random.choice(all_inbox_files[1:])
                try:
                    with open(past_file, "r", encoding="utf-8") as f:
                        historical_echo = f"【來自過去的紀錄：{os.path.basename(past_file)}】\n" + f.read()[:1000]
                except: pass

        # ── 2. 抓取最新週報 ──
        weekly_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📊 每週總結")
        latest_weekly = "（暫無週報）"
        if os.path.isdir(weekly_dir):
            w_files = [os.path.join(weekly_dir, f) for f in os.listdir(weekly_dir) if f.endswith(".md")]
            if w_files:
                w_files.sort(reverse=True)
                try:
                    with open(w_files[0], "r", encoding="utf-8") as f:
                        latest_weekly = f.read()[:1000]
                except: pass

        # ── 3. 抽取跨界標籤筆記 (維持強制文史哲濾鏡) ──
        PHILOSOPHY_TAGS = ["[[文史哲簡單說]]", "[[人生哲學]]"]
        tag_a = random.choice(PHILOSOPHY_TAGS)
        remaining_tags = [t for t in CORE_TAGS if t not in PHILOSOPHY_TAGS]
        tag_b = random.choice(remaining_tags)
        print(f"[冥想] 標籤A：{tag_a}　標籤B：{tag_b}")

        def find_notes_with_tag(tag: str, max_count: int = 2) -> list[str]:
            matches = []
            for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
                for fname in files:
                    if not fname.endswith(".md"): continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            content = f.read()
                        if tag in content:
                            matches.append(content[:800])
                    except: pass
            random.shuffle(matches)
            return matches[:max_count]

        notes_a = find_notes_with_tag(tag_a)
        notes_b = find_notes_with_tag(tag_b)

        def format_notes(tag: str, notes: list[str]) -> str:
            if not notes: return f"（{tag} 暫無相關筆記）"
            return f"### 來自 {tag}\n" + "\n\n".join([f"#### 筆記 {i+1}\n{n}" for i, n in enumerate(notes)])

        notes_section = format_notes(tag_a, notes_a) + "\n\n" + format_notes(tag_b, notes_b)

        # ── 4. 讀取 Persona.md ──
        persona_content = ""
        persona_path = os.path.join(OBSIDIAN_VAULT_PATH, "Persona.md")
        if not os.path.isfile(persona_path):
            for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
                if "Persona.md" in files:
                    persona_path = os.path.join(root, "Persona.md")
                    break
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                persona_content = f.read()
            print(f"[冥想] 已載入 Persona.md")
        except Exception as e:
            print(f"[冥想] 讀取 Persona.md 失敗：{e}")

        # ── 5. 讀取 文章存檔 資料夾（近 3 天修改的 .md 檔，各擷取 800 字元）──
        article_archive_section = "（近期無文章存檔）"
        article_archive_dir = os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔")
        if os.path.isdir(article_archive_dir):
            cutoff_3days = datetime.now() - timedelta(days=3)
            recent_articles = []
            for root, _, files in os.walk(article_archive_dir):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                        if datetime.fromtimestamp(mtime) >= cutoff_3days:
                            with open(fpath, "r", encoding="utf-8") as f:
                                content = f.read(800)
                            mod_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                            recent_articles.append(f"#### {fname}（修改：{mod_time}）\n{content}")
                    except Exception as e:
                        print(f"[冥想] 讀取文章存檔 {fname} 失敗：{e}")
            if recent_articles:
                article_archive_section = "\n\n".join(recent_articles)
                print(f"[冥想] 找到 {len(recent_articles)} 篇近期文章存檔")
            else:
                print("[冥想] 文章存檔資料夾無近 3 天修改的檔案")
        else:
            print(f"[冥想] 找不到文章存檔資料夾：{article_archive_dir}")

        # ── 6. 讀取 SKILL_早晨冥想規則.md 並組合 Prompt ──
        skill_path = os.path.join(OBSIDIAN_VAULT_PATH, "系統設定", "SKILL_早晨冥想規則.md")
        skill_content = "（未找到 SKILL_早晨冥想規則.md，請確認檔案位置）"
        if os.path.isfile(skill_path):
            try:
                with open(skill_path, "r", encoding="utf-8") as f:
                    skill_content = f.read()
            except Exception as e:
                print(f"[冥想] 讀取 SKILL_早晨冥想規則.md 失敗：{e}")
        else:
            print(f"[冥想] 找不到 {skill_path}")

        prompt = (
            f"{skill_content}\n\n"
            "=== 以下為輸入素材 ===\n"
            f"[個人 Persona]:\n{persona_content}\n\n"
            f"[近期狀態-昨日碎語]:\n{recent_note}\n\n"
            f"[近期狀態-本週週報]:\n{latest_weekly}\n\n"
            f"[歷史的迴音]:\n{historical_echo}\n\n"
            f"[近期文章存檔]:\n{article_archive_section}\n\n"
            f"[跨界筆記]:\n{notes_section}"
        )
        raw_result = safe_generate(prompt)

        # 解析四個部分
        part_obsidian = ""
        part_line = ""
        if "【第一部分" in raw_result and "【第四部分" in raw_result:
            idx_start = raw_result.index("【第一部分")
            idx4_start = raw_result.index("【第四部分")
            part_obsidian = raw_result[idx_start:idx4_start].strip()
            # 移除標題字眼以利閱讀
            part_line = raw_result[idx4_start:].replace("【第四部分：LINE 晨間推播】", "").strip()
        else:
            part_obsidian = raw_result
            part_line = f"早安！今天的冥想結合了 {tag_a} 與 {tag_b}，請到 Obsidian 查看今日報告。"

        # 存入 Obsidian
        now = datetime.now()
        year_str = now.strftime("%Y")
        month_str = now.strftime("%m")
        date_str = now.strftime("%Y-%m-%d")
        meditation_dir = os.path.join(OBSIDIAN_VAULT_PATH, "早晨冥想", year_str, month_str)
        os.makedirs(meditation_dir, exist_ok=True)
        output_path = os.path.join(meditation_dir, f"{date_str}_早晨冥想.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(part_obsidian)
            f.write(f"\n\n關聯節點：{tag_a}, {tag_b}\n意圖與時態：#系統除錯, #情緒覺察, #已發生")
        print(f"[冥想] 已儲存：{output_path}")

        print("[冥想] 完成，Obsidian 已儲存，略過 LINE 推播（節流優化）")

    except Exception as e:
        print(f"[冥想] 執行失敗：{e}")


# ── Mac 工具執行器 ───────────────────────────────────────────────────────────

def execute_tool(name: str, tool_input: dict) -> str:
    """執行 Mac 控制工具並回傳結果"""

    if name == "run_shell":
        command = tool_input["command"]
        try:
            result = subprocess.run(
                command, shell=True,
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip() or result.stderr.strip()
            return output[:3000] if output else "命令執行完成（無輸出）"
        except subprocess.TimeoutExpired:
            return "命令執行逾時（30 秒上限）"
        except Exception as e:
            return f"執行錯誤：{e}"

    elif name == "open_app":
        app_name = tool_input["app_name"]
        result = subprocess.run(
            ["open", "-a", app_name], capture_output=True, text=True
        )
        if result.returncode == 0:
            return f"已成功開啟 {app_name}"
        return f"無法開啟 {app_name}：{result.stderr.strip()}"

    elif name == "run_applescript":
        script = tool_input["script"]
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip() or result.stderr.strip()
            return output or "AppleScript 執行完成"
        except subprocess.TimeoutExpired:
            return "AppleScript 執行逾時"
        except Exception as e:
            return f"AppleScript 錯誤：{e}"

    elif name == "get_system_info":
        info_type = tool_input["info_type"]
        commands = {
            "cpu": "top -l 1 -s 0 | grep 'CPU usage'",
            "memory": "top -l 1 -s 0 | grep PhysMem",
            "disk": "df -h / | awk 'NR==2'",
            "processes": "ps aux -r | head -11 | awk '{print $1,$2,$3,$4,$11}'",
            "network": "ifconfig | grep 'inet ' | grep -v 127.0.0.1",
            "all": (
                "echo '【CPU】'; top -l 1 -s 0 | grep 'CPU usage';"
                "echo '【記憶體】'; top -l 1 -s 0 | grep PhysMem;"
                "echo '【磁碟】'; df -h / | awk 'NR==2';"
                "echo '【網路】'; ifconfig | grep 'inet ' | grep -v 127.0.0.1"
            )
        }
        cmd = commands.get(info_type, commands["all"])
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()[:2000] or "無法取得系統資訊"

    elif name == "take_screenshot":
        path = tool_input.get("save_path", "/tmp/screenshot.png")
        result = subprocess.run(
            ["screencapture", "-x", path], capture_output=True
        )
        if result.returncode == 0:
            return f"截圖已儲存至：{path}"
        return "截圖失敗"

    return f"未知工具：{name}"


# ── Gemini 核心處理 ──────────────────────────────────────────────────────────

def process_with_gemini(user_message: str) -> str:
    """透過 Gemini 處理使用者訊息，執行 Mac 操作，回傳繁體中文結果"""

    chat = client.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            tools=MAC_TOOLS,
            system_instruction=SYSTEM_PROMPT,
        )
    )

    response = chat.send_message(user_message)

    # 工具循環：持續執行直到 Gemini 完成任務
    for _ in range(10):  # 最多 10 輪工具呼叫
        # 新 SDK format, response.function_calls 取得
        if not response.function_calls:
            # 無工具呼叫，回傳最終文字（可能是 [SAVE_NOTE] 或正常回覆）
            return response.text or "任務完成"

        # 執行所有工具並收集結果
        fn_responses = []
        for fc in response.function_calls:
            result = execute_tool(fc.name, fc.args)
            fn_responses.append(
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": result}
                )
            )

        response = chat.send_message(fn_responses)

    return "任務處理完成"


# ── LINE 回覆工具 ────────────────────────────────────────────────────────────

def send_line_reply(reply_token: str, text: str):
    """回覆 LINE 訊息（自動處理超過 5000 字元的情況）"""
    if not text:
        text = "老闆，目前 API 額度塞車中，我正在深呼吸，請您一分鐘後再問我一次！"
    # LINE 單則訊息上限 5000 字元，超過則分段發送（最多 5 則）
    chunks = [text[i:i + 4900] for i in range(0, min(len(text), 24500), 4900)]
    messages_to_send = [TextMessage(text=chunk) for chunk in chunks[:5]]

    with ApiClient(line_config) as api_client:
        MessagingApi(api_client).reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=messages_to_send
            )
        )


# ── 背景 Threads 發布（供「脆發文」指令呼叫）────────────────────────────────

def _publish_threads_background():
    """背景執行 Threads 發布，結果記錄在 terminal log 和 Obsidian，不佔用 LINE 推播額度。"""
    try:
        from daily_news_digest import publish_threads_from_obsidian
        result = publish_threads_from_obsidian()
        print(f"[脆發文] 背景發布結果：{result}")
    except Exception as e:
        print(f"[錯誤][脆發文] 背景發布失敗：{e}")


def _generate_news_background():
    """背景執行新聞摘要產生（不推播，純 Obsidian 存檔）。"""
    try:
        from daily_news_digest import generate_digest
        generate_digest()
        print("[新聞摘要] 背景產生完成")
    except Exception as e:
        print(f"[錯誤][新聞摘要] 背景產生失敗：{e}")


def _generate_meditation_background():
    """背景執行早晨冥想（不推播，純 Obsidian 存檔）。"""
    try:
        morning_meditation_job()
        print("[冥想] 背景產生完成")
    except Exception as e:
        print(f"[錯誤][冥想] 背景產生失敗：{e}")


def _generate_pickleball_background(user_id=None):
    """背景執行匹克球與健走醫學文獻監控（純 Obsidian 存檔並回傳狀態）。"""
    import subprocess
    try:
        # 使用 Pickleball 專案專屬的虛擬環境執行
        cmd = ["/Volumes/2TB/program/Pickleball/.venv/bin/python3", "main.py"]
        result = subprocess.run(
            cmd,
            cwd="/Volumes/2TB/program/Pickleball",
            capture_output=True,
            text=True,
            check=True
        )
        print("[匹克球] 醫學文獻監控完成")
        
    except subprocess.CalledProcessError as e:
        print(f"[錯誤][匹克球] 醫學文獻監控失敗：{e.stderr}")
    except Exception as e:
        print(f"[錯誤][匹克球] 啟觸失敗：{e}")


def _run_build_wiki(force=False):
    """背景執行掃描文章產出 Wiki"""
    import subprocess
    try:
        cmd = ["/Users/wenhung/book translate/.venv/bin/python3", "-u", "/Users/wenhung/book translate/build_wiki.py"]
        if force:
            cmd.append("--force")
        with open("/tmp/wiki_scan.log", "w", encoding="utf-8") as f:
            subprocess.run(cmd, cwd="/Users/wenhung/book translate", stdout=f, stderr=subprocess.STDOUT, check=True)
        print("[維基掃描] 背景執行完成")
    except Exception as e:
        print(f"[錯誤][維基掃描] 啟動失敗: {e}")

def _stop_build_wiki():
    """強制停止正在背景執行的掃描爬蟲程式"""
    import subprocess
    try:
        # pkill 會透過給定文字名稱中止程序
        subprocess.run(["pkill", "-f", "build_wiki.py"], check=True)
        print("[維基掃描] 已強制中止程序")
    except subprocess.CalledProcessError:
        print("[維基掃描] 沒有發現正在執行的程序可以中止")
    except Exception as e:
        print(f"[錯誤][維基掃描] 強制中止失敗: {e}")

def _query_wiki_background(user_message):
    """背景讀取整個 Wiki 並請求 Gemini 取得洞見，只存檔不推播"""
    try:
        import os
        from datetime import datetime
        # Load configuration
        config_path = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain/系統設定/SKILL_私人維基建置設定.md"
        query_model = "gemini-2.5-flash"
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f.read().split("\n"):
                    if line.startswith("query_model:"):
                        query_model = line.split(":", 1)[1].strip()
        
        wiki_dir = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain/文章存檔/Wiki"
        megaprompt_parts = []
        if os.path.isdir(wiki_dir):
            for fname in os.listdir(wiki_dir):
                if fname.endswith(".md") and fname != "000_維基總目錄.md":
                    try:
                        with open(os.path.join(wiki_dir, fname), "r", encoding="utf-8") as f:
                            content = f.read()
                        megaprompt_parts.append(f"### 📄 參考資料：{fname}\n{content}\n")
                    except:
                        pass
        context_str = "\n".join(megaprompt_parts)
        
        prompt = (
            "你是一個超群的個人專屬知識庫分析總監。請「只」使用以下隨附的全部私人維基筆記當作唯一事實來源，來解答使用者的問題。\n"
            "如果有找到答案，請總結並引用檔案名稱。如果資料庫內沒有，請誠實說沒有，不要自己腦補。\n"
            "請將重點歸納成一份排版精美的深度分析 Markdown 文件。\n\n"
            f"【使用者問題】\n{user_message}\n\n"
            f"【私人維基文字全庫】\n{context_str}"
        )
        
        ans = safe_generate(prompt, model_name=query_model)
        
        # Save to ~/Library/.../文章存檔/1. 反思/YYYY/MM
        now = datetime.now()
        out_dir = f"/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain/文章存檔/1. 反思/{now.strftime('%Y')}/{now.strftime('%m')}"
        os.makedirs(out_dir, exist_ok=True)
        # 清理檔名可能的不可見字元
        safe_msg = "".join(c for c in user_message[:15] if c.isalnum() or c in (" ", "_", "-")).strip()
        if not safe_msg: safe_msg = "檢索結果"
        filename = f"{now.strftime('%Y-%m-%d_%H%M')}_{safe_msg}.md"
        out_path = os.path.join(out_dir, filename)
        
        content = f"---\ntags: [大腦檢索, {query_model}]\nquestion: '{user_message}'\ndate: {now.strftime('%Y-%m-%d %H:%M')}\n---\n# {user_message}\n\n{ans}"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[維基檢索] 已完成並存檔至: {out_path}")
        
    except Exception as e:
        print(f"[錯誤][維基檢索] 失敗: {e}")

# ── Flask Routes ─────────────────────────────────────────────────────────────


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    global CHAT_HISTORY

    user_id = event.source.user_id

    # 安全驗證：只允許授權的 LINE 使用者
    if AUTHORIZED_USER_ID and user_id != AUTHORIZED_USER_ID:
        print(f"[拒絕] 傳訊者 user_id         : '{user_id}'")
        print(f"[拒絕] 環境變數 AUTHORIZED_USER_ID: '{AUTHORIZED_USER_ID}'")
        return

    user_text = event.message.text
    print(f"[收到] {user_id}: {user_text}")

    if user_text.strip() == "整理到筆記":
        if not CHAT_HISTORY:
            send_line_reply(event.reply_token, "老闆，目前沒有可以展開的上下文喔！請先跟我聊聊。")
            return
        send_line_reply(event.reply_token, "好的！正在為您深度推演並整理到 Obsidian 中...")
        # 由於是依賴過去對話，這裡暫時將 notes_context 留空，讓函數從 Persona 和對話中推演
        result_msg = expand_and_save_insight("整理對話", "")
        if AUTHORIZED_USER_ID:
            with ApiClient(line_config) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(to=AUTHORIZED_USER_ID, messages=[TextMessage(text=result_msg)])
                )
        return

    if user_text == "測試第一階段":
        daily_line_summary()
        send_line_reply(event.reply_token, "第一階段手動測試已觸發，請查看 Obsidian 與終端機日誌！")
        return

    if user_text == "測試第二階段總結":
        weekly_summary_job()
        send_line_reply(event.reply_token, "手動觸發【每週總結】完成！")
        return

    if user_text == "測試第二階段冥想":
        morning_meditation_job()
        send_line_reply(event.reply_token, "手動觸發【早晨冥想】完成！")
        return

    # ── 嚴格三岔路口：以標點符號判斷訊息類型 ─────────────────────────────────
    text = user_text.strip()

    # 提早攔截進度查詢，避免被標點符號（例如問號）誤判為發問
    import unicodedata
    raw_clean = "".join(ch for ch in text if not unicodedata.category(ch).startswith(("Z", "C")) or ch == " ")
    raw_clean = raw_clean.strip("!！❕?？.，, ").replace("line詢問，", "").strip()
    if raw_clean in ["掃描進度", "進度", "查看進度"]:
        try:
            import os
            if not os.path.exists("/tmp/wiki_scan.log"):
                reply_msg = "目前沒有產生任何進度日誌喔！或者掃描已經結束。"
            else:
                with open("/tmp/wiki_scan.log", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if not lines:
                        reply_msg = "目前沒有產生任何進度日誌喔！"
                    else:
                        last_lines = "".join(lines[-15:])
                        reply_msg = f"🔍 最新掃描進度：\n\n{last_lines}"
            send_line_reply(event.reply_token, reply_msg)
        except Exception as e:
            send_line_reply(event.reply_token, f"無法讀取掃描進度：{e}")
        return

    if text.lower().endswith(("wiki?", "wiki？")):
        # ── 全庫維基超級檢索 (Long Context) ──
        clean_text = text[:-5].strip()
        send_line_reply(event.reply_token, "✅ 已啟動維基大腦全局檢索，結果將彙整至筆記本【1. 反思】中。")
        executor.submit(_query_wiki_background, clean_text)
        return

    elif text.endswith(("?", "？")):
        # ── 原本的大腦問答模式 (ChromaDB Vector Search) ──
        try:
            notes_context = search_obsidian(text)
            reply_text = answer_with_obsidian(text, notes_context)
        except Exception as e:
            reply_text = "老闆，查詢筆記時發生錯誤，請稍後再試！"
            print(f"[錯誤][問答] {e}")

    elif text.endswith(("!", "！", "❕")):
        # ── Mac 控制模式：移除句尾驚嘆號再送給 Gemini ──
        cmd_text = text.rstrip("!！❕")

        # 攔截特殊指令：脆發文 / 新聞 / 冥想
        # 使用 strip + 正規化移除可能的不可見 Unicode 字元
        import unicodedata
        cmd_clean = "".join(ch for ch in cmd_text if not unicodedata.category(ch).startswith(("Z", "C")) or ch == " ").strip()
        print(f"[DEBUG][指令] cmd_clean='{cmd_clean}'")

        if cmd_clean == "脆發文":
            send_line_reply(event.reply_token, "📤 發文中...")
            executor.submit(_publish_threads_background)
            return

        if cmd_clean == "新聞":
            send_line_reply(event.reply_token, "📰 製作新聞中...")
            executor.submit(_generate_news_background)
            return

        if cmd_clean == "冥想":
            send_line_reply(event.reply_token, "🧘 冥想生成中...")
            executor.submit(_generate_meditation_background)
            return

        if cmd_clean == "匹克球":
            send_line_reply(event.reply_token, "🏓 匹克球與健走文獻監控啟動中...")
            executor.submit(_generate_pickleball_background, event.source.user_id)
            return

        if cmd_clean == "停止掃描文件":
            send_line_reply(event.reply_token, "🛑 正在強制煞車，中止當前的知識庫爬蟲與文件掃描...")
            executor.submit(_stop_build_wiki)
            return

        if cmd_clean == "重新掃描文件":
            send_line_reply(event.reply_token, "🧹 收到強制重置指令。將無視舊紀錄，開始重新掃描所有來源檔案重建維基，請稍候。")
            executor.submit(_run_build_wiki, force=True)
            return

        if cmd_clean in ["掃描文件", "掃描文章"]:
            send_line_reply(event.reply_token, "✅ 知識庫爬蟲起動中，將會開始依照資料夾建立鏡像維基，請稍候查看 Obsidian 維基目錄刷新。")
            executor.submit(_run_build_wiki)
            return

        try:
            reply_text = process_with_gemini(cmd_text) or "老闆，目前 API 額度塞車中，請稍後再試！"
        except Exception as e:
            reply_text = "老闆，執行指令時發生錯誤，請稍後再試！"
            print(f"[錯誤][Mac控制] {e}")

    else:
        # ── 日常收錄模式 ──
        save_note(user_text)
        reply_text = "✅ 已收錄至記憶庫"

    send_line_reply(event.reply_token, reply_text)
    print(f"[回覆] {reply_text[:100]}...")


# ── 語音處理邏輯 (MLX Whisper + Gemini) ──────────────────────────────────────

def process_audio_transcription(message_id, reply_token):
    """
    非同步處理：下載音檔 -> Whisper 轉錄 -> Gemini 摘要 -> 存入 Obsidian -> LINE 回覆
    """
    temp_audio_path = f"/tmp/line_audio_{message_id}.m4a"
    try:
        # 1. 下載 LINE 音檔
        with ApiClient(line_config) as api_client:
            line_blob_api = MessagingApiBlob(api_client)
            message_content = None
            for attempt in range(5):
                message_content = line_blob_api.get_message_content(message_id)
                if message_content is not None:
                    break
                print(f"[語音] 尚未取得音檔，等待 2 秒後重試... ({attempt + 1}/5)")
                time.sleep(2)
            
            if message_content is None:
                raise ValueError("無法讀取語音檔案內容，可能檔案過大或 LINE 伺服器延遲。")
                
            with open(temp_audio_path, "wb") as f:
                f.write(message_content)
        print(f"[語音] 音檔已暫存：{temp_audio_path}")

        # ── 雙重字典讀取 ──────────────────────────────────────────────────────
        # 正確路徑（沒有雙 .txt）
        custom_dict_path = "/Users/wenhung/Library/Mobile Documents/com~apple~CloudDocs/MacMini/custom_dict.txt"
        custom_dict_str  = ""
        try:
            with open(custom_dict_path, "r", encoding="utf-8") as f:
                terms = [ln.strip() for ln in f if ln.strip()]
            custom_dict_str = "、".join(terms)
            print(f"[語音] 字典讀取成功，共 {len(terms)} 個專有名詞")
        except FileNotFoundError:
            print(f"[語音] 找不到自訂字典，繼續不帶字典提示")
        except Exception as e:
            print(f"[語音] 字典讀取失敗：{e}")

        # 2. 音檔分段處理（pydub Chunking）─────────────────────────────────────
        # 長音檔（> 3 分鐘）會造成 Whisper 記憶體耗盡與 semaphore 洩漏，
        # 改為切割成 3 分鐘小片段，逐段轉錄後再合併。
        print(f"[語音] 開始讀取音檔（pydub）...")
        from pydub import AudioSegment

        CHUNK_MS      = 3 * 60 * 1000   # 每段 3 分鐘（毫秒）
        audio         = AudioSegment.from_file(temp_audio_path)
        total_ms      = len(audio)
        total_min     = total_ms / 60000
        print(f"[語音] 音檔長度：{total_min:.1f} 分鐘")

        # 第一重：Whisper initial_prompt （內嵌字典，強制提示模型正確發音）
        dict_hint = f"。請務必正確拼寫以下專有名詞：{custom_dict_str}。" if custom_dict_str else ""
        initial_prompt = (
            f"這是一段台灣家醫科醫師的語音紀錄，請以繁體中文輸出{dict_hint}"
        )

        raw_parts    = []
        chunk_paths  = []   # 暫存分段檔，用完立刻刪除

        if total_ms <= CHUNK_MS:
            # ── 短音檔：直接轉錄 ─────────────────────────────────────────────
            print(f"[語音] 短音檔，直接轉錄...")
            result = mlx_whisper.transcribe(
                temp_audio_path,
                path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                initial_prompt=initial_prompt,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
                fp16=True,                            # 半精度，節省 GPU 記憶體
            )
            raw_parts.append(result.get("text", "").strip())
            del result
            gc.collect()
            mx.metal.clear_cache()
        else:
            # ── 長音檔：切成 3 分鐘片段逐段轉錄 ──────────────────────────────
            starts = list(range(0, total_ms, CHUNK_MS))
            print(f"[語音] 長音檔，切成 {len(starts)} 段轉錄...")
            for idx, start_ms in enumerate(starts):
                end_ms     = min(start_ms + CHUNK_MS, total_ms)
                chunk      = audio[start_ms:end_ms]
                chunk_path = f"/tmp/line_audio_{message_id}_chunk{idx}.m4a"
                chunk.export(chunk_path, format="ipod")   # m4a → ipod codec
                chunk_paths.append(chunk_path)
                del chunk   # 立刻釋放 pydub 物件，避免記憶體堆積

                print(f"[語音] 轉錄第 {idx+1}/{len(starts)} 段（"
                      f"{start_ms//60000:.0f}分{(start_ms%60000)//1000:.0f}秒 ~ "
                      f"{end_ms//60000:.0f}分{(end_ms%60000)//1000:.0f}秒）...")
                try:
                    result = mlx_whisper.transcribe(
                        chunk_path,
                        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                        initial_prompt=initial_prompt,
                        condition_on_previous_text=False,   # 抗幻覺：每段獨立
                        no_speech_threshold=0.6,
                        compression_ratio_threshold=2.4,
                        fp16=True,                          # 半精度，節省 GPU 記憶體
                    )
                    chunk_text = result.get("text", "").strip()
                    if chunk_text:
                        raw_parts.append(chunk_text)
                        print(f"[語音] 第 {idx+1} 段完成：{chunk_text[:60]}...")
                    else:
                        print(f"[語音] 第 {idx+1} 段為靜音，跳過")
                finally:
                    # 立刻刪除分段暫存，釋放磁碟
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)
                    # ── 強制資源釋放（防止 semaphore 洩漏 / OOM）──────────────
                    try:
                        del result
                    except NameError:
                        pass
                    gc.collect()                # 強制 Python GC
                    mx.metal.clear_cache()      # 清空 MLX Metal GPU 快取
                    time.sleep(2)               # 喘息 2 秒，避免連續推播擠爆執行緒

        raw_text = "\n".join(raw_parts).strip()
        print(f"[語音] 全段轉錄合併完成，總字數：{len(raw_text)}")

        if not raw_text:
            send_push_message(AUTHORIZED_USER_ID, "老闆，這段語音好像沒有聲音，我聽不清楚喔！")
            return

        # 3. Gemini 第二重：全局字典校對────────────────────────────────────────
        print(f"[語音] 呼叫 Gemini 2.5 Flash 進行雙重字典校對...")
        dict_rule = (
            f"【最高指導原則】：請嚴格比對此專有名詞字典：「{custom_dict_str}」。"
            "若逐字稿中有發音相近的名字或術語，必須強制替換為字典內的正確用字。\n"
        ) if custom_dict_str else ""

        correction_prompt = (
            "你是一個極度精準的語音轉錄校對員。以下是一段由語音辨識系統分段產出的逐字稿。\n"
            "你的任務是：\n\n"
            "1. 修正同音錯字與不通順的斷句，特別是段落交界處。\n"
            f"2. {dict_rule}"
            "3. 100% 忠於原意，絕對禁止擴充句子、總結重點或自行腦補。\n"
            "4. 絕對禁止在任何人名或專有名詞前後加上星號 (*) 或任何 Markdown 標示。直接輸出純淨的純文字。\n"
            "5. 絕對禁止輸出開場白、對話回應或結語。\n\n"
            f"【語音逐字稿內容】\n{raw_text}"
        )

        corrected_text = safe_generate(correction_prompt)

        # 強制清除星號標記（加上 None 防護，避免 Gemini 回傳空值時崩潰）
        if corrected_text is None:
            print("[語音] ⚠️  Gemini 回傳 None，使用原始轉錄文備用")
            corrected_text = raw_text or "⚠️ Gemini 校對失敗或回傳空值，請檢查音檔長度或稍後重試。"
        else:
            corrected_text = corrected_text.replace("*", "")

        # 4. 存入 Obsidian
        save_note(f"[語音紀錄]\n{corrected_text}")
        print(f"[語音] 校對內容已存入記憶庫")

        # 5. 回覆使用者校對結果
        send_push_message(AUTHORIZED_USER_ID, corrected_text)

    except Exception as e:
        error_msg = f"老闆，語音處理出錯了：{e}"
        print(f"[錯誤][語音處理] {e}")
        send_push_message(AUTHORIZED_USER_ID, error_msg)
    finally:
        # 6. 刪除暫存檔
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
            print(f"[語音] 已清理暫存檔：{temp_audio_path}")


def send_push_message(to_user_id, text):
    """主動推播訊息 (用於非同步任務回報)"""
    if not to_user_id: return
    try:
        with ApiClient(line_config) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=to_user_id, messages=[TextMessage(text=text)])
            )
    except Exception as e:
        print(f"[錯誤][推播失敗] {e}")


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    """處理 LINE 語音訊息事件"""
    user_id = event.source.user_id
    if AUTHORIZED_USER_ID and user_id != AUTHORIZED_USER_ID:
        return

    message_id = event.message.id
    print(f"[音訊] 收到來自 {user_id} 的語音訊息 (ID: {message_id})")
    
    # 立即給予初步回應，避免使用者乾等
    send_line_reply(event.reply_token, "🎙️ 收到語音訊息！正在本地進行 AI 精準轉錄與摘要，請稍候片刻...")

    # 使用執行緒池非同步處理，避免阻塞下一則訊息
    executor.submit(process_audio_transcription, message_id, event.reply_token)


# ── APScheduler 啟動 ─────────────────────────────────────────────────────────

taipei_tz = pytz.timezone("Asia/Taipei")
scheduler = BackgroundScheduler(timezone=taipei_tz)
scheduler.add_job(
    daily_line_summary,
    trigger="cron",
    hour=11,
    minute=0,
    misfire_grace_time=3600,
    id="daily_line_summary"
)
# scheduler.add_job(  # ── 節流優化：已停用晚安晨報 LINE 推播 ──
#     morning_briefing,
#     trigger="cron",
#     hour=20,
#     minute=0,
#     id="morning_briefing"
# )
scheduler.add_job(
    weekly_summary_job,
    trigger="cron",
    day_of_week="fri",
    hour=13,
    minute=0,
    misfire_grace_time=3600,
    id="weekly_summary_job"
)
# scheduler.add_job(  # ── 改為 LINE「冥想！」手動觸發 ──
#     morning_meditation_job,
#     trigger="cron",
#     day_of_week="mon-fri",
#     hour=8,
#     minute=30,
#     misfire_grace_time=3600,
#     id="morning_meditation_job"
# )


def run_build_vector_db():
    """在背景執行 build_vector_db.py 以增量更新本地向量資料庫"""
    script_path = os.path.join(BASE_DIR, "build_vector_db.py")
    if not os.path.isfile(script_path):
        print(f"[向量DB] 找不到 {script_path}，略過")
        return
    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True, text=True, timeout=300
        )
        print(f"[向量DB] build_vector_db.py 執行完畢（exit={result.returncode}）")
        if result.stdout:
            print(f"[向量DB] stdout: {result.stdout[-500:]}")
        if result.stderr:
            print(f"[向量DB] stderr: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        print("[向量DB] build_vector_db.py 執行逾時（5分鐘）")
    except Exception as e:
        print(f"[向量DB] 執行失敗：{e}")


# scheduler.add_job(  # ── 暫停：向量資料庫改為手動觸發 ──
#     run_build_vector_db,
#     trigger="cron",
#     hour=14,
#     minute=0,
#     misfire_grace_time=3600,
#     id="build_vector_db"
# )


def run_sync_gmail():
    """在背景執行 sync_gmail.py 以增量同步 Gmail 電子報至 Obsidian"""
    script_path = os.path.join(BASE_DIR, "sync_gmail.py")
    if not os.path.isfile(script_path):
        print(f"[Gmail排程] 找不到 {script_path}，略過")
        return
    try:
        result = subprocess.run(
            [os.path.join(BASE_DIR, ".venv", "bin", "python"), script_path],
            capture_output=True, text=True, timeout=300
        )
        print(f"[Gmail排程] sync_gmail.py 執行完畢（exit={result.returncode}）")
        if result.stdout:
            print(f"[Gmail排程] stdout: {result.stdout[-500:]}")
        if result.stderr:
            print(f"[Gmail排程] stderr: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        print("[Gmail排程] sync_gmail.py 執行逾時（5分鐘）")
    except Exception as e:
        print(f"[Gmail排程] 執行失敗：{e}")


scheduler.add_job(
    run_sync_gmail,
    trigger="interval",
    hours=12,
    id="sync_gmail"
)


def run_weaver():
    """在背景執行 weaver.py 以自動提練筆記標籤（每週一下午 13:00）"""
    script_path = os.path.join(BASE_DIR, "weaver.py")
    if not os.path.isfile(script_path):
        print(f"[Weaver排程] 找不到 {script_path}，略過")
        return
    try:
        result = subprocess.run(
            [os.path.join(BASE_DIR, ".venv", "bin", "python"), script_path],
            capture_output=True, text=True, timeout=600
        )
        print(f"[Weaver排程] weaver.py 執行完畢（exit={result.returncode}）")
        if result.stdout:
            print(f"[Weaver排程] stdout: {result.stdout[-500:]}")
        if result.stderr:
            print(f"[Weaver排程] stderr: {result.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        print("[Weaver排程] weaver.py 執行逾時（10分鐘）")
    except Exception as e:
        print(f"[Weaver排程] 執行失敗：{e}")


# scheduler.add_job(  # ── 暫停：weaver 標籤分類改為手動觸發 ──
#     run_weaver,
#     trigger="cron",
#     day_of_week="mon",
#     hour=13,
#     minute=0,
#     misfire_grace_time=3600,
#     id="weaver_job"
# )


def run_daily_guideline_job():
    """在背景執行 daily_med_guideline.py（週一至週五 10:00）"""
    import logging
    log = logging.getLogger(__name__)
    script_path = os.path.join(BASE_DIR, "daily_med_guideline.py")
    if not os.path.isfile(script_path):
        log.warning(f"[指引新知] 找不到 {script_path}，略過")
        return
    try:
        result = subprocess.run(
            [os.path.join(BASE_DIR, ".venv", "bin", "python"), script_path],
            capture_output=True, text=True, timeout=600
        )
        log.info(f"[指引新知] 執行完畢（exit={result.returncode}）")
        if result.stdout:
            log.info(f"[指引新知] stdout: {result.stdout[-500:]}")
        if result.stderr:
            log.warning(f"[指引新知] stderr: {result.stderr[-500:]}")
        if result.returncode != 0:
            log.error(f"[指引新知] 非零退出碼: {result.returncode}")
    except subprocess.TimeoutExpired:
        log.error("[指引新知] 執行逾時（10分鐘）")
    except Exception as e:
        log.error(f"[指引新知] 執行失敗：{e}")


scheduler.add_job(
    run_daily_guideline_job,
    trigger="cron",
    day_of_week="mon-fri",
    hour=10,
    minute=0,
    misfire_grace_time=3600,
    id="daily_guideline_job"
)

scheduler.start()
print("[排程] APScheduler 已啟動，每天 11:00（台北）自動每日碎語歸納")
print("[排程] 冥想/新聞改為 LINE 手動觸發（已停用排程）")
print("[排程] APScheduler 已啟動，每週五 13:00（台北）每週總結")
print("[排程] APScheduler 已啟動，週一至週五 10:00（台北）自動每日醫學指引")
print("[排程] APScheduler 已啟動，每 12 小時自動執行 sync_gmail.py")
print("[排程] 已暫停：build_vector_db、weaver（改為手動觸發）")
print("[排程] 已移除 LaunchAgent：daily-news-digest、medical-literature-monitor、move-obsidian-notes")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"LINE Mac Bot 啟動中，監聽 port {port} ...")
    app.run(host="0.0.0.0", port=port, debug=False)
