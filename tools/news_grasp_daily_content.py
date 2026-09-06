"""Daily current-issue の生成を read-only model output から一度だけ物理化する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROTECTED_RELEASE = "2026-09-02"
CONTENT_RECEIPT_SCHEMA = "NEWS_GRASP_DAILY_CONTENT_RECEIPT_V1"
MODEL_BUNDLE_SCHEMA = "NEWS_GRASP_DAILY_MODEL_BUNDLE_V1"
MODEL_ARTIFACT_SCHEMA = "NEWS_GRASP_DAILY_MODEL_ARTIFACT_V1"
MODEL_FAILURE_SCHEMA = "NEWS_GRASP_DAILY_MODEL_FAILURE_V1"
MODEL_CALL_INTENT_SCHEMA = "NEWS_GRASP_MODEL_CALL_INTENT_V1"
MODEL_CALL_RAW_FILENAME = "raw.json"
REPORTER_SCHEMA = "schemas/news_grasp_daily_reporter_output.schema.json"
REPORTER_SHARD_SCHEMA = "schemas/news_grasp_daily_reporter_shard_output.schema.json"
EDITOR_SCHEMA = "schemas/news_grasp_daily_editor_output.schema.json"
DEEPDIVE_SCHEMA = "schemas/news_grasp_daily_deepdive_output.schema.json"
DAILY_OUTPUT_SCHEMAS = (
    REPORTER_SCHEMA,
    REPORTER_SHARD_SCHEMA,
    EDITOR_SCHEMA,
    DEEPDIVE_SCHEMA,
)
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,191}")
_CARD_RE = re.compile(r"(?m)^###\s+\[")
_ARTICLES_JSONL_LIMIT = 64 * 1024 * 1024
_MODEL_EVENTS_MAX_BYTES = 16 * 1024 * 1024
_GENRES = {
    "fx": "FX",
    "ai": "AI",
    "it": "IT-Consulting",
    "mobility": "Mobility",
    "manufacturing": "Manufacturing",
    "economy": "Economy",
    "game": "Game",
}


class DailyContentError(RuntimeError):
    """生成入力、model output、materializationのtyped failure。"""


class ModelResultPending(DailyContentError):
    """送信状態または結果回収が未確定で、同じ予約を保持する運用状態。"""

    def __init__(self, detail: str = "") -> None:
        suffix = f":{detail}" if detail else ""
        super().__init__(f"MODEL_RESULT_PENDING{suffix}")


def _schema_pointer_part(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _validate_closed_schema_node(node: Any, *, pointer: str) -> None:
    if isinstance(node, Mapping):
        if node.get("type") == "object":
            properties = node.get("properties")
            required = node.get("required")
            if not isinstance(properties, Mapping):
                raise DailyContentError(
                    f"DAILY_OUTPUT_SCHEMA_INVALID:{pointer}:properties"
                )
            if node.get("additionalProperties") is not False:
                raise DailyContentError(
                    f"DAILY_OUTPUT_SCHEMA_INVALID:{pointer}:additionalProperties"
                )
            if not isinstance(required, list) or any(
                not isinstance(item, str) for item in required
            ):
                raise DailyContentError(
                    f"DAILY_OUTPUT_SCHEMA_INVALID:{pointer}:required"
                )
            if len(required) != len(set(required)) or set(required) != set(properties):
                raise DailyContentError(
                    f"DAILY_OUTPUT_SCHEMA_INVALID:{pointer}:required_properties"
                )
        for name, child in node.items():
            _validate_closed_schema_node(
                child,
                pointer=f"{pointer}/{_schema_pointer_part(name)}",
            )
        return
    if isinstance(node, list):
        for index, child in enumerate(node):
            _validate_closed_schema_node(child, pointer=f"{pointer}/{index}")


def validate_daily_output_schemas(repo_root: Path | str) -> None:
    """日次モデル出力schemaの全objectを静的に閉鎖検査する。"""

    root = _safe_root(repo_root)
    for relative in DAILY_OUTPUT_SCHEMAS:
        path = _safe_path(root, relative)
        try:
            raw = path.read_bytes()
            schema = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DailyContentError(
                f"DAILY_OUTPUT_SCHEMA_INVALID:{relative}:json"
            ) from exc
        if not isinstance(schema, Mapping):
            raise DailyContentError(f"DAILY_OUTPUT_SCHEMA_INVALID:{relative}:root")
        _validate_closed_schema_node(schema, pointer=f"{relative}#")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    candidate = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def _safe_root(repo_root: Path | str) -> Path:
    try:
        root = Path(os.path.abspath(os.fspath(repo_root))).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise DailyContentError("REPO_ROOT_INVALID") from exc
    if not root.is_dir() or root.is_symlink():
        raise DailyContentError("REPO_ROOT_INVALID")
    return root


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise DailyContentError("CONTENT_PATH_INVALID")
    absolute = (root / candidate).resolve(strict=False)
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise DailyContentError("CONTENT_PATH_INVALID") from exc
    cursor = root
    for part in candidate.parts[:-1]:
        cursor /= part
        if cursor.exists() and (cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)()):
            raise DailyContentError("CONTENT_PATH_INVALID")
    return absolute


def _trusted_git_executable() -> Path:
    candidates = (
        (Path(r"C:\Program Files\Git\cmd\git.exe"),)
        if os.name == "nt"
        else (Path("/usr/bin/git"), Path("/usr/local/bin/git"))
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise DailyContentError("TRUSTED_GIT_MISSING")


def _run_local_git(root: Path, *args: str, text: bool = False) -> subprocess.CompletedProcess[Any]:
    env = {
        key: value
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
        if (value := os.environ.get(key))
    }
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
        }
    )
    return subprocess.run(
        [str(_trusted_git_executable()), *args],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="ascii" if text else None,
        errors="replace" if text else None,
        timeout=30,
        check=False,
        shell=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        env=env,
    )


def _read_articles_jsonl_baseline(root: Path, runtime_ledger: Any) -> bytes:
    from tools import news_grasp_direct_runtime as runtime

    with runtime_ledger.store.connect() as conn:
        row = runtime_ledger.store._run_row(conn, runtime_ledger.run_id)
        seal = json.loads(str(row["start_seal_json"] or "{}"))
        failures = runtime._start_seal_integrity_failures(
            seal,
            store=runtime_ledger.store,
            row=row,
            allow_legacy_fields=runtime_ledger.store.test_only_allow_semantic_verifier,
        )
    if failures:
        raise DailyContentError("ARTICLES_BASELINE_START_SEAL_INVALID:" + ",".join(failures))
    source_baseline = str(seal.get("sourceBaseline") or "").casefold()
    if re.fullmatch(r"[0-9a-f]{40}", source_baseline) is None:
        if runtime_ledger.store.test_only_allow_semantic_verifier:
            test_path = _safe_path(root, "data/articles.jsonl")
            if test_path.is_symlink():
                raise DailyContentError("ARTICLES_BASELINE_TEST_PATH_INVALID")
            return test_path.read_bytes() if test_path.is_file() else b""
        raise DailyContentError("ARTICLES_BASELINE_SHA_INVALID")
    identity = _run_local_git(
        root,
        "rev-parse",
        f"{source_baseline}:data/articles.jsonl",
        text=True,
    )
    blob_id = str(identity.stdout or "").strip()
    if identity.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", blob_id) is None:
        raise DailyContentError("ARTICLES_BASELINE_BLOB_MISSING")
    size = _run_local_git(root, "cat-file", "-s", blob_id, text=True)
    try:
        byte_count = int(str(size.stdout or "").strip())
    except ValueError as exc:
        raise DailyContentError("ARTICLES_BASELINE_SIZE_INVALID") from exc
    if size.returncode != 0 or not 0 <= byte_count <= _ARTICLES_JSONL_LIMIT:
        raise DailyContentError("ARTICLES_BASELINE_SIZE_INVALID")
    blob = _run_local_git(root, "cat-file", "blob", blob_id)
    if blob.returncode != 0 or len(blob.stdout) != byte_count:
        raise DailyContentError("ARTICLES_BASELINE_READ_FAILED")
    return bytes(blob.stdout)


def _historical_article_lines(baseline: bytes, *, issue_date: str) -> list[bytes]:
    value = baseline[3:] if baseline.startswith(b"\xef\xbb\xbf") else baseline
    history: list[bytes] = []
    for raw_line in value.splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DailyContentError("ARTICLES_BASELINE_JSON_INVALID") from exc
        if not isinstance(row, Mapping) or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", str(row.get("date") or "")
        ) is None:
            raise DailyContentError("ARTICLES_BASELINE_ROW_INVALID")
        if str(row["date"]) != issue_date:
            history.append(raw_line + b"\n")
    return history


def _load_completion(root: Path, run_id: str, issue_date: str) -> dict[str, Any] | None:
    path = _safe_path(root, f"build/daily-content/{run_id}/completion.json")
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError("CONTENT_RECEIPT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != CONTENT_RECEIPT_SCHEMA
        or value.get("issue_date") != issue_date
        or value.get("run_id") != run_id
        or value.get("ok") is not True
    ):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    hashes = value.get("artifact_hashes")
    derived_hashes = value.get("derived_artifact_hashes", {})
    if not isinstance(hashes, dict) or not hashes or not isinstance(derived_hashes, dict):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    for relative, expected in {**hashes, **derived_hashes}.items():
        target = _safe_path(root, str(relative))
        if not target.is_file() or _sha256_bytes(target.read_bytes()) != expected:
            raise DailyContentError("CONTENT_RECEIPT_ARTIFACT_DRIFT")
    reused = dict(value)
    reused["status"] = "reused"
    reused["model_call_count"] = 0
    reused["reporter_call_count"] = 0
    return reused


def _validate_completion_payload(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        value.get("schemaVersion") != CONTENT_RECEIPT_SCHEMA
        or value.get("issue_date") != issue_date
        or value.get("run_id") != run_id
        or value.get("ok") is not True
    ):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    hashes = value.get("artifact_hashes")
    derived_hashes = value.get("derived_artifact_hashes", {})
    if not isinstance(hashes, Mapping) or not hashes or not isinstance(derived_hashes, Mapping):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    for relative, expected in {**dict(hashes), **dict(derived_hashes)}.items():
        target = _safe_path(root, str(relative))
        if not target.is_file() or _sha256_bytes(target.read_bytes()) != expected:
            raise DailyContentError("CONTENT_RECEIPT_ARTIFACT_DRIFT")
    reused = dict(value)
    reused["status"] = "reused"
    reused["model_call_count"] = 0
    reused["reporter_call_count"] = 0
    return reused


def _model_bundle_path(root: Path, run_id: str) -> Path:
    return _safe_path(root, f"build/daily-content/{run_id}/model-bundle.json")


def _artifact_cache_path(root: Path, run_id: str, artifact_id: str) -> Path:
    digest = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
    return _safe_path(root, f"build/daily-content/{run_id}/artifacts/{digest}.json")


def _failure_cache_path(root: Path, run_id: str, artifact_id: str) -> Path:
    digest = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
    return _safe_path(root, f"build/daily-content/{run_id}/failures/{digest}.json")


def _artifact_input_hash(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_json_bytes(dict(value)))


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _model_call_root(root: Path, run_id: str, call_id: str) -> Path:
    path = _safe_path(root, f"build/daily-content/{run_id}/model-calls/{call_id}")
    if path.exists() and (_is_reparse_point(path) or not path.is_dir()):
        raise DailyContentError("MODEL_CALL_ROOT_INVALID")
    path.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(path):
        raise DailyContentError("MODEL_CALL_ROOT_INVALID")
    return path


def _model_call_intent(
    *,
    root: Path,
    run_id: str,
    issue_date: str,
    role: str,
    category: str | None,
    call_id: str,
    input_hash: str,
) -> dict[str, Any]:
    return {
        "schemaVersion": MODEL_CALL_INTENT_SCHEMA,
        "runId": run_id,
        "issueDate": issue_date,
        "role": role,
        "category": category,
        "callId": call_id,
        "inputHash": input_hash,
        "expectedResultFilename": MODEL_CALL_RAW_FILENAME,
    }


def _read_json_file(path: Path, *, error_code: str) -> Any:
    if not path.is_file() or _is_reparse_point(path):
        raise DailyContentError(error_code)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError(error_code) from exc


def _ensure_model_call_intent(path: Path, expected: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        actual = _read_json_file(path, error_code="MODEL_CALL_INTENT_INVALID")
        if not isinstance(actual, Mapping) or dict(actual) != dict(expected):
            raise DailyContentError("MODEL_CALL_INTENT_INVALID")
        return
    payload = _json_bytes(expected)
    descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        actual = _read_json_file(path, error_code="MODEL_CALL_INTENT_INVALID")
        if not isinstance(actual, Mapping) or dict(actual) != dict(expected):
            raise DailyContentError("MODEL_CALL_INTENT_INVALID")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_model_call_raw(path: Path) -> tuple[bool, Any]:
    if not path.exists():
        return False, None
    return True, _read_json_file(path, error_code="MODEL_RAW_OUTPUT_INVALID")


def _has_reparse_ancestor(path: Path) -> bool:
    current = path
    while True:
        try:
            if _is_reparse_point(current):
                return True
        except OSError:
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _read_bounded_model_events(path: Path) -> bytes | None:
    """モデルeventsを正規・非reparseの同一fileから上限付きで読む。"""

    if _has_reparse_ancestor(path) or not path.is_file():
        return None
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or _is_reparse_point(path):
            return None
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return None
            raw = stream.read(_MODEL_EVENTS_MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if not stat.S_ISREG(after.st_mode):
            return None
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_opened = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if (
            identity_before != identity_opened
            or identity_opened != identity_after
            or len(raw) > _MODEL_EVENTS_MAX_BYTES
            or len(raw) != opened.st_size
            or _has_reparse_ancestor(path)
            or _is_reparse_point(path)
        ):
            return None
        return raw
    except (OSError, ValueError):
        return None


def _confirmed_schema_rejection_sha256(events_path: Path) -> str | None:
    """確定したAPI schema拒否を同一read bytesで検証し、そのSHA256を返す。"""

    event_bytes = _read_bounded_model_events(events_path)
    if event_bytes is None:
        return None
    try:
        lines = event_bytes.splitlines()
        events = []
        for line in lines:
            if not line.strip():
                return None
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, Mapping):
                return None
            events.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not events or events[-1].get("type") != "turn.failed":
        return None
    if any(event.get("type") == "turn.completed" for event in events):
        return None
    error = events[-1].get("error")
    if not isinstance(error, Mapping) or not isinstance(error.get("message"), str):
        return None
    try:
        detail = json.loads(str(error["message"]))
    except (TypeError, json.JSONDecodeError):
        return None
    if not (
        isinstance(detail, Mapping)
        and type(detail.get("status")) is int
        and detail.get("status") == 400
        and isinstance(detail.get("error"), Mapping)
        and detail["error"].get("code") == "invalid_json_schema"
    ):
        return None
    return _sha256_bytes(event_bytes)


def _is_confirmed_schema_rejection(events_path: Path) -> bool:
    """保存済みeventsの末尾が確定したAPI schema拒否かを厳密に判定する。"""

    return _confirmed_schema_rejection_sha256(events_path) is not None


def _model_call_label(
    role: str,
    category: str | None,
    context: Mapping[str, Any],
) -> str:
    if role == "reporter":
        return f"reporter-{category}"
    if role == "reporter_shard":
        categories = tuple(str(item) for item in context.get("categories", ()))
        return f"reporter-shard-{'-'.join(categories)}"
    return role


def _model_schema_for_role(role: str) -> str:
    try:
        return {
            "reporter": REPORTER_SCHEMA,
            "reporter_shard": REPORTER_SHARD_SCHEMA,
            "editor": EDITOR_SCHEMA,
            "deepdive": DEEPDIVE_SCHEMA,
        }[role]
    except KeyError as exc:
        raise DailyContentError("MODEL_ROLE_UNKNOWN") from exc


def _verified_model_schema_sha256(
    root: Path,
    role: str,
    *,
    pending_detail: str,
) -> str:
    try:
        validate_daily_output_schemas(root)
        schema_path = _safe_path(root, _model_schema_for_role(role))
        schema_bytes = schema_path.read_bytes()
    except (DailyContentError, OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ModelResultPending(pending_detail) from exc
    return _sha256_bytes(schema_bytes)


def _schema_recovery_metadata_matches(
    path: Path,
    *,
    call_id: str,
    original_events_sha: str,
    schema_sha: str,
) -> bool:
    expected = {
        "schemaVersion": "NEWS_GRASP_MODEL_SCHEMA_RECOVERY_V1",
        "reason": "invalid_json_schema",
        "callId": call_id,
        "originalEventsSha256": original_events_sha,
        "schemaSha256": schema_sha,
    }
    try:
        actual = _read_json_file(path, error_code="MODEL_SCHEMA_RECOVERY_METADATA_INVALID")
    except DailyContentError:
        return False
    return isinstance(actual, Mapping) and dict(actual) == expected


def _schema_recovery_root(call_root: Path) -> Path:
    recovery_root = _safe_path(call_root, "schema-recovery")
    if recovery_root.exists() and (_is_reparse_point(recovery_root) or not recovery_root.is_dir()):
        raise DailyContentError("MODEL_SCHEMA_RECOVERY_ROOT_INVALID")
    recovery_root.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(recovery_root):
        raise DailyContentError("MODEL_SCHEMA_RECOVERY_ROOT_INVALID")
    return recovery_root


@contextmanager
def _persistent_model_output_context(root: Path, run_id: str):
    path = _safe_path(root, f"build/daily-content/{run_id}/model-calls")
    if path.exists() and (_is_reparse_point(path) or not path.is_dir()):
        raise DailyContentError("MODEL_CALL_ROOT_INVALID")
    path.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(path):
        raise DailyContentError("MODEL_CALL_ROOT_INVALID")
    yield path


def _validator_id(artifact_id: str) -> str:
    if artifact_id.startswith("candidate:"):
        return "candidate_available_v1"
    if artifact_id.startswith("reporter:"):
        return "reporter_output_valid_v1"
    if artifact_id == "editor":
        return "editor_output_valid_v1"
    if artifact_id == "deepdive_model":
        return "deepdive_output_valid_v1"
    if artifact_id == "content_completion":
        return "content_completion_artifact_hashes_v1"
    return "deterministic_artifact_hash_v1"


def _load_artifact_checkpoint(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    artifact_id: str,
    input_hash: str,
    runtime_ledger: Any | None = None,
) -> dict[str, Any] | None:
    if runtime_ledger is not None:
        return runtime_ledger.load_checkpoint(
            artifact_id=artifact_id,
            input_hash=input_hash,
            validator_id=_validator_id(artifact_id),
        )
    path = _artifact_cache_path(root, run_id, artifact_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError(f"MODEL_ARTIFACT_INVALID:{artifact_id}") from exc
    if not isinstance(value, dict):
        raise DailyContentError(f"MODEL_ARTIFACT_INVALID:{artifact_id}")
    payload = value.get("payload")
    if (
        value.get("schemaVersion") != MODEL_ARTIFACT_SCHEMA
        or value.get("runId") != run_id
        or value.get("issueDate") != issue_date
        or value.get("artifactId") != artifact_id
        or value.get("inputHash") != input_hash
        or not isinstance(payload, dict)
        or value.get("outputHash") != _sha256_bytes(_json_bytes(payload))
    ):
        raise DailyContentError(f"MODEL_ARTIFACT_INVALID:{artifact_id}")
    return value


def _write_artifact_checkpoint(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    artifact_id: str,
    input_hash: str,
    payload: Mapping[str, Any],
    runtime_ledger: Any | None = None,
) -> dict[str, Any]:
    if runtime_ledger is not None:
        return runtime_ledger.write_checkpoint(
            artifact_id=artifact_id,
            input_hash=input_hash,
            validator_id=_validator_id(artifact_id),
            payload=payload,
        )
    value = {
        "schemaVersion": MODEL_ARTIFACT_SCHEMA,
        "runId": run_id,
        "issueDate": issue_date,
        "artifactId": artifact_id,
        "inputHash": input_hash,
        "outputHash": _sha256_bytes(_json_bytes(dict(payload))),
        "status": "Green",
        "payload": dict(payload),
    }
    _atomic_write_bytes(
        _artifact_cache_path(root, run_id, artifact_id),
        _json_bytes(value),
    )
    return value


def _load_failure_checkpoint(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    artifact_id: str,
    runtime_ledger: Any | None = None,
) -> dict[str, Any] | None:
    if runtime_ledger is not None:
        return runtime_ledger.load_failure(artifact_id)
    path = _failure_cache_path(root, run_id, artifact_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError(f"MODEL_FAILURE_INVALID:{artifact_id}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != MODEL_FAILURE_SCHEMA
        or value.get("runId") != run_id
        or value.get("issueDate") != issue_date
        or value.get("artifactId") != artifact_id
        or not value.get("failureSignature")
    ):
        raise DailyContentError(f"MODEL_FAILURE_INVALID:{artifact_id}")
    return value


def _write_failure_checkpoint(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    stage: str,
    artifact_id: str,
    predicate_id: str,
    reason_code: str,
    input_hash: str,
    cause_input_mask: Sequence[str] = (),
    invalid_payload: Any = None,
    runtime_ledger: Any | None = None,
) -> dict[str, Any]:
    value = _failure_checkpoint_value(
        run_id=run_id,
        issue_date=issue_date,
        stage=stage,
        artifact_id=artifact_id,
        predicate_id=predicate_id,
        reason_code=reason_code,
        input_hash=input_hash,
        cause_input_mask=cause_input_mask,
        invalid_payload=invalid_payload,
    )
    if runtime_ledger is not None:
        return runtime_ledger.record_failure(value)
    _atomic_write_bytes(_failure_cache_path(root, run_id, artifact_id), _json_bytes(value))
    return value


def _failure_checkpoint_value(
    *,
    run_id: str,
    issue_date: str,
    stage: str,
    artifact_id: str,
    predicate_id: str,
    reason_code: str,
    input_hash: str,
    cause_input_mask: Sequence[str] = (),
    invalid_payload: Any = None,
) -> dict[str, Any]:
    from tools.news_grasp_repair_registry import failure_signature

    normalized_reason = str(reason_code).replace("|", "/")
    failure = {
        "stage": stage,
        "artifactId": artifact_id,
        "predicateId": predicate_id,
        "reasonCode": normalized_reason,
        "inputHash": input_hash,
        "causeInputMask": [str(item) for item in cause_input_mask],
    }
    if invalid_payload is not None:
        failure["invalidPayload"] = invalid_payload
        allowed_paths = _repair_allowed_paths(
            artifact_id=artifact_id,
            reason_code=normalized_reason,
            invalid_payload=invalid_payload,
        )
        if allowed_paths:
            failure["allowedMutationPaths"] = allowed_paths
            failure["causeInputMask"] = allowed_paths
    value = {
        "schemaVersion": MODEL_FAILURE_SCHEMA,
        "runId": run_id,
        "issueDate": issue_date,
        **failure,
        "failureSignature": failure_signature(failure),
    }
    return value


def _repair_allowed_paths(
    *,
    artifact_id: str,
    reason_code: str,
    invalid_payload: Any,
) -> list[str]:
    if not isinstance(invalid_payload, Mapping):
        return []
    if artifact_id.startswith("reporter:"):
        category = artifact_id.split(":", 1)[1]
        identity = re.search(r":identity:([a-z_,]+)$", reason_code)
        if identity:
            required = {
                "category",
                "issue_date",
                "records",
                "digest_markdown",
                "search_audit",
            }
            invalid_fields = set(identity.group(1).split(","))
            invalid_fields.update(required - set(invalid_payload))
            invalid_fields.update(set(invalid_payload) - required)
            return [f"/{field}" for field in sorted(invalid_fields)]
        if ":card_count" in reason_code:
            return ["/digest_markdown"]
        semantic = re.search(r":semantic:(\d+):([^:]+)$", reason_code)
        if semantic:
            index = semantic.group(1)
            fields = {
                "date": "date",
                "published_date": "published_date",
                "date_evidence_source_missing": "date_evidence_source",
                "date_evidence_source_rss": "date_evidence_source",
                "google_news_url": "url",
                "landing_url": "url",
                "thumb_missing": "thumb",
                "google_thumb": "thumb",
                "self_thumb": "thumb",
            }
            return list(
                dict.fromkeys(
                    f"/records/{index}/{fields[field]}"
                    for field in semantic.group(2).split(",")
                    if field in fields
                )
            )
        candidate = re.search(r":candidate_provenance:(\d+)$", reason_code)
        if candidate:
            return [f"/records/{candidate.group(1)}/url"]
        schema = re.search(r":schema:(\d+):([a-z_]+)$", reason_code)
        if schema:
            return [f"/records/{schema.group(1)}/{schema.group(2)}"]
        record = re.search(r":record:(\d+)$", reason_code)
        if record:
            return [f"/records/{record.group(1)}"]
        audit = re.search(r":audit:([a-z_,]+)$", reason_code)
        if audit:
            return [f"/search_audit/{field}" for field in audit.group(1).split(",")]
        shape = re.search(r":shape:([a-z_,]+)$", reason_code)
        if shape:
            return [f"/{field}" for field in shape.group(1).split(",")]
        if invalid_payload.get("category") != category:
            return ["/category"]
    if artifact_id == "editor":
        if ":identity" in reason_code:
            required = {"issue_date", "inputs", "append_records", "summary_markdown"}
            invalid_fields = {"issue_date"}
            invalid_fields.update(required - set(invalid_payload))
            invalid_fields.update(set(invalid_payload) - required)
            return [f"/{field}" for field in sorted(invalid_fields)]
        if ":reporter_binding" in reason_code:
            return ["/append_records"]
        shape = re.search(r":shape:([a-z_,]+)$", reason_code)
        if shape:
            return [f"/{field}" for field in shape.group(1).split(",")]
        paths: list[str] = []
        if any(marker in reason_code for marker in ("editor summary", "summary ", "reflection", "lane line")):
            paths.append("/summary_markdown")
        for raw_index, message in re.findall(r"append_records\[(\d+)\] ([^|]+)", reason_code):
            index = max(0, int(raw_index) - 1)
            if "date mismatch" in message:
                paths.append(f"/append_records/{index}/date")
            elif "reserved .invalid URL" in message:
                paths.append(f"/append_records/{index}/url")
            elif "abort/block marker" in message:
                paths.extend(
                    f"/append_records/{index}/{field}"
                    for field in ("title", "title_ja", "summary")
                )
            elif "not an object" in message:
                paths.append(f"/append_records/{index}")
        return list(dict.fromkeys(paths))
    if artifact_id == "deepdive_model":
        if ":dialogue" in reason_code:
            return ["/dialogue_markdown"]
        if any(marker in reason_code for marker in (":date", ":sections", ":url_provenance")):
            return ["/article_markdown"]
    return []


def _assert_repair_scope(failure: Mapping[str, Any] | None, repaired: Any) -> None:
    if not isinstance(failure, Mapping):
        return
    previous = failure.get("invalidPayload")
    paths = failure.get("allowedMutationPaths")
    if not isinstance(previous, Mapping):
        return
    if (
        not isinstance(repaired, Mapping)
        or not isinstance(paths, list)
        or not paths
        or len(set(paths)) != len(paths)
    ):
        raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
    before = json.loads(json.dumps(dict(previous), ensure_ascii=False))
    after = json.loads(json.dumps(dict(repaired), ensure_ascii=False))
    sentinel = {"__news_grasp_allowed_mutation__": True}
    def mask(document: Any, raw_path: Any) -> None:
        if not isinstance(raw_path, str) or not raw_path.startswith("/") or raw_path.endswith("/"):
            raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
        encoded = raw_path.split("/")[1:]
        if not encoded or any(part == "" for part in encoded):
            raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
        parts = [part.replace("~1", "/").replace("~0", "~") for part in encoded]
        cursor = document
        for part in parts[:-1]:
            if isinstance(cursor, list):
                if not part.isdigit() or int(part) >= len(cursor):
                    raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
                cursor = cursor[int(part)]
            elif isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
        leaf = parts[-1]
        if isinstance(cursor, list):
            if not leaf.isdigit() or int(leaf) >= len(cursor):
                raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")
            cursor[int(leaf)] = sentinel
        elif isinstance(cursor, dict):
            cursor[leaf] = sentinel
        else:
            raise DailyContentError("REPAIR_MUTATION_SCOPE_UNRESOLVED")

    for raw_path in paths:
        for document in (before, after):
            mask(document, raw_path)
    if before != after:
        raise DailyContentError("REPAIR_UNSCOPED_MUTATION")


def _project_repair_result(failure: Mapping[str, Any] | None, proposed: Any) -> Any:
    """元成果へ許可済み箇所だけを適用し、提案の範囲外変更を正本へ持ち込まない。"""
    if failure is None:
        return proposed
    previous = failure.get("invalidPayload")
    paths = failure.get("allowedMutationPaths")
    if not isinstance(previous, Mapping) or not isinstance(proposed, Mapping) or not isinstance(paths, list) or not paths:
        raise ModelResultPending("repair_scope_unresolved")
    result = json.loads(json.dumps(dict(previous), ensure_ascii=False))
    candidate = json.loads(json.dumps(dict(proposed), ensure_ascii=False))
    try:
        for path in paths:
            if not isinstance(path, str) or not path.startswith("/") or path.endswith("/"):
                raise ValueError("pointer")
            parts = [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]
            if any(not part for part in parts):
                raise ValueError("pointer")
            target, source = result, candidate
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
                source = source[int(part)] if isinstance(source, list) else source[part]
            leaf = parts[-1]
            if isinstance(target, list):
                target[int(leaf)] = source[int(leaf)]
            elif leaf in source:
                target[leaf] = source[leaf]
            else:
                target.pop(leaf, None)
        _assert_repair_scope(failure, result)
    except (KeyError, IndexError, TypeError, ValueError, DailyContentError) as exc:
        raise ModelResultPending("repair_scope_unresolved") from exc
    return result


def _reporter_shards(categories: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    shard_count = min(3, len(categories))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    for index, category in enumerate(categories):
        shards[index % shard_count].append(str(category))
    return tuple(tuple(shard) for shard in shards if shard)


def _load_model_bundle(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    categories: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]] | None:
    path = _model_bundle_path(root, run_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError("MODEL_BUNDLE_INVALID") from exc
    if not isinstance(value, dict):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    payload = value.get("payload")
    expected_hash = str(value.get("payload_hash") or "")
    if (
        value.get("schemaVersion") != MODEL_BUNDLE_SCHEMA
        or not isinstance(payload, dict)
        or _sha256_bytes(_json_bytes(payload)) != expected_hash
        or payload.get("run_id") != run_id
        or payload.get("issue_date") != issue_date
        or payload.get("scheduled_categories") != list(categories)
    ):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    reporters = payload.get("reporters")
    editor = payload.get("editor")
    deepdive = payload.get("deepdive")
    if (
        not isinstance(reporters, list)
        or len(reporters) != len(categories)
        or [str(item.get("category") or "") for item in reporters if isinstance(item, Mapping)] != list(categories)
        or not all(isinstance(item, dict) and item.get("issue_date") == issue_date for item in reporters)
        or not isinstance(editor, dict)
        or editor.get("issue_date") != issue_date
        or not isinstance(deepdive, dict)
        or not isinstance(deepdive.get("article_markdown"), str)
        or not isinstance(deepdive.get("dialogue_markdown"), str)
    ):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    return [dict(item) for item in reporters], dict(editor), {
        "article_markdown": str(deepdive["article_markdown"]),
        "dialogue_markdown": str(deepdive["dialogue_markdown"]),
    }


def _write_model_bundle(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    categories: Sequence[str],
    reporters: Sequence[Mapping[str, Any]],
    editor: Mapping[str, Any],
    deepdive: Mapping[str, str],
) -> None:
    payload = {
        "run_id": run_id,
        "issue_date": issue_date,
        "scheduled_categories": list(categories),
        "reporters": [dict(item) for item in reporters],
        "editor": dict(editor),
        "deepdive": dict(deepdive),
    }
    envelope = {
        "schemaVersion": MODEL_BUNDLE_SCHEMA,
        "payload_hash": _sha256_bytes(_json_bytes(payload)),
        "payload": payload,
    }
    _atomic_write_bytes(_model_bundle_path(root, run_id), _json_bytes(envelope))


def _default_candidate_provider(category: str, issue_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from tools.harvest_candidates import harvest_category_with_audit
    from tools.prepare_reporter_candidates import prepare_rows

    candidates, audit = harvest_category_with_audit(category, max_per_category=25, timeout=12.0)
    prepared, dropped = prepare_rows(
        candidates,
        max_rows=25,
        thumb_limit=5,
        decode_timeout=3.0,
        thumb_timeout=5.0,
        thumb_retries=0,
    )
    audit = dict(audit)
    audit.update(
        {
            "date": issue_date,
            "category_id": category,
            "candidates_total": len(prepared),
            "selected_total": 0,
            "dropped_after_prepare": len(dropped),
        }
    )
    if not prepared:
        raise DailyContentError(f"CANDIDATES_EMPTY:{category}")
    return prepared, audit


def _validate_candidate_payload(
    *,
    category: str,
    issue_date: str,
    candidates: Any,
    audit: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if (
        not isinstance(candidates, list)
        or not candidates
        or not all(
            isinstance(item, Mapping)
            and isinstance(item.get("title"), str)
            and bool(str(item.get("title") or "").strip())
            and re.fullmatch(r"https?://[^\s]+", str(item.get("url") or "")) is not None
            for item in candidates
        )
        or not isinstance(audit, Mapping)
        or audit.get("date") != issue_date
        or audit.get("category_id") != category
        or not isinstance(audit.get("queries"), list)
        or any(
            not isinstance(audit.get(key), int) or int(audit[key]) < 0
            for key in ("raw_results_total", "candidates_total", "selected_total")
        )
    ):
        raise DailyContentError(f"CANDIDATE_OUTPUT_INVALID:{category}")
    return [dict(item) for item in candidates], dict(audit)


def _resolve_codex_executable() -> Path:
    candidates: list[Path] = []
    local = os.environ.get("USERPROFILE", "").strip()
    if local:
        candidates.extend(
            Path(local).glob(".vscode/extensions/openai.chatgpt-*/bin/windows-x86_64/codex.exe")
        )
    unique: dict[str, Path] = {}
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            unique[_sha256_bytes(resolved.read_bytes())] = resolved
    if len(unique) != 1:
        raise DailyContentError("CODEX_EXECUTABLE_IDENTITY_AMBIGUOUS")
    return next(iter(unique.values()))


def _model_prompt(
    *,
    root: Path,
    role: str,
    issue_date: str,
    category: str | None,
    context: Mapping[str, Any],
) -> str:
    if role in {"reporter", "reporter_shard"}:
        source = (root / "prompts" / "newsroom-reporter-system.md").read_text(encoding="utf-8-sig")
        shard_rule = (
            "reporters配列へ入力itemsと同じcategoryを同じ順序で一件ずつ返す。"
            if role == "reporter_shard"
            else ""
        )
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。候補を再収集せず、指定JSON schemaだけを返す。"
            f"{shard_rule}"
            "recordsのpublished_dateはissue_dateと完全一致させ、RSS/pubDateの時刻を公開日証拠に使わない。"
            "前日以前の候補は採用せず、date_evidence_sourceはRSS由来以外の根拠だけを記載する。"
            "recordのurlは入力candidatesにあるURL文字列を完全コピーし、未収集URLや別URLへ置換しない。"
            "digest_markdownの各記事カード見出しは必ず`### [1]`、`### [2]`の形式でrecordsと同数だけ置き、余分なカード見出しを置かない。"
            f"\nissue_date={issue_date}\ncategory={category}\n入力:\n"
            f"{json.dumps(context, ensure_ascii=False)}"
        )
    if role == "editor":
        source = (root / "prompts" / "newsroom-editor-system.md").read_text(encoding="utf-8-sig")
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。reporter recordsを再収集・改変せず、"
            "重複URLだけを一件に畳み、公開用Summaryとappend_recordsを指定JSON schemaだけで返す。"
            f"\nissue_date={issue_date}\n入力:\n{json.dumps(context, ensure_ascii=False)}"
        )
    if role == "deepdive":
        source = (root / "prompts" / "deepdive-research-system.md").read_text(encoding="utf-8-sig")
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。入力recordのURL以外を捏造せず、"
            "当日DeepDive Markdown全文と記事固有の対談Markdown全文を指定JSON schemaだけで返す。"
            f"\nissue_date={issue_date}\n入力:\n{json.dumps(context, ensure_ascii=False)}"
        )
    raise DailyContentError("MODEL_ROLE_UNKNOWN")


def _default_model_runner(
    *,
    role: str,
    repo_root: Path,
    issue_date: str,
    run_id: str,
    category: str | None = None,
    output_dir: Path,
    **context: Any,
) -> dict[str, Any]:
    from tools.model_spawn_client import run_model_process

    model = "gpt-5.6-sol" if role == "deepdive" else "gpt-5.6-luna"
    effort = "max"
    schema = _model_schema_for_role(role)
    shard_categories = tuple(str(item) for item in context.get("categories", ()))
    label = _model_call_label(role, category, context)
    raw_path_value = context.get("raw_path")
    output = Path(raw_path_value) if raw_path_value else output_dir / f"{label}.json"
    if output.parent != output_dir:
        try:
            output.relative_to(output_dir)
        except ValueError as exc:
            raise DailyContentError("MODEL_OUTPUT_PATH_INVALID") from exc
    prompt_context = {
        key: value
        for key, value in context.items()
        if key not in {"call_id", "input_hash", "intent_path", "raw_path"}
    }
    prompt = _model_prompt(
        root=repo_root,
        role=role,
        issue_date=issue_date,
        category=category,
        context=prompt_context,
    )
    prompt_path = output_dir / f"{label}.prompt.txt"
    try:
        _atomic_write_bytes(prompt_path, prompt.encode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ModelResultPending(f"{role}:prompt_persist") from exc
    events_path = output_dir / f"{label}.events.jsonl"
    stderr_path = output_dir / f"{label}.stderr.log"
    try:
        executable = str(_resolve_codex_executable())
    except Exception as exc:  # noqa: BLE001 - unavailable launcher is operationally pending.
        raise ModelResultPending(f"{role}:executable") from exc
    command = [
        executable,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-c",
        "project_doc_max_bytes=0",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-C",
        str(repo_root),
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--output-schema",
        str(_safe_path(repo_root, schema)),
        "-o",
        str(output),
        "-",
    ]
    _verified_model_schema_sha256(
        repo_root,
        role,
        pending_detail=f"{role}:schema",
    )
    try:
        route = (
            f"reporter:{category}"
            if role == "reporter" and category
            else f"reporter-shard:{_sha256_bytes('|'.join(shard_categories).encode('utf-8'))[:12]}"
            if role == "reporter_shard" and shard_categories
            else "newsroom_editor"
            if role == "editor"
            else "deepdive"
        )
        with (
            events_path.open("ab", buffering=0) as events_sink,
            stderr_path.open("ab", buffering=0) as stderr_sink,
        ):
            completed = run_model_process(
                command,
                route=route,
                cwd=repo_root,
                stdin_path=prompt_path,
                stdout_sink=events_sink,
                stderr_sink=stderr_sink,
                timeout=None,
                max_output_bytes=16 * 1024 * 1024,
            )
    except Exception as exc:  # noqa: BLE001 - broker failure is typed and has no canonical mutation.
        raise ModelResultPending(f"{role}:{type(exc).__name__}") from exc
    if not output.is_file():
        raise ModelResultPending(f"{role}:no_result")
    try:
        value = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError(f"MODEL_OUTPUT_JSON_INVALID:{role}") from exc
    if not isinstance(value, dict):
        raise DailyContentError(f"MODEL_OUTPUT_JSON_INVALID:{role}")
    return value


def _validate_reporter(value: Any, *, category: str, issue_date: str, search_audit: Mapping[str, Any]) -> dict[str, Any]:
    from tools.validate_record import RecordSchemaError, validate_record
    from tools.url_quality import is_google_news_rss_url, is_google_news_proxy_thumb, is_news_grasp_self_thumb, looks_homepage_or_section_landing

    if not isinstance(value, Mapping):
        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:payload_type")
    identity_fields = [
        field
        for field, valid in (
            ("category", value.get("category") == category),
            ("issue_date", value.get("issue_date") == issue_date),
        )
        if not valid
    ]
    if identity_fields:
        raise DailyContentError(
            f"REPORTER_OUTPUT_INVALID:{category}:identity:{','.join(identity_fields)}"
        )
    records = value.get("records")
    digest = value.get("digest_markdown")
    audit = value.get("search_audit")
    shape_fields = [
        field
        for field, valid in (
            ("records", isinstance(records, list) and 1 <= len(records) <= 5),
            ("digest_markdown", isinstance(digest, str)),
            ("search_audit", isinstance(audit, Mapping)),
        )
        if not valid
    ]
    if shape_fields:
        raise DailyContentError(
            f"REPORTER_OUTPUT_INVALID:{category}:shape:{','.join(shape_fields)}"
        )
    if _CARD_RE.findall(digest).__len__() != len(records):
        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:card_count")
    candidate_urls = {str(item.get("url") or "").rstrip("/") for item in (search_audit.get("candidates") or []) if isinstance(item, Mapping)}
    normalized_records: list[dict[str, Any]] = []
    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:record:{record_index}")
        try:
            validate_record(record)
        except RecordSchemaError as exc:
            message = str(exc)
            field = next(
                (
                    candidate
                    for candidate in ("date", "title_ja", "title", "url", "thumb", "genre")
                    if candidate in message
                ),
                "",
            )
            if not field:
                raise DailyContentError(
                    f"REPORTER_OUTPUT_INVALID:{category}:schema_unresolved:{record_index}"
                ) from exc
            raise DailyContentError(
                f"REPORTER_OUTPUT_INVALID:{category}:schema:{record_index}:{field}"
            ) from exc
        url = str(record.get("url") or "").rstrip("/")
        thumb = str(record.get("thumb") or "")
        semantic_errors: list[str] = []
        if record.get("date") != issue_date:
            semantic_errors.append("date")
        if str(record.get("published_date") or "") not in {issue_date, str(date.fromisoformat(issue_date))}:
            semantic_errors.append("published_date")
        evidence = str(record.get("date_evidence_source") or "")
        if not evidence.strip():
            semantic_errors.append("date_evidence_source_missing")
        elif "rss" in evidence.casefold():
            semantic_errors.append("date_evidence_source_rss")
        if is_google_news_rss_url(url):
            semantic_errors.append("google_news_url")
        if looks_homepage_or_section_landing(url):
            semantic_errors.append("landing_url")
        if not thumb.startswith(("http://", "https://")):
            semantic_errors.append("thumb_missing")
        elif is_google_news_proxy_thumb(thumb):
            semantic_errors.append("google_thumb")
        elif is_news_grasp_self_thumb(thumb):
            semantic_errors.append("self_thumb")
        if semantic_errors:
            raise DailyContentError(
                f"REPORTER_OUTPUT_INVALID:{category}:semantic:{record_index}:{','.join(semantic_errors)}"
            )
        if candidate_urls and url not in candidate_urls:
            raise DailyContentError(
                f"REPORTER_OUTPUT_INVALID:{category}:candidate_provenance:{record_index}"
            )
        normalized_records.append(dict(record))
    merged_audit = dict(search_audit)
    merged_audit.update(dict(audit))
    merged_audit.update({"date": issue_date, "category_id": category, "selected_total": len(records)})
    missing_audit = [
        key
        for key in ("queries", "raw_results_total", "candidates_total", "selected_total")
        if key not in merged_audit
    ]
    if missing_audit:
        raise DailyContentError(
            f"REPORTER_OUTPUT_INVALID:{category}:audit:{','.join(missing_audit)}"
        )
    return {
        "category": category,
        "issue_date": issue_date,
        "records": normalized_records,
        "digest_markdown": digest.rstrip() + "\n",
        "search_audit": merged_audit,
    }


def _validate_editor(
    value: Any,
    *,
    issue_date: str,
    reporters: Sequence[Mapping[str, Any]],
    preview_dir: Path,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    from tools.validate_editor_output_preview import validate_editor_output_preview

    if not isinstance(value, Mapping) or value.get("issue_date") != issue_date:
        raise DailyContentError("EDITOR_OUTPUT_INVALID:identity")
    records = value.get("append_records")
    summary = value.get("summary_markdown")
    shape_fields = [
        field
        for field, valid in (
            ("append_records", isinstance(records, list) and bool(records)),
            ("summary_markdown", isinstance(summary, str)),
        )
        if not valid
    ]
    if shape_fields:
        raise DailyContentError(f"EDITOR_OUTPUT_INVALID:shape:{','.join(shape_fields)}")
    expected_urls = {str(record.get("url") or "").rstrip("/") for reporter in reporters for record in reporter["records"]}
    actual_urls = [str(record.get("url") or "").rstrip("/") for record in records if isinstance(record, Mapping)]
    if set(actual_urls) != expected_urls or len(actual_urls) != len(set(actual_urls)):
        raise DailyContentError("EDITOR_OUTPUT_INVALID:reporter_binding")
    preview = preview_dir / "editor-preview.json"
    preview.write_bytes(_json_bytes(dict(value)))
    errors = validate_editor_output_preview(preview, issue_date=issue_date, repo_root=repo_root)
    if errors:
        raise DailyContentError("EDITOR_OUTPUT_INVALID:" + "|".join(errors[:5]))
    return dict(value)


def _validate_deepdive(value: Any, *, issue_date: str, allowed_urls: set[str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:shape")
    article = str(value.get("article_markdown") or "")
    dialogue = str(value.get("dialogue_markdown") or "")
    if (
        f"date: '{issue_date}'" not in article
        and f'date: "{issue_date}"' not in article
        and f"date: {issue_date}" not in article
    ):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:date")
    if not all(marker in article for marker in ("## 背景", "## 深掘り", "## 注目点", "## 参考リンク")):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:sections")
    urls = set(re.findall(r"https?://[^\s)>\]\"']+", article))
    if any(url.rstrip("/") not in allowed_urls for url in urls):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:url_provenance")
    if "## 台本" not in dialogue or not all(label in dialogue for label in ("若手:", "先輩:")):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:dialogue")
    return {"article_markdown": article.rstrip() + "\n", "dialogue_markdown": dialogue.rstrip() + "\n"}


def _atomic_apply(root: Path, outputs: Mapping[str, bytes]) -> dict[str, str]:
    ordered = sorted(outputs)
    originals: dict[str, bytes | None] = {}
    candidates: dict[str, Path] = {}
    try:
        for relative in ordered:
            target = _safe_path(root, relative)
            originals[relative] = target.read_bytes() if target.is_file() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            candidate = Path(raw)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(outputs[relative])
                handle.flush()
                os.fsync(handle.fileno())
            candidates[relative] = candidate
        for relative in ordered:
            os.replace(candidates[relative], _safe_path(root, relative))
    except BaseException:
        for relative in reversed(ordered):
            if relative not in originals:
                continue
            target = _safe_path(root, relative)
            previous = originals[relative]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(target, previous)
        raise
    finally:
        for candidate in candidates.values():
            candidate.unlink(missing_ok=True)
    return {relative: _sha256_bytes(outputs[relative]) for relative in ordered}


def _default_derived_builder(
    *,
    repo_root: Path,
    issue_date: str,
    run_id: str,
    repair_actions: Mapping[str, str] | None = None,
    artifact_checkpoints: Mapping[str, Mapping[str, Any]] | None = None,
    writer_guard: Callable[[], None] | None = None,
    high_cost_admission: Callable[[], bool] | None = None,
    **_: Any,
) -> dict[str, Any]:
    from tools import generate_pages
    from tools.news_grasp_deterministic_builders import materialize_summary_audio_script
    from tools.tts import build_script, deepdive_audio, deepdive_dialogue, publish_audio, synthesize_daily
    from tools.youtube_podcast import build_video

    actions = dict(repair_actions or {})
    checkpoints = dict(artifact_checkpoints or {})

    def needs(artifact_id: str) -> bool:
        return actions.get(artifact_id, "rebuild_deterministic") != "reuse"

    def guard() -> None:
        if writer_guard is not None:
            writer_guard()

    def high_cost_guard(artifact_id: str) -> None:
        guard()
        if high_cost_admission is not None and high_cost_admission() is not True:
            raise DailyContentError(f"SLO_HIGH_COST_GENERATION_FROZEN:{artifact_id}")

    def checkpoint_paths(artifact_id: str) -> list[Path]:
        checkpoint = checkpoints.get(artifact_id) or {}
        payload = checkpoint.get("payload") if isinstance(checkpoint, Mapping) else {}
        hashes = payload.get("artifactHashes") if isinstance(payload, Mapping) else {}
        if not isinstance(hashes, Mapping):
            return []
        return [repo_root / str(relative) for relative in hashes]

    artifacts: list[str] = []
    summary_audio: Mapping[str, Any] = {}
    if needs("daily_audio_script"):
        guard()
        summary_audio = materialize_summary_audio_script(
            repo_root=repo_root,
            issue_date=issue_date,
        )
        artifacts.append(str(repo_root / str(summary_audio["artifactPath"])))

    daily_mp3: Path | None = None
    if needs("daily_audio"):
        high_cost_guard("daily_audio")
        normalized_script = build_script.build(issue_date)
        if normalized_script is None:
            raise DailyContentError("AUDIO_SCRIPT_NORMALIZATION_FAILED")
        artifacts.append(str(normalized_script))
        synthesized = synthesize_daily.synthesize(issue_date)
        if synthesized is None:
            raise DailyContentError("AUDIO_SYNTHESIS_FAILED:daily")
        daily_mp3 = Path(synthesized)
        artifacts.append(str(daily_mp3))
    else:
        daily_mp3 = next(
            (item for item in checkpoint_paths("daily_audio") if item.suffix.casefold() == ".mp3"),
            None,
        )

    deep_script = repo_root / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md"
    deep_mp3: Path | None = None
    if needs("deepdive_audio"):
        high_cost_guard("deepdive_audio")
        synthesized = deepdive_dialogue.synthesize_dialogue(deep_script, out_name=issue_date)
        if synthesized is None:
            raise DailyContentError("AUDIO_SYNTHESIS_FAILED:deepdive")
        deep_mp3 = Path(synthesized)
        artifacts.append(str(deep_mp3))
    else:
        deep_mp3 = next(
            (item for item in checkpoint_paths("deepdive_audio") if item.suffix.casefold() == ".mp3"),
            None,
        )

    if needs("daily_audio_projection"):
        guard()
        if daily_mp3 is None or not daily_mp3.is_file():
            raise DailyContentError("DAILY_AUDIO_REUSE_MISSING")
        publish_audio.write_latest_audio(
            issue_date,
            publish_audio.versioned_audio_url(issue_date, daily_mp3),
            run_id=run_id,
        )
        artifacts.append(str(repo_root / "build" / "tts" / "daily" / "latest_audio.json"))
    if needs("deepdive_audio_projection"):
        guard()
        if deep_mp3 is None or not deep_mp3.is_file():
            raise DailyContentError("DEEPDIVE_AUDIO_REUSE_MISSING")
        deepdive_audio.write_latest_audio(
            issue_date,
            deepdive_audio.versioned_deepdive_audio_url(issue_date, deep_mp3),
            run_id=run_id,
        )
        artifacts.append(str(repo_root / "build" / "tts" / "deepdive" / "latest_audio.json"))

    if needs("daily_video"):
        high_cost_guard("daily_video")
        video = build_video.build(issue_date, kind="daily")
        if not isinstance(video, Mapping) or not video.get("mp4_path"):
            raise DailyContentError("VIDEO_BUILD_FAILED:daily")
        artifacts.append(str(video["mp4_path"]))
    if needs("deepdive_video"):
        high_cost_guard("deepdive_video")
        video = build_video.build(issue_date, kind="deepdive")
        if not isinstance(video, Mapping) or not video.get("mp4_path"):
            raise DailyContentError("VIDEO_BUILD_FAILED:deepdive")
        artifacts.append(str(video["mp4_path"]))
    if needs("site_html"):
        guard()
        artifacts.extend(map(str, generate_pages.build_all(full=False)))
    from tools.render_deepdive import build_deepdive_archive, build_deepdive_pages

    if needs("deepdive_html"):
        guard()
        artifacts.extend(
            map(
                str,
                build_deepdive_pages(
                    docs_root=repo_root / "docs",
                    full=False,
                    issue_date=issue_date,
                ),
            )
        )
        archive = build_deepdive_archive(docs_root=repo_root / "docs")
        if archive is not None:
            if isinstance(archive, Sequence) and not isinstance(archive, (str, bytes, bytearray)):
                artifacts.extend(map(str, archive))
            else:
                artifacts.append(str(archive))
    guard()
    return {
        "ok": True,
        "status": "built",
        "artifacts": list(dict.fromkeys(artifacts)),
        "summary_audio": dict(summary_audio),
    }


def _derived_artifact_hashes(root: Path, derived: Mapping[str, Any]) -> dict[str, str]:
    artifacts = derived.get("artifacts", [])
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        raise DailyContentError("DERIVED_ARTIFACTS_INVALID")
    hashes: dict[str, str] = {}
    for item in artifacts:
        candidate = Path(str(item))
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError) as exc:
            raise DailyContentError("DERIVED_ARTIFACT_PATH_INVALID") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise DailyContentError("DERIVED_ARTIFACT_PATH_INVALID")
        hashes[relative] = _sha256_bytes(resolved.read_bytes())
    return hashes


def _repair_plan_requires_work(runtime_ledger: Any) -> bool:
    checkpoints = runtime_ledger.list_checkpoints()
    if any(item.get("status") == "Red" for item in checkpoints.values()):
        return True
    repair_plan = runtime_ledger.load_repair_plan()
    return bool(
        repair_plan
        and any(step.get("action") != "reuse" for step in repair_plan.get("steps") or ())
    )


def produce_current_issue(
    *,
    repo_root: Path | str,
    issue_date: str,
    run_id: str,
    scheduled_categories: Sequence[str],
    candidate_provider: Callable[[str, str], tuple[list[dict[str, Any]], dict[str, Any]]] | None = None,
    model_runner: Callable[..., Mapping[str, Any]] | None = None,
    derived_builder: Callable[..., Mapping[str, Any]] | None = None,
    runtime_store: Any = None,
    writer_lease: str = "",
    fencing_token: int = 0,
    slo_dispatch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """対象日のmodel outputを全検証後に一つのcanonical bundleへ反映する。"""

    if issue_date == PROTECTED_RELEASE:
        raise DailyContentError("PROTECTED_RELEASE_REEXECUTION_FORBIDDEN")
    try:
        if date.fromisoformat(issue_date).isoformat() != issue_date:
            raise ValueError(issue_date)
    except ValueError as exc:
        raise DailyContentError("ISSUE_DATE_INVALID") from exc
    if not _RUN_ID_RE.fullmatch(str(run_id or "")) or str(run_id).casefold() in {"final", "latest", "current"}:
        raise DailyContentError("ACTUAL_RUN_ID_REQUIRED")
    categories = tuple(str(item) for item in scheduled_categories)
    if not categories or len(categories) != len(set(categories)) or any(item not in _GENRES for item in categories):
        raise DailyContentError("SCHEDULED_CATEGORIES_INVALID")
    root = _safe_root(repo_root)
    runtime_ledger: Any = None
    if runtime_store is not None:
        from tools.news_grasp_direct_runtime import DailyArtifactLedger

        runtime_ledger = DailyArtifactLedger(
            runtime_store,
            run_id=run_id,
            issue_date=issue_date,
            writer_lease=writer_lease,
            fencing_token=fencing_token,
        )
    elif candidate_provider is None or model_runner is None or derived_builder is None:
        raise DailyContentError("CANONICAL_RUNTIME_LEDGER_REQUIRED")

    completion_input_hash = _artifact_input_hash(
        {"issueDate": issue_date, "scheduledCategories": list(categories)}
    )
    if runtime_ledger is not None:
        if not _repair_plan_requires_work(runtime_ledger):
            completion_checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id="content_completion",
                input_hash=completion_input_hash,
                runtime_ledger=runtime_ledger,
            )
            if completion_checkpoint is not None:
                try:
                    return _validate_completion_payload(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        value=completion_checkpoint["payload"],
                    )
                except DailyContentError as exc:
                    if str(exc) != "CONTENT_RECEIPT_ARTIFACT_DRIFT":
                        raise
    else:
        reused = _load_completion(root, run_id, issue_date)
        if reused is not None:
            return reused
    candidate_fn = candidate_provider or _default_candidate_provider
    model_fn = model_runner or _default_model_runner
    derived_fn = derived_builder or _default_derived_builder
    cached_bundle = (
        None
        if runtime_ledger is not None
        else _load_model_bundle(
            root,
            run_id=run_id,
            issue_date=issue_date,
            categories=categories,
        )
    )
    from tools.news_grasp_repair_registry import ModelCallBudgetLedger

    budget = (
        runtime_ledger
        if runtime_ledger is not None
        else ModelCallBudgetLedger(
            _safe_path(root, f"build/daily-content/{run_id}/model-call-budget.json"),
            issue_date=issue_date,
            run_id=run_id,
        )
    )

    def refresh_repair_plan() -> dict[str, Any] | None:
        if runtime_ledger is None:
            return None
        from tools.news_grasp_repair_registry import build_repair_plan

        checkpoints = runtime_ledger.list_checkpoints()
        failures = [
            dict(item.get("failure") or {})
            for item in checkpoints.values()
            if item.get("status") == "Red" and item.get("failure")
        ]
        return runtime_ledger.persist_repair_plan(
            build_repair_plan(
                issue_date=issue_date,
                run_id=run_id,
                categories=categories,
                checkpoints=checkpoints,
                failures=failures,
            )
        )

    repair_plan = refresh_repair_plan()

    def planned_action(artifact_id: str) -> str:
        if repair_plan is None:
            return "legacy"
        for step in repair_plan["steps"]:
            if step["artifactId"] == artifact_id:
                return str(step["action"])
        raise DailyContentError(f"REPAIR_PLAN_ARTIFACT_MISSING:{artifact_id}")

    def authorize_missing_checkpoint(
        artifact_id: str,
        input_hash: str,
        *,
        stage: str,
        predicate_id: str,
    ) -> None:
        nonlocal repair_plan
        if runtime_ledger is None or planned_action(artifact_id) != "reuse":
            return
        existing = runtime_ledger.list_checkpoints().get(artifact_id)
        _write_failure_checkpoint(
            root,
            run_id=run_id,
            issue_date=issue_date,
            stage=stage,
            artifact_id=artifact_id,
            predicate_id=predicate_id,
            reason_code="checkpoint_input_or_validator_drift",
            input_hash=input_hash,
            cause_input_mask=(artifact_id,),
            invalid_payload=(existing or {}).get("payload") if existing else None,
            runtime_ledger=runtime_ledger,
        )
        repair_plan = refresh_repair_plan()

    def consume_model_call(**request: Any) -> dict[str, Any]:
        if runtime_ledger is not None:
            current_admission = __import__(
                "tools.news_grasp_direct_runtime",
                fromlist=["admit_daily_operation"],
            ).admit_daily_operation(
                runtime_store,
                run_id=run_id,
                writer_lease=writer_lease,
                fencing_token=fencing_token,
                operation_id="current_issue_integration",
            )
            if current_admission.get("required_content_generation_allowed") is not True:
                raise DailyContentError("SLO_REQUIRED_CONTENT_GENERATION_FROZEN")
            return runtime_ledger.reserve_model_call(**request)
        return budget.consume(**request)
    model_call_count = 0
    reporter_call_count = 0
    reused_model_artifacts: list[str] = []
    repaired_model_artifacts: list[str] = []
    if cached_bundle is not None:
        ordered_reporters, editor, deepdive = cached_bundle
        reused_model_artifacts = [
            *[f"reporter:{category}" for category in categories],
            "editor",
            "deepdive_model",
        ]
    else:
        candidates_by_category: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        candidate_checkpoints: dict[str, dict[str, Any]] = {}
        missing_candidates: list[str] = []
        for category in categories:
            artifact_id = f"candidate:{category}"
            input_hash = _artifact_input_hash(
                {"issueDate": issue_date, "runId": run_id, "category": category}
            )
            checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id=artifact_id,
                input_hash=input_hash,
                runtime_ledger=runtime_ledger,
            )
            if checkpoint is None:
                authorize_missing_checkpoint(
                    artifact_id,
                    input_hash,
                    stage="candidate_collection",
                    predicate_id="candidate_available",
                )
            if runtime_ledger is not None and checkpoint is not None and planned_action(artifact_id) != "reuse":
                checkpoint = None
            if checkpoint is None:
                missing_candidates.append(category)
                continue
            payload = checkpoint["payload"]
            candidates = payload.get("candidates")
            audit = payload.get("search_audit")
            try:
                validated_candidates, validated_audit = _validate_candidate_payload(
                    category=category,
                    issue_date=issue_date,
                    candidates=candidates,
                    audit=audit,
                )
            except DailyContentError:
                _write_failure_checkpoint(
                    root,
                    run_id=run_id,
                    issue_date=issue_date,
                    stage="candidate_collection",
                    artifact_id=artifact_id,
                    predicate_id="candidate_available",
                    reason_code=f"CANDIDATE_CHECKPOINT_INVALID:{category}",
                    input_hash=input_hash,
                    cause_input_mask=(artifact_id,),
                    invalid_payload=payload,
                    runtime_ledger=runtime_ledger,
                )
                missing_candidates.append(category)
                continue
            candidates_by_category[category] = (validated_candidates, validated_audit)
            candidate_checkpoints[category] = checkpoint

        if missing_candidates and runtime_ledger is not None:
            admission = __import__(
                "tools.news_grasp_direct_runtime",
                fromlist=["admit_daily_operation"],
            ).admit_daily_operation(
                runtime_store,
                run_id=run_id,
                writer_lease=writer_lease,
                fencing_token=fencing_token,
                operation_id="current_issue_integration",
            )
            if admission.get("required_content_generation_allowed") is not True:
                raise DailyContentError("SLO_CANDIDATE_COLLECTION_FROZEN")
        with ThreadPoolExecutor(max_workers=max(1, len(missing_candidates))) as pool:
            futures = {
                pool.submit(candidate_fn, category, issue_date): category
                for category in missing_candidates
            }
            for future in as_completed(futures):
                category = futures[future]
                artifact_id = f"candidate:{category}"
                input_hash = _artifact_input_hash(
                    {"issueDate": issue_date, "runId": run_id, "category": category}
                )
                try:
                    candidates, audit = future.result()
                    validated_candidates, validated_audit = _validate_candidate_payload(
                        category=category,
                        issue_date=issue_date,
                        candidates=candidates,
                        audit=audit,
                    )
                    candidates_by_category[category] = (
                        validated_candidates,
                        validated_audit,
                    )
                    candidate_checkpoints[category] = _write_artifact_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        artifact_id=artifact_id,
                        input_hash=input_hash,
                        payload={"candidates": candidates, "search_audit": audit},
                        runtime_ledger=runtime_ledger,
                    )
                except Exception as exc:  # noqa: BLE001
                    _write_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        stage="candidate_collection",
                        artifact_id=artifact_id,
                        predicate_id="candidate_available",
                        reason_code=f"{type(exc).__name__}:{exc}",
                        input_hash=input_hash,
                        cause_input_mask=(artifact_id,),
                        runtime_ledger=runtime_ledger,
                    )
                    raise DailyContentError(f"CANDIDATE_COLLECTION_FAILED:{category}:{type(exc).__name__}") from exc

        repair_plan = refresh_repair_plan()

        with _persistent_model_output_context(root, run_id) as output_dir:
            def invoke_model_call(
                *,
                reservation: Mapping[str, Any],
                role: str,
                category: str | None,
                call_id: str,
                input_hash: str,
                artifact_id: str,
                **model_context: Any,
            ) -> tuple[Any, bool]:
                call_root = _model_call_root(root, run_id, call_id)
                intent_path = call_root / "intent.json"
                raw_path = call_root / MODEL_CALL_RAW_FILENAME
                expected_intent = _model_call_intent(
                    root=root,
                    run_id=run_id,
                    issue_date=issue_date,
                    role=role,
                    category=category,
                    call_id=call_id,
                    input_hash=input_hash,
                )

                def recover_schema_rejection() -> tuple[Any, bool]:
                    events_path = call_root / (
                        f"{_model_call_label(role, category, model_context)}.events.jsonl"
                    )
                    original_events_sha = _confirmed_schema_rejection_sha256(events_path)
                    if original_events_sha is None:
                        raise ModelResultPending(artifact_id)
                    try:
                        recovery_root = _schema_recovery_root(call_root)
                    except (DailyContentError, OSError) as exc:
                        raise ModelResultPending(
                            f"{artifact_id}:schema_recovery"
                        ) from exc
                    recovery_intent_path = recovery_root / "intent.json"
                    recovery_raw_path = recovery_root / MODEL_CALL_RAW_FILENAME
                    schema_sha = _verified_model_schema_sha256(
                        root,
                        role,
                        pending_detail=f"{artifact_id}:schema",
                    )
                    if recovery_intent_path.exists():
                        if not _schema_recovery_metadata_matches(
                            recovery_root / "metadata.json",
                            call_id=call_id,
                            original_events_sha=original_events_sha,
                            schema_sha=schema_sha,
                        ):
                            raise ModelResultPending(
                                f"{artifact_id}:schema_recovery_metadata"
                            )
                        try:
                            _ensure_model_call_intent(
                                recovery_intent_path,
                                expected_intent,
                            )
                        except (DailyContentError, OSError) as exc:
                            raise ModelResultPending(
                                f"{artifact_id}:schema_recovery_intent"
                            ) from exc
                        present, recovery_value = _load_model_call_raw(recovery_raw_path)
                        if not present:
                            raise ModelResultPending(artifact_id)
                        return recovery_value, False

                    try:
                        metadata = {
                            "schemaVersion": "NEWS_GRASP_MODEL_SCHEMA_RECOVERY_V1",
                            "reason": "invalid_json_schema",
                            "callId": call_id,
                            "originalEventsSha256": original_events_sha,
                            "schemaSha256": schema_sha,
                        }
                        _atomic_write_bytes(
                            recovery_root / "metadata.json",
                            _json_bytes(metadata),
                        )
                    except (OSError, TypeError, ValueError) as exc:
                        raise ModelResultPending(
                            f"{artifact_id}:schema_recovery_metadata"
                        ) from exc
                    try:
                        _ensure_model_call_intent(recovery_intent_path, expected_intent)
                    except (DailyContentError, OSError) as exc:
                        raise ModelResultPending(
                            f"{artifact_id}:schema_recovery_intent"
                        ) from exc
                    recovery_value = model_fn(
                        role=role,
                        repo_root=root,
                        issue_date=issue_date,
                        run_id=run_id,
                        category=category,
                        output_dir=recovery_root,
                        call_id=call_id,
                        input_hash=input_hash,
                        intent_path=recovery_intent_path,
                        raw_path=recovery_raw_path,
                        **model_context,
                    )
                    present, persisted_value = _load_model_call_raw(recovery_raw_path)
                    if not present:
                        try:
                            _atomic_write_bytes(
                                recovery_raw_path,
                                _json_bytes(recovery_value),
                            )
                        except (OSError, TypeError, ValueError) as exc:
                            raise DailyContentError(
                                "MODEL_RAW_OUTPUT_PERSIST_FAILED"
                            ) from exc
                        present, persisted_value = _load_model_call_raw(recovery_raw_path)
                    if not present:
                        raise ModelResultPending(artifact_id)
                    return persisted_value, True

                is_idempotent = reservation.get("idempotent") is True
                status = str(reservation.get("status") or "reserved")
                if is_idempotent and status == "completed":
                    raise DailyContentError(
                        f"MODEL_CALL_COMPLETED_CHECKPOINT_MISSING:{artifact_id}"
                    )
                if is_idempotent:
                    if not intent_path.is_file() or _is_reparse_point(intent_path):
                        raise ModelResultPending(artifact_id)
                    _ensure_model_call_intent(intent_path, expected_intent)
                    present, raw_value = _load_model_call_raw(raw_path)
                    if not present:
                        return recover_schema_rejection()
                    return raw_value, False

                if intent_path.exists():
                    _ensure_model_call_intent(intent_path, expected_intent)
                    present, raw_value = _load_model_call_raw(raw_path)
                    if present:
                        return raw_value, False
                    return recover_schema_rejection()
                _ensure_model_call_intent(intent_path, expected_intent)
                raw_value = model_fn(
                    role=role,
                    repo_root=root,
                    issue_date=issue_date,
                    run_id=run_id,
                    category=category,
                    output_dir=call_root,
                    call_id=call_id,
                    input_hash=input_hash,
                    intent_path=intent_path,
                    raw_path=raw_path,
                    **model_context,
                )
                present, persisted_value = _load_model_call_raw(raw_path)
                if not present:
                    try:
                        _atomic_write_bytes(raw_path, _json_bytes(raw_value))
                    except (OSError, TypeError, ValueError) as exc:
                        raise DailyContentError("MODEL_RAW_OUTPUT_PERSIST_FAILED") from exc
                    present, persisted_value = _load_model_call_raw(raw_path)
                if not present:
                    raise ModelResultPending(artifact_id)
                return persisted_value, True

            reporter_rows: dict[str, dict[str, Any]] = {}
            reporter_checkpoints: dict[str, dict[str, Any]] = {}
            reporter_inputs: dict[str, dict[str, Any]] = {}
            missing_reporters: list[str] = []
            for category in categories:
                artifact_id = f"reporter:{category}"
                failure = _load_failure_checkpoint(
                    root,
                    run_id=run_id,
                    issue_date=issue_date,
                    artifact_id=artifact_id,
                    runtime_ledger=runtime_ledger,
                )
                reporter_input = {
                    "issueDate": issue_date,
                    "candidateOutputHash": candidate_checkpoints[category]["outputHash"],
                    "repairFailureSignature": failure["failureSignature"] if failure else None,
                }
                reporter_inputs[category] = reporter_input
                input_hash = _artifact_input_hash(reporter_input)
                checkpoint = _load_artifact_checkpoint(
                    root,
                    run_id=run_id,
                    issue_date=issue_date,
                    artifact_id=artifact_id,
                    input_hash=input_hash,
                    runtime_ledger=runtime_ledger,
                )
                if checkpoint is None:
                    authorize_missing_checkpoint(
                        artifact_id,
                        input_hash,
                        stage="reporter",
                        predicate_id="reporter_output_valid",
                    )
                if runtime_ledger is not None and checkpoint is not None and planned_action(artifact_id) != "reuse":
                    checkpoint = None
                if checkpoint is not None:
                    try:
                        checkpoint["payload"] = _validate_reporter(
                            checkpoint["payload"],
                            category=category,
                            issue_date=issue_date,
                            search_audit={
                                **candidates_by_category[category][1],
                                "candidates": candidates_by_category[category][0],
                            },
                        )
                    except DailyContentError as exc:
                        _write_failure_checkpoint(
                            root,
                            run_id=run_id,
                            issue_date=issue_date,
                            stage="reporter",
                            artifact_id=artifact_id,
                            predicate_id="reporter_output_valid",
                            reason_code=str(exc),
                            input_hash=input_hash,
                            cause_input_mask=(artifact_id,),
                            invalid_payload=checkpoint.get("payload"),
                            runtime_ledger=runtime_ledger,
                        )
                        checkpoint = None
                if checkpoint is None:
                    missing_reporters.append(category)
                    continue
                reporter_rows[category] = dict(checkpoint["payload"])
                reporter_checkpoints[category] = checkpoint
                reused_model_artifacts.append(artifact_id)

            def produce_reporter_shard(
                shard: tuple[str, ...],
            ) -> tuple[
                dict[str, dict[str, Any]],
                dict[str, dict[str, Any]],
                list[str],
                bool,
            ]:
                if runtime_ledger is not None and any(
                    planned_action(f"reporter:{category}") != "repair_model"
                    for category in shard
                ):
                    raise DailyContentError("REPAIR_PLAN_REPORTER_ACTION_MISMATCH")
                shard_failures = {
                    category: _load_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        artifact_id=f"reporter:{category}",
                        runtime_ledger=runtime_ledger,
                    )
                    for category in shard
                }
                budget_class = "repair" if any(shard_failures.values()) else "initial"
                call_artifact_id = (
                    f"reporter:{shard[0]}"
                    if len(shard) == 1
                    else "reporter-shard:" + ",".join(shard)
                )
                call_input_hash = _artifact_input_hash(
                    {
                        "issueDate": issue_date,
                        "reporters": {
                            category: reporter_inputs[category]
                            for category in shard
                        },
                    }
                )
                call_id = _sha256_bytes(
                    f"{budget_class}|{call_artifact_id}|{call_input_hash}".encode("utf-8")
                )
                reservation = consume_model_call(
                    call_id=call_id,
                    budget_class=budget_class,
                    artifact_id=call_artifact_id,
                    input_hash=call_input_hash,
                )

                items = []
                for category in shard:
                    candidates, audit = candidates_by_category[category]
                    items.append(
                        {
                            "category": category,
                            "candidates": candidates,
                            "search_audit": {**audit, "candidates": candidates},
                        }
                    )
                try:
                    if len(shard) == 1:
                        item = items[0]
                        raw_value, model_sent = invoke_model_call(
                            reservation=reservation,
                            role="reporter",
                            category=shard[0],
                            call_id=call_id,
                            input_hash=call_input_hash,
                            artifact_id=call_artifact_id,
                            candidates=item["candidates"],
                            search_audit=item["search_audit"],
                            repair_feedback=shard_failures[shard[0]],
                        )
                        raw_reporters = [raw_value]
                    else:
                        raw_value, model_sent = invoke_model_call(
                            reservation=reservation,
                            role="reporter_shard",
                            category=None,
                            call_id=call_id,
                            input_hash=call_input_hash,
                            artifact_id=call_artifact_id,
                            categories=list(shard),
                            items=items,
                            repair_feedback={
                                category: shard_failures[category]
                                for category in shard
                                if shard_failures[category] is not None
                            },
                        )
                        if (
                            not isinstance(raw_value, Mapping)
                            or raw_value.get("issue_date") != issue_date
                            or not isinstance(raw_value.get("reporters"), list)
                        ):
                            raise DailyContentError(
                                f"REPORTER_OUTPUT_INVALID:{shard[0]}:shard_shape"
                            )
                        raw_reporters = raw_value["reporters"]
                        if [
                            str(item.get("category") or "")
                            for item in raw_reporters
                            if isinstance(item, Mapping)
                        ] != list(shard):
                            raise DailyContentError(
                                f"REPORTER_OUTPUT_INVALID:{shard[0]}:shard_identity"
                            )
                except ModelResultPending:
                    raise
                except Exception as exc:  # noqa: BLE001
                    for category in shard:
                        _write_failure_checkpoint(
                            root,
                            run_id=run_id,
                            issue_date=issue_date,
                            stage="reporter",
                            artifact_id=f"reporter:{category}",
                            predicate_id="reporter_output_valid",
                            reason_code=f"{type(exc).__name__}:{exc}",
                            input_hash=_artifact_input_hash(reporter_inputs[category]),
                            cause_input_mask=(f"reporter:{category}",),
                            invalid_payload=(raw_value if "raw_value" in locals() else None),
                            runtime_ledger=runtime_ledger,
                        )
                    if runtime_ledger is not None:
                        try:
                            runtime_ledger.fail_model_call(
                                call_id=call_id,
                                failure_code=f"{type(exc).__name__}:{exc}",
                            )
                        except PermissionError:
                            raise
                    if isinstance(exc, DailyContentError):
                        raise
                    raise DailyContentError(
                        f"REPORTER_OUTPUT_INVALID:{shard[0]}:{type(exc).__name__}"
                    ) from exc

                rows: dict[str, dict[str, Any]] = {}
                checkpoints: dict[str, dict[str, Any]] = {}
                repaired: list[str] = []
                errors: list[DailyContentError] = []
                partial_failures: dict[str, dict[str, Any]] = {}
                for category, raw_reporter, item in zip(shard, raw_reporters, items, strict=True):
                    artifact_id = f"reporter:{category}"
                    input_hash = _artifact_input_hash(reporter_inputs[category])
                    try:
                        raw_reporter = _project_repair_result(shard_failures[category], raw_reporter)
                        row = _validate_reporter(
                            raw_reporter,
                            category=category,
                            issue_date=issue_date,
                            search_audit=item["search_audit"],
                        )
                    except ModelResultPending:
                        raise
                    except DailyContentError as exc:
                        failure_args = {
                            "run_id": run_id,
                            "issue_date": issue_date,
                            "stage": "reporter",
                            "artifact_id": artifact_id,
                            "predicate_id": "reporter_output_valid",
                            "reason_code": str(exc),
                            "input_hash": input_hash,
                            "cause_input_mask": (artifact_id,),
                            "invalid_payload": raw_reporter,
                        }
                        if runtime_ledger is not None:
                            partial_failures[artifact_id] = _failure_checkpoint_value(
                                **failure_args
                            )
                        else:
                            _write_failure_checkpoint(root, **failure_args)
                        errors.append(exc)
                        continue
                    rows[category] = row
                    if runtime_ledger is None:
                        checkpoints[category] = _write_artifact_checkpoint(
                            root,
                            run_id=run_id,
                            issue_date=issue_date,
                            artifact_id=artifact_id,
                            input_hash=input_hash,
                            payload=row,
                        )
                    if shard_failures[category] is not None:
                        repaired.append(artifact_id)
                if errors:
                    if runtime_ledger is not None:
                        committed = runtime_ledger.commit_model_call_partial(
                            call_id=call_id,
                            artifacts={
                                f"reporter:{category}": {
                                    "inputHash": _artifact_input_hash(reporter_inputs[category]),
                                    "validatorId": _validator_id(f"reporter:{category}"),
                                    "payload": row,
                                }
                                for category, row in rows.items()
                            },
                            failures=partial_failures,
                        )
                        checkpoints.update(
                            {
                                category: committed[f"reporter:{category}"]
                                for category in rows
                            }
                        )
                    raise errors[0]
                if runtime_ledger is not None:
                    committed = runtime_ledger.commit_model_call(
                        call_id=call_id,
                        artifacts={
                            f"reporter:{category}": {
                                "inputHash": _artifact_input_hash(reporter_inputs[category]),
                                "validatorId": _validator_id(f"reporter:{category}"),
                                "payload": rows[category],
                            }
                            for category in shard
                        },
                    )
                    checkpoints.update(
                        {
                            category: committed[f"reporter:{category}"]
                            for category in shard
                        }
                    )
                return rows, checkpoints, repaired, model_sent

            reporter_errors: list[DailyContentError] = []
            reporter_shards = _reporter_shards(missing_reporters)
            with ThreadPoolExecutor(max_workers=max(1, len(reporter_shards))) as pool:
                futures = {
                    pool.submit(produce_reporter_shard, shard): shard
                    for shard in reporter_shards
                }
                for future in as_completed(futures):
                    try:
                        rows, checkpoints, repaired, model_sent = future.result()
                        reporter_rows.update(rows)
                        reporter_checkpoints.update(checkpoints)
                        repaired_model_artifacts.extend(repaired)
                        if model_sent:
                            model_call_count += 1
                            reporter_call_count += 1
                    except DailyContentError as exc:
                        reporter_errors.append(exc)
            if reporter_errors:
                reporter_errors.sort(key=str)
                raise reporter_errors[0]

            repair_plan = refresh_repair_plan()

            ordered_reporters = [reporter_rows[category] for category in categories]
            editor_failure = _load_failure_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id="editor",
                runtime_ledger=runtime_ledger,
            )
            if runtime_ledger is not None and editor_failure and any(
                marker in str(editor_failure.get("reasonCode") or "")
                for marker in ("REPAIR_UNSCOPED_MUTATION", "REPAIR_MUTATION_SCOPE_UNRESOLVED")
            ):
                from tools.news_grasp_saved_scope_recovery import recover_saved_editor

                recovered = recover_saved_editor(
                    repo_root=root, ledger=runtime_ledger, reporters=ordered_reporters,
                    reporter_hashes=[reporter_checkpoints[category]["outputHash"] for category in categories],
                )
                if recovered is None:
                    raise ModelResultPending("saved_editor_scope_recovery_unavailable")
                editor_failure = None
                repair_plan = refresh_repair_plan()
            editor_input = {
                "issueDate": issue_date,
                "reporterOutputHashes": [
                    reporter_checkpoints[category]["outputHash"]
                    for category in categories
                ],
                "repairFailureSignature": editor_failure["failureSignature"] if editor_failure else None,
            }
            editor_input_hash = _artifact_input_hash(editor_input)
            editor_checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id="editor",
                input_hash=editor_input_hash,
                runtime_ledger=runtime_ledger,
            )
            if editor_checkpoint is None:
                authorize_missing_checkpoint(
                    "editor",
                    editor_input_hash,
                    stage="editor",
                    predicate_id="editor_output_valid",
                )
            if runtime_ledger is not None and editor_checkpoint is not None and planned_action("editor") != "reuse":
                editor_checkpoint = None
            if editor_checkpoint is not None:
                try:
                    editor_checkpoint["payload"] = _validate_editor(
                        editor_checkpoint["payload"],
                        issue_date=issue_date,
                        reporters=ordered_reporters,
                        preview_dir=output_dir,
                        repo_root=root,
                    )
                except DailyContentError as exc:
                    editor_failure = _write_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        stage="editor",
                        artifact_id="editor",
                        predicate_id="editor_output_valid",
                        reason_code=str(exc),
                        input_hash=editor_input_hash,
                        cause_input_mask=("editor",),
                        invalid_payload=editor_checkpoint.get("payload"),
                        runtime_ledger=runtime_ledger,
                    )
                    editor_checkpoint = None
            if editor_checkpoint is not None:
                editor = dict(editor_checkpoint["payload"])
                reused_model_artifacts.append("editor")
            else:
                if runtime_ledger is not None and planned_action("editor") != "repair_model":
                    raise DailyContentError("REPAIR_PLAN_EDITOR_ACTION_MISMATCH")
                editor_budget_class = "repair" if editor_failure else "initial"
                editor_call_id = _sha256_bytes(
                    f"{editor_budget_class}|editor|{editor_input_hash}".encode("utf-8")
                )
                reservation = consume_model_call(
                    call_id=editor_call_id,
                    budget_class=editor_budget_class,
                    artifact_id="editor",
                    input_hash=editor_input_hash,
                )
                try:
                    editor_raw, model_sent = invoke_model_call(
                        reservation=reservation,
                        role="editor",
                        category=None,
                        call_id=editor_call_id,
                        input_hash=editor_input_hash,
                        artifact_id="editor",
                        reporters=ordered_reporters,
                        repair_feedback=editor_failure,
                    )
                    if model_sent:
                        model_call_count += 1
                    editor_raw = _project_repair_result(editor_failure, editor_raw)
                    editor = _validate_editor(
                        editor_raw,
                        issue_date=issue_date,
                        reporters=ordered_reporters,
                        preview_dir=output_dir,
                        repo_root=root,
                    )
                except ModelResultPending:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _write_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        stage="editor",
                        artifact_id="editor",
                        predicate_id="editor_output_valid",
                        reason_code=f"{type(exc).__name__}:{exc}",
                        input_hash=editor_input_hash,
                        cause_input_mask=("editor",),
                        invalid_payload=(editor_raw if "editor_raw" in locals() else None),
                        runtime_ledger=runtime_ledger,
                    )
                    if runtime_ledger is not None:
                        try:
                            runtime_ledger.fail_model_call(
                                call_id=editor_call_id,
                                failure_code=f"{type(exc).__name__}:{exc}",
                            )
                        except PermissionError:
                            raise
                    if isinstance(exc, DailyContentError):
                        raise
                    raise DailyContentError(f"EDITOR_OUTPUT_INVALID:{type(exc).__name__}") from exc
                if runtime_ledger is not None:
                    editor_checkpoint = runtime_ledger.commit_model_call(
                        call_id=editor_call_id,
                        artifacts={
                            "editor": {
                                "inputHash": editor_input_hash,
                                "validatorId": _validator_id("editor"),
                                "payload": editor,
                            }
                        },
                    )["editor"]
                else:
                    editor_checkpoint = _write_artifact_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        artifact_id="editor",
                        input_hash=editor_input_hash,
                        payload=editor,
                    )
                if editor_failure is not None:
                    repaired_model_artifacts.append("editor")

            repair_plan = refresh_repair_plan()

            allowed_urls = {
                str(record[key]).rstrip("/")
                for record in editor["append_records"]
                for key in ("url", "thumb")
                if str(record.get(key) or "").startswith(("http://", "https://"))
            }
            deepdive_failure = _load_failure_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id="deepdive_model",
                runtime_ledger=runtime_ledger,
            )
            deepdive_input = {
                "issueDate": issue_date,
                "editorOutputHash": editor_checkpoint["outputHash"],
                "repairFailureSignature": deepdive_failure["failureSignature"] if deepdive_failure else None,
            }
            deepdive_input_hash = _artifact_input_hash(deepdive_input)
            deepdive_checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id="deepdive_model",
                input_hash=deepdive_input_hash,
                runtime_ledger=runtime_ledger,
            )
            if deepdive_checkpoint is None:
                authorize_missing_checkpoint(
                    "deepdive_model",
                    deepdive_input_hash,
                    stage="deepdive",
                    predicate_id="deepdive_output_valid",
                )
            if runtime_ledger is not None and deepdive_checkpoint is not None and planned_action("deepdive_model") != "reuse":
                deepdive_checkpoint = None
            if deepdive_checkpoint is not None:
                try:
                    deepdive_checkpoint["payload"] = _validate_deepdive(
                        deepdive_checkpoint["payload"],
                        issue_date=issue_date,
                        allowed_urls=allowed_urls,
                    )
                except DailyContentError as exc:
                    deepdive_failure = _write_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        stage="deepdive",
                        artifact_id="deepdive_model",
                        predicate_id="deepdive_output_valid",
                        reason_code=str(exc),
                        input_hash=deepdive_input_hash,
                        cause_input_mask=("deepdive_model",),
                        invalid_payload=deepdive_checkpoint.get("payload"),
                        runtime_ledger=runtime_ledger,
                    )
                    deepdive_checkpoint = None
            if deepdive_checkpoint is not None:
                deepdive = dict(deepdive_checkpoint["payload"])
                reused_model_artifacts.append("deepdive_model")
            else:
                if runtime_ledger is not None and planned_action("deepdive_model") != "repair_model":
                    raise DailyContentError("REPAIR_PLAN_DEEPDIVE_ACTION_MISMATCH")
                deepdive_budget_class = "repair" if deepdive_failure else "initial"
                deepdive_call_id = _sha256_bytes(
                    f"{deepdive_budget_class}|deepdive_model|{deepdive_input_hash}".encode("utf-8")
                )
                reservation = consume_model_call(
                    call_id=deepdive_call_id,
                    budget_class=deepdive_budget_class,
                    artifact_id="deepdive_model",
                    input_hash=deepdive_input_hash,
                )
                try:
                    deepdive_raw, model_sent = invoke_model_call(
                        reservation=reservation,
                        role="deepdive",
                        category=None,
                        call_id=deepdive_call_id,
                        input_hash=deepdive_input_hash,
                        artifact_id="deepdive_model",
                        summary_markdown=editor["summary_markdown"],
                        records=editor["append_records"],
                        repair_feedback=deepdive_failure,
                    )
                    if model_sent:
                        model_call_count += 1
                    deepdive_raw = _project_repair_result(deepdive_failure, deepdive_raw)
                    deepdive = _validate_deepdive(
                        deepdive_raw,
                        issue_date=issue_date,
                        allowed_urls=allowed_urls,
                    )
                except ModelResultPending:
                    raise
                except Exception as exc:  # noqa: BLE001
                    _write_failure_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        stage="deepdive",
                        artifact_id="deepdive_model",
                        predicate_id="deepdive_output_valid",
                        reason_code=f"{type(exc).__name__}:{exc}",
                        input_hash=deepdive_input_hash,
                        cause_input_mask=("deepdive_model",),
                        invalid_payload=(deepdive_raw if "deepdive_raw" in locals() else None),
                        runtime_ledger=runtime_ledger,
                    )
                    if runtime_ledger is not None:
                        try:
                            runtime_ledger.fail_model_call(
                                call_id=deepdive_call_id,
                                failure_code=f"{type(exc).__name__}:{exc}",
                            )
                        except PermissionError:
                            raise
                    if isinstance(exc, DailyContentError):
                        raise
                    raise DailyContentError(
                        f"DEEPDIVE_OUTPUT_INVALID:{type(exc).__name__}"
                    ) from exc
                if runtime_ledger is not None:
                    deepdive_checkpoint = runtime_ledger.commit_model_call(
                        call_id=deepdive_call_id,
                        artifacts={
                            "deepdive_model": {
                                "inputHash": deepdive_input_hash,
                                "validatorId": _validator_id("deepdive_model"),
                                "payload": deepdive,
                            }
                        },
                    )["deepdive_model"]
                else:
                    deepdive_checkpoint = _write_artifact_checkpoint(
                        root,
                        run_id=run_id,
                        issue_date=issue_date,
                        artifact_id="deepdive_model",
                        input_hash=deepdive_input_hash,
                        payload=deepdive,
                    )
                if deepdive_failure is not None:
                    repaired_model_artifacts.append("deepdive_model")

            repair_plan = refresh_repair_plan()

        _write_model_bundle(
            root,
            run_id=run_id,
            issue_date=issue_date,
            categories=categories,
            reporters=ordered_reporters,
            editor=editor,
            deepdive=deepdive,
        )

    desired_outputs: dict[str, bytes] = {}
    path_owner: dict[str, str] = {}
    for reporter in ordered_reporters:
        category = str(reporter["category"])
        genre = _GENRES[category]
        records_path = f"tmp/newsroom/{issue_date}/{category}.records.jsonl"
        audit_path = f"data/search_audit/{issue_date}/{category}.json"
        digest_path = f"digest/{genre}/{issue_date}-{genre}.md"
        desired_outputs[records_path] = b"".join(
            _json_bytes(record) for record in reporter["records"]
        )
        desired_outputs[audit_path] = _json_bytes(reporter["search_audit"])
        desired_outputs[digest_path] = str(reporter["digest_markdown"]).encode("utf-8")
        path_owner[records_path] = f"reporter_records:{category}"
        path_owner[audit_path] = f"search_audit:{category}"
        path_owner[digest_path] = f"digest:{category}"
    summary_path = f"digest/Summary/{issue_date}.md"
    deep_article_path = f"digest/DeepDive/{issue_date}-DeepDive.md"
    deep_dialogue_path = f"digest/DeepDive/{issue_date}-DeepDive-dialogue.md"
    desired_outputs[summary_path] = (str(editor["summary_markdown"]).rstrip() + "\n").encode("utf-8")
    desired_outputs[deep_article_path] = deepdive["article_markdown"].encode("utf-8")
    desired_outputs[deep_dialogue_path] = deepdive["dialogue_markdown"].encode("utf-8")
    path_owner[summary_path] = "summary"
    path_owner[deep_article_path] = "deepdive_article"
    path_owner[deep_dialogue_path] = "deepdive_dialogue"
    articles_path = _safe_path(root, "data/articles.jsonl")
    if runtime_ledger is not None:
        baseline = _read_articles_jsonl_baseline(root, runtime_ledger)
        history = _historical_article_lines(baseline, issue_date=issue_date)
        desired_outputs["data/articles.jsonl"] = b"".join(history) + b"".join(
            _json_bytes(record) for record in editor["append_records"]
        )
    else:
        previous = articles_path.read_bytes() if articles_path.is_file() else b""
        if previous and not previous.endswith(b"\n"):
            previous += b"\n"
        existing_keys: set[tuple[str, str]] = set()
        for line in previous.decode("utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DailyContentError("ARTICLES_JSONL_INVALID") from exc
            existing_keys.add((str(row.get("date") or ""), str(row.get("url") or "")))
        additions = [
            record
            for record in editor["append_records"]
            if (str(record.get("date") or ""), str(record.get("url") or "")) not in existing_keys
        ]
        desired_outputs["data/articles.jsonl"] = previous + b"".join(
            _json_bytes(record) for record in additions
        )
    path_owner["data/articles.jsonl"] = "articles_jsonl"

    deterministic_input_hashes: dict[str, str] = {}
    if runtime_ledger is not None:
        model_hashes = {
            **{
                f"reporter:{category}": reporter_checkpoints[category]["outputHash"]
                for category in categories
            },
            "editor": editor_checkpoint["outputHash"],
            "deepdive_model": deepdive_checkpoint["outputHash"],
        }
        for artifact_id in dict.fromkeys(path_owner.values()):
            if artifact_id.startswith(("reporter_records:", "search_audit:", "digest:")):
                dependency = "reporter:" + artifact_id.split(":", 1)[1]
            elif artifact_id in {"articles_jsonl", "summary"}:
                dependency = "editor"
            else:
                dependency = "deepdive_model"
            expected_input_hash = _artifact_input_hash(
                {"artifactId": artifact_id, "dependencyOutputHash": model_hashes[dependency]}
            )
            deterministic_input_hashes[artifact_id] = expected_input_hash
            checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id=artifact_id,
                input_hash=expected_input_hash,
                runtime_ledger=runtime_ledger,
            )
            if checkpoint is None:
                authorize_missing_checkpoint(
                    artifact_id,
                    expected_input_hash,
                    stage="deterministic_materialization",
                    predicate_id="materialized_bytes_match",
                )
        repair_plan = refresh_repair_plan()

    outputs: dict[str, bytes] = {}
    recorded_drift: set[str] = set()
    for relative, value in desired_outputs.items():
        owner = path_owner[relative]
        target = _safe_path(root, relative)
        current = target.read_bytes() if target.is_file() else None
        if runtime_ledger is None or planned_action(owner) != "reuse" or current != value:
            if (
                runtime_ledger is not None
                and planned_action(owner) == "reuse"
                and owner not in recorded_drift
            ):
                _write_failure_checkpoint(
                    root,
                    run_id=run_id,
                    issue_date=issue_date,
                    stage="deterministic_materialization",
                    artifact_id=owner,
                    predicate_id="materialized_bytes_match",
                    reason_code="materialized_artifact_drift",
                    input_hash=_artifact_input_hash({"artifactId": owner, "desiredHash": _sha256_bytes(value)}),
                    cause_input_mask=(owner,),
                    runtime_ledger=runtime_ledger,
                )
                recorded_drift.add(owner)
                repair_plan = refresh_repair_plan()
            outputs[relative] = value
    if runtime_ledger is not None:
        with runtime_ledger.materialization_fence():
            _atomic_apply(root, outputs)
    else:
        _atomic_apply(root, outputs)
    artifact_hashes = {
        relative: _sha256_bytes(_safe_path(root, relative).read_bytes())
        for relative in desired_outputs
    }

    if runtime_ledger is not None:
        for artifact_id in dict.fromkeys(path_owner.values()):
            owned = {
                relative: artifact_hashes[relative]
                for relative, owner in path_owner.items()
                if owner == artifact_id
            }
            _write_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id=artifact_id,
                input_hash=deterministic_input_hashes[artifact_id],
                payload={"artifactHashes": owned},
                runtime_ledger=runtime_ledger,
            )
        repair_plan = refresh_repair_plan()

    derived_ids = (
        "daily_audio_script",
        "daily_audio",
        "daily_audio_projection",
        "daily_video",
        "deepdive_html",
        "deepdive_audio",
        "deepdive_audio_projection",
        "deepdive_video",
        "site_html",
    )
    if runtime_ledger is not None:
        from tools.news_grasp_repair_registry import build_daily_artifact_dag

        dag = build_daily_artifact_dag(categories)
        checkpoints = runtime_ledger.list_checkpoints()
        for artifact_id in derived_ids:
            dependency_hashes = {
                dependency: str((checkpoints.get(dependency) or {}).get("outputHash") or "")
                for dependency in dag[artifact_id]["dependsOn"]
            }
            if not all(dependency_hashes.values()):
                continue
            expected_input_hash = _artifact_input_hash(
                {"artifactId": artifact_id, "dependencyOutputHashes": dependency_hashes}
            )
            checkpoint = _load_artifact_checkpoint(
                root,
                run_id=run_id,
                issue_date=issue_date,
                artifact_id=artifact_id,
                input_hash=expected_input_hash,
                runtime_ledger=runtime_ledger,
            )
            if checkpoint is None:
                authorize_missing_checkpoint(
                    artifact_id,
                    expected_input_hash,
                    stage="derived_materialization",
                    predicate_id="derived_artifact_hashes_match",
                )
        repair_plan = refresh_repair_plan()

    derived_kwargs: dict[str, Any] = {
        "repo_root": root,
        "issue_date": issue_date,
        "run_id": run_id,
        "artifact_hashes": artifact_hashes,
    }
    if runtime_ledger is not None:
        def high_cost_admission() -> bool:
            current = __import__(
                "tools.news_grasp_direct_runtime",
                fromlist=["admit_daily_operation"],
            ).admit_daily_operation(
                runtime_store,
                run_id=run_id,
                writer_lease=writer_lease,
                fencing_token=fencing_token,
                operation_id="current_issue_integration",
            )
            return current.get("required_content_generation_allowed") is True

        derived_kwargs.update(
            {
                "repair_actions": {
                    str(step["artifactId"]): str(step["action"])
                    for step in (repair_plan or {}).get("steps", [])
                },
                "artifact_checkpoints": runtime_ledger.list_checkpoints(),
                "writer_guard": runtime_ledger.assert_writer,
                "high_cost_admission": high_cost_admission,
            }
        )
    if runtime_ledger is not None:
        runtime_ledger.assert_writer()
    derived = derived_fn(**derived_kwargs)
    if runtime_ledger is not None:
        runtime_ledger.assert_writer()
    if not isinstance(derived, Mapping) or derived.get("ok") is not True:
        raise DailyContentError("DERIVED_BUILD_FAILED")
    changed_derived_hashes = _derived_artifact_hashes(root, derived)
    derived_artifact_hashes = dict(changed_derived_hashes)
    if runtime_ledger is not None:
        def derived_owner(relative: str) -> str:
            folded = relative.replace("\\", "/").casefold()
            if folded.endswith("latest_audio.json") and "deepdive" in folded:
                return "deepdive_audio_projection"
            if folded.endswith("latest_audio.json"):
                return "daily_audio_projection"
            if folded.endswith(".mp3") and "deepdive" in folded:
                return "deepdive_audio"
            if folded.endswith(".mp3"):
                return "daily_audio"
            if folded.endswith(".mp4") and "deepdive" in folded:
                return "deepdive_video"
            if folded.endswith(".mp4"):
                return "daily_video"
            if "deepdive" in folded:
                return "deepdive_html"
            if "audio" in Path(relative).name.casefold() and folded.endswith(".md"):
                return "daily_audio_script"
            return "site_html"

        changed_groups: dict[str, dict[str, str]] = {}
        for relative, digest in changed_derived_hashes.items():
            changed_groups.setdefault(derived_owner(relative), {})[relative] = digest
        checkpoints = runtime_ledger.list_checkpoints()
        for artifact_id in derived_ids:
            node = dag[artifact_id]
            dependency_hashes = {
                dependency: str((checkpoints.get(dependency) or {}).get("outputHash") or "")
                for dependency in node["dependsOn"]
            }
            if not all(dependency_hashes.values()):
                raise DailyContentError(f"DERIVED_DEPENDENCY_CHECKPOINT_MISSING:{artifact_id}")
            owned = changed_groups.get(artifact_id)
            if owned:
                checkpoint = _write_artifact_checkpoint(
                    root,
                    run_id=run_id,
                    issue_date=issue_date,
                    artifact_id=artifact_id,
                    input_hash=_artifact_input_hash(
                        {"artifactId": artifact_id, "dependencyOutputHashes": dependency_hashes}
                    ),
                    payload={"artifactHashes": owned},
                    runtime_ledger=runtime_ledger,
                )
                checkpoints[artifact_id] = checkpoint
            else:
                checkpoint = checkpoints.get(artifact_id)
                if not isinstance(checkpoint, Mapping) or checkpoint.get("status") != "Green":
                    raise DailyContentError(f"DERIVED_REPAIR_OUTPUT_MISSING:{artifact_id}")
                hashes = (checkpoint.get("payload") or {}).get("artifactHashes")
                if not isinstance(hashes, Mapping):
                    raise DailyContentError(f"DERIVED_CHECKPOINT_INVALID:{artifact_id}")
                for relative, expected in hashes.items():
                    target = _safe_path(root, str(relative))
                    if not target.is_file() or _sha256_bytes(target.read_bytes()) != expected:
                        raise DailyContentError(f"DERIVED_CHECKPOINT_DRIFT:{artifact_id}")
                    derived_artifact_hashes[str(relative)] = str(expected)
        repair_plan = refresh_repair_plan()
    bundle_id = _sha256_bytes(
        _json_bytes(
            {
                "issue_date": issue_date,
                "run_id": run_id,
                "artifact_hashes": artifact_hashes,
                "derived_artifact_hashes": derived_artifact_hashes,
            }
        )
    )
    receipt = {
        "schemaVersion": CONTENT_RECEIPT_SCHEMA,
        "ok": True,
        "status": "materialized",
        "issue_date": issue_date,
        "run_id": run_id,
        "scheduled_categories": list(categories),
        "reporter_call_count": reporter_call_count,
        "model_call_count": model_call_count,
        "model_call_count_total": max(
            (
                runtime_ledger.model_call_usage()["total"]
                if runtime_ledger is not None
                else budget.usage()["total"]
            ),
            min(3, len(categories)) + 2 if cached_bundle is not None else 0,
        ),
        "reused_model_artifacts": reused_model_artifacts,
        "repaired_model_artifacts": repaired_model_artifacts,
        "bundle_id": bundle_id,
        "artifact_hashes": artifact_hashes,
        "derived_artifact_hashes": derived_artifact_hashes,
        "derived": dict(derived),
        "repair_plan_sha256": str((repair_plan or {}).get("planSha256") or ""),
    }
    if runtime_ledger is not None:
        _write_artifact_checkpoint(
            root,
            run_id=run_id,
            issue_date=issue_date,
            artifact_id="content_completion",
            input_hash=completion_input_hash,
            payload=receipt,
            runtime_ledger=runtime_ledger,
        )
    receipt_path = _safe_path(root, f"build/daily-content/{run_id}/completion.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(receipt_path, _json_bytes(receipt))
    return receipt


__all__ = ["CONTENT_RECEIPT_SCHEMA", "MODEL_BUNDLE_SCHEMA", "DailyContentError", "produce_current_issue"]
