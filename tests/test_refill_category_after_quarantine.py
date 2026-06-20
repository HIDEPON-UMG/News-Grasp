from __future__ import annotations

import json
from pathlib import Path

from tools.refill_category_after_quarantine import refill_category


ISSUE = "2026-06-20"


def _record(url: str, idx: int) -> dict:
    return {
        "date": ISSUE,
        "genre": "AI",
        "title": f"title {idx}",
        "summary": f"summary {idx}",
        "url": url,
        "thumb": "https://example.com/thumb.jpg",
        "source": "Example",
        "published": ISSUE,
        "date_evidence_source": "rss_pubDate",
    }


def _write_fixture(repo: Path) -> None:
    digest = repo / "digest" / "AI" / f"{ISSUE}-AI.md"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        "\n".join(
            [
                "# AI",
                "- title 1 https://example.com/a1",
                "- title 2 https://bad.example.com/dead",
                "- title 3 https://example.com/a3",
                "- title 4 https://example.com/a4",
                "- title 5 https://example.com/a5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    records = repo / "tmp" / "newsroom" / ISSUE / "ai.records.jsonl"
    records.parent.mkdir(parents=True)
    rows = [
        _record("https://example.com/a1", 1),
        _record("https://bad.example.com/dead", 2),
        _record("https://example.com/a3", 3),
        _record("https://example.com/a4", 4),
        _record("https://example.com/a5", 5),
    ]
    records.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    audit = repo / "data" / "search_audit" / ISSUE / "ai.json"
    audit.parent.mkdir(parents=True)
    audit.write_text(
        json.dumps({"category_id": "ai", "date": ISSUE, "candidates_total": 6, "selected_total": 5, "dropped": []}),
        encoding="utf-8",
    )


def test_refill_category_replaces_quarantined_url_from_reserve(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    candidate_dir = tmp_path / "build" / "deduped-candidates"
    candidate_dir.mkdir(parents=True)
    candidate = _record("https://example.com/reserve", 6)
    candidate["title"] = "reserve title"
    (candidate_dir / "ai_candidates.jsonl").write_text(json.dumps(candidate, ensure_ascii=False) + "\n", encoding="utf-8")

    result = refill_category(
        repo_root=tmp_path,
        date=ISSUE,
        category="ai",
        bad_urls=["https://bad.example.com/dead"],
        candidate_dir=candidate_dir,
        txid="tx1",
    )

    assert result["ok"] is True
    assert result["mode"] == "refilled"
    assert (tmp_path / "build" / "repair-transactions" / ISSUE / "tx1" / "before").exists()
    records_text = (tmp_path / "tmp" / "newsroom" / ISSUE / "ai.records.jsonl").read_text(encoding="utf-8")
    digest_text = (tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md").read_text(encoding="utf-8")
    audit = json.loads((tmp_path / "data" / "search_audit" / ISSUE / "ai.json").read_text(encoding="utf-8"))

    assert "https://bad.example.com/dead" not in records_text
    assert "https://bad.example.com/dead" not in digest_text
    assert "https://example.com/reserve" in records_text
    assert "https://example.com/reserve" in digest_text
    assert audit["selected_total"] == 5
    assert audit["dropped"]


def test_refill_category_blocks_shortfall_below_three(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    bad_urls = [
        "https://bad.example.com/dead",
        "https://example.com/a3",
        "https://example.com/a4",
    ]

    result = refill_category(
        repo_root=tmp_path,
        date=ISSUE,
        category="ai",
        bad_urls=bad_urls,
        candidate_dir=tmp_path / "build" / "deduped-candidates",
        txid="tx-short",
    )

    assert result["ok"] is False
    assert result["reason"] == "blocked_refill_unresolved"
