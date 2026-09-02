"""Release gate V2 の専用 contract/fixture test。

このファイルは Release gate の最小境界だけを検証する。Daily gate、外部公開、
既存のテスト群を起動せず、collection、partition、receipt、ledger の fail-closed
契約を test seam で観測する。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import news_grasp_release_gate as gate


def _collection_report(nodes: list[str]) -> dict[str, Any]:
    return {
        "schemaVersion": gate.RELEASE_COLLECTION_SCHEMA,
        "kind": "collection",
        "complete": True,
        "collection_complete": True,
        "collection_errors": [],
        "collection_nodes": list(nodes),
        "collection_count": len(nodes),
        "collection_sha256": gate._node_hash(nodes),
    }


def _partition_map(nodes: list[str]) -> dict[str, list[str]]:
    result = {name: [] for name in gate.RELEASE_PARTITIONS}
    result[gate.RELEASE_PARTITIONS[0]] = list(nodes)
    return result


def _partition_receipt(
    *,
    release_id: str,
    partition: str,
    nodes: list[str],
    collection_sha256: str,
    receipt_id: str = "partition-receipt-1",
) -> dict[str, Any]:
    return {
        "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
        "receipt_id": receipt_id,
        "partition": partition,
        "node_ids": list(nodes),
        "node_count": len(nodes),
        "node_receipts": [
            {
                "node_id": node,
                "partition": partition,
                "status": "fail" if index == 0 else "pass",
                "events": [],
                "receipt_id": receipt_id,
            }
            for index, node in enumerate(nodes)
        ],
        "failed_nodes": [nodes[0]],
        "skipped_nodes": [],
        "exact_failed_set_sha256": gate._node_hash([nodes[0]]),
        "process_count": 1,
        "ok": False,
        "status": "red",
        "failure": "fixture_failure",
        "cause_hash": "1" * 64,
        "release_id": release_id,
        "collection_sha256": collection_sha256,
    }


def _seed_failed_partition(
    ledger_path: Path,
    *,
    release_id: str = "release-causal",
    partition: str = "scoped_changed",
) -> tuple[dict[str, Any], list[str]]:
    nodes = [
        "tests/test_fixture_release.py::test_failed",
        "tests/test_fixture_release.py::test_success",
    ]
    previous = _partition_receipt(
        release_id=release_id,
        partition=partition,
        nodes=nodes,
        collection_sha256="2" * 64,
    )
    gate._append_ledger(
        ledger_path,
        "partition_completed",
        release_id=release_id,
        partition=partition,
        identity=f"{release_id}:{partition}",
        collection_sha256=previous["collection_sha256"],
        receipt=previous,
        receipt_hash=gate._mapping_hash(previous),
    )
    return previous, nodes


def test_NG_RG_01_authoritative_collection_count_mismatch_is_red() -> None:
    nodes = ["tests/test_release.py::test_one"]
    report = _collection_report(nodes)
    report["collection_count"] = 2

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_collection_count_mismatch"):
        gate._validate_collection_report(report)


def test_NG_RG_01_authoritative_collection_hash_mismatch_is_red() -> None:
    nodes = ["tests/test_release.py::test_one"]
    report = _collection_report(nodes)
    report["collection_sha256"] = "f" * 64

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_collection_hash_mismatch"):
        gate._validate_collection_report(report)


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (
            lambda parts, node: parts["scoped_changed"].append(node),
            "release_partition_overlap",
        ),
        (
            lambda parts, node: parts["scoped_changed"].clear(),
            "release_partition_missing",
        ),
        (
            lambda parts, node: parts["scoped_changed"].append("tests/not-collected.py::test_extra"),
            "release_partition_extra",
        ),
        (
            lambda parts, node: parts.__setitem__("unknown_partition", []),
            "release_partition_unknown",
        ),
    ],
    ids=["overlap", "missing", "extra-unknown-node", "unknown-partition"],
)
def test_NG_RG_02_partition_shape_is_fail_closed(mutator: Any, reason: str) -> None:
    node = "tests/test_release.py::test_one"
    parts = _partition_map([node])
    if reason == "release_partition_overlap":
        parts["historical"] = [node]
    else:
        mutator(parts, node)

    with pytest.raises(gate.NewsGraspReleaseGateError, match=reason):
        gate.validate_partition([node], parts)


def test_NG_RG_02_registry_unknown_node_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_registry_unknown_node"):
        gate.classify_collection_nodes(["fixtures/fabricated.py::test_fake"], repo_root=tmp_path)


def test_NG_RG_03_selector_is_rejected_before_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    starts: list[object] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: starts.append(args))

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_selector_forbidden"):
        gate.collect_only_nodes(
            tmp_path,
            pytest_args=("-k", "test_fake"),
            ledger_path=tmp_path / "ledger.jsonl",
            release_id="release-selector",
        )
    assert starts == []


@pytest.mark.parametrize(
    "forbidden_key",
    ["collection_nodes", "partitions", "runner", "pytest_args"],
    ids=["fake-collection", "fake-partition", "runner-injection", "selector"],
)
def test_NG_RG_03_cli_payload_injection_is_rejected_without_process(
    forbidden_key: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    starts: list[object] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: starts.append(args))
    payload: dict[str, Any] = {"release_id": "release-cli-injection"}
    payload[forbidden_key] = {"fake": True} if forbidden_key == "partitions" else ["-k", "fake"]
    payload_path = tmp_path / f"{forbidden_key}.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = gate._main(["run", str(payload_path), str(tmp_path)])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "release_payload_selector_or_fake_collection_forbidden" in output
    assert starts == []


def test_NG_RG_03_production_api_rejects_runner_and_partition_override_before_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    starts: list[object] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: starts.append(args))
    node = "tests/test_release.py::test_one"
    receipt = {
        "ok": True,
        "authority": "release_authoritative_collect_only",
        "collection_nodes": [node],
        "collection_sha256": gate._node_hash([node]),
        "release_id": "release-api-injection",
    }

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_runner_injection_forbidden"):
        gate.execute_partitioned_nodes(
            [node],
            repo_root=tmp_path,
            collection_receipt=receipt,
            runner=lambda value: {"status": "pass"},
            ledger_path=tmp_path / "ledger.jsonl",
        )
    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_partition_override_forbidden"):
        gate.execute_partitioned_nodes(
            [node],
            repo_root=tmp_path,
            collection_receipt=receipt,
            partitions=_partition_map([node]),
            ledger_path=tmp_path / "ledger.jsonl",
        )
    assert starts == []


class _FakeItem:
    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


class _FakeReport:
    def __init__(
        self,
        nodeid: str,
        *,
        when: str,
        passed: bool = False,
        failed: bool = False,
        skipped: bool = False,
    ) -> None:
        self.nodeid = nodeid
        self.when = when
        self.passed = passed
        self.failed = failed
        self.skipped = skipped
        self.duration = 0.001


def test_NG_RG_04_structured_plugin_receipt_has_exact_pass_fail_error_skip_nodes(tmp_path: Path) -> None:
    nodes = [
        "tests/test_release.py::test_pass",
        "tests/test_release.py::test_fail",
        "tests/test_release.py::test_error",
        "tests/test_release.py::test_skip",
    ]
    output = tmp_path / "structured.json"
    plugin = gate._StructuredPytestPlugin(output, "run")
    session = SimpleNamespace(items=[_FakeItem(node) for node in nodes])
    plugin.pytest_collection_finish(session)
    plugin.pytest_runtest_logreport(_FakeReport(nodes[0], when="call", passed=True))
    plugin.pytest_runtest_logreport(_FakeReport(nodes[1], when="call", failed=True))
    plugin.pytest_runtest_logreport(_FakeReport(nodes[2], when="setup", failed=True))
    plugin.pytest_runtest_logreport(_FakeReport(nodes[3], when="call", skipped=True))
    plugin.pytest_sessionfinish(session, 1)

    report = json.loads(output.read_text(encoding="utf-8"))
    rows = gate._validate_node_report(report, nodes)

    assert [row["node_id"] for row in rows] == nodes
    assert {row["node_id"]: row["status"] for row in rows} == {
        nodes[0]: "pass",
        nodes[1]: "fail",
        nodes[2]: "error",
        nodes[3]: "skip",
    }


def test_NG_RG_05_completed_partition_attaches_without_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "ledger.jsonl"
    release_id = "release-attach"
    partition = "scoped_changed"
    nodes = ["tests/test_release.py::test_one"]
    receipt = {
        "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
        "receipt_id": "partition-completed-1",
        "partition": partition,
        "node_ids": nodes,
        "collection_sha256": gate._node_hash(nodes),
        "node_receipts": [],
        "failed_nodes": [],
        "skipped_nodes": [],
        "process_count": 1,
        "ok": True,
        "status": "green",
    }
    gate._append_ledger(
        ledger,
        "partition_completed",
        release_id=release_id,
        partition=partition,
        identity=f"{release_id}:{partition}",
        collection_sha256=receipt["collection_sha256"],
        receipt=receipt,
        receipt_hash=gate._mapping_hash(receipt),
    )
    starts: list[object] = []
    monkeypatch.setattr(gate, "_run_partition_process", lambda *args, **kwargs: starts.append(args))

    result = gate._partition_process_receipt(
        partition,
        nodes,
        repo_root=tmp_path,
        timeout_seconds=1,
        release_id=release_id,
        collection_sha256=receipt["collection_sha256"],
        ledger_path=ledger,
    )

    assert result["attached"] is True
    assert result["process_count"] == 0
    assert starts == []


def test_NG_RG_05_started_partition_requires_crash_attach_and_never_reruns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    release_id = "release-crash-attach"
    partition = "historical"
    nodes = ["tests/test_release.py::test_one"]
    collection_sha256 = gate._node_hash(nodes)
    gate._append_ledger(
        ledger,
        "partition_started",
        release_id=release_id,
        partition=partition,
        identity=f"{release_id}:{partition}",
        collection_sha256=collection_sha256,
        node_ids=nodes,
    )
    starts: list[object] = []
    monkeypatch.setattr(gate, "_run_partition_process", lambda *args, **kwargs: starts.append(args))

    result = gate._partition_process_receipt(
        partition,
        nodes,
        repo_root=tmp_path,
        timeout_seconds=1,
        release_id=release_id,
        collection_sha256=collection_sha256,
        ledger_path=ledger,
    )

    assert result["status"] == "crash_attach_required"
    assert result["failure"] == "release_partition_inflight"
    assert result["process_count"] == 0
    assert starts == []


def test_NG_RG_06_causal_repair_allows_only_exact_failed_set_and_new_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    previous, nodes = _seed_failed_partition(ledger)
    calls: list[tuple[Any, ...]] = []

    def fake_partition_process(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(args)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
            "receipt_id": "repair-1",
            "partition": "scoped_changed",
            "node_ids": [nodes[0]],
            "node_count": 1,
            "node_receipts": [],
            "failed_nodes": [],
            "skipped_nodes": [],
            "process_count": 1,
            "ok": True,
            "status": "green",
        }

    monkeypatch.setattr(gate, "_run_partition_process", fake_partition_process)
    result = gate.causal_repair_partition(
        repo_root=tmp_path,
        partition="scoped_changed",
        node_ids=[nodes[0]],
        cause_hash="3" * 64,
        previous_receipt=previous,
        repair_id="repair-allowed",
        release_id="release-causal",
        ledger_path=ledger,
    )

    assert result["ok"] is True
    assert result["exact_failed_nodes"] == [nodes[0]]
    assert result["automatic_retry"] is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("nodes_mode", "cause_hash", "expected"),
    [
        ("exact", "1" * 64, "release_repair_same_cause_rejected"),
        ("all", "3" * 64, "release_repair_exact_failed_set_required"),
    ],
    ids=["same-cause", "success-node-injected"],
)
def test_NG_RG_06_causal_repair_rejects_same_cause_or_success_node(
    nodes_mode: str,
    cause_hash: str,
    expected: str,
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    previous, nodes = _seed_failed_partition(ledger)
    requested = [nodes[0]] if nodes_mode == "exact" else nodes

    with pytest.raises(gate.NewsGraspReleaseGateError, match=expected):
        gate.causal_repair_partition(
            repo_root=tmp_path,
            partition="scoped_changed",
            node_ids=requested,
            cause_hash=cause_hash,
            previous_receipt=previous,
            repair_id=f"repair-{nodes_mode}",
            release_id="release-causal",
            ledger_path=ledger,
        )


def test_NG_RG_06_causal_repair_rejects_unbound_previous_receipt(tmp_path: Path) -> None:
    previous = _partition_receipt(
        release_id="release-unbound",
        partition="scoped_changed",
        nodes=["tests/test_release.py::test_failed", "tests/test_release.py::test_success"],
        collection_sha256="4" * 64,
    )

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_repair_previous_receipt_not_in_ledger"):
        gate.causal_repair_partition(
            repo_root=tmp_path,
            partition="scoped_changed",
            node_ids=[previous["node_ids"][0]],
            cause_hash="5" * 64,
            previous_receipt=previous,
            repair_id="repair-unbound",
            release_id="release-unbound",
            ledger_path=tmp_path / "ledger.jsonl",
        )


def test_NG_RG_07_nopublish_receipt_is_outside_union_and_duplicate_is_red(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    receipt = {
        "ok": True,
        "mode": "nopublish",
        "external_mutation_count": 0,
        "pytest_union_excluded": True,
        "attempt_id": "nopublish-attempt-1",
    }

    first = gate.record_nopublish_receipt(
        release_id="release-nopublish",
        receipt=receipt,
        ledger_path=ledger,
    )
    assert first["ok"] is True
    assert first["pytest_union_excluded"] is True
    assert first["external_mutation_count"] == 0

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_nopublish_duplicate"):
        gate.record_nopublish_receipt(
            release_id="release-nopublish",
            receipt=receipt,
            ledger_path=ledger,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("external_mutation_count", 1, "release_nopublish_side_effect"),
        ("pytest_union_excluded", False, "release_nopublish_union_binding_missing"),
        ("node_ids", ["tests/test_release.py::test_one"], "release_nopublish_pytest_union_overlap"),
    ],
    ids=["mutation", "union-binding", "union-overlap"],
)
def test_NG_RG_07_nopublish_side_effect_or_union_overlap_is_red(
    field: str,
    value: Any,
    expected: str,
    tmp_path: Path,
) -> None:
    receipt: dict[str, Any] = {
        "ok": True,
        "mode": "nopublish",
        "external_mutation_count": 0,
        "pytest_union_excluded": True,
        "attempt_id": f"nopublish-{field}",
    }
    receipt[field] = value

    with pytest.raises(gate.NewsGraspReleaseGateError, match=expected):
        gate.record_nopublish_receipt(
            release_id=f"release-nopublish-{field}",
            receipt=receipt,
            ledger_path=tmp_path / "ledger.jsonl",
        )


def test_NG_RG_08_six_partition_union_runs_each_node_once_in_collection_order() -> None:
    nodes = [f"tests/test_release.py::test_{index}" for index in range(1, 7)]
    partitions = {
        name: [nodes[index]]
        for index, name in enumerate(gate.RELEASE_PARTITIONS)
    }
    calls: list[str] = []

    def seam_runner(node: str) -> dict[str, Any]:
        calls.append(node)
        return {"ok": True, "status": "pass", "node_id": node}

    result = gate.execute_partitioned_nodes(
        nodes,
        partitions=partitions,
        runner=seam_runner,
        release_id="release-union-seam",
    )

    assert result["ok"] is True
    assert result["executed_node_count"] == len(nodes)
    assert result["union_node_count"] == len(nodes)
    assert calls == nodes
    assert len(calls) == len(set(calls))
    assert set(result["partition"]["partitions"]) == set(gate.RELEASE_PARTITIONS)


def test_NG_RG_09_fixed_pytest_uses_utf8_fixed_python_and_structured_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, Any] = {}

    class _Completed:
        returncode = 0
        stdout = b"stdout is diagnostic only"
        stderr = b"stderr log"

    def fake_run(command: list[str], **kwargs: Any) -> _Completed:
        observed["command"] = command
        observed.update(kwargs)
        structured_path = Path(kwargs["env"]["NEWS_GRASP_RELEASE_STRUCTURED_FILE"])
        structured_path.write_text(json.dumps(_collection_report([])), encoding="utf-8")
        return _Completed()

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    result = gate._run_fixed_pytest(
        repo_root=tmp_path,
        collect_only=True,
        timeout_seconds=5,
    )

    assert result["ok"] is True
    assert observed["command"] == [
        gate.RELEASE_PYTHON,
        "-m",
        "pytest",
        "-p",
        gate.RELEASE_PLUGIN_MODULE,
        "--collect-only",
        "-q",
    ]
    assert observed["shell"] is False
    assert observed["env"]["PYTHONIOENCODING"] == "utf-8"
    assert observed["env"]["PYTHONUTF8"] == "1"
    assert observed["env"]["PYTEST_ADDOPTS"] == ""
    assert result["structured"]["kind"] == "collection"
    assert result["stdout"] == "stdout is diagnostic only"


def test_NG_RG_10_append_only_ledger_event_hash_tamper_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    gate._append_ledger(ledger, "fixture_started", release_id="release-ledger")
    gate._append_ledger(ledger, "fixture_completed", release_id="release-ledger")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[0]["event_type"] = "tampered"
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_ledger_event_hash_invalid"):
        gate._ledger_events(ledger)


def test_NG_RG_10_append_only_ledger_chain_tamper_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    gate._append_ledger(ledger, "fixture_started", release_id="release-ledger-chain")
    gate._append_ledger(ledger, "fixture_completed", release_id="release-ledger-chain")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[1]["prev_event_hash"] = "0" * 64
    rows[1].pop("event_hash", None)
    rows[1]["event_hash"] = gate._mapping_hash(rows[1])
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_ledger_chain_invalid"):
        gate._ledger_events(ledger)
