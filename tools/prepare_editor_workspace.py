"""編集長が再構成する号日限定の derived artifact を決定論的に初期化する。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def prepare_editor_workspace(repo_root: Path, issue_date: str) -> dict[str, int]:
    articles_path = repo_root / "data" / "articles.jsonl"
    removed_records = 0
    if articles_path.exists():
        kept: list[str] = []
        for raw in articles_path.read_text(encoding="utf-8-sig").splitlines():
            if not raw.strip():
                continue
            record = json.loads(raw)
            if str(record.get("date") or "") == issue_date:
                removed_records += 1
            else:
                kept.append(json.dumps(record, ensure_ascii=False))
        articles_path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8", newline="\n")

    removed_files = 0
    for name in (f"{issue_date}.md", f"{issue_date}-audio-script.md"):
        path = repo_root / "digest" / "Summary" / name
        if path.exists():
            path.unlink()
            removed_files += 1
    return {
        "removed_article_records": removed_records,
        "removed_derived_files": removed_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    result = prepare_editor_workspace(args.repo_root.resolve(), args.date)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
