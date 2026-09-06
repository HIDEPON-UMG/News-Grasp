"""WP11のmodel結果保持・同一run回収境界。"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.test_news_grasp_daily_content import (
    ISSUE_DATE,
    RUN_ID,
    _candidate_provider,
    _deepdive,
    _digest,
    _record,
    _summary,
)


def _write_call_intent_and_raw(
    repo: Path,
    *,
    run_id: str,
    issue_date: str,
    role: str,
    category: str | None,
    call_id: str,
    input_hash: str,
    payload: dict[str, Any],
) -> None:
    call_root = repo / "build" / "daily-content" / run_id / "model-calls" / call_id
    call_root.mkdir(parents=True, exist_ok=True)
    intent = {
        "schemaVersion": "NEWS_GRASP_MODEL_CALL_INTENT_V1",
        "runId": run_id,
        "issueDate": issue_date,
        "role": role,
        "category": category,
        "callId": call_id,
        "inputHash": input_hash,
        "expectedResultFilename": "raw.json",
    }
    (call_root / "intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (call_root / "raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_call_intent_only(
    repo: Path,
    *,
    run_id: str,
    issue_date: str,
    role: str,
    category: str | None,
    call_id: str,
    input_hash: str,
) -> None:
    call_root = repo / "build" / "daily-content" / run_id / "model-calls" / call_id
    call_root.mkdir(parents=True, exist_ok=True)
    intent = {
        "schemaVersion": "NEWS_GRASP_MODEL_CALL_INTENT_V1",
        "runId": run_id,
        "issueDate": issue_date,
        "role": role,
        "category": category,
        "callId": call_id,
        "inputHash": input_hash,
        "expectedResultFilename": "raw.json",
    }
    (call_root / "intent.json").write_text(
        json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _derived_builder(tmp_path: Path):
    """新しいderived ledger契約を満たす、外部効果なしのfixture。"""

    paths = {
        "daily_audio_script": f"digest/Summary/{ISSUE_DATE}-audio-script.md",
        "daily_audio": f"build/tts/{ISSUE_DATE}.mp3",
        "daily_audio_projection": "build/tts/daily/latest_audio.json",
        "daily_video": f"build/youtube-podcast/{ISSUE_DATE}.mp4",
        "deepdive_html": f"docs/deepdive/{ISSUE_DATE}/index.html",
        "deepdive_audio": f"build/tts/deepdive/{ISSUE_DATE}.mp3",
        "deepdive_audio_projection": "build/tts/deepdive/latest_audio.json",
        "deepdive_video": f"build/youtube-podcast-deepdive/{ISSUE_DATE}.mp4",
        "site_html": "docs/index.html",
    }

    def build(**context: Any) -> dict[str, Any]:
        built: list[str] = []
        repair_actions = context.get("repair_actions", {})
        for artifact_id, relative in paths.items():
            if repair_actions.get(artifact_id) == "reuse":
                continue
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"{artifact_id}:{ISSUE_DATE}".encode("utf-8"))
            built.append(str(target))
        return {"ok": True, "status": "built", "artifacts": built}

    return build


def test_same_run_recovers_persisted_raw_result_without_model_or_repair_reservation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """checkpoint直前終了後はrawを再検証し、同じcallを再送しない。"""

    from tools import news_grasp_direct_runtime as runtime
    from tools.news_grasp_daily_content import produce_current_issue

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-04T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at=clock().isoformat(),
    )

    real_commit = runtime.DailyArtifactLedger.commit_model_call
    model_calls: list[tuple[str, str | None]] = []

    def model_runner(*, role: str, category: str | None = None, **context: Any):
        model_calls.append((role, category))
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT call_id,input_hash
                FROM daily_model_calls
                WHERE run_id=? AND status='reserved'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (run["run_id"],),
            ).fetchone()
        assert row is not None
        call_id, input_hash = str(row[0]), str(row[1])
        if role == "reporter":
            payload = {
                "category": category,
                "issue_date": ISSUE_DATE,
                "records": [_record(str(category))],
                "digest_markdown": _digest(str(category)),
                "search_audit": context["search_audit"],
            }
        elif role == "editor":
            payload = {
                "issue_date": ISSUE_DATE,
                "inputs": {},
                "append_records": [_record("fx")],
                "summary_markdown": _summary(),
            }
        elif role == "deepdive":
            payload = _deepdive()
            payload["article_markdown"] = payload["article_markdown"].replace(
                "https://example.com/ai/",
                "https://example.com/fx/",
            )
        else:
            raise AssertionError(role)
        _write_call_intent_and_raw(
            tmp_path,
            run_id=run["run_id"],
            issue_date=ISSUE_DATE,
            role=role,
            category=category,
            call_id=call_id,
            input_hash=input_hash,
            payload=payload,
        )
        return payload

    def crash_after_deepdive_raw(self, *, call_id: str, artifacts: Any):
        with store.connect() as conn:
            row = conn.execute(
                "SELECT artifact_id FROM daily_model_calls WHERE run_id=? AND call_id=?",
                (run["run_id"], call_id),
            ).fetchone()
        if row is not None and str(row[0]) == "deepdive_model":
            raise SystemExit("simulated_process_end_after_raw_persist")
        return real_commit(self, call_id=call_id, artifacts=artifacts)

    monkeypatch.setattr(runtime.DailyArtifactLedger, "commit_model_call", crash_after_deepdive_raw)

    with pytest.raises(SystemExit, match="simulated_process_end_after_raw_persist"):
        produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=run["run_id"],
            scheduled_categories=("fx",),
            candidate_provider=_candidate_provider,
            model_runner=model_runner,
            derived_builder=_derived_builder(tmp_path),
            runtime_store=store,
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
        )

    assert model_calls == [("reporter", "fx"), ("editor", None), ("deepdive", None)]
    first_usage = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    ).model_call_usage()
    assert first_usage == {"initial": 3, "repair": 0, "total": 3}

    monkeypatch.setattr(runtime.DailyArtifactLedger, "commit_model_call", real_commit)
    resume_model_calls: list[tuple[str, str | None]] = []

    def no_model_on_resume(**kwargs: Any):
        resume_model_calls.append((str(kwargs.get("role")), kwargs.get("category")))
        pytest.fail("model was re-sent during raw-result recovery")

    resumed = produce_current_issue(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        scheduled_categories=("fx",),
        candidate_provider=lambda *_: pytest.fail("candidate provider was repeated"),
        model_runner=no_model_on_resume,
        derived_builder=_derived_builder(tmp_path),
        runtime_store=store,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )

    assert resumed["ok"] is True
    assert resumed["model_call_count"] == 0
    assert resume_model_calls == []
    assert runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    ).model_call_usage() == {"initial": 3, "repair": 0, "total": 3}


def test_intent_only_result_stays_pending_without_repair_or_failure_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """intentだけでrawがない予約は保留し、repairを新設しない。"""

    from tools import news_grasp_daily_content as content
    from tools import news_grasp_direct_runtime as runtime

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-04T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "articles.jsonl").write_text("", encoding="utf-8")
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at=clock().isoformat(),
        runtime_generation=runtime.RUNTIME_SCHEMA_V2,
        allowed_side_effect_ids=(),
    )
    model_calls: list[tuple[str, str | None]] = []

    def model_runner(*, role: str, category: str | None = None, **context: Any):
        model_calls.append((role, category))
        with store.connect() as conn:
            row = conn.execute(
                """
                SELECT call_id,input_hash
                FROM daily_model_calls
                WHERE run_id=? AND status='reserved'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (run["run_id"],),
            ).fetchone()
        assert row is not None
        call_id, input_hash = str(row[0]), str(row[1])
        if role == "reporter":
            return {
                "category": category,
                "issue_date": ISSUE_DATE,
                "records": [_record(str(category))],
                "digest_markdown": _digest(str(category)),
                "search_audit": context["search_audit"],
            }
        if role == "editor":
            return {
                "issue_date": ISSUE_DATE,
                "inputs": {},
                "append_records": [_record("fx")],
                "summary_markdown": _summary(),
            }
        if role == "deepdive":
            _write_call_intent_only(
                tmp_path,
                run_id=run["run_id"],
                issue_date=ISSUE_DATE,
                role=role,
                category=category,
                call_id=call_id,
                input_hash=input_hash,
            )
            raise SystemExit("simulated_process_end_after_intent_only")
        raise AssertionError(role)

    with pytest.raises(SystemExit, match="simulated_process_end_after_intent_only"):
        content.produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=run["run_id"],
            scheduled_categories=("fx",),
            candidate_provider=_candidate_provider,
            model_runner=model_runner,
            derived_builder=_derived_builder(tmp_path),
            runtime_store=store,
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
        )

    assert model_calls == [("reporter", "fx"), ("editor", None), ("deepdive", None)]
    pending_type = getattr(content, "ModelResultPending", None)
    assert pending_type is not None, "production must expose ModelResultPending"

    with pytest.raises(pending_type, match="MODEL_RESULT_PENDING"):
        content.produce_current_issue(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=run["run_id"],
            scheduled_categories=("fx",),
            candidate_provider=lambda *_: pytest.fail("candidate provider was repeated"),
            model_runner=lambda **_: pytest.fail("model was re-sent for intent-only call"),
            derived_builder=_derived_builder(tmp_path),
            runtime_store=store,
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
        )

    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    assert ledger.model_call_usage() == {"initial": 3, "repair": 0, "total": 3}
    assert ledger.load_failure("deepdive_model") is None


def test_reserved_model_call_rebinds_to_new_writer_without_stale_commit(
    tmp_path: Path,
) -> None:
    """旧writerを拒否し、新writerだけがreserved callを回収できる。"""

    from tools import news_grasp_direct_runtime as runtime

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-04T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    run = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at=clock().isoformat(),
        runtime_generation=runtime.RUNTIME_SCHEMA_V2,
        allowed_side_effect_ids=(),
    )
    old_ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    old_ledger.reserve_model_call(
        call_id="writer-rebind-call",
        budget_class="initial",
        artifact_id="deepdive_model",
        input_hash="i" * 64,
    )

    clock.value += store.lease_ttl + timedelta(seconds=1)
    recovered = runtime.start_run(
        store,
        cwd=tmp_path,
        issue_date=ISSUE_DATE,
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at=run["scheduler_trigger_at"],
        runtime_generation=runtime.RUNTIME_SCHEMA_V2,
        allowed_side_effect_ids=(),
    )
    assert recovered["run_id"] == run["run_id"]
    assert recovered["fencing_token"] == run["fencing_token"] + 1
    with pytest.raises(PermissionError):
        old_ledger.reserve_model_call(
            call_id="writer-rebind-call",
            budget_class="initial",
            artifact_id="deepdive_model",
            input_hash="i" * 64,
        )

    new_ledger = runtime.DailyArtifactLedger(
        store,
        run_id=recovered["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=recovered["writer_lease"],
        fencing_token=recovered["fencing_token"],
    )
    receipt = new_ledger.reserve_model_call(
        call_id="writer-rebind-call",
        budget_class="initial",
        artifact_id="deepdive_model",
        input_hash="i" * 64,
    )
    assert receipt["idempotent"] is True
    with store.connect() as conn:
        row = conn.execute(
            "SELECT fencing_token,status FROM daily_model_calls WHERE run_id=? AND call_id=?",
            (run["run_id"], "writer-rebind-call"),
        ).fetchone()
    assert row is not None
    assert int(row[0]) == recovered["fencing_token"]
    new_ledger.commit_model_call(
        call_id="writer-rebind-call",
        artifacts={
            "deepdive_model": {
                "inputHash": "i" * 64,
                "validatorId": "deepdive_output_valid_v1",
                "payload": {"article_markdown": "raw", "dialogue_markdown": "raw"},
            }
        },
    )
    assert new_ledger.model_call_usage() == {"initial": 1, "repair": 0, "total": 1}


def test_windows_owned_process_streams_small_utf8_output_to_sinks_while_waiting(
    tmp_path: Path,
) -> None:
    """実Windows childのstdinと小出力を待機中から逐次保存する。"""

    if os.name != "nt":
        pytest.skip("このcontractはWindows実process専用")
    from tools.news_grasp_owned_process import run_owned_bounded

    child = tmp_path / "small_output_child.py"
    child.write_text(
        "import sys, time\n"
        "payload = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write('小出力:'.encode('utf-8') + payload)\n"
        "sys.stdout.buffer.flush()\n"
        "sys.stderr.buffer.write('stderr'.encode('utf-8'))\n"
        "sys.stderr.buffer.flush()\n"
        "time.sleep(1.0)\n",
        encoding="utf-8",
    )
    stdin_path = tmp_path / "stdin.txt"
    stdin_path.write_bytes("日本語stdin".encode("utf-8"))
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    stdout_sink = stdout_path.open("ab", buffering=0)
    stderr_sink = stderr_path.open("ab", buffering=0)
    result_box: dict[str, Any] = {}

    def run_child() -> None:
        try:
            result_box["result"] = run_owned_bounded(
                [sys.executable, str(child)],
                cwd=tmp_path,
                stdin_path=stdin_path,
                timeout=None,
                max_output_bytes=4096,
                stdout_sink=stdout_sink,
                stderr_sink=stderr_sink,
            )
        except BaseException as exc:  # noqa: BLE001 - surface API Red precisely.
            result_box["error"] = exc

    worker = threading.Thread(target=run_child, daemon=True)
    worker.start()
    observed_while_waiting = False
    deadline = time.monotonic() + 5.0
    while worker.is_alive() and time.monotonic() < deadline:
        if stdout_path.is_file() and "小出力:日本語stdin".encode("utf-8") in stdout_path.read_bytes():
            observed_while_waiting = True
            break
        time.sleep(0.02)
    worker.join(timeout=5.0)
    stdout_sink.close()
    stderr_sink.close()

    if "error" in result_box:
        raise result_box["error"]
    assert observed_while_waiting is True
    result = result_box["result"]
    assert result.returncode == 0
    assert result.timed_out is False
    assert result.output_exceeded is False
    assert "小出力:日本語stdin".encode("utf-8") in stdout_path.read_bytes()
    assert b"stderr" in stderr_path.read_bytes()
