---
title: "Robotaxi 商用化の分岐点：規模か安全か"
date: "2026-05-31"
issue: "20260531"
kind: deepdive
lens: mobility
theme: "Waymo の段階拡大と Tesla の参入で、Robotaxi 商用化は走行規模・展開都市・安全データでどこまで進んだか。"
og_image: ""
tags: ["deepdive", "weekly", "news-grasp", "issue-20260531", "Waymo", "Tesla", "robotaxi", "NHTSA"]
---

## 背景

```timeline
[
  {"date": "2025-06-22", "title": "Tesla、Austin で監督者付き Robotaxi を開始（当初は安全監視員が同乗）", "source": "Electrek", "url": "https://electrek.co/2026/02/16/tesla-robotaxi-status-check-8-months-in/", "thumb": ""},
  {"date": "2026-02-16", "title": "Electrek、Tesla の稼働率 19%・約 42 台を報告（開始 8 か月時点）", "source": "Electrek", "url": "https://electrek.co/2026/02/16/tesla-robotaxi-status-check-8-months-in/", "thumb": ""},
  {"date": "2026-03-27", "title": "Waymo、週 50 万回・10 都市に到達（X で公表）。2026 年末に週 100 万回を目標", "source": "TechCrunch", "url": "https://techcrunch.com/2026/03/27/waymo-skyrocketing-ridership-in-one-chart/", "thumb": ""},
  {"date": "2026-05-12", "title": "Electrek、Tesla の安全ボトルネックを報道（crash 率・稼働率・無人台数の実態）", "source": "Electrek", "url": "https://electrek.co/2026/05/12/tesla-robotaxi-convenience-issues-hide-safety-bottleneck/", "thumb": ""}
]
```

```players
[
  {"name": "Waymo", "role": "Alphabet 傘下の自動運転タクシー最大手", "move": "週 50 万回・10 都市・累計 1 億 7,070 万無人マイルを公表し、2026 年末に週 100 万回を目標化", "position": "安全データを堀に段階拡大。東京・ロンドンを国際展開先に明示"},
  {"name": "Tesla", "role": "EV 最大手・カメラのみで後発参入", "move": "Austin で約 50 台、Dallas・Houston へ展開（真に無人は 3 都市計 25 台）", "position": "安全検証が拡大の律速。週間乗車数は非開示で、Musk は予測を下方修正"},
  {"name": "NHTSA", "role": "米運輸安全規制（道路交通安全局）", "move": "Standing General Order で全 ADS 事業者に crash 報告を義務化", "position": "両社のデータ開示の共通土台。事故統計が事実上の信頼通貨"}
]
```

```relations
{
  "title": "当事者の関係：競合・規制・出資",
  "nodes": [
    {"id": "alphabet", "label": "Alphabet", "group": "親会社"},
    {"id": "waymo", "label": "Waymo", "group": "段階拡大・先行"},
    {"id": "tesla", "label": "Tesla", "group": "ビジョン・後発"},
    {"id": "nhtsa", "label": "NHTSA", "group": "規制当局"}
  ],
  "edges": [
    {"from": "alphabet", "to": "waymo", "label": "親会社・出資", "kind": "出資"},
    {"from": "waymo", "to": "tesla", "label": "商用化レース／週次規模", "kind": "競合"},
    {"from": "nhtsa", "to": "waymo", "label": "SGO crash 報告義務", "kind": "規制"},
    {"from": "nhtsa", "to": "tesla", "label": "SGO crash 報告義務", "kind": "規制"}
  ],
  "source": "関係は本文・一次ソースに基づく編集部整理。Waymo Safety / NHTSA SGO"
}
```

[[Waymo]]は**2025 年内に累計 2,000 万回**超の有料乗車を積み上げ、12 月の年次振り返りで__「実証から拡大の段階へ」__と総括した。一方[[Tesla]]は**2025 年 6 月**に[[Austin]]で監督者同乗の限定サービスから始めており、__出発点の戦略差__がそのまま両社の現在地を分けている。

両社を貫く力学は__センサー哲学とコスト構造の対立__である。[[Waymo]]は**LiDAR を含む高価なセンサー群**で安全データを先に積み上げ、[[Tesla]]は**カメラのみのビジョン方式**で量産コストを抑える。__どちらが先に「安全に、速く」拡大できるか__が、2026 年の中心的な問いになった。

## 深掘り

```chart
{
  "type": "bar",
  "title": "Waymo 週間有料乗車回数",
  "unit": "万回 / 週",
  "categories": ["2024年5月", "2026年3月", "2026年末"],
  "series": [
    {"name": "週間有料乗車", "data": [5, 50, 100]}
  ],
  "annotations": [{"label": "2026 年末は目標値（未達）", "at": "2026年末"}],
  "source": "Waymo 公式（X／年次ブログ）／ TechCrunch 2026-03-27 https://techcrunch.com/2026/03/27/waymo-skyrocketing-ridership-in-one-chart/"
}
```

```chart
{
  "type": "bar",
  "title": "Austin 稼働車両数（概数）",
  "unit": "台",
  "categories": ["Waymo", "Tesla"],
  "series": [
    {"name": "稼働台数", "data": [250, 50]}
  ],
  "annotations": [{"label": "Tesla の真の無人は 3 都市計 25 台", "at": "Tesla"}],
  "source": "市当局集計（Electrek 2026-05-12）https://electrek.co/2026/05/12/tesla-robotaxi-convenience-issues-hide-safety-bottleneck/"
}
```

[[Waymo]]の週間有料乗車は**2024 年 5 月の 5 万回から 2026 年 3 月に 50 万回**へと、__2 年弱で 10 倍__に達した。さらに**2026 年末に 100 万回**を目標に置く。これは__自動運転が実験から日常インフラへ移りつつある__ことを示す数字だが、達成の__確度そのものはまだ未確定__である（目標値であって実績ではない）。

```table
{
  "title": "Waymo / Tesla 主要指標の比較・変遷",
  "columns": ["指標", "Waymo", "Tesla", "出典"],
  "rows": [
    ["週間有料乗車回数", "50 万回 / 週", "未開示", "TechCrunch / Electrek"],
    ["稼働都市数", "10 都市", "3 都市（真の無人）", "各社公表"],
    ["Austin 稼働台数", "250 台超", "約 50 台", "市当局集計"],
    ["累計無人マイル", "1 億 7,070 万", "非開示", "Waymo Safety / —"],
    ["事故率（1 事故あたり）", "非開示相当", "約 1 / 5.6 万マイル", "Electrek"],
    ["2026 年末の指針", "週 100 万回（目標）", "十数州へ下方修正", "公式 / Q1 決算"]
  ],
  "source": "Electrek 2026-02-16 / 2026-05-12, TechCrunch 2026-03-27, Waymo Safety Impact"
}
```

安全面では[[Waymo]]が**1 億 7,070 万無人マイル**（2025 年 12 月時点）を基に、人間運転比で**重傷事故 92% 減**と公表する。対して[[Tesla]]は**1 事故あたり約 5.6 万マイル**で、__人間ドライバー比でおよそ 4〜9 倍悪い__——ただしこの倍率は**出典で割れる**ため、__単一の倍率を断定せず両論として扱う__。

ここから立つ論点は__「規模の速度」と「安全の厳格さ」のどちらが商用化の律速か__である。[[Tesla]]の[[Musk]]自身が**Q1 決算で「制約は安全検証だ」**と認め、予測を**半数の州から十数州へ下方修正**した。__規模を追っていた側が、安全に引き戻された__構図がこの四半期で鮮明になっている。

## 注目点

```decision
{
  "issue": "Robotaxi 商用化の勝者を分けるのは「規模の拡大速度」か「安全検証の厳格さ」か——投資家・規制当局・利用者は誰のモデルに賭けるべきか",
  "options": ["Waymo 型：LiDAR を含む高コストセンサーで安全データを先に積み、段階的に拡大する", "Tesla 型：カメラのみのビジョン方式で低コスト・大量展開を狙うが、安全検証が律速になる", "規制主導：NHTSA・州当局が crash データ基準で各社の展開ペースを実質コントロールする"],
  "deadline": "2026 年末（Waymo の週 100 万回目標の達否）／ Tesla の Q2・Q3 2026 決算（無人台数と crash 率の改善）",
  "decider": "各社経営陣（Waymo: Tekedra Mawakana ／ Tesla: Elon Musk）と NHTSA・州規制当局、最終的には利用者の選好"
}
```

[[Waymo]]型は__安全データという堀__を深める代わりに**高コスト**を抱え、[[Tesla]]型は**低コスト量産**を武器にしつつ__安全検証で足踏み__する。__私の読み筋は「2026 年は規模の速度より、安全データの公開度が信頼を決める」__というものだ——[[NHTSA]]の SGO 報告が事実上の共通通貨になり、宣言ではなく統計が評価軸になる。

## 要約

[[Waymo]]は週**50 万回**・**10 都市**・**1 億 7,070 万無人マイル**で先行し、**2026 年末に 100 万回**を狙う。[[Tesla]]は[[Austin]]**約 50 台**・無人は限定で、**約 1 事故 / 5.6 万マイル**と安全検証が拡大の律速だ。[[Musk]]自身が予測を下方修正した。__2026 年の勝負は規模の速度ではなく、NHTSA に開示される安全データの厚みが信頼を決める__——派手な宣言より事故統計が問われる年になる。

## 参考リンク

- Waymo「2025 Year in Review」（Waymo 公式ブログ・2025-12）— 累計 20M 回超・週 100 万回目標: https://waymo.com/blog/2025/12/2025-year-in-review/
- Waymo「Safety Impact」（Waymo 公式・2025-12 時点）— 1 億 7,070 万無人マイル・重傷 92% 減: https://waymo.com/safety/impact/
- Waymo's skyrocketing ridership in one chart（TechCrunch・2026-03-27）— 5 万回→50 万回の 10 倍成長: https://techcrunch.com/2026/03/27/waymo-skyrocketing-ridership-in-one-chart/
- Tesla 'Robotaxi' status check: 8 months in（Electrek・2026-02-16）— Austin 約 42 台・稼働率 19%: https://electrek.co/2026/02/16/tesla-robotaxi-status-check-8-months-in/
- Tesla Robotaxi's safety bottleneck（Electrek・2026-05-12）— 1 事故 / 約 5.7 万マイル・Musk の下方修正: https://electrek.co/2026/05/12/tesla-robotaxi-convenience-issues-hide-safety-bottleneck/
