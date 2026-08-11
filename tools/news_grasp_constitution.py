from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import io
import json
import os
import re
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


CONSTITUTION_SCHEMA_VERSION = "NEWS_GRASP_CONSTITUTION_V1"
TRACE_SCHEMA_VERSION = "NEWS_GRASP_CONSTITUTION_TRACE_V1"
ACCEPTANCE_RESULT_SCHEMA_VERSION = "NEWS_GRASP_CONSTITUTION_ACCEPTANCE_RESULT_V1"
PROJECTION_SCHEMA_VERSION = "NEWS_GRASP_CONSTITUTION_PROJECTION_V1"
AMENDMENT_AUTHORITY = "user_only"
ALLOWED_PERSPECTIVES = frozenset(
    {
        "primary_behavior",
        "adversarial_boundary",
        "operational_recovery",
    }
)
NGC_A05_primary_behavior = "NGC_A05_primary_behavior"
NGC_A05_adversarial_boundary = "NGC_A05_adversarial_boundary"
NGC_A05_operational_recovery = "NGC_A05_operational_recovery"
generate_mermaid_projection_marker = "generate_mermaid_projection"

ROOT = Path(__file__).resolve().parents[1]
CONSTITUTION_RELATIVE_PATH = Path("config/news_grasp_constitution_v1.json")
TRACE_RELATIVE_PATH = Path("config/news_grasp_constitution_trace_v1.json")
SPEC_RELATIVE_PATH = Path("docs/spec.md")
ACTIVE_CATALOG_RELATIVE_PATH = Path("config/news_grasp_active_object_catalog_v1.json")
SPEC_DISPOSITION_RELATIVE_PATH = Path("config/news_grasp_spec_disposition_v1.json")
TEST_MAP_RELATIVE_PATH = Path("config/news_grasp_test_constitution_map_v1.json")
SKILL_BINDING_RELATIVE_PATH = Path("config/news_grasp_skill_binding_v1.json")
PRODUCT_WRITE_ALLOWLIST_RELATIVE_PATH = Path("config/news_grasp_product_write_allowlist_v1.json")
OPERATIONAL_BINDINGS_RELATIVE_PATH = Path("config/news_grasp_operational_bindings_v1.json")
PROJECTION_RELATIVE_PATH = Path("config/news_grasp_constitution_projection_v1.json")
HTML_SPEC_RELATIVE_PATH = Path("docs/specs/2026-08-12_news-grasp-product-constitution.html")
AUTOMATION_ASSET_MANIFEST_RELATIVE_PATH = Path("config/news_grasp_automation_assets_v2.json")
AGENT_PROJECTION_PATHS = (Path("AGENTS.md"), Path("CLAUDE.md"))
PROJECTION_START = "<!-- NEWS_GRASP_CONSTITUTION_PROJECTION_V1_START -->"
PROJECTION_END = "<!-- NEWS_GRASP_CONSTITUTION_PROJECTION_V1_END -->"
CATALOG_SCHEMA_VERSION = "NEWS_GRASP_ACTIVE_OBJECT_CATALOG_V1"
SPEC_DISPOSITION_SCHEMA_VERSION = "NEWS_GRASP_SPEC_DISPOSITION_V1"
TEST_MAP_SCHEMA_VERSION = "NEWS_GRASP_TEST_CONSTITUTION_MAP_V1"
SKILL_BINDING_SCHEMA_VERSION = "NEWS_GRASP_SKILL_BINDING_V1"
EXPECTED_SKILL_ROLES = {
    "ops-write-operational-plan": "operational",
    "ops-codex-long-running-work": "operational",
    "news-grasp-e2e-discipline": "operational",
    "news-grasp-repair-method": "operational",
    "report-news-grasp-incident": "operational",
    "ops-safe-commit": "operational",
    "report-completion-format": "operational",
    "ops-human-friendly-work-governance": "operational",
    "ops-sdd-tdd-harness-governance": "design_read_only",
    "skill-creator": "design_read_only",
    "diagram-tech-graph": "design_read_only",
}
PRODUCT_OVERLAY_SKILLS = frozenset(
    {
        "news-grasp-e2e-discipline",
        "news-grasp-repair-method",
        "report-news-grasp-incident",
    }
)
GENERATED_CATALOG_PATHS = (
    ACTIVE_CATALOG_RELATIVE_PATH.as_posix(),
    SPEC_DISPOSITION_RELATIVE_PATH.as_posix(),
    TEST_MAP_RELATIVE_PATH.as_posix(),
)
DISCOVERY_PATTERNS = (
    "docs/spec.md",
    "docs/specs/**",
    "plans/**",
    "docs/specs/plans/**",
    "AGENTS.md",
    "CLAUDE.md",
    ".ai/harness/brain-manifest.json",
    ".ai/harness/policy.json",
    ".ai/harness/workflow-contract.json",
    "automation/skills/**",
    "config/*.json",
    "schemas/*.json",
    "scripts/ops/*",
    "tests/test_*.py",
    "tests/fixtures/constitutional-operations/*.json",
)
EXPLICIT_PRODUCTION_CONSUMERS = (
    "tools/news_grasp_constitution.py",
    "tools/news_grasp_task_packet.py",
    "tools/news_grasp_operational_contract.py",
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _overlay_sha256(root: Path, relative: str) -> str:
    overlay = _resolve_product_route(root, relative)
    if not overlay.is_dir() or overlay.is_symlink():
        raise ValueError("CONSTITUTION_SKILL_OVERLAY_INVALID")
    rows: list[str] = []
    for path in sorted(
        candidate
        for candidate in overlay.rglob("*")
        if candidate.is_file()
        and "__pycache__" not in candidate.parts
        and candidate.suffix not in {".pyc", ".pyo"}
    ):
        relative_path = path.relative_to(root).as_posix()
        rows.append(f"{relative_path}\0{_sha256(path)}")
    if not rows:
        raise ValueError("CONSTITUTION_SKILL_OVERLAY_EMPTY")
    payload = ("\n".join(rows) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_skill_binding(value: dict[str, Any], root: Path) -> dict[str, Any]:
    if value.get("schemaVersion") != SKILL_BINDING_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_SKILL_BINDING_SCHEMA_INVALID")
    if value.get("productId") != "News-Grasp":
        raise ValueError("CONSTITUTION_SKILL_BINDING_PRODUCT_INVALID")
    if value.get("sharedGlobalMutationCount") != 0:
        raise ValueError("CONSTITUTION_SHARED_SKILL_MUTATION_FORBIDDEN")
    if value.get("overlayHashAlgorithm") != "sha256_joined_relative_path_nul_file_sha256_lf":
        raise ValueError("CONSTITUTION_SKILL_OVERLAY_HASH_ALGORITHM_INVALID")
    rows = value.get("skills")
    if not isinstance(rows, list) or len(rows) != len(EXPECTED_SKILL_ROLES):
        raise ValueError("CONSTITUTION_SKILL_BINDING_COUNT_INVALID")
    ids = [str(row.get("skillId", "")) for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(EXPECTED_SKILL_ROLES):
        raise ValueError("CONSTITUTION_SKILL_BINDING_SET_INVALID")
    for row in rows:
        skill_id = str(row["skillId"])
        if row.get("role") != EXPECTED_SKILL_ROLES[skill_id]:
            raise ValueError("CONSTITUTION_SKILL_ROLE_INVALID")
        source_locator = row.get("sourceLocator")
        source_sha256 = row.get("sourceSha256")
        if not isinstance(source_locator, str) or not source_locator.startswith("shared-skill://"):
            raise ValueError("CONSTITUTION_SKILL_SOURCE_LOCATOR_INVALID")
        if (
            not isinstance(source_sha256, str)
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
        ):
            raise ValueError("CONSTITUTION_SKILL_SOURCE_HASH_INVALID")
        if skill_id in PRODUCT_OVERLAY_SKILLS:
            if row.get("classification") != "repo_versioned_overlay":
                raise ValueError("CONSTITUTION_PRODUCT_SKILL_OVERLAY_REQUIRED")
            expected_path = f"automation/skills/{skill_id}"
            if row.get("overlayPath") != expected_path:
                raise ValueError("CONSTITUTION_SKILL_OVERLAY_PATH_INVALID")
            if row.get("installedPath") != f"news-grasp-assets/skills/{skill_id}":
                raise ValueError("CONSTITUTION_SKILL_INSTALLED_PATH_INVALID")
            if row.get("overlaySha256") != _overlay_sha256(root, expected_path):
                raise ValueError("CONSTITUTION_SKILL_OVERLAY_HASH_DRIFT")
        else:
            if row.get("classification") != "shared_read_only_unchanged_with_reason":
                raise ValueError("CONSTITUTION_SHARED_SKILL_CLASSIFICATION_INVALID")
            if any(field in row for field in ("overlayPath", "overlaySha256", "installedPath")):
                raise ValueError("CONSTITUTION_SHARED_SKILL_OVERLAY_FORBIDDEN")
        if not row.get("consumer") or not row.get("reason"):
            raise ValueError("CONSTITUTION_SKILL_JUSTIFICATION_MISSING")
    role_counts = {
        role: sum(1 for row in rows if row["role"] == role)
        for role in {"operational", "design_read_only"}
    }
    if role_counts != {"operational": 8, "design_read_only": 3}:
        raise ValueError("CONSTITUTION_SKILL_ROLE_COUNT_INVALID")
    return value


def load_skill_binding(root: Path) -> dict[str, Any]:
    return validate_skill_binding(_read(root / SKILL_BINDING_RELATIVE_PATH), root)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _git_lines(root: Path, *arguments: str) -> list[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        shell=False,
        creationflags=flags,
    )
    return [line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()]


def _json_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _json_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _json_strings(child)]
    return []


def _discover_active_candidates(root: Path) -> list[str]:
    discovered = set(
        _git_lines(
            root,
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *DISCOVERY_PATTERNS,
        )
    )
    discovered.update(GENERATED_CATALOG_PATHS)
    discovered.update(EXPLICIT_PRODUCTION_CONSUMERS)
    config_paths = sorted(
        path
        for path in discovered
        if path.startswith("config/")
        and path.endswith(".json")
        and path not in GENERATED_CATALOG_PATHS
        and (root / path).is_file()
    )
    for config_path in config_paths:
        try:
            value = _read(root / config_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for raw in _json_strings(value):
            normalized = raw.replace("\\", "/").lstrip("./")
            if not normalized or normalized.startswith(("http://", "https://")):
                continue
            candidate = root / normalized
            if candidate.is_file() and candidate.resolve().is_relative_to(root):
                discovered.add(normalized)
    return sorted(
        path
        for path in discovered
        if path and not path.endswith("/.gitkeep") and not path.endswith("\\.gitkeep")
    )


def _object_class(path: str) -> str:
    if path == "docs/spec.md" or path.startswith("docs/specs/"):
        return "specification"
    if path.startswith("plans/"):
        return "plan"
    if path in {"AGENTS.md", "CLAUDE.md"} or path.startswith(".ai/harness/"):
        return "product_rule"
    if path.startswith("automation/skills/"):
        return "product_skill"
    if path.startswith("tests/test_"):
        return "test"
    if path.startswith("tests/fixtures/"):
        return "test_fixture"
    if path.startswith("schemas/"):
        return "state_schema"
    if path.startswith("scripts/ops/"):
        name = Path(path).name.casefold()
        if name.startswith("install-"):
            return "installer"
        if "task-launcher" in name or "bootstrap" in name:
            return "scheduled_task_entry"
        return "runtime_entry"
    if path.startswith("config/"):
        return "product_configuration"
    if path.startswith("tools/"):
        return "production_consumer"
    return "product_source"


def _clauses_for(object_class: str) -> list[str]:
    mapping = {
        "specification": ["NGC-C14"],
        "plan": ["NGC-C14"],
        "product_rule": ["NGC-C06", "NGC-C11", "NGC-C14"],
        "product_skill": ["NGC-C11", "NGC-C12", "NGC-C14"],
        "test": ["NGC-C06", "NGC-C14"],
        "test_fixture": ["NGC-C06", "NGC-C14"],
        "state_schema": ["NGC-C07", "NGC-C09", "NGC-C14"],
        "installer": ["NGC-C06", "NGC-C09", "NGC-C11", "NGC-C13"],
        "scheduled_task_entry": ["NGC-C04", "NGC-C06", "NGC-C09", "NGC-C13"],
        "runtime_entry": ["NGC-C04", "NGC-C06", "NGC-C09", "NGC-C11"],
        "product_configuration": ["NGC-C06", "NGC-C09", "NGC-C10", "NGC-C14"],
        "production_consumer": ["NGC-C04", "NGC-C06", "NGC-C09", "NGC-C10", "NGC-C14"],
        "product_source": ["NGC-C06", "NGC-C09", "NGC-C14"],
    }
    return mapping[object_class]


def _disposition(path: str) -> str:
    if path.startswith("docs/specs/") or path.startswith("plans/"):
        return "superseded_history"
    return "active_linked"


@dataclass(eq=False)
class _CollectionRecorder:
    node_ids: list[str] = field(default_factory=list)
    collection_errors: list[str] = field(default_factory=list)

    def pytest_collection_modifyitems(self, session: Any, config: Any, items: list[Any]) -> None:
        del session, config
        self.node_ids = sorted(
            re.sub(r"0x[0-9A-Fa-f]+", "0xADDR", item.nodeid.replace("\\", "/"))
            for item in items
        )

    def pytest_collectreport(self, report: Any) -> None:
        if report.failed:
            self.collection_errors.append(str(report.longrepr))


def collect_test_nodes(repo_root: Path | str = ROOT) -> list[str]:
    import pytest

    root = _root(repo_root)
    recorder = _CollectionRecorder()
    previous = Path.cwd()
    output = io.StringIO()
    try:
        os.chdir(root)
        with redirect_stdout(output), redirect_stderr(output):
            exit_code = int(
                pytest.main(
                    ["--collect-only", "-q", "-p", "no:cacheprovider"],
                    plugins=[recorder],
                )
            )
    finally:
        os.chdir(previous)
    if exit_code != 0 or recorder.collection_errors:
        raise ValueError("CONSTITUTION_TEST_COLLECTION_FAILED")
    if not recorder.node_ids or len(recorder.node_ids) != len(set(recorder.node_ids)):
        raise ValueError("CONSTITUTION_TEST_NODE_SET_INVALID")
    return recorder.node_ids


def _build_spec_disposition(root: Path, paths: list[str]) -> dict[str, Any]:
    rows = []
    for path in paths:
        if path != "docs/spec.md" and not path.startswith(("docs/specs/", "plans/")):
            continue
        disposition = _disposition(path)
        rows.append(
            {
                "path": path,
                "disposition": disposition,
                "active": disposition == "active_linked",
                "dependencyScan": "not_required_active" if disposition == "active_linked" else "preserve_reference_only",
                "deleteAuthorized": False,
                "rollback": "restore tracked bytes from the bound source commit",
            }
        )
    return {
        "schemaVersion": SPEC_DISPOSITION_SCHEMA_VERSION,
        "sourceCommit": _git_lines(root, "rev-parse", "HEAD")[0],
        "rows": rows,
        "deleteReadyCount": 0,
        "acceptanceBindings": [
            {
                "acceptanceId": "A04",
                "requirementId": "R04",
                "clauseIds": ["NGC-C09", "NGC-C14"],
                "todoId": "TODO-147",
                "productionRoute": SPEC_DISPOSITION_RELATIVE_PATH.as_posix(),
                "consumerMarker": SPEC_DISPOSITION_SCHEMA_VERSION,
                "stateId": "all_specs_dispositioned",
                "recoveryId": "disable_then_dependency_scan_before_delete",
                "evidenceId": "spec_disposition_set_hash",
                "perspectives": sorted(ALLOWED_PERSPECTIVES),
                "testNodeIds": [
                    "test_a04_boundary",
                    "test_a04_recovery",
                    "test_a04_primary",
                ],
            }
            ,
            {
                "acceptanceId": "A08",
                "requirementId": "R08",
                "clauseIds": ["NGC-C11", "NGC-C12", "NGC-C14"],
                "todoId": "TODO-148",
                "productionRoute": "config/news_grasp_skill_binding_v1.json",
                "consumerMarker": "NEWS_GRASP_SKILL_BINDING_V1",
                "stateId": "skills_audited_and_bound",
                "recoveryId": "disable_unclassified_skill_then_reconcile_installer",
                "evidenceId": "skill_source_installed_hash_parity",
                "perspectives": sorted(ALLOWED_PERSPECTIVES),
                "testNodeIds": [
                    "test_a08_boundary",
                    "test_a08_recovery",
                    "test_a08_primary",
                ],
            }
        ],
    }


def _build_test_map(root: Path, paths: list[str], node_ids: list[str]) -> dict[str, Any]:
    test_paths = sorted(path for path in paths if path.startswith("tests/test_") and path.endswith(".py"))
    rows = []
    for path in test_paths:
        owned = sorted(node_id for node_id in node_ids if node_id.split("::", 1)[0] == path)
        rows.append(
            {
                "path": path,
                "sha256": _sha256(root / path),
                "nodeCount": len(owned),
                "nodeIds": owned,
                "clauseIds": _clauses_for("test"),
            }
        )
    mapped = [node_id for row in rows for node_id in row["nodeIds"]]
    if mapped != sorted(node_ids):
        raise ValueError("CONSTITUTION_TEST_NODE_UNMAPPED")
    return {
        "schemaVersion": TEST_MAP_SCHEMA_VERSION,
        "collectionCommand": "python -m pytest --collect-only -q -p no:cacheprovider",
        "collectedNodeCount": len(node_ids),
        "collectedNodeSetSha256": _canonical_sha256(node_ids),
        "files": rows,
    }


def _build_active_catalog(root: Path, paths: list[str], test_map: dict[str, Any]) -> dict[str, Any]:
    objects = []
    for path in paths:
        object_class = _object_class(path)
        disposition = _disposition(path)
        source = root / path
        objects.append(
            {
                "objectId": hashlib.sha256(f"{object_class}:{path}".encode("utf-8")).hexdigest()[:24],
                "class": object_class,
                "path": path,
                "owner": "News-Grasp",
                "generationBinding": "source_commit",
                "disposition": disposition,
                "active": disposition == "active_linked",
                "clauseIds": _clauses_for(object_class),
                "sha256": None if path == ACTIVE_CATALOG_RELATIVE_PATH.as_posix() else _sha256(source),
                "hashPolicy": "self_excluded" if path == ACTIVE_CATALOG_RELATIVE_PATH.as_posix() else "exact",
                "rollback": "restore tracked bytes from the bound source commit",
            }
        )
    active_ids = sorted(row["objectId"] for row in objects if row["active"])
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "sourceCommit": _git_lines(root, "rev-parse", "HEAD")[0],
        "discoveryMode": "git_index_plus_untracked_wip_plus_route_reference_scan",
        "objects": objects,
        "activeObjectIds": active_ids,
        "activeObjectCount": len(active_ids),
        "unlinkedActiveObjectCount": 0,
        "objectPathSetSha256": _canonical_sha256(paths),
        "testNodeSetSha256": test_map["collectedNodeSetSha256"],
    }


def generate_active_object_files(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    paths = _discover_active_candidates(root)
    nodes = collect_test_nodes(root)
    disposition = _build_spec_disposition(root, paths)
    test_map = _build_test_map(root, paths, nodes)
    _atomic_json(root / SPEC_DISPOSITION_RELATIVE_PATH, disposition)
    _atomic_json(root / TEST_MAP_RELATIVE_PATH, test_map)
    paths = _discover_active_candidates(root)
    catalog = _build_active_catalog(root, paths, test_map)
    _atomic_json(root / ACTIVE_CATALOG_RELATIVE_PATH, catalog)
    return validate_active_object_catalog(root)


def validate_active_object_catalog(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    catalog = _read(root / ACTIVE_CATALOG_RELATIVE_PATH)
    disposition = _read(root / SPEC_DISPOSITION_RELATIVE_PATH)
    test_map = _read(root / TEST_MAP_RELATIVE_PATH)
    if catalog.get("schemaVersion") != CATALOG_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_ACTIVE_CATALOG_SCHEMA_INVALID")
    if disposition.get("schemaVersion") != SPEC_DISPOSITION_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_SPEC_DISPOSITION_SCHEMA_INVALID")
    if test_map.get("schemaVersion") != TEST_MAP_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_TEST_MAP_SCHEMA_INVALID")
    discovered = _discover_active_candidates(root)
    catalog_paths = [str(row["path"]) for row in catalog.get("objects", [])]
    if catalog_paths != discovered:
        raise ValueError("CONSTITUTION_ACTIVE_UNIVERSE_DRIFT")
    if catalog.get("objectPathSetSha256") != _canonical_sha256(discovered):
        raise ValueError("CONSTITUTION_ACTIVE_UNIVERSE_HASH_DRIFT")
    for row in catalog["objects"]:
        if row.get("active") and not row.get("clauseIds"):
            raise ValueError("CONSTITUTION_ACTIVE_OBJECT_UNLINKED")
        if row.get("hashPolicy") == "exact" and row.get("sha256") != _sha256(root / row["path"]):
            raise ValueError("CONSTITUTION_ACTIVE_OBJECT_HASH_DRIFT")
    test_rows = test_map.get("files", [])
    test_paths = sorted(row["path"] for row in test_rows)
    expected_test_paths = sorted(path for path in discovered if path.startswith("tests/test_") and path.endswith(".py"))
    if test_paths != expected_test_paths:
        raise ValueError("CONSTITUTION_TEST_FILE_SET_DRIFT")
    for row in test_rows:
        if row.get("sha256") != _sha256(root / row["path"]):
            raise ValueError("CONSTITUTION_TEST_SOURCE_DRIFT")
    node_ids = sorted(node_id for row in test_rows for node_id in row.get("nodeIds", []))
    if len(node_ids) != len(set(node_ids)) or len(node_ids) != test_map.get("collectedNodeCount"):
        raise ValueError("CONSTITUTION_TEST_NODE_SET_DRIFT")
    if _canonical_sha256(node_ids) != test_map.get("collectedNodeSetSha256"):
        raise ValueError("CONSTITUTION_TEST_NODE_HASH_DRIFT")
    disposition_paths = sorted(row["path"] for row in disposition.get("rows", []))
    expected_disposition_paths = sorted(
        path for path in discovered if path == "docs/spec.md" or path.startswith(("docs/specs/", "plans/"))
    )
    if disposition_paths != expected_disposition_paths:
        raise ValueError("CONSTITUTION_SPEC_DISPOSITION_SET_DRIFT")
    if disposition.get("deleteReadyCount") != 0:
        raise ValueError("CONSTITUTION_UNAUTHORIZED_DELETE_READY")
    return {
        "schemaVersion": CATALOG_SCHEMA_VERSION,
        "status": "Green",
        "objectCount": len(catalog_paths),
        "activeObjectCount": catalog["activeObjectCount"],
        "unlinkedActiveObjectCount": 0,
        "testFileCount": len(test_paths),
        "testNodeCount": len(node_ids),
        "specDispositionCount": len(disposition_paths),
        "deleteReadyCount": 0,
        "catalogSha256": _sha256(root / ACTIVE_CATALOG_RELATIVE_PATH),
        "testMapSha256": _sha256(root / TEST_MAP_RELATIVE_PATH),
        "specDispositionSha256": _sha256(root / SPEC_DISPOSITION_RELATIVE_PATH),
    }


def _root(repo_root: Path | str) -> Path:
    return Path(repo_root).resolve(strict=True)


def _resolve_product_route(repo_root: Path, route: str) -> Path:
    relative = Path(route)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("CONSTITUTION_ROUTE_OUTSIDE_PRODUCT")
    candidate = (repo_root / relative).resolve(strict=False)
    if not candidate.is_relative_to(repo_root):
        raise ValueError("CONSTITUTION_ROUTE_OUTSIDE_PRODUCT")
    return candidate


def load_constitution(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    value = _read(root / CONSTITUTION_RELATIVE_PATH)
    validate_constitution(value)
    spec_path = root / SPEC_RELATIVE_PATH
    if not spec_path.is_file():
        raise ValueError("CONSTITUTION_SPEC_MISSING")
    if value.get("specSha256") != _sha256(spec_path):
        raise ValueError("CONSTITUTION_SPEC_HASH_MISMATCH")
    return value


def validate_constitution(value: dict[str, Any]) -> None:
    if value.get("schemaVersion") != CONSTITUTION_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_SCHEMA_VERSION_INVALID")
    if value.get("amendmentAuthority") != AMENDMENT_AUTHORITY:
        raise ValueError("CONSTITUTION_AMENDMENT_AUTHORITY_INVALID")
    if value.get("naturalRunEvidenceAllowed") is not False:
        raise ValueError("CONSTITUTION_NATURAL_RUN_EVIDENCE_FORBIDDEN")
    if value.get("sharedGlobalHarnessMutationAllowed") is not False:
        raise ValueError("CONSTITUTION_SHARED_HARNESS_MUTATION_FORBIDDEN")
    spec_sha = str(value.get("specSha256", ""))
    if len(spec_sha) != 64 or any(char not in "0123456789abcdef" for char in spec_sha):
        raise ValueError("CONSTITUTION_SPEC_HASH_INVALID")
    provenance = value.get("userProvenance")
    if not isinstance(provenance, dict):
        raise ValueError("CONSTITUTION_USER_PROVENANCE_MISSING")
    if provenance.get("sourceType") != "user_confirmed_plan":
        raise ValueError("CONSTITUTION_USER_PROVENANCE_INVALID")
    if provenance.get("amendmentAuthority") != AMENDMENT_AUTHORITY:
        raise ValueError("CONSTITUTION_USER_PROVENANCE_AUTHORITY_INVALID")

    pillars = value.get("pillars")
    clauses = value.get("clauses")
    if not isinstance(pillars, list) or len(pillars) != 6:
        raise ValueError("CONSTITUTION_PILLAR_CARDINALITY_INVALID")
    if not isinstance(clauses, list) or len(clauses) != 14:
        raise ValueError("CONSTITUTION_CLAUSE_CARDINALITY_INVALID")
    pillar_ids = [str(row.get("id", "")) for row in pillars]
    clause_ids = [str(row.get("id", "")) for row in clauses]
    if pillar_ids != [f"NGP-P{number:02d}" for number in range(1, 7)]:
        raise ValueError("CONSTITUTION_PILLAR_SET_INVALID")
    if clause_ids != [f"NGC-C{number:02d}" for number in range(1, 15)]:
        raise ValueError("CONSTITUTION_CLAUSE_SET_INVALID")
    if any(str(row.get("pillarId", "")) not in pillar_ids for row in clauses):
        raise ValueError("CONSTITUTION_CLAUSE_PILLAR_UNBOUND")


def load_trace(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    value = _read(root / TRACE_RELATIVE_PATH)
    if value.get("schemaVersion") != TRACE_SCHEMA_VERSION:
        raise ValueError("CONSTITUTION_TRACE_SCHEMA_INVALID")
    edges = value.get("edges")
    if not isinstance(edges, list) or not edges:
        raise ValueError("CONSTITUTION_TRACE_EMPTY")
    bindings = value.get("acceptanceBindings")
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("CONSTITUTION_ACCEPTANCE_BINDINGS_EMPTY")
    ids = [str(row.get("acceptanceId", "")) for row in bindings]
    if len(ids) != len(set(ids)):
        raise ValueError("CONSTITUTION_ACCEPTANCE_BINDING_DUPLICATE")
    for binding in bindings:
        if set(binding.get("perspectives", [])) != ALLOWED_PERSPECTIVES:
            raise ValueError("CONSTITUTION_ACCEPTANCE_PERSPECTIVES_INVALID")
        if not binding.get("clauseIds"):
            raise ValueError("CONSTITUTION_ACCEPTANCE_CLAUSE_UNBOUND")
        if not binding.get("testNodeIds"):
            raise ValueError("CONSTITUTION_ACCEPTANCE_TEST_UNBOUND")
    return value


def generate_mermaid_projection(trace: dict[str, Any]) -> list[str]:
    edges = trace.get("edges")
    if not isinstance(edges, list) or not edges:
        raise ValueError("CONSTITUTION_TRACE_EMPTY")
    projection = [f"{edge['from']}-->{edge['to']}" for edge in edges]
    if len(projection) != len(set(projection)):
        raise ValueError("CONSTITUTION_MERMAID_EDGE_DUPLICATE")
    return projection


def _extension_acceptance_bindings(root: Path) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for relative in (
        SPEC_DISPOSITION_RELATIVE_PATH,
        SKILL_BINDING_RELATIVE_PATH,
        PRODUCT_WRITE_ALLOWLIST_RELATIVE_PATH,
        OPERATIONAL_BINDINGS_RELATIVE_PATH,
    ):
        path = root / relative
        if not path.is_file():
            continue
        value = _read(path)
        rows = value.get("acceptanceBindings", [])
        if not isinstance(rows, list):
            raise ValueError("CONSTITUTION_EXTENSION_BINDINGS_INVALID")
        bindings.extend(rows)
    return bindings


def compile_constitution(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    constitution = load_constitution(root)
    trace = load_trace(root)
    merged_bindings = [
        *trace["acceptanceBindings"],
        *_extension_acceptance_bindings(root),
    ]
    binding_ids = [str(row["acceptanceId"]) for row in merged_bindings]
    if len(binding_ids) != len(set(binding_ids)):
        raise ValueError("CONSTITUTION_ACCEPTANCE_BINDING_DUPLICATE")
    merged_trace = {**trace, "acceptanceBindings": merged_bindings}
    clause_ids = {str(row["id"]) for row in constitution["clauses"]}
    for binding in merged_bindings:
        if not set(binding["clauseIds"]) <= clause_ids:
            raise ValueError("CONSTITUTION_ACCEPTANCE_CLAUSE_UNKNOWN")
        _resolve_product_route(root, str(binding["productionRoute"]))
    projection = generate_mermaid_projection(merged_trace)
    return {
        "schemaVersion": TRACE_SCHEMA_VERSION,
        "constitution": constitution,
        "trace": merged_trace,
        "projection": projection,
        "constitutionSha256": _sha256(root / CONSTITUTION_RELATIVE_PATH),
        "traceSha256": _sha256(root / TRACE_RELATIVE_PATH),
        "projectionSha256": _canonical_sha256(projection),
        "consumerMarkers": [
            NGC_A05_primary_behavior,
            NGC_A05_adversarial_boundary,
            NGC_A05_operational_recovery,
        ],
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mermaid_sources(compiled: dict[str, Any]) -> list[dict[str, str]]:
    constitution = compiled["constitution"]
    pillar_lines = ["flowchart TD", "  constitution[Product Constitution]"]
    for pillar in constitution["pillars"]:
        pillar_lines.append(f"  constitution --> {pillar['id']}[{pillar['name']}]")
    for clause in constitution["clauses"]:
        pillar_lines.append(f"  {clause['id']}[{clause['text']}] --> {clause['pillarId']}")

    trace_lines = ["flowchart LR"]
    for edge in compiled["trace"]["edges"]:
        if edge["from"] in {
            "clause",
            "pillar",
            "userOutcome",
            "requirement",
            "acceptance",
            "TODO",
            "activeObject",
            "consumer",
            "state",
            "recovery",
            "evidence",
            "test",
        }:
            trace_lines.append(f"  {edge['from']} --> {edge['to']}")

    operation_lines = [
        "flowchart LR",
        "  generation[fixed generation] --> scheduled[scheduled attempt]",
        "  scheduled --> checkpoint[artifact checkpoint]",
        "  checkpoint --> recovery[registered recovery]",
        "  recovery --> public[public completion]",
        "  public --> readiness[pure readiness]",
        "  readiness --> evidence[append-only evidence]",
    ]
    return [
        {"id": "pillar-map", "title": "6柱と14条項", "source": "\n".join(pillar_lines)},
        {"id": "trace-map", "title": "憲法から物理提出までのtrace", "source": "\n".join(trace_lines)},
        {"id": "operation-map", "title": "通常scheduled production", "source": "\n".join(operation_lines)},
    ]


def _asset_projection(root: Path) -> dict[str, Any]:
    from tools import news_grasp_asset_manifest

    manifest_path = root / AUTOMATION_ASSET_MANIFEST_RELATIVE_PATH
    manifest = news_grasp_asset_manifest.load_manifest(manifest_path)
    snapshot = news_grasp_asset_manifest.snapshot_assets(root, manifest)
    rows = [
        {
            "assetId": row["assetId"],
            "kind": row["kind"],
            "sourcePath": row["sourcePath"],
            "installPath": row["installPath"],
            "sourceSha256": row["sourceSha256"],
        }
        for row in snapshot["assets"]
    ]
    return {
        "manifestPath": AUTOMATION_ASSET_MANIFEST_RELATIVE_PATH.as_posix(),
        "manifestSha256": _sha256(manifest_path),
        "assetCount": len(rows),
        "assets": rows,
        "assetSetSha256": _canonical_sha256(rows),
    }


def _render_agent_projection(
    *, compiled: dict[str, Any], asset_projection: dict[str, Any]
) -> str:
    constitution = compiled["constitution"]
    acceptance_count = len(compiled["trace"]["acceptanceBindings"])
    return "\n".join(
        [
            PROJECTION_START,
            "## Product Constitution operation projection",
            "",
            "- `NEWS_GRASP_CONSTITUTION_V1` in `docs/spec.md` is the product-local constitutional authority.",
            f"- All News-Grasp active objects bind to {len(constitution['pillars'])} pillars and {len(constitution['clauses'])} clauses through `NEWS_GRASP_CONSTITUTION_TRACE_V1`.",
            f"- The closed-world proof contains {acceptance_count} Acceptance items, {acceptance_count * 3} core nodes, 32 daily replays, and 5 compound replays; natural scheduled execution is not completion evidence.",
            "- Shared/global harness, broker, routing, hooks, and other product repositories are read-only boundaries for this product-local contract.",
            "- Completion keeps implementation, test, commit, push, install, runtime freshness, task parity, rollback, public authority, readiness, and one isolated NoPublish E2E as separate fields.",
            f"- Projection SHA-256: `{compiled['projectionSha256']}`; product asset set SHA-256: `{asset_projection['assetSetSha256']}`.",
            PROJECTION_END,
        ]
    )


def _replace_agent_projection(document: str, block: str) -> str:
    if document.count(PROJECTION_START) > 1 or document.count(PROJECTION_END) > 1:
        raise ValueError("CONSTITUTION_AGENT_PROJECTION_MARKER_DUPLICATE")
    if PROJECTION_START in document or PROJECTION_END in document:
        if PROJECTION_START not in document or PROJECTION_END not in document:
            raise ValueError("CONSTITUTION_AGENT_PROJECTION_MARKER_UNPAIRED")
        start = document.index(PROJECTION_START)
        end = document.index(PROJECTION_END, start) + len(PROJECTION_END)
        return document[:start].rstrip() + "\n\n" + block + document[end:].rstrip() + "\n"
    legacy = "\n## Product Constitution operation projection\n"
    if legacy in document:
        prefix, suffix = document.split(legacy, 1)
        if "\n## " in suffix:
            raise ValueError("CONSTITUTION_AGENT_LEGACY_PROJECTION_NOT_TERMINAL")
        document = prefix.rstrip()
    return document.rstrip() + "\n\n" + block + "\n"


def _render_html_projection(
    *, compiled: dict[str, Any], asset_projection: dict[str, Any], mermaid: list[dict[str, str]]
) -> str:
    constitution = compiled["constitution"]
    pillars = constitution["pillars"]
    clauses = constitution["clauses"]
    bindings = compiled["trace"]["acceptanceBindings"]
    lines = [
        "<!doctype html>",
        '<html lang="ja">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        '  <meta name="color-scheme" content="light">',
        "  <title>News-Grasp Product Constitution</title>",
        "  <style>",
        "    :root {",
        "      --navy: #181C2A;",
        "      --cream: #F0EBE0;",
        "      --paper: #FAF7F0;",
        "      --surface: #FFFFFF;",
        "      --gold: #C9A155;",
        "      --border: #949494;",
        "      --ink: #1A1A1A;",
        "      --muted: #5C5A52;",
        "      --success: #197A4B;",
        "      --focus: #0877D7;",
        "      --focus-halo: #FFD43D;",
        "    }",
        "    * { box-sizing: border-box; }",
        "    html { background: var(--paper); color: var(--ink); }",
        "    body { margin: 0; font-family: 'Noto Serif JP', 'Yu Mincho', serif; line-height: 1.85; }",
        "    .skip-link { position: absolute; left: 8px; top: -80px; background: var(--surface); color: var(--navy); padding: 12px; z-index: 10; }",
        "    .skip-link:focus { top: 8px; outline: 3px solid var(--focus); box-shadow: 0 0 0 3px var(--focus-halo); }",
        "    .spec-header { background: var(--navy); color: var(--cream); border-bottom: 3px solid var(--gold); padding: 48px clamp(16px, 6vw, 80px); }",
        "    .spec-eyebrow { margin: 0 0 8px; font: 700 .82rem/1.4 'JetBrains Mono', Consolas, monospace; letter-spacing: .15em; }",
        "    h1 { margin: 0; font: 900 clamp(2.25rem, 6vw, 4.5rem)/1.05 Inter, 'Segoe UI', sans-serif; letter-spacing: -.03em; overflow-wrap: anywhere; }",
        "    .lead { max-width: 72ch; font-size: 1.05rem; }",
        "    .container { width: min(1280px, calc(100% - 32px)); margin: 0 auto; }",
        "    main.container { padding-block: 48px 72px; }",
        "    .summary { background: var(--surface); border-left: 4px solid var(--gold); padding: 24px; margin-bottom: 32px; }",
        "    .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 24px; padding: 0; border-bottom: 1px solid var(--border); }",
        "    .tab { border: 1px solid var(--border); border-bottom: 3px solid transparent; background: var(--surface); color: var(--navy); padding: 12px 16px; font: 700 .82rem/1.3 'JetBrains Mono', Consolas, monospace; cursor: pointer; }",
        "    .tab.active, .tab[aria-selected='true'] { background: var(--navy); color: var(--cream); border-bottom-color: var(--gold); }",
        "    .tab:focus-visible, button:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; box-shadow: 0 0 0 3px var(--focus-halo); }",
        "    .tab-panel { background: var(--surface); border: 1px solid var(--border); padding: clamp(20px, 4vw, 40px); }",
        "    .tab-panel[hidden] { display: none; }",
        "    h2 { margin-top: 0; font: 900 2rem/1.2 Inter, 'Segoe UI', sans-serif; }",
        "    h3 { font: 800 1.35rem/1.4 'Noto Serif JP', 'Yu Mincho', serif; }",
        "    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 250px), 1fr)); gap: 16px; }",
        "    .card { background: var(--paper); border: 1px solid var(--border); padding: 20px; }",
        "    .card h3 { margin-top: 0; }",
        "    .badge { display: inline-block; background: var(--success); color: var(--surface); padding: 4px 8px; font: 700 .78rem/1.3 'JetBrains Mono', Consolas, monospace; }",
        "    .table-wrap { overflow-x: auto; border: 1px solid var(--border); }",
        "    table { border-collapse: collapse; width: 100%; min-width: 720px; }",
        "    th, td { padding: 12px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--border); }",
        "    th { background: var(--paper); font-family: 'JetBrains Mono', Consolas, monospace; }",
        "    code, pre, .mono { font-family: 'JetBrains Mono', Consolas, monospace; overflow-wrap: anywhere; }",
        "    pre.mermaid { white-space: pre-wrap; background: var(--paper); border-left: 4px solid var(--gold); padding: 16px; overflow-x: auto; }",
        "    .playground { background: var(--paper); border: 1px solid var(--border); padding: 24px; }",
        "    #copy-prompt { background: var(--navy); color: var(--cream); border: 2px solid var(--navy); padding: 12px 16px; font-weight: 700; }",
        "    footer { border-top: 1px solid var(--border); padding: 32px 0; color: var(--muted); }",
        "    @media (max-width: 640px) { .container { width: min(100% - 20px, 1280px); } .spec-header { padding-block: 32px; } .tab { width: 100%; text-align: left; } }",
        "    @media (forced-colors: active) { * { forced-color-adjust: auto; } .spec-header { border-bottom-color: CanvasText; } }",
        "    @media print { .tabs, #copy-prompt { display: none; } .tab-panel[hidden] { display: block; } }",
        "  </style>",
        "</head>",
        "<body>",
        '  <a class="skip-link" href="#main">本文へ移動</a>',
        '  <header class="spec-header">',
        '    <div class="container">',
        '      <p class="spec-eyebrow">NEWS-GRASP / PRODUCT CONSTITUTION / GENERATED PROJECTION</p>',
        "      <h1>公開価値と運用完全性を、同じ正本で閉じる。</h1>",
        f"      <p class=\"lead\">{html_lib.escape(constitution['userProvenance']['statement'])}</p>",
        "    </div>",
        "  </header>",
        '  <main class="container" id="main">',
        '    <section class="summary" aria-labelledby="summary-title">',
        '      <h2 id="summary-title">結論</h2>',
        f"      <p><span class=\"badge\">{len(pillars)} pillars / {len(clauses)} clauses / {len(bindings)} acceptances</span></p>",
        "      <p>固定generation上で、生成・checkpoint・登録済み復旧・公開確認・readinessを人手なしで閉じる。自然scheduled run待ちとshared/global harness変更は完了条件に含めない。</p>",
        "    </section>",
        '    <nav class="tabs" role="tablist" aria-label="仕様書セクション">',
        '      <button class="tab active" role="tab" aria-selected="true" aria-controls="overview" id="tab-overview" data-tab="overview">概要</button>',
        '      <button class="tab" role="tab" aria-selected="false" aria-controls="alternatives" id="tab-alternatives" data-tab="alternatives">14条項</button>',
        '      <button class="tab" role="tab" aria-selected="false" aria-controls="dataflow" id="tab-dataflow" data-tab="dataflow">Trace</button>',
        '      <button class="tab" role="tab" aria-selected="false" aria-controls="impl" id="tab-impl" data-tab="impl">実装binding</button>',
        '      <button class="tab" role="tab" aria-selected="false" aria-controls="playground" id="tab-playground" data-tab="playground">証拠</button>',
        "    </nav>",
        '    <section class="tab-panel active" role="tabpanel" id="overview" aria-labelledby="tab-overview">',
        "      <h2>6つの利用者価値</h2>",
        '      <div class="grid">',
    ]
    for pillar in pillars:
        lines.extend(
            [
                '        <article class="card">',
                f"          <h3><span class=\"mono\">{html_lib.escape(pillar['id'])}</span> {html_lib.escape(pillar['name'])}</h3>",
                f"          <p>{html_lib.escape(pillar['userOutcome'])}</p>",
                "        </article>",
            ]
        )
    lines.extend(
        [
            "      </div>",
            "    </section>",
            '    <section class="tab-panel" role="tabpanel" id="alternatives" aria-labelledby="tab-alternatives" hidden>',
            "      <h2>14条項</h2>",
            '      <div class="table-wrap"><table>',
            "        <thead><tr><th>Clause</th><th>Pillar</th><th>拘束内容</th></tr></thead>",
            "        <tbody>",
        ]
    )
    for clause in clauses:
        lines.append(
            f"          <tr><td><code>{html_lib.escape(clause['id'])}</code></td><td><code>{html_lib.escape(clause['pillarId'])}</code></td><td>{html_lib.escape(clause['text'])}</td></tr>"
        )
    lines.extend(
        [
            "        </tbody>",
            "      </table></div>",
            "    </section>",
            '    <section class="tab-panel" role="tabpanel" id="dataflow" aria-labelledby="tab-dataflow" hidden>',
            "      <h2>3つのMermaid projection</h2>",
            "      <p>図はtrace正本からcompilerが生成したsourceであり、手編集した図を証拠にしない。</p>",
        ]
    )
    for diagram in mermaid:
        lines.extend(
            [
                f"      <h3>{html_lib.escape(diagram['title'])}</h3>",
                f"      <pre class=\"mermaid\" id=\"{html_lib.escape(diagram['id'])}\"><code>{html_lib.escape(diagram['source'])}</code></pre>",
            ]
        )
    lines.extend(
        [
            "    </section>",
            '    <section class="tab-panel" role="tabpanel" id="impl" aria-labelledby="tab-impl" hidden>',
            "      <h2>Acceptanceからproduction consumerへのbinding</h2>",
            '      <div class="table-wrap"><table>',
            "        <thead><tr><th>Acceptance</th><th>Requirement</th><th>TODO</th><th>Production route</th><th>State / Recovery</th></tr></thead>",
            "        <tbody>",
        ]
    )
    for binding in bindings:
        lines.append(
            "          <tr>"
            f"<td><code>{html_lib.escape(binding['acceptanceId'])}</code></td>"
            f"<td><code>{html_lib.escape(binding['requirementId'])}</code></td>"
            f"<td><code>{html_lib.escape(binding['todoId'])}</code></td>"
            f"<td><code>{html_lib.escape(binding['productionRoute'])}</code></td>"
            f"<td><code>{html_lib.escape(binding['stateId'])}</code><br>{html_lib.escape(binding['recoveryId'])}</td>"
            "</tr>"
        )
    prompt = (
        "News-Graspの変更をNEWS_GRASP_CONSTITUTION_V1へ照合し、"
        "clause、acceptance、consumer、state、recovery、evidence、test、physical deliveryを列挙する。"
    )
    lines.extend(
        [
            "        </tbody>",
            "      </table></div>",
            "    </section>",
            '    <section class="tab-panel" role="tabpanel" id="playground" aria-labelledby="tab-playground" hidden>',
            "      <h2>生成証拠</h2>",
            '      <div class="playground">',
            f"        <p><strong>constitution</strong><br><code>{compiled['constitutionSha256']}</code></p>",
            f"        <p><strong>trace</strong><br><code>{compiled['traceSha256']}</code></p>",
            f"        <p><strong>projection</strong><br><code>{compiled['projectionSha256']}</code></p>",
            f"        <p><strong>product assets</strong><br><code>{asset_projection['assetSetSha256']}</code></p>",
            f"        <p id=\"prompt-text\">{html_lib.escape(prompt)}</p>",
            '        <button id="copy-prompt" type="button">照合プロンプトをコピー</button>',
            '        <p id="copy-status" role="status" aria-live="polite"></p>',
            "      </div>",
            "    </section>",
            "  </main>",
            '  <footer><div class="container">News-Grasp product-local generated projection. Shared/global harness mutation: 0.</div></footer>',
            "  <script>",
            "    (() => {",
            "      const tabs = [...document.querySelectorAll('.tab')];",
            "      const panels = [...document.querySelectorAll('.tab-panel')];",
            "      const activate = (tab) => {",
            "        tabs.forEach((item) => {",
            "          const active = item === tab;",
            "          item.classList.toggle('active', active);",
            "          item.setAttribute('aria-selected', String(active));",
            "          item.tabIndex = active ? 0 : -1;",
            "        });",
            "        panels.forEach((panel) => {",
            "          const active = panel.id === tab.dataset.tab;",
            "          panel.hidden = !active;",
            "          panel.classList.toggle('active', active);",
            "        });",
            "      };",
            "      tabs.forEach((tab, index) => {",
            "        tab.addEventListener('click', () => activate(tab));",
            "        tab.addEventListener('keydown', (event) => {",
            "          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;",
            "          event.preventDefault();",
            "          let next = index;",
            "          if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;",
            "          if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;",
            "          if (event.key === 'Home') next = 0;",
            "          if (event.key === 'End') next = tabs.length - 1;",
            "          tabs[next].focus();",
            "          activate(tabs[next]);",
            "        });",
            "      });",
            "      document.getElementById('copy-prompt').addEventListener('click', async () => {",
            "        const value = document.getElementById('prompt-text').textContent;",
            "        await navigator.clipboard.writeText(value);",
            "        document.getElementById('copy-status').textContent = 'コピーしました。';",
            "      });",
            "    })();",
            "  </script>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines) + "\n"


def build_constitution_projection(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    compiled = compile_constitution(root)
    assets = _asset_projection(root)
    mermaid = _mermaid_sources(compiled)
    agent_block = _render_agent_projection(compiled=compiled, asset_projection=assets)
    html = _render_html_projection(
        compiled=compiled,
        asset_projection=assets,
        mermaid=mermaid,
    )
    value = {
        "schemaVersion": PROJECTION_SCHEMA_VERSION,
        "constitutionVersion": compiled["constitution"]["constitutionVersion"],
        "generator": "tools/news_grasp_constitution.py",
        "constitutionSha256": compiled["constitutionSha256"],
        "traceSha256": compiled["traceSha256"],
        "projectionSha256": compiled["projectionSha256"],
        "mermaidDiagramCount": len(mermaid),
        "mermaid": mermaid,
        "assetProjection": assets,
        "outputs": [
            {
                "path": path.as_posix(),
                "managedBlockSha256": _text_sha256(agent_block),
            }
            for path in AGENT_PROJECTION_PATHS
        ]
        + [
            {
                "path": HTML_SPEC_RELATIVE_PATH.as_posix(),
                "sha256": _text_sha256(html),
                "lineCount": len(html.splitlines()),
            }
        ],
    }
    return {"projection": value, "agentBlock": agent_block, "html": html}


def generate_constitution_projections(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    built = build_constitution_projection(root)
    for relative in AGENT_PROJECTION_PATHS:
        path = root / relative
        document = path.read_text(encoding="utf-8-sig")
        _atomic_text(path, _replace_agent_projection(document, built["agentBlock"]))
    _atomic_text(root / HTML_SPEC_RELATIVE_PATH, built["html"])
    _atomic_json(root / PROJECTION_RELATIVE_PATH, built["projection"])
    return validate_constitution_projections(root)


def validate_constitution_projections(repo_root: Path | str = ROOT) -> dict[str, Any]:
    root = _root(repo_root)
    built = build_constitution_projection(root)
    projection_path = root / PROJECTION_RELATIVE_PATH
    if _read(projection_path) != built["projection"]:
        raise ValueError("CONSTITUTION_PROJECTION_JSON_DRIFT")
    documents = []
    for relative in AGENT_PROJECTION_PATHS:
        document = (root / relative).read_text(encoding="utf-8-sig")
        documents.append(document)
        if document.count(PROJECTION_START) != 1 or document.count(PROJECTION_END) != 1:
            raise ValueError("CONSTITUTION_AGENT_PROJECTION_MARKER_INVALID")
        start = document.index(PROJECTION_START)
        end = document.index(PROJECTION_END, start) + len(PROJECTION_END)
        if document[start:end] != built["agentBlock"]:
            raise ValueError("CONSTITUTION_AGENT_PROJECTION_DRIFT")
    if documents[0] != documents[1]:
        raise ValueError("CONSTITUTION_AGENT_MIRROR_DRIFT")
    html_path = root / HTML_SPEC_RELATIVE_PATH
    if html_path.read_text(encoding="utf-8-sig") != built["html"]:
        raise ValueError("CONSTITUTION_HTML_PROJECTION_DRIFT")
    if len(built["html"].splitlines()) < 100:
        raise ValueError("CONSTITUTION_HTML_TOO_SHORT")
    return {
        "status": "Green",
        "schemaVersion": PROJECTION_SCHEMA_VERSION,
        "projectionPath": PROJECTION_RELATIVE_PATH.as_posix(),
        "htmlPath": HTML_SPEC_RELATIVE_PATH.as_posix(),
        "htmlLineCount": len(built["html"].splitlines()),
        "mermaidDiagramCount": len(built["projection"]["mermaid"]),
        "agentProjectionMirror": True,
        "manualProjectionDrift": False,
        "projectionFileSha256": _sha256(projection_path),
        "htmlSha256": _sha256(html_path),
    }


def _expect_value_error(operation: Any, reason_code: str) -> None:
    try:
        operation()
    except ValueError:
        return
    raise ValueError(reason_code)


def _expect_exception(operation: Any, error_type: type[BaseException], reason_code: str) -> None:
    try:
        operation()
    except error_type:
        return
    raise ValueError(reason_code)


def _operational_generation_fixture(temp_root: Path) -> dict[str, Any]:
    from tools import news_grasp_generation as generation

    source = temp_root / "source"
    runtime = temp_root / "runtime"
    source.mkdir()
    runtime.mkdir()
    (source / "consumer.py").write_text("consumer-v1\n", encoding="utf-8")
    config = source / "config.json"
    config.write_text('{"generation":"v1"}\n', encoding="utf-8")
    runtime_file = runtime / "runtime.txt"
    runtime_file.write_text("runtime-v1\n", encoding="utf-8")
    launcher = temp_root / "news-grasp-task-launcher.pyw"
    launcher.write_text("launcher-v1\n", encoding="utf-8")
    action = ["pythonw.exe", str(launcher), "runner"]
    trigger = {"daily": "06:00"}
    authority = generation.create_stable_task_authority(
        task_name="News-Grasp Runner",
        launcher_path=launcher,
        launcher_sha256=_sha256(launcher),
        action=action,
        trigger=trigger,
    )
    manifest = generation.create_manifest(
        source_root=source,
        source_paths=["consumer.py"],
        runtime_root=runtime,
        runtime_paths=["runtime.txt"],
        config_path=config,
        launcher_paths=[launcher],
        task_action=action,
        task_trigger=trigger,
        generation_id="generation-v1",
        previous_generation_id="generation-v0",
        output=temp_root / "generation-v1.json",
        input_manifest={"issueDate": "2026-08-11", "inputSha256": "a" * 64},
    )
    return {
        "source": source,
        "runtime": runtime,
        "config": config,
        "runtimeFile": runtime_file,
        "launcher": launcher,
        "action": action,
        "trigger": trigger,
        "authority": authority,
        "manifest": manifest,
        "inputManifest": {"issueDate": "2026-08-11", "inputSha256": "a" * 64},
    }


def _perspective_oracle(
    *,
    root: Path,
    acceptance_id: str,
    perspective: str,
    compiled: dict[str, Any],
) -> None:
    if acceptance_id == "A01":
        if perspective == "adversarial_boundary":
            invalid = json.loads(json.dumps(compiled["constitution"]))
            invalid["amendmentAuthority"] = "agent"
            _expect_value_error(
                lambda: validate_constitution(invalid),
                "CONSTITUTION_AMENDMENT_BOUNDARY_NOT_ENFORCED",
            )
        elif perspective == "operational_recovery":
            load_constitution(root)
        return
    if acceptance_id == "A03":
        ids = [
            str(row["acceptanceId"])
            for row in compiled["trace"]["acceptanceBindings"]
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("CONSTITUTION_TRACE_DUPLICATE_ACCEPTANCE")
        if perspective == "adversarial_boundary":
            _expect_value_error(
                lambda: _resolve_product_route(root, "../shared.txt"),
                "CONSTITUTION_TRACE_PATH_BOUNDARY_NOT_ENFORCED",
            )
        if (root / ACTIVE_CATALOG_RELATIVE_PATH).is_file():
            validate_active_object_catalog(root)
        return
    if acceptance_id == "A04":
        validation = validate_active_object_catalog(root)
        if validation["unlinkedActiveObjectCount"] != 0:
            raise ValueError("CONSTITUTION_ACTIVE_OBJECT_UNLINKED")
        disposition = _read(root / SPEC_DISPOSITION_RELATIVE_PATH)
        if perspective == "adversarial_boundary":
            if any(row.get("deleteAuthorized") for row in disposition["rows"]):
                raise ValueError("CONSTITUTION_UNAUTHORIZED_DELETE")
        elif perspective == "operational_recovery":
            if any(
                row["disposition"] == "superseded_history"
                and row["dependencyScan"] != "preserve_reference_only"
                for row in disposition["rows"]
            ):
                raise ValueError("CONSTITUTION_HISTORY_RECOVERY_INVALID")
        return
    if acceptance_id == "A05":
        projection = compiled["projection"]
        if not projection:
            raise ValueError("CONSTITUTION_PROJECTION_EMPTY")
        if perspective == "adversarial_boundary":
            duplicate = {
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "a", "to": "b"},
                ]
            }
            _expect_value_error(
                lambda: generate_mermaid_projection(duplicate),
                "CONSTITUTION_PROJECTION_DUPLICATE_NOT_REJECTED",
            )
        return
    if acceptance_id == "A06":
        from tools import news_grasp_change_control as change_control

        routes = change_control.load_route_registry(root)
        if set(routes) != set(change_control.EXPECTED_ROUTE_EXECUTORS):
            raise ValueError("CONSTITUTION_CHANGE_ROUTE_SET_INVALID")
        if any(
            row.get("executor") != change_control.EXPECTED_ROUTE_EXECUTORS[route_id]
            for route_id, row in routes.items()
        ):
            raise ValueError("CONSTITUTION_CHANGE_ROUTE_EXECUTOR_INVALID")
        for route_id, executor in change_control.EXPECTED_ROUTE_EXECUTORS.items():
            change_control.validate_actor_route(
                actor_route_id=route_id,
                executor=executor,
                routes=routes,
            )
        if perspective == "adversarial_boundary":
            registry = _read(root / change_control.ROUTE_RELATIVE_PATH)
            invalid = json.loads(json.dumps(registry))
            invalid["routes"].append(
                {
                    "routeId": "unknown-agent",
                    "producer": "unknown",
                    "executor": {"actor": "unknown"},
                }
            )
            try:
                change_control.validate_route_registry(invalid)
            except change_control.NewsGraspChangeControlError:
                pass
            else:
                raise ValueError("CONSTITUTION_UNKNOWN_CHANGE_ROUTE_NOT_REJECTED")
            fallback = json.loads(json.dumps(registry))
            fallback["unknownRoutePolicy"] = "fallback"
            try:
                change_control.validate_route_registry(fallback)
            except change_control.NewsGraspChangeControlError:
                pass
            else:
                raise ValueError("CONSTITUTION_CHANGE_ROUTE_FALLBACK_NOT_REJECTED")
            try:
                change_control.validate_actor_route(
                    actor_route_id="unknown-agent",
                    executor={"actor": "unknown"},
                    routes=routes,
                )
            except change_control.NewsGraspChangeControlError:
                pass
            else:
                raise ValueError("CONSTITUTION_UNKNOWN_PACKET_ROUTE_NOT_REJECTED")
        elif perspective == "operational_recovery":
            allowlist = _read(root / PRODUCT_WRITE_ALLOWLIST_RELATIVE_PATH)
            forbidden_prefixes = (
                ".agents/",
                ".claude/",
                ".codex/",
                "docs/harness/",
                "AIHarnessState/",
            )
            if any(
                str(path).replace("\\", "/").startswith(forbidden_prefixes)
                for path in allowlist.get("allowedPaths", [])
            ):
                raise ValueError("CONSTITUTION_SHARED_WRITE_ROUTE_PRESENT")
        return
    if acceptance_id == "A08":
        binding = load_skill_binding(root)
        if perspective == "adversarial_boundary":
            invalid = json.loads(json.dumps(binding))
            invalid["sharedGlobalMutationCount"] = 1
            _expect_value_error(
                lambda: validate_skill_binding(invalid, root),
                "CONSTITUTION_SHARED_SKILL_MUTATION_NOT_REJECTED",
            )
        elif perspective == "operational_recovery":
            for row in binding["skills"]:
                if row["skillId"] in PRODUCT_OVERLAY_SKILLS:
                    if row["overlaySha256"] != _overlay_sha256(root, row["overlayPath"]):
                        raise ValueError("CONSTITUTION_SKILL_OVERLAY_NOT_RECOVERABLE")
        return
    if acceptance_id in {"A07", "A11", "A16"}:
        from tools import news_grasp_generation as generation

        with TemporaryDirectory(prefix=f"news-grasp-{acceptance_id.casefold()}-") as temporary:
            fixture = _operational_generation_fixture(Path(temporary))
            verify_kwargs = {
                "manifest": fixture["manifest"],
                "source_root": fixture["source"],
                "runtime_root": fixture["runtime"],
                "config_path": fixture["config"],
                "launcher_paths": [fixture["launcher"]],
                "task_action": fixture["action"],
                "task_trigger": fixture["trigger"],
                "input_manifest": fixture["inputManifest"],
            }
            if acceptance_id == "A07":
                if perspective == "adversarial_boundary":
                    fixture["runtimeFile"].write_text("runtime-drift\n", encoding="utf-8")
                    _expect_exception(
                        lambda: generation.verify_parity(**verify_kwargs),
                        generation.NewsGraspGenerationError,
                        "CONSTITUTION_GENERATION_DRIFT_NOT_REJECTED",
                    )
                elif perspective == "operational_recovery":
                    pointer = Path(temporary) / "active-generation.json"
                    promoted = generation.promote_generation(
                        active_pointer=pointer,
                        old_generation_id="generation-v0",
                        new_generation_id="generation-v1",
                        phase="transaction_committed",
                        stable_task_authority=fixture["authority"],
                        runtime_manifest_sha256=fixture["manifest"]["runtime"]["manifestSha256"],
                        input_manifest_sha256=fixture["manifest"]["inputManifestSha256"],
                    )
                    if promoted["generationId"] != "generation-v1" or not pointer.is_file():
                        raise ValueError("CONSTITUTION_GENERATION_PROMOTION_INVALID")
                else:
                    generation.verify_parity(**verify_kwargs)
                return
            if acceptance_id == "A11":
                if perspective == "adversarial_boundary":
                    invalid = {"issueDate": "2026-08-11", "inputSha256": "b" * 64}
                    _expect_exception(
                        lambda: generation.verify_parity(**{**verify_kwargs, "input_manifest": invalid}),
                        generation.NewsGraspGenerationError,
                        "CONSTITUTION_INPUT_DRIFT_NOT_REJECTED",
                    )
                elif perspective == "operational_recovery":
                    generation.verify_parity(**verify_kwargs)
                else:
                    if fixture["manifest"]["source"]["trackedManifestSha256"] == fixture["manifest"]["inputManifestSha256"]:
                        raise ValueError("CONSTITUTION_GENERATION_INPUT_NOT_SEPARATED")
                return
            active = {
                "schemaVersion": "NEWS_GRASP_ACTIVE_GENERATION_V2",
                "generationId": "generation-v1",
                "stableTaskAuthoritySha256": fixture["authority"]["authoritySha256"],
            }
            wrapper_text = (root / "scripts/ops/invoke-scheduled-equivalent-nopublish.ps1").read_text(
                encoding="utf-8-sig"
            )
            launcher_text = (root / "scripts/ops/news-grasp-task-launcher.pyw").read_text(
                encoding="utf-8"
            )
            if (
                "scheduled_entrypoint_mode = 'installed_stable_launcher'" not in wrapper_text
                or "_run_installed_nopublish_authority" not in launcher_text
                or '"scheduled-equivalent-nopublish"' not in launcher_text
                or "& $PowerShellExe @runnerArguments" in wrapper_text
            ):
                raise ValueError("CONSTITUTION_INSTALLED_NOPUBLISH_ROUTE_INVALID")
            if perspective == "adversarial_boundary":
                fixture["launcher"].write_text("launcher-drift\n", encoding="utf-8")
                _expect_exception(
                    lambda: generation.validate_installed_launcher_identity(
                        launcher_path=fixture["launcher"],
                        stable_task_authority=fixture["authority"],
                        active_generation=active,
                        expected_generation_id="generation-v1",
                    ),
                    generation.NewsGraspGenerationError,
                    "CONSTITUTION_INSTALLED_LAUNCHER_DRIFT_NOT_REJECTED",
                )
            else:
                observed = generation.validate_installed_launcher_identity(
                    launcher_path=fixture["launcher"],
                    stable_task_authority=fixture["authority"],
                    active_generation=active,
                    expected_generation_id="generation-v1",
                )
                if observed["status"] != "green":
                    raise ValueError("CONSTITUTION_INSTALLED_LAUNCHER_INVALID")
            return
    if acceptance_id == "A09":
        from tools import news_grasp_checkpoint as checkpoint

        lineage = checkpoint.derive_daily_operation_lineage(
            issue_date="2026-08-11",
            scheduled_authority_id="scheduled-authority-v1",
        )
        base = {
            "issue_date": "2026-08-11",
            "daily_operation_lineage_id": lineage,
            "artifact_key": "deepdive",
            "stage_id": "deepdive-article",
            "producer_route_id": "deepdive_article_model_route",
            "failure_class": "producer_source_failure",
            "reason_code": "WRAPPER_RC126",
            "cause_input_mask": ["sourceHash", "promptHash"],
        }
        first = checkpoint.cause_fingerprint(
            **base,
            input_hashes={"sourceHash": "s1", "promptHash": "p1", "runId": "run-1"},
        )
        second = checkpoint.cause_fingerprint(
            **base,
            input_hashes={"sourceHash": "s1", "promptHash": "p1", "runId": "run-2"},
        )
        if first != second:
            raise ValueError("CONSTITUTION_CAUSAL_FINGERPRINT_NOISE_DRIFT")
        if perspective == "adversarial_boundary":
            _expect_exception(
                lambda: checkpoint.cause_fingerprint(
                    **base,
                    input_hashes={"sourceHash": "s1"},
                ),
                checkpoint.NewsGraspCheckpointError,
                "CONSTITUTION_CAUSAL_MASK_MISSING_NOT_REJECTED",
            )
        elif perspective == "operational_recovery":
            with TemporaryDirectory(prefix="news-grasp-retry-") as temporary:
                ledger = checkpoint.RetryLedger(Path(temporary) / "retry.json")
                key = f"2026-08-11|{lineage}|deepdive|deepdive_article_model_route|producer_source_failure"
                ledger.admit_retry(key=key, fingerprint=first, cause_hash="cause-v1")
                changed = ledger.admit_retry(key=key, fingerprint="f" * 64, cause_hash="cause-v2")
                repeated = ledger.admit_retry(key=key, fingerprint="f" * 64, cause_hash="cause-v2")
                if changed.get("retry") != 1 or repeated.get("retry") != 0:
                    raise ValueError("CONSTITUTION_CAUSAL_RETRY_BUDGET_INVALID")
        return
    if acceptance_id == "A10":
        from tools.news_grasp_daily_control import build_completion_state_vector_v3

        vector = build_completion_state_vector_v3(
            scheduled_attempt={"status": "failed"},
            recovery_attempt={"status": "not_needed"},
            public_receipt={
                "status": "verification_unavailable" if perspective == "adversarial_boundary" else "verified_green",
                "previousVerifiedGreen": True,
                "authorityId": "authority-v1",
            },
            readiness_probe={"status": "red" if perspective != "operational_recovery" else "green"},
            audit_observation={"status": "unverified"},
            external_dependency={"status": "ready", "evidenceHash": "e" * 64},
            constitution_admission={"status": "green", "constitutionHash": "c" * 64},
        )
        if vector["publicCompletionStatus"] != "green":
            raise ValueError("CONSTITUTION_PUBLIC_GREEN_RETREATED")
        if perspective == "operational_recovery" and vector["operationalStatus"] != "green":
            raise ValueError("CONSTITUTION_READINESS_RECOVERY_NOT_CONVERGED")
        return
    if acceptance_id == "A12":
        from tools import news_grasp_checkpoint as checkpoint

        with TemporaryDirectory(prefix="news-grasp-checkpoint-") as temporary:
            destination = Path(temporary) / "checkpoint.json"
            fingerprint = checkpoint.cause_fingerprint(
                issue_date="2026-08-11",
                daily_operation_lineage_id="lineage-v1",
                artifact_key="deepdive",
                stage_id="article",
                producer_route_id="deepdive_article_model_route",
                failure_class="wrapper_failure",
                reason_code="WRAPPER_RC126",
                cause_input_mask=["inputHash"],
                input_hashes={"inputHash": "input-v1"},
            )
            value = checkpoint.create_checkpoint(
                issue_date="2026-08-11",
                daily_operation_lineage_id="lineage-v1",
                stage="deepdive",
                artifact_key="deepdive",
                input_hashes={"inputHash": "input-v1"},
                output_hash="output-v1",
                schema="MARKDOWN_V1",
                oracle_id="deepdive-quality-v1",
                producer_route_id="deepdive_article_model_route",
                next_deterministic_step="build-dialogue",
                cause_fingerprint_value=fingerprint,
                output_path=destination,
            )
            wrapper = {
                "checkpointAlreadyMaterialized": True,
                "exitCode": 126,
                "checkpointSha256": value["checkpointSha256"],
                "issueDate": "2026-08-11",
                "dailyOperationLineageId": "lineage-v1",
                "artifactKey": "deepdive",
            }
            if perspective == "adversarial_boundary":
                _expect_exception(
                    lambda: checkpoint.resume_stage(
                        checkpoint=value,
                        wrapper_result={**wrapper, "dailyOperationLineageId": "cross-lineage"},
                    ),
                    checkpoint.NewsGraspCheckpointError,
                    "CONSTITUTION_CHECKPOINT_LINEAGE_NOT_REJECTED",
                )
            else:
                resumed = checkpoint.resume_stage(checkpoint=value, wrapper_result=wrapper)
                if resumed["modelCalls"] != 0 or resumed["nextStep"] != "build-dialogue":
                    raise ValueError("CONSTITUTION_CHECKPOINT_CONTINUATION_INVALID")
        return
    if acceptance_id == "A13":
        from tools import operational_recovery_registry as registry

        validated = registry.validate_registry(root)
        if validated["handlerCount"] != 16:
            raise ValueError("CONSTITUTION_RECOVERY_REGISTRY_COUNT_INVALID")
        handlers = registry.default_handlers()
        if perspective == "adversarial_boundary":
            result = registry.dispatch(
                repo_root=root,
                reason_code="GENERATION_DRIFT_SUFFIX",
                context={"reasonCode": "GENERATION_DRIFT_SUFFIX"},
                handlers=handlers,
            )
            if result.handler_id != "major_incident_terminal" or result.result.get("mutationCount") != 0:
                raise ValueError("CONSTITUTION_RECOVERY_PREFIX_FALLBACK_PRESENT")
        else:
            result = registry.dispatch(
                repo_root=root,
                reason_code="GENERATION_DRIFT",
                context={"reasonCode": "GENERATION_DRIFT", "dailyOperationLineageId": "lineage-v1"},
                handlers=handlers,
            )
            if result.handler_id != "active_generation_reconcile":
                raise ValueError("CONSTITUTION_RECOVERY_EXACT_DISPATCH_INVALID")
        return
    if acceptance_id == "A14":
        from tools import news_grasp_gate_profiles as gates

        gates.validate_profiles()
        if perspective == "adversarial_boundary":
            _expect_exception(
                lambda: gates.scheduled_call_graph(calls=["artifact_schema_quality", "pytest"]),
                gates.NewsGraspGateProfileError,
                "CONSTITUTION_RELEASE_GATE_REACHED_FROM_DAILY",
            )
        elif perspective == "operational_recovery":
            release = gates.evaluate_release({oracle: True for oracle in gates.RELEASE_ORACLES})
            if release["status"] != "green":
                raise ValueError("CONSTITUTION_RELEASE_GATE_INVALID")
        return
    if acceptance_id == "A15":
        from tools.news_grasp_task_packet import validate_packet

        packet = {
            "schemaVersion": "LUNA_EXECUTION_PACKET_V2",
            "packetId": "news-grasp-todo-146",
            "todoId": "TODO-146",
            "dependencyIds": ["TODO-145"],
            "writeSet": ["tools/news_grasp_constitution.py"],
            "baselineSha256": "a" * 64,
            "requirementIds": ["R15"],
            "acceptanceIds": ["A15"],
            "redNodeIds": ["test_a15_primary"],
            "command": "pytest -q -k a15",
            "expectedFailureSignature": "NGC_RED_A15",
            "artifactPaths": ["build/constitutional-operations/a15.json"],
            "localVerification": ["pytest -q -k a15"],
            "causalRetryCondition": "packet_bytes_changed",
            "rollback": "restore_prechange_snapshot",
            "snapshot": "todo-145-red-freeze",
            "delivery": "validated_packet_receipt",
            "stopPolicy": "return_to_sol_before_execution",
            "returnToSolBeforeExecution": True,
            "unresolvedDecisionIds": [],
        }
        validate_packet(packet)
        if perspective == "adversarial_boundary":
            invalid = {**packet, "unresolvedDecisionIds": ["D-UNKNOWN"]}
            _expect_value_error(
                lambda: validate_packet(invalid),
                "LUNA_PACKET_UNRESOLVED_DECISION_NOT_REJECTED",
            )
        elif perspective == "operational_recovery":
            invalid = {**packet, "stopPolicy": "continue_anyway"}
            _expect_value_error(
                lambda: validate_packet(invalid),
                "LUNA_PACKET_STOP_POLICY_NOT_ENFORCED",
            )


def evaluate_acceptance(
    *,
    repo_root: Path | str,
    acceptance_id: str,
    perspective: str,
) -> dict[str, Any]:
    if perspective not in ALLOWED_PERSPECTIVES:
        raise ValueError("CONSTITUTION_ACCEPTANCE_PERSPECTIVE_UNKNOWN")
    root = _root(repo_root)
    compiled = compile_constitution(root)
    bindings = {
        str(row["acceptanceId"]): row
        for row in compiled["trace"]["acceptanceBindings"]
    }
    binding = bindings.get(acceptance_id)
    if binding is None:
        raise ValueError("CONSTITUTION_ACCEPTANCE_UNBOUND")
    if perspective not in binding["perspectives"]:
        raise ValueError("CONSTITUTION_ACCEPTANCE_PERSPECTIVE_UNBOUND")

    route = _resolve_product_route(root, str(binding["productionRoute"]))
    if not route.is_file():
        raise ValueError("CONSTITUTION_PRODUCTION_ROUTE_MISSING")
    marker = str(binding["consumerMarker"])
    route_text = route.read_text(encoding="utf-8-sig")
    if marker not in route_text:
        raise ValueError("CONSTITUTION_CONSUMER_MARKER_MISSING")

    _perspective_oracle(
        root=root,
        acceptance_id=acceptance_id,
        perspective=perspective,
        compiled=compiled,
    )

    evidence = {
        "sourceBound": True,
        "traceBound": True,
        "consumerObserved": True,
        "oracleSatisfied": True,
        "routeSha256": _sha256(route),
        "constitutionSha256": compiled["constitutionSha256"],
        "traceSha256": compiled["traceSha256"],
        "projectionSha256": compiled["projectionSha256"],
        "stateId": binding["stateId"],
        "recoveryId": binding["recoveryId"],
        "evidenceId": binding["evidenceId"],
        "testNodeId": dict(
            zip(binding["perspectives"], binding["testNodeIds"], strict=True)
        )[perspective],
    }
    return {
        "schemaVersion": ACCEPTANCE_RESULT_SCHEMA_VERSION,
        "acceptanceId": acceptance_id,
        "requirementId": binding["requirementId"],
        "perspective": perspective,
        "productionRoute": binding["productionRoute"],
        "consumerMarker": marker,
        "status": "Green",
        "evidence": evidence,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="News-Grasp Product Constitutionを検証・生成します。"
    )
    parser.add_argument(
        "command",
        choices=("generate-projections", "check-projections", "generate-active-catalog"),
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.command == "generate-projections":
        result = generate_constitution_projections(args.repo_root)
    elif args.command == "check-projections":
        result = validate_constitution_projections(args.repo_root)
    else:
        result = generate_active_object_files(args.repo_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
