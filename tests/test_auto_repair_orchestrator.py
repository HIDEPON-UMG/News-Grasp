from __future__ import annotations

import json
from types import SimpleNamespace

import tools.auto_repair_orchestrator as orchestrator_module
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


def test_classify_fails_closed_when_repair_completeness_audit_is_not_green(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        orchestrator_module,
        "_audit_current_repair_system",
        lambda: SimpleNamespace(
            ok=False,
            findings=(
                SimpleNamespace(
                    code="handler_verify_gate_mismatch",
                    detail="daily-quality:thumb_invalid",
                ),
            ),
        ),
    )

    rc = main(
        [
            "classify",
            "--gate-id",
            "daily-quality",
            "--output",
            "thumbnail is missing",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["action"] == "fatal"
    assert payload["issue_code"] == "repair_system_incomplete"
    assert payload["failure_status"] == "blocked_repair_system_incomplete"
    assert payload["findings"][0]["code"] == "handler_verify_gate_mismatch"


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


def test_classify_repairs_recoverable_date_evidence_before_generating_missing_audio(capsys) -> None:
    """compound failureは既存artifactのdeterministic patchを先に収束させる。"""
    output = json.dumps(
        {
            "ok": False,
            "errors": [
                {
                    "code": "date_evidence_source_recoverable",
                    "artifact": "data/articles.jsonl",
                },
                {
                    "code": "missing_artifact",
                    "artifact": "digest/Summary/2026-08-01-audio-script.md",
                    "category": "Summary",
                },
            ],
        },
        ensure_ascii=False,
    )

    rc = main(["classify", "--gate-id", "generation-quality", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_code"] == "date_evidence_source_recoverable"
    assert payload["artifact_paths"] == ["data/articles.jsonl"]
    assert [issue["issue_code"] for issue in payload["issues"]] == [
        "date_evidence_source_recoverable",
        "missing_artifact",
    ]


def test_digest_reconcile_ledger_preserves_direction_and_selected_artifacts(capsys) -> None:
    output = json.dumps(
        {
            "ok": False,
            "gate_id": "digest-articles-reconcile",
            "issues": [
                {
                    "issue_code": "digest_articles_articles_only",
                    "direction": "articles_only",
                    "issue_date": "2026-07-27",
                    "category": "AI",
                    "artifact_paths": [
                        "digest/AI/2026-07-27-AI.md",
                        "tmp/newsroom/2026-07-27/ai.records.jsonl",
                    ],
                    "evidence": {
                        "direction": "articles_only",
                        "url": "https://example.com/missing",
                        "target_digest_path": "digest/AI/2026-07-27-AI.md",
                    },
                }
            ],
        },
        ensure_ascii=False,
    )

    rc = main(["classify", "--gate-id", "digest-articles-reconcile", "--output", output])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["issue_code"] == "digest_articles_articles_only"
    assert payload["evidence"]["direction"] == "articles_only"
    assert payload["selected_artifacts"] == [
        "digest/AI/2026-07-27-AI.md",
        "tmp/newsroom/2026-07-27/ai.records.jsonl",
    ]
    assert payload["issues"][0]["direction"] == "articles_only"
    assert payload["issues"][0]["evidence"]["target_digest_path"].endswith("2026-07-27-AI.md")


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


def test_matrix_scope_and_verify_gate_are_not_overwritten_by_shared_registry_handler() -> None:
    """matrix が所有する gate/scope を共有 handler metadata で広げない。"""
    from tools.auto_repair_orchestrator import classify

    daily_payload = classify(
        "daily-quality",
        json.dumps(
            {
                "ok": False,
                "issues": [
                    {
                        "issue_code": "category_card_emphasis_missing",
                        "issue_date": "2026-07-22",
                        "artifact_paths": ["digest/AI/2026-07-22-AI.md"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    record_payload = classify(
        "record-schema",
        "line 10: 必須キー欠落: 'thumb' (title='missing')",
    )

    assert daily_payload["verify_gate"] == "daily-quality"
    assert daily_payload["allowed_artifacts"] == ["digest/{category}/{date}-{category}.md"]
    assert record_payload["verify_gate"] == "record-schema"
    assert record_payload["allowed_artifacts"] == [
        "data/articles.jsonl",
        "data/search_audit/{date}",
    ]


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
