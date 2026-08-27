from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
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


def _canonical_source_sha256(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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


def _body_sentences(markdown: str, *, limit: int = 20) -> list[str]:
    return deepdive_dialogue.source_evidence_sentences(markdown, limit=limit)


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


def _existing_script_is_valid(markdown: str, source_markdown: str, source_name: str) -> bool:
    if _frontmatter_value(markdown, "source", "") != source_name:
        return False
    if _frontmatter_value(markdown, "source_sha256", "") != _canonical_source_sha256(
        source_markdown
    ):
        return False
    return not deepdive_dialogue.validate_dialogue_document(
        markdown,
        source_markdown=source_markdown,
    )


def _turns(
    title: str,
    sentences: list[str],
    contexts: list[ContextSource],
) -> list[tuple[str, str, str, str, str]]:
    """記事固有の7価値を各1区間へ割り当てる決定的な対談を作る。"""
    if not sentences:
        raise ValueError("DeepDive本文に台本根拠として使える文がありません")
    if len(sentences) < 14:
        raise ValueError(
            f"DeepDive本文の独立根拠が不足しています: {len(sentences)}件 (必要14件以上)"
        )
    evidence = sentences[:14]
    evidence_id = lambda index: f"source:{index}"
    historical = (
        " ".join(
            f"{context.date}の「{context.title}」では、{_clip(context.summary, 90)} "
            f"今回との差分は、{_clip(context.change, 110)}"
            for context in contexts
        )
        if contexts
        else ""
    )
    if contexts:
        historical += " "
    return [
        (
            "current_signal", evidence_id(0), evidence_id(1), "若手",
            f"「{_clip(evidence[0], 96)}」という記述で、以前と違う対象はどこですか。",
        ),
        (
            "current_signal", evidence_id(0), evidence_id(1), "先輩",
            f"{_clip(evidence[0], 175)} 対照になる材料は、{_clip(evidence[1], 175)} 両者の対象と時点の差が、今回更新された認識です。",
        ),
        (
            "evidence", evidence_id(2), evidence_id(3), "若手",
            f"「{_clip(evidence[2], 96)}」を、別の確認済み材料で照合できますか。",
        ),
        (
            "evidence", evidence_id(2), evidence_id(3), "先輩",
            f"{_clip(evidence[2], 175)} 独立して照合する材料は、{_clip(evidence[3], 175)} この二件は主体・数値・日付を分けて記録できます。",
        ),
        (
            "causal_chain", evidence_id(4), evidence_id(5), "若手",
            f"「{_clip(evidence[4], 96)}」の後に観測された結果は何ですか。",
        ),
        (
            "causal_chain", evidence_id(4), evidence_id(5), "先輩",
            f"前提として、{_clip(evidence[4], 175)} 観測された結果は、{_clip(evidence[5], 175)} 前者から後者までを記事が示す範囲の因果として扱います。",
        ),
        (
            "counterevidence_or_limit", evidence_id(6), evidence_id(7), "若手",
            f"「{_clip(evidence[6], 96)}」を確定事項にしすぎない境界はどこですか。",
        ),
        (
            "counterevidence_or_limit", evidence_id(6), evidence_id(7), "先輩",
            f"確認済みの範囲は、{_clip(evidence[6], 175)} ただし、{_clip(evidence[7], 175)} この二件に書かれていない将来結果は未確定として残します。",
        ),
        (
            "change_over_time", evidence_id(8), evidence_id(9), "若手",
            f"「{_clip(evidence[8], 96)}」から、次の段階へ何が移りましたか。",
        ),
        (
            "change_over_time", evidence_id(8), evidence_id(9), "先輩",
            f"当日の基準点は、{_clip(evidence[8], 170)} 移動先を示す材料は、{_clip(evidence[9], 170)} {historical}対象・時点・判断基準の移動として比較します。",
        ),
        (
            "decision_implication", evidence_id(10), evidence_id(11), "若手",
            f"「{_clip(evidence[10], 96)}」を顧客判断へ持ち込む際、分けるべき選択条件は何ですか。",
        ),
        (
            "decision_implication", evidence_id(10), evidence_id(11), "先輩",
            f"選択肢を作る根拠は、{_clip(evidence[10], 170)} 条件を具体化する材料は、{_clip(evidence[11], 170)} 対象、前提、撤回条件を別々に置いて比較します。",
        ),
        (
            "next_action", evidence_id(12), evidence_id(13), "若手",
            f"「{_clip(evidence[12], 96)}」を受け、次の会議までに誰が何を確認しますか。",
        ),
        (
            "next_action", evidence_id(12), evidence_id(13), "先輩",
            f"確認対象は、{_clip(evidence[12], 170)} 照合先は、{_clip(evidence[13], 170)} 両方に担当者と期限を置き、差分が出た時点で判断条件を更新します。",
        ),
    ]


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
        min_sources=0,
    )
    turns = _turns(title, _body_sentences(source_markdown), contexts)
    body_parts: list[str] = []
    current_marker: tuple[str, str, str] | None = None
    for value_id, evidence_id, support_id, speaker, text in turns:
        marker = (value_id, evidence_id, support_id)
        if marker != current_marker:
            body_parts.append(
                f"<!-- value:{value_id} evidence:{evidence_id} support:{support_id} -->"
            )
            current_marker = marker
        body_parts.append(f"{speaker}: {text}")
    body = "\n\n".join(body_parts)
    audio_target_minutes = 6 if len(contexts) >= MIN_CONTEXT_SOURCES else 5
    markdown = f"""---
title: "DeepDive解説対談: {title}"
date: "{issue_date}"
source: "{source_name}"
source_sha256: "{_canonical_source_sha256(source_markdown)}"
type: "deepdive-dialogue"
audio_target_minutes: {audio_target_minutes}
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
    issues = deepdive_dialogue.validate_dialogue_document(markdown, source_markdown=source_markdown)
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
    source_markdown = source.read_text(encoding="utf-8-sig")
    try:
        source_name = source.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        parts = source.resolve().parts
        if "digest" not in parts:
            raise ValueError("DeepDive sourceはrepo内のdigest/DeepDiveに置く必要があります")
        source_name = Path(*parts[parts.index("digest"):]).as_posix()
    if output.exists() and not force:
        existing = output.read_text(encoding="utf-8-sig")
        if _existing_script_is_valid(existing, source_markdown, source_name):
            return output
    markdown = build_dialogue_markdown(
        source_markdown,
        source_name=source_name,
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
