"""保存記事で音声台本の不足だけを補う境界を検証する。"""
import hashlib
import runpy
from pathlib import Path

import pytest

from tools import news_grasp_deterministic_builders as builders
from tools.publish_inventory import scheduled_category_ids
from tools.tts.build_script import effective_char_count, validate_script

HELPERS = runpy.run_path(str(Path(__file__).with_name("test_2026_08_14_recovery_replay.py")))
DAY = "2026-08-14"


def _repo(tmp_path, *, rich=False):
    summary = HELPERS["_rich_summary"](DAY)
    if not rich:
        summary["sections"] = summary["sections"][:3]
    return HELPERS["_materialization_repo"](tmp_path, summary=summary)


def _records():
    return [{"date": DAY, "url": f"https://example.org/news/{i}",
             "title_ja": f"供給契約の第{i}報", "summary": (
                 f"事業者{i}は設備の稼働計画を公表した。契約には供給量と検収時期が明記されている。"
                 f"対象拠点は第{i}地区にあり、運用責任と保守費用を契約当事者が分担する。"
                 f"次の確認点は第{i}期の受注結果と稼働実績であり、需要増加時の追加費用は未確定である。"
             )} for i in range(1, 15)]


def test_saved_articles_complete_script_without_changing_sources(tmp_path):
    root = _repo(tmp_path)
    source = root / "digest" / "Summary" / f"{DAY}.md"
    original = source.read_bytes()
    first = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=_records())
    output = root / first["artifactPath"]
    body = builders._strip_frontmatter(output.read_text(encoding="utf-8"))
    assert 2500 <= effective_char_count(body) <= 3000
    assert validate_script(body, date=DAY, history_texts=[], required_categories=scheduled_category_ids(DAY)) == []
    assert source.read_bytes() == original
    assert first["sourceHash"] != hashlib.sha256(original).hexdigest()
    assert first["supplementalSources"]
    assert all(len(item["recordHash"]) == 64 for item in first["supplementalSources"])
    second = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=_records())
    assert second["status"] == "reused"
    assert second["outputHash"] == first["outputHash"]
    assert second["supplementalSources"] == first["supplementalSources"]


@pytest.mark.parametrize("kind", ["wrong_day", "duplicate", "missing_title", "missing_summary", "missing_url", "insufficient"])
def test_invalid_or_insufficient_supplement_writes_nothing(tmp_path, kind):
    root = _repo(tmp_path)
    records = _records()
    if kind == "wrong_day":
        records[0]["date"] = "2026-08-13"
    elif kind == "duplicate":
        records.append(dict(records[0]))
    elif kind.startswith("missing_"):
        records[0][{"missing_title": "title_ja", "missing_summary": "summary", "missing_url": "url"}[kind]] = ""
    else:
        records = []
    with pytest.raises(builders.NewsGraspBuilderError):
        builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=records)
    assert not (root / "digest" / "Summary" / f"{DAY}-audio-script.md").exists()


def test_sufficient_summary_keeps_original_bytes_and_hash(tmp_path):
    root = _repo(tmp_path, rich=True)
    original = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY)
    with_articles = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=_records())
    assert with_articles["status"] == "reused"
    assert original["sourceHash"] == with_articles["sourceHash"]
    assert original["outputHash"] == with_articles["outputHash"]
    assert not with_articles.get("supplementalSources")


def test_audio_dependency_binds_editor_not_article_history():
    from tools.news_grasp_repair_registry import build_daily_artifact_dag
    node = build_daily_artifact_dag(("ai",))["daily_audio_script"]
    assert tuple(node["dependsOn"]) == ("summary", "editor")


def test_saved_previous_audio_dependency_plan_remains_readable(monkeypatch):
    from tools import news_grasp_repair_registry as repair
    original = repair.build_daily_artifact_dag

    def previous(categories):
        dag = original(categories)
        dag["daily_audio_script"]["dependsOn"] = ["summary"]
        return dag

    with monkeypatch.context() as patch:
        patch.setattr(repair, "build_daily_artifact_dag", previous)
        plan = repair.build_repair_plan(issue_date=DAY, run_id="saved-run", categories=("ai",), checkpoints={}, failures=[])
    assert repair.validate_repair_plan(plan) == plan


def test_used_article_change_invalidates_script_binding(tmp_path):
    root = _repo(tmp_path)
    records = _records()
    first = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=records)
    records[0]["summary"] = records[0]["summary"].replace("設備の稼働計画", "設備の増設計画")
    changed = builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=records)
    assert changed["status"] == "materialized"
    assert changed["sourceHash"] != first["sourceHash"]
    assert changed["outputHash"] != first["outputHash"]


def test_default_builder_passes_saved_editor_records(tmp_path, monkeypatch):
    from tools.news_grasp_daily_content import _default_derived_builder
    records = _records()

    class Reached(BaseException):
        pass

    def capture(**kwargs):
        assert kwargs["article_records"] == records
        raise Reached()

    monkeypatch.setattr(builders, "materialize_summary_audio_script", capture)
    with pytest.raises(Reached):
        _default_derived_builder(repo_root=tmp_path, issue_date=DAY, run_id="saved-run",
                                 artifact_checkpoints={"editor": {"status": "Green", "payload": {"append_records": records}}})


@pytest.mark.parametrize("status", ["Red", "", None])
def test_default_builder_rejects_unverified_editor_supplement(tmp_path, monkeypatch, status):
    from tools.news_grasp_daily_content import _default_derived_builder, DailyContentError

    def capture(**kwargs):
        assert not kwargs.get("article_records"), "未検証の記事が台本へ渡されました"
        raise builders.NewsGraspBuilderError("NG_SUMMARY_AUDIO_SCRIPT_QUALITY_INVALID")

    monkeypatch.setattr(builders, "materialize_summary_audio_script", capture)
    with pytest.raises((DailyContentError, builders.NewsGraspBuilderError)):
        _default_derived_builder(repo_root=tmp_path, issue_date=DAY, run_id="saved-run",
                                 artifact_checkpoints={"editor": {"status": status, "payload": {"append_records": _records()}}})


def test_builder_and_real_normalizer_share_history_quality(tmp_path, monkeypatch):
    from tools.tts import build_script
    root = _repo(tmp_path, rich=True)
    scripts = root / "digest/Summary"
    (scripts / "2026-08-13-audio-script.md").write_text(
        "今日の観点・考察です。Summaryで確認できた事実と未確定事項を分け、"
        "誰が実装と継続運用の責任を負うのかを見極めることが重要です。"
        "明日以降は続報と実装条件を観測点として追います。", encoding="utf-8")
    builders.materialize_summary_audio_script(repo_root=root, issue_date=DAY, article_records=_records())
    monkeypatch.setattr(build_script, "SCRIPT_DIR", scripts)
    monkeypatch.setattr(build_script, "BUILD_DIR", root / "build/tts")
    assert build_script.build(DAY) is not None
