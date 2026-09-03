from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import news_grasp_daily_external as external
from tools import news_grasp_direct_runtime as runtime
from tools import news_grasp_publish_contract as publish_contract


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


def _materialize_production_manifest_fixture(
    root: Path,
    *,
    run_id: str,
) -> dict[str, Any]:
    """production preflightが読むcanonical manifestと全必須bytesを作る。

    production adapter/CASの回帰は、manifest不在という別のRedで遮られては
    ならない。fixtureは実consumerの ``build_publish_manifest`` と
    ``verify_manifest(require_files=True)`` を通し、externalOperationContractを
    実際の入力bytes・run identityへ束縛する。
    """

    normal_files = {
        "docs/index.html": "<html><body>fixture home</body></html>",
        "docs/sw.js": 'self.addEventListener("fetch",()=>{});',
        f"docs/{ISSUE_DATE}/index.html": "<html><body>fixture issue</body></html>",
        f"digest/Summary/{ISSUE_DATE}.md": "---\ntitle: Fixture\n---\n# Summary\n",
        f"docs/{ISSUE_DATE}/summary/index.html": "<html><body>fixture summary</body></html>",
        f"digest/DeepDive/{ISSUE_DATE}-DeepDive.md": "---\ntitle: Fixture DeepDive\n---\n# DeepDive\n",
        f"docs/deepdive/{ISSUE_DATE}/index.html": "<html><body>fixture deepdive</body></html>",
        "docs/publish-status.json": "{}",
    }
    for category_id in publish_contract.scheduled_category_ids(ISSUE_DATE):
        normal_files[publish_contract.digest_artifact_for_category(category_id, ISSUE_DATE)] = (
            f"# {category_id}\n"
        )
        normal_files[publish_contract.docs_artifact_for_category(category_id, ISSUE_DATE)] = (
            "<html><body>fixture category</body></html>"
        )
    input_files = {
        f"build/tts/{ISSUE_DATE}.mp3": b"fixture daily audio",
        f"build/tts/deepdive/{ISSUE_DATE}.mp3": b"fixture deepdive audio",
        f"build/youtube-podcast/{ISSUE_DATE}.mp4": b"fixture daily youtube",
        f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4": b"fixture deepdive youtube",
    }
    for relative, content in input_files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for relative, content in normal_files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    manifest = publish_contract.build_publish_manifest(
        repo_root=root,
        issue_date=ISSUE_DATE,
        run_id=run_id,
        run_intent=runtime.RUN_INTENT,
        source_baseline=SOURCE_BASELINE,
    )
    manifest_target = publish_contract.manifest_path(root, ISSUE_DATE)
    manifest_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    verification = publish_contract.verify_manifest(
        manifest,
        repo_root=root,
        require_files=True,
    )
    assert verification["ok"] is True, verification
    assert isinstance(manifest.get("externalOperationContract"), dict)
    assert manifest["externalOperationContract"]["operationIds"] == list(
        external.EXTERNAL_OPERATION_ORDER
    )
    return manifest


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
    manifest = (
        _materialize_production_manifest_fixture(cwd, run_id=str(start["run_id"]))
        if production
        else None
    )
    sealed_manifest_id = (
        str(manifest["manifestId"])
        if manifest is not None
        else MANIFEST_ID
    )
    external_input_hashes = (
        dict(manifest["externalOperationContract"]["externalInputHashes"])
        if manifest is not None
        else {
            f"build/tts/{ISSUE_DATE}.mp3": "d" * 64,
            f"build/tts/deepdive/{ISSUE_DATE}.mp3": "e" * 64,
            f"build/youtube-podcast/{ISSUE_DATE}.mp4": "f" * 64,
            f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4": "1" * 64,
        }
    )
    operation_ids = external_operation_ids or list(external.EXTERNAL_OPERATION_ORDER)
    runtime.seal_publish(
        store,
        run_id=str(start["run_id"]),
        writer_lease=str(start["writer_lease"]),
        fencing_token=int(start["fencing_token"]),
        release_commit_sha=RELEASE_COMMIT_SHA,
        exact_write_set=["docs/index.html"],
        file_hashes={"docs/index.html": FILE_HASH},
        manifest_id=sealed_manifest_id,
        bundle_id=BUNDLE_ID,
        external_operation_ids=operation_ids,
        external_input_hashes=external_input_hashes,
    )
    context = {
        "run_id": str(start["run_id"]),
        "manifest_id": sealed_manifest_id,
        "bundle_id": BUNDLE_ID,
        "fencing_token": int(start["fencing_token"]),
        "issue_date": ISSUE_DATE,
        "run_intent": runtime.RUN_INTENT,
    }
    if production:
        context.update(
            {
                "repo_root": str(cwd),
                "external_input_hashes": external_input_hashes,
                "external_operation_contract": manifest["externalOperationContract"],
            }
        )
    return _BoundRun(
        store=store,
        cwd=cwd,
        run_id=str(start["run_id"]),
        writer_lease=str(start["writer_lease"]),
        fencing_token=int(start["fencing_token"]),
        manifest_id=sealed_manifest_id,
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


def test_production_adapter_unregistered_red_precedes_reserve_and_preserves_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """未登録のproduction adapterはreserve/start前にtyped Redとなる。"""
    bound = _bind_run(tmp_path, production=True)
    before = _db_snapshot(bound.store)
    monkeypatch.setattr(external, "PRODUCTION_ADAPTERS", {})

    result = _execute(bound, adapters=None)

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == [
        f"external_adapter_unavailable:{external.EXTERNAL_OPERATION_ORDER[0]}"
    ]
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
    assert result.get("duplicate_call_count", 0) == 0
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


def test_completed_outbox_persists_provider_evidence_and_release_identity(
    tmp_path: Path,
) -> None:
    """completed rowはprovider evidenceとseal identityを同じreceiptへ永続化する。"""

    bound = _bind_run(tmp_path)
    payload_by_operation = {
        operation_id: {
            "provider": "fixture-provider",
            "operation": operation_id,
            "payloadIdentity": hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
        }
        for operation_id in external.EXTERNAL_OPERATION_ORDER
    }

    def adapter(**kwargs: Any) -> dict[str, Any]:
        operation_id = str(kwargs["operation_id"])
        output_hash = hashlib.sha256(
            external._canonical_json(payload_by_operation[operation_id]).encode("utf-8")
        ).hexdigest()
        return {
            "schemaVersion": external.EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": True,
            "status": "completed",
            "operationId": operation_id,
            "sideEffectId": str(kwargs["side_effect_id"]),
            "idempotencyKey": str(kwargs["idempotency_key"]),
            "outputHash": output_hash,
            "providerAckStatus": "unknown_unobtainable",
            "payload": payload_by_operation[operation_id],
        }

    adapters = {
        operation_id: adapter
        for operation_id in external.EXTERNAL_OPERATION_ORDER
    }
    result = _execute(bound, adapters=adapters)

    assert result["ok"] is True
    assert result["status"] == "completed"
    with bound.store.connect() as conn:
        rows = conn.execute(
            "SELECT logical_operation_id,status,provider_receipt_json,provider_receipt_hash "
            "FROM external_outbox WHERE run_id=? ORDER BY logical_operation_id",
            (bound.run_id,),
        ).fetchall()

    assert len(rows) == len(external.EXTERNAL_OPERATION_ORDER)
    for operation_id, status, receipt_json, receipt_hash in rows:
        assert status == "completed"
        receipt = json.loads(str(receipt_json))
        assert receipt["operation_id"] == operation_id
        assert receipt["manifest_id"] == MANIFEST_ID
        assert receipt["bundle_id"] == BUNDLE_ID
        assert receipt["release_commit_sha"] == RELEASE_COMMIT_SHA
        assert receipt["provider_evidence"] == payload_by_operation[operation_id]
        assert receipt["provider_evidence"]["payloadIdentity"]
        assert hashlib.sha256(str(receipt_json).encode("utf-8")).hexdigest() == receipt_hash


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
        external_input_hashes={
            f"build/tts/{second_issue}.mp3": "d" * 64,
            f"build/tts/deepdive/{second_issue}.mp3": "e" * 64,
            f"build/youtube-podcast/{second_issue}.mp4": "f" * 64,
            f"build/youtube-podcast-deepdive/{second_issue}.mp4": "1" * 64,
        },
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
            "run_intent": runtime.RUN_INTENT,
        },
    )
    second_adapters, second_spies = _adapters()
    second_result = _execute(second, adapters=second_adapters)

    assert second_result["ok"] is True
    assert all(len(spy.calls) == 1 for spy in first_spies.values())
    assert all(len(spy.calls) == 1 for spy in second_spies.values())
    with first.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM external_outbox").fetchone()[0] == (
            len(external.EXTERNAL_OPERATION_ORDER) * 2
        )
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
    assert first["adapter_call_count"] == (
        external.EXTERNAL_OPERATION_ORDER.index("audio_daily_upload") + 1
    )
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


def test_reconcile_required_preserves_operation_and_reconciles_once_without_adapter_resend(
    tmp_path: Path,
) -> None:
    """reconcile_requiredは同じoperationへ一度だけ進み、adapterを再送しない。"""

    bound = _bind_run(tmp_path)
    target_operation = "audio_daily_upload"
    adapters, spies = _adapters()
    expected_keys = {
        operation_id: (
            f"{bound.run_id}:{operation_id}:{operation_id}:{MANIFEST_ID}:{BUNDLE_ID}"
        )
        for operation_id in external.EXTERNAL_OPERATION_ORDER
    }
    identity, _state, identity_failure = external._sealed_identity(
        bound.store,
        run_id=bound.run_id,
        writer_lease=bound.writer_lease,
        fencing_token=bound.fencing_token,
        context=bound.context,
    )
    assert identity_failure is None
    assert identity is not None

    def raw_receipt(operation_id: str) -> dict[str, Any]:
        return {
            "schemaVersion": external.EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": True,
            "status": "completed",
            "operationId": operation_id,
            "sideEffectId": operation_id,
            "idempotencyKey": expected_keys[operation_id],
            "outputHash": hashlib.sha256(
                f"reconcile-fixture:{operation_id}".encode("utf-8")
            ).hexdigest(),
            "providerAckStatus": "sent",
        }

    # 全operationを先にreservedへ登録し、targetだけprovider call直後のstarted
    # 状態で停止したsnapshotを作る。他operationは既にcompletedなので、
    # reconcile後に未完了adapterが割り込むことはない。
    for operation_id in external.EXTERNAL_OPERATION_ORDER:
        runtime.record_external_outbox(
            bound.store,
            run_id=bound.run_id,
            writer_lease=bound.writer_lease,
            operation_id=operation_id,
            side_effect_id=operation_id,
            status="reserved",
            payload=bound.context,
            idempotency_key=expected_keys[operation_id],
            fencing_token=bound.fencing_token,
        )
        runtime.transition_external_outbox(
            bound.store,
            run_id=bound.run_id,
            writer_lease=bound.writer_lease,
            operation_id=operation_id,
            expected_status="reserved",
            next_status="started",
            fencing_token=bound.fencing_token,
        )
        if operation_id != target_operation:
            validated = external.validate_external_adapter_receipt(
                raw_receipt(operation_id),
                operation_id=operation_id,
                side_effect_id=operation_id,
                idempotency_key=expected_keys[operation_id],
                identity=identity,
                require_provider_evidence=False,
            )
            assert validated["ok"] is True
            runtime.complete_external_outbox_atomic(
                bound.store,
                run_id=bound.run_id,
                writer_lease=bound.writer_lease,
                operation_id=operation_id,
                provider_receipt=validated,
                provider_ack_status="sent",
                fencing_token=bound.fencing_token,
            )

    first = _execute(bound, adapters=adapters)
    assert first["ok"] is False
    assert first["status"] == "reconcile_required"
    assert len(first["failures"]) == 1
    assert target_operation in first["failures"][0]
    assert first["failures"][0].startswith("external_")
    assert first["exact_successor"] == f"external_reconcile:{target_operation}"
    assert first["adapter_call_count"] == 0

    reconcile_calls: list[dict[str, Any]] = []

    def reconciler(**kwargs: Any) -> dict[str, Any]:
        reconcile_calls.append(dict(kwargs))
        operation_id = str(kwargs["operation_id"])
        return raw_receipt(operation_id)

    # Reconciliation is an explicit successor; the first call above must not
    # silently flatten into a new adapter attempt. The very next invocation
    # supplies the reconciler for that exact operation only once.
    second = external.execute_external_publication(
        store=bound.store,
        run_id=bound.run_id,
        writer_lease=bound.writer_lease,
        fencing_token=bound.fencing_token,
        adapters=adapters,
        reconcilers={target_operation: reconciler},
        context=bound.context,
    )

    assert second["ok"] is True
    assert second["status"] == "completed"
    assert len(reconcile_calls) == 1
    assert reconcile_calls[0]["operation_id"] == target_operation
    assert second["adapter_call_count"] == 0

    third = external.execute_external_publication(
        store=bound.store,
        run_id=bound.run_id,
        writer_lease=bound.writer_lease,
        fencing_token=bound.fencing_token,
        adapters=adapters,
        reconcilers={target_operation: reconciler},
        context=bound.context,
    )
    assert third["ok"] is True
    assert third["status"] == "completed"
    assert third["adapter_call_count"] == 0
    assert len(reconcile_calls) == 1
    assert all(not spy.calls for spy in spies.values())

    with bound.store.connect() as conn:
        target_row = conn.execute(
            "SELECT status FROM external_outbox "
            "WHERE run_id=? AND logical_operation_id=?",
            (bound.run_id, target_operation),
        ).fetchone()
    assert tuple(target_row or ()) == ("completed",)


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


@pytest.mark.parametrize(
    "drift_after",
    (
        "git_release_push",
        "pages_deployment_wait",
    ),
)
def test_remote_cas_race_after_completed_side_effect_stops_before_following_adapters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift_after: str,
) -> None:
    """sealed release後のorigin/main driftを次の副作用直前に検出し、後続送信を止める。"""

    bound = _bind_run(tmp_path, production=True)
    expected_prefix = tuple(
        external.EXTERNAL_OPERATION_ORDER[
            : external.EXTERNAL_OPERATION_ORDER.index(drift_after) + 1
        ]
    )
    remote = {"sha": REMOTE_BASE_SHA, "history": [REMOTE_BASE_SHA]}
    cas_observations: list[dict[str, Any]] = []

    def fresh_remote_cas(
        identity: dict[str, Any],
        context: dict[str, Any],
        rows: dict[str, dict[str, Any]],
    ) -> str | None:
        del context
        git_status = str((rows.get("git_release_push") or {}).get("status") or "")
        expected = (
            str(identity["release_commit_sha"])
            if git_status == "completed"
            else REMOTE_BASE_SHA
        )
        cas_observations.append(
            {
                "origin_main_sha": remote["sha"],
                "expected_sha": expected,
                "completed_operations": tuple(
                    operation_id
                    for operation_id, row in rows.items()
                    if row.get("status") == "completed"
                ),
            }
        )
        return None if remote["sha"] == expected else "external_remote_base_cas_mismatch"

    def receipt_for(operation_id: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any]
        input_hashes = bound.context["external_input_hashes"]
        if operation_id == "git_release_push":
            evidence = {
                "observed_remote_sha": RELEASE_COMMIT_SHA,
                "release_commit_sha": RELEASE_COMMIT_SHA,
            }
        elif operation_id in {"audio_daily_upload", "audio_deepdive_upload"}:
            input_path = (
                f"build/tts/{ISSUE_DATE}.mp3"
                if operation_id == "audio_daily_upload"
                else f"build/tts/deepdive/{ISSUE_DATE}.mp3"
            )
            evidence = {
                "input_sha256": input_hashes[input_path],
                "release_commit_sha": RELEASE_COMMIT_SHA,
                "input_path": input_path,
            }
        elif operation_id.startswith("youtube_"):
            kind = "daily" if operation_id.startswith("youtube_daily_") else "deepdive"
            input_path = (
                f"build/youtube-podcast/{ISSUE_DATE}.mp4"
                if kind == "daily"
                else f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4"
            )
            evidence = {
                "input_sha256": input_hashes[input_path],
                "release_commit_sha": RELEASE_COMMIT_SHA,
                "result": {"videoId": f"{kind}-fixture-video"},
            }
        elif operation_id == "pages_deployment_wait":
            evidence = {
                "workflow": {
                    "ok": True,
                    "deploymentBinding": {"deploymentSha": RELEASE_COMMIT_SHA},
                },
            }
        else:
            # このraceではnotification/finalizeまで到達しないが、登録済み
            # adapter集合のpreflightを満たすためtyped receiptを返す。
            evidence = {
                "release_commit_sha": RELEASE_COMMIT_SHA,
                "operation": operation_id,
            }
        contract_rows = bound.context.get("external_operation_contract", {})
        payload_identity = ""
        for contract_row in contract_rows.get("operations", ()):
            if contract_row.get("operationId") == operation_id:
                payload_identity = str(contract_row.get("payloadIdentity") or "")
                break
        if not payload_identity:
            payload_identity = hashlib.sha256(
                f"{operation_id}\0{RELEASE_COMMIT_SHA}".encode("utf-8")
            ).hexdigest()
        output_hash = hashlib.sha256(
            external._canonical_json(evidence).encode("utf-8")
        ).hexdigest()
        return {
            "schemaVersion": external.EXTERNAL_ADAPTER_RECEIPT_SCHEMA,
            "ok": True,
            "status": "completed",
            "operationId": operation_id,
            "sideEffectId": str(kwargs["side_effect_id"]),
            "idempotencyKey": str(kwargs["idempotency_key"]),
            "outputHash": output_hash,
            "providerAckStatus": "sent",
            "payloadIdentity": payload_identity,
            "payload": evidence,
            "runId": bound.run_id,
            "manifestId": bound.manifest_id,
            "bundleId": bound.bundle_id,
            "fencingToken": bound.fencing_token,
        }

    spies: dict[str, _AdapterSpy] = {}

    def adapter(**kwargs: Any) -> dict[str, Any]:
        operation_id = str(kwargs["operation_id"])
        spy = spies.setdefault(operation_id, _AdapterSpy(operation_id))
        spy.calls.append(dict(kwargs))
        if operation_id == "git_release_push":
            # push完了後、同じsealed SHAからorigin/mainが別SHAへ進むrace。
            remote["sha"] = RELEASE_COMMIT_SHA
            remote["history"].append(RELEASE_COMMIT_SHA)
            if drift_after == operation_id:
                remote["sha"] = "9" * 40
                remote["history"].append(remote["sha"])
        elif operation_id == "pages_deployment_wait" and drift_after == operation_id:
            # Pages deployment wait完了後、次のYouTube finalize/notification
            # の直前にorigin/mainがsealed SHAから進んだ状態。
            remote["sha"] = RELEASE_COMMIT_SHA
            remote["history"].append(RELEASE_COMMIT_SHA)
            remote["sha"] = "8" * 40
            remote["history"].append(remote["sha"])
        return receipt_for(operation_id, kwargs)

    for operation_id in external.EXTERNAL_OPERATION_ORDER:
        spies[operation_id] = _AdapterSpy(operation_id)
    monkeypatch.setattr(
        external,
        "PRODUCTION_ADAPTERS",
        {operation_id: adapter for operation_id in external.EXTERNAL_OPERATION_ORDER},
    )
    # CAS raceのpredicateだけを評価するfixtureなので、sealed bytesの観測は
    # 独立fixture済みであり、ここでは公開内容のprovider呼出しを発生させない。
    monkeypatch.setattr(
        external,
        "_verify_sealed_release_files",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(external, "_verify_remote_cas_before_side_effects", fresh_remote_cas)
    monkeypatch.setattr(external, "_record_external_provider_binding", lambda **_kwargs: None)

    result = _execute(bound, adapters=None)

    assert result["ok"] is False
    assert result["status"] == "red"
    assert result["failures"] == ["external_remote_base_cas_mismatch"]
    assert result.get("duplicate_call_count", 0) == 0
    assert tuple(
        operation_id
        for operation_id in external.EXTERNAL_OPERATION_ORDER
        if spies[operation_id].calls
    ) == expected_prefix
    assert sum(len(spy.calls) for spy in spies.values()) == len(expected_prefix)
    # race検出後のYouTube finalize/notificationは、既存のexternal receiptが
    # あっても新しいprovider callを重ねてはならない。
    for operation_id in external.EXTERNAL_OPERATION_ORDER[
        len(expected_prefix):
    ]:
        assert spies[operation_id].calls == []
    assert spies["youtube_daily_finalize"].calls == []
    assert spies["youtube_deepdive_finalize"].calls == []
    assert spies["notification_send"].calls == []
    assert len(cas_observations) >= len(expected_prefix) + 1
    last = cas_observations[-1]
    assert last["origin_main_sha"] != RELEASE_COMMIT_SHA
    assert last["expected_sha"] == RELEASE_COMMIT_SHA
    assert drift_after in last["completed_operations"]


def test_external_timing_open_fault_leaves_reserved_and_retries_each_adapter_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """timing open の永続化失敗で副作用を開始せず、次回だけを再開する。"""

    bound = _bind_run(tmp_path)
    adapters, spies = _adapters()
    original_append = runtime._append_timing_event_in_tx
    fault = {"open": True}

    def fail_open_once(*args: Any, **kwargs: Any) -> int:
        if fault["open"]:
            fault["open"] = False
            raise RuntimeError("fixture_timing_open_insert_fault")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(runtime, "_append_timing_event_in_tx", fail_open_once)

    first = _execute(bound, adapters=adapters)

    assert first["ok"] is False
    assert first["status"] == "red"
    assert first["failures"] == [
        "external_outbox_start_failed:audio_daily_upload:RuntimeError",
    ]
    assert all(not spy.calls for spy in spies.values())
    with bound.store.connect() as conn:
        first_row = conn.execute(
            "SELECT status FROM external_outbox WHERE run_id=? AND logical_operation_id=?",
            (bound.run_id, external.EXTERNAL_OPERATION_ORDER[0]),
        ).fetchone()
    assert tuple(first_row or ()) == ("reserved",)

    second = _execute(bound, adapters=adapters)

    assert second["ok"] is True
    assert second["status"] == "completed"
    assert second["adapter_call_count"] == len(external.EXTERNAL_OPERATION_ORDER)
    assert second.get("duplicate_call_count", 0) == 0
    assert all(len(spy.calls) == 1 for spy in spies.values())
