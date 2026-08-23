import datetime
import json
import os
import re

import fitz
import google.generativeai as genai
import pandas as pd
import requests
import streamlit as st


ICON_PATH = "estimand.png"
CTTI_PATH = "000111598.xlsx"
MODEL_NAME = "gemini-3-flash-preview"
LOCAL_ENDPOINT = "http://localhost:1234/v1/chat/completions"

st.set_page_config(
    page_title="Estimand-Protocol Mapping Tool",
    page_icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    layout="wide",
)


def extract_pdf(uploaded_file):
    if uploaded_file is None:
        return ""
    document = fitz.open(stream=uploaded_file.getvalue(), filetype="pdf")
    return "\n".join(
        f"--- [PAGE {index + 1}] ---\n{page.get_text()}"
        for index, page in enumerate(document)
    )


def format_ice_rows(dataframe):
    if dataframe is None or dataframe.empty:
        return "中間事象は登録されていません。"
    cleaned = dataframe.fillna("")
    rows = []
    for index, row in cleaned.iterrows():
        if not str(row.get("中間事象", "")).strip():
            continue
        rows.append(
            "\n".join(
                [
                    f"ICE {index + 1}: {row.get('中間事象', '')}",
                    f"定義・発生条件: {row.get('定義・発生条件', '')}",
                    f"関連する評価項目: {row.get('関連する評価項目', '')}",
                    f"Strategy: {row.get('Strategy', '')}",
                    f"Strategyの根拠・説明: {row.get('根拠・説明', '')}",
                    f"出典: {row.get('出典', '')}",
                ]
            )
        )
    return "\n\n".join(rows) if rows else "中間事象は登録されていません。"


def load_ctti_reference():
    if not os.path.exists(CTTI_PATH):
        return ""
    try:
        sheets = pd.read_excel(CTTI_PATH, sheet_name=None)
        sheet_name = "日本語訳" if "日本語訳" in sheets else list(sheets.keys())[0]
        frame = sheets[sheet_name]
        columns = ["カテゴリ", "CTQ ファクター", "説明/理由"]
        if all(column in frame.columns for column in columns):
            return frame[columns].dropna(how="all").to_string(index=False)
        return frame.head(100).to_string(index=False)
    except Exception as error:
        st.sidebar.warning(f"CTTI参照データを読み込めませんでした: {error}")
        return ""


def call_ai(prompt, mode, api_key, local_url):
    if mode == "Gemini API":
        if not api_key:
            raise ValueError("Gemini APIキーを設定してください。")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)
        return model.generate_content(prompt).text

    response = requests.post(
        local_url,
        json={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        },
        timeout=300,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def parse_json_response(text):
    """Parse JSON even when the model wraps it in a Markdown code fence."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def result_as_text(result):
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, indent=2)
    return str(result)


def format_display_value(value):
    if value is None or value == "":
        return "―"
    if isinstance(value, list):
        return " / ".join(str(item) for item in value) if value else "―"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def record_heading(record, index):
    record_id = (
        record.get("ID")
        or record.get("規定ID")
        or f"項目 {index + 1}"
    )
    summary = (
        record.get("規定の要約")
        or record.get("確認したい状態")
        or record.get("事象")
        or ""
    )
    summary = str(summary).strip()
    if len(summary) > 70:
        summary = summary[:70] + "…"
    return f"{record_id}｜{summary}" if summary else str(record_id)


def show_table(records, empty_message):
    """Show one vertical table per regulation/observation item."""
    if not records:
        st.info(empty_message)
        return

    for index, record in enumerate(records):
        with st.expander(
            record_heading(record, index),
            expanded=(index == 0),
        ):
            rows = [
                {"項目": key, "内容": format_display_value(value)}
                for key, value in record.items()
            ]
            st.table(pd.DataFrame(rows))


def estimand_context(treatment, population, variable, summary, ice_rows):
    summary_text = summary.strip() if summary.strip() else "未入力（任意項目）"
    return f"""
【関心のある治療条件】
{treatment}

【対象集団】
{population}

【個人レベルの変数】
{variable}

【中間事象とStrategy】
{format_ice_rows(ice_rows)}

【集団レベルの要約（任意・参考情報）】
{summary_text}
"""


if "ice_table" not in st.session_state:
    st.session_state.ice_table = pd.DataFrame(
        columns=[
            "中間事象",
            "定義・発生条件",
            "関連する評価項目",
            "Strategy",
            "根拠・説明",
            "出典",
        ]
    )


with st.sidebar:
    st.header("AI接続設定")
    ai_mode = st.radio("接続モード", ["Gemini API", "Local LLM"])
    api_key = ""
    local_url = LOCAL_ENDPOINT
    if ai_mode == "Gemini API":
        try:
            secret_key = st.secrets.get("GOOGLE_API_KEY", "")
        except Exception:
            secret_key = ""
        use_secret = st.checkbox("Streamlit Secretsを使用", value=bool(secret_key))
        api_key = secret_key if use_secret else st.text_input("Gemini API Key", type="password")
        if api_key:
            st.success("APIキーを読み込みました")
    else:
        local_url = st.text_input("Local API Endpoint", value=LOCAL_ENDPOINT)

    st.divider()
    st.caption("研究用プロトタイプです。外部APIへ未公開・機密情報を送信しないでください。")
    ctti_reference = load_ctti_reference()
    if ctti_reference:
        st.success("CTTI参照データを読み込みました")


title_column, text_column = st.columns([1, 20])
with title_column:
    if os.path.exists(ICON_PATH):
        st.image(ICON_PATH, width=45)
with text_column:
    st.title("Estimand-Protocol Mapping Tool")

st.markdown(
    """
本ツールは、**Estimandに対応する推定結果を意図した意味で解釈するために重要な規定**を文書から探索し、
その規定について**何を観測・確認すべきかの候補**を段階的に整理します。

確認方法、担当者、頻度、中央・サイトモニタリングの選択およびリスク低減策は決定しません。
出力は専門家によるリスク評価と計画策定のための検討材料です。
"""
)
st.warning("AI出力には誤りや過剰な推論が含まれ得ます。必ず原文と照合してください。")

input_tab, regulation_tab, observation_tab, ctq_tab = st.tabs(
    ["1. 入力", "2. 関連規定", "3. 観測情報", "4. CTQ・レポート"]
)

with input_tab:
    st.header("Estimand情報")
    col_a, col_b = st.columns(2)
    with col_a:
        treatment = st.text_area(
            "関心のある治療条件",
            value="ペムブロリズマブ（200 mg、3週ごと静注）と治験薬Xの併用療法",
            height=110,
        )
        population = st.text_area(
            "対象集団",
            value="適格基準を満たし、治験薬が1回以上投与された対象患者",
            height=110,
        )
    with col_b:
        variable = st.text_area(
            "個人レベルの変数",
            value="中央判定による確定された客観的奏効",
            height=110,
        )
        with st.expander("集団レベルの要約（任意・参考情報）"):
            population_summary = st.text_area(
                "集団レベルの要約",
                value="",
                placeholder="例：奏効割合の点推定値と95%信頼区間",
                help="主要なマッピング軸には使用せず、SAPとの整合性確認などの参考情報として扱います。",
            )

    strategy_options = [
        "Treatment policy",
        "Hypothetical",
        "Composite variable",
        "While on treatment",
        "Principal stratum",
        "未決定",
        "その他・複合的な取扱い",
    ]
    source_options = ["Protocol", "SAP", "ProtocolとSAP", "ユーザー入力", "未確認"]
    with st.expander("中間事象（ICE）とStrategyの詳細設定（任意）"):
        st.caption(
            "未入力のまま解析できます。文書から抽出されたICE候補を確認した後、必要に応じて追加してください。"
        )
        edited_ice = st.data_editor(
            st.session_state.ice_table,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            column_config={
                "Strategy": st.column_config.SelectboxColumn(
                    "Strategy", options=strategy_options, required=True
                ),
                "出典": st.column_config.SelectboxColumn(
                    "出典", options=source_options, required=True
                ),
            },
            key="ice_editor",
        )
        st.info(
            "ICE、Strategy、Strategy適用の前提情報、試験実施上の規定は区別して解析します。"
        )
    st.session_state.ice_table = edited_ice

    st.subheader("対象文書")
    protocol_file = st.file_uploader("Protocol PDF（必須）", type="pdf", key="protocol_file")
    sap_file = st.file_uploader("SAP PDF（任意）", type="pdf", key="sap_file")
    st.caption("ProtocolとSAPは別文書として扱い、出典を保持します。SAPの解析記述から実施要件を自動生成しません。")

    if st.button("文書を読み込む", type="primary"):
        if protocol_file is None:
            st.error("Protocol PDFをアップロードしてください。")
        else:
            try:
                st.session_state.protocol_text = extract_pdf(protocol_file)
                st.session_state.sap_text = extract_pdf(sap_file)
                st.session_state.estimand_input = estimand_context(
                    treatment, population, variable, population_summary, edited_ice
                )
                st.session_state.pop("regulation_result", None)
                st.session_state.pop("observation_result", None)
                st.session_state.pop("ctq_result", None)
                message = "Protocolを読み込みました。"
                if sap_file is not None:
                    message += " SAPも別文書として読み込みました。"
                st.success(message)
            except Exception as error:
                st.error(f"PDFの読み込みに失敗しました: {error}")

with regulation_tab:
    st.header("関連規定の抽出")
    st.write(
        "規定を「Estimand関連」と「安全性・被験者保護」に分け、原文に忠実な候補を表で表示します。"
    )

    if "protocol_text" not in st.session_state:
        st.info("先に「1. 入力」で文書を読み込んでください。")
    elif st.button("関連規定を抽出", type="primary"):
        sap_section = (
            st.session_state.sap_text[:60000]
            if st.session_state.get("sap_text")
            else "SAPはアップロードされていません。"
        )
        prompt = f"""
あなたは臨床試験文書のレビューを支援する専門家です。
以下のEstimand情報を手がかりに、Protocolおよび任意のSAPから関連記述を抽出してください。

【目的】
1. Estimandに対応する推定結果の解釈に関連し得る規定を抽出する。
2. 安全性・被験者保護のために重要となり得る規定を、上記とは別に抽出する。

【重要な制約】
- 文書にない規定を一般的なGCP知識や経験から補完しない。
- Strategyは中間事象を踏まえた治療効果の定義上の取扱いであり、現場への実施指示と混同しない。
- SAPの解析上の記述から、施設や担当者が実施すべき要件を新たに作らない。
- ProtocolとSAPの出典を混ぜない。
- 各記述に文書名、章・項番号、ページ番号、短い原文引用を付す。
- 不明な場合は「不明」、記載がない場合は「該当記載なし」とする。
- Estimand関連規定には、「治療条件」「対象集団」「変数」のうち少なくとも1つを必ず対応づける。
- 複数要素に関係する場合は配列に複数記載する。
- 安全性規定はEstimandに無理に関連づけず、関連がある場合だけ対応要素を記載する。

【Estimand情報】
{st.session_state.estimand_input}

【Estimand要素の分類定義】
- 治療条件：投与、変更、休薬、中断、中止、併用条件など、実際に受ける治療に関する規定。
- 対象集団：適格性、除外条件、診断、解析対象集団への所属・採否に関する規定。
- 変数：患者ごとの結果を意図した定義、方法、時点、判定手順で取得・導出するための規定。

【安全性規定の抽出範囲】
治療開始・継続基準、休薬・減量・再開・中止基準、AE/SAEの定義・評価期間・報告、
安全性検査、妊娠・過量投与等の特別な状況、安全性追跡を対象とする。

【ICE候補】
文書に明記されたICE、ICEとなる可能性がある事象、単なる欠測・評価不能、
評価項目を構成するイベントを区別する。Strategyが明記されていなければ推測せず「未記載」とする。

【出力形式】
Markdownを付けず、以下のキーを持つ正しいJSONオブジェクトだけを出力する。
{{
  "estimand_regulations": [
    {{
      "ID": "E-01",
      "Estimand要素": ["治療条件"],
      "関連ICE": "",
      "規定の要約": "",
      "規定種別": "",
      "文書": "Protocol",
      "章・項": "",
      "ページ": "",
      "原文引用": ""
    }}
  ],
  "safety_regulations": [
    {{
      "ID": "S-01",
      "安全性領域": "",
      "Estimand要素との関連": [],
      "関連ICE": "",
      "規定の要約": "",
      "対象": "",
      "文書": "Protocol",
      "章・項": "",
      "ページ": "",
      "原文引用": ""
    }}
  ],
  "ice_candidates": [
    {{
      "事象": "",
      "区分": "ICE候補",
      "主に関係するEstimand要素": [],
      "文書上のStrategy": "未記載",
      "根拠": "",
      "文書": "Protocol",
      "章・項": "",
      "ページ": ""
    }}
  ],
  "notes": []
}}

【Protocol】
{st.session_state.protocol_text[:80000]}

【SAP】
{sap_section}
"""
        try:
            with st.spinner("関連規定を抽出しています..."):
                raw_result = call_ai(prompt, ai_mode, api_key, local_url)
                try:
                    st.session_state.regulation_result = parse_json_response(raw_result)
                    st.session_state.pop("regulation_raw", None)
                except Exception:
                    st.session_state.regulation_raw = raw_result
                    st.session_state.regulation_result = raw_result
                    st.warning("表形式への変換に失敗したため、AIの原文を表示します。")
        except Exception as error:
            st.error(f"解析に失敗しました: {error}")

    if "regulation_result" in st.session_state:
        result = st.session_state.regulation_result
        if isinstance(result, dict):
            st.subheader("Estimand関連規定")
            st.caption("各規定を治療条件・対象集団・変数の少なくとも1つに対応づけています。")
            show_table(result.get("estimand_regulations", []), "関連規定は抽出されませんでした。")

            st.subheader("安全性・被験者保護規定")
            st.caption("Estimandとの関連は、実際に関係する場合だけ表示します。")
            show_table(result.get("safety_regulations", []), "安全性規定は抽出されませんでした。")

            st.subheader("中間事象（ICE）候補")
            st.caption("Strategyは文書に明記されている場合だけ表示し、欠測等とは区別します。")
            show_table(result.get("ice_candidates", []), "ICE候補は抽出されませんでした。")
            if result.get("notes"):
                st.write("補足:", result["notes"])
        else:
            st.markdown(result)

with observation_tab:
    st.header("観測・確認すべき情報の特定")
    st.write(
        "関連規定から、何を観測・確認すべきかの候補を整理します。確認手法やモニタリング計画は決定しません。"
    )

    if "regulation_result" not in st.session_state:
        st.info("先に「2. 関連規定」を実行してください。")
    elif st.button("観測情報の候補を特定", type="primary"):
        prompt = f"""
あなたは臨床試験の品質検討を支援する専門家です。
以下の関連規定の抽出結果だけを根拠として、観測・確認すべき情報の候補を整理してください。

【目的】
1. Estimand関連規定について、対応する試験運用上の状態と観測情報候補を明らかにする。
2. 安全性・被験者保護規定について、必要な状態と観測情報候補を別に整理する。

【重要な制約】
- 中央モニタリング、サイトモニタリング、SDV、SDRなどの確認手法を決定しない。
- 担当者、確認頻度、閾値、KRI、サンプリング、リスク低減策を決定しない。
- 情報をEDCデータに限定しない。原資料、実施記録、判定記録、システムログ、中央検査、画像、薬剤管理、設備、運用状態なども候補にできる。
- 文書に直接記載された内容と、規定から論理的に導いた候補を区別する。
- 一般的なGCP要求事項を根拠なく追加しない。
- 観測方法を特定できない場合も候補から除外せず、「要専門家検討」とする。
- 元の規定IDを必ず保持する。
- Estimand側では元の「治療条件」「対象集団」「変数」の対応を必ず保持する。

【ICEについて必ず分ける項目】
1. ICEの発生を特定する情報
2. Strategy適用の前提となる情報
3. Strategyに対応した解析に必要な情報
4. それらの情報を得るために関係する実施規定

【出力形式】
Markdownを付けず、以下のキーを持つ正しいJSONオブジェクトだけを出力する。
{{
  "estimand_observations": [
    {{
      "規定ID": "E-01",
      "Estimand要素": ["変数"],
      "関連ICE": "",
      "確認したい状態": "",
      "観測・確認すべき情報": "",
      "想定情報源": "",
      "観測単位": "",
      "観測時点": "",
      "導出区分": "規定から論理的に導出",
      "残る不確実性": ""
    }}
  ],
  "safety_observations": [
    {{
      "規定ID": "S-01",
      "安全性領域": "",
      "Estimand要素との関連": [],
      "確認したい状態": "",
      "観測・確認すべき情報": "",
      "想定情報源": "",
      "観測単位": "",
      "観測時点": "",
      "導出区分": "規定から論理的に導出",
      "残る不確実性": ""
    }}
  ],
  "notes": []
}}

【Estimand情報】
{st.session_state.estimand_input}

【関連規定の抽出結果】
{result_as_text(st.session_state.regulation_result)}
"""
        try:
            with st.spinner("観測情報を整理しています..."):
                raw_result = call_ai(prompt, ai_mode, api_key, local_url)
                try:
                    st.session_state.observation_result = parse_json_response(raw_result)
                    st.session_state.pop("observation_raw", None)
                except Exception:
                    st.session_state.observation_raw = raw_result
                    st.session_state.observation_result = raw_result
                    st.warning("表形式への変換に失敗したため、AIの原文を表示します。")
        except Exception as error:
            st.error(f"解析に失敗しました: {error}")

    if "observation_result" in st.session_state:
        result = st.session_state.observation_result
        if isinstance(result, dict):
            st.subheader("Estimand解釈に関する観測情報")
            show_table(
                result.get("estimand_observations", []),
                "Estimand関連の観測情報は抽出されませんでした。",
            )
            st.subheader("安全性・被験者保護に関する観測情報")
            show_table(
                result.get("safety_observations", []),
                "安全性関連の観測情報は抽出されませんでした。",
            )
            if result.get("notes"):
                st.write("補足:", result["notes"])
        else:
            st.markdown(result)

with ctq_tab:
    st.header("CTQ・リスク候補とレポート")
    st.write("観測情報までの結果を基に、必要な場合だけCTQ・リスク候補を整理します。")

    if "observation_result" not in st.session_state:
        st.info("先に「3. 観測情報」を実行してください。")
    elif st.button("CTQ・リスク候補を整理"):
        reference_section = ctti_reference[:30000] if ctti_reference else "CTTI参照データなし"
        prompt = f"""
あなたは臨床試験のRBQM検討を支援する専門家です。
以下の結果を基に、専門家がレビューすべきCTQ要因とリスク候補を整理してください。

【制約】
- CTQ要因を単一データ名、単一手順、個別逸脱名として表現しない。
- Estimandの解釈、安全性・被験者保護、試験運営を別区分にする。
- 一般論で補完せず、提示された規定と観測情報から導ける候補に限定する。
- モニタリング手法、担当者、頻度、閾値、リスク低減策は決定しない。
- 候補ごとに根拠と、専門家が確認すべき不確実性を示す。

【CTTI参照情報】
{reference_section}

【関連規定】
{result_as_text(st.session_state.regulation_result)}

【観測・確認すべき情報】
{result_as_text(st.session_state.observation_result)}
"""
        try:
            with st.spinner("CTQ・リスク候補を整理しています..."):
                st.session_state.ctq_result = call_ai(prompt, ai_mode, api_key, local_url)
        except Exception as error:
            st.error(f"解析に失敗しました: {error}")

    if "ctq_result" in st.session_state:
        st.markdown(st.session_state.ctq_result)

    available_results = any(
        key in st.session_state
        for key in ["regulation_result", "observation_result", "ctq_result"]
    )
    if available_results:
        now = datetime.datetime.now()
        report_parts = [
            "【Estimand-Protocol Mapping Report】",
            f"生成日時: {now:%Y-%m-%d %H:%M:%S}",
            "=" * 60,
            "■ 入力Estimand情報",
            st.session_state.get("estimand_input", ""),
        ]
        if "regulation_result" in st.session_state:
            report_parts.extend(
                ["=" * 60, "■ 関連規定", result_as_text(st.session_state.regulation_result)]
            )
        if "observation_result" in st.session_state:
            report_parts.extend(
                [
                    "=" * 60,
                    "■ 観測・確認すべき情報",
                    result_as_text(st.session_state.observation_result),
                ]
            )
        if "ctq_result" in st.session_state:
            report_parts.extend(["=" * 60, "■ CTQ・リスク候補", st.session_state.ctq_result])

        st.download_button(
            "解析結果をテキストでダウンロード",
            data="\n\n".join(report_parts),
            file_name=f"Estimand_Mapping_{now:%Y%m%d_%H%M}.txt",
            mime="text/plain",
        )

st.divider()
st.caption(
    "研究用プロトタイプ：本ツールの出力は臨床・統計・規制上の判断、モニタリング計画または公式記録を代替しません。"
)
