"""日付アーカイブ DIGEST / DEEP DIVE トグル + 書架一本化 の契約テスト。

意図 (なぜ重要か): 旧テーマ書架 /deepdive/ を日付アーカイブ /archive/ のトグルに
一本化した。この再編で壊してはいけない不変条件を locked-in する。

  1. collect_archive_items が DEEP DIVE ビュー用の items / chips を「単一の経路」で返す
     (= 書架とアーカイブが別々の収集ロジックに分岐して二重メンテにならないこと)
  2. 旧 /deepdive/ は 404 にせず /archive/?view=deepdive へリダイレクトする
     (= 既存ブックマーク・被リンクを保護する意図)
  3. build_archive が deepdive_items を受けて トグル + DEEP DIVE ペイン + 初期選択 JS を出す
     (= DeepDive 個別記事から ?view=deepdive で遷移したとき DEEP DIVE が初期選択される
       というユーザー要望の核心を、テンプレ改修が満たし続けること)
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_pages import build_archive  # noqa: E402
from tools.render_deepdive import (  # noqa: E402
    build_deepdive_archive,
    collect_archive_items,
)

# deepdive_robotaxi.md を含む fixture ディレクトリ (test_deepdive_render と共用)
_FIXTURE_DIR = ROOT / "tests" / "fixtures"


def _sample_entries() -> list[dict]:
    """build_archive が最小で動く日付 issue ストリーム (1 日 / 1 記事 + summary)。"""
    return [
        {"date": "2026-05-30", "category_id": "ai",
         "top_title": "テスト AI 記事", "top_score": 91,
         "canonical": "https://example.com/ai/2026-05-30/"},
        {"date": "2026-05-30", "category_id": "summary",
         "summary_text": "本日の総括テキスト",
         "canonical": "https://example.com/2026-05-30/summary/"},
    ]


# ── 1. データ供給の一本化 ─────────────────────────────────────────────────────
def test_collect_archive_items_returns_deepdive_schema() -> None:
    """collect_archive_items は DEEP DIVE 行に必要な全フィールドを返す。"""
    dd = collect_archive_items(digest_dir=_FIXTURE_DIR)
    assert dd["items"], "fixture の DeepDive md から item が 1 件は採れること"
    item = dd["items"][0]
    for key in ("date", "title", "url", "read_min",
                "lens_id", "lens_code", "lens_glyph", "lens_accent", "search"):
        assert key in item, f"item に必須キー {key} が無い"
    assert dd["chips"], "レンズチップが生成されること"
    assert all(c["id"] != "summary" for c in dd["chips"]), "summary 疑似カテゴリは除外"


def test_collect_archive_items_empty_without_dir(tmp_path: Path) -> None:
    """DeepDive md が無いディレクトリでは items を空で返す (落ちない)。"""
    dd = collect_archive_items(digest_dir=tmp_path / "nonexistent")
    assert dd["items"] == []
    assert dd["chips"] == []


# ── 2. 書架の 404 回避リダイレクト ───────────────────────────────────────────
def test_deepdive_index_is_redirect_not_404(tmp_path: Path) -> None:
    """旧 /deepdive/ は 404 にせず /archive/?view=deepdive へ誘導する。"""
    out = build_deepdive_archive(docs_root=tmp_path)
    assert out is not None and out.exists()
    html = out.read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in html, "meta refresh が無い"
    assert "/archive/?view=deepdive" in html, "リダイレクト先が違う"
    assert "canonical" in html, "canonical 指定が無い (SEO 保護)"


# ── 3. アーカイブのトグル + DEEP DIVE ペイン + 初期選択 ──────────────────────
def test_build_archive_emits_toggle_and_deepdive_pane(tmp_path: Path) -> None:
    """build_archive が deepdive_items を受けると、トグル・2 ペイン・初期選択 JS を出す。"""
    dd = collect_archive_items(digest_dir=_FIXTURE_DIR)
    out = build_archive(_sample_entries(), tmp_path,
                        deepdive_items=dd["items"], lens_chips=dd["chips"])
    html = out.read_text(encoding="utf-8")
    assert 'data-view="digest"' in html and 'data-view="deepdive"' in html, "トグルが無い"
    assert 'data-view-pane="digest"' in html, "DIGEST ペインが無い"
    assert 'data-view-pane="deepdive"' in html, "DEEP DIVE ペインが無い"
    assert "view=deepdive" in html, "?view=deepdive 初期選択ロジックが無い"
    assert dd["items"][0]["title"] in html, "DEEP DIVE 行に fixture のテーマが出ていない"


def test_build_archive_places_podcast_after_deepdive_and_removes_density(tmp_path: Path) -> None:
    """日付アーカイブは DEEP DIVE 右に PODCAST を置き、DENSITY の名残を出さない。"""
    podcast_state = tmp_path.parent / "build" / "youtube-podcast"
    podcast_state.mkdir(parents=True, exist_ok=True)
    (podcast_state / "uploads.json").write_text(
        json.dumps({
            "2026-06-21": {
                "status": "public",
                "videoId": "archive-video",
                "playlistId": "archive-playlist",
            }
        }),
        encoding="utf-8",
    )
    dd = collect_archive_items(digest_dir=_FIXTURE_DIR)
    out = build_archive(_sample_entries(), tmp_path,
                        deepdive_items=dd["items"], lens_chips=dd["chips"])
    html = out.read_text(encoding="utf-8")
    assert "DENSITY" not in html
    assert "COMFORTABLE" not in html
    assert 'ng-modeswitch__btn ng-modeswitch__btn--link' in html
    assert "https://www.youtube.com/playlist?list=archive-playlist" in html
    assert "https://www.youtube.com/watch?v=archive-video" not in html
    assert html.index('data-view="deepdive"') < html.index("PODCAST")


def test_build_archive_deepdive_rows_have_filter_attrs(tmp_path: Path) -> None:
    """DEEP DIVE 行は検索/レンズ統合 (単一 apply) のための data 属性を持つ。"""
    dd = collect_archive_items(digest_dir=_FIXTURE_DIR)
    out = build_archive(_sample_entries(), tmp_path,
                        deepdive_items=dd["items"], lens_chips=dd["chips"])
    html = out.read_text(encoding="utf-8")
    assert "data-ddrow" in html, "DEEP DIVE 行マーカーが無い"
    assert "data-lens=" in html, "レンズ絞り込み属性が無い"
    assert "data-text=" in html, "検索属性が無い (digest と統一命名)"
