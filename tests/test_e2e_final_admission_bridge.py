from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.e2e_final_admission_bridge import (
    E2EFinalAdmissionError,
    consume_admission as _consume_admission,
    issue_admission,
)


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
BRIDGE = ROOT / "tools" / "e2e_final_admission_bridge.py"


def _write_json(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def consume_admission(*, admission_path: Path, ledger_path: Path) -> dict[str, object]:
    """既存テストも実起動引数との照合を必ず通す。"""
    value = json.loads(admission_path.read_text(encoding="utf-8"))
    return _consume_admission(
        admission_path=admission_path,
        ledger_path=ledger_path,
        runner_arguments=list(value["runnerArguments"]),
    )


def _green_evidence(tmp_path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in (
        "efficiency_design",
        "adversarial_review",
        "route_manifest",
        "static",
        "simulation",
        "isolation",
    ):
        path = _write_json(
            tmp_path / f"{name}.json",
            {
                "schemaVersion": f"{name.upper()}_V1",
                "status": "Green",
            },
        )
        rows.append({"kind": name, "path": str(path), "sha256": _sha256(path)})
    return rows


def _issue(
    tmp_path: Path,
    *,
    repo_root: Path | None = None,
    admission_name: str = "admission.json",
) -> tuple[Path, Path]:
    repo = repo_root or tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    runner = repo / "scripts" / "ops" / "news-grasp-runner.ps1"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("Write-Output 'runner'\n", encoding="utf-8")
    admission = tmp_path / admission_name
    ledger = tmp_path / "durable" / "attempts.json"
    issue_admission(
        issue_date="2026-08-01",
        canonical_product_id="News-Grasp",
        repo_root=repo,
        runner_path=runner,
        runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-01"],
        evidence_bindings=_green_evidence(tmp_path / admission.stem),
        output_path=admission,
    )
    return admission, ledger


def test_file_existence_is_not_e2e_admission(tmp_path: Path) -> None:
    evidence = _green_evidence(tmp_path)
    evidence[0]["path"] = str(
        _write_json(
            tmp_path / "false-green.json",
            {"schemaVersion": "EFFICIENCY_DESIGN_V1", "status": "Red"},
        )
    )
    evidence[0]["sha256"] = _sha256(Path(evidence[0]["path"]))
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = _write_json(repo / "runner.ps1", {"runner": True})

    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_UPSTREAM_NOT_GREEN",
    ):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=evidence,
            output_path=tmp_path / "admission.json",
        )


def test_admission_requires_isolation_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "evidence")
    evidence = [row for row in evidence if row["kind"] != "isolation"]
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_INCOMPLETE"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=evidence,
            output_path=tmp_path / "admission.json",
        )


def test_consumer_rejects_isolation_receipt_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    isolation = next(
        row for row in value["evidenceBindings"] if row["kind"] == "isolation"
    )
    Path(isolation["path"]).write_text('{"status":"Red"}\n', encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_valid_admission_is_consumed_once(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    result = consume_admission(admission_path=admission, ledger_path=ledger)
    assert result["state"] == "consumed"
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    assert list(ledger_value["attempts"]) == [
        "News-Grasp:2026-08-01:scheduled-equivalent-nopublish"
    ]


def test_admission_is_consumed_once_across_worktree_and_receipt_paths(
    tmp_path: Path,
) -> None:
    first, ledger = _issue(
        tmp_path,
        repo_root=tmp_path / "worktree-r1",
        admission_name="first.json",
    )
    consume_admission(admission_path=first, ledger_path=ledger)

    second, _ = _issue(
        tmp_path,
        repo_root=tmp_path / "worktree-r2",
        admission_name="second.json",
    )
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_FINAL_ATTEMPT_ALREADY_CONSUMED",
    ):
        consume_admission(admission_path=second, ledger_path=ledger)


def test_admission_rejects_evidence_and_runner_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    evidence_path = Path(value["evidenceBindings"][0]["path"])
    evidence_path.write_text('{"status":"Red"}\n', encoding="utf-8")

    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_UPSTREAM_EVIDENCE_DRIFT",
    ):
        consume_admission(admission_path=admission, ledger_path=ledger)

    admission2, ledger2 = _issue(tmp_path / "runner-drift")
    value2 = json.loads(admission2.read_text(encoding="utf-8"))
    Path(value2["runnerPath"]).write_text("changed\n", encoding="utf-8")
    with pytest.raises(
        E2EFinalAdmissionError,
        match="E2E_RUNNER_DRIFT",
    ):
        consume_admission(admission_path=admission2, ledger_path=ledger2)


def test_admission_rejects_evidence_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    Path(value["evidenceBindings"][0]["path"]).write_text(
        '{"status":"Red"}\n', encoding="utf-8"
    )
    with pytest.raises(E2EFinalAdmissionError, match="E2E_UPSTREAM_EVIDENCE_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_admission_rejects_runner_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    Path(value["runnerPath"]).write_text("changed\n", encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_RUNNER_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_admission_rejects_resume_and_publish_arguments(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    evidence = _green_evidence(tmp_path / "evidence")
    for arguments in (
        ["-NoPublish", "-ResumeFromStage", "deepdive"],
        ["-DateStampOverride", "2026-08-01"],
    ):
        with pytest.raises(E2EFinalAdmissionError, match="E2E_COMMAND_FORBIDDEN"):
            issue_admission(
                issue_date="2026-08-01",
                canonical_product_id="News-Grasp",
                repo_root=repo,
                runner_path=runner,
                runner_arguments=arguments,
                evidence_bindings=evidence,
                output_path=tmp_path / f"{len(arguments)}.json",
            )


def test_admission_rejects_identity_tamper(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    value = json.loads(admission.read_text(encoding="utf-8"))
    value["issueDate"] = "2026-08-02"
    admission.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ADMISSION_IDENTITY_DRIFT"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_consumed_admission_is_typed_replay(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    consume_admission(admission_path=admission, ledger_path=ledger)
    with pytest.raises(E2EFinalAdmissionError, match="E2E_ADMISSION_REPLAY"):
        consume_admission(admission_path=admission, ledger_path=ledger)


def test_consumer_rejects_actual_runner_argument_drift(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)
    with pytest.raises(E2EFinalAdmissionError, match="E2E_COMMAND_DRIFT"):
        _consume_admission(
            admission_path=admission,
            ledger_path=ledger,
            runner_arguments=["-NoPublish", "-DateStampOverride", "2026-08-02"],
        )


def test_parallel_consume_has_exactly_one_winner(tmp_path: Path) -> None:
    admission, ledger = _issue(tmp_path)

    def consume() -> str:
        try:
            consume_admission(admission_path=admission, ledger_path=ledger)
            return "consumed"
        except E2EFinalAdmissionError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: consume(), range(2)))
    assert results.count("consumed") == 1
    assert len(results) == 2


def test_cli_does_not_accept_caller_selected_ledger() -> None:
    bridge_source = BRIDGE.read_text(encoding="utf-8-sig")
    wrapper_source = WRAPPER.read_text(encoding="utf-8-sig")
    assert 'add_argument("--ledger"' not in bridge_source
    assert 'add_argument("--runner-arguments-file", type=Path, required=True)' in bridge_source
    assert "'--ledger'" not in wrapper_source
    assert "'--runner-arguments-file'" in wrapper_source
    assert "news-grasp-e2e-final-attempts.json" in bridge_source


def test_product_identity_cannot_be_aliased(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = repo / "runner.ps1"
    runner.write_text("runner\n", encoding="utf-8")
    with pytest.raises(E2EFinalAdmissionError, match="E2E_PRODUCT_ID_INVALID"):
        issue_admission(
            issue_date="2026-08-01",
            canonical_product_id="News-Grasp-copy",
            repo_root=repo,
            runner_path=runner,
            runner_arguments=["-NoPublish"],
            evidence_bindings=_green_evidence(tmp_path / "evidence"),
            output_path=tmp_path / "admission.json",
        )


def test_final_e2e_wrapper_consumes_before_runner_and_forbids_resume() -> None:
    source = WRAPPER.read_text(encoding="utf-8-sig")
    consume = source.index("e2e_final_admission_bridge.py")
    launch = source.index("& $PowerShellExe @runnerArguments")
    assert consume < launch
    assert "E2EAdmissionPath" in source
    assert "$runnerArguments | ConvertTo-Json" in source
    assert source.index("$runnerArguments = @(") < source.index("'consume'") < launch
    assert "-ResumeFromStage" not in source
    assert "resume_model" not in source
