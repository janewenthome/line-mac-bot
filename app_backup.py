"""
LINE Mac Bot - 透過 LINE 訊息遠端操控 Mac Mini
使用 Gemini API 理解自然語言指令並執行 Mac 操作
新增：AI 自動分類筆記 + APScheduler 週五煉金
"""
import os
import subprocess
import time
import random
from datetime import datetime, timedelta
import requests
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import google.generativeai as genai
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

load_dotenv()

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
AUTHORIZED_USER_ID = os.environ.get("LINE_AUTHORIZED_USER_ID", "")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
CWA_API_KEY = os.getenv("CWA_API_KEY", "")
MOENV_API_KEY = os.getenv("MOENV_API_KEY", "")  # 選做：環保署 AQI 金鑰

genai.configure(api_key=GEMINI_API_KEY)

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
MAC_TOOLS = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="run_shell",
            description="在 Mac Mini 上執行 shell 命令。適用於檔案管理、系統操作、安裝軟體等。",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "command": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="要執行的 shell 命令"
                    )
                },
                required=["command"]
            )
        ),
        genai.protos.FunctionDeclaration(
            name="open_app",
            description="在 Mac 上開啟應用程式",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "app_name": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="應用程式名稱，例如：Safari、Music、Finder、Terminal、VS Code"
                    )
                },
                required=["app_name"]
            )
        ),
        genai.protos.FunctionDeclaration(
            name="run_applescript",
            description="執行 AppleScript 進行 GUI 自動化、控制 Mac 系統設定，例如調整音量、播放音樂、顯示通知",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "script": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="要執行的 AppleScript 程式碼"
                    )
                },
                required=["script"]
            )
        ),
        genai.protos.FunctionDeclaration(
            name="get_system_info",
            description="取得 Mac 系統資訊，包含 CPU 使用率、記憶體、磁碟空間、執行中的程序",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "info_type": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        enum=["cpu", "memory", "disk", "processes", "network", "all"],
                        description="要查詢的資訊類型：cpu/memory/disk/processes/network/all"
                    )
                },
                required=["info_type"]
            )
        ),
        genai.protos.FunctionDeclaration(
            name="take_screenshot",
            description="截取目前 Mac 螢幕畫面，並將圖片儲存到指定路徑",
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "save_path": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="截圖儲存路徑（預設 /tmp/screenshot.png）"
                    )
                }
            )
        ),
    ]
)

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

def safe_generate(model, prompt: str) -> str:
    """呼叫 model.generate_content，遇到例外（含 429 Quota）自動等待 20 秒後重試，最多 3 次"""
    for attempt in range(3):
        try:
            resp = model.generate_content(prompt)
            return resp.text
        except Exception as e:
            print(f"[safe_generate] 第 {attempt + 1} 次失敗：{e}")
            if attempt < 2:
                time.sleep(20)
    return "老闆，目前 API 額度塞車中，我正在深呼吸，請您一分鐘後再問我一次！"


# ── 筆記工具函數 ─────────────────────────────────────────────────────────────

def search_obsidian(query: str) -> str:
    """優先讀取 Persona.md 完整內容，再掃描最近修改的 3 個 .md 檔（各限 1500 字元）作為上下文"""
    if not os.path.isdir(OBSIDIAN_VAULT_PATH):
        print(f"[Obsidian] 找不到 Vault 路徑：{OBSIDIAN_VAULT_PATH}")
        return ""

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
    except FileNotFoundError:
        print(f"[Obsidian] 找不到 Persona.md，略過")
    except Exception as e:
        print(f"[Obsidian] 讀取 Persona.md 失敗：{e}")

    # ── 2. 掃描其他 .md 檔（排除 Persona.md），取最近修改的 3 個 ──────────────
    md_files = []
    for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
        for fname in files:
            if fname.endswith(".md") and fname != "Persona.md":
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                    md_files.append((mtime, fpath))
                except (OSError, FileNotFoundError) as e:
                    print(f"[Obsidian] 取得修改時間失敗，略過 {fpath}：{e}")
    md_files.sort(reverse=True)
    top_files = md_files[:3]

    parts = []
    if persona_content:
        parts.append(f"### 📄 Persona.md（核心個人檔案）\n{persona_content}")

    for mtime, fpath in top_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read(1500)  # 每檔最多 1500 字元
            rel_path = os.path.relpath(fpath, OBSIDIAN_VAULT_PATH)
            mod_time = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            parts.append(f"### 📄 {rel_path}（最後修改：{mod_time}）\n{content}")
        except FileNotFoundError as e:
            print(f"[Obsidian] 檔案不存在，略過 {fpath}：{e}")
        except (OSError, PermissionError) as e:
            print(f"[Obsidian] 無法存取，略過 {fpath}：{e}")
        except Exception as e:
            print(f"[Obsidian] 讀取失敗 {fpath}：{e}")

    print(f"[Obsidian] 已載入 {len(parts)} 個筆記檔案作為上下文")
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
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")

        persona_section = (
            f"【核心個人檔案 Persona.md】\n{persona_content}\n\n"
            if persona_content else ""
        )
        notes_section = (
            f"【Obsidian 最近筆記內容】\n{notes_context}\n\n"
            if notes_context else ""
        )

        prompt = (
            "你現在是搭載『意圖感知型知識探索引擎』的數位 Muse。當使用者提出問題時，請啟動鏈式聯想。\n"
            "請參考過去的對話紀錄，以確保對話的連貫性。如果使用者是在延續先前的話題，請接續探討。\n"
            "【注意：因為這是在 LINE 上閱讀，請保持非常簡短、精煉（約 150~200 字即可）】\n\n"
            "請嚴格使用以下格式直接回覆：\n"
            "🌟 創新觀點：結合跨領域筆記，給出一個大膽的假說或全新視角（一句話）。\n"
            "💡 建議行動：1~2 個具體、立即可行的微行動。\n\n"
            f"{persona_section}"
            f"{notes_section}"
            f"{history_text}"
            f"【使用者當前問題】\n{user_message}"
        )
        ai_reply = safe_generate(model, prompt).strip()

        # 將本次對話加入歷史紀錄 (User 與 Muse)
        CHAT_HISTORY.append({"role": "User", "content": user_message})
        CHAT_HISTORY.append({"role": "Muse", "content": ai_reply})
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
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")

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
            "你現在是數位 Muse。請將我們先前的對話脈絡（包含我的問題和你的簡短回覆），整理成一篇完整的 Obsidian 筆記。\n"
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
        expanded_content = safe_generate(model, prompt)

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
    每天 08:00 排程任務：
    讀取 inbox.txt → Gemini 整理並標記核心標籤 → 存入 Obsidian LINE每日碎語/YYYY/MM/ → 清空 inbox → LINE 推播
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

    # 2. 呼叫 Gemini 整理筆記並附加核心標籤
    try:
        notes_model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        tags_str = "、".join(CORE_TAGS)
        summary_prompt = (
            "你是一個整理專家。請將以下破碎的日常筆記整理成結構化 Markdown 文章。\n"
            "除了從以下 21 個核心標籤中挑選 1~3 個最相關的標籤之外，請務必再為這篇筆記加上『意圖與時態標籤』：\n"
            "1. 意圖標籤 (1~2個)：判斷這篇筆記背後的深層意圖，例如 #未來假說、#技術瓶頸突破、#倫理考量、#情感連結、#歷史借鑒、#情緒覺察。\n"
            "2. 時態標籤 (1個)：判斷這件事的狀態，例如 #已發生、#正在進行、#預期未來、#理論可能。\n\n"
            "請在文章最尾端嚴格遵守以下輸出格式：\n"
            "關聯節點：[[標籤A]], [[標籤B]]\n"
            "意圖與時態：#未來假說, #正在進行\n\n"
            f"核心標籤清單：{tags_str}\n"
            f"筆記內容：{notes_content}"
        )
        markdown_content = safe_generate(notes_model, summary_prompt)
    except Exception as e:
        print(f"[每日歸納] Gemini 整理失敗：{e}")
        return

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
        print(f"[每日歸納] 儲存失敗：{e}")
        return

    # 4. 儲存成功後才清空 inbox.txt
    with open(INBOX_PATH, "w", encoding="utf-8") as f:
        f.write("")
    print("[每日歸納] inbox.txt 已清空")

    # 5. Gemini 濃縮 150 字精要
    push_text = "老闆，今天的碎碎念已為您打包並連上星系圖囉！"
    try:
        summary_model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        condensed_prompt = (
            f"你是一個貼心的助理。請將以下這篇日常歸納筆記，濃縮成約 150 字的精要版，"
            f"語氣輕鬆溫暖，總結今天的重點情緒或事件。筆記內容：\n{markdown_content}"
        )
        push_text = safe_generate(summary_model, condensed_prompt).strip()
        print("[每日歸納] Gemini 濃縮完成")
    except Exception as e:
        print(f"[每日歸納] Gemini 濃縮失敗，使用備用字串：{e}")

    # 6. LINE 推播通知給授權使用者
    if not AUTHORIZED_USER_ID:
        print("[每日歸納] 未設定 AUTHORIZED_USER_ID，略過推播")
        return
    try:
        with ApiClient(line_config) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(
                    to=AUTHORIZED_USER_ID,
                    messages=[TextMessage(text=push_text)]
                )
            )
        print("[每日歸納] LINE 推播通知已送出")
    except Exception as e:
        print(f"[每日歸納] LINE 推播失敗：{e}")


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
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = (
            "你是一個細心的私人管家。以下是『明天』的天氣數據，"
            "請用『明天古坑／梅山的天氣是…』的語氣，寫一段 100 字以內的晚安預報給老闆，"
            "並提醒老闆明天出門的準備建議（例如帶傘、防曬、加件外套等）。\n\n"
            f"{data_text}"
        )
        briefing_text = safe_generate(model, prompt)
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
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = (
            "請閱讀我這一週的日常歸納，寫出一份『這一週來，我的情緒起伏、關注焦點與生活哲學總結』的 Markdown 報告。\n\n"
            f"{notes_text}"
        )
        report_content = safe_generate(model, prompt)

        # 存入 Obsidian/📊 每週總結/YYYY-Wxx_週報總結.md
        now = datetime.now()
        week_str = now.strftime("%Y-W%W")
        summary_dir = os.path.join(OBSIDIAN_VAULT_PATH, "📊 每週總結")
        os.makedirs(summary_dir, exist_ok=True)
        output_path = os.path.join(summary_dir, f"{week_str}_週報總結.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        print(f"[週報] 已儲存：{output_path}")

        # LINE 推播
        if AUTHORIZED_USER_ID:
            with ApiClient(line_config) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(
                        to=AUTHORIZED_USER_ID,
                        messages=[TextMessage(text=f"老闆，您的 {week_str} 週報已整理完畢，請到 Obsidian 查閱！")]
                    )
                )
            print("[週報] LINE 推播已送出")

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

        # ── 4. 呼叫 Gemini (精簡版 Prompt) ──
        model = genai.GenerativeModel(model_name="gemini-2.5-flash")
        prompt = (
            "你現在是我的專屬靈感教練與生活督導『Muse』。你深諳家醫科臨床、公共衛生、AI 系統架構，同時也是一位犀利卻溫暖的婚姻/教養顧問與文史哲學者。\n\n"
            "請閱讀以下我提供的【近期狀態】、【歷史的迴音】，以及抽出的【跨界筆記】。\n"
            "【重要排版限制】：為了讓我能在 3 分鐘內輕鬆讀完，請在下方區塊一與區塊二中，『只挑選最相關的兩個主題』進行深入探討即可，不要全部都寫！\n\n"
            "請嚴格按照以下四個區塊輸出：\n\n"
            "【第一部分：歷史對比與哲學新視角】（精簡扼要）\n"
            "- 對比「近期狀態」與「歷史迴音」，精準點出我的成長或重複的盲點。\n"
            "- 結合文史哲濾鏡，從「面對臨床與公衛」或「面對負面情緒」中【擇一】探討，給出昇華的解法。\n\n"
            "【第二部分：關係盲點與技術洞察】（精簡扼要）\n"
            "- 從「家庭與伴侶」或「AI與創作」中【擇一】給予犀利見解或技術點子。\n\n"
            "【第三部分：今日微行動清單】\n"
            "- 針對你剛才挑選的主題，給出 1~2 個今天立刻能做的具體微行動。\n\n"
            "【第四部分：LINE 晨間推播】\n"
            "- 用輕鬆幽默、宛如老搭檔的語氣，大約 150 字。\n"
            "- 告訴我今天你把哪兩個領域的概念結合了，送我一句最核心的哲學金句，並預告一個今日微行動。\n"
            "- 注意：格式必須明確標示出【第四部分：LINE 晨間推播】，以便程式截取。\n\n"
            "=== 以下為輸入素材 ===\n"
            f"[近期狀態-昨日碎語]:\n{recent_note}\n\n"
            f"[近期狀態-本週週報]:\n{latest_weekly}\n\n"
            f"[歷史的迴音]:\n{historical_echo}\n\n"
            f"[跨界筆記]:\n{notes_section}"
        )
        raw_result = safe_generate(model, prompt)

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
        date_str = now.strftime("%Y-%m-%d")
        meditation_dir = os.path.join(OBSIDIAN_VAULT_PATH, "早晨冥想")
        os.makedirs(meditation_dir, exist_ok=True)
        output_path = os.path.join(meditation_dir, f"{date_str}_早晨冥想.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(part_obsidian)
            f.write(f"\n\n關聯節點：{tag_a}, {tag_b}\n意圖與時態：#系統除錯, #情緒覺察, #已發生")
        print(f"[冥想] 已儲存：{output_path}")

        # LINE 推播
        if AUTHORIZED_USER_ID:
            with ApiClient(line_config) as api_client:
                MessagingApi(api_client).push_message(
                    PushMessageRequest(to=AUTHORIZED_USER_ID, messages=[TextMessage(text=part_line)])
                )
            print("[冥想] LINE 推播已送出")

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

    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        tools=[MAC_TOOLS],
        system_instruction=SYSTEM_PROMPT
    )

    chat = model.start_chat(enable_automatic_function_calling=False)
    response = chat.send_message(user_message)

    # 工具循環：持續執行直到 Gemini 完成任務
    for _ in range(10):  # 最多 10 輪工具呼叫
        # 收集所有 function call
        fn_calls = [p.function_call for p in response.parts if p.function_call]

        if not fn_calls:
            # 無工具呼叫，回傳最終文字（可能是 [SAVE_NOTE] 或正常回覆）
            text = "".join(p.text for p in response.parts if p.text)
            return text or "任務完成"

        # 執行所有工具並收集結果
        fn_responses = []
        for fc in fn_calls:
            result = execute_tool(fc.name, dict(fc.args))
            fn_responses.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fc.name,
                        response={"result": result}
                    )
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

    try:
        reply_text = process_with_gemini(user_text) or "老闆，目前 API 額度塞車中，我正在深呼吸，請您一分鐘後再問我一次！"

        # AI 自動分類：若 Gemini 判定為非 Mac 操作（回傳 [SAVE_NOTE]）
        if "[SAVE_NOTE]" in reply_text:
            # 老闆專屬硬規則：句尾是問號（半形或全形）就是提問，其他一律當作碎碎念存檔
            if user_text.strip().endswith(("?", "？")):
                notes_context = search_obsidian(user_text)
                # 如果沒有相關筆記，依然強制呼叫 answer_with_obsidian 來發揮 Muse 的創意
                reply_text = answer_with_obsidian(user_text, notes_context)
            else:
                # 非問號結尾 → 單純收錄
                save_note(user_text)
                reply_text = "✅ 已收錄至記憶庫"

    except Exception as e:
        reply_text = "老闆，目前 API 額度塞車中，我正在深呼吸，請您一分鐘後再問我一次！"
        print(f"[錯誤] {e}")

    send_line_reply(event.reply_token, reply_text)
    print(f"[回覆] {reply_text[:100]}...")


# ── APScheduler 啟動 ─────────────────────────────────────────────────────────

taipei_tz = pytz.timezone("Asia/Taipei")
scheduler = BackgroundScheduler(timezone=taipei_tz)
scheduler.add_job(
    daily_line_summary,
    trigger="cron",
    hour=8,
    minute=0,
    misfire_grace_time=3600,
    id="daily_line_summary"
)
scheduler.add_job(
    morning_briefing,
    trigger="cron",
    hour=20,
    minute=0,
    id="morning_briefing"
)
scheduler.add_job(
    weekly_summary_job,
    trigger="cron",
    day_of_week="fri",
    hour=13,
    minute=0,
    misfire_grace_time=3600,
    id="weekly_summary_job"
)
scheduler.add_job(
    morning_meditation_job,
    trigger="cron",
    day_of_week="mon-fri",
    hour=8,
    minute=30,
    misfire_grace_time=3600,
    id="morning_meditation_job"
)
scheduler.start()
print("[排程] APScheduler 已啟動，每天 08:00（台北）自動每日碎語歸納")
print("[排程] APScheduler 已啟動，每天 20:00（台北）晨報預抓（隔日早安資料）")
print("[排程] APScheduler 已啟動，每週五 13:00（台北）每週總結")
print("[排程] APScheduler 已啟動，週一至週五 08:30（台北）早晨冥想")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"LINE Mac Bot 啟動中，監聽 port {port} ...")
    app.run(host="0.0.0.0", port=port, debug=False)
