"""DeepDiveの公開本文とTTS入力から内部transportを除去する共通境界。"""

from __future__ import annotations

import json
import re
from html import unescape


# claim-source/value markerは記事本文に保持するが、公開表示・音声入力へは渡さない。
_INTERNAL_COMMENT_RE = re.compile(
    r"<!--\s*(?:claim-source|value|evidence|support)\s*:[\s\S]*?-->",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_MARKER_RE = re.compile(
    r"\bvalue\s*:\s*[a-z0-9_]+\s+"
    r"evidence\s*:\s*[^\s]+(?:\s+support\s*:\s*[^\s]+)?",
    re.IGNORECASE,
)
_TRANSPORT_JSON_RE = re.compile(
    r"\{[^{}\r\n]{0,65536}?"
    r"\"(?:claimId|claim|sourceUrl|evidence|support|value)\"\s*:"
    r"[^{}\r\n]{0,65536}?\}",
    re.IGNORECASE,
)
_TRANSPORT_JSON_KEYS = frozenset(
    {"claimId", "claim", "sourceUrl", "evidence", "support", "value"}
)
# Markdown制御片は本文へ渡さず、リンク表示名だけを公開本文として残す。
_MARKDOWN_LINK_RE = re.compile(
    r"\[([^\]\r\n]+)\]\(\s*[^)\r\n]+\)",
)
_BARE_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"^```[^\r\n]*\r?\n.*?^```\s*$", re.DOTALL | re.MULTILINE)
_RAW_FENCE_RE = re.compile(r"```")


def _decoded(value: str) -> str:
    """HTML entity化されたtransportもraw形式と同じ判定へそろえる。"""

    return unescape(str(value))


def _internal_comment_matches(value: str) -> bool:
    return _INTERNAL_COMMENT_RE.search(value) is not None


def _transport_json_spans(value: str) -> list[tuple[int, int]]:
    """有効なJSON objectのうち内部transport keyを含む範囲を返す。"""

    decoder = json.JSONDecoder()
    spans: list[tuple[int, int]] = []
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(value, index)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and _TRANSPORT_JSON_KEYS.intersection(payload):
            spans.append((index, end))
    return spans


def _contains_transport_json(value: str) -> bool:
    return bool(_TRANSPORT_JSON_RE.search(value) or _transport_json_spans(value))


def _strip_transport_json(value: str) -> str:
    for start, end in reversed(_transport_json_spans(value)):
        value = value[:start] + value[end:]
    return _TRANSPORT_JSON_RE.sub("", value)


def contains_internal_metadata(value: str) -> bool:
    """公開surfaceへ内部metadataまたはraw Markdown制御片が露出しているか判定する。"""

    text = _decoded(value)
    return bool(
        _internal_comment_matches(text)
        or _INTERNAL_MARKER_RE.search(text)
        or _contains_transport_json(text)
        or _CODE_FENCE_RE.search(text)
        or _RAW_FENCE_RE.search(text)
        or _MARKDOWN_LINK_RE.search(text)
    )


def strip_internal_metadata(value: str) -> str:
    """内部comment、transport JSON、fence、Markdownリンク構文を除去する。

    Markdownリンクは表示名を残して、公開本文の意味を削らず制御構文だけを落とす。
    それ以外のtransport片は本文に相当する公開文を持たないため、fragment全体を除く。
    """

    text = _decoded(value)
    text = _CODE_FENCE_RE.sub("", text)
    text = _RAW_FENCE_RE.sub("", text)
    text = _INTERNAL_COMMENT_RE.sub("", text)
    text = _strip_transport_json(text)
    text = _INTERNAL_MARKER_RE.sub("", text)
    text = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1).strip(), text)
    text = _BARE_URL_RE.sub("", text)
    return text


# 呼び出し側が用途を明示できる別名。判定ロジックは常に上記共通実装だけを使う。
strip_internal_transport_metadata = strip_internal_metadata
has_internal_metadata = contains_internal_metadata


__all__ = [
    "contains_internal_metadata",
    "has_internal_metadata",
    "strip_internal_metadata",
    "strip_internal_transport_metadata",
]
