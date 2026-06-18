from pathlib import Path

from tools.validate_incident_report_design import validate_report


def test_incident_report_design_rejects_class_based_report(tmp_path: Path) -> None:
    report = tmp_path / "incident.html"
    report.write_text(
        """
<!doctype html>
<html lang="ja">
<head><meta charset="utf-8"><style>.card{color:red}</style></head>
<body>
  <div class="card">結論</div>
  <section><h2>どの工程で問題が起きたか</h2></section>
</body>
</html>
""",
        encoding="utf-8",
    )

    result = validate_report(report)

    assert not result.ok
    assert any("class 属性" in error for error in result.errors)


def test_current_incident_report_follows_required_design() -> None:
    result = validate_report(Path("docs/incidents/2026-06-18-daily-batch-failure-report.html"))

    assert result.ok, result.errors
