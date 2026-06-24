from __future__ import annotations

import json

from tools.validate_batch_slo import validate_usage_log


def _write_jsonl(path, records) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def test_usage_log_rejects_token_budget_violation(tmp_path) -> None:
    usage = tmp_path / "usage.jsonl"
    _write_jsonl(
        usage,
        [
            {"timestamp": "2026-06-24T08:00:00+09:00", "flow": "reporter:fx", "tokens_used": 1_900_000},
            {"timestamp": "2026-06-24T08:10:00+09:00", "flow": "reporter:ai", "tokens_used": 1_900_000},
        ],
    )

    errors = validate_usage_log(usage, max_total_tokens=3_000_000, max_window_sec=3600)

    assert any("SLO token budget exceeded" in error for error in errors)


def test_usage_log_rejects_duration_budget_violation(tmp_path) -> None:
    usage = tmp_path / "usage.jsonl"
    _write_jsonl(
        usage,
        [
            {"timestamp": "2026-06-24T08:00:00+09:00", "flow": "reporter:fx", "tokens_used": 10},
            {"timestamp": "2026-06-24T10:01:00+09:00", "flow": "newsroom_editor", "tokens_used": 10},
        ],
    )

    errors = validate_usage_log(usage, max_total_tokens=3_000_000, max_window_sec=3600)

    assert any("SLO duration budget exceeded" in error for error in errors)


def test_usage_log_can_scope_duration_to_current_runner_start(tmp_path) -> None:
    """同一 issue date の過去試行を resume/no-publish の SLO 実測に混ぜない。"""
    usage = tmp_path / "usage.jsonl"
    _write_jsonl(
        usage,
        [
            {"timestamp": "2026-06-24T23:34:00+09:00", "flow": "reporter:fx", "tokens_used": 500_000},
            {"timestamp": "2026-06-25T02:01:40+09:00", "flow": "resume:post-deepdive", "tokens_used": 10},
            {"timestamp": "2026-06-25T02:05:16+09:00", "flow": "resume:post-deepdive", "tokens_used": 10},
        ],
    )

    errors = validate_usage_log(
        usage,
        max_total_tokens=3_000_000,
        max_window_sec=3600,
        since="2026-06-25T02:01:29+09:00",
    )

    assert not errors


def test_usage_log_scoped_duration_still_rejects_current_run_violation(tmp_path) -> None:
    """since 以降だけでも 1時間を超えた run は publish 前に止める。"""
    usage = tmp_path / "usage.jsonl"
    _write_jsonl(
        usage,
        [
            {"timestamp": "2026-06-24T23:34:00+09:00", "flow": "previous", "tokens_used": 10},
            {"timestamp": "2026-06-25T02:01:40+09:00", "flow": "reporter:fx", "tokens_used": 10},
            {"timestamp": "2026-06-25T03:05:16+09:00", "flow": "newsroom_editor", "tokens_used": 10},
        ],
    )

    errors = validate_usage_log(
        usage,
        max_total_tokens=3_000_000,
        max_window_sec=3600,
        since="2026-06-25T02:01:29+09:00",
    )

    assert any("SLO duration budget exceeded" in error for error in errors)
