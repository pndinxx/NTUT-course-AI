import streamlit as st
import os
import requests
import json
from google import genai
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# ==========================================
# 1. 設定頁面與 API Keys
# ==========================================
st.set_page_config(page_title="北科大AI課程評價", layout="wide")

# 路徑設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 安全讀取 API Key (防呆機制) ---
def get_secret(key_name):
    try:
        return st.secrets[key_name]
    except FileNotFoundError:
        return None # 本機沒設檔案
    except KeyError:
        return None # 有檔案但沒填這個 Key

GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GOOGLE_SEARCH_API_KEY = get_secret("GOOGLE_SEARCH_API_KEY")
SEARCH_ENGINE_ID = get_secret("SEARCH_ENGINE_ID")

# 如果讀不到 Key，在側邊欄顯示警告輸入框
if not GEMINI_API_KEY:
    with st.sidebar:
        st.warning("偵測到本機執行且未設定 Secrets")
        GEMINI_API_KEY = st.text_input("請輸入 Gemini API Key", type="password")
        GOOGLE_SEARCH_API_KEY = st.text_input("請輸入 Google Search Key", type="password")
        SEARCH_ENGINE_ID = st.text_input("請輸入 Search Engine ID")

# 初始化 Gemini
@st.cache_resource
def get_gemini_client(api_key):
    if not api_key: return None
    try:
        return genai.Client(api_key=api_key)
    except Exception as e:
        st.error(f"Gemini 初始化失敗: {e}")
        return None

client = get_gemini_client(GEMINI_API_KEY)

# ==========================================
# 2. 側邊欄設定 (版本切換邏輯)
# ==========================================
with st.sidebar:
    st.header("介面設定")
    
    # 讓使用者選擇版本
    version_option = st.radio(
        "選擇Tier List版本",
        ("中文", "英文"),
        index=0
    )

    # 根據選擇設定檔案路徑和 Session Key
    if version_option == "中文":
        BASE_IMAGE_FILENAME = "tier_list.png"
        RESULT_IMAGE_FILENAME = "final_tier_list.png"
        SESSION_KEY = "tier_counts_zh" # 中文版專用的計數器
    else:
        BASE_IMAGE_FILENAME = "tier_list_en.png"
        RESULT_IMAGE_FILENAME = "final_tier_list_en.png"
        SESSION_KEY = "tier_counts_en" # 英文版專用的計數器

    # 組合完整路徑
    BASE_IMAGE_PATH = os.path.join(BASE_DIR, BASE_IMAGE_FILENAME)
    RESULT_IMAGE_PATH = os.path.join(BASE_DIR, RESULT_IMAGE_FILENAME)

    # 初始化對應版本的 Session State
    if SESSION_KEY not in st.session_state:
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}

    st.divider()
    
    # 清空按鈕 (只清空目前選擇的版本)
    st.header("操作")
    if st.button("清空目前榜單", type="primary"):
        if os.path.exists(RESULT_IMAGE_PATH):
            os.remove(RESULT_IMAGE_PATH)
        st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        st.success("已重置！")
        st.rerun()

# ==========================================
# 3. 功能函式 (搜尋、Agent 分析、繪圖)
# ==========================================

def search_google_text(query):
    if not GOOGLE_SEARCH_API_KEY or not SEARCH_ENGINE_ID:
        st.error("缺少 Google Search API Key 或 Engine ID")
        return []
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': GOOGLE_SEARCH_API_KEY,
        'cx': SEARCH_ENGINE_ID,
        'q': f"{query} 評價 心得",
        'num': 8
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            st.error(f"Google API 錯誤: {response.status_code}")
            return []
        data = response.json()
        if 'items' not in data: return []
        # 回傳原始字串列表，方便 Agent 處理
        return [f"標題:{i.get('title')} \n内容:{i.get('snippet')}" for i in data['items']]
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []

# --- Agent 函式區 (全部鎖定 2.5-flash) ---

def agent_data_curator(course_name, raw_data):
    """Agent 1: 資料清理探員"""
    # 稍微整理一下輸入格式
    raw_text = "\n---\n".join([r.replace('\n', ' ') for r in raw_data])
    
    prompt = f"""
    你是資料清理專家。使用者想查詢北科大課程「{course_name}」。
    以下是 Google 搜尋到的原始資料，可能包含廣告或雜訊。
    請執行：
    1. 過濾掉與「北科大」或該課程無關的資訊。
    2. 過濾掉補習班廣告。
    3. 只保留包含「評價」、「給分甜度」、「作業量」的相關句子。
    4. 將重點整理成條列式摘要 (Bullet points)。
    
    原始資料：
    {raw_text}
    
    請直接輸出整理後的摘要：
    """
    try:
        res = client.models.generate_content(model="gemini-3-flash", contents=prompt)
        return res.text
    except Exception as e:
        st.warning(f"Agent 1 失敗: {e}")
        return raw_text # 失敗就回傳原始資料

def agent_senior_analyst(course_name, curated_data):
    """Agent 2: 首席分析師"""
    prompt = f"""
    你現在是北科大的選課權威分析師。請根據以下「已過濾的真實評論」來分析課程「{course_name}」。
    
    ### 已過濾評論：
    {curated_data}
    
    ### 評分標準 (Rubric)：
    1. **S級**：幾乎全好評、分數甜、必選。
    2. **A級**：好評居多、學得到東西且分數不錯。
    3. **B級**：評價兩極、或是中規中矩。
    4. **C級**：無聊、涼但也學不到東西、或分數普通。
    5. **D級**：負評居多、大刀、極度雷。

    ### 輸出限制：
    請務必輸出 **純 JSON 格式**，嚴禁使用 Markdown：
    {{
      "rank": "等級名稱 (e.g. 頂級)", 
      "tier": "S/A/B/C/D", 
      "score": 0-100的數值,
      "reason": "犀利的一句話短評", 
      "tags": ["標籤1", "標籤2", "標籤3"],
      "details": "詳細的分析報告，包含給分甜度、作業考試狀況。"
    }}
    """
    try:
        res = client.models.generate_content(model="gemini-3-flash", contents=prompt)
        return res.text
    except Exception as e:
        st.warning(f"Agent 2 失敗: {e}")
        return None

def agent_json_guardrail(raw_response):
    """Agent 3: 格式審查員 (自我修復)"""
    if not raw_response: return None
    
    # 1. 嘗試直接解析
    cleaned_text = raw_response.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        pass 
    
    # 2. 修復機制
    prompt = f"""
    你是一個 JSON 修復工具。以下的文字應該要是 JSON，但格式錯誤。
    請只輸出修正後的標準 JSON，不要有任何其他文字。
    錯誤文字：{raw_response}
    """
    try:
        res = client.models.generate_content(model="gemini-3-flash", contents=prompt)
        fixed_text = res.text.replace("```json", "").replace("```", "").strip()
        return json.loads(fixed_text)
    except Exception as e:
        st.warning(f"Agent 3 失敗: {e}")
        return None

# --- 字體與圖片處理函式 (保持原樣) ---

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
    if not os.path.exists(target_path):
        st.error(f"找不到底圖檔案：{target_path} (請確認是否有上傳對應版本的圖片)")
        return False

    try:
        base_img = Image.open(target_path).convert("RGBA")
    except:
        if os.path.exists(BASE_IMAGE_PATH):
            base_img = Image.open(BASE_IMAGE_PATH).convert("RGBA")
            st.session_state[SESSION_KEY] = {'S': 0, 'A': 0, 'B': 0, 'C': 0, 'D': 0}
        else:
            st.error(f"無法重置，找不到原始底圖：{BASE_IMAGE_PATH}")
            return False

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
        st.warning(f"{tier} 級已滿，無法再貼圖片了！")
        return False

    base_img.alpha_composite(card_img, (pos_x, pos_y))
    base_img.save(RESULT_IMAGE_PATH)
    st.session_state[SESSION_KEY][tier] += 1
    return True

# ==========================================
# 4. 網頁主介面
# ==========================================

st.title("北科大課程 AI 評價系統")
st.markdown("輸入課程名稱，AI 幫你爬文、分析評價，並自動生成Tier List！")

col1, col2, col3 = st.columns([3, 0.5, 1.5], vertical_alignment="bottom")

with col1:
    query = st.text_input("請輸入課程或老師名稱", placeholder="輸入完按 Enter 或搜尋")
with col2:
    search_btn = st.button("搜尋", use_container_width=True)
# col3 空著當右邊的留白

# 主邏輯
if search_btn or query:
    if not query:
        st.warning("請輸入課程名稱！")
    elif not GEMINI_API_KEY or not GOOGLE_SEARCH_API_KEY:
        st.error("請先設定 API Keys")
    else:
        # 使用 status 顯示 Agent 工作狀態
        with st.status("🤖 Agent 團隊啟動中...", expanded=True) as status:
            
            # Step 1: 搜尋
            st.write("🔍 [System] 正在 Google 搜尋原始資料...")
            raw_results = search_google_text(query)
            
            if not raw_results:
                status.update(label="搜尋失敗", state="error")
                st.error("找不到相關評論，請換個關鍵字試試。")
            else:
                # ★★★ 新增：顯示原始資料 (折疊) ★★★
                with st.expander("📄 點擊查看 Google 搜尋到的原始資料", expanded=False):
                    for idx, res in enumerate(raw_results):
                        st.markdown(f"**結果 {idx+1}:**")
                        st.text(res) # 使用 text 比較整齊，不會被 markdown 格式跑版
                        st.divider()

                # Step 2: Agent 1 (資料探員) - 2.5-flash
                st.write("🕵️‍♂️ [Agent 1] 資料探員：正在過濾雜訊與廣告...")
                curated_content = agent_data_curator(query, raw_results)
                
                # ★★★ 新增：顯示整理後的重點 (折疊) ★★★
                with st.expander("📝 點擊查看 Agent 1 整理後的重點摘要", expanded=False):
                    st.markdown(curated_content)

                # Step 3: Agent 2 (首席分析師) - 2.5-flash
                st.write("👨‍🏫 [Agent 2] 首席分析師：正在進行評級與撰寫報告...")
                analysis_raw_text = agent_senior_analyst(query, curated_content)
                
                # Step 4: Agent 3 (格式審查員) - 2.5-flash
                st.write("🤖 [Agent 3] 審查員：正在驗證資料格式...")
                data = agent_json_guardrail(analysis_raw_text)
                
                if data:
                    status.update(label="分析完成！", state="complete")
                    
                    st.divider()
                    c1, c2 = st.columns([1, 2])
                    
                    with c1:
                        st.metric(label="評級", value=f"{data.get('tier')} 級", delta=f"分數: {data.get('score')}")
                        st.caption(f"稱號: {data.get('rank')}")
                        st.info(f"💡 {data.get('reason')}")
                        st.write("🏷️ " + "、".join(data.get('tags', [])))
                    
                    with c2:
                        st.subheader("詳細評價")
                        st.write(data.get('details'))
                    
                    if update_tier_list(query, data):
                        st.success(f"已將「{query}」加入 {data.get('tier')} 級榜單！")
                    
                else:
                    status.update(label="AI 分析失敗 (格式錯誤)", state="error")
                    st.error("分析過程發生錯誤，請重試。")

# 顯示圖片
st.divider()
st.subheader(f"課程排位榜單 ({version_option})")

if os.path.exists(RESULT_IMAGE_PATH):
    import time
    st.image(RESULT_IMAGE_PATH, caption=f"Tier List ({version_option})", use_column_width=True)
elif os.path.exists(BASE_IMAGE_PATH):
    st.image(BASE_IMAGE_PATH, caption="尚未有資料 (Empty)", use_column_width=True)
else:
    st.error(f"找不到底圖 ({BASE_IMAGE_FILENAME})，請確認檔案已上傳至 GitHub/資料夾。")
