from __future__ import annotations

import json

from tools.codex_usage_pressure import analyze_usage_pressure


def _write_jsonl(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def test_usage_pressure_sums_main_logs_without_reporter_duplicates(tmp_path) -> None:
    wt = tmp_path / "build" / "e2e-worktrees" / "run-a" / "build" / "codex-usage"
    _write_jsonl(
        wt / "2026-06-30.jsonl",
        [
            {"flow": "reporter:ai", "tokens_used": 100},
            {"flow": "newsroom_editor", "tokens_used": 50},
        ],
    )
    _write_jsonl(wt / "2026-06-30.reporter-ai-attempt1.jsonl", [{"flow": "reporter:ai", "tokens_used": 100}])

    report = analyze_usage_pressure(tmp_path / "build" / "e2e-worktrees", "2026-06-30")

    assert report["total_tokens_used"] == 150
    assert report["runs"][0]["tokens_used"] == 150
    assert report["runs"][0]["ignored_duplicate_usage_files"] == 1


def test_usage_pressure_marks_image_tokens_unavailable_when_not_logged(tmp_path) -> None:
    wt = tmp_path / "build" / "e2e-worktrees" / "run-a" / "build" / "codex-usage"
    _write_jsonl(wt / "2026-06-30.jsonl", [{"flow": "reporter:ai", "tokens_used": 100}])
    (tmp_path / "build" / "ui-review").mkdir(parents=True)
    (tmp_path / "build" / "ui-review" / "screen.png").write_bytes(b"png")

    report = analyze_usage_pressure(tmp_path / "build" / "e2e-worktrees", "2026-06-30", ui_review_root=tmp_path / "build" / "ui-review")

    assert report["image_token_total"] is None
    assert report["image_token_accounting"] == "unavailable"
    assert report["image_artifact_count"] == 1
