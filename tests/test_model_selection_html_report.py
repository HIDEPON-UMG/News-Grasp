from pathlib import Path
import re

import pytest


REPORT = Path("build/model-eval-5.6/model-selection-report.html")
pytestmark = pytest.mark.skipif(
    not REPORT.exists(),
    reason="model selection report is generated only by the dedicated benchmark workflow",
)


def test_model_selection_report_has_decisions_charts_costs_and_pending_banner() -> None:
    html = REPORT.read_text(encoding="utf-8")
    required = (
        "本番未反映",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "$9.0863",
        "25 / 25",
        "data-chart=\"reader-quality\"",
        "data-chart=\"latency\"",
        "data-chart=\"cost\"",
        "data-chart=\"deepdive-quality-score\"",
        "data-chart-kind=\"stacked\"",
        "data-axis-tone=\"editorial-muted\"",
        "data-table=\"deepdive-model-comparison\"",
        "総合品質点 = Σ（各軸平均点 × DeepDive重み）",
        "洞察 <strong>25%</strong>",
        "読者有用性 <strong>20%</strong>",
        "4.257",
        "3.917",
        "4.923",
        "9–1",
        "10–0",
        "API単価",
        "文章品質",
        "安全性",
        "data-section=\"daily-cost-projection\"",
        "$8.7155",
        "$11.9464",
        "+37.1%",
    )
    for sentinel in required:
        assert sentinel in html
    assert html.count("data-role-comparison=\"deepdive\"") == 2
    assert html.count("data-deepdive-model=") == 3
    assert "data-pair=\"deepdive-" not in html
    assert html.count("data-segment-label=") == 21
    assert html.count("data-cost-date=") == 2
    assert html.count("data-cost-category=") == 7
    weights = re.findall(r'data-axis-weight="([0-9]+)%"', html)
    assert sorted(int(value) for value in weights) == [5, 10, 10, 15, 15, 20, 25]


def test_model_selection_report_is_single_file_without_external_assets() -> None:
    html = REPORT.read_text(encoding="utf-8")
    assert "<svg" in html
    assert "<script src=" not in html
    assert "<link rel=" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_deepdive_stacks_sum_to_displayed_scores_with_unique_axis_colors() -> None:
    html = REPORT.read_text(encoding="utf-8")
    expected = {
        "gpt-5.5": 4.257,
        "gpt-5.6-terra": 3.917,
        "gpt-5.6-sol": 4.923,
    }
    for model, score in expected.items():
        match = re.search(rf'<g aria-label="{re.escape(model)} 軸別寄与">(.*?)</g>', html, re.S)
        assert match is not None
        widths = [float(value) for value in re.findall(r'width="([0-9.]+)"', match.group(1))]
        colors = re.findall(r'fill="var\((--color-axis-[^)]+)\)"', match.group(1))
        assert len(widths) == 7
        assert len(set(colors)) == 7
        assert abs(sum(widths) / 80 - score) < 0.003
