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
st.set_page_config(page_title="北科大 AI 課程推薦系統", layout="wide")

try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    GOOGLE_SEARCH_API_KEY = st.secrets["GOOGLE_SEARCH_API_KEY"]
    SEARCH_ENGINE_ID = st.secrets["SEARCH_ENGINE_ID"]
except:
    GEMINI_API_KEY = None; GOOGLE_SEARCH_API_KEY = None; SEARCH_ENGINE_ID = None

if not GEMINI_API_KEY:
    with st.sidebar:
        st.warning("請輸入 API Keys")
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")
        GOOGLE_SEARCH_API_KEY = st.text_input("Google Search Key", type="password")
        SEARCH_ENGINE_ID = st.text_input("Search Engine ID")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 1. 模型定義 (MoE 架構 - 雙模型對決)
# ==========================================
MODELS = {
    "MANAGER":     "models/gemini-2.5-flash",       # 總控
    "CLEANER":     "models/gemini-2.5-flash-lite",  # 資料清理
    
    # === 評審團 (Expert Panel) ===
    "JUDGE_A":     "models/gemma-3-27b-it",         # 嚴格學術派 (Gemma 3)
    "JUDGE_B":     "models/gemini-2.5-flash",       # 甜涼快樂派 (Gemini 2.0)
    # [移除] Judge C
    
    # === 總結者 ===
    "SYNTHESIZER": "models/gemini-2.5-flash",       # 綜合決策
    
    # === 工具 ===
    "FIXER":       "models/gemini-2.5-flash-lite",
    "HUNTER":      "models/gemini-2.5-flash"
}

# ==========================================
# 2. 側邊欄與狀態管理
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

with st.sidebar:
    st.title("系統資源")
    
    # --- 動態狀態顯示區 ---
    st.subheader("即時運算狀態")
    status_placeholder = st.empty() 
    
    def update_sidebar_status(agent_name, model_name, status="running"):
        with status_placeholder.container():
            if status == "running":
                st.info(f"**{agent_name}** 正在工作")
                st.caption(f"Model: `{model_name}`")
            elif status == "idle":
                st.success("系統待機中")
            elif status == "error":
                st.error("發生錯誤")

    update_sidebar_status("System", "Ready", "idle")
    
    st.divider()
    st.caption("評審團架構 (Gemma vs Gemini)")
    st.text("Judge A: 嚴格學術 (Gemma 3)") 
    st.text("Judge B: 甜涼快樂 (Gemini 2.0)")
    st.text("Synthesizer: 總結決策")
    
    st.divider()
    st.caption("推薦獵頭 (Hunter)")
    st.text("Hunter: 推薦顧問 (2.5 Flash)")

    st.divider()
    version_option = st.radio("Tier List 版本", ("中文", "英文"), index=0)
    
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

    if st.button("清空榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.session_state.analysis_result = None
        st.success("已重置")
        st.rerun()

# ==========================================
# 3. 圖片處理
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
            # Fallback
            print(f"Model {model_name} failed. Reason: {e}")
            fallback = genai.GenerativeModel("models/gemini-2.0-flash")
            return fallback.generate_content(contents).text
        except: return None

def agent_manager(user_query):
    """
    Manager Agent: 負責意圖識別與關鍵字提取
    """
    prompt = f"""
    使用者輸入：「{user_query}」
    
    請判斷使用者意圖，並輸出標準 JSON 格式：

    1. 【推薦模式】(intent: "recommend")
       - 觸發條件：輸入僅包含「課程名稱」、「類別」或「通識」(例如：體育, 微積分, 甜課)。
       - 任務：**keywords 欄位必須填入該課程名稱**。
    
    2. 【分析模式】(intent: "analyze")
       - 觸發條件：輸入包含「特定老師名字」(例如：微積分 羅仁傑, 施坤龍)。
       - 任務：keywords 欄位只填入「老師本名」(去除課程名與評價字眼)。

    回傳範例：
    - 輸入"體育" -> {{"intent": "recommend", "keywords": "體育", "reason": "找體育課推薦"}}
    - 輸入"羅仁傑" -> {{"intent": "analyze", "keywords": "羅仁傑", "reason": "查老師評價"}}
    
    JSON format: {{"intent": "recommend" or "analyze", "keywords": "...", "reason": "..."}}
    """
    
    res = call_ai(prompt, MODELS["MANAGER"])
    
    try: 
        data = json.loads(res.replace("```json","").replace("```","").strip())
        
        # Python 防呆機制
        if not data.get("keywords") or len(str(data.get("keywords")).strip()) == 0:
            data["keywords"] = user_query
            
        return data
        
    except: 
        return {"intent": "recommend", "keywords": user_query, "reason": "解析失敗，使用原始輸入"}
        
def search_google(query, mode="analysis"):
    if not GOOGLE_SEARCH_API_KEY: return []
    q_str = f'(北科大 "{query}") OR ("{query}" Dcard PTT)' if mode == "analysis" else f'北科大 {query} 推薦 site:dcard.tw OR site:ptt.cc'
    url = "https://www.googleapis.com/customsearch/v1"
    try:
        res = requests.get(url, params={'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': q_str, 'num': 8}, timeout=10)
        data = res.json()
        return [f"[{i.get('title')}]\n{i.get('snippet')}\nLink: {i.get('link')}" for i in data.get('items', [])]
    except: return []

# === 評審團機制 (刪除 Judge C) ===
def agent_judge_panel(course_name, data):
    """
    Panel of Experts:
    - A: Gemma 3 27B (Strict)
    - B: Gemini 2.0 Flash (Chill)
    """
    
    # 1. Judge A (Gemma 3): 嚴格學術派
    prompt_a = f"""
    你是【嚴格學術派教授】。評估目標：「{course_name}」。資料：{data}。
    請專注於：課程紮實度、學得到東西嗎、專業知識含量。
    請給出你的分數(0-100)與簡短評論 (100字內)。不要客套。
    """
    
    # 2. Judge B: 甜涼快樂派
    prompt_b = f"""
    你是【想輕鬆通過課程的同學】。評估目標：「{course_name}」。資料：{data}。
    請專注於：給分甜不甜、作業多不多、點名頻率、好不好過。
    請給出你的分數(0-100)與簡短評論 (100字內)。
    """
    
    # 依序呼叫
    res_a = call_ai(prompt_a, MODELS["JUDGE_A"])
    res_b = call_ai(prompt_b, MODELS["JUDGE_B"])
    
    return {
        "A": res_a if res_a else "Gemma 思考過久...",
        "B": res_b if res_b else "Judge B 離線..."
    }

def agent_synthesizer(course_name, panel_results):
    # 先把評審結果轉成乾淨的字串
    import json
    panel_text = json.dumps(panel_results, ensure_ascii=False, indent=2)

    prompt = f"""
    你是最終決策長 (Synthesizer)。
    目標：「{course_name}」。
    
    以下是兩位評審的詳細意見：
    {panel_text}
    
    任務：
    1. 綜合雙方意見 (嚴格 vs 輕鬆)，給予評級 Tier (S/A/B/C/D)。
    2. 給予包含課程內涵(學不學得到東西)、課程輕鬆程度、甜度，以★★★★★表示 滿分為五顆星。
    3. 總結出一個短評。

    **極重要：請務必只輸出純 JSON 格式，不要有任何 Markdown (```json) 或其他文字。**
    
    JSON 範例：
    {{
        "rank": "稱號", 
        "tier": "A", 
        "reason": "雖然作業多但學得到東西", 
        "tags": ["札實", "偏累"], 
        "details": "綜合看法..."
    }}
    """
    return call_ai(prompt, MODELS["SYNTHESIZER"])

# === Hunter Agent ===
def agent_hunter(topic, data):
    """
    Hunter: 課程推薦專家
    """
    prompt = f"""
    你是北科大選課獵頭 (Hunter)。
    使用者想找：「{topic}」。
    搜尋結果：{data}
    
    請推薦 **3 門** 最符合的課程或老師。
    請用 Markdown 表格呈現，包含：
    | 課程/老師 | 推薦指數 | 特色短評 |
    推薦指數以★★★★★表示 滿分為五顆星
    特色短評50字內
    |---|---|---|
    
    並在最後給出一個總結建議。
    """
    return call_ai(prompt, MODELS["HUNTER"])

def agent_fixer(text):
    res = call_ai(f"Extract valid JSON:\n{text}", MODELS["FIXER"])
    try: return json.loads(res.replace("```json","").replace("```","").strip())
    except: return None

# ==========================================
# 5. 主介面邏輯
# ==========================================
st.title("北科大 AI 課程推薦系統")
st.caption("(Powered by Google AI Studio)")

c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
with c1: user_input = st.text_input("輸入「課程 老師」「老師」以查找評價，輸入「課程」以查找推薦教師", placeholder="例：物理 施坤龍")
with c2: btn_search = st.button("智能搜尋", use_container_width=True, type="primary")

if 'analysis_result' not in st.session_state: st.session_state.analysis_result = None

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("缺 API Key"); st.stop()
    st.session_state.analysis_result = None 
    
    with st.status("任務啟動...", expanded=True) as status:
        
        # 1. Manager
        update_sidebar_status("Manager", MODELS["MANAGER"])
        st.write("**Manager**: 分析意圖...")
        intent_data = agent_manager(user_input)
        intent = intent_data.get("intent", "recommend")
        keywords = intent_data.get("keywords", user_input)
        
        intent_text = "分析特定老師評價" if intent == "analyze" else "推薦相關課程"
        st.success(f"意圖：**{intent_text}** (目標：`{keywords}`)")
        
        if intent == "analyze":
            # 2. Search
            update_sidebar_status("Search Engine", "Google API")
            st.write(f"🔍 **Search**: 廣域搜尋中...")
            raw_data = search_google(keywords, mode="analysis")
            
            if not raw_data:
                status.update(label="無搜尋結果", state="error")
                st.stop()
            
            with st.expander(f"原始搜尋資料 ({len(raw_data)} 筆)", expanded=False):
                for item in raw_data:
                    st.text(item)
                    st.divider()

            # 3. Cleaner
            update_sidebar_status("Cleaner", MODELS["CLEANER"])
            st.write("**Cleaner**: 資料摘要中...")
            curated = call_ai(f"摘要重點評價：{raw_data}", MODELS["CLEANER"])
            
            with st.expander("📝 資料摘要", expanded=False):
                st.markdown(curated)

            # 4. Panel Judges (A vs B)
            st.write("⚖️ **Panel Judges**: 雙方評審正在激烈辯論...")
            update_sidebar_status("Judge A (Gemma 3)", MODELS["JUDGE_A"])
            panel_res = agent_judge_panel(keywords, curated)
            
            with st.expander("🗣️ 點擊查看評審意見 (Gemma 3 vs Gemini)", expanded=False):
                st.markdown(f"**👨‍🏫 嚴格學術派 (Gemma 3 27B)**:\n{panel_res['A']}")
                st.divider()
                st.markdown(f"**😎 甜涼快樂派 (2.0 flash)**:\n{panel_res['B']}")
                
            # 5. Synthesizer
            update_sidebar_status("Synthesizer", MODELS["SYNTHESIZER"])
            st.write("🏆 **Synthesizer**: 正在統整最終判決...")
            final_raw = agent_synthesizer(keywords, panel_res)
            final_data = agent_fixer(final_raw)
            
            if final_data:
                st.session_state.analysis_result = final_data
                
                # 6. Illustrator
                update_sidebar_status("Illustrator", "Local")
                st.write("🎨 **Illustrator**: 更新榜單...")
                update_tier_list_image(user_input, final_data.get('tier', 'C'))
                
                status.update(label="✅ 評審完成！", state="complete")
                update_sidebar_status("System", "Ready", "idle")
            else:
                status.update(label="❌ 綜合分析失敗", state="error")
        else:
            # === 推薦模式 ===
            update_sidebar_status("Hunter", MODELS["HUNTER"])
            st.write("🕵️ **Hunter**: 搜尋熱門課程...")
            
            raw_data = search_google(keywords, mode="recommend")
            with st.expander("📄 搜尋結果", expanded=False):
                st.write(raw_data)
            
            st.write("🕵️ **Hunter**: 正在撰寫推薦報告...")
            res = agent_hunter(keywords, raw_data)
            
            st.markdown(res)
            
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
        st.subheader("📝 最終決策報告")
        st.metric("綜合評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"### {d.get('rank')}")
        st.success(d.get('reason'))
        st.write(d.get('details'))
        st.caption("標籤：" + ", ".join(d.get('tags', [])))
        
    with col_img:
        st.subheader(f"課程排位榜 ({version_option})")
        if os.path.exists(RESULT_IMAGE_PATH):
            st.image(RESULT_IMAGE_PATH, use_column_width=True)
        else:
            st.image(BASE_IMAGE_PATH, caption="尚無資料", use_column_width=True)
