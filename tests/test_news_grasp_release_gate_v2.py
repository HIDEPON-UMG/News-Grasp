"""Release gate V2 の専用 contract/fixture test。

このファイルは Release gate の最小境界だけを検証する。Daily gate、外部公開、
既存のテスト群を起動せず、collection、partition、receipt、ledger の fail-closed
契約を test seam で観測する。
"""

from __future__ import annotations

import inspect
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools import news_grasp_release_gate as gate


_REAL_OBSERVE_RELEASE_SOURCE = gate._observe_release_source


def test_machine_stdout_is_reconfigured_to_utf8_before_non_cp932_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows既定CP932でもreceipt JSONをUTF-8で一度だけ出力する。"""

    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp932", errors="strict", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", stdout)

    gate._emit({"ok": True, "value": "\ufffd"})
    stdout.flush()

    assert stdout.encoding.casefold() == "utf-8"
    assert raw.getvalue() == b'{"ok":true,"value":"\xef\xbf\xbd"}\n'


@pytest.fixture(autouse=True)
def _deterministic_release_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release unit testsを実worktreeのdirty状態から分離する。"""

    monkeypatch.setattr(
        gate,
        "_observe_release_source",
        lambda repo_root: {
            "source_root": str(Path(repo_root).resolve()),
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "worktree_clean": True,
        },
    )


def _run_candidate_git(repo: Path, *args: str) -> str:
    completed = gate.subprocess.run(
        ["git", *args],
        cwd=str(repo),
        stdin=gate.subprocess.DEVNULL,
        stdout=gate.subprocess.PIPE,
        stderr=gate.subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        shell=False,
    )
    assert completed.returncode == 0, (
        f"git {' '.join(args)} failed: {completed.stderr.strip()}"
    )
    return completed.stdout.strip()


def _make_staged_candidate_repo(tmp_path: Path, *, name: str = "candidate") -> dict[str, Any]:
    """baseline commit + fully staged candidateだけを持つ一時Git repoを作る。"""

    repo = tmp_path / name
    repo.mkdir()
    _run_candidate_git(repo, "init", "-b", "main")
    _run_candidate_git(repo, "config", "user.name", "News-Grasp fixture")
    _run_candidate_git(repo, "config", "user.email", "fixture@example.invalid")
    _run_candidate_git(repo, "config", "core.autocrlf", "false")
    tracked = repo / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _run_candidate_git(repo, "add", "tracked.txt")
    _run_candidate_git(repo, "commit", "-m", "baseline")
    baseline_head = _run_candidate_git(repo, "rev-parse", "HEAD")
    baseline_tree = _run_candidate_git(repo, "rev-parse", "HEAD^{tree}")

    tracked.write_text("candidate\n", encoding="utf-8")
    _run_candidate_git(repo, "add", "tracked.txt")
    candidate_tree = _run_candidate_git(repo, "write-tree")
    candidate = _REAL_OBSERVE_RELEASE_SOURCE(
        repo,
        allow_staged_candidate=True,
    )
    assert candidate["source_head"] == baseline_head
    assert candidate["source_tree"] == candidate_tree
    assert candidate["source_tree"] != baseline_tree
    return {
        "repo": repo,
        "tracked": tracked,
        "baseline_head": baseline_head,
        "baseline_tree": baseline_tree,
        "candidate_tree": candidate_tree,
        "candidate": candidate,
    }


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


def _node_report(nodes: list[str], *, statuses: list[str] | None = None) -> dict[str, Any]:
    """指定node集合だけを実行した構造化receiptを作るfixture。"""

    canonical_nodes = gate._node_list(nodes)
    effective_statuses = statuses or ["pass"] * len(canonical_nodes)
    assert len(effective_statuses) == len(canonical_nodes)
    return {
        "schemaVersion": gate.RELEASE_NODE_REPORT_SCHEMA,
        "kind": "run",
        "complete": True,
        "collection_complete": True,
        "collection_errors": [],
        "collection_nodes": canonical_nodes,
        "collection_count": len(canonical_nodes),
        "collection_sha256": gate._node_hash(canonical_nodes),
        "node_results": [
            {
                "node_id": node,
                "status": status,
                "events": [],
                "event_count": 0,
            }
            for node, status in zip(canonical_nodes, effective_statuses, strict=True)
        ],
        "node_result_count": len(canonical_nodes),
        "exit_code": 0 if all(status in {"pass", "skip"} for status in effective_statuses) else 1,
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
    source_root: Path | str | None = None,
) -> dict[str, Any]:
    resolved_source_root = (
        str(Path(source_root).resolve())
        if source_root is not None
        else str(Path(__file__).resolve().parents[1])
    )
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
        "source_root": resolved_source_root,
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "worktree_clean": True,
    }


def _seed_failed_partition(
    ledger_path: Path,
    *,
    release_id: str = "release-causal",
    partition: str = "scoped_changed",
    source_root: Path | str | None = None,
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
        source_root=source_root,
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


def _seed_causal_release_ledger(
    ledger_path: Path,
    *,
    release_id: str,
    target_green: bool = False,
) -> dict[str, Any]:
    """causal repair合成用のauthoritative collection/partition鎖を作るfixture。"""

    partition_nodes = {
        gate.RELEASE_PARTITIONS[0]: [
            "tests/test_news_grasp_daily_45m_contract.py::test_fixture_pass",
            "tests/test_news_grasp_daily_45m_contract.py::test_fixture_fail",
        ],
        gate.RELEASE_PARTITIONS[1]: [
            "tests/test_news_grasp_constitution_acceptance.py::test_fixture_pass",
        ],
        gate.RELEASE_PARTITIONS[2]: [
            "tests/test_historical_failure_scenarios.py::test_fixture_pass",
        ],
        gate.RELEASE_PARTITIONS[3]: [
            "tests/test_all_article_urls_live.py::test_fixture_pass",
        ],
        gate.RELEASE_PARTITIONS[4]: [
            "tests/test_2026_08_14_recovery_replay.py::test_fixture_pass",
        ],
        gate.RELEASE_PARTITIONS[5]: [
            "tests/test_news_grasp_release_gate_v2.py::test_fixture_pass",
        ],
    }
    collection_nodes = [
        node
        for partition in gate.RELEASE_PARTITIONS
        for node in partition_nodes[partition]
    ]
    classification = gate.validate_partition(collection_nodes, partition_nodes)
    collection_sha256 = classification["collection_sha256"]
    collection_receipt = {
        "schemaVersion": gate.RELEASE_COLLECTION_SCHEMA,
        "ok": True,
        "status": "collection_produced",
        "authority": "release_authoritative_collect_only",
        "release_id": release_id,
        "collection_nodes": list(collection_nodes),
        "collection_count": len(collection_nodes),
        "collection_sha256": collection_sha256,
        "node_set_hash": collection_sha256,
        "ledger_path": str(ledger_path),
        "source_root": str(Path(__file__).resolve().parents[1]),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "worktree_clean": True,
    }
    gate._append_ledger(
        ledger_path,
        "collection_completed",
        release_id=release_id,
        receipt=collection_receipt,
        receipt_hash=gate._mapping_hash(collection_receipt),
    )

    partition_receipts: dict[str, dict[str, Any]] = {}
    for partition in gate.RELEASE_PARTITIONS:
        nodes = list(partition_nodes[partition])
        receipt = _partition_receipt(
            release_id=release_id,
            partition=partition,
            nodes=nodes,
            collection_sha256=collection_sha256,
            receipt_id=f"partition-{partition}",
        )
        if partition != gate.RELEASE_PARTITIONS[0] or target_green:
            receipt["node_receipts"] = [
                {**row, "status": "pass"}
                for row in receipt["node_receipts"]
            ]
            receipt["failed_nodes"] = []
            receipt["exact_failed_set_sha256"] = gate._node_hash([])
            receipt["ok"] = True
            receipt["status"] = "green"
            receipt["failure"] = None
        partition_receipts[partition] = receipt
        gate._append_ledger(
            ledger_path,
            "partition_completed",
            release_id=release_id,
            partition=partition,
            identity=f"{release_id}:{partition}",
            collection_sha256=collection_sha256,
            receipt=receipt,
            receipt_hash=gate._mapping_hash(receipt),
        )

    return {
        "collection_nodes": collection_nodes,
        "classification": classification,
        "collection_receipt": collection_receipt,
        "partition_nodes": partition_nodes,
        "partition_receipts": partition_receipts,
        "target_partition": gate.RELEASE_PARTITIONS[0],
        "target_receipt": partition_receipts[gate.RELEASE_PARTITIONS[0]],
        "failed_node": partition_nodes[gate.RELEASE_PARTITIONS[0]][0],
        "collection_sha256": collection_sha256,
    }


def _green_repair_process(
    *,
    partition: str,
    nodes: list[str],
    release_id: str,
    collection_sha256: str,
    receipt_id: str,
) -> dict[str, Any]:
    """causal_repair_partitionへ渡す当日修復Green process fixture。"""

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
                "status": "pass",
                "events": [],
                "receipt_id": receipt_id,
            }
            for node in nodes
        ],
        "failed_nodes": [],
        "skipped_nodes": [],
        "exact_failed_set_sha256": gate._node_hash([]),
        "process_count": 1,
        "ok": True,
        "status": "green",
        "release_id": release_id,
        "collection_sha256": collection_sha256,
    }


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
    ["collection_nodes", "partitions", "runner", "pytest_args", "ledger_path"],
    ids=["fake-collection", "fake-partition", "runner-injection", "selector", "ledger-override"],
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


def test_NG_RG_03_production_ledger_override_is_rejected_before_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    starts: list[object] = []
    monkeypatch.setattr(gate.subprocess, "run", lambda *args, **kwargs: starts.append(args))

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_production_ledger_override_forbidden"):
        gate.collect_only_nodes(
            tmp_path,
            ledger_path=tmp_path / "caller-ledger.jsonl",
            release_id="release-ledger-override",
        )
    assert starts == []


def test_NG_RG_03_fabricated_collection_mapping_is_not_release_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "canonical" / "release_ledger.jsonl"
    monkeypatch.setattr(gate, "_canonical_ledger_path", lambda: ledger)
    test_module = tmp_path / "tests" / "test_fixture_release.py"
    test_module.parent.mkdir(parents=True)
    test_module.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    node = "tests/test_fixture_release.py::test_ok"
    receipt = {
        "schemaVersion": gate.RELEASE_COLLECTION_SCHEMA,
        "ok": True,
        "status": "collection_produced",
        "authority": "release_authoritative_collect_only",
        "release_id": "release-fabricated",
        "collection_nodes": [node],
        "collection_sha256": gate._node_hash([node]),
        "ledger_path": str(ledger),
        "source_root": str(tmp_path.resolve()),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "worktree_clean": True,
    }

    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="release_authoritative_collection_ledger_binding_invalid",
    ):
        gate.execute_partitioned_nodes(
            [node],
            repo_root=tmp_path,
            collection_receipt=receipt,
            release_id="release-fabricated",
        )


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
    previous, nodes = _seed_failed_partition(ledger, source_root=tmp_path)
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


def test_NG_RG_06_red_causal_repair_chains_only_its_remaining_failed_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    previous, nodes = _seed_failed_partition(ledger, source_root=tmp_path)
    calls: list[list[str]] = []

    def fake_partition_process(name: str, selected: list[str], **kwargs: Any) -> dict[str, Any]:
        calls.append(list(selected))
        status = "fail" if len(calls) == 1 else "pass"
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
            "receipt_id": str(kwargs["operation_id"]),
            "partition": name,
            "node_ids": list(selected),
            "node_count": len(selected),
            "node_receipts": [
                {
                    "node_id": selected[0],
                    "partition": name,
                    "status": status,
                    "events": [],
                    "receipt_id": str(kwargs["operation_id"]),
                }
            ],
            "failed_nodes": [selected[0]] if status == "fail" else [],
            "skipped_nodes": [],
            "process_count": 1,
            "ok": status == "pass",
            "status": "green" if status == "pass" else "red",
        }

    monkeypatch.setattr(gate, "_run_partition_process", fake_partition_process)
    first = gate.causal_repair_partition(
        repo_root=tmp_path,
        partition="scoped_changed",
        node_ids=[nodes[0]],
        cause_hash="2" * 64,
        previous_receipt=previous,
        repair_id="repair-red-first",
        release_id="release-causal",
        ledger_path=ledger,
    )
    assert first["ok"] is False

    second = gate.causal_repair_partition(
        repo_root=tmp_path,
        partition="scoped_changed",
        node_ids=[nodes[0]],
        cause_hash="3" * 64,
        previous_receipt=first,
        repair_id="repair-green-second",
        release_id="release-causal",
        ledger_path=ledger,
    )

    assert second["ok"] is True
    assert second["exact_failed_nodes"] == [nodes[0]]
    assert calls == [[nodes[0]], [nodes[0]]]


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
    previous, nodes = _seed_failed_partition(ledger, source_root=tmp_path)
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


def test_NG_RG_10_canonical_ledger_uses_known_folder_mac_not_localappdata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trusted_local = tmp_path / "known-folder"
    trusted_local.mkdir()
    monkeypatch.setattr(gate, "_known_folder_local_app_data", lambda: trusted_local)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "attacker-env"))
    ledger = gate._canonical_ledger_path()

    gate._append_ledger(ledger, "fixture_mac", release_id="release-mac")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert len(rows[0]["event_mac"]) == 64
    assert str(ledger).startswith(str(trusted_local))
    assert "attacker-env" not in str(ledger)
    rows[0]["event_mac"] = "0" * 64
    ledger.write_text(json.dumps(rows[0], separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_ledger_event_mac_invalid"):
        gate._ledger_events(ledger)


def test_NG_RG_10_parallel_ledger_append_has_one_valid_hash_chain(tmp_path: Path) -> None:
    ledger = tmp_path / "parallel" / "release_ledger.jsonl"

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: gate._append_ledger(
                    ledger,
                    "parallel_fixture",
                    release_id=f"release-{index}",
                ),
                range(24),
            )
        )

    events = gate._ledger_events(ledger)
    assert len(events) == 24
    assert len({event["event_hash"] for event in events}) == 24


def test_NG_RG_11_green_release_event_issues_exactly_one_daily_promotion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    ledger = tmp_path / "canonical" / "release_ledger.jsonl"
    state_root = tmp_path / "direct-mainline"
    monkeypatch.setattr(gate, "_canonical_ledger_path", lambda: ledger)
    monkeypatch.setattr(gate, "_canonical_daily_state_root", lambda: state_root)
    node = "tests/test_news_grasp_release_gate_v2.py::test_NG_RG_01_authoritative_collection_count_mismatch_is_red"
    release_id = "release-promotion-authority"
    monkeypatch.setattr(
        gate,
        "_run_fixed_pytest",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": True,
            "status": "green",
            "transport": "ok",
            "exit_code": 0,
            "structured": _collection_report([node]),
        },
    )
    collection = gate.collect_only_nodes(repo, release_id=release_id)
    assert collection["ok"] is True

    def green_partition(name: str, nodes: list[str], **_kwargs: Any) -> dict[str, Any]:
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PARTITION_PROCESS_RECEIPT_V2",
            "receipt_id": "partition-green",
            "partition": name,
            "node_ids": list(nodes),
            "node_count": len(nodes),
            "node_receipts": [
                {
                    "node_id": item,
                    "partition": name,
                    "status": "pass",
                    "events": [],
                    "receipt_id": "partition-green",
                }
                for item in nodes
            ],
            "failed_nodes": [],
            "skipped_nodes": [],
            "exact_failed_set_sha256": gate._node_hash([]),
            "process_count": 1,
            "ok": True,
            "status": "green",
        }

    monkeypatch.setattr(gate, "_run_partition_process", green_partition)
    first = gate.execute_partitioned_nodes(
        [node],
        repo_root=repo,
        collection_receipt=collection,
        release_id=release_id,
    )
    second = gate.execute_partitioned_nodes(
        [node],
        repo_root=repo,
        collection_receipt=collection,
        release_id=release_id,
    )

    assert first["ok"] is True
    assert second["status"] == "release_attached"
    events = gate._ledger_events(ledger)
    promotions = [event for event in events if event["event_type"] == "daily_promotion_issued"]
    assert len(promotions) == 1
    promotion_path = state_root / "promotion" / "daily-scoped-promotion.json"
    assert promotion_path.is_file()
    tampered = json.loads(promotion_path.read_text(encoding="utf-8"))
    tampered["release_id"] = "tampered-release"
    promotion_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="daily_promotion_existing_receipt_invalid",
    ):
        gate.execute_partitioned_nodes(
            [node],
            repo_root=repo,
            collection_receipt=collection,
            release_id=release_id,
        )


def test_NG_RG_12_many_nodes_use_bounded_process_chunks_and_execute_each_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Windows command-line上限を越えるnode集合を一括spawnせず分割する。"""

    nodes = [
        f"tests/test_release.py::test_param_{index}[{'x' * 180}]"
        for index in range(200)
    ]
    observed_chunks: list[list[str]] = []

    def fake_run_fixed_pytest(**kwargs: Any) -> dict[str, Any]:
        chunk = list(kwargs["node_ids"])
        observed_chunks.append(chunk)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": True,
            "status": "green",
            "transport": "ok",
            "exit_code": 0,
            "structured": _node_report(chunk),
        }

    monkeypatch.setattr(gate, "_run_fixed_pytest", fake_run_fixed_pytest)
    result = gate._run_partition_process(
        "general_complement",
        nodes,
        repo_root=tmp_path,
        timeout_seconds=5,
        operation_id="release-bounded-chunks",
    )

    assert result["ok"] is True
    assert len(observed_chunks) > 1
    flattened = [node for chunk in observed_chunks for node in chunk]
    assert flattened == nodes
    assert len(flattened) == len(set(flattened))
    for chunk in observed_chunks:
        assert 0 < len(chunk) <= gate.RELEASE_PROCESS_NODE_LIMIT
        assert sum(len(node) + 1 for node in chunk) <= gate.RELEASE_PROCESS_SELECTOR_CHAR_LIMIT
    assert result["process_count"] == len(observed_chunks)
    assert [row["node_id"] for row in result["node_receipts"]] == nodes


def test_NG_RG_12_partition_deadline_stops_future_spawns_and_binds_remaining_nodes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    nodes = [
        "tests/test_release.py::test_first",
        "tests/test_release.py::test_second",
    ]
    calls: list[list[str]] = []
    clock = iter((0.0, 0.0, 6.0))
    monkeypatch.setattr(gate, "RELEASE_PROCESS_NODE_LIMIT", 1)
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock))

    def fake_run_fixed_pytest(**kwargs: Any) -> dict[str, Any]:
        chunk = list(kwargs["node_ids"])
        calls.append(chunk)
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": True,
            "status": "green",
            "transport": "ok",
            "exit_code": 0,
            "structured": _node_report(chunk),
        }

    monkeypatch.setattr(gate, "_run_fixed_pytest", fake_run_fixed_pytest)
    result = gate._run_partition_process(
        "general_complement",
        nodes,
        repo_root=tmp_path,
        timeout_seconds=5,
        operation_id="release-partition-deadline",
    )

    assert calls == [[nodes[0]]]
    assert result["ok"] is False
    assert result["process_count"] == 1
    assert result["failed_nodes"] == [nodes[1]]
    assert [row["node_id"] for row in result["node_receipts"]] == nodes
    assert result["node_receipts"][1]["failure"] == "partition_timeout_before_chunk_1"


def test_NG_RG_13_parametrized_address_identity_survives_module_deselection_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """param reprのaddress変動を吸収し、指定nodeだけを一度だけ実行する。"""

    collected = "tests/test_release.py::test_param[value at 0xAAA]"
    executed = "tests/test_release.py::test_param[value at 0xBBB]"
    other = "tests/test_release.py::test_other"
    allowed_path = tmp_path / "allowed-nodes.json"
    allowed_path.write_text(json.dumps({"nodes": [collected]}), encoding="utf-8")
    monkeypatch.setenv("NEWS_GRASP_RELEASE_ALLOWED_NODES_FILE", str(allowed_path))

    output = tmp_path / "structured.json"
    plugin = gate._StructuredPytestPlugin(output, "run")
    items = [_FakeItem(collected), _FakeItem(other)]
    deselected: list[_FakeItem] = []

    def record_deselected(*, items: list[_FakeItem]) -> None:
        deselected.extend(items)

    config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=record_deselected))
    plugin.pytest_collection_modifyitems(SimpleNamespace(), config, items)
    assert [item.nodeid for item in items] == [collected]
    assert [item.nodeid for item in deselected] == [other]

    plugin.pytest_collection_finish(SimpleNamespace(items=items))
    plugin.pytest_runtest_logreport(_FakeReport(executed, when="call", passed=True))
    plugin.pytest_sessionfinish(SimpleNamespace(items=items), 0)

    payload = json.loads(output.read_text(encoding="utf-8"))
    expected = gate._canonical_node_list([collected])
    rows = gate._validate_node_report(payload, expected)
    assert gate._canonical_node_list([collected]) == gate._canonical_node_list([executed])
    assert payload["collection_count"] == 1
    assert len(rows) == 1
    assert rows[0]["node_id"] == expected[0]
    assert rows[0]["status"] == "pass"
    assert rows[0]["event_count"] == 1


@pytest.mark.parametrize("fault", ["transport", "collection"], ids=["transport", "collection-mismatch"])
def test_NG_RG_14_partition_transport_or_collection_red_binds_all_nodes_for_causal_repair(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """receipt欠落時もexpected全nodeをRed化し、repair対象をその集合に固定する。"""

    release_id = f"release-partition-fault-{fault}"
    partition = "scoped_changed"
    nodes = [
        "tests/test_release.py::test_transport_first",
        "tests/test_release.py::test_transport_second",
    ]
    ledger = tmp_path / "release-ledger.jsonl"
    observed: list[list[str]] = []
    collection_receipt = {
        "schemaVersion": gate.RELEASE_COLLECTION_SCHEMA,
        "ok": True,
        "status": "collection_produced",
        "authority": "release_authoritative_collect_only",
        "release_id": release_id,
        "collection_nodes": list(nodes),
        "collection_count": len(nodes),
        "collection_sha256": gate._node_hash(nodes),
        "node_set_hash": gate._node_hash(nodes),
        "ledger_path": str(ledger),
        "source_root": str(tmp_path.resolve()),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "worktree_clean": True,
    }
    gate._append_ledger(
        ledger,
        "collection_completed",
        release_id=release_id,
        receipt=collection_receipt,
        receipt_hash=gate._mapping_hash(collection_receipt),
    )

    def fake_run_fixed_pytest(**kwargs: Any) -> dict[str, Any]:
        chunk = list(kwargs["node_ids"])
        observed.append(chunk)
        if len(observed) == 1 and fault == "transport":
            return {
                "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
                "ok": False,
                "status": "transport_timeout",
                "transport": "timeout",
                "exit_code": None,
                "failures": ["release_process_timeout"],
            }
        if len(observed) == 1:
            invalid = _node_report(chunk)
            invalid["collection_nodes"] = [chunk[0]]
            invalid["collection_count"] = 1
            invalid["collection_sha256"] = gate._node_hash([chunk[0]])
            return {
                "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
                "ok": True,
                "status": "green",
                "transport": "ok",
                "exit_code": 0,
                "structured": invalid,
            }
        return {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": True,
            "status": "green",
            "transport": "ok",
            "exit_code": 0,
            "structured": _node_report(chunk),
        }

    monkeypatch.setattr(gate, "_run_fixed_pytest", fake_run_fixed_pytest)
    first = gate._partition_process_receipt(
        partition,
        nodes,
        repo_root=tmp_path,
        timeout_seconds=5,
        release_id=release_id,
        collection_sha256=gate._node_hash(nodes),
        ledger_path=ledger,
    )

    assert first["ok"] is False
    assert first["failed_nodes"] == nodes
    assert [row["node_id"] for row in first["node_receipts"]] == nodes
    assert first["exact_failed_set_sha256"] == gate._node_hash(nodes)

    repair = gate.causal_repair_partition(
        repo_root=tmp_path,
        partition=partition,
        node_ids=first["failed_nodes"],
        cause_hash="2" * 64,
        previous_receipt=first,
        repair_id=f"repair-{fault}",
        release_id=release_id,
        ledger_path=ledger,
        timeout_seconds=5,
    )

    assert repair["ok"] is True
    assert repair["exact_failed_nodes"] == nodes
    assert observed == [nodes, nodes]


def test_NG_RG_15_finalize_causal_repairs_composes_green_chain_without_rerunning_successes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """既存passを再実行せず、causal repairだけを合成してReleaseを完了する。"""

    release_id = "release-finalize-causal-green"
    ledger = tmp_path / "release-ledger.jsonl"
    repo_root = Path(__file__).resolve().parents[1]
    fixture = _seed_causal_release_ledger(ledger, release_id=release_id)
    repair_calls: list[list[str]] = []

    def green_repair(name: str, nodes: list[str], **_kwargs: Any) -> dict[str, Any]:
        repair_calls.append(list(nodes))
        return _green_repair_process(
            partition=name,
            nodes=list(nodes),
            release_id=release_id,
            collection_sha256=fixture["collection_sha256"],
            receipt_id="repair-process-green",
        )

    monkeypatch.setattr(gate, "_run_partition_process", green_repair)
    repair = gate.causal_repair_partition(
        repo_root=repo_root,
        partition=fixture["target_partition"],
        node_ids=[fixture["failed_node"]],
        cause_hash="2" * 64,
        previous_receipt=fixture["target_receipt"],
        repair_id="repair-finalize-green",
        release_id=release_id,
        ledger_path=ledger,
        timeout_seconds=5,
    )
    assert repair["ok"] is True
    assert repair_calls == [[fixture["failed_node"]]]
    before_finalize_calls = list(repair_calls)

    result = gate.finalize_causal_repairs(
        repo_root=repo_root,
        release_id=release_id,
        ledger_path=ledger,
        _test_only_allow_ledger_override=True,
    )
    release_receipt = (
        result["receipt"]
        if result.get("event_type") == "release_completed"
        else result
    )

    assert repair_calls == before_finalize_calls
    assert release_receipt["release_id"] == release_id
    assert release_receipt["ok"] is True
    assert release_receipt["status"] == "green"
    node_ids = [row["node_id"] for row in release_receipt["node_receipts"]]
    assert node_ids == fixture["collection_nodes"]
    assert len(node_ids) == len(set(node_ids)) == len(fixture["collection_nodes"])
    assert release_receipt["failed_nodes"] == []
    assert fixture["partition_nodes"][fixture["target_partition"]][1] not in repair_calls
    events = gate._ledger_events(ledger)
    completed = [event for event in events if event["event_type"] == "release_completed"]
    assert len(completed) == 1
    assert completed[0]["receipt"]["ok"] is True
    assert not [event for event in events if event["event_type"] == "daily_promotion_issued"]


@pytest.mark.parametrize("repair_state", ["red", "missing"], ids=["repair-red", "repair-missing"])
def test_NG_RG_16_finalize_causal_repairs_rejects_incomplete_red_chain(
    repair_state: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """repair Redまたは未完了のchainからGreen releaseを合成しない。"""

    release_id = f"release-finalize-causal-{repair_state}"
    ledger = tmp_path / "release-ledger.jsonl"
    repo_root = Path(__file__).resolve().parents[1]
    fixture = _seed_causal_release_ledger(ledger, release_id=release_id)

    if repair_state == "red":
        def red_repair(name: str, nodes: list[str], **_kwargs: Any) -> dict[str, Any]:
            result = _green_repair_process(
                partition=name,
                nodes=list(nodes),
                release_id=release_id,
                collection_sha256=fixture["collection_sha256"],
                receipt_id="repair-process-red",
            )
            result["ok"] = False
            result["status"] = "red"
            result["failed_nodes"] = list(nodes)
            result["failure"] = "fixture_repair_red"
            result["node_receipts"] = [
                {**row, "status": "fail"}
                for row in result["node_receipts"]
            ]
            return result

        monkeypatch.setattr(gate, "_run_partition_process", red_repair)
        repair = gate.causal_repair_partition(
            repo_root=repo_root,
            partition=fixture["target_partition"],
            node_ids=[fixture["failed_node"]],
            cause_hash="2" * 64,
            previous_receipt=fixture["target_receipt"],
            repair_id=f"repair-finalize-{repair_state}",
            release_id=release_id,
            ledger_path=ledger,
            timeout_seconds=5,
        )
        assert repair["ok"] is False

    with pytest.raises(gate.NewsGraspReleaseGateError):
        gate.finalize_causal_repairs(
            repo_root=repo_root,
            release_id=release_id,
            ledger_path=ledger,
            _test_only_allow_ledger_override=True,
        )

    events = gate._ledger_events(ledger)
    assert not [event for event in events if event["event_type"] == "release_completed"]
    assert not [event for event in events if event["event_type"] == "daily_promotion_issued"]


def test_NG_RG_17_finalize_causal_repairs_rejects_forked_repair_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """同一previous_receipt_idから分岐したrepairをRelease authorityが拒否する。"""

    release_id = "release-finalize-causal-fork"
    ledger = tmp_path / "release-ledger.jsonl"
    repo_root = Path(__file__).resolve().parents[1]
    fixture = _seed_causal_release_ledger(ledger, release_id=release_id)

    def green_repair(name: str, nodes: list[str], **_kwargs: Any) -> dict[str, Any]:
        return _green_repair_process(
            partition=name,
            nodes=list(nodes),
            release_id=release_id,
            collection_sha256=fixture["collection_sha256"],
            receipt_id="repair-process-fork",
        )

    monkeypatch.setattr(gate, "_run_partition_process", green_repair)
    repair = gate.causal_repair_partition(
        repo_root=repo_root,
        partition=fixture["target_partition"],
        node_ids=[fixture["failed_node"]],
        cause_hash="2" * 64,
        previous_receipt=fixture["target_receipt"],
        repair_id="repair-fork-first",
        release_id=release_id,
        ledger_path=ledger,
        timeout_seconds=5,
    )
    assert repair["ok"] is True

    fork = dict(repair)
    fork["repair_id"] = "repair-fork-second"
    gate._append_ledger(
        ledger,
        "repair_completed",
        release_id=release_id,
        partition=fixture["target_partition"],
        identity=fork["repair_id"],
        repair_id=fork["repair_id"],
        previous_receipt_id=repair["previous_receipt_id"],
        cause_hash=fork["cause_hash"],
        receipt=fork,
        receipt_hash=gate._mapping_hash(fork),
    )

    with pytest.raises(gate.NewsGraspReleaseGateError):
        gate.finalize_causal_repairs(
            repo_root=repo_root,
            release_id=release_id,
            ledger_path=ledger,
            _test_only_allow_ledger_override=True,
        )

    events = gate._ledger_events(ledger)
    assert not [event for event in events if event["event_type"] == "release_completed"]
    assert not [event for event in events if event["event_type"] == "daily_promotion_issued"]


def test_NG_RG_18_canonical_repair_authority_rejects_generic_and_startless_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """repair authorityは専用writerだけがstarted→completed順で発行できる。"""

    ledger = tmp_path / "canonical" / "release-ledger.jsonl"
    monkeypatch.setattr(gate, "_canonical_ledger_path", lambda: ledger)
    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="release_authority_event_generic_append_forbidden",
    ):
        gate._append_ledger(
            ledger,
            "repair_completed",
            release_id="release-startless-repair",
            partition="scoped_changed",
            repair_id="repair-startless",
            previous_receipt_id="partition-base",
            receipt={},
            receipt_hash=gate._mapping_hash({}),
        )


def test_NG_RG_19_collection_seals_source_and_rejects_collection_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """collection前後のHEAD/treeが変わればGreen receiptを発行しない。"""

    repo = Path(__file__).resolve().parents[1]
    node = "tests/test_news_grasp_release_gate_v2.py::test_NG_RG_01_authoritative_collection_count_mismatch_is_red"
    monkeypatch.setattr(
        gate,
        "_run_fixed_pytest",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_RELEASE_PROCESS_RECEIPT_V1",
            "ok": True,
            "status": "green",
            "transport": "ok",
            "exit_code": 0,
            "structured": _collection_report([node]),
        },
    )
    observed = iter(
        (
            {
                "source_root": str(repo.resolve()),
                "source_head": "a" * 40,
                "source_tree": "b" * 40,
                "worktree_clean": True,
            },
            {
                "source_root": str(repo.resolve()),
                "source_head": "c" * 40,
                "source_tree": "d" * 40,
                "worktree_clean": True,
            },
        )
    )
    monkeypatch.setattr(gate, "_observe_release_source", lambda _root: next(observed))
    receipt = gate.collect_only_nodes(
        repo,
        release_id="release-source-drift",
        ledger_path=tmp_path / "release-ledger.jsonl",
        _test_only_allow_ledger_override=True,
    )
    assert receipt["ok"] is False
    assert receipt["status"] == "red"
    assert receipt["failures"] == ["release_source_drift"]
    assert receipt["source_head"] == "a" * 40
    assert receipt["source_tree"] == "b" * 40


@pytest.mark.parametrize(
    "observed,reason",
    [
        (
            {
                "source_root": str(Path(__file__).resolve().parents[1]),
                "source_head": "c" * 40,
                "source_tree": "b" * 40,
                "worktree_clean": True,
            },
            "release_source_binding_mismatch",
        ),
        (None, "release_source_worktree_dirty"),
    ],
    ids=["different-head", "dirty"],
)
def test_NG_RG_20_finalize_rejects_current_source_mismatch_or_dirty(
    observed: dict[str, Any] | None,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """completion時sourceがcollection sealと異なる場合はreleaseを作らない。"""

    release_id = f"release-source-{reason}"
    ledger = tmp_path / "release-ledger.jsonl"
    repo = Path(__file__).resolve().parents[1]
    _seed_causal_release_ledger(ledger, release_id=release_id, target_green=True)
    if observed is None:
        def observe(_root: Path) -> dict[str, Any]:
            raise gate.NewsGraspReleaseGateError("release_source_worktree_dirty")
    else:
        def observe(_root: Path) -> dict[str, Any]:
            return dict(observed)
    monkeypatch.setattr(gate, "_observe_release_source", observe)
    with pytest.raises(gate.NewsGraspReleaseGateError, match=reason):
        gate.finalize_causal_repairs(
            repo_root=repo,
            release_id=release_id,
            ledger_path=ledger,
            _test_only_allow_ledger_override=True,
        )
    assert not [
        event for event in gate._ledger_events(ledger)
        if event.get("event_type") == "release_completed"
    ]


def test_NG_RG_21_promotion_rejects_release_source_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """promotion時のsourceをRelease試験時sourceへ必ず束縛する。"""

    repo = Path(__file__).resolve().parents[1]
    ledger = tmp_path / "canonical" / "release-ledger.jsonl"
    receipt = {
        "schemaVersion": gate.RELEASE_GATE_SCHEMA,
        "ok": True,
        "status": "green",
        "release_id": "release-promotion-source-mismatch",
        "failed_nodes": [],
        "executed_node_count": 1,
        "union_node_count": 1,
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "source_root": str(repo.resolve()),
        "worktree_clean": True,
    }
    release_event = {
        "event_type": "release_completed",
        "release_id": receipt["release_id"],
        "event_hash": "e" * 64,
        "receipt": receipt,
        "receipt_hash": gate._mapping_hash(receipt),
    }
    monkeypatch.setattr(gate, "_canonical_ledger_path", lambda: ledger)
    monkeypatch.setattr(gate, "_ledger_events", lambda _ledger: [release_event])
    monkeypatch.setattr(
        gate,
        "_validate_release_completion_chain",
        lambda *_args, **_kwargs: {"green": True},
    )
    monkeypatch.setattr(
        gate,
        "_observe_release_source",
        lambda _root: {
            "source_root": str(repo.resolve()),
            "source_head": "c" * 40,
            "source_tree": "b" * 40,
            "worktree_clean": True,
        },
    )
    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="release_source_binding_mismatch",
    ):
        gate._ensure_daily_promotion(
            repo_root=repo,
            ledger=ledger,
            release_event=release_event,
            release_receipt=receipt,
        )


def test_NG_RG_22_staged_candidate_observation_binds_head_and_index_tree(
    tmp_path: Path,
) -> None:
    """完全stage済みcandidateはbaseline HEADとindex treeへ束縛する。"""

    fixture = _make_staged_candidate_repo(tmp_path, name="candidate-observation")
    candidate = fixture["candidate"]

    assert candidate["source_mode"] == "staged_candidate"
    assert candidate["source_head"] == fixture["baseline_head"]
    assert candidate["source_tree"] == fixture["candidate_tree"]
    assert candidate["worktree_exact"] is True
    assert candidate["worktree_clean"] is False


@pytest.mark.parametrize(
    "candidate_change",
    ["unstaged", "untracked"],
    ids=["unstaged-candidate", "untracked-candidate"],
)
def test_NG_RG_23_staged_candidate_rejects_unstaged_or_untracked_input(
    candidate_change: str,
    tmp_path: Path,
) -> None:
    """candidate観測は意図外のunstaged/untracked差分を受理しない。"""

    fixture = _make_staged_candidate_repo(tmp_path, name=f"candidate-{candidate_change}")
    repo = fixture["repo"]
    if candidate_change == "unstaged":
        _run_candidate_git(repo, "reset", "HEAD", "--", "tracked.txt")
        expected = "release_source_unstaged_candidate_forbidden"
    else:
        (repo / "untracked.txt").write_text("outside-candidate\n", encoding="utf-8")
        expected = "release_source_untracked_candidate_forbidden"

    with pytest.raises(gate.NewsGraspReleaseGateError, match=expected):
        _REAL_OBSERVE_RELEASE_SOURCE(repo, allow_staged_candidate=True)


def test_NG_RG_24_candidate_receipt_is_not_promotable_before_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """未commitのcandidate receiptをpromotion境界へ持ち込めない。"""

    fixture = _make_staged_candidate_repo(tmp_path, name="candidate-before-promotion")
    repo = fixture["repo"]
    monkeypatch.setattr(gate, "_observe_release_source", _REAL_OBSERVE_RELEASE_SOURCE)

    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="release_source_worktree_dirty",
    ):
        gate._require_release_source_promotable(fixture["candidate"], repo)
    assert _run_candidate_git(repo, "rev-parse", "HEAD") == fixture["baseline_head"]


@pytest.mark.parametrize(
    "commit_shape",
    ["one-commit-green", "different-tree", "different-parent"],
    ids=["one-commit-green", "candidate-tree-mismatch", "candidate-parent-mismatch"],
)
def test_NG_RG_25_candidate_commit_requires_exact_tree_and_baseline_parent(
    commit_shape: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """candidate一回commit後だけを同一tree・baseline parentへpromotionできる。"""

    fixture = _make_staged_candidate_repo(tmp_path, name=f"candidate-{commit_shape}")
    repo = fixture["repo"]
    tracked = fixture["tracked"]
    candidate = fixture["candidate"]
    _run_candidate_git(repo, "commit", "-m", "candidate")

    if commit_shape == "one-commit-green":
        monkeypatch.setattr(gate, "_observe_release_source", _REAL_OBSERVE_RELEASE_SOURCE)
        promoted = gate._require_release_source_promotable(candidate, repo)
        assert promoted["source_mode"] == "committed"
        assert promoted["source_head"] == _run_candidate_git(repo, "rev-parse", "HEAD")
        assert promoted["source_tree"] == candidate["source_tree"]
        assert _run_candidate_git(repo, "rev-parse", "HEAD^") == fixture["baseline_head"]
        return

    if commit_shape == "different-tree":
        tracked.write_text("different-candidate-tree\n", encoding="utf-8")
        _run_candidate_git(repo, "add", "tracked.txt")
        _run_candidate_git(repo, "commit", "-m", "different candidate tree")
        expected = "release_candidate_tree_mismatch"
    else:
        _run_candidate_git(repo, "commit", "--allow-empty", "-m", "different parent")
        expected = "release_candidate_parent_mismatch"

    monkeypatch.setattr(gate, "_observe_release_source", _REAL_OBSERVE_RELEASE_SOURCE)
    with pytest.raises(gate.NewsGraspReleaseGateError, match=expected):
        gate._require_release_source_promotable(candidate, repo)


def test_NG_RG_26_candidate_index_tree_drift_is_rejected_by_source_match(
    tmp_path: Path,
) -> None:
    """candidate観測後にindex treeが変化した場合は同一sourceと見なさない。"""

    fixture = _make_staged_candidate_repo(tmp_path, name="candidate-index-drift")
    repo = fixture["repo"]
    tracked = fixture["tracked"]
    candidate = fixture["candidate"]
    tracked.write_text("changed-after-observation\n", encoding="utf-8")
    _run_candidate_git(repo, "add", "tracked.txt")
    changed = _REAL_OBSERVE_RELEASE_SOURCE(repo, allow_staged_candidate=True)

    assert changed["source_mode"] == "staged_candidate"
    assert changed["source_tree"] != candidate["source_tree"]
    with pytest.raises(
        gate.NewsGraspReleaseGateError,
        match="release_source_binding_mismatch",
    ):
        gate._require_release_source_match(candidate, changed)


def _seed_expected_red_legacy_prior_candidate_release(
    ledger_path: Path,
    *,
    repo_root: Path,
    release_id: str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """旧prior branchを再現するcandidate source付きGreen release fixture。"""

    partition_nodes = {
        gate.RELEASE_PARTITIONS[0]: [
            "tests/test_news_grasp_daily_45m_contract.py::test_fixture_candidate_pass",
        ],
        gate.RELEASE_PARTITIONS[1]: [
            "tests/test_news_grasp_constitution_acceptance.py::test_fixture_candidate_pass",
        ],
        gate.RELEASE_PARTITIONS[2]: [
            "tests/test_historical_failure_scenarios.py::test_fixture_candidate_pass",
        ],
        gate.RELEASE_PARTITIONS[3]: [
            "tests/test_all_article_urls_live.py::test_fixture_candidate_pass",
        ],
        gate.RELEASE_PARTITIONS[4]: [
            "tests/test_2026_08_14_recovery_replay.py::test_fixture_candidate_pass",
        ],
        gate.RELEASE_PARTITIONS[5]: [
            "tests/test_news_grasp_release_gate_v2.py::test_fixture_candidate_pass",
        ],
    }
    collection_nodes = [
        node
        for partition in gate.RELEASE_PARTITIONS
        for node in partition_nodes[partition]
    ]
    classification = gate.validate_partition(collection_nodes, partition_nodes)
    collection_sha256 = classification["collection_sha256"]
    collection_receipt = {
        "schemaVersion": gate.RELEASE_COLLECTION_SCHEMA,
        "ok": True,
        "status": "collection_produced",
        "authority": "release_authoritative_collect_only",
        "release_id": release_id,
        "collection_nodes": list(collection_nodes),
        "collection_count": len(collection_nodes),
        "collection_sha256": collection_sha256,
        "node_set_hash": collection_sha256,
        "ledger_path": str(ledger_path),
        **source,
    }
    gate._append_ledger_locked(
        ledger_path,
        "collection_completed",
        release_id=release_id,
        repo_root=str(repo_root),
        receipt=collection_receipt,
        receipt_hash=gate._mapping_hash(collection_receipt),
        **source,
    )

    partition_receipts: list[dict[str, Any]] = []
    node_receipts: list[dict[str, Any]] = []
    for partition in gate.RELEASE_PARTITIONS:
        nodes = list(partition_nodes[partition])
        receipt = _partition_receipt(
            release_id=release_id,
            partition=partition,
            nodes=nodes,
            collection_sha256=collection_sha256,
            receipt_id=f"prior-{partition}",
        )
        receipt["node_receipts"] = [
            {**row, "status": "pass"}
            for row in receipt["node_receipts"]
        ]
        receipt.update(
            {
                "failed_nodes": [],
                "exact_failed_set_sha256": gate._node_hash([]),
                "ok": True,
                "status": "green",
                "failure": None,
                **source,
            }
        )
        partition_receipts.append(receipt)
        node_receipts.extend(receipt["node_receipts"])
        gate._append_ledger_locked(
            ledger_path,
            "partition_completed",
            release_id=release_id,
            partition=partition,
            identity=f"{release_id}:{partition}",
            collection_sha256=collection_sha256,
            receipt=receipt,
            receipt_hash=gate._mapping_hash(receipt),
            **source,
        )

    release_receipt = {
        "schemaVersion": gate.RELEASE_GATE_SCHEMA,
        "ok": True,
        "status": "green",
        "partition": classification,
        "node_receipts": node_receipts,
        "partition_receipts": partition_receipts,
        "executed_node_count": len(collection_nodes),
        "executed_process_count": len(collection_nodes),
        "finalization_process_count": 0,
        "union_node_count": len(collection_nodes),
        "failed_nodes": [],
        "collection_sha256": collection_sha256,
        "release_id": release_id,
        "ledger_path": str(ledger_path),
        **source,
    }
    collection_event = gate._latest_event(
        gate._ledger_events(ledger_path),
        release_id=release_id,
        event_type="collection_completed",
    )
    assert collection_event is not None
    release_event = gate._append_ledger_locked(
        ledger_path,
        "release_completed",
        release_id=release_id,
        collection_sha256=collection_sha256,
        collection_event_hash=str(collection_event["event_hash"]),
        partition_receipt_hashes=[gate._mapping_hash(item) for item in partition_receipts],
        receipt=release_receipt,
        receipt_hash=gate._mapping_hash(release_receipt),
        **source,
    )
    return {
        "collection_nodes": collection_nodes,
        "collection_receipt": collection_receipt,
        "partition_receipts": partition_receipts,
        "release_receipt": release_receipt,
        "release_event": release_event,
        "collection_sha256": collection_sha256,
    }


def test_NG_RG_27_authoritative_repair_is_single_capability_without_caller_green(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expected Red固定事実: 旧実装はrepair writer globalsを公開していた。"""

    assert not hasattr(gate, "_record_authoritative_repair_started")
    assert not hasattr(gate, "_record_authoritative_repair_completed")
    parameters = inspect.signature(gate._run_authoritative_repair).parameters
    assert "receipt" not in parameters
    assert "green_receipt" not in parameters
    assert "caller_receipt" not in parameters
    assert "result" not in parameters

    repo_root = Path(__file__).resolve().parents[1]
    ledger = tmp_path / "repair-authority.jsonl"
    release_id = "release-authoritative-repair-capability"
    partition = "scoped_changed"
    nodes = ["tests/test_news_grasp_release_gate_v2.py::test_fixture_authoritative_repair"]
    source = {
        "source_root": str(repo_root.resolve()),
        "source_head": "a" * 40,
        "source_tree": "b" * 40,
        "source_mode": "committed",
        "worktree_clean": True,
        "worktree_exact": True,
    }
    process_calls: list[dict[str, Any]] = []

    def green_process(name: str, selected: list[str], **kwargs: Any) -> dict[str, Any]:
        process_calls.append({"partition": name, "nodes": list(selected), **kwargs})
        return _green_repair_process(
            partition=name,
            nodes=list(selected),
            release_id=release_id,
            collection_sha256="c" * 64,
            receipt_id="repair-process-authority",
        )

    monkeypatch.setattr(gate, "_run_partition_process", green_process)
    result = gate._run_authoritative_repair(
        ledger,
        repo_root=repo_root,
        release_id=release_id,
        partition=partition,
        repair_id="repair-authority-once",
        previous_receipt_id="partition-authority-base",
        cause_hash="d" * 64,
        nodes=nodes,
        collection_sha256="c" * 64,
        timeout_seconds=5,
        source=source,
    )

    assert result["ok"] is True
    assert len(process_calls) == 1
    assert process_calls[0]["nodes"] == nodes
    events = gate._ledger_events(ledger)
    assert len([item for item in events if item["event_type"] == "repair_started"]) == 1
    assert len([item for item in events if item["event_type"] == "repair_completed"]) == 1


def test_NG_RG_28_prior_candidate_release_resume_attaches_and_promotes_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expected Red固定事実: 旧prior branchはpromotionを発行せず再開を閉じていた。"""

    fixture = _make_staged_candidate_repo(tmp_path, name="candidate-prior-release")
    repo = fixture["repo"]
    candidate = fixture["candidate"]
    release_id = "release-prior-candidate-resume"
    ledger = tmp_path / "canonical" / "release-ledger.jsonl"
    state_root = tmp_path / "direct-mainline"
    key = b"k" * 32
    authority_types = gate._CANONICAL_AUTHORITY_EVENT_TYPES
    monkeypatch.setattr(gate, "_canonical_ledger_mac_key", lambda create: key)
    monkeypatch.setattr(gate, "_canonical_ledger_path", lambda: ledger)
    # frozen legacy fixtureだけは旧generic writerでcanonical MAC付きeventをseedする。
    monkeypatch.setattr(gate, "_CANONICAL_AUTHORITY_EVENT_TYPES", frozenset())
    prior_fixture = _seed_expected_red_legacy_prior_candidate_release(
        ledger,
        repo_root=repo,
        release_id=release_id,
        source=candidate,
    )
    monkeypatch.setattr(gate, "_CANONICAL_AUTHORITY_EVENT_TYPES", authority_types)
    monkeypatch.setattr(gate, "_canonical_daily_state_root", lambda: state_root)
    _run_candidate_git(repo, "commit", "-m", "candidate release")
    monkeypatch.setattr(gate, "_observe_release_source", _REAL_OBSERVE_RELEASE_SOURCE)

    from tools import news_grasp_scoped_test_broker as broker

    real_issue = broker._issue_authorized_promotion
    issue_calls: list[dict[str, Any]] = []

    def issue_once(**kwargs: Any) -> dict[str, Any]:
        issue_calls.append(dict(kwargs))
        return real_issue(**kwargs)

    monkeypatch.setattr(broker, "_issue_authorized_promotion", issue_once)
    process_calls: list[object] = []
    monkeypatch.setattr(
        gate,
        "_run_partition_process",
        lambda *args, **kwargs: process_calls.append((args, kwargs)),
    )

    first = gate.finalize_causal_repairs(repo_root=repo, release_id=release_id)
    second = gate.finalize_causal_repairs(repo_root=repo, release_id=release_id)
    events = gate._ledger_events(ledger)
    release_events = [item for item in events if item["event_type"] == "release_completed"]
    promotions = [item for item in events if item["event_type"] == "daily_promotion_issued"]

    assert process_calls == []
    assert len(issue_calls) == 1
    assert first["attached"] is True
    assert second["attached"] is True
    assert first["event_hash"] == prior_fixture["release_event"]["event_hash"]
    assert second["event_hash"] == first["event_hash"]
    assert first["promotion"]["release_event_hash"] == first["event_hash"]
    assert second["promotion"]["release_event_hash"] == first["event_hash"]
    assert len(release_events) == 1
    assert len(promotions) == 1


def test_NG_RG_29_causal_repair_source_mismatch_rejects_before_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """source binding不一致はpartition process起動前にfail-closedする。"""

    ledger = tmp_path / "causal-source-mismatch.jsonl"
    previous, nodes = _seed_failed_partition(ledger, source_root=tmp_path)
    process_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def mismatched_source(repo_root: Path | str) -> dict[str, Any]:
        return {
            "source_root": str(Path(repo_root).resolve()),
            "source_head": "c" * 40,
            "source_tree": "b" * 40,
            "source_mode": "committed",
            "worktree_clean": True,
            "worktree_exact": True,
        }

    monkeypatch.setattr(gate, "_observe_release_source", mismatched_source)
    monkeypatch.setattr(
        gate,
        "_run_partition_process",
        lambda *args, **kwargs: process_calls.append((args, kwargs)),
    )

    with pytest.raises(gate.NewsGraspReleaseGateError, match="release_source_binding_mismatch"):
        gate.causal_repair_partition(
            repo_root=tmp_path,
            partition="scoped_changed",
            node_ids=[nodes[0]],
            cause_hash="3" * 64,
            previous_receipt=previous,
            repair_id="repair-source-mismatch",
            release_id="release-causal",
            ledger_path=ledger,
        )

    assert process_calls == []


def test_NG_RG_30_promote_cli_returns_zero_for_trusted_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """trusted promotionをstate適用後のCLI失敗へ誤投影しない。"""

    monkeypatch.setattr(
        gate,
        "promote_completed_release",
        lambda **_kwargs: {
            "schemaVersion": "NEWS_GRASP_DAILY_SCOPED_PROMOTION_RECEIPT_V1",
            "status": "trusted",
            "release_id": "release-cli-promotion",
        },
    )

    exit_code = gate._main(
        ["promote", str(tmp_path), "release-cli-promotion"]
    )
    output = capsys.readouterr().out
    rows = output.splitlines()

    assert exit_code == 0
    assert len(rows) == 1
    assert json.loads(rows[0])["ok"] is True
