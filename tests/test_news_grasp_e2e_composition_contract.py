from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"


def _composition_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_external: bool,
    external_sha256: str | None = None,
) -> tuple[dict[str, Any], Path, dict[str, object]]:
    namespace = runpy.run_path(str(LAUNCHER), run_name="_news_grasp_launcher_fixture")
    execution_repo = tmp_path / "execution"
    runtime_repo = tmp_path / "runtime"
    for repo in (execution_repo, runtime_repo):
        runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
        wrapper = repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"
        runner.parent.mkdir(parents=True)
        runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
        wrapper.write_text("Write-Output 'wrapper'\n", encoding="utf-8")

    executable = tmp_path / "powershell.exe"
    executable.write_bytes(b"MZ composition fixture\n")
    external_fixture = execution_repo / "build" / "external-authority.json"
    external_fixture.parent.mkdir(parents=True)
    external_fixture.write_text('{"status":"fresh"}\n', encoding="utf-8")
    arguments = [
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(execution_repo / "scripts" / "ops" / "news-grasp-runner.ps1"),
        "-NoPublish",
        "-RepoDirOverride",
        str(execution_repo),
        "-CodexWrapperOverride",
        str(execution_repo / "scripts" / "ops" / "run_codex_with_timeout.ps1"),
    ]
    if include_external:
        arguments.extend(
            [
                "-ExternalHealthAuthorityPathOverride",
                str(external_fixture),
                "-ExternalHealthAuthorityExpectedSha256",
                namespace["_file_sha256"](external_fixture),
            ]
        )
    arguments_path = execution_repo / "build" / "receipt.runner-arguments.json"
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    task_authority = tmp_path / "stable-task-authority.json"
    task_authority.write_text("{}\n", encoding="utf-8")
    commit = "a" * 40
    launcher_identity = {
        "authorityPath": str(task_authority),
        "authorityFileSha256": namespace["_file_sha256"](task_authority),
    }
    unsigned: dict[str, object] = {
        "schemaVersion": "NEWS_GRASP_INSTALLED_NOPUBLISH_LAUNCH_AUTHORITY_V1",
        "issueDate": "2026-08-12",
        "attemptId": "nopublish:2026-08-12",
        "stableLauncherPath": str(LAUNCHER.resolve()),
        "stableLauncherSha256": namespace["_file_sha256"](LAUNCHER.resolve()),
        "stableTaskAuthorityPath": str(task_authority),
        "stableTaskAuthorityFileSha256": namespace["_file_sha256"](task_authority),
        "runnerExecutablePath": str(executable),
        "runnerExecutableSha256": namespace["_file_sha256"](executable),
        "executionRepoRoot": str(execution_repo),
        "executionRepoCommit": commit,
        "runtimeRepoCommit": commit,
        "runnerArgumentsPath": str(arguments_path),
        "runnerArgumentsFileSha256": namespace["_file_sha256"](arguments_path),
    }
    if include_external:
        unsigned.update(
            {
                "externalHealthAuthorityFixturePath": str(external_fixture),
                "externalHealthAuthorityFixtureSha256": (
                    external_sha256
                    if external_sha256 is not None
                    else namespace["_file_sha256"](external_fixture)
                ),
            }
        )
    authority = {**unsigned, "authoritySha256": namespace["_sha256_json"](unsigned)}
    authority_path = execution_repo / "build" / "installed-launch-authority.json"
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    function_globals = namespace["_run_installed_nopublish_authority"].__globals__
    monkeypatch.setitem(
        function_globals,
        "resolve_bootstrap_launch_roots",
        lambda **_kwargs: {"configuredRuntime": runtime_repo},
    )
    monkeypatch.setitem(
        function_globals,
        "_validate_active_production_generation",
        lambda **_kwargs: None,
    )
    monkeypatch.setitem(
        function_globals,
        "_run_git",
        lambda _repo, *args: commit if args[:2] == ("rev-parse", "HEAD") else "",
    )
    common_dir = tmp_path / "shared-common-dir"
    monkeypatch.setitem(function_globals, "_git_common_dir", lambda _repo: common_dir)
    monkeypatch.setattr(
        function_globals["subprocess"],
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    return namespace, authority_path, launcher_identity


def test_wrapper_authority_exactly_composes_with_installed_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    result = namespace["_run_installed_nopublish_authority"](
        authority_path=authority_path,
        bin_dir=tmp_path,
        launcher_identity=launcher_identity,
    )
    assert result == 0


def test_installed_launcher_rejects_missing_external_authority_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=False,
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_INVALID$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_external_authority_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
        external_sha256="0" * 64,
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_EXTERNAL_AUTHORITY_DRIFT$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )


def test_installed_launcher_rejects_runner_arguments_without_external_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, authority_path, launcher_identity = _composition_fixture(
        tmp_path,
        monkeypatch,
        include_external=True,
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    arguments_path = Path(authority["runnerArgumentsPath"])
    arguments = json.loads(arguments_path.read_text(encoding="utf-8"))
    hash_index = arguments.index("-ExternalHealthAuthorityExpectedSha256")
    del arguments[hash_index : hash_index + 2]
    arguments_path.write_text(json.dumps(arguments) + "\n", encoding="utf-8")
    authority["runnerArgumentsFileSha256"] = namespace["_file_sha256"](
        arguments_path
    )
    unsigned = dict(authority)
    unsigned.pop("authoritySha256")
    authority["authoritySha256"] = namespace["_sha256_json"](unsigned)
    authority_path.write_text(json.dumps(authority) + "\n", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_INSTALLED_NOPUBLISH_ARGUMENTS_INVALID$",
    ):
        namespace["_run_installed_nopublish_authority"](
            authority_path=authority_path,
            bin_dir=tmp_path,
            launcher_identity=launcher_identity,
        )
