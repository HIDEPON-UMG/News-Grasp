from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = (
    ROOT
    / "automation"
    / "skills"
    / "news-grasp-e2e-discipline"
    / "SKILL.md"
)
SPEC = ROOT / "docs" / "spec.md"
WRAPPER = ROOT / "scripts" / "ops" / "invoke-scheduled-equivalent-nopublish.ps1"
BRIDGE = ROOT / "tools" / "e2e_final_admission_bridge.py"
AUTOMATION_TEMPLATE = ROOT / "automation" / "news-grasp-6-40" / "automation.toml.template"


def _skill() -> str:
    return SKILL.read_text(encoding="utf-8-sig")


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8-sig")


def test_e2e_purpose_is_one_final_production_equivalent_confirmation() -> None:
    skill = _skill()
    assert "完成済みの運用鎖が本番相当入口で成立することを確認する最終試験" in skill
    assert "E2E Greenは「そのinputで一度通った」証拠であり、翌日以降の完走性の十分証明ではない" in skill
    assert "L8 | final E2E | scheduled-equivalent NoPublishのattempt A、必要時だけattempt Bによる最終確認" in skill


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
        "official wrapperでattempt Aのadmissionを消費",
    )
    positions = [skill.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert "missing evidenceを「E2Eで確かめる」ことを禁止" in skill


def test_completion_viability_matrix_defines_conditions_methods_and_destinations() -> None:
    skill = _skill()
    required = (
        "完走性choke point matrix",
        "completionCondition",
        "verificationMethod",
        "greenCriteria",
        "failureDestination",
        "entry_control_plane",
        "input_inventory",
        "model_route_authority",
        "artifact_generation_contract",
        "quality_repair_routing",
        "dry_public_boundary",
        "production_completion_authority",
        "bounded_slo_control",
        "post_publish_issue_boundary",
        "external_dependency_boundary",
    )
    assert all(item in skill for item in required)


def test_completion_viability_failure_destinations_are_closed_world() -> None:
    skill = _skill()
    for destination in (
        "fix_now",
        "recover_now",
        "external_blocker",
        "post_publish_issue",
        "major_incident",
    ):
        assert destination in skill
    assert "YES -> fix_now または recover_now" in skill
    assert "NO -> post_publish_issue" in skill
    assert "Redを見た直後の行き先をskillが決め" in skill


def test_completion_viability_branching_prefers_deterministic_evidence_over_llm_judgment() -> None:
    skill = _skill()
    deterministic_evidence = {
        "artifact path",
        "manifest field",
        "runner state",
        "exit code",
        "issue code",
        "hash",
        "timestamp",
        "ledger event",
        "public verifier result",
    }
    for evidence in deterministic_evidence:
        assert evidence in skill
    assert "条件分岐は原則として決定論で実装する" in skill
    assert "LLM判断が必要な場合は、入力field、rubric、許容出力、reject条件、再判定禁止条件を先に固定" in skill
    assert "自由文の印象で `failureDestination` を選ばせない" in skill


def test_completion_viability_reports_the_user_facing_decision_first() -> None:
    skill = _skill()
    required = (
        "viability_green",
        "viability_yellow",
        "viability_red",
        "朝6時に任せてよいか",
        "完走見込み",
        "未担保のchoke point",
        "Red時の行き先",
        "結論の代替にしない",
    )
    assert all(item in skill for item in required)


def test_temporary_mainline_automation_template_matches_viability_contract() -> None:
    template = AUTOMATION_TEMPLATE.read_text(encoding="utf-8-sig")
    required = (
        'id = "news-grasp-6-40"',
        'name = "News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開"',
        'rrule = "RRULE:FREQ=DAILY;BYHOUR=6;BYMINUTE=0;BYSECOND=0"',
        'model = "gpt-5.6-luna"',
        'reasoning_effort = "max"',
        "SLO は 90 分",
        "automation は監査バッチではありません",
        "public incomplete を Green として最終応答しない",
        "post_publish_issue_list",
        "tools.publish_inventory.scheduled_category_ids(issue_date)",
        "automation/skills/news-grasp-e2e-discipline/SKILL.md",
        "tools.news_grasp_direct_runtime.DIRECT_STAGES",
    )
    assert all(fragment in template for fragment in required)
    forbidden = (
        'schedule = "40 6 * * *"',
        'execution_mode = "stdout_projection"',
        "stdout へ一つの\nJSON projection だけを返す",
        "runner、installer、publisher、\nfinalizerを起動せず",
    )
    assert not any(fragment in template for fragment in forbidden)


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
    recovery_boundary = skill.index(
        "full runnerを無制御に部分stageから再開して確認することはE2Eではなく、復旧integration"
    )
    admission_boundary = skill.index("admission自体の再消費やattempt resetは許可しない")
    forbidden_boundary = skill.index("`ResumeFromStage`をfinal E2Eへ混入")

    assert recovery_boundary < admission_boundary < forbidden_boundary


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
        "logical E2E上限: 2回",
        "external mutation: 0 (`NoPublish`)",
        "上限はworktree、session、receipt、内部継続でリセットしない",
        "同じ失敗shapeを名前や引数だけ変えて再試行してはならない",
    )
    assert all(item in skill for item in required)


def test_scheduled_production_budget_is_disjoint_from_final_e2e_budget() -> None:
    skill = _skill()
    spec = _spec()
    required = (
        "通常06:00 Scheduled TaskはE2Eではない",
        "scheduled_production",
        "scheduled_recovery",
        "issue date単位の最大9 model call",
        "復旧は同じ日付identityの残予算を共有",
        "final E2E attemptを消費しない",
    )
    assert all(item in skill for item in required)
    assert all(item in spec for item in required)


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
        "E2E内で場当たり的にpatchせず",
        "同一issue dateの第三L8",
        "最上流の設計・fixture・consumerを修正してから次の論理attemptへ進む",
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
