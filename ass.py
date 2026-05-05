import streamlit as st
import google.generativeai as genai
import fitz  # PyMuPDF
import os
from openai import OpenAI

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
        model_name = st.selectbox("モデルを選択", ["gemini-3-flash-preview"])
        api_key = st.secrets.get("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            st.success(f"Gemini {model_name} 準備完了")
        else:
            st.error("GOOGLE_API_KEYが見つかりません。")
    
    else:
        local_url = st.text_input("LM Studio Server URL", "http://localhost:1234/v1")
        st.info("LM StudioでServerを開始し、CORSをEnabledにしてください。")
        client_local = OpenAI(base_url=local_url, api_key="lm-studio")

    st.header("📝 エスティマンド定義")
    tre = st.text_area("i. 関心のある治療", "入力してください")
    pop = st.text_area("ii. 対象集団", "入力してください")
    var = st.text_area("iii. 変数", "入力してください")
    ice = st.text_area("iv. 中間事象の取扱い", "入力してください")
    sum_val = st.text_area("v. 集団レベルでの要約", "入力してください")

# --- 補助関数 ---
def extract_text_with_page_info(file):
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = ""
    for page_num, page in enumerate(doc, 1):
        text += f"\n\n--- [PAGE {page_num}] ---\n\n"
        text += page.get_text()
    return text

def split_text(text, chunk_size=4000):
    return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]

# --- メイン機能 ---
st.header("1. プロトコルのアップロード")
uploaded_file = st.file_uploader("治験実施計画書（PDF）を選択してください", type="pdf")

if uploaded_file:
    can_run = (ai_source == "Gemini (Cloud)" and api_key) or (ai_source == "LM Studio (Local)")
    
    if can_run and st.button("解析を開始"):
        with st.spinner(f"{ai_source} で解析中..."):
            try:
                protocol_text = extract_text_with_page_info(uploaded_file)
                
                # --- プロンプト本体 (修正不可のため変数として保持) ---
                # ※実行時に {} 部分を format で流し込みます
                base_prompt_template = """
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
- エスティマンドの各要素（関心のある治療、対象集団、変数、中間事象の取り扱い）および有害事象発生の対応規定についてそれぞれの規定を記述。複数ある場合はそれぞれ記述。ただし優先順位は下記とする。
1. その要素の「定義」そのものに関する規定
   （例：FASの定義、PFSの定義など）
2. その定義を成立させるために直接必要な守るべき条件と規定が満たされない場合に影響を受けるエスティマンド要素を記述
   （例：投与実績、イベント判定ルールなど）
3. 上記に該当しない周辺的な運用規定は原則として含めない
   （例：スケジュール、一般的な運用手順など）
- それらの規定に対して守るべき条件を記述
- それらの規定が守られているかどうか観測するために必要なデータ（例：投与記録、報告日時）

### 【基本原則】
- 各要素の見出し（例：## ii. 対象集団）の直後に詳細解析を記述。
- 末尾には「【該当文章の直接引用】」を設け、原文を抜粋すること。
- 「--- [PAGE X] ---」をページ数の根拠とすること。
- プロトコルに明示されている情報のみを抽出すること
- 推測・補完・一般知識の付加を禁止する
- 該当箇所が不明確な場合は「不明」と明記する
- 「守るべき条件」は規定文の直接的な言い換えのみとする
- 規定に記載されていない条件（適格基準、スケジュール等）を補完してはならない

【抽出ルール】
- 各項目ごとに章番号とページ番号を必ず記載
- 最後に原文を引用する
- 要約や総括は出力しない
---
【解析対象プロトコル本文】
{target_text} 
"""

                # --- AIへのリクエスト分岐 ---
                if ai_source == "Gemini (Cloud)":
                    # Geminiは広大なコンテキストを活かし、一括で送信 (最大10万文字制限に拡張)
                    model = genai.GenerativeModel('gemini-3-flash-preview') 
                    prompt = base_prompt_template.format(
                        tre=tre, pop=pop, var=var, ice=ice, sum_val=sum_val,
                        target_text=protocol_text[:100000]
                    )
                    response_text = model.generate_content(prompt).text

                else:
                    # LM Studio用：チャンク分割処理
                    chunks = split_text(protocol_text, chunk_size=4000)
                    extracted_fragments = []
                    
                    status_text = st.empty()
                    for i, chunk in enumerate(chunks[:10]): # 先頭10チャンクに限定
                        status_text.text(f"チャンク {i+1}/10 を抽出中...")
                        
                        # ローカルLLMが拾い漏らさないための抽出専用指示
                        extract_instruction = f"以下のテキストから、エスティマンド要素（{tre}, {pop}等）やAE規定、モニタリングに関連する箇所を、後で解析するために【原文のまま】抽出してください。該当がなければ「なし」と答えてください。\n\n{chunk}"
                        
                        completion = client_local.chat.completions.create(
                            model="local-model",
                            messages=[{"role": "user", "content": extract_instruction}],
                            temperature=0.0,
                        )
                        res = completion.choices[0].message.content
                        if res and "なし" not in res:
                            extracted_fragments.append(res)
                    
                    # 最後に、抽出された断片を「指定のプロンプト」で清書させる
                    if not extracted_fragments:
                        response_text = "指定された範囲内に該当する規定が見つかりませんでした。"
                    else:
                        status_text.text("抽出結果を統合して最終解析中...")
                        final_prompt = base_prompt_template.format(
                            tre=tre, pop=pop, var=var, ice=ice, sum_val=sum_val,
                            target_text="\n\n".join(extracted_fragments)
                        )
                        final_completion = client_local.chat.completions.create(
                            model="local-model",
                            messages=[{"role": "user", "content": final_prompt}],
                            temperature=0.1,
                        )
                        response_text = final_completion.choices[0].message.content

                st.header("2. 解析結果")
                st.markdown(response_text)
                st.success("解析が完了しました。")

            except Exception as e:
                st.error(f"エラーが発生しました: {e}")

elif not api_key and ai_source == "Gemini (Cloud)":
    st.warning("Geminiを使用するにはAPIキーが必要です。")m}] ---\n\n"
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

### 3.モニタリング項目
- エスティマンドの各要素（関心のある治療、対象集団、変数、中間事象の取り扱い）および有害事象発生の対応規定についてそれぞれの規定を記述。複数ある場合はそれぞれ記述。ただし優先順位は下記とする。
1. その要素の「定義」そのものに関する規定
   （例：FASの定義、PFSの定義など）
2. その定義を成立させるために直接必要な守るべき条件と規定が満たされない場合に影響を受けるエスティマンド要素を記述
   （例：投与実績、イベント判定ルールなど）
3. 上記に該当しない周辺的な運用規定は原則として含めない
   （例：スケジュール、一般的な運用手順など）
- それらの規定に対して守るべき条件を記述
- それらの規定が守られているかどうか観測するために必要なデータ（例：投与記録、報告日時）

### 【基本原則】
- 各要素の見出し（例：## ii. 対象集団）の直後に詳細解析を記述。
- 末尾には「【該当文章の直接引用】」を設け、原文を抜粋すること。
- 「--- [PAGE X] ---」をページ数の根拠とすること。
- プロトコルに明示されている情報のみを抽出すること
- 推測・補完・一般知識の付加を禁止する
- 該当箇所が不明確な場合は「不明」と明記する
- 「守るべき条件」は規定文の直接的な言い換えのみとする
- 規定に記載されていない条件（適格基準、スケジュール等）を補完してはならない

【抽出ルール】
- 各項目ごとに章番号とページ番号を必ず記載
- 最後に原文を引用する
- 要約や総括は出力しない
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
