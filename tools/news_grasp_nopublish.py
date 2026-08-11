"""scheduled-equivalent NoPublish E2Eの副作用境界。"""

from __future__ import annotations

from typing import Any, Mapping


class NewsGraspNoPublishError(RuntimeError):
    """NoPublish契約違反。"""


def validate_result(result: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("executionMode") != "scheduled-equivalent-nopublish":
        raise NewsGraspNoPublishError("NG_NOPUBLISH_MODE_INVALID")
    if result.get("publishCount", 0) != 0 or result.get("pushCount", 0) != 0:
        raise NewsGraspNoPublishError("NG_NOPUBLISH_SIDE_EFFECT")
    if result.get("uploadCount", 0) != 0 or result.get("notificationCount", 0) != 0:
        raise NewsGraspNoPublishError("NG_NOPUBLISH_SIDE_EFFECT")
    return {
        "status": "green" if result.get("status") == "green" else "red",
        "attemptFrozen": bool(result.get("failed") and result.get("resumeForbidden")),
        "sideEffects": {
            "publish": int(result.get("publishCount", 0) or 0),
            "push": int(result.get("pushCount", 0) or 0),
            "upload": int(result.get("uploadCount", 0) or 0),
            "notification": int(result.get("notificationCount", 0) or 0),
        },
    }


def execute_no_publish(result: Mapping[str, Any]) -> dict[str, Any]:
    """副作用を実行せず、事前に検証した結果だけを受け付ける。"""
    return validate_result(result)
