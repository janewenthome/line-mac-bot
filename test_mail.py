import re
text = 'From:"test user"<test@genai.com>'
match = re.search(r'From:\s*(.*)', text, re.IGNORECASE)
if match:
    sender = match.group(1).split()[-1].strip("<>")
    print(sender)
else:
    print("no")
