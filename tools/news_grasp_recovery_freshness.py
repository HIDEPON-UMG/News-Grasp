"""Recovery worktree と封印済み production generation の鮮度を検証する。

この module は recovery の preflight 専用であり、worktree を同期したり、
manifest を再発行したりしない。active generation の pointer/manifest、
recovery worktree の Git tree、production runtime の critical-file SHA256 を
同じ immutable generation に束縛する。

``verify_recovery_freshness`` は、runner を起動する前に扱える typed dict を
常に返す。検証失敗は ``RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH`` として
返り、呼び出し側は child process を spawn してはならない。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


SCHEMA_VERSION = "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_V1"
NG_RC_03_RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS = (
    "NG_RC_03_RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS"
)
ACTIVE_POINTER_SCHEMA = "NEWS_GRASP_ACTIVE_GENERATION_V2"
GENERATION_MANIFEST_SCHEMA = "PRODUCTION_GENERATION_MANIFEST_V2"
MISMATCH_REASON = "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH"
CLI_RED_EXIT = 78

# この集合は launcher の generation seal と同じ閉包である。順序は正本として
# 固定し、criticalSetSha256 はこの配列の canonical JSON から計算する。
CRITICAL_PATHS: tuple[str, ...] = (
    "tools/deepdive_quality.py",
    "tools/render_deepdive.py",
    "tools/tts/build_deepdive_dialogue_script.py",
    "tools/tts/deepdive_dialogue.py",
    "tools/tts/proc.py",
    "tools/validate_deepdive_urls.py",
    "prompts/deepdive-template.html",
    "prompts/deepdive-runner-prompt.md",
    "scripts/ops/invoke-deepdive-system-fetch.ps1",
    "tools/news_grasp_recovery_freshness.py",
    "tools/news_grasp_recovery_closeout.py",
    "tools/news_grasp_operational_contract.py",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _critical_set_sha256() -> str:
    return _sha256_json(list(CRITICAL_PATHS))


CRITICAL_SET_SHA256 = _critical_set_sha256()


def _is_reparse_point(path: Path) -> bool:
    """symlink 以外の Windows reparse point も拒否する。"""

    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return False
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


def _regular_file(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not _is_reparse_point(path)


def _safe_existing_root(path: Path) -> Path:
    """既存の通常ディレクトリを、symlink/reparse 無しで返す。"""

    _reject_lexical_links(path)
    try:
        candidate = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("ROOT_MISSING") from error
    if not candidate.is_dir() or candidate.is_symlink() or _is_reparse_point(candidate):
        raise ValueError("ROOT_INVALID")
    # resolve() は祖先の symlink を追跡するため、各 component を再確認する。
    cursor = candidate
    ancestors: list[Path] = []
    while True:
        ancestors.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for ancestor in ancestors:
        if ancestor.is_symlink() or _is_reparse_point(ancestor):
            raise ValueError("ROOT_REPARSE")
    return candidate


def _reject_lexical_links(path: Path) -> None:
    """resolve() 前の名前解決経路にも symlink/reparse が無いことを確認する。"""

    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("PATH_INVALID") from error
    cursor = lexical
    while True:
        try:
            if cursor.is_symlink() or _is_reparse_point(cursor):
                raise ValueError("PATH_REPARSE")
        except OSError as error:
            raise ValueError("PATH_INVALID") from error
        if cursor.parent == cursor:
            break
        cursor = cursor.parent


def _assert_under_regular_tree(path: Path, root: Path, *, file_required: bool) -> Path:
    """path が root 配下の実体で、途中に symlink/reparse が無いことを確認する。"""

    _reject_lexical_links(root)
    _reject_lexical_links(path)
    try:
        root_resolved = root.resolve(strict=True)
        candidate_resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError("PATH_MISSING") from error
    try:
        relative = candidate_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise ValueError("PATH_ESCAPE") from error
    cursor = root_resolved
    if cursor.is_symlink() or _is_reparse_point(cursor):
        raise ValueError("ROOT_REPARSE")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink() or _is_reparse_point(cursor):
            raise ValueError("PATH_REPARSE")
    if file_required and not _regular_file(candidate_resolved):
        raise ValueError("FILE_INVALID")
    if not file_required and not candidate_resolved.is_dir():
        raise ValueError("DIRECTORY_INVALID")
    return candidate_resolved


def _safe_relative(root: Path, relative: str, *, file_required: bool = True) -> Path:
    """manifest の POSIX relative path を安全に root に束縛する。"""

    if not isinstance(relative, str) or not relative:
        raise ValueError("RELATIVE_PATH_INVALID")
    # Git tree は POSIX path で封印される。Windows separator、drive、NUL、
    # dot segment は同一表現の別名を作るため fail-closed にする。
    if "\\" in relative or "\x00" in relative or relative.startswith("/"):
        raise ValueError("RELATIVE_PATH_INVALID")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError("RELATIVE_PATH_INVALID")
    return _assert_under_regular_tree(root / Path(*posix.parts), root, file_required=file_required)


def _json_file(path: Path) -> dict[str, Any]:
    if not _regular_file(path):
        raise ValueError("JSON_FILE_INVALID")
    # pointer は小さく、manifest は source tree 全体を含むため上限を分ける。
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("JSON_FILE_TOO_LARGE")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        raise ValueError("JSON_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return value


def _git_executable() -> str:
    # production Windows は標準インストール場所を優先し、fixture/Linux では
    # PATH の git を使う。いずれも shell=False で呼び出す。
    windows_git = Path(r"C:\Program Files\Git\cmd\git.exe")
    if windows_git.is_file():
        return str(windows_git)
    return shutil.which("git") or "git"


def _run_git(repo: Path, *args: str) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [_git_executable(), "-C", str(repo), *args],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("GIT_TIMEOUT") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-500:]
        raise ValueError(f"GIT_FAILED:{completed.returncode}:{detail}")
    return completed.stdout.decode("utf-8", errors="strict")


def _git_tree_manifest(repo: Path, commit: str) -> dict[str, str]:
    output = _run_git(
        repo,
        "-c",
        "core.quotePath=false",
        "ls-tree",
        "-r",
        "--full-tree",
        "-z",
        commit,
    )
    rows: dict[str, str] = {}
    for entry in output.split("\x00"):
        if not entry:
            continue
        try:
            metadata, relative = entry.split("\t", 1)
            mode, object_type, object_id = metadata.split(" ", 2)
        except ValueError as error:
            raise ValueError("GIT_TREE_INVALID") from error
        if (
            not relative
            or relative in rows
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or not re.fullmatch(r"[0-7]{6}", mode)
            or object_type not in {"blob", "commit"}
            or not re.fullmatch(r"[0-9a-f]{40,64}", object_id)
        ):
            raise ValueError("GIT_TREE_INVALID")
        rows[relative] = f"{mode}:{object_type}:{object_id}"
    if not rows:
        raise ValueError("GIT_TREE_EMPTY")
    return dict(sorted(rows.items()))


def _hex_hash(value: object, *, length: int = 64) -> str:
    text = str(value or "")
    if re.fullmatch(rf"[0-9a-f]{{{length}}}", text) is None:
        raise ValueError("HASH_INVALID")
    return text


def _failure(
    *,
    worktree_root: object,
    runtime_root: object,
    detail_code: str,
    manifest_path: object = "",
    commit: object = "",
    generation_id: object = "",
    per_file_sha256: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """全ての失敗を同一 typed Red に射影する。"""

    def _display_path(value: object) -> str:
        try:
            return str(Path(str(value)).expanduser().resolve()) if value else ""
        except (OSError, RuntimeError, ValueError):
            return str(value or "")

    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "red",
        "ok": False,
        "reasonCode": MISMATCH_REASON,
        "detailCode": str(detail_code),
        "worktreeRoot": _display_path(worktree_root),
        "runtimeRoot": _display_path(runtime_root),
        "commit": str(commit or ""),
        "generationId": str(generation_id or ""),
        "perFileSha256": dict(per_file_sha256 or {}),
        "criticalSetSha256": CRITICAL_SET_SHA256,
        "manifestPath": _display_path(manifest_path),
    }


def verify_recovery_freshness(
    worktree_root: Path,
    runtime_root: Path,
    active_pointer_path: Path | None = None,
) -> dict[str, object]:
    """recovery workspace を active production generation へ再束縛する。

    ``runtime_root`` は ``active-generation-v2.json`` と ``generations`` を
    所有する authority root である。検証は読み取り専用であり、stale の場合に
    runtime や worktree を同期しない。戻り値の ``status`` が ``green`` でない
    限り caller は recovery child を起動してはならない。
    """

    requested_worktree = Path(worktree_root)
    requested_runtime = Path(runtime_root)
    requested_pointer = (
        Path(active_pointer_path)
        if active_pointer_path is not None
        else requested_runtime / "active-generation-v2.json"
    )
    manifest_display = ""
    generation_id = ""
    commit = ""
    observed_runtime: dict[str, str] = {}
    try:
        worktree = _safe_existing_root(requested_worktree)
        runtime_authority = _safe_existing_root(requested_runtime)
        pointer = _assert_under_regular_tree(
            requested_pointer,
            runtime_authority,
            file_required=True,
        )
        pointer_value = _json_file(pointer)
        required_pointer = (
            "schemaVersion",
            "generationId",
            "manifestPath",
            "manifestSha256",
            "pointerSha256",
        )
        if (
            pointer_value.get("schemaVersion") != ACTIVE_POINTER_SCHEMA
            or any(not pointer_value.get(key) for key in required_pointer[1:])
        ):
            raise ValueError("ACTIVE_POINTER_SCHEMA_INVALID")
        pointer_unsigned = dict(pointer_value)
        pointer_sha = pointer_unsigned.pop("pointerSha256", None)
        if _hex_hash(pointer_sha) != _sha256_json(pointer_unsigned):
            raise ValueError("ACTIVE_POINTER_HASH_MISMATCH")
        generation_id = str(pointer_value["generationId"])
        _hex_hash(pointer_value["manifestSha256"])
        manifest_raw = str(pointer_value["manifestPath"])
        manifest_candidate = Path(manifest_raw)
        if not manifest_candidate.is_absolute():
            raise ValueError("MANIFEST_PATH_NOT_ABSOLUTE")
        manifest_root = _assert_under_regular_tree(
            runtime_authority / "generations",
            runtime_authority,
            file_required=False,
        )
        manifest_path = _assert_under_regular_tree(
            manifest_candidate,
            manifest_root,
            file_required=True,
        )
        manifest_display = str(manifest_path)
        manifest_value = _json_file(manifest_path)
        manifest_unsigned = dict(manifest_value)
        manifest_sha = manifest_unsigned.pop("manifestSha256", None)
        if _hex_hash(manifest_sha) != _sha256_json(manifest_unsigned):
            raise ValueError("MANIFEST_HASH_MISMATCH")
        if (
            manifest_value.get("schemaVersion") != GENERATION_MANIFEST_SCHEMA
            or manifest_value.get("generationId") != generation_id
            or str(manifest_value.get("manifestSha256"))
            != str(pointer_value.get("manifestSha256"))
        ):
            raise ValueError("MANIFEST_SCHEMA_INVALID")

        source = manifest_value.get("source")
        runtime = manifest_value.get("runtime")
        if not isinstance(source, Mapping) or not isinstance(runtime, Mapping):
            raise ValueError("MANIFEST_COMPONENT_INVALID")
        source_commit = str(source.get("commit") or "").lower()
        runtime_commit = str(runtime.get("commit") or "").lower()
        if (
            re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None
            or re.fullmatch(r"[0-9a-f]{40,64}", runtime_commit) is None
            or source_commit != runtime_commit
        ):
            raise ValueError("MANIFEST_COMMIT_INVALID")
        source_tracked = source.get("trackedFiles")
        runtime_tracked = runtime.get("trackedFiles")
        if not isinstance(source_tracked, Mapping) or not isinstance(runtime_tracked, Mapping):
            raise ValueError("MANIFEST_TRACKED_FILES_INVALID")
        if source.get("trackedManifestSha256") is not None:
            if _hex_hash(source.get("trackedManifestSha256")) != _sha256_json(dict(source_tracked)):
                raise ValueError("SOURCE_TRACKED_MANIFEST_HASH_MISMATCH")
        if runtime.get("trackedManifestSha256") is not None:
            if _hex_hash(runtime.get("trackedManifestSha256")) != _sha256_json(dict(runtime_tracked)):
                raise ValueError("RUNTIME_TRACKED_MANIFEST_HASH_MISMATCH")

        # Recovery worktree が generation の source commit そのものかを Git tree
        # identity で確認する。source tree 全体の blob ID を比較するため、
        # manifest に無い別 commit や改変を Green にできない。
        commit = _run_git(worktree, "rev-parse", "HEAD").strip().lower()
        if commit != source_commit:
            raise ValueError("WORKTREE_COMMIT_MISMATCH")
        source_tree = _git_tree_manifest(worktree, commit)
        if dict(source_tracked) != source_tree:
            raise ValueError("SOURCE_TRACKED_BLOB_MISMATCH")
        if _run_git(worktree, "status", "--porcelain", "--untracked-files=all").strip():
            raise ValueError("WORKTREE_DIRTY")
        # critical file を含む tracked path の実体に symlink/reparse を許さない。
        observed_source: dict[str, str] = {}
        for relative in source_tree:
            if relative in CRITICAL_PATHS:
                source_entry = str(source_tree[relative])
                if ":blob:" not in source_entry:
                    raise ValueError("SOURCE_CRITICAL_NOT_BLOB")
                source_candidate = _safe_relative(
                    worktree, relative, file_required=True
                )
                observed_source[relative] = _sha256_file(source_candidate)

        runtime_root_value = str(runtime.get("root") or "")
        if not runtime_root_value:
            raise ValueError("RUNTIME_ROOT_MISSING")
        runtime_repo = Path(runtime_root_value)
        if not runtime_repo.is_absolute():
            raise ValueError("RUNTIME_ROOT_NOT_ABSOLUTE")
        runtime_repo = _assert_under_regular_tree(
            runtime_repo,
            runtime_authority,
            file_required=False,
        )

        # manifest が封印した runtime trackedFiles をすべて検証する。critical
        # 集合だけを抜き出して検査すると、manifest 内の escape を見逃すためである。
        for relative, expected in runtime_tracked.items():
            if not isinstance(relative, str) or not isinstance(expected, str):
                raise ValueError("RUNTIME_TRACKED_ENTRY_INVALID")
            expected_sha = _hex_hash(expected)
            candidate = _safe_relative(runtime_repo, relative, file_required=True)
            actual_sha = _sha256_file(candidate)
            if actual_sha != expected_sha:
                raise ValueError("RUNTIME_FILE_SHA_MISMATCH")
            if relative in CRITICAL_PATHS:
                observed_runtime[relative] = actual_sha

        if not set(CRITICAL_PATHS).issubset(set(runtime_tracked)):
            raise ValueError("CRITICAL_SET_INCOMPLETE")
        for relative in CRITICAL_PATHS:
            # mapping validation above has already checked bytes; this second lookup
            # gives a deterministic failure code if a duplicate/alias was attempted.
            expected = runtime_tracked.get(relative)
            if not isinstance(expected, str) or _hex_hash(expected) != observed_runtime.get(relative):
                raise ValueError("CRITICAL_SET_SHA_MISMATCH")
            if observed_source.get(relative) != observed_runtime.get(relative):
                raise ValueError("SOURCE_RUNTIME_CRITICAL_SHA_MISMATCH")

        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "green",
            "ok": True,
            "reasonCode": "",
            "worktreeRoot": str(worktree),
            "runtimeRoot": str(runtime_authority),
            "runtimeRepositoryRoot": str(runtime_repo),
            "commit": commit,
            "generationId": generation_id,
            "perFileSha256": dict(sorted(observed_runtime.items())),
            "sourcePerFileSha256": dict(sorted(observed_source.items())),
            "criticalSetSha256": CRITICAL_SET_SHA256,
            "manifestPath": str(manifest_path),
            "manifestSha256": str(manifest_value["manifestSha256"]),
        }
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as error:
        return _failure(
            worktree_root=requested_worktree,
            runtime_root=requested_runtime,
            detail_code=str(error) or error.__class__.__name__,
            manifest_path=manifest_display or requested_pointer,
            commit=commit,
            generation_id=generation_id,
            per_file_sha256=observed_runtime,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="recovery freshness を検証する")
    check.add_argument("--worktree-root", required=True, type=Path)
    check.add_argument("--runtime-root", required=True, type=Path)
    check.add_argument("--active-pointer", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "check":
        return CLI_RED_EXIT
    result = verify_recovery_freshness(
        worktree_root=args.worktree_root,
        runtime_root=args.runtime_root,
        active_pointer_path=args.active_pointer,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "green" else CLI_RED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
