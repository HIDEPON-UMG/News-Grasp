from __future__ import annotations

import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest


ISSUE_DATE = "2026-09-01"
RUN_ID = "direct-2026-09-01-test"
RUN_INTENT = "scheduled_production_direct"


def _api():
    return importlib.import_module("tools.news_grasp_publish_contract")


def _manifest(tmp_path: Path) -> dict:
    api = _api()
    for relative in (
        "docs/index.html",
        "docs/sw.js",
        f"docs/{ISSUE_DATE}/index.html",
        f"docs/{ISSUE_DATE}/summary/index.html",
        f"digest/DeepDive/{ISSUE_DATE}-DeepDive.md",
        f"docs/deepdive/{ISSUE_DATE}/index.html",
        "build/tts/daily/latest_audio.json",
        "build/tts/deepdive/latest_audio.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html><head></head><body>fixture</body></html>" if relative.endswith(".html") else "fixture", encoding="utf-8")
    initial = api.build_publish_manifest(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        run_intent=RUN_INTENT,
        source_baseline="a" * 40,
    )
    for row in initial["entries"]:
        if row["artifactKind"] == "publish_manifest":
            continue
        path = tmp_path / row["localPath"]
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".html":
            path.write_text("<html><head></head><body>fixture</body></html>", encoding="utf-8")
        elif path.suffix == ".json":
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_text("fixture", encoding="utf-8")
    return api.build_publish_manifest(repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=RUN_ID, run_intent=RUN_INTENT, source_baseline="a" * 40)


def _lease(api, tmp_path: Path):
    return api.PublishLeaseStore(tmp_path / "lease-state", test_only_allow_noncanonical=True, test_only_skip_runtime_binding=True)


def test_publish_lease_rejects_runtime_database_replacement(tmp_path: Path, monkeypatch) -> None:
    api = _api()
    runtime = importlib.import_module("tools.news_grasp_direct_runtime")
    state_root = tmp_path / "state"
    verifier = object()
    runtime_store = runtime.DirectRunStore(state_root, semantic_verifier=verifier, test_only_allow_semantic_verifier=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    run = runtime.start_run(runtime_store, cwd=repo, issue_date=ISSUE_DATE)
    lease_store = api.PublishLeaseStore(state_root, test_only_allow_noncanonical=True)
    original_pre_connect = lease_store._pre_connect

    def replace_after_snapshot():
        identity = original_pre_connect()
        replacement = state_root / "replacement.sqlite3"
        replacement.write_bytes(lease_store.runtime_db_path.read_bytes())
        os.replace(replacement, lease_store.runtime_db_path)
        return identity

    monkeypatch.setattr(lease_store, "_pre_connect", replace_after_snapshot)
    result = lease_store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id=run["run_id"], token=run["writer_lease"], ttl_seconds=600)
    assert result["ok"] is False
    assert result["status"] == "runtime_writer_identity_changed"


def test_guarded_repo_write_rejects_parent_redirect_without_touching_victim(tmp_path: Path) -> None:
    api = _api()
    repo = tmp_path / "repo"
    parent = repo / "docs"
    parent.mkdir(parents=True)
    target = parent / "index.html"
    target.write_text("original", encoding="utf-8")
    guard = api._capture_repo_write_guard(repo, "docs/index.html")
    held = repo / "docs-held"
    parent.rename(held)
    victim = tmp_path / "victim"
    victim.mkdir()
    victim_target = victim / "index.html"
    victim_target.write_text("victim", encoding="utf-8")
    junction_created = False
    try:
        if os.name == "nt":
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(parent), str(victim)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if created.returncode != 0:
                held.rename(parent)
                pytest.skip("directory junction creation is unavailable")
            junction_created = True
        else:
            try:
                parent.symlink_to(victim, target_is_directory=True)
            except OSError:
                held.rename(parent)
                pytest.skip("directory symlink creation is unavailable")
        with pytest.raises(ValueError, match="path_invalid|reparse_forbidden|identity_changed"):
            api._guarded_repo_write(repo, guard, b"attacker-controlled")
        assert victim_target.read_text(encoding="utf-8") == "victim"
    finally:
        if junction_created:
            os.rmdir(parent)
        elif parent.is_symlink():
            parent.unlink()
        if held.exists() and not parent.exists():
            held.rename(parent)


def test_r01_manifest_includes_home_and_exact_write_set(tmp_path: Path) -> None:
    """R01: 公開トップをmanifestとexact write setの必須entryにする。"""
    api = _api()
    manifest = _manifest(tmp_path)
    paths = {row["localPath"] for row in manifest["entries"]}
    assert manifest["schemaVersion"] == "NEWS_GRASP_PUBLISH_MANIFEST_V2"
    assert "docs/index.html" in paths
    assert "docs/sw.js" in paths
    assert {
        "youtube_daily_state",
        "youtube_deepdive_state",
        "playlist_state",
        "distribution_binding",
        "notification_v2",
    } <= {row["artifactKind"] for row in manifest["entries"]}
    assert api.verify_manifest(manifest, repo_root=tmp_path)["ok"] is True
    tampered = {**manifest, "entries": [row for row in manifest["entries"] if row["localPath"] != "docs/index.html"]}
    assert "manifest_home_missing" in api.verify_manifest(tampered, repo_root=tmp_path)["reasonCodes"]


def _semantic_fixture(manifest: dict) -> dict[str, str]:
    marker = manifest["manifestId"]
    daily_url = manifest["audio"]["daily"]["publicUrl"]
    deepdive_href = f"https://hidepon-umg.github.io/News-Grasp/deepdive/{ISSUE_DATE}/"
    summary_href = f"https://hidepon-umg.github.io/News-Grasp/{ISSUE_DATE}/summary/"
    category_rows = [row for row in manifest["entries"] if row["artifactKind"] == "category_html"]
    category_links = "".join(f'<a href="{row["publicUrl"]}">category</a>' for row in category_rows)
    meta = f'<meta name="news-grasp-manifest-id" content="{marker}">'
    pages = {
        "home": f'{meta}<source src="{daily_url}"><a href="{deepdive_href}">DeepDive</a><a href="{summary_href}">Summary</a>',
        "daily": f"{meta}<main>{ISSUE_DATE}{category_links}</main>",
        "summary": f'{meta}<main>{ISSUE_DATE}<p class="summary-hero__lead">検証済み材料を分離し、次の観測点へつなぐ本日の編集上の振り返りです。</p></main>',
        "deepdive": f"{meta}<main>{ISSUE_DATE}</main>",
        "publish_status": f'{{"date":"{ISSUE_DATE}","manifestId":"{marker}","result":"success"}}',
    }
    for category_id in manifest["scheduledCategoryIds"]:
        pages[f"category:{category_id}"] = f"{meta}<main>{ISSUE_DATE} category</main>"
    return pages


def test_r02_daily_audio_href_is_semantic_required(tmp_path: Path) -> None:
    """R02: HTTP成功だけではなくdaily audio href一致を要求する。"""
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    pages["home"] = pages["home"].replace(manifest["audio"]["daily"]["publicUrl"], "")
    assert "daily_audio_href_missing" in api.verify_semantic_pages(manifest, pages)["reasonCodes"]
    assert api.verify_semantic_pages(manifest, _semantic_fixture(manifest))["ok"] is True


def test_semantic_parser_accepts_standard_meta_without_name(tmp_path: Path) -> None:
    """meta charsetなどname属性を持たない正規head要素でconsumerを落とさない。"""

    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    pages["home"] = '<meta charset="utf-8">' + pages["home"]

    assert api.verify_semantic_pages(manifest, pages)["ok"] is True


def test_r03_deepdive_link_is_semantic_required(tmp_path: Path) -> None:
    """R03: 当日DeepDiveが存在してもトップから未リンクならRedにする。"""
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    pages["home"] = pages["home"].replace(f"/News-Grasp/deepdive/{ISSUE_DATE}/", "")
    assert "deepdive_href_missing" in api.verify_semantic_pages(manifest, pages)["reasonCodes"]


def test_home_summary_and_daily_scheduled_category_links_are_required(tmp_path: Path) -> None:
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    summary_href = f"https://hidepon-umg.github.io/News-Grasp/{ISSUE_DATE}/summary/"
    pages["home"] = pages["home"].replace(summary_href, "")
    first_category = next(row for row in manifest["entries"] if row["artifactKind"] == "category_html")
    pages["daily"] = pages["daily"].replace(first_category["publicUrl"], "")
    reasons = api.verify_semantic_pages(manifest, pages)["reasonCodes"]
    assert "summary_href_missing" in reasons
    assert any(reason.startswith("scheduled_category_href_missing:") for reason in reasons)


def test_r04_summary_reflection_is_semantic_required(tmp_path: Path) -> None:
    """R04: Summaryの振り返り本文欠落をHTTP 200で隠さない。"""
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    pages["summary"] = f"{ISSUE_DATE} {manifest['manifestId']}"
    assert "summary_reflection_missing" in api.verify_semantic_pages(manifest, pages)["reasonCodes"]


def test_manifest_marker_and_links_in_comments_are_not_semantic_evidence(tmp_path: Path) -> None:
    """security Red: comment/script中の文字列はmeta・href・reflectionの証拠にしない。"""
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    marker = manifest["manifestId"]
    daily = manifest["audio"]["daily"]["publicUrl"]
    deepdive = f"https://hidepon-umg.github.io/News-Grasp/deepdive/{ISSUE_DATE}/"
    pages["home"] = f"<!-- {marker} {daily} {deepdive} --><script>{json.dumps([marker, daily, deepdive])}</script>"
    result = api.verify_semantic_pages(manifest, pages)
    assert result["ok"] is False
    assert {"manifest_marker_missing:home", "daily_audio_href_missing", "deepdive_href_missing"} <= set(result["reasonCodes"])


def test_manifest_recomputes_marker_neutral_artifact_digest(tmp_path: Path) -> None:
    """security Red: materialize後のHTML改ざんを実bytes再計算で検出する。"""
    api = _api()
    manifest = _manifest(tmp_path)
    api.materialize_manifest_markers(tmp_path, manifest, lease_store=_lease(api, tmp_path), writer_lease="token-a", test_only_allow_noncanonical_lease_store=True)
    home = tmp_path / "docs" / "index.html"
    home.write_text(home.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
    result = api.verify_manifest(manifest, repo_root=tmp_path, require_files=True)
    assert "manifest_artifact_digest_mismatch:docs/index.html" in result["reasonCodes"]


def test_manifest_digest_does_not_neutralize_script_or_comment_literals(tmp_path: Path) -> None:
    """marker同形文字列でもscript/comment内の変更はartifact bytesとして検出する。"""
    api = _api()
    target = tmp_path / "index.html"
    target.write_text(f'<html><head><script>"<meta name=\\"news-grasp-manifest-id\\" content=\\"{"a" * 64}\\">"</script><!-- <meta name="news-grasp-manifest-id" content="{"c" * 64}"> --></head></html>', encoding="utf-8")
    before = api._artifact_digest(target, "public_home")
    target.write_text(f'<html><head><script>"<meta name=\\"news-grasp-manifest-id\\" content=\\"{"b" * 64}\\">"</script><!-- <meta name="news-grasp-manifest-id" content="{"d" * 64}"> --></head></html>', encoding="utf-8")
    assert api._artifact_digest(target, "public_home") != before


def test_manifest_digest_neutralizes_only_owned_direct_head_marker(tmp_path: Path) -> None:
    api = _api()
    target = tmp_path / "index.html"
    base = "<html><head></head><body>ok</body></html>"
    target.write_text(base, encoding="utf-8")
    before = api._artifact_digest(target, "public_home")
    target.write_text(f'<html><head>  <meta name="news-grasp-manifest-id" content="{"a" * 64}">\n</head><body>ok</body></html>', encoding="utf-8")
    assert api._artifact_digest(target, "public_home") == before


@pytest.mark.parametrize(
    "html",
    [
        f'<html><head></head><body><meta name="news-grasp-manifest-id" content="{"a" * 64}"></body></html>',
        f'<html><head>  <meta name="news-grasp-manifest-id" content="{"a" * 64}">\n  <meta name="news-grasp-manifest-id" content="{"b" * 64}">\n</head></html>',
    ],
)
def test_manifest_digest_rejects_noncanonical_or_duplicate_marker(tmp_path: Path, html: str) -> None:
    api = _api()
    target = tmp_path / "index.html"
    target.write_text(html, encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_meta_shape_invalid"):
        api._artifact_digest(target, "public_home")


def test_manifest_rejects_self_exclusion_on_required_artifact(tmp_path: Path) -> None:
    """security Red: self_excludedはcanonical manifest entryだけに限定する。"""
    api = _api()
    manifest = _manifest(tmp_path)
    home = next(row for row in manifest["entries"] if row["localPath"] == "docs/index.html")
    home["digestAuthority"] = "self_excluded"
    home["digest"] = "self_excluded"
    result = api.verify_manifest(manifest, repo_root=tmp_path, require_files=True)
    assert "manifest_self_exclusion_forbidden:docs/index.html" in result["reasonCodes"]


def test_manifest_rejects_required_flag_downgrade_even_with_rehashed_identity(tmp_path: Path) -> None:
    """security Red: producer由来required flagをmanifest自己申告でoptional化できない。"""
    api = _api()
    manifest = _manifest(tmp_path)
    for row in manifest["entries"]:
        if row["artifactKind"] != "publish_manifest":
            row["required"] = False
    manifest["manifestId"] = __import__("hashlib").sha256(api._json_bytes(api._manifest_identity(manifest))).hexdigest()
    result = api.verify_manifest(manifest, repo_root=tmp_path, require_files=True)
    assert "manifest_entry_policy_mismatch" in result["reasonCodes"]


def test_manifest_rejects_forged_url_audio_and_non_git_baseline(tmp_path: Path) -> None:
    api = _api()
    manifest = _manifest(tmp_path)
    home = next(row for row in manifest["entries"] if row["artifactKind"] == "public_home")
    home["publicUrl"] = "https://evil.invalid/"
    home["linkFrom"] = "forged"
    manifest["audio"]["daily"]["publicUrl"] = "https://evil.invalid/audio.mp3"
    manifest["sourceBaseline"] = "not-a-git-sha"
    manifest["manifestId"] = __import__("hashlib").sha256(api._json_bytes(api._manifest_identity(manifest))).hexdigest()
    result = api.verify_manifest(manifest, repo_root=tmp_path, require_files=True)
    assert {"manifest_entry_policy_mismatch", "manifest_audio_projection_mismatch", "manifest_source_baseline_invalid"} <= set(result["reasonCodes"])


def test_hidden_or_template_links_are_not_reader_visible_evidence(tmp_path: Path) -> None:
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    marker = manifest["manifestId"]
    daily = manifest["audio"]["daily"]["publicUrl"]
    deepdive = f"https://hidepon-umg.github.io/News-Grasp/deepdive/{ISSUE_DATE}/"
    pages["home"] = f'<meta name="news-grasp-manifest-id" content="{marker}"><template><source src="{daily}"><a href="{deepdive}">hidden</a></template>'
    pages["summary"] = f'<meta name="news-grasp-manifest-id" content="{marker}"><main>{ISSUE_DATE}<p class="summary-hero__lead" hidden>これは読者に見えない十分に長い振り返り本文です。</p></main>'
    result = api.verify_semantic_pages(manifest, pages)
    assert {"daily_audio_href_missing", "deepdive_href_missing", "summary_reflection_missing"} <= set(result["reasonCodes"])


def test_malformed_closing_tag_cannot_escape_hidden_surface(tmp_path: Path) -> None:
    api = _api()
    manifest = _manifest(tmp_path)
    pages = _semantic_fixture(manifest)
    marker = manifest["manifestId"]
    daily = manifest["audio"]["daily"]["publicUrl"]
    deepdive = f"https://hidepon-umg.github.io/News-Grasp/deepdive/{ISSUE_DATE}/"
    pages["home"] = f'<meta name="news-grasp-manifest-id" content="{marker}"><div hidden><span>x</bogus><source src="{daily}"><a href="{deepdive}">hidden</a></div>'
    pages["summary"] = f'<meta name="news-grasp-manifest-id" content="{marker}"><main>{ISSUE_DATE}<template><span>x</bogus><p class="summary-hero__lead">これは読者に見えない十分に長い振り返り本文です。</p></template></main>'
    result = api.verify_semantic_pages(manifest, pages)
    assert {"daily_audio_href_missing", "deepdive_href_missing", "summary_reflection_missing"} <= set(result["reasonCodes"])


def test_r06_claim_binding_rejects_wrong_run_intent_and_source_url() -> None:
    """R06: 内部整合していてもrun-intent/sourceUrlの誤束縛はRedにする。"""
    api = _api()
    row = api.verify_claim_binding(
        {"issueDate": ISSUE_DATE, "runIntent": "repair_publish", "sourceUrl": "https://wrong.example/"},
        issue_date=ISSUE_DATE,
        run_intent=RUN_INTENT,
        allowed_source_urls={"https://source.example/article"},
    )
    assert row["ok"] is False
    assert {"claim_run_intent_mismatch", "claim_source_url_unbound"} <= set(row["reasonCodes"])


def test_r07_generic_dialogue_value_is_red() -> None:
    """R07: 汎用的で記事固有の価値を示さない対談valueをRedにする。"""
    api = _api()
    row = api.verify_claim_evidence_value(
        claim="中央銀行は金利を据え置いた",
        evidence="詳細は記事を参照してください。",
    )
    assert row["ok"] is False
    assert "dialogue_value_generic" in row["reasonCodes"]


def test_r08_normalized_claim_evidence_equality_is_red() -> None:
    """R08: 空白・句読点だけが違うclaim/evidenceを独立根拠とみなさない。"""
    api = _api()
    row = api.verify_claim_evidence_value(
        claim="AI 投資は、前年比 20% 増加した。",
        evidence="AI投資は前年比20％増加した",
    )
    assert row["ok"] is False
    assert "claim_evidence_normalized_equal" in row["reasonCodes"]
    assert api.verify_claim_evidence_value(
        claim="AI投資は前年比20%増加した",
        evidence="調査対象120社のうち72社が予算増額を回答した",
    )["ok"] is True


def test_r09_dirty_or_unbound_checkout_is_not_remote_authority() -> None:
    """R09: dirty/detached/remote未一致のcheckoutをremote commit Greenにしない。"""
    api = _api()
    assert api.evaluate_checkout_observation({"clean": False, "detached": True, "head": "a", "remoteHead": "a"})["ok"] is False
    assert api.evaluate_checkout_observation({"clean": True, "detached": True, "head": "a", "remoteHead": "a", "baselineBound": True})["ok"] is True


def test_r14_required_warning_is_red_optional_warning_is_preserved() -> None:
    """R14: required warningをsuccessへ変換せずoptional warningだけを分離する。"""
    api = _api()
    red = api.aggregate_external_surfaces({"pages": {"required": True, "status": "warning"}})
    assert red["ok"] is False
    green = api.aggregate_external_surfaces(
        {
            "pages": {"required": True, "status": "verified"},
            "provider_teardown": {"required": False, "status": "warning"},
        }
    )
    assert green["status"] == "verified_with_warnings"
    assert green["post_publish_issue_list"]


def test_r15_history_quarantine_is_not_daily_authority() -> None:
    """R15: history Redは隔離し、当日manifestの成否へ混入させない。"""
    api = _api()
    result = api.evaluate_history_promotion(
        daily_manifest_ok=True,
        history_candidates=[{"path": "docs/2026-08-31/index.html", "status": "red"}],
    )
    assert result["dailyAuthority"] == "verified"
    assert result["promoted"] == []
    assert result["quarantine"][0]["status"] == "red"


def test_publish_lease_fences_same_artifact_across_run_intents(tmp_path: Path) -> None:
    """同一日・同一artifactへの別run writerをrun-intentに関係なく拒否する。"""
    api = _api()
    store = api.PublishLeaseStore(tmp_path / "state", test_only_allow_noncanonical=True, test_only_skip_runtime_binding=True)
    first = store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id="run-a", token="token-a")
    assert first["ok"] is True
    conflict = store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id="run-b", token="token-b")
    assert conflict["exitCode"] == 4


def test_publish_lease_rejects_token_takeover_and_path_alias(tmp_path: Path) -> None:
    """security Red: 同一run別tokenと親参照aliasでlease fenceを迂回できない。"""
    api = _api()
    store = api.PublishLeaseStore(tmp_path / "state", test_only_allow_noncanonical=True, test_only_skip_runtime_binding=True)
    assert store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id="run-a", token="token-a")["ok"] is True
    assert store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id="run-a", token="token-b")["exitCode"] == 4
    with pytest.raises(ValueError, match="artifact_path_invalid"):
        store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/x/../index.html"], run_id="run-b", token="token-b")


def test_publish_lease_production_rejects_alternate_root_and_too_short_ttl(tmp_path: Path, monkeypatch) -> None:
    """production state rootのすり替えと1秒leaseをfail-closedにする。"""
    api = _api()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    with pytest.raises(ValueError, match="state_root_not_canonical"):
        api.PublishLeaseStore(tmp_path / "alternate")
    store = api.PublishLeaseStore(tmp_path / "test", test_only_allow_noncanonical=True, test_only_skip_runtime_binding=True)
    with pytest.raises(ValueError, match="lease_ttl_out_of_policy"):
        store.acquire(issue_date=ISSUE_DATE, artifact_paths=["docs/index.html"], run_id="run", token="token", ttl_seconds=1)


def test_publish_lease_rejects_canonical_parent_junction(tmp_path: Path, monkeypatch) -> None:
    """LOCALAPPDATA canonical文字列がjunctionならlease DB/ATTACHをredirect先へ作らない。"""
    api = _api()
    local_app_data = tmp_path / "LocalAppData"
    local_app_data.mkdir()
    redirect = tmp_path / "redirect"
    redirect.mkdir()
    junction = local_app_data / "News-Grasp"
    if os.name == "nt":
        created = __import__("subprocess").run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(redirect)],
            capture_output=True,
            check=False,
            shell=False,
        )
        if created.returncode != 0:
            pytest.skip("directory junction creation is unavailable")
    else:
        junction.symlink_to(redirect, target_is_directory=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    try:
        with pytest.raises(ValueError, match="state_root_reparse_forbidden"):
            api.PublishLeaseStore(junction / "direct-mainline")
        assert not (redirect / "direct-mainline" / "publish-leases.sqlite3").exists()
    finally:
        os.rmdir(junction)


def test_distribution_binding_validates_all_sources_before_any_target_mutation(tmp_path: Path) -> None:
    """dailyが正しくてもdeepdiveがRedなら3 targetすべてをpreimageのまま保つ。"""
    api = _api()
    daily_source = tmp_path / "source-daily.json"
    deep_source = tmp_path / "source-deep.json"
    valid = {
        "status": "public",
        "videoId": "daily-video",
        "playlistId": "playlist",
        "playlistItemId": "daily-item",
    }
    daily_source.write_text(json.dumps({ISSUE_DATE: valid}), encoding="utf-8")
    deep_source.write_text(json.dumps({ISSUE_DATE: {**valid, "status": "private"}}), encoding="utf-8")
    targets = [
        tmp_path / "build" / "youtube-podcast" / "uploads.json",
        tmp_path / "build" / "youtube-podcast-deepdive" / "uploads.json",
        tmp_path / "build" / "distribution" / ISSUE_DATE / "playlist.json",
    ]
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"preimage": index}), encoding="utf-8")
    before = {target: target.read_bytes() for target in targets}

    with pytest.raises(ValueError, match="youtube_deepdive_not_public"):
        api.bind_existing_distribution_receipts(
            repo_root=tmp_path,
            issue_date=ISSUE_DATE,
            run_id=RUN_ID,
            run_intent=RUN_INTENT,
            daily_upload_state=daily_source,
            deepdive_upload_state=deep_source,
            lease_store=_lease(api, tmp_path),
            writer_lease="token-a",
        )

    assert {target: target.read_bytes() for target in targets} == before


def test_distribution_binding_exactly_binds_run_and_component_receipts(tmp_path: Path) -> None:
    """同日別runのdistributionとcurrent playlistの組合せをexact identityで拒否する。"""
    api = _api()
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    daily = {"status": "public", "videoId": "daily-video", "playlistId": "daily-list", "playlistItemId": "daily-item"}
    deepdive = {"status": "public", "videoId": "deep-video", "playlistId": "deep-list", "playlistItemId": "deep-item"}
    daily_source = tmp_path / "source-daily.json"
    deep_source = tmp_path / "source-deep.json"
    daily_source.write_text(json.dumps({ISSUE_DATE: daily}), encoding="utf-8")
    deep_source.write_text(json.dumps({ISSUE_DATE: deepdive}), encoding="utf-8")
    distribution_path = tmp_path / "data" / "distribution" / f"{ISSUE_DATE}.json"
    distribution_path.parent.mkdir(parents=True)
    distribution = {
        "date": ISSUE_DATE,
        "status": "published_ok",
        "primary_podcast_state": "build/youtube-podcast/uploads.json",
        "deepdive_podcast_state": "build/youtube-podcast-deepdive/uploads.json",
        "latest_audio_state": "build/tts/daily/latest_audio.json",
        "deepdive_audio_state": "build/tts/deepdive/latest_audio.json",
        "generated_at": "2026-09-01T06:00:00+09:00",
        "playlist": {"daily": {key: daily[key] for key in ("videoId", "playlistId", "playlistItemId")}, "deepdive": {key: deepdive[key] for key in ("videoId", "playlistId", "playlistItemId")}},
        "notification": {"status": "sent", "sent_count": 1},
    }
    distribution_path.write_text(json.dumps(distribution), encoding="utf-8")
    for relative in (
        "build/tts/daily/latest_audio.json",
        "build/tts/deepdive/latest_audio.json",
        f"build/notification/{ISSUE_DATE}.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"fixture": relative}), encoding="utf-8")
    api.bind_existing_distribution_receipts(
        repo_root=tmp_path,
        issue_date=ISSUE_DATE,
        run_id=RUN_ID,
        run_intent=RUN_INTENT,
        daily_upload_state=daily_source,
        deepdive_upload_state=deep_source,
        lease_store=_lease(api, tmp_path),
        writer_lease="token-a",
    )
    manifest = {
        "entries": [
            {"artifactKind": kind, "localPath": path, "required": True}
            for kind, path in (
                ("daily_audio_state", "build/tts/daily/latest_audio.json"),
                ("deepdive_audio_state", "build/tts/deepdive/latest_audio.json"),
                ("youtube_daily_state", "build/youtube-podcast/uploads.json"),
                ("youtube_deepdive_state", "build/youtube-podcast-deepdive/uploads.json"),
                ("playlist_state", f"build/distribution/{ISSUE_DATE}/playlist.json"),
                ("distribution_binding", f"build/distribution/{ISSUE_DATE}/binding.json"),
                ("notification_v2", f"build/notification/{ISSUE_DATE}.json"),
                ("distribution", f"data/distribution/{ISSUE_DATE}.json"),
            )
        ]
    }
    assert completion._required_distribution(tmp_path, ISSUE_DATE, manifest=manifest, run_id=RUN_ID, run_intent=RUN_INTENT)["ok"] is True
    distribution["generated_at"] = "2026-09-01T07:00:00+09:00"
    distribution_path.write_text(json.dumps(distribution), encoding="utf-8")
    red = completion._required_distribution(tmp_path, ISSUE_DATE, manifest=manifest, run_id=RUN_ID, run_intent=RUN_INTENT)
    assert "distribution_run_binding_invalid" in red["failures"]


def test_manifest_materializer_rejects_repo_escape(tmp_path: Path) -> None:
    """security Red: untrusted manifest mappingからrepo外へ書かない。"""
    api = _api()
    manifest = _manifest(tmp_path)
    manifest["entries"].append({"localPath": "../../outside.html", "artifactKind": "public_home"})
    with pytest.raises(ValueError, match="manifest_path_invalid"):
        api.materialize_manifest_markers(tmp_path, manifest, lease_store=_lease(api, tmp_path), writer_lease="token-a", test_only_allow_noncanonical_lease_store=True)


def test_materializer_connects_exact_write_set_lease_to_real_write_path(tmp_path: Path) -> None:
    api = _api()
    manifest = _manifest(tmp_path)
    store = _lease(api, tmp_path)
    api.materialize_manifest_markers(tmp_path, manifest, lease_store=store, writer_lease="token-a", test_only_allow_noncanonical_lease_store=True)
    with pytest.raises(PermissionError, match="lease_conflict"):
        api.materialize_manifest_markers(tmp_path, manifest, lease_store=store, writer_lease="token-b", test_only_allow_noncanonical_lease_store=True)


def test_pages_deployment_must_match_remote_head_manifest_and_issue() -> None:
    """Pages successが別SHAならRed、remote HEAD一致ならGreenにする。"""
    api = _api()
    red = api.evaluate_pages_deployment(
        remote_head="a" * 40,
        workflow_runs=[{"head_sha": "b" * 40, "path": ".github/workflows/deploy-pages.yml", "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success"}],
        manifest_id="f" * 64,
        issue_date=ISSUE_DATE,
    )
    assert "pages_successful_head_missing" in red["reasonCodes"]
    green = api.evaluate_pages_deployment(
        remote_head="a" * 40,
        workflow_runs=[{"head_sha": "a" * 40, "path": ".github/workflows/deploy-pages.yml", "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success", "html_url": "https://example.test/run"}],
        manifest_id="f" * 64,
        issue_date=ISSUE_DATE,
    )
    assert green["ok"] is True


def test_pages_deployment_rejects_same_sha_from_wrong_workflow() -> None:
    """security Red: pages-smoke等の別workflow成功をPages authorityへ昇格しない。"""
    api = _api()
    result = api.evaluate_pages_deployment(
        remote_head="a" * 40,
        workflow_runs=[{"head_sha": "a" * 40, "path": ".github/workflows/pages-smoke.yml", "event": "push", "head_branch": "main", "status": "completed", "conclusion": "success"}],
        manifest_id="f" * 64,
        issue_date=ISSUE_DATE,
    )
    assert result["ok"] is False
    assert "pages_successful_head_missing" in result["reasonCodes"]


def test_production_public_base_url_requires_https() -> None:
    """security Red: HTTPをpublic completion probeのauthorityにしない。"""
    completion = importlib.import_module("tools.news_grasp_direct_completion")
    with pytest.raises(ValueError, match="scheme"):
        completion.validate_public_base_url("http://hidepon-umg.github.io/News-Grasp/")
