#!/usr/bin/env python3
"""NG プレースホルダ画像の base64 ルックアップ (prompts/ng-thumbs-base64.md) を再生成。
assets/*.jpg を編集したら本スクリプトを実行する。"""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAMES = ["ng-thumb-fx","ng-thumb-ai","ng-thumb-it","ng-thumb-economy","ng-thumb-game",
         "ng-thumb-common-fx","ng-thumb-common-ai","ng-thumb-common-it","ng-thumb-common-economy","ng-thumb-common-game"]
out_path = ROOT / "prompts" / "ng-thumbs-base64.md"

with out_path.open("w", encoding="utf-8", newline="
") as out:
    out.write("# NG Placeholder Thumbnails — Base64 Data URI Lookup

")
    out.write("Routine がメール HTML を生成する際、サムネ URL が取得できなかった記事の <img src> に**この表の data URI を verbatim で貼り付ける**ためのルックアップ。

")
    out.write("| 表示位置 | キー | 用途 |
|---|---|---|
")
    out.write("| FEATURED (TOP記事 568x200) | ng-thumb-{cat_id} | カテゴリ別キービジュアル |
")
    out.write("| サイドサムネ (2件目以降 140x90) | ng-thumb-common-{cat_id} | カテゴリ別共通サムネ |

")
    out.write("cat_id は fx / ai / it / economy / game のいずれか。

---

")
    out.write("**運用ルール**: assets/*.jpg を更新した時のみ手動再生成する。本スクリプト (tests/build_ng_thumbs_lookup.py) を実行すること。Routine は本ファイルを読み込むだけで、自前で base64 コマンドを呼ばない。

---

")
    for n in NAMES:
        b64 = base64.b64encode((ROOT/"assets"/f"{n}.jpg").read_bytes()).decode("ascii")
        out.write(f"## {n}

")
        out.write("```
")
        out.write(f"data:image/jpeg;base64,{b64}
")
        out.write("```

")
print("regenerated:", out_path)
