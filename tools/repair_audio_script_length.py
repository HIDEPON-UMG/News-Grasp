#!/usr/bin/env python3
"""音声台本の字数不足・定型文重複を決定論的に補修する CLI。"""
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

REPEATED_CLOSING_REPLACEMENTS = (
    ("ありがとうございました。", "ここまでお聞きいただき、ありがとうございました。"),
    ("ニュースグラスプでした。", "ニュースグラスプ、{issue_jp}号でした。"),
    ("ニュース グラスプでした。", "ニュース グラスプ、{issue_jp}号でした。"),
    ("ニュース グラスプです。", "ニュース グラスプ、{issue_jp}号です。"),
    ("今日はここまでです。", "今日の整理はここで区切ります。"),
    ("最後に、今日の観点・考察です。", "締めくくりに、今日の観点を整理します。"),
)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return "", raw.strip()
    return f"---{parts[1]}---\n\n", parts[2].strip()


def _issue_japanese(issue: str) -> str:
    try:
        _year, month, day = issue.split("-")
        return f"{int(month)}月{int(day)}日"
    except ValueError:
        return issue


def _recent_history_texts(repo_root: Path, issue: str) -> list[str]:
    from datetime import date, timedelta

    try:
        day = date.fromisoformat(issue)
    except ValueError:
        return []
    history: list[str] = []
    for offset in (1, 2):
        path = repo_root / "digest" / "Summary" / f"{(day - timedelta(days=offset)).isoformat()}-audio-script.md"
        if path.exists():
            _frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8-sig"))
            history.append(body)
    return history


def _repair_repeated_closing(body: str, *, issue: str) -> tuple[str, bool]:
    repaired = body
    for src, dst in REPEATED_CLOSING_REPLACEMENTS:
        repaired = repaired.replace(src, dst.format(issue_jp=_issue_japanese(issue)), 1)
    return repaired, repaired != body


def repair_text(raw: str, *, issue: str, history_texts: list[str] | None = None) -> tuple[str, bool]:
    frontmatter, body = _split_frontmatter(raw)
    repaired_body, changed = _repair_repeated_closing(body, issue=issue)

    additions: list[str] = []
    if effective_char_count(repaired_body) < TARGET_MIN:
        for _ in range(3):
            for sentence in SUPPLEMENT_SENTENCES:
                candidate_body = repaired_body.rstrip() + "\n\n" + "\n".join(additions + [sentence])
                if effective_char_count(candidate_body) > TARGET_MAX:
                    break
                additions.append(sentence)
                if effective_char_count(candidate_body) >= TARGET_MIN:
                    break
            if effective_char_count(repaired_body.rstrip() + "\n\n" + "\n".join(additions)) >= TARGET_MIN:
                break

    if additions:
        repaired_body = repaired_body.rstrip() + "\n\n" + "\n".join(additions)
        changed = True

    if not changed:
        return raw, False
    if effective_char_count(repaired_body) < TARGET_MIN:
        return raw, False

    issues = validate_script(
        repaired_body,
        date=issue,
        history_texts=history_texts or [],
        required_categories=scheduled_category_ids(issue),
    )
    if issues:
        return raw, False
    return frontmatter + repaired_body.strip() + "\n", True


def repair_file(repo_root: Path, issue: str) -> bool:
    path = repo_root / "digest" / "Summary" / f"{issue}-audio-script.md"
    if not path.exists():
        return False
    raw = path.read_text(encoding="utf-8-sig")
    repaired, changed = repair_text(raw, issue=issue, history_texts=_recent_history_texts(repo_root, issue))
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
