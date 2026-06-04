#!/usr/bin/env python3
"""Claude Code PostToolUse hook: WebSearch / WebFetch で観測した URL を
   `data/_session_urls.json` に自動 append する。

# 役割 (案②-Lite 案③: 2026-06-05 導入)

LLM の規律破り (= prompts で「session ファイルを書き出せ」と命じても破る) を構造的に
封じるための **Lv2 境界 1 箇所集約** (feedback_check_design_principles)。
LLM が WebSearch / WebFetch を呼ぶたびに Claude Code ハーネスが本 hook を発火し、
**LLM 経由でなくハーネス層が直接** session 白リストに URL を追記する。これにより:

  - 「LLM が session ファイルを書き忘れる」が起きえない (= illegal state unrepresentable に近い)
  - articles.jsonl に書かれた URL が session 白リストに無い = WebSearch を通さず記憶から
    書いた URL と確定できる (push 前 gate `audit_all_article_urls.py --gate --match-session`
    がそれを fatal 化する境界として既存)

# stdin JSON スキーマ (Claude Code PostToolUse hook 公式仕様)

  {
    "session_id": "...",
    "transcript_path": "...",
    "cwd": "...",
    "hook_event_name": "PostToolUse",
    "tool_name": "WebSearch" | "WebFetch",
    "tool_input":  { ... },
    "tool_response": { ... }
  }

WebFetch の `tool_input.url` は LLM が指定した URL (= 取得済み)。
WebSearch の `tool_response` 形式は公式 docs に明示なし。dict / list / string の
3 形態をいずれも防御的に処理する (将来仕様変更耐性):

  1. tool_response が dict で `results` が list of dict → 各要素の `url` を採用
  2. それ以外 → tool_response を JSON dump して正規表現で http URL を抽出

# 失敗時の挙動

任意の例外で **exit 0** (= claude を止めない・追跡可能なエラーは stderr に書く)。
hook が失敗しても session ファイルが古いまま残るだけで、後段の audit_all_article_urls
が degrade で動作継続する (現体制 Lv4 のフェイルセーフ)。

# 出力ファイル

`<repo_root>/data/_session_urls.json` を以下スキーマで上書き:

  {"date": "YYYY-MM-DD", "urls": ["https://...", ...]}

date が当日でない or ファイル不在 → 新規作成 (urls は本回追加分のみ)。
date が当日 → 既存 urls に union (重複排除・sorted)。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path


# repo root を hook スクリプト自身の位置から計算する。
# 配置: <repo>/.claude/hooks/append_session_urls.py → parent.parent.parent が <repo>。
# stdin JSON の cwd / 環境変数 CLAUDE_PROJECT_DIR より確実 (Claude が cwd を変える可能性が
# あるため、ファイル位置基準が最も安定)。
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SESSION_FILE = _REPO_ROOT / "data" / "_session_urls.json"
# 発火痕跡ログ。2026-06-05 朝バッチで hook が一度も発火しなかった (= session 空のまま)
# 真因調査の証拠取り用。明日朝以降のバッチでこのファイルを Read することで、
# 「hook は呼ばれたが URL 抽出が空だった」/「hook が一度も呼ばれなかった」の区別がつく。
_AUDIT_LOG = _REPO_ROOT / "data" / "_session_urls.audit.log"

# tool_response 形式が dict/list でない場合の URL 抽出用正規表現。
# trailing punctuation は剥がす (validate_deepdive_urls._strip_url_tail と同方針)。
_URL_RE = re.compile(r"https?://[^\s\"<>\)\]\}]+")
_URL_TRAIL_PUNCT = ".,;:!?)」』』”'\""


def _strip_tail(u: str) -> str:
    return u.rstrip(_URL_TRAIL_PUNCT)


def extract_urls_from_event(event: dict) -> set[str]:
    """PostToolUse event dict から URL を抽出する純粋関数 (テスト容易性のため分離)。

    対象:
      - WebFetch: tool_input.url
      - WebSearch: tool_response 内の URL を防御的に拾う
        1. dict で {results: [...]} なら各要素の url を採用
        2. その他は JSON 全体の文字列から http URL を正規表現抽出
    """
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    tool_response = event.get("tool_response")

    urls: set[str] = set()

    if tool_name == "WebFetch":
        u = tool_input.get("url", "") if isinstance(tool_input, dict) else ""
        if isinstance(u, str) and u.startswith("http"):
            urls.add(_strip_tail(u))
        return urls

    if tool_name == "WebSearch":
        # 1) results 配列を優先 (公式形式の可能性が高い)
        if isinstance(tool_response, dict):
            results = tool_response.get("results")
            if isinstance(results, list):
                for r in results:
                    if isinstance(r, dict):
                        u = r.get("url", "")
                        if isinstance(u, str) and u.startswith("http"):
                            urls.add(_strip_tail(u))
        # 2) フォールバック: 何かしらの dict/str から正規表現抽出
        #    (results キー無し / 別構造 / string-formatted 出力でも URL を拾う)
        if not urls:
            if isinstance(tool_response, str):
                text = tool_response
            else:
                try:
                    text = json.dumps(tool_response, ensure_ascii=False)
                except (TypeError, ValueError):
                    text = ""
            for m in _URL_RE.finditer(text):
                urls.add(_strip_tail(m.group(0)))

    return urls


def merge_into_session_file(new_urls: set[str], today_str: str, session_path: Path) -> dict:
    """既存 session ファイルに new_urls を union して上書き保存する。

    date が today と一致 → union
    date が異なる or ファイル不在 → 当日 date で新規 (urls = new_urls のみ)
    `_note` 等の運用説明フィールドは date が変わっても **carry over** する
    (リポ初期サンプルの説明文が朝バッチの初回 hook で消えるのを防ぐ)

    返り値: 保存した dict (テスト用に明示)
    """
    existing_urls: set[str] = set()
    note: str | None = None
    if session_path.exists():
        try:
            with session_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # _note は date と独立に carry over (運用説明はリポ初期からずっと保持される)
                n = data.get("_note")
                if isinstance(n, str):
                    note = n
                if data.get("date") == today_str:
                    raw_urls = data.get("urls")
                    if isinstance(raw_urls, list):
                        existing_urls = {
                            u for u in raw_urls
                            if isinstance(u, str) and u.startswith("http")
                        }
        except (json.JSONDecodeError, OSError):
            # 破損時は当日 fresh で作り直し (= 安全側に倒す)
            pass

    merged = sorted(existing_urls | new_urls)
    # 順序: _note → date → urls (Python 3.7+ dict 挿入順保持で読みやすさ確保)
    payload: dict = {}
    if note:
        payload["_note"] = note
    payload["date"] = today_str
    payload["urls"] = merged
    session_path.parent.mkdir(parents=True, exist_ok=True)
    # 一時ファイル経由でアトミック書込 (Windows でも os.replace は atomic)
    tmp = session_path.with_suffix(session_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(session_path)
    return payload


def _append_audit(tool_name: str, n_urls: int, note: str = "") -> None:
    """発火痕跡を audit ログに 1 行 append (claude を止めないよう任意例外で握りつぶす)。

    フォーマット: ``YYYY-MM-DDTHH:MM:SS+09:00 tool=WebSearch urls=5 note=...``
    明日朝以降のバッチでこのファイルを Read することで、hook 発火状況が判定できる:
      - audit log が当日分で増えていれば hook 発火している
      - 増えていなければ hook 自体が呼ばれていない (Claude Code 側設定 / matcher / cwd 問題)
    """
    try:
        ts = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"{ts} tool={tool_name} urls={n_urls}"
        if note:
            line += f" note={note}"
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception as e:  # noqa: BLE001
        print(f"append_session_urls: stdin read failed: {e}", file=sys.stderr)
        _append_audit("?", 0, note=f"stdin_read_failed:{e}")
        return 0

    if not raw or not raw.strip():
        _append_audit("?", 0, note="empty_stdin")
        return 0

    try:
        event = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"append_session_urls: invalid stdin JSON: {e}", file=sys.stderr)
        _append_audit("?", 0, note=f"invalid_json:{e}")
        return 0

    if not isinstance(event, dict):
        _append_audit("?", 0, note="event_not_dict")
        return 0

    tool_name = event.get("tool_name", "?")

    try:
        urls = extract_urls_from_event(event)
    except Exception as e:  # noqa: BLE001
        print(f"append_session_urls: extract failed: {e}", file=sys.stderr)
        _append_audit(tool_name, 0, note=f"extract_failed:{e}")
        return 0

    if not urls:
        # 発火はしたが URL が抽出できなかった (= 対象外ツール / response 空) → 痕跡だけ残す
        _append_audit(tool_name, 0, note="no_urls_extracted")
        return 0

    try:
        today_str = date.today().strftime("%Y-%m-%d")
        merge_into_session_file(urls, today_str, _SESSION_FILE)
        _append_audit(tool_name, len(urls))
    except Exception as e:  # noqa: BLE001
        print(f"append_session_urls: write failed: {e}", file=sys.stderr)
        _append_audit(tool_name, len(urls), note=f"write_failed:{e}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
