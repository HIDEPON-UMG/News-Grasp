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
    assert payload["handler"] == "quarantine-refill"
