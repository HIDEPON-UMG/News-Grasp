#!/usr/bin/env python3
"""SPEC.html ベースで docs/specs HTML を生成する。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sys


DEFAULT_TEMPLATE = Path.home() / ".codex" / "templates" / "SPEC.html"


@dataclass(frozen=True)
class SpecContent:
    title: str
    subtitle: str
    badge: str
    overview: str
    alternatives: str
    dataflow: str
    impl: str
    playground: str


def _replace_section(html: str, section_id: str, body: str) -> str:
    pattern = re.compile(
        rf'    <section id="{re.escape(section_id)}" class="tab-panel(?: active)?">.*?(?=\n    <section id="|\n  </main>)',
        re.S,
    )
    match = pattern.search(html)
    if not match:
        raise ValueError(f"template section not found: {section_id}")
    active = " active" if section_id == "overview" else ""
    replacement = f'    <section id="{section_id}" class="tab-panel{active}">\n{body.rstrip()}\n    </section>'
    return pattern.sub(replacement, html, count=1)


def _patch_template_tokens(html: str, content: SpecContent, spec_date: str) -> str:
    html = html.replace("{{TITLE}}", content.title)
    html = html.replace('<span id="spec-date">YYYY-MM-DD</span>', f'<span id="spec-date">{spec_date}</span>')
    html = html.replace("<!-- TITLE: 仕様書のタイトル -->", content.title)
    html = html.replace("<!-- SUBTITLE: 1 行サマリ -->", content.subtitle)
    html = html.replace("<!-- 担当者など -->", content.badge)
    # SPEC.html 旧テンプレに残る直書き hover 色を CSS 変数化する。
    html = html.replace(".btn-primary:hover { background: #262625; }", ".btn-primary:hover { background: var(--color-primary); }")
    html = html.replace(".btn-accent:hover { background: #B86A50; }", ".btn-accent:hover { background: var(--color-tertiary); }")
    return html


def _newsgrasp_gate_convergence() -> SpecContent:
    overview = """      <h2>概要</h2>
      <p>2026-06-09 日次バッチ障害の再発防止仕様。目的は gate を緩めることではなく、gate 失敗が 1〜2 回で収束し、収束しない場合も朝の公開面が破綻しない構造にすること。</p>
      <div class="severity-info">
        <strong>成功条件</strong>
        <ul>
          <li>Content Gate を通った本日号だけを通常公開する。</li>
          <li>Content Gate を通らない本日号の記事コンテンツは公開・commit・pushしない。</li>
          <li>本日号が出せない場合は、直近成功号を維持し、設定に応じて notice または status file だけを出す。</li>
          <li>secret / security / 破壊的変更の疑いは自動修復せず停止する。</li>
        </ul>
      </div>
      <div class="card">
        <h3>今回の根本論点</h3>
        <p>通常 30 分程度で終わる処理が 1 時間に達し、記事の半分も作られなかった。timeout そのものではなく、収集、要約、gate 修復のどこかで同じ失敗を繰り返せる構造が疑われる。</p>
      </div>"""
    alternatives = """      <h2>比較</h2>
      <div class="compare-grid">
        <div class="card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-sm);">
            <h3 style="margin: 0;">無制限自己修復</h3>
            <span class="badge badge-error">却下</span>
          </div>
          <p>Claude が gate を見ながら全体生成を何度も繰り返す方式。失敗が収束しない場合に 1 時間 timeout まで進み、どこで詰まったかも runner が把握できない。</p>
        </div>
        <div class="card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-sm);">
            <h3 style="margin: 0;">runner 管理の bounded repair</h3>
            <span class="badge badge-success">採用</span>
          </div>
          <p>runner が失敗署名と artifact hash を記録し、同一失敗は 1 回だけ targeted repair に戻す。別 failure_signature なら段階的修復を許す余地を残す。</p>
        </div>
      </div>
      <table>
        <thead><tr><th>論点</th><th>修正前</th><th>修正後</th></tr></thead>
        <tbody>
          <tr><td>Claude の責務</td><td>生成と commit まで抱える。</td><td>生成専用。repair は runner から渡された failure report の範囲だけ。</td></tr>
          <tr><td>gate 失敗</td><td>同じ失敗を繰り返せる。</td><td><code>failure_signature</code> と artifact hash で再発を止める。</td></tr>
          <tr><td>公開保証</td><td>push 前停止で朝の公開面が更新されない。</td><td>未検証記事は出さず、直近成功号を維持する。</td></tr>
        </tbody>
      </table>"""
    dataflow = """      <h2>データフロー</h2>
      <p>Claude は一時生成領域で digest/jsonl を作る。runner が Content Gate を通し、通過した場合だけ docs 生成と commit/push へ進む。失敗時は retry budget を確認し、収束しなければ last-known-good を維持する。</p>
      <div class="fig">
        <svg viewBox="0 0 860 300" role="img" aria-label="News-Grasp daily gate flow">
          <defs>
            <marker id="ng-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto"><path d="M0 0 L9 3 L0 6 Z" fill="#6A6A6A"/></marker>
          </defs>
          <rect x="20" y="70" width="140" height="54" rx="10" fill="#A8C5E6"/>
          <text x="90" y="101" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#141413">Claude生成</text>
          <path d="M160 97 L250 97" stroke="#6A6A6A" stroke-width="2" marker-end="url(#ng-arrow)"/>
          <rect x="250" y="70" width="150" height="54" rx="10" fill="#9DD4C7"/>
          <text x="325" y="101" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#141413">Content Gate</text>
          <path d="M400 97 L500 97" stroke="#6A6A6A" stroke-width="2" marker-end="url(#ng-arrow)"/>
          <rect x="500" y="70" width="140" height="54" rx="10" fill="#F4E4C1"/>
          <text x="570" y="101" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#141413">通常公開</text>
          <path d="M325 124 L325 190" stroke="#6A6A6A" stroke-width="2" marker-end="url(#ng-arrow)"/>
          <rect x="230" y="190" width="190" height="54" rx="10" fill="#F4E4C1"/>
          <text x="325" y="212" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#141413">Retry Budget</text>
          <text x="325" y="231" text-anchor="middle" font-family="Inter, sans-serif" font-size="11" fill="#141413">same signature once</text>
          <path d="M420 217 L535 217" stroke="#6A6A6A" stroke-width="2" marker-end="url(#ng-arrow)"/>
          <rect x="535" y="190" width="190" height="54" rx="10" fill="#A8C5E6"/>
          <text x="630" y="212" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#141413">Last-known-good</text>
          <text x="630" y="231" text-anchor="middle" font-family="Inter, sans-serif" font-size="11" fill="#141413">notice/status only</text>
        </svg>
        <div class="fig-caption">未検証の本日号は fallback publish に含めない。fallback は記事更新ではなく公開状態の維持である。</div>
      </div>"""
    impl = """      <h2>実装ステップ</h2>
      <div class="timeline">
        <div class="timeline-step"><strong>1.</strong>HTML仕様書を SPEC.html 形式で作成し、承認後に実装へ進む。</div>
        <div class="timeline-step"><strong>2.</strong>gate failure contract を実装し、失敗署名と artifact hash を記録する。</div>
        <div class="timeline-step"><strong>3.</strong>runner が bounded repair と Availability Gate を管理する。</div>
      </div>
      <h3>変更ファイル</h3>
      <table>
        <thead><tr><th>パス</th><th>変更種別</th><th>意図</th></tr></thead>
        <tbody>
          <tr><td><code>tools/gate_contract.py</code></td><td>add</td><td>同一失敗の再発を runner が判断できるようにする。</td></tr>
          <tr><td><code>tools/publish_fallback.py</code></td><td>add</td><td>本日号を出せない場合に公開面を last-known-good へ維持する。</td></tr>
          <tr><td><code>prompts/runner-prompt.md</code></td><td>edit</td><td>Claude の責務を生成専用へ縮小する。</td></tr>
        </tbody>
      </table>
      <h3>判定ルール</h3>
      <pre><code>same_failure_signature_retry_limit = 1
category_attempt_limit = 2
fallback_mode = notice_keep_last_success
non_retryable = secret | security | destructive</code></pre>"""
    playground = """      <h2>パラメータ</h2>
      <p>fallback 表示方式と retry budget を調整し、承認後の実装プロンプトとして戻す。</p>
      <div class="playground">
        <div class="playground-control">
          <label for="param-1">同一失敗署名の再試行上限</label>
          <input type="range" id="param-1" min="0" max="3" value="1" step="1" data-unit="回">
          <output for="param-1">1回</output>
        </div>
        <div class="playground-control">
          <label for="param-2">カテゴリ最大試行回数</label>
          <input type="range" id="param-2" min="1" max="4" value="2" step="1" data-unit="回">
          <output for="param-2">2回</output>
        </div>
        <div style="margin-top: var(--space-lg);">
          <button class="btn btn-accent" id="copy-prompt">プロンプトとしてコピー</button>
        </div>
      </div>"""
    return SpecContent(
        title="News-Grasp Gate Convergence",
        subtitle="品質 gate を緩めず、同じ失敗の無制限再生成と朝の公開面破綻を防ぐ日次 runner 仕様。",
        badge="News-Grasp daily runner",
        overview=overview,
        alternatives=alternatives,
        dataflow=dataflow,
        impl=impl,
        playground=playground,
    )


PRESETS = {
    "newsgrasp-gate-convergence": _newsgrasp_gate_convergence,
}


def build_spec(template_path: Path, content: SpecContent, spec_date: str) -> str:
    html = template_path.read_text(encoding="utf-8-sig")
    html = _patch_template_tokens(html, content, spec_date)
    html = _replace_section(html, "overview", content.overview)
    html = _replace_section(html, "alternatives", content.alternatives)
    html = _replace_section(html, "dataflow", content.dataflow)
    html = _replace_section(html, "impl", content.impl)
    html = _replace_section(html, "playground", content.playground)
    # 動的日付で上書きされるとレビュー対象が日によって変わるため固定する。
    html = re.sub(
        r"\n    // 日付セット\n    const dateEl = document.getElementById\('spec-date'\);\n    if \(dateEl\) dateEl.textContent = new Date\(\).toISOString\(\).slice\(0, 10\);\n",
        "\n",
        html,
    )
    return html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SPEC.html ベースの HTML 仕様書を生成します。")
    parser.add_argument("--preset", choices=sorted(PRESETS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if not args.template.exists():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.force:
        print(f"output exists: {args.output} (--force required)", file=sys.stderr)
        return 2
    content = PRESETS[args.preset]()
    html = build_spec(args.template, content, args.date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
