#!/usr/bin/env python3
"""tools.create_spec の契約テスト。"""
from __future__ import annotations

from pathlib import Path

from tools.create_spec import build_spec
from tools.validate_html_specs import validate_spec


def test_create_spec_uses_spec_template_shape(tmp_path: Path) -> None:
    template = Path.home() / ".codex" / "templates" / "SPEC.html"
    from tools.create_spec import PRESETS

    html = build_spec(template, PRESETS["newsgrasp-gate-convergence"](), "2026-06-09")
    out = tmp_path / "spec.html"
    out.write_text(html, encoding="utf-8")

    assert 'class="spec-header"' in html
    assert 'class="tab active"' in html
    assert 'class="tab-panel active"' in html
    assert 'id="copy-prompt"' in html
    assert validate_spec(out) == []
