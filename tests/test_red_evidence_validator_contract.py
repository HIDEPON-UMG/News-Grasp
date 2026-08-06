from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.validate_autonomous_red_evidence import (
    assert_unique_normalizations,
    validate_consumer_sources,
)


def test_label_only_difference_is_rejected_as_semantic_duplicate() -> None:
    shared = "1" * 64
    with pytest.raises(ValueError, match="RED_NODE_SEMANTIC_DUPLICATE"):
        assert_unique_normalizations(
            [
                {"name": "test_label_a", "normalizationSha256": shared},
                {"name": "test_label_b", "normalizationSha256": shared},
            ]
        )


def test_one_byte_consumer_source_drift_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "consumer.py"
    source.write_bytes(b"a")
    declared = [
        {
            "path": str(source),
            "symbol": "consumer",
            "sha256": hashlib.sha256(b"a").hexdigest(),
        }
    ]
    source.write_bytes(b"b")
    with pytest.raises(ValueError, match="RED_NODE_CONSUMER_SOURCE_DRIFT"):
        validate_consumer_sources(name="test_drift", sources=declared)
