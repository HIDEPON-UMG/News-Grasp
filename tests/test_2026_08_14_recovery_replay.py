from __future__ import annotations

import json
import hashlib
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "2026_08_14_recovery_replay.json"


def test_sanitized_replay_contains_every_observed_stop_class() -> None:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert value["issueDate"] == "2026-08-14"
    assert value["schemaVersion"] == "NEWS_GRASP_RECOVERY_REPLAY_FIXTURE_V1"
    assert {event["expectedStop"] for event in value["events"]} == {
        "execution_receipt_v2_not_sealed",
        "capability_reservation_preflight",
        "single_reconcile_then_no_restart",
        "checkpoint_branch_gate",
        "issue_date_single_flight",
        "deterministic_materialization_quality_gate",
        "readiness_debt_sidecar",
        "common_finalizer_post_green_15_minute_gate",
    }
    assert "c:\\users\\" not in FIXTURE.read_text(encoding="utf-8").lower()


def test_issue_date_transaction_is_single_flight_fenced_and_terminal(tmp_path: Path) -> None:
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    store = RecoveryTransactionStore(tmp_path)
    started = datetime(2026, 8, 14, 6, 40, tzinfo=timezone(timedelta(hours=9)))
    owner = store.acquire(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        owner_id="deadman",
        now=started,
    )
    attached = store.acquire(
        issue_date="2026-08-14",
        trigger="automation_0640",
        owner_id="automation",
        now=started + timedelta(seconds=1),
    )

    assert owner["status"] == "acquired"
    assert owner["fencingToken"] == 1
    assert attached["status"] == "attached"
    assert attached["processExitCode"] == 3
    assert attached["transactionId"] == owner["transactionId"]

    stale = store.acquire(
        issue_date="2026-08-14",
        trigger="watcher_failure",
        owner_id="watcher",
        now=started + timedelta(minutes=6),
    )
    assert stale["status"] == "recovered_stale_owner"
    assert stale["fencingToken"] == 2

    terminal = store.complete(
        issue_date="2026-08-14",
        owner_id="watcher",
        fencing_token=2,
        terminal={"terminal": "audit_recovered_green", "exitCode": 0},
        now=started + timedelta(minutes=20),
    )
    projected = store.acquire(
        issue_date="2026-08-14",
        trigger="deadman_hourly",
        owner_id="deadman-second-pass",
        now=started + timedelta(minutes=21),
    )
    assert terminal["status"] == "terminal"
    assert projected["status"] == "terminal_projection"
    assert projected["terminal"]["terminal"] == "audit_recovered_green"


def test_canonical_ensure_executes_once_then_projects_terminal(tmp_path: Path) -> None:
    from tools.audit_recovery_control import ensure_audit_0640

    calls: list[str] = []

    def execute(*, issue_date: str) -> dict[str, object]:
        calls.append(issue_date)
        return {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V2",
            "issueDate": issue_date,
            "terminal": "audit_normal_green",
            "exitCode": 0,
        }

    first = ensure_audit_0640(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        transaction_root=tmp_path,
        executor=execute,
    )
    second = ensure_audit_0640(
        issue_date="2026-08-14",
        trigger="automation_0640",
        transaction_root=tmp_path,
        executor=execute,
    )

    assert calls == ["2026-08-14"]
    assert first["transactionStatus"] == "terminal"
    assert second["transactionStatus"] == "terminal_projection"
    assert second["terminal"] == "audit_normal_green"


def test_stale_fencing_token_cannot_complete_new_owner(tmp_path: Path) -> None:
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    store = RecoveryTransactionStore(tmp_path)
    started = datetime(2026, 8, 14, 6, 40, tzinfo=timezone(timedelta(hours=9)))
    first = store.acquire(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        owner_id="owner-a",
        now=started,
    )
    store.acquire(
        issue_date="2026-08-14",
        trigger="watcher_failure",
        owner_id="owner-b",
        now=started + timedelta(minutes=6),
    )

    try:
        store.complete(
            issue_date="2026-08-14",
            owner_id="owner-a",
            fencing_token=first["fencingToken"],
            terminal={"terminal": "audit_major_incident_open", "exitCode": 2},
            now=started + timedelta(minutes=7),
        )
    except ValueError as error:
        assert str(error) == "AUDIT_RECOVERY_FENCING_TOKEN_STALE"
    else:
        raise AssertionError("stale owner completed a newer transaction")


def test_transaction_lock_file_residue_is_not_a_permanent_denial(
    tmp_path: Path,
) -> None:
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    (tmp_path / ".2026-08-14.lock").write_bytes(b"\0")
    result = RecoveryTransactionStore(tmp_path).acquire(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        owner_id="owner-after-crash",
    )

    assert result["status"] == "acquired"


def test_expired_lease_cannot_take_over_a_live_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    identities = {111: "process-a", 222: "process-b"}
    monkeypatch.setattr(
        RecoveryTransactionStore,
        "_process_identity",
        staticmethod(lambda pid: identities.get(pid, "")),
    )
    store = RecoveryTransactionStore(tmp_path)
    started = datetime(2026, 8, 14, 6, 40, tzinfo=timezone(timedelta(hours=9)))
    owner = store.acquire(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        owner_id="owner-a",
        owner_pid=111,
        now=started,
    )
    attached = store.acquire(
        issue_date="2026-08-14",
        trigger="automation_0640",
        owner_id="owner-b",
        owner_pid=222,
        now=started + timedelta(minutes=6),
    )

    assert owner["fencingToken"] == 1
    assert attached["status"] == "attached_owner_alive"
    assert attached["fencingToken"] == 1


def test_heartbeat_retries_transient_lock_contention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tools import audit_recovery_control
    from tools.news_grasp_recovery_transaction import RecoveryTransactionStore

    original = RecoveryTransactionStore.renew
    attempts = 0

    def renew_once_busy(self, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("AUDIT_RECOVERY_TRANSACTION_BUSY")
        return original(self, **kwargs)

    monkeypatch.setattr(RecoveryTransactionStore, "renew", renew_once_busy)

    def execute(*, issue_date: str) -> dict[str, object]:
        time.sleep(0.06)
        return {
            "schemaVersion": "AUDIT_RECOVERY_DECISION_V2",
            "issueDate": issue_date,
            "terminal": "audit_normal_green",
            "exitCode": 0,
        }

    result = audit_recovery_control.ensure_audit_0640(
        issue_date="2026-08-14",
        trigger="deadman_0640",
        transaction_root=tmp_path,
        executor=execute,
        heartbeat_interval_seconds=0.01,
    )

    assert attempts >= 2
    assert result["terminal"] == "audit_normal_green"


def test_audit_deadlines_are_fixed_to_issue_date_0640_jst() -> None:
    from tools.news_grasp_recovery_transaction import audit_deadlines

    value = audit_deadlines("2026-08-14")

    assert value["auditSloAnchor"] == "2026-08-14T06:40:00+09:00"
    assert value["preflightDeadlineAt"] == "2026-08-14T06:45:00+09:00"
    assert value["targetCloseoutReserveAt"] == "2026-08-14T07:25:00+09:00"
    assert value["targetDeadlineAt"] == "2026-08-14T07:40:00+09:00"
    assert value["highCostCutoffAt"] == "2026-08-14T07:55:00+09:00"
    assert value["hardDeadlineAt"] == "2026-08-14T08:10:00+09:00"


def test_artifact_delta_without_checkpoint_is_typed_major_not_full() -> None:
    import hashlib

    from tools.news_grasp_operational_contract import select_recovery_branch_from_truth

    body = {
        "schemaVersion": "NEWS_GRASP_OPERATIONAL_TRUTH_V1",
        "issuer": "tools.audit_recovery_control.actual_observer",
        "issueDate": "2026-08-14",
        "stopPointKnown": False,
        "scheduledAttemptReachedRunner": True,
        "artifactDelta": {"exists": True, "manifestSha256": "a" * 64},
    }
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    assert select_recovery_branch_from_truth(body) == "major_incident_fail_closed"


def test_completion_outcome_boundaries_and_readiness_debt_are_separate() -> None:
    from tools.news_grasp_completion_guard import build_completion_outcome_envelope

    anchor = "2026-08-14T06:40:00+09:00"
    target = build_completion_outcome_envelope(
        issue_date="2026-08-14",
        completion_state_vector={"schemaVersion": "COMPLETION_STATE_VECTOR_V3"},
        public_green_at="2026-08-14T07:25:00+09:00",
        done_at="2026-08-14T07:40:00+09:00",
        recovery_operation_count=1,
        readiness_debt=None,
    )
    budget = build_completion_outcome_envelope(
        issue_date="2026-08-14",
        completion_state_vector={"schemaVersion": "COMPLETION_STATE_VECTOR_V3"},
        public_green_at="2026-08-14T07:55:00+09:00",
        done_at="2026-08-14T08:10:00+09:00",
        recovery_operation_count=1,
        readiness_debt={"reasonCode": "scheduled_task_missed_runs"},
    )
    no_recovery = build_completion_outcome_envelope(
        issue_date="2026-08-14",
        completion_state_vector={"schemaVersion": "COMPLETION_STATE_VECTOR_V3"},
        public_green_at="2026-08-14T07:31:00+09:00",
        done_at="2026-08-14T07:41:00+09:00",
        recovery_operation_count=0,
        readiness_debt=None,
    )

    assert target["auditSloAnchor"] == anchor
    assert target["targetMet"] is True
    assert target["processExitCode"] == 0
    assert budget["repairBudgetMet"] is True
    assert budget["publicAuthorityPreserved"] is True
    assert budget["processExitCode"] == 2
    assert no_recovery["repairBudgetMet"] is False
    assert no_recovery["automationOutcome"] == "audit_major_incident_open"


@pytest.mark.parametrize(
    ("overall_minutes", "post_green_minutes", "recovery_count", "target_met", "budget_met", "exit_code"),
    (
        (59, 14, 1, True, False, 0),
        (60, 15, 1, True, False, 0),
        (61, 15, 1, False, True, 0),
        (89, 15, 1, False, True, 0),
        (90, 15, 1, False, True, 0),
        (91, 15, 1, False, False, 2),
        (60, 16, 1, False, False, 2),
        (61, 14, 0, False, False, 2),
    ),
)
def test_slo_minute_boundaries_are_exact(
    overall_minutes: int,
    post_green_minutes: int,
    recovery_count: int,
    target_met: bool,
    budget_met: bool,
    exit_code: int,
) -> None:
    from tools.news_grasp_completion_guard import build_completion_outcome_envelope

    anchor = datetime(2026, 8, 14, 6, 40, tzinfo=timezone(timedelta(hours=9)))
    done = anchor + timedelta(minutes=overall_minutes)
    public_green = done - timedelta(minutes=post_green_minutes)
    result = build_completion_outcome_envelope(
        issue_date="2026-08-14",
        completion_state_vector={"schemaVersion": "COMPLETION_STATE_VECTOR_V3"},
        public_green_at=public_green.isoformat(),
        done_at=done.isoformat(),
        recovery_operation_count=recovery_count,
        readiness_debt=None,
    )

    assert result["targetMet"] is target_met
    assert result["repairBudgetMet"] is budget_met
    assert result["processExitCode"] == exit_code


def test_all_recovery_triggers_route_to_canonical_ensure_owner() -> None:
    deadman = (REPO / "scripts" / "ops" / "news-grasp-deadman.ps1").read_text(
        encoding="utf-8-sig"
    )
    watcher = (REPO / "scripts" / "ops" / "watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    daily = (REPO / "tools" / "news_grasp_daily_control.py").read_text(
        encoding="utf-8"
    )

    assert "audit_recovery_control.py" in deadman
    assert "'-I' '-S' '-B' $AuditControlPath" in deadman
    assert "'-m' 'tools.audit_recovery_control'" not in deadman
    assert "ensure-0640" in deadman
    assert "execute-audit-0640" not in deadman
    assert "ensure-0640" in watcher
    assert "Start-RecoveryFromDecision" not in watcher
    assert "[string] $RecoveryDecisionPath" not in watcher
    assert "ensure_audit_0640" in daily


def test_execution_receipt_v2_binds_branch_python_reservation_and_deadlines() -> None:
    from tools import news_grasp_recovery_receipts as receipts

    runner = (REPO / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert receipts.EXECUTION_SCHEMA == "RECOVERY_EXECUTION_RECEIPT_V2"
    assert "RECOVERY_EXECUTION_BRANCH_MISMATCH" in runner
    assert "RECOVERY_EXECUTION_PYTHON_MISMATCH" in runner
    assert "RECOVERY_EXECUTION_HARD_DEADLINE_EXCEEDED" in runner
    assert "Acquire-RecoveryHighCostBudget -Stage \"model:$FlowName\"" in runner
    assert "$script:HighCostCallSequence -ge $script:RecoveryMaxExternalModelCalls" in runner
    assert "$TimeoutSec = [Math]::Max(1, [Math]::Min($TimeoutSec, $remainingSeconds))" in runner
    assert runner.index("Acquire-RecoveryHighCostBudget -Stage \"model:$FlowName\"") < runner.index("& $CodexWrapper @codexArgs")
    assert 'Acquire-RecoveryHighCostBudget -Stage "model:reporter:$waveCat:attempt:$Attempt"' in runner
    assert "$reporterTimeoutSec" in runner
    assert "$reporterIdleTimeoutSec" in runner
    assert runner.index('Acquire-RecoveryHighCostBudget -Stage "model:reporter:$waveCat:attempt:$Attempt"') < runner.index("$job = Start-Job")


def test_recovery_controllers_reject_ambient_python_and_module_resolution() -> None:
    deadman = (REPO / "scripts/ops/news-grasp-deadman.ps1").read_text(
        encoding="utf-8-sig"
    )
    watcher = (REPO / "scripts/ops/watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    installer = (REPO / "scripts/ops/install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )

    for source in (deadman, watcher):
        assert "auditControlSha256" in source
        assert "audit_recovery_control.py" in source
        assert "https://github.com/HIDEPON-UMG/News-Grasp.git" in source
        assert "ls-remote" in source
        assert "status --porcelain --untracked-files=all" in source
        assert "Get-AuthenticodeSignature" in source
        assert "'-I' '-S' '-B'" in source
        assert "return 'python'" not in source
        assert "'-m' 'tools.audit_recovery_control'" not in source
    assert "auditControlPath" in installer
    assert "auditControlSha256" in installer


def test_completion_guard_uses_validated_manifest_and_state_snapshots() -> None:
    source = (REPO / "tools" / "news_grasp_completion_guard.py").read_text(
        encoding="utf-8"
    )

    assert 'receipt.pop("_validatedRunnerStateSnapshot", {})' in source
    assert 'receipt.pop("_validatedManifestSnapshot", {})' in source
    assert "_load_json(args.runner_state)" not in source
    assert "_load_json(Path(manifest_path))" not in source


def test_new_audit_decisions_and_public_authority_use_v2() -> None:
    from tools import audit_recovery_control as control

    incident = control._incident(
        issue_date="2026-08-14",
        scheduled_status="failed",
        recovery_status="not_started",
        reason_code="fixture",
    )
    source = Path(control.__file__).read_text(encoding="utf-8")

    assert incident["schemaVersion"] == "AUDIT_RECOVERY_DECISION_V2"
    assert '"schemaVersion": "COMPLETION_AUTHORITY_V2"' in source


def test_notification_skip_is_not_success_and_zero_audience_needs_receipt(
    tmp_path: Path,
) -> None:
    from tools import daily_self_heal, send_push

    issue_date = send_push._today_jst_str()
    path = tmp_path / "notification.json"
    path.write_text(
        json.dumps(
            {
                "date": issue_date,
                "status": "skipped_not_normal",
                "ok": True,
            }
        ),
        encoding="utf-8",
    )
    skipped = daily_self_heal._load_notification_state(path, issue_date)
    valid = send_push._notification_state(
        status="no_subscribers",
        ok=True,
        source="file",
        subscription_count=0,
        sent_count=0,
    )
    send_push._write_notification_state(str(path), valid)
    resolved = daily_self_heal._load_notification_state(path, issue_date)

    assert skipped["reason"] == "notification_delivery_unverified"
    assert resolved["reason"] == ""
    assert valid["audienceResolutionReceiptSha256"]


def test_partial_notification_delivery_cannot_claim_green(tmp_path: Path) -> None:
    from tools import daily_self_heal, send_push

    issue_date = send_push._today_jst_str()
    state = send_push._notification_state(
        status="sent",
        ok=True,
        source="file",
        subscription_count=2,
        sent_count=1,
        payload_sha256="a" * 64,
        audience_set_sha256="b" * 64,
    )
    path = tmp_path / "notification.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    result = daily_self_heal._load_notification_state(path, issue_date)

    assert result["reason"] == "notification_semantics_invalid"


def test_production_completion_rejects_fixture_notification_source(
    tmp_path: Path,
) -> None:
    from tools import daily_self_heal, send_push

    issue_date = send_push._today_jst_str()
    state = send_push._notification_state(
        status="no_subscribers",
        ok=True,
        source="fixture",
        subscription_count=0,
        sent_count=0,
    )
    path = tmp_path / "notification.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    result = daily_self_heal._load_notification_state(path, issue_date)

    assert result["reason"] == "notification_semantics_invalid"


def _rich_summary(issue_date: str) -> dict[str, object]:
    return {
        "issueDate": issue_date,
        "title": "供給責任と実装条件を横断して読む",
        "sections": [
            (
                f"第{section_number}論点では、確認済みの事実と前提条件を区別する。"
                "現場への影響は費用、供給能力、責任分界の順に確認し、"
                "一方で残るリスクと未確定事項を明示する。"
                "次の観測点は契約条件、価格、制度適用、実装時期であり、続報を追う。"
            )
            * 6
            for section_number in range(1, 8)
        ],
    }


def _write_summary_source(root: Path, summary: dict[str, object]) -> Path:
    issue_date = str(summary["issueDate"])
    source = root / "digest" / "Summary" / f"{issue_date}.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "---\n"
        f"title: '{summary['title']}'\n"
        f"theme: '{summary['title']}'\n"
        f"date: {issue_date}\n"
        "---\n\n"
        + "\n\n".join(str(item) for item in summary["sections"]),
        encoding="utf-8",
    )
    return source


def _materialization_repo(
    tmp_path: Path, *, summary: dict[str, object] | None = None
) -> Path:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    shutil.copy2(
        REPO / "config" / "operational_recovery_registry_v1.json",
        root / "config" / "operational_recovery_registry_v1.json",
    )
    _write_summary_source(root, summary or _rich_summary("2026-08-14"))
    return root


def test_missing_summary_audio_is_materialized_once_and_quality_gated(
    tmp_path: Path,
) -> None:
    from tools import operational_recovery_registry as registry
    from tools.publish_inventory import scheduled_category_ids
    from tools.tts.build_script import validate_script

    materialization_root = _materialization_repo(tmp_path)
    context = {
        "reasonCode": "SUMMARY_AUDIO_SCRIPT_MISSING",
        "artifactRoot": str(tmp_path / "outside-must-not-be-used"),
        "summary": _rich_summary("2026-08-14"),
    }
    first = registry.dispatch(
        repo_root=materialization_root,
        reason_code="SUMMARY_AUDIO_SCRIPT_MISSING",
        context=context,
        handlers=registry.default_handlers(),
    )
    second = registry.dispatch(
        repo_root=materialization_root,
        reason_code="SUMMARY_AUDIO_SCRIPT_MISSING",
        context=context,
        handlers=registry.default_handlers(),
    )
    artifact = materialization_root / "digest" / "Summary" / "2026-08-14-audio-script.md"

    assert first.result["status"] == "materialized"
    assert second.result["status"] == "reused"
    assert first.result["outputHash"] == second.result["outputHash"]
    assert artifact.is_file()
    assert not (tmp_path / "outside-must-not-be-used").exists()
    body = artifact.read_text(encoding="utf-8").split("---", 2)[-1]
    assert validate_script(
        body,
        date="2026-08-14",
        history_texts=[],
        required_categories=scheduled_category_ids("2026-08-14"),
    ) == []


def test_short_summary_audio_source_fails_closed_without_artifact(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_deterministic_builders as builders

    short_summary = {
        "issueDate": "2026-08-14",
        "title": "短い入力",
        "sections": ["確認済みの事実だけを述べる。"],
    }
    materialization_root = _materialization_repo(tmp_path, summary=short_summary)
    with pytest.raises(
        builders.NewsGraspBuilderError,
        match="NG_SUMMARY_AUDIO_SCRIPT_QUALITY_INVALID",
    ):
        builders.materialize_summary_audio_script(
            repo_root=materialization_root,
            issue_date="2026-08-14",
        )

    assert not (
        materialization_root / "digest" / "Summary" / "2026-08-14-audio-script.md"
    ).exists()


def test_summary_audio_rejects_reparse_output_component(tmp_path: Path) -> None:
    from tools import news_grasp_deterministic_builders as builders

    root = _materialization_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(root / "digest" / "Summary")
    summary_link = root / "digest" / "Summary"
    try:
        summary_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with pytest.raises(
        builders.NewsGraspBuilderError, match="NG_BUILDER_OUTPUT_PATH_INVALID"
    ):
        builders.materialize_summary_audio_script(
            repo_root=root,
            issue_date="2026-08-14",
        )

    assert not (outside / "2026-08-14-audio-script.md").exists()


def test_summary_audio_stale_source_hash_is_not_reused(tmp_path: Path) -> None:
    from tools import news_grasp_deterministic_builders as builders

    root = _materialization_repo(tmp_path)
    original = builders.materialize_summary_audio_script(
        repo_root=root, issue_date="2026-08-14"
    )
    changed = _rich_summary("2026-08-14")
    changed["title"] = "更新後の供給責任と実装条件"
    _write_summary_source(root, changed)
    replaced = builders.materialize_summary_audio_script(
        repo_root=root, issue_date="2026-08-14"
    )

    assert original["outputHash"] != replaced["outputHash"]
    assert replaced["status"] == "materialized"
    assert replaced["sourceHash"] != original["sourceHash"]


def test_summary_audio_reuse_rejects_parent_reparse_swap(
    tmp_path: Path, monkeypatch
) -> None:
    from tools import news_grasp_deterministic_builders as builders

    root = _materialization_repo(tmp_path)
    builders.materialize_summary_audio_script(
        repo_root=root, issue_date="2026-08-14"
    )
    summary_dir = root / "digest" / "Summary"
    outside = tmp_path / "outside-summary"
    shutil.copytree(summary_dir, outside)
    held = root / "digest" / "Summary-held"
    original_loader = builders._load_canonical_summary

    def load_then_swap(*args, **kwargs):
        result = original_loader(*args, **kwargs)
        summary_dir.rename(held)
        try:
            summary_dir.symlink_to(outside, target_is_directory=True)
        except OSError:
            held.rename(summary_dir)
            pytest.skip("directory symlink creation is unavailable")
        return result

    monkeypatch.setattr(builders, "_load_canonical_summary", load_then_swap)
    with pytest.raises(
        builders.NewsGraspBuilderError, match="NG_BUILDER_OUTPUT_PATH_INVALID"
    ):
        builders.materialize_summary_audio_script(
            repo_root=root, issue_date="2026-08-14"
        )


def test_summary_audio_directory_identity_failure_is_typed(
    tmp_path: Path, monkeypatch
) -> None:
    from tools import news_grasp_deterministic_builders as builders

    root = _materialization_repo(tmp_path)
    target = root / "digest" / "Summary" / "2026-08-14-audio-script.md"

    def identity_failure(_path: Path):
        raise OSError("injected directory identity failure")

    monkeypatch.setattr(builders, "_directory_identity", identity_failure)
    with pytest.raises(
        builders.NewsGraspBuilderError, match="NG_BUILDER_OUTPUT_PATH_INVALID"
    ):
        with builders._pinned_output_directories(target, root=root):
            pass


def test_summary_audio_ignores_caller_content_and_can_pin_source_sha(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_deterministic_builders as builders

    root = _materialization_repo(tmp_path)
    source = root / "digest" / "Summary" / "2026-08-14.md"
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    result = builders.materialize_summary_audio_script(
        repo_root=root,
        issue_date="2026-08-14",
        expected_source_sha256=source_sha,
    )
    with pytest.raises(
        builders.NewsGraspBuilderError, match="NG_SUMMARY_AUDIO_SOURCE_MISMATCH"
    ):
        builders.materialize_summary_audio_script(
            repo_root=root,
            issue_date="2026-08-14",
            expected_source_sha256="0" * 64,
        )

    artifact = root / str(result["artifactPath"])
    assert "任意caller本文" not in artifact.read_text(encoding="utf-8")
