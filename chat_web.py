"""
chat_web.py — 🧠 Obsidian 超級大腦（三大引擎 Streamlit 版）
============================================================
啟動方式：
    cd ~/line-mac-bot
    .venv/bin/streamlit run chat_web.py

🔁 前提：請先執行 build_vector_db.py 建立向量資料庫。

三大引擎：
  Tab 1  🔍 資料引擎 — 快速向量 + 關鍵字混合搜尋
  Tab 2  🧠 思考引擎 — 自動反思分析，存入 [文章存檔/1. 反思]
  Tab 3  ✍️  產出引擎 — 自動生成演講稿，存入 [文章存檔/2. 演講稿]
"""

import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Optional

# ====== Streamlit 避震器 ======
os.environ["CHROMA_TELEMETRY"]    = "False"
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import chromadb
from chromadb.utils import embedding_functions
from openai import OpenAI
import streamlit as st
from dotenv import load_dotenv

# ── 環境設定 ──────────────────────────────────────────────────────────────────
load_dotenv()

OPENROUTER_API_KEY  = os.environ.get("OPENROUTER_API_KEY", "")
OBSIDIAN_VAULT_PATH = (
    "/Users/wenhung/Library/Mobile Documents/"
    "iCloud~md~obsidian/Documents/Second brain"
)
BASE_DIR    = os.path.expanduser("~/line-mac-bot")
CHROMA_PATH = "/Volumes/2TB/program/vector"
MAX_HISTORY = 10

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

# 混合搜尋：需要關鍵字加成的詞彙
KEYWORD_BOOST_TERMS = [
    "健保碼", "健保", "保險", "ICD", "NHI", "診斷碼",
    "給付", "申報", "核刪", "費用", "自費",
]

# ── 頁面設定 ──────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="🧠 Obsidian 超級大腦",
    page_icon="🧠",
    layout="wide",
)

# ── 自訂 CSS（Apple 極簡白底風格）────────────────────────────────────────────
st.markdown("""
<style>
/* Google Font */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans TC', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* ── 主背景：蘋果淺灰 ─────────────────────────────────────── */
.stApp {
    background-color: #f5f5f7;
    color: #1d1d1f;
}

/* ── 主標題區塊：無色底、置中俐落 ────────────────────────── */
.main-hero {
    text-align: center;
    padding: 2.5rem 1rem 1.5rem;
    background: transparent;
    border: none;
    margin-bottom: 1rem;
}
.main-hero h1 {
    font-size: 2.2rem;
    font-weight: 700;
    color: #1d1d1f;
    letter-spacing: -0.02em;
    margin-bottom: 0.4rem;
}
.main-hero p {
    color: #6e6e73;
    font-size: 1rem;
    font-weight: 400;
}

/* ── Tab：細緻底線風格 ────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    background: transparent;
    border-bottom: 1px solid #d2d2d7;
    border-radius: 0;
    padding: 0;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 0;
    font-size: 0.95rem;
    font-weight: 500;
    padding: 0.7rem 1.4rem;
    color: #6e6e73;
    background: transparent;
    border-bottom: 2px solid transparent;
    transition: color 0.2s, border-color 0.2s;
}
.stTabs [data-baseweb="tab"]:hover {
    color: #1d1d1f;
}
.stTabs [aria-selected="true"] {
    background: transparent !important;
    color: #0071e3 !important;
    border-bottom: 2px solid #0071e3 !important;
}

/* ── 引擎說明卡片：白底輕陰影 ────────────────────────────── */
.engine-desc {
    padding: 0.9rem 1.2rem;
    border-radius: 10px;
    margin: 0.8rem 0 1.2rem;
    font-size: 0.88rem;
    color: #515154;
    line-height: 1.7;
    background: #ffffff;
    border-left: 3px solid;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.engine-desc-blue   { border-color: #0071e3; }
.engine-desc-violet { border-color: #5856d6; }
.engine-desc-pink   { border-color: #ff375f; }

/* 按鈕 (Apple 質感美化) */
.stButton > button {
    background-color: #ffffff;
    color: #1d1d1f;
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    padding: 0.5rem 1rem;
    font-size: 0.95rem;
    font-weight: 500;
    width: 100%;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    transition: all 0.2s cubic-bezier(0.25, 0.1, 0.25, 1);
}
.stButton > button:hover {
    background-color: #f5f5f7;
    border-color: #86868b;
    color: #0071e3;
    transform: translateY(-1px);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.06);
}
.stButton > button:active {
    transform: translateY(0);
    background-color: #e8e8ed;
}

/* ── 輸入框：白底細框 ─────────────────────────────────────── */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea {
    background: #ffffff !important;
    border: 1px solid #d2d2d7 !important;
    border-radius: 10px !important;
    color: #1d1d1f !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05) !important;
    font-size: 0.95rem !important;
}
.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: #0071e3 !important;
    box-shadow: 0 0 0 3px rgba(0,113,227,0.15) !important;
    outline: none !important;
}

/* Selectbox */
.stSelectbox > div > div {
    background: #ffffff !important;
    border: 1px solid #d2d2d7 !important;
    border-radius: 10px !important;
    color: #1d1d1f !important;
}

/* ── 結果卡片：浮動白卡 ─────────────────────────────────── */
.result-card {
    background: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 16px;
    padding: 1.6rem 2rem;
    margin-top: 1.2rem;
    box-shadow: 0 2px 12px rgba(0,0,0,0.07);
    color: #1d1d1f;
    line-height: 1.75;
}

/* ── Sidebar：極淺灰 ───────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background-color: #fbfbfd;
    border-right: 1px solid #d2d2d7;
}
section[data-testid="stSidebar"] * {
    color: #1d1d1f !important;
}

/* ── Metric ─────────────────────────────────────────────────── */
[data-testid="metric-container"] {
    background: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 12px;
    padding: 0.8rem 1rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* ── Chat 訊息泡泡 ──────────────────────────────────────────── */
[data-testid="stChatMessage"] {
    background: #ffffff;
    border: 1px solid #e5e5ea;
    border-radius: 14px;
    padding: 0.8rem 1.2rem;
    margin-bottom: 0.6rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
}

/* ── Expander ─────────────────────────────────────────────── */
details {
    background: #ffffff;
    border: 1px solid #e5e5ea !important;
    border-radius: 10px !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session State 初始化 ──────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []


# ════════════════════════════════════════════════════════════════════════════════
# ── 工具函數 ──────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════

def _load_persona() -> str:
    """讀取 Persona.md 完整內容"""
    persona_path = os.path.join(OBSIDIAN_VAULT_PATH, "Persona.md")
    if not os.path.isfile(persona_path):
        for root, _, files in os.walk(OBSIDIAN_VAULT_PATH):
            if "Persona.md" in files:
                persona_path = os.path.join(root, "Persona.md")
                break
    try:
        with open(persona_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def get_collection_count() -> int:
    if not os.path.isdir(CHROMA_PATH):
        return 0
    try:
        db = chromadb.PersistentClient(path=CHROMA_PATH)
        col = db.get_or_create_collection("obsidian_notes")
        return col.count()
    except Exception:
        return 0


# _embed_query 移除，改由 ChromaDB 原生處理


def _date_cutoff_str(range_label: str) -> Optional[str]:
    """將下拉選單的時間範圍轉成最早日期字串（YYYY-MM-DD），None 代表全部"""
    today = datetime.now()
    if "一個月" in range_label:
        return (today - timedelta(days=30)).strftime("%Y-%m-%d")
    elif "三個月" in range_label:
        return (today - timedelta(days=90)).strftime("%Y-%m-%d")
    elif "半年" in range_label:
        return (today - timedelta(days=180)).strftime("%Y-%m-%d")
    return None  # 全部


def _has_keyword_boost(query: str) -> bool:
    """判斷查詢是否含有應加強關鍵字比對的詞彙"""
    return any(kw in query for kw in KEYWORD_BOOST_TERMS)


def hybrid_search(
    query: str,
    n_results: int = 8,
    date_cutoff: Optional[str] = None,
    prioritize_daily: bool = False,
) -> tuple:
    """
    混合搜尋：向量語意搜尋 + 關鍵字權重加成。
    - date_cutoff：只回傳 note_date >= 此日期的文件（None = 全部）
    - prioritize_daily：將 is_daily_note=true 的結果排到最前面
    回傳 (context_text, source_list)
    """
    parts   = []
    sources = []

    # 1. 載入 Persona
    persona = _load_persona()
    if persona:
        parts.append(f"### 📄 Persona.md（核心個人檔案）\n{persona}")

    if not os.path.isdir(CHROMA_PATH):
        return "\n\n---\n\n".join(parts), sources

    try:
        db  = chromadb.PersistentClient(path=CHROMA_PATH)
        emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="paraphrase-multilingual-MiniLM-L12-v2")
        col = db.get_or_create_collection("obsidian_notes", embedding_function=emb_fn)

        # 建立 ChromaDB where 過濾條件（日期）
        where_filter = None
        if date_cutoff:
            where_filter = {"note_date": {"$gte": date_cutoff}}

        results = col.query(
            query_texts=[query],
            n_results=min(n_results, col.count() or 1),
            include=["documents", "metadatas"],
            where=where_filter if where_filter else None,
        )
        docs  = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        # ── 關鍵字加成（模擬 Hybrid Rerank）──────────────────────────────────
        scored = []
        use_boost = _has_keyword_boost(query)
        query_terms = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())

        for doc, meta in zip(docs, metas):
            score = 1.0  # base score
            if use_boost:
                doc_lower = doc.lower()
                for term in query_terms:
                    if term in doc_lower:
                        score += 0.5  # 每個命中關鍵字加 0.5
            scored.append((score, doc, meta))

        # ── 排序：is_daily_note 優先 → 再按 score 降序 ──────────────────────
        def sort_key(item):
            s, _, m = item
            is_daily = m.get("is_daily_note", "false") == "true"
            daily_bonus = 10.0 if (prioritize_daily and is_daily) else 0.0
            return -(s + daily_bonus)

        scored.sort(key=sort_key)

        for _, doc, meta in scored:
            src       = meta.get("source", "未知來源")
            note_date = meta.get("note_date", "")
            mtime_str = meta.get("mtime", "")
            is_daily  = meta.get("is_daily_note", "false") == "true"
            daily_tag = " 📥" if is_daily else ""
            date_tag  = f"（{note_date}）" if note_date else (f"（{mtime_str}）" if mtime_str else "")
            parts.append(f"### 📄 {src}{daily_tag}{date_tag}\n{doc}")
            sources.append(src)

    except Exception as e:
        st.warning(f"ChromaDB 搜尋失敗，略過向量搜尋：{e}")

    return "\n\n---\n\n".join(parts), sources


def _call_gemini(prompt: str, model: str = "google/gemini-2.5-flash-lite") -> str:
    """呼叫 OpenRouter，最多重試 3 次"""
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(10)
            else:
                return f"⚠️ 生成失敗：{e}"
    return ""


def _save_to_obsidian(content: str, folder: str) -> str:
    """
    將 content 存入 OBSIDIAN_VAULT_PATH/folder/時間戳記.md。
    回傳存檔路徑或錯誤訊息。
    """
    try:
        target_dir = os.path.join(OBSIDIAN_VAULT_PATH, folder)
        os.makedirs(target_dir, exist_ok=True)
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fpath = os.path.join(target_dir, f"{ts}.md")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        return fpath
    except Exception as e:
        return f"ERROR:{e}"


def rebuild_index():
    """觸發重新建立向量索引"""
    import subprocess
    with st.spinner("🔄 正在重建向量索引..."):
        result = subprocess.run(
            [f"{BASE_DIR}/.venv/bin/python",
             os.path.join(BASE_DIR, "build_vector_db.py")],
            capture_output=True, text=True, timeout=600,
        )
    if result.returncode == 0:
        st.success("✅ 向量索引重建完成！")
    else:
        st.error(f"❌ 重建失敗：\n{result.stderr[-500:]}")


# ════════════════════════════════════════════════════════════════════════════════
# ── Sidebar ──────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## ⚙️ 控制台")
    st.divider()
    count = get_collection_count()
    st.metric("📚 向量庫 Chunk 數", count if count > 0 else "尚未建立")
    if count == 0:
        st.warning("請先執行 `build_vector_db.py` 建立向量庫。")
    st.divider()
    if st.button("🔄 重新索引 (Re-build Index)", use_container_width=True):
        rebuild_index()
        st.rerun()
    st.divider()
    if st.button("🗑️ 清除對話紀錄", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    st.divider()
    st.caption("Gemini 2.5 Flash × ChromaDB\nObsidian Vault 三大引擎")


# ════════════════════════════════════════════════════════════════════════════════
# ── 主畫面 ────────────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="main-hero">
  <h1>🧠 我的 Obsidian 超級大腦</h1>
  <p>三大引擎：資料搜尋 · 深度反思 · 內容產出</p>
</div>
""", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs([
    "🔍 資料引擎",
    "🧠 思考引擎 (反思)",
    "✍️ 產出引擎 (生成內容)",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1：資料引擎
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown("### 🔍 資料引擎")
    st.markdown("""
<div class="engine-desc engine-desc-blue">
專門<b>快速搜尋</b>健保規範、特定檔案內容（PDF / Excel）或個人紀錄。<br>
支援關鍵字加成：輸入「健保碼」、「保險」等詞彙時，系統會自動強化精準比對。
</div>
""", unsafe_allow_html=True)

    # 歷史對話顯示
    for msg_idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("sources"):
                with st.expander("📎 參考來源", expanded=False):
                    unique_srcs = list(dict.fromkeys(msg["sources"]))
                    for idx, s in enumerate(unique_srcs):
                        col1, col2 = st.columns([5, 1])
                        col1.caption(f"• {s}")
                        if col2.button("打開", key=f"open_hist_{msg_idx}_{idx}"):
                            abs_path = os.path.join(OBSIDIAN_VAULT_PATH, s)
                            subprocess.Popen(["open", abs_path])

    # 聊天輸入
    if user_input := st.chat_input("🔍 輸入想搜尋的內容...", key="data_engine_input"):
        with st.chat_message("user"):
            st.markdown(user_input)
        st.session_state.messages.append({"role": "user", "content": user_input, "sources": []})

        with st.chat_message("assistant"):
            with st.spinner("🔍 混合搜尋中（向量 + 關鍵字）..."):
                ctx, srcs = hybrid_search(user_input, n_results=8)

            with st.spinner("✍️ 資料終端查詢中..."):
                prompt = (
                    "【最高指令：絕對簡潔】\n"
                    "你是一個無感情的資料檢索終端。請根據以下 Obsidian 筆記，直接給出最終答案。\n"
                    "嚴格禁止：任何稱呼、開場白（如『根據資料...』、『我發現...』）、分析過程、或結語。\n"
                    "如果資料中找不到，只能輸出這13個字：『在現有資料庫中未找到相關資訊。』\n\n"
                    f"【資料庫內容】\n{ctx}\n\n"
                    f"【查詢問題】\n{user_input}"
                )
                reply = _call_gemini(prompt)

            st.markdown(reply)
            if srcs:
                new_msg_idx = len(st.session_state.messages)  # 此訊息尚未加入 state
                with st.expander("📎 參考來源", expanded=False):
                    unique_srcs = list(dict.fromkeys(srcs))
                    for idx, s in enumerate(unique_srcs):
                        col1, col2 = st.columns([5, 1])
                        col1.caption(f"• {s}")
                        if col2.button("打開", key=f"open_new_{new_msg_idx}_{idx}"):
                            abs_path = os.path.join(OBSIDIAN_VAULT_PATH, s)
                            subprocess.Popen(["open", abs_path])

        st.session_state.messages.append({
            "role": "assistant", "content": reply, "sources": srcs
        })


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2：思考引擎
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown("### 🧠 思考引擎（深度反思）")
    st.markdown("""
<div class="engine-desc engine-desc-violet">
統合你的思考歷程，以<b>第三人稱智者視角</b>，分析你的關注點與想做的事，並依時間軸整理。<br>
結果將直接存入 Obsidian 的 <code>文章存檔/1. 反思</code>。
</div>
""", unsafe_allow_html=True)

    col_a, col_b = st.columns([1, 2])

    with col_a:
        reflect_range = st.selectbox(
            "📅 檢索範圍",
            ["近一個月", "近三個月", "近半年", "全部"],
            key="reflect_range",
            help="限制分析的筆記時間範圍",
        )

    with col_b:
        reflect_topic = st.text_input(
            "🎯 關注主題",
            placeholder="例如：人際關係、職涯發展、健康習慣、親子教育…",
            key="reflect_topic",
        )

    st.markdown("&nbsp;")

    if st.button("🧠 啟動思考引擎，生成反思分析", key="reflect_btn"):
        if not reflect_topic.strip():
            st.warning("請輸入關注主題後再啟動。")
        else:
            cutoff = _date_cutoff_str(reflect_range)
            with st.spinner(f"📚 擴大調閱（Top-50），按時間軸整理「{reflect_topic}」相關筆記..."):
                ctx, srcs = hybrid_search(
                    query=reflect_topic,
                    n_results=50,
                    date_cutoff=cutoff,
                    prioritize_daily=True,
                )

            with st.spinner("🧠 Gemini 以第三人稱智者視角深度分析中（請稍候 30~60 秒）..."):
                date_desc = f"時間範圍：{reflect_range}" if reflect_range != "全部" else "時間範圍：全部"
                prompt = (
                    "你現在是一位睿智的第三人稱敘事者，同時也是作者最私密的思考夥伴。\n"
                    "請閱讀以下來自 Obsidian 筆記的原始素材（以 📥 LINE每日碎語為主），\n"
                    "針對主題進行深度反思分析，輸出完整的 Markdown 文章，包含：\n\n"
                    "1. **時間軸總覽**：按日期梳理核心思考脈絡變化。\n"
                    "2. **深層洞察**：以第三人稱點出作者真正在乎的事與潛在渴望。\n"
                    "3. **關鍵矛盾與成長點**：找出思考中的矛盾與值得深挖的議題。\n"
                    "4. **行動建議**：具體可執行的下一步清單（3~5 項）。\n"
                    "5. **結語**：以溫暖且有力的第三人稱視角總結。\n\n"
                    f"【主題】{reflect_topic}\n"
                    f"【{date_desc}】\n\n"
                    f"【筆記素材（LINE每日碎語優先）】\n{ctx}"
                )
                result_md = _call_gemini(prompt, model="gemini-2.5-flash")

            # 存入 Obsidian
            save_path = _save_to_obsidian(
                content=f"# 反思：{reflect_topic}\n> {date_desc}\n\n{result_md}",
                folder="文章存檔/1. 反思",
            )

            if save_path.startswith("ERROR:"):
                st.error(f"❌ 存檔失敗：{save_path}")
            else:
                st.success(f"✅ 已成功生成並存入 Obsidian！\n\n📁 `{os.path.basename(save_path)}`")

            st.markdown('<div class="result-card">', unsafe_allow_html=True)
            st.markdown(result_md)
            st.markdown('</div>', unsafe_allow_html=True)

            if srcs:
                with st.expander(f"📎 參考了 {len(set(srcs))} 篇筆記", expanded=False):
                    unique_srcs = list(dict.fromkeys(srcs))
                    for idx, s in enumerate(unique_srcs):
                        col1, col2 = st.columns([5, 1])
                        col1.caption(f"• {s}")
                        if col2.button("打開", key=f"open_reflect_{idx}_{s[:20]}"):
                            abs_path = os.path.join(OBSIDIAN_VAULT_PATH, s)
                            subprocess.Popen(["open", abs_path])



# ════════════════════════════════════════════════════════════════════════════════
# TAB 3：產出引擎
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("### ✍️ 產出引擎（生成內容）")
    st.markdown("""
<div class="engine-desc engine-desc-pink">
根據現有筆記（以 <b>LINE 每日碎語</b>優先），自動為您撰寫分享內容。<br>
結果將直接存入 Obsidian 的 <code>文章存檔/2. 演講稿</code>。
</div>
""", unsafe_allow_html=True)

    col_x, col_y, col_z = st.columns([1, 2, 1])

    with col_x:
        output_range = st.selectbox(
            "📅 檢索範圍",
            ["近三個月", "近半年", "全部"],
            key="output_range",
            help="限制素材的時間範圍",
        )

    with col_y:
        output_topic = st.text_input(
            "🎯 核心素材（主題）",
            placeholder="例如：時間管理、番茄鐘、巡迴醫療、親子溝通…",
            key="output_topic",
        )

    with col_z:
        output_type = st.selectbox(
            "📝 產出目標",
            ["10 分鐘演講稿", "20 分鐘演講稿", "網誌文章", "短篇分享（3 分鐘）", "社群貼文"],
            key="output_type",
        )

    st.markdown("&nbsp;")

    if st.button("✍️ 啟動產出引擎，生成內容", key="output_btn"):
        if not output_topic.strip():
            st.warning("請輸入核心素材主題後再啟動。")
        else:
            cutoff = _date_cutoff_str(output_range)
            with st.spinner(f"📚 擴大調閱（Top-50），從筆記中蒐集「{output_topic}」素材..."):
                ctx, srcs = hybrid_search(
                    query=output_topic,
                    n_results=50,
                    date_cutoff=cutoff,
                    prioritize_daily=True,
                )

            format_guide = {
                "10 分鐘演講稿": (
                    "請撰寫一篇適合 10 分鐘演講的稿件（約 1500~2000 字）。\n"
                    "包含：開場故事、三個核心論點（各含小標題）、結語與行動呼籲。"
                ),
                "20 分鐘演講稿": (
                    "請撰寫一篇適合 20 分鐘演講的稿件（約 3000~4000 字）。\n"
                    "包含：開場故事、五個核心論點（各含小標題）、互動提問、結語與行動呼籲。"
                ),
                "網誌文章": (
                    "請撰寫一篇適合發布在個人網誌的文章（約 1200~1800 字）。\n"
                    "需有吸引人的標題、文體流暢、善用小段落，結尾引導讀者思考或留言。"
                ),
                "短篇分享（3 分鐘）": (
                    "請撰寫一篇適合 3 分鐘站台分享的稿件（約 400~600 字）。\n"
                    "重點突出、節奏明快，開頭一句話就要抓住注意力。"
                ),
                "社群貼文": (
                    "請撰寫一篇適合 Facebook / Instagram 的社群貼文（約 150~300 字）。\n"
                    "語氣親切自然、包含 2~3 個相關 #hashtag，結尾有互動引導。"
                ),
            }.get(output_type, "請撰寫相關內容。")

            with st.spinner(f"✍️ Gemini 正在生成「{output_topic}」的{output_type}（請稍候 30~60 秒）..."):
                date_desc = f"時間範圍：{output_range}"
                prompt = (
                    f"你是一位擁有豐富實務經驗的內容創作者。\n"
                    f"請以下方的 Obsidian 個人筆記（以 📥 LINE每日碎語優先）為核心素材，\n"
                    f"圍繞主題「{output_topic}」，創作『{output_type}』。\n\n"
                    f"{format_guide}\n\n"
                    "【重要】請盡量使用筆記中的真實經歷、觀察和洞見，讓內容有血有肉。\n"
                    "輸出格式：Markdown（使用適當的標題與段落）。\n\n"
                    f"【{date_desc}】\n\n"
                    f"【筆記素材（LINE每日碎語優先）】\n{ctx}"
                )
                result_md = _call_gemini(prompt, model="gemini-2.5-flash")

            # 存入 Obsidian
            save_path = _save_to_obsidian(
                content=f"# {output_type}：{output_topic}\n> {date_desc}\n\n{result_md}",
                folder="文章存檔/2. 演講稿",
            )

            if save_path.startswith("ERROR:"):
                st.error(f"❌ 存檔失敗：{save_path}")
            else:
                st.success(f"✅ 已成功為您生成筆記並存入 Obsidian！\n\n📁 `{os.path.basename(save_path)}`")

            st.markdown('<div class="result-card">', unsafe_allow_html=True)
            st.markdown(result_md)
            st.markdown('</div>', unsafe_allow_html=True)

            if srcs:
                with st.expander(f"📎 參考了 {len(set(srcs))} 篇筆記", expanded=False):
                    unique_srcs = list(dict.fromkeys(srcs))
                    for idx, s in enumerate(unique_srcs):
                        col1, col2 = st.columns([5, 1])
                        col1.caption(f"• {s}")
                        if col2.button("打開", key=f"open_output_{idx}_{s[:20]}"):
                            abs_path = os.path.join(OBSIDIAN_VAULT_PATH, s)
                            subprocess.Popen(["open", abs_path])

