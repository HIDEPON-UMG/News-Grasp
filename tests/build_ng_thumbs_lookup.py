#!/usr/bin/env python3
"""NG プレースホルダ画像の base64 ルックアップ (prompts/ng-thumbs-base64.md) を再生成。

assets/*.jpg を編集・差し替え・圧縮した後に本スクリプトを実行する。
ルックアップは Routine が live で base64 を呼ばずに済ませるためのもので、
中身は assets/ と byte 一致でなければならない（tests/test_ng_thumbs_lookup.py で検証）。
"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = [
    "ng-thumb-fx", "ng-thumb-ai", "ng-thumb-it", "ng-thumb-economy", "ng-thumb-game",
    "ng-thumb-common-fx", "ng-thumb-common-ai", "ng-thumb-common-it",
    "ng-thumb-common-economy", "ng-thumb-common-game",
]
out_path = ROOT / "prompts" / "ng-thumbs-base64.md"

lines = []
lines.append("# NG Placeholder Thumbnails — Base64 Data URI Lookup\n")
lines.append("\n")
lines.append(
    "Routine がメール HTML を生成する際、サムネ URL が取得できなかった記事の "
    "`<img>` タグの **`inlineImages` マップに data URI として渡す**ためのルックアップ。"
    "HTML 本文側は `<img src=\"cid:KEY\">` のみで参照する。\n"
)
lines.append("\n")
lines.append("| 表示位置 | キー | 用途 |\n")
lines.append("|---|---|---|\n")
lines.append("| FEATURED (TOP記事 568x200) | `ng-thumb-{cat_id}` | カテゴリ別キービジュアル |\n")
lines.append("| サイドサムネ (2件目以降 140x90) | `ng-thumb-common-{cat_id}` | カテゴリ別共通サムネ |\n")
lines.append("\n")
lines.append("`cat_id` は `fx` / `ai` / `it` / `economy` / `game` のいずれか。\n")
lines.append("\n")
lines.append("---\n")
lines.append("\n")
lines.append(
    "**運用ルール**: `assets/*.jpg` を更新した時のみ手動再生成する。"
    "本スクリプト (`tests/build_ng_thumbs_lookup.py`) を実行すること。"
    "Routine は本ファイルを読み込むだけで、自前で `base64` コマンドを呼ばない。\n"
)
lines.append("\n")
lines.append("---\n")
lines.append("\n")
for n in NAMES:
    b64 = base64.b64encode((ROOT / "assets" / f"{n}.jpg").read_bytes()).decode("ascii")
    lines.append(f"## {n}\n")
    lines.append("\n")
    lines.append("```\n")
    lines.append(f"data:image/jpeg;base64,{b64}\n")
    lines.append("```\n")
    lines.append("\n")

with out_path.open("w", encoding="utf-8", newline="\n") as out:
    out.writelines(lines)

print(f"regenerated: {out_path}")
print(f"size: {out_path.stat().st_size:,} bytes")
