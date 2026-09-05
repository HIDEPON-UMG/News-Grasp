from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from tools import audit_recovery_control as audit_control
from tools import daily_self_heal as dsh


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _automation_prompt() -> str:
    value = tomllib.loads(
        (REPO_ROOT / "automation/news-grasp-6-40/automation.toml.template").read_text(
            encoding="utf-8-sig"
        )
    )
    return str(value["prompt"])


def _direct_branch() -> str:
    guard = _source("automation/news-grasp-6-40/completion_guard.py")
    return guard.split("if args.direct_run_id or args.direct_state_root is not None:", 1)[
        1
    ].split("if args.direct_receipt is not None:", 1)[0]


def _launcher_source() -> str:
    return """
parser.add_argument(
    "mode",
    choices=(
        "runner",
        "bootstrap",
        "converge-runtime",
        "maintain-runtime",
        "scheduled-equivalent-nopublish",
    ),
)
script = bin_dir / "news-grasp-bootstrap.ps1"
extra = [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Runner",
] if args.mode == "runner" else [
    "-Start", "-UseProductionRuntime", "-ScheduledTaskName", "News-Grasp Bootstrap",
    "-SmokeTest", "-SkipSourceSync", "-PollSeconds", "1", "-TimeoutMinutes", "2",
    "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
]
creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
"""


def _ready_task_details(*, task_name: str, live_launcher: Path) -> dict:
    mode = "bootstrap" if task_name == "News-Grasp Bootstrap" else "runner"
    start = "05:55:00" if mode == "bootstrap" else "06:00:00"
    return {
        "ok": True,
        "state": "Ready",
        "action_summary": f'pythonw.exe "{live_launcher}" {mode}',
        "triggers": [{"enabled": True, "start_boundary": f"2026-08-13T{start}"}],
        "last_task_result": 0,
        "last_run_time": f"2026-08-13T{start}",
        "next_run_time": f"2026-08-14T{start}",
        "number_of_missed_runs": 0,
    }


def test_ng813_retired_runner_launcher_does_not_invoke_canary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """adversarial: 廃止済みrunner launcherはcanaryで正当化しない。"""
    artifact_root = tmp_path / "artifact"
    ops_root = tmp_path / "ops"
    live_bin = tmp_path / "live"
    artifact_root.mkdir()
    (ops_root / "scripts" / "ops").mkdir(parents=True)
    live_bin.mkdir()
    sources = {
        "news-grasp-runner.ps1": "function Assert-PreRunBootstrapInterlock {}\nbootstrap-self-repair-reexec\n",
        "watch-news-grasp-runner.ps1": "watcher",
        "news-grasp-bootstrap.ps1": "bootstrap",
        "news-grasp-task-launcher.pyw": _launcher_source(),
    }
    for name, source in sources.items():
        (ops_root / "scripts" / "ops" / name).write_text(source, encoding="utf-8")
        (live_bin / name).write_text(source, encoding="utf-8")
    live_launcher = live_bin / "news-grasp-task-launcher.pyw"
    def retired_launcher_details(**kwargs) -> dict:
        details = _ready_task_details(
            task_name=str(kwargs["task_name"]), live_launcher=live_launcher
        )
        if "Bootstrap" not in str(kwargs["task_name"]):
            details["action_summary"] = (
                f'pythonw.exe "{live_launcher}" scheduled-equivalent-nopublish'
            )
        return details

    monkeypatch.setattr(dsh, "_scheduled_task_details", retired_launcher_details)
    monkeypatch.setattr(
        dsh,
        "_validate_live_high_cost_binding_authority",
        lambda **_kwargs: {
            "ok": True,
            "reason": "",
            "binding_path": str(live_bin / "news-grasp-high-cost-binding-v1.json"),
            "binding_receipt_sha256": "b" * 64,
            "binding_file_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        dsh,
        "_probe_external_control_plane_readiness",
        lambda: {"status": "ready", "reasonCode": "", "modelLaunchCount": 0},
    )
    observed: dict = {}

    def fake_canary(**kwargs):
        observed.update(kwargs)
        return {"ok": True, "status": "smoke_ok"}

    monkeypatch.setattr(dsh, "_run_live_startup_canary", fake_canary)

    result = dsh.verify_live_runner_readiness(
        repo_root=artifact_root,
        ops_repo_root=ops_root,
        date="2026-08-13",
        live_runner_path=live_bin / "news-grasp-runner.ps1",
        live_watcher_path=live_bin / "watch-news-grasp-runner.ps1",
        live_bootstrap_path=live_bin / "news-grasp-bootstrap.ps1",
        live_task_launcher_path=live_launcher,
    )

    assert result["ok"] is False
    assert result["reason"] == "scheduled_task_action_not_production_start"
    assert observed == {}


def test_ng813_canary_keeps_artifacts_in_artifact_root_and_sources_runtime_from_ops_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """operational recovery: canary成果物とproduction runtime正本を引数でも分離する。"""
    artifact_root = tmp_path / "artifact"
    ops_root = tmp_path / "ops"
    live_bin = tmp_path / "live"
    for root in (artifact_root, ops_root, live_bin):
        root.mkdir()
    startup = live_bin / "news-grasp-bootstrap.ps1"
    runner = live_bin / "news-grasp-runner.ps1"
    startup.write_text("bootstrap", encoding="utf-8")
    runner.write_text("runner", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == artifact_root.resolve()
        assert "-UseProductionRuntime" in command
        assert Path(command[command.index("-RepoDir") + 1]) == ops_root.resolve()
        assert Path(command[command.index("-EvidenceRepoDir") + 1]) == ops_root.resolve()
        state_file = Path(command[command.index("-StateFile") + 1])
        log_dir = Path(command[command.index("-LogDir") + 1])
        assert state_file.is_relative_to(artifact_root.resolve())
        assert log_dir.is_relative_to(artifact_root.resolve())
        state_file.write_text(json.dumps({"status": "smoke_ok"}), encoding="utf-8")
        (log_dir / "2026-08-13.log").write_text(
            "news-grasp-runner.ps1 SMOKE OK\n", encoding="utf-8"
        )
        return Proc()

    monkeypatch.setattr(dsh.subprocess, "run", fake_run)

    result = dsh._run_live_startup_canary(
        repo_root=artifact_root,
        ops_repo_root=ops_root,
        startup_path=startup,
        live_runner_path=runner,
        date="2026-08-13",
    )

    assert result["ok"] is True


def test_ng813_smoke_inventory_probe_cannot_write_ops_bytecode() -> None:
    """adversarial: 旧runnerを復活させず、direct入口とdeadmanだけを検査する。"""
    direct_runtime = _source("tools/news_grasp_direct_runtime.py")

    assert not RUNNER.exists()
    assert "validate_installed_automation_semantics" in direct_runtime
    assert "start_run(" in direct_runtime
    assert "DIRECT_STAGES" in direct_runtime

    deadman = _source("scripts/ops/news-grasp-deadman.ps1")
    assert "& $PyExe '-I' '-S' '-B' $DailySelfHealPath" in deadman
    assert "& $PyExe '-I' '-S' '-B' $AuditControlPath" in deadman
    assert "'-m' 'tools.daily_self_heal'" not in deadman
    assert "'-m' 'tools.audit_recovery_control'" not in deadman
    assert "'ensure-0640'" in deadman
    assert "'--trigger' 'deadman_0640'" in deadman
    assert "if ((Get-Date).Hour -eq 6)" in deadman
    assert "exit (Invoke-Audit0640Control)" in deadman


def test_ng813_producer_lineage_uses_explicit_ops_root_not_state_parent(
    tmp_path: Path,
) -> None:
    """adversarial: live bin にある state の親を ops 正本として受理しない。"""
    artifact_root = tmp_path / "artifact"
    ops_root = tmp_path / "ops"
    live_bin = tmp_path / "live-bin"
    for root in (artifact_root, ops_root, live_bin):
        root.mkdir()
    expected = dsh._producer_lineage_expected(
        repo_root=artifact_root,
        ops_root=ops_root,
        date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        run_id="recovery-1",
    )
    state = {
        "date": "2026-08-13",
        "run_intent": "ScheduledRecoveryFull",
        "run_id": "recovery-1",
        **expected,
    }
    state_path = live_bin / "news-grasp-runner-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    actual = dsh._load_producer_lineage(
        repo_root=artifact_root,
        ops_root=ops_root,
        state_path=state_path,
        date="2026-08-13",
    )

    assert actual == expected


def test_ng813_publish_complete_manifest_is_typed_even_when_red(tmp_path: Path) -> None:
    """operational recovery: verifier の早期 Red も V2 schema を失わない。"""
    result = dsh.verify_publish_complete(
        repo_root=tmp_path,
        ops_repo_root=tmp_path,
        date="2026-08-13",
        remote="origin",
        branch="main",
        public_base_url="https://example.invalid/News-Grasp/",
        wait_sec=0,
        poll_sec=1,
    )

    assert result["schemaVersion"] == "NEWS_GRASP_PUBLISH_COMPLETE_V2"
    assert result["ok"] is False


def test_ng813_runner_finalizer_consumes_only_typed_top_level_publish_commit() -> None:
    """primary/adversarial: direct completion は caller receipt でGreenにしない。"""
    guard = _source("automation/news-grasp-6-40/completion_guard.py")
    direct_branch = _direct_branch()

    assert not RUNNER.exists()
    assert "verify_public_completion" in direct_branch
    assert "DirectRunStore(args.direct_state_root, create=False)" in direct_branch
    assert "direct_completion_requires_canonical_runtime_state" in guard
    assert "direct_public_v1" in guard
    assert "--runner-state" not in direct_branch
    assert "publish_commit" not in direct_branch


def test_ng813_runner_passes_real_state_file_and_ops_root() -> None:
    """primary: 単一launcherがcanonical stateを内部解決しwriter capabilityを公開しない。"""
    prompt = _automation_prompt()
    direct_branch = _direct_branch()
    launcher = _source("tools/news_grasp_direct_runtime.py")

    assert "tools.news_grasp_direct_runtime daily" in prompt
    assert "news_grasp_daily.run_daily" not in prompt
    assert "--direct-state-root" not in prompt
    assert "--direct-run-id" not in prompt
    assert "state作成・外部処理前" in prompt
    assert "_canonical_daily_state_root()" in launcher
    assert "public verifier" in prompt
    assert "ops_root = args.ops_root.resolve(strict=True)" in direct_branch
    assert "sys.path.insert(0, str(ops_root))" in direct_branch
    assert "--runner-state" not in prompt


def test_ng813_empty_broker_path_is_typed_without_literal_path_secondary_failure() -> None:
    """adversarial: direct本線は high-cost broker 不在を入口依存にしない。"""
    prompt = _automation_prompt()
    direct_runtime = _source("tools/news_grasp_direct_runtime.py")
    direct_branch = _direct_branch()

    assert "コスト起因またはハーネス起因" in prompt
    assert "記事品質・公開完成と無関係な停止判断をしない" in prompt
    assert "HighCostBindingResolverPath" not in direct_runtime
    assert "Assert-HighCostOperationAdmission" not in direct_branch


def test_ng813_public_green_contract_allows_only_finalization_critical_path() -> None:
    """operational recovery: Green 後は広域 root-cause repair へ戻らない。"""
    assert audit_control.PUBLIC_GREEN_FOLLOWUP_PRIORITY == "direct_public_closeout_only"
    assert audit_control.PUBLIC_GREEN_ALLOWED_OPERATIONS == (
        "finalizer_exact_args_replay",
        "receipt_reseal",
        "completion_guard",
        "verify_public_surface",
        "final_report",
    )


def test_ng813_bootstrap_and_watcher_propagate_explicit_ops_root_before_runner() -> None:
    """primary: launcher後のbootstrap/watch/runnerがambient探索なしでops rootを渡す。"""
    bootstrap = (
        REPO_ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    watcher = (
        REPO_ROOT / "scripts" / "ops" / "watch-news-grasp-runner.ps1"
    ).read_text(encoding="utf-8-sig")

    assert "$OpsRepoRoot = if ($EvidenceRepoDir)" in bootstrap
    assert "'-OpsRepoRoot', $OpsRepoRoot" in bootstrap
    assert bootstrap.index("news_grasp_control_plane.py") < bootstrap.rindex(
        "issue-news-grasp-audit-mission"
    )
    assert "[string] $OpsRepoRoot = ''" in watcher
    assert "OpsRepoRootOverride = $OpsRepoRoot" in watcher


def test_ng813_control_plane_repair_only_exits_before_broker_and_watcher() -> None:
    """operational recovery: bounded root repair はmodel/runnerを起動せず再検証で止まる。"""
    bootstrap = (
        REPO_ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    repair_terminal = "if ($ControlPlaneRepairOnly)"

    assert "[switch] $ControlPlaneRepairOnly" in bootstrap
    assert repair_terminal in bootstrap
    assert bootstrap.index(repair_terminal) < bootstrap.rindex(
        "$broker = if ($highCostBinding)"
    )
    assert bootstrap.index(repair_terminal) < bootstrap.index("& powershell.exe @args")


def test_ng813_recovery_preflight_runs_before_runner_spawn() -> None:
    """adversarial: authority検証後の4-root Redはrunner/modelへ到達しない。"""
    source = (REPO_ROOT / "tools" / "audit_recovery_control.py").read_text(
        encoding="utf-8-sig"
    )
    execute = source.split("def execute_audit_recovery", 1)[1].split(
        "def _audit_event_history_path", 1
    )[0]

    assert execute.index("validate_recovery_execution_manifest(") < execute.index(
        "verify_control_plane("
    )
    assert execute.index("verify_control_plane(") < execute.index("_run_bounded(")


def test_ng813_typed_finalizer_skips_global_high_cost_reentry_and_runs_guard() -> None:
    """operational recovery: direct completion は global readiness/high-cost へ戻らない。"""
    direct_branch = _direct_branch()

    assert "verify_public_completion" in direct_branch
    assert "Get-NewsGraspExternalControlPlaneReadiness" not in direct_branch
    assert "Assert-HighCostOperationAdmission" not in direct_branch
    assert "HighCostBindingResolverPath" not in direct_branch


def test_ng813_typed_finalizer_does_not_require_high_cost_broker_availability() -> None:
    """RC-06: direct closeout は high-cost broker availability を要求しない。"""

    direct_runtime = _source("tools/news_grasp_direct_runtime.py")
    direct_branch = _direct_branch()

    assert "verify_public_completion" in direct_branch
    assert "highCostBindingTool" not in direct_branch
    assert "HighCostWorkspaceRoot" not in direct_runtime


def test_ng813_typed_finalizer_requires_sealed_receipt_before_state_mutation() -> None:
    """adversarial: direct completion は runtime state/public verifier 後だけ完了化する。"""
    guard = _source("automation/news-grasp-6-40/completion_guard.py")
    runtime = _source("tools/news_grasp_direct_runtime.py")

    assert "direct_completion_requires_canonical_runtime_state" in guard
    public_gate = runtime.index('if stage_id == "public_completion" and ok:')
    public_failures = runtime.index("_public_failures(verifier_row", public_gate)
    completion_update = runtime.index('SET status = ?, current_stage_index = ?', public_failures)
    assert public_gate < public_failures < completion_update
    assert "runnerStatePath" not in _direct_branch()
    assert "lineageReceiptSha256" not in runtime


def test_ng813_typed_finalizer_preserves_scheduled_failure_provenance() -> None:
    """operational recovery: direct は公開後残課題とsurface scoped失敗をstateに残す。"""
    runtime = _source("tools/news_grasp_direct_runtime.py")

    assert "post_publish_issue_list" in runtime
    assert "surface_failures" in runtime
    assert "scheduledFailureReceiptSha256" not in runtime
    assert "PreservedScheduledFailureReceipt" not in runtime


def test_ng813_control_plane_repair_requires_one_shot_sealed_authority() -> None:
    """adversarial: repair-onlyを任意root同期APIとして直呼びできない。"""
    bootstrap = (
        REPO_ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "[string] $ControlPlaneRepairAuthorityPath = ''" in bootstrap
    assert "CONTROL_PLANE_REPAIR_AUTHORITY_REQUIRED" in bootstrap
    assert "consume-control-plane-repair" in bootstrap
    assert "CONTROL_PLANE_REPAIR_INTERPRETER_INVALID" in bootstrap
    assert "NEWS_GRASP_CONTROL_PLANE_REPAIR_CONSUMPTION_V1" not in bootstrap
    convergence_call = bootstrap.index("$RepoDir = if ($UseProductionRuntime)")
    assert bootstrap.index("consume-control-plane-repair") < convergence_call


def test_ng813_production_recovery_uses_installed_binding_not_caller_root() -> None:
    """adversarial: direct automation は App-visible installed row も同期・検証する。"""
    bootstrap = _source("scripts/ops/news-grasp-bootstrap.ps1")
    installer = _source("scripts/ops/install-news-grasp-ops.ps1")
    syncer = _source("tools/sync_news_grasp_codex_automation.py")

    assert not RUNNER.exists()
    assert "retired News-Grasp Runner is a legacy tombstone only" in bootstrap
    assert "_default_app_db" in syncer
    assert "validate_app_db_semantics" in syncer
    assert "reasoning_effort" in syncer
    assert "gpt-5.6-luna" in syncer
    assert "max" in syncer
    assert "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1" in installer
    assert "opsRepoRoot" in installer
    assert "pythonExeSha256" in installer
    assert "pythonTrustAnchor" in installer
    assert "pythonwTrustAnchor" in installer
    assert "$pythonwSignature = Get-AuthenticodeSignature -LiteralPath $TaskPythonwPath" in installer
    assert "NEWS_GRASP_RECOVERY_PYTHONW_TRUST_ANCHOR_INVALID" in installer
    assert "Get-AuthenticodeSignature" in installer
    assert "--untracked-files=all" in installer
    assert "status --porcelain --untracked-files=all" in installer
    assert "NEWS_GRASP_RECOVERY_OPS_STARTUP_CUSTOMIZATION_FORBIDDEN" in installer
    assert "NEWS_GRASP_RECOVERY_OPS_GENERATION_INVALID" in installer


def test_ng813_recovery_python_entrypoints_are_isolated_direct_scripts() -> None:
    """adversarial: direct completion は明示ops rootから trusted runtime を読む。"""
    bootstrap = _source("scripts/ops/news-grasp-bootstrap.ps1")
    direct_branch = _direct_branch()
    prompt = _automation_prompt()

    assert "& $PythonExe '-I' '-S' '-B' $RecoveryReceiptTool" in bootstrap
    assert "$controlPlaneArgs = @('-I', '-S', '-B', $controlPlaneVerifier" in bootstrap
    assert "& $PythonExe @controlPlaneArgs" in bootstrap
    assert "from tools.news_grasp_direct_runtime import" in direct_branch
    assert "DirectRunStore(args.direct_state_root, create=False)" in direct_branch
    assert "tools.news_grasp_direct_runtime daily" in prompt
    assert "news_grasp_daily.run_daily" not in prompt
    assert "static_check" in prompt
    assert "atomic_completion" in prompt
    assert "tools.news_grasp_daily_gate static_check" not in prompt
    assert "tools.news_grasp_daily_gate atomic_completion" not in prompt
    assert "python -m tools.news_grasp_direct_runtime start" not in prompt
    assert "'-P' '-m' 'tools.news_grasp_recovery_receipts'" not in bootstrap
    assert "'-P' '-m' 'tools.news_grasp_recovery_receipts'" not in direct_branch
    assert "'-P' '-m' 'tools.news_grasp_completion_guard'" not in direct_branch


@pytest.mark.parametrize(
    "relative_script",
    (
        "tools/news_grasp_recovery_receipts.py",
        "tools/news_grasp_completion_guard.py",
        "tools/daily_self_heal.py",
    ),
)
def test_ng813_isolated_recovery_entrypoints_ignore_ambient_sitecustomize(
    tmp_path: Path, relative_script: str
) -> None:
    """adversarial: CWD/PYTHONPATH上のstartup codeはtrusted toolより先に動かない。"""
    sentinel = tmp_path / "sitecustomize-executed.txt"
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(REPO_ROOT / relative_script),
            "--help",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not sentinel.exists()


def test_ng813_finalizer_guards_candidate_before_state_applied() -> None:
    """Expected Red: direct public verifier が完了state更新より前に実行される。"""
    runtime = _source("tools/news_grasp_direct_runtime.py")
    verify_function = runtime.split("def verify_public_completion(", 1)[1]
    advance_core = runtime.split("def _advance_core(", 1)[1].split(
        "def advance_stage(", 1
    )[0]

    assert "verify_direct_public_completion(" in verify_function
    public_gate = advance_core.index('if stage_id == "public_completion" and ok:')
    failure_gate = advance_core.index("_public_failures(verifier_row", public_gate)
    final_state = advance_core.index('("completed", len(DIRECT_STAGES)', failure_gate)
    assert public_gate < failure_gate < final_state


def _load_control_plane_module():
    return importlib.import_module("tools.news_grasp_control_plane")


def _write_managed_surface(root: Path, payload: str = "same") -> None:
    for relative in (
        "scripts/ops/news-grasp-task-launcher.pyw",
        "scripts/ops/news-grasp-bootstrap.ps1",
        "scripts/ops/watch-news-grasp-runner.ps1",
        "scripts/ops/news-grasp-runner.ps1",
        "scripts/ops/run_codex_with_timeout.ps1",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}:{payload}\n", encoding="utf-8")


def _copy_to_live(ops_root: Path, live_root: Path) -> None:
    live_root.mkdir(parents=True, exist_ok=True)
    for source in (ops_root / "scripts" / "ops").iterdir():
        (live_root / source.name).write_bytes(source.read_bytes())


def _control_readiness(*, ok: bool = True, reason: str = "") -> dict:
    return {
        "ok": ok,
        "reason": reason,
        "scheduled_task": {
            "ok": ok,
            "definition_ok": ok,
            "task_name": "News-Grasp Production",
            "action_summary": "pythonw.exe news-grasp-task-launcher.pyw runner --start",
        },
        "last_scheduled_attempt": {"status": "failed", "last_task_result": 1},
        "external_control": {
            "schemaVersion": "EXTERNAL_CONTROL_PLANE_READINESS_V1",
            "status": "ready",
            "reasonCode": "",
            "modelLaunchCount": 0,
            "receiptSha256": "c" * 64,
        },
        "next_run_readiness": {"ok": ok, "status": "ready" if ok else "not_ready"},
    }


def test_ng813_four_root_preflight_accepts_role_separated_matching_roots(
    tmp_path: Path,
) -> None:
    """primary: artifact/ops/runtime/live が別 root でも role と hash が一致すれば ready。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledProduction",
        runner_readiness=_control_readiness(),
        allow_isolated_high_cost_fixture=True,
    )

    assert result["schemaVersion"] == "NEWS_GRASP_CONTROL_PLANE_PREFLIGHT_V1"
    assert result["ok"] is True
    assert result["status"] == "ready"


@pytest.mark.parametrize(
    ("drift_surface", "expected_reason"),
    [
        ("runtime", "PRODUCTION_RUNTIME_DRIFT"),
        ("live", "LIVE_BIN_DRIFT"),
    ],
)
def test_ng813_four_root_preflight_rejects_drift_before_high_cost_work(
    tmp_path: Path, drift_surface: str, expected_reason: str
) -> None:
    """adversarial: runtime/live drift は model・生成・publish 前の typed Red。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    if drift_surface == "runtime":
        (runtime / "scripts" / "ops" / "news-grasp-runner.ps1").write_text(
            "drift", encoding="utf-8"
        )
    else:
        (live / "news-grasp-runner.ps1").write_text("drift", encoding="utf-8")

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=_control_readiness(),
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is False
    assert result["status"] == "not_ready"
    assert result["reasonCode"] == expected_reason


def test_ng813_four_root_preflight_recovers_after_bounded_convergence(
    tmp_path: Path,
) -> None:
    """operational recovery: 外部 converger 後の同一 pure preflight は Green へ戻る。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime, payload="drift")
    _copy_to_live(ops, live)
    first = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=_control_readiness(),
        allow_isolated_high_cost_fixture=True,
    )
    _write_managed_surface(runtime)
    second = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=_control_readiness(),
        allow_isolated_high_cost_fixture=True,
    )

    assert first["reasonCode"] == "PRODUCTION_RUNTIME_DRIFT"
    assert second["ok"] is True


def test_ng813_four_root_preflight_rejects_scheduled_task_action_drift(
    tmp_path: Path,
) -> None:
    """adversarial: managed bytes一致でもTask Action driftなら高コスト処理へ進まない。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=_control_readiness(
            ok=False, reason="scheduled_task_target_mismatch"
        ),
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is False
    assert result["reasonCode"] == "SCHEDULED_TASK_ACTION_DRIFT"
    assert result["scheduledTask"]["action_summary"]


def test_ng813_four_root_preflight_rejects_same_date_runner_state_root_drift(
    tmp_path: Path,
) -> None:
    """operational recovery: 同日stateが旧ops rootを指す場合はtyped Redにする。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    (live / "news-grasp-runner-state.json").write_text(
        json.dumps(
            {
                "date": "2026-08-13",
                "status": "publish_complete",
                "artifactRoot": str(artifact),
                "opsRoot": str(live),
            }
        ),
        encoding="utf-8",
    )

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=_control_readiness(),
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is False
    assert result["reasonCode"] == "RUNNER_STATE_ROOT_DRIFT"
    assert result["runnerState"]["opsRoot"] == str(live)


def test_ng813_four_root_preflight_honors_explicit_isolated_runner_state(
    tmp_path: Path,
) -> None:
    """primary: canaryの明示stateはlive production stateから完全分離する。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    (live / "news-grasp-runner-state.json").write_text(
        json.dumps(
            {
                "date": "2026-08-13",
                "artifactRoot": str(live),
                "opsRoot": str(live),
            }
        ),
        encoding="utf-8",
    )
    isolated_state = live / "ng-smoke-state.json"
    isolated_state.write_text(
        json.dumps({"date": "2026-08-13", "status": "smoke_ok"}),
        encoding="utf-8",
    )

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="StartupCanary",
        runner_readiness=_control_readiness(),
        runner_state_path=isolated_state,
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is True
    assert result["runnerState"]["path"] == str(isolated_state)


@pytest.mark.parametrize(
    ("run_intent", "expected_ok"),
    [
        ("StartupCanary", True),
        ("ScheduledProduction", False),
        ("ScheduledEquivalentNoPublish", False),
    ],
)
@pytest.mark.parametrize(
    "observation_reason",
    [
        "execution_receipt_missing",
        "execution_receipt_mismatch",
        "execution_receipt_stale",
        "bootstrap_last_run_issue_date_stale",
        "bootstrap_generation_timestamp_stale",
        "bootstrap_task_last_result_not_ok",
    ],
)
def test_ng813_only_startup_canary_refreshes_stale_bootstrap_observation(
    tmp_path: Path,
    run_intent: str,
    expected_ok: bool,
    observation_reason: str,
) -> None:
    """recovery: 新generationのreceipt再生成は隔離canaryだけに許可する。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    isolated_state = live / "ng-smoke-state.json"
    isolated_state.write_text(
        json.dumps({"date": "2026-08-13", "status": "smoke_ok"}),
        encoding="utf-8",
    )
    readiness = _control_readiness(ok=False, reason=observation_reason)
    readiness["scheduled_task"]["definition_ok"] = True

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent=run_intent,
        runner_readiness=readiness,
        runner_state_path=isolated_state if run_intent == "StartupCanary" else None,
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is expected_ok
    if expected_ok:
        assert result["bootstrapRefreshObservation"]["reason"] == observation_reason
        assert result["nextRunReadiness"]["status"] == (
            "ready_for_current_bootstrap_canary"
        )
    else:
        expected_reason = (
            "SCHEDULED_TASK_ACTION_DRIFT"
            if observation_reason.startswith("bootstrap_task_")
            else "NEXT_RUN_READINESS_DRIFT"
        )
        assert result["reasonCode"] == expected_reason


def test_ng813_bootstrap_preflight_uses_the_selected_isolated_state_path() -> None:
    """recovery: smoke/canaryはlive production stateを現在のbindingに読み替えない。"""
    bootstrap = (
        REPO_ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1"
    ).read_text(encoding="utf-8-sig")
    assert "$controlPlaneRunIntent = if ($SmokeTest)" in bootstrap
    assert "'StartupCanary'" in bootstrap
    assert "if ($SmokeTest) {\n        $controlPlaneArgs += @('--runner-state', $StateFile)" in bootstrap
    assert "$controlPlaneStatePath = if ($StateFile)" not in bootstrap


@pytest.mark.parametrize(
    "run_intent",
    ["ScheduledProduction", "ScheduledRecoveryFull"],
)
@pytest.mark.parametrize("state_variant", ["arbitrary", "missing", "minimal"])
def test_ng813_production_intents_reject_noncanonical_runner_state_path(
    tmp_path: Path,
    run_intent: str,
    state_variant: str,
) -> None:
    """adversarial: production/recoveryはisolated stateでroot driftを迂回できない。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    alternate_state = live / f"{state_variant}-state.json"
    if state_variant == "arbitrary":
        alternate_state.write_text(
            json.dumps(
                {
                    "date": "2026-08-13",
                    "artifactRoot": str(artifact),
                    "opsRoot": str(ops),
                }
            ),
            encoding="utf-8",
        )
    elif state_variant == "minimal":
        alternate_state.write_text(
            json.dumps({"date": "2026-08-13", "status": "smoke_ok"}),
            encoding="utf-8",
        )

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent=run_intent,
        runner_readiness=_control_readiness(),
        runner_state_path=alternate_state,
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is False
    assert result["reasonCode"] == "RUNNER_STATE_PATH_NOT_ALLOWED"


def test_ng813_preflight_preserves_failed_bootstrap_history_without_permanent_block(
    tmp_path: Path,
) -> None:
    """recovery: Task定義Greenなら過去のbootstrap失敗を成功へ書換えず前進する。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    readiness = _control_readiness(
        ok=False, reason="bootstrap_task_last_result_not_ok"
    )
    readiness["scheduled_task"]["definition_ok"] = True
    readiness["scheduled_task"]["bootstrap_last_task_result"] = 1

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-13",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=readiness,
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is True
    assert result["scheduledTask"]["bootstrap_last_task_result"] == 1
    assert result["historicalReadinessObservation"]["reason"] == (
        "bootstrap_task_last_result_not_ok"
    )


def test_ng813_recovery_admits_missed_production_run_without_rewriting_it(
    tmp_path: Path,
) -> None:
    """recovery: missed runは復旧理由であり、Task定義driftとして遮断しない。"""
    control_plane = _load_control_plane_module()
    artifact = tmp_path / "artifact"
    ops = tmp_path / "ops"
    runtime = tmp_path / "runtime"
    live = tmp_path / "live"
    artifact.mkdir()
    _write_managed_surface(ops)
    _write_managed_surface(runtime)
    _copy_to_live(ops, live)
    readiness = _control_readiness(ok=False, reason="scheduled_task_missed_runs")
    readiness["last_scheduled_attempt"] = {
        "status": "failed",
        "last_task_result": 1,
        "last_run_time": "08/13/2026 06:00:00",
    }
    readiness["scheduled_task"].update(
        {
            "definition_ok": True,
            "state": "Ready",
            "number_of_missed_runs": 1,
            "number_of_missed_runs_ok": False,
            "trigger_is_daily_0600": True,
            "next_run_time_is_0600": True,
            "runner_action_is_production_start": True,
            "targets_live_task_launcher": True,
            "task_launcher_mode_ok": True,
            "task_launcher_ready": True,
            "high_cost_binding_action_ok": True,
            "bootstrap_definition_ok": True,
            "bootstrap_last_task_result": 72,
        }
    )

    result = control_plane.verify_control_plane(
        artifact_root=artifact,
        ops_root=ops,
        production_runtime_root=runtime,
        live_bin_root=live,
        issue_date="2026-08-14",
        run_intent="ScheduledRecoveryFull",
        runner_readiness=readiness,
        allow_isolated_high_cost_fixture=True,
    )

    assert result["ok"] is True
    assert result["lastScheduledAttempt"]["status"] == "failed"
    assert result["scheduledTask"]["number_of_missed_runs"] == 1
    assert result["recoveryAdmissionObservation"]["reason"] == (
        "scheduled_task_missed_runs"
    )
