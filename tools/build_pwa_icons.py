#!/usr/bin/env python3
"""PWA マニフェスト用アイコンを `docs/assets/favicon-512.png` から派生生成する。

生成物:
    - docs/assets/icons/icon-192.png         (any purpose)
    - docs/assets/icons/icon-512.png         (any purpose、512 を流用)
    - docs/assets/icons/icon-maskable-512.png (maskable purpose、safe-zone 80%)

`favicon-512.png` の N→ ロゴは中央に十分余白を持つ前提で、maskable は 12.5%
ずつ周囲に navy パディングを追加して safe-zone 80% を担保する。
512px → 720px 相当のキャンバスに 512px ロゴを内接させ、最終 512px に縮小する。

要 Pillow (venv にのみ install。再生成は稀なので pyproject 本体には入れない)。
実行: `python tools/build_pwa_icons.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "assets" / "favicon-512.png"
OUT_DIR = ROOT / "docs" / "assets" / "icons"

# DESIGN.md の navy (#181C2A) をパディング色に使う
NAVY = (24, 28, 42, 255)


def build() -> None:
    if not SRC.exists():
        sys.exit(f"[build_pwa_icons] 入力が見つかりません: {SRC}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    src = Image.open(SRC).convert("RGBA")
    if src.size != (512, 512):
        print(f"[build_pwa_icons] 注意: 入力サイズが 512x512 ではありません ({src.size})")

    # icon-192: 単純縮小
    icon_192 = src.resize((192, 192), Image.LANCZOS)
    (OUT_DIR / "icon-192.png").write_bytes(b"")
    icon_192.save(OUT_DIR / "icon-192.png", "PNG", optimize=True)

    # icon-512: 元画像をそのままコピー保存
    src.save(OUT_DIR / "icon-512.png", "PNG", optimize=True)

    # icon-maskable-512: safe-zone 80% を確保するため周囲に navy パディング
    canvas = Image.new("RGBA", (512, 512), NAVY)
    inner = src.resize((410, 410), Image.LANCZOS)  # 410/512 ≒ 80%
    canvas.paste(inner, ((512 - 410) // 2, (512 - 410) // 2), inner)
    canvas.save(OUT_DIR / "icon-maskable-512.png", "PNG", optimize=True)

    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        p = OUT_DIR / name
        print(f"  生成: {p.relative_to(ROOT)}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
