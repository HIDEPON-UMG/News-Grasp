from __future__ import annotations

import random
from pathlib import Path

from tools.audit_powershell_scriptblock_scope import audit_paths


ROOT = Path(__file__).resolve().parent.parent
OPS_SCRIPTS = sorted((ROOT / "scripts" / "ops").glob("*.ps1"))


def _write_fixture(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_ops_scripts_have_no_scriptblock_parameter_scope_collisions():
    findings = audit_paths(OPS_SCRIPTS)
    assert findings == []


def test_classify_capture_path_collision_is_detected(tmp_path: Path):
    fixture = _write_fixture(
        tmp_path / "runner-classify-bug.ps1",
        """
function Invoke-LoggedCapture {
  param([scriptblock] $Block, [string] $CapturePath)
  & $Block
}
$capturePath = 'gate-output.json'
$classifyPath = 'classify-output.json'
Invoke-LoggedCapture -CapturePath $classifyPath -Block {
  & $PyExe '-m' 'tools.auto_repair_orchestrator' 'classify' '--output-file' $capturePath
}
""",
    )
    findings = audit_paths([fixture])
    assert [(f["wrapper"], f["variable"]) for f in findings] == [
        ("Invoke-LoggedCapture", "capturePath")
    ]


def test_safe_alias_before_scriptblock_is_not_reported(tmp_path: Path):
    fixture = _write_fixture(
        tmp_path / "runner-classify-fixed.ps1",
        """
function Invoke-LoggedCapture {
  param([scriptblock] $Block, [string] $CapturePath)
  & $Block
}
$capturePath = 'gate-output.json'
$classifyPath = 'classify-output.json'
$gateCapturePathForClassify = $capturePath
Invoke-LoggedCapture -CapturePath $classifyPath -Block {
  & $PyExe '-m' 'tools.auto_repair_orchestrator' 'classify' '--output-file' $gateCapturePathForClassify
}
""",
    )
    assert audit_paths([fixture]) == []


def test_case_insensitive_scope_collision_is_detected(tmp_path: Path):
    fixture = _write_fixture(
        tmp_path / "runner-case-bug.ps1",
        """
function Invoke-WithState {
  param([string] $StatePath, [scriptblock] $Block)
  & $Block
}
$STATEPATH = 'outer-state.json'
Invoke-WithState -StatePath 'inner-state.json' -Block {
  Get-Content -LiteralPath $STATEPATH
}
""",
    )
    findings = audit_paths([fixture])
    assert [(f["wrapper"], f["variable"]) for f in findings] == [
        ("Invoke-WithState", "STATEPATH")
    ]


def test_fixed_seed_monkey_injected_wrapper_collisions_are_detected(tmp_path: Path):
    rng = random.Random(20260627)
    names = ["CapturePath", "StatePath", "LogPath", "ManifestPath", "OutputFile"]
    rng.shuffle(names)
    chunks: list[str] = []
    expected: list[tuple[str, str]] = []

    for index, param_name in enumerate(names):
        wrapper = f"Invoke-Monkey{index}"
        outer_var = param_name[0].lower() + param_name[1:]
        chunks.append(
            f"""
function {wrapper} {{
  param([string] ${param_name}, [scriptblock] $Block)
  & $Block
}}
${outer_var} = 'outer-{index}.json'
{wrapper} -{param_name} 'inner-{index}.json' -Block {{
  Write-Output ${outer_var}
}}
"""
        )
        expected.append((wrapper, outer_var))

    fixture = _write_fixture(tmp_path / "runner-monkey-bugs.ps1", "\n".join(chunks))
    findings = audit_paths([fixture])
    actual = [(f["wrapper"], f["variable"]) for f in findings]
    assert actual == expected
