"""既存artifactから再生成できるNews-Grasp決定論builder。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import ctypes
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Mapping


class NewsGraspBuilderError(RuntimeError):
    """builder入力の不足・不整合。"""


DEEPDIVE_LLM_DIALOGUE_REQUIRED = "DEEPDIVE_LLM_DIALOGUE_REQUIRED"


class DeepDiveDialogueGenerationRequired(NewsGraspBuilderError):
    """DeepDive 対談台本を LLM 生成経路へ委譲するための fail-closed。"""


MAX_SUMMARY_BYTES = 1024 * 1024
REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _safe_regular_bytes(
    path: Path, *, maximum: int, code: str = "NG_BUILDER_OUTPUT_PATH_INVALID"
) -> bytes:
    """reparse/hardlinkを拒否し、同一handleからbounded bytesを読む。"""

    try:
        before = os.lstat(path)
        attributes = int(getattr(before, "st_file_attributes", 0))
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or attributes & REPARSE_FLAG
            or before.st_nlink != 1
            or before.st_size > maximum
        ):
            raise ValueError("unsafe file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_nlink,
            ) != (before.st_dev, before.st_ino, before.st_size, 1):
                raise ValueError("file identity drift")
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        raw = b"".join(chunks)
        if (
            len(raw) != before.st_size
            or len(raw) > maximum
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        ):
            raise ValueError("file changed during read")
        return raw
    except (OSError, ValueError) as error:
        raise NewsGraspBuilderError(code) from error


def _assert_safe_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_PATH_INVALID") from error
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or attributes & REPARSE_FLAG
    ):
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_PATH_INVALID")


def _safe_materialization_root(repo_root: Path | str) -> Path:
    root = Path(os.path.abspath(repo_root))
    _assert_safe_directory(root)
    _assert_safe_directory(root / "config")
    registry = root / "config" / "operational_recovery_registry_v1.json"
    _safe_regular_bytes(registry, maximum=MAX_SUMMARY_BYTES)
    return root.resolve(strict=True)


def _ensure_safe_directory(path: Path, *, root: Path) -> None:
    if root not in path.parents:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_PATH_INVALID")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            current.mkdir()
        except FileExistsError:
            pass
        _assert_safe_directory(current)


def _directory_identity(path: Path) -> tuple[int, int, int]:
    metadata = os.lstat(path)
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or attributes & REPARSE_FLAG
    ):
        raise OSError("unsafe directory identity")
    return (metadata.st_dev, metadata.st_ino, attributes)


@contextmanager
def _pinned_output_directories(path: Path, *, root: Path):
    """rootから出力parentまでを検査し、Windowsではrename不可で保持する。"""

    directories = [root]
    cursor = root
    for part in path.parent.relative_to(root).parts:
        cursor = cursor / part
        directories.append(cursor)
    handles: list[int] = []
    try:
        identities = tuple(_directory_identity(item) for item in directories)
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.restype = ctypes.c_void_p
            kernel32.CreateFileW.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
            ]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            for directory in directories:
                handle = kernel32.CreateFileW(
                    str(directory),
                    0x80000000,
                    0x00000001 | 0x00000002,
                    None,
                    3,
                    0x02000000 | 0x00200000,
                    None,
                )
                if handle in {None, 0, ctypes.c_void_p(-1).value}:
                    raise OSError("directory pin failed")
                handles.append(int(handle))
        if tuple(_directory_identity(item) for item in directories) != identities:
            raise OSError("output directory identity drift before use")
        yield
        if tuple(_directory_identity(item) for item in directories) != identities:
            raise OSError("output directory identity drift")
    except OSError as error:
        raise NewsGraspBuilderError("NG_BUILDER_OUTPUT_PATH_INVALID") from error
    finally:
        if os.name == "nt":
            for handle in reversed(handles):
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def _atomic_write(path: Path, content: bytes, *, root: Path) -> bytes:
    _ensure_safe_directory(path.parent, root=root)
    with _pinned_output_directories(path, root=root):
        if path.exists():
            _safe_regular_bytes(path, maximum=MAX_SUMMARY_BYTES)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _assert_safe_directory(path.parent)
            if path.exists():
                _safe_regular_bytes(path, maximum=MAX_SUMMARY_BYTES)
            os.replace(temporary, path)
            return _safe_regular_bytes(path, maximum=MAX_SUMMARY_BYTES)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


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


def _select_source_sentences(sections: list[str], *, maximum_chars: int) -> str:
    """Summary本文を順序どおり採用し、上限だけを文境界で抑える。"""

    source = "\n".join(item.strip() for item in sections)
    sentences = re.findall(r"[^。！？\n]+[。！？]?", source)
    selected: list[str] = []
    count = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if count + len(sentence) > maximum_chars:
            break
        selected.append(sentence)
        count += len(sentence)
    return "\n".join(selected)


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"\A---\r?\n.*?\r?\n---\r?\n", "", text, count=1, flags=re.DOTALL)


def _frontmatter_value(text: str, key: str) -> str:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", text)
    if not match:
        return ""
    return match.group(1).strip().strip("'\"")


def _load_canonical_summary(
    root: Path, *, issue_date: str, expected_source_sha256: str | None
) -> tuple[str, list[str], str]:
    digest_dir = root / "digest"
    summary_dir = digest_dir / "Summary"
    _assert_safe_directory(digest_dir)
    _assert_safe_directory(summary_dir)
    source_path = summary_dir / f"{issue_date}.md"
    with _pinned_output_directories(source_path, root=root):
        raw = _safe_regular_bytes(
            source_path,
            maximum=MAX_SUMMARY_BYTES,
            code="NG_SUMMARY_AUDIO_SOURCE_INVALID",
        )
    source_hash = hashlib.sha256(raw).hexdigest()
    if expected_source_sha256 is not None and (
        re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None
        or expected_source_sha256 != source_hash
    ):
        raise NewsGraspBuilderError("NG_SUMMARY_AUDIO_SOURCE_MISMATCH")
    try:
        source_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise NewsGraspBuilderError("NG_SUMMARY_AUDIO_SOURCE_INVALID") from error
    if _frontmatter_value(source_text, "date") != issue_date:
        raise NewsGraspBuilderError("NG_SUMMARY_AUDIO_SOURCE_INVALID")
    title = (
        _frontmatter_value(source_text, "theme")
        or _frontmatter_value(source_text, "hero_headline")
        or _frontmatter_value(source_text, "title")
    )
    from tools.tts.build_script import normalize_for_tts

    normalized = normalize_for_tts(source_text)
    if not title or not normalized:
        raise NewsGraspBuilderError("NG_SUMMARY_AUDIO_SOURCE_INVALID")
    return title, [normalized], source_hash


def materialize_summary_audio_script(
    *,
    repo_root: Path | str,
    issue_date: str,
    expected_source_sha256: str | None = None,
) -> dict[str, Any]:
    """immutable Summaryから音声原稿を作り、既存TTS品質gate後にだけ確定する。

    sourceが品質基準を満たす情報量を持たない場合は、定型文の反復や文字数の
    水増しをせずfail-closedにする。既存の有効な原稿は上書きしない。
    """

    try:
        issue_day = date.fromisoformat(issue_date)
    except ValueError as exc:
        raise NewsGraspBuilderError("NG_BUILDER_INPUT_INVALID") from exc
    root = _safe_materialization_root(repo_root)
    title, sections, source_hash = _load_canonical_summary(
        root,
        issue_date=issue_date,
        expected_source_sha256=expected_source_sha256,
    )

    from tools.publish_inventory import scheduled_category_ids
    from tools.tts.build_script import CATEGORY_ALIASES, validate_script

    categories = tuple(scheduled_category_ids(issue_date))
    category_names = [CATEGORY_ALIASES[cat_id][0] for cat_id in categories]
    outline_categories = "\n".join(
        f"- {cat_id}: Summary本文の事実、背景、影響、リスク、次の観測点を順に確認する。"
        for cat_id in categories
    )
    outline = (
        "<!-- tts-outline\n"
        f"中心論点: {title}を、発表そのものではなく現場の制約と責任分界から読み解く。\n"
        "背景: 当日のSummaryで確認済みの事実を起点にし、外部情報を追加しない。\n"
        "なぜ今: 同日に並んだ材料の因果と実装順序を整理する必要がある。\n"
        "因果関係: 事実から前提、現場への影響、未確定のリスク、次の観測点へつなぐ。\n"
        "カテゴリ論点:\n"
        f"{outline_categories}\n"
        "リスク・未確定: Summaryに書かれていない断定を避け、未確定事項を残す。\n"
        "次の観測点: 続報、価格、制度適用、受注、実装条件を確認する。\n"
        "-->"
    )
    opening = (
        f"今日は{issue_day.month}月{issue_day.day}日です。朝のニュースをお伝えします。"
        f"対象は、{'、'.join(category_names)}です。"
        f"中心に置くのは、{title}です。"
        "背景と前提を押さえ、現場への影響、一方で残るリスク、次の観測点の順で見ていきます。"
    )
    closing = (
        "今日の観点・考察です。Summaryで確認できた事実と未確定事項を分け、"
        "誰が実装と継続運用の責任を負うのかを見極めることが重要です。"
        "明日以降は続報と実装条件を観測点として追います。"
    )
    source_body = _select_source_sentences(
        [str(item) for item in sections],
        maximum_chars=max(0, 2870 - len(opening) - len(closing)),
    )
    body = "\n\n".join((outline, opening, source_body, closing))
    issues = validate_script(
        body,
        date=issue_date,
        history_texts=[],
        required_categories=categories,
    )
    if issues:
        raise NewsGraspBuilderError(
            "NG_SUMMARY_AUDIO_SCRIPT_QUALITY_INVALID:" + "; ".join(issues)
        )

    target = root / "digest" / "Summary" / f"{issue_date}-audio-script.md"
    with _pinned_output_directories(target, root=root):
        if target.exists():
            existing_raw = _safe_regular_bytes(target, maximum=MAX_SUMMARY_BYTES)
            try:
                existing = existing_raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise NewsGraspBuilderError(
                    "NG_EXISTING_SUMMARY_AUDIO_SCRIPT_INVALID"
                ) from error
            existing_source_match = re.search(
                r"(?m)^sourceHash: ([0-9a-f]{64})$", existing
            )
            existing_issues = validate_script(
                _strip_frontmatter(existing),
                date=issue_date,
                history_texts=[],
                required_categories=categories,
            )
            if existing_issues:
                raise NewsGraspBuilderError("NG_EXISTING_SUMMARY_AUDIO_SCRIPT_INVALID")
            if existing_source_match and existing_source_match.group(1) == source_hash:
                return {
                    "schemaVersion": "SUMMARY_AUDIO_SCRIPT_MATERIALIZATION_V1",
                    "status": "reused",
                    "issueDate": issue_date,
                    "artifactPath": target.relative_to(root).as_posix(),
                    "sourceHash": source_hash,
                    "outputHash": hashlib.sha256(existing_raw).hexdigest(),
                    "qualityGate": "tools.tts.build_script.validate_script",
                }

    document = (
        "---\n"
        "title: Audio Script\n"
        f"date: {issue_date}\n"
        "type: audio-script\n"
        f"sourceHash: {source_hash}\n"
        "---\n\n"
        f"{body}\n"
    )
    output_bytes = _atomic_write(target, document.encode("utf-8"), root=root)
    return {
        "schemaVersion": "SUMMARY_AUDIO_SCRIPT_MATERIALIZATION_V1",
        "status": "materialized",
        "issueDate": issue_date,
        "artifactPath": target.relative_to(root).as_posix(),
        "sourceHash": source_hash,
        "outputHash": hashlib.sha256(output_bytes).hexdigest(),
        "qualityGate": "tools.tts.build_script.validate_script",
    }


def build_deepdive_dialogue(article: Mapping[str, Any]) -> dict[str, Any]:
    raise DeepDiveDialogueGenerationRequired(DEEPDIVE_LLM_DIALOGUE_REQUIRED)
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
