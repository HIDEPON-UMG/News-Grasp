"""Daily本線をlegacy runnerから切り離すための最小cutover contract。"""

from __future__ import annotations

import ast
import importlib
import importlib.machinery
import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
FIXED_PYTHON = Path(
    r"C:\Program Files\Python312\python.exe"
)


def _read(path: Path) -> str:
    return path.resolve(strict=True).read_text(encoding="utf-8-sig")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_read(path), filename=str(path))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"function {name!r} must have exactly one definition"
    node = matches[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _string_constants(node: ast.AST) -> list[str]:
    return [
        item.value
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    ]


def _call_name(node: ast.Call) -> str:
    """AST callの末尾symbol名を取得する。"""

    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return ""


def _mode_branch(function: ast.FunctionDef, mode: str) -> ast.If:
    """main内の ``if args.mode == <mode>`` branchを取得する。"""

    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        condition = node.test
        if not isinstance(condition, ast.Compare) or len(condition.ops) != 1:
            continue
        if not isinstance(condition.ops[0], ast.Eq):
            continue
        left = condition.left
        right = condition.comparators[0]
        if (
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "args"
            and left.attr == "mode"
            and isinstance(right, ast.Constant)
            and right.value == mode
        ):
            return node
    raise AssertionError(f"main mode branch {mode!r} is missing")


def _contains_name(node: ast.AST, *names: str) -> bool:
    expected = set(names)
    return any(
        isinstance(item, ast.Name) and item.id in expected
        for item in ast.walk(node)
    )


def _load_pyw(path: Path) -> ModuleType:
    module_name = f"_news_grasp_cutover_{path.stem.replace('-', '_')}"
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_file_location(module_name, path, loader=loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ps_assignment_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\s*\${re.escape(name)}\s*=\s*@\((.*?)^\s*\)",
        source,
    )
    assert match is not None, f"PowerShell array assignment ${name} is missing"
    return match.group(1)


def _ps_ordered_body(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^\s*\${re.escape(name)}\s*=\s*\[ordered\]@\{{(.*?)^\s*\}}",
        source,
    )
    assert match is not None, f"PowerShell ordered binding ${name} is missing"
    return match.group(1)


def _assert_zero_external_effects(receipt: Mapping[str, Any]) -> None:
    """外部副作用の表記揺れを正規化し、全カウンタをゼロで要求する。"""

    def value(*names: str) -> Any:
        for name in names:
            if name in receipt:
                return receipt[name]
        return None

    assert value("external_effect_count", "externalEffectCount") == 0
    assert value("duplicate_send_count", "duplicateSendCount") == 0
    assert value("duplicate_upload_count", "duplicateUploadCount") == 0


def test_NG_CUTOVER_01_installer_managed_files_exclude_legacy_runner() -> None:
    """installerのmanaged file集合から存在しないlegacy runnerを除外する。"""

    source = _read(OPS / "install-news-grasp-ops.ps1")
    files = _ps_assignment_body(source, "files")
    assert re.search(
        r"(?m)^\s*'news-grasp-runner\.ps1',?\s*$",
        files,
    ) is None
    assert "news-grasp-task-launcher.pyw" in files


def test_NG_CUTOVER_02_recovery_binding_targets_stable_task_launcher() -> None:
    """recovery bindingのrunner identityをstable task launcherへ束縛する。"""

    source = _read(OPS / "install-news-grasp-ops.ps1")
    binding = _ps_ordered_body(source, "recoveryRuntimeBinding")
    runner_path = re.search(
        r"(?m)^\s*runnerPath\s*=\s*\(Join-Path\s+\$BinDir\s+'([^']+)'\)",
        binding,
    )
    runner_sha = re.search(
        r"(?m)^\s*runnerSha256\s*=.*?\$sourceSnapshots\['([^']+)'\]",
        binding,
    )
    assert runner_path is not None and runner_path.group(1) == "news-grasp-task-launcher.pyw"
    assert runner_sha is not None and runner_sha.group(1) == "news-grasp-task-launcher.pyw"


def test_NG_CUTOVER_03_scheduled_runner_child_uses_fixed_python_daily_launcher_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """scheduled childはresolved runtimeのdaily launcherを固定Pythonで一回だけ起動する。"""

    module = _load_pyw(OPS / "news-grasp-task-launcher.pyw")
    runtime_root = (tmp_path / "resolved-runtime").resolve()
    (runtime_root / "tools").mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "resolve_bootstrap_launch_roots",
        lambda **_kwargs: {"configuredRuntime": runtime_root},
    )
    authority = {"action": [str(FIXED_PYTHON)]}
    command, safety = module._cleanroom_child_command(
        route="runner",
        bin_dir=tmp_path,
        authority=authority,
    )

    assert command == [
        str(FIXED_PYTHON),
        "-I",
        "-S",
        "-B",
        str(runtime_root / "tools" / "news_grasp_daily_launcher.py"),
    ]
    assert safety["cwd"] == str(runtime_root)
    assert safety["shell"] is False
    assert safety["creationflags"] == getattr(module.subprocess, "CREATE_NO_WINDOW", 0)


def test_NG_CUTOVER_04_task_launcher_bootstrap_mode_has_no_legacy_script_child() -> None:
    """mainのchild routeは絶対daily launcherへ固定し、legacy scriptを起動しない。"""

    tree = _tree(OPS / "news-grasp-task-launcher.pyw")
    main = _function(tree, "main")
    strings = _string_constants(main)
    assert "news-grasp-bootstrap.ps1" not in strings
    assert "news-grasp-runner.ps1" not in strings
    assert "watch-news-grasp-runner.ps1" not in strings

    def path_parts(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return path_parts(node.left) + path_parts(node.right)
        return []

    direct_entry = next(
        (
            node.value
            for node in ast.walk(main)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "direct_entry"
                for target in node.targets
            )
        ),
        None,
    )
    assert path_parts(direct_entry) == [
        "runtime_repo",
        "tools",
        "news_grasp_daily_launcher.py",
    ]

    command = next(
        (
            node.value
            for node in ast.walk(main)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "command"
                for target in node.targets
            )
            and isinstance(node.value, ast.List)
            and len(node.value.elts) == 5
            and isinstance(node.value.elts[-1], ast.Call)
            and _call_name(node.value.elts[-1]) == "str"
            and node.value.elts[-1].args
            and isinstance(node.value.elts[-1].args[0], ast.Name)
            and node.value.elts[-1].args[0].id == "direct_entry"
        ),
        None,
    )
    assert isinstance(command, ast.List)
    assert isinstance(command.elts[0], ast.Call)
    assert _call_name(command.elts[0]) == "str"
    assert command.elts[0].args and isinstance(command.elts[0].args[0], ast.Name)
    assert command.elts[0].args[0].id == "python_exe"
    assert [
        item.value
        for item in command.elts[1:4]
        if isinstance(item, ast.Constant)
    ] == ["-I", "-S", "-B"]

    safety = next(
        (
            node.value
            for node in ast.walk(main)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "safety"
                for target in node.targets
            )
            and isinstance(node.value, ast.Dict)
        ),
        None,
    )
    assert isinstance(safety, ast.Dict)
    safety_values = {
        key.value: value
        for key, value in zip(safety.keys, safety.values)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }
    assert isinstance(safety_values.get("cwd"), ast.Call)
    assert _call_name(safety_values["cwd"]) == "str"
    assert safety_values["cwd"].args
    assert isinstance(safety_values["cwd"].args[0], ast.Name)
    assert safety_values["cwd"].args[0].id == "runtime_repo"
    assert isinstance(safety_values.get("shell"), ast.Constant)
    assert safety_values["shell"].value is False


def test_NG_CUTOVER_05_deadman_converges_to_stable_task_launcher() -> None:
    """deadman launcherはlegacy routeではなくstable authorityのexact dispatchへ収束する。"""

    tree = _tree(OPS / "news-grasp-deadman-launcher.pyw")
    main = _function(tree, "main")
    strings = _string_constants(main)
    assert "news-grasp-task-launcher.pyw" in strings
    assert "news-grasp-deadman.ps1" not in strings
    assert "news-grasp-runner.ps1" not in strings
    expected_tail = next(
        (
            node.value
            for node in ast.walk(main)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "expected_tail"
                for target in node.targets
            )
            and isinstance(node.value, ast.List)
            and all(isinstance(item, ast.Constant) for item in node.value.elts)
        ),
        None,
    )
    assert expected_tail is None, "stable launcher path must remain runtime-bound"
    expected_tail_node = next(
        (
            node.value
            for node in ast.walk(main)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "expected_tail"
                for target in node.targets
            )
            and isinstance(node.value, ast.List)
        ),
        None,
    )
    assert isinstance(expected_tail_node, ast.List)
    assert len(expected_tail_node.elts) == 9
    assert [
        item.value
        for item in expected_tail_node.elts[:3]
        if isinstance(item, ast.Constant)
    ] == ["-I", "-S", "-B"]
    launcher_path = expected_tail_node.elts[3]
    assert isinstance(launcher_path, ast.Call)
    assert _call_name(launcher_path) == "str"
    assert launcher_path.args and isinstance(launcher_path.args[0], ast.Name)
    assert launcher_path.args[0].id == "stable_launcher"
    assert [
        item.value
        for item in expected_tail_node.elts[4:]
        if isinstance(item, ast.Constant)
    ] == [
        "dispatch",
        "--schedule-id",
        "news-grasp-daily-v1",
        "--intent",
        "reconcile",
    ]
    assert any(
        isinstance(node, ast.Compare)
        and any(
            isinstance(comparator, ast.Name) and comparator.id == "expected_tail"
            for comparator in node.comparators
        )
        for node in ast.walk(main)
    ), "authority action must be checked against the exact dispatch tail"


def test_NG_CUTOVER_06_release_nopublish_wrapper_uses_installed_launcher_module() -> None:
    """Release-only wrapperはlegacy runnerを参照せずinstalled authorityへ委譲する。"""

    source = _read(OPS / "invoke-scheduled-equivalent-nopublish.ps1")
    assert "news-grasp-runner.ps1" not in source
    assert "news-grasp-task-launcher.pyw" in source
    assert "scheduled-equivalent-nopublish" in source
    assert "tools.news_grasp_release_nopublish" in source


def test_NG_CUTOVER_07_release_nopublish_isolated_state_and_zero_external_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Release-only producerがtransactional storeと副作用ゼロのreceiptを返す。"""

    path = ROOT / "tools" / "news_grasp_release_nopublish.py"
    assert path.is_file(), "Release-only NoPublish producer is required"
    tree = _tree(path)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(
        module == "tools.news_grasp_daily_launcher"
        or module.endswith(".news_grasp_daily_launcher")
        for module in imported_modules
    )
    assert any(
        isinstance(node, ast.Call)
        and _call_name(node) == "import_module"
        and any(
            isinstance(argument, ast.Constant)
            and argument.value == "tools.news_grasp_direct_runtime"
            for argument in ast.walk(node)
        )
        for node in ast.walk(tree)
    ), "Release-only producer must lazy-load the transactional direct runtime store"
    assert any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_external_nopublish_receipt"
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_release_nopublish"
        for node in ast.walk(tree)
    )

    module = importlib.import_module("tools.news_grasp_release_nopublish")
    adapter_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def forbidden_external_adapter(*args: Any, **kwargs: Any) -> None:
        adapter_calls.append((args, kwargs))
        raise AssertionError("NoPublish must not invoke an external adapter")

    external = module._external_nopublish_receipt(
        external_adapter=forbidden_external_adapter,
    )
    _assert_zero_external_effects(external)
    assert external["no_publish"] is True
    assert external["adapter_call_count"] == 0
    assert adapter_calls == []

    module._load_release_runtime_modules()
    repo_root = tmp_path / "release-worktree"
    repo_root.mkdir()
    isolation_receipt = repo_root / "isolation-receipt.json"
    isolation_receipt.write_text('{"status":"verified"}\n', encoding="utf-8")
    monkeypatch.setattr(module, "_git", lambda _root, *_args: "a" * 40)
    sequence_calls: list[dict[str, Any]] = []

    def fail_if_sequence_reached(**kwargs: Any) -> list[dict[str, Any]]:
        sequence_calls.append(kwargs)
        raise AssertionError("claim admission must precede daily sequence")

    monkeypatch.setattr(module.daily, "run_daily_sequence", fail_if_sequence_reached)
    with pytest.raises((RuntimeError, PermissionError, ValueError, TypeError)) as error:
        module.run_release_nopublish(
            repo_root=repo_root,
            source_issue_date="2026-09-03",
            state_root=repo_root / "isolated-state",
            isolation_receipt=isolation_receipt,
        )
    assert any(
        token in str(error.value).casefold()
        for token in ("claim", "capability", "authority")
    )
    assert sequence_calls == []
    assert adapter_calls == []


def test_NG_CUTOVER_09_bootstrap_syncs_evidence_origin_before_active_generation_validation() -> None:
    """bootstrapはevidence repoを同期してからruntime convergenceとgeneration検証を行う。"""

    tree = _tree(OPS / "news-grasp-task-launcher.pyw")
    bootstrap = _mode_branch(_function(tree, "main"), "bootstrap")
    calls = [
        node
        for node in ast.walk(bootstrap)
        if isinstance(node, ast.Call)
    ]

    def git_call(*tokens: str) -> ast.Call | None:
        for call in calls:
            if _call_name(call) not in {"_run_git", "run_git", "run"}:
                continue
            constants = {
                item.value
                for item in ast.walk(call)
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
            origin_ref_present = (
                tokens == ("rev-parse", "origin/main")
                and any(
                    value == "origin/main" or value.startswith("origin/main^")
                    for value in constants
                )
            )
            token_match = (
                origin_ref_present
                if tokens == ("rev-parse", "origin/main")
                else set(tokens).issubset(constants)
            )
            if token_match and _contains_name(call, "evidence_repo"):
                return call
        return None

    fetch = git_call("fetch")
    rev_parse = git_call("rev-parse", "origin/main")
    converge = next(
        (
            call
            for call in calls
            if _call_name(call) == "_converge_production_runtime_locked"
        ),
        None,
    )
    validate = next(
        (
            call
            for call in calls
            if _call_name(call) == "_validate_active_production_generation"
        ),
        None,
    )
    assert fetch is not None, "bootstrap must fetch from evidence repo"
    assert rev_parse is not None, "bootstrap must rev-parse evidence origin/main"
    assert converge is not None, "bootstrap must converge the production runtime"
    assert validate is not None, "bootstrap must validate active generation"
    assert fetch.lineno < rev_parse.lineno < converge.lineno < validate.lineno
    keyword_names = {keyword.arg for keyword in converge.keywords if keyword.arg}
    assert {"runtime_root", "bin_dir"}.issubset(keyword_names)


def test_NG_CUTOVER_10_nopublish_isolation_uses_validator_signature_and_green_status(
    tmp_path: Path,
) -> None:
    """isolation validatorのsource_repo_rootとstatus/validationをそのまま受理する。"""

    launcher = _load_pyw(OPS / "news-grasp-task-launcher.pyw")
    execution_repo = tmp_path / "execution-repo"
    runtime_repo = tmp_path / "runtime-repo"
    (runtime_repo / "tools").mkdir(parents=True)
    execution_repo.mkdir()
    receipt_path = runtime_repo / "isolation-receipt.json"
    receipt_path.write_text('{"status":"Green"}\n', encoding="utf-8")
    (runtime_repo / "tools" / "news_grasp_p08_evidence.py").write_text(
        "\n".join(
            (
                "def validate_isolation_receipt(path, *, repo_root, source_repo_root, issue_date):",
                "    return {",
                "        'status': 'Green',",
                "        'validation': {",
                "            'repo_root': str(repo_root),",
                "            'source_repo_root': str(source_repo_root),",
                "            'issue_date': issue_date,",
                "        },",
                "    }",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = launcher._validate_nopublish_isolation(
        execution_repo=execution_repo,
        runtime_repo=runtime_repo,
        issue_date="2026-09-03",
        receipt_path=receipt_path,
    )

    assert result["status"] == "Green"
    validation = result["validation"]
    assert validation["repo_root"] == str(execution_repo.resolve())
    assert validation["source_repo_root"] == str(runtime_repo.resolve())
    assert validation["issue_date"] == "2026-09-03"
    assert "ok" not in result


def test_NG_CUTOVER_11_nopublish_launches_fix_cwd_and_scrubbed_environment() -> None:
    """installed childとRelease moduleがexecution repo固定・ambient Python隔離になる。"""

    launcher_tree = _tree(OPS / "news-grasp-task-launcher.pyw")
    launcher_function = _function(launcher_tree, "_run_installed_nopublish_authority")
    process_calls = [
        node
        for node in ast.walk(launcher_function)
        if isinstance(node, ast.Call) and _call_name(node) in {"run", "Popen"}
    ]
    failures: list[str] = []
    if not process_calls:
        failures.append("installed_nopublish_child_process_call_missing")
    for call in process_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        if "cwd" not in keywords or not _contains_name(keywords["cwd"], "execution_repo"):
            failures.append("installed_nopublish_execution_repo_cwd_missing")
        if "env" not in keywords or not _contains_name(keywords["env"], "child_environment"):
            failures.append("installed_nopublish_scrubbed_environment_missing")
    launcher_strings = set(_string_constants(launcher_function))
    for inherited_name in ("PYTHONPATH", "PYTHONHOME"):
        if inherited_name not in launcher_strings:
            failures.append(f"installed_nopublish_{inherited_name}_scrub_missing")

    wrapper = _read(OPS / "news-grasp-release-nopublish.ps1")
    if not re.search(r"(?im)^\s*Push-Location\s+-LiteralPath\s+\$repo\s*$", wrapper):
        failures.append("release_nopublish_execution_repo_cwd_missing")
    for inherited_name in ("PYTHONPATH", "PYTHONHOME"):
        if not re.search(
            rf"(?im)^\s*Remove-Item\s+Env:{re.escape(inherited_name)}\b",
            wrapper,
        ):
            failures.append(f"release_nopublish_{inherited_name}_scrub_missing")
    assert not failures, "; ".join(failures)


def test_NG_CUTOVER_12_protected_release_maps_to_simulation_in_real_daily_sequence(
    tmp_path: Path,
) -> None:
    """保護済み日付は翌日simulationへ写像し、実Daily sequenceで外部副作用ゼロを閉じる。"""

    release = importlib.import_module("tools.news_grasp_release_nopublish")
    daily = release.daily
    runtime = release.runtime
    assert release.simulation_issue_date("2026-09-02") == "2026-09-03"
    simulation_date = release.simulation_issue_date("2026-09-02")
    state_root = tmp_path / "isolated-state"
    store = runtime.DirectRunStore(
        state_root,
        test_only_allow_semantic_verifier=True,
    )
    source_baseline = "a" * 40
    manifest_id = "b" * 64
    adapter_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def forbidden_external_adapter(*args: Any, **kwargs: Any) -> None:
        adapter_calls.append((args, kwargs))
        raise AssertionError("Release NoPublish must not call an external adapter")

    def producer(expected_operation_id: str, **_context: Any) -> dict[str, Any]:
        return daily._producer_result(
            f"NEWS_GRASP_TEST_{expected_operation_id.upper()}_V1",
            ok=True,
            status="verified",
            operation_id=expected_operation_id,
        )

    def external_handler(**context: Any) -> dict[str, Any]:
        return release._external_nopublish_receipt(
            **context,
            external_adapter=forbidden_external_adapter,
        )

    def consumer_handler(**context: Any) -> dict[str, Any]:
        run_id = str(context["run_id"])
        current = runtime.inspect_run(store, run_id=run_id)
        external = runtime.get_daily_operation_receipt(
            store,
            run_id=run_id,
            operation_id="external_publication",
        )
        assert isinstance(external, Mapping)
        previous_applied_at = str(external.get("applied_at") or "")
        observed_at = store.now()
        if previous_applied_at:
            previous = datetime.fromisoformat(previous_applied_at)
            observed_at = max(observed_at, previous + timedelta(microseconds=1))
        observed_text = observed_at.isoformat()
        nonce = "nopublish-consumer-observation"
        binding = {
            "runId": run_id,
            "issueDate": simulation_date,
            "runIntent": "release_nopublish",
            "generation": current["generation"],
            "manifestId": current["manifest_id"],
            "fencingBindingHash": runtime.fencing_binding_hash(
                run_id=run_id,
                generation=int(current["generation"]),
                writer_lease=str(context["writer_lease"]),
                fencing_token=int(context["fencing_token"]),
            ),
            "updatedAt": previous_applied_at,
            "observedAt": observed_text,
            "observationNonce": nonce,
        }
        return daily._producer_result(
            runtime.CONSUMER_PUBLIC_VERIFICATION_RECEIPT_SCHEMA,
            ok=True,
            status="verified",
            operation_id="consumer_public_verification",
            values={
                "observation": {
                    "ok": True,
                    "status": "verified",
                    "observationToken": nonce,
                    "observedAt": observed_text,
                },
                "observation_token": nonce,
                "external_operation_id": "release-nopublish-local-observation",
                "freshnessBinding": binding,
            },
        )

    handlers = {
        operation_id: (lambda _operation_id=operation_id, **context: producer(_operation_id, **context))
        for operation_id in daily.DAILY_OPERATIONS
    }
    handlers["external_publication"] = external_handler
    handlers["consumer_public_verification"] = consumer_handler
    handlers["atomic_completion"] = daily._default_atomic_completion
    receipts = daily.run_daily_sequence(
        handlers=handlers,
        store=store,
        cwd=tmp_path,
        issue_date=simulation_date,
        run_intent="release_nopublish",
        automation_id="news-grasp-release-gate",
        scheduler_trigger_at=store.now().isoformat(),
        manifest_id=manifest_id,
        source_baseline=source_baseline,
        runtime_generation=f"release-nopublish:{source_baseline}",
        remote_base_sha=source_baseline,
        allowed_side_effect_ids=(),
        context={"repo_root": tmp_path},
    )

    assert len(receipts) == len(daily.DAILY_OPERATIONS)
    assert receipts[-1]["ok"] is True
    assert receipts[-1]["status"] == "completed"
    external_receipt = receipts[3]["producer_receipt"]
    _assert_zero_external_effects(external_receipt)
    assert adapter_calls == []


def test_NG_CUTOVER_13_release_cli_claim_missing_fails_before_import_or_receipt_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """高コストclaim欠落時はruntime importとstate/receipt mutationへ到達しない。"""

    module = importlib.import_module("tools.news_grasp_release_nopublish")
    repo_root = tmp_path / "execution-repo"
    repo_root.mkdir()
    state_file = repo_root / "state.json"
    receipt_path = repo_root / "receipt.json"
    isolation_receipt = repo_root / "isolation-receipt.json"
    isolation_receipt.write_text('{"status":"Green"}\n', encoding="utf-8")
    for environment_name in (
        "NEWS_GRASP_E2E_ADMISSION_PATH",
        "NEWS_GRASP_E2E_ARGUMENTS_PATH",
        "NEWS_GRASP_E2E_CLAIM_PATH",
        "NEWS_GRASP_E2E_RESERVATION_PATH",
        "NEWS_GRASP_E2E_PARENT_AUTHORITY_PATH",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    runtime_imports: list[bool] = []
    writes: list[Path] = []

    def fail_if_runtime_imported() -> None:
        runtime_imports.append(True)
        raise AssertionError("claim admission must precede daily/runtime import")

    def record_write(path: Path, _value: Mapping[str, Any]) -> None:
        writes.append(path)

    monkeypatch.setattr(module, "_load_release_runtime_modules", fail_if_runtime_imported)
    monkeypatch.setattr(module, "_atomic_json", record_write)
    exit_code = module._main(
        [
            "--repo-root",
            str(repo_root),
            "--source-issue-date",
            "2026-09-03",
            "--state-root",
            str(repo_root / "isolated-state"),
            "--state-file",
            str(state_file),
            "--receipt-path",
            str(receipt_path),
            "--isolation-receipt",
            str(isolation_receipt),
        ]
    )

    assert exit_code != 0
    assert runtime_imports == []
    assert writes == []
    assert not state_file.exists()
    assert not receipt_path.exists()


def test_NG_CUTOVER_14_run_git_scrubs_all_inherited_git_config_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """task launcherのGit childへconfig override環境を継承しない。"""

    launcher = _load_pyw(OPS / "news-grasp-task-launcher.pyw")
    inherited_names = (
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    )
    for name in inherited_names:
        monkeypatch.setenv(name, "injected")
    observed: dict[str, Any] = {}

    class Completed:
        returncode = 0
        stdout = b"head\n"
        stderr = b""

    def fake_run(*args: Any, **kwargs: Any) -> Completed:
        observed["args"] = args
        observed["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    assert launcher._run_git(tmp_path, "rev-parse", "HEAD") == "head"
    child_environment = observed["kwargs"]["env"]
    assert all(name not in child_environment for name in inherited_names)


def test_NG_CUTOVER_15_outer_receipt_flushes_and_atomically_moves_temp_file() -> None:
    """outer receiptはtemp writeをflush/fsyncしてからatomic moveする。"""

    tree = _tree(OPS / "news-grasp-task-launcher.pyw")
    writer = _function(tree, "_write_json_atomic")
    call_names = {
        _call_name(node)
        for node in ast.walk(writer)
        if isinstance(node, ast.Call)
    }
    assert "write" in call_names or "write_text" in call_names
    assert any(
        isinstance(node, ast.Call)
        and _call_name(node) == "open"
        and any(
            isinstance(argument, ast.Constant) and argument.value == "xb"
            for argument in node.args
        )
        for node in ast.walk(writer)
    ), "outer receipt must exclusively create a same-volume binary temp file"
    assert "flush" in call_names
    assert "fsync" in call_names
    assert "replace" in call_names or "rename" in call_names


def test_NG_CUTOVER_08_source_repo_has_no_legacy_runner_script() -> None:
    """source repoにlegacy news-grasp-runner.ps1を復活させない。"""

    assert not (OPS / "news-grasp-runner.ps1").exists()


def test_NG_CUTOVER_16_deadman_validates_authority_and_runtime_before_import() -> None:
    """deadmanはinstalled/runtime codeをauthority検証前にimportしない。"""

    source = _read(OPS / "news-grasp-deadman-launcher.pyw")
    authority = source.index("authority = _load_stable_authority_before_import(")
    installed_import = source.index("spec.loader.exec_module(stable_module)")
    runtime_validation = source.index("stable_module._validate_active_production_generation(")
    owned_import = source.index("stable_module._load_module_from_exact_path(")
    assert authority < installed_import < runtime_validation < owned_import


def test_NG_CUTOVER_17_working_tree_identity_changes_with_same_path_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """同じ変更pathでもbytes差替えをprelaunch identityで検出する。"""

    launcher = _load_pyw(OPS / "news-grasp-task-launcher.pyw")
    changed = tmp_path / "changed.txt"
    changed.write_text("before\n", encoding="utf-8")

    def fake_git(_repo: Path, *arguments: str) -> str:
        if arguments[:3] == ("diff", "--name-only", "--no-renames"):
            return "changed.txt\x00"
        if arguments[:4] == ("diff", "--cached", "--name-only", "--no-renames"):
            return ""
        raise AssertionError(arguments)

    monkeypatch.setattr(launcher, "_run_git", fake_git)
    before = launcher._working_tree_content_identity(tmp_path)
    changed.write_text("after\n", encoding="utf-8")
    after = launcher._working_tree_content_identity(tmp_path)
    assert before != after


def test_NG_CUTOVER_18_wrapper_binds_caller_python_before_first_child() -> None:
    """caller Pythonはinstalled runtime path/hashと一致してからのみ実行する。"""

    source = _read(OPS / "invoke-scheduled-equivalent-nopublish.ps1")
    binding_reject = source.index("authority Python does not match installed runtime binding")
    first_child = source.index("& $pythonCanonicalPath")
    assert "[string]::Equals($pythonCanonicalPath, $installedTaskPythonPath" in source
    assert "[string]::Equals($pythonCanonicalSha256, $installedPythonSha256" in source
    assert binding_reject < first_child
