from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.news_grasp_release_reflection import (
    ReleaseReflectionError,
    classify_push_paths,
    create_release_reflection_receipt,
    validate_release_reflection_receipt,
)


def _evidence(seed: str, status: str = "green") -> dict[str, str]:
    return {
        "status": status,
        "evidenceSha256": hashlib.sha256(seed.encode()).hexdigest() if status == "green" else "",
    }


def _state() -> dict[str, dict[str, str]]:
    return {
        name: _evidence(name)
        for name in (
            "remoteHeadVerified",
            "installed",
            "installedSkillsFresh",
            "runtimeGenerationFresh",
            "scheduledTaskParity",
            "publicSurface",
        )
    }


def test_release_reflection_classifies_unknown_and_runtime_paths_fail_closed() -> None:
    assert classify_push_paths(["tests/test.py", "docs/index.html"]) == "public-content-only"
    assert classify_push_paths(["scripts/ops/news-grasp-runner.ps1"]) == "source-runtime-impacting"
    with pytest.raises(ReleaseReflectionError, match="RELEASE_IMPACT_CLASS_UNKNOWN"):
        classify_push_paths(["unclassified/file.bin"])


def test_release_reflection_requires_single_producer_and_runtime_parity() -> None:
    receipt = create_release_reflection_receipt(
        impact_class="source-runtime-impacting",
        source_commit="a" * 40,
        remote_head="a" * 40,
        target_ref="refs/heads/fix/news-grasp-0640-operation-v1",
        evidence=_state(),
    )
    assert validate_release_reflection_receipt(receipt)["l8Mode"] == "consume-only"
    duplicate = dict(receipt, producerInvocationCount=2)
    with pytest.raises(ReleaseReflectionError, match="RELEASE_REFLECTION_DUPLICATE_PRODUCER"):
        validate_release_reflection_receipt(duplicate)
    missing = _state()
    missing["runtimeGenerationFresh"] = _evidence("", "pending")
    with pytest.raises(ReleaseReflectionError, match="TRUSTED_RUNTIME_REFLECTION_REQUIRED"):
        create_release_reflection_receipt(
            impact_class="source-runtime-impacting",
            source_commit="a" * 40,
            remote_head="a" * 40,
            target_ref="refs/heads/main",
            evidence=missing,
        )


def test_release_reflection_cli_is_consume_only(tmp_path: Path) -> None:
    receipt = create_release_reflection_receipt(
        impact_class="internal-only",
        source_commit="b" * 40,
        remote_head="b" * 40,
        target_ref="refs/heads/fix/news-grasp-0640-operation-v1",
        evidence=_state(),
    )
    path = tmp_path / "release-reflection.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(Path(__file__).parents[1] / "tools" / "news_grasp_release_reflection.py"), "validate", "--receipt", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["receiptSha256"] == receipt["receiptSha256"]
