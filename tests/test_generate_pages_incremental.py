#!/usr/bin/env python3
"""generate_pages.build_all() の mtime ベース incremental build を検証。

契約:
    - --full = True なら mtime に関係なく全件 render される
    - --full = False で初回は全件 render される (出力が存在しないので)
    - 2 回目を mtime を触らず実行すると 0 件 (再生成スキップ)
    - 入力 digest の mtime を未来にずらすと、その digest だけ再生成される
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import build_all  # noqa: E402


def _write_digest(root: Path, cat: str, date: str) -> Path:
    digest_dir = root / "digest" / cat.upper()
    digest_dir.mkdir(parents=True, exist_ok=True)
    path = digest_dir / f"{date}-{cat.upper()}.md"
    path.write_text(
        f"""---
title: "News Grasp #{date.replace('-', '')} — {cat.title()}"
date: {date}
issue: {date.replace('-', '')}
weekday: 水
category: {cat.title()}
categoryId: {cat}
---

# {cat.upper()}

> [!summary]
> incremental build テスト用サマリ ({cat}).

---

### [80] テスト記事

📅 {date} · 📰 Test · 🔗 [元記事](https://example.com)

#cat/{cat} #score/中

- bullet 1
- bullet 2
""",
        encoding="utf-8",
    )
    return path


def _out_for(docs: Path, src: Path) -> Path:
    """フィクスチャの {date}-{CAT}.md と digest/{CAT}/ の組から出力パスを決める。"""
    cat = src.parent.name.lower()
    date_str = "-".join(src.stem.split("-")[:3])
    return docs / cat / date_str / "index.html"


@pytest.fixture
def two_digests(tmp_path):
    """2 つの digest を tmp に作って (root, docs_root, [path1, path2]) を返す。"""
    docs = tmp_path / "docs"
    sources = [
        _write_digest(tmp_path, "fx", "2026-05-20"),
        _write_digest(tmp_path, "ai", "2026-05-20"),
    ]
    return tmp_path, docs, sources


def test_initial_full_build_writes_all(two_digests):
    _, docs, sources = two_digests
    written = build_all(full=True, docs_root=docs, digests=sources)
    assert len(written) == 2, f"expected 2 pages on initial --full, got {len(written)}"
    for p in written:
        assert p.exists() and p.stat().st_size > 0


def test_second_run_without_full_skips_all(two_digests):
    """同じ source / 同じ mtime で 2 回目を full=False で呼ぶと 0 件再生成。"""
    _, docs, sources = two_digests
    build_all(full=True, docs_root=docs, digests=sources)

    # 出力側の mtime を入力より未来にずらして up-to-date 状態にする。
    future = time.time() + 5
    for src in sources:
        os.utime(_out_for(docs, src), (future, future))

    written = build_all(full=False, docs_root=docs, digests=sources)
    assert written == [], f"expected no rebuild on unchanged mtime, got {written}"


def test_touched_source_triggers_rebuild(two_digests):
    """1 件だけ digest の mtime を未来にずらすと、その 1 件だけ再生成される。"""
    _, docs, sources = two_digests
    build_all(full=True, docs_root=docs, digests=sources)

    # 出力側の mtime を入力より未来にずらし、両方とも up-to-date 状態に揃える。
    future = time.time() + 10
    for src in sources:
        os.utime(_out_for(docs, src), (future, future))

    # 一方の digest の mtime をさらに先にずらして "更新" 扱いにする。
    touched = sources[0]
    further_future = future + 10
    os.utime(touched, (further_future, further_future))

    written = build_all(full=False, docs_root=docs, digests=sources)
    assert len(written) == 1, f"expected 1 rebuild after touch, got {len(written)}"
    expected_out = _out_for(docs, touched)
    assert written[0] == expected_out, (
        f"rebuilt path {written[0]} should equal {expected_out}"
    )


def test_full_flag_overrides_mtime(two_digests):
    """--full=True なら mtime 関係なく全件再生成。"""
    _, docs, sources = two_digests
    build_all(full=True, docs_root=docs, digests=sources)

    # 全部 up-to-date 状態に
    future = time.time() + 10
    for src in sources:
        os.utime(_out_for(docs, src), (future, future))

    # incremental では 0 件
    assert build_all(full=False, docs_root=docs, digests=sources) == []
    # --full なら全件
    assert len(build_all(full=True, docs_root=docs, digests=sources)) == 2
