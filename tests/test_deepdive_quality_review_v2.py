from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools import deepdive_quality


AXES = (
    "theme_specific_insight",
    "evidence_depth",
    "causal_coherence",
    "counterevidence",
    "decision_utility",
    "dialogue_naturalness",
    "relation_map_utility",
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _prepare_issue(tmp_path: Path, issue_date: str) -> tuple[Path, Path, dict[str, object]]:
    relation = {
        "title": "規制変更後の当事者関係",
        "nodes": [
            {"id": "regulator", "label": "規制当局", "group": "監督"},
            {"id": "vendor", "label": "供給企業", "group": "供給"},
            {"id": "buyer", "label": "導入企業", "group": "需要"},
        ],
        "edges": [
            {
                "from": "regulator",
                "to": "vendor",
                "label": "2026年9月から報告を義務化",
                "kind": "規制",
            },
            {
                "from": "vendor",
                "to": "buyer",
                "label": "監査済み部品を供給",
                "kind": "供給",
            },
        ],
        "source": "一次資料 https://example.com/source",
    }
    article = tmp_path / "digest" / "DeepDive" / f"{issue_date}-DeepDive.md"
    dialogue = article.with_name(f"{issue_date}-DeepDive-dialogue.md")
    article.parent.mkdir(parents=True)
    article.write_text(
        "---\ntitle: V2意味レビュー検証\n"
        f"date: {issue_date}\n---\n\n"
        "## 背景\n\n規制変更が供給条件を変えた。\n\n"
        "```relations\n"
        + json.dumps(relation, ensure_ascii=False, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )
    dialogue.write_text(
        "---\nschema: DEEPDIVE_DIALOGUE_V2\n---\n\n"
        "## 台本\n\n若手: 何が変わったのでしょうか。\n\n"
        "先輩: 報告義務が供給条件を変えたんだ。\n",
        encoding="utf-8",
    )
    return article, dialogue, relation


def _review_payload(
    *,
    tmp_path: Path,
    issue_date: str,
    scores: dict[str, int] | None = None,
    route: str = "production_generation",
) -> dict[str, object]:
    article, dialogue, relation = _prepare_issue(tmp_path, issue_date)
    actual_scores = scores or {axis: 4 for axis in AXES}
    average = sum(actual_scores.values()) / len(AXES)
    return {
        "schemaVersion": "DEEPDIVE_QUALITY_REVIEW_V2",
        "issueDate": issue_date,
        "artifacts": {
            "article": {
                "path": article.relative_to(tmp_path).as_posix(),
                "sha256": _sha256(article.read_bytes()),
            },
            "relation": {
                "path": article.relative_to(tmp_path).as_posix(),
                "sha256": _canonical_sha256(relation),
            },
            "dialogue": {
                "path": dialogue.relative_to(tmp_path).as_posix(),
                "sha256": _sha256(dialogue.read_bytes()),
            },
        },
        "scores": actual_scores,
        "findings": {
            axis: f"{axis}を記事固有の根拠と判断差分で確認した"
            for axis in AXES
        },
        "averageScore": average,
        "reviewRoute": route,
        "status": "Green"
        if min(actual_scores.values()) >= 3 and average >= 4
        else "Red",
    }


def _write_review(tmp_path: Path, issue_date: str, value: dict[str, object]) -> Path:
    path = tmp_path / "data" / "deepdive-quality-review" / f"{issue_date}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _audit_without_unrelated_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    issue_date: str,
) -> dict[str, object]:
    monkeypatch.setattr(
        deepdive_quality,
        "_validate_provenance_with_evidence",
        lambda *_args: ([], []),
    )
    monkeypatch.setattr(
        deepdive_quality,
        "validate_claim_source_fit",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deepdive_quality,
        "_claim_source_declarations",
        lambda *_args: [{"claimId": "fixture-binding"}],
    )
    monkeypatch.setattr(
        deepdive_quality.deepdive_dialogue,
        "validate_dialogue_document",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deepdive_quality,
        "_dialogue_source_lineage_issues",
        lambda *_args: [],
    )
    return deepdive_quality.audit_issue(
        repo_root=tmp_path,
        issue_date=issue_date,
        include_corpus=False,
    )


def test_audit_rejects_missing_v2_quality_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-31"
    _prepare_issue(tmp_path, issue_date)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Red"
    assert {
        "deepdive_article_value_invalid",
        "deepdive_relation_quality_invalid",
        "deepdive_dialogue_value_invalid",
    } <= set(result["issueCodes"])
    assert "DEEPDIVE_QUALITY_REVIEW_MISSING" in result["issues"]


def test_audit_accepts_bound_v2_quality_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-31"
    review = _review_payload(tmp_path=tmp_path, issue_date=issue_date)
    review_path = _write_review(tmp_path, issue_date, review)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Green"
    assert result["qualityReviewPath"] == str(review_path)
    assert result["qualityReview"]["computedAverageScore"] == 4.0
    assert result["qualityReview"]["status"] == "Green"


def test_audit_rejects_stale_relation_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-31"
    review = _review_payload(tmp_path=tmp_path, issue_date=issue_date)
    review["artifacts"]["relation"]["sha256"] = "0" * 64
    _write_review(tmp_path, issue_date, review)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Red"
    assert "deepdive_relation_quality_invalid" in result["issueCodes"]
    assert any(
        issue == "DEEPDIVE_QUALITY_REVIEW_ARTIFACT_STALE relation"
        for issue in result["issues"]
    )


@pytest.mark.parametrize(
    ("axis", "issue_code"),
    [
        ("theme_specific_insight", "deepdive_article_value_invalid"),
        ("dialogue_naturalness", "deepdive_dialogue_value_invalid"),
        ("relation_map_utility", "deepdive_relation_quality_invalid"),
    ],
)
def test_audit_maps_low_semantic_axis_to_owned_issue_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    axis: str,
    issue_code: str,
) -> None:
    issue_date = "2026-08-31"
    scores = {name: 5 for name in AXES}
    scores[axis] = 2
    review = _review_payload(
        tmp_path=tmp_path,
        issue_date=issue_date,
        scores=scores,
    )
    _write_review(tmp_path, issue_date, review)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Red"
    assert issue_code in result["issueCodes"]
    assert any(
        issue == f"DEEPDIVE_QUALITY_REVIEW_SCORE_TOO_LOW {axis}=2"
        for issue in result["issues"]
    )


def test_audit_recomputes_average_and_status_instead_of_trusting_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-31"
    scores = {axis: 3 for axis in AXES}
    review = _review_payload(
        tmp_path=tmp_path,
        issue_date=issue_date,
        scores=scores,
    )
    review["averageScore"] = 5.0
    review["status"] = "Green"
    _write_review(tmp_path, issue_date, review)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Red"
    assert "deepdive_article_value_invalid" in result["issueCodes"]
    assert "DEEPDIVE_QUALITY_REVIEW_AVERAGE_TOO_LOW 3.0" in result["issues"]
    assert "DEEPDIVE_QUALITY_REVIEW_SUMMARY_MISMATCH" in result["issues"]


def test_audit_rejects_unknown_review_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue_date = "2026-08-31"
    review = _review_payload(
        tmp_path=tmp_path,
        issue_date=issue_date,
        route="unregistered_route",
    )
    _write_review(tmp_path, issue_date, review)

    result = _audit_without_unrelated_failures(monkeypatch, tmp_path, issue_date)

    assert result["status"] == "Red"
    assert "DEEPDIVE_QUALITY_REVIEW_ROUTE_UNKNOWN unregistered_route" in result["issues"]
