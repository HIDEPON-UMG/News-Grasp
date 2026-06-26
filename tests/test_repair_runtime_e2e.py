from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.repair_runtime_e2e import (
    CompoundRepairStep,
    python_gate_command,
    run_compound_repair_plan,
    run_registry_repair_cycle,
)


def _write_article_without_title_ja(path: Path, issue: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": issue,
        "seen_at": f"{issue}T06:00:00+09:00",
        "genre": "AI",
        "title": "Anthropic launches Claude repair benchmark",
        "title_ja": "",
        "url": "https://example.com/claude-repair-benchmark",
        "url_norm": "example.com/claude-repair-benchmark",
        "source": "Example",
        "summary": "Repair benchmark article.",
        "thumb": "https://example.com/og.png",
        "entities": {"companies": ["Anthropic"], "countries": [], "services": [], "people": [], "tickers": []},
        "topics": ["AI"],
        "industries": ["AI"],
        "events": ["benchmark"],
        "tags": ["topic/AI"],
    }
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_record_schema_runtime_repair_cycle_reruns_same_gate(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_article_without_title_ja(articles, issue)

    result = run_registry_repair_cycle(
        repo_root=tmp_path,
        issue=issue,
        gate_id="record-schema",
        command=[
            sys.executable,
            "-m",
            "tools.validate_record",
            "--articles",
            str(articles),
            "--issue-date",
            issue,
            "--all",
        ],
        artifacts=["data/articles.jsonl"],
    )

    repaired = articles.read_text(encoding="utf-8")
    assert result.initial_exit_code == 1
    assert result.handler_id == "record-title-ja-patch"
    assert result.repair_status == "repaired"
    assert result.repair_changed is True
    assert result.post_repair_exit_code == 0
    assert result.final_status == "green_after_repair"
    assert '"title_ja": "Anthropic launches Claude repair benchmark"' in repaired


def test_python_gate_command_uses_current_interpreter() -> None:
    assert python_gate_command("tools.validate_record", "--all")[0] == sys.executable


def _write_valid_article(path: Path, issue: str, *, title_ja: str = "日本語タイトル", date: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "date": date or issue,
        "seen_at": f"{issue}T06:00:00+09:00",
        "genre": "AI",
        "title": "Compound repair validates abnormal paths",
        "title_ja": title_ja,
        "url": "https://example.com/compound-repair",
        "thumb": "https://example.com/compound.png",
    }
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def test_compound_repair_plan_requires_every_step_green(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_valid_article(articles, issue, title_ja="")
    gate = python_gate_command(
        "tools.validate_record",
        "--articles",
        str(articles),
        "--issue-date",
        issue,
        "--all",
    )

    result = run_compound_repair_plan(
        repo_root=tmp_path,
        issue=issue,
        no_publish=True,
        steps=[
            CompoundRepairStep(
                name="first-record-schema",
                gate_id="record-schema",
                command=gate,
                artifacts=["data/articles.jsonl"],
            ),
            CompoundRepairStep(
                name="second-record-schema",
                gate_id="record-schema",
                command=gate,
                artifacts=["data/articles.jsonl"],
            ),
        ],
    )

    assert result.final_status == "green_after_compound_repair"
    assert [step.final_status for step in result.steps] == ["green_after_repair", "already_green"]
    assert result.public_actions_attempted == ()
    assert '"title_ja": "Compound repair validates abnormal paths"' in articles.read_text(encoding="utf-8")


def test_compound_repair_plan_blocks_residual_failure_without_fallback(tmp_path: Path) -> None:
    issue = "2026-06-25"
    articles = tmp_path / "data" / "articles.jsonl"
    _write_valid_article(articles, issue, title_ja="", date="2026-06-24")

    result = run_compound_repair_plan(
        repo_root=tmp_path,
        issue=issue,
        no_publish=True,
        steps=[
            CompoundRepairStep(
                name="repairable-record-schema",
                gate_id="record-schema",
                command=python_gate_command(
                    "tools.validate_record",
                    "--articles",
                    str(articles),
                    "--issue-date",
                    issue,
                    "--all",
                ),
                artifacts=["data/articles.jsonl"],
            ),
        ],
    )

    assert result.final_status == "blocked_unresolved_compound_failure"
    assert result.steps[0].final_status == "still_red"
    assert result.steps[0].repair_status == "repaired"
    assert "号日不整合" in result.steps[0].post_repair_output
    assert result.public_actions_attempted == ()
