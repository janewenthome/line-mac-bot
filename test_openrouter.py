import os
import json
from openai import OpenAI

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    from dotenv import load_dotenv
    load_dotenv("/Users/wenhung/line-mac-bot/.env")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

MAC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "在 Mac Mini 上執行 shell 命令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要執行的 shell 命令"
                    }
                },
                "required": ["command"]
            }
        }
    }
]

resp = client.chat.completions.create(
    model="google/gemini-2.5-flash-lite",
    messages=[{"role": "user", "content": "幫我執行 echo hello 的 shell 指令"}],
    tools=MAC_TOOLS
)

msg = resp.choices[0].message
if msg.tool_calls:
    print("Function Calling 測試成功！")
    for t in msg.tool_calls:
        print(f"Tool name: {t.function.name}")
        print(f"Args: {t.function.arguments}")
else:
    print("測試失敗，沒有回傳工具呼叫。")
    print(msg)
