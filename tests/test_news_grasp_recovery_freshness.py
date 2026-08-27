"""RC-03 recovery worktree/tooling freshness の Red/Green fixture。

このテストは recovery runner の child process を起動しない pre-spawn gate を
固定する。fixture は ``PRODUCTION_GENERATION_MANIFEST_V2`` と
``NEWS_GRASP_ACTIVE_GENERATION_V2`` を一時的な git repository/runtime root に
封印し、source commit、worktree bytes、runtime bytes、critical set の全てを
同じ検証結果へ束縛する。

予定する product consumer 契約（実装側が満たすべき形）:

``verify_recovery_freshness(
    worktree_root, runtime_root, active_pointer_path=None
) -> dict``

Green result は ``status=green``, ``ok=True`` とし、typed result に
``commit``, ``criticalSetSha256``, ``perFileSha256`` を含める。
Red は例外または ``ok=False`` の typed result
で、少なくとも要求された reason code を公開する。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
ISSUE_DATE = "2026-08-27"
RUN_INTENT = "ScheduledRecoveryFull"
CRITICAL_PATHS = (
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
POINTER_SCHEMA = "NEWS_GRASP_ACTIVE_GENERATION_V2"
MANIFEST_SCHEMA = "PRODUCTION_GENERATION_MANIFEST_V2"
MISMATCH = "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH"
INVALID = "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_INVALID"
CRITICAL_SET_MISMATCH = "RECOVERY_DEEPDIVE_CRITICAL_SET_MISMATCH"
PATH_ESCAPE = "RECOVERY_DEEPDIVE_RUNTIME_PATH_ESCAPE"


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value) + b"\n")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout.strip()


def _git_tracked_manifest(repo: Path, head: str) -> dict[str, str]:
    raw = subprocess.run(
        ["git", "ls-tree", "-r", "--full-tree", "-z", head],
        cwd=repo,
        shell=False,
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    result: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        identity, relative = record.split(b"\t", 1)
        mode, object_type, object_id = identity.decode("ascii").split(" ", 2)
        result[relative.decode("utf-8")] = f"{mode}:{object_type}:{object_id}"
    return result


def _seal_manifest(path: Path, manifest: dict[str, Any]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("manifestSha256", None)
    manifest_sha = _sha(unsigned)
    manifest["manifestSha256"] = manifest_sha
    _write_json(path, manifest)
    return manifest_sha


def _seal_pointer(path: Path, pointer: dict[str, Any]) -> str:
    unsigned = dict(pointer)
    unsigned.pop("pointerSha256", None)
    pointer_sha = _sha(unsigned)
    pointer["pointerSha256"] = pointer_sha
    _write_json(path, pointer)
    return pointer_sha


@dataclass(frozen=True)
class FreshnessFixture:
    worktree_root: Path
    runtime_root: Path
    active_pointer_path: Path
    manifest_path: Path
    commit: str
    runtime_sha_by_path: dict[str, str]


def _fixture(
    tmp_path: Path,
    *,
    critical_paths: tuple[str, ...] = CRITICAL_PATHS,
) -> FreshnessFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "recovery-worktree"
    runtime = tmp_path / "runtime-root"
    worktree.mkdir()
    runtime.mkdir()

    for index, relative in enumerate(CRITICAL_PATHS, start=1):
        source = worktree / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            f"News-Grasp RC-03 fixture {index}: {relative}\n",
            encoding="utf-8",
        )
        installed = runtime / relative
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(source.read_bytes())

    _git(worktree, "init", "--initial-branch=main", "-q")
    _git(worktree, "config", "user.email", "news-grasp-rc03@example.invalid")
    _git(worktree, "config", "user.name", "News-Grasp RC-03 Fixture")
    _git(worktree, "add", "--all")
    _git(worktree, "commit", "--no-gpg-sign", "-m", "rc03 freshness fixture", "-q")
    commit = _git(worktree, "rev-parse", "HEAD")
    source_tracked = _git_tracked_manifest(worktree, commit)
    runtime_sha_by_path = {
        relative: _sha_file(runtime / relative) for relative in CRITICAL_PATHS
    }

    manifest_path = runtime / "generations" / "generation-rc03.json"
    manifest: dict[str, Any] = {
        "schemaVersion": MANIFEST_SCHEMA,
        "productId": "News-Grasp",
        "generationId": "generation-rc03",
        "source": {
            "commit": commit,
            "observedHead": commit,
            "remoteHead": commit,
            "origin": "origin/main",
            "root": str(worktree),
            "trackedFiles": source_tracked,
            "trackedManifestSha256": _sha(source_tracked),
        },
        "runtime": {
            "root": str(runtime),
            "commit": commit,
            "trackedFiles": runtime_sha_by_path,
            "trackedManifestSha256": _sha(runtime_sha_by_path),
        },
        "criticalPaths": list(critical_paths),
        "criticalSetSha256": _sha(list(critical_paths)),
        "recovery": {
            "issueDate": ISSUE_DATE,
            "runIntent": RUN_INTENT,
            "criticalPaths": list(critical_paths),
            "criticalSetSha256": _sha(list(critical_paths)),
        },
    }
    manifest_sha = _seal_manifest(manifest_path, manifest)

    pointer_path = runtime / "active-generation-v2.json"
    pointer: dict[str, Any] = {
        "schemaVersion": POINTER_SCHEMA,
        "generationId": "generation-rc03",
        "manifestPath": str(manifest_path),
        "manifestSha256": manifest_sha,
        "phase": "transaction_committed",
    }
    _seal_pointer(pointer_path, pointer)
    return FreshnessFixture(
        worktree_root=worktree,
        runtime_root=runtime,
        active_pointer_path=pointer_path,
        manifest_path=manifest_path,
        commit=commit,
        runtime_sha_by_path=runtime_sha_by_path,
    )


def _freshness_module() -> Any:
    """baselineでは明示的な implementation-missing Red を出す。"""
    try:
        from tools import news_grasp_recovery_freshness
    except ModuleNotFoundError as error:
        pytest.fail(
            "NG_RC03_IMPLEMENTATION_MISSING: "
            "tools.news_grasp_recovery_freshness.verify_recovery_freshness "
            "is not implemented",
            pytrace=False,
        )
        raise AssertionError from error
    return news_grasp_recovery_freshness


def _verify(
    fixture: FreshnessFixture,
) -> Mapping[str, Any]:
    module = _freshness_module()
    verifier = getattr(module, "verify_recovery_freshness", None)
    assert callable(verifier), "NG_RC03_VERIFY_API_MISSING"
    result = verifier(
        worktree_root=fixture.worktree_root,
        runtime_root=fixture.runtime_root,
        active_pointer_path=fixture.active_pointer_path,
    )
    assert isinstance(result, Mapping), "NG_RC03_RESULT_NOT_TYPED"
    return result


def _pre_spawn_gate(
    fixture: FreshnessFixture,
    spawn_callback: Callable[[], object],
) -> Mapping[str, Any]:
    """runner caller 側の pre-spawn contract を child無しで再現する。"""
    result = _verify(fixture)
    if result.get("status") == "green":
        spawn_callback()
    return result


def _expect_red(
    call: Callable[[], Mapping[str, Any]],
    *reason_codes: str,
) -> Mapping[str, Any] | None:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001 - typed product error is the oracle
        text = str(error)
        assert any(code in text for code in reason_codes), (
            f"NG_RC03_TYPED_RED_MISSING expected={reason_codes!r} actual={text!r}"
        )
        return None
    assert result.get("ok") is False or str(result.get("status", "")).lower() not in {
        "green",
        "ready",
    }, f"NG_RC03_RED_ACCEPTED_AS_GREEN result={result!r}"
    rendered = json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
    assert any(
        code in rendered for code in reason_codes
    ), f"NG_RC03_TYPED_RED_MISSING expected={reason_codes!r} result={result!r}"
    return result


def _expect_green(result: Mapping[str, Any]) -> Mapping[str, Any]:
    assert result.get("ok") is True, f"NG_RC03_GREEN_ORACLE_FAILED result={result!r}"
    assert str(result.get("status", "")).lower() in {"green", "ready"}
    return result


def _reseal_manifest_and_pointer(fixture: FreshnessFixture, manifest: dict[str, Any]) -> None:
    manifest_sha = _seal_manifest(fixture.manifest_path, manifest)
    pointer = json.loads(fixture.active_pointer_path.read_text(encoding="utf-8"))
    pointer["manifestSha256"] = manifest_sha
    _seal_pointer(fixture.active_pointer_path, pointer)


def test_stale_worktree_file_is_rejected_before_spawn_callback(tmp_path: Path) -> None:
    """primary/admission Red: stale tooling must stop before a child can spawn."""
    fixture = _fixture(tmp_path)
    stale = fixture.worktree_root / CRITICAL_PATHS[0]
    stale.write_text("stale recovery tooling\n", encoding="utf-8")
    spawn_count = 0

    def spawn_sentinel() -> object:
        nonlocal spawn_count
        spawn_count += 1
        return "must-not-run"

    _expect_red(
        lambda: _pre_spawn_gate(fixture, spawn_sentinel),
        MISMATCH,
    )
    assert spawn_count == 0, "NG_RC03_SPAWN_BEFORE_FRESHNESS_GATE"


def test_untracked_sitecustomize_is_rejected_before_spawn_callback(tmp_path: Path) -> None:
    """adversarial: tracked SHA一致でもuntracked import hookを起動前に拒否する。"""

    fixture = _fixture(tmp_path)
    (fixture.worktree_root / "sitecustomize.py").write_text(
        "raise RuntimeError('must not import')\n", encoding="utf-8"
    )
    spawn_count = 0

    def spawn_sentinel() -> object:
        nonlocal spawn_count
        spawn_count += 1
        return "must-not-run"

    _expect_red(lambda: _pre_spawn_gate(fixture, spawn_sentinel), MISMATCH)
    assert spawn_count == 0


def test_git_timeout_is_typed_red_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """recovery: Git observation timeoutをGreen/無期限waitへ変換しない。"""

    fixture = _fixture(tmp_path)
    module = _freshness_module()

    def timeout(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(module.subprocess, "run", timeout)
    result = _expect_red(lambda: _verify(fixture), MISMATCH)
    assert result is not None
    assert "GIT_TIMEOUT" in json.dumps(dict(result), ensure_ascii=False)


def test_exact_sha_parity_is_green_with_per_file_and_set_evidence(tmp_path: Path) -> None:
    """Green: critical file SHA、critical set、current HEAD が一つの証拠に残る。"""
    fixture = _fixture(tmp_path)
    result = _expect_green(_verify(fixture))
    assert result.get("commit") == fixture.commit
    assert result.get("criticalSetSha256") == _sha(list(CRITICAL_PATHS))
    assert result.get("perFileSha256") == fixture.runtime_sha_by_path
    assert result.get("sourcePerFileSha256") == fixture.runtime_sha_by_path


def test_self_consistent_stale_runtime_manifest_cannot_bypass_source_cross_sha(
    tmp_path: Path,
) -> None:
    """adversarial: stale runtimeを自己整合manifestへ再封印してもRed。"""

    fixture = _fixture(tmp_path)
    target = fixture.runtime_root / CRITICAL_PATHS[0]
    target.write_text("self-consistent stale runtime\n", encoding="utf-8")
    manifest = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"]["trackedFiles"][CRITICAL_PATHS[0]] = _sha_file(target)
    manifest["runtime"]["trackedManifestSha256"] = _sha(
        manifest["runtime"]["trackedFiles"]
    )
    _reseal_manifest_and_pointer(fixture, manifest)

    result = _expect_red(lambda: _verify(fixture), MISMATCH)
    assert result is not None
    assert "SOURCE_RUNTIME_CRITICAL_SHA_MISMATCH" in str(
        result.get("detailCode") or ""
    )


def test_missing_and_tampered_generation_inputs_fail_closed(tmp_path: Path) -> None:
    missing = _fixture(tmp_path / "missing")
    missing.active_pointer_path.unlink()
    _expect_red(lambda: _verify(missing), MISMATCH)

    tampered_pointer = _fixture(tmp_path / "pointer")
    pointer = json.loads(
        tampered_pointer.active_pointer_path.read_text(encoding="utf-8")
    )
    pointer["generationId"] = "generation-attacker"
    tampered_pointer.active_pointer_path.write_bytes(
        _json_bytes(pointer) + b"\n"
    )
    _expect_red(lambda: _verify(tampered_pointer), MISMATCH)

    tampered_manifest = _fixture(tmp_path / "manifest")
    manifest = json.loads(tampered_manifest.manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"]["trackedFiles"][CRITICAL_PATHS[0]] = "0" * 64
    # manifestSha256 は意図的に旧値のままにして、sealed manifest tamper を検出させる。
    tampered_manifest.manifest_path.write_bytes(_json_bytes(manifest) + b"\n")
    _expect_red(lambda: _verify(tampered_manifest), MISMATCH)


def test_trusted_manifest_critical_set_mismatch_is_typed_red(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    shortened = list(CRITICAL_PATHS[:-1])
    # trusted manifest の runtime tracked set から critical file を落とす。
    # top-level の説明用 criticalPaths だけを変えても検証をすり抜けないことを
    # 同時に確認するため、両方を短縮して manifest 自体は再封印する。
    manifest["criticalPaths"] = shortened
    manifest["criticalSetSha256"] = _sha(shortened)
    manifest["recovery"]["criticalPaths"] = shortened
    manifest["recovery"]["criticalSetSha256"] = _sha(shortened)
    manifest["runtime"]["trackedFiles"].pop(CRITICAL_PATHS[-1])
    manifest["runtime"]["trackedManifestSha256"] = _sha(
        manifest["runtime"]["trackedFiles"]
    )
    _reseal_manifest_and_pointer(fixture, manifest)
    _expect_red(
        lambda: _verify(fixture),
        CRITICAL_SET_MISMATCH,
        MISMATCH,
    )


def test_current_exact_commit_is_checked(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    wrong_commit = "f" * 40
    manifest = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["commit"] = wrong_commit
    manifest["runtime"]["commit"] = wrong_commit
    _reseal_manifest_and_pointer(fixture, manifest)
    _expect_red(lambda: _verify(fixture), MISMATCH)


def test_manifest_rejects_path_escape_before_child_spawn(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    manifest = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    escaped = [*CRITICAL_PATHS[:-1], "../outside.py"]
    manifest["criticalPaths"] = escaped
    manifest["criticalSetSha256"] = _sha(escaped)
    manifest["recovery"]["criticalPaths"] = escaped
    manifest["recovery"]["criticalSetSha256"] = _sha(escaped)
    manifest["runtime"]["trackedFiles"]["../outside.py"] = _sha_file(outside)
    manifest["runtime"]["trackedManifestSha256"] = _sha(
        manifest["runtime"]["trackedFiles"]
    )
    _reseal_manifest_and_pointer(fixture, manifest)
    spawn_count = 0

    def spawn_sentinel() -> object:
        nonlocal spawn_count
        spawn_count += 1
        return "must-not-run"

    _expect_red(lambda: _verify(fixture), PATH_ESCAPE, MISMATCH)
    assert spawn_count == 0, "NG_RC03_SPAWN_BEFORE_PATH_BOUNDARY"


def test_symlinked_critical_file_is_rejected_when_platform_allows_symlink(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    target = tmp_path / "outside-tool.py"
    target.write_text("outside tool\n", encoding="utf-8")
    candidate = fixture.worktree_root / CRITICAL_PATHS[0]
    replacement = tmp_path / "original-tool.py"
    candidate.replace(replacement)
    try:
        os.symlink(target, candidate)
    except OSError as error:
        replacement.replace(candidate)
        # Windows の通常ユーザーでは file symlink 権限が無いことがある。
        # その場合も directory junction（reparse point）で同じ境界を実測し、
        # fixture を単なる skip にしない。mklink は一時dir配下だけを対象に
        # shell=False/CREATE_NO_WINDOW で起動する。
        if os.name != "nt":
            pytest.skip(f"symlink privilege unavailable: {error}")
        original_tools = fixture.worktree_root / "tools"
        junction_target = tmp_path / "outside-tools"
        original_tools_backup = tmp_path / "original-tools"
        original_tools.rename(original_tools_backup)
        junction_target.mkdir()
        (junction_target / "deepdive_quality.py").write_text(
            "outside junction target\n", encoding="utf-8"
        )
        junction = fixture.worktree_root / "tools"
        comspec = os.environ.get("ComSpec", r"C:\\Windows\\System32\\cmd.exe")
        created = subprocess.run(
            [comspec, "/d", "/c", "mklink", "/J", str(junction), str(junction_target)],
            shell=False,
            check=False,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if created.returncode != 0:
            original_tools_backup.rename(original_tools)
            pytest.skip(
                "symlink and junction privilege unavailable: "
                f"{error}; mklink exit={created.returncode}"
            )
        try:
            _expect_red(lambda: _verify(fixture), PATH_ESCAPE, MISMATCH)
        finally:
            # junction 自体を解消してから元の fixture directory を戻す。
            junction.rmdir()
            original_tools_backup.rename(original_tools)
        return
    manifest = json.loads(fixture.manifest_path.read_text(encoding="utf-8"))
    # sealed manifest は bytes/hash が正しくても、reparse/symlink 境界で拒否する。
    manifest["runtime"]["trackedFiles"][CRITICAL_PATHS[0]] = _sha_file(target)
    manifest["runtime"]["trackedManifestSha256"] = _sha(
        manifest["runtime"]["trackedFiles"]
    )
    _reseal_manifest_and_pointer(fixture, manifest)
    _expect_red(lambda: _verify(fixture), PATH_ESCAPE, MISMATCH)


def _cli(
    fixture: FreshnessFixture,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.news_grasp_recovery_freshness",
            "check",
            "--worktree-root",
            str(fixture.worktree_root),
            "--runtime-root",
            str(fixture.runtime_root),
            "--active-pointer",
            str(fixture.active_pointer_path),
        ],
        cwd=ROOT,
        env=env,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _last_json(stdout: str, stderr: str) -> Mapping[str, Any]:
    for line in reversed([*stdout.splitlines(), *stderr.splitlines()]):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, Mapping):
            return value
    pytest.fail(f"NG_RC03_CLI_JSON_MISSING stdout={stdout!r} stderr={stderr!r}")


def test_cli_green_and_cli_stale_red_use_same_freshness_predicate(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    green = _cli(fixture)
    assert green.returncode == 0, green.stderr
    green_payload = _last_json(green.stdout, green.stderr)
    assert green_payload.get("ok") is True
    assert green_payload.get("status") in {"Green", "green", "ready"}
    assert green_payload.get("commit") == fixture.commit

    stale = fixture.worktree_root / CRITICAL_PATHS[1]
    stale.write_text("CLI stale tooling\n", encoding="utf-8")
    red = _cli(fixture)
    assert red.returncode != 0
    red_payload = _last_json(red.stdout, red.stderr)
    assert red_payload.get("ok") is False or str(red_payload.get("status", "")).lower() not in {
        "green",
        "ready",
    }
    rendered = json.dumps(dict(red_payload), ensure_ascii=False)
    assert MISMATCH in rendered


def test_audit_and_runner_consume_freshness_before_recovery_child_admission() -> None:
    """consumer contract: audit/runnerの両入口が同じpredicateをchild前に使う。"""

    audit = (ROOT / "tools" / "audit_recovery_control.py").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "ops" / "news-grasp-runner.ps1").read_text(
        encoding="utf-8-sig"
    )

    audit_gate = audit.index("news_grasp_recovery_freshness.verify_recovery_freshness")
    audit_receipt = audit.index("execution_receipt = _issue_recovery_execution_receipt")
    audit_recovery_spawn = audit.index("return_code, _ = _run_bounded(command")
    assert audit_gate < audit_receipt < audit_recovery_spawn
    assert "RECOVERY_DEEPDIVE_RUNTIME_FRESHNESS_MISMATCH" in audit[
        audit_gate:audit_receipt
    ]

    runner_gate = runner.index("tools\\news_grasp_recovery_freshness.py")
    runner_receipt = runner.index("function Invoke-RecoveryReceiptValidation")
    runner_materializer = runner.index("current DeepDive issue bundle materialization")
    assert runner_gate < runner_receipt < runner_materializer
    assert "exit 78" in runner[runner_gate:runner_receipt]
