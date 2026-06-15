"""tools.prepare_reporter_candidates の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

from tools.prepare_reporter_candidates import prepare_directory


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def test_prepare_directory_decodes_google_news_url_and_hydrates_thumb(tmp_path: Path) -> None:
    """reporter 前に Google News URL と thumb を決定論的に補完する。"""
    input_dir = tmp_path / "deduped"
    _write_jsonl(
        input_dir / "ai.jsonl",
        [
            {
                "title": "AI story",
                "url": "https://news.google.com/rss/articles/CBMiExample?oc=5",
                "url_norm": "news.google.com/rss/articles/cbmiexample",
                "category": "ai",
            }
        ],
    )

    summary = prepare_directory(
        input_dir,
        google_decoder=lambda url: {"status": "ok", "decoded_url": "https://example.com/ai/story"},
        fetch_ogp_func=lambda url, **kwargs: {"og_image": "https://example.com/ai/thumb.jpg"},
    )

    rows = _read_jsonl(input_dir / "ai.jsonl")
    assert summary["input_count"] == 1
    assert summary["prepared_count"] == 1
    assert summary["dropped_count"] == 0
    assert rows[0]["url"] == "https://example.com/ai/story"
    assert rows[0]["url_norm"] == "https://example.com/ai/story"
    assert rows[0]["thumb"] == "https://example.com/ai/thumb.jpg"


def test_prepare_directory_drops_unresolved_google_news_url(tmp_path: Path) -> None:
    """Google News URL を解決できない候補は reporter に渡さない。"""
    input_dir = tmp_path / "deduped"
    _write_jsonl(
        input_dir / "fx.jsonl",
        [
            {
                "title": "FX story",
                "url": "https://news.google.com/rss/articles/CBMiExample?oc=5",
                "category": "fx",
            }
        ],
    )

    summary = prepare_directory(
        input_dir,
        google_decoder=lambda url: {"status": "error"},
        fetch_ogp_func=lambda url, **kwargs: {"og_image": "https://example.com/thumb.jpg"},
    )

    assert summary["input_count"] == 1
    assert summary["prepared_count"] == 0
    assert summary["dropped_count"] == 1
    assert _read_jsonl(input_dir / "fx.jsonl") == []
