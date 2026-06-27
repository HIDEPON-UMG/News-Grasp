from __future__ import annotations

import json

from tools.auto_repair_orchestrator import main


def test_classify_reads_gate_output_file(tmp_path, capsys) -> None:
    output_file = tmp_path / "gate.log"
    output_file.write_text("404 Not Found\n", encoding="utf-8")

    rc = main(["classify", "--gate-id", "daily-quality", "--output-file", str(output_file)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "quarantine"
    assert payload["handler"] == "deterministic-repair"
    assert payload["handler_id"] == "url-quarantine-refill"
    assert payload["failure_status"] == "blocked_refill_unresolved"


def test_classify_routes_summary_emphasis_to_deterministic_handler(capsys) -> None:
    rc = main(
        [
            "classify",
            "--gate-id",
            "daily-quality",
            "--output",
            "Summary section lacks required emphasis",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "repairable"
    assert payload["handler"] == "deterministic-repair"
    assert payload["handler_id"] == "summary-emphasis-patch"
    assert payload["handler_kind"] == "deterministic"
    assert payload["failure_status"] == "blocked_repair_handler_unimplemented"
    assert "digest/Summary/{date}.md" in payload["allowed_artifacts"]
    assert payload["verify_gate"] == "daily-quality"
