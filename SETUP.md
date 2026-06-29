# News-Grasp セットアップ手順

D 案（ローカル Claude Code via Windows タスクスケジューラ）の最終版手順。すでに本番運用中なので、これは **新規環境への再構築** や **誰かに引き継ぐとき** のリファレンスとして使う。

## 確定構成

| 項目 | 値 |
|---|---|
| 実行方式 | Windows タスクスケジューラ → `news-grasp-runner.ps1` → `claude.exe --print` |
| 実行時刻 | 毎朝 06:00 JST |
| モデル | Claude Sonnet 4.6（Max サブスク内認証） |
| GitHub repo（記事本体） | `HIDEPON-UMG/News-Grasp`（プライベート、Runner が直接 commit/push） |
| GitHub repo（Vault root） | `HIDEPON-UMG/obsidian-newsgrasp-vault`（プライベート、Obsidian Git プラグインが同期。2026-05-21〜） |
| Obsidian ボルト | `C:\Users\hidek\Obsidian\New's Grasp\` |
| Repo の実体 | `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp\`（**2026-05-28 に Vault 内から ProjectFolders へ物理移管**。旧構成は Vault 直下にネストしていた） |
| Obsidian で digest を見る経路 | 旧 Vault パス `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp` を実体へのディレクトリ junction にして表示（移管後の再リンク方式） |
| Vault 同期方式 | **Obsidian Git プラグイン**（Remotely Save は 2026-05-21 に廃止） |
| 配信経路 | 公開 Web (GitHub Pages + PWA) + Web Push 通知 (Cloudflare Worker + KV) |
| 旧メール配信 | **2026-06-05 廃止**（旧 `tools/send_email.py` Gmail SMTP 直送と旧 GAS Webhook 経路は機能ごと削除済み。`~/.secrets/news-grasp-smtp.txt` と `news.grasp.magazine@gmail.com` アカウントは未使用化、ユーザー手動で App Password 失効・アカウント整理） |
| 旧 Remotely Save 同期 | **2026-05-21 で廃止**（GitHub 経由 Obsidian Git に切替） |

## 0. 前提環境

- Windows 11
- Git for Windows（`C:\Program Files\Git\cmd\git.exe`）
- Claude Code CLI（`C:\Users\hidek\.local\bin\claude.exe`、Max サブスクで認証済み）
- Python 3.13+（テスト実行用）
- `gh` CLI（HIDEPON-UMG でログイン済み）
- Obsidian Desktop（任意・閲覧用）

## 1. リポジトリの clone（2 段階）

### 1-A. Vault root リポジトリの clone

まず Vault root を `HIDEPON-UMG/obsidian-newsgrasp-vault` から取得する。
これには `.obsidian/` 配下の設定・テーマ・スニペット（`news-grasp.css` 含む）と、
Vault ルート直下のメモが含まれる。

```powershell
cd "C:\Users\hidek\Obsidian"
gh repo clone HIDEPON-UMG/obsidian-newsgrasp-vault "New's Grasp"
```

> **注意**: フォルダ名 `New's Grasp` はアポストロフィ + スペースを含む。
> PowerShell ではダブルクォート必須。git 操作は `git -C "..."` 形式で安全。

### 1-B. News-Grasp サブリポジトリの clone

次に Vault root の下に **記事・Runner プロンプトを持つ本リポジトリ**を clone する。
**`News-Grasp/` は Vault root リポの `.gitignore` で除外**されているため、
このサブリポは Vault root とは独立した git 履歴を持つ。

```powershell
cd "C:\Users\hidek\Obsidian\New's Grasp"
gh repo clone HIDEPON-UMG/News-Grasp
```

完了後のディレクトリ構造：

```
C:\Users\hidek\Obsidian\New's Grasp\               # Vault root (obsidian-newsgrasp-vault)
├── .git\                                            # ← Vault root の git
├── .obsidian\
│   ├── snippets\news-grasp.css                      # ← CSS スニペット
│   └── ...
├── ようこそ.md / Cyber.AI.md / 後藤祐二朗.md        # ← Vault root の メモ
└── News-Grasp\                                       # ← サブリポ (HIDEPON-UMG/News-Grasp)
    ├── .git\                                         # ← サブリポの git（独立）
    ├── digest\
    ├── prompts\
    └── ...
```

Runner は **サブリポ側（`News-Grasp\.git`）に対してのみ commit/push** する。
Vault root の同期は別途 Obsidian Git プラグインが担当する（後述）。

## 2. Web Push の VAPID 鍵設定（1 回だけ）

PWA へのプッシュ通知（`tools/send_push.py`）は VAPID 鍵ペアで本人性を担保する。新規環境では 1 回だけ以下を行う。

1. 鍵ペアを生成する。秘密鍵が `~/.secrets/news-grasp-vapid.pem` に保存され、ブラウザ用の公開鍵が表示される：

   ```powershell
   python tools/gen_vapid_keys.py
   ```

2. 表示された公開鍵（base64url の 1 行）を `docs/push.js` の `VAPID_PUBLIC_KEY` 定数に貼る。
3. 鍵を作り直すと既存の全購読が無効化される（全端末の再登録が必要）。`~/.secrets/news-grasp-vapid.pem` が既にある環境では再生成しない。

## 2-B. Web Push 購読ストア Worker のデプロイ（1 回だけ）

読者が「許可」を押すだけで購読を完結させるには、購読情報を保存する受け口が要る。静的サイトには書き込み口が無いため、極小の Cloudflare Worker (+ KV) を 1 つ立てる（無料枠）。

```powershell
cd worker

# 0) 初回のみ: Cloudflare アカウントにログイン（ブラウザが開く）
npx wrangler login

# 1) KV namespace を作成 → 表示された "id" の値だけを wrangler.toml の id に貼る
#    （binding = "SUBS" は変えない。Worker コードが env.SUBS を参照するため）
npx wrangler kv namespace create news-grasp-subs

# 2) デプロイ（Worker 本体を作成）→ 表示された
#    https://news-grasp-push.<subdomain>.workers.dev を控える
#    （初回は workers.dev サブドメインの登録を求められることがある）
npx wrangler deploy

# 3) 受信者リスト取得を守る乱数トークンを Worker に設定する。
#    secret put は Worker が存在してから（= deploy 後）に行う。
npx wrangler secret put LIST_TOKEN
#    プロンプトに乱数を貼る。生成例（リポジトリ直下で）:
#      python -c "import secrets; print(secrets.token_hex(24))"
#    入力した同じ値を Runner 側にも保存する（前後空白は自動除去される）:
#      notepad "$HOME\.secrets\news-grasp-push-token.txt"
```

仕上げ:

1. 控えた Worker URL を `docs/push.js` の `WORKER_URL` 定数に貼る（末尾スラッシュ無し）。
2. 同じ URL を Runner に環境変数 `NEWS_GRASP_PUSH_WORKER_URL` として渡す（`news-grasp-runner.ps1` に `$env:NEWS_GRASP_PUSH_WORKER_URL = "https://..."` を追記）。
3. 動作確認: `python tools/send_push.py --dry-run`（`取得元: worker` と購読者数が表示されれば疎通 OK）。

> ローカル検証だけなら `cd worker && npx wrangler dev`（CF 認証不要・local KV）。`worker/.dev.vars` に `LIST_TOKEN` を置けば `/list` も叩ける。`.dev.vars` は git 管理外。

読者の購読手順は [README.md](README.md) の「Web Push 通知 (PWA)」節を参照。`data/push_subscriptions.secret.json` は Worker 未設定時の手元テスト用 fallback（`*.secret.json` で git 管理外）。

## 3. `news-grasp-runner.ps1` 配置

`%USERPROFILE%\bin\news-grasp-runner.ps1` に PowerShell スクリプトとして配置する。要点:

- 当日 digest 生成は `run_claude_with_timeout.ps1` 経由で、`runner-prompt.md` を `-PromptFile` から stdin に流して実行する。Claude は `md/jsonl` 生成だけを担当し、`git commit` / `git push` / docs 生成 / publish gate は実行しない
- wrapper の claude 既定引数は `--print --output-format stream-json --verbose`（2026-06-10 無出力ハング事故対策）。init / assistant / tool 各イベントが JSONL でログに流れるため、「ハング（init 後沈黙）/ 迷走（tool_use の内訳）/ 生成中（イベント継続）」を日次ログから区別できる。既定退行は `tests/test_runner_wrapper_smoke.py::test_wrapper_default_args_use_stream_json` が物理検知する
- Claude 終了後に ps1 が継続して Content Gate 群 → bounded repair worker (同一失敗 1 回だけ) → `tools/generate_pages.py` → `tools.validate_public_home` → Availability Gate → docs commit → `git push origin main` → `tools/send_push.py` を順に走らせる
- record schema gate (step 2.65) は 2026-06-12 から `tools.validate_record --recent 7 --issue-date <号日>` で実行する。当日 `seen_at` のレコードが `date != 号日` のとき fatal（06-11 号の 21 件誤記 = 「date を記事公開日と誤解釈」する class of bugs の機械検査）
- Content Gate が収束しない場合、本日号は通常公開せず typed Red で停止する。通常日次バッチ経路の fallback publish は禁止し、手動緊急公開が必要な場合だけ別承認の例外経路として扱う
- wrapper は hard timeout に加えて idle timeout を持つ。標準設定では `TimeoutSec=4800`、`IdleTimeoutSec=900`。stream-json 既定化により「動いている限り stdout が継続する」前提が成立するため、15 分無出力 = 真のハングとして 80 分の hard timeout を待たずに kill する。heartbeat は途中ログに elapsed/idle 秒を残す
- gate failed 後の復旧は `-RecoverOnly` を使う。Claude / DeepDive を再実行せず、手修正済みのローカル状態から gate 群 → docs 再生成 → docs commit → push → Web Push だけを再開する
- 手動慣らし運転や Codex からの実行では `news-grasp-runner.ps1` を foreground で直接待たない。`watch-news-grasp-runner.ps1` が runner を hidden 起動し、`news-grasp-runner-state.json` と日次ログを poll して `ok` / typed Red / stale を機械判定する。`fallback_ok` は通常完走の終端状態に含めない
- 手動公開で runner を通さない場合は `python tools/publish_update.py` を使う。Web Push 通知が必要な更新だけ `--notify` を付ける（微細修正では付けない）
- ファイルは **UTF-8 BOM 必須**（PS5.1 が BOM 無しを CP932 解釈して日本語コメントごと壊す既知問題。`enforce_script_encoding.ps1` hook が自動付与する）

### 3-1. 通常公開完了条件

News-Grasp の自走修正で「直った」と言ってよいのは、Activation Path 全体が通ったときだけ。
fallback_ok / published_fallback_with_notice は通常公開完了条件ではない。旧 fallback 証跡を読む場合は、歴史データまたは手動緊急公開の痕跡として扱い、通常完走に昇格しない。
上流契約で防げる漏れを高コスト E2E に委ねない。E2E は省略せず必要な統合検証として残すが、E2E を設計漏れのバグ発見機として濫用しない。E2E が見つけた前提漏れは runner / watcher / prompt / publish の責務境界、静的契約、文面契約、bounded dry-run へ戻して固定する。

通常公開完了条件:

- live runner と repo runner の checksum 一致を確認する
- Task Scheduler が指す live runner で起動する
- agent prompt が git / docs 生成 / publish / bare python を担当しない
- repair worker が runner_python 以外の bare `python` / `py` / `uv` / git / 広域検索へ逃げない
- `digest/Summary/YYYY-MM-DD.md` と各カテゴリ digest が生成される
- `docs/YYYY-MM-DD/index.html` と `docs/YYYY-MM-DD/summary/index.html` が生成される
- docs/publish-status.json の published_ok と当日日付を確認する
- remote HEAD と local HEAD が一致する
- 公開 URL の sentinel で当日日付が見えることを確認する
- Web Push は付随機能。送信失敗だけで通常公開を失敗扱いにしないが、失敗はログに残す

## 4. Windows タスクスケジューラ登録

| 設定 | 値 |
|---|---|
| 名前 | `News-Grasp Runner` |
| 全般 | **「ユーザーがログオンしている時にだけ実行する」** を選択（claude の OAuth キーチェーン認証のため） |
| トリガー | 毎日 06:00:00 |
| 操作 | プログラム開始：`pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\hidek\bin\news-grasp-runner.ps1"` |
| 条件 | 「タスクを実行するためにスリープを解除する」チェック、AC 電源条件のチェック外す |
| 設定 | 失敗時 5 分間隔で 3 回リトライ |

## 5. 動作確認

```powershell
# Runner の手動起動（実機の本番フロー）
# タスクスケジューラから「News-Grasp Runner」を右クリック → 「実行する」
```

ログは `C:\Users\hidek\bin\news-grasp-logs\YYYY-MM-DD.log` に出る。Codex / 手動慣らしでは foreground 直実行ではなく watcher を使う：

```powershell
# 起動だけ行い、以後は短い status poll で完了検知する（Codex 推奨）
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\bin\watch-news-grasp-runner.ps1" -StartOnly
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\bin\watch-news-grasp-runner.ps1" -Status

# 手元で最後まで待つ場合（終端マーカーを検知して JSON を返す）
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\bin\watch-news-grasp-runner.ps1" -Start -PollSeconds 30 -StaleMinutes 15 -TimeoutMinutes 120
```

成功条件：

- `digest/{Genre}/{YYYY-MM-DD}-{Genre}.md` 各ファイルが repo に push されている
- `digest/Summary/{YYYY-MM-DD}.md` も生成されている
- `data/articles.jsonl` に 20〜25 件のメタが追記されている
- 公開 Web (GitHub Pages) に当日分が反映されている
- Web Push 通知が購読端末に届いている

## 6. watchlist の更新（運用作業）

トラッキング対象を増減するときは `data/watchlist.md` を編集して push する：

```powershell
# Obsidian で開いて編集 → 通常の git
cd "C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp"
git add data/watchlist.md
git commit -m "watchlist: ${変更概要}"
git push
```

翌朝の Runner から自動的に新しい watchlist が反映される。

## 7. トラブルシューティング

| 症状 | 対処 |
|---|---|
| 黒い画面が開いてすぐ閉じる | ログに `ERROR: ...` が出る → `news-grasp-invoked.log` から原因切り分け |
| `claude` が見つからない | ps1 内のフルパスを `where claude` で確認した結果に差し替え |
| 画像が壊れる（公開 Web 内） | `assets/` の JPG が repo にあるか確認 → `tools/generate_pages.py` の thumb fallback 処理を確認 |
| `public HTML gate failed` | runner が targeted repair worker に戻す。収束しなければ通常号を出さず typed Red で停止する。手動調査は `python -m tools.validate_public_home --date YYYY-MM-DD` |
| `claude TIMEOUT` / `IDLE TIMEOUT` | ログの `PromptFile loaded`、`WorkingDirectory resolved`、heartbeat / elapsed 秒数を確認。partial artifacts は未検証なので通常公開せず、必要なら手修正後に `-RecoverOnly` |
| pre-push gate / `generate_pages.py` 失敗 | runner が deterministic repair を試し、収束しなければ typed Red で停止する。`failed_before_push` は secret/security/破壊的リスク時だけを原則にする |
| `summary reflection gate failed` | runner が selected issue artifacts だけを対象に repair worker を呼ぶ。収束しなければ typed Red と同一 `failure_signature` を `data/gate_attempts/YYYY-MM-DD.json` に残す |
| 手動 push 後に Web Push が届かない | runner 外の `git push` では `tools/send_push.py` が走らない。手動公開は `python tools/publish_update.py` を使い、通知が必要な更新だけ `--notify` を付ける |
| Web Push が届かない | `python tools/send_push.py --dry-run` で疎通確認 → Worker URL / `LIST_TOKEN` / VAPID 公開鍵の食い違いを点検 |
| Obsidian で digest（記事 .md）が反映されない | サブリポ `News-Grasp/` 担当の Runner が `git pull` を内包しているので、サブリポは自動更新。手動なら `git -C "...\News-Grasp" pull` |
| Obsidian の Vault 設定（テーマ・CSS スニペット）が反映されない | 親 Vault リポ `obsidian-newsgrasp-vault` 担当の Obsidian Git が auto-pull (10 分) で更新。起動時 pull を ON にしていれば即時取得。手動なら `Ctrl + P` → 「Obsidian Git: Pull」 |
| 親 Vault リポとサブリポの責任が分からない | 親（`obsidian-newsgrasp-vault`）= Vault 設定・CSS スニペット・ルートメモ。サブ（`News-Grasp`）= 記事 .md・Runner プロンプト・テスト。Vault root の `.gitignore` で `News-Grasp/` を除外しているので 2 つの git 履歴は混ざらない |

## 8. Obsidian Git プラグインのセットアップ（Vault root 同期）

2026-05-21 に Vault root の同期を **Remotely Save から Obsidian Git に切替**。Vault root リポ `HIDEPON-UMG/obsidian-newsgrasp-vault` を起動時 pull + 10 分間隔 commit-and-sync で運用する。

### 8-1. プラグイン導入

1. Obsidian → 設定 → コミュニティプラグイン → 「閲覧」 → 検索 `obsidian-git`
2. 著者 **Vinzent03** の **Obsidian Git** を **インストール** → **有効化**

### 8-2. 推奨設定

設定 → Obsidian Git → 「Automatic」セクション：

| 設定項目 | 推奨値 | 補足 |
|---|---|---|
| Split timers for automatic commit and sync | OFF | commit と push を一体運用 |
| Auto commit-and-sync interval (minutes) | `10` | 10 分ごとに自動 commit + push |
| Auto commit-and-sync after stopping file edits | ON | 編集中はコミット保留 |
| Auto commit-and-sync after latest commit | ON | 手動コミット直後の二重発火を防ぐ |
| Auto pull interval (minutes) | `10` | 他端末からの変更を取り込み |
| Auto commit-and-sync only staged files | OFF | 変更ファイルを全自動 add |
| Specify custom commit message on auto commit-and-sync | OFF | ポップアップで作業を止めない |
| Commit message on auto commit-and-sync | `vault backup: {{date}}` または `vault: {{date}} sync` | 任意 |

「General」または該当セクション：

| 設定項目 | 推奨値 | 補足 |
|---|---|---|
| Pull updates on startup | ON | Obsidian 起動時に親 Vault リポを最新化 |

### 8-3. 認証

- 私の確認時点では Windows の **Git Credential Manager** が `gh auth login` 由来のトークンを保持しており、Obsidian Git は追加設定なしで push できた
- もし「commit-and-sync」コマンドが「Push failed: authentication ...」で止まる場合は、`gh auth setup-git` で credential.helper を再セットアップするか、Obsidian Git の Settings → Authentication に PAT を直接登録する

### 8-4. 動作確認

Obsidian で `Ctrl + P` → `Obsidian Git: Commit-and-sync` を手動実行。
画面右下に「Successfully committed and synced」のトーストが出れば OK。
新環境では初回のみ手動で発火させて認証を通すと安定する。

### 8-5. Remotely Save の停止（旧環境からの移行時のみ）

旧 PC からの移行時は、上記 8-1〜8-4 で Obsidian Git が動くことを確認した **後で** 以下を実施：

1. 設定 → コミュニティプラグイン → `Remotely Save` を **無効化**
2. 同画面で `Remotely Save` を **アンインストール**

> **重要**: `.obsidian/plugins/remotely-save/data.json` には S3/WebDAV/OneDrive 等の同期先資格情報が含まれる。Vault root リポの `.gitignore` で除外済みだが、念のため新規環境では同フォルダごと残さない方が安全。

## 9. 関連ドキュメント

- [README.md](README.md) — 全体構成と運用フロー
- [prompts/routine-system.md](prompts/routine-system.md) — Runner プロンプトの完全仕様
- [prompts/obsidian-tagging-spec.md](prompts/obsidian-tagging-spec.md) — Obsidian タグ階層仕様（正本）
- [prompts/obsidian-template.md](prompts/obsidian-template.md) — Obsidian Markdown テンプレート + **CSS スニペット連動の必須要素契約**（2026-05-21〜）
- [docs/architecture.pptx](docs/architecture.pptx) — アーキテクチャ図と仕様まとめ（プレゼン用）
- [memory/reference_newsgrasp_vault_github_sync.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/reference_newsgrasp_vault_github_sync.md) — Vault root GitHub 同期方式と除外ルール
