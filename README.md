# News-Grasp

毎朝 06:00 JST に Anthropic Routine（`/schedule`）が走り、watchlist と Web 検索の組み合わせで日次レポートを自動生成する。生成物は本 repo に push され、Obsidian ボルト内のサブフォルダとして同期される。

## 仕組みの概要

1. **Routine 起動**（毎朝 06:00 JST / cron UTC `0 21 * * *`）
2. `gh` CLI で `data/watchlist.md` と `data/articles.jsonl` を取得
3. 当日の曜日に応じてジャンルを判定（下表）
4. ジャンルごとに `web_search` で watchlist + 汎用キーワードを検索
5. `articles.jsonl` から直近 90 日の関連記事を抽出（タグ・URL・キーワード一致）
6. Sonnet 4.6 が「記事カード解説」＋「今日のテーマ考察」Markdown を生成
7. `digest/YYYY-MM-DD-{Genre}.md` として作成、`articles.jsonl` を更新
8. commit & push
9. HTML メールを生成 → GAS Webhook 経由で Gmail 配信

## 曜日別ジャンル

| 曜日 | AI | IT&コンサル | 経済 | ゲーム |
|---|---|---|---|---|
| 月 | ● | ● | ● |  |
| 火 | ● | ● | ● | ● |
| 水 | ● | ● | ● |  |
| 木 | ● | ● | ● | ● |
| 金 | ● | ● | ● |  |
| 土 | ● | ● |  | ● |
| 日 | ● | ● |  | ● |

為替（日経平均 / S&P500 / USD-JPY）は経済セクション冒頭に毎日掲載。土日は AI レポート末尾にミニ要約のみ。

## ディレクトリ

```
News-Grasp/
├── README.md
├── digest/                    # 日次レポート（Routine が生成）
│   └── 2026-MM-DD-{Genre}.md
├── data/
│   ├── watchlist.md           # ★ ユーザー編集可：トラッキング対象
│   ├── articles.jsonl         # 過去記事メタ（90日でローテート）
│   ├── archive/               # 90日超のアーカイブ
│   │   └── YYYY-MM.jsonl
│   └── _status.md             # 実行ログ
└── prompts/
    ├── routine-system.md      # Routine プロンプト本体
    └── email-template.html    # GAS 送信用 HTML テンプレ
```

## ユーザー操作

- **watchlist 編集**: `data/watchlist.md` を編集して push すれば翌朝から反映
- **Obsidian 同期**: 朝 07:00 JST に Windows タスクが `git pull` を実行
- **手動同期**: ボルトの `News-Grasp/` で `git pull`
- **手動実行**: `/schedule run news-grasp-daily`

## 関連付けの 5 軸

レポートの「今日のテーマ考察」では、以下が当てはまるときだけ自然に織り込む：

1. 同一トピックの復状・進展
2. 論調の対立・複数ソース間の齟齬
3. 業界跨ぎの連携・波及
4. クロストピックの似てる事例
5. ニュースと株価・為替の連動
