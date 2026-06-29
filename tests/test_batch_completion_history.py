from __future__ import annotations

import json
from pathlib import Path

from tools.batch_completion_history import classify_day


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_batch_completion_history_classifies_complete_day(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "publish_complete"})
    _write_json(tmp_path / "docs" / "publish-status.json", {"date": "2026-06-29", "result": "published_ok"})
    _write_json(tmp_path / "data" / "distribution" / "2026-06-29.json", {"date": "2026-06-29"})

    assert classify_day(tmp_path, "2026-06-29").status == "complete"


def test_batch_completion_history_classifies_fallback_as_forbidden(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "fallback_ok"})
    _write_json(
        tmp_path / "docs" / "publish-status.json",
        {"date": "2026-06-29", "result": "published_fallback_with_notice"},
    )

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "forbidden_fallback"
    assert "通常日次 fallback 完走扱いは禁止" in result.reason


def test_batch_completion_history_missing_sources_are_unverified_not_complete(tmp_path: Path) -> None:
    _write_json(tmp_path / "state" / "news-grasp-runner-state.json", {"date": "2026-06-29", "status": "running"})

    result = classify_day(tmp_path, "2026-06-29")

    assert result.status == "unverified"
