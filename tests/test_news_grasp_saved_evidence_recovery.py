"""証拠欠落を本文Redへ誤分類した既存runの局所回復境界。"""
from types import SimpleNamespace
from unittest.mock import MagicMock
import hashlib
import json

import pytest

from tools import deepdive_quality as quality
from tools import news_grasp_daily_content as content
from tools import news_grasp_direct_runtime as runtime


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    issue = "2026-09-05"
    payload = {"article_markdown": "保存本文\n", "dialogue_markdown": "保存対談\n"}
    folder = tmp_path / "digest" / "DeepDive"
    folder.mkdir(parents=True)
    for suffix, key in (("", "article_markdown"), ("-dialogue", "dialogue_markdown")):
        (folder / f"{issue}-DeepDive{suffix}.md").write_bytes(payload[key].encode())
    failure = {"stage": "current_issue_integration", "predicateId": "deepdive_current_issue_audit", "reasonCode": "POST_QUALITY:deepdive_current_issue_red"}
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (1,)
    ledger = SimpleNamespace(store=SimpleNamespace(connect=lambda: connection, now=lambda: "now"),
        run_id="saved-run", writer_lease="writer", fencing_token=6,
        list_checkpoints=lambda: {"deepdive_model": {"status": "Red", "failure": failure, "payload": payload, "inputHash": "a" * 64}})
    audited = [folder / f"{issue}-DeepDive.md"]
    for directory in ("deepdive-provenance", "deepdive-quality-review"):
        path = tmp_path / "data" / directory / f"{issue}.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b'{}')
        if directory == "deepdive-quality-review":
            path.write_text(json.dumps({"artifacts": {"dialogue": {
                "path": f"digest/DeepDive/{issue}-DeepDive-dialogue.md",
                "sha256": hashlib.sha256(payload["dialogue_markdown"].encode()).hexdigest()}}}), encoding="utf-8")
        audited.append(path)
    audit = MagicMock(return_value={"status": "Green", "issues": [], "issueCodes": [],
        "auditedFiles": [{"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in audited]})
    monkeypatch.setattr(quality, "audit_issue", audit)
    monkeypatch.setattr(content, "_validate_deepdive", lambda value, **kwargs: value)
    checkpoint = MagicMock()
    journal = MagicMock()
    monkeypatch.setattr(content, "_write_artifact_checkpoint", checkpoint)
    monkeypatch.setattr(runtime, "record_timing_event", journal)
    def invoke():
        return content._recover_missing_evidence_failure(tmp_path, ledger, issue_date=issue,
            allowed_urls=set(), editor_hash="editor")
    return SimpleNamespace(invoke=invoke, audit=audit, checkpoint=checkpoint, journal=journal,
        connection=connection, folder=folder, failure=failure, payload=payload)


def test_green_saved_payload_recovers_and_preserves_original_failure(recovery):
    assert recovery.invoke() is True
    assert recovery.checkpoint.call_args.kwargs["payload"] == recovery.payload
    assert recovery.journal.call_args.kwargs["evidence"]["originalFailure"] == recovery.failure


def test_missing_evidence_cannot_restore_checkpoint(recovery):
    recovery.audit.return_value = {"status": "Red", "issues": ["DEEPDIVE_QUALITY_REVIEW_MISSING"]}
    with pytest.raises(content.DailyContentError, match="QUALITY_PENDING"):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()
    recovery.journal.assert_not_called()


def test_changed_article_cannot_restore_checkpoint(recovery):
    (recovery.folder / "2026-09-05-DeepDive.md").write_text("差替え", encoding="utf-8")
    with pytest.raises(content.DailyContentError):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()


def test_missing_completed_model_receipt_cannot_restore_checkpoint(recovery):
    recovery.connection.execute.return_value.fetchone.return_value = None
    with pytest.raises(content.DailyContentError):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()
    recovery.journal.assert_not_called()


def test_missing_audited_hash_cannot_restore_checkpoint(recovery):
    recovery.audit.return_value.pop("auditedFiles")
    with pytest.raises(content.DailyContentError):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()


def test_changed_review_after_audit_cannot_restore_checkpoint(recovery):
    review = recovery.folder.parents[1] / "data/deepdive-quality-review/2026-09-05.json"
    review.write_bytes(b'{"changed":true}')
    with pytest.raises(content.DailyContentError):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()


def test_completed_receipt_must_belong_to_preserved_input(recovery):
    assert recovery.invoke() is True
    sql, parameters = recovery.connection.execute.call_args.args
    assert "input_hash=?" in sql
    assert "a" * 64 in parameters


def test_review_of_other_dialogue_cannot_restore_checkpoint(recovery):
    review = recovery.folder.parents[1] / "data/deepdive-quality-review/2026-09-05.json"
    raw = json.loads(review.read_text(encoding="utf-8"))
    raw["artifacts"]["dialogue"]["sha256"] = "0" * 64
    review.write_text(json.dumps(raw), encoding="utf-8")
    for item in recovery.audit.return_value["auditedFiles"]:
        if item["path"] == str(review.resolve()):
            item["sha256"] = hashlib.sha256(review.read_bytes()).hexdigest()
    with pytest.raises(content.DailyContentError):
        recovery.invoke()
    recovery.checkpoint.assert_not_called()


def test_checkpoint_failure_does_not_record_success(recovery):
    recovery.checkpoint.side_effect = RuntimeError("checkpoint write failed")
    with pytest.raises(RuntimeError):
        recovery.invoke()
    assert recovery.journal.call_args.kwargs["evidence"]["event"] == "saved_evidence_recovery_validated"
