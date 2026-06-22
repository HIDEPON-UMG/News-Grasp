from __future__ import annotations

import argparse
import re
from pathlib import Path

from tools.tts import build_script, deepdive_dialogue


_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def _frontmatter_value(markdown: str, key: str, default: str) -> str:
    match = _FRONTMATTER_RE.match(markdown)
    if not match:
        return default
    for line in match.group(1).splitlines():
        if not line.startswith(f"{key}:"):
            continue
        value = line.split(":", 1)[1].strip().strip('"').strip("'")
        return value or default
    return default


def _body_sentences(markdown: str) -> list[str]:
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
        if len(sentences) >= 8:
            break
    return sentences


def _clip(text: str, limit: int = 86) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("、。") + "。"


def _turns(title: str, sentences: list[str]) -> list[tuple[str, str]]:
    s = sentences + [
        "今回の論点は、単発のニュースではなく政策、金利差、市場心理が同時に動く構図として見る必要があります。",
        "企業には輸入コスト、ドル建て契約、価格改定、投資判断の前提として波及します。",
        "次に見るべき点は、当局の発言、金利見通し、市場がどの順序で反応するかです。",
        "短期の材料だけでなく、次の意思決定にどうつながるかを見ることが重要です。",
        "現場では、ニュースを予算、契約、調達、顧客説明の前提に置き換える必要があります。",
        "公開情報を確認しながら、過度な断定を避けて変化点を追う姿勢が求められます。",
        "同じ数字でも、誰が動き、どの制約が残るかによって意味は変わります。",
        "だから結論だけでなく、政策手段、時間軸、企業影響の三点を並べて読む必要があります。",
    ]
    return [
        ("若手", f"今日のDeepDiveは「{title}」でした。まず、何が一番大きな変化だったと見ればいいですか。"),
        ("先輩", f"一番の変化は、{_clip(s[0])} ここから市場の読み筋が一段変わったことだね。"),
        ("若手", "数字や政策主体がいくつも出てきます。どの関係から整理すると分かりやすいですか。"),
        ("先輩", f"まずは因果を分けよう。{_clip(s[1])} つまり、短期の動きと構造要因を混ぜないことが大事だ。"),
        ("若手", "短期の動きに効く手段と、構造に効く手段は違うということですね。"),
        ("先輩", f"そう。{_clip(s[2])} ただし、強い手段ほど副作用や持続性の限界も一緒に見る必要がある。"),
        ("若手", f"記事では、{_clip(s[3])} という点も印象に残りました。これは企業にも関係しますか。"),
        ("先輩", "関係するよ。為替や政策金利は、調達費、海外SaaS、クラウド費用、海外売上の換算にそのまま跳ねる。"),
        ("若手", "ニュースとして追うだけでなく、予算や契約更新の前提に置き換える必要があるんですね。"),
        ("先輩", f"その通り。{_clip(s[4])} 現場では、どの費用がドル建てで、いつ更新されるかを先に見ておくべきだ。"),
        ("若手", "次に同じテーマのニュースを見るときは、どの順番で確認するとよいですか。"),
        ("先輩", f"まず事実、次に政策手段、最後に企業影響だね。{_clip(s[5])} という流れで見ると判断しやすい。"),
        ("若手", "単発の材料ではなく、政策と市場心理の束として読むわけですね。"),
        ("先輩", f"うん。{_clip(s[6])} だから、今日のDeepDiveはリスク管理の前提を更新する材料として読むといい。"),
        ("若手", "明日は数字だけでなく、発言、金利、企業コストへの波及まで一緒に追ってみます。"),
        ("先輩", f"それで十分鋭い。{_clip(s[7])} ニュースを行動に翻訳するところまでが、今回の読みどころだね。"),
    ]


def build_dialogue_markdown(source_markdown: str, *, source_name: str) -> str:
    title = _frontmatter_value(source_markdown, "title", "DeepDive")
    issue_date = _frontmatter_value(source_markdown, "date", "")
    turns = _turns(title, _body_sentences(source_markdown))
    body = "\n\n".join(f"{speaker}: {text}" for speaker, text in turns)
    markdown = f"""---
title: "DeepDive解説対談: {title}"
date: "{issue_date}"
source: "{source_name}"
type: "deepdive-dialogue"
audio_target_minutes: 5
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
    while issues:
        filler = (
            "\n\n若手: 最後に、現場で今日から確認できることはありますか。"
            "\n\n先輩: 契約通貨、更新時期、価格改定の余地、代替調達先を確認することだね。"
            "政策ニュースを見たら、必ず自分たちの費用と収益の前提に引き直す。"
        )
        markdown += filler
        parsed_turns = deepdive_dialogue.parse_dialogue(markdown)
        issues = deepdive_dialogue.validate_dialogue(parsed_turns)
        if len(markdown) > 5000:
            break
    if issues:
        raise ValueError("; ".join(issues))
    return markdown


def build_dialogue_script(source: Path, *, output: Path | None = None, force: bool = False) -> Path:
    output = output or source.with_name(source.name.replace("-DeepDive.md", "-DeepDive-dialogue.md"))
    if output.exists() and not force:
        return output
    markdown = build_dialogue_markdown(source.read_text(encoding="utf-8"), source_name=source.as_posix())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepDive 本文から TTS 対談台本を生成します。")
    parser.add_argument("source", type=Path, help="digest/DeepDive/YYYY-MM-DD-DeepDive.md")
    parser.add_argument("--output", type=Path, help="出力する dialogue Markdown")
    parser.add_argument("--force", action="store_true", help="既存台本があっても再生成する")
    args = parser.parse_args(argv)
    out = build_dialogue_script(args.source, output=args.output, force=args.force)
    print(f"[tts] DeepDive dialogue script ready: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
