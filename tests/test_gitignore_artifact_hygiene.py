from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_evidence_and_repair_artifacts_are_gitignored() -> None:
    """実装後の検証・復旧証跡を staging 候補に残さない。"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    required_patterns = {
        "build/*.png",
        "build/publish-complete/",
        "build/quarantine/",
        "build/repair-transactions/",
        "build/*_tmp.py",
        "build/*-final-draft.md",
        "build/*-preview.mp3",
        "build/*_analyze.wav",
        "build/*-bgm.wav",
    }

    missing = sorted(pattern for pattern in required_patterns if pattern not in gitignore)
    assert missing == []
