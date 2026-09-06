"""Release gate専用のdirect-mainline NoPublish実行器。

Daily launcherから本moduleをimportしない。隔離済みworktreeと隔離stateだけを使い、
content producer・派生物・六operationのtransactionを実行する一方、外部providerは
一切呼ばない。保護済みissueは再生成せず、翌日のsimulation issueへ写像する。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(_PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PRODUCT_ROOT))
_TRUSTED_SITE_PACKAGES = Path(sys.executable).resolve().parent / "Lib" / "site-packages"
if _TRUSTED_SITE_PACKAGES.is_dir() and str(_TRUSTED_SITE_PACKAGES) not in sys.path:
    # -Sでstartup hook/user siteを無効化したまま、固定Python配下の依存だけを許可する。
    sys.path.append(str(_TRUSTED_SITE_PACKAGES))

SCHEMA = "NEWS_GRASP_RELEASE_NOPUBLISH_RECEIPT_V1"
STATE_SCHEMA = "NEWS_GRASP_RELEASE_NOPUBLISH_STATE_V1"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
PROTECTED_RELEASE = "2026-09-02"
daily: Any = None
runtime: Any = None
_RELEASE_CAPABILITY_MARKER = object()


class _ReleaseCapability:
    """live high-cost claim検証後にだけ発行するprocess-local capability。"""

    __slots__ = ("witness", "_marker")

    def __init__(self, witness: Mapping[str, Any], marker: object) -> None:
        if marker is not _RELEASE_CAPABILITY_MARKER:
            raise RuntimeError("nopublish_high_cost_capability_invalid")
        self.witness = dict(witness)
        self._marker = marker


def _load_release_runtime_modules() -> tuple[Any, Any]:
    """high-cost claim検証後にだけproduct runtimeをimportする。"""

    global daily, runtime
    if daily is None:
        daily = importlib.import_module("tools.news_grasp_daily_gate")
    if runtime is None:
        runtime = importlib.import_module("tools.news_grasp_direct_runtime")
    return daily, runtime


def _load_exact_module(path: Path, *, prefix: str) -> Any:
    candidate = path.resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError("nopublish_authority_consumer_invalid")
    module_name = f"{prefix}_{hashlib.sha256(str(candidate).encode()).hexdigest()[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, candidate)
    if spec is None or spec.loader is None:
        raise RuntimeError("nopublish_authority_consumer_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _require_high_cost_claim(root: Path) -> _ReleaseCapability:
    """current PowerShell parentのlive claimをledgerまでread-onlyで再検証する。"""

    names = {
        "admission": "NEWS_GRASP_E2E_ADMISSION_PATH",
        "arguments": "NEWS_GRASP_E2E_ARGUMENTS_PATH",
        "claim": "NEWS_GRASP_E2E_CLAIM_PATH",
        "reservation": "NEWS_GRASP_E2E_RESERVATION_PATH",
        "parent": "NEWS_GRASP_E2E_PARENT_AUTHORITY_PATH",
    }
    paths: dict[str, Path] = {}
    for key, environment_name in names.items():
        raw = os.environ.get(environment_name, "")
        if not raw:
            raise RuntimeError("nopublish_high_cost_claim_missing")
        candidate = Path(raw).resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_file() or not candidate.is_relative_to(root):
            raise RuntimeError("nopublish_high_cost_claim_invalid")
        paths[key] = candidate
    bridge = _load_exact_module(
        root / "tools" / "e2e_final_admission_bridge.py",
        prefix="news_grasp_nopublish_claim_bridge",
    )
    child_identity = bridge._query_process_identity(os.getpid())
    parent_pid = int(child_identity.get("parentPid") or 0)
    if parent_pid <= 0:
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    arguments = json.loads(paths["arguments"].read_text(encoding="utf-8-sig"))
    claim = json.loads(paths["claim"].read_text(encoding="utf-8-sig"))
    if not isinstance(arguments, list) or not isinstance(claim, dict):
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    witness = bridge.validate_runner_claim(
        admission_path=paths["admission"],
        ledger_path=bridge.default_attempt_ledger_path(),
        runner_arguments=arguments,
        parent_authority_path=paths["parent"],
        runner_arguments_path=paths["arguments"],
        reservation_receipt=paths["reservation"],
        claim_receipt=paths["claim"],
        actual_runner_executable_path=Path(str(claim.get("runnerExecutablePath") or "")),
        actual_authority_python_executable_path=Path(sys.executable),
        expected_owner_pid=parent_pid,
    )
    if not isinstance(witness, dict) or witness.get("ownerProcessIdentity") != claim.get("ownerProcessIdentity"):
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    if not isinstance(witness.get("claimId"), str) or not witness["claimId"]:
        raise RuntimeError("nopublish_high_cost_claim_invalid")
    return _ReleaseCapability({**witness, "moduleProcessIdentity": child_identity}, _RELEASE_CAPABILITY_MARKER)


def _await_owner_start_confirmation(root: Path, capability: _ReleaseCapability) -> None:
    """本体の生存中にownerのOS照合と永続開始確認を待つ。"""

    from tools.news_grasp_preentry_journal import environment_journal

    context = environment_journal()
    if context is None:
        raise RuntimeError("NEWS_GRASP_PREENTRY_CONTEXT_MISSING")
    journal, issue_date, session_id = context
    detail = {
        "processIdentity": capability.witness["moduleProcessIdentity"],
        "modulePath": str(root / "tools" / "news_grasp_release_nopublish.py"),
    }
    journal.append(issue_date, session_id, "module_entered", detail)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for event in journal.events(issue_date, session_id):
            if event["phase"] == "module_started":
                if event["detail"] != detail:
                    raise RuntimeError("NEWS_GRASP_PREENTRY_START_IDENTITY_DRIFT")
                return
        time.sleep(0.05)
    raise RuntimeError("NEWS_GRASP_PREENTRY_OWNER_CONFIRMATION_TIMEOUT")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(dict(value)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _git(root: Path, *args: str) -> str:
    git_exe = Path(r"C:\Program Files\Git\cmd\git.exe")
    if not git_exe.is_file():
        raise RuntimeError("nopublish_git_executable_missing")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "Never"
    completed = subprocess.run(
        [str(git_exe), *args],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"nopublish_git_failed:{args[0]}:{completed.returncode}")
    return completed.stdout.strip().casefold()


def simulation_issue_date(source_issue_date: str) -> str:
    parsed = date.fromisoformat(source_issue_date)
    candidate = parsed + timedelta(days=1) if source_issue_date == PROTECTED_RELEASE else parsed
    return candidate.isoformat()


def _producer_receipt(
    schema: str,
    operation_id: str,
    *,
    values: Mapping[str, Any],
    failures: Sequence[str] = (),
) -> dict[str, Any]:
    failure_rows = [str(item) for item in failures if str(item)]
    body: dict[str, Any] = {
        "schemaVersion": schema,
        "ok": not failure_rows,
        "status": "verified" if not failure_rows else "red",
        "operation_id": operation_id,
        "producer_id": f"tools.news_grasp_release_nopublish.{operation_id}",
        "observed_at": datetime.now(JST).isoformat(),
        "failures": failure_rows,
        **dict(values),
    }
    body["output_hash"] = _sha(body)
    return body


def _scoped_release_receipt(**context: Any) -> dict[str, Any]:
    return _producer_receipt(
        "NEWS_GRASP_NOPUBLISH_SCOPED_RELEASE_RECEIPT_V1",
        "scoped_contract_unit",
        values={
            "mode": "release_promotion_and_isolation_reuse",
            "source_head": str(context.get("source_baseline") or ""),
            "test_process_count": 0,
            "isolation_receipt": str(context.get("isolation_receipt") or ""),
        },
    )


def _external_nopublish_receipt(**_context: Any) -> dict[str, Any]:
    return _producer_receipt(
        "NEWS_GRASP_NOPUBLISH_EXTERNAL_RECEIPT_V1",
        "external_publication",
        values={
            "no_publish": True,
            "external_effect_count": 0,
            "adapter_call_count": 0,
            "duplicate_send_count": 0,
            "duplicate_upload_count": 0,
        },
    )


def _materialize_local_bundle(
    *,
    repo_root: Path,
    issue_date: str,
    run_id: str,
    content_receipt: Mapping[str, Any],
    **context: Any,
) -> dict[str, Any]:
    artifact_hashes = {
        **dict(content_receipt.get("artifact_hashes") or {}),
        **dict(content_receipt.get("derived_artifact_hashes") or {}),
    }
    failures: list[str] = []
    for relative, expected in sorted(artifact_hashes.items()):
        path = (repo_root / str(relative)).resolve(strict=False)
        try:
            path.relative_to(repo_root)
        except ValueError:
            failures.append(f"artifact_outside_repo:{relative}")
            continue
        if not path.is_file() or path.is_symlink():
            failures.append(f"artifact_missing:{relative}")
            continue
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
        if observed != str(expected):
            failures.append(f"artifact_hash_mismatch:{relative}")
    required_home = repo_root / "docs" / "index.html"
    if not required_home.is_file() or required_home.is_symlink():
        failures.append("manifest_home_missing")
    elif "docs/index.html" not in artifact_hashes:
        artifact_hashes["docs/index.html"] = hashlib.sha256(required_home.read_bytes()).hexdigest()
    exact_write_set = sorted(artifact_hashes)
    bundle_id = _sha(
        {
            "issue_date": issue_date,
            "run_id": run_id,
            "exact_write_set": exact_write_set,
            "artifact_hashes": artifact_hashes,
        }
    )
    publish_seal: Mapping[str, Any] = {}
    store = context.get("store")
    if not failures and isinstance(store, runtime.DirectRunStore):
        fresh = runtime.inspect_run(store, run_id=run_id)
        publish_seal = runtime.seal_publish(
            store,
            run_id=run_id,
            writer_lease=str(context.get("writer_lease") or ""),
            release_commit_sha=str(context.get("source_baseline") or fresh.get("source_baseline") or ""),
            exact_write_set=exact_write_set,
            file_hashes=artifact_hashes,
            manifest_id=str(fresh.get("manifest_id") or ""),
            bundle_id=bundle_id,
            external_operation_ids=(),
            external_input_hashes={},
            fencing_token=int(context.get("fencing_token") or 0),
        )
    return {
        "schemaVersion": "NEWS_GRASP_NOPUBLISH_LOCAL_BUNDLE_V1",
        "ok": not failures,
        "status": "sealed" if not failures else "red",
        "bundle_id": bundle_id,
        "issue_date": issue_date,
        "run_id": run_id,
        "exact_write_set": exact_write_set,
        "artifact_hashes": artifact_hashes,
        "external_effect_count": 0,
        "publish_seal": dict(publish_seal),
        "failures": failures,
    }


def _local_consumer_receipt(**context: Any) -> dict[str, Any]:
    store = context.get("store")
    run_id = str(context.get("run_id") or "")
    if not isinstance(store, runtime.DirectRunStore) or not run_id:
        raise RuntimeError("nopublish_consumer_runtime_binding_missing")
    integration = runtime.get_daily_operation_receipt(
        store,
        run_id=run_id,
        operation_id="current_issue_integration",
    )
    external = runtime.get_daily_operation_receipt(
        store,
        run_id=run_id,
        operation_id="external_publication",
    )
    if not isinstance(integration, Mapping) or not isinstance(external, Mapping):
        raise RuntimeError("nopublish_consumer_prior_receipt_missing")
    producer = integration.get("producer_receipt")
    producer = producer if isinstance(producer, Mapping) else {}
    content = producer.get("content_generation")
    content = content if isinstance(content, Mapping) else {}
    bundle = producer.get("release_bundle")
    bundle = bundle if isinstance(bundle, Mapping) else {}
    external_producer = external.get("producer_receipt")
    external_producer = external_producer if isinstance(external_producer, Mapping) else {}
    failures: list[str] = []
    if content.get("ok") is not True:
        failures.append("nopublish_content_receipt_red")
    if bundle.get("ok") is not True:
        failures.append("nopublish_bundle_receipt_red")
    if int(external_producer.get("external_effect_count", -1)) != 0:
        failures.append("nopublish_external_effect_detected")
    fresh = runtime.inspect_run(store, run_id=run_id)
    observed_at = datetime.now(JST).isoformat()
    nonce = uuid.uuid4().hex
    binding = {
        "runId": run_id,
        "issueDate": str(context.get("issue_date") or ""),
        "runIntent": str(context.get("run_intent") or runtime.RUN_INTENT),
        "generation": fresh.get("generation"),
        "manifestId": str(fresh.get("manifest_id") or ""),
        "fencingBindingHash": runtime.fencing_binding_hash(
            run_id=run_id,
            generation=int(fresh.get("generation") or 0),
            writer_lease=str(context.get("writer_lease") or ""),
            fencing_token=int(context.get("fencing_token") or 0),
        ),
        "updatedAt": str(fresh.get("updated_at") or ""),
        "observedAt": observed_at,
        "observationNonce": nonce,
    }
    observation = {
        "ok": not failures,
        "status": "verified" if not failures else "red",
        "observationToken": nonce,
        "observedAt": observed_at,
        "mode": "consumer_owned_local_nopublish",
        "bundleId": str(bundle.get("bundle_id") or ""),
        "artifactCount": len(bundle.get("artifact_hashes") or {}),
        "externalEffectCount": 0,
        "failures": failures,
    }
    return _producer_receipt(
        runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
        "consumer_public_verification",
        values={
            "observation": observation,
            "observation_token": nonce,
            "external_operation_id": "release-nopublish-local-observation",
            "freshnessBinding": binding,
        },
        failures=failures,
    )


def _run_release_nopublish_core(
    *,
    repo_root: Path,
    source_issue_date: str,
    state_root: Path,
    isolation_receipt: Path,
) -> dict[str, Any]:
    _load_release_runtime_modules()
    root = repo_root.resolve(strict=True)
    isolated_state = state_root.resolve(strict=False)
    if isolated_state == root or not isolated_state.is_relative_to(root):
        raise ValueError("nopublish_state_root_outside_isolated_repo")
    resolved_isolation_receipt = isolation_receipt.resolve(strict=True)
    if (
        not resolved_isolation_receipt.is_file()
        or isolation_receipt.is_symlink()
        or not resolved_isolation_receipt.is_relative_to(root)
    ):
        raise ValueError("nopublish_isolation_receipt_missing")
    simulation_date = simulation_issue_date(source_issue_date)
    source_head = _git(root, "rev-parse", "HEAD")
    manifest_id = _sha(
        {
            "source_head": source_head,
            "source_issue_date": source_issue_date,
            "simulation_issue_date": simulation_date,
            "isolation_receipt_sha256": hashlib.sha256(resolved_isolation_receipt.read_bytes()).hexdigest(),
        }
    )
    store = runtime.DirectRunStore(
        isolated_state,
        test_only_allow_semantic_verifier=True,
    )
    from tools import news_grasp_daily_content as content

    handlers = {
        "scoped_contract_unit": (
            "tools.news_grasp_release_nopublish.scoped_contract_unit",
            _scoped_release_receipt,
        ),
        "external_publication": (
            "tools.news_grasp_release_nopublish.external_publication",
            _external_nopublish_receipt,
        ),
        "consumer_public_verification": (
            "tools.news_grasp_release_nopublish.consumer_public_verification",
            _local_consumer_receipt,
        ),
    }
    receipts = daily.run_daily_sequence(
        handlers=handlers,
        store=store,
        cwd=root,
        issue_date=simulation_date,
        run_intent="release_nopublish",
        automation_id="news-grasp-release-gate",
        scheduler_trigger_at=datetime.now(JST).isoformat(),
        manifest_id=manifest_id,
        source_baseline=source_head,
        runtime_generation=f"release-nopublish:{source_head}",
        remote_base_sha=source_head,
        allowed_side_effect_ids=(),
        context={
            "repo_root": root,
            "source_baseline": source_head,
            "isolation_receipt": str(resolved_isolation_receipt),
            "content_candidate_provider": content._default_candidate_provider,
            "content_model_runner": content._default_model_runner,
            "content_derived_builder": content._default_derived_builder,
            "content_release_materializer": _materialize_local_bundle,
        },
    )
    final = receipts[-1] if receipts else {}
    external_effect_count = 0
    result = {
        "schemaVersion": SCHEMA,
        "ok": (
            len(receipts) == len(daily.DAILY_OPERATIONS)
            and final.get("ok") is True
            and final.get("status") == "completed"
        ),
        "status": "publish_dry_run_ok" if final.get("ok") is True and final.get("status") == "completed" else "red",
        "source_issue_date": source_issue_date,
        "simulation_issue_date": simulation_date,
        "source_head": source_head,
        "run_id": str((receipts[0] if receipts else {}).get("run_id") or ""),
        "operation_count": len(receipts),
        "operation_ids": [str(row.get("operation_id") or "") for row in receipts],
        "externalEffectCount": external_effect_count,
        "duplicateSendCount": 0,
        "duplicateUploadCount": 0,
        "failures": list(final.get("failures") or ()),
        "receipts": receipts,
    }
    result["receiptSha256"] = _sha(result)
    return result


def run_release_nopublish(
    *,
    repo_root: Path,
    source_issue_date: str,
    state_root: Path,
    isolation_receipt: Path,
    capability: _ReleaseCapability | None = None,
) -> dict[str, Any]:
    """検証済みlive claim capabilityを必須にする唯一の運用入口。"""

    if (
        not isinstance(capability, _ReleaseCapability)
        or capability._marker is not _RELEASE_CAPABILITY_MARKER
        or not isinstance(capability.witness.get("claimId"), str)
        or not capability.witness["claimId"]
    ):
        raise RuntimeError("nopublish_high_cost_capability_missing")
    return _run_release_nopublish_core(
        repo_root=repo_root,
        source_issue_date=source_issue_date,
        state_root=state_root,
        isolation_receipt=isolation_receipt,
    )


def _main(argv: Sequence[str] | None = None) -> int:
    from tools.news_grasp_preentry_journal import environment_journal
    context = environment_journal()
    if context is not None:
        journal, issue_date, session_id = context
        journal.append(issue_date, session_id, "module_loaded", {
            "pid": os.getpid(), "parentPid": os.getppid(), "modulePath": str(Path(__file__).resolve()),
        })
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--source-issue-date", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--receipt-path", type=Path, required=True)
    parser.add_argument("--isolation-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    state_file: Path | None = None
    receipt_path: Path | None = None
    claim_validated = False
    try:
        root = args.repo_root.resolve(strict=True)
        state_file = args.state_file.resolve(strict=False)
        receipt_path = args.receipt_path.resolve(strict=False)
        for candidate, code in (
            (state_file, "nopublish_state_file_outside_isolated_repo"),
            (receipt_path, "nopublish_receipt_outside_isolated_repo"),
        ):
            if candidate == root or not candidate.is_relative_to(root):
                raise ValueError(code)
        capability = _require_high_cost_claim(root)
        claim_validated = True
        _await_owner_start_confirmation(root, capability)
        result = run_release_nopublish(
            repo_root=root,
            source_issue_date=args.source_issue_date,
            state_root=args.state_root,
            isolation_receipt=args.isolation_receipt,
            capability=capability,
        )
        result["highCostClaimId"] = str(capability.witness.get("claimId") or "")
    except Exception as exc:  # noqa: BLE001 - machine boundary is typed Red.
        result = {
            "schemaVersion": SCHEMA,
            "ok": False,
            "status": "red",
            "externalEffectCount": 0,
            "failures": [f"release_nopublish_error:{type(exc).__name__}:{exc}"],
        }
    state = {
        "schemaVersion": STATE_SCHEMA,
        "status": "publish_dry_run_ok" if result.get("ok") is True else "release_nopublish_red",
        "exit_code": 0 if result.get("ok") is True else 1,
        "externalEffectCount": int(result.get("externalEffectCount") or 0),
        "e2eFinalAdmissionPath": os.environ.get("NEWS_GRASP_E2E_ADMISSION_PATH", ""),
        "e2eFinalRunnerArgumentsPath": os.environ.get("NEWS_GRASP_E2E_ARGUMENTS_PATH", ""),
        "receiptPath": str(receipt_path or ""),
    }
    if claim_validated and state_file is not None and receipt_path is not None:
        _atomic_json(receipt_path, result)
        _atomic_json(state_file, state)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return int(state["exit_code"])


if __name__ == "__main__":
    raise SystemExit(_main())
