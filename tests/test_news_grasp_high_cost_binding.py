from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import news_grasp_high_cost_binding as binding


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "ops" / "news-grasp-runner.ps1"
DIRECT_RUNTIME = ROOT / "tools" / "news_grasp_direct_runtime.py"
DIRECT_SKILL = ROOT / "automation" / "skills" / "news-grasp-direct-mainline" / "SKILL.md"
AUTOMATION_TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_global_fixture(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    adapter = workspace / "tools" / "harness" / "high_cost_capability_adapter.py"
    broker_source = workspace / "tools" / "harness" / "model_spawn_broker.py"
    broker_installed = root / "bin" / "ai-model-spawn-broker.py"
    descriptor = root / "state" / "capability-v1.json"
    adapter.parent.mkdir(parents=True)
    broker_installed.parent.mkdir(parents=True)
    descriptor.parent.mkdir(parents=True)
    broker_source.write_text("# broker\n", encoding="utf-8")
    broker_installed.write_bytes(broker_source.read_bytes())
    descriptor_value = {
        "schemaVersion": "HIGH_COST_CAPABILITY_DESCRIPTOR_V1",
        "workspaceRoot": str(workspace.resolve()),
        "brokerSourcePath": str(broker_source.resolve()),
        "brokerSourceSha256": _sha256(broker_source),
        "brokerInstalledPath": str(broker_installed.resolve()),
        "brokerInstalledSha256": _sha256(broker_installed),
        "reasonSchemaVersion": "HIGH_COST_TYPED_REASON_V1",
        "generation": 7,
    }
    descriptor.write_text(
        json.dumps(descriptor_value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    adapter.write_text(
        """from __future__ import annotations
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('--descriptor', type=Path, required=True)
a=p.parse_args(); v=json.loads(a.descriptor.read_text(encoding='utf-8'))
v.update({'descriptorPath': str(a.descriptor.resolve()), 'status': 'available'})
print(json.dumps(v, ensure_ascii=False, sort_keys=True))
""",
        encoding="utf-8",
    )
    return adapter, descriptor


def test_binding_round_trip_uses_global_adapter_and_pins_identity(tmp_path: Path) -> None:
    """primary: installer生成bindingをconsumerが共通adapter経由で解決する。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    output = tmp_path / "live" / "news-grasp-high-cost-binding-v1.json"
    created = binding.create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=output,
    )

    resolved = binding.resolve_binding(
        binding_path=output,
        expected_receipt_sha256=created["bindingReceiptSha256"],
    )

    assert created["schemaVersion"] == "NEWS_GRASP_HIGH_COST_BINDING_V1"
    assert created["contractVersion"] == "HIGH_COST_CAPABILITY_DESCRIPTOR_V1"
    assert created["reasonSchemaVersion"] == "HIGH_COST_TYPED_REASON_V1"
    assert created["descriptorSha256"] == _sha256(descriptor)
    assert created["adapterSha256"] == _sha256(adapter)
    assert resolved["status"] == "available"
    assert resolved["generation"] == 7
    assert resolved["workspaceRoot"] == str((tmp_path / "workspace").resolve())


def test_descriptor_change_after_binding_is_typed_identity_drift(tmp_path: Path) -> None:
    """adversarial: bootstrap後のdescriptor差替えはidentity driftで停止する。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    output = tmp_path / "live" / "news-grasp-high-cost-binding-v1.json"
    created = binding.create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=output,
    )
    descriptor.write_text("{}\n", encoding="utf-8")

    with pytest.raises(binding.HighCostBindingError) as caught:
        binding.resolve_binding(
            binding_path=output,
            expected_receipt_sha256=created["bindingReceiptSha256"],
        )
    assert caught.value.reason == "HIGH_COST_IDENTITY_DRIFT"


def test_adapter_execution_snapshot_must_match_pinned_binding_hash(
    tmp_path: Path,
) -> None:
    """adversarial: hash確認後にadapterを同じidentity応答へ差替えても実行しない。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    output = tmp_path / "live" / "news-grasp-high-cost-binding-v1.json"
    created = binding.create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=output,
    )
    adapter.write_text(
        adapter.read_text(encoding="utf-8") + "\n# changed execution bytes\n",
        encoding="utf-8",
    )

    with pytest.raises(binding.HighCostBindingError) as caught:
        binding.resolve_binding(
            binding_path=output,
            expected_receipt_sha256=created["bindingReceiptSha256"],
        )
    assert caught.value.reason == "HIGH_COST_IDENTITY_DRIFT"


def test_adapter_probe_detects_source_change_during_snapshot_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adversarial: adapter実行中のdescriptor差替えをpost identityで拒否する。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    real_run = binding.subprocess.run

    def mutate_after_snapshot(*args: object, **kwargs: object):
        result = real_run(*args, **kwargs)
        descriptor.write_text("{}\n", encoding="utf-8")
        return result

    monkeypatch.setattr(binding.subprocess, "run", mutate_after_snapshot)
    with pytest.raises(binding.HighCostBindingError) as caught:
        binding.create_binding(
            adapter_path=adapter,
            descriptor_path=descriptor,
            output_path=tmp_path / "binding.json",
        )
    assert caught.value.reason == "HIGH_COST_IDENTITY_DRIFT"


def test_adapter_probe_does_not_inherit_unrelated_secret_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """boundary: side-effect-free probeへcredential系ambient envを渡さない。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    adapter.write_text(
        "import argparse,json,os\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--descriptor',type=Path,required=True)\n"
        "a=p.parse_args();v=json.loads(a.descriptor.read_text(encoding='utf-8'));v.update({'descriptorPath':str(a.descriptor.resolve()),'status':'available','inheritedSecret':os.environ.get('NEWS_GRASP_SECRET_SENTINEL')});print(json.dumps(v))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEWS_GRASP_SECRET_SENTINEL", "must-not-leak")
    resolved = binding._invoke_adapter(
        adapter_path=adapter, descriptor_path=descriptor
    )
    assert resolved["inheritedSecret"] is None


def test_global_adapter_cp932_output_preserves_japanese_workspace_path(
    tmp_path: Path,
) -> None:
    """operational recovery: Windows既定encodingでも日本語root identityを壊さない。"""

    adapter, descriptor = _write_global_fixture(tmp_path)
    original_workspace = tmp_path / "workspace"
    japanese_workspace = tmp_path / "ドキュメント"
    original_workspace.rename(japanese_workspace)
    value = json.loads(descriptor.read_text(encoding="utf-8"))
    broker_source = japanese_workspace / "tools" / "harness" / "model_spawn_broker.py"
    value.update(
        workspaceRoot=str(japanese_workspace.resolve()),
        brokerSourcePath=str(broker_source.resolve()),
        brokerSourceSha256=_sha256(broker_source),
    )
    descriptor.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    adapter = japanese_workspace / "tools" / "harness" / adapter.name
    adapter.write_text(
        "import argparse,json,sys\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--descriptor',type=Path,required=True)\n"
        "a=p.parse_args();v=json.loads(a.descriptor.read_text(encoding='utf-8'));v.update({'descriptorPath':str(a.descriptor.resolve()),'status':'available'});sys.stdout.buffer.write((json.dumps(v,ensure_ascii=False)+'\\n').encode('cp932'))\n",
        encoding="utf-8",
    )
    output = tmp_path / "binding.json"
    created = binding.create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=output,
    )
    assert created["workspaceRoot"] == str(japanese_workspace.resolve())


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HIGH_COST_WORKSPACE_BINDING_MISSING", "HIGH_COST_WORKSPACE_BINDING_MISSING"),
        ("HIGH_COST_BROKER_UNAVAILABLE", "HIGH_COST_BROKER_UNAVAILABLE"),
        ("HIGH_COST_OPERATION_ADMISSION_REQUIRED", "HIGH_COST_OPERATION_ADMISSION_REQUIRED"),
    ],
)
def test_binding_preserves_global_typed_reason_classes(message: str, expected: str) -> None:
    """operational recovery: binding/broker/admission欠落を同一reasonへ潰さない。"""

    assert binding.classify_reason(message) == expected


def test_production_chain_propagates_binding_without_ambient_workspace_root() -> None:
    """primary: launcher→bootstrap→watcher→runnerがpath/hashを明示伝播する。"""
    if not RUNNER.exists():
        direct = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
        skill = DIRECT_SKILL.read_text(encoding="utf-8-sig")
        template = AUTOMATION_TEMPLATE.read_text(encoding="utf-8-sig")
        assert "exact_successor" in direct
        assert "surface_scoped" in direct
        assert "slo_debt_continue_public" in direct
        assert "cost/ledger/binding failureは該当model operationだけをzero-call Red" in skill
        assert "実行可能な public-critical successor がある限り継続" in template
        assert "NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT" not in direct
        return

    launcher = (ROOT / "scripts" / "ops" / "news-grasp-task-launcher.pyw").read_text(
        encoding="utf-8-sig"
    )
    bootstrap = (ROOT / "scripts" / "ops" / "news-grasp-bootstrap.ps1").read_text(
        encoding="utf-8-sig"
    )
    watcher = (ROOT / "scripts" / "ops" / "watch-news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )
    runner = RUNNER.read_text(encoding="utf-8-sig")

    assert "--high-cost-binding-path" in launcher
    assert "--high-cost-binding-sha256" in launcher
    assert "-HighCostBindingPath" in launcher
    assert "-HighCostBindingReceiptSha256" in launcher
    for source in (bootstrap, watcher, runner):
        assert "HighCostBindingPath" in source
        assert "HighCostBindingReceiptSha256" in source
    assert "NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT" not in runner
    assert "model_spawn_broker.py') -PathType Leaf" not in runner


def test_installer_probes_global_adapter_and_pins_task_action_binding() -> None:
    """recovery: install時にprobe/resolve成功後だけbindingとTask Actionを確定する。"""

    installer = (ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "high_cost_capability_adapter.py" in installer
    assert "news_grasp_high_cost_binding.py" in installer
    assert "NEWS_GRASP_HIGH_COST_BINDING_V1" in installer
    assert "highCostBindingResolverDestination" in installer
    assert "--high-cost-binding-path" in installer
    assert "--high-cost-binding-sha256" in installer
    assert installer.index("news_grasp_high_cost_binding.py") < installer.index(
        "Write-NewsGraspInstallJournal -Phase 'files_installed'"
    )


def test_model_consumers_resolve_binding_instead_of_ambient_workspace_root() -> None:
    """adversarial: Python clientとmodel wrapperも同じbinding consumerを使う。"""

    harness_init = (ROOT / "tools" / "harness" / "__init__.py").read_text(
        encoding="utf-8"
    )
    client = (ROOT / "tools" / "model_spawn_client.py").read_text(encoding="utf-8")
    wrapper = (ROOT / "scripts" / "ops" / "run_codex_with_timeout.ps1").read_text(
        encoding="utf-8-sig"
    )
    for source in (harness_init, client):
        assert "resolve_binding_from_environment" in source
        assert "NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT" not in source
        assert "NEWS_GRASP_HIGH_COST_TEST_WORKSPACE_ROOT" not in source
    assert "news_grasp_high_cost_binding.py" in wrapper
    assert "HighCostBindingPath" in wrapper
    assert "HighCostBindingReceiptSha256" in wrapper
    assert "HighCostBindingResolverSha256" in wrapper
    assert "$derivedResolver" in wrapper
    assert "& $HighCostPythonExe '-I' '-S' '-B' $derivedResolver" in wrapper
    if not RUNNER.exists():
        direct = DIRECT_RUNTIME.read_text(encoding="utf-8-sig")
        assert "exact_successor" in direct
        assert "surface_scoped" in direct
        assert "NEWS_GRASP_HIGH_COST_WORKSPACE_ROOT" not in direct
        assert "detail=$bindingJson" not in wrapper
        return
    runner = RUNNER.read_text(encoding="utf-8-sig")
    assert "detail=$bindingJson" not in wrapper
    assert "detail=$highCostBindingJson" not in runner
    assert "$bindingFailure.reason" in wrapper
    assert "$highCostBindingFailure.reason" in runner


def test_control_plane_validates_live_binding_before_managed_runtime_files(
    tmp_path: Path,
) -> None:
    """operational recovery: 4-root preflightはbinding identityも高コスト処理前に検証する。"""

    from tools import news_grasp_control_plane

    adapter, descriptor = _write_global_fixture(tmp_path)
    roots = {
        name: tmp_path / name
        for name in ("artifact", "ops", "runtime", "live")
    }
    for root in roots.values():
        root.mkdir()
    output = roots["live"] / "news-grasp-high-cost-binding-v1.json"
    created = binding.create_binding(
        adapter_path=adapter,
        descriptor_path=descriptor,
        output_path=output,
    )

    result = news_grasp_control_plane.verify_control_plane(
        artifact_root=roots["artifact"],
        ops_root=roots["ops"],
        production_runtime_root=roots["runtime"],
        live_bin_root=roots["live"],
        issue_date="2026-08-13",
        run_intent="ScheduledProduction",
        high_cost_binding_path=output,
        high_cost_binding_receipt_sha256=created["bindingReceiptSha256"],
    )

    assert result["reasonCode"] == "OPS_MANAGED_FILE_MISSING"
    assert result["globalBinding"]["status"] == "available"
    assert result["globalBinding"]["authorityReplicated"] is False


def test_control_plane_rejects_missing_binding_before_managed_runtime_files(
    tmp_path: Path,
) -> None:
    """adversarial: production preflightはbinding欠落を最上流reasonにする。"""

    from tools import news_grasp_control_plane

    roots = {
        name: tmp_path / name
        for name in ("artifact", "ops", "runtime", "live")
    }
    for root in roots.values():
        root.mkdir()
    result = news_grasp_control_plane.verify_control_plane(
        artifact_root=roots["artifact"],
        ops_root=roots["ops"],
        production_runtime_root=roots["runtime"],
        live_bin_root=roots["live"],
        issue_date="2026-08-13",
        run_intent="ScheduledProduction",
    )
    assert result["reasonCode"] == "HIGH_COST_WORKSPACE_BINDING_MISSING"


def test_recovery_controllers_pass_binding_instead_of_legacy_roots() -> None:
    """adversarial: scheduled recovery consumerも旧root/tool引数へ戻らない。"""

    audit = (ROOT / "tools" / "audit_recovery_control.py").read_text(
        encoding="utf-8"
    )
    daily = (ROOT / "tools" / "news_grasp_daily_control.py").read_text(
        encoding="utf-8"
    )
    nopublish = (
        ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
    ).read_text(encoding="utf-8-sig")
    for source in (audit, daily):
        assert '"-HighCostBindingPath"' in source
        assert '"-HighCostBindingReceiptSha256"' in source
        assert '"-HighCostWorkspaceRoot"' not in source
        assert '"-HighCostBudgetToolPath"' not in source
    runner_arguments = nopublish.split("$runnerArguments = @(", 1)[1].split(")", 1)[0]
    assert "'-HighCostBindingPath'" in runner_arguments
    assert "'-HighCostBindingReceiptSha256'" in runner_arguments
    assert "'-HighCostWorkspaceRoot'" not in runner_arguments
    assert "'-HighCostBudgetToolPath'" not in runner_arguments
