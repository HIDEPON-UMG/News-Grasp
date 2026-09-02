"""News-Grasp実行環境・retry・SLOをdurableに記録するCLI。"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


OBSERVATION_SCHEMA = "NEWS_GRASP_RUN_OBSERVATION_V1"
_VERIFICATION_NODE_RE = re.compile(r"tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_\[\]-]{1,160}")


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_bounded_child(
    command: list[str], *, cwd: Path, env: Mapping[str, str], timeout: float, output_limit: int
) -> subprocess.CompletedProcess[bytes]:
    """owned childのstdout/stderrをstream上限内で読む。"""
    process = subprocess.Popen(
        command, cwd=str(cwd), env=dict(env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        shell=False, creationflags=_creation_flags(),
    )
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def drain(stream: Any, buffer: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    return
                remaining = output_limit - len(buffer)
                if remaining > 0:
                    buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, buffers[0]), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, buffers[1]), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    while process.poll() is None:
        if overflow.is_set() or time.monotonic() >= deadline:
            timed_out = not overflow.is_set()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            break
        time.sleep(0.01)
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("verification_runner_stream_shutdown_red")
    if overflow.is_set():
        raise RuntimeError("verification_runner_output_too_large")
    if timed_out:
        raise TimeoutError("verification_runner_timeout")
    return subprocess.CompletedProcess(command, int(process.returncode or 0), bytes(buffers[0]), bytes(buffers[1]))


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
        creationflags=_creation_flags(),
    )


def probe_python(executable: str) -> dict[str, Any]:
    """pytestをimportできるPythonだけをverified候補にする。"""
    probe = _run([executable, "-X", "utf8", "-c", "import json,platform,pytest,sys; print(json.dumps({'executable':sys.executable,'pythonVersion':platform.python_version(),'pytestVersion':pytest.__version__}))"])
    if probe.returncode != 0:
        return {
            "ok": False,
            "status": "environment_missing",
            "executable": executable,
            "reason": (probe.stderr or probe.stdout).strip()[-1000:],
        }
    try:
        value = json.loads(probe.stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "status": "environment_missing", "executable": executable, "reason": "python_probe_json_invalid"}
    return {"ok": True, "status": "verified", **value}


def select_canonical_python(candidates: Iterable[str]) -> dict[str, Any]:
    """入力順で最初のpytest-capable interpreterを固定する。"""
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        row = probe_python(str(candidate))
        if row.get("ok") is True:
            return {**row, "rejected": rejected}
        rejected.append(row)
    return {"ok": False, "status": "environment_missing", "rejected": rejected}


def _stable_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_regular_bounded_no_follow(path: Path, *, limit: int) -> bytes:
    before = os.lstat(path)
    attributes = int(getattr(before, "st_file_attributes", 0))
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or before.st_size > limit:
        raise ValueError("retry_environment_config_invalid")
    descriptor = os.open(path, os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0)))
    try:
        opened = os.fstat(descriptor)
        data = os.read(descriptor, limit + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(opened) or identity(opened) != identity(after) or len(data) != after.st_size or len(data) > limit:
        raise ValueError("retry_environment_config_identity_changed")
    return data


def environment_shape(value: Mapping[str, Any]) -> str:
    """実行可能file/version/config/cwd/HEAD/env/external readinessからshapeを作る。"""
    material = {
        key: value.get(key)
        for key in ("executable", "pythonVersion", "pytestVersion", "pytestConfigSource", "cwd", "sourceHead", "worktree", "environment", "externalReadiness")
    }
    return _stable_hash(material)


def failure_shape(*, node_id: str, failure_class: str, causal_frame: str) -> str:
    """可変messageを除き、安定した原因frameだけでfailure shapeを作る。"""
    return _stable_hash({"nodeId": node_id, "failureClass": failure_class, "causalFrame": causal_frame})


def capture_retry_environment(*, repo_root: str | Path, source_generation: str) -> dict[str, Any]:
    """retry consumer自身がinterpreter/config/cwd/HEAD/envを再観測する。"""
    raw_root = Path(os.path.abspath(os.fspath(repo_root)))
    for current in reversed((raw_root, *raw_root.parents)):
        if str(current) == current.anchor or (not current.exists() and not current.is_symlink()):
            continue
        info = os.lstat(current)
        attributes = int(getattr(info, "st_file_attributes", 0))
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("retry_environment_repo_reparse_forbidden")
    root = Path(repo_root).resolve(strict=True)
    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if head.returncode != 0 or head.stdout.strip() != source_generation:
        raise ValueError("retry_environment_source_generation_unbound")
    status = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root)
    if status.returncode != 0 or status.stdout:
        raise ValueError("retry_environment_worktree_not_clean")
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=root)
    if tree.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", tree.stdout.strip()) is None:
        raise ValueError("retry_environment_tree_unverified")
    python = probe_python(sys.executable)
    if python.get("ok") is not True:
        raise EnvironmentError("retry_environment_python_unverified")
    config_rows: list[dict[str, Any]] = []
    for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            raw = _read_regular_bounded_no_follow(path, limit=1_048_576)
            config_rows.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
    shaping_names = ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "PYTHONHOME", "COVERAGE_PROCESS_START")
    relevant_env = {
        name: {
            "present": name in os.environ,
            "sha256": hashlib.sha256(os.environ.get(name, "").encode("utf-8", errors="replace")).hexdigest() if name in os.environ else "",
        }
        for name in shaping_names
    }
    material = {
        "schemaVersion": "NEWS_GRASP_RETRY_ENVIRONMENT_OBSERVATION_V1",
        "executable": str(Path(str(python["executable"])).resolve()),
        "pythonVersion": python.get("pythonVersion"),
        "pytestVersion": python.get("pytestVersion"),
        "pytestConfigSource": config_rows,
        "cwd": str(root),
        "sourceHead": source_generation,
        "sourceTree": tree.stdout.strip(),
        "worktree": str(root),
        "environment": relevant_env,
        "externalReadiness": {},
    }
    core = {key: value for key, value in material.items() if key not in {"schemaVersion", "sourceHead", "sourceTree"}}
    material["environmentShape"] = environment_shape(material)
    material["environmentCoreShape"] = _stable_hash(core)
    return material


def capture_observation(
    *,
    repo_root: str | Path,
    purpose: str,
    python_executable: str = sys.executable,
    run_id: str = "",
    run_intent: str = "scheduled_production_direct",
    issue_date: str,
    manifest_path: str | Path,
    runtime_state_root: str | Path,
) -> dict[str, Any]:
    """未来の結果を捏造せず、現在観測だけをfield分離して返す。"""
    root = Path(repo_root).resolve()
    state_root = Path(runtime_state_root).resolve(strict=False)
    python = probe_python(python_executable)
    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    tree = _run(["git", "write-tree"], cwd=root)
    status = _run(["git", "status", "--porcelain=v1"], cwd=root)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("issueDate") != issue_date
        or manifest.get("runId") != run_id
        or manifest.get("runIntent") != run_intent
        or not isinstance(manifest.get("exactWriteSet"), list)
        or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("manifestId") or ""))
    ):
        raise ValueError("observation_manifest_binding_invalid")
    env_allowlist = ("PYTEST_ADDOPTS", "PYTHONUTF8", "TZ", "CI")
    relevant_env = {
        name: {
            "present": True,
            "length": len(os.environ[name]),
            "sha256": hashlib.sha256(os.environ[name].encode("utf-8", errors="replace")).hexdigest(),
        }
        for name in env_allowlist if name in os.environ
    }
    observed = {
        "schemaVersion": OBSERVATION_SCHEMA,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "runId": run_id,
        "runIntent": run_intent,
        "issueDate": issue_date,
        "purpose": purpose,
        "cwd": str(root),
        "sourceHead": head.stdout.strip() if head.returncode == 0 else "unverified",
        "indexTree": tree.stdout.strip() if tree.returncode == 0 else "unverified",
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else "unverified",
        "python": python,
        "pytestConfigSource": "pyproject.toml" if (root / "pyproject.toml").is_file() else "unverified",
        "PYTEST_ADDOPTS": relevant_env.get("PYTEST_ADDOPTS", {"present": False}),
        "environment": relevant_env,
        "externalReadiness": {
            "git": "verified" if shutil.which("git") else "unverified",
            "gh": "verified" if shutil.which("gh") else "unverified",
            "node": "verified" if shutil.which("node") else "unverified",
        },
        "exactWriteSet": list(manifest["exactWriteSet"]),
        "manifestId": str(manifest.get("manifestId") or "unverified"),
        "runtimeState": {"root": str(state_root), "dbExists": (state_root / "direct-mainline.sqlite3").is_file()},
        "automationTemplate": "unverified",
        "installedToml": "unverified",
        "appDb": "unverified",
        "loadedThread": "unverified",
        "generated": "unverified",
        "staged": "unverified",
        "committed": "unverified",
        "remote": "unverified",
        "installed": "unverified",
        "loaded": "unverified",
        "localVerifier": "unverified",
        "pages": "unverified",
        "publicCompletion": "unverified",
    }
    observed["environmentShape"] = environment_shape({**observed, **python, "worktree": str(root)})
    return observed


def _capture_cause_input_snapshot(
    *, repo_root: str | Path, source_generation: str, cause_input_paths: Iterable[str]
) -> tuple[dict[str, Any], str]:
    """consumer自身がGit HEADと登録cause input bytesをbounded/no-followで観測する。"""

    root = Path(repo_root).resolve(strict=True)
    head = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if head.returncode != 0 or head.stdout.strip() != source_generation:
        raise ValueError("cause_snapshot_source_generation_unbound")
    raw_paths = list(itertools.islice(iter(cause_input_paths), 65))
    if not raw_paths or len(raw_paths) > 64:
        raise ValueError("cause_snapshot_path_count_invalid")
    files: dict[str, str] = {}
    for raw in raw_paths:
        normalized = str(raw).replace("\\", "/")
        parts = normalized.split("/")
        if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("cause_snapshot_path_invalid")
        candidate = root / normalized
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise ValueError("cause_snapshot_path_escape")
        for current in (candidate, *candidate.parents):
            if current == root.parent:
                break
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                continue
            attributes = int(getattr(info, "st_file_attributes", 0))
            if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("cause_snapshot_reparse_forbidden")
        before = os.lstat(candidate)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1_048_576:
            raise ValueError("cause_snapshot_file_invalid")
        descriptor = os.open(candidate, os.O_RDONLY | int(getattr(os, "O_BINARY", 0)) | int(getattr(os, "O_NOFOLLOW", 0)))
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = 1_048_577
            while remaining > 0:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw_bytes = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(opened) or identity(opened) != identity(after) or len(raw_bytes) != opened.st_size or len(raw_bytes) > 1_048_576:
            raise ValueError("cause_snapshot_file_identity_changed")
        files[normalized] = hashlib.sha256(raw_bytes).hexdigest()
    snapshot = {"sourceGeneration": source_generation, "files": dict(sorted(files.items()))}
    return snapshot, _stable_hash(snapshot)


class ExecutionControlStore:
    """retry、checkpoint、duration bucketのappend-only SQLite正本。"""

    def __init__(self, state_root: str | Path) -> None:
        self.root = Path(state_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "execution-control.sqlite3"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, source_generation TEXT NOT NULL, environment_shape TEXT NOT NULL, failure_shape TEXT NOT NULL, failure_node_id TEXT NOT NULL DEFAULT '', recovery_nodes_json TEXT NOT NULL DEFAULT '[]', cause_snapshot_json TEXT NOT NULL DEFAULT '{}', remediation_json TEXT NOT NULL, admitted_at TEXT NOT NULL)")
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(attempts)")}
            if "cause_snapshot_json" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN cause_snapshot_json TEXT NOT NULL DEFAULT '{}'")
            if "failure_node_id" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN failure_node_id TEXT NOT NULL DEFAULT ''")
            if "recovery_nodes_json" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN recovery_nodes_json TEXT NOT NULL DEFAULT '[]'")
            if "environment_observation_json" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN environment_observation_json TEXT NOT NULL DEFAULT '{}'")
            if "environment_core_shape" not in columns:
                conn.execute("ALTER TABLE attempts ADD COLUMN environment_core_shape TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS attempts_shape_generation_uq ON attempts(source_generation,environment_shape,failure_shape)")
            conn.execute("CREATE TABLE IF NOT EXISTS verification_events (id INTEGER PRIMARY KEY AUTOINCREMENT, source_generation TEXT NOT NULL, node_id TEXT NOT NULL, cause_snapshot_sha256 TEXT NOT NULL, command_json TEXT NOT NULL DEFAULT '[]', result_sha256 TEXT NOT NULL DEFAULT '', exit_code INTEGER NOT NULL DEFAULT -1, status TEXT NOT NULL, completed_at TEXT NOT NULL)")
            event_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(verification_events)")}
            for column, declaration in (
                ("command_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("result_sha256", "TEXT NOT NULL DEFAULT ''"),
                ("exit_code", "INTEGER NOT NULL DEFAULT -1"),
                ("environment_shape", "TEXT NOT NULL DEFAULT ''"),
                ("environment_core_shape", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in event_columns:
                    conn.execute(f"ALTER TABLE verification_events ADD COLUMN {column} {declaration}")
            conn.execute("CREATE TABLE IF NOT EXISTS checkpoints (run_id TEXT NOT NULL, minute INTEGER NOT NULL, elapsed_minutes REAL NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY(run_id, minute))")
            conn.execute("CREATE TABLE IF NOT EXISTS durations (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, bucket TEXT NOT NULL, duration_seconds REAL NOT NULL, recorded_at TEXT NOT NULL)")
            conn.commit()

    def admit_attempt(
        self,
        *,
        source_generation: str,
        environment_shape: str,
        failure_shape: str,
        failure_node_id: str,
        failure_class: str,
        causal_frame: str,
        repo_root: str | Path,
        cause_input_paths: Iterable[str],
        causal_remediation_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = dict(causal_remediation_receipt or {})
        recovery_nodes: list[str] = []
        if (
            _VERIFICATION_NODE_RE.fullmatch(failure_node_id) is None
            or ".." in failure_node_id
            or failure_shape != globals()["failure_shape"](node_id=failure_node_id, failure_class=failure_class, causal_frame=causal_frame)
        ):
            raise ValueError("attempt_failure_contract_invalid")
        environment_observation = capture_retry_environment(repo_root=repo_root, source_generation=source_generation)
        if environment_shape != environment_observation["environmentShape"]:
            raise ValueError("attempt_environment_shape_unbound")
        environment_core = str(environment_observation["environmentCoreShape"])
        if (
            re.fullmatch(r"[0-9a-f]{40}", source_generation) is None
            or re.fullmatch(r"[0-9a-f]{64}", environment_shape) is None
            or re.fullmatch(r"[0-9a-f]{64}", failure_shape) is None
        ):
            raise ValueError("attempt_identity_invalid")
        cause_snapshot, cause_identity = _capture_cause_input_snapshot(
            repo_root=repo_root,
            source_generation=source_generation,
            cause_input_paths=cause_input_paths,
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute("SELECT id FROM attempts WHERE source_generation=? AND environment_shape=? AND failure_shape=? LIMIT 1", (source_generation, environment_shape, failure_shape)).fetchone()
            if duplicate:
                conn.rollback()
                return {"ok": False, "status": "blocked", "reasonCode": "same_shape_retry_rejected"}
            prior = conn.execute(
                "SELECT id,source_generation,failure_shape,cause_snapshot_json,failure_node_id,recovery_nodes_json FROM attempts WHERE environment_core_shape=? AND failure_shape=? ORDER BY id DESC LIMIT 1",
                (environment_core, failure_shape),
            ).fetchone()
            if prior:
                prior_snapshot = json.loads(str(prior[3]))
                before_identity = _stable_hash(prior_snapshot) if isinstance(prior_snapshot, Mapping) else ""
                event_id = receipt.get("verificationEventId")
                event = conn.execute(
                    "SELECT source_generation,cause_snapshot_sha256,status,exit_code,result_sha256,node_id,environment_shape,environment_core_shape FROM verification_events WHERE id=?",
                    (event_id,),
                ).fetchone() if isinstance(event_id, int) and not isinstance(event_id, bool) else None
                valid_receipt = (
                    receipt.get("schemaVersion") == "NEWS_GRASP_CAUSAL_REMEDIATION_RECEIPT_V1"
                    and receipt.get("priorAttemptId") == int(prior[0])
                    and receipt.get("priorFailureShape") == prior[2]
                    and receipt.get("priorSourceGeneration") == prior[1]
                    and receipt.get("afterSourceGeneration") == source_generation
                    and receipt.get("beforeInputIdentity") == before_identity
                    and receipt.get("afterInputIdentity") == cause_identity
                    and before_identity != cause_identity
                    and isinstance(prior_snapshot, Mapping)
                    and sorted((prior_snapshot.get("files") or {}).keys()) == sorted(cause_snapshot["files"].keys())
                    and event is not None
                    and event[0] == source_generation
                    and event[1] == cause_identity
                    and event[2] == "verified"
                    and event[3] == 0
                    and re.fullmatch(r"[0-9a-f]{64}", str(event[4] or "")) is not None
                    and receipt.get("verificationNodeId") == event[5]
                    and event[5] == str(prior[4])
                    and event[6] == environment_shape
                    and event[7] == environment_core
                )
                if not valid_receipt:
                    conn.rollback()
                    return {"ok": False, "status": "blocked", "reasonCode": "causal_remediation_receipt_invalid"}
            cursor = conn.execute("INSERT INTO attempts (source_generation,environment_shape,environment_core_shape,environment_observation_json,failure_shape,failure_node_id,recovery_nodes_json,cause_snapshot_json,remediation_json,admitted_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (source_generation, environment_shape, environment_core, json.dumps(environment_observation, ensure_ascii=False, sort_keys=True), failure_shape, failure_node_id, json.dumps(recovery_nodes, ensure_ascii=False), json.dumps(cause_snapshot, ensure_ascii=False, sort_keys=True), json.dumps(receipt, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat()))
            conn.commit()
        return {"ok": True, "status": "verified", "reasonCode": "attempt_admitted", "attemptId": int(cursor.lastrowid), "causeInputIdentity": cause_identity, "causeInputPaths": sorted(cause_snapshot["files"].keys())}

    def record_verification_event(
        self,
        *,
        source_generation: str,
        repo_root: str | Path,
        cause_input_paths: Iterable[str],
        node_id: str,
        prior_attempt_id: int,
    ) -> dict[str, Any]:
        if _VERIFICATION_NODE_RE.fullmatch(node_id) is None or ".." in node_id:
            raise ValueError("verification_node_id_invalid")
        if not isinstance(prior_attempt_id, int) or isinstance(prior_attempt_id, bool):
            raise ValueError("verification_prior_attempt_invalid")
        with closing(sqlite3.connect(self.db_path)) as conn:
            prior = conn.execute(
                "SELECT source_generation,failure_node_id,recovery_nodes_json,cause_snapshot_json,environment_core_shape FROM attempts WHERE id=?",
                (prior_attempt_id,),
            ).fetchone()
        if prior is None or source_generation == str(prior[0]):
            raise ValueError("verification_prior_attempt_unbound")
        if node_id != str(prior[1]) or json.loads(str(prior[2])) != []:
            raise ValueError("verification_node_not_bound_to_failure")
        snapshot_before, identity_before = _capture_cause_input_snapshot(
            repo_root=repo_root,
            source_generation=source_generation,
            cause_input_paths=cause_input_paths,
        )
        prior_snapshot = json.loads(str(prior[3]))
        if not isinstance(prior_snapshot, Mapping) or sorted((prior_snapshot.get("files") or {}).keys()) != sorted(snapshot_before["files"].keys()):
            raise ValueError("verification_cause_inputs_unbound")
        if node_id.split("::", 1)[0] not in snapshot_before["files"]:
            raise ValueError("verification_node_source_not_snapshotted")
        environment_observation = capture_retry_environment(repo_root=repo_root, source_generation=source_generation)
        if environment_observation["environmentCoreShape"] != str(prior[4]):
            raise ValueError("verification_environment_shape_mismatch")
        root = Path(repo_root).resolve(strict=True)
        handle, junit_name = tempfile.mkstemp(prefix="verification-", suffix=".xml", dir=self.root)
        os.close(handle)
        Path(junit_name).unlink(missing_ok=True)
        basetemp = Path(tempfile.mkdtemp(prefix="verification-pytest-", dir=self.root)).resolve(strict=True)
        if not basetemp.is_relative_to(self.root.resolve(strict=True)):
            raise RuntimeError("verification_basetemp_escape")
        command = [sys.executable, "-B", "-X", "utf8", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--capture=no", f"--basetemp={basetemp}", node_id, f"--junitxml={junit_name}"]
        allowed_environment_names = {
            "appdata", "comspec", "lang", "lc_all", "localappdata", "number_of_processors",
            "os", "path", "pathext", "processor_architecture", "programdata", "programfiles",
            "programfiles(x86)", "systemdrive", "systemroot", "temp", "tmp", "tmpdir", "tz",
            "userprofile", "windir",
        }
        child_env = {
            name: value
            for name, value in os.environ.items()
            if name.casefold() in allowed_environment_names
        }
        child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        child_env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = _run_bounded_child(
                command, cwd=root, env=child_env, timeout=300, output_limit=1_048_576
            )
            junit_path = Path(junit_name)
            if not junit_path.is_file() or junit_path.stat().st_size > 1_048_576:
                raise RuntimeError("verification_runner_outcome_missing")
            junit_bytes = junit_path.read_bytes()
        finally:
            Path(junit_name).unlink(missing_ok=True)
            if basetemp.is_relative_to(self.root.resolve(strict=True)):
                shutil.rmtree(basetemp, ignore_errors=True)
        if result.returncode != 0:
            raise RuntimeError("verification_runner_red")
        if len(result.stdout) > 1_048_576 or len(result.stderr) > 1_048_576:
            raise RuntimeError("verification_runner_output_too_large")
        try:
            junit_root = ET.fromstring(junit_bytes)
        except ET.ParseError as exc:
            raise RuntimeError("verification_runner_outcome_invalid") from exc
        cases = list(junit_root.iter("testcase"))
        if len(cases) != 1 or any(case.find(kind) is not None for case in cases for kind in ("failure", "error", "skipped")):
            raise RuntimeError("verification_runner_node_not_passed")
        _, identity_after = _capture_cause_input_snapshot(
            repo_root=repo_root,
            source_generation=source_generation,
            cause_input_paths=cause_input_paths,
        )
        if identity_before != identity_after:
            raise RuntimeError("verification_runner_mutated_cause_inputs")
        environment_after = capture_retry_environment(repo_root=repo_root, source_generation=source_generation)
        if environment_after != environment_observation:
            raise RuntimeError("verification_runner_mutated_worktree_or_environment")
        stored_command = [
            "--basetemp=<consumer-owned-temp>" if item.startswith("--basetemp=")
            else "--junitxml=<consumer-owned-output>" if item.startswith("--junitxml=")
            else item
            for item in command
        ]
        result_identity = _stable_hash(
            {
                "command": stored_command,
                "exitCode": result.returncode,
                "stdoutSha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderrSha256": hashlib.sha256(result.stderr).hexdigest(),
                "junitSha256": hashlib.sha256(junit_bytes).hexdigest(),
                "sourceGeneration": source_generation,
                "causeInputIdentity": identity_after,
                "environmentShape": environment_observation["environmentShape"],
            }
        )
        with closing(sqlite3.connect(self.db_path)) as conn:
            cursor = conn.execute(
                "INSERT INTO verification_events(source_generation,node_id,cause_snapshot_sha256,command_json,result_sha256,exit_code,status,completed_at,environment_shape,environment_core_shape) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (source_generation, node_id, identity_after, json.dumps(stored_command, ensure_ascii=False), result_identity, result.returncode, "verified", datetime.now(timezone.utc).isoformat(), environment_observation["environmentShape"], environment_observation["environmentCoreShape"]),
            )
            conn.commit()
        return {"ok": True, "status": "verified", "verificationEventId": int(cursor.lastrowid), "causeInputIdentity": identity_after, "resultIdentity": result_identity, "nodeId": node_id}

    def record_checkpoint(self, *, run_id: str, minute: int, elapsed_minutes: float | None = None) -> dict[str, Any]:
        if minute not in {45, 75, 90}:
            raise ValueError("checkpoint_minute_invalid")
        elapsed = float(minute if elapsed_minutes is None else elapsed_minutes)
        with closing(sqlite3.connect(self.db_path)) as conn:
            changed = conn.execute("INSERT OR IGNORE INTO checkpoints (run_id,minute,elapsed_minutes,recorded_at) VALUES (?,?,?,?)", (run_id, minute, elapsed, datetime.now(timezone.utc).isoformat())).rowcount
            conn.commit()
        return {
            "ok": True,
            "recorded": changed == 1,
            "minute": minute,
            "optionalHighCostFrozen": elapsed >= 75,
            "sloDebt": elapsed > 90,
            "continuePublicCriticalSuccessor": True,
        }

    def record_duration(self, *, run_id: str, bucket: str, duration_seconds: float) -> dict[str, Any]:
        if bucket not in {"generation", "verification", "external_wait", "pages_propagation", "retry"}:
            raise ValueError("duration_bucket_invalid")
        if duration_seconds < 0:
            raise ValueError("duration_negative")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("INSERT INTO durations (run_id,bucket,duration_seconds,recorded_at) VALUES (?,?,?,?)", (run_id, bucket, float(duration_seconds), datetime.now(timezone.utc).isoformat()))
            conn.commit()
        return {"ok": True, "bucket": bucket, "durationSeconds": float(duration_seconds)}


def _main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--repo-root", type=Path, required=True)
    capture.add_argument("--purpose", required=True)
    capture.add_argument("--run-id", default="")
    capture.add_argument("--run-intent", default="scheduled_production_direct")
    capture.add_argument("--issue-date", required=True)
    capture.add_argument("--manifest", type=Path, required=True)
    capture.add_argument("--runtime-state-root", type=Path, required=True)
    capture.add_argument("--output", type=Path, default=None)
    capture.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = capture_observation(repo_root=args.repo_root, purpose=args.purpose, run_id=args.run_id, run_intent=args.run_intent, issue_date=args.issue_date, manifest_path=args.manifest, runtime_state_root=args.runtime_state_root)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, args.output)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.get("python", {}).get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(_main())
