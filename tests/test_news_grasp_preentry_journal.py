"""本体未到達、crash後再読込、同日重複開始の局所契約。"""

import sqlite3
import os
import subprocess
import sys
import json
from pathlib import Path

import pytest

from tools.news_grasp_preentry_journal import PreentryJournal


def test_preentry_failure_preserves_events_without_consuming_body_attempt(tmp_path):
    path = tmp_path / "journal.sqlite3"
    first = PreentryJournal(path)
    first.append("2026-09-06", "wrapper-1", "wrapper_started", {})
    first.append("2026-09-06", "wrapper-1", "preentry_failed", {"exitCode":76})
    resumed = PreentryJournal(path)
    resumed.append("2026-09-06", "wrapper-2", "wrapper_started", {})
    rows = resumed.events("2026-09-06")
    assert [row["sequence"] for row in rows] == [1,2,3]
    assert not any(row["phase"] == "module_started" for row in rows)


def test_entered_is_not_started_and_confirmed_start_is_idempotent(tmp_path):
    journal = PreentryJournal(tmp_path / "journal.sqlite3")
    identity = {"pid":123,"creationFileTimeUtc":"observed"}
    journal.append("2026-09-06", "one", "module_entered", identity)
    assert [row["phase"] for row in journal.events("2026-09-06")] == ["module_entered"]
    sequence = journal.append("2026-09-06", "one", "module_started", identity)
    assert journal.append("2026-09-06", "one", "module_started", identity) == sequence
    journal.append("2026-09-06", "two", "module_entered", identity)
    with pytest.raises(RuntimeError, match="BODY_ALREADY_STARTED"):
        journal.append("2026-09-06", "two", "module_started", identity)


def test_start_without_matching_entry_observation_is_rejected(tmp_path):
    journal = PreentryJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(RuntimeError, match="START_WITHOUT_OBSERVATION"):
        journal.append("2026-09-06", "one", "module_started", {"pid":1})
    journal.append("2026-09-06", "one", "module_entered", {"pid":1})
    with pytest.raises(RuntimeError, match="START_WITHOUT_OBSERVATION"):
        journal.append("2026-09-06", "one", "module_started", {"pid":2})


def test_history_cannot_be_updated_or_deleted(tmp_path):
    journal = PreentryJournal(tmp_path / "journal.sqlite3")
    journal.append("2026-09-06", "one", "wrapper_started", {})
    with sqlite3.connect(journal.path) as db:
        for sql in ("UPDATE events SET phase='terminal'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError, match="append_only"):
                db.execute(sql)
    assert len(journal.events("2026-09-06")) == 1


def test_unknown_phase_and_oversize_detail_are_rejected(tmp_path):
    journal = PreentryJournal(tmp_path / "journal.sqlite3")
    with pytest.raises(RuntimeError, match="EVENT_INVALID"):
        journal.append("2026-09-06", "one", "unknown", {})
    with pytest.raises(RuntimeError, match="EVENT_TOO_LARGE"):
        journal.append("2026-09-06", "one", "wrapper_started", {"data":"x"*4096})
    assert journal.events("2026-09-06") == []


@pytest.mark.skipif(os.name != "nt", reason="Windows実起動契約")
def test_windows_powershell_isolated_python_failure_and_resume(tmp_path):
    """本体を起動せず、実際のPowerShell→-I -S Pythonで永続記録する。"""
    script = Path(__file__).resolve().parents[1] / "tools/news_grasp_preentry_journal.py"
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    journal_path = tmp_path / "日本語の復旧記録" / "journal.sqlite3"
    env = dict(os.environ, NEWS_GRASP_PREENTRY_JOURNAL=str(journal_path),
               NEWS_GRASP_PREENTRY_ISSUE="2026-09-06", NEWS_GRASP_PREENTRY_SESSION="first")
    env["NG_TEST_PYTHON"] = sys.executable
    env["NG_TEST_SCRIPT"] = str(script)
    command = "& $env:NG_TEST_PYTHON -I -S -B $env:NG_TEST_SCRIPT wrapper_started; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; & $env:NG_TEST_PYTHON -I -S -B $env:NG_TEST_SCRIPT failure --exit-code 76; exit $LASTEXITCODE"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                            env=env, capture_output=True, timeout=15, shell=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    env["NEWS_GRASP_PREENTRY_SESSION"] = "resumed"
    resumed = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), "wrapper_started"],
                             env=env, capture_output=True, timeout=15, shell=False,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    assert resumed.returncode == 0, resumed.stderr
    rows = PreentryJournal(journal_path).events("2026-09-06")
    assert [(row["sessionId"], row["phase"]) for row in rows] == [
        ("first", "wrapper_started"), ("first", "preentry_failed"), ("resumed", "wrapper_started")]
    assert rows[1]["detail"]["exitCode"] == 76


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap実境界")
def test_bootstrap_records_without_python(tmp_path):
    """Pythonに依存しない入口記録を本番と同じPowerShell関数で検証する。"""
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/ops/invoke-scheduled-equivalent-nopublish.ps1").read_text(encoding="utf-8")
    function = source[source.index("function Write-PreentryBootstrap {"):source.index("function Write-PreentryPhase {")]
    script = tmp_path / "bootstrap.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$DateStamp='2026-09-06'\n" + function +
        "\nWrite-PreentryBootstrap 'wrapper_started'\n"
        "Write-PreentryBootstrap 'preentry_failed' -ExitCode 76 -ReasonCode 'PYTHON_START_REJECTED'\n",
        encoding="utf-8-sig")
    journal = tmp_path / "日本語" / "preentry.sqlite3"
    env = dict(os.environ, NEWS_GRASP_PREENTRY_JOURNAL=str(journal), NEWS_GRASP_PREENTRY_SESSION="bootstrap-only")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-File", str(script)],
                            env=env, capture_output=True, timeout=15, shell=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in journal.with_name("preentry-bootstrap.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["phase"] for row in rows] == ["wrapper_started", "preentry_failed"]
    assert rows[1]["exitCode"] == 76
    assert rows[1]["reasonCode"] == "PYTHON_START_REJECTED"
    assert not journal.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows owner/process実境界")
def test_windows_owner_confirms_live_isolated_child_before_ack(tmp_path):
    """実PowerShell→隔離Pythonの生存照合。業務NoPublishは起動しない。"""
    from tools import news_grasp_nopublish_owner as owner
    from tools import news_grasp_owned_process as owned
    from tools import e2e_final_admission_bridge as bridge
    root = Path(__file__).resolve().parents[1]
    fixture_root = tmp_path / "隔離repo"
    child = fixture_root / "tools/news_grasp_release_nopublish.py"
    child.parent.mkdir(parents=True)
    child.write_text(
        "import sys,os,time\nfrom pathlib import Path\nsys.path.insert(0,os.environ['NG_TEST_ROOT'])\n"
        "from tools.news_grasp_preentry_journal import environment_journal\n"
        "from tools.e2e_final_admission_bridge import _query_process_identity\n"
        "j,d,s=environment_journal()\n"
        "j.append(d,s,'module_entered',{'processIdentity':_query_process_identity(os.getpid()),'modulePath':str(Path(__file__).resolve())})\n"
        "deadline=time.monotonic()+15\n"
        "while time.monotonic()<deadline:\n"
        " if any(r['phase']=='module_started' for r in j.events(d,s)):sys.exit(0)\n"
        " time.sleep(.05)\n"
        "sys.exit(124)\n", encoding="utf-8")
    journal = PreentryJournal(tmp_path / "events.sqlite3")
    context = (journal, "2026-09-06", "windows-simulation")
    env = dict(os.environ, NEWS_GRASP_PREENTRY_JOURNAL=str(journal.path),
               NEWS_GRASP_PREENTRY_ISSUE=context[1], NEWS_GRASP_PREENTRY_SESSION=context[2],
               NG_TEST_ROOT=str(root), NG_TEST_PYTHON=sys.executable, NG_TEST_CHILD=str(child))
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    process = owned.spawn_owned([str(powershell), "-NoProfile", "-NonInteractive", "-Command",
                                "& $env:NG_TEST_PYTHON -I -S -B $env:NG_TEST_CHILD; exit $LASTEXITCODE"],
                               cwd=fixture_root, env=env, capture_output=True)
    consumed = []
    confirmed = False
    import runpy
    workspace = Path(os.environ['USERPROFILE']) / 'OneDrive/ドキュメント/ProjectFolders'
    verify = runpy.run_path(str(workspace / 'tools/harness/high_cost_operation_budget.py'))['_verify_module_start_process']
    def consume(detail):
        assert not any(row["phase"] == "module_started" for row in journal.events(context[1]))
        verify(detail['processIdentity'], fixture_root)
        consumed.append(detail)
    def tick():
        nonlocal confirmed
        if not confirmed:
            confirmed = owner._confirm_module_start(
                journal_context=context, parent_pid=process.pid, python_executable=Path(sys.executable),
                repo_root=fixture_root, query_process_identity=bridge._query_process_identity,
                consume_start=consume)
    try:
        result = owner._wait_for_owned_process(process, timeout_seconds=20, on_tick=tick)
    finally:
        process.close()
    assert result["exitCode"] == 0, result
    assert len(consumed) == 1
    assert [row["phase"] for row in PreentryJournal(journal.path).events(context[1])] == ["module_entered", "module_started"]


def test_owner_direct_isolated_import_resolves_product_helpers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / 'isolated-owner.py'
    script.write_text(
        "import sys,runpy\n"
        "runpy.run_path(sys.argv[1])\n"
        "from tools.news_grasp_preentry_journal import environment_journal\n"
        "from tools.news_grasp_p08_evidence import _load_global_module\n"
        "assert callable(environment_journal) and callable(_load_global_module)\n",
        encoding='utf-8')
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(script), str(root/'tools/news_grasp_nopublish_owner.py')],
                            cwd=tmp_path, capture_output=True, timeout=10, shell=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr


def test_module_entry_is_observed_even_when_arguments_are_invalid(tmp_path, monkeypatch):
    """CLI本体到達後の引数拒否を、開始前失敗へ戻さない。業務処理は未実行。"""
    from tools import news_grasp_release_nopublish as release
    from tools import e2e_final_admission_bridge as bridge
    monkeypatch.setenv('NEWS_GRASP_PREENTRY_JOURNAL', str(tmp_path/'entry.sqlite3'))
    monkeypatch.setenv('NEWS_GRASP_PREENTRY_ISSUE', '2026-09-06')
    monkeypatch.setenv('NEWS_GRASP_PREENTRY_SESSION', 'invalid-argv-unit')
    identity = {'pid': os.getpid()}
    monkeypatch.setattr(bridge, '_query_process_identity', lambda _pid: identity)
    observations = []
    monkeypatch.setattr(release, '_await_owner_start_confirmation', lambda root, observed: observations.append((root, observed)))
    with pytest.raises(SystemExit) as error:
        release._main([])
    assert error.value.code == 2
    assert observations == [(Path(release.__file__).resolve().parents[1], identity)]
    assert PreentryJournal(tmp_path/'entry.sqlite3').events('2026-09-06')[0]['phase'] == 'module_loaded'
