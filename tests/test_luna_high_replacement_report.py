from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def require_reporter():
    try:
        return importlib.import_module("tools.build_luna_high_replacement_report")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing Luna-high replacement report builder: {exc}")


def test_report_provenance_path_is_repo_relative() -> None:
    reporter = require_reporter()

    rendered = reporter.portable_provenance_path(REPO_ROOT / "_ops" / "benchmark-runs" / "records.json")

    assert rendered == "_ops/benchmark-runs/records.json"
    assert "Users" not in rendered


def _records(*, model: str = "gpt-5.6-sol", effort: str = "high") -> list[dict]:
    return [
        {
            "model": model,
            "effort": effort,
            "case_id": "case-a",
            "task_id": "NG-RC",
            "repetition": repetition,
            "score": 1.0,
            "fatal": False,
            "pass": True,
        }
        for repetition in (1, 2, 3)
    ]


def test_record_set_validation_rejects_missing_duplicate_and_rejected_rows() -> None:
    reporter = require_reporter()
    expected = {
        ("gpt-5.6-sol", "high", "case-a", repetition)
        for repetition in (1, 2, 3)
    }

    valid = reporter.validate_record_set(_records(), expected_keys=expected)
    assert valid["complete"] is True
    assert valid["missing"] == []
    assert valid["duplicates"] == []
    assert valid["rejected_rows"] == []

    with pytest.raises(ValueError, match="incomplete benchmark input"):
        reporter.validate_record_set(_records()[:-1], expected_keys=expected)
    with pytest.raises(ValueError, match="duplicate benchmark key"):
        reporter.validate_record_set(_records() + [_records()[0]], expected_keys=expected)
    rejected = _records()
    rejected[0] = dict(rejected[0], rejected=True)
    with pytest.raises(ValueError, match="rejected benchmark row"):
        reporter.validate_record_set(rejected, expected_keys=expected)


def test_role_replacement_verdict_requires_quality_and_operational_noninferiority() -> None:
    reporter = require_reporter()
    current = {"quality": 0.80, "pass_rate": 0.90, "fatal_rate": 0.0, "closure_rate": 0.80, "sample_count": 3}
    luna = {"quality": 0.78, "pass_rate": 0.90, "fatal_rate": 0.0, "closure_rate": 0.80, "sample_count": 3}

    assert reporter.classify_role_replacement(current=current, luna_high=luna, current_effort="medium")["verdict"] == "replace_ok"
    assert reporter.classify_role_replacement(current=current, luna_high=luna, current_effort=None)["verdict"] == "conditional"
    assert reporter.classify_role_replacement(
        current=current,
        luna_high=dict(luna, fatal_rate=0.34),
        current_effort="high",
    )["verdict"] == "keep_current"


def test_deepdive_proxy_measurement_cannot_produce_unconditional_replacement() -> None:
    reporter = require_reporter()
    guarded = reporter.apply_role_measurement_guard(
        role="deepdive",
        decision={"verdict": "replace_ok", "reason": "numeric noninferiority", "deltas": {}},
    )

    assert guarded["verdict"] == "conditional"
    assert "DeepDive専用" in guarded["reason"]


def test_report_is_decision_first_bar_chart_dominant_and_keeps_measurement_limits(tmp_path: Path) -> None:
    reporter = require_reporter()
    payload = {
        "generated_at": "2026-07-15T00:00:00+09:00",
        "complete": True,
        "model_labels": {"gpt-5.4": "M1", "gpt-5.6-luna-high": "M2"},
        "roles": [
            {
                "role": "reporter",
                "current": "gpt-5.4 / medium",
                "candidate": "gpt-5.6-luna / high",
                "verdict": "replace_ok",
                "reason": "品質と運用ゲートが非劣後",
                "metrics": {"current": 0.72, "candidate": 0.78},
            }
        ],
        "score_rows": [
            {"axis": "JA_SUMMARY", "values": {"gpt-5.4": 0.72, "gpt-5.6-luna-high": 0.78}}
        ],
        "case_rows": [{"case_id": "ja-summary-a", "purpose": "要約忠実性"}],
        "audits": [{"name": "3反復", "status": "pass", "detail": "全キー完備"}],
        "measurement_limits": ["News-Grasp固有DeepDive品質はproxy評価"],
    }
    output = reporter.render_report(payload, tmp_path / "report.html")
    html = output.read_text(encoding="utf-8")

    required_sections = [
        "Hero verdict",
        "Decision Matrix",
        "Score Explorer",
        "Usecase Winners",
        "Operational Gate",
        "Measurement Limit",
        "Evaluation Design",
        "Case Library",
        "Audits",
    ]
    assert all(section in html for section in required_sections)
    assert [html.index(section) for section in required_sections] == sorted(html.index(section) for section in required_sections)
    assert html.count('class="bar-track"') >= 2
    assert "M1" in html and "M2" in html
    assert "data-label-mode" in html
    assert 'class="label-toggle"' in html
    assert 'data-mode="symbol"' in html and 'data-mode="name"' in html
    assert "data-code-label" in html and "data-full-label" in html
    assert 'data-report-primary="true"' in html
    assert 'data-report-section="score-method"' in html
    assert "VRAM" in html
    assert "News-Grasp固有DeepDive品質はproxy評価" in html
    assert "linear-gradient" not in html


def test_cli_exposes_validate_inputs_and_render_subcommands() -> None:
    reporter = require_reporter()
    parser = reporter.build_parser()

    validate_args = parser.parse_args(
        [
            "validate-inputs",
            "--recovery-base",
            "recovery.json",
            "--external-base",
            "external.json",
            "--manifest-out",
            "manifest.json",
        ]
    )
    render_args = parser.parse_args(
        [
            "render",
            "--policy",
            "policy.py",
            "--recovery-base",
            "recovery.json",
            "--recovery-sol",
            "recovery-sol.json",
            "--external-base",
            "external.json",
            "--external-sol",
            "external-sol.json",
            "--out-dir",
            "out",
        ]
    )

    assert validate_args.command == "validate-inputs"
    assert render_args.command == "render"
