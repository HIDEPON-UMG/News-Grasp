from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools import news_grasp_release_gate as release_gate
from tools import news_grasp_scoped_test_broker as broker


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "tools" / "news_grasp_daily_gate.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_news_grasp_daily_route_runtime_review.py").write_text(
        "def test_fixture():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tests" / "test_news_grasp_daily_45m_contract.py").write_text(
        "def test_fixture():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "init")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    return repo


def _issue_fixture_promotion(repo: Path, state: Path) -> dict[str, object]:
    """consumer検証専用の署名済みfixture。production issuerは呼ばない。"""

    head, tree = broker._head_and_tree(repo)
    issued = datetime(2026, 9, 3, 6, 0, tzinfo=broker.JST)
    registry = {key: list(value) for key, value in broker.SCOPED_PATH_TEST_REGISTRY.items()}
    unsigned = {
        "schemaVersion": broker.PROMOTION_SCHEMA,
        "status": "trusted",
        "source_head": head,
        "source_tree": tree,
        "release_id": "fixture-release-1",
        "release_receipt_sha256": "a" * 64,
        "release_event_hash": "b" * 64,
        "path_test_registry": registry,
        "registry_sha256": broker._sha(registry),
        "issued_at": broker._iso(issued),
        "expires_at": broker._iso(issued + timedelta(days=8)),
        "nonce": "c" * 32,
    }
    key = b"k" * 32
    receipt = {**unsigned, "signature": broker._signed(unsigned, key)}
    receipt_path, key_path = broker._promotion_paths(state)
    broker._atomic_write(key_path, key)
    broker._atomic_write(receipt_path, broker._json_bytes(receipt) + b"\n")
    return {**receipt, "receipt_path": str(receipt_path)}


def test_scoped_gate_reuses_signed_same_head_promotion_without_pytest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _issue_fixture_promotion(repo, state)
    calls: list[object] = []

    result = broker.evaluate_scoped_contract(
        repo_root=repo,
        state_root=state,
        runner=lambda *_args: calls.append(_args),
    )

    assert result["ok"] is True
    assert result["mode"] == "promotion_reuse"
    assert result["test_process_count"] == 0
    assert calls == []


def test_scoped_gate_runs_only_signed_registry_nodes_once_after_source_change(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _issue_fixture_promotion(repo, state)
    (repo / "tools" / "news_grasp_daily_gate.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "registered change")
    calls: list[tuple[Path, list[str]]] = []

    def runner(root: Path, nodes: list[str]) -> dict[str, object]:
        calls.append((root, list(nodes)))
        return {"return_code": 0, "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64}

    result = broker.evaluate_scoped_contract(repo_root=repo, state_root=state, runner=runner)

    assert result["ok"] is True
    assert result["mode"] == "changed_source"
    assert result["changed_paths"] == ["tools/news_grasp_daily_gate.py"]
    assert result["test_process_count"] == 1
    assert len(calls) == 1
    assert calls[0][1] == sorted(broker.SCOPED_PATH_TEST_REGISTRY["tools/news_grasp_daily_gate.py"])


def test_scoped_gate_rejects_unknown_changed_path_before_test_process(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _issue_fixture_promotion(repo, state)
    (repo / "unregistered.txt").write_text("drift\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "unknown change")
    calls: list[object] = []

    result = broker.evaluate_scoped_contract(
        repo_root=repo,
        state_root=state,
        runner=lambda *_args: calls.append(_args),
    )

    assert result["ok"] is False
    assert result["failures"] == ["scoped_changed_path_unregistered"]
    assert result["unknown_paths"] == ["unregistered.txt"]
    assert calls == []


def test_scoped_gate_rejects_tampered_promotion_signature(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    issued = _issue_fixture_promotion(repo, state)
    receipt_path = Path(str(issued["receipt_path"]))
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["source_head"] = "f" * 40
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    result = broker.evaluate_scoped_contract(repo_root=repo, state_root=state)

    assert result["ok"] is False
    assert result["failures"] == ["scoped_promotion_signature_invalid"]


def test_scoped_promotion_direct_import_cannot_redirect_canonical_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    assert not hasattr(broker, "issue_promotion_receipt")
    assert not hasattr(broker, "_RELEASE_GATE_AUTHORITY")
    trusted_local = tmp_path / "trusted-known-folder"
    trusted_local.mkdir()
    attacker_local = tmp_path / "attacker-env-root"
    monkeypatch.setattr(release_gate, "_known_folder_local_app_data", lambda: trusted_local)
    monkeypatch.setenv("LOCALAPPDATA", str(attacker_local))
    fake_receipt = {
        "schemaVersion": broker.RELEASE_SCHEMA,
        "ok": True,
        "status": "green",
        "release_id": "fixture-release-1",
        "executed_node_count": 1,
        "union_node_count": 1,
        "failed_nodes": [],
    }
    fake_ledger = attacker_local / "News-Grasp" / "release-gate" / "release_ledger.jsonl"
    fake_event = release_gate._append_ledger(
        fake_ledger,
        "release_completed",
        release_id="fixture-release-1",
        receipt=fake_receipt,
        receipt_hash=release_gate._mapping_hash(fake_receipt),
    )

    with pytest.raises(ValueError, match="scoped_promotion_state_root_noncanonical"):
        broker._issue_authorized_promotion(
            repo_root=repo,
            state_root=attacker_local / "News-Grasp" / "direct-mainline",
            release_event=fake_event,
        )
    with pytest.raises(ValueError, match="scoped_promotion_release_binding_invalid"):
        broker._issue_authorized_promotion(
            repo_root=repo,
            state_root=trusted_local / "News-Grasp" / "direct-mainline",
            release_event=fake_event,
        )


def test_scoped_gate_rejects_release_only_changed_path_before_runner(tmp_path: Path) -> None:
    """Release gate変更はDaily scoped runnerへ到達させない。"""

    repo = _repo(tmp_path)
    state = tmp_path / "state"
    _issue_fixture_promotion(repo, state)
    release_path = repo / "tools" / "news_grasp_release_gate.py"
    release_path.write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "release-only change")
    calls: list[object] = []

    result = broker.evaluate_scoped_contract(
        repo_root=repo,
        state_root=state,
        runner=lambda *_args: calls.append(True),
    )

    assert result["ok"] is False
    assert result["failures"] == ["scoped_changed_path_release_only_forbidden"]
    assert result["changed_paths"] == ["tools/news_grasp_release_gate.py"]
    assert result["test_nodes"] == []
    assert result["test_process_count"] == 0
    assert calls == []


@pytest.mark.parametrize(
    ("node_name", "node_source", "expected_failure"),
    (
        (
            "tests/test_release_gate_fixture.py",
            "def test_fixture():\n    assert True\n",
            "scoped_test_node_release_only_forbidden",
        ),
        (
            "tests/test_historical_failure_fixture.py",
            "def test_fixture():\n    assert True\n",
            "scoped_test_node_release_only_forbidden",
        ),
        (
            "tests/test_playwright_fixture.py",
            "def test_fixture():\n    assert True\n",
            "scoped_test_node_release_only_forbidden",
        ),
        (
            "tests/test_nopublish_fixture.py",
            "def test_fixture():\n    assert True\n",
            "scoped_test_node_release_only_forbidden",
        ),
        (
            "tests/test_forbidden_release_import.py",
            "import tools.news_grasp_release_gate\n",
            "scoped_test_transitive_release_import_forbidden",
        ),
        (
            "tests/test_forbidden_historical_import.py",
            "from tools import historical_failure_scenarios\n",
            "scoped_test_transitive_release_import_forbidden",
        ),
        (
            "tests/test_forbidden_ui_dependency.py",
            "import playwright\n",
            "scoped_test_transitive_release_import_forbidden",
        ),
        (
            "tests/test_forbidden_nested_process.py",
            "import subprocess\nsubprocess.run(['nested'])\n",
            "scoped_test_nested_process_forbidden",
        ),
    ),
)
def test_scoped_registered_forbidden_node_is_red_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    node_name: str,
    node_source: str,
    expected_failure: str,
) -> None:
    """Release系import/NoPublish path/nested processはspawn前に静的拒否する。"""

    repo = _repo(tmp_path)
    node_path = repo / node_name
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text(node_source, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "register forbidden node fixture")

    # 署名済みpromotionへtest nodeを登録し、実行前closure検査まで到達させる。
    monkeypatch.setitem(
        broker.SCOPED_PATH_TEST_REGISTRY,
        "tools/news_grasp_daily_gate.py",
        (node_name,),
    )
    state = tmp_path / "state"
    _issue_fixture_promotion(repo, state)
    (repo / "tools" / "news_grasp_daily_gate.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "registered source change")
    calls: list[object] = []

    result = broker.evaluate_scoped_contract(
        repo_root=repo,
        state_root=state,
        runner=lambda *_args: calls.append(True),
    )

    assert result["ok"] is False
    assert result["failures"] == [expected_failure]
    assert result["test_nodes"] == [node_name]
    assert result["test_process_count"] == 0
    assert calls == []
