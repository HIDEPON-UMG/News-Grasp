"""tools.prepare_reporter_candidates の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

import tools.prepare_reporter_candidates as prc
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


def test_prepare_directory_does_not_hydrate_google_news_proxy_thumb(tmp_path: Path) -> None:
    """reporter 前の補完で Google News 代理サムネを thumb として採用しない。"""
    input_dir = tmp_path / "deduped"
    _write_jsonl(
        input_dir / "ai.jsonl",
        [
            {
                "title": "AI story",
                "url": "https://example.com/ai/story",
                "category": "ai",
            }
        ],
    )

    summary = prepare_directory(
        input_dir,
        fetch_ogp_func=lambda url, **kwargs: {
            "og_image": "https://lh3.googleusercontent.com/J6_proxy=s0-w300-rw"
        },
    )

    rows = _read_jsonl(input_dir / "ai.jsonl")
    assert summary["prepared_count"] == 1
    assert summary["dropped_count"] == 0
    assert rows[0]["thumb"] is None


def test_prepare_directory_drops_unresolved_google_news_url(tmp_path: Path) -> None:
    """Google News URL を解決できなくても候補全滅にせず reporter に渡す。"""
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
    rows = _read_jsonl(input_dir / "fx.jsonl")
    assert summary["prepared_count"] == 1
    assert summary["dropped_count"] == 0
    assert rows[0]["url"] == "https://news.google.com/rss/articles/CBMiExample?oc=5"
    assert rows[0]["url_norm"] == "https://news.google.com/rss/articles/CBMiExample?oc=5"
    assert rows[0]["google_news_decode_status"] == "unresolved"
    assert rows[0]["url_resolution_action"] == "reporter_must_resolve_canonical"


def test_prepare_directory_keeps_all_google_news_unresolved_candidates_with_date_evidence(tmp_path: Path) -> None:
    """Google News 解決不能が複数あっても全 drop せず、根拠付き候補を reporter に渡す。"""
    input_dir = tmp_path / "deduped"
    _write_jsonl(
        input_dir / "ai.jsonl",
        [
            {
                "title": "AI story one",
                "url": "https://news.google.com/rss/articles/CBMiOne?oc=5",
                "category": "ai",
                "source": "Example",
                "published_date": "2026-06-17",
                "date_evidence_source": "rss-pubdate",
            },
            {
                "title": "AI story two",
                "url": "https://news.google.com/rss/articles/CBMiTwo?oc=5",
                "category": "ai",
                "source": "Example",
                "published_date": "2026-06-17",
                "date_evidence_source": "rss-pubdate",
            },
        ],
    )

    summary = prepare_directory(
        input_dir,
        google_decoder=lambda url: {"status": "error"},
        fetch_ogp_func=lambda url, **kwargs: {},
    )

    rows = _read_jsonl(input_dir / "ai.jsonl")
    assert summary["prepared_count"] == 2
    assert summary["dropped_count"] == 0
    assert all(row["google_news_decode_status"] == "unresolved" for row in rows)
    assert all(row["url_resolution_action"] == "reporter_must_resolve_canonical" for row in rows)
    assert all(row["date_evidence_source"] == "rss-pubdate" for row in rows)


def test_decode_fallback_does_not_require_multiprocessing(monkeypatch) -> None:
    """Windows の Queue/Semaphore 権限問題で候補生成全体を空にしない。"""

    monkeypatch.setattr(prc, "_local_google_news_decode", None)
    monkeypatch.setattr(
        prc,
        "decode_google_news_url",
        lambda _url: "https://example.com/decoded/story",
    )

    assert (
        prc._decode_google_news_url_with_timeout(
            "https://news.google.com/rss/articles/CBMiExample?oc=5",
            0.01,
        )
        == "https://example.com/decoded/story"
    )
