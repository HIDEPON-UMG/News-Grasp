#!/usr/bin/env python3
"""日次 runner gate の失敗署名と retry budget を扱う小さな契約モジュール。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_MAX_SAME_SIGNATURE_RETRIES = 1
DEFAULT_MAX_CATEGORY_FAILURES = 2
RETRY_BUDGET_POLICY_VERSION = 4
NON_RETRYABLE_PATTERNS = (
    "secret",
    "credential",
    "api key",
    "apikey",
    "token",
    "password",
    "private key",
    "pii",
    "個人情報",
    "機密",
    "認証情報",
    "秘密",
    "セキュリティ",
)


@dataclass(frozen=True)
class GateFailure:
    """runner が扱う gate 失敗の最小形。"""

    gate_id: str
    category: str
    artifact_paths: tuple[str, ...]
    output: str
    retryable: bool = True
    artifact_identity: str = ""
    next_action: str = "対象 artifact を修正して gate を再実行してください。"

    def signature(self) -> str:
        return failure_signature(
            self.gate_id,
            self.category,
            self.output,
            artifact_identity=self.artifact_identity,
        )

    def artifact_hash(self, repo_root: Path) -> str:
        return hash_artifacts(repo_root, self.artifact_paths)


@dataclass(frozen=True)
class AttemptDecision:
    """失敗後に repair worker を呼んでよいかの判定結果。"""

    retry_allowed: bool
    reason: str
    failure_signature: str
    artifact_hash: str
    same_signature_failures: int
    category_failures: int


def _normalize_output(output: str) -> str:
    text = output.lower()
    text = re.sub(r"\[[0-9]{4}-[0-9]{2}-[0-9]{2}[^]]*\]", "[timestamp]", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<num>", text)
    text = re.sub(r"[a-f0-9]{8,}", "<hex>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def failure_signature(
    gate_id: str,
    category: str,
    output: str,
    *,
    artifact_identity: str = "",
) -> str:
    """同じ gate 失敗を再認識するための安定署名を返す。"""
    payload = json.dumps(
        {
            "gate_id": gate_id,
            "category": category,
            "artifact_identity": artifact_identity,
            "output": _normalize_output(output),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def hash_artifacts(repo_root: Path, artifact_paths: tuple[str, ...]) -> str:
    """対象 artifact の内容 hash。存在しないファイルも missing として署名に含める。"""
    h = hashlib.sha256()
    for rel in sorted(artifact_paths):
        p = repo_root / rel
        h.update(rel.replace("\\", "/").encode("utf-8"))
        h.update(b"\0")
        if p.exists() and p.is_file():
            h.update(p.read_bytes())
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()[:16]


def is_retryable_output(output: str) -> bool:
    lowered = output.lower()
    return not any(pat in lowered for pat in NON_RETRYABLE_PATTERNS)


def _category_failures_for_artifact(
    signatures: dict[str, Any],
    *,
    category: str,
    artifact_hash: str,
) -> int:
    failures = 0
    for sig_state in signatures.values():
        if not isinstance(sig_state, dict):
            continue
        sig_category = sig_state.get("category") or category
        if sig_category != category:
            continue
        for seen_hash in sig_state.get("artifact_hashes", []):
            if seen_hash == artifact_hash:
                failures += 1
    return failures


def record_gate_failure(
    state: dict[str, Any],
    failure: GateFailure,
    *,
    repo_root: Path,
    max_same_signature_retries: int = DEFAULT_MAX_SAME_SIGNATURE_RETRIES,
    max_category_failures: int = DEFAULT_MAX_CATEGORY_FAILURES,
) -> AttemptDecision:
    """失敗を state に記録し、次の repair worker を許可するか返す。"""
    if state.get("retry_budget_policy_version") != RETRY_BUDGET_POLICY_VERSION:
        state["retry_budget_policy_version"] = RETRY_BUDGET_POLICY_VERSION
        state["gates"] = {}
    sig = failure.signature()
    art_hash = failure.artifact_hash(repo_root)
    gates = state.setdefault("gates", {})
    gate_state = gates.setdefault(failure.gate_id, {"signatures": {}, "categories": {}})
    signatures = gate_state.setdefault("signatures", {})
    categories = gate_state.setdefault("categories", {})

    sig_state = signatures.setdefault(sig, {"failures": 0, "artifact_hashes": [], "category": failure.category})
    sig_state.setdefault("category", failure.category)
    sig_state["failures"] = int(sig_state.get("failures", 0)) + 1
    sig_state.setdefault("artifact_hashes", []).append(art_hash)

    category_failures = _category_failures_for_artifact(
        signatures,
        category=failure.category,
        artifact_hash=art_hash,
    )
    categories[failure.category] = category_failures

    same_signature_failures = int(sig_state["failures"])
    retryable = failure.retryable and is_retryable_output(failure.output)
    unchanged_artifact = (
        len(sig_state["artifact_hashes"]) >= 2
        and sig_state["artifact_hashes"][-1] == sig_state["artifact_hashes"][-2]
    )

    retry_allowed = True
    reason = "retryable failure; targeted repair is allowed"
    if not retryable:
        retry_allowed = False
        reason = "non-retryable failure class"
    elif same_signature_failures > max_same_signature_retries:
        retry_allowed = False
        reason = "same failure_signature repeated"
    elif category_failures > max_category_failures:
        retry_allowed = False
        reason = "category attempt budget exhausted"
    elif unchanged_artifact:
        retry_allowed = False
        reason = "artifact hash did not change after prior failure"

    return AttemptDecision(
        retry_allowed=retry_allowed,
        reason=reason,
        failure_signature=sig,
        artifact_hash=art_hash,
        same_signature_failures=same_signature_failures,
        category_failures=category_failures,
    )
