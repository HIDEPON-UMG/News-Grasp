"""S6 release evidence / final-admission のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s6_cases.json"
S0_CONFIG_PATH = Path(__file__).parents[1] / "config" / "news_grasp_cleanroom_control_s0_v2.json"
MANIFEST_PATH = Path(__file__).parents[1] / "config" / "news_grasp_cleanroom_task_manifest_v1.json"
ROOT = Path(__file__).parents[1]
TRACKED_LAUNCHER_PATH = ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw"
TOKYO = ZoneInfo("Asia/Tokyo")
ISSUE_DATE = "2026-08-21"
SCHEDULE_ID = "news-grasp-daily-v1"
RAW_ARGV = ["dispatch", "--schedule-id", SCHEDULE_ID, "--intent", "reconcile"]
CREATE_NO_WINDOW = 0x08000000
CURRENT_SOURCE_COMMIT = "a" * 40
INSTALLED_CLOSURE_RELATIVE_PATHS = [
    "scripts/ops/news-grasp-task-launcher.pyw",
    "scripts/ops/news-grasp-runner.ps1",
    "scripts/ops/run_codex_with_timeout.ps1",
    "tools/daily_self_heal.py",
    "tools/news_grasp_completion_guard.py",
    "tools/news_grasp_daily_control.py",
    "tools/audit_recovery_control.py",
]
INSTALLED_BINDING_RELATIVE_PATHS = [
    "bin/news-grasp-high-cost-binding-v1.json",
    "bin/news-grasp-recovery-runtime-binding-v1.json",
]
TASK_ACTION_NAMES = ["News-Grasp Bootstrap", "News-Grasp Production"]
OWNER_RECEIPT_SCHEMAS = {
    "runtime_generation_owner": [
        "PRODUCTION_GENERATION_MANIFEST_V2",
        "NEWS_GRASP_ACTIVE_GENERATION_V2",
    ],
    "ops_install_owner": [
        "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
        "NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1",
    ],
}


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
            "plannedEntrypoints": deepcopy(row["plannedEntrypoints"]),
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


def _tracked_closure_bytes() -> dict[str, bytes]:
    return {
        relative_path: (ROOT / relative_path).read_bytes()
        for relative_path in INSTALLED_CLOSURE_RELATIVE_PATHS
    }


def _materialize_installed_closure(installed: Path) -> None:
    for relative_path, content in _tracked_closure_bytes().items():
        target = installed / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for index, relative_path in enumerate(INSTALLED_BINDING_RELATIVE_PATHS):
        target = installed / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "schemaVersion": "NEWS_GRASP_RUNTIME_BINDING_V1",
            "relativePath": relative_path,
            "bindingId": f"s6-binding-{index}",
            "source": "pytest-temp-installed-closure",
        }
        body["bindingSha256"] = _sha(body)
        target.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _runtime_root(tmp_path: Path, index: int) -> tuple[Path, Path, Path, bytes]:
    root = tmp_path / f"日本語-リリース面-{index}"
    source = root / "source"
    installed = root / "installed"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(MANIFEST_PATH.read_bytes())
    launcher = TRACKED_LAUNCHER_PATH.read_bytes()
    (source / "launcher.pyw").write_bytes(launcher)
    (source / TRACKED_LAUNCHER_PATH.name).write_bytes(launcher)
    (root / "pythonw.exe").write_bytes(b"test-pythonw-sentinel")
    _materialize_installed_closure(installed)
    (installed / "launcher.pyw").write_bytes(launcher)
    return root, source, installed, launcher


def _writer(index: int) -> dict[str, Any]:
    return {
        "writerId": f"s6-release-{index}",
        "bootId": "s6-test-boot",
        "pid": 12000 + index,
        "processStartToken": f"s6-process-{index}",
    }


class _FakeWriterAttestor:
    """S6 deterministic fixture seam for strict writer identity admission."""

    def validate(self, writer: dict[str, Any]) -> bool:
        return True


def _dispatch_pair(root: Path, manifest_path: Path, dispatch_module: Any, index: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    scheduled = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=manifest_path,
        observed_at=_at(6, 1),
        writer=_writer(index),
        writer_attestor=_FakeWriterAttestor(),
    )
    audit = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=manifest_path,
        observed_at=_at(6, 41),
        writer={**_writer(index), "writerId": f"s6-release-audit-{index}", "pid": 13000 + index},
        writer_attestor=_FakeWriterAttestor(),
    )
    inspection = dispatch_module.inspect_control_state(runtime_root=root, manifest_path=manifest_path)
    return scheduled, audit, inspection


def _sqlite_control_observation(root: Path) -> dict[str, Any]:
    database = root / "control" / "control-ledger-v1.sqlite3"
    assert database.exists()
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at FROM slots ORDER BY schedule_id,issue_date,slot_kind"
            ).fetchall()
        ]
        schema = [
            tuple(row)
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
    payload = {"schema": schema, "slots": rows}
    return {
        "databaseSha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "databaseBytes": database.stat().st_size,
        "walPresent": (database.with_name(database.name + "-wal")).exists(),
        "schemaSha256": _sha(schema),
        "slotsSha256": _sha(rows),
        "serializedSlots": json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "slots": rows,
        "payloadSha256": _sha(payload),
    }


def _observations_from_dispatch(
    root: Path,
    scheduled: dict[str, Any],
    audit: dict[str, Any],
    inspection: dict[str, Any],
    *,
    release_module: Any | None = None,
    l4_l5: list[str] | None = None,
    l6: list[str] | None = None,
    l7: list[str] | None = None,
) -> dict[str, list[str]]:
    sqlite_observation = _sqlite_control_observation(root)
    slots = sqlite_observation["slots"]
    by_kind = {row["slotKind"]: row for row in inspection["slots"]}
    assert (
        f"{by_kind['Scheduled']['scheduleId']}/{by_kind['Scheduled']['issueDate']}/{by_kind['Scheduled']['slotKind']}"
        == scheduled["slotKey"]
    )
    assert (
        f"{by_kind['Audit']['scheduleId']}/{by_kind['Audit']['issueDate']}/{by_kind['Audit']['slotKind']}"
        == audit["slotKey"]
    )
    assert by_kind["Scheduled"]["fenceToken"] == scheduled["fenceToken"]
    assert by_kind["Audit"]["fenceToken"] == audit["fenceToken"]
    release_symbols = sorted(
        name
        for name in ("validate_layer_evidence", "admit_final_e2e", "validate_natural_evidence")
        if release_module is not None and callable(getattr(release_module, name, None))
    )
    return {
        "L0": [
            f"{S0_CONFIG_PATH.as_posix()}:{hashlib.sha256(S0_CONFIG_PATH.read_bytes()).hexdigest()}",
            f"{MANIFEST_PATH.as_posix()}:{hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()}",
        ],
        "L1": [
            f"dispatch:{hashlib.sha256((ROOT / 'tools' / 'news_grasp_cleanroom_dispatch.py').read_bytes()).hexdigest()}",
            f"controller:{hashlib.sha256((ROOT / 'tools' / 'news_grasp_cleanroom_controller.py').read_bytes()).hexdigest()}",
            f"releaseSymbols:{','.join(release_symbols)}",
        ],
        "L2": [
            f"sqlite:{sqlite_observation['databaseSha256']}:{sqlite_observation['databaseBytes']}",
            f"wal:{sqlite_observation['walPresent']}",
            f"schema:{sqlite_observation['schemaSha256']}",
        ],
        "L3": [
            f"scheduled-return:{_sha(scheduled)}:{json.dumps(scheduled, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
            f"audit-return:{_sha(audit)}:{json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
            f"sqlite-slots:{sqlite_observation['slotsSha256']}:{sqlite_observation['serializedSlots']}",
            f"inspection:{_sha(inspection)}:{json.dumps(inspection, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
            f"slot-count:{len(slots)}",
        ],
        "L4": list(l4_l5 or [
            f"s1-scheduled:{scheduled['slotKey']}:{scheduled['slotState']}:{scheduled['fenceToken']}",
            f"s1-audit:{audit['slotKey']}:{audit['slotState']}:{audit['fenceToken']}",
        ]),
        "L5": list(l4_l5 or [
            f"recovery-lineage:{scheduled['slotKey']}",
            f"scheduled-terminal:{scheduled.get('resultHash', '')}",
        ]),
        "L6": list(l6 or [f"planned-entrypoint:{_layer_manifest()[6]['plannedEntrypoints']}" ]),
        "L7": list(l7 or [f"planned-entrypoint:{_layer_manifest()[7]['plannedEntrypoints']}" ]),
    }


def _file_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.read_bytes(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _reseal_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    receipt["terminalHash"] = _sha(
        {
            "lineage": receipt["lineage"],
            "issueDate": receipt["issueDate"],
            "generation": receipt["generation"],
            "state": receipt["state"],
        }
    )
    receipt["receiptSha256"] = _sha({key: value for key, value in receipt.items() if key != "receiptSha256"})
    return receipt


def _receipt(lineage: str, *, issue_date: str = ISSUE_DATE, generation: int = 7, state: str = "GREEN") -> dict[str, Any]:
    value = {
        "schemaVersion": "NATURAL_OPERATION_RECEIPT_V1",
        "lineage": lineage,
        "issueDate": issue_date,
        "generation": generation,
        "state": state,
    }
    return _reseal_receipt(value)


def _trusted_receipts() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for index in range(6):
        value: dict[str, Any] = {
            "receiptId": f"NG-CLEANROOM-S{index}-GREEN-V1",
            "schemaVersion": f"NEWS_GRASP_CLEANROOM_S{index}_ACCEPTED_RECEIPT_V1",
            "status": "ACCEPTED",
            "issueDate": ISSUE_DATE,
            "generation": 7,
            "sourceCommit": CURRENT_SOURCE_COMMIT,
            "sourceSha256": _sha({"source": f"S{index}", "commit": CURRENT_SOURCE_COMMIT}),
            "testSha256": _sha({"test": f"S{index}", "commit": CURRENT_SOURCE_COMMIT}),
            "receiptBytes": f"NEWS-GRASP-S{index}-ACCEPTED-{ISSUE_DATE}".encode("utf-8").decode("latin-1"),
        }
        value["receiptBytesSha256"] = hashlib.sha256(value["receiptBytes"].encode("latin-1")).hexdigest()
        value["receiptSha256"] = _sha(value)
        rows[f"S{index}"] = value
    return rows


def _accepted_receipts() -> list[dict[str, Any]]:
    return [deepcopy(value) for value in _trusted_receipts().values()]


def _expected_context(*, observed_at: datetime | None = None, generation: int = 7) -> dict[str, Any]:
    return {
        "schemaVersion": "S6_EXPECTED_CONTEXT_V1",
        "issueDate": ISSUE_DATE,
        "generation": generation,
        "scheduleId": SCHEDULE_ID,
        "sourceCommit": CURRENT_SOURCE_COMMIT,
        "observedAt": (observed_at or _at(6, 42)).isoformat(),
    }


def _task_definition(root: Path) -> dict[str, Any]:
    task = _manifest()["tasks"][0]
    action = task["action"]
    return {
        "taskPath": task["taskPath"],
        "taskName": task["taskName"],
        "enabled": False,
        "executable": str(root / "pythonw.exe"),
        "arguments": list(action["argv"]),
        "workingDirectory": str(root),
        "triggers": deepcopy(task["triggers"]),
        "multipleInstancesPolicy": task["multipleInstancesPolicy"],
    }


def _task_actions(root: Path, installed: Path) -> list[dict[str, Any]]:
    production = _task_definition(root)
    production_action = {
        "taskName": "News-Grasp Production",
        "executable": production["executable"],
        "arguments": list(production["arguments"]),
        "workingDirectory": production["workingDirectory"],
    }
    bootstrap_action = {
        "taskName": "News-Grasp Bootstrap",
        "executable": production["executable"],
        "arguments": [
            str(installed / "scripts/ops/news-grasp-task-launcher.pyw"),
            "bootstrap",
            "--scheduled-task-name",
            "News-Grasp Bootstrap",
            "--high-cost-binding-path",
            str(installed / INSTALLED_BINDING_RELATIVE_PATHS[0]),
        ],
        "workingDirectory": production["workingDirectory"],
    }
    rows = []
    for name in TASK_ACTION_NAMES:
        action = bootstrap_action if name == "News-Grasp Bootstrap" else production_action
        rows.append({"taskName": name, "action": action, "actionSha256": _sha(action)})
    return rows


def _closure_rows(installed: Path) -> list[dict[str, Any]]:
    rows = []
    for relative_path in sorted(INSTALLED_CLOSURE_RELATIVE_PATHS):
        content = (installed / relative_path).read_bytes()
        rows.append({"path": relative_path, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    return rows


def _binding_rows(installed: Path) -> list[dict[str, Any]]:
    rows = []
    for relative_path in sorted(INSTALLED_BINDING_RELATIVE_PATHS):
        value = json.loads((installed / relative_path).read_text(encoding="utf-8"))
        rows.append(
            {
                "schemaVersion": value["schemaVersion"],
                "relativePath": relative_path,
                "sha256": hashlib.sha256((installed / relative_path).read_bytes()).hexdigest(),
            }
        )
    return rows


def _owner_receipts(*, source_sha256: str, installed_sha256: str, task_action_sha256: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for owner_id, receipt_schemas in OWNER_RECEIPT_SCHEMAS.items():
        receipt = {
            "ownerId": owner_id,
            "receiptSchemas": list(receipt_schemas),
            "sourceSha256": source_sha256,
            "installedSha256": installed_sha256,
            "taskActionSha256": task_action_sha256,
            "preimageSha256": _sha({"ownerId": owner_id, "preimage": "s6-test-preimage-v1"}),
        }
        receipt["receiptSha256"] = _sha(receipt)
        rows[owner_id] = receipt
    return rows


def _installed_authority(root: Path, source: Path, installed: Path, launcher: bytes) -> dict[str, Any]:
    source_path = source / "launcher.pyw"
    installed_path = installed / "launcher.pyw"
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    installed_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
    task_definition = _task_definition(root)
    task_actions = _task_actions(root, installed)
    task_action_sha256 = _sha(_manifest()["tasks"][0]["action"])
    task_xml = json.dumps(task_definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value: dict[str, Any] = {
        "schemaVersion": "INSTALLED_AUTHORITY_V1",
        "sourcePath": str(TRACKED_LAUNCHER_PATH),
        "sourceSha256": hashlib.sha256(launcher).hexdigest(),
        "sourceCommit": CURRENT_SOURCE_COMMIT,
        "installedRoot": str(installed),
        "installedPath": str(installed_path),
        "installedSha256": installed_hash,
        "generation": 7,
        "generationReceipt": {"schemaVersion": "INSTALL_GENERATION_RECEIPT_V1", "generation": 7, "sourceSha256": source_hash},
        "taskXml": task_xml,
        "taskXmlSha256": hashlib.sha256(task_xml.encode("utf-8")).hexdigest(),
        "taskAction": task_definition,
        "taskActionSha256": task_action_sha256,
        "installedClosure": _closure_rows(installed),
        "bindings": _binding_rows(installed),
        "taskActions": task_actions,
        "ownerReceipts": _owner_receipts(
            source_sha256=hashlib.sha256(launcher).hexdigest(),
            installed_sha256=installed_hash,
            task_action_sha256=task_action_sha256,
        ),
        "loadedFreshness": "fresh",
        "loadedGeneration": 7,
        "executable": str(root / "pythonw.exe"),
        "argv": list(RAW_ARGV),
        "workingDirectory": str(root),
    }
    value["generationReceiptSha256"] = _sha(value["generationReceipt"])
    value["installedClosureSha256"] = _sha(value["installedClosure"])
    value["bindingsSha256"] = _sha(value["bindings"])
    value["taskActionsSha256"] = _sha(value["taskActions"])
    value["ownerReceiptsSha256"] = _sha(value["ownerReceipts"])
    return value


def _history_coverage(cases: dict[str, Any], receipts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    history = cases["history"]
    daily_rows: list[dict[str, Any]] = []
    for date in history["dates"]:
        lineages: dict[str, dict[str, str]] = {}
        for lineage, receipt in receipts.items():
            dated = deepcopy(receipt)
            dated["issueDate"] = date
            _reseal_receipt(dated)
            lineages[lineage] = {
                "receiptSha256": dated["receiptSha256"],
                "ledgerProvenanceSha256": _sha(
                    {"issueDate": date, "lineage": lineage, "receiptSha256": dated["receiptSha256"]}
                ),
            }
        row: dict[str, Any] = {
            "issueDate": date,
            "timezone": cases["timezone"],
            "introductionDate": history["introductionDate"],
            "missing": {"Scheduled": [], "Audit": [], "Public": [], "Readiness": []},
            "lineages": lineages,
        }
        row["ledgerProvenanceSha256"] = _sha({key: value for key, value in row.items() if key != "ledgerProvenanceSha256"})
        daily_rows.append(row)
    corpus: list[dict[str, Any]] = []
    for domain, count in (("Scheduled", history["scheduledCorpusCount"]), ("Audit", history["auditCorpusCount"])):
        for index in range(count):
            receipt_sha = receipts[domain if domain in receipts else "Scheduled"]["receiptSha256"]
            row = {
                "caseId": f"{domain.lower()}-{index:03d}",
                "domain": domain,
                "issueDate": history["dates"][index % len(history["dates"])],
                "receiptSha256": receipt_sha,
                "ledgerProvenanceSha256": _sha({"domain": domain, "index": index, "receiptSha256": receipt_sha}),
            }
            corpus.append(row)
    return {
        "schemaVersion": "HISTORY_COVERAGE_V1",
        "timezone": cases["timezone"],
        "introductionDate": history["introductionDate"],
        "dates": list(history["dates"]),
        "dailyEvidence": daily_rows,
        "missingDays": [],
        "corpusEntries": corpus,
        "corpusCounts": {
            "Scheduled": sum(row["domain"] == "Scheduled" for row in corpus),
            "Audit": sum(row["domain"] == "Audit" for row in corpus),
        },
        "legacyWriterCount": history["legacyWriterCount"],
    }


def _natural_evidence(tmp_path: Path, index: int = 0) -> tuple[Path, dict[str, Any]]:
    root, source, installed, launcher = _runtime_root(tmp_path, index)
    (installed / "launcher.pyw").write_bytes(launcher)
    source_hash = hashlib.sha256((source / "launcher.pyw").read_bytes()).hexdigest()
    installed_hash = hashlib.sha256((installed / "launcher.pyw").read_bytes()).hexdigest()
    cases = _cases()
    receipts = {
        "Scheduled": _receipt("Scheduled"),
        "Audit": _receipt("Audit"),
        "Public": _receipt("Public"),
        "Readiness": _receipt("Readiness"),
    }
    value: dict[str, Any] = {
        "schemaVersion": "NATURAL_OPERATION_EVIDENCE_V1",
        "issueDate": ISSUE_DATE,
        "generation": cases["natural"]["generation"],
        "installed": {
            "commit": CURRENT_SOURCE_COMMIT,
            "sourceSha256": source_hash,
            "installedSha256": installed_hash,
            "freshness": cases["natural"]["freshness"],
            "observedAt": _at(6, 42).isoformat(),
        },
        "receipts": receipts,
        "historyCoverage": _history_coverage(cases, receipts),
    }
    value["naturalEvidenceSha256"] = _sha(value)
    return root, value


def _admission(tmp_path: Path, index: int = 0) -> tuple[Path, dict[str, Any]]:
    root, source, installed, launcher = _runtime_root(tmp_path, index)
    (installed / "launcher.pyw").write_bytes(launcher)
    dispatch_module = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    scheduled, audit, inspection = _dispatch_pair(root, root / "manifest.json", dispatch_module, index)
    release_module = importlib.import_module("tools.news_grasp_cleanroom_release")
    _l4_root, _l4_values, l4_observed = _real_l4_l5_boundary(tmp_path, index + 100)
    _l6_root, _l6_values, l6_observed = _real_l6_boundary(tmp_path, index + 200)
    _l7_root, _l7_values, l7_observed = _real_l7_boundary(tmp_path, index + 300)
    observations = _observations_from_dispatch(
        root,
        scheduled,
        audit,
        inspection,
        release_module=release_module,
        l4_l5=l4_observed,
        l6=l6_observed,
        l7=l7_observed,
    )
    layers = _layer_evidence(observed=observations)
    authority = _installed_authority(root, source, installed, launcher)
    value: dict[str, Any] = {
        "schemaVersion": "E2E_FINAL_ADMISSION_V1",
        "admissionId": f"s6-final-admission-{index}",
        "issueDate": ISSUE_DATE,
        "generation": 7,
        "acceptedReceipts": _accepted_receipts(),
        "layerEvidence": layers,
        "layerEvidenceSha256": _sha(layers),
        "installedSourceSha256": authority["sourceSha256"],
        "installedHashSha256": authority["installedSha256"],
        "installedAuthoritySha256": _sha(authority),
        "externalMutationSuppressed": True,
        "externalMutationCount": 0,
        "attemptBudget": 1,
        "attemptsUsed": 0,
        "independentBlockerCount": 0,
        "mode": "final_confirmation_only",
    }
    value["admissionSha256"] = _sha(value)
    return root, value


def _reseal_layer_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidenceSha256"] = _sha({key: value for key, value in row.items() if key != "evidenceSha256"})
    return row


def _reseal_layer_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        _reseal_layer_row(row)
    return rows


def _reseal_admission(value: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("layerEvidence"), list):
        _reseal_layer_evidence(value["layerEvidence"])
        value["layerEvidenceSha256"] = _sha(value["layerEvidence"])
    value["admissionSha256"] = _sha({key: item for key, item in value.items() if key != "admissionSha256"})
    return value


def _reseal_installed_authority(value: dict[str, Any], *, preserve: set[str] | None = None) -> dict[str, Any]:
    preserve = preserve or set()
    if "generationReceipt" in value and "generationReceiptSha256" not in preserve:
        value["generationReceiptSha256"] = _sha(value["generationReceipt"])
    if "taskActions" in value:
        for row in value["taskActions"]:
            if "taskActionHash" not in preserve:
                row["actionSha256"] = _sha(row["action"])
        if "taskActionsSha256" not in preserve:
            value["taskActionsSha256"] = _sha(value["taskActions"])
    if "ownerReceipts" in value:
        for receipt in value["ownerReceipts"].values():
            if "ownerReceiptHash" not in preserve:
                receipt["receiptSha256"] = _sha({key: item for key, item in receipt.items() if key != "receiptSha256"})
        if "ownerReceiptsSha256" not in preserve:
            value["ownerReceiptsSha256"] = _sha(value["ownerReceipts"])
    if "installedClosureSha256" not in preserve:
        value["installedClosureSha256"] = _sha(value["installedClosure"])
    if "bindingsSha256" not in preserve:
        value["bindingsSha256"] = _sha(value["bindings"])
    if "authoritySha256" not in preserve:
        value["authoritySha256"] = _sha({key: item for key, item in value.items() if key != "authoritySha256"})
    return value


def _mutate_installed_authority(value: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = deepcopy(value)
    preserve: set[str] = set()
    if case == "missing_closure_row":
        mutated["installedClosure"].pop()
    elif case == "unknown_closure_row":
        mutated["installedClosure"].append({"path": "tools/unknown.py", "sha256": "a" * 64, "bytes": 1})
    elif case == "duplicate_closure_row":
        mutated["installedClosure"].append(deepcopy(mutated["installedClosure"][-1]))
    elif case == "unsorted_closure_rows":
        mutated["installedClosure"].reverse()
    elif case == "closure_path_escape":
        mutated["installedClosure"][0]["path"] = "../escape.py"
    elif case == "closure_bytes_drift":
        mutated["installedClosure"][0]["bytes"] += 1
    elif case == "closure_sha_drift":
        mutated["installedClosure"][0]["sha256"] = "0" * 64
        preserve.add("installedClosureSha256")
    elif case == "binding_schema_drift":
        mutated["bindings"][0]["schemaVersion"] = "DRIFTED_BINDING_V9"
    elif case == "binding_hash_drift":
        mutated["bindings"][0]["sha256"] = "0" * 64
        preserve.add("bindingsSha256")
    elif case == "task_action_executable_drift":
        mutated["taskActions"][0]["action"]["executable"] = str(Path(mutated["executable"]).with_name("evil.exe"))
    elif case == "task_action_argv_drift":
        mutated["taskActions"][0]["action"]["arguments"].append("--drift")
    elif case == "task_action_workdir_drift":
        mutated["taskActions"][0]["action"]["workingDirectory"] = "../escape"
    elif case == "task_action_hash_drift":
        mutated["taskActions"][0]["actionSha256"] = "0" * 64
        preserve.add("taskActionHash")
    elif case == "missing_owner_receipt":
        mutated["ownerReceipts"].pop("ops_install_owner")
    elif case == "owner_receipt_schema_drift":
        mutated["ownerReceipts"]["runtime_generation_owner"]["receiptSchemas"].append("DRIFTED_RECEIPT_V9")
    elif case == "owner_receipt_inner_hash_drift":
        mutated["ownerReceipts"]["runtime_generation_owner"]["receiptSha256"] = "0" * 64
        preserve.add("ownerReceiptHash")
    elif case == "owner_preimage_substitution":
        mutated["ownerReceipts"]["runtime_generation_owner"]["preimageSha256"] = "f" * 64
    elif case == "owner_receipts_hash_drift":
        mutated["ownerReceiptsSha256"] = "0" * 64
        preserve.add("ownerReceiptsSha256")
    elif case == "loaded_generation_mismatch":
        mutated["loadedGeneration"] = mutated["generation"] - 1
    elif case == "loaded_freshness_stale":
        mutated["loadedFreshness"] = "stale"
    else:
        raise AssertionError(f"unknown installed authority case: {case}")
    return _reseal_installed_authority(mutated, preserve=preserve)


def _reseal_natural(value: dict[str, Any]) -> dict[str, Any]:
    for receipt in value.get("receipts", {}).values():
        if isinstance(receipt, dict) and {"lineage", "issueDate", "generation", "state"}.issubset(receipt):
            _reseal_receipt(receipt)
    value["naturalEvidenceSha256"] = _sha({key: item for key, item in value.items() if key != "naturalEvidenceSha256"})
    return value


def _mutate_layer(rows: list[dict[str, Any]], case: str) -> list[dict[str, Any]]:
    value = deepcopy(rows)
    if case == "unknown_layer":
        value[0]["layer"] = "L10"
    elif case == "missing_layer":
        value.pop()
    elif case == "duplicate_layer":
        value.append(deepcopy(value[-1]))
    elif case == "schema_drift":
        for row in value:
            row["schemaIdentity"] = "DRIFTED_SCHEMA_V9"
    elif case == "fake_not_sealed":
        for row in value:
            row["fakeUsed"].append("unsealed provider")
    elif case == "real_boundary_missing":
        for row in value:
            row["realObserved"].pop()
    elif case == "real_required_drift":
        for row in value:
            row["realRequired"].append("unbound boundary")
    elif case == "observed_drift":
        for row in value:
            row["realObserved"] = [f"unbound-observation-{row['layer']}"]
    elif case == "hash_drift":
        value[0]["evidenceSha256"] = "0" * 64
    elif case == "status_not_green":
        for row in value:
            row["status"] = "UNVERIFIED"
    elif case == "unknown_key":
        for row in value:
            row["unexpected"] = "drift"
    else:
        raise AssertionError(f"unknown layer case: {case}")
    if case != "hash_drift" and case not in {"missing_layer", "duplicate_layer"}:
        _reseal_layer_evidence(value)
    return value


def _mutate_admission(value: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = deepcopy(value)
    if case == "missing_receipt":
        mutated["acceptedReceipts"].pop()
    elif case == "unknown_receipt":
        mutated["acceptedReceipts"][0]["receiptId"] = "NG-UNKNOWN-V9"
        mutated["acceptedReceipts"][0]["receiptSha256"] = _sha(
            {key: item for key, item in mutated["acceptedReceipts"][0].items() if key != "receiptSha256"}
        )
    elif case == "stale_receipt_hash":
        mutated["acceptedReceipts"][0]["receiptSha256"] = "0" * 64
    elif case == "duplicate_receipt":
        mutated["acceptedReceipts"].append(deepcopy(mutated["acceptedReceipts"][0]))
    elif case == "missing_layer_hash":
        mutated.pop("layerEvidenceSha256")
    elif case == "unknown_layer_hash":
        mutated["layerEvidence"].append(deepcopy(mutated["layerEvidence"][-1]))
        mutated["layerEvidence"][-1]["layer"] = "L8"
        _reseal_layer_row(mutated["layerEvidence"][-1])
    elif case == "installed_source_drift":
        mutated["installedSourceSha256"] = "f" * 64
    elif case == "installed_hash_drift":
        mutated["installedHashSha256"] = "e" * 64
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
    resealed = _reseal_admission(mutated)
    if case == "missing_layer_hash":
        resealed.pop("layerEvidenceSha256", None)
        resealed["admissionSha256"] = _sha(
            {key: item for key, item in resealed.items() if key != "admissionSha256"}
        )
    return resealed


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
    elif case == "receipt_hash_drift":
        mutated["receipts"]["Scheduled"]["receiptSha256"] = "0" * 64
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
    _reseal_natural(mutated)
    if case == "receipt_hash_drift":
        mutated["receipts"]["Scheduled"]["receiptSha256"] = "0" * 64
        mutated["naturalEvidenceSha256"] = _sha(
            {key: item for key, item in mutated.items() if key != "naturalEvidenceSha256"}
        )
    return mutated


class _Admission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(deepcopy(request))
        value = {
            "schemaVersion": "HIGH_COST_ADMISSION_DECISION_V1",
            "status": "GRANTED",
            "authorityId": request["authority"]["authorityId"],
            "authoritySha256": request["authority"]["authoritySha256"],
            "idempotencyKey": request["idempotencyKey"],
        }
        value["decisionSha256"] = _sha(value)
        return value


class _Provider:
    def __init__(self) -> None:
        self.dispatch_calls: list[dict[str, Any]] = []
        self.query_calls: list[str] = []

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        self.dispatch_calls.append(deepcopy(request))
        key = request["idempotencyKey"]
        return {
            "schemaVersion": "EXTERNAL_RESULT_RECEIPT_V1",
            "status": "CONFIRMED",
            "idempotencyKey": key,
            "externalReceiptId": f"s6-{_sha(key)[:16]}",
            "effectHash": _sha({"idempotencyKey": key, "effect": "deterministic"}),
        }

    def query(self, idempotency_key: str) -> dict[str, Any]:
        self.query_calls.append(idempotency_key)
        return {
            "status": "PRESENT",
            "receipt": {
                "schemaVersion": "EXTERNAL_RESULT_RECEIPT_V1",
                "status": "CONFIRMED",
                "idempotencyKey": idempotency_key,
                "externalReceiptId": f"s6-{_sha(idempotency_key)[:16]}",
                "effectHash": _sha({"idempotencyKey": idempotency_key, "effect": "deterministic"}),
            },
        }


class _Stage:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, stage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(stage_id)
        return {"stageId": stage_id, "outputHash": _sha({"stage": stage_id, "input": payload})}


class _Publisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.queries: list[str] = []
        self.receipts: dict[str, dict[str, Any]] = {}

    def publish(self, request: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(request)
        self.calls.append(request)
        key = request["idempotencyKey"]
        receipt = self.receipts.setdefault(
            key,
            {
                "schemaVersion": "PUBLIC_SURFACE_RECEIPT_V1",
                "idempotencyKey": key,
                "surfaceId": request["surfaceId"],
                "status": "CONFIRMED",
                "terminalHash": _sha(request),
            },
        )
        return deepcopy(receipt)

    def query(self, idempotency_key: str) -> dict[str, Any] | None:
        self.queries.append(idempotency_key)
        return deepcopy(self.receipts.get(idempotency_key))


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.queries: list[str] = []
        self.receipts: dict[str, dict[str, Any]] = {}

    def notify(self, request: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(request)
        self.calls.append(request)
        key = request["idempotencyKey"]
        receipt = self.receipts.setdefault(
            key,
            {
                "schemaVersion": "PUBLIC_NOTIFICATION_RECEIPT_V1",
                "idempotencyKey": key,
                "status": "CONFIRMED",
                "terminalHash": _sha(request),
            },
        )
        return deepcopy(receipt)

    def query(self, idempotency_key: str) -> dict[str, Any] | None:
        self.queries.append(idempotency_key)
        return deepcopy(self.receipts.get(idempotency_key))


class _TaskAdapter:
    """Task Schedulerを触らず、S5公開protocolの登録定義とstateを記録する。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self.enabled = {"News-Grasp Production": False, "News-Grasp Production (old)": True}
        self.definitions: dict[str, Any] = {"News-Grasp Production (old)": "<old-task-v1/>"}
        self.registered: dict[str, Any] = {}
        self.history: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return {"tasks": {key: {"enabled": value, "definition": deepcopy(self.definitions.get(key))} for key, value in self.enabled.items()}}

    def register_disabled(self, name: str, definition: dict[str, Any], **kwargs: Any) -> None:
        value = deepcopy(definition)
        value.update(deepcopy(kwargs))
        self.registered[name] = value
        self.definitions[name] = deepcopy(value)
        self.enabled[name] = False
        self.history.append({"operation": "register_disabled", "name": name, "definition": value})

    def disable(self, name: str) -> None:
        self.enabled[name] = False
        self.history.append({"operation": "disable", "name": name})

    def enable(self, name: str) -> None:
        self.enabled[name] = True
        self.history.append({"operation": "enable", "name": name})

    def restore(self, name: str, definition: Any) -> None:
        self.definitions[name] = deepcopy(definition)
        self.enabled[name] = False
        self.history.append({"operation": "restore", "name": name, "definition": deepcopy(definition)})

    def remove_candidate(self, name: str) -> None:
        self.registered.pop(name, None)
        self.enabled[name] = False
        self.history.append({"operation": "remove_candidate", "name": name})

    def task_definition(self, name: str) -> Any:
        return deepcopy(self.definitions[name])

    def registered_definition(self, name: str) -> dict[str, Any]:
        return deepcopy(self.registered[name])

    def task_state(self) -> dict[str, bool]:
        return dict(self.enabled)


def _s1_slot(root: Path, slot_kind: str) -> dict[str, Any]:
    database = root / "control" / "control-ledger-v1.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT schedule_id,issue_date,slot_kind,generation,state,owner_key,fence_token,lease_expires_at,terminal_state,result_hash,updated_at FROM slots WHERE schedule_id=? AND issue_date=? AND slot_kind=?",
            (SCHEDULE_ID, ISSUE_DATE, slot_kind),
        ).fetchone()
    if row is None:
        raise AssertionError(f"S1 {slot_kind} row is missing")
    return {
        "scheduleId": row["schedule_id"],
        "issueDate": row["issue_date"],
        "slotKind": row["slot_kind"],
        "generation": row["generation"],
        "state": row["state"],
        "ownerKey": row["owner_key"],
        "fenceToken": row["fence_token"],
        "leaseExpiresAt": row["lease_expires_at"],
        "terminalState": row["terminal_state"],
        "resultHash": row["result_hash"],
        "updatedAt": row["updated_at"],
        "slotKey": f"{row['schedule_id']}/{row['issue_date']}/{row['slot_kind']}",
    }


def _s2_authority(root: Path, slot: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "EXECUTION_AUTHORITY_V1",
        "authorityId": f"s6-execution-{slot['slotKind'].lower()}",
        "scheduleId": slot["scheduleId"],
        "issueDate": slot["issueDate"],
        "slotKey": slot["slotKey"],
        "generation": slot["generation"],
        "ownerKey": slot["ownerKey"],
        "fenceToken": slot["fenceToken"],
        "maxDispatchAttempts": 1,
    }
    value["authoritySha256"] = _sha(value)
    return value


def _s4_parent(slot: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_PARENT_V1",
        "lineage": "Scheduled",
        "issueDate": ISSUE_DATE,
        "scheduleId": SCHEDULE_ID,
        "slotKey": slot["slotKey"],
        "terminalState": "FAILED",
        "terminalHash": slot["resultHash"],
        "generation": slot["generation"],
    }
    value["parentSha256"] = _sha(value)
    return value


def _s4_authority(parent: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_AUTHORITY_V1",
        "authorityId": "s6-recovery-authority",
        "issueDate": ISSUE_DATE,
        "scheduledParentTerminalHash": parent["terminalHash"],
        "scheduledGeneration": parent["generation"],
        "auditOwnerKey": audit["ownerKey"],
        "auditFenceToken": audit["fenceToken"],
        "maxAttempts": 1,
    }
    value["authoritySha256"] = _sha(value)
    return value


def _s4_budget(authority: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schemaVersion": "RECOVERY_BUDGET_V1",
        "authorityId": authority["authorityId"],
        "authoritySha256": authority["authoritySha256"],
        "remainingAttempts": 1,
    }
    value["budgetSha256"] = _sha(value)
    return value


def _public_inventory() -> dict[str, Any]:
    surfaces = [
        {
            "surfaceId": surface_id,
            "status": "CONFIRMED",
            "artifactSha256": _sha({"issueDate": ISSUE_DATE, "surfaceId": surface_id}),
        }
        for surface_id in ("web", "archive", "podcast")
    ]
    value: dict[str, Any] = {
        "schemaVersion": "PUBLIC_SURFACE_INVENTORY_V1",
        "issueDate": ISSUE_DATE,
        "requiredSurfaceIds": ["web", "archive", "podcast"],
        "eligibleNotRequiredSurfaceIds": ["podcast"],
        "surfaces": surfaces,
    }
    value["inventorySha256"] = _sha(value)
    return value


def _real_l4_l5_boundary(tmp_path: Path, index: int) -> tuple[Path, dict[str, Any], list[str]]:
    root, _source, _installed, _launcher = _runtime_root(tmp_path, index)
    dispatch_module = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    deterministic_clock = lambda: _at(6, 43)
    scheduled_return = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=root / "manifest.json",
        observed_at=_at(6, 1),
        writer=_writer(index),
        lease_seconds=3600,
        writer_attestor=_FakeWriterAttestor(),
        clock=deterministic_clock,
    )
    audit_return = dispatch_module.dispatch(
        raw_argv=RAW_ARGV,
        runtime_root=root,
        manifest_path=root / "manifest.json",
        observed_at=_at(6, 41),
        writer={**_writer(index), "writerId": f"s6-audit-{index}", "pid": 13000 + index},
        lease_seconds=3600,
        writer_attestor=_FakeWriterAttestor(),
        clock=deterministic_clock,
    )
    audit_slot = _s1_slot(root, "Audit")
    audit_authority = _s2_authority(root, audit_slot)
    execution_module = importlib.import_module("tools.news_grasp_cleanroom_execution")
    admission = _Admission()
    provider = _Provider()
    stage = _Stage()
    execution_controller = execution_module.ExecutionController(
        root,
        admission_adapter=admission,
        provider=provider,
        stage_runner=stage,
    )
    execution_result = execution_controller.execute(
        slot_key=audit_slot["slotKey"],
        issue_date=ISSUE_DATE,
        authority=audit_authority,
        payload={"source": "s6-l4", "issueDate": ISSUE_DATE},
        observed_at=_at(6, 42),
    )
    public_module = importlib.import_module("tools.news_grasp_cleanroom_public")
    publisher = _Publisher()
    notifier = _Notifier()
    public_controller = public_module.PublicController(root, publisher=publisher, notifier=notifier)
    public_result = public_controller.reconcile(
        issue_date=ISSUE_DATE,
        scheduled_state="GREEN",
        recovery_state="GREEN",
        readiness_state="GREEN",
        inventory=_public_inventory(),
        observed_at=_at(6, 42),
    )
    commit_result = dispatch_module.commit_slot(
        runtime_root=root,
        manifest_path=root / "manifest.json",
        slot_key=scheduled_return["slotKey"],
        writer=_writer(index),
        fence_token=scheduled_return["fenceToken"],
        terminal_state="FAILED",
        result_hash=_sha({"lineage": "Scheduled", "terminal": "FAILED", "index": index}),
        observed_at=_at(6, 43),
        writer_attestor=_FakeWriterAttestor(),
        clock=deterministic_clock,
    )
    scheduled_slot = _s1_slot(root, "Scheduled")
    parent = _s4_parent(scheduled_slot)
    recovery_authority = _s4_authority(parent, audit_slot)
    recovery_budget = _s4_budget(recovery_authority)
    execution_calls = 0
    public_calls = 0

    def execution_child(_request: dict[str, Any]) -> dict[str, Any]:
        nonlocal execution_calls
        execution_calls += 1
        return {
            "schemaVersion": "EXECUTION_RECONCILE_RESULT_V1",
            "status": "CONFIRMED",
            "lineage": "execution",
            "terminalHash": _sha(execution_result),
        }

    def public_child(_request: dict[str, Any]) -> dict[str, Any]:
        nonlocal public_calls
        public_calls += 1
        return {
            "schemaVersion": "PUBLIC_RECONCILE_RESULT_V1",
            "status": "CONFIRMED",
            "lineage": "public",
            "terminalHash": _sha(public_result),
        }

    recovery_module = importlib.import_module("tools.news_grasp_cleanroom_recovery")
    recovery_controller = recovery_module.RecoveryController(
        root,
        execution_reconciler=execution_child,
        public_reconciler=public_child,
    )
    recovery_result = recovery_controller.audit(
        issue_date=ISSUE_DATE,
        parent=parent,
        authority=recovery_authority,
        budget=recovery_budget,
        observed_at=_at(6, 44),
    )
    retry = recovery_controller.audit(
        issue_date=ISSUE_DATE,
        parent=parent,
        authority=recovery_authority,
        budget=recovery_budget,
        observed_at=_at(6, 45),
    )
    assert recovery_result == retry
    assert execution_calls == 1 and public_calls == 1
    assert recovery_result["recoveryHistory"][-1]["lineage"] == "Recovery"
    assert recovery_result["execution"]["terminalHash"] == _sha(execution_result)
    assert recovery_result["public"]["terminalHash"] == _sha(public_result)
    assert scheduled_slot["terminalState"] == "FAILED"
    assert scheduled_slot["resultHash"] == commit_result["resultHash"]
    return root, {
        "scheduled": scheduled_return,
        "audit": audit_return,
        "execution": execution_result,
        "public": public_result,
        "recovery": recovery_result,
        "scheduledTerminal": scheduled_slot,
    }, [
        f"S2-terminal:{_sha(execution_result)}",
        f"S3-terminal:{_sha(public_result)}",
        f"S4-terminal:{_sha(recovery_result)}",
        f"Scheduled-terminal:{scheduled_slot['resultHash']}",
        "Recovery-lineage:Recovery",
        "terminal-retry-child-delta:execution=0,public=0",
    ]


def _cli_args(root: Path, manifest_path: Path, writer_path: Path, *, fault: bool = False) -> list[str]:
    arguments = [
        sys.executable,
        "-m",
        "tools.news_grasp_cleanroom_dispatch",
        *RAW_ARGV,
        "--runtime-root",
        str(root),
        "--manifest-path",
        str(manifest_path),
        "--observed-at",
        _at(6, 1).isoformat(),
        "--writer-json",
        str(writer_path),
    ]
    if fault:
        arguments.extend(["--fault-after-initial-wal"])
    return arguments


def _run_cli(root: Path, *, manifest_path: Path | None = None, fault: bool = False) -> subprocess.CompletedProcess[str]:
    writer_path = root / "writer.json"
    writer_path.write_text(json.dumps(_writer(77), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        _cli_args(root, manifest_path or (root / "manifest.json"), writer_path, fault=fault),
        cwd=ROOT,
        env=environment,
        shell=False,
        creationflags=CREATE_NO_WINDOW,
        encoding="utf-8",
        text=True,
        capture_output=True,
        check=False,
    )


def _real_l6_boundary(tmp_path: Path, index: int) -> tuple[Path, dict[str, Any], list[str]]:
    root, _source, _installed, _launcher = _runtime_root(tmp_path, index)
    fault = _run_cli(root, fault=True)
    assert fault.returncode != 0
    fault_text = f"{fault.stdout}\n{fault.stderr}"
    assert "NEWS_GRASP_ENTRY" in fault_text
    wal_root = root / "control" / "wal"
    assert wal_root.exists() and list(wal_root.rglob("*.json"))
    normal = _run_cli(root)
    assert normal.returncode == 0, normal.stderr
    lines = [line for line in normal.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    result = json.loads(lines[0])
    assert result["schemaVersion"] == "RECONCILE_RESULT_V1"
    restarted = _run_cli(root)
    assert restarted.returncode == 0, restarted.stderr
    restarted_lines = [line for line in restarted.stdout.splitlines() if line.strip()]
    assert len(restarted_lines) == 1
    restarted_result = json.loads(restarted_lines[0])
    assert restarted_result == result
    corrupt_manifest = root / "manifest-corrupt.json"
    corrupt_manifest.write_bytes(b"{not-json")
    corrupt = _run_cli(root, manifest_path=corrupt_manifest)
    assert corrupt.returncode != 0
    corrupt_text = f"{corrupt.stdout}\n{corrupt.stderr}"
    assert "NEWS_GRASP_ENTRY_MANIFEST_INVALID" in corrupt_text
    assert "NEWS_GRASP_ENTRY_MANIFEST_INVALID" not in fault_text
    return root, result, [
        f"cli-command:{' '.join(_cli_args(root, root / 'manifest.json', root / 'writer.json'))}",
        f"stdout:{_sha(result)}:{json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}",
        f"fault-exit:{fault.returncode}:{fault_text.strip()}",
        f"restart:{_sha(restarted_result)}:equal={restarted_result == result}",
        f"wal-files:{','.join(sorted(path.relative_to(root).as_posix() for path in wal_root.rglob('*.json')))}",
        f"corrupt-exit:{corrupt.returncode}:{corrupt_text.strip()}",
    ]


def _real_l7_boundary(tmp_path: Path, index: int) -> tuple[Path, dict[str, Any], list[str]]:
    root, source, installed, launcher = _runtime_root(tmp_path, index)
    install_module = importlib.import_module("tools.news_grasp_cleanroom_install")
    manifest = _manifest()
    authority: dict[str, Any] = {
        "schemaVersion": "INSTALL_AUTHORITY_V1",
        "authorityId": f"s6-install-{index}",
        "issueDate": ISSUE_DATE,
        "generation": 1,
        "sourceRoot": str(source),
        "sourceSha256": hashlib.sha256((source / "launcher.pyw").read_bytes()).hexdigest(),
    }
    authority["ownerReceipts"] = _owner_receipts(
        source_sha256=authority["sourceSha256"],
        installed_sha256=authority["sourceSha256"],
        task_action_sha256=_sha(_manifest()["tasks"][0]["action"]),
    )
    authority["authoritySha256"] = _sha(authority)
    task = _TaskAdapter(install_module)
    controller = install_module.InstallCutoverController(
        root,
        task_adapter=task,
        pythonw_path=root / "pythonw.exe",
    )
    staged = controller.stage(manifest, source, installed, authority, _at(6, 0))
    assert isinstance(staged, dict)
    assert staged["schemaVersion"] == "INSTALL_STAGE_RESULT_V1"
    assert isinstance(staged["generation"], int)
    assert isinstance(staged["journal"], dict)
    assert isinstance(staged["installedReceipt"], dict)
    assert task.task_state()["News-Grasp Production"] is False
    assert task.task_state()["News-Grasp Production (old)"] is True
    assert task.registered_definition("News-Grasp Production")["enabled"] is False
    assert sum(row["operation"] == "register_disabled" for row in task.history) == 1
    assert sum(row["operation"] == "disable" for row in task.history) == 0
    assert sum(row["operation"] == "enable" for row in task.history) == 0
    installed_path = installed / "launcher.pyw"
    assert installed_path.read_bytes() == launcher
    installed_hash = hashlib.sha256(installed_path.read_bytes()).hexdigest()
    assert staged["installedReceipt"]["installedSha256"] == installed_hash
    return root, {
        "stage": staged,
        "taskDefinition": task.registered_definition("News-Grasp Production"),
        "installedAuthority": _installed_authority(root, source, installed, launcher),
        "installedSha256": installed_hash,
    }, [
        f"tracked-source:{TRACKED_LAUNCHER_PATH}:{hashlib.sha256(launcher).hexdigest()}",
        f"installed:{installed_path}:{installed_hash}",
        f"generation:{staged['generation']}",
        f"journal:{_sha(staged['journal'])}",
        f"installed-receipt:{_sha(staged['installedReceipt'])}",
        f"task-action:{_sha(task.registered_definition('News-Grasp Production'))}",
    ]


def test_s6_l3_l7_real_boundary_manifest(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    manifest = _layer_manifest()
    assert len(manifest) == 10
    assert [row["layer"] for row in manifest] == cases["layerOrder"]
    assert list(inspect.signature(module.validate_layer_evidence).parameters) == [
        "evidence",
        "sealed_manifest",
        "trusted_receipts",
        "expected_context",
        "installed_authority",
    ]

    root, source, installed, launcher = _runtime_root(tmp_path, 1)
    installed.mkdir(parents=True, exist_ok=True)
    (installed / "launcher.pyw").write_bytes(launcher)
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
    _l4_root, _l4_values, l4_observed = _real_l4_l5_boundary(tmp_path, 101)
    _l6_root, _l6_result, l6_observed = _real_l6_boundary(tmp_path, 102)
    _l7_root, _l7_values, l7_observed = _real_l7_boundary(tmp_path, 103)
    observed = _observations_from_dispatch(
        root,
        scheduled,
        audit,
        inspection,
        release_module=module,
        l4_l5=l4_observed,
        l6=l6_observed,
        l7=l7_observed,
    )
    evidence = _layer_evidence(observed=observed)
    authority = _installed_authority(root, source, installed, launcher)
    validated = module.validate_layer_evidence(
        evidence,
        manifest[:8],
        _trusted_receipts(),
        _expected_context(),
        authority,
    )
    assert validated["schemaVersion"] == "LAYER_EVIDENCE_V1"
    assert validated["status"] == "GREEN"
    assert validated["layers"] == [row["layer"] for row in evidence]
    assert all(row["realObserved"] for row in evidence)
    assert manifest[3]["plannedEntrypoints"] == [
        {"path": "tools/news_grasp_cleanroom_dispatch.py", "symbols": ["dispatch"]},
        {"path": "tools/news_grasp_cleanroom_controller.py", "symbols": ["Controller.reconcile"]},
    ]
    assert manifest[6]["plannedEntrypoints"][0]["module"] == "tools.news_grasp_cleanroom_dispatch"
    assert manifest[7]["plannedEntrypoints"][0]["argv"] == RAW_ARGV

    for index, case in enumerate(cases["layerEvidenceCases"], start=10):
        negative = _mutate_layer(evidence, case)
        before = _file_inventory(root)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_layer_evidence(
                negative,
                manifest[:8],
                _trusted_receipts(),
                _expected_context(),
                authority,
            )
        assert _file_inventory(root) == before

    for case in cases["installedClosureCases"]:
        negative_authority = _mutate_installed_authority(authority, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_layer_evidence(
                evidence,
                manifest[:8],
                _trusted_receipts(),
                _expected_context(),
                negative_authority,
            )
        assert _file_inventory(root) == before


def test_s6_installed_bytes_args_workdir(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    _l4_root, _l4_values, l4_observed = _real_l4_l5_boundary(tmp_path, 1)
    l6_root, l6_result, l6_observed = _real_l6_boundary(tmp_path, 2)
    l7_root, l7_values, l7_observed = _real_l7_boundary(tmp_path, 3)
    assert l6_result["schemaVersion"] == "RECONCILE_RESULT_V1"
    assert l6_result["status"] in {"GREEN", "TERMINAL", "accepted", "noop"}
    assert l7_values["stage"]["schemaVersion"] == "INSTALL_STAGE_RESULT_V1"
    assert l7_values["taskDefinition"]["enabled"] is False
    assert l7_values["taskDefinition"]["arguments"] == RAW_ARGV
    assert l7_values["taskDefinition"]["workingDirectory"] == str(l7_root)
    assert l7_values["installedAuthority"]["sourcePath"] == str(TRACKED_LAUNCHER_PATH)
    assert l7_values["installedAuthority"]["installedSha256"] == l7_values["installedSha256"]
    installed_authority = l7_values["installedAuthority"]
    assert [row["path"] for row in installed_authority["installedClosure"]] == sorted(INSTALLED_CLOSURE_RELATIVE_PATHS)
    assert installed_authority["installedClosureSha256"] == _sha(installed_authority["installedClosure"])
    assert [row["relativePath"] for row in installed_authority["bindings"]] == sorted(INSTALLED_BINDING_RELATIVE_PATHS)
    assert installed_authority["bindingsSha256"] == _sha(installed_authority["bindings"])
    assert [row["taskName"] for row in installed_authority["taskActions"]] == TASK_ACTION_NAMES
    assert installed_authority["taskActionsSha256"] == _sha(installed_authority["taskActions"])
    assert set(installed_authority["ownerReceipts"]) == set(OWNER_RECEIPT_SCHEMAS)
    assert installed_authority["ownerReceiptsSha256"] == _sha(installed_authority["ownerReceipts"])
    assert installed_authority["loadedFreshness"] == "fresh"
    assert installed_authority["loadedGeneration"] == installed_authority["generation"]

    dispatch_module = importlib.import_module("tools.news_grasp_cleanroom_dispatch")
    evidence_root, _source, _installed, _launcher = _runtime_root(tmp_path, 4)
    scheduled, audit, inspection = _dispatch_pair(evidence_root, evidence_root / "manifest.json", dispatch_module, 4)
    observations = _observations_from_dispatch(
        evidence_root,
        scheduled,
        audit,
        inspection,
        release_module=module,
        l4_l5=l4_observed,
        l6=l6_observed,
        l7=l7_observed,
    )
    evidence = _layer_evidence(observed=observations)
    (evidence_root / "installed" / "launcher.pyw").write_bytes(_launcher)
    authority = _installed_authority(evidence_root, evidence_root / "source", evidence_root / "installed", _launcher)
    validated = module.validate_layer_evidence(
        evidence,
        _layer_manifest()[:8],
        _trusted_receipts(),
        _expected_context(),
        authority,
    )
    assert validated["status"] == "GREEN"
    assert cases["l8"]["externalMutationSuppressed"] is True


def test_s6_l8_final_admission(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, admission = _admission(tmp_path, 3)
    source = root / "source"
    installed = root / "installed"
    launcher = (source / "launcher.pyw").read_bytes()
    trusted = _trusted_receipts()
    context = _expected_context()
    installed_authority = _installed_authority(root, source, installed, launcher)
    before = _file_inventory(root)
    admitted = module.admit_final_e2e(admission, trusted, context, installed_authority)
    assert admitted["schemaVersion"] == "E2E_FINAL_ADMISSION_V1"
    assert admitted["status"] == "ADMITTED"
    assert admitted["attemptBudget"] == cases["l8"]["attemptBudget"]
    assert admitted["attemptsUsed"] == cases["l8"]["attemptsUsed"]
    assert admitted["independentBlockerCount"] == 0
    assert _file_inventory(root) == before

    for case in cases["l8AdmissionCases"]:
        negative = _mutate_admission(admission, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.admit_final_e2e(negative, trusted, context, installed_authority)
        assert _file_inventory(root) == before

    for case in cases["authorityCases"]:
        negative = deepcopy(admission)
        negative_trusted = deepcopy(trusted)
        negative_context = deepcopy(context)
        negative_authority = deepcopy(installed_authority)
        if case == "missing_trusted_receipt":
            negative_trusted.pop("S5")
        elif case == "unknown_trusted_receipt":
            negative_trusted["S9"] = deepcopy(next(iter(negative_trusted.values())))
        elif case == "trusted_receipt_source_drift":
            negative["acceptedReceipts"][0]["sourceSha256"] = "f" * 64
            negative["acceptedReceipts"][0]["receiptSha256"] = _sha(
                {key: item for key, item in negative["acceptedReceipts"][0].items() if key != "receiptSha256"}
            )
            _reseal_admission(negative)
        elif case == "trusted_receipt_test_drift":
            negative["acceptedReceipts"][1]["testSha256"] = "e" * 64
            negative["acceptedReceipts"][1]["receiptSha256"] = _sha(
                {key: item for key, item in negative["acceptedReceipts"][1].items() if key != "receiptSha256"}
            )
            _reseal_admission(negative)
        elif case == "context_date_drift":
            negative_context["issueDate"] = "2026-08-20"
        elif case == "context_generation_drift":
            negative_context["generation"] = 6
        elif case == "context_commit_drift":
            negative_context["sourceCommit"] = "b" * 40
        elif case == "installed_authority_source_drift":
            negative_authority["sourceSha256"] = "d" * 64
        elif case == "installed_authority_hash_drift":
            negative_authority["installedSha256"] = "c" * 64
        elif case == "installed_authority_task_action_drift":
            negative_authority["taskAction"] = {"unexpected": "drift"}
            negative_authority["taskActionSha256"] = _sha(negative_authority["taskAction"])
        elif case == "caller_registry_overwrite":
            negative["acceptedReceipts"][0]["receiptId"] = "NG-CLEANROOM-S9-OVERWRITE"
            negative["acceptedReceipts"][0]["receiptSha256"] = _sha(
                {key: item for key, item in negative["acceptedReceipts"][0].items() if key != "receiptSha256"}
            )
            _reseal_admission(negative)
        else:
            raise AssertionError(f"unknown authority case: {case}")
        with pytest.raises(module.ReleaseEvidenceError):
            module.admit_final_e2e(negative, negative_trusted, negative_context, negative_authority)
        assert _file_inventory(root) == before

    for case in cases["installedClosureCases"]:
        negative_authority = _mutate_installed_authority(installed_authority, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.admit_final_e2e(admission, trusted, context, negative_authority)
        assert _file_inventory(root) == before


def test_s6_history_coverage_and_legacy_zero(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 4)
    source = root / "source"
    installed = root / "installed"
    launcher = (source / "launcher.pyw").read_bytes()
    trusted = _trusted_receipts()
    context = _expected_context()
    installed_authority = _installed_authority(root, source, installed, launcher)
    validated = module.validate_natural_evidence(natural, trusted, context, installed_authority)
    history = validated["historyCoverage"]
    assert history["schemaVersion"] == "HISTORY_COVERAGE_V1"
    assert history["timezone"] == cases["timezone"]
    assert history["introductionDate"] == cases["history"]["introductionDate"]
    assert len(history["dates"]) == cases["history"]["expectedDays"] == 30
    assert history["missingDays"] == []
    assert history["corpusCounts"] == {"Scheduled": 63, "Audit": 8}
    assert len(history["dailyEvidence"]) == 30
    assert len(history["corpusEntries"]) == 71
    assert all(set(row["lineages"]) == set(cases["history"]["requiredLineages"]) for row in history["dailyEvidence"])
    assert all(row["ledgerProvenanceSha256"] == _sha({key: value for key, value in row.items() if key != "ledgerProvenanceSha256"}) for row in history["dailyEvidence"])
    assert all(set(row) == set(cases["history"]["corpusEntryKeys"]) for row in history["corpusEntries"])
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
        elif case == "daily_receipt_hash_drift":
            history_negative["dailyEvidence"][0]["lineages"]["Scheduled"]["receiptSha256"] = "0" * 64
        elif case == "daily_ledger_hash_drift":
            history_negative["dailyEvidence"][0]["ledgerProvenanceSha256"] = "1" * 64
        elif case == "corpus_provenance_hash_drift":
            history_negative["corpusEntries"][0]["ledgerProvenanceSha256"] = "2" * 64
        else:
            history_negative["unexpected"] = "drift"
        _reseal_natural(negative)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_natural_evidence(negative, trusted, context, installed_authority)


def test_s6_natural_receipt_schema(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 5)
    source = root / "source"
    installed = root / "installed"
    launcher = (source / "launcher.pyw").read_bytes()
    trusted = _trusted_receipts()
    context = _expected_context()
    installed_authority = _installed_authority(root, source, installed, launcher)
    validated = module.validate_natural_evidence(natural, trusted, context, installed_authority)
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
                module.validate_natural_evidence(negative, trusted, context, installed_authority)
        else:
            with pytest.raises(module.ReleaseEvidenceError):
                module.validate_natural_evidence(negative, trusted, context, installed_authority)

    for case in cases["installedClosureCases"]:
        negative_authority = _mutate_installed_authority(installed_authority, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_natural_evidence(natural, trusted, context, negative_authority)
        assert _file_inventory(root)


def test_s6_natural_states_non_substitutable(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_release")
    cases = _cases()
    root, natural = _natural_evidence(tmp_path, 6)
    source = root / "source"
    installed = root / "installed"
    launcher = (source / "launcher.pyw").read_bytes()
    trusted = _trusted_receipts()
    context = _expected_context()
    installed_authority = _installed_authority(root, source, installed, launcher)
    public_not_required = deepcopy(natural)
    public_not_required["receipts"]["Public"]["state"] = "NOT_REQUIRED"
    public_not_required["historyCoverage"] = _history_coverage(cases, public_not_required["receipts"])
    _reseal_natural(public_not_required)
    accepted = module.validate_natural_evidence(public_not_required, trusted, context, installed_authority)
    assert accepted["receipts"]["Public"]["state"] == "NOT_REQUIRED"
    assert accepted["receipts"]["Scheduled"]["state"] == "GREEN"
    assert accepted["receipts"]["Audit"]["state"] == "GREEN"

    for case in cases["nonSubstitutionCases"]:
        negative = _mutate_natural(natural, case)
        with pytest.raises(module.ReleaseEvidenceError):
            module.validate_natural_evidence(negative, trusted, context, installed_authority)
    assert _file_inventory(root)
