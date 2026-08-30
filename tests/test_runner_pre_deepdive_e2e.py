"""direct 本線の DeepDive 前後 gate 契約テスト。"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
ISSUE_DATE = "2026-08-30"


class _Verifier:
    def verify(self, stage_id: str, **_: Any) -> dict[str, Any]:
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


def test_deepdive_stages_are_after_daily_audio_and_before_html_quality() -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    stages = list(api.DIRECT_STAGES)

    assert stages.index("daily_audio") < stages.index("deepdive_article")
    assert stages.index("deepdive_article") < stages.index("deepdive_quality")
    assert stages.index("deepdive_quality") < stages.index("html_docs")
    assert stages.index("html_docs") < stages.index("daily_quality")


def test_public_stages_start_only_after_daily_quality() -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    stages = list(api.DIRECT_STAGES)

    public_start = stages.index("youtube_podcasts")
    assert stages.index("daily_quality") < public_start
    for stage in ["playlist", "notification", "distribution", "publish_status", "commit_push", "pages_verify", "public_completion"]:
        assert public_start <= stages.index(stage)


def test_direct_runtime_can_stop_before_publication_without_claiming_complete(tmp_path: Path) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "direct-mainline", semantic_verifier=_Verifier())
    run = api.start_run(store, cwd=ROOT, issue_date=ISSUE_DATE)

    state = run
    while state["current_stage"] != "youtube_podcasts":
        state = api.run_exact_successor(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
        )

    assert state["status"] in {"active", "green"}
    assert state["current_stage"] == "youtube_podcasts"
    assert state["exact_successor"] == "youtube_podcasts"
    assert state["completed_at"] in {None, ""}
