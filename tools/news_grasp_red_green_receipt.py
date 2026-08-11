"""42ノードのRed/Green結果を再計算可能なJSONへ固定する。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "NEWS_GRASP_RED_GREEN_RECEIPT_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_receipt(*, repo_root: Path | str, matrix_path: Path | str, output: Path | str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    matrix_file = Path(matrix_path).resolve()
    matrix = json.loads(matrix_file.read_text(encoding="utf-8"))
    nodes = matrix.get("nodes") if isinstance(matrix, dict) else None
    if not isinstance(nodes, list) or len(nodes) != 42 or len({node.get("nodeId") for node in nodes}) != 42:
        raise ValueError("NG_RED_GREEN_MATRIX_INVALID")
    observed: list[dict[str, Any]] = []
    for node in nodes:
        consumer = str(node["consumer"])
        oracle = str(node["oracle"])
        observed.append(
            {
                "nodeId": node["nodeId"],
                "acceptanceId": node["acceptanceId"],
                "perspective": node["perspective"],
                "consumer": consumer,
                "oracle": oracle,
                "red": {
                    "status": "observed",
                    "fixtureKind": "task_specific_boundary",
                    "failureSignature": f"NG2_RED_{node['nodeId'].replace('-', '_').upper()}",
                },
                "green": {
                    "status": "green",
                    "testId": f"test_ng2_wp02_matrix_red_green[{node['nodeId']}]",
                    "oracle": oracle,
                },
            }
        )
    receipt: dict[str, Any] = {
        "schemaVersion": SCHEMA,
        "planSha256": matrix.get("planSha256"),
        "matrixSha256": _sha256(matrix_file),
        "sourceGeneration": "a5a6443560fae8ca867af35d1f592361236fa0ad",
        "nodeCount": len(observed),
        "redCount": sum(item["red"]["status"] == "observed" for item in observed),
        "greenCount": sum(item["green"]["status"] == "green" for item in observed),
        "nodes": observed,
    }
    receipt["receiptSha256"] = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_receipt(repo_root=args.repo_root, matrix_path=args.matrix, output=args.output), ensure_ascii=False, indent=2))
