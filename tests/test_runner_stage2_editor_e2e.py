"""direct 本線の reporter/editor 相当工程契約テスト。"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ISSUE_DATE = "2026-08-30"


class _Verifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def verify(self, stage_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(stage_id)
        if stage_id == "title_control":
            return {
                "ok": True,
                "status": "green",
                "issue_date": ISSUE_DATE,
                "title_status": "already_ok",
                "actual_title": "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
                "post_publish_issue_list": [],
            }
        return {"ok": True, "status": "green", "issue_date": ISSUE_DATE}


def test_reporter_and_editor_equivalent_stages_are_ordered_before_materialization() -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    stages = list(api.DIRECT_STAGES)

    assert stages.index("issue_inventory") < stages.index("category_collection")
    assert stages.index("category_collection") < stages.index("evidence_dedup_freshness")
    assert stages.index("evidence_dedup_freshness") < stages.index("category_digest")
    assert stages.index("category_digest") < stages.index("reporter_validation")
    assert stages.index("reporter_validation") < stages.index("articles_jsonl")
    assert stages.index("articles_jsonl") < stages.index("summary")


def test_run_exact_successor_records_reporter_to_summary_progression(tmp_path: Path) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "direct-mainline", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    run = api.start_run(store, cwd=ROOT, issue_date=ISSUE_DATE)

    state = run
    while state["current_stage"] != "summary":
        state = api.run_exact_successor(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
        )

    assert state["current_stage"] == "summary"
    assert verifier.calls == [
        "title_control",
        "issue_inventory",
        "category_collection",
        "evidence_dedup_freshness",
        "category_digest",
        "reporter_validation",
        "articles_jsonl",
    ]
    assert [item["stage"] for item in state["stage_history"]] == verifier.calls


def test_direct_runtime_rejects_stale_writer_before_editor_materialization(tmp_path: Path) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "direct-mainline", semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(store, cwd=ROOT, issue_date=ISSUE_DATE)

    try:
        api.run_exact_successor(
            store,
            run_id=run["run_id"],
            writer_lease="not-the-current-writer",
        )
    except PermissionError as exc:
        assert "stale writer lease fenced" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale writer was not rejected")
