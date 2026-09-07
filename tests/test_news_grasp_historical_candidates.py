"""過去号の候補検索が実行日へずれないことを確認する。"""
from urllib.parse import parse_qs, urlsplit
import pytest

from tools import harvest_candidates as harvest
from tools import news_grasp_daily_content as content


def test_invalid_target_date_does_not_fall_back_to_today():
    with pytest.raises(ValueError):
        harvest.build_query("AI", issue_date="2026-99-06")


def test_old_completion_cannot_skip_current_validation(tmp_path, monkeypatch):
    import json
    from tests.test_news_grasp_daily_content import _candidate_provider, _model_runner, ISSUE_DATE, RUN_ID
    (tmp_path / "data").mkdir()
    (tmp_path / "data/articles.jsonl").write_text("", encoding="utf-8")
    args = dict(repo_root=tmp_path, issue_date=ISSUE_DATE, run_id=RUN_ID,
                scheduled_categories=("fx", "ai"), candidate_provider=_candidate_provider,
                model_runner=_model_runner,
                derived_builder=lambda **_: {"ok": True, "status": "built", "artifacts": []})
    content.produce_current_issue(**args)
    path = tmp_path / "build/daily-content" / RUN_ID / "completion.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved.pop("candidateTargetDateVersion", None)
    path.write_text(json.dumps(saved), encoding="utf-8")
    observed = []
    original = content._validate_reporter_cards
    def validate(*args, **kwargs):
        observed.append(kwargs["category"])
        return original(*args, **kwargs)
    monkeypatch.setattr(content, "_validate_reporter_cards", validate)
    args["model_runner"] = lambda **_: pytest.fail("正常保存記事の再生成")
    result = content.produce_current_issue(**args)
    assert observed == ["fx", "ai"]
    assert result["candidateTargetDateVersion"] == 1
    assert result["model_call_count"] == 0


def test_historical_query_replaces_relative_window():
    query = harvest.build_query("AI when:1d", issue_date="2026-09-06")
    assert "when:" not in query
    assert "after:2026-09-05" in query
    assert "before:2026-09-07" in query
    url = harvest.build_feed_url("AI", issue_date="2026-09-06")
    assert parse_qs(urlsplit(url).query)["q"] == [query]


def test_target_day_candidates_precede_newer_rows_before_limit(monkeypatch):
    monkeypatch.setattr(harvest, "category_queries", lambda category: ["AI"])
    monkeypatch.setattr(harvest, "_source_definitions_for_category", lambda category: [])
    monkeypatch.setattr(harvest, "fetch_feed", lambda *args, **kwargs: "xml")
    rows = [
        {"url": "https://example.org/new", "pubDate": "2026-09-06T23:00:00+00:00"},
        {"url": "https://example.org/target", "pubDate": "2026-09-05T23:00:00+00:00"},
    ]
    monkeypatch.setattr(harvest, "parse_rss", lambda *args: rows)
    monkeypatch.setattr(harvest, "_filter_rows", lambda rows, *args: rows)
    selected, audit = harvest.harvest_category_with_audit("ai", max_per_category=1, issue_date="2026-09-06")
    assert selected[0]["url"] == "https://example.org/target"
    assert audit["target_issue_date"] == "2026-09-06"


def test_daily_candidate_provider_passes_target_date(monkeypatch):
    observed = {}
    def fetch(category, **kwargs):
        observed.update(kwargs)
        return [{"url": "https://example.org/a"}], {}
    monkeypatch.setattr(harvest, "harvest_category_with_audit", fetch)
    from tools import prepare_reporter_candidates
    monkeypatch.setattr(prepare_reporter_candidates, "prepare_rows", lambda rows, **kwargs: (rows, []))
    _, audit = content._default_candidate_provider("ai", "2026-09-06")
    assert observed["issue_date"] == "2026-09-06"
    assert audit["target_issue_date"] == "2026-09-06"
