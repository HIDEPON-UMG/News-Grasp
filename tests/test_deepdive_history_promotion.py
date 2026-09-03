"""履歴 DeepDive promotion の凍結済みRelease fixture。

対象は実運用で使用したPowerShell consumerの追跡済み凍結bytesとし、temp repo 内だけに
stage/canonical の最小履歴を構成する。8 日分の claim-source / bundle を欠落
させても promotion は全 70 件を一括検証でき、成功時には全 target が stage
bytes と一致する、という acceptance を固定する。
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SCRIPT = (
    ROOT
    / "tests"
    / "fixtures"
    / "deepdive-history-promotion"
    / "promote_selected_history.ps1"
)

DAYS = (
    "2026-08-18",
    "2026-08-19",
    "2026-08-20",
    "2026-08-21",
    "2026-08-22",
    "2026-08-23",
    "2026-08-24",
    "2026-08-26",
    "2026-08-30",
    "2026-08-31",
)
RELATIVE_TEMPLATES = (
    "digest\\DeepDive\\{0}-DeepDive.md",
    "digest\\DeepDive\\{0}-DeepDive-dialogue.md",
    "data\\deepdive-quality-review\\{0}.json",
    "data\\deepdive-provenance\\{0}.json",
    "data\\deepdive-claim-source\\{0}.json",
    "data\\deepdive-bundles\\{0}.json",
    "docs\\deepdive\\{0}\\index.html",
)
MISSING_DAYS = frozenset(DAYS[:8])
MISSING_TEMPLATES = frozenset(
    {
        "data\\deepdive-claim-source\\{0}.json",
        "data\\deepdive-bundles\\{0}.json",
    }
)


def _relative_path(root: Path, relative: str) -> Path:
    """PowerShell の Windows 相対表記を temp repo の Path へ変換する。"""
    return root.joinpath(*relative.split("\\"))


def test_history_promotion_expected_green_fixture_current_script_is_red(
    tmp_path: Path,
) -> None:
    """70件 promotion の成功 oracle を現行 script で実測する（変更前 Red）。"""
    temp_repo = tmp_path / "News-Grasp"
    copied_script = (
        temp_repo / "build" / "deepdive-history-remediation" / FIXTURE_SCRIPT.name
    )
    copied_script.parent.mkdir(parents=True)
    copied_script.write_bytes(FIXTURE_SCRIPT.read_bytes())

    summary_path = (
        temp_repo
        / "data"
        / "deepdive-history-remediation"
        / "2026-08-18-to-2026-08-31-stage-materialization.json"
    )
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps(
            {
                "schemaVersion": "DEEPDIVE_HISTORY_STAGE_MATERIALIZATION_V1",
                "status": "Green",
                "actualIssueDates": list(DAYS),
            }
        ),
        encoding="utf-8",
    )

    stage_root = temp_repo / "data" / "deepdive-history-remediation" / "stage"
    source_paths: dict[str, Path] = {}
    target_paths: dict[str, Path] = {}
    for day in DAYS:
        for template in RELATIVE_TEMPLATES:
            relative = template.format(day)
            source = _relative_path(stage_root / day, relative)
            target = _relative_path(temp_repo, relative)
            source.parent.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(f"stage source::{relative}\n".encode("utf-8"))
            source_paths[relative] = source
            target_paths[relative] = target
            if day in MISSING_DAYS and template in MISSING_TEMPLATES:
                continue
            target.write_bytes(f"existing canonical::{relative}\n".encode("utf-8"))

    pwsh = shutil.which("pwsh")
    assert pwsh is not None, "pwsh が必要な production consumer fixture です"
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(copied_script)],
        cwd=str(temp_repo),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        "Expected Green: promotion must resolve all 70 targets before copying; "
        f"exit={result.returncode}, stderr={result.stderr!r}, stdout={result.stdout!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["schemaVersion"] == "DEEPDIVE_HISTORY_PROMOTION_V1"
    assert payload["status"] == "promoted"
    assert payload["days"] == list(DAYS)
    assert payload["fileCount"] == 70

    for relative, source in source_paths.items():
        target = target_paths[relative]
        assert target.is_file(), f"promotion target missing: {relative}"
        assert target.read_bytes() == source.read_bytes(), (
            f"promotion bytes differ from stage source: {relative}"
        )
