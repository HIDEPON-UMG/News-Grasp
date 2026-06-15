# News Grasp

> 時勢を掴み、日々に新たに。

毎朝 06:30 JST にローカル PC 上の Claude Code (Sonnet 4.6) が起動し、watchlist の対象を Web 検索 → 過去 90 日の関連記事と照合 → カテゴリ別 digest Markdown を生成 → GitHub に commit & push → 公開 web (GitHub Pages + PWA) に SSG で配信、までを自律実行する **個人運用の日次ニュースダイジェスト**。

配信は **公開 Web (GitHub Pages + PWA) + Web Push 通知** のみ。旧 Gmail SMTP 直送によるメール配信は 2026-06-05 に機能ごと廃止済み。

## 公開 web

Magazine Spread デザイン (navy / cream / gold + 角丸 0 + Noto Serif JP × Inter × JetBrains Mono の 3 フォント鼎立)。PWA としてホーム画面追加可、モバイル幅でも崩れない。4 階層の URL 体系を持つ。

| 階層 | URL | パターン | 役割 |
|---|---|---|---|
| Home | `/` | **Variant B** (Editorial Landing) | サイトの顔。Today's Theme 76px ハイライト見出し + Editor's Top 3 + Featured Story + 5 lens カテゴリグリッド + Editorial preview |
| Daily Overview | `/{YYYY-MM-DD}/` | **Pattern C** (Category Overview) | 1 日分の俯瞰。Page header (DAILY OVERVIEW + 56px 日付) → Theme banner → Category rows × 5 (KV + Top 3 + Score histogram) |
| Editorial Summary | `/{YYYY-MM-DD}/summary/` | **Pattern D** (Summary Only) | 1 日分の長文考察。Dark hero + Pull quote + 7 § sections (総論/為替/AI/IT/経済/ゲーム/明日へ) + Key Takeaways × 3 |
| Category Detail | `/{cat}/{YYYY-MM-DD}/` | **Variant B Magazine** | 1 カテゴリの詳細。480px Hero KV + TOP STORY + More stories + Editorial reflection |
| Category Archive | `/{fx,ai,it,economy,game,summary}/` | 旧スタイル | カテゴリの全 digest 一覧 |
| 日付横断 Archive | `/archive/` | **Editorial Timeline** | 全カテゴリ × 全日付。号ごとのカード (日付レール + リード記事 + レンズ別トップ + スコア) を縦タイムラインで降順表示。カテゴリ絞り込み / 検索 / 月ジャンプ付き |

SSG は `tools/generate_pages.py` (Jinja2)。`docs/` 配下に静的 HTML を生成し GitHub Pages で配信する。Daily Overview と Editorial Summary は γ schema の `reflection` ブロック (`prompts/routine-system.md` ステップ 4 参照) が digest に入っていればリッチに、無ければ fallback (lead = summary_text / takeaways = Top 3 / sections = 各カテゴリ Top 1 + 総論/明日へプレースホルダ) で必ず描画する。OGP メタは 1120×587 (1.91:1) の og:image + 180 字以内 og:description + summary_large_image card を全ページ出力。PWA メタ (manifest / theme-color / apple-touch-icon / service worker) は `prompts/_partials/pwa-head.html` を Jinja include。デザインシステムは [`DESIGN.md`](DESIGN.md) を一次ソースとする。詳細仕様は [`docs/specs/2026-05-21_public-web-ogp.html`](docs/specs/2026-05-21_public-web-ogp.html)。

### モバイル対応 (2026-05-26〜)

- Editorial Summary の § セクション記号は 768px 以下で stats 右側の余白に絶対配置 (`.summary-hero__sigil` を `position: absolute; right:0; bottom:0`)
- 「7 つの § セクション」見出しは 768px 以下で「7 つの」「§ セクション」の 2 行に強制改行
- Home の subscribe band は「毎朝 6:30 更新 / 土日祝日も毎朝公開」に統一 (メール購読を前提とした旧表現は撤去)

## アーキテクチャ概要 (ローカル Codex runner + dead-man)

```
┌──────────────────────────────────────────────────────────────────┐
│ Windows タスクスケジューラ「News-Grasp Runner」 06:30 JST 毎日   │
│   └─→ %USERPROFILE%\bin\news-grasp-runner.ps1                    │
│         ├─ repo 管理版 scripts/ops/news-grasp-runner.ps1 と同期確認 │
│         ├─ git fetch / pull origin main                           │
│         └─ codex exec による newsroom fan-out / editor 統合         │
│ Windows タスクスケジューラ「News-Grasp Deadman」 1 時間ごと       │
│   └─→ %USERPROFILE%\bin\news-grasp-deadman.ps1                    │
│         └─ stale / no ok / publish_failed を検知し RecoverOnly 起動 │
└──────────────────────────────────────────────────────────────────┘
                          │
                  runner が以下を段階実行
                          │
   ┌────────────────────────────────────────────────────────────┐
   │ ① 当日 (JST) の対象カテゴリを曜日マトリクスで決定          │
   │ ② watchlist.md / articles.jsonl / prompts/* を Read         │
   │ ③ WebSearch でカテゴリごとに 5 記事を厳選                  │
   │ ④ WebFetch で OGP 画像を取得 (失敗時は NG プレースホルダ)  │
   │ ⑤ 過去 90 日の articles.jsonl と関連付け (5 軸)             │
   │ ⑥ digest Markdown を生成 (カテゴリ別 + Summary)             │
   │ ⑦ git commit (push は ps1 が代行)                          │
   └────────────────────────────────────────────────────────────┘
                          │
                  Claude 終了後に ps1 が継続
                          │
   ┌────────────────────────────────────────────────────────────┐
   │ A. git push origin main                                     │
   │ B. tools/generate_pages.py で SSG → docs/ 出力 → push       │
   │ C. tools/send_push.py で Web Push 通知配信                  │
   └────────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┴────────────────────┐
        ▼                                      ▼
┌──────────────┐                  ┌──────────────────────────────┐
│ GitHub       │                  │ Cloudflare Worker + KV       │
│ News-Grasp   │                  │ (Web Push 購読ストア)         │
│  (private)   │                  │ docs/sw.js が受信し通知表示   │
└──────┬───────┘                  └──────────────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Obsidian Vault               │
│ ├── News-Grasp/digest/       │
│ │   (Runner が直接書込・     │
│ │    本サブリポを git 同期)  │
│ └── .obsidian/snippets/      │
│     news-grasp.css 他        │
│     (Obsidian Git で別の     │
│      private repo へ同期、   │
│      2026-05-21〜)           │
└──────────────────────────────┘
```

**Anthropic Max サブスク内で完結** するため追加課金は発生せず (5h 枠の 15〜25% / 回を消費)、Anthropic Routine のクラウド側障害にも依存しない。

## 7 カテゴリ × 曜日マトリクス

毎日のジャンル構成は以下（FX・AI・IT・Mobility は毎日固定、Manufacturing・Economy は平日のみ、Game は火木土日のみ）:

| 曜日 | FX | AI | IT-Consulting | Mobility | Manufacturing | Economy | Game | 計 |
|---|---|---|---|---|---|---|---|---|
| 月 | ● | ● | ● | ● | ● | ● | – | 6 |
| 火 | ● | ● | ● | ● | ● | ● | ● | 7 |
| 水 | ● | ● | ● | ● | ● | ● | – | 6 |
| 木 | ● | ● | ● | ● | ● | ● | ● | 7 |
| 金 | ● | ● | ● | ● | ● | ● | – | 6 |
| 土 | ● | ● | ● | ● | – | – | ● | 5 |
| 日 | ● | ● | ● | ● | – | – | ● | 5 |

各カテゴリ **5 記事 / 日**、合計 **25〜30 記事 / 日**。土日祝日も休まず公開。なお PWA のプッシュ通知本文も、この表に従ってその日に配信されるカテゴリだけを列挙する（`tools/send_push.py` の `_PUBLISH_SCHEDULE`）。

### カテゴリ別アクセントカラー

| ID | 日本語 | 英名 | 色 | グリフ |
|---|---|---|---|---|
| `fx` | 為替 | Foreign Exchange | `#B8860B` (琥珀) | `¥` |
| `ai` | AI | Artificial Intelligence | `#2D5BB8` (電子青) | `◆` |
| `it` | IT-Consulting | IT & Consulting | `#2E6B52` (苔緑) | `▲` |
| `mobility` | モビリティ | Mobility | `#3A7B8C` (ティール) | `◎` |
| `manufacturing` | 製造 | Manufacturing | `#5A6B7B` (スチールグレー) | `⬢` |
| `economy` | 経済 | Economy | `#8E2A19` (深紅) | `■` |
| `game` | ゲーム | Gaming | `#5E3D8C` (洋紫) | `●` |

## Web Push 通知 (PWA / 2026-05-29〜)

スマホのホーム画面に追加した PWA へ、毎朝の更新を「本日のダイジェストを公開しました。読んでみて！」とプッシュ通知で届ける。**読者は「通知を受け取る」を押して許可するだけで購読が完了する**（手動コピーや管理人への連絡は不要）。もう一度押せば購読解除。

### 仕組み (受信・購読保存・送信)

GitHub Pages は読み取り専用の静的サイトで購読情報を保存できないため、保存だけを**極小の Cloudflare Worker (+ KV)** が担う。Worker には VAPID 秘密鍵を置かず、保持する秘密は受信者リストを守る `LIST_TOKEN` のみ。

| 要素 | 実装 | 置き場所 |
|---|---|---|
| 受信 | Service Worker の `push` / `notificationclick` ハンドラ | `docs/sw.js` |
| 購読 UI | Home の「通知を受け取る」ボタン → 許可 → 購読情報を Worker に自動 POST | `docs/push.js` + `prompts/index-template.html` |
| 購読保存 | `POST /subscribe` で KV 保存・`POST /unsubscribe` で削除・`GET /list?token=` で一覧 | `worker/src/index.js` (Cloudflare Worker + KV) |
| 送信 | 毎朝の Runner が `tools/send_push.py` で Worker から一覧取得 → pywebpush 送信 | Runner ステップ 7 |

VAPID 方式（送信側が秘密鍵で署名、ブラウザが公開鍵で検証）で本人性を担保する。秘密鍵は `~/.secrets/news-grasp-vapid.pem`（repo 外）、ブラウザ用の公開鍵は `docs/push.js` の `VAPID_PUBLIC_KEY` 定数に埋め込む。

### 初期セットアップ (管理人が 1 回だけ)

詳細手順は [SETUP.md](SETUP.md) の「VAPID 鍵」「Worker デプロイ」を参照。要点:

1. `python tools/gen_vapid_keys.py` で VAPID 鍵を生成し、表示された公開鍵を `docs/push.js` の `VAPID_PUBLIC_KEY` に貼る。
2. `cd worker && npx wrangler kv namespace create news-grasp-subs`（→ id を `worker/wrangler.toml` に貼る）、`npx wrangler secret put LIST_TOKEN`（乱数トークン。同じ値を `~/.secrets/news-grasp-push-token.txt` にも保存）、`npx wrangler deploy`。
3. デプロイで表示された `*.workers.dev` の URL を `docs/push.js` の `WORKER_URL` に貼り、Runner には環境変数 `NEWS_GRASP_PUSH_WORKER_URL` として渡す。

### 読者の購読手順 (ユーザー操作だけで完結)

1. iPhone / iPad は、Safari で公開 Web を開き **「ホーム画面に追加」して PWA として開き直す**（iOS は Safari タブのままでは Web Push を受け取れない仕様）。Android Chrome はタブのままでよい。
2. Home の「**スマホに更新通知を受け取る**」を押し、通知を許可する。**これで完了**（購読は自動で Worker に保存される）。

### 運用上の約束

- `tools/send_push.py` は購読者が 0 人でも秘密鍵が無くても **exit 0** で、毎朝の digest 生成・公開を絶対に止めない（push は付随機能）。Worker に繋がらない一時障害も警告して skip（exit 0）し、`LIST_TOKEN` 不一致のときだけ exit 1 で表面化する。
- 失効した購読（HTTP 404/410）は送信時に自動検出し、Worker の `/unsubscribe` で除去する。
- 購読保存先は Worker (KV) が本番。`data/push_subscriptions.secret.json` は管理人の手元テスト用 fallback（`*.secret.json` で git 管理外）。
- 送信は **TTL 12 時間**で行う（`DEFAULT_TTL_SECONDS`）。pywebpush の既定 `ttl=0` は「送信時に端末がオフラインなら破棄（かつ 201 を返す）」ため、朝 06:35 にスリープ中のスマホへ毎朝 silently 破棄され「3/3 送信成功なのに通知が来ない」が起きていた（2026-06-01 修正）。TTL を持たせ FCM/APNs が端末再接続まで保持→当日中に配信する。`tests/test_send_push.py::test_send_one_passes_positive_ttl` が `ttl=0` への退行を契約で封じる。
- 送信は **`Urgency: high`** を付ける（`DEFAULT_URGENCY`）。ヘッダ省略時の既定 **normal** は、端末が省電力状態（Android の Doze / iOS の低電力・スリープ）だと OS が配信を後回し（バッチ）にするため、一晩アイドルだった朝 06:38 の通知が **FCM/APNs は 201 受理しても端末画面に出ない**（日中の手動送信は端末がアクティブなので即届く＝「手動は届くが毎朝来ない」の非対称性の正体。2026-06-02 修正）。`high` は FCM 優先度 high / apns-priority 10 にマップされ Doze を貫通して即時配信する。`tests/test_send_push.py::test_send_one_passes_high_urgency` が normal への退行を契約で封じる。なお iOS は urgency=high でも端末側の「集中モード（スリープ/おやすみ）」「通知の要約」「低電力モード」が朝の通知を抑制しうるため、それらは端末側で News Grasp を許可対象にする必要がある。

## ディレクトリ構造

```
News-Grasp/
├── README.md                # 本ドキュメント
├── SETUP.md                 # 初期セットアップ手順
├── DESIGN.md                # デザインシステム一次ソース
├── pyproject.toml           # Python 設定 (ruff banned-api, pytest)
├── .gitignore
├── digest/                  # ★ 日次レポート (Runner が生成)
│   ├── FX/          ┐
│   ├── AI/           ├─ {YYYY-MM-DD}-{Genre}.md
│   ├── IT-Consulting/
│   ├── Mobility/
│   ├── Manufacturing/
│   ├── Economy/
│   ├── Game/        ┘
│   └── Summary/
│       └── {YYYY-MM-DD}.md  # 当日のテーマ考察ハブ
├── data/
│   ├── watchlist.md         # ★ 編集可: トラッキング対象 (人物 / 企業 / 銘柄等)
│   ├── articles.jsonl       # 過去 90 日の記事メタ (自動ローテート)
│   ├── archive/             # 90 日超のアーカイブ
│   └── _status.md           # 実行ログ
├── prompts/
│   ├── routine-system.md         # ★ Runner の中核プロンプト (γ schema を含む)
│   ├── runner-prompt.md          # Runner 起動時の User prompt
│   ├── obsidian-tagging-spec.md  # ★ Obsidian タグ生成ルール (階層タグ仕様)
│   ├── obsidian-template.md      # Obsidian Markdown テンプレート
│   ├── index-template.html       # 公開 Home (Variant B)
│   ├── overview-template.html    # Daily Overview (Pattern C)
│   ├── summary-template.html     # Editorial Summary (Pattern D)
│   ├── category-template.html    # Category Detail (Variant B Magazine)
│   ├── archive-template.html     # Archive (Editorial Timeline)
│   ├── page-template.html        # 旧汎用テンプレート
│   └── _partials/
├── scripts/
│   └── ops/
│       ├── news-grasp-runner.ps1       # repo 管理版 runner
│       ├── watch-news-grasp-runner.ps1 # runner watcher / RecoverOnly 起動
│       ├── news-grasp-deadman.ps1      # dead-man switch
│       └── install-news-grasp-ops.ps1  # %USERPROFILE%\bin へ同期
│       └── pwa-head.html         # PWA <head> include (manifest / theme-color / apple-touch-icon / sw)
├── assets/                  # OGP 不足時の NG プレースホルダ (公開 Web 用、計 14 JPG)
│   ├── ng-thumb-{cat}.jpg          # FEATURED 横長 1136×400
│   └── ng-thumb-common-{cat}.jpg   # サイドサムネ 280×180
├── docs/                    # GitHub Pages 公開先 (SSG 出力)
│   ├── index.html
│   ├── assets/site.css      # 公開サイト CSS (DESIGN.md トークン由来)
│   ├── manifest.webmanifest # PWA manifest
│   ├── sw.js                # service worker (push / notificationclick 含む)
│   ├── push.js              # Web Push 購読クライアント (許可→Worker へ自動 POST)
│   ├── offline.html         # オフライン時 fallback
│   ├── {YYYY-MM-DD}/        # 日次オーバービュー / summary
│   ├── {cat}/               # カテゴリアーカイブと日別詳細
│   └── specs/               # 仕様書 HTML
├── worker/                  # Web Push 購読ストア (Cloudflare Worker + KV)
│   ├── src/index.js         # /subscribe・/unsubscribe・/list (token 保護)
│   └── wrangler.toml        # KV namespace / デプロイ設定
├── tools/
│   ├── generate_pages.py    # SSG (Jinja2) — 全テンプレートを束ねる
│   ├── send_push.py         # Web Push 送信 (Worker から購読取得 → pywebpush)
│   ├── gen_vapid_keys.py    # VAPID 鍵ペア生成 (1 回だけ)
│   ├── fetch_ogp.py         # OGP 画像取得 (urllib + html.parser)
│   ├── append_after_dedup.py # dedup/鮮度ゲート通過後の articles.jsonl 追記
│   ├── append_articles.py   # 旧単発追記スクリプト
│   ├── append_today.py      # 当日分だけ追記
│   ├── build_pwa_icons.py   # favicon / apple-touch-icon / 192/512 PNG 生成
│   ├── dedup.py             # 過去 90 日と照合する重複排除
│   ├── recover_thumbs.py    # サムネ再取得
│   ├── thumb_stats.py       # サムネ取得成功率集計
│   └── config.py            # BASE_URL 等
└── tests/
    ├── test_generate_pages*.py    # SSG E2E / 増分生成
    ├── test_home_variant_b.py     # Home (Variant B) アサーション
    ├── test_overview_pattern_c.py # Daily Overview
    ├── test_summary_pattern_d.py  # Editorial Summary
    ├── test_fetch_ogp*.py         # OGP 取得契約
    ├── test_thumb_contract.py     # articles.jsonl の thumb キー必須契約
    ├── test_dedup.py              # 重複排除
    ├── test_pwa_meta.py           # PWA メタ存在チェック
    ├── test_send_push.py          # Web Push の TTL / Urgency 契約
    └── test_runner_wrapper_smoke.py
```

## デザインシステム

DESIGN.md を一次ソースとし、`docs/assets/site.css` で実装。色 / フォント / 余白 / 角丸は CSS 変数 (`var(--color-*)` 等) 経由で参照し、コミット前に `npx "@google/design.md" lint .\DESIGN.md` を通す運用 (`safe-commit` ゲート 4)。

### タイポグラフィ

- 本文 / 見出し: **Noto Serif JP** (明朝、Yu Mincho へフォールバック)
- メタ / eyebrow: **JetBrains Mono** (Menlo へフォールバック)
- 欧文 / 数値: **Inter**

### 強調記法 (プロンプトに明示)

| マーカー | 表示 | 使い所 |
|---|---|---|
| `[[キーワード]]` | 太字 + アクセント色背景 (`.emph-bold`) | 固有名詞・数字・主役の動詞句 (記事あたり 2〜4 箇所) |
| `__重要文__` | 下線 + 太字 (`.emph-und`) | 解釈・含意の核 (段落あたり 1〜2 箇所) |

<a id="thumbnail"></a>

### サムネイル運用

| 表示枠 | 取得試行 | フォールバック |
|---|---|---|
| FEATURED (568×200、TOP 記事) | `tools/fetch_ogp.py` で OGP 画像取得 | `assets/ng-thumb-{cat}.jpg` (カテゴリ別キービジュアル) |
| サイドサムネ (140×90、2 件目以降) | 同上 | `assets/ng-thumb-common-{cat}.jpg` (共通系・カテゴリ色違い) |

フォールバック画像は公開 Web ページ (`tools/generate_pages.py` 出力の `docs/{cat}/{YYYY-MM-DD}/index.html` 等) でのみ参照される。

### テーマ考察構成 (Editorial Summary)

- **タイトル + サブタイトル**: 当日の通底テーマ (10〜20 字)
- **LEAD**: 5 カテゴリ横断の最重要テーマ (金色帯付き)
- **PULL QUOTE**: 象徴的な一文 (大型タイポ)
- **§01〜§07 セクション**: 総論 / 為替 / AI / IT / 経済 / ゲーム / 明日へ (各 150〜250 字)
- **KEY TAKEAWAYS**: 3 カードで結論
- **RELATED ISSUES**: 過去号への wiki link

総量 **1200〜1800 字目安**。

## 運用フロー

| ステップ | 担当 | 頻度 |
|---|---|---|
| watchlist 編集 | 管理人 (`data/watchlist.md` を編集 → push) | 必要に応じて |
| Runner 起動 | Windows タスクスケジューラ | 毎朝 06:30 JST |
| digest 生成・commit | Claude Code (Sonnet 4.6) | 毎朝 06:30〜06:45 JST |
| pre-push 契約ゲート | Runner 内で 3 段ゲート (① URL liveness ② `[!ja]` 和訳 callout 必須テスト ③ pytest 全件 PASS `NEWS_GRASP_SKIP_URL_CHECK=1`) を強制発火。fail なら push を阻止。「別件」judgement での bypass は禁止 (2026-06-06 plan v2 で B-1 ロックダウン追加。同日セッションで thumb FAIL bypass 事故が発覚) | 自動 |
| build 時層 2 品質ゲート | `tools/output_quality.py` で関係図 SVG の幾何違反 (線がノード貫通) と カテゴリトップ連続同テーマを生成段階で検出。違反時は `OutputQualityError` で `generate_pages.py` を中止し docs/ への書き込みを物理ブロック (2026-06-06 plan v2 で A 軸追加。同日セッションで関係図貫通 / カテゴリ重複の 2 件公開後検知事故への構造解決) | 自動 |
| git push | Runner (Claude 終了後の ps1) | 自動 |
| SSG ビルド | Runner 内で `tools/generate_pages.py` を実行 | 自動 |
| Obsidian 同期 | Runner 内で `git pull/push` | 自動 |
| Web Push 配信 | Runner 内で `tools/send_push.py` を実行 | 自動 |

## 単体テスト

`pytest` で SSG / OGP / サムネ / 重複排除 / PWA メタ / wrapper smoke を検証 (CI なし、ローカルで `pytest -q` 実行)。

## コスト

| 項目 | 月額 |
|---|---|
| Anthropic Sonnet 4.6 実行料 | **$0** (Max サブスク内) |
| GitHub プライベート repo + Pages | **$0** |
| Cloudflare Worker + KV (Web Push 購読ストア) | **$0** (無料枠内) |
| **合計** | **$0** |

5h 枠は 1 回の実行で **15〜25% 程度** 消費。朝 06:30 実行のため日中の作業と干渉しない。

## Obsidian タグ運用

Runner は記事の要約と同じターンで `entities` / `topics` / `industries` / `events` を抽出し、**階層タグ** (`cat/`, `co/`, `country/`, `svc/`, `person/`, `ticker/`, `topic/`, `industry/`, `event/`, `score/`) として `digest/` 配下の frontmatter と記事カード行内、`data/articles.jsonl` に展開する。タグ値は**日本語優先** (英字固有名詞 OpenAI / NVIDIA 等はそのまま)、半角スペース・スラッシュ・ピリオドはそれぞれ `-` / 削除 / `_` に置換する。詳細は [`prompts/obsidian-tagging-spec.md`](prompts/obsidian-tagging-spec.md) を正本とする。

例:

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

- [SETUP.md](SETUP.md) — 初期セットアップ手順 (タスクスケジューラ・watchlist・Web Push 等)
- [DESIGN.md](DESIGN.md) — デザインシステム一次ソース
- [prompts/routine-system.md](prompts/routine-system.md) — Runner の中核プロンプト
- [prompts/obsidian-tagging-spec.md](prompts/obsidian-tagging-spec.md) — Obsidian タグ階層仕様 (正本)
- [docs/specs/2026-05-21_public-web-ogp.html](docs/specs/2026-05-21_public-web-ogp.html) — 公開 Web 仕様書 (HTML 形式)

## 補足

- **採用しなかった方針**: ① ローカル cron (PC オン依存) / ② Anthropic Routine (クラウドコンテナのプロビジョニング不安定で 1 時間以上ハング) / ③ Hook + claude-mem / ④ GitHub Actions cron + API → 最終的に **D 案 (ローカル Claude Code via Windows タスク)** で安定運用に到達
- **強調記法 `[[]]` `__` の意味**: 機械処理用のマーカーではなく **HTML レンダリング時に視覚強調に変換される** ためのプロンプト規約
- **メール配信の廃止 (2026-06-05)**: 旧運用は Gmail SMTP (`tools/send_email.py`) で管理人本人の受信箱に毎朝投函していたが、機能ごと削除。読者向けの正規導線は公開 Web (GitHub Pages + PWA) と Web Push のみ
- **Vault 同期方式 (2026-05-21〜)**: 親 Vault `New's Grasp/` 全体を **Obsidian Git プラグイン** で別 private repo に同期。本リポ (記事本体) は Vault root の `.gitignore` で除外され、Runner が独立に commit/push する 2 リポ構成。旧 Remotely Save 同期は廃止
- **Reading View デザイン (2026-05-21〜)**: 親 Vault の `.obsidian/snippets/news-grasp.css` が digest 記事をニュースカード化 (accent `#497074` 単一トーン)。Runner 出力構造は [`prompts/obsidian-template.md`](prompts/obsidian-template.md) 末尾の「CSS スニペット連動の必須要素」契約に従う
- **PWA 化 (2026-05-25〜)**: `prompts/_partials/pwa-head.html` を全テンプレートに include。`docs/manifest.json` + `docs/sw.js` + `docs/offline.html` でホーム画面追加 / オフライン閲覧に対応。`tools/build_pwa_icons.py` で 192/512 png と apple-touch-icon を生成
- **モバイル対応 (2026-05-26〜)**: 上記「モバイル対応」セクション参照。Editorial Summary の § 配置と見出し改行、Home の subscribe band 文言を整理
