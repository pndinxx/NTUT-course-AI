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
    "MANAGER": "models/gemini-2.5-flash",       # 大腦
    "JUDGE":   "models/gemini-2.5-flash",       # 評分 (高智商)
    "CLEANER": "models/gemini-2.5-flash-lite",  # 清理 (快)
    "HUNTER":  "models/gemini-2.5-flash-lite",  # 推薦 (快)
    "FIXER":   "models/gemini-2.5-flash-lite"   # 格式 (快)
}

# ==========================================
# 2. 圖片處理邏輯 (Tier List)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_IMAGE_FILENAME = "final_tier_list.png"
RESULT_IMAGE_PATH = os.path.join(BASE_DIR, RESULT_IMAGE_FILENAME)

def get_font(size):
    paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc"
    ]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def create_base_tier_list():
    W, H = 1200, 1000
    img = Image.new('RGB', (W, H), (30, 30, 30))
    draw = ImageDraw.Draw(img)
    colors = {'S': '#FF7F7F', 'A': '#FFBF7F', 'B': '#FFFF7F', 'C': '#7FFF7F', 'D': '#7F7FFF'}
    row_h = H // 5
    font = get_font(60)
    
    for idx, (tier, color) in enumerate(colors.items()):
        y = idx * row_h
        draw.rectangle([(0, y), (200, y + row_h)], fill=color)
        draw.rectangle([(0, y), (W, y + row_h)], outline='black', width=2)
        draw.text((70, y + row_h//2 - 30), tier, fill='black', font=font)
        draw.line([(0, y+row_h), (W, y+row_h)], fill='white', width=2)
    return img

def create_course_card(text, size=(120, 120)):
    img = Image.new('RGBA', size, (240, 240, 240, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0), (size[0]-1, size[1]-1)], outline='black', width=3)
    
    font_size = 24
    font = get_font(font_size)
    lines = text.split(' ')
    y_text = 20
    for line in lines:
        draw.text((10, y_text), line, fill='black', font=font)
        y_text += 30
    return img

def update_tier_list_image(course_name, tier):
    if 'tier_counts' not in st.session_state:
        st.session_state.tier_counts = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
    
    tier = tier.upper()
    if tier not in ['S', 'A', 'B', 'C', 'D']: tier = 'C'
    
    if os.path.exists(RESULT_IMAGE_PATH):
        base = Image.open(RESULT_IMAGE_PATH).convert("RGBA")
    else:
        base = create_base_tier_list().convert("RGBA")
    
    W, H = base.size
    ROW_H = H // 5
    CARD_SIZE = int(ROW_H * 0.8)
    START_X = 220
    PADDING = 10
    
    count = st.session_state.tier_counts[tier]
    x = START_X + (count * (CARD_SIZE + PADDING))
    y_idx = {'S':0, 'A':1, 'B':2, 'C':3, 'D':4}[tier]
    y = y_idx * ROW_H + (ROW_H - CARD_SIZE) // 2
    
    if x + CARD_SIZE > W: return False
        
    card = create_course_card(course_name, size=(CARD_SIZE, CARD_SIZE))
    base.alpha_composite(card, (int(x), int(y)))
    
    base.save(RESULT_IMAGE_PATH)
    st.session_state.tier_counts[tier] += 1
    return True

# ==========================================
# 3. 核心 Agent 函式
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
    請判斷意圖並輸出 JSON：
    1. 僅有「課程/類別」-> intent: "recommend"
    2. 包含「老師名字」-> intent: "analyze", keywords: "老師名字"
    重點：如果是 analyze，keywords 只需留人名。
    
    JSON format: {{"intent": "...", "keywords": "...", "reason": "..."}}
    """
    res = call_ai(prompt, MODELS["MANAGER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return {"intent": "recommend", "keywords": user_query}

def search_google(query, mode="analysis"):
    if not GOOGLE_SEARCH_API_KEY: return []
    if mode == "analysis":
        final_query = f'(北科大 "{query}") OR ("{query}" Dcard PTT)'
    else:
        final_query = f'北科大 {query} 推薦 site:dcard.tw OR site:ptt.cc'
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': final_query, 'num': 8}
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        # 回傳格式化好的 List
        return [f"[{i.get('title')}]\n{i.get('snippet')}\nLink: {i.get('link')}" for i in data.get('items', [])]
    except: return []

def agent_analyst(course_name, data):
    prompt = f"""
    分析目標：「{course_name}」。資料：{data}
    請評分 0-100 並給予 Tier (S/A/B/C/D)。
    JSON: {{"rank": "稱號", "tier": "S/A/B/C/D", "score": int, "reason": "短評", "tags": [], "details": "詳述"}}
    """
    return call_ai(prompt, MODELS["JUDGE"])

def agent_fixer(text):
    prompt = f"Extract valid JSON from this:\n{text}"
    res = call_ai(prompt, MODELS["FIXER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return None

# ==========================================
# 4. 側邊欄 UI
# ==========================================
with st.sidebar:
    st.title("⚙️ 系統設定")
    st.info(f"主力模型: {MODELS['MANAGER'].split('/')[-1]}")
    
    st.divider()
    st.subheader("📊 Tier List 管理")
    if st.button("🗑️ 清空榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state.tier_counts = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.session_state.analysis_result = None
        st.session_state.debug_raw_data = None
        st.session_state.debug_curated = None
        st.success("榜單已重置")
        st.rerun()

# ==========================================
# 5. 主介面邏輯
# ==========================================
st.title("🎓 北科大 AI 選課顧問 (Pro版)")
st.caption("🚀 Agent Workflow + Visual Tier List")

c1, c2 = st.columns([4, 1])
with c1: user_input = st.text_input("輸入課程/老師...", placeholder="例：微積分 羅仁傑")
with c2: btn_search = st.button("🔍 智能搜尋", use_container_width=True, type="primary")

# 初始化 Session State
if 'analysis_result' not in st.session_state: st.session_state.analysis_result = None
if 'debug_raw_data' not in st.session_state: st.session_state.debug_raw_data = None
if 'debug_curated' not in st.session_state: st.session_state.debug_curated = None

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("缺 API Key"); st.stop()
    
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
            st.session_state.debug_raw_data = raw_data # 保存原始搜尋結果
            
            if not raw_data:
                status.update(label="搜尋無結果", state="error")
                st.stop()

            # 3. Cleaner
            st.write(f"🧹 Cleaner：資料摘要中...")
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
                update_tier_list_image(keywords, final_data.get('tier', 'C'))
                
                status.update(label="分析完成！", state="complete")
            else:
                status.update(label="分析失敗", state="error")
        else:
            status.update(label="推薦功能暫未完全整合圖片生成", state="complete")

# ==========================================
# 6. 結果顯示 (包含原始資料與分析摘要)
# ==========================================
if st.session_state.analysis_result:
    d = st.session_state.analysis_result
    
    st.divider()
    col_res, col_img = st.columns([1.5, 2])
    
    with col_res:
        st.subheader("📝 分析報告")
        st.metric("AI 評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"**{d.get('rank')}**")
        st.success(d.get('reason'))
        st.write(d.get('details'))
        st.caption("標籤：" + ", ".join(d.get('tags', [])))
        
    with col_img:
        st.subheader("🏆 課程排位榜")
        if os.path.exists(RESULT_IMAGE_PATH):
            st.image(RESULT_IMAGE_PATH, use_column_width=True)
    
    # === 新增：資料來源與分析細節 (類似原本的功能) ===
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
