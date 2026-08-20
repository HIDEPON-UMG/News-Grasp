"""S5 clean-room install/cutover plane のsealed Expected Red suite。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_grasp_cleanroom_s5_cases.json"
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


def _at(minute: int = 0) -> datetime:
    return datetime(2026, 8, 21, 6, minute, tzinfo=TOKYO)


def _cases() -> dict[str, Any]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schemaVersion"] == "NEWS_GRASP_CLEANROOM_S5_CASES_V1"
    assert value["packetId"] == "NG-CLEANROOM-S5-RED-V1"
    assert value["requiredManifestKeys"] == ["schemaVersion", "scheduleId", "tasks"]
    return value


def _manifest() -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_CONTROL_MANIFEST_V1",
        "scheduleId": "news-grasp-daily-v1",
        "tasks": [
            {
                "taskPath": "\\",
                "taskName": NEW_TASK,
                "multipleInstancesPolicy": "Parallel",
                "triggers": [{"triggerId": "scheduled-0600", "kind": "daily", "localTime": "06:00:00", "timeZone": "Asia/Tokyo"}],
                "action": {
                    "executable": "C:/Python312/pythonw.exe",
                    "argv": ["-m", "tools.news_grasp_cleanroom_dispatch", "--schedule-id", "news-grasp-daily-v1"],
                    "workingDirectory": "<RUNTIME_ROOT>",
                },
            }
        ],
    }


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
    return root, source, content, _manifest(), _authority(source, content)


class TaskAdapter:
    """Windowsを触らずTask XMLとenabled stateだけを記録する。"""

    def __init__(self, module: Any, *, old_enabled: bool = True, new_enabled: bool = False) -> None:
        self.module = module
        self.enabled = {OLD_TASK: old_enabled, NEW_TASK: new_enabled, PULL_TASK: False, INVENTORY_TASK: False}
        self.xml = {OLD_TASK: "<old-task-v1/>", NEW_TASK: "<candidate-task-v1/>"}
        self.registered: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, bool]] = []
        self.deny_operation: str | None = None

    def _check_denial(self, operation: str) -> None:
        if self.deny_operation == operation:
            raise self.module.PrivilegeDenied(f"test-owned privilege denial: {operation}")

    def _record(self, operation: str, name: str, **detail: Any) -> None:
        self.history.append({"operation": operation, "name": name, **detail})
        self.snapshots.append(dict(self.enabled))

    def snapshot(self) -> dict[str, Any]:
        self._check_denial("snapshot")
        self._record("snapshot", "all")
        return {"tasks": {name: {"enabled": value, "xml": self.xml.get(name)} for name, value in self.enabled.items()}}

    def register_disabled(self, name: str, definition: str, **kwargs: Any) -> None:
        self._check_denial("register_disabled")
        self.registered[name] = {"definition": definition, **kwargs}
        self.enabled[name] = False
        self.xml[name] = definition
        self._record("register_disabled", name, definition=definition, kwargs=kwargs)

    def disable(self, name: str) -> None:
        self._check_denial("disable")
        self.enabled[name] = False
        self._record("disable", name)

    def enable(self, name: str) -> None:
        self._check_denial("enable")
        self.enabled[name] = True
        self._record("enable", name)

    def restore(self, name: str, definition: str) -> None:
        self._check_denial("restore")
        self.xml[name] = definition
        self.enabled[name] = False
        self._record("restore", name, definition=definition)

    def remove_candidate(self, name: str) -> None:
        self._check_denial("remove_candidate")
        self.registered.pop(name, None)
        self.enabled[name] = False
        self._record("remove_candidate", name)

    @property
    def dual_enabled_count(self) -> int:
        return int(self.enabled.get(OLD_TASK, False) and self.enabled.get(NEW_TASK, False))


class ProcessAdapter:
    def __init__(self) -> None:
        self.launches: list[dict[str, Any]] = []
        self.kills: list[Any] = []

    def launch(self, argv: list[str], cwd: Path, **kwargs: Any) -> dict[str, Any]:
        self.launches.append({"argv": list(argv), "cwd": str(cwd), **kwargs})
        return {"status": "started", "pid": 9001}

    def kill(self, process: Any) -> None:
        self.kills.append(process)
        raise AssertionError("shared-process kill is forbidden")


def _stage(controller: Any, manifest: dict[str, Any], source: Path, installed: Path, authority: dict[str, Any]) -> Any:
    return controller.stage(manifest, source, installed, authority, _at())


def _cutover(controller: Any, authority: dict[str, Any]) -> Any:
    return controller.cutover(authority, _at(1))


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

        controller = module.InstallCutoverController(root, task_adapter=task, boundary_hook=hook)
        if boundary == "stage":
            with pytest.raises(Exception):
                _stage(controller, manifest, source, installed, authority)
            _stage(controller, manifest, source, installed, authority)
            _cutover(controller, authority)
        else:
            _stage(controller, manifest, source, installed, authority)
            try:
                _cutover(controller, authority)
            except Exception:
                pass
            _cutover(controller, authority)
        inspection = controller.inspect()
        assert (installed / "launcher.pyw").read_bytes() == content
        assert inspection["dualEnabledCount"] == 0
        assert task.enabled[OLD_TASK] is False
        assert task.enabled[NEW_TASK] is True
        assert len(task.registered) == 1
        assert sum(row["operation"] == "register_disabled" for row in task.history) == 1


def test_s5_dual_enabled_invariant(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    for index, (state, old_enabled, new_enabled) in enumerate(
        (("old_only", True, False), ("new_only", False, True), ("both", True, True), ("both_disabled", False, False)),
        start=20,
    ):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        task = TaskAdapter(module, old_enabled=old_enabled, new_enabled=new_enabled)
        controller = module.InstallCutoverController(root, task_adapter=task)
        try:
            _stage(controller, manifest, source, root / "installed", authority)
            _cutover(controller, authority)
        except module.InstallControlError:
            pass
        assert task.dual_enabled_count == 0, state
        assert all(not snapshot[OLD_TASK] or not snapshot[NEW_TASK] for snapshot in task.snapshots)
        assert task.enabled[PULL_TASK] is False and task.enabled[INVENTORY_TASK] is False
        assert not any(row["operation"] == "remove_candidate" and row["name"] in {PULL_TASK, INVENTORY_TASK} for row in task.history)
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
        _stage(module.InstallCutoverController(root, task_adapter=task), manifest, source, root / "installed", authority)
        task.deny_operation = denied_operation
        controller = module.InstallCutoverController(root, task_adapter=task)
        with pytest.raises((module.PrivilegeDenied, module.InstallControlError)):
            _cutover(controller, authority)
        assert task.dual_enabled_count == 0
        task.deny_operation = None
        _cutover(controller, authority)
        assert task.enabled[OLD_TASK] is False and task.enabled[NEW_TASK] is True

    for index, boundary in enumerate(cases["rollbackBoundaries"], start=50):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        task = TaskAdapter(module)
        controller = module.InstallCutoverController(root, task_adapter=task)
        _stage(controller, manifest, source, root / "installed", authority)
        _cutover(controller, authority)
        old_xml = task.xml[OLD_TASK]
        crashed = {"active": True}

        def hook(name: str, expected=boundary) -> None:
            if crashed["active"] and name == expected:
                crashed["active"] = False
                raise RuntimeError(f"test-owned rollback crash: {name}")

        rollback_controller = module.InstallCutoverController(root, task_adapter=task, boundary_hook=hook)
        try:
            rollback_controller.rollback(authority, _at(2))
        except Exception:
            pass
        rollback_controller.rollback(authority, _at(3))
        assert task.enabled[OLD_TASK] is True and task.enabled[NEW_TASK] is False
        assert task.xml[OLD_TASK] == old_xml
        assert task.dual_enabled_count == 0
        ordered = [row["operation"] + ":" + row["name"] for row in task.history]
        assert ordered.index("disable:" + NEW_TASK) < ordered.index("restore:" + OLD_TASK)


def test_s5_path_security_matrix(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    for index, path_case in enumerate(cases["pathCases"], start=70):
        root, source, _content, manifest, authority = _roots(tmp_path, index)
        mutated = deepcopy(manifest)
        if path_case == "traversal":
            mutated["tasks"][0]["action"]["workingDirectory"] = "../escape"
        elif path_case == "absolute_escape":
            mutated["tasks"][0]["action"]["workingDirectory"] = str(tmp_path.parent / "outside")
        elif path_case == "symlink_reparse":
            mutated["tasks"][0]["action"]["entryModule"] = "reparse://launcher.pyw"
        elif path_case == "source_identity_swap":
            authority["sourceRoot"] = str(tmp_path.parent / "swapped")
            authority["authoritySha256"] = _sha({key: value for key, value in authority.items() if key != "authoritySha256"})
        elif path_case == "hash_mismatch":
            authority["sourceSha256"] = "0" * 64
            authority["authoritySha256"] = _sha({key: value for key, value in authority.items() if key != "authoritySha256"})
        elif path_case == "insecure_acl":
            mutated["security"] = {"acl": "insecure"}
        else:
            mutated["security"] = {"signer": "untrusted"}
        task = TaskAdapter(module)
        controller = module.InstallCutoverController(root, task_adapter=task)
        with pytest.raises(module.InstallControlError):
            _stage(controller, mutated, source, root / "installed", authority)
        assert task.history == []
        assert not list((root / "installed").rglob("*"))


def test_s5_secret_and_argument_injection(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    root, source, _content, manifest, authority = _roots(tmp_path, 90)
    task = TaskAdapter(module)
    valid_controller = module.InstallCutoverController(root, task_adapter=task)
    _stage(valid_controller, manifest, source, root / "installed", authority)
    definition = task.registered[NEW_TASK]["definition"]
    assert manifest["tasks"][0]["action"]["executable"] in definition
    assert all(argument in definition for argument in manifest["tasks"][0]["action"]["argv"])
    for index, secret_case in enumerate(cases["secretCases"], start=91):
        case_root, case_source, _case_content, mutated, case_authority = _roots(tmp_path, index)
        if secret_case == "manifest_unknown_key":
            mutated["unexpected"] = "value"
        elif secret_case == "shell_metacharacter":
            mutated["tasks"][0]["action"]["argv"].append("& whoami")
        elif secret_case == "control_byte":
            mutated["tasks"][0]["action"]["argv"].append("bad\narg")
        elif secret_case == "secret_argv":
            mutated["tasks"][0]["action"]["argv"].append("TOKEN=super-secret")
        else:
            mutated["diagnostic"] = "apiToken=super-secret"
        case_task = TaskAdapter(module)
        case_controller = module.InstallCutoverController(case_root, task_adapter=case_task)
        with pytest.raises(module.InstallControlError) as caught:
            _stage(case_controller, mutated, case_source, case_root / "installed", case_authority)
        assert case_task.history == []
        assert "super-secret" not in str(caught.value)
        assert "super-secret" not in json.dumps(case_controller.inspect(), ensure_ascii=False)


def test_s5_child_tree_human_impact(tmp_path: Path) -> None:
    module = importlib.import_module("tools.news_grasp_cleanroom_install")
    cases = _cases()
    root, source, _content, manifest, authority = _roots(tmp_path, 100)
    process = ProcessAdapter()
    task = TaskAdapter(module)
    controller = module.InstallCutoverController(root, task_adapter=task, process_adapter=process)
    _stage(controller, manifest, source, root / "installed", authority)
    _cutover(controller, authority)
    assert process.launches
    for launch in process.launches:
        assert launch["shell"] is False
        assert launch["creationflags"] & CREATE_NO_WINDOW
        assert "日本語" in launch["cwd"]
        assert launch.get("noFocusTheft") is True
        assert launch.get("noAutoOpen") is True
        assert launch.get("noUserMonitoring") is True
    assert process.kills == []
    inspection = controller.inspect()
    assert inspection["focusTheftCount"] == 0
    assert inspection["sharedProcessKillCount"] == 0
    assert cases["humanImpact"]["shell"] is False
