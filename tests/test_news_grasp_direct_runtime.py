"""06:00 direct mainline の実dispatcherとsemantic completionを検証する。

外部配信は行わず、一時state root、決定論的clock/host generation、検証器が
所有する観測surface rowだけを注入する。callerの``ok``や偽のcompletion JSON
はauthorityにしない。
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


REPO = Path(__file__).resolve().parents[1]
ISSUE_DATE = "2026-08-30"
AUTOMATION_ID = "news-grasp-6-40"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
STARTED_AT = datetime(2026, 8, 30, 6, 0, tzinfo=JST)
EXPECTED_TITLE = "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"

EXPECTED_STAGES = (
    "title_control",
    "issue_inventory",
    "category_collection",
    "evidence_dedup_freshness",
    "category_digest",
    "reporter_validation",
    "articles_jsonl",
    "summary",
    "daily_audio",
    "deepdive_article",
    "deepdive_quality",
    "html_docs",
    "daily_quality",
    "youtube_podcasts",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "commit_push",
    "pages_verify",
    "public_completion",
)

PUBLIC_SURFACES = (
    "web",
    "daily_audio",
    "deepdive_article",
    "deepdive_audio",
    "youtube_daily",
    "youtube_deepdive",
    "playlist",
    "notification",
    "distribution",
    "publish_status",
    "remote_commit",
    "pages",
)


class _Clock:
    """runtimeへ注入する決定論的なJST clock。"""

    def __init__(self, value: datetime = STARTED_AT) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def move(self, **delta: int) -> None:
        self.value += timedelta(**delta)


class _HostGeneration:
    """OS/host側の単調generationをcontentから独立して供給する。"""

    def __init__(self, value: int = 1) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


class FakeSemanticVerifier:
    """検証器が所有する観測rowだけでstage結果を決めるfake。

    ``observed_surface`` と ``caller_result`` は参照しない。dispatcherがcaller
    自己申告をsemantic oracleへ昇格させた場合、このテストはRedになる。
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def observe(self, stage_id: str, **row: Any) -> None:
        self.rows[stage_id] = dict(row)

    def verify(self, stage_id: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(stage_id)
        return dict(self.rows.get(stage_id, {"ok": False, "status": "unobserved"}))

    def __call__(self, stage_id: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self.verify(stage_id, *_args, **_kwargs)


def _api():
    """direct producerとdispatcherの欠落を明示的なRedにする。"""

    try:
        module = importlib.import_module("tools.news_grasp_direct_runtime")
    except ModuleNotFoundError as error:
        pytest.fail(f"RED_DIRECT_RUNTIME_PRODUCER_MISSING:{error}")
    required = (
        "DIRECT_STAGES",
        "DirectRunStore",
        "start_run",
        "run_exact_successor",
        "inspect_run",
        "verify_public_completion",
        "validate_installed_automation_semantics",
    )
    missing = [name for name in required if not hasattr(module, name)]
    assert not missing, f"RED_DIRECT_RUNTIME_API_MISSING:{','.join(missing)}"
    if hasattr(module, "advance_stage"):
        assert callable(module.advance_stage)
    return module


def _mapping(value: object) -> Mapping[str, Any]:
    """dictまたはruntimeのprojectionをmappingとして読む。"""

    if isinstance(value, Mapping):
        return value
    for method_name in ("as_dict", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            projected = method()
            if isinstance(projected, Mapping):
                return projected
    if hasattr(value, "__dict__"):
        projected = vars(value)
        if isinstance(projected, Mapping):
            return projected
    raise AssertionError(f"runtime result is not inspectable: {value!r}")


def _store(
    api: Any,
    tmp_path: Path,
    *,
    clock: _Clock | None = None,
    host_generation: _HostGeneration | None = None,
    lease_seconds: int = 60,
    verifier: FakeSemanticVerifier | None = None,
) -> tuple[Any, _Clock, _HostGeneration, FakeSemanticVerifier]:
    clock = clock or _Clock()
    host_generation = host_generation or _HostGeneration()
    verifier = verifier or FakeSemanticVerifier()
    state_root = tmp_path / "日本語-direct-runtime-state"
    state_root.mkdir(parents=True, exist_ok=True)
    store = api.DirectRunStore(
        state_root,
        clock=clock,
        host_generation=host_generation,
        lease_ttl=timedelta(seconds=lease_seconds),
        semantic_verifier=verifier,
        test_only_allow_semantic_verifier=True,
    )
    return store, clock, host_generation, verifier


def _start(
    api: Any,
    store: Any,
    cwd: Path,
    *,
    issue_date: str = ISSUE_DATE,
    automation_id: str = AUTOMATION_ID,
) -> Mapping[str, Any]:
    return _mapping(
        api.start_run(
            store,
            automation_id=automation_id,
            cwd=str(cwd),
            issue_date=issue_date,
            manifest_id="f" * 64,
        )
    )


def _run_id(run: Mapping[str, Any]) -> str:
    value = run.get("run_id", run.get("runId"))
    assert str(value or "").strip(), f"run id missing: {run!r}"
    return str(value)


def _lease(run: Mapping[str, Any]) -> str:
    value = run.get("writer_lease", run.get("writerLease", run.get("lease")))
    assert str(value or "").strip(), f"writer lease missing: {run!r}"
    return str(value)


def _generation(run: Mapping[str, Any]) -> int:
    value = run.get(
        "generation",
        run.get("host_generation", run.get("hostGeneration")),
    )
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"generation missing: {run!r}"
    )
    return value


def _dispatch(
    api: Any,
    store: Any,
    run: Mapping[str, Any],
    verifier: FakeSemanticVerifier,
    *,
    clock: _Clock,
    caller_ok: bool = True,
    requested_stage_id: str | None = None,
    caller_surface: Mapping[str, Any] | None = None,
    writer_lease: str | None = None,
) -> Mapping[str, Any]:
    """manual stage mutationではなく実dispatcherのexact successorを実行する。"""

    kwargs: dict[str, Any] = {
        "run_id": _run_id(run),
        "writer_lease": writer_lease or _lease(run),
        "caller_result": {"ok": caller_ok},
        "observed_surface": dict(caller_surface or {"ok": caller_ok}),
        "semantic_verifier": verifier,
        "observed_at": clock(),
    }
    if requested_stage_id is not None:
        kwargs["stage_id"] = requested_stage_id
    return _mapping(api.run_exact_successor(store, **kwargs))


def _inspect(api: Any, store: Any, run: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(api.inspect_run(store, run_id=_run_id(run)))


def _set_green(verifier: FakeSemanticVerifier, stage_id: str, **row: Any) -> None:
    verifier.observe(stage_id, ok=True, status="green", **row)


def _complete_before(
    api: Any,
    store: Any,
    run: Mapping[str, Any],
    verifier: FakeSemanticVerifier,
    stage_id: str,
    *,
    clock: _Clock,
) -> None:
    """指定stage直前までを実dispatcherで進める。"""

    assert stage_id in EXPECTED_STAGES
    for current in EXPECTED_STAGES[: EXPECTED_STAGES.index(stage_id)]:
        if current == "title_control":
            _set_green(
                verifier,
                current,
                title_status="already_ok",
                actual_title=EXPECTED_TITLE,
                post_publish_issue_list=[],
            )
        else:
            _set_green(verifier, current)
        _dispatch(api, store, run, verifier, clock=clock)


def _public_rows(issue_date: str = ISSUE_DATE) -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "status": "green",
            "issue_date": issue_date,
            "public_evidence": f"fixture/{surface}.json",
            "semantic_ok": True,
        }
        for surface in PUBLIC_SURFACES
    }


def _set_public_green(verifier: FakeSemanticVerifier) -> None:
    verifier.observe(
        "public_completion",
        ok=True,
        status="green",
        issue_date=ISSUE_DATE,
        public_surfaces=_public_rows(),
        completion_mode="direct_public_v1",
    )


def _finalize_public(api: Any, store: Any, run: Mapping[str, Any], verifier: FakeSemanticVerifier) -> Mapping[str, Any]:
    _set_public_green(verifier)
    return _mapping(api.finalize_public_completion(
        store,
        run_id=_run_id(run),
        writer_lease=_lease(run),
        semantic_verifier=verifier,
        exact_successor="public_completion",
    ))


def test_public_completion_reverifies_public_surface_when_base_url_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """completed state内のpublic_stageだけを実公開検証の代替にしない。"""

    api = _api()
    store, clock, _host_generation, verifier = _store(api, tmp_path)
    cwd = tmp_path / "News-Grasp"
    cwd.mkdir()
    run = _start(api, store, cwd)
    for stage_id in EXPECTED_STAGES[:-1]:
        if stage_id == "title_control":
            _set_green(
                verifier,
                stage_id,
                title_status="already_ok",
                actual_title=EXPECTED_TITLE,
                post_publish_issue_list=[],
            )
        else:
            _set_green(verifier, stage_id)
        _dispatch(api, store, run, verifier, clock=clock)
    _finalize_public(api, store, run, verifier)

    completion = importlib.import_module("tools.news_grasp_direct_completion")
    calls: list[dict[str, Any]] = []

    def actual_public_red(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {
            "ok": False,
            "status": "red",
            "completion_mode": "direct_public_v1",
            "issue_date": ISSUE_DATE,
            "failures": ["actual_public_surface_red"],
        }

    monkeypatch.setattr(completion, "verify_direct_public_completion", actual_public_red)

    result = api.verify_public_completion(
        store,
        run_id=_run_id(run),
        repo_root=REPO,
        public_base_url="https://hidepon-umg.github.io/News-Grasp",
        wait_sec=0,
        poll_sec=0,
    )

    assert calls, "actual public verifier was not called"
    assert result["ok"] is False
    assert "actual_public_surface_red" in result["failures"]


def test_direct_runtime_exports_real_dispatcher_api() -> None:
    """新direct runtimeは手動advanceだけでなくexact successor dispatcherを公開する。"""

    api = _api()
    assert tuple(api.DIRECT_STAGES) == EXPECTED_STAGES
    assert len(api.DIRECT_STAGES) == 21


def test_dispatcher_and_atomic_finalizer_complete_normal_run(tmp_path: Path) -> None:
    """工程0〜19はdispatcher、工程20はatomic finalizerで完了する。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    for stage_id in EXPECTED_STAGES[:-1]:
        if stage_id == "title_control":
            _set_green(
                verifier,
                stage_id,
                title_status="already_ok",
                actual_title=EXPECTED_TITLE,
                post_publish_issue_list=[],
            )
        else:
            _set_green(verifier, stage_id)
        result = _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "claim": "caller-only"},
        )
        assert result.get("stage", result.get("completed_stage", stage_id)) == stage_id
    final = _finalize_public(api, store, run, verifier)
    assert final.get("status") in {"complete", "completed"}
    state = _inspect(api, store, run)
    assert state.get("status", "").casefold() in {"complete", "completed", "green"}
    assert verifier.calls[: len(EXPECTED_STAGES) - 1] == list(EXPECTED_STAGES[:-1])
    assert set(verifier.calls[len(EXPECTED_STAGES) - 1 :]) == {"public_completion"}


def test_duplicate_and_stale_writer_are_rejected_with_monotonic_generation(
    tmp_path: Path,
) -> None:
    """identity単位single-flight、host generation、opaque writer leaseを検証する。"""

    api = _api()
    store, clock, host, verifier = _store(api, tmp_path, lease_seconds=30)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    first = _start(api, store, cwd)
    first_id = _run_id(first)
    first_lease = _lease(first)
    first_generation = _generation(first)

    duplicate: Mapping[str, Any] | None = None
    try:
        duplicate = _start(api, store, cwd / ".")
    except (RuntimeError, ValueError) as error:
        assert "single" in str(error).casefold() or "flight" in str(error).casefold()
    if duplicate is not None:
        assert _run_id(duplicate) == first_id
        assert str(duplicate.get("status", "")).casefold() in {
            "attached",
            "active",
            "existing",
            "single_flight",
        }

    assert first_lease not in {first_id, AUTOMATION_ID, ISSUE_DATE}
    clock.move(seconds=31)
    host.value = 2
    second = _start(api, store, cwd)
    assert _run_id(second) != first_id
    assert _generation(second) > first_generation
    assert _lease(second) != first_lease
    _set_green(
        verifier,
        "title_control",
        title_status="already_ok",
        actual_title=EXPECTED_TITLE,
        post_publish_issue_list=[],
    )
    with pytest.raises(
        (PermissionError, RuntimeError, ValueError),
        match="(?i)(stale|lease|fenc|owner)",
    ):
        _dispatch(
            api,
            store,
            second,
            verifier,
            clock=clock,
            writer_lease=first_lease,
        )
    assert _inspect(api, store, second).get("current_stage") == "title_control"


def test_dispatcher_rejects_skip_and_reverse_before_state_change(tmp_path: Path) -> None:
    """exact successor以外のskip/reverseをdispatcherが拒否する。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    _set_green(verifier, "issue_inventory")
    with pytest.raises(
        (RuntimeError, ValueError),
        match="(?i)(order|skip|stage|sequence|successor)",
    ):
        _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            requested_stage_id="issue_inventory",
        )
    assert _inspect(api, store, run).get("current_stage") == "title_control"

    _set_green(
        verifier,
        "title_control",
        title_status="already_ok",
        actual_title=EXPECTED_TITLE,
        post_publish_issue_list=[],
    )
    _dispatch(api, store, run, verifier, clock=clock)
    with pytest.raises(
        (RuntimeError, ValueError),
        match="(?i)(order|reverse|stage|sequence|successor)",
    ):
        _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            requested_stage_id="title_control",
        )
    assert _inspect(api, store, run).get("current_stage") == "issue_inventory"


def test_fake_semantic_verifier_red_overrides_caller_ok(tmp_path: Path) -> None:
    """caller ok=trueでも検証器owned rowがRedならstageは進まない。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    verifier.observe(
        "title_control",
        ok=False,
        status="red",
        title_status="updated",
        actual_title="wrong title",
    )
    try:
        result = _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "actual_title": EXPECTED_TITLE},
        )
    except (RuntimeError, ValueError):
        result = {}
    assert _inspect(api, store, run).get("current_stage") == "title_control"
    assert result.get("status", "").casefold() not in {"complete", "completed", "green"}
    assert verifier.calls == ["title_control"]


@pytest.mark.parametrize("status", ["unavailable", "failed", "skipped"])
def test_title_failure_is_recorded_and_dispatcher_continues(
    tmp_path: Path, status: str
) -> None:
    """title action失敗はissue記録後、公開本線を止めない。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / f"repo-{status}"
    cwd.mkdir()
    run = _start(api, store, cwd)
    verifier.observe(
        "title_control",
        ok=True,
        status="green",
        title_status=status,
        actual_title="",
        post_publish_issue_list=[f"title: host_action_{status}"],
    )
    result = _dispatch(api, store, run, verifier, clock=clock)
    assert result.get("next_stage", result.get("successor")) == "issue_inventory"
    state = _inspect(api, store, run)
    assert state.get("current_stage") == "issue_inventory"
    issues = state.get("post_publish_issue_list", state.get("postPublishIssueList", []))
    assert any("title" in str(item).casefold() for item in issues)


def test_title_success_requires_exact_actual_title(tmp_path: Path) -> None:
    """updated/already_okの成功claimは実title exact一致だけをGreenにする。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    verifier.observe(
        "title_control",
        ok=False,
        status="red",
        title_status="already_ok",
        actual_title=EXPECTED_TITLE + " ",
        post_publish_issue_list=[],
    )
    with pytest.raises((RuntimeError, ValueError), match="(?i)(title|semantic|oracle|exact)"):
        _dispatch(api, store, run, verifier, clock=clock, caller_ok=True)
    assert _inspect(api, store, run).get("current_stage") == "title_control"

    verifier.observe(
        "title_control",
        ok=True,
        status="green",
        title_status="updated",
        actual_title=EXPECTED_TITLE,
        post_publish_issue_list=[],
    )
    _dispatch(api, store, run, verifier, clock=clock)
    assert _inspect(api, store, run).get("current_stage") == "issue_inventory"


def test_quality_red_stays_at_same_stage_then_dispatches_exact_successor(
    tmp_path: Path,
) -> None:
    """quality Redは同じstageに留まり、修復後だけexact successorへ進む。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    _complete_before(api, store, run, verifier, "daily_quality", clock=clock)
    verifier.observe(
        "daily_quality",
        ok=False,
        status="red",
        artifact_surface="daily_quality",
    )
    try:
        red = _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "quality": "green"},
        )
    except (RuntimeError, ValueError):
        red = {}
    state = _inspect(api, store, run)
    assert state.get("current_stage") == "daily_quality"
    assert state.get("exact_successor", state.get("next_stage", "daily_quality")) == "daily_quality"
    assert red.get("next_stage", red.get("successor", "daily_quality")) == "daily_quality"

    _set_green(verifier, "daily_quality", artifact_surface="daily_quality-repaired")
    green = _dispatch(api, store, run, verifier, clock=clock)
    assert green.get("next_stage", green.get("successor")) == "youtube_podcasts"
    assert _inspect(api, store, run).get("current_stage") == "youtube_podcasts"


def test_youtube_quota_failure_is_surface_scoped_and_web_successor_continues(
    tmp_path: Path,
) -> None:
    """YouTube quota failureだけをdeferし、playlist以降のsuccessorを継続する。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    _complete_before(api, store, run, verifier, "youtube_podcasts", clock=clock)
    verifier.observe(
        "youtube_podcasts",
        ok=False,
        status="external_failure",
        surface="youtube_daily",
        reason="quota fixture",
        surface_scoped=True,
        recoverable=True,
        successor="playlist",
    )
    failed = _dispatch(
        api,
        store,
        run,
        verifier,
        clock=clock,
        caller_ok=True,
        caller_surface={"ok": True, "surface": "youtube_daily"},
    )
    assert failed.get("status", "").casefold() in {
        "deferred",
        "surface_red",
        "continued",
        "accepted",
    }
    assert failed.get("next_stage", failed.get("successor")) == "playlist"
    state = _inspect(api, store, run)
    assert state.get("current_stage") == "playlist"
    failures = state.get("surface_failures", state.get("surfaceFailures", []))
    assert any("youtube" in str(item).casefold() for item in failures)

    _set_green(verifier, "playlist")
    _dispatch(api, store, run, verifier, clock=clock)
    completion = _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    )
    assert completion.get("ok") is False
    assert any("youtube" in str(item).casefold() for item in completion.get("failures", ()))


def test_ninety_minute_overrun_keeps_public_successor_available(tmp_path: Path) -> None:
    """90分超過はSLO debtであり、public successorの実行を止めない。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    verifier.observe(
        "title_control",
        ok=True,
        status="green",
        title_status="unavailable",
        actual_title="",
        post_publish_issue_list=["title: host unavailable"],
    )
    _dispatch(api, store, run, verifier, clock=clock)
    clock.move(minutes=91)
    _set_green(verifier, "issue_inventory")
    result = _dispatch(api, store, run, verifier, clock=clock)
    assert result.get("next_stage", result.get("successor")) == "category_collection"
    state = _inspect(api, store, run)
    assert state.get("current_stage") == "category_collection"
    slo = state.get("slo", {})
    assert slo.get("slo_debt", slo.get("sloDebt", state.get("slo_debt"))) is True
    assert state.get("status", "").casefold() not in {"complete", "completed", "terminal_red"}


def test_completion_requires_all_stages_and_public_semantic_green(tmp_path: Path) -> None:
    """全stageとpublic semantic rowのGreenだけをcompletion authorityにする。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    _complete_before(api, store, run, verifier, "public_completion", clock=clock)
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        public_surfaces=_public_rows(),
    )
    red = api.probe_public_completion(store, run_id=_run_id(run), semantic_verifier=verifier)
    assert _inspect(api, store, run).get("current_stage") == "public_completion"
    assert red.get("status", "").casefold() not in {"complete", "completed", "green"}
    assert _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    ).get("ok") is False

    green = _finalize_public(api, store, run, verifier)
    assert green.get("status", "").casefold() in {"complete", "completed", "green"}
    completion = _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    )
    assert completion.get("ok") is True
    assert completion.get("completion_mode") == "direct_public_v1"


def test_publish_status_only_cannot_claim_public_completion(tmp_path: Path) -> None:
    """publish-status単独は他public surfaceのsemantic Greenを代替しない。"""

    api = _api()
    store, _, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    forged = {
        "completion_mode": "direct_public_v1",
        "issue_date": ISSUE_DATE,
        "public_surfaces": {
            "publish_status": {
                "status": "published_ok",
                "issue_date": ISSUE_DATE,
                "public_evidence": "fixture/publish-status.json",
            }
        },
        "ok": True,
    }
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        failures=["public_surface_unobserved"],
    )
    result = _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            completion_receipt=forged,
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False


def test_url_200_only_cannot_claim_web_semantic_completion(tmp_path: Path) -> None:
    """HTTP 200だけで読者可視semantic Greenを自己申告できない。"""

    api = _api()
    store, _, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    public = _public_rows()
    public["web"] = {
        "status": "reachable",
        "http_status": 200,
        "semantic_ok": False,
        "issue_date": ISSUE_DATE,
        "public_evidence": "fixture/web-response.json",
    }
    forged = {
        "completion_mode": "direct_public_v1",
        "issue_date": ISSUE_DATE,
        "public_surfaces": public,
        "ok": True,
    }
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        public_surfaces=public,
        failures=["web_semantics_unverified"],
    )
    result = _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            completion_receipt=forged,
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False
    assert any("web" in str(item).casefold() for item in result.get("failures", ()))


def _authority_field_names(path: Path) -> set[str]:
    """active direct controlのfield-like nameだけを抽出する。"""

    text = path.read_text(encoding="utf-8-sig")
    names: set[str] = set()
    if path.suffix == ".py":
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        names.add(key.value)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
                    elif isinstance(target, ast.Attribute):
                        names.add(target.attr)
    else:
        for match in re.finditer(
            r"(?im)(?:^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=|[\"']([^\"']+)[\"']\s*:)",
            text,
        ):
            names.add(next(item for item in match.groups() if item is not None))
    return names


def test_active_direct_controls_have_no_content_derived_authority_fields() -> None:
    """direct controlにcontent-derived authority fieldを置かない。"""

    paths = (
        REPO / "tools" / "news_grasp_direct_runtime.py",
        REPO / "tools" / "news_grasp_direct_completion.py",
        REPO / "automation" / "news-grasp-6-40" / "completion_guard.py",
        REPO / "automation" / "news-grasp-6-40" / "automation.toml.template",
        REPO / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md",
        REPO
        / "automation"
        / "skills"
        / "news-grasp-direct-mainline"
        / "agents"
        / "openai.yaml",
    )
    missing = [str(path.relative_to(REPO)) for path in paths if not path.is_file()]
    assert not missing, f"RED_DIRECT_CONTROL_SURFACE_MISSING:{missing}"

    # digestはidentity/parity証拠には利用できるが、completion単独authority名は禁止する。
    forbidden_parts = (
        "completion_" + "sha256",
        "completion_" + "hash",
        "completion_" + "digest",
        "hash_" + "only_" + "completion",
    )
    forbidden = re.compile("(?i)(?:" + "|".join(forbidden_parts) + ")")
    violations = {
        str(path.relative_to(REPO)): sorted(
            name for name in _authority_field_names(path) if forbidden.search(name)
        )
        for path in paths
        if any(forbidden.search(name) for name in _authority_field_names(path))
    }
    assert not violations, f"DIRECT_CONTENT_DERIVED_AUTHORITY_FIELD:{violations}"


def test_installed_direct_config_semantics_are_validated_by_producer() -> None:
    """installed configはLuna/max/06:00/direct skill/title contractを検証器で判定する。"""

    api = _api()
    path = Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"
    assert path.is_file(), f"RED_INSTALLED_DIRECT_CONFIG_MISSING:{path}"
    result = _mapping(api.validate_installed_automation_semantics(path))
    assert result.get("ok") is True

    value = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    assert str(value.get("model", "")).casefold().endswith("luna")
    assert value.get("reasoning_effort") == "max"
    assert str(value.get("rrule", "")).upper() == (
        "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"
    )
    assert isinstance(value.get("created_at"), int)
    assert isinstance(value.get("updated_at"), int)
    assert value["created_at"] > 0
    assert value["updated_at"] >= value["created_at"]
    assert result.get("created_at") == value["created_at"]
    assert result.get("updated_at") == value["updated_at"]
    cwds = value.get("cwds")
    assert isinstance(cwds, list) and cwds
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    assert any(
        syncer._same_path(Path(str(item)), REPO)  # noqa: SLF001
        or syncer._same_git_repository(Path(str(item)), REPO)  # noqa: SLF001
        for item in cwds
    )
    prompt = str(value.get("prompt", ""))
    assert "$news-grasp-direct-mainline" in prompt
    assert "YY/MM/DD" in prompt
    assert "title_status" in prompt
    assert "title_status=already_ok" in prompt
    assert "post_publish_issue_list" in prompt
    assert "direct completion guard" in prompt

    live_result = _mapping(api.validate_installed_automation_semantics())
    assert live_result.get("ok") is True
    assert live_result.get("app_db", {}).get("reasoning_effort") == "max"
    assert live_result.get("snapshots")
    assert all(item.get("ok") is True for item in live_result["snapshots"])


def test_codex_automation_sync_renders_luna_max_and_preserves_app_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """staleなtargetをApp project registryのNews-Grasp bindingへ復旧する。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    app_state = tmp_path / ".codex-global-state.json"
    app_state.write_text(
        json.dumps(
            {
                "local-projects": {
                    "local-test-project": {
                        "id": "local-test-project",
                        "name": "News-Grasp",
                        "rootPaths": [str(REPO.resolve())],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(syncer, "_default_app_global_state", lambda: app_state, raising=False)
    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed_path = fixture_root / "automation.toml"
    installed_path.parent.mkdir(parents=True)
    installed_path.write_text(
        "\n".join(
            [
                'version = 1',
                f'id = "{AUTOMATION_ID}"',
                'kind = "cron"',
                'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"',
                'prompt = "old"',
                'status = "ACTIVE"',
                'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
                'model = "gpt-5.6-luna"',
                'reasoning_effort = "medium"',
                'execution_environment = "local"',
                'target = { type = "project", project_id = "local-stale-project" }',
                f"cwds = [{json.dumps(str(tmp_path), ensure_ascii=False)}]",
                'created_at = 10',
                'updated_at = 20',
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = syncer.sync(
        repo_root=REPO,
        template_path=template_path,
        installed_path=installed_path,
        allow_custom_paths=True,
    )
    value = tomllib.loads(installed_path.read_text(encoding="utf-8-sig"))

    assert result["ok"] is True
    assert value["reasoning_effort"] == "max"
    assert value["target"]["project_id"] == "local-test-project"
    assert value["cwds"] == [str(REPO.resolve())]
    assert "$news-grasp-direct-mainline" in value["prompt"]
    assert "title_status=already_ok" in value["prompt"]
    assert "direct completion guard" in value["prompt"]
    assert "public incomplete のまま最終応答しないでください" not in value["prompt"]
    assert "最初に `python -m tools.news_grasp_direct_runtime start" not in value["prompt"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows HANDLE ABI regression")
def test_windows_no_reparse_handle_api_is_import_order_independent() -> None:
    """共有kernel32関数の型定義をmodule import順で破壊しない。"""

    program = r"""
import importlib
import sys

target = sys.argv[1]
orders = (
    ("tools.sync_news_grasp_codex_automation", "tools.news_grasp_direct_completion"),
    ("tools.news_grasp_direct_completion", "tools.sync_news_grasp_codex_automation"),
)
for first_name, second_name in orders:
    first = importlib.reload(importlib.import_module(first_name))
    second = importlib.reload(importlib.import_module(second_name))
    modules = {first_name: first, second_name: second}
    syncer = modules["tools.sync_news_grasp_codex_automation"]
    completion = modules["tools.news_grasp_direct_completion"]
    sync_handle, _sync_size = syncer._open_windows_no_reparse(
        syncer.Path(target),
        directory=False,
    )
    syncer._CloseHandle(sync_handle)
    completion_handle, _completion_size = completion._open_windows_file_no_reparse(
        completion.Path(target)
    )
    completion._CloseHandle(completion_handle)
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, str((REPO / "docs" / "spec.md").resolve())],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_codex_automation_sync_can_project_direct_skill_to_installed_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """automation prompt が読む installed skill を repo source と同じ内容へ同期できる。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed_path = fixture_root / "automation.toml"
    installed_path.parent.mkdir(parents=True)
    installed_path.write_text(template_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    source_skill = REPO / "automation/skills/news-grasp-direct-mainline/SKILL.md"
    installed_skill = fixture_root / "skills" / "news-grasp-direct-mainline" / "SKILL.md"
    installed_skill.parent.mkdir(parents=True)
    installed_skill.write_text("# stale\n最大1回だけ試す\n", encoding="utf-8")

    result = syncer.sync(
        repo_root=REPO,
        template_path=template_path,
        installed_path=installed_path,
        source_skill_path=source_skill,
        installed_skill_path=installed_skill,
        write_skill=True,
        allow_custom_paths=True,
    )

    assert result["ok"] is True
    assert result["skill_changed"] is True
    assert result["skill"]["ok"] is True
    assert installed_skill.read_text(encoding="utf-8-sig") == source_skill.read_text(encoding="utf-8-sig")


def test_codex_automation_sync_cli_rejects_custom_write_paths_without_explicit_allow(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLIのpath overrideは、明示allowなしで任意書込に使えない。"""

    monkeypatch.delenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", raising=False)
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    rc = syncer._main(  # noqa: SLF001
        [
            "--installed",
            str(tmp_path / "automation.toml"),
            "--write-snapshot",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert result["ok"] is False
    assert result["failures"] == [
        "custom_path_override_requires_explicit_allow_custom_paths"
    ]
    assert result["custom_path_args"] == {"installed": True}
    assert not (tmp_path / "automation.toml").exists()


def test_codex_automation_sync_updates_app_visible_db_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App画面が参照するSQLite rowもMedium/旧promptからtemplateへ同期できる。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    app_state = tmp_path / ".codex-global-state.json"
    app_state.write_text(
        json.dumps(
            {
                "local-projects": {
                    "local-test-project": {
                        "id": "local-test-project",
                        "name": "News-Grasp",
                        "rootPaths": [str(REPO.resolve())],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(syncer, "_default_app_global_state", lambda: app_state, raising=False)
    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed_path = fixture_root / "automation.toml"
    installed_path.parent.mkdir(parents=True)
    installed_path.write_text(template_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    app_db = fixture_root / "codex-dev.db"
    conn = sqlite3.connect(app_db)
    conn.execute(
        """
        create table automations (
            id text primary key,
            name text not null,
            prompt text not null,
            status text not null default 'ACTIVE',
            next_run_at integer,
            last_run_at integer,
            cwds text not null default '[]',
            rrule text not null,
            model text,
            reasoning_effort text,
            created_at integer not null,
            updated_at integer not null,
            target_type text,
            project_id text
        )
        """
    )
    conn.execute(
        """
        insert into automations
        (id, name, prompt, status, next_run_at, last_run_at, cwds, rrule, model,
         reasoning_effort, created_at, updated_at, target_type, project_id)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            AUTOMATION_ID,
            "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開",
            "最初に `python -m tools.news_grasp_direct_runtime start --state-root build/direct-mainline`",
            "ACTIVE",
            123,
            100,
            json.dumps([str(tmp_path)], ensure_ascii=False),
            "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0",
            "gpt-5.6-luna",
            "medium",
            10,
            20,
            "project",
            "local-stale-project",
        ),
    )
    conn.commit()
    conn.close()

    result = syncer.sync(
        repo_root=REPO,
        template_path=template_path,
        installed_path=installed_path,
        app_db_path=app_db,
        write_app_db=True,
        allow_custom_paths=True,
    )

    assert result["ok"] is True
    assert result["app_db"]["ok"] is True
    assert result["app_db_changed"] is True
    conn = sqlite3.connect(app_db)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("select * from automations where id = ?", (AUTOMATION_ID,)).fetchone())
    conn.close()
    assert row["reasoning_effort"] == "max"
    assert row["model"] == "gpt-5.6-luna"
    assert json.loads(row["cwds"]) == [str(REPO.resolve())]
    assert row["project_id"] == "local-test-project"
    assert "$news-grasp-direct-mainline" in row["prompt"]
    assert "public incomplete のまま最終応答しないでください" not in row["prompt"]
    assert "最初に `python -m tools.news_grasp_direct_runtime start" not in row["prompt"]


@pytest.mark.parametrize(
    ("projects", "expected_failure"),
    [
        ({}, "app_project_binding_missing"),
        (
            {
                "local-project-a": {
                    "id": "local-project-a",
                    "name": "News-Grasp A",
                    "rootPaths": [str(REPO.resolve())],
                },
                "local-project-b": {
                    "id": "local-project-b",
                    "name": "News-Grasp B",
                    "rootPaths": [str(REPO.resolve())],
                },
            },
            "app_project_binding_ambiguous",
        ),
    ],
)
def test_codex_automation_sync_rejects_missing_or_ambiguous_app_project_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    projects: dict[str, Any],
    expected_failure: str,
) -> None:
    """App正本にexact-one project bindingが無ければwrite前にfail-closedする。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    app_state = tmp_path / ".codex-global-state.json"
    app_state.write_text(
        json.dumps({"local-projects": projects}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(syncer, "_default_app_global_state", lambda: app_state, raising=False)
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed_path = fixture_root / "automation.toml"
    installed_path.parent.mkdir(parents=True)
    original = (REPO / "automation/news-grasp-6-40/automation.toml.template").read_text(
        encoding="utf-8-sig"
    )
    installed_path.write_text(original, encoding="utf-8")

    result = syncer.sync(
        repo_root=REPO,
        template_path=REPO / "automation/news-grasp-6-40/automation.toml.template",
        installed_path=installed_path,
        allow_custom_paths=True,
    )

    assert result["ok"] is False
    assert result["failures"] == [expected_failure]
    assert result["changed"] is False
    assert installed_path.read_text(encoding="utf-8-sig") == original


def test_codex_automation_sync_updates_project_and_shadow_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runtime freshness用snapshotを、project側とshadow側の両方で同期する。"""

    monkeypatch.setenv("NEWS_GRASP_ALLOW_TEST_SYNC_PATHS", "1")
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    fixture_root = tmp_path / "news-grasp-sync-fixture"
    installed_path = fixture_root / "automation.toml"
    project_snapshot = fixture_root / "AIHarnessState" / "snapshot" / "codex" / "automations" / AUTOMATION_ID / "automation.toml"
    shadow_snapshot = fixture_root / ".codex" / "state" / "harness-worktrees" / "AIHarnessState-global-harness-v1" / "snapshot" / "codex" / "automations" / AUTOMATION_ID / "automation.toml"

    installed_path.parent.mkdir(parents=True)
    installed_path.write_text(template_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
    project_snapshot.parent.mkdir(parents=True)
    project_snapshot.write_text('id = "news-grasp-6-40"\nreasoning_effort = "medium"\n', encoding="utf-8")
    shadow_snapshot.parent.mkdir(parents=True)
    shadow_snapshot.write_text('id = "news-grasp-6-40"\nmodel = "gpt-5.5"\n', encoding="utf-8")

    monkeypatch.setattr(syncer, "_default_snapshot", lambda repo_root: project_snapshot)
    monkeypatch.setattr(syncer, "_default_shadow_snapshot", lambda: shadow_snapshot)

    result = syncer.sync(
        repo_root=REPO,
        template_path=template_path,
        installed_path=installed_path,
        write_snapshot=True,
        allow_custom_paths=True,
    )

    assert result["ok"] is True
    assert result["snapshot_changed"] is True
    assert {Path(item["path"]) for item in result["snapshots"]} == {
        project_snapshot,
        shadow_snapshot,
    }
    assert all(item["ok"] is True for item in result["snapshots"])
    assert tomllib.loads(project_snapshot.read_text(encoding="utf-8-sig"))["reasoning_effort"] == "max"
    assert tomllib.loads(shadow_snapshot.read_text(encoding="utf-8-sig"))["model"] == "gpt-5.6-luna"


def test_installed_direct_config_rejects_codex_app_schema_timestamp_gaps(
    tmp_path: Path,
) -> None:
    """Codex App loaderで必須のcreated_at/updated_at欠落をRedにする。"""

    api = _api()
    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    template_text = template_path.read_text(encoding="utf-8-sig")
    stale_text = "\n".join(
        line
        for line in template_text.splitlines()
        if not line.startswith("created_at =") and not line.startswith("updated_at =")
    )
    path = tmp_path / "automation.toml"
    path.write_text(
        stale_text.replace("${NEWS_GRASP_REPO_ROOT}", str(REPO.resolve())),
        encoding="utf-8",
    )

    result = _mapping(api.validate_installed_automation_semantics(path))

    assert result.get("ok") is False
    assert "automation_app_schema_created_at_invalid" in result.get("failures", [])
    assert "automation_app_schema_updated_at_invalid" in result.get("failures", [])


def test_public_template_uses_portable_cwd_placeholder_while_installed_is_bound() -> None:
    """public repoのtemplateは個人pathを持たず、installed configだけが実cwdへbindする。"""

    template_path = REPO / "automation/news-grasp-6-40/automation.toml.template"
    template_text = template_path.read_text(encoding="utf-8-sig")
    template = tomllib.loads(template_text)
    assert template.get("cwds") == ["${NEWS_GRASP_REPO_ROOT}"]
    assert "C:\\Users\\" not in template_text
    assert ("hi" + "dek") not in template_text.casefold()

    installed_path = Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"
    installed = tomllib.loads(installed_path.read_text(encoding="utf-8-sig"))
    installed_cwds = installed.get("cwds")
    assert isinstance(installed_cwds, list) and installed_cwds
    syncer = importlib.import_module("tools.sync_news_grasp_codex_automation")
    assert any(
        syncer._same_path(Path(str(item)), REPO)  # noqa: SLF001
        or syncer._same_git_repository(Path(str(item)), REPO)  # noqa: SLF001
        for item in installed_cwds
    )


def test_start_cli_uses_jst_today_when_issue_date_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """06:00 promptはangle placeholderを実行せず、runtimeがJST当日を確定できる。"""

    api = _api()
    monkeypatch.setattr(api, "_now_jst", lambda: STARTED_AT)
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(tmp_path / "direct-mainline"),
        ],
    )

    assert api._main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schemaVersion"] == "NEWS_GRASP_DIRECT_RUNTIME_V1"
    assert output["issue_date"] == ISSUE_DATE
    assert output["exact_successor"] == "title_control"


def test_start_cli_repairs_live_installed_config_once_before_run_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """live automation Red は開始前に一度だけ同期し、Green化できたら記事工程へ進む。"""

    api = _api()
    calls: list[str] = []

    def validate(path: Path | None = None) -> dict[str, Any]:
        assert path is None
        calls.append("validate")
        if calls.count("validate") == 1:
            return {
                "schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1",
                "ok": False,
                "failures": ["automation_reasoning_not_max"],
            }
        return {
            "schemaVersion": "NEWS_GRASP_DIRECT_AUTOMATION_CONFIG_V1",
            "ok": True,
            "failures": [],
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
        }

    def repair(*, cwd: Path) -> dict[str, Any]:
        calls.append("repair")
        assert cwd == REPO
        return {
            "schemaVersion": "NEWS_GRASP_CODEX_AUTOMATION_SYNC_V1",
            "ok": True,
            "changed": True,
            "app_db_changed": True,
        }

    monkeypatch.setattr(api, "validate_installed_automation_semantics", validate)
    monkeypatch.setattr(api, "_repair_installed_automation_config_once", repair)
    monkeypatch.setattr(api, "_now_jst", lambda: STARTED_AT)
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(tmp_path / "direct-mainline"),
        ],
    )

    assert api._main() == 0
    output = json.loads(capsys.readouterr().out)
    assert calls == ["validate", "repair", "validate"]
    assert output["schemaVersion"] == "NEWS_GRASP_DIRECT_RUNTIME_V1"
    assert output["exact_successor"] == "title_control"
    assert output["config_repair"]["ok"] is True
    assert "automation_config_repaired_before_stage_start" in output["post_publish_issue_list"]


def test_start_cli_forbids_installed_config_override_outside_test_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """本番startではcaller提供TOMLをlive automation authorityの代替にできない。"""

    api = _api()
    stale = tmp_path / "automation.toml"
    stale.write_text('id = "news-grasp-6-40"\nreasoning_effort = "medium"\n', encoding="utf-8")
    monkeypatch.delenv("NEWS_GRASP_DIRECT_RUNTIME_ALLOW_TEST_INSTALLED_CONFIG", raising=False)
    monkeypatch.setattr(api, "_now_jst", lambda: STARTED_AT)
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(tmp_path / "direct-mainline"),
            "--installed-config",
            str(stale),
        ],
    )

    assert api._main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "installed_config_override_forbidden"
    assert output["failures"] == ["installed_config_override_test_only"]
    assert output["exact_successor"] == "use live installed automation without --installed-config"


def test_start_cli_rejects_medium_installed_config_before_run_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """live automation が medium へ戻っていたら、記事処理に入らず exact successor を返す。"""

    api = _api()
    installed = Path.home() / ".codex" / "automations" / AUTOMATION_ID / "automation.toml"
    text = installed.read_text(encoding="utf-8-sig")
    assert 'reasoning_effort = "max"' in text
    stale = tmp_path / "automation.toml"
    stale.write_text(text.replace('reasoning_effort = "max"', 'reasoning_effort = "medium"'), encoding="utf-8")
    state_root = tmp_path / "direct-mainline"
    monkeypatch.setenv("NEWS_GRASP_DIRECT_RUNTIME_ALLOW_TEST_INSTALLED_CONFIG", "1")
    monkeypatch.setattr(api, "_now_jst", lambda: STARTED_AT)
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(state_root),
            "--installed-config",
            str(stale),
        ],
    )

    assert api._main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["schemaVersion"] == "NEWS_GRASP_DIRECT_RUNTIME_V1"
    assert output["status"] == "automation_config_red"
    assert "automation_reasoning_not_max" in output["failures"]
    assert output["exact_successor"] == (
        "python -m tools.sync_news_grasp_codex_automation --write-snapshot --write-skill --write-app-db"
    )
    assert output["post_publish_issue_list"]


def test_advance_cli_records_exact_successor_title_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """automationから呼べるCLIだけでtitle_controlをstage_historyへ記録する。"""

    api = _api()
    state_root = tmp_path / "direct-mainline"
    monkeypatch.setattr(api, "_now_jst", lambda: STARTED_AT)
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "start",
            "--state-root",
            str(state_root),
        ],
    )
    assert api._main() == 0
    started = json.loads(capsys.readouterr().out)
    evidence = json.dumps(
        {
            "ok": True,
            "status": "green",
            "issue_date": ISSUE_DATE,
            "title_status": "already_ok",
            "actual_title": EXPECTED_TITLE,
            "post_publish_issue_list": [],
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "advance",
            "--state-root",
            str(state_root),
            "--run-id",
            started["run_id"],
            "--writer-lease",
            started["writer_lease"],
            "--evidence-json",
            evidence,
        ],
    )

    assert api._main() == 0
    advanced = json.loads(capsys.readouterr().out)
    assert advanced["completed_stage"] == "title_control"
    assert advanced["current_stage"] == "issue_inventory"
    assert advanced["title_status"] == "already_ok"
    assert advanced["actual_title"] == EXPECTED_TITLE


def test_advance_cli_cannot_bypass_atomic_public_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """generic advanceはpublic verifierを呼ばずfinalize-publicへ誘導する。"""

    api = _api()
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    store, clock, _, verifier = _store(api, tmp_path)
    state_root = store.state_root
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    _complete_before(api, store, run, verifier, "public_completion", clock=clock)
    calls: list[dict[str, Any]] = []

    def public_green(**kwargs: Any) -> dict[str, Any]:
        calls.append(dict(kwargs))
        return {
            "schemaVersion": "NEWS_GRASP_DIRECT_PUBLIC_VERIFICATION_V1",
            "ok": True,
            "completion_mode": "direct_public_v1",
            "issue_date": ISSUE_DATE,
            "status": "green",
            "public_surfaces": _public_rows(),
            "failures": [],
        }

    monkeypatch.setattr(completion, "verify_direct_public_completion", public_green)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "news_grasp_direct_runtime.py",
            "advance",
            "--state-root",
            str(state_root),
            "--run-id",
            _run_id(run),
            "--writer-lease",
            _lease(run),
            "--repo-root",
            str(REPO),
            "--public-base-url",
            "https://hidepon-umg.github.io/News-Grasp",
            "--wait-sec",
            "0",
            "--poll-sec",
            "30",
        ],
    )

    assert api._main() == 1
    advanced = json.loads(capsys.readouterr().out)
    assert not calls
    assert advanced["status"] == "blocked"
    assert advanced["failures"] == ["public_completion_requires_atomic_finalizer"]


@pytest.mark.parametrize(
    "public_base_url",
    (
        "file:///tmp/news-grasp-public",
        "http://127.0.0.1:8780",
        "http://localhost:8780",
        "http://10.0.0.8",
        "https://192.168.1.8",
    ),
)
def test_public_verifier_rejects_local_private_or_file_base_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    public_base_url: str,
) -> None:
    """公開検証のURL入口はSSRF/ローカルファイル経路を許可しない。"""

    completion = importlib.import_module("tools.news_grasp_direct_completion")

    def green(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "semantic_ok": True, "status": "green"}

    monkeypatch.setattr(completion, "_required_docs", green)
    monkeypatch.setattr(completion, "_deepdive_quality", green)
    monkeypatch.setattr(completion, "_daily_quality", green)
    monkeypatch.setattr(completion, "_required_distribution", green)
    monkeypatch.setattr(completion, "_load_json", green)
    monkeypatch.setattr(completion, "_publish_status", green)
    monkeypatch.setattr(completion, "_notification", green)
    monkeypatch.setattr(completion, "_up_to_date_observation", green)
    monkeypatch.setattr(
        completion,
        "_podcast_rows",
        lambda *_args, **_kwargs: {
            name: {"ok": True, "semantic_ok": True, "status": "green"}
            for name in ("youtube_daily", "youtube_deepdive", "playlist")
        },
    )

    class _Response:
        status = 200

        def __init__(self, target: str) -> None:
            self.target = target

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self, *_args: Any) -> bytes:
            if self.target.endswith("publish-status.json"):
                return json.dumps({"date": ISSUE_DATE}).encode("utf-8")
            return f"<html>{ISSUE_DATE}</html>".encode("utf-8")

    urlopen_calls: list[str] = []

    def fake_urlopen(target: object, **_kwargs: Any) -> _Response:
        urlopen_calls.append(str(target))
        return _Response(str(target))

    monkeypatch.setattr(completion.urllib.request, "urlopen", fake_urlopen)

    try:
        result = completion.verify_direct_public_completion(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            public_base_url=public_base_url,
            wait_sec=0,
            poll_sec=30,
        )
    except (OSError, ValueError, RuntimeError) as error:
        result = {"ok": False, "failures": [str(error)]}

    assert result.get("ok") is False
    failures = " ".join(str(item) for item in result.get("failures", ()))
    assert any(
        fragment in failures.casefold()
        for fragment in ("public_base_url", "private", "internal", "loopback", "file", "localhost")
    ), result
    assert urlopen_calls == []
