from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest


ISSUE_DATE = "2026-09-05"
RUN_ID = "direct-2026-09-05-1-fixture"
CATEGORIES = ("fx", "ai", "business")


def _green_checkpoints(dag: dict[str, dict]) -> dict[str, dict]:
    return {
        artifact_id: {
            "artifactId": artifact_id,
            "status": "Green",
            "outputHash": f"hash-{artifact_id}",
        }
        for artifact_id in dag
    }


def test_red_repair_plan_disables_stale_content_completion_fast_path() -> None:
    from tools import news_grasp_daily_content as content

    class Ledger:
        @staticmethod
        def list_checkpoints() -> dict[str, dict]:
            return {
                "content_completion": {"status": "Green"},
                "editor": {"status": "Red"},
            }

        @staticmethod
        def load_repair_plan() -> dict[str, object]:
            return {"steps": [{"artifactId": "editor", "action": "repair_model"}]}

    assert content._repair_plan_requires_work(Ledger()) is True


def test_quality_failure_repairs_only_root_and_dirty_downstream() -> None:
    from tools import news_grasp_repair_registry as repair

    dag = repair.build_daily_artifact_dag(CATEGORIES)
    checkpoints = _green_checkpoints(dag)
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        categories=CATEGORIES,
        checkpoints=checkpoints,
        failures=[
            {
                "stage": "reporter_validation",
                "artifactId": "reporter:ai",
                "predicateId": "reporter_semantic_quality",
                "reasonCode": "SUMMARY_TOO_SHALLOW",
                "inputHash": "candidate-ai-v1",
            }
        ],
    )

    actions = {item["artifactId"]: item["action"] for item in plan["steps"]}
    assert plan["schemaVersion"] == "NEWS_GRASP_REPAIR_PLAN_V1"
    assert plan["failureSignatures"] == [
        "reporter_validation|reporter:ai|reporter_semantic_quality|SUMMARY_TOO_SHALLOW|candidate-ai-v1"
    ]
    assert actions["candidate:ai"] == "reuse"
    assert actions["reporter:fx"] == "reuse"
    assert actions["reporter:business"] == "reuse"
    assert actions["reporter:ai"] == "repair_model"
    assert actions["editor"] == "repair_model"
    assert actions["deepdive_model"] == "repair_model"
    assert actions["daily_audio"] == "rebuild_deterministic"
    assert actions["youtube_daily"] == "reconcile_external"
    assert plan["modelCallsRequired"] == 3
    assert plan["nextArtifactId"] == "reporter:ai"


def test_missing_deterministic_audio_rebuilds_without_model_call() -> None:
    from tools import news_grasp_repair_registry as repair

    dag = repair.build_daily_artifact_dag(CATEGORIES)
    checkpoints = _green_checkpoints(dag)
    checkpoints["daily_audio"]["status"] = "Red"
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        categories=CATEGORIES,
        checkpoints=checkpoints,
        failures=[
            {
                "stage": "daily_audio",
                "artifactId": "daily_audio",
                "predicateId": "audio_file_hash",
                "reasonCode": "ARTIFACT_MISSING",
                "inputHash": "daily-script-v1",
            }
        ],
    )

    dirty = {item["artifactId"]: item["action"] for item in plan["steps"] if item["action"] != "reuse"}
    assert dirty["daily_audio"] == "rebuild_deterministic"
    assert dirty["youtube_daily"] == "reconcile_external"
    assert "reporter:fx" not in dirty
    assert "editor" not in dirty
    assert "deepdive_model" not in dirty
    assert plan["modelCallsRequired"] == 0


def test_initial_five_category_plan_counts_three_reporter_shards() -> None:
    from tools import news_grasp_repair_registry as repair

    categories = ("fx", "ai", "it", "mobility", "game")
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        categories=categories,
        checkpoints={},
        failures=[],
    )

    assert plan["modelCallsRequired"] == 5


def test_repair_plan_persistence_is_atomic_and_hash_verified(tmp_path: Path) -> None:
    from tools import news_grasp_repair_registry as repair

    dag = repair.build_daily_artifact_dag(("fx",))
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        categories=("fx",),
        checkpoints=_green_checkpoints(dag),
        failures=[],
    )
    path = tmp_path / "repair-plan.json"
    repair.write_repair_plan(path, plan)

    loaded = repair.load_repair_plan(path)
    assert loaded == plan
    path.write_text(path.read_text(encoding="utf-8").replace("completed", "tampered"), encoding="utf-8")
    try:
        repair.load_repair_plan(path)
    except repair.NewsGraspRepairPlanError as exc:
        assert str(exc) == "NG_REPAIR_PLAN_HASH_INVALID"
    else:
        raise AssertionError("tampered plan was accepted")


def test_repair_plan_rejects_semantic_tamper_even_with_recomputed_hash() -> None:
    from tools import news_grasp_repair_registry as repair

    dag = repair.build_daily_artifact_dag(("fx",))
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        categories=("fx",),
        checkpoints=_green_checkpoints(dag),
        failures=[],
    )
    plan["steps"][0]["action"] = "repair_model"
    body = dict(plan)
    body.pop("planSha256")
    plan["planSha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with pytest.raises(repair.NewsGraspRepairPlanError, match="NG_REPAIR_PLAN_SEMANTIC_INVALID"):
        repair.validate_repair_plan(plan)


def test_runtime_repair_plan_binds_db_hash_and_current_fence(tmp_path: Path) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools import news_grasp_repair_registry as repair

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
    )
    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    plan = repair.build_repair_plan(
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        categories=("fx",),
        checkpoints=_green_checkpoints(repair.build_daily_artifact_dag(("fx",))),
        failures=[],
    )
    ledger.persist_repair_plan(plan)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE daily_repair_plans SET plan_sha256=? WHERE run_id=?",
            ("0" * 64, run["run_id"]),
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="daily_repair_plan_db_hash_mismatch"):
        ledger.load_repair_plan()

    ledger.persist_repair_plan(plan)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE daily_repair_plans SET fencing_token=? WHERE run_id=?",
            (int(run["fencing_token"]) + 1, run["run_id"]),
        )
        connection.commit()
    with pytest.raises(PermissionError, match="daily_repair_plan_fencing_token_mismatch"):
        ledger.load_repair_plan()


def test_model_call_budget_is_shared_idempotent_and_bounded(tmp_path: Path) -> None:
    from tools import news_grasp_repair_registry as repair

    ledger = repair.ModelCallBudgetLedger(
        tmp_path / "model-call-budget.json",
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        initial_limit=5,
        repair_limit=4,
    )
    for index in range(5):
        receipt = ledger.consume(
            call_id=f"initial-{index}",
            budget_class="initial",
            artifact_id=f"artifact-{index}",
            input_hash=f"input-{index}",
        )
        assert receipt["consumed"] is True
    duplicate = ledger.consume(
        call_id="initial-0",
        budget_class="initial",
        artifact_id="artifact-0",
        input_hash="input-0",
    )
    assert duplicate["consumed"] is False
    assert duplicate["idempotent"] is True
    with pytest.raises(repair.NewsGraspRepairPlanError, match="NG_MODEL_CALL_INITIAL_BUDGET_EXHAUSTED"):
        ledger.consume(
            call_id="initial-5",
            budget_class="initial",
            artifact_id="artifact-5",
            input_hash="input-5",
        )
    for index in range(4):
        ledger.consume(
            call_id=f"repair-{index}",
            budget_class="repair",
            artifact_id=f"repair-artifact-{index}",
            input_hash=f"repair-input-{index}",
        )
    with pytest.raises(repair.NewsGraspRepairPlanError, match="NG_MODEL_CALL_REPAIR_BUDGET_EXHAUSTED"):
        ledger.consume(
            call_id="repair-4",
            budget_class="repair",
            artifact_id="repair-artifact-4",
            input_hash="repair-input-4",
        )


def test_runtime_artifact_ledger_is_fenced_and_budget_survives_new_consumer(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_direct_runtime as runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
    )
    binding = {
        "run_id": run["run_id"],
        "issue_date": ISSUE_DATE,
        "writer_lease": run["writer_lease"],
        "fencing_token": run["fencing_token"],
    }
    ledger = runtime.DailyArtifactLedger(store, **binding)
    for index in range(5):
        ledger.reserve_model_call(
            call_id=f"initial-{index}",
            budget_class="initial",
            artifact_id=f"artifact-{index}",
            input_hash=f"input-{index}",
        )
    resumed_consumer = runtime.DailyArtifactLedger(store, **binding)
    assert resumed_consumer.model_call_usage() == {
        "initial": 5,
        "repair": 0,
        "total": 5,
    }
    with pytest.raises(RuntimeError, match="INITIAL_BUDGET_EXHAUSTED"):
        resumed_consumer.reserve_model_call(
            call_id="initial-5",
            budget_class="initial",
            artifact_id="artifact-5",
            input_hash="input-5",
        )
    with pytest.raises(PermissionError, match="fencing_token_fenced"):
        runtime.DailyArtifactLedger(
            store,
            **{**binding, "fencing_token": int(run["fencing_token"]) + 1},
        )


def test_runtime_model_budget_is_shared_across_same_issue_run_ids(tmp_path: Path) -> None:
    from tools import news_grasp_direct_runtime as runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
    )
    first_ledger = runtime.DailyArtifactLedger(
        store,
        run_id=first["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
    )
    for index in range(5):
        first_ledger.reserve_model_call(
            call_id=f"initial-{index}",
            budget_class="initial",
            artifact_id=f"artifact-{index}",
            input_hash=f"input-{index}",
        )
    with store.connect() as conn:
        conn.execute(
            "UPDATE runs SET status='stale_writer_rejected' WHERE run_id=?",
            (first["run_id"],),
        )
        conn.commit()
    second = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="e" * 64,
    )
    second_ledger = runtime.DailyArtifactLedger(
        store,
        run_id=second["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=second["writer_lease"],
        fencing_token=second["fencing_token"],
    )

    assert second_ledger.model_call_usage()["initial"] == 5
    with pytest.raises(RuntimeError, match="INITIAL_BUDGET_EXHAUSTED"):
        second_ledger.reserve_model_call(
            call_id="initial-second-run",
            budget_class="initial",
            artifact_id="artifact-second-run",
            input_hash="input-second-run",
        )


def test_runtime_model_call_completion_and_artifacts_commit_atomically(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_direct_runtime as runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="e" * 64,
    )
    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    ledger.reserve_model_call(
        call_id="reporter-call",
        budget_class="initial",
        artifact_id="reporter:fx",
        input_hash="candidate-hash",
    )
    committed = ledger.commit_model_call(
        call_id="reporter-call",
        artifacts={
            "reporter:fx": {
                "inputHash": "candidate-hash",
                "validatorId": "reporter_output_valid_v1",
                "payload": {"category": "fx", "records": [1]},
            }
        },
    )

    assert committed["reporter:fx"]["status"] == "Green"
    loaded = ledger.load_checkpoint(
        artifact_id="reporter:fx",
        input_hash="candidate-hash",
        validator_id="reporter_output_valid_v1",
    )
    assert loaded is not None
    assert loaded["payload"] == {"category": "fx", "records": [1]}
    duplicate = ledger.reserve_model_call(
        call_id="reporter-call",
        budget_class="initial",
        artifact_id="reporter:fx",
        input_hash="candidate-hash",
    )
    assert duplicate["idempotent"] is True
    assert duplicate["status"] == "completed"
