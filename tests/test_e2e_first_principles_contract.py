from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = (
    Path.home()
    / ".codex"
    / "skills"
    / "news-grasp-e2e-discipline"
    / "SKILL.md"
)
SPEC = ROOT / "docs" / "spec.md"
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
BRIDGE = ROOT / "tools" / "e2e_final_admission_bridge.py"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8-sig")


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8-sig")


def test_e2e_purpose_is_one_final_production_equivalent_confirmation() -> None:
    skill = _skill()
    assert "完成済みの運用鎖が本番相当入口で一度だけ成立することを確認する最終試験" in skill
    assert "L8 | final E2E | scheduled-equivalent NoPublishの一回だけの最終確認" in skill


def test_e2e_non_purpose_forbids_discovery_debug_and_readiness_probing() -> None:
    skill = _skill()
    forbidden = (
        "未知の欠陥を探す。",
        "原因を切り分ける。",
        "readinessを確認する。",
        "外部API、認証、quota、公開面の状態を試し打ちする。",
        "「念のため」成功を再確認する。",
    )
    assert all(item in skill for item in forbidden)


def test_e2e_layer_model_keeps_l0_through_l7_out_of_final_attempt_count() -> None:
    skill = _skill()
    for layer in range(9):
        assert f"| L{layer} |" in skill
    assert "L8の条件を満たさないものはE2E試行へ数えない" in skill
    assert "full runnerを起動するものは名前に関係なくE2Eとして数える" in skill


def test_e2e_readiness_admission_requires_all_low_cost_layers_first() -> None:
    skill = _skill()
    ordered = (
        "全RequirementとAcceptanceを凍結",
        "L0からL7を安価な順に一回ずつ閉じる",
        "高コスト予算と独立反証reviewをGreen",
        "final admissionを一度発行",
        "L8を一度だけ実行",
    )
    positions = [skill.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert "missing evidenceを「E2Eで確かめる」ことを禁止" in skill


def test_e2e_attempt_identity_cannot_reset_through_aliases_or_resume() -> None:
    skill = _skill()
    bridge = BRIDGE.read_text(encoding="utf-8-sig")
    assert "News-Grasp:<issue-date>:scheduled-equivalent-nopublish" in skill
    for alias in ("worktree", "run ID", "receipt path", "internal continuation", "ResumeFromStage"):
        assert alias in skill
    assert 'f"{canonical_product_id}:{issue_date}:"' in bridge
    assert '"scheduled-equivalent-nopublish"' in bridge


def test_e2e_checkpoint_boundary_allows_recovery_but_never_final_resume() -> None:
    skill = _skill()
    assert "full runnerを部分stageから再開して確認することはE2Eではなく、復旧integration" in skill
    assert "E2E wrapperにresume機能を持たせてはならない" in skill
    assert "同じrunをresumeしない" in skill


def test_e2e_exploration_is_owned_by_the_cheapest_pre_e2e_layer() -> None:
    skill = _skill()
    mappings = (
        "promptやroute文字列: static/contract",
        "JSON schemaやmanifest: contract/fixture",
        "URL、TTS、DeepDive品質: component/fixture",
        "retry、stale、replay、停止: fault injection",
        "外部認証やquota: dedicated readiness probe",
    )
    assert all(item in skill for item in mappings)


def test_e2e_resource_budget_is_single_use_and_non_resettable() -> None:
    skill = _skill()
    required = (
        "full E2E上限: 1回",
        "external mutation: 0 (`NoPublish`)",
        "上限はworktree、session、receipt、内部継続でリセットしない",
        "同じ失敗shapeを名前や引数だけ変えて再試行してはならない",
    )
    assert all(item in skill for item in required)


def test_e2e_side_effect_boundary_requires_nopublish_and_no_push_evidence() -> None:
    wrapper = WRAPPER.read_text(encoding="utf-8-sig")
    skill = _skill()
    assert "'-NoPublish'" in wrapper
    assert "ResumeFromStage" not in wrapper
    assert "no-publish/no-push" in skill
    assert "external mutation: 0 (`NoPublish`)" in skill


def test_e2e_stop_and_failure_consumes_attempt_and_returns_upstream() -> None:
    skill = _skill()
    required = (
        "attemptを消費済みのまま保持",
        "UPSTREAM_DESIGN_ESCAPE_V1",
        "E2E内でpatchしない",
        "同一issue dateの第二L8は実行しない",
        "最上流の設計・fixture・consumerを修正し、L0からL7を閉じ直す",
    )
    assert all(item in skill for item in required)


def test_e2e_evidence_contract_binds_identity_freshness_and_actual_command() -> None:
    skill = _skill()
    spec = _spec()
    required = (
        "runnerの絶対pathとSHA-256がfresh",
        "runner argumentsが完全一致",
        "admission自身のcanonical hashが一致",
        "attempt keyを原子的に消費",
    )
    assert all(item in skill for item in required)
    assert "実際に起動する引数配列" in spec
    assert "E2E_COMMAND_DRIFT" in spec


def test_e2e_completion_boundary_cannot_substitute_nopublish_for_public_green() -> None:
    skill = _skill()
    assert "NoPublish E2E GreenだけでNews-Grasp全体を完了としない" in skill
    for surface in (
        "runner terminal state",
        "publish complete",
        "distribution manifest",
        "public surface",
        "DeepDive記事とPodcast",
        "local/remote HEAD",
    ):
        assert surface in skill
    assert "NoPublishだけ" in skill
