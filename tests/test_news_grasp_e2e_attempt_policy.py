from __future__ import annotations

import pytest
import hashlib
import json
import sys

from tools.news_grasp_e2e_attempt_policy import (
    E2EAttemptPolicyError,
    append_policy_transition,
    bind_policy_admission,
    issue_logical_attempt,
    mark_full_correction,
    new_policy,
    record_failure,
    record_success,
    validate_policy,
)


def test_attempt_a_green_does_not_issue_attempt_b() -> None:
    state = issue_logical_attempt(new_policy(), 1)
    state = record_success(state, 1)
    assert state["terminal"] == "product_completion"
    with pytest.raises(E2EAttemptPolicyError, match="ATTEMPT_TERMINAL"):
        issue_logical_attempt(state, 2)


def test_attempt_a_failure_local_resumes_once_then_allows_attempt_b() -> None:
    state = issue_logical_attempt(new_policy(), 1)
    state = record_failure(state, 1, "failure_local")
    assert state["logicalAttemptIssued"] == 1
    assert state["attemptA"]["resumeCount"] == 1
    state = record_success(state, 1)
    state = mark_full_correction(state)
    state = issue_logical_attempt(state, 2)
    state = record_success(state, 2)
    assert state["terminal"] == "product_completion"
    assert state["logicalAttemptIssued"] == 2


def test_attempt_b_random_failure_is_design_feedback_and_attempt_three_is_forbidden() -> None:
    state = issue_logical_attempt(new_policy(), 1)
    state = record_failure(state, 1, "failure_local")
    state = record_success(state, 1)
    state = mark_full_correction(state)
    state = issue_logical_attempt(state, 2)
    state = record_failure(state, 2, "random_transient")
    assert state["terminal"] == "design_feedback_terminal"
    assert state["designFeedback"]["thirdAttemptForbidden"] is True
    with pytest.raises(E2EAttemptPolicyError, match="ATTEMPT_TERMINAL"):
        issue_logical_attempt(state, 3)


def test_second_failure_local_resume_is_rejected_without_new_logical_attempt() -> None:
    state = issue_logical_attempt(new_policy(), 1)
    state = record_failure(state, 1, "failure_local")
    with pytest.raises(E2EAttemptPolicyError, match="RESUME_LIMIT"):
        record_failure(state, 1, "failure_local")


def test_attempt_b_requires_full_correction_after_attempt_a() -> None:
    state = issue_logical_attempt(new_policy(), 1)
    state = record_failure(state, 1, "failure_local")
    state = record_success(state, 1)
    with pytest.raises(E2EAttemptPolicyError, match="ATTEMPT_B_NOT_REQUIRED"):
        issue_logical_attempt(state, 2)


def test_forged_final_b_state_without_transition_history_is_rejected() -> None:
    state = new_policy()
    state.update(
        {
            "logicalAttemptIssued": 2,
            "attemptA": {"status": "ready_for_attempt_b", "resumeCount": 1},
            "attemptB": {"status": "running", "resumeCount": 0},
            "transition": {
                "sequence": 5,
                "event": "issue_b",
                "previousStateSha256": "f" * 64,
                "stateSha256": "e" * 64,
            },
        }
    )
    with pytest.raises(E2EAttemptPolicyError, match="TRANSITION_INVALID"):
        validate_policy(state)


def test_policy_ledger_appends_exactly_one_authorized_transition(tmp_path) -> None:
    policy_path = tmp_path / "policy.json"
    admission_path = tmp_path / "admission.json"
    admission = {"state": "issued", "attemptKey": "News-Grasp:2026-08-13:scheduled-equivalent-nopublish", "issueDate": "2026-08-13", "purpose": "final_confirmation_only"}
    admission["admissionId"] = __import__("hashlib").sha256(json.dumps(admission, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    admission_path.write_text(json.dumps(admission, sort_keys=True) + "\n", encoding="utf-8")
    first = issue_logical_attempt(bind_policy_admission(new_policy(), admission_path), 1)
    policy_path.write_text(json.dumps(first, sort_keys=True) + "\n", encoding="utf-8")
    first_transition = first["transition"]
    first_receipt = tmp_path / "e2e-transition-1.json"
    producer = str(__import__("pathlib").Path(sys.executable).resolve())
    producer_sha = hashlib.sha256(__import__("pathlib").Path(producer).read_bytes()).hexdigest()
    receipt_base = {"schemaVersion": "NEWS_GRASP_E2E_TRANSITION_RECEIPT_V1", "event": first_transition["event"], "sequence": first_transition["sequence"], "attemptKey": admission["attemptKey"], "issueDate": admission["issueDate"], "admissionId": admission["admissionId"], "previousStateSha256": first_transition["previousStateSha256"], "stateSha256": first_transition["stateSha256"], "producerRouteId": "news-grasp-runner", "status": "succeeded", "producerProcessId": 1, "producerExecutablePath": producer, "producerExecutableSha256": producer_sha, "outcomeSchemaVersion": "NEWS_GRASP_E2E_TRANSITION_OUTCOME_V1", "outcomeStatus": "admission_validated", "outcomeSha256": "0" * 64, "outcomeStatePath": "", "outcomeStateSha256": "", "outcomeExitCode": -1, "outcomeRunnerStatus": "not_started"}
    first_receipt.write_text(json.dumps(receipt_base, sort_keys=True) + "\n", encoding="utf-8")
    append_policy_transition(policy_path, admission_path, transition_receipt_path=first_receipt)
    final = record_failure(first, 1, "failure_local")
    policy_path.write_text(json.dumps(final, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path = tmp_path / "e2e-transition-2.json"
    transition = final["transition"]
    receipt_base.update({"event": transition["event"], "sequence": transition["sequence"], "previousStateSha256": transition["previousStateSha256"], "stateSha256": transition["stateSha256"]})
    receipt_path.write_text(json.dumps(receipt_base, sort_keys=True) + "\n", encoding="utf-8")
    append_policy_transition(policy_path, admission_path, transition_receipt_path=receipt_path)
    append_policy_transition(policy_path, admission_path, transition_receipt_path=receipt_path)
