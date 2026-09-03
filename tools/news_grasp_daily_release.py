"""current issue成果物を一つのGit release commitへ固定してpublish sealする。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import news_grasp_direct_runtime as runtime
from tools.news_grasp_daily_external import EXTERNAL_OPERATION_ORDER
from tools.news_grasp_publish_contract import (
    PublishLeaseStore,
    build_publish_manifest,
    load_manifest,
    materialize_manifest_markers,
    verify_manifest,
)


RELEASE_RECEIPT_SCHEMA = "NEWS_GRASP_DAILY_RELEASE_BUNDLE_V1"
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class DailyReleaseError(RuntimeError):
    """release candidate作成前後のtyped failure。"""


def _write_publish_status(
    root: Path,
    *,
    issue_date: str,
    scheduler_trigger_at: str,
    run_id: str,
    run_intent: str,
) -> None:
    target = root / "docs" / "publish-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": issue_date,
        # このfileはPages bundleの到達予定を示すだけであり、公開完了authority
        # ではない。外部副作用後のimmutable attestationをconsumerがfreshに
        # 検証するまではpendingのまま固定する。
        "result": "publication_pending",
        "status": "awaiting_external_completion_attestation",
        "completionAuthority": "consumer-owned_public_verifier",
        "runId": run_id,
        "runIntent": run_intent,
        # retryでmanifest identityが変わらないよう、現在時刻ではなくstart sealの
        # scheduler triggerを公開bundleの安定時刻として使用する。
        "updated_at": scheduler_trigger_at,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _git(root: Path, args: Sequence[str], *, env: Mapping[str, str] | None = None, input_text: str | None = None) -> str:
    child_env = dict(os.environ)
    child_env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if env:
        child_env.update(env)
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        input=input_text,
        stdin=None if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=120,
        check=False,
        shell=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        env=child_env,
    )
    if completed.returncode != 0:
        raise DailyReleaseError(f"GIT_COMMAND_RED:{args[0]}:{completed.returncode}")
    return completed.stdout


def _status_paths(root: Path) -> set[str]:
    output = _git(root, ["-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    paths: set[str] = set()
    records = output.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise DailyReleaseError("RELEASE_GIT_STATUS_INVALID")
        status = record[:2]
        if "R" in status or "C" in status:
            raise DailyReleaseError("RELEASE_RENAME_FORBIDDEN")
        path = record[3:].replace("\\", "/")
        if path:
            paths.add(path)
    return paths


def _release_head_ref(root: Path) -> str:
    """mainまたはdetached専用release checkoutを返し、他branchを拒否する。"""

    try:
        branch_name = _git(root, ["symbolic-ref", "--short", "HEAD"]).strip()
    except DailyReleaseError:
        branch_name = ""
    if branch_name:
        if branch_name != "main":
            raise DailyReleaseError("RELEASE_BRANCH_NOT_MAIN")
        return "refs/heads/main"
    # detached HEADだけを許す。broken HEADはrev-parse側で拒否する。
    head = _git(root, ["rev-parse", "--verify", "HEAD"]).strip().casefold()
    if not _SHA1_RE.fullmatch(head):
        raise DailyReleaseError("RELEASE_HEAD_INVALID")
    return "HEAD"


def _committed_hashes(root: Path, release_sha: str, paths: Sequence[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in paths:
        raw = subprocess.run(
            ["git", "show", f"{release_sha}:{relative}"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
            shell=False,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        if raw.returncode != 0:
            raise DailyReleaseError(f"RELEASE_COMMITTED_FILE_MISSING:{relative}")
        hashes[relative] = hashlib.sha256(raw.stdout).hexdigest()
    return hashes


def _committed_paths(root: Path, release_sha: str) -> set[str]:
    """release commit が実際に変更したpath集合を返す。

    reuse時にmessage/parentだけが一致する別commitを受理しないため、rename
    detectionへ依存せず旧/新pathを列挙できる ``--no-renames`` を固定する。
    """

    output = _git(
        root,
        [
            "-c",
            "core.quotepath=false",
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "--no-renames",
            "-r",
            "-z",
            release_sha,
        ],
    )
    return {item.replace("\\", "/") for item in output.split("\0") if item}


def _create_exact_commit(root: Path, *, paths: Sequence[str], expected_parent: str, issue_date: str, run_id: str) -> str:
    branch_ref = _release_head_ref(root)
    observed_head = _git(root, ["rev-parse", "HEAD"]).strip().casefold()
    if observed_head != expected_parent:
        raise DailyReleaseError("RELEASE_SOURCE_BASELINE_DRIFT")
    dirty = _status_paths(root)
    unexpected = sorted(dirty - set(paths))
    if unexpected:
        raise DailyReleaseError("RELEASE_UNEXPECTED_DIRTY_PATH:" + "|".join(unexpected[:10]))
    with tempfile.TemporaryDirectory(prefix="news-grasp-release-index-") as raw:
        index = Path(raw) / "index"
        index_env = {"GIT_INDEX_FILE": str(index)}
        _git(root, ["read-tree", observed_head], env=index_env)
        _git(root, ["add", "--", *paths], env=index_env)
        staged = {
            item for item in _git(root, ["diff", "--cached", "--name-only", "-z"], env=index_env).split("\0") if item
        }
        if not staged:
            raise DailyReleaseError("RELEASE_COMMIT_EMPTY")
        if not staged.issubset(set(paths)) or dirty != staged:
            raise DailyReleaseError("RELEASE_STAGED_WRITE_SET_MISMATCH")
        tree = _git(root, ["write-tree"], env=index_env).strip()
        message = f"Publish {issue_date} News-Grasp daily release [{run_id}]\n"
        release_sha = _git(root, ["commit-tree", tree, "-p", observed_head], env=index_env, input_text=message).strip().casefold()
    if not _SHA1_RE.fullmatch(release_sha):
        raise DailyReleaseError("RELEASE_COMMIT_SHA_INVALID")
    _git(root, ["update-ref", branch_ref, release_sha, observed_head])
    # controlled write setしかdirtyでないことを上で確認済み。real indexを新treeへ
    # 移し、worktree bytesは変更せずcommit後のclean状態へ同期する。
    _git(root, ["read-tree", release_sha])
    if _status_paths(root):
        raise DailyReleaseError("RELEASE_WORKTREE_NOT_CLEAN_AFTER_COMMIT")
    return release_sha


def _reuse_exact_commit(
    root: Path,
    *,
    paths: Sequence[str],
    expected_parent: str,
    issue_date: str,
    run_id: str,
) -> str | None:
    """update-ref後に停止しても同一runのrelease commitだけを再利用する。"""

    observed_head = _git(root, ["rev-parse", "HEAD"]).strip().casefold()
    _release_head_ref(root)
    if observed_head == expected_parent:
        return None
    parent = _git(root, ["rev-parse", f"{observed_head}^"]).strip().casefold()
    expected_message = f"Publish {issue_date} News-Grasp daily release [{run_id}]"
    message = _git(root, ["log", "-1", "--format=%B", observed_head]).strip()
    if parent != expected_parent or message != expected_message:
        raise DailyReleaseError("RELEASE_SOURCE_BASELINE_DRIFT")
    committed_paths = _committed_paths(root, observed_head)
    unexpected_committed = sorted(committed_paths - set(paths))
    if unexpected_committed:
        raise DailyReleaseError(
            "RELEASE_REUSE_WRITE_SET_MISMATCH:" + "|".join(unexpected_committed[:10])
        )
    if not committed_paths:
        raise DailyReleaseError("RELEASE_REUSE_COMMIT_EMPTY")
    committed_hashes = _committed_hashes(root, observed_head, paths)
    for relative, expected_hash in committed_hashes.items():
        candidate = root / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise DailyReleaseError(f"RELEASE_REUSE_WORKTREE_FILE_INVALID:{relative}")
        observed_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if observed_hash != expected_hash:
            raise DailyReleaseError(f"RELEASE_REUSE_WORKTREE_DRIFT:{relative}")
    # update-ref成功後・real index同期前の停止だけを安全に回復する。
    # worktree bytesがcommitと完全一致すると確認した後にindexだけを進める。
    dirty = _status_paths(root)
    if not dirty.issubset(set(paths)):
        raise DailyReleaseError("RELEASE_UNEXPECTED_DIRTY_PATH:" + "|".join(sorted(dirty - set(paths))[:10]))
    if dirty:
        _git(root, ["read-tree", observed_head])
    if _status_paths(root):
        raise DailyReleaseError("RELEASE_WORKTREE_NOT_CLEAN_AFTER_REUSE")
    return observed_head


def _preflight_head_identity(
    root: Path,
    *,
    expected_parent: str,
    issue_date: str,
    run_id: str,
) -> str:
    """worktree mutation前にbaselineまたは同run reusable commitだけを許可する。"""

    _release_head_ref(root)
    observed_head = _git(root, ["rev-parse", "HEAD"]).strip().casefold()
    if observed_head == expected_parent:
        return "baseline"
    parent = _git(root, ["rev-parse", f"{observed_head}^"]).strip().casefold()
    message = _git(root, ["log", "-1", "--format=%B", observed_head]).strip()
    expected_message = f"Publish {issue_date} News-Grasp daily release [{run_id}]"
    if parent != expected_parent or message != expected_message:
        raise DailyReleaseError("RELEASE_SOURCE_BASELINE_DRIFT")
    return "reusable_commit"


def materialize_and_seal_release(
    *,
    store: runtime.DirectRunStore,
    repo_root: str | Path,
    issue_date: str,
    run_id: str,
    run_intent: str,
    writer_lease: str,
    fencing_token: int,
    content_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """marker→exact commit→Git tree hash→publish sealを一方向に確定する。"""

    root = Path(repo_root).resolve(strict=True)
    state = runtime.inspect_run(store, run_id=run_id)
    start = state.get("start_seal") if isinstance(state.get("start_seal"), Mapping) else {}
    source_baseline = str(start.get("sourceBaseline") or "").casefold()
    remote_base_sha = str(start.get("remoteBaseSha") or "").casefold()
    scheduler_trigger_at = str(start.get("schedulerTriggerAt") or "")
    if not _SHA1_RE.fullmatch(source_baseline) or not _SHA1_RE.fullmatch(remote_base_sha):
        raise DailyReleaseError("RELEASE_START_SEAL_IDENTITY_INVALID")
    if _git(root, ["rev-parse", "origin/main"]).strip().casefold() != remote_base_sha:
        raise DailyReleaseError("RELEASE_REMOTE_BASE_DRIFT")
    if content_receipt.get("ok") is not True or content_receipt.get("run_id") != run_id:
        raise DailyReleaseError("RELEASE_CONTENT_RECEIPT_INVALID")
    _preflight_head_identity(
        root,
        expected_parent=source_baseline,
        issue_date=issue_date,
        run_id=run_id,
    )

    _write_publish_status(
        root,
        issue_date=issue_date,
        scheduler_trigger_at=scheduler_trigger_at,
        run_id=run_id,
        run_intent=run_intent,
    )

    manifest = build_publish_manifest(
        repo_root=root,
        issue_date=issue_date,
        run_id=run_id,
        run_intent=run_intent,
        source_baseline=source_baseline,
    )
    lease_store = PublishLeaseStore(
        store.state_root,
        test_only_allow_noncanonical=store.test_only_allow_semantic_verifier,
    )
    materialize_manifest_markers(
        root,
        manifest,
        lease_store=lease_store,
        writer_lease=writer_lease,
        test_only_allow_noncanonical_lease_store=store.test_only_allow_semantic_verifier,
    )
    materialized = load_manifest(root, issue_date)
    content_binding = materialized.get("contentReceiptBinding")
    if (
        not isinstance(content_binding, Mapping)
        or content_binding.get("schemaVersion") != "NEWS_GRASP_MANIFEST_CONTENT_BINDING_V1"
        or content_binding.get("issueDate") != issue_date
        or content_binding.get("runId") != run_id
    ):
        raise DailyReleaseError("RELEASE_CONTENT_RECEIPT_BINDING_MISSING")
    verified = verify_manifest(materialized, repo_root=root, require_files=True)
    if verified.get("ok") is not True:
        raise DailyReleaseError("RELEASE_MANIFEST_RED:" + "|".join(verified.get("reasonCodes") or ()))
    write_set = [str(item) for item in materialized.get("exactWriteSet") or ()]
    release_sha = _reuse_exact_commit(
        root,
        paths=write_set,
        expected_parent=source_baseline,
        issue_date=issue_date,
        run_id=run_id,
    )
    if release_sha is None:
        release_sha = _create_exact_commit(
            root,
            paths=write_set,
            expected_parent=source_baseline,
            issue_date=issue_date,
            run_id=run_id,
        )
    file_hashes = _committed_hashes(root, release_sha, write_set)
    canonical_external_inputs = (
        f"build/tts/{issue_date}.mp3",
        f"build/tts/deepdive/{issue_date}.mp3",
        f"build/youtube-podcast/{issue_date}.mp4",
        f"build/youtube-podcast-deepdive/{issue_date}.mp4",
    )
    derived_hashes = content_receipt.get("derived_artifact_hashes")
    if not isinstance(derived_hashes, Mapping):
        raise DailyReleaseError("RELEASE_EXTERNAL_INPUT_RECEIPT_INVALID")
    external_input_hashes: dict[str, str] = {}
    for relative in canonical_external_inputs:
        expected_hash = str(derived_hashes.get(relative) or "").casefold()
        candidate = root / relative
        if (
            re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
            or not candidate.is_file()
            or candidate.is_symlink()
            or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected_hash
        ):
            raise DailyReleaseError(f"RELEASE_EXTERNAL_INPUT_DRIFT:{relative}")
        external_input_hashes[relative] = expected_hash
    bundle_id = hashlib.sha256(
        json.dumps(
            {
                "content_bundle_id": content_receipt.get("bundle_id"),
                "manifest_id": materialized["manifestId"],
                "release_commit_sha": release_sha,
                "file_hashes": file_hashes,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    seal = runtime.seal_publish(
        store,
        run_id=run_id,
        writer_lease=writer_lease,
        fencing_token=fencing_token,
        release_commit_sha=release_sha,
        exact_write_set=write_set,
        file_hashes=file_hashes,
        manifest_id=str(materialized["manifestId"]),
        bundle_id=bundle_id,
        external_operation_ids=EXTERNAL_OPERATION_ORDER,
        external_input_hashes=external_input_hashes,
    )
    return {
        "schemaVersion": RELEASE_RECEIPT_SCHEMA,
        "ok": True,
        "status": "sealed",
        "issue_date": issue_date,
        "run_id": run_id,
        "run_intent": run_intent,
        "source_baseline": source_baseline,
        "remote_base_sha": remote_base_sha,
        "release_commit_sha": release_sha,
        "manifest_id": str(materialized["manifestId"]),
        "bundle_id": bundle_id,
        "exact_write_set": write_set,
        "file_hashes": file_hashes,
        "external_input_hashes": external_input_hashes,
        "publish_seal": seal,
    }


__all__ = ["RELEASE_RECEIPT_SCHEMA", "DailyReleaseError", "materialize_and_seal_release"]
