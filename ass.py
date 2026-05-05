import streamlit as st
import google.generativeai as genai
import fitz  # PyMuPDF
import os
from openai import OpenAI  # 追加

# --- ページ設定 ---
st.set_page_config(page_title="Estimand-Protocol Mapping Tool", layout="wide")

st.title("🧬Estimand-Protocol Mapping Tool")
st.caption("プロトコルからエスティマンドを成立させる規定を抽出します（Gemini / ローカルAI対応）")

# --- サイドバー：設定 ---
with st.sidebar:
    st.header("🤖 AI モデル設定")
    ai_source = st.radio("使用するAIを選択してください:", ["Gemini (Cloud)", "LM Studio (Local)"])

    api_key = None
    client_local = None
    model_name = ""

    if ai_source == "Gemini (Cloud)":
        # Gemini用のモデル選択
        model_name = st.selectbox("モデルを選択", ["gemini-3-flash-preview"])
        
        api_key = st.secrets.get("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            st.success(f"Gemini {model_name} 準備完了")
        else:
            st.error("GOOGLE_API_KEYが見つかりません。")
    
    else:
        # LM Studioは自由記述に設定
        local_url = st.text_input("LM Studio Server URL", "http://localhost:1234/v1")
        
        # --- ここを自由記述（text_input）に ---
        model_name = st.text_input("使用するモデルIDを入力", "Llama 3 70B")
        
        st.info("LM StudioでServerを開始し、CORSをEnabledにしてください。")
        client_local = OpenAI(base_url=local_url, api_key="lm-studio")

    st.header("📝 エスティマンド定義")
    tre = st.text_area("i. 関心のある治療", "入力してください")
    pop = st.text_area("ii. 対象集団", "入力してください")
    var = st.text_area("iii. 変数", "入力してください")
    ice = st.text_area("iv. 中間事象の取扱い", "入力してください")
    sum_val = st.text_area("v. 集団レベルでの要約", "入力してください")

# --- PDF抽出関数 ---
def extract_text_with_page_info(file):
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = ""
    for page_num, page in enumerate(doc, 1):
        text += f"\n\n--- [PAGE {page_num}] ---\n\n"
        text += page.get_text()
    return text

# --- メイン機能 ---
st.header("1. プロトコルのアップロード")
uploaded_file = st.file_uploader("治験実施計画書（PDF）を選択してください", type="pdf")

if uploaded_file:
    # 実行条件のチェック
    can_run = (ai_source == "Gemini (Cloud)" and api_key) or (ai_source == "LM Studio (Local)")
    
    if can_run and st.button("解析を開始"):
        with st.spinner(f"{ai_source} で解析中..."):
            try:
                protocol_text = extract_text_with_page_info(uploaded_file)
                
                prompt = f"""
あなたは臨床試験の専門家です。プロトコル全文を解析し、以下の指示に従って情報を抽出してください。
すべての項目において、該当箇所の「章番号（項番号）」に加え、必ず「ページ数」を明記してください。

### 1. エスティマンド解析
i. 関心のある治療：{tre}
ii. 対象集団：{pop}
iii. 変数：{var}
iv. 中間事象（ICE）の取扱い：{ice}
v. 集団レベルでの要約：{sum_val}

### 2. 有害事象（AE）発生時の対応規定
- 治験薬の減量（Dose Reduction）や休薬（Interruption）の基準
- AE/SAEの報告手順・報告期限
- 投与中止（Discontinuation）に至るAEの基準

### 出力ルール
- 各要素の見出し（例：## ii. 対象集団）の直後に詳細解析を記述。
- 末尾には「【該当文章の直接引用】」を設け、原文を抜粋すること。
- 「--- [PAGE X] ---」をページ数の根拠とすること。

---
【解析対象プロトコル本文】
{protocol_text[:50000]} 
"""
                # AIへのリクエスト
                if ai_source == "Gemini (Cloud)":
                    model = genai.GenerativeModel('gemini-3-flash-preview') 
                    response_text = model.generate_content(prompt).text
                else:
                    # LM Studioへのリクエスト
                    completion = client_local.chat.completions.create(
                        model="local-model",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                    )
                    response_text = completion.choices[0].message.content

                st.header("2. 解析結果")
                st.markdown(response_text)
                st.success("解析が完了しました。")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

elif not api_key and ai_source == "Gemini (Cloud)":
    st.warning("Geminiを使用するにはAPIキーが必要です。")
