"""News-Grasp の install/cutover を隔離して扱う clean-room control plane。

このモジュールは実際の Windows Task Scheduler や共有プロセスへ直接触れず、
task/process/security/owner adapter という狭い protocol の上で、耐久 journal と
fail-closed な preflight を提供する。production adapter は上位の運用層が注入する。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


CREATE_NO_WINDOW = 0x08000000
_SCHEMA = "INSTALL_CUTOVER_JOURNAL_V1"
_ROOT_DIR = "control/install-cutover-v1"
_OLD_TASK = "News-Grasp Production (old)"
_NEW_TASK = "News-Grasp Production"
_PULL_TASK = "News-Grasp Pull"
_INVENTORY_TASK = "News-Grasp Inventory"
_OWNER_SPECS = {
    "runtime_generation_owner": [
        "PRODUCTION_GENERATION_MANIFEST_V2",
        "NEWS_GRASP_ACTIVE_GENERATION_V2",
    ],
    "ops_install_owner": [
        "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
        "NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1",
    ],
}
_AUTHORITY_KEYS = {
    "schemaVersion",
    "authorityId",
    "issueDate",
    "generation",
    "sourceRoot",
    "sourceSha256",
    "ownerReceipts",
    "authoritySha256",
}
_RECEIPT_KEYS = {
    "ownerId",
    "receiptSchemas",
    "sourceSha256",
    "installedSha256",
    "taskActionSha256",
    "preimageSha256",
    "receiptSha256",
}
_MANIFEST_KEYS = {"schemaVersion", "scheduleId", "tasks"}
_TASK_KEYS = {
    "taskPath",
    "taskName",
    "multipleInstancesPolicy",
    "triggers",
    "action",
}
_ACTION_KEYS = {"entryModule", "argv", "workingDirectoryToken"}
_TRIGGER_KEYS = {"triggerId", "kind", "localTime", "timeZone"}
_JOURNAL_KEYS = {
    "schemaVersion",
    "authorityId",
    "authoritySha256",
    "sourceRoot",
    "sourceSha256",
    "installedRoot",
    "installedSha256",
    "installedIdentity",
    "sourceIdentity",
    "oldPreimage",
    "oldPreimageSha256",
    "candidateDefinition",
    "ownerPreimages",
    "ownerReceiptHashes",
    "phase",
    "candidateRegistered",
    "canaryLaunched",
    "observedAt",
    "journalSha256",
}
_PHASES = {
    "PREIMAGE_DURABLE",
    "STAGED",
    "INSTALLED",
    "POINTER_DURABLE",
    "OLD_DISABLED",
    "NEW_ENABLED",
    "COMMITTED",
    "ROLLBACK_NEW_DISABLED",
    "ROLLBACK_OPS_RESTORED",
    "ROLLBACK_RUNTIME_RESTORED",
    "ROLLBACK_OLD_RESTORED",
    "ROLLED_BACK",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHELL = re.compile(r'[&|;<>`"]')
_SECRET = re.compile(r"(?i)(?:token|secret|password|passwd|api[_-]?key)\s*=")


class InstallControlError(RuntimeError):
    """公開契約違反を secret-free な安定 reason で表す。"""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = str(reason)
        super().__init__(message or self.reason)


class PrivilegeDenied(InstallControlError):
    """注入 adapter が権限拒否を表す型。"""

    def __init__(self, message: str = "privilege_denied") -> None:
        super().__init__("privilege_denied", message)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, ValueError) as exc:
        raise InstallControlError("file_read_failed") from exc


def _safe_path(value: Path | str, label: str) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise InstallControlError(f"{label}_not_absolute")
        return path.resolve(strict=False)
    except InstallControlError:
        raise
    except (OSError, ValueError) as exc:
        raise InstallControlError(f"{label}_invalid") from exc


def _within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise InstallControlError(f"{label}_outside_authorized_root") from exc


def _validate_hex(value: Any, reason: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise InstallControlError(reason)
    return value


def _at_string(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or not value:
        raise InstallControlError("observed_at_invalid")
    return value


class _DefaultSecurity:
    """Unit-safe default security adapter; production may inject stronger checks."""

    @staticmethod
    def capture_identity(path: Path) -> dict[str, Any]:
        return {"path": str(path), "sha256": _file_sha(path)}

    @staticmethod
    def verify_identity(path: Path, token: Mapping[str, Any]) -> bool:
        return isinstance(token, Mapping) and token.get("sha256") == _file_sha(path)

    @staticmethod
    def is_reparse(path: Path) -> bool:
        return path.is_symlink()

    @staticmethod
    def acl_is_secure(path: Path) -> bool:
        return True

    @staticmethod
    def signer_is_trusted(executable: Path) -> bool:
        return executable.is_file()


class InstallCutoverController:
    """耐久 journal に束縛された stage/cutover/rollback controller。"""

    def __init__(
        self,
        runtime_root: Path | str,
        task_adapter: Any,
        pythonw_path: Path | str,
        process_adapter: Any = None,
        security_adapter: Any = None,
        owner_adapter: Any = None,
        boundary_hook: Any = None,
    ) -> None:
        self.runtime_root = _safe_path(runtime_root, "runtime_root")
        self.task_adapter = task_adapter
        self.pythonw_path = _safe_path(pythonw_path, "pythonw_path")
        self.process_adapter = process_adapter
        self.security = security_adapter or _DefaultSecurity()
        self.owner_adapter = owner_adapter
        self.boundary_hook = boundary_hook
        self._journal_path = self.runtime_root / _ROOT_DIR / "journal.json"
        self._focus_theft_count = 0
        self._shared_process_kill_count = 0
        self._auto_deleted_pull_count = 0

    # ---- durable state -------------------------------------------------
    def _write_journal(self, journal: Mapping[str, Any]) -> None:
        body = dict(journal)
        body.pop("journalSha256", None)
        if set(body) != _JOURNAL_KEYS - {"journalSha256"}:
            raise InstallControlError("journal_keys_invalid")
        body["journalSha256"] = _sha(body)
        if isinstance(journal, dict):
            journal["journalSha256"] = body["journalSha256"]
        directory = self._journal_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = _canonical(body)
        fd, temporary = tempfile.mkstemp(prefix=".journal-", suffix=".tmp", dir=str(directory))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._journal_path)
            try:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _load_journal(self) -> dict[str, Any] | None:
        if not self._journal_path.is_file():
            return None
        try:
            value = json.loads(self._journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InstallControlError("journal_corrupt") from exc
        if not isinstance(value, dict) or set(value) != _JOURNAL_KEYS:
            raise InstallControlError("journal_keys_invalid")
        expected = value.get("journalSha256")
        body = {key: item for key, item in value.items() if key != "journalSha256"}
        if not isinstance(expected, str) or expected != _sha(body):
            raise InstallControlError("journal_hash_mismatch")
        if value.get("schemaVersion") != _SCHEMA or value.get("phase") not in _PHASES:
            raise InstallControlError("journal_semantics_invalid")
        return value

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    # ---- validation ----------------------------------------------------
    @staticmethod
    def _validate_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS:
            raise InstallControlError("manifest_keys_invalid")
        if manifest.get("schemaVersion") != "NEWS_GRASP_CONTROL_MANIFEST_V1":
            raise InstallControlError("manifest_schema_invalid")
        if manifest.get("scheduleId") != "news-grasp-daily-v1":
            raise InstallControlError("manifest_schedule_invalid")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], Mapping):
            raise InstallControlError("manifest_tasks_invalid")
        task = tasks[0]
        if set(task) != _TASK_KEYS:
            raise InstallControlError("manifest_task_keys_invalid")
        if task.get("taskPath") != "\\" or task.get("taskName") != _NEW_TASK:
            raise InstallControlError("manifest_task_identity_invalid")
        if task.get("multipleInstancesPolicy") != "Parallel":
            raise InstallControlError("manifest_policy_invalid")
        triggers = task.get("triggers")
        if not isinstance(triggers, list) or len(triggers) != 2:
            raise InstallControlError("manifest_triggers_invalid")
        expected_ids = ["scheduled-0600", "audit-0640"]
        for index, trigger in enumerate(triggers):
            if not isinstance(trigger, Mapping) or set(trigger) != _TRIGGER_KEYS:
                raise InstallControlError("manifest_trigger_keys_invalid")
            if trigger.get("triggerId") != expected_ids[index]:
                raise InstallControlError("manifest_trigger_identity_invalid")
            if trigger.get("kind") != "daily" or trigger.get("timeZone") != "Asia/Tokyo":
                raise InstallControlError("manifest_trigger_invalid")
            if not isinstance(trigger.get("localTime"), str):
                raise InstallControlError("manifest_trigger_time_invalid")
        action = task.get("action")
        if not isinstance(action, Mapping) or set(action) != _ACTION_KEYS:
            raise InstallControlError("manifest_action_keys_invalid")
        if action.get("entryModule") != "tools.news_grasp_cleanroom_dispatch":
            raise InstallControlError("manifest_entry_invalid")
        if action.get("workingDirectoryToken") != "<RUNTIME_ROOT>":
            raise InstallControlError("manifest_working_directory_invalid")
        argv = action.get("argv")
        if not isinstance(argv, list) or not argv or any(not isinstance(token, str) for token in argv):
            raise InstallControlError("manifest_argv_invalid")
        for token in argv:
            if not token or _CONTROL.search(token) or _SHELL.search(token) or _SECRET.search(token):
                raise InstallControlError("manifest_argv_rejected")
        return manifest

    def _validate_authority(
        self,
        authority: Mapping[str, Any],
        manifest: Mapping[str, Any],
        source_root: Path,
        source_sha256: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        if not isinstance(authority, Mapping) or set(authority) != _AUTHORITY_KEYS:
            raise InstallControlError("authority_keys_invalid")
        if authority.get("schemaVersion") != "INSTALL_AUTHORITY_V1":
            raise InstallControlError("authority_schema_invalid")
        if not isinstance(authority.get("authorityId"), str) or not authority["authorityId"]:
            raise InstallControlError("authority_id_invalid")
        if not isinstance(authority.get("issueDate"), str) or not authority["issueDate"]:
            raise InstallControlError("authority_issue_date_invalid")
        if not isinstance(authority.get("generation"), int) or isinstance(authority.get("generation"), bool):
            raise InstallControlError("authority_generation_invalid")
        if _safe_path(authority.get("sourceRoot"), "authority_source_root") != source_root:
            raise InstallControlError("authority_source_root_mismatch")
        authority_hash = _validate_hex(authority.get("authoritySha256"), "authority_hash_invalid")
        if authority_hash != _sha({key: value for key, value in authority.items() if key != "authoritySha256"}):
            raise InstallControlError("authority_hash_mismatch")
        source_hash = _validate_hex(authority.get("sourceSha256"), "authority_source_hash_invalid")
        if source_sha256 is not None and source_hash != source_sha256:
            raise InstallControlError("authority_source_hash_mismatch")
        owners = authority.get("ownerReceipts")
        if not isinstance(owners, Mapping) or set(owners) != set(_OWNER_SPECS):
            raise InstallControlError("owner_receipts_invalid")
        action_hash = _sha(manifest["tasks"][0]["action"])
        preimages: dict[str, str] = {}
        for owner_id, schemas in _OWNER_SPECS.items():
            receipt = owners.get(owner_id)
            if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_KEYS:
                raise InstallControlError("owner_receipt_keys_invalid")
            if receipt.get("ownerId") != owner_id or receipt.get("receiptSchemas") != schemas:
                raise InstallControlError("owner_receipt_identity_invalid")
            if receipt.get("sourceSha256") != source_hash or receipt.get("installedSha256") != source_hash:
                raise InstallControlError("owner_receipt_content_hash_mismatch")
            if receipt.get("taskActionSha256") != action_hash:
                raise InstallControlError("owner_receipt_action_hash_mismatch")
            preimage = _validate_hex(receipt.get("preimageSha256"), "owner_preimage_invalid")
            receipt_hash = _validate_hex(receipt.get("receiptSha256"), "owner_receipt_hash_invalid")
            if receipt_hash != _sha({key: value for key, value in receipt.items() if key != "receiptSha256"}):
                raise InstallControlError("owner_receipt_hash_mismatch")
            preimages[owner_id] = preimage
        return source_hash, preimages

    def _security_preflight(self, source_root: Path, launcher: Path) -> dict[str, Any]:
        if not launcher.is_file():
            raise InstallControlError("launcher_missing")
        try:
            if self.security.is_reparse(source_root) or self.security.is_reparse(launcher):
                raise InstallControlError("symlink_reparse_rejected")
            token = self.security.capture_identity(launcher)
            if not self.security.verify_identity(launcher, token):
                raise InstallControlError("source_identity_swap")
            if not self.security.acl_is_secure(source_root) or not self.security.acl_is_secure(launcher):
                raise InstallControlError("insecure_acl")
            if not self.security.signer_is_trusted(self.pythonw_path):
                raise InstallControlError("untrusted_signer")
        except InstallControlError:
            raise
        except PrivilegeDenied:
            raise
        except Exception as exc:
            raise InstallControlError("security_preflight_failed") from exc
        return token

    def _task_snapshot(self) -> Mapping[str, Any]:
        try:
            snapshot = self.task_adapter.snapshot()
        except PrivilegeDenied:
            raise
        except InstallControlError:
            raise
        except Exception as exc:
            raise InstallControlError("task_snapshot_failed") from exc
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("tasks"), Mapping):
            raise InstallControlError("task_snapshot_invalid")
        return snapshot

    @staticmethod
    def _task_state(snapshot: Mapping[str, Any], name: str) -> bool:
        task = snapshot.get("tasks", {}).get(name)
        return bool(task.get("enabled")) if isinstance(task, Mapping) else False

    def _candidate_definition(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        task = manifest["tasks"][0]
        action = task["action"]
        return {
            "taskPath": task["taskPath"],
            "taskName": task["taskName"],
            "enabled": False,
            "executable": str(self.pythonw_path),
            "arguments": list(action["argv"]),
            "workingDirectory": str(self.runtime_root),
            "triggers": deepcopy(task["triggers"]),
            "multipleInstancesPolicy": task["multipleInstancesPolicy"],
        }

    def _new_journal(
        self,
        authority: Mapping[str, Any],
        source_root: Path,
        installed_root: Path,
        source_hash: str,
        source_identity: Mapping[str, Any],
        old_preimage: Any,
        candidate: Mapping[str, Any],
        preimages: Mapping[str, str],
        receipt_hashes: Mapping[str, str],
        installed_identity: Mapping[str, Any] | None,
        observed_at: str,
    ) -> dict[str, Any]:
        return {
            "schemaVersion": _SCHEMA,
            "authorityId": authority["authorityId"],
            "authoritySha256": authority["authoritySha256"],
            "sourceRoot": str(source_root),
            "sourceSha256": source_hash,
            "installedRoot": str(installed_root),
            "installedSha256": source_hash,
            "installedIdentity": deepcopy(dict(installed_identity)) if installed_identity else None,
            "sourceIdentity": deepcopy(dict(source_identity)),
            "oldPreimage": deepcopy(old_preimage),
            "oldPreimageSha256": _sha(old_preimage),
            "candidateDefinition": deepcopy(dict(candidate)),
            "ownerPreimages": dict(preimages),
            "ownerReceiptHashes": dict(receipt_hashes),
            "phase": "PREIMAGE_DURABLE",
            "candidateRegistered": False,
            "canaryLaunched": False,
            "observedAt": observed_at,
            "journalSha256": "",
        }

    # ---- public operations --------------------------------------------
    def stage(
        self,
        manifest: Mapping[str, Any],
        source_root: Path | str,
        installed_root: Path | str,
        authority: Mapping[str, Any],
        observed_at: datetime | str,
    ) -> dict[str, Any]:
        manifest = self._validate_manifest(manifest)
        source = _safe_path(source_root, "source_root")
        installed = _safe_path(installed_root, "installed_root")
        _within(source, self.runtime_root, "source_root")
        _within(installed, self.runtime_root, "installed_root")
        if not source.is_dir() or not installed.exists() or not installed.is_dir():
            raise InstallControlError("install_root_invalid")
        launcher = source / "launcher.pyw"
        source_hash_actual = _file_sha(launcher) if launcher.is_file() else None
        source_hash, preimages = self._validate_authority(authority, manifest, source, source_hash_actual)
        if source_hash_actual != source_hash:
            raise InstallControlError("source_hash_mismatch")
        installed_launcher = installed / "launcher.pyw"
        if installed_launcher.exists():
            if not installed_launcher.is_file() or _file_sha(installed_launcher) != source_hash:
                raise InstallControlError("installed_hash_mismatch")
        source_identity = self._security_preflight(source, launcher)
        observed = _at_string(observed_at)
        journal = self._load_journal()
        candidate = self._candidate_definition(manifest)
        if journal is None:
            snapshot = self._task_snapshot()
            old_task = snapshot.get("tasks", {}).get(_OLD_TASK, {})
            old_preimage = old_task.get("definition") if isinstance(old_task, Mapping) else None
            installed_identity = (
                {"path": str(installed_launcher), "sha256": _file_sha(installed_launcher)}
                if installed_launcher.is_file()
                else None
            )
            journal = self._new_journal(
                authority,
                source,
                installed,
                source_hash,
                source_identity,
                old_preimage,
                candidate,
                preimages,
                {
                    owner_id: authority["ownerReceipts"][owner_id]["receiptSha256"]
                    for owner_id in _OWNER_SPECS
                },
                installed_identity,
                observed,
            )
            self._write_journal(journal)
            self._hook("stage")
        else:
            self._validate_resume(journal, authority, source, installed, source_hash, candidate)

        snapshot = self._task_snapshot()
        new_enabled = self._task_state(snapshot, _NEW_TASK)
        if new_enabled:
            self.task_adapter.disable(_NEW_TASK)
            snapshot = self._task_snapshot()
            if self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK):
                raise InstallControlError("dual_enabled")
        if not self._task_state(snapshot, _OLD_TASK):
            self.task_adapter.enable(_OLD_TASK)
        if journal["phase"] == "PREIMAGE_DURABLE":
            self._copy_launcher(source, installed)
            journal["phase"] = "STAGED"
            self._write_journal(journal)
            self._hook("install")
        if not journal.get("candidateRegistered"):
            self.task_adapter.register_disabled(_NEW_TASK, candidate)
            journal["candidateRegistered"] = True
            journal["phase"] = "INSTALLED"
            self._write_journal(journal)
        if self.process_adapter is not None and not journal.get("canaryLaunched"):
            self.process_adapter.launch(
                [str(self.pythonw_path), str(installed / "launcher.pyw")],
                installed,
                shell=False,
                creationflags=CREATE_NO_WINDOW,
                encoding="utf-8",
                includeChildTree=True,
                noFocusTheft=True,
                noAutoOpen=True,
                noUserMonitoring=True,
            )
            journal["canaryLaunched"] = True
        if journal["phase"] in {"STAGED", "INSTALLED"}:
            self._hook("pointer")
            journal["phase"] = "POINTER_DURABLE"
            self._write_journal(journal)
        return self._result("INSTALL_STAGE_RESULT_V1", journal)

    def cutover(self, authority: Mapping[str, Any], observed_at: datetime | str) -> dict[str, Any]:
        journal = self._load_journal()
        if journal is None:
            raise InstallControlError("stage_required")
        self._validate_resume(journal, authority, Path(journal["sourceRoot"]), Path(journal["installedRoot"]), journal["sourceSha256"], journal["candidateDefinition"])
        if journal["phase"] in {"COMMITTED", "ROLLED_BACK"}:
            return self._result("CUTOVER_RESULT_V1", journal)
        if journal["phase"] == "POINTER_DURABLE":
            snapshot = self._task_snapshot()
            if self._task_state(snapshot, _OLD_TASK):
                self._hook("old_disable")
                self.task_adapter.disable(_OLD_TASK)
            journal["phase"] = "OLD_DISABLED"
            self._write_journal(journal)
        if journal["phase"] == "OLD_DISABLED":
            snapshot = self._task_snapshot()
            if not self._task_state(snapshot, _NEW_TASK):
                self._hook("new_enable")
                self.task_adapter.enable(_NEW_TASK)
            snapshot = self._task_snapshot()
            if self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK):
                raise InstallControlError("dual_enabled")
            journal["phase"] = "NEW_ENABLED"
            self._write_journal(journal)
        if journal["phase"] == "NEW_ENABLED":
            self._hook("commit")
            journal["phase"] = "COMMITTED"
            journal["observedAt"] = _at_string(observed_at)
            self._write_journal(journal)
        return self._result("CUTOVER_RESULT_V1", journal)

    def rollback(self, authority: Mapping[str, Any], observed_at: datetime | str) -> dict[str, Any]:
        journal = self._load_journal()
        if journal is None:
            raise InstallControlError("stage_required")
        self._validate_resume(journal, authority, Path(journal["sourceRoot"]), Path(journal["installedRoot"]), journal["sourceSha256"], journal["candidateDefinition"])
        if journal["phase"] == "ROLLED_BACK":
            return self._result("ROLLBACK_RESULT_V1", journal)
        if journal["phase"] not in {"ROLLBACK_NEW_DISABLED", "ROLLBACK_OPS_RESTORED", "ROLLBACK_RUNTIME_RESTORED", "ROLLBACK_OLD_RESTORED"}:
            self._hook("rollback_before_new_disable")
            snapshot = self._task_snapshot()
            if self._task_state(snapshot, _NEW_TASK):
                self.task_adapter.disable(_NEW_TASK)
            journal["phase"] = "ROLLBACK_NEW_DISABLED"
            self._write_journal(journal)
            self._hook("rollback_after_new_disable")
        if journal["phase"] == "ROLLBACK_NEW_DISABLED":
            self._hook("rollback_before_restore")
            if self.owner_adapter is not None:
                self.owner_adapter.restore("install_news_grasp_ops", journal["ownerPreimages"]["ops_install_owner"])
                journal["phase"] = "ROLLBACK_OPS_RESTORED"
                self._write_journal(journal)
                self.owner_adapter.restore("launcher_runtime_lifecycle", journal["ownerPreimages"]["runtime_generation_owner"])
                journal["phase"] = "ROLLBACK_RUNTIME_RESTORED"
                self._write_journal(journal)
            else:
                journal["phase"] = "ROLLBACK_RUNTIME_RESTORED"
                self._write_journal(journal)
        if journal["phase"] == "ROLLBACK_OPS_RESTORED":
            # Defensive resume if a future adapter persists between owner calls.
            self.owner_adapter.restore("launcher_runtime_lifecycle", journal["ownerPreimages"]["runtime_generation_owner"])
            journal["phase"] = "ROLLBACK_RUNTIME_RESTORED"
            self._write_journal(journal)
        if journal["phase"] == "ROLLBACK_RUNTIME_RESTORED":
            self.task_adapter.restore(_OLD_TASK, journal["oldPreimage"])
            journal["phase"] = "ROLLBACK_OLD_RESTORED"
            self._write_journal(journal)
            self.task_adapter.enable(_OLD_TASK)
        if journal["phase"] == "ROLLBACK_OLD_RESTORED":
            self._hook("rollback_commit")
            journal["phase"] = "ROLLED_BACK"
            journal["observedAt"] = _at_string(observed_at)
            self._write_journal(journal)
        return self._result("ROLLBACK_RESULT_V1", journal)

    def inspect(self) -> dict[str, Any]:
        journal = self._load_journal()
        phase = journal["phase"] if journal else "ABSENT"
        dual = 0
        try:
            snapshot = self.task_adapter.snapshot()
            dual = int(self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK))
        except Exception:
            dual = 0
        return {
            "schemaVersion": "INSTALL_INSPECTION_V1",
            "phase": phase,
            "dualEnabledCount": dual,
            "autoDeletedPullTaskCount": self._auto_deleted_pull_count,
            "focusTheftCount": self._focus_theft_count,
            "sharedProcessKillCount": self._shared_process_kill_count,
            "installedSha256": journal.get("installedSha256") if journal else None,
            "taskActionSha256": _sha(journal["candidateDefinition"]["arguments"]) if journal else None,
        }

    # ---- internal operations -----------------------------------------
    def _validate_resume(
        self,
        journal: Mapping[str, Any],
        authority: Mapping[str, Any],
        source: Path,
        installed: Path,
        source_hash: str,
        candidate: Mapping[str, Any],
    ) -> None:
        action = {
            "entryModule": "tools.news_grasp_cleanroom_dispatch",
            "argv": list(candidate.get("arguments", [])),
            "workingDirectoryToken": "<RUNTIME_ROOT>",
        }
        resume_manifest = {
            "schemaVersion": "NEWS_GRASP_CONTROL_MANIFEST_V1",
            "scheduleId": "news-grasp-daily-v1",
            "tasks": [
                {
                    "taskPath": candidate.get("taskPath"),
                    "taskName": candidate.get("taskName"),
                    "multipleInstancesPolicy": candidate.get("multipleInstancesPolicy"),
                    "triggers": deepcopy(candidate.get("triggers")),
                    "action": action,
                }
            ],
        }
        current_source_hash = _file_sha(source / "launcher.pyw")
        self._validate_manifest(resume_manifest)
        self._validate_authority(authority, resume_manifest, source, current_source_hash)
        if journal.get("authoritySha256") != authority.get("authoritySha256"):
            raise InstallControlError("journal_authority_mismatch")
        if journal.get("sourceRoot") != str(source) or journal.get("installedRoot") != str(installed):
            raise InstallControlError("journal_path_mismatch")
        if journal.get("sourceSha256") != source_hash or current_source_hash != source_hash:
            raise InstallControlError("journal_binding_mismatch")
        if journal.get("candidateDefinition") != dict(candidate):
            raise InstallControlError("journal_candidate_mismatch")
        expected_preimages = {
            owner_id: authority["ownerReceipts"][owner_id]["preimageSha256"]
            for owner_id in _OWNER_SPECS
        }
        expected_receipts = {
            owner_id: authority["ownerReceipts"][owner_id]["receiptSha256"]
            for owner_id in _OWNER_SPECS
        }
        if journal.get("ownerPreimages") != expected_preimages or journal.get("ownerReceiptHashes") != expected_receipts:
            raise InstallControlError("journal_owner_binding_mismatch")
        installed_launcher = installed / "launcher.pyw"
        if installed_launcher.is_file() and _file_sha(installed_launcher) != source_hash:
            raise InstallControlError("installed_hash_mismatch")

    @staticmethod
    def _copy_launcher(source: Path, installed: Path) -> None:
        launcher = source / "launcher.pyw"
        target = installed / "launcher.pyw"
        installed.mkdir(parents=True, exist_ok=True)
        data = launcher.read_bytes()
        temporary = target.with_name(f".{target.name}.tmp")
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if _file_sha(target) != hashlib.sha256(data).hexdigest():
            raise InstallControlError("installed_hash_mismatch")

    @staticmethod
    def _result(schema: str, journal: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": schema,
            "phase": journal["phase"],
            "authorityId": journal["authorityId"],
            "journalSha256": journal["journalSha256"],
            "dualEnabledCount": 0,
        }


__all__ = ["InstallCutoverController", "InstallControlError", "PrivilegeDenied"]
