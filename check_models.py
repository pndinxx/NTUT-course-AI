import streamlit as st
import google.generativeai as genai
import os

st.title("🕵️‍♂️ Gemini 模型偵測器")

# 1. 嘗試讀取 API Key
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.success("✅ 成功讀取 API Key (來自 secrets.toml)")
except:
    st.warning("⚠️ 讀取不到 secrets，請手動輸入")
    api_key = st.text_input("請輸入 Gemini API Key", type="password")

if st.button("開始列出模型 (List Models)"):
    if not api_key:
        st.error("❌ 沒有 Key，無法查詢")
        st.stop()

    # 2. 設定 Key
    try:
        genai.configure(api_key=api_key)
        
        st.info("正在向 Google 查詢您的帳號可用模型...")
        
        # 3. 呼叫 list_models
        models = list(genai.list_models())
        
        st.write(f"🔍 總共找到 {len(models)} 個模型：")
        
        found_flash = False
        
        for m in models:
            # 只顯示支援「文字生成 (generateContent)」的模型
            if 'generateContent' in m.supported_generation_methods:
                st.code(f"name: {m.name}\nversion: {m.version}\ndisplay_name: {m.display_name}")
                
                if "flash" in m.name:
                    found_flash = True

        st.divider()
        if found_flash:
            st.success("🎉 恭喜！你的帳號有 Flash 模型權限！請複製上方有 'flash' 字樣的完整 name (例如 models/gemini-1.5-flash)。")
        else:
            st.error("😱 你的帳號似乎沒有 Flash 模型的權限？這很罕見，可能是 API Key 的專案設定問題，或者是免費版額度被鎖了。")

    except Exception as e:
        st.error(f"❌ 查詢失敗: {e}")
        st.write("這代表你的 google-generativeai 套件版本可能還是舊的，或者網路/Key有問題。")
