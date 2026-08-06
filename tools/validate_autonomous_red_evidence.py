from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from tests.helpers.red_matrix_registry import load_red_matrix
from tests.helpers.red_node_evidence import semantic_stimulus


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _assertion_expression(failure_text: str, name: str) -> str:
    candidates = []
    for line in failure_text.splitlines():
        match = re.match(r"^\s*>\s+(assert\s+.+)$", line)
        if match:
            candidates.append(match.group(1).strip())
    if not candidates:
        raise ValueError(f"RED_ASSERTION_EXPRESSION_MISSING:{name}")
    return candidates[-1]


def validate_consumer_sources(
    *, name: str, sources: object
) -> tuple[list[dict[str, str]], str]:
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"RED_NODE_CONSUMER_SOURCE_MISSING:{name}")
    verified: list[dict[str, str]] = []
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError(f"RED_NODE_CONSUMER_SOURCE_INVALID:{name}")
        path = Path(str(item.get("path") or ""))
        symbol = str(item.get("symbol") or "")
        expected = str(item.get("sha256") or "")
        if not path.is_file() or not symbol or SHA256.fullmatch(expected) is None:
            raise ValueError(f"RED_NODE_CONSUMER_SOURCE_INVALID:{name}:{path}:{symbol}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"RED_NODE_CONSUMER_SOURCE_DRIFT:{name}:{path}")
        verified.append({"path": str(path.resolve()), "symbol": symbol, "sha256": actual})
    return verified, _sha(verified)


def assert_unique_normalizations(bindings: list[dict[str, str]]) -> None:
    seen: dict[str, str] = {}
    for binding in bindings:
        normalized = str(binding["normalizationSha256"])
        name = str(binding["name"])
        if normalized in seen:
            raise ValueError(
                f"RED_NODE_SEMANTIC_DUPLICATE:{name}:matches={seen[normalized]}"
            )
        seen[normalized] = name


def validate(*, repo: Path, artifact_root: Path) -> dict[str, object]:
    nodes, baselines = load_red_matrix(
        repo / "tests" / "fixtures" / "autonomous_operations" / "red-matrix-v5.md"
    )
    expected_names = {
        f"test_{node.case_id.lower()}_{node.perspective}" for node in nodes
    }
    failures: dict[str, str] = {}
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    family_files = [
        artifact_root / name
        for name in (
            "red-audit-family.xml",
            "red-hook-family.xml",
            "red-import-goal-family.xml",
            "red-runner-launcher-family.xml",
            "red-dcp01-dcp02.xml",
            "red-dcp04-dcp05.xml",
        )
    ]
    for path in family_files:
        root = ET.parse(path).getroot()
        for suite in root.iter("testsuite"):
            for field in totals:
                totals[field] += int(suite.attrib.get(field, "0"))
        for testcase in root.iter("testcase"):
            name = str(testcase.attrib.get("name") or "")
            failure = testcase.find("failure")
            if name in failures:
                raise ValueError(f"RED_TESTCASE_DUPLICATE:{name}")
            failures[name] = "" if failure is None else (failure.text or "")
    if set(failures) != expected_names:
        raise ValueError(
            "RED_TESTCASE_SET_MISMATCH:"
            f"missing={sorted(expected_names-set(failures))}:"
            f"unexpected={sorted(set(failures)-expected_names)}"
        )
    if totals != {"tests": 81, "failures": 81, "errors": 0, "skipped": 0}:
        raise ValueError(f"RED_TOTALS_INVALID:{totals}")

    receipts: list[dict[str, object]] = []
    normalization_bindings: list[dict[str, str]] = []
    for node in nodes:
        name = f"test_{node.case_id.lower()}_{node.perspective}"
        path = (
            artifact_root
            / "red-nodes"
            / f"{node.case_id.lower()}-{node.perspective}.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("schemaVersion") != "NEWS_GRASP_RED_NODE_EVIDENCE_V2":
            raise ValueError(f"RED_NODE_SCHEMA_INVALID:{name}")
        if value.get("caseId") != node.case_id or value.get("perspective") != node.perspective:
            raise ValueError(f"RED_NODE_IDENTITY_MISMATCH:{name}")
        oracle = value.get("oracle")
        if not isinstance(oracle, dict):
            raise ValueError(f"RED_NODE_ORACLE_MISSING:{name}")
        oracle_sha = str(value.get("oracleSha256") or "")
        if oracle_sha != _sha(oracle):
            raise ValueError(f"RED_NODE_ORACLE_HASH_INVALID:{name}")
        signature = str(oracle.get("failureSignature") or "")
        if signature not in failures[name]:
            raise ValueError(f"RED_FAILURE_SIGNATURE_MISMATCH:{name}:{signature}")
        required_hashes = (
            "currentConsumerSourceSha256",
            "inputArtifactSha256",
            "semanticStimulusSha256",
            "stdoutSha256",
            "stderrSha256",
            "actualStateSha256",
            "actualStateArtifactFileSha256",
            "oracleSha256",
        )
        for field in required_hashes:
            if SHA256.fullmatch(str(value.get(field) or "")) is None:
                raise ValueError(f"RED_NODE_HASH_INVALID:{name}:{field}")
        actual_state_path = Path(str(value.get("actualStateArtifactPath") or ""))
        if not actual_state_path.is_file():
            raise ValueError(f"RED_NODE_ACTUAL_STATE_ARTIFACT_MISSING:{name}")
        if hashlib.sha256(actual_state_path.read_bytes()).hexdigest() != value[
            "actualStateArtifactFileSha256"
        ]:
            raise ValueError(f"RED_NODE_ACTUAL_STATE_ARTIFACT_DRIFT:{name}")
        actual_state = json.loads(actual_state_path.read_text(encoding="utf-8"))
        if _sha(actual_state) != value["actualStateSha256"]:
            raise ValueError(f"RED_NODE_ACTUAL_STATE_HASH_INVALID:{name}")
        input_path = Path(str(value.get("inputArtifactPath") or ""))
        if not input_path.is_file():
            raise ValueError(f"RED_NODE_INPUT_ARTIFACT_MISSING:{name}")
        input_bytes = input_path.read_bytes()
        if hashlib.sha256(input_bytes).hexdigest() != value["inputArtifactSha256"]:
            raise ValueError(f"RED_NODE_INPUT_ARTIFACT_DRIFT:{name}")
        input_value = json.loads(input_bytes.decode("utf-8-sig"))
        semantic_sha = _sha(semantic_stimulus(input_value))
        if semantic_sha != value["semanticStimulusSha256"]:
            raise ValueError(f"RED_NODE_SEMANTIC_STIMULUS_INVALID:{name}")
        sources, consumer_sha = validate_consumer_sources(
            name=name, sources=value.get("consumerSources")
        )
        if consumer_sha != value["currentConsumerSourceSha256"]:
            raise ValueError(f"RED_NODE_CONSUMER_BINDING_HASH_INVALID:{name}")
        assertion = _assertion_expression(failures[name], name)
        assertion_sha = hashlib.sha256(assertion.encode("utf-8")).hexdigest()
        normalized = _sha(
            {
                "semanticStimulusSha256": semantic_sha,
                "consumerSources": sources,
                "assertionExpressionSha256": assertion_sha,
            }
        )
        normalization_bindings.append(
            {
                "name": name,
                "assertionExpressionSha256": assertion_sha,
                "normalizationSha256": normalized,
            }
        )
        receipts.append(value)
    assert_unique_normalizations(normalization_bindings)

    dcp03 = json.loads(
        (artifact_root / "dcp03-preserved-green-observation.json").read_text(
            encoding="utf-8"
        )
    )
    dcp03_predicates = {
        "sameDateIdentityStable": dcp03.get("sameDateIdentityStable") is True,
        "otherDateIdentityDistinct": dcp03.get("otherDateIdentityDistinct") is True,
        "scheduledReplayRejected": dcp03.get("scheduledReplayRejected") is True,
        "rootOverrideRejected": dcp03.get("rootOverrideRejected") is True,
        "callCountDeltaTwo": (
            int(dcp03.get("callCountAfter", -1))
            - int(dcp03.get("callCountBefore", -1))
            == 2
        ),
        "identityUnchanged": dcp03.get("taskIdentityAfter") == dcp03.get("taskIdentityBefore"),
        "maxUnchanged": dcp03.get("maxCallsAfter") == dcp03.get("maxCallsBefore") == 9,
    }
    if not all(dcp03_predicates.values()):
        raise ValueError(f"DCP03_BASELINE_INVALID:{dcp03_predicates}")

    dcp03_xml = ET.parse(
        artifact_root / "dcp03-workspace-regression-green.xml"
    ).getroot()
    dcp03_totals = {field: 0 for field in totals}
    for suite in dcp03_xml.iter("testsuite"):
        for field in dcp03_totals:
            dcp03_totals[field] += int(suite.attrib.get(field, "0"))
    if dcp03_totals != {"tests": 2, "failures": 0, "errors": 0, "skipped": 0}:
        raise ValueError(f"DCP03_REGRESSION_INVALID:{dcp03_totals}")

    return {
        "schemaVersion": "NEWS_GRASP_AUTONOMOUS_RED_VALIDATION_V2",
        "status": "Green",
        "redTotals": totals,
        "redNodeCount": len(receipts),
        "uniqueNormalizationCount": len(normalization_bindings),
        "nodeBindings": normalization_bindings,
        "familyFiles": [path.name for path in family_files],
        "preservedGreenBaselines": [item.risk for item in baselines],
        "dcp03Predicates": dcp03_predicates,
        "dcp03RegressionTotals": dcp03_totals,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate(repo=args.repo, artifact_root=args.artifact_root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "Red", "reason": str(error)}, sort_keys=True))
        return 1
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "redTotals": result["redTotals"],
                    "redNodeCount": result["redNodeCount"],
                    "uniqueNormalizationCount": result[
                        "uniqueNormalizationCount"
                    ],
                    "output": str(args.output),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
