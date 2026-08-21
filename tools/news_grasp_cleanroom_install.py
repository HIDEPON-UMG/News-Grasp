"""News-Grasp の install/cutover を隔離して扱う clean-room control plane。

このモジュールは実際の Windows Task Scheduler や共有プロセスへ直接触れず、
task/process/security/owner adapter という狭い protocol の上で、耐久 journal と
fail-closed な preflight を提供する。production adapter は上位の運用層が注入する。
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
    "canaryReceipt",
    "canaryReceiptSha256",
    "observedAt",
    "journalSha256",
}
_LEGACY_JOURNAL_KEYS = _JOURNAL_KEYS - {"canaryReceipt", "canaryReceiptSha256"}
_CANARY_RECEIPT_KEYS = {
    "schemaVersion",
    "status",
    "exitCode",
    "processId",
    "installedSha256",
    "argvSha256",
    "receiptSha256",
}
FILE_READ_ATTRIBUTES = 0x00000080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
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


def _pin_path_key(value: Path | str) -> str:
    """Handle の最終pathと要求pathを比較するための安定表現。"""
    text = os.path.normpath(os.path.abspath(os.fspath(value))).replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(text).rstrip("\\") or text


class _DirectoryPin:
    """path based write を同一 directory object へ束縛する OS pin。"""

    def __init__(self, path: Path, label: str) -> None:
        self.path = Path(path)
        self.label = str(label)
        self._handle: Any = None
        self._kernel32: Any = None
        self._fd: int | None = None
        self._identity: tuple[int, int] | None = None
        self._final_path: str | None = None

    def __enter__(self) -> "_DirectoryPin":
        try:
            if os.name == "nt":
                self._open_windows()
            else:
                self._open_posix()
            self.verify()
            return self
        except InstallControlError:
            self.close()
            raise
        except (OSError, ValueError, TypeError) as exc:
            self.close()
            raise InstallControlError(f"{self.label}_pin_failed") from exc

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _open_posix(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        self._fd = os.open(self.path, flags)
        opened = os.fstat(self._fd)
        current = os.lstat(self.path)
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            raise InstallControlError("symlink_reparse_rejected")
        opened_key = (int(opened.st_dev), int(opened.st_ino))
        current_key = (int(current.st_dev), int(current.st_ino))
        if opened_key != current_key:
            raise InstallControlError(f"{self.label}_identity_swap")
        self._identity = opened_key
        self._final_path = _pin_path_key(self.path)

    def _open_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(self.path),
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, invalid):
            raise OSError(ctypes.get_last_error(), "CreateFileW directory pin failed")
        self._kernel32 = kernel32
        self._handle = handle
        try:
            self._final_path = self._get_final_path()
            volume, file_index = self._get_file_identity()
            info = os.lstat(self.path)
            if info.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                raise InstallControlError("symlink_reparse_rejected")
            if not stat.S_ISDIR(info.st_mode):
                raise InstallControlError(f"{self.label}_not_directory")
            # Windows の Python は st_dev 下位32bitへvolume serialを返し、
            # GetFileInformationByHandleW は同値をDWORDで返す。
            current_key = (int(getattr(info, "st_dev", 0)) & 0xFFFFFFFF, int(getattr(info, "st_ino", 0)))
            handle_key = (volume, file_index)
            if current_key != handle_key:
                raise InstallControlError(f"{self.label}_identity_swap")
            if _pin_path_key(self._final_path) != _pin_path_key(self.path):
                raise InstallControlError(f"{self.label}_path_swap")
            self._identity = handle_key
        except Exception:
            self.close()
            raise

    def _get_final_path(self) -> str:
        import ctypes
        from ctypes import wintypes

        get_final = self._kernel32.GetFinalPathNameByHandleW
        get_final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final.restype = wintypes.DWORD
        size = 32768
        buffer = ctypes.create_unicode_buffer(size)
        length = int(get_final(self._handle, buffer, size, 0))
        if length <= 0 or length >= size:
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
        return buffer.value[:length]

    def _get_file_identity(self) -> tuple[int, int]:
        import ctypes
        from ctypes import wintypes

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", wintypes.DWORD),
                ("ftCreationTime", wintypes.FILETIME),
                ("ftLastAccessTime", wintypes.FILETIME),
                ("ftLastWriteTime", wintypes.FILETIME),
                ("dwVolumeSerialNumber", wintypes.DWORD),
                ("nFileSizeHigh", wintypes.DWORD),
                ("nFileSizeLow", wintypes.DWORD),
                ("nNumberOfLinks", wintypes.DWORD),
                ("nFileIndexHigh", wintypes.DWORD),
                ("nFileIndexLow", wintypes.DWORD),
            ]

        get_info = self._kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_info.restype = wintypes.BOOL
        info = _ByHandleFileInformation()
        if not get_info(self._handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise InstallControlError("symlink_reparse_rejected")
        file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
        return int(info.dwVolumeSerialNumber), file_index

    def verify(self) -> None:
        if self._identity is None:
            raise InstallControlError(f"{self.label}_pin_invalid")
        try:
            if os.name == "nt":
                if self._handle is None:
                    raise InstallControlError(f"{self.label}_pin_invalid")
                final_path = self._get_final_path()
                if _pin_path_key(final_path) != _pin_path_key(self.path):
                    raise InstallControlError(f"{self.label}_path_swap")
                if self._get_file_identity() != self._identity:
                    raise InstallControlError(f"{self.label}_identity_swap")
                current = os.lstat(self.path)
                if current.st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise InstallControlError("symlink_reparse_rejected")
                current_key = (int(getattr(current, "st_dev", 0)) & 0xFFFFFFFF, int(getattr(current, "st_ino", 0)))
                if current_key != self._identity:
                    raise InstallControlError(f"{self.label}_identity_swap")
            else:
                if self._fd is None:
                    raise InstallControlError(f"{self.label}_pin_invalid")
                opened = os.fstat(self._fd)
                current = os.lstat(self.path)
                if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
                    raise InstallControlError("symlink_reparse_rejected")
                if (int(opened.st_dev), int(opened.st_ino)) != self._identity or (int(current.st_dev), int(current.st_ino)) != self._identity:
                    raise InstallControlError(f"{self.label}_identity_swap")
        except InstallControlError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise InstallControlError(f"{self.label}_identity_check_failed") from exc

    def close(self) -> None:
        if self._handle is not None and self._kernel32 is not None:
            try:
                self._kernel32.CloseHandle(self._handle)
            finally:
                self._handle = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None


@contextmanager
def _directory_pin(path: Path, label: str) -> Any:
    with _DirectoryPin(path, label) as pin:
        yield pin


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
        # Resolve is deliberately last.  Every lexical component that already
        # exists is inspected with lstat first, so a link/reparse point cannot
        # redirect the later containment check or any write operation.
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise InstallControlError(f"{label}_invalid") from exc
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise InstallControlError("symlink_reparse_rejected")
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
    """互換用の名前だけを残し、実運用のsecurity fallbackには使わない。"""

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
        raise InstallControlError("security_adapter_required")


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
        self.security = security_adapter
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
        # 未完了のlegacy V1 journalにはcanary receiptが無い場合があるため、
        # 次回の耐久書込みでsealed shapeへ昇格する。
        if set(body) == _LEGACY_JOURNAL_KEYS - {"journalSha256"}:
            body["canaryReceipt"] = None
            body["canaryReceiptSha256"] = None
            if isinstance(journal, dict):
                journal["canaryReceipt"] = None
                journal["canaryReceiptSha256"] = None
        if set(body) != _JOURNAL_KEYS - {"journalSha256"}:
            raise InstallControlError("journal_keys_invalid")
        body["journalSha256"] = _sha(body)
        if isinstance(journal, dict):
            journal["journalSha256"] = body["journalSha256"]
        directory = self._journal_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = _canonical(body)
        with _directory_pin(directory, "journal_parent") as pin:
            pin.verify()
            self._hook("before_journal_parent_swap")
            pin.verify()
            fd, temporary = tempfile.mkstemp(prefix=".journal-", suffix=".tmp", dir=str(directory))
            try:
                pin.verify()
                with os.fdopen(fd, "wb") as stream:
                    fd = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                pin.verify()
                os.replace(temporary, self._journal_path)
                pin.verify()
                try:
                    directory_fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
                pin.verify()
            finally:
                if fd >= 0:
                    os.close(fd)
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
        if not isinstance(value, dict) or (set(value) != _JOURNAL_KEYS and set(value) != _LEGACY_JOURNAL_KEYS):
            raise InstallControlError("journal_keys_invalid")
        expected = value.get("journalSha256")
        body = {key: item for key, item in value.items() if key != "journalSha256"}
        if not isinstance(expected, str) or expected != _sha(body):
            raise InstallControlError("journal_hash_mismatch")
        if set(value) == _LEGACY_JOURNAL_KEYS:
            # 元のV1 sealを検証したまま、欠落canaryを呼び出し側へ明示する。
            # 次回の耐久書込みで新shapeへ昇格する。
            value["canaryReceipt"] = None
            value["canaryReceiptSha256"] = None
        if value.get("schemaVersion") != _SCHEMA or not isinstance(value.get("phase"), str) or value.get("phase") not in _PHASES:
            raise InstallControlError("journal_semantics_invalid")
        self._validate_journal_structure(value)
        return value

    def _validate_journal_structure(self, journal: Mapping[str, Any]) -> None:
        """journalのcritical fieldをmutation前に型・path・hash検証する。"""
        if not isinstance(journal.get("authorityId"), str) or not journal["authorityId"]:
            raise InstallControlError("journal_authority_id_invalid")
        for key in ("authoritySha256", "sourceSha256", "installedSha256", "oldPreimageSha256", "journalSha256"):
            _validate_hex(journal.get(key), f"journal_{key}_invalid")
        for key in ("sourceRoot", "installedRoot"):
            path = _safe_path(journal.get(key), f"journal_{key}")
            _within(path, self.runtime_root, f"journal_{key}")
        for key in ("sourceIdentity",):
            identity = journal.get(key)
            if not isinstance(identity, Mapping) or set(identity) != {"path", "sha256"}:
                raise InstallControlError("journal_source_identity_invalid")
            _safe_path(identity.get("path"), "journal_source_identity_path")
            _validate_hex(identity.get("sha256"), "journal_source_identity_hash_invalid")
        installed_identity = journal.get("installedIdentity")
        if installed_identity is not None:
            if not isinstance(installed_identity, Mapping) or set(installed_identity) != {"path", "sha256"}:
                raise InstallControlError("journal_installed_identity_invalid")
            _safe_path(installed_identity.get("path"), "journal_installed_identity_path")
            _validate_hex(installed_identity.get("sha256"), "journal_installed_identity_hash_invalid")
        if journal.get("oldPreimageSha256") != _sha(journal.get("oldPreimage")):
            raise InstallControlError("journal_old_preimage_hash_mismatch")
        candidate = journal.get("candidateDefinition")
        if not isinstance(candidate, Mapping) or set(candidate) != {
            "taskPath",
            "taskName",
            "enabled",
            "executable",
            "arguments",
            "workingDirectory",
            "triggers",
            "multipleInstancesPolicy",
        }:
            raise InstallControlError("journal_candidate_invalid")
        if candidate.get("enabled") is not False or not isinstance(candidate.get("arguments"), list):
            raise InstallControlError("journal_candidate_invalid")
        _safe_path(candidate.get("executable"), "journal_candidate_executable")
        _safe_path(candidate.get("workingDirectory"), "journal_candidate_working_directory")
        for key in ("ownerPreimages", "ownerReceiptHashes"):
            values = journal.get(key)
            if not isinstance(values, Mapping) or set(values) != set(_OWNER_SPECS):
                raise InstallControlError(f"journal_{key}_invalid")
            for owner_id in _OWNER_SPECS:
                _validate_hex(values.get(owner_id), f"journal_{key}_{owner_id}_invalid")
        if not isinstance(journal.get("candidateRegistered"), bool) or not isinstance(journal.get("canaryLaunched"), bool):
            raise InstallControlError("journal_flags_invalid")
        canary = journal.get("canaryReceipt")
        canary_hash = journal.get("canaryReceiptSha256")
        if canary is not None and not isinstance(canary, Mapping):
            raise InstallControlError("canary_receipt_invalid")
        if canary is None:
            if canary_hash is not None:
                raise InstallControlError("canary_receipt_invalid")
        else:
            _validate_hex(canary_hash, "canary_receipt_hash_invalid")
        if not isinstance(journal.get("observedAt"), str) or not journal["observedAt"]:
            raise InstallControlError("journal_observed_at_invalid")

    def _hook(self, name: str) -> None:
        if self.boundary_hook is not None:
            self.boundary_hook(name)

    @staticmethod
    def _require_adapter(adapter: Any, reason: str, methods: tuple[str, ...]) -> None:
        if adapter is None or any(not callable(getattr(adapter, method, None)) for method in methods):
            raise InstallControlError(reason)

    def _require_security_adapter(self) -> None:
        self._require_adapter(
            self.security,
            "security_adapter_required",
            ("capture_identity", "verify_identity", "is_reparse", "acl_is_secure", "signer_is_trusted"),
        )

    def _require_stage_adapters(self) -> None:
        self._require_adapter(
            self.task_adapter,
            "task_adapter_required",
            ("snapshot", "disable", "enable", "register_disabled"),
        )
        self._require_adapter(self.process_adapter, "process_adapter_required", ("launch",))

    def _require_owner_adapter(self) -> None:
        self._require_adapter(self.owner_adapter, "owner_adapter_required", ("restore",))

    def _lexical_reparse_preflight(self, value: Path | str, label: str) -> None:
        """Injected reparse seam must run before _safe_path resolves anything."""
        try:
            path = Path(value)
            if not path.is_absolute():
                raise InstallControlError(f"{label}_not_absolute")
            if self.security.is_reparse(path):
                raise InstallControlError("symlink_reparse_rejected")
        except InstallControlError:
            raise
        except Exception as exc:
            raise InstallControlError("security_preflight_failed") from exc

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

    def _security_preflight(
        self,
        source_root: Path,
        launcher: Path,
        installed_root: Path | None = None,
        expected_identity: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_security_adapter()
        try:
            if self.security.is_reparse(source_root) or self.security.is_reparse(launcher):
                raise InstallControlError("symlink_reparse_rejected")
            if installed_root is not None:
                if self.security.is_reparse(installed_root):
                    raise InstallControlError("symlink_reparse_rejected")
                installed_launcher = installed_root / "launcher.pyw"
                if os.path.lexists(installed_launcher) and self.security.is_reparse(installed_launcher):
                    raise InstallControlError("symlink_reparse_rejected")
            if not launcher.is_file():
                raise InstallControlError("launcher_missing")
            token = dict(expected_identity) if expected_identity is not None else self.security.capture_identity(launcher)
            if not self.security.verify_identity(launcher, token):
                raise InstallControlError("source_identity_swap")
            if not self.security.acl_is_secure(source_root) or not self.security.acl_is_secure(launcher):
                raise InstallControlError("insecure_acl")
            if installed_root is not None and not self.security.acl_is_secure(installed_root):
                raise InstallControlError("insecure_acl")
            if installed_root is not None and (installed_root / "launcher.pyw").exists() and not self.security.acl_is_secure(installed_root / "launcher.pyw"):
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
        except Exception as exc:
            raise InstallControlError("task_snapshot_failed") from exc
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("tasks"), Mapping):
            raise InstallControlError("task_snapshot_failed")
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
            "canaryReceipt": None,
            "canaryReceiptSha256": None,
            "observedAt": observed_at,
            "journalSha256": "",
        }

    @staticmethod
    def _read_source_once(path: Path) -> tuple[bytes, str, dict[str, Any]]:
        """同一file handleでidentity確認とbytes/hashを束ねて読む。"""
        try:
            before = os.lstat(path)
            if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
                raise InstallControlError("source_identity_swap")
            fd = os.open(path, os.O_RDONLY)
            try:
                opened = os.fstat(fd)
                identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                identity_opened = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                if identity_before != identity_opened:
                    raise InstallControlError("source_identity_swap")
                digest = hashlib.sha256()
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    digest.update(chunk)
                after = os.fstat(fd)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != identity_opened:
                    raise InstallControlError("source_identity_swap")
                data = b"".join(chunks)
                if len(data) != after.st_size:
                    raise InstallControlError("source_identity_swap")
                source_hash = digest.hexdigest()
                return data, source_hash, {"path": str(path), "sha256": source_hash}
            finally:
                os.close(fd)
        except InstallControlError:
            raise
        except (OSError, ValueError) as exc:
            raise InstallControlError("file_read_failed") from exc

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
        self._require_security_adapter()
        self._lexical_reparse_preflight(source_root, "source_root")
        self._lexical_reparse_preflight(installed_root, "installed_root")
        self._require_stage_adapters()
        source = _safe_path(source_root, "source_root")
        installed = _safe_path(installed_root, "installed_root")
        _within(source, self.runtime_root, "source_root")
        _within(installed, self.runtime_root, "installed_root")
        if not source.is_dir() or not installed.exists() or not installed.is_dir():
            raise InstallControlError("install_root_invalid")
        launcher = source / "launcher.pyw"
        source_data, source_hash_actual, source_identity = self._read_source_once(launcher)
        source_hash, preimages = self._validate_authority(authority, manifest, source, source_hash_actual)
        if source_hash_actual != source_hash:
            raise InstallControlError("source_hash_mismatch")
        installed_launcher = installed / "launcher.pyw"
        if installed_launcher.exists():
            if not installed_launcher.is_file() or _file_sha(installed_launcher) != source_hash:
                raise InstallControlError("installed_hash_mismatch")
        observed = _at_string(observed_at)
        journal = self._load_journal()
        candidate = self._candidate_definition(manifest)
        if journal is None:
            self._security_preflight(source, launcher, installed, source_identity)
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
            if journal["phase"] == "COMMITTED":
                return self._stage_result(authority, journal)
            if journal["phase"] == "ROLLED_BACK":
                raise InstallControlError("stage_after_rollback")

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
            self._copy_launcher(source, installed, source_hash, source_identity)
            journal["phase"] = "STAGED"
            self._write_journal(journal)
            self._hook("install")
        if not journal.get("candidateRegistered"):
            self.task_adapter.register_disabled(_NEW_TASK, candidate)
            journal["candidateRegistered"] = True
            journal["phase"] = "INSTALLED"
            self._write_journal(journal)
        canary_valid = self._validate_canary_receipt(journal, installed, required=False)
        if not canary_valid:
            try:
                receipt = self.process_adapter.launch(
                    self._canary_argv(installed),
                    installed,
                    shell=False,
                    creationflags=CREATE_NO_WINDOW,
                    encoding="utf-8",
                    includeChildTree=True,
                    noFocusTheft=True,
                    noAutoOpen=True,
                    noUserMonitoring=True,
                )
            except Exception as exc:
                raise InstallControlError("canary_failed") from exc
            if not isinstance(receipt, Mapping) or receipt.get("status") != "succeeded" or receipt.get("exitCode") != 0:
                raise InstallControlError("canary_failed")
            sealed = dict(journal)
            sealed["canaryReceipt"] = dict(receipt)
            sealed["canaryReceiptSha256"] = _sha(dict(receipt))
            self._validate_canary_receipt(sealed, installed, required=True)
            journal["canaryReceipt"] = dict(receipt)
            journal["canaryReceiptSha256"] = sealed["canaryReceiptSha256"]
            journal["canaryLaunched"] = True
            # Canary success itself is durable before pointer/cutover progress.
            self._write_journal(journal)
        if journal["phase"] in {"STAGED", "INSTALLED"}:
            self._hook("pointer")
            journal["phase"] = "POINTER_DURABLE"
            self._write_journal(journal)
        return self._stage_result(authority, journal)

    def cutover(self, authority: Mapping[str, Any], observed_at: datetime | str) -> dict[str, Any]:
        journal = self._load_journal()
        if journal is None:
            raise InstallControlError("stage_required")
        self._validate_resume(
            journal,
            authority,
            Path(journal["sourceRoot"]),
            Path(journal["installedRoot"]),
            journal["sourceSha256"],
            journal["candidateDefinition"],
            require_canary=True,
        )
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
        self._require_owner_adapter()
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
        if journal["phase"] == "ROLLBACK_OLD_RESTORED":
            snapshot = self._task_snapshot()
            if self._task_state(snapshot, _NEW_TASK):
                raise InstallControlError("rollback_new_not_disabled")
            if not self._task_state(snapshot, _OLD_TASK):
                self.task_adapter.enable(_OLD_TASK)
            snapshot = self._task_snapshot()
            if not self._task_state(snapshot, _OLD_TASK) or self._task_state(snapshot, _NEW_TASK):
                raise InstallControlError("rollback_old_only_not_confirmed")
            self._hook("rollback_commit")
            journal["phase"] = "ROLLED_BACK"
            journal["observedAt"] = _at_string(observed_at)
            self._write_journal(journal)
        return self._result("ROLLBACK_RESULT_V1", journal)

    def inspect(self) -> dict[str, Any]:
        journal = self._load_journal()
        phase = journal["phase"] if journal else "ABSENT"
        snapshot = self._task_snapshot()
        dual = int(self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK))
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
    def _canary_argv(self, installed: Path) -> list[str]:
        return [str(self.pythonw_path), str(installed / "launcher.pyw")]

    def _validate_canary_receipt(self, journal: Mapping[str, Any], installed: Path, *, required: bool) -> bool:
        """現在の installed bytes/argv に対する sealed canary evidence を検証する。"""
        canary = journal.get("canaryReceipt")
        if canary is None:
            if required:
                raise InstallControlError("canary_receipt_invalid")
            return False
        if not isinstance(canary, Mapping) or set(canary) != _CANARY_RECEIPT_KEYS:
            raise InstallControlError("canary_receipt_invalid")
        if canary.get("schemaVersion") != "NEWS_GRASP_INSTALL_CANARY_RECEIPT_V1" or canary.get("status") != "succeeded":
            raise InstallControlError("canary_receipt_invalid")
        exit_code = canary.get("exitCode")
        process_id = canary.get("processId")
        if isinstance(exit_code, bool) or exit_code != 0 or isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise InstallControlError("canary_receipt_invalid")
        installed_hash = _validate_hex(canary.get("installedSha256"), "canary_receipt_invalid")
        argv_hash = _validate_hex(canary.get("argvSha256"), "canary_receipt_invalid")
        receipt_hash = _validate_hex(canary.get("receiptSha256"), "canary_receipt_invalid")
        if receipt_hash != _sha({key: value for key, value in canary.items() if key != "receiptSha256"}):
            raise InstallControlError("canary_receipt_invalid")
        journal_hash = journal.get("canaryReceiptSha256")
        if not isinstance(journal_hash, str) or journal_hash != _sha(dict(canary)):
            raise InstallControlError("canary_receipt_invalid")
        launcher = installed / "launcher.pyw"
        if not launcher.is_file():
            raise InstallControlError("canary_receipt_invalid")
        try:
            current_hash = _file_sha(launcher)
        except InstallControlError as exc:
            raise InstallControlError("canary_receipt_invalid") from exc
        if current_hash != journal.get("installedSha256") or installed_hash != current_hash:
            raise InstallControlError("canary_receipt_invalid")
        if argv_hash != _sha(self._canary_argv(installed)):
            raise InstallControlError("canary_receipt_invalid")
        return True

    def _validate_resume(
        self,
        journal: Mapping[str, Any],
        authority: Mapping[str, Any],
        source: Path,
        installed: Path,
        source_hash: str,
        candidate: Mapping[str, Any],
        *,
        require_canary: bool = False,
    ) -> None:
        self._validate_journal_structure(journal)
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
        launcher = source / "launcher.pyw"
        if not source.is_dir() or not installed.is_dir():
            raise InstallControlError("resume_root_invalid")
        _source_data, current_source_hash, _current_identity = self._read_source_once(launcher)
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
        source_identity = journal["sourceIdentity"]
        if _safe_path(source_identity["path"], "journal_source_identity_path") != launcher:
            raise InstallControlError("journal_source_identity_path_mismatch")
        if source_identity["sha256"] != current_source_hash:
            raise InstallControlError("journal_source_identity_hash_mismatch")
        installed_identity = journal.get("installedIdentity")
        installed_launcher = installed / "launcher.pyw"
        if installed_identity is not None:
            if _safe_path(installed_identity["path"], "journal_installed_identity_path") != installed_launcher:
                raise InstallControlError("journal_installed_identity_path_mismatch")
            if not installed_launcher.is_file() or installed_identity["sha256"] != _file_sha(installed_launcher):
                raise InstallControlError("journal_installed_identity_hash_mismatch")
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
        if installed_launcher.is_file() and _file_sha(installed_launcher) != source_hash:
            raise InstallControlError("installed_hash_mismatch")
        self._security_preflight(source, launcher, installed, source_identity)
        self._validate_canary_receipt(journal, installed, required=require_canary)

    def _copy_launcher(
        self,
        source: Path,
        installed: Path,
        expected_hash: str,
        expected_identity: Mapping[str, Any],
    ) -> None:
        launcher = source / "launcher.pyw"
        target = installed / "launcher.pyw"
        installed.mkdir(parents=True, exist_ok=True)
        try:
            with _directory_pin(installed, "install_parent") as pin:
                pin.verify()
                self._hook("before_install_parent_swap")
                pin.verify()
                predictable = target.with_name(f".{target.name}.tmp")
                if os.path.lexists(predictable):
                    info = os.lstat(predictable)
                    if (
                        stat.S_ISLNK(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400
                        or info.st_nlink != 1
                    ):
                        raise InstallControlError("install_temp_link_rejected")
                if os.path.lexists(target):
                    target_info = os.lstat(target)
                    if stat.S_ISLNK(target_info.st_mode) or getattr(target_info, "st_file_attributes", 0) & 0x400:
                        raise InstallControlError("install_temp_link_rejected")
                pin.verify()
                data, source_hash, _current_identity = self._read_source_once(launcher)
                if source_hash != expected_hash or not self.security.verify_identity(launcher, expected_identity):
                    raise InstallControlError("source_identity_swap")
                pin.verify()
                fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(installed))
                temporary = Path(temporary_name)
                try:
                    pin.verify()
                    temp_info = os.lstat(temporary)
                    if (
                        stat.S_ISLNK(temp_info.st_mode)
                        or getattr(temp_info, "st_file_attributes", 0) & 0x400
                        or temp_info.st_nlink != 1
                    ):
                        raise InstallControlError("install_temp_link_rejected")
                    with os.fdopen(fd, "wb") as stream:
                        fd = -1
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    pin.verify()
                    os.replace(temporary, target)
                    pin.verify()
                    try:
                        directory_fd = os.open(installed, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    except OSError:
                        pass
                    pin.verify()
                finally:
                    if fd >= 0:
                        os.close(fd)
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
                pin.verify()
                if _file_sha(target) != expected_hash:
                    raise InstallControlError("installed_hash_mismatch")
                pin.verify()
        except InstallControlError:
            raise
        except (OSError, ValueError) as exc:
            raise InstallControlError("install_temp_failed") from exc

    def _stage_result(self, authority: Mapping[str, Any], journal: Mapping[str, Any]) -> dict[str, Any]:
        """検証済みauthorityとdurable journalからsealed stage evidenceを構成する。"""
        snapshot = self._task_snapshot()
        dual_enabled_count = int(self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK))
        installed_receipt = {
            "schemaVersion": "INSTALL_STAGED_RECEIPT_V1",
            "authorityId": authority["authorityId"],
            "generation": authority["generation"],
            "installedRoot": journal["installedRoot"],
            "sourceSha256": journal["sourceSha256"],
            "installedSha256": journal["installedSha256"],
            "ownerReceiptHashes": deepcopy(dict(journal["ownerReceiptHashes"])),
            "journalSha256": journal["journalSha256"],
        }
        installed_receipt["receiptSha256"] = _sha(installed_receipt)
        return {
            "schemaVersion": "INSTALL_STAGE_RESULT_V1",
            "phase": journal["phase"],
            "authorityId": authority["authorityId"],
            "generation": authority["generation"],
            "journalSha256": journal["journalSha256"],
            "journal": deepcopy(dict(journal)),
            "installedReceipt": installed_receipt,
            "dualEnabledCount": dual_enabled_count,
        }

    def _result(self, schema: str, journal: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._task_snapshot()
        dual_enabled_count = int(self._task_state(snapshot, _OLD_TASK) and self._task_state(snapshot, _NEW_TASK))
        return {
            "schemaVersion": schema,
            "phase": journal["phase"],
            "authorityId": journal["authorityId"],
            "journalSha256": journal["journalSha256"],
            "dualEnabledCount": dual_enabled_count,
        }


__all__ = ["InstallCutoverController", "InstallControlError", "PrivilegeDenied"]
