"""アプリ全体の設定値（モデル一覧・既定値）。"""

from __future__ import annotations

from dataclasses import dataclass

APP_TITLE = "AI Writing Studio"
APP_ICON = "✍️"


@dataclass(frozen=True)
class ModelSpec:
    """選択できる Gemini モデル 1 件分の情報。"""

    id: str
    label: str
    note: str


# 一般的な文章生成向けの安定モデルを並べている。
# 新しいモデルが出たらここに 1 行足すだけで UI に反映される。
MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="gemini-3.6-flash",
        label="Gemini 3.6 Flash（バランス型・推奨）",
        note="速度と品質のバランスが良い既定モデル。迷ったらこれ。",
    ),
    ModelSpec(
        id="gemini-3.7-flash",
        label="Gemini 3.7 Flash（最新・高性能）",
        note="長文や複雑な指示に強い最新モデル。品質重視のとき。",
    ),
    ModelSpec(
        id="gemini-3.5-flash",
        label="Gemini 3.5 Flash（安定）",
        note="安定した性能。大量生成向け。",
    ),
    ModelSpec(
        id="gemini-3.5-flash-lite",
        label="Gemini 3.5 Flash Lite（最速・低コスト）",
        note="下書きの量産や短文の生成向け。",
    ),
)

DEFAULT_MODEL = "gemini-3.6-flash"

MODELS_BY_ID = {m.id: m for m in MODELS}

# thinking_level: モデルがどれだけ考えてから書き始めるか。
THINKING_LEVELS: dict[str, str] = {
    "minimal": "最小 — 最速。定型文向け",
    "low": "低 — 既定。日常的な執筆向け",
    "medium": "中 — 構成を練りたいとき",
    "high": "高 — じっくり考える。難しいテーマ向け",
}

DEFAULT_THINKING_LEVEL = "low"


def estimate_max_output_tokens(target_chars: int) -> int:
    """目安文字数から max_output_tokens を余裕を持って見積もる。

    日本語は 1 文字あたり 1 トークン前後になることが多いため 3 倍に加えて
    思考トークン分のバッファを足す。少なすぎると途中で切れるので多めに取る。
    """
    return min(32_768, max(2_048, target_chars * 3 + 4_096))
