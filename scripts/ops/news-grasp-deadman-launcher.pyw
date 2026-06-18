"""News-Grasp Deadman を console なしで起動する launcher。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    bin_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "bin"
    script = bin_dir / "news-grasp-deadman.ps1"
    powershell = os.environ.get("NEWS_GRASP_POWERSHELL", "powershell.exe")
    creationflags = 0
    if sys.platform == "win32":
        creationflags |= subprocess.CREATE_NO_WINDOW

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=str(bin_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
