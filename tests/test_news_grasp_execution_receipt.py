from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest


def _api():
    return importlib.import_module("tools.news_grasp_execution_receipt")


def _commit(repo: Path, content: str, *, test_source: str = "def test_cause():\n    assert True\n") -> str:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "cause.txt").write_text(content, encoding="utf-8")
    test_path = repo / "tests" / "test_cause.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test_source, encoding="utf-8")
    subprocess.run(["git", "add", "cause.txt", "tests/test_cause.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", content],
        cwd=repo,
        check=True,
    )
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_r11_missing_pytest_candidate_is_not_selected(monkeypatch) -> None:
    """R11: pytest欠落interpreterを記録して起動せず、利用可能候補だけを固定する。"""
    api = _api()
    rows = {
        "missing.exe": {"ok": False, "status": "environment_missing", "pythonVersion": "3.12"},
        "canonical.exe": {"ok": True, "status": "verified", "pythonVersion": "3.12", "pytestVersion": "9.1"},
    }
    monkeypatch.setattr(api, "probe_python", lambda value: {"executable": value, **rows[value]})
    selected = api.select_canonical_python(["missing.exe", "canonical.exe"])
    assert selected["executable"] == "canonical.exe"
    assert selected["rejected"][0]["status"] == "environment_missing"


def test_r12_same_shape_retry_requires_causal_receipt(tmp_path: Path) -> None:
    """R12: 同一environment/failure shapeの無因果retryを拒否する。"""
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    generation_1 = _commit(repo, "before")
    environment = api.capture_retry_environment(repo_root=repo, source_generation=generation_1)["environmentShape"]
    failure_node = "tests/test_cause.py::test_cause"
    failure_class = "AssertionError"
    causal_frame = "tests/test_cause.py:2"
    failure = api.failure_shape(node_id=failure_node, failure_class=failure_class, causal_frame=causal_frame)
    inputs = ["cause.txt", "tests/test_cause.py"]
    contract = {"failure_node_id": failure_node, "failure_class": failure_class, "causal_frame": causal_frame}
    first = store.admit_attempt(source_generation=generation_1, environment_shape=environment, failure_shape=failure, repo_root=repo, cause_input_paths=inputs, **contract)
    assert first["ok"] is True
    assert store.admit_attempt(source_generation=generation_1, environment_shape=environment, failure_shape=failure, repo_root=repo, cause_input_paths=inputs, **contract)["reasonCode"] == "same_shape_retry_rejected"
    generation_2 = _commit(repo, "after")
    environment_2 = api.capture_retry_environment(repo_root=repo, source_generation=generation_2)["environmentShape"]
    blocked_fake = store.admit_attempt(
        source_generation=generation_2,
        environment_shape=environment_2,
        failure_shape=failure,
        repo_root=repo,
        cause_input_paths=inputs,
        **contract,
        causal_remediation_receipt={"changedInputs": ["tools/news_grasp_execution_receipt.py"]},
    )
    assert blocked_fake["ok"] is False
    event = store.record_verification_event(source_generation=generation_2, repo_root=repo, cause_input_paths=inputs, node_id=failure_node, prior_attempt_id=first["attemptId"])
    admitted = store.admit_attempt(
        source_generation=generation_2,
        environment_shape=environment_2,
        failure_shape=failure,
        repo_root=repo,
        cause_input_paths=inputs,
        **contract,
        causal_remediation_receipt={
            "schemaVersion": "NEWS_GRASP_CAUSAL_REMEDIATION_RECEIPT_V1",
            "priorAttemptId": first["attemptId"],
            "priorFailureShape": failure,
            "priorSourceGeneration": generation_1,
            "afterSourceGeneration": generation_2,
            "beforeInputIdentity": first["causeInputIdentity"],
            "afterInputIdentity": event["causeInputIdentity"],
            "verificationEventId": event["verificationEventId"],
            "verificationNodeId": event["nodeId"],
        },
    )
    assert admitted["ok"] is True


def test_attempt_admission_is_unique_per_source_generation(tmp_path: Path) -> None:
    """security Red: composite unique境界で同shape二重admitを拒否する。"""
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    generation = _commit(repo, "same")
    node = "tests/test_cause.py::test_cause"
    failure = api.failure_shape(node_id=node, failure_class="AssertionError", causal_frame="tests/test_cause.py:2")
    environment = api.capture_retry_environment(repo_root=repo, source_generation=generation)["environmentShape"]
    kwargs = {"source_generation": generation, "environment_shape": environment, "failure_shape": failure, "failure_node_id": node, "failure_class": "AssertionError", "causal_frame": "tests/test_cause.py:2", "repo_root": repo, "cause_input_paths": ["cause.txt", "tests/test_cause.py"]}
    assert store.admit_attempt(**kwargs)["ok"] is True
    assert store.admit_attempt(**kwargs)["ok"] is False


def test_verification_child_receives_only_secret_free_allowlisted_environment(tmp_path: Path, monkeypatch) -> None:
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    generation_1 = _commit(repo, "before")
    node = "tests/test_cause.py::test_cause"
    failure = api.failure_shape(node_id=node, failure_class="AssertionError", causal_frame="tests/test_cause.py:2")
    environment_1 = api.capture_retry_environment(repo_root=repo, source_generation=generation_1)["environmentShape"]
    first = store.admit_attempt(
        source_generation=generation_1,
        environment_shape=environment_1,
        failure_shape=failure,
        failure_node_id=node,
        failure_class="AssertionError",
        causal_frame="tests/test_cause.py:2",
        repo_root=repo,
        cause_input_paths=["cause.txt", "tests/test_cause.py"],
    )
    generation_2 = _commit(
        repo,
        "after",
        test_source="import os\n\ndef test_cause():\n    assert 'VAPID_PRIVATE_KEY' not in os.environ\n    assert 'AWS_ACCESS_KEY_ID' not in os.environ\n",
    )
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "sentinel-private-key")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "sentinel-access-key")
    event = store.record_verification_event(
        source_generation=generation_2,
        repo_root=repo,
        cause_input_paths=["cause.txt", "tests/test_cause.py"],
        node_id=node,
        prior_attempt_id=first["attemptId"],
    )
    assert event["status"] == "verified"


def test_non_external_quota_word_remains_blocking_red() -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    assert api._surface_scoped({"status": "red", "reason": "quota-like content", "surface": "daily_quality"}, "daily_quality") is False


def test_verification_event_rejects_unrelated_passing_node(tmp_path: Path) -> None:
    """無関係なGreen testをcausal recovery authorityへ昇格させない。"""
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    first_generation = _commit(repo, "before")
    node = "tests/test_cause.py::test_cause"
    failure_class = "AssertionError"
    causal_frame = "tests/test_cause.py:2"
    shape = api.failure_shape(node_id=node, failure_class=failure_class, causal_frame=causal_frame)
    first = store.admit_attempt(
        source_generation=first_generation,
        environment_shape=api.capture_retry_environment(repo_root=repo, source_generation=first_generation)["environmentShape"],
        failure_shape=shape,
        failure_node_id=node,
        failure_class=failure_class,
        causal_frame=causal_frame,
        repo_root=repo,
        cause_input_paths=["cause.txt", "tests/test_cause.py"],
    )
    unrelated = repo / "tests" / "test_unrelated.py"
    unrelated.write_text("def test_unrelated_always_green():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "add", "tests/test_unrelated.py"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-q", "-m", "after"], cwd=repo, check=True)
    generation_2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(ValueError, match="verification_node_not_bound_to_failure"):
        store.record_verification_event(
            source_generation=generation_2,
            repo_root=repo,
            cause_input_paths=["cause.txt", "tests/test_cause.py"],
            node_id="tests/test_unrelated.py::test_unrelated_always_green",
            prior_attempt_id=first["attemptId"],
        )


def test_verification_event_rejects_collect_only_environment_bypass(tmp_path: Path, monkeypatch) -> None:
    """PYTEST_ADDOPTS=--collect-onlyでも未実行Red nodeをverifiedにしない。"""
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    red = "def test_cause():\n    assert False\n"
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    generation_1 = _commit(repo, "before", test_source=red)
    node = "tests/test_cause.py::test_cause"
    failure_class = "AssertionError"
    causal_frame = "tests/test_cause.py:2"
    shape = api.failure_shape(node_id=node, failure_class=failure_class, causal_frame=causal_frame)
    first = store.admit_attempt(
        source_generation=generation_1,
        environment_shape=api.capture_retry_environment(repo_root=repo, source_generation=generation_1)["environmentShape"],
        failure_shape=shape,
        failure_node_id=node,
        failure_class=failure_class,
        causal_frame=causal_frame,
        repo_root=repo,
        cause_input_paths=["cause.txt", "tests/test_cause.py"],
    )
    generation_2 = _commit(repo, "after", test_source=red)
    with pytest.raises(RuntimeError, match="verification_runner_red"):
        store.record_verification_event(
            source_generation=generation_2,
            repo_root=repo,
            cause_input_paths=["cause.txt", "tests/test_cause.py"],
            node_id=node,
            prior_attempt_id=first["attemptId"],
        )


def test_verification_event_bounds_child_output_while_running(tmp_path: Path) -> None:
    api = _api()
    store = api.ExecutionControlStore(tmp_path / "state")
    repo = tmp_path / "repo"
    generation_1 = _commit(repo, "before", test_source="def test_cause():\n    assert False\n")
    node = "tests/test_cause.py::test_cause"
    failure_class = "AssertionError"
    causal_frame = "tests/test_cause.py:2"
    shape = api.failure_shape(node_id=node, failure_class=failure_class, causal_frame=causal_frame)
    first = store.admit_attempt(
        source_generation=generation_1,
        environment_shape=api.capture_retry_environment(repo_root=repo, source_generation=generation_1)["environmentShape"],
        failure_shape=shape,
        failure_node_id=node,
        failure_class=failure_class,
        causal_frame=causal_frame,
        repo_root=repo,
        cause_input_paths=["cause.txt", "tests/test_cause.py"],
    )
    noisy = "def test_cause():\n    print('x' * 2_000_000)\n    assert True\n"
    generation_2 = _commit(repo, "after", test_source=noisy)
    with pytest.raises(RuntimeError, match="verification_runner_output_too_large"):
        store.record_verification_event(
            source_generation=generation_2,
            repo_root=repo,
            cause_input_paths=["cause.txt", "tests/test_cause.py"],
            node_id=node,
            prior_attempt_id=first["attemptId"],
        )


def test_retry_environment_rejects_dirty_green_substitution(tmp_path: Path) -> None:
    api = _api()
    repo = tmp_path / "repo"
    generation = _commit(repo, "red", test_source="def test_cause():\n    assert False\n")
    (repo / "tests" / "test_cause.py").write_text("def test_cause():\n    assert True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="retry_environment_worktree_not_clean"):
        api.capture_retry_environment(repo_root=repo, source_generation=generation)


def test_cause_input_iterator_is_bounded_before_materialization(tmp_path: Path) -> None:
    """untrusted/infinite iterableを最大65件で停止してresource DoSを防ぐ。"""
    api = _api()
    repo = tmp_path / "repo"
    generation = _commit(repo, "bounded")
    consumed = {"count": 0}

    def paths():
        for index in range(100_000):
            consumed["count"] += 1
            yield f"cause-{index}.txt"

    with pytest.raises(ValueError, match="path_count_invalid"):
        api._capture_cause_input_snapshot(repo_root=repo, source_generation=generation, cause_input_paths=paths())
    assert consumed["count"] == 65


def test_observation_redacts_environment_and_binds_manifest(tmp_path: Path, monkeypatch) -> None:
    """security Red: raw env値を保存せずissue/exact write setをmanifestへ束縛する。"""
    api = _api()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"issueDate": "2026-09-01", "runId": "run", "runIntent": "scheduled_production_direct", "manifestId": "f" * 64, "exactWriteSet": ["docs/index.html"]}), encoding="utf-8")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--base-url=https://user:secret@example.test token=abc")
    monkeypatch.setattr(api, "probe_python", lambda _value: {"ok": True, "status": "verified", "executable": "python", "pythonVersion": "3.12", "pytestVersion": "9"})
    row = api.capture_observation(repo_root=tmp_path, purpose="test", run_id="run", issue_date="2026-09-01", manifest_path=manifest, runtime_state_root=tmp_path / "state")
    assert row["issueDate"] == "2026-09-01"
    assert row["exactWriteSet"] == ["docs/index.html"]
    assert "secret" not in json.dumps(row["environment"], ensure_ascii=False)


def test_r13_checkpoints_are_exactly_once_and_freeze_optional_work(tmp_path: Path) -> None:
    """R13: 45/75/90分checkpointを一度だけ記録し、public-critical successorは止めない。"""
    api = _api()
    store = api.ExecutionControlStore(tmp_path)
    assert store.record_checkpoint(run_id="run", minute=45)["recorded"] is True
    assert store.record_checkpoint(run_id="run", minute=45)["recorded"] is False
    at_75 = store.record_checkpoint(run_id="run", minute=75)
    assert at_75["optionalHighCostFrozen"] is True
    at_90 = store.record_checkpoint(run_id="run", minute=90, elapsed_minutes=91)
    assert at_90["sloDebt"] is True
    assert at_90["continuePublicCriticalSuccessor"] is True


def test_environment_and_failure_shapes_are_stable_and_separate() -> None:
    """環境差と失敗原因を別shapeとして記録する。"""
    api = _api()
    environment = api.environment_shape({"executable": "python.exe", "pythonVersion": "3.12", "pytestVersion": "9.1", "cwd": "repo", "sourceHead": "a"})
    failure = api.failure_shape(node_id="R12", failure_class="AssertionError", causal_frame="consumer.py:42")
    assert len(environment) == 64
    assert len(failure) == 64
    assert environment != failure
