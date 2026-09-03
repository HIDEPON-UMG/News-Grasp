from pathlib import Path


ROOT = Path(__file__).parents[1]
RETIRED = "NEWS_GRASP_WINDOWS_TASK_SCHEDULER_RETIRED"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def test_codex_automation_is_the_only_daily_scheduler_and_legacy_installers_fail_closed() -> None:
    spec = _text("docs/spec.md")
    readme = _text("README.md")
    automation = _text("automation/news-grasp-6-40/automation.toml.template")
    skill = _text("automation/skills/news-grasp-direct-mainline/SKILL.md")
    syncer = _text("tools/sync_news_grasp_codex_automation.py")

    assert "Windows Task Scheduler は廃止済み" in spec
    assert "Codex automation `news-grasp-6-40`" in spec
    assert "Windows タスクスケジューラ「News-Grasp Runner」" not in readme
    assert "Codex automation" in readme
    assert "ScheduledProductionが起動できるcommand" not in automation
    assert "Codex automationが起動できるcommand" in automation
    assert "ScheduledProductionが実行可能なentry" not in skill
    assert "Codex automationが実行可能なentry" in skill
    assert "Windows Scheduled Task や旧 runner には触れない" in syncer

    for relative in (
        "scripts/ops/install-news-grasp-ops.ps1",
        "scripts/ops/install-news-grasp-title-materializer.ps1",
    ):
        source = _text(relative)
        guard_at = source.index(RETIRED)
        registration_at = source.index("Register-ScheduledTask")
        assert guard_at < registration_at
