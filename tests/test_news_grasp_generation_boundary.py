from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from tools import news_grasp_generation as generation


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
CRITICAL_PATHS = (
    "scripts/ops/news-grasp-runner.ps1",
    "tools/daily_self_heal.py",
    "tools/news_grasp_daily_control.py",
    "tools/news_grasp_operational_contract.py",
    "tools/news_grasp_checkpoint.py",
    "tools/news_grasp_generation.py",
    "tools/operational_recovery_registry.py",
    "config/operational_recovery_registry_v1.json",
)


def _generation_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    tracked_status: str = "",
    remote_heads: list[str] | None = None,
) -> tuple[dict[str, Any], Path, Path, Path, dict[str, str]]:
    namespace = runpy.run_path(str(LAUNCHER), run_name="_news_grasp_generation_fixture")
    repo = tmp_path / "repo"
    repo.mkdir()
    tracked_rows: dict[str, str] = {}
    for index, relative in enumerate((*CRITICAL_PATHS, "docs/spec.md"), start=1):
        candidate = repo / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(f"tracked-{index}\n", encoding="utf-8")
        tracked_rows[relative] = f"100644:blob:{index:040x}"

    bin_dir = tmp_path / "bin"
    runtime_root = tmp_path / "runtime-root"
    bin_dir.mkdir()
    runtime_root.mkdir()
    runtime_config = bin_dir / "news-grasp-runtime-root-v1.json"
    runtime_config.write_text('{"schemaVersion":"NEWS_GRASP_RUNTIME_ROOT_V1"}\n', encoding="utf-8")
    authority: dict[str, object] = {
        "schemaVersion": "STABLE_TASK_AUTHORITY_V1",
        "taskName": "News-Grasp Runner",
        "stableLauncherPath": str(LAUNCHER.resolve()),
        "stableLauncherSha256": namespace["_file_sha256"](LAUNCHER.resolve()),
        "bootstrapPath": str((ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1").resolve()),
        "bootstrapSha256": "b" * 64,
        "action": ["pythonw.exe", str(LAUNCHER.resolve()), "runner"],
        "trigger": {"daily": "06:00"},
        "repoArgumentCount": 0,
    }
    authority["authoritySha256"] = namespace["_sha256_json"](authority)
    (bin_dir / "news-grasp-stable-task-authority-v1.json").write_text(
        json.dumps(authority) + "\n",
        encoding="utf-8",
    )

    head = "a" * 40
    remote_values = list(remote_heads or [head, head])

    def fake_git(_repo: Path, *arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return head
        if arguments == ("rev-parse", "origin/main"):
            return remote_values.pop(0) if remote_values else head
        if arguments in {
            ("status", "--porcelain", "--untracked-files=no"),
            ("status", "--porcelain", "--untracked-files=no", "-z"),
        }:
            return tracked_status + ("\0" if arguments[-1] == "-z" and tracked_status else "")
        if arguments == ("ls-tree", "-r", "--full-tree", "-z", head):
            return "\0".join(
                f"{identity.replace(':', ' ', 2)}\t{relative}"
                for relative, identity in tracked_rows.items()
            ) + "\0"
        raise AssertionError(f"unexpected git call: {arguments!r}")

    function_globals = namespace["_seal_active_production_generation"].__globals__
    monkeypatch.setitem(function_globals, "_run_git", fake_git)
    monkeypatch.setitem(
        function_globals,
        "_git_common_dir",
        lambda _repo: tmp_path / "common-dir",
    )
    return namespace, repo, runtime_root, bin_dir, tracked_rows


def test_generation_manifest_seals_complete_tracked_source_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, repo, runtime_root, bin_dir, tracked_rows = _generation_fixture(
        tmp_path,
        monkeypatch,
    )
    pointer = namespace["_seal_active_production_generation"](
        source_repo=repo,
        runtime_repo=repo,
        runtime_root=runtime_root,
        origin_sha="a" * 40,
        bin_dir=bin_dir,
    )
    manifest = json.loads(Path(pointer["manifestPath"]).read_text(encoding="utf-8"))
    assert manifest["source"].get("trackedFiles") == tracked_rows, (
        "NGC_RED_SOURCE_TRACKED_MANIFEST_MISSING"
    )
    assert manifest["source"].get("trackedManifestSha256") == namespace["_sha256_json"](
        tracked_rows
    )


def test_generation_seal_rejects_tracked_dirty_source_before_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, repo, runtime_root, bin_dir, _rows = _generation_fixture(
        tmp_path,
        monkeypatch,
        tracked_status=" M docs/spec.md",
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_PRODUCTION_GENERATION_DIRTY$",
    ):
        namespace["_seal_active_production_generation"](
            source_repo=repo,
            runtime_repo=repo,
            runtime_root=runtime_root,
            origin_sha="a" * 40,
            bin_dir=bin_dir,
        )
    assert not (runtime_root / "active-generation-v2.json").exists()


def test_generation_seal_rejects_remote_owner_drift_before_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace, repo, runtime_root, bin_dir, _rows = _generation_fixture(
        tmp_path,
        monkeypatch,
        remote_heads=["a" * 40, "b" * 40],
    )
    with pytest.raises(
        RuntimeError,
        match="^NEWS_GRASP_PRODUCTION_GENERATION_SOURCE_DRIFT$",
    ):
        namespace["_seal_active_production_generation"](
            source_repo=repo,
            runtime_repo=repo,
            runtime_root=runtime_root,
            origin_sha="a" * 40,
            bin_dir=bin_dir,
        )
    assert not (runtime_root / "active-generation-v2.json").exists()


def test_run_envelope_derives_active_and_standby_without_new_generation() -> None:
    envelope = generation.seal_run_envelope(
        issue_date="2026-08-17",
        generation_manifest={"schemaVersion": generation.SCHEMA, "generationId": "generation-active"},
        stable_task_authority={"authority": "task"},
        runtime_binding={"python": "system"},
        task_action=["pythonw.exe", "launcher.pyw", "runner"],
        descriptor_sha256="a" * 64,
        deadman_sha256="b" * 64,
        active_capsule={"generationId": "generation-active", "status": "verified"},
        standby_capsule={"generationId": "generation-standby", "status": "verified"},
    )
    assert envelope["generationId"] == "generation-active"
    assert generation.validate_run_envelope(envelope)["schemaVersion"] == "RUN_ENVELOPE_V1"
    assert envelope["activeCapsule"]["generationId"] != envelope["standbyCapsule"]["generationId"]


def test_installer_delivery_binds_generation_to_task_and_automation_evidence() -> None:
    installer = (ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "RUN_ENVELOPE_V1" in installer
    assert "active-generation-v2.json" in installer
    assert "stableTaskAuthoritySha256" in installer
    assert "automationAssetManifestSha256" in installer
    assert "generationBinding" in installer
    assert "sourceRoot = $RepoDir" in installer
    assert "installedRoot = $BinDir" in installer


def test_installer_reads_generation_pointer_from_runtime_parent() -> None:
    installer = (ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "$generationAuthorityRoot = Split-Path -Parent $runtimeEvidenceRepoDir" in installer
    assert "$activeGenerationPointerPath = Join-Path $generationAuthorityRoot" in installer
    assert (
        "$activeGenerationPointerPath = Join-Path $runtimeEvidenceRepoDir 'active-generation-v2.json'"
        not in installer
    )
