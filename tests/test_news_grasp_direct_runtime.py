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


def test_public_completion_reverifies_public_surface_when_base_url_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """completed state内のpublic_stageだけを実公開検証の代替にしない。"""

    api = _api()
    store, clock, _host_generation, verifier = _store(api, tmp_path)
    cwd = tmp_path / "News-Grasp"
    cwd.mkdir()
    run = _start(api, store, cwd)
    for stage_id in EXPECTED_STAGES:
        if stage_id == "title_control":
            _set_green(
                verifier,
                stage_id,
                title_status="already_ok",
                actual_title=EXPECTED_TITLE,
                post_publish_issue_list=[],
            )
        elif stage_id == "public_completion":
            _set_public_green(verifier)
        else:
            _set_green(verifier, stage_id)
        _dispatch(api, store, run, verifier, clock=clock)

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


def test_dispatcher_completes_normal_run_without_manual_stage_mutation(tmp_path: Path) -> None:
    """通常fixtureはdispatcher/executor経由で全21工程を順に完了する。"""

    api = _api()
    store, clock, _, verifier = _store(api, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(api, store, cwd)
    for stage_id in EXPECTED_STAGES:
        if stage_id == "title_control":
            _set_green(
                verifier,
                stage_id,
                title_status="already_ok",
                actual_title=EXPECTED_TITLE,
                post_publish_issue_list=[],
            )
        elif stage_id == "public_completion":
            _set_public_green(verifier)
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
    state = _inspect(api, store, run)
    assert state.get("status", "").casefold() in {"complete", "completed", "green"}
    assert verifier.calls == list(EXPECTED_STAGES)


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
    try:
        red = _dispatch(
            api,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "public_status": "complete"},
        )
    except (RuntimeError, ValueError):
        red = {}
    assert _inspect(api, store, run).get("current_stage") == "public_completion"
    assert red.get("status", "").casefold() not in {"complete", "completed", "green"}
    assert _mapping(
        api.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    ).get("ok") is False

    _set_public_green(verifier)
    green = _dispatch(api, store, run, verifier, clock=clock)
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

    # scanner自身はcontent identityを生成せず、field名の検査だけを行う。
    forbidden_parts = (
        "s" + "ha" + "256",
        "ha" + "sh",
        "di" + "gest",
        "finger" + "print",
        "mer" + "kle",
        "content_" + "address",
        "content-" + "address",
        "content_" + "identity",
        "publish_" + "commit",
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
    cwds = value.get("cwds")
    assert isinstance(cwds, list) and cwds
    resolved_cwds = {str(Path(str(item)).expanduser().resolve()) for item in cwds}
    assert str(REPO.resolve()) in resolved_cwds
    prompt = str(value.get("prompt", ""))
    assert "$news-grasp-direct-mainline" in prompt
    assert "YY/MM/DD" in prompt
    assert "title_status" in prompt
    assert "post_publish_issue_list" in prompt


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
    assert str(REPO.resolve()) in {
        str(Path(str(item)).expanduser().resolve()) for item in installed_cwds
    }


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
            poll_sec=0,
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
