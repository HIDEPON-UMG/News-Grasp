from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "ops" / "install-news-grasp-ops.ps1"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_production_has_only_the_0600_trigger_and_ignorenew() -> None:
    source = _installer_text()

    assert "$canonicalProductionTriggers = @('T06:00:00')" in source
    assert "$canonicalProductionTriggers = @('T06:00:00', 'T06:40:00')" not in source
    assert "$runnerTrigger = New-ScheduledTaskTrigger -Daily -At 6:00am" in source
    assert "$auditTrigger = New-ScheduledTaskTrigger -Daily -At 6:40am" not in source
    assert (
        "$runnerSettings = New-ScheduledTaskSettingsSet "
        "-StartWhenAvailable -MultipleInstances IgnoreNew"
    ) in source


def test_deadman_owns_0640_hourly_recovery_and_is_enabled() -> None:
    source = _installer_text()

    assert "$deadmanTrigger = New-ScheduledTaskTrigger -Daily -At 6:40am" in source
    assert "Interval = 'PT1H'" in source
    assert "Duration = 'P1D'" in source
    assert (
        "Register-ScheduledTask -TaskPath '\\' -TaskName $DeadmanTaskName "
        "-Action $deadmanAction -Trigger $deadmanTrigger"
    ) in source
    assert "Enable-ScheduledTask -TaskPath '\\' -TaskName $DeadmanTaskName -ErrorAction Stop" in source
    assert "Disable-ScheduledTask -TaskName $DeadmanTaskName -ErrorAction Stop" not in source


def test_installed_state_checks_all_five_managed_tasks_and_three_role_specs() -> None:
    source = _installer_text()

    expected_block = source.split("$expected = @(", 1)[1].split("    )", 1)[0]
    task_specs = [line for line in expected_block.splitlines() if "[ordered]@{" in line]

    # The three enabled role definitions are checked as shape-complete specs;
    # Pull and legacy Runner are managed tombstones but must still be part of
    # the preimage/rollback name set below.
    assert len(task_specs) == 3
    for spec in task_specs:
        assert "taskPath = '\\'" in spec, spec
        assert "starts =" in spec, spec
        assert "policy = 'IgnoreNew'" in spec, spec
        assert "interval =" in spec, spec
        assert "duration =" in spec, spec
    assert any("name = $RunnerTaskName" in spec for spec in task_specs)
    assert any("name = $BootstrapTaskName" in spec for spec in task_specs)
    assert any("name = $DeadmanTaskName" in spec for spec in task_specs)

    managed_names = (
        "$RunnerTaskName, $BootstrapTaskName, $DeadmanTaskName, "
        "$PullTaskName, $LegacyRunnerTaskName"
    )
    assert f"$managedTaskNames = @({managed_names})" in source
    assert f"foreach ($taskName in @({managed_names}))" in source
    assert "-ExpectedTaskNames $managedTaskNames" in source
    assert "Disable-ScheduledTask -TaskPath '\\' -TaskName $disabledTaskName -ErrorAction Stop" in source
    assert "foreach ($disabledTaskName in @($PullTaskName, $LegacyRunnerTaskName))" in source
    assert "legacy task state invalid" in source


def test_installer_rejects_noncanonical_task_names_before_mutation() -> None:
    source = _installer_text()
    guard = source.index("NEWS_GRASP_TASK_NAME_AUTHORITY_INVALID")
    first_mutation = source.index("New-Item -ItemType Directory -Force -Path $BinDir")

    assert guard < first_mutation
    for expected in (
        "News-Grasp Production",
        "News-Grasp Bootstrap",
        "News-Grasp Deadman",
        "News-Grasp Pull",
        "News-Grasp Runner",
    ):
        assert expected in source[:first_mutation]


def test_deadman_launcher_uses_only_canonical_signed_powershell() -> None:
    launcher = (
        ROOT / "scripts" / "ops" / "news-grasp-deadman-launcher.pyw"
    ).read_text(encoding="utf-8")

    # The launcher may retain a typed error signature containing this name;
    # only ambient environment selection is forbidden.
    assert re.search(r"(?:os\.environ(?:\.get)?|os\.getenv).*NEWS_GRASP_POWERSHELL", launcher) is None
    assert r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" in launcher
    assert "Get-AuthenticodeSignature" in launcher
    assert "timeout=15" in launcher
