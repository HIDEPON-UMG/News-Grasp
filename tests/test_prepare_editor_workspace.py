import json
from pathlib import Path

from tools.prepare_editor_workspace import prepare_editor_workspace


def test_prepare_editor_workspace_removes_only_target_issue_date(tmp_path: Path) -> None:
    articles = tmp_path / "data" / "articles.jsonl"
    articles.parent.mkdir(parents=True)
    rows = [
        {"date": "2026-07-11", "url": "https://example.com/old"},
        {"date": "2026-07-12", "url": "https://example.com/retry"},
    ]
    articles.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    summary = tmp_path / "digest" / "Summary" / "2026-07-12.md"
    audio = tmp_path / "digest" / "Summary" / "2026-07-12-audio-script.md"
    summary.parent.mkdir(parents=True)
    summary.write_text("stale", encoding="utf-8")
    audio.write_text("stale", encoding="utf-8")

    result = prepare_editor_workspace(tmp_path, "2026-07-12")

    kept = [json.loads(line) for line in articles.read_text(encoding="utf-8").splitlines()]
    assert kept == [rows[0]]
    assert not summary.exists()
    assert not audio.exists()
    assert result["removed_article_records"] == 1
    assert result["removed_derived_files"] == 2
