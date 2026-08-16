"""News-Grasp release reflectionのpure helper。

L8/NoPublishはこのreceiptをconsume-onlyで検証し、remote/runtime/installの証拠を
再発行しない。source-runtime変更は必要な全parity evidenceがGreenでなければRed。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


SCHEMA = "NEWS_GRASP_RELEASE_REFLECTION_RECEIPT_V1"
PRODUCER_ID = "news-grasp.release-reflection.v1"
IMPACT_CLASSES = frozenset({"public-content-only", "internal-only", "source-runtime-impacting"})
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


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
    if not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", target_ref):
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[:2] != ["validate", "--receipt"]:
        print("RELEASE_REFLECTION_CLI_USAGE", file=sys.stderr)
        return 2
    try:
        value = json.loads(Path(args[2]).read_text(encoding="utf-8-sig"))
        print(json.dumps(validate_release_reflection_receipt(value), ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ReleaseReflectionError) as error:
        print(str(error) or "RELEASE_REFLECTION_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
