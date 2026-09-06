"""検証済み保存成果を別の公開runへ採用する。生成・公開副作用は持たない。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import news_grasp_daily_content as content
from tools import news_grasp_direct_runtime as runtime
from tools.publish_inventory import CATEGORY_PATHS
from tools.news_grasp_repair_registry import build_daily_artifact_dag


@dataclass(frozen=True)
class ArtifactSource:
    root: Path
    run_id: str
    issue_date: str
    checkpoint_bytes: bytes


_ADOPTION_AUTHORITY = object()


def _authorized_adoption_context(source: ArtifactSource) -> dict[str, Any]:
    return {
        "artifact_source": source,
        "_artifact_adoption_authority": _ADOPTION_AUTHORITY,
    }


def _validate_adoption_context(context: Mapping[str, Any] | None) -> None:
    if context is None or "artifact_source" not in context:
        return
    if context.get("_artifact_adoption_authority") is not _ADOPTION_AUTHORITY:
        raise ValueError("artifact_adoption_authority_required")


def capture_artifact_source(*, repo_root: Path, database: Path, run_id: str, issue_date: str) -> ArtifactSource:
    """完了runのcheckpointを一つの読取りtransactionから固定bytesへ取り込む。"""
    if content._has_reparse_ancestor(repo_root) or content._has_reparse_ancestor(database):
        raise ValueError("adoption_source_reparse")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        run = db.execute("SELECT status,issue_date,cwd FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["status"] != "completed" or run["issue_date"] != issue_date:
            raise ValueError("adoption_source_not_completed")
        if Path(run["cwd"]).resolve() != repo_root.resolve():
            raise ValueError("adoption_source_root_mismatch")
        rows = db.execute("SELECT * FROM daily_artifact_checkpoints WHERE run_id=?", (run_id,)).fetchall()
        checkpoints = {str(row["artifact_id"]): runtime.DailyArtifactLedger._checkpoint_projection(row) for row in rows}
    return ArtifactSource(repo_root, run_id, issue_date, runtime._json_dump(checkpoints).encode("utf-8"))


def _read_source_file(root: Path, relative: str, expected_hash: str) -> bytes:
    raw_path = root / relative
    if content._has_reparse_ancestor(raw_path):
        raise ValueError("adoption_source_reparse")
    path = content._safe_path(root, relative)
    before = path.stat()
    limit = 512 * 1024 * 1024
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
        raise ValueError("adoption_source_file_size")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if (
        identity(before) != identity(opened) or identity(opened) != identity(after)
        or len(raw) != opened.st_size or len(raw) > limit
        or content._has_reparse_ancestor(raw_path)
        or hashlib.sha256(raw).hexdigest() != expected_hash
    ):
        raise ValueError("adoption_source_file_hash")
    return raw


def _owned_artifact_paths(issue_date: str, categories: Sequence[str]) -> dict[str, str]:
    """採用対象deterministic artifactの所有pathを固定する。"""
    paths: dict[str, str] = {}
    for category in categories:
        try:
            genre = CATEGORY_PATHS[category]["digest_folder"]
        except KeyError as exc:
            raise ValueError("adoption_category_unknown") from exc
        paths.update(
            {
                f"reporter_records:{category}": f"tmp/newsroom/{issue_date}/{category}.records.jsonl",
                f"search_audit:{category}": f"data/search_audit/{issue_date}/{category}.json",
                f"digest:{category}": f"digest/{genre}/{issue_date}-{genre}.md",
            }
        )
    paths.update(
        {
            "summary": f"digest/Summary/{issue_date}.md",
            "deepdive_article": f"digest/DeepDive/{issue_date}-DeepDive.md",
            "deepdive_dialogue": f"digest/DeepDive/{issue_date}-DeepDive-dialogue.md",
            "daily_audio_script": f"digest/Summary/{issue_date}-audio-script.md",
            "daily_audio": f"build/tts/{issue_date}.mp3",
            "deepdive_audio": f"build/tts/deepdive/{issue_date}.mp3",
            "daily_video": f"build/youtube-podcast/{issue_date}.mp4",
            "deepdive_video": f"build/youtube-podcast-deepdive/{issue_date}.mp4",
        }
    )
    return paths


def _capture_quality_evidence(
    root: Path,
    issue_date: str,
    payload: Mapping[str, Any],
) -> dict[str, bytes]:
    """Green監査済みのDeepDive証拠を保存用の正規2pathへ束縛する。"""
    from tools import deepdive_quality as quality

    try:
        observation = quality.audit_issue(
            repo_root=root,
            issue_date=issue_date,
            include_corpus=False,
            require_rendered_public=False,
            route="production_generation",
        )
    except Exception as exc:  # noqa: BLE001 - adoptionは不確実な監査を拒否する。
        raise ValueError("adoption_quality_audit_invalid") from exc
    if (
        not isinstance(observation, Mapping)
        or observation.get("status") != "Green"
        or observation.get("issues") != []
        or observation.get("issueCodes") != []
    ):
        raise ValueError("adoption_quality_audit_not_green")

    relative_paths = {
        "article": f"digest/DeepDive/{issue_date}-DeepDive.md",
        "dialogue": f"digest/DeepDive/{issue_date}-DeepDive-dialogue.md",
        "provenance": f"data/deepdive-provenance/{issue_date}.json",
        "quality_review": f"data/deepdive-quality-review/{issue_date}.json",
    }
    try:
        paths = {
            name: content._safe_path(root, relative)
            for name, relative in relative_paths.items()
        }
    except Exception as exc:  # noqa: BLE001 - invalid adoption path is typed below.
        raise ValueError("adoption_quality_evidence_path_invalid") from exc

    audited_files = observation.get("auditedFiles")
    if not isinstance(audited_files, list):
        raise ValueError("adoption_quality_audit_evidence_invalid")

    def read_audited(name: str) -> bytes:
        path = paths[name]
        expected_path = str(path.resolve())
        matches = [
            item
            for item in audited_files
            if isinstance(item, Mapping) and item.get("path") == expected_path
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("sha256"), str):
            raise ValueError("adoption_quality_audit_artifact_missing")
        raw = content._read_bounded_model_events(path)
        if raw is None:
            raise ValueError("adoption_quality_evidence_file_invalid")
        if hashlib.sha256(raw).hexdigest().casefold() != matches[0]["sha256"].casefold():
            raise ValueError("adoption_quality_audit_artifact_drift")
        return raw

    article_bytes = read_audited("article")
    provenance_bytes = read_audited("provenance")
    quality_review_bytes = read_audited("quality_review")
    dialogue_bytes = content._read_bounded_model_events(paths["dialogue"])
    if dialogue_bytes is None:
        raise ValueError("adoption_quality_evidence_file_invalid")

    try:
        quality_review = json.loads(quality_review_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("adoption_quality_review_invalid") from exc
    if not isinstance(quality_review, Mapping) or quality_review.get("issueDate") != issue_date:
        raise ValueError("adoption_quality_review_invalid")
    review_artifacts = quality_review.get("artifacts")
    review_dialogue = review_artifacts.get("dialogue") if isinstance(review_artifacts, Mapping) else None
    expected_dialogue_path = relative_paths["dialogue"]
    if (
        not isinstance(review_dialogue, Mapping)
        or set(review_dialogue) != {"path", "sha256"}
        or review_dialogue.get("path") != expected_dialogue_path
        or not isinstance(review_dialogue.get("sha256"), str)
        or review_dialogue["sha256"].casefold() != hashlib.sha256(dialogue_bytes).hexdigest().casefold()
    ):
        raise ValueError("adoption_quality_dialogue_binding_invalid")

    if not isinstance(payload, Mapping):
        raise ValueError("adoption_quality_payload_invalid")
    article_markdown = payload.get("article_markdown")
    dialogue_markdown = payload.get("dialogue_markdown")
    if (
        not isinstance(article_markdown, str)
        or not isinstance(dialogue_markdown, str)
        or article_bytes != article_markdown.encode("utf-8")
        or dialogue_bytes != dialogue_markdown.encode("utf-8")
    ):
        raise ValueError("adoption_quality_payload_drift")

    return {
        relative_paths["provenance"]: provenance_bytes,
        relative_paths["quality_review"]: quality_review_bytes,
    }


def adopt_artifact_source(source: ArtifactSource, *, repo_root: Path, ledger: runtime.DailyArtifactLedger,
                          categories: Sequence[str]) -> dict[str, Any]:
    """既存validatorと実file hashを確認し、未作成checkpointだけを採用する。"""
    if source.issue_date != ledger.issue_date:
        raise ValueError("adoption_issue_date_mismatch")
    if content._has_reparse_ancestor(source.root) or content._has_reparse_ancestor(repo_root):
        raise ValueError("adoption_source_reparse")
    if len(source.checkpoint_bytes) > 32 * 1024 * 1024:
        raise ValueError("adoption_checkpoint_size")
    saved = json.loads(source.checkpoint_bytes)
    if not isinstance(saved, dict):
        raise ValueError("adoption_checkpoint_shape")
    dag = build_daily_artifact_dag(categories)
    normalized_categories = tuple(str(item).strip() for item in categories)
    owned_paths = _owned_artifact_paths(source.issue_date, normalized_categories)
    model_artifacts = {
        *(f"candidate:{category}" for category in normalized_categories),
        *(f"reporter:{category}" for category in normalized_categories),
        "editor",
        "deepdive_model",
    }
    known_ids = set(dag) | {"content_completion"}
    unknown_ids = set(saved) - known_ids
    if unknown_ids:
        raise ValueError("adoption_artifact_owner_unknown")
    excluded = {
        "articles_jsonl", "site_html", "deepdive_html", "daily_audio_projection", "deepdive_audio_projection",
        "content_completion", "distribution_manifest", "publish_status",
    }
    selected = {
        key: row for key, row in saved.items()
        if key in dag and key not in excluded and dag[key]["producerKind"] != "external"
    }
    if set(selected) - model_artifacts - set(owned_paths):
        raise ValueError("adoption_artifact_owner_unknown")
    required = list(model_artifacts)
    if any(key not in selected for key in required):
        raise ValueError("adoption_model_checkpoint_missing")
    files: dict[str, bytes] = {}
    for key, row in selected.items():
        if (not isinstance(row, dict) or row.get("status") != "Green"
            or not isinstance(row.get("payload"), dict)
            or hashlib.sha256(runtime._json_dump(row["payload"]).encode()).hexdigest() != row.get("outputHash")):
            raise ValueError("adoption_checkpoint_hash")
        payload = row["payload"]
        if key in model_artifacts:
            if "artifactHashes" in payload:
                raise ValueError("adoption_model_artifact_hashes_forbidden")
            continue
        owned = payload.get("artifactHashes")
        expected_relative = owned_paths.get(key)
        if expected_relative is None or not isinstance(owned, Mapping) or set(owned) != {expected_relative}:
            raise ValueError("adoption_artifact_owned_paths_mismatch")
        expected_hash = owned[expected_relative]
        if not isinstance(expected_hash, str):
            raise ValueError("adoption_artifact_hash_invalid")
        if expected_relative in files:
            raise ValueError("adoption_duplicate_owner_path")
        files[expected_relative] = _read_source_file(source.root, expected_relative, expected_hash)
    payloads = {key: row["payload"] for key, row in selected.items()}
    reporters = []
    for category in categories:
        candidate = payloads[f"candidate:{category}"]
        content._validate_candidate_payload(category=category, issue_date=source.issue_date,
                                            candidates=candidate.get("candidates"), audit=candidate.get("search_audit"))
        reporter = content._validate_reporter(payloads[f"reporter:{category}"], category=category,
                                             issue_date=source.issue_date, search_audit=candidate["search_audit"])
        if reporter != payloads[f"reporter:{category}"]:
            raise ValueError("adoption_reporter_normalization_drift")
        reporters.append(reporter)
    with tempfile.TemporaryDirectory(prefix="ng-adoption-preview-") as preview:
        content._validate_editor(payloads["editor"], issue_date=source.issue_date, reporters=reporters, preview_dir=Path(preview), repo_root=source.root)
    deep = content._validate_deepdive(
        payloads["deepdive_model"], issue_date=source.issue_date,
        allowed_urls={str(record[key]).rstrip("/") for record in payloads["editor"]["append_records"]
                      for key in ("url", "thumb") if str(record.get(key) or "").startswith(("http://", "https://"))},
    )
    if deep != payloads["deepdive_model"]:
        raise ValueError("adoption_deepdive_normalization_drift")
    quality_evidence: dict[str, bytes] = {}
    deepdive_materializer_ids = {"deepdive_article", "deepdive_dialogue"}
    if deepdive_materializer_ids.intersection(selected):
        quality_evidence = _capture_quality_evidence(
            source.root,
            source.issue_date,
            deep,
        )
    current = ledger.list_checkpoints()
    writes = {}
    outputs = {}
    for key, node in dag.items():
        if key not in selected or key in current:
            continue
        dependencies = node["dependsOn"]
        if any(dep not in current or dep not in selected or current[dep].get("status") != "Green"
               or current[dep]["outputHash"] != selected[dep]["outputHash"] for dep in dependencies):
            continue
        if key.startswith("candidate:"):
            inputs = {"issueDate": source.issue_date, "runId": ledger.run_id, "category": key.split(":", 1)[1]}
        elif key.startswith("reporter:"):
            inputs = {"issueDate": source.issue_date, "candidateOutputHash": current[dependencies[0]]["outputHash"], "repairFailureSignature": None}
        elif key == "editor":
            inputs = {"issueDate": source.issue_date, "reporterOutputHashes": [current[f"reporter:{c}"]["outputHash"] for c in categories], "repairFailureSignature": None}
        elif key == "deepdive_model":
            inputs = {"issueDate": source.issue_date, "editorOutputHash": current["editor"]["outputHash"], "repairFailureSignature": None}
        elif key.startswith(("reporter_records:", "search_audit:", "digest:")) or key in {"summary", "deepdive_article", "deepdive_dialogue"}:
            inputs = {"artifactId": key, "dependencyOutputHash": current[dependencies[0]]["outputHash"]}
        else:
            inputs = {"artifactId": key, "dependencyOutputHashes": {dep: current[dep]["outputHash"] for dep in dependencies}}
        writes[key] = (content._artifact_input_hash(inputs), payloads[key])
        current[key] = selected[key]
        relative = owned_paths.get(key)
        if relative is not None:
            outputs[relative] = files[relative]
    adopted_deepdive = any(
        key in writes
        or (
            key in current
            and current[key].get("status") == "Green"
            and current[key].get("outputHash") == selected[key].get("outputHash")
        )
        for key in deepdive_materializer_ids.intersection(selected)
    )
    if quality_evidence and adopted_deepdive:
        outputs.update(quality_evidence)
    with ledger.materialization_fence():
        for relative in outputs:
            if content._has_reparse_ancestor(repo_root / relative):
                raise ValueError("adoption_target_reparse")
        content._atomic_apply(repo_root, outputs)
    for key, (input_hash, payload) in writes.items():
        ledger.write_checkpoint(artifact_id=key, input_hash=input_hash, validator_id=content._validator_id(key), payload=payload)
    return {"sourceRunId": source.run_id, "adoptedArtifactIds": list(writes), "modelCalls": 0}
