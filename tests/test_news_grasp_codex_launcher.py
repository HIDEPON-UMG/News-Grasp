"""利用可能なCLI選択と、起動準備失敗時の予算保存を検証する。"""
from pathlib import Path
import subprocess
import time
import json

import pytest

from tools import news_grasp_daily_content as content
from tools import news_grasp_owned_process as owned


@pytest.mark.parametrize("schema_recovery", [False, True])
def test_launch_failure_preserves_reserved_call_without_new_intent(tmp_path, monkeypatch, schema_recovery):
    request = dict(root=tmp_path, run_id="launcher-recovery", issue_date="2026-09-07",
                   role="editor", category=None, call_id="a" * 64,
                   input_hash="b" * 64, artifact_id="editor")
    call_root = content._model_call_root(tmp_path, request["run_id"], request["call_id"])
    if schema_recovery:
        def interrupted(**kwargs):
            raise SystemExit("schema rejected")
        with pytest.raises(SystemExit):
            content._invoke_persistent_model_call(**request, reservation={}, model_fn=interrupted)
        monkeypatch.setattr(content, "_confirmed_schema_rejection_sha256", lambda _: "c" * 64)
        monkeypatch.setattr(content, "_verified_model_schema_sha256", lambda *a, **k: "d" * 64)
    before = {str(p.relative_to(call_root)): p.read_bytes()
              for p in call_root.rglob("*") if p.is_file()}

    def unavailable():
        raise content.DailyContentError("CODEX_EXECUTABLE_UNAVAILABLE")

    with pytest.raises(content.ModelResultPending, match="executable"):
        content._invoke_persistent_model_call(
            **request, reservation={"idempotent": schema_recovery, "status": "reserved"},
            model_fn=lambda **k: pytest.fail("起動準備失敗後の送信は禁止"),
            codex_executable_provider=unavailable)
    assert {str(p.relative_to(call_root)): p.read_bytes()
            for p in call_root.rglob("*") if p.is_file()} == before


def _installed(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData/Local"))
    old = tmp_path / ".vscode/extensions/openai.chatgpt-old/bin/windows-x86_64/codex.exe"
    current = tmp_path / "AppData/Local/OpenAI/Codex/bin/current/codex.exe"
    for path in (old, current):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    return old, current


def test_executable_removed_after_selection_is_pending_without_quality_failure(tmp_path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    executable.unlink()
    with pytest.raises(content.ModelResultPending, match="executable"):
        content._default_model_runner(
            role="editor", repo_root=tmp_path, issue_date="2026-09-07",
            run_id="launcher-recovery", output_dir=tmp_path,
            codex_executable=executable,
        )


def test_selects_current_desktop_cli_when_old_vscode_cli_remains(tmp_path, monkeypatch):
    old, current = _installed(tmp_path, monkeypatch)

    def probe(command, **kwargs):
        assert command[1:] == ["--version"]
        assert kwargs["timeout"] == 5
        assert kwargs["max_output_bytes"] == 4096
        version = "0.153.1" if Path(command[0]) == current else "0.146.0-alpha.3"
        return owned.OwnedRunResult(returncode=0, stdout=f"codex-cli {version}\n".encode(), stderr=b"")

    monkeypatch.setattr(owned, "run_owned_bounded", probe)
    assert content._resolve_codex_executable() == current


def test_failed_version_probe_does_not_hide_other_installed_cli(tmp_path, monkeypatch):
    old, current = _installed(tmp_path, monkeypatch)

    def probe(command, **kwargs):
        if Path(command[0]) == old:
            raise subprocess.TimeoutExpired(command, 5)
        return owned.OwnedRunResult(returncode=0, stdout=b"codex-cli 0.153.1\n", stderr=b"")

    monkeypatch.setattr(owned, "run_owned_bounded", probe)
    assert content._resolve_codex_executable() == current


def test_same_version_prefers_desktop_deterministically(tmp_path, monkeypatch):
    _, current = _installed(tmp_path, monkeypatch)
    monkeypatch.setattr(owned, "run_owned_bounded", lambda *a, **k: owned.OwnedRunResult(returncode=0, stdout=b"codex-cli 0.153.1\n", stderr=b""))
    assert content._resolve_codex_executable() == current


def test_reparse_candidate_is_rejected_before_probe(tmp_path, monkeypatch):
    old, current = _installed(tmp_path, monkeypatch)
    monkeypatch.setattr(content, "_has_reparse_ancestor", lambda path: Path(path) == current)
    probes = []

    def probe(command, **kwargs):
        probes.append(Path(command[0]))
        return owned.OwnedRunResult(returncode=0, stdout=b"codex-cli 0.146.0-alpha.3\n", stderr=b"")

    monkeypatch.setattr(owned, "run_owned_bounded", probe)
    assert content._resolve_codex_executable() == old
    assert current not in probes


def test_preentry_failure_leaves_model_budget_and_intent_untouched(tmp_path, monkeypatch):
    from tools import news_grasp_direct_runtime as runtime
    from tests.test_news_grasp_daily_content import ISSUE_DATE, _candidate_provider

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(store, cwd=tmp_path, issue_date=ISSUE_DATE,
                            run_intent=runtime.RUN_INTENT, manifest_id="f" * 64)

    def unavailable():
        raise content.DailyContentError("CODEX_EXECUTABLE_UNAVAILABLE")

    monkeypatch.setattr(content, "_resolve_codex_executable", unavailable)
    with pytest.raises(content.DailyContentError):
        content.produce_current_issue(
            repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=run["run_id"],
            scheduled_categories=("fx",), candidate_provider=_candidate_provider,
            derived_builder=lambda **_: pytest.fail("起動準備失敗後に生成しました"),
            runtime_store=store, writer_lease=run["writer_lease"], fencing_token=run["fencing_token"],
        )
    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily_model_calls WHERE run_id=?", (run["run_id"],)).fetchone()[0]
    assert count == 0
    assert not list((tmp_path / "build").rglob("intent.json"))


def test_parallel_reporters_share_one_prepared_cli(tmp_path, monkeypatch):
    from tools import news_grasp_direct_runtime as runtime
    from tests.test_news_grasp_daily_content import ISSUE_DATE, _candidate_provider
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(store, cwd=tmp_path, issue_date=ISSUE_DATE,
                            run_intent=runtime.RUN_INTENT, manifest_id="f" * 64)
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    probes, launches = [], []

    class Observed(BaseException):
        pass

    def resolve():
        probes.append(True)
        time.sleep(0.03)
        return executable

    def runner(**kwargs):
        launches.append(kwargs.get("codex_executable"))
        raise Observed()

    monkeypatch.setattr(content, "_resolve_codex_executable", resolve)
    monkeypatch.setattr(content, "_default_model_runner", runner)
    with pytest.raises(Observed):
        content.produce_current_issue(
            repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=run["run_id"],
            scheduled_categories=("fx", "ai"), candidate_provider=_candidate_provider,
            derived_builder=lambda **_: pytest.fail("今回の試験はmodel入口までです"),
            runtime_store=store, writer_lease=run["writer_lease"], fencing_token=run["fencing_token"],
        )
    assert probes == [True]
    assert launches == [executable, executable]


def test_saved_raw_is_recovered_even_when_cli_is_unavailable(tmp_path, monkeypatch):
    from tools import news_grasp_direct_runtime as runtime
    from tests.test_news_grasp_daily_content import ISSUE_DATE, _candidate_provider, _record, _digest
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(store, cwd=tmp_path, issue_date=ISSUE_DATE,
                            run_intent=runtime.RUN_INTENT, manifest_id="f" * 64)
    args = dict(repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=run["run_id"],
                scheduled_categories=("fx",), candidate_provider=_candidate_provider,
                derived_builder=lambda **_: pytest.fail("Editor生成前の試験です"),
                runtime_store=store, writer_lease=run["writer_lease"], fencing_token=run["fencing_token"])

    def save_before_crash(**request):
        payload = {"category": "fx", "issue_date": ISSUE_DATE, "records": [_record("fx")],
                   "digest_markdown": _digest("fx"), "search_audit": request["search_audit"]}
        Path(request["raw_path"]).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        raise SystemExit("保存後の中断")

    with pytest.raises(SystemExit):
        content.produce_current_issue(**args, model_runner=save_before_crash)

    def unavailable():
        raise content.DailyContentError("CODEX_EXECUTABLE_UNAVAILABLE")

    monkeypatch.setattr(content, "_resolve_codex_executable", unavailable)
    with pytest.raises(content.DailyContentError, match="CODEX_EXECUTABLE_UNAVAILABLE"):
        content.produce_current_issue(**args)
    ledger = runtime.DailyArtifactLedger(store, run_id=run["run_id"], issue_date=ISSUE_DATE,
                                         writer_lease=run["writer_lease"], fencing_token=run["fencing_token"])
    assert ledger.list_checkpoints()["reporter:fx"]["status"] == "Green"
    with store.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM daily_model_calls WHERE run_id=?", (run["run_id"],)).fetchone()[0]
    assert count == 1
