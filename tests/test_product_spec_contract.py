#!/usr/bin/env python3
"""News-Grasp product constitution contract tests."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec.md"
README = ROOT / "README.md"
AGENTS = ROOT / "AGENTS.md"
CLAUDE = ROOT / "CLAUDE.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _headings(markdown: str) -> set[str]:
    return set(re.findall(r"(?m)^##+\s+(.+)$", markdown))


def test_product_constitution_defines_mission_reader_and_outcome() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Product Constitution" in headings
    assert "ITコンサルタント" in text
    assert "膨大なニュースを一つ一つ確認" in text
    assert "最適な粒度" in text
    assert "完全自立型ニュースサイト" in text


def test_product_constitution_locks_done_and_autonomous_repair_principles() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    required_headings = {
        "Principle 1: 直せるものは直して完走",
        "Definition of Done",
        "System Integrity",
        "Fatal Boundaries",
        "Change Governance",
    }
    assert required_headings <= headings

    for phrase in [
        "直せるものは直して完走",
        "repair",
        "quarantine+refill",
        "reporter retry",
        "re-verify",
        "verified",
        "typed fatal",
        "Web / Audio / YouTube Podcast / playlist / notification",
    ]:
        assert phrase in text


def test_product_constitution_maps_feature_changes_to_quality_gates() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Feature Change Quality Gate Matrix" in headings
    for phrase in [
        "機能を追加、削除、修正する場合",
        "同じ変更単位で品質 gate",
        "Quality gate / predicate",
        "Up/downstream artifacts",
        "Required verification",
        "Source collection / URL freshness / dedup",
        "tools.audit_all_article_urls.blocking_url_dates(issue_date)",
        "TODAY / YESTERDAY",
        "2日以上前の過去URL不良",
        "warning / inventory / repair candidate",
        "Article data / schema / tags",
        "tools.publish_inventory.scheduled_category_ids(issue)",
        "tools.validate_daily_quality --date <date> --docs-root docs --require-deepdive",
        "Web publish surface",
        "Deploy workflow success",
        "workflow Pages status built",
        "Public UI / OGP / PWA / thumbnails",
        "docs/sw.js",
        "local test pass や local DOM/visual sentinel だけでは完了ではない",
        "local HEAD / remote HEAD 一致",
        "public CSS",
        "public DOM sentinel",
        "番号付き要求 coverage",
        "ToDo（今後の作業）",
        "Audio / TTS",
        "YouTube Podcast / playlist",
        "Daily Podcast と DeepDive Podcast の playlist 境界",
        "同日重複禁止",
        "Deleted video item 禁止",
        "--audit-playlists",
        "Notification",
        "NEWS_GRASP_NOTIFICATION_DELIVERY_RECEIPT_V2",
        "Direct execution evidence / canonical publish manifest / causal retry",
        "NEWS_GRASP_RUN_OBSERVATION_V1",
        "NEWS_GRASP_PUBLISH_MANIFEST_V2",
        "NEWS_GRASP_DIRECT_RUNTIME_V2",
        "causalRemediationReceipt",
        "Codex automation title / task lifecycle",
        "task自身が開始後最初のhost操作",
        "tools/news_grasp_title_control.py",
        "Codex App DB",
        "template/installed/App DB/snapshot exact parity",
        "Direct runtime / state / recovery",
        "旧installer起動はmutation前にfail-closed",
        "Incident / reporting",
        "recovery evidence",
        "新規 `docs/incidents/*-report.html` は追跡・公開しない",
        "build/incidents/",
        "Affected matrix rows",
        "Gate update decision",
        "Verification command",
        "docs/spec.md",
        "tests/test_product_spec_contract.py",
    ]:
        assert phrase in text


def test_product_constitution_defines_category_schedule_source_of_truth() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Category Schedule Source of Truth" in headings
    for phrase in [
        "月 | fx, ai, it, mobility, manufacturing, economy | game",
        "火 | fx, ai, it, mobility, manufacturing, economy, game |",
        "水 | fx, ai, it, mobility, manufacturing, economy | game",
        "土 | fx, ai, it, mobility, game | manufacturing, economy",
        "日 | fx, ai, it, mobility, game | manufacturing, economy",
        "tools.publish_inventory.scheduled_category_ids(issue)",
        "runner は 7 カテゴリ固定で sub-agent を起動してはならない",
        "土日に Manufacturing / Economy digest",
        "Game に限らず、任意の非対象カテゴリ",
        "非対象カテゴリ artifact",
        "runner bug",
    ]:
        assert phrase in text


def test_product_constitution_maps_category_schedule_impact() -> None:
    text = _read(SPEC)

    for phrase in [
        "Category schedule impact map",
        "Runner Stage0 / Stage2 reporter fan-out",
        "Editor manifest / newsroom prompt",
        "publish inventory / repair scope",
        "generate_pages / public UI",
        "validate_daily_quality / validate_generation_quality / reconcile",
        "YouTube Podcast / publish_complete",
        "historical fallback evidence",
        "verify-publish-complete",
        "Codex automation template/installed/App DB/snapshot parity",
        "direct launcherとruntime generation",
        "consumer-owned public verifier",
        "Windows Task Scheduler、runner、bootstrap、deadman readinessを公開成功条件へ戻さない",
    ]:
        assert phrase in text


def test_product_constitution_keeps_recovered_state_and_future_gates_separate() -> None:
    text = _read(SPEC)
    headings = _headings(text)

    assert "Operational Premise Fidelity" in headings
    for phrase in [
        "復旧済みの公開成果物",
        "未復旧扱いに巻き戻してはならない",
        "現在状態の復旧タスク",
        "将来の完走判定 gate",
        "goal が打ち取れなかった理由",
        "完走扱いになった理由",
        "公開済みの非対象カテゴリ artifact",
        "当日必須カテゴリへ昇格しない",
    ]:
        assert phrase in text


def test_product_constitution_distinguishes_necessary_checks_from_sufficient_e2e_proof() -> None:
    """テスト Green や SLO gate 実装を、実運用完走の十分証明へ矮小化しない。"""
    text = _read(SPEC)

    for phrase in [
        "pytest PASS は必要条件",
        "daily quality PASS は必要条件",
        "public URL PASS は必要条件",
        "runner/watcher live readiness は必要条件",
        "1時間以内の本番相当 push直前 E2E PASS",
        "効率的・完全完走を主張するための必要条件",
        "SLO gate 実装を SLO 達成実測と混同してはならない",
        "E2E 未実施なら効率的・完全・1時間以内完走とは報告してはならない",
    ]:
        assert phrase in text


def test_product_constitution_defines_final_only_e2e_and_shared_deepdive_quality() -> None:
    """E2E連発とURL/対談の個別修正へ戻れない上位契約を固定する。"""
    text = _read(SPEC)
    headings = _headings(text)

    assert "E2E Final Admission Covenant" in headings
    assert "DeepDive Source and Podcast Value Covenant" in headings
    for phrase in [
        "final_confirmation_only",
        "発見・デバッグ・readiness判定に使ってはならない",
        "static → contract → simulation → component → integration → live reconcile",
        "NEWS_GRASP_E2E_FINAL_ADMISSION_V1",
        "同一issue date・同一scheduled-equivalent intentで一回だけ",
        "別worktree、別receipt、別run_idで試行回数をresetしない",
        "NoPublishを必須",
        "ResumeFromStageを禁止",
        "efficiency_design",
        "simulation",
        "isolation",
        "対象日artifactだけを除去",
        "high-cost attemptを二重予約してはならず",
        "TDDのRedは、単一の失敗テストや単一fixtureを作れば足りるものではない",
        "網羅的なテスト観点を列挙できない状態は要件定義が未完了である反証",
        "全deterministic handlerを同一再検証前に各一回",
        "各recordのthumbを個別に検証",
        "followup-review-evidence-patch",
        "repair-plan",
        "UPSTREAM_DESIGN_ESCAPE",
        "data/deepdive-provenance/<date>.json",
        "URLの全出現位置",
        "evidence-backed findings",
        "theme_specific_insight / evidence_depth / causal_coherence / counterevidence / decision_utility / dialogue_naturalness / relation_map_utility",
        "reviewRoute",
        "DEEPDIVE_QUALITY_REVIEW_V2",
        "deepdive_url_provenance_invalid",
        "deepdive_dialogue_value_invalid",
    ]:
        assert phrase in text


def test_runner_matrix_separates_scheduled_production_from_final_e2e_budget() -> None:
    text = _read(SPEC)
    for phrase in (
        "scheduled_production",
        "scheduled_recovery",
        "final E2E attemptを消費しない",
        "issue date単位の最大9 model call",
        "復旧は同じ日付identityの残予算を共有",
    ):
        assert phrase in text


def test_product_constitution_retires_windows_task_scheduler_from_daily_authority() -> None:
    """廃止済みTask Schedulerを通常日次の入口・readinessへ戻さない。"""
    text = _read(SPEC)

    assert "Windows Task Scheduler は廃止済み" in text
    assert "Codex automation `news-grasp-6-40`" in text
    assert "NEWS_GRASP_WINDOWS_TASK_SCHEDULER_RETIRED" in text
    current = text[text.index("## 2026-08-30 Direct 06:00 Mainline Supersession") : text.index("## 憲法の機械正本")]
    assert "通常日次の起動、preflight、readiness、install、rollback、completion evidenceに使用しない" in current


def test_product_constitution_defines_sustainable_complete_repair_invariants() -> None:
    """内部欠陥を fallback で正常扱いせず、局所 repair と live runner guard を正本化する。"""
    text = _read(SPEC)

    for phrase in [
        "外部システム要因以外で公開面が揃わない停止は許容しない",
        "fallback は通常日次完走ではない",
        "通常日次バッチ経路の fallback publish は完全禁止",
        "fallback_ok や published_fallback_with_notice を OK marker",
        "handler 未実装は Red とする",
        "live runner 上書きは backup + 明示承認 + rollback",
    ]:
        assert phrase in text


def test_product_constitution_defines_repair_decision_debt_covenant() -> None:
    """repair の恒久対策を retry 回数ではなく決定責務で固定する。"""
    text = _read(SPEC)
    headings = _headings(text)

    assert "Repair Decision Debt Covenant" in headings
    assert "Repair Decision Debt Commitment" in headings
    for phrase in [
        "repair の回数を増やすことではなく",
        "validator / coverage matrix / orchestrator / registry / runner",
        "handler別の有限`repair-plan`",
        "全deterministic handlerへ各handlerに属するartifactだけを渡し",
        "repair_context_overbroad",
        "repair_context_scope_mismatch",
        "blocked_repair_handler_unimplemented",
        "blocked_deterministic_repair_not_applicable",
        "repair_handler_output_scope_violation",
        "blocked_unknown_repair_class",
        "repair-decision-debt-2026-06-29",
    ]:
        assert phrase in text


def test_weekly_failure_source_of_truth_and_runner_terminal_semantics_are_documented() -> None:
    text = _read(SPEC)

    for phrase in [
        "validator が生成した structured issue_code を唯一の分類正本",
        "structured unknown を message prose から再分類してはならない",
        "matrix は issue artifact と handler scope を実行前に照合",
        "固定 attempt 回数を terminal predicate にしてはならない",
        "deadline と typed repair ledger",
        "GitHub Release upload の HTTP 502 / 503",
        "blocked_external_readiness",
    ]:
        assert phrase in text


def test_product_constitution_has_human_commitment_review_gate() -> None:
    """Codex が spec 上の Human Commitment を自己判断で変更しない。"""
    text = _read(SPEC)
    headings = _headings(text)

    assert "Human Commitment" in headings
    for phrase in [
        "| approval_status | Committed |",
        "| committed_by_human | true |",
        "| approved_by_user_text | PLEASE IMPLEMENT THIS PLAN: |",
        "| approved_goal_statement | News-Grasp最大重大障害 hardening + Plan Modeレビュー恒久対策 R7 を、Phase 0/A/B の範囲で実装する。 |",
        "| approval_evidence_ref | current chat turn: user message `PLEASE IMPLEMENT THIS PLAN:` with R7 plan body |",
        "| approved_at | 2026-06-26 |",
        "| commitment_version | news-grasp-max-incident-hardening-r7 |",
        "| commitment_scope | Phase 0 spec/provenance repair; Phase A review discipline; Phase B News-Grasp local hardening. Excludes live runner sync/full E2E/publish/push/public proof/rollback unless separately approved. |",
        "| open_questions | None for Phase 0/A/B local implementation scope. Yellow public actions remain separately approval-gated. |",
        "## User Answer Provenance",
        "PLEASE IMPLEMENT THIS PLAN:",
        "ChatGPTレビューに通すための最低限の基準であるインプットは完全に用意してからレビューに渡す",
        "Codex はこの Human Commitment を自己判断で変更してはならない",
        "repo-local pytest Green は実装証跡であり、人間承認ではない",
        "full E2E 未実施時に 1時間以内の完全完走証明済み と報告してはならない",
    ]:
        assert phrase in text


def test_product_constitution_commits_luna_high_runtime_migration() -> None:
    text = _read(SPEC)

    for phrase in [
        "### Luna-high Runtime Migration Commitment (2026-07-16)",
        "model-runtime-luna-high-2026-07-16",
        "gpt-5.6-luna",
        "reasoning effort `high`",
        "gpt-5.4系に依存する本番処理を残さない",
        "Runner / state / recovery",
    ]:
        assert phrase in text


def test_product_constitution_links_summary_lanes_to_quality_gates() -> None:
    """記事カード/ESSAY要約レーン改修を spec の品質 gate と人間承認へ接続する。"""
    text = _read(SPEC)

    for phrase in [
        "## Summary Layer Lanes Commitment",
        "summary-layer-lanes-2026-06-29",
        "記事カード要約UI",
        "ESSAY",
        "スマホ版トップ帯",
        "役割者名を出さない",
        "アイコンは保持",
        "DOM に存在するだけでは Green ではなく",
        "FACT / CONTEXT / OUTLOOK",
        "WATCH / SIGNAL / IMPLICATION",
        "theme_lanes",
        "section.lanes",
        "後から文分割",
        "tests/test_reflection_theme_essay.py",
        "事実・概要 / 背景・要点 / 影響・展望",
        "3段すべて視認可能",
        "--summary-*",
        "未定義CSS変数",
        "Chrome 操作系スキルの実画面証跡",
        "過去記事要約3層リライト",
        "Affected matrix rows",
        "Public UI / OGP / PWA / thumbnails",
        "Summary / editorial reflection",
        "Gate update decision",
        "tests/test_summary_layer_lanes.py",
        "tests/test_newsroom_prompts.py",
        "tests/test_summary_pattern_d.py",
        "tests/test_home_variant_b.py",
        "tests/test_rewrite_bullets_3layer.py",
        "結合テスト Green の場合のみ commit/push",
        "Yellow 以下は修正と再テストを継続",
    ]:
        assert phrase in text


def test_product_constitution_defines_repair_completeness_proof() -> None:
    """完全自走 repair は実装の雰囲気ではなく、matrix と contract で証明する。"""
    text = _read(SPEC)

    for phrase in [
        "repair completeness = coverage matrix + zero unimplemented + fixture repair + runner single path",
        "coverage matrix に未掲載の failure は blocked_unknown_repair_class",
        "handler_unimplemented_red は最終 Green 条件では 0 件",
        "existing artifact repair では LLM worker を起動しない",
    ]:
        assert phrase in text


def test_product_constitution_has_category_hero_turn4_commitment() -> None:
    """カテゴリートップヒーローカード改善を spec と品質 gate に結び付ける。"""
    text = _read(SPEC)
    headings = _headings(text)

    assert "Category Hero Card Turn 4 Commitment" in headings
    for phrase in [
        "category-hero-turn4-2026-07-01",
        "ヒーローカード",
        "Turn 4 の 4a / 4b / 4c",
        "為替レンズ ヒーローカード改善",
        "文単位",
        "続きを読む →",
        "body_max_chars=104",
        "ExchangeRate-API Open",
        "https://open.er-api.com/v6/latest/USD",
        "Rates By Exchange Rate API",
        "lead_title_lines",
        "category hero lead title line quality",
        "Public UI / OGP / PWA / thumbnails",
        "Summary / editorial reflection",
        "tests/test_category_hero_sentence_fit.py",
        "tests/test_category_hero_turn4_contract.py",
        "tests/test_fx_rates.py",
        "ChatGTPレビューを受けてから再提出して。",
        "pushまで含める",
    ]:
        assert phrase in text


def test_product_constitution_requires_horizontal_incident_bugfix_investigation() -> None:
    """バグ修正を単一部品で閉じず、runner/repair/state/report を同じ incident で見る。"""
    text = _read(SPEC)
    headings = _headings(text)
    agents = _read(AGENTS)
    claude = _read(CLAUDE)

    assert "Incident Bugfix Horizontal Investigation Covenant" in headings
    for phrase in [
        "runner / repair / state / report の横並び調査",
        "同じ incident 単位",
        "runner: 実行体、wrapper、stage 遷移、live copy、scheduler、NoPublish/RecoverOnly",
        "repair: coverage matrix、registry、handler 実装、same-gate re-verify",
        "state: runner state、distribution manifest、gate attempts、publish-complete、recovery proof",
        "report: incident report、bug class、横並び類似候補、新規バグ候補、恒久対策",
        "1 レーンでも未調査なら修正完了にしてはならない",
        "tools.historical_failure_scenarios",
    ]:
        assert phrase in text

    for text in [agents, claude]:
        assert "runner / repair / state / report の横並び調査" in text
        assert "同じ incident 単位" in text
        assert "1 レーンでも未調査なら修正完了にしてはならない" in text
        assert "Do not place new `docs/incidents/*-report.html` files in git or GitHub Pages" in text
        assert "untracked `build/incidents/`" in text
        assert "unless the user separately approves public publication" in text


def test_product_constitution_keeps_markdown_structure_and_links_minimal() -> None:
    text = _read(SPEC)

    assert text.startswith("# Product Spec: News-Grasp")
    assert "## Open Questions" not in text
    assert "Describe the stable user or operator outcome" not in text
    assert "| Area | Requirement |" in text
    assert text.count("docs/spec.md") >= 1


def test_product_constitution_binds_directional_digest_repair_to_quality_matrix() -> None:
    """reconcile の検出方向・handler 能力・runner 成功条件を spec に固定する。"""
    text = _read(SPEC)

    for phrase in [
        "digest_articles_digest_only",
        "digest_articles_articles_only",
        "digest-articles-digest-only-patch",
        "digest-card-insert-patch",
        "blocked_digest_only_ambiguous",
        "blocked_articles_only_record_incomplete",
        "deterministic handler として宣言する row は `_blocked_ambiguous`",
        "`noop` / `not_applicable` を repair 成功として扱わない",
    ]:
        assert phrase in text


def test_product_constitution_binds_horizontal_failure_modes_and_matrix_ownership() -> None:
    """digest 以外の片方向潰しと matrix/registry drift も spec 契約に固定する。"""
    text = _read(SPEC)

    for phrase in [
        "`thumb_missing` / `thumb_invalid`",
        "`search_audit_coverage_terms_missing` / `search_audit_queries_recoverable`",
        "`audio_script_missing` / `audio_script_quality_invalid`",
        "matrix が所有する `verify_gate` / `allowed_artifacts`",
        "registry metadata で上書きせず",
        "legacy の方向不明 code は explicit typed Red",
    ]:
        assert phrase in text


def test_product_constitution_binds_repair_closed_world_completeness_gate() -> None:
    """完全性 claim を ad hoc 棚卸しでなく source hash 付き単一 gate に束縛する。"""
    text = _read(SPEC)

    for phrase in [
        "tools.repair_system_completeness",
        "supported_verify_gates",
        "orphan_repair_implementation",
        "source_hashes",
        "validator issue code",
        "registry handler",
        "historical failure corpus",
    ]:
        assert phrase in text


def test_product_constitution_is_referenced_from_repo_entrypoints() -> None:
    readme = _read(README)
    agents = _read(AGENTS)
    claude = _read(CLAUDE)

    assert "[docs/spec.md](docs/spec.md)" in readme
    for text in [agents, claude]:
        assert "docs/spec.md" in text
        assert "上位プロダクト真実" in text
        assert "非自明な News-Grasp 改修" in text
        assert "Feature Change Quality Gate Matrix" in text
        assert "affected" in text
        assert "tests/test_product_spec_contract.py" in text


def test_daily_45m_contract_is_bound_to_gate_matrix_and_public_completion() -> None:
    """Daily短縮が品質省略でなくroute分離とfresh公開論理積であることを固定する。"""
    text = _read(SPEC)

    for phrase in [
        "2026-09-03 Daily Public 45-minute Contract",
        "static_check` → `scoped_contract_unit` → `current_issue_integration`",
        "raw/full pytest、historical corpus、Playwright全件、crash/replay/drift、final NoPublish E2E",
        "generation_id + predicate_id",
        "automation_id + issue_date + run_intent",
        "superseded_after_external_start",
        "unknown_unobtainable",
        "Daily 45-minute public completion / Release partition",
        "自然scheduled canaryが45分以内",
    ]:
        assert phrase in text

    contract = json.loads(
        (ROOT / "config" / "news_grasp_daily_45m_contract_v1.json").read_text(
            encoding="utf-8"
        )
    )
    ledger = json.loads(
        (ROOT / "config" / "news_grasp_failure_ledger_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["taskOperatingContract"]["taskId"] == "NG-DAILY-45M-20260902"
    assert len(contract["requirements"]) == 11
    assert len(contract["fixtureRegistry"]) == 18
    assert ledger["task_id"] == "NG-DAILY-45M-20260902"
    assert {row["defect_code"] for row in ledger["entries"]} == {
        f"NG-I{index:02d}" for index in range(1, 38)
    }
    required = {
        "defect_code",
        "source_of_truth",
        "affected_consumer",
        "impact_binding",
        "red_fixture",
        "green_test",
        "operational_recovery",
        "independent_evidence",
        "owner",
        "maintenance_condition",
    }
    assert all(required <= set(row) for row in ledger["entries"])
    assert all(
        all(bool(row.get(field)) for field in required)
        for row in ledger["entries"]
    )
    for entrypoint in (_read(AGENTS), _read(CLAUDE)):
        assert "daily_45m_public_route" in entrypoint
        assert "tools.news_grasp_daily_launcher" in entrypoint
        assert "fresh consumer public verifier Green" in entrypoint
