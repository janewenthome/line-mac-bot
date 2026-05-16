import re
with open('/Users/wenhung/Tseng-Health-Bot/app.py', 'r') as f:
    lines = f.readlines()

output = []
for i, line in enumerate(lines):
    if "gemini_client.aio.models.generate_content" in line or "from google import genai" in line or "types.Part.from_bytes" in line or "types.GenerateContentConfig" in line or "gemini_client = genai.Client" in line:
        start = max(0, i-7)
        end = min(len(lines), i+15)
        output.append(f"----- MATCH AT {i+1} -----")
        for j in range(start, end):
            output.append(f"{j+1}: {lines[j].rstrip()}")
        output.append("----- END MATCH -----")
        
with open('gemini_usages.txt', 'w') as f:
    f.write('\n'.join(output))
