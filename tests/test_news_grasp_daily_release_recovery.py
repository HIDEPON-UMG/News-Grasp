"""release commit と publish seal の境界で停止したrunの回復回帰。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools import news_grasp_daily_release as release
from tools import news_grasp_direct_runtime as runtime


ISSUE_DATE = "2026-09-04"
RUN_ID = "daily-run-20260904-actual-002"


def _git_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    release._git(root, ["init", "-b", "main"])
    release._git(root, ["config", "core.autocrlf", "false"])
    release._git(root, ["config", "user.name", "News-Grasp fixture"])
    release._git(root, ["config", "user.email", "fixture@example.invalid"])
    index = root / "docs" / "index.html"
    index.parent.mkdir(parents=True)
    index.write_text("<html>baseline</html>\n", encoding="utf-8")
    release._git(root, ["add", "--", "docs/index.html"])
    release._git(root, ["commit", "-m", "fixture baseline"])
    baseline = release._git(root, ["rev-parse", "HEAD"]).strip().casefold()
    return root, baseline


def test_exact_commit_includes_declared_ignored_audit_only(tmp_path: Path) -> None:
    """local excludeに関わらず宣言済み成果だけをcommitする。"""
    root, baseline = _git_repo(tmp_path)
    (root / '.git/info/exclude').write_text('data/search_audit/\n', encoding='utf-8')
    audit = root / 'data/search_audit/current.json'
    audit.parent.mkdir(parents=True)
    audit.write_text('{}', encoding='utf-8')
    (audit.parent / 'unrelated.json').write_text('{"private":true}', encoding='utf-8')
    sha = release._create_exact_commit(root, paths=['data/search_audit/current.json'], expected_parent=baseline, issue_date=ISSUE_DATE, run_id=RUN_ID)
    assert release._committed_paths(root, sha) == {'data/search_audit/current.json'}
    assert (audit.parent / 'unrelated.json').is_file()


def test_commit_then_seal_boundary_reuses_same_release_commit_without_second_commit(
    tmp_path: Path,
) -> None:
    """commit済み・seal前の再開は同一SHAを返し、二重commitしない。"""

    root, baseline = _git_repo(tmp_path)
    (root / "docs" / "index.html").write_text("<html>release</html>\n", encoding="utf-8")

    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
    )
    resumed_sha = release._reuse_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
    )

    assert resumed_sha == release_sha
    assert release._git(root, ["rev-parse", "HEAD"]).strip().casefold() == release_sha
    assert int(release._git(root, ["rev-list", "--count", "HEAD"]).strip()) == 2
    assert release._status_paths(root) == set()


def test_commit_then_seal_boundary_recovers_real_index_drift_without_recommit(
    tmp_path: Path,
) -> None:
    """update-ref後・real index同期前をfixture化し、再開時にindexだけを復旧する。"""

    root, baseline = _git_repo(tmp_path)
    (root / "docs" / "index.html").write_text("<html>release</html>\n", encoding="utf-8")
    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
    )

    # update-refは成功したがreal indexのread-treeだけ失敗した状態を再現する。
    release._git(root, ["read-tree", f"{release_sha}^"])
    assert release._status_paths(root) == {"docs/index.html"}

    resumed_sha = release._reuse_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
    )

    assert resumed_sha == release_sha
    assert release._git(root, ["rev-parse", "HEAD"]).strip().casefold() == release_sha
    assert int(release._git(root, ["rev-list", "--count", "HEAD"]).strip()) == 2
    assert release._status_paths(root) == set()


def test_reuse_exact_commit_rejects_extra_path_outside_sealed_write_set(
    tmp_path: Path,
) -> None:
    """同一messageのcommitでもsealed write set外のpathを含めば再利用しない。"""

    root, baseline = _git_repo(tmp_path)
    (root / "docs" / "index.html").write_text("<html>release</html>\n", encoding="utf-8")
    extra = root / "docs" / "unexpected.txt"
    extra.write_text("must not be part of this bundle\n", encoding="utf-8")

    # crash前のcommitは、誤ってexact write set外のpathまで含むlegacy bundleを模擬する。
    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html", "docs/unexpected.txt"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
    )

    try:
        release._reuse_exact_commit(
            root,
            paths=["docs/index.html"],
            expected_parent=baseline,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
        )
    except release.DailyReleaseError as exc:
        assert "WRITE_SET" in str(exc).upper()
    else:
        raise AssertionError(f"legacy commit with extra path was reused: {release_sha}")

    assert release._git(root, ["rev-parse", "HEAD"]).strip().casefold() == release_sha
    assert int(release._git(root, ["rev-list", "--count", "HEAD"]).strip()) == 2


def _current_issue_recovery_fixture(
    tmp_path: Path,
) -> tuple[Path, str, object, runtime.DirectRunStore, dict[str, object], str, str]:
    """current_issue claimを持つexpired runとclean git release candidateを作る。"""

    root, baseline = _git_repo(tmp_path)

    class Clock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-04T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = Clock()
    store = runtime.DirectRunStore(
        tmp_path / "runtime-state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    manifest_id = "a" * 64
    run = runtime.start_run(
        store,
        cwd=root,
        issue_date=ISSUE_DATE,
        run_intent="scheduled_production_direct",
        source_baseline=baseline,
        remote_base_sha=baseline,
        scheduler_trigger_at="2026-09-04T06:00:00+09:00",
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    for index, operation_id in enumerate(runtime.DAILY_OPERATION_ORDER[:2]):
        input_hash = f"fixture-input-{operation_id}"
        handler_id = f"fixture.handler.{operation_id}"
        runtime.claim_daily_operation(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
        )
        runtime.apply_daily_operation_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            fencing_token=run["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt={
                "schemaVersion": f"FIXTURE_{index}_V1",
                "ok": True,
                "status": "verified",
            },
        )
    current_input_hash = "fixture-input-current_issue_integration"
    current_handler_id = "fixture.handler.current_issue_integration"
    runtime.claim_daily_operation(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        operation_id="current_issue_integration",
        input_hash=current_input_hash,
        handler_id=current_handler_id,
    )
    # Current integrationがrelease candidateを生成した直後のworking tree。
    (root / "docs" / "index.html").write_text("<html>current issue release</html>\n", encoding="utf-8")
    return (
        root,
        baseline,
        clock,
        store,
        run,
        manifest_id,
        current_input_hash,
    )


def _seal_fixture(
    store: runtime.DirectRunStore,
    run: dict[str, object],
    *,
    manifest_id: str,
    release_sha: str,
) -> dict[str, object]:
    return runtime.seal_publish(
        store,
        run_id=str(run["run_id"]),
        writer_lease=str(run["writer_lease"]),
        fencing_token=int(run["fencing_token"]),
        release_commit_sha=release_sha,
        exact_write_set=["docs/index.html"],
        file_hashes={"docs/index.html": "c" * 64},
        manifest_id=manifest_id,
        bundle_id="fixture-bundle",
        external_operation_ids=["external_publication"],
    )


def _apply_current_issue_receipt(
    store: runtime.DirectRunStore,
    run: dict[str, object],
    *,
    input_hash: str,
    release_sha: str,
) -> dict[str, object]:
    handler_id = "fixture.handler.current_issue_integration"
    claim = runtime.claim_daily_operation(
        store,
        run_id=str(run["run_id"]),
        writer_lease=str(run["writer_lease"]),
        fencing_token=int(run["fencing_token"]),
        operation_id="current_issue_integration",
        input_hash=input_hash,
        handler_id=handler_id,
    )
    assert claim["status"] == "claimed"
    return runtime.apply_daily_operation_atomic(
        store,
        run_id=str(run["run_id"]),
        writer_lease=str(run["writer_lease"]),
        fencing_token=int(run["fencing_token"]),
        operation_id="current_issue_integration",
        input_hash=input_hash,
        handler_id=handler_id,
        producer_receipt={
            "schemaVersion": "NEWS_GRASP_CURRENT_ISSUE_INTEGRATION_RECEIPT_V1",
            "ok": True,
            "status": "verified",
            "releaseCommitSha": release_sha,
        },
    )


def test_current_issue_commit_update_ref_crash_reuses_same_run_and_exact_commit_before_seal(
    tmp_path: Path,
) -> None:
    """update-ref直後の停止は新generation化せず、同一commitをsealへ継続する。"""

    root, baseline, clock, store, first, manifest_id, input_hash = _current_issue_recovery_fixture(tmp_path)
    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=str(first["run_id"]),
    )
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=root,
        issue_date=ISSUE_DATE,
        run_intent="scheduled_production_direct",
        source_baseline=baseline,
        remote_base_sha=baseline,
        scheduler_trigger_at="2026-09-04T06:00:00+09:00",
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )

    assert recovered["status"] == "active"
    assert recovered["run_id"] == first["run_id"]
    assert recovered["generation"] == first["generation"]
    assert recovered["fencing_token"] == first["fencing_token"] + 1
    assert recovered["writer_lease"] != first["writer_lease"]
    reused_sha = release._reuse_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=str(first["run_id"]),
    )
    assert reused_sha == release_sha
    assert int(release._git(root, ["rev-list", "--count", "HEAD"]).strip()) == 2
    sealed = _seal_fixture(store, recovered, manifest_id=manifest_id, release_sha=release_sha)
    assert sealed["releaseCommitSha"] == release_sha
    applied = _apply_current_issue_receipt(
        store,
        recovered,
        input_hash=input_hash,
        release_sha=release_sha,
    )
    assert applied["status"] == "completed"


def test_current_issue_publish_seal_crash_reuses_seal_and_applies_receipt_without_recommit(
    tmp_path: Path,
) -> None:
    """publish seal後・Daily receipt前の停止はsealを再利用しcommitを増やさない。"""

    root, baseline, clock, store, first, manifest_id, input_hash = _current_issue_recovery_fixture(tmp_path)
    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=str(first["run_id"]),
    )
    before_seal = _seal_fixture(store, first, manifest_id=manifest_id, release_sha=release_sha)
    commit_count = int(release._git(root, ["rev-list", "--count", "HEAD"]).strip())
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=root,
        issue_date=ISSUE_DATE,
        run_intent="scheduled_production_direct",
        source_baseline=baseline,
        remote_base_sha=baseline,
        scheduler_trigger_at="2026-09-04T06:00:00+09:00",
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )

    assert recovered["status"] == "active"
    assert recovered["run_id"] == first["run_id"]
    assert recovered["generation"] == first["generation"]
    assert recovered["fencing_token"] == first["fencing_token"] + 1
    reused_seal = _seal_fixture(store, recovered, manifest_id=manifest_id, release_sha=release_sha)
    assert reused_seal == before_seal
    assert int(release._git(root, ["rev-list", "--count", "HEAD"]).strip()) == commit_count
    applied = _apply_current_issue_receipt(
        store,
        recovered,
        input_hash=input_hash,
        release_sha=release_sha,
    )
    assert applied["status"] == "completed"


def test_current_issue_publish_seal_takeover_rejects_changed_release(
    tmp_path: Path,
) -> None:
    """引継ぎは旧sealを再利用できても、公開内容の差替えは許可しない。"""

    root, baseline, clock, store, first, manifest_id, _ = _current_issue_recovery_fixture(tmp_path)
    release_sha = release._create_exact_commit(
        root,
        paths=["docs/index.html"],
        expected_parent=baseline,
        issue_date=ISSUE_DATE,
        run_id=str(first["run_id"]),
    )
    _seal_fixture(store, first, manifest_id=manifest_id, release_sha=release_sha)
    clock.value += timedelta(minutes=11)
    recovered = runtime.start_run(
        store,
        cwd=root,
        issue_date=ISSUE_DATE,
        run_intent="scheduled_production_direct",
        source_baseline=baseline,
        remote_base_sha=baseline,
        scheduler_trigger_at="2026-09-04T06:00:00+09:00",
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )

    with pytest.raises(RuntimeError, match="publish_seal_idempotency_conflict"):
        _seal_fixture(store, recovered, manifest_id=manifest_id, release_sha="d" * 40)
