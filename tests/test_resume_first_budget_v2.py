from __future__ import annotations


def test_existing_artifact_consumes_no_full_attempt() -> None:
    from tools.harness.high_cost_control_v2 import resolve_news_grasp_operation

    result = resolve_news_grasp_operation(
        requested_mode="full_e2e", artifacts_exist=True, resume_stage=None
    )
    assert result == {"operationKind": "resume_zero_external", "consumeFullAttempt": False}


def test_stage_model_requirement_is_derived_not_claimed() -> None:
    from tools.harness.high_cost_control_v2 import resolve_news_grasp_operation

    deepdive = resolve_news_grasp_operation(
        requested_mode="resume", artifacts_exist=True, resume_stage="deepdive"
    )
    post = resolve_news_grasp_operation(
        requested_mode="resume", artifacts_exist=True, resume_stage="post-deepdive"
    )
    assert deepdive["operationKind"] == "resume_model"
    assert post["operationKind"] == "resume_zero_external"
