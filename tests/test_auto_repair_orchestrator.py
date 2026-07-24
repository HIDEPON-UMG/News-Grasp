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
    assert payload["selected_artifacts"] == [
        "data/articles.jsonl",
        "digest/Summary/2026-06-28-audio-script.md",
    ]
    assert [issue["issue_code"] for issue in payload["issues"]] == [
        "articles_issue_empty",
        "audio_script_quality_invalid",
    ]


def test_classify_routes_search_audit_count_mismatch_to_metadata_patch(capsys) -> None:
    output = "\n".join(
        [
            "ERROR: opaque validator text without a registered issue code",
            "ERROR: data\\search_audit\\2026-07-06\\fx.json: selected_total=5 does not match digest article count 3.",
        ]
    )

    rc = main(["classify", "--gate-id", "daily-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "repairable"
    assert payload["handler"] == "deterministic-repair"
    assert payload["handler_id"] == "search-audit-metadata-patch"
    assert payload["issue_code"] == "search_audit_count_mismatch"
    assert payload["artifact_paths"] == ["data/search_audit/2026-07-06/fx.json"]
    assert [issue["issue_code"] for issue in payload["issues"]] == [
        "search_audit_count_mismatch",
        "unknown",
    ]


def test_classify_preserves_structured_artifact_after_warning_prefix(capsys) -> None:
    output = "WARNING: provenance annotation is missing\n" + json.dumps(
        {
            "ok": False,
            "gate_id": "daily-quality",
            "issues": [
                {
                    "gate_id": "daily-quality",
                    "issue_code": "unknown",
                    "message": "economy candidates_total=4; expected at least 5",
                    "issue_date": "2026-07-13",
                },
                {
                    "gate_id": "daily-quality",
                    "issue_code": "search_audit_count_mismatch",
                    "message": "fx selected_total=5 does not match digest article count 4",
                    "issue_date": "2026-07-13",
                    "artifact_paths": ["data/search_audit/2026-07-13/fx.json"],
                    "category": "fx",
                    "evidence": {"selected_total": 5, "digest_article_count": 4},
                },
            ],
        }
    )

    rc = main(["classify", "--gate-id", "daily-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_code"] == "search_audit_count_mismatch"
    assert payload["artifact_paths"] == ["data/search_audit/2026-07-13/fx.json"]


def test_classify_does_not_rescue_structured_unknown_from_message_prose(capsys) -> None:
    output = json.dumps(
        {
            "ok": False,
            "gate_id": "daily-quality",
            "issues": [
                {
                    "gate_id": "daily-quality",
                    "issue_code": "unknown",
                    "message": "https://example.invalid/article returned 404",
                    "issue_date": "2026-07-22",
                }
            ],
        }
    )

    rc = main(["classify", "--gate-id", "daily-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_code"] == "unknown"
    assert payload["failure_status"] == "blocked_unknown_repair_class"
    assert payload.get("handler_id") is None


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


def test_classify_pytest_static_failure_is_not_retry_budget(capsys) -> None:
    output = "\n".join(
        [
            "FAILED tests/test_external_benchmark_matrix.py::test_external_benchmark_matrix",
            "AssertionError: benchmark lesson drift",
        ]
    )

    rc = main(["classify", "--gate-id", "pytest-static", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "fatal"
    assert payload["handler"] == "fatal"
    assert payload["issue_code"] == "local_contract_failure"
    assert payload["failure_status"] == "blocked_local_contract_failure"
    assert "retry" not in json.dumps(payload, ensure_ascii=False)
