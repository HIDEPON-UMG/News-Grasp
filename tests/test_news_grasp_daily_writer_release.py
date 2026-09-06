"""関数終了後の占有解除と保存run即時復帰を確認する。"""
from datetime import datetime, timezone

import pytest

from tools import news_grasp_daily_gate as daily
from tools import news_grasp_direct_runtime as runtime


def _fixture(tmp_path):
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    store = runtime.DirectRunStore(tmp_path / "state", clock=lambda: now, test_only_allow_semantic_verifier=True)
    kwargs = dict(cwd=tmp_path, issue_date="2026-09-06", run_intent=runtime.RUN_INTENT,
                  source_baseline="a" * 40, remote_base_sha="a" * 40,
                  runtime_generation=runtime.RUNTIME_SCHEMA_V2 + ":" + "a" * 64)
    return store, kwargs


def _release(store, state, **changes):
    return runtime.release_daily_writer_lease(store, run_id=state["run_id"],
        writer_lease=changes.get("writer_lease", state["writer_lease"]), fencing_token=state["fencing_token"])


def test_release_revokes_old_writer_and_resumes_same_run_without_clock_advance(tmp_path):
    store, kwargs = _fixture(tmp_path)
    first = runtime.start_run(store, **kwargs)
    seal = first["start_seal"]
    assert _release(store, first)["status"] == "released"
    with pytest.raises(PermissionError):
        runtime.renew_daily_writer_lease(store, run_id=first["run_id"], writer_lease=first["writer_lease"], fencing_token=first["fencing_token"])
    second = runtime.start_run(store, **kwargs)
    assert second["run_id"] == first["run_id"]
    assert second["status"] == "active"
    assert second["fencing_token"] == first["fencing_token"] + 1
    assert second["start_seal"] == seal


def test_foreign_writer_cannot_release_current_owner(tmp_path):
    store, kwargs = _fixture(tmp_path)
    state = runtime.start_run(store, **kwargs)
    before = store.db_path.read_bytes()
    assert _release(store, state, writer_lease="foreign")["status"] == "not_owner"
    assert store.db_path.read_bytes() == before


def test_completed_run_is_unchanged_by_release(tmp_path):
    store, kwargs = _fixture(tmp_path)
    state = runtime.start_run(store, **kwargs)
    with store.connect() as db:
        db.execute("UPDATE runs SET status='completed' WHERE run_id=?", (state["run_id"],))
        db.commit()
    before = store.db_path.read_bytes()
    assert _release(store, state)["status"] == "not_active"
    assert store.db_path.read_bytes() == before


def test_sequence_exception_releases_its_writer_before_return(tmp_path):
    store, kwargs = _fixture(tmp_path)

    class HandlerStopped(BaseException):
        pass

    def factory(_):
        raise HandlerStopped()

    with pytest.raises(HandlerStopped):
        daily.run_daily_sequence(store=store, handler_factory=factory, **kwargs)
    active = runtime.get_active_run(store, issue_date=kwargs["issue_date"], run_intent=runtime.RUN_INTENT, include_writer=True)
    resumed = runtime.start_run(store, **kwargs)
    assert resumed["status"] == "active"
    assert resumed["run_id"] == active["run_id"]
    assert resumed["fencing_token"] == active["fencing_token"] + 1


def test_release_error_preserves_original_exception(tmp_path, monkeypatch, capsys):
    store, kwargs = _fixture(tmp_path)

    def original(_):
        raise ValueError("元の業務エラー")

    def release_failure(*_, **__):
        raise OSError("返却記録失敗")

    monkeypatch.setattr(runtime, "release_daily_writer_lease", release_failure)
    with pytest.raises(ValueError, match="元の業務エラー"):
        daily.run_daily_sequence(store=store, handler_factory=original, **kwargs)
    assert "writer_release_failed:OSError" in capsys.readouterr().err


def test_finalizing_without_admission_is_not_released_or_renewed(tmp_path):
    store, kwargs = _fixture(tmp_path)
    state = runtime.start_run(store, **kwargs)
    with store.connect() as db:
        db.execute("UPDATE runs SET status='finalizing' WHERE run_id=?", (state["run_id"],))
        db.commit()
    before = store.db_path.read_bytes()
    with pytest.raises(PermissionError, match="finalizer_admission_invalid"):
        _release(store, state)
    with pytest.raises(RuntimeError, match="run_not_writable"):
        runtime.renew_daily_writer_lease(store, run_id=state["run_id"], writer_lease=state["writer_lease"], fencing_token=state["fencing_token"])
    with pytest.raises(RuntimeError, match="run_not_writable"):
        runtime.admit_daily_operation(store, run_id=state["run_id"], writer_lease=state["writer_lease"], fencing_token=state["fencing_token"], operation_id="atomic_completion")
    assert store.db_path.read_bytes() == before
