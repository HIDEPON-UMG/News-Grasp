from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.news_grasp_completion_guard import evaluate_direct_public


ISSUE_DATE = "2026-08-30"
EXPECTED_TITLE = "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
STAGES = [
    "title_control",
    "issue_inventory",
    "category_collection",
    "evidence_dedup_freshness",
    "category_digest",
    "reporter_validation",
    "articles_jsonl",
    "summary",
    "daily_audio",
    "deepdive_article",
    "deepdive_quality",
    "html_docs",
    "daily_quality",
    "youtube_podcasts",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "commit_push",
    "pages_verify",
    "public_completion",
]
SURFACES = (
    "web",
    "daily_audio",
    "deepdive_article",
    "deepdive_audio",
    "youtube_daily",
    "youtube_deepdive",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "remote_commit",
    "pages",
)


def _receipt() -> dict:
    publish_commit = "c" * 40
    surfaces = {
        name: {"ok": True, "issue_date": ISSUE_DATE, "evidence": f"build/{name}.json"}
        for name in SURFACES
    }
    surfaces["distribution"]["publish_commit"] = publish_commit
    surfaces["publish_status"]["status"] = "published_ok"
    surfaces["remote_commit"]["commit"] = publish_commit
    surfaces["pages"]["content_identity_verified"] = True
    surfaces["web"]["content_identity_verified"] = True
    surfaces["youtube_daily"]["video_id"] = "daily-video"
    surfaces["youtube_deepdive"]["video_id"] = "deepdive-video"
    surfaces["playlist"]["membership_verified"] = True
    surfaces["notification"]["sent_count"] = 1
    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1",
        "completion_mode": "direct_public_v1",
        "issue_date": ISSUE_DATE,
        "automation_id": "news-grasp-6-40",
        "cwd": "C:/workspace/News-Grasp",
        "run_intent": "ScheduledProductionDirect",
        "scheduled_inventory": {
            "scheduled_category_ids": ["AI", "Business"],
            "generated_digest_category_ids": ["AI", "Business"],
        },
        "title": {
            "title_status": "already_ok",
            "expected_title": EXPECTED_TITLE,
            "actual_title": EXPECTED_TITLE,
            "publication_blocked": False,
        },
        "post_publish_issue_list": [],
        "stage_history": [
            {"stage": stage, "completed_at": f"2026-08-30T06:{index:02d}:00+09:00"}
            for index, stage in enumerate(STAGES)
        ],
        "quality_gate": {
            "ok": True,
            "issue_date": ISSUE_DATE,
            "command": "python -m tools.validate_daily_quality --date 2026-08-30 --require-deepdive --json",
        },
        "deepdive_quality": {
            "ok": True,
            "issue_date": ISSUE_DATE,
            "rendered_public": True,
            "provenance_valid": True,
            "dialogue_valid": True,
        },
        "public_surfaces": surfaces,
        "publish_commit": publish_commit,
        "fallback_publish": False,
        "no_publish": False,
        "elapsed_minutes": 42,
    }


def test_direct_public_completion_accepts_public_only_evidence_without_runner() -> None:
    assert len(STAGES) == 21
    result = evaluate_direct_public(_receipt(), ISSUE_DATE)
    assert result["schemaVersion"] == "NEWS_GRASP_DIRECT_COMPLETION_GUARD_V1"
    assert result["ok"] is True
    assert result["completion_mode"] == "direct_public_v1"
    assert result["title_status"] == "already_ok"
    assert result["slo"]["target_met"] is True
    assert "runner_status" not in result
    assert "readiness" not in result


def test_title_unavailable_is_nonblocking_only_when_issue_is_recorded() -> None:
    receipt = _receipt()
    receipt["title"] = {
        "title_status": "unavailable",
        "expected_title": EXPECTED_TITLE,
        "actual_title": "",
        "publication_blocked": False,
    }
    receipt["post_publish_issue_list"] = ["title: host_title_action_unavailable"]
    assert evaluate_direct_public(receipt, ISSUE_DATE)["ok"] is True

    receipt["post_publish_issue_list"] = []
    result = evaluate_direct_public(receipt, ISSUE_DATE)
    assert result["ok"] is False
    assert "title_failure_issue_not_recorded" in result["failures"]


@pytest.mark.parametrize(
    "mutation,expected_failure",
    [
        (lambda r: r["quality_gate"].update(ok=False), "daily_quality_gate_not_green"),
        (lambda r: r.update(fallback_publish=True), "fallback_publish_not_completion"),
        (lambda r: r.update(no_publish=True), "nopublish_not_completion"),
        (lambda r: r["public_surfaces"]["pages"].pop("content_identity_verified"), "pages_content_identity_unverified"),
        (lambda r: r["public_surfaces"]["publish_status"].update(status="published_ok_previous_day"), "publish_status_not_published_ok"),
        (lambda r: r["stage_history"].reverse(), "stage_order_invalid"),
    ],
)
def test_direct_completion_rejects_quality_and_completion_shortcuts(mutation, expected_failure: str) -> None:
    receipt = copy.deepcopy(_receipt())
    mutation(receipt)
    result = evaluate_direct_public(receipt, ISSUE_DATE)
    assert result["ok"] is False
    assert expected_failure in result["failures"]


def test_slo_overrun_records_debt_but_does_not_turn_public_green_red() -> None:
    receipt = _receipt()
    receipt["elapsed_minutes"] = 96
    result = evaluate_direct_public(receipt, ISSUE_DATE)
    assert result["ok"] is True
    assert result["slo"]["slo_met"] is False
    assert result["slo"]["continue_public_successors"] is True


@pytest.mark.parametrize(
    "elapsed,band,frozen,slo_met",
    [
        (45, "target", False, True),
        (74, "closeout", False, True),
        (75, "public_critical_only", True, True),
        (90, "public_critical_only", True, True),
        (91, "slo_debt_continue_public", True, False),
    ],
)
def test_virtual_clock_45_75_90_boundaries_keep_public_successor(
    elapsed: int, band: str, frozen: bool, slo_met: bool
) -> None:
    receipt = _receipt()
    receipt["elapsed_minutes"] = elapsed
    result = evaluate_direct_public(receipt, ISSUE_DATE)
    assert result["ok"] is True
    assert result["slo"]["time_band"] == band
    assert result["slo"]["optional_high_cost_frozen"] is frozen
    assert result["slo"]["slo_met"] is slo_met
    assert result["slo"]["continue_public_successors"] is True


def test_inventory_deepdive_and_distribution_bindings_are_conjunctive() -> None:
    receipt = _receipt()
    receipt["scheduled_inventory"]["generated_digest_category_ids"] = ["AI"]
    receipt["deepdive_quality"]["dialogue_valid"] = False
    receipt["public_surfaces"]["playlist"]["membership_verified"] = False
    result = evaluate_direct_public(receipt, ISSUE_DATE)
    assert result["ok"] is False
    assert "scheduled_inventory_digest_mismatch" in result["failures"]
    assert "deepdive_quality_not_green" in result["failures"]
    assert "playlist_membership_unverified" in result["failures"]


def test_automation_and_skill_bind_direct_contract() -> None:
    root = Path(__file__).parents[1]
    prompt = (root / "automation/news-grasp-6-40/automation.toml.template").read_text(encoding="utf-8")
    skill = (root / "automation/skills/news-grasp-direct-mainline/SKILL.md").read_text(encoding="utf-8")
    combined = prompt + "\n" + skill

    for token in (
        "news-grasp-direct-mainline",
        "YY/MM/DD",
        "TT26/",
        "45",
        "90",
        "scheduled_category_ids",
        "--require-deepdive",
        "public-only",
        "post_publish_issue_list",
        "title_status",
    ):
        assert token in combined
    assert "news_grasp_runner.py" not in prompt
    assert "news_grasp_nopublish.py" not in prompt


def test_installed_completion_guard_direct_canary(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    installed = Path.home() / ".codex" / "automations" / "news-grasp-6-40" / "completion_guard.py"
    assert installed.is_file()
    receipt_path = tmp_path / "direct-receipt.json"
    receipt_path.write_text(json.dumps(_receipt(), ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(installed),
            "--issue-date",
            ISSUE_DATE,
            "--direct-receipt",
            str(receipt_path),
            "--ops-root",
            str(root),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["completion_mode"] == "direct_public_v1"
    assert "runner_status" not in result
