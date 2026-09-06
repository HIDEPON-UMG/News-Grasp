"""製品内NoPublish入口のlocal起動・開始境界・同日復帰契約。"""

from __future__ import annotations

import importlib
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tools.news_grasp_preentry_journal import PreentryJournal


ISSUE_DATE = "2026-09-06"
CLAIM_ENVIRONMENT = (
    "NEWS_GRASP_E2E_ADMISSION_PATH",
    "NEWS_GRASP_E2E_ARGUMENTS_PATH",
    "NEWS_GRASP_E2E_CLAIM_PATH",
    "NEWS_GRASP_E2E_RESERVATION_PATH",
    "NEWS_GRASP_E2E_PARENT_AUTHORITY_PATH",
)


def _create_windows_directory_junction(*, junction: Path, target: Path) -> None:
    """非昇格PowerShellで実reparse directory junctionを作る。"""

    if os.name != "nt":
        pytest.skip("Windows directory junction contract")
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    env = dict(os.environ)
    env["NG_TEST_JUNCTION"] = str(junction)
    env["NG_TEST_JUNCTION_TARGET"] = str(target)
    command = (
        "$ErrorActionPreference='Stop'; "
        "New-Item -ItemType Junction -Path $env:NG_TEST_JUNCTION "
        "-Target $env:NG_TEST_JUNCTION_TARGET | Out-Null"
    )
    completed = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        pytest.fail(
            "Windows directory junction fixture creation failed: "
            f"exit={completed.returncode}, stderr={completed.stderr!r}"
        )


@dataclass
class _ReleaseFixture:
    module: Any
    runtime: Any
    source_repo: Path
    worktree_a: Path
    worktree_b: Path
    receipt_a: Path
    receipt_b: Path
    canonical_state_root: Path

    def diagnostic_paths(
        self,
        *,
        repo_root: Path,
        caller_state_root: Path | None = None,
    ) -> tuple[Path, Path]:
        state_root = caller_state_root or (repo_root / "caller-state")
        invocation = state_root.name or "saved-green"
        diagnostic_root = (
            repo_root / "build" / "release-nopublish-diagnostics" / invocation
        )
        return diagnostic_root / "state.json", diagnostic_root / "receipt.json"

    def argv(
        self,
        *,
        repo_root: Path,
        isolation_receipt: Path,
        caller_state_root: Path | None = None,
        diagnostics: bool = False,
    ) -> list[str]:
        state_root = caller_state_root or (repo_root / "caller-state")
        if diagnostics:
            state_file, receipt_file = self.diagnostic_paths(
                repo_root=repo_root,
                caller_state_root=state_root,
            )
        else:
            state_file = state_root / "caller-state.json"
            receipt_file = state_root / "caller-receipt.json"
        return [
            "--repo-root",
            str(repo_root),
            "--source-issue-date",
            ISSUE_DATE,
            "--state-root",
            str(state_root),
            "--state-file",
            str(state_file),
            "--receipt-path",
            str(receipt_file),
            "--isolation-receipt",
            str(isolation_receipt),
        ]


def _write_isolation_receipt(
    path: Path,
    *,
    source_repo: Path,
    target_root: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "NEWS_GRASP_E2E_ISOLATION_V1",
                "status": "Green",
                "issueDate": ISSUE_DATE,
                "sourceRepo": str(source_repo.resolve()),
                "targetRoot": str(target_root.resolve()),
                "sourceCommit": "a" * 40,
                "targetCommit": "b" * 40,
                "runnerArtifactPredicate": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def release_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _ReleaseFixture:
    module = importlib.import_module("tools.news_grasp_release_nopublish")
    runtime = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "source-repo"
    worktree_a = tmp_path / "作業木A"
    worktree_b = tmp_path / "作業木B"
    source_repo.mkdir(parents=True)
    (source_repo / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    module._git(source_repo, "init", "-b", "main")
    module._git(source_repo, "config", "user.name", "News-Grasp fixture")
    module._git(source_repo, "config", "user.email", "fixture@example.invalid")
    module._git(source_repo, "add", "--", "fixture.txt")
    module._git(source_repo, "commit", "-m", "fixture baseline")
    module._git(source_repo, "worktree", "add", "--detach", str(worktree_a), "HEAD")
    module._git(source_repo, "worktree", "add", "--detach", str(worktree_b), "HEAD")
    receipt_a = worktree_a / "isolation-receipt.json"
    receipt_b = worktree_b / "isolation-receipt.json"
    _write_isolation_receipt(
        receipt_a,
        source_repo=source_repo,
        target_root=worktree_a,
    )
    _write_isolation_receipt(
        receipt_b,
        source_repo=source_repo,
        target_root=worktree_b,
    )

    canonical_state_root = tmp_path / "LocalAppData" / "News-Grasp" / "release-nopublish"
    monkeypatch.setattr(runtime, "_windows_local_app_data", lambda: canonical_state_root.parent.parent)
    monkeypatch.setattr(module, "runtime", runtime)
    for name in CLAIM_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NEWS_GRASP_PREENTRY_JOURNAL", raising=False)
    monkeypatch.delenv("NEWS_GRASP_PREENTRY_ISSUE", raising=False)
    monkeypatch.delenv("NEWS_GRASP_PREENTRY_SESSION", raising=False)
    return _ReleaseFixture(
        module=module,
        runtime=runtime,
        source_repo=source_repo,
        worktree_a=worktree_a,
        worktree_b=worktree_b,
        receipt_a=receipt_a,
        receipt_b=receipt_b,
        canonical_state_root=canonical_state_root,
    )


class _FakeBusiness:
    """release core差し替え用のbounded fake。外部providerとモデルを持たない。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.model_calls = 0
        self.crash_after_checkpoint = False

    def core(self, **kwargs: Any) -> dict[str, Any]:
        state_root = Path(kwargs["state_root"]).resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        ledger_path = state_root / "fake-business-ledger.json"
        self.calls.append(
            {
                "repo_root": Path(kwargs["repo_root"]).resolve(),
                "state_root": state_root,
                "isolation_receipt": Path(kwargs["isolation_receipt"]).resolve(),
            }
        )
        if not ledger_path.exists():
            self.model_calls += 1
            ledger = {
                "runId": "fake-local-run-1",
                "checkpoint": "scoped_contract_unit",
                "modelCalls": 1,
            }
            ledger_path.write_text(
                json.dumps(ledger, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if self.crash_after_checkpoint:
                self.crash_after_checkpoint = False
                raise RuntimeError("fake_business_crash_after_checkpoint")
            resumed = False
        else:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            resumed = True
        return {
            "schemaVersion": self.module_schema,
            "ok": True,
            "status": "publish_dry_run_ok",
            "source_issue_date": str(kwargs["source_issue_date"]),
            "run_id": str(ledger["runId"]),
            "operation_count": 0,
            "externalEffectCount": 0,
            "no_publish": True,
            "no_push": True,
            "e2eAttemptConsumed": 1,
            "modelCallCount": 0 if resumed else 1,
            "resumed": resumed,
            "artifactRoot": str(state_root),
        }

    @property
    def module_schema(self) -> str:
        return "NEWS_GRASP_RELEASE_NOPUBLISH_RECEIPT_V1"


def _invoke_main(
    fixture: _ReleaseFixture,
    *,
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any]]:
    exit_code = fixture.module._main(argv)
    output = capsys.readouterr().out.strip().splitlines()
    assert output, "local入口は機械可読receiptを1行出力する"
    return exit_code, json.loads(output[-1])


def test_local_entry_accepts_no_external_claim_and_runs_bounded_fake(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """外部claim/owner/P08/goalなしでも既存CLIからfake businessへ到達する。"""

    fake = _FakeBusiness()
    monkeypatch.setattr(release_fixture.module, "_run_release_nopublish_core", fake.core)
    argv = release_fixture.argv(
        repo_root=release_fixture.worktree_a,
        isolation_receipt=release_fixture.receipt_a,
        caller_state_root=release_fixture.worktree_a / "caller-state",
        diagnostics=True,
    )

    exit_code, result = _invoke_main(release_fixture, argv=argv, capsys=capsys)

    assert exit_code == 0
    assert result["ok"] is True
    assert result["e2eAttemptConsumed"] == 1
    assert result["externalEffectCount"] == 0
    assert len(fake.calls) == 1
    state_file, receipt_file = release_fixture.diagnostic_paths(
        repo_root=release_fixture.worktree_a,
        caller_state_root=release_fixture.worktree_a / "caller-state",
    )
    assert state_file.is_file()
    assert receipt_file.is_file()
    assert not result.get("highCostClaimId")


def test_preentry_failure_keeps_caller_artifacts_and_consumes_zero(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """開始前の隔離入力Redはjournalへ残し、本体予算と既存receiptを触らない。"""

    fake = _FakeBusiness()
    monkeypatch.setattr(release_fixture.module, "_run_release_nopublish_core", fake.core)
    legacy_journal_path = tmp_path / "journal" / "preentry.sqlite3"
    monkeypatch.setenv("NEWS_GRASP_PREENTRY_JOURNAL", str(legacy_journal_path))
    monkeypatch.setenv("NEWS_GRASP_PREENTRY_ISSUE", ISSUE_DATE)
    monkeypatch.setenv("NEWS_GRASP_PREENTRY_SESSION", "preentry-red")
    monkeypatch.setattr(
        release_fixture.module,
        "_await_owner_start_confirmation",
        lambda *_args: None,
    )
    caller_state = release_fixture.worktree_a / "caller-state"
    caller_state.mkdir()
    state_file, receipt_path = release_fixture.diagnostic_paths(
        repo_root=release_fixture.worktree_a,
        caller_state_root=caller_state,
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("preserve-state\n", encoding="utf-8")
    receipt_path.write_text("preserve-receipt\n", encoding="utf-8")
    missing_receipt = release_fixture.worktree_a / "missing-isolation.json"
    argv = release_fixture.argv(
        repo_root=release_fixture.worktree_a,
        isolation_receipt=missing_receipt,
        caller_state_root=caller_state,
        diagnostics=True,
    )

    exit_code, result = _invoke_main(release_fixture, argv=argv, capsys=capsys)

    assert exit_code != 0
    assert result["ok"] is False
    assert result.get("e2eAttemptConsumed", 0) == 0
    assert any("isolation" in str(item).casefold() for item in result.get("failures", []))
    assert fake.calls == []
    assert state_file.read_text(encoding="utf-8") == "preserve-state\n"
    assert receipt_path.read_text(encoding="utf-8") == "preserve-receipt\n"
    phases = [
        row["phase"]
        for row in PreentryJournal(
            release_fixture.canonical_state_root / "preentry.sqlite3"
        ).events(ISSUE_DATE)
    ]
    assert "preentry_failed" in phases
    assert "module_started" not in phases


def test_same_canonical_run_resumes_after_checkpoint_crash_without_new_model(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """checkpoint後crashは同一canonical runへ復帰し、完了済みmodelを再実行しない。"""

    fake = _FakeBusiness()
    fake.crash_after_checkpoint = True
    monkeypatch.setattr(release_fixture.module, "_run_release_nopublish_core", fake.core)
    first = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            caller_state_root=release_fixture.worktree_a / "caller-state",
            diagnostics=True,
        ),
        capsys=capsys,
    )
    assert first[0] != 0
    assert fake.model_calls == 1
    assert (release_fixture.canonical_state_root / "fake-business-ledger.json").exists()

    second = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            caller_state_root=release_fixture.worktree_a / "caller-state-2",
            diagnostics=True,
        ),
        capsys=capsys,
    )
    assert second[0] == 0
    assert second[1]["ok"] is True
    assert second[1]["resumed"] is True
    assert second[1]["run_id"] == "fake-local-run-1"
    assert second[1]["modelCallCount"] == 0
    assert fake.model_calls == 1
    assert {call["state_root"] for call in fake.calls} == {release_fixture.canonical_state_root}


def test_alternate_worktree_cannot_reset_canonical_budget(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """caller Bからcanonical Aのcompleted runへ追加消費なしで復帰する。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    PreentryJournal(
        release_fixture.canonical_state_root / "preentry.sqlite3"
    ).bind(
        ISSUE_DATE,
        {
            "issueDate": ISSUE_DATE,
            "artifactRoot": str(release_fixture.worktree_a),
            "stateRoot": str(release_fixture.canonical_state_root),
            "isolationReceipt": str(release_fixture.receipt_a),
            "resultPath": str(fixture["result_path"]),
            "runIdentity": fixture["identity"],
        },
    )
    monkeypatch.setattr(
        release_fixture.module,
        "_run_release_nopublish_core",
        lambda **_kwargs: pytest.fail(
            "alternate worktree saved Green must not enter business core"
        ),
    )
    second = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_b,
            isolation_receipt=release_fixture.receipt_b,
            caller_state_root=release_fixture.worktree_b / "別state",
            diagnostics=True,
        ),
        capsys=capsys,
    )
    assert second[0] == 0
    assert second[1]["ok"] is True
    assert second[1]["run_id"] == fixture["run_id"]
    assert second[1]["resumed"] is True
    assert second[1]["modelCallCount"] == 0
    assert second[1].get("e2eAttemptConsumed", 0) == 0
    assert second[1].get("e2eAttemptCount") == 0


def test_green_rerun_returns_saved_result_without_generation(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Green済み同日runの再呼出しは保存結果を返し、fake businessを再生成しない。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    monkeypatch.setattr(
        release_fixture.module,
        "_run_release_nopublish_core",
        lambda **_kwargs: pytest.fail("saved Green rerun must not enter business core"),
    )
    first = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            diagnostics=True,
        ),
        capsys=capsys,
    )
    second = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            diagnostics=True,
        ),
        capsys=capsys,
    )
    assert first[0] == second[0] == 0
    assert second[1]["ok"] is True
    assert first[1]["run_id"] == second[1]["run_id"] == fixture["run_id"]
    assert second[1]["modelCallCount"] == 0
    assert second[1]["resumed"] is True
    assert second[1]["e2eAttemptConsumed"] == 0
    assert second[1]["e2eAttemptCount"] == 0


def test_reparse_isolation_input_is_rejected_before_business_and_preserves_artifacts(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """reparse pathは開始前に拒否し、既存caller成果物を削除しない。"""

    fake = _FakeBusiness()
    monkeypatch.setattr(release_fixture.module, "_run_release_nopublish_core", fake.core)
    target = release_fixture.worktree_a / "reparse-target"
    target.mkdir()
    (target / "isolation-receipt.json").write_text(
        '{"keep":true}\n',
        encoding="utf-8",
    )
    junction = release_fixture.worktree_a / "reparse-receipt-root"
    _create_windows_directory_junction(junction=junction, target=target)
    reparse = junction / "isolation-receipt.json"
    caller_state = release_fixture.worktree_a / "caller-state"
    caller_state.mkdir()
    state_file, receipt_path = release_fixture.diagnostic_paths(
        repo_root=release_fixture.worktree_a,
        caller_state_root=caller_state,
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("keep-state\n", encoding="utf-8")
    receipt_path.write_text("keep-receipt\n", encoding="utf-8")

    exit_code, result = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=reparse,
            caller_state_root=caller_state,
            diagnostics=True,
        ),
        capsys=capsys,
    )

    assert exit_code != 0
    assert result["ok"] is False
    assert result.get("e2eAttemptConsumed", 0) == 0
    assert any("reparse" in str(item).casefold() for item in result.get("failures", []))
    assert fake.calls == []
    assert state_file.read_text(encoding="utf-8") == "keep-state\n"
    assert receipt_path.read_text(encoding="utf-8") == "keep-receipt\n"


def test_duplicate_writer_is_rejected_before_business_and_consumption(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存daily mutexがbusyなら本体・E2E・artifact生成へ到達しない。"""

    fake = _FakeBusiness()
    monkeypatch.setattr(release_fixture.module, "_run_release_nopublish_core", fake.core)

    @contextmanager
    def busy_mutex(*, timeout_ms: int = 0):
        del timeout_ms
        raise RuntimeError("daily_process_mutex_busy")
        yield

    monkeypatch.setattr(release_fixture.runtime, "daily_process_mutex", busy_mutex)
    exit_code, result = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            diagnostics=True,
        ),
        capsys=capsys,
    )

    assert exit_code != 0
    assert result["ok"] is False
    assert result.get("e2eAttemptConsumed", 0) == 0
    assert any("mutex" in str(item).casefold() for item in result.get("failures", []))
    assert fake.calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows -I -S fake business contract")
def test_windows_isolated_python_runs_fake_business_from_japanese_path(
    release_fixture: _ReleaseFixture,
    tmp_path: Path,
) -> None:
    """実WindowsのPowerShellなし子processでも-I -SとUTF-8 pathを維持する。"""

    script = tmp_path / "日本語の入口" / "fake-local-entry.py"
    script.parent.mkdir(parents=True)
    root = Path(__file__).resolve().parents[1]
    argv = release_fixture.argv(
        repo_root=release_fixture.worktree_a,
        isolation_receipt=release_fixture.receipt_a,
        caller_state_root=release_fixture.worktree_a / "日本語のcaller-state",
        diagnostics=True,
    )
    script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, os.environ['NG_TEST_ROOT'])\n"
        "from tools import news_grasp_release_nopublish as release\n"
        "release._load_release_runtime_modules()\n"
        "release.runtime._windows_local_app_data = lambda: Path(os.environ['NG_TEST_LOCALAPPDATA'])\n"
        "def fake_core(**kwargs):\n"
        "    state = Path(kwargs['state_root'])\n"
        "    state.mkdir(parents=True, exist_ok=True)\n"
        "    return {'schemaVersion': 'NEWS_GRASP_RELEASE_NOPUBLISH_RECEIPT_V1', 'ok': True, 'status': 'publish_dry_run_ok', 'run_id': 'windows-fake-run', 'externalEffectCount': 0, 'e2eAttemptConsumed': 1, 'modelCallCount': 0, 'no_publish': True}\n"
        "release._run_release_nopublish_core = fake_core\n"
        f"raise SystemExit(release._main({json.dumps(argv, ensure_ascii=False)}))\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["NG_TEST_ROOT"] = str(root)
    env["NG_TEST_LOCALAPPDATA"] = str(
        release_fixture.canonical_state_root.parent.parent
    )
    env["PYTHONIOENCODING"] = "utf-8"
    for name in CLAIM_ENVIRONMENT:
        env.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(script)],
        cwd=str(release_fixture.worktree_a),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["ok"] is True
    assert result["externalEffectCount"] == 0
    assert result["no_publish"] is True


def test_public_api_rejects_importable_marker_context_even_with_importable_token(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """import可能なmarker/context/tokenを作れても公開APIを経由できない。"""

    module = release_fixture.module
    run_identity = module._initial_run_identity(
        repo_root=release_fixture.worktree_a,
        source_issue_date=ISSUE_DATE,
        isolation_receipt=release_fixture.receipt_a,
    )
    forged_context = module._LocalEntryContext(
        artifact_root=release_fixture.worktree_a,
        isolation_receipt=release_fixture.receipt_a,
        run_identity=run_identity,
        source_issue_date=ISSUE_DATE,
        state_root=release_fixture.canonical_state_root,
        marker=module._LOCAL_ENTRY_CONTEXT_MARKER,
    )
    forged_capability = module._ReleaseCapability(
        {"claimId": "imported-fake-claim"},
        module._RELEASE_CAPABILITY_MARKER,
    )
    core_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        module,
        "_run_release_nopublish_core",
        lambda **kwargs: core_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="nopublish_public_api_retired_use_cli"):
        module.run_release_nopublish(
            repo_root=release_fixture.worktree_a,
            source_issue_date=ISSUE_DATE,
            state_root=release_fixture.canonical_state_root,
            isolation_receipt=release_fixture.receipt_a,
            capability=forged_capability,
            run_identity=run_identity,
            entry_context=forged_context,
        )
    assert core_calls == []


@pytest.mark.parametrize(
    "diagnostic_case",
    (
        "canonical_preentry",
        "root_tracked_file",
        "same_path",
        "wrong_state_basename",
        "wrong_receipt_basename",
    ),
)
def test_cli_rejects_untrusted_diagnostic_paths_before_start_and_preserves_targets(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    diagnostic_case: str,
) -> None:
    """canonical DB、tracked file、alias path、basename違反を診断出力に使わせない。"""

    module = release_fixture.module
    fake = _FakeBusiness()
    monkeypatch.setattr(module, "_run_release_nopublish_core", fake.core)
    if diagnostic_case == "canonical_preentry":
        target = release_fixture.canonical_state_root / "preentry.sqlite3"
        PreentryJournal(target)
        state_path = target
        receipt_path = target
        before = target.read_bytes()
    elif diagnostic_case == "root_tracked_file":
        target = release_fixture.worktree_a / "fixture.txt"
        state_path = target
        receipt_path = target
        before = target.read_bytes()
    elif diagnostic_case == "same_path":
        target = release_fixture.worktree_a / "build" / "release-nopublish-diagnostics" / "same.json"
        state_path = target
        receipt_path = target
        before = None
    elif diagnostic_case == "wrong_state_basename":
        diagnostic_root = release_fixture.worktree_a / "build" / "release-nopublish-diagnostics" / "attempt"
        state_path = diagnostic_root / "not-state.json"
        receipt_path = diagnostic_root / "receipt.json"
        before = None
        target = state_path
    else:
        diagnostic_root = release_fixture.worktree_a / "build" / "release-nopublish-diagnostics" / "attempt"
        state_path = diagnostic_root / "state.json"
        receipt_path = diagnostic_root / "not-receipt.json"
        before = None
        target = receipt_path

    argv = release_fixture.argv(
        repo_root=release_fixture.worktree_a,
        isolation_receipt=release_fixture.receipt_a,
        caller_state_root=release_fixture.worktree_a / "caller-state",
    )
    argv[argv.index("--state-file") + 1] = str(state_path)
    argv[argv.index("--receipt-path") + 1] = str(receipt_path)
    exit_code, result = _invoke_main(release_fixture, argv=argv, capsys=capsys)

    assert exit_code != 0
    assert result["ok"] is False
    assert result.get("e2eAttemptConsumed", 0) == 0
    assert fake.calls == []
    phases = [
        row["phase"]
        for row in PreentryJournal(
            release_fixture.canonical_state_root / "preentry.sqlite3"
        ).events(ISSUE_DATE)
    ]
    assert "module_started" not in phases
    if diagnostic_case == "canonical_preentry":
        import sqlite3

        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    elif before is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before


@pytest.mark.parametrize(
    "predicate_value",
    (
        pytest.param("missing", id="missing"),
        pytest.param(None, id="none"),
        pytest.param(0, id="zero"),
        pytest.param(False, id="false-allowed"),
    ),
)
def test_isolation_receipt_requires_boolean_false_runner_artifact_predicate(
    release_fixture: _ReleaseFixture,
    predicate_value: object,
) -> None:
    """runnerArtifactPredicateは厳密なFalseだけを隔離Greenとして受け付ける。"""

    receipt = json.loads(release_fixture.receipt_a.read_text(encoding="utf-8"))
    if predicate_value == "missing":
        receipt.pop("runnerArtifactPredicate", None)
    else:
        receipt["runnerArtifactPredicate"] = predicate_value
    release_fixture.receipt_a.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if predicate_value is False:
        validated = release_fixture.module._validate_isolation_receipt(
            repo_root=release_fixture.worktree_a,
            source_issue_date=ISSUE_DATE,
            isolation_receipt=release_fixture.receipt_a,
        )
        assert validated["runnerArtifactPredicate"] is False
    else:
        with pytest.raises(RuntimeError, match="nopublish_isolation_receipt_invalid"):
            release_fixture.module._validate_isolation_receipt(
                repo_root=release_fixture.worktree_a,
                source_issue_date=ISSUE_DATE,
                isolation_receipt=release_fixture.receipt_a,
            )


def test_saved_green_result_requires_bound_completed_runtime_evidence(
    release_fixture: _ReleaseFixture,
) -> None:
    """自己申告だけの保存GreenはDB/receipt/content検証なしに再開させない。"""

    module = release_fixture.module
    result_path = (
        release_fixture.canonical_state_root
        / f"release-nopublish-result-{ISSUE_DATE}.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    self_asserted = {
        "ok": True,
        "status": "publish_dry_run_ok",
    }
    result_path.write_text(
        json.dumps(self_asserted, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_identity = module._initial_run_identity(
        repo_root=release_fixture.worktree_a,
        source_issue_date=ISSUE_DATE,
        isolation_receipt=release_fixture.receipt_a,
    )

    with pytest.raises(RuntimeError, match="nopublish_saved_result_invalid"):
        module._saved_green_result(
            result_path,
            artifact_root=release_fixture.worktree_a,
            canonical_state=release_fixture.canonical_state_root,
            source_issue_date=ISSUE_DATE,
            run_identity=run_identity,
        )


def _build_completed_saved_green_fixture(
    release_fixture: _ReleaseFixture,
) -> dict[str, Any]:
    """既存DirectRunStore/DailyArtifactLedgerでsaved Greenの正ケースを組む。"""

    module = release_fixture.module
    runtime = release_fixture.runtime
    daily = importlib.import_module("tools.news_grasp_daily_gate")
    content = importlib.import_module("tools.news_grasp_daily_content")
    identity = module._initial_run_identity(
        repo_root=release_fixture.worktree_a,
        source_issue_date=ISSUE_DATE,
        isolation_receipt=release_fixture.receipt_a,
    )
    store = runtime.DirectRunStore(
        release_fixture.canonical_state_root,
        test_only_allow_semantic_verifier=True,
    )

    artifact_relatives = (
        f"digest/Summary/{ISSUE_DATE}.md",
        f"build/tts/{ISSUE_DATE}.mp3",
        "build/tts/daily/latest_audio.json",
        f"build/youtube-podcast/{ISSUE_DATE}.mp4",
        f"docs/deepdive/{ISSUE_DATE}/index.html",
        f"build/tts/deepdive/{ISSUE_DATE}.mp3",
        "build/tts/deepdive/latest_audio.json",
        f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4",
        "docs/index.html",
    )

    def integration_handler(**context: Any) -> dict[str, Any]:
        root = release_fixture.worktree_a
        artifact_hashes: dict[str, str] = {}
        for relative in artifact_relatives:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = f"saved-green-fixture:{relative}".encode("utf-8")
            target.write_bytes(payload)
            artifact_hashes[relative] = hashlib.sha256(payload).hexdigest()
        content_receipt = {
            "schemaVersion": content.CONTENT_RECEIPT_SCHEMA,
            "ok": True,
            "status": "materialized",
            "issue_date": ISSUE_DATE,
            "run_id": str(context["run_id"]),
            "scheduled_categories": ["fx", "ai"],
            "reporter_call_count": 0,
            "model_call_count": 0,
            "model_call_count_total": 0,
            "reused_model_artifacts": [],
            "repaired_model_artifacts": [],
            "bundle_id": hashlib.sha256(
                json.dumps(artifact_hashes, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "artifact_hashes": {"docs/index.html": artifact_hashes["docs/index.html"]},
            "derived_artifact_hashes": {
                relative: digest
                for relative, digest in artifact_hashes.items()
                if relative != "docs/index.html"
            },
            "derived": {"ok": True, "status": "built"},
            "repair_plan_sha256": "",
        }
        content._validate_completion_payload(
            root,
            run_id=str(context["run_id"]),
            issue_date=ISSUE_DATE,
            value=content_receipt,
        )
        ledger = runtime.DailyArtifactLedger(
            store,
            run_id=str(context["run_id"]),
            issue_date=ISSUE_DATE,
            writer_lease=str(context["writer_lease"]),
            fencing_token=int(context["fencing_token"]),
        )
        ledger.write_checkpoint(
            artifact_id="content_completion",
            input_hash=content._artifact_input_hash(
                {"issueDate": ISSUE_DATE, "scheduledCategories": ["fx", "ai"]}
            ),
            validator_id="content_completion_artifact_hashes_v1",
            payload=content_receipt,
        )
        completion_path = root / "build" / "daily-content" / str(context["run_id"]) / "completion.json"
        completion_path.parent.mkdir(parents=True, exist_ok=True)
        completion_path.write_text(
            json.dumps(content_receipt, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return daily._producer_result(
            "NEWS_GRASP_SAVED_GREEN_INTEGRATION_RECEIPT_V1",
            ok=True,
            status="verified",
            operation_id="current_issue_integration",
            values={
                "content_generation": content_receipt,
                "release_bundle": {
                    "ok": True,
                    "status": "verified",
                    "bundle_id": content_receipt["bundle_id"],
                    "artifact_hashes": artifact_hashes,
                    "externalEffectCount": 0,
                },
            },
        )

    def noop_handler(**context: Any) -> dict[str, Any]:
        operation_id = str(context["operation_id"])
        return daily._producer_result(
            f"NEWS_GRASP_SAVED_GREEN_{operation_id.upper()}_V1",
            ok=True,
            status="verified",
            operation_id=operation_id,
        )

    def consumer_handler(**context: Any) -> dict[str, Any]:
        run_id = str(context["run_id"])
        current = runtime.inspect_run(store, run_id=run_id)
        external = runtime.get_daily_operation_receipt(
            store,
            run_id=run_id,
            operation_id="external_publication",
        )
        assert isinstance(external, dict)
        previous_applied_at = str(external.get("applied_at") or "")
        observed_at = store.now()
        if previous_applied_at:
            previous = datetime.fromisoformat(previous_applied_at)
            observed_at = max(observed_at, previous + timedelta(microseconds=1))
        observed_text = observed_at.isoformat()
        nonce = "saved-green-fixture-consumer"
        binding = {
            "runId": run_id,
            "issueDate": ISSUE_DATE,
            "runIntent": "release_nopublish",
            "generation": current["generation"],
            "manifestId": current["manifest_id"],
            "fencingBindingHash": runtime.fencing_binding_hash(
                run_id=run_id,
                generation=int(current["generation"]),
                writer_lease=str(context["writer_lease"]),
                fencing_token=int(context["fencing_token"]),
            ),
            "updatedAt": previous_applied_at,
            "observedAt": observed_text,
            "observationNonce": nonce,
        }
        observation = {
            "ok": True,
            "status": "verified",
            "observationToken": nonce,
            "observedAt": observed_text,
            "mode": "consumer_owned_local_nopublish",
            "bundleId": "saved-green-fixture-bundle",
            "artifactCount": len(artifact_relatives),
            "externalEffectCount": 0,
            "failures": [],
        }
        receipt = module._producer_receipt(
            runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
            "consumer_public_verification",
            values={
                "observation": observation,
                "observation_token": nonce,
                "external_operation_id": "release-nopublish-local-observation",
                "freshnessBinding": binding,
            },
        )
        return receipt

    handlers = {
        "static_check": ("tests.saved_green.static", noop_handler),
        "scoped_contract_unit": ("tests.saved_green.scoped", noop_handler),
        "current_issue_integration": ("tests.saved_green.integration", integration_handler),
        "external_publication": (
            "tools.news_grasp_release_nopublish.external_publication",
            module._external_nopublish_receipt,
        ),
        "consumer_public_verification": (
            "tools.news_grasp_release_nopublish.consumer_public_verification",
            consumer_handler,
        ),
        "atomic_completion": ("tests.saved_green.atomic", daily._default_atomic_completion),
    }
    receipts = daily.run_daily_sequence(
        handlers=handlers,
        store=store,
        cwd=release_fixture.worktree_a,
        issue_date=ISSUE_DATE,
        run_intent="release_nopublish",
        automation_id="news-grasp-release-gate",
        scheduler_trigger_at=store.now().isoformat(),
        manifest_id=identity["manifestId"],
        source_baseline=identity["sourceHead"],
        runtime_generation=f"release-nopublish:{identity['sourceHead']}",
        remote_base_sha=identity["sourceHead"],
        allowed_side_effect_ids=(),
        context={"repo_root": release_fixture.worktree_a},
    )
    assert len(receipts) == len(daily.DAILY_OPERATIONS)
    assert receipts[-1]["ok"] is True, json.dumps(
        receipts[-1], ensure_ascii=False, indent=2, default=str
    )
    assert receipts[-1]["status"] == "completed"
    run_id = str(receipts[0]["run_id"])
    saved = {
        "schemaVersion": module.SCHEMA,
        "ok": True,
        "status": "publish_dry_run_ok",
        "source_issue_date": ISSUE_DATE,
        "simulation_issue_date": module.simulation_issue_date(ISSUE_DATE),
        "source_head": identity["sourceHead"],
        "run_id": run_id,
        "operation_count": len(receipts),
        "operation_ids": [str(item["operation_id"]) for item in receipts],
        "externalEffectCount": 0,
        "duplicateSendCount": 0,
        "duplicateUploadCount": 0,
        "failures": [],
        "receipts": receipts,
    }
    saved["receiptSha256"] = module._sha(saved)
    result_path = (
        release_fixture.canonical_state_root
        / f"release-nopublish-result-{ISSUE_DATE}.json"
    )
    result_path.write_text(
        json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "identity": identity,
        "store": store,
        "run_id": run_id,
        "result_path": result_path,
        "artifact_path": release_fixture.worktree_a / "docs" / "index.html",
        "saved": saved,
    }


def test_saved_green_result_accepts_only_real_completed_bound_fixture(
    release_fixture: _ReleaseFixture,
) -> None:
    """completed DB、六receipt、content checkpoint、実bytesを束ねた正ケースだけを受理する。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    result = release_fixture.module._saved_green_result(
        fixture["result_path"],
        artifact_root=release_fixture.worktree_a,
        canonical_state=release_fixture.canonical_state_root,
        source_issue_date=ISSUE_DATE,
        run_identity=fixture["identity"],
    )

    assert result["ok"] is True
    assert result["status"] == "publish_dry_run_ok"
    assert result["modelCallCount"] == 0
    assert result["resumed"] is True


def test_saved_green_result_rejects_tampered_saved_receipt(
    release_fixture: _ReleaseFixture,
) -> None:
    """保存receiptのhash差替えをcompleted DBが補完してGreenにしてはいけない。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    saved = dict(fixture["saved"])
    saved["receiptSha256"] = "0" * 64
    fixture["result_path"].write_text(
        json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="nopublish_saved_result_invalid"):
        release_fixture.module._saved_green_result(
            fixture["result_path"],
            artifact_root=release_fixture.worktree_a,
            canonical_state=release_fixture.canonical_state_root,
            source_issue_date=ISSUE_DATE,
            run_identity=fixture["identity"],
        )


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param("source_date", id="source-date-mismatch"),
        pytest.param("run_id", id="run-id-mismatch"),
    ),
)
def test_saved_green_result_rejects_date_or_run_identity_drift(
    release_fixture: _ReleaseFixture,
    mutation: str,
) -> None:
    """別日・別runの保存Greenを当日completed DBへ再束縛しない。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    saved = dict(fixture["saved"])
    if mutation == "source_date":
        saved["source_issue_date"] = "2026-09-05"
    else:
        saved["run_id"] = "different-run-id"
    saved["receiptSha256"] = release_fixture.module._sha(saved)
    fixture["result_path"].write_text(
        json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="nopublish_saved_result_invalid"):
        release_fixture.module._saved_green_result(
            fixture["result_path"],
            artifact_root=release_fixture.worktree_a,
            canonical_state=release_fixture.canonical_state_root,
            source_issue_date=ISSUE_DATE,
            run_identity=fixture["identity"],
        )


def test_saved_green_result_rejects_artifact_bytes_drift_without_second_consumption(
    release_fixture: _ReleaseFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """成果bytes driftは既存module-start countを保持し、今回の消費を0にする。"""

    fixture = _build_completed_saved_green_fixture(release_fixture)
    fixture["artifact_path"].write_bytes(b"tampered-after-green\n")
    journal = PreentryJournal(release_fixture.canonical_state_root / "preentry.sqlite3")
    prior_detail = {
        "processIdentity": {"pid": 4242, "startTime": "fixture"},
        "modulePath": str(release_fixture.module.__file__),
    }
    journal.append(ISSUE_DATE, "prior-module", "module_entered", prior_detail)
    journal.append(ISSUE_DATE, "prior-module", "module_started", prior_detail)

    content = importlib.import_module("tools.news_grasp_daily_content")
    for name in (
        "_default_candidate_provider",
        "_default_model_runner",
        "_default_derived_builder",
    ):
        monkeypatch.setattr(
            content,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(
                f"saved-result drift must not invoke {_name}"
            ),
        )
    monkeypatch.setattr(
        release_fixture.module,
        "_run_release_nopublish_core",
        lambda **_kwargs: pytest.fail("artifact drift must stop before public entry"),
    )
    exit_code, result = _invoke_main(
        release_fixture,
        argv=release_fixture.argv(
            repo_root=release_fixture.worktree_a,
            isolation_receipt=release_fixture.receipt_a,
            caller_state_root=release_fixture.worktree_a / "saved-green-attempt",
            diagnostics=True,
        ),
        capsys=capsys,
    )

    assert exit_code != 0
    assert result["ok"] is False
    assert result.get("e2eAttemptConsumed", 0) == 0
    assert result.get("e2eAttemptCount") == 1
    assert any("saved_result_invalid" in str(item) for item in result.get("failures", ()))
    assert fixture["artifact_path"].read_bytes() == b"tampered-after-green\n"
