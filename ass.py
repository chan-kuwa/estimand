import streamlit as st
import google.generativeai as genai
import fitz  # PyMuPDF
import os
import pandas as pd
import requests

# --- ページ設定 ---
# ブラウザのタブアイコンも指定
icon_path = "estimand.png"
if os.path.exists(icon_path):
    st.set_page_config(page_title="Estimand-Protocol Mapping Tool", page_icon=icon_path, layout="wide")
else:
    st.set_page_config(page_title="Estimand-Protocol Mapping Tool", layout="wide")

# --- CTTIデータの読み込み ---
ctti_summary_text = ""
excel_file = '000111598.xlsx'

if os.path.exists(excel_file):
    try:
        df_all = pd.read_excel(excel_file, sheet_name=None)
        sheet_name = '日本語訳' if '日本語訳' in df_all else list(df_all.keys())[0]
        df = df_all[sheet_name]
        ctti_summary_text = df[['カテゴリ', 'CTQ ファクター', '説明/理由']].dropna().to_string(index=False)
        st.sidebar.success(f"✅ 参照データを読み込みました")
    except Exception as e:
        st.sidebar.warning(f"⚠️ ファイル読み込みエラー: {e}")

# --- サイドバー：AI設定 ---
with st.sidebar:
    st.header("🤖 AI 設定")
    ai_mode = st.radio("接続モード:", ["Gemini API", "Local LLM (LM Studio等)"])
    
    api_key = None
    if ai_mode == "Gemini API":
        auth_method = st.radio("APIキーの取得方法:", ["手動入力", "シークレット(Secrets)を使用"])
        if auth_method == "シークレット(Secrets)を使用":
            try:
                api_key = st.secrets.get("GOOGLE_API_KEY")
            except: pass
        else:
            api_key = st.text_input("Gemini API Key を入力", type="password")

        if api_key:
            genai.configure(api_key=api_key)
            st.success("Gemini API 設定完了")
    else:
        local_url = st.text_input("Local API Endpoint", value="http://localhost:1234/v1/chat/completions")

    st.divider()
    st.header("📝 エスティマンド定義")
    tre = st.text_area("i. 関心のある治療", "ペムブロリズマブ（200 mg 3週毎静注）＋治験薬X（20 mg 1日1回経口）の併用療法\n（治験実施計画書より：「ペムブロリズマブを Day1 に200 mg、治験薬Xを20mg 1日1回投与」）", height=100)
    pop = st.text_area("ii. 対象集団", "FAS：\n適格基準を満たし、除外基準に該当せず、治験薬が1回以上投与された MSI-High 進行・再発固形がん患者\n（「FAS…治験薬が1回以上投与された集団」）", height=100)
    var = st.text_area("iii. 変数", "中央判定による 確定された客観意図奏効割合（ORR）\n（「Primary endpoint：中央判定による確定された客観的奏効割合」）", height=100)
    ice = st.text_area("iv. 中間事象の取扱い", "ORR に関する中間事象は以下の方針で扱う：\n\n・治療中止：評価不能は非奏効（treatment policy）\n・後治療開始：後治療を考慮せず最良効果判定（treatment policy）\n・死亡：非奏効\n・画像評価不能：非奏効", height=150)
    sum_val = st.text_area("v. 集団レベルでの要約", "FAS における ORR の 点推定値と二項分布に基づく95%信頼区間\n（「二項分布に基づく95%信頼区間を算出」）", height=80)

# --- AI実行関数 ---
def call_ai(prompt_text):
    if ai_mode == "Gemini API":
        model = genai.GenerativeModel('gemini-3-flash-preview') # 固定
        return model.generate_content(prompt_text).text
    else:
        try:
            res = requests.post(local_url, json={"messages": [{"role": "user", "content": prompt_text}], "temperature": 0.1})
            return res.json()['choices'][0]['message']['content']
        except Exception as e:
            return f"接続エラー: {e}"

# --- メインタイトル（画像とテキストを横並び） ---
if os.path.exists(icon_path):
    col_img, col_tit = st.columns([1, 20])
    with col_img:
        st.image(icon_path, width=45)
    with col_tit:
        st.title("Estimand-Protocol Mapping Tool")
else:
    st.title("📋 Estimand-Protocol Mapping Tool")

# --- 解析実行セクション ---
uploaded_file = st.file_uploader("プロトコル (PDF) をアップロード", type="pdf")

if uploaded_file and st.button("🚀 解析を開始"):
    if ai_mode == "Gemini API" and not api_key:
        st.warning("APIキーを設定してください。")
    else:
        with st.spinner("AI解析中..."):
            doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
            text = "\n".join([f"--- [PAGE {i+1}] ---\n{p.get_text()}" for i, p in enumerate(doc)])
            
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

### 3.モニタリング項目
- エスティマンドの各要素（関心のある治療、対象集団、変数、中間事象の取り扱い）および有害事象発生の対応規定についてそれぞれの規定を記述。
1. その要素の「定義」そのものに関する規定
2. その定義を成立させるために直接必要な守るべき条件
3. 上記規定が守られているかどうか観測するために必要なデータ

### 【基本原則】
- 各要素の見出し（例：## ii. 対象集団）の直後に詳細解析を記述。
- 末尾には「【該当文章の直接引用】」を設け、原文を抜粋すること。
- 「--- [PAGE X] ---」をページ数の根拠とすること。
- 推測・補完を禁止し、不明な場合は「不明」と明記する。
---
{text[:80000]}
"""
            st.session_state['res'] = call_ai(prompt)

if 'res' in st.session_state:
    st.markdown(st.session_state['res'])
    
    if st.button("🔍 CTQ（信頼性リスク）を深掘りする"):
        with st.spinner("CTTIフレームワークと照合中..."):
            ctq_prompt = f"""
あなたは臨床試験のRBQMおよびEstimandの専門家です。

以下の解析結果を基に、
エスティマンドを成立させるために必要な条件群を、
CTTIの考え方を参考にしながら、
「試験解釈の信頼性を成立させるための状態（CTQ要因）」として整理してください。
CTQ要因は、
「〜性」「〜整合性」「〜独立性」
「〜完遂性」「〜維持」
など、
試験の解釈成立状態として表現すること。

単なるデータ名、
単一手順、
個別逸脱名で表現してはならない。
【重要】
ここでいうCTQ要因とは、
エスティマンドの解釈を成立させるために維持されている必要がある「試験運用上の状態」を指す。
個別データ項目、個別手順、個別逸脱ではなく、
「その状態が崩れると、エスティマンドの解釈信頼性が低下する試験運用上の成立状態」を指します。
なお一般的なGCP要求事項、抽象的な品質概念をCTQとして出力してはならない。
【制約】
ICH-GCPや一般的RBQM知識による補完は禁止。
プロトコル本文および参照CTTI情報から
直接導ける内容のみ記述すること。

「重要である」「望ましい」
「適切であるべき」
などの一般的品質論は禁止

【実施内容】
1. 成立条件群を整理
2. それら条件群が支えている「試験解釈上の成立状態」を抽象化
3. その成立状態をCTQ要因として記述
4. そのCTQ要因が崩れる代表的リスク例を簡潔に記述
5. 関連するプロトコル規定・観測データを対応づける

【CTTI参照データ】
{ctti_summary_text}

【対象解析結果】
{st.session_state['res']}

【出力形式】
## CTQ要因名
### 1. このCTQ要因が支える試験解釈
### 2. 関連する成立条件
### 3. 関連する観測データ
### 4. 想定される代表的リスク
### 5. 関連プロトコル規定
"""
            st.session_state['ctq_res'] = call_ai(ctq_prompt)

    if 'ctq_res' in st.session_state:
        st.divider()
        st.header("🤖 CTQ分析レポート (RBQM分析)")
        st.markdown(st.session_state['ctq_res'])
