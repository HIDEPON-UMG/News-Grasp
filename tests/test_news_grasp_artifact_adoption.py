"""公開runへの成果採用は生成と公開副作用を呼ばず、検証済みbytesだけを使う。"""
from __future__ import annotations

import hashlib
import json
import runpy
import sqlite3
from contextlib import nullcontext
from pathlib import Path

import pytest


@pytest.mark.parametrize("forbidden_path", [None, "tools/news_grasp_daily_content.py", "data/articles.jsonl", "digest/Summary/2026-09-06.md"])
def test_adoption_reuses_models_and_audio_without_touching_other_issue(tmp_path: Path, forbidden_path: str | None, monkeypatch) -> None:
    from tools import news_grasp_artifact_adoption as adoption
    from tools import news_grasp_daily_content as content
    from tools import news_grasp_direct_runtime as runtime

    fixtures = runpy.run_path(str(Path(__file__).with_name("test_news_grasp_daily_content.py")))
    issue = fixtures["ISSUE_DATE"]
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "data").mkdir()
    history = b'{"date":"2026-09-06","title":"preserve"}\n'
    (target / "data/articles.jsonl").write_bytes(history)
    payloads = {}
    reporters = []
    for category in ("fx", "ai"):
        candidates, audit = fixtures["_candidate_provider"](category, issue)
        payloads[f"candidate:{category}"] = {"candidates": candidates, "search_audit": audit}
        row = fixtures["_model_runner"](role="reporter", category=category, search_audit=audit)
        row = content._validate_reporter(row, category=category, issue_date=issue, search_audit=audit)
        payloads[f"reporter:{category}"] = row
        reporters.append(row)
    payloads["editor"] = fixtures["_model_runner"](role="editor")
    payloads["deepdive_model"] = content._validate_deepdive(
        fixtures["_deepdive"](), issue_date=issue,
        allowed_urls={record[key] for reporter in reporters for record in reporter["records"] for key in ("url", "thumb")},
    )
    for artifact, relative, value in (
        ("deepdive_article", f"digest/DeepDive/{issue}-DeepDive.md", payloads["deepdive_model"]["article_markdown"].encode()),
        ("deepdive_dialogue", f"digest/DeepDive/{issue}-DeepDive-dialogue.md", payloads["deepdive_model"]["dialogue_markdown"].encode()),
        ("summary", f"digest/Summary/{issue}.md", fixtures["_summary"]().encode()),
        ("daily_audio_script", f"digest/Summary/{issue}-audio-script.md", b"audio-script"),
        ("daily_audio", f"build/tts/{issue}.mp3", b"preserved-audio"),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        payloads[artifact] = {"artifactHashes": {relative: hashlib.sha256(value).hexdigest()}}
    if forbidden_path is not None:
        invalid = source / forbidden_path
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_bytes(b"forbidden-owner")
        payloads["summary"] = {"artifactHashes": {forbidden_path: hashlib.sha256(invalid.read_bytes()).hexdigest()}}
    checkpoints = {
        key: {"artifactId": key, "status": "Green", "payload": value,
              "outputHash": hashlib.sha256(runtime._json_dump(value).encode()).hexdigest()}
        for key, value in payloads.items()
    }
    checkpoints["content_completion"] = {"status": "Green", "payload": {"artifactHashes": {"data/articles.jsonl": "f" * 64}}}
    snapshot = adoption.ArtifactSource(source, "source-run", issue, runtime._json_dump(checkpoints).encode())
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(store, cwd=target, issue_date=issue, run_intent=runtime.RUN_INTENT, manifest_id="f" * 64)
    ledger = runtime.DailyArtifactLedger(
        store, run_id=run["run_id"], issue_date=issue,
        writer_lease=run["writer_lease"], fencing_token=run["fencing_token"],
    )
    if forbidden_path is not None:
        with pytest.raises(ValueError):
            adoption.adopt_artifact_source(snapshot, repo_root=target, ledger=ledger, categories=("fx", "ai"))
        assert (target / "data/articles.jsonl").read_bytes() == history
        assert ledger.list_checkpoints() == {}
        assert sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()) == [str(Path("data/articles.jsonl"))]
        return
    monkeypatch.setattr(adoption, "_capture_quality_evidence", lambda *args: {f"data/deepdive-quality-review/{issue}.json": b'{}'})
    result = adoption.adopt_artifact_source(snapshot, repo_root=target, ledger=ledger, categories=("fx", "ai"))
    assert set(result["adoptedArtifactIds"]) == set(payloads)
    assert ledger.model_call_usage()["total"] == 0
    assert (target / f"build/tts/{issue}.mp3").read_bytes() == b"preserved-audio"
    assert (target / "data/articles.jsonl").read_bytes() == history
    assert ledger.list_checkpoints()["editor"]["outputHash"] == checkpoints["editor"]["outputHash"]
    again = adoption.adopt_artifact_source(snapshot, repo_root=target, ledger=ledger, categories=("fx", "ai"))
    assert again["adoptedArtifactIds"] == []
    dialogue_path = target / f"digest/DeepDive/{issue}-DeepDive-dialogue.md"
    dialogue_before = dialogue_path.read_bytes()
    dialogue_path.write_bytes(b'changed-target-dialogue')
    with pytest.raises(ValueError):
        adoption.adopt_artifact_source(snapshot, repo_root=target, ledger=ledger, categories=("fx", "ai"))
    assert dialogue_path.read_bytes() == b'changed-target-dialogue'
    dialogue_path.write_bytes(dialogue_before)
    (source / f"build/tts/{issue}.mp3").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="adoption_source_file_hash"):
        adoption.adopt_artifact_source(snapshot, repo_root=target, ledger=ledger, categories=("fx", "ai"))


def test_adoption_rejects_other_issue_before_any_target_write(tmp_path: Path) -> None:
    from tools import news_grasp_artifact_adoption as adoption

    class Ledger:
        issue_date = "2026-09-06"
    snapshot = adoption.ArtifactSource(tmp_path, "source-run", "2026-09-05", b"{}")
    with pytest.raises(ValueError, match="adoption_issue_date_mismatch"):
        adoption.adopt_artifact_source(snapshot, repo_root=tmp_path, ledger=Ledger(), categories=("fx",))


def test_capture_rejects_unfinished_source(tmp_path: Path) -> None:
    from tools import news_grasp_artifact_adoption as adoption
    database = tmp_path / "source.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE runs(run_id TEXT,status TEXT,issue_date TEXT,cwd TEXT)")
        db.execute("INSERT INTO runs VALUES('source','active','2026-09-05',?)", (str(tmp_path),))
    with pytest.raises(ValueError, match="adoption_source_not_completed"):
        adoption.capture_artifact_source(repo_root=tmp_path, database=database, run_id="source", issue_date="2026-09-05")


def test_public_recovery_requires_canonical_green_before_start(tmp_path: Path, monkeypatch) -> None:
    from tools import news_grasp_recover_public as recovery
    with sqlite3.connect(tmp_path / "preentry.sqlite3") as db:
        db.execute("CREATE TABLE run_bindings(issue_date TEXT,detail TEXT)")
        db.execute("INSERT INTO run_bindings VALUES(?,?)", ("2026-09-05", json.dumps({"artifactRoot": str(tmp_path), "runIdentity": {}})))
    monkeypatch.setattr(recovery.release, "_canonical_release_state_root", lambda: tmp_path)
    monkeypatch.setattr(recovery.release, "_saved_green_result", lambda *args, **kwargs: None)
    def forbidden(**kwargs):
        raise AssertionError("公開runは開始してはいけません")
    monkeypatch.setattr(recovery.runtime, "run_daily_mainline", forbidden)
    with pytest.raises(ValueError, match="nopublish_green_required"):
        recovery.recover_public(repo_root=tmp_path, state_root=tmp_path / "target", issue_date="2026-09-05")
    assert not (tmp_path / "target").exists()


def test_normal_daily_does_not_add_adoption_dependency(tmp_path: Path, monkeypatch) -> None:
    from tools import news_grasp_direct_runtime as runtime
    observed = []
    monkeypatch.setattr(runtime, "daily_process_mutex", lambda **kwargs: nullcontext())
    monkeypatch.setattr(runtime, "_run_daily_mainline_locked", lambda **kwargs: observed.append(kwargs) or {"ok": True})
    kwargs = dict(repo_root=tmp_path, state_root=tmp_path / "state", issue_date="2026-09-06", scheduler_trigger_at="2026-09-06T06:00:00+09:00")
    runtime.run_daily_mainline(**kwargs)
    assert "artifact_source" not in observed[-1]
    marker = object()
    with pytest.raises(TypeError):
        runtime.run_daily_mainline(**kwargs, artifact_source=marker)
    assert len(observed) == 1


def test_gate_seeds_before_content_repair_plan(tmp_path: Path, monkeypatch) -> None:
    from tools import news_grasp_artifact_adoption as adoption
    from tools import news_grasp_daily_content as content
    from tools import news_grasp_daily_gate as gate
    from tools import news_grasp_direct_runtime as runtime
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    order = []
    marker = object()
    monkeypatch.setattr(gate, "_daily_artifact_ledger", lambda context: marker)
    def adopt(source, **kwargs):
        assert source is marker and kwargs["ledger"] is marker
        order.append("adopt")
        return {"adoptedArtifactIds": ["editor"]}
    class ReachedContent(BaseException):
        pass
    def produce(**kwargs):
        order.append("produce")
        raise ReachedContent()
    monkeypatch.setattr(adoption, "adopt_artifact_source", adopt)
    monkeypatch.setattr(content, "produce_current_issue", produce)
    with pytest.raises(ReachedContent):
        gate._default_current_issue_integration(
            store=store, run_id="fixture-run", generation_id="fixture-generation",
            cwd=tmp_path, issue_date="2026-09-05", **adoption._authorized_adoption_context(marker),
            route_capability={"capability": "scheduled_production_daily"}, content_model_runner=lambda: None,
        )
    assert order == ["adopt", "produce"]


@pytest.mark.parametrize("route", ["sequence", "operation", "producer"])
def test_public_gate_routes_reject_untrusted_adoption_before_mutation(tmp_path: Path, monkeypatch, route: str) -> None:
    from tools import news_grasp_daily_gate as gate
    from tools import news_grasp_direct_runtime as runtime
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    before = store.db_path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("未承認sourceでwriterを開始してはいけません")
    monkeypatch.setattr(gate, "_resolve_run", forbidden)
    context = {"artifact_source": object(), "_artifact_adoption_authority": object()}
    with pytest.raises(ValueError, match="artifact_adoption_authority_required"):
        if route == "sequence":
            gate.run_daily_sequence(store=store, cwd=tmp_path, issue_date="2026-09-05", context=context)
        elif route == "operation":
            gate.run_daily_operation("static_check", store=store, cwd=tmp_path, issue_date="2026-09-05", context=context)
        else:
            gate._default_current_issue_integration(store=store, cwd=tmp_path, issue_date="2026-09-05", **context)
    assert store.db_path.read_bytes() == before
