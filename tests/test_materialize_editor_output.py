from __future__ import annotations

import json
import hashlib
import threading
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.materialize_editor_output import (
    MaterializationError,
    materialize_editor_output,
    recover_editor_materialization,
)


ISSUE_DATE = "2026-08-04"


def _payload(*, valid: bool = True) -> dict:
    lead = "日米の為替介入と企業決算を軸に、政策と事業への影響を整理する。" * 8
    summary = (
        "---\n"
        "title: 'News Grasp #20260804 — 日米が円買い介入、ドル円は155円台へ'\n"
        "date: 2026-08-04\n"
        "issue: 20260804\n"
        "weekday: 火曜日\n"
        "edition: Morning Edition\n"
        "publisher: News Grasp\n"
        "category: Summary\n"
        "categoryId: summary\n"
        "hero_headline: '日米が円買い介入、ドル円は155円台へ'\n"
        "theme: '日米の協調介入が円相場と金融政策へ与える影響を追う。'\n"
        "categories: [fx]\n"
        "tags: [fx]\n"
        "sections: [fx]\n"
        "---\n"
        f"## § 本日のテーマ考察\n\n> {lead}\n"
    )
    if not valid:
        summary = "### ⛔ 生成前に中断"
    return {
        "issue_date": ISSUE_DATE,
        "inputs": {
            "reporter_artifacts": [],
            "dedup_file": "build/deduped-candidates",
            "source_policy": "no_recollection",
        },
        "append_records": [
            {
                "date": ISSUE_DATE,
                "genre": "FX",
                "title": "日米が円買い介入",
                "title_ja": "日米が円買い介入、ドル円は155円台へ",
                "url": "https://example.com/fx-intervention",
                "source": "Example",
                "summary": "日米の協調介入で円相場が動いた。",
                "bullets": ["事実", "背景", "展望"],
            }
        ],
        "summary_markdown": summary,
    }


def _write_source(path: Path, *, valid: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_payload(valid=valid), ensure_ascii=False), encoding="utf-8")


def test_materializes_only_semantically_valid_output(tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source)

    receipt = materialize_editor_output(
        source=source,
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
    )

    summary = tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md"
    preview = (
        tmp_path
        / "build"
        / "reporter-artifacts"
        / ISSUE_DATE
        / "editor-output.preview.json"
    )
    summary_text = summary.read_text(encoding="utf-8")
    assert summary_text.startswith("---\n")
    assert "## § 本日のテーマ考察" in summary_text
    assert json.loads(preview.read_text(encoding="utf-8"))["issue_date"] == ISSUE_DATE
    assert receipt["appendedCount"] == 1
    assert receipt["status"] == "materialized_validated_editor_output"


def test_invalid_output_is_fail_closed_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source, valid=False)

    with pytest.raises(MaterializationError, match="EDITOR_OUTPUT_SEMANTIC_INVALID"):
        materialize_editor_output(
            source=source,
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
        )

    assert not (tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md").exists()
    assert not (tmp_path / "data" / "articles.jsonl").exists()


def test_replay_does_not_duplicate_article_records(tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source)

    first = materialize_editor_output(
        source=source,
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
    )
    second = materialize_editor_output(
        source=source,
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
    )

    lines = (tmp_path / "data" / "articles.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert first["appendedCount"] == 1
    assert second["appendedCount"] == 0
    assert second["duplicateCount"] == 1


@pytest.mark.parametrize("issue_date", ["../outside", "2026-8-4", "C:\\outside", "//server/share"])
def test_issue_date_path_substitution_is_rejected_without_mutation(
    tmp_path: Path, issue_date: str
) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    payload = _payload()
    payload["issue_date"] = issue_date
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(MaterializationError, match="EDITOR_OUTPUT_ISSUE_DATE_INVALID"):
        materialize_editor_output(source=source, repo_root=tmp_path, issue_date=issue_date)

    assert not (tmp_path.parent / "outside").exists()
    assert not (tmp_path / "digest").exists()


def test_source_hash_is_bound_to_the_parsed_bytes(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source)
    original = source.read_bytes()
    from tools import materialize_editor_output as module

    validator = module.validate_editor_output_preview

    def mutate_after_parse(candidate: Path, *, issue_date: str):
        source.write_text('{"replaced":true}', encoding="utf-8")
        return validator(candidate, issue_date=issue_date)

    monkeypatch.setattr(module, "validate_editor_output_preview", mutate_after_parse)
    receipt = materialize_editor_output(
        source=source, repo_root=tmp_path, issue_date=ISSUE_DATE
    )

    assert receipt["sourceSha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["sourcePath"] == "build/codex-last-message.txt"


def test_oversized_source_is_rejected_before_output_mutation(tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"{" + b" " * (8 * 1024 * 1024) + b"}")

    with pytest.raises(MaterializationError, match="EDITOR_OUTPUT_SOURCE_TOO_LARGE"):
        materialize_editor_output(source=source, repo_root=tmp_path, issue_date=ISSUE_DATE)

    assert not (tmp_path / "digest").exists()


def test_partial_write_failure_rolls_back_all_outputs(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source)
    from tools import materialize_editor_output as module

    original_writer = module._atomic_write_bytes
    writes = 0

    def fail_second_write(path: Path, data: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected transaction failure")
        original_writer(path, data)

    monkeypatch.setattr(module, "_atomic_write_bytes", fail_second_write)
    with pytest.raises(OSError, match="injected transaction failure"):
        materialize_editor_output(source=source, repo_root=tmp_path, issue_date=ISSUE_DATE)

    assert not (tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md").exists()
    assert not (tmp_path / "data" / "articles.jsonl").exists()
    assert not (
        tmp_path / "build" / "reporter-artifacts" / ISSUE_DATE / "editor-output.preview.json"
    ).exists()


def test_concurrent_materialization_does_not_lose_article_append(
    monkeypatch, tmp_path: Path
) -> None:
    from tools import materialize_editor_output as module

    original_sha256 = module._sha256
    preview_hash_barrier = threading.Barrier(2)

    def synchronize_unlocked_preview_hash(path: Path) -> str:
        if path.name == "editor-output.preview.json":
            preview_hash_barrier.wait(timeout=10)
        return original_sha256(path)

    monkeypatch.setattr(module, "_sha256", synchronize_unlocked_preview_hash)
    sources = []
    expected_preview_sha = []
    for index in range(2):
        source = tmp_path / "build" / f"codex-last-message-{index}.txt"
        payload = _payload()
        payload["append_records"][0]["url"] = f"https://example.com/item-{index}"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        sources.append(source)
        canonical = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        expected_preview_sha.append(hashlib.sha256(canonical).hexdigest())

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(
            executor.map(
                lambda source: materialize_editor_output(
                    source=source, repo_root=tmp_path, issue_date=ISSUE_DATE
                ),
                sources,
            )
        )

    records = [
        json.loads(line)
        for line in (tmp_path / "data" / "articles.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {record["url"] for record in records} == {
        "https://example.com/item-0",
        "https://example.com/item-1",
    }
    assert sum(receipt["appendedCount"] for receipt in receipts) == 2
    assert [receipt["previewSha256"] for receipt in receipts] == expected_preview_sha


def test_interrupted_transaction_is_recovered_on_restart(tmp_path: Path) -> None:
    source = tmp_path / "build" / "codex-last-message.txt"
    _write_source(source)
    child = (
        "import os\n"
        "from pathlib import Path\n"
        "from tools import materialize_editor_output as m\n"
        f"source=Path({str(source)!r})\n"
        f"root=Path({str(tmp_path)!r})\n"
        "original=m._atomic_write_bytes\n"
        "def crash_after_summary(path,data):\n"
        "    original(path,data)\n"
        "    if path.name.endswith('.md'):\n"
        "        os._exit(77)\n"
        "m._atomic_write_bytes=crash_after_summary\n"
        f"m.materialize_editor_output(source=source,repo_root=root,issue_date={ISSUE_DATE!r})\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", child],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        timeout=30,
    )
    assert completed.returncode == 77

    from tools import materialize_editor_output as module

    module._recover_pending_transaction(tmp_path.resolve(), ISSUE_DATE)
    assert not (tmp_path / "digest" / "Summary" / f"{ISSUE_DATE}.md").exists()
    assert not (tmp_path / "data" / "articles.jsonl").exists()
    assert not (
        tmp_path / "build" / "reporter-artifacts" / ISSUE_DATE / "editor-output.preview.json"
    ).exists()


def test_recover_only_rejects_forged_commit_marker(tmp_path: Path) -> None:
    transaction = (
        tmp_path / "build" / "transactions" / f"editor-materialize-{ISSUE_DATE}"
    )
    transaction.mkdir(parents=True)
    manifest = {
        "schemaVersion": "EDITOR_MATERIALIZATION_TRANSACTION_V1",
        "issueDate": ISSUE_DATE,
        "entries": [],
    }
    (transaction / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (transaction / "commit.json").write_text(
        json.dumps(
            {
                "schemaVersion": "EDITOR_MATERIALIZATION_COMMIT_V1",
                "manifestSha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MaterializationError, match="EDITOR_OUTPUT_TRANSACTION_INVALID"):
        recover_editor_materialization(repo_root=tmp_path, issue_date=ISSUE_DATE)


def test_recover_only_requires_manifest_schema_and_exact_output_set(tmp_path: Path) -> None:
    transaction = (
        tmp_path / "build" / "transactions" / f"editor-materialize-{ISSUE_DATE}"
    )
    transaction.mkdir(parents=True)
    manifest = {
        "schemaVersion": "FORGED_SCHEMA",
        "issueDate": ISSUE_DATE,
        "entries": [
            {"path": f"build/reporter-artifacts/{ISSUE_DATE}/editor-output.preview.json", "existed": False},
            {"path": f"digest/Summary/{ISSUE_DATE}.md", "existed": False},
        ],
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    (transaction / "manifest.json").write_bytes(manifest_bytes)
    (transaction / "commit.json").write_text(
        json.dumps(
            {
                "schemaVersion": "EDITOR_MATERIALIZATION_COMMIT_V1",
                "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "outputSha256": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MaterializationError, match="EDITOR_OUTPUT_TRANSACTION_INVALID"):
        recover_editor_materialization(repo_root=tmp_path, issue_date=ISSUE_DATE)
