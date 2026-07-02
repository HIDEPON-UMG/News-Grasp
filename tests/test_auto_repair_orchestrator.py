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
    assert payload["failure_status"] == "blocked_deterministic_repair_failed"
    assert "digest/Summary/{date}.md" in payload["allowed_artifacts"]
    assert payload["verify_gate"] == "daily-quality"


def test_classify_emits_ordered_issue_ledger_and_selected_artifacts(capsys) -> None:
    output = json.dumps(
        {
            "ok": False,
            "errors": [
                {"code": "audio_script_quality_invalid", "artifact": "digest/Summary/2026-06-28-audio-script.md"},
                {"code": "articles_issue_empty", "artifact": "data/articles.jsonl"},
            ],
        },
        ensure_ascii=False,
    )

    rc = main(["classify", "--gate-id", "generation-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_code"] == "articles_issue_empty"
    assert payload["artifact_paths"] == ["data/articles.jsonl"]
    assert [issue["issue_code"] for issue in payload["issues"]] == [
        "articles_issue_empty",
        "audio_script_quality_invalid",
    ]


def test_classify_routes_audio_script_quality_to_targeted_rewrite(capsys) -> None:
    output = json.dumps(
        {
            "ok": False,
            "errors": [
                {
                    "code": "audio_script_quality_invalid",
                    "artifact": "digest/Summary/2026-06-28-audio-script.md",
                    "reason": "論点設計メモ不足; 論点充足不足; 字数不足",
                },
            ],
        },
        ensure_ascii=False,
    )

    rc = main(["classify", "--gate-id", "generation-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "repairable"
    assert payload["handler"] == "targeted-repair"
    assert payload["handler_id"] == "audio-script-depth-rewrite"
    assert payload["failure_status"] == "blocked_audio_script_rewrite_failed"
