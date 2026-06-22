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
                "### [91] title 1",
                "🔗 [元記事](https://example.com/a1)",
                "---",
                "### [82] title 2",
                "🔗 [元記事](https://bad.example.com/dead)",
                "---",
                "### [73] title 3",
                "🔗 [元記事](https://example.com/a3)",
                "---",
                "### [64] title 4",
                "🔗 [元記事](https://example.com/a4)",
                "---",
                "### [55] title 5",
                "🔗 [元記事](https://example.com/a5)",
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


def test_refill_category_blocks_empty_result_after_quarantine(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    bad_urls = [
        "https://example.com/a1",
        "https://bad.example.com/dead",
        "https://example.com/a3",
        "https://example.com/a4",
        "https://example.com/a5",
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


def test_refill_category_allows_single_survivor_with_shortfall_reason(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    bad_urls = [
        "https://bad.example.com/dead",
        "https://example.com/a3",
        "https://example.com/a4",
        "https://example.com/a5",
    ]

    result = refill_category(
        repo_root=tmp_path,
        date=ISSUE,
        category="ai",
        bad_urls=bad_urls,
        candidate_dir=tmp_path / "build" / "deduped-candidates",
        txid="tx-single",
    )

    assert result["ok"] is True
    assert result["mode"] == "shortfall"
    assert result["selected_total"] == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "tmp" / "newsroom" / ISSUE / "ai.records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    audit = json.loads((tmp_path / "data" / "search_audit" / ISSUE / "ai.json").read_text(encoding="utf-8"))

    assert len(records) == 1
    assert records[0]["quality_shortfall_reason"] == "reserve candidates exhausted after quarantine"
    assert audit["selected_total"] == 1
    assert audit["quality_shortfall_reason"] == "reserve candidates exhausted after quarantine"


def test_refill_category_skips_audit_dropped_reserve_candidate(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    candidate_dir = tmp_path / "build" / "deduped-candidates"
    candidate_dir.mkdir(parents=True)
    rejected = _record("https://example.com/rejected-reserve", 6)
    (candidate_dir / "ai_candidates.jsonl").write_text(json.dumps(rejected, ensure_ascii=False) + "\n", encoding="utf-8")
    audit_path = tmp_path / "data" / "search_audit" / ISSUE / "ai.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["dropped"].append({"url": "https://example.com/rejected-reserve", "reason": "stale candidate"})
    audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    result = refill_category(
        repo_root=tmp_path,
        date=ISSUE,
        category="ai",
        bad_urls=[
            "https://bad.example.com/dead",
            "https://example.com/a3",
            "https://example.com/a4",
            "https://example.com/a5",
        ],
        candidate_dir=candidate_dir,
        txid="tx-dropped-reserve",
    )

    records_text = (tmp_path / "tmp" / "newsroom" / ISSUE / "ai.records.jsonl").read_text(encoding="utf-8")
    digest_text = (tmp_path / "digest" / "AI" / f"{ISSUE}-AI.md").read_text(encoding="utf-8")

    assert result["ok"] is True
    assert result["mode"] == "shortfall"
    assert result["refilled"] == 0
    assert "https://example.com/rejected-reserve" not in records_text
    assert "https://example.com/rejected-reserve" not in digest_text
