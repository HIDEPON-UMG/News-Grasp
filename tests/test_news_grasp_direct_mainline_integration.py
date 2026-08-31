"""06:00 direct mainline のcanonical runtime state integration test。

旧来のcaller生成receiptはcompletion authorityにしない。公開可能性は、一時
state rootへ保存されたDirectRunStoreの21工程と、検証器が所有する観測rowを
実dispatcherで収束させた結果だけから判定する。外部配信は実行しない。
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tools.news_grasp_completion_guard import evaluate_direct_public


ISSUE_DATE = "2026-08-30"
AUTOMATION_ID = "news-grasp-6-40"
EXPECTED_TITLE = "26/08/30 News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"
JST = timezone(timedelta(hours=9), name="Asia/Tokyo")
STARTED_AT = datetime(2026, 8, 30, 6, 0, tzinfo=JST)
STAGES = [
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
]
SURFACES = (
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
    def __init__(self, value: datetime = STARTED_AT) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def move(self, **delta: int) -> None:
        self.value += timedelta(**delta)


class _Verifier:
    """caller payloadを読まず、fixture所有の観測rowを返すsemantic verifier。"""

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


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    for name in ("as_dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            projected = method()
            if isinstance(projected, Mapping):
                return projected
    if hasattr(value, "__dict__"):
        projected = vars(value)
        if isinstance(projected, Mapping):
            return projected
    raise AssertionError(f"runtime result is not inspectable: {value!r}")


def _runtime_api():
    try:
        from tools import news_grasp_direct_runtime as runtime
    except ModuleNotFoundError as error:
        pytest.fail(f"RED_DIRECT_RUNTIME_PRODUCER_MISSING:{error}")
    required = (
        "DIRECT_STAGES",
        "DirectRunStore",
        "start_run",
        "run_exact_successor",
        "inspect_run",
        "verify_public_completion",
    )
    missing = [name for name in required if not hasattr(runtime, name)]
    assert not missing, f"RED_DIRECT_RUNTIME_API_MISSING:{','.join(missing)}"
    return runtime


def _store(
    runtime: Any,
    tmp_path: Path,
    *,
    lease_seconds: int = 60,
) -> tuple[Any, _Clock, _Verifier, Path]:
    clock = _Clock()
    verifier = _Verifier()
    state_root = tmp_path / "日本語-direct-integration-state"
    state_root.mkdir(parents=True, exist_ok=True)
    store = runtime.DirectRunStore(
        state_root,
        clock=clock,
        host_generation=lambda: 1,
        lease_ttl=timedelta(seconds=lease_seconds),
        semantic_verifier=verifier,
    )
    return store, clock, verifier, state_root


def _start(runtime: Any, store: Any, cwd: Path) -> Mapping[str, Any]:
    return _mapping(
        runtime.start_run(
            store,
            automation_id=AUTOMATION_ID,
            cwd=str(cwd),
            issue_date=ISSUE_DATE,
        )
    )


def _run_id(run: Mapping[str, Any]) -> str:
    value = run.get("run_id", run.get("runId"))
    assert str(value or "").strip()
    return str(value)


def _lease(run: Mapping[str, Any]) -> str:
    value = run.get("writer_lease", run.get("writerLease", run.get("lease")))
    assert str(value or "").strip()
    return str(value)


def _dispatch(
    runtime: Any,
    store: Any,
    run: Mapping[str, Any],
    verifier: _Verifier,
    *,
    clock: _Clock,
    caller_ok: bool = True,
    requested_stage_id: str | None = None,
    caller_surface: Mapping[str, Any] | None = None,
    writer_lease: str | None = None,
) -> Mapping[str, Any]:
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
    return _mapping(runtime.run_exact_successor(store, **kwargs))


def _inspect(runtime: Any, store: Any, run: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(runtime.inspect_run(store, run_id=_run_id(run)))


def _green(verifier: _Verifier, stage_id: str, **row: Any) -> None:
    verifier.observe(stage_id, ok=True, status="green", **row)


def _set_title_green(verifier: _Verifier, *, unavailable: bool = False) -> None:
    if unavailable:
        verifier.observe(
            "title_control",
            ok=True,
            status="green",
            title_status="unavailable",
            actual_title="",
            post_publish_issue_list=["title: host action unavailable"],
        )
    else:
        _green(
            verifier,
            "title_control",
            title_status="already_ok",
            actual_title=EXPECTED_TITLE,
            post_publish_issue_list=[],
        )


def _complete_before(
    runtime: Any,
    store: Any,
    run: Mapping[str, Any],
    verifier: _Verifier,
    stage_id: str,
    *,
    clock: _Clock,
) -> None:
    assert stage_id in STAGES
    for current in STAGES[: STAGES.index(stage_id)]:
        if current == "title_control":
            _set_title_green(verifier)
        else:
            _green(verifier, current)
        _dispatch(runtime, store, run, verifier, clock=clock)


def _public_rows(issue_date: str = ISSUE_DATE) -> dict[str, dict[str, Any]]:
    return {
        surface: {
            "status": "green",
            "issue_date": issue_date,
            "public_evidence": f"fixture/{surface}.json",
            "semantic_ok": True,
        }
        for surface in SURFACES
    }


def _set_public_green(verifier: _Verifier) -> None:
    verifier.observe(
        "public_completion",
        ok=True,
        status="green",
        issue_date=ISSUE_DATE,
        public_surfaces=_public_rows(),
        completion_mode="direct_public_v1",
    )


def _forged_receipt() -> dict[str, Any]:
    """旧direct receiptに見えるがcanonical runtime stateへ束縛されていないJSON。"""

    return {
        "schemaVersion": "NEWS_GRASP_DIRECT_MAINLINE_RECEIPT_V1",
        "completion_mode": "direct_public_v1",
        "issue_date": ISSUE_DATE,
        "automation_id": AUTOMATION_ID,
        "cwd": "C:/workspace/News-Grasp",
        "run_intent": "ScheduledProductionDirect",
        "scheduled_inventory": {
            "scheduled_category_ids": ["AI", "Business"],
            "generated_category_ids": ["AI", "Business"],
        },
        "title": {
            "title_status": "already_ok",
            "expected_title": EXPECTED_TITLE,
            "actual_title": EXPECTED_TITLE,
            "publication_blocked": False,
        },
        "post_publish_issue_list": [],
        "stage_history": [
            {"stage": stage, "completed_at": f"2026-08-30T06:{index:02d}:00+09:00"}
            for index, stage in enumerate(STAGES)
        ],
        "quality_gate": {
            "ok": True,
            "issue_date": ISSUE_DATE,
            "command": "python -m tools.validate_daily_quality --date 2026-08-30 --require-deepdive --json",
        },
        "deepdive_quality": {
            "ok": True,
            "issue_date": ISSUE_DATE,
            "rendered_public": True,
            "provenance_valid": True,
            "dialogue_valid": True,
        },
        "public_surfaces": _public_rows(),
        "ok": True,
    }


def test_self_report_direct_receipt_is_red_without_canonical_runtime_state() -> None:
    """caller-createdreceiptとevaluate_direct_publicだけではGreenにしない。"""

    assert len(STAGES) == 21
    result = evaluate_direct_public(_forged_receipt(), ISSUE_DATE)
    assert result["ok"] is False
    failures = [str(item) for item in result.get("failures", ())]
    assert (
        "direct_completion_requires_canonical_runtime_state" in failures
        or any("canonical" in item.casefold() for item in failures)
    )


def test_canonical_runtime_reaches_public_green_via_real_dispatcher(tmp_path: Path) -> None:
    """全21工程をrun_exact_successorで進め、runtime verifierだけでGreenにする。"""

    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    for stage_id in STAGES:
        if stage_id == "title_control":
            _set_title_green(verifier)
        elif stage_id == "public_completion":
            _set_public_green(verifier)
        else:
            _green(verifier, stage_id)
        result = _dispatch(
            runtime,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "claim": "caller-only"},
        )
        assert result.get("stage", result.get("completed_stage", stage_id)) == stage_id
    completion = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    )
    assert completion.get("ok") is True
    assert completion.get("completion_mode") == "direct_public_v1"
    assert verifier.calls[: len(STAGES)] == STAGES
    assert verifier.calls[len(STAGES) :] == ["public_completion"]


def test_title_failure_is_nonblocking_only_in_canonical_state(tmp_path: Path) -> None:
    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    _set_title_green(verifier, unavailable=True)
    result = _dispatch(runtime, store, run, verifier, clock=clock)
    assert result.get("next_stage", result.get("successor")) == "issue_inventory"
    state = _inspect(runtime, store, run)
    assert state.get("current_stage") == "issue_inventory"
    issues = state.get("post_publish_issue_list", state.get("postPublishIssueList", []))
    assert any("title" in str(item).casefold() for item in issues)


def test_quality_gate_red_stays_at_same_stage_and_uses_exact_successor(
    tmp_path: Path,
) -> None:
    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    _complete_before(runtime, store, run, verifier, "daily_quality", clock=clock)
    verifier.observe(
        "daily_quality",
        ok=False,
        status="red",
        artifact_surface="daily_quality",
    )
    try:
        red = _dispatch(
            runtime,
            store,
            run,
            verifier,
            clock=clock,
            caller_ok=True,
            caller_surface={"ok": True, "quality": "green"},
        )
    except (RuntimeError, ValueError):
        red = {}
    state = _inspect(runtime, store, run)
    assert state.get("current_stage") == "daily_quality"
    assert state.get("exact_successor", state.get("next_stage", "daily_quality")) == "daily_quality"
    assert red.get("next_stage", red.get("successor", "daily_quality")) == "daily_quality"
    _green(verifier, "daily_quality", artifact_surface="daily_quality-repaired")
    result = _dispatch(runtime, store, run, verifier, clock=clock)
    assert result.get("next_stage", result.get("successor")) == "youtube_podcasts"


def test_youtube_quota_failure_is_surface_scoped_and_public_successor_continues(
    tmp_path: Path,
) -> None:
    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    _complete_before(runtime, store, run, verifier, "youtube_podcasts", clock=clock)
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
    result = _dispatch(
        runtime,
        store,
        run,
        verifier,
        clock=clock,
        caller_ok=True,
        caller_surface={"ok": True, "surface": "youtube_daily"},
    )
    assert result.get("next_stage", result.get("successor")) == "playlist"
    assert _inspect(runtime, store, run).get("current_stage") == "playlist"
    _green(verifier, "playlist")
    _dispatch(runtime, store, run, verifier, clock=clock)
    completion = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    )
    assert completion.get("ok") is False
    assert any("youtube" in str(item).casefold() for item in completion.get("failures", ()))


@pytest.mark.parametrize(
    ("elapsed", "target_met", "optional_frozen", "slo_met", "slo_debt"),
    [
        (45, True, False, True, False),
        (75, False, True, True, False),
        (90, False, True, True, False),
        (91, False, True, False, True),
    ],
)
def test_virtual_clock_keeps_public_successor_at_45_75_90_minutes(
    tmp_path: Path,
    elapsed: int,
    target_met: bool,
    optional_frozen: bool,
    slo_met: bool,
    slo_debt: bool,
) -> None:
    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    _set_title_green(verifier, unavailable=True)
    _dispatch(runtime, store, run, verifier, clock=clock)
    clock.move(minutes=elapsed)
    _green(verifier, "issue_inventory")
    result = _dispatch(runtime, store, run, verifier, clock=clock)
    assert result.get("next_stage", result.get("successor")) == "category_collection"
    state = _inspect(runtime, store, run)
    assert state.get("current_stage") == "category_collection"
    slo = state.get("slo", {})
    assert slo.get("continue_public_successors") is True
    assert slo.get("target_met") is target_met
    assert slo.get("optional_high_cost_frozen") is optional_frozen
    assert slo.get("slo_met") is slo_met
    assert slo.get("slo_debt", state.get("slo_debt")) is slo_debt


def test_publish_status_only_is_not_canonical_public_completion(tmp_path: Path) -> None:
    runtime = _runtime_api()
    store, _, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        failures=["public_surface_unobserved"],
    )
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
    result = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            completion_receipt=forged,
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False


def test_url_200_only_is_not_web_semantic_completion(tmp_path: Path) -> None:
    runtime = _runtime_api()
    store, _, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    web_only = {"status": "reachable", "http_status": 200, "semantic_ok": False}
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        public_surfaces={"web": web_only},
        failures=["web_semantics_unverified"],
    )
    forged = {
        "completion_mode": "direct_public_v1",
        "issue_date": ISSUE_DATE,
        "public_surfaces": {"web": web_only},
        "ok": True,
    }
    result = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            completion_receipt=forged,
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False
    assert any("web" in str(item).casefold() for item in result.get("failures", ()))


def test_inventory_and_public_surface_rows_are_conjunctive(tmp_path: Path) -> None:
    runtime = _runtime_api()
    store, clock, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    _complete_before(runtime, store, run, verifier, "public_completion", clock=clock)
    verifier.observe(
        "public_completion",
        ok=False,
        status="red",
        inventory_categories_match=False,
        public_surfaces={"web": {"status": "green", "semantic_ok": True}},
        failures=["scheduled_inventory_mismatch", "public_surface_unobserved"],
    )
    try:
        _dispatch(runtime, store, run, verifier, clock=clock, caller_ok=True)
    except (RuntimeError, ValueError):
        pass
    state = _inspect(runtime, store, run)
    assert state.get("current_stage") == "public_completion"
    result = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False
    failures = " ".join(str(item) for item in result.get("failures", ()))
    assert "inventory" in failures.casefold()
    assert "public" in failures.casefold()


def test_forged_completion_json_is_rejected_by_canonical_public_verifier(
    tmp_path: Path,
) -> None:
    runtime = _runtime_api()
    store, _, verifier, _ = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    verifier.observe(
        "public_completion",
        ok=False,
        status="unobserved",
        failures=["canonical_runtime_frontier_incomplete"],
    )
    forged = _forged_receipt()
    forged["ok"] = True
    result = _mapping(
        runtime.verify_public_completion(
            store,
            run_id=_run_id(run),
            completion_receipt=forged,
            semantic_verifier=verifier,
        )
    )
    assert result.get("ok") is False
    assert any(
        fragment in " ".join(str(item) for item in result.get("failures", ())).casefold()
        for fragment in ("canonical", "frontier", "runtime", "stage")
    )


def test_automation_and_skill_bind_direct_contract() -> None:
    root = Path(__file__).parents[1]
    prompt = (root / "automation/news-grasp-6-40/automation.toml.template").read_text(
        encoding="utf-8"
    )
    skill = (root / "automation/skills/news-grasp-direct-mainline/SKILL.md").read_text(
        encoding="utf-8"
    )
    combined = prompt + "\n" + skill
    for fragment in (
        "news-grasp-direct-mainline",
        "YY/MM/DD",
        "TT26/",
        "45",
        "90",
        "scheduled_category_ids",
        "--require-deepdive",
        "public-only",
        "post_publish_issue_list",
        "title_status",
        "title_completion",
        "materializer receipt",
        "最初の実行操作は `python -m tools.news_grasp_title_materializer --verify-only --repo-root .`",
    ):
        assert fragment in combined
    assert "news_grasp_runner.py" not in prompt
    assert "news_grasp_nopublish.py" not in prompt


def test_installed_completion_guard_rejects_self_report_receipt(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    installed = (
        Path.home()
        / ".codex"
        / "automations"
        / "news-grasp-6-40"
        / "completion_guard.py"
    )
    assert installed.is_file()
    receipt_path = tmp_path / "forged-direct-receipt.json"
    receipt_path.write_text(json.dumps(_forged_receipt(), ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(installed),
            "--issue-date",
            ISSUE_DATE,
            "--direct-receipt",
            str(receipt_path),
            "--ops-root",
            str(root),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result.get("ok") is False
    failures = " ".join(str(item) for item in result.get("failures", ()))
    assert "canonical" in failures.casefold() or "runtime" in failures.casefold()


def test_installed_guard_does_not_execute_forged_repo_root_validator(
    tmp_path: Path,
) -> None:
    """receiptのrepo_rootを攻撃者側へ向けても、そのvalidatorを起動しない。"""

    root = Path(__file__).parents[1]
    installed = (
        Path.home()
        / ".codex"
        / "automations"
        / "news-grasp-6-40"
        / "completion_guard.py"
    )
    assert installed.is_file()

    runtime = _runtime_api()
    store, _, verifier, state_root = _store(runtime, tmp_path)
    canonical_cwd = tmp_path / "canonical-repo"
    canonical_cwd.mkdir()
    run = _start(runtime, store, canonical_cwd)

    attacker_repo = tmp_path / "attacker-repo"
    attacker_tools = attacker_repo / "tools"
    attacker_tools.mkdir(parents=True)
    (attacker_tools / "__init__.py").write_text("", encoding="utf-8")
    marker = attacker_repo / "attacker-validator-ran.txt"
    (attacker_tools / "validate_daily_quality.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )

    receipt = _forged_receipt()
    receipt.update(
        {
            "state_root": str(state_root),
            "run_id": _run_id(run),
            "repo_root": str(attacker_repo),
            "public_base_url": attacker_repo.as_uri(),
        }
    )
    receipt_path = tmp_path / "forged-repo-root-receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(installed),
            "--issue-date",
            ISSUE_DATE,
            "--direct-receipt",
            str(receipt_path),
            "--ops-root",
            str(root),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    assert not marker.exists(), (
        "forged repo_root caused an attacker-controlled validate_daily_quality "
        "module to execute"
    )
    result = json.loads(completed.stdout)
    assert result.get("ok") is False


def test_installed_guard_does_not_create_missing_state_root_on_read_only_verify(
    tmp_path: Path,
) -> None:
    """read-only direct verificationは任意の不存在state rootをmaterializeしない。"""

    root = Path(__file__).parents[1]
    installed = (
        Path.home()
        / ".codex"
        / "automations"
        / "news-grasp-6-40"
        / "completion_guard.py"
    )
    assert installed.is_file()
    missing_root = tmp_path / "missing-arbitrary-state-root"
    assert not missing_root.exists()

    completed = subprocess.run(
        [
            sys.executable,
            str(installed),
            "--issue-date",
            ISSUE_DATE,
            "--direct-run-id",
            "arbitrary-missing-run",
            "--direct-state-root",
            str(missing_root),
            "--ops-root",
            str(root),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )

    assert completed.returncode != 0
    assert not missing_root.exists()
    assert not (missing_root / "direct-mainline.sqlite3").exists()
    result = json.loads(completed.stdout)
    assert result.get("ok") is False


def test_installed_completion_guard_accepts_canonical_direct_state(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    installed = (
        Path.home()
        / ".codex"
        / "automations"
        / "news-grasp-6-40"
        / "completion_guard.py"
    )
    assert installed.is_file()
    runtime = _runtime_api()
    store, clock, verifier, state_root = _store(runtime, tmp_path)
    cwd = tmp_path / "repo"
    cwd.mkdir()
    run = _start(runtime, store, cwd)
    for stage_id in STAGES:
        if stage_id == "title_control":
            _set_title_green(verifier)
        elif stage_id == "public_completion":
            _set_public_green(verifier)
        else:
            _green(verifier, stage_id)
        _dispatch(runtime, store, run, verifier, clock=clock)
    completed = subprocess.run(
        [
            sys.executable,
            str(installed),
            "--issue-date",
            ISSUE_DATE,
            "--direct-state-root",
            str(state_root),
            "--direct-run-id",
            _run_id(run),
            "--ops-root",
            str(root),
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout
    result = json.loads(completed.stdout)
    assert result.get("ok") is True
    assert result.get("completion_mode") == "direct_public_v1"
