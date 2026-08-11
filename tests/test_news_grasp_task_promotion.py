"""WP-16 A19 stable task/promotion Red/Green契約。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import news_grasp_generation as generation


def test_ng3_a19_primary_stable_task_has_no_repo_path(tmp_path: Path) -> None:
    assert callable(getattr(generation, "create_stable_task_authority", None))
    result = generation.create_stable_task_authority(
        task_name="News-Grasp Production",
        launcher_path=tmp_path / "news-grasp-task-launcher.pyw",
        launcher_sha256="a" * 64,
        action=["pythonw.exe", "news-grasp-task-launcher.pyw", "runner"],
        trigger={"daily": "06:00"},
    )
    assert result["repoArgumentCount"] == 0


def test_ng3_a19_installer_persists_stable_task_authority() -> None:
    installer = Path(__file__).parents[1] / "scripts" / "ops" / "install-news-grasp-ops.ps1"
    source = installer.read_text(encoding="utf-8-sig")
    assert "STABLE_TASK_AUTHORITY_V1" in source
    assert "news-grasp-stable-task-authority-v1.json" in source
    assert "repoArgumentCount = 0" in source


def test_ngc_c07_installer_does_not_gate_deterministic_promotion_on_external_model_control() -> None:
    """runtime同期はexternal model readinessと分離し、model実行側だけをfail-closedにする。"""
    root = Path(__file__).parents[1]
    installer = (root / "scripts/ops/install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    executable = installer.split("$TaskPythonwPath = Resolve-NewsGraspTaskPythonw", 1)[1]
    executable = executable.split("$ops = Join-Path $RepoDir 'scripts\\ops'", 1)[0]

    assert "Assert-NewsGraspExternalControlPlaneReady" not in executable
    assert "Assert-NewsGraspSharedBrokerGeneration" not in executable

    daily_control = (root / "tools/news_grasp_daily_control.py").read_text(encoding="utf-8")
    runner = (root / "scripts/ops/news-grasp-runner.ps1").read_text(encoding="utf-8-sig")
    assert "probe_external_readiness" in daily_control
    assert "external_control_plane_unavailable" in runner


def test_ng3_a19_adversarial_task_fire_during_promotion_is_old_or_new_only(tmp_path: Path) -> None:
    assert callable(getattr(generation, "promote_generation", None))
    with pytest.raises(generation.NewsGraspGenerationError, match="NG_PROMOTION_PHASE_INVALID"):
        generation.promote_generation(
            active_pointer=tmp_path / "active.json",
            old_generation_id="old",
            new_generation_id="new",
            phase="runtime_staged",
            stable_task_authority={"repoArgumentCount": 0},
        )


def test_ng3_a19_recovery_crash_forward_or_old_generation(tmp_path: Path) -> None:
    assert callable(getattr(generation, "recover_promotion", None))
    result = generation.recover_promotion(
        wal={"phase": "runtime_staged", "oldGenerationId": "old", "newGenerationId": "new"},
        active_pointer=tmp_path / "active.json",
    )
    assert result["generationId"] == "old"
    assert result["status"] == "old_generation_retained"
