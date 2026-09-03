"""News-Grasp日次六operationの実行broker。

Dailyのentryは六つのoperationだけに閉じ、各operationでは登録済みの正規handler
を一度だけ呼び出す。handlerが返すproducer receiptを検証してから、runtime SQLite
の順序付きreceiptへ原子的に適用する。Release専用gateや汎用Python子processは
このmoduleからimportもspawnもしない。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.news_grasp_gate_profiles import (
    DAILY_OPERATIONS,
    DAILY_PYTHON,
    NewsGraspGateProfileError,
    authorize_daily_operation,
    build_daily_route_capability,
    daily_operation_command,
    validate_profiles,
)
from tools import news_grasp_direct_runtime as runtime


DAILY_GATE_SCHEMA = "NEWS_GRASP_DAILY_GATE_RECEIPT_V1"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
PROTECTED_RELEASE = "2026-09-02"
PROTECTED_RELEASE_POLICY = "再生成・再アップロード・通知再送・再公開を禁止"
DAILY_ALLOWED_SIDE_EFFECT_IDS = (
    "audio_daily_upload",
    "audio_deepdive_upload",
    "youtube_daily_prepare",
    "youtube_deepdive_prepare",
    "git_release_push",
    "pages_deployment_wait",
    "youtube_daily_finalize",
    "youtube_deepdive_finalize",
    "notification_send",
    "completion_attestation_publish",
)

# Installed ScheduledProductionの正規producerは、この固定registryから解決する。
# ``handlers``引数とregister関数はunit seamとしてだけ上書きを許可する。
DAILY_OPERATION_HANDLERS: dict[str, Callable[..., Any]] = {}
DAILY_OPERATION_HANDLER_IDS: dict[str, str] = {}


def protected_release_failure(
    *,
    repo_root: str | Path,
    issue_date: str,
    require_contract_integrity: bool = False,
) -> str | None:
    """保護済みreleaseの再実行を全公開entryで同じ規則により拒否する。"""

    protected = str(issue_date or "").strip() == PROTECTED_RELEASE
    if not protected and not require_contract_integrity:
        return None
    contract_path = Path(repo_root).resolve() / "config" / "news_grasp_daily_45m_contract_v1.json"
    try:
        raw = contract_path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("bom")
        contract = json.loads(raw.decode("utf-8", errors="strict"))
        task_contract = contract["taskOperatingContract"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return "daily_protected_release_contract_invalid"
    if (
        not isinstance(task_contract, Mapping)
        or task_contract.get("schemaVersion") != "TASK_OPERATING_CONTRACT_V1"
        or task_contract.get("protectedRelease") != PROTECTED_RELEASE
        or task_contract.get("protectedReleasePolicy") != PROTECTED_RELEASE_POLICY
    ):
        return "daily_protected_release_contract_mismatch"
    if protected:
        return "protected_release_reexecution_forbidden"
    return None


def _red_result(operation_id: str, reason: str, **extra: Any) -> dict[str, Any]:
    status = str(extra.pop("status", "red") or "red")
    return {
        "schemaVersion": DAILY_GATE_SCHEMA,
        "ok": False,
        "status": status,
        "operation_id": operation_id,
        "phase": operation_id,
        "failures": [reason],
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
        **extra,
    }


def register_daily_operation_handler(
    operation_id: str,
    handler: Callable[..., Any],
    *,
    handler_id: str | None = None,
) -> dict[str, Any]:
    """正規Daily producer handlerを明示登録する。"""

    if operation_id not in DAILY_OPERATIONS:
        raise NewsGraspGateProfileError("daily_operation_unknown")
    if not callable(handler):
        raise TypeError("daily_operation_handler_invalid")
    resolved_id = str(handler_id or "").strip()
    if not resolved_id:
        resolved_id = str(
            getattr(handler, "handler_id", "")
            or f"{getattr(handler, '__module__', '')}.{getattr(handler, '__qualname__', repr(handler))}"
        ).strip(".")
    if not resolved_id:
        raise ValueError("daily_operation_handler_id_invalid")
    if operation_id in DAILY_OPERATION_HANDLERS:
        raise NewsGraspGateProfileError("daily_operation_handler_already_registered")
    DAILY_OPERATION_HANDLERS[operation_id] = handler
    DAILY_OPERATION_HANDLER_IDS[operation_id] = resolved_id
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_HANDLER_REGISTRATION_V1",
        "ok": True,
        "status": "registered",
        "operation_id": operation_id,
        "handler_id": resolved_id,
    }


def clear_daily_operation_handlers() -> None:
    """テスト用にregistryを空へ戻す。"""

    DAILY_OPERATION_HANDLERS.clear()
    DAILY_OPERATION_HANDLER_IDS.clear()


# 短い互換名も同じregistryへ束縛する。別registryは作らない。
register_daily_handler = register_daily_operation_handler
clear_daily_handlers = clear_daily_operation_handlers


def _context_root(context: Mapping[str, Any]) -> Path:
    value = context.get("repo_root") or context.get("cwd") or Path.cwd()
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError("daily_repo_root_invalid") from exc
    if not root.is_dir():
        raise ValueError("daily_repo_root_invalid")
    return root


def _producer_result(
    schema: str,
    *,
    ok: bool,
    status: str,
    operation_id: str,
    values: Mapping[str, Any] | None = None,
    failures: Sequence[str] = (),
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schemaVersion": schema,
        "ok": bool(ok),
        "status": status,
        "operation_id": operation_id,
        "producer_id": f"tools.news_grasp_daily_gate.{operation_id}",
        "observed_at": datetime.now(JST).isoformat(),
        "failures": [str(item) for item in failures],
    }
    if values:
        body.update(dict(values))
    body["output_hash"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def _default_static_check(**context: Any) -> dict[str, Any]:
    """固定route、runtime schema、installed automationのread-only検査。"""

    failures: list[str] = []
    try:
        profile = validate_profiles()
    except Exception as exc:  # noqa: BLE001 - static producer emits typed Red.
        profile = {}
        failures.append(f"gate_profile_red:{exc}")
    store = context.get("store")
    schema = {}
    if isinstance(store, runtime.DirectRunStore):
        try:
            schema = store.ensure_runtime_schema()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"runtime_schema_red:{exc}")
    else:
        failures.append("runtime_store_missing")
    try:
        config = runtime.validate_installed_automation_semantics()
    except Exception as exc:  # noqa: BLE001
        config = {"ok": False, "failures": [str(exc)]}
    if config.get("ok") is not True:
        failures.extend(f"installed_automation:{item}" for item in config.get("failures") or ["red"])
    issue_date = str(context.get("issue_date") or "")
    title_status = str((context.get("run") or {}).get("title_status") or "unavailable")
    return _producer_result(
        "NEWS_GRASP_STATIC_CHECK_RECEIPT_V1",
        ok=not failures,
        status="verified" if not failures else "red",
        operation_id="static_check",
        values={
            "issue_date": issue_date,
            "title_status": title_status if title_status else "unavailable",
            "profile": profile,
            "runtime_schema": schema,
            "automation": config,
        },
        failures=failures,
    )


def _default_scoped_contract_unit(**context: Any) -> dict[str, Any]:
    """署名済みpromotionまたは変更path対応testを一度だけ検証する。"""

    root = _context_root(context)
    path = root / "config" / "news_grasp_daily_45m_contract_v1.json"
    failures: list[str] = []
    payload: Mapping[str, Any] = {}
    digest = ""
    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        loaded = json.loads(raw.decode("utf-8"))
        if not isinstance(loaded, Mapping):
            failures.append("daily_contract_not_object")
        else:
            payload = loaded
            if payload.get("schemaVersion") != "NEWS_GRASP_DAILY_45M_CONTRACT_V1":
                failures.append("daily_contract_schema_invalid")
            operating = payload.get("taskOperatingContract")
            if not isinstance(operating, Mapping) or operating.get("unknownRoutePolicy") != "fail_closed":
                failures.append("daily_contract_unknown_route_policy_invalid")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"daily_contract_read_red:{type(exc).__name__}")
    scoped: Mapping[str, Any] = {}
    store = context.get("store")
    if not isinstance(store, runtime.DirectRunStore):
        failures.append("daily_scoped_test_store_missing")
    else:
        from tools.news_grasp_scoped_test_broker import evaluate_scoped_contract

        scoped = evaluate_scoped_contract(repo_root=root, state_root=store.state_root)
        if scoped.get("ok") is not True:
            failures.extend(str(item) for item in (scoped.get("failures") or ["daily_scoped_test_red"]))
    return _producer_result(
        "NEWS_GRASP_SCOPED_CONTRACT_RECEIPT_V1",
        ok=not failures,
        status="verified" if not failures else "red",
        operation_id="scoped_contract_unit",
        values={
            "contract_path": str(path),
            "contract_sha256": digest,
            "contract_schema": payload.get("schemaVersion") if isinstance(payload, Mapping) else "",
            "daily_operations": list(DAILY_OPERATIONS),
            "scoped_test": dict(scoped),
        },
        failures=failures,
    )


def _current_issue_producer_jsonl(context: Mapping[str, Any]) -> dict[str, Any]:
    from tools import validate_daily_quality

    issue = date.fromisoformat(str(context["issue_date"]))
    path = _context_root(context) / "data" / "articles.jsonl"
    failures = validate_daily_quality.validate_jsonl_source_freshness(path, issue)
    return {"predicate_id": "jsonl_source_freshness", "failures": list(failures)}


def _current_issue_producer_dedup(context: Mapping[str, Any]) -> dict[str, Any]:
    from tools import validate_daily_quality

    issue = date.fromisoformat(str(context["issue_date"]))
    path = _context_root(context) / "data" / "articles.jsonl"
    failures = validate_daily_quality.validate_dedup_annotation_present(path, issue)
    # dedup注釈のWARNINGはfreshness predicateを再実行せず観測へ残す。
    return {"predicate_id": "dedup_annotation_present", "failures": list(failures)}


def _current_issue_producer_digest(context: Mapping[str, Any]) -> dict[str, Any]:
    from tools import validate_daily_quality

    issue = date.fromisoformat(str(context["issue_date"]))
    root = _context_root(context)
    failures = validate_daily_quality.validate_issue_schedule(root / "digest", issue)
    failures.extend(validate_daily_quality.validate_digest_article_counts(root / "digest", issue))
    return {"predicate_id": "digest_schedule_and_counts", "failures": list(failures)}


def _current_issue_producer_summary(context: Mapping[str, Any]) -> dict[str, Any]:
    from tools import validate_daily_quality

    issue = str(context["issue_date"])
    summary = _context_root(context) / "digest" / "Summary" / f"{issue}.md"
    failures = validate_daily_quality.validate_summary_hero(summary)
    failures.extend(validate_daily_quality.validate_summary_emphasis(summary))
    return {"predicate_id": "summary_markdown_quality", "failures": list(failures)}


def _current_issue_producer_deepdive(context: Mapping[str, Any]) -> dict[str, Any]:
    from tools import deepdive_quality

    issue = str(context["issue_date"])
    observation = deepdive_quality.audit_issue(
        repo_root=_context_root(context),
        issue_date=issue,
        include_corpus=False,
        require_rendered_public=False,
        route="production_generation",
    )
    failures = list(observation.get("issueCodes") or observation.get("issues") or [])
    if observation.get("status") != "Green":
        failures.append("deepdive_current_issue_red")
    return {
        "predicate_id": "deepdive_current_issue_audit",
        "failures": failures,
        "observation": observation,
    }


_CURRENT_ISSUE_PRODUCER_GROUPS: tuple[tuple[str, Callable[[Mapping[str, Any]], dict[str, Any]]], ...] = (
    ("jsonl", _current_issue_producer_jsonl),
    ("dedup", _current_issue_producer_dedup),
    ("digest", _current_issue_producer_digest),
    ("summary", _current_issue_producer_summary),
    ("deepdive", _current_issue_producer_deepdive),
)


# 各predicateの評価主体を固定し、後続stageが同じfreshness/auditを再実行しない。
DAILY_PREDICATE_OWNERSHIP: Mapping[str, Mapping[str, str]] = {
    "jsonl_source_freshness": {"owner": "jsonl", "source": "data/articles.jsonl"},
    "dedup_annotation_present": {"owner": "dedup", "source": "data/articles.jsonl"},
    "digest_schedule_and_counts": {"owner": "digest", "source": "digest/<issue>"},
    "summary_markdown_quality": {"owner": "summary", "source": "digest/Summary/<issue>.md"},
    "deepdive_current_issue_audit": {"owner": "deepdive", "source": "digest/DeepDive/<issue>-DeepDive.md"},
    # 外部surfaceも同一SQLite predicate ledgerで所有者を固定する。
    "category_bundle": {"owner": "current_issue_integration", "source": "digest/<issue>/categories"},
    "summary_html_binding": {"owner": "html_producer", "source": "docs/<issue>/summary"},
    "deepdive_html_binding": {"owner": "html_producer", "source": "docs/deepdive/<issue>"},
    "daily_audio_identity": {"owner": "audio_producer", "source": "build/tts/daily/<issue>"},
    "deepdive_audio_identity": {"owner": "audio_producer", "source": "build/tts/deepdive/<issue>"},
    "distribution_manifest": {"owner": "distribution_producer", "source": "data/distribution/<issue>.json"},
    "youtube_identity": {"owner": "external_outbox", "source": "youtube/<issue>"},
    "playlist_identity": {"owner": "external_outbox", "source": "playlist/<issue>"},
    "notification_delivery": {"owner": "immutable_sender_ledger", "source": "notification/<issue>"},
    "pages_public_surface": {"owner": "consumer_public_verifier", "source": "pages/<issue>"},
    "publish_status": {"owner": "consumer_public_verifier", "source": "publish-status/<issue>"},
}


def _default_current_issue_integration(**context: Any) -> dict[str, Any]:
    """登録producer群を各一回だけ実行し、predicate evidenceを束ねる。"""

    results: list[dict[str, Any]] = []
    failures: list[str] = []
    store = context.get("store")
    run_id = str(context.get("run_id") or "").strip()
    run = context.get("run") if isinstance(context.get("run"), Mapping) else {}
    generation = str(
        context.get("generation_id")
        or f"{run_id}:{run.get('generation') or ''}"
    ).strip()
    if not isinstance(store, runtime.DirectRunStore) or not run_id or not generation:
        return _producer_result(
            "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="current_issue_integration",
            failures=("predicate_ledger_binding_missing",),
        )
    capability = context.get("route_capability")
    if not isinstance(capability, Mapping) or capability.get("capability") != "scheduled_production_daily":
        return _producer_result(
            "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="current_issue_integration",
            failures=("producer_route_capability_missing",),
        )
    content_receipt: Mapping[str, Any] = {}
    # production current_issue_integration は検証済み既存artifactを前提にせず、
    # 当日sourceからカテゴリ/summary/deepdive/派生成果物を一回だけ作る。
    # test storeは既存fixture互換のため、明示seamがある場合だけ生成経路を通す。
    content_seam = any(
        key in context
        for key in ("content_candidate_provider", "content_model_runner", "content_derived_builder")
    )
    if not store.test_only_allow_semantic_verifier or content_seam:
        try:
            from tools.news_grasp_daily_content import produce_current_issue
            from tools.publish_inventory import scheduled_category_ids

            content_receipt = produce_current_issue(
                repo_root=_context_root(context),
                issue_date=str(context.get("issue_date") or ""),
                run_id=run_id,
                scheduled_categories=tuple(
                    scheduled_category_ids(str(context.get("issue_date") or ""))
                ),
                candidate_provider=(
                    context.get("content_candidate_provider")
                    if store.test_only_allow_semantic_verifier
                    and callable(context.get("content_candidate_provider"))
                    else None
                ),
                model_runner=(
                    context.get("content_model_runner")
                    if store.test_only_allow_semantic_verifier
                    and callable(context.get("content_model_runner"))
                    else None
                ),
                derived_builder=(
                    context.get("content_derived_builder")
                    if store.test_only_allow_semantic_verifier
                    and callable(context.get("content_derived_builder"))
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 - canonical producer failure is typed Red.
            return _producer_result(
                "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
                ok=False,
                status="red",
                operation_id="current_issue_integration",
                values={"content_generation": dict(content_receipt)},
                failures=(f"content_generation_red:{type(exc).__name__}:{exc}",),
            )
        if content_receipt.get("ok") is not True:
            return _producer_result(
                "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
                ok=False,
                status="red",
                operation_id="current_issue_integration",
                values={"content_generation": dict(content_receipt)},
                failures=("content_generation_receipt_red",),
            )
    ledger = runtime.PredicateLedger(store)
    source_base = str(context.get("source_identity") or run.get("manifest_id") or "").strip()
    if not source_base:
        source_base = f"{context.get('issue_date') or ''}:{context.get('run_intent') or ''}"
    for producer_id, producer in _CURRENT_ISSUE_PRODUCER_GROUPS:
        predicate_hint = next(
            (item for item, binding in DAILY_PREDICATE_OWNERSHIP.items() if binding.get("owner") == producer_id),
            producer_id,
        )
        try:
            claim = ledger.claim_once(
                generation_id=generation,
                predicate_id=predicate_hint,
                owner=producer_id,
                source_identity=f"{source_base}:{producer_id}",
                evidence={"issue_date": str(context.get("issue_date") or ""), "producer_id": producer_id},
            )
        except (PermissionError, RuntimeError, ValueError) as exc:
            failures.append(f"predicate_claim_red:{producer_id}:{exc}")
            results.append({"predicate_id": predicate_hint, "producer_id": producer_id, "failures": [str(exc)]})
            continue
        try:
            row = producer(context)
        except Exception as exc:  # noqa: BLE001 - producer failure is typed Red.
            row = {"predicate_id": producer_id, "failures": [f"producer_error:{type(exc).__name__}"]}
        predicate_id = str(row.get("predicate_id") or producer_id)
        binding = DAILY_PREDICATE_OWNERSHIP.get(predicate_id)
        if binding is None or binding.get("owner") != producer_id:
            failures.append(f"predicate_owner_registry_mismatch:{predicate_id}")
        row["producer_id"] = producer_id
        row["claim"] = claim
        results.append(row)
        for item in row.get("failures") or []:
            text = str(item)
            if not text.startswith("WARNING:"):
                failures.append(text)
    warnings = [
        str(item)
        for row in results
        for item in row.get("failures") or []
        if str(item).startswith("WARNING:")
    ]
    release_receipt: Mapping[str, Any] = {}
    release_materializer = context.get("content_release_materializer")
    if not failures and (
        not store.test_only_allow_semantic_verifier or callable(release_materializer)
    ):
        try:
            if not callable(release_materializer):
                from tools.news_grasp_daily_release import materialize_and_seal_release

                release_materializer = materialize_and_seal_release
            release_receipt = release_materializer(
                store=store,
                repo_root=_context_root(context),
                issue_date=str(context.get("issue_date") or ""),
                run_id=run_id,
                run_intent=str(context.get("run_intent") or runtime.RUN_INTENT),
                writer_lease=str(context.get("writer_lease") or ""),
                fencing_token=int(context.get("fencing_token") or 0),
                content_receipt=content_receipt,
            )
        except Exception as exc:  # noqa: BLE001 - release seal failure is typed Red.
            failures.append(f"release_bundle_red:{type(exc).__name__}:{exc}")
        else:
            if release_receipt.get("ok") is not True:
                failures.append("release_bundle_receipt_red")
    return _producer_result(
        "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
        ok=not failures,
        status="verified_with_warnings" if not failures and warnings else ("verified" if not failures else "red"),
        operation_id="current_issue_integration",
        values={
            "content_generation": dict(content_receipt),
            "release_bundle": dict(release_receipt),
            "predicates": results,
            "warnings": warnings,
        },
        failures=failures,
    )


def _default_external_publication(**context: Any) -> dict[str, Any]:
    """publish seal済みの固定outboxを一度だけ実行する。"""

    store = context.get("store")
    if not isinstance(store, runtime.DirectRunStore):
        return _producer_result(
            "NEWS_GRASP_EXTERNAL_PUBLICATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="external_publication",
            failures=("runtime_store_missing",),
        )
    try:
        from tools.news_grasp_daily_external import execute_external_publication

        fresh_run = runtime.inspect_run(store, run_id=str(context["run_id"]))
        publish_seal = fresh_run.get("publish_seal") if isinstance(fresh_run.get("publish_seal"), Mapping) else {}
        start_seal = fresh_run.get("start_seal") if isinstance(fresh_run.get("start_seal"), Mapping) else {}

        external = execute_external_publication(
            store=store,
            run_id=str(context["run_id"]),
            writer_lease=str(context["writer_lease"]),
            fencing_token=int(context.get("fencing_token") or 0),
            adapters=(
                context.get("external_adapters")
                if store.test_only_allow_semantic_verifier
                and isinstance(context.get("external_adapters"), Mapping)
                else None
            ),
            context={
                "issue_date": str(context.get("issue_date") or ""),
                "run_intent": str(context.get("run_intent") or ""),
                "repo_root": str(_context_root(context)),
                "run_id": str(context.get("run_id") or ""),
                "manifest_id": str(fresh_run.get("manifest_id") or ""),
                "bundle_id": str(publish_seal.get("bundleId") or ""),
                "fencing_token": int(context.get("fencing_token") or 0),
                "release_commit_sha": str(publish_seal.get("releaseCommitSha") or ""),
                "remote_base_sha": str(start_seal.get("remoteBaseSha") or ""),
                "exact_write_set": list(publish_seal.get("exactWriteSet") or ()),
                "publish_seal": dict(publish_seal),
                "start_seal": dict(start_seal),
            },
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        return _producer_result(
            "NEWS_GRASP_EXTERNAL_PUBLICATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="external_publication",
            failures=(str(exc),),
        )
    ok = external.get("ok") is True and external.get("status") == "completed"
    return _producer_result(
        "NEWS_GRASP_EXTERNAL_PUBLICATION_RECEIPT_V1",
        ok=ok,
        status="published" if ok else str(external.get("status") or "red"),
        operation_id="external_publication",
        values={"outbox": external, "external_started": bool(external.get("adapter_call_count"))},
        failures=external.get("failures") or (() if ok else (str(external.get("exact_successor") or "external_publication_red"),)),
    )


def _default_consumer_public_verification(**context: Any) -> dict[str, Any]:
    """fresh public consumer verifierを一回だけ実行する。"""

    public_base_url = str(context.get("public_base_url") or os.environ.get("NEWS_GRASP_PUBLIC_BASE_URL", "")).strip()
    if not public_base_url:
        return _producer_result(
            "NEWS_GRASP_CONSUMER_PUBLIC_VERIFICATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="consumer_public_verification",
            failures=("public_base_url_missing",),
        )
    try:
        from tools.news_grasp_direct_completion import verify_direct_public_completion

        fresh_run = runtime.inspect_run(
            context["store"],
            run_id=str(context.get("run_id") or ""),
        )
        publish_seal = (
            fresh_run.get("publish_seal")
            if isinstance(fresh_run.get("publish_seal"), Mapping)
            else {}
        )
        external_outbox = runtime.inspect_external_outbox(
            context["store"],
            run_id=str(context.get("run_id") or ""),
        )

        observation = verify_direct_public_completion(
            repo_root=_context_root(context),
            issue_date=str(context["issue_date"]),
            public_base_url=public_base_url,
            remote="origin",
            branch="main",
            wait_sec=0,
            poll_sec=30,
            run_id=str(context.get("run_id") or ""),
            run_intent=str(context.get("run_intent") or runtime.RUN_INTENT),
            manifest_id=str(fresh_run.get("manifest_id") or ""),
            bundle_id=str(publish_seal.get("bundleId") or ""),
            release_commit_sha=str(publish_seal.get("releaseCommitSha") or ""),
            external_outbox=external_outbox,
        )
    except Exception as exc:  # noqa: BLE001 - public verifier emits typed Red.
        return _producer_result(
            "NEWS_GRASP_CONSUMER_PUBLIC_VERIFICATION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="consumer_public_verification",
            failures=(f"public_verifier_error:{type(exc).__name__}",),
        )
    ok = observation.get("ok") is True
    run = fresh_run
    observed_at = str(observation.get("observedAt") or datetime.now(JST).isoformat())
    observation_nonce = str(
        observation.get("observationToken")
        or (observation.get("observation") or {}).get("nonce")
        or ""
    ).strip()
    freshness_binding = {
        "runId": str(context.get("run_id") or ""),
        "issueDate": str(context.get("issue_date") or ""),
        "runIntent": str(context.get("run_intent") or ""),
        "generation": run.get("generation"),
        "manifestId": str(run.get("manifest_id") or context.get("manifest_id") or ""),
        "fencingBindingHash": runtime.fencing_binding_hash(
            run_id=str(context.get("run_id") or ""),
            generation=int(run.get("generation") or 0),
            writer_lease=str(context.get("writer_lease") or ""),
            fencing_token=int(context.get("fencing_token") or 0),
        ),
        "updatedAt": str(run.get("updated_at") or ""),
        "observedAt": observed_at,
        "observationNonce": observation_nonce,
    }
    if not observation_nonce:
        ok = False
    return _producer_result(
        "NEWS_GRASP_CONSUMER_PUBLIC_VERIFICATION_RECEIPT_V1",
        ok=ok,
        status="verified" if ok else "red",
        operation_id="consumer_public_verification",
        values={
            "observation": observation,
            "observation_token": observation_nonce,
            "external_operation_id": observation.get("externalOperationId") or observation.get("external_operation_id") or "",
            "freshnessBinding": freshness_binding,
        },
        failures=observation.get("failures") or observation.get("reasonCodes") or (("public_verification_red",) if not ok else ()),
    )


def _default_atomic_completion(**context: Any) -> dict[str, Any]:
    """先行五receiptとfresh consumer observationを最終receiptへ束ねる。"""

    store = context.get("store")
    run_id = str(context.get("run_id") or "")
    if not isinstance(store, runtime.DirectRunStore) or not run_id:
        return _producer_result(
            "NEWS_GRASP_ATOMIC_COMPLETION_RECEIPT_V1",
            ok=False,
            status="red",
            operation_id="atomic_completion",
            failures=("runtime_store_or_run_missing",),
        )
    prior: list[dict[str, Any]] = []
    failures: list[str] = []
    for operation_id in DAILY_OPERATIONS[:-1]:
        receipt = runtime.get_daily_operation_receipt(store, run_id=run_id, operation_id=operation_id)
        if receipt is None or receipt.get("ok") is not True or receipt.get("status") != "completed":
            failures.append(f"prior_operation_receipt_missing:{operation_id}")
        else:
            prior.append(receipt)
    if not any(str(item.get("operation_id") or "") == "consumer_public_verification" for item in prior):
        failures.append("fresh_consumer_observation_missing")
    consumer_receipt: Mapping[str, Any] = {}
    for item in prior:
        if str(item.get("operation_id") or "") == "consumer_public_verification":
            candidate = item.get("producer_receipt")
            if isinstance(candidate, Mapping):
                consumer_receipt = candidate
            break
    consumer_hash = (
        hashlib.sha256(
            json.dumps(dict(consumer_receipt), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if consumer_receipt
        else ""
    )
    if not consumer_hash:
        failures.append("fresh_consumer_receipt_hash_missing")
    return _producer_result(
        "NEWS_GRASP_ATOMIC_COMPLETION_RECEIPT_V1",
        ok=not failures,
        status="completed" if not failures else "red",
        operation_id="atomic_completion",
        values={
            "prior_receipt_count": len(prior),
            "completion_authority": "consumer-owned-public-verifier",
            "consumer_receipt_hash": consumer_hash,
        },
        failures=failures,
    )


# module import時点で本番handlerを固定解決する。外部registerは不要。
DAILY_OPERATION_HANDLERS.update({
    "static_check": _default_static_check,
    "scoped_contract_unit": _default_scoped_contract_unit,
    "current_issue_integration": _default_current_issue_integration,
    "external_publication": _default_external_publication,
    "consumer_public_verification": _default_consumer_public_verification,
    "atomic_completion": _default_atomic_completion,
})
DAILY_OPERATION_HANDLER_IDS.update({
    operation_id: f"tools.news_grasp_daily_gate.{operation_id}"
    for operation_id in DAILY_OPERATIONS
})


def _canonical_argv(operation_id: str) -> tuple[str, ...]:
    return daily_operation_command(operation_id)


def _handler_spec(
    operation_id: str,
    handlers: Mapping[str, Any] | None,
) -> tuple[Callable[..., Any] | None, str]:
    selected: Any = None
    selected_id = ""
    if handlers is not None:
        if not isinstance(handlers, Mapping):
            raise TypeError("daily_handlers_invalid")
        provided = operation_id in handlers
        selected = handlers.get(operation_id)
        if provided and selected is None:
            return None, ""
        if selected is not None:
            if isinstance(selected, Mapping):
                selected_id = str(selected.get("handler_id") or selected.get("handlerId") or "").strip()
                selected = selected.get("handler") or selected.get("producer")
            elif isinstance(selected, tuple) and len(selected) == 2:
                selected_id = str(selected[0] or "").strip()
                selected = selected[1]
    if selected is None and (handlers is None or operation_id not in handlers):
        selected = DAILY_OPERATION_HANDLERS.get(operation_id)
        selected_id = DAILY_OPERATION_HANDLER_IDS.get(operation_id, "")
    if selected is None:
        return None, ""
    if not callable(selected):
        raise TypeError("daily_operation_handler_invalid")
    if not selected_id:
        selected_id = str(
            getattr(selected, "handler_id", "")
            or f"{getattr(selected, '__module__', '')}.{getattr(selected, '__qualname__', repr(selected))}"
        ).strip(".")
    if not selected_id:
        raise ValueError("daily_operation_handler_id_invalid")
    return selected, selected_id


def _invoke_handler(handler: Callable[..., Any], context: Mapping[str, Any]) -> Any:
    """handlerをsignatureに合わせて一度だけ呼ぶ。TypeError再実行はしない。"""

    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        # signatureが取得できないcallableも一回だけcontext positionalで呼ぶ。
        return handler(dict(context))
    parameters = list(signature.parameters.values())
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters):
        return handler(**dict(context))
    if not parameters:
        return handler()
    if len(parameters) == 1 and parameters[0].name in {"context", "ctx", "operation_context"}:
        return handler(dict(context))
    kwargs: dict[str, Any] = {}
    positional_only: list[Any] = []
    for parameter in parameters:
        if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
            if parameter.name in context:
                positional_only.append(context[parameter.name])
            elif parameter.default is inspect.Parameter.empty:
                return handler(dict(context))
        elif parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        } and parameter.name in context:
            kwargs[parameter.name] = context[parameter.name]
        elif parameter.default is inspect.Parameter.empty:
            return handler(dict(context))
    return handler(*positional_only, **kwargs)


def _producer_receipt(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("producer_receipt", "producerReceipt", "receipt"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            return candidate
    if any(key in value for key in ("schemaVersion", "schema_version")):
        return value
    return None


def _validate_producer_receipt(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    receipt = _producer_receipt(value)
    if receipt is None:
        return None, "daily_operation_producer_receipt_missing"
    if receipt.get("ok") is not True:
        return None, "daily_operation_producer_receipt_red"
    schema = str(receipt.get("schemaVersion") or receipt.get("schema_version") or "").strip()
    if not schema or schema in {DAILY_GATE_SCHEMA, "NEWS_GRASP_DAILY_OPERATION_AUTHORIZATION_V1"}:
        return None, "daily_operation_producer_receipt_schema_invalid"
    status = str(receipt.get("status") or receipt.get("result") or "").casefold()
    if status in {"", "authorized", "admitted", "not_executed", "blocked", "red", "failed", "missing"}:
        return None, "daily_operation_producer_receipt_status_invalid"
    output_hash = str(receipt.get("output_hash") or receipt.get("outputHash") or "").strip()
    if output_hash:
        unsigned = dict(receipt)
        unsigned.pop("output_hash", None)
        unsigned.pop("outputHash", None)
        actual_hash = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if output_hash != actual_hash:
            return None, "daily_operation_producer_receipt_output_hash_mismatch"
    return dict(receipt), None


def _issue_date_default() -> str:
    return datetime.now(JST).date().isoformat()


def _input_hash(
    *,
    operation_id: str,
    run_id: str,
    issue_date: str,
    run_intent: str,
    context: Mapping[str, Any] | None,
    explicit: str | None,
) -> str:
    supplied = str(explicit or (context or {}).get("input_hash") or (context or {}).get("inputHash") or "").strip()
    binding = {
        "operation_id": operation_id,
        "run_id": run_id,
        "issue_date": issue_date,
        "run_intent": run_intent,
        "source_baseline": str((context or {}).get("source_baseline") or ""),
        "manifest_id": str((context or {}).get("manifest_id") or ""),
        "manifest_reservation_id": str((context or {}).get("manifest_reservation_id") or ""),
        "runtime_generation": str((context or {}).get("runtime_generation") or ""),
    }
    calculated = hashlib.sha256(
        json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if supplied and supplied != calculated:
        raise ValueError("daily_input_hash_caller_override_rejected")
    return calculated


def _resolve_run(
    store: runtime.DirectRunStore,
    *,
    run_id: str | None,
    writer_lease: str | None,
    cwd: str | Path,
    issue_date: str,
    run_intent: str,
    automation_id: str,
    scheduler_trigger_at: str | None,
    manifest_id: str,
    manifest_reservation_id: str,
    source_baseline: str,
    runtime_generation: str,
    remote_base_sha: str,
    allowed_side_effect_ids: Sequence[str],
) -> tuple[dict[str, Any] | None, str | None]:
    # active lookupより前にV1→V2 migrationを完了させる。旧rowを先に
    # attachするとmigration receipt/notification ledgerなしでwriter扱いされる。
    try:
        store.ensure_runtime_schema()
    except Exception as exc:  # noqa: BLE001 - typed route Red
        return None, f"daily_runtime_migration_red:{type(exc).__name__}"
    if run_id:
        try:
            state = runtime.inspect_run(store, run_id=run_id)
        except (KeyError, ValueError):
            return None, "daily_run_not_found"
        lease = str(writer_lease or "").strip()
        if not lease:
            return None, "daily_writer_lease_missing"
        if state.get("status") not in {"active", "executing", "finalizing"}:
            return None, "daily_run_not_writable"
        if (
            str(state.get("automation_id") or "") != automation_id
            or str(state.get("issue_date") or "") != issue_date
            or str(state.get("run_intent") or "") != run_intent
        ):
            return None, "daily_run_identity_mismatch"
        state["writer_lease"] = lease
        return state, None
    # 観測lookup→startのTOCTOUを作らない。単一BEGIN IMMEDIATEを持つ
    # start_runだけがnew/attach/expired-owner recoveryを決定する。
    started = runtime.start_run(
        store,
        automation_id=automation_id,
        cwd=cwd,
        issue_date=issue_date,
        run_intent=run_intent,
        manifest_id=manifest_id,
        manifest_reservation_id=manifest_reservation_id,
        scheduler_trigger_at=scheduler_trigger_at,
        source_baseline=source_baseline,
        runtime_generation=runtime_generation,
        remote_base_sha=remote_base_sha,
        allowed_side_effect_ids=list(allowed_side_effect_ids),
    )
    if started.get("status") == "blocked":
        return None, str((started.get("failures") or ["daily_run_blocked"])[0])
    if (
        str(started.get("status") or "") in {"active", "executing", "finalizing"}
        and started.get("writer_lease")
        and int(started.get("fencing_token") or 0) > 0
    ):
        return started, None
    if started.get("status") == "attached":
        return None, "daily_writer_lease_required_for_existing_run"
    return None, "daily_active_run_missing"


def _receipt_projection(
    receipt: Mapping[str, Any],
    *,
    authorization: Mapping[str, Any],
    operation_id: str,
    expected: Sequence[str],
) -> dict[str, Any]:
    return {
        **dict(receipt),
        "schemaVersion": DAILY_GATE_SCHEMA,
        "ok": receipt.get("status") == "completed" and receipt.get("ok") is True,
        "status": receipt.get("status") or "red",
        "profile": authorization.get("profile"),
        "operation_id": operation_id,
        "operation_index": DAILY_OPERATIONS.index(operation_id),
        "operation_count": len(DAILY_OPERATIONS),
        "phase": operation_id,
        "argv": list(expected),
        "authorization": dict(authorization),
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }


def run_daily_operation(
    operation_id: str,
    *,
    command: Sequence[str] | None = None,
    completed_operations: Sequence[str] | None = None,
    store: runtime.DirectRunStore | None = None,
    run_id: str | None = None,
    writer_lease: str | None = None,
    cwd: str | Path | None = None,
    issue_date: str | None = None,
    run_intent: str = runtime.RUN_INTENT,
    automation_id: str = runtime.AUTOMATION_ID,
    scheduler_trigger_at: str | None = None,
    manifest_id: str = "",
    manifest_reservation_id: str = "",
    source_baseline: str = "",
    runtime_generation: str = runtime.RUNTIME_SCHEMA_V2,
    remote_base_sha: str = "",
    allowed_side_effect_ids: Sequence[str] = (),
    input_hash: str | None = None,
    context: Mapping[str, Any] | None = None,
    handlers: Mapping[str, Any] | None = None,
    route_capability: Mapping[str, Any] | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """一つのDaily operationを認可し、handlerとproducer receiptを適用する。"""

    if operation_id not in DAILY_OPERATIONS:
        authorize_daily_operation(operation_id, list(command or ()))
    expected = _canonical_argv(operation_id)
    if command is None and not (store is not None and getattr(store, "test_only_allow_semantic_verifier", False)):
        return _red_result(operation_id, "daily_command_required_from_global_broker")
    observed = expected if command is None else tuple(command)
    if route_capability is None and not (store is not None and getattr(store, "test_only_allow_semantic_verifier", False)):
        return _red_result(operation_id, "daily_route_capability_required_from_global_broker")
    capability = route_capability
    if capability is None:
        # test-only seamに限りfixtureを短縮できる。本番では上のfail-closed
        # 分岐でglobal broker capabilityの欠落を拒否する。
        capability = build_daily_route_capability(
            operation_id,
            runtime_generation=runtime_generation,
        )
    authorization = authorize_daily_operation(
        operation_id,
        observed,
        capability=capability,
        runtime_generation=runtime_generation,
    )
    index = DAILY_OPERATIONS.index(operation_id)
    completed = tuple(completed_operations or ())
    if len(set(completed)) != len(completed):
        raise NewsGraspGateProfileError("daily_operation_duplicate")
    if any(item not in DAILY_OPERATIONS for item in completed):
        raise NewsGraspGateProfileError("daily_operation_unknown_completed")
    expected_previous = DAILY_OPERATIONS[:index]
    if completed and tuple(completed) != expected_previous:
        raise NewsGraspGateProfileError("daily_operation_order_violation")
    if store is None or not isinstance(store, runtime.DirectRunStore):
        return _red_result(operation_id, "daily_execution_store_required", authorization=dict(authorization))

    # writer capabilityは開始processが一度だけ受け取り、後続processへ明示継承する。
    # DB observerからlease/fenceを再取得する経路はsingle-flightの所有境界を壊す。
    creates_run = run_id is None
    if creates_run and operation_id != DAILY_OPERATIONS[0]:
        return _red_result(operation_id, "daily_writer_capability_required", authorization=dict(authorization))
    if run_id is not None and (not str(writer_lease or "").strip() or fencing_token is None):
        return _red_result(operation_id, "daily_writer_capability_incomplete", authorization=dict(authorization))
    if creates_run and (writer_lease is not None or fencing_token is not None):
        return _red_result(operation_id, "daily_writer_capability_inconsistent", authorization=dict(authorization))

    selected_handler, handler_id = _handler_spec(operation_id, handlers)
    if selected_handler is None:
        # 明示run_idのretryは既存receiptをauthorityとし、handlerを再実行しない。
        if run_id:
            existing = runtime.get_daily_operation_receipt(store, run_id=run_id, operation_id=operation_id)
            if existing is not None:
                return _receipt_projection(existing, authorization=authorization, operation_id=operation_id, expected=expected)
        return _red_result(operation_id, "daily_operation_handler_missing", authorization=dict(authorization))
    if handlers is not None and not store.test_only_allow_semantic_verifier:
        return _red_result(operation_id, "daily_handler_injection_production_forbidden", authorization=dict(authorization))

    issue = issue_date or _issue_date_default()
    requested_cwd = cwd or Path.cwd()
    protected_failure = protected_release_failure(repo_root=requested_cwd, issue_date=issue)
    if protected_failure:
        return _red_result(
            operation_id,
            protected_failure,
            protected_release=PROTECTED_RELEASE,
            protected_release_policy=PROTECTED_RELEASE_POLICY,
            exact_successor="explicit_new_release_authority_required",
        )
    state, failure = _resolve_run(
        store,
        run_id=run_id,
        writer_lease=writer_lease,
        cwd=requested_cwd,
        issue_date=issue,
        run_intent=str(run_intent),
        automation_id=str(automation_id),
        scheduler_trigger_at=scheduler_trigger_at,
        manifest_id=manifest_id,
        manifest_reservation_id=manifest_reservation_id,
        source_baseline=source_baseline,
        runtime_generation=runtime_generation,
        remote_base_sha=remote_base_sha,
        allowed_side_effect_ids=allowed_side_effect_ids,
    )
    if state is None:
        return _red_result(operation_id, failure or "daily_run_resolution_failed", authorization=dict(authorization))
    active_run_id = str(state.get("run_id") or "")
    lease = str(state.get("writer_lease") or writer_lease or "")
    if not active_run_id or not lease:
        return _red_result(operation_id, "daily_active_writer_missing", authorization=dict(authorization))
    state_token = int(state.get("fencing_token") or 0)
    if fencing_token is not None and int(fencing_token) != state_token:
        return _red_result(operation_id, "fencing_token_fenced", authorization=dict(authorization))
    effective_fencing_token = int(fencing_token if fencing_token is not None else state_token)
    if effective_fencing_token <= 0:
        return _red_result(operation_id, "fencing_token_required", authorization=dict(authorization))

    start_seal = state.get("start_seal") if isinstance(state.get("start_seal"), Mapping) else {}
    bound_source_baseline = str(start_seal.get("sourceBaseline") or source_baseline or "")
    bound_manifest_id = str(state.get("manifest_id") or start_seal.get("manifestId") or manifest_id or "")
    bound_manifest_reservation_id = str(
        state.get("manifest_reservation_id")
        or start_seal.get("manifestReservationId")
        or manifest_reservation_id
        or ""
    )
    bound_runtime_generation = str(start_seal.get("runtimeGeneration") or runtime_generation or "")
    bound_remote_base_sha = str(start_seal.get("remoteBaseSha") or remote_base_sha or "")
    try:
        admission = runtime.admit_daily_operation(
            store,
            run_id=active_run_id,
            writer_lease=lease,
            operation_id=operation_id,
            fencing_token=effective_fencing_token,
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        return _red_result(operation_id, f"daily_operation_admission_red:{exc}", authorization=dict(authorization))

    try:
        effective_hash = _input_hash(
            operation_id=operation_id,
            run_id=active_run_id,
            issue_date=issue,
            run_intent=str(run_intent),
            context={
                **dict(context or {}),
                "source_baseline": bound_source_baseline,
                "manifest_id": bound_manifest_id,
                "manifest_reservation_id": bound_manifest_reservation_id,
                "runtime_generation": bound_runtime_generation,
            },
            explicit=input_hash,
        )
    except ValueError as exc:
        return _red_result(operation_id, str(exc), authorization=dict(authorization))
    existing = runtime.get_daily_operation_receipt(
        store,
        run_id=active_run_id,
        operation_id=operation_id,
    )
    if existing is not None:
        if (
            str(existing.get("input_hash") or "") != effective_hash
            or str(existing.get("handler_id") or "") != handler_id
        ):
            raise RuntimeError("idempotency_conflict")
        return _receipt_projection(existing, authorization=authorization, operation_id=operation_id, expected=expected)

    operation_context: dict[str, Any] = {
        "operation_id": operation_id,
        "operation_index": index,
        "run_id": active_run_id,
        "writer_lease": lease,
        "issue_date": issue,
        "run_intent": str(run_intent),
        "automation_id": str(automation_id),
        "cwd": Path(requested_cwd),
        "store": store,
        "run": dict(state),
        "input_hash": effective_hash,
        "inputHash": effective_hash,
        "humanImpact": authorization["humanImpact"],
        "route_capability": dict(authorization.get("route_capability") or {}),
        "fencing_token": effective_fencing_token,
        "source_baseline": bound_source_baseline,
        "manifest_id": bound_manifest_id,
        "manifest_reservation_id": bound_manifest_reservation_id,
        "runtime_generation": bound_runtime_generation,
        "remote_base_sha": bound_remote_base_sha,
        "slo_dispatch": dict(admission),
    }
    if context:
        # callerがrun/lease/hash/identityを後から上書きする経路を閉じる。
        immutable_context_keys = {
            "operation_id", "operation_index", "run_id", "writer_lease", "issue_date",
            "run_intent", "automation_id", "cwd", "store", "run", "input_hash", "inputHash",
            "source_baseline", "manifest_id", "runtime_generation", "remote_base_sha",
            "manifest_reservation_id",
        }
        operation_context.update(
            {key: value for key, value in dict(context).items() if key not in immutable_context_keys}
        )
    try:
        claim = runtime.claim_daily_operation(
            store,
            run_id=active_run_id,
            writer_lease=lease,
            operation_id=operation_id,
            input_hash=effective_hash,
            handler_id=handler_id,
            fencing_token=effective_fencing_token,
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        return _red_result(operation_id, str(exc), authorization=dict(authorization))
    if claim.get("status") == "completed" and isinstance(claim.get("receipt"), Mapping):
        return _receipt_projection(claim["receipt"], authorization=authorization, operation_id=operation_id, expected=expected)
    if claim.get("status") != "claimed":
        return _red_result(operation_id, "daily_operation_claim_not_owned", authorization=dict(authorization), claim=claim)

    def _release_claim(reason: str) -> None:
        runtime.release_daily_operation_claim(
            store,
            run_id=active_run_id,
            writer_lease=lease,
            operation_id=operation_id,
            input_hash=effective_hash,
            handler_id=handler_id,
            reason=reason,
            fencing_token=effective_fencing_token,
        )

    heartbeat_stop = threading.Event()
    heartbeat_failures: list[str] = []

    def _heartbeat() -> None:
        interval = max(1.0, min(30.0, float(store.lease_ttl.total_seconds()) / 3.0))
        while not heartbeat_stop.wait(interval):
            try:
                runtime.renew_daily_writer_lease(
                    store,
                    run_id=active_run_id,
                    writer_lease=lease,
                    fencing_token=effective_fencing_token,
                )
            except Exception as exc:  # noqa: BLE001 - owner loss is typed Red
                heartbeat_failures.append(type(exc).__name__)
                heartbeat_stop.set()
                return

    heartbeat_thread = threading.Thread(
        target=_heartbeat,
        name=f"news-grasp-lease-{operation_id}",
        daemon=True,
    )
    heartbeat_thread.start()

    try:
        produced = _invoke_handler(selected_handler, operation_context)
    except Exception as exc:  # noqa: BLE001 - handler fault is a typed producer Red.
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5.0)
        try:
            runtime.record_timing_event(
                store,
                run_id=active_run_id,
                writer_lease=lease,
                event_kind="failure",
                started_at=store.now(),
                ended_at=store.now(),
                evidence={"operation_id": operation_id, "phase": "daily_operation", "error_type": type(exc).__name__},
                fencing_token=effective_fencing_token,
            )
        except (PermissionError, RuntimeError, ValueError):
            pass
        try:
            _release_claim(f"handler_error:{type(exc).__name__}")
        except (PermissionError, RuntimeError, ValueError):
            pass
        return _red_result(
            operation_id,
            "daily_operation_handler_error",
            error_type=type(exc).__name__,
            authorization=dict(authorization),
        )
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=5.0)
    if heartbeat_thread.is_alive() or heartbeat_failures:
        try:
            _release_claim("daily_writer_heartbeat_red")
        except (PermissionError, RuntimeError, ValueError):
            pass
        return _red_result(
            operation_id,
            "daily_writer_heartbeat_red",
            heartbeat_failures=list(heartbeat_failures),
            authorization=dict(authorization),
        )
    producer, producer_failure = _validate_producer_receipt(produced)
    if producer is None:
        try:
            runtime.record_timing_event(
                store,
                run_id=active_run_id,
                writer_lease=lease,
                event_kind="failure",
                started_at=store.now(),
                ended_at=store.now(),
                evidence={"operation_id": operation_id, "phase": "daily_operation", "reason": producer_failure or "producer_receipt_invalid"},
                fencing_token=effective_fencing_token,
            )
        except (PermissionError, RuntimeError, ValueError):
            pass
        try:
            _release_claim(producer_failure or "producer_receipt_invalid")
        except (PermissionError, RuntimeError, ValueError):
            pass
        if (
            operation_id == "external_publication"
            and isinstance(produced, Mapping)
            and str(produced.get("status") or "") == "reconcile_required"
        ):
            return _red_result(
                operation_id,
                str((produced.get("failures") or ["external_reconcile_required"])[0]),
                status="reconcile_required",
                exact_successor=str(produced.get("exact_successor") or "external_reconcile"),
                outbox=dict(produced),
                slo_dispatch=dict(admission),
                authorization=dict(authorization),
            )
        return _red_result(
            operation_id,
            producer_failure or "daily_operation_producer_receipt_invalid",
            authorization=dict(authorization),
        )
    try:
        applied = runtime.apply_daily_operation_atomic(
            store,
            run_id=active_run_id,
            writer_lease=lease,
            operation_id=operation_id,
            input_hash=effective_hash,
            handler_id=handler_id,
            producer_receipt=producer,
            fencing_token=effective_fencing_token,
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        try:
            runtime.record_timing_event(
                store,
                run_id=active_run_id,
                writer_lease=lease,
                event_kind="failure",
                started_at=store.now(),
                ended_at=store.now(),
                evidence={"operation_id": operation_id, "phase": "daily_operation", "reason": str(exc)},
                fencing_token=effective_fencing_token,
            )
        except (PermissionError, RuntimeError, ValueError):
            pass
        try:
            _release_claim(str(exc))
        except (PermissionError, RuntimeError, ValueError):
            pass
        return _red_result(
            operation_id,
            str(exc),
            authorization=dict(authorization),
            producer_receipt=dict(producer),
        )
    projected = _receipt_projection(applied, authorization=authorization, operation_id=operation_id, expected=expected)
    if operation_id == DAILY_OPERATIONS[-1]:
        try:
            finalized = runtime.finalize_public_completion(
                store,
                run_id=active_run_id,
                writer_lease=lease,
                exact_successor="public_completion",
                fencing_token=effective_fencing_token,
            )
        except (PermissionError, RuntimeError, ValueError) as exc:
            return _red_result(
                operation_id,
                f"daily_finalizer_red:{exc}",
                authorization=dict(authorization),
                operation_receipt=projected,
            )
        return {
            **projected,
            **dict(finalized),
            "schemaVersion": DAILY_GATE_SCHEMA,
            "ok": finalized.get("ok") is True and finalized.get("status") == "completed",
            "status": finalized.get("status") or "red",
            "operation_id": operation_id,
            "phase": operation_id,
            "operation_receipt": projected,
        }
    return projected


def run_daily_sequence(
    *,
    command_factory: Any = None,
    handler_factory: Any = None,
    handlers: Mapping[str, Any] | None = None,
    store: runtime.DirectRunStore | None = None,
    cwd: str | Path | None = None,
    issue_date: str | None = None,
    run_intent: str = runtime.RUN_INTENT,
    automation_id: str = runtime.AUTOMATION_ID,
    scheduler_trigger_at: str | None = None,
    manifest_id: str = "",
    manifest_reservation_id: str = "",
    source_baseline: str = "",
    runtime_generation: str = runtime.RUNTIME_SCHEMA_V2,
    remote_base_sha: str = "",
    allowed_side_effect_ids: Sequence[str] = (),
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """六operationを一process内の同一writerで順序どおり一回だけ実行する。"""

    if store is None:
        return [_red_result("", "daily_execution_store_required")]
    issue = issue_date or _issue_date_default()
    requested_cwd = cwd or Path.cwd()
    protected_failure = protected_release_failure(repo_root=requested_cwd, issue_date=issue)
    if protected_failure:
        return [
            _red_result(
                "static_check",
                protected_failure,
                protected_release=PROTECTED_RELEASE,
                protected_release_policy=PROTECTED_RELEASE_POLICY,
                exact_successor="explicit_new_release_authority_required",
            )
        ]
    state, failure = _resolve_run(
        store,
        run_id=None,
        writer_lease=None,
        cwd=requested_cwd,
        issue_date=issue,
        run_intent=run_intent,
        automation_id=automation_id,
        scheduler_trigger_at=scheduler_trigger_at,
        manifest_id=manifest_id,
        manifest_reservation_id=manifest_reservation_id,
        source_baseline=source_baseline,
        runtime_generation=runtime_generation,
        remote_base_sha=remote_base_sha,
        allowed_side_effect_ids=allowed_side_effect_ids,
    )
    if state is None:
        return [_red_result("static_check", failure or "daily_run_resolution_failed")]
    sequence_run_id = str(state.get("run_id") or "")
    sequence_writer_lease = str(state.get("writer_lease") or "")
    sequence_fencing_token = int(state.get("fencing_token") or 0)
    if not sequence_run_id or not sequence_writer_lease or sequence_fencing_token <= 0:
        return [_red_result("static_check", "daily_active_writer_missing")]
    if state.get("single_flight") == "recovered_finalizer_receipt":
        # 六operationは既にatomic receipt化済みであり、再度handler/admissionへ
        # 通すとpredicate重複とupdated_at driftを作る。保存済み六receiptを読み、
        # final transactionだけを同じnonceでresumeする。
        recovered_receipts = [
            runtime.get_daily_operation_receipt(
                store,
                run_id=sequence_run_id,
                operation_id=operation_id,
            )
            for operation_id in DAILY_OPERATIONS
        ]
        if any(receipt is None for receipt in recovered_receipts):
            return [_red_result("atomic_completion", "finalizer_recovery_operation_receipt_missing")]
        try:
            finalized = runtime.finalize_public_completion(
                store,
                run_id=sequence_run_id,
                writer_lease=sequence_writer_lease,
                exact_successor="public_completion",
                fencing_token=sequence_fencing_token,
            )
        except (PermissionError, RuntimeError, ValueError) as exc:
            return [_red_result("atomic_completion", f"daily_finalizer_resume_red:{exc}")]
        projected = [dict(receipt or {}) for receipt in recovered_receipts]
        projected[-1] = {
            **projected[-1],
            **dict(finalized),
            "schemaVersion": DAILY_GATE_SCHEMA,
            "ok": finalized.get("ok") is True and finalized.get("status") == "completed",
            "status": finalized.get("status") or "red",
            "operation_id": "atomic_completion",
            "phase": "atomic_completion",
            "recovery_mode": "finalizer_receipt_resume",
        }
        return projected
    receipts: list[dict[str, Any]] = []
    for operation_id in DAILY_OPERATIONS:
        command = command_factory(operation_id) if callable(command_factory) else _canonical_argv(operation_id)
        selected_handlers = handlers
        if callable(handler_factory):
            produced_handler = handler_factory(operation_id)
            selected_handlers = {operation_id: produced_handler}
        result = run_daily_operation(
            operation_id,
            command=command,
            completed_operations=[item["operation_id"] for item in receipts if item.get("ok") is True],
            store=store,
            run_id=sequence_run_id,
            writer_lease=sequence_writer_lease,
            cwd=cwd,
            issue_date=issue_date,
            run_intent=run_intent,
            automation_id=automation_id,
            scheduler_trigger_at=scheduler_trigger_at,
            manifest_id=manifest_id,
            manifest_reservation_id=manifest_reservation_id,
            source_baseline=source_baseline,
            runtime_generation=runtime_generation,
            remote_base_sha=remote_base_sha,
            allowed_side_effect_ids=allowed_side_effect_ids,
            context=context,
            handlers=selected_handlers,
            route_capability=build_daily_route_capability(
                operation_id,
                runtime_generation=runtime_generation,
            ),
            fencing_token=sequence_fencing_token,
        )
        receipts.append(result)
        if result.get("ok") is not True:
            # provider結果が曖昧なexternal publicationだけ、同一writer・同一
            # input hashでread-only reconcileを一回行う。二回目もRedなら終了し、
            # provider callの再送へは進まない。
            if (
                operation_id == "external_publication"
                and str(result.get("status") or "") == "reconcile_required"
                and bool((result.get("slo_dispatch") or {}).get("retry_allowed", True))
            ):
                retry = run_daily_operation(
                    operation_id,
                    command=command,
                    completed_operations=[item["operation_id"] for item in receipts[:-1] if item.get("ok") is True],
                    store=store,
                    run_id=sequence_run_id,
                    writer_lease=sequence_writer_lease,
                    cwd=cwd,
                    issue_date=issue_date,
                    run_intent=run_intent,
                    automation_id=automation_id,
                    scheduler_trigger_at=scheduler_trigger_at,
                    manifest_id=manifest_id,
                    manifest_reservation_id=manifest_reservation_id,
                    source_baseline=source_baseline,
                    runtime_generation=runtime_generation,
                    remote_base_sha=remote_base_sha,
                    allowed_side_effect_ids=allowed_side_effect_ids,
                    context=context,
                    handlers=selected_handlers,
                    route_capability=build_daily_route_capability(operation_id, runtime_generation=runtime_generation),
                    fencing_token=sequence_fencing_token,
                )
                receipts[-1] = retry
                if retry.get("ok") is True:
                    continue
            break
    return receipts


def _default_state_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "News-Grasp" / "direct-mainline"
    return Path.cwd() / ".news-grasp-runtime"


def _git_ref_sha(root: Path, ref: str) -> str:
    """固定argvのgit観測だけで実行identityを補う。"""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return ""
    value = completed.stdout.strip().casefold()
    return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else ""


def _git_is_ancestor(root: Path, candidate: str, descendant: str) -> bool:
    """指定SHAの祖先関係をgitのexit codeだけで確認する。"""

    if not re.fullmatch(r"[0-9a-f]{40}", candidate) or not re.fullmatch(r"[0-9a-f]{40}", descendant):
        return False
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, descendant],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def resolve_daily_identity_context(*, repo_root: Path, issue_date: str) -> dict[str, Any]:
    """自然CLIが開始前に必要なseal材料を一度だけ解決する。

    任意のcaller JSON、環境変数、handlerではなく、canonical manifest/gitの
    read-only観測からのみ値を得る。欠落は推測で埋めず、呼び出し側がRedとして
    停止できる形で返す。
    """

    failures: list[str] = []
    # seal identityは実Git/route registryだけから観測する。環境変数や既存
    # manifestをauthorityにすると、callerがbaselineや副作用集合を差し替え、
    # completed同日runの再実行や別bundleへのrebindを誘発できるためである。
    observed_manifest_id = ""
    source_baseline = ""
    remote_base_sha = ""
    allowed_side_effect_ids = list(DAILY_ALLOWED_SIDE_EFFECT_IDS)
    manifest: Mapping[str, Any] = {}
    try:
        from tools.news_grasp_publish_contract import load_manifest

        loaded = load_manifest(repo_root, issue_date)
        if isinstance(loaded, Mapping):
            manifest = loaded
    except Exception:  # noqa: BLE001 - zero-artifact startではmanifest未作成が正規状態。
        manifest = {}
    observed_manifest_id = str(manifest.get("manifestId") or "").strip().casefold()
    source_baseline = _git_ref_sha(repo_root, "HEAD")
    observed_remote_base_sha = _git_ref_sha(repo_root, "origin/main")
    remote_base_sha = observed_remote_base_sha
    if not observed_remote_base_sha:
        failures.append("remote_base_sha_unobserved")
    if not re.fullmatch(r"[0-9a-f]{40}", source_baseline):
        failures.append("source_baseline_missing")
    if not re.fullmatch(r"[0-9a-f]{40}", remote_base_sha):
        failures.append("remote_base_sha_missing")
    head_sha = _git_ref_sha(repo_root, "HEAD")
    if re.fullmatch(r"[0-9a-f]{40}", source_baseline) and (
        not head_sha or not _git_is_ancestor(repo_root, source_baseline, head_sha)
    ):
        failures.append("source_baseline_not_ancestor")
    if observed_remote_base_sha and remote_base_sha != observed_remote_base_sha:
        failures.append("remote_base_sha_drift")
    if re.fullmatch(r"[0-9a-f]{40}", remote_base_sha) and (
        not head_sha or not _git_is_ancestor(repo_root, remote_base_sha, head_sha)
    ):
        failures.append("remote_base_sha_not_ancestor")
    if len(set(allowed_side_effect_ids)) != len(allowed_side_effect_ids) or not allowed_side_effect_ids:
        failures.append("allowed_side_effect_ids_missing")
    reservation_identity = {
        "automation_id": runtime.AUTOMATION_ID,
        "issue_date": issue_date,
        "run_intent": runtime.RUN_INTENT,
        "source_baseline": source_baseline,
        "remote_base_sha": remote_base_sha,
        "runtime_generation": runtime.RUNTIME_SCHEMA_V2,
        "allowed_side_effect_ids": allowed_side_effect_ids,
    }
    manifest_reservation_id = hashlib.sha256(
        json.dumps(reservation_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_IDENTITY_CONTEXT_V2",
        "ok": not failures,
        # start sealは完成前manifestを受け取らない。既存manifestはdrift観測に
        # だけ残し、actual IDはpublish seal transactionで一度だけ確定する。
        "manifest_id": "",
        "observed_manifest_id": observed_manifest_id,
        "manifest_reservation_id": manifest_reservation_id,
        "source_baseline": source_baseline,
        "remote_base_sha": remote_base_sha,
        "allowed_side_effect_ids": allowed_side_effect_ids,
        "failures": failures,
        "manifest": dict(manifest),
    }


def _main(
    argv: Sequence[str] | None = None,
    *,
    _test_only_allow_single_operation: bool = False,
) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        result = {
            "schemaVersion": DAILY_GATE_SCHEMA,
            "ok": False,
            "status": "daily_operation_argv_invalid",
            "failures": ["exactly_one_operation_argument_required"],
        }
        runtime._emit_cli(result)
        return 2
    if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(
        os.path.abspath(DAILY_PYTHON)
    ):
        runtime._emit_cli(
            {
                "schemaVersion": DAILY_GATE_SCHEMA,
                "ok": False,
                "status": "python_runtime_not_approved",
                "failures": ["fixed_python_3_12_required"],
                "expected_python": DAILY_PYTHON,
                "observed_python": sys.executable,
            }
        )
        return 2
    operation_id = args[0]
    if not _test_only_allow_single_operation:
        runtime._emit_cli(
            _red_result(operation_id, "daily_single_operation_cli_forbidden_use_sequence_launcher")
        )
        return 2
    try:
        state_root = Path(os.environ.get("NEWS_GRASP_STATE_ROOT", str(_default_state_root())))
        repo_root = Path(os.environ.get("NEWS_GRASP_REPO_ROOT", str(Path.cwd())))
        issue_date = os.environ.get("NEWS_GRASP_ISSUE_DATE", "").strip() or _issue_date_default()
        scheduler_trigger_at = (
            os.environ.get("NEWS_GRASP_SCHEDULER_TRIGGER_AT", "").strip()
            or f"{issue_date}T06:00:00+09:00"
        )
        command = _canonical_argv(operation_id)
        capability_raw = os.environ.get("NEWS_GRASP_DAILY_ROUTE_CAPABILITY", "").strip()
        capability = (
            json.loads(capability_raw)
            if capability_raw
            else build_daily_route_capability(
                operation_id,
                runtime_generation=runtime.RUNTIME_SCHEMA_V2,
            )
        )
        if not isinstance(capability, Mapping):
            result = _red_result(operation_id, "daily_route_capability_invalid")
            runtime._emit_cli(result)
            return 2
        identity = resolve_daily_identity_context(repo_root=repo_root, issue_date=issue_date)
        if identity.get("ok") is not True:
            result = _red_result(operation_id, "daily_identity_preflight_red", identity=identity)
            runtime._emit_cli(result)
            return 2
        store = runtime.DirectRunStore(state_root)
        fencing_value: int | None = None
        fencing_raw = os.environ.get("NEWS_GRASP_FENCING_TOKEN", "").strip()
        if fencing_raw:
            try:
                fencing_value = int(fencing_raw)
            except ValueError:
                result = _red_result(operation_id, "fencing_token_invalid")
                runtime._emit_cli(result)
                return 2
        run_id = os.environ.get("NEWS_GRASP_RUN_ID", "").strip() or None
        writer_lease = os.environ.get("NEWS_GRASP_WRITER_LEASE", "").strip() or None
        # CLI subprocessには任意handlerを注入する入口を設けない。installed
        # producerが登録されていない自然canaryは明示Redとなり、認可だけで0を返さない。
        result = run_daily_operation(
            operation_id,
            command=command,
            store=store,
            run_id=run_id,
            writer_lease=writer_lease,
            cwd=repo_root,
            issue_date=issue_date,
            run_intent=runtime.RUN_INTENT,
            automation_id=runtime.AUTOMATION_ID,
            scheduler_trigger_at=scheduler_trigger_at,
            manifest_id=str(identity.get("manifest_id") or ""),
            manifest_reservation_id=str(identity.get("manifest_reservation_id") or ""),
            source_baseline=str(identity.get("source_baseline") or ""),
            runtime_generation=runtime.RUNTIME_SCHEMA_V2,
            remote_base_sha=str(identity.get("remote_base_sha") or ""),
            allowed_side_effect_ids=list(identity.get("allowed_side_effect_ids") or ()),
            route_capability=capability,
            fencing_token=fencing_value,
        )
    except (NewsGraspGateProfileError, ValueError, TypeError, OSError, RuntimeError) as exc:
        result = {
            "schemaVersion": DAILY_GATE_SCHEMA,
            "ok": False,
            "status": "blocked",
            "failures": [str(exc)],
        }
    runtime._emit_cli(result)
    return 0 if result.get("ok") is True and result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
