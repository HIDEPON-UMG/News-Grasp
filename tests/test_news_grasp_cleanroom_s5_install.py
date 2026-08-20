"""S5 clean-room install/cutover plane のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
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
    value = {
        "schemaVersion": "INSTALL_AUTHORITY_V1",
        "authorityId": "s5-authority-20260821",
        "issueDate": "2026-08-21",
        "generation": 1,
        "sourceRoot": str(source_root),
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    value["authoritySha256"] = _sha(value)
    return value


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
        return {"status": "started", "pid": 9001}

    def kill(self, process: Any) -> None:
        self._kills.append(process)
        raise AssertionError("shared-process kill is forbidden")

    def launch_observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._launches)

    def kill_observations(self) -> list[Any]:
        return deepcopy(self._kills)


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


def _controller(
    module: Any,
    root: Path,
    task: TaskAdapter,
    *,
    process: ProcessAdapter | None = None,
    security: SecurityAdapter | None = None,
    boundary_hook: Any = None,
) -> Any:
    return module.InstallCutoverController(
        root,
        task_adapter=task,
        pythonw_path=root / "pythonw.exe",
        process_adapter=process,
        security_adapter=security,
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
        controller = _controller(module, root, task)
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
        controller = _controller(module, root, task)
        _stage(controller, manifest, source, root / "installed", authority)
        _cutover(controller, authority)
        crashed = {"active": True}

        def hook(name: str, expected=boundary) -> None:
            if crashed["active"] and name == expected:
                crashed["active"] = False
                raise RuntimeError(f"test-owned rollback crash: {name}")

        rollback_controller = _controller(module, root, task, boundary_hook=hook)
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
