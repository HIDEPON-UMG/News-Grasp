#!/usr/bin/env python3
"""PWA マニフェスト用アイコンを `docs/assets/favicon-512.png` から派生生成する。

生成物:
    - docs/assets/icons/icon-192.png          (any purpose)
    - docs/assets/icons/icon-512.png          (any purpose、512 を流用)
    - docs/assets/icons/icon-maskable-512.png (maskable purpose、safe-zone 80%)
    - docs/assets/favicon-256.png             (ブラウザタブ、256px)
    - docs/assets/apple-touch-icon.png        (iOS ホーム画面、256px)

`favicon-512.png` を単一ソースに上記すべてを派生させる。maskable はロゴを
内側 80% (410/512) に収めて周囲を黒で埋め、OS のマスク切り抜き (円・角丸) で
ロゴが欠けないようにする。アイコン地色が黒なのでパディングも黒で地続きにする。

要 Pillow (venv にのみ install。再生成は稀なので pyproject 本体には入れない)。
実行: `python tools/build_pwa_icons.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "assets" / "favicon-512.png"
ASSETS_DIR = ROOT / "docs" / "assets"
OUT_DIR = ASSETS_DIR / "icons"

# maskable のパディング色。アイコン地色が黒なので黒で地続きにする。
MASK_BG = (0, 0, 0, 255)


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

    # icon-maskable-512: safe-zone 80% を確保するため周囲に黒パディング
    canvas = Image.new("RGBA", (512, 512), MASK_BG)
    inner = src.resize((410, 410), Image.LANCZOS)  # 410/512 ≒ 80%
    canvas.paste(inner, ((512 - 410) // 2, (512 - 410) // 2), inner)
    canvas.save(OUT_DIR / "icon-maskable-512.png", "PNG", optimize=True)

    # favicon-256 / apple-touch-icon: 同一ソースから 256px へ単純縮小
    favicon_256 = src.resize((256, 256), Image.LANCZOS)
    favicon_256.save(ASSETS_DIR / "favicon-256.png", "PNG", optimize=True)
    favicon_256.save(ASSETS_DIR / "apple-touch-icon.png", "PNG", optimize=True)

    for p in (
        OUT_DIR / "icon-192.png",
        OUT_DIR / "icon-512.png",
        OUT_DIR / "icon-maskable-512.png",
        ASSETS_DIR / "favicon-256.png",
        ASSETS_DIR / "apple-touch-icon.png",
    ):
        print(f"  生成: {p.relative_to(ROOT)}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
