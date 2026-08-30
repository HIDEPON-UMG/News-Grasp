from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping

from tools.historical_failure_scenarios import (
    historical_failure_scenarios,
    weekly_failure_regression_cases,
)
from tools.repair_coverage_matrix import COVERAGE_ROWS, CoverageRow, RepairClass
from tools.repair_registry import REGISTRY, RepairHandler
from tools.validate_daily_quality import daily_quality_issue_code


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATHS: tuple[str, ...] = (
    "docs/spec.md",
    "tools/validate_daily_quality.py",
    "tools/validate_generation_quality.py",
    "tools/validate_digest_articles_reconcile.py",
    "tools/repair_coverage_matrix.py",
    "tools/repair_registry.py",
    "tools/auto_repair_orchestrator.py",
    "tools/repair_runtime_e2e.py",
    "tools/repair_system_completeness.py",
    "tools/historical_failure_scenarios.py",
    "tools/news_grasp_direct_runtime.py",
    "automation/news-grasp-6-40/completion_guard.py",
    "automation/skills/news-grasp-direct-mainline/SKILL.md",
    "tests/test_repair_system_completeness.py",
    "tests/test_repair_matrix_validator_sync.py",
    "tests/test_repair_registry.py",
    "tests/test_auto_repair_orchestrator.py",
    "tests/test_news_grasp_direct_runtime.py",
    "tests/test_historical_failure_scenarios.py",
)


@dataclass(frozen=True)
class RepairCompletenessFinding:
    code: str
    detail: str


@dataclass(frozen=True)
class RepairCompletenessReport:
    ok: bool
    findings: tuple[RepairCompletenessFinding, ...]
    coverage_row_count: int
    registry_handler_count: int
    validator_issue_count: int
    deterministic_row_count: int
    historical_scenario_count: int
    weekly_regression_count: int
    source_observations: dict[str, dict[str, int | str]]

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "findings": [asdict(finding) for finding in self.findings],
        }


def _string_constants(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def extract_generation_quality_codes(source: str) -> set[str]:
    """`_error(code, ...)` の code 候補を source から列挙する。"""
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_error":
            continue
        codes.update(_string_constants(node.args[0]))
    return codes


def extract_daily_quality_codes(source: str) -> set[str]:
    tree = ast.parse(source)
    mapper = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == daily_quality_issue_code.__name__
    )
    return {
        node.value.value
        for node in ast.walk(mapper)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value != "unknown"
    }


def extract_structured_issue_codes(source: str) -> set[str]:
    """structured issue dict の `issue_code` literal を列挙する。"""
    tree = ast.parse(source)
    codes: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "issue_code"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                codes.add(value.value)
    return codes


def _annotation_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def extract_registry_repair_entrypoints(source: str) -> set[str]:
    """RepairContext 1引数・RepairResult 戻り値の registry entrypoint を列挙する。"""
    tree = ast.parse(source)
    entrypoints: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_repair_") or len(node.args.args) != 1:
            continue
        argument = node.args.args[0]
        if _annotation_name(argument.annotation) != "RepairContext":
            continue
        if _annotation_name(node.returns) != "RepairResult":
            continue
        entrypoints.add(node.name)
    return entrypoints


def _read_source(relative_path: str, *, repo_root: Path = REPO_ROOT) -> str:
    return (repo_root / relative_path).read_text(encoding="utf-8-sig")


def _validator_codes(*, repo_root: Path = REPO_ROOT) -> dict[str, frozenset[str]]:
    return {
        "daily-quality": frozenset(
            extract_daily_quality_codes(
                _read_source("tools/validate_daily_quality.py", repo_root=repo_root)
            )
        ),
        "generation-quality": frozenset(
            extract_generation_quality_codes(
                _read_source("tools/validate_generation_quality.py", repo_root=repo_root)
            )
        ),
        "digest-articles-reconcile": frozenset(
            extract_structured_issue_codes(
                _read_source(
                    "tools/validate_digest_articles_reconcile.py",
                    repo_root=repo_root,
                )
            )
        ),
    }


REPAIR_VALIDATOR_CODESETS: dict[str, frozenset[str]] = _validator_codes()


def _source_observations(*, repo_root: Path = REPO_ROOT) -> dict[str, dict[str, int | str]]:
    observations: dict[str, dict[str, int | str]] = {}
    for relative_path in SOURCE_PATHS:
        path = repo_root / relative_path
        stat = path.stat()
        observations[relative_path] = {
            "status": "present",
            "bytes": stat.st_size,
        }
    return observations


def _normalized_pattern(pattern: str) -> str:
    return pattern.replace("\\", "/").strip().lstrip("./").rstrip("/")


def _pattern_supported(pattern: str, handler_patterns: tuple[str, ...]) -> bool:
    expected = _normalized_pattern(pattern)
    for candidate in handler_patterns:
        supported = _normalized_pattern(candidate)
        if expected == supported:
            return True
        if supported and expected.startswith(supported + "/"):
            return True
        if expected and supported.startswith(expected + "/"):
            return True
    return False


def _supported_verify_gates(handler: RepairHandler) -> tuple[str, ...]:
    return handler.supported_verify_gates or (handler.verify_gate,)


def audit_repair_system(
    *,
    rows: Iterable[CoverageRow] = COVERAGE_ROWS,
    registry: Mapping[str, RepairHandler] = REGISTRY,
    validator_codes: Mapping[str, set[str] | frozenset[str]] | None = None,
    repo_root: Path = REPO_ROOT,
) -> RepairCompletenessReport:
    row_list = tuple(rows)
    declared = dict(validator_codes or _validator_codes(repo_root=repo_root))
    findings: list[RepairCompletenessFinding] = []

    keys = [(row.gate_id, row.issue_code) for row in row_list]
    for key, count in sorted(Counter(keys).items()):
        if count > 1:
            findings.append(
                RepairCompletenessFinding(
                    "duplicate_coverage_row",
                    f"{key[0]}:{key[1]} count={count}",
                )
            )

    row_map = {(row.gate_id, row.issue_code): row for row in row_list}
    for gate_id, codes in sorted(declared.items()):
        for issue_code in sorted(codes):
            if (gate_id, issue_code) not in row_map:
                findings.append(
                    RepairCompletenessFinding(
                        "validator_issue_missing_coverage",
                        f"{gate_id}:{issue_code}",
                    )
                )

    for row in row_list:
        key = f"{row.gate_id}:{row.issue_code}"
        if not row.status_on_failure:
            findings.append(RepairCompletenessFinding("failure_status_missing", key))
        if row.status_on_failure in {"noop", "not_applicable"}:
            findings.append(
                RepairCompletenessFinding(
                    "failure_status_false_success",
                    f"{key} status={row.status_on_failure}",
                )
            )
        if row.repair_class == RepairClass.HANDLER_UNIMPLEMENTED_RED:
            findings.append(RepairCompletenessFinding("handler_unimplemented_row", key))

    unknown = row_map.get(("any", "unknown"))
    if (
        unknown is None
        or unknown.repair_class != RepairClass.TYPED_FATAL
        or unknown.handler_id
        or unknown.status_on_failure != "blocked_unknown_repair_class"
    ):
        findings.append(
            RepairCompletenessFinding(
                "unknown_route_not_fail_closed",
                "any:unknown must be typed_fatal without handler",
            )
        )

    deterministic_rows = tuple(
        row for row in row_list if row.repair_class == RepairClass.DETERMINISTIC_HANDLER
    )
    referenced_handlers = {row.handler_id for row in deterministic_rows if row.handler_id}
    for row in deterministic_rows:
        key = f"{row.gate_id}:{row.issue_code}"
        handler = registry.get(row.handler_id)
        if handler is None:
            findings.append(
                RepairCompletenessFinding(
                    "deterministic_handler_missing",
                    f"{key} handler={row.handler_id}",
                )
            )
            continue
        if handler.kind != "deterministic":
            findings.append(
                RepairCompletenessFinding(
                    "deterministic_handler_kind_mismatch",
                    f"{key} handler={row.handler_id} kind={handler.kind}",
                )
            )
        if handler.repair.__name__ == "_blocked_ambiguous":
            findings.append(
                RepairCompletenessFinding(
                    "deterministic_handler_ambiguous",
                    f"{key} handler={row.handler_id}",
                )
            )
        verify_gate = row.verify_gate or row.gate_id
        supported_gates = _supported_verify_gates(handler)
        if verify_gate not in supported_gates:
            findings.append(
                RepairCompletenessFinding(
                    "handler_verify_gate_mismatch",
                    (
                        f"{key} handler={row.handler_id} verify_gate={verify_gate} "
                        f"supported={','.join(supported_gates)}"
                    ),
                )
            )
        unsupported_patterns = [
            pattern
            for pattern in row.allowed_artifacts
            if not _pattern_supported(pattern, handler.allowed_artifacts)
        ]
        if unsupported_patterns:
            findings.append(
                RepairCompletenessFinding(
                    "handler_artifact_scope_mismatch",
                    (
                        f"{key} handler={row.handler_id} unsupported="
                        + ",".join(unsupported_patterns)
                    ),
                )
            )

    for handler_id, handler in sorted(registry.items()):
        if handler_id != handler.handler_id:
            findings.append(
                RepairCompletenessFinding(
                    "registry_handler_identity_mismatch",
                    f"key={handler_id} handler_id={handler.handler_id}",
                )
            )
        if handler_id not in referenced_handlers:
            findings.append(
                RepairCompletenessFinding(
                    "registry_handler_unreachable",
                    handler_id,
                )
            )
        if handler.repair.__name__ == "_blocked_ambiguous":
            findings.append(
                RepairCompletenessFinding(
                    "deterministic_handler_ambiguous",
                    handler_id,
                )
            )

    registry_source = _read_source("tools/repair_registry.py", repo_root=repo_root)
    declared_entrypoints = extract_registry_repair_entrypoints(registry_source)
    registered_entrypoints = {handler.repair.__name__ for handler in registry.values()}
    for entrypoint in sorted(declared_entrypoints - registered_entrypoints):
        findings.append(
            RepairCompletenessFinding(
                "orphan_repair_implementation",
                entrypoint,
            )
        )

    scenarios = historical_failure_scenarios()
    weekly_cases = weekly_failure_regression_cases()
    source_observations = _source_observations(repo_root=repo_root)
    return RepairCompletenessReport(
        ok=not findings,
        findings=tuple(findings),
        coverage_row_count=len(row_list),
        registry_handler_count=len(registry),
        validator_issue_count=sum(len(codes) for codes in declared.values()),
        deterministic_row_count=len(deterministic_rows),
        historical_scenario_count=len(scenarios),
        weekly_regression_count=len(weekly_cases),
        source_observations=source_observations,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="News-Grasp repair system closed-world completeness audit."
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-proof", type=Path)
    args = parser.parse_args(argv)

    report = audit_repair_system()
    payload = report.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.write_proof:
        args.write_proof.parent.mkdir(parents=True, exist_ok=True)
        args.write_proof.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    if args.json or not args.write_proof:
        print(rendered)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
