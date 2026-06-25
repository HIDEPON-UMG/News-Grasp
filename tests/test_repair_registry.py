from __future__ import annotations

from pathlib import Path

from tools.repair_registry import (
    RepairContext,
    find_handler,
    repair_with_registry,
)


def test_registry_exposes_summary_emphasis_patch_metadata() -> None:
    handler = find_handler("summary-emphasis-patch")

    assert handler is not None
    assert handler.handler_id == "summary-emphasis-patch"
    assert handler.kind == "deterministic"
    assert handler.verify_gate == "daily-quality"
    assert "digest/Summary/{date}.md" in handler.allowed_artifacts


def test_missing_handler_returns_typed_unimplemented_status(tmp_path: Path) -> None:
    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="does-not-exist",
            artifacts=[],
        )
    )

    assert not result.changed
    assert result.status == "blocked_repair_handler_unimplemented"


def test_summary_emphasis_patch_updates_existing_summary_only(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    other = tmp_path / "digest" / "AI" / "2026-06-25-AI.md"
    summary.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")
    other.write_text("# AI\n\nこのファイルは触らない。\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="summary-emphasis-patch",
            artifacts=["digest/Summary/2026-06-25.md"],
        )
    )

    assert result.changed
    assert result.status == "repaired"
    assert "**市場の変化**を整理する。" in summary.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == "# AI\n\nこのファイルは触らない。\n"


def test_summary_emphasis_patch_is_idempotent(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")
    ctx = RepairContext(
        repo_root=tmp_path,
        issue="2026-06-25",
        handler_id="summary-emphasis-patch",
        artifacts=["digest/Summary/2026-06-25.md"],
    )

    first = repair_with_registry(ctx)
    second = repair_with_registry(ctx)

    assert first.status == "repaired"
    assert second.status in {"noop", "not_applicable"}
    assert summary.read_text(encoding="utf-8").count("**市場の変化**") == 1


def test_registry_blocks_handler_scope_violation(tmp_path: Path) -> None:
    summary = tmp_path / "digest" / "Summary" / "2026-06-25.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("# Summary\n\n### AI\n\n市場の変化を整理する。\n", encoding="utf-8")

    result = repair_with_registry(
        RepairContext(
            repo_root=tmp_path,
            issue="2026-06-25",
            handler_id="summary-emphasis-patch",
            artifacts=["docs/index.html"],
        )
    )

    assert result.status == "blocked_scope_violation"
    assert not result.changed
    assert "**市場の変化**" not in summary.read_text(encoding="utf-8")
