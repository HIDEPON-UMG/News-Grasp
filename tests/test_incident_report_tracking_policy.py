from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REPORT_GLOB = "docs/incidents/*-report.html"
BUILD_REPORT_GLOB = "build/incidents/"
REMOVED_PUBLIC_REPORT = "docs/incidents/2026-06-30-daily-batch-recovery-report.html"

# Historical files that were already tracked before the 2026-06-30 removal.
# Any new incident report HTML must stay out of git and public Pages.
LEGACY_TRACKED_REPORTS = {
    "docs/incidents/2026-06-20-daily-batch-recovery-report.html",
    "docs/incidents/2026-06-22-daily-batch-and-summary-incident-report.html",
    "docs/incidents/2026-06-23-daily-batch-recovery-report.html",
    "docs/incidents/2026-06-24-digest-articles-reconcile-report.html",
    "docs/incidents/2026-06-25-daily-batch-incomplete-report.html",
    "docs/incidents/2026-06-27-scriptblock-nopublish-report.html",
    "docs/incidents/2026-06-28-daily-batch-recovery-report.html",
    "docs/incidents/2026-06-28-e2e-artifact-collision-report.html",
    "docs/incidents/2026-06-28-final-predeepdive-e2e-report.html",
    "docs/incidents/2026-06-28-full-runner-bug-patterns-report.html",
    "docs/incidents/2026-06-28-historical-failure-horizontal-audit-report.html",
}


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _tracked_reports() -> set[str]:
    return {path for path in _git_lines("ls-files", "docs/incidents") if path.endswith("-report.html")}


def test_incident_report_html_is_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert REPORT_GLOB in gitignore
    assert BUILD_REPORT_GLOB in gitignore


def test_no_new_incident_report_html_is_tracked() -> None:
    unexpected = sorted(_tracked_reports() - LEGACY_TRACKED_REPORTS)

    assert not unexpected, f"{REPORT_GLOB} must not be tracked: {unexpected}"


def test_2026_06_30_public_report_is_not_tracked() -> None:
    assert REMOVED_PUBLIC_REPORT not in _tracked_reports()
