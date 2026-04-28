# News Grasp

> 時勢を掴み、日々に新たに。

毎朝 06:00 JST にローカル PC 上の Claude Code (Sonnet 4.6) が起動し、watchlist の対象を Web 検索 → 過去 90 日の関連記事と照合 → カテゴリ別 digest Markdown を生成 → GitHub に commit & push → GAS Webhook 経由で Gmail に HTML メール配信、までを自律実行する **個人向け日次ニュースレター・パイプライン**。

## アーキテクチャ概要（D 案：ローカル Claude Code）

```
┌──────────────────────────────────────────────────────────────────┐
│ Windows タスクスケジューラ「News-Grasp Runner」  06:00 JST 毎日  │
│   └─→ C:\Users\hidek\bin\news-grasp-runner.bat                    │
│         ├─ git fetch / pull origin main                           │
│         └─ claude.exe --print --dangerously-skip-permissions ...  │
└──────────────────────────────────────────────────────────────────┘
                          │
                  Sonnet 4.6 が以下を自律実行
                          │
   ┌────────────────────────────────────────────────────────────┐
   │ ① 当日（JST）の対象カテゴリを曜日マトリクスで決定           │
   │ ② watchlist.md / articles.jsonl / prompts/* を Read         │
   │ ③ WebSearch でカテゴリごとに 10 記事を厳選                  │
   │ ④ WebFetch で OGP 画像を取得（失敗時は NG プレースホルダ）  │
   │ ⑤ 過去 90 日の articles.jsonl と関連付け（5 軸）            │
   │ ⑥ digest Markdown を生成（カテゴリ別 + Summary）            │
   │ ⑦ git commit & push                                         │
   │ ⑧ HTML メール組み立て → GAS Webhook へ POST                 │
   └────────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
┌──────────────┐                ┌─────────────────────┐
│ GitHub       │                │ GAS Webhook         │
│ HIDEPON-UMG  │                │ news-grasp-mailer   │
│ /News-Grasp  │                │  └─ GmailApp        │
│  (private)   │                │     .sendEmail x 2  │
└──────────────┘                └─────────────────────┘
        │                                   │
        ▼                                   ▼
┌──────────────────────┐          ┌─────────────────────┐
│ Obsidian Vault       │          │ Gmail 受信箱（×2）  │
│ News's Grasp/News-   │          │ - hideki@gmail.com  │
│ Grasp/digest/        │          │ - h2-hiramatsu@nri  │
│ （Runner が直接書込）│          └─────────────────────┘
└──────────────────────┘
```

**Max サブスク内で完結**するため追加課金は発生せず（5h 枠の 15〜25% / 回を消費）、Anthropic Routine のクラウド側障害にも依存しません。

## 5 カテゴリ × 曜日マトリクス

毎日のジャンル構成は以下：

| 曜日 | FX | AI | IT-Consulting | Economy | Game | 計 |
|---|---|---|---|---|---|---|
| 月 | ● | ● | ● | ● | – | 4 |
| 火 | ● | ● | ● | ● | ● | 5 |
| 水 | ● | ● | ● | ● | – | 4 |
| 木 | ● | ● | ● | ● | ● | 5 |
| 金 | ● | ● | ● | ● | – | 4 |
| 土 | ● | ● | ● | – | ● | 4 |
| 日 | ● | ● | ● | – | ● | 4 |

各カテゴリ **10 記事 / 日**、合計 **40〜50 記事 / 日**。

### カテゴリ別アクセントカラー

| ID | 日本語 | 英名 | 色 | グリフ |
|---|---|---|---|---|
| `fx` | 為替 | Foreign Exchange | `#B8860B`（琥珀） | `¥` |
| `ai` | AI | Artificial Intelligence | `#2D5BB8`（電子青） | `◆` |
| `it` | IT-Consulting | IT & Consulting | `#2E6B52`（苔緑） | `▲` |
| `economy` | 経済 | Economy | `#8E2A19`（深紅） | `■` |
| `game` | ゲーム | Gaming | `#5E3D8C`（洋紫） | `●` |

## ディレクトリ構造

```
News-Grasp/
├── README.md                # 本ドキュメント
├── SETUP.md                 # 初期セットアップ手順
├── .gitignore
├── digest/                  # ★ 日次レポート（Runner が生成）
│   ├── FX/         ┐
│   ├── AI/          ├─ {YYYY-MM-DD}-{Genre}.md
│   ├── IT-Consulting/
│   ├── Economy/
│   ├── Game/       ┘
│   └── Summary/
│       └── {YYYY-MM-DD}.md  # 当日のテーマ考察ハブ
├── data/
│   ├── watchlist.md         # ★ ユーザー編集可：トラッキング対象
│   ├── articles.jsonl       # 過去 90 日の記事メタ（自動ローテート）
│   ├── archive/             # 90 日超のアーカイブ
│   └── _status.md           # 実行ログ
├── prompts/
│   ├── routine-system.md         # ★ Runner の中核プロンプト
│   ├── obsidian-tagging-spec.md  # ★ Obsidian タグ生成ルール（階層タグ仕様）
│   ├── email-template.html       # メール HTML テンプレート
│   └── obsidian-template.md      # Obsidian Markdown テンプレート
├── assets/                  # OGP 不足時の NG プレースホルダ（v2、計 10 JPG）
│   ├── ng-thumb-{cat}.jpg          # FEATURED 横長 1136×400
│   └── ng-thumb-common-{cat}.jpg   # サイドサムネ 280×180
└── tests/
    ├── README.md
    ├── mock_data.py         # サンプルデータ
    └── render_email.py      # 単体テスト用 HTML レンダラー
```

## デザインシステム

### タイポグラフィ

- 本文：**Noto Serif JP**（明朝、Yu Mincho へフォールバック）
- メタ：**JetBrains Mono**（Menlo へフォールバック）
- 欧文：**Inter**

### 強調記法（プロンプトに明示）

| マーカー | 表示 | 使い所 |
|---|---|---|
| `[[キーワード]]` | 太字 + アクセント色背景 | 固有名詞・数字・主役の動詞句（記事あたり 2〜4 箇所） |
| `__重要文__` | 下線 + 太字 | 解釈・含意の核（段落あたり 1〜2 箇所） |

### サムネイル運用

| 表示枠 | 取得試行 | フォールバック |
|---|---|---|
| FEATURED（568×200、TOP 記事） | `WebFetch` で OGP 画像取得 | `assets/ng-thumb-{cat}.jpg`（カテゴリ別キービジュアル） |
| サイドサムネ（140×90、2 件目以降） | 同上 | `assets/ng-thumb-common-{cat}.jpg`（共通系・カテゴリ色違い） |

メール HTML には **base64 data URI** で埋め込み（プライベート repo の raw URL は受信側からアクセス不可なため）。

### テーマ考察構成

- **タイトル + サブタイトル**：当日の通底テーマ（10〜20 字）
- **LEAD**：5 カテゴリ横断の最重要テーマ（金色帯付き）
- **PULL QUOTE**：象徴的な一文（大型タイポ）
- **§01〜§05 セクション**：総論 / 為替・経済 / AI・技術 / 産業・業界 / 明日へ（各 150〜250 字）
- **KEY TAKEAWAYS**：3 カードで結論
- **RELATED ISSUES**：過去号への wiki link

総量 **800〜1200 字目安**。

## 運用フロー

| ステップ | 担当 | 頻度 |
|---|---|---|
| watchlist 編集 | ユーザー（`data/watchlist.md` を vim/Obsidian で編集 → push） | 必要に応じて |
| Runner 起動 | Windows タスクスケジューラ | 毎朝 06:00 JST |
| digest 生成・push | Claude Code (Sonnet 4.6) | 毎朝 06:00〜06:15 JST |
| Obsidian 同期 | Runner 内で `git pull` 実施済み（git push と同 repo） | 自動 |
| メール受信確認 | ユーザー（Gmail で開く） | 毎朝 |
| メールテンプレ修正 | `prompts/email-template.html` を編集 → `python tests/render_email.py` でプレビュー | 必要に応じて |

## 単体テスト

`tests/render_email.py` で **Claude を呼ばずに** メールテンプレートだけ確認可：

```powershell
# A: ローカルプレビュー（送信なし、$0、1〜2 秒）
python tests/render_email.py
# → tests/output/preview.html を吐く

# C: Webhook 経由でテスト送信（自分宛のみ、$0、5〜10 秒）
python tests/render_email.py --send
```

詳細は [`tests/README.md`](tests/README.md) 参照。

## コスト

| 項目 | 月額 |
|---|---|
| Anthropic Sonnet 4.6 実行料 | **$0**（Max サブスク内） |
| GitHub プライベート repo | **$0** |
| GAS Webhook | **$0**（Workspace 無料枠） |
| メール配信（GmailApp） | **$0** |
| **合計** | **$0** |

5h 枠は 1 回の実行で **15〜25% 程度**消費。朝 06:00 実行のため日中の作業と干渉しない。

## Obsidian タグ運用

Runner は記事の要約と同じターンで `entities` / `topics` / `industries` / `events`
を抽出し、**階層タグ**（`cat/`, `co/`, `country/`, `svc/`, `person/`, `ticker/`,
`topic/`, `industry/`, `event/`, `score/`）として `digest/` 配下の frontmatter と
記事カード行内、`data/articles.jsonl` に展開する。タグ値は**日本語優先**（英字
固有名詞 OpenAI / NVIDIA 等はそのまま）、半角スペース・スラッシュ・ピリオドは
それぞれ `-` / 削除 / `_` に置換する。詳細は
[prompts/obsidian-tagging-spec.md](prompts/obsidian-tagging-spec.md) を正本とする。

例：
```yaml
tags:
  - daily
  - newsletter
  - news-grasp
  - issue-20260428
  - cat/ai
  - co/Anthropic
  - co/OpenAI
  - country/米国
  - person/Sarah-Friar
  - svc/Claude
  - svc/GPT-5_5         # ピリオドはアンダースコア化
  - ticker/USDJPY       # スラッシュは削除
  - topic/AIエージェント
  - score/高
```

## 関連ドキュメント

- [SETUP.md](SETUP.md) — 初期セットアップ手順（タスクスケジューラ・GAS Webhook・watchlist 等）
- [prompts/routine-system.md](prompts/routine-system.md) — Runner の中核プロンプト
- [prompts/obsidian-tagging-spec.md](prompts/obsidian-tagging-spec.md) — Obsidian タグ階層仕様（正本）
- [tests/README.md](tests/README.md) — 単体テストの使い方

## 補足

- **採用しなかった方針**: ① ローカル cron（PC オン依存）/ ② Anthropic Routine（クラウドコンテナのプロビジョニング不安定で 1 時間以上ハング）/ ③ Hook + claude-mem / ④ GitHub Actions cron + API → 最終的に **D 案（ローカル Claude Code via Windows タスク）** で安定運用に到達
- **採用しているが意識してほしい点**: 強調記法 `[[]]` `__` は機械処理用のマーカーではなく **HTML レンダリング時に視覚強調に変換される** ためのプロンプト規約
- **デザイン由来**: メールテンプレートは Claude Design (claude.ai/design) で作成した「News Grasp Template」を実装ベースに、サムネイル v2（10 JPG）を後から差し込んだもの
