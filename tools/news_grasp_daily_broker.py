"""Luna向けの単一typed Daily実行入口。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any


BROKER_SCHEMA = "NEWS_GRASP_DAILY_BROKER_RESULT_V1"


class DailyBrokerError(RuntimeError):
    """broker requestまたはruntime resultの契約違反。"""


def run_daily(
    arguments: Mapping[str, Any],
    *,
    runtime_runner: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """空requestを一度だけ既存direct runtimeへ渡す。"""

    if not isinstance(arguments, Mapping) or dict(arguments):
        raise DailyBrokerError("NEWS_GRASP_DAILY_BROKER_ARGUMENTS_MUST_BE_EMPTY")
    if runtime_runner is None:
        from tools.news_grasp_direct_runtime import _run_daily_cli

        runtime_runner = _run_daily_cli
    result = runtime_runner()
    if not isinstance(result, Mapping):
        raise DailyBrokerError("NEWS_GRASP_DAILY_BROKER_RUNTIME_RESULT_INVALID")
    runtime = dict(result)
    return {
        "schemaVersion": BROKER_SCHEMA,
        "ok": runtime.get("ok") is True and runtime.get("status") == "completed",
        "status": str(runtime.get("status") or "red"),
        "run_id": str(runtime.get("run_id") or ""),
        "runtime": runtime,
        "humanImpact": {
            "noFocusTheft": True,
            "noAutoOpen": True,
            "noUserMonitoring": True,
        },
    }


def _main(argv: Sequence[str] | None = None) -> int:
    if list(argv or ()):
        raise DailyBrokerError("NEWS_GRASP_DAILY_BROKER_ARGUMENTS_MUST_BE_EMPTY")
    result = run_daily({})
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] is True else 1


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))


__all__ = ["BROKER_SCHEMA", "DailyBrokerError", "run_daily"]
