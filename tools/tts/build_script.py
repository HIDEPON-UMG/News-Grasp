from __future__ import annotations

import argparse
from datetime import date as date_type, timedelta
from decimal import Decimal, InvalidOperation
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "digest" / "Summary"
BUILD_DIR = REPO_ROOT / "build" / "tts"

CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "fx": ("為替", "FX", "Foreign Exchange"),
    "ai": ("AI", "人工知能", "Artificial Intelligence"),
    "it": ("IT-Consulting", "IT", "コンサル", "Consulting"),
    "mobility": ("モビリティ", "Mobility", "EV", "自動運転"),
    "manufacturing": ("製造", "Manufacturing", "半導体", "工場"),
    "economy": ("経済", "Economy", "消費", "雇用"),
    "game": ("ゲーム", "Game", "Gaming"),
}

KANA_REPLACEMENTS = {
    "News Grasp": "ニュース グラスプ",
    "IT-Consulting": "アイティー コンサルティング",
    "IT": "アイティー",
    "AI": "エーアイ",
    "EV": "イーブイ",
    "FX": "エフエックス",
}

PRONUNCIATION_REPLACEMENTS = {
    "後工程": "あとこうてい",
    "上方修正": "じょうほうしゅうせい",
}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_URL_RE = re.compile(r"https?://\S+")
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
_MARKDOWN_TOKEN_RE = re.compile(r"^[#>\-\*\s]+", re.MULTILINE)
_AUDIO_TITLE_LINE_RE = re.compile(r"^\s*#*\s*(?:News Grasp|ニュース\s*グラスプ)\s*#?\d{8}\s*(?:[—\-–]\s*)?音声朗読原稿\s*$")
_COUNT_IGNORE_RE = re.compile(r"[\s\u3000、。，．・…「」『』（）()\[\]【】!！?？:：;；,.\-—–_#>*`~|/\\]")
_PATRONIZING_RE = re.compile(
    r"(細かな数字を全部覚えるより|覚えることが大切|持ち帰ることが大切|落ち着いて追えば|流れは見えてきます)"
)
_SENTENCE_RE = re.compile(r"[^。！？\n]+[。！？]")
_PROMPT_EXAMPLE_PHRASES = (
    "ここは少し意外でした",
    "このニュースは地味ですが、後から効いてきそうです",
    "これは現場側には重い話です",
    "ここは少し胸に来ます",
    "地味ですが、あとから効きそうです",
    "自分が提案側なら、ここは契約条件まで聞かれそうです",
    "現場に入れる立場だと、この後工程コストは無視できません",
    "これはAIだけの話ではなく、製造や電力にもつながります",
)
_DOLLAR_PREFIX_UNIT_RE = re.compile(
    r"(?i)(?:US\s*)?[$＄]\s*([0-9]+(?:\.[0-9]+)?)\s*([KMBT])(?![A-Za-z0-9])"
)
_DOLLAR_SUFFIX_UNIT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])([0-9]+(?:\.[0-9]+)?)\s*([KMBT])\s*(?:ドル|dollars?|USD)(?![A-Za-z0-9])"
)
_REPEATED_MOTIFS = (
    "ここは少し",
    "地味ですが",
    "後から効",
    "あとから効",
    "今日の軸",
    "今日の観点",
    "朝会で一言",
    "誰が説明",
    "誰が運用",
    "後工程",
)


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def load_script(date: str) -> str:
    path = SCRIPT_DIR / f"{date}-audio-script.md"
    text = path.read_text(encoding="utf-8")
    return _FRONTMATTER_RE.sub("", text).strip()


def effective_char_count(text: str) -> int:
    return len(_COUNT_IGNORE_RE.sub("", text))


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _format_usd_japanese_units(value: Decimal, unit: str) -> str:
    multipliers = {
        "K": Decimal("1000"),
        "M": Decimal("1000000"),
        "B": Decimal("1000000000"),
        "T": Decimal("1000000000000"),
    }
    dollars = value * multipliers[unit.upper()]
    if dollars >= Decimal("1000000000000"):
        return f"{_format_decimal(dollars / Decimal('1000000000000'))}兆ドル"
    if dollars >= Decimal("100000000"):
        return f"{_format_decimal(dollars / Decimal('100000000'))}億ドル"
    if dollars >= Decimal("10000"):
        return f"{_format_decimal(dollars / Decimal('10000'))}万ドル"
    return f"{_format_decimal(dollars)}ドル"


def normalize_us_currency_units(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        try:
            return _format_usd_japanese_units(Decimal(match.group(1)), match.group(2))
        except InvalidOperation:
            return match.group(0)

    text = _DOLLAR_PREFIX_UNIT_RE.sub(_replace, text)
    return _DOLLAR_SUFFIX_UNIT_RE.sub(_replace, text)


def _strip_audio_title_lines(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not _AUDIO_TITLE_LINE_RE.match(line.strip())
    ]
    return "\n".join(lines)


def _date_japanese(date: str) -> str:
    try:
        _, month, day = date.split("-")
        return f"{int(month)}月{int(day)}日"
    except Exception:
        return date


def _sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", _strip_audio_title_lines(text))
    return [m.group(0).strip() for m in _SENTENCE_RE.finditer(normalized) if len(m.group(0).strip()) >= 8]


def _recent_history_texts(target_date: str) -> list[str]:
    try:
        day = date_type.fromisoformat(target_date)
    except ValueError:
        return []
    history: list[str] = []
    for offset in (1, 2):
        path = SCRIPT_DIR / f"{(day - timedelta(days=offset)).isoformat()}-audio-script.md"
        if path.exists():
            history.append(_FRONTMATTER_RE.sub("", path.read_text(encoding="utf-8")).strip())
    return history


def _history_issues(text: str, history_texts: list[str]) -> list[str]:
    issues: list[str] = []
    for phrase in _PROMPT_EXAMPLE_PHRASES:
        if phrase in text:
            issues.append(f"例文コピー禁止: prompt 例文をそのまま使っています ({phrase})")
            break

    if not history_texts:
        return issues

    current_sentences = set(_sentences(text))
    for history in history_texts:
        overlap = sorted(current_sentences & set(_sentences(history)))
        if len(overlap) >= 3:
            issues.append(
                "過去原稿との同一文: 直近原稿と同じ文が3件以上あります "
                f"({'; '.join(overlap[:3])})"
            )
            break

    for history in history_texts:
        shared = [motif for motif in _REPEATED_MOTIFS if motif in text and motif in history]
        if len(shared) >= 4:
            issues.append("過去原稿との定型表現: 直近原稿と同じ motif が多すぎます (" + ", ".join(shared[:6]) + ")")
            break
    return issues


def validate_script(text: str, *, date: str | None = None, history_texts: list[str] | None = None) -> list[str]:
    issues: list[str] = []
    text = _strip_audio_title_lines(text)
    missing: list[str] = []
    for cat_id, aliases in CATEGORY_ALIASES.items():
        if not any(alias in text for alias in aliases):
            missing.append(cat_id)
    if missing:
        issues.append(f"カテゴリ不足: {', '.join(missing)}")

    if date:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        expected_date = _date_japanese(date)
        if expected_date not in first_line or "朝のニュース" not in first_line:
            issues.append(f"冒頭セリフ不足: 最初の本文で {expected_date} と 朝のニュース を伝える")
        tail = "".join(line.strip() for line in text.splitlines() if line.strip())[-450:]
        if "今日の観点" not in tail or "考察" not in tail:
            issues.append("今日の観点・考察不足: 締めで当日の判断軸そのものをまとめる")

    if _PATRONIZING_RE.search(text):
        issues.append("上から目線コメント: 聞き手に説教せず、今日の観点・考察を具体化する")

    issues.extend(_history_issues(text, history_texts or []))

    count = effective_char_count(text)
    if count < 2500:
        issues.append(f"字数不足: {count}字 (必要: 2500〜3000字)")
    elif count > 3000:
        issues.append(f"字数超過: {count}字 (必要: 2500〜3000字)")
    return issues


def normalize_for_tts(text: str) -> str:
    text = _FRONTMATTER_RE.sub("", text)
    text = _strip_audio_title_lines(text)
    text = _WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _URL_RE.sub("", text)
    text = re.sub(r"`{3}.*?`{3}", "", text, flags=re.DOTALL)
    text = _MARKDOWN_TOKEN_RE.sub("", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = normalize_us_currency_units(text)
    for src, dst in KANA_REPLACEMENTS.items():
        text = re.sub(
            rf"(?<![A-Za-z]){re.escape(src)}(?![A-Za-z])",
            dst,
            text,
        )
    for src, dst in PRONUNCIATION_REPLACEMENTS.items():
        text = text.replace(src, dst)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def build(date: str) -> Path | None:
    path = SCRIPT_DIR / f"{date}-audio-script.md"
    if not path.exists():
        _warn(f"audio script not found: {path}")
        return None
    text = load_script(date)
    issues = validate_script(text, date=date, history_texts=_recent_history_texts(date))
    if issues:
        for issue in issues:
            _warn(issue)
        return None

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    out = BUILD_DIR / f"{date}.script.txt"
    out.write_text(normalize_for_tts(text), encoding="utf-8", newline="\n")
    print(f"[tts] script built: {out}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日次朗読原稿を検査し TTS 用 plain text を生成します。")
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    return 0 if build(args.date) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
