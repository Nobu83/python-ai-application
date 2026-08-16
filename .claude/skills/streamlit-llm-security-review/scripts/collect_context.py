#!/usr/bin/env python3
"""Streamlit × LLM アプリのセキュリティ点検で、最初に集める事実をまとめて出す。

使い方:
    python3 collect_context.py [プロジェクトのパス]

出すのは「当たりをつけるための材料」であって、指摘そのものではない。
ヒットした行は必ず現物を読んで裏を取ること。

このスクリプト自身が秘密情報を漏らさないよう、次を守っている:
  - .env や secrets.toml の中身は読まない（存在・権限・行数のみ）
  - 出力するソース行は、API キーらしき文字列を伏せ字にしてから出す
標準ライブラリのみで動く。
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

# 点検対象はアプリのコード。仮想環境と、エージェント用ツール（.claude / .agents）は除く。
SKIP_DIRS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules",
    ".mypy_cache", ".pytest_cache", ".claude", ".agents",
}

# 出力時に伏せるパターン。Google / OpenAI / Anthropic 系のキー形状と、引用符に囲まれた長い英数字列。
_REDACT = [
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{10,}"),
    re.compile(r"(?<=['\"])[0-9A-Za-z_\-]{28,}(?=['\"])"),
]

# (ラベル, 正規表現, 補足)
PATTERNS = [
    ("HTML をそのまま描画", r"unsafe_allow_html\s*=\s*True|components\.v1\.html\s*\(",
     "LLM 出力を流していれば XSS 経路"),
    ("session_state を丸ごと表示", r"st\.(write|json|code|text)\s*\(\s*st\.session_state",
     "API キー入力欄の値まで画面に出る"),
    ("環境変数を表示", r"st\.(write|json|code)\s*\(\s*os\.environ|print\s*\(\s*os\.environ", ""),
    ("標準出力・ログ出力", r"^\s*(print\s*\(|logging\.|logger\.)",
     "キー・プロンプト・例外が混ざっていないか確認"),
    ("例外を文字列化", r"str\s*\(\s*exc\b|str\s*\(\s*e\b|\{exc\}|\{e\}|traceback\.",
     "UI に流していればリクエスト URL 経由でキーが出る恐れ"),
    ("API キーの参照", r"api[_\-]?key|API[_\-]?KEY|getenv|environ\[", ""),
    ("パスワード型の入力欄", r"type\s*=\s*['\"]password['\"]", "キー入力欄にあるべき"),
    ("外部入力の入口", r"st\.(text_area|text_input|file_uploader|chat_input)\s*\(", ""),
    ("入力の文字数上限", r"max_chars\s*=", "外部入力の入口に付いているか"),
    ("LLM 出力の描画先", r"st\.(markdown|write|write_stream)\s*\(", ""),
    ("コード実行・シェル", r"\b(eval|exec)\s*\(|subprocess|os\.system|pickle\.loads?\s*\(",
     "LLM 出力が到達するなら重大"),
    ("ファイル書き込み", r"open\s*\([^)]*['\"][wa]|\.write_text\s*\(|\.write_bytes\s*\(", ""),
    ("外部への送信", r"\b(requests|httpx|urllib\.request|aiohttp)\b", ""),
    ("待ち受け設定", r"server\.address|server\.port|enableXsrfProtection|enableCORS|0\.0\.0\.0|headless", ""),
    ("公開ホスティングの痕跡", r"\bngrok\b|cloudflared|localtunnel|streamlit\.app|huggingface", ""),
]

SECRET_FILES = [".env", ".env.local", ".env.production", ".streamlit/secrets.toml", "secrets.toml"]


def redact(line: str) -> str:
    for pat in _REDACT:
        line = pat.sub("***REDACTED***", line)
    return line


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".venv")]
        for name in filenames:
            if name.endswith(suffixes):
                found.append(Path(dirpath) / name)
    return sorted(found)


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def run(cmd: list[str], cwd: Path) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)
        return out.stdout.strip()
    except Exception:
        return ""


def report_overview(root: Path) -> list[Path]:
    section("1. 対象")
    print(f"プロジェクト: {root}")

    py_files = iter_files(root, (".py",))
    print(f"Python ファイル: {len(py_files)} 件")

    req = root / "requirements.txt"
    if req.exists():
        print(f"\n{req.name}:")
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                print(f"  {line.strip()}")
    else:
        print("\nrequirements.txt: なし（依存関係の把握元を別途探すこと）")

    for doc in ("README.md", "CLAUDE.md"):
        print(f"{doc}: {'あり（設計上の前提を必ず読む）' if (root / doc).exists() else 'なし'}")
    return py_files


def report_settings(root: Path) -> None:
    section("2. Streamlit の設定と公開範囲")

    cfg = root / ".streamlit" / "config.toml"
    if cfg.exists():
        print(f"{cfg.relative_to(root)}: あり")
        for i, line in enumerate(cfg.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip():
                print(f"  {i}: {line}")
    else:
        print(".streamlit/config.toml: なし")
        print("  → server.address が未設定なら、Streamlit は全インターフェースで待ち受ける。")
        print("    ローカル専用の想定なら要指摘。")

    listen = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], root)
    hits = [ln for ln in listen.splitlines() if ":85" in ln or "python" in ln.lower()]
    if hits:
        print("\n現在の待ち受け（lsof より。*:8501 なら全インターフェース）:")
        for ln in hits[:10]:
            print(f"  {ln}")
    else:
        print("\n現在の待ち受け: 8501 番台で待ち受け中のプロセスは見つからず（未起動の可能性）")


def report_secret_files(root: Path) -> None:
    section("3. 秘密情報ファイル（中身は読まない）")

    gitignore = root / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
    if gitignore.exists():
        patterns = [ln.strip() for ln in ignored.splitlines() if ln.strip() and not ln.startswith("#")]
        print(f".gitignore: あり / {patterns}")
        for name in (".env", "secrets.toml"):
            if name not in ignored:
                print(f"  → '{name}' の記述が見当たらない。作られたときに追跡されないか要確認")
    else:
        print(".gitignore: なし（秘密情報ファイルを作った時点で追跡される）")

    present = [rel for rel in SECRET_FILES if (root / rel).exists()]
    if not present:
        print(f"\n秘密情報ファイル: なし（探した対象: {', '.join(SECRET_FILES)}）")
        print("  → キーは環境変数か画面入力から来ているはず。取得経路をコードで確認すること")

    for rel in present:
        path = root / rel
        mode = stat.S_IMODE(path.stat().st_mode)
        lines = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        covered = any(part and part in ignored for part in (rel, Path(rel).name))
        print(f"{rel}: あり / 権限 {mode:o} / {lines} 行 / .gitignore: {'covered' if covered else '未カバー'}")
        if mode & 0o077:
            print("  → 他ユーザーが読める権限。chmod 600 を推奨")
        if not covered:
            print("  → .gitignore に入っていない。コミット済みでないか git 側も確認")

    example = root / ".env.example"
    if example.exists():
        text = example.read_text(encoding="utf-8", errors="replace")
        suspicious = [ln for ln in text.splitlines() if re.search(r"AIza[\w\-]{10,}|sk-[\w\-]{10,}", ln)]
        print(f".env.example: あり / 実キーらしき記載: {'あり（要確認）' if suspicious else 'なし'}")

    if (root / ".git").exists():
        tracked = run(["git", "ls-files"], root)
        leaked = [f for f in tracked.splitlines() if re.search(r"(^|/)\.env($|\.)|secrets\.toml", f)]
        print(f"\ngit: 管理下 / 追跡されている秘密情報ファイル: {leaked or 'なし'}")
        if leaked:
            print("  → .gitignore を直すだけでは消えない。履歴を確認し、キー再発行の要否を判断")
    else:
        print("\ngit: 未初期化（履歴経由の漏えいは対象外）")


def report_patterns(root: Path, py_files: list[Path]) -> None:
    section("4. 該当箇所（現物を読んで裏を取ること）")

    targets = py_files + iter_files(root, (".toml", ".md", ".sh", ".yaml", ".yml"))
    targets = [p for p in targets if ".claude/skills" not in str(p)]

    for label, pattern, note in PATTERNS:
        regex = re.compile(pattern)
        hits = []
        for path in targets:
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"  {path.relative_to(root)}:{i}: {redact(line.strip())[:110]}")
            except Exception:
                continue
        header = f"\n[{label}] {len(hits)} 件" + (f" — {note}" if note else "")
        print(header)
        for h in hits[:12]:
            print(h)
        if len(hits) > 12:
            print(f"  … 他 {len(hits) - 12} 件")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not root.is_dir():
        print(f"ディレクトリが見つかりません: {root}", file=sys.stderr)
        return 1

    py_files = report_overview(root)
    report_settings(root)
    report_secret_files(root)
    report_patterns(root, py_files)

    print("\n" + "=" * 70)
    print("以上は材料。指摘にする前に、該当行を読み、成立条件を自分で確認すること。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
