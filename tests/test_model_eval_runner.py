#!/usr/bin/env python3
"""モデル評価 runner/集計の契約テスト。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.prepare_model_eval_fixture import CANONICAL_GENRES, build_eval_fixture
from tools.run_model_eval import (
    VARIANTS,
    aggregate_combo_scores,
    aggregate_newsroom_editor_scores,
    aggregate_scores,
    build_prompt,
    main,
    run_codex_variant,
    write_combo_prompts,
)


def _record(cat: str, idx: int) -> dict:
    return {
        "date": "2026-06-13",
        "genre": cat,
        "title": f"{cat} source title {idx}",
        "title_ja": f"{cat} 日本語タイトル {idx}",
        "url": f"https://example.com/{cat}/{idx}",
        "source": "Example",
        "summary": f"{cat} summary {idx}",
        "bullets": [f"{cat} bullet {idx}"],
    }


def test_build_eval_fixture_uses_canonical_genres_and_title_ja(tmp_path: Path) -> None:
    jsonl = tmp_path / "articles.jsonl"
    rows = []
    for cat in CANONICAL_GENRES:
        rows.extend(_record(cat, i) for i in range(5))
    rows.append(_record("Foreign Exchange", 1))
    rows.append({**_record("AI", 99), "title_ja": ""})
    jsonl.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    fixture = build_eval_fixture(jsonl, per_category=3)

    assert len(fixture["items"]) == 21
    assert {item["genre"] for item in fixture["items"]} == set(CANONICAL_GENRES)
    assert all(item["title_ja"] for item in fixture["items"])


def test_model_eval_prompt_contains_variant_and_fixture() -> None:
    fixture = {"version": 1, "items": [_record("AI", 1)]}
    prompt = build_prompt(
        instruction="# Instruction\nReturn JSON.",
        fixture=fixture,
        variant="mini-reporter",
        model="gpt-5.6-luna",
    )
    assert "mini-reporter" in prompt
    assert "gpt-5.6-luna" in prompt
    assert '"items"' in prompt


def test_aggregate_scores_selects_best_cost_adjusted_variant(tmp_path: Path) -> None:
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    for name, naturalness, cost in [
        ("mini", 3, 1.0),
        ("full", 5, 3.3),
        ("mini-editor", 4, 1.6),
    ]:
        (results_dir / f"{name}.json").write_text(json.dumps({
            "model": name,
            "cost_weight": cost,
            "items": [{
                "url": "https://example.com/1",
                "title_ja": "title",
                "summary": "summary",
                "bullets": ["bullet"],
                "self_score": {
                    "fact_retention": 5,
                    "naturalness": naturalness,
                    "news_grasp_style": naturalness,
                    "compression": 4,
                    "emphasis_ready": 4,
                },
            }],
        }), encoding="utf-8")

    report = aggregate_scores(results_dir)

    assert report["recommended_variant"] == "mini-editor"
    assert report["variants"]["mini"]["item_count"] == 1
    assert report["variants"]["full"]["cost_weight"] == 3.3


def test_aggregate_scores_reports_role_scoped_recommendations(tmp_path: Path) -> None:
    """記者・文体 editor モデルは別々に選ぶ。prompt の存在だけで確定しない。"""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    for name, model, naturalness, cost in [
        ("mini", "gpt-5.4-mini", 4, 1.0),
        ("full", "gpt-5.4", 5, 3.3),
        ("mini-editor", "gpt-5.4-mini", 4, 1.6),
        ("mini-editor-55", "gpt-5.5", 5, 5.0),
    ]:
        (results_dir / f"{name}.json").write_text(json.dumps({
            "model": model,
            "cost_weight": cost,
            "items": [{
                "url": "https://example.com/1",
                "title_ja": "title",
                "summary": "summary",
                "bullets": ["bullet"],
                "self_score": {
                    "fact_retention": 5,
                    "naturalness": naturalness,
                    "news_grasp_style": naturalness,
                    "compression": 4,
                    "emphasis_ready": 4,
                },
            }],
        }), encoding="utf-8")

    report = aggregate_scores(results_dir)

    assert report["roles"]["reporter"]["candidate_variants"] == ["full", "mini"]
    assert report["roles"]["style_editor"]["candidate_variants"] == ["mini-editor", "mini-editor-55"]
    assert report["roles"]["reporter"]["recommended_variant"] == "mini"
    assert report["roles"]["style_editor"]["recommended_variant"] == "mini-editor"


def test_run_codex_variant_uses_current_exec_cli_without_search(monkeypatch, tmp_path: Path) -> None:
    """Codex CLI help に無い `--search` を評価 runner へ混入させない。"""
    prompt = tmp_path / "prompt.md"
    output = tmp_path / "out.json"
    log = tmp_path / "eval.log"
    schema = tmp_path / "schema.json"
    prompt.write_text("Return JSON.", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr("tools.run_model_eval.subprocess.run", fake_run)

    rc = run_codex_variant(
        prompt_path=prompt,
        output_path=output,
        log_path=log,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        schema_path=schema,
        repo_root=tmp_path,
        codex_exe="codex",
    )

    cmd = seen["cmd"]
    assert rc == 0
    assert cmd[:2] == ["codex", "exec"]
    assert "--search" not in cmd
    assert cmd[-1] != "-"
    assert "--output-schema" in cmd
    assert "--output-last-message" in cmd
    assert 'model_reasoning_effort="high"' in cmd


def test_run_codex_variant_wraps_ps1_codex_executable(monkeypatch, tmp_path: Path) -> None:
    """Windows の `codex.ps1` は直接 CreateProcess せず powershell.exe 経由で起動する。"""
    prompt = tmp_path / "prompt.md"
    output = tmp_path / "out.json"
    log = tmp_path / "eval.log"
    schema = tmp_path / "schema.json"
    codex_ps1 = tmp_path / "codex.ps1"
    prompt.write_text("Return JSON.", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    codex_ps1.write_text("param()", encoding="utf-8")
    seen: dict[str, object] = {}

    class _Result:
        returncode = 0

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        seen["cmd"] = cmd
        return _Result()

    monkeypatch.setattr("tools.run_model_eval.subprocess.run", fake_run)

    rc = run_codex_variant(
        prompt_path=prompt,
        output_path=output,
        log_path=log,
        model="gpt-5.6-luna",
        reasoning_effort="high",
        schema_path=schema,
        repo_root=tmp_path,
        codex_exe=str(codex_ps1),
    )

    cmd = seen["cmd"]
    assert rc == 0
    assert cmd[:5] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert str(codex_ps1) in cmd
    assert "exec" in cmd
    assert 'model_reasoning_effort="high"' in cmd


def test_run_codex_variant_rejects_shell_script_executables(monkeypatch, tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    output = tmp_path / "out.json"
    log = tmp_path / "eval.log"
    schema = tmp_path / "schema.json"
    prompt.write_text("Return JSON.", encoding="utf-8")
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "tools.run_model_eval.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("shell script must be rejected before subprocess.run"),
    )

    with pytest.raises(ValueError, match="unsupported Codex executable extension"):
        run_codex_variant(
            prompt_path=prompt,
            output_path=output,
            log_path=log,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            schema_path=schema,
            repo_root=tmp_path,
            codex_exe=str(tmp_path / "codex.cmd"),
        )


def test_aggregate_combo_scores_selects_reporter_editor_pair_by_final_quality_and_total_cost(tmp_path: Path) -> None:
    """採用判断は reporter/editor 単体ではなく、最終出力品質と合算コストで行う。"""
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    def write_result(name: str, model: str, cost: float, naturalness: int) -> None:
        (results_dir / f"{name}.json").write_text(json.dumps({
            "model": model,
            "cost_weight": cost,
            "items": [{
                "url": "https://example.com/1",
                "title_ja": "title",
                "summary": "summary",
                "bullets": ["bullet"],
                "self_score": {
                    "fact_retention": 5,
                    "naturalness": naturalness,
                    "news_grasp_style": naturalness,
                    "compression": 5,
                    "emphasis_ready": 5,
                },
            }],
        }), encoding="utf-8")

    write_result("mini", "gpt-5.4-mini", 1.0, 3)
    write_result("full", "gpt-5.4", 3.3, 4)
    write_result("mini__mini-editor", "gpt-5.4-mini", 1.6, 4)
    write_result("full__mini-editor", "gpt-5.4-mini", 1.6, 5)
    write_result("full__mini-editor-55", "gpt-5.5", 5.0, 5)

    report = aggregate_combo_scores(results_dir)

    assert report["selection_status"] == "selected"
    assert report["recommended_combo"] == "full__mini-editor"
    assert report["combos"]["full__mini-editor"]["total_cost_weight"] == 4.9
    assert report["combos"]["full__mini-editor"]["final_quality_score"] == 5.0
    assert report["combos"]["full__mini-editor-55"]["total_cost_weight"] == 8.3


def test_write_combo_prompts_uses_each_reporter_result_as_editor_input(tmp_path: Path) -> None:
    """editor 評価は mini 固定ではなく、reporter ごとの出力を入力にする。"""
    results_dir = tmp_path / "results"
    prompt_dir = tmp_path / "prompts"
    results_dir.mkdir()
    (results_dir / "mini.json").write_text(json.dumps({
        "model": "gpt-5.4-mini",
        "items": [{"title_ja": "mini reporter output"}],
    }), encoding="utf-8")
    (results_dir / "full.json").write_text(json.dumps({
        "model": "gpt-5.4",
        "items": [{"title_ja": "full reporter output"}],
    }), encoding="utf-8")

    paths = write_combo_prompts(results_dir=results_dir, output_dir=prompt_dir)

    full_prompt = paths["full__mini-editor"].read_text(encoding="utf-8")
    assert "full reporter output" in full_prompt
    assert "mini reporter output" not in full_prompt


def test_newsroom_editor_variants_are_separate_from_style_rewrite_editor() -> None:
    """編集長モデル評価を、文体 rewrite editor 評価と混同しない。"""
    newsroom = {
        name: cfg for name, cfg in VARIANTS.items()
        if cfg.get("role") == "newsroom_editor"
    }
    assert set(newsroom) == {
        "newsroom-editor-mini",
        "newsroom-editor-54",
        "newsroom-editor-55",
    }
    assert all(cfg["prompt"] == Path("prompts") / "model-eval-newsroom-editor.md" for cfg in newsroom.values())
    assert VARIANTS["mini-editor"]["role"] == "style_editor"


def test_newsroom_editor_prompt_covers_editor_in_chief_tasks() -> None:
    """編集長評価 prompt は rewrite ではなく、統制・判断・統合を評価する。"""
    prompt = (Path("prompts") / "model-eval-newsroom-editor.md").read_text(encoding="utf-8-sig")
    for phrase in [
        "spawn plan",
        "verify_reporter_output",
        "repair decision",
        "cross-category dedup",
        "Summary",
        "append safety",
        "context budget",
        "DeepDive",
    ]:
        assert phrase in prompt
    assert "Rewrite only unnatural or weak prose" not in prompt


def test_newsroom_editor_fixture_covers_required_full_duty_scenarios() -> None:
    """編集長評価 fixture は実業務の判断シナリオをすべて含む。"""
    fixture = json.loads((Path("fixtures") / "newsroom_editor_eval_fixture.json").read_text(encoding="utf-8-sig"))
    task_ids = {task["task_id"] for task in fixture["tasks"]}
    assert task_ids == {
        "orchestration",
        "gate_repair",
        "cross_category_dedup",
        "summary_planning",
        "append_safety",
        "context_budget",
        "deepdive_direction",
    }
    assert fixture["constraints"]["source_policy"] == "no_recollection"
    assert any("full articles.jsonl" in action for action in fixture["forbidden_actions"])


def test_aggregate_newsroom_editor_scores_selects_by_full_editor_duties_and_cost(tmp_path: Path) -> None:
    """編集長モデルは全業務スコアの平均と総コストで選定する。"""
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    def write_result(name: str, model: str, cost: float, base: int) -> None:
        (results_dir / f"{name}.json").write_text(json.dumps({
            "model": model,
            "cost_weight": cost,
            "tasks": [{
                "task_id": "orchestration",
                "decision": "ok",
                "self_score": {
                    "orchestration": base,
                    "gate_decision": base,
                    "dedup_resolution": base,
                    "summary_planning": base,
                    "append_safety": base,
                    "context_budget": base,
                    "deepdive_direction": base,
                },
            }],
        }), encoding="utf-8")

    write_result("newsroom-editor-mini", "gpt-5.4-mini", 1.6, 3)
    write_result("newsroom-editor-54", "gpt-5.4", 3.3, 5)
    write_result("newsroom-editor-55", "gpt-5.5", 5.0, 5)

    report = aggregate_newsroom_editor_scores(results_dir)

    assert report["selection_status"] == "selected"
    assert report["source_of_truth"] == "newsroom_editor_full_duty_eval"
    assert report["recommended_variant"] == "newsroom-editor-54"
    assert report["variants"]["newsroom-editor-54"]["quality_score"] == 5.0
    assert report["variants"]["newsroom-editor-55"]["cost_weight"] == 5.0


def test_newsroom_editor_prepare_only_emits_only_newsroom_prompts(tmp_path: Path, capsys) -> None:
    """編集長評価準備は reporter/style editor prompt を混ぜない。"""
    fixture = tmp_path / "fixture.json"
    out_dir = tmp_path / "eval"
    fixture.write_text(json.dumps({"version": 1, "items": []}), encoding="utf-8")

    rc = main([
        "--fixture",
        str(fixture),
        "--out-dir",
        str(out_dir),
        "--newsroom-editor-only",
        "--prepare-only",
    ])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["prompts"]) == {
        "newsroom-editor-mini",
        "newsroom-editor-55",
    }
