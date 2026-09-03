from __future__ import annotations

import importlib
import inspect
import json
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
    launcher = r"C:\Users\hidek\AppData\Local\Programs\Python\Python312\python.exe -m tools.news_grasp_daily_launcher"
    assert prompt.count(launcher) == 1
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
