# News-Grasp セットアップ手順

D 案（ローカル Claude Code via Windows タスクスケジューラ）の最終版手順。すでに本番運用中なので、これは **新規環境への再構築** や **誰かに引き継ぐとき** のリファレンスとして使う。

## 確定構成

| 項目 | 値 |
|---|---|
| 実行方式 | Windows タスクスケジューラ → `news-grasp-runner.bat` → `claude.exe --print` |
| 実行時刻 | 毎朝 06:00 JST |
| モデル | Claude Sonnet 4.6（Max サブスク内認証） |
| GitHub repo（記事本体） | `HIDEPON-UMG/News-Grasp`（プライベート、Runner が直接 commit/push） |
| GitHub repo（Vault root） | `HIDEPON-UMG/obsidian-newsgrasp-vault`（プライベート、Obsidian Git プラグインが同期。2026-05-21〜） |
| Obsidian ボルト | `C:\Users\hidek\Obsidian\New's Grasp\` |
| Repo の実体 | `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp\`（**2026-05-28 に Vault 内から ProjectFolders へ物理移管**。旧構成は Vault 直下にネストしていた） |
| Obsidian で digest を見る経路 | 旧 Vault パス `C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp` を実体へのディレクトリ junction にして表示（移管後の再リンク方式） |
| Vault 同期方式 | **Obsidian Git プラグイン**（Remotely Save は 2026-05-21 に廃止） |
| メール送信 | `tools/send_email.py`（Gmail SMTP `smtp.gmail.com:587` STARTTLS） |
| 差出人 | `news.grasp.magazine@gmail.com`（専用アカウント、`tools/send_email.py:35` の `DEFAULT_SENDER` で集約・正本） |
| App Password | `~/.secrets/news-grasp-smtp.txt`（差出人アカウントの Gmail App Password） |
| 配信宛先 | `hideki.kusunoki@gmail.com` / `h2-hiramatsu@nri.co.jp` |
| 旧 GAS Webhook 経路 | **2026-04 末で廃止**（旧 `hidepontrainer@gmail.com` 配下の web app。本番未使用） |
| 旧 Remotely Save 同期 | **2026-05-21 で廃止**（GitHub 経由 Obsidian Git に切替） |

## 0. 前提環境

- Windows 11
- Git for Windows（`C:\Program Files\Git\cmd\git.exe`）
- Claude Code CLI（`C:\Users\hidek\.local\bin\claude.exe`、Max サブスクで認証済み）
- Python 3.13+（テスト実行用）
- `gh` CLI（HIDEPON-UMG でログイン済み）
- Gmail App Password を `~/.secrets/news-grasp-smtp.txt` に保存済み（差出人 `news.grasp.magazine@gmail.com` の 2FA → App Password 発行画面で生成）
- Obsidian Desktop（任意・閲覧用）

> **旧経路**: `clasp` v3.x（`hidepontrainer@gmail.com` 配下の GAS web app `news-grasp-mailer` 管理用）は 2026-04 末で廃止のため新規環境では不要。

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

## 2. Gmail SMTP の認証設定

`tools/send_email.py` は Gmail SMTP（`smtp.gmail.com:587` STARTTLS）で **`news.grasp.magazine@gmail.com` から直送**する。差出人は `tools/send_email.py:35` の `DEFAULT_SENDER` 1 箇所で集約済みなので、本番では `--from` フラグを指定しないこと（指定すると先祖返りリスクがある）。

新規環境でのセットアップ：

1. `news.grasp.magazine@gmail.com` に Google アカウントでログイン → 2 段階認証を有効化
2. <https://myaccount.google.com/apppasswords> で App Password を発行（用途名: `News-Grasp SMTP`）
3. 発行された 16 文字のパスワードを `~/.secrets/news-grasp-smtp.txt` に **改行なしで保存**：

   ```powershell
   New-Item -ItemType Directory -Force -Path "$HOME\.secrets" | Out-Null
   # 16文字のパスワードを貼り付け（末尾改行なし）
   notepad "$HOME\.secrets\news-grasp-smtp.txt"
   ```

4. パーミッション制限（任意・PC 共有時のみ）：エクスプローラ → プロパティ → セキュリティで自分以外をアクセス拒否

> **旧 GAS Webhook 経路**: `hidepontrainer@gmail.com` 配下の `news-grasp-mailer` web app は 2026-04 末で廃止。`tests/render_email.py --send` 内の Webhook 系コードは互換のため残しているが本番は使わない。誤って `--send` を実行すると差出人が旧アドレスに先祖返りするので注意。

## 3. `news-grasp-runner.bat` 配置

`C:\Users\hidek\bin\news-grasp-runner.bat` に以下を配置。**ASCII のみ・CRLF・goto ベース**で書く（Windows の cmd.exe 互換性確保のため）：

```bat
@echo off
echo [%DATE% %TIME%] runner-invoked >> "%USERPROFILE%\bin\news-grasp-invoked.log"

set REPO_DIR=C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp
set LOG_DIR=%USERPROFILE%\bin\news-grasp-logs
set GIT=C:\Program Files\Git\cmd\git.exe
set CLAUDE=C:\Users\hidek\.local\bin\claude.exe

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "tokens=2 delims==" %%i in ('wmic os get LocalDateTime /value 2^>NUL ^| find "="') do set DT=%%i
set DATESTAMP=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%
set LOG=%LOG_DIR%\%DATESTAMP%.log

echo. >> "%LOG%"
echo [%DATE% %TIME%] news-grasp-runner start >> "%LOG%"

if not exist "%REPO_DIR%\.git" goto err_repo
"%GIT%" -C "%REPO_DIR%" fetch --quiet origin main >> "%LOG%" 2>&1
if errorlevel 1 goto err_fetch
"%GIT%" -C "%REPO_DIR%" pull --ff-only origin main >> "%LOG%" 2>&1
if errorlevel 1 goto err_pull

pushd "%REPO_DIR%"
"%CLAUDE%" --print --dangerously-skip-permissions --tools default --model sonnet "本リポジトリ内の prompts/routine-system.md を Read で読み込み、その指示に厳密に従って当日（JST）の News-Grasp 日次 digest を生成してください..." >> "%LOG%" 2>&1
set CLAUDE_RC=%errorlevel%
popd
if not %CLAUDE_RC%==0 goto err_claude

echo [%DATE% %TIME%] news-grasp-runner OK >> "%LOG%"
exit /b 0

:err_repo & echo ERROR: repo not found >> "%LOG%" & exit /b 1
:err_fetch & echo ERROR: fetch failed >> "%LOG%" & exit /b 1
:err_pull & echo ERROR: pull failed >> "%LOG%" & exit /b 1
:err_claude & echo ERROR: claude exited %CLAUDE_RC% >> "%LOG%" & exit /b 1
```

**注意**: 改行コードは **CRLF 必須**。Write tool で書いた直後に：

```bash
sed -i 's/$/\r/' "/c/Users/hidek/bin/news-grasp-runner.bat"
```

## 4. Windows タスクスケジューラ登録

| 設定 | 値 |
|---|---|
| 名前 | `News-Grasp Runner` |
| 全般 | **「ユーザーがログオンしている時にだけ実行する」** を選択（claude の OAuth キーチェーン認証のため） |
| トリガー | 毎日 06:00:00 |
| 操作 | プログラム開始：`C:\Users\hidek\bin\news-grasp-runner.bat` |
| 条件 | 「タスクを実行するためにスリープを解除する」チェック、AC 電源条件のチェック外す |
| 設定 | 失敗時 5 分間隔で 3 回リトライ |

## 5. 動作確認

```powershell
# 疎通テスト（SMTP 直送・自分宛のみ、本番経路と同じ）
cd "C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp"
python tests/render_email.py --smtp

# Runner の手動起動（実機の本番フロー）
# タスクスケジューラから「News-Grasp Runner」を右クリック → 「実行する」
```

ログは `C:\Users\hidek\bin\news-grasp-logs\YYYY-MM-DD.log` に出る。リアルタイム監視は別 PowerShell で：

```powershell
Get-Content "C:\Users\hidek\bin\news-grasp-logs\2026-04-28.log" -Wait -Tail 100
```

成功条件：

- `digest/{Genre}/{YYYY-MM-DD}-{Genre}.md` 各ファイルが repo に push されている
- `digest/Summary/{YYYY-MM-DD}.md` も生成されている
- `data/articles.jsonl` に 20〜25 件のメタが追記されている
- Gmail 2 宛先に HTML メールが届いている

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
| `'sks' は内部コマンド〜` | bat の改行が LF。`sed -i 's/$/\r/' news-grasp-runner.bat` で CRLF 化 |
| `claude` が見つからない | bat 内で `set CLAUDE=` をフルパスに（`where claude` で確認） |
| メールが届かない | `tests/render_email.py --smtp` で SMTP 単体テスト → 失敗時は `~/.secrets/news-grasp-smtp.txt` の App Password が有効か Google アカウントの App Passwords ページで確認（古い App Password は revoke して再発行） |
| 差出人が `hidepontrainer@gmail.com` に先祖返り | `news-grasp-runner.bat` の `claude --print` 引数に「GAS Webhook」が残っていないか確認。`tools/send_email.py` の `DEFAULT_SENDER` が正本（`news.grasp.magazine@gmail.com`） |
| NRI 宛だけ届かない | NRI セキュリティ部のメールフィルタ。`news.grasp.magazine@gmail.com` のホワイトリスト依頼か、`hideki.kusunoki@gmail.com` から手動転送運用へ |
| 画像が壊れる（メール内） | `assets/` の JPG が repo にあるか確認 → `routine-system.md` の base64 化指示が守られているか確認 |
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
- [tests/README.md](tests/README.md) — 単体テストの使い方
- [docs/architecture.pptx](docs/architecture.pptx) — アーキテクチャ図と仕様まとめ（プレゼン用）
- [memory/feedback_windows_bat_gotchas.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/feedback_windows_bat_gotchas.md) — Windows .bat 落とし穴チェックリスト
- [memory/feedback_email_html_image_inline.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/feedback_email_html_image_inline.md) — メール HTML 画像 base64 必須ルール
- [memory/reference_newsgrasp_vault_github_sync.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/reference_newsgrasp_vault_github_sync.md) — Vault root GitHub 同期方式と除外ルール
