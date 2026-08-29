from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.news_grasp_title_control import (
    TitleControlError,
    expected_title,
    record_title_status,
    validate_title,
)


ISSUE_DATE = "2026-08-30"
EXPECTED = "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"


def test_expected_title_and_exact_validation() -> None:
    assert expected_title(ISSUE_DATE) == EXPECTED
    assert validate_title(EXPECTED, ISSUE_DATE) == {"ok": True, "reason": "exact_match"}


@pytest.mark.parametrize(
    "candidate,reason",
    [
        ("TT26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開", "unexpected_prefix"),
        ("2026/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開", "invalid_format"),
        ("08/30/26 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開", "wrong_issue_date"),
        ("News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開", "date_missing"),
        (EXPECTED + " extra", "unexpected_suffix"),
    ],
)
def test_title_validation_rejects_non_contract_shapes(candidate: str, reason: str) -> None:
    result = validate_title(candidate, ISSUE_DATE)
    assert result == {"ok": False, "reason": reason}


def test_nonblocking_title_failure_is_recorded_in_post_publish_issues(tmp_path: Path) -> None:
    output = tmp_path / "title-status.json"
    receipt = record_title_status(
        issue_date=ISSUE_DATE,
        status="unavailable",
        actual_title="",
        reason="host_title_action_unavailable",
        post_publish_issue_list=[],
        output_path=output,
    )

    assert receipt["title_status"] == "unavailable"
    assert receipt["publication_blocked"] is False
    assert receipt["post_publish_issue_list"]
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


def test_success_status_cannot_claim_a_malformed_actual_title() -> None:
    with pytest.raises(TitleControlError, match="TITLE_STATUS_ACTUAL_TITLE_INVALID"):
        record_title_status(
            issue_date=ISSUE_DATE,
            status="updated",
            actual_title="TT" + EXPECTED,
            reason="",
            post_publish_issue_list=[],
        )
