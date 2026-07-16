from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("runner", "bootstrap"))
    parser.add_argument("--probe", type=Path)
    args = parser.parse_args()
    if args.probe:
        args.probe.parent.mkdir(parents=True, exist_ok=True)
        args.probe.write_text("probe_ok", encoding="utf-8")
        return 0
    bin_dir = Path.home() / "bin"
    script = bin_dir / ("news-grasp-bootstrap.ps1" if args.mode == "runner" else "news-grasp-bootstrap.ps1")
    extra = ["-Start"] if args.mode == "runner" else [
        "-Start", "-SmokeTest", "-PollSeconds", "1", "-TimeoutMinutes", "2",
        "-StateFile", "ng-smoke-state.json", "-LogDir", "ng-smoke-logs",
    ]
    powershell = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if not script.is_file():
        return 66
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = bin_dir / "news-grasp-task-launcher.log"
    with log.open("a", encoding="utf-8", errors="replace") as stream:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), *extra],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            check=False,
        )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
