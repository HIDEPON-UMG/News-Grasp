from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import news_grasp_daily_gate as daily
from tools import news_grasp_direct_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]
ISSUE_DATE = "2026-09-03"
RUN_INTENT = runtime.RUN_INTENT
TRIGGER_AT = "2026-09-03T06:00:00+09:00"


def _identity() -> dict[str, object]:
    return {
        "ok": True,
        "manifest_id": "",
        "manifest_reservation_id": "a" * 64,
        "source_baseline": "b" * 40,
        "remote_base_sha": "c" * 40,
        "allowed_side_effect_ids": list(daily.DAILY_ALLOWED_SIDE_EFFECT_IDS),
        "failures": [],
        "manifest": {},
    }


def _installed_green(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1",
        "ok": True,
        "failures": [],
        "python_executable": (
            "C:\\Users\\hidek\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"
        ),
    }


def test_ng_rrt_cli_carries_one_writer_across_exact_six_operation_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自然CLIはcaller提供lease/capabilityなしでもexact argvから同writerへ接続する。"""

    state_root = tmp_path / "state"
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", str(state_root))
    monkeypatch.setenv("NEWS_GRASP_REPO_ROOT", str(ROOT))
    monkeypatch.setenv("NEWS_GRASP_ISSUE_DATE", ISSUE_DATE)
    monkeypatch.setenv("NEWS_GRASP_SCHEDULER_TRIGGER_AT", TRIGGER_AT)
    monkeypatch.delenv("NEWS_GRASP_DAILY_ROUTE_CAPABILITY", raising=False)
    monkeypatch.delenv("NEWS_GRASP_RUN_ID", raising=False)
    monkeypatch.delenv("NEWS_GRASP_WRITER_LEASE", raising=False)
    monkeypatch.delenv("NEWS_GRASP_FENCING_TOKEN", raising=False)
    monkeypatch.setattr(daily, "resolve_daily_identity_context", lambda **_kwargs: _identity())
    monkeypatch.setattr(runtime, "validate_installed_automation_semantics", _installed_green)

    assert daily._main(["static_check"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ok"] is True
    assert first["operation_id"] == "static_check"

    assert daily._main(["scoped_contract_unit"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["ok"] is True
    assert second["run_id"] == first["run_id"]

    store = runtime.DirectRunStore(state_root)
    inspected = runtime.inspect_run(store, run_id=first["run_id"])
    assert [row["operation_id"] for row in inspected["daily_operations"]] == [
        "static_check",
        "scoped_contract_unit",
    ]


def test_ng_rrt_cli_rejects_windowsapps_or_noncanonical_python_before_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", str(state_root))
    monkeypatch.setattr(
        daily.sys,
        "executable",
        r"C:\Users\hidek\AppData\Local\Microsoft\WindowsApps\python.exe",
    )

    assert daily._main(["static_check"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "python_runtime_not_approved"
    assert result["failures"] == ["fixed_python_3_12_required"]
    assert not (state_root / "runtime.sqlite3").exists()


def test_ng_rrt_direct_api_cannot_self_authorize_missing_command_or_capability(
    tmp_path: Path,
) -> None:
    """CLI外のcallerはcommand/capabilityを省略してproducerへ到達できない。"""

    store = runtime.DirectRunStore(tmp_path / "state")
    before = store.db_path.read_bytes()
    result = daily.run_daily_operation(
        "static_check",
        store=store,
        cwd=ROOT,
        issue_date=ISSUE_DATE,
        scheduler_trigger_at=TRIGGER_AT,
        manifest_id="a" * 64,
        source_baseline="b" * 40,
        remote_base_sha="c" * 40,
        allowed_side_effect_ids=daily.DAILY_ALLOWED_SIDE_EFFECT_IDS,
    )
    assert result["ok"] is False
    assert result["failures"] == ["daily_command_required_from_global_broker"]
    assert store.db_path.read_bytes() == before


def test_ng_rrt_zero_artifact_current_issue_cannot_be_green(tmp_path: Path) -> None:
    """当日artifactが無い状態をintegration完了として受理しない。"""

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    result = daily._default_current_issue_integration(
        store=store,
        run_id=run["run_id"],
        run=run,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
    )
    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"]


def test_ng_rrt_child_canonical_key_collision_and_red_never_mutate_state(
    tmp_path: Path,
) -> None:
    """canonical衝突とok=falseはparse段階で閉じ、DB bytesを変えない。"""

    collision = (
        '{"schemaVersion":"NEWS_GRASP_CHILD_RESULT_V1","inputHash":"x",'
        '"input_hash":"y","ok":true,"status":"verified"}'
    ).encode("utf-8")
    parsed = runtime.parse_child_result(collision, expected_input_hash="x")
    assert parsed["ok"] is False
    assert parsed["reason_code"] == "child_result_canonical_key_collision"

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    before = store.db_path.read_bytes()
    red = json.dumps(
        {
            "schema_version": runtime.CHILD_RESULT_SCHEMA,
            "input_hash": "x",
            "stage_id": runtime.DIRECT_STAGES[0],
            "ok": False,
            "status": "red",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(ValueError, match="child_result_ok_false"):
        runtime.apply_stage_result_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
            stage_id=runtime.DIRECT_STAGES[0],
            child_result=red,
            expected_input_hash="x",
        )
    assert store.db_path.read_bytes() == before


def test_ng_rrt_attached_observer_never_receives_writer_secret(tmp_path: Path) -> None:
    """single-flight attachはread-onlyでlease/fenceを再公開しない。"""

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    attached = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    assert attached["status"] == "attached"
    assert attached["run_id"] == first["run_id"]
    assert "writer_lease" not in attached
    assert "fencing_token" not in attached


def test_ng_rrt_mapping_child_result_is_test_only(tmp_path: Path) -> None:
    """production境界はbytes一行JSON以外を適用しない。"""

    store = runtime.DirectRunStore(tmp_path / "state")
    before = store.db_path.read_bytes()
    with pytest.raises(ValueError, match="child_result_mapping_test_only"):
        runtime.apply_stage_result_atomic(
            store,
            run_id="missing",
            writer_lease="missing",
            fencing_token=1,
            stage_id=runtime.DIRECT_STAGES[0],
            child_result={
                "schemaVersion": runtime.CHILD_RESULT_SCHEMA,
                "inputHash": "x",
                "ok": True,
                "status": "verified",
            },
            expected_input_hash="x",
        )
    assert store.db_path.read_bytes() == before


def test_ng_rrt_boolean_gate_is_explicitly_report_only() -> None:
    """旧boolean projectionはGreen表示でもcompletion authorityを持たない。"""

    from tools import news_grasp_gate_profiles as profiles

    result = profiles.evaluate_daily({oracle: True for oracle in profiles.DAILY_ORACLES})
    assert result["status"] == "green"
    assert result["report_only"] is True
    assert result["completion_authority"] == "none"


def test_ng_t_rrt_18_production_freeze_is_finalizer_only(tmp_path: Path) -> None:
    """T-RRT-18: productionの任意elapsed書込みはDB照会より前に拒否する。"""

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=False)
    before = store.db_path.read_bytes()

    with pytest.raises(PermissionError, match="completion_elapsed_finalizer_only"):
        runtime.freeze_completion_elapsed(
            store,
            run_id="missing-run",
            writer_lease="missing-lease",
            elapsed_seconds=1.0,
            fencing_token=1,
        )

    assert store.db_path.read_bytes() == before


def test_ng_t_rrt_19_incomplete_migration_journal_fails_closed_without_run_creation(
    tmp_path: Path,
) -> None:
    """T-RRT-19: started journalは暗黙Greenへ丸めず、run作成前に停止する。"""

    sqlite3 = __import__("sqlite3")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_migration_journal(
                journal_id,db_path,from_schema,to_schema,backup_path,status,started_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "fixture-incomplete-migration",
                str(store.db_path),
                "NEWS_GRASP_DIRECT_RUNTIME_V1",
                runtime.RUNTIME_SCHEMA_V2,
                "",
                "started",
                "2026-09-03T06:00:00+09:00",
            ),
        )
        before_migrations = conn.execute(
            "SELECT COUNT(*) FROM runtime_migrations"
        ).fetchone()[0]
        before_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.commit()

    store._schema_ready = True
    with pytest.raises(RuntimeError, match="runtime_schema_migration_incomplete"):
        store.ensure_runtime_schema()

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_migrations").fetchone()[0] == before_migrations
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == before_runs


def test_ng_t_rrt_20_notification_ledger_migration_receipt_is_v2_and_hashed(
    tmp_path: Path,
) -> None:
    """T-RRT-20: schema migration receiptは通知ledger V2を明示する。"""

    hashlib = __import__("hashlib")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    migration = store.ensure_runtime_schema()
    receipt = migration["migration_receipt"]
    notification = receipt["notificationLedgerMigration"]

    assert notification["schemaVersion"] == "NEWS_GRASP_NOTIFICATION_LEDGER_V2"
    assert notification["table"] == "notification_ledger"
    assert notification["status"] == "initialized"

    for body, hash_field in (
        (receipt, "receiptSha256"),
        (receipt, "migrationHash"),
        (notification, "receiptSha256"),
        (notification, "migrationHash"),
    ):
        if hash_field not in body:
            continue
        unsigned = dict(body)
        expected = hashlib.sha256(
            runtime._json_dump(
                {key: value for key, value in unsigned.items() if key != hash_field}
            ).encode("utf-8")
        ).hexdigest()
        assert body[hash_field] == expected


def test_ng_t_rrt_21_daily_finalizer_consumes_fresh_public_verifier_once_and_freezes_elapsed(
    tmp_path: Path,
) -> None:
    """T-RRT-21: Daily六receiptとfresh public観測を唯一のfinalizerで閉じる。"""

    from datetime import datetime, timedelta

    class Clock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = Clock()
    network_verifier_calls: list[dict[str, object]] = []

    def network_verifier(**kwargs: object) -> dict[str, object]:
        """finalizerからの追加network観測を検出する禁止fixture。"""

        network_verifier_calls.append(dict(kwargs))
        raise AssertionError("daily_finalizer_must_not_call_network_verifier")

    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        semantic_verifier=network_verifier,
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        scheduler_trigger_at=TRIGGER_AT,
        manifest_id="a" * 64,
    )

    phase5_public_observation_calls = 1
    for index, operation_id in enumerate(runtime.DAILY_OPERATION_ORDER):
        input_hash = f"fixture-input-{index}"
        handler_id = f"fixture.handler.{operation_id}"
        producer_receipt: dict[str, object] = {
            "schemaVersion": f"FIXTURE_{operation_id.upper()}_V1",
            "ok": True,
            "status": "verified",
            "operation_id": operation_id,
        }
        if operation_id == "consumer_public_verification":
            # claim/apply前のrun.updated_atがconsumer観測の束縛対象である。
            consumer_updated_at = runtime.inspect_run(
                store, run_id=run["run_id"]
            )["updated_at"]
            freshness = {
                "runId": run["run_id"],
                "issueDate": ISSUE_DATE,
                "runIntent": runtime.RUN_INTENT,
                "generation": run["generation"],
                "manifestId": run["manifest_id"],
                "fencingToken": run["fencing_token"],
                "observedAt": runtime._iso(clock.value),
                "updatedAt": consumer_updated_at,
                "observationNonce": "fixture-public-observation-1",
            }
            observation = {
                "schemaVersion": "NEWS_GRASP_PUBLIC_OBSERVATION_V2",
                "ok": True,
                "status": "verified",
                "completion_mode": "direct_public_v2",
                "issue_date": ISSUE_DATE,
                "public_surfaces": {
                    name: {
                        "issue_date": ISSUE_DATE,
                        "semantic_ok": True,
                        "status": "verified",
                    }
                    for name in runtime.PUBLIC_SURFACES
                },
                "freshnessBinding": dict(freshness),
                "networkVerifierCalls": phase5_public_observation_calls,
            }
            producer_receipt = {
                "schemaVersion": runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
                "ok": True,
                "status": "verified",
                "operation_id": operation_id,
                "observation": observation,
                "freshnessBinding": freshness,
                "observationNonce": freshness["observationNonce"],
                "observationToken": freshness["observationNonce"],
            }
        claim = runtime.claim_daily_operation(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            fencing_token=run["fencing_token"],
        )
        assert claim["status"] == "claimed"
        receipt = runtime.apply_daily_operation_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt=producer_receipt,
            fencing_token=run["fencing_token"],
        )
        assert receipt["ok"] is True
        assert receipt["status"] == "completed"

    clock.value += timedelta(seconds=123)
    final = runtime.finalize_public_completion(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        exact_successor="public_completion",
        fencing_token=run["fencing_token"],
    )
    assert final["ok"] is True
    assert final["status"] == "completed"
    assert len(network_verifier_calls) == 0
    assert phase5_public_observation_calls == 1
    assert final["publicProbe"]["observation"]["networkVerifierCalls"] == 1
    assert final["public_probe_source"] == "consumer_public_verification_receipt"
    assert final["completed_at"] == "2026-09-03T06:02:03+09:00"
    assert final["completion_elapsed_seconds"] == 123.0

    frozen_elapsed = final["completion_elapsed_seconds"]
    before_repeated_finalize = store.db_path.read_bytes()
    clock.value += timedelta(minutes=10)
    inspected = runtime.inspect_run(store, run_id=run["run_id"])
    assert inspected["completion_elapsed_seconds"] == frozen_elapsed

    with pytest.raises((PermissionError, RuntimeError)) as repeated:
        runtime.finalize_public_completion(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            exact_successor="public_completion",
            fencing_token=run["fencing_token"],
        )
    assert str(repeated.value) in {"run_not_writable", "finalizer_already_consumed"}
    assert len(network_verifier_calls) == 0
    assert store.db_path.read_bytes() == before_repeated_finalize


def test_ng_t_rrt_22_daily_cli_calls_unique_finalizer_and_emits_one_line_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T-RRT-22: atomic receiptだけでGreenにせず、CLIが一回だけfinalizerへ渡す。"""

    from datetime import datetime

    class Clock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = Clock()
    sqlite_root = tmp_path / "LocalAppData"
    state_root = sqlite_root / "News-Grasp" / "direct-mainline"
    repo = tmp_path / "repo"
    repo.mkdir()
    # CLIが再生成するDirectRunStoreも同一clockを使い、consumer receiptの
    # updatedAtをapply直後のrun rowへ決定的に束縛する。
    monkeypatch.setattr(runtime, "_now_jst", clock)
    store = runtime.DirectRunStore(
        state_root,
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        scheduler_trigger_at=TRIGGER_AT,
        manifest_id="a" * 64,
    )
    phase5_public_observation_calls = 1
    for index, operation_id in enumerate(runtime.DAILY_OPERATION_ORDER[:-1]):
        input_hash = f"fixture-cli-input-{index}"
        handler_id = f"fixture.cli.handler.{operation_id}"
        producer_receipt: dict[str, object] = {
            "schemaVersion": f"FIXTURE_CLI_{operation_id.upper()}_V1",
            "ok": True,
            "status": "verified",
            "operation_id": operation_id,
        }
        if operation_id == "consumer_public_verification":
            consumer_updated_at = runtime.inspect_run(
                store, run_id=run["run_id"]
            )["updated_at"]
            freshness = {
                "runId": run["run_id"],
                "issueDate": ISSUE_DATE,
                "runIntent": runtime.RUN_INTENT,
                "generation": run["generation"],
                "manifestId": run["manifest_id"],
                "fencingToken": run["fencing_token"],
                "observedAt": runtime._iso(clock.value),
                "updatedAt": consumer_updated_at,
                "observationNonce": "fixture-cli-public-observation-1",
            }
            observation = {
                "schemaVersion": "NEWS_GRASP_PUBLIC_OBSERVATION_V2",
                "ok": True,
                "status": "verified",
                "completion_mode": "direct_public_v2",
                "issue_date": ISSUE_DATE,
                "public_surfaces": {
                    name: {
                        "issue_date": ISSUE_DATE,
                        "semantic_ok": True,
                        "status": "verified",
                    }
                    for name in runtime.PUBLIC_SURFACES
                },
                "freshnessBinding": dict(freshness),
                "networkVerifierCalls": phase5_public_observation_calls,
            }
            producer_receipt = {
                "schemaVersion": runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
                "ok": True,
                "status": "verified",
                "operation_id": operation_id,
                "observation": observation,
                "freshnessBinding": freshness,
                "observationNonce": freshness["observationNonce"],
                "observationToken": freshness["observationNonce"],
            }
        runtime.claim_daily_operation(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            fencing_token=run["fencing_token"],
        )
        runtime.apply_daily_operation_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt=producer_receipt,
            fencing_token=run["fencing_token"],
        )

    stored_consumer = runtime.get_daily_operation_receipt(
        store,
        run_id=run["run_id"],
        operation_id="consumer_public_verification",
    )
    assert stored_consumer is not None
    assert stored_consumer["producer_receipt"]["schemaVersion"] == runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA
    assert stored_consumer["producer_receipt"]["observation"]["networkVerifierCalls"] == 1

    monkeypatch.setenv("LOCALAPPDATA", str(sqlite_root))
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", str(state_root))
    monkeypatch.setenv("NEWS_GRASP_REPO_ROOT", str(repo))
    monkeypatch.setenv("NEWS_GRASP_ISSUE_DATE", ISSUE_DATE)
    monkeypatch.setenv("NEWS_GRASP_SCHEDULER_TRIGGER_AT", TRIGGER_AT)
    monkeypatch.delenv("NEWS_GRASP_RUN_ID", raising=False)
    monkeypatch.delenv("NEWS_GRASP_WRITER_LEASE", raising=False)
    monkeypatch.delenv("NEWS_GRASP_FENCING_TOKEN", raising=False)
    monkeypatch.setattr(daily, "resolve_daily_identity_context", lambda **_kwargs: _identity())

    public_completion = __import__(
        "tools.news_grasp_direct_completion", fromlist=["verify_direct_public_completion"]
    )
    verifier_calls: list[dict[str, object]] = []

    def fake_public_verifier(**kwargs: object) -> dict[str, object]:
        """Daily finalizerが追加network観測をしないことを検出する。"""

        verifier_calls.append(dict(kwargs))
        return {
            "ok": True,
            "status": "verified",
            "completion_mode": "direct_public_v2",
            "issue_date": ISSUE_DATE,
            "public_surfaces": {
                name: {
                    "issue_date": ISSUE_DATE,
                    "semantic_ok": True,
                    "status": "verified",
                }
                for name in runtime.PUBLIC_SURFACES
            },
        }

    monkeypatch.setattr(public_completion, "verify_direct_public_completion", fake_public_verifier)
    real_finalizer = runtime.finalize_public_completion
    finalizer_calls: list[dict[str, object]] = []

    def counted_finalizer(*args: object, **kwargs: object) -> dict[str, object]:
        finalizer_calls.append(dict(kwargs))
        return real_finalizer(*args, **kwargs)

    monkeypatch.setattr(runtime, "finalize_public_completion", counted_finalizer)

    first_rc = daily._main(["atomic_completion"])
    first_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert first_rc == 0
    assert len(first_lines) == 1
    first = json.loads(first_lines[0])
    assert first["ok"] is True
    assert first["status"] == "completed"
    assert len(finalizer_calls) == 1
    assert len(verifier_calls) == 0

    monkeypatch.setenv("NEWS_GRASP_RUN_ID", run["run_id"])
    monkeypatch.setenv("NEWS_GRASP_WRITER_LEASE", run["writer_lease"])
    monkeypatch.setenv("NEWS_GRASP_FENCING_TOKEN", str(run["fencing_token"]))
    second_rc = daily._main(["atomic_completion"])
    second_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert second_rc != 0
    assert len(second_lines) == 1
    second = json.loads(second_lines[0])
    assert second["ok"] is False
    assert len(finalizer_calls) == 1
    assert len(verifier_calls) == 0
