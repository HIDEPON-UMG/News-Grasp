#!/usr/bin/env python3
"""prompts/ng-thumbs-base64.md の整合性テスト。

各キーの data URI を decode して assets/*.jpg のバイト列と一致するか検証する。
不一致が出たら tests/build_ng_thumbs_lookup.py を再実行して同期する。
"""
import base64
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOKUP = ROOT / "prompts" / "ng-thumbs-base64.md"
ASSETS = ROOT / "assets"

EXPECTED = [
    "ng-thumb-fx", "ng-thumb-ai", "ng-thumb-it", "ng-thumb-economy", "ng-thumb-game",
    "ng-thumb-common-fx", "ng-thumb-common-ai", "ng-thumb-common-it",
    "ng-thumb-common-economy", "ng-thumb-common-game",
]


def parse_lookup(text: str) -> dict[str, str]:
    """Markdown から `## <name>` の直下 fenced code block を抽出。"""
    pattern = re.compile(
        r"^##\s+(ng-thumb-[\w-]+)\s*\n\s*\n```\s*\n(data:image/jpeg;base64,[A-Za-z0-9+/=]+)\s*\n```",
        re.MULTILINE,
    )
    return {m.group(1): m.group(2) for m in pattern.finditer(text)}


def main() -> int:
    if not LOOKUP.exists():
        print(f"FAIL: {LOOKUP} が存在しません")
        return 1
    text = LOOKUP.read_text(encoding="utf-8")
    found = parse_lookup(text)

    errors: list[str] = []
    for name in EXPECTED:
        if name not in found:
            errors.append(f"{name}: lookup に欠落")
            continue
        prefix = "data:image/jpeg;base64,"
        uri = found[name]
        if not uri.startswith(prefix):
            errors.append(f"{name}: data URI prefix 不正")
            continue
        decoded = base64.b64decode(uri[len(prefix):])
        expected_bytes = (ASSETS / f"{name}.jpg").read_bytes()
        if decoded != expected_bytes:
            errors.append(
                f"{name}: バイト不一致 "
                f"(decoded={len(decoded)} bytes, asset={len(expected_bytes)} bytes)"
            )
            continue
        if not (decoded.startswith(b"\xff\xd8") and decoded.endswith(b"\xff\xd9")):
            errors.append(f"{name}: JPEG マジックナンバー不正")
            continue

    extra = set(found.keys()) - set(EXPECTED)
    for name in sorted(extra):
        errors.append(f"{name}: 想定外のキーが lookup に存在")

    if errors:
        print("FAIL:")
        for e in errors:
            print(f"  - {e}")
        print(f"\n→ 修復: cd {ROOT} && python tests/build_ng_thumbs_lookup.py")
        return 1

    print(f"PASS: 全 {len(EXPECTED)} 個のサムネ data URI が assets/ と一致")
    print(f"  lookup file:  {LOOKUP.relative_to(ROOT)}")
    print(f"  total size:   {sum(len(v) for v in found.values()):,} chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
