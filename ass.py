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
DEMO_PROTOCOL_PATH = "demo_protocol_ra_das28.pdf"

DEMO_TREATMENT = (
    "安定用量MTXに加え、薬剤A 100 mgを週1回24週間皮下投与する治療と、"
    "対応プラセボを同様に投与する治療の比較"
)
DEMO_POPULATION = "選択・除外基準を満たし、無作為化された成人活動性関節リウマチ患者"
DEMO_VARIABLE = "24週時点のDAS28-CRP寛解（DAS28-CRP＜2.6）の有無"
DEMO_SUMMARY = "各群の寛解割合の差（薬剤A群－プラセボ群）および95%信頼区間"
DEMO_ICE_ROWS = [
    {
        "中間事象": "研究薬の永久中止",
        "定義・発生条件": "24週以前に研究薬を永久中止すること",
        "関連する評価項目": "24週時点のDAS28-CRP寛解",
        "Strategy": "Treatment policy",
        "根拠・説明": "中止理由を問わず、24週のDAS28-CRP寛解を評価する。",
        "出典": "Protocol",
    },
    {
        "中間事象": "研究薬の一時休薬または投与遅延",
        "定義・発生条件": "研究薬の一時休薬または予定投与の遅延",
        "関連する評価項目": "24週時点のDAS28-CRP寛解",
        "Strategy": "Treatment policy",
        "根拠・説明": "休薬・遅延を含む実際の治療経過下で24週を評価する。",
        "出典": "Protocol",
    },
    {
        "中間事象": "規定救済治療の開始または増量",
        "定義・発生条件": "12週以降に規定された救済治療を開始または増量すること",
        "関連する評価項目": "24週時点のDAS28-CRP寛解",
        "Strategy": "Hypothetical",
        "根拠・説明": "救済治療がなかった場合の24週寛解を推定対象とする。",
        "出典": "Protocol",
    },
    {
        "中間事象": "24週以前の死亡",
        "定義・発生条件": "無作為化後24週評価以前の死亡",
        "関連する評価項目": "24週時点のDAS28-CRP寛解",
        "Strategy": "Composite variable",
        "根拠・説明": "原因を問わず24週寛解なしとして扱う。",
        "出典": "Protocol",
    },
]

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


def extract_pdf_path(path):
    document = fitz.open(path)
    try:
        return "\n".join(
            f"--- [PAGE {index + 1}] ---\n{page.get_text()}"
            for index, page in enumerate(document)
        )
    finally:
        document.close()


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
        return "", set()
    try:
        sheets = pd.read_excel(CTTI_PATH, sheet_name=None)
        sheet_name = "日本語訳" if "日本語訳" in sheets else list(sheets.keys())[0]
        frame = sheets[sheet_name]
        columns = ["カテゴリ", "CTQ ファクター", "説明/理由"]
        if not all(column in frame.columns for column in columns):
            st.sidebar.warning("CTTI参照データの列を確認してください。")
            return "", set()
        frame = frame[columns].dropna(subset=["カテゴリ", "CTQ ファクター"]).fillna("")
        factors = {
            (str(row["カテゴリ"]).strip(), str(row["CTQ ファクター"]).strip())
            for _, row in frame.iterrows()
        }
        return frame.to_string(index=False), factors
    except Exception as error:
        st.sidebar.warning(f"CTTI参照データを読み込めませんでした: {error}")
        return "", set()


def validate_ctq_references(result, factors):
    if not isinstance(result, dict) or not isinstance(result.get("ctq_candidates"), list):
        return "CTQ候補の形式を確認してください。"
    for index, candidate in enumerate(result["ctq_candidates"], 1):
        if not isinstance(candidate, dict):
            return f"CTQ候補{index}の形式を確認してください。"
        references = candidate.get("CTTI参照項目")
        if not isinstance(references, list) or not references:
            return f"CTQ候補{index}にCTTI参照項目がありません。"
        for reference in references:
            if not isinstance(reference, dict) or (
                str(reference.get("カテゴリ", "")).strip(),
                str(reference.get("CTQ ファクター", "")).strip(),
            ) not in factors:
                return f"CTQ候補{index}のCTTI参照項目が参照表と一致しません。"
            if not str(reference.get("CTTI項目の説明・理由", "")).strip():
                return f"CTQ候補{index}のCTTI参照項目に説明・理由がありません。"
        if not str(candidate.get("本試験の特性", "")).strip():
            return f"CTQ候補{index}に本試験の特性がありません。"
        if not str(candidate.get("CTTI項目との結びつき", "")).strip():
            return f"CTQ候補{index}にCTTI項目との結びつきがありません。"
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
        or record.get("CTQ要因候補")
        or record.get("リスク事象")
        or record.get("確認事項")
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
【関心のある治療】
{treatment}

【対象集団】
{population}

【変数】
{variable}

【中間事象とStrategy】
{format_ice_rows(ice_rows)}

【集団レベルの要約（任意・参考情報）】
{summary_text}
"""


for field_key in [
    "treatment_input",
    "population_input",
    "variable_input",
    "population_summary_input",
]:
    if field_key not in st.session_state:
        st.session_state[field_key] = ""


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
    connection_mode = st.radio(
        "接続方法",
        ["Gemini API（Secrets）", "Gemini API（キーを入力）", "Local LLM"],
    )
    api_key = ""
    local_url = LOCAL_ENDPOINT
    if connection_mode == "Gemini API（Secrets）":
        try:
            api_key = st.secrets.get("GOOGLE_API_KEY", "")
        except Exception:
            api_key = ""
        if api_key:
            st.success("Streamlit SecretsのAPIキーを読み込みました")
        else:
            st.warning("Streamlit SecretsにGOOGLE_API_KEYが設定されていません。")
        ai_mode = "Gemini API"
    elif connection_mode == "Gemini API（キーを入力）":
        api_key = st.text_input(
            "Gemini API Key",
            type="password",
            help="入力したキーはセッション中だけ使用し、保存しません。",
        )
        if api_key:
            st.success("入力されたAPIキーを使用します")
        ai_mode = "Gemini API"
    else:
        local_url = st.text_input("Local API Endpoint", value=LOCAL_ENDPOINT)
        ai_mode = "Local LLM"

    st.divider()
    st.caption("研究用プロトタイプです。外部APIへ未公開・機密情報を送信しないでください。")
    ctti_reference, ctti_factors = load_ctti_reference()
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
Estimandを手がかりに関連する規定を探し、試験結果が正しく解釈されるための成立要件と観測項目を整理します。

結果は、リスク評価やモニタリング計画を検討する際の参考にしてください。
"""
)
st.warning("AI出力には誤りや過剰な推論が含まれ得ます。必ず原文と照合してください。")

st.markdown("""
<style>
.stTabs [data-baseweb="tab-list"] {
    gap: 0.45rem;
    overflow-x: auto;
    flex-wrap: nowrap;
    padding: 0.3rem 0.1rem 0.65rem;
}
.stTabs button[role="tab"] {
    flex-shrink: 0;
    min-height: 2.9rem;
    padding: 0.45rem 0.9rem;
    border: 1px solid #cbd5e1;
    border-radius: 0.7rem;
    background: #f8fafc;
    color: #334155;
    font-weight: 600;
}
.stTabs button[role="tab"][aria-selected="true"] {
    border-color: #b42318;
    background: #fff1f0;
    color: #9f1d15;
}
.stTabs [data-baseweb="tab-highlight"] {
    display: none;
}
</style>
""", unsafe_allow_html=True)
st.caption("下のタブをタップして、1から4の順に進めてください。スマートフォンではタブを横にスクロールできます。")
input_tab, regulation_tab, observation_tab, ctq_tab = st.tabs(
    ["1. 入力", "2. 関連規定", "3. 観測情報", "4. CTQ・レポート"]
)

with input_tab:
    st.header("Estimand情報")

    if st.button("模擬プロトコルを用いて解析する"):
        if not os.path.exists(DEMO_PROTOCOL_PATH):
            st.error("模擬プロトコルが見つかりません。")
        else:
            demo_ice_table = pd.DataFrame(DEMO_ICE_ROWS)
            st.session_state.treatment_input = DEMO_TREATMENT
            st.session_state.population_input = DEMO_POPULATION
            st.session_state.variable_input = DEMO_VARIABLE
            st.session_state.population_summary_input = DEMO_SUMMARY
            st.session_state.ice_table = demo_ice_table
            st.session_state.pop("ice_editor", None)
            st.session_state.protocol_text = extract_pdf_path(DEMO_PROTOCOL_PATH)
            st.session_state.estimand_input = estimand_context(
                DEMO_TREATMENT,
                DEMO_POPULATION,
                DEMO_VARIABLE,
                DEMO_SUMMARY,
                demo_ice_table,
            )
            for result_key in [
                "regulation_result",
                "observation_result",
                "ctq_result",
                "regulation_raw",
                "observation_raw",
                "ctq_raw",
            ]:
                st.session_state.pop(result_key, None)
            st.session_state.demo_protocol_loaded = True
            st.session_state.demo_loaded_notice = True
            st.rerun()

    if st.session_state.pop("demo_loaded_notice", False):
        st.success(
            "模擬プロトコルと対応するEstimandを読み込みました。"
            "「2. 関連規定」から解析を開始できます。"
        )

    if st.session_state.get("demo_protocol_loaded") and os.path.exists(DEMO_PROTOCOL_PATH):
        with st.expander("模擬プロトコルの内容を確認する"):
            st.link_button(
                "模擬プロトコルをPDFで開く",
                "https://github.com/chan-kuwa/estimand/raw/refs/heads/main/demo_protocol_ra_das28.pdf",
            )
            with open(DEMO_PROTOCOL_PATH, "rb") as demo_file:
                st.download_button(
                    "模擬プロトコルをダウンロード",
                    data=demo_file.read(),
                    file_name="demo_protocol_ra_das28.pdf",
                    mime="application/pdf",
                )
            st.text_area(
                "本文のテキスト表示",
                value=st.session_state.protocol_text,
                height=320,
                disabled=True,
            )

    col_a, col_b = st.columns(2)
    with col_a:
        treatment = st.text_area(
            "関心のある治療",
            key="treatment_input",
            height=110,
        )
        population = st.text_area(
            "対象集団",
            key="population_input",
            height=110,
        )
    with col_b:
        variable = st.text_area(
            "変数",
            key="variable_input",
            height=110,
        )
        with st.expander("集団レベルの要約（任意・参考情報）"):
            population_summary = st.text_area(
                "集団レベルの要約",
                key="population_summary_input",
                placeholder="例：奏効割合の点推定値と95%信頼区間",
                help="主要な規定探索の軸には使用せず、Estimandの背景情報として扱います。",
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
    source_options = ["Protocol", "ユーザー入力", "未確認"]
    with st.expander("中間事象（ICE）とStrategyの詳細設定（任意）"):
        st.caption(
            "ICEとStrategyを解析に含める場合のみ入力してください。未入力の場合、ICEに関する解析は行いません。"
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

    if st.button("文書を読み込む", type="primary"):
        if protocol_file is None:
            st.error("Protocol PDFをアップロードしてください。")
        else:
            try:
                st.session_state.protocol_text = extract_pdf(protocol_file)
                st.session_state.demo_protocol_loaded = False
                st.session_state.estimand_input = estimand_context(
                    treatment, population, variable, population_summary, edited_ice
                )
                st.session_state.pop("regulation_result", None)
                st.session_state.pop("observation_result", None)
                st.session_state.pop("ctq_result", None)
                st.success("Protocolを読み込みました。")
            except Exception as error:
                st.error(f"PDFの読み込みに失敗しました: {error}")

with regulation_tab:
    st.header("関連規定の抽出")
    st.write(
        "入力した文書から、Estimandに関わる規定と、有害事象発生時の対応に関する規定を抽出します。"
    )

    if "protocol_text" not in st.session_state:
        st.info("先に「1. 入力」で文書を読み込んでください。")
    elif st.button("関連規定を抽出", type="primary"):
        prompt = f"""
あなたは臨床試験文書のレビューを支援する専門家です。
以下のEstimandの各要素を起点に、Protocolから対応する規定を探してください。

【目的】
1. 関心のある治療、対象集団、変数のそれぞれについて、推定結果の解釈に関係する規定を探して抽出する。
2. 安全性規定として有害事象発生時に対応すべき関連規定を、上記とは別に抽出する。

【重要な制約】
- 文書にない規定を一般的なGCP知識や経験から補完しない。
- 各記述に文書名、章・項番号、ページ番号、短い原文引用を付す。
- 不明な場合は「不明」、記載がない場合は「該当記載なし」とする。
- 入力された各要素の具体的な内容と規定の関係を確認し、関連が説明できる規定だけを抽出する。
- 見つかったEstimand関連規定には、探索の起点となった「関心のある治療」「対象集団」「変数」の要素を記載する。
- 複数要素に関係する場合は配列に複数記載する。対応する規定が見つからない要素については、無理に規定を補わない。

【入力されたEstimand要素】
関心のある治療：{st.session_state.treatment_input}
対象集団：{st.session_state.population_input}
変数：{st.session_state.variable_input}

【安全性規定の抽出範囲】
有害事象が発生した後に適用される、事象の評価、報告、必要な治療、研究薬の休薬・減量・再開・中止、
および事象の追跡に関する規定を対象とする。
通常の安全性検査スケジュール、一般的な被験者保護、妊娠・過量投与等の特別な状況は、
それ自体だけでは抽出対象とせず、発生した有害事象への対応を直接定めている場合に限って抽出する。

【出力形式】
Markdownを付けず、以下のキーを持つ正しいJSONオブジェクトだけを出力する。
{{
  "estimand_regulations": [
    {{
      "ID": "E-01",
      "Estimand要素": [],
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
      "規定の要約": "",
      "対象": "",
      "文書": "Protocol",
      "章・項": "",
      "ページ": "",
      "原文引用": ""
    }}
  ],
  "notes": []
}}

【Protocol】
{st.session_state.protocol_text[:80000]}

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
            st.caption("関心のある治療・対象集団・変数を起点に、文書から見つかった規定を表示します。")
            show_table(result.get("estimand_regulations", []), "関連規定は抽出されませんでした。")

            st.subheader("安全性規定")
            st.caption("有害事象発生時の対応に関する規定を表示します。")
            show_table(result.get("safety_regulations", []), "安全性規定は抽出されませんでした。")

            if result.get("notes"):
                st.write("補足:", result["notes"])
        else:
            st.markdown(result)

with observation_tab:
    st.header("観測・確認すべき情報の特定")
    st.write(
        "抽出した規定をもとに、Estimandに沿って結果を解釈できるか、また有害事象発生時に規定どおり対応しているかを確認するための情報を整理します。"
    )

    if "regulation_result" not in st.session_state:
        st.info("先に「2. 関連規定」を実行してください。")
    elif st.button("観測情報の候補を特定", type="primary"):
        prompt = f"""
あなたは臨床試験の品質検討を支援する専門家です。
以下の関連規定の抽出結果だけを根拠として、観測・確認すべき情報の候補を整理してください。

【目的】
1. Estimand関連規定について、対応する試験運用上の状態と観測情報候補を明らかにする。
2. 安全性規定について、必要な状態と観測情報候補を別に整理する。

【重要な制約】
- 中央モニタリング、サイトモニタリング、SDV、SDRなどの確認手法を決定しない。
- 担当者、確認頻度、閾値、KRI、サンプリング、リスク低減策を決定しない。
- 情報をEDCデータに限定しない。原資料、実施記録、判定記録、システムログ、中央検査、画像、薬剤管理、設備、運用状態なども候補にできる。
- 文書に直接記載された内容と、規定から論理的に導いた候補を区別する。
- 一般的なGCP要求事項を根拠なく追加しない。
- 観測方法を特定できない場合も候補から除外せず、「要専門家検討」とする。
- 元の規定IDを必ず保持する。
- Estimand側では元の「関心のある治療」「対象集団」「変数」の対応を必ず保持する。

【ICEに関する観測情報が必要な場合】
入力されたICEに関係する観測情報が、抽出された規定から導ける場合に限り、次の観点を必要に応じて区別する。
1. ICEの発生を特定する情報
2. Strategy適用の前提となる情報
3. Strategyに対応した解析に必要な情報
4. それらの情報を得るために関係する実施規定
すべての観点を埋める必要はなく、規定から導けない情報や関係を補わない。

【出力形式】
Markdownを付けず、以下のキーを持つ正しいJSONオブジェクトだけを出力する。
{{
  "estimand_observations": [
    {{
      "規定ID": "",
      "Estimand要素": [],
      "関連ICE": "",
      "確認したい状態": "",
      "観測・確認すべき情報": "",
      "想定情報源": "",
      "観測単位": "",
      "観測時点": "",
      "導出区分": "",
      "残る不確実性": ""
    }}
  ],
  "safety_observations": [
    {{
      "規定ID": "",
      "安全性領域": "",
      "確認したい状態": "",
      "観測・確認すべき情報": "",
      "想定情報源": "",
      "観測単位": "",
      "観測時点": "",
      "導出区分": "",
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
            st.subheader("安全性に関する観測情報")
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
    st.write(
        "規定と観測情報をCTTIの項目と照らし合わせ、CTQとリスクの候補を整理します。"
    )

    if "observation_result" not in st.session_state:
        st.info("先に「3. 観測情報」を実行してください。")
    elif st.button("CTQ・リスク候補を整理"):
        if not ctti_factors:
            st.error("CTTI参照データを読み込めません。ファイルを確認してください。")
            st.stop()
        reference_section = ctti_reference
        prompt = f"""
あなたは臨床試験のRBQM検討を支援する専門家です。
以下の結果を基に、専門家がレビューすべきCTQ要因とリスク候補を整理してください。

【制約】
- CTQ要因候補は、試験結果の解釈または安全性のために維持・確認されるべき状態として、「～が確保されている状態」「～が適切に把握できる状態」など、具体的な状態を表す文で記述する。
- 「重要な状態」には、そのCTQ要因について規定から確認したい具体的な状態を記載する。
- ICEや有害事象が発生しないことを当然の維持目標とせず、発生時の取り扱いや必要な情報を確認できる状態を記述する。
- CTQ要因を単一データ名、単一手順、個別逸脱名として表現しない。
- CTQの主目的区分は「Estimand解釈」と「安全性」の2区分だけとする。
- 登録、適格性確認、投与記録、画像転送、SAE報告等の運営プロセスは、
  それが支えるCTQ候補の「関連する実施プロセス」として記述する。
- 一般論で補完せず、提示された規定と観測情報から導ける候補に限定する。
- モニタリング手法、担当者、頻度、閾値、リスク低減策は決定しない。
- 文書に手順があるという理由だけでCTQにしない。試験目的の解釈または被験者の安全性への重要性を根拠で示す。
- SAE報告等は試験運営ではなく、主目的が被験者の安全性確保であれば「安全性規定」に分類する。
- 関心のある治療、対象集団、変数に関係する実施プロセスは「Estimand解釈」に分類し、対応要素を保持する。
- 主目的区分が「安全性」の候補では、「関連Estimand要素」を空配列とし、Estimand要素との関連づけを行わない。
- 入力されていないICEやStrategyを文書から推測しない。
- 記述の不足や不整合を独立した抽出対象にしない。
- 規定から直接導けるCTQ候補と、将来起こり得るリスク事象を混同しない。
- 候補ごとに根拠規定IDと、専門家が確認すべき不確実性を示す。
- 各CTQ候補には、下のCTTI参照データに実在する「カテゴリ」と「CTQ ファクター」の組を必ず記載する。
- 参照した各CTTI項目について、参照データの「説明/理由」に基づき、その項目がどのような内容かを「CTTI項目の説明・理由」に記載する。
- そのCTTI項目が本試験のどの特性（関心のある治療、対象集団、変数、入力されたICE、規定の内容）に関係するか、具体的に説明する。
- 本試験の特性と結びつかないCTTI項目を形式上だけで挙げない。CTTIの一般論だけからCTQを作らない。

【出力形式】
Markdownを付けず、以下のキーを持つ正しいJSONオブジェクトだけを出力する。
{{
  "ctq_candidates": [
    {{
      "ID": "",
      "主目的区分": "",
      "関連Estimand要素": [],
      "CTQ要因候補": "",
      "重要な状態": "",
      "関連する実施プロセス": "",
      "CTTI参照項目": [
        {{"カテゴリ": "", "CTQ ファクター": "", "CTTI項目の説明・理由": ""}}
      ],
      "本試験の特性": "",
      "CTTI項目との結びつき": "",
      "根拠規定ID": [],
      "根拠": "",
      "専門家が確認すべき不確実性": ""
    }}
  ],
  "risk_candidates": [
    {{
      "ID": "",
      "関連CTQ": "",
      "リスク事象": "",
      "根拠規定ID": [],
      "根拠": "",
      "推論区分": "",
      "専門家確認事項": ""
    }}
  ],
  "notes": []
}}

【CTTI参照情報】
{reference_section}

【関連規定】
{result_as_text(st.session_state.regulation_result)}

【観測・確認すべき情報】
{result_as_text(st.session_state.observation_result)}
"""
        try:
            with st.spinner("CTQ・リスク候補を整理しています..."):
                raw_result = call_ai(prompt, ai_mode, api_key, local_url)
                try:
                    parsed_result = parse_json_response(raw_result)
                except Exception:
                    st.session_state.ctq_raw = raw_result
                    st.session_state.pop("ctq_result", None)
                    st.warning("表形式への変換に失敗しました。AIの原文を確認してください。")
                    st.text(raw_result)
                else:
                    reference_error = validate_ctq_references(parsed_result, ctti_factors)
                    if reference_error:
                        st.session_state.pop("ctq_result", None)
                        st.session_state.ctq_raw = raw_result
                        st.error(f"{reference_error} 再実行するか、原文を確認してください。")
                        st.text(raw_result)
                    else:
                        st.session_state.ctq_result = parsed_result
                        st.session_state.pop("ctq_raw", None)
        except Exception as error:
            st.error(f"解析に失敗しました: {error}")

    if "ctq_result" in st.session_state:
        result = st.session_state.ctq_result
        if isinstance(result, dict):
            st.subheader("CTQ要因候補")
            st.caption("主目的はEstimand解釈または安全性です。")
            show_table(
                result.get("ctq_candidates", []),
                "提示された情報からCTQ候補は導出されませんでした。",
            )

            st.subheader("リスク候補")
            st.caption("CTQ候補と区別し、起こり得る事象として表示します。")
            show_table(
                result.get("risk_candidates", []),
                "提示された情報からリスク候補は導出されませんでした。",
            )

            if result.get("notes"):
                st.write("補足:", result["notes"])
        else:
            st.markdown(result)

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
            report_parts.extend(
                [
                    "=" * 60,
                    "■ CTQ・リスク候補",
                    result_as_text(st.session_state.ctq_result),
                ]
            )

        st.download_button(
            "解析結果をテキストでダウンロード",
            data="\n\n".join(report_parts),
            file_name=f"Estimand_Mapping_{now:%Y%m%d_%H%M}.txt",
            mime="text/plain",
        )

st.divider()
st.caption(
    "研究用の試作ツールです。結果だけで臨床・統計・規制上の判断やモニタリング計画を決めないでください。公式記録としても使用しないでください。"
)
