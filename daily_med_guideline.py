import os
import random
import time
from datetime import datetime
from PyPDF2 import PdfReader
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Configuration Paths - Matched with app.py
BASE_DIR = os.path.expanduser("~/line-mac-bot")
OBSIDIAN_VAULT_PATH = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"

PDF_DIR = "/Users/wenhung/Library/Mobile Documents/com~apple~CloudDocs/MacMini/Guideline"
SKILL_FILE = os.path.join(OBSIDIAN_VAULT_PATH, "系統設定", "SKILL：醫學指引拆解規則.md")
OUT_DIR = os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "3. 指引新知")
MEMORY_FILE = os.path.join(BASE_DIR, "processed_guidelines.txt")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)


def safe_generate(prompt: str, system_instruction: str, model_name="gemini-2.5-flash", **kwargs) -> str:
    """Invokes Gemini model with retries."""
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    **kwargs
                )
            )
            return resp.text
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


def load_memory() -> set[str]:
    """Load previously processed topics from memory file."""
    if not os.path.exists(MEMORY_FILE):
        return set()
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_memory(topic: str):
    """Save processed topic to memory file."""
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(topic + "\n")


def generate_topic_name(pdf_text: str, filename: str) -> str:
    """Prompt Gemini to extract a suitable topic/title from the document."""
    prompt = f"Based on the following document excerpt (Filename: {filename}), please suggest a short, concise topic name (under 15 characters if possible, Chinese preferred). Return ONLY the exact topic name, without any other text or punctuation.\n\nExcerpt:\n{pdf_text[:2000]}"
    try:
        # Use simple generate for topic
        resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
        
        # Ensure we have a string to strip
        if not resp.text:
            return filename.replace(".pdf", "").replace("/", "-")[0:15]
            
        return str(resp.text).strip().replace("/", "-")
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

    # 2. Get PDFs and setup memory
    pdfs = get_all_pdfs(PDF_DIR)
    if not pdfs:
        print(f"[Guideline] No PDFs found in {PDF_DIR}")
        return
        
    processed_topics = load_memory()
    MAX_ATTEMPTS = 5
    
    selected_pdf = None
    topic_name = ""
    pdf_text = ""
    
    # 3. Random selection loop matching memory file
    for attempt in range(MAX_ATTEMPTS):
        candidate_pdf = random.choice(pdfs)
        filename = os.path.basename(candidate_pdf)
        print(f"[Guideline] Attempt {attempt+1}: Checking {filename}")
        
        extracted_text = extract_pdf_text(candidate_pdf)
        if not extracted_text:
            continue
            
        candidate_topic = generate_topic_name(extracted_text, filename)
        if candidate_topic in processed_topics:
            print(f"[Guideline] Topic '{candidate_topic}' already processed. Retrying...")
            continue
            
        # Found completely new topic
        selected_pdf = candidate_pdf
        topic_name = candidate_topic
        pdf_text = str(extracted_text)
        break
        
    if not selected_pdf:
        print("[Guideline] Exhausted attempts. All sampled PDFs were already processed or unreadable.")
        return

    print(f"[Guideline] Selected '{topic_name}' from {os.path.basename(selected_pdf)}")

    # 4. Generate Guide Note
    prompt = (
        f"文件來源：{os.path.basename(selected_pdf)}\n"
        f"以下是擷取的部分文件內容，請依據 system_instruction 的規則為我撰寫導讀筆記：\n\n"
        f"{pdf_text[0:15000]}" # Limit context window just to be safe, ~15k chars is reasonable for quick summary
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

    # 5. Save to Obsidian
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

    # 6. Record to memory
    if topic_name:
        save_memory(topic_name)
    print(f"[Guideline] Successfully recorded '{topic_name}' to memory. Done!")


if __name__ == "__main__":
    run_daily_guideline()
