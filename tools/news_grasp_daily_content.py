"""Daily current-issue の生成を read-only model output から一度だけ物理化する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROTECTED_RELEASE = "2026-09-02"
CONTENT_RECEIPT_SCHEMA = "NEWS_GRASP_DAILY_CONTENT_RECEIPT_V1"
MODEL_BUNDLE_SCHEMA = "NEWS_GRASP_DAILY_MODEL_BUNDLE_V1"
REPORTER_SCHEMA = "schemas/news_grasp_daily_reporter_output.schema.json"
EDITOR_SCHEMA = "schemas/news_grasp_daily_editor_output.schema.json"
DEEPDIVE_SCHEMA = "schemas/news_grasp_daily_deepdive_output.schema.json"
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,191}")
_CARD_RE = re.compile(r"(?m)^###\s+\[")
_GENRES = {
    "fx": "FX",
    "ai": "AI",
    "it": "IT-Consulting",
    "mobility": "Mobility",
    "manufacturing": "Manufacturing",
    "economy": "Economy",
    "game": "Game",
}


class DailyContentError(RuntimeError):
    """生成入力、model output、materializationのtyped failure。"""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    candidate = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def _safe_root(repo_root: Path | str) -> Path:
    try:
        root = Path(os.path.abspath(os.fspath(repo_root))).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise DailyContentError("REPO_ROOT_INVALID") from exc
    if not root.is_dir() or root.is_symlink():
        raise DailyContentError("REPO_ROOT_INVALID")
    return root


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise DailyContentError("CONTENT_PATH_INVALID")
    absolute = (root / candidate).resolve(strict=False)
    try:
        absolute.relative_to(root)
    except ValueError as exc:
        raise DailyContentError("CONTENT_PATH_INVALID") from exc
    cursor = root
    for part in candidate.parts[:-1]:
        cursor /= part
        if cursor.exists() and (cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)()):
            raise DailyContentError("CONTENT_PATH_INVALID")
    return absolute


def _load_completion(root: Path, run_id: str, issue_date: str) -> dict[str, Any] | None:
    path = _safe_path(root, f"build/daily-content/{run_id}/completion.json")
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError("CONTENT_RECEIPT_INVALID") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != CONTENT_RECEIPT_SCHEMA
        or value.get("issue_date") != issue_date
        or value.get("run_id") != run_id
        or value.get("ok") is not True
    ):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    hashes = value.get("artifact_hashes")
    derived_hashes = value.get("derived_artifact_hashes", {})
    if not isinstance(hashes, dict) or not hashes or not isinstance(derived_hashes, dict):
        raise DailyContentError("CONTENT_RECEIPT_INVALID")
    for relative, expected in {**hashes, **derived_hashes}.items():
        target = _safe_path(root, str(relative))
        if not target.is_file() or _sha256_bytes(target.read_bytes()) != expected:
            raise DailyContentError("CONTENT_RECEIPT_ARTIFACT_DRIFT")
    reused = dict(value)
    reused["status"] = "reused"
    reused["model_call_count"] = 0
    reused["reporter_call_count"] = 0
    return reused


def _model_bundle_path(root: Path, run_id: str) -> Path:
    return _safe_path(root, f"build/daily-content/{run_id}/model-bundle.json")


def _load_model_bundle(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    categories: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]] | None:
    path = _model_bundle_path(root, run_id)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError("MODEL_BUNDLE_INVALID") from exc
    if not isinstance(value, dict):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    payload = value.get("payload")
    expected_hash = str(value.get("payload_hash") or "")
    if (
        value.get("schemaVersion") != MODEL_BUNDLE_SCHEMA
        or not isinstance(payload, dict)
        or _sha256_bytes(_json_bytes(payload)) != expected_hash
        or payload.get("run_id") != run_id
        or payload.get("issue_date") != issue_date
        or payload.get("scheduled_categories") != list(categories)
    ):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    reporters = payload.get("reporters")
    editor = payload.get("editor")
    deepdive = payload.get("deepdive")
    if (
        not isinstance(reporters, list)
        or len(reporters) != len(categories)
        or [str(item.get("category") or "") for item in reporters if isinstance(item, Mapping)] != list(categories)
        or not all(isinstance(item, dict) and item.get("issue_date") == issue_date for item in reporters)
        or not isinstance(editor, dict)
        or editor.get("issue_date") != issue_date
        or not isinstance(deepdive, dict)
        or not isinstance(deepdive.get("article_markdown"), str)
        or not isinstance(deepdive.get("dialogue_markdown"), str)
    ):
        raise DailyContentError("MODEL_BUNDLE_INVALID")
    return [dict(item) for item in reporters], dict(editor), {
        "article_markdown": str(deepdive["article_markdown"]),
        "dialogue_markdown": str(deepdive["dialogue_markdown"]),
    }


def _write_model_bundle(
    root: Path,
    *,
    run_id: str,
    issue_date: str,
    categories: Sequence[str],
    reporters: Sequence[Mapping[str, Any]],
    editor: Mapping[str, Any],
    deepdive: Mapping[str, str],
) -> None:
    payload = {
        "run_id": run_id,
        "issue_date": issue_date,
        "scheduled_categories": list(categories),
        "reporters": [dict(item) for item in reporters],
        "editor": dict(editor),
        "deepdive": dict(deepdive),
    }
    envelope = {
        "schemaVersion": MODEL_BUNDLE_SCHEMA,
        "payload_hash": _sha256_bytes(_json_bytes(payload)),
        "payload": payload,
    }
    _atomic_write_bytes(_model_bundle_path(root, run_id), _json_bytes(envelope))


def _default_candidate_provider(category: str, issue_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from tools.harvest_candidates import harvest_category_with_audit
    from tools.prepare_reporter_candidates import prepare_rows

    candidates, audit = harvest_category_with_audit(category, max_per_category=25, timeout=12.0)
    prepared, dropped = prepare_rows(
        candidates,
        max_rows=25,
        thumb_limit=5,
        decode_timeout=3.0,
        thumb_timeout=5.0,
        thumb_retries=0,
    )
    audit = dict(audit)
    audit.update(
        {
            "date": issue_date,
            "category_id": category,
            "candidates_total": len(prepared),
            "selected_total": 0,
            "dropped_after_prepare": len(dropped),
        }
    )
    if not prepared:
        raise DailyContentError(f"CANDIDATES_EMPTY:{category}")
    return prepared, audit


def _resolve_codex_executable() -> Path:
    candidates: list[Path] = []
    local = os.environ.get("USERPROFILE", "").strip()
    if local:
        candidates.extend(
            Path(local).glob(".vscode/extensions/openai.chatgpt-*/bin/windows-x86_64/codex.exe")
        )
    unique: dict[str, Path] = {}
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            unique[_sha256_bytes(resolved.read_bytes())] = resolved
    if len(unique) != 1:
        raise DailyContentError("CODEX_EXECUTABLE_IDENTITY_AMBIGUOUS")
    return next(iter(unique.values()))


def _model_prompt(
    *,
    root: Path,
    role: str,
    issue_date: str,
    category: str | None,
    context: Mapping[str, Any],
) -> str:
    if role == "reporter":
        source = (root / "prompts" / "newsroom-reporter-system.md").read_text(encoding="utf-8-sig")
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。候補を再収集せず、指定JSON schemaだけを返す。"
            "recordsのpublished_dateはissue_dateと完全一致させ、RSS/pubDateの時刻を公開日証拠に使わない。"
            "前日以前の候補は採用せず、date_evidence_sourceはRSS由来以外の根拠だけを記載する。"
            "recordのurlは入力candidatesにあるURL文字列を完全コピーし、未収集URLや別URLへ置換しない。"
            "digest_markdownの各記事カード見出しは必ず`### [1]`、`### [2]`の形式でrecordsと同数だけ置き、余分なカード見出しを置かない。"
            f"\nissue_date={issue_date}\ncategory={category}\n入力:\n"
            f"{json.dumps(context, ensure_ascii=False)}"
        )
    if role == "editor":
        source = (root / "prompts" / "newsroom-editor-system.md").read_text(encoding="utf-8-sig")
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。reporter recordsを再収集・改変せず、"
            "重複URLだけを一件に畳み、公開用Summaryとappend_recordsを指定JSON schemaだけで返す。"
            f"\nissue_date={issue_date}\n入力:\n{json.dumps(context, ensure_ascii=False)}"
        )
    if role == "deepdive":
        source = (root / "prompts" / "deepdive-research-system.md").read_text(encoding="utf-8-sig")
        return (
            f"{source}\n\nこの実行ではrepoを変更してはならない。入力recordのURL以外を捏造せず、"
            "当日DeepDive Markdown全文と記事固有の対談Markdown全文を指定JSON schemaだけで返す。"
            f"\nissue_date={issue_date}\n入力:\n{json.dumps(context, ensure_ascii=False)}"
        )
    raise DailyContentError("MODEL_ROLE_UNKNOWN")


def _default_model_runner(
    *,
    role: str,
    repo_root: Path,
    issue_date: str,
    run_id: str,
    category: str | None = None,
    output_dir: Path,
    **context: Any,
) -> dict[str, Any]:
    from tools.model_spawn_client import run_model_process

    model = "gpt-5.6-sol" if role == "deepdive" else "gpt-5.6-luna"
    effort = "max"
    schema = {
        "reporter": REPORTER_SCHEMA,
        "editor": EDITOR_SCHEMA,
        "deepdive": DEEPDIVE_SCHEMA,
    }[role]
    label = f"reporter-{category}" if role == "reporter" else role
    output = output_dir / f"{label}.json"
    prompt = _model_prompt(
        root=repo_root,
        role=role,
        issue_date=issue_date,
        category=category,
        context=context,
    )
    command = [
        str(_resolve_codex_executable()),
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-C",
        str(repo_root),
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--output-schema",
        str(_safe_path(repo_root, schema)),
        "-o",
        str(output),
        "-",
    ]
    try:
        route = (
            f"reporter:{category}"
            if role == "reporter" and category
            else "newsroom_editor"
            if role == "editor"
            else "deepdive"
        )
        completed = run_model_process(
            command,
            route=route,
            input=prompt,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            timeout=900 if role == "deepdive" else 600,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - broker failure is typed and has no canonical mutation.
        raise DailyContentError(f"MODEL_PROCESS_FAILED:{role}:{type(exc).__name__}:{exc}") from exc
    (output_dir / f"{label}.events.jsonl").write_text(str(completed.stdout or ""), encoding="utf-8")
    (output_dir / f"{label}.stderr.log").write_text(str(completed.stderr or ""), encoding="utf-8")
    if completed.returncode != 0 or not output.is_file():
        print(
            f"ERROR: model role={role} category={category} returncode={completed.returncode} "
            f"stderr={str(completed.stderr or '')[-3000:]}",
            file=sys.stderr,
        )
        raise DailyContentError(f"MODEL_PROCESS_FAILED:{role}:{completed.returncode}")
    try:
        value = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DailyContentError(f"MODEL_OUTPUT_JSON_INVALID:{role}") from exc
    if not isinstance(value, dict):
        raise DailyContentError(f"MODEL_OUTPUT_JSON_INVALID:{role}")
    return value


def _validate_reporter(value: Any, *, category: str, issue_date: str, search_audit: Mapping[str, Any]) -> dict[str, Any]:
    from tools.validate_record import RecordSchemaError, validate_record
    from tools.url_quality import is_google_news_rss_url, is_google_news_proxy_thumb, is_news_grasp_self_thumb, looks_homepage_or_section_landing

    if not isinstance(value, Mapping) or value.get("category") != category or value.get("issue_date") != issue_date:
        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:identity")
    records = value.get("records")
    digest = value.get("digest_markdown")
    audit = value.get("search_audit")
    if not isinstance(records, list) or not 1 <= len(records) <= 5 or not isinstance(digest, str) or not isinstance(audit, Mapping):
        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:shape")
    if _CARD_RE.findall(digest).__len__() != len(records):
        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:card_count")
    candidate_urls = {str(item.get("url") or "").rstrip("/") for item in (search_audit.get("candidates") or []) if isinstance(item, Mapping)}
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:record")
        try:
            validate_record(record)
        except RecordSchemaError as exc:
            raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:schema") from exc
        url = str(record.get("url") or "").rstrip("/")
        thumb = str(record.get("thumb") or "")
        semantic_errors: list[str] = []
        if record.get("date") != issue_date:
            semantic_errors.append("date")
        if str(record.get("published_date") or "") not in {issue_date, str(date.fromisoformat(issue_date))}:
            semantic_errors.append("published_date")
        evidence = str(record.get("date_evidence_source") or "")
        if not evidence.strip():
            semantic_errors.append("date_evidence_source_missing")
        elif "rss" in evidence.casefold():
            semantic_errors.append("date_evidence_source_rss")
        if is_google_news_rss_url(url):
            semantic_errors.append("google_news_url")
        if looks_homepage_or_section_landing(url):
            semantic_errors.append("landing_url")
        if not thumb.startswith(("http://", "https://")):
            semantic_errors.append("thumb_missing")
        elif is_google_news_proxy_thumb(thumb):
            semantic_errors.append("google_thumb")
        elif is_news_grasp_self_thumb(thumb):
            semantic_errors.append("self_thumb")
        if semantic_errors:
            raise DailyContentError(
                f"REPORTER_OUTPUT_INVALID:{category}:semantic:{','.join(semantic_errors)}"
            )
        if candidate_urls and url not in candidate_urls:
            raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:candidate_provenance")
        normalized_records.append(dict(record))
    merged_audit = dict(search_audit)
    merged_audit.update(dict(audit))
    merged_audit.update({"date": issue_date, "category_id": category, "selected_total": len(records)})
    for key in ("queries", "raw_results_total", "candidates_total", "selected_total"):
        if key not in merged_audit:
            raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:audit")
    return {
        "category": category,
        "issue_date": issue_date,
        "records": normalized_records,
        "digest_markdown": digest.rstrip() + "\n",
        "search_audit": merged_audit,
    }


def _validate_editor(value: Any, *, issue_date: str, reporters: Sequence[Mapping[str, Any]], preview_dir: Path) -> dict[str, Any]:
    from tools.validate_editor_output_preview import validate_editor_output_preview

    if not isinstance(value, Mapping) or value.get("issue_date") != issue_date:
        raise DailyContentError("EDITOR_OUTPUT_INVALID:identity")
    records = value.get("append_records")
    summary = value.get("summary_markdown")
    if not isinstance(records, list) or not records or not isinstance(summary, str):
        raise DailyContentError("EDITOR_OUTPUT_INVALID:shape")
    expected_urls = {str(record.get("url") or "").rstrip("/") for reporter in reporters for record in reporter["records"]}
    actual_urls = [str(record.get("url") or "").rstrip("/") for record in records if isinstance(record, Mapping)]
    if set(actual_urls) != expected_urls or len(actual_urls) != len(set(actual_urls)):
        raise DailyContentError("EDITOR_OUTPUT_INVALID:reporter_binding")
    preview = preview_dir / "editor-preview.json"
    preview.write_bytes(_json_bytes(dict(value)))
    errors = validate_editor_output_preview(preview, issue_date=issue_date)
    if errors:
        raise DailyContentError("EDITOR_OUTPUT_INVALID:" + "|".join(errors[:5]))
    return dict(value)


def _validate_deepdive(value: Any, *, issue_date: str, allowed_urls: set[str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:shape")
    article = str(value.get("article_markdown") or "")
    dialogue = str(value.get("dialogue_markdown") or "")
    if (
        f"date: '{issue_date}'" not in article
        and f'date: "{issue_date}"' not in article
        and f"date: {issue_date}" not in article
    ):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:date")
    if not all(marker in article for marker in ("## 背景", "## 深掘り", "## 注目点", "## 参考リンク")):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:sections")
    urls = set(re.findall(r"https?://[^\s)>\]\"']+", article))
    if any(url.rstrip("/") not in allowed_urls for url in urls):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:url_provenance")
    if "## 台本" not in dialogue or not all(label in dialogue for label in ("若手:", "先輩:")):
        raise DailyContentError("DEEPDIVE_OUTPUT_INVALID:dialogue")
    return {"article_markdown": article.rstrip() + "\n", "dialogue_markdown": dialogue.rstrip() + "\n"}


def _atomic_apply(root: Path, outputs: Mapping[str, bytes]) -> dict[str, str]:
    ordered = sorted(outputs)
    originals: dict[str, bytes | None] = {}
    candidates: dict[str, Path] = {}
    try:
        for relative in ordered:
            target = _safe_path(root, relative)
            originals[relative] = target.read_bytes() if target.is_file() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
            candidate = Path(raw)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(outputs[relative])
                handle.flush()
                os.fsync(handle.fileno())
            candidates[relative] = candidate
        for relative in ordered:
            os.replace(candidates[relative], _safe_path(root, relative))
    except BaseException:
        for relative in reversed(ordered):
            if relative not in originals:
                continue
            target = _safe_path(root, relative)
            previous = originals[relative]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(target, previous)
        raise
    finally:
        for candidate in candidates.values():
            candidate.unlink(missing_ok=True)
    return {relative: _sha256_bytes(outputs[relative]) for relative in ordered}


def _default_derived_builder(*, repo_root: Path, issue_date: str, run_id: str, **_: Any) -> dict[str, Any]:
    from tools import generate_pages
    from tools.news_grasp_deterministic_builders import materialize_summary_audio_script
    from tools.tts import deepdive_audio, deepdive_dialogue, publish_audio, synthesize_daily
    from tools.youtube_podcast import build_video

    summary_audio = materialize_summary_audio_script(repo_root=repo_root, issue_date=issue_date)
    daily_mp3 = synthesize_daily.synthesize(issue_date)
    deep_script = repo_root / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md"
    deep_mp3 = deepdive_dialogue.synthesize_dialogue(deep_script, out_name=issue_date)
    if daily_mp3 is None or deep_mp3 is None:
        raise DailyContentError("AUDIO_SYNTHESIS_FAILED")
    publish_audio.write_latest_audio(
        issue_date,
        publish_audio.versioned_audio_url(issue_date, Path(daily_mp3)),
        run_id=run_id,
    )
    deepdive_audio.write_latest_audio(
        issue_date,
        deepdive_audio.versioned_deepdive_audio_url(issue_date, Path(deep_mp3)),
        run_id=run_id,
    )
    daily_video = build_video.build(issue_date, kind="daily")
    deep_video = build_video.build(issue_date, kind="deepdive")
    generated = generate_pages.build_all(full=False)
    from tools.render_deepdive import build_deepdive_archive, build_deepdive_pages

    deep_pages = build_deepdive_pages(docs_root=repo_root / "docs", full=False, issue_date=issue_date)
    build_deepdive_archive(docs_root=repo_root / "docs")
    return {
        "ok": True,
        "status": "built",
        "artifacts": [
            str(daily_mp3),
            str(deep_mp3),
            str(daily_video),
            str(deep_video),
            str(repo_root / "build" / "tts" / "daily" / "latest_audio.json"),
            str(repo_root / "build" / "tts" / "deepdive" / "latest_audio.json"),
            str(repo_root / str(summary_audio["artifactPath"])),
            *map(str, generated),
            *map(str, deep_pages),
        ],
        "summary_audio": summary_audio,
    }


def _derived_artifact_hashes(root: Path, derived: Mapping[str, Any]) -> dict[str, str]:
    artifacts = derived.get("artifacts", [])
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes, bytearray)):
        raise DailyContentError("DERIVED_ARTIFACTS_INVALID")
    hashes: dict[str, str] = {}
    for item in artifacts:
        candidate = Path(str(item))
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError) as exc:
            raise DailyContentError("DERIVED_ARTIFACT_PATH_INVALID") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise DailyContentError("DERIVED_ARTIFACT_PATH_INVALID")
        hashes[relative] = _sha256_bytes(resolved.read_bytes())
    return hashes


def produce_current_issue(
    *,
    repo_root: Path | str,
    issue_date: str,
    run_id: str,
    scheduled_categories: Sequence[str],
    candidate_provider: Callable[[str, str], tuple[list[dict[str, Any]], dict[str, Any]]] | None = None,
    model_runner: Callable[..., Mapping[str, Any]] | None = None,
    derived_builder: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """対象日のmodel outputを全検証後に一つのcanonical bundleへ反映する。"""

    if issue_date == PROTECTED_RELEASE:
        raise DailyContentError("PROTECTED_RELEASE_REEXECUTION_FORBIDDEN")
    try:
        if date.fromisoformat(issue_date).isoformat() != issue_date:
            raise ValueError(issue_date)
    except ValueError as exc:
        raise DailyContentError("ISSUE_DATE_INVALID") from exc
    if not _RUN_ID_RE.fullmatch(str(run_id or "")) or str(run_id).casefold() in {"final", "latest", "current"}:
        raise DailyContentError("ACTUAL_RUN_ID_REQUIRED")
    categories = tuple(str(item) for item in scheduled_categories)
    if not categories or len(categories) != len(set(categories)) or any(item not in _GENRES for item in categories):
        raise DailyContentError("SCHEDULED_CATEGORIES_INVALID")
    root = _safe_root(repo_root)
    reused = _load_completion(root, run_id, issue_date)
    if reused is not None:
        return reused
    candidate_fn = candidate_provider or _default_candidate_provider
    model_fn = model_runner or _default_model_runner
    derived_fn = derived_builder or _default_derived_builder
    cached_bundle = _load_model_bundle(
        root,
        run_id=run_id,
        issue_date=issue_date,
        categories=categories,
    )
    model_call_count = 0
    if cached_bundle is not None:
        ordered_reporters, editor, deepdive = cached_bundle
    else:
        candidates_by_category: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=len(categories)) as pool:
            futures = {pool.submit(candidate_fn, category, issue_date): category for category in categories}
            for future in as_completed(futures):
                category = futures[future]
                try:
                    candidates_by_category[category] = future.result()
                except Exception as exc:  # noqa: BLE001
                    raise DailyContentError(f"CANDIDATE_COLLECTION_FAILED:{category}:{type(exc).__name__}") from exc

        with tempfile.TemporaryDirectory(prefix=f"news-grasp-daily-content-{issue_date}-") as raw:
            output_dir = Path(raw)
            reporter_rows: dict[str, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(4, len(categories))) as pool:
                futures = {}
                for category in categories:
                    candidates, audit = candidates_by_category[category]
                    audit_input = {**audit, "candidates": candidates}
                    futures[
                        pool.submit(
                            model_fn,
                            role="reporter",
                            repo_root=root,
                            issue_date=issue_date,
                            run_id=run_id,
                            category=category,
                            output_dir=output_dir,
                            candidates=candidates,
                            search_audit=audit_input,
                        )
                    ] = (category, audit_input)
                for future in as_completed(futures):
                    category, audit_input = futures[future]
                    try:
                        reporter_rows[category] = _validate_reporter(
                            future.result(), category=category, issue_date=issue_date, search_audit=audit_input
                        )
                    except DailyContentError as exc:
                        print(
                            f"ERROR: reporter category={category} validation={exc}",
                            file=sys.stderr,
                        )
                        raise
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"ERROR: reporter category={category} validation={exc}",
                            file=sys.stderr,
                        )
                        raise DailyContentError(f"REPORTER_OUTPUT_INVALID:{category}:{type(exc).__name__}") from exc
            ordered_reporters = [reporter_rows[category] for category in categories]
            editor_raw = model_fn(
                role="editor",
                repo_root=root,
                issue_date=issue_date,
                run_id=run_id,
                category=None,
                output_dir=output_dir,
                reporters=ordered_reporters,
            )
            editor = _validate_editor(editor_raw, issue_date=issue_date, reporters=ordered_reporters, preview_dir=output_dir)
            allowed_urls = {
                str(record[key]).rstrip("/")
                for record in editor["append_records"]
                for key in ("url", "thumb")
                if str(record.get(key) or "").startswith(("http://", "https://"))
            }
            deepdive_raw = model_fn(
                role="deepdive",
                repo_root=root,
                issue_date=issue_date,
                run_id=run_id,
                category=None,
                output_dir=output_dir,
                summary_markdown=editor["summary_markdown"],
                records=editor["append_records"],
            )
            deepdive = _validate_deepdive(deepdive_raw, issue_date=issue_date, allowed_urls=allowed_urls)
        model_call_count = len(categories) + 2
        _write_model_bundle(
            root,
            run_id=run_id,
            issue_date=issue_date,
            categories=categories,
            reporters=ordered_reporters,
            editor=editor,
            deepdive=deepdive,
        )

    outputs: dict[str, bytes] = {}
    for reporter in ordered_reporters:
        category = str(reporter["category"])
        genre = _GENRES[category]
        outputs[f"tmp/newsroom/{issue_date}/{category}.records.jsonl"] = b"".join(
            _json_bytes(record) for record in reporter["records"]
        )
        outputs[f"data/search_audit/{issue_date}/{category}.json"] = _json_bytes(reporter["search_audit"])
        outputs[f"digest/{genre}/{issue_date}-{genre}.md"] = str(reporter["digest_markdown"]).encode("utf-8")
    outputs[f"digest/Summary/{issue_date}.md"] = (str(editor["summary_markdown"]).rstrip() + "\n").encode("utf-8")
    outputs[f"digest/DeepDive/{issue_date}-DeepDive.md"] = deepdive["article_markdown"].encode("utf-8")
    outputs[f"digest/DeepDive/{issue_date}-DeepDive-dialogue.md"] = deepdive["dialogue_markdown"].encode("utf-8")
    articles_path = _safe_path(root, "data/articles.jsonl")
    previous = articles_path.read_bytes() if articles_path.is_file() else b""
    if previous and not previous.endswith(b"\n"):
        previous += b"\n"
    existing_keys: set[tuple[str, str]] = set()
    for line in previous.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DailyContentError("ARTICLES_JSONL_INVALID") from exc
        existing_keys.add((str(row.get("date") or ""), str(row.get("url") or "")))
    additions = [
        record
        for record in editor["append_records"]
        if (str(record.get("date") or ""), str(record.get("url") or "")) not in existing_keys
    ]
    outputs["data/articles.jsonl"] = previous + b"".join(_json_bytes(record) for record in additions)
    artifact_hashes = _atomic_apply(root, outputs)

    derived = derived_fn(repo_root=root, issue_date=issue_date, run_id=run_id, artifact_hashes=artifact_hashes)
    if not isinstance(derived, Mapping) or derived.get("ok") is not True:
        raise DailyContentError("DERIVED_BUILD_FAILED")
    derived_artifact_hashes = _derived_artifact_hashes(root, derived)
    bundle_id = _sha256_bytes(
        _json_bytes(
            {
                "issue_date": issue_date,
                "run_id": run_id,
                "artifact_hashes": artifact_hashes,
                "derived_artifact_hashes": derived_artifact_hashes,
            }
        )
    )
    receipt = {
        "schemaVersion": CONTENT_RECEIPT_SCHEMA,
        "ok": True,
        "status": "materialized",
        "issue_date": issue_date,
        "run_id": run_id,
        "scheduled_categories": list(categories),
        "reporter_call_count": len(categories),
        "model_call_count": model_call_count,
        "model_call_count_total": len(categories) + 2,
        "bundle_id": bundle_id,
        "artifact_hashes": artifact_hashes,
        "derived_artifact_hashes": derived_artifact_hashes,
        "derived": dict(derived),
    }
    receipt_path = _safe_path(root, f"build/daily-content/{run_id}/completion.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(receipt_path, _json_bytes(receipt))
    return receipt


__all__ = ["CONTENT_RECEIPT_SCHEMA", "MODEL_BUNDLE_SCHEMA", "DailyContentError", "produce_current_issue"]
