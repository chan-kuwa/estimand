import streamlit as st
import google.generativeai as genai
import fitz  # PyMuPDF
import os

# --- ページ設定 ---
st.set_page_config(page_title="shin-Estimand-Protocol Mapping Tool", layout="wide")

st.title("🧬 TRI Estimand-Protocol Mapping Tool")
st.caption("プロトコルからエスティマンドを成立させる規定をページ数・引用付きで抽出します。AE対応規定も同時に解析します")

# --- サイドバー：設定 ---
with st.sidebar:
    st.header("🔑 API 設定")
    
    # 1. 最初に変数 api_key を定義（初期化）しておく
    api_key = st.secrets.get("GOOGLE_API_KEY") # secretsにあれば取得、なければNone
    
    if api_key:
        genai.configure(api_key=api_key)
        st.success("APIキーが正常に読み込まれました。")
    else:
        st.error("GOOGLE_API_KEYが見つかりません。Streamlit CloudのSecretsを確認してください。")
    
    st.header("📝 エスティマンド定義")
    st.info("解析の起点となる5要素を入力してください。")
    tre = st.text_area("i. 関心のある治療", "入力してください")
    pop = st.text_area("ii. 対象集団", "入力してください")
    var = st.text_area("iii. 変数", "入力してください")
    ice = st.text_area("iv. 中間事象の取扱い", "入力してください")
    sum_val = st.text_area("v. 集団レベルでの要約", "入力してください")

# --- メイン機能：PDF抽出（ページ境界情報を付与） ---
def extract_text_with_page_info(file):
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = ""
    for page_num, page in enumerate(doc, 1):
        # AIがページ数を認識しやすいようにマーカーを挿入
        text += f"\n\n--- [PAGE {page_num}] ---\n\n"
        text += page.get_text()
    return text

# --- 解析実行 ---
st.header("1. プロトコルのアップロード")
uploaded_file = st.file_uploader("治験実施計画書（PDF）を選択してください", type="pdf")

if uploaded_file and api_key:
    if st.button("解析を開始"):
        with st.spinner("プロトコルを解析中..."):
            try:
                # PDFテキスト化
                protocol_text = extract_text_with_page_info(uploaded_file)
                
                # Gemini設定
                genai.configure(api_key=api_key)
                # モデル名は変更せず維持
                model = genai.GenerativeModel('gemini-3-flash-preview')

                # 改良プロンプト
                prompt = f"""
あなたは臨床試験の専門家です。プロトコル全文を解析し、以下の指示に従って情報を抽出してください。
すべての項目において、該当箇所の「章番号（項番号）」に加え、必ず「ページ数」を明記してください。

### 1. エスティマンド解析
入力された以下の5要素について、所在（章・ページ）、識別条件、モニタリング項目を特定してください。
i. 関心のある治療：{tre}
ii. 対象集団：{pop}
iii. 変数：{var}
iv. 中間事象（ICE）の取扱い：{ice}
v. 集団レベルでの要約：{sum_val}

### 2. 有害事象（AE）発生時の対応規定
プロトコル内の以下の規定を特定してください。
- 治験薬の減量（Dose Reduction）や休薬（Interruption）の基準
- AE/SAEの報告手順・報告期限
- 投与中止（Discontinuation）に至るAEの基準

### 出力ルール
- 各要素の見出し（例：## ii. 対象集団）の直後に詳細解析を記述すること。
- 解析の末尾には、必ず「【該当文章の直接引用】」という項目を設け、プロトコル内の原文をそのまま抜粋すること。
- プロトコル内の「--- [PAGE X] ---」という記述をページ数の根拠として使用すること。

---
【解析対象プロトコル本文】
{protocol_text[:80000]} 
"""
                # 解析実行
                response = model.generate_content(prompt)
                
                st.header("2. 解析結果")
                st.markdown(response.text)
                st.success("解析が完了しました。")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

elif not api_key:
    st.warning("サイドバーで Gemini API Key を入力してください。")
