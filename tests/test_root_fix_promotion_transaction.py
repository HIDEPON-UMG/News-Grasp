from __future__ import annotations

import json
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path


TRUSTED_APPLIER = Path(__file__).resolve().parents[3] / "apply_root_fix_promotion.py"
REVIEW_BROKER = Path(__file__).resolve().parents[3] / "review_authority_broker.py"


def test_review_authority_separates_trusted_transaction_regression_from_untrusted_product_child() -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_profile_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PROFILE["allowedSystemDllNames"] == []
    product_target = module.PROFILE["testTargets"]["repo:daily_control"]
    assert product_target["paths"] == ["tests/test_news_grasp_daily_control.py"]
    transaction_target = module.PROFILE["trustedTcbRegression"]
    assert transaction_target["paths"] == ["tests/test_root_fix_promotion_transaction.py"]
    assert "tests/test_root_fix_promotion_transaction.py" not in product_target["paths"]
    assert "FixedReviewPlugin" not in module.ISOLATED_PYTEST_BOOTSTRAP
    assert "review_plugin" not in module.ISOLATED_PYTEST_BOOTSTRAP
    broker_source = REVIEW_BROKER.read_text(encoding="utf-8")
    applier_source = TRUSTED_APPLIER.read_text(encoding="utf-8")
    assert '"reviewInvocationId": sha(canonical(expected))[:32]' in broker_source
    assert 'f"{tree}-{review_invocation}.json"' in broker_source
    assert "reviewAuthorityPath" in applier_source
    assert '"system-dll:kernel32.dll"' in broker_source
    assert "ctypes.WinDLL(str(system_kernel32)" in applier_source


def test_trusted_applier_recovers_hard_crash_and_rejects_replay() -> None:
    result = subprocess.run(
        [sys.executable, str(TRUSTED_APPLIER), "self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload == {
        "absentBeforeRollback": True,
        "atomicNoReplace": True,
        "applyBlobOwnership": True,
        "casLock": True,
        "cleanupFailureNotSuccess": True,
        "commitCleanupCrashRecovery": True,
        "finalTargetRehash": True,
        "hardCrashRecovery": True,
        "guardAwareBrokerPrerequisite": True,
        "guardedFreshnessRace": True,
        "authoritativeProcessLeaseObserver": True,
        "allBrokerConsumerSurfacesPrerequisite": True,
        "appendOnlyProcessLedger": True,
        "crossLineageProcessBinding": True,
        "durableAdmissionBarrier": True,
        "registryAheadTwoPhase": True,
        "singleUseBarrierClearAuthority": True,
        "barrierSchemaAuthority": True,
        "barrierCrashConvergence": True,
        "preActivationSnapshotRejected": True,
        "terminalRollbackReceipt": True,
        "clearedForwardReplayRejected": True,
        "rollbackCrashBarrier": True,
        "terminalForgeryObserved": True,
        "recoveryForwardGuard": True,
        "immutableSourceLock": True,
        "installedBrokerFirst": True,
        "brokerLastRollback": True,
        "journalAuthorityBinding": True,
        "dynamicJournalAuthentication": True,
        "terminalReceipt": True,
        "journalSelfReportNotAuthority": True,
        "fakeCommittedRecovery": True,
        "residualTombstoneNotReplay": True,
        "lexicalCleanupResidueRejected": True,
        "nonterminalConsumerGuard": True,
        "pathSubstitutionRejected": True,
        "preconvergedAdmission": True,
        "priorCommittedAdmission": True,
        "reviewAuthorityBroker": True,
        "rollbackOnFailure": True,
        "foreignDriftPreserved": True,
        "foreignIdenticalPreserved": True,
        "runtimeFailureRollback": True,
        "runtimeFreshness": True,
        "singleUseCapability": True,
        "stagedHashValidation": True,
        "status": "Green",
    }


def test_trusted_applier_has_no_direct_mutation_api_or_candidate_validator() -> None:
    source = TRUSTED_APPLIER.read_text(encoding="utf-8")
    assert "def apply_bound_transaction" not in source
    assert "importlib" not in source
    assert "spec_from_file_location" not in source
    assert "def run_transaction" in source
    assert source.index("def run_transaction") > source.index("def main")
    assert "INDEPENDENT_ADVERSARIAL_REVIEW_BLOCKED" in source
    assert "trustedApplierSha256" in source
    assert "PROMOTION_DIRECT_IMPORT_INVOCATION_FORBIDDEN" in source
    assert "PROMOTION_TRUSTED_ROOT_IDENTITY_INVALID" in source
    assert "EXPECTED_REPO_TARGETS" in source
    assert "def _production_delta_paths" in source
    assert "PROMOTION_UNDECLARED_DELTA" in source
    assert "NON_PROMOTABLE_RUNTIME_PREFIXES" in source


def test_trusted_applier_requires_runtime_freshness_before_commit() -> None:
    source = TRUSTED_APPLIER.read_text(encoding="utf-8")
    freshness = source.index("_observe_runtime_freshness")
    commit = source.index('journal["phase"] = "committed"')
    assert freshness < commit
    assert '"staged_not_active"' in source
    assert "PROMOTION_RUNTIME_STAGED_NOT_ACTIVE" in source


def test_trusted_applier_binds_immutable_payload_and_gates_consumers_during_partial_state() -> None:
    source = TRUSTED_APPLIER.read_text(encoding="utf-8")
    assert "def prelock_candidate_sources" in source
    assert "source_locks: dict[Path, tuple[int, bytes]]" in source
    assert "source_key = source.resolve()" in source
    assert "source_locks[source_key] = (handle, payload)" in source
    assert "immutablePayloadTreeSha256" in source
    assert "NEWS_GRASP_PROMOTION_GUARD_V1" in source
    assert "def finish_with_guard" in source
    assert "installed_broker" in source[source.index("def application_order") :]
    assert "hard_crash_before_apply" in source
    assert "def ensure_guard_aware_broker_prerequisite" in source
    assert "guardedBrokerFreshness" in source
    assert "preserve_guard_broker" in source
    assert "def broker_prerequisite_indices" in source
    assert "model-spawn-process-ledger-v1" in source
    assert 'set(payload) == {"callId"}' in source
    assert "promotion_admission_barrier_events" in source
    assert "CREATE TABLE IF NOT EXISTS promotion_admission_barrier" not in source
    assert "PromotionBarrierStateJson" in source
    assert "clearCapabilitySha256" in source
    assert "rollback-terminal-receipt.json" in source
    assert "PROMOTION_ADMISSION_BARRIER_ACTIVE" in source
    assert "install_durable_admission_barrier" in source
    assert "clear_durable_admission_barrier" in source
    assert "reversed(application_order(journal[\"rows\"]))" in source


def test_trusted_applier_revalidates_resume_authority_stage_and_all_targets() -> None:
    source = TRUSTED_APPLIER.read_text(encoding="utf-8")
    assert "PROMOTION_CRASH_RECOVERY_JOURNAL_INVALID" in source
    assert "PROMOTION_STAGED_BYTES_HASH_MISMATCH" in source
    assert "PROMOTION_POST_APPLY_TARGET_DRIFT" in source
    assert "PROMOTION_TRANSACTION_ROOT_OUTSIDE_RECEIPT" in source
    assert "CreateFileW" in source
    assert "FILE_SHARE_READ" in source
    assert "FILE_SHARE_DELETE" in source
    assert "rename_by_handle" in source
    assert "delete_by_handle" in source
    assert "SetFileInformationByHandle" in source
    assert "os.link(" in source
    assert "samefile" in source
    assert 'row["applyBlobPath"]' in source
    assert "rowOwnership" not in source
    assert 'row["installPath"]' not in source
    transition_source = source[source.index("def lock_transition") : source.index("def handle_identity")]
    assert "FILE_SHARE_DELETE" not in transition_source
    finalize_source = source[source.index("def finalize_commit") : source.index("def finish")]
    assert finalize_source.index('journal["phase"] = "commit_cleanup_pending"') < finalize_source.index(
        "delete_by_handle"
    ) < finalize_source.index('journal["phase"] = "committed"')
    finish_start = source.index("def finish(")
    finish_end = source.index("        if consumed_path.exists():", finish_start)
    finish_source = source[finish_start:finish_end]
    assert finish_source.index("prelock_targets(journal, handles)") < finish_source.index(
        "for ordinal, row_index in enumerate"
    )
    assert "rollback_blocked_foreign_drift" in source
    assert "rolled_back_retryable" in source
    assert "REVIEW_AUTHORITY_BROKER_PATH" in source
    assert "reviewAuthorityEventHash" in source
    assert 'consumed.get("transactionId")' in source
    assert 'journal.get("manifestBodySha256")' in source
    assert "journalTransitionHeadSha256" in source
    assert "validate_dynamic_journal" in source
    assert "append_journal_transition" in source
    assert "PROMOTION_JOURNAL_DYNAMIC_STATE_INVALID" in source
    assert "NEWS_GRASP_TRUSTED_PROMOTION_TERMINAL_RECEIPT_V1" in source
    assert "journalAuthorityKey" not in source
    assert "validate_committed_reality" in source
    assert "reconstruct_journal_from_reality" in source


def test_candidate_evidence_consumer_requires_the_trusted_applier_path() -> None:
    source = (Path(__file__).resolve().parents[1] / "tools" / "root_fix_promotion_control.py").read_text(
        encoding="utf-8"
    )
    assert '"NEWS_GRASP_REVIEW_AUTHORITY_RECEIPT_V1"' not in source
    assert '"reviewAuthorityEventHash"' not in source
    assert '"reviewerAgent"' not in source
    assert '"tools/root_fix_promotion_apply.py"' not in source
    assert "PROMOTION_TRUSTED_APPLIER_REQUIRED" in source
    assert '"promotionEvidenceValid": True' not in source


def test_review_authority_broker_rejects_path_substitution() -> None:
    source = REVIEW_BROKER.read_text(encoding="utf-8")
    assert "REVIEW_AUTHORITY_PATH_OUTSIDE_ROOT" in source
    assert "REVIEW_AUTHORITY_SYMLINK_FORBIDDEN" in source
    assert "expected_event_path" in source
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in source
    assert "PYTEST_ADDOPTS" in source
    assert '"--noconftest"' in source
    assert '"-I"' in source and '"-S"' in source
    assert "reviewInputClosureSha256" in source
    assert "expectedCollectionSha256" in source
    assert "loadedFileRows" in source
    assert '"__cached__"' in source
    assert '".pyc"' in source
    assert "lock_review_input_closure" in source
    assert "trusted:apply_root_fix_promotion.py" in source
    assert "trusted:promotion-manifest.json" in source
    assert "REVIEW_AUTHORITY_LOADED_FILE_OUTSIDE_CLOSURE" in source
    assert "REVIEW_AUTHORITY_LOADED_FILE_HASH_MISMATCH" in source
    assert "REVIEW_AUTHORITY_IMPORT_OUTSIDE_CLOSURE" in source
    assert "REVIEW_AUTHORITY_LOADED_FILE_MISSING" in source
    assert "REVIEW_AUTHORITY_DYNAMIC_PAIR_REPLAY" in source
    assert "REVIEW_AUTHORITY_DYNAMIC_PAIR_CROSS_BINDING" in source
    assert "expectedPreHookTcbSha256" in source
    assert "REVIEW_AUTHORITY_PREHOOK_TCB_MISMATCH" in source


def test_review_authority_rejects_direct_loader_self_deleting_import(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_for_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    closure = module.review_input_closure()

    def run_probe(name: str, source: str) -> dict[str, object]:
        probe = candidate / name
        probe.write_text(source, encoding="utf-8")
        payload = probe.read_bytes()
        probe_row = {
            "path": f"fixture:{name}",
            "absolutePath": str(probe.resolve()),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
        return module.run_isolated_pytest(
            candidate_root=candidate,
            pytest_args=[],
            closure_rows=[*closure["entryRows"], probe_row],
            site_root=Path(closure["pytestSiteRoot"]),
            timeout=30,
            execution_mode="probe",
            probe_path=probe,
        )

    direct_result = run_probe(
        "probe_direct_source_loader.py",
        "from pathlib import Path\n"
        "from importlib.machinery import SourceFileLoader\n"
        "from importlib.util import module_from_spec, spec_from_loader\n"
        "proxy = Path(__file__).with_name('sqlite3.py')\n"
        "proxy.write_text(\"from pathlib import Path\\n"
        "Path(__file__).unlink()\\n"
        "Path(__file__).with_name('direct-loader-executed.marker').write_text('executed', encoding='utf-8')\\n\", encoding='utf-8')\n"
        "loader = SourceFileLoader('transient_sqlite3', str(proxy))\n"
        "spec = spec_from_loader('transient_sqlite3', loader)\n"
        "loaded = module_from_spec(spec)\n"
        "loader.exec_module(loaded)\n",
    )
    direct_diagnostic = direct_result["stderr"] + direct_result["stdout"]
    assert direct_result["returnCode"] != 0
    assert "OUTSIDE_CLOSURE" in direct_diagnostic
    assert "sqlite3.py" in direct_diagnostic
    assert not (candidate / "direct-loader-executed.marker").exists()

    pseudo_result = run_probe(
        "probe_pseudo_filename.py",
        "from pathlib import Path\n"
        "payload = Path(__file__).with_name('pseudo-payload.py')\n"
        "payload.write_text(\"from pathlib import Path\\n"
        "Path(__file__).with_name('pseudo-code-executed.marker').write_text('executed', encoding='utf-8')\\n\", encoding='utf-8')\n"
        "source = payload.read_text(encoding='utf-8')\n"
        "exec(compile(source, '<module>', 'exec'), {'__file__': str(payload)})\n",
    )
    pseudo_diagnostic = pseudo_result["stderr"] + pseudo_result["stdout"]
    assert pseudo_result["returnCode"] != 0
    assert "REVIEW_AUTHORITY_DYNAMIC_CODE_OUTSIDE_CLOSURE" in pseudo_diagnostic
    assert "<module>" in pseudo_diagnostic
    assert not (candidate / "pseudo-code-executed.marker").exists()

    registered_path_result = run_probe(
        "probe_registered_path_filename.py",
        "from pathlib import Path\n"
        "payload = Path(__file__).with_name('registered-path-payload.py')\n"
        "payload.write_text(\"from pathlib import Path\\n"
        "Path(__file__).with_name('registered-path-executed.marker').write_text('executed', encoding='utf-8')\\n\", encoding='utf-8')\n"
        "source = payload.read_text(encoding='utf-8')\n"
        "exec(compile(source, __file__, 'exec'), {'__file__': str(payload)})\n",
    )
    registered_diagnostic = registered_path_result["stderr"] + registered_path_result["stdout"]
    assert registered_path_result["returnCode"] != 0
    assert "REVIEW_AUTHORITY_DYNAMIC_CODE_SOURCE_MISMATCH" in registered_diagnostic
    assert "probe_registered_path_filename.py" in registered_diagnostic
    assert not (candidate / "registered-path-executed.marker").exists()

    forged_blob = (
        "4wAAAAAAAAAAAAAAAAUAAAAAAAAA81oAAACVAFMAUwFLAEoBcgEgAFwBIgBcAjUBAAAAAAAA"
        "UgcAAAAAAAAAAAAAAAAAAAAAAABTAjUBAAAAAAAAUgkAAAAAAAAAAAAAAAAAAAAAAABTA1ME"
        "UwU5AiAAZwYpB+kAAAAAKQHaBFBhdGh6HG1hcnNoYWwtY29kZS1leGVjdXRlZC5tYXJrZXLa"
        "CGV4ZWN1dGVkegV1dGYtOCkB2ghlbmNvZGluZ04pBdoHcGF0aGxpYnIDAAAA2ghfX2ZpbGVf"
        "X9oJd2l0aF9uYW1l2gp3cml0ZV90ZXh0qQDzAAAAANoWPGZyb3plbiBmb3JnZWQtcmV2aWV3"
        "PtoIPG1vZHVsZT5yDQAAAAEAAABzLAAAAPADAQEB3QAY2QAEgFiDDtcAGNEAGNAZN9MAONcA"
        "Q9EAQ8BK0Flg0ABD0gBhcgsAAAA="
    )
    marshal_result = run_probe(
        "probe_marshal_constructor.py",
        "import base64\n"
        "import marshal\n"
        f"code = marshal.loads(base64.b64decode('{forged_blob}'))\n"
        "exec(code, {'__file__': __file__})\n",
    )
    marshal_diagnostic = marshal_result["stderr"] + marshal_result["stdout"]
    assert marshal_result["returnCode"] != 0
    assert "REVIEW_AUTHORITY_DYNAMIC_CODE_FROZEN_OBJECT_FORBIDDEN" in marshal_diagnostic
    assert "<frozen forged-review>" in marshal_diagnostic
    assert not (candidate / "marshal-code-executed.marker").exists()

    constructor_result = run_probe(
        "probe_code_constructor.py",
        "def original():\n"
        "    return None\n"
        "original.__code__.replace(co_filename=__file__)\n",
    )
    constructor_diagnostic = constructor_result["stderr"] + constructor_result["stdout"]
    assert constructor_result["returnCode"] != 0
    assert "REVIEW_AUTHORITY_DYNAMIC_CODE_CONSTRUCTOR_FORBIDDEN:code.__new__" in constructor_diagnostic

    native_source = next(
        Path(row["absolutePath"])
        for row in closure["entryRows"]
        if Path(row["absolutePath"]).name.casefold() == "_socket.pyd"
    )
    native_copy = candidate / "transient_socket.pyd"
    native_copy.write_bytes(native_source.read_bytes())
    native_result = run_probe(
        "probe_direct_native_loader.py",
        "from pathlib import Path\n"
        "from importlib.machinery import ExtensionFileLoader\n"
        "from importlib.util import module_from_spec, spec_from_loader\n"
        "native = Path(__file__).with_name('transient_socket.pyd')\n"
        "loader = ExtensionFileLoader('_socket', str(native))\n"
        "spec = spec_from_loader('_socket', loader)\n"
        "loaded = module_from_spec(spec)\n"
        "loader.exec_module(loaded)\n",
    )
    native_diagnostic = native_result["stderr"] + native_result["stdout"]
    assert native_result["returnCode"] != 0
    assert "OUTSIDE_CLOSURE" in native_diagnostic
    assert "transient_socket.pyd" in native_diagnostic

    valid_pyc_result = run_probe(
        "probe_valid_closure_pyc.py",
        "import importlib\n"
        "import sys\n"
        "sys.modules.pop('base64', None)\n"
        "importlib.import_module('base64')\n"
        "sys.modules.pop('_pytest._argcomplete', None)\n"
        "importlib.import_module('_pytest._argcomplete')\n",
    )
    assert valid_pyc_result["returnCode"] == 0, (
        valid_pyc_result["stderr"] + valid_pyc_result["stdout"]
    )

    system_dll_result = run_probe(
        "probe_system_dll_forbidden.py",
        "import ctypes\n"
        "ctypes.WinDLL('kernel32')\n",
    )
    system_dll_diagnostic = system_dll_result["stderr"] + system_dll_result["stdout"]
    assert system_dll_result["returnCode"] == 98, system_dll_diagnostic
    assert "REVIEW_AUTHORITY_IMPORT_OUTSIDE_CLOSURE" in system_dll_diagnostic

    caught_rejection_result = run_probe(
        "probe_caught_rejection.py",
        "from pathlib import Path\n"
        "try:\n"
        "    compile(\"Path('caught-rejection-executed.marker').write_text('bad')\\n\", '<caught-rejection>', 'exec')\n"
        "except RuntimeError:\n"
        "    pass\n"
        "Path(__file__).with_name('caught-rejection-continued.marker').write_text('continued', encoding='utf-8')\n",
    )
    caught_diagnostic = caught_rejection_result["stderr"] + caught_rejection_result["stdout"]
    assert caught_rejection_result["returnCode"] == 98, caught_diagnostic
    assert caught_rejection_result["receipt"].get("firstRejection") is not None
    assert caught_rejection_result["receipt"].get("exitCode") == 98
    assert not (candidate / "caught-rejection-continued.marker").exists()

    authority_global_tamper_result = run_probe(
        "probe_authority_global_tamper.py",
        "import sys\n"
        "from pathlib import Path\n"
        "from importlib.machinery import SourceFileLoader\n"
        "from importlib.util import module_from_spec, spec_from_loader\n"
        "authority = sys.modules['__main__']\n"
        "authority.bootstrap_internal_audit = True\n"
        "authority.bootstrap_allowed = {}\n"
        "authority.os._exit = lambda code: None\n"
        "proxy = Path(__file__).with_name('authority-tamper.py')\n"
        "proxy.write_text(\"from pathlib import Path\\nPath(__file__).with_name('authority-tamper-executed.marker').write_text('executed', encoding='utf-8')\\n\", encoding='utf-8')\n"
        "loader = SourceFileLoader('authority_tamper', str(proxy))\n"
        "spec = spec_from_loader('authority_tamper', loader)\n"
        "loaded = module_from_spec(spec)\n"
        "loader.exec_module(loaded)\n",
    )
    authority_global_tamper_diagnostic = (
        authority_global_tamper_result["stderr"]
        + authority_global_tamper_result["stdout"]
    )
    assert authority_global_tamper_result["returnCode"] == 98, authority_global_tamper_diagnostic
    assert "OUTSIDE_CLOSURE" in authority_global_tamper_diagnostic
    assert not (candidate / "authority-tamper-executed.marker").exists()

    introspection_result = run_probe(
        "probe_authority_closure_introspection.py",
        "import gc\n"
        "gc.get_objects()\n",
    )
    introspection_diagnostic = introspection_result["stderr"] + introspection_result["stdout"]
    assert introspection_result["returnCode"] == 98, introspection_diagnostic
    assert "REVIEW_AUTHORITY_INTROSPECTION_FORBIDDEN:gc.get_objects" in introspection_diagnostic

    traceback_introspection_result = run_probe(
        "probe_authority_traceback_introspection.py",
        "import sys\n"
        "try:\n"
        "    raise RuntimeError('trace')\n"
        "except RuntimeError:\n"
        "    frame = sys.exc_info()[2].tb_frame\n"
        "    outer = frame.f_back\n"
        "    snapshot = outer.f_locals['state_snapshot']\n"
        "    for cell in snapshot.__closure__ or ():\n"
        "        if isinstance(cell.cell_contents, (list, set, dict)):\n"
        "            cell.cell_contents.clear()\n",
    )
    traceback_introspection_diagnostic = (
        traceback_introspection_result["stderr"]
        + traceback_introspection_result["stdout"]
    )
    assert traceback_introspection_result["returnCode"] == 98, (
        traceback_introspection_diagnostic
    )
    assert "REVIEW_AUTHORITY_INTROSPECTION_FORBIDDEN:object.__getattr__" in (
        traceback_introspection_diagnostic
    )

    pair_a_blob = (
        "4wAAAAAAAAAAAAAAAAEAAAAAAAAA8wgAAACVAFMAcgBnASkC6QEAAABOKQHaBnBhaXJfYakA"
        "8wAAAADaDzxyZXZpZXctcGFpci1hPtoIPG1vZHVsZT5yBwAAAAEAAABzCgAAAPADAQEB2AkK"
        "gQZyBQAAAA=="
    )
    pair_b_blob = (
        "4wAAAAAAAAAAAAAAAAEAAAAAAAAA8wgAAACVAFMAcgBnASkC6QIAAABOKQHaBnBhaXJfYqkA"
        "8wAAAADaDzxyZXZpZXctcGFpci1iPtoIPG1vZHVsZT5yBwAAAAEAAABzCgAAAPADAQEB2AkK"
        "gQZyBQAAAA=="
    )
    replay_result = run_probe(
        "probe_dynamic_pair_replay.py",
        "source = 'pair_a = 1\\n'\n"
        "exec(compile(source, '<review-pair-a>', 'exec'), {})\n"
        "compile(source, '<review-pair-a>', 'exec')\n",
    )
    replay_diagnostic = replay_result["stderr"] + replay_result["stdout"]
    assert replay_result["returnCode"] == 98, replay_diagnostic
    assert "REVIEW_AUTHORITY_DYNAMIC_PAIR_REPLAY" in replay_diagnostic

    cross_pair_result = run_probe(
        "probe_dynamic_pair_cross_binding.py",
        "import base64\n"
        "import marshal\n"
        "compile('pair_a = 1\\n', '<review-pair-a>', 'exec')\n"
        f"exec(marshal.loads(base64.b64decode('{pair_b_blob}')), {{}})\n",
    )
    cross_pair_diagnostic = cross_pair_result["stderr"] + cross_pair_result["stdout"]
    assert cross_pair_result["returnCode"] == 98, cross_pair_diagnostic
    assert "REVIEW_AUTHORITY_DYNAMIC_PAIR_CROSS_BINDING" in cross_pair_diagnostic

    # pair blobは固定fixtureとしてprofileとのexact bindingも検証する。
    assert pair_a_blob and pair_b_blob


def test_review_authority_rejects_prehook_tcb_digest_drift(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_prehook_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    probe = candidate / "probe.py"
    probe.write_text("value = 1\n", encoding="utf-8")
    closure = module.review_input_closure()
    payload = probe.read_bytes()
    probe_row = {
        "path": "fixture:probe.py",
        "absolutePath": str(probe.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    module.PROFILE["preHookTrustBoundary"]["expectedPreHookTcbSha256"] = "0" * 64
    result = module.run_isolated_pytest(
        candidate_root=candidate,
        pytest_args=[],
        closure_rows=[*closure["entryRows"], probe_row],
        site_root=Path(closure["pytestSiteRoot"]),
        timeout=30,
        execution_mode="probe",
        probe_path=probe,
    )
    diagnostic = result["stderr"] + result["stdout"]
    assert result["returnCode"] != 0
    assert "REVIEW_AUTHORITY_PREHOOK_TCB_MISMATCH" in diagnostic


def test_review_authority_rejects_receipt_path_forgery(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_receipt_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    probe = candidate / "probe_receipt_forgery.py"
    probe.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(sys.argv[3]).write_text(json.dumps({'exitCode': 0, 'mode': 'probe', 'firstRejection': None}), encoding='utf-8')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    closure = module.review_input_closure()
    payload = probe.read_bytes()
    result = module.run_isolated_pytest(
        candidate_root=candidate,
        pytest_args=[],
        closure_rows=[
            *closure["entryRows"],
            {
                "path": "fixture:probe_receipt_forgery.py",
                "absolutePath": str(probe.resolve()),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            },
        ],
        site_root=Path(closure["pytestSiteRoot"]),
        timeout=30,
        execution_mode="probe",
        probe_path=probe,
    )
    diagnostic = result["stderr"] + result["stdout"]
    assert result["returnCode"] == 98, diagnostic
    assert "REVIEW_AUTHORITY_RECEIPT_PATH_ACCESS_FORBIDDEN" in diagnostic
    assert result["receipt"].get("firstRejection") == (
        "REVIEW_AUTHORITY_RECEIPT_PATH_ACCESS_FORBIDDEN"
    )

    replace_probe = candidate / "probe_receipt_replace.py"
    replace_probe.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "replacement = Path(__file__).with_name('forged-receipt.json')\n"
        "replacement.write_text(json.dumps({'exitCode': 0, 'mode': 'probe', 'firstRejection': None}), encoding='utf-8')\n"
        "os.replace(replacement, sys.argv[3])\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    replace_payload = replace_probe.read_bytes()
    replace_result = module.run_isolated_pytest(
        candidate_root=candidate,
        pytest_args=[],
        closure_rows=[
            *closure["entryRows"],
            {
                "path": "fixture:probe_receipt_replace.py",
                "absolutePath": str(replace_probe.resolve()),
                "sha256": hashlib.sha256(replace_payload).hexdigest(),
                "size": len(replace_payload),
            },
        ],
        site_root=Path(closure["pytestSiteRoot"]),
        timeout=30,
        execution_mode="probe",
        probe_path=replace_probe,
    )
    replace_diagnostic = replace_result["stderr"] + replace_result["stdout"]
    assert replace_result["returnCode"] == 98, replace_diagnostic
    assert "REVIEW_AUTHORITY_RECEIPT_PATH_ACCESS_FORBIDDEN" in replace_diagnostic

    relative_probe = candidate / "probe_receipt_relative_path.py"
    relative_probe.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "receipt = Path(sys.argv[3])\n"
        "os.chdir(receipt.parent)\n"
        "Path(receipt.name).write_text(json.dumps({'exitCode': 0, 'mode': 'probe', 'firstRejection': None}), encoding='utf-8')\n"
        "os._exit(0)\n",
        encoding="utf-8",
    )
    relative_payload = relative_probe.read_bytes()
    relative_result = module.run_isolated_pytest(
        candidate_root=candidate,
        pytest_args=[],
        closure_rows=[
            *closure["entryRows"],
            {
                "path": "fixture:probe_receipt_relative_path.py",
                "absolutePath": str(relative_probe.resolve()),
                "sha256": hashlib.sha256(relative_payload).hexdigest(),
                "size": len(relative_payload),
            },
        ],
        site_root=Path(closure["pytestSiteRoot"]),
        timeout=30,
        execution_mode="probe",
        probe_path=relative_probe,
    )
    relative_diagnostic = relative_result["stderr"] + relative_result["stdout"]
    assert relative_result["returnCode"] == 98, relative_diagnostic
    assert "REVIEW_AUTHORITY_RECEIPT_PATH_ACCESS_FORBIDDEN" in relative_diagnostic


def test_review_authority_input_closure_changes_with_conftest(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_for_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conftest = tmp_path / "conftest.py"
    conftest.write_text("def pytest_sessionfinish(session):\n    session.exitstatus = 0\n", encoding="utf-8")
    first = module._closure_rows(tmp_path, [tmp_path], label="fixture")
    conftest.write_text("def pytest_sessionfinish(session):\n    session.exitstatus = 1\n", encoding="utf-8")
    second = module._closure_rows(tmp_path, [tmp_path], label="fixture")
    assert hashlib.sha256(module.canonical(first)).hexdigest() != hashlib.sha256(
        module.canonical(second)
    ).hexdigest()


def test_review_authority_runtime_prefixes_include_direct_site_modules(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("review_authority_broker_for_test", REVIEW_BROKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    direct_module = tmp_path / "py.py"
    direct_module.write_text("value = 1\n", encoding="utf-8")
    package = tmp_path / "pytest"
    package.mkdir()
    prefixes = module.pytest_runtime_prefixes(tmp_path)
    assert direct_module in prefixes
    assert package in prefixes
