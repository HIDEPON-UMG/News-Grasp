from pathlib import Path

import hashlib
import importlib.util
import io
import json
import os
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
SKILL = ROOT / "automation" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"
SPEC = ROOT / "docs" / "spec.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _load_owner():
    owner_path = ROOT / "tools" / "news_grasp_nopublish_owner.py"
    spec = importlib.util.spec_from_file_location("news_grasp_nopublish_owner_test", owner_path)
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = owner
    spec.loader.exec_module(owner)
    return owner


def test_nopublish_binds_isolated_execution_to_clean_candidate_not_production() -> None:
    wrapper = _read(WRAPPER)

    assert "$sourceRepoPath" in wrapper
    assert "$sourceRepoCommit" in wrapper
    assert "$executionRepoCommit -cne $sourceRepoCommit" in wrapper
    assert "source candidate generation is dirty" in wrapper
    assert "'--source-repo' $sourceRepoPath" in wrapper
    assert "execution generation is not the clean active runtime generation" not in wrapper
    assert "$executionRepoCommit -cne $runtimeRepoCommit" not in wrapper


def test_nopublish_direct_entry_has_no_installed_runtime_or_remote_reflection_gate() -> None:
    wrapper = _read(WRAPPER)

    assert "news-grasp-stable-task-authority-v1.json" not in wrapper
    assert "news-grasp-runtime-root-v1.json" not in wrapper
    assert "ReleaseReflectionReceiptPath" not in wrapper
    assert "& $installedTaskPythonPath @installedLauncherArguments" not in wrapper
    assert "'-B' $nopublishOwnerPath" in wrapper
    assert "'--runner-arguments' $runnerArgumentsPath" in wrapper
    assert "Join-Path $repoPath 'tools\\news_grasp_p08_evidence.py'" in wrapper


def test_prepromotion_order_is_explicit_in_product_contracts() -> None:
    combined = _read(SKILL) + "\n" + _read(SPEC)

    assert "pre-promotion candidate" in combined
    assert "NoPublish Greenまでproduction runtimeへ切り替えない" in combined


def test_candidate_owner_rejects_a_different_python_identity(tmp_path: Path) -> None:
    owner = _load_owner()
    different_python = tmp_path / "python.exe"
    different_python.write_bytes(b"not-the-current-python")

    with pytest.raises(
        RuntimeError,
        match="NEWS_GRASP_NOPUBLISH_OWNER_PYTHON_IDENTITY_DRIFT",
    ):
        owner.run_owned_nopublish(
            repo_root=tmp_path,
            python_executable=different_python,
            powershell_executable=different_python,
            runner_arguments_path=different_python,
            policy_path=different_python,
            attempt=1,
            admission_path=different_python,
            state_path=different_python,
            claim_path=different_python,
            launch_evidence_path=different_python,
        )


def test_wrapper_fixes_signed_system_powershell_and_head_bound_runtime() -> None:
    wrapper = _read(WRAPPER)

    assert "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" in wrapper
    assert "Get-Command $PowerShellExe" not in wrapper
    assert "Get-AuthenticodeSignature -FilePath $powerShellCanonicalPath" in wrapper
    assert "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US" in wrapper
    assert "function Assert-HeadBlobMatch" in wrapper
    for relative_path in (
        "scripts/ops/invoke-scheduled-equivalent-nopublish.ps1",
        "scripts/ops/news-grasp-release-nopublish.ps1",
        "scripts/ops/run_codex_with_timeout.ps1",
        "tools/e2e_final_admission_bridge.py",
        "tools/news_grasp_nopublish_owner.py",
        "tools/news_grasp_owned_process.py",
        "tools/news_grasp_p08_evidence.py",
        "tools/news_grasp_release_nopublish.py",
    ):
        assert relative_path in wrapper
    assert "--expected-owner-sha256" in wrapper
    assert "--expected-bridge-sha256" in wrapper
    assert "--expected-owned-process-sha256" in wrapper


def test_wrapper_uses_bounded_reads_for_reclaimed_authority_and_final_state() -> None:
    wrapper = _read(WRAPPER)

    assert "Read-BoundedJsonFile -Path $candidate -MaxBytes 65536" in wrapper
    assert "Read-BoundedJsonFile -Path $statePath -MaxBytes 65536" in wrapper
    assert "Get-Content -LiteralPath $statePath -Raw" not in wrapper
    assert "$ownerOutput = (& $pythonCanonicalPath" not in wrapper


def test_candidate_owner_rejects_outside_and_hardlinked_authority_files(tmp_path: Path) -> None:
    owner = _load_owner()
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID"):
        owner._canonical_repo_file(outside, root=root, max_bytes=1024)

    original = root / "original.json"
    linked = root / "linked.json"
    original.write_text("{}", encoding="utf-8")
    os.link(original, linked)
    with pytest.raises(RuntimeError, match="NEWS_GRASP_NOPUBLISH_OWNER_PATH_INVALID"):
        owner._canonical_repo_file(linked, root=root, max_bytes=1024, require_unique=True)


def test_candidate_owner_reads_one_bounded_snapshot(tmp_path: Path) -> None:
    owner = _load_owner()
    root = tmp_path / "repo"
    root.mkdir()
    value_path = root / "value.json"
    value_path.write_text(json.dumps({"ok": True}), encoding="utf-8")

    value, raw = owner._read_json_snapshot(value_path, root=root, max_bytes=1024)
    assert value == {"ok": True}
    assert raw == value_path.read_bytes()

    value_path.write_bytes(b"{" + (b" " * 1024) + b"}")
    with pytest.raises(RuntimeError, match="NEWS_GRASP_NOPUBLISH_OWNER_INPUT_INVALID"):
        owner._read_json_snapshot(value_path, root=root, max_bytes=1024)


@pytest.mark.skipif(os.name != "nt", reason="Windows path/handle stat contract")
def test_candidate_owner_hashes_system_executable_from_stable_handle() -> None:
    owner = _load_owner()
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )

    assert owner._file_sha256(powershell) == hashlib.sha256(
        powershell.read_bytes()
    ).hexdigest()


def test_candidate_owner_rebinds_claim_to_current_admission_and_arguments() -> None:
    owner = _read(ROOT / "tools" / "news_grasp_nopublish_owner.py")

    assert 'claim_value.get("admissionSha256") != hashlib.sha256(admission_bytes).hexdigest()' in owner
    assert 'claim_value.get("runnerArgumentsSha256") != hashlib.sha256(arguments_bytes).hexdigest()' in owner
    assert 'observed["imageSha256"] != powershell_sha256' in owner


def test_runtime_hashes_are_computed_from_locked_handles() -> None:
    wrapper = _read(WRAPPER)
    owner = _read(ROOT / "tools" / "news_grasp_nopublish_owner.py")

    assert "function Get-StableFileBinding" in wrapper
    assert "[System.IO.FileShare]::Read" in wrapper
    head_binding = wrapper.split("function Assert-HeadBlobMatch", 1)[1].split(
        "function Get-CanonicalFutureDirectory", 1
    )[0]
    assert "Get-StableFileBinding" in head_binding
    assert "hash-object" not in head_binding
    assert "expected_identity" in owner
    assert "_file_identity(before) != expected_identity" in owner


@pytest.mark.skipif(os.name != "nt", reason="Windows owned process contract")
def test_candidate_owner_captures_bounded_failure_output_from_owned_powershell(
    tmp_path: Path,
) -> None:
    owner = _load_owner()
    owned_path = ROOT / "tools" / "news_grasp_owned_process.py"
    spec = importlib.util.spec_from_file_location(
        "news_grasp_owned_process_capture_test", owned_path
    )
    assert spec is not None and spec.loader is not None
    owned = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = owned
    spec.loader.exec_module(owned)
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    process = owned.spawn_owned(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::Out.Write(('x' * 5000)); "
            "[Console]::Error.WriteLine('NEWS_GRASP_SYNTHETIC_CLAIM_FAILURE'); "
            "exit 17",
        ],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
    )
    try:
        result = owner._wait_for_owned_process(
            process,
            timeout_seconds=10,
            max_output_bytes=4096,
        )
    finally:
        process.close()

    assert result["exitCode"] == 17
    assert result["stdoutTruncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 4096
    assert "NEWS_GRASP_SYNTHETIC_CLAIM_FAILURE" in result["stderr"]

    runner = tmp_path / "runner.ps1"
    runner.write_text("Write-Output 'fixture'\n", encoding="utf-8")
    evidence_path = tmp_path / "child-failure.json"

    class FixtureBridge:
        @staticmethod
        def _write_exclusive(path: Path, value: dict[str, object]) -> None:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")

    owner._write_child_failure_evidence(
        bridge=FixtureBridge,
        output_path=evidence_path,
        root=tmp_path,
        admission={"issueDate": "2026-09-05"},
        process_identity={"pid": process.pid},
        powershell_path=powershell,
        powershell_sha256=hashlib.sha256(powershell.read_bytes()).hexdigest(),
        runner_path=runner,
        result=result,
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schemaVersion"] == "NEWS_GRASP_NOPUBLISH_CHILD_FAILURE_EVIDENCE_V1"
    assert evidence["childReasonCode"] == "NEWS_GRASP_SYNTHETIC_CLAIM_FAILURE"
    assert evidence["childExitCode"] == 17
    assert evidence["stdoutTruncated"] is True
    assert evidence_path.stat().st_size < 65536
    owner._write_child_failure_evidence(
        bridge=FixtureBridge,
        output_path=evidence_path,
        root=tmp_path,
        admission={"issueDate": "2026-09-05"},
        process_identity={"pid": process.pid},
        powershell_path=powershell,
        powershell_sha256=hashlib.sha256(powershell.read_bytes()).hexdigest(),
        runner_path=runner,
        result=result,
    )

    tampered = dict(evidence)
    tampered["processId"] = process.pid + 1
    evidence_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="NEWS_GRASP_NOPUBLISH_CHILD_FAILURE_EVIDENCE_DRIFT",
    ):
        owner._write_child_failure_evidence(
            bridge=FixtureBridge,
            output_path=evidence_path,
            root=tmp_path,
            admission={"issueDate": "2026-09-05"},
            process_identity={"pid": process.pid},
            powershell_path=powershell,
            powershell_sha256=hashlib.sha256(powershell.read_bytes()).hexdigest(),
            runner_path=runner,
            result=result,
        )

    mismatched = dict(evidence)
    mismatched["schemaVersion"] = "NEWS_GRASP_RUNNER_LAUNCH_EVIDENCE_V1"
    mismatched["status"] = "child_failed_before_terminal"
    evidence_path.write_text(
        json.dumps(mismatched, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        RuntimeError,
        match="NEWS_GRASP_NOPUBLISH_CHILD_FAILURE_EVIDENCE_DRIFT",
    ):
        owner._write_child_failure_evidence(
            bridge=FixtureBridge,
            output_path=evidence_path,
            root=tmp_path,
            admission={"issueDate": "2026-09-05"},
            process_identity={"pid": process.pid},
            powershell_path=powershell,
            powershell_sha256=hashlib.sha256(powershell.read_bytes()).hexdigest(),
            runner_path=runner,
            result=result,
        )

    timeout_path = tmp_path / "child-timeout.json"
    timeout_result = dict(result)
    timeout_result["timedOut"] = True
    owner._write_child_failure_evidence(
        bridge=FixtureBridge,
        output_path=timeout_path,
        root=tmp_path,
        admission={"issueDate": "2026-09-05"},
        process_identity={"pid": process.pid},
        powershell_path=powershell,
        powershell_sha256=hashlib.sha256(powershell.read_bytes()).hexdigest(),
        runner_path=runner,
        result=timeout_result,
    )
    timeout_evidence = json.loads(timeout_path.read_text(encoding="utf-8"))
    assert timeout_evidence["reasonCode"] == "NEWS_GRASP_RELEASE_NOPUBLISH_TIMEOUT"


def test_candidate_owner_marks_stream_read_failure_instead_of_losing_evidence() -> None:
    owner = _load_owner()

    class BrokenStream:
        def read(self, _size: int) -> bytes:
            raise OSError("synthetic read failure")

    class FixtureProcess:
        stdout = BrokenStream()
        stderr = io.BytesIO(b"NEWS_GRASP_SYNTHETIC_CHILD_FAILURE\n")

        def wait(self, timeout: int | float) -> int:
            assert timeout == 1
            return 17

        def close_job(self) -> None:
            return None

    result = owner._wait_for_owned_process(
        FixtureProcess(),
        timeout_seconds=1,
        max_output_bytes=4096,
    )

    assert result["exitCode"] == 17
    assert result["captureComplete"] is False
    assert result["captureErrorCode"] == "NEWS_GRASP_NOPUBLISH_OWNER_STREAM_READ_FAILED"
    assert "NEWS_GRASP_SYNTHETIC_CHILD_FAILURE" in result["stderr"]


def test_claim_failure_evidence_is_durable_and_bounded_at_each_owner_boundary() -> None:
    owner = _read(ROOT / "tools" / "news_grasp_nopublish_owner.py")
    release = _read(ROOT / "scripts" / "ops" / "news-grasp-release-nopublish.ps1")
    wrapper = _read(WRAPPER)

    assert "capture_output=True" in owner
    assert "NEWS_GRASP_NOPUBLISH_CHILD_FAILURE_EVIDENCE_V1" in owner
    assert "record-claim-failure" in release
    claim_boundary = release.split("$claimNonceSource", 1)[1].split("$state =", 1)[0]
    assert "Out-String" not in claim_boundary
    assert "Read-BoundedJsonFile -Path $launchEvidencePath -MaxBytes 65536" in wrapper
    assert "runner_evidence_path = $ownerEvidencePath" in wrapper
    assert "runner_evidence_sha256 = $ownerEvidenceSha256" in wrapper
    assert "runner_evidence_schema = $ownerEvidenceSchema" in wrapper
    assert "runner_evidence_valid = $ownerEvidenceValid" in wrapper
    assert "if ($runnerExitCode -eq 0 -and $ownerEvidenceValid)" in wrapper
    assert "ok = ($runnerExitCode -eq 0 -and $ownerEvidenceValid" in wrapper
    assert "runner_output = $ownerOutput" in wrapper
