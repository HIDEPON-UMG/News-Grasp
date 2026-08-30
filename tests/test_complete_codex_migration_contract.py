#!/usr/bin/env python3
"""News-Grasp direct 本線への Codex 移行 Acceptance 契約テスト。"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AUTOMATION_TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"
DIRECT_RUNTIME = ROOT / "tools" / "news_grasp_direct_runtime.py"
DIRECT_COMPLETION = ROOT / "tools" / "news_grasp_direct_completion.py"


def test_legacy_runner_is_not_an_active_execution_or_completion_authority() -> None:
    """旧 runner 実体・NoPublish・readiness を direct completion authority に戻さない。"""

    assert not (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").exists()
    assert not (Path.home() / "bin" / "news-grasp-runner.ps1").exists()

    runtime_text = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
    completion_text = DIRECT_COMPLETION.read_text(encoding="utf-8-sig")
    combined = runtime_text + "\n" + completion_text

    assert "runner_state_path" not in combined
    assert "news-grasp-runner.ps1" not in combined
    assert "news_grasp_runner.py" not in combined
    assert "NoPublish" not in completion_text


def test_direct_automation_contract_contains_title_quality_and_completion_gates() -> None:
    value = tomllib.loads(AUTOMATION_TEMPLATE.read_text(encoding="utf-8-sig"))
    prompt = value["prompt"]

    assert value["model"] == "gpt-5.6-luna"
    assert value["reasoning_effort"] == "max"
    for marker in [
        "$news-grasp-direct-mainline",
        "YY/MM/DD",
        "title_status=already_ok",
        "post_publish_issue_list",
        "--require-deepdive",
        "direct completion guard",
        "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1",
        "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
    ]:
        assert marker in prompt
    assert "TT26/" in prompt


def test_direct_runtime_stage_inventory_replaces_runner_stage_contract() -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")

    assert len(api.DIRECT_STAGES) == 21
    assert api.DIRECT_STAGES[0] == "title_control"
    assert api.DIRECT_STAGES[12] == "daily_quality"
    assert api.DIRECT_STAGES[-1] == "public_completion"
    assert "youtube_podcasts" in api.DIRECT_STAGES
    assert "pages_verify" in api.DIRECT_STAGES


def test_direct_public_completion_uses_consumer_owned_verifier_schema() -> None:
    text = DIRECT_COMPLETION.read_text(encoding="utf-8-sig")

    assert "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1" in text
    assert "verify_direct_public_completion" in text
    assert "url_200_only" not in text.lower()
    assert "runner_state" not in text.lower()
