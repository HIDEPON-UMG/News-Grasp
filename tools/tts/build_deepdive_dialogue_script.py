from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import date, timedelta
import re
from pathlib import Path

from tools.deepdive_context_pack import context_sources_for_deepdive as _shared_context_sources_for_deepdive
from tools.tts import deepdive_dialogue


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTEXT_DAYS = 30
MIN_CONTEXT_SOURCES = 2
MAX_CONTEXT_SOURCES = 4
RELATION_KINDS = ("続報", "主役共有", "波及", "対比")
GENERIC_TAGS = {"deepdive", "daily", "weekly", "news-grasp"}
LOW_SIGNAL_CONTEXT_TERMS = {"ai"}
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_RELATED_RE = re.compile(r"```related\s*(.*?)```", re.DOTALL)
_TERM_RE = re.compile(r"[A-Za-z0-9]+|[一-龯ぁ-んァ-ンー]{2,}")


@dataclass(frozen=True)
class ContextSource:
    date: str
    title: str
    relation: str
    link: str
    change: str
    summary: str
    score: int


def _frontmatter_value(markdown: str, key: str, default: str) -> str:
    frontmatter = _frontmatter_map(markdown)
    return frontmatter.get(key, default) or default


def _frontmatter_map(markdown: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return {}
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _frontmatter_tags(markdown: str) -> list[str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return []
    for line in match.group(1).splitlines():
        if not line.startswith("tags:"):
            continue
        raw = line.split(":", 1)[1].strip()
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            return [part.strip().strip('"').strip("'") for part in raw.strip("[]").split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _body_sentences(markdown: str, *, limit: int = 12) -> list[str]:
    body = _FRONTMATTER_RE.sub("", markdown)
    body = _CODE_FENCE_RE.sub("", body)
    body = _HEADING_RE.sub("", body)
    body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
    body = body.replace("__", "").replace("**", "")
    candidates = re.split(r"(?<=[。！？])\s*", body)
    sentences: list[str] = []
    for sentence in candidates:
        cleaned = re.sub(r"\s+", "", sentence).strip()
        if len(cleaned) >= 24:
            sentences.append(cleaned)
        if len(sentences) >= limit:
            break
    return sentences


def _clip(text: str, limit: int = 160) -> str:
    cleaned = re.sub(r"\s+", "", text).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned if cleaned.endswith(("。", "！", "？")) else cleaned + "。"
    cut = cleaned[:limit]
    boundary = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if boundary >= 32:
        clipped = cut[: boundary + 1]
    else:
        clipped = cleaned if len(cleaned) <= limit * 2 else cut.rstrip("、。！？") + "。"
    return clipped if clipped.endswith(("。", "！", "？")) else clipped + "。"


def _date_from_source_name(source_name: str, fallback: str) -> date:
    match = re.search(r"\d{4}-\d{2}-\d{2}", source_name)
    value = match.group(0) if match else fallback
    return date.fromisoformat(value)


def _tag_terms(tags: list[str]) -> set[str]:
    terms: set[str] = set()
    for tag in tags:
        folded = tag.strip().casefold()
        if not folded or folded in GENERIC_TAGS or folded.startswith("issue-"):
            continue
        terms.add(folded)
        terms.update(term.casefold() for term in _TERM_RE.findall(tag) if len(term) >= 2)
    return terms


def _title_terms(title: str) -> set[str]:
    return {term.casefold() for term in _TERM_RE.findall(title) if len(term) >= 2}


def _related_by_date(markdown: str) -> dict[str, dict[str, str]]:
    related: dict[str, dict[str, str]] = {}
    for match in _RELATED_RE.finditer(markdown):
        raw = match.group(1).strip()
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if not isinstance(item, dict):
                continue
            item_date = str(item.get("date") or "").strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item_date):
                continue
            related[item_date] = {str(key): str(value) for key, value in item.items()}
    return related


def _archive_dir_for_source(source_name: str, archive_dir: Path | None) -> Path:
    if archive_dir is not None:
        return archive_dir
    source_path = Path(source_name)
    if not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    if source_path.parent.exists():
        return source_path.parent
    return REPO_ROOT / "digest" / "DeepDive"


def _context_relation(*, related: dict[str, str] | None, tag_overlap: set[str], title_overlap: set[str]) -> str:
    if related:
        relation = str(related.get("relation") or "").strip()
        if relation in RELATION_KINDS:
            return relation
    if tag_overlap:
        return "主役共有"
    if title_overlap:
        return "続報"
    return "対比"


def _context_change(
    *,
    related: dict[str, str] | None,
    context_title: str,
    current_title: str,
    relation: str,
) -> str:
    if related and str(related.get("change") or "").strip():
        return str(related["change"]).strip()
    if relation == "主役共有":
        return f"前回は「{context_title}」として同じ主役の論点を扱い、今回は「{current_title}」として業務への埋め込み方に焦点が移った。"
    if relation == "続報":
        return f"前回の論点を受けて、今回は「{current_title}」で新しい変化点を読む段階に進んだ。"
    if relation == "波及":
        return f"前回の構造が今回の「{current_title}」へ波及し、実務上の確認点が広がった。"
    return f"前回の「{context_title}」と今回の「{current_title}」を対比すると、同じAI導入でも価値の出方が変わっている。"


def _context_signal_terms(tag_overlap: set[str], title_overlap: set[str]) -> set[str]:
    return {term for term in (tag_overlap | title_overlap) if term.casefold() not in LOW_SIGNAL_CONTEXT_TERMS}


def _is_relevant_context(*, related: dict[str, str] | None, tag_overlap: set[str], title_overlap: set[str]) -> bool:
    if related:
        return True
    return bool(_context_signal_terms(tag_overlap, title_overlap))


def _load_context_sources(
    source_markdown: str,
    *,
    source_name: str,
    archive_dir: Path | None,
    context_days: int,
) -> list[ContextSource]:
    source_title = _frontmatter_value(source_markdown, "title", "DeepDive")
    source_date = _date_from_source_name(source_name, _frontmatter_value(source_markdown, "date", "1970-01-01"))
    source_terms = _tag_terms(_frontmatter_tags(source_markdown)) | _title_terms(source_title)
    related_map = _related_by_date(source_markdown)
    resolved_archive_dir = _archive_dir_for_source(source_name, archive_dir)
    source_filename = Path(source_name).name
    candidates: list[ContextSource] = []
    for path in sorted(resolved_archive_dir.glob("*-DeepDive.md")):
        if path.name == source_filename:
            continue
        try:
            item_date = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if not (source_date - timedelta(days=context_days) <= item_date < source_date):
            continue
        text = path.read_text(encoding="utf-8-sig")
        title = _frontmatter_value(text, "title", path.stem)
        item_terms = _tag_terms(_frontmatter_tags(text)) | _title_terms(title)
        tag_overlap = source_terms & item_terms
        title_overlap = _title_terms(source_title) & _title_terms(title)
        related = related_map.get(item_date.isoformat())
        if not _is_relevant_context(related=related, tag_overlap=tag_overlap, title_overlap=title_overlap):
            continue
        signal_terms = _context_signal_terms(tag_overlap, title_overlap)
        score = len(signal_terms) * 3 + (3 if title_overlap else 0)
        if related:
            score += 20
        summary = _clip((_body_sentences(text, limit=1) or [title])[0], 78)
        relation = _context_relation(related=related, tag_overlap=tag_overlap, title_overlap=title_overlap)
        context = ContextSource(
            date=item_date.isoformat(),
            title=title,
            relation=relation,
            link=str((related or {}).get("link") or ", ".join(sorted(tag_overlap)[:3]) or "過去DeepDiveとの構造比較"),
            change=_context_change(
                related=related,
                context_title=title,
                current_title=source_title,
                relation=relation,
            ),
            summary=summary,
            score=score,
        )
        candidates.append(context)
    candidates.sort(key=lambda item: (item.score, item.date), reverse=True)
    selected = candidates[:MAX_CONTEXT_SOURCES]
    if len(selected) < MIN_CONTEXT_SOURCES:
        raise ValueError(f"関連DeepDive文脈不足: {len(selected)}件 (必要: {MIN_CONTEXT_SOURCES}件以上)")
    return selected[:MAX_CONTEXT_SOURCES]


def _yaml_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _context_frontmatter(contexts: list[ContextSource]) -> str:
    lines = ["context_sources:"]
    for context in contexts:
        lines.extend(
            [
                f"  - date: {_yaml_quote(context.date)}",
                f"    title: {_yaml_quote(context.title)}",
                f"    relation: {_yaml_quote(context.relation)}",
            ]
        )
    return "\n".join(lines)


def _context_source_dates(markdown: str) -> list[str]:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return []
    return re.findall(r"^\s*-\s+date:\s*[\"']?(\d{4}-\d{2}-\d{2})", match.group(1), flags=re.MULTILINE)


def _existing_script_is_valid(markdown: str) -> bool:
    if len(_context_source_dates(markdown)) < MIN_CONTEXT_SOURCES:
        return False
    return not deepdive_dialogue.validate_dialogue(deepdive_dialogue.parse_dialogue(markdown))


def _turns(title: str, sentences: list[str], contexts: list[ContextSource]) -> list[tuple[str, str]]:
    s = sentences + [
        "今回の論点は、単発のニュースではなく過去のDeepDiveで積み上げた流れの次の段階として読む必要があります。",
        "企業には業務設計、検収責任、権限管理、再利用可能な手順づくりとして波及します。",
        "次に見るべき点は、モデル性能そのものではなく、誰がどの業務を委任し、どこで止めるかです。",
    ]
    first = contexts[0]
    second = contexts[1]
    extra = contexts[2:]
    turns = [
        ("若手", f"今日のDeepDiveは「{title}」でした。今回は単体の解説ではなく、過去回とのつながりから読むべきですか。"),
        ("先輩", f"その読み方がいいね。当日記事では、{_clip(s[0], 150)} 過去のDeepDiveで扱った論点が、業務設計の話へ一段進んだと見る。"),
        ("若手", f"まず {first.date} の「{first.title}」とは、どうつながりますか。"),
        ("先輩", f"{first.date}回では、{_clip(first.summary, 150)} {_clip(first.change, 180)}"),
        ("若手", f"関係の種類でいうと、これは「{first.relation}」ですね。単なる再説明ではなく、論点の位置が変わっている。"),
        ("先輩", f"そう。接点は{first.link}だ。過去回を踏まえると、今回のニュースは新機能紹介ではなく、運用責任の置き場を問う材料になる。"),
        ("若手", f"もう一つ、{second.date} の「{second.title}」も関係しますか。"),
        ("先輩", f"関係する。{second.date}回では、{_clip(second.summary, 150)} {_clip(second.change, 180)}"),
        ("若手", "つまり、AIを使うかどうかではなく、AIで変わった業務を誰が説明できるかが焦点になるんですね。"),
        ("先輩", f"その通り。{_clip(s[1], 150)} ここを外すと、導入率や利用率の数字だけを追ってしまう。"),
        ("若手", "ITコンサルタントの実務示唆としては、どこを顧客に確認すべきでしょうか。"),
        ("先輩", "まず、どの業務を委任するのか、成果物を誰が検収するのか、失敗時にどこで止めるのかを確認する。さらに、その手順を再利用できる形に残せるかを見る。"),
        ("若手", "DeepDive記事を読むだけなら理解で終わりますが、過去回と並べると提案論点になりますね。"),
        ("先輩", f"そうだね。{_clip(s[2], 150)} 過去回は文脈、今回の記事は実装段階の確認表として使える。"),
        ("若手", "過去30日の流れで見ると、単発の話題ではなく、同じ問題が少しずつ現場実装へ近づいているように見えます。"),
        ("先輩", "そこが大事だね。DeepDiveのストックは、今日の記事を薄めるためではなく、論点の重心がどこからどこへ移ったかを説明するために使う。音声でもその連続性を前面に出す。"),
        ("若手", "すると、音声で伝えるべきなのは記事の要約だけではなく、前の論点が今回どの判断材料に変わったかですね。"),
        ("先輩", "うん。聞き手が次に動けるように、過去回の背景、今回の変化、顧客に確認する問いを一本の線にする必要がある。"),
        ("若手", "ここまでをつなぐと、若手側からも一つ指摘できそうです。"),
        ("先輩", "言ってみよう。数字の大小ではなく、業務OSとして定着する条件を見抜けるかが今回の肝だ。"),
        ("若手", f"過去回では「{first.title}」で統制の必要性を見ました。今回の「{title}」では、その統制を前提に、どの仕事をAIへ渡すかまで問われています。"),
        ("先輩", "それが鋭い指摘だね。ニュースを行動に翻訳するなら、導入ツール名ではなく、委任、検収、再利用の設計を確認するところまでが今回の読みどころだ。"),
    ]
    for context in extra:
        context_turns = [
            ("若手", f"補助線として {context.date} の「{context.title}」も見ておくとよさそうです。"),
            ("先輩", f"うん。これは「{context.relation}」の関係で、{_clip(context.change, 160)}"),
        ]
        turns = turns[:-4] + context_turns + turns[-4:]
    return turns


def build_dialogue_markdown(
    source_markdown: str,
    *,
    source_name: str,
    archive_dir: Path | None = None,
    context_pack_path: Path | None = None,
    context_days: int = DEFAULT_CONTEXT_DAYS,
) -> str:
    title = _frontmatter_value(source_markdown, "title", "DeepDive")
    issue_date = _frontmatter_value(source_markdown, "date", "")
    contexts = _shared_context_sources_for_deepdive(
        source_markdown,
        source_name=source_name,
        archive_dir=archive_dir,
        context_pack_path=context_pack_path,
        context_days=context_days,
    )
    turns = _turns(title, _body_sentences(source_markdown), contexts)
    body = "\n\n".join(f"{speaker}: {text}" for speaker, text in turns)
    markdown = f"""---
title: "DeepDive解説対談: {title}"
date: "{issue_date}"
source: "{source_name}"
type: "deepdive-dialogue"
audio_target_minutes: 5
{_context_frontmatter(contexts)}
roles:
  senior:
    model_uuid: "47e53151-a378-46f3-abee-ce13aa07feb1"
  junior:
    model_uuid: "59f96896-64d2-4378-830a-4d5feb3d81aa"
---

## 台本

{body}
"""
    parsed_turns = deepdive_dialogue.parse_dialogue(markdown)
    issues = deepdive_dialogue.validate_dialogue(parsed_turns)
    if issues:
        raise ValueError("; ".join(issues))
    return markdown


def build_dialogue_script(
    source: Path,
    *,
    output: Path | None = None,
    force: bool = False,
    context_pack_path: Path | None = None,
) -> Path:
    output = output or source.with_name(source.name.replace("-DeepDive.md", "-DeepDive-dialogue.md"))
    if output.exists() and not force:
        existing = output.read_text(encoding="utf-8-sig")
        if _existing_script_is_valid(existing):
            return output
    markdown = build_dialogue_markdown(
        source.read_text(encoding="utf-8"),
        source_name=source.as_posix(),
        archive_dir=source.parent,
        context_pack_path=context_pack_path,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepDive 本文から TTS 対談台本を生成します。")
    parser.add_argument("source", type=Path, help="digest/DeepDive/YYYY-MM-DD-DeepDive.md")
    parser.add_argument("--output", type=Path, help="出力する dialogue Markdown")
    parser.add_argument("--context-pack", type=Path, help="build/deepdive-context/YYYY-MM-DD.json")
    parser.add_argument("--force", action="store_true", help="既存台本があっても再生成する")
    args = parser.parse_args(argv)
    out = build_dialogue_script(
        args.source,
        output=args.output,
        force=args.force,
        context_pack_path=args.context_pack,
    )
    print(f"[tts] DeepDive dialogue script ready: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
