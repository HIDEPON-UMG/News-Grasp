"""既存artifactから再生成できるNews-Grasp決定論builder。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import news_grasp_verified_storage as verified_storage


class NewsGraspBuilderError(RuntimeError):
    """builder入力の不足・不整合。"""


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["receiptSha256"] = _hash(payload)
    return sealed


_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def _prepare_output_path(target: Path, root: Path | None) -> tuple[Path, Path]:
    destination = Path(os.path.abspath(target))
    boundary = Path(os.path.abspath(root or destination.parent))
    try:
        boundary.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    if not boundary.is_dir() or boundary.is_symlink():
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
    try:
        relative_parent = destination.parent.relative_to(boundary)
    except ValueError as error:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    cursor = boundary
    for part in relative_parent.parts:
        cursor = cursor / part
        if cursor.exists():
            if not cursor.is_dir() or cursor.is_symlink():
                raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
        else:
            try:
                cursor.mkdir()
            except OSError as error:
                raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    if destination.exists() and destination.is_symlink():
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
    return destination, boundary


def _atomic_write_json(
    target: Path, payload: Mapping[str, Any], *, root: Path | None = None
) -> bool:
    destination, boundary = _prepare_output_path(target, root)
    document = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    if len(document) > _MAX_OUTPUT_BYTES:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
    try:
        previous = verified_storage.read_bytes(
            destination,
            root=boundary,
            max_bytes=_MAX_OUTPUT_BYTES,
            code="NG_BUILDER_OUTPUT_INVALID",
        )
    except ValueError:
        previous = None
    changed = previous != document
    if not changed:
        return False
    try:
        verified_storage.atomic_write_bytes(
            destination,
            document,
            root=boundary,
            max_bytes=_MAX_OUTPUT_BYTES,
            code="NG_BUILDER_OUTPUT_INVALID",
        )
    except (OSError, ValueError) as error:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    return True


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


def materialize_summary_audio_script(
    summary: Mapping[str, Any],
    target: Path,
    *,
    quality_gate: Callable[[Path], Mapping[str, Any]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """immutable Summaryから実scriptを原子的・冪等にmaterializeする。"""

    built = build_summary_audio_script(summary)
    text = str(built["text"])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) != len(set(lines)):
        raise NewsGraspBuilderError("NG_AUDIO_SCRIPT_QUALITY_INVALID")
    document = (
        "---\n"
        f"issueDate: {built['issueDate']}\n"
        f"sourceHash: {built['sourceHash']}\n"
        "generator: NEWS_GRASP_DETERMINISTIC_AUDIO_SCRIPT_V2\n"
        "---\n\n"
        f"# {summary['title']}\n\n"
        + "\n\n".join(lines)
        + "\n"
    ).encode("utf-8")
    output_sha = hashlib.sha256(document).hexdigest()
    destination, boundary = _prepare_output_path(target, root)
    if len(document) > _MAX_OUTPUT_BYTES:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
    try:
        previous = verified_storage.read_bytes(
            destination,
            root=boundary,
            max_bytes=_MAX_OUTPUT_BYTES,
            code="NG_BUILDER_OUTPUT_INVALID",
        )
    except ValueError:
        previous = None
    changed = previous != document
    if changed:
        try:
            verified_storage.atomic_write_bytes(
                destination,
                document,
                root=boundary,
                max_bytes=_MAX_OUTPUT_BYTES,
                code="NG_BUILDER_OUTPUT_INVALID",
            )
        except (OSError, ValueError) as error:
            raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    gate_result: Mapping[str, Any] = {
        "status": "green",
        "reason": "deterministic_nonempty_unique_lines",
    }
    if quality_gate is not None:
        gate_result = quality_gate(destination)
        if not isinstance(gate_result, Mapping) or not (
            gate_result.get("ok") is True
            or str(gate_result.get("status") or "").casefold() == "green"
        ):
            raise NewsGraspBuilderError("NG_AUDIO_SCRIPT_QUALITY_INVALID")
    return {
        "schemaVersion": "SUMMARY_AUDIO_SCRIPT_MATERIALIZATION_V2",
        "issueDate": built["issueDate"],
        "target": str(destination),
        "sourceHash": built["sourceHash"],
        "outputSha256": output_sha,
        "changed": changed,
        "qualityGate": dict(gate_result),
    }


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK_PAREN_RE = re.compile(r"[（(]\[\[[^\]]+\]\][）)]")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_SECTION_RE = re.compile(r"^###\s+§\d+\s+(.+?)\s+[—-]\s+(.+)$")
_CATEGORY_HEADING_ALIASES = {
    "fx": ("為替", "FX"),
    "ai": ("AI",),
    "it": ("IT", "IT-Consulting"),
    "mobility": ("モビリティ", "Mobility"),
    "manufacturing": ("製造", "Manufacturing"),
    "economy": ("経済", "Economy"),
    "game": ("ゲーム", "Game"),
}


def _clean_summary_line(line: str) -> str:
    text = _WIKILINK_PAREN_RE.sub("", line.strip())
    text = _WIKILINK_RE.sub(lambda match: match.group(2) or match.group(1), text)
    text = re.sub(r"^[>\-*+\s]+", "", text)
    text = re.sub(r"^【(?:事実・概要|背景・要点|影響・展望)】[:：]\s*", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _summary_sections(raw: str) -> tuple[str, dict[str, list[str]]]:
    match = _FRONTMATTER_RE.match(raw)
    frontmatter = match.group(1) if match else ""
    body = raw[match.end() :] if match else raw
    sections: dict[str, list[str]] = {"theme": []}
    current = "theme"
    for raw_line in body.splitlines():
        heading = _SECTION_RE.match(raw_line.strip())
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, []).append(
                _clean_summary_line(f"{heading.group(1)}では、{heading.group(2)}。")
            )
            continue
        if raw_line.startswith("## ") and "KEY TAKEAWAYS" in raw_line:
            current = "takeaways"
            sections.setdefault(current, [])
            continue
        if raw_line.startswith("#") or "PULL QUOTE" in raw_line:
            continue
        cleaned = _clean_summary_line(raw_line)
        if len(cleaned) >= 12 and cleaned not in sections.setdefault(current, []):
            sections[current].append(cleaned)
    return frontmatter, sections


def _select_summary_audio_body(raw: str, issue_date: str) -> str:
    from tools.publish_inventory import scheduled_category_ids
    from tools.tts.build_script import effective_char_count

    frontmatter, sections = _summary_sections(raw)
    year, month, day = (int(part) for part in issue_date.split("-"))
    lines = [f"{year}年{month}月{day}日、朝のニュースです。News-Graspをお届けします。"]
    selected: set[str] = set(lines)

    def add(value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned not in selected:
            selected.add(cleaned)
            lines.append(cleaned)

    for value in sections.get("theme", [])[:5]:
        add(value)
    for category_id in scheduled_category_ids(issue_date):
        aliases = _CATEGORY_HEADING_ALIASES.get(category_id, (category_id,))
        matched = next(
            (
                values
                for heading, values in sections.items()
                if any(alias.casefold() == heading.casefold() for alias in aliases)
            ),
            None,
        )
        if not matched:
            raise NewsGraspBuilderError(
                f"NG_AUDIO_SCRIPT_SOURCE_CATEGORY_MISSING:{category_id}"
            )
        for value in matched[:4]:
            add(value)

    remaining = [
        value
        for values in sections.values()
        for value in values
        if value not in selected
    ]
    for value in remaining:
        candidate = "\n".join([*lines, value])
        if effective_char_count(candidate) > 2780:
            continue
        add(value)
        if effective_char_count("\n".join(lines)) >= 2600:
            break
    if effective_char_count("\n".join(lines)) < 2500:
        raise NewsGraspBuilderError("NG_AUDIO_SCRIPT_SOURCE_DEPTH_INSUFFICIENT")

    closing_source = sections.get("明日へ", []) or sections.get("takeaways", [])
    if closing_source:
        add(
            "今日の観点・考察として、背景と影響、未確定のリスクを分け、次の観測点を確認します。"
        )
        add(closing_source[-1])
    theme = ""
    for line in frontmatter.splitlines():
        if line.strip().startswith("theme:"):
            theme = line.split(":", 1)[1].strip().strip("'\"")
            break
    outline_values = [value for value in sections.get("theme", []) if value][:3]
    outline_source = " ".join(outline_values)
    outline = [
        "<!-- tts-outline",
        f"中心論点: {theme or outline_source[:160]}",
        f"背景: {outline_source[:220]}",
        f"なぜ今: {outline_source[80:300] or outline_source[:220]}",
        f"因果関係: {outline_source[160:380] or outline_source[:220]}",
        "カテゴリ論点: " + "、".join(scheduled_category_ids(issue_date)),
        "リスク・未確定: 公開本文に記録された制約と未確定事項を、発表事実と分けて追います。",
        "次の観測点: Summaryの影響・展望と明日への節に記録された指標を確認します。",
        "-->",
    ]
    return "\n\n".join(lines) + "\n" + "\n".join(outline) + "\n"


def materialize_summary_audio_script_from_markdown(
    *, source: Path, target: Path, issue_date: str, repo_root: Path
) -> dict[str, Any]:
    """既存Summary本文だけからaudio scriptを作り、既存TTS gateを即時実行する。"""

    source_path = Path(source)
    try:
        raw_bytes = verified_storage.read_bytes(
            source_path,
            root=repo_root,
            max_bytes=_MAX_OUTPUT_BYTES,
            code="NG_AUDIO_SCRIPT_SOURCE_INVALID",
        )
        raw = raw_bytes.decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError) as error:
        raise NewsGraspBuilderError("NG_AUDIO_SCRIPT_SOURCE_INVALID") from error
    body = _select_summary_audio_body(raw, issue_date)
    source_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    document = (
        "---\n"
        f"date: {issue_date}\n"
        "categoryId: summary\n"
        "type: audio-script\n"
        "category: Summary\n"
        f"sourceHash: {source_sha}\n"
        "generator: NEWS_GRASP_DETERMINISTIC_AUDIO_SCRIPT_V2\n"
        "---\n\n"
        + body
    ).encode("utf-8")
    destination, boundary = _prepare_output_path(target, repo_root)
    if len(document) > _MAX_OUTPUT_BYTES:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID")
    try:
        previous = verified_storage.read_bytes(
            destination,
            root=boundary,
            max_bytes=_MAX_OUTPUT_BYTES,
            code="NG_BUILDER_OUTPUT_INVALID",
        )
    except ValueError:
        previous = None
    changed = previous != document
    if changed:
        try:
            verified_storage.atomic_write_bytes(
                destination,
                document,
                root=boundary,
                max_bytes=_MAX_OUTPUT_BYTES,
                code="NG_BUILDER_OUTPUT_INVALID",
            )
        except (OSError, ValueError) as error:
            raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_INVALID") from error
    from tools.publish_inventory import scheduled_category_ids
    from tools.tts.build_script import validate_script

    history: list[str] = []
    day_value = datetime.fromisoformat(issue_date).date()
    from datetime import timedelta

    for offset in (1, 2):
        historical = (
            Path(repo_root)
            / "digest"
            / "Summary"
            / f"{(day_value - timedelta(days=offset)).isoformat()}-audio-script.md"
        )
        if historical.is_file():
            history.append(historical.read_text(encoding="utf-8-sig"))
    issues = validate_script(
        body,
        date=issue_date,
        history_texts=history,
        required_categories=scheduled_category_ids(issue_date),
    )
    if issues:
        raise NewsGraspBuilderError(
            "NG_AUDIO_SCRIPT_QUALITY_INVALID:" + "; ".join(issues)
        )
    return {
        "schemaVersion": "SUMMARY_AUDIO_SCRIPT_MATERIALIZATION_V2",
        "issueDate": issue_date,
        "source": str(source_path),
        "target": str(destination),
        "sourceHash": source_sha,
        "outputSha256": hashlib.sha256(document).hexdigest(),
        "changed": changed,
        "qualityGate": {"status": "green", "oracle": "tools.tts.build_script.validate_script"},
    }


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


_DISTRIBUTION_REQUIRED_ARTIFACTS = {
    "summaryAudio",
    "summaryPodcast",
    "deepdiveAudio",
    "deepdivePodcast",
}


def materialize_distribution_manifest_v2(
    *,
    issue_date: str,
    generation_id: str,
    pre_publish_commit: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    target: Path,
    root: Path | None = None,
) -> dict[str, Any]:
    """検証入力として使った配信artifactをsealed manifestへ実体化する。"""

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date):
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")
    if not generation_id.strip() or not re.fullmatch(
        r"[0-9a-fA-F]{40}", pre_publish_commit
    ):
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")
    if not _DISTRIBUTION_REQUIRED_ARTIFACTS.issubset(artifacts):
        raise NewsGraspBuilderError("NG_BUILDER_BUNDLE_INCOMPLETE")
    normalized: dict[str, dict[str, str]] = {}
    for key in sorted(artifacts):
        item = artifacts[key]
        path = str(item.get("path") or "").replace("\\", "/").strip()
        sha256 = str(item.get("sha256") or "").lower().strip()
        if (
            not key
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        ):
            raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID")
        normalized[key] = {"path": path, "sha256": sha256}
    manifest = _seal(
        {
            "schemaVersion": "NEWS_GRASP_DISTRIBUTION_MANIFEST_V2",
            "date": issue_date,
            "issueDate": issue_date,
            "generationId": generation_id,
            "stage": "pre-verifier",
            "pre_publish_commit": pre_publish_commit.lower(),
            "publish_commit": "",
            "publish_commit_resolution": "post_push_verify",
            "same_publish_contract": "pre_publish_commit_must_be_ancestor_of_verified_publish_commit",
            "primary_podcast_state": "build/youtube-podcast/uploads.json",
            "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
            "latest_audio_state": "build/tts/latest_audio.json",
            "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
            "artifacts": normalized,
        }
    )
    changed = _atomic_write_json(Path(target), manifest, root=root)
    return {**manifest, "changed": changed, "target": str(Path(target))}


def build_notification_outcome_v2(
    *,
    issue_date: str,
    status: str,
    source: str,
    subscription_count: int,
    sent_count: int,
    evidence: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """通知の成功をdeliveryまたはzero-audienceのsealed receiptへ束縛する。"""

    if (
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date)
        or not source.strip()
        or subscription_count < 0
        or sent_count < 0
        or sent_count > subscription_count
    ):
        raise NewsGraspBuilderError("NG_NOTIFICATION_INPUT_INVALID")
    evidence = dict(evidence or {})
    observed = recorded_at or datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "schemaVersion": "NEWS_GRASP_NOTIFICATION_OUTCOME_V2",
        "date": issue_date,
        "status": status,
        "source": source,
        "subscription_count": subscription_count,
        "sent_count": sent_count,
        "recorded_at": observed,
        "ok": False,
    }
    audience_sha = str(evidence.get("audienceSha256") or "").lower()
    payload_sha = str(evidence.get("payloadSha256") or "").lower()
    if status in {"sent", "already_sent"}:
        if (
            subscription_count > 0
            and sent_count == subscription_count
            and re.fullmatch(r"[0-9a-f]{64}", audience_sha)
            and re.fullmatch(r"[0-9a-f]{64}", payload_sha)
        ):
            base["deliveryReceipt"] = _seal(
                {
                    "schemaVersion": "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V1",
                    "issueDate": issue_date,
                    "source": source,
                    "targetCount": subscription_count,
                    "acceptedCount": sent_count,
                    "audienceSha256": audience_sha,
                    "payloadSha256": payload_sha,
                    "recordedAt": observed,
                }
            )
            base["ok"] = True
    elif status == "no_subscribers":
        if (
            subscription_count == 0
            and sent_count == 0
            and re.fullmatch(r"[0-9a-f]{64}", audience_sha)
        ):
            base["audienceResolutionReceipt"] = _seal(
                {
                    "schemaVersion": "NEWS_GRASP_NOTIFICATION_AUDIENCE_RESOLUTION_RECEIPT_V1",
                    "issueDate": issue_date,
                    "source": source,
                    "resolvedAudienceCount": 0,
                    "audienceSha256": audience_sha,
                    "recordedAt": observed,
                }
            )
            base["ok"] = True
    return _seal(base)


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


def _distribution_artifacts_from_repo(repo_root: Path, issue_date: str) -> dict[str, dict[str, str]]:
    paths = {
        "summaryAudio": "build/tts/latest_audio.json",
        "summaryPodcastVideo": f"build/youtube-podcast/{issue_date}.mp4",
        "summaryPodcast": "build/youtube-podcast/uploads.json",
        "deepdiveAudio": "build/tts/deepdive/latest_audio.json",
        "deepdivePodcastVideo": f"build/youtube-podcast-deepdive/{issue_date}.mp4",
        "deepdivePodcast": "build/youtube-podcast-deepdive/uploads.json",
    }
    artifacts: dict[str, dict[str, str]] = {}
    for key, relative in paths.items():
        path = Path(repo_root) / relative
        try:
            raw = verified_storage.read_bytes(
                path,
                root=Path(repo_root),
                max_bytes=_MAX_OUTPUT_BYTES,
                code=f"NG_BUILDER_ARTIFACT_INVALID:{relative}",
            )
        except (OSError, ValueError) as error:
            raise NewsGraspBuilderError(f"NG_BUILDER_ARTIFACT_MISSING:{relative}") from error
        if not raw:
            raise NewsGraspBuilderError(f"NG_BUILDER_ARTIFACT_MISSING:{relative}")
        artifacts[key] = {
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp deterministic artifact builders")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audio = subparsers.add_parser("materialize-summary-audio")
    audio.add_argument("--repo-root", type=Path, required=True)
    audio.add_argument("--date", required=True)
    distribution = subparsers.add_parser("materialize-distribution")
    distribution.add_argument("--repo-root", type=Path, required=True)
    distribution.add_argument("--date", required=True)
    distribution.add_argument("--generation-id", required=True)
    distribution.add_argument("--pre-publish-commit", required=True)
    try:
        args = parser.parse_args(argv)
        repo_root = args.repo_root.resolve()
        if args.command == "materialize-summary-audio":
            result = materialize_summary_audio_script_from_markdown(
                source=repo_root / "digest" / "Summary" / f"{args.date}.md",
                target=repo_root / "digest" / "Summary" / f"{args.date}-audio-script.md",
                issue_date=args.date,
                repo_root=repo_root,
            )
        else:
            result = materialize_distribution_manifest_v2(
                issue_date=args.date,
                generation_id=args.generation_id,
                pre_publish_commit=args.pre_publish_commit,
                artifacts=_distribution_artifacts_from_repo(repo_root, args.date),
                target=repo_root / "data" / "distribution" / f"{args.date}.json",
                root=repo_root,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (NewsGraspBuilderError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
