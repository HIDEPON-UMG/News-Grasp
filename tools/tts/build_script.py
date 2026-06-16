from __future__ import annotations

import argparse
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


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def load_script(date: str) -> str:
    path = SCRIPT_DIR / f"{date}-audio-script.md"
    text = path.read_text(encoding="utf-8")
    return _FRONTMATTER_RE.sub("", text).strip()


def effective_char_count(text: str) -> int:
    return len(_COUNT_IGNORE_RE.sub("", text))


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


def validate_script(text: str, *, date: str | None = None) -> list[str]:
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
    issues = validate_script(text, date=date)
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
