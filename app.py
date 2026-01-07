import streamlit as st
import os
import requests
import json
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import time

# ==========================================
# 0. 雙重 SDK 匯入 (Hybrid SDK Import)
# ==========================================
# 嘗試匯入舊版 SDK (穩定版)
try:
    import google.generativeai as genai_v1
    HAS_V1_SDK = True
except ImportError:
    HAS_V1_SDK = False

# 嘗試匯入新版 SDK (實驗版)
try:
    from google import genai as genai_v2
    HAS_V2_SDK = True
except ImportError:
    HAS_V2_SDK = False

# ==========================================
# 1. 設定頁面與 API Keys
# ==========================================
st.set_page_config(page_title="北科大AI選課顧問", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_secret(key_name):
    try:
        return st.secrets[key_name]
    except FileNotFoundError:
        return None 
    except KeyError:
        return None 

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GOOGLE_SEARCH_API_KEY = get_secret("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = get_secret("SEARCH_ENGINE_ID")

if not GEMINI_API_KEY:
    with st.sidebar:
        st.warning("偵測到本機執行且未設定 Secrets")
        GEMINI_API_KEY = st.text_input("請輸入 Gemini API Key", type="password")
        GOOGLE_SEARCH_API_KEY = st.text_input("請輸入 Google Search Key", type="password")
        SEARCH_ENGINE_ID = st.text_input("請輸入 Search Engine ID")

# --- 初始化雙客戶端 ---
client_v2 = None
if GEMINI_API_KEY:
    # 1. 初始化舊版 (V1)
    if HAS_V1_SDK:
        genai_v1.configure(api_key=GEMINI_API_KEY)
    
    # 2. 初始化新版 (V2)
    if HAS_V2_SDK:
        try:
            client_v2 = genai_v2.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            st.error(f"V2 SDK 初始化失敗: {e}")

# ==========================================
# 2. 核心：混合呼叫引擎 (The Hybrid Engine)
# ==========================================
def call_gemini_hybrid(contents):
    """
    策略：
    1. 先嘗試用 google-genai 呼叫 gemini-2.5-flash
    2. 失敗則用 google-generativeai 呼叫 gemini-1.5-flash
    """
    
    # --- 策略 A: 優先嘗試 V2 SDK + 2.5-flash ---
    if HAS_V2_SDK and client_v2:
        try:
            # 嘗試呼叫 2.5
            response = client_v2.models.generate_content(
                model="gemini-2.5-flash", 
                contents=contents
            )
            return response.text
        except Exception as e:
            # 如果失敗 (404, 429)，只記錄不報錯，繼續往下走
            # st.toast(f"⚠️ 2.5-flash 呼叫失敗，切換至 1.5 備援...", icon="🔀")
            pass

    # --- 策略 B: 備援使用 V1 SDK + 1.5-flash ---
    if HAS_V1_SDK:
        try:
            model = genai_v1.GenerativeModel("gemini-1.5-flash")
            # 這裡加上簡單的重試機制，防止 1.5 也忙碌
            for i in range(2):
                try:
                    response = model.generate_content(contents)
                    return response.text
                except Exception as e:
                    if "429" in str(e):
                        time.sleep(2)
                        continue
                    else:
                        raise e
        except Exception as e:
            st.warning(f"❌ 所有模型嘗試皆失敗 (V1 & V2): {e}")
            return None
    else:
        st.error("❌ 嚴重錯誤：找不到 google-generativeai 套件，無法執行備援。")
        return None

    return None

# ==========================================
# 3. 側邊欄與狀態設定
# ==========================================
if 'current_analysis_data' not in st.session_state:
    st.session_state.current_analysis_data = None
if 'current_recommend_data' not in st.session_state:
    st.session_state.current_recommend_data = None

with st.sidebar:
    st.header("介面設定")
    version_option = st.radio("選擇 Tier List 版本", ("中文", "英文"), index=0)

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

    st.divider()
    st.header("操作")
    if st.button("清空目前榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.session_state.current_analysis_data = None
        st.session_state.current_recommend_data = None
        st.success("已重置！")
        st.rerun()

# ==========================================
# 4. 功能函式
# ==========================================

def search_google_text(query, mode="analysis"):
    if not GOOGLE_SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        st.error("缺少 Google Search API Key")
        return []
    
    search_suffix = "評價 心得" if mode == "analysis" else "推薦 甜涼 好過"
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': GOOGLE_SEARCH_API_KEY,
        'cx': SEARCH_ENGINE_ID,
        'q': f"北科大 {query} {search_suffix}",
        'num': 8
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200: return []
        data = response.json()
        if 'items' not in data: return []
        return [f"標題:{i.get('title')} \n内容:{i.get('snippet')}" for i in data['items']]
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []

# --- Agent 團隊 (全部改用 call_gemini_hybrid) ---

def agent_data_curator(course_name, raw_data):
    """Agent 1: 資料清理"""
    raw_text = "\n---\n".join([r.replace('\n', ' ') for r in raw_data])
    prompt = f"""
    你是資料清理專家。查詢目標：「{course_name}」。
    請過濾掉廣告、無關資訊，只保留關於課程評價、老師教學風格、分數甜度的真實討論。
    原始資料：{raw_text}
    請直接輸出摘要：
    """
    return call_gemini_hybrid(prompt) or raw_text

def agent_senior_analyst(course_name, curated_data):
    """Agent 2: 首席分析師"""
    prompt = f"""
    你現在是北科大選課權威。請分析課程「{course_name}」。
    已過濾評論：{curated_data}
    
    評分標準：S(神課/必搶), A(頂級/推), B(不錯/普通), C(無聊/涼但沒用), D(大刀/雷)。
    
    請務必輸出純 JSON：
    {{
      "rank": "等級名稱", "tier": "S/A/B/C/D", "score": 分數,
      "reason": "一句話短評", "tags": ["標籤1", "標籤2"], "details": "詳細說明"
    }}
    """
    return call_gemini_hybrid(prompt)

def agent_course_recommender(category, raw_data):
    """Agent 4: 獵頭顧問"""
    raw_text = "\n---\n".join(raw_data)
    prompt = f"""
    你是北科大選課推薦顧問。使用者想找「{category}」類別的好課。
    請閱讀以下搜尋結果，找出評價最好、討論度最高的 3 位老師或課程。
    
    搜尋資料：
    {raw_text}
    
    請務必輸出純 JSON 格式的列表 (List of Objects)：
    [
      {{
        "teacher": "老師姓名 (若無則填課程名)",
        "subject": "具體課程 (e.g. 羽球, 電影欣賞)",
        "reason": "推薦理由 (e.g. 老師人好、不點名、學很多)",
        "stars": "推薦指數 (1-5)"
      }},
      ... (最多3個)
    ]
    """
    return call_gemini_hybrid(prompt)

def agent_json_guardrail(raw_response, is_list=False):
    """Agent 3: 格式審查"""
    if not raw_response: return None
    cleaned_text = raw_response.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned_text)
    except:
        prompt = f"你是JSON修復工具。請修正以下錯誤格式並輸出純JSON:\n{raw_response}"
        res_text = call_gemini_hybrid(prompt)
        if res_text:
            fixed = res_text.replace("```json", "").replace("```", "").strip()
            try: return json.loads(fixed)
            except: return None
        return None

# --- 圖片處理 ---
def load_font(size):
    linux_font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if os.path.exists(linux_font): return ImageFont.truetype(linux_font, size)
    mac_font = "/System/Library/Fonts/PingFang.ttc"
    if os.path.exists(mac_font): return ImageFont.truetype(mac_font, size)
    mac_font_2 = "/System/Library/Fonts/STHeiti Light.ttc"
    if os.path.exists(mac_font_2): return ImageFont.truetype(mac_font_2, size)
    return ImageFont.load_default()

def get_fit_font(draw, text, max_width, max_height, initial_size):
    size = initial_size
    font = load_font(size)
    while size > 10: 
        try:
            l, t, r, b = draw.textbbox((0, 0), text, font=font)
            w, h = r - l, b - t
        except: w, h = draw.textsize(text, font=font)
        if w < max_width and h < max_height: return font, h
        size -= 2
        font = load_font(size)
    return font, max_height

def create_course_card(full_text, size=(150, 150)):
    bg_color = (245, 245, 245, 255)
    border_color = (50, 50, 50, 255)
    img = Image.new('RGBA', size, bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0,0), (size[0]-1, size[1]-1)], outline=border_color, width=3)
    
    parts = full_text.rsplit(' ', 1)
    if len(parts) >= 2:
        course_name, teacher_name = parts[0], parts[1]
    else:
        course_name, teacher_name = full_text, ""

    W, H = size
    PADDING = 8
    target_w = W - (PADDING * 2)
    font_course, h_c = get_fit_font(draw, course_name, target_w, H * 0.6, int(H * 0.45))
    try: l, t, r, b = draw.textbbox((0,0), course_name, font=font_course); w_c = r - l
    except: w_c, _ = draw.textsize(course_name, font=font_course)
    draw.text(((W - w_c) / 2, (H * 0.55 - h_c) / 2), course_name, fill=(0, 0, 0), font=font_course)
    
    if teacher_name:
        font_teacher, h_t = get_fit_font(draw, teacher_name, target_w, H * 0.3, int(H * 0.25))
        try: l, t, r, b = draw.textbbox((0,0), teacher_name, font=font_teacher); w_t = r - l
        except: w_t, _ = draw.textsize(teacher_name, font=font_teacher)
        draw.text(((W - w_t) / 2, (H * 0.75) - (h_t / 2)), teacher_name, fill=(80, 80, 80), font=font_teacher)
    return img

def update_tier_list(course_name, tier_data):
    tier = tier_data.get('tier', 'C').upper()
    if tier not in ['S', 'A', 'B', 'C', 'D']: tier = 'C'
    
    target_path = RESULT_IMAGE_PATH if os.path.exists(RESULT_IMAGE_PATH) else BASE_IMAGE_PATH
    if not os.path.exists(target_path): return False

    try: base_img = Image.open(target_path).convert("RGBA")
    except: 
        if os.path.exists(BASE_IMAGE_PATH):
            base_img = Image.open(BASE_IMAGE_PATH).convert("RGBA")
            st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        else: return False

    W, H = base_img.size
    ROW_H = H / 5  
    START_X = int(W * 0.28)
    CARD_SIZE = int(ROW_H * 0.85) 
    PADDING = 10 
    card_img = create_course_card(course_name, size=(CARD_SIZE, CARD_SIZE))
    
    tier_map = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4}
    row_index = tier_map.get(tier, 3)
    count = st.session_state[SESSION_KEY][tier]
    pos_y = int((row_index * ROW_H) + (ROW_H - CARD_SIZE) / 2)
    pos_x = START_X + (count * (CARD_SIZE + PADDING))
    
    if pos_x + CARD_SIZE > W:
        st.warning(f"{tier} 級已滿！")
        return False

    base_img.alpha_composite(card_img, (pos_x, pos_y))
    base_img.save(RESULT_IMAGE_PATH)
    st.session_state[SESSION_KEY][tier] += 1
    return True

# ==========================================
# 5. 網頁主介面
# ==========================================

st.title("🎓 北科大課程 AI 評價系統")
st.markdown("輸入課程名稱，AI 幫你分析評價 (Tier List) 或 推薦好老師！")

c_input, c_btn1, c_btn2, c_space = st.columns([3, 1, 1, 1], vertical_alignment="bottom")

with c_input:
    query = st.text_input("請輸入課程關鍵字 (e.g. 體育, 通識, 工數)", placeholder="輸入關鍵字...")
with c_btn1:
    btn_analyze = st.button("🔍 分析特定課程", use_container_width=True)
with c_btn2:
    btn_recommend = st.button("✨ 幫我推薦老師", use_container_width=True)

# === 邏輯 A: 分析特定課程 ===
if btn_analyze and query:
    if not GEMINI_API_KEY: st.error("請設定 API Key"); st.stop()
    
    with st.status("🤖 Agent 團隊啟動中 (分析模式)...", expanded=True) as status:
        st.write("🔍 [System] Google 搜尋中...")
        raw_results = search_google_text(query, mode="analysis")
        
        if not raw_results:
            status.update(label="搜尋失敗", state="error"); st.error("找不到資料")
        else:
            with st.expander("📄 查看搜尋原始資料"):
                for r in raw_results: st.text(r); st.divider()
            
            st.write("🕵️‍♂️ [Agent 1] 資料過濾中...")
            curated = agent_data_curator(query, raw_results)
            with st.expander("📝 查看過濾後摘要"): st.write(curated)
            
            st.write("👨‍🏫 [Agent 2] 進行評級...")
            raw_analysis = agent_senior_analyst(query, curated)
            
            st.write("🤖 [Agent 3] 格式驗證...")
            data = agent_json_guardrail(raw_analysis)
            
            if data:
                status.update(label="分析完成！", state="complete")
                st.session_state.current_analysis_data = data 
                st.session_state.current_recommend_data = None 
                update_tier_list(query, data)
            else:
                status.update(label="失敗", state="error")

# === 邏輯 B: 推薦好老師 ===
if btn_recommend and query:
    if not GEMINI_API_KEY: st.error("請設定 API Key"); st.stop()
    
    with st.status("🤖 獵頭顧問啟動中 (推薦模式)...", expanded=True) as status:
        st.write(f"🔍 [System] 正在搜尋「{query}」相關的高評價課程...")
        raw_results = search_google_text(query, mode="recommend")
        
        if not raw_results:
            status.update(label="搜尋失敗", state="error"); st.error("找不到資料")
        else:
            with st.expander("📄 查看搜尋原始資料"):
                for r in raw_results: st.text(r); st.divider()

            st.write("🕵️‍♂️ [Agent 4] 獵頭顧問：正在分析討論串並挑選人選...")
            raw_recs = agent_course_recommender(query, raw_results)
            
            st.write("🤖 [Agent 3] 格式驗證...")
            rec_list = agent_json_guardrail(raw_recs, is_list=True)
            
            if rec_list:
                status.update(label="推薦清單已生成！", state="complete")
                st.session_state.current_recommend_data = rec_list 
                st.session_state.current_analysis_data = None 
            else:
                status.update(label="失敗", state="error")

# === 結果顯示區 ===

if st.session_state.current_recommend_data:
    st.subheader(f"✨ 「{query}」推薦清單")
    rec_cols = st.columns(3)
    for idx, rec in enumerate(st.session_state.current_recommend_data):
        with rec_cols[idx % 3]:
            with st.container(border=True):
                st.markdown(f"### 🏆 {rec.get('teacher', '未知')}")
                st.caption(f"課程: {rec.get('subject', query)}")
                st.markdown(f"**推薦指數:** {'⭐' * int(rec.get('stars', 3))}")
                st.info(rec.get('reason', '無詳細理由'))
                if st.button(f"分析 {rec.get('teacher')}", key=f"btn_rec_{idx}"):
                    st.toast(f"請在上方搜尋欄輸入「{rec.get('teacher')}」進行詳細評級！")

elif st.session_state.current_analysis_data:
    data = st.session_state.current_analysis_data
    st.divider()
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric(label="評級", value=f"{data.get('tier')} 級", delta=f"分數: {data.get('score')}")
        st.caption(f"稱號: {data.get('rank')}")
        st.info(f"💡 {data.get('reason')}")
    with c2:
        st.subheader("詳細評價")
        st.write(data.get('details'))

if os.path.exists(RESULT_IMAGE_PATH):
    st.divider()
    st.subheader(f"🏆 課程排位榜單 ({version_option})")
    import time
    st.image(RESULT_IMAGE_PATH, caption=f"Tier List - {time.time()}", use_column_width=True)
elif os.path.exists(BASE_IMAGE_PATH):
    st.divider()
    st.subheader(f"🏆 課程排位榜單 ({version_option})")
    st.image(BASE_IMAGE_PATH, caption="Empty List", use_column_width=True)
