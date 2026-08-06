from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path.home() / "OneDrive" / "ドキュメント" / "ProjectFolders"
REVIEWER_PATH = (
    WORKSPACE_ROOT / "tools" / "harness" / "root_principle_adversarial_reviewer.py"
)
REQUIREMENTS_PATH = (
    WORKSPACE_ROOT / "docs" / "harness" / "root_principle_requirements_v1.json"
)


def validate_adversarial_review(
    contract: object, candidate_manifest: object
) -> dict[str, Any]:
    if not isinstance(contract, dict) or not isinstance(candidate_manifest, dict):
        return {
            "reason": "ADVERSARIAL_REVIEW_ORDER_INVALID",
            "mutationCapability": False,
        }
    expected_binding = {
        "candidateTreeSha256": candidate_manifest.get("candidateTreeSha256"),
        "manifestBodySha256": candidate_manifest.get("manifestBodySha256"),
    }
    if contract.get("candidateBinding") != expected_binding:
        return {
            "reason": "ADVERSARIAL_REVIEW_CANDIDATE_BINDING_INVALID",
            "mutationCapability": False,
        }
    try:
        requirements = json.loads(
            REQUIREMENTS_PATH.read_text(encoding="utf-8-sig")
        )
        spec = importlib.util.spec_from_file_location(
            "news_grasp_independent_root_principle_reviewer", REVIEWER_PATH
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("reviewer unavailable")
        reviewer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reviewer)
        receipt = reviewer.review_root_principle_contract(
            contract, requirements
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        return {
            "reason": "ADVERSARIAL_REVIEW_ORDER_INVALID",
            "mutationCapability": False,
        }
    if receipt.get("status") != "Green":
        return {
            "reason": "ADVERSARIAL_REVIEW_ORDER_INVALID",
            "mutationCapability": False,
            "reviewReceipt": receipt,
        }
    return {
        "reason": "ADVERSARIAL_REVIEW_GREEN",
        "mutationCapability": True,
        **expected_binding,
        "reviewReceipt": receipt,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        contract = None
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = None
    result = validate_adversarial_review(contract, manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("mutationCapability") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
