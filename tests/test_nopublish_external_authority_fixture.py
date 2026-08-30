from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import news_grasp_external_control as external_control


RUNNER = Path(__file__).parents[1] / "scripts" / "ops" / "news-grasp-runner.ps1"
NOPUBLISH_WRAPPER = (
    Path(__file__).parents[1]
    / "scripts"
    / "ops"
    / "invoke-scheduled-equivalent-nopublish.ps1"
)


def _fixture_authority(path: Path) -> Path:
    body = {
        "schemaVersion": "EXTERNAL_CONTROL_PLANE_HEALTH_AUTHORITY_V1",
        "authorityLineageId": "lineage-a",
        "authorityLineageDerivation": "sha256-utf8-lf-v1",
        "authorityGeneration": 1,
        "previousReceiptSha256": "0" * 64,
        "canonicalDescriptorPath": str(path.parent / "descriptor.json"),
        "canonicalDescriptorSha256": "1" * 64,
        "sourceBrokerPath": str(path.parent / "source-broker.py"),
        "sourceBrokerSha256": "2" * 64,
        "installedBrokerPath": str(path.parent / "installed-broker.py"),
        "installedBrokerSha256": "2" * 64,
        "dependencyGenerationHash": "3" * 64,
        "routeGenerationHash": "4" * 64,
        "ledgerGenerationId": "fixture-ledger",
        "registryAnchorGenerationId": "fixture-registry",
        "promotionGuardGenerationId": "fixture-promotion",
        "statefulSelfTestStatus": "green",
        "statefulSelfTestId": "fixture-self-test",
        "testedAt": "2026-08-12T00:00:00+00:00",
        "publisherId": "news-grasp-nopublish-fixture",
    }
    body["receiptSha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_nopublish_fixture_probe_is_explicit_and_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    authority = _fixture_authority(tmp_path / "authority.json")

    result = external_control.main(["probe", "--authority-path", str(authority), "--fixture-mode"])

    assert result == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "ready"
    assert observed["modelLaunchCount"] == 0


def test_nopublish_fixture_probe_binds_expected_raw_file_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    authority = _fixture_authority(tmp_path / "authority.json")
    expected = hashlib.sha256(authority.read_bytes()).hexdigest()

    assert external_control.main(
        [
            "probe",
            "--authority-path",
            str(authority),
            "--fixture-mode",
            "--expected-authority-sha256",
            expected,
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"

    assert external_control.main(
        [
            "probe",
            "--authority-path",
            str(authority),
            "--fixture-mode",
            "--expected-authority-sha256",
            "0" * 64,
        ]
    ) == 74
    assert json.loads(capsys.readouterr().out)["reasonCode"] == "EXTERNAL_AUTHORITY_HASH_DRIFT"


def test_authority_path_override_without_fixture_mode_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    authority = _fixture_authority(tmp_path / "authority.json")

    result = external_control.main(["probe", "--authority-path", str(authority)])

    assert result == 74
    observed = json.loads(capsys.readouterr().out)
    assert observed["reasonCode"] == "EXTERNAL_AUTHORITY_OVERRIDE_FORBIDDEN"


def test_direct_mainline_keeps_external_authority_surface_scoped() -> None:
    root = Path(__file__).parents[1]
    direct_runtime = (root / "tools/news_grasp_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    skill = (root / "automation/skills/news-grasp-direct-mainline/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert not RUNNER.exists()
    assert "external_failure" in direct_runtime
    assert "quota" in direct_runtime
    assert "OAuth" in skill
    assert "NoPublish" in skill


def test_final_wrapper_binds_fixture_path_into_exact_runner_arguments() -> None:
    wrapper = NOPUBLISH_WRAPPER.read_text(encoding="utf-8-sig")
    assert "ExternalHealthAuthorityFixturePath" in wrapper
    assert "-ExternalHealthAuthorityPathOverride" in wrapper
    assert "fixture" in wrapper.lower()
