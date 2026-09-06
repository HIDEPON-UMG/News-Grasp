"""NoPublish保存bundleからproduction-mode sealへの実受渡し契約。"""
import hashlib
from pathlib import Path

import pytest

from tools import news_grasp_direct_runtime as runtime
from tools import news_grasp_release_nopublish as release


@pytest.fixture
def bundle(tmp_path, request):
    release._load_release_runtime_modules()
    issue = "2026-09-05"
    root = tmp_path / "repo"
    root.mkdir()
    hashes = {}
    for relative in ("docs/index.html", f"build/tts/{issue}.mp3", f"build/tts/deepdive/{issue}.mp3",
                     f"build/youtube-podcast/{issue}.mp4", f"build/youtube-podcast-deepdive/{issue}.mp4"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    legacy = getattr(request, "param", False)
    store = runtime.DirectRunStore(tmp_path / "state", test_only_allow_semantic_verifier=legacy)
    run = runtime.start_run(store, cwd=root, issue_date=issue, run_intent="release_nopublish",
        scheduler_trigger_at=f"{issue}T06:00:00+09:00", source_baseline="a" * 40,
        remote_base_sha="a" * 40, runtime_generation=runtime.RUNTIME_SCHEMA_V2 + ":" + "d" * 64,
        manifest_id="e" * 64 if legacy else "",
        manifest_reservation_id="b" * 64, allowed_side_effect_ids=["release-nopublish-local-observation"])
    def invoke(**extra):
        return release._materialize_local_bundle(repo_root=root, issue_date=issue, run_id=run["run_id"],
            content_receipt={"artifact_hashes": hashes}, store=store,
            writer_lease=run["writer_lease"], fencing_token=run["fencing_token"], **extra)
    return store, run, hashes, invoke


def test_empty_start_manifest_seals_using_start_seal_and_media_hashes(bundle):
    store, run, hashes, invoke = bundle
    assert runtime.inspect_run(store, run_id=run["run_id"])["manifest_id"] == ""
    result = invoke()
    assert result["ok"] is True
    seal = result["publish_seal"]
    assert seal["releaseCommitSha"] == "a" * 40
    assert len(seal["manifestId"]) == 64
    assert seal["externalOperationIds"] == []
    assert seal["externalInputHashes"] == {k: v for k, v in hashes.items() if k.startswith("build/")}


def test_same_bundle_seal_is_idempotent(bundle):
    *_, invoke = bundle
    assert invoke()["publish_seal"] == invoke()["publish_seal"]


def test_caller_source_override_does_not_replace_start_seal(bundle):
    *_, invoke = bundle
    assert invoke(source_baseline="c" * 40)["publish_seal"]["releaseCommitSha"] == "a" * 40


def test_missing_media_does_not_seal(bundle):
    store, run, hashes, invoke = bundle
    del hashes["build/tts/2026-09-05.mp3"]
    assert invoke()["ok"] is False
    assert runtime.inspect_run(store, run_id=run["run_id"])["publish_seal"] == {}


@pytest.mark.parametrize("bundle", [True], indirect=True)
def test_existing_nopublish_manifest_is_preserved_on_resume(bundle):
    store, run, _, invoke = bundle
    before = runtime.inspect_run(store, run_id=run["run_id"])["manifest_id"]
    result = invoke()
    assert result["publish_seal"]["manifestId"] == before


def test_consumer_binds_external_receipt_time_not_later_claim_time(bundle, monkeypatch):
    store, run, _, _ = bundle
    external_time = "2026-09-05T06:10:00+09:00"
    def saved_receipt(store, *, operation_id, **kwargs):
        if operation_id == "external_publication":
            return {"applied_at": external_time, "producer_receipt": {"external_effect_count": 0}}
        return {"producer_receipt": {"content_generation": {"ok": True},
            "release_bundle": {"ok": True, "bundle_id": "saved-bundle", "artifact_hashes": {}}}}
    monkeypatch.setattr(runtime, "get_daily_operation_receipt", saved_receipt)
    result = release._local_consumer_receipt(store=store, run_id=run["run_id"],
        issue_date="2026-09-05", run_intent="release_nopublish",
        writer_lease=run["writer_lease"], fencing_token=run["fencing_token"])
    assert result["freshnessBinding"]["updatedAt"] == external_time
