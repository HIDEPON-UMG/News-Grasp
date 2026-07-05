from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "ops-prompts" / "2026-07-05-planning-mode-correction.md"


def test_planning_mode_correction_prompt_exists_and_is_copyable() -> None:
    text = PROMPT.read_text(encoding="utf-8")

    assert "```text" in text
    assert "あなたは Codex / Claude の Plan Mode" in text
    assert "受入条件" in text


def test_planning_mode_correction_prompt_locks_required_planning_structure() -> None:
    text = PROMPT.read_text(encoding="utf-8")

    required_phrases = [
        "調査の結果、どこの何が悪いのか",
        "課題と修正の対応表",
        "アンチパターン",
        "入力・処理・出力",
        "runner、repair、state、publish、notification、report、preflight、history",
        "Red",
        "Green",
        "Refactor",
        "低位モデルが追加判断なしで実装できる粒度",
        "未実行、未検証、未反映、未commit、未push",
        "ChatGPTレビュー不要",
        "NATIVE_PLAN_MODE_REVIEW_RECOVERY",
        "no-review 経路",
    ]
    for phrase in required_phrases:
        assert phrase in text


def test_planning_mode_correction_prompt_requires_false_completion_rca() -> None:
    text = PROMPT.read_text(encoding="utf-8")

    required_phrases = [
        "完了詐称",
        "成果物存在確認",
        "実ファイル",
        "report gate",
        "preflight が通らない場合",
        "未作成成果物",
        "残タスク",
        "完了報告禁止",
        "原因究明",
        "対策依頼",
    ]
    for phrase in required_phrases:
        assert phrase in text
