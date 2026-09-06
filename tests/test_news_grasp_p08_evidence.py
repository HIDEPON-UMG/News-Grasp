from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import os
import sys
import types
from pathlib import Path

import pytest

from tools import news_grasp_p08_evidence as evidence
from tools import e2e_final_admission_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]


def test_stage1_descriptor_is_not_active_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = tmp_path / ".codex/state/high-cost-operation"
    state.mkdir(parents=True)
    descriptor = {"schemaVersion": "HIGH_COST_CAPABILITY_DESCRIPTOR_V1", "workspaceRoot": str(tmp_path), "generation": 2}
    (state / "capability-v1.json").write_text(json.dumps(descriptor), encoding="utf-8")
    authority = {"promotionGuardGenerationId": "high-cost-generation-1", "statefulSelfTestStatus": "green"}
    authority["receiptSha256"] = evidence.canonical_sha256(authority)
    path = state / "external-health-authority-v1.json"
    path.write_text(json.dumps(authority), encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="GLOBAL_MODULE_BINDING_INVALID"):
        evidence._read_authority_descriptor()
    authority["promotionGuardGenerationId"] = "high-cost-generation-2"
    authority["receiptSha256"] = evidence.canonical_sha256({key: value for key, value in authority.items() if key != "receiptSha256"})
    path.write_text(json.dumps(authority), encoding="utf-8")
    assert evidence._read_authority_descriptor()["generation"] == 2


@pytest.fixture
def promoted_authority(tmp_path, monkeypatch):
    harness = tmp_path / "tools/harness"
    harness.mkdir(parents=True)
    contents = {name: b"value = 'trusted'\n" for name in evidence.AUTHORITY_MODULES}
    contents["high_cost_operation_budget.py"] = b"import high_cost_adversarial_review as review\nvalue = review.value\n"
    for name, raw in contents.items():
        (harness / name).write_bytes(raw)
    descriptor = {"workspaceRoot": str(tmp_path), "generation": 1,
                  "authorityModules": {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}}
    monkeypatch.setattr(evidence, "_read_authority_descriptor", lambda: descriptor)
    monkeypatch.setattr(evidence, "_authority_module_paths", lambda _: {name: harness / name for name in contents})
    return tmp_path, harness


def test_authority_ignores_import_cache_poison_and_rejects_dependency_replacement(promoted_authority, monkeypatch):
    root, harness = promoted_authority
    poison = types.ModuleType("high_cost_adversarial_review")
    poison.value = "untrusted"
    monkeypatch.setitem(sys.modules, poison.__name__, poison)
    assert evidence._load_global_module(root, "high_cost_operation_budget.py").value == "trusted"
    assert sys.modules[poison.__name__] is poison
    (harness / "high_cost_adversarial_review.py").write_text("value = 'untrusted'\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="GLOBAL_MODULE_BINDING_INVALID"):
        evidence._load_global_module(root, "high_cost_operation_budget.py")


def test_authority_executes_snapshot_when_path_changes_after_read(promoted_authority, monkeypatch):
    from tools import news_grasp_nopublish_owner as owner
    root, harness = promoted_authority
    original = owner._read_stable_bytes
    reads = []
    def read(path, **kwargs):
        result = original(path, **kwargs)
        reads.append(path.name)
        if path.name == "high_cost_adversarial_review.py":
            path.write_text("value = 'untrusted'\n", encoding="utf-8")
        return result
    monkeypatch.setattr(owner, "_read_stable_bytes", read)
    assert evidence._load_global_module(root, "high_cost_operation_budget.py").value == "trusted"
    assert len(reads) == len(set(reads)) == len(evidence.AUTHORITY_MODULES)


def test_authority_rejects_actual_user_code_replacement(promoted_authority):
    root, harness = promoted_authority
    module = evidence._load_global_module(root, "high_cost_operation_budget.py")
    assert module._load_task_operating_contract_module().value == "trusted"
    (harness / "task_operating_contract.py").write_text("value = 'untrusted'\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="GLOBAL_MODULE_BINDING_INVALID"):
        evidence._load_global_module(root, "high_cost_operation_budget.py")


def test_actual_authority_modules_load_with_isolated_python(tmp_path):
    """stubでなく実6 moduleのimport/実ユーザー判定入口を-I -Sで検証する。"""
    workspace = Path.home() / "OneDrive/ドキュメント/ProjectFolders"
    paths = evidence._authority_module_paths(workspace)
    if not all(path.is_file() for path in paths.values()):
        pytest.skip("実authority sourceのあるWindows host専用")
    descriptor = {"workspaceRoot": str(workspace), "generation": 1,
                  "authorityModules": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}}
    descriptor_path = tmp_path / "authority.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    script = tmp_path / "load.py"
    script.write_text(
        "import json,sys,importlib.util\nfrom pathlib import Path\n"
        "spec=importlib.util.spec_from_file_location('p08',Path(sys.argv[1])/'tools/news_grasp_p08_evidence.py')\n"
        "p=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(p)\n"
        "d=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "p._read_authority_descriptor=lambda:d\n"
        "m=p._load_global_module(Path(d['workspaceRoot']),'high_cost_operation_budget.py')\n"
        "assert callable(m.consume_full_e2e_start)\n"
        "assert callable(m._load_task_operating_contract_module()._authoritative_user_events)\n",
        encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), str(ROOT), str(descriptor_path)],
                            capture_output=True, timeout=20, shell=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse実境界")
def test_authority_rejects_parent_junction(promoted_authority):
    import _winapi
    root, harness = promoted_authority
    moved = root / "original-harness"
    harness.rename(moved)
    _winapi.CreateJunction(str(moved), str(harness))
    with pytest.raises(evidence.P08EvidenceError, match="GLOBAL_MODULE_BINDING_INVALID"):
        evidence._load_global_module(root, "high_cost_operation_budget.py")


def test_global_authority_requires_promoted_content_and_accepts_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = tmp_path / "tools" / "harness"
    harness.mkdir(parents=True)
    authority = harness / "high_cost_adversarial_review.py"
    monkeypatch.setattr(evidence, "_canonical_workspace_root", lambda _candidate: tmp_path)
    authority.write_text("value = 'first'\n", encoding="utf-8")
    names = evidence.AUTHORITY_MODULES
    for name in names:
        if name != authority.name:
            (harness / name).write_text("value = 'dependency'\n", encoding="utf-8")
    descriptor = {"workspaceRoot": str(tmp_path), "generation": 1, "authorityModules": {
        name: hashlib.sha256((harness / name).read_bytes()).hexdigest() for name in names}}
    monkeypatch.setattr(evidence, "_read_authority_descriptor", lambda: descriptor, raising=False)
    monkeypatch.setattr(evidence, "_authority_module_paths", lambda _: {name: harness / name for name in names})
    assert evidence._load_global_module(tmp_path, authority.name).value == "first"

    authority.write_text("value = 'second'\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="GLOBAL_MODULE_BINDING_INVALID"):
        evidence._load_global_module(tmp_path, authority.name)
    descriptor["generation"] = 2
    descriptor["authorityModules"][authority.name] = hashlib.sha256(authority.read_bytes()).hexdigest()
    assert evidence._load_global_module(tmp_path, authority.name).value == "second"
    assert not hasattr(evidence, "GLOBAL_BUDGET_SHA256")
    assert not hasattr(evidence, "GLOBAL_REVIEW_SHA256")


def test_design_and_route_seals_are_recomputed(tmp_path: Path) -> None:
    design = evidence.build_design(
        workspace_root=tmp_path,
        thread_id="01a00a21-86b9-7800-ab39-3845476e5e44",
        task_root_user_event_hash="a" * 64,
        latest_actual_user_event_hash="b" * 64,
    )
    assert design["designSha256"] == evidence.canonical_sha256(
        {key: value for key, value in design.items() if key != "designSha256"}
    )
    route = evidence.build_route_manifest(
        workspace_root=tmp_path,
        task_identity=design["taskIdentity"],
        route_specs=[],
        required_route_ids=[],
    )
    assert route["manifestSha256"] == evidence.canonical_sha256(
        {key: value for key, value in route.items() if key != "manifestSha256"}
    )


def test_review_rejects_route_source_drift(tmp_path: Path) -> None:
    source = tmp_path / "route.ps1"
    source.write_text("gate\nlaunch\n", encoding="utf-8")
    route = evidence.build_route_manifest(
        workspace_root=tmp_path,
        task_identity="a" * 64,
        route_specs=[
            ("r1", source, "gate", "launch"),
        ],
        required_route_ids=["r1"],
    )
    source.write_text("launch\ngate\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="HIGH_COST_ROUTE_SOURCE_DRIFT"):
        evidence.validate_route_manifest(route, workspace_root=tmp_path, task_identity="a" * 64, required_route_ids=["r1"])


def test_command_receipt_is_green_only_for_zero_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class _Process:
        returncode = 0

        def wait(self, timeout: int) -> None:
            return None

    monkeypatch.setattr(evidence.subprocess, "Popen", lambda *args, **kwargs: _Process())
    receipt = evidence.run_verification_command(
        schema=evidence.STATIC_SCHEMA,
        command=["python", "-m", "pytest"],
        cwd=tmp_path,
    )
    assert receipt["status"] == "Green"
    assert receipt["receiptSha256"] == evidence.canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receiptSha256"}
    )


def test_caller_evidence_order_excludes_red_execution(tmp_path: Path) -> None:
    paths = {}
    for name in evidence.CALLER_EVIDENCE_KINDS:
        path = tmp_path / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        paths[name] = path
    rows = evidence.caller_evidence_bindings(paths)
    assert [row["kind"] for row in rows] == list(evidence.CALLER_EVIDENCE_KINDS)
    assert "red_suite_execution" not in [row["kind"] for row in rows]
    for path in paths.values():
        path.unlink()


def test_runtime_python_identity_requires_fixed_trust_anchor_and_thumbprint(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "python.exe"
    candidate.write_bytes(b"MZ fixture")
    binding = {
        "schemaVersion": "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1",
        "pythonTrustAnchor": bridge.PYTHON_TRUST_ANCHOR,
        "pythonSignerSubject": bridge.PYTHON_SIGNER_SUBJECT,
        "pythonSignerThumbprint": bridge.PYTHON_SIGNER_THUMBPRINT,
    }
    signature = {
        "status": "Valid",
        "subject": bridge.PYTHON_SIGNER_SUBJECT,
        "thumbprint": bridge.PYTHON_SIGNER_THUMBPRINT,
    }
    bridge._validate_runtime_python_identity(
        binding,
        candidate=candidate,
        bound_path=candidate.resolve(),
        bound_sha="a" * 64,
        signature=signature,
    )
    binding["pythonSignerThumbprint"] = "0" * 64
    with pytest.raises(bridge.E2EFinalAdmissionError, match="E2E_AUTHORITY_PYTHON_INVALID"):
        bridge._validate_runtime_python_identity(
            binding,
            candidate=candidate,
            bound_path=candidate.resolve(),
            bound_sha="a" * 64,
            signature=signature,
        )


def test_p08_generates_current_source_red_suite_coverage_once(tmp_path: Path) -> None:
    output = tmp_path / "red-suite-coverage-report.json"

    report = evidence.generate_red_suite_coverage(
        repo_root=ROOT,
        output_path=output,
    )

    assert report["schemaVersion"] == "RED_SUITE_COVERAGE_REPORT_V1"
    assert report["status"] == "Green"
    assert report["findings"] == []
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_p08_isolation_receipt_is_bound_to_issue_target_and_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "target"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "News-Grasp Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    article = repo / "data" / "articles.jsonl"
    article.parent.mkdir()
    article.write_text(
        '{"date":"2026-09-01","title":"retain"}\n'
        '{"date":"2026-09-02","title":"remove"}\n',
        encoding="utf-8",
    )
    legacy_session = repo / "data" / "_session_urls.json"
    legacy_session.write_text('{"date":"2026-09-02"}\n', encoding="utf-8")
    daily = repo / "docs" / "2026-09-02" / "index.html"
    daily.parent.mkdir(parents=True)
    daily.write_text("published", encoding="utf-8")
    target_validator = repo / "tools" / "news_grasp_p08_evidence.py"
    target_validator.parent.mkdir()
    target_validator.write_text("trusted = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    source = tmp_path / "source"
    subprocess.run(["git", "clone", "-q", str(repo), str(source)], check=True)
    daily.unlink()
    daily.parent.rmdir()
    legacy_session.unlink()
    article.write_text('{"date":"2026-09-01","title":"retain"}\n', encoding="utf-8")
    receipt = tmp_path / "e2e-isolation-2026-09-02.json"
    value = {
        "schemaVersion": "NEWS_GRASP_E2E_ISOLATION_V1",
        "status": "Green",
        "issueDate": "2026-09-02",
        "targetRoot": str(repo.resolve()),
        "sourceRepo": str(source.resolve()),
        "sourceCommit": head,
        "targetCommit": head,
        "runnerArtifactPredicate": False,
        "removed": [
            "data/_session_urls.json",
            "docs/2026-09-02/",
            "data/articles.jsonl#issue-date-records",
        ],
        "removedArticleCount": 1,
        "removalPolicyVersion": "NEWS_GRASP_E2E_ISSUE_REMOVAL_POLICY_V1",
        "allowedParent": str(repo.parent.resolve()),
    }
    value["removedSetSha256"] = evidence._load_repo_module(
        ROOT, "e2e_isolation.py"
    ).isolation_removed_set_sha256(value["removed"])
    receipt.write_text(json.dumps(value), encoding="utf-8")

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "redirected-git-dir"))
    validated = evidence.validate_isolation_receipt(
        receipt,
        repo_root=repo,
        source_repo_root=source,
        issue_date="2026-09-02",
    )
    assert {key: validated[key] for key in value} == value
    assert validated["validation"]["sourceHead"] == head
    assert validated["validation"]["validatorSha256"] == evidence.file_sha256(
        Path(evidence.__file__)
    )

    for field, bad_value in (
        ("issueDate", "2026-08-17"),
        ("targetRoot", str(tmp_path.resolve())),
        ("targetCommit", "0" * 40),
        ("runnerArtifactPredicate", True),
    ):
        changed = dict(value, **{field: bad_value})
        receipt.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(evidence.P08EvidenceError, match="P08_ISOLATION_EVIDENCE_INVALID"):
            evidence.validate_isolation_receipt(
                receipt,
                repo_root=repo,
                source_repo_root=source,
                issue_date="2026-09-02",
            )

    receipt.write_text(json.dumps(value), encoding="utf-8")
    target_validator.write_text("trusted = False\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="P08_ISOLATION_EVIDENCE_INVALID"):
        evidence.validate_isolation_receipt(
            receipt,
            repo_root=repo,
            source_repo_root=source,
            issue_date="2026-09-02",
        )

    target_validator.write_text("trusted = True\n", encoding="utf-8")
    untracked_module = repo / "tools" / "untracked_probe.py"
    untracked_module.write_text("trusted = False\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="P08_ISOLATION_EVIDENCE_INVALID"):
        evidence.validate_isolation_receipt(
            receipt,
            repo_root=repo,
            source_repo_root=source,
            issue_date="2026-09-02",
        )
    untracked_module.unlink()

    article.write_text('{"date":"2026-09-01","title":"tampered"}\n', encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="P08_ISOLATION_EVIDENCE_INVALID"):
        evidence.validate_isolation_receipt(
            receipt,
            repo_root=repo,
            source_repo_root=source,
            issue_date="2026-09-02",
        )
    article.write_text('{"date":"2026-09-01","title":"retain"}\n', encoding="utf-8")

    malicious = dict(value)
    malicious["removed"] = [*value["removed"], "tools/news_grasp_p08_evidence.py"]
    malicious["removedSetSha256"] = evidence._load_repo_module(
        ROOT, "e2e_isolation.py"
    ).isolation_removed_set_sha256(malicious["removed"])
    receipt.write_text(json.dumps(malicious), encoding="utf-8")
    target_validator.write_text("trusted = False\n", encoding="utf-8")
    with pytest.raises(evidence.P08EvidenceError, match="P08_ISOLATION_EVIDENCE_INVALID"):
        evidence.validate_isolation_receipt(
            receipt,
            repo_root=repo,
            source_repo_root=source,
            issue_date="2026-09-02",
        )


def test_p08_generate_requires_explicit_isolation_receipt() -> None:
    parameters = inspect.signature(evidence.generate).parameters
    assert "isolation_receipt_path" in parameters


def test_p08_route_manifest_uses_candidate_owner_without_post_promotion_evidence() -> None:
    design = evidence.build_design(
        workspace_root=ROOT,
        thread_id="01a06f12-ab74-7340-8e8a-ccd52214f885",
        task_root_user_event_hash="a" * 64,
    )
    source = (ROOT / "tools" / "news_grasp_p08_evidence.py").read_text(
        encoding="utf-8"
    )

    assert "candidate_owner" in design["requiredRouteIds"]
    assert "installed_launcher" not in design["requiredRouteIds"]
    assert "R08_source_isolation_owner_parity" in design["requirementIds"]
    assert "R08_source_installed_task_parity" not in design["requirementIds"]
    chosen = next(
        item
        for item in design["candidateStrategies"]
        if item["strategyId"] == design["chosenStrategyId"]
    )
    assert "release_reflection" not in chosen["reusedEvidenceIds"]
    assert "news_grasp_nopublish_owner.py" in source
    assert "& $installedTaskPythonPath" not in source


def test_nopublish_wrapper_validates_bound_isolation_before_launch() -> None:
    wrapper = (
        ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "[Parameter(Mandatory=$true)][string] $IsolationReceiptPath" in wrapper
    assert "source candidate generation is dirty" in wrapper
    assert ".StartsWith('GIT_', [StringComparison]::OrdinalIgnoreCase)" in wrapper
    assert "[Environment]::SetEnvironmentVariable(" in wrapper
    assert "Local\\NewsGraspOpsInstallV1" not in wrapper
    assert "$runtimeInstallMutex.ReleaseMutex()" not in wrapper
    assert "NEWS_GRASP_NOPUBLISH_CANDIDATE_DRIFT_BEFORE_LAUNCH" in wrapper
    assert "$sourceRepoStatusBeforeLaunchExitCode = $LASTEXITCODE" in wrapper
    assert "$executionTrackedStatusBeforeLaunchExitCode = $LASTEXITCODE" in wrapper
    assert "$p08EvidenceToolBlobBeforeLaunchExitCode -ne 0" in wrapper
    assert "HEAD:tools/news_grasp_p08_evidence.py" in wrapper
    assert "NEWS_GRASP_NOPUBLISH_RUNTIME_VALIDATOR_BLOB_INVALID" in wrapper
    assert "Join-Path $repoPath 'tools\\news_grasp_p08_evidence.py'" in wrapper
    assert "'--source-repo' $sourceRepoPath" in wrapper
    validation = wrapper.index("'validate-isolation'")
    binding = wrapper.index("NEWS_GRASP_E2E_ISOLATION_ADMISSION_BINDING_INVALID")
    launch = wrapper.index("'-B' $nopublishOwnerPath")
    assert validation < binding < launch


def test_e2e_isolation_git_calls_ignore_inherited_git_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    module = evidence._load_repo_module(ROOT, "e2e_isolation.py")

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "redirected-git-dir"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.worktree=C:/Windows'")

    assert Path(module._run_git(repo, "rev-parse", "--show-toplevel")) == repo.resolve()

    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._run_git(repo, "status")
    assert observed["timeout"] == 120

    def timeout_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", timeout_run)
    with pytest.raises(module.E2EIsolationError, match="E2E_ISOLATION_GIT_TIMEOUT"):
        module._run_git(repo, "status")


def test_e2e_isolation_cleans_partial_worktree_after_add_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = evidence._load_repo_module(ROOT, "e2e_isolation.py")
    source = tmp_path / "source"
    source.mkdir()
    allowed = tmp_path / "isolated"
    target = allowed / "target"
    expected_commit = "a" * 40
    calls: list[tuple[str, ...]] = []

    def fake_git(repo: Path, *args: str) -> str:
        calls.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return expected_commit
        if args[:2] == ("worktree", "add"):
            target.mkdir(parents=True)
            raise module.E2EIsolationError("E2E_ISOLATION_GIT_TIMEOUT")
        if args == ("worktree", "remove", "--force", str(target)):
            raise OSError("first remove failed")
        if args == (
            "worktree",
            "remove",
            "--force",
            "--force",
            str(target),
        ):
            target.rmdir()
            return ""
        if args == ("worktree", "list", "--porcelain"):
            return f"worktree {source}\nHEAD {expected_commit}\ndetached"
        raise AssertionError(args)

    monkeypatch.setattr(module, "_run_git", fake_git)
    with pytest.raises(module.E2EIsolationError, match="E2E_ISOLATION_GIT_TIMEOUT"):
        module.prepare_isolated_worktree(
            source_repo=source,
            target_root=target,
            allowed_parent=allowed,
            issue_date="2026-09-02",
            expected_commit=expected_commit,
        )
    assert not target.exists()
    assert any(args[:2] == ("worktree", "remove") for args in calls)
    assert (
        "worktree",
        "remove",
        "--force",
        "--force",
        str(target),
    ) in calls
    assert ("worktree", "list", "--porcelain") in calls
    assert not any(args[:2] == ("worktree", "prune") for args in calls)


def test_e2e_isolation_wraps_unexpected_outer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = evidence._load_repo_module(ROOT, "e2e_isolation.py")

    def fail_impl(**kwargs: object) -> dict[str, object]:
        raise OSError("sensitive fixture path")

    monkeypatch.setattr(module, "_prepare_isolated_worktree_impl", fail_impl)
    with pytest.raises(module.E2EIsolationError, match="^E2E_ISOLATION_PREPARE_FAILED$"):
        module.prepare_isolated_worktree(
            source_repo=tmp_path,
            target_root=tmp_path / "target",
            allowed_parent=tmp_path,
            issue_date="2026-09-02",
            expected_commit="a" * 40,
        )
