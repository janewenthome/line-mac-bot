"""
sync_gmail.py
==============
透過 IMAP 登入 Gmail，將特定寄件者的電子報自動轉換為 Markdown，
存入 Obsidian Vault 的「文章存檔/曼報pro與修修」資料夾。

支援增量更新：已存在的 .md 檔案直接跳過，不會重複下載。

使用方式：
    python3 sync_gmail.py              # 手動執行
    （app.py 內的 APScheduler 每 12 小時自動執行）
"""

import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime

from dotenv import load_dotenv
from markdownify import markdownify as md

# ── 環境設定 ─────────────────────────────────────────────────────────────────
load_dotenv()

GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
OBSIDIAN_VAULT_PATH = "/Users/wenhung/Library/Mobile Documents/iCloud~md~obsidian/Documents/Second brain"

# 寄件者 → Obsidian 存檔資料夾映射表
med_humanities_folder = os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "醫學人文反思作業")

SENDER_FOLDER_MAP = {
    "manny@manny-li.com": os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "曼報pro"),
    "shosho@shosho.tw":   os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "張修修"),
    "wcr12011@gms.tcu.edu.tw": med_humanities_folder,
    "114101120@gms.tcu.edu.tw": med_humanities_folder,
    "114101144@gms.tcu.edu.tw": med_humanities_folder,
    "114101109@gms.tcu.edu.tw": med_humanities_folder,
    "114101149@gms.tcu.edu.tw": med_humanities_folder,
    "114101127@gms.tcu.edu.tw": med_humanities_folder,
    "114101126@gms.tcu.edu.tw": med_humanities_folder,
}

# 要監聽的寄件者清單（從映射表自動生成，新增寄件者只需修改上方）
TARGET_SENDERS = list(SENDER_FOLDER_MAP.keys())

# 每封信最多擷取字元數（防止超大信件塞爆）
MAX_CONTENT_CHARS = 50_000


# ── 工具函數 ─────────────────────────────────────────────────────────────────

def decode_str(raw) -> str:
    """解碼 email header 字串（可能為 bytes 或 encoded-word）"""
    if raw is None:
        return ""
    parts = decode_header(raw)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def sanitize_filename(name: str) -> str:
    """移除檔名中不合法的特殊符號，保留中文、英數、空格、底線、連字號"""
    name = re.sub(r'[\\/:*?"<>|【】「」『』【】《》〈〉〔〕｛｝（）\[\]{}()\n\r\t]', " ", name)
    name = re.sub(r'\s+', " ", name).strip()
    return name[:80]  # 最長 80 字元，避免路徑過長


def _decode_payload(payload: bytes, charset: str) -> str:
    """
    嘗試多種編碼解碼 bytes payload，確保不因編碼標記錯誤而回傳空字串。
    優先序：charset → utf-8 → big5 → latin-1（最後保底，errors=ignore）
    """
    candidates = [charset, "utf-8", "big5", "gb2312", "latin-1"]
    seen = set()
    for enc in candidates:
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return payload.decode(enc, errors="ignore")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1", errors="ignore")


def clean_html(html: str):
    """
    使用 BeautifulSoup 清洗 HTML，回傳 soup 物件供後續彈性使用：
    1. 移除 <style>、<script>、<head>（防止 CSS/JS 代碼滲入 Markdown）
    2. 移除含 'fluentcrm' 的所有標籤
    3. 移除 1×1 tracking pixel <img>
    （不再執行 unwrap，改由 markdownify 的 strip= 參數原生處理排版標籤）
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # ① 移除 <style>、<script>、<head>，防止 CSS/JS 代碼滲入
    for tag in soup(["style", "script", "head"]):
        tag.extract()

    # ② 移除含 fluentcrm 字樣的任何標籤
    for tag in soup.find_all(True):
        if "fluentcrm" in str(tag).lower():
            tag.decompose()

    # ③ 移除 1×1 tracking pixel img
    for img in soup.find_all("img"):
        width  = img.get("width",  "")
        height = img.get("height", "")
        style  = img.get("style",  "")
        src    = img.get("src",    "")
        try:
            is_tiny = (str(width) in ("1", "0") or str(height) in ("1", "0")
                       or "width:0" in style or "height:0" in style
                       or "display:none" in style.replace(" ", ""))
        except Exception:
            is_tiny = False
        if is_tiny or "open" in src.lower() or "track" in src.lower():
            img.decompose()

    return soup  # 回傳 soup 物件，方便呼叫端同時使用 str(soup) 和 soup.get_text()


def extract_text_from_message(msg) -> str:
    """
    強健版 MIME 解析（列表收集 + 挑選最長區塊）：
    - 用 walk() 走訪所有 part，收集所有 text/html 與 text/plain 區塊
    - 用 max(key=len) 挑出最長的區塊，防止空白 tracking pixel 區塊覆寫正文
    - errors='replace' 確保文字不整段遺失
    """
    html_parts  = []   # 收集所有 text/html 區塊
    plain_parts = []   # 收集所有 text/plain 區塊

    for part in msg.walk():
        ct = part.get_content_type()
        cd = str(part.get("Content-Disposition", ""))

        # 跳過附件
        if "attachment" in cd:
            continue
        # 只處理 text/* 類型
        if not ct.startswith("text/"):
            continue

        payload = part.get_payload(decode=True)
        if not payload:
            continue

        # 動態精準解碼：使用 replace 確保文字不整段遺失
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            # charset 標記錯誤：嘗試常見繁體中文編碼
            for enc in ("utf-8", "big5", "gb2312", "latin-1"):
                try:
                    text = payload.decode(enc, errors="replace")
                    break
                except Exception:
                    continue
            else:
                text = payload.decode("latin-1", errors="replace")

        # 用 append 收集，絕不用 = 覆寫
        if ct == "text/html":
            html_parts.append(text)
        elif ct == "text/plain":
            plain_parts.append(text)

    # 挑選最長區塊（防止空白 tracking pixel 覆寫正文）
    html_content  = max(html_parts,  key=len, default="")
    plain_content = max(plain_parts, key=len, default="")

    # —— 無敋決策樹 A → B → C ——
    if html_content:
        soup = clean_html(html_content)

        # 【步驟 A】markdownify 原生 strip= 剝除排版標籤
        STRIP_TAGS = ["table", "thead", "tbody", "tfoot", "tr", "td", "th",
                      "div", "span", "script", "style", "head"]
        result = md(str(soup), heading_style="ATX", bullets="-", strip=STRIP_TAGS)
        result = re.sub(r'\n{3,}', '\n\n', result).strip()

        # 【步驟 B】markdownify 結果過短 → get_text() 磬抓
        if len(result.replace(" ", "").replace("\n", "")) < 50:
            print("[Gmail] ⚠️  步驟 A 結果過短，啟動步驟 B get_text() 保底")
            result = soup.get_text(separator='\n\n', strip=True)
            result = re.sub(r'\n{3,}', '\n\n', result).strip()

        # 【步驟 C】HTML 彼彼無效 → 強制捨棄、直接用 plain_content
        if len(result.replace(" ", "").replace("\n", "")) < 50 and plain_content.strip():
            print("[Gmail] ⚠️  步驟 B 依然過短，啟動步驟 C 強制醫用 plain_content")
            result = plain_content.strip()

        if result.strip():
            return result[:MAX_CONTENT_CHARS]

    # 最後備案：純文字
    if plain_content.strip():
        print("[Gmail] ⚠️  HTML 完全無效，發動 plain_content 備案")
        return plain_content.strip()[:MAX_CONTENT_CHARS]

    return ""


def parse_date(date_str: str) -> str:
    """將 email Date header 解析為 YYYY-MM-DD 格式（解析失敗回傳今日日期）"""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def sync_gmail():
    print(f"[Gmail] 開始同步收信，帳號：{GMAIL_USER}")

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("[Gmail] ⚠️  請在 .env 中設定 GMAIL_USER 與 GMAIL_APP_PASSWORD")
        return

    # 登入 IMAP
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        print("[Gmail] ✅ 登入成功")
    except Exception as e:
        print(f"[Gmail] ❌ 登入失敗：{e}")
        return

    saved = 0
    skipped = 0

    try:
        imap.select("INBOX")

        for sender in TARGET_SENDERS:
            print(f"\n[Gmail] 搜尋寄件者：{sender}")

            # 取得該寄件者對應的存檔資料夾
            save_dir = SENDER_FOLDER_MAP.get(
                sender,
                os.path.join(OBSIDIAN_VAULT_PATH, "文章存檔", "其他電子報")
            )
            os.makedirs(save_dir, exist_ok=True)
            # 搜尋所有來自該寄件者的信件（含已讀）以及寄給該信箱的信件（實現雙向同步）
            # 使用 X-GM-RAW 進行精確的 Gmail 語法搜尋
            status, data = imap.search(None, "X-GM-RAW", f'"from:{sender} OR to:{sender}"')
            if status != "OK" or not data[0]:
                print(f"[Gmail] 找不到關聯 {sender} 的信件")
                continue

            mail_ids = data[0].split()
            print(f"[Gmail] 找到 {len(mail_ids)} 封信")

            for mail_id in mail_ids:
                try:
                    status, msg_data = imap.fetch(mail_id, "(RFC822)")
                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # 解析日期與主旨
                    date_str = parse_date(msg.get("Date", ""))
                    subject  = decode_str(msg.get("Subject", "（無主旨）"))
                    safe_subject = sanitize_filename(subject)

                    # 組合檔名
                    year_str = date_str[:4]
                    month_str = date_str[5:7]
                    target_dir = os.path.join(save_dir, year_str, month_str)
                    os.makedirs(target_dir, exist_ok=True)
                    
                    filename = f"{date_str}_{safe_subject}.md"
                    filepath = os.path.join(target_dir, filename)

                    # 防呆：已存在就跳過
                    if os.path.exists(filepath):
                        skipped += 1
                        continue

                    # 萃取正文：依寄件者分流（雙通道最佳化）
                    from bs4 import BeautifulSoup

                    # ── 先取得最長 HTML 區塊（兩條通道共用）──
                    _html_best  = ""
                    _plain_best = ""
                    for _part in msg.walk():
                        _ct = _part.get_content_type()
                        _cd = str(_part.get("Content-Disposition", ""))
                        if "attachment" in _cd or not _ct.startswith("text/"):
                            continue
                        _raw = _part.get_payload(decode=True)
                        if not _raw:
                            continue
                        _cs = _part.get_content_charset() or "utf-8"
                        try:
                            _dec = _raw.decode(_cs, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            _dec = _raw.decode("latin-1", errors="replace")
                        if _ct == "text/html" and len(_dec) > len(_html_best):
                            _html_best = _dec
                        elif _ct == "text/plain" and len(_dec) > len(_plain_best):
                            _plain_best = _dec

                    if "manny" in sender:
                        # ══ 曼報專屬通道：黃金排版粉碎機 + markdownify ══
                        if _html_best:
                            msoup = BeautifulSoup(_html_best, "html.parser")
                            # 步驟一：清除雜訊
                            for s in msoup(["style", "script", "head", "meta"]):
                                s.extract()
                            # 步驟二：unwrap 排版框，完好保留 <img> <a> <p>
                            for tag in list(msoup(["table", "thead", "tbody",
                                                   "tr", "td", "th",
                                                   "div", "span"])):
                                try:
                                    tag.unwrap()
                                except Exception:
                                    pass
                            # 步驟三：交給 markdownify
                            content = md(str(msoup), heading_style="ATX")
                            content = re.sub(r'\n{3,}', '\n\n', content).strip()
                        else:
                            content = _plain_best.strip()

                    elif "shosho" in sender:
                        # ══ 張修修專屬通道：暴力純文字，完全不碰 markdownify ══
                        if _html_best:
                            ssoup = BeautifulSoup(_html_best, "html.parser")
                            # 清除雜訊
                            for s in ssoup(["style", "script", "head"]):
                                s.extract()
                            # 暴力萃取純文字
                            content = ssoup.get_text(separator='\n\n', strip=True)
                            content = re.sub(r'\n{3,}', '\n\n', content).strip()
                        else:
                            content = _plain_best.strip()

                    else:
                        # 其他寄件者：走通用穩定解析通道
                        content = extract_text_from_message(msg)

                    if not content.strip():
                        print(f"[Gmail] ⚠️ 偵測異常 - 略過：{subject}")
                        continue

                    # ── 擷取並儲存附件 ──
                    attachments_refs = []
                    for _part in msg.walk():
                        if _part.get_content_maintype() == 'multipart':
                            continue
                        
                        # 過濾掉純文字區塊（避免將正文誤認）
                        if _part.get_content_type().startswith("text/"):
                            continue

                        _filename = _part.get_filename()
                        if not _filename:
                            continue

                        raw_name = decode_str(_filename)
                        safe_name = sanitize_filename(raw_name)
                        
                        # 加上日期前綴防止同名覆蓋，並保留副檔名
                        safe_name = f"{date_str}_{safe_name}"
                        attach_path = os.path.join(target_dir, safe_name)
                        
                        try:
                            payload = _part.get_payload(decode=True)
                            if payload:
                                with open(attach_path, "wb") as af:
                                    af.write(payload)
                                attachments_refs.append(f"![[{safe_name}]]")
                                print(f"[Gmail] 📎 下載附件：{safe_name}")
                        except Exception as e:
                            print(f"[Gmail] ⚠️ 附件下載失敗 {safe_name}: {e}")

                    if attachments_refs:
                        content += "\n\n## 📎 附件檔案\n" + "\n".join(attachments_refs)

                    # 寫入 Markdown（含 frontmatter）
                    md_content = (
                        f"---\n"
                        f"title: \"{subject}\"\n"
                        f"date: {date_str}\n"
                        f"from: {sender}\n"
                        f"tags: [電子報, 信箱存檔]\n"
                        f"---\n\n"
                        f"# {subject}\n\n"
                        f"{content}\n"
                    )
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(md_content)

                    saved += 1
                    print(f"[Gmail] ➕ 存檔：{filename}")

                except Exception as e:
                    print(f"[Gmail] 處理信件失敗（id={mail_id}）：{e}")

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    print(f"\n[Gmail] 完成！新增 {saved} 封 | 跳過（已存在） {skipped} 封")
    for sender, folder in SENDER_FOLDER_MAP.items():
        print(f"[Gmail]   {sender} → {folder}")


if __name__ == "__main__":
    sync_gmail()
