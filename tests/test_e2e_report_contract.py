from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "tasks" / "reviews" / "2026-06-28-predeepdive-repair-e2e-report.md"
REPORT_HTML = ROOT / "tasks" / "reviews" / "2026-06-28-predeepdive-repair-e2e-report.html"
E2E_ARTIFACT_INCIDENT_HTML = ROOT / "docs" / "incidents" / "2026-06-28-e2e-artifact-collision-report.html"
FULL_RUNNER_INCIDENT_HTML = ROOT / "docs" / "incidents" / "2026-06-28-full-runner-bug-patterns-report.html"


def test_predeepdive_repair_e2e_report_uses_japanese_contract_sections() -> None:
    """E2E報告を英語テンプレ見出しのまま通さない。"""
    text = REPORT.read_text(encoding="utf-8")

    required_sections = [
        "## 判定",
        "## 実行サマリ",
        "## 検証フロー図",
        "## 試行履歴",
        "## 発見バグ台帳",
        "## 修正台帳",
        "## 本番量台帳",
        "## 通過シナリオ台帳",
        "## 失敗 / 未実行シナリオ台帳",
        "## 副作用台帳",
        "## 証跡マップ",
        "## 完了表現の境界",
    ]
    for section in required_sections:
        assert section in text, section

    forbidden_english_headings = [
        "## Verdict",
        "## Run Summary",
        "## Attempt Ledger",
        "## Bug Discovery Ledger",
        "## Fix Ledger",
        "## Production Volume Ledger",
        "## Scenario Pass Ledger",
        "## Scenario Fail / Skip Ledger",
        "## Side-Effect Ledger",
        "## Evidence Map",
        "## Completion Wording",
    ]
    for heading in forbidden_english_headings:
        assert heading not in text, heading


def test_predeepdive_repair_e2e_report_has_mermaid_flow_map() -> None:
    """各検証フローが何を見ているかをMermaid図で固定する。"""
    text = REPORT.read_text(encoding="utf-8")

    assert "```mermaid" in text
    assert "本番量DeepDive前工程" in text
    assert "repair復帰fixture" in text
    assert "複合異常系" in text
    assert "未証明境界" in text


def test_predeepdive_repair_e2e_report_splits_flow_maps_for_readability() -> None:
    """1枚の巨大Mermaidに詰め込まず、読める単位に分割する。"""
    text = REPORT.read_text(encoding="utf-8")

    required_subsections = [
        "### 図1: 検証全体の見取り図",
        "### 図2: 本番量DeepDive前工程",
        "### 図3: repair復帰fixture",
        "### 図4: local全体回帰と未証明境界",
        "### 図5: 複合異常系A 同一artifact repair後の残留赤",
        "### 図6: 複合異常系B multi gate repair後のpublish境界",
        "### 図7: 複合異常系C 外部障害block と local Red の分離",
        "### 図8: 複合異常系D 曜日inventoryとdistribution manifest境界",
    ]
    for subsection in required_subsections:
        assert subsection in text, subsection

    mermaid_blocks = text.count("```mermaid")
    assert mermaid_blocks >= 8

    for block in text.split("```mermaid")[1:]:
        body = block.split("```", 1)[0]
        edge_count = body.count("-->")
        assert edge_count <= 12, f"Mermaid図が大きすぎます: edge_count={edge_count}"


def test_predeepdive_repair_e2e_report_maps_compound_abnormal_patterns_individually() -> None:
    """複合異常系は各パターンの仕込み、検出、自律復旧、再検証、判定まで個別に示す。"""
    text = REPORT.read_text(encoding="utf-8")

    required_fragments = [
        "### 図5: 複合異常系A 同一artifact repair後の残留赤",
        "仕込み: `data/articles.jsonl` の `title_ja` 欠落 + date不整合。",
        "検出: `record-schema gate` 初回赤。",
        "repair: `record-title-ja-patch`。",
        "recovery: date不整合を再分類し、published_date evidence repair を実行。",
        "re-verify: `record-schema gate` を同一入力で再実行。",
        "判定: `green_after_compound_repair`。",
        "### 図6: 複合異常系B multi gate repair後のpublish境界",
        "仕込み: 複数gateでrepair可能なlocal赤 + 後段publish境界赤。",
        "検出: local gate群とpublish境界。",
        "repair: deterministic repair。",
        "recovery: pre-publish 内部工程を全て Green に戻し、公開工程をテスト対象外境界へ分離。",
        "side-effect guard: fallback/push/upload/通知は実行しない。",
        "判定: `green_before_publish_boundary_no_public_actions`。",
        "### 図7: 複合異常系C 外部障害block と local Red の分離",
        "仕込み: 外部障害block + local Red。",
        "検出: external readiness と local gate。",
        "repair: local側のみ修復可能。",
        "recovery: local Red を修復し、外部障害は typed external evidence として分離。",
        "re-verify: local gate は Green、外部障害block は scenario PASS だが publish Green ではない。",
        "判定: `typed_external_block_handled`。",
        "### 図8: 複合異常系D 曜日inventoryとdistribution manifest境界",
        "仕込み: required/non-target境界 + distribution manifest anchor。",
        "検出: scheduled_category_ids と distribution manifest。",
        "repair: 非対象カテゴリをrequiredへ昇格しない。",
        "recovery: required manifest だけ再構築して再検証。",
        "re-verify: scheduled_category_ids と distribution manifest が一致。",
        "判定: `green_after_inventory_manifest_reverify`。",
    ]

    for fragment in required_fragments:
        assert fragment in text, fragment


def test_predeepdive_repair_e2e_report_html_is_primary_readable_artifact() -> None:
    """E2E報告書はHTML正本として、複合異常系の工程を読めるようにする。"""
    text = REPORT_HTML.read_text(encoding="utf-8")

    assert "<!doctype html>" in text.lower()
    assert "<script" not in text.lower()
    assert "```mermaid" not in text
    assert 'data-report-kind="predeepdive-repair-e2e"' in text

    required_sections = [
        "News-Grasp DeepDive前工程 / repair E2E 結果報告",
        "検証フロー図",
        "複合異常系A 同一artifact repair後の残留赤",
        "複合異常系B multi gate repair後のpublish境界",
        "複合異常系C 外部障害block と local Red の分離",
        "複合異常系D 曜日inventoryとdistribution manifest境界",
        "試行履歴",
        "発見バグ台帳",
        "証跡マップ",
    ]
    for section in required_sections:
        assert section in text, section

    for pattern in ["A", "B", "C", "D"]:
        assert f'data-compound-pattern="{pattern}"' in text

    for fragment in [
        "仕込み",
        "検出",
        "repair",
        "recovery",
        "re-verify",
        "判定",
        "直せるものは直して完走",
        "green_after_compound_repair",
        "green_before_publish_boundary_no_public_actions",
        "typed_external_block_handled",
        "green_after_inventory_manifest_reverify",
    ]:
        assert fragment in text, fragment

    for forbidden in [
        "blocked_unresolved_compound_failure",
        "typed_yellow_not_complete",
        "typed_red_not_complete",
        ">block<",
    ]:
        assert forbidden not in text, forbidden


def test_predeepdive_repair_e2e_report_html_matches_incident_report_tone() -> None:
    """News-Grasp障害レポートの文字フォント、サイズ感、強調、図表表現から逸脱させない。"""
    text = REPORT_HTML.read_text(encoding="utf-8")

    required_tone_fragments = [
        "News-Grasp · Incident Report",
        "Noto Sans JP",
        "Noto Serif JP",
        "JetBrains Mono",
        "max-width:1040px",
        "padding:56px 56px 64px",
        "font-size:44px",
        "font-family:'Noto Serif JP',serif",
        "Started",
        "Stopped",
        "Recovered",
        "Published",
        "Workflow Map · E2E検証工程",
        "Fault boundary · 検証境界",
        "text-decoration-color:#C9A155",
        "background:#F6E7C6",
        "grid-template-columns:1fr 1fr",
        "Layer 1",
        "Layer 6",
        "News-Grasp BUG_REPORT_DESIGN.md · navy × gold × paper",
    ]
    for fragment in required_tone_fragments:
        assert fragment in text, fragment

    assert 'class="' not in text
    assert "<svg" not in text
    assert text.count("data-flow-node") >= 24
    assert text.count("data-compound-pattern") >= 4


def test_news_grasp_e2e_skill_requires_html_report_after_e2e() -> None:
    """E2E実施後に障害レポート準拠HTMLを作る運用をskillへ固定する。"""
    skill = (
        Path.home() / ".codex" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"
    ).read_text(encoding="utf-8")

    required_fragments = [
        "HTML E2E report",
        "News-Grasp incident report tone",
        "report-news-grasp-incident",
        "BUG_REPORT_DESIGN.md",
        "single HTML",
        "Workflow Map",
        "Fault boundary",
        "data-compound-pattern",
        "E2E execution is not report-complete until this HTML report exists",
        "passes `tools/validate_incident_report_design.py`",
        "desktop and one mobile render check",
        "no horizontal overflow",
        "historical failure matrix",
        "public URL returns HTTP 200",
        "report-specific sentinel text",
    ]
    for fragment in required_fragments:
        assert fragment in skill, fragment


def test_news_grasp_e2e_skill_forbids_single_issue_compound_red_and_partial_thumb_gate() -> None:
    """E2Eで露呈した複合Redと部分thumbの抜け道をskill正本へ固定する。"""
    skill = (
        Path.home() / ".codex" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"
    ).read_text(encoding="utf-8")

    for fragment in [
        "全deterministic handlerを同一再検証前に各一回",
        "各recordのthumbを個別に検証",
        "followup-review-evidence-patch",
        "repair-plan",
    ]:
        assert fragment in skill, fragment


def test_news_grasp_e2e_skill_requires_machine_checked_red_suite_coverage() -> None:
    """単一RED禁止を自由記述で終わらせず、網羅matrixの実consumerへ接続する。"""
    skill = (
        Path.home() / ".codex" / "skills" / "news-grasp-e2e-discipline" / "SKILL.md"
    ).read_text(encoding="utf-8")

    for fragment in [
        "RED_SUITE_COVERAGE_V1",
        "normal/failure/boundary/substitution/drift/replay/missing/cross_lineage/recovery/human_impact",
        "fixtures/deepdive_quality/tdd_acceptance_matrix.json",
        "tools.deepdive_red_suite_coverage",
        "Requirement × viewpoint × route",
        "90 coverage cells",
        "単一fixtureへの集約",
    ]:
        assert fragment in skill, fragment


def test_20260628_e2e_artifact_collision_has_separate_incident_report() -> None:
    """E2E成果物衝突をフルrunner障害へ混ぜて矮小化しない。"""
    text = E2E_ARTIFACT_INCIDENT_HTML.read_text(encoding="utf-8")

    required_fragments = [
        "News-Grasp · Incident Report",
        "2026-06-28 E2E成果物衝突",
        "E2E成果物衝突は本番フル runner 障害とは別障害",
        "Test-DailyArtifactsExist",
        "build/reporter-artifacts/2026-06-28",
        "本番 runner 障害とは別扱い",
        "artifact boundary",
        "same-predicate verification",
        "Production Predicate Inventory",
        "E2E output namespace",
        "Publication Pending Approval",
    ]
    for fragment in required_fragments:
        assert fragment in text, fragment

    assert "フル runner の根本原因として扱わない" in text
    assert "public URL proof は未承認" in text


def test_20260628_full_runner_report_covers_bug_classes_and_horizontal_scan() -> None:
    """フルrunner障害を復旧済みで矮小化せず、bug classと横並び調査で固定する。"""
    text = FULL_RUNNER_INCIDENT_HTML.read_text(encoding="utf-8")

    required_classes = {
        "Class A": "E2E artifact collision / operational contamination",
        "Class B": "wrapper completion vs child process termination",
        "Class C": "repair matrix / handler / artifact-scope mismatch",
        "Class D": "state consistency / articles-digest reconciliation",
        "Class E": "audio script quality / repeated closing drift",
        "Class F": "internal failure fallback overclaim",
        "Class G": "current-day vs historical URL liveness responsibility",
        "Class H": "evidence observability / overbroad log handling failure",
    }
    for label, title in required_classes.items():
        assert label in text, label
        assert title in text, title

    required_fragments = [
        "bug class",
        "関連機能洗い出し",
        "横並び調査",
        "新規バグ候補",
        "全体観点での対策",
        "Invoke-CodexWrapper",
        "SuccessProbeCommand",
        "Invoke-AutonomousCompletionPolicy",
        "repair_coverage_matrix.py",
        "repair_registry.py",
        "historical_failure_scenarios.py",
        "blocked_internal_quality_gate",
        "Local Green",
        "Publication Pending Approval",
        "Public Verified",
    ]
    for fragment in required_fragments:
        assert fragment in text, fragment

    forbidden_fragments = [
        "復旧済みなので根本対策不要",
        "URL 200 なので完了",
        "local proof を public proof とみなす",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in text, fragment
