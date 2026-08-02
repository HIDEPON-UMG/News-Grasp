from __future__ import annotations

import hashlib
import json
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
    observed_paths: list[Path] = []

    def red_corpus(paths: list[Path]) -> dict[str, object]:
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
        lambda _paths: _corpus_result(issues=["日跨ぎ完全反復率超過"]),
    )
    result = deepdive_quality.audit_period(
        repo_root=tmp_path,
        start_date="2026-07-31",
        end_date="2026-08-01",
    )
    assert result["status"] == "Red"
    assert result["issueCodes"] == ["deepdive_dialogue_value_invalid"]
    assert result["dialogueCorpusAudit"]["issues"]


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
        lambda _paths: _corpus_result(issues=[]),
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

    def fake_system_fetch(value: str, *, timeout: float):
        calls.append(value)
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
        "_run_system_curl",
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
        "_run_system_curl",
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
