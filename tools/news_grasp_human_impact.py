"""News-Grasp専用handlerのHumanImpact境界。"""

from __future__ import annotations

from typing import Any, Mapping


class HumanImpactContractError(RuntimeError):
    """人間影響契約違反。"""


def validate_human_impact(intent: Mapping[str, Any]) -> dict[str, Any]:
    required = {"noFocusTheft", "noAutoOpen", "noUserMonitoring", "ownedProcessOnly"}
    if not required.issubset(intent) or any(intent.get(key) is not True for key in required):
        raise HumanImpactContractError("NG_HUMAN_IMPACT_CONTRACT_INVALID")
    if intent.get("rawProcessKill") is True or intent.get("sharedProcessTermination") is True:
        raise HumanImpactContractError("NG_RAW_PROCESS_TERMINATION_FORBIDDEN")
    return {"status": "green", "noFocusTheft": True, "noAutoOpen": True, "noUserMonitoring": True, "ownedProcessOnly": True}


validate = validate_human_impact
