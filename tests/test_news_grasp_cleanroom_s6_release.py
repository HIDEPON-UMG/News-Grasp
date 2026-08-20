"""S6 release evidence / final-admission のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s6_cases.json"
S0_CONFIG_PATH = Path(__file__).parents[1] / "config" / "news_grasp_cleanroom_control_s0_v2.json"
MANIFEST_PATH = Path(__file__).parents[1] / "config" / "news_grasp_cleanroom_task_manifest_v1.json"
TOKYO = ZoneInfo("Asia/Tokyo")
ISSUE_DATE = "2026-08-21"
SCHEDULE_ID = "news-grasp-daily-v1"
RAW_ARGV = ["dispatch", "--schedule-id", SCHEDULE_ID, "--intent", "reconcile"]
CREATE_NO_WINDOW = 0x08000000


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 21, hour, minute, tzinfo=TOKYO)


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S6_CASES_V1"
    assert value["packetId"] == "NG-CLEANROOM-S6-RED-V1"
    assert value["issueDate"] == ISSUE_DATE
    assert value["scheduleId"] == SCHEDULE_ID
    assert value["timezone"] == "Asia/Tokyo"
    assert value["layerOrder"] == [f"L{index}" for index in range(10)]
    return value


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(value) == {"schemaVersion", "scheduleId", "tasks"}
    assert value["schemaVersion"] == "NEWS_GRASP_CONTROL_MANIFEST_V1"
    assert value["scheduleId"] == SCHEDULE_ID
    assert len(value["tasks"]) == 1
    task = value["tasks"][0]
    assert [item["triggerId"] for item in task["triggers"]] == ["scheduled-0600", "audit-0640"]
    assert set(task["action"]) == {"entryModule", "argv", "workingDirectoryToken"}
    return value


def _layer_manifest() -> list[dict[str, Any]]:
    source = json.loads(S0_CONFIG_PATH.read_text(encoding="utf-8"))
    rows = source["fakeRealCurrentPlannedBindings"]
    assert [row["layer"] for row in rows] == [f"L{index}" for index in range(10)]
    projected = [
        {
            "layer": row["layer"],
            "fakeSubstitution": list(row["fakeSubstitution"]),
            "realRequired": list(row["realRequired"]),
            "schemaIdentity": row["schemaIdentity"],
            "parityNode": row["parityNode"],
        }
        for row in rows
    ]
    return projected


def _layer_row(
    manifest_row: dict[str, Any],
    *,
    observed: list[str] | None = None,
    fake_used: list[str] | None = None,
) -> dict[str, Any]:
    row = {
        "layer": manifest_row["layer"],
        "realRequired": list(manifest_row["realRequired"]),
        "realObserved": list(observed if observed is not None else manifest_row["realRequired"]),
        "fakeUsed": list(fake_used if fake_used is not None else manifest_row["fakeSubstitution"]),
        "schemaIdentity": manifest_row["schemaIdentity"],
        "status": "GREEN",
    }
    row["evidenceSha256"] = _sha(row)
    return row


def _layer_evidence(*, observed: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    manifest = _layer_manifest()
    observed = observed or {}
    return [_layer_row(row, observed=observed.get(row["layer"])) for row in manifest[:8]]


def _runtime_root(tmp_path: Path, index: int) -> tuple[Path, Path, Path, bytes]:
    root = tmp_path / f"日本語-リリース面-{index}"
    source = root / "source"
    installed = root / "installed"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(MANIFEST_PATH.read_bytes())
    launcher = f"# S6 deterministic launcher {index}\nprint('safe')\n".encode("utf-8")
    (source / "launcher.pyw").write_bytes(launcher)
    return root, source, installed, launcher


def _writer(index: int) -> dict[str, Any]:
    return {
        "writerId": f"s6-release-{index}",
        "bootId": "s6-test-boot",
        "pid": 12000 + index,
        "processStartToken": f"s6-process-{index}",
    }


def _dispatch_pair(root: Path, manifest_path: Path, dispatch_module: Any, index: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scheduled = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=manifest_path,
        observed_at=_at(6, 1),
        writer=_writer(index),
    )
    audit = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=manifest_path,
        observed_at=_at(6, 41),
        writer={**_writer(index), "writerId": f"s6-release-audit-{index}", "pid": 13000 + index},
    )
    inspection = dispatch_module.inspect_control_state(runtime_root=root, manifest_path=manifest_path)
    return scheduled, audit, inspection


def _file_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.read_bytes(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _receipt(lineage: str, *, issue_date: str = ISSUE_DATE, generation: int = 7, state: str = "GREEN") -> dict[str, Any]:
    value = {
        "schemaVersion": "NATURAL_OPERATION_RECEIPT_V1",
        "lineage": lineage,
        "issueDate": issue_date,
        "generation": generation,
        "state": state,
        "terminalHash": _sha({"lineage": lineage, "issueDate": issue_date, "generation": generation, "state": state}),
    }
    value["receiptSha256"] = _sha(value)
    return value


def _natural_evidence(tmp_path: Path, index: int = 0) -> tuple[Path, dict[str, Any]]:
    root, source, installed, launcher = _runtime_root(tmp_path, index)
    (installed / "launcher.pyw").write_bytes(launcher)
    source_hash = hashlib.sha256((source / "launcher.pyw").read_bytes()).hexdigest()
    installed_hash = hashlib.sha256((installed / "launcher.pyw").read_bytes()).hexdigest()
    cases = _cases()
    history = cases["history"]
    value: dict[str, Any] = {
        "schemaVersion": "NATURAL_OPERATION_EVIDENCE_V1",
        "issueDate": ISSUE_DATE,
        "generation": cases["natural"]["generation"],
        "installed": {
            "commit": "a" * 40,
            "sourceSha256": source_hash,
            "installedSha256": installed_hash,
            "freshness": cases["natural"]["freshness"],
            "observedAt": _at(6, 42).isoformat(),
        },
        "receipts": {
            "Scheduled": _receipt("Scheduled"),
            "Audit": _receipt("Audit"),
            "Public": _receipt("Public"),
            "Readiness": _receipt("Readiness"),
        },
        "historyCoverage": {
            "schemaVersion": "HISTORY_COVERAGE_V1",
            "timezone": cases["timezone"],
            "introductionDate": history["introductionDate"],
            "dates": list(history["dates"]),
            "missingDays": [],
            "corpusCounts": {
                "Scheduled": history["scheduledCorpusCount"],
                "Audit": history["auditCorpusCount"],
            },
            "legacyWriterCount": history["legacyWriterCount"],
        },
    }
    value["naturalEvidenceSha256"] = _sha(value)
    return root, value


def _accepted_receipts() -> list[dict[str, Any]]:
    return [
        {
            "receiptId": f"NG-CLEANROOM-S{index}-GREEN-V1",
            "status": "GREEN",
            "issueDate": ISSUE_DATE,
            "generation": 7,
            "receiptSha256": _sha({"receiptId": f"NG-CLEANROOM-S{index}-GREEN-V1", "issueDate": ISSUE_DATE, "generation": 7}),
        }
        for index in range(6)
    ]


def _admission(tmp_path: Path, index: int = 0) -> tuple[Path, dict[str, Any]]:
    root, source, installed, launcher = _runtime_root(tmp_path, index)
    (installed / "launcher.pyw").write_bytes(launcher)
    source_hash = hashlib.sha256((source / "launcher.pyw").read_bytes()).hexdigest()
    installed_hash = hashlib.sha256((installed / "launcher.pyw").read_bytes()).hexdigest()
    layers = _layer_evidence()
    value: dict[str, Any] = {
        "schemaVersion": "E2E_FINAL_ADMISSION_V1",
        "admissionId": f"s6-final-admission-{index}",
        "issueDate": ISSUE_DATE,
        "generation": 7,
        "acceptedReceipts": _accepted_receipts(),
        "layerEvidence": layers,
        "layerEvidenceSha256": _sha(layers),
        "installedSourceSha256": source_hash,
        "installedHashSha256": installed_hash,
        "externalMutationSuppressed": True,
        "externalMutationCount": 0,
        "attemptBudget": 1,
        "attemptsUsed": 0,
        "independentBlockerCount": 0,
        "mode": "final_confirmation_only",
    }
    value["admissionSha256"] = _sha(value)
    return root, value


def _mutate_layer(rows: list[dict[str, Any]], case: str) -> list[dict[str, Any]]:
    value = deepcopy(rows)
    if case == "unknown_layer":
        value[0]["layer"] = "L10"
    elif case == "missing_layer":
        value.pop()
    elif case == "duplicate_layer":
        value.append(deepcopy(value[-1]))
    elif case == "schema_drift":
        value[0]["schemaIdentity"] = "DRIFTED_SCHEMA_V9"
    elif case == "fake_not_sealed":
        value[0]["fakeUsed"].append("unsealed provider")
    elif case == "real_boundary_missing":
        value[0]["realObserved"].pop()
    elif case == "hash_drift":
        value[0]["evidenceSha256"] = "0" * 64
    elif case == "status_not_green":
        value[0]["status"] = "UNVERIFIED"
    elif case == "unknown_key":
        value[0]["unexpected"] = "drift"
    else:
        raise AssertionError(f"unknown layer case: {case}")
    return value


def _mutate_admission(value: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = deepcopy(value)
    if case == "missing_receipt":
        mutated["acceptedReceipts"].pop()
    elif case == "unknown_receipt":
        mutated["acceptedReceipts"][0]["receiptId"] = "NG-UNKNOWN-V9"
    elif case == "stale_receipt_hash":
        mutated["acceptedReceipts"][0]["receiptSha256"] = "0" * 64
    elif case == "duplicate_receipt":
        mutated["acceptedReceipts"].append(deepcopy(mutated["acceptedReceipts"][0]))
    elif case == "missing_layer_hash":
        mutated.pop("layerEvidenceSha256")
    elif case == "unknown_layer_hash":
        mutated["layerEvidence"].append(deepcopy(mutated["layerEvidence"][-1]))
        mutated["layerEvidence"][-1]["layer"] = "L8"
    elif case == "installed_source_drift":
        mutated["installedSourceSha256"] = "a" * 64
    elif case == "installed_hash_drift":
        mutated["installedHashSha256"] = "b" * 64
    elif case == "attempt_budget_two":
        mutated["attemptBudget"] = 2
    elif case == "attempt_already_used":
        mutated["attemptsUsed"] = 1
    elif case == "independent_blocker":
        mutated["independentBlockerCount"] = 1
    elif case == "external_mutation_not_suppressed":
        mutated["externalMutationSuppressed"] = False
    elif case == "unknown_key":
        mutated["unexpected"] = "drift"
    else:
        raise AssertionError(f"unknown L8 case: {case}")
    return mutated


def _mutate_natural(value: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = deepcopy(value)
    if case == "missing_scheduled":
        mutated["receipts"].pop("Scheduled")
    elif case == "missing_audit":
        mutated["receipts"].pop("Audit")
    elif case == "missing_public":
        mutated["receipts"].pop("Public")
    elif case == "missing_readiness":
        mutated["receipts"].pop("Readiness")
    elif case == "wrong_issue_date":
        mutated["receipts"]["Scheduled"]["issueDate"] = "2026-08-20"
    elif case == "wrong_generation":
        mutated["receipts"]["Audit"]["generation"] = 6
    elif case == "installed_commit_drift":
        mutated["installed"]["commit"] = "b" * 40
    elif case == "installed_hash_drift":
        mutated["installed"]["installedSha256"] = "c" * 64
    elif case == "stale_freshness":
        mutated["installed"]["freshness"] = "stale"
    elif case == "public_not_required_without_green_scheduled":
        mutated["receipts"]["Public"]["state"] = "NOT_REQUIRED"
        mutated["receipts"]["Scheduled"]["state"] = "FAILED"
    elif case == "receipt_schema_drift":
        mutated["receipts"]["Readiness"]["schemaVersion"] = "E2E_FINAL_ADMISSION_V1"
    elif case == "unknown_key":
        mutated["unexpected"] = "drift"
    elif case == "l8_receipt_for_natural":
        mutated["receipts"]["Scheduled"] = {"schemaVersion": "E2E_FINAL_ADMISSION_V1", "status": "ADMITTED"}
    elif case == "recovery_for_scheduled":
        mutated["receipts"]["Scheduled"]["lineage"] = "Recovery"
    elif case == "public_for_readiness":
        mutated["receipts"]["Readiness"] = deepcopy(mutated["receipts"]["Public"])
    elif case == "previous_generation":
        for receipt in mutated["receipts"].values():
            receipt["generation"] = 6
        mutated["generation"] = 6
    elif case == "previous_issue_date":
        mutated["issueDate"] = "2026-08-20"
    elif case == "installed_stale":
        mutated["installed"]["freshness"] = "stale"
    else:
        raise AssertionError(f"unknown natural case: {case}")
    return mutated


def test_s6_l3_l7_real_boundary_manifest(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    manifest = _layer_manifest()
    assert len(manifest) == 10
    assert [row["layer"] for row in manifest] == cases["layerOrder"]

    root, _source, _installed, _launcher = _runtime_root(tmp_path, 1)
    dispatch = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    scheduled, audit, inspection = _dispatch_pair(root, root / "manifest.json", dispatch, 1)
    assert scheduled["schemaVersion"] == "DISPATCH_DECISION_V1"
    assert audit["schemaVersion"] == "DISPATCH_DECISION_V1"
    assert scheduled["slotKind"] == "Scheduled"
    assert audit["slotKind"] == "Audit"
    assert scheduled["slotKey"] == f"{SCHEDULE_ID}/{ISSUE_DATE}/Scheduled"
    assert audit["slotKey"] == f"{SCHEDULE_ID}/{ISSUE_DATE}/Audit"
    slots = {(row["slotKind"], row["issueDate"]): row for row in inspection["slots"]}
    assert ("Scheduled", ISSUE_DATE) in slots
    assert ("Audit", ISSUE_DATE) in slots
    assert inspection["integrityStatus"] == "green"
    assert (root / "control" / "control-ledger-v1.sqlite3").exists()
    assert list((root / "control" / "wal").rglob("0001-initial.json"))

    contracts = importlib.import_module("tools.news_grasp_cleanroom_contracts")
    assert contracts.validate_manifest(_manifest())["scheduleId"] == SCHEDULE_ID
    execution = importlib.import_module("tools.news_grasp_cleanroom_execution")
    public = importlib.import_module("tools.news_grasp_cleanroom_public")
    assert hasattr(execution, "ExecutionController")
    assert hasattr(public, "PublicController")
    observed = {
        "L0": ["filesystem bytes", "git blob"],
        "L1": ["production validator symbols"],
        "L2": ["sqlite3 file", "fsync", "atomic replace"],
        "L3": ["production functions", "shared SQLite/WAL"],
        "L4": ["vertical production components"],
        "L5": ["dispatch", "ledger", "controller", "product gate", "recovery"],
        "L6": ["subprocess", "WAL", "SQLite", "filesystem restart"],
        "L7": ["installed bytes", "Task Scheduler action"],
    }
    evidence = _layer_evidence(observed=observed)
    validated = module.validate_layer_evidence(evidence, manifest[:8])
    assert validated["schemaVersion"] == "LAYER_EVIDENCE_V1"
    assert validated["status"] == "GREEN"
    assert validated["layers"] == [row["layer"] for row in evidence]

    for index, case in enumerate(cases["layerEvidenceCases"], start=10):
        negative = _mutate_layer(evidence, case)
        before = _file_inventory(root)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_layer_evidence(negative, manifest[:8])
        assert _file_inventory(root) == before


def test_s6_installed_bytes_args_workdir(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, source, installed, launcher = _runtime_root(tmp_path, 2)
    (installed / "launcher.pyw").write_bytes(launcher)
    assert (source / "launcher.pyw").read_bytes() == (installed / "launcher.pyw").read_bytes()
    source_hash = hashlib.sha256((source / "launcher.pyw").read_bytes()).hexdigest()
    installed_hash = hashlib.sha256((installed / "launcher.pyw").read_bytes()).hexdigest()
    assert source_hash == installed_hash

    runner = root / "実行-検証.py"
    runner.write_text(
        "import json\n"
        "from datetime import datetime\n"
        "from pathlib import Path\n"
        "from zoneinfo import ZoneInfo\n"
        "from tools.news_grasp_cleanroom_dispatch import dispatch\n"
        f"root = Path({str(root)!r})\n"
        f"manifest = Path({str(root / 'manifest.json')!r})\n"
        "result = dispatch(raw_argv=['dispatch','--schedule-id','news-grasp-daily-v1','--intent','reconcile'], "
        "runtime_root=root, manifest_path=manifest, observed_at=datetime(2026,8,21,6,1,tzinfo=ZoneInfo('Asia/Tokyo')), "
        "writer={'writerId':'s6-child','bootId':'s6-boot','pid':14002,'processStartToken':'s6-child-token'})\n"
        "print(json.dumps({'schemaVersion':'RECONCILE_RESULT_V1','status':'GREEN','dispatch':result}, ensure_ascii=False, sort_keys=True, separators=(',',':')))\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1]) + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, str(runner)],
        cwd=Path(__file__).parents[1],
        env=environment,
        shell=False,
        creationflags=CREATE_NO_WINDOW,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    child_result = json.loads(lines[0])
    assert child_result["schemaVersion"] == "RECONCILE_RESULT_V1"
    assert child_result["dispatch"]["schemaVersion"] == "DISPATCH_DECISION_V1"

    task_definition = {
        "taskPath": "\\",
        "taskName": "News-Grasp Production",
        "enabled": False,
        "executable": sys.executable,
        "arguments": list(RAW_ARGV),
        "workingDirectory": str(root),
        "triggers": deepcopy(_manifest()["tasks"][0]["triggers"]),
        "multipleInstancesPolicy": "Parallel",
    }
    assert task_definition["arguments"] == RAW_ARGV
    assert task_definition["workingDirectory"] == str(root)
    assert isinstance(task_definition["arguments"], list)
    assert " " not in task_definition["executable"] or Path(task_definition["executable"]).exists()
    evidence = _layer_evidence(
        observed={
            "L6": ["subprocess", "WAL", "SQLite", "filesystem restart"],
            "L7": ["installed bytes", "Task Scheduler action"],
        }
    )
    validated = module.validate_layer_evidence(evidence, _layer_manifest()[:8])
    assert validated["status"] == "GREEN"
    assert cases["l8"]["externalMutationSuppressed"] is True


def test_s6_l8_final_admission(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, admission = _admission(tmp_path, 3)
    before = _file_inventory(root)
    admitted = module.admit_final_e2e(admission)
    assert admitted["schemaVersion"] == "E2E_FINAL_ADMISSION_V1"
    assert admitted["status"] == "ADMITTED"
    assert admitted["attemptBudget"] == cases["l8"]["attemptBudget"]
    assert admitted["attemptsUsed"] == cases["l8"]["attemptsUsed"]
    assert admitted["independentBlockerCount"] == 0
    assert _file_inventory(root) == before

    for case in cases["l8AdmissionCases"]:
        negative = _mutate_admission(admission, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.admit_final_e2e(negative)
        assert _file_inventory(root) == before


def test_s6_history_coverage_and_legacy_zero(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 4)
    validated = module.validate_natural_evidence(natural)
    history = validated["historyCoverage"]
    assert history["schemaVersion"] == "HISTORY_COVERAGE_V1"
    assert history["timezone"] == cases["timezone"]
    assert history["introductionDate"] == cases["history"]["introductionDate"]
    assert len(history["dates"]) == cases["history"]["expectedDays"] == 30
    assert history["missingDays"] == []
    assert history["corpusCounts"] == {"Scheduled": 63, "Audit": 8}
    assert history["legacyWriterCount"] == 0
    assert _file_inventory(root)

    for case in cases["historyCases"]:
        negative = deepcopy(natural)
        history_negative = negative["historyCoverage"]
        if case == "missing_date":
            history_negative["dates"].pop()
        elif case == "duplicate_date":
            history_negative["dates"].append(history_negative["dates"][-1])
        elif case == "wrong_timezone":
            history_negative["timezone"] = "UTC"
        elif case == "wrong_introduction_date":
            history_negative["introductionDate"] = "2026-07-24"
        elif case == "scheduled_count_drift":
            history_negative["corpusCounts"]["Scheduled"] = 62
        elif case == "audit_count_drift":
            history_negative["corpusCounts"]["Audit"] = 7
        elif case == "legacy_writer_nonzero":
            history_negative["legacyWriterCount"] = 1
        else:
            history_negative["unexpected"] = "drift"
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_natural_evidence(negative)


def test_s6_natural_receipt_schema(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 5)
    validated = module.validate_natural_evidence(natural)
    assert validated["schemaVersion"] == "NATURAL_OPERATION_EVIDENCE_V1"
    assert set(validated["receipts"]) == set(cases["natural"]["requiredLineages"])
    assert [validated["receipts"][key]["lineage"] for key in cases["natural"]["requiredLineages"]] == cases["natural"]["requiredLineages"]
    assert all(receipt["issueDate"] == ISSUE_DATE for receipt in validated["receipts"].values())
    assert all(receipt["generation"] == cases["natural"]["generation"] for receipt in validated["receipts"].values())
    assert validated["installed"]["sourceSha256"] == validated["installed"]["installedSha256"]
    assert validated["installed"]["freshness"] == "fresh"
    assert _file_inventory(root)

    for case in cases["naturalCases"]:
        negative = _mutate_natural(natural, case)
        if case == "public_not_required_without_green_scheduled":
            with pytest.raises(module.ReleaseEvidenceError):
                module.validate_natural_evidence(negative)
        else:
            with pytest.raises(module.ReleaseEvidenceError):
                module.validate_natural_evidence(negative)


def test_s6_natural_states_non_substitutable(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 6)
    public_not_required = deepcopy(natural)
    public_not_required["receipts"]["Public"]["state"] = "NOT_REQUIRED"
    accepted = module.validate_natural_evidence(public_not_required)
    assert accepted["receipts"]["Public"]["state"] == "NOT_REQUIRED"
    assert accepted["receipts"]["Scheduled"]["state"] == "GREEN"
    assert accepted["receipts"]["Audit"]["state"] == "GREEN"

    for case in cases["nonSubstitutionCases"]:
        negative = _mutate_natural(natural, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_natural_evidence(negative)
    assert _file_inventory(root)
