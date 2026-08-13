"""News-GraspとGlobal high-cost capabilityを結ぶproduct-local binding。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4


BINDING_SCHEMA_VERSION = "NEWS_GRASP_HIGH_COST_BINDING_V1"
DESCRIPTOR_SCHEMA_VERSION = "HIGH_COST_CAPABILITY_DESCRIPTOR_V1"
REASON_SCHEMA_VERSION = "HIGH_COST_TYPED_REASON_V1"
MAX_BINDING_BYTES = 64 * 1024
MAX_ADAPTER_BYTES = 1024 * 1024
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_ADAPTER_OUTPUT_BYTES = 1024 * 1024
REASON_EXIT_CODES = {
    "HIGH_COST_WORKSPACE_BINDING_MISSING": 72,
    "HIGH_COST_BROKER_UNAVAILABLE": 69,
    "HIGH_COST_OPERATION_ADMISSION_REQUIRED": 76,
    "HIGH_COST_AUTHORITY_INVALID": 77,
    "HIGH_COST_BUDGET_EXHAUSTED": 78,
    "HIGH_COST_IDENTITY_DRIFT": 79,
}


class HighCostBindingError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = (
            reason if reason in REASON_EXIT_CODES else "HIGH_COST_AUTHORITY_INVALID"
        )
        self.detail = detail
        super().__init__(self.reason if not detail else f"{self.reason}: {detail}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_receipt_sha256(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "bindingReceiptSha256"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_stable_file(
    path: Path, *, max_bytes: int, reason: str
) -> tuple[Path, bytes]:
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > max_bytes
        ):
            raise OSError("not a regular file")
        resolved = path.resolve(strict=True)
        with resolved.open("rb") as stream:
            opened_before = os.fstat(stream.fileno())
            value = stream.read(max_bytes + 1)
            opened_after = os.fstat(stream.fileno())
        after = resolved.stat()
    except OSError as error:
        raise HighCostBindingError(reason, "file unavailable") from error
    identities = {
        (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
        )
        for item in (before, opened_before, opened_after, after)
    }
    if len(value) > max_bytes or len(identities) != 1:
        raise HighCostBindingError("HIGH_COST_IDENTITY_DRIFT", str(path))
    return resolved, value


def classify_reason(message: str) -> str:
    upper = str(message).upper()
    for reason in (
        "HIGH_COST_WORKSPACE_BINDING_MISSING",
        "HIGH_COST_BROKER_UNAVAILABLE",
        "HIGH_COST_OPERATION_ADMISSION_REQUIRED",
        "HIGH_COST_BUDGET_EXHAUSTED",
        "HIGH_COST_IDENTITY_DRIFT",
        "HIGH_COST_AUTHORITY_INVALID",
    ):
        if reason in upper:
            return reason
    if "ADMISSION_REQUIRED" in upper:
        return "HIGH_COST_OPERATION_ADMISSION_REQUIRED"
    if "BROKER" in upper:
        return "HIGH_COST_BROKER_UNAVAILABLE"
    if "BUDGET" in upper:
        return "HIGH_COST_BUDGET_EXHAUSTED"
    if "DRIFT" in upper or "MISMATCH" in upper:
        return "HIGH_COST_IDENTITY_DRIFT"
    if "BINDING" in upper or "DESCRIPTOR" in upper:
        return "HIGH_COST_WORKSPACE_BINDING_MISSING"
    return "HIGH_COST_AUTHORITY_INVALID"


def _invoke_adapter_snapshot(
    *,
    adapter_path: Path,
    descriptor_path: Path,
    expected_adapter_sha256: str | None = None,
    expected_descriptor_sha256: str | None = None,
) -> tuple[dict[str, Any], Path, bytes, Path, bytes]:
    """実際に実行するsnapshot bytesを期待identityと同一openで照合する。"""

    adapter, adapter_bytes = _read_stable_file(
        adapter_path,
        max_bytes=MAX_ADAPTER_BYTES,
        reason="HIGH_COST_BROKER_UNAVAILABLE",
    )
    descriptor, descriptor_bytes = _read_stable_file(
        descriptor_path,
        max_bytes=MAX_DESCRIPTOR_BYTES,
        reason="HIGH_COST_WORKSPACE_BINDING_MISSING",
    )
    adapter_sha256 = _sha256_bytes(adapter_bytes)
    descriptor_sha256 = _sha256_bytes(descriptor_bytes)
    if (
        expected_adapter_sha256 is not None
        and adapter_sha256 != str(expected_adapter_sha256).lower()
    ) or (
        expected_descriptor_sha256 is not None
        and descriptor_sha256 != str(expected_descriptor_sha256).lower()
    ):
        raise HighCostBindingError(
            "HIGH_COST_IDENTITY_DRIFT", "execution snapshot hash mismatch"
        )
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    child_env = {
        key: os.environ[key]
        for key in (
            "COMSPEC",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "WINDIR",
        )
        if key in os.environ
    }
    child_env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8:backslashreplace",
        "PYTHONSAFEPATH": "1",
        "PYTHONNOUSERSITE": "1",
    })
    try:
        with tempfile.TemporaryDirectory(prefix="news-grasp-high-cost-probe-") as raw:
            snapshot_root = Path(raw)
            adapter_snapshot = snapshot_root / "high_cost_capability_adapter.py"
            descriptor_snapshot = snapshot_root / "capability-v1.json"
            adapter_snapshot.write_bytes(adapter_bytes)
            descriptor_snapshot.write_bytes(descriptor_bytes)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(adapter_snapshot),
                    "resolve",
                    "--descriptor",
                    str(descriptor_snapshot),
                ],
                cwd=str(snapshot_root),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=20,
                check=False,
                creationflags=creationflags,
                env=child_env,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise HighCostBindingError(
            "HIGH_COST_BROKER_UNAVAILABLE", "adapter execution failed"
        ) from error
    _, adapter_after = _read_stable_file(
        adapter,
        max_bytes=MAX_ADAPTER_BYTES,
        reason="HIGH_COST_BROKER_UNAVAILABLE",
    )
    _, descriptor_after = _read_stable_file(
        descriptor,
        max_bytes=MAX_DESCRIPTOR_BYTES,
        reason="HIGH_COST_WORKSPACE_BINDING_MISSING",
    )
    if adapter_after != adapter_bytes or descriptor_after != descriptor_bytes:
        raise HighCostBindingError(
            "HIGH_COST_IDENTITY_DRIFT", "bound file changed during probe"
        )
    raw_combined = (result.stdout or b"") + (result.stderr or b"")
    if len(raw_combined) > MAX_ADAPTER_OUTPUT_BYTES:
        raise HighCostBindingError(
            "HIGH_COST_BROKER_UNAVAILABLE", "adapter output too large"
        )
    combined = ""
    for encoding in ("utf-8", "cp932"):
        try:
            combined = raw_combined.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not combined:
        combined = raw_combined.decode("utf-8", errors="backslashreplace")
    candidates = [line.strip() for line in combined.splitlines() if line.strip()]
    payload: dict[str, Any] | None = None
    for line in reversed(candidates):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if result.returncode != 0 or payload is None:
        raise HighCostBindingError(classify_reason(combined), "adapter resolve failed")
    if payload.get("status") != "available":
        raise HighCostBindingError(
            classify_reason(str(payload.get("reason") or payload)),
            "adapter unavailable",
        )
    if (
        payload.get("schemaVersion") != DESCRIPTOR_SCHEMA_VERSION
        or payload.get("reasonSchemaVersion") != REASON_SCHEMA_VERSION
        or not isinstance(payload.get("generation"), int)
        or int(payload["generation"]) < 1
    ):
        raise HighCostBindingError(
            "HIGH_COST_WORKSPACE_BINDING_MISSING", "adapter schema invalid"
        )
    expected_adapter = (
        Path(str(payload.get("workspaceRoot") or ""))
        / "tools"
        / "harness"
        / "high_cost_capability_adapter.py"
    )
    try:
        if adapter != expected_adapter.resolve(strict=True):
            raise HighCostBindingError(
                "HIGH_COST_IDENTITY_DRIFT", "adapter/workspace mismatch"
            )
    except OSError as error:
        raise HighCostBindingError(
            "HIGH_COST_WORKSPACE_BINDING_MISSING", "adapter workspace invalid"
        ) from error
    payload["descriptorPath"] = str(descriptor)
    return payload, adapter, adapter_bytes, descriptor, descriptor_bytes


def _invoke_adapter(*, adapter_path: Path, descriptor_path: Path) -> dict[str, Any]:
    """互換用pure probe。実行snapshotの完全性はcore関数が保証する。"""

    payload, _, _, _, _ = _invoke_adapter_snapshot(
        adapter_path=adapter_path,
        descriptor_path=descriptor_path,
    )
    return payload


def _read_binding(binding_path: Path) -> tuple[Path, dict[str, Any]]:
    path, raw = _read_stable_file(
        binding_path,
        max_bytes=MAX_BINDING_BYTES,
        reason="HIGH_COST_WORKSPACE_BINDING_MISSING",
    )
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise HighCostBindingError(
            "HIGH_COST_WORKSPACE_BINDING_MISSING", "binding JSON invalid"
        ) from error
    if not isinstance(value, dict) or value.get("schemaVersion") != BINDING_SCHEMA_VERSION:
        raise HighCostBindingError(
            "HIGH_COST_WORKSPACE_BINDING_MISSING", "binding schema invalid"
        )
    return path, value


def create_binding(
    *, adapter_path: Path, descriptor_path: Path, output_path: Path
) -> dict[str, Any]:
    resolved, adapter, adapter_bytes, descriptor, descriptor_bytes = (
        _invoke_adapter_snapshot(
            adapter_path=adapter_path,
            descriptor_path=descriptor_path,
        )
    )
    value: dict[str, Any] = {
        "schemaVersion": BINDING_SCHEMA_VERSION,
        "contractVersion": DESCRIPTOR_SCHEMA_VERSION,
        "reasonSchemaVersion": REASON_SCHEMA_VERSION,
        "descriptorPath": str(descriptor),
        "descriptorSha256": _sha256_bytes(descriptor_bytes),
        "descriptorGeneration": int(resolved["generation"]),
        "adapterPath": str(adapter),
        "adapterSha256": _sha256_bytes(adapter_bytes),
        "workspaceRoot": str(Path(str(resolved["workspaceRoot"])).resolve(strict=True)),
    }
    value["bindingReceiptSha256"] = _canonical_receipt_sha256(value)
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return value


def resolve_binding(
    *, binding_path: Path, expected_receipt_sha256: str
) -> dict[str, Any]:
    path, value = _read_binding(binding_path)
    expected = str(expected_receipt_sha256 or "").lower()
    embedded = str(value.get("bindingReceiptSha256") or "").lower()
    computed = _canonical_receipt_sha256(value)
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected) is None
        or embedded != expected
        or computed != expected
    ):
        raise HighCostBindingError(
            "HIGH_COST_IDENTITY_DRIFT", "binding receipt mismatch"
        )
    if (
        value.get("contractVersion") != DESCRIPTOR_SCHEMA_VERSION
        or value.get("reasonSchemaVersion") != REASON_SCHEMA_VERSION
    ):
        raise HighCostBindingError(
            "HIGH_COST_WORKSPACE_BINDING_MISSING", "binding contract invalid"
        )
    resolved, adapter, _, descriptor, _ = _invoke_adapter_snapshot(
        adapter_path=Path(str(value.get("adapterPath") or "")),
        descriptor_path=Path(str(value.get("descriptorPath") or "")),
        expected_adapter_sha256=str(value.get("adapterSha256") or ""),
        expected_descriptor_sha256=str(value.get("descriptorSha256") or ""),
    )
    if (
        int(resolved["generation"]) != int(value.get("descriptorGeneration", -1))
        or str(Path(str(resolved["workspaceRoot"])).resolve(strict=True))
        != str(value.get("workspaceRoot") or "")
        or str(Path(str(resolved["descriptorPath"])).resolve(strict=True))
        != str(descriptor)
    ):
        raise HighCostBindingError(
            "HIGH_COST_IDENTITY_DRIFT", "resolved identity mismatch"
        )
    return {
        **resolved,
        "bindingPath": str(path),
        "bindingReceiptSha256": expected,
        "bindingSchemaVersion": BINDING_SCHEMA_VERSION,
        "adapterPath": str(adapter),
        "adapterSha256": value["adapterSha256"],
        "descriptorSha256": value["descriptorSha256"],
    }


def resolve_binding_from_environment() -> dict[str, Any]:
    path = os.environ.get("NEWS_GRASP_HIGH_COST_BINDING_PATH", "").strip()
    receipt = os.environ.get(
        "NEWS_GRASP_HIGH_COST_BINDING_RECEIPT_SHA256", ""
    ).strip()
    if not path or not receipt:
        raise HighCostBindingError("HIGH_COST_WORKSPACE_BINDING_MISSING")
    return resolve_binding(
        binding_path=Path(path), expected_receipt_sha256=receipt
    )


def _failure_payload(error: HighCostBindingError) -> dict[str, Any]:
    return {
        "schemaVersion": REASON_SCHEMA_VERSION,
        "status": "unavailable",
        "reason": error.reason,
        "detail": "redacted" if error.detail else "",
        "highCostAction": "defer",
        "localSafeAllowed": True,
        "taskTerminal": False,
        "exitCode": REASON_EXIT_CODES[error.reason],
    }


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp high-cost binding")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--adapter", type=Path, required=True)
    create_parser.add_argument("--descriptor", type=Path, required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    for name in ("resolve", "probe"):
        resolve_parser = subparsers.add_parser(name)
        resolve_parser.add_argument("--binding", type=Path, required=True)
        resolve_parser.add_argument("--expected-receipt-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_binding(
                adapter_path=args.adapter,
                descriptor_path=args.descriptor,
                output_path=args.output,
            )
        else:
            result = resolve_binding(
                binding_path=args.binding,
                expected_receipt_sha256=args.expected_receipt_sha256,
            )
    except HighCostBindingError as error:
        failure = _failure_payload(error)
        print(json.dumps(failure, ensure_ascii=True, sort_keys=True))
        return int(failure["exitCode"])
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
