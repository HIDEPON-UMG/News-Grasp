#!/usr/bin/env python3
"""tools.validate_html_specs の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.validate_html_specs import validate_spec


def _valid_spec() -> str:
    filler = "\n".join(f"<p>line {i}</p>" for i in range(110))
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<style>
:root {{
  --color-primary: #141413;
  --color-secondary: #6A6A6A;
  --color-tertiary: #CC785C;
  --color-neutral: #F8F6F3;
  --color-surface: #FFFFFF;
  --color-border: #E8E6E3;
}}
.container {{ color: var(--color-primary); }}
.card {{ background: var(--color-surface); }}
.playground {{ background: var(--color-surface); }}
</style>
</head>
<body>
<main class="container">
<header class="spec-header"><div class="spec-eyebrow">SPEC</div></header>
<nav class="tabs" role="tablist">
<button class="tab active" data-tab="overview">概要</button>
<button class="tab" data-tab="alternatives">比較</button>
<button class="tab" data-tab="dataflow">データフロー</button>
<button class="tab" data-tab="impl">実装ステップ</button>
<button class="tab" data-tab="playground">パラメータ</button>
</nav>
<section id="overview" class="tab-panel active"><div class="card">{filler}</div></section>
<section id="alternatives" class="tab-panel"><div class="card"></div></section>
<section id="dataflow" class="tab-panel"><svg><rect fill="#A8C5E6" stroke="#6A6A6A"></rect></svg></section>
<section id="impl" class="tab-panel"><div class="card"></div></section>
<section id="playground" class="tab-panel"><div class="playground"><button id="copy-prompt">コピー</button></div></section>
</main>
<script>
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {{}}));
navigator.clipboard.writeText('x');
</script>
</body>
</html>
"""


def test_validate_spec_accepts_spec_template_shape(tmp_path: Path) -> None:
    path = tmp_path / "spec.html"
    path.write_text(_valid_spec(), encoding="utf-8")

    assert validate_spec(path) == []


def test_validate_spec_rejects_anchor_nav_panel_shape(tmp_path: Path) -> None:
    path = tmp_path / "spec.html"
    path.write_text(
        "<!doctype html><html><body><nav class=\"tabs\"><a href=\"#overview\">概要</a></nav>"
        "<section class=\"panel\" id=\"overview\"></section></body></html>\n",
        encoding="utf-8",
    )

    errors = validate_spec(path)

    assert any("SPEC.html 共通構造" in e for e in errors)
    assert any("100 行未満" in e for e in errors)


def test_validate_spec_rejects_external_assets(tmp_path: Path) -> None:
    path = tmp_path / "spec.html"
    path.write_text(_valid_spec().replace("</head>", '<script src="https://example.com/x.js"></script></head>'), encoding="utf-8")

    errors = validate_spec(path)

    assert any("外部 CDN" in e for e in errors)
