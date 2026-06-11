#!/usr/bin/env python3
"""Claude Code PostToolUse hook: WebSearch / WebFetch で観測した URL を
   `data/_session_urls.d/{YYYY-MM-DD}/{uuid4}.json` にフラグメントとして書き出す。

# 役割 (案②-Lite 案③: 2026-06-05 導入 / 2026-06-12 フラグメント化)

LLM の規律破り (= prompts で「session ファイルを書き出せ」と命じても破る) を構造的に
封じるための **Lv2 境界 1 箇所集約** (feedback_check_design_principles)。
LLM が WebSearch / WebFetch を呼ぶたびに Claude Code ハーネスが本 hook を発火し、
**LLM 経由でなくハーネス層が直接** session 白リストに URL を追記する。これにより:

  - 「LLM が session ファイルを書き忘れる」が起きえない (= illegal state unrepresentable に近い)
  - articles.jsonl に書かれた URL が session 白リストに無い = WebSearch を通さず記憶から
    書いた URL と確定できる (push 前 gate `audit_all_article_urls.py --gate --match-session`
    がそれを fatal 化する境界として既存)

# フラグメント方式 (2026-06-12 導入: 並列発火 race の構造的解消)

旧方式は共有ファイル `data/_session_urls.json` に対する read → union → replace で、
複数のサブエージェントが並列で WebSearch / WebFetch を呼ぶと「後勝ちで前の URL が消える」
race を持っていた (= 共有可変状態)。本 hook は発火 1 回につき **新規フラグメント 1 ファイル
を作成するだけ** に変える。共有可変状態を消すことで race を表現不能にする (Lv1 解決)。

  - 書き出し先: `<repo>/data/_session_urls.d/{YYYY-MM-DD}/{uuid4.hex}.json`
  - 各フラグメント内容: ``{"date": "YYYY-MM-DD", "urls": ["https://...", ...]}``
  - 読み側 (`audit_all_article_urls.py --match-session`) が当日フラグメント群と
    legacy `data/_session_urls.json` を union して白リストを再構成する

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

`<repo_root>/data/_session_urls.d/{YYYY-MM-DD}/{uuid4.hex}.json` を発火 1 回ごとに
新規作成する (= 共有ファイルへの上書きはしない):

  {"date": "YYYY-MM-DD", "urls": ["https://...", ...]}

URL を 1 件も抽出できなかった発火ではフラグメントを作らない (audit ログには痕跡を残す)。
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path


# repo root を hook スクリプト自身の位置から計算する。
# 配置: <repo>/.claude/hooks/append_session_urls.py → parent.parent.parent が <repo>。
# stdin JSON の cwd / 環境変数 CLAUDE_PROJECT_DIR より確実 (Claude が cwd を変える可能性が
# あるため、ファイル位置基準が最も安定)。
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# フラグメント書き出し先ルート (発火 1 回 = この配下に 1 ファイル新規作成)。
_FRAGMENTS_ROOT = _REPO_ROOT / "data" / "_session_urls.d"
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


def write_fragment(new_urls: set[str], today_str: str, fragments_root: Path) -> Path:
    """発火 1 回分の URL を独立したフラグメント 1 ファイルとして新規作成する。

    共有可変状態 (旧 `_session_urls.json` への read-modify-write) を一切持たないので、
    並列サブエージェントが同時発火しても互いに上書きしない (= race を表現不能化)。
    ファイル名は uuid4.hex でユニークに採るため衝突しない。

    書き出し先: ``fragments_root / today_str / {uuid4.hex}.json``
    内容: ``{"date": today_str, "urls": sorted(new_urls)}`` (ensure_ascii=False, utf-8)

    返り値: 作成したフラグメントのパス (テスト用に明示)。
    """
    day_dir = fragments_root / today_str
    day_dir.mkdir(parents=True, exist_ok=True)
    frag = day_dir / f"{uuid.uuid4().hex}.json"
    frag.write_text(
        json.dumps({"date": today_str, "urls": sorted(new_urls)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return frag


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
        write_fragment(urls, today_str, _FRAGMENTS_ROOT)
        _append_audit(tool_name, len(urls))
    except Exception as e:  # noqa: BLE001
        print(f"append_session_urls: write failed: {e}", file=sys.stderr)
        _append_audit(tool_name, len(urls), note=f"write_failed:{e}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
