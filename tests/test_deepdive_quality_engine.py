from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tools import deepdive_quality


class _FakeResponse:
    def __init__(self, *, body: bytes, final_url: str, status: int = 200) -> None:
        self._body = body
        self._offset = 0
        self._final_url = final_url
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        chunk = self._body[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def getcode(self) -> int:
        return self._status

    def geturl(self) -> str:
        return self._final_url


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _article(path: Path, *, url: str = "https://example.com/source") -> Path:
    sentences = "\n\n".join(
        f"固有根拠{i}では主体{i}が時点{i}に確認できる変化{i}を公表しました。"
        for i in range(20)
    )
    path.write_text(
        "---\ntitle: 品質検証\ndate: 2026-08-01\n---\n\n"
        f"{sentences}\n\n## 参考リンク\n- [一次資料]({url})\n",
        encoding="utf-8",
    )
    return path


def _fetch(url: str) -> dict[str, object]:
    return {
        "url": url,
        "finalUrl": url,
        "httpStatus": 200,
        "fetchedAt": "2026-08-01T06:20:00+09:00",
        "contentSha256": _sha256_text("observed body"),
    }


def _v2_article(path: Path, *, url: str) -> Path:
    evidence = "official protected report confirms recovery transport evidence"
    claim = "限定system transportでも同じ本文検査を通過する"
    declaration = json.dumps(
        {
            "claimId": "transport-fallback",
            "claim": claim,
            "sourceUrl": url,
            "evidence": evidence,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    path.write_text(
        "---\ntitle: 復旧transport検証\ndate: 2026-08-27\n---\n\n"
        f"{claim}。\n\n<!-- claim-source: {declaration} -->\n\n"
        f"## 参考リンク\n- [一次資料]({url})\n",
        encoding="utf-8",
    )
    return path


def _write_llm_dialogue(article: Path) -> Path:
    """LLM生成済みのcanonical対談をmaterializer入力として用意する。"""

    output = article.with_name(article.name.replace("-DeepDive.md", "-DeepDive-dialogue.md"))
    output.write_text(
        "---\n"
        f'source_sha256: "{deepdive_quality._canonical_text_sha256(article.read_bytes())}"\n'
        "---\n\n## 台本\n\n統合検証用対話\n",
        encoding="utf-8",
    )
    return output


def _patch_green_quality_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """意味review以外を所有する旧fixtureではreview境界をGreenに固定する。"""

    monkeypatch.setattr(
        deepdive_quality,
        "_validate_quality_review",
        lambda **_kwargs: (
            [],
            set(),
            {
                "schemaVersion": "DEEPDIVE_QUALITY_REVIEW_V2",
                "computedAverageScore": 4.0,
                "status": "Green",
                "issues": [],
            },
            None,
        ),
    )


@pytest.mark.parametrize(
    "leaked_fragment",
    [
        '<!-- claim-source: {"claimId":"secret"} -->',
        '&lt;!-- claim-source: {&quot;claimId&quot;:&quot;secret&quot;} --&gt;',
        '{"claim":"内部主張","sourceUrl":"https://example.com/private"}',
        '```json\n{"sourceUrl":"https://example.com/private"}\n```',
        '[内部リンク](https://example.com/private)',
    ],
)
def test_rendered_public_surface_rejects_internal_transport_metadata(
    tmp_path: Path,
    leaked_fragment: str,
) -> None:
    manifest = tmp_path / "provenance.json"
    rendered = tmp_path / "index.html"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "fixture",
                "sources": [
                    {"publicHref": "https://example.com/source"},
                ],
            }
        ),
        encoding="utf-8",
    )
    rendered.write_text(
        '<html><body><a href="https://example.com/source">source</a>'
        f"{leaked_fragment}</body></html>",
        encoding="utf-8",
    )

    issues, _evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert "DEEPDIVE_PUBLIC_METADATA_EXPOSED" in issues


def test_audit_issue_maps_duplicate_claim_evidence_to_article_value_invalid(
    tmp_path: Path,
) -> None:
    issue_date = "2026-08-27"
    url = "https://example.com/source"
    claim = "対象企業は2026年8月に限定地域で新しい運用条件を公表した"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    declaration = json.dumps(
        {
            "claimId": "duplicate-evidence",
            "claim": claim,
            "sourceUrl": url,
            "evidence": claim,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    article.write_text(
        "---\ntitle: 重複根拠の検証\ndate: 2026-08-27\n---\n\n"
        f"{claim}。\n\n<!-- claim-source: {declaration} -->\n\n"
        f"## 参考リンク\n- [一次資料]({url})\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[
            deepdive_quality._observed_record(
                url=url,
                final_url=url,
                status=200,
                body=claim.encode("utf-8"),
            )
        ],
        output_path=manifest,
    )

    result = deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date=issue_date,
        include_corpus=False,
    )

    assert "deepdive_article_value_invalid" in result["issueCodes"]
    assert "deepdive_claim_source_fit_invalid" not in result["issueCodes"]


def test_observed_record_decodes_declared_shift_jis_evidence() -> None:
    """一次資料の宣言charsetを使い、日本語evidenceをmojibakeさせない。"""

    evidence = "生成AIによる災害対策本部の意思決定支援に向けた実証を開始"
    body = (
        '<!doctype html><html><head><meta charset="Shift_JIS"></head>'
        f"<body>{evidence}</body></html>"
    ).encode("cp932")

    record = deepdive_quality._observed_record(
        url="https://example.com/shift-jis",
        final_url="https://example.com/shift-jis",
        status=200,
        body=body,
    )

    assert evidence in record["observedText"]


def test_generic_claim_evidence_is_article_value_invalid() -> None:
    issues = deepdive_quality._claim_article_value_issues(
        [
            {
                "claimId": "generic-evidence",
                "claim": "対象企業が条件付き運用を開始した",
                "sourceUrl": "https://example.com/source",
                "evidence": "元記事を確認する。",
            }
        ]
    )

    assert any("DEEPDIVE_ARTICLE_VALUE_INVALID" in issue for issue in issues)


def test_materialize_issue_routes_missing_claim_to_research_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2 recovery: claim不足は本文複製で補わずresearch routeへ戻す。"""

    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _article(article, url="https://example.com/source")
    fetched: list[str] = []
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda url, **_kwargs: (
            fetched.append(url)
            or deepdive_quality._observed_record(
                url=url,
                final_url=url,
                status=200,
                body=(
                    "固有根拠19では主体19が時点19に確認できる変化19を公表しました。"
                ).encode("utf-8"),
            )
        ),
    )

    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    original_article = article.read_text(encoding="utf-8")

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date="2026-08-27",
        )

    assert fetched == ["https://example.com/source"]
    assert article.read_text(encoding="utf-8") == original_article
    assert not (tmp_path / "data" / "deepdive-bundles" / "2026-08-27.json").exists()
    assert not (tmp_path / "data" / "deepdive-provenance" / "2026-08-27.json").exists()
    assert not article.with_name("2026-08-27-DeepDive-dialogue.md").exists()


def test_materialize_issue_missing_claim_fails_closed_without_observed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-02 adversarial: 本文spanを取得できなければ推測annotationしない。"""

    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _article(article, url="https://example.com/source")
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda url, **_kwargs: {
            "url": url,
            "finalUrl": url,
            "httpStatus": 200,
            "fetchedAt": "2026-08-27T00:00:00+00:00",
            "contentSha256": "a" * 64,
            "observedText": "short",
        },
    )

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT bindings_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date="2026-08-27",
        )
    assert "<!-- claim-source:" not in article.read_text(encoding="utf-8")
    assert not (tmp_path / "data" / "deepdive-bundles" / "2026-08-27.json").exists()


def test_materialize_issue_rejects_unrelated_long_body_as_claim_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-02 adversarial: URL 200＋非空本文だけではclaimをGreenにしない。"""

    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _article(article, url="https://example.com/source")
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda url, **_kwargs: deepdive_quality._observed_record(
            url=url,
            final_url=url,
            status=200,
            body=b"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        ),
    )

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT bindings_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date="2026-08-27",
        )


def test_materialize_issue_writes_v2_provenance_dialogue_and_bundle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 Green: claim/provenance/dialogueが同一handlerのGreen receiptへ束縛される。"""

    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"official protected report confirms recovery transport evidence",
        ),
    )

    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    result = deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date="2026-08-27",
    )

    manifest = json.loads(
        (tmp_path / "data" / "deepdive-provenance" / "2026-08-27.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["schemaVersion"] == "DEEPDIVE_ISSUE_BUNDLE_V1"
    assert result["status"] == "Green"
    assert manifest["schemaVersion"] == "DEEPDIVE_SOURCE_PROVENANCE_V2"
    assert manifest["claimBindings"][0]["claimId"] == "transport-fallback"
    assert (tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive-dialogue.md").is_file()


def test_materialize_issue_restores_previous_outputs_when_semantic_review_is_red(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 recovery: semantic Redでは最終pathを部分更新せず旧成果物を保持する。"""

    issue_date = "2026-08-27"
    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)

    manifest = tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json"
    manifest.parent.mkdir(parents=True)
    previous_manifest = b'{"schemaVersion":"PREVIOUS_GREEN"}\n'
    manifest.write_bytes(previous_manifest)
    claim_transport = (
        tmp_path / "data" / "deepdive-claim-source" / f"{issue_date}.json"
    )

    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"official protected report confirms recovery transport evidence",
        ),
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deepdive_quality,
        "_dialogue_source_lineage_issues",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)
    monkeypatch.setattr(
        deepdive_quality,
        "audit_issue",
        lambda **_kwargs: {
            "status": "Red",
            "issues": ["deepdive_article_value_invalid:quality_review_missing"],
        },
    )

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="quality_review_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date=issue_date,
        )

    assert manifest.read_bytes() == previous_manifest
    assert not claim_transport.exists()
    assert not (tmp_path / "data" / "deepdive-bundles" / f"{issue_date}.json").exists()


def test_materialize_issue_restores_rendered_directory_when_renderer_crashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 recovery: renderer途中失敗でも当日公開directoryとbundleを元へ戻す。"""

    from tools import render_deepdive

    issue_date = "2026-08-27"
    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)

    manifest = tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json"
    manifest.parent.mkdir(parents=True)
    previous_manifest = b'{"schemaVersion":"PREVIOUS_GREEN"}\n'
    manifest.write_bytes(previous_manifest)
    rendered_directory = tmp_path / "docs" / "deepdive" / issue_date
    rendered_directory.mkdir(parents=True)
    rendered = rendered_directory / "index.html"
    previous_rendered = b"<html>previous Green</html>\n"
    rendered.write_bytes(previous_rendered)
    newly_created = rendered_directory / "partial.tmp"

    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"official protected report confirms recovery transport evidence",
        ),
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deepdive_quality,
        "_dialogue_source_lineage_issues",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    def crashing_renderer(**_kwargs):
        rendered.write_bytes(b"<html>partial replacement</html>\n")
        newly_created.write_bytes(b"partial\n")
        raise RuntimeError("renderer crashed after partial write")

    monkeypatch.setattr(render_deepdive, "build_deepdive_pages", crashing_renderer)

    with pytest.raises(RuntimeError, match="renderer crashed"):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date=issue_date,
            render_public=True,
        )

    assert manifest.read_bytes() == previous_manifest
    assert rendered.read_bytes() == previous_rendered
    assert not newly_created.exists()
    assert not (
        tmp_path / "data" / "deepdive-claim-source" / f"{issue_date}.json"
    ).exists()
    assert not (tmp_path / "data" / "deepdive-bundles" / f"{issue_date}.json").exists()


def test_materialize_issue_without_llm_dialogue_never_creates_partial_green_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 recovery: LLM対談欠落をdeterministic生成で埋めない。"""

    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"official protected report confirms recovery transport evidence",
        ),
    )
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_LLM_REWRITE_REQUIRED dialogue_staged_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date="2026-08-27",
        )

    assert not (tmp_path / "data" / "deepdive-bundles" / "2026-08-27.json").exists()
    assert not (tmp_path / "data" / "deepdive-provenance" / "2026-08-27.json").exists()
    assert not (tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive-dialogue.md").exists()


def test_materializer_rejects_untracked_reparse_output_scope(
    tmp_path: Path,
) -> None:
    """RC-02 adversarial: 未追跡junction経由でrepo外へbundleを書かない。"""

    article = tmp_path / "digest" / "DeepDive" / "2026-08-27-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url="https://example.com/source")
    outside = tmp_path / "outside-data"
    outside.mkdir()
    data = tmp_path / "data"
    junction_created = False
    try:
        try:
            os.symlink(outside, data, target_is_directory=True)
        except OSError as symlink_error:
            if os.name != "nt":
                pytest.skip(f"directory symlink unavailable: {symlink_error}")
            comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
            created = subprocess.run(
                [comspec, "/d", "/c", "mklink", "/J", str(data), str(outside)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if created.returncode != 0:
                pytest.skip(
                    f"symlink/junction unavailable: {symlink_error}; {created.stderr}"
                )
            junction_created = True

        with pytest.raises(
            deepdive_quality.DeepDiveQualityError,
            match="DEEPDIVE_MATERIALIZER_PATH_INVALID",
        ):
            deepdive_quality.materialize_issue_bundle(
                repo_root=tmp_path,
                issue_date="2026-08-27",
            )
        assert not (outside / "deepdive-provenance" / "2026-08-27.json").exists()
    finally:
        if data.is_symlink():
            data.unlink()
        elif junction_created and data.exists():
            data.rmdir()


def test_materializer_rejects_reparse_stage_leaf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RC-02 adversarial: 攻撃者が置いたstage symlinkを追跡しない。"""

    target = tmp_path / "data" / "deepdive-provenance" / "2026-08-27.json"
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside-stage.json"
    outside.write_text("outside\n", encoding="utf-8")
    stage = target.parent / ".attacker.stage"
    try:
        os.symlink(outside, stage)
    except OSError as error:
        pytest.skip(f"file symlink unavailable: {error}")
    descriptor = os.open(os.devnull, os.O_RDONLY)
    monkeypatch.setattr(
        deepdive_quality.tempfile,
        "mkstemp",
        lambda **_kwargs: (descriptor, str(stage)),
    )

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_MATERIALIZER_PATH_INVALID",
    ):
        deepdive_quality._exclusive_stage_file(target, repo_root=tmp_path)
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_materializer_stage_names_are_exclusive_and_not_pid_predictable() -> None:
    """RC-02 contract: Windows symlink権限に依存せずrandom exclusive stageを固定する。"""

    source = Path(deepdive_quality.__file__).read_text(encoding="utf-8")
    materializer = source.split("def materialize_issue_bundle(", 1)[1].split(
        "def capture_period_provenance(", 1
    )[0]
    stage_helper = source.split("def _exclusive_stage_file(", 1)[1].split(
        "def _safe_repo_path(", 1
    )[0]
    assert "os.getpid" not in materializer
    assert materializer.count("_exclusive_stage_file(") == 3
    assert "tempfile.mkstemp(" in stage_helper
    assert "file_required=True" in stage_helper


def test_live_but_unobserved_url_cannot_become_provenance(
    tmp_path: Path,
) -> None:
    article = _article(tmp_path / "2026-08-01-DeepDive.md")
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_URL_NOT_OBSERVED",
    ):
        deepdive_quality.build_provenance_manifest(
            article_path=article,
            fetch_records=[],
            output_path=tmp_path / "provenance.json",
        )


def test_provenance_binds_article_hash_location_and_public_href(
    tmp_path: Path,
) -> None:
    url = "https://example.com/source"
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    manifest = tmp_path / "provenance.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=manifest,
    )
    assert deepdive_quality.validate_provenance(article, manifest) == []

    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["sources"][0]["publicHref"] = "https://example.com/other"
    value["manifestSha256"] = deepdive_quality.canonical_manifest_sha256(value)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    issues = deepdive_quality.validate_provenance(article, manifest)
    assert any("DEEPDIVE_PUBLIC_HREF_DRIFT" in issue for issue in issues)


def test_provenance_fails_closed_after_article_or_fetch_drift(
    tmp_path: Path,
) -> None:
    url = "https://example.com/source"
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    manifest = tmp_path / "provenance.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=manifest,
    )
    article.write_text(article.read_text(encoding="utf-8") + "\n改変\n", encoding="utf-8")
    issues = deepdive_quality.validate_provenance(article, manifest)
    assert any("DEEPDIVE_ARTICLE_DRIFT" in issue for issue in issues)


def test_provenance_rejects_manifest_hash_tamper(tmp_path: Path) -> None:
    url = "https://example.com/source"
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    manifest = tmp_path / "provenance.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=manifest,
    )
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["sources"][0]["fetchedAt"] = "tampered"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    issues = deepdive_quality.validate_provenance(article, manifest)
    assert "DEEPDIVE_PROVENANCE_HASH_DRIFT" in issues


def test_provenance_rejects_missing_extra_and_duplicate_sources(
    tmp_path: Path,
) -> None:
    url = "https://example.com/source"
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    manifest = tmp_path / "provenance.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=manifest,
    )
    original = json.loads(manifest.read_text(encoding="utf-8"))
    variants = []
    missing = json.loads(json.dumps(original))
    missing["sources"] = []
    variants.append(missing)
    duplicate = json.loads(json.dumps(original))
    duplicate["sources"].append(dict(duplicate["sources"][0]))
    variants.append(duplicate)
    extra = json.loads(json.dumps(original))
    extra_row = dict(extra["sources"][0])
    extra_row["url"] = "https://example.com/extra"
    extra_row["publicHref"] = extra_row["url"]
    extra["sources"].append(extra_row)
    variants.append(extra)
    for index, value in enumerate(variants):
        value["sourceSetSha256"] = deepdive_quality._canonical_sha256(value["sources"])
        value["manifestSha256"] = deepdive_quality.canonical_manifest_sha256(value)
        path = tmp_path / f"variant-{index}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        issues = deepdive_quality.validate_provenance(article, path)
        assert any(
            code in " ".join(issues)
            for code in (
                "DEEPDIVE_URL_SET_DRIFT",
                "DEEPDIVE_PROVENANCE_DUPLICATE_URL",
                "DEEPDIVE_URL_LOCATION_DRIFT",
            )
        )


def _rendered_public_fixture(
    tmp_path: Path,
    *,
    url: str = "https://example.com/source",
    html: bytes | None = None,
) -> tuple[Path, Path]:
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    manifest = tmp_path / "provenance.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=manifest,
    )
    rendered = tmp_path / "docs" / "deepdive" / "2026-08-01" / "index.html"
    if html is not None:
        rendered.parent.mkdir(parents=True)
        rendered.write_bytes(html)
    return manifest, rendered


def test_rendered_public_surface_rejects_missing_html(tmp_path: Path) -> None:
    manifest, rendered = _rendered_public_fixture(tmp_path)

    issues, evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == ["DEEPDIVE_RENDERED_HTML_MISSING"]
    assert evidence == []


def test_rendered_public_surface_rejects_missing_anchor_href(tmp_path: Path) -> None:
    manifest, rendered = _rendered_public_fixture(
        tmp_path,
        html=b'<html><a href="https://example.com/other">other</a></html>',
    )

    issues, evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == [
        "DEEPDIVE_RENDERED_PUBLIC_HREF_MISSING https://example.com/source"
    ]
    assert evidence[0]["path"] == str(rendered.resolve())


def test_rendered_public_surface_rejects_url_text_substitution(tmp_path: Path) -> None:
    url = "https://example.com/source"
    manifest, rendered = _rendered_public_fixture(
        tmp_path,
        url=url,
        html=(
            '<html><script type="application/json">'
            f'{{"url":"{url}"}}'
            '</script><a href="https://example.com/other">other</a></html>'
        ).encode("utf-8"),
    )

    issues, _evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == [f"DEEPDIVE_RENDERED_PUBLIC_HREF_MISSING {url}"]


def test_rendered_public_surface_accepts_entity_bom_and_crlf(tmp_path: Path) -> None:
    url = "https://example.com/source?a=1&b=2"
    manifest, rendered = _rendered_public_fixture(
        tmp_path,
        url=url,
        html=(
            '\ufeff<html>\r\n<a href="https://example.com/source?a=1&amp;b=2">'
            "source</a>\r\n</html>"
        ).encode("utf-8"),
    )

    issues, evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == []
    assert evidence[0]["canonicalTextSha256"] == deepdive_quality._canonical_text_sha256(
        rendered.read_bytes()
    )


def test_rendered_public_surface_rejects_cross_date_html_substitution(
    tmp_path: Path,
) -> None:
    manifest, rendered = _rendered_public_fixture(
        tmp_path,
        url="https://example.com/2026-08-01",
        html=(
            '<html><a href="https://example.com/2026-07-31">old</a></html>'
        ).encode("utf-8"),
    )

    issues, _evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == [
        "DEEPDIVE_RENDERED_PUBLIC_HREF_MISSING https://example.com/2026-08-01"
    ]


def test_rendered_public_surface_validation_does_not_mutate_files(
    tmp_path: Path,
) -> None:
    url = "https://example.com/source"
    manifest, rendered = _rendered_public_fixture(
        tmp_path,
        url=url,
        html=f'<html><a href="{url}">source</a></html>'.encode("utf-8"),
    )
    before = {path: path.read_bytes() for path in (manifest, rendered)}

    issues, _evidence = deepdive_quality.validate_rendered_public_surface(
        manifest,
        rendered,
    )

    assert issues == []
    assert {path: path.read_bytes() for path in before} == before


def test_issue_audit_requires_rendered_public_surface_only_when_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-01"
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    article = _article(deepdive_dir / f"{issue_date}-DeepDive.md")
    dialogue = deepdive_dir / f"{issue_date}-DeepDive-dialogue.md"
    dialogue.write_text("検証用対談", encoding="utf-8")
    manifest = tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch("https://example.com/source")],
        output_path=manifest,
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    pre_generation = deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date=issue_date,
        include_corpus=False,
    )
    post_generation = deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date=issue_date,
        include_corpus=False,
        require_rendered_public=True,
    )

    assert pre_generation["status"] == "Green"
    assert post_generation["status"] == "Red"
    assert "deepdive_public_surface_invalid" in post_generation["issueCodes"]
    assert "DEEPDIVE_RENDERED_HTML_MISSING" in post_generation["issues"]


def test_provenance_manifest_is_worktree_portable_and_crlf_stable(tmp_path: Path) -> None:
    issue_date = "2026-08-01"
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    article = _article(deepdive_dir / f"{issue_date}-DeepDive.md")
    manifest = tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json"

    value = deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch("https://example.com/source")],
        output_path=manifest,
    )
    stable_text = article.read_text(encoding="utf-8").replace("\r\n", "\n")
    article.write_bytes(stable_text.replace("\n", "\r\n").encode("utf-8"))

    assert value["articlePath"] == f"digest/DeepDive/{issue_date}-DeepDive.md"
    assert deepdive_quality.validate_provenance(article, manifest) == []


def test_fetch_record_requires_success_and_content_hash(tmp_path: Path) -> None:
    url = "https://example.com/source"
    article = _article(tmp_path / "2026-08-01-DeepDive.md", url=url)
    for record in (
        {**_fetch(url), "httpStatus": 403},
        {**_fetch(url), "httpStatus": 404},
        {**_fetch(url), "contentSha256": ""},
    ):
        with pytest.raises(
            deepdive_quality.DeepDiveQualityError,
            match="DEEPDIVE_FETCH_RECORD_INVALID",
        ):
            deepdive_quality.build_provenance_manifest(
                article_path=article,
                fetch_records=[record],
                output_path=tmp_path / "invalid.json",
            )


def test_issue_audit_uses_typed_shared_issue_codes(tmp_path: Path) -> None:
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    _article(deepdive_dir / "2026-08-01-DeepDive.md")
    result = deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date="2026-08-01",
    )
    assert result["status"] == "Red"
    assert "deepdive_url_provenance_invalid" in result["issueCodes"]
    assert "deepdive_dialogue_value_invalid" in result["issueCodes"]


def test_audit_history_routes_pre_enforcement_missing_claim_bindings_to_research(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-01"
    url = "https://example.com/source"
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    article = _article(deepdive_dir / f"{issue_date}-DeepDive.md", url=url)
    (deepdive_dir / f"{issue_date}-DeepDive-dialogue.md").write_text(
        "検証用の対談です。\n",
        encoding="utf-8",
    )
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch(url)],
        output_path=tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json",
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    result = deepdive_quality.audit_history(
        repo_root=tmp_path,
        start_date=issue_date,
        end_date=issue_date,
        route="codex_daily_audit",
    )

    day = result["days"][0]
    assert "deepdive_research_evidence_insufficient" in day["issueCodes"]
    assert "DEEPDIVE_CLAIM_SOURCE_BINDINGS_MISSING" in day["issues"]


def test_issue_audit_validates_date_before_any_path_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deepdive_quality,
        "validate_provenance",
        lambda *_args: pytest.fail("invalid date reached path reader"),
    )
    with pytest.raises(ValueError):
        deepdive_quality.audit_issue(
            repo_root=tmp_path,
            issue_date="../../outside",
        )


def _green_issue_row(issue_date: str) -> dict[str, object]:
    return {
        "schemaVersion": deepdive_quality.REPORT_SCHEMA,
        "status": "Green",
        "issueDate": issue_date,
        "issueCodes": [],
        "issues": [],
    }


def _corpus_result(*, issues: list[str]) -> dict[str, object]:
    return {
        "script_count": 2,
        "turn_count": 28,
        "repeated_turn_rate": 1.0 if issues else 0.0,
        "maximum_cross_script_similarity": 1.0 if issues else 0.0,
        "maximum_pair": (
            "2026-07-31-DeepDive-dialogue.md",
            "2026-08-01-DeepDive-dialogue.md",
        ),
        "issues": issues,
    }


def test_issue_audit_rejects_cross_day_dialogue_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    for issue_date in ("2026-07-31", "2026-08-01"):
        _article(deepdive_dir / f"{issue_date}-DeepDive.md")
        (deepdive_dir / f"{issue_date}-DeepDive-dialogue.md").write_text(
            "若手: 仮の対談です。\n\n先輩: 仮の回答です。\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(deepdive_quality, "validate_provenance", lambda *_args: [])
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)
    observed_paths: list[Path] = []

    def red_corpus(paths: list[Path], **_kwargs) -> dict[str, object]:
        observed_paths.extend(paths)
        return _corpus_result(issues=["日跨ぎ台本類似度超過"])

    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "audit_dialogue_corpus",
        red_corpus,
    )
    result = deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date="2026-08-01",
    )
    assert result["status"] == "Red"
    assert "deepdive_dialogue_value_invalid" in result["issueCodes"]
    assert [path.name for path in observed_paths] == [
        "2026-07-31-DeepDive-dialogue.md",
        "2026-08-01-DeepDive-dialogue.md",
    ]
    assert result["dialogueCorpusAudit"]["issues"]


def test_issue_audit_binds_dialogue_sources_to_explicit_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """module 所在repoが別でも auditedFiles を artifact repo だけへ束縛する。"""
    artifact_repo = tmp_path / "artifact-repo"
    runtime_repo = tmp_path / "runtime-repo"
    issue_date = "2026-08-01"
    artifact_dir = artifact_repo / "digest" / "DeepDive"
    runtime_dir = runtime_repo / "digest" / "DeepDive"
    artifact_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    article = _article(artifact_dir / f"{issue_date}-DeepDive.md")
    (runtime_dir / article.name).write_bytes(article.read_bytes())
    dialogue = artifact_dir / f"{issue_date}-DeepDive-dialogue.md"
    dialogue.write_text(
        "---\nsource: digest/DeepDive/2026-08-01-DeepDive.md\n---\n\n"
        "若手: 検証用の問いです。\n\n先輩: 検証用の回答です。\n",
        encoding="utf-8",
    )
    manifest = artifact_repo / "data" / "deepdive-provenance" / f"{issue_date}.json"
    deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[_fetch("https://example.com/source")],
        output_path=manifest,
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "REPO_ROOT",
        runtime_repo,
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)

    result = deepdive_quality.audit_issue(
        repo_root=artifact_repo,
        issue_date=issue_date,
    )

    audited_paths = [Path(str(row["path"])).resolve() for row in result["auditedFiles"]]
    assert result["status"] == "Green"
    assert audited_paths
    assert all(path.is_relative_to(artifact_repo.resolve()) for path in audited_paths)
    assert not any(path.is_relative_to(runtime_repo.resolve()) for path in audited_paths)


def test_period_audit_rejects_cross_day_dialogue_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deepdive_quality,
        "audit_issue",
        lambda *, issue_date, **_kwargs: _green_issue_row(issue_date),
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "audit_dialogue_corpus",
        lambda _paths, **_kwargs: _corpus_result(issues=["日跨ぎ完全反復率超過"]),
    )
    result = deepdive_quality.audit_period(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
    )
    assert result["status"] == "Red"
    assert result["issueCodes"] == ["deepdive_dialogue_value_invalid"]
    assert result["dialogueCorpusAudit"]["issues"]


def test_period_audit_forwards_rendered_public_requirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_audit_issue(**kwargs):
        calls.append(kwargs)
        return _green_issue_row(str(kwargs["issue_date"]))

    monkeypatch.setattr(deepdive_quality, "audit_issue", fake_audit_issue)
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "audit_dialogue_corpus",
        lambda _paths, **_kwargs: _corpus_result(issues=[]),
    )

    deepdive_quality.audit_period(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
        require_rendered_public=True,
    )

    assert len(calls) == 2
    assert all(call["require_rendered_public"] is True for call in calls)


def test_period_audit_exposes_green_corpus_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deepdive_quality,
        "audit_issue",
        lambda *, issue_date, **_kwargs: _green_issue_row(issue_date),
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "audit_dialogue_corpus",
        lambda _paths, **_kwargs: _corpus_result(issues=[]),
    )
    result = deepdive_quality.audit_period(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
    )
    assert result["status"] == "Green"
    assert result["dialogueCorpusAudit"]["script_count"] == 2
    assert result["dialogueCorpusAudit"]["issues"] == []


def test_period_audit_rejects_more_than_thirty_one_days() -> None:
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_PERIOD_TOO_LARGE",
    ):
        deepdive_quality.audit_period(
            repo_root=Path.cwd(),
            start_date="2026-07-01",
            end_date="2026-08-01",
        )


def test_history_audit_enumerates_only_existing_articles_across_rest_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No.10 Primary: 31日超の履歴は実在記事だけを監査し、休載日を欠落扱いしない。"""

    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    existing_dates = ["2026-06-10", "2026-06-12", "2026-08-24", "2026-08-26"]
    for issue_date in existing_dates:
        (deepdive_dir / f"{issue_date}-DeepDive.md").write_text(
            f"# {issue_date}\n",
            encoding="utf-8",
        )
        (deepdive_dir / f"{issue_date}-DeepDive-dialogue.md").write_text(
            "対談は記事として数えない。\n",
            encoding="utf-8",
        )

    audited_dates: list[str] = []

    def fake_audit_issue(*, issue_date: str, **_kwargs):
        audited_dates.append(issue_date)
        return _green_issue_row(issue_date)

    monkeypatch.setattr(deepdive_quality, "audit_issue", fake_audit_issue)
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "audit_dialogue_corpus",
        lambda _paths, **_kwargs: _corpus_result(issues=[]),
    )

    result = deepdive_quality.audit_history(
        repo_root=tmp_path,
        start_date="2026-06-01",
        end_date="2026-08-31",
    )

    assert audited_dates == existing_dates
    assert [row["issueDate"] for row in result["days"]] == existing_dates
    assert "2026-06-11" not in audited_dates
    assert "2026-08-25" not in audited_dates
    assert result["articleCount"] == len(existing_dates)
    assert result["status"] == "Green"


def test_history_audit_cli_uses_history_enumerator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No.10 Adversarial: audit-history CLIを連続日audit-periodへ縮退させない。"""

    calls: list[dict[str, object]] = []

    def fake_audit_history(**kwargs):
        calls.append(kwargs)
        return {
            "schemaVersion": "DEEPDIVE_SHARED_QUALITY_HISTORY_REPORT_V1",
            "status": "Green",
            "startDate": kwargs["start_date"],
            "endDate": kwargs["end_date"],
            "route": kwargs["route"],
            "issueCodes": [],
            "articleCount": 0,
            "days": [],
            "dialogueCorpusAudit": _corpus_result(issues=[]),
        }

    monkeypatch.setattr(deepdive_quality, "audit_history", fake_audit_history)

    exit_code = deepdive_quality.main(
        [
            "--repo-root",
            str(tmp_path),
            "audit-history",
            "--start",
            "2026-06-01",
            "--end",
            "2026-08-31",
            "--route",
            "codex_daily_audit",
        ]
    )

    assert exit_code == 0
    assert calls == [
        {
            "repo_root": tmp_path,
            "start_date": "2026-06-01",
            "end_date": "2026-08-31",
            "require_rendered_public": False,
            "route": "codex_daily_audit",
        }
    ]
    assert json.loads(capsys.readouterr().out)["articleCount"] == 0


def test_history_audit_cli_atomically_writes_repo_local_red_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No.10 Recovery: 初回Red監査をrepo内manifestへ固定しstdoutだけで失わない。"""

    report = {
        "schemaVersion": "DEEPDIVE_SHARED_QUALITY_HISTORY_REPORT_V1",
        "status": "Red",
        "startDate": "2026-05-31",
        "endDate": "2026-08-31",
        "route": "codex_daily_audit",
        "issueCodes": ["deepdive_article_value_invalid"],
        "articleCount": 1,
        "days": [
            {
                "status": "Red",
                "issueDate": "2026-05-31",
                "issueCodes": ["deepdive_article_value_invalid"],
            }
        ],
        "dialogueCorpusAudit": _corpus_result(issues=[]),
    }
    monkeypatch.setattr(
        deepdive_quality,
        "audit_history",
        lambda **_kwargs: report,
    )
    output = Path("data/deepdive-history-remediation/2026-08-31-initial-audit.json")

    exit_code = deepdive_quality.main(
        [
            "--repo-root",
            str(tmp_path),
            "audit-history",
            "--start",
            "2026-05-31",
            "--end",
            "2026-08-31",
            "--output",
            str(output),
        ]
    )

    manifest = tmp_path / output
    assert exit_code == 1
    assert json.loads(manifest.read_text(encoding="utf-8")) == report
    assert not list(manifest.parent.glob(f".{manifest.name}.*.tmp"))


def test_period_capture_fetches_each_unique_url_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    shared = "https://example.com/shared"
    _article(deepdive_dir / "2026-07-31-DeepDive.md", url=shared)
    _article(deepdive_dir / "2026-08-01-DeepDive.md", url=shared)
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout: float) -> dict[str, object]:
        calls.append(url)
        return _fetch(url)

    monkeypatch.setattr(deepdive_quality, "_fetch_one", fake_fetch)
    result = deepdive_quality.capture_period_provenance(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
    )
    assert result["status"] == "Green"
    assert result["articleCount"] == 2
    assert result["uniqueUrlCount"] == 1
    assert calls == [shared]
    assert (tmp_path / "data" / "deepdive-provenance" / "2026-07-31.json").is_file()
    assert (tmp_path / "data" / "deepdive-provenance" / "2026-08-01.json").is_file()


def test_period_capture_collects_all_url_failures_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    bad = "https://example.com/bad"
    good = "https://example.com/good"
    _article(deepdive_dir / "2026-07-31-DeepDive.md", url=bad)
    _article(deepdive_dir / "2026-08-01-DeepDive.md", url=good)
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout: float) -> dict[str, object]:
        calls.append(url)
        if url == bad:
            raise deepdive_quality.DeepDiveQualityError("DEEPDIVE_URL_FETCH_FAILED bad")
        return _fetch(url)

    monkeypatch.setattr(deepdive_quality, "_fetch_one", fake_fetch)
    result = deepdive_quality.capture_period_provenance(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
    )
    assert result["status"] == "Red"
    assert result["failedUrlCount"] == 1
    assert result["failures"][0]["url"] == bad
    assert sorted(calls) == [bad, good]


def test_period_capture_reuses_observation_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deepdive_dir = tmp_path / "digest" / "DeepDive"
    deepdive_dir.mkdir(parents=True)
    first = "https://example.com/first"
    second = "https://example.com/second"
    _article(deepdive_dir / "2026-07-31-DeepDive.md", url=first)
    _article(deepdive_dir / "2026-08-01-DeepDive.md", url=second)
    cache = tmp_path / "build" / "observations.json"

    def first_pass(url: str, *, timeout: float) -> dict[str, object]:
        if url == second:
            raise deepdive_quality.DeepDiveQualityError("DEEPDIVE_URL_FETCH_FAILED second")
        return _fetch(url)

    monkeypatch.setattr(deepdive_quality, "_fetch_one", first_pass)
    red = deepdive_quality.capture_period_provenance(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
        observation_cache_path=cache,
    )
    assert red["status"] == "Red"

    calls: list[str] = []

    def second_pass(url: str, *, timeout: float) -> dict[str, object]:
        calls.append(url)
        assert url == second
        return _fetch(url)

    monkeypatch.setattr(deepdive_quality, "_fetch_one", second_pass)
    green = deepdive_quality.capture_period_provenance(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
        observation_cache_path=cache,
    )
    assert green["status"] == "Green"
    assert calls == [second]


def test_fetch_uses_browser_compatible_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/report"
    observed: dict[str, object] = {}

    def fake_urlopen(request, *, timeout: float, context):
        observed["headers"] = dict(request.header_items())
        return _FakeResponse(body=b"official report body", final_url=url)

    monkeypatch.setattr(deepdive_quality, "urlopen", fake_urlopen)
    deepdive_quality._fetch_one(url, timeout=1.0)
    user_agent = str(observed["headers"].get("User-agent", ""))
    assert "Mozilla/5.0" in user_agent
    assert "News-Grasp-Provenance" not in user_agent


def test_fetch_uses_bundled_ca_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/report"
    sentinel = object()
    observed: dict[str, object] = {}
    monkeypatch.setattr(deepdive_quality, "_build_tls_context", lambda: sentinel)

    def fake_urlopen(request, *, timeout: float, context):
        observed["context"] = context
        return _FakeResponse(body=b"official report body", final_url=url)

    monkeypatch.setattr(deepdive_quality, "urlopen", fake_urlopen)
    deepdive_quality._fetch_one(url, timeout=1.0)
    assert observed["context"] is sentinel


def test_fetch_rejects_soft_404_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/missing-report"
    monkeypatch.setattr(
        deepdive_quality,
        "urlopen",
        lambda request, *, timeout, context: _FakeResponse(
            body=b"<html><title>Page not found</title>The page you requested could not be found.</html>",
            final_url=url,
        ),
    )
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_URL_SOFT_404",
    ):
        deepdive_quality._fetch_one(url, timeout=1.0)


def test_fetch_rejects_generic_home_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/reports/2026/source"
    monkeypatch.setattr(
        deepdive_quality,
        "urlopen",
        lambda request, *, timeout, context: _FakeResponse(
            body=b"official site homepage",
            final_url="https://example.com/",
        ),
    )
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_URL_GENERIC_REDIRECT",
    ):
        deepdive_quality._fetch_one(url, timeout=1.0)


def test_fetch_uses_single_system_transport_fallback_for_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/protected-report"
    calls: list[str] = []
    monkeypatch.setattr(
        deepdive_quality,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPError(url, 403, "Forbidden", {}, None)
        ),
    )

    def fake_system_fetch(value: str, *, timeout: float, primary_failure: str):
        calls.append(value)
        assert primary_failure == "HTTP_403"
        return deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"official protected report",
        )

    monkeypatch.setattr(deepdive_quality, "_fetch_one_system", fake_system_fetch)
    result = deepdive_quality._fetch_one(url, timeout=1.0)
    assert result["httpStatus"] == 200
    assert calls == [url]


def test_public_fetch_boundary_rejects_loopback_before_transport() -> None:
    """security boundary: test serverを含むloopback/任意portへtransportを開かない。"""

    class Handler(BaseHTTPRequestHandler):
        requests: list[str] = []

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            user_agent = self.headers.get("User-Agent", "")
            self.__class__.requests.append(user_agent)
            if user_agent == deepdive_quality.USER_AGENT:
                self.send_response(403)
                self.end_headers()
                return
            body = b"official protected report confirms recovery transport evidence"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/bls-profile"
        with pytest.raises(ValueError, match="public_fetch_(port|address)_forbidden"):
            deepdive_quality._fetch_one(url, timeout=5.0)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert Handler.requests == []


def test_windows_system_transport_stops_stream_before_body_limit(
    tmp_path: Path,
) -> None:
    """RC-01 adversarial: chunked巨大本文を全量download後に判定しない。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            try:
                self.wfile.write(b"x" * 4096)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args: object) -> None:
            return

    descriptor, body_name = tempfile.mkstemp(
        prefix="news-grasp-provenance-", suffix=".body"
    )
    os.close(descriptor)
    body_path = Path(body_name)
    helper = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "ops"
        / "invoke-deepdive-system-fetch.ps1"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
                "-Url",
                f"http://127.0.0.1:{server.server_port}/oversized",
                "-BodyPath",
                str(body_path),
                "-MaxBytes",
                "1024",
                "-TimeoutSec",
                "5",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    try:
        assert completed.returncode != 0
        assert "DEEPDIVE_SYSTEM_FETCH_BODY_LIMIT_EXCEEDED" in (
            completed.stderr + completed.stdout
        )
        assert body_path.stat().st_size == 0
    finally:
        body_path.unlink(missing_ok=True)


def test_fallback_transport_evidence_is_bound_into_v2_provenance(
    tmp_path: Path,
) -> None:
    """RC-01 recovery: fallback種別・最終URL・status・本文hashを一枚へ封印する。"""

    url = "https://example.com/bls-profile"
    article = _v2_article(tmp_path / "2026-08-27-DeepDive.md", url=url)
    output = tmp_path / "2026-08-27.json"
    body = "official protected report confirms recovery transport evidence"
    record = {
        "url": url,
        "finalUrl": "https://example.com/final-report",
        "httpStatus": 200,
        "fetchedAt": "2026-08-27T06:50:00+09:00",
        "contentSha256": _sha256_text(body),
        "observedText": body,
        "transportEvidence": {
            "selectedTransport": "windows_system_http",
            "primaryTransport": "python_urllib",
            "primaryFailure": "HTTP_403",
            "fallbackAttemptCount": 1,
        },
    }

    manifest = deepdive_quality.build_provenance_manifest(
        article_path=article,
        fetch_records=[record],
        output_path=output,
    )

    assert manifest["schemaVersion"] == deepdive_quality.SCHEMA
    assert manifest["transportEvidenceVersion"] == 1
    assert manifest["sources"][0]["transportEvidence"] == record["transportEvidence"]
    assert deepdive_quality.validate_provenance(article, output) == []


@pytest.mark.parametrize("status", [404, 410])
def test_fetch_never_falls_back_after_404_or_410(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    url = "https://example.com/nonexistent"
    calls: list[str] = []
    monkeypatch.setattr(
        deepdive_quality,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPError(url, status, "Not Found", {}, None)
        ),
    )
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one_system",
        lambda value, *, timeout: calls.append(value),
    )
    with pytest.raises(deepdive_quality.DeepDiveQualityError):
        deepdive_quality._fetch_one(url, timeout=1.0)
    assert calls == []


def test_system_transport_fallback_cannot_bypass_soft_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/protected-missing"
    monkeypatch.setattr(
        deepdive_quality,
        "_run_system_transport",
        lambda *_args, **_kwargs: (
            200,
            url,
            b"<html><title>404 Page Not Found</title></html>",
        ),
    )
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_URL_SOFT_404",
    ):
        deepdive_quality._fetch_one_system(url, timeout=1.0)


def test_system_transport_failure_remains_typed_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://example.com/protected-error"
    monkeypatch.setattr(
        deepdive_quality,
        "_run_system_transport",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            deepdive_quality.DeepDiveQualityError(
                "DEEPDIVE_SYSTEM_FETCH_FAILED exit=22"
            )
        ),
    )
    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_SYSTEM_FETCH_FAILED",
    ):
        deepdive_quality._fetch_one_system(url, timeout=1.0)


def _patch_bilingual_materializer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: str = "official protected report confirms recovery transport evidence",
) -> None:
    """sidecar fixtureで使うnetwork/validator境界を注入する。"""

    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=evidence.encode("utf-8"),
        ),
    )

    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    _patch_green_quality_review(monkeypatch)


def _claim_transport_path(root: Path, issue_date: str = "2026-08-27") -> Path:
    return root / "data" / "deepdive-claim-source" / f"{issue_date}.json"


def test_materialize_issue_seals_bilingual_claim_transport_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 primary: 日本語claim/英語evidenceをfull-row sidecarへ封印する。"""

    issue_date = "2026-08-27"
    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)
    original_text = article.read_text(encoding="utf-8")
    expected_bindings = deepdive_quality._claim_source_declarations(original_text)
    _patch_bilingual_materializer(monkeypatch)

    result = deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date=issue_date,
    )

    sidecar = _claim_transport_path(tmp_path, issue_date)
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    assert result["status"] == "Green"
    assert sidecar.is_file()
    assert value["schemaVersion"] == "DEEPDIVE_CLAIM_SOURCE_TRANSPORT_V1"
    assert value["status"] == "Green"
    assert value["issueDate"] == issue_date
    assert value["articlePath"] == f"digest/DeepDive/{issue_date}-DeepDive.md"
    assert value["articleContentSha256"] == deepdive_quality._claim_free_article_sha256(
        original_text
    )
    assert value["bindings"] == expected_bindings
    unsigned = dict(value)
    transport_sha = unsigned.pop("transportSha256")
    assert transport_sha == deepdive_quality._canonical_sha256(unsigned)


def test_materialize_issue_replaces_valid_but_stale_transport_after_article_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 recovery: 記事rewrite後は現行declarationで旧transportを置換する。"""

    issue_date = "2026-08-27"
    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)
    _patch_bilingual_materializer(monkeypatch)
    deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date=issue_date,
    )

    stale_transport = json.loads(
        _claim_transport_path(tmp_path, issue_date).read_text(encoding="utf-8")
    )
    rewritten_claim = "現行の修正版claim"
    rewritten_evidence = (
        "official protected report confirms recovery transport evidence"
    )
    rewritten_declaration = json.dumps(
        {
            "claimId": "rewritten-claim",
            "claim": rewritten_claim,
            "sourceUrl": url,
            "evidence": rewritten_evidence,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    rewritten_text = deepdive_quality.CLAIM_SOURCE_RE.sub(
        f"<!-- claim-source: {rewritten_declaration} -->",
        article.read_text(encoding="utf-8"),
    ).replace(
        "限定system transportでも同じ本文検査を通過する",
        rewritten_claim,
    )
    article.write_text(rewritten_text, encoding="utf-8")
    _write_llm_dialogue(article)

    result = deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date=issue_date,
    )

    current_transport = json.loads(
        _claim_transport_path(tmp_path, issue_date).read_text(encoding="utf-8")
    )
    assert result["status"] == "Green"
    assert current_transport["bindings"] == (
        deepdive_quality._claim_source_declarations(
            article.read_text(encoding="utf-8")
        )
    )
    assert current_transport["bindings"] != stale_transport["bindings"]


def test_materialize_issue_preserves_article_when_dialogue_rewrite_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 recovery: 後段Redではsidecar由来claimも部分反映しない。"""

    issue_date = "2026-08-27"
    url = "https://example.com/source"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url=url)
    _write_llm_dialogue(article)
    _patch_bilingual_materializer(monkeypatch)
    first = deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date=issue_date,
    )
    sidecar = _claim_transport_path(tmp_path, issue_date)
    sidecar_before = sidecar.read_bytes()
    article.write_text(
        deepdive_quality.CLAIM_SOURCE_RE.sub(
            "", article.read_text(encoding="utf-8")
        ),
        encoding="utf-8",
    )
    article_before_retry = article.read_bytes()
    for output in (
        tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json",
        tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md",
        tmp_path / "data" / "deepdive-bundles" / f"{issue_date}.json",
    ):
        output.unlink()

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_LLM_REWRITE_REQUIRED dialogue_staged_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date=issue_date,
        )

    assert first["status"] == "Green"
    assert article.read_bytes() == article_before_retry
    assert deepdive_quality._claim_source_declarations(
        article.read_text(encoding="utf-8")
    ) == []
    assert sidecar.read_bytes() == sidecar_before
    assert not (tmp_path / "data" / "deepdive-provenance" / f"{issue_date}.json").exists()
    assert not (tmp_path / "data" / "deepdive-bundles" / f"{issue_date}.json").exists()
    assert not (
        tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive-dialogue.md"
    ).exists()


@pytest.mark.parametrize("tamper", ("transport_hash", "article_content_hash", "evidence"))
def test_materialize_issue_rejects_tampered_claim_transport_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    """RC-02 adversarial: sidecar seal/content/evidence改変をtyped Redにする。"""

    issue_date = "2026-08-27"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _v2_article(article, url="https://example.com/source")
    _write_llm_dialogue(article)
    _patch_bilingual_materializer(monkeypatch)
    deepdive_quality.materialize_issue_bundle(
        repo_root=tmp_path,
        issue_date=issue_date,
    )
    sidecar = _claim_transport_path(tmp_path, issue_date)
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    if tamper == "transport_hash":
        value["transportSha256"] = "0" * 64
    elif tamper == "article_content_hash":
        value["articleContentSha256"] = "1" * 64
    else:
        value["bindings"][0]["evidence"] = "tampered evidence text"
    sidecar.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_CLAIM_SOURCE_TRANSPORT_INVALID",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date=issue_date,
        )


def test_materialize_issue_without_claim_transport_rejects_unrelated_url_200(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RC-02 boundary: sidecarなしの無関係URL 200は従来どおりtyped Red。"""

    issue_date = "2026-08-27"
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    article.parent.mkdir(parents=True)
    _article(article, url="https://example.com/unrelated")
    monkeypatch.setattr(
        deepdive_quality,
        "_fetch_one",
        lambda value, **_kwargs: deepdive_quality._observed_record(
            url=value,
            final_url=value,
            status=200,
            body=b"unrelated page body without the article claim",
        ),
    )
    sidecar = _claim_transport_path(tmp_path, issue_date)
    assert not sidecar.exists()

    with pytest.raises(
        deepdive_quality.DeepDiveQualityError,
        match="DEEPDIVE_RESEARCH_EVIDENCE_INSUFFICIENT bindings_missing",
    ):
        deepdive_quality.materialize_issue_bundle(
            repo_root=tmp_path,
            issue_date=issue_date,
        )
    assert not sidecar.exists()
