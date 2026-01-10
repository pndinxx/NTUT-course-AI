import streamlit as st
import os
import requests
import json
import time
import google.generativeai as genai
from tavily import TavilyClient
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 0. 設定與 API Keys
# ==========================================
st.set_page_config(page_title="北科大 AI 課程推薦系統", layout="wide")

def get_secret(key):
    if key in st.secrets:
        return st.secrets[key]
    if os.getenv(key):
        return os.getenv(key)
    return None

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GOOGLE_SEARCH_API_KEY = get_secret("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = get_secret("SEARCH_ENGINE_ID")
TAVILY_API_KEY = get_secret("TAVILY_API_KEY")

if not GEMINI_API_KEY:
    with st.sidebar:
        st.warning("請輸入 API Keys")
        GEMINI_API_KEY = st.text_input("Gemini API Key", type="password")
        GOOGLE_SEARCH_API_KEY = st.text_input("Google Search Key", type="password")
        SEARCH_ENGINE_ID = st.text_input("Search Engine ID")
        TAVILY_API_KEY = st.text_input("Tavily API Key", type="password")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 1. 模型定義
# ==========================================
MODELS = {
    "MANAGER":        "models/gemini-2.5-flash",
    "CLEANER":        "models/gemini-2.5-flash",
    
    "JUDGE_A_Gemma":  "models/gemma-3-27b-it",
    "JUDGE_A_Gemini": "models/gemini-2.5-flash",
    "JUDGE_B_Gemma":  "models/gemma-3-27b-it",
    "JUDGE_B_Gemini": "models/gemini-2.5-flash",
    
    "SYNTHESIZER":    "models/gemini-2.5-flash",
    "FIXER":          "models/gemini-2.5-flash-lite",
    "HUNTER":         "models/gemini-2.5-flash"
}

# ==========================================
# 2. 側邊欄與狀態管理
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_tier_filename(list_type, lang="zh"):
    suffix = "_en" if lang == "en" else ""
    return f"tier_list_{list_type}{suffix}.png" if list_type != "Total" else f"final_tier_list{suffix}.png"

with st.sidebar:
    st.title("系統資源")
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
    
    version_option = st.radio("底圖語言版本", ("中文", "英文"), index=0)
    CURRENT_LANG = "en" if version_option == "英文" else "zh"
    
    if version_option == "中文":
        BASE_IMAGE_FILENAME = "tier_list.png"
    else:
        BASE_IMAGE_FILENAME = "tier_list_en.png"

    BASE_IMAGE_PATH = os.path.join(BASE_DIR, BASE_IMAGE_FILENAME)

    if "tier_counts" not in st.session_state or "zh" not in st.session_state.tier_counts:
        st.session_state.tier_counts = {
            "zh": { "A": {'S':0,'A':0,'B':0,'C':0,'D':0}, "B": {'S':0,'A':0,'B':0,'C':0,'D':0}, "Total": {'S':0,'A':0,'B':0,'C':0,'D':0} },
            "en": { "A": {'S':0,'A':0,'B':0,'C':0,'D':0}, "B": {'S':0,'A':0,'B':0,'C':0,'D':0}, "Total": {'S':0,'A':0,'B':0,'C':0,'D':0} }
        }
    if st.button("清空所有榜單", type="primary"):
        for lang in ["zh", "en"]:
            for l_type in ["A", "B", "Total"]:
                fname = get_tier_filename(l_type, lang)
                path = os.path.join(BASE_DIR, fname)
                if os.path.exists(path): os.remove(path)
                st.session_state.tier_counts[lang][l_type] = {'S':0,'A':0,'B':0,'C':0,'D':0}
            
        st.session_state.analysis_result = None
        st.session_state.judge_results = None
        st.success("已重置所有榜單")
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

def update_tier_list_image(list_type, course_name, tier, lang="zh"):
    tier = tier.upper()
    if tier not in ['S', 'A', 'B', 'C', 'D']: tier = 'C'
    
    target_filename = get_tier_filename(list_type, lang)
    target_path = os.path.join(BASE_DIR, target_filename)
    
    current_base_img_name = "tier_list.png" if lang == "zh" else "tier_list_en.png"
    current_base_img_path = os.path.join(BASE_DIR, current_base_img_name)

    if os.path.exists(target_path): base = Image.open(target_path).convert("RGBA")
    elif os.path.exists(current_base_img_path): base = Image.open(current_base_img_path).convert("RGBA")
    else: base = create_base_tier_list_fallback().convert("RGBA")
    
    W, H = base.size
    ROW_H = H // 5
    CARD_SIZE = int(ROW_H * 0.85)
    START_X = int(W * 0.28)
    PADDING = 10
    
    count = st.session_state.tier_counts[lang][list_type][tier]
    x = START_X + (count * (CARD_SIZE + PADDING))
    y = int(({'S':0,'A':1,'B':2,'C':3,'D':4}[tier] * ROW_H) + (ROW_H - CARD_SIZE)/2)
    
    if x + CARD_SIZE > W: return False
    
    card = create_course_card(course_name, size=(CARD_SIZE, CARD_SIZE))
    base.alpha_composite(card, (int(x), int(y)))
    base.save(target_path)
    
    st.session_state.tier_counts[lang][list_type][tier] += 1
    return True

# ==========================================
# 4. Agent 邏輯
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
    1. 推薦模式 (intent: "recommend"): 僅有課程名 -> keywords: 課程名
    2. 分析模式 (intent: "analyze"): 含老師名 -> keywords: 老師名
    JSON format: {{"intent": "...", "keywords": "...", "reason": "..."}}
    """
    res = call_ai(prompt, MODELS["MANAGER"])
    try: 
        data = json.loads(res.replace("```json","").replace("```","").strip())
        if not data.get("keywords") or len(str(data.get("keywords")).strip()) == 0:
            data["keywords"] = user_query
        return data
    except: return {"intent": "recommend", "keywords": user_query}

def search_hybrid(query, mode="analysis"):
    results = []
    if GOOGLE_SEARCH_API_KEY and SEARCH_ENGINE_ID:
        try:
            q_str = f'(北科大 "{query}") OR ("{query}" Dcard PTT)' if mode == "analysis" else f'北科大 {query} 推薦'
            url = "https://www.googleapis.com/customsearch/v1"
            params = {'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': q_str, 'num': 5}
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                for item in res.json().get('items', []):
                    results.append(f"[Google] {item.get('title')}\n{item.get('snippet')}\nLink: {item.get('link')}")
        except Exception as e: print(f"Google Error: {e}")

    if TAVILY_API_KEY:
        try:
            tavily = TavilyClient(api_key=TAVILY_API_KEY)
            tav_res = tavily.search(query=f"北科大 {query} 評價 Dcard PTT", search_depth="advanced", max_results=5)
            for item in tav_res.get('results', []):
                content = item.get('content', '')[:300]
                results.append(f"[Tavily] {item.get('title')}\n{content}\nLink: {item.get('url')}")
        except Exception as e: print(f"Tavily Error: {e}")

    if not results: return []
    unique_results = {}
    for r in results:
        link = r.split("Link: ")[-1].strip()
        if link not in unique_results: unique_results[link] = r
    return list(unique_results.values())
        
def agent_cleaner(course_name, raw_data):
    prompt = f"""
    你是資料過濾專家。查詢目標：「{course_name}」。
    任務：過濾雜訊，保留北科大/教學相關完整資料。
    規則：
    1. 完整保留原文，不摘要。
    2. 刪除無關雜訊(廣告/導航)。
    3. 特別保留「北科課程好朋友」數據。
    強制輸出格式：
    ---
    ### 來源：[連結標題](連結網址)
    **原始內文**：
    (內容)
    ---
    資料：{raw_data}
    """
    return call_ai(prompt, MODELS["CLEANER"])
    
def agent_judge_panel(course_name, data):
    base_prompt = f"""
    目標：「{course_name}」。資料：{data}。
    請評分並給予 Tier (S/A/B/C/D)。
    **務必輸出純 JSON 格式**：{{ "tier": "S", "score": 95, "comment": "簡短評語" }}
    """
    prompt_a = f"你是【嚴格學術派教授】。專注：紮實度、專業性。{base_prompt}"
    prompt_b = f"你是【想輕鬆通過的同學】。專注：甜度、好過。{base_prompt}"
    
    res_a_gemma = call_ai(prompt_a, MODELS["JUDGE_A_Gemma"])
    res_a_gemini = call_ai(prompt_a, MODELS["JUDGE_A_Gemini"])
    res_b_gemma = call_ai(prompt_b, MODELS["JUDGE_B_Gemma"])
    res_b_gemini = call_ai(prompt_b, MODELS["JUDGE_B_Gemini"])
    
    def parse_judge(raw_text):
        try: return json.loads(raw_text.replace("```json","").replace("```","").strip())
        except: return {"tier": "C", "score": 70, "comment": str(raw_text)[:100]}

    return {
        "A_Gemma": parse_judge(res_a_gemma),
        "A_Gemini": parse_judge(res_a_gemini),
        "B_Gemma": parse_judge(res_b_gemma),
        "B_Gemini": parse_judge(res_b_gemini)
    }

def agent_synthesizer(course_name, panel_results):
    import json
    panel_text = json.dumps(panel_results, ensure_ascii=False, indent=2)
    prompt = f"""
    你是最終決策長 (Synthesizer)。目標：「{course_name}」。
    意見：{panel_text}
    任務：
    1. 計算最終分數與 Tier。
    2. 新增星星評等 (內涵/輕鬆/甜度)。
    3. 總結短評。
    JSON 範例：
    {{
        "rank": "硬核大刀", "tier": "B", "score": 75,
        "star_ratings": {{ "learning": "★★★★★", "chill": "★★☆☆☆", "sweet": "★★☆☆☆" }},
        "reason": "...", "tags": [], "details": "..."
    }}
    """
    return call_ai(prompt, MODELS["SYNTHESIZER"])

def agent_hunter(topic, data):
    prompt = f"""
    你是北科大選課獵頭。使用者想找：「{topic}」。
    
    參考資料 (已過濾)：
    {data}
    
    請推薦 **3 門** 最符合需求的課程或老師。
    
    請務必使用 **Markdown 表格** 呈現，欄位如下：
    | 課程/老師 | ★星星推薦指數 | 核心推薦理由 |
    |---|---|---|
    
    (請在表格下方補充一段總結建議)
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
if 'judge_results' not in st.session_state: st.session_state.judge_results = None

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("缺 API Key"); st.stop()
    st.session_state.analysis_result = None 
    st.session_state.judge_results = None
    
    with st.status("任務啟動...", expanded=True) as status:
        update_sidebar_status("Manager", MODELS["MANAGER"])
        intent_data = agent_manager(user_input)
        intent = intent_data.get("intent", "recommend")
        keywords = intent_data.get("keywords", user_input)
        st.success(f"意圖：**{intent}** (目標：`{keywords}`)")
        
        if intent == "analyze":
            update_sidebar_status("Search Engine", "Hybrid")
            st.write(f"**Search**: 廣域搜尋中...")
            raw_data = search_hybrid(keywords, mode="analysis")
            if not raw_data: st.stop()
            
            with st.expander(f"原始搜尋資料 ({len(raw_data)} 筆)", expanded=False):
                for item in raw_data:
                    # [修改] 使用 HTML div 搭配 font-size: 13px 來縮小字體
                    st.markdown(f'<div style="font-size: 13px; line-height: 1.4; white-space: pre-wrap;">{item}</div>', unsafe_allow_html=True)
                    st.divider()

            update_sidebar_status("Cleaner", MODELS["CLEANER"])
            st.write("**Cleaner**: 資料摘要中...")
            curated = agent_cleaner(keywords, raw_data)
            with st.expander("資料摘要", expanded=False): st.markdown(curated)

            st.write("**Panel Judges**: 四方會談 (Gemma vs Gemini)...")
            update_sidebar_status("Judges (x4)", "Multi-Model")
            panel_res = agent_judge_panel(keywords, curated)
            st.session_state.judge_results = panel_res
            
            with st.expander("查看四位評審意見", expanded=False):
                c_a, c_b = st.columns(2)
                with c_a:
                    st.markdown("###嚴格學術派")
                    st.info(f"**Gemma 3**: {panel_res['A_Gemma']['score']}分\n{panel_res['A_Gemma']['comment']}")
                    st.info(f"**Gemini 2.5**: {panel_res['A_Gemini']['score']}分\n{panel_res['A_Gemini']['comment']}")
                with c_b:
                    st.markdown("###甜涼快樂派")
                    st.warning(f"**Gemma 3**: {panel_res['B_Gemma']['score']}分\n{panel_res['B_Gemma']['comment']}")
                    st.warning(f"**Gemini 2.5**: {panel_res['B_Gemini']['score']}分\n{panel_res['B_Gemini']['comment']}")

            update_sidebar_status("Synthesizer", MODELS["SYNTHESIZER"])
            st.write("**Synthesizer**: 正在統整最終判決...")
            final_raw = agent_synthesizer(keywords, panel_res)
            final_data = agent_fixer(final_raw)
            
            if final_data:
                st.session_state.analysis_result = final_data
                update_sidebar_status("Illustrator", "Local")
                st.write("**Illustrator**: 更新三張榜單...")
                
                update_tier_list_image("A", user_input, panel_res['A_Gemini'].get('tier', 'C'), lang=CURRENT_LANG)
                update_tier_list_image("B", user_input, panel_res['B_Gemini'].get('tier', 'C'), lang=CURRENT_LANG)
                update_tier_list_image("Total", user_input, final_data.get('tier', 'C'), lang=CURRENT_LANG)
                
                status.update(label="評審完成！", state="complete")
                update_sidebar_status("System", "Ready", "idle")
            else:
                status.update(label="綜合分析失敗", state="error")
        else:
            # === 推薦模式 (Hunter Mode) ===
            update_sidebar_status("Hunter", MODELS["HUNTER"])
            st.write("**Hunter**: 搜尋熱門課程...")
            
            # 1. 搜尋 (Search)
            raw_data = search_hybrid(keywords, mode="recommend")
            
            with st.expander(" 原始搜尋結果", expanded=False):
                for item in raw_data:
                    st.markdown(f'<div style="font-size: 13px; line-height: 1.4; white-space: pre-wrap;">{item}</div>', unsafe_allow_html=True)
                    st.divider()
            
            # 2. 清理 (Cleaner) -> 關鍵修改：Hunter 現在也用 Cleaner 了
            st.write("**Cleaner**: 正在過濾雜訊...")
            curated = agent_cleaner(keywords, raw_data)
            
            # 3. 推薦 (Hunter)
            st.write("**Hunter**: 正在撰寫推薦報告...")
            # 這裡傳入的是 curated (清理後的資料)，而不是 raw_data
            res = agent_hunter(keywords, curated)
            
            st.markdown(res)
            
            status.update(label="推薦完成", state="complete")
            update_sidebar_status("System", "Ready", "idle")

# ==========================================
# 6. 結果顯示區
# ==========================================
if st.session_state.analysis_result:
    d = st.session_state.analysis_result
    st.divider()
    col_res, col_img = st.columns([1.5, 2])
    
    with col_res:
        st.subheader("最終決策報告")
        st.metric("綜合評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"### {d.get('rank')}")
        stars = d.get('star_ratings', {})
        if stars:
            st.markdown("---")
            st.write(f" **課程內涵**：{stars.get('learning', 'N/A')}")
            st.write(f" **輕鬆程度**：{stars.get('chill', 'N/A')}")
            st.write(f" **分數甜度**：{stars.get('sweet', 'N/A')}")
            st.markdown("---")
        st.success(d.get('reason'))
        st.write(d.get('details'))
        st.caption("標籤：" + ", ".join(d.get('tags', [])))

    with col_img:
        tab_total, tab_a, tab_b = st.tabs(["綜合榜單", "嚴格派榜單", "甜涼派榜單"])
        
        def show_tier_img(l_type):
            fname = get_tier_filename(l_type, CURRENT_LANG)
            path = os.path.join(BASE_DIR, fname)
            if os.path.exists(path):
                st.image(path, use_column_width=True)
            else:
                st.image(BASE_IMAGE_PATH, caption="尚無資料", use_column_width=True)
        
        with tab_total:
            st.caption("Synthesizer 綜合決策")
            show_tier_img("Total")
        with tab_a:
            st.caption("嚴格學術派")
            show_tier_img("A")
        with tab_b:
            st.caption("甜涼快樂派")
            show_tier_img("B")
