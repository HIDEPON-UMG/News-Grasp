"""News-Grasp Deadman を console なしで起動する launcher。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


_CANONICAL_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: object, *, sort_keys: bool) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=sort_keys,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_stable_authority_before_import(
    *, bin_dir: Path, stable_launcher: Path
) -> dict[str, object]:
    """未検証installed codeをimportする前にstdlibだけでauthorityを確定する。"""

    authority_path = (bin_dir / "news-grasp-stable-task-authority-v1.json").resolve(strict=True)
    if authority_path.is_symlink() or authority_path.stat().st_size > 64 * 1024:
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    value = json.loads(authority_path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    unsigned = dict(value)
    observed_authority_sha256 = str(unsigned.pop("authoritySha256", ""))
    action = value.get("action")
    if (
        observed_authority_sha256
        not in {
            _json_sha256(unsigned, sort_keys=True),
            _json_sha256(unsigned, sort_keys=False),
        }
        or value.get("schemaVersion") != "STABLE_TASK_AUTHORITY_V1"
        or value.get("repoArgumentCount") != 0
        or not isinstance(action, list)
        or len(action) != 10
        or any(not isinstance(item, str) or not item for item in action)
        or Path(str(value.get("stableLauncherPath") or "")).resolve(strict=True)
        != stable_launcher
        or str(value.get("stableLauncherSha256") or "")
        != _file_sha256(stable_launcher)
        or action[1:]
        != [
            "-I",
            "-S",
            "-B",
            str(stable_launcher),
            "dispatch",
            "--schedule-id",
            "news-grasp-daily-v1",
            "--intent",
            "reconcile",
        ]
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    task_pythonw = Path(action[0]).resolve(strict=True)
    binding_path = Path(str(value.get("highCostBindingPath") or "")).resolve(strict=True)
    recovery_path = (bin_dir / "news-grasp-recovery-runtime-binding-v1.json").resolve(strict=True)
    if (
        task_pythonw.is_symlink()
        or not task_pythonw.is_file()
        or task_pythonw.name.casefold() not in {"pythonw.exe", "pythonw"}
        or binding_path != (bin_dir / "news-grasp-high-cost-binding-v1.json").resolve(strict=True)
        or binding_path.is_symlink()
        or recovery_path.is_symlink()
        or binding_path.stat().st_size > 64 * 1024
        or recovery_path.stat().st_size > 64 * 1024
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    binding = json.loads(binding_path.read_text(encoding="utf-8-sig"))
    recovery = json.loads(recovery_path.read_text(encoding="utf-8-sig"))
    receipt = str(value.get("highCostBindingReceiptSha256") or "").lower()
    if (
        re.fullmatch(r"[0-9a-f]{64}", receipt) is None
        or not isinstance(binding, dict)
        or binding.get("schemaVersion") != "NEWS_GRASP_HIGH_COST_BINDING_V1"
        or str(binding.get("bindingReceiptSha256") or "").lower() != receipt
        or not isinstance(recovery, dict)
        or recovery.get("schemaVersion") != "NEWS_GRASP_RECOVERY_RUNTIME_BINDING_V1"
        or Path(str(recovery.get("highCostBindingPath") or "")).resolve() != binding_path
        or str(recovery.get("highCostBindingReceiptSha256") or "").lower() != receipt
        or Path(str(recovery.get("taskPythonwPath") or "")).resolve() != task_pythonw
        or str(recovery.get("taskPythonwSha256") or "").lower()
        != _file_sha256(task_pythonw)
    ):
        raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
    return {
        **value,
        "authorityPath": str(authority_path),
        "authorityFileSha256": _file_sha256(authority_path),
    }


def resolve_runtime(bin_dir: Path, explicit_repo: Path | None, explicit_python: Path | None) -> tuple[Path | None, Path | None, Path | None]:
    candidate = explicit_repo
    python_candidate = explicit_python
    evidence_candidate: Path | None = None
    if candidate is None:
        config_path = bin_dir / "news-grasp-runtime-root-v1.json"
        if not config_path.is_file():
            return None
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID") from exc
        if set(config) != {"schemaVersion", "repoDir", "pythonExe", "evidenceRepoDir"} or config.get("schemaVersion") != "NEWS_GRASP_RUNTIME_ROOT_V1":
            raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID")
        candidate = Path(str(config.get("repoDir", "")))
        python_candidate = Path(str(config.get("pythonExe", "")))
        evidence_candidate = Path(str(config.get("evidenceRepoDir", "")))
    if candidate is None:
        return None, None, None
    try:
        repo_dir = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID") from exc
    if not (repo_dir / "tools" / "daily_self_heal.py").is_file():
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID")
    if python_candidate is None:
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID")
    try:
        python_exe = python_candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID") from exc
    if not python_exe.is_file():
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID")
    if evidence_candidate is None:
        evidence_candidate = repo_dir
    try:
        evidence_repo = evidence_candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID") from exc
    return repo_dir, python_exe, evidence_repo


def resolve_trusted_powershell() -> Path:
    """環境変数やPATHを使わず、Microsoft署名済みWindows PowerShellだけを返す。"""
    try:
        powershell = _CANONICAL_POWERSHELL.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("NEWS_GRASP_POWERSHELL_INVALID") from exc
    if os.path.normcase(str(powershell)) != os.path.normcase(str(_CANONICAL_POWERSHELL)):
        raise RuntimeError("NEWS_GRASP_POWERSHELL_INVALID")
    signature_command = (
        "$signature=Get-AuthenticodeSignature -LiteralPath "
        "'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' "
        "-ErrorAction Stop; "
        "if ([string]$signature.Status -cne 'Valid' -or "
        "[string]$signature.SignerCertificate.Subject -notlike '*Microsoft*') { exit 66 }"
    )
    try:
        verification = subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                signature_command,
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            ),
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("NEWS_GRASP_POWERSHELL_INVALID") from exc
    if verification.returncode != 0:
        raise RuntimeError("NEWS_GRASP_POWERSHELL_INVALID")
    return powershell


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=Path)
    parser.add_argument("--python-exe", type=Path)
    parser.add_argument("--evidence-repo-dir", type=Path)
    args = parser.parse_args()
    bin_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "bin"
    if any(
        value is not None
        for value in (args.repo_dir, args.python_exe, args.evidence_repo_dir)
    ):
        return 66
    try:
        runtime_repo, _runtime_python, _evidence_repo = resolve_runtime(
            bin_dir, None, None
        )
        if runtime_repo is None:
            raise RuntimeError("NEWS_GRASP_RUNTIME_ROOT_INVALID")
        stable_launcher = (bin_dir / "news-grasp-task-launcher.pyw").resolve(strict=True)
        authority = _load_stable_authority_before_import(
            bin_dir=bin_dir,
            stable_launcher=stable_launcher,
        )
        loader = importlib.machinery.SourceFileLoader(
            "news_grasp_installed_task_launcher_deadman",
            str(stable_launcher),
        )
        spec = importlib.util.spec_from_file_location(
            loader.name,
            stable_launcher,
            loader=loader,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
        stable_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stable_module)
        consumer_authority = stable_module._load_stable_launcher_identity(bin_dir=bin_dir)
        if consumer_authority.get("authoritySha256") != authority.get("authoritySha256"):
            raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
        stable_module._validate_active_production_generation(
            runtime_repo=runtime_repo,
            launcher_identity=authority,
        )
        action = authority.get("action")
        expected_tail = [
            "-I",
            "-S",
            "-B",
            str(stable_launcher),
            "dispatch",
            "--schedule-id",
            "news-grasp-daily-v1",
            "--intent",
            "reconcile",
        ]
        if (
            not isinstance(authority, dict)
            or authority.get("schemaVersion") != "STABLE_TASK_AUTHORITY_V1"
            or not isinstance(action, list)
            or len(action) != 10
            or not isinstance(action[0], str)
            or action[1:] != expected_tail
        ):
            raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
        task_pythonw = Path(action[0]).resolve(strict=True)
        if not task_pythonw.is_file() or not stable_launcher.is_file():
            raise RuntimeError("NEWS_GRASP_STABLE_TASK_AUTHORITY_INVALID")
        owned_module = stable_module._load_module_from_exact_path(
            runtime_repo / "tools" / "news_grasp_owned_process.py",
            prefix="news_grasp_deadman_owned_process",
        )
        spawn_owned = getattr(owned_module, "spawn_owned", None)
        if not callable(spawn_owned):
            raise RuntimeError("NEWS_GRASP_OWNED_PROCESS_RUNTIME_IMPORT_FAILED")
    except (OSError, RuntimeError, ValueError, TypeError):
        return 66
    creationflags = 0
    if sys.platform == "win32":
        creationflags |= subprocess.CREATE_NO_WINDOW

    command = list(action)
    child_environment = dict(os.environ)
    for inherited_name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
    ):
        child_environment.pop(inherited_name, None)
    child_environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    process = spawn_owned(
        command,
        cwd=str(bin_dir),
        env=child_environment,
        capture_output=False,
    )
    try:
        return process.wait(timeout=100 * 60)
    except subprocess.TimeoutExpired:
        process.close_job()
        return 124
    finally:
        process.close()


if __name__ == "__main__":
    raise SystemExit(main())
