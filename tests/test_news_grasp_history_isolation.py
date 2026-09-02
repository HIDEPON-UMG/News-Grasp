from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from tools import news_grasp_history_isolation as history


def test_history_period_is_fixed_and_inclusive() -> None:
    dates = history.period_dates()
    assert dates[0] == "2026-06-21"
    assert dates[-1] == "2026-08-31"
    assert len(dates) == 72


def test_history_quarantine_does_not_change_daily_authority() -> None:
    result = history.aggregate_history_audit([{"date": "2026-06-21", "status": "quarantine", "reasonCodes": ["fixture"]}], daily_manifest_ok=True)
    assert result["dailyAuthority"] == "verified"
    assert result["promoted"] == []
    assert result["quarantine"]


def test_inventory_output_inside_dirty_source_is_forbidden(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="inside_source"):
        history.write_inventory_outside_source(source, source / "inventory.json")


def test_audit_cli_requires_daily_manifest_receipt() -> None:
    """security Red: history CLIがdaily authorityを自己申告verifiedにしない。"""
    parser = history.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["audit-period", "--repo-root", ".", "--output", "out.json"])


def test_dirty_inventory_never_follows_symlink_outside_source(tmp_path: Path, monkeypatch) -> None:
    """dirty pathのresolve前追跡を禁止し、外部target bytesをhashしない。"""
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret outside bytes", encoding="utf-8")
    link = source / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    def fake_git(_root: Path, *args: str):
        if args[:2] == ("status", "--porcelain=v1"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout="?? link.txt\0", stderr="")
        if args == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(["git", *args], 0, stdout="a" * 40 + "\n", stderr="")
        return subprocess.CompletedProcess(["git", *args], 0, stdout="b" * 40 + "\trefs/heads/main\n", stderr="")

    monkeypatch.setattr(history, "_run_git", fake_git)
    receipt = history.inventory_dirty_checkout(source)
    assert receipt["paths"][0]["sha256"] == "unsafe"
    assert receipt["paths"][0]["sha256"] != __import__("hashlib").sha256(outside.read_bytes()).hexdigest()


def test_inventory_output_rejects_parent_junction_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.json"
    victim.write_text("DO_NOT_OVERWRITE", encoding="utf-8")
    link = tmp_path / "inventory-link"
    if os.name == "nt":
        created = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False, shell=False)
        if created.returncode != 0:
            pytest.skip("directory junction creation is unavailable")
    else:
        link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(history, "inventory_dirty_checkout", lambda _source: {"schemaVersion": history.SCHEMA})
    try:
        with pytest.raises(ValueError, match="history_output_reparse_forbidden"):
            history.write_inventory_outside_source(source, link / "victim.json")
        assert victim.read_text(encoding="utf-8") == "DO_NOT_OVERWRITE"
    finally:
        os.rmdir(link)
