from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.root_fix_adversarial_review_gate import validate_adversarial_review
from tools.root_fix_goal_lineage import validate_goal_lineage


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_TEXT_SUFFIXES = {
    ".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".pyw",
    ".txt", ".ts", ".yaml", ".yml",
}

_RUNTIME_BASELINE_RAW_SHA256 = {
    ("workspace", "tools/harness/high_cost_control_v2.py"): "47e406103a8531e0a2067ce6f40d23a09115631e7fdd632df240f8f7dae0306b",
    ("workspace", "tools/harness/model_spawn_broker.py"): "cb94eecd7909ba5a750c19ca459641a5f724757057e871b7f436fa1ca154baf8",
    ("workspace", "tools/tests/test_news_grasp_control_plane_budget_v3.py"): None,
    ("workspace", "tests/test_news_grasp_pre_admission_control.py"): None,
    ("installed_broker", "tools/harness/model_spawn_broker.py"): "cb94eecd7909ba5a750c19ca459641a5f724757057e871b7f436fa1ca154baf8",
}

_PROMOTION_REPO_PATHS = frozenset(
    {
        "config/news_grasp_daily_control_routes.json",
        "scripts/ops/install-news-grasp-ops.ps1",
        "scripts/ops/news-grasp-bootstrap.ps1",
        "scripts/ops/news-grasp-deadman.ps1",
        "scripts/ops/news-grasp-lineage.ps1",
        "scripts/ops/news-grasp-runner.ps1",
        "scripts/ops/news-grasp-task-launcher.pyw",
        "scripts/ops/watch-news-grasp-runner.ps1",
        "tests/fixtures/autonomous_operations/broker_crash_probe.py",
        "tests/fixtures/autonomous_operations/production-control-impact-v1.json",
        "tests/fixtures/autonomous_operations/red-matrix-v5.md",
        "tests/fixtures/autonomous_operations/stub_model_spawn_broker.py",
        "tests/helpers/__init__.py",
        "tests/helpers/current_audit_adapter.py",
        "tests/helpers/current_broker_child_adapter.py",
        "tests/helpers/current_broker_daily_baseline_adapter.py",
        "tests/helpers/current_completion_consumer_adapter.py",
        "tests/helpers/current_hook_adapter.py",
        "tests/helpers/current_import_adapter.py",
        "tests/helpers/current_launcher_task_adapter.py",
        "tests/helpers/current_runner_adapter.py",
        "tests/helpers/current_scheduled_reentry_adapter.py",
        "tests/helpers/historical_goal_replay_adapter.py",
        "tests/helpers/red_matrix_registry.py",
        "tests/helpers/red_node_evidence.py",
        "tests/test_adversarial_implementation_escape_contract.py",
        "tests/test_audit_recovery_control.py",
        "tests/test_autonomous_operations_semantic_red.py",
        "tests/test_daily_self_heal.py",
        "tests/test_news_grasp_daily_control.py",
        "tests/test_production_exit_surface_contract.py",
        "tests/test_red_evidence_validator_contract.py",
        "tests/test_root_fix_promotion_transaction.py",
        "tests/test_runner_convergence_contract.py",
        "tests/test_scheduled_high_cost_separation.py",
        "tools/audit_recovery_control.py",
        "tools/daily_self_heal.py",
        "tools/historical_failure_scenarios.py",
        "tools/news_grasp_daily_control.py",
        "tools/news_grasp_operational_contract.py",
        "tools/root_fix_adversarial_review_gate.py",
        "tools/root_fix_goal_lineage.py",
        "tools/root_fix_promotion_control.py",
        "tools/validate_autonomous_red_evidence.py",
    }
)

_NON_PROMOTABLE_RUNTIME_PREFIXES = (
    "%SystemDrive%/ProgramData/Microsoft/Windows/Caches/",
    "ops/news-grasp-runner-state",
)


def _comparable_bytes(path: Path, value: bytes) -> bytes:
    """Git/Windowsのcheckout改行だけを正規化し、内容driftは保持する。"""
    if path.suffix.casefold() in _TEXT_SUFFIXES:
        return value.replace(b"\r\n", b"\n")
    return value


def _comparable_sha256(path: Path, value: bytes) -> str:
    return hashlib.sha256(_comparable_bytes(path, value)).hexdigest()


def _regular_contained(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return (
        root.resolve() in resolved.parents
        and path.is_file()
        and not path.is_symlink()
    )


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("PROMOTION_GIT_EVIDENCE_INVALID")
    return completed.stdout


def _isolated_delta_paths(repo: Path) -> set[str]:
    tracked = {
        line.strip()
        for line in _git(repo, "diff", "--name-only", "--relative", "HEAD", "--").decode(
            "utf-8", errors="replace"
        ).splitlines()
        if line.strip()
    }
    untracked = {
        line.strip()
        for line in _git(
            repo, "ls-files", "--others", "--exclude-standard"
        ).decode("utf-8", errors="replace").splitlines()
        if line.strip()
    }
    return {Path(item).as_posix() for item in tracked | untracked}


def _promotion_delta_paths(repo: Path) -> set[str]:
    observed = _isolated_delta_paths(repo)
    runtime_only = {
        path
        for path in observed
        if any(path.startswith(prefix) for prefix in _NON_PROMOTABLE_RUNTIME_PREFIXES)
    }
    undeclared = observed - set(_PROMOTION_REPO_PATHS) - runtime_only
    if undeclared:
        raise ValueError("PROMOTION_UNDECLARED_DELTA:" + ",".join(sorted(undeclared)))
    missing = set(_PROMOTION_REPO_PATHS) - observed
    if missing:
        raise ValueError("PROMOTION_REQUIRED_DELTA_MISSING:" + ",".join(sorted(missing)))
    return set(_PROMOTION_REPO_PATHS)


def _git_head_bytes(repo: Path, relative: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    return completed.stdout if completed.returncode == 0 else None


def _optional_regular_bytes(path: Path, root: Path) -> bytes | None:
    if not path.exists():
        return None
    if not _regular_contained(path, root):
        raise ValueError("PROMOTION_MANIFEST_INVALID")
    return path.read_bytes()


def _hash_pair(relative: Path, value: bytes | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    return hashlib.sha256(value).hexdigest(), _comparable_sha256(relative, value)


def _candidate_row(
    *,
    relative: Path,
    candidate_root: Path,
    source_isolation_root: Path,
    shared_root: Path,
) -> dict[str, str | None]:
    candidate_repo = candidate_root / "repo"
    source_repo = source_isolation_root / "repo"
    candidate = candidate_repo / relative
    if not _regular_contained(candidate, candidate_root):
        raise ValueError("PROMOTION_MANIFEST_INVALID")
    base_bytes = _git_head_bytes(candidate_repo, relative.as_posix())
    shared_bytes = _optional_regular_bytes(shared_root / relative, shared_root)
    isolation_bytes = _optional_regular_bytes(source_repo / relative, source_isolation_root)
    candidate_bytes = candidate.read_bytes()
    base_raw, base_comparable = _hash_pair(relative, base_bytes)
    shared_raw, shared_comparable = _hash_pair(relative, shared_bytes)
    isolation_raw, isolation_comparable = _hash_pair(relative, isolation_bytes)
    candidate_raw, candidate_comparable = _hash_pair(relative, candidate_bytes)
    return {
        "relativePath": relative.as_posix(),
        "baseRawSha256": base_raw,
        "baseComparableSha256": base_comparable,
        "sharedRawSha256": shared_raw,
        "sharedComparableSha256": shared_comparable,
        "sourceIsolationRawSha256": isolation_raw,
        "sourceIsolationComparableSha256": isolation_comparable,
        "candidateRawSha256": candidate_raw,
        "candidateComparableSha256": candidate_comparable,
    }


def _candidate_tree_sha256(rows: list[dict[str, Any]]) -> str:
    tree = [
        {
            "targetClass": row.get("targetClass", "repo"),
            "relativePath": row["relativePath"],
            "targetPath": row.get("targetPath"),
            "candidateRawSha256": row["candidateRawSha256"],
            "candidateComparableSha256": row["candidateComparableSha256"],
        }
        for row in sorted(
            rows,
            key=lambda item: (
                str(item.get("targetClass", "repo")),
                str(item["relativePath"]),
                str(item.get("targetPath") or ""),
            ),
        )
    ]
    return hashlib.sha256(
        json.dumps(tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _manifest_body_sha256(manifest: dict[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key != "manifestBodySha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _runtime_row(
    *,
    target_class: str,
    relative: Path,
    candidate_workspace_root: Path,
    source_workspace_root: Path,
    target: Path,
    baseline_raw_sha256: str | None,
) -> dict[str, Any]:
    candidate = candidate_workspace_root / relative
    source = source_workspace_root / relative
    if not _regular_contained(candidate, candidate_workspace_root):
        raise ValueError("PROMOTION_RUNTIME_SOURCE_INVALID")
    candidate_bytes = candidate.read_bytes()
    source_bytes = _optional_regular_bytes(source, source_workspace_root)
    if target.exists() and (not target.is_file() or target.is_symlink()):
        raise ValueError("PROMOTION_RUNTIME_TARGET_INVALID")
    target_bytes = target.read_bytes() if target.exists() else None
    source_raw, source_comparable = _hash_pair(relative, source_bytes)
    target_raw, target_comparable = _hash_pair(relative, target_bytes)
    candidate_raw, candidate_comparable = _hash_pair(relative, candidate_bytes)
    return {
        "targetClass": target_class,
        "relativePath": relative.as_posix(),
        "targetPath": str(target.resolve()),
        "baselineRawSha256": baseline_raw_sha256,
        "sourceIsolationRawSha256": source_raw,
        "sourceIsolationComparableSha256": source_comparable,
        "sharedRawSha256": target_raw,
        "sharedComparableSha256": target_comparable,
        "candidateRawSha256": candidate_raw,
        "candidateComparableSha256": candidate_comparable,
    }


def build_candidate_manifest(
    *,
    candidate_root: Path,
    source_isolation_root: Path,
    shared_root: Path,
    shared_workspace_root: Path | None = None,
    installed_broker_path: Path | None = None,
) -> dict[str, Any]:
    candidate_root = candidate_root.resolve()
    source_isolation_root = source_isolation_root.resolve()
    shared_root = shared_root.resolve()
    paths = sorted(_promotion_delta_paths(candidate_root / "repo"))
    rows = [
        _candidate_row(
            relative=Path(value),
            candidate_root=candidate_root,
            source_isolation_root=source_isolation_root,
            shared_root=shared_root,
        )
        for value in paths
    ]
    runtime_rows: list[dict[str, Any]] = []
    if shared_workspace_root is not None or installed_broker_path is not None:
        if shared_workspace_root is None or installed_broker_path is None:
            raise ValueError("PROMOTION_RUNTIME_TARGET_SET_INCOMPLETE")
        shared_workspace_root = shared_workspace_root.resolve()
        installed_broker_path = installed_broker_path.resolve()
        candidate_workspace_root = candidate_root / "workspace-harness"
        source_workspace_root = source_isolation_root / "workspace-harness"
        runtime_paths = (
            Path("tools/harness/high_cost_control_v2.py"),
            Path("tools/harness/model_spawn_broker.py"),
            Path("tools/tests/test_news_grasp_control_plane_budget_v3.py"),
            Path("tests/test_news_grasp_pre_admission_control.py"),
        )
        runtime_rows = [
            _runtime_row(
                target_class="workspace",
                relative=relative,
                candidate_workspace_root=candidate_workspace_root,
                source_workspace_root=source_workspace_root,
                target=shared_workspace_root / relative,
                baseline_raw_sha256=_RUNTIME_BASELINE_RAW_SHA256[
                    ("workspace", relative.as_posix())
                ],
            )
            for relative in runtime_paths
        ]
        runtime_rows.append(
            _runtime_row(
                target_class="installed_broker",
                relative=Path("tools/harness/model_spawn_broker.py"),
                candidate_workspace_root=candidate_workspace_root,
                source_workspace_root=source_workspace_root,
                target=installed_broker_path,
                baseline_raw_sha256=_RUNTIME_BASELINE_RAW_SHA256[
                    ("installed_broker", "tools/harness/model_spawn_broker.py")
                ],
            )
        )
    all_rows: list[dict[str, Any]] = [
        {"targetClass": "repo", **row} for row in rows
    ] + runtime_rows
    manifest: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_ROOT_FIX_PROMOTION_MANIFEST_V3",
        "candidateRoot": str(candidate_root),
        "sourceIsolationRoot": str(source_isolation_root),
        "sharedRepoRoot": str(shared_root),
        "sharedWorkspaceRoot": (
            str(shared_workspace_root) if shared_workspace_root is not None else None
        ),
        "installedBrokerPath": (
            str(installed_broker_path) if installed_broker_path is not None else None
        ),
        "candidateTreeSha256": _candidate_tree_sha256(all_rows),
        "targetRows": all_rows,
    }
    manifest["manifestBodySha256"] = _manifest_body_sha256(manifest)
    return manifest


def validate_overlap_manifest(manifest: object) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != (
        "NEWS_GRASP_ROOT_FIX_PROMOTION_MANIFEST_V3"
    ):
        return {
            "reason": "PROMOTION_MANIFEST_INVALID",
            "mutationCapability": False,
        }
    candidate_root = Path(str(manifest.get("candidateRoot") or "")).resolve()
    source_isolation_root = Path(
        str(manifest.get("sourceIsolationRoot") or "")
    ).resolve()
    shared_root = Path(str(manifest.get("sharedRepoRoot") or "")).resolve()
    rows = manifest.get("targetRows")
    if (
        candidate_root == shared_root
        or source_isolation_root == shared_root
        or not candidate_root.is_dir()
        or not source_isolation_root.is_dir()
        or not shared_root.is_dir()
        or not isinstance(rows, list)
        or not rows
    ):
        return {
            "reason": "PROMOTION_MANIFEST_INVALID",
            "mutationCapability": False,
        }
    shared_workspace_value = manifest.get("sharedWorkspaceRoot")
    installed_broker_value = manifest.get("installedBrokerPath")
    has_runtime = shared_workspace_value is not None or installed_broker_value is not None
    if has_runtime and (shared_workspace_value is None or installed_broker_value is None):
        return {"reason": "PROMOTION_RUNTIME_TARGET_SET_INCOMPLETE", "mutationCapability": False}
    if has_runtime:
        shared_workspace_root = Path(str(shared_workspace_value)).resolve()
        installed_broker_path = Path(str(installed_broker_value)).resolve()
        if (
            shared_workspace_root != shared_root.parent.resolve()
            or installed_broker_path != (Path.home() / "bin" / "ai-model-spawn-broker.py").resolve()
        ):
            return {"reason": "PROMOTION_RUNTIME_TARGET_IDENTITY_INVALID", "mutationCapability": False}
    else:
        shared_workspace_root = None
        installed_broker_path = None
    try:
        exact_delta_paths = _promotion_delta_paths(candidate_root / "repo")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "reason": "PROMOTION_GIT_EVIDENCE_INVALID",
            "mutationCapability": False,
        }
    repo_rows = [
        row for row in rows if isinstance(row, dict) and row.get("targetClass") == "repo"
    ]
    declared_paths = {
        Path(str(row.get("relativePath") or "")).as_posix()
        for row in repo_rows
    }
    if declared_paths != exact_delta_paths or len(declared_paths) != len(repo_rows):
        return {
            "reason": "PROMOTION_TARGET_SET_NOT_EXACT",
            "mutationCapability": False,
            "missingTargetRows": sorted(exact_delta_paths - declared_paths),
            "extraTargetRows": sorted(declared_paths - exact_delta_paths),
        }
    if manifest.get("manifestBodySha256") != _manifest_body_sha256(manifest):
        return {
            "reason": "PROMOTION_MANIFEST_BINDING_INVALID",
            "mutationCapability": False,
        }
    expected_runtime_keys: set[tuple[str, str, str]] = set()
    if has_runtime:
        expected_runtime_keys = {
            ("workspace", value.as_posix(), str((shared_workspace_root / value).resolve()))
            for value in (
                Path("tools/harness/high_cost_control_v2.py"),
                Path("tools/harness/model_spawn_broker.py"),
                Path("tools/tests/test_news_grasp_control_plane_budget_v3.py"),
                Path("tests/test_news_grasp_pre_admission_control.py"),
            )
        }
        expected_runtime_keys.add(
            (
                "installed_broker",
                "tools/harness/model_spawn_broker.py",
                str(installed_broker_path),
            )
        )
    declared_runtime_keys = {
        (
            str(row.get("targetClass") or ""),
            Path(str(row.get("relativePath") or "")).as_posix(),
            str(Path(str(row.get("targetPath") or "")).resolve()),
        )
        for row in rows
        if isinstance(row, dict) and row.get("targetClass") != "repo"
    }
    if declared_runtime_keys != expected_runtime_keys:
        return {"reason": "PROMOTION_RUNTIME_TARGET_SET_NOT_EXACT", "mutationCapability": False}
    evidence: list[dict[str, Any]] = []
    actual_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            return {
                "reason": "PROMOTION_MANIFEST_INVALID",
                "mutationCapability": False,
            }
        relative = Path(str(row.get("relativePath") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            return {
                "reason": "PROMOTION_MANIFEST_INVALID",
                "mutationCapability": False,
            }
        try:
            if row.get("targetClass") == "repo":
                actual = {
                    "targetClass": "repo",
                    **_candidate_row(
                        relative=relative,
                        candidate_root=candidate_root,
                        source_isolation_root=source_isolation_root,
                        shared_root=shared_root,
                    ),
                }
            else:
                actual = _runtime_row(
                    target_class=str(row.get("targetClass")),
                    relative=relative,
                    candidate_workspace_root=candidate_root / "workspace-harness",
                    source_workspace_root=source_isolation_root / "workspace-harness",
                    target=Path(str(row.get("targetPath"))),
                    baseline_raw_sha256=_RUNTIME_BASELINE_RAW_SHA256[
                        (str(row.get("targetClass")), relative.as_posix())
                    ],
                )
        except (OSError, ValueError, subprocess.SubprocessError):
            return {
                "reason": "PROMOTION_MANIFEST_INVALID",
                "mutationCapability": False,
            }
        evidence.append(actual)
        actual_rows.append(actual)
        if actual["candidateRawSha256"] != row.get("candidateRawSha256"):
            return {
                "reason": "PROMOTION_CANDIDATE_BYTES_DRIFT",
                "mutationCapability": False,
                "evidence": evidence,
            }
        if actual["sharedRawSha256"] != row.get("sharedRawSha256"):
            return {
                "reason": "FOREIGN_OVERLAP_ADMISSION_INVALID",
                "mutationCapability": False,
                "evidence": evidence,
            }
        if actual != row:
            return {
                "reason": "PROMOTION_BOUND_EVIDENCE_DRIFT",
                "mutationCapability": False,
                "evidence": evidence,
            }
        baseline = actual.get("baseComparableSha256")
        shared_matches_baseline = actual["sharedComparableSha256"] == baseline
        if row.get("targetClass") != "repo":
            baseline = actual.get("baselineRawSha256")
            shared_matches_baseline = actual["sharedRawSha256"] == baseline
        if not shared_matches_baseline:
            return {
                "reason": "FOREIGN_OVERLAP_ADMISSION_INVALID",
                "mutationCapability": False,
                "evidence": evidence,
            }
        candidate_matches_baseline = actual["candidateComparableSha256"] == baseline
        if row.get("targetClass") != "repo":
            candidate_matches_baseline = actual["candidateRawSha256"] == baseline
        if candidate_matches_baseline:
            return {
                "reason": "NO_ROOT_FIX_DELTA",
                "mutationCapability": False,
                "evidence": evidence,
            }
    candidate_tree_sha256 = _candidate_tree_sha256(actual_rows)
    if candidate_tree_sha256 != manifest.get("candidateTreeSha256"):
        return {
            "reason": "PROMOTION_CANDIDATE_TREE_BINDING_INVALID",
            "mutationCapability": False,
            "evidence": evidence,
        }
    return {
        "reason": "ROOT_FIX_OVERLAP_EVIDENCE_VALID",
        "overlapEvidenceValid": True,
        "mutationCapability": False,
        "candidateTreeSha256": candidate_tree_sha256,
        "manifestBodySha256": manifest["manifestBodySha256"],
        "evidence": evidence,
    }


def validate_promotion_bundle(
    *,
    manifest: object,
    transcript_path: Path,
    task_contract_path: Path,
    review_contract: object,
    independent_review_receipt: object,
) -> dict[str, Any]:
    overlap = validate_overlap_manifest(manifest)
    if overlap.get("overlapEvidenceValid") is not True:
        return overlap
    try:
        lineage = validate_goal_lineage(
            transcript_path=transcript_path,
            task_contract_path=task_contract_path,
            candidate_manifest=manifest,
        )
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "reason": str(error),
            "mutationCapability": False,
        }
    review = validate_adversarial_review(review_contract, manifest)
    if review.get("mutationCapability") is not True:
        return review
    return {
        "reason": "PROMOTION_TRUSTED_APPLIER_REQUIRED",
        "promotionEvidenceValid": False,
        "mutationCapability": False,
        "overlap": overlap,
        "goalLineage": lineage,
        "adversarialReview": review,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check-overlap", "validate-promotion"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--transcript", type=Path)
    parser.add_argument("--task-contract", type=Path)
    parser.add_argument("--review-contract", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = None
    if args.command == "check-overlap":
        result = validate_overlap_manifest(manifest)
    elif not args.transcript or not args.task_contract or not args.review_contract:
        result = {"reason": "PROMOTION_BUNDLE_INPUT_INVALID", "mutationCapability": False}
    else:
        try:
            review_contract = json.loads(args.review_contract.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            review_contract = None
        independent_review_path = (
            Path(str(manifest["candidateRoot"]))
            / "artifacts"
            / "independent-review"
            / f"{manifest['candidateTreeSha256']}.json"
        )
        try:
            independent_review = json.loads(
                independent_review_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            independent_review = None
        result = validate_promotion_bundle(
            manifest=manifest,
            transcript_path=args.transcript,
            task_contract_path=args.task_contract,
            review_contract=review_contract,
            independent_review_receipt=independent_review,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.command == "check-overlap":
        return 0 if result.get("overlapEvidenceValid") is True else 2
    return 0 if result.get("promotionEvidenceValid") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
