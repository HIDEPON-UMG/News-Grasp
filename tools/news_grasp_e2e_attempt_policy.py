"""NoPublish E2Eの論理attempt A/Bと同一attempt再開を管理する。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
import stat
import re
import sqlite3
import sys
import sysconfig
from datetime import date
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "NEWS_GRASP_E2E_ATTEMPT_POLICY_V1"
MAX_LOGICAL_ATTEMPTS = 2
MAX_FAILURE_LOCAL_RESUMES = 1
MAX_ADMISSION_BYTES = 1024 * 1024


class E2EAttemptPolicyError(RuntimeError):
    """E2E attempt policyの不正遷移。"""


def _load_issued_admission(admission_path: Path) -> tuple[Path, dict[str, Any], str, str]:
    """公式bridgeでissued admissionを検証し、bytes identityを返す。"""
    candidate = Path(admission_path)
    try:
        before = candidate.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError("admission is not a regular file")
        resolved = candidate.resolve(strict=True)
        resolved_info = resolved.lstat()
        if stat.S_ISLNK(resolved_info.st_mode) or not stat.S_ISREG(resolved_info.st_mode):
            raise OSError("admission is not a regular file")
        if resolved_info.st_size > MAX_ADMISSION_BYTES:
            raise OSError("admission is oversized")
        raw = resolved.read_bytes()
        after = resolved.lstat()
        if (
            after.st_size != resolved_info.st_size
            or after.st_mtime_ns != resolved_info.st_mtime_ns
        ):
            raise OSError("admission changed while reading")
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID") from error
    try:
        bridge_path = Path(__file__).resolve().with_name("e2e_final_admission_bridge.py")
        spec = importlib.util.spec_from_file_location(
            "_news_grasp_e2e_final_admission_bridge_for_attempt_policy", bridge_path
        )
        if spec is None or spec.loader is None or not isinstance(value, dict):
            raise ImportError("official admission validator unavailable")
        bridge = importlib.util.module_from_spec(spec)
        original_sys_path = list(sys.path)
        try:
            module_root = str(bridge_path.parents[1])
            if module_root not in sys.path:
                sys.path.insert(0, module_root)
            for site_path in (
                sysconfig.get_paths().get("purelib"),
                sysconfig.get_paths().get("platlib"),
            ):
                if site_path and site_path not in sys.path:
                    sys.path.insert(0, site_path)
            spec.loader.exec_module(bridge)
        finally:
            sys.path[:] = original_sys_path
        validator = getattr(bridge, "_validate_admission", None)
        if not callable(validator):
            raise ImportError("official admission validator unavailable")
        validator(value)
    except Exception as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID") from error
    if value.get("state") != "issued":
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID")
    admission_projection = dict(value)
    admission_id = admission_projection.pop("admissionId", None)
    expected_admission_id = hashlib.sha256(_canonical_json(admission_projection)).hexdigest()
    attempt_key = value.get("attemptKey")
    issue_date = value.get("issueDate")
    try:
        parsed_issue_date = date.fromisoformat(str(issue_date))
    except (TypeError, ValueError) as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID") from error
    if (
        not isinstance(admission_id, str)
        or admission_id != expected_admission_id
        or not isinstance(attempt_key, str)
        or not attempt_key
        or not isinstance(issue_date, str)
        or parsed_issue_date.isoformat() != issue_date
    ):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID")
    return resolved, value, admission_id, hashlib.sha256(raw).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_policy_once(output_path: Path, value: dict[str, Any]) -> Path:
    output = Path(output_path)
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_OUTPUT_INVALID") from error
    candidate = parent / output.name
    if candidate.is_symlink():
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_OUTPUT_INVALID")
    payload = _canonical_json(value) + b"\n"
    try:
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_OUTPUT_EXISTS") from error
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return candidate


def policy_ledger_path(policy_path: Path) -> Path:
    return Path(policy_path).resolve().with_name("e2e-attempt-policy-ledger.sqlite3")


def append_policy_transition(
    policy_path: Path,
    admission_path: Path,
    ledger_path: Path | None = None,
    transition_receipt_path: Path | None = None,
) -> Path:
    """型付きauthority一件だけをcanonical ledgerへappendする。"""
    policy_path = Path(policy_path).resolve(strict=True)
    admission_path = Path(admission_path).resolve(strict=True)
    ledger_path = (ledger_path or policy_ledger_path(policy_path)).resolve()
    value = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    state = validate_policy(value)
    history = state["transitionHistory"]
    admission = json.loads(admission_path.read_text(encoding="utf-8-sig"))
    if not isinstance(admission, dict) or admission.get("state") != "issued":
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID")
    admission_id = admission.get("admissionId")
    admission_projection = dict(admission)
    admission_projection.pop("admissionId", None)
    expected_admission_id = hashlib.sha256(
        json.dumps(admission_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not isinstance(admission_id, str) or admission_id != expected_admission_id or not admission.get("attemptKey") or not admission.get("issueDate"):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_INVALID")
    binding = state.get("admissionBinding")
    admission_sha = hashlib.sha256(admission_path.read_bytes()).hexdigest()
    if (
        not isinstance(binding, dict)
        or binding.get("admissionPath") != str(admission_path)
        or binding.get("admissionSha256") != admission_sha
        or binding.get("attemptKey") != admission.get("attemptKey")
        or binding.get("issueDate") != admission.get("issueDate")
        or binding.get("admissionId") != admission_id
    ):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_BINDING_INVALID")
    if not history:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
    if len(history) > 1 and transition_receipt_path is None:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_REQUIRED")
    receipt_path = ""
    receipt_sha = ""
    if transition_receipt_path is not None:
        try:
            receipt = Path(transition_receipt_path).resolve(strict=True)
        except OSError:
            if len(history) == 1:
                receipt = None
            else:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
        if receipt is None:
            receipt_path = ""
            receipt_sha = ""
        else:
            if receipt.is_symlink() or not receipt.is_file() or not receipt.is_relative_to(policy_path.parent):
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            receipt_value = json.loads(receipt.read_text(encoding="utf-8-sig"))
            required_receipt = {"schemaVersion", "event", "sequence", "attemptKey", "issueDate", "admissionId", "previousStateSha256", "stateSha256", "producerRouteId", "status", "producerProcessId", "producerExecutablePath", "producerExecutableSha256", "outcomeSchemaVersion", "outcomeStatus", "outcomeSha256", "outcomeStatePath", "outcomeStateSha256", "outcomeExitCode", "outcomeRunnerStatus"}
            if not isinstance(receipt_value, dict) or set(receipt_value) != required_receipt or receipt_value.get("schemaVersion") != "NEWS_GRASP_E2E_TRANSITION_RECEIPT_V1" or receipt_value.get("status") != "succeeded" or receipt_value.get("producerRouteId") not in {"news-grasp-runner", "news-grasp-recovery"} or type(receipt_value.get("producerProcessId")) is not int or receipt_value.get("producerProcessId") <= 0 or not isinstance(receipt_value.get("producerExecutablePath"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt_value.get("producerExecutableSha256"))) or receipt_value.get("outcomeSchemaVersion") != "NEWS_GRASP_E2E_TRANSITION_OUTCOME_V1" or receipt_value.get("outcomeStatus") not in {"admission_validated", "runner_terminal"} or not re.fullmatch(r"[0-9a-f]{64}", str(receipt_value.get("outcomeSha256"))) or not isinstance(receipt_value.get("outcomeStatePath"), str) or not isinstance(receipt_value.get("outcomeStateSha256"), str) or type(receipt_value.get("outcomeExitCode")) is not int or not isinstance(receipt_value.get("outcomeRunnerStatus"), str) or receipt_value.get("sequence") != history[-1]["sequence"]:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            if any(receipt_value.get(key) != expected for key, expected in (("event", history[-1]["event"]), ("attemptKey", admission["attemptKey"]), ("issueDate", admission["issueDate"]), ("admissionId", admission_id), ("previousStateSha256", history[-1]["previousStateSha256"]), ("stateSha256", history[-1]["stateSha256"]))):
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            producer_path = Path(str(receipt_value["producerExecutablePath"]))
            try:
                producer_bytes = producer_path.read_bytes()
            except OSError as error:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID") from error
            if hashlib.sha256(producer_bytes).hexdigest() != receipt_value["producerExecutableSha256"]:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            if receipt_value["outcomeStatus"] == "admission_validated":
                if receipt_value["outcomeStatePath"] != "" or receipt_value["outcomeStateSha256"] != "" or receipt_value["outcomeExitCode"] != -1 or receipt_value["outcomeRunnerStatus"] != "not_started":
                    raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            else:
                state_path = Path(receipt_value["outcomeStatePath"])
                if not state_path.is_file() or state_path.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", receipt_value["outcomeStateSha256"]):
                    raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
                try:
                    state_raw = state_path.read_bytes()
                    state_value = json.loads(state_raw.decode("utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID") from error
                if hashlib.sha256(state_raw).hexdigest() != receipt_value["outcomeStateSha256"] or not isinstance(state_value, dict) or state_value.get("status") != receipt_value["outcomeRunnerStatus"] or int(state_value.get("exit_code", -1)) != receipt_value["outcomeExitCode"] or receipt_value["outcomeExitCode"] != 0 or receipt_value["outcomeRunnerStatus"] != "publish_dry_run_ok":
                    raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_INVALID")
            receipt_path = str(receipt)
            receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger_path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS transitions (sequence INTEGER PRIMARY KEY, event TEXT NOT NULL, previous_sha256 TEXT NOT NULL, state_sha256 TEXT NOT NULL, attempt_key TEXT NOT NULL, issue_date TEXT NOT NULL, admission_id TEXT NOT NULL, admission_path TEXT NOT NULL, admission_sha256 TEXT NOT NULL, receipt_path TEXT NOT NULL, receipt_sha256 TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT sequence,event,previous_sha256,state_sha256,attempt_key,issue_date,admission_id,admission_path,admission_sha256,receipt_path,receipt_sha256 FROM transitions ORDER BY sequence").fetchall()
        expected = []
        for item in history:
            sequence = int(item["sequence"])
            candidate_receipt = policy_path.with_name(f"e2e-transition-{sequence}.json")
            if receipt_path and sequence == int(history[-1]["sequence"]):
                row_receipt_path, row_receipt_sha = receipt_path, receipt_sha
            elif candidate_receipt.is_file():
                row_receipt_path = str(candidate_receipt.resolve())
                row_receipt_sha = hashlib.sha256(candidate_receipt.read_bytes()).hexdigest()
            else:
                row_receipt_path, row_receipt_sha = "", ""
            expected.append((sequence, item["event"], item["previousStateSha256"], item["stateSha256"], admission["attemptKey"], admission["issueDate"], admission_id, str(admission_path), admission_sha, row_receipt_path, row_receipt_sha))
        if rows != expected[: len(rows)]:
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_LEDGER_DIVERGENCE")
        if len(rows) == len(expected):
            return ledger_path
        if len(rows) == 0 and len(history) == 1 and not receipt_path:
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_REQUIRED")
        if len(expected) != len(rows) + 1:
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_APPEND_REQUIRED")
        db.execute(
            "INSERT INTO transitions(sequence,event,previous_sha256,state_sha256,attempt_key,issue_date,admission_id,admission_path,admission_sha256,receipt_path,receipt_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            expected[-1],
        )
        db.commit()
    return ledger_path


def validate_policy_ledger(value: object, policy_path: Path) -> dict[str, Any]:
    state = validate_policy(value)
    ledger_path = policy_ledger_path(policy_path)
    try:
        with sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True) as db:
            rows = db.execute("SELECT sequence,event,previous_sha256,state_sha256,attempt_key,issue_date,admission_id,admission_path,admission_sha256,receipt_path,receipt_sha256 FROM transitions ORDER BY sequence").fetchall()
    except sqlite3.Error as error:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_LEDGER_REQUIRED") from error
    binding = state.get("admissionBinding")
    if not isinstance(binding, dict):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ADMISSION_BINDING_INVALID")
    expected_prefix = [(int(item["sequence"]), item["event"], item["previousStateSha256"], item["stateSha256"], binding["attemptKey"], binding["issueDate"], binding["admissionId"], binding["admissionPath"], binding["admissionSha256"]) for item in state["transitionHistory"]]
    if [row[:9] for row in rows] != expected_prefix:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_LEDGER_DIVERGENCE")
    for row in rows:
        receipt = Path(row[9]).resolve(strict=True)
        if hashlib.sha256(receipt.read_bytes()).hexdigest() != row[10]:
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_RECEIPT_DRIFT")
    return state


def new_policy() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "maxLogicalAttempts": MAX_LOGICAL_ATTEMPTS,
        "maxFailureLocalResumes": MAX_FAILURE_LOCAL_RESUMES,
        "logicalAttemptIssued": 0,
        "attemptA": {"status": "not_issued", "resumeCount": 0},
        "attemptB": {"status": "not_issued", "resumeCount": 0},
        "terminal": None,
        "designFeedback": None,
        "transition": {"sequence": 0, "event": "created", "previousStateSha256": "", "stateSha256": ""},
        "transitionHistory": [],
        "admissionBinding": {"attemptKey": "", "issueDate": "", "admissionId": "", "admissionPath": "", "admissionSha256": ""},
    }


def bind_policy_admission(value: object, admission_path: Path) -> dict[str, Any]:
    """未発行policyへ、既存issued admissionの不変bindingを設定する。"""
    state = validate_policy(value)
    if state["transitionHistory"]:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_BINDING_TOO_LATE")
    admission_path, admission, admission_id, admission_sha = _load_issued_admission(
        Path(admission_path)
    )
    state["admissionBinding"] = {
        "attemptKey": str(admission.get("attemptKey") or ""),
        "issueDate": str(admission.get("issueDate") or ""),
        "admissionId": admission_id,
        "admissionPath": str(admission_path),
        "admissionSha256": admission_sha,
    }
    return state


def _state_sha256(state: dict[str, Any]) -> str:
    projection = {
        key: value
        for key, value in state.items()
        if key not in {"transition", "transitionHistory"}
    }
    return hashlib.sha256(
        json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _advance(state: dict[str, Any], event: str, previous: str) -> None:
    state_hash = _state_sha256(state)
    state["transition"] = {
        "sequence": int(state["transition"]["sequence"]) + 1,
        "event": event,
        "previousStateSha256": previous,
        "stateSha256": state_hash,
    }
    state["transitionHistory"].append(dict(state["transition"]))


def validate_policy(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if value.get("maxLogicalAttempts") != MAX_LOGICAL_ATTEMPTS or value.get(
        "maxFailureLocalResumes"
    ) != MAX_FAILURE_LOCAL_RESUMES:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    if value.get("logicalAttemptIssued") not in (0, 1, 2):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    for key in ("attemptA", "attemptB"):
        row = value.get(key)
        if not isinstance(row, dict) or row.get("resumeCount") not in (0, 1):
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    transition = value.get("transition")
    if (
        not isinstance(transition, dict)
        or set(transition) != {"sequence", "event", "previousStateSha256", "stateSha256"}
        or not isinstance(transition.get("sequence"), int)
        or transition.get("sequence") < 0
        or not isinstance(transition.get("event"), str)
        or not isinstance(transition.get("previousStateSha256"), str)
        or not isinstance(transition.get("stateSha256"), str)
    ):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID")
    history = value.get("transitionHistory")
    if not isinstance(history, list) or len(history) != int(transition["sequence"]):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
    replay = new_policy()
    replay["admissionBinding"] = deepcopy(value.get("admissionBinding"))
    previous_hash = _state_sha256(replay)
    for expected_sequence, item in enumerate(history, start=1):
        if (
            not isinstance(item, dict)
            or set(item) != {"sequence", "event", "previousStateSha256", "stateSha256"}
            or item["sequence"] != expected_sequence
            or item["previousStateSha256"] != previous_hash
            or not isinstance(item["stateSha256"], str)
        ):
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
        event = item["event"]
        if event == "issue_a":
            if replay["logicalAttemptIssued"] != 0:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
            replay["logicalAttemptIssued"] = 1
            replay["attemptA"]["status"] = "running"
        elif event == "failure_local_resume":
            key = "attemptA" if replay["logicalAttemptIssued"] == 1 else "attemptB"
            row = replay[key]
            if row["status"] != "running" or row["resumeCount"] >= 1:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
            row["resumeCount"] += 1
            row["status"] = "resuming_after_minimal_repair"
        elif event == "success":
            key = "attemptA" if replay["logicalAttemptIssued"] == 1 else "attemptB"
            row = replay[key]
            if row["status"] not in {"running", "resuming_after_minimal_repair"}:
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
            if key == "attemptA" and row["resumeCount"] == 0:
                row["status"] = "succeeded_without_attempt_b"
                replay["terminal"] = "product_completion"
            elif key == "attemptA":
                row["status"] = "completed_after_minimal_repair"
            else:
                row["status"] = "succeeded"
                replay["terminal"] = "product_completion"
        elif event == "full_correction_verified":
            if replay["logicalAttemptIssued"] != 1 or replay["attemptA"]["status"] != "completed_after_minimal_repair":
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
            replay["attemptA"]["status"] = "ready_for_attempt_b"
        elif event == "issue_b":
            if replay["logicalAttemptIssued"] != 1 or replay["attemptA"]["status"] != "ready_for_attempt_b":
                raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
            replay["logicalAttemptIssued"] = 2
            replay["attemptB"]["status"] = "running"
        elif event in {"design_feedback_terminal", "user_stopped"}:
            replay["terminal"] = "design_feedback_terminal" if event == "design_feedback_terminal" else "user_stopped"
            if event == "design_feedback_terminal":
                replay["designFeedback"] = deepcopy(value.get("designFeedback"))
                key = "attemptA" if replay["logicalAttemptIssued"] == 1 else "attemptB"
                replay[key]["status"] = "blocked_design_feedback"
        else:
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
        if item["stateSha256"] != _state_sha256(replay):
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
        previous_hash = item["stateSha256"]
    if history and history[-1] != transition:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
    current_projection = {key: val for key, val in value.items() if key not in {"transition", "transitionHistory"}}
    replay_projection = {key: val for key, val in replay.items() if key not in {"transition", "transitionHistory"}}
    if current_projection != replay_projection or (history and transition["stateSha256"] != _state_sha256(value)):
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
    sequence = int(transition["sequence"])
    event = str(transition["event"])
    issued = int(value["logicalAttemptIssued"])
    allowed = {
        0: {(0, "created")},
        1: {(1, "issue_a"), (2, "failure_local_resume"), (2, "success"), (2, "design_feedback_terminal"), (2, "user_stopped"), (3, "success"), (4, "full_correction_verified")},
        2: {(5, "issue_b"), (6, "success"), (6, "failure_local_resume"), (6, "design_feedback_terminal"), (6, "user_stopped"), (7, "success")},
    }
    if (sequence, event) not in allowed[issued]:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TRANSITION_INVALID")
    return deepcopy(value)


def issue_logical_attempt(value: object, attempt: int) -> dict[str, Any]:
    state = validate_policy(value)
    previous = _state_sha256(state)
    if state["terminal"] is not None:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_TERMINAL")
    if attempt not in (1, 2) or attempt != state["logicalAttemptIssued"] + 1:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_ISSUE_ORDER_INVALID")
    if attempt == 2 and state["attemptA"]["status"] != "ready_for_attempt_b":
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_B_NOT_REQUIRED")
    state["logicalAttemptIssued"] = attempt
    state[f"attempt{'A' if attempt == 1 else 'B'}"]["status"] = "running"
    _advance(state, "issue_a" if attempt == 1 else "issue_b", previous)
    return state


def mark_full_correction(value: object) -> dict[str, Any]:
    """Aの局所修正後に、全体最適の完全修正とL0-L7再検証を封印する。"""
    state = validate_policy(value)
    if (
        state["logicalAttemptIssued"] != 1
        or state["terminal"] is not None
        or state["attemptA"]["status"] != "completed_after_minimal_repair"
    ):
        raise E2EAttemptPolicyError("NEWS_GRASP_FULL_CORRECTION_REQUIRED")
    previous = _state_sha256(state)
    state["attemptA"]["status"] = "ready_for_attempt_b"
    _advance(state, "full_correction_verified", previous)
    return state


def resume_same_attempt(value: object, attempt: int, cause_class: str) -> dict[str, Any]:
    state = validate_policy(value)
    previous = _state_sha256(state)
    if state["logicalAttemptIssued"] != attempt or cause_class != "failure_local":
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_RESUME_NOT_ALLOWED")
    row = state[f"attempt{'A' if attempt == 1 else 'B'}"]
    if row["status"] != "running" or row["resumeCount"] >= MAX_FAILURE_LOCAL_RESUMES:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_RESUME_LIMIT")
    row["resumeCount"] += 1
    row["status"] = "resuming_after_minimal_repair"
    _advance(state, "failure_local_resume", previous)
    return state


def record_success(value: object, attempt: int) -> dict[str, Any]:
    state = validate_policy(value)
    previous = _state_sha256(state)
    if state["logicalAttemptIssued"] != attempt:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_SUCCESS_ORDER_INVALID")
    row = state[f"attempt{'A' if attempt == 1 else 'B'}"]
    if row["status"] not in {"running", "resuming_after_minimal_repair"}:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_SUCCESS_ORDER_INVALID")
    if attempt == 1 and row["resumeCount"] == 0:
        row["status"] = "succeeded_without_attempt_b"
        state["terminal"] = "product_completion"
    elif attempt == 1:
        row["status"] = "completed_after_minimal_repair"
    else:
        row["status"] = "succeeded"
        state["terminal"] = "product_completion"
    _advance(state, "success", previous)
    return state


def record_failure(value: object, attempt: int, cause_class: str) -> dict[str, Any]:
    state = validate_policy(value)
    previous = _state_sha256(state)
    if state["logicalAttemptIssued"] != attempt:
        raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_FAILURE_ORDER_INVALID")
    if cause_class == "failure_local":
        return resume_same_attempt(state, attempt, cause_class)
    if cause_class in {"random_transient", "design_defect", "unknown"}:
        state["terminal"] = "design_feedback_terminal"
        state["designFeedback"] = {
            "attempt": attempt,
            "causeClass": cause_class,
            "thirdAttemptForbidden": True,
        }
        state[f"attempt{'A' if attempt == 1 else 'B'}"]["status"] = "blocked_design_feedback"
        _advance(state, "design_feedback_terminal", previous)
        return state
    if cause_class == "user_stopped":
        state["terminal"] = "user_stopped"
        _advance(state, "user_stopped", previous)
        return state
    raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_CAUSE_INVALID")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    issue_a_parser = subparsers.add_parser("issue-a")
    issue_a_parser.add_argument("--admission", type=Path, required=True)
    issue_a_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command != "issue-a":
            raise E2EAttemptPolicyError("NEWS_GRASP_E2E_ATTEMPT_COMMAND_INVALID")
        state = new_policy()
        state = bind_policy_admission(state, args.admission)
        state = issue_logical_attempt(state, 1)
        output = _write_policy_once(args.output, state)
        print(json.dumps({"status": "issued", "policyPath": str(output)}, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, E2EAttemptPolicyError) as error:
        print(str(error) or "NEWS_GRASP_E2E_ATTEMPT_POLICY_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
