"""News-Grasp Dailyの依存追加なしstdio MCP server。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import ctypes
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Mapping


def _windows_known_folder(folder_id: str) -> Path:
    """ambient環境変数を使わずWindows Known Folderを解決する。"""

    if os.name != "nt":
        raise OSError("NEWS_GRASP_DAILY_MCP_WINDOWS_REQUIRED")

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", ctypes.c_uint32),
            ("data2", ctypes.c_uint16),
            ("data3", ctypes.c_uint16),
            ("data4", ctypes.c_ubyte * 8),
        ]

    folder_guid = _Guid.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    output = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_guid),
        0,
        None,
        ctypes.byref(output),
    )
    if status != 0 or not output.value:
        raise OSError(f"NEWS_GRASP_DAILY_MCP_KNOWN_FOLDER_UNAVAILABLE:{status}")
    try:
        return Path(output.value).resolve(strict=True)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(output)


def _windows_local_app_data() -> Path:
    return _windows_known_folder("f1b32785-6fba-4fcf-9d55-7b8e7f157091")


def _windows_profile() -> Path:
    return _windows_known_folder("5e6c858f-0e22-4760-9afe-ea3317b67173")


PYTHON312 = (
    _windows_local_app_data()
    / "Programs"
    / "Python"
    / "Python312"
    / "python.exe"
)
TRUSTED_GIT = Path(r"C:\Program Files\Git\cmd\git.exe")
AUTOMATION_CONFIG = _windows_profile() / ".codex" / "automations" / "news-grasp-6-40" / "automation.toml"
PROMOTION_RECEIPT = AUTOMATION_CONFIG.with_name("daily-broker-promotion.json")
TRUSTED_RUNTIME_ROOT = Path(r"C:\ngstage\News-Grasp-runtime")
TRUSTED_REMOTE_URLS = {
    "https://github.com/HIDEPON-UMG/News-Grasp.git",
}
PROMOTED_RUNTIME_FILES = {
    "tools/news_grasp_daily_broker.py",
    "tools/news_grasp_direct_runtime.py",
    "tools/news_grasp_daily_gate.py",
    "tools/news_grasp_daily_content.py",
    "tools/news_grasp_repair_registry.py",
    "tools/news_grasp_trusted_process.py",
    "tools/news_grasp_daily_release.py",
    "tools/news_grasp_daily_external.py",
    "tools/news_grasp_direct_completion.py",
    "tools/news_grasp_production_adapters.py",
    "tools/news_grasp_publish_contract.py",
    "tools/news_grasp_gate_profiles.py",
    "tools/news_grasp_audio_projection.py",
    "tools/publish_inventory.py",
    "config/news_grasp_daily_45m_contract_v1.json",
    "schemas/news_grasp_daily_reporter_output.schema.json",
    "schemas/news_grasp_daily_reporter_shard_output.schema.json",
    "schemas/news_grasp_daily_editor_output.schema.json",
    "schemas/news_grasp_daily_deepdive_output.schema.json",
    ".agents/plugins/marketplace.json",
    "plugins/news-grasp-daily/server.py",
    "plugins/news-grasp-daily/.mcp.json",
    "plugins/news-grasp-daily/.codex-plugin/plugin.json",
}
TOOL_NAME = "run_daily"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_CHILD_STREAM_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 128 * 1024
CREATE_SUSPENDED = 0x00000004


def _reject_reparse_chain(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    cursor = absolute
    while True:
        if cursor.exists():
            info = os.lstat(cursor)
            attributes = int(getattr(info, "st_file_attributes", 0))
            if (
                stat.S_ISLNK(info.st_mode)
                or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or getattr(cursor, "is_junction", lambda: False)()
            ):
                raise RuntimeError("NEWS_GRASP_DAILY_MCP_REPARSE_PATH_FORBIDDEN")
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent


def _git_probe(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    profile = _windows_profile()
    local_app_data = _windows_local_app_data()
    windows = Path(r"C:\Windows")
    env = {
        "APPDATA": str(
            _windows_known_folder("3eb685db-65f9-4cf6-a03a-e3ef65729f3d")
        ),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(profile),
        "LOCALAPPDATA": str(local_app_data),
        "NoDefaultCurrentDirectoryInExePath": "1",
        "PATH": os.pathsep.join((str(TRUSTED_GIT.parent), str(windows / "System32"))),
        "SystemRoot": str(windows),
        "TEMP": str(local_app_data / "Temp"),
        "TMP": str(local_app_data / "Temp"),
        "USERPROFILE": str(profile),
        "WINDIR": str(windows),
    }
    return subprocess.run(
        [str(TRUSTED_GIT), *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
        shell=False,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _repo_root() -> Path:
    _reject_reparse_chain(TRUSTED_GIT)
    if not TRUSTED_GIT.is_file():
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_TRUSTED_GIT_INVALID")
    _reject_reparse_chain(PROMOTION_RECEIPT)
    raw = PROMOTION_RECEIPT.read_bytes()
    if not raw or len(raw) > MAX_RECEIPT_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_RECEIPT_INVALID")
    try:
        receipt = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_RECEIPT_INVALID") from exc
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schemaVersion") != "NEWS_GRASP_DAILY_BROKER_PROMOTION_V1"
        or not isinstance(receipt.get("fileHashes"), Mapping)
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("sourceHead") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sourceGeneration") or "")) is None
    ):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_RECEIPT_INVALID")
    root = Path(str(receipt.get("repoRoot") or "")).resolve(strict=True)
    trusted_root = TRUSTED_RUNTIME_ROOT.resolve(strict=True)
    if os.path.normcase(str(root)) != os.path.normcase(str(trusted_root)):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_REPO_ROOT_INVALID")
    _reject_reparse_chain(root)
    if not root.is_dir():
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_REPO_ROOT_INVALID")
    hashes = {str(key): str(value) for key, value in receipt["fileHashes"].items()}
    if set(hashes) != PROMOTED_RUNTIME_FILES:
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_FILE_SET_INVALID")
    remote_url = str(receipt.get("remoteUrl") or "")
    remote_ref = str(receipt.get("remoteRef") or "")
    remote_main = str(receipt.get("remoteMainSha") or "").casefold()
    remote_evidence = {
        "remoteUrl": remote_url,
        "remoteRef": remote_ref,
        "remoteMainSha": remote_main,
    }
    expected_remote_evidence_sha = hashlib.sha256(
        json.dumps(
            remote_evidence,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        remote_url not in TRUSTED_REMOTE_URLS
        or remote_ref != "refs/heads/main"
        or remote_main != str(receipt["sourceHead"]).casefold()
        or receipt.get("remoteEvidenceSha256") != expected_remote_evidence_sha
    ):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_REMOTE_INVALID")
    for relative, expected_hash in hashes.items():
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
        ):
            raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_FILE_SET_INVALID")
        target = root / relative_path
        _reject_reparse_chain(target)
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != expected_hash
        ):
            raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTED_SOURCE_DRIFT")
    generation = hashlib.sha256(
        json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if generation != receipt["sourceGeneration"]:
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_SOURCE_GENERATION_MISMATCH")
    source_head = str(receipt["sourceHead"])
    head = _git_probe(root, "rev-parse", "--verify", "HEAD")
    origin_main = _git_probe(root, "rev-parse", "--verify", "origin/main")
    remote = _git_probe(root, "remote", "get-url", "origin")
    status = _git_probe(root, "status", "--porcelain=v1", "--untracked-files=all")
    if any(item.returncode != 0 for item in (head, origin_main, remote, status)):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_GIT_REFERENCE_INVALID")
    observed_head = head.stdout.decode("ascii", errors="strict").strip()
    observed_remote = remote.stdout.decode("utf-8", errors="strict").strip()
    if (
        observed_head != source_head
        or observed_remote != remote_url
        or observed_remote not in TRUSTED_REMOTE_URLS
    ):
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_GIT_REFERENCE_INVALID")
    if bytes(status.stdout or b"").strip():
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_SOURCE_TREE_DIRTY")
    ancestor = _git_probe(
        root,
        "merge-base",
        "--is-ancestor",
        source_head,
        origin_main.stdout.decode("ascii", errors="strict").strip(),
    )
    if ancestor.returncode != 0:
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_PROMOTION_NOT_ANCESTOR")
    expected_server = (root / "plugins" / "news-grasp-daily" / "server.py").resolve(strict=True)
    if Path(__file__).resolve(strict=True).read_bytes() != expected_server.read_bytes():
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_SERVER_DRIFT")
    return root


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _assign_kill_on_close_job(process: subprocess.Popen[bytes]) -> int | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    info = _JobObjectExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    ) or not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        kernel32.CloseHandle(job)
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    return int(job)


def _close_job(job: int | None) -> None:
    if job is not None and os.name == "nt":
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(job))


def _resume_process(process: subprocess.Popen[bytes]) -> None:
    if os.name != "nt":
        return
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = int(ntdll.NtResumeProcess(wintypes.HANDLE(process._handle)))
    if status < 0:
        raise OSError(status, "NtResumeProcess")


def _run_child_bounded(root: Path, env: Mapping[str, str]) -> tuple[int, bytes, str]:
    bootstrap = (
        "import runpy,sys,sysconfig;"
        "sys.path.extend(dict.fromkeys(p for p in (sysconfig.get_path('purelib'),"
        "sysconfig.get_path('platlib')) if p and p not in sys.path));"
        "sys.path.insert(0,sys.argv[1]);"
        "runpy.run_module('tools.news_grasp_daily_broker',run_name='__main__')"
    )
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if os.name == "nt":
        creationflags |= CREATE_SUSPENDED
    process = subprocess.Popen(
        [str(PYTHON312), "-I", "-S", "-B", "-c", bootstrap, str(root)],
        cwd=root,
        env=dict(env),
        shell=False,
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        job = _assign_kill_on_close_job(process)
        _resume_process(process)
    except Exception:
        if "job" in locals():
            _close_job(job)
        process.kill()
        process.wait()
        raise
    stdout_tail = bytearray()
    stderr_digest = hashlib.sha256()

    def drain_stdout() -> None:
        assert process.stdout is not None
        while chunk := process.stdout.read(64 * 1024):
            stdout_tail.extend(chunk)
            if len(stdout_tail) > MAX_CHILD_STREAM_BYTES:
                del stdout_tail[:-MAX_CHILD_STREAM_BYTES]

    def drain_stderr() -> None:
        assert process.stderr is not None
        while chunk := process.stderr.read(64 * 1024):
            stderr_digest.update(chunk)

    readers = (
        threading.Thread(target=drain_stdout, name="news-grasp-broker-stdout", daemon=True),
        threading.Thread(target=drain_stderr, name="news-grasp-broker-stderr", daemon=True),
    )
    try:
        for reader in readers:
            reader.start()
        return_code = process.wait()
        for reader in readers:
            reader.join()
        return return_code, bytes(stdout_tail), stderr_digest.hexdigest()
    except BaseException:
        _close_job(job)
        job = None
        process.wait()
        raise
    finally:
        _close_job(job)


def _invoke_daily(arguments: Mapping[str, Any]) -> dict[str, Any]:
    if dict(arguments):
        raise RuntimeError("NEWS_GRASP_DAILY_BROKER_ARGUMENTS_MUST_BE_EMPTY")
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.news_grasp_trusted_process import daily_child_environment

    env = daily_child_environment(
        repo_root=root,
        python_executable=PYTHON312,
    )
    return_code, stdout, stderr_hash = _run_child_bounded(root, env)
    result: dict[str, Any] | None = None
    try:
        stdout_text = stdout.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RuntimeError("NEWS_GRASP_DAILY_MCP_RESULT_ENCODING_INVALID") from exc
    for line in reversed(stdout_text.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            result = candidate
            break
    if result is None:
        raise RuntimeError(
            f"NEWS_GRASP_DAILY_MCP_RESULT_MISSING:{return_code}:{stderr_hash}"
        )
    return result


def _send(value: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _public_error(exc: Exception) -> str:
    message = str(exc)
    if re.fullmatch(r"[A-Z0-9_.:-]{1,240}", message):
        return f"{type(exc).__name__}:{message}"
    return f"{type(exc).__name__}:NEWS_GRASP_DAILY_MCP_INTERNAL_RED"


def _tool_result(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(dict(result), ensure_ascii=False, sort_keys=True),
                }
            ],
            "structuredContent": dict(result),
            "isError": result.get("ok") is not True,
        },
    }


def _dispatch(message: Mapping[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "news-grasp-daily", "version": "1.0.0"},
            },
        }
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": "News-Graspの日次本線を一回だけ実行し、最終typed結果を返す。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        }
    if method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != TOOL_NAME:
            raise RuntimeError("NEWS_GRASP_DAILY_MCP_TOOL_UNKNOWN")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise RuntimeError("NEWS_GRASP_DAILY_MCP_ARGUMENTS_INVALID")
        return _tool_result(request_id, _invoke_daily(arguments))
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    while True:
        raw_bytes = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
        if not raw_bytes:
            break
        if len(raw_bytes) > MAX_REQUEST_BYTES or not raw_bytes.endswith(b"\n"):
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "REQUEST_TOO_LARGE"},
                }
            )
            return 2
        try:
            raw = raw_bytes.decode("utf-8", errors="strict")
        except UnicodeError:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "INVALID_UTF8"},
                }
            )
            continue
        if not raw.strip():
            continue
        request_id: Any = None
        try:
            message = json.loads(raw)
            if not isinstance(message, Mapping):
                raise ValueError("request must be an object")
            request_id = message.get("id")
            response = _dispatch(message)
        except Exception as exc:  # noqa: BLE001 - MCP boundary returns typed JSON-RPC error.
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": _public_error(exc)},
            }
        if response is not None:
            _send(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
