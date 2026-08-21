"""S5 clean-room install/cutover plane のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s5_cases.json"
CANONICAL_MANIFEST_PATH = Path(__file__).parents[1] / "config" / "news_grasp_cleanroom_task_manifest_v1.json"
CANONICAL_MANIFEST_SHA256 = "99888f219a8483e5d8c8698538444cf84d68fc36ab40e842962447982d86e641"
CANONICAL_MANIFEST_BYTES = 872
TOKYO = ZoneInfo("Asia/Tokyo")
CREATE_NO_WINDOW = 0x08000000
OLD_TASK = "News-Grasp Production (old)"
NEW_TASK = "News-Grasp Production"
PULL_TASK = "News-Grasp Pull"
INVENTORY_TASK = "News-Grasp Inventory"


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _at(minute: int = 0) -> datetime:
    return datetime(2026, 8, 21, 6, minute, tzinfo=TOKYO)


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S5_CASES_V1"
    assert value["packetId"] == "NG-CLEANROOM-S5-RED-V1"
    assert value["requiredManifestKeys"] == ["schemaVersion", "scheduleId", "tasks"]
    return value


def _manifest() -> dict[str, Any]:
    """Tracked canonical manifestをそのまま読み、driftをRed oracleにする。"""

    raw = CANONICAL_MANIFEST_PATH.read_bytes()
    assert len(raw) == CANONICAL_MANIFEST_BYTES
    assert hashlib.sha256(raw).hexdigest() == CANONICAL_MANIFEST_SHA256
    value = json.loads(raw.decode("utf-8"))
    assert set(value) == {"schemaVersion", "scheduleId", "tasks"}
    assert value["schemaVersion"] == "NEWS_GRASP_CONTROL_MANIFEST_V1"
    assert value["scheduleId"] == "news-grasp-daily-v1"
    assert len(value["tasks"]) == 1
    task = value["tasks"][0]
    assert set(task) == {
        "taskPath",
        "taskName",
        "multipleInstancesPolicy",
        "triggers",
        "action",
    }
    assert task["taskName"] == NEW_TASK
    assert set(task["action"]) == {"entryModule", "argv", "workingDirectoryToken"}
    assert task["action"]["entryModule"] == "tools.news_grasp_cleanroom_dispatch"
    assert task["action"]["workingDirectoryToken"] == "<RUNTIME_ROOT>"
    assert [trigger["triggerId"] for trigger in task["triggers"]] == ["scheduled-0600", "audit-0640"]
    return value


def _authority(source_root: Path, source_bytes: bytes) -> dict[str, Any]:
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    task_action_sha256 = _sha(_manifest()["tasks"][0]["action"])
    owner_specs = {
        "runtime_generation_owner": [
            "PRODUCTION_GENERATION_MANIFEST_V2",
            "NEWS_GRASP_ACTIVE_GENERATION_V2",
        ],
        "ops_install_owner": [
            "NEWS_GRASP_OPS_INSTALL_JOURNAL_V1",
            "NEWS_GRASP_PHYSICAL_DELIVERY_STATE_V1",
        ],
    }
    owner_receipts: dict[str, dict[str, Any]] = {}
    for owner_id, receipt_schemas in owner_specs.items():
        receipt = {
            "ownerId": owner_id,
            "receiptSchemas": receipt_schemas,
            "sourceSha256": source_sha256,
            "installedSha256": source_sha256,
            "taskActionSha256": task_action_sha256,
            "preimageSha256": _sha({"ownerId": owner_id, "preimage": "test-preimage-v1"}),
        }
        receipt["receiptSha256"] = _sha(receipt)
        owner_receipts[owner_id] = receipt
    value = {
        "schemaVersion": "INSTALL_AUTHORITY_V1",
        "authorityId": "s5-authority-20260821",
        "issueDate": "2026-08-21",
        "generation": 1,
        "sourceRoot": str(source_root),
        "sourceSha256": source_sha256,
        "ownerReceipts": owner_receipts,
    }
    value["authoritySha256"] = _sha(value)
    return value


def _reseal_authority(authority: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in authority.items() if key != "authoritySha256"}
    authority["authoritySha256"] = _sha(body)
    return authority


def _roots(tmp_path: Path, index: int) -> tuple[Path, Path, bytes, dict[str, Any], dict[str, Any]]:
    root = tmp_path / f"日本語-導入面-{index}"
    source = root / "source"
    installed = root / "installed"
    source.mkdir(parents=True)
    installed.mkdir(parents=True)
    content = f"# S5 deterministic launcher {index}\nprint('safe')\n".encode("utf-8")
    (source / "launcher.pyw").write_bytes(content)
    # The corrected constructor receives a concrete executable path.  It is a test
    # sentinel only; no subprocess or OS mutation is allowed by this suite.
    (root / "pythonw.exe").write_bytes(b"test-pythonw-sentinel")
    return root, source, content, _manifest(), _authority(source, content)


class TaskAdapter:
    """Windowsを触らずTask XMLとenabled stateだけを記録する公開protocol substitute。"""

    def __init__(self, module: Any, *, old_enabled: bool = True, new_enabled: bool = False) -> None:
        self.module = module
        self._enabled = {
            OLD_TASK: old_enabled,
            NEW_TASK: new_enabled,
            PULL_TASK: False,
            INVENTORY_TASK: False,
        }
        self._definitions: dict[str, Any] = {
            OLD_TASK: "<old-task-v1/>",
            NEW_TASK: "<candidate-task-v1/>",
        }
        self._registered: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._initial: list[dict[str, Any]] = []
        self._mutations: list[dict[str, bool]] = []
        self._deny_operation: str | None = None
        self._observe_initial("constructor")

    def _check_denial(self, operation: str) -> None:
        if self._deny_operation == operation:
            raise self.module.PrivilegeDenied(f"test-owned privilege denial: {operation}")

    def _observe_initial(self, source: str) -> None:
        self._initial.append({"source": source, "enabled": dict(self._enabled)})

    def _record_mutation(self, operation: str, name: str, **detail: Any) -> None:
        self._history.append({"operation": operation, "name": name, **deepcopy(detail)})
        self._mutations.append(dict(self._enabled))

    def snapshot(self) -> dict[str, Any]:
        self._check_denial("snapshot")
        self._observe_initial("snapshot")
        return {
            "tasks": {
                name: {
                    "enabled": enabled,
                    "definition": deepcopy(self._definitions.get(name)),
                }
                for name, enabled in self._enabled.items()
            }
        }

    def register_disabled(self, name: str, definition: Mapping[str, Any], **kwargs: Any) -> None:
        self._check_denial("register_disabled")
        if not isinstance(definition, Mapping):
            raise TypeError("TASK_DEFINITION_V1 must be a structured mapping")
        registered = deepcopy(dict(definition))
        if kwargs:
            registered.update(deepcopy(kwargs))
        self._registered[name] = registered
        self._definitions[name] = deepcopy(registered)
        self._enabled[name] = False
        self._record_mutation("register_disabled", name, definition=registered)

    def disable(self, name: str) -> None:
        self._check_denial("disable")
        self._enabled[name] = False
        self._record_mutation("disable", name)

    def enable(self, name: str) -> None:
        self._check_denial("enable")
        self._enabled[name] = True
        self._record_mutation("enable", name)

    def restore(self, name: str, definition: Any) -> None:
        self._check_denial("restore")
        self._definitions[name] = deepcopy(definition)
        self._enabled[name] = False
        self._record_mutation("restore", name, definition=definition)

    def remove_candidate(self, name: str) -> None:
        self._check_denial("remove_candidate")
        self._registered.pop(name, None)
        self._enabled[name] = False
        self._record_mutation("remove_candidate", name)

    def set_denial(self, operation: str | None) -> None:
        self._deny_operation = operation

    def initial_observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._initial)

    def mutation_snapshots(self) -> list[dict[str, bool]]:
        return deepcopy(self._mutations)

    def history_rows(self) -> list[dict[str, Any]]:
        return deepcopy(self._history)

    def task_state(self) -> dict[str, bool]:
        return dict(self._enabled)

    def task_definition(self, name: str) -> Any:
        return deepcopy(self._definitions[name])

    def registered_definition(self, name: str) -> dict[str, Any]:
        return deepcopy(self._registered[name])

    @property
    def dual_enabled_count(self) -> int:
        return int(self._enabled.get(OLD_TASK, False) and self._enabled.get(NEW_TASK, False))


class ProcessAdapter:
    """HumanImpactContractV1のlaunch観測だけを公開する。"""

    def __init__(self) -> None:
        self._launches: list[dict[str, Any]] = []
        self._kills: list[Any] = []

    def launch(self, argv: list[str], cwd: Path, **kwargs: Any) -> dict[str, Any]:
        self._launches.append({"argv": list(argv), "cwd": str(cwd), **deepcopy(kwargs)})
        receipt = {
            "schemaVersion": "NEWS_GRASP_INSTALL_CANARY_RECEIPT_V1",
            "status": "succeeded",
            "exitCode": 0,
            "processId": 9001,
            "installedSha256": hashlib.sha256(Path(argv[1]).read_bytes()).hexdigest(),
            "argvSha256": _sha(argv),
        }
        receipt["receiptSha256"] = _sha(receipt)
        return receipt

    def kill(self, process: Any) -> None:
        self._kills.append(process)
        raise AssertionError("shared-process kill is forbidden")

    def launch_observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._launches)

    def kill_observations(self) -> list[Any]:
        return deepcopy(self._kills)


class OwnerAdapter:
    """二つのowner transactionのrestore順とpreimageを記録する。"""

    def __init__(self, module: Any, task: TaskAdapter) -> None:
        self.module = module
        self.task = task
        self.deny_owner: str | None = None
        self._restores: list[dict[str, str]] = []

    def restore(self, owner_id: str, preimage_sha256: str) -> None:
        if self.task.task_state()[NEW_TASK]:
            raise AssertionError("owner restore before new task disable")
        if owner_id == self.deny_owner:
            self.deny_owner = None
            raise self.module.PrivilegeDenied(f"test-owned owner denial: {owner_id}")
        self._restores.append(
            {"ownerId": owner_id, "preimageSha256": preimage_sha256}
        )

    def restores(self) -> list[dict[str, str]]:
        return deepcopy(self._restores)


class SecurityAdapter:
    """公開security protocolの因果注入を記録する。"""

    def __init__(self, module: Any) -> None:
        self.module = module
        self._injections: set[str] = set()
        self._observations: list[dict[str, Any]] = []

    def inject(self, case: str) -> None:
        self._injections.add(case)

    def capture_identity(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        payload = path.read_bytes() if path.is_file() else str(path).encode("utf-8")
        token = {"path": str(path), "sha256": hashlib.sha256(payload).hexdigest()}
        self._observations.append({"operation": "capture_identity", "path": str(path)})
        return token

    def verify_identity(self, path: Path, token: Mapping[str, Any]) -> bool:
        self._observations.append({"operation": "verify_identity", "path": str(path)})
        return "source_identity_swap" not in self._injections

    def is_reparse(self, path: Path) -> bool:
        self._observations.append({"operation": "is_reparse", "path": str(path)})
        return "symlink_reparse" in self._injections

    def acl_is_secure(self, path: Path) -> bool:
        self._observations.append({"operation": "acl_is_secure", "path": str(path)})
        return "insecure_acl" not in self._injections

    def signer_is_trusted(self, executable: Path) -> bool:
        self._observations.append({"operation": "signer_is_trusted", "path": str(executable)})
        return "untrusted_signer" not in self._injections

    def observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._observations)


_DEFAULT_SECURITY = object()
_DEFAULT_PROCESS = object()


def _controller(
    module: Any,
    root: Path,
    task: TaskAdapter,
    *,
    process: ProcessAdapter | None | object = _DEFAULT_PROCESS,
    security: SecurityAdapter | None | object = _DEFAULT_SECURITY,
    owner_adapter: OwnerAdapter | None = None,
    boundary_hook: Any = None,
) -> Any:
    if security is _DEFAULT_SECURITY:
        security = SecurityAdapter(module)
    if process is _DEFAULT_PROCESS:
        process = ProcessAdapter()
    return module.InstallCutoverController(
        root,
        task_adapter=task,
        pythonw_path=root / "pythonw.exe",
        process_adapter=process,
        security_adapter=security,
        owner_adapter=owner_adapter,
        boundary_hook=boundary_hook,
    )


def _stage(controller: Any, manifest: dict[str, Any], source: Path, installed: Path, authority: dict[str, Any]) -> Any:
    return controller.stage(manifest, source, installed, authority, _at())


def _cutover(controller: Any, authority: dict[str, Any]) -> Any:
    return controller.cutover(authority, _at(1))


def _rollback(controller: Any, authority: dict[str, Any], minute: int = 2) -> Any:
    return controller.rollback(authority, _at(minute))


def _assert_stage_safe(task: TaskAdapter) -> None:
    state = task.task_state()
    assert state[OLD_TASK] is True
    assert state[NEW_TASK] is False
    assert task.dual_enabled_count == 0


def _assert_installed(installed: Path, content: bytes) -> None:
    launcher = installed / "launcher.pyw"
    assert launcher.read_bytes() == content
    assert hashlib.sha256(launcher.read_bytes()).hexdigest() == hashlib.sha256(content).hexdigest()


def _inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _outside_sentinel(tmp_path: Path, index: int) -> Path:
    outside = tmp_path / f"外部-センチネル-{index}"
    (outside / "nested").mkdir(parents=True)
    (outside / "sentinel.txt").write_text("must-not-change\n", encoding="utf-8")
    (outside / "nested" / "data.bin").write_bytes(b"outside-data")
    return outside


def _mk_junction(link: Path, target: Path) -> None:
    """実Windows junctionを、shellなし・黒窓なしで作るtest-owned seam。"""

    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        shell=False,
        check=True,
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )


def _remove_junction(link: Path) -> None:
    """junction自身だけをrmdirし、リンク先treeを削除しない。"""

    subprocess.run(
        ["cmd.exe", "/d", "/c", "rmdir", str(link)],
        shell=False,
        check=True,
        creationflags=CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )


def _mutate_manifest(manifest: dict[str, Any], case: str) -> dict[str, Any]:
    mutated = deepcopy(manifest)
    task = mutated["tasks"][0]
    action = task["action"]
    if case == "manifest_unknown_key":
        mutated["unexpected"] = "value"
    elif case == "shell_metacharacter":
        action["argv"].append("& whoami")
    elif case == "control_byte":
        action["argv"].append("bad\narg")
    elif case == "secret_argv":
        action["argv"].append("TOKEN=super-secret")
    elif case == "secret_diagnostic":
        mutated["diagnostic"] = "apiToken=super-secret"
    elif case == "action_unknown_key":
        action["unexpected"] = "value"
    elif case == "action_missing_key":
        action.pop("entryModule")
    elif case == "trigger_unknown_key":
        task["triggers"][0]["unexpected"] = "value"
    elif case == "trigger_missing_key":
        task["triggers"][0].pop("triggerId")
    else:
        raise AssertionError(f"unhandled S5 negative case: {case}")
    return mutated


def test_s5_cutover_crash_matrix(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    for index, boundary in enumerate(cases["cutoverBoundaries"], start=1):
        root, source, content, manifest, authority = _roots(tmp_path, index)
        installed = root / "installed"
        task = TaskAdapter(module)
        crashed = {"active": True}

        def hook(name: str, expected=boundary) -> None:
            if crashed["active"] and name == expected:
                crashed["active"] = False
                raise RuntimeError(f"test-owned cutover crash: {name}")

        controller = _controller(module, root, task, boundary_hook=hook)
        if boundary in {"stage", "install", "pointer"}:
            with pytest.raises(RuntimeError, match=f"^test-owned cutover crash: {boundary}$"):
                _stage(controller, manifest, source, installed, authority)
            assert crashed["active"] is False
            _assert_stage_safe(task)
            _stage(controller, manifest, source, installed, authority)
            _assert_stage_safe(task)
            _assert_installed(installed, content)
            assert sum(row["operation"] == "register_disabled" for row in task.history_rows()) == 1
            assert sum(row["operation"] == "disable" and row["name"] == OLD_TASK for row in task.history_rows()) == 0
            assert sum(row["operation"] == "enable" and row["name"] == NEW_TASK for row in task.history_rows()) == 0
            _cutover(controller, authority)
        else:
            _stage(controller, manifest, source, installed, authority)
            _assert_stage_safe(task)
            with pytest.raises(RuntimeError, match=f"^test-owned cutover crash: {boundary}$"):
                _cutover(controller, authority)
            assert crashed["active"] is False
            assert task.dual_enabled_count == 0
            state = task.task_state()
            assert not (state[OLD_TASK] and state[NEW_TASK])
            _cutover(controller, authority)
        inspection = controller.inspect()
        assert task.task_state()[OLD_TASK] is False
        assert task.task_state()[NEW_TASK] is True
        assert task.dual_enabled_count == 0
        assert inspection["dualEnabledCount"] == 0
        assert _inventory(installed)["launcher.pyw"]["sha256"] == hashlib.sha256(content).hexdigest()
        assert sum(row["operation"] == "register_disabled" for row in task.history_rows()) == 1
        assert sum(row["operation"] == "disable" and row["name"] == OLD_TASK for row in task.history_rows()) == 1
        assert sum(row["operation"] == "enable" and row["name"] == NEW_TASK for row in task.history_rows()) == 1


def test_s5_dual_enabled_invariant(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    states = (("old_only", True, False), ("new_only", False, True), ("both", True, True), ("both_disabled", False, False))
    for index, (state_name, old_enabled, new_enabled) in enumerate(states, start=20):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        task = TaskAdapter(module, old_enabled=old_enabled, new_enabled=new_enabled)
        controller = _controller(module, root, task)
        _stage(controller, manifest, source, root / "installed", authority)
        assert task.task_state()[OLD_TASK] is True, state_name
        assert task.task_state()[NEW_TASK] is False, state_name
        assert task.dual_enabled_count == 0, state_name
        rows = task.history_rows()
        mutation_names = [row for row in rows if row["operation"] != "snapshot"]
        if state_name in {"new_only", "both"}:
            assert mutation_names[0]["operation"] == "disable", state_name
            assert mutation_names[0]["name"] == NEW_TASK, state_name
        assert all(not snapshot[OLD_TASK] or not snapshot[NEW_TASK] for snapshot in task.mutation_snapshots())
        assert task.initial_observations()
        _cutover(controller, authority)
        assert task.task_state()[OLD_TASK] is False
        assert task.task_state()[NEW_TASK] is True
        assert task.dual_enabled_count == 0
        assert task.task_state()[PULL_TASK] is False and task.task_state()[INVENTORY_TASK] is False
        assert not any(
            row["operation"] == "remove_candidate" and row["name"] in {PULL_TASK, INVENTORY_TASK}
            for row in task.history_rows()
        )
        inspection = controller.inspect()
        assert inspection["dualEnabledCount"] == 0
        assert inspection["autoDeletedPullTaskCount"] == 0
    assert cases["humanImpact"]["noSharedProcessKill"] is True


def test_s5_uac_and_rollback_recovery(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    for index, denied_operation in enumerate(("disable", "enable"), start=40):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        task = TaskAdapter(module)
        owner = OwnerAdapter(module, task)
        controller = _controller(module, root, task, owner_adapter=owner)
        _stage(controller, manifest, source, root / "installed", authority)
        task.set_denial(denied_operation)
        with pytest.raises((module.PrivilegeDenied, module.InstallControlError)):
            _cutover(controller, authority)
        assert task.dual_enabled_count == 0
        assert not (task.task_state()[OLD_TASK] and task.task_state()[NEW_TASK])
        task.set_denial(None)
        _cutover(controller, authority)
        assert task.task_state()[OLD_TASK] is False and task.task_state()[NEW_TASK] is True

    for index, boundary in enumerate(cases["rollbackBoundaries"], start=50):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        task = TaskAdapter(module)
        old_preimage = task.task_definition(OLD_TASK)
        old_preimage_hash = hashlib.sha256(_bytes(old_preimage)).hexdigest()
        owner = OwnerAdapter(module, task)
        controller = _controller(module, root, task, owner_adapter=owner)
        _stage(controller, manifest, source, root / "installed", authority)
        _cutover(controller, authority)
        crashed = {"active": True}

        def hook(name: str, expected=boundary) -> None:
            if crashed["active"] and name == expected:
                crashed["active"] = False
                raise RuntimeError(f"test-owned rollback crash: {name}")

        rollback_controller = _controller(
            module,
            root,
            task,
            owner_adapter=owner,
            boundary_hook=hook,
        )
        with pytest.raises(RuntimeError, match=f"^test-owned rollback crash: {boundary}$"):
            _rollback(rollback_controller, authority)
        assert crashed["active"] is False
        assert task.dual_enabled_count == 0
        assert not (task.task_state()[OLD_TASK] and task.task_state()[NEW_TASK])
        _rollback(rollback_controller, authority, minute=3)
        assert task.task_state()[OLD_TASK] is True and task.task_state()[NEW_TASK] is False
        restored = task.task_definition(OLD_TASK)
        assert _bytes(restored) == _bytes(old_preimage)
        assert hashlib.sha256(_bytes(restored)).hexdigest() == old_preimage_hash
        rows = task.history_rows()
        assert max(i for i, row in enumerate(rows) if row["operation"] == "disable" and row["name"] == NEW_TASK) < max(
            i for i, row in enumerate(rows) if row["operation"] == "restore" and row["name"] == OLD_TASK
        )
        assert all(not snapshot[OLD_TASK] or not snapshot[NEW_TASK] for snapshot in task.mutation_snapshots())


def test_s5_path_security_matrix(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    security_injections = {
        "symlink_reparse": "symlink_reparse",
        "source_identity_swap": "source_identity_swap",
        "insecure_acl": "insecure_acl",
        "invalid_signer": "untrusted_signer",
    }
    expected_security_calls = {
        "symlink_reparse": "is_reparse",
        "source_identity_swap": "verify_identity",
        "insecure_acl": "acl_is_secure",
        "invalid_signer": "signer_is_trusted",
    }
    for index, path_case in enumerate(cases["pathCases"], start=70):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        outside = _outside_sentinel(tmp_path, index)
        outside_before = _inventory(outside)
        mutated = deepcopy(manifest)
        if path_case == "traversal":
            mutated["tasks"][0]["action"]["workingDirectoryToken"] = "../escape"
        elif path_case == "absolute_escape":
            mutated["tasks"][0]["action"]["workingDirectoryToken"] = str(outside)
        elif path_case == "hash_mismatch":
            authority["sourceSha256"] = "0" * 64
            authority["authoritySha256"] = _sha({key: value for key, value in authority.items() if key != "authoritySha256"})
        security = SecurityAdapter(module)
        if path_case in security_injections:
            security.inject(security_injections[path_case])
        task = TaskAdapter(module)
        controller = _controller(module, root, task, security=security)
        with pytest.raises(module.InstallControlError):
            _stage(controller, mutated, source, root / "installed", authority)
        assert task.history_rows() == []
        assert _inventory(outside) == outside_before
        assert not _inventory(root / "installed")
        if path_case in expected_security_calls:
            assert any(row["operation"] == expected_security_calls[path_case] for row in security.observations())


def test_s5_secret_and_argument_injection(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    root, source, _content, manifest, authority = _roots(tmp_path, 90)
    task = TaskAdapter(module)
    valid_controller = _controller(module, root, task)
    _stage(valid_controller, manifest, source, root / "installed", authority)
    task_spec = manifest["tasks"][0]
    action = task_spec["action"]
    registered = task.registered_definition(NEW_TASK)
    expected = {
        "taskPath": task_spec["taskPath"],
        "taskName": task_spec["taskName"],
        "enabled": False,
        "executable": str(root / "pythonw.exe"),
        "arguments": list(action["argv"]),
        "workingDirectory": str(root),
        "triggers": deepcopy(task_spec["triggers"]),
        "multipleInstancesPolicy": task_spec["multipleInstancesPolicy"],
    }
    assert set(registered) == set(expected)
    assert registered == expected
    assert action["entryModule"] == "tools.news_grasp_cleanroom_dispatch"
    assert action["workingDirectoryToken"] == "<RUNTIME_ROOT>"
    assert isinstance(registered["arguments"], list)
    assert not any(isinstance(value, str) and "&&" in value for value in registered.values())

    negative_cases = [*cases["secretCases"], *cases["manifestNestedCases"]]
    for index, negative_case in enumerate(negative_cases, start=91):
        case_root, case_source, _case_content, case_manifest, case_authority = _roots(tmp_path, index)
        mutated = _mutate_manifest(case_manifest, negative_case)
        case_task = TaskAdapter(module)
        case_controller = _controller(module, case_root, case_task)
        with pytest.raises(module.InstallControlError) as caught:
            _stage(case_controller, mutated, case_source, case_root / "installed", case_authority)
        assert case_task.history_rows() == []
        assert not _inventory(case_root / "installed")
        assert "super-secret" not in str(caught.value)
        assert "super-secret" not in json.dumps(case_controller.inspect(), ensure_ascii=False)


def test_s5_child_tree_human_impact(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    root, source, _content, manifest, authority = _roots(tmp_path, 100)
    process = ProcessAdapter()
    task = TaskAdapter(module)
    controller = _controller(module, root, task, process=process)
    _stage(controller, manifest, source, root / "installed", authority)
    _cutover(controller, authority)
    launches = process.launch_observations()
    assert launches
    for launch in launches:
        assert launch["shell"] is False
        assert launch["creationflags"] & CREATE_NO_WINDOW
        assert "日本語" in launch["cwd"]
        assert launch["encoding"] == "utf-8"
        assert launch["includeChildTree"] is True
        assert launch["noFocusTheft"] is True
        assert launch["noAutoOpen"] is True
        assert launch["noUserMonitoring"] is True
    assert process.kill_observations() == []
    inspection = controller.inspect()
    assert inspection["focusTheftCount"] == 0
    assert inspection["sharedProcessKillCount"] == 0
    assert cases["humanImpact"] == {
        "shell": False,
        "CREATE_NO_WINDOW": CREATE_NO_WINDOW,
        "noFocusTheft": True,
        "noAutoOpen": True,
        "noUserMonitoring": True,
        "noSharedProcessKill": True,
    }


def test_s5_preflight_rejects_source_install_binding_task_action_drift_before_mutation(
    tmp_path: Path,
) -> None:
    """source/install/binding/Task actionのdriftは最初のTask mutation前に拒否する。"""
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    parallel = json.loads(
        (Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_parallel_hotfix_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert parallel["driftCases"] == [
        "source_sha256", "installed_sha256", "binding_receipt_sha256", "task_action_sha256"
    ]
    cases = _cases()
    for index, drift in enumerate(parallel["driftCases"], start=120):
        root, source, content, manifest, authority = _roots(tmp_path, index)
        installed = root / "installed"
        if drift == "source_sha256":
            source.joinpath("launcher.pyw").write_bytes(content + b"drift")
        elif drift == "installed_sha256":
            installed.mkdir(parents=True, exist_ok=True)
            installed.joinpath("launcher.pyw").write_bytes(b"installed-drift")
        elif drift == "binding_receipt_sha256":
            authority["ownerReceipts"]["runtime_generation_owner"]["receiptSha256"] = "0" * 64
            _reseal_authority(authority)
        else:
            for receipt in authority["ownerReceipts"].values():
                receipt["taskActionSha256"] = "f" * 64
                receipt["receiptSha256"] = _sha(
                    {key: value for key, value in receipt.items() if key != "receiptSha256"}
                )
            _reseal_authority(authority)
        task = TaskAdapter(module)
        controller = _controller(module, root, task)
        with pytest.raises(module.InstallControlError):
            _stage(controller, manifest, source, installed, authority)
        assert task.history_rows() == [], drift
    assert cases["lineage"] == "InstallCutover"


def test_s5_requires_both_owner_receipts_before_candidate_registration(tmp_path: Path) -> None:
    """runtime-generationとops-installの両owner receiptがcandidate登録を先行する。"""
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 140)
    authority["ownerReceipts"].pop("ops_install_owner")
    _reseal_authority(authority)
    task = TaskAdapter(module)
    controller = _controller(module, root, task)
    with pytest.raises(module.InstallControlError):
        _stage(controller, manifest, source, root / "installed", authority)
    assert task.history_rows() == []


def test_s5_two_owner_crash_and_privilege_failure_restore_exact_preimages(tmp_path: Path) -> None:
    """owner transaction crash/privilege failureはTaskとruntime preimageを順序付き復元する。"""
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    parallel = json.loads(
        (Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_parallel_hotfix_cases.json").read_text(
            encoding="utf-8"
        )
    )
    assert parallel["ownerIds"] == ["runtime_generation_owner", "ops_install_owner"]
    assert parallel["rollbackOrder"] == ["install_news_grasp_ops", "launcher_runtime_lifecycle"]
    root, source, content, manifest, authority = _roots(tmp_path, 160)
    task = TaskAdapter(module)
    owner = OwnerAdapter(module, task)
    controller = _controller(module, root, task, owner_adapter=owner)
    _stage(controller, manifest, source, root / "installed", authority)
    _cutover(controller, authority)
    owner.deny_owner = "install_news_grasp_ops"
    with pytest.raises((module.PrivilegeDenied, module.InstallControlError)):
        _rollback(controller, authority)
    _rollback(controller, authority)
    restores = owner.restores()
    assert [row["ownerId"] for row in restores] == [
        "install_news_grasp_ops", "launcher_runtime_lifecycle"
    ]
    assert [row["preimageSha256"] for row in restores] == [
        authority["ownerReceipts"][owner_id]["preimageSha256"]
        for owner_id in ["ops_install_owner", "runtime_generation_owner"]
    ]
    assert (root / "installed" / "launcher.pyw").read_bytes() == content
    assert task.dual_enabled_count == 0


def test_sec_s5_reparse_is_checked_before_path_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """reparse/junction拒否はresolve(strict=False)より前のlexical boundaryで行う。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 180)
    installed = root / "installed"
    task = TaskAdapter(module)
    security = SecurityAdapter(module)
    security.inject("symlink_reparse")
    events: list[tuple[str, str]] = []
    original_safe_path = module._safe_path

    def tracked_safe_path(value: Any, label: str) -> Path:
        events.append(("resolve", label))
        return original_safe_path(value, label)

    original_is_reparse = security.is_reparse

    def tracked_is_reparse(path: Path) -> bool:
        events.append(("reparse", str(path)))
        return original_is_reparse(path)

    security.is_reparse = tracked_is_reparse
    monkeypatch.setattr(module, "_safe_path", tracked_safe_path)
    controller = _controller(module, root, task, security=security)
    events.clear()
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source / "." / ".." / source.name, installed, authority)
    assert captured.value.reason == "symlink_reparse_rejected"
    assert events and events[0][0] == "reparse"
    assert task.history_rows() == []


def test_sec_s5_default_security_cannot_fail_open_acl_or_signature(tmp_path: Path) -> None:
    """OS ACL/署名を測れないdefault securityは導入を継続しない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 181)
    task = TaskAdapter(module)
    process = ProcessAdapter()
    owner = OwnerAdapter(module, task)
    controller = _controller(
        module,
        root,
        task,
        process=process,
        owner_adapter=owner,
        security=None,
    )
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source, root / "installed", authority)
    assert captured.value.reason == "security_adapter_required"
    assert task.history_rows() == []


def test_sec_s5_source_swap_after_authority_capture_is_refused_before_copy(tmp_path: Path) -> None:
    """authority capture後copy前のsource差替えはinstalledへ流さない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, content, manifest, authority = _roots(tmp_path, 182)
    installed = root / "installed"
    task = TaskAdapter(module)
    security = SecurityAdapter(module)
    process = ProcessAdapter()

    def swap_after_capture(name: str) -> None:
        if name == "stage":
            (source / "launcher.pyw").write_bytes(content + b"\n# swapped after capture\n")

    controller = _controller(
        module,
        root,
        task,
        process=process,
        security=security,
        owner_adapter=OwnerAdapter(module, task),
        boundary_hook=swap_after_capture,
    )
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source, installed, authority)
    assert captured.value.reason == "source_identity_swap"
    assert not (installed / "launcher.pyw").exists()


def test_sec_s5_preseeded_temp_hardlink_cannot_mutate_victim(tmp_path: Path) -> None:
    """copy tempがhardlinkならvictimを変更せずtyped拒否する。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 183)
    installed = root / "installed"
    victim = root / "victim.bin"
    victim.write_bytes(b"victim-before")
    temporary = installed / ".launcher.pyw.tmp"
    os.link(victim, temporary)
    before = victim.read_bytes()
    task = TaskAdapter(module)
    controller = _controller(
        module,
        root,
        task,
        process=ProcessAdapter(),
        security=SecurityAdapter(module),
        owner_adapter=OwnerAdapter(module, task),
    )
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source, installed, authority)
    assert captured.value.reason == "install_temp_link_rejected"
    assert victim.read_bytes() == before


def test_sec_s5_process_adapter_is_required_for_canary(tmp_path: Path) -> None:
    """process adapter欠落でcanaryを黙って省略しない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 184)
    task = TaskAdapter(module)
    controller = _controller(
        module,
        root,
        task,
        process=None,
        security=SecurityAdapter(module),
        owner_adapter=OwnerAdapter(module, task),
    )
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source, root / "installed", authority)
    assert captured.value.reason == "process_adapter_required"
    assert task.history_rows() == []


class _FailingProcessAdapter(ProcessAdapter):
    def launch(self, argv: list[str], cwd: Path, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("test-owned canary failure")


def test_sec_s5_canary_failure_is_typed_and_durable(tmp_path: Path) -> None:
    """canary launch failureはraw exceptionを漏らさずcanary_failedへ束ねる。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 185)
    task = TaskAdapter(module)
    controller = _controller(
        module,
        root,
        task,
        process=_FailingProcessAdapter(),
        security=SecurityAdapter(module),
        owner_adapter=OwnerAdapter(module, task),
    )
    with pytest.raises(module.InstallControlError) as captured:
        _stage(controller, manifest, source, root / "installed", authority)
    assert captured.value.reason == "canary_failed"


def test_sec_s5_owner_adapter_is_required_for_rollback(tmp_path: Path) -> None:
    """rollbackはowner adapter欠落時にruntime復元済みと偽装しない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 186)
    task = TaskAdapter(module)
    controller = _controller(
        module,
        root,
        task,
        process=ProcessAdapter(),
        security=SecurityAdapter(module),
        owner_adapter=None,
    )
    _stage(controller, manifest, source, root / "installed", authority)
    _cutover(controller, authority)
    with pytest.raises(module.InstallControlError) as captured:
        _rollback(controller, authority)
    assert captured.value.reason == "owner_adapter_required"


def test_sec_s5_snapshot_exception_is_not_reported_as_dual_zero(tmp_path: Path) -> None:
    """Task snapshot例外はdual=0へ偽装せずtyped失敗として残す。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 187)
    task = TaskAdapter(module)
    controller = _controller(
        module,
        root,
        task,
        process=ProcessAdapter(),
        security=SecurityAdapter(module),
        owner_adapter=OwnerAdapter(module, task),
    )
    _stage(controller, manifest, source, root / "installed", authority)
    task.set_denial("snapshot")
    with pytest.raises(module.InstallControlError) as captured:
        controller.inspect()
    assert captured.value.reason == "task_snapshot_failed"


def test_sec_s5_stage_cutover_rollback_dual_count_is_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    """stage/cutover/rollback resultはdualEnabledCountのliteral 0を返さない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    stage_source = inspect.getsource(module.InstallCutoverController._stage_result)
    result_source = inspect.getsource(module.InstallCutoverController._result)
    assert '"dualEnabledCount": 0' not in stage_source
    assert '"dualEnabledCount": 0' not in result_source


class _ParentSwapRejected(RuntimeError):
    """test-owned signal for an OS-level no-delete-share rename rejection."""


def _swap_parent_to_junction(parent: Path, outside: Path, backup: Path, state: dict[str, Any]) -> None:
    """parent rename→junction差替えを試み、OS拒否だけをtest-private signalにする。"""

    state["called"] = True
    try:
        os.replace(parent, backup)
    except OSError as exc:
        state["swap"] = "rejected"
        raise _ParentSwapRejected from exc
    state["swap"] = "succeeded"
    _mk_junction(parent, outside)


def test_sec_s5_installer_parent_pin_rejects_junction_swap_before_task_mutation(tmp_path: Path) -> None:
    """_copy_launcherのdestination parentはdelete-shareなしhandleでpinする。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    source_text = Path(module.__file__).read_text(encoding="utf-8")
    assert "CreateFileW" in source_text
    assert "FILE_FLAG_BACKUP_SEMANTICS" in source_text
    assert "FILE_FLAG_OPEN_REPARSE_POINT" in source_text
    assert not re.search(r"FILE_SHARE_READ\s*\|\s*FILE_SHARE_WRITE\s*\|\s*FILE_SHARE_DELETE", source_text)

    root, source, _content, manifest, authority = _roots(tmp_path, 190)
    installed = root / "installed"
    outside = _outside_sentinel(tmp_path, 190)
    outside_before = _inventory(outside)
    backup = root / "installed-real"
    state: dict[str, Any] = {"called": False, "swap": "not-attempted"}
    task = TaskAdapter(module)

    def hook(name: str) -> None:
        if name == "before_install_parent_swap" and not state["called"]:
            _swap_parent_to_junction(installed, outside, backup, state)

    controller = _controller(module, root, task, boundary_hook=hook)
    try:
        try:
            _stage(controller, manifest, source, installed, authority)
        except _ParentSwapRejected:
            assert state["swap"] == "rejected"
        except module.InstallControlError as captured:
            assert state["swap"] == "succeeded"
            assert captured.reason == "install_parent_identity_swap"
        else:
            pytest.fail("installer accepted the parent junction swap")
    finally:
        if installed.is_symlink():
            _remove_junction(installed)
        if backup.exists() and not installed.exists():
            os.replace(backup, installed)
    assert state["called"] is True
    assert state["swap"] in {"rejected", "succeeded"}
    assert task.history_rows() == []
    assert _inventory(outside) == outside_before


def test_sec_s5_journal_parent_pin_rejects_junction_swap_before_task_mutation(tmp_path: Path) -> None:
    """journal parentも同一のno-delete-share pin境界を通る。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 191)
    journal_parent = root / "control" / "install-cutover-v1"
    journal_parent.mkdir(parents=True)
    outside = _outside_sentinel(tmp_path, 191)
    outside_before = _inventory(outside)
    backup = root / "journal-parent-real"
    state: dict[str, Any] = {"called": False, "swap": "not-attempted"}
    task = TaskAdapter(module)

    def hook(name: str) -> None:
        if name == "before_journal_parent_swap" and not state["called"]:
            _swap_parent_to_junction(journal_parent, outside, backup, state)

    controller = _controller(module, root, task, boundary_hook=hook)
    try:
        try:
            _stage(controller, manifest, source, root / "installed", authority)
        except _ParentSwapRejected:
            assert state["swap"] == "rejected"
        except module.InstallControlError as captured:
            assert state["swap"] == "succeeded"
            assert captured.reason == "journal_parent_identity_swap"
        else:
            pytest.fail("journal writer accepted the parent junction swap")
    finally:
        if journal_parent.is_symlink():
            _remove_junction(journal_parent)
        if backup.exists() and not journal_parent.exists():
            os.replace(backup, journal_parent)
    assert state["called"] is True
    assert state["swap"] in {"rejected", "succeeded"}
    assert task.history_rows() == []
    assert _inventory(outside) == outside_before


def _reseal_journal(journal: dict[str, Any]) -> None:
    body = {key: value for key, value in journal.items() if key != "journalSha256"}
    journal["journalSha256"] = _sha(body)


def test_sec_s5_canary_receipt_is_full_sealed_evidence_and_tamper_is_typed(tmp_path: Path) -> None:
    """canaryはboolではなくcanonical receipt全体をstage/cutoverで再検証する。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 192)
    installed = root / "installed"
    task = TaskAdapter(module)
    controller = _controller(module, root, task, process=ProcessAdapter())
    _stage(controller, manifest, source, installed, authority)
    journal_path = root / "control" / "install-cutover-v1" / "journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    canary = journal.get("canaryReceipt")
    assert isinstance(canary, dict)
    assert canary["schemaVersion"] == "NEWS_GRASP_INSTALL_CANARY_RECEIPT_V1"
    assert canary["status"] == "succeeded" and canary["exitCode"] == 0 and canary["processId"] > 0
    assert canary["installedSha256"] == journal["installedSha256"]
    assert journal["canaryReceiptSha256"] == _sha(canary)

    for index, tamper in enumerate(("installed", "argv", "receipt"), start=193):
        case_root, case_source, _case_content, case_manifest, case_authority = _roots(tmp_path, index)
        case_installed = case_root / "installed"
        case_task = TaskAdapter(module)
        case_controller = _controller(module, case_root, case_task, process=ProcessAdapter())
        _stage(case_controller, case_manifest, case_source, case_installed, case_authority)
        case_journal_path = case_root / "control" / "install-cutover-v1" / "journal.json"
        mutated = json.loads(case_journal_path.read_text(encoding="utf-8"))
        mutated_canary = mutated["canaryReceipt"]
        if tamper == "installed":
            mutated_canary["installedSha256"] = "0" * 64
        elif tamper == "argv":
            mutated_canary["argvSha256"] = "1" * 64
        else:
            mutated_canary["receiptSha256"] = "2" * 64
        mutated["canaryReceiptSha256"] = _sha(mutated_canary)
        _reseal_journal(mutated)
        case_journal_path.write_text(json.dumps(mutated, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        history_before = case_task.history_rows()
        with pytest.raises(module.InstallControlError) as stage_error:
            _stage(case_controller, case_manifest, case_source, case_installed, case_authority)
        assert stage_error.value.reason == "canary_receipt_invalid"
        assert case_task.history_rows() == history_before
        with pytest.raises(module.InstallControlError) as cutover_error:
            _cutover(case_controller, case_authority)
        assert cutover_error.value.reason == "canary_receipt_invalid"
        assert case_task.history_rows() == history_before


def test_sec_s5_canary_receipt_replay_and_legacy_cutover_are_fail_closed(tmp_path: Path) -> None:
    """別installed/argvへの再sealとlegacy V1の新cutoverを許可しない。"""

    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    root, source, _content, manifest, authority = _roots(tmp_path, 198)
    installed = root / "installed"
    task = TaskAdapter(module)
    controller = _controller(module, root, task, process=ProcessAdapter(), owner_adapter=OwnerAdapter(module, task))
    _stage(controller, manifest, source, installed, authority)
    journal_path = root / "control" / "install-cutover-v1" / "journal.json"
    replay = json.loads(journal_path.read_text(encoding="utf-8"))
    replay["canaryReceipt"]["installedSha256"] = "3" * 64
    replay["canaryReceipt"]["argvSha256"] = "4" * 64
    replay["canaryReceipt"]["receiptSha256"] = _sha(replay["canaryReceipt"])
    replay["canaryReceiptSha256"] = _sha(replay["canaryReceipt"])
    _reseal_journal(replay)
    journal_path.write_text(json.dumps(replay, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    history_before = task.history_rows()
    with pytest.raises(module.InstallControlError) as captured:
        _cutover(controller, authority)
    assert captured.value.reason == "canary_receipt_invalid"
    assert task.history_rows() == history_before

    legacy_root, legacy_source, _legacy_content, legacy_manifest, legacy_authority = _roots(tmp_path, 199)
    legacy_installed = legacy_root / "installed"
    legacy_task = TaskAdapter(module)
    legacy_owner = OwnerAdapter(module, legacy_task)
    legacy_controller = _controller(module, legacy_root, legacy_task, process=ProcessAdapter(), owner_adapter=legacy_owner)
    _stage(legacy_controller, legacy_manifest, legacy_source, legacy_installed, legacy_authority)
    legacy_journal_path = legacy_root / "control" / "install-cutover-v1" / "journal.json"
    legacy = json.loads(legacy_journal_path.read_text(encoding="utf-8"))
    # V1 legacy journal has no sealed canary receipt; rollback remains the only legal path.
    legacy.pop("canaryReceipt", None)
    legacy.pop("canaryReceiptSha256", None)
    legacy["schemaVersion"] = "INSTALL_CUTOVER_JOURNAL_V1"
    legacy["phase"] = "COMMITTED"
    _reseal_journal(legacy)
    legacy_journal_path.write_text(json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    history_before_legacy_stage = legacy_task.history_rows()
    with pytest.raises(module.InstallControlError) as stage_error:
        _stage(legacy_controller, legacy_manifest, legacy_source, legacy_installed, legacy_authority)
    assert stage_error.value.reason == "canary_receipt_invalid"
    assert legacy_task.history_rows() == history_before_legacy_stage
    with pytest.raises(module.InstallControlError) as cutover_error:
        _cutover(legacy_controller, legacy_authority)
    assert cutover_error.value.reason == "canary_receipt_invalid"
    assert legacy_task.history_rows() == history_before_legacy_stage
    rollback = _rollback(legacy_controller, legacy_authority, minute=3)
    assert rollback["schemaVersion"] == "ROLLBACK_RESULT_V1"
