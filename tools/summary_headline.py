"""Summary のニュース見出し契約を一箇所で判定する。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
from typing import Any, Mapping


SUMMARY_HEADLINE_CUTOVER = date(2026, 8, 3)

_NEWS_ANCHORS = (
    "AI", "OpenAI", "円", "ドル", "為替", "金利", "介入", "クラウド", "半導体",
    "EV", "自動車", "企業", "政府", "日銀", "米国", "日本", "日米", "ゲーム",
    "資源", "決算", "関税", "選挙", "地震", "台風", "原油", "株",
)
_NEWS_ACTIONS = (
    "発表", "開始", "拡大", "縮小", "値下げ", "値上げ", "迫る", "決定", "導入",
    "参入", "撤退", "再開", "停止", "成立", "合意", "更新", "転換", "上昇", "下落",
    "回復", "再編", "介入", "買収", "売却", "承認", "可決", "発足", "発売",
)
_DOMAIN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("円", "ドル", "為替", "日銀", "FRB", "ECB", "介入"),
    ("AI", "OpenAI", "Anthropic", "モデル", "半導体"),
    ("クラウド", "防衛クラウド", "IT", "サイバー"),
    ("EV", "自動車", "モビリティ", "軽EV"),
    ("ゲーム", "Switch", "PlayStation"),
    ("製造", "工場", "供給網"),
    ("景気", "GDP", "物価", "決算", "株", "原油", "資源"),
)


def issue_date(frontmatter: Mapping[str, Any], path: Path | None = None) -> date | None:
    """frontmatter、次いでファイル名から号日を得る。"""
    raw = str(frontmatter.get("date") or "").strip()
    if not raw and path:
        raw = path.stem[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def uses_single_headline(frontmatter: Mapping[str, Any], path: Path | None = None) -> bool:
    parsed = issue_date(frontmatter, path)
    return parsed is not None and parsed >= SUMMARY_HEADLINE_CUTOVER


def _matched_domain_count(headline: str) -> int:
    folded = headline.casefold()
    return sum(any(term.casefold() in folded for term in group) for group in _DOMAIN_GROUPS)


def _looks_cross_topic(headline: str) -> bool:
    """結果節より前で、複数の独立ニュースを列挙した見出しを検出する。"""
    subject_clause = re.split(r"[、,]", headline, maxsplit=1)[0]
    if "・" not in subject_clause and "と" not in subject_clause:
        return False
    domain_count = _matched_domain_count(subject_clause)
    action_count = sum(action in subject_clause for action in _NEWS_ACTIONS)
    if "・" in subject_clause and domain_count >= 3:
        return True
    return domain_count >= 2 and action_count >= 2


def summary_headline_quality_errors(headline: str) -> list[str]:
    """単一の主役ニュースを示す具体的見出しかを検査する。"""
    value = str(headline or "").strip().rstrip("。")
    errors: list[str] = []
    if not value:
        return ["frontmatter hero_headline が不足しています。"]
    if not 12 <= len(value) <= 42:
        errors.append(f"hero_headline は12〜42字にしてください（現在 {len(value)} 字）。")
    if not any(anchor.casefold() in value.casefold() for anchor in _NEWS_ANCHORS) or not any(
        action in value for action in _NEWS_ACTIONS
    ):
        errors.append("hero_headline は主体・出来事・動作または結果が分かる具体的なニュース見出しにしてください。")
    if _looks_cross_topic(value):
        errors.append("hero_headline は複数の独立ニュースを接合せず、単一の主役ニュースにしてください。")
    return errors
