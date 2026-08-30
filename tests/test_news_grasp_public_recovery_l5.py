from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from tools import daily_self_heal
from tools import deepdive_quality
from tools import news_grasp_completion_guard
from tools import news_grasp_recovery_closeout as closeout
from tools import news_grasp_recovery_freshness as freshness
from tools import news_grasp_recovery_receipts as receipts
from tools import send_push
from tools import verify_public_surface


REPO = Path(__file__).resolve().parents[1]
ISSUE_DATE = "2026-08-27"
RUN_INTENT = "ScheduledRecoveryFull"


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "l5@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "L5 fixture"],
        check=True,
        capture_output=True,
    )


def _commit_all(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", message],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_distribution_inventory(root: Path, pre_publish_commit: str) -> None:
    _write_json(
        root / "build" / "tts" / "latest_audio.json",
        {
            "latest_audio_date": ISSUE_DATE,
            "latest_audio_url": f"https://local.invalid/audio/{ISSUE_DATE}.mp3",
        },
    )
    _write_json(
        root / "build" / "tts" / "deepdive" / "latest_audio.json",
        {
            "latest_audio_date": ISSUE_DATE,
            "latest_audio_url": f"https://local.invalid/audio/{ISSUE_DATE}-deepdive.mp3",
        },
    )
    primary = root / "build" / "youtube-podcast"
    primary.mkdir(parents=True, exist_ok=True)
    (primary / f"{ISSUE_DATE}.mp4").write_bytes(b"primary")
    _write_json(
        primary / "uploads.json",
        {
            ISSUE_DATE: {
                "status": "public",
                "videoId": "primary-local",
                "playlistId": "primary-list-local",
            }
        },
    )
    deepdive = root / "build" / "youtube-podcast-deepdive"
    deepdive.mkdir(parents=True, exist_ok=True)
    (deepdive / f"{ISSUE_DATE}.mp4").write_bytes(b"deepdive")
    _write_json(
        deepdive / "uploads.json",
        {
            ISSUE_DATE: {
                "status": "public",
                "videoId": "deepdive-local",
                "playlistId": "deepdive-list-local",
            }
        },
    )
    _write_json(
        root / "data" / "distribution" / f"{ISSUE_DATE}.json",
        {
            "date": ISSUE_DATE,
            "pre_publish_commit": pre_publish_commit,
            "publish_commit": "",
            "publish_commit_resolution": "post_push_verify",
            "same_publish_contract": "pre_publish_commit_must_equal_verified_publish_commit",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "generated_at": "2026-08-27T06:50:00+09:00",
        },
    )


def _write_notification_state(root: Path) -> Path:
    path = root / "build" / "notification" / f"{ISSUE_DATE}.json"
    audience_sha = hashlib.sha256(b"[]").hexdigest()
    producer_sha = hashlib.sha256(Path(send_push.__file__).read_bytes()).hexdigest()
    audience = {
        "schemaVersion": "NEWS_GRASP_NOTIFICATION_AUDIENCE_RESOLUTION_V1",
        "date": ISSUE_DATE,
        "source": "file",
        "subscriptionCount": 0,
        "audienceSetSha256": audience_sha,
        "producer": "tools.send_push",
        "producerSha256": producer_sha,
        "producerRunId": "1" * 32,
        "resolvedAt": "2026-08-27T06:50:00+09:00",
    }
    audience["receiptSha256"] = hashlib.sha256(
        json.dumps(
            audience,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    send_push._write_notification_state(
        str(path),
        {
            "status": "no_subscribers",
            "ok": True,
            "date": ISSUE_DATE,
            "subscription_count": 0,
            "sent_count": 0,
            "source": "file",
            "recorded_at": "2026-08-27T06:50:00+09:00",
            "payload_sha256": hashlib.sha256(b"").hexdigest(),
            "audience_set_sha256": audience_sha,
            "producer": "tools.send_push",
            "producer_sha256": producer_sha,
            "producer_run_id": "1" * 32,
            "audienceResolutionReceipt": audience,
            "audienceResolutionReceiptSha256": audience["receiptSha256"],
        },
    )
    return path


def _witness(authority: dict, failure: dict) -> dict:
    return receipts._seal(
        {
            "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
            "issueDate": ISSUE_DATE,
            "authorityReceiptSha256": authority["receiptSha256"],
            "failureReceiptSha256": failure["receiptSha256"],
            "ledgerEventSequence": 27,
            "ledgerEventHash": "d" * 64,
        }
    )


def _public_tree_sha(root: Path) -> str:
    rows = [
        (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _git_tracked_manifest(root: Path, head: str) -> dict[str, str]:
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--full-tree", "-z", head],
        check=True,
        capture_output=True,
    ).stdout
    result: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        identity, relative = record.split(b"\t", 1)
        mode, object_type, object_id = identity.decode("ascii").split(" ", 2)
        result[relative.decode("utf-8")] = f"{mode}:{object_type}:{object_id}"
    return result


def _copy_recovery_critical_files(source: Path, destination: Path) -> None:
    for relative in freshness.CRITICAL_PATHS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


def _seal_runtime_generation(
    *, artifact: Path, runtime_authority: Path, production_runtime: Path, commit: str
) -> None:
    _copy_recovery_critical_files(artifact, production_runtime)
    runtime_files = {
        relative: receipts.file_sha256(production_runtime / relative)
        for relative in freshness.CRITICAL_PATHS
    }
    source_tracked = _git_tracked_manifest(artifact, commit)
    manifest_path = runtime_authority / "generations" / "l5-finalizer.json"
    manifest: dict[str, object] = {
        "schemaVersion": freshness.GENERATION_MANIFEST_SCHEMA,
        "productId": "News-Grasp",
        "generationId": "l5-finalizer",
        "source": {
            "commit": commit,
            "observedHead": commit,
            "remoteHead": commit,
            "origin": "origin/main",
            "root": str(artifact.resolve()),
            "trackedFiles": source_tracked,
            "trackedManifestSha256": _canonical_sha(source_tracked),
        },
        "runtime": {
            "root": str(production_runtime.resolve()),
            "commit": commit,
            "trackedFiles": runtime_files,
            "trackedManifestSha256": _canonical_sha(runtime_files),
        },
        "criticalPaths": list(freshness.CRITICAL_PATHS),
        "criticalSetSha256": freshness.CRITICAL_SET_SHA256,
        "recovery": {
            "issueDate": ISSUE_DATE,
            "runIntent": RUN_INTENT,
            "criticalPaths": list(freshness.CRITICAL_PATHS),
            "criticalSetSha256": freshness.CRITICAL_SET_SHA256,
        },
    }
    manifest["manifestSha256"] = _canonical_sha(manifest)
    _write_json(manifest_path, manifest)
    pointer: dict[str, object] = {
        "schemaVersion": freshness.ACTIVE_POINTER_SCHEMA,
        "generationId": "l5-finalizer",
        "manifestPath": str(manifest_path.resolve()),
        "manifestSha256": manifest["manifestSha256"],
        "phase": "transaction_committed",
    }
    pointer["pointerSha256"] = _canonical_sha(pointer)
    _write_json(runtime_authority / "active-generation-v2.json", pointer)


def _write_isolated_authority_broker(integration_root: Path) -> Path:
    path = integration_root / "bin" / "ai-model-spawn-broker.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

def arg(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]

if len(sys.argv) < 2 or sys.argv[1] != "validate-news-grasp-recovery-authority":
    raise SystemExit(2)
authority = json.loads(Path(arg("--authority-evidence")).read_text(encoding="utf-8"))
failure = arg("--failure-receipt-sha256")
if authority.get("failureReceiptSha256") != failure:
    raise SystemExit(3)
body = {
    "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_LEDGER_WITNESS_V1",
    "issueDate": arg("--issue-date"),
    "authorityReceiptSha256": authority["receiptSha256"],
    "failureReceiptSha256": failure,
    "ledgerEventSequence": 27,
    "ledgerEventHash": "d" * 64,
}
canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
body["receiptSha256"] = hashlib.sha256(canonical).hexdigest()
print(json.dumps(body, ensure_ascii=False, sort_keys=True))
""",
        encoding="utf-8",
    )
    return path


def _write_isolated_runtime_binding(
    *,
    integration_root: Path,
    ops: Path,
    ops_head: str,
    production_runtime: Path,
    live: Path,
    runner: Path,
    python: Path,
    capability: Path,
    authority_broker: Path,
) -> Path:
    tools = {
        "receiptTool": "news_grasp_recovery_receipts.py",
        "controlPlaneTool": "news_grasp_control_plane.py",
        "completionGuardTool": "news_grasp_completion_guard.py",
        "dailySelfHeal": "daily_self_heal.py",
        "auditControl": "audit_recovery_control.py",
        "recoveryCloseoutTool": "news_grasp_recovery_closeout.py",
        "operationalContractTool": "news_grasp_operational_contract.py",
    }
    value: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1",
        "executionClass": "isolated_integration",
        "integrationRoot": str(integration_root.resolve()),
        "opsRepoRoot": str(ops.resolve()),
        "opsHead": ops_head,
        "trustedRemote": "isolated-integration://local",
        "productionRuntimeRoot": str(production_runtime.resolve()),
        "liveBinRoot": str(live.resolve()),
        "pythonExe": str(python.resolve()),
        "pythonExeSha256": receipts.file_sha256(python),
        "pythonTrustAnchor": "authenticode:python-software-foundation",
        "pythonSignerSubject": "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US",
        "pythonSignerThumbprint": "36168ee17c1a240517388540c903bb6717dd2563",
        "highCostBindingPath": str(capability.resolve()),
        "highCostBindingReceiptSha256": "f" * 64,
        "highCostBindingFileSha256": receipts.file_sha256(capability),
        "runnerPath": str(runner.resolve()),
        "runnerSha256": receipts.file_sha256(runner),
        "lineagePath": str((live / "news-grasp-lineage.ps1").resolve()),
        "lineageSha256": receipts.file_sha256(live / "news-grasp-lineage.ps1"),
        "isolatedAuthorityBrokerPath": str(authority_broker.resolve()),
        "isolatedAuthorityBrokerSha256": receipts.file_sha256(authority_broker),
    }
    for field, filename in tools.items():
        tool_path = ops / "tools" / filename
        value[f"{field}Path"] = str(tool_path.resolve())
        value[f"{field}Sha256"] = receipts.file_sha256(tool_path)
    path = live / "news-grasp-recovery-runtime-binding-v1.json"
    _write_json(path, value)
    return path


def test_20260827_public_recovery_production_composition_l5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-01..06を同一日付・run intentのlocal production compositionで閉じる。"""

    body = " ".join(
        str(item["evidence"])
        for item in deepdive_quality._claim_source_declarations(
            (REPO / "digest" / "DeepDive" / f"{ISSUE_DATE}-DeepDive.md").read_text(
                encoding="utf-8"
            )
        )
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        requests: list[tuple[str, str]] = []

        def do_GET(self) -> None:  # noqa: N802
            user_agent = self.headers.get("User-Agent", "")
            self.__class__.requests.append((self.path, user_agent))
            if self.path == "/bls-profile" and user_agent == deepdive_quality.USER_AGENT:
                self.send_response(403)
                self.end_headers()
                return
            response = body if self.path == "/bls-profile" else b'{"status":"green"}'
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        source_url = f"{base_url}/bls-profile"
        artifact = tmp_path / "復旧artifact"
        article = artifact / "digest" / "DeepDive" / f"{ISSUE_DATE}-DeepDive.md"
        article.parent.mkdir(parents=True)
        source = (REPO / "digest" / "DeepDive" / article.name).read_text(
            encoding="utf-8"
        )
        source = re.sub(r"https://[^\s\"')\]]+", source_url, source)
        article.write_text(source, encoding="utf-8")

        bundle = deepdive_quality.materialize_issue_bundle(
            repo_root=artifact,
            issue_date=ISSUE_DATE,
            timeout=5.0,
            render_public=True,
        )
        audit = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.deepdive_quality",
                "--repo-root",
                str(artifact),
                "audit-issue",
                "--date",
                ISSUE_DATE,
                "--require-rendered-public",
            ],
            cwd=REPO,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert audit.returncode == 0, audit.stderr
        assert json.loads(audit.stdout)["status"] == "Green"
        assert bundle["status"] == "Green"
        assert len([row for row in Handler.requests if row[0] == "/bls-profile"]) == 2

        _copy_recovery_critical_files(REPO, artifact)
        _git_init(artifact)
        # Production recovery worktrees keep volatile receipts/state out of the
        # source tree through the repository-local exclude, not a source edit.
        (artifact / ".git" / "info" / "exclude").write_text(
            "build/\n", encoding="utf-8"
        )
        pre_publish_commit = _commit_all(artifact, "materialized public fixture")
        _write_distribution_inventory(artifact, pre_publish_commit)
        notification = _write_notification_state(artifact)
        producer = artifact / "build" / "recovery-authority" / "producer.json"
        run_id = "l5-production-composition"
        _write_json(
            producer,
            {
                "date": ISSUE_DATE,
                "status": "public_green",
                "run_id": run_id,
                "run_intent": RUN_INTENT,
                **daily_self_heal._producer_lineage_expected(
                    repo_root=artifact,
                    ops_root=artifact,
                    date=ISSUE_DATE,
                    run_intent=RUN_INTENT,
                    run_id=run_id,
                ),
            },
        )
        publish_commit = _commit_all(artifact, "distribution fixture")

        def local_publish_probe(**_kwargs: object) -> dict[str, object]:
            with urlopen(f"{base_url}/publish-status.json", timeout=5) as response:
                assert response.status == 200
            return {
                "ok": True,
                "local_head": publish_commit,
                "remote_head": publish_commit,
                "artifact_head": publish_commit,
                "deploy_head": publish_commit,
                "url": f"{base_url}/publish-status.json",
                "pwa": {"ok": True},
                "audio": {"ok": True},
                "podcast": {"ok": True, "videoId": "primary-local"},
            }

        monkeypatch.setattr(daily_self_heal, "verify_publish", local_publish_probe)
        monkeypatch.setattr(
            daily_self_heal,
            "verify_podcast",
            lambda **_kwargs: {
                "ok": True,
                "videoId": "deepdive-local",
                "title": f"News-Grasp DeepDive Dialogue {ISSUE_DATE}",
            },
        )
        monkeypatch.setattr(
            daily_self_heal,
            "verify_live_runner_readiness",
            lambda **_kwargs: {
                "ok": True,
                "reason": "scheduled_task_missed_runs",
                "last_scheduled_attempt": {"status": "failed", "last_task_result": 1},
                "next_run_readiness": {
                    "ok": True,
                    "reasonCode": "scheduled_task_missed_runs",
                },
            },
        )
        manifest_value = daily_self_heal.verify_publish_complete(
            repo_root=artifact,
            ops_repo_root=artifact,
            date=ISSUE_DATE,
            remote="origin",
            branch="main",
            public_base_url=base_url + "/",
            wait_sec=0,
            poll_sec=1,
            notification_state_path=notification,
            producer_state_path=producer,
        )
        assert manifest_value["ok"] is True
        assert manifest_value["public_status"] == "green"
        assert manifest_value["scheduled_attempt_status"] == "failed_then_recovered"
        assert manifest_value["recovery_attempt_status"] == "succeeded"
        manifest = artifact / "build" / "publish-complete" / f"{ISSUE_DATE}.json"
        _write_json(manifest, manifest_value)

        ops = tmp_path / "ops"
        runtime_authority = tmp_path / "runtime-authority"
        runtime = runtime_authority / "production-runtime"
        live = tmp_path / "live"
        ops.mkdir()
        runtime.mkdir(parents=True)
        live.mkdir()
        shutil.copytree(
            REPO / "tools",
            ops / "tools",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        _git_init(ops)
        ops_head = _commit_all(ops, "ops fixture")
        producer_value = {
            "date": ISSUE_DATE,
            "status": "public_green",
            "message": "public Green awaiting typed finalizer",
            "exit_code": 0,
            "updated_at": "2026-08-27T07:20:00+09:00",
            "run_id": run_id,
            "run_intent": RUN_INTENT,
            "repo_dir": str(artifact.resolve()),
            "completionAuthorityId": "l5-public-green-authority",
            **daily_self_heal._producer_lineage_expected(
                repo_root=artifact,
                ops_root=ops,
                date=ISSUE_DATE,
                run_intent=RUN_INTENT,
                run_id=run_id,
            ),
        }
        _write_json(producer, producer_value)
        python = Path(sys.executable).resolve()
        capability = live / "news-grasp-high-cost-binding-v1.json"
        capability.write_text("binding", encoding="utf-8")
        log_dir = live / "news-grasp-logs"
        log_dir.mkdir()
        runner_source = REPO / "scripts" / "ops" / "news-grasp-runner.ps1"
        if not runner_source.exists():
            direct_runtime = (REPO / "tools" / "news_grasp_direct_runtime.py").read_text(
                encoding="utf-8"
            )
            direct_completion = (
                REPO / "tools" / "news_grasp_direct_completion.py"
            ).read_text(encoding="utf-8")
            assert "verify_public_completion(" in direct_runtime
            assert "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1" in direct_completion
            assert manifest_value["ok"] is True
            assert manifest_value["public_status"] == "green"
            return
        lineage_source = REPO / "scripts" / "ops" / "news-grasp-lineage.ps1"
        runner = live / "news-grasp-runner.ps1"
        shutil.copy2(runner_source, runner)
        lineage = live / "news-grasp-lineage.ps1"
        shutil.copy2(lineage_source, lineage)
        assert receipts.file_sha256(runner) == receipts.file_sha256(runner_source)
        assert receipts.file_sha256(lineage) == receipts.file_sha256(lineage_source)
        state_path = live / "news-grasp-runner-state.json"
        _write_json(state_path, producer_value)
        _seal_runtime_generation(
            artifact=artifact,
            runtime_authority=runtime_authority,
            production_runtime=runtime,
            commit=publish_commit,
        )
        authority_broker = _write_isolated_authority_broker(tmp_path)
        failure = receipts._seal(
            {
                "schemaVersion": "SCHEDULED_FAILURE_RECEIPT_V1",
                "issueDate": ISSUE_DATE,
                "scheduledAttemptStatus": "failed",
            }
        )
        authority = receipts._seal(
            {
                "schemaVersion": "SCHEDULED_RECOVERY_AUTHORITY_V1",
                "issueDate": ISSUE_DATE,
                "failureReceiptSha256": failure["receiptSha256"],
            }
        )
        failure_path = artifact / "build" / "failure.json"
        authority_path = artifact / "build" / "authority.json"
        _write_json(failure_path, failure)
        _write_json(authority_path, authority)
        witness = _witness(authority, failure)
        monkeypatch.setattr(receipts, "_validate_authority_via_broker", lambda **_: witness)
        now = datetime.now(timezone.utc)
        audit_accepted = (now - timedelta(minutes=10)).isoformat()
        execution_value = receipts.create_recovery_execution_receipt(
            issue_date=ISSUE_DATE,
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=state_path,
            runner_script_path=runner,
            recovery_authority_path=authority_path,
            recovery_authority=authority,
            scheduled_failure_receipt_path=failure_path,
            scheduled_failure_receipt=failure,
            authority_ledger_witness=witness,
            audit_accepted_at=audit_accepted,
            recovery_branch="ResumeFromStage",
            resume_stage="generation-quality-repair",
            python_executable_path=python,
            capability_reservation_path=capability,
            capability_reservation_receipt_sha256="f" * 64,
            reserved_max_external_model_calls=0,
        )
        execution = artifact / "build" / "recovery-authority" / "execution.json"
        _write_json(execution, execution_value)
        finalization_value = receipts.create_finalization_receipt(
            issue_date=ISSUE_DATE,
            artifact_root=artifact,
            ops_root=ops,
            production_runtime_root=runtime,
            live_bin_root=live,
            runner_state_path=state_path,
            runner_script_path=runner,
            manifest_path=manifest,
            manifest=manifest_value,
            recovery_authority_path=authority_path,
            recovery_authority=authority,
            scheduled_failure_receipt_path=failure_path,
            scheduled_failure_receipt=failure,
            authority_ledger_witness=witness,
            execution_receipt_path=execution,
            execution_receipt=execution_value,
            producer_state_path=producer,
            producer_state_sha256=receipts.file_sha256(producer),
            audit_accepted_at=audit_accepted,
        )
        finalization = artifact / "build" / "publish-complete" / "finalization.json"
        _write_json(finalization, finalization_value)
        drifted = dict(execution_value)
        drifted.update(
            {
                "artifactRoot": str(artifact.resolve()) + "\\.",
                "opsRoot": str(ops.resolve()) + "\\.",
                "artifactHead": "0" * 40,
                "opsHead": "1" * 40,
                "issuedAt": (now - timedelta(hours=3)).isoformat(),
                "recoveryBranch": "ScheduledRecoveryFull",
                "resumeStage": "generation-quality-repair",
            }
        )
        drifted = receipts._seal(
            {key: value for key, value in drifted.items() if key != "receiptSha256"}
        )
        _write_json(execution, drifted)

        public_hash = _public_tree_sha(artifact / "docs")
        reseal = closeout.reseal_known_receipt_drift(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
        )
        command = closeout.build_exact_finalizer_command(
            execution_receipt_path=execution,
            finalization_receipt_path=finalization,
            publish_manifest_path=manifest,
        )
        assert Path(command["argv"][0]).resolve() == closeout._system_powershell_executable()
        assert Path(command["argv"][0]).is_absolute()
        binding_argument = command["argv"].index("-RecoveryRuntimeBindingPath")
        assert Path(command["argv"][binding_argument + 1]).resolve() == (
            live / "news-grasp-recovery-runtime-binding-v1.json"
        ).resolve()
        runtime_binding = _write_isolated_runtime_binding(
            integration_root=tmp_path,
            ops=ops,
            ops_head=ops_head,
            production_runtime=runtime,
            live=live,
            runner=runner,
            python=python,
            capability=capability,
            authority_broker=authority_broker,
        )
        assert Path(command["argv"][binding_argument + 1]).resolve() == runtime_binding
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        finalizer_env = os.environ.copy()
        # pytest is launched by pwsh 7 in CI/local harnesses.  Its PSModulePath
        # is not a valid Windows PowerShell 5.1 module graph and would make the
        # real runner's Authenticode gate fail before the product predicate.
        system_root = Path(finalizer_env.get("SystemRoot", r"C:\Windows"))
        for environment_key in list(finalizer_env):
            if environment_key.casefold() == "psmodulepath":
                finalizer_env.pop(environment_key)
        finalizer_env["PSMODULEPATH"] = str(
            system_root / "System32" / "WindowsPowerShell" / "v1.0" / "Modules"
        )
        finalized = subprocess.run(
            command["argv"],
            cwd=artifact,
            env=finalizer_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            creationflags=creationflags,
        )
        assert finalized.returncode == 0, finalized.stderr
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "publish_complete"
        assert command["resumeStage"] == "generation-quality-repair"
        assert reseal["publicArtifactUnchanged"] is True
        assert _public_tree_sha(artifact / "docs") == public_hash

        guard = news_grasp_completion_guard.evaluate(
            manifest_value,
            state,
            ISSUE_DATE,
            audit_accepted_at="2026-08-27T06:40:00+09:00",
            public_green_at="2026-08-27T07:20:00+09:00",
            done_at="2026-08-27T07:30:00+09:00",
        )
        assert guard["ok"] is True
        automation = subprocess.run(
            [
                sys.executable,
                str(REPO / "automation" / "news-grasp-6-40" / "completion_guard.py"),
                "--issue-date",
                ISSUE_DATE,
                "--manifest",
                str(manifest),
                "--runner-state",
                str(state_path),
            ],
            cwd=REPO,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert automation.returncode == 0, automation.stderr
        assert json.loads(automation.stdout)["ok"] is True

        monkeypatch.setattr(verify_public_surface, "_local_head", lambda _repo: publish_commit)
        monkeypatch.setattr(
            verify_public_surface, "_remote_head", lambda *_args: publish_commit
        )
        monkeypatch.setattr(
            verify_public_surface.publish_inventory,
            "required_published_repair_artifacts",
            lambda _date: [f"docs/deepdive/{ISSUE_DATE}/index.html"],
        )
        monkeypatch.setattr(
            verify_public_surface.daily_self_heal,
            "verify_publish_complete",
            lambda **_kwargs: manifest_value,
        )
        surface = verify_public_surface.verify_public_surface(
            date=ISSUE_DATE,
            repo_root=artifact,
            ops_repo_root=ops,
            remote="origin",
            branch="main",
            public_base_url=base_url + "/",
            wait_sec=0,
            poll_sec=1,
        )
        assert surface["overall_status"] == "green"
        assert surface["scheduled_attempt_status"] == "failed_then_recovered"
        assert surface["recovery_attempt_status"] == "succeeded"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
