from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest


class _Verifier:
    def verify(self, stage_id: str, *, run: dict, caller_result: dict, observed_surface: dict) -> dict:
        del caller_result, observed_surface
        if stage_id == "title_control":
            return {
                "ok": True,
                "status": "green",
                "title_status": "already_ok",
                "actual_title": "26/09/01 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
                "post_publish_issue_list": [],
            }
        if stage_id == "public_completion":
            surfaces = {
                name: {"issue_date": run["issue_date"], "semantic_ok": True, "status": "verified"}
                for name in importlib.import_module("tools.news_grasp_direct_runtime").PUBLIC_SURFACES
            }
            return {
                "ok": True,
                "status": "verified",
                "completion_mode": "direct_public_v2",
                "issue_date": run["issue_date"],
                "public_surfaces": surfaces,
            }
        return {"ok": True, "status": "green", "evidenceRef": f"fixture:{stage_id}"}


def test_r10_stage_20_probe_and_exact_finalizer_have_no_circular_precondition(tmp_path) -> None:
    """R10: 工程0〜19完了時にprobeがGreenとなり、20だけをatomic finalizeする。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(
            store,
            run_id=run["run_id"],
            stage_id=stage_id,
            writer_lease=run["writer_lease"],
            semantic_verifier=verifier,
        )
    probe = api.probe_public_completion(store, run_id=run["run_id"], semantic_verifier=verifier)
    assert probe["ok"] is True
    final = api.finalize_public_completion(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        semantic_verifier=verifier,
        exact_successor="public_completion",
    )
    assert final["status"] in {"complete", "completed"}
    assert final["current_stage"] in {None, ""}


def test_generic_advance_cannot_close_public_completion(tmp_path: Path) -> None:
    """工程20はnonce/CAS finalizer以外のmutation routeを持たない。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=verifier)
    with pytest.raises(PermissionError, match="public_completion_requires_atomic_finalizer"):
        api.advance_stage(store, run_id=run["run_id"], stage_id="public_completion", writer_lease=run["writer_lease"], semantic_verifier=verifier)
    assert api.inspect_run(store, run_id=run["run_id"])["current_stage"] == "public_completion"


def test_v2_manifest_rebinding_is_append_only_and_preserves_stage_history(tmp_path: Path, monkeypatch) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    publish = importlib.import_module("tools.news_grasp_publish_contract")
    execution = importlib.import_module("tools.news_grasp_execution_receipt")
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    previous_id = "f" * 64
    manifest_id = "e" * 64
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id=previous_id)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=verifier)
    before = api.inspect_run(store, run_id=run["run_id"])
    bound_manifest = {"manifestId": manifest_id, "runId": run["run_id"], "runIntent": api.RUN_INTENT, "exactWriteSet": ["docs/index.html"]}
    monkeypatch.setattr(publish, "load_manifest", lambda *_args, **_kwargs: bound_manifest)
    monkeypatch.setattr(publish, "verify_manifest", lambda *_args, **_kwargs: {"ok": True})
    observation = {
        "schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1",
        "runId": run["run_id"],
        "issueDate": "2026-09-01",
        "runIntent": api.RUN_INTENT,
        "cwd": str(repo.resolve()),
        "dirty": False,
        "sourceHead": "a" * 40,
        "exactWriteSet": ["docs/index.html"],
        "manifestId": manifest_id,
        "runtimeState": {"root": str(store.state_root.resolve()), "dbExists": True},
    }
    monkeypatch.setattr(execution, "capture_observation", lambda **_kwargs: observation)
    monkeypatch.setattr(completion, "_up_to_date_observation", lambda *_args: {"ok": False, "head": "a" * 40, "remoteHead": "b" * 40})
    with pytest.raises(ValueError, match="consumer_owned_manifest_observation_red"):
        api.rebind_runtime_manifest(
            store,
            run_id=run["run_id"],
            previous_manifest_id=previous_id,
            manifest_id=manifest_id,
            repo_root=repo,
            writer_lease=run["writer_lease"],
        )
    rejected = api.inspect_run(store, run_id=run["run_id"])
    assert rejected["manifest_id"] == previous_id
    assert rejected["manifest_rebindings"] == []
    monkeypatch.setattr(completion, "_up_to_date_observation", lambda *_args: {"ok": True, "head": "a" * 40, "remoteHead": "a" * 40})
    rebound = api.rebind_runtime_manifest(
        store,
        run_id=run["run_id"],
        previous_manifest_id=previous_id,
        manifest_id=manifest_id,
        repo_root=repo,
        writer_lease=run["writer_lease"],
    )
    assert rebound["manifest_id"] == manifest_id
    assert rebound["stage_history"] == before["stage_history"]
    assert rebound["manifest_rebindings"][0]["previousManifestId"] == previous_id
    assert rebound["manifest_rebindings"][0]["manifestId"] == manifest_id
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runtime_manifest_rebindings WHERE run_id=?", (run["run_id"],)).fetchone()[0] == 1


def test_production_runtime_store_rejects_database_inode_replacement(tmp_path: Path, monkeypatch) -> None:
    """final public consumerはbind後に同bytes DBへ差し替えられてもfile identityで拒否する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    local_app_data = tmp_path / "localapp"
    canonical = local_app_data / "News-Grasp" / "direct-mainline"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    store = api.DirectRunStore(canonical)
    store.bind_production_runtime()
    replacement = canonical / "replacement.sqlite3"
    replacement.write_bytes(store.db_path.read_bytes())
    os.replace(replacement, store.db_path)
    with pytest.raises(PermissionError, match="production_runtime_db_identity_changed"):
        store.connect()


@pytest.mark.parametrize(
    ("remote", "branch", "wait_sec", "poll_sec"),
    [
        ("--upload-pack=evil", "main", 0, 30),
        ("origin", "../main", 0, 30),
        ("origin", "main", 901, 30),
        ("origin", "main", 0, 0),
    ],
)
def test_registered_consumer_rejects_git_and_timing_inputs_before_transport(
    tmp_path: Path, remote: str, branch: str, wait_sec: int, poll_sec: int
) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    with pytest.raises(ValueError):
        api._registered_stage_verifier(
            "youtube_podcasts",
            run={"run_id": "run", "issue_date": "2026-09-01", "run_intent": api.RUN_INTENT},
            evidence={},
            repo_root=tmp_path,
            public_base_url="https://hidepon-umg.github.io/News-Grasp/",
            remote=remote,
            branch=branch,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        )


def test_registered_summary_consumer_reads_frontmatter_source_not_rendered_html(
    tmp_path: Path, monkeypatch
) -> None:
    """Summary stageはfrontmatter正本を品質consumerへ渡す。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    root = tmp_path / "repo"
    source = root / "digest" / "Summary" / "2026-09-01.md"
    rendered = root / "docs" / "2026-09-01" / "summary" / "index.html"
    source.parent.mkdir(parents=True)
    rendered.parent.mkdir(parents=True)
    source.write_text(
        "---\n"
        "hero_headline: '日米が円買い協調介入、ドル円は一時155円台前半へ'\n"
        "---\n\n"
        "## § 本日のテーマ考察\n\n"
        "> [[為替]] の変化を **政策** と __市場__ から読む。\n",
        encoding="utf-8",
    )
    rendered.write_text("<html>generated public summary</html>\n", encoding="utf-8")

    daily_quality = importlib.import_module("tools.validate_daily_quality")
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    monkeypatch.setattr(completion, "resolve_trusted_repo_root", lambda _path: root)
    seen: list[Path] = []

    def _hero(path: Path) -> list[str]:
        seen.append(path)
        return []

    monkeypatch.setattr(daily_quality, "validate_summary_hero", _hero)
    monkeypatch.setattr(daily_quality, "validate_summary_emphasis", lambda _path: [])

    result = api._registered_stage_verifier(
        "summary",
        run={"run_id": "run", "issue_date": "2026-09-01", "run_intent": api.RUN_INTENT},
        evidence={},
        repo_root=root,
        public_base_url="https://hidepon-umg.github.io/News-Grasp/",
        remote="origin",
        branch="main",
        wait_sec=0,
        poll_sec=30,
    )

    assert result["ok"] is True, result
    assert seen == [source]


def test_pages_workflow_redirect_is_not_followed(tmp_path: Path, monkeypatch) -> None:
    del tmp_path
    import urllib.error

    completion = importlib.import_module("tools.news_grasp_direct_completion")
    calls: list[str] = []

    class RedirectingOpener:
        def open(self, request, *, timeout):
            del timeout
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": "http://127.0.0.1/"}, None)

    monkeypatch.setattr(completion.urllib.request, "build_opener", lambda *_args: RedirectingOpener())
    result = completion._pages_workflow_observation(remote_head="a" * 40, manifest_id="b" * 64, issue_date="2026-09-01")
    assert result["ok"] is False
    assert result["reasonCodes"] == ["pages_workflow_fetch_failed"]
    assert len(calls) == 1


def test_pages_workflow_body_is_bounded(monkeypatch) -> None:
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    expected_url = "https://api.github.com/repos/HIDEPON-UMG/News-Grasp/actions/workflows/deploy-pages.yml/runs?branch=main&per_page=20"

    class OversizeResponse:
        status = 200

        def getcode(self):
            return 200

        def geturl(self):
            return expected_url

        def read(self, limit):
            return b"x" * limit

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, request, *, timeout):
            del request, timeout
            return OversizeResponse()

    monkeypatch.setattr(completion.urllib.request, "build_opener", lambda *_args: Opener())
    result = completion._pages_workflow_observation(remote_head="a" * 40, manifest_id="b" * 64, issue_date="2026-09-01")
    assert result["ok"] is False
    assert "pages_workflow_response_too_large" in result["detail"]


def test_deepdive_completion_audits_current_issue_without_history_corpus(monkeypatch) -> None:
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    deepdive_quality = importlib.import_module("tools.deepdive_quality")
    captured: dict[str, object] = {}

    def audit_issue(**kwargs):
        captured.update(kwargs)
        return {"status": "Green", "issueCodes": [], "issues": []}

    monkeypatch.setattr(deepdive_quality, "audit_issue", audit_issue)
    result = completion._deepdive_quality(Path.cwd(), "2026-09-01")
    assert result["ok"] is True
    assert captured["include_corpus"] is False


def test_finalizer_persists_verified_optional_warning(tmp_path) -> None:
    """provider delivery ack非観測などの非必須warningをGreenと分離して残す。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")

    class WarningVerifier(_Verifier):
        def verify(self, stage_id: str, *, run: dict, caller_result: dict, observed_surface: dict) -> dict:
            row = super().verify(stage_id, run=run, caller_result=caller_result, observed_surface=observed_surface)
            if stage_id == "public_completion":
                row["post_publish_issue_list"] = [{
                    "surface": "notification",
                    "reasonCode": "notification_provider_delivery_ack_unavailable",
                    "status": "warning",
                }]
            return row

    verifier = WarningVerifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=verifier)
    final = api.finalize_public_completion(store, run_id=run["run_id"], writer_lease=run["writer_lease"], semantic_verifier=verifier, exact_successor="public_completion")
    assert any(item.get("reasonCode") == "notification_provider_delivery_ack_unavailable" for item in final["post_publish_issue_list"])


def test_v1_to_v2_migration_preserves_run_id_and_stage_history(tmp_path, monkeypatch) -> None:
    """V1 runを同じIDのままV2へ追記移行し、SQLite preimageを保存する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    api.advance_stage(store, run_id=run["run_id"], stage_id="title_control", writer_lease=run["writer_lease"], semantic_verifier=verifier)
    before = api.inspect_run(store, run_id=run["run_id"])
    publish = importlib.import_module("tools.news_grasp_publish_contract")
    bound_manifest = {"manifestId": "f" * 64, "runId": run["run_id"], "runIntent": api.RUN_INTENT, "exactWriteSet": ["docs/index.html"]}
    monkeypatch.setattr(publish, "load_manifest", lambda *_args, **_kwargs: bound_manifest)
    monkeypatch.setattr(publish, "verify_manifest", lambda *_args, **_kwargs: {"ok": True})
    observation = {"schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1", "runId": run["run_id"], "issueDate": "2026-09-01", "runIntent": api.RUN_INTENT, "cwd": str(repo.resolve()), "dirty": False, "sourceHead": "a" * 40, "exactWriteSet": ["docs/index.html"], "manifestId": "f" * 64, "runtimeState": {"root": str(store.state_root.resolve()), "dbExists": True}}
    migrated = api.migrate_run_v1_to_v2(
        store,
        run_id=run["run_id"],
        manifest_id="f" * 64,
        observation_receipt=observation,
        writer_lease=run["writer_lease"],
    )
    assert migrated["run_id"] == before["run_id"]
    assert migrated["stage_history"] == before["stage_history"]
    assert migrated["schemaVersion"] == "NEWS_GRASP_DIRECT_RUNTIME_V2"
    backup_path = Path(migrated["migration_receipt"]["backupPath"])
    assert backup_path.is_file()
    with sqlite3.connect(backup_path) as backup_db:
        assert backup_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup_db.execute("SELECT runtime_schema FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()[0] == api.RUNTIME_SCHEMA
        assert backup_db.execute("SELECT COUNT(*) FROM runtime_migrations").fetchone()[0] == 0
    repeated = api.migrate_run_v1_to_v2(
        store,
        run_id=run["run_id"],
        manifest_id="f" * 64,
        observation_receipt=observation,
        writer_lease=run["writer_lease"],
    )
    assert repeated["migration_receipt"] == migrated["migration_receipt"]


def test_v1_to_v2_migration_rebinds_only_target_copy_to_clean_observation_cwd(
    tmp_path, monkeypatch
) -> None:
    """relocated V1の旧cwdを保存しつつ、V2 targetだけclean worktreeへ束縛する。"""

    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(
        tmp_path / "state",
        semantic_verifier=_Verifier(),
        test_only_allow_semantic_verifier=True,
    )
    source_repo = tmp_path / "dirty-source"
    clean_repo = tmp_path / "clean-production"
    source_repo.mkdir()
    clean_repo.mkdir()
    run = api.start_run(store, cwd=source_repo, issue_date="2026-09-01")
    publish = importlib.import_module("tools.news_grasp_publish_contract")
    bound_manifest = {
        "manifestId": "f" * 64,
        "runId": run["run_id"],
        "runIntent": api.RUN_INTENT,
        "exactWriteSet": ["docs/index.html"],
    }
    loaded_from: list[Path] = []

    def load_manifest(repo_root, _issue_date):
        loaded_from.append(Path(repo_root).resolve())
        return bound_manifest

    monkeypatch.setattr(publish, "load_manifest", load_manifest)
    monkeypatch.setattr(publish, "verify_manifest", lambda *_args, **_kwargs: {"ok": True})
    observation = {
        "schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1",
        "runId": run["run_id"],
        "issueDate": "2026-09-01",
        "runIntent": api.RUN_INTENT,
        "cwd": str(clean_repo.resolve()),
        "dirty": False,
        "sourceHead": "a" * 40,
        "exactWriteSet": ["docs/index.html"],
        "manifestId": "f" * 64,
        "runtimeState": {"root": str(store.state_root.resolve()), "dbExists": True},
    }

    migrated = api.migrate_run_v1_to_v2(
        store,
        run_id=run["run_id"],
        manifest_id="f" * 64,
        observation_receipt=observation,
        writer_lease=run["writer_lease"],
    )

    assert loaded_from == [clean_repo.resolve()]
    assert Path(migrated["cwd"]) == clean_repo.resolve()
    assert migrated["migration_receipt"]["sourceCwd"] == str(source_repo.resolve())
    assert migrated["migration_receipt"]["targetCwd"] == str(clean_repo.resolve())
    backup = Path(migrated["migration_receipt"]["backupPath"])
    with sqlite3.connect(backup) as backup_db:
        assert Path(
            backup_db.execute(
                "SELECT cwd FROM runs WHERE run_id=?", (run["run_id"],)
            ).fetchone()[0]
        ) == source_repo.resolve()


def test_migration_rejects_cross_run_observation_and_invalid_manifest_id(tmp_path) -> None:
    """security Red: observationとmanifest identityを対象runへ完全束縛する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    with pytest.raises(ValueError):
        api.migrate_run_v1_to_v2(store, run_id=run["run_id"], manifest_id="not-a-digest", observation_receipt={"schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1"}, writer_lease=run["writer_lease"])


def test_inspect_does_not_disclose_writer_lease(tmp_path) -> None:
    """security Red: lease capabilityはstart callerへ一度だけ返しinspectから隠す。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    assert run["writer_lease"]
    assert "writer_lease" not in api.inspect_run(store, run_id=run["run_id"])


def test_finalizer_rolls_back_when_run_changes_after_fresh_probe(tmp_path) -> None:
    """security Red: fresh probe後のrow変更時にfailure clearやstage20を部分commitしない。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    base = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=base, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=base)

    class MutatingVerifier(_Verifier):
        calls = 0

        def verify(self, stage_id: str, **kwargs):
            result = super().verify(stage_id, **kwargs)
            if stage_id == "public_completion":
                self.calls += 1
                if self.calls == 2:
                    with store.connect() as conn:
                        conn.execute("UPDATE runs SET updated_at=? WHERE run_id=?", ("2099-01-01T00:00:00+09:00", run["run_id"]))
                        conn.commit()
            return result

    with pytest.raises(PermissionError, match="freshness"):
        api.finalize_public_completion(store, run_id=run["run_id"], writer_lease=run["writer_lease"], semantic_verifier=MutatingVerifier(), exact_successor="public_completion")
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stages WHERE run_id=? AND stage_id='public_completion'", (run["run_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT status FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()[0] != "completed"


def test_finalizer_fences_wrong_lease_before_any_public_probe(tmp_path) -> None:
    """writer authorityをpublic probeより前に検査して外部観測を開始しない。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")

    class CountingVerifier(_Verifier):
        calls = 0

        def verify(self, stage_id: str, **kwargs):
            self.calls += 1
            return super().verify(stage_id, **kwargs)

    verifier = CountingVerifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=verifier)
    calls_before = verifier.calls
    with pytest.raises(PermissionError, match="stale writer"):
        api.finalize_public_completion(store, run_id=run["run_id"], writer_lease="wrong", semantic_verifier=verifier, exact_successor="public_completion")
    assert verifier.calls == calls_before


def test_finalizer_rejects_noncontiguous_stage_history_even_when_count_matches(tmp_path) -> None:
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01", run_intent=api.RUN_INTENT, manifest_id="f" * 64)
    for stage_id in api.DIRECT_STAGES[:-1]:
        api.advance_stage(store, run_id=run["run_id"], stage_id=stage_id, writer_lease=run["writer_lease"], semantic_verifier=verifier)
    with store.connect() as conn:
        conn.execute("DELETE FROM stages WHERE run_id=? AND stage_index=19", (run["run_id"],))
        conn.execute("INSERT INTO stages(run_id,stage_index,stage_id,status,started_at,completed_at,evidence_json) VALUES(?,?,?,?,?,?,?)", (run["run_id"], 20, "public_completion", "green", "x", "x", "{}"))
        conn.commit()
    with pytest.raises(PermissionError, match="stage_history"):
        api.finalize_public_completion(store, run_id=run["run_id"], writer_lease=run["writer_lease"], semantic_verifier=verifier, exact_successor="public_completion")


def test_runtime_state_relocation_uses_sqlite_snapshot_and_preserves_source(tmp_path, monkeypatch) -> None:
    """worktree内V1 DBを変更せず外部state rootへ同一run snapshotする。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "dirty-worktree"
    source_root = source_repo / "build" / "direct-mainline"
    source = api.DirectRunStore(source_root, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(source, cwd=source_repo, issue_date="2026-09-01")
    with source.connect() as conn:
        conn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00+09:00' WHERE run_id=?", (run["run_id"],))
        conn.commit()
    before = source.db_path.read_bytes()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    target_root = local_app_data / "News-Grasp" / "direct-mainline"
    new_lease = "a" * 64
    receipt = api.relocate_runtime_state_v1(source_state_root=source_root, source_repo_root=source_repo, target_state_root=target_root, run_id=run["run_id"], writer_lease=run["writer_lease"], new_writer_lease=new_lease, recovery_authority="same_run_append_only_migration")
    assert receipt["ok"] is True
    assert source.db_path.read_bytes() == before
    with sqlite3.connect(target_root / "direct-mainline.sqlite3") as target:
        assert target.execute("SELECT run_id FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()[0] == run["run_id"]
        assert target.execute("SELECT writer_lease FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()[0] == new_lease


def test_runtime_relocation_rejects_fresh_source_lease_dual_writer(tmp_path, monkeypatch) -> None:
    """fresh source writerが有効な間はtarget tokenを発行せずdual writerを防ぐ。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "source-repo"
    source_root = source_repo / "build" / "direct-mainline"
    source = api.DirectRunStore(source_root, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(source, cwd=source_repo, issue_date="2026-09-01")
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    target_root = local_app_data / "News-Grasp" / "direct-mainline"
    with pytest.raises(PermissionError, match="source_lease_active"):
        api.relocate_runtime_state_v1(
            source_state_root=source_root,
            source_repo_root=source_repo,
            target_state_root=target_root,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            new_writer_lease="a" * 64,
            recovery_authority="same_run_append_only_migration",
        )
    assert not (target_root / "direct-mainline.sqlite3").exists()


def test_runtime_relocation_transient_failure_rolls_back_recovery_claim(tmp_path, monkeypatch) -> None:
    """backup前の一時失敗でclaimed orphanを残さず、同runを安全に再試行できる。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "source-repo"
    source_root = source_repo / "build" / "direct-mainline"
    source = api.DirectRunStore(source_root, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(source, cwd=source_repo, issue_date="2026-09-01")
    with source.connect() as conn:
        conn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00+09:00' WHERE run_id=?", (run["run_id"],))
        conn.commit()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    target_root = local_app_data / "News-Grasp" / "direct-mainline"
    original = api._reject_reparse_chain
    target_checks = {"count": 0}

    def fail_after_claim(path, *, reason):
        if Path(path) == target_root:
            target_checks["count"] += 1
            if target_checks["count"] == 4:
                raise RuntimeError("synthetic_transient_target_open_failure")
        return original(path, reason=reason)

    monkeypatch.setattr(api, "_reject_reparse_chain", fail_after_claim)
    with pytest.raises(RuntimeError, match="synthetic_transient"):
        api.relocate_runtime_state_v1(
            source_state_root=source_root,
            source_repo_root=source_repo,
            target_state_root=target_root,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            new_writer_lease="a" * 64,
            recovery_authority="same_run_append_only_migration",
        )
    monkeypatch.setattr(api, "_reject_reparse_chain", original)
    receipt = api.relocate_runtime_state_v1(
        source_state_root=source_root,
        source_repo_root=source_repo,
        target_state_root=target_root,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        new_writer_lease="a" * 64,
        recovery_authority="same_run_append_only_migration",
    )
    assert receipt["ok"] is True


def test_runtime_relocation_adopts_exact_orphan_after_post_commit_crash(tmp_path, monkeypatch) -> None:
    """target token commit直後のprocess crashをsame run/stage/token exact一致で回復する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "source-repo"
    source_root = source_repo / "build" / "direct-mainline"
    source = api.DirectRunStore(source_root, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(source, cwd=source_repo, issue_date="2026-09-01")
    with source.connect() as conn:
        conn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00+09:00' WHERE run_id=?", (run["run_id"],))
        conn.commit()
    local_app_data = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    target_root = local_app_data / "News-Grasp" / "direct-mainline"
    target_db = target_root / "direct-mainline.sqlite3"
    original_connect = api.sqlite3.connect
    target_connects = {"count": 0}

    class CrashAfterCommit:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self):
            self.inner.commit()
            raise KeyboardInterrupt("synthetic post-commit crash")

        def close(self):
            self.inner.close()

    def crash_connect(database, *args, **kwargs):
        inner = original_connect(database, *args, **kwargs)
        if Path(str(database)) == target_db:
            target_connects["count"] += 1
            if target_connects["count"] == 2:
                return CrashAfterCommit(inner)
        return inner

    monkeypatch.setattr(api.sqlite3, "connect", crash_connect)
    with pytest.raises(KeyboardInterrupt, match="post-commit"):
        api.relocate_runtime_state_v1(
            source_state_root=source_root,
            source_repo_root=source_repo,
            target_state_root=target_root,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            new_writer_lease="a" * 64,
            recovery_authority="same_run_append_only_migration",
        )
    monkeypatch.setattr(api.sqlite3, "connect", original_connect)
    receipt = api.relocate_runtime_state_v1(
        source_state_root=source_root,
        source_repo_root=source_repo,
        target_state_root=target_root,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        new_writer_lease="a" * 64,
        recovery_authority="same_run_append_only_migration",
    )
    assert receipt["ok"] is True
    assert receipt["recoveryStatus"] == "completed_adopted"


def test_runtime_relocation_rejects_canonical_parent_junction(tmp_path, monkeypatch) -> None:
    """canonical文字列内のjunctionから別dirへ外部writeを逃がさない。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    source_repo = tmp_path / "source-repo"
    source_root = source_repo / "build" / "direct-mainline"
    source = api.DirectRunStore(source_root, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(source, cwd=source_repo, issue_date="2026-09-01")
    local_app_data = tmp_path / "LocalAppData"
    local_app_data.mkdir()
    redirect = tmp_path / "redirect-target"
    redirect.mkdir()
    junction = local_app_data / "News-Grasp"
    if os.name == "nt":
        created = __import__("subprocess").run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(redirect)],
            capture_output=True,
            check=False,
            shell=False,
        )
        if created.returncode != 0:
            pytest.skip("directory junction creation is unavailable")
    else:
        junction.symlink_to(redirect, target_is_directory=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    try:
        with pytest.raises(ValueError, match="runtime_target_reparse_forbidden"):
            api.relocate_runtime_state_v1(
                source_state_root=source_root,
                source_repo_root=source_repo,
                target_state_root=junction / "direct-mainline",
                run_id=run["run_id"],
                writer_lease=run["writer_lease"],
                new_writer_lease="a" * 64,
                recovery_authority="same_run_append_only_migration",
            )
        assert not (redirect / "direct-mainline.sqlite3").exists()
    finally:
        os.rmdir(junction)


def test_migrate_cli_opens_legacy_store_without_pre_backup_schema_mutation(tmp_path, monkeypatch, capsys) -> None:
    """migrate-v2 CLIはcreate=Falseでlegacy DBを開き、関数内backup後だけschemaを変更する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    state = tmp_path / "state"
    repo = tmp_path / "repo"
    repo.mkdir()
    base = api.DirectRunStore(state, semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    run = api.start_run(base, cwd=repo, issue_date="2026-09-01")
    observation_path = tmp_path / "observation.json"
    publish = importlib.import_module("tools.news_grasp_publish_contract")
    bound_manifest = {"manifestId": "f" * 64, "runId": run["run_id"], "runIntent": api.RUN_INTENT, "exactWriteSet": ["docs/index.html"]}
    monkeypatch.setattr(publish, "load_manifest", lambda *_args, **_kwargs: bound_manifest)
    monkeypatch.setattr(publish, "verify_manifest", lambda *_args, **_kwargs: {"ok": True})
    observation_path.write_text(
        __import__("json").dumps({"schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1", "runId": run["run_id"], "issueDate": "2026-09-01", "runIntent": api.RUN_INTENT, "cwd": str(repo.resolve()), "dirty": False, "sourceHead": "a" * 40, "exactWriteSet": ["docs/index.html"], "manifestId": "f" * 64, "runtimeState": {"root": str(state.resolve()), "dbExists": True}}),
        encoding="utf-8",
    )
    seen: list[bool] = []
    original = api.DirectRunStore

    class SpyStore(original):
        def __init__(self, *args, **kwargs):
            seen.append(bool(kwargs.get("create", True)))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(api, "DirectRunStore", SpyStore)
    monkeypatch.setattr(sys, "argv", ["news_grasp_direct_runtime", "migrate-v2", "--state-root", str(state), "--run-id", run["run_id"], "--manifest-id", "f" * 64, "--observation-file", str(observation_path), "--writer-lease", run["writer_lease"]])
    assert api._main() == 0
    capsys.readouterr()
    assert seen == [False]


def test_migration_rejects_non_git_length_observation_source_head(tmp_path) -> None:
    """observation sourceHeadはSHA-1 commitのexact 40hex以外をauthorityにしない。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    observation = {
        "schemaVersion": "NEWS_GRASP_RUN_OBSERVATION_V1",
        "sourceHead": "a" * 41,
        "exactWriteSet": ["docs/index.html"],
    }
    with pytest.raises(ValueError, match="observation_source_head_invalid"):
        api.migrate_run_v1_to_v2(
            store,
            run_id=run["run_id"],
            manifest_id="f" * 64,
            observation_receipt=observation,
            writer_lease=run["writer_lease"],
        )


def test_start_run_has_database_single_writer_identity_gate(tmp_path) -> None:
    """SELECT→INSERT raceをpartial unique indexとBEGIN IMMEDIATEで閉じる。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=_Verifier(), test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    first = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    second = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    assert first["run_id"] == second["run_id"]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE status IN ('active','executing','finalizing')").fetchone()[0] == 1
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(runs)").fetchall()}
    assert "runs_active_identity_uq" in indexes


def test_title_completion_is_separate_from_publication_status(tmp_path) -> None:
    """titleのfulfilled/deferredをrun statusと別fieldに投影する。"""
    api = importlib.import_module("tools.news_grasp_direct_runtime")
    verifier = _Verifier()
    store = api.DirectRunStore(tmp_path / "state", semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = api.start_run(store, cwd=repo, issue_date="2026-09-01")
    advanced = api.advance_stage(store, run_id=run["run_id"], stage_id="title_control", writer_lease=run["writer_lease"], semantic_verifier=verifier)
    assert advanced["title_completion"] == "fulfilled"
    assert advanced["status"] != "completed"
