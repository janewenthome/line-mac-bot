import os
import random
import time
from datetime import datetime
from PyPDF2 import PdfReader
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Configuration Paths - Matched with app.py
BASE_DIR = os.path.expanduser("~/line-mac-bot")
OBSIDIAN_VAULT_PATH = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"

PDF_DIR = "/Users/wenhung/Library/Mobile Documents/com~apple~CloudDocs/MacMini/Guideline"
SKILL_FILE = os.path.join(OBSIDIAN_VAULT_PATH, "系統設定", "SKILL：醫學指引拆解規則.md")
OUT_DIR = os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "Wiki", "醫學指引新知")
MEMORY_FILE = os.path.join(BASE_DIR, "processed_guidelines.txt")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)


def safe_generate(prompt: str, system_instruction: str, model_name="google/gemini-2.5-flash-lite", **kwargs) -> str:
    """Invokes OpenRouter model with retries."""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ],
                **kwargs
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[safe_generate] Attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(20)
    raise Exception("API failure after 3 attempts.")


def get_all_pdfs(directory: str) -> list[str]:
    """Recursively find all PDFs in the given directory."""
    pdf_files = []
    for root, _, files in os.walk(directory):
        for f in files:
            if f.lower().endswith('.pdf'):
                pdf_files.append(os.path.join(root, f))
    return pdf_files


def extract_pdf_text(pdf_path: str, max_pages: int = 50) -> str:
    """Extracts text from a PDF, limiting to max_pages to prevent context overflow."""
    text_chunks = []
    try:
        reader = PdfReader(pdf_path)
        num_pages = min(len(reader.pages), max_pages)
        for i in range(num_pages):
            page_text = reader.pages[i].extract_text()
            if page_text:
                text_chunks.append(page_text)
    except Exception as e:
        print(f"Failed to read PDF {pdf_path}: {e}")
    return "\n".join(text_chunks)


def get_existing_topics() -> list[str]:
    """掃描輸出目錄，從檔名中提取已產生的主題清單（最省 token 的比對方式）。"""
    topics = []
    for root, _, files in os.walk(OUT_DIR):
        for f in files:
            if f.endswith(".md") and "_" in f:
                # 檔名格式: 2026-03-25_BPH指南.md → 提取 "BPH指南"
                topic = f.rsplit(".", 1)[0].split("_", 1)[-1]
                if topic:
                    topics.append(topic)
    return topics


def generate_topic_name(pdf_text: str, filename: str, existing_topics: list[str]) -> str:
    """Prompt Gemini to extract a suitable topic/title from the document, avoiding duplicates."""
    existing_str = "、".join(existing_topics) if existing_topics else "（目前無已產生主題）"
    prompt = (
        f"Based on the following document excerpt (Filename: {filename}), "
        f"please suggest a short, concise topic name (under 15 characters if possible, Chinese preferred).\n"
        f"Return ONLY the exact topic name, without any other text or punctuation.\n\n"
        f"⚠️ The following topics have ALREADY been generated, please suggest a DIFFERENT topic:\n"
        f"{existing_str}\n\n"
        f"Excerpt:\n{pdf_text[:2000]}"
    )
    try:
        resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
        if not resp.text:
            return filename.replace(".pdf", "").replace("/", "-")[0:15]
        topic = str(resp.text).strip().replace("/", "-")
        # 最終防護：如果 Gemini 仍回傳重複主題，加上檔名前綴區分
        if topic in existing_topics:
            topic = f"{topic}_{filename[:8]}"
        return topic
    except Exception as e:
        print(f"Failed to generate topic name: {e}")
        return filename.replace(".pdf", "").replace("/", "-")[0:15]


def run_daily_guideline():
    """Main execution flow for Guideline Automation Engine."""
    print("[Guideline] Starting Daily Med Guideline Automation Engine...")
    
    # 1. Verification and Setup
    os.makedirs(OUT_DIR, exist_ok=True)
    if not os.path.exists(SKILL_FILE):
        print(f"[Guideline] SKILL file not found: {SKILL_FILE}")
        return
        
    with open(SKILL_FILE, "r", encoding="utf-8") as f:
        system_instruction = f.read()

    # 2. Get PDFs
    all_pdfs = get_all_pdfs(PDF_DIR)
    if not all_pdfs:
        print(f"[Guideline] No PDFs found in {PDF_DIR}")
        return

    # 3. 取得已產生的主題清單（從輸出目錄檔名解析，零 API 成本）
    existing_topics = get_existing_topics()
    print(f"[Guideline] 已有 {len(existing_topics)} 個主題: {existing_topics}")

    # 4. 隨機抽取一篇 PDF（所有 PDF 都可被重複使用，只要主題不重複）
    random.shuffle(all_pdfs)
    selected_pdf = all_pdfs[0]
    filename = os.path.basename(selected_pdf)
    print(f"[Guideline] Selected PDF: {filename}")

    # 擷取文字
    pdf_text = str(extract_pdf_text(selected_pdf))
    if not pdf_text.strip():
        print(f"[Guideline] Failed to extract text or PDF is empty: {filename}")
        return

    # 產生主題名稱（Gemini 只需多看幾個已有主題字串，token 成本極低）
    topic_name = generate_topic_name(pdf_text, filename, existing_topics)
    print(f"[Guideline] Generated Topic: '{topic_name}'")

    # 最終主題重複檢查
    if topic_name in existing_topics:
        print(f"[Guideline] Topic '{topic_name}' already exists, skipping.")
        return

    # 5. Generate Guide Note
    prompt = (
        f"文件名稱：{filename}\n"
        f"文件位置：{selected_pdf}\n"
        f"以下是擷取的部分文件內容，請依據 system_instruction 的規則為我撰寫導讀筆記：\n\n"
        f"{pdf_text[0:15000]}"
    )

    try:
        print("[Guideline] Generating guideline note with Gemini...")
        guide_note = safe_generate(
            prompt=prompt, 
            system_instruction=system_instruction
        )
    except Exception as e:
        print(f"[Guideline] Failed to generate note: {e}")
        return

    # 6. Save to Obsidian
    now = datetime.now()
    year_str = now.strftime("%Y")
    month_str = now.strftime("%m")
    date_str = now.strftime("%Y-%m-%d")
    out_dir_ym = os.path.join(OUT_DIR, year_str, month_str)
    os.makedirs(out_dir_ym, exist_ok=True)
    
    output_filename = f"{date_str}_{topic_name}.md"
    output_path = os.path.join(out_dir_ym, output_filename)
    
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(guide_note)
        print(f"[Guideline] Saved note to {output_path}")
    except Exception as e:
        print(f"[Guideline] Failed to save note: {e}")
        return

    print(f"[Guideline] Successfully generated '{topic_name}' from '{filename}'. Done!")

if __name__ == "__main__":
    run_daily_guideline()

