"""News-Grasp Deadman を console なしで起動する launcher。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


_CANONICAL_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)


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
    script = bin_dir / "news-grasp-deadman.ps1"
    try:
        repo_dir, python_exe, evidence_repo = resolve_runtime(bin_dir, args.repo_dir, args.python_exe)
        powershell = resolve_trusted_powershell()
        if args.evidence_repo_dir is not None:
            evidence_repo = args.evidence_repo_dir.resolve(strict=True)
    except RuntimeError:
        return 66
    creationflags = 0
    if sys.platform == "win32":
        creationflags |= subprocess.CREATE_NO_WINDOW

    command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    if repo_dir is not None:
        command.extend(["-RepoDir", str(repo_dir)])
        command.extend(["-PythonExe", str(python_exe)])
        command.extend(["-EvidenceRepoDir", str(evidence_repo)])
    process = subprocess.Popen(
            command,
            cwd=str(bin_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    try:
        return process.wait(timeout=100 * 60)
    except subprocess.TimeoutExpired:
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            trusted_taskkill = taskkill.resolve(strict=True)
            if os.path.normcase(str(trusted_taskkill)) != os.path.normcase(str(taskkill)):
                return 125
            subprocess.run(
                [str(trusted_taskkill), "/PID", str(process.pid), "/T", "/F"],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                timeout=30,
                check=False,
            )
            process.wait(timeout=30)
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
