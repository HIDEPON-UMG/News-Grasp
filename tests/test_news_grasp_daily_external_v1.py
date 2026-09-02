from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import news_grasp_daily_external as external
from tools import news_grasp_direct_runtime as runtime


ISSUE_DATE = "2026-09-03"
MANIFEST_ID = "a" * 64
BUNDLE_ID = "daily-bundle-20260903"
RELEASE_COMMIT_SHA = "b" * 40
FILE_HASH = "c" * 64
MANIFEST_RESERVATION_ID = "d" * 64
SOURCE_BASELINE = "e" * 40
REMOTE_BASE_SHA = "f" * 40


@dataclass(frozen=True)
class _BoundRun:
    store: runtime.DirectRunStore
    cwd: Path
    run_id: str
    writer_lease: str
    fencing_token: int
    manifest_id: str
    bundle_id: str
    context: dict[str, Any]


class _AdapterSpy:
    def __init__(
        self,
        operation_id: str,
        *,
        failure_mode: str | None = None,
    ) -> None:
        self.operation_id = operation_id
        self.failure_mode = failure_mode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if self.failure_mode == "exception":
            raise RuntimeError("provider response became ambiguous")
        if self.failure_mode == "ambiguous":
            return {"ok": True}
        operation_id = str(kwargs["operation_id"])
        return {
            "schemaVersion": external.EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": True,
            "status": "sent",
            "operationId": operation_id,
            "sideEffectId": str(kwargs["side_effect_id"]),
            "idempotencyKey": str(kwargs["idempotency_key"]),
            "outputHash": hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
            "providerAckStatus": "sent",
        }


def _adapters(
    *,
    failure_operation: str | None = None,
    failure_mode: str | None = None,
) -> tuple[dict[str, Callable[..., dict[str, Any]]], dict[str, _AdapterSpy]]:
    spies = {
        operation_id: _AdapterSpy(
            operation_id,
            failure_mode=failure_mode if operation_id == failure_operation else None,
        )
        for operation_id in external.EXTERNAL_OPERATION_ORDER
    }
    return spies, spies


def _bind_run(
    tmp_path: Path,
    *,
    production: bool = False,
    external_operation_ids: list[str] | None = None,
) -> _BoundRun:
    cwd = tmp_path / "repo"
    cwd.mkdir(parents=True)
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=not production,
    )
    start_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "issue_date": ISSUE_DATE,
        "run_intent": runtime.RUN_INTENT,
        "allowed_side_effect_ids": list(external.EXTERNAL_OPERATION_ORDER),
    }
    if production:
        start_kwargs.update(
            {
                "manifest_reservation_id": MANIFEST_RESERVATION_ID,
                "scheduler_trigger_at": f"{ISSUE_DATE}T00:00:00+09:00",
                "source_baseline": SOURCE_BASELINE,
                "runtime_generation": "fixture-runtime-generation",
                "remote_base_sha": REMOTE_BASE_SHA,
            }
        )
    else:
        start_kwargs["manifest_id"] = MANIFEST_ID
    start = runtime.start_run(store, **start_kwargs)
    operation_ids = external_operation_ids or list(external.EXTERNAL_OPERATION_ORDER)
    runtime.seal_publish(
        store,
        run_id=str(start["run_id"]),
        writer_lease=str(start["writer_lease"]),
        fencing_token=int(start["fencing_token"]),
        release_commit_sha=RELEASE_COMMIT_SHA,
        exact_write_set=["docs/index.html"],
        file_hashes={"docs/index.html": FILE_HASH},
        manifest_id=MANIFEST_ID,
        bundle_id=BUNDLE_ID,
        external_operation_ids=operation_ids,
    )
    context = {
        "run_id": str(start["run_id"]),
        "manifest_id": MANIFEST_ID,
        "bundle_id": BUNDLE_ID,
        "fencing_token": int(start["fencing_token"]),
        "issue_date": ISSUE_DATE,
    }
    return _BoundRun(
        store=store,
        cwd=cwd,
        run_id=str(start["run_id"]),
        writer_lease=str(start["writer_lease"]),
        fencing_token=int(start["fencing_token"]),
        manifest_id=MANIFEST_ID,
        bundle_id=BUNDLE_ID,
        context=context,
    )


def _db_snapshot(store: runtime.DirectRunStore) -> dict[str, list[tuple[Any, ...]]]:
    with store.connect() as conn:
        table_names = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        snapshot: dict[str, list[tuple[Any, ...]]] = {}
        for table_name in table_names:
            quoted = '"' + table_name.replace('"', '""') + '"'
            rows = conn.execute(f"SELECT * FROM {quoted} ORDER BY rowid").fetchall()
            snapshot[table_name] = [tuple(row) for row in rows]
        return snapshot


def _execute(
    bound: _BoundRun,
    *,
    adapters: dict[str, Callable[..., dict[str, Any]]] | None,
    context: dict[str, Any] | None = None,
    run_id: str | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    return external.execute_external_publication(
        store=bound.store,
        run_id=run_id or bound.run_id,
        writer_lease=bound.writer_lease,
        fencing_token=bound.fencing_token if fencing_token is None else fencing_token,
        adapters=adapters,
        context=bound.context if context is None else context,
    )


def test_production_adapter_unregistered_red_precedes_reserve_and_preserves_db(tmp_path: Path) -> None:
    """未登録のproduction adapterはreserve/start前にtyped Redとなる。"""
    bound = _bind_run(tmp_path, production=True)
    before = _db_snapshot(bound.store)

    result = _execute(bound, adapters=None)

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["external_adapter_unavailable:audio_daily_upload"]
    assert _db_snapshot(bound.store) == before
    with bound.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_outbox").fetchone()[0] == 0
        assert conn.execute("SELECT external_started_at FROM runs WHERE run_id=?", (bound.run_id,)).fetchone()[0] == ""


def test_test_only_adapter_is_called_at_most_once_per_operation(tmp_path: Path) -> None:
    bound = _bind_run(tmp_path)
    adapters, spies = _adapters()

    result = _execute(bound, adapters=adapters)

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["adapter_call_count"] == len(external.EXTERNAL_OPERATION_ORDER)
    assert result["duplicate_call_count"] == 0
    assert Counter({operation_id: len(spy.calls) for operation_id, spy in spies.items()}) == Counter(
        {operation_id: 1 for operation_id in external.EXTERNAL_OPERATION_ORDER}
    )
    with bound.store.connect() as conn:
        statuses = conn.execute(
            "SELECT logical_operation_id,status FROM external_outbox "
            "WHERE run_id=? ORDER BY logical_operation_id",
            (bound.run_id,),
        ).fetchall()
    assert {str(row[0]): str(row[1]) for row in statuses} == {
        operation_id: "completed" for operation_id in external.EXTERNAL_OPERATION_ORDER
    }


def test_fixed_logical_operation_ids_are_scoped_per_run_and_next_day_can_publish(
    tmp_path: Path,
) -> None:
    first = _bind_run(tmp_path)
    first_adapters, first_spies = _adapters()
    assert _execute(first, adapters=first_adapters)["ok"] is True

    second_issue = "2026-09-04"
    second_manifest = "9" * 64
    second_bundle = "daily-bundle-20260904"
    started = runtime.start_run(
        first.store,
        cwd=first.cwd,
        issue_date=second_issue,
        run_intent=runtime.RUN_INTENT,
        manifest_id=second_manifest,
        allowed_side_effect_ids=list(external.EXTERNAL_OPERATION_ORDER),
    )
    runtime.seal_publish(
        first.store,
        run_id=str(started["run_id"]),
        writer_lease=str(started["writer_lease"]),
        fencing_token=int(started["fencing_token"]),
        release_commit_sha=RELEASE_COMMIT_SHA,
        exact_write_set=["docs/index.html"],
        file_hashes={"docs/index.html": FILE_HASH},
        manifest_id=second_manifest,
        bundle_id=second_bundle,
        external_operation_ids=list(external.EXTERNAL_OPERATION_ORDER),
    )
    second = _BoundRun(
        store=first.store,
        cwd=first.cwd,
        run_id=str(started["run_id"]),
        writer_lease=str(started["writer_lease"]),
        fencing_token=int(started["fencing_token"]),
        manifest_id=second_manifest,
        bundle_id=second_bundle,
        context={
            "run_id": str(started["run_id"]),
            "manifest_id": second_manifest,
            "bundle_id": second_bundle,
            "fencing_token": int(started["fencing_token"]),
            "issue_date": second_issue,
        },
    )
    second_adapters, second_spies = _adapters()
    second_result = _execute(second, adapters=second_adapters)

    assert second_result["ok"] is True
    assert all(len(spy.calls) == 1 for spy in first_spies.values())
    assert all(len(spy.calls) == 1 for spy in second_spies.values())
    with first.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_outbox").fetchone()[0] == 18
        counts = conn.execute(
            "SELECT COUNT(*) FROM external_outbox "
            "GROUP BY run_id HAVING COUNT(*)=?",
            (len(external.EXTERNAL_OPERATION_ORDER),),
        ).fetchall()
        assert [int(row[0]) for row in counts] == [
            len(external.EXTERNAL_OPERATION_ORDER),
            len(external.EXTERNAL_OPERATION_ORDER),
        ]


@pytest.mark.parametrize("failure_mode", ["exception", "ambiguous"])
def test_adapter_exception_or_ambiguous_result_is_unknown_and_rerun_does_not_resend(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    bound = _bind_run(tmp_path)
    adapters, spies = _adapters(
        failure_operation="audio_daily_upload",
        failure_mode=failure_mode,
    )

    first = _execute(bound, adapters=adapters)
    with bound.store.connect() as conn:
        first_row = conn.execute(
            "SELECT status,provider_ack_status,idempotency_key FROM external_outbox "
            "WHERE run_id=? AND logical_operation_id=?",
            (bound.run_id, "audio_daily_upload"),
        ).fetchone()

    second = _execute(bound, adapters=adapters)
    with bound.store.connect() as conn:
        second_row = conn.execute(
            "SELECT status,provider_ack_status,idempotency_key FROM external_outbox "
            "WHERE run_id=? AND logical_operation_id=?",
            (bound.run_id, "audio_daily_upload"),
        ).fetchone()

    assert first["ok"] is False
    assert first["status"] == "reconcile_required"
    assert first["exact_successor"] == "external_reconcile:audio_daily_upload"
    assert first["adapter_call_count"] == 1
    assert tuple(first_row) == (
        "unknown_delivery",
        "unknown_unobtainable",
        first_row[2],
    )
    assert second["ok"] is False
    assert second["status"] == "reconcile_required"
    assert second["exact_successor"] == "external_reconcile:audio_daily_upload"
    assert second["adapter_call_count"] == 0
    assert second["duplicate_call_count"] == 0
    assert tuple(second_row) == tuple(first_row)
    assert len(spies["audio_daily_upload"].calls) == 1


def test_completed_operation_attach_and_rerun_has_zero_adapter_calls(tmp_path: Path) -> None:
    bound = _bind_run(tmp_path)
    adapters, spies = _adapters()
    first = _execute(bound, adapters=adapters)

    attached = runtime.start_run(
        bound.store,
        cwd=bound.cwd,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id=bound.manifest_id,
        allowed_side_effect_ids=list(external.EXTERNAL_OPERATION_ORDER),
    )
    second = _execute(bound, adapters=adapters)

    assert first["ok"] is True
    assert attached["status"] == "attached"
    assert attached["attached_to_run_id"] == bound.run_id
    assert "writer_lease" not in attached
    assert "fencing_token" not in attached
    assert second["ok"] is True
    assert second["status"] == "completed"
    assert second["adapter_call_count"] == 0
    assert second["duplicate_call_count"] == 0
    assert all(len(spy.calls) == 1 for spy in spies.values())
    assert all(item["idempotent"] is True for item in second["operations"])


def test_dependency_order_violation_in_publish_seal_is_rejected_before_outbox(tmp_path: Path) -> None:
    bound_order = list(external.EXTERNAL_OPERATION_ORDER)
    bound_order[0], bound_order[1] = bound_order[1], bound_order[0]
    bound = _bind_run(tmp_path, external_operation_ids=bound_order)
    before = _db_snapshot(bound.store)

    result = _execute(bound, adapters={})

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["publish_seal_external_operation_ids_mismatch"]
    assert _db_snapshot(bound.store) == before
    with bound.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_outbox").fetchone()[0] == 0


def test_production_adapter_injection_is_rejected_before_state_mutation(tmp_path: Path) -> None:
    bound = _bind_run(tmp_path, production=True)
    adapters, _ = _adapters()
    before = _db_snapshot(bound.store)

    result = _execute(bound, adapters=adapters)

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["production_adapter_injection_forbidden"]
    assert _db_snapshot(bound.store) == before


@pytest.mark.parametrize("mismatch", ["run_id", "manifest_id", "bundle_id", "fencing_token"])
def test_actual_run_manifest_bundle_and_fencing_mismatch_are_rejected(
    tmp_path: Path,
    mismatch: str,
) -> None:
    bound = _bind_run(tmp_path)
    context = dict(bound.context)
    run_id = bound.run_id
    fencing_token = bound.fencing_token
    expected_failure: str
    if mismatch == "run_id":
        run_id = "actual-run-id-drift"
        expected_failure = "external_identity_inspection_failed:ValueError"
    elif mismatch == "manifest_id":
        context["manifest_id"] = "0" * 64
        expected_failure = "external_context_manifest_id_mismatch"
    elif mismatch == "bundle_id":
        context["bundle_id"] = "different-bundle"
        expected_failure = "external_context_bundle_id_mismatch"
    else:
        fencing_token = bound.fencing_token + 1
        expected_failure = "fencing_token_fenced"
    before = _db_snapshot(bound.store)

    result = _execute(
        bound,
        adapters={},
        context=context,
        run_id=run_id,
        fencing_token=fencing_token,
    )

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == [expected_failure]
    assert _db_snapshot(bound.store) == before


def test_notification_and_youtube_upload_finalize_duplicates_are_zero(tmp_path: Path) -> None:
    bound = _bind_run(tmp_path)
    adapters, spies = _adapters()
    first = _execute(bound, adapters=adapters)
    before_counts = {
        operation_id: len(spies[operation_id].calls)
        for operation_id in (
            "audio_daily_upload",
            "audio_deepdive_upload",
            "youtube_daily_prepare",
            "youtube_deepdive_prepare",
            "youtube_daily_finalize",
            "youtube_deepdive_finalize",
            "notification_send",
        )
    }

    second = _execute(bound, adapters=adapters)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["adapter_call_count"] == 0
    assert second["duplicate_call_count"] == 0
    assert {
        operation_id: len(spies[operation_id].calls)
        for operation_id in before_counts
    } == before_counts
    assert {
        item["operation_id"]: item["adapter_called"]
        for item in second["operations"]
        if item["operation_id"] in before_counts
    } == {operation_id: False for operation_id in before_counts}
