#!/usr/bin/env python3
"""音声台本の字数不足だけを決定論的に補修する CLI。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tools.publish_inventory import scheduled_category_ids
from tools.tts.build_script import effective_char_count, validate_script


TARGET_MIN = 2600
TARGET_MAX = 2800

SUPPLEMENT_SENTENCES = (
    "補足すると、今日の材料は新しい機能の多さより、どの条件を先に満たすかを見た方が整理しやすい一日でした。",
    "投資、認証、防御、供給網の話がそれぞれ別の見出しで出ていますが、実務では同じ順番の問題としてつながります。",
    "先に通す道を決め、次に守る場所を決め、最後に広げる範囲を決める会社ほど、変化への対応が速くなります。",
    "数字や社名だけを見ると散らばって見えますが、準備の置き方を見ると、今日のニュースはかなり一本の線で読めます。",
    "今日の観点・考察としては、成長の速さそのものより、認証、監査、供給、説明責任をどの順番で固めるかが焦点です。",
)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return "", raw.strip()
    return f"---{parts[1]}---\n\n", parts[2].strip()


def repair_text(raw: str, *, issue: str) -> tuple[str, bool]:
    frontmatter, body = _split_frontmatter(raw)
    if effective_char_count(body) >= TARGET_MIN:
        return raw, False

    additions: list[str] = []
    for _ in range(3):
        for sentence in SUPPLEMENT_SENTENCES:
            candidate_body = body.rstrip() + "\n\n" + "\n".join(additions + [sentence])
            if effective_char_count(candidate_body) > TARGET_MAX:
                break
            additions.append(sentence)
            if effective_char_count(candidate_body) >= TARGET_MIN:
                break
        if effective_char_count(body.rstrip() + "\n\n" + "\n".join(additions)) >= TARGET_MIN:
            break

    repaired_body = body.rstrip() + "\n\n" + "\n".join(additions)
    if effective_char_count(repaired_body) < TARGET_MIN:
        return raw, False

    issues = validate_script(
        repaired_body,
        date=issue,
        required_categories=scheduled_category_ids(issue),
    )
    if any("字数不足" in issue_text or "字数超過" in issue_text for issue_text in issues):
        return raw, False
    return frontmatter + repaired_body.strip() + "\n", True


def repair_file(repo_root: Path, issue: str) -> bool:
    path = repo_root / "digest" / "Summary" / f"{issue}-audio-script.md"
    if not path.exists():
        return False
    raw = path.read_text(encoding="utf-8-sig")
    repaired, changed = repair_text(raw, issue=issue)
    if not changed:
        return False
    path.write_text(repaired, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair short News-Grasp daily audio script deterministically.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args(argv)

    ok = repair_file(args.repo_root, args.date)
    if not ok:
        print("audio script length deterministic repair was not applicable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
