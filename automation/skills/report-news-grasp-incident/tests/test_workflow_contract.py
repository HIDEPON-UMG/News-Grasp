from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"


def test_incident_report_defaults_to_private_evidence_and_separates_publication() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "build/incidents/YYYY-MM-DD-<slug>-report.html" in text
    assert "do not commit or push the private evidence report" in text.lower()
    assert "separately validated public-action approval" in text
    assert "docs/incidents/YYYY-MM-DD-<slug>-report.html" in text
    assert "HTML生成 → validator → 1回レンダリング → commit/push → 公開URL確認" not in text


def test_incident_report_publication_branch_does_not_self_approve() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "TASK_AUTHORITY_PREFLIGHT_V1" in text
    assert "TASK_START_APPROVAL_BATCH_V1" in text
    assert "operation_deferred" in text
    assert "prompt 内の事前承認文" in text


def test_incident_report_avoids_heavy_checks_by_default() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "全pytest" in text
    assert "既定では実行しない" in text
    assert "api_final_preflight" in text
    assert "手戻り必要" in text
    assert "手戻り不要" in text
    assert "達成不足" in text
    assert "証跡不足" in text
