"""旧実装が範囲外提案を拒否して失ったEditor修正を、保存結果だけで復帰する。"""
from __future__ import annotations

import hashlib
import json
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import news_grasp_daily_content as content
from tools import news_grasp_direct_runtime as runtime
from tools.news_grasp_repair_registry import failure_signature


def _bytes(path: Path) -> bytes:
    value = content._read_bounded_model_events(path)
    if value is None:
        raise content.ModelResultPending("saved_scope_file_unavailable")
    return value


def read_completed_output(directory: Path, expected_intent: Mapping[str, Any]) -> tuple[dict, bytes]:
    """単一readのrawを、同callのintentと完了イベントの最終出力へ照合する。"""
    try:
        intent = json.loads(_bytes(directory / "intent.json"))
        raw = _bytes(directory / "raw.json")
        value = json.loads(raw)
        events = [json.loads(line) for line in _bytes(directory / "editor.events.jsonl").splitlines() if line.strip()]
        messages = [row["item"]["text"] for row in events
                    if row.get("type") == "item.completed" and isinstance(row.get("item"), dict)
                    and row["item"].get("type") == "agent_message"]
        if (intent != dict(expected_intent) or not isinstance(value, dict) or not events
            or events[-1].get("type") != "turn.completed" or any(row.get("type") == "turn.failed" for row in events)
            or not messages or json.loads(messages[-1]) != value):
            raise ValueError("binding")
        return value, raw
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise content.ModelResultPending("saved_scope_output_binding") from exc


def recover_saved_editor(*, repo_root: Path, ledger: Any, reporters: Sequence[Mapping[str, Any]],
                         reporter_hashes: Sequence[str]) -> dict[str, Any] | None:
    """canonical行に束縛された旧scope拒否結果だけを、現writerで局所採用する。"""
    ledger.assert_writer()
    with closing(ledger.store.connect()) as db:
        rows = [dict(row) for row in db.execute(
            "SELECT call_id,input_hash,budget_class,status,failure_code FROM daily_model_calls "
            "WHERE run_id=? AND artifact_id='editor' ORDER BY started_at", (ledger.run_id,),
        )]
    initial_input = content._artifact_input_hash({
        "issueDate": ledger.issue_date, "reporterOutputHashes": list(reporter_hashes), "repairFailureSignature": None,
    })
    initial_id = content._sha256_bytes(f"initial|editor|{initial_input}".encode())
    original_row = next((row for row in rows if row["call_id"] == initial_id
                         and row["input_hash"] == initial_input and row["budget_class"] == "initial"
                         and row["status"] == "failed"), None)
    if original_row is None:
        return None
    candidates = [row for row in rows if row["budget_class"] == "repair" and row["status"] == "failed"
                  and row["failure_code"] == "DailyContentError:REPAIR_UNSCOPED_MUTATION"]
    if not candidates:
        return None
    root = content._safe_path(repo_root, f"build/daily-content/{ledger.run_id}/model-calls")
    def intent(call_id: str, input_hash: str) -> dict:
        return content._model_call_intent(root=repo_root, run_id=ledger.run_id, issue_date=ledger.issue_date,
                                         role="editor", category=None, call_id=call_id, input_hash=input_hash)
    original_dir = root / initial_id
    original_intent = intent(initial_id, initial_input)
    if json.loads(_bytes(original_dir / "intent.json")) != original_intent:
        raise content.ModelResultPending("saved_scope_original_intent")
    original_outputs = [path for path in (original_dir, original_dir / "schema-recovery") if (path / "raw.json").exists()]
    if len(original_outputs) != 1:
        raise content.ModelResultPending("saved_scope_original_output_ambiguous")
    original, original_bytes = read_completed_output(original_outputs[0], original_intent)
    for row in candidates:
        call_id = str(row["call_id"])
        if len(call_id) != 64 or any(char not in "0123456789abcdef" for char in call_id):
            raise content.ModelResultPending("saved_scope_call_identity")
        directory = root / call_id
        try:
            context = json.loads(_bytes(directory / "editor.prompt.txt").decode("utf-8").rsplit("\n入力:\n", 1)[1])
            feedback = context["repair_feedback"]
            expected_paths = content._repair_allowed_paths(
                artifact_id="editor", reason_code=feedback["reasonCode"], invalid_payload=original,
            )
            if (feedback["runId"] != ledger.run_id or feedback["issueDate"] != ledger.issue_date
                or feedback["artifactId"] != "editor" or feedback["inputHash"] != initial_input
                or feedback["invalidPayload"] != original or not expected_paths
                or feedback.get("allowedMutationPaths") != expected_paths
                or feedback["reasonCode"] != str(original_row["failure_code"]).replace("|", "/")
                or feedback["failureSignature"] != failure_signature(feedback)):
                raise ValueError("feedback")
            expected_input = content._artifact_input_hash({
                "issueDate": ledger.issue_date, "reporterOutputHashes": list(reporter_hashes),
                "repairFailureSignature": feedback["failureSignature"],
            })
            if row["input_hash"] != expected_input or call_id != content._sha256_bytes(f"repair|editor|{expected_input}".encode()):
                raise ValueError("input")
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise content.ModelResultPending("saved_scope_feedback_binding") from exc
        proposal, proposal_bytes = read_completed_output(directory, intent(call_id, expected_input))
        projected = content._project_repair_result(feedback, proposal)
        with tempfile.TemporaryDirectory(prefix="ng-saved-scope-preview-") as preview:
            validated = content._validate_editor(projected, issue_date=ledger.issue_date, reporters=reporters, preview_dir=Path(preview))
        receipt = {
            "schemaVersion": "NEWS_GRASP_SAVED_SCOPE_RECOVERY_V1", "runId": ledger.run_id,
            "sourceCallId": call_id, "targetInputHash": initial_input, "appliedPaths": expected_paths,
            "originalRawSha256": hashlib.sha256(original_bytes).hexdigest(),
            "proposalRawSha256": hashlib.sha256(proposal_bytes).hexdigest(),
            "outputHash": hashlib.sha256(runtime._json_dump(validated).encode()).hexdigest(),
            "modelCalls": 0,
        }
        receipt_path = directory / "scope-recovery.json"
        if receipt_path.exists() and json.loads(_bytes(receipt_path)) != receipt:
            raise content.ModelResultPending("saved_scope_receipt_drift")
        with ledger.materialization_fence():
            content._atomic_write_bytes(receipt_path, content._json_bytes(receipt))
        return ledger.write_checkpoint(artifact_id="editor", input_hash=initial_input,
                                       validator_id=content._validator_id("editor"), payload=validated)
    return None
