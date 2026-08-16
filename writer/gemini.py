"""Gemini API（google-genai SDK）の薄いラッパー。

UI 層はこのモジュール経由でのみ API を呼ぶ。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator

from dotenv import load_dotenv
from google import genai

load_dotenv()


class GeminiError(RuntimeError):
    """API キー未設定や生成失敗など、UI に見せたいエラー。"""


# .env のテンプレートをそのままコピーした場合の文言。設定済みとみなさない。
_PLACEHOLDERS = {"ここにAPIキーを貼り付け", "your_api_key_here", ""}

# 画面から入力されたキー（.env を作らずに使いたいとき用）。プロセス内にのみ保持する。
_session_api_key: str | None = None


def set_api_key(key: str | None) -> None:
    """画面から入力された API キーを保持する。ファイルには一切書き込まない。"""
    global _session_api_key
    key = (key or "").strip()
    _session_api_key = key if key not in _PLACEHOLDERS else None


def get_api_key() -> str | None:
    """API キーを取得する。画面入力 → 環境変数（.env 含む）の順で探す。"""
    if _session_api_key:
        return _session_api_key
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = (os.getenv(name) or "").strip()
        if value and value not in _PLACEHOLDERS:
            return value
    return None


@lru_cache(maxsize=1)
def _client_for(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def get_client() -> genai.Client:
    api_key = get_api_key()
    if not api_key:
        raise GeminiError(
            "GEMINI_API_KEY が設定されていません。"
            ".env.example を .env にコピーして API キーを記入してください。"
        )
    return _client_for(api_key)


def _friendly_message(exc: Exception) -> str:
    """API のエラーを、原因と対処が分かる日本語に言い換える。"""
    detail = str(exc)
    lowered = detail.lower()

    if "api_key_invalid" in lowered or "api key not valid" in lowered:
        return (
            "API キーが正しくありません。`.env` の GEMINI_API_KEY を確認してください。"
            "（Google AI Studio で再発行できます）"
        )
    if "permission_denied" in lowered or "403" in detail:
        return "この API キーではアクセスできませんでした。キーの権限を確認してください。"
    if "resource_exhausted" in lowered or "429" in detail:
        return "利用上限（レート制限）に達しました。少し待ってから再実行してください。"
    if "not_found" in lowered or "404" in detail:
        return (
            "指定したモデルが見つかりませんでした。"
            "サイドバーで別のモデルを選ぶか、writer/config.py のモデル名を更新してください。"
        )
    if "deadline" in lowered or "timeout" in lowered:
        return "応答がタイムアウトしました。文字数を減らすか、もう一度お試しください。"

    return f"リクエストに失敗しました: {detail}"


@dataclass
class StreamResult:
    """ストリーミング中に得られた本文とメタ情報を受け取る箱。"""

    text: str = ""
    usage: Any = None
    interaction_id: str | None = None
    chunks: list[str] = field(default_factory=list)


def stream_text(
    *,
    model: str,
    system_instruction: str,
    prompt: str,
    thinking_level: str = "low",
    max_output_tokens: int | None = None,
    result: StreamResult | None = None,
) -> Iterator[str]:
    """Gemini にテキスト生成を依頼し、本文の差分を逐次 yield する。

    `result` を渡すと、生成完了後に本文全体と usage が書き込まれる。
    """
    client = get_client()

    generation_config: dict[str, Any] = {"thinking_level": thinking_level}
    if max_output_tokens:
        generation_config["max_output_tokens"] = max_output_tokens

    try:
        stream = client.interactions.create(
            model=model,
            input=prompt,
            system_instruction=system_instruction,
            generation_config=generation_config,
            stream=True,
        )
    except Exception as exc:  # 認証エラー・モデル名誤りなど
        raise GeminiError(_friendly_message(exc)) from exc

    parts: list[str] = []
    try:
        for event in stream:
            event_type = getattr(event, "event_type", None)

            if event_type == "step.delta":
                delta = getattr(event, "delta", None)
                if getattr(delta, "type", None) == "text":
                    chunk = delta.text or ""
                    if chunk:
                        parts.append(chunk)
                        yield chunk

            elif event_type == "error":
                message = getattr(getattr(event, "error", None), "message", None)
                raise GeminiError(f"生成中にエラーが発生しました: {message or event}")

            elif event_type == "interaction.completed":
                interaction = getattr(event, "interaction", None)
                if result is not None and interaction is not None:
                    result.usage = getattr(interaction, "usage", None)
                    result.interaction_id = getattr(interaction, "id", None)
    except GeminiError:
        raise
    except Exception as exc:
        raise GeminiError(_friendly_message(exc)) from exc
    finally:
        if result is not None:
            result.chunks = parts
            result.text = "".join(parts)
