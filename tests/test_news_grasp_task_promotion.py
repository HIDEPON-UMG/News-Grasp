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
