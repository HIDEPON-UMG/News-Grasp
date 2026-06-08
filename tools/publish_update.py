#!/usr/bin/env python3
"""手動公開時に git push と任意の Web Push 通知を実行する。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], *, dry_run: bool = False) -> int:
    print("+ " + " ".join(args))
    if dry_run:
        return 0
    completed = subprocess.run(args, cwd=ROOT)
    return completed.returncode


def publish(*, dry_run: bool = False, remote: str = "origin", branch: str = "main",
            notify: bool = False,
            notify_dry_run: bool = False) -> int:
    """git push を実行し、notify 指定時だけ send_push.py を続けて実行する。"""
    rc = _run(["git", "push", remote, branch], dry_run=dry_run)
    if rc != 0:
        print(f"ERROR: git push failed (rc={rc}); Web Push 通知は送信しません", file=sys.stderr)
        return rc

    if not notify and not notify_dry_run:
        print("Web Push 通知は送信しません（通知する場合は --notify を付けてください）")
        return 0

    notify_cmd = [sys.executable, str(ROOT / "tools" / "send_push.py")]
    if notify_dry_run:
        notify_cmd.append("--dry-run")
    rc = _run(notify_cmd, dry_run=dry_run)
    if rc != 0:
        print(f"ERROR: send_push.py failed (rc={rc})", file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="手動公開時に git push し、必要な時だけ Web Push 通知します。",
    )
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true",
                        help="git push / send_push を実行せず、実行予定コマンドだけ表示します。")
    parser.add_argument("--notify", action="store_true",
                        help="git push 成功後に Web Push 通知を送信します。微細修正では付けないでください。")
    parser.add_argument("--notify-dry-run", action="store_true",
                        help="git push は実行し、通知は send_push.py --dry-run にします。")
    args = parser.parse_args(argv)
    return publish(
        dry_run=args.dry_run,
        remote=args.remote,
        branch=args.branch,
        notify=args.notify,
        notify_dry_run=args.notify_dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
