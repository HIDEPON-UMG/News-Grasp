"""NoPublishのcanonical Green確認後に限り、保存成果を通常dailyへ採用する。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from tools import news_grasp_daily_content as content
from tools import news_grasp_direct_runtime as runtime
from tools import news_grasp_release_nopublish as release
from tools.news_grasp_artifact_adoption import capture_artifact_source


def recover_public(*, repo_root: Path, state_root: Path, issue_date: str) -> dict:
    canonical = release._canonical_release_state_root()
    journal = canonical / "preentry.sqlite3"
    if content._has_reparse_ancestor(journal):
        raise ValueError("nopublish_source_reparse")
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT detail FROM run_bindings WHERE issue_date=?", (issue_date,)).fetchone()
    if row is None:
        raise ValueError("nopublish_green_required")
    binding = json.loads(row[0])
    source_root = Path(binding["artifactRoot"])
    green = release._saved_green_result(
        release._canonical_result_path(canonical, issue_date),
        artifact_root=source_root, canonical_state=canonical, source_issue_date=issue_date,
        run_identity=binding["runIdentity"],
    )
    if green is None:
        raise ValueError("nopublish_green_required")
    source = capture_artifact_source(
        repo_root=source_root, database=canonical / "direct-mainline.sqlite3",
        run_id=green["run_id"], issue_date=issue_date,
    )
    return runtime._run_daily_mainline_with_artifacts(
        repo_root=repo_root, state_root=state_root, issue_date=issue_date,
        scheduler_trigger_at=f"{issue_date}T06:00:00+09:00", artifact_source=source,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--issue-date", required=True)
    args = parser.parse_args()
    result = recover_public(repo_root=args.repo_root, state_root=args.state_root, issue_date=args.issue_date)
    runtime._emit_cli(result)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(_main())
