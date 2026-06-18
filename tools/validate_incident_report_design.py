from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


REQUIRED_TEXTS = (
    "News-Grasp · Incident Report",
    "結論",
    "Started",
    "Stopped",
    "Recovered",
    "Published",
    "どの工程で問題が起きたか",
    "問題の詳細と、なぜ起きたか",
    "問題の暫定対応内容",
    "直近改修・過去障害との関係",
    "恒久対応方針の網羅性と完璧性の担保",
    "恒久対応の実行計画",
    "STOP",
    "Fault boundary",
)

REQUIRED_TOKENS = (
    "#181C2A",
    "#C9A155",
    "#E6E1D5",
    "#B83A2D",
    "#3D7E60",
    "#F1F6F2",
    "#FCF2F0",
    "Noto+Sans+JP",
    "Noto+Serif+JP",
    "JetBrains+Mono",
    "word-break:break-all",
    "overflow-wrap:anywhere",
)

ALLOWED_EXTERNAL_PREFIXES = (
    "https://fonts.googleapis.com",
    "https://fonts.gstatic.com",
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...]


def _body(html: str) -> str:
    match = re.search(r"<body\b[^>]*>(?P<body>.*)</body>", html, re.IGNORECASE | re.DOTALL)
    return match.group("body") if match else html


def _external_urls(html: str) -> list[str]:
    return re.findall(r"""(?:src|href)=["'](https?://[^"']+)["']""", html, flags=re.IGNORECASE)


def validate_report(path: Path) -> ValidationResult:
    errors: list[str] = []
    if not path.exists():
        return ValidationResult(False, (f"ファイルが存在しません: {path}",))

    html = path.read_text(encoding="utf-8")
    body = _body(html)

    for text in REQUIRED_TEXTS:
        if text not in html:
            errors.append(f"必須テキストがありません: {text}")

    for token in REQUIRED_TOKENS:
        if token not in html:
            errors.append(f"必須デザイントークンがありません: {token}")

    if "#FFFFFF" not in html and not re.search(r"#[Ff]{3}\b", html):
        errors.append("必須デザイントークンがありません: #FFFFFF または #fff")

    if re.search(r"\sclass\s*=", body, flags=re.IGNORECASE):
        errors.append("body 内に class 属性があります。障害レポートはインライン style を基本にしてください。")

    if re.search(r"<script\b", html, flags=re.IGNORECASE):
        errors.append("script 要素があります。障害レポートは単一 HTML で、フォント以外の外部依存を持たせないでください。")

    if re.search(r"<img\b", html, flags=re.IGNORECASE):
        errors.append("img 要素があります。障害レポートは外部画像・画像依存を避けてください。")

    for url in _external_urls(html):
        if not url.startswith(ALLOWED_EXTERNAL_PREFIXES):
            errors.append(f"許可されていない外部 URL があります: {url}")

    if "grid-template-columns:repeat(4,1fr)" not in html:
        errors.append("KPI 4連ストリップの grid 指定がありません。")

    if re.search(r"overflow-x\s*:\s*(auto|scroll)", html, flags=re.IGNORECASE):
        errors.append("横スクロールを作る overflow-x 指定があります。")

    return ValidationResult(not errors, tuple(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="News-Grasp 障害レポートのデザイン仕様を検証します。")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()

    result = validate_report(args.report)
    if result.ok:
        print(f"OK: {args.report}")
        return 0

    for error in result.errors:
        print(f"NG: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
