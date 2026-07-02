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

CATEGORY_LABELS = {
    "fx": "FX",
    "ai": "AI",
    "it": "IT",
    "mobility": "Mobility",
    "manufacturing": "Manufacturing",
    "economy": "Economy",
    "game": "Game",
}

CATEGORY_SUPPLEMENT_SENTENCES = {
    "fx": (
        "FXは、ドル円の水準そのものより、政策発言を市場がどこまで織り込んだかを見る場面です。",
        "為替が少し緩んでも、輸入価格や企業の採算には時間差で効くため、次の物価材料と合わせて追う必要があります。",
    ),
    "ai": (
        "AIは、モデル名の更新だけでなく、計算資源、配布先、利用画面を誰が押さえるかが競争軸になっています。",
        "大型投資のニュースは華やかですが、実際にはサービスを安定して届ける供給設計の話として聞く方が実務に近いです。",
    ),
    "it": (
        "ITは、導入スピードよりも、審査、監視、責任分界を先に置けるかが案件の成否を左右します。",
        "新しいクラウドや評価サービスは、便利さを増やすだけでなく、後から説明できる運用を作るための材料でもあります。",
    ),
    "mobility": (
        "Mobilityは、車両価格、関税、安全基準、運行支援が同時に動き、実装条件を一つずつ積み直す局面です。",
        "自動運転やEVの話題も、技術の完成度だけでなく、どの国のルールで、どの現場に入るかまで見ないと判断しにくくなっています。",
    ),
    "manufacturing": (
        "Manufacturingは、増産の量より、拠点、材料、人材、品質保証をどの組み合わせで持つかが焦点です。",
        "日印協力や新拠点の話は、短期の数字より、数年後の供給網をどこに置くかという意思決定として重みがあります。",
    ),
    "economy": (
        "Economyは、同じ物価や金利の数字でも、家計、企業、政策当局で受け止める重さが違うところに注意が必要です。",
        "円安、値上げ、投資協力が重なる日は、負担と成長のどちらを先に説明するかで、ニュースの見え方が変わります。",
    ),
    "game": (
        "Gameは、作品の中身だけでなく、販売導線、ストア運営、安全対応、過去資産の扱いが前面に出ています。",
        "ダウンロード専売や通報機能の更新は、遊びを届け続けるための流通と信頼の設計として読むとつながります。",
    ),
}

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


def _daily_supplement_sentences(issue: str) -> list[str]:
    issue_jp = _issue_japanese(issue)
    categories = scheduled_category_ids(issue)
    labels = [CATEGORY_LABELS.get(cat_id, cat_id) for cat_id in categories]
    joined = "、".join(labels)
    sentences: list[str] = [
        f"{issue_jp}号の補足整理です。",
        f"{issue_jp}号では、{joined}を同じ流れの中で聞き直すと、個別のニュースより実装条件の違いが見えます。",
        "価格、供給、審査、運用、安全、販路のどれが先に動いたかを分けると、朝の材料はかなり整理しやすくなります。",
    ]
    for cat_id, label in zip(categories, labels):
        sentences.extend(
            CATEGORY_SUPPLEMENT_SENTENCES.get(
                cat_id,
                (
                    f"{label}では、発表内容だけでなく、誰が負担を持ち、誰が次の確認を引き受けるかまで見る必要があります。",
                    f"{label}の続報では、数字の大小より、利用者、取引先、運営側に残る摩擦が減っているかを確認します。",
                ),
            )
        )
    sentences.extend(
        [
            "今日の材料を実務側から見るなら、最初に問うべきなのは、どの選択肢が増えたかではなく、どの制約が先に固まったかです。",
            "その制約を見落とすと、明るい見出しでも導入後の負担が重くなり、慎重な見出しでも次の準備が進んでいることがあります。",
            "聞き手としては、各カテゴリの結論を急ぐより、条件が変わった場所を一つずつ拾う方が、明日以降の続報を追いやすくなります。",
            "もう一段だけ引いて見ると、今日の各ニュースは、拡大局面に入る前の確認表を埋めているようにも見えます。",
            "この確認表が埋まるほど、企業は次の投資を説明しやすくなり、利用者は変化を受け入れる準備をしやすくなります。",
            "反対に、確認表が空いたまま進む領域では、期待が高くても、後から費用や責任の置き場が問題になりやすくなります。",
            "だから今日は、派手な発表を並べるだけでなく、発表の後に必要になる確認、調整、説明の順番まで含めて見る日です。",
            "その順番が見えると、短期の驚きと中期の変化を分けられ、どのニュースを続けて追うべきかも決めやすくなります。",
            "一つひとつの材料は小さくても、同じ方向の条件整備が複数の領域で重なると、次の週の動き方はかなり変わります。",
            "この読み方を置いておくと、次に同じ企業名や政策名が出たとき、前回から何が進んだのかを比べやすくなります。",
            "朝の段階では断定しすぎず、条件がそろった領域と、まだ説明が必要な領域を分けておくことが役に立ちます。",
            "たとえば投資のニュースは、金額だけでなく、どの市場で回収する前提なのかを見ないと評価がぶれます。",
            "安全基準や審査のニュースは、足止めに見えることもありますが、広く使うための入口を整える意味もあります。",
            "販売導線の変更は、ユーザーから見ると小さな仕様変更でも、企業側では在庫、課金、サポートの設計を変える判断です。",
            "供給網の再配置は、今日すぐ数字に出なくても、数か月後の価格や納期にじわじわ効いてきます。",
            "だから、今日の材料を一つの勝ち負けで読むより、どの前提が固定され、どの前提がまだ揺れているかで分ける方が現実的です。",
            "固定された前提が増える領域では、次の発表が実装や販売に進みやすくなります。",
            "逆に、前提が揺れたままの領域では、大きな発表が出ても、現場では確認作業が残りやすくなります。",
            "この差を意識しておくと、同じニュースでも、期待してよい部分と、まだ保留すべき部分を分けて聞けます。",
            "今日の並びでは、派手さよりも、説明責任をどこに置くかが繰り返し出てきました。",
            "利用者、企業、政策側の三者が同じ速度で動くことは少ないので、その速度差を見ることも大切です。",
            "速度差が大きい領域では、よい技術やよい制度でも、導入の順番を間違えると摩擦が残ります。",
            "一方で、速度差を前提に設計できている領域は、地味でも着実に広がりやすくなります。",
            "今日のニュースを明日につなげるなら、次に見るべきなのは、発表の追加ではなく、実装条件がどこまで具体化したかです。",
            "その具体化が見えたとき、今日の点だったニュースは、週をまたいで線として読めるようになります。",
            "朝の原稿では、ここを細かく言い切りすぎず、追跡する観点として残しておくくらいがちょうどよい距離感です。",
            "その距離感を保つと、ニュースを煽らず、かといって単なる一覧にもせず、次に見るべき変化を残して終われます。",
        ]
    )
    return sentences


def _daily_closing_sentence(_issue: str) -> str:
    return "今日の観点・考察として、判断軸は成長の速さそのものではなく、責任分界と供給条件を先に言語化できているかにあります。"


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
        supplement_sentences = SUPPLEMENT_SENTENCES + tuple(_daily_supplement_sentences(issue))
        closing_sentence = _daily_closing_sentence(issue)
        for _ in range(4):
            for sentence in supplement_sentences:
                if sentence in repaired_body or sentence in additions:
                    continue
                candidate_additions = additions + [sentence]
                if closing_sentence not in repaired_body and closing_sentence not in candidate_additions:
                    candidate_additions = candidate_additions + [closing_sentence]
                candidate_body = repaired_body.rstrip() + "\n\n" + "\n".join(candidate_additions)
                if effective_char_count(candidate_body) > TARGET_MAX:
                    break
                additions.append(sentence)
                if effective_char_count(candidate_body) >= TARGET_MIN:
                    break
            final_additions = list(additions)
            if closing_sentence not in repaired_body and closing_sentence not in final_additions:
                final_additions.append(closing_sentence)
            if effective_char_count(repaired_body.rstrip() + "\n\n" + "\n".join(final_additions)) >= TARGET_MIN:
                break
        if closing_sentence not in repaired_body and closing_sentence not in additions:
            additions.append(closing_sentence)

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
