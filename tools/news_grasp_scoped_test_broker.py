"""Daily scoped contract testの署名済みpromotion/changed-source broker。

通常の定期実行は、Release gateが発行した同一HEADのpromotion receiptを検証する
だけでpytestを起動しない。promotion後にsource HEADが変わった場合だけ、署名済み
registryに登録された変更path対応testを一回実行する。未知pathはfail-closedにする。
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROMOTION_SCHEMA = "NEWS_GRASP_DAILY_SCOPED_PROMOTION_RECEIPT_V1"
SCOPED_SCHEMA = "NEWS_GRASP_DAILY_SCOPED_TEST_RECEIPT_V1"
RELEASE_SCHEMA = "NEWS_GRASP_RELEASE_PARTITION_RECEIPT_V1"
FIXED_PYTHON = r"C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# 値はpytest node idとしてそのまま渡せる固定selectorである。registry自体を
# promotion receiptへ署名し、変更後sourceが自分で許可範囲を広げられないようにする。
SCOPED_PATH_TEST_REGISTRY: dict[str, tuple[str, ...]] = {
    "config/news_grasp_daily_45m_contract_v1.json": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_daily_gate.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_daily_launcher.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_gate_profiles.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_direct_runtime.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_direct_completion.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_publish_contract.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/publish_inventory.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/deepdive_quality.py": (
        "tests/test_news_grasp_daily_45m_contract.py",
    ),
    "tools/news_grasp_daily_external.py": (
        "tests/test_news_grasp_daily_external_v1.py",
    ),
}

# Release-only source/testをDaily pytest processへ渡す経路は、署名済み旧registry
# に残っていても拒否する。これらは新しいRelease promotionが完了するまで
# scheduled pathから到達不能でなければならない。
_DAILY_FORBIDDEN_CHANGED_PATHS = frozenset(
    {
        "tools/news_grasp_release_gate.py",
        "tools/news_grasp_scoped_test_broker.py",
        "tools/sync_news_grasp_codex_automation.py",
    }
)
_DAILY_FORBIDDEN_TEST_PATH_PARTS = (
    "release_gate",
    "historical_failure",
    "tests/test_playwright",
    "nopublish",
    "crash_replay",
)
_DAILY_FORBIDDEN_IMPORT_PREFIXES = (
    "tools.news_grasp_release_gate",
    "tools.historical_failure_scenarios",
    "playwright",
)


def _import_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = str(node.module or "")
        return tuple(
            f"{module}.{alias.name}" if module else alias.name
            for alias in node.names
        )
    return ()


def _nested_process_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in {
            ("subprocess", "run"),
            ("subprocess", "Popen"),
            ("subprocess", "call"),
            ("subprocess", "check_call"),
            ("subprocess", "check_output"),
            ("os", "system"),
            ("os", "popen"),
            ("pytest", "main"),
            ("runpy", "run_module"),
            ("runpy", "run_path"),
        }
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        inner = func.value
        if (
            isinstance(inner.func, ast.Name)
            and inner.func.id == "__import__"
            and inner.args
            and isinstance(inner.args[0], ast.Constant)
            and inner.args[0].value == "subprocess"
        ):
            return True
    return False


def _validate_daily_test_closure(repo_root: Path, test_nodes: Sequence[str]) -> str | None:
    """選択testがRelease import/nested processへ推移しないことをspawn前に証明する。"""

    for test_node in test_nodes:
        relative = str(test_node).split("::", 1)[0].replace("\\", "/")
        if (
            not relative.startswith("tests/")
            or ".." in Path(relative).parts
            or any(part in relative.casefold() for part in _DAILY_FORBIDDEN_TEST_PATH_PARTS)
        ):
            return "scoped_test_node_release_only_forbidden"
        path = (repo_root / relative).resolve(strict=False)
        try:
            path.relative_to(repo_root)
        except ValueError:
            return "scoped_test_node_outside_repo"
        if not path.is_file():
            return "scoped_test_node_missing"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        except (OSError, UnicodeError, SyntaxError):
            return "scoped_test_node_uninspectable"
        for node in ast.walk(tree):
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for imported in _import_names(node)
                for prefix in _DAILY_FORBIDDEN_IMPORT_PREFIXES
            ):
                return "scoped_test_transitive_release_import_forbidden"
            if isinstance(node, ast.Call) and _nested_process_call(node):
                return "scoped_test_nested_process_forbidden"
    return None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(JST).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(JST)


def _promotion_paths(state_root: str | Path) -> tuple[Path, Path]:
    root = Path(os.path.abspath(os.fspath(state_root)))
    return root / "promotion" / "daily-scoped-promotion.json", root / "promotion" / "daily-scoped-promotion.key"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _git(repo_root: Path, args: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return 255, ""
    return completed.returncode, completed.stdout.strip()


def _head_and_tree(repo_root: Path) -> tuple[str, str]:
    head_rc, head = _git(repo_root, ("rev-parse", "--verify", "HEAD"))
    tree_rc, tree = _git(repo_root, ("rev-parse", "--verify", "HEAD^{tree}"))
    if head_rc or tree_rc or not _HEX40.fullmatch(head) or not _HEX40.fullmatch(tree):
        raise ValueError("scoped_promotion_git_identity_invalid")
    return head, tree


def _signed(unsigned: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, _json_bytes(dict(unsigned)), hashlib.sha256).hexdigest()


def _issue_authorized_promotion(
    *,
    repo_root: str | Path,
    state_root: str | Path,
    release_event: Mapping[str, Any],
    now: datetime | None = None,
    validity_days: int = 8,
) -> dict[str, Any]:
    """同一processのRelease gateが確定したGreenだけをpromotionへ署名する。"""

    # Public mappingを信頼せず、Release gate所有のcanonical hash-chainに実在する
    # completed eventとそのGreen receiptをissuer自身でも再検証する。
    from tools import news_grasp_release_gate as release_gate

    canonical_state = release_gate._canonical_daily_state_root()
    if Path(state_root).resolve() != canonical_state:
        raise ValueError("scoped_promotion_state_root_noncanonical")
    release_event_hash = str(release_event.get("event_hash") or "")
    canonical_event = next(
        (
            event
            for event in release_gate._ledger_events(release_gate._canonical_ledger_path())
            if event.get("event_hash") == release_event_hash
        ),
        None,
    )
    release_receipt = release_event.get("receipt")
    if (
        not _HEX64.fullmatch(release_event_hash)
        or not isinstance(canonical_event, Mapping)
        or release_gate._mapping_hash(canonical_event) != release_gate._mapping_hash(release_event)
        or release_event.get("event_type") != "release_completed"
        or not isinstance(release_receipt, Mapping)
        or release_receipt.get("schemaVersion") != RELEASE_SCHEMA
        or release_receipt.get("ok") is not True
        or release_receipt.get("status") != "green"
        or list(release_receipt.get("failed_nodes") or ())
        or int(release_receipt.get("executed_node_count") or 0)
        != int(release_receipt.get("union_node_count") or -1)
        or int(release_receipt.get("union_node_count") or 0) <= 0
        or release_event.get("receipt_hash") != release_gate._mapping_hash(release_receipt)
    ):
        raise ValueError("scoped_promotion_release_binding_invalid")
    try:
        release_gate._validate_release_completion_chain(
            release_gate._canonical_ledger_path(),
            release_receipt,
        )
    except release_gate.NewsGraspReleaseGateError as exc:
        raise ValueError("scoped_promotion_release_evidence_chain_invalid") from exc
    release_id = str(release_receipt.get("release_id") or "")
    release_receipt_sha256 = release_gate._mapping_hash(release_receipt)
    if not release_id or not _HEX64.fullmatch(release_receipt_sha256):
        raise ValueError("scoped_promotion_release_binding_invalid")
    root = Path(repo_root).resolve(strict=True)
    head, tree = _head_and_tree(root)
    issued = (now or datetime.now(JST)).astimezone(JST)
    if validity_days < 1 or validity_days > 31:
        raise ValueError("scoped_promotion_validity_invalid")
    receipt_path, key_path = _promotion_paths(state_root)
    key = key_path.read_bytes() if key_path.is_file() else secrets.token_bytes(32)
    if len(key) != 32:
        raise ValueError("scoped_promotion_key_invalid")
    unsigned = {
        "schemaVersion": PROMOTION_SCHEMA,
        "status": "trusted",
        "source_head": head,
        "source_tree": tree,
        "release_id": release_id,
        "release_receipt_sha256": release_receipt_sha256,
        "release_event_hash": release_event_hash,
        "path_test_registry": {key: list(value) for key, value in SCOPED_PATH_TEST_REGISTRY.items()},
        "registry_sha256": _sha({key: list(value) for key, value in SCOPED_PATH_TEST_REGISTRY.items()}),
        "issued_at": _iso(issued),
        "expires_at": _iso(issued + timedelta(days=validity_days)),
        "nonce": secrets.token_hex(16),
    }
    receipt = {**unsigned, "signature": _signed(unsigned, key)}
    if not key_path.is_file():
        _atomic_write(key_path, key)
    _atomic_write(receipt_path, _json_bytes(receipt) + b"\n")
    return {**receipt, "receipt_path": str(receipt_path)}


def _load_trusted_promotion(*, state_root: str | Path, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    receipt_path, key_path = _promotion_paths(state_root)
    try:
        raw = receipt_path.read_bytes()
        key = key_path.read_bytes()
        loaded = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "scoped_promotion_receipt_missing_or_invalid"
    if not isinstance(loaded, Mapping) or len(key) != 32:
        return None, "scoped_promotion_receipt_missing_or_invalid"
    receipt = dict(loaded)
    signature = str(receipt.pop("signature", "")).casefold()
    if receipt.get("schemaVersion") != PROMOTION_SCHEMA or receipt.get("status") != "trusted":
        return None, "scoped_promotion_receipt_schema_invalid"
    if not _HEX64.fullmatch(signature) or not hmac.compare_digest(signature, _signed(receipt, key)):
        return None, "scoped_promotion_signature_invalid"
    expires = _parse_time(receipt.get("expires_at"))
    if expires is None or now.astimezone(JST) > expires:
        return None, "scoped_promotion_receipt_expired"
    registry = receipt.get("path_test_registry")
    if not isinstance(registry, Mapping) or receipt.get("registry_sha256") != _sha(dict(registry)):
        return None, "scoped_promotion_registry_invalid"
    canonical_registry = {key: list(value) for key, value in SCOPED_PATH_TEST_REGISTRY.items()}
    if dict(registry) != canonical_registry or receipt.get("registry_sha256") != _sha(canonical_registry):
        return None, "scoped_promotion_registry_generation_mismatch"
    receipt["signature"] = signature
    return receipt, None


def _run_fixed_tests(repo_root: Path, test_nodes: Sequence[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [FIXED_PYTHON, "-m", "pytest", "-q", *test_nodes],
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15 * 60,
        check=False,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "return_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
    }


def evaluate_scoped_contract(
    *,
    repo_root: str | Path,
    state_root: str | Path,
    now: datetime | None = None,
    runner: Any = None,
) -> dict[str, Any]:
    """同一HEADならpromotion receiptを再利用し、変更時だけ登録testを一回実行する。"""

    observed_at = (now or datetime.now(JST)).astimezone(JST)
    promotion, failure = _load_trusted_promotion(state_root=state_root, now=observed_at)
    if promotion is None:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": [failure]}
    root = Path(repo_root).resolve(strict=True)
    try:
        current_head, current_tree = _head_and_tree(root)
    except ValueError as exc:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": [str(exc)]}
    status_rc, worktree_status = _git(root, ("status", "--porcelain=v1", "--untracked-files=all"))
    if status_rc != 0 or worktree_status:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_source_worktree_not_clean"]}
    baseline = str(promotion.get("source_head") or "")
    if current_head == baseline:
        if current_tree != promotion.get("source_tree"):
            return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_source_tree_drift"]}
        result = {
            "schemaVersion": SCOPED_SCHEMA,
            "ok": True,
            "status": "verified",
            "mode": "promotion_reuse",
            "source_head": current_head,
            "source_tree": current_tree,
            "changed_paths": [],
            "test_nodes": [],
            "test_process_count": 0,
            "promotion_signature": promotion["signature"],
            "observed_at": _iso(observed_at),
            "failures": [],
        }
        return {**result, "receipt_sha256": _sha(result)}
    ancestor_rc, _ = _git(root, ("merge-base", "--is-ancestor", baseline, current_head))
    if ancestor_rc != 0:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_promotion_head_not_ancestor"]}
    diff_rc, changed_output = _git(root, ("diff", "--name-only", "--diff-filter=ACMRT", f"{baseline}..{current_head}"))
    if diff_rc != 0:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_changed_paths_unavailable"]}
    changed_paths = [line.strip().replace("\\", "/") for line in changed_output.splitlines() if line.strip()]
    if any(path in _DAILY_FORBIDDEN_CHANGED_PATHS for path in changed_paths):
        return {
            "schemaVersion": SCOPED_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": ["scoped_changed_path_release_only_forbidden"],
            "changed_paths": changed_paths,
            "test_nodes": [],
            "test_process_count": 0,
        }
    signed_registry = promotion.get("path_test_registry")
    assert isinstance(signed_registry, Mapping)
    unknown = sorted(path for path in changed_paths if path not in signed_registry)
    if unknown or not changed_paths:
        return {
            "schemaVersion": SCOPED_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": ["scoped_changed_path_unregistered" if unknown else "scoped_changed_paths_empty"],
            "unknown_paths": unknown,
        }
    test_nodes = sorted({str(node) for path in changed_paths for node in signed_registry[path]})
    if not test_nodes or any(not node.startswith("tests/") for node in test_nodes):
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_test_node_invalid"]}
    closure_failure = _validate_daily_test_closure(root, test_nodes)
    if closure_failure:
        return {
            "schemaVersion": SCOPED_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": [closure_failure],
            "changed_paths": changed_paths,
            "test_nodes": test_nodes,
            "test_process_count": 0,
        }
    executor = runner or _run_fixed_tests
    process = executor(root, test_nodes)
    if not isinstance(process, Mapping) or int(process.get("return_code", -1)) != 0:
        return {
            "schemaVersion": SCOPED_SCHEMA,
            "ok": False,
            "status": "red",
            "failures": ["scoped_changed_tests_red"],
            "changed_paths": changed_paths,
            "test_nodes": test_nodes,
            "process": dict(process) if isinstance(process, Mapping) else {},
        }
    after_head, after_tree = _head_and_tree(root)
    after_status_rc, after_status = _git(root, ("status", "--porcelain=v1", "--untracked-files=all"))
    if after_status_rc or after_status or after_head != current_head or after_tree != current_tree:
        return {"schemaVersion": SCOPED_SCHEMA, "ok": False, "status": "red", "failures": ["scoped_source_drift_during_test"]}
    result = {
        "schemaVersion": SCOPED_SCHEMA,
        "ok": True,
        "status": "verified",
        "mode": "changed_source",
        "baseline_head": baseline,
        "source_head": current_head,
        "source_tree": current_tree,
        "changed_paths": changed_paths,
        "test_nodes": test_nodes,
        "test_process_count": 1,
        "process": dict(process),
        "promotion_signature": promotion["signature"],
        "observed_at": _iso(observed_at),
        "failures": [],
    }
    return {**result, "receipt_sha256": _sha(result)}
