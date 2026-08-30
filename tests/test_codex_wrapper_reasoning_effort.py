"""direct 本線 automation のモデル・reasoning 契約テスト。"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
AUTOMATION_TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"


def test_repo_template_uses_luna_max_for_scheduled_direct_mainline() -> None:
    value = tomllib.loads(AUTOMATION_TEMPLATE.read_text(encoding="utf-8-sig"))

    assert value["model"] == "gpt-5.6-luna"
    assert value["reasoning_effort"] == "max"
    assert value["rrule"].upper() == "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"
    assert "$news-grasp-direct-mainline" in value["prompt"]


def test_direct_runtime_forbids_installed_config_override_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """caller提供TOMLを production authority にできない。"""

    api = __import__("tools.news_grasp_direct_runtime", fromlist=["_main"])
    stale = tmp_path / "automation.toml"
    stale.write_text('id = "news-grasp-6-40"\nreasoning_effort = "medium"\n', encoding="utf-8")
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("NEWS_GRASP_DIRECT_RUNTIME_ALLOW_TEST_INSTALLED_CONFIG", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(tmp_path / "state"),
            "--installed-config",
            str(stale),
        ],
    )

    assert api._main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "installed_config_override_forbidden"
    assert result["failures"] == ["installed_config_override_test_only"]


def test_syncer_default_projection_hardcodes_luna_max_without_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """installed projection は template から Luna/max direct 契約だけを描画する。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = __import__("tools.sync_news_grasp_codex_automation", fromlist=["sync"])
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed = fixture_root / "automation.toml"
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "\n".join(
            [
                'id = "news-grasp-6-40"',
                'kind = "cron"',
                'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"',
                'status = "ACTIVE"',
                'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
                'model = "gpt-5.6-luna"',
                'reasoning_effort = "medium"',
                'cwds = []',
                'created_at = 1',
                'updated_at = 1',
                'prompt = "old"',
            ]
        ),
        encoding="utf-8",
    )

    result = syncer.sync(
        repo_root=ROOT,
        template_path=AUTOMATION_TEMPLATE,
        installed_path=installed,
        allow_custom_paths=True,
    )

    assert result["ok"] is True
    value = tomllib.loads(installed.read_text(encoding="utf-8-sig"))
    assert value["model"] == "gpt-5.6-luna"
    assert value["reasoning_effort"] == "max"
    assert "news-grasp-runner.ps1" not in value["prompt"]
