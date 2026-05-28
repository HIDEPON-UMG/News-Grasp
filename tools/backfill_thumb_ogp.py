#!/usr/bin/env python3
"""thumb キーが欠落している記事行に、OGP 再取得で thumb を補完する保守ユーティリティ。

2026-05-17 の append 不整合で thumb キーごと落ちた 20 行 (date=2026-05-17) を対象に、
各 URL を tools/fetch_ogp.py に通し、実 OGP / Twitter Card 画像 URL が取れれば格納、
取れなければ null を格納する (routine-system.md 3-B Stage 1 と同じ方針)。

安全設計 (tools/repair_articles_nul.py と同型):
- read_bytes → 行分割 → 対象行だけ文字列挿入 → write_bytes。全行 re-dump はしない
  (既存行は標準 json.dumps と byte 一致しない表記のため、再 dump すると表記揺れを壊す)。
- 各対象行は ``, "entities":`` をユニークアンカーに ``"thumb": <val>`` を summary の後・
  entities の前へ挿入する。挿入後に「thumb を除去すると元行へ byte 完全一致」を assert し、
  thumb 挿入以外の差分が 1 バイトも無いことを保証する。
- articles.jsonl は UTF-8 BOM 付き。BOM (line 1) を含む健全な行は一切変更しない。

実行:
    python tools/backfill_thumb_ogp.py            # OGP 取得して補完を適用
    python tools/backfill_thumb_ogp.py --dry-run  # 取得して内容プレビュー (書き込まない)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSONL = ROOT / "data" / "articles.jsonl"
CUTOFF_DATE = "2026-05-11"

# 同じ tools/ にある fetch_ogp を import する
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_ogp import fetch_ogp  # noqa: E402

ANCHOR = ', "entities":'


def _build_inserted_line(decoded: str, thumb_val: str | None) -> str:
    """decoded 行に thumb を挿入した新しい行文字列を返す。挿入不能なら ValueError。"""
    if decoded.count('"entities"') != 1 or ANCHOR not in decoded:
        raise ValueError("anchor ', \"entities\":' が一意に見つからない")
    if '"thumb"' in decoded:
        raise ValueError("既に thumb キーが存在する")
    insert = f', "thumb": {json.dumps(thumb_val, ensure_ascii=False)}'
    new_decoded = decoded.replace(ANCHOR, insert + ANCHOR, 1)
    # 安全保証: thumb 挿入を取り除くと元行へ byte 完全一致すること
    if new_decoded.replace(insert, "", 1) != decoded:
        raise ValueError("thumb 挿入以外の差分が発生 (byte 不一致); 中断")
    # 念のため valid JSON かつ summary の直後に thumb が来ること
    rec = json.loads(new_decoded)
    keys = list(rec.keys())
    if "summary" in keys and keys[keys.index("summary") + 1] != "thumb":
        raise ValueError("thumb の挿入位置が summary の直後でない")
    return new_decoded


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    dry_run = "--dry-run" in sys.argv
    data = JSONL.read_bytes()
    lines = data.split(b"\n")

    # 対象行 (date >= cutoff かつ thumb キー欠落) を特定
    targets: list[int] = []
    for idx, raw in enumerate(lines):
        if not raw.strip():
            continue
        if b"\x00" in raw[:10]:  # NUL 破損行は別ユーティリティの管轄。触らない
            continue
        rec = json.loads(raw.decode("utf-8-sig"))
        if rec.get("date", "") >= CUTOFF_DATE and "thumb" not in rec:
            targets.append(idx)

    if not targets:
        print("補完対象なし (thumb 欠落行は 0 件)")
        return 0

    print(f"補完対象: {len(targets)} 行 (L{targets[0] + 1}〜L{targets[-1] + 1})")

    inserted_bytes = 0
    n_url = 0
    n_null = 0
    for idx in targets:
        raw = lines[idx]
        decoded = raw.decode("utf-8-sig")  # 対象行は BOM なし (line1 のみ BOM)
        rec = json.loads(decoded)
        url = rec.get("url", "")
        res = fetch_ogp(url)
        thumb_val = res.get("og_image") or res.get("twitter_image") or None
        if thumb_val:
            n_url += 1
        else:
            n_null += 1
        try:
            new_decoded = _build_inserted_line(decoded, thumb_val)
        except ValueError as e:
            print(f"L{idx + 1}: 挿入失敗 ({e}); 全体を中断")
            return 1
        new_raw = new_decoded.encode("utf-8")
        inserted_bytes += len(new_raw) - len(raw)
        lines[idx] = new_raw
        title = (rec.get("title") or "")[:36]
        print(
            f"L{idx + 1} {rec.get('genre'):<14} status={res.get('status'):<12} "
            f"thumb={'URL' if thumb_val else 'null':<4} {title!r}"
        )

    out = b"\n".join(lines)
    # NUL 修復ユーティリティと同様、想定外の差分が無いことを最終保証
    if len(out) - len(data) != inserted_bytes:
        print(
            f"byte-diff mismatch (data={len(data)} out={len(out)} "
            f"inserted={inserted_bytes}); 中断"
        )
        return 1

    print(f"\n内訳: 実URL {n_url} 件 / null {n_null} 件 / 計 {len(targets)} 件")
    if dry_run:
        print(f"[dry-run] 書き込みなし (挿入バイト数 {inserted_bytes})")
        return 0

    JSONL.write_bytes(out)
    print(f"補完完了: {len(targets)} 行に thumb 挿入 (挿入 {inserted_bytes} バイト)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
