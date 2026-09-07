"""日次朗読を記事の常体から分離し、検証した台本だけを保存する。"""
from pathlib import Path
import runpy

import pytest

from tools import news_grasp_deterministic_builders as builders
from tools.publish_inventory import scheduled_category_ids
from tools.tts.build_script import validate_script


DAY = "2026-08-14"
HELPERS = runpy.run_path(str(Path(__file__).with_name("test_2026_08_14_recovery_replay.py")))
TTS_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_tts_build_script.py")))


def _source():
    raw = (Path(__file__).parent / "fixtures/tts/good-audio-script.md").read_text(encoding="utf-8")
    body = raw.split("\n", 1)[1].strip()
    return (
        TTS_HELPERS["_outline"](tuple(scheduled_category_ids(DAY)))
        + "今日は8月14日です。朝のニュースをお伝えします。\n"
        + body
        + "\n今日の観点・考察です。次の観測点は価格と供給条件の変化です。"
    )


def _repo(tmp_path):
    return HELPERS["_materialization_repo"](tmp_path, summary=HELPERS["_rich_summary"](DAY))


def test_editor_narration_materializes_and_reuses_without_changing_summary(tmp_path):
    root = _repo(tmp_path)
    source = root / "digest/Summary" / f"{DAY}.md"
    before = source.read_bytes()
    narration = _source()
    assert validate_script(narration, date=DAY, required_categories=scheduled_category_ids(DAY)) == []
    first = builders.materialize_editor_audio_script(repo_root=root, issue_date=DAY, audio_script_markdown=narration)
    output = root / first["artifactPath"]
    actual = builders._strip_frontmatter(output.read_text(encoding="utf-8"))
    assert actual.strip() == narration.strip()
    assert source.read_bytes() == before
    second = builders.materialize_editor_audio_script(repo_root=root, issue_date=DAY, audio_script_markdown=narration)
    assert second["status"] == "reused"
    assert second["outputHash"] == first["outputHash"]


def test_invalid_narration_preserves_existing_script_and_summary(tmp_path):
    root = _repo(tmp_path)
    target = root / "digest/Summary" / f"{DAY}-audio-script.md"
    target.write_text("保存済みの台本", encoding="utf-8")
    before = {p: p.read_bytes() for p in (target, root / "digest/Summary" / f"{DAY}.md")}
    with pytest.raises(builders.NewsGraspBuilderError, match="QUALITY_INVALID"):
        builders.materialize_editor_audio_script(repo_root=root, issue_date=DAY, audio_script_markdown=_source()+"確認が必要だ。")
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_corrected_narration_replaces_only_invalid_daily_script(tmp_path):
    root = _repo(tmp_path)
    target = root / "digest/Summary" / f"{DAY}-audio-script.md"
    target.write_text("古い朗読だ。", encoding="utf-8")
    deepdive = root / "digest/Summary" / "unrelated-deepdive.md"
    deepdive.write_text("先輩: ここが重要だ。", encoding="utf-8")
    before = deepdive.read_bytes()
    result = builders.materialize_editor_audio_script(repo_root=root, issue_date=DAY, audio_script_markdown=_source())
    assert result["status"] == "materialized"
    assert "古い朗読だ" not in target.read_text(encoding="utf-8")
    assert deepdive.read_bytes() == before


def test_audio_only_repair_keeps_editor_and_deepdive_reusable():
    from tools.news_grasp_repair_registry import build_daily_artifact_dag, build_repair_plan
    dag = build_daily_artifact_dag(("ai",))
    assert "daily_audio_script_source" in dag
    checkpoints = {key: {"status": "Green"} for key in dag if key != "daily_audio_script_source"}
    plan = build_repair_plan(issue_date=DAY, run_id="saved-run", categories=("ai",), checkpoints=checkpoints, failures=[])
    actions = {step["artifactId"]: step["action"] for step in plan["steps"]}
    assert actions["editor"] == actions["summary"] == actions["deepdive_model"] == "reuse"
    assert actions["daily_audio_script_source"] == "repair_model"
    assert actions["daily_audio"] == "rebuild_deterministic"
    assert plan["modelCallsRequired"] == 1


def test_editor_and_narration_share_one_initial_call():
    from tools.news_grasp_repair_registry import build_repair_plan
    plan = build_repair_plan(issue_date=DAY, run_id="new-run", categories=("ai",), checkpoints={}, failures=[])
    # Reporter 1、Editorと台本 1、DeepDive 1、独立review 1。台本単独callは増えない。
    assert plan["modelCallsRequired"] == 4


def test_editor_validates_new_audio_without_invalidating_legacy_content(tmp_path, monkeypatch):
    from tools import news_grasp_daily_content as content
    from tools import validate_editor_output_preview as preview
    monkeypatch.setattr(preview, "validate_editor_output_preview", lambda *a, **k: [])
    record = {"url": "https://example.org/news/1"}
    old = {"issue_date": DAY, "append_records": [record], "summary_markdown": "保存済み記事だ。"}
    args = dict(issue_date=DAY, reporters=[{"records": [record]}], preview_dir=tmp_path)
    assert content._validate_editor(old, **args) == old
    with pytest.raises(content.DailyContentError, match="audio_script_markdown"):
        content._validate_editor(old, require_audio_script=True, **args)
    new = {**old, "audio_script_markdown": _source()}
    assert content._validate_editor(new, require_audio_script=True, **args)["audio_script_markdown"] == _source()
    with pytest.raises(content.DailyContentError, match="日次朗読口調違反"):
        content._validate_editor({**new, "audio_script_markdown": _source()+"必要だ。"}, require_audio_script=True, **args)


def test_daily_builder_uses_narration_source_not_summary_text(tmp_path, monkeypatch):
    from tools import news_grasp_daily_content as content

    class Observed(BaseException):
        pass

    def capture(**kwargs):
        assert kwargs["audio_script_markdown"] == _source()
        assert "article_records" not in kwargs
        raise Observed()

    monkeypatch.setattr(builders, "materialize_editor_audio_script", capture)
    checkpoint = content._write_artifact_checkpoint(
        tmp_path, run_id="saved-run", issue_date=DAY,
        artifact_id="daily_audio_script_source", input_hash="e" * 64,
        payload={"issue_date": DAY, "audio_script_markdown": _source()},
    )
    with pytest.raises(Observed):
        content._default_derived_builder(repo_root=tmp_path, issue_date=DAY, run_id="saved-run",
            narration_input_hash="e" * 64,
            artifact_checkpoints={"daily_audio_script_source": checkpoint})


@pytest.mark.parametrize("field", ["inputHash", "validatorId", "outputHash", "payload"])
def test_daily_builder_rejects_replaced_source_before_materialization(tmp_path, monkeypatch, field):
    from tools import news_grasp_daily_content as content
    checkpoint = content._write_artifact_checkpoint(
        tmp_path, run_id="saved-run", issue_date=DAY,
        artifact_id="daily_audio_script_source", input_hash="e" * 64,
        payload={"issue_date": DAY, "audio_script_markdown": _source()},
    )
    if field == "payload":
        checkpoint["payload"]["audio_script_markdown"] += "次の情報を確認します。"
    else:
        checkpoint[field] = "差し替え"
    monkeypatch.setattr(builders, "materialize_editor_audio_script", lambda **_: pytest.fail("未検証sourceを保存しました"))
    with pytest.raises(content.DailyContentError, match="SOURCE_UNVERIFIED"):
        content._default_derived_builder(
            repo_root=tmp_path, issue_date=DAY, run_id="saved-run", narration_input_hash="e" * 64,
            artifact_checkpoints={"daily_audio_script_source": checkpoint},
        )


def test_saved_narration_repair_calls_only_narrator_once(tmp_path):
    from tools import news_grasp_daily_content as content
    root = _repo(tmp_path)
    calls, reservations = [], []

    def reserve(**request):
        reservations.append(request)
        return {"idempotent": len(reservations) > 1, "status": "reserved"}

    def model(**request):
        calls.append(request["role"])
        return {"issue_date": DAY, "audio_script_markdown": _source()}

    editor = {"summary_markdown": "保存済みSummaryだ。", "append_records": []}
    args = dict(root=root, run_id="saved-run", issue_date=DAY, editor=editor,
                consume_model_call=reserve, model_fn=model)
    first, sent = content._ensure_daily_narration_source(**args)
    second, resent = content._ensure_daily_narration_source(**args)
    assert sent and not resent
    assert calls == ["daily_narration"]
    assert len(reservations) == 1 and reservations[0]["budget_class"] == "repair"
    assert first["payload"] == second["payload"]
    assert editor == {"summary_markdown": "保存済みSummaryだ。", "append_records": []}


def test_unknown_narration_result_is_not_resent(tmp_path):
    from tools import news_grasp_daily_content as content
    root = _repo(tmp_path)
    calls, reservations = [], []

    def reserve(**request):
        reservations.append(request)
        return {"idempotent": len(reservations) > 1, "status": "reserved"}

    def model(**request):
        calls.append(request["role"])
        raise content.ModelResultPending("provider_result_unknown")

    args = dict(root=root, run_id="saved-run", issue_date=DAY,
                editor={"summary_markdown": "保存本文", "append_records": []},
                consume_model_call=reserve, model_fn=model)
    for _ in range(2):
        with pytest.raises(content.ModelResultPending):
            content._ensure_daily_narration_source(**args)
    assert calls == ["daily_narration"]


def test_same_day_script_without_source_binding_is_repaired_without_overwrite(tmp_path):
    from tools import news_grasp_daily_content as content
    root = _repo(tmp_path)
    target = root / "digest/Summary" / f"{DAY}-audio-script.md"
    target.write_text(_source(), encoding="utf-8")
    original = target.read_bytes()
    calls = []

    def model(**request):
        calls.append(request)
        return {"issue_date": DAY, "audio_script_markdown": _source()}

    checkpoint, sent = content._ensure_daily_narration_source(
        root=root, run_id="saved-run", issue_date=DAY,
        editor={"summary_markdown": "保存した別の記事です。", "append_records": []},
        consume_model_call=lambda **_: {"idempotent": False, "status": "reserved"},
        model_fn=model,
    )
    assert sent and len(calls) == 1
    assert checkpoint["status"] == "Green"
    assert target.read_bytes() == original


def test_legacy_editor_repair_keeps_scoped_content_and_accepts_new_narration():
    from tools import news_grasp_daily_content as content
    previous = {"issue_date": DAY, "append_records": [{"url": "保存URL"}], "summary_markdown": "修正対象です。"}
    failure = content._failure_checkpoint_value(
        run_id="saved-run", issue_date=DAY, stage="editor", artifact_id="editor",
        predicate_id="editor_output_valid", reason_code="editor summary invalid",
        input_hash="e" * 64, invalid_payload=previous,
    )
    original_paths = list(failure["allowedMutationPaths"])
    result = content._project_editor_repair_result(failure, {
        **previous, "summary_markdown": "修正したSummaryです。",
        "append_records": [{"url": "許可外URL"}], "audio_script_markdown": _source(),
    })
    assert result["append_records"] == previous["append_records"]
    assert result["summary_markdown"] == "修正したSummaryです。"
    assert result["audio_script_markdown"] == _source()
    assert failure["allowedMutationPaths"] == original_paths
    assert "audio_script_markdown" not in previous


def test_narration_quality_failure_allows_scoped_correction_with_preserved_raw(tmp_path):
    from tools import news_grasp_daily_content as content
    root = _repo(tmp_path)
    calls, reservations = [], []

    def reserve(**request):
        reservations.append(request)
        return {"idempotent": False, "status": "reserved"}

    def model(**request):
        calls.append(request)
        return {"issue_date": DAY, "audio_script_markdown": _source() + ("必要だ。" if len(calls) == 1 else "")}

    args = dict(root=root, run_id="saved-run", issue_date=DAY,
                editor={"summary_markdown": "保存記事です。", "append_records": []},
                consume_model_call=reserve, model_fn=model)
    with pytest.raises(content.DailyContentError, match="日次朗読口調違反"):
        content._ensure_daily_narration_source(**args)
    raw_files = list((root / "build").rglob("raw.json"))
    original = {path: path.read_bytes() for path in raw_files}
    assert original
    checkpoint, sent = content._ensure_daily_narration_source(**args)
    assert sent and checkpoint["status"] == "Green" and len(calls) == 2
    assert reservations[0]["call_id"] != reservations[1]["call_id"]
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert calls[1]["repair_feedback"]["failure"]["allowedMutationPaths"] == ["/audio_script_markdown"]


def test_narration_correction_uses_existing_repair_budget_limit(tmp_path):
    from tools import news_grasp_daily_content as content
    from tools.news_grasp_repair_registry import ModelCallBudgetLedger, NewsGraspRepairPlanError
    root = _repo(tmp_path)
    budget = ModelCallBudgetLedger(root / "budget.json", issue_date=DAY, run_id="saved-run")
    calls = []

    def model(**request):
        calls.append(request)
        return {"issue_date": DAY, "audio_script_markdown": _source() + "必要だ。"}

    args = dict(root=root, run_id="saved-run", issue_date=DAY,
                editor={"summary_markdown": "保存記事です。", "append_records": []},
                consume_model_call=budget.consume, model_fn=model)
    for _ in range(4):
        with pytest.raises(content.DailyContentError, match="日次朗読口調違反"):
            content._ensure_daily_narration_source(**args)
    with pytest.raises(NewsGraspRepairPlanError):
        content._ensure_daily_narration_source(**args)
    assert len(calls) == 4
