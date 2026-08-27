"""S6 のリリース境界を検証する副作用のない純粋バリデータ。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


class ReleaseEvidenceError(ValueError):
    """封印済みのリリース証拠が契約に適合しない場合に送出する。"""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        super().__init__(reason if detail is None else f"{reason}: {detail}")


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_LAYER_NAMES = [f"L{i}" for i in range(8)]
_TRUSTED_KEYS = [f"S{i}" for i in range(6)]
_NATURAL_LINEAGES = ["Scheduled", "Audit", "Public", "Readiness"]
_HISTORY_MISSING_LINEAGES = ["Scheduled", "Audit", "Public", "Readiness"]
_CLOSURE_PATHS = [
    "scripts/ops/news-grasp-runner.ps1",
    "scripts/ops/news-grasp-task-launcher.pyw",
    "scripts/ops/run_codex_with_timeout.ps1",
    "tools/audit_recovery_control.py",
    "tools/daily_self_heal.py",
    "tools/news_grasp_completion_guard.py",
    "tools/news_grasp_daily_control.py",
]
_BINDING_PATHS = [
    "bin/news-grasp-high-cost-binding-v1.json",
    "bin/news-grasp-recovery-runtime-binding-v1.json",
]
_TASK_NAMES = ["News-Grasp Bootstrap", "News-Grasp Production"]
_OWNER_SCHEMAS = {
    "runtime_generation_owner": [
        "PRODUCTION_GENERATION_MANIFEST_V2",
        "NEWS_GRASP_ACTIVE_GENERATION_V2",
    ],
    "ops_install_owner": [
        "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
        "NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1",
    ],
}
_EXPECTED_MANIFEST_ACTION = {
    "entryModule": "tools.news_grasp_cleanroom_dispatch",
    "argv": ["dispatch", "--schedule-id", "news-grasp-daily-v1", "--intent", "reconcile"],
    "workingDirectoryToken": "<RUNTIME_ROOT>",
}
_NATURAL_RECEIPT_KEYS = {
    "schemaVersion",
    "lineage",
    "issueDate",
    "generation",
    "state",
    "terminalHash",
    "receiptSha256",
}
_TRUSTED_RECEIPT_KEYS = {
    "receiptId",
    "schemaVersion",
    "status",
    "issueDate",
    "generation",
    "sourceCommit",
    "sourceSha256",
    "testSha256",
    "receiptBytes",
    "receiptBytesSha256",
    "receiptSha256",
}

_LIVE_TASK_SNAPSHOT_SCHEMA = "NEWS_GRASP_LIVE_TASK_SNAPSHOT_V1"
_CONTROL_MANIFEST_SCHEMA = "NEWS_GRASP_CONTROL_MANIFEST_V1"
_CLEANROOM_TASK_KEYS = {
    "taskPath",
    "taskName",
    "multipleInstancesPolicy",
    "triggers",
    "action",
}
_CLEANROOM_LIVE_TASK_KEYS = _CLEANROOM_TASK_KEYS | {"enabled"}
_CLEANROOM_ACTION_KEYS = {"entryModule", "argv", "workingDirectoryToken"}
_CLEANROOM_TRIGGER_KEYS = {"triggerId", "kind", "localTime", "timeZone"}


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _error(code: str, detail: str) -> None:
    raise ReleaseEvidenceError(code, detail)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        _error("TYPE", f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        _error("TYPE", f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], keys: Iterable[str], label: str) -> None:
    expected = set(keys)
    actual = set(value)
    if actual != expected:
        _error("SHAPE", f"{label} keys differ")


def _text(value: Any, label: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        _error("TYPE", f"{label} must be a non-empty string")
    return value


def _count(value: Any, label: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _error("COUNT", f"{label} must be a non-negative integer")
    return value


def _sha_hex(value: Any, label: str) -> str:
    value = _text(value, label)
    if _HEX64.fullmatch(value) is None or len(set(value)) == 1:
        _error("HASH", f"{label} is not a non-placeholder lowercase SHA-256")
    return value


def _commit(value: Any, label: str) -> str:
    value = _text(value, label)
    if _HEX40.fullmatch(value) is None:
        _error("COMMIT", f"{label} is not a lowercase commit hash")
    return value


def _timestamp(value: Any, label: str) -> str:
    value = _text(value, label)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        _error("TIMESTAMP", f"{label} is not ISO-8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _error("TIMESTAMP", f"{label} has no timezone")
    return value


def _hash_field(value: Mapping[str, Any], field: str, label: str) -> None:
    digest = _sha_hex(value.get(field), f"{label}.{field}")
    expected = _sha({key: item for key, item in value.items() if key != field})
    if digest != expected:
        _error("HASH_MISMATCH", f"{label}.{field}")


def validate_live_task_parity(manifest: Any, live_snapshot: Any) -> bool:
    """canonical clean-room Task定義とlive観測を厳密比較する。"""
    # boolや self-report は mapping として扱わず、必ず実観測形状へ戻す。
    manifest_value = _mapping(manifest, "manifest")
    snapshot_value = _mapping(live_snapshot, "live_snapshot")
    _exact_keys(manifest_value, {"schemaVersion", "scheduleId", "tasks"}, "manifest")
    if manifest_value.get("schemaVersion") != _CONTROL_MANIFEST_SCHEMA:
        _error("SCHEMA", "manifest.schemaVersion")
    if manifest_value.get("scheduleId") != "news-grasp-daily-v1":
        _error("IDENTITY", "manifest.scheduleId")
    manifest_tasks = _list(manifest_value.get("tasks"), "manifest.tasks")
    if len(manifest_tasks) != 1:
        _error("CARDINALITY", "manifest.tasks")
    manifest_task = _mapping(manifest_tasks[0], "manifest.tasks[0]")
    _exact_keys(manifest_task, _CLEANROOM_TASK_KEYS, "manifest.tasks[0]")
    if manifest_task.get("taskPath") != "\\" or manifest_task.get("taskName") != "News-Grasp Production":
        _error("IDENTITY", "manifest task")
    if manifest_task.get("multipleInstancesPolicy") != "IgnoreNew":
        _error("POLICY", "manifest multipleInstancesPolicy")
    action = _mapping(manifest_task.get("action"), "manifest.tasks[0].action")
    _exact_keys(action, _CLEANROOM_ACTION_KEYS, "manifest action")
    if action != _EXPECTED_MANIFEST_ACTION:
        _error("ACTION", "manifest action")
    triggers = _list(manifest_task.get("triggers"), "manifest task triggers")
    expected_triggers = [
        {"triggerId": "scheduled-0600", "kind": "daily", "localTime": "06:00:00", "timeZone": "Asia/Tokyo"},
    ]
    if triggers != expected_triggers:
        _error("TRIGGERS", "manifest task triggers")
    for index, trigger in enumerate(triggers):
        item = _mapping(trigger, f"manifest task triggers[{index}]")
        _exact_keys(item, _CLEANROOM_TRIGGER_KEYS, f"manifest task triggers[{index}]")

    _exact_keys(
        snapshot_value,
        {"schemaVersion", "tasks", "extraEnabledTasks"},
        "live_snapshot",
    )
    if snapshot_value.get("schemaVersion") != _LIVE_TASK_SNAPSHOT_SCHEMA:
        _error("SCHEMA", "live_snapshot.schemaVersion")
    live_tasks = _list(snapshot_value.get("tasks"), "live_snapshot.tasks")
    if len(live_tasks) != 1:
        _error("CARDINALITY", "live_snapshot.tasks")
    extras = _list(snapshot_value.get("extraEnabledTasks"), "live_snapshot.extraEnabledTasks")
    if extras:
        _error("EXTRA_ENABLED_TASK", "live_snapshot.extraEnabledTasks")
    live_task = _mapping(live_tasks[0], "live_snapshot.tasks[0]")
    _exact_keys(live_task, _CLEANROOM_LIVE_TASK_KEYS, "live_snapshot.tasks[0]")
    if type(live_task.get("enabled")) is not bool or live_task.get("enabled") is not True:
        _error("STATE", "live task enabled")
    for key in _CLEANROOM_TASK_KEYS:
        if live_task.get(key) != manifest_task.get(key):
            _error("PARITY", f"live task {key}")
    return True


def _within(root: Path, relative: str, label: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative or relative.startswith("/"):
        _error("PATH", f"{label} is not a portable relative path")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _error("PATH", f"{label} contains a path escape")
    resolved_root = root.resolve()
    target = (root / Path(*parts)).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        _error("PATH", f"{label} escapes installedRoot")
    return target


def _validate_context(context: Any) -> dict[str, Any]:
    value = _mapping(context, "expected_context")
    _exact_keys(value, {"schemaVersion", "issueDate", "generation", "scheduleId", "sourceCommit", "observedAt"}, "expected_context")
    if value["schemaVersion"] != "S6_EXPECTED_CONTEXT_V1":
        _error("SCHEMA", "expected_context")
    issue_date = _text(value["issueDate"], "expected_context.issueDate")
    try:
        date.fromisoformat(issue_date)
    except ValueError:
        _error("DATE", "expected_context.issueDate")
    _count(value["generation"], "expected_context.generation")
    _text(value["scheduleId"], "expected_context.scheduleId")
    _commit(value["sourceCommit"], "expected_context.sourceCommit")
    _timestamp(value["observedAt"], "expected_context.observedAt")
    return value


def _validate_trusted_receipts(trusted: Any, context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    value = _mapping(trusted, "trusted_receipts")
    if list(value) != _TRUSTED_KEYS:
        _error("SHAPE", "trusted_receipts order or keys")
    result: dict[str, dict[str, Any]] = {}
    for index, key in enumerate(_TRUSTED_KEYS):
        receipt = _mapping(value[key], f"trusted_receipts.{key}")
        _exact_keys(receipt, _TRUSTED_RECEIPT_KEYS, f"trusted_receipts.{key}")
        if receipt["receiptId"] != f"NG-CLEANROOM-S{index}-GREEN-V1":
            _error("IDENTITY", f"trusted_receipts.{key}.receiptId")
        if receipt["schemaVersion"] != f"NEWS_GRASP_CLEANROOM_S{index}_ACCEPTED_RECEIPT_V1":
            _error("SCHEMA", f"trusted_receipts.{key}.schemaVersion")
        if receipt["status"] != "ACCEPTED":
            _error("STATE", f"trusted_receipts.{key}.status")
        if receipt["issueDate"] != context["issueDate"] or receipt["generation"] != context["generation"]:
            _error("CONTEXT", f"trusted_receipts.{key}")
        if receipt["sourceCommit"] != context["sourceCommit"]:
            _error("CONTEXT", f"trusted_receipts.{key}.sourceCommit")
        _sha_hex(receipt["sourceSha256"], f"trusted_receipts.{key}.sourceSha256")
        _sha_hex(receipt["testSha256"], f"trusted_receipts.{key}.testSha256")
        raw = _text(receipt["receiptBytes"], f"trusted_receipts.{key}.receiptBytes", nonempty=False)
        try:
            raw_bytes = raw.encode("latin-1")
        except UnicodeEncodeError:
            _error("BYTES", f"trusted_receipts.{key}.receiptBytes")
        if hashlib.sha256(raw_bytes).hexdigest() != receipt["receiptBytesSha256"]:
            _error("HASH_MISMATCH", f"trusted_receipts.{key}.receiptBytesSha256")
        _hash_field(receipt, "receiptSha256", f"trusted_receipts.{key}")
        result[key] = deepcopy(receipt)
    return result


def _validate_installed_authority(authority: Any, context: Mapping[str, Any]) -> dict[str, Any]:
    """検証済み installed authority を detached copy として返す。"""
    value = _mapping(authority, "installed_authority")
    required = {
        "schemaVersion", "sourcePath", "sourceSha256", "sourceCommit", "installedRoot", "installedPath",
        "installedSha256", "generation", "generationReceipt", "taskXml", "taskXmlSha256", "taskAction",
        "taskActionSha256", "installedClosure", "bindings", "taskActions", "ownerReceipts", "loadedFreshness",
        "loadedGeneration", "executable", "argv", "workingDirectory", "generationReceiptSha256",
        "installedClosureSha256", "bindingsSha256", "taskActionsSha256", "ownerReceiptsSha256",
    }
    optional = {"authoritySha256"}
    if set(value) - (required | optional) or not required.issubset(value):
        _error("SHAPE", "installed_authority keys")
    if value["schemaVersion"] != "INSTALLED_AUTHORITY_V1":
        _error("SCHEMA", "installed_authority.schemaVersion")
    source_path = Path(_text(value["sourcePath"], "installed_authority.sourcePath"))
    installed_root = Path(_text(value["installedRoot"], "installed_authority.installedRoot"))
    installed_path = Path(_text(value["installedPath"], "installed_authority.installedPath"))
    if not source_path.is_file() or not installed_path.is_file():
        _error("FILESYSTEM", "source or installed launcher missing")
    source_bytes = source_path.read_bytes()
    installed_bytes = installed_path.read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    installed_digest = hashlib.sha256(installed_bytes).hexdigest()
    if value["sourceSha256"] != source_digest or value["installedSha256"] != installed_digest:
        _error("HASH_MISMATCH", "launcher bytes")
    _sha_hex(value["sourceSha256"], "installed_authority.sourceSha256")
    _sha_hex(value["installedSha256"], "installed_authority.installedSha256")
    if source_digest != installed_digest or value["sourceSha256"] != value["installedSha256"]:
        _error("AUTHORITY", "source and installed launcher differ")
    if value["sourceCommit"] != context["sourceCommit"]:
        _error("CONTEXT", "installed_authority.sourceCommit")
    if value["generation"] != context["generation"] or value["loadedGeneration"] != value["generation"]:
        _error("CONTEXT", "installed_authority generation")
    if value["loadedFreshness"] != "fresh":
        _error("FRESHNESS", "installed_authority.loadedFreshness")
    _validate_generation_receipt(value, source_digest)
    task = _validate_task_authority(value, installed_root)
    _validate_closure(value, installed_root)
    _validate_bindings(value, installed_root)
    _validate_task_actions(value, installed_root, task)
    _validate_owner_receipts(value, context)
    if "authoritySha256" in value:
        _hash_field(value, "authoritySha256", "installed_authority")
    return deepcopy(value)


def _validate_generation_receipt(value: Mapping[str, Any], source_digest: str) -> None:
    receipt = _mapping(value["generationReceipt"], "generationReceipt")
    _exact_keys(receipt, {"schemaVersion", "generation", "sourceSha256"}, "generationReceipt")
    if receipt["schemaVersion"] != "INSTALL_GENERATION_RECEIPT_V1" or receipt["generation"] != value["generation"]:
        _error("GENERATION", "generationReceipt")
    if receipt["sourceSha256"] != source_digest:
        _error("HASH_MISMATCH", "generationReceipt.sourceSha256")
    if value["generationReceiptSha256"] != _sha(receipt):
        _error("HASH_MISMATCH", "generationReceiptSha256")


def _validate_task_authority(value: Mapping[str, Any], installed_root: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(value["taskXml"])
    except (TypeError, json.JSONDecodeError):
        _error("TASK", "taskXml is not JSON")
    if type(parsed) is not dict:
        _error("TASK", "taskXml object")
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if canonical != value["taskXml"]:
        _error("TASK", "taskXml is not canonical")
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != value["taskXmlSha256"]:
        _error("HASH_MISMATCH", "taskXmlSha256")
    _sha_hex(value["taskXmlSha256"], "taskXmlSha256")
    _exact_keys(parsed, {"taskPath", "taskName", "enabled", "executable", "arguments", "workingDirectory", "triggers", "multipleInstancesPolicy"}, "taskXml")
    task_action = _mapping(value["taskAction"], "taskAction")
    _exact_keys(task_action, set(parsed), "taskAction")
    if task_action != parsed:
        _error("TASK", "taskAction differs from taskXml")
    if parsed["taskPath"] != "\\" or parsed["taskName"] != "News-Grasp Production" or parsed["enabled"] is not False:
        _error("TASK", "production task identity")
    if parsed["executable"] != value["executable"] or parsed["workingDirectory"] != value["workingDirectory"]:
        _error("TASK", "production task path binding")
    if not isinstance(parsed["arguments"], list) or parsed["arguments"] != ["dispatch", "--schedule-id", "news-grasp-daily-v1", "--intent", "reconcile"]:
        _error("TASK", "production argv")
    if parsed["workingDirectory"] != str(installed_root.parent):
        _error("TASK", "production workingDirectory")
    if parsed["executable"] != str(installed_root.parent / "pythonw.exe"):
        _error("TASK", "production executable")
    if parsed["multipleInstancesPolicy"] != "IgnoreNew":
        _error("TASK", "multipleInstancesPolicy")
    triggers = _list(parsed["triggers"], "taskXml.triggers")
    expected_triggers = [
        {"triggerId": "scheduled-0600", "kind": "daily", "localTime": "06:00:00", "timeZone": "Asia/Tokyo"},
    ]
    if triggers != expected_triggers:
        _error("TASK", "production triggers")
    if value["argv"] != parsed["arguments"] or value["workingDirectory"] != parsed["workingDirectory"] or value["executable"] != parsed["executable"]:
        _error("TASK", "top-level task action")
    _text(value["executable"], "installed_authority.executable")
    _text(value["workingDirectory"], "installed_authority.workingDirectory")
    _list(value["argv"], "installed_authority.argv")
    _sha_hex(value["taskActionSha256"], "installed_authority.taskActionSha256")
    if value["taskActionSha256"] != _sha(_EXPECTED_MANIFEST_ACTION):
        _error("TASK", "taskActionSha256 does not bind manifest action")
    return parsed


def _validate_closure(value: Mapping[str, Any], installed_root: Path) -> None:
    rows = _list(value["installedClosure"], "installedClosure")
    if [row.get("path") for row in rows if type(row) is dict] != _CLOSURE_PATHS:
        _error("CLOSURE", "installedClosure paths/order")
    for index, relative in enumerate(_CLOSURE_PATHS):
        row = _mapping(rows[index], f"installedClosure[{index}]")
        _exact_keys(row, {"path", "sha256", "bytes"}, f"installedClosure[{index}]")
        if row["path"] != relative:
            _error("CLOSURE", f"installedClosure[{index}].path")
        target = _within(installed_root, relative, f"installedClosure[{index}].path")
        if not target.is_file():
            _error("FILESYSTEM", f"installedClosure[{index}] missing")
        content = target.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        _sha_hex(row["sha256"], f"installedClosure[{index}].sha256")
        if row["sha256"] != actual_hash or row["bytes"] != len(content):
            _error("CLOSURE", f"installedClosure[{index}] bytes/hash")
        _count(row["bytes"], f"installedClosure[{index}].bytes")
    _sha_hex(value["installedClosureSha256"], "installedClosureSha256")
    if value["installedClosureSha256"] != _sha(rows):
        _error("HASH_MISMATCH", "installedClosureSha256")


def _validate_bindings(value: Mapping[str, Any], installed_root: Path) -> None:
    rows = _list(value["bindings"], "bindings")
    if [row.get("relativePath") for row in rows if type(row) is dict] != _BINDING_PATHS:
        _error("BINDINGS", "bindings paths/order")
    for index, relative in enumerate(_BINDING_PATHS):
        row = _mapping(rows[index], f"bindings[{index}]")
        _exact_keys(row, {"schemaVersion", "relativePath", "sha256"}, f"bindings[{index}]")
        if row["relativePath"] != relative or row["schemaVersion"] != "NEWS_GRASP_RUNTIME_BINDING_V1":
            _error("BINDINGS", f"bindings[{index}] identity")
        target = _within(installed_root, relative, f"bindings[{index}].relativePath")
        if not target.is_file():
            _error("FILESYSTEM", f"bindings[{index}] missing")
        content = target.read_bytes()
        _sha_hex(row["sha256"], f"bindings[{index}].sha256")
        if row["sha256"] != hashlib.sha256(content).hexdigest():
            _error("BINDINGS", f"bindings[{index}] hash")
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _error("BINDINGS", f"bindings[{index}] invalid JSON")
        parsed = _mapping(parsed, f"bindings[{index}] file")
        _exact_keys(parsed, {"schemaVersion", "relativePath", "bindingId", "source", "bindingSha256"}, f"bindings[{index}] file")
        if parsed["schemaVersion"] != "NEWS_GRASP_RUNTIME_BINDING_V1" or parsed["relativePath"] != relative:
            _error("BINDINGS", f"bindings[{index}] file identity")
        _text(parsed["bindingId"], f"bindings[{index}].bindingId")
        _text(parsed["source"], f"bindings[{index}].source")
        _hash_field(parsed, "bindingSha256", f"bindings[{index}] file")
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if content.decode("utf-8") != canonical:
            _error("BINDINGS", f"bindings[{index}] file canonical bytes")
    _sha_hex(value["bindingsSha256"], "bindingsSha256")
    if value["bindingsSha256"] != _sha(rows):
        _error("HASH_MISMATCH", "bindingsSha256")


def _validate_task_actions(value: Mapping[str, Any], installed_root: Path, production_task: Mapping[str, Any]) -> None:
    rows = _list(value["taskActions"], "taskActions")
    if [row.get("taskName") for row in rows if type(row) is dict] != _TASK_NAMES:
        _error("TASK", "taskActions names/order")
    expected: list[dict[str, Any]] = []
    root = installed_root.parent
    production_action = {
        "taskName": "News-Grasp Production",
        "executable": str(root / "pythonw.exe"),
        "arguments": list(production_task["arguments"]),
        "workingDirectory": str(root),
    }
    bootstrap_action = {
        "taskName": "News-Grasp Bootstrap",
        "executable": str(root / "pythonw.exe"),
        "arguments": [
            str(installed_root / "scripts/ops/news-grasp-task-launcher.pyw"),
            "bootstrap",
            "--scheduled-task-name",
            "News-Grasp Bootstrap",
            "--high-cost-binding-path",
            str(installed_root / "bin/news-grasp-high-cost-binding-v1.json"),
        ],
        "workingDirectory": str(root),
    }
    for name, action in zip(_TASK_NAMES, [bootstrap_action, production_action]):
        expected.append({"taskName": name, "action": action, "actionSha256": _sha(action)})
    if rows != expected:
        _error("TASK", "taskActions semantics")
    for index, row in enumerate(rows):
        _exact_keys(row, {"taskName", "action", "actionSha256"}, f"taskActions[{index}]")
        _sha_hex(row["actionSha256"], f"taskActions[{index}].actionSha256")
        action = _mapping(row["action"], f"taskActions[{index}].action")
        _exact_keys(action, {"taskName", "executable", "arguments", "workingDirectory"}, f"taskActions[{index}].action")
        if action["taskName"] != row["taskName"]:
            _error("TASK", f"taskActions[{index}].action.taskName")
        _list(action["arguments"], f"taskActions[{index}].arguments")
    _sha_hex(value["taskActionsSha256"], "taskActionsSha256")
    if value["taskActionsSha256"] != _sha(rows):
        _error("HASH_MISMATCH", "taskActionsSha256")


def _validate_owner_receipts(value: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    rows = _mapping(value["ownerReceipts"], "ownerReceipts")
    if list(rows) != list(_OWNER_SCHEMAS):
        _error("OWNER", "owner receipt ids/order")
    for owner_id, schemas in _OWNER_SCHEMAS.items():
        receipt = _mapping(rows[owner_id], f"ownerReceipts.{owner_id}")
        _exact_keys(receipt, {"ownerId", "receiptSchemas", "sourceSha256", "installedSha256", "taskActionSha256", "preimageSha256", "receiptSha256"}, f"ownerReceipts.{owner_id}")
        if receipt["ownerId"] != owner_id or receipt["receiptSchemas"] != schemas:
            _error("OWNER", f"ownerReceipts.{owner_id} identity")
        if receipt["sourceSha256"] != value["sourceSha256"] or receipt["installedSha256"] != value["installedSha256"] or receipt["taskActionSha256"] != value["taskActionSha256"]:
            _error("OWNER", f"ownerReceipts.{owner_id} authority binding")
        _sha_hex(receipt["sourceSha256"], f"ownerReceipts.{owner_id}.sourceSha256")
        _sha_hex(receipt["installedSha256"], f"ownerReceipts.{owner_id}.installedSha256")
        _sha_hex(receipt["taskActionSha256"], f"ownerReceipts.{owner_id}.taskActionSha256")
        _sha_hex(receipt["preimageSha256"], f"ownerReceipts.{owner_id}.preimageSha256")
        expected_preimage = _sha({"ownerId": owner_id, "preimage": "s6-test-preimage-v1"})
        if receipt["preimageSha256"] != expected_preimage:
            _error("OWNER", f"ownerReceipts.{owner_id} preimage")
        _hash_field(receipt, "receiptSha256", f"ownerReceipts.{owner_id}")
    _sha_hex(value["ownerReceiptsSha256"], "ownerReceiptsSha256")
    if value["ownerReceiptsSha256"] != _sha(rows):
        _error("HASH_MISMATCH", "ownerReceiptsSha256")


def _validate_layer_rows(evidence: Any, sealed_manifest: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = _list(evidence, "layer evidence")
    if len(rows) != 8 or [row.get("layer") for row in rows if type(row) is dict] != _LAYER_NAMES:
        _error("LAYERS", "layer order or count")
    result: list[dict[str, Any]] = []
    for index, row_value in enumerate(rows):
        row = _mapping(row_value, f"layerEvidence[{index}]")
        _exact_keys(row, {"layer", "realRequired", "realObserved", "fakeUsed", "schemaIdentity", "status", "evidenceSha256"}, f"layerEvidence[{index}]")
        if row["layer"] != _LAYER_NAMES[index] or row["status"] != "GREEN":
            _error("LAYERS", f"layerEvidence[{index}] identity/status")
        required = _list(row["realRequired"], f"layerEvidence[{index}].realRequired")
        observed = _list(row["realObserved"], f"layerEvidence[{index}].realObserved")
        fake = _list(row["fakeUsed"], f"layerEvidence[{index}].fakeUsed")
        if len(observed) < len(required):
            _error("LAYERS", f"layerEvidence[{index}] incomplete real boundary")
        if not required or not observed:
            _error("LAYERS", f"layerEvidence[{index}] empty boundary")
        for item in required + observed + fake:
            _text(item, f"layerEvidence[{index}] boundary")
        if any("unbound" in item.lower() for item in observed):
            _error("LAYERS", f"layerEvidence[{index}] contains an unbound observation")
        _text(row["schemaIdentity"], f"layerEvidence[{index}].schemaIdentity")
        _sha_hex(row["evidenceSha256"], f"layerEvidence[{index}].evidenceSha256")
        if row["evidenceSha256"] != _sha({key: item for key, item in row.items() if key != "evidenceSha256"}):
            _error("HASH_MISMATCH", f"layerEvidence[{index}].evidenceSha256")
        if sealed_manifest is not None:
            manifest = _mapping(sealed_manifest[index], f"sealed_manifest[{index}]")
            _exact_keys(manifest, {"layer", "fakeSubstitution", "realRequired", "plannedEntrypoints", "schemaIdentity", "parityNode"}, f"sealed_manifest[{index}]")
            if manifest["layer"] != row["layer"] or manifest["realRequired"] != required or manifest["fakeSubstitution"] != fake or manifest["schemaIdentity"] != row["schemaIdentity"]:
                _error("LAYERS", f"layerEvidence[{index}] differs from sealed manifest")
            _list(manifest["plannedEntrypoints"], f"sealed_manifest[{index}].plannedEntrypoints")
            _text(manifest["parityNode"], f"sealed_manifest[{index}].parityNode")
        result.append(deepcopy(row))
    return result


def validate_layer_evidence(
    evidence: Any,
    sealed_manifest: Any,
    trusted_receipts: Any,
    expected_context: Any,
    installed_authority: Any,
) -> dict[str, Any]:
    """L0-L7 の実観測証拠、受理レシート、installed authorityを検証する。"""
    context = _validate_context(expected_context)
    _validate_trusted_receipts(trusted_receipts, context)
    _validate_installed_authority(installed_authority, context)
    manifest = _list(sealed_manifest, "sealed_manifest")
    if len(manifest) != 8:
        _error("LAYERS", "sealed_manifest must contain L0-L7")
    rows = _validate_layer_rows(evidence, [dict(row) if type(row) is dict else row for row in manifest])
    return {
        "schemaVersion": "LAYER_EVIDENCE_V1",
        "status": "GREEN",
        "layers": [row["layer"] for row in rows],
    }


def _validate_admission_layer_evidence(rows: Any) -> list[dict[str, Any]]:
    return _validate_layer_rows(rows, None)


def admit_final_e2e(
    admission: Any,
    trusted_receipts: Any,
    expected_context: Any,
    installed_authority: Any,
) -> dict[str, Any]:
    """final-only NoPublish E2E の開始権限を副作用なしで判定する。"""
    context = _validate_context(expected_context)
    trusted = _validate_trusted_receipts(trusted_receipts, context)
    authority = _validate_installed_authority(installed_authority, context)
    value = _mapping(admission, "admission")
    _exact_keys(
        value,
        {
            "schemaVersion", "admissionId", "issueDate", "generation", "acceptedReceipts", "layerEvidence",
            "layerEvidenceSha256", "installedSourceSha256", "installedHashSha256", "installedAuthoritySha256",
            "externalMutationSuppressed", "externalMutationCount", "attemptBudget", "attemptsUsed",
            "independentBlockerCount", "mode", "admissionSha256",
        },
        "admission",
    )
    if value["schemaVersion"] != "E2E_FINAL_ADMISSION_V1" or value["issueDate"] != context["issueDate"] or value["generation"] != context["generation"]:
        _error("CONTEXT", "admission")
    _text(value["admissionId"], "admission.admissionId")
    accepted = _list(value["acceptedReceipts"], "admission.acceptedReceipts")
    if len(accepted) != len(_TRUSTED_KEYS):
        _error("RECEIPTS", "admission accepted receipt count")
    for index, receipt in enumerate(accepted):
        _mapping(receipt, f"admission.acceptedReceipts[{index}]")
        if receipt != trusted[_TRUSTED_KEYS[index]]:
            _error("RECEIPTS", f"admission.acceptedReceipts[{index}] does not match trusted registry")
    layers = _validate_admission_layer_evidence(value["layerEvidence"])
    _sha_hex(value["layerEvidenceSha256"], "admission.layerEvidenceSha256")
    if value["layerEvidenceSha256"] != _sha(layers):
        _error("HASH_MISMATCH", "admission.layerEvidenceSha256")
    if value["installedSourceSha256"] != authority["sourceSha256"] or value["installedHashSha256"] != authority["installedSha256"]:
        _error("AUTHORITY", "admission installed hash binding")
    _sha_hex(value["installedSourceSha256"], "admission.installedSourceSha256")
    _sha_hex(value["installedHashSha256"], "admission.installedHashSha256")
    _sha_hex(value["installedAuthoritySha256"], "admission.installedAuthoritySha256")
    if value["installedAuthoritySha256"] != _sha(authority):
        _error("HASH_MISMATCH", "admission.installedAuthoritySha256")
    if value["externalMutationSuppressed"] is not True or value["externalMutationCount"] != 0:
        _error("MUTATION", "admission external mutation")
    if value["attemptBudget"] != 1 or value["attemptsUsed"] != 0 or value["independentBlockerCount"] != 0:
        _error("BUDGET", "admission attempt/blocker budget")
    _count(value["externalMutationCount"], "admission.externalMutationCount")
    _count(value["attemptBudget"], "admission.attemptBudget")
    _count(value["attemptsUsed"], "admission.attemptsUsed")
    _count(value["independentBlockerCount"], "admission.independentBlockerCount")
    if value["mode"] != "final_confirmation_only":
        _error("MODE", "admission.mode")
    _hash_field(value, "admissionSha256", "admission")
    result = deepcopy(value)
    result["status"] = "ADMITTED"
    return result


def _validate_natural_receipts(receipts: Any, context: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    value = _mapping(receipts, "natural.receipts")
    if list(value) != _NATURAL_LINEAGES:
        _error("RECEIPTS", "natural receipt lineages/order")
    result: dict[str, dict[str, Any]] = {}
    for lineage in _NATURAL_LINEAGES:
        receipt = _mapping(value[lineage], f"natural.receipts.{lineage}")
        _exact_keys(receipt, _NATURAL_RECEIPT_KEYS, f"natural.receipts.{lineage}")
        if receipt["schemaVersion"] != "NATURAL_OPERATION_RECEIPT_V1" or receipt["lineage"] != lineage:
            _error("RECEIPTS", f"natural.receipts.{lineage} identity")
        if receipt["issueDate"] != context["issueDate"] or receipt["generation"] != context["generation"]:
            _error("CONTEXT", f"natural.receipts.{lineage}")
        allowed = {"GREEN"} if lineage != "Public" else {"GREEN", "NOT_REQUIRED"}
        if receipt["state"] not in allowed:
            _error("STATE", f"natural.receipts.{lineage}.state")
        _sha_hex(receipt["terminalHash"], f"natural.receipts.{lineage}.terminalHash")
        expected_terminal = _sha({
            "lineage": lineage,
            "issueDate": receipt["issueDate"],
            "generation": receipt["generation"],
            "state": receipt["state"],
        })
        if receipt["terminalHash"] != expected_terminal:
            _error("HASH_MISMATCH", f"natural.receipts.{lineage}.terminalHash")
        _hash_field(receipt, "receiptSha256", f"natural.receipts.{lineage}")
        result[lineage] = deepcopy(receipt)
    if result["Public"]["state"] == "NOT_REQUIRED" and result["Scheduled"]["state"] != "GREEN":
        _error("STATE", "Public NOT_REQUIRED requires Scheduled GREEN")
    return result


def _dated_receipt_sha(receipt: Mapping[str, Any], issue_date: str) -> str:
    dated = deepcopy(dict(receipt))
    dated["issueDate"] = issue_date
    dated["terminalHash"] = _sha({
        "lineage": dated["lineage"],
        "issueDate": dated["issueDate"],
        "generation": dated["generation"],
        "state": dated["state"],
    })
    dated["receiptSha256"] = _sha({key: item for key, item in dated.items() if key != "receiptSha256"})
    return dated["receiptSha256"]


def _expected_dates(introduction: str, issue_date: str) -> list[str]:
    try:
        start = date.fromisoformat(introduction)
        end = date.fromisoformat(issue_date)
    except ValueError:
        _error("DATE", "history date")
    if end < start:
        _error("DATE", "history issue date precedes introduction")
    return [(start + timedelta(days=index)).isoformat() for index in range((end - start).days + 1)]


def _validate_history(history: Any, receipts: Mapping[str, Mapping[str, Any]], context: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(history, "natural.historyCoverage")
    _exact_keys(value, {"schemaVersion", "timezone", "introductionDate", "dates", "dailyEvidence", "missingDays", "corpusEntries", "corpusCounts", "legacyWriterCount"}, "natural.historyCoverage")
    if value["schemaVersion"] != "HISTORY_COVERAGE_V1" or value["timezone"] != "Asia/Tokyo" or value["introductionDate"] != "2026-07-23":
        _error("HISTORY", "history identity")
    expected_dates = _expected_dates(value["introductionDate"], context["issueDate"])
    dates = _list(value["dates"], "history.dates")
    if dates != expected_dates or len(dates) != 30 or len(set(dates)) != len(dates):
        _error("HISTORY", "history dates")
    for index, item in enumerate(dates):
        _text(item, f"history.dates[{index}]")
    if value["missingDays"] != []:
        _error("HISTORY", "missingDays is not empty")
    _count(value["legacyWriterCount"], "history.legacyWriterCount")
    if value["legacyWriterCount"] != 0:
        _error("HISTORY", "legacyWriterCount")
    daily = _list(value["dailyEvidence"], "history.dailyEvidence")
    if len(daily) != len(expected_dates):
        _error("HISTORY", "daily evidence count")
    for index, row_value in enumerate(daily):
        row = _mapping(row_value, f"history.dailyEvidence[{index}]")
        _exact_keys(row, {"issueDate", "timezone", "introductionDate", "missing", "lineages", "ledgerProvenanceSha256"}, f"history.dailyEvidence[{index}]")
        issue = expected_dates[index]
        if row["issueDate"] != issue or row["timezone"] != "Asia/Tokyo" or row["introductionDate"] != "2026-07-23":
            _error("HISTORY", f"dailyEvidence[{index}] identity")
        missing = _mapping(row["missing"], f"dailyEvidence[{index}].missing")
        if list(missing) != _HISTORY_MISSING_LINEAGES or any(missing[key] != [] for key in _HISTORY_MISSING_LINEAGES):
            _error("HISTORY", f"dailyEvidence[{index}].missing")
        lineages = _mapping(row["lineages"], f"dailyEvidence[{index}].lineages")
        if list(lineages) != _NATURAL_LINEAGES:
            _error("HISTORY", f"dailyEvidence[{index}].lineages")
        for lineage in _NATURAL_LINEAGES:
            item = _mapping(lineages[lineage], f"dailyEvidence[{index}].lineages.{lineage}")
            _exact_keys(item, {"receiptSha256", "ledgerProvenanceSha256"}, f"dailyEvidence[{index}].lineages.{lineage}")
            expected_receipt = _dated_receipt_sha(receipts[lineage], issue)
            if item["receiptSha256"] != expected_receipt:
                _error("HISTORY", f"dailyEvidence[{index}].lineages.{lineage}.receiptSha256")
            _sha_hex(item["receiptSha256"], f"dailyEvidence[{index}].lineages.{lineage}.receiptSha256")
            expected_ledger = _sha({"issueDate": issue, "lineage": lineage, "receiptSha256": expected_receipt})
            if item["ledgerProvenanceSha256"] != expected_ledger:
                _error("HISTORY", f"dailyEvidence[{index}].lineages.{lineage}.ledgerProvenanceSha256")
            _sha_hex(item["ledgerProvenanceSha256"], f"dailyEvidence[{index}].lineages.{lineage}.ledgerProvenanceSha256")
        _hash_field(row, "ledgerProvenanceSha256", f"history.dailyEvidence[{index}]")
    corpus = _list(value["corpusEntries"], "history.corpusEntries")
    if len(corpus) != 71:
        _error("HISTORY", "corpus entry count")
    counts = _mapping(value["corpusCounts"], "history.corpusCounts")
    _exact_keys(counts, {"Scheduled", "Audit"}, "history.corpusCounts")
    if counts != {"Scheduled": 63, "Audit": 8}:
        _error("HISTORY", "corpus counts")
    seen: set[str] = set()
    for index, row_value in enumerate(corpus):
        row = _mapping(row_value, f"history.corpusEntries[{index}]")
        _exact_keys(row, {"caseId", "domain", "issueDate", "receiptSha256", "ledgerProvenanceSha256"}, f"history.corpusEntries[{index}]")
        domain = "Scheduled" if index < 63 else "Audit"
        domain_index = index if index < 63 else index - 63
        case_id = f"{domain.lower()}-{domain_index:03d}"
        if row["caseId"] != case_id or row["domain"] != domain or row["issueDate"] != expected_dates[domain_index % len(expected_dates)]:
            _error("HISTORY", f"corpusEntries[{index}] identity")
        if row["caseId"] in seen:
            _error("HISTORY", "duplicate corpus caseId")
        seen.add(row["caseId"])
        expected_receipt = receipts[domain]["receiptSha256"]
        if row["receiptSha256"] != expected_receipt:
            _error("HISTORY", f"corpusEntries[{index}].receiptSha256")
        expected_ledger = _sha({"domain": domain, "index": domain_index, "receiptSha256": expected_receipt})
        if row["ledgerProvenanceSha256"] != expected_ledger:
            _error("HISTORY", f"corpusEntries[{index}].ledgerProvenanceSha256")
        _sha_hex(row["receiptSha256"], f"corpusEntries[{index}].receiptSha256")
        _sha_hex(row["ledgerProvenanceSha256"], f"corpusEntries[{index}].ledgerProvenanceSha256")
    return deepcopy(value)


def validate_natural_evidence(
    evidence: Any,
    trusted_receipts: Any,
    expected_context: Any,
    installed_authority: Any,
) -> dict[str, Any]:
    """自然運用の Scheduled/Audit/Public/Readiness の継続証拠を検証する。"""
    context = _validate_context(expected_context)
    _validate_trusted_receipts(trusted_receipts, context)
    authority = _validate_installed_authority(installed_authority, context)
    value = _mapping(evidence, "natural evidence")
    _exact_keys(value, {"schemaVersion", "issueDate", "generation", "installed", "receipts", "historyCoverage", "naturalEvidenceSha256"}, "natural evidence")
    if value["schemaVersion"] != "NATURAL_OPERATION_EVIDENCE_V1" or value["issueDate"] != context["issueDate"] or value["generation"] != context["generation"]:
        _error("CONTEXT", "natural evidence")
    installed = _mapping(value["installed"], "natural.installed")
    _exact_keys(installed, {"commit", "sourceSha256", "installedSha256", "freshness", "observedAt"}, "natural.installed")
    if installed["commit"] != context["sourceCommit"] or installed["sourceSha256"] != authority["sourceSha256"] or installed["installedSha256"] != authority["installedSha256"]:
        _error("AUTHORITY", "natural installed binding")
    _commit(installed["commit"], "natural.installed.commit")
    _sha_hex(installed["sourceSha256"], "natural.installed.sourceSha256")
    _sha_hex(installed["installedSha256"], "natural.installed.installedSha256")
    if installed["freshness"] != "fresh":
        _error("FRESHNESS", "natural.installed.freshness")
    _timestamp(installed["observedAt"], "natural.installed.observedAt")
    receipts = _validate_natural_receipts(value["receipts"], context)
    history = _validate_history(value["historyCoverage"], receipts, context)
    _hash_field(value, "naturalEvidenceSha256", "natural evidence")
    result = deepcopy(value)
    # Keep the detached returned representation tied to validated runtime values.
    result["installed"] = deepcopy(installed)
    result["receipts"] = deepcopy(receipts)
    result["historyCoverage"] = deepcopy(history)
    _ = authority
    return result
