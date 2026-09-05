"""News-Grasp の Codex App automation 定義を repo template から同期する。

Windows Scheduled Task や旧 runner には触れない。Codex App が読む
``~/.codex/automations/news-grasp-6-40/automation.toml`` を、repo-local
template と同じ direct 本線契約へ揃えるための薄い同期器である。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .news_grasp_direct_runtime import DAILY_RUNTIME_RELATIVE_PATHS, _windows_local_app_data
from .news_grasp_title_control import TITLE_PATTERN, TITLE_SUFFIX


AUTOMATION_ID = "news-grasp-6-40"
CANONICAL_NEWS_GRASP_REPO_ROOT = (
    Path.home() / "OneDrive" / "ドキュメント" / "ProjectFolders" / "News-Grasp"
)
TEST_FIXTURE_ROOT_NAME = "news-grasp-sync-fixture"
MAX_AUTOMATION_TOML_BYTES = 96 * 1024
MAX_SKILL_BYTES = 96 * 1024
MAX_APP_GLOBAL_STATE_BYTES = 2 * 1024 * 1024
MAX_PROMOTION_BACKUP_BYTES = 64 * 1024 * 1024
DAILY_BROKER_PROMOTION_FILES = DAILY_RUNTIME_RELATIVE_PATHS
DAILY_RUNTIME_ROOT = Path(r"C:\ngstage\News-Grasp-runtime")
DAILY_PLUGIN_ROOT = Path(r"C:\ngstage\News-Grasp-daily-plugin")
DAILY_PLUGIN_ID = "news-grasp-daily@news-grasp"
DAILY_MARKETPLACE_NAME = "news-grasp"
DAILY_PYTHON312_TOKEN = "${NEWS_GRASP_PYTHON312}"
TRUSTED_GIT = Path(r"C:\Program Files\Git\cmd\git.exe")
TRUSTED_CODEX = (
    _windows_local_app_data()
    / "OpenAI"
    / "Codex"
    / "bin"
    / "1e3e57cdf0634c02"
    / "codex.exe"
)
TRUSTED_CODEX_SHA256 = "56a84de2b617af6b95b0c5c5d8ae120d3c2fb69008ab330c7e7df3945b98b782"
DAILY_PLUGIN_RELATIVE_FILES = (
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "server.py",
)
DAILY_PLUGIN_GENERATION_RELATIVE_FILES = (
    ".mcp.json",
    "server.py",
)
DAILY_PLUGIN_DEPLOYMENT_RELATIVE_FILES = (
    ".agents/plugins/marketplace.json",
    "plugins/news-grasp-daily/.codex-plugin/plugin.json",
    "plugins/news-grasp-daily/.mcp.json",
    "plugins/news-grasp-daily/server.py",
)
TRUSTED_NEWS_GRASP_REMOTE_URLS = {
    "https://github.com/HIDEPON-UMG/News-Grasp.git",
}
APP_DB_AUTOMATION_OWNED_COLUMNS = (
    "id",
    "name",
    "prompt",
    "status",
    "next_run_at",
    "last_run_at",
    "cwds",
    "rrule",
    "model",
    "reasoning_effort",
    "created_at",
    "updated_at",
    "target_type",
    "project_id",
)
# App DBにはCodex所有の追加列がある。News-Grasp promotionはこれらを消去せず
# pass-throughするが、存在自体はrow-level CAS rollbackのschema契約に含める。
APP_DB_AUTOMATION_OPTIONAL_COLUMNS = (
    "kind",
    "target_thread_id",
    "execution_environment",
    "local_environment_config_path",
    "plugin_template_id",
    "notification_policy",
    "account_id",
    "user_id",
    "installation_id",
    "legacy_automation_id",
)
APP_DB_AUTOMATION_SCHEMA_COLUMNS = (
    *APP_DB_AUTOMATION_OWNED_COLUMNS,
    *APP_DB_AUTOMATION_OPTIONAL_COLUMNS,
)
APP_DB_ROW_HASH_SCHEMA_VERSION = "NEWS_GRASP_CODEX_AUTOMATION_ROW_HASH_V1"
REQUIRED_PROMPT_PARTS = (
    "$news-grasp-direct-mainline",
    "YY/MM/DD",
    "news_grasp_daily.run_daily",
    "空のJSON",
    "title_status",
    "title_status=already_ok",
    "already_ok",
    "post_publish_issue_list",
    "static_check",
    "scoped_contract_unit",
    "current_issue_integration",
    "external_publication",
    "consumer_public_verification",
    "atomic_completion",
    "protected_release_reexecution_forbidden",
    "producer receipt",
    "unknown_unobtainable",
)
AUTOMATION_COMPLETION_PHRASE = "完全な品質で記事公開するまで完了してはならない"


class _AppDbSchemaDrift(RuntimeError):
    """App DB schemaがrow-CAS契約の範囲外である。"""


if os.name == "nt":
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
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

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_template(repo_root: Path) -> Path:
    return repo_root / "automation" / "news-grasp-6-40" / "automation.toml.template"


def _default_source_skill(repo_root: Path) -> Path:
    return repo_root / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"


def _default_installed() -> Path:
    return Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"


def _default_installed_skill() -> Path:
    return Path.home() / ".codex" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"


def _daily_broker_promotion_path(installed_path: Path) -> Path:
    return installed_path.parent / "daily-broker-promotion.json"


def _build_daily_broker_promotion(repo_root: Path) -> dict[str, Any]:
    if not _same_path(repo_root, DAILY_RUNTIME_ROOT):
        raise ValueError("daily_broker_runtime_root_invalid")
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    git_env = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    git_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )

    def probe(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(TRUSTED_GIT), *args],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            check=False,
            env=git_env,
            timeout=30,
            creationflags=creationflags,
        )

    head_result = probe("rev-parse", "--verify", "HEAD")
    try:
        source_head = bytes(head_result.stdout or b"").decode("ascii", errors="strict").strip().casefold()
    except UnicodeError as exc:
        raise ValueError("daily_broker_source_head_invalid") from exc
    if head_result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", source_head) is None:
        raise ValueError("daily_broker_source_head_invalid")
    status = probe("status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0 or bytes(status.stdout or b"").strip():
        raise ValueError("daily_broker_source_tree_not_clean")
    remote_result = probe("remote", "get-url", "origin")
    try:
        remote_url = bytes(remote_result.stdout or b"").decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise ValueError("daily_broker_remote_url_invalid") from exc
    if remote_result.returncode != 0 or remote_url not in TRUSTED_NEWS_GRASP_REMOTE_URLS:
        raise ValueError("daily_broker_remote_url_invalid")
    remote_main = _read_trusted_remote_main(remote_url, cwd=repo_root)
    if remote_main != source_head:
        raise ValueError("daily_broker_trusted_remote_head_mismatch")
    remote_head_result = probe("rev-parse", "--verify", "origin/main")
    try:
        remote_head = bytes(remote_head_result.stdout or b"").decode("ascii", errors="strict").strip().casefold()
    except UnicodeError as exc:
        raise ValueError("daily_broker_remote_head_invalid") from exc
    if remote_head_result.returncode != 0 or remote_head != source_head:
        raise ValueError("daily_broker_remote_head_mismatch")
    hashes: dict[str, str] = {}
    for relative in DAILY_BROKER_PROMOTION_FILES:
        path = repo_root / relative
        raw = _read_bytes_no_follow(path, limit=2 * 1024 * 1024)
        tracked = probe("ls-files", "--error-unmatch", "--", relative)
        if tracked.returncode != 0:
            raise ValueError(f"daily_broker_source_not_committed:{relative}")
        hashes[relative] = hashlib.sha256(raw).hexdigest()
    generation = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    remote_evidence = {
        "remoteUrl": remote_url,
        "remoteRef": "refs/heads/main",
        "remoteMainSha": remote_main,
    }
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_BROKER_PROMOTION_V1",
        "repoRoot": str(repo_root),
        "sourceHead": source_head,
        "remoteUrl": remote_url,
        "remoteRef": "refs/heads/main",
        "remoteMainSha": remote_main,
        "remoteEvidenceSha256": hashlib.sha256(
            json.dumps(
                remote_evidence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "sourceGeneration": generation,
        "fileHashes": hashes,
    }


def _assert_daily_promotion_quiescent() -> None:
    """実行中runのloaded bytesをpromotionで差し替えない。"""

    from .news_grasp_direct_runtime import _canonical_daily_state_root

    database = _canonical_daily_state_root() / "direct-mainline.sqlite3"
    if not database.is_file():
        return
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT run_id,status,lease_until FROM runs "
            "WHERE status IN ('active','executing','finalizing')"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError("daily_broker_runtime_state_invalid") from exc
    finally:
        connection.close()
    for row in rows:
        raise ValueError(f"daily_broker_active_run:{row[0]}:{row[1]}")


def _minimal_subprocess_env() -> dict[str, str]:
    allowed = (
        "APPDATA",
        "HOME",
        "LOCALAPPDATA",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env["CODEX_HOME"] = str(Path.home() / ".codex")
    env["NO_COLOR"] = "1"
    return env


def _trusted_codex_executable() -> Path:
    _assert_no_reparse_chain(TRUSTED_CODEX)
    if not _is_regular_file_no_reparse(TRUSTED_CODEX):
        raise ValueError("daily_plugin_codex_executable_missing")
    raw = _read_bytes_no_follow(TRUSTED_CODEX, limit=384 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != TRUSTED_CODEX_SHA256:
        raise ValueError("daily_plugin_codex_executable_untrusted")
    return TRUSTED_CODEX.resolve(strict=True)


def _trusted_python312_executable() -> Path:
    executable = (
        _windows_local_app_data()
        / "Programs"
        / "Python"
        / "Python312"
        / "python.exe"
    )
    _assert_no_reparse_chain(executable)
    if not _is_regular_file_no_reparse(executable):
        raise ValueError("daily_plugin_python312_executable_missing")
    return executable.resolve(strict=True)


def _render_daily_plugin_mcp(raw: bytes) -> bytes:
    try:
        config = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("daily_plugin_mcp_source_invalid") from exc
    server = (config.get("mcpServers") or {}).get("news_grasp_daily")
    if not isinstance(server, dict) or server.get("command") != DAILY_PYTHON312_TOKEN:
        raise ValueError("daily_plugin_mcp_python_token_invalid")
    server["command"] = str(_trusted_python312_executable())
    rendered = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if DAILY_PYTHON312_TOKEN in rendered:
        raise ValueError("daily_plugin_mcp_python_token_unresolved")
    return rendered.encode("utf-8")


def _expected_daily_plugin_bytes(plugin_root: Path, relative: str) -> bytes:
    raw = _read_bytes_no_follow(
        plugin_root / relative,
        limit=2 * 1024 * 1024,
    )
    return _render_daily_plugin_mcp(raw) if relative == ".mcp.json" else raw


def _read_trusted_remote_main(remote_url: str, *, cwd: Path) -> str:
    git_env = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    git_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    completed = subprocess.run(
        [
            str(TRUSTED_GIT),
            "ls-remote",
            "--exit-code",
            remote_url,
            "refs/heads/main",
        ],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        check=False,
        env=git_env,
        timeout=60,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    try:
        output = bytes(completed.stdout or b"").decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise ValueError("daily_broker_trusted_remote_head_invalid") from exc
    match = re.fullmatch(r"([0-9a-fA-F]{40})\s+refs/heads/main", output)
    if completed.returncode != 0 or match is None:
        raise ValueError("daily_broker_trusted_remote_head_invalid")
    return match.group(1).casefold()


def _run_codex_json(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(_trusted_codex_executable()), *args, "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        env=_minimal_subprocess_env(),
        timeout=60,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise ValueError(f"daily_plugin_cli_red:{':'.join(args)}")
    try:
        value = json.loads(bytes(completed.stdout or b"").decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"daily_plugin_cli_receipt_invalid:{':'.join(args)}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"daily_plugin_cli_receipt_invalid:{':'.join(args)}")
    return value


def _run_codex_list_json(*args: str) -> list[Any]:
    completed = subprocess.run(
        [str(_trusted_codex_executable()), *args, "--json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        env=_minimal_subprocess_env(),
        timeout=60,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    if completed.returncode != 0:
        raise ValueError(f"daily_plugin_cli_red:{':'.join(args)}")
    try:
        value = json.loads(bytes(completed.stdout or b"").decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"daily_plugin_cli_receipt_invalid:{':'.join(args)}") from exc
    if not isinstance(value, list):
        raise ValueError(f"daily_plugin_cli_receipt_invalid:{':'.join(args)}")
    return value


def _plugin_source_generation(plugin_root: Path) -> tuple[str, dict[str, str]]:
    hashes = {
        relative: hashlib.sha256(
            _read_bytes_no_follow(plugin_root / relative, limit=2 * 1024 * 1024)
        ).hexdigest()
        for relative in DAILY_PLUGIN_GENERATION_RELATIVE_FILES
    }
    manifest = json.loads(
        _read_text_limited(
            plugin_root / ".codex-plugin" / "plugin.json",
            limit=MAX_AUTOMATION_TOML_BYTES,
        )
    )
    if not isinstance(manifest, dict):
        raise ValueError("daily_plugin_manifest_invalid")
    manifest_without_version = dict(manifest)
    manifest_without_version.pop("version", None)
    hashes[".codex-plugin/plugin.without-version.json"] = hashlib.sha256(
        json.dumps(
            manifest_without_version,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    generation = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return generation, hashes


def _run_registered_mcp_canary(registered_mcp: Mapping[str, Any]) -> list[str]:
    transport = registered_mcp.get("transport")
    if not isinstance(transport, Mapping) or transport.get("type") != "stdio":
        raise ValueError("daily_plugin_registered_transport_invalid")
    command = str(transport.get("command") or "")
    arguments = [str(item) for item in transport.get("args") or ()]
    cwd = Path(str(transport.get("cwd") or "")).resolve(strict=True)
    request = "".join(
        json.dumps(item, separators=(",", ":")) + "\n"
        for item in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    )
    canary = subprocess.run(
        [command, *arguments],
        cwd=cwd,
        env=_minimal_subprocess_env(),
        input=request.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
        timeout=30,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    try:
        responses = [
            json.loads(line)
            for line in bytes(canary.stdout or b"").decode(
                "utf-8", errors="strict"
            ).splitlines()
            if line.strip()
        ]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("daily_plugin_loaded_canary_invalid") from exc
    tools = (
        (responses[1].get("result") or {}).get("tools")
        if len(responses) > 1
        else None
    )
    if canary.returncode != 0 or not isinstance(tools, list):
        raise ValueError("daily_plugin_loaded_canary_red")
    names = [str(item.get("name") or "") for item in tools if isinstance(item, Mapping)]
    if len(names) != len(tools) or not names:
        raise ValueError("daily_plugin_loaded_canary_red")
    return names


def _rollback_daily_plugin_activation(receipt: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    removed: list[str] = []
    restored: list[str] = []

    def observe() -> tuple[Any, Any, Any]:
        marketplaces = _run_codex_json("plugin", "marketplace", "list").get(
            "marketplaces"
        )
        installed = _run_codex_json("plugin", "list").get("installed")
        mcp_registry = _run_codex_list_json("mcp", "list")
        if not isinstance(marketplaces, list) or not isinstance(installed, list):
            raise ValueError("daily_plugin_rollback_registry_invalid")
        marketplace = next(
            (
                item
                for item in marketplaces
                if isinstance(item, Mapping)
                and item.get("name") == DAILY_MARKETPLACE_NAME
            ),
            None,
        )
        plugin = next(
            (
                item
                for item in installed
                if isinstance(item, Mapping) and item.get("pluginId") == DAILY_PLUGIN_ID
            ),
            None,
        )
        registered_mcp = next(
            (
                item
                for item in mcp_registry
                if isinstance(item, Mapping) and item.get("name") == "news_grasp_daily"
            ),
            None,
        )
        return marketplace, plugin, registered_mcp

    def plugin_projection(value: Any) -> Any:
        if not isinstance(value, Mapping):
            return None
        return {
            key: value.get(key)
            for key in (
                "pluginId",
                "marketplaceName",
                "version",
                "installed",
                "enabled",
                "source",
            )
        }

    previous_marketplace = receipt.get("previousMarketplace")
    previous_plugin = receipt.get("previousPlugin")
    try:
        marketplace, plugin, _registered_mcp = observe()
        if isinstance(previous_marketplace, Mapping) and not isinstance(
            marketplace, Mapping
        ):
            _run_codex_json(
                "plugin",
                "marketplace",
                "add",
                str(previous_marketplace.get("root") or ""),
            )
            restored.append("previous_marketplace")
            marketplace, plugin, _registered_mcp = observe()
        if plugin_projection(plugin) != plugin_projection(previous_plugin):
            if isinstance(plugin, Mapping):
                _run_codex_json("plugin", "remove", DAILY_PLUGIN_ID)
                removed.append("plugin")
            if isinstance(previous_plugin, Mapping):
                _run_codex_json("plugin", "add", DAILY_PLUGIN_ID)
                restored.append("previous_plugin")
            marketplace, plugin, _registered_mcp = observe()
        if not isinstance(previous_marketplace, Mapping) and isinstance(
            marketplace, Mapping
        ):
            _run_codex_json(
                "plugin", "marketplace", "remove", DAILY_MARKETPLACE_NAME
            )
            removed.append("marketplace")

        marketplace, plugin, registered_mcp = observe()
        if plugin_projection(plugin) != plugin_projection(previous_plugin):
            raise ValueError("daily_plugin_rollback_previous_plugin_mismatch")
        if isinstance(previous_marketplace, Mapping):
            if not isinstance(marketplace, Mapping) or not _same_path(
                Path(str(marketplace.get("root") or "")),
                Path(str(previous_marketplace.get("root") or "")),
            ):
                raise ValueError("daily_plugin_rollback_previous_marketplace_mismatch")
        elif marketplace is not None:
            raise ValueError("daily_plugin_rollback_marketplace_still_registered")
        previous_mcp = receipt.get("previousMcpRegistration")
        if isinstance(previous_mcp, Mapping):
            if (
                registered_mcp != previous_mcp
                or _run_registered_mcp_canary(registered_mcp)
                != receipt.get("previousToolNames")
            ):
                raise ValueError("daily_plugin_rollback_previous_binding_mismatch")
        elif registered_mcp is not None:
            raise ValueError("daily_plugin_rollback_mcp_still_registered")
    except Exception as exc:  # noqa: BLE001 - rollback verification is receipt-bound.
        failures.append(f"verification:{type(exc).__name__}:{exc}")
    return {
        "schemaVersion": "NEWS_GRASP_DAILY_PLUGIN_ROLLBACK_V1",
        "ok": not failures,
        "status": "rolled_back" if not failures else "rollback_failed",
        "removed": removed,
        "restored": restored,
        "failures": failures,
    }


def _activate_daily_plugin(
    repo_root: Path,
    *,
    marketplace_root: Path | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """marketplace、installed source、loaded MCPを同じruntime bytesへ束縛する。"""

    desired_marketplace_root = (marketplace_root or repo_root).resolve(strict=True)
    source_plugin_root = repo_root / "plugins" / "news-grasp-daily"
    expected_plugin_root = desired_marketplace_root / "plugins" / "news-grasp-daily"
    plugin_manifest = json.loads(
        _read_text_limited(
            source_plugin_root / ".codex-plugin" / "plugin.json",
            limit=MAX_AUTOMATION_TOML_BYTES,
        )
    )
    generation, generation_hashes = _plugin_source_generation(source_plugin_root)
    version = str(plugin_manifest.get("version") or "")
    version_match = re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+\+codex\.([0-9a-f]{16})", version
    )
    if version_match is None or version_match.group(1) != generation[:16]:
        raise ValueError("daily_plugin_version_generation_mismatch")
    activation: dict[str, Any] = {
        "marketplaceAdded": False,
        "marketplaceAddAttempted": False,
        "pluginAdded": False,
        "pluginAddAttempted": False,
        "pluginRemovedForRefresh": False,
        "pluginRemoveAttempted": False,
        "previousMarketplace": None,
        "previousPlugin": None,
        "previousPluginVersion": "",
        "previousPluginSource": None,
        "previousMcpRegistration": None,
        "previousToolNames": [],
    }
    try:
        marketplaces = _run_codex_json("plugin", "marketplace", "list").get("marketplaces")
        if not isinstance(marketplaces, list):
            raise ValueError("daily_plugin_marketplace_list_invalid")
        marketplace = next(
            (
                item
                for item in marketplaces
                if isinstance(item, Mapping) and item.get("name") == DAILY_MARKETPLACE_NAME
            ),
            None,
        )
        activation["previousMarketplace"] = (
            dict(marketplace) if isinstance(marketplace, Mapping) else None
        )
        if marketplace is None:
            activation["marketplaceAddAttempted"] = True
            _run_codex_json(
                "plugin", "marketplace", "add", str(desired_marketplace_root)
            )
            activation["marketplaceAdded"] = True
            marketplaces = _run_codex_json(
                "plugin", "marketplace", "list"
            ).get("marketplaces")
            marketplace = next(
                (
                    item
                    for item in marketplaces or ()
                    if isinstance(item, Mapping)
                    and item.get("name") == DAILY_MARKETPLACE_NAME
                ),
                None,
            )
        if not isinstance(marketplace, Mapping) or not _same_path(
            Path(str(marketplace.get("root") or "")),
            desired_marketplace_root,
        ):
            raise ValueError("daily_plugin_marketplace_root_invalid")

        installed = _run_codex_json("plugin", "list").get("installed")
        if not isinstance(installed, list):
            raise ValueError("daily_plugin_install_list_invalid")
        plugin = next(
            (
                item
                for item in installed
                if isinstance(item, Mapping) and item.get("pluginId") == DAILY_PLUGIN_ID
            ),
            None,
        )
        activation["previousPlugin"] = (
            dict(plugin) if isinstance(plugin, Mapping) else None
        )
        if isinstance(plugin, Mapping):
            previous_source = plugin.get("source")
            previous_root = Path(str((previous_source or {}).get("path") or ""))
            already_current = (
                plugin.get("installed") is True
                and plugin.get("enabled") is True
                and plugin.get("marketplaceName") == DAILY_MARKETPLACE_NAME
                and plugin.get("version") == version
                and isinstance(previous_source, Mapping)
                and previous_source.get("source") == "local"
                and _same_path(previous_root, expected_plugin_root)
            )
            if force_refresh or not already_current:
                activation["previousPluginVersion"] = str(
                    plugin.get("version") or ""
                )
                activation["previousPluginSource"] = dict(previous_source or {})
                previous_registry = _run_codex_list_json("mcp", "list")
                previous_mcp = next(
                    (
                        item
                        for item in previous_registry
                        if isinstance(item, Mapping)
                        and item.get("name") == "news_grasp_daily"
                    ),
                    None,
                )
                if not isinstance(previous_mcp, Mapping):
                    raise ValueError("daily_plugin_previous_mcp_registration_missing")
                activation["previousMcpRegistration"] = dict(previous_mcp)
                activation["previousToolNames"] = _run_registered_mcp_canary(
                    previous_mcp
                )
                activation["pluginRemoveAttempted"] = True
                _run_codex_json("plugin", "remove", DAILY_PLUGIN_ID)
                activation["pluginRemovedForRefresh"] = True
                plugin = None
        if plugin is None:
            activation["pluginAddAttempted"] = True
            _run_codex_json("plugin", "add", DAILY_PLUGIN_ID)
            activation["pluginAdded"] = True
            installed = _run_codex_json("plugin", "list").get("installed")
            plugin = next(
                (
                    item
                    for item in installed or ()
                    if isinstance(item, Mapping) and item.get("pluginId") == DAILY_PLUGIN_ID
                ),
                None,
            )

        source = plugin.get("source") if isinstance(plugin, Mapping) else None
        plugin_root = Path(str((source or {}).get("path") or ""))
        if (
            not isinstance(plugin, Mapping)
            or plugin.get("installed") is not True
            or plugin.get("enabled") is not True
            or plugin.get("marketplaceName") != DAILY_MARKETPLACE_NAME
            or plugin.get("version") != version
            or not isinstance(source, Mapping)
            or source.get("source") != "local"
            or not _same_path(plugin_root, expected_plugin_root)
        ):
            raise ValueError("daily_plugin_install_binding_invalid")

        hashes: dict[str, str] = {}
        for relative in DAILY_PLUGIN_RELATIVE_FILES:
            expected_bytes = _expected_daily_plugin_bytes(source_plugin_root, relative)
            installed_bytes = _read_bytes_no_follow(
                plugin_root / relative, limit=2 * 1024 * 1024
            )
            if installed_bytes != expected_bytes:
                raise ValueError(f"daily_plugin_installed_source_drift:{relative}")
            hashes[relative] = hashlib.sha256(installed_bytes).hexdigest()

        config = json.loads(
            _read_text_limited(plugin_root / ".mcp.json", limit=MAX_AUTOMATION_TOML_BYTES)
        )
        server = (config.get("mcpServers") or {}).get("news_grasp_daily")
        if not isinstance(server, Mapping):
            raise ValueError("daily_plugin_mcp_config_invalid")
        expected_command = str(server.get("command") or "")
        expected_arguments = [str(item) for item in server.get("args") or ()]
        expected_cwd_raw = str(server.get("cwd") or "")
        expected_cwd = (plugin_root / expected_cwd_raw).resolve(strict=True)
        expected_env_vars = [str(item) for item in server.get("env_vars") or ()]

        mcp_registry = _run_codex_list_json("mcp", "list")
        registered_mcp = next(
            (
                item
                for item in mcp_registry
                if isinstance(item, Mapping) and item.get("name") == "news_grasp_daily"
            ),
            None,
        )
        transport = (
            registered_mcp.get("transport")
            if isinstance(registered_mcp, Mapping)
            else None
        )
        registered_command = str((transport or {}).get("command") or "")
        registered_arguments = [str(item) for item in (transport or {}).get("args") or ()]
        registered_cwd_raw = str((transport or {}).get("cwd") or "")
        try:
            registered_cwd = Path(registered_cwd_raw).resolve(strict=True)
        except (OSError, ValueError):
            registered_cwd = Path()
        if (
            not isinstance(registered_mcp, Mapping)
            or registered_mcp.get("enabled") is not True
            or not isinstance(transport, Mapping)
            or transport.get("type") != "stdio"
            or not _same_path(Path(registered_command), Path(expected_command))
            or registered_arguments != expected_arguments
            or not _same_path(registered_cwd, expected_cwd)
            or transport.get("env") not in (None, {})
            or [str(item) for item in registered_mcp.get("env_vars") or ()]
            != expected_env_vars
        ):
            raise ValueError("daily_plugin_host_registry_transport_mismatch")
        tool_names = _run_registered_mcp_canary(registered_mcp)
        if tool_names != ["run_daily"]:
            raise ValueError("daily_plugin_loaded_canary_red")
        return {
            "schemaVersion": "NEWS_GRASP_DAILY_PLUGIN_ACTIVATION_V1",
            "ok": True,
            "status": "registered_and_canary_loaded",
            "pluginId": DAILY_PLUGIN_ID,
            "version": version,
            "marketplaceRoot": str(desired_marketplace_root),
            "installedRoot": str(plugin_root.resolve(strict=True)),
            "fileHashes": hashes,
            "sourceGeneration": generation,
            "generationFileHashes": generation_hashes,
            "hostRegistryMcp": "news_grasp_daily",
            "hostRegistryTransport": {
                "command": registered_command,
                "args": registered_arguments,
                "cwd": str(registered_cwd),
                "env": transport.get("env"),
                "env_vars": expected_env_vars,
            },
            "toolNames": tool_names,
            **activation,
        }
    except Exception as exc:
        return {
            "schemaVersion": "NEWS_GRASP_DAILY_PLUGIN_ACTIVATION_V1",
            "ok": False,
            "status": "activation_failed",
            "pluginId": DAILY_PLUGIN_ID,
            "marketplaceRoot": str(desired_marketplace_root),
            "failures": [f"{type(exc).__name__}:{exc}"],
            **activation,
        }


def _default_app_db() -> Path:
    return Path.home() / ".codex" / "sqlite" / "codex-dev.db"


def _default_app_global_state() -> Path:
    return Path.home() / ".codex" / ".codex-global-state.json"


def _default_snapshot(repo_root: Path) -> Path:
    snapshot_repo = repo_root
    try:
        canonical = CANONICAL_NEWS_GRASP_REPO_ROOT.expanduser().resolve(strict=True)
        if _same_git_repository(repo_root, canonical):
            snapshot_repo = canonical
    except (OSError, ValueError):
        pass
    return (
        snapshot_repo.parent
        / "AIHarnessState"
        / "snapshot"
        / "codex"
        / "automations"
        / AUTOMATION_ID
        / "automation.toml"
    )


def _default_shadow_snapshot() -> Path:
    return (
        Path.home()
        / ".codex"
        / "state"
        / "harness-worktrees"
        / "AIHarnessState-global-harness-v1"
        / "snapshot"
        / "codex"
        / "automations"
        / AUTOMATION_ID
        / "automation.toml"
    )


def _snapshot_targets(repo_root: Path, explicit_snapshot: Path | None) -> list[Path]:
    if explicit_snapshot is not None:
        return [explicit_snapshot]
    targets = [_default_snapshot(repo_root), _default_shadow_snapshot()]
    unique: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        key = str(target.expanduser().resolve(strict=False)).casefold()
        if key not in seen:
            unique.append(target)
            seen.add(key)
    return unique


def _has_reparse_point(path: Path) -> bool:
    try:
        attributes = int(getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attributes & 0x400)


def _exists_no_follow(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _is_regular_file_no_reparse(path: Path) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not _has_reparse_point(path)


def _open_windows_no_reparse(path: Path, *, directory: bool) -> tuple[int, int]:
    if os.name != "nt":  # pragma: no cover - Windows production path
        raise RuntimeError("windows_handle_unavailable")
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    handle = _CreateFileW(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed: {path}")
    info = BY_HANDLE_FILE_INFORMATION()
    try:
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), f"GetFileInformationByHandle failed: {path}")
        attrs = int(info.dwFileAttributes)
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"unsafe_reparse_path:{path}")
        is_directory = bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
        if directory != is_directory:
            raise ValueError(f"unexpected_path_type:{path}")
        size = (int(info.nFileSizeHigh) << 32) + int(info.nFileSizeLow)
        return int(handle), size
    except Exception:
        _CloseHandle(handle)
        raise


@contextlib.contextmanager
def _directory_guard(path: Path):
    _assert_no_reparse_chain(path)
    if os.name == "nt":
        handle, _size = _open_windows_no_reparse(path, directory=True)
        try:
            yield
        finally:
            _CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        yield
    finally:
        os.close(fd)


@contextlib.contextmanager
def _existing_file_guard(path: Path, *, require_exists: bool = False):
    _assert_no_reparse_chain(path)
    if not _exists_no_follow(path):
        if require_exists:
            raise FileNotFoundError(path)
        yield None
        return
    if os.name == "nt":
        handle, _size = _open_windows_no_reparse(path, directory=False)
        try:
            yield handle
        finally:
            _CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        yield fd
    finally:
        os.close(fd)


def _read_bytes_no_follow(path: Path, *, limit: int) -> bytes:
    _assert_no_reparse_chain(path)
    if os.name == "nt":
        handle, size = _open_windows_no_reparse(path, directory=False)
        if size > limit:
            _CloseHandle(handle)
            raise ValueError(f"file_too_large:{path}")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
    else:  # pragma: no cover - Windows production path
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"file_too_large:{path}")
    return data


def _assert_no_reparse_chain(path: Path) -> None:
    expanded = path.expanduser()
    probe = expanded if _exists_no_follow(expanded) else expanded.parent
    while not _exists_no_follow(probe) and probe != probe.parent:
        probe = probe.parent
    for item in (probe, *probe.parents):
        if item.is_symlink() or _has_reparse_point(item):
            raise ValueError(f"unsafe_reparse_path:{path}")


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _approved_roots(repo_root: Path) -> tuple[Path, ...]:
    candidates = (
        repo_root,
        repo_root.parent / "AIHarnessState",
        CANONICAL_NEWS_GRASP_REPO_ROOT.parent / "AIHarnessState",
        DAILY_PLUGIN_ROOT,
        Path.home() / ".codex",
        Path(tempfile.gettempdir()),
    )
    roots: list[Path] = []
    for candidate in candidates:
        try:
            _assert_no_reparse_chain(candidate)
            roots.append(candidate.expanduser().resolve(strict=False))
        except (OSError, ValueError):
            continue
    return tuple(roots)


def _assert_approved_path(path: Path, *, repo_root: Path, label: str) -> Path:
    _assert_no_reparse_chain(path)
    resolved = path.expanduser().resolve(strict=False)
    roots = _approved_roots(repo_root)
    if not any(_is_relative_to(resolved, root) for root in roots):
        raise ValueError(f"path_outside_approved_root:{label}:{resolved}")
    return resolved


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.expanduser().resolve(strict=False))) == os.path.normcase(
        str(right.expanduser().resolve(strict=False))
    )


def _git_common_dir(repo_root: Path) -> Path | None:
    """git worktreeをdereferenceせずcommon dirへ束縛する。"""

    root = repo_root.expanduser().resolve(strict=True)
    git_entry = root / ".git"
    if git_entry.is_dir() and not git_entry.is_symlink() and not _has_reparse_point(git_entry):
        return git_entry.resolve(strict=True)
    if not _is_regular_file_no_reparse(git_entry):
        return None
    marker = _read_bytes_no_follow(git_entry, limit=4096).decode("utf-8-sig").strip()
    if not marker.casefold().startswith("gitdir:"):
        return None
    git_dir_text = marker.split(":", 1)[1].strip()
    if not git_dir_text:
        return None
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    _assert_no_reparse_chain(git_dir)
    git_dir = git_dir.resolve(strict=True)
    common_marker = git_dir / "commondir"
    if not _is_regular_file_no_reparse(common_marker):
        return git_dir
    common_text = _read_bytes_no_follow(common_marker, limit=4096).decode("utf-8-sig").strip()
    if not common_text:
        return None
    common = Path(common_text)
    if not common.is_absolute():
        common = git_dir / common
    _assert_no_reparse_chain(common)
    return common.resolve(strict=True)


def _same_git_repository(left: Path, right: Path) -> bool:
    try:
        left_common = _git_common_dir(left)
        right_common = _git_common_dir(right)
    except (OSError, UnicodeError, ValueError):
        return False
    return (
        left_common is not None
        and right_common is not None
        and _same_path(left_common, right_common)
    )


def _endswith_parts(path: Path, suffix: tuple[str, ...]) -> bool:
    folded_parts = tuple(part.casefold() for part in path.parts)
    folded_suffix = tuple(part.casefold() for part in suffix)
    return len(folded_parts) >= len(folded_suffix) and folded_parts[-len(folded_suffix) :] == folded_suffix


def _under_temp(path: Path) -> bool:
    return _is_relative_to(path.expanduser().resolve(strict=False), Path(tempfile.gettempdir()).resolve(strict=False))


def _role_default_paths(repo_root: Path, label: str) -> tuple[Path, ...]:
    if label == "template":
        return (_default_template(repo_root),)
    if label == "installed":
        return (_default_installed(),)
    if label == "snapshot":
        return (_default_snapshot(repo_root), _default_shadow_snapshot())
    if label == "source_skill":
        return (_default_source_skill(repo_root),)
    if label == "installed_skill":
        return (_default_installed_skill(),)
    if label == "app_db":
        return (_default_app_db(),)
    return ()


def _fixture_role_path_allowed(path: Path, label: str) -> bool:
    if os.environ.get("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS") != "1":
        return False
    if TEST_FIXTURE_ROOT_NAME not in path.parts:
        return False
    if not _under_temp(path):
        return False
    if label == "installed":
        return path.name == "automation.toml"
    if label == "snapshot":
        return _endswith_parts(path, ("snapshot", "codex", "automations", AUTOMATION_ID, "automation.toml"))
    if label == "installed_skill":
        return _endswith_parts(path, ("skills", "news-grasp-direct-mainline", "SKILL.md"))
    if label == "app_db":
        return path.name == "codex-dev.db"
    return False


def _assert_role_path(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    custom_allowed: bool,
) -> Path:
    resolved = _assert_approved_path(path, repo_root=repo_root, label=label)
    if any(_same_path(resolved, default) for default in _role_default_paths(repo_root, label)):
        return resolved
    if custom_allowed and _fixture_role_path_allowed(resolved, label):
        return resolved
    raise ValueError(f"path_not_allowed_for_role:{label}:{resolved}")


def _assert_trusted_repo_root(path: Path, *, read_only: bool = False) -> Path:
    _assert_no_reparse_chain(path)
    resolved = path.expanduser().resolve(strict=True)
    canonical = CANONICAL_NEWS_GRASP_REPO_ROOT.expanduser().resolve(strict=True)
    same_worktree = _same_git_repository(resolved, canonical)
    clean_release_worktree = False
    if same_worktree and not _same_path(resolved, canonical) and not read_only:
        staging_root = Path(r"C:\ngstage").resolve(strict=True)
        try:
            resolved.relative_to(staging_root)
        except ValueError:
            clean_release_worktree = False
        else:
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

            def git_text(*args: str) -> tuple[int, str]:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=str(resolved),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    check=False,
                    timeout=30,
                    creationflags=creationflags,
                )
                try:
                    stdout = bytes(completed.stdout or b"").decode("utf-8", errors="strict").strip()
                except UnicodeDecodeError:
                    return 1, ""
                return int(completed.returncode), stdout

            top_rc, top = git_text("rev-parse", "--show-toplevel")
            head_rc, head = git_text("rev-parse", "HEAD")
            remote_rc, remote_head = git_text("rev-parse", "refs/remotes/origin/main")
            status_rc, status_text = git_text("status", "--porcelain", "--untracked-files=all")
            clean_release_worktree = (
                top_rc == head_rc == remote_rc == status_rc == 0
                and _same_path(Path(top), resolved)
                and bool(head)
                and head == remote_head
                and not status_text
            )
    if not _same_path(resolved, canonical) and not (
        same_worktree
        and (
            read_only
            or clean_release_worktree
            or os.environ.get("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS") == "1"
        )
    ):
        raise ValueError("repo_root_not_canonical_news_grasp")
    required = (
        resolved / "automation" / "news-grasp-6-40" / "automation.toml.template",
        resolved / "tools" / "news_grasp_direct_runtime.py",
        resolved / "tools" / "sync_news_grasp_codex_automation.py",
    )
    if any(not _is_regular_file_no_reparse(item) for item in required):
        raise ValueError("repo_root_not_trusted_news_grasp")
    return resolved


def _read_text_limited(path: Path, *, limit: int) -> str:
    return _read_bytes_no_follow(path, limit=limit).decode("utf-8-sig")


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(_read_text_limited(path, limit=MAX_AUTOMATION_TOML_BYTES))


def _resolve_app_project_target(repo_root: Path) -> dict[str, str]:
    path = _default_app_global_state()
    try:
        value = json.loads(_read_text_limited(path, limit=MAX_APP_GLOBAL_STATE_BYTES))
    except FileNotFoundError as exc:
        raise ValueError("app_project_registry_missing") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("app_project_registry_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("app_project_registry_invalid")
    projects = value.get("local-projects")
    if not isinstance(projects, dict):
        raise ValueError("app_project_registry_invalid")

    matches: list[str] = []
    for registry_id, row in projects.items():
        if not isinstance(registry_id, str) or not registry_id or not isinstance(row, dict):
            continue
        project_id = row.get("id")
        roots = row.get("rootPaths")
        if project_id != registry_id or not isinstance(roots, list):
            continue
        if any(
            isinstance(root, str)
            and root
            and (
                _same_path(Path(root), repo_root)
                or _same_git_repository(Path(root), repo_root)
            )
            for root in roots
        ):
            matches.append(project_id)
    if not matches:
        raise ValueError("app_project_binding_missing")
    if len(matches) != 1:
        raise ValueError("app_project_binding_ambiguous")
    return {"type": "project", "project_id": matches[0]}


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inline_target(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    target_type = value.get("type")
    project_id = value.get("project_id")
    if not isinstance(target_type, str) or not isinstance(project_id, str):
        return None
    return f"target = {{ type = {_quote(target_type)}, project_id = {_quote(project_id)} }}"


def _render_installed(
    *,
    template_path: Path,
    installed_path: Path,
    repo_root: Path,
    project_target: dict[str, str],
    now_ms: int | None = None,
) -> str:
    template = _load_toml(template_path)
    installed = _load_toml(installed_path) if installed_path.is_file() else {}
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    created_at = installed.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at <= 0:
        created_at = now_ms
    target_line = _inline_target(project_target)

    prompt = str(template.get("prompt") or "").strip()
    # multiline basic stringでも、固定Windows実行パスのバックスラッシュを
    # TOML escapeとして誤解釈させず、installed側のprompt bytesを正本値へ戻す。
    toml_prompt = prompt.replace("\\", "\\\\")
    repo_cwd = str(repo_root.resolve(strict=True))
    template_name = str(template.get("name") or TITLE_SUFFIX)
    installed_name = installed.get("name")
    materialized_name = (
        installed_name
        if isinstance(installed_name, str) and TITLE_PATTERN.fullmatch(installed_name)
        else template_name
    )

    def build(updated_at: int) -> str:
        lines = [
            "version = 1",
            f"id = {_quote(AUTOMATION_ID)}",
            f"kind = {_quote(str(template.get('kind') or 'cron'))}",
            f"name = {_quote(materialized_name)}",
            f"status = {_quote(str(template.get('status') or 'ACTIVE'))}",
            f"rrule = {_quote(str(template.get('rrule') or 'RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0'))}",
            f"model = {_quote('gpt-5.6-luna')}",
            f"reasoning_effort = {_quote('max')}",
            f"execution_environment = {_quote(str(template.get('execution_environment') or 'local'))}",
        ]
        if target_line:
            lines.append(target_line)
        lines.extend(
            [
                f"cwds = [{_quote(repo_cwd)}]",
                f"created_at = {created_at}",
                f"updated_at = {updated_at}",
                "prompt = \"\"\"",
                toml_prompt,
                "\"\"\"",
                "",
            ]
        )
        return "\n".join(lines)

    installed_updated_at = installed.get("updated_at")
    if isinstance(installed_updated_at, int) and not isinstance(installed_updated_at, bool):
        candidate = build(max(installed_updated_at, created_at))
        if installed_path.is_file() and _read_text_limited(installed_path, limit=MAX_AUTOMATION_TOML_BYTES) == candidate:
            return candidate
    return build(max(now_ms, created_at))


def _atomic_write_text(path: Path, text: str) -> None:
    _assert_no_reparse_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_chain(path.parent)
    with _directory_guard(path.parent):
        with _existing_file_guard(path):
            if path.exists() and not _is_regular_file_no_reparse(path):
                raise ValueError(f"unsafe_write_target:{path}")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            _assert_no_reparse_chain(path)
            _assert_no_reparse_chain(path.parent)
            with _existing_file_guard(path):
                if path.exists() and not _is_regular_file_no_reparse(path):
                    raise ValueError(f"unsafe_write_target:{path}")
            os.replace(temp_path, path)
            _assert_no_reparse_chain(path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    """backup復元用にexact bytesをatomic replaceする。"""

    _assert_no_reparse_chain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_chain(path.parent)
    with _directory_guard(path.parent):
        with _existing_file_guard(path):
            if path.exists() and not _is_regular_file_no_reparse(path):
                raise ValueError(f"unsafe_write_target:{path}")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            _assert_no_reparse_chain(path)
            _assert_no_reparse_chain(path.parent)
            with _existing_file_guard(path):
                if path.exists() and not _is_regular_file_no_reparse(path):
                    raise ValueError(f"unsafe_write_target:{path}")
            os.replace(temp_path, path)
            _assert_no_reparse_chain(path)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def _app_db_schema_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    """App DB の対象表と列を検証し、CASで使う列順を返す。"""

    try:
        info = conn.execute("PRAGMA table_xinfo(automations)").fetchall()
    except sqlite3.Error as exc:
        raise _AppDbSchemaDrift("app_db_schema_unreadable") from exc
    if not info:
        raise _AppDbSchemaDrift("app_db_schema_missing")

    columns: list[str] = []
    for row in info:
        name = row[1]
        if not isinstance(name, str) or not name:
            raise _AppDbSchemaDrift("app_db_schema_column_name_invalid")
        if len(row) >= 7 and int(row[6] or 0) != 0:
            raise _AppDbSchemaDrift("app_db_schema_hidden_column_unsupported")
        columns.append(name)
    if len(columns) != len(set(columns)):
        raise _AppDbSchemaDrift("app_db_schema_duplicate_column")
    missing = sorted(set(APP_DB_AUTOMATION_OWNED_COLUMNS) - set(columns))
    unknown = sorted(set(columns) - set(APP_DB_AUTOMATION_SCHEMA_COLUMNS))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise _AppDbSchemaDrift("app_db_schema_drift:" + ";".join(details))

    id_rows = [row for row in info if row[1] == "id"]
    if len(id_rows) != 1 or int(id_rows[0][5] or 0) != 1:
        raise _AppDbSchemaDrift("app_db_schema_id_primary_key_required")
    return tuple(columns)


def _app_db_row_value_for_hash(value: Any) -> Any:
    """SQLite値を秘密値を含まないcanonical hash入力へ変換する。"""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _AppDbSchemaDrift("app_db_row_non_finite_value")
        return value
    if isinstance(value, bytes):
        return {
            "type": "sqlite_blob",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    raise _AppDbSchemaDrift(f"app_db_row_value_unsupported:{type(value).__name__}")


def _app_db_row_hash(
    row: Mapping[str, Any] | None,
    columns: tuple[str, ...],
) -> str:
    """列名を含む行のcanonical SHA-256を返す。"""

    if row is None:
        return ""
    row_keys = set(row.keys())
    if row_keys != set(columns):
        raise _AppDbSchemaDrift("app_db_row_columns_drift")
    payload = {
        "schemaVersion": APP_DB_ROW_HASH_SCHEMA_VERSION,
        "columns": list(columns),
        "row": {
            column: _app_db_row_value_for_hash(row[column])
            for column in columns
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fetch_app_db_automation_row(
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """対象 automation row を一意に取得する。"""

    rows = conn.execute(
        "SELECT * FROM automations WHERE id = ?",
        (AUTOMATION_ID,),
    ).fetchall()
    if len(rows) > 1:
        raise _AppDbSchemaDrift("app_db_automation_row_not_unique")
    return None if not rows else dict(rows[0])


def _new_app_db_promotion_target(path: Path) -> dict[str, Any]:
    """App DB専用のrow-CAS promotion targetを作る。"""

    return {
        "target": str(path),
        "kind": "app_db",
        "automationId": AUTOMATION_ID,
        "preimagePresent": False,
        "preimageColumns": [],
        "preimageHash": "",
        "preimageSha256": "",
        "candidateHash": "",
        "candidateSha256": "",
        "postimageHash": "",
        "postimageSha256": "",
        "backupStatus": "row_captured",
        "atomic": True,
        "rollbackCas": {
            "status": "not_attempted",
            "automationId": AUTOMATION_ID,
        },
        "status": "pending",
    }


def _capture_promotion_target(path: Path, *, kind: str) -> dict[str, Any]:
    """promotion前のbytesをmemory backupとして固定する。"""

    if kind == "app_db":
        raise ValueError("app_db_row_cas_required")
    try:
        preimage = _read_bytes_no_follow(path, limit=MAX_PROMOTION_BACKUP_BYTES)
        present = True
    except FileNotFoundError:
        preimage = b""
        present = False
    return {
        "target": str(path),
        "kind": kind,
        "preimagePresent": present,
        "preimageSha256": hashlib.sha256(preimage).hexdigest() if present else "",
        "preimageBytes": preimage,
        "candidateSha256": "",
        "postimageSha256": "",
        "backupStatus": "captured",
        "atomic": True,
        "status": "pending",
    }


def _promote_text_target(
    target: dict[str, Any],
    text: str,
    *,
    promoted: list[dict[str, Any]],
) -> bool:
    """candidateを明示promotionし、失敗時は同runのbackupへ戻す。"""

    if target.get("kind") == "app_db":
        raise ValueError("app_db_row_cas_required")
    path = Path(str(target["target"]))
    encoded = text.encode("utf-8")
    target["candidateSha256"] = hashlib.sha256(encoded).hexdigest()
    if target["preimagePresent"] and target["preimageBytes"] == encoded:
        target["status"] = "noop"
        target["postimageSha256"] = target["preimageSha256"]
        return False
    try:
        _atomic_write_text(path, text)
        postimage = _read_bytes_no_follow(path, limit=MAX_PROMOTION_BACKUP_BYTES)
        target["postimageSha256"] = hashlib.sha256(postimage).hexdigest()
        if postimage != encoded:
            raise RuntimeError("promotion_postimage_mismatch")
        target["status"] = "promoted"
        promoted.append(target)
        return True
    except Exception as exc:
        target["promotionError"] = f"{type(exc).__name__}:{exc}"
        target["rollbackReceipt"] = _rollback_promotion_targets([*promoted, target])
        raise


def _rollback_app_db_target(target: Mapping[str, Any]) -> dict[str, Any]:
    """App DBの対象automation rowだけを、hash付きCASで復元する。"""

    raw_path = target.get("target")
    path = Path(raw_path) if isinstance(raw_path, str) and raw_path.strip() else None
    automation_id = target.get("automationId")
    receipt: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_ROLLBACK_RECEIPT_V1",
        "status": "blocked",
        "ok": False,
        "automationId": automation_id,
        "preimageHash": str(target.get("preimageHash") or ""),
        "postimageHash": str(target.get("postimageHash") or ""),
        "cas": {
            "status": "not_started",
            "automationId": automation_id,
        },
        "failures": [],
    }
    if automation_id != AUTOMATION_ID:
        receipt["failures"] = ["app_db_rollback_automation_id_mismatch"]
        return receipt
    if path is None:
        receipt["failures"] = ["app_db_rollback_target_missing"]
        return receipt
    expected_post_hash = str(target.get("postimageHash") or "")
    preimage_hash = str(target.get("preimageHash") or "")
    columns_raw = target.get("preimageColumns")
    preimage_row = target.get("_preimageRow")
    preimage_present = target.get("preimagePresent") is True
    if (
        not expected_post_hash
        or not isinstance(columns_raw, list)
        or not columns_raw
        or any(not isinstance(column, str) or not column for column in columns_raw)
    ):
        receipt["failures"] = ["app_db_rollback_receipt_incomplete"]
        return receipt
    columns = tuple(columns_raw)
    if tuple(dict.fromkeys(columns)) != columns:
        receipt["failures"] = ["app_db_rollback_receipt_columns_drift"]
        return receipt
    if preimage_present and not isinstance(preimage_row, Mapping):
        receipt["failures"] = ["app_db_rollback_preimage_missing"]
        return receipt
    if not preimage_present and preimage_hash:
        receipt["failures"] = ["app_db_rollback_absent_preimage_hash"]
        return receipt

    conn: sqlite3.Connection | None = None
    began = False
    try:
        _assert_no_reparse_chain(path)
        with _existing_file_guard(path, require_exists=True):
            conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("BEGIN IMMEDIATE")
        began = True
        actual_columns = _app_db_schema_columns(conn)
        if actual_columns != columns:
            raise _AppDbSchemaDrift("app_db_rollback_schema_drift")
        current = _fetch_app_db_automation_row(conn)
        if current is None:
            receipt["cas"] = {
                "status": "row_missing",
                "automationId": AUTOMATION_ID,
                "expectedPostimageHash": expected_post_hash,
                "currentHash": "",
            }
            conn.rollback()
            began = False
            receipt["failures"] = ["app_db_rollback_row_missing"]
            return receipt
        current_hash = _app_db_row_hash(current, actual_columns)
        receipt["cas"] = {
            "status": "compare",
            "automationId": AUTOMATION_ID,
            "expectedPostimageHash": expected_post_hash,
            "currentHash": current_hash,
        }
        if current_hash != expected_post_hash:
            conn.rollback()
            began = False
            receipt["cas"]["status"] = "mismatch"
            receipt["failures"] = ["app_db_rollback_postimage_mismatch"]
            return receipt

        if preimage_present:
            assert isinstance(preimage_row, Mapping)
            if set(preimage_row.keys()) != set(actual_columns):
                raise _AppDbSchemaDrift("app_db_rollback_preimage_schema_drift")
            assignments = ", ".join(
                f"{column} = ?"
                for column in actual_columns
                if column != "id"
            )
            values = [preimage_row[column] for column in actual_columns if column != "id"]
            values.append(AUTOMATION_ID)
            cursor = conn.execute(
                f"UPDATE automations SET {assignments} WHERE id = ?",
                tuple(values),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("app_db_rollback_row_update_count")
        else:
            cursor = conn.execute(
                "DELETE FROM automations WHERE id = ?",
                (AUTOMATION_ID,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("app_db_rollback_row_delete_count")

        restored = _fetch_app_db_automation_row(conn)
        if preimage_present:
            if restored is None or _app_db_row_hash(restored, actual_columns) != preimage_hash:
                raise RuntimeError("app_db_rollback_preimage_mismatch")
        elif restored is not None:
            raise RuntimeError("app_db_rollback_absent_preimage_not_restored")
        conn.commit()
        began = False
        receipt["cas"] = {
            **receipt["cas"],
            "status": "restored",
            "restoredHash": preimage_hash,
        }
        receipt["status"] = "rolled_back"
        receipt["ok"] = True
        return receipt
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        if conn is not None and began and conn.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
        receipt["cas"] = {
            **receipt.get("cas", {}),
            "status": "blocked",
        }
        if isinstance(exc, _AppDbSchemaDrift):
            receipt["failures"] = [str(exc)]
        else:
            receipt["failures"] = [f"app_db_rollback_failed:{type(exc).__name__}:{exc}"]
        return receipt
    finally:
        if conn is not None:
            conn.close()


def _rollback_promotion_targets(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """promotion backupを明示的に復元し、rollback receiptを返す。"""

    failures: list[str] = []
    restored: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in reversed(targets):
        if target.get("kind") == "app_db":
            app_db_receipt = _rollback_app_db_target(target)
            target["rollbackCas"] = app_db_receipt.get("cas", {})
            target["rollbackReceipt"] = app_db_receipt
            if app_db_receipt.get("ok") is True:
                target["status"] = "rolled_back"
                restored.append(
                    {
                        "target": target.get("target"),
                        "kind": "app_db",
                        "automationId": target.get("automationId"),
                        "preimageHash": target.get("preimageHash", ""),
                        "postimageHash": target.get("postimageHash", ""),
                        "rollbackCas": app_db_receipt.get("cas", {}),
                        "status": "rolled_back",
                        "atomic": True,
                    }
                )
            else:
                target["status"] = "rollback_failed"
                failures.extend(str(item) for item in app_db_receipt.get("failures") or [])
            continue
        path = Path(str(target.get("target") or ""))
        key = str(path.expanduser().resolve(strict=False)).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            if target.get("preimagePresent") is True:
                _atomic_write_bytes(path, bytes(target.get("preimageBytes") or b""))
                post = _read_bytes_no_follow(path, limit=MAX_PROMOTION_BACKUP_BYTES)
                post_sha = hashlib.sha256(post).hexdigest()
                if post_sha != target.get("preimageSha256"):
                    raise RuntimeError("rollback_postimage_mismatch")
            elif _exists_no_follow(path):
                _assert_no_reparse_chain(path)
                path.unlink()
                post_sha = ""
            else:
                post_sha = ""
            target["status"] = "rolled_back"
            target["rollbackPostimageSha256"] = post_sha
            restored.append(
                {
                    "target": target.get("target"),
                    "kind": target.get("kind"),
                    "preimageSha256": target.get("preimageSha256", ""),
                    "postimageSha256": post_sha,
                    "status": "rolled_back",
                    "atomic": True,
                }
            )
        except Exception as exc:  # noqa: BLE001 - receipt records typed recovery failure.
            failures.append(f"{target.get('target')}:{exc}")
            target["status"] = "rollback_failed"
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_ROLLBACK_RECEIPT_V1",
        "status": "rolled_back" if not failures else "blocked",
        "ok": not failures,
        "targets": restored,
        "failures": failures,
    }


def _promotion_target_projection(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in target.items()
        if key not in {"preimageBytes", "_preimageRow", "_postimageRow"}
    }


def _promotion_receipt(
    *,
    promotion_id: str,
    targets: list[dict[str, Any]],
    dry_run: bool,
    status: str,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_PROMOTION_RECEIPT_V1",
        "promotionId": promotion_id,
        "mode": "dry_run" if dry_run else "explicit",
        "startAutoRepair": False,
        "status": status,
        "ok": not failures,
        "atomic": True,
        "targets": [_promotion_target_projection(target) for target in targets],
        "failures": sorted(set(failures or [])),
    }


def _custom_path_args(
    *,
    repo_root: Path | None,
    template_path: Path | None,
    installed_path: Path | None,
    snapshot_path: Path | None,
    source_skill_path: Path | None,
    installed_skill_path: Path | None,
    app_db_path: Path | None,
) -> dict[str, bool]:
    return {
        "repo_root": repo_root is not None and repo_root.resolve(strict=False) != _default_repo_root().resolve(strict=True),
        "template": template_path is not None,
        "installed": installed_path is not None,
        "snapshot": snapshot_path is not None,
        "source_skill": source_skill_path is not None,
        "installed_skill": installed_skill_path is not None,
        "app_db": app_db_path is not None,
    }


def _custom_path_error(custom_path_args: dict[str, bool]) -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": False,
        "dry_run": False,
        "changed": False,
        "snapshot_changed": False,
        "skill_changed": False,
        "app_db_changed": False,
        "failures": [
            "custom_path_override_requires_explicit_allow_custom_paths"
        ],
        "custom_path_args": {
            key: value for key, value in custom_path_args.items() if value
        },
    }


def _validate_loaded_automation(
    value: dict[str, Any],
    *,
    path: Path,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []

    if expected_target is None:
        try:
            expected_target = _resolve_app_project_target(repo_root)
        except ValueError as exc:
            failures.append(str(exc))
            expected_target = None

    if value.get("id") != AUTOMATION_ID:
        failures.append("id_invalid")
    name = value.get("name")
    if not isinstance(name, str) or (name != TITLE_SUFFIX and not TITLE_PATTERN.fullmatch(name)):
        failures.append("name_not_canonical_or_materialized")
    if value.get("status") != "ACTIVE":
        failures.append("status_not_active")
    if str(value.get("rrule") or "").upper() != "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0":
        failures.append("schedule_not_0600")
    if str(value.get("model") or "").casefold() != "gpt-5.6-luna":
        failures.append("model_not_luna")
    if value.get("reasoning_effort") != "max":
        failures.append("reasoning_not_max")
    nested_target = value.get("target")
    if isinstance(nested_target, dict):
        target_type = nested_target.get("type")
        project_id = nested_target.get("project_id")
    else:
        target_type = value.get("target_type")
        project_id = value.get("project_id")
    if expected_target is not None:
        if target_type != expected_target["type"]:
            failures.append("target_type_not_bound_to_app_project")
        if project_id != expected_target["project_id"]:
            failures.append("project_id_not_bound_to_app_project")
    cwds = value.get("cwds")
    if not isinstance(cwds, list) or not any(
        _same_path(Path(str(item)), repo_root)
        or _same_git_repository(Path(str(item)), repo_root)
        for item in cwds
    ):
        failures.append("cwd_not_bound_to_repo")
    prompt = str(value.get("prompt") or "")
    for part in REQUIRED_PROMPT_PARTS:
        if part not in prompt:
            failures.append(f"prompt_missing:{part}")
    if "public incomplete のまま最終応答しないでください" in prompt:
        failures.append("prompt_external_blocker_boundary_ambiguous")
    if "最初に `python -m tools.news_grasp_direct_runtime start" in prompt:
        failures.append("prompt_title_runtime_order_ambiguous")
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": not failures,
        "path": str(path),
        "failures": failures,
        "model": value.get("model"),
        "reasoning_effort": value.get("reasoning_effort"),
        "target_type": target_type,
        "project_id": project_id,
        "rrule": value.get("rrule"),
        "prompt_length": len(prompt),
        "updated_at": value.get("updated_at"),
    }


def validate_semantics(
    path: Path,
    *,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        _assert_approved_path(
            path,
            repo_root=_assert_trusted_repo_root(repo_root, read_only=True),
            label="automation",
        )
        value = _load_toml(path)
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["automation_missing"]}
    except (tomllib.TOMLDecodeError, UnicodeError, ValueError) as exc:
        return {"ok": False, "path": str(path), "failures": [f"automation_invalid:{exc}"]}
    return _validate_loaded_automation(
        value,
        path=path,
        repo_root=repo_root,
        expected_target=expected_target,
    )


def validate_integrity_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """template/installed/App DB/snapshotのprompt整合性を純粋に検証する。

    この関数は同期・自動修復・外部状態の変更を行わず、呼び出し側が渡した
    観測bundleだけを判定する。promptの一部一致やTOML本文の置換をGreenへ
    昇格させず、三つの運用区分に同じ必須文言が一度ずつあることを要求する。
    """

    reasons: list[str] = []
    if not isinstance(bundle, Mapping):
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_INTEGRITY_V1",
            "ok": False,
            "status": "blocked",
            "reasonCodes": ["automation_prompt_drift_fail_closed"],
        }

    template_prompt = bundle.get("templatePrompt")
    installed_prompt = bundle.get("installedPrompt")
    app_db_prompt = bundle.get("appDbPrompt")
    snapshots = bundle.get("snapshotPrompts")
    if not isinstance(template_prompt, str) or not template_prompt:
        reasons.append("template_prompt_missing")
    if not isinstance(installed_prompt, str) or installed_prompt != template_prompt:
        reasons.append("installed_prompt_drift")
    if not isinstance(app_db_prompt, str) or app_db_prompt != template_prompt:
        reasons.append("app_db_prompt_drift")
    if not isinstance(snapshots, list) or not snapshots:
        reasons.append("snapshot_prompt_missing")
    elif any(not isinstance(prompt, str) or prompt != template_prompt for prompt in snapshots):
        reasons.append("snapshot_prompt_drift")

    required_phrase = bundle.get("requiredPhrase")
    if required_phrase != AUTOMATION_COMPLETION_PHRASE:
        reasons.append("required_phrase_invalid")

    if isinstance(template_prompt, str):
        if template_prompt.count(AUTOMATION_COMPLETION_PHRASE) != 3:
            reasons.append("required_phrase_total_count_invalid")
        section_bounds = (
            ("最優先事項", "完了条件"),
            ("完了条件", "禁止"),
            ("禁止", "最終報告"),
        )
        for start_marker, end_marker in section_bounds:
            start = template_prompt.find(start_marker)
            end = template_prompt.find(end_marker, start + len(start_marker)) if start >= 0 else -1
            section = template_prompt[start:end if end >= 0 else len(template_prompt)] if start >= 0 else ""
            if section.count(AUTOMATION_COMPLETION_PHRASE) != 1:
                reasons.append(f"required_phrase_section_invalid:{start_marker}")

    toml_body = bundle.get("tomlBody")
    if not isinstance(toml_body, str) or not toml_body:
        reasons.append("toml_body_missing")
    else:
        try:
            parsed_toml = tomllib.loads(toml_body)
        except (tomllib.TOMLDecodeError, UnicodeError, TypeError):
            reasons.append("toml_body_invalid")
        else:
            if parsed_toml.get("prompt") != template_prompt:
                reasons.append("toml_prompt_replacement_forbidden")

    if bundle.get("startAutoRepair") is not False:
        reasons.append("start_auto_repair_forbidden")
    if reasons:
        reason_codes = ["automation_prompt_drift_fail_closed"]
    else:
        reason_codes = []
    digest = hashlib.sha256(template_prompt.encode("utf-8")).hexdigest() if isinstance(template_prompt, str) else ""
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_INTEGRITY_V1",
        "ok": not reasons,
        "status": "verified" if not reasons else "blocked",
        "reasonCodes": reason_codes,
        "detailCodes": sorted(set(reasons)),
        "promptSha256": digest,
        "snapshotCount": len(snapshots) if isinstance(snapshots, list) else 0,
        "startAutoRepair": bundle.get("startAutoRepair"),
    }


def observe_integrity_bundle(
    *,
    repo_root: Path | None = None,
    template_path: Path | None = None,
    installed_path: Path | None = None,
    app_db_path: Path | None = None,
    snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """source/installed/App DB/snapshotを別々にread-only観測する。"""

    repo = _assert_trusted_repo_root(repo_root or _default_repo_root(), read_only=True)
    template = _assert_role_path(
        template_path or _default_template(repo),
        repo_root=repo,
        label="template",
        custom_allowed=False,
    )
    installed = _assert_role_path(
        installed_path or _default_installed(),
        repo_root=repo,
        label="installed",
        custom_allowed=False,
    )
    app_db = _assert_role_path(
        app_db_path or _default_app_db(),
        repo_root=repo,
        label="app_db",
        custom_allowed=False,
    )
    snapshots = [
        _assert_role_path(path, repo_root=repo, label="snapshot", custom_allowed=False)
        for path in _snapshot_targets(repo, snapshot_path)
    ]
    template_bytes = _read_bytes_no_follow(template, limit=MAX_AUTOMATION_TOML_BYTES)
    installed_bytes = _read_bytes_no_follow(installed, limit=MAX_AUTOMATION_TOML_BYTES)
    template_value = tomllib.loads(template_bytes.decode("utf-8-sig"))
    installed_value = tomllib.loads(installed_bytes.decode("utf-8-sig"))
    snapshot_values: list[dict[str, Any]] = []
    snapshot_hashes: list[dict[str, str]] = []
    for path in snapshots:
        raw = _read_bytes_no_follow(path, limit=MAX_AUTOMATION_TOML_BYTES)
        snapshot_values.append(tomllib.loads(raw.decode("utf-8-sig")))
        snapshot_hashes.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
    with _existing_file_guard(app_db, require_exists=True):
        connection = sqlite3.connect(f"file:{app_db.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "select * from automations where id = ?",
                (AUTOMATION_ID,),
            ).fetchone()
        finally:
            connection.close()
    app_value = _automation_value_from_app_db_row(row)
    if app_value is None:
        raise ValueError("app_db_automation_missing")
    runtime_path = repo / "tools" / "news_grasp_direct_runtime.py"
    runtime_text = _read_text_limited(runtime_path, limit=2 * 1024 * 1024)
    auto_repair_call = "config_repair = _repair_installed_automation_config_once(" in runtime_text
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_INTEGRITY_OBSERVATION_V1",
        "templatePrompt": str(template_value.get("prompt") or ""),
        "installedPrompt": str(installed_value.get("prompt") or ""),
        "appDbPrompt": str(app_value.get("prompt") or ""),
        "snapshotPrompts": [str(value.get("prompt") or "") for value in snapshot_values],
        "requiredPhrase": AUTOMATION_COMPLETION_PHRASE,
        "tomlBody": template_bytes.decode("utf-8-sig"),
        "startAutoRepair": auto_repair_call,
        "surfaceHashes": {
            "template": hashlib.sha256(template_bytes).hexdigest(),
            "installed": hashlib.sha256(installed_bytes).hexdigest(),
            "appDbPrompt": hashlib.sha256(
                str(app_value.get("prompt") or "").encode("utf-8")
            ).hexdigest(),
            "snapshots": snapshot_hashes,
            "runtimeSource": hashlib.sha256(runtime_text.encode("utf-8")).hexdigest(),
        },
    }


def validate_integrity_surfaces(**kwargs: Any) -> dict[str, Any]:
    """live四surface観測をcaller自己申告なしで検証する。"""

    try:
        bundle = observe_integrity_bundle(**kwargs)
    except (OSError, UnicodeError, ValueError, sqlite3.Error, tomllib.TOMLDecodeError) as exc:
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_INTEGRITY_V1",
            "ok": False,
            "status": "blocked",
            "reasonCodes": ["automation_prompt_drift_fail_closed"],
            "detailCodes": [f"integrity_observation_unavailable:{type(exc).__name__}:{exc}"],
        }
    result = validate_integrity_bundle(bundle)
    result["observation"] = bundle
    return result


def _automation_value_from_app_db_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    cwds_raw = value.get("cwds")
    if isinstance(cwds_raw, str):
        try:
            value["cwds"] = json.loads(cwds_raw)
        except json.JSONDecodeError:
            value["cwds"] = cwds_raw
    return value


def validate_app_db_semantics(
    path: Path,
    *,
    repo_root: Path,
    expected_target: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        _assert_approved_path(
            path,
            repo_root=_assert_trusted_repo_root(repo_root, read_only=True),
            label="app_db",
        )
        with _existing_file_guard(path, require_exists=True):
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "select * from automations where id = ?",
                (AUTOMATION_ID,),
            ).fetchone()
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["app_db_missing"]}
    except sqlite3.Error as exc:
        return {"ok": False, "path": str(path), "failures": [f"app_db_invalid:{exc}"]}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    value = _automation_value_from_app_db_row(row)
    if value is None:
        return {"ok": False, "path": str(path), "failures": ["app_db_automation_missing"]}
    result = _validate_loaded_automation(
        value,
        path=path,
        repo_root=repo_root,
        expected_target=expected_target,
    )
    result["schemaVersion"] = "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1"
    return result


def validate_skill_semantics(path: Path) -> dict[str, Any]:
    failures: list[str] = []
    try:
        text = _read_text_limited(path, limit=MAX_SKILL_BYTES)
    except FileNotFoundError:
        return {"ok": False, "path": str(path), "failures": ["skill_missing"]}
    except UnicodeError as exc:
        return {"ok": False, "path": str(path), "failures": [f"skill_invalid:{exc}"]}

    required = (
        "Daily 六phase",
        "static_check",
        "scoped_contract_unit",
        "current_issue_integration",
        "external_publication",
        "consumer_public_verification",
        "atomic_completion",
        "frontmatter付きMarkdown",
        "current issue",
        "unknown_unobtainable",
        "callerの`ok=true`だけではrunをcompletedにしない",
        "Git commit ID は観測値としてだけ報告してよい",
        "public incompleteかつexact successorがある状態で終了しない",
    )
    for part in required:
        if part not in text:
            failures.append(f"skill_missing:{part}")
    if "最大1回だけ試す" in text:
        failures.append("skill_title_retry_old_contract")
    for forbidden in (
        "python -m tools.news_grasp_direct_runtime start",
        "python -m tools.news_grasp_direct_runtime advance",
        "python -m pytest",
    ):
        if forbidden in text:
            failures.append(f"skill_forbidden_daily_route:{forbidden}")
    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_SKILL_SYNC_V1",
        "ok": not failures,
        "path": str(path),
        "failures": failures,
        "line_count": len(text.splitlines()),
    }


def _desired_app_db_row(
    *,
    template_path: Path,
    repo_root: Path,
    existing: dict[str, Any] | None,
    project_target: dict[str, str],
    now_ms: int | None = None,
) -> dict[str, Any]:
    template = _load_toml(template_path)
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    existing = existing or {}
    created_at = existing.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at <= 0:
        created_at = now_ms
    updated_at = existing.get("updated_at")
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = created_at
    existing_name = existing.get("name")
    materialized_name = (
        existing_name
        if isinstance(existing_name, str) and TITLE_PATTERN.fullmatch(existing_name)
        else str(template.get("name") or TITLE_SUFFIX)
    )
    return {
        "id": AUTOMATION_ID,
        "name": materialized_name,
        "prompt": str(template.get("prompt") or ""),
        "status": str(template.get("status") or "ACTIVE"),
        "next_run_at": existing.get("next_run_at"),
        "last_run_at": existing.get("last_run_at"),
        "cwds": json.dumps([str(repo_root.resolve(strict=True))], ensure_ascii=False),
        "rrule": str(template.get("rrule") or "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"),
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "created_at": created_at,
        "updated_at": max(now_ms, created_at, updated_at),
        "target_type": project_target["type"],
        "project_id": project_target["project_id"],
    }


def sync_app_db(
    *,
    repo_root: Path,
    template_path: Path,
    app_db_path: Path,
    project_target: dict[str, str],
    dry_run: bool,
    allow_custom_app_db: bool = False,
    promotion_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn: sqlite3.Connection | None = None
    began = False
    changed = False
    existing: dict[str, Any] | None = None
    desired: dict[str, Any] = {}
    schema_columns: tuple[str, ...] = ()
    app_db_path_display = str(app_db_path)
    target = promotion_target or _new_app_db_promotion_target(app_db_path)

    def failure_result(failures: list[str]) -> dict[str, Any]:
        target["status"] = "blocked"
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1",
            "ok": False,
            "path": app_db_path_display,
            "changed": False,
            "automationId": AUTOMATION_ID,
            "preimageHash": target.get("preimageHash", ""),
            "postimageHash": target.get("postimageHash", ""),
            "failures": failures,
        }

    try:
        repo_root = _assert_trusted_repo_root(repo_root, read_only=dry_run)
        app_db_path = _assert_role_path(
            app_db_path,
            repo_root=repo_root,
            label="app_db",
            custom_allowed=allow_custom_app_db,
        )
        template_path = _assert_role_path(
            template_path,
            repo_root=repo_root,
            label="template",
            custom_allowed=False,
        )
        app_db_path_display = str(app_db_path)
        with _existing_file_guard(app_db_path, require_exists=True):
            if dry_run:
                conn = sqlite3.connect(
                    f"file:{app_db_path.as_posix()}?mode=ro",
                    uri=True,
                    timeout=5,
                    isolation_level=None,
                )
            else:
                conn = sqlite3.connect(
                    str(app_db_path), timeout=5, isolation_level=None
                )
            conn.row_factory = sqlite3.Row
            conn.execute("pragma busy_timeout = 5000")
            if not dry_run:
                conn.execute("BEGIN IMMEDIATE")
                began = True
            schema_columns = _app_db_schema_columns(conn)
            existing = _fetch_app_db_automation_row(conn)
            preimage_hash = _app_db_row_hash(existing, schema_columns)
            target.update(
                {
                    "target": str(app_db_path),
                    "kind": "app_db",
                    "automationId": AUTOMATION_ID,
                    "preimagePresent": existing is not None,
                    "preimageColumns": list(schema_columns),
                    "_preimageRow": existing,
                    "preimageHash": preimage_hash,
                    "preimageSha256": preimage_hash,
                    "backupStatus": "row_captured",
                    "atomic": True,
                }
            )
            desired = _desired_app_db_row(
                template_path=template_path,
                repo_root=repo_root,
                existing=existing,
                project_target=project_target,
            )
            if existing is not None:
                comparable = {
                    key: existing.get(key)
                    for key in desired
                    if key != "updated_at"
                }
                desired_comparable = {
                    key: desired.get(key)
                    for key in desired
                    if key != "updated_at"
                }
                if comparable == desired_comparable:
                    desired["updated_at"] = existing.get("updated_at")
            changed = existing is None or any(
                existing.get(key) != desired.get(key)
                for key in APP_DB_AUTOMATION_OWNED_COLUMNS
            )
            if changed and not dry_run:
                columns = list(APP_DB_AUTOMATION_OWNED_COLUMNS)
                placeholders = ", ".join("?" for _ in columns)
                update_assignments = ", ".join(
                    f"{column} = excluded.{column}"
                    for column in columns
                    if column != "id"
                )
                conn.execute(
                    f"insert into automations ({', '.join(columns)}) values ({placeholders}) "
                    f"on conflict(id) do update set {update_assignments}",
                    tuple(desired[column] for column in columns),
                )
                postimage = _fetch_app_db_automation_row(conn)
                if postimage is None:
                    raise RuntimeError("app_db_postimage_row_missing")
                postimage_hash = _app_db_row_hash(postimage, schema_columns)
                target.update(
                    {
                        "_postimageRow": postimage,
                        "candidateHash": postimage_hash,
                        "candidateSha256": postimage_hash,
                        "postimageHash": postimage_hash,
                        "postimageSha256": postimage_hash,
                        "status": "promoted",
                    }
                )
                conn.commit()
                began = False
            else:
                postimage_hash = _app_db_row_hash(existing, schema_columns)
                target.update(
                    {
                        "_postimageRow": existing,
                        "candidateHash": postimage_hash,
                        "candidateSha256": postimage_hash,
                        "postimageHash": postimage_hash,
                        "postimageSha256": postimage_hash,
                        "status": "dry_run" if dry_run else "noop",
                    }
                )
                if began:
                    conn.commit()
                    began = False
    except FileNotFoundError:
        return failure_result(["app_db_missing"])
    except _AppDbSchemaDrift as exc:
        if conn is not None and began and conn.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            began = False
        return failure_result([str(exc)])
    except sqlite3.Error as exc:
        if conn is not None and began and conn.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            began = False
        return failure_result([f"app_db_update_failed:{exc}"])
    except (OSError, RuntimeError, ValueError) as exc:
        if conn is not None and began and conn.in_transaction:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
            began = False
        return failure_result([f"app_db_update_failed:{type(exc).__name__}:{exc}"])
    finally:
        if conn is not None:
            if began and conn.in_transaction:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
            conn.close()

    if dry_run:
        result = _validate_loaded_automation(
            _automation_value_from_app_db_row(desired) or {},
            path=app_db_path,
            repo_root=repo_root,
            expected_target=project_target,
        )
        result["schemaVersion"] = "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1"
    else:
        result = validate_app_db_semantics(
            app_db_path,
            repo_root=repo_root,
            expected_target=project_target,
        )
    result["changed"] = changed
    result["automationId"] = AUTOMATION_ID
    result["preimageHash"] = target.get("preimageHash", "")
    result["postimageHash"] = target.get("postimageHash", "")
    return result


def _sync_unlocked(
    *,
    repo_root: Path | None = None,
    template_path: Path | None = None,
    installed_path: Path | None = None,
    snapshot_path: Path | None = None,
    source_skill_path: Path | None = None,
    installed_skill_path: Path | None = None,
    app_db_path: Path | None = None,
    write_snapshot: bool = False,
    write_skill: bool = False,
    write_app_db: bool = False,
    dry_run: bool = False,
    promote: bool = False,
    allow_custom_paths: bool = False,
) -> dict[str, Any]:
    custom_path_args = _custom_path_args(
        repo_root=repo_root,
        template_path=template_path,
        installed_path=installed_path,
        snapshot_path=snapshot_path,
        source_skill_path=source_skill_path,
        installed_skill_path=installed_skill_path,
        app_db_path=app_db_path,
    )
    if any(custom_path_args.values()) and not allow_custom_paths:
        return _custom_path_error(custom_path_args)

    repo = _assert_trusted_repo_root(
        repo_root or _default_repo_root(),
        read_only=dry_run,
    )
    template = _assert_role_path(
        template_path or _default_template(repo),
        repo_root=repo,
        label="template",
        custom_allowed=allow_custom_paths and custom_path_args["template"],
    )
    installed = _assert_role_path(
        installed_path or _default_installed(),
        repo_root=repo,
        label="installed",
        custom_allowed=allow_custom_paths and custom_path_args["installed"],
    )
    try:
        project_target = _resolve_app_project_target(repo)
    except ValueError as exc:
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": False,
            "dry_run": dry_run,
            "changed": False,
            "snapshot_changed": False,
            "skill_changed": False,
            "app_db_changed": False,
            "failures": [str(exc)],
        }
    rendered = _render_installed(
        template_path=template,
        installed_path=installed,
        repo_root=repo,
        project_target=project_target,
    )
    before = _read_text_limited(installed, limit=MAX_AUTOMATION_TOML_BYTES) if installed.is_file() else ""
    changed = before != rendered
    # canonical installed/App DB/snapshotを変更できるのは、operatorが明示した
    # promotionだけである。custom pathはfixture専用capabilityであり、既存unitの
    # isolated mutationを許すがlive promotion authorityにはならない。
    if (
        not dry_run
        and not promote
        and not allow_custom_paths
        and (changed or write_snapshot or write_skill or write_app_db)
    ):
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": False,
            "status": "promotion_required",
            "dry_run": False,
            "changed": changed,
            "snapshot_changed": None,
            "skill_changed": None,
            "app_db_changed": None,
            "failures": ["explicit_promotion_required"],
            "exact_successor": "rerun with --promote after dry-run validation and backup review",
            "promotionReceipt": None,
            "rollbackReceipt": None,
        }
    promotion_id = hashlib.sha256(
        f"{AUTOMATION_ID}|{time.time_ns()}|{hashlib.sha256(rendered.encode('utf-8')).hexdigest()}".encode("utf-8")
    ).hexdigest()
    promotion_targets: list[dict[str, Any]] = []
    promoted_targets: list[dict[str, Any]] = []
    rollback_receipt: dict[str, Any] | None = None
    promotion_failures: list[str] = []

    def explicit_promote(target: dict[str, Any], text: str) -> bool:
        """一つの明示promotionを実行し、失敗時はJSON receiptへ戻す。"""

        nonlocal rollback_receipt
        try:
            return _promote_text_target(target, text, promoted=promoted_targets)
        except Exception as exc:  # noqa: BLE001 - typed promotion failure is returned.
            receipt = target.get("rollbackReceipt")
            if isinstance(receipt, dict):
                rollback_receipt = receipt
            else:
                rollback_receipt = _rollback_promotion_targets([*promoted_targets, target])
            promotion_failures.append(f"{target.get('kind', 'target')}:{type(exc).__name__}:{exc}")
            return False

    broker_promotion_result: dict[str, Any] | None = None
    plugin_activation_result: dict[str, Any] | None = None
    plugin_rollback_result: dict[str, Any] | None = None
    if promote and not dry_run and not allow_custom_paths:
        broker_path = _daily_broker_promotion_path(installed)
        try:
            _assert_daily_promotion_quiescent()
            _assert_no_reparse_chain(broker_path.parent)
            broker_receipt = _build_daily_broker_promotion(repo)
            broker_text = json.dumps(
                broker_receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ) + "\n"
            target = _capture_promotion_target(
                broker_path,
                kind="daily_broker_promotion",
            )
            promotion_targets.append(target)
            explicit_promote(target, broker_text)
            if promotion_failures:
                raise ValueError("daily_broker_promotion_red")
            observed = json.loads(
                _read_text_limited(broker_path, limit=MAX_AUTOMATION_TOML_BYTES)
            )
            if observed != broker_receipt:
                raise ValueError("daily_broker_promotion_postimage_mismatch")
            broker_promotion_result = {
                "ok": True,
                "status": "promoted",
                "path": str(broker_path),
                "sourceHead": broker_receipt["sourceHead"],
                "sourceGeneration": broker_receipt["sourceGeneration"],
            }
            for relative in DAILY_PLUGIN_DEPLOYMENT_RELATIVE_FILES:
                source_path = repo / relative
                target_path = _assert_approved_path(
                    DAILY_PLUGIN_ROOT / relative,
                    repo_root=repo,
                    label="daily_plugin_deployment",
                )
                source_bytes = _read_bytes_no_follow(
                    source_path, limit=2 * 1024 * 1024
                )
                if relative == "plugins/news-grasp-daily/.mcp.json":
                    source_bytes = _render_daily_plugin_mcp(source_bytes)
                try:
                    source_text = source_bytes.decode("utf-8", errors="strict")
                except UnicodeError as exc:
                    raise ValueError(
                        f"daily_plugin_source_encoding_invalid:{relative}"
                    ) from exc
                target = _capture_promotion_target(
                    target_path,
                    kind=f"daily_plugin:{relative}",
                )
                promotion_targets.append(target)
                explicit_promote(target, source_text)
                if promotion_failures:
                    raise ValueError("daily_plugin_deployment_red")
                installed_bytes = _read_bytes_no_follow(
                    target_path, limit=2 * 1024 * 1024
                )
                if installed_bytes != source_bytes:
                    raise ValueError(
                        f"daily_plugin_deployment_postimage_mismatch:{relative}"
                    )
            plugin_activation_result = _activate_daily_plugin(
                repo,
                marketplace_root=DAILY_PLUGIN_ROOT,
                force_refresh=True,
            )
            if plugin_activation_result.get("ok") is not True:
                raise ValueError("daily_plugin_activation_red")
        except Exception as exc:  # noqa: BLE001 - promotion rollback is receipt-bound.
            promotion_failures.append(
                f"daily_broker_activation:{type(exc).__name__}:{exc}"
            )
            if promoted_targets:
                rollback_receipt = _rollback_promotion_targets(promoted_targets)
            if (
                isinstance(plugin_activation_result, Mapping)
                and any(
                    plugin_activation_result.get(marker) is True
                    for marker in (
                        "pluginAdded",
                        "pluginAddAttempted",
                        "pluginRemovedForRefresh",
                        "pluginRemoveAttempted",
                        "marketplaceAdded",
                        "marketplaceAddAttempted",
                    )
                )
            ):
                plugin_rollback_result = _rollback_daily_plugin_activation(
                    plugin_activation_result
                )
            broker_promotion_result = {
                "ok": False,
                "status": "failed",
                "failures": [f"{type(exc).__name__}:{exc}"],
            }
            if plugin_activation_result is None:
                plugin_activation_result = {
                    "ok": False,
                    "status": "failed",
                    "failures": [f"{type(exc).__name__}:{exc}"],
                }

    snapshot_results: list[dict[str, Any]] = []
    if not dry_run:
        if changed and not promotion_failures:
            target = _capture_promotion_target(installed, kind="installed_toml")
            promotion_targets.append(target)
            explicit_promote(target, rendered)
        if write_snapshot and not promotion_failures:
            for snapshot in _snapshot_targets(repo, snapshot_path):
                snapshot = _assert_role_path(
                    snapshot,
                    repo_root=repo,
                    label="snapshot",
                    custom_allowed=allow_custom_paths,
                )
                before_snapshot = _read_text_limited(snapshot, limit=MAX_AUTOMATION_TOML_BYTES) if snapshot.is_file() else ""
                snapshot_changed = before_snapshot != rendered
                if snapshot_changed:
                    target = _capture_promotion_target(snapshot, kind="snapshot_toml")
                    promotion_targets.append(target)
                    if not explicit_promote(target, rendered):
                        break
                snapshot_result = validate_semantics(
                    snapshot,
                    repo_root=repo,
                    expected_target=project_target,
                )
                snapshot_result["changed"] = snapshot_changed
                snapshot_results.append(snapshot_result)
    skill_result = None
    skill_changed = False
    if write_skill:
        source_skill = _assert_role_path(
            source_skill_path or _default_source_skill(repo),
            repo_root=repo,
            label="source_skill",
            custom_allowed=allow_custom_paths and custom_path_args["source_skill"],
        )
        installed_skill = _assert_role_path(
            installed_skill_path or _default_installed_skill(),
            repo_root=repo,
            label="installed_skill",
            custom_allowed=allow_custom_paths and custom_path_args["installed_skill"],
        )
        skill_text = _read_text_limited(source_skill, limit=MAX_SKILL_BYTES)
        before_skill = _read_text_limited(installed_skill, limit=MAX_SKILL_BYTES) if installed_skill.is_file() else ""
        skill_changed = before_skill != skill_text
        if skill_changed and not dry_run and not promotion_failures:
            target = _capture_promotion_target(installed_skill, kind="installed_skill")
            promotion_targets.append(target)
            explicit_promote(target, skill_text)
        skill_result = validate_skill_semantics(source_skill if dry_run else installed_skill)
    app_db_result = None
    if write_app_db and not promotion_failures:
        app_db_target = _new_app_db_promotion_target(app_db_path or _default_app_db())
        promotion_targets.append(app_db_target)
        app_db_result = sync_app_db(
            repo_root=repo,
            template_path=template,
            app_db_path=app_db_path or _default_app_db(),
            project_target=project_target,
            dry_run=dry_run,
            allow_custom_app_db=allow_custom_paths and custom_path_args["app_db"],
            promotion_target=app_db_target,
        )
        # sync_app_dbがBEGIN IMMEDIATE内でrow hashをsealする。DB bytes全体を
        # 読み直すことや、WAL/SHMを含むファイルbackupを作ることは禁止する。
        if app_db_target.get("status") == "promoted":
            promoted_targets.append(app_db_target)
        if app_db_result.get("ok") is not True:
            promotion_failures.extend(
                f"app_db:{failure}"
                for failure in (app_db_result.get("failures") or [])
            )
            if promoted_targets:
                rollback_receipt = _rollback_promotion_targets(promoted_targets)
    elif write_app_db and promotion_failures:
        app_db_result = {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_APP_DB_V1",
            "ok": False,
            "changed": False,
            "failures": ["promotion_aborted_after_failure"],
        }
    if (
        promotion_failures
        and plugin_rollback_result is None
        and isinstance(plugin_activation_result, Mapping)
        and plugin_activation_result.get("ok") is True
    ):
        plugin_rollback_result = _rollback_daily_plugin_activation(
            plugin_activation_result
        )
        if plugin_rollback_result.get("ok") is not True:
            promotion_failures.extend(
                f"daily_plugin_rollback:{failure}"
                for failure in plugin_rollback_result.get("failures") or ()
            )
    if dry_run:
        installed_result = _validate_loaded_automation(
            tomllib.loads(rendered),
            path=installed,
            repo_root=repo,
            expected_target=project_target,
        )
    else:
        installed_result = validate_semantics(
            installed,
            repo_root=repo,
            expected_target=project_target,
        )
    snapshot_result = None
    if write_snapshot:
        if dry_run:
            for snapshot in _snapshot_targets(repo, snapshot_path):
                snapshot = _assert_role_path(
                    snapshot,
                    repo_root=repo,
                    label="snapshot",
                    custom_allowed=allow_custom_paths,
                )
                before_snapshot = _read_text_limited(snapshot, limit=MAX_AUTOMATION_TOML_BYTES) if snapshot.is_file() else ""
                snapshot_candidate = _validate_loaded_automation(
                    tomllib.loads(rendered),
                    path=snapshot,
                    repo_root=repo,
                    expected_target=project_target,
                )
                snapshot_candidate["changed"] = before_snapshot != rendered
                snapshot_results.append(snapshot_candidate)
        else:
            if not snapshot_results:
                for snapshot in _snapshot_targets(repo, snapshot_path):
                    snapshot = _assert_role_path(
                        snapshot,
                        repo_root=repo,
                        label="snapshot",
                        custom_allowed=allow_custom_paths,
                    )
                    snapshot_result_existing = validate_semantics(
                        snapshot,
                        repo_root=repo,
                        expected_target=project_target,
                    )
                    snapshot_result_existing["changed"] = False
                    snapshot_results.append(snapshot_result_existing)
        snapshot_result = snapshot_results[0] if snapshot_results else None
    promotion_status = (
        "dry_run"
        if dry_run
        else (
            "rolled_back"
            if rollback_receipt is not None
            else ("failed" if promotion_failures else ("promoted" if promoted_targets else "noop"))
        )
    )
    promotion_result = _promotion_receipt(
        promotion_id=promotion_id,
        targets=promotion_targets,
        dry_run=dry_run,
        status=promotion_status,
        failures=[
            *promotion_failures,
            *([] if rollback_receipt is None else list(rollback_receipt.get("failures") or [])),
        ],
    )
    return {
        "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
        "ok": (
            installed_result.get("ok") is True
            and (snapshot_result is None or snapshot_result.get("ok") is True)
            and all(item.get("ok") is True for item in snapshot_results)
            and (skill_result is None or skill_result.get("ok") is True)
            and (app_db_result is None or app_db_result.get("ok") is True)
            and (broker_promotion_result is None or broker_promotion_result.get("ok") is True)
            and (
                not (promote and not dry_run and not allow_custom_paths)
                or (
                    isinstance(plugin_activation_result, Mapping)
                    and plugin_activation_result.get("ok") is True
                )
            )
            and not promotion_failures
        ),
        "dry_run": dry_run,
        "changed": changed,
        "snapshot_changed": None
        if not snapshot_results
        else any(item.get("changed") is True for item in snapshot_results),
        "skill_changed": skill_changed,
        "app_db_changed": None if app_db_result is None else app_db_result.get("changed"),
        "installed": installed_result,
        "skill": skill_result,
        "app_db": app_db_result,
        "daily_broker_promotion": broker_promotion_result,
        "daily_plugin_activation": plugin_activation_result,
        "daily_plugin_rollback": plugin_rollback_result,
        "snapshot": snapshot_result,
        "snapshots": snapshot_results,
        "promotionReceipt": promotion_result,
        "promotion_receipt": promotion_result,
        "rollbackReceipt": rollback_receipt,
        "rollback_receipt": rollback_receipt,
    }


def sync(
    *,
    repo_root: Path | None = None,
    template_path: Path | None = None,
    installed_path: Path | None = None,
    snapshot_path: Path | None = None,
    source_skill_path: Path | None = None,
    installed_skill_path: Path | None = None,
    app_db_path: Path | None = None,
    write_snapshot: bool = False,
    write_skill: bool = False,
    write_app_db: bool = False,
    dry_run: bool = False,
    promote: bool = False,
    allow_custom_paths: bool = False,
) -> dict[str, Any]:
    arguments = {
        "repo_root": repo_root,
        "template_path": template_path,
        "installed_path": installed_path,
        "snapshot_path": snapshot_path,
        "source_skill_path": source_skill_path,
        "installed_skill_path": installed_skill_path,
        "app_db_path": app_db_path,
        "write_snapshot": write_snapshot,
        "write_skill": write_skill,
        "write_app_db": write_app_db,
        "dry_run": dry_run,
        "promote": promote,
        "allow_custom_paths": allow_custom_paths,
    }
    if not (promote and not dry_run and not allow_custom_paths):
        return _sync_unlocked(**arguments)
    from .news_grasp_direct_runtime import daily_process_mutex

    try:
        with daily_process_mutex(timeout_ms=0):
            return _sync_unlocked(**arguments)
    except RuntimeError as exc:
        if str(exc) != "daily_process_mutex_busy":
            raise
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": False,
            "status": "promotion_deferred",
            "dry_run": False,
            "changed": False,
            "failures": ["daily_process_mutex_busy"],
            "exact_successor": "retry_promotion_after_current_writer_process_exit",
            "promotionReceipt": None,
            "rollbackReceipt": None,
        }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument("--template", type=Path, default=None)
    parser.add_argument("--installed", type=Path, default=None)
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--source-skill", type=Path, default=None)
    parser.add_argument("--installed-skill", type=Path, default=None)
    parser.add_argument("--app-db", type=Path, default=None)
    parser.add_argument("--write-snapshot", action="store_true")
    parser.add_argument("--write-skill", action="store_true")
    parser.add_argument("--write-app-db", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="backupとrollback receiptを伴う明示promotionを許可する。",
    )
    parser.add_argument(
        "--allow-custom-paths",
        action="store_true",
        help="テスト・手動復旧時だけ、default以外のpath overrideを許可する。",
    )
    args = parser.parse_args(argv)
    default_repo = _default_repo_root().resolve()
    custom_path_args = _custom_path_args(
        repo_root=args.repo_root,
        template_path=args.template,
        installed_path=args.installed,
        snapshot_path=args.snapshot,
        source_skill_path=args.source_skill,
        installed_skill_path=args.installed_skill,
        app_db_path=args.app_db,
    )
    if any(custom_path_args.values()) and not args.allow_custom_paths:
        result = _custom_path_error(custom_path_args)
        result["dry_run"] = args.dry_run
        sys.stdout.buffer.write(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        return 2
    result = sync(
        repo_root=args.repo_root,
        template_path=args.template,
        installed_path=args.installed,
        snapshot_path=args.snapshot,
        source_skill_path=args.source_skill,
        installed_skill_path=args.installed_skill,
        app_db_path=args.app_db,
        write_snapshot=args.write_snapshot,
        write_skill=args.write_skill,
        write_app_db=args.write_app_db,
        dry_run=args.dry_run,
        promote=args.promote,
        allow_custom_paths=args.allow_custom_paths,
    )
    sys.stdout.buffer.write(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
