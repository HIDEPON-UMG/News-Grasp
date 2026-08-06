from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


EXPECTED_MATRIX_SHA256 = (
    "c589e341350efc6e3689943933020781addb571d168c6c8e090b7e5cc2ea90d8"
)
EXPECTED_CASE_COUNT = 27
EXPECTED_NODE_COUNT = 81
EXPECTED_PERSPECTIVES = {"primary", "adversarial", "recovery"}


@dataclass(frozen=True)
class RedNode:
    case_id: str
    source_finding_ids: str
    perspective: str
    current_invocation: str
    stimulus: str
    prechange_observation: str
    target_consumer: str
    green_oracle: str
    fixture_node: str

    @property
    def node_id(self) -> str:
        return f"{self.case_id}:{self.perspective}"


@dataclass(frozen=True)
class PreservedBaseline:
    risk: str
    current_consumer: str
    current_green_evidence: str
    target_rule: str
    regression_fixture: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_red_matrix(path: Path) -> tuple[list[RedNode], list[PreservedBaseline]]:
    """hash 固定した Markdown 正本から executable node と baseline を読む。"""
    actual_hash = _sha256(path)
    if actual_hash != EXPECTED_MATRIX_SHA256:
        raise ValueError(
            "RED_MATRIX_HASH_DRIFT: "
            f"expected={EXPECTED_MATRIX_SHA256} actual={actual_hash}"
        )

    nodes: list[RedNode] = []
    baselines: list[PreservedBaseline] = []
    section = "before"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Case |"):
            section = "nodes"
            continue
        if line.startswith("## Preserved baseline"):
            section = "before_baseline"
            continue
        if line.startswith("| Risk |"):
            section = "baseline"
            continue
        if not line.startswith("|") or line.startswith("|---"):
            continue

        cells = _cells(line)
        if section == "nodes":
            if len(cells) != 9:
                raise ValueError(f"RED_MATRIX_COLUMN_COUNT_INVALID: {line}")
            fixture = cells[8].strip("`")
            nodes.append(
                RedNode(
                    case_id=cells[0],
                    source_finding_ids=cells[1],
                    perspective=cells[2],
                    current_invocation=cells[3],
                    stimulus=cells[4],
                    prechange_observation=cells[5],
                    target_consumer=cells[6],
                    green_oracle=cells[7],
                    fixture_node=fixture,
                )
            )
        elif section == "baseline":
            if len(cells) != 5:
                raise ValueError(f"BASELINE_COLUMN_COUNT_INVALID: {line}")
            baselines.append(PreservedBaseline(*cells))

    validate_registry(nodes, baselines)
    return nodes, baselines


def validate_registry(
    nodes: list[RedNode], baselines: list[PreservedBaseline]
) -> None:
    """identityだけでなく刺激・consumer・oracleの意味的一意性も検証する。"""
    if len(nodes) != EXPECTED_NODE_COUNT:
        raise ValueError(
            f"RED_NODE_COUNT_MISMATCH: expected={EXPECTED_NODE_COUNT} actual={len(nodes)}"
        )
    case_ids = {node.case_id for node in nodes}
    if len(case_ids) != EXPECTED_CASE_COUNT:
        raise ValueError(
            "RED_CASE_COUNT_MISMATCH: "
            f"expected={EXPECTED_CASE_COUNT} actual={len(case_ids)}"
        )
    node_ids = [node.node_id for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("RED_NODE_ID_DUPLICATE")
    fixture_nodes = [node.fixture_node for node in nodes]
    if len(fixture_nodes) != len(set(fixture_nodes)):
        raise ValueError("RED_FIXTURE_NODE_DUPLICATE")
    if any("::" not in node.fixture_node for node in nodes):
        raise ValueError("RED_FIXTURE_NODE_UNADDRESSABLE")
    semantic_keys = [
        hashlib.sha256(
            "\n".join(
                (
                    node.current_invocation,
                    node.stimulus,
                    node.target_consumer,
                    node.green_oracle,
                )
            ).encode("utf-8")
        ).hexdigest()
        for node in nodes
    ]
    if len(semantic_keys) != len(set(semantic_keys)):
        raise ValueError("RED_NODE_SEMANTIC_DESIGN_DUPLICATE")
    for case_id in sorted(case_ids):
        perspectives = {
            node.perspective for node in nodes if node.case_id == case_id
        }
        if perspectives != EXPECTED_PERSPECTIVES:
            raise ValueError(
                f"RED_PERSPECTIVE_SET_MISMATCH: case={case_id} actual={sorted(perspectives)}"
            )
    if len(baselines) != 1 or not baselines[0].risk.startswith("DCP03"):
        raise ValueError("DCP03_PRESERVED_BASELINE_MISSING")
    if any(node.case_id == "DCP03" for node in nodes):
        raise ValueError("DCP03_MUST_NOT_BE_EXECUTABLE_RED")
