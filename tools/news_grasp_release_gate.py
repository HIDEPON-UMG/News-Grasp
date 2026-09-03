"""News-Grasp の Release gate。

このモジュールは Daily gate から呼び出してはならない Release 専用の境界である。
pytest の collection は Release authority が一回だけ取得し、collection で得た
node 集合だけを明示 registry の六 partition へ分ける。stdout は診断情報であり、
collection/result の正本には使わない。pytest plugin が UTF-8 の構造化 receipt を
作成し、親プロセスはその receipt と append-only ledger だけを採用する。

公開面への副作用はこのモジュールの責務ではない。NoPublish receipt は pytest の
node 集合とは別の一回限りの証跡として記録するだけで、外部送信を行わない。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RELEASE_PARTITIONS = (
    "scoped_changed",
    "known_constitution_regressions",
    "historical",
    "playwright",
    "crash_replay_drift",
    "general_complement",
)
RELEASE_GATE_SCHEMA = "NEWS_GRASP_RELEASE_PARTITION_RECEIPT_V1"
RELEASE_COLLECTION_SCHEMA = "NEWS_GRASP_RELEASE_COLLECTION_RECEIPT_V1"
RELEASE_NODE_REPORT_SCHEMA = "NEWS_GRASP_RELEASE_NODE_REPORT_V1"
RELEASE_LEDGER_SCHEMA = "NEWS_GRASP_RELEASE_LEDGER_V1"
RELEASE_PYTHON = r"C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe"
RELEASE_PLUGIN_MODULE = "tools.news_grasp_release_gate"
RELEASE_TIMEOUT_SECONDS = 60 * 60
RELEASE_LEDGER_ENV = "NEWS_GRASP_RELEASE_LEDGER_PATH"
RELEASE_ID_ENV = "NEWS_GRASP_RELEASE_ID"
RELEASE_SCHEMA_VERSION = "NEWS_GRASP_RELEASE_GATE_V2"
RELEASE_PROCESS_NODE_LIMIT = 160
RELEASE_PROCESS_SELECTOR_CHAR_LIMIT = 20_000

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_NODE_ADDRESS = re.compile(r" at 0x(?!ADDR\b)[0-9a-fA-F]+")
_LEDGER_LOCKS_GUARD = threading.Lock()
_LEDGER_LOCKS: dict[str, threading.Lock] = {}
_CANONICAL_AUTHORITY_EVENT_TYPES = frozenset(
    {
        "collection_started",
        "collection_completed",
        "partition_started",
        "partition_completed",
        "release_completed",
        "daily_promotion_issued",
    }
)
# This is an exact registry, not a name heuristic. Files not listed here can belong to
# general_complement only when they are a real test module in the authoritative repo.
# That explicit tests-root rule prevents an arbitrary caller supplied node from being
# treated as a Release node while permitting newly registered test files to be collected
# by the Release owner before they are assigned a special partition.
RELEASE_PARTITION_REGISTRY: dict[str, tuple[str, ...]] = {
    "scoped_changed": (
        "tests/test_news_grasp_daily_45m_contract.py",
        "tests/test_news_grasp_automation_appdb_cas.py",
        "tests/test_news_grasp_daily_control.py",
        "tests/test_news_grasp_daily_route_runtime_review.py",
        "tests/test_news_grasp_scoped_test_broker.py",
        "tests/test_news_grasp_direct_mainline_integration.py",
        "tests/test_news_grasp_direct_runtime.py",
        "tests/test_news_grasp_direct_runtime_v2.py",
        "tests/test_news_grasp_finalization.py",
        "tests/test_news_grasp_generation_boundary.py",
        "tests/test_news_grasp_publish_contract_v2.py",
        "tests/test_news_grasp_scheduled_admission.py",
    ),
    "known_constitution_regressions": (
        "tests/test_news_grasp_constitution_acceptance.py",
        "tests/test_news_grasp_constitution_projection.py",
        "tests/test_news_grasp_constitution_trace_compiler.py",
        "tests/test_operational_redesign_contract.py",
        "tests/test_operational_redesign_matrix.py",
        "tests/test_operational_redesign_r5_contract.py",
        "tests/test_product_spec_contract.py",
    ),
    "historical": (
        "tests/test_historical_failure_scenarios.py",
        "tests/test_news_grasp_compound_failure_corpus.py",
        "tests/test_news_grasp_monthly_failure_corpus.py",
    ),
    "playwright": (
        "tests/test_all_article_urls_live.py",
        "tests/test_deepdive_urls_live.py",
    ),
    "crash_replay_drift": (
        "tests/test_2026_08_14_recovery_replay.py",
        "tests/test_2026_08_15_recovery_replay.py",
        "tests/test_2026_08_16_recovery_replay.py",
        "tests/test_audit_recovery_control.py",
        "tests/test_recovery_state_gate.py",
        "tests/test_repair_runtime_e2e.py",
    ),
    "general_complement": (),
}
_SPECIAL_MODULE_PARTITIONS = {
    module: partition
    for partition, modules in RELEASE_PARTITION_REGISTRY.items()
    for module in modules
}
_KNOWN_REGISTERED_TEST_MODULES = frozenset(
    module
    for modules in RELEASE_PARTITION_REGISTRY.values()
    for module in modules
)

# A selector is a policy input, not a node id. It must never reach the fixed pytest
# command. Keeping this set explicit also makes review of future command additions easy.
_FORBIDDEN_SELECTOR_FLAGS = frozenset(
    {
        "-k",
        "--keyword",
        "--ignore",
        "--ignore-glob",
        "--deselect",
        "--maxfail",
        "-m",
        "--markers",
        "--lf",
        "--last-failed",
        "--ff",
        "--failed-first",
        "--new-first",
        "--nf",
        "--pdb",
        "--trace",
        "--collect-only",
        "--override-ini",
        "-o",
        "--config",
        "-c",
        "--rootdir",
        "--basetemp",
    }
)


class NewsGraspReleaseGateError(RuntimeError):
    """Release partition の構造違反。"""


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _node_hash(nodes: Sequence[str]) -> str:
    return hashlib.sha256(_json_bytes(list(nodes))).hexdigest()


def _mapping_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(dict(value))).hexdigest()


def _canonical_node_id(node: str) -> str:
    """processごとに変わるfunction reprのaddressをnode identityから除く。"""

    return _NODE_ADDRESS.sub(" at 0xADDR", node)


def _node_list(nodes: Iterable[str]) -> list[str]:
    if isinstance(nodes, (str, bytes, bytearray)):
        raise NewsGraspReleaseGateError("release_collection_nodes_invalid")
    try:
        result = list(nodes)
    except TypeError as exc:
        raise NewsGraspReleaseGateError("release_collection_nodes_invalid") from exc
    for node in result:
        if not isinstance(node, str) or not node.strip():
            raise NewsGraspReleaseGateError("release_collection_node_invalid")
        if node != node.strip() or "\x00" in node or "\r" in node or "\n" in node:
            raise NewsGraspReleaseGateError("release_collection_node_invalid")
        if node.startswith("-"):
            raise NewsGraspReleaseGateError("release_selector_or_node_flag_forbidden")
    if len(set(result)) != len(result):
        raise NewsGraspReleaseGateError("release_collection_duplicate_node")
    return result


def _canonical_node_list(nodes: Iterable[str]) -> list[str]:
    canonical = [_canonical_node_id(node) for node in _node_list(nodes)]
    if len(set(canonical)) != len(canonical):
        raise NewsGraspReleaseGateError("release_collection_duplicate_canonical_node")
    return canonical


def _validate_pytest_args(values: Sequence[str]) -> tuple[str, ...]:
    """pytest selectorを全面拒否する。

    Release の collection は repo root 全体の一回だけであり、caller の node、
    `-k`、`--ignore`、`--deselect`、`--maxfail` その他の引数を許可しない。
    この関数は旧API互換の引数名を残すが、空tuple以外は常に Red にする。
    """

    if isinstance(values, (str, bytes, bytearray)):
        raise NewsGraspReleaseGateError("release_pytest_args_invalid")
    try:
        result = tuple(values)
    except TypeError as exc:
        raise NewsGraspReleaseGateError("release_pytest_args_invalid") from exc
    if result:
        flag = next((item for item in result if isinstance(item, str)), "unknown")
        if flag in _FORBIDDEN_SELECTOR_FLAGS or flag.startswith(("-k", "--ignore", "--deselect", "--maxfail")):
            raise NewsGraspReleaseGateError(f"release_selector_forbidden:{flag}")
        raise NewsGraspReleaseGateError("release_caller_pytest_args_forbidden")
    return ()


def _repo_root(value: str | Path) -> Path:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise NewsGraspReleaseGateError("release_repo_root_invalid") from exc
    if not root.is_dir():
        raise NewsGraspReleaseGateError("release_repo_root_invalid")
    return root


def _safe_id(value: str | None, *, field: str, generate: bool = True) -> str:
    if value is None or not str(value).strip():
        if generate:
            return f"release-{uuid.uuid4().hex}"
        raise NewsGraspReleaseGateError(f"release_{field}_missing")
    text = str(value).strip()
    if not _SAFE_ID.fullmatch(text) or text.casefold() in {"final", "latest", "current", "alias"}:
        raise NewsGraspReleaseGateError(f"release_{field}_invalid")
    return text


def _validate_timeout(value: float | int | None) -> float:
    timeout = RELEASE_TIMEOUT_SECONDS if value is None else float(value)
    if timeout <= 0 or timeout > 24 * 60 * 60:
        raise NewsGraspReleaseGateError("release_timeout_invalid")
    return timeout


def _normalise_module(node: str) -> str:
    return node.split("::", 1)[0].replace("\\", "/")


def _registry_partition(node: str, *, repo_root: Path | None = None) -> str:
    module = _normalise_module(node)
    exact = _SPECIAL_MODULE_PARTITIONS.get(module)
    if exact is not None:
        return exact
    if module in _KNOWN_REGISTERED_TEST_MODULES:
        return "general_complement"
    # The general partition is explicit about its universe: only an actual Python
    # module below the authoritative tests root may enter it. A fabricated path or a
    # node from an unregistered tree is unknown and therefore fail-closed.
    if repo_root is not None and module.startswith("tests/"):
        candidate = (repo_root / Path(module)).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError as exc:
            raise NewsGraspReleaseGateError(f"release_registry_unknown_node:{node}") from exc
        if candidate.is_file() and candidate.suffix == ".py":
            return "general_complement"
    raise NewsGraspReleaseGateError(f"release_registry_unknown_node:{node}")


def validate_partition(
    collection_nodes: Iterable[str],
    partitions: Mapping[str, Iterable[str]],
) -> dict[str, Any]:
    """partitionの排他性・完全性・未知キーを実行前に検証する。"""

    collection = _node_list(collection_nodes)
    if not isinstance(partitions, Mapping):
        raise NewsGraspReleaseGateError("release_partition_invalid")
    if any(not isinstance(name, str) for name in partitions):
        raise NewsGraspReleaseGateError("release_partition_key_invalid")
    unknown = sorted(set(partitions) - set(RELEASE_PARTITIONS))
    if unknown:
        raise NewsGraspReleaseGateError(f"release_partition_unknown:{unknown}")
    normalized: dict[str, list[str]] = {name: [] for name in RELEASE_PARTITIONS}
    owner: dict[str, str] = {}
    for name, values in partitions.items():
        if isinstance(values, (str, bytes, bytearray)):
            raise NewsGraspReleaseGateError("release_partition_nodes_invalid")
        try:
            iterable = list(values)
        except TypeError as exc:
            raise NewsGraspReleaseGateError("release_partition_nodes_invalid") from exc
        for node in iterable:
            if not isinstance(node, str) or not node.strip():
                raise NewsGraspReleaseGateError("release_partition_node_invalid")
            _node_list([node])
            if node in owner:
                raise NewsGraspReleaseGateError(f"release_partition_overlap:{node}")
            owner[node] = name
            normalized[name].append(node)
    collection_set = set(collection)
    owner_set = set(owner)
    missing = sorted(collection_set - owner_set)
    extra = sorted(owner_set - collection_set)
    if missing:
        raise NewsGraspReleaseGateError(f"release_partition_missing:{missing}")
    if extra:
        raise NewsGraspReleaseGateError(f"release_partition_extra:{extra}")
    # Preserve authoritative collection order inside every partition. This makes the
    # node hash and repair set deterministic and prevents a caller from reordering a
    # receipt to disguise a replay.
    for name in RELEASE_PARTITIONS:
        members = set(normalized[name])
        normalized[name] = [node for node in collection if node in members]
    partition_hash = _mapping_hash({name: normalized[name] for name in RELEASE_PARTITIONS})
    return {
        "schemaVersion": RELEASE_GATE_SCHEMA,
        "ok": True,
        "status": "partition_validated",
        "partitions": normalized,
        "collection_count": len(collection),
        "collection_sha256": _node_hash(collection),
        "partition_sha256": partition_hash,
        "partition_count": len(RELEASE_PARTITIONS),
        "node_set": collection,
    }


def classify_collection_nodes(
    collection_nodes: Iterable[str],
    *,
    partitions: Mapping[str, Iterable[str]] | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """authoritative collection nodeを明示 registryへ一度だけ分類する。

    ``partitions`` は純粋な validator/test seam としてのみ受け付ける。production
    CLI は caller mapping を渡さず、この関数が固定 registryから分類する。
    """

    collection = _node_list(collection_nodes)
    if partitions is None:
        root = _repo_root(repo_root) if repo_root is not None else None
        classified: dict[str, list[str]] = {name: [] for name in RELEASE_PARTITIONS}
        for node in collection:
            classified[_registry_partition(node, repo_root=root)].append(node)
        partitions = classified
    return validate_partition(collection, partitions)


def _write_json_atomic(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(dict(value))
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _read_json_object(path: str | Path) -> Mapping[str, Any]:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise NewsGraspReleaseGateError("release_structured_receipt_bom")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NewsGraspReleaseGateError("release_structured_receipt_invalid_utf8_json") from exc
    if not isinstance(value, Mapping):
        raise NewsGraspReleaseGateError("release_structured_receipt_not_object")
    return value


def _default_ledger_path() -> Path:
    configured = str(os.environ.get(RELEASE_LEDGER_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local_app_data) if local_app_data else Path.home()
    return (base / "News-Grasp" / "release-gate" / "release_ledger.jsonl").resolve()


def _known_folder_local_app_data() -> Path:
    """環境変数で差替えられないWindows Known Folderを解決する。"""

    if os.name != "nt":
        raise NewsGraspReleaseGateError("release_windows_known_folder_required")
    try:
        import ctypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("data1", ctypes.c_uint32),
                ("data2", ctypes.c_uint16),
                ("data3", ctypes.c_uint16),
                ("data4", ctypes.c_ubyte * 8),
            ]

        raw = uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
        folder_id = _Guid.from_buffer_copy(raw)
        output = ctypes.c_wchar_p()
        status = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(output)
        )
        if status != 0 or not output.value:
            raise OSError(f"SHGetKnownFolderPath:{status}")
        try:
            return Path(output.value).resolve(strict=True)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(output)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise NewsGraspReleaseGateError("release_windows_known_folder_unavailable") from exc


def _canonical_ledger_path() -> Path:
    """production Release authorityが所有する固定ledger pathを返す。

    caller環境の ``NEWS_GRASP_RELEASE_LEDGER_PATH`` はtest/diagnostic seamにだけ
    使用し、production collection/runのauthorityには決して使わない。
    """

    return (_known_folder_local_app_data() / "News-Grasp" / "release-gate" / "release_ledger.jsonl").resolve()


def _canonical_daily_state_root() -> Path:
    return (_known_folder_local_app_data() / "News-Grasp" / "direct-mainline").resolve()


def _is_canonical_ledger(path: Path) -> bool:
    try:
        return path.resolve() == _canonical_ledger_path()
    except NewsGraspReleaseGateError:
        return False


def _canonical_ledger_mac_key(*, create: bool) -> bytes:
    key_path = _canonical_ledger_path().parent / "release_ledger.mac.key"
    if not key_path.is_file() and create:
        _write_bytes_atomic(key_path, secrets.token_bytes(32))
    try:
        key = key_path.read_bytes()
    except OSError as exc:
        raise NewsGraspReleaseGateError("release_ledger_mac_key_missing") from exc
    if len(key) != 32:
        raise NewsGraspReleaseGateError("release_ledger_mac_key_invalid")
    return key


@contextmanager
def _ledger_lock(path: Path):
    """thread/process横断でledger appendとpromotion pairを直列化する。"""

    target = path.resolve()
    lock_key = os.path.normcase(str(target))
    with _LEDGER_LOCKS_GUARD:
        process_lock = _LEDGER_LOCKS.setdefault(lock_key, threading.Lock())
    with process_lock:
        lock_path = target.with_name(f"{target.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "nt":
                import msvcrt

                deadline = time.monotonic() + 30.0
                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise NewsGraspReleaseGateError("release_ledger_lock_timeout")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                yield


def _ledger_file(path: str | Path | None) -> Path:
    target = _default_ledger_path() if path is None else Path(path).expanduser().resolve()
    if target.exists() and target.is_dir():
        raise NewsGraspReleaseGateError("release_ledger_path_is_directory")
    return target


def _ledger_events(path: str | Path | None) -> list[dict[str, Any]]:
    target = _ledger_file(path)
    if not target.exists():
        return []
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise NewsGraspReleaseGateError("release_ledger_read_failed") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise NewsGraspReleaseGateError("release_ledger_bom")
    events: list[dict[str, Any]] = []
    previous_hash = ""
    for line in raw.splitlines():
        if not line:
            continue
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NewsGraspReleaseGateError("release_ledger_corrupt") from exc
        if not isinstance(value, dict) or value.get("schemaVersion") != RELEASE_LEDGER_SCHEMA:
            raise NewsGraspReleaseGateError("release_ledger_schema_invalid")
        if value.get("prev_event_hash", "") != previous_hash:
            raise NewsGraspReleaseGateError("release_ledger_chain_invalid")
        event_hash = str(value.get("event_hash") or "")
        event_mac = str(value.get("event_mac") or "").casefold()
        unsigned = dict(value)
        unsigned.pop("event_hash", None)
        unsigned.pop("event_mac", None)
        if not _HEX64.fullmatch(event_hash) or hashlib.sha256(_json_bytes(unsigned)).hexdigest() != event_hash:
            raise NewsGraspReleaseGateError("release_ledger_event_hash_invalid")
        if _is_canonical_ledger(target):
            key = _canonical_ledger_mac_key(create=False)
            expected_mac = hmac.new(key, event_hash.encode("ascii"), hashlib.sha256).hexdigest()
            if not _HEX64.fullmatch(event_mac) or not hmac.compare_digest(event_mac, expected_mac):
                raise NewsGraspReleaseGateError("release_ledger_event_mac_invalid")
        previous_hash = event_hash
        events.append(value)
    return events


def _build_ledger_recorders():
    """raw authority writerをclosure内へ封じ、任意fields APIを公開しない。"""

    def write_locked(path: Path, event_type: str, fields: Mapping[str, Any]) -> dict[str, Any]:
        target = path.resolve()
        events = _ledger_events(target)
        event: dict[str, Any] = {
            "schemaVersion": RELEASE_LEDGER_SCHEMA,
            "event_id": f"release-event-{uuid.uuid4().hex}",
            "event_type": str(event_type),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "prev_event_hash": str(events[-1].get("event_hash") if events else ""),
            **dict(fields),
        }
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        if _is_canonical_ledger(target):
            key = _canonical_ledger_mac_key(create=True)
            event["event_mac"] = hmac.new(
                key, str(event["event_hash"]).encode("ascii"), hashlib.sha256
            ).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        line = _json_bytes(event) + b"\n"
        try:
            descriptor = os.open(str(target), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                written = os.write(descriptor, line)
                if written != len(line):
                    raise OSError("short ledger write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise NewsGraspReleaseGateError("release_ledger_append_failed") from exc
        return event

    def append_non_authority_locked(
        path: Path,
        event_type: str,
        **fields: Any,
    ) -> dict[str, Any]:
        if _is_canonical_ledger(path) and event_type in _CANONICAL_AUTHORITY_EVENT_TYPES:
            raise NewsGraspReleaseGateError("release_authority_event_generic_append_forbidden")
        return write_locked(path, event_type, fields)

    def record_collection(
        path: Path,
        *,
        repo_root: Path,
        release_id: str,
        timeout_seconds: float | int | None,
    ) -> dict[str, Any]:
        with _ledger_lock(path):
            write_locked(
                path,
                "collection_started",
                {
                    "release_id": release_id,
                    "repo_root": str(repo_root),
                    "authority": "release_authoritative_collect_only",
                },
            )
        process = _run_fixed_pytest(
            repo_root=repo_root,
            node_ids=(),
            collect_only=True,
            timeout_seconds=timeout_seconds,
        )
        result = _collection_result_from_process(
            process,
            release_id=release_id,
            ledger_path=path,
        )
        with _ledger_lock(path):
            write_locked(
                path,
                "collection_completed",
                {
                    "release_id": release_id,
                    "receipt": result,
                    "receipt_hash": _mapping_hash(result),
                },
            )
        return result

    def record_partition(
        path: Path,
        *,
        name: str,
        nodes: Sequence[str],
        repo_root: Path,
        timeout_seconds: float | int | None,
        release_id: str,
        collection_sha256: str,
        operation_id: str,
    ) -> dict[str, Any]:
        with _ledger_lock(path):
            write_locked(
                path,
                "partition_started",
                {
                    "release_id": release_id,
                    "partition": name,
                    "identity": f"{release_id}:{name}",
                    "collection_sha256": collection_sha256,
                    "node_ids": list(nodes),
                    "operation_id": operation_id,
                },
            )
        result = _run_partition_process(
            name,
            nodes,
            repo_root=repo_root,
            timeout_seconds=timeout_seconds,
            operation_id=operation_id,
        )
        result["release_id"] = release_id
        result["collection_sha256"] = collection_sha256
        if not result.get("cause_hash"):
            result["cause_hash"] = _mapping_hash(
                {
                    "partition": name,
                    "node_ids": list(nodes),
                    "failed_nodes": result.get("failed_nodes") or [],
                    "failure": result.get("failure"),
                }
            )
        with _ledger_lock(path):
            write_locked(
                path,
                "partition_completed",
                {
                    "release_id": release_id,
                    "partition": name,
                    "identity": f"{release_id}:{name}",
                    "collection_sha256": collection_sha256,
                    "receipt": result,
                    "receipt_hash": _mapping_hash(result),
                },
            )
        return result

    def record_release_locked(
        path: Path,
        *,
        release_receipt: Mapping[str, Any],
        chain: Mapping[str, Any],
    ) -> dict[str, Any]:
        return write_locked(
            path,
            "release_completed",
            {
                "release_id": chain["release_id"],
                "collection_sha256": chain["collection_sha256"],
                "collection_event_hash": chain["collection_event_hash"],
                "partition_receipt_hashes": chain["partition_receipt_hashes"],
                "receipt": dict(release_receipt),
                "receipt_hash": _mapping_hash(release_receipt),
            },
        )

    def record_promotion_locked(
        path: Path,
        *,
        release_id: str,
        event_hash: str,
        stored: Mapping[str, Any],
    ) -> dict[str, Any]:
        return write_locked(
            path,
            "daily_promotion_issued",
            {
                "release_id": release_id,
                "identity": event_hash,
                "release_event_hash": event_hash,
                "receipt": dict(stored),
                "receipt_hash": _mapping_hash(stored),
            },
        )

    def finalize_release_event(
        ledger_path: str | Path,
        release_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """完全なcollection/partition/node鎖を再検証してからだけ完了を書く。"""

        ledger = _ledger_file(ledger_path)
        with _ledger_lock(ledger):
            chain = _validate_release_completion_chain(ledger, release_receipt)
            return record_release_locked(
                ledger,
                release_receipt=release_receipt,
                chain=chain,
            )

    def ensure_daily_promotion(
        *,
        repo_root: str | Path,
        ledger: Path,
        release_event: Mapping[str, Any],
        release_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """canonical Release鎖を同一lock内で再検証しpromotionを一回だけ発行する。"""

        with _ledger_lock(ledger):
            if (
                release_receipt.get("schemaVersion") != RELEASE_GATE_SCHEMA
                or release_receipt.get("ok") is not True
                or release_receipt.get("status") != "green"
            ):
                raise NewsGraspReleaseGateError("daily_promotion_release_green_required")
            release_id = _safe_id(
                release_receipt.get("release_id"), field="release_id", generate=False
            )
            if ledger != _canonical_ledger_path():
                raise NewsGraspReleaseGateError("daily_promotion_noncanonical_ledger_forbidden")
            # callerのmappingだけを信頼せず、完了鎖をもう一度canonical ledgerから
            # 集約する。raw writerはこのclosure外へ一切公開しない。
            chain = _validate_release_completion_chain(ledger, release_receipt)
            if chain.get("green") is not True:
                raise NewsGraspReleaseGateError("daily_promotion_release_green_required")
            event_hash = str(release_event.get("event_hash") or "")
            authoritative = release_event.get("receipt")
            ledger_event = next(
                (
                    event
                    for event in _ledger_events(ledger)
                    if event.get("event_hash") == event_hash
                    and event.get("event_type") == "release_completed"
                    and event.get("release_id") == release_id
                ),
                None,
            )
            if (
                not isinstance(authoritative, Mapping)
                or _mapping_hash(authoritative) != _mapping_hash(release_receipt)
                or release_event.get("receipt_hash") != _mapping_hash(release_receipt)
                or not _HEX64.fullmatch(event_hash)
                or not isinstance(ledger_event, Mapping)
                or _mapping_hash(ledger_event) != _mapping_hash(release_event)
            ):
                raise NewsGraspReleaseGateError(
                    "daily_promotion_release_ledger_binding_invalid"
                )
            from tools import news_grasp_scoped_test_broker as broker

            existing = _latest_event(
                _ledger_events(ledger),
                release_id=release_id,
                event_type="daily_promotion_issued",
                identity=event_hash,
            )
            if existing is not None:
                stored = existing.get("receipt")
                loaded, failure = broker._load_trusted_promotion(
                    state_root=_canonical_daily_state_root(),
                    now=datetime.now(timezone.utc),
                )
                if (
                    not isinstance(stored, Mapping)
                    or not isinstance(loaded, Mapping)
                    or loaded.get("release_id") != release_id
                    or loaded.get("release_event_hash") != event_hash
                    or loaded.get("release_receipt_sha256")
                    != _mapping_hash(release_receipt)
                    or stored.get("release_event_hash") != event_hash
                ):
                    raise NewsGraspReleaseGateError(
                        f"daily_promotion_existing_receipt_invalid:{failure or 'binding'}"
                    )
                return dict(stored)

            issued = broker._issue_authorized_promotion(
                repo_root=_repo_root(repo_root),
                state_root=_canonical_daily_state_root(),
                release_event=release_event,
            )
            stored = {
                "schemaVersion": issued.get("schemaVersion"),
                "status": issued.get("status"),
                "release_id": issued.get("release_id"),
                "release_receipt_sha256": issued.get("release_receipt_sha256"),
                "release_event_hash": issued.get("release_event_hash"),
                "source_head": issued.get("source_head"),
                "source_tree": issued.get("source_tree"),
                "registry_sha256": issued.get("registry_sha256"),
                "issued_at": issued.get("issued_at"),
                "expires_at": issued.get("expires_at"),
                "receipt_path": issued.get("receipt_path"),
                "receipt_sha256": _mapping_hash(issued),
            }
            record_promotion_locked(
                ledger,
                release_id=release_id,
                event_hash=event_hash,
                stored=stored,
            )
            return stored

    return (
        append_non_authority_locked,
        record_collection,
        record_partition,
        finalize_release_event,
        ensure_daily_promotion,
    )


(
    _append_ledger_locked,
    _record_authoritative_collection,
    _record_authoritative_partition,
    _finalize_release_event,
    _ensure_daily_promotion,
) = _build_ledger_recorders()
del _build_ledger_recorders


def _append_ledger(path: str | Path | None, event_type: str, **fields: Any) -> dict[str, Any]:
    target = _ledger_file(path)
    if _is_canonical_ledger(target) and event_type in _CANONICAL_AUTHORITY_EVENT_TYPES:
        raise NewsGraspReleaseGateError("release_authority_event_generic_append_forbidden")
    with _ledger_lock(target):
        return _append_ledger_locked(target, event_type, **fields)


def _latest_event(
    events: Sequence[Mapping[str, Any]],
    *,
    release_id: str,
    event_type: str | None = None,
    partition: str | None = None,
    identity: str | None = None,
) -> Mapping[str, Any] | None:
    found: Mapping[str, Any] | None = None
    for event in events:
        if event.get("release_id") != release_id:
            continue
        if event_type is not None and event.get("event_type") != event_type:
            continue
        if partition is not None and event.get("partition") != partition:
            continue
        if identity is not None and event.get("identity") != identity:
            continue
        found = event
    return found


class _StructuredPytestPlugin:
    """pytest process内でcollectionとnode outcomeをJSON化するplugin。"""

    def __init__(self, output_path: str | Path, kind: str) -> None:
        self.output_path = Path(output_path)
        self.kind = kind
        self.collection_nodes: list[str] = []
        self.collection_errors: list[str] = []
        self.reports: dict[str, list[dict[str, Any]]] = {}
        self.allowed_nodes = self._load_allowed_nodes()

    def _load_allowed_nodes(self) -> set[str] | None:
        raw = str(os.environ.get("NEWS_GRASP_RELEASE_ALLOWED_NODES_FILE") or "").strip()
        if self.kind != "run":
            return None
        if not raw:
            return set()
        try:
            value = json.loads(Path(raw).read_text(encoding="utf-8"))
            if not isinstance(value, Mapping):
                return set()
            return set(_canonical_node_list(value.get("nodes") or ()))
        except (OSError, TypeError, ValueError, json.JSONDecodeError, NewsGraspReleaseGateError):
            return set()

    @staticmethod
    def _report_event(report: Any) -> dict[str, Any]:
        failed = bool(getattr(report, "failed", False))
        skipped = bool(getattr(report, "skipped", False))
        passed = bool(getattr(report, "passed", False))
        when = str(getattr(report, "when", "call"))
        if failed:
            status = "fail" if when == "call" else "error"
        elif skipped:
            status = "skip"
        elif passed:
            status = "pass"
        else:
            status = "error"
        return {
            "when": when,
            "status": status,
            "duration_seconds": float(getattr(report, "duration", 0.0) or 0.0),
        }

    def pytest_collection_finish(self, session: Any) -> None:
        self.collection_nodes = [
            _canonical_node_id(str(item.nodeid)) for item in getattr(session, "items", ())
        ]

    def pytest_collection_modifyitems(self, session: Any, config: Any, items: list[Any]) -> None:
        if self.kind != "run" or self.allowed_nodes is None:
            return
        selected: list[Any] = []
        deselected: list[Any] = []
        for item in items:
            if _canonical_node_id(str(item.nodeid)) in self.allowed_nodes:
                selected.append(item)
            else:
                deselected.append(item)
        items[:] = selected
        if deselected:
            config.hook.pytest_deselected(items=deselected)

    def pytest_collectreport(self, report: Any) -> None:
        if bool(getattr(report, "failed", False)):
            nodeid = _canonical_node_id(str(getattr(report, "nodeid", "<collection>")))
            self.collection_errors.append(nodeid)

    def pytest_runtest_logreport(self, report: Any) -> None:
        nodeid = _canonical_node_id(str(getattr(report, "nodeid", "")))
        if nodeid:
            self.reports.setdefault(nodeid, []).append(self._report_event(report))

    @staticmethod
    def _node_status(events: Sequence[Mapping[str, Any]]) -> str:
        if not events:
            return "error"
        statuses = [str(event.get("status")) for event in events]
        if any(status == "error" for status in statuses):
            return "error"
        if any(status == "fail" for status in statuses):
            return "fail"
        if any(status == "skip" for status in statuses):
            return "skip"
        return "pass"

    def _payload(self, *, exit_code: int | None = None) -> dict[str, Any]:
        collection = list(self.collection_nodes)
        node_results: list[dict[str, Any]] = []
        for node in collection:
            events = self.reports.get(node, [])
            node_results.append(
                {
                    "node_id": node,
                    "status": self._node_status(events),
                    "events": list(events),
                    "event_count": len(events),
                }
            )
        return {
            "schemaVersion": RELEASE_NODE_REPORT_SCHEMA,
            "kind": self.kind,
            "complete": True,
            "collection_complete": not bool(self.collection_errors),
            "collection_errors": list(self.collection_errors),
            "collection_nodes": collection,
            "collection_count": len(collection),
            "collection_sha256": _node_hash(collection),
            "node_results": node_results,
            "node_result_count": len(node_results),
            "exit_code": exit_code,
        }

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self.collection_nodes = [
            _canonical_node_id(str(item.nodeid)) for item in getattr(session, "items", ())
        ]
        payload = self._payload(exit_code=int(exitstatus))
        if self.kind == "collection":
            payload["schemaVersion"] = RELEASE_COLLECTION_SCHEMA
            payload.pop("node_results", None)
            payload.pop("node_result_count", None)
        _write_json_atomic(self.output_path, payload)


def pytest_configure(config: Any) -> None:
    """`-p tools.news_grasp_release_gate`でだけ有効になるpytest hook。"""

    output_path = str(os.environ.get("NEWS_GRASP_RELEASE_STRUCTURED_FILE") or "").strip()
    if not output_path:
        return
    kind = str(os.environ.get("NEWS_GRASP_RELEASE_STRUCTURED_KIND") or "run").strip().casefold()
    if kind not in {"collection", "run"}:
        kind = "run"
    plugin = _StructuredPytestPlugin(output_path, kind)
    setattr(config, "_news_grasp_release_plugin", plugin)
    config.pluginmanager.register(plugin, "news-grasp-release-structured")


def pytest_unconfigure(config: Any) -> None:
    plugin = getattr(config, "_news_grasp_release_plugin", None)
    if plugin is not None:
        try:
            config.pluginmanager.unregister(plugin)
        except Exception:
            pass


def _decode_capture(raw: bytes, *, field: str) -> str:
    try:
        # The complete capture is retained. It is diagnostic only; structured JSON is
        # the result authority, so no truncation or line parser can change gate state.
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NewsGraspReleaseGateError(f"release_{field}_not_utf8") from exc


def _process_env(
    structured_path: Path,
    kind: str,
    *,
    allowed_nodes_path: Path | None = None,
) -> dict[str, str]:
    env = {str(key): str(value) for key, value in os.environ.items()}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Ambient selectors/options would make collection non-authoritative.
    env["PYTEST_ADDOPTS"] = ""
    env["NEWS_GRASP_RELEASE_STRUCTURED_FILE"] = str(structured_path)
    env["NEWS_GRASP_RELEASE_STRUCTURED_KIND"] = kind
    if allowed_nodes_path is not None:
        env["NEWS_GRASP_RELEASE_ALLOWED_NODES_FILE"] = str(allowed_nodes_path)
    else:
        env.pop("NEWS_GRASP_RELEASE_ALLOWED_NODES_FILE", None)
    return env


def _validate_collection_report(report: Mapping[str, Any]) -> list[str]:
    if report.get("schemaVersion") != RELEASE_COLLECTION_SCHEMA:
        raise NewsGraspReleaseGateError("release_collection_structured_schema_invalid")
    if report.get("kind") != "collection" or report.get("complete") is not True:
        raise NewsGraspReleaseGateError("release_collection_structured_incomplete")
    if report.get("collection_complete") is not True:
        raise NewsGraspReleaseGateError("release_collection_errors_present")
    nodes = _node_list(report.get("collection_nodes") or ())
    if report.get("collection_count") != len(nodes):
        raise NewsGraspReleaseGateError("release_collection_count_mismatch")
    expected_hash = _node_hash(nodes)
    if report.get("collection_sha256") != expected_hash:
        raise NewsGraspReleaseGateError("release_collection_hash_mismatch")
    return nodes


def _validate_node_report(report: Mapping[str, Any], expected_nodes: Sequence[str]) -> list[dict[str, Any]]:
    if report.get("schemaVersion") != RELEASE_NODE_REPORT_SCHEMA:
        raise NewsGraspReleaseGateError("release_node_report_schema_invalid")
    if report.get("kind") != "run" or report.get("complete") is not True:
        raise NewsGraspReleaseGateError("release_node_report_incomplete")
    if report.get("collection_complete") is not True:
        raise NewsGraspReleaseGateError("release_node_report_collection_incomplete")
    collected = _canonical_node_list(report.get("collection_nodes") or ())
    expected = _canonical_node_list(expected_nodes)
    if collected != expected or report.get("collection_count") != len(expected):
        raise NewsGraspReleaseGateError("release_node_report_collection_mismatch")
    if report.get("collection_sha256") != _node_hash(expected):
        raise NewsGraspReleaseGateError("release_node_report_collection_hash_mismatch")
    raw_results = report.get("node_results")
    if not isinstance(raw_results, list) or len(raw_results) != len(expected):
        raise NewsGraspReleaseGateError("release_node_report_count_mismatch")
    by_node: dict[str, dict[str, Any]] = {}
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise NewsGraspReleaseGateError("release_node_result_invalid")
        node = raw.get("node_id")
        status = raw.get("status")
        if not isinstance(node, str) or node in by_node:
            raise NewsGraspReleaseGateError("release_node_result_identity_invalid")
        if status not in {"pass", "fail", "error", "skip"}:
            raise NewsGraspReleaseGateError("release_node_result_status_invalid")
        by_node[node] = dict(raw)
    if set(by_node) != set(expected):
        raise NewsGraspReleaseGateError("release_node_result_union_mismatch")
    return [by_node[node] for node in expected]


def _run_fixed_pytest(
    *,
    repo_root: str | Path,
    node_ids: Sequence[str] = (),
    collect_only: bool = False,
    timeout_seconds: float | int | None = None,
) -> dict[str, Any]:
    """固定Python、固定env、shell=False、timeout付きでpytestを起動する。"""

    root = _repo_root(repo_root)
    nodes = _canonical_node_list(node_ids)
    if collect_only and nodes:
        raise NewsGraspReleaseGateError("release_collect_node_args_forbidden")
    timeout = _validate_timeout(timeout_seconds)
    command = [RELEASE_PYTHON, "-m", "pytest", "-p", RELEASE_PLUGIN_MODULE]
    module_paths: list[str] = []
    for node in nodes:
        module = node.split("::", 1)[0]
        if module not in module_paths:
            module_paths.append(module)
    if collect_only:
        command.extend(["--collect-only", "-q"])
    else:
        command.append("-q")
        command.extend(module_paths)
    temp_root = Path(tempfile.mkdtemp(prefix="news-grasp-release-"))
    structured_path = temp_root / "pytest-result.json"
    allowed_nodes_path = temp_root / "allowed-nodes.json"
    kind = "collection" if collect_only else "run"
    if not collect_only:
        _write_json_atomic(allowed_nodes_path, {"nodes": nodes})
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    completed: Any = None
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_process_env(
                structured_path,
                kind,
                allowed_nodes_path=None if collect_only else allowed_nodes_path,
            ),
            shell=False,
            check=False,
            timeout=timeout,
            creationflags=creationflags,
        )
    except (subprocess.TimeoutExpired, TimeoutError):
        shutil.rmtree(temp_root, ignore_errors=True)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": False,
            "status": "transport_timeout",
            "transport": "timeout",
            "command": command,
            "node_ids": nodes,
            "timeout_seconds": timeout,
            "exit_code": None,
            "failures": ["release_process_timeout"],
        }
    except (OSError, ValueError) as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": False,
            "status": "process_spawn_red",
            "transport": "spawn",
            "command": command,
            "node_ids": nodes,
            "timeout_seconds": timeout,
            "exit_code": None,
            "failures": [f"release_process_spawn_error:{type(exc).__name__}"],
        }
    try:
        stdout = _decode_capture(bytes(completed.stdout or b""), field="stdout")
        stderr = _decode_capture(bytes(completed.stderr or b""), field="stderr")
    except NewsGraspReleaseGateError as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": False,
            "status": "transport_red",
            "transport": "decode",
            "command": command,
            "node_ids": nodes,
            "timeout_seconds": timeout,
            "exit_code": int(getattr(completed, "returncode", 1)),
            "failures": [str(exc)],
        }
    process: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
        "ok": int(getattr(completed, "returncode", 1)) == 0,
        "status": "green" if int(getattr(completed, "returncode", 1)) == 0 else "red",
        "transport": "ok",
        "command": command,
        "node_ids": nodes,
        "timeout_seconds": timeout,
        "exit_code": int(getattr(completed, "returncode", 1)),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        # Keep complete diagnostics; never parse or truncate them for gate decisions.
        "stdout": stdout,
        "stderr": stderr,
    }
    try:
        structured = _read_json_object(structured_path)
    except (OSError, NewsGraspReleaseGateError) as exc:
        process.update(
            {
                "ok": False,
                "status": "structured_report_missing",
                "failures": [str(exc) if isinstance(exc, NewsGraspReleaseGateError) else "release_structured_report_missing"],
            }
        )
        shutil.rmtree(temp_root, ignore_errors=True)
        return process
    process["structured"] = dict(structured)
    process["structured_sha256"] = _mapping_hash(structured)
    try:
        if collect_only:
            _validate_collection_report(structured)
        else:
            _validate_node_report(structured, nodes)
    except NewsGraspReleaseGateError as exc:
        process.update({"ok": False, "status": "structured_report_invalid", "failures": [str(exc)]})
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return process


def _collection_result_from_process(
    process: Mapping[str, Any],
    *,
    release_id: str,
    ledger_path: Path,
) -> dict[str, Any]:
    if process.get("ok") is not True:
        return {
            "schemaVersion": RELEASE_COLLECTION_SCHEMA,
            "ok": False,
            "status": "collection_red",
            "release_id": release_id,
            "collection_nodes": [],
            "collection_count": 0,
            "collection_sha256": "",
            "process": dict(process),
            "ledger_path": str(ledger_path),
        }
    report = process.get("structured")
    if not isinstance(report, Mapping):
        return {
            "schemaVersion": RELEASE_COLLECTION_SCHEMA,
            "ok": False,
            "status": "collection_structured_missing",
            "release_id": release_id,
            "collection_nodes": [],
            "process": dict(process),
            "ledger_path": str(ledger_path),
        }
    try:
        nodes = _validate_collection_report(report)
    except NewsGraspReleaseGateError as exc:
        return {
            "schemaVersion": RELEASE_COLLECTION_SCHEMA,
            "ok": False,
            "status": "collection_invalid",
            "release_id": release_id,
            "collection_nodes": [],
            "process": dict(process),
            "failures": [str(exc)],
            "ledger_path": str(ledger_path),
        }
    return {
        "schemaVersion": RELEASE_COLLECTION_SCHEMA,
        "ok": True,
        "status": "collection_produced",
        "authority": "release_authoritative_collect_only",
        "release_id": release_id,
        "collection_nodes": nodes,
        "collection_count": len(nodes),
        "collection_sha256": _node_hash(nodes),
        "node_set_hash": _node_hash(nodes),
        "process": dict(process),
        "ledger_path": str(ledger_path),
    }


def collect_only_nodes(
    repo_root: str | Path,
    *,
    pytest_args: Sequence[str] = (),
    timeout_seconds: float | int | None = None,
    ledger_path: str | Path | None = None,
    release_id: str | None = None,
    _test_only_allow_ledger_override: bool = False,
) -> dict[str, Any]:
    """固定Python 3.12でauthoritative collectionを一回だけ生成する。"""

    _validate_pytest_args(pytest_args)
    root = _repo_root(repo_root)
    rid = _safe_id(release_id or os.environ.get(RELEASE_ID_ENV), field="release_id")
    if ledger_path is not None and not _test_only_allow_ledger_override:
        raise NewsGraspReleaseGateError("release_production_ledger_override_forbidden")
    ledger = _ledger_file(ledger_path) if _test_only_allow_ledger_override else _canonical_ledger_path()
    events = _ledger_events(ledger)
    prior_complete = _latest_event(events, release_id=rid, event_type="collection_completed")
    if prior_complete is not None:
        receipt = prior_complete.get("receipt")
        if isinstance(receipt, Mapping) and receipt.get("ok") is True:
            attached = dict(receipt)
            attached["status"] = "collection_attached"
            attached["attached"] = True
            attached["ledger_path"] = str(ledger)
            return attached
        return {
            "schemaVersion": RELEASE_COLLECTION_SCHEMA,
            "ok": False,
            "status": "collection_previous_red",
            "release_id": rid,
            "ledger_path": str(ledger),
            "process": prior_complete.get("process"),
        }
    if _latest_event(events, release_id=rid, event_type="collection_started") is not None:
        return {
            "schemaVersion": RELEASE_COLLECTION_SCHEMA,
            "ok": False,
            "status": "collection_crash_attach_required",
            "release_id": rid,
            "ledger_path": str(ledger),
            "failures": ["release_collection_inflight"],
        }
    return _record_authoritative_collection(
        ledger,
        repo_root=root,
        release_id=rid,
        timeout_seconds=timeout_seconds,
    )


def _node_receipts_from_process(
    process: Mapping[str, Any],
    *,
    expected_nodes: Sequence[str],
    partition: str,
    receipt_id: str,
) -> tuple[list[dict[str, Any]], list[str], list[str], bool, str | None]:
    structured = process.get("structured")
    # A pytest assertion failure makes process.ok false, but its structured report is
    # still authoritative and must expose the exact failed node set for repair. Only a
    # transport/structured receipt failure removes node-level evidence.
    if process.get("transport") != "ok" or not isinstance(structured, Mapping):
        failure = str((process.get("failures") or [process.get("status")])[0])
        receipts = [
            {
                "node_id": node,
                "partition": partition,
                "status": "error",
                "events": [],
                "receipt_id": receipt_id,
                "failure": failure,
            }
            for node in expected_nodes
        ]
        return receipts, list(expected_nodes), [], False, failure
    try:
        rows = _validate_node_report(structured, expected_nodes)
    except NewsGraspReleaseGateError as exc:
        failure = str(exc)
        receipts = [
            {
                "node_id": node,
                "partition": partition,
                "status": "error",
                "events": [],
                "receipt_id": receipt_id,
                "failure": failure,
            }
            for node in expected_nodes
        ]
        return receipts, list(expected_nodes), [], False, failure
    receipts: list[dict[str, Any]] = []
    failed: list[str] = []
    skipped: list[str] = []
    for row in rows:
        node = str(row["node_id"])
        status = str(row["status"])
        receipts.append(
            {
                "node_id": node,
                "partition": partition,
                "status": status,
                "events": list(row.get("events") or ()),
                "receipt_id": receipt_id,
            }
        )
        if status in {"fail", "error"}:
            failed.append(node)
        elif status == "skip":
            skipped.append(node)
    # pytest treats skip as an exit-0 outcome; fail/error are the exact failed set.
    ok = not failed and int(process.get("exit_code", 1)) == 0
    return receipts, failed, skipped, ok, None


def _run_partition_process(
    name: str,
    nodes: Sequence[str],
    *,
    repo_root: str | Path,
    timeout_seconds: float | int | None,
    operation_id: str,
) -> dict[str, Any]:
    expected = _canonical_node_list(nodes)
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for node in expected:
        size = len(node) + 1
        if current and (
            len(current) >= RELEASE_PROCESS_NODE_LIMIT
            or current_chars + size > RELEASE_PROCESS_SELECTOR_CHAR_LIMIT
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(node)
        current_chars += size
    if current:
        chunks.append(current)
    timeout = _validate_timeout(timeout_seconds)
    started = time.monotonic()
    processes: list[dict[str, Any]] = []
    node_receipts: list[dict[str, Any]] = []
    failed: list[str] = []
    skipped: list[str] = []
    failures: list[str] = []
    all_ok = True
    for index, chunk in enumerate(chunks):
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            failure = f"partition_timeout_before_chunk_{index}"
            remaining_nodes = [node for pending in chunks[index:] for node in pending]
            node_receipts.extend(
                {
                    "node_id": node,
                    "partition": name,
                    "status": "error",
                    "events": [],
                    "receipt_id": operation_id,
                    "failure": failure,
                }
                for node in remaining_nodes
            )
            failed.extend(remaining_nodes)
            failures.append(failure)
            all_ok = False
            break
        remaining = timeout - elapsed
        process = _run_fixed_pytest(
            repo_root=repo_root,
            node_ids=chunk,
            collect_only=False,
            timeout_seconds=remaining,
        )
        process["chunk_index"] = index
        process["chunk_count"] = len(chunks)
        processes.append(process)
        receipts, chunk_failed, chunk_skipped, chunk_ok, chunk_failure = _node_receipts_from_process(
            process,
            expected_nodes=chunk,
            partition=name,
            receipt_id=operation_id,
        )
        node_receipts.extend(receipts)
        failed.extend(chunk_failed)
        skipped.extend(chunk_skipped)
        all_ok = all_ok and chunk_ok
        if chunk_failure:
            failures.append(f"chunk_{index}:{chunk_failure}")
    ok = all_ok and len(node_receipts) == len(expected)
    failure = ";".join(failures) if failures else None
    return {
        "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
        "receipt_id": operation_id,
        "partition": name,
        "node_ids": expected,
        "node_count": len(expected),
        "node_receipts": node_receipts,
        "failed_nodes": failed,
        "skipped_nodes": skipped,
        "exact_failed_set_sha256": _node_hash(failed),
        "process_count": len(processes),
        "processes": processes,
        "process": processes[0] if len(processes) == 1 else None,
        "ok": ok,
        "status": "green" if ok else "red",
        "failure": failure,
    }


def _attached_partition_receipt(
    event: Mapping[str, Any],
    *,
    expected_nodes: Sequence[str],
    collection_sha256: str,
) -> dict[str, Any] | None:
    receipt = event.get("receipt")
    if not isinstance(receipt, Mapping):
        return None
    if receipt.get("collection_sha256") != collection_sha256:
        return None
    if list(receipt.get("node_ids") or ()) != list(expected_nodes):
        return None
    result = dict(receipt)
    result["attached"] = True
    result["process_count"] = 0
    result["status"] = "attached" if result.get("ok") is True else str(result.get("status") or "red")
    return result


def _partition_process_receipt(
    name: str,
    nodes: Sequence[str],
    *,
    repo_root: str | Path,
    timeout_seconds: float | int | None,
    release_id: str,
    collection_sha256: str,
    ledger_path: Path,
) -> dict[str, Any]:
    operation_id = f"release-partition-{name}-{uuid.uuid4().hex}"
    events = _ledger_events(ledger_path)
    completed = _latest_event(events, release_id=release_id, event_type="partition_completed", partition=name)
    if completed is not None:
        attached = _attached_partition_receipt(
            completed,
            expected_nodes=nodes,
            collection_sha256=collection_sha256,
        )
        if attached is not None:
            return attached
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
            "receipt_id": operation_id,
            "partition": name,
            "node_ids": list(nodes),
            "node_count": len(nodes),
            "node_receipts": [],
            "failed_nodes": [],
            "skipped_nodes": [],
            "process_count": 0,
            "ok": False,
            "status": "red",
            "failure": "release_partition_receipt_binding_mismatch",
        }
    if _latest_event(events, release_id=release_id, event_type="partition_started", partition=name) is not None:
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
            "receipt_id": operation_id,
            "partition": name,
            "node_ids": list(nodes),
            "node_count": len(nodes),
            "node_receipts": [],
            "failed_nodes": [],
            "skipped_nodes": [],
            "process_count": 0,
            "ok": False,
            "status": "crash_attach_required",
            "failure": "release_partition_inflight",
        }
    return _record_authoritative_partition(
        ledger_path,
        name=name,
        nodes=nodes,
        repo_root=_repo_root(repo_root),
        timeout_seconds=timeout_seconds,
        release_id=release_id,
        collection_sha256=collection_sha256,
        operation_id=operation_id,
    )


def _validate_release_completion_chain(
    ledger_path: str | Path,
    release_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Release完了receiptをauthoritative collection/partition eventから再集約する。"""

    ledger = _ledger_file(ledger_path)
    if not isinstance(release_receipt, Mapping):
        raise NewsGraspReleaseGateError("release_completion_receipt_invalid")
    release_id = _safe_id(release_receipt.get("release_id"), field="release_id", generate=False)
    classification = release_receipt.get("partition")
    if not isinstance(classification, Mapping):
        raise NewsGraspReleaseGateError("release_completion_partition_missing")
    collection_nodes = _node_list(classification.get("node_set") or ())
    if not collection_nodes:
        raise NewsGraspReleaseGateError("release_completion_collection_empty")
    collection_sha256 = _node_hash(collection_nodes)
    if (
        release_receipt.get("schemaVersion") != RELEASE_GATE_SCHEMA
        or release_receipt.get("collection_sha256") != collection_sha256
        or classification.get("collection_sha256") != collection_sha256
        or int(release_receipt.get("union_node_count") or 0) != len(collection_nodes)
        or int(release_receipt.get("executed_node_count") or 0) != len(collection_nodes)
    ):
        raise NewsGraspReleaseGateError("release_completion_collection_binding_invalid")
    raw_partitions = classification.get("partitions")
    if not isinstance(raw_partitions, Mapping) or set(raw_partitions) != set(RELEASE_PARTITIONS):
        raise NewsGraspReleaseGateError("release_completion_partition_registry_invalid")
    normalized = validate_partition(collection_nodes, raw_partitions)
    if _mapping_hash(normalized) != _mapping_hash(classification):
        raise NewsGraspReleaseGateError("release_completion_partition_classification_invalid")

    events = _ledger_events(ledger)
    collection_event = _latest_event(
        events,
        release_id=release_id,
        event_type="collection_completed",
    )
    collection_receipt = (
        collection_event.get("receipt") if isinstance(collection_event, Mapping) else None
    )
    if (
        not isinstance(collection_receipt, Mapping)
        or collection_receipt.get("authority") != "release_authoritative_collect_only"
        or collection_receipt.get("ok") is not True
        or collection_receipt.get("release_id") != release_id
        or list(collection_receipt.get("collection_nodes") or ()) != collection_nodes
        or collection_receipt.get("collection_sha256") != collection_sha256
        or collection_event.get("receipt_hash") != _mapping_hash(collection_receipt)
    ):
        raise NewsGraspReleaseGateError("release_completion_collection_event_invalid")

    listed_receipts = release_receipt.get("partition_receipts")
    if not isinstance(listed_receipts, list):
        raise NewsGraspReleaseGateError("release_completion_partition_receipts_invalid")
    listed_by_partition: dict[str, Mapping[str, Any]] = {}
    for receipt in listed_receipts:
        if not isinstance(receipt, Mapping):
            raise NewsGraspReleaseGateError("release_completion_partition_receipt_invalid")
        name = str(receipt.get("partition") or "")
        if name in listed_by_partition or name not in RELEASE_PARTITIONS:
            raise NewsGraspReleaseGateError("release_completion_partition_receipt_duplicate")
        listed_by_partition[name] = receipt

    expected_results: list[dict[str, Any]] = []
    authoritative_receipts: list[dict[str, Any]] = []
    all_partition_green = True
    for name in RELEASE_PARTITIONS:
        nodes = list(normalized["partitions"].get(name) or ())
        if not nodes:
            if name in listed_by_partition:
                raise NewsGraspReleaseGateError("release_completion_empty_partition_receipt")
            continue
        event = _latest_event(
            events,
            release_id=release_id,
            event_type="partition_completed",
            partition=name,
        )
        authoritative = event.get("receipt") if isinstance(event, Mapping) else None
        listed = listed_by_partition.get(name)
        if (
            not isinstance(authoritative, Mapping)
            or not isinstance(listed, Mapping)
            or event.get("receipt_hash") != _mapping_hash(authoritative)
            or _mapping_hash(listed) != _mapping_hash(authoritative)
            or authoritative.get("release_id") != release_id
            or authoritative.get("partition") != name
            or list(authoritative.get("node_ids") or ()) != nodes
            or authoritative.get("collection_sha256") != collection_sha256
        ):
            raise NewsGraspReleaseGateError("release_completion_partition_event_invalid")
        node_receipts = authoritative.get("node_receipts")
        if not isinstance(node_receipts, list):
            raise NewsGraspReleaseGateError("release_completion_node_receipts_invalid")
        if node_receipts:
            if [str(item.get("node_id") or "") for item in node_receipts if isinstance(item, Mapping)] != nodes:
                raise NewsGraspReleaseGateError("release_completion_node_union_invalid")
            for item in node_receipts:
                if not isinstance(item, Mapping) or item.get("partition") != name:
                    raise NewsGraspReleaseGateError("release_completion_node_receipt_invalid")
                expected_results.append(dict(item))
        else:
            for node in nodes:
                expected_results.append(
                    {
                        "node_id": node,
                        "partition": name,
                        "status": "error",
                        "result": {
                            "status": authoritative.get("status"),
                            "failure": authoritative.get("failure"),
                        },
                    }
                )
        authoritative_receipts.append(dict(authoritative))
        all_partition_green = all_partition_green and authoritative.get("ok") is True

    if set(listed_by_partition) != {
        name for name in RELEASE_PARTITIONS if normalized["partitions"].get(name)
    }:
        raise NewsGraspReleaseGateError("release_completion_partition_union_invalid")
    top_results = release_receipt.get("node_receipts")
    if (
        not isinstance(top_results, list)
        or hashlib.sha256(_json_bytes(top_results)).hexdigest()
        != hashlib.sha256(_json_bytes(expected_results)).hexdigest()
    ):
        raise NewsGraspReleaseGateError("release_completion_node_receipt_union_invalid")
    failed_nodes = [
        str(item.get("node_id") or "")
        for item in expected_results
        if item.get("status") in {"fail", "error"}
    ]
    green = (
        all_partition_green
        and not failed_nodes
        and len(expected_results) == len(collection_nodes)
        and all(item.get("status") in {"pass", "skip"} for item in expected_results)
    )
    if (
        list(release_receipt.get("failed_nodes") or ()) != failed_nodes
        or release_receipt.get("ok") is not green
        or release_receipt.get("status") != ("green" if green else "blocked")
    ):
        raise NewsGraspReleaseGateError("release_completion_status_invalid")
    return {
        "release_id": release_id,
        "collection_sha256": collection_sha256,
        "collection_event_hash": str(collection_event.get("event_hash") or ""),
        "partition_receipt_hashes": [_mapping_hash(item) for item in authoritative_receipts],
        "node_count": len(collection_nodes),
        "green": green,
    }


def execute_partitioned_nodes(
    collection_nodes: Iterable[str],
    *,
    partitions: Mapping[str, Iterable[str]] | None = None,
    runner: Callable[[str], Mapping[str, Any]] | None = None,
    repo_root: str | Path | None = None,
    collection_receipt: Mapping[str, Any] | None = None,
    release_id: str | None = None,
    ledger_path: str | Path | None = None,
    timeout_seconds: float | int | None = None,
    _test_only_allow_ledger_override: bool = False,
) -> dict[str, Any]:
    """collection集合をpartitionごとに一回だけ実行する。

    production (`repo_root`指定) では authoritative collection receiptが必須で、
    callerのpartition mapping/runnerを受け付けない。runnerは純粋なtest seamとして
    repo rootなしの場合だけ残している。
    """

    collection = _node_list(collection_nodes)
    if repo_root is not None:
        if runner is not None:
            raise NewsGraspReleaseGateError("release_runner_injection_forbidden")
        if partitions is not None:
            raise NewsGraspReleaseGateError("release_partition_override_forbidden")
        if (
            not isinstance(collection_receipt, Mapping)
            or collection_receipt.get("ok") is not True
            or collection_receipt.get("authority") != "release_authoritative_collect_only"
        ):
            raise NewsGraspReleaseGateError("release_authoritative_collection_receipt_required")
        receipt_nodes = _node_list(collection_receipt.get("collection_nodes") or ())
        if receipt_nodes != collection or collection_receipt.get("collection_sha256") != _node_hash(collection):
            raise NewsGraspReleaseGateError("release_authoritative_collection_binding_mismatch")
        root = _repo_root(repo_root)
        rid = _safe_id(release_id or collection_receipt.get("release_id"), field="release_id")
        if ledger_path is not None and not _test_only_allow_ledger_override:
            raise NewsGraspReleaseGateError("release_production_ledger_override_forbidden")
        ledger = (
            _ledger_file(ledger_path or collection_receipt.get("ledger_path"))
            if _test_only_allow_ledger_override
            else _canonical_ledger_path()
        )
        if str(collection_receipt.get("ledger_path") or "") != str(ledger):
            raise NewsGraspReleaseGateError("release_authoritative_collection_ledger_mismatch")
        if not _test_only_allow_ledger_override:
            collection_event = _latest_event(
                _ledger_events(ledger),
                release_id=rid,
                event_type="collection_completed",
            )
            authoritative_collection = (
                collection_event.get("receipt") if isinstance(collection_event, Mapping) else None
            )
            if (
                not isinstance(authoritative_collection, Mapping)
                or _mapping_hash(authoritative_collection) != _mapping_hash(collection_receipt)
                or collection_event.get("receipt_hash") != _mapping_hash(collection_receipt)
            ):
                raise NewsGraspReleaseGateError(
                    "release_authoritative_collection_ledger_binding_invalid"
                )
        classification = classify_collection_nodes(collection, repo_root=root)
        prior_release = _latest_event(
            _ledger_events(ledger),
            release_id=rid,
            event_type="release_completed",
        )
        if prior_release is not None:
            prior = prior_release.get("receipt")
            if isinstance(prior, Mapping) and prior.get("collection_sha256") == classification["collection_sha256"]:
                if prior.get("ok") is True and not _test_only_allow_ledger_override:
                    _ensure_daily_promotion(
                        repo_root=root,
                        ledger=ledger,
                        release_event=prior_release,
                        release_receipt=prior,
                    )
                attached_release = dict(prior)
                attached_release["status"] = "release_attached"
                attached_release["attached"] = True
                attached_release["executed_process_count"] = 0
                attached_release["ledger_path"] = str(ledger)
                return attached_release
    else:
        if runner is None and partitions is None:
            raise NewsGraspReleaseGateError("release_test_seam_requires_runner_or_partitions")
        root = None
        rid = _safe_id(release_id, field="release_id")
        ledger = _ledger_file(ledger_path) if ledger_path is not None else _default_ledger_path()
        classification = classify_collection_nodes(collection, partitions=partitions)
    if not collection:
        raise NewsGraspReleaseGateError("release_collection_empty")
    ordered = list(classification["node_set"])
    partition_for = {
        node: name
        for name, values in classification["partitions"].items()
        for node in values
    }
    results: list[dict[str, Any]] = []
    partition_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    if runner is not None:
        # Test-only node seam. Production CLI has no path to inject it.
        for node in ordered:
            if node in seen:
                raise NewsGraspReleaseGateError(f"release_node_replay:{node}")
            seen.add(node)
            result = runner(node)
            if not isinstance(result, Mapping):
                raise NewsGraspReleaseGateError(f"release_node_result_invalid:{node}")
            status = result.get("status")
            if status is None:
                status = "pass" if result.get("ok") is True else "error"
            if status not in {"pass", "fail", "error", "skip"}:
                raise NewsGraspReleaseGateError(f"release_node_result_status_invalid:{node}")
            results.append(
                {
                    "node_id": node,
                    "partition": partition_for[node],
                    "status": status,
                    "result": dict(result),
                }
            )
        failed = [item["node_id"] for item in results if item["status"] in {"fail", "error"}]
        green = not failed and all(item["status"] in {"pass", "skip"} for item in results)
        return {
            "schemaVersion": RELEASE_GATE_SCHEMA,
            "ok": green,
            "status": "green" if green else "blocked",
            "partition": classification,
            "node_receipts": results,
            "partition_receipts": [],
            "executed_node_count": len(seen),
            "executed_process_count": 0,
            "union_node_count": len(ordered),
            "failed_nodes": failed,
            "collection_sha256": classification["collection_sha256"],
            "release_id": rid,
        }
    if root is None:
        raise NewsGraspReleaseGateError("release_production_repo_root_required")
    for name in RELEASE_PARTITIONS:
        nodes = list(classification["partitions"].get(name) or ())
        if not nodes:
            continue
        group = _partition_process_receipt(
            name,
            nodes,
            repo_root=root,
            timeout_seconds=timeout_seconds,
            release_id=rid,
            collection_sha256=classification["collection_sha256"],
            ledger_path=ledger,
        )
        partition_results.append(group)
        seen.update(nodes)
        for item in group.get("node_receipts") or ():
            results.append(dict(item))
        if not group.get("node_receipts"):
            # Transport/crash receipt has no node result. Preserve exact node identity
            # as red rather than silently treating an absent report as Green.
            for node in nodes:
                results.append(
                    {
                        "node_id": node,
                        "partition": name,
                        "status": "error",
                        "result": {"status": group.get("status"), "failure": group.get("failure")},
                    }
                )
    if set(seen) != set(ordered) or len(seen) != len(ordered):
        raise NewsGraspReleaseGateError("release_partition_union_mismatch")
    failed_nodes = [
        str(item["node_id"])
        for item in results
        if item.get("status") in {"fail", "error"}
    ]
    green = (
        len(seen) == len(ordered)
        and not failed_nodes
        and all(item.get("ok") is True for item in partition_results)
    )
    authoritative_partition_results: list[dict[str, Any]] = []
    completed_events = _ledger_events(ledger)
    for name in RELEASE_PARTITIONS:
        if not classification["partitions"].get(name):
            continue
        event = _latest_event(
            completed_events,
            release_id=rid,
            event_type="partition_completed",
            partition=name,
        )
        receipt = event.get("receipt") if isinstance(event, Mapping) else None
        if not isinstance(receipt, Mapping):
            raise NewsGraspReleaseGateError("release_partition_authoritative_receipt_missing")
        authoritative_partition_results.append(dict(receipt))
    result = {
        "schemaVersion": RELEASE_GATE_SCHEMA,
        "ok": green,
        "status": "green" if green else "blocked",
        "partition": classification,
        "node_receipts": results,
        "partition_receipts": authoritative_partition_results,
        "executed_node_count": len(seen),
        "executed_process_count": sum(int(item.get("process_count", 0)) for item in partition_results),
        "union_node_count": len(ordered),
        "failed_nodes": failed_nodes,
        "collection_sha256": classification["collection_sha256"],
        "release_id": rid,
        "ledger_path": str(ledger),
    }
    release_event = _finalize_release_event(ledger, result)
    if green and not _test_only_allow_ledger_override:
        _ensure_daily_promotion(
            repo_root=root,
            ledger=ledger,
            release_event=release_event,
            release_receipt=result,
        )
    return result


def causal_repair_partition(
    *,
    repo_root: str | Path,
    partition: str,
    node_ids: Iterable[str],
    cause_hash: str,
    previous_receipt: Mapping[str, Any],
    repair_id: str | None = None,
    release_id: str | None = None,
    ledger_path: str | Path | None = None,
    timeout_seconds: float | int | None = None,
) -> dict[str, Any]:
    """直前receiptのexact failed setだけを原因変更一回分として修復する。"""

    if partition not in RELEASE_PARTITIONS:
        raise NewsGraspReleaseGateError("release_partition_unknown")
    if not isinstance(previous_receipt, Mapping) or previous_receipt.get("ok") is not False:
        raise NewsGraspReleaseGateError("release_repair_previous_receipt_not_failed")
    cause = str(cause_hash or "").strip()
    if not _HEX64.fullmatch(cause):
        raise NewsGraspReleaseGateError("release_repair_cause_hash_invalid")
    previous_cause = str(previous_receipt.get("cause_hash") or previous_receipt.get("causeHash") or "")
    if previous_cause and previous_cause == cause:
        raise NewsGraspReleaseGateError("release_repair_same_cause_rejected")
    nodes = _node_list(node_ids)
    if not nodes:
        raise NewsGraspReleaseGateError("release_repair_nodes_empty")
    previous_nodes = _node_list(previous_receipt.get("node_ids") or ())
    raw_failed = previous_receipt.get("failed_nodes")
    if raw_failed is None:
        raw_failed = [
            str(item.get("node_id"))
            for item in (previous_receipt.get("node_receipts") or ())
            if isinstance(item, Mapping) and item.get("status") in {"fail", "error"}
        ]
    failed_nodes = _node_list(raw_failed or ())
    if (
        not failed_nodes
        and previous_nodes
        and not list(previous_receipt.get("node_receipts") or ())
        and previous_receipt.get("failure")
    ):
        # V2導入前のtransport/structured failure receiptはfailed_nodesを空で
        # 保存していた。successを推測せず、束縛済みpartition全件をexact Redとする。
        failed_nodes = list(previous_nodes)
    if set(nodes) != set(failed_nodes) or set(failed_nodes) - set(previous_nodes):
        raise NewsGraspReleaseGateError("release_repair_exact_failed_set_required")
    rid = _safe_id(release_id or previous_receipt.get("release_id"), field="release_id", generate=False)
    repair = _safe_id(repair_id, field="repair_id")
    ledger = _ledger_file(ledger_path or previous_receipt.get("ledger_path"))
    events = _ledger_events(ledger)
    previous_id = str(previous_receipt.get("receipt_id") or previous_receipt.get("partition_receipt_id") or "")
    if not previous_id:
        raise NewsGraspReleaseGateError("release_repair_previous_receipt_id_missing")
    authoritative_previous = None
    authoritative_event = None
    for event in events:
        if event.get("release_id") != rid or event.get("event_type") != "partition_completed":
            continue
        receipt = event.get("receipt")
        if isinstance(receipt, Mapping) and receipt.get("receipt_id") == previous_id:
            authoritative_previous = receipt
            authoritative_event = event
            break
    if authoritative_previous is None:
        raise NewsGraspReleaseGateError("release_repair_previous_receipt_not_in_ledger")
    authoritative_nodes = _node_list(authoritative_previous.get("node_ids") or ())
    authoritative_failed = _node_list(authoritative_previous.get("failed_nodes") or ())
    if (
        not authoritative_failed
        and authoritative_nodes
        and not list(authoritative_previous.get("node_receipts") or ())
        and authoritative_previous.get("failure")
    ):
        authoritative_failed = list(authoritative_nodes)
    if previous_nodes != authoritative_nodes or failed_nodes != authoritative_failed:
        raise NewsGraspReleaseGateError("release_repair_previous_receipt_binding_mismatch")
    if isinstance(authoritative_event, Mapping) and authoritative_event.get("receipt_hash") != _mapping_hash(dict(previous_receipt)):
        raise NewsGraspReleaseGateError("release_repair_previous_receipt_hash_mismatch")
    authoritative_cause = str(authoritative_previous.get("cause_hash") or "")
    if not _HEX64.fullmatch(authoritative_cause):
        raise NewsGraspReleaseGateError("release_repair_previous_cause_hash_missing")
    if authoritative_cause == cause:
        raise NewsGraspReleaseGateError("release_repair_same_cause_rejected")
    prior_repair = _latest_event(events, release_id=rid, event_type="repair_completed", identity=repair)
    if prior_repair is not None or _latest_event(events, release_id=rid, event_type="repair_started", identity=repair) is not None:
        raise NewsGraspReleaseGateError("release_repair_id_replay")
    if any(
        event.get("release_id") == rid
        and event.get("event_type") == "repair_completed"
        and event.get("previous_receipt_id") == previous_id
        for event in events
    ):
        raise NewsGraspReleaseGateError("release_repair_already_applied")
    collection_sha256 = str(authoritative_previous.get("collection_sha256") or "")
    if not _HEX64.fullmatch(collection_sha256):
        raise NewsGraspReleaseGateError("release_repair_collection_binding_missing")
    _append_ledger(
        ledger,
        "repair_started",
        release_id=rid,
        partition=partition,
        identity=repair,
        repair_id=repair,
        previous_receipt_id=previous_id,
        cause_hash=cause,
        node_ids=list(nodes),
        collection_sha256=collection_sha256,
    )
    group = _run_partition_process(
        partition,
        nodes,
        repo_root=_repo_root(repo_root),
        timeout_seconds=timeout_seconds,
        operation_id=repair,
    )
    result = {
        "schemaVersion": "NEWS_GRASP_RELEASE_CAUSAL_REPAIR_RECEIPT_V2",
        "ok": group.get("ok") is True,
        "status": "repair_green" if group.get("ok") is True else "repair_red",
        "repair": True,
        "repair_id": repair,
        "cause_hash": cause,
        "previous_receipt_id": previous_id,
        "exact_failed_nodes": list(nodes),
        "collection_sha256": collection_sha256,
        "partition_receipt": group,
        "automatic_retry": False,
        "process_count": int(group.get("process_count", 0)),
    }
    _append_ledger(
        ledger,
        "repair_completed",
        release_id=rid,
        partition=partition,
        identity=repair,
        repair_id=repair,
        previous_receipt_id=previous_id,
        cause_hash=cause,
        receipt=result,
        receipt_hash=_mapping_hash(result),
    )
    return result


def record_nopublish_receipt(
    *,
    release_id: str,
    receipt: Mapping[str, Any],
    ledger_path: str | Path | None = None,
) -> dict[str, Any]:
    """pytest union外のfinal NoPublish receiptを一度だけ永続化する。

    この関数は外部送信も公開完了判定も行わない。`external_mutation_count=0` と
    `pytest_union_excluded=true` を強制し、同じreleaseの二回目はRedにする。
    """

    rid = _safe_id(release_id, field="release_id", generate=False)
    if not isinstance(receipt, Mapping):
        raise NewsGraspReleaseGateError("release_nopublish_receipt_invalid")
    if receipt.get("ok") is not True:
        raise NewsGraspReleaseGateError("release_nopublish_receipt_not_green")
    mode = str(receipt.get("mode") or receipt.get("run_mode") or "").casefold()
    if mode not in {"nopublish", "no_publish", "no-publish"}:
        raise NewsGraspReleaseGateError("release_nopublish_mode_required")
    if "external_mutation_count" not in receipt and "externalMutationCount" not in receipt:
        raise NewsGraspReleaseGateError("release_nopublish_side_effect_count_missing")
    mutation_count = receipt.get("external_mutation_count", receipt.get("externalMutationCount"))
    if mutation_count != 0:
        raise NewsGraspReleaseGateError("release_nopublish_side_effect")
    if receipt.get("pytest_union_excluded") is not True and receipt.get("pytestUnionExcluded") is not True:
        raise NewsGraspReleaseGateError("release_nopublish_union_binding_missing")
    for key in ("node_ids", "collection_nodes", "pytest_nodes"):
        if key in receipt and receipt.get(key):
            raise NewsGraspReleaseGateError("release_nopublish_pytest_union_overlap")
    attempt_id = str(receipt.get("attempt_id") or receipt.get("attemptId") or "").strip()
    if not _SAFE_ID.fullmatch(attempt_id):
        raise NewsGraspReleaseGateError("release_nopublish_attempt_id_invalid")
    ledger = _ledger_file(ledger_path)
    events = _ledger_events(ledger)
    if _latest_event(events, release_id=rid, event_type="nopublish_completed") is not None:
        raise NewsGraspReleaseGateError("release_nopublish_duplicate")
    stored = dict(receipt)
    stored["receipt_sha256"] = _mapping_hash(receipt)
    event = _append_ledger(
        ledger,
        "nopublish_completed",
        release_id=rid,
        identity=attempt_id,
        attempt_id=attempt_id,
        pytest_union_excluded=True,
        external_mutation_count=0,
        receipt=stored,
        receipt_hash=_mapping_hash(stored),
    )
    return {
        "schemaVersion": "NEWS_GRASP_RELEASE_NOPUBLISH_RECEIPT_V1",
        "ok": True,
        "status": "nopublish_recorded",
        "release_id": rid,
        "attempt_id": attempt_id,
        "receipt": stored,
        "ledger_event_id": event["event_id"],
        "ledger_path": str(ledger),
        "pytest_union_excluded": True,
        "external_mutation_count": 0,
    }


final_nopublish_receipt = record_nopublish_receipt


def _forbidden_payload_keys(payload: Mapping[str, Any]) -> list[str]:
    forbidden = {
        "collection_nodes",
        "partitions",
        "pytest_args",
        "selectors",
        "node_ids",
        "runner",
        "ledger_path",
        "collection",
        "-k",
        "--ignore",
        "--deselect",
        "--maxfail",
    }
    return sorted(key for key in payload if str(key) in forbidden)


def _forbidden_run_payload_keys(payload: Mapping[str, Any]) -> list[str]:
    return _forbidden_payload_keys(payload)


def _forbidden_repair_payload_keys(payload: Mapping[str, Any]) -> list[str]:
    return sorted(
        key
        for key in _forbidden_payload_keys(payload)
        if str(key) not in {"node_ids"}
    )


def _emit(result: Mapping[str, Any]) -> None:
    # No BOM and exactly one machine-readable JSON line. The final newline is the
    # record delimiter, not an additional JSON value.
    sys.stdout.write(json.dumps(dict(result), ensure_ascii=False, separators=(",", ":")) + "\n")


def _load_payload(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise NewsGraspReleaseGateError("release_payload_not_object")
    return value


def _main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if args and args[0] == "collect-only":
            # No caller args after repo root: authoritative collection is always full.
            if len(args) != 2:
                raise NewsGraspReleaseGateError("release_collect_only_selector_forbidden")
            result = collect_only_nodes(args[1])
        elif args and args[0] == "run":
            if len(args) != 3:
                raise NewsGraspReleaseGateError("release_run_requires_payload_and_repo_root")
            payload = _load_payload(args[1])
            forbidden = _forbidden_run_payload_keys(payload)
            if forbidden:
                raise NewsGraspReleaseGateError(f"release_payload_selector_or_fake_collection_forbidden:{forbidden}")
            repo_root = args[2]
            rid = _safe_id(payload.get("release_id") or os.environ.get(RELEASE_ID_ENV), field="release_id")
            timeout = payload.get("timeout_seconds")
            collected = collect_only_nodes(
                repo_root,
                timeout_seconds=timeout,
                release_id=rid,
            )
            if collected.get("ok") is not True:
                result = collected
            else:
                result = execute_partitioned_nodes(
                    collected["collection_nodes"],
                    repo_root=repo_root,
                    collection_receipt=collected,
                    release_id=rid,
                    timeout_seconds=timeout,
                )
                result["collection"] = collected
        elif args and args[0] == "repair":
            if len(args) != 6:
                raise NewsGraspReleaseGateError("release_repair_requires_payload_repo_partition_cause_repair_id")
            payload = _load_payload(args[1])
            forbidden = _forbidden_repair_payload_keys(payload)
            if forbidden:
                raise NewsGraspReleaseGateError(f"release_payload_selector_forbidden:{forbidden}")
            previous = payload.get("previous_receipt")
            if not isinstance(previous, Mapping):
                raise NewsGraspReleaseGateError("release_repair_previous_receipt_missing")
            result = causal_repair_partition(
                repo_root=args[2],
                partition=args[3],
                node_ids=payload.get("node_ids") or (),
                cause_hash=args[4],
                previous_receipt=previous,
                repair_id=args[5],
                release_id=payload.get("release_id"),
                ledger_path=payload.get("ledger_path"),
                timeout_seconds=payload.get("timeout_seconds"),
            )
        elif args and args[0] == "nopublish":
            if len(args) != 2:
                raise NewsGraspReleaseGateError("release_nopublish_requires_payload")
            payload = _load_payload(args[1])
            result = record_nopublish_receipt(
                release_id=str(payload.get("release_id") or ""),
                receipt=payload.get("receipt") if isinstance(payload.get("receipt"), Mapping) else payload,
                ledger_path=payload.get("ledger_path"),
            )
        else:
            # Legacy one-argument validator / arbitrary collection input is deliberately
            # unreachable from Release production. It could bypass authoritative collect.
            raise NewsGraspReleaseGateError("release_authoritative_cli_required")
    except (OSError, ValueError, TypeError, NewsGraspReleaseGateError) as exc:
        result = {
            "schemaVersion": RELEASE_GATE_SCHEMA,
            "ok": False,
            "status": "blocked",
            "failures": [str(exc)],
        }
    _emit(result)
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(_main())
