from __future__ import annotations

import importlib
import inspect
import json
import re
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "news_grasp_daily_45m_contract_v1.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "news_grasp_daily_45m_legacy_cases.json"
ISSUE_DATE = "2026-09-03"
RUN_INTENT = "scheduled_production_direct"

# NG-A01〜NG-A11 の normal / adversarial / recovery を同一モジュールへ公開する。
# fixtureIds は immutable な legacy input、testNodeId は config の trace node である。
NG45_ACCEPTANCE_TRACE_MATRIX: dict[str, dict[str, Any]] = {
    "NG-A01": {
        "primary": {"testNodeId": "NG-A01-N", "fixtureIds": ["F01"]},
        "adversarial": {"testNodeId": "NG-A01-A", "fixtureIds": ["F02"]},
        "recovery": {"testNodeId": "NG-A01-R", "fixtureIds": ["F01", "F02"]},
    },
    "NG-A02": {
        "primary": {"testNodeId": "NG-A02-N", "fixtureIds": ["F10"]},
        "adversarial": {"testNodeId": "NG-A02-A", "fixtureIds": ["F09"]},
        "recovery": {"testNodeId": "NG-A02-R", "fixtureIds": ["F10"]},
    },
    "NG-A03": {
        "primary": {"testNodeId": "NG-A03-N", "fixtureIds": ["F03"]},
        "adversarial": {"testNodeId": "NG-A03-A", "fixtureIds": ["F16"]},
        "recovery": {"testNodeId": "NG-A03-R", "fixtureIds": ["F03", "F16"]},
    },
    "NG-A04": {
        "primary": {"testNodeId": "NG-A04-N", "fixtureIds": ["F11"]},
        "adversarial": {"testNodeId": "NG-A04-A", "fixtureIds": ["F11"]},
        "recovery": {"testNodeId": "NG-A04-R", "fixtureIds": ["F11"]},
    },
    "NG-A05": {
        "primary": {"testNodeId": "NG-A05-N", "fixtureIds": ["F04", "F05"]},
        "adversarial": {"testNodeId": "NG-A05-A", "fixtureIds": ["F06"]},
        "recovery": {"testNodeId": "NG-A05-R", "fixtureIds": ["F15"]},
    },
    "NG-A06": {
        "primary": {"testNodeId": "NG-A06-N", "fixtureIds": ["F07"]},
        "adversarial": {"testNodeId": "NG-A06-A", "fixtureIds": ["F13"]},
        "recovery": {"testNodeId": "NG-A06-R", "fixtureIds": ["F07", "F13"]},
    },
    "NG-A07": {
        "primary": {"testNodeId": "NG-A07-N", "fixtureIds": ["F08"]},
        "adversarial": {"testNodeId": "NG-A07-A", "fixtureIds": ["F09"]},
        "recovery": {"testNodeId": "NG-A07-R", "fixtureIds": ["F14"]},
    },
    "NG-A08": {
        "primary": {"testNodeId": "NG-A08-N", "fixtureIds": ["F17"]},
        "adversarial": {"testNodeId": "NG-A08-A", "fixtureIds": ["F18"]},
        "recovery": {"testNodeId": "NG-A08-R", "fixtureIds": ["F17", "F18"]},
    },
    "NG-A09": {
        "primary": {"testNodeId": "NG-A09-N", "fixtureIds": ["F12"]},
        "adversarial": {"testNodeId": "NG-A09-A", "fixtureIds": ["F12"]},
        "recovery": {"testNodeId": "NG-A09-R", "fixtureIds": ["F12"]},
    },
    "NG-A10": {
        "primary": {"testNodeId": "NG-A10-N", "fixtureIds": ["F01"]},
        "adversarial": {"testNodeId": "NG-A10-A", "fixtureIds": ["F01"]},
        "recovery": {"testNodeId": "NG-A10-R", "fixtureIds": ["F01"]},
    },
    "NG-A11": {
        "primary": {"testNodeId": "NG-A11-N", "fixtureIds": ["F12"]},
        "adversarial": {"testNodeId": "NG-A11-A", "fixtureIds": ["F12"]},
        "recovery": {"testNodeId": "NG-A11-R", "fixtureIds": ["F12"]},
    },
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pytest.fail(f"missing-capability:fixture-file:{path}", pytrace=False)
    except (UnicodeError, json.JSONDecodeError) as exc:
        pytest.fail(f"missing-capability:fixture-json:{path}:{exc}", pytrace=False)


def _contract() -> dict[str, Any]:
    value = _load_json(CONFIG_PATH)
    if not isinstance(value, dict) or value.get("schemaVersion") != "NEWS_GRASP_DAILY_45M_CONTRACT_V1":
        pytest.fail("missing-capability:daily-45m-contract-config", pytrace=False)
    return value


def _fixture(fixture_id: str) -> dict[str, Any]:
    value = _load_json(FIXTURE_PATH)
    cases = value.get("cases") if isinstance(value, dict) else None
    row = next((item for item in cases or [] if isinstance(item, dict) and item.get("fixtureId") == fixture_id), None)
    if row is None:
        pytest.fail(f"missing-capability:legacy-fixture:{fixture_id}", pytrace=False)
    return row


def _module(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - baseline missing capability is an explicit Red.
        pytest.fail(f"missing-capability:{module_name}:import:{type(exc).__name__}:{exc}", pytrace=False)


def _require_callable(module_name: str, name: str) -> Callable[..., Any]:
    module = _module(module_name)
    value = getattr(module, name, None)
    if not callable(value):
        pytest.fail(f"missing-capability:{module_name}.{name}", pytrace=False)
    return value


def _manifest(tmp_path: Path, *, source_baseline: str = "a" * 40) -> dict[str, Any]:
    api = _module("tools.news_grasp_publish_contract")
    return api.build_publish_manifest(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id="direct-2026-09-03-fresh",
        run_intent=RUN_INTENT,
        source_baseline=source_baseline,
    )


def _semantic_pages(manifest: Mapping[str, Any]) -> dict[str, str]:
    marker = str(manifest["manifestId"])
    daily_url = str(manifest["audio"]["daily"]["publicUrl"])
    public_base = "https://hidepon-umg.github.io/News-Grasp/"
    meta = f'<meta name="news-grasp-manifest-id" content="{marker}">'
    summary_href = f"{public_base}{ISSUE_DATE}/summary/"
    deepdive_href = f"{public_base}deepdive/{ISSUE_DATE}/"
    category_rows = [row for row in manifest["entries"] if row.get("artifactKind") == "category_html"]
    category_links = "".join(f'<a href="{row["publicUrl"]}">category</a>' for row in category_rows)
    pages: dict[str, str] = {
        "home": f'{meta}<source src="{daily_url}"><a href="{deepdive_href}">DeepDive</a><a href="{summary_href}">Summary</a>',
        "daily": f"{meta}<main>{ISSUE_DATE}{category_links}</main>",
        "summary": f'{meta}<main>{ISSUE_DATE}<p class="summary-hero__lead">検証済み材料を分離し、次の観測点へつなぐ本日の編集上の振り返りです。</p></main>',
        "deepdive": f"{meta}<main>{ISSUE_DATE}</main>",
        "publish_status": json.dumps({"date": ISSUE_DATE, "manifestId": marker, "result": "success"}),
    }
    for category_id in manifest["scheduledCategoryIds"]:
        pages[f"category:{category_id}"] = f"{meta}<main>{ISSUE_DATE} category</main>"
    return pages


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def test_release_materializer_rebinds_old_publish_status_to_current_issue_before_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release materializerは旧publish-statusをcurrent issueへ束縛してcommit候補にする。"""

    release = _module("tools.news_grasp_daily_release")
    runtime = _module("tools.news_grasp_direct_runtime")
    repo = tmp_path / "repo"
    repo.mkdir()
    status_path = repo / "docs" / "publish-status.json"
    _write_json(status_path, {"date": "2026-09-03", "result": "published_ok"})
    start_seal = {
        "sourceBaseline": "a" * 40,
        "remoteBaseSha": "b" * 40,
        "schedulerTriggerAt": "2026-09-04T06:00:00+09:00",
    }
    monkeypatch.setattr(
        runtime,
        "inspect_run",
        lambda _store, run_id: {"run_id": run_id, "start_seal": start_seal},
    )

    def fake_git(_root: Path, args: list[str], **_kwargs: Any) -> str:
        if args == ["rev-parse", "origin/main"]:
            return "b" * 40 + "\n"
        if args == ["rev-parse", "HEAD"]:
            return "a" * 40 + "\n"
        if args == ["symbolic-ref", "--short", "HEAD"]:
            return "main\n"
        raise AssertionError(f"unexpected git probe: {args}")

    monkeypatch.setattr(release, "_git", fake_git)

    def stop_after_status(**_kwargs: Any) -> None:
        raise release.DailyReleaseError("fixture_stop_after_status")

    monkeypatch.setattr(release, "build_publish_manifest", stop_after_status)
    with pytest.raises(release.DailyReleaseError, match="fixture_stop_after_status"):
        release.materialize_and_seal_release(
            store=object(),
            repo_root=repo,
            issue_date=ISSUE_DATE,
            run_id="actual-run-20260904",
            run_intent=RUN_INTENT,
            writer_lease="writer-fixture",
            fencing_token=1,
            content_receipt={"ok": True, "run_id": "actual-run-20260904"},
        )

    current = _load_json(status_path)
    assert current["date"] == ISSUE_DATE
    assert current["result"] == "publication_pending"
    assert current["status"] == "awaiting_external_completion_attestation"
    assert current["completionAuthority"] == "consumer-owned_public_verifier"
    assert current["runId"] == "actual-run-20260904"
    assert current["runIntent"] == RUN_INTENT
    assert current["updated_at"] == start_seal["schedulerTriggerAt"]


def test_release_materializer_rejects_non_main_before_publish_status_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main以外ではpublish-statusを一byteも書き換えずに止める。"""

    release = _module("tools.news_grasp_daily_release")
    runtime = _module("tools.news_grasp_direct_runtime")
    repo = tmp_path / "repo"
    repo.mkdir()
    status_path = repo / "docs" / "publish-status.json"
    _write_json(status_path, {"date": "2026-09-03", "result": "published_ok"})
    before = status_path.read_bytes()
    start_seal = {
        "sourceBaseline": "a" * 40,
        "remoteBaseSha": "b" * 40,
        "schedulerTriggerAt": "2026-09-04T06:00:00+09:00",
    }
    monkeypatch.setattr(
        runtime,
        "inspect_run",
        lambda _store, run_id: {"run_id": run_id, "start_seal": start_seal},
    )

    def fake_git(_root: Path, args: list[str], **_kwargs: Any) -> str:
        if args == ["rev-parse", "origin/main"]:
            return "b" * 40 + "\n"
        if args == ["symbolic-ref", "--short", "HEAD"]:
            return "feature\n"
        raise AssertionError(f"unexpected git probe: {args}")

    monkeypatch.setattr(release, "_git", fake_git)
    monkeypatch.setattr(
        release,
        "_write_publish_status",
        lambda **_kwargs: pytest.fail("publish-status must not be mutated on non-main"),
    )

    with pytest.raises(release.DailyReleaseError, match="RELEASE_BRANCH_NOT_MAIN"):
        release.materialize_and_seal_release(
            store=object(),
            repo_root=repo,
            issue_date=ISSUE_DATE,
            run_id="actual-run-20260904",
            run_intent=RUN_INTENT,
            writer_lease="writer-fixture",
            fencing_token=1,
            content_receipt={"ok": True, "run_id": "actual-run-20260904"},
        )

    assert status_path.read_bytes() == before


def test_f01_daily_route_rejects_release_and_raw_process_before_spawn() -> None:
    gate = _module("tools.news_grasp_gate_profiles")
    authorize = _require_callable("tools.news_grasp_gate_profiles", "authorize_daily_operation")
    error_type = getattr(gate, "NewsGraspGateProfileError", RuntimeError)
    for attempt in _fixture("F01")["attempts"]:
        with pytest.raises(error_type) as exc_info:
            authorize(attempt["operationId"], attempt["command"])
        assert _fixture("F01")["attempts"]
        assert attempt["reasonCode"] in str(exc_info.value) or "release" in str(exc_info.value).casefold()


def test_f02_daily_route_rejects_historical_unknown_and_raw_python() -> None:
    gate = _module("tools.news_grasp_gate_profiles")
    authorize = _require_callable("tools.news_grasp_gate_profiles", "authorize_daily_operation")
    error_type = getattr(gate, "NewsGraspGateProfileError", RuntimeError)
    for attempt in _fixture("F02")["attempts"]:
        with pytest.raises(error_type):
            authorize(attempt["operationId"], attempt["command"])


def test_f03_summary_uses_markdown_and_deepdive_is_current_issue_only(tmp_path: Path) -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F03")
    manifest = _manifest(tmp_path)
    summary_source = next(row for row in manifest["entries"] if row.get("artifactKind") == "summary_source")
    assert str(summary_source["localPath"]).endswith(".md")
    pages = _semantic_pages(manifest)
    pages["deepdive"] = pages["deepdive"].replace(ISSUE_DATE, fixture["deepdiveIssueDate"])
    result = api.verify_semantic_pages(manifest, pages)
    assert result["ok"] is False
    assert fixture["reasonCode"] in result["reasonCodes"]


def test_f04_distribution_rebinding_retains_optional_primary_playlist_fields(tmp_path: Path) -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F04")
    repo = tmp_path / "repo"
    repo.mkdir()
    for relative in (
        f"data/distribution/{ISSUE_DATE}.json",
        "build/tts/daily/latest_audio.json",
        "build/tts/deepdive/latest_audio.json",
        f"build/notification/{ISSUE_DATE}.json",
    ):
        _write_json(repo / relative, {"fixture": relative})
    daily_source = tmp_path / "daily-upload.json"
    deepdive_source = tmp_path / "deepdive-upload.json"
    _write_json(daily_source, {ISSUE_DATE: fixture["dailyReceipt"]})
    _write_json(deepdive_source, {ISSUE_DATE: fixture["deepdiveReceipt"]})
    lease_store = api.PublishLeaseStore(
        tmp_path / "lease-state",
        test_only_allow_noncanonical=True,
        test_only_skip_runtime_binding=True,
    )
    result = api.bind_existing_distribution_receipts(
        repo_root=repo,
        issue_date=ISSUE_DATE,
        run_id="run-f04",
        run_intent=RUN_INTENT,
        daily_upload_state=daily_source,
        deepdive_upload_state=deepdive_source,
        lease_store=lease_store,
        writer_lease="lease-f04",
    )
    assert result["ok"] is True
    playlist = json.loads((repo / f"build/distribution/{ISSUE_DATE}/playlist.json").read_text(encoding="utf-8"))
    assert playlist["deepdive"]["primaryPodcastPlaylistId"] == fixture["deepdiveReceipt"]["primaryPodcastPlaylistId"]
    assert playlist["deepdive"]["primaryPodcastPlaylistItemId"] == fixture["deepdiveReceipt"]["primaryPodcastPlaylistItemId"]


def test_f05_audio_identity_ignores_only_v_cache_query(tmp_path: Path) -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F05")
    manifest = _manifest(tmp_path)
    pages = _semantic_pages(manifest)
    daily_url = manifest["audio"]["daily"]["publicUrl"]
    separator = "&" if "?" in daily_url else "?"
    legacy_url = f"{daily_url}{separator}{fixture['nonCacheQuery']}&{fixture['cacheQuery']}"
    pages["home"] = pages["home"].replace(daily_url, legacy_url)
    result = api.verify_semantic_pages(manifest, pages)
    assert result["ok"] is False
    assert fixture["reasonCode"] in result["reasonCodes"]


def test_f06_manifest_source_baseline_must_be_real_ancestor() -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F06")
    manifest = api.build_publish_manifest(
        repo_root=ROOT,
        issue_date=ISSUE_DATE,
        run_id="direct-2026-09-03-ancestor",
        run_intent=RUN_INTENT,
        source_baseline=fixture["sourceBaseline"],
    )
    result = api.verify_manifest(manifest, repo_root=ROOT)
    assert result["ok"] is False
    assert fixture["reasonCode"] in result["reasonCodes"]


def test_f07_start_run_single_flight_includes_run_intent(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    fixture = _fixture("F07")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runtime.start_run(
        store,
        automation_id=fixture["automationId"],
        cwd=repo,
        issue_date=fixture["issueDate"],
        run_intent=fixture["runIntents"][0],
    )
    second = runtime.start_run(
        store,
        automation_id=fixture["automationId"],
        cwd=repo,
        issue_date=fixture["issueDate"],
        run_intent=fixture["runIntents"][1],
    )
    assert second["status"] in {"attached", "blocked"}
    if second["status"] == "attached":
        assert second["run_id"] == first["run_id"]


def test_f08_invalid_child_json_is_observed_before_any_state_mutation(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    parse_child_result = _require_callable("tools.news_grasp_direct_runtime", "parse_child_result")
    fixture = _fixture("F08")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    before = store.db_path.read_bytes()
    result = parse_child_result(
        fixture["raw"],
        expected_schema=fixture["expectedSchema"],
        expected_input_hash=fixture["expectedInputHash"],
    )
    assert result["ok"] is False
    assert store.db_path.read_bytes() == before


def test_f09_schema_input_identity_rejects_atomic_apply_without_projection_drift(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    apply_stage_result_atomic = _require_callable("tools.news_grasp_direct_runtime", "apply_stage_result_atomic")
    fixture = _fixture("F09")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    before = store.db_path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        apply_stage_result_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            stage_id=fixture["childResult"]["stageId"],
            child_result=fixture["childResult"],
            expected_input_hash=fixture["expectedInputHash"],
        )
    assert store.db_path.read_bytes() == before


def test_f10_completion_elapsed_freezes_and_dispatches_45_75_90_boundaries(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    freeze_completion_elapsed = _require_callable("tools.news_grasp_direct_runtime", "freeze_completion_elapsed")
    slo_dispatch = _require_callable("tools.news_grasp_direct_runtime", "slo_dispatch")
    fixture = _fixture("F10")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    frozen = freeze_completion_elapsed(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        elapsed_seconds=fixture["elapsedSeconds"],
    )
    repeated = freeze_completion_elapsed(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        elapsed_seconds=fixture["laterElapsedSeconds"],
    )
    assert frozen["elapsed_seconds"] == fixture["elapsedSeconds"]
    assert repeated["elapsed_seconds"] == fixture["elapsedSeconds"]
    for branch in fixture["branches"]:
        result = slo_dispatch(elapsed_seconds=branch["elapsedSeconds"])
        assert result["time_band"] == branch["expectedBand"]


def test_post_90_admission_blocks_regeneration_but_keeps_same_run_recovery(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
    )
    clock.value += timedelta(minutes=91)

    admission = runtime.admit_daily_operation(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        operation_id="consumer_public_verification",
    )

    assert admission["dispatch"] == "deadline_revision"
    assert admission["model_regeneration_allowed"] is False
    assert admission["high_cost_generation_allowed"] is False
    assert admission["provider_initial_send_allowed"] is True
    assert admission["provider_resend_allowed"] is False
    assert admission["read_only_reconcile_allowed"] is True
    assert admission["same_run_resume_allowed"] is True
    assert admission["deterministic_successor_allowed"] is True
    assert admission["finalization_allowed"] is True


@pytest.mark.parametrize(
    ("elapsed_seconds", "expected_attempts"),
    [(3600, 1), (5500, 2)],
)
def test_consumer_public_verifier_poll_continues_after_slo_debt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    elapsed_seconds: int,
    expected_attempts: int,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    completion = _module("tools.news_grasp_direct_completion")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
    )
    observed: dict[str, Any] = {}
    attempts: list[int] = []
    monotonic_values = iter((0.0, 601.0, 602.0, 603.0))

    def verify(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        attempts.append(1)
        if elapsed_seconds > 5400 and len(attempts) == 1:
            return {
                "ok": False,
                "status": "blocked",
                "failures": ["public_surface_red:web"],
                "public_surfaces": {
                    "web": {
                        "status": "blocked",
                        "retryDisposition": "transient",
                        "public": {"status": "red", "failures": ["public_digest_mismatch:home"]},
                    },
                    "pages": {
                        "status": "blocked",
                        "retryDisposition": "transient",
                        "workflow": {
                            "status": "pending",
                            "pendingWorkflowRun": {"head_sha": "a" * 40, "status": "in_progress"},
                        },
                    },
                },
                "observationToken": "fresh-observation-red",
                "observedAt": "2026-09-05T07:00:00+09:00",
            }
        return {
            "ok": True,
            "status": "verified",
            "observationToken": "fresh-observation",
            "observedAt": "2026-09-05T07:00:00+09:00",
        }

    monkeypatch.setattr(completion, "verify_direct_public_completion", verify)
    monkeypatch.setattr(gate.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(monotonic_values))
    result = gate._default_consumer_public_verification(
        store=store,
        repo_root=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        public_base_url="https://example.test/News-Grasp/",
        slo_dispatch={"elapsed_seconds": elapsed_seconds},
    )

    assert result["observation"]["ok"] is True, result
    assert observed["wait_sec"] == 0
    assert observed["poll_sec"] == 30
    assert result["observation_attempt_count"] == expected_attempts


def test_consumer_public_retry_classifier_rejects_terminal_provider_failure() -> None:
    gate = _module("tools.news_grasp_daily_gate")

    assert gate._public_observation_retryable(
        {
            "failures": ["public_surface_red:youtube_daily"],
            "public_surfaces": {
                "youtube_daily": {
                    "status": "blocked",
                    "retryDisposition": "terminal",
                    "provider": {"status": "failed", "reason": "upload_rejected"},
                }
            },
        }
    ) is False


def test_consumer_public_poll_continues_same_run_at_slo_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    completion = _module("tools.news_grasp_direct_completion")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
    )
    clock.value = datetime.fromisoformat("2026-09-03T07:30:00+09:00")
    attempts: list[int] = []

    def verify(**_kwargs: Any) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            return {
                "ok": False,
                "status": "blocked",
                "failures": ["public_surface_red:web"],
                "public_surfaces": {
                    "pages": {
                        "retryDisposition": "transient",
                        "workflow": {"status": "pending"},
                    },
                },
                "observationToken": "fresh-observation-red",
                "observedAt": "2026-09-03T07:30:00+09:00",
            }
        return {
            "ok": True,
            "status": "verified",
            "observationToken": "fresh-observation-green",
            "observedAt": "2026-09-03T07:35:00+09:00",
        }

    sleeps: list[float] = []
    monkeypatch.setattr(completion, "verify_direct_public_completion", verify)
    monkeypatch.setattr(gate.time, "sleep", sleeps.append)

    result = gate._default_consumer_public_verification(
        store=store,
        repo_root=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        public_base_url="https://example.test/News-Grasp/",
        slo_dispatch={"elapsed_seconds": 5_400},
    )

    assert result["observation"]["ok"] is True, result
    assert result["observation_attempt_count"] == 2
    assert sleeps == [pytest.approx(300.0)]
    with store.connect() as conn:
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT checkpoint_minute FROM runtime_checkpoints WHERE run_id=? ORDER BY checkpoint_minute",
                (run["run_id"],),
            ).fetchall()
        ] == [(45,), (75,), (90,)]


@pytest.mark.parametrize(
    "producer_failures,expected_failures",
    [
        (["public_surface_red:web"], ["public_surface_red:web"]),
        (["x" * 3000] * 20, ["x" * 2048] * 16),
        ([{"private_context": "not projected"}, "typed_failure"], ["typed_failure"]),
        ("malformed_failures", []),
    ],
)
def test_public_verification_red_keeps_same_run_successor_recoverable(
    tmp_path: Path,
    producer_failures: object,
    expected_failures: list[str],
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
    )
    prior = list(gate.DAILY_OPERATIONS[:4])
    for index, operation_id in enumerate(prior):
        input_hash = f"input-{index}"
        handler_id = f"fixture.{operation_id}"
        runtime.claim_daily_operation(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            fencing_token=run["fencing_token"],
        )
        runtime.apply_daily_operation_atomic(
            store,
            run_id=run["run_id"],
            writer_lease=run["writer_lease"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt=gate._producer_result(
                f"FIXTURE_{operation_id}",
                ok=True,
                status="verified",
                operation_id=operation_id,
            ),
            fencing_token=run["fencing_token"],
        )

    result = gate.run_daily_operation(
        "consumer_public_verification",
        completed_operations=prior,
        store=store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        handlers={
            "consumer_public_verification": lambda **_context: gate._producer_result(
                "NEWS_GRASP_CONSUMER_PUBLIC_VERIFICATION_RECEIPT_V1",
                ok=False,
                status="red",
                operation_id="consumer_public_verification",
                failures=(),
            ) | {"failures": producer_failures}
        },
    )

    assert result["ok"] is False
    assert result["producer_failures"] == expected_failures
    assert runtime.inspect_run(store, run_id=run["run_id"])["status"] in {
        "active",
        "executing",
    }
    with store.connect() as conn:
        assert tuple(
            conn.execute(
                "SELECT status FROM daily_operation_claims WHERE run_id=? AND operation_id='consumer_public_verification'",
                (run["run_id"],),
            ).fetchone()
        ) == ("recoverable",)


def test_pages_deployment_surfaces_same_head_in_progress_as_pending() -> None:
    evaluate = _require_callable(
        "tools.news_grasp_publish_contract",
        "evaluate_pages_deployment",
    )
    head = "a" * 40

    result = evaluate(
        remote_head=head,
        workflow_runs=[
            {
                "head_sha": head,
                "path": ".github/workflows/deploy-pages.yml",
                "event": "push",
                "head_branch": "main",
                "status": "in_progress",
                "conclusion": None,
            }
        ],
        manifest_id="b" * 64,
        issue_date=ISSUE_DATE,
    )

    assert result["ok"] is False
    assert result["status"] == "pending"
    assert result["pendingWorkflowRun"]["head_sha"] == head


def test_public_surface_retry_disposition_is_closed_for_transport_and_semantic_red() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    classify = completion._surface_retry_disposition

    assert classify(
        "pages",
        {"ok": False, "semantic_ok": False, "reasonCodes": ["pages_workflow_fetch_failed"]},
        {},
    ) == "transient"
    assert classify(
        "remote_commit",
        {"ok": False, "semantic_ok": False, "status": "red", "remote_exit_code": 128},
        {},
    ) == "transient"
    assert classify(
        "daily_audio",
        {"ok": False, "semantic_ok": False, "reasonCodes": ["audio_public_probe_failed"]},
        {},
    ) == "transient"
    assert classify(
        "daily_audio",
        {"ok": False, "semantic_ok": False, "reasonCodes": ["audio_projection_invalid"]},
        {},
    ) == "terminal"


def test_web_propagation_and_pages_api_failure_remain_same_call_retryable() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    gate = _module("tools.news_grasp_daily_gate")
    surfaces: dict[str, dict[str, Any]] = {
        "web": {
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "public": {"failures": ["public_digest_mismatch:home"]},
        },
        "pages": {
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "workflow": {
                "ok": False,
                "semantic_ok": False,
                "status": "blocked",
                "reasonCodes": ["pages_workflow_fetch_failed"],
            },
        },
    }
    for name in ("web", "pages"):
        surfaces[name]["retryDisposition"] = completion._surface_retry_disposition(
            name,
            surfaces[name],
            surfaces,
        )

    assert {row["retryDisposition"] for row in surfaces.values()} == {"transient"}
    assert gate._public_observation_retryable(
        {
            "failures": ["public_surface_red:web", "public_surface_red:pages"],
            "public_surfaces": surfaces,
        }
    ) is True


def test_pages_completed_failure_stops_web_propagation_poll() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    gate = _module("tools.news_grasp_daily_gate")
    evaluate = _require_callable(
        "tools.news_grasp_publish_contract",
        "evaluate_pages_deployment",
    )
    head = "a" * 40
    workflow = evaluate(
        remote_head=head,
        workflow_runs=[
            {
                "head_sha": head,
                "path": ".github/workflows/deploy-pages.yml",
                "event": "push",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
            }
        ],
        manifest_id="b" * 64,
        issue_date=ISSUE_DATE,
    )
    surfaces: dict[str, dict[str, Any]] = {
        "web": {
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "public": {"failures": ["public_digest_mismatch:home"]},
        },
        "pages": {
            "ok": False,
            "semantic_ok": False,
            "status": "blocked",
            "workflow": workflow,
        },
    }
    for name in ("web", "pages"):
        surfaces[name]["retryDisposition"] = completion._surface_retry_disposition(
            name,
            surfaces[name],
            surfaces,
        )

    assert surfaces["web"]["retryDisposition"] == "terminal"
    assert surfaces["pages"]["retryDisposition"] == "terminal"
    assert workflow["failedWorkflowRun"]["conclusion"] == "failure"
    assert gate._public_observation_retryable(
        {
            "failures": ["public_surface_red:web", "public_surface_red:pages"],
            "public_surfaces": surfaces,
        }
    ) is False


def test_pages_pending_rerun_supersedes_failed_history_for_retry() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    evaluate = _require_callable(
        "tools.news_grasp_publish_contract",
        "evaluate_pages_deployment",
    )
    head = "a" * 40
    workflow = evaluate(
        remote_head=head,
        workflow_runs=[
            {
                "head_sha": head,
                "path": ".github/workflows/deploy-pages.yml",
                "event": "push",
                "head_branch": "main",
                "status": "in_progress",
                "conclusion": None,
            },
            {
                "head_sha": head,
                "path": ".github/workflows/deploy-pages.yml",
                "event": "push",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "failure",
            },
        ],
        manifest_id="b" * 64,
        issue_date=ISSUE_DATE,
    )
    surface = {
        "ok": False,
        "semantic_ok": False,
        "status": "blocked",
        "workflow": workflow,
    }

    assert workflow["status"] == "pending"
    assert workflow["failedWorkflowRun"]["conclusion"] == "failure"
    assert completion._surface_retry_disposition(
        "pages",
        surface,
        {"pages": surface},
    ) == "transient"


def test_pages_pending_cannot_hide_immutable_release_contract_red() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    evaluate = _require_callable(
        "tools.news_grasp_publish_contract",
        "evaluate_pages_deployment",
    )
    head = "a" * 40
    workflow = evaluate(
        remote_head=head,
        workflow_runs=[
            {
                "head_sha": head,
                "path": ".github/workflows/deploy-pages.yml",
                "event": "push",
                "head_branch": "main",
                "status": "in_progress",
                "conclusion": None,
            }
        ],
        manifest_id="b" * 64,
        issue_date=ISSUE_DATE,
        changed_paths=["docs/category/ai.html"],
    )
    surface = {
        "ok": False,
        "semantic_ok": False,
        "status": "blocked",
        "workflow": workflow,
    }

    assert "pages_public_release_docs_diff_missing" in workflow["reasonCodes"]
    assert completion._surface_retry_disposition(
        "pages",
        surface,
        {"pages": surface},
    ) == "terminal"


def test_consumer_public_verifier_recovers_distinct_transport_reds_in_same_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    completion = _module("tools.news_grasp_direct_completion")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
    )
    observations = iter(
        [
            {
                "ok": False,
                "status": "blocked",
                "failures": [f"public_surface_red:{surface}"],
                "public_surfaces": {
                    surface: {"ok": False, "retryDisposition": "transient"}
                },
                "observationToken": f"red-{surface}",
                "observedAt": "2026-09-05T07:00:00+09:00",
            }
            for surface in ("pages", "remote_commit", "daily_audio")
        ]
        + [
            {
                "ok": True,
                "status": "verified",
                "observationToken": "green",
                "observedAt": "2026-09-05T07:00:00+09:00",
            }
        ]
    )
    monkeypatch.setattr(completion, "verify_direct_public_completion", lambda **_kwargs: next(observations))
    monkeypatch.setattr(gate.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(gate.time, "monotonic", lambda: 0.0)

    result = gate._default_consumer_public_verification(
        store=store,
        repo_root=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        public_base_url="https://example.test/News-Grasp/",
        slo_dispatch={"elapsed_seconds": 0},
    )

    assert result["observation"]["ok"] is True
    assert result["observation_attempt_count"] == 4


def test_current_issue_handler_executes_persisted_repair_plan_in_same_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    content = _module("tools.news_grasp_daily_content")
    inventory = _module("tools.publish_inventory")
    repair = _module("tools.news_grasp_repair_registry")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
    )
    calls: list[int] = []

    def produce(**kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            ledger = runtime.DailyArtifactLedger(
                kwargs["runtime_store"],
                run_id=kwargs["run_id"],
                issue_date=kwargs["issue_date"],
                writer_lease=kwargs["writer_lease"],
                fencing_token=kwargs["fencing_token"],
            )
            ledger.record_failure(
                {
                    "stage": "reporter",
                    "artifactId": "reporter:fx",
                    "predicateId": "reporter_output_valid",
                    "reasonCode": "fixture_quality_red",
                    "inputHash": "fixture-input",
                    "causeInputMask": ["/records/0/summary"],
                }
            )
            checkpoints = ledger.list_checkpoints()
            ledger.persist_repair_plan(
                repair.build_repair_plan(
                    issue_date=kwargs["issue_date"],
                    run_id=kwargs["run_id"],
                    categories=("fx",),
                    checkpoints=checkpoints,
                    failures=[checkpoints["reporter:fx"]["failure"]],
                )
            )
            raise content.DailyContentError("fixture_quality_red")
        return {"ok": True, "status": "completed", "run_id": kwargs["run_id"]}

    monkeypatch.setattr(content, "produce_current_issue", produce)
    monkeypatch.setattr(inventory, "scheduled_category_ids", lambda _issue: ("fx",))
    monkeypatch.setattr(gate, "_CURRENT_ISSUE_PRODUCER_GROUPS", ())
    result = gate._default_current_issue_integration(
        store=store,
        repo_root=repo,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        run=run,
        run_intent=RUN_INTENT,
        route_capability={"capability": "scheduled_production_daily"},
        content_model_runner=lambda **_: None,
    )

    assert result["ok"] is True
    assert calls == [1, 2]


def test_post_materialization_quality_red_repairs_and_rechecks_in_same_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools import news_grasp_daily_gate as gate
    from tools import news_grasp_direct_runtime as runtime

    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date="2026-09-05",
        run_intent=runtime.RUN_INTENT,
        manifest_id="f" * 64,
    )
    produced: list[int] = []
    checked: list[int] = []

    def produce(**_kwargs):
        produced.append(1)
        return {"ok": True, "status": "completed"}

    def quality(_context):
        checked.append(1)
        return {
            "predicate_id": "summary_markdown_quality",
            "failures": ["summary quality red"] if len(checked) == 1 else [],
        }

    monkeypatch.setattr("tools.news_grasp_daily_content.produce_current_issue", produce)
    monkeypatch.setattr(gate, "_CURRENT_ISSUE_PRODUCER_GROUPS", (("summary", quality),))
    monkeypatch.setattr(
        gate,
        "_record_current_issue_quality_failures",
        lambda *_args, **_kwargs: {
            "status": "repair_required",
            "planSha256": f"{len(checked):064x}",
            "steps": [{"action": "repair_model"}],
        },
    )

    result = gate._default_current_issue_integration(
        store=store,
        cwd=repo,
        issue_date="2026-09-05",
        run_id=run["run_id"],
        run=runtime.inspect_run(store, run_id=run["run_id"]),
        run_intent=runtime.RUN_INTENT,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
        route_capability={"capability": "scheduled_production_daily"},
        content_model_runner=lambda **_kwargs: {},
    )

    assert result["ok"] is True, result
    assert len(produced) == 2
    assert len(checked) == 2


def test_post_quality_summary_failure_records_only_summary_field(tmp_path: Path) -> None:
    from tools import news_grasp_daily_gate as gate
    from tools import news_grasp_direct_runtime as runtime

    repo = tmp_path / "repo"
    repo.mkdir()
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        manifest_id="f" * 64,
    )
    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    ledger.write_checkpoint(
        artifact_id="editor",
        input_hash="editor-input",
        validator_id="editor_output_valid_v1",
        payload={
            "issue_date": ISSUE_DATE,
            "append_records": [{"url": "https://example.test/keep"}],
            "summary_markdown": "old summary",
        },
    )

    plan = gate._record_current_issue_quality_failures(
        ledger,
        categories=("fx",),
        results=[
            {
                "predicate_id": "summary_markdown_quality",
                "failures": ["reflection section §01 lacks required emphasis"],
            }
        ],
    )

    failure = ledger.load_failure("editor")
    assert failure is not None
    assert failure["allowedMutationPaths"] == ["/summary_markdown"]
    actions = {item["artifactId"]: item["action"] for item in plan["steps"]}
    assert actions["editor"] == "repair_model"


def test_quality_failure_resolver_covers_relation_and_followup_exact_field() -> None:
    from tools import news_grasp_daily_gate as gate

    relation = gate._quality_failure_target(
        predicate_id="deepdive_current_issue_audit",
        failure="deepdive_relation_quality_invalid",
        categories=("fx",),
        checkpoints={},
    )
    followup = gate._quality_failure_target(
        predicate_id="jsonl_source_freshness",
        failure=(
            "articles.jsonl:1 [fx]: follow-up matched_with URL date 2026-09-01 "
            "is old; matched_with=https://example.test/2026/09/01/story; "
            "add followup_review_note"
        ),
        categories=("fx",),
        checkpoints={
            "reporter:fx": {
                "payload": {
                    "records": [
                        {"matched_with": "https://example.test/2026/09/01/story"}
                    ]
                }
            }
        },
    )
    missing_jsonl = gate._quality_failure_target(
        predicate_id="jsonl_source_freshness",
        failure="articles JSONL が存在しません",
        categories=("fx",),
        checkpoints={},
    )

    assert relation == ("deepdive_model", ["/article_markdown"])
    assert followup == ("reporter:fx", ["/records/0/followup_review_note"])
    assert missing_jsonl == ("articles_jsonl", [])


def test_sequence_continues_distinct_external_reconcile_progress_in_same_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    external_calls: list[int] = []
    progress_calls: list[int] = []

    def operation(operation_id: str, **_kwargs: Any) -> dict[str, Any]:
        if operation_id != "external_publication":
            return {"ok": True, "status": "completed", "operation_id": operation_id}
        external_calls.append(1)
        if len(external_calls) < 3:
            return {
                "ok": False,
                "status": "reconcile_required",
                "operation_id": operation_id,
                "exact_successor": f"external_reconcile:step-{len(external_calls)}",
                "slo_dispatch": {"read_only_reconcile_allowed": True},
            }
        return {"ok": True, "status": "completed", "operation_id": operation_id}

    def outbox(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        progress_calls.append(1)
        return [
            {
                "logical_operation_id": "fixture",
                "status": f"state-{len(progress_calls)}",
            }
        ]

    monkeypatch.setattr(gate, "run_daily_operation", operation)
    monkeypatch.setattr(runtime, "inspect_external_outbox", outbox)

    receipts = gate.run_daily_sequence(
        store=store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at=f"{ISSUE_DATE}T06:00:00+09:00",
    )

    assert [item["operation_id"] for item in receipts] == list(gate.DAILY_OPERATIONS)
    assert len(external_calls) == 3


def test_external_and_consumer_red_persist_repair_plan_in_same_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _module("tools.news_grasp_daily_gate")
    external = _module("tools.news_grasp_daily_external")
    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
    )
    monkeypatch.setattr(
        external,
        "execute_external_publication",
        lambda **_kwargs: {
            "ok": False,
            "status": "reconcile_required",
            "operation_id": "notification",
            "exact_successor": "notification",
            "failures": ["provider_ack_unknown"],
            "adapter_call_count": 0,
        },
    )
    context = {
        "store": store,
        "repo_root": repo,
        "issue_date": ISSUE_DATE,
        "run_intent": RUN_INTENT,
        "run_id": run["run_id"],
        "writer_lease": run["writer_lease"],
        "fencing_token": run["fencing_token"],
        "slo_dispatch": {"provider_resend_allowed": False},
    }

    external_result = gate._default_external_publication(**context)
    consumer_result = gate._default_consumer_public_verification(
        **context,
        public_base_url="",
    )
    ledger = runtime.DailyArtifactLedger(
        store,
        run_id=run["run_id"],
        issue_date=ISSUE_DATE,
        writer_lease=run["writer_lease"],
        fencing_token=run["fencing_token"],
    )
    checkpoints = ledger.list_checkpoints()

    assert external_result["ok"] is False
    assert consumer_result["ok"] is False
    assert checkpoints["notification"]["status"] == "Red"
    assert checkpoints["public_verification"]["status"] == "Red"
    assert ledger.load_repair_plan()["status"] == "repair_required"


def test_f11_home_is_required_and_source_only_release_does_not_require_pages(tmp_path: Path) -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F11")
    manifest = _manifest(tmp_path)
    without_home = {**manifest, "entries": [row for row in manifest["entries"] if row.get("artifactKind") != "public_home"]}
    assert "manifest_home_missing" in api.verify_manifest(without_home, repo_root=tmp_path)["reasonCodes"]
    evaluate_pages_deployment = _require_callable("tools.news_grasp_publish_contract", "evaluate_pages_deployment")
    valid_source_only = {
        "remote_head": "a" * 40,
        "workflow_runs": [],
        "manifest_id": "b" * 64,
        "issue_date": ISSUE_DATE,
        "release_kind": "source_only",
        "changed_paths": fixture["sourceOnlyChangedPaths"],
    }
    try:
        result = evaluate_pages_deployment(**valid_source_only)
    except TypeError as exc:
        pytest.fail(f"missing-capability:source-only-pages-boundary:{exc}", pytrace=False)
    assert result["ok"] is True


def test_f12_automation_integrity_bundle_rejects_prompt_replacement_and_auto_repair() -> None:
    validate_integrity_bundle = _require_callable("tools.sync_news_grasp_codex_automation", "validate_integrity_bundle")
    fixture = _fixture("F12")
    result = validate_integrity_bundle(fixture["bundle"])
    assert result["ok"] is False
    assert fixture["reasonCode"] in result.get("reasonCodes", [])


def test_f13_start_run_requires_scheduler_trigger_inheritance(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    fixture = _fixture("F13")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        run = runtime.start_run(
            store,
            cwd=repo,
            issue_date=ISSUE_DATE,
            run_intent=RUN_INTENT,
            scheduler_trigger_at=fixture["schedulerTriggerAt"],
        )
    except TypeError as exc:
        pytest.fail(f"missing-capability:scheduler_trigger_at:{exc}", pytrace=False)
    assert run["scheduler_trigger_at"] == fixture["schedulerTriggerAt"]


def test_f14_atomic_receipt_lookup_is_the_retry_authority(tmp_path: Path) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    apply_stage_result_atomic = _require_callable("tools.news_grasp_direct_runtime", "apply_stage_result_atomic")
    get_applied_receipt = _require_callable("tools.news_grasp_direct_runtime", "get_applied_receipt")
    fixture = _fixture("F14")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(store, cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT)
    receipt = apply_stage_result_atomic(
        store,
        run_id=run["run_id"],
        writer_lease=run["writer_lease"],
        stage_id=fixture["stageId"],
        child_result={
            "schemaVersion": "NEWS_GRASP_CHILD_RESULT_V1",
            "inputHash": fixture["inputHash"],
            "status": "verified",
        },
        expected_input_hash=fixture["inputHash"],
        operation_id=fixture["operationId"],
    )
    looked_up = get_applied_receipt(
        store,
        run_id=run["run_id"],
        operation_id=fixture["operationId"],
    )
    assert looked_up == receipt


def test_f15_post_external_drift_becomes_superseded_observation() -> None:
    api = _module("tools.news_grasp_publish_contract")
    fixture = _fixture("F15")
    result = api.evaluate_checkout_observation(fixture["observation"])
    assert result["ok"] is False
    assert fixture["reasonCode"] in result["reasonCodes"]


def test_f16_predicate_owner_mismatch_and_duplicate_claim_are_rejected() -> None:
    gate = _module("tools.news_grasp_gate_profiles")
    claim_predicate_once = _require_callable("tools.news_grasp_gate_profiles", "claim_predicate_once")
    fixture = _fixture("F16")
    store: dict[str, Any] = {}
    claim_kwargs = {
        "store": store,
        "generation_id": fixture["generationId"],
        "predicate_id": fixture["predicateId"],
        "owner": fixture["owner"],
        "source_identity": fixture["sourceIdentity"],
        "evidence": fixture["evidence"],
    }
    claim_predicate_once(**claim_kwargs)
    error_type = getattr(gate, "NewsGraspGateProfileError", RuntimeError)
    with pytest.raises(error_type):
        claim_predicate_once(**claim_kwargs)
    with pytest.raises(error_type):
        claim_predicate_once(**{**claim_kwargs, "owner": fixture["otherOwner"]})


def test_f17_direct_completion_requires_fresh_observation_and_side_effect_identity() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    verifier = _require_callable("tools.news_grasp_direct_completion", "verify_direct_public_completion")
    fixture = _fixture("F17")
    parameters = inspect.signature(verifier).parameters
    required = {"observation_token", "observed_at", "external_operation_id"}
    assert required <= set(parameters)
    assert fixture["observationToken"]
    assert fixture["observedAt"]
    assert fixture["externalOperationId"]
    # caller が module の別名や保存済みJSONを completion authority に差し替えない。
    assert completion.__doc__ and "caller" in completion.__doc__


def test_consumer_public_verifier_does_not_reaudit_current_issue_quality_predicates() -> None:
    """consumer verifierはcurrent_issue_integrationの品質predicateを再実行しない。"""

    completion = _module("tools.news_grasp_direct_completion")
    source = inspect.getsource(completion.verify_direct_public_completion)

    # DeepDive本文品質とDaily品質はcurrent_issue_integration receiptのownerであり、
    # consumer側はsealed manifestとfresh public network semanticだけを照合する。
    assert "_deepdive_quality(" not in source
    assert "_daily_quality(" not in source
    assert "load_manifest" in source
    assert "manifest=manifest" in source
    assert "_public_web(" in source
    assert '"predicateOwner": "current_issue_integration"' in source


def test_f18_arbitrary_caller_green_json_is_not_completion_authority(tmp_path: Path) -> None:
    completion = _module("tools.news_grasp_direct_completion")
    fixture = _fixture("F18")
    notification = tmp_path / "build" / "notification" / f"{ISSUE_DATE}.json"
    _write_json(notification, fixture["callerResult"])
    result = completion._notification(tmp_path, ISSUE_DATE, run_id="run-f18", run_intent=RUN_INTENT)
    assert result["ok"] is False
    assert result["status"] in {"red", "blocked"}


def test_ng_rrt_public_observation_marks_only_actual_network_fetch_as_fresh() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    context = completion._new_observation_context(
        issue_date=ISSUE_DATE,
        run_id="direct-observation-owner",
        run_intent=RUN_INTENT,
    )
    completion._bind_observation_context(
        context,
        issue_date=ISSUE_DATE,
        run_id="direct-observation-owner",
        run_intent=RUN_INTENT,
        manifest_id="a" * 64,
    )
    local = completion._observation_metadata(
        context,
        content={"surface": "notification"},
        observation_kind="local_canonical_read",
        source_identity="immutable_sender_ledger",
        source_path=f"build/notification/{ISSUE_DATE}.json",
    )
    network = completion._observation_metadata(
        context,
        request_started_at=context["startedAt"],
        response_observed_at=datetime.now(timezone.utc).isoformat(),
        body=b"public bytes",
        observation_kind="network_fetch",
        source_identity="https://example.invalid/public",
        source_path="https://example.invalid/public",
        status_code=200,
    )
    assert local["freshNetwork"] is False
    assert network["freshNetwork"] is True
    assert local["nonce"] == network["nonce"] == context["nonce"]


def test_ng_rg_31_surface_observation_accepts_explicit_public_identity() -> None:
    """consumer public verifierの明示source identityを共通観測へ保持する。"""

    completion = _module("tools.news_grasp_direct_completion")
    context = completion._new_observation_context(
        issue_date=ISSUE_DATE,
        run_id="direct-public-observation",
        run_intent=RUN_INTENT,
    )
    completion._bind_observation_context(
        context,
        issue_date=ISSUE_DATE,
        run_id="direct-public-observation",
        run_intent=RUN_INTENT,
        manifest_id="b" * 64,
    )
    result = completion._attach_surface_observation(
        "playlist",
        {"ok": True, "status": "green"},
        context,
        request_started_at=context["startedAt"],
        response_observed_at=datetime.now(timezone.utc).isoformat(),
        observation_kind="network_fetch",
        source_identity="youtube:playlist:playlist-identity",
        source_path="youtube:playlist:playlist-identity",
    )

    assert result["observation"]["sourceIdentity"] == "youtube:playlist:playlist-identity"
    assert result["observation"]["sourcePath"] == "youtube:playlist:playlist-identity"
    assert result["observation"]["freshNetwork"] is True


def test_ng_rrt_duplicate_side_effect_uses_sealed_ledger_identity_not_key_name() -> None:
    completion = _module("tools.news_grasp_direct_completion")
    one_upload = {
        "ok": True,
        "duplicateUploadCount": 99,
        "immutableLedger": [
            {
                "operationId": "yt-upload-1",
                "payloadSha256": "a" * 64,
                "status": "uploaded",
                "ledgerBound": True,
            }
        ],
    }
    clean = completion._validate_side_effect_identity(
        {
            "youtube_daily": one_upload,
            "youtube_deepdive": {"ok": False},
            "notification": {"ok": False},
        }
    )
    assert "duplicate_upload_detected" not in clean["failures"]

    duplicate = completion._validate_side_effect_identity(
        {
            "youtube_daily": {
                "ok": True,
                "immutableLedger": [
                    {
                        "operationId": "yt-upload-1",
                        "payloadSha256": "a" * 64,
                        "status": "uploaded",
                        "ledgerBound": True,
                    },
                    {
                        "operationId": "yt-upload-2",
                        "payloadSha256": "b" * 64,
                        "status": "uploaded",
                        "ledgerBound": True,
                    },
                ],
            },
            "youtube_deepdive": {"ok": False},
            "notification": {"ok": False},
        }
    )
    assert "duplicate_upload_detected" in duplicate["failures"]


def test_ng_rrt_consumer_rejects_arbitrary_side_effect_identity_not_bound_to_outbox() -> None:
    """caller自己申告のoperationId/payloadIdentityをoutbox未束縛のままGreenにしない。"""

    completion = _module("tools.news_grasp_direct_completion")
    verifier = _require_callable(
        "tools.news_grasp_direct_completion",
        "_validate_side_effect_identity",
    )
    surfaces = {
        "youtube_daily": {
            "ok": True,
            "immutableLedger": [
                {
                    "operationId": "attacker-operation-id",
                    "payloadSha256": "d" * 64,
                    "status": "uploaded",
                    # provider/local payloadの自己申告だけではoutbox bindingにならない。
                    "ledgerBound": True,
                }
            ],
        },
        "youtube_deepdive": {"ok": False},
        "notification": {"ok": False},
    }
    expected_outbox = {
        "youtube_daily": [
            {
                "operationId": "sealed-youtube-daily-operation",
                "payloadIdentity": "e" * 64,
            }
        ]
    }

    # expected_outboxはconsumerがruntime external_outboxから取得した束縛projection。
    # 現在の未修正consumerがこの引数を持たない場合も、設計欠落としてRedにする。
    result = verifier(surfaces, expected_outbox=expected_outbox)

    assert result["ok"] is False
    assert any("unbound" in str(reason).casefold() for reason in result["failures"])


def test_ng_rrt_automation_template_has_exact_prompt_parity_contract() -> None:
    sync = _module("tools.sync_news_grasp_codex_automation")
    template_path = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"
    toml_body = template_path.read_text(encoding="utf-8")
    prompt = str(tomllib.loads(toml_body)["prompt"])
    required_phrase = "完全な品質で記事公開するまで完了してはならない"
    result = sync.validate_integrity_bundle(
        {
            "templatePrompt": prompt,
            "installedPrompt": prompt,
            "appDbPrompt": prompt,
            "snapshotPrompts": [prompt],
            "requiredPhrase": required_phrase,
            "tomlBody": toml_body,
            "startAutoRepair": False,
        }
    )
    assert result["ok"] is True
    assert prompt.count(required_phrase) == 3
    launcher = "tools.news_grasp_direct_runtime daily"
    assert prompt.count(launcher) == 1
    assert "news_grasp_daily.run_daily" not in prompt
    for operation in (
        "static_check",
        "scoped_contract_unit",
        "current_issue_integration",
        "external_publication",
        "consumer_public_verification",
        "atomic_completion",
    ):
        assert operation in prompt
        assert f"tools.news_grasp_daily_gate {operation}" not in prompt


def test_ng_rrt_writer_heartbeat_survives_ttl_and_second_start_is_attach_only(
    tmp_path: Path,
) -> None:
    """10分TTLを跨ぐ長時間operationでもheartbeatがwriterを保持する。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        manifest_id="a" * 64,
    )

    # 初回leaseの期限直前にownerだけがheartbeatを打つ。これによりwall-clockは
    # 10分を超えても、別startは新writerを作らずread-only attachになる。
    clock.value += timedelta(minutes=9, seconds=59)
    heartbeat = runtime.renew_daily_writer_lease(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
    )
    assert heartbeat["status"] == "renewed"
    clock.value += timedelta(seconds=2)
    assert clock.value > datetime.fromisoformat("2026-09-03T06:10:00+09:00")

    second = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        manifest_id="a" * 64,
    )

    assert second["status"] == "attached"
    assert second["single_flight"] == "attached"
    assert second["attached_to_run_id"] == first["run_id"]
    assert "writer_lease" not in second
    assert "fencing_token" not in second
    with store.connect() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE issue_date=? AND run_intent=? "
            "AND status IN ('active','executing','finalizing')",
            (ISSUE_DATE, RUN_INTENT),
        ).fetchone()[0]
    assert active == 1


def test_ng_rrt_expired_pre_external_owner_resumes_same_run_from_next_operation(
    tmp_path: Path,
) -> None:
    """開始後driftは新runを作らず、保存済みreceiptの次へ復帰する。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    allowed = ["external_publication"]
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="a" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=allowed,
    )
    runtime.claim_daily_operation(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="static_check",
        input_hash="input-static-check",
        handler_id="fixture.handler.static_check",
    )
    runtime.apply_daily_operation_atomic(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="static_check",
        input_hash="input-static-check",
        handler_id="fixture.handler.static_check",
        producer_receipt={"schemaVersion": "FIXTURE_STATIC_V1", "ok": True, "status": "verified"},
    )
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="b" * 40,
        remote_base_sha="c" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=allowed,
    )

    assert recovered["status"] == "active"
    assert recovered["single_flight"] == "recovered_after_expired_owner"
    assert recovered["continuation_recovery"] is True
    assert recovered["run_id"] == first["run_id"]
    assert recovered["generation"] == first["generation"]
    assert recovered["writer_lease"] != first["writer_lease"]
    assert recovered["fencing_token"] > first["fencing_token"]
    assert recovered["exact_successor"] == "scoped_contract_unit"
    with pytest.raises(PermissionError, match="fenc|lease"):
        runtime.renew_daily_writer_lease(
            store,
            run_id=first["run_id"],
            writer_lease=first["writer_lease"],
            fencing_token=first["fencing_token"],
        )
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runs WHERE issue_date=? AND run_intent=?",
            (ISSUE_DATE, RUN_INTENT),
        ).fetchone()[0] == 1


def test_ng_rrt_expired_claim_resumes_same_run_without_repeating_green_receipts(
    tmp_path: Path,
) -> None:
    """失効時の未完了claimだけをrecoverable化し、Green receiptを保持する。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="a" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    runtime.claim_daily_operation(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="static_check",
        input_hash="input-static-check",
        handler_id="fixture.handler.static_check",
    )
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="a" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )

    assert recovered["run_id"] == first["run_id"]
    assert recovered["exact_successor"] == "static_check"
    with store.connect() as conn:
        claim = conn.execute(
            "SELECT status,fencing_token FROM daily_operation_claims WHERE run_id=? AND operation_id='static_check'",
            (first["run_id"],),
        ).fetchone()
    assert tuple(claim) == ("recoverable", recovered["fencing_token"])


def test_ng_rrt_restart_uses_start_seal_when_observed_sha_drifted(
    tmp_path: Path,
) -> None:
    """開始後のSHA観測差は現runを止めず、次回readiness debtへ分離する。"""

    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    started = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    observed = {
        "schemaVersion": "NEWS_GRASP_DAILY_IDENTITY_CONTEXT_V2",
        "ok": False,
        "failures": ["remote_base_sha_not_ancestor"],
        "source_baseline": "c" * 40,
        "remote_base_sha": "d" * 40,
        "manifest_reservation_id": "e" * 64,
        "runtime_generation": "fixture-runtime-generation",
        "allowed_side_effect_ids": ["external_publication"],
    }

    resolved = runtime.resolve_daily_start_identity(
        store,
        issue_date=ISSUE_DATE,
        observed_identity=observed,
    )

    assert resolved["ok"] is True
    assert resolved["resume_identity"] is True
    assert resolved["run_id"] == started["run_id"]
    assert resolved["source_baseline"] == "a" * 40
    assert resolved["remote_base_sha"] == "b" * 40
    assert resolved["runtime_generation"] == "fixture-runtime-generation"
    assert resolved["next_run_readiness_status"] == "red"
    assert "remote_base_sha_not_ancestor" in resolved["next_run_readiness_debt"]


def test_ng_rrt_tampered_start_seal_fails_integrity_without_new_generation(
    tmp_path: Path,
) -> None:
    """有効形式へのseal改変でも現run authorityへ再採用しない。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    started = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    with store.connect() as conn:
        seal = json.loads(
            conn.execute(
                "SELECT start_seal_json FROM runs WHERE run_id=?",
                (started["run_id"],),
            ).fetchone()[0]
        )
        seal["sourceBaseline"] = "c" * 40
        seal["startSealSha256"] = runtime._start_seal_sha256(seal)
        conn.execute(
            "UPDATE runs SET start_seal_json=? WHERE run_id=?",
            (json.dumps(seal, sort_keys=True, separators=(",", ":")), started["run_id"]),
        )
        conn.commit()

    resolved = runtime.resolve_daily_start_identity(
        store,
        issue_date=ISSUE_DATE,
        observed_identity={"ok": True},
    )
    assert resolved["ok"] is False
    assert resolved["status"] == "failed_integrity"
    assert "active_start_seal_receipt_mismatch" in resolved["failures"]
    with store.connect() as conn:
        assert conn.execute(
            "SELECT status FROM runs WHERE run_id=?",
            (started["run_id"],),
        ).fetchone()[0] == "failed_integrity"

    clock.value += timedelta(minutes=11)
    restarted = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="d" * 40,
        remote_base_sha="d" * 40,
        manifest_id="e" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    assert restarted["status"] == "failed_integrity"
    assert restarted["run_id"] == started["run_id"]
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runs WHERE issue_date=? AND run_intent=?",
            (ISSUE_DATE, RUN_INTENT),
        ).fetchone()[0] == 1


def test_ng_rrt_runtime_generation_is_bound_to_runtime_bytes(tmp_path: Path) -> None:
    """runtime file差替えは同じschema名でも別generationとして検出する。"""

    runtime = _module("tools.news_grasp_direct_runtime")
    repo = tmp_path / "repo"
    for relative in runtime.DAILY_RUNTIME_RELATIVE_PATHS:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())

    before = runtime.daily_runtime_generation(repo)
    changed = repo / runtime.DAILY_RUNTIME_RELATIVE_PATHS[0]
    changed.write_bytes(changed.read_bytes() + b"\n# runtime drift\n")
    after = runtime.daily_runtime_generation(repo)

    assert before.startswith(f"{runtime.RUNTIME_SCHEMA_V2}:")
    assert re.fullmatch(rf"{runtime.RUNTIME_SCHEMA_V2}:[0-9a-f]{{64}}", before)
    assert after != before


def test_ng_rrt_runtime_bytes_drift_resumes_same_contract(
    tmp_path: Path,
) -> None:
    """同じ状態契約の更新は元sealを保持し、同runの再開を許す。"""

    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    started = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation=runtime.RUNTIME_SCHEMA_V2 + ":" + "a" * 64,
        allowed_side_effect_ids=["external_publication"],
    )

    resolved = runtime.resolve_daily_start_identity(
        store,
        issue_date=ISSUE_DATE,
        observed_identity={
            "ok": True,
            "runtime_generation": runtime.RUNTIME_SCHEMA_V2 + ":" + "b" * 64,
            "allowed_side_effect_ids": ["external_publication"],
        },
    )

    assert resolved["ok"] is True
    assert resolved["runtime_compatibility"] == "same_contract"
    assert resolved["run_id"] == started["run_id"]
    assert resolved["runtime_generation"].endswith("b" * 64)
    assert resolved["sealed_runtime_generation"].endswith("a" * 64)


def test_ng_rrt_unobserved_runtime_bytes_preserve_active_identity(
    tmp_path: Path,
) -> None:
    """未観測は応答だけを保留し、保存runを破損状態へ書き換えない。"""

    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation="runtime-bytes-a",
        allowed_side_effect_ids=["external_publication"],
    )

    resolved = runtime.resolve_daily_start_identity(
        store,
        issue_date=ISSUE_DATE,
        observed_identity={
            "ok": False,
            "failures": ["runtime_generation_unobserved"],
            "runtime_generation": "",
            "allowed_side_effect_ids": ["external_publication"],
        },
    )

    assert resolved["status"] == "blocked"
    assert resolved["runtime_compatibility"] == "pending"
    with store.connect() as conn:
        assert conn.execute("SELECT status FROM runs").fetchone()[0] == "active"


def test_ng_rrt_allowed_side_effect_drift_fails_active_identity_integrity(
    tmp_path: Path,
) -> None:
    """許可副作用集合は現在runtimeのcanonical集合とexact一致させる。"""

    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(
        tmp_path / "state",
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation="runtime-bytes-a",
        allowed_side_effect_ids=["external_publication"],
    )

    resolved = runtime.resolve_daily_start_identity(
        store,
        issue_date=ISSUE_DATE,
        observed_identity={
            "ok": True,
            "runtime_generation": "runtime-bytes-a",
            "allowed_side_effect_ids": ["unauthorized_side_effect"],
        },
    )

    assert resolved["status"] == "failed_integrity"
    assert (
        "active_start_seal_allowed_side_effect_ids_mismatch"
        in resolved["failures"]
    )


def test_ng_rrt_claim_recovery_cas_conflict_rolls_back_run_takeover(
    tmp_path: Path,
) -> None:
    """破損claimを見つけたtakeoverはrunだけを部分更新しない。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    started = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="b" * 40,
        manifest_id="f" * 64,
        runtime_generation="runtime-bytes-a",
        allowed_side_effect_ids=["external_publication"],
    )
    runtime.claim_daily_operation(
        store,
        run_id=started["run_id"],
        writer_lease=started["writer_lease"],
        fencing_token=started["fencing_token"],
        operation_id="static_check",
        input_hash="input-static-check",
        handler_id="fixture.handler.static_check",
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE daily_operation_claims SET fencing_token=? WHERE run_id=?",
            (started["fencing_token"] + 99, started["run_id"]),
        )
        conn.commit()
    clock.value += timedelta(minutes=11)

    with pytest.raises(PermissionError, match="claim_recovery_cas_conflict"):
        runtime.start_run(
            store,
            cwd=repo,
            issue_date=ISSUE_DATE,
            run_intent=RUN_INTENT,
            scheduler_trigger_at="2026-09-03T06:00:00+09:00",
            source_baseline="a" * 40,
            remote_base_sha="b" * 40,
            manifest_id="f" * 64,
            runtime_generation="runtime-bytes-a",
            allowed_side_effect_ids=["external_publication"],
        )
    with store.connect() as conn:
        row = conn.execute(
            "SELECT writer_lease,fencing_token FROM runs WHERE run_id=?",
            (started["run_id"],),
        ).fetchone()
    assert tuple(row) == (started["writer_lease"], started["fencing_token"])


def test_ng_rrt_expired_external_owner_recovery_rebinds_claim_to_reconcile(
    tmp_path: Path,
) -> None:
    """expired ownerはexternal副作用を再送せず同runへreconcileだけを継承する。"""

    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    allowed = ["external_publication"]
    manifest_id = "a" * 64
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="e" * 40,
        remote_base_sha="e" * 40,
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=allowed,
    )

    # 先行3 operationだけを一度で完了し、external_publicationはclaim済みだが
    # receipt未適用の状態を作る。publish sealは外部startより前に固定する。
    for index, operation_id in enumerate(runtime.DAILY_OPERATION_ORDER[:3]):
        input_hash = f"input-{operation_id}"
        handler_id = f"fixture.handler.{operation_id}"
        runtime.claim_daily_operation(
            store,
            run_id=first["run_id"],
            writer_lease=first["writer_lease"],
            fencing_token=first["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
        )
        applied = runtime.apply_daily_operation_atomic(
            store,
            run_id=first["run_id"],
            writer_lease=first["writer_lease"],
            fencing_token=first["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt={
                "schemaVersion": f"FIXTURE_{index}_V1",
                "ok": True,
                "status": "verified",
            },
        )
        assert applied["status"] == "completed"
    runtime.claim_daily_operation(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="external_publication",
        input_hash="input-external-publication",
        handler_id="fixture.handler.external_publication",
    )
    runtime.seal_publish(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        release_commit_sha="b" * 40,
        exact_write_set=["docs/index.html"],
        file_hashes={"docs/index.html": "c" * 64},
        manifest_id=manifest_id,
        bundle_id="fixture-bundle",
        external_operation_ids=["external_publication"],
    )
    runtime.record_external_outbox(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="external_publication",
        side_effect_id="external_publication",
        status="reserved",
        payload={"issueDate": ISSUE_DATE, "runId": first["run_id"]},
    )
    runtime.transition_external_outbox(
        store,
        run_id=first["run_id"],
        writer_lease=first["writer_lease"],
        fencing_token=first["fencing_token"],
        operation_id="external_publication",
        expected_status="reserved",
        next_status="started",
    )
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="e" * 40,
        remote_base_sha="e" * 40,
        manifest_id=manifest_id,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=allowed,
    )

    assert recovered["status"] == "active"
    assert recovered["single_flight"] == "recovered_after_expired_owner"
    assert recovered["continuation_recovery"] is True
    assert recovered["run_id"] == first["run_id"]
    assert recovered["writer_lease"] != first["writer_lease"]
    # このfixtureはDaily runtimeのcustom operation recoveryだけを検証する。
    # provider external outboxのcanonical operation projection（別owner）へ
    # genericなDaily operation IDを渡して成功扱いにしない。
    with store.connect() as conn:
        claim_status = conn.execute(
            "SELECT status FROM daily_operation_claims WHERE run_id=? AND operation_id=?",
            (first["run_id"], "external_publication"),
        ).fetchone()[0]
        outbox_status = conn.execute(
            "SELECT status FROM external_outbox WHERE run_id=? AND logical_operation_id=?",
            (first["run_id"], "external_publication"),
        ).fetchone()[0]
    assert claim_status == "recoverable"
    assert outbox_status == "started"


@pytest.mark.parametrize(
    ("completed_count", "expected_successor"),
    [
        (3, "external_publication"),
        (4, "consumer_public_verification"),
    ],
)
def test_expired_owner_resumes_same_run_at_first_missing_daily_receipt(
    tmp_path: Path,
    completed_count: int,
    expected_successor: str,
) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")

    class FakeClock:
        def __init__(self) -> None:
            self.value = datetime.fromisoformat("2026-09-03T06:00:00+09:00")

        def __call__(self) -> datetime:
            return self.value

    clock = FakeClock()
    store = runtime.DirectRunStore(
        tmp_path / "state",
        clock=clock,
        lease_ttl=timedelta(minutes=10),
        test_only_allow_semantic_verifier=True,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    first = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="a" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )
    for operation_id in runtime.DAILY_OPERATION_ORDER[:completed_count]:
        input_hash = f"input-{operation_id}"
        handler_id = f"fixture.handler.{operation_id}"
        runtime.claim_daily_operation(
            store,
            run_id=first["run_id"],
            writer_lease=first["writer_lease"],
            fencing_token=first["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
        )
        runtime.apply_daily_operation_atomic(
            store,
            run_id=first["run_id"],
            writer_lease=first["writer_lease"],
            fencing_token=first["fencing_token"],
            operation_id=operation_id,
            input_hash=input_hash,
            handler_id=handler_id,
            producer_receipt={
                "schemaVersion": "FIXTURE_OPERATION_V1",
                "ok": True,
                "status": "verified",
                "external_started": operation_id == "external_publication",
            },
        )
    clock.value += timedelta(minutes=11)

    recovered = runtime.start_run(
        store,
        cwd=repo,
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        scheduler_trigger_at="2026-09-03T06:00:00+09:00",
        source_baseline="a" * 40,
        remote_base_sha="a" * 40,
        manifest_id="f" * 64,
        runtime_generation="fixture-runtime-generation",
        allowed_side_effect_ids=["external_publication"],
    )

    assert recovered["run_id"] == first["run_id"]
    assert recovered["exact_successor"] == expected_successor
    assert recovered["fencing_token"] == first["fencing_token"] + 1


@pytest.mark.parametrize("scenario", ["pre_external", "claim", "current_issue", "external_reconcile", "consumer"])
def test_compatible_runtime_update_preserves_existing_recovery(tmp_path: Path, monkeypatch, scenario: str) -> None:
    """既存の回復oracleを、開始後に実bytes世代が変わる条件でも満たす。"""
    runtime = _module("tools.news_grasp_direct_runtime")
    original_start = runtime.start_run
    calls = 0
    def changed_generation(*args, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["runtime_generation"] = runtime.RUNTIME_SCHEMA_V2 + ":" + ("a" if calls == 1 else "b") * 64
        def immutable_rows():
            with args[0].connect() as conn:
                return (
                    [tuple(row) for row in conn.execute("SELECT run_id,start_seal_json FROM runs ORDER BY run_id")],
                    [tuple(row) for row in conn.execute("SELECT * FROM daily_operation_receipts ORDER BY run_id,operation_index")],
                )
        before = immutable_rows() if calls > 1 else None
        result = original_start(*args, **kwargs)
        if before is not None:
            assert immutable_rows() == before
        return result
    monkeypatch.setattr(runtime, "start_run", changed_generation)
    if scenario == "pre_external":
        test_ng_rrt_expired_pre_external_owner_resumes_same_run_from_next_operation(tmp_path)
    elif scenario == "claim":
        test_ng_rrt_expired_claim_resumes_same_run_without_repeating_green_receipts(tmp_path)
    elif scenario == "external_reconcile":
        test_ng_rrt_expired_external_owner_recovery_rebinds_claim_to_reconcile(tmp_path)
    else:
        count = 2 if scenario == "current_issue" else 4
        test_expired_owner_resumes_same_run_at_first_missing_daily_receipt(tmp_path, count, runtime.DAILY_OPERATION_ORDER[count])
    assert calls == 2


@pytest.mark.parametrize("observed", ["", "unknown", "NEWS_GRASP_DIRECT_RUNTIME_V3:" + "b" * 64, "db_schema_changed"])
def test_unknown_runtime_compatibility_leaves_canonical_db_unchanged(tmp_path: Path, observed: str) -> None:
    runtime = _module("tools.news_grasp_direct_runtime")
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    kwargs = dict(cwd=repo, issue_date=ISSUE_DATE, run_intent=RUN_INTENT,
                  runtime_generation=runtime.RUNTIME_SCHEMA_V2 + ":" + "a" * 64,
                  allowed_side_effect_ids=["external_publication"])
    first = runtime.start_run(store, **kwargs)
    if observed == "db_schema_changed":
        with store.connect() as conn:
            conn.execute("UPDATE runs SET runtime_schema='UNKNOWN_RUNTIME_SCHEMA' WHERE run_id=?", (first["run_id"],))
            conn.commit()
        observed = runtime.RUNTIME_SCHEMA_V2 + ":" + "b" * 64
    before = store.db_path.read_bytes()
    result = runtime.start_run(store, **{**kwargs, "runtime_generation": observed})
    assert result["status"] == "blocked"
    assert result["runtime_compatibility"] == "pending"
    assert "writer_lease" not in result
    assert store.db_path.read_bytes() == before
