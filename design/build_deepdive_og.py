"""News Grasp DeepDive 専用 OGP サムネイル生成スクリプト。

DeepDive (週次 TODAY'S THEME) 記事の og:image フォールバック画像を 1 枚生成する。
日次記事のカテゴリ別キービジュアル (docs/assets/og/{category}.jpg) と同じ
「上下バー + 中央キービジュアル」構造を、DeepDive のデザイントークン
(紙地 PAPER + 金 GOLD + 墨 INK + ❖ モチーフ / Georgia・游明朝のセリフ) で表現する。

DeepDive はすべての号でこの 1 枚を共有する (= 既存カテゴリ画像と同じ静的方式)。
個別記事で差し替えたい場合は frontmatter に `og_image:` を書けば render_deepdive
側がそちらを優先する (resolve は render_deepdive.build_deepdive_context)。

実行: python design/build_deepdive_og.py
出力: News-Grasp/docs/assets/og/deepdive.jpg  (1200x630, OGP 標準比 1.91:1)
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# 出力先 = GitHub Pages 公開元 (Obsidian ボルト内の live repo の docs/)。
# build_ng_placeholders.py は OneDrive 旧コピーを指していたが、git remote
# (HIDEPON-UMG/News-Grasp) が紐づく live repo はこちら。
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_REPO, "docs", "assets", "og")
OUT_PATH = os.path.join(OUT_DIR, "deepdive.jpg")

W, H = 1200, 630

# DeepDive デザイントークン (render_deepdive.py の DD と同値)
INK = (0x1A, 0x1A, 0x1A)
GOLD = (0xC9, 0xA1, 0x55)
CREAM = (0xF0, 0xEB, 0xE0)
DIM = (0x5C, 0x5A, 0x52)
SOFT = (0x8B, 0x8B, 0x85)
PAPER = (0xFA, 0xF7, 0xF0)
BORDER = (0xE2, 0xDE, 0xD4)

BAR_TOP_H = 68
BAR_BOT_H = 64


def find_font(candidates: list[str], size: int):
    """候補から最初に見つかった TTF/TTC を返す。なければデフォルト。"""
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _diamond(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """中心 (cx,cy)・半径 r の菱形 (45度回転した正方形) 頂点列。"""
    return [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]


def draw_tracked_text(draw, xy, text, font, fill, tracking):
    """letter-spacing (字間 tracking px) 付きで 1 文字ずつ描画し、総幅を返す。"""
    x, y = xy
    total = 0.0
    widths = []
    for ch in text:
        bbox = draw.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        widths.append((ch, w, bbox[0]))
        total += w + tracking
    total -= tracking
    for ch, w, ox in widths:
        draw.text((x - ox, y), ch, font=font, fill=fill)
        x += w + tracking
    return total


def tracked_width(draw, text, font, tracking) -> float:
    total = 0.0
    for ch in text:
        bbox = draw.textbbox((0, 0), ch, font=font)
        total += (bbox[2] - bbox[0]) + tracking
    return total - tracking if text else 0.0


def build() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)

    img = Image.new("RGB", (W, H), PAPER)

    # ── 中央領域の淡い装飾 (斜めヘアライン + 大きな ❖ ウォーターマーク) ──
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # 斜めヘアライン (金・極淡)。上下バーの内側だけに敷く。
    for x in range(-H, W, 46):
        od.line([(x, BAR_TOP_H), (x + (H - BAR_TOP_H - BAR_BOT_H), H - BAR_BOT_H)],
                fill=(*GOLD, 12), width=1)
    cx, cy = W / 2, (BAR_TOP_H + (H - BAR_BOT_H)) / 2
    # ❖ ウォーターマーク = 大菱形 + 中央小菱形 (金・極淡)
    od.polygon(_diamond(cx, cy, 232), outline=(*GOLD, 30), width=3)
    od.polygon(_diamond(cx, cy, 150), outline=(*GOLD, 22), width=2)
    od.polygon(_diamond(cx, cy, 60), fill=(*GOLD, 16))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)

    # ── フォント ──
    f_dive = find_font([r"C:\Windows\Fonts\georgiab.ttf",
                        r"C:\Windows\Fonts\timesbd.ttf"], 150)
    f_jp = find_font([r"C:\Windows\Fonts\yumindb.ttf",
                      r"C:\Windows\Fonts\yumin.ttf",
                      r"C:\Windows\Fonts\msmincho.ttc"], 46)
    f_mono = find_font([r"C:\Windows\Fonts\consola.ttf"], 17)
    f_mono_bar = find_font([r"C:\Windows\Fonts\consolab.ttf",
                            r"C:\Windows\Fonts\consola.ttf"], 16)

    # ── 上バー (墨地・クリーム/金のモノラベル) ──
    draw.rectangle([0, 0, W, BAR_TOP_H], fill=INK)
    draw.text((40, BAR_TOP_H / 2 - 10), "TODAY'S THEME  /  DEEP DIVE",
              font=f_mono, fill=CREAM)
    # 上バー左頭の ❖ (金)
    od2 = ImageDraw.Draw(img)
    od2.polygon(_diamond(28, BAR_TOP_H / 2, 7), fill=GOLD)
    right = "NEWS GRASP"
    rb = draw.textbbox((0, 0), right, font=f_mono)
    rw = rb[2] - rb[0]
    draw.text((W - 40 - 22 - rw, BAR_TOP_H / 2 - 10), right, font=f_mono, fill=CREAM)
    od2.polygon(_diamond(W - 40 - 8, BAR_TOP_H / 2, 7), fill=GOLD)

    # ── 中央キービジュアル ──
    # "DEEP DIVE" (Georgia Bold・墨・字間広め)
    title = "DEEP DIVE"
    tw = tracked_width(draw, title, f_dive, 10)
    th = draw.textbbox((0, 0), "DEEP", font=f_dive)
    title_h = th[3] - th[1]
    ty = cy - 110
    draw_tracked_text(draw, (cx - tw / 2, ty), title, f_dive, INK, 10)

    # 金の罫線
    rule_y = ty + title_h + 54
    draw.rectangle([cx - 150, rule_y, cx + 150, rule_y + 3], fill=GOLD)
    # 罫線中央の小菱形アクセント
    od2.polygon(_diamond(cx, rule_y + 1.5, 11), fill=PAPER)
    od2.polygon(_diamond(cx, rule_y + 1.5, 9), outline=GOLD, width=2)

    # サブタイトル「ひとつのテーマを深く」(游明朝・DIM・適度な字間)
    jp = "ひとつのテーマを深く"
    jw = tracked_width(draw, jp, f_jp, 6)
    draw_tracked_text(draw, (cx - jw / 2, rule_y + 26), jp, f_jp, DIM, 6)

    # ── 下バー (墨地・モノラベル) ──
    by0 = H - BAR_BOT_H
    draw.rectangle([0, by0, W, H], fill=INK)
    draw.text((40, by0 + BAR_BOT_H / 2 - 9),
              "NG  ·  DEEP DIVE KEY VISUAL  ·  v1", font=f_mono_bar, fill=SOFT)
    rlabel = "—  ONE THEME, IN DEPTH  —"
    rb = draw.textbbox((0, 0), rlabel, font=f_mono_bar)
    draw.text((W - 40 - (rb[2] - rb[0]), by0 + BAR_BOT_H / 2 - 9),
              rlabel, font=f_mono_bar, fill=GOLD)

    img.save(OUT_PATH, quality=90, optimize=True)
    print(f"saved: {OUT_PATH}  ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    build()
