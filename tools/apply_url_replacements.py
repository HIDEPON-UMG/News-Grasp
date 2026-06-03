#!/usr/bin/env python3
"""url_replacement_map.json に従い articles.jsonl + digest md + DeepDive md を一括修正する。

# 動作

- REPLACE: fake_url → replacement_url に置換 (全ファイル横断)
- DROP: articles.jsonl から該当エントリ削除、digest md の該当ブロック削除、DeepDive 参照は警告
- KEEP_AS_NOTE: fake_url の行を残し、横に「(URL未確認・出典名一般化)」マーカーを付ける

# 安全策

- dry-run (--dry) 既定 ON。`--apply` で実反映。
- バックアップを `data/articles.jsonl.bak.{timestamp}` と `digest/.bak.{timestamp}/...` に作る。
- 各ファイル変更は diff サマリを stdout に出す。

# 実行

```
./.venv/Scripts/python.exe tools/apply_url_replacements.py --dry           # dry run
./.venv/Scripts/python.exe tools/apply_url_replacements.py --apply         # 反映
```
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent

MAP_PATH = Path("c:/tmp/url_replacement_map.json")


def load_map() -> tuple[dict[str, str], set[str], set[str]]:
    """fake_url → 処置の dict を返す: (replace_map, drop_set, keep_as_note_set)"""
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    replace: dict[str, str] = {}
    drop: set[str] = set()
    keep_as_note: set[str] = set()
    for item in data:
        fu = item["fake_url"]
        dec = item["decision"]
        if dec == "REPLACE":
            replace[fu] = item["replacement_url"]
        elif dec == "DROP":
            drop.add(fu)
        elif dec == "KEEP_AS_NOTE":
            keep_as_note.add(fu)
    return replace, drop, keep_as_note


def fix_jsonl(jsonl: Path, replace: dict[str, str], drop: set[str], keep_as_note: set[str], *, apply: bool) -> tuple[int, int, int]:
    """articles.jsonl を修正。(replaced, dropped, notes) を返す。"""
    replaced = dropped = notes = 0
    out_lines: list[str] = []
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            try:
                d = json.loads(line_stripped)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            url = d.get("url", "")
            if url in drop:
                dropped += 1
                continue  # skip this article entirely
            if url in replace:
                d["url"] = replace[url]
                # url_norm も更新 (lowercase)
                if "url_norm" in d:
                    d["url_norm"] = replace[url].lower()
                replaced += 1
            elif url in keep_as_note:
                # source 名を一般化マーカーに変える
                old_source = d.get("source", "")
                d["source"] = f"{old_source}・出典URL未確認" if old_source else "出典URL未確認"
                notes += 1
            out_lines.append(json.dumps(d, ensure_ascii=False) + "\n")
    if apply:
        bak = jsonl.with_suffix(jsonl.suffix + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy(jsonl, bak)
        jsonl.write_text("".join(out_lines), encoding="utf-8")
        print(f"  jsonl backup: {bak.name}")
    return replaced, dropped, notes


def fix_md(md: Path, replace: dict[str, str], drop: set[str], keep_as_note: set[str], *, apply: bool) -> tuple[int, int, int]:
    """digest md (Markdown) を修正。

    drop: そのカードを削除 (### からそのカードの最後の行まで)。
    replace: URL を置換。
    keep_as_note: URL の右に「(URL未確認)」マーカー追加。
    """
    text = md.read_text(encoding="utf-8")
    original = text
    replaced = dropped = notes = 0

    # REPLACE は単純置換
    for fu, ru in replace.items():
        if fu in text:
            text = text.replace(fu, ru)
            replaced += 1

    # KEEP_AS_NOTE: URL の直後に (URL未確認) を追加 (重複防止のため既に追加されていなければ)
    for fu in keep_as_note:
        if fu in text and f"{fu})" + " (URL未確認" not in text:
            text = text.replace(fu, fu + " (URL未確認)", 1)
            notes += 1

    # DROP: カード ([NN] タイトル …  [元記事](fu))  全体を削除する
    # 簡易戦略: fu を含む行を見つけ、その行の前 `### [...]` から 次の `### [` または `---` または 空行2連続 までを削除
    for fu in drop:
        if fu not in text:
            continue
        lines = text.splitlines(keepends=True)
        # 該当行 idx を全部見つける
        for i, ln in enumerate(lines):
            if fu in ln:
                # 上方向に `### [` を探す
                start = i
                while start > 0 and not lines[start].lstrip().startswith("### ["):
                    start -= 1
                # 下方向に「次の ### [」または「文書終端」までスキャン
                end = i
                while end + 1 < len(lines) and not lines[end + 1].lstrip().startswith("### ["):
                    end += 1
                # `### [N] タイトル` の N が前後で連続する番号体系なら抜けても問題ない (digest 用途では番号は飾り)
                del lines[start:end + 1]
                dropped += 1
                break  # 1 fu につき 1 ブロック (md 内重複は想定せず)
        text = "".join(lines)

    if text != original and apply:
        bak = md.with_suffix(md.suffix + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy(md, bak)
        md.write_text(text, encoding="utf-8")
    return replaced, dropped, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実反映 (既定は dry-run)")
    args = ap.parse_args()

    replace, drop, keep_as_note = load_map()
    print(f"マップ読込: REPLACE={len(replace)}, DROP={len(drop)}, KEEP_AS_NOTE={len(keep_as_note)}")
    print(f"モード: {'APPLY (実反映)' if args.apply else 'DRY-RUN'}")
    print()

    # articles.jsonl
    jsonl = _PKG_ROOT / "data" / "articles.jsonl"
    print(f"→ {jsonl.relative_to(_PKG_ROOT)}")
    r, d, n = fix_jsonl(jsonl, replace, drop, keep_as_note, apply=args.apply)
    print(f"  REPLACE={r}, DROP={d}, NOTE={n}")

    # digest md 群 (FX/Summary/AI/Economy/IT/Mobility/Game/DeepDive 配下のすべて)
    digest_root = _PKG_ROOT / "digest"
    total_r = total_d = total_n = 0
    affected = 0
    for md in sorted(digest_root.rglob("*.md")):
        # archive 配下の古い静的 md は除外しない (公開対象なので)
        r, d, n = fix_md(md, replace, drop, keep_as_note, apply=args.apply)
        if r or d or n:
            print(f"→ {md.relative_to(_PKG_ROOT)}: REPLACE={r}, DROP={d}, NOTE={n}")
            total_r += r; total_d += d; total_n += n
            affected += 1
    print(f"\ndigest md 合計: {affected} ファイル影響 / REPLACE={total_r}, DROP={total_d}, NOTE={total_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
