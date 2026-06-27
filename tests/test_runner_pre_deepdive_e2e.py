from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"


def _runner_text() -> str:
    return RUNNER.read_text(encoding="utf-8-sig")


def test_runner_exposes_stop_before_deepdive_switch() -> None:
    """NoPublish E2E can stop at the pre-DeepDive boundary."""
    runner = _runner_text()

    assert "[switch] $StopBeforeDeepDive" in runner
    assert "if ($StopBeforeDeepDive) { $NoPublish = $true }" in runner


def test_stop_before_deepdive_writes_pre_deepdive_state_only_after_daily_quality() -> None:
    """pre_deepdive_e2e_ok is written after daily-quality and volume proof."""
    runner = _runner_text()

    assert "news-grasp-runner.ps1 PRE DEEPDIVE E2E OK" in runner
    assert "Set-RunnerState -Status 'pre_deepdive_e2e_ok'" in runner
    assert runner.index("daily quality gate OK") < runner.index(
        "pre-DeepDive production volume gate OK"
    )
    assert runner.index("pre-DeepDive production volume gate OK") < runner.index(
        "StopBeforeDeepDive mode: summary-reflection and daily-quality gates succeeded"
    )
    assert runner.index(
        "StopBeforeDeepDive mode: summary-reflection and daily-quality gates succeeded"
    ) < runner.index("Stage4: Codex DeepDive")


def test_stop_before_deepdive_does_not_claim_publish_complete_or_dry_run_publish() -> None:
    """The pre-DeepDive E2E terminal state is not publish_complete or publish_dry_run_ok."""
    runner = _runner_text()
    stop_block = runner.split(
        "StopBeforeDeepDive mode: summary-reflection and daily-quality gates succeeded",
        1,
    )[1].split("Stage4: Codex DeepDive", 1)[0]

    assert "news-grasp-runner.ps1 OK" not in stop_block
    assert "news-grasp-runner.ps1 PUBLISH DRY RUN OK" not in stop_block
    assert "publish_complete" not in stop_block
    assert "publish_dry_run_ok" not in stop_block
    assert "exit 0" in stop_block


def test_stop_before_deepdive_blocks_production_volume_shortfall() -> None:
    """pre-DeepDive E2E must not mark OK when any required category is below target."""
    runner = _runner_text()

    assert "pre-DeepDive production volume gate start" in runner
    assert "$ProductionVolumeTarget = 5" in runner
    assert "failed_predeepdive_production_volume" in runner
    assert "records_count" in runner
    assert "digest_card_count" in runner
