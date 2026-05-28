"""News Grasp 用 NG ロゴ サムネイルプレースホルダ生成スクリプト。

OGP 画像が取得できなかった記事の代替サムネとして使う。
カテゴリごとにアクセント色を変えた 5 種を出力する。

実行: python build_ng_placeholders.py
出力: News-Grasp/assets/ng-thumb-{id}.png   (5 ファイル、600x400)
"""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFont

# 本物の repo は Obsidian ボルト内（ProjectFolders/News-Grasp/ ではない）
OUT_DIR = r"C:\Users\hidek\OneDrive\Obsidians\New's Grasp\News-Grasp\assets"
os.makedirs(OUT_DIR, exist_ok=True)

W, H = 600, 400

# カテゴリ別アクセント色（routine-system.md と完全一致）
CATEGORIES = [
    ("fx",      (0xB8, 0x86, 0x0B), "FOREIGN EXCHANGE",        "為 替"),
    ("ai",      (0x2D, 0x5B, 0xB8), "ARTIFICIAL INTELLIGENCE", "A I"),
    ("it",      (0x2E, 0x6B, 0x52), "IT  &  CONSULTING",       "I T"),
    ("economy", (0x8E, 0x2A, 0x19), "ECONOMY",                  "経 済"),
    ("game",    (0x5E, 0x3D, 0x8C), "GAMING",                   "ゲ ー ム"),
]


def find_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """候補のフォントから最初に見つかった TTF を返す。なければデフォルト。"""
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_diag_stripes(draw: ImageDraw.ImageDraw, w: int, h: int) -> None:
    """背景に淡い斜めストライプを描く。"""
    spacing = 28
    color = (255, 255, 255, 16)
    # 左上から右下方向に
    for x in range(-h, w, spacing):
        draw.line([(x, 0), (x + h, h)], fill=color, width=2)


def draw_vignette(img: Image.Image) -> Image.Image:
    """周辺を暗く落とすビネット。"""
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    cx, cy = W // 2, H // 2
    max_r = int((cx ** 2 + cy ** 2) ** 0.5)
    # 同心円で外側ほど暗く
    for r in range(max_r, max_r - 80, -2):
        alpha = max(0, int((max_r - r) / 80 * 90))
        odraw.ellipse(
            [(cx - r, cy - r), (cx + r, cy + r)],
            outline=(0, 0, 0, alpha), width=2,
        )
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def make_thumb(cat_id: str, color: tuple[int, int, int], name_en: str, name_jp: str) -> None:
    img = Image.new("RGB", (W, H), color)
    base_rgba = img.convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 斜めストライプ
    draw_diag_stripes(draw, W, H)
    img = Image.alpha_composite(base_rgba, overlay).convert("RGB")

    # ビネット
    img = draw_vignette(img)

    draw = ImageDraw.Draw(img)

    # フォント探索（Windows 標準）
    font_ng_big = find_font([
        r"C:\Windows\Fonts\segoeuib.ttf",     # Segoe UI Bold
        r"C:\Windows\Fonts\arialbd.ttf",       # Arial Bold
        r"C:\Windows\Fonts\YuGothB.ttc",       # Yu Gothic Bold
    ], 180)
    font_en = find_font([
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ], 22)
    font_jp = find_font([
        r"C:\Windows\Fonts\YuMin.ttc",         # Yu Mincho
        r"C:\Windows\Fonts\msmincho.ttc",       # MS Mincho
        r"C:\Windows\Fonts\YuGothM.ttc",        # Yu Gothic Medium
    ], 26)

    # NG ロゴ（中央）
    text = "NG"
    bbox = draw.textbbox((0, 0), text, font=font_ng_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((W - tw) / 2 - bbox[0], (H - th) / 2 - bbox[1] - 30),
        text, fill=(255, 255, 255), font=font_ng_big,
    )

    # 区切り線
    draw.line([(W / 2 - 60, H / 2 + 70), (W / 2 + 60, H / 2 + 70)],
              fill=(255, 255, 255, 200), width=2)

    # 英名
    bbox = draw.textbbox((0, 0), name_en, font=font_en)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2 - bbox[0], H / 2 + 90), name_en,
              fill=(255, 255, 255, 230), font=font_en)

    # 日本語名
    bbox = draw.textbbox((0, 0), name_jp, font=font_jp)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) / 2 - bbox[0], H / 2 + 130), name_jp,
              fill=(255, 255, 255, 200), font=font_jp)

    # 角ラベル（上左に NEWS GRASP）
    label_font = find_font([r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf"], 14)
    draw.text((24, 22), "NEWS GRASP", fill=(255, 255, 255, 200), font=label_font)
    draw.text((24, 46), "—————————————", fill=(255, 255, 255, 120), font=label_font)
    # 角ラベル（下右に カテゴリ ID）
    bbox = draw.textbbox((0, 0), f"// {cat_id.upper()}", font=label_font)
    tw = bbox[2] - bbox[0]
    draw.text((W - tw - 24, H - 38), f"// {cat_id.upper()}",
              fill=(255, 255, 255, 200), font=label_font)

    out_path = os.path.join(OUT_DIR, f"ng-thumb-{cat_id}.png")
    img.save(out_path, optimize=True)
    print(f"  saved: {out_path}")


def main() -> None:
    print(f"Output dir: {OUT_DIR}")
    for cat_id, color, name_en, name_jp in CATEGORIES:
        make_thumb(cat_id, color, name_en, name_jp)
    print(f"Done. {len(CATEGORIES)} thumbnails generated.")


if __name__ == "__main__":
    main()
