"""AI Writing Studio — 個人用の AI ライティングツール（Streamlit + Gemini API）。

起動: streamlit run app.py
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from writer import config, gemini, prompts

st.set_page_config(
    page_title=config.APP_TITLE,
    page_icon=config.APP_ICON,
    layout="centered",
)


# --- サイドバー ----------------------------------------------------------------

def render_sidebar() -> tuple[str, str, bool]:
    """モデル設定を描画し、(モデルID, thinking_level, APIキーの有無) を返す。"""
    with st.sidebar:
        st.header("設定")

        # 画面から入力されたキーを毎回セットし直す（再描画されても保持するため）。
        gemini.set_api_key(st.session_state.get("api_key_input"))

        has_key = bool(gemini.get_api_key())
        if has_key:
            st.success("API キー: 設定済み", icon="✅")
        else:
            st.warning("API キーを入力してください", icon="🔑")

        if not has_key or st.session_state.get("api_key_input"):
            st.text_input(
                "Gemini API キー",
                type="password",
                key="api_key_input",
                placeholder="AIza...",
                help="入力したキーはこのアプリの実行中だけ保持され、ファイルには保存されません。",
            )
            st.caption(
                "キーの取得: [Google AI Studio](https://aistudio.google.com/apikey) ／ "
                "毎回入力したくない場合は `.env.example` を `.env` にコピーして "
                "`GEMINI_API_KEY` に記入してください。"
            )

        model_id = st.selectbox(
            "モデル",
            options=[m.id for m in config.MODELS],
            format_func=lambda mid: config.MODELS_BY_ID[mid].label,
            index=[m.id for m in config.MODELS].index(config.DEFAULT_MODEL),
        )
        st.caption(config.MODELS_BY_ID[model_id].note)

        thinking_level = st.select_slider(
            "考える深さ",
            options=list(config.THINKING_LEVELS.keys()),
            value=config.DEFAULT_THINKING_LEVEL,
        )
        st.caption(config.THINKING_LEVELS[thinking_level])

        st.divider()
        st.caption("生成結果は保存されません。必要なものはダウンロードしてください。")

    return model_id, thinking_level, has_key


# --- 生成と結果表示 -------------------------------------------------------------

def _usage_caption(usage: object | None) -> str | None:
    """usage オブジェクトから表示用の文字列を作る（項目名は SDK 依存なので防御的に読む）。"""
    if usage is None:
        return None
    labels = {
        "input_tokens": "入力",
        "output_tokens": "出力",
        "thoughts_tokens": "思考",
        "total_tokens": "合計",
    }
    parts = [
        f"{label} {getattr(usage, attr):,} トークン"
        for attr, label in labels.items()
        if isinstance(getattr(usage, attr, None), int)
    ]
    return " / ".join(parts) if parts else None


def run_generation(
    key: str,
    *,
    system_instruction: str,
    prompt: str,
    target_chars: int,
    model_id: str,
    thinking_level: str,
) -> None:
    """ストリーミング生成を実行し、結果を session_state に保存して再描画する。"""
    result = gemini.StreamResult()

    st.divider()
    st.caption("生成中…")
    try:
        st.write_stream(
            gemini.stream_text(
                model=model_id,
                system_instruction=system_instruction,
                prompt=prompt,
                thinking_level=thinking_level,
                max_output_tokens=config.estimate_max_output_tokens(target_chars),
                result=result,
            )
        )
    except gemini.GeminiError as exc:
        st.error(str(exc))
        return

    if not result.text.strip():
        st.warning("本文が返ってきませんでした。入力を変えてもう一度お試しください。")
        return

    st.session_state[f"result_{key}"] = result.text
    st.session_state[f"usage_{key}"] = _usage_caption(result.usage)
    st.rerun()


def render_result(key: str, filename_stem: str) -> None:
    """保存済みの生成結果を、プレビュー／コピー用／ダウンロードで表示する。"""
    text = st.session_state.get(f"result_{key}")
    if not text:
        return

    st.divider()
    st.subheader("生成結果")

    tab_preview, tab_raw = st.tabs(["プレビュー", "コピー用テキスト"])
    with tab_preview:
        st.markdown(text)
    with tab_raw:
        st.code(text, language="markdown")

    col_dl, col_clear, col_info = st.columns([1, 1, 2])
    with col_dl:
        st.download_button(
            "ダウンロード",
            data=text,
            file_name=f"{filename_stem}_{datetime.now():%Y%m%d_%H%M}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col_clear:
        if st.button("クリア", key=f"clear_{key}", use_container_width=True):
            st.session_state.pop(f"result_{key}", None)
            st.session_state.pop(f"usage_{key}", None)
            st.rerun()
    with col_info:
        info = [f"{len(text):,} 文字"]
        usage = st.session_state.get(f"usage_{key}")
        if usage:
            info.append(usage)
        st.caption(" ／ ".join(info))


# --- 各タブ --------------------------------------------------------------------

def tab_blog(model_id: str, thinking_level: str, has_key: bool) -> None:
    st.subheader("ブログ記事を書く")

    with st.form("form_blog"):
        theme = st.text_input(
            "テーマ *", placeholder="例: Python の非同期処理を初心者向けに解説する"
        )
        audience = st.text_input(
            "想定読者", placeholder="例: Python の基本文法は分かるがWeb開発は未経験の人"
        )

        col_a, col_b = st.columns(2)
        with col_a:
            tone = st.selectbox("トーン", options=list(prompts.BLOG_TONES.keys()))
        with col_b:
            target_chars = st.slider("目安の文字数", 400, 6000, 2000, step=200)

        keywords = st.text_input(
            "盛り込みたいキーワード", placeholder="例: async/await, イベントループ, aiohttp"
        )

        col_s1, col_s2 = st.columns([1, 2])
        with col_s1:
            seo = st.toggle(
                "SEO を意識する",
                value=False,
                help="検索意図を満たす構成にし、タイトル案・メタディスクリプション・FAQ を付けます。",
            )
        with col_s2:
            seo_keyword = st.text_input(
                "対策キーワード", placeholder="例: Python 非同期処理 入門"
            )

        with st.expander("さらに細かく指定する"):
            include_faq = st.checkbox(
                "よくある質問（FAQ）を付ける",
                value=True,
                help="SEO を意識する場合のみ有効です。",
            )
            outline = st.text_area(
                "希望する構成・見出し案", height=100,
                placeholder="例:\n1. 同期処理の限界\n2. async/await の基本\n3. 実際に書いてみる",
            )
            extra = st.text_area(
                "その他の指示", height=80,
                placeholder="例: コード例を必ず 2 つ以上入れる／です・ます調で書く",
            )

        submitted = st.form_submit_button(
            "記事を生成する", type="primary", disabled=not has_key, use_container_width=True
        )

    if submitted:
        if not theme.strip():
            st.warning("テーマを入力してください。")
        else:
            system, prompt = prompts.build_blog(
                theme=theme,
                audience=audience,
                tone=tone,
                target_chars=target_chars,
                keywords=keywords,
                outline=outline,
                extra=extra,
                seo=seo,
                seo_keyword=seo_keyword,
                include_faq=include_faq,
            )
            run_generation(
                "blog",
                system_instruction=system,
                prompt=prompt,
                target_chars=target_chars,
                model_id=model_id,
                thinking_level=thinking_level,
            )

    render_result("blog", "blog")


def tab_email(model_id: str, thinking_level: str, has_key: bool) -> None:
    st.subheader("メール文面を作る")

    with st.form("form_email"):
        purpose = st.text_input(
            "メールの目的 *", placeholder="例: 来週の打ち合わせの日程を再調整したい"
        )
        recipient = st.text_input(
            "宛先（相手と自分の関係）", placeholder="例: 取引先の田中部長。初めてのやり取り"
        )
        points = st.text_area(
            "必ず伝えたい要点", height=100,
            placeholder="例:\n・急な変更のお詫び\n・候補日は水曜午後か金曜午前\n・オンラインでも可",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            tone = st.selectbox("トーン", options=list(prompts.EMAIL_TONES.keys()))
        with col_b:
            length = st.selectbox("分量", options=list(prompts.EMAIL_LENGTHS.keys()), index=1)

        with st.expander("返信メールを書く／その他の指示"):
            original = st.text_area(
                "返信元のメール本文", height=140,
                placeholder="返信したいメールの本文をそのまま貼り付けてください。",
            )
            extra = st.text_area(
                "その他の指示", height=80,
                placeholder="例: 署名は不要／件名は簡潔に",
            )

        submitted = st.form_submit_button(
            "メールを生成する", type="primary", disabled=not has_key, use_container_width=True
        )

    if submitted:
        if not purpose.strip():
            st.warning("メールの目的を入力してください。")
        else:
            system, prompt = prompts.build_email(
                purpose=purpose,
                recipient=recipient,
                points=points,
                tone=tone,
                length=length,
                original=original,
                extra=extra,
            )
            run_generation(
                "email",
                system_instruction=system,
                prompt=prompt,
                target_chars=800,
                model_id=model_id,
                thinking_level=thinking_level,
            )

    render_result("email", "email")


def tab_summary(model_id: str, thinking_level: str, has_key: bool) -> None:
    st.subheader("文章を要約する")

    with st.form("form_summary"):
        source_text = st.text_area(
            "要約したい文章 *", height=260,
            placeholder="記事・議事録・メールなどをそのまま貼り付けてください。",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            style = st.selectbox("要約の形式", options=list(prompts.SUMMARY_FORMATS.keys()))
        with col_b:
            target_chars = st.slider("目安の文字数", 100, 2000, 400, step=50)

        with st.expander("用途を指定して精度を上げる"):
            purpose = st.text_input(
                "要約の用途・想定読者", placeholder="例: 会議に出ていない上司への共有用"
            )
            focus = st.text_input(
                "特に重視してほしい観点", placeholder="例: 決定事項と担当者を漏らさない"
            )

        submitted = st.form_submit_button(
            "要約する", type="primary", disabled=not has_key, use_container_width=True
        )

    if submitted:
        if not source_text.strip():
            st.warning("要約したい文章を貼り付けてください。")
        else:
            system, prompt = prompts.build_summary(
                source_text=source_text,
                style=style,
                target_chars=target_chars,
                purpose=purpose,
                focus=focus,
            )
            run_generation(
                "summary",
                system_instruction=system,
                prompt=prompt,
                target_chars=target_chars,
                model_id=model_id,
                thinking_level=thinking_level,
            )

    render_result("summary", "summary")


# --- エントリポイント -----------------------------------------------------------

def main() -> None:
    st.title(f"{config.APP_ICON} {config.APP_TITLE}")
    st.caption("ブログ・メール・要約を 1 つにまとめた、個人用の AI ライティングツール。")

    model_id, thinking_level, has_key = render_sidebar()

    blog, email, summary = st.tabs(["ブログ記事", "メール文面", "文章要約"])
    with blog:
        tab_blog(model_id, thinking_level, has_key)
    with email:
        tab_email(model_id, thinking_level, has_key)
    with summary:
        tab_summary(model_id, thinking_level, has_key)


main()
