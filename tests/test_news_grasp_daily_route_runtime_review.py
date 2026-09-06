from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from tools import news_grasp_daily_gate as daily
from tools import news_grasp_direct_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]
ISSUE_DATE = "2026-09-03"
RUN_INTENT = runtime.RUN_INTENT
TRIGGER_AT = "2026-09-03T06:00:00+09:00"


@pytest.fixture(autouse=True)
def _isolate_windows_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    """実OS mutexを使い、稼働中の本番writerとは名前だけ分離する。"""
    monkeypatch.setattr(runtime, "DAILY_PROCESS_MUTEX_NAME", "Local\\NewsGraspTest-" + uuid.uuid4().hex)


def _identity() -> dict[str, object]:
    return {
        "ok": True,
        "manifest_id": "",
        "manifest_reservation_id": "d" * 64,
        "observed_manifest_id": "a" * 64,
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


def _allow_fixture_state_root(monkeypatch: pytest.MonkeyPatch) -> None:
    store_type = runtime.DirectRunStore
    monkeypatch.setattr(
        runtime,
        "DirectRunStore",
        lambda path: store_type(path, test_only_allow_semantic_verifier=True),
    )


def test_ng_rrt_sequence_keeps_one_writer_in_memory_across_exact_six_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bearer leaseはstdoutへ出さず同一launcher processのmemoryだけで継承する。"""

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    monkeypatch.setattr(
        daily,
        "_resolve_run",
        lambda *_args, **_kwargs: (
            {"run_id": "direct-fixture-1", "writer_lease": "secret-lease", "fencing_token": 7},
            None,
        ),
    )
    observed: list[tuple[str, str, str, int]] = []

    def fake_operation(operation_id: str, **kwargs: object) -> dict[str, object]:
        observed.append(
            (
                operation_id,
                str(kwargs["run_id"]),
                str(kwargs["writer_lease"]),
                int(kwargs["fencing_token"]),
            )
        )
        return {
            "schemaVersion": daily.DAILY_GATE_SCHEMA,
            "ok": True,
            "status": "completed",
            "operation_id": operation_id,
            "run_id": kwargs["run_id"],
        }

    monkeypatch.setattr(daily, "run_daily_operation", fake_operation)
    receipts = daily.run_daily_sequence(
        store=store,
        cwd=ROOT,
        issue_date=ISSUE_DATE,
        scheduler_trigger_at=TRIGGER_AT,
    )

    assert [item[0] for item in observed] == list(daily.DAILY_OPERATIONS)
    assert {item[1:] for item in observed} == {("direct-fixture-1", "secret-lease", 7)}
    assert "secret-lease" not in json.dumps(receipts)


def test_ng_rrt_single_operation_cli_is_rejected_before_database_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", str(state_root))
    monkeypatch.setenv("NEWS_GRASP_REPO_ROOT", str(ROOT))
    monkeypatch.setenv("NEWS_GRASP_ISSUE_DATE", ISSUE_DATE)
    monkeypatch.setenv("NEWS_GRASP_SCHEDULER_TRIGGER_AT", TRIGGER_AT)
    monkeypatch.delenv("NEWS_GRASP_RUN_ID", raising=False)
    monkeypatch.delenv("NEWS_GRASP_WRITER_LEASE", raising=False)
    monkeypatch.delenv("NEWS_GRASP_FENCING_TOKEN", raising=False)
    assert daily._main(["scoped_contract_unit"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["failures"] == ["daily_single_operation_cli_forbidden_use_sequence_launcher"]
    assert not (state_root / "direct-mainline.sqlite3").exists()


def test_ng_rrt_launcher_runs_exact_sequence_and_never_projects_writer_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_fixture_state_root(monkeypatch)
    monkeypatch.setattr(daily, "resolve_daily_identity_context", lambda **_kwargs: _identity())
    monkeypatch.setattr(
        daily,
        "run_daily_sequence",
        lambda **_kwargs: [
            {
                "schemaVersion": daily.DAILY_GATE_SCHEMA,
                "ok": True,
                "status": "completed",
                "operation_id": operation,
                "run_id": "direct-launcher-1",
            }
            for operation in daily.DAILY_OPERATIONS
        ],
    )

    result = runtime.run_daily_mainline(
        repo_root=ROOT,
        state_root=tmp_path / "state",
        issue_date=ISSUE_DATE,
        scheduler_trigger_at=TRIGGER_AT,
    )

    assert result["ok"] is True
    assert result["operation_count"] == 6
    assert [row["operation_id"] for row in result["operation_receipts"]] == list(
        daily.DAILY_OPERATIONS
    )
    assert runtime._contains_writer_capability(result) is False


@pytest.mark.parametrize(
    "capability_key",
    ["writer_lease", "writerLease", "fencing_token", "fencingToken", "continuation_capability"],
)
def test_ng_rrt_launcher_rejects_nested_writer_capability_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability_key: str,
) -> None:
    _allow_fixture_state_root(monkeypatch)
    monkeypatch.setattr(daily, "resolve_daily_identity_context", lambda **_kwargs: _identity())
    monkeypatch.setattr(
        daily,
        "run_daily_sequence",
        lambda **_kwargs: [
            {
                "ok": True,
                "status": "completed",
                "operation_id": daily.DAILY_OPERATIONS[-1],
                "run_id": "direct-launcher-2",
                "nested": {capability_key: "must-not-leak"},
            }
        ]
        * len(daily.DAILY_OPERATIONS),
    )

    result = runtime.run_daily_mainline(
        repo_root=ROOT,
        state_root=tmp_path / "state",
        issue_date=ISSUE_DATE,
        scheduler_trigger_at=TRIGGER_AT,
    )

    assert result["ok"] is False
    assert result["failures"] == ["daily_writer_capability_projection_violation"]
    assert "must-not-leak" not in json.dumps(result)


def test_ng_rrt_protected_20260902_is_rejected_before_identity_or_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        daily,
        "resolve_daily_identity_context",
        lambda **_kwargs: calls.append("identity") or _identity(),
    )
    state_root = tmp_path / "state"

    result = runtime.run_daily_mainline(
        repo_root=ROOT,
        state_root=state_root,
        issue_date="2026-09-02",
        scheduler_trigger_at="2026-09-02T06:00:00+09:00",
    )

    assert result["ok"] is False
    assert result["failures"] == ["protected_release_reexecution_forbidden"]
    assert result["exact_successor"] == "explicit_new_release_authority_required"
    assert calls == []
    assert not state_root.exists()


def test_ng_rrt_launcher_cli_ignores_state_root_environment_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert "news_grasp_release_gate" not in Path(runtime.__file__).read_text(
        encoding="utf-8-sig"
    )
    trusted_local = tmp_path / "known-folder"
    trusted_local.mkdir()
    attacker_state = tmp_path / "attacker-state"
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        runtime,
        "_canonical_daily_state_root",
        lambda: trusted_local / "News-Grasp" / "direct-mainline",
    )
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", str(attacker_state))
    # launcherのPython固定入口を通過させ、state rootのauthorityだけを検証する。
    monkeypatch.setattr(runtime.sys, "executable", daily.DAILY_PYTHON)
    monkeypatch.setattr(
        runtime,
        "run_daily_mainline",
        lambda **kwargs: observed.update(kwargs)
        or {
            "schemaVersion": runtime.DAILY_SEQUENCE_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": ["fixture_stop"],
        },
    )

    assert runtime._main(["daily"]) == 1
    capsys.readouterr()
    assert observed["state_root"] == trusted_local / "News-Grasp" / "direct-mainline"
    assert observed["state_root"] != attacker_state


@pytest.mark.parametrize(
    ("poison_source_baseline", "poison_remote_base_sha"),
    [
        ("b" * 40, "d" * 40),
        ("b" * 40, "e" * 40),
    ],
)
def test_ng_rrt_identity_preflight_ignores_caller_baseline_and_remote_env(
    monkeypatch: pytest.MonkeyPatch,
    poison_source_baseline: str,
    poison_remote_base_sha: str,
) -> None:
    """source/remote identityはcaller環境変数ではなく実Git観測だけを採る。"""

    monkeypatch.setenv("NEWS_GRASP_SOURCE_BASELINE", poison_source_baseline)
    monkeypatch.setenv("NEWS_GRASP_REMOTE_BASE_SHA", poison_remote_base_sha)
    monkeypatch.setattr(
        daily,
        "_git_ref_sha",
        lambda _root, ref: "c" * 40 if ref == "origin/main" else "e" * 40,
    )
    monkeypatch.setattr(
        daily,
        "_git_is_ancestor",
        lambda _root, _candidate, _descendant: True,
    )

    result = daily.resolve_daily_identity_context(repo_root=ROOT, issue_date=ISSUE_DATE)

    assert result["ok"] is True
    assert result["source_baseline"] == "e" * 40
    assert result["remote_base_sha"] == "c" * 40
    assert result["source_baseline"] != poison_source_baseline
    assert result["remote_base_sha"] != poison_remote_base_sha


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


def test_ng_rrt_static_check_separates_unavailable_codex_config_from_daily_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex設定の観測不能だけでprofile/runtime schemaをRedにしない。"""

    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    monkeypatch.setattr(
        runtime,
        "validate_installed_automation_semantics",
        lambda: {
            "schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1",
            "ok": False,
            "status": "verification_unavailable",
            "verification_unavailable": True,
            "failures": ["automation_config_unavailable"],
        },
    )

    result = daily._default_static_check(
        store=store,
        issue_date=ISSUE_DATE,
        run={"title_status": "unavailable"},
    )

    assert result["ok"] is True
    assert result["status"] == "verified"
    assert result["failures"] == []
    assert result["profile"]["status"] == "validated"
    assert result["runtime_schema"]["ok"] is True
    assert result["automation"]["verification_unavailable"] is True


def test_ng_rrt_static_check_keeps_invalid_profile_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """profile検査そのものの失敗はreadiness debtへ降格しない。"""

    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )

    def invalid_profile() -> dict[str, object]:
        raise daily.NewsGraspGateProfileError("fixture_profile_invalid")

    monkeypatch.setattr(daily, "validate_profiles", invalid_profile)
    monkeypatch.setattr(runtime, "validate_installed_automation_semantics", _installed_green)

    result = daily._default_static_check(
        store=store,
        issue_date=ISSUE_DATE,
        run={"title_status": "unavailable"},
    )

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["gate_profile_red:fixture_profile_invalid"]


def test_ng_rrt_static_check_keeps_invalid_runtime_schema_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime schema検査そのものの失敗はreadiness debtへ降格しない。"""

    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )

    def invalid_schema() -> dict[str, object]:
        raise RuntimeError("fixture_schema_invalid")

    monkeypatch.setattr(store, "ensure_runtime_schema", invalid_schema)
    monkeypatch.setattr(runtime, "validate_installed_automation_semantics", _installed_green)

    result = daily._default_static_check(
        store=store,
        issue_date=ISSUE_DATE,
        run={"title_status": "unavailable"},
    )

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["runtime_schema_red:fixture_schema_invalid"]


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
                "fencingBindingHash": runtime.fencing_binding_hash(
                    run_id=run["run_id"],
                    generation=run["generation"],
                    writer_lease=run["writer_lease"],
                    fencing_token=run["fencing_token"],
                ),
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
                "fencingBindingHash": runtime.fencing_binding_hash(
                    run_id=run["run_id"],
                    generation=run["generation"],
                    writer_lease=run["writer_lease"],
                    fencing_token=run["fencing_token"],
                ),
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
    monkeypatch.setenv("NEWS_GRASP_RUN_ID", run["run_id"])
    monkeypatch.setenv("NEWS_GRASP_WRITER_LEASE", run["writer_lease"])
    monkeypatch.setenv("NEWS_GRASP_FENCING_TOKEN", str(run["fencing_token"]))
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

    first_rc = daily._main(["atomic_completion"], _test_only_allow_single_operation=True)
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
    second_rc = daily._main(["atomic_completion"], _test_only_allow_single_operation=True)
    second_lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert second_rc != 0
    assert len(second_lines) == 1
    second = json.loads(second_lines[0])
    assert second["ok"] is False
    assert len(finalizer_calls) == 1
    assert len(verifier_calls) == 0


def test_ng_t_rrt_protected_sequence_direct_call_is_red_before_resolve_without_state_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保護済みreleaseはresolver、outbox、runtime stateへ到達する前に停止する。"""

    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    state_root = tmp_path / "state"
    before = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }
    resolver_calls: list[object] = []

    def resolver_must_not_run(*_args: object, **_kwargs: object) -> object:
        resolver_calls.append(True)
        raise AssertionError("protected_release_must_precede_resolve_run")

    monkeypatch.setattr(daily, "_resolve_run", resolver_must_not_run)
    result = daily.run_daily_sequence(
        store=store,
        cwd=ROOT,
        issue_date="2026-09-02",
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-02T06:00:00+09:00",
        manifest_reservation_id="d" * 64,
        source_baseline="b" * 40,
        remote_base_sha="c" * 40,
        allowed_side_effect_ids=daily.DAILY_ALLOWED_SIDE_EFFECT_IDS,
    )

    assert len(result) == 1
    assert result[0]["ok"] is False
    assert result[0]["status"] == "red"
    assert result[0]["failures"] == ["protected_release_reexecution_forbidden"]
    assert result[0]["exact_successor"] == "explicit_new_release_authority_required"
    assert resolver_calls == []
    after = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_ng_t_rrt_protected_external_direct_call_is_red_before_identity_outbox_or_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保護済みreleaseのexternal直呼出しは全identity・副作用境界より前で拒否する。"""

    from tools import news_grasp_daily_external as external

    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date="2026-09-02",
        run_intent=runtime.RUN_INTENT,
        scheduler_trigger_at="2026-09-02T06:00:00+09:00",
        manifest_id="a" * 64,
    )
    state_root = tmp_path / "state"
    before = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }
    boundary_calls: list[str] = []

    def must_not_run(name: str):
        def blocked(*_args: object, **_kwargs: object) -> object:
            boundary_calls.append(name)
            raise AssertionError(f"protected_release_must_precede_{name}")

        return blocked

    monkeypatch.setattr(external, "_outbox_rows", must_not_run("outbox"))
    monkeypatch.setattr(external, "_adapter_for", must_not_run("adapter"))
    adapter_calls: list[dict[str, object]] = []

    def adapter(**kwargs: object) -> dict[str, object]:
        adapter_calls.append(dict(kwargs))
        raise AssertionError("protected_release_must_not_call_adapter")

    adapters = {
        operation_id: adapter
        for operation_id in external.EXTERNAL_OPERATION_ORDER
    }
    result = external.execute_external_publication(
        store=store,
        run_id=str(run["run_id"]),
        writer_lease=str(run["writer_lease"]),
        fencing_token=int(run["fencing_token"]),
        adapters=adapters,
        context={
            "issue_date": "2026-09-03",
            "run_intent": runtime.RUN_INTENT,
        },
    )

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["protected_release_reexecution_forbidden"]
    assert result["exact_successor"] == "explicit_new_release_authority_required"
    assert boundary_calls == []
    assert adapter_calls == []
    after = {
        path.relative_to(state_root).as_posix(): path.read_bytes()
        for path in state_root.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    "event_type",
    ("release_completed", "collection_completed", "partition_completed"),
)
def test_ng_t_rrt_canonical_generic_authority_append_is_rejected_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_type: str,
) -> None:
    """canonical authority eventは専用producer append以外から発行できない。"""

    from tools import news_grasp_release_gate as release_gate

    ledger = tmp_path / "canonical" / "release_ledger.jsonl"
    monkeypatch.setattr(release_gate, "_canonical_ledger_path", lambda: ledger)
    assert not hasattr(release_gate, "_AUTHORITY_APPEND_CAPABILITY")
    assert not hasattr(release_gate, "_append_authority_event")
    assert not hasattr(release_gate, "_record_release_completion_locked")
    assert not hasattr(release_gate, "_record_daily_promotion_locked")
    with pytest.raises(
        release_gate.NewsGraspReleaseGateError,
        match="release_authority_event_generic_append_forbidden",
    ):
        release_gate._append_ledger(
            ledger,
            event_type,
            release_id="fixture-generic-append",
        )
    with release_gate._ledger_lock(ledger):
        with pytest.raises(
            release_gate.NewsGraspReleaseGateError,
            match="release_authority_event_generic_append_forbidden",
        ):
            release_gate._append_ledger_locked(
                ledger,
                event_type,
                release_id="fixture-locked-append",
            )
    assert not ledger.exists()


def test_ng_t_rrt_broker_rejects_complete_forged_chain_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """callerは完全な偽collection/partition/release chain自体を発行できない。"""

    from tools import news_grasp_release_gate as release_gate
    from tools import news_grasp_scoped_test_broker as broker

    ledger = tmp_path / "canonical" / "release_ledger.jsonl"
    state_root = tmp_path / "direct-mainline"
    monkeypatch.setattr(release_gate, "_canonical_ledger_path", lambda: ledger)
    monkeypatch.setattr(
        release_gate,
        "_canonical_daily_state_root",
        lambda: state_root,
    )
    assert not hasattr(release_gate, "_AUTHORITY_APPEND_CAPABILITY")
    assert not hasattr(release_gate, "_append_authority_event")
    assert not hasattr(release_gate, "_record_release_completion_locked")
    assert not hasattr(release_gate, "_record_daily_promotion_locked")
    for event_type in ("collection_completed", "partition_completed", "release_completed"):
        with pytest.raises(
            release_gate.NewsGraspReleaseGateError,
            match="release_authority_event_generic_append_forbidden",
        ):
            release_gate._append_ledger(
                ledger,
                event_type,
                release_id="fixture-complete-forgery",
                receipt={"ok": True, "status": "green"},
            )
    receipt_path, key_path = broker._promotion_paths(state_root)
    assert not ledger.exists()
    assert not receipt_path.exists()
    assert not key_path.exists()


def _snapshot_identity_rows(store: runtime.DirectRunStore) -> list[tuple[object, ...]]:
    """再実行拒否がrow・generation・writerを増やさないことを比較する。"""

    with store.connect() as conn:
        return [
            tuple(row)
            for row in conn.execute(
                """
                SELECT run_id, automation_id, issue_date, run_intent, generation,
                       writer_lease, status, current_stage_index
                  FROM runs
                 ORDER BY generation, run_id
                """
            ).fetchall()
        ]


def test_ng_rrt_completed_identity_reexecution_is_blocked_before_new_row_or_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """completed済み同一identityの再実行はcwdや環境変数で迂回できない。"""

    repo = tmp_path / "first-clean-worktree"
    alternate_repo = tmp_path / "different-clean-worktree"
    repo.mkdir()
    alternate_repo.mkdir()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    first = runtime.start_run(
        store,
        automation_id=runtime.AUTOMATION_ID,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at=TRIGGER_AT,
        manifest_id="a" * 64,
        manifest_reservation_id="b" * 64,
        source_baseline="c" * 40,
        runtime_generation=runtime.RUNTIME_SCHEMA_V2,
        remote_base_sha="d" * 40,
        allowed_side_effect_ids=list(daily.DAILY_ALLOWED_SIDE_EFFECT_IDS),
    )
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE runs
               SET status='completed', current_stage_index=?, exact_successor='',
                   completed_at=?, completion_elapsed_seconds=?, completion_elapsed_at=?,
                   updated_at=?
             WHERE run_id=?
            """,
            (
                len(runtime.DIRECT_STAGES),
                TRIGGER_AT,
                1.0,
                TRIGGER_AT,
                TRIGGER_AT,
                first["run_id"],
            ),
        )
        conn.commit()

    before_rows = _snapshot_identity_rows(store)
    before_db = store.db_path.read_bytes()
    handler_calls: list[object] = []

    def handler_must_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
        handler_calls.append(True)
        raise AssertionError("completed_identity_must_stop_before_content_or_external_handler")

    poison_baseline = "e" * 40
    monkeypatch.setenv("NEWS_GRASP_SOURCE_BASELINE", poison_baseline)

    # resolverはcaller環境変数ではなく、実Git HEADだけをsource authorityにする。
    actual_head = daily._git_ref_sha(ROOT, "HEAD")
    observed_identity = daily.resolve_daily_identity_context(repo_root=ROOT, issue_date=ISSUE_DATE)
    assert observed_identity["source_baseline"] == actual_head
    assert observed_identity["source_baseline"] != poison_baseline

    monkeypatch.setattr(daily, "protected_release_failure", lambda **_kwargs: None)
    monkeypatch.setattr(daily, "resolve_daily_identity_context", lambda **_kwargs: _identity())
    monkeypatch.setattr(daily, "run_daily_operation", handler_must_not_run)
    monkeypatch.setattr(runtime, "DirectRunStore", lambda *_args, **_kwargs: store)
    result = runtime.run_daily_mainline(
        repo_root=alternate_repo,
        state_root=store.state_root,
        issue_date=ISSUE_DATE,
        scheduler_trigger_at=TRIGGER_AT,
    )

    assert result["ok"] is False
    assert result["failures"] == ["same_issue_completed_reexecution_forbidden"]
    assert result["operation_count"] == 1
    assert handler_calls == []
    assert runtime._contains_writer_capability(result) is False
    assert _snapshot_identity_rows(store) == before_rows
    assert store.db_path.read_bytes() == before_db
