"""モデル提案の範囲外変更を適用せず、元の修正範囲を保持する。"""
import copy
import json
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize("path,before,proposal,expected", [
    ("/summary_markdown", {"summary_markdown": "old", "records": [1]}, {"summary_markdown": "new", "records": [2]}, {"summary_markdown": "new", "records": [1]}),
    ("/records/0/thumb", {"records": [{"thumb": "old", "title": "keep"}]}, {"records": [{"thumb": "new", "title": "changed"}]}, {"records": [{"thumb": "new", "title": "keep"}]}),
    ("/dialogue_markdown", {"article_markdown": "keep", "dialogue_markdown": "old"}, {"article_markdown": "changed", "dialogue_markdown": "new"}, {"article_markdown": "keep", "dialogue_markdown": "new"}),
])
def test_only_authorized_model_fields_are_applied(path, before, proposal, expected):
    from tools.news_grasp_daily_content import _project_repair_result
    preserved = copy.deepcopy(before)
    failure = {"invalidPayload": before, "allowedMutationPaths": [path]}
    assert _project_repair_result(failure, proposal) == expected
    assert before == preserved


def test_unknown_scope_is_operational_pending_not_new_quality_failure():
    from tools.news_grasp_daily_content import ModelResultPending, _project_repair_result
    with pytest.raises(ModelResultPending):
        _project_repair_result({"invalidPayload": {"summary_markdown": "old"}}, {"summary_markdown": "new"})


@pytest.mark.parametrize("headline", [
    "OpenAI、休眠サイトへのAIエージェント書き込みを認める",
    "日銀、9月利上げを検討　政策金利1.25％が軸",
    "OpenAI、AIエージェントの検証結果を公表",
])
def test_concrete_news_actions_are_recognized(headline):
    from tools.summary_headline import summary_headline_quality_errors
    assert summary_headline_quality_errors(headline) == []


def test_abstract_headline_remains_rejected():
    from tools.summary_headline import summary_headline_quality_errors
    assert summary_headline_quality_errors("複数分野の変化を読み、これからの対応を考える")


@pytest.mark.parametrize("mutation", ["none", "raw", "intent", "terminal"])
def test_saved_model_output_rebinds_intent_and_terminal(tmp_path, mutation):
    from tools.news_grasp_daily_content import ModelResultPending
    from tools.news_grasp_saved_scope_recovery import read_completed_output
    intent = {"callId": "a" * 64, "inputHash": "b" * 64}
    value = {"summary_markdown": "saved"}
    (tmp_path / "intent.json").write_text(json.dumps(intent), encoding="utf-8")
    (tmp_path / "raw.json").write_text(json.dumps(value), encoding="utf-8")
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(value)}}, {"type": "turn.completed"}]
    if mutation == "raw":
        (tmp_path / "raw.json").write_text('{"summary_markdown":"changed"}', encoding="utf-8")
    if mutation == "intent":
        (tmp_path / "intent.json").write_text('{"callId":"other"}', encoding="utf-8")
    if mutation == "terminal":
        events[-1] = {"type": "turn.failed"}
    (tmp_path / "editor.events.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n", encoding="utf-8")
    if mutation == "none":
        assert read_completed_output(tmp_path, intent)[0] == value
    else:
        with pytest.raises(ModelResultPending):
            read_completed_output(tmp_path, intent)


def test_reporter_scope_pending_does_not_overwrite_quality_cause(tmp_path, monkeypatch):
    from tools import news_grasp_daily_content as content
    fixture = runpy.run_path(str(Path(__file__).with_name("test_news_grasp_daily_content.py")))
    (tmp_path / "data").mkdir()
    (tmp_path / "data/articles.jsonl").write_bytes(b"")
    def pending(*args):
        raise content.ModelResultPending("repair_scope_unresolved")
    monkeypatch.setattr(content, "_project_repair_result", pending)
    with pytest.raises(content.ModelResultPending):
        content.produce_current_issue(
            repo_root=tmp_path, issue_date=fixture["ISSUE_DATE"], run_id=fixture["RUN_ID"],
            scheduled_categories=("fx",), candidate_provider=fixture["_candidate_provider"],
            model_runner=fixture["_model_runner"], derived_builder=lambda **kwargs: {"ok": True},
        )
    assert not content._failure_cache_path(tmp_path, fixture["RUN_ID"], "reporter:fx").exists()
