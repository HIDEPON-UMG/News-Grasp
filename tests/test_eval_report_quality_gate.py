from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT.parent
REPORT_GATE = Path.home() / ".codex" / "tools" / "report_quality_gate.py"


def test_generated_eval_report_passes_decision_report_quality_gate(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from tools import run_external_benchmark_matrix as matrix

    records = []
    for model in matrix.TARGET_MODELS:
        for effort in matrix.EFFORT_LEVELS:
            for case in matrix.build_matrix_cases():
                for repetition in range(1, matrix.MIN_REPETITIONS + 1):
                    records.append(
                        {
                            "model": model,
                            "effort": effort,
                            "task_type": case["task_type"],
                            "case_id": case["case_id"],
                            "pass": True,
                            "score": 10.0,
                            "fatal": False,
                            "credits": 1,
                            "messages": 1,
                            "repetition": repetition,
                        }
                    )
    summary = matrix.write_summary(tmp_path, records)
    report = matrix.generate_html_report(summary, tmp_path / "external-benchmark-report.html")

    result = subprocess.run(
        [sys.executable, str(REPORT_GATE), str(report)],
        cwd=str(PROJECT_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    html = report.read_text(encoding="utf-8")
    assert "意思決定者向けサマリ" in html
    assert "0/1" not in html
    assert ">0<" not in html
    assert "1-5 projection" in html
