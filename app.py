import streamlit as st
import os
import requests
import json
import time
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 0. 設定與 API Keys
# ==========================================
st.set_page_config(page_title="北科大 AI 選課顧問 (Pro版)", layout="wide")

try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GOOGLE_SEARCH_API_KEY = st.secrets["GOOGLE_SEARCH_API_KEY"]
    SEARCH_ENGINE_ID = st.secrets["SEARCH_ENGINE_ID"]
except:
    GEMINI_API_KEY = None; GOOGLE_SEARCH_API_KEY = None; SEARCH_ENGINE_ID = None

if not GEMINI_API_KEY:
    with st.sidebar:
        st.warning("⚠️ 請輸入 API Keys")
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")
        GOOGLE_SEARCH_API_KEY = st.text_input("Google Search Key", type="password")
        SEARCH_ENGINE_ID = st.text_input("Search Engine ID")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 1. 模型定義
# ==========================================
MODELS = {
    "MANAGER": "models/gemini-2.5-flash",       # 大腦
    "JUDGE":   "models/gemini-2.5-flash",       # 評分
    "CLEANER": "models/gemini-2.5-flash-lite",  # 清理
    "HUNTER":  "models/gemini-2.5-flash-lite",  # 推薦
    "FIXER":   "models/gemini-2.5-flash-lite"   # 格式
}

# ==========================================
# 2. 側邊欄與狀態管理
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with st.sidebar:
    st.title("⚙️ 系統核心")
    
    # --- 動態狀態顯示區 (Placeholder) ---
    st.subheader("📡 即時運算狀態")
    status_placeholder = st.empty() # 這是一個空的容器，稍後會動態填入內容
    
    def update_sidebar_status(agent_name, model_name, status="running"):
        """動態更新側邊欄狀態"""
        with status_placeholder.container():
            if status == "running":
                st.info(f"🔄 **{agent_name}** 正在工作中...")
                st.caption(f"使用模型: `{model_name}`")
            elif status == "idle":
                st.success("✅ 系統待機中")
            elif status == "error":
                st.error("❌ 發生錯誤")

    # 預設狀態
    update_sidebar_status("System", "Ready", "idle")
    
    st.divider()
    st.caption("架構配置")
    st.text(f"Manager: 2.5 Flash")
    st.text(f"Lite Agents: 2.5 Flash-Lite")

    st.divider()
    version_option = st.radio("Tier List 版本", ("中文", "英文"), index=0)
    
    # 圖片路徑設定
    if version_option == "中文":
        BASE_IMAGE_FILENAME = "tier_list.png"
        RESULT_IMAGE_FILENAME = "final_tier_list.png"
        SESSION_KEY = "tier_counts_zh"
    else:
        BASE_IMAGE_FILENAME = "tier_list_en.png"
        RESULT_IMAGE_FILENAME = "final_tier_list_en.png"
        SESSION_KEY = "tier_counts_en"

    BASE_IMAGE_PATH = os.path.join(BASE_DIR, BASE_IMAGE_FILENAME)
    RESULT_IMAGE_PATH = os.path.join(BASE_DIR, RESULT_IMAGE_FILENAME)

    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}

    if st.button("🗑️ 清空榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.session_state.analysis_result = None
        st.success("已重置")
        st.rerun()

# ==========================================
# 3. 圖片處理 (保持不變)
# ==========================================
def load_font(size):
    paths = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "C:\\Windows\\Fonts\\msjh.ttc", "C:\\Windows\\Fonts\\simhei.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def get_fit_font(draw, text, max_width, max_height, initial_size):
    size = initial_size
    font = load_font(size)
    while size > 10: 
        try:
            l, t, r, b = draw.textbbox((0, 0), text, font=font)
            w, h = r - l, b - t
        except: w, h = draw.textlength(text, font=font), size
        if w < max_width and h < max_height: return font, h
        size -= 2
        font = load_font(size)
    return font, max_height

def create_base_tier_list_fallback():
    W, H = 1200, 1000
    img = Image.new('RGB', (W, H), (30, 30, 30))
    draw = ImageDraw.Draw(img)
    colors = {'S': '#FF7F7F', 'A': '#FFBF7F', 'B': '#FFFF7F', 'C': '#7FFF7F', 'D': '#7F7FFF'}
    row_h = H // 5
    font = load_font(60)
    for idx, (tier, color) in enumerate(colors.items()):
        y = idx * row_h
        draw.rectangle([(0, y), (200, y + row_h)], fill=color)
        draw.rectangle([(0, y), (W, y + row_h)], outline='black', width=2)
        draw.text((70, y + row_h//2 - 30), tier, fill='black', font=font)
        draw.line([(0, y+row_h), (W, y+row_h)], fill='white', width=2)
    return img

def create_course_card(full_text, size=(150, 150)):
    img = Image.new('RGBA', size, (245, 245, 245, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0), (size[0]-1, size[1]-1)], outline=(50,50,50), width=3)
    parts = full_text.split(' ')
    course_name = parts[0] if len(parts) >= 1 else full_text
    teacher_name = " ".join(parts[1:]) if len(parts) >= 2 else ""
    W, H = size
    target_w = W - 16
    
    font_c, h_c = get_fit_font(draw, course_name, target_w, H*0.5, int(H*0.4))
    try: l, t, r, b = draw.textbbox((0,0), course_name, font=font_c); w_c = r-l
    except: w_c = draw.textlength(course_name, font=font_c)
    draw.text(((W-w_c)/2, (H*0.45-h_c)/2), course_name, fill='black', font=font_c)
    
    if teacher_name:
        font_t, h_t = get_fit_font(draw, teacher_name, target_w, H*0.3, int(H*0.25))
        try: l, t, r, b = draw.textbbox((0,0), teacher_name, font=font_t); w_t = r-l
        except: w_t = draw.textlength(teacher_name, font=font_t)
        draw.text(((W-w_t)/2, (H*0.75)-(h_t/2)), teacher_name, fill='gray', font=font_t)
    return img

def update_tier_list_image(course_name, tier):
    tier = tier.upper()
    if tier not in ['S', 'A', 'B', 'C', 'D']: tier = 'C'
    
    if os.path.exists(RESULT_IMAGE_PATH): base = Image.open(RESULT_IMAGE_PATH).convert("RGBA")
    elif os.path.exists(BASE_IMAGE_PATH): base = Image.open(BASE_IMAGE_PATH).convert("RGBA")
    else: base = create_base_tier_list_fallback().convert("RGBA")
    
    W, H = base.size
    ROW_H = H // 5
    CARD_SIZE = int(ROW_H * 0.85)
    START_X = int(W * 0.28)
    PADDING = 10
    
    count = st.session_state[SESSION_KEY][tier]
    x = START_X + (count * (CARD_SIZE + PADDING))
    y = int(({'S':0,'A':1,'B':2,'C':3,'D':4}[tier] * ROW_H) + (ROW_H - CARD_SIZE)/2)
    
    if x + CARD_SIZE > W: return False
    card = create_course_card(course_name, size=(CARD_SIZE, CARD_SIZE))
    base.alpha_composite(card, (int(x), int(y)))
    base.save(RESULT_IMAGE_PATH)
    st.session_state[SESSION_KEY][tier] += 1
    return True

# ==========================================
# 4. 核心 Agent 函式
# ==========================================
def call_ai(contents, model_name):
    try:
        model = genai.GenerativeModel(model_name)
        return model.generate_content(contents).text
    except Exception as e:
        try:
            fallback = genai.GenerativeModel("models/gemini-2.0-flash")
            return fallback.generate_content(contents).text
        except: return None

def agent_manager(user_query):
    prompt = f"""
    使用者輸入：「{user_query}」
    判斷意圖並輸出 JSON：
    1. 僅「課程/類別」-> intent: "recommend"
    2. 含「老師名字」-> intent: "analyze", keywords: "老師名" (去掉評價等字)
    JSON format: {{"intent": "...", "keywords": "...", "reason": "..."}}
    """
    res = call_ai(prompt, MODELS["MANAGER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return {"intent": "recommend", "keywords": user_query}

def search_google(query, mode="analysis"):
    if not GOOGLE_SEARCH_API_KEY: return []
    q_str = f'(北科大 "{query}") OR ("{query}" Dcard PTT)' if mode == "analysis" else f'北科大 {query} 推薦 site:dcard.tw OR site:ptt.cc'
    url = "https://www.googleapis.com/customsearch/v1"
    try:
        res = requests.get(url, params={'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': q_str, 'num': 8}, timeout=10)
        data = res.json()
        return [f"[{i.get('title')}]\n{i.get('snippet')}\nLink: {i.get('link')}" for i in data.get('items', [])]
    except: return []

def agent_analyst(course_name, data):
    prompt = f"""
    分析目標：「{course_name}」。資料：{data}
    評分 0-100 並給 Tier (S/A/B/C/D)。
    JSON: {{"rank": "稱號", "tier": "S/A/B/C/D", "score": int, "reason": "短評", "tags": [], "details": "詳述"}}
    """
    return call_ai(prompt, MODELS["JUDGE"])

def agent_fixer(text):
    res = call_ai(f"Extract valid JSON:\n{text}", MODELS["FIXER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return None

# ==========================================
# 5. 主介面邏輯 (重點修改區)
# ==========================================
st.title("🎓 北科大 AI 選課顧問 (Pro版)")
st.caption("🚀 Agent Workflow + Real-time Visualization")

c1, c2 = st.columns([4, 1])
with c1: user_input = st.text_input("輸入課程/老師...", placeholder="例：微積分 羅仁傑")
with c2: btn_search = st.button("🔍 智能搜尋", use_container_width=True, type="primary")

if 'analysis_result' not in st.session_state: st.session_state.analysis_result = None

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("缺 API Key"); st.stop()
    
    st.session_state.analysis_result = None # 清空舊結果
    
    # === 流程開始 ===
    with st.status("🚀 任務啟動...", expanded=True) as status:
        
        # 1. Manager 階段
        update_sidebar_status("Manager", MODELS["MANAGER"])
        st.write("🧠 **Manager**: 正在分析您的意圖...")
        intent_data = agent_manager(user_input)
        intent = intent_data.get("intent", "recommend")
        keywords = intent_data.get("keywords", user_input)
        
        # 顯示 Manager 結果
        with st.expander(f"✅ 意圖識別: {intent.upper()}", expanded=True):
            st.json(intent_data)
        
        if intent == "analyze":
            # 2. Search 階段
            update_sidebar_status("Search Engine", "Google API")
            st.write(f"🔍 **Search**: 正在廣域搜尋「{keywords}」...")
            raw_data = search_google(keywords, mode="analysis")
            
            if not raw_data:
                status.update(label="❌ 搜尋無結果", state="error")
                update_sidebar_status("System", "Error", "error")
                st.stop()
            
            # 顯示搜尋結果
            with st.expander(f"📄 原始搜尋資料 ({len(raw_data)} 筆)", expanded=False):
                for item in raw_data:
                    st.text(item)
                    st.divider()

            # 3. Cleaner 階段
            update_sidebar_status("Cleaner", MODELS["CLEANER"])
            st.write("🧹 **Cleaner**: 正在閱讀並摘要資料...")
            curated = call_ai(f"摘要重點評價，保留外校資訊：{raw_data}", MODELS["CLEANER"])
            
            # 顯示摘要結果
            with st.expander("📝 資料摘要", expanded=False):
                st.markdown(curated)

            # 4. Analyst 階段
            update_sidebar_status("Analyst", MODELS["JUDGE"])
            st.write("⚖️ **Analyst**: 正在進行深度評分...")
            raw_res = agent_analyst(keywords, curated)
            final_data = agent_fixer(raw_res)
            
            # 顯示 JSON 結果
            with st.expander("📊 評分數據 (JSON)", expanded=False):
                st.json(final_data)
            
            if final_data:
                st.session_state.analysis_result = final_data
                
                # 5. Illustrator 階段
                update_sidebar_status("Illustrator", "Pillow (Local)")
                st.write("🎨 **Illustrator**: 正在繪製 Tier List...")
                update_tier_list_image(user_input, final_data.get('tier', 'C'))
                
                status.update(label="✅ 分析完成！", state="complete")
                update_sidebar_status("System", "Ready", "idle")
            else:
                status.update(label="❌ 分析失敗", state="error")
                update_sidebar_status("System", "Error", "error")
        else:
            # 推薦模式 (簡化版)
            update_sidebar_status("Hunter", MODELS["HUNTER"])
            st.write("🕵️ **Hunter**: 正在搜尋熱門課程...")
            raw_data = search_google(keywords, mode="recommend")
            with st.expander("📄 搜尋結果"): st.write(raw_data)
            
            res = call_ai(f"推薦3門課：{raw_data}", MODELS["HUNTER"])
            st.write(res)
            status.update(label="✅ 推薦完成", state="complete")
            update_sidebar_status("System", "Ready", "idle")

# ==========================================
# 6. 結果顯示區
# ==========================================
if st.session_state.analysis_result:
    d = st.session_state.analysis_result
    st.divider()
    
    col_res, col_img = st.columns([1.5, 2])
    
    with col_res:
        st.subheader("📝 分析報告")
        st.metric("AI 評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"### {d.get('rank')}")
        st.success(d.get('reason'))
        st.write(d.get('details'))
        st.caption("標籤：" + ", ".join(d.get('tags', [])))
        
    with col_img:
        st.subheader(f"🏆 課程排位榜 ({version_option})")
        if os.path.exists(RESULT_IMAGE_PATH):
            st.image(RESULT_IMAGE_PATH, use_column_width=True)
        else:
            st.image(BASE_IMAGE_PATH, caption="尚無資料", use_column_width=True)
