"""S2 clean-room execution-plane Expected Red suite."""

from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s2_cases.json"
TZ = ZoneInfo("Asia/Tokyo")
STAGES = ("harvest", "model", "finalize")


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S2_CASES_V1"
    assert value["stages"] == list(STAGES)
    assert value["externalStates"] == ["PENDING", "RESERVED", "INTENT_DURABLE", "DISPATCHED", "CONFIRMED", "COMMITTED"]
    return value


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, tzinfo=TZ)


def _runtime(tmp_path: Path, index: int) -> Path:
    root = tmp_path / f"日本語-実行面-{index}"
    root.mkdir(parents=True, exist_ok=True)
    s1_cases = json.loads(
        (Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s1_cases.json").read_text(encoding="utf-8")
    )
    manifest = {
        "schemaVersion": s1_cases["normative"]["manifest"]["schemaVersion"],
        "scheduleId": s1_cases["normative"]["manifest"]["scheduleId"],
        "tasks": [s1_cases["normative"]["manifest"]["task"]],
    }
    manifest_path = root.parent / f"manifest-{index}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    controller_module = importlib.import_module("tools.news_grasp_cleanroom_controller")
    controller_module.Controller(runtime_root=root, manifest_path=manifest_path).reconcile(
        raw_argv=s1_cases["normative"]["rawArgv"]["exact"],
        observed_at=_at(6, 1),
        writer={
            "writerId": f"s2-test-{index}",
            "bootId": "s2-test-boot",
            "pid": 5000 + index,
            "processStartToken": f"s2-process-{index}",
        },
    )
    return root


def _authority(cases: dict[str, Any], runtime_root: Path) -> dict[str, Any]:
    with sqlite3.connect(runtime_root / "control" / "control-ledger-v1.sqlite3") as connection:
        row = connection.execute(
            "SELECT generation,owner_key,fence_token FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind='Scheduled'",
            (cases["scheduleId"], cases["issueDate"]),
        ).fetchone()
    if row is None:
        raise AssertionError("S1 active Scheduled slot was not created")
    value = {
        "schemaVersion": "EXECUTION_AUTHORITY_V1",
        "authorityId": cases["authority"]["authorityId"],
        "scheduleId": cases["scheduleId"],
        "issueDate": cases["issueDate"],
        "slotKey": cases["slotKey"],
        "generation": row[0],
        "ownerKey": row[1],
        "fenceToken": row[2],
        "maxDispatchAttempts": cases["authority"]["maxDispatchAttempts"],
    }
    value["authoritySha256"] = _sha(value)
    return value


def _payload(cases: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(cases["payload"], ensure_ascii=False))


class Admission:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(request)
        if self.status == "MALFORMED":
            return {"schemaVersion": "BROKEN"}
        value: dict[str, Any] = {
            "schemaVersion": "HIGH_COST_ADMISSION_DECISION_V1",
            "status": self.status,
            "authorityId": request["authority"]["authorityId"],
            "authoritySha256": request["authority"]["authoritySha256"],
            "idempotencyKey": request["idempotencyKey"],
        }
        value["decisionSha256"] = _sha(value)
        return value


class Provider:
    def __init__(self, unknown_type: type[BaseException] | None = None, query_outcome: str = "PRESENT") -> None:
        self.unknown_type = unknown_type
        self.query_outcome = query_outcome
        self.dispatch_calls: list[dict[str, Any]] = []
        self.query_calls: list[str] = []
        self.unknown_once = False

    def _receipt(self, key: str) -> dict[str, Any]:
        return {
            "schemaVersion": "EXTERNAL_RESULT_RECEIPT_V1",
            "status": "CONFIRMED",
            "idempotencyKey": key,
            "externalReceiptId": f"receipt-{_sha(key)[:16]}",
            "effectHash": _sha({"key": key, "effect": "deterministic"}),
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        self.dispatch_calls.append(request)
        key = request["idempotencyKey"]
        if self.unknown_once:
            self.unknown_once = False
            if self.unknown_type is None:
                raise RuntimeError("provider response unknown")
            raise self.unknown_type("provider response unknown")
        return self._receipt(key)

    def query(self, idempotency_key: str) -> dict[str, Any]:
        self.query_calls.append(idempotency_key)
        if self.query_outcome == "PRESENT":
            return {"status": "PRESENT", "receipt": self._receipt(idempotency_key)}
        return {"status": self.query_outcome}


class StageRunner:
    def __init__(self, fail_stage: str | None = None) -> None:
        self.fail_stage = fail_stage
        self.failed = False
        self.calls: list[str] = []

    def __call__(self, stage_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(stage_id)
        if stage_id == self.fail_stage and not self.failed:
            self.failed = True
            raise RuntimeError(f"stage failure: {stage_id}")
        return {"stageId": stage_id, "outputHash": _sha({"stage": stage_id, "input": input_payload})}


def _controller(execution: Any, runtime_root: Path, admission: Admission, provider: Provider, runner: StageRunner, **kwargs: Any) -> Any:
    return execution.ExecutionController(
        runtime_root,
        admission_adapter=admission,
        provider=provider,
        stage_runner=runner,
        **kwargs,
    )


def _execute(controller: Any, cases: dict[str, Any], authority: dict[str, Any]) -> Any:
    return controller.execute(
        slot_key=cases["slotKey"],
        issue_date=cases["issueDate"],
        authority=authority,
        payload=_payload(cases),
        observed_at=_at(6, 1),
    )


def _expect_reason(execution: Any, reason: str, operation: Callable[[], Any]) -> BaseException:
    with pytest.raises(execution.ExecutionError) as captured:
        operation()
    assert getattr(captured.value, "reason", None) == reason
    return captured.value


def _checkpoint_paths(runtime_root: Path, stage: str | None = None) -> list[Path]:
    root = runtime_root / "control" / "execution-checkpoints"
    paths = list(root.rglob("*.json")) if root.exists() else []
    return [path for path in paths if stage is None or path.stem == stage]


def _persisted_states(runtime_root: Path) -> list[str]:
    database = runtime_root / "control" / "execution-ledger-v1.sqlite3"
    with sqlite3.connect(database) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            if "state" in columns and "sequence" in columns:
                return [row[0] for row in connection.execute(f'SELECT state FROM "{table}" ORDER BY sequence')]
    raise AssertionError("execution state transition table is missing")


def test_s2_admission_grant_deny_unavailable(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, status in enumerate(cases["admissionStatuses"], start=1):
        runtime_root = _runtime(tmp_path, index)
        authority = _authority(cases, runtime_root)
        admission = Admission(status)
        provider = Provider()
        runner = StageRunner()
        controller = _controller(execution, runtime_root, admission, provider, runner)
        if status == "GRANTED":
            result = _execute(controller, cases, authority)
            assert result["schemaVersion"] == "RECONCILE_EXECUTION_RESULT_V1"
            assert len(admission.calls) == 1
        else:
            reason = {
                "DENIED": "NEWS_GRASP_EXECUTION_ADMISSION_DENIED",
                "UNAVAILABLE": "NEWS_GRASP_EXECUTION_ADMISSION_UNAVAILABLE",
                "MALFORMED": "NEWS_GRASP_EXECUTION_ADMISSION_INVALID",
            }[status]
            _expect_reason(execution, reason, lambda: _execute(controller, cases, authority))
        assert len(provider.dispatch_calls) == 0 if status != "GRANTED" else len(provider.dispatch_calls) >= 0
        assert len(provider.query_calls) == 0 if status != "GRANTED" else len(provider.query_calls) >= 0


def test_s2_external_call_crash_boundaries(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, boundary in enumerate(cases["crashBoundaries"], start=1):
        runtime_root = _runtime(tmp_path, 100 + index)
        authority = _authority(cases, runtime_root)
        admission = Admission("GRANTED")
        provider = Provider()
        runner = StageRunner()
        failed = {"active": True}

        def hook(name: str, expected=boundary) -> None:
            if failed["active"] and name == expected:
                failed["active"] = False
                raise RuntimeError(f"boundary crash: {name}")

        controller = _controller(execution, runtime_root, admission, provider, runner, boundary_hook=hook)
        with pytest.raises(Exception):
            _execute(controller, cases, authority)
        result = _execute(controller, cases, authority)
        assert result["externalState"] == "COMMITTED"
        assert len({call["idempotencyKey"] for call in provider.dispatch_calls}) <= 1


def test_s2_result_unknown_query_only(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, outcome in enumerate(cases["queryOutcomes"], start=1):
        runtime_root = _runtime(tmp_path, 200 + index)
        authority = _authority(cases, runtime_root)
        admission = Admission("GRANTED")
        provider = Provider(execution.ExternalResultUnknown, outcome)
        provider.unknown_once = True
        controller = _controller(execution, runtime_root, admission, provider, StageRunner())
        _expect_reason(execution, "NEWS_GRASP_EXECUTION_RESULT_UNKNOWN", lambda: _execute(controller, cases, authority))
        if outcome == "PRESENT":
            assert _execute(controller, cases, authority)["externalState"] == "COMMITTED"
            assert len(provider.dispatch_calls) == 1
        elif outcome == "ABSENT":
            assert _execute(controller, cases, authority)["externalState"] == "COMMITTED"
            assert len(provider.dispatch_calls) == 2
        else:
            _expect_reason(execution, "NEWS_GRASP_EXECUTION_RESULT_UNKNOWN", lambda: _execute(controller, cases, authority))
            assert len(provider.dispatch_calls) == 1
        assert len(set(provider.query_calls)) == 1


def test_s2_resume_skips_completed_stages(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, checkpoint_stage in enumerate(cases["checkpointStages"], start=1):
        next_stage = STAGES[STAGES.index(checkpoint_stage) + 1]
        runtime_root = _runtime(tmp_path, 300 + index)
        authority = _authority(cases, runtime_root)
        runner = StageRunner(next_stage)
        controller = _controller(execution, runtime_root, Admission("GRANTED"), Provider(), runner)
        _expect_reason(execution, "NEWS_GRASP_EXECUTION_CHILD_FAILED", lambda: _execute(controller, cases, authority))
        runner.fail_stage = None
        result = _execute(controller, cases, authority)
        assert result["externalState"] == "COMMITTED"
        assert runner.calls.count("harvest") == 1
        if checkpoint_stage == "model":
            assert runner.calls.count("model") == 1


def test_s2_checkpoint_corrupt_recovery(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, fault in enumerate(cases["checkpointFaults"], start=1):
        runtime_root = _runtime(tmp_path, 400 + index)
        authority = _authority(cases, runtime_root)
        provider = Provider()
        controller = _controller(execution, runtime_root, Admission("GRANTED"), provider, StageRunner("model"))
        _expect_reason(execution, "NEWS_GRASP_EXECUTION_CHILD_FAILED", lambda: _execute(controller, cases, authority))
        checkpoint = _checkpoint_paths(runtime_root, "harvest")[0]
        if fault == "corrupt":
            value = json.loads(checkpoint.read_text(encoding="utf-8"))
            value["outputHash"] = "f" * 64
            checkpoint.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        else:
            checkpoint.unlink()
        reason = "NEWS_GRASP_EXECUTION_CHECKPOINT_CORRUPT" if fault == "corrupt" else "NEWS_GRASP_EXECUTION_CHECKPOINT_MISSING"
        _expect_reason(execution, reason, lambda: _execute(controller, cases, authority))
        assert provider.dispatch_calls == []


def test_s2_child_failure_each_stage(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    for index, stage in enumerate(cases["childStages"], start=1):
        runtime_root = _runtime(tmp_path, 500 + index)
        authority = _authority(cases, runtime_root)
        admission = Admission("GRANTED")
        provider = Provider()
        runner = StageRunner(stage)
        controller = _controller(execution, runtime_root, admission, provider, runner)
        error = _expect_reason(execution, "NEWS_GRASP_EXECUTION_CHILD_FAILED", lambda: _execute(controller, cases, authority))
        assert getattr(error, "stage", stage) == stage
        runner.fail_stage = None
        assert _execute(controller, cases, authority)["externalState"] == "COMMITTED"
        assert len(admission.calls) == 1
        assert runner.calls.count(stage) == 2


def test_s2_external_state_machine_property(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    canonical = cases["externalStates"]
    for index, boundary in enumerate([None, *cases["crashBoundaries"]], start=1):
        runtime_root = _runtime(tmp_path, 600 + index)
        authority = _authority(cases, runtime_root)
        failed = {"active": boundary is not None}

        def hook(name: str, expected=boundary) -> None:
            if expected is not None and failed["active"] and name == expected:
                failed["active"] = False
                raise RuntimeError(name)

        controller = _controller(execution, runtime_root, Admission("GRANTED"), Provider(), StageRunner(), boundary_hook=hook)
        if boundary is None:
            assert _execute(controller, cases, authority)["externalState"] == "COMMITTED"
        else:
            with pytest.raises(Exception):
                _execute(controller, cases, authority)
        states = _persisted_states(runtime_root)
        assert states == canonical[: len(states)]
        if boundary is None:
            assert states[-1] == "COMMITTED"


def test_s2_fsync_and_response_loss_faults(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    wal = importlib.import_module("tools.news_grasp_cleanroom_wal")
    for index, fault in enumerate(cases["durabilityFaults"], start=1):
        runtime_root = _runtime(tmp_path, 700 + index)
        authority = _authority(cases, runtime_root)

        def fail_fsync(fd: int) -> None:
            raise OSError("test-owned fsync failure")

        def fail_flush(path: Path) -> None:
            raise OSError("test-owned parent flush failure")

        operations = wal.DurabilityOps(fsync=fail_fsync) if fault == "fsync" else wal.DurabilityOps(flush_parent=fail_flush)
        provider = Provider()
        controller = _controller(execution, runtime_root, Admission("GRANTED"), provider, StageRunner(), durability_ops=operations)
        _expect_reason(execution, "NEWS_GRASP_EXECUTION_DURABILITY_FAILED", lambda: _execute(controller, cases, authority))
        assert provider.dispatch_calls == []

    runtime_root = _runtime(tmp_path, 703)
    authority = _authority(cases, runtime_root)
    provider = Provider(execution.ExternalResultUnknown, "PRESENT")
    provider.unknown_once = True
    controller = _controller(execution, runtime_root, Admission("GRANTED"), provider, StageRunner())
    _expect_reason(execution, "NEWS_GRASP_EXECUTION_RESULT_UNKNOWN", lambda: _execute(controller, cases, authority))
    assert _execute(controller, cases, authority)["externalState"] == "COMMITTED"
    assert len(provider.dispatch_calls) == 1
    assert len(provider.query_calls) == 1


def test_s2_stale_fence_external_commit_rejected(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    runtime_root = _runtime(tmp_path, 800)
    authority = _authority(cases, runtime_root)
    provider = Provider()
    admission = Admission("GRANTED")
    mutated = {"done": False}

    def mutate_authority(name: str) -> None:
        if name != "after_CONFIRMED" or mutated["done"]:
            return
        mutated["done"] = True
        database = runtime_root / "control" / "control-ledger-v1.sqlite3"
        ledger = importlib.import_module("tools.news_grasp_cleanroom_ledger").ControlLedger(runtime_root)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE slots SET generation=generation+1,owner_key=?,fence_token=fence_token+1 WHERE schedule_id=? AND issue_date=? AND slot_kind='Scheduled'",
                ("s2-mutated-owner", cases["scheduleId"], cases["issueDate"]),
            )
            ledger._update_materialized_state(connection)
            connection.commit()

    controller = _controller(execution, runtime_root, admission, provider, StageRunner(), boundary_hook=mutate_authority)
    _expect_reason(execution, "NEWS_GRASP_EXECUTION_STALE_FENCE", lambda: _execute(controller, cases, authority))
    assert provider.dispatch_calls and provider.dispatch_calls[0]["idempotencyKey"]
    assert "COMMITTED" not in _persisted_states(runtime_root)
    _expect_reason(execution, "NEWS_GRASP_EXECUTION_STALE_FENCE", lambda: _execute(controller, cases, authority))
    assert len(provider.dispatch_calls) == 1


def test_s2_persisted_admission_is_revalidated_before_resume(tmp_path: Path) -> None:
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    cases = _cases()
    mutations = ("schema_missing", "decision_hash_tamper", "authority_binding_drift", "idempotency_binding_drift")
    observations: list[dict[str, Any]] = []
    for index, mutation in enumerate(mutations, start=900):
        runtime_root = _runtime(tmp_path, index)
        authority = _authority(cases, runtime_root)
        admission = Admission("GRANTED")
        provider = Provider()
        crashed = {"active": True}

        def hook(name: str) -> None:
            if crashed["active"] and name == "before_INTENT_DURABLE":
                crashed["active"] = False
                raise RuntimeError("test-owned crash before INTENT_DURABLE")

        controller = _controller(execution, runtime_root, admission, provider, StageRunner(), boundary_hook=hook)
        with pytest.raises(RuntimeError):
            _execute(controller, cases, authority)
        database = runtime_root / "control" / "execution-ledger-v1.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT execution_id,admission_json FROM executions").fetchone()
            assert row is not None and row["admission_json"]
            decision = json.loads(row["admission_json"])
            if mutation == "schema_missing":
                decision.pop("schemaVersion")
            elif mutation == "decision_hash_tamper":
                decision["decisionSha256"] = "0" * 64
            elif mutation == "authority_binding_drift":
                decision["authorityId"] = "drifted-authority"
                decision["decisionSha256"] = _sha({key: value for key, value in decision.items() if key != "decisionSha256"})
            else:
                decision["idempotencyKey"] = "drifted-idempotency-key"
                decision["decisionSha256"] = _sha({key: value for key, value in decision.items() if key != "decisionSha256"})
            connection.execute(
                "UPDATE executions SET admission_json=? WHERE execution_id=?",
                (json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")), row["execution_id"]),
            )
            connection.commit()
        try:
            result = _execute(controller, cases, authority)
        except Exception as caught:
            observations.append(
                {
                    "mutation": mutation,
                    "reason": getattr(caught, "reason", type(caught).__name__),
                    "dispatch": len(provider.dispatch_calls),
                    "query": len(provider.query_calls),
                    "states": _persisted_states(runtime_root),
                    "admissionCalls": len(admission.calls),
                }
            )
        else:
            observations.append(
                {
                    "mutation": mutation,
                    "reason": f"returned:{result.get('externalState')}",
                    "dispatch": len(provider.dispatch_calls),
                    "query": len(provider.query_calls),
                    "states": _persisted_states(runtime_root),
                    "admissionCalls": len(admission.calls),
                }
            )
    assert [item["mutation"] for item in observations] == list(mutations)
    assert [item["reason"] for item in observations] == [
        "NEWS_GRASP_EXECUTION_ADMISSION_INVALID"
    ] * len(mutations), observations
    assert all(item["dispatch"] == 0 and item["query"] == 0 for item in observations)
    assert all(item["admissionCalls"] == 1 for item in observations)
    assert all(
        not set(item["states"]) & {"INTENT_DURABLE", "DISPATCHED", "CONFIRMED", "COMMITTED"}
        for item in observations
    )
