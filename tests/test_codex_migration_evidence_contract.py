#!/usr/bin/env python3
"""Codex 完全移行を思い込みで進めないための証拠契約テスト。"""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "codex_migration_evidence_register.md"
ASSUMPTIONS = ROOT / "docs" / "codex_migration_assumption_ledger.md"
SELECTION = ROOT / "build" / "model-eval-selection" / "combo_summary.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_evidence_register_exists_and_covers_all_todos() -> None:
    """T00-T28 は実装者の記憶ではなく、証拠 ID で追跡する。"""
    text = _read(EVIDENCE)
    for i in range(29):
        todo = f"T{i:02d}"
        assert todo in text
        assert re.search(rf"\|\s*EVID-[0-9]{{3}}\s*\|\s*{todo}\s*\|", text), todo
    for needle in [
        "C:\\Users\\hidek\\bin\\news-grasp-runner.ps1",
        "C:\\Users\\hidek\\bin\\run_codex_with_timeout.ps1",
        "codex_migration_plan_2026-06-06.md",
        "handoff_2026-06-13_codex-migration.md",
        "OpenAI API key / SDK は使わない",
    ]:
        assert needle in text


def test_assumption_ledger_blocks_unproven_implementation_claims() -> None:
    """未根拠の仮定は Green 扱いせず、実装禁止または検証待ちとして明示する。"""
    text = _read(ASSUMPTIONS)
    assert "unresolved_implementation_assumptions: 0" in text
    for phrase in [
        "gpt-5.5 editor 採用済み",
        "Stage2 fan-out 実装済み",
        "RSS registry 完備",
        "Publish-always 完了",
    ]:
        assert phrase in text
        pattern = rf"\|\s*{re.escape(phrase)}\s*\|[^|]*\|\s*(False|Rejected|Blocked)\s*\|"
        assert re.search(pattern, text), phrase


def test_model_selection_summary_is_combo_scoped_and_not_decided_by_prompt_presence() -> None:
    """記者・編集者モデルは prompt の存在ではなく combo 評価 artifact でだけ決める。"""
    data = json.loads(SELECTION.read_text(encoding="utf-8-sig"))
    assert data["selection_status"] in {"undecided", "selected"}
    assert data["coverage_status"] == "complete"
    assert data["source_of_truth"] == "reporter_editor_combo_final_quality_and_total_cost"
    assert data["uses_openai_api_key"] is False
    assert data["uses_openai_sdk"] is False
    assert "full__mini-editor-55" in data["expected_combos"]
    assert data["missing_combos"] == []
    assert data["recommended_combo"] == "full__mini-editor"


def test_reporter_and_editor_json_schemas_exist() -> None:
    """Stage2/Stage3 は自由文の約束ではなく JSON schema 境界で固定する。"""
    reporter = ROOT / "schemas" / "reporter_records.schema.json"
    editor = ROOT / "schemas" / "editor_summary.schema.json"
    for path in [reporter, editor]:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        assert data["type"] == "object"
        assert data.get("required")
        assert data["additionalProperties"] is False
