"""News-Grasp release reflectionのpure helper。

L8/NoPublishはこのreceiptをconsume-onlyで検証し、remote/runtime/installの証拠を
再発行しない。source-runtime変更は必要な全parity evidenceがGreenでなければRed。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SCHEMA = "NEWS_GRASP_RELEASE_REFLECTION_RECEIPT_V1"
# global L8 consumerと同じproducer identityを使う。
PRODUCER_ID = "ops-safe-commit.release-reflection.v1"
IMPACT_CLASSES = frozenset({"public-content-only", "internal-only", "source-runtime-impacting"})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_EVIDENCE_BYTES = 1024 * 1024


class ReleaseReflectionError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def classify_push_paths(paths: Iterable[str]) -> str:
    normalized = [str(path).replace("\\", "/").strip().lstrip("./") for path in paths]
    if not normalized or any(not path or path.startswith("/") for path in normalized):
        raise ReleaseReflectionError("RELEASE_IMPACT_CLASS_UNKNOWN")
    runtime_prefixes = (
        "scripts/ops/", "tools/", "config/", "schemas/", "automation/", "bin/",
        "installer/", "launcher/", "task/", "docs/harness/", ".codex/", ".claude/", ".agents/",
    )
    public_prefixes = ("docs/", "assets/", "digest/", "data/", "deploy/", ".github/workflows/")
    internal_prefixes = ("tests/", "plans/", "fixtures/", "prompts/", "ops-prompts/", "tasks/")
    classes: set[str] = set()
    for path in normalized:
        if path.startswith(runtime_prefixes) or path in {"AGENTS.md", "CLAUDE.md", "pyproject.toml", "requirements.txt"}:
            classes.add("source-runtime-impacting")
        elif path.startswith(internal_prefixes) or path == "README.md" or path == "docs/spec.md":
            classes.add("internal-only")
        elif path.startswith(public_prefixes):
            classes.add("public-content-only")
        else:
            raise ReleaseReflectionError("RELEASE_IMPACT_CLASS_UNKNOWN")
    if "source-runtime-impacting" in classes:
        return "source-runtime-impacting"
    if len(classes) == 1:
        return next(iter(classes))
    # public + internal changes remain public-only when no runtime surface is touched.
    if classes == {"public-content-only", "internal-only"}:
        return "public-content-only"
    raise ReleaseReflectionError("RELEASE_IMPACT_CLASS_UNKNOWN")


def _normalize_evidence(evidence: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, raw in evidence.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            raise ReleaseReflectionError("RELEASE_REFLECTION_EVIDENCE_INVALID")
        status = str(raw.get("status") or "")
        sha = str(raw.get("evidenceSha256") or "")
        if status == "green" and not _HEX64.fullmatch(sha):
            raise ReleaseReflectionError("RELEASE_REFLECTION_EVIDENCE_INVALID")
        if status != "green" and (status not in {"pending", "operation_deferred", "not_required_not_run"} or sha):
            raise ReleaseReflectionError("RELEASE_REFLECTION_EVIDENCE_INVALID")
        row = {"status": status, "evidenceSha256": sha}
        if raw.get("reasonCode"):
            row["reasonCode"] = str(raw["reasonCode"])
        result[name] = row
    return result


def _require_green(evidence: Mapping[str, Mapping[str, str]], names: Iterable[str]) -> None:
    if any(evidence.get(name, {}).get("status") != "green" for name in names):
        raise ReleaseReflectionError("TRUSTED_RUNTIME_REFLECTION_REQUIRED")


def create_release_reflection_receipt(
    *, impact_class: str, source_commit: str, remote_head: str, target_ref: str,
    evidence: Mapping[str, Any], producer_id: str = PRODUCER_ID,
) -> dict[str, Any]:
    if impact_class not in IMPACT_CLASSES:
        raise ReleaseReflectionError("RELEASE_IMPACT_CLASS_UNKNOWN")
    if not _HEX40.fullmatch(source_commit) or not _HEX40.fullmatch(remote_head):
        raise ReleaseReflectionError("RELEASE_REFLECTION_COMMIT_INVALID")
    if source_commit != remote_head:
        raise ReleaseReflectionError("TRUSTED_RUNTIME_REFLECTION_REF_MISMATCH")
    if target_ref != "refs/heads/main":
        raise ReleaseReflectionError("RELEASE_REFLECTION_TARGET_INVALID")
    normalized = _normalize_evidence(evidence)
    _require_green(normalized, ("remoteHeadVerified",))
    if impact_class == "public-content-only":
        _require_green(normalized, ("publicSurface",))
    elif impact_class == "source-runtime-impacting":
        _require_green(normalized, ("installed", "installedSkillsFresh", "runtimeGenerationFresh", "scheduledTaskParity"))
    body: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "producerId": producer_id,
        "producerInvocationCount": 1,
        "l8Mode": "consume-only",
        "status": "green",
        "impactClass": impact_class,
        "sourceCommit": source_commit,
        "remoteHead": remote_head,
        "targetRef": target_ref,
        "evidence": normalized,
    }
    body["receiptSha256"] = _sha(body)
    return body


def validate_release_reflection_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseReflectionError("RELEASE_REFLECTION_INVALID")
    body = dict(value)
    receipt_sha = str(body.pop("receiptSha256") or "")
    if body.get("schemaVersion") != SCHEMA or body.get("producerId") != PRODUCER_ID:
        raise ReleaseReflectionError("RELEASE_REFLECTION_INVALID")
    if body.get("producerInvocationCount") != 1:
        raise ReleaseReflectionError("RELEASE_REFLECTION_DUPLICATE_PRODUCER")
    if body.get("l8Mode") != "consume-only":
        raise ReleaseReflectionError("RELEASE_REFLECTION_L8_REISSUE_FORBIDDEN")
    if not _HEX64.fullmatch(receipt_sha) or _sha(body) != receipt_sha:
        raise ReleaseReflectionError("RELEASE_REFLECTION_INVALID")
    expected = create_release_reflection_receipt(
        impact_class=str(body.get("impactClass") or ""),
        source_commit=str(body.get("sourceCommit") or ""),
        remote_head=str(body.get("remoteHead") or ""),
        target_ref=str(body.get("targetRef") or ""),
        evidence=body.get("evidence") if isinstance(body.get("evidence"), Mapping) else {},
    )
    if dict(expected, receiptSha256=receipt_sha) != dict(value):
        raise ReleaseReflectionError("RELEASE_REFLECTION_INVALID")
    return dict(value)


def _read_evidence_file(path: Path) -> dict[str, Any]:
    candidate = Path(path)
    try:
        before = candidate.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OSError("evidence is not a regular file")
        resolved = candidate.resolve(strict=True)
        info = resolved.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise OSError("evidence is not a regular file")
        if info.st_size > MAX_EVIDENCE_BYTES:
            raise OSError("evidence is oversized")
        raw = resolved.read_bytes()
        after = resolved.lstat()
        if after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
            raise OSError("evidence changed while reading")
        value = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise ReleaseReflectionError("RELEASE_REFLECTION_EVIDENCE_INVALID") from error
    if not isinstance(value, dict):
        raise ReleaseReflectionError("RELEASE_REFLECTION_EVIDENCE_INVALID")
    return value


def _write_receipt_once(output_path: Path, value: Mapping[str, Any]) -> Path:
    output = Path(output_path)
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as error:
        raise ReleaseReflectionError("RELEASE_REFLECTION_OUTPUT_INVALID") from error
    candidate = parent / output.name
    if candidate.is_symlink():
        raise ReleaseReflectionError("RELEASE_REFLECTION_OUTPUT_INVALID")
    payload = _canonical(dict(value)) + b"\n"
    try:
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ReleaseReflectionError("RELEASE_REFLECTION_OUTPUT_EXISTS") from error
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--impact-class", required=True)
    create_parser.add_argument("--source-commit", required=True)
    create_parser.add_argument("--remote-head", required=True)
    create_parser.add_argument("--target-ref", required=True)
    create_parser.add_argument("--evidence", type=Path, required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            value = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
            result = validate_release_reflection_receipt(value)
        else:
            evidence = _read_evidence_file(args.evidence)
            result = create_release_reflection_receipt(
                impact_class=args.impact_class,
                source_commit=args.source_commit,
                remote_head=args.remote_head,
                target_ref=args.target_ref,
                evidence=evidence,
            )
            _write_receipt_once(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ReleaseReflectionError) as error:
        print(str(error) or "RELEASE_REFLECTION_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
