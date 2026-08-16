# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

個人用の AI ライティングツール（ブログ記事執筆・メール文面作成・文章要約）。
**Python + Streamlit + Gemini API** で構成された、ローカル実行のみのアプリ。

設計上の制約: **データベースなし・認証なし**。生成結果は永続化せず、
`st.session_state` にのみ保持する（ユーザーはダウンロードで持ち出す）。
この方針は意図的なものなので、DB やログイン機構を追加しないこと。

## Commands

```bash
# セットアップ
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 起動（ブラウザで http://localhost:8501）
.venv/bin/streamlit run app.py

# バックグラウンド起動時の停止
lsof -ti:8501 | xargs kill
```

### スモークテスト

テストスイートは無い。変更後の検証には Streamlit の AppTest を使う
（スクリプトを実際に実行し、描画時の例外を拾える）:

```bash
.venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('app.py', default_timeout=60).run()
print('exceptions:', [str(e.value) for e in at.exception])
print('tabs:', len(at.tabs))
"
```

ウィジェット操作もテストできる（例: API キー入力後にボタンが有効化されるか）:

```python
at.sidebar.text_input(key="api_key_input").input("dummy").run()
print([(b.label, b.disabled) for b in at.button])
```

API エラー経路の確認は、無効なキーで `writer/gemini.py` を直接叩くのが早い:

```bash
GEMINI_API_KEY=invalid .venv/bin/python -c "
from writer import gemini
try: list(gemini.stream_text(model='gemini-3.6-flash', system_instruction='t', prompt='hi'))
except gemini.GeminiError as e: print(e)
"
```

## Architecture

```
app.py            UI 層。Streamlit のみ。SDK を直接呼ばない
writer/config.py  モデル一覧・thinking_level・max_output_tokens の見積もり
writer/gemini.py  Gemini API との唯一の接点。ストリーミングとエラー整形
writer/prompts.py 機能ごとのプロンプト組み立て。(system, prompt) を返す純関数
```

**依存の向きは一方向**: `app.py` → `writer/*`。`writer/` は Streamlit に依存しない
（AppTest なしで単体実行・検証できる）。

### 機能を追加するとき

1. `writer/prompts.py` に `build_xxx(...) -> tuple[str, str]` を追加
2. `app.py` に `tab_xxx()` を追加し、`main()` の `st.tabs([...])` に 1 つ増やす

モデルを増やす場合は `writer/config.py` の `MODELS` に `ModelSpec` を 1 行足すだけ
（UI のセレクトボックスは自動で追随する）。

### Streamlit の再実行モデル（この構成の前提）

Streamlit は**ウィジェットが操作されるたびに `app.py` を上から下まで丸ごと再実行する**。
`main()` が末尾で無条件に呼ばれているのはこのため（`if __name__ == "__main__":` は不要）。

この性質から、以下はすべて同じ理由に由来する:

- **状態は `st.session_state` にしか残らない** — ローカル変数は再実行のたびに消える
- **入力は `st.form` でまとめている** — フォーム内のウィジェットは操作しても再実行を起こさず、
  送信ボタンを押した瞬間にだけ値が確定して再実行される。
  これが無いと、テキストを 1 文字打つたびに全体が再実行されて重くなる
- **`st.form_submit_button` はフォーム内でしか使えない**。逆に「クリア」ボタンのような
  即時反応させたいものはフォームの外に置く（`render_result()` 参照）

### 生成フロー（`app.py`）

`run_generation()` が中核。ここは Streamlit 特有の都合で順序が決まっている:

1. `st.write_stream()` で生成を逐次描画（`writer.gemini.stream_text` のジェネレータ）
2. 完了後、本文と usage を `st.session_state["result_<key>"]` / `["usage_<key>"]` に保存
3. `st.rerun()` を呼ぶ

`st.rerun()` が無いと、再描画のたびにストリーミング表示が消えて結果が失われる。
保存済みの結果は `render_result()` が session_state から読んで
「プレビュー / コピー用テキスト / ダウンロード」として描画する。

`max_output_tokens` は `config.estimate_max_output_tokens()` が
「目安文字数 × 3 + 4096」で多めに見積もる。**思考トークンもこの上限を消費する**ため、
文字数ぴったりに絞ると本文が途中で切れる。生成が尻切れになる報告があったら、
まずここの係数とバッファを疑う。

### API キーの解決順

`writer.gemini.get_api_key()` が唯一の窓口。優先順は
**サイドバー入力（プロセス内のみ保持）→ 環境変数 `GEMINI_API_KEY` / `GOOGLE_API_KEY`（`.env` 含む）**。

`.env.example` のプレースホルダ文言（`ここにAPIキーを貼り付け` など）は
`_PLACEHOLDERS` で「未設定」として扱う。テンプレートをコピーしただけの `.env` を
「設定済み」と誤認させないための仕組みなので、キー判定を書き換えるときは
このガードを維持すること。

サイドバーの入力欄は `st.session_state["api_key_input"]` に入り、毎回の再実行で
`gemini.set_api_key()` に渡し直される（モジュール変数に保持。ファイルには書かない）。

### エラーメッセージ

API 由来の例外は `writer/gemini.py` の `_friendly_message()` で日本語に言い換えてから
`GeminiError` として投げる。UI 側は `GeminiError` だけを捕捉して `st.error()` に流す。
新しいエラー種別（課金停止・地域制限など）に出くわしたら、UI ではなく
`_friendly_message()` に分岐を足すこと。

## Gemini SDK の注意点（google-genai）

このコードは **新しい `client.interactions.create(...)` API** を使っている。
旧来の `client.models.generate_content(...)` とは引数もレスポンス形状も別物なので、
訓練データ由来の記憶で書き換えないこと。実際の型は SDK を直接調べて確認する
（`google.genai.interactions` / `google.genai._gaos.types.interactions`）。

- リクエスト: `model=` / `input=` / `system_instruction=` / `generation_config=` / `stream=True`
- `generation_config` に **`temperature` は存在しない**。使えるのは
  `max_output_tokens`, `thinking_level`（`minimal`/`low`/`medium`/`high`）, `seed`,
  `stop_sequences`, `thinking_summaries`, `tool_choice` など
- ストリーミングのイベント形状:
  `event.event_type == "step.delta"` → `event.delta.type == "text"` → `event.delta.text`。
  他に `"error"` と `"interaction.completed"`（`event.interaction.usage` を持つ）
- 非ストリーミングの本文は `interaction.output_text`（インスタンス属性。クラスには生えていない）

モデル ID は `writer/config.py` に集約。存在しないモデル名は 404 になるため、
新しいモデルを追加するときは推測せず https://ai.google.dev/gemini-api/docs/models で確認する。

## プロンプトの方針（`writer/prompts.py`）

- 出力は日本語。前置き・挨拶を書かせず、成果物そのものだけを返させる
- `_COMMON_RULES` に全機能共通のルール（断定を避ける・定型句を使わない・水増ししない）
- メール生成では、こちらが与えていない日時や固有名詞を捏造させず
  `【要確認: ○○】` と明示させる
- 要約では原文にない情報を足さないことを最優先ルールにしている
- 入力が空の項目は `_optional()` で行ごと落とす（空欄をプロンプトに残さない）

## 現状メモ（解消したら消すこと）

- **実際の文章生成は未検証**。有効な API キーでの生成がまだ一度も通っていない。
  検証済みなのは「アプリの描画」「API へのリクエスト到達（無効キーで 400 を確認）」まで。
  初回の生成で失敗したら、まずモデル ID とリクエスト形状を疑う
- **`.venv` に未使用の `anthropic` パッケージが残っている**。開発初期に Claude API 前提で
  進めかけた名残。`requirements.txt` には含まれていないので実害はないが、
  依存を調べるときは `requirements.txt` を正とすること
- **git 未初期化**。バージョン管理を始める場合、`.gitignore` は作成済み（`.env` を除外済み）

---

**参考**: `~/.codex/config.toml`（OpenAI Codex の設定）が見つかりました。
そこに定義された MCP サーバーやカスタムコマンドを Claude Code に取り込みたい場合は、
`/import` と返信すると取り込み可能な項目が一覧表示されます。
