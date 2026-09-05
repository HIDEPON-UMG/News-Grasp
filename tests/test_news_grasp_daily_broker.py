from __future__ import annotations

import json
import ast
import hashlib
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _load_server_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "plugins" / "news-grasp-daily" / "server.py"
    spec = importlib.util.spec_from_file_location("news_grasp_daily_mcp_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sync_dry_run_uses_read_only_repo_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    class AdmissionObserved(RuntimeError):
        pass

    observed: dict[str, object] = {}

    def admission(path: Path, *, read_only: bool = False) -> Path:
        observed.update(path=path, read_only=read_only)
        raise AdmissionObserved

    monkeypatch.setattr(syncer, "_assert_trusted_repo_root", admission)

    with pytest.raises(AdmissionObserved):
        syncer._sync_unlocked(
            repo_root=tmp_path,
            dry_run=True,
            allow_custom_paths=True,
        )

    assert observed == {"path": tmp_path, "read_only": True}


def test_app_db_dry_run_repeats_read_only_admission_and_uses_sqlite_ro_uri(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    database = tmp_path / "codex.db"
    sqlite3.connect(database).close()
    template = tmp_path / "automation.toml.template"
    template.write_text("", encoding="utf-8")
    admissions: list[bool] = []
    connections: list[tuple[object, dict[str, object]]] = []
    original_connect = sqlite3.connect

    def admission(path: Path, *, read_only: bool = False) -> Path:
        admissions.append(read_only)
        return path.resolve(strict=True)

    def connect(database_arg, *args, **kwargs):
        connections.append((database_arg, dict(kwargs)))
        return original_connect(database_arg, *args, **kwargs)

    monkeypatch.setattr(syncer, "_assert_trusted_repo_root", admission)
    monkeypatch.setattr(syncer, "_assert_role_path", lambda path, **_kwargs: path.resolve(strict=True))
    monkeypatch.setattr(syncer.sqlite3, "connect", connect)

    result = syncer.sync_app_db(
        repo_root=tmp_path,
        template_path=template,
        app_db_path=database,
        project_target={"type": "local", "project_id": "fixture"},
        dry_run=True,
        allow_custom_app_db=True,
    )

    assert result["ok"] is False
    assert admissions == [True]
    assert connections == [
        (
            f"file:{database.resolve().as_posix()}?mode=ro",
            {"uri": True, "timeout": 5, "isolation_level": None},
        )
    ]


def test_daily_trust_paths_do_not_embed_a_windows_user_name() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in (
        ".agents/plugins/marketplace.json",
        "plugins/news-grasp-daily/.codex-plugin/plugin.json",
        "plugins/news-grasp-daily/.mcp.json",
        "plugins/news-grasp-daily/server.py",
        "tools/news_grasp_direct_runtime.py",
        "tools/sync_news_grasp_codex_automation.py",
    ):
        assert "C:\\Users\\" not in (root / relative).read_text(encoding="utf-8")
    module = _load_server_module()
    from tools import sync_news_grasp_codex_automation as syncer

    expected_remote = {"https://github.com/HIDEPON-UMG/News-Grasp.git"}
    assert module.TRUSTED_REMOTE_URLS == expected_remote
    assert syncer.TRUSTED_NEWS_GRASP_REMOTE_URLS == expected_remote


def test_run_daily_accepts_only_empty_arguments_and_invokes_runtime_once() -> None:
    from tools.news_grasp_daily_broker import run_daily

    calls: list[str] = []

    def runtime() -> dict[str, object]:
        calls.append("runtime")
        return {"ok": True, "status": "completed", "run_id": "direct-2026-09-05-1-real"}

    result = run_daily({}, runtime_runner=runtime)

    assert calls == ["runtime"]
    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["humanImpact"]["noFocusTheft"] is True


def test_run_daily_rejects_any_llm_supplied_option_before_runtime() -> None:
    from tools.news_grasp_daily_broker import DailyBrokerError, run_daily

    with pytest.raises(DailyBrokerError, match="ARGUMENTS_MUST_BE_EMPTY"):
        run_daily({"retry": True}, runtime_runner=lambda: pytest.fail("runtime was called"))


def test_mcp_server_lists_only_run_daily_and_rejects_nonempty_arguments() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "news-grasp-daily"
    module = _load_server_module()
    config = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["news_grasp_daily"]
    assert server["command"] == "${NEWS_GRASP_PYTHON312}"
    assert server["args"][:3] == ["-I", "-S", "-B"]
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "run_daily", "arguments": {"retry": True}},
        },
    ]
    completed = subprocess.run(
        [str(module.PYTHON312), *server["args"]],
        cwd=plugin,
        input="".join(json.dumps(item) + "\n" for item in requests),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=10,
        check=False,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 0
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert [item["name"] for item in responses[1]["result"]["tools"]] == ["run_daily"]
    assert "ARGUMENTS_MUST_BE_EMPTY" in responses[2]["error"]["message"]


def test_mcp_git_probe_uses_fixed_binary_and_scrubbed_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_server_module()
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker"))
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module._git_probe(tmp_path, "status")

    assert observed["command"][0] == str(module.TRUSTED_GIT)
    assert "GIT_DIR" not in observed["env"]
    assert observed["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert observed["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_mcp_child_environment_does_not_inherit_ambient_control_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_server_module()
    root = Path(__file__).resolve().parents[1]
    observed: dict[str, object] = {}
    monkeypatch.setenv("NEWS_GRASP_ISSUE_DATE", "1999-01-01")
    monkeypatch.setenv("NEWS_GRASP_SCHEDULER_TRIGGER_AT", "attacker")
    monkeypatch.setenv("NEWS_GRASP_STATE_ROOT", "C:/attacker-state")
    monkeypatch.setenv("GIT_DIR", "C:/attacker-git")
    monkeypatch.setenv("PATH", "C:/attacker-bin")
    monkeypatch.setattr(module, "_repo_root", lambda: root)

    def child(child_root: Path, env: dict[str, str]):
        observed.update(root=child_root, env=dict(env))
        return 0, b'{"ok":true,"status":"completed"}\n', ""

    monkeypatch.setattr(module, "_run_child_bounded", child)

    result = module._invoke_daily({})

    env = observed["env"]
    assert result["ok"] is True
    assert observed["root"] == root
    assert env["NEWS_GRASP_REPO_ROOT"] == str(root)
    assert not any(
        key.startswith("NEWS_GRASP_") and key != "NEWS_GRASP_REPO_ROOT"
        for key in env
    )
    assert "GIT_DIR" not in env
    assert "attacker" not in env["PATH"].casefold()
    assert str(module.TRUSTED_GIT.parent) in env["PATH"]
    assert env["GCM_INTERACTIVE"] == "Never"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "credential.interactive"
    assert env["GIT_CONFIG_VALUE_0"] == "never"
    assert env["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_1"] == ""
    assert env["GIT_CONFIG_KEY_2"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_2"] == "manager"


def test_mcp_child_rejects_untrusted_effective_credential_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import news_grasp_trusted_process as trusted
    module = _load_server_module()

    def fake_run(command, **_kwargs):
        if command[-1] == "credential.helper":
            return subprocess.CompletedProcess(
                command,
                0,
                "file:C:/attacker/gitconfig\tmanager\n",
                "",
            )
        return subprocess.CompletedProcess(command, 0, "never\n", "")

    monkeypatch.setattr(trusted.subprocess, "run", fake_run)

    with pytest.raises(
        trusted.TrustedProcessError,
        match="trusted_git_credential_helper_invalid",
    ):
        trusted.daily_child_environment(
            repo_root=Path(__file__).resolve().parents[1],
            python_executable=module.PYTHON312,
        )


def test_mcp_child_rejects_repo_local_credential_helper(
    tmp_path: Path,
) -> None:
    from tools import news_grasp_trusted_process as trusted

    module = _load_server_module()
    repo = tmp_path / "runtime"
    repo.mkdir()
    subprocess.run(
        [str(module.TRUSTED_GIT), "init", "-q"],
        cwd=repo,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    subprocess.run(
        [
            str(module.TRUSTED_GIT),
            "config",
            "--local",
            "credential.helper",
            "!untrusted-helper.exe",
        ],
        cwd=repo,
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )

    with pytest.raises(
        trusted.TrustedProcessError,
        match="trusted_git_credential_helper_invalid",
    ):
        trusted.daily_child_environment(
            repo_root=repo,
            python_executable=module.PYTHON312,
        )


def test_promoted_daily_closure_has_no_relative_git_or_gh_argv() -> None:
    from tools import news_grasp_direct_runtime as runtime

    root = Path(__file__).resolve().parents[1]
    violations: list[tuple[str, int, str]] = []
    for relative in runtime.DAILY_RUNTIME_RELATIVE_PATHS:
        if not relative.endswith(".py"):
            continue
        tree = ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.List, ast.Tuple))
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value in {"git", "gh"}
            ):
                violations.append((relative, node.lineno, str(node.elts[0].value)))
    assert violations == []


def test_mcp_child_is_suspended_until_job_assignment() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "news-grasp-daily"
        / "server.py"
    ).read_text(encoding="utf-8")

    assert "CREATE_SUSPENDED = 0x00000004" in source
    assert "_assign_kill_on_close_job(process)" in source
    assert "_resume_process(process)" in source
    assert source.index("_assign_kill_on_close_job(process)") < source.index("_resume_process(process)")


def test_mcp_server_rejects_oversized_request_without_reading_unbounded_input() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "news-grasp-daily"
    module = _load_server_module()
    config = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["news_grasp_daily"]
    completed = subprocess.run(
        [str(module.PYTHON312), *server["args"]],
        cwd=plugin,
        input=("x" * (1024 * 1024 + 1) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 2
    assert b"REQUEST_TOO_LARGE" in completed.stdout
    assert len(completed.stderr) == 0


def test_mcp_repo_root_uses_committed_promotion_receipt_not_automation_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_server_module()
    source_root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "promoted"
    from tools import sync_news_grasp_codex_automation as syncer

    monkeypatch.setattr(syncer, "DAILY_RUNTIME_ROOT", repo.resolve())

    files = syncer.DAILY_BROKER_PROMOTION_FILES
    hashes: dict[str, str] = {}
    for relative in files:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/HIDEPON-UMG/News-Grasp.git"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head],
        cwd=repo,
        check=True,
    )
    monkeypatch.setattr(
        syncer,
        "_read_trusted_remote_main",
        lambda _remote_url, *, cwd: head,
    )
    receipt = syncer._build_daily_broker_promotion(repo.resolve())
    assert receipt["sourceHead"] == head
    assert receipt["fileHashes"] == hashes
    receipt_path = tmp_path / "daily-broker-promotion.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(module, "PROMOTION_RECEIPT", receipt_path)
    monkeypatch.setattr(module, "TRUSTED_RUNTIME_ROOT", repo.resolve())

    assert module._repo_root() == repo.resolve()
    tampered_receipt = dict(receipt)
    tampered_receipt["remoteEvidenceSha256"] = "0" * 64
    receipt_path.write_text(json.dumps(tampered_receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="REMOTE_INVALID"):
        module._repo_root()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (repo / "tools" / "news_grasp_daily_broker.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="PROMOTED_SOURCE_DRIFT"):
        module._repo_root()


def test_promoted_runtime_file_sets_are_exactly_shared() -> None:
    module = _load_server_module()
    from tools import news_grasp_direct_runtime as runtime
    from tools import sync_news_grasp_codex_automation as syncer

    assert set(syncer.DAILY_BROKER_PROMOTION_FILES) == set(runtime.DAILY_RUNTIME_RELATIVE_PATHS)
    assert module.PROMOTED_RUNTIME_FILES == set(runtime.DAILY_RUNTIME_RELATIVE_PATHS)


def test_plugin_activation_binds_marketplace_installed_source_and_loaded_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    repo = tmp_path / "runtime"
    marketplace = tmp_path / "marketplace"
    source_plugin = Path(__file__).resolve().parents[1] / "plugins" / "news-grasp-daily"
    repo_plugin = repo / "plugins" / "news-grasp-daily"
    plugin = marketplace / "plugins" / "news-grasp-daily"
    shutil.copytree(source_plugin, repo_plugin)
    shutil.copytree(source_plugin, plugin)
    installed_mcp = syncer._render_daily_plugin_mcp((plugin / ".mcp.json").read_bytes())
    (plugin / ".mcp.json").write_bytes(installed_mcp)
    version = json.loads(
        (repo_plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    installed_server = json.loads(installed_mcp)["mcpServers"]["news_grasp_daily"]
    mutation_calls: list[tuple[str, ...]] = []
    plugin_lists = 0

    def fake_codex(*args: str) -> dict[str, object]:
        nonlocal plugin_lists
        if args == ("plugin", "marketplace", "list"):
            return {"marketplaces": [{"name": "news-grasp", "root": str(marketplace)}]}
        if args == ("plugin", "list"):
            plugin_lists += 1
            if plugin_lists == 1:
                return {
                    "installed": [
                        {
                            "pluginId": "news-grasp-daily@news-grasp",
                            "marketplaceName": "news-grasp",
                            "version": "0.1.0+codex.0000000000000000",
                            "installed": True,
                            "enabled": True,
                            "source": {"source": "local", "path": str(plugin)},
                        }
                    ]
                }
            return {
                "installed": [
                    {
                        "pluginId": "news-grasp-daily@news-grasp",
                        "marketplaceName": "news-grasp",
                        "version": version,
                        "installed": True,
                        "enabled": True,
                        "source": {"source": "local", "path": str(plugin)},
                    }
                ]
            }
        if args in {
            ("plugin", "remove", "news-grasp-daily@news-grasp"),
            ("plugin", "add", "news-grasp-daily@news-grasp"),
        }:
            mutation_calls.append(args)
            return {"installed": True}
        raise AssertionError(args)

    responses = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "run_daily"}]},
            }
        )
        + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(syncer, "_run_codex_json", fake_codex)
    monkeypatch.setattr(
        syncer,
        "_run_codex_list_json",
        lambda *args: [
            {
                "name": "news_grasp_daily",
                "enabled": True,
                "transport": {
                    "type": "stdio",
                    "command": installed_server["command"],
                    "args": ["-I", "-S", "-B", "server.py"],
                    "cwd": str(plugin),
                    "env": None,
                },
                "env_vars": [],
            }
        ],
    )
    monkeypatch.setattr(
        syncer.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, responses, b""),
    )

    result = syncer._activate_daily_plugin(repo, marketplace_root=marketplace)

    assert result["ok"] is True
    assert result["marketplaceRoot"] == str(marketplace)
    assert result["installedRoot"] == str(plugin.resolve())
    assert result["toolNames"] == ["run_daily"]
    assert result["version"] == version
    assert result["hostRegistryMcp"] == "news_grasp_daily"
    assert len(result["sourceGeneration"]) == 64
    assert result["fileHashes"][".mcp.json"] == hashlib.sha256(installed_mcp).hexdigest()
    assert result["generationFileHashes"][".mcp.json"] == hashlib.sha256(
        (repo_plugin / ".mcp.json").read_bytes()
    ).hexdigest()
    assert installed_server["command"] == str(syncer._trusted_python312_executable())
    assert result["pluginRemovedForRefresh"] is True
    assert mutation_calls == [
        ("plugin", "remove", "news-grasp-daily@news-grasp"),
        ("plugin", "add", "news-grasp-daily@news-grasp"),
    ]


def test_new_plugin_registration_has_exact_rollback(monkeypatch) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    calls: list[tuple[str, ...]] = []
    state = {"marketplace": True, "plugin": True}

    def fake_codex(*args: str) -> dict[str, object]:
        calls.append(args)
        if args == ("plugin", "marketplace", "list"):
            return {
                "marketplaces": (
                    [{"name": "news-grasp", "root": "C:/fixture"}]
                    if state["marketplace"]
                    else []
                )
            }
        if args == ("plugin", "list"):
            return {
                "installed": (
                    [{"pluginId": "news-grasp-daily@news-grasp"}]
                    if state["plugin"]
                    else []
                )
            }
        if args == ("plugin", "remove", "news-grasp-daily@news-grasp"):
            state["plugin"] = False
        if args == ("plugin", "marketplace", "remove", "news-grasp"):
            state["marketplace"] = False
        return {"ok": True}

    monkeypatch.setattr(
        syncer,
        "_run_codex_json",
        fake_codex,
    )
    monkeypatch.setattr(
        syncer,
        "_run_codex_list_json",
        lambda *args: ([{"name": "news_grasp_daily"}] if state["plugin"] else []),
    )

    result = syncer._rollback_daily_plugin_activation(
        {
            "pluginAdded": True,
            "marketplaceAdded": True,
            "pluginAddAttempted": True,
            "marketplaceAddAttempted": True,
            "previousPlugin": None,
            "previousMarketplace": None,
        }
    )

    assert result["ok"] is True
    mutations = [call for call in calls if "remove" in call]
    assert mutations == [
        ("plugin", "remove", "news-grasp-daily@news-grasp"),
        ("plugin", "marketplace", "remove", "news-grasp"),
    ]


def test_refreshed_plugin_rollback_accepts_exact_previous_registry_and_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    source = {"source": "local", "path": str(plugin_root)}
    previous_plugin = {
        "pluginId": "news-grasp-daily@news-grasp",
        "version": "0.1.0+codex.old0000000000000",
        "source": source,
    }
    previous_mcp = {
        "name": "news_grasp_daily",
        "enabled": True,
        "transport": {
            "type": "stdio",
            "command": "python.exe",
            "args": ["server.py"],
            "cwd": str(plugin_root),
            "env": None,
        },
        "env_vars": [],
    }
    calls: list[tuple[str, ...]] = []

    def fake_codex(*args: str) -> dict[str, object]:
        calls.append(args)
        if args == ("plugin", "marketplace", "list"):
            return {"marketplaces": [{"name": "news-grasp", "root": str(tmp_path)}]}
        if args == ("plugin", "list"):
            return {
                "installed": [
                    previous_plugin
                ]
            }
        return {"ok": True}

    responses = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
        + "\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "run_daily"}]}}
        )
        + "\n"
    ).encode("utf-8")
    monkeypatch.setattr(syncer, "_run_codex_json", fake_codex)
    monkeypatch.setattr(syncer, "_run_codex_list_json", lambda *args: [previous_mcp])
    monkeypatch.setattr(
        syncer.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, responses, b""),
    )

    result = syncer._rollback_daily_plugin_activation(
        {
            "pluginAdded": True,
            "marketplaceAdded": False,
            "pluginRemovedForRefresh": True,
            "previousPluginVersion": "0.1.0+codex.old0000000000000",
            "previousPluginSource": source,
            "previousPlugin": previous_plugin,
            "previousMarketplace": {"name": "news-grasp", "root": str(tmp_path)},
            "previousMcpRegistration": previous_mcp,
            "previousToolNames": ["run_daily"],
        }
    )

    assert result["ok"] is True, result
    assert result["restored"] == []
    assert [call for call in calls if "remove" in call or "add" in call] == []


def test_promotion_rejects_live_nonterminal_daily_run(tmp_path: Path, monkeypatch) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools import sync_news_grasp_codex_automation as syncer

    state = tmp_path / "state"
    state.mkdir()
    database = state / "direct-mainline.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE runs(run_id TEXT,status TEXT,issue_date TEXT,lease_until TEXT)"
    )
    connection.execute(
        "INSERT INTO runs VALUES(?,?,?,?)",
        ('run-active', 'executing', '2026-09-05', '2999-09-05T06:30:00+00:00'),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(runtime, "_canonical_daily_state_root", lambda: state)

    with pytest.raises(ValueError, match="daily_broker_active_run:run-active:executing"):
        syncer._assert_daily_promotion_quiescent()


def test_promotion_capture_accepts_missing_first_generation_file(tmp_path: Path) -> None:
    from tools import sync_news_grasp_codex_automation as syncer

    target = syncer._capture_promotion_target(
        tmp_path / "daily-broker-promotion.json",
        kind="daily_broker_promotion",
    )

    assert target["preimagePresent"] is False
    assert target["preimageBytes"] == b""
    assert target["status"] == "pending"


def test_promotion_rejects_expired_nonterminal_run_until_same_run_recovery_finishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import news_grasp_direct_runtime as runtime
    from tools import sync_news_grasp_codex_automation as syncer

    state = tmp_path / "state"
    state.mkdir()
    database = state / "direct-mainline.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE runs(run_id TEXT,status TEXT,issue_date TEXT,lease_until TEXT)"
    )
    connection.execute(
        "INSERT INTO runs VALUES(?,?,?,?)",
        ('run-expired', 'active', '2026-09-04', '2000-09-04T06:30:00+00:00'),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(runtime, "_canonical_daily_state_root", lambda: state)

    with pytest.raises(ValueError, match="daily_broker_active_run:run-expired:active"):
        syncer._assert_daily_promotion_quiescent()
