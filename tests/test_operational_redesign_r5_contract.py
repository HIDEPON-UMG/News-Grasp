"""R6 WP-13 のA15/A16 Red/Green契約（既存product entryに束縛）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import news_grasp_daily_control as daily_control
from tools import news_grasp_external_control as external_control


def _authority_body(
    *, generation: int = 1, lineage: str = "lineage-a", previous: str | None = None
) -> dict[str, object]:
    body: dict[str, object] = {
        "schemaVersion": "EXTERNAL_CONTROL_PLANE_HEALTH_AUTHORITY_V1",
        "authorityLineageId": lineage,
        "authorityLineageDerivation": "sha256-utf8-lf-v1",
        "authorityGeneration": generation,
        "previousReceiptSha256": previous or ("0" * 64 if generation == 1 else "a" * 64),
        "canonicalDescriptorPath": "descriptor.json",
        "canonicalDescriptorSha256": "b" * 64,
        "sourceBrokerPath": "source-broker.py",
        "sourceBrokerSha256": "c" * 64,
        "installedBrokerPath": "installed-broker.py",
        "installedBrokerSha256": "c" * 64,
        "dependencyGenerationHash": "d" * 64,
        "routeGenerationHash": "e" * 64,
        "ledgerGenerationId": "ledger-1",
        "registryAnchorGenerationId": "registry-1",
        "promotionGuardGenerationId": "guard-1",
        "statefulSelfTestStatus": "green",
        "statefulSelfTestId": "self-test-1",
        "testedAt": "2026-08-11T00:00:00+09:00",
        "publisherId": "global-control-plane-owner",
    }
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def test_ng3_a15_primary_external_drift_defers_without_spawn() -> None:
    result = daily_control.classify_observed_failure(
        runner_state={"status": "failed_shared_broker_generation_drift"},
        process_exit_code=126,
        log_text="NEWS_GRASP_SHARED_BROKER_GENERATION_DRIFT",
    )
    assert result == "external_control_plane_unavailable"


def test_ng3_a15_adversarial_external_readiness_input_rejected(tmp_path: Path) -> None:
    assert callable(getattr(daily_control, "validate_external_readiness_input", None))
    with pytest.raises(ValueError, match="EXTERNAL_CONTROL_PLANE_INPUT_INVALID"):
        daily_control.validate_external_readiness_input(
            {"status": "ready", "canonicalDescriptorPath": str(tmp_path / "outside.json")},
            canonical_root=tmp_path / "canonical",
        )


def test_ng3_a15_recovery_external_fingerprint_reprobes_once(tmp_path: Path) -> None:
    assert callable(getattr(daily_control, "accept_external_authority", None))
    first = daily_control.accept_external_authority(
        authority=_authority_body(generation=1), state_path=tmp_path / "accepted.json"
    )
    second = daily_control.accept_external_authority(
        authority=_authority_body(generation=2, previous=str(first["receiptSha256"])),
        state_path=tmp_path / "accepted.json",
    )
    replay = daily_control.accept_external_authority(
        authority=_authority_body(generation=4), state_path=tmp_path / "accepted.json"
    )
    assert first["accepted"] is True
    assert second["accepted"] is True
    assert second["chainGap"] is False
    assert replay["accepted"] is True
    assert replay["chainGap"] is True
    same = daily_control.accept_external_authority(
        authority=_authority_body(generation=4), state_path=tmp_path / "accepted.json"
    )
    assert same["accepted"] is False
    assert same["reasonCode"] == "EXTERNAL_AUTHORITY_REPLAY"


def test_ng3_a16_primary_external_green_binds_official_broker(tmp_path: Path) -> None:
    assert callable(getattr(daily_control, "build_run_generation_binding", None))
    readiness = {"status": "ready", "externalGenerationFingerprint": "f" * 64}
    binding = daily_control.build_run_generation_binding(
        readiness=readiness,
        product_generation_id="generation-1",
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-a",
        checkpoint_id="checkpoint-1",
    )
    assert binding["schemaVersion"] == "RUN_GENERATION_BINDING_V1"
    assert binding["productGenerationId"] == "generation-1"
    assert binding["externalGenerationFingerprint"] == "f" * 64


def test_ng3_a16_adversarial_post_readiness_drift_accepts_no_output() -> None:
    assert callable(getattr(daily_control, "validate_model_invocation_outcome", None))
    for rc, stdout in ((2, "{}"), (124, "{}"), (126, "{}"), (0, "not-json")):
        result = daily_control.validate_model_invocation_outcome(
            return_code=rc,
            stdout=stdout,
            expected_schema="MODEL_PRE_EXEC_RECEIPT_V1",
        )
        assert result["status"] == "model_outcome_unavailable"
        assert result["modelLaunchAccepted"] is False


def test_ng3_a16_recovery_checkpoint_resumes_after_external_green() -> None:
    assert callable(getattr(daily_control, "external_reentry_decision", None))
    deferred = daily_control.external_reentry_decision(
        previous_authority_generation=1,
        current_authority_generation=2,
        previous_lineage="lineage-a",
        current_lineage="lineage-a",
        checkpoint_id="checkpoint-1",
        issue_date="2026-08-11",
        daily_operation_lineage_id="lineage-a",
    )
    assert deferred["resume"] is True
    assert deferred["modelCalls"] == 1
    assert deferred["checkpointId"] == "checkpoint-1"


def test_ng3_a15_external_probe_maps_installed_drift_without_shared_reads() -> None:
    authority = _authority_body()
    readiness = external_control.probe_external_readiness(
        fixture_source={
            "authority": authority,
            "sourceBrokerSha256": "c" * 64,
            "installedBrokerSha256": "f" * 64,
        }
    )
    assert readiness["status"] == "unavailable"
    assert readiness["reasonCode"] == "installed_source_drift"
    assert readiness["modelLaunchCount"] == 0


def test_ng3_a15_external_probe_rejects_path_escape_without_mutation(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    payload = {
        "status": "ready",
        "externalGenerationFingerprint": "f" * 64,
        "sourceBrokerPath": str(outside),
    }
    with pytest.raises(ValueError, match="EXTERNAL_CONTROL_PLANE_INPUT_INVALID"):
        daily_control.validate_external_readiness_input(payload, canonical_root=tmp_path)
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_ng3_a15_same_generation_different_bytes_is_tamper(tmp_path: Path) -> None:
    state = tmp_path / "accepted.json"
    daily_control.accept_external_authority(
        authority=_authority_body(generation=1), state_path=state
    )
    changed = _authority_body(generation=1)
    changed["statefulSelfTestId"] = "changed"
    changed["receiptSha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in changed.items() if key != "receiptSha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="EXTERNAL_AUTHORITY_TAMPERED"):
        daily_control.accept_external_authority(authority=changed, state_path=state)


def test_ng3_a16_binding_rejects_external_red_and_retry_requires_new_authority() -> None:
    with pytest.raises(ValueError, match="EXTERNAL_CONTROL_PLANE_UNAVAILABLE"):
        daily_control.build_run_generation_binding(
            readiness={"status": "unavailable"},
            product_generation_id="generation-1",
            issue_date="2026-08-11",
            daily_operation_lineage_id="lineage-a",
            checkpoint_id="checkpoint-1",
        )
    assert external_control.should_retry_model(
        previous_authority_generation=2, current_authority_generation=2
    ) is False
    assert external_control.should_retry_model(
        previous_authority_generation=2, current_authority_generation=3
    ) is True


def test_ng3_a15_prepare_recovery_external_red_is_typed_deferred(tmp_path: Path) -> None:
    calls: list[str] = []

    class Backend:
        repo_root = tmp_path

        def load_state(self, _date: str) -> dict[str, object]:
            return {"status": "failed", "scheduledAuthorityId": "authority-1"}

        def probe_external_control_plane(self) -> dict[str, object]:
            return {"status": "unavailable", "reasonCode": "installed_source_drift"}

        def inspect_attempt(self, _date: str) -> dict[str, object]:
            calls.append("inspect")
            raise AssertionError("external Red must not inspect shared broker ledger")

    result = daily_control.prepare_recovery(
        issue_date="2026-08-11",
        trigger="production_failure",
        process_exit_code=126,
        backend=Backend(),
    )
    assert result["action"] == "defer_external_control_plane"
    assert result["terminal"] == "operation_deferred_external_dependency"
    assert result["modelLaunchCount"] == 0
    assert calls == []
