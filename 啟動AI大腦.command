#!/bin/bash
cd /Users/wenhung/line-mac-bot
source .venv/bin/activate
pkill -f "streamlit run chat_web.py"
/Users/wenhung/line-mac-bot/.venv/bin/streamlit run chat_web.py
