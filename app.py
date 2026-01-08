import streamlit as st
import os
import requests
import json
import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont
import graphviz

# ==========================================
# 0. 模型定義 (策略升級版)
# ==========================================
# Manager & Judge (大腦): 使用最強的 2.5 Flash 處理複雜邏輯
# Cleaner, Hunter, Fixer (手腳): 使用 2.5 Flash-Lite 處理簡易任務 (更省資源、速度更快)
MODELS = {
    "MANAGER": "models/gemini-2.5-flash",       # 中央大腦 (判斷意圖)
    "JUDGE":   "models/gemini-2.5-flash",       # 首席分析師 (深度評分)
    "CLEANER": "models/gemini-2.5-flash-lite",  # 資料清理 (Lite)
    "HUNTER":  "models/gemini-2.5-flash-lite",  # 獵頭/推薦 (Lite)
    "FIXER":   "models/gemini-2.5-flash-lite"   # 格式修復 (Lite)
}

# ==========================================
# 1. RAG 知識庫 (本地檔案)
# ==========================================
def retrieve_local_rag(query):
    file_path = os.path.join(os.path.dirname(__file__), "knowledge.json")
    if not os.path.exists(file_path): return None
    try:
        with open(file_path, "r", encoding='utf-8') as f:
            knowledge_db = json.load(f)
        results = []
        for key, info in knowledge_db.items():
            if key in query: results.append(info)
        if results: return "\n".join(results)
    except: pass
    return None

# ==========================================
# 2. 設定頁面與 API Keys
# ==========================================
st.set_page_config(page_title="北科大AI選課顧問 (Manager版)", layout="wide")

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
# 3. 核心：通用模型呼叫器
# ==========================================
def call_ai(contents, model_name):
    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(contents)
        return response.text
    except Exception as e:
        # Fallback 機制：如果 Lite 或 2.5 出錯，退回穩定的 2.0 Flash
        try:
            print(f"Model {model_name} failed, falling back to 2.0-flash. Error: {e}")
            fallback = genai.GenerativeModel("models/gemini-2.0-flash")
            return fallback.generate_content(contents).text
        except: return None

# ==========================================
# 4. Agent 團隊
# ==========================================

def agent_manager(user_query):
    """
    ★ 中央大腦 (Manager Agent) ★
    判斷使用者意圖，並精確提取「人名」或「關鍵字」
    """
    prompt = f"""
    使用者輸入：「{user_query}」
    
    請判斷使用者的意圖，並輸出 JSON：
    1. 若輸入僅包含「課程名稱」或「類別」(如：微積分, 體育, 甜課) -> 意圖為 "recommend"
    2. 若輸入包含「特定老師名字」(如：微積分 羅仁傑, 羅仁傑, 廖xx) -> 意圖為 "analyze"
    
    重點：在 "keywords" 欄位中，如果意圖是 "analyze"，請只提取「老師姓名」本身，不要包含「評價」、「好嗎」等字眼。
    
    回傳格式：
    {{
        "intent": "recommend" 或 "analyze",
        "keywords": "乾淨的搜尋主體 (人名或課名)",
        "reason": "判斷理由"
    }}
    """
    res = call_ai(prompt, MODELS["MANAGER"])
    try:
        return json.loads(res.replace("```json","").replace("```","").strip())
    except:
        return {"intent": "recommend", "keywords": user_query, "reason": "解析失敗，預設推薦"}

def search_google(query, mode="analysis"):
    """
    ★ 搜尋引擎升級版 ★
    - Analysis 模式：解鎖 NTUT 限制，同時搜尋校內資訊與廣域 Dcard/PTT 討論。
    - Recommend 模式：維持鎖定北科大相關討論。
    """
    if not GOOGLE_SEARCH_API_KEY: return []
    
    # ★★★ 關鍵修改：搜尋策略升級 ★★★
    if mode == "analysis":
        # 策略：(北科大 + 老師) OR (老師 + Dcard/PTT)
        # 不加入「評價」二字，讓搜尋更廣泛
        q1 = f'"北科大" {query}' 
        q2 = f'{query} Dcard PTT'
        final_query = f"({q1}) OR ({q2})"
    else:
        # 推薦模式：找北科大範圍內的好課
        final_query = f"北科大 {query} 推薦 甜涼 好過 (site:dcard.tw OR site:ptt.cc)"
    
    print(f"🔍 Executing Search: {final_query}") # Debug 用

    url = "https://www.googleapis.com/customsearch/v1"
    params = {'key': GOOGLE_SEARCH_API_KEY, 'cx': SEARCH_ENGINE_ID, 'q': final_query, 'num': 8}
    
    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()
        if 'items' not in data: return []
        results = []
        for i in data['items']:
            link = i.get('link', '')
            src = "PTT" if "ptt.cc" in link else "Dcard" if "dcard.tw" in link else "Official/Web"
            results.append(f"[{src}] {i.get('title')}\n{i.get('snippet')}")
        return results
    except Exception as e:
        print(f"Search Error: {e}")
        return []

def agent_data_curator(course_name, raw_data):
    """Agent 1: 資料清理 (使用 Lite 模型)"""
    web_context = "\n---\n".join(raw_data)
    rag_info = retrieve_local_rag(course_name)
    rag_text = f"\n### 🏫 校內RAG資訊:\n{rag_info}\n" if rag_info else ""

    prompt = f"""
    你是資料清理專家。查詢目標：「{course_name}」。
    請去除無關廣告。
    
    **重要指令**：
    1. 若資料包含該老師在「其他學校」(如台科、成大等) 的評價，務必保留，這對評估老師風格至關重要。
    2. 摘要重點：評分風格、點名頻率、作業量、個性。
    
    {rag_text}
    原始資料：{web_context}
    請直接輸出精簡摘要 (Markdown格式)：
    """
    return call_ai(prompt, MODELS["CLEANER"])

def agent_analyst(course_name, curated_data):
    """Agent 2: 評分分析 (使用 Pro/Flash 高智商模型)"""
    prompt = f"""
    你是嚴格的選課分析師。分析目標：「{course_name}」。
    資料：{curated_data}
    
    請進行 0-100 分評級。
    **注意：請綜合參考該老師在北科大及過往其他學校(若有)的評價。**
    
    請輸出 JSON: 
    {{
        "rank": "稱號 (e.g. 佛心, 大刀, 札實)", 
        "tier": "S/A/B/C/D", 
        "score": 分數(int), 
        "reason": "一句話短評", 
        "tags": ["特徵1", "特徵2"], 
        "details": "詳細說明(若有參考外校評價請特別註明)"
    }}
    """
    return call_ai(prompt, MODELS["JUDGE"])

def agent_recommender(category, raw_data):
    """Agent 4: 推薦清單 (使用 Lite 模型)"""
    web_context = "\n---\n".join(raw_data)
    prompt = f"""
    使用者想找「{category}」的好課。
    資料：{web_context}
    
    請找出 **最推薦的 3 位** 老師或課程。
    請輸出 JSON List: 
    [
        {{"teacher": "老師名", "subject": "課程名", "reason": "推薦理由", "stars": 1-5}}
    ]
    """
    return call_ai(prompt, MODELS["HUNTER"])

def agent_fixer(raw_text, is_list=False):
    """Agent 3: 格式修復 (使用 Lite 模型)"""
    try:
        clean = raw_text.replace("```json","").replace("```","").strip()
        return json.loads(clean)
    except:
        # Lite 模型修復 JSON 也綽綽有餘
        res = call_ai(f"Return only valid JSON based on this:\n{raw_text}", MODELS["FIXER"])
        try: return json.loads(res.replace("```json","").replace("```","").strip())
        except: return None

# ==========================================
# 5. UI 與 執行邏輯
# ==========================================
st.title("🎓 北科大 AI 選課顧問 (Pro版)")
st.caption(f"🚀 Powered by {MODELS['MANAGER']} & {MODELS['CLEANER']} | 智能意圖識別 | 廣域搜尋")

if 'analysis_result' not in st.session_state: st.session_state.analysis_result = None
if 'recommend_result' not in st.session_state: st.session_state.recommend_result = None

c1, c2 = st.columns([4, 1])
with c1: 
    user_input = st.text_input("想問什麼？", placeholder="輸入「微積分」推薦好課，或「微積分 羅仁傑」分析評價")
with c2: 
    btn_search = st.button("🔍 智能搜尋", use_container_width=True, type="primary")

if btn_search and user_input:
    if not GEMINI_API_KEY: st.error("請設定 API Key"); st.stop()
    
    # 1. Manager 思考
    with st.status("🧠 Manager 正在思考您的意圖...", expanded=True) as status:
        intent_data = agent_manager(user_input)
        intent = intent_data.get("intent", "recommend")
        keywords = intent_data.get("keywords", user_input)
        
        if intent == "analyze":
            st.info(f"💡 識別意圖：**分析特定老師/課程** (目標：{keywords})")
            st.write("🔍 啟動廣域搜尋引擎 (校內 + Dcard/PTT 廣域)...")
            
            # 執行分析流程
            raw_data = search_google(keywords, mode="analysis")
            if not raw_data:
                st.warning("找不到相關資料，請檢查老師名字是否正確。")
                status.update(label="搜尋無結果", state="error")
                st.stop()

            st.write(f"🧹 資料清洗 (使用 {MODELS['CLEANER']})...")
            curated = agent_data_curator(keywords, raw_data)
            
            st.write(f"⚖️ 深度評分 (使用 {MODELS['JUDGE']})...")
            raw_res = agent_analyst(keywords, curated)
            final_data = agent_fixer(raw_res)
            
            if final_data:
                st.session_state.analysis_result = final_data
                st.session_state.recommend_result = None
                status.update(label="分析完成", state="complete")
            else:
                status.update(label="分析失敗", state="error")
                
        else:
            st.info(f"💡 識別意圖：**推薦好課清單** (目標：{keywords})")
            st.write("🔍 搜尋北科大熱門課程...")
            
            # 執行推薦流程
            raw_data = search_google(keywords, mode="recommend")
            st.write(f"🕵️ 獵頭篩選 (使用 {MODELS['HUNTER']})...")
            raw_res = agent_recommender(keywords, raw_data)
            final_list = agent_fixer(raw_res, is_list=True)
            
            if final_list:
                st.session_state.recommend_result = final_list
                st.session_state.analysis_result = None
                status.update(label="推薦完成", state="complete")
            else:
                status.update(label="推薦失敗", state="error")

# === 結果顯示區 ===

# 1. 分析結果
if st.session_state.analysis_result:
    d = st.session_state.analysis_result
    st.divider()
    
    c_score, c_info = st.columns([1, 2])
    with c_score:
        st.metric("AI 評分", f"{d.get('score')} 分", d.get('tier'))
        st.markdown(f"### {d.get('rank')}")
    with c_info:
        st.success(f"💬 {d.get('reason')}")
        st.write(d.get('details'))
        st.write("🏷️ " + " ".join([f"`{t}`" for t in d.get('tags', [])]))

    st.divider()
    st.subheader("🕸️ 評價關聯圖")
    g = graphviz.Digraph(attr={'rankdir':'LR', 'bgcolor':'transparent'})
    g.node(user_input, shape='doublecircle', style='filled', fillcolor='#E1F5FE')
    g.node(d['tier'], shape='circle', style='filled', fillcolor='#FFF9C4')
    g.edge(user_input, d['tier'], label=str(d['score']))
    for t in d.get('tags', []):
        g.node(t, shape='ellipse', style='filled', fillcolor='#F5F5F5')
        g.edge(user_input, t)
    st.graphviz_chart(g)

# 2. 推薦結果
if st.session_state.recommend_result:
    st.divider()
    st.subheader(f"✨ 根據「{user_input}」為您推薦：")
    cols = st.columns(3)
    for i, r in enumerate(st.session_state.recommend_result):
        with cols[i%3]:
            with st.container(border=True):
                st.markdown(f"### 🏆 {r.get('teacher')}")
                st.caption(f"課程: {r.get('subject')}")
                st.write(f"推薦度: {'⭐'*int(r.get('stars', 3))}")
                st.info(r.get('reason'))
                if st.button(f"詳細分析 {r.get('teacher')}", key=f"rec_{i}"):
                     st.info(f"請在搜尋欄輸入「{r.get('teacher')}」進行詳細分析！")
