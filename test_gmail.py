import re
from datetime import datetime

text = 'From:"test user"<test@genai.com>'
match = re.search(r'From:\s*(?:".*?"\s*)?<([^>]+)>', text, re.IGNORECASE)
if match:
    sender = match.group(1).strip()
    print("MATCH1:", sender)
else:
    match2 = re.search(r'From:\s*([^\r\n]+)', text, re.IGNORECASE)
    if match2:
        print("MATCH2:", match2.group(1).strip())
