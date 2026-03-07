import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

api_key = os.getenv("GEMINI_API_KEY")
vault_path = '/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain'

client = genai.Client(api_key=api_key)

# 關閉所有安全審查（避免正常筆記被 PROHIBITED_CONTENT 阻擋）
SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

CORE_TAGS = [
    "[[家醫科臨床]]", "[[皮膚病]]", "[[巡迴醫療紀錄]]", "[[糖尿病照護]]", "[[公共衛生]]",
    "[[文獻閱讀心得]]", "[[三寶爸日常]]", "[[人生哲學]]", "[[旅行]]", "[[亞斯伯格]]",
    "[[AI與自動化]]", "[[專案開發紀錄]]", "[[文史哲簡單說]]", "[[音樂創作]]", "[[情緒]]",
    "[[鐵道紀行]]", "[[樂高與機械原理]]", "[[芳療與自然療癒]]", "[[科技與攝影]]",
    "[[活動與社區經營]]", "[[論文構想]]"
]

PROMPT_TEMPLATE = """你現在是我的自動化知識分類引擎。請閱讀以下這篇筆記。
1. 從我提供的 21 個核心標籤清單中，挑選出 1 到 3 個最相關的標籤（務必「只能」使用清單中的標籤）。
2. 為這篇筆記加上『意圖與時態標籤』：
   - 意圖標籤 (1~2個)：判斷這篇筆記背後的深層意圖，例如 #未來假說、#技術瓶頸突破、#倫理考量、#情感連結、#歷史借鑒、#情緒覺察。
   - 時態標籤 (1個)：判斷這件事的狀態，例如 #已發生、#正在進行、#預期未來、#理論可能。

請回傳一句簡短的摘要，並在最後附上標籤。格式必須嚴格遵守：
> 🤖 Muse 提煉：[一句話摘要]。
> 關聯節點：[[標籤1]], [[標籤2]]
> 意圖與時態：#意圖標籤1, #時態標籤

核心標籤清單：
{tags}

筆記內容：
{content}"""

for root, dirs, files in os.walk(vault_path):
    for filename in files:
        if not filename.endswith(".md"):
            continue

        filepath = os.path.join(root, filename)

        with open(filepath, "r", encoding="utf-8") as f:
            original_content = f.read()

        # 1. 新的防呆機制：檢查是否已經具備「意圖與時態」標籤
        if "意圖與時態：" in original_content[-500:]:
            print(f"[跳過] 已具備多維度標籤：{filename}")
            continue

        # 2. 智慧切除舊標籤：如果發現有舊版標籤，先把它切掉，還原乾淨的筆記
        content_to_process = original_content
        if "> 🤖 Muse 提煉：" in content_to_process:
            # 以舊標籤為界線，只保留前面的內容，並去掉多餘的空白與換行
            content_to_process = content_to_process.split("> 🤖 Muse 提煉：")[0].strip()
            print(f"[升級] 發現舊版標籤，正在重新提煉升級：{filename}")
        else:
            print(f"[處理] 正在提煉新筆記：{filename}")

        prompt = PROMPT_TEMPLATE.format(
            tags=", ".join(CORE_TAGS),
            content=content_to_process
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(safety_settings=SAFETY_SETTINGS)
            )
            result = response.text.strip()
        except Exception as e:
            print(f"[警告] 處理失敗，跳過此篇：{filename}\n  錯誤：{e}")
            continue

        # 3. 覆寫檔案：寫入「乾淨的原始筆記」+「換行」+「新版多維度標籤」
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content_to_process + "\n\n" + result + "\n")

        print(f"[成功] 完成處理：{filename}")
        time.sleep(2)