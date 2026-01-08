import streamlit as st
import os
import requests
import json
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 0. 設定與 API Keys
# ==========================================
st.set_page_config(page_title="北科大 AI 選課顧問 (完整版)", layout="wide")

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
# 1. 模型定義 (策略分流)
# ==========================================
MODELS = {
    "MANAGER": "models/gemini-2.5-flash",       # 大腦 (意圖判斷)
    "JUDGE":   "models/gemini-2.5-flash",       # 評分 (高智商)
    "CLEANER": "models/gemini-2.5-flash-lite",  # 清理 (快)
    "HUNTER":  "models/gemini-2.5-flash-lite",  # 推薦 (快)
    "FIXER":   "models/gemini-2.5-flash-lite"   # 格式 (快)
}

# ==========================================
# 2. 側邊欄與狀態設定 (還原舊版功能)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 初始化 Session State
if 'analysis_result' not in st.session_state: st.session_state.analysis_result = None
if 'debug_raw_data' not in st.session_state: st.session_state.debug_raw_data = None
if 'debug_curated' not in st.session_state: st.session_state.debug_curated = None

with st.sidebar:
    st.header("⚙️ 系統設定")
    
    # 模型狀態顯示
    st.success(f"🧠 主力模型: Gemini 2.5 Flash")
    st.info(f"⚡ 輕量任務: Gemini 2.5 Flash-Lite")
    
    st.divider()
    
    # 版本選擇 (還原功能)
    version_option = st.radio("選擇 Tier List 版本", ("中文", "英文"), index=0)

    if version_option == "中文":
        BASE_IMAGE_FILENAME = "tier_list.png"
        RESULT_IMAGE_FILENAME = "final_tier_list.png"
        SESSION_KEY = "tier_counts_zh"
    else:
        BASE_IMAGE_FILENAME = "tier_list_en.png" # 假設你有英文版底圖
        RESULT_IMAGE_FILENAME = "final_tier_list_en.png"
        SESSION_KEY = "tier_counts_en"

    BASE_IMAGE_PATH = os.path.join(BASE_DIR, BASE_IMAGE_FILENAME)
    RESULT_IMAGE_PATH = os.path.join(BASE_DIR, RESULT_IMAGE_FILENAME)

    # 初始化計數器
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}

    st.divider()
    if st.button("🗑️ 清空目前榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.session_state.analysis_result = None
        st.session_state.debug_raw_data = None
        st.session_state.debug_curated = None
        st.success("已重置！")
        st.rerun()

# ==========================================
# 3. 圖片處理邏輯 (包含字體縮放)
# ==========================================
def load_font(size):
    # 嘗試載入系統字型，解決中文亂碼
    paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "C:\\Windows\\Fonts\\simhei.ttf"
    ]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def get_fit_font(draw, text, max_width, max_height, initial_size):
    """自動縮放字體大小以適應框框"""
    size = initial_size
    font = load_font(size)
    while size > 10: 
        try:
            l, t, r, b = draw.textbbox((0, 0), text, font=font)
            w, h = r - l, b - t
        except: w, h = draw.textlength(text, font=font), size # 舊版pillow相容
        
        if w < max_width and h < max_height: return font, h
        size -= 2
        font = load_font(size)
    return font, max_height

def create_base_tier_list_fallback():
    """如果找不到底圖，自動畫一張 (避免程式崩潰)"""
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
    """製作課程卡片 (還原舊版樣式)"""
    bg_color = (245, 245, 245, 255)
    border_color = (50, 50, 50, 255)
    img = Image.new('RGBA', size, bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0), (size[0]-1, size[1]-1)], outline=border_color, width=3)
    
    # 簡單拆分 課程 與 老師
    parts = full_text.split(' ')
    if len(parts) >= 2:
        course_name = parts[0]
        teacher_name = " ".join(parts[1:])
    else:
        course_name, teacher_name = full_text, ""

    W, H = size
    PADDING = 8
    target_w = W - (PADDING * 2)
    
    # 畫課程名
    font_course, h_c = get_fit_font(draw, course_name, target_w, H * 0.5, int(H * 0.4))
    try: l, t, r, b = draw.textbbox((0,0), course_name, font=font_course); w_c = r - l
    except: w_c = draw.textlength(course_name, font=font_course)
    draw.text(((W - w_c) / 2, (H * 0.45 - h_c) / 2), course_name, fill=(0, 0, 0), font=font_course)
    
    # 畫老師名
    if teacher_name:
        font_teacher, h_t = get_fit_font(draw, teacher_name, target_w, H * 0.3, int(H * 0.25))
        try: l, t, r, b = draw.textbbox((0,0), teacher_name, font=font_teacher); w_t = r - l
        except: w_t = draw.textlength(teacher_name, font=font_teacher)
        draw.text(((W - w_t) / 2, (H * 0.75) - (h_t / 2)), teacher_name, fill=(80, 80, 80), font=font_teacher)
        
    return img

def update_tier_list_image(course_name, tier):
    """更新 Tier List (整合版)"""
    tier = tier.upper()
    if tier not in ['S', 'A', 'B', 'C', 'D']: tier = 'C'
    
    # 優先讀取上次生成的結果，如果沒有則讀取底圖，再沒有則自動生成
    if os.path.exists(RESULT_IMAGE_PATH):
        base = Image.open(RESULT_IMAGE_PATH).convert("RGBA")
    elif os.path.exists(BASE_IMAGE_PATH):
        base = Image.open(BASE_IMAGE_PATH).convert("RGBA")
    else:
        base = create_base_tier_list_fallback().convert("RGBA")
    
    W, H = base.size
    ROW_H = H // 5
    # 自動計算卡片大小
    CARD_SIZE = int(ROW_H * 0.85)
    START_X = int(W * 0.28) # 避開左邊 S/A/B... 的字
    PADDING = 10
    
    count = st.session_state[SESSION_KEY][tier]
    x = START_X + (count * (CARD_SIZE + PADDING))
    y_idx = {'S':0, 'A':1, 'B':2, 'C':3, 'D':4}[tier]
    y = int(y_idx * ROW_H + (ROW_H - CARD_SIZE) / 2)
    
    if x + CARD_SIZE > W: return False # 滿了
        
    card = create_course_card(course_name, size=(CARD_SIZE, CARD_SIZE))
    base.alpha_composite(card, (int(x), int(y)))
    
    base.save(RESULT_IMAGE_PATH)
    st.session_state[SESSION_KEY][tier] += 1
    return True

# ==========================================
# 4. 核心 Agent 函式 (Manager 架構)
# ==========================================
def call_ai(contents, model_name):
    try:
        model = genai.GenerativeModel(model_name)
        return model.generate_content(contents).text
    except Exception as e:
        try:
            # Fallback
            fallback = genai.GenerativeModel("models/gemini-2.0-flash")
            return fallback.generate_content(contents).text
        except: return None

def agent_manager(user_query):
    """意圖識別"""
    prompt = f"""
    使用者輸入：「{user_query}」
    請判斷意圖並輸出 JSON：
    1. 僅有「課程/類別」-> intent: "recommend"
    2. 包含「老師名字」-> intent: "analyze", keywords: "老師名字"
    重點：如果是 analyze，keywords 只需留人名。
    
    JSON format: {{"intent": "recommend" or "analyze", "keywords": "...", "reason": "..."}}
    """
    res = call_ai(prompt, MODELS["MANAGER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return {"intent": "recommend", "keywords": user_query}

def search_google(query, mode="analysis"):
    """搜尋引擎"""
    if not GOOGLE_SEARCH_API_KEY: return []
    
    if mode == "analysis":
        # 廣域搜尋策略
        final_query = f'(北科大 "{query}") OR ("{query}" Dcard PTT)'
    else:
        final_query = f'北科大 {query} 推薦 site:dcard.tw OR site:ptt.cc'
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': final_query, 'num': 8}
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        # 格式化輸出
        return [f"[{i.get('title')}]\n{i.get('snippet')}\nLink: {i.get('link')}" for i in data.get('items', [])]
    except: return []

def agent_analyst(course_name, data):
    """評分 Agent"""
    prompt = f"""
    分析目標：「{course_name}」。資料：{data}
    請評分 0-100 並給予 Tier (S/A/B/C/D)。
    JSON: {{"rank": "稱號", "tier": "S/A/B/C/D", "score": int, "reason": "短評", "tags": [], "details": "詳述"}}
    """
    return call_ai(prompt, MODELS["JUDGE"])

def agent_fixer(text):
    """格式修復 Agent"""
    prompt = f"Extract valid JSON from this:\n{text}"
    res = call_ai(prompt, MODELS["FIXER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return None

# ==========================================
# 5. 主介面邏輯
# ==========================================
st.title("🎓 北科大 AI 選課顧問 (Pro版)")
st.caption("🚀 Agent Workflow + Visual Tier List")

c1, c2 = st.columns([4, 1])
with c1: user_input = st.text_input("輸入課程/老師...", placeholder="例：微積分 羅仁傑")
with c2: btn_search = st.button("🔍 智能搜尋", use_container_width=True, type="primary")

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("缺 API Key"); st.stop()
    
    # 清空舊資料
    st.session_state.debug_raw_data = None
    st.session_state.debug_curated = None
    
    with st.status("🤖 Agent 團隊啟動中...", expanded=True) as status:
        
        # 1. Manager
        st.write(f"🧠 Manager：識別意圖中...")
        intent_data = agent_manager(user_input)
        intent = intent_data.get("intent", "recommend")
        keywords = intent_data.get("keywords", user_input)
        
        if intent == "analyze":
            st.info(f"目標：分析「{keywords}」")
            
            # 2. Search
            st.write("🔍 Search Engine：廣域搜尋中...")
            raw_data = search_google(keywords, mode="analysis")
            st.session_state.debug_raw_data = raw_data # 保存原始資料
            
            if not raw_data:
                status.update(label="搜尋無結果", state="error")
                st.stop()

            # 3. Cleaner
            st.write(f"🧹 Cleaner：資料摘要中 (保留外校評價)...")
            curated = call_ai(f"摘要重點評價，保留外校資訊：{raw_data}", MODELS["CLEANER"])
            st.session_state.debug_curated = curated # 保存摘要
            
            # 4. Analyst
            st.write(f"⚖️ Analyst：深度評分中...")
            raw_res = agent_analyst(keywords, curated)
            final_data = agent_fixer(raw_res)
            
            if final_data:
                st.session_state.analysis_result = final_data
                
                # 5. Update Tier List Image
                st.write("🎨 Illustrator：繪製圖表中...")
                # 這裡傳入完整的 user_input 或 keywords 以顯示在卡片上
                update_tier_list_image(user_input, final_data.get('tier', 'C'))
                
                status.update(label="分析完成！", state="complete")
            else:
                status.update(label="分析失敗", state="error")
        else:
            st.info("推薦模式：(此模式暫不支援 Tier List 繪圖)")
            # 這裡可以保留推薦邏輯，與分析邏輯類似
            raw_data = search_google(keywords, mode="recommend")
            st.session_state.debug_raw_data = raw_data
            recommender_res = call_ai(f"推薦3門課：{raw_data}", MODELS["HUNTER"])
            st.session_state.debug_curated = recommender_res
            status.update(label="推薦完成", state="complete")

# ==========================================
# 6. 結果顯示區 (整合版)
# ==========================================
if st.session_state.analysis_result:
    d = st.session_state.analysis_result
    
    st.divider()
    col_res, col_img = st.columns([1.5, 2])
    
    # 左側：分析文字報告
    with col_res:
        st.subheader("📝 分析報告")
        st.metric("AI 評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"### {d.get('rank')}")
        st.success(d.get('reason'))
        st.write(d.get('details'))
        st.caption("標籤：" + ", ".join(d.get('tags', [])))
        
    # 右側：Tier List 圖片
    with col_img:
        st.subheader(f"🏆 課程排位榜 ({version_option})")
        if os.path.exists(RESULT_IMAGE_PATH):
            st.image(RESULT_IMAGE_PATH, use_column_width=True)
        elif os.path.exists(BASE_IMAGE_PATH):
            st.image(BASE_IMAGE_PATH, caption="尚無資料", use_column_width=True)
        else:
            st.info("尚未上傳底圖，使用自動生成模式。")

# === 資料來源與分析細節 (Expander) ===
if st.session_state.debug_raw_data or st.session_state.debug_curated:
    st.divider()
    st.caption("🔍 資料來源與分析細節")
    
    with st.expander("📄 點擊查看 Google 原始搜尋資料 (Raw Data)"):
        if st.session_state.debug_raw_data:
            for idx, item in enumerate(st.session_state.debug_raw_data):
                st.markdown(f"**Result {idx+1}:**")
                st.text(item)
                st.divider()
        else:
            st.write("無資料")

    with st.expander("🧠 點擊查看 AI 整理後的摘要 (Curated Data)"):
        if st.session_state.debug_curated:
            st.markdown(st.session_state.debug_curated)
        else:
            st.write("無資料")
