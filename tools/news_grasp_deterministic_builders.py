"""既存artifactから再生成できるNews-Grasp決定論builder。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


class NewsGraspBuilderError(RuntimeError):
    """builder入力の不足・不整合。"""


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _require(source: Mapping[str, Any], *keys: str) -> None:
    if any(key not in source for key in keys):
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")


def build_summary_audio_script(summary: Mapping[str, Any]) -> dict[str, Any]:
    _require(summary, "issueDate", "title", "sections")
    sections = summary["sections"]
    if not isinstance(sections, list) or not all(isinstance(item, str) and item.strip() for item in sections):
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")
    script = "\n".join([f"本日のNews-Grasp、{summary['title']}。", *sections])
    return {"schemaVersion": "SUMMARY_AUDIO_SCRIPT_V1", "issueDate": summary["issueDate"], "text": script, "sourceHash": _hash(summary)}


def build_deepdive_dialogue(article: Mapping[str, Any]) -> dict[str, Any]:
    _require(article, "issueDate", "title", "body", "provenanceHash")
    if not isinstance(article["body"], str) or not article["body"].strip():
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")
    dialogue = [
        {"speaker": "編集者", "text": article["title"]},
        {"speaker": "解説者", "text": article["body"]},
    ]
    return {"schemaVersion": "DEEPDIVE_DIALOGUE_V1", "issueDate": article["issueDate"], "turns": dialogue, "provenanceHash": article["provenanceHash"]}


def build_distribution_manifest(artifacts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    required = {"summary", "deepdive", "audio"}
    if set(artifacts) != required or any(not artifacts[key].get("hash") for key in required):
        raise NewsGraspBuilderError("NG_BUILDER_BUNDLE_INCOMPLETE")
    return {"schemaVersion": "DISTRIBUTION_MANIFEST_V1", "artifacts": {key: artifacts[key]["hash"] for key in sorted(artifacts)}}


def build_public_republish(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    _require(checkpoint, "issueDate", "artifactKey", "outputHash", "oracleId")
    return {
        "schemaVersion": "CHECKPOINT_PUBLIC_REPUBLISH_V1",
        "issueDate": checkpoint["issueDate"],
        "artifactKey": checkpoint["artifactKey"],
        "outputHash": checkpoint["outputHash"],
        "oracleId": checkpoint["oracleId"],
        "modelCalls": 0,
        "sourceWriteCount": 0,
        "publishMutation": False,
    }
