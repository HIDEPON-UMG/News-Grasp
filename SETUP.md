# News-Grasp セットアップ手順

D 案（ローカル Claude Code via Windows タスクスケジューラ）の最終版手順。すでに本番運用中なので、これは **新規環境への再構築** や **誰かに引き継ぐとき** のリファレンスとして使う。

## 確定構成

| 項目 | 値 |
|---|---|
| 実行方式 | Windows タスクスケジューラ → `news-grasp-runner.bat` → `claude.exe --print` |
| 実行時刻 | 毎朝 06:00 JST |
| モデル | Claude Sonnet 4.6（Max サブスク内認証） |
| GitHub repo | `HIDEPON-UMG/News-Grasp`（プライベート） |
| Obsidian ボルト | `C:\Users\hidek\Obsidian\New's Grasp\` |
| Repo の clone 先 | ボルト直下 `New's Grasp\News-Grasp\` |
| メール送信 | `tools/send_email.py`（Gmail SMTP `smtp.gmail.com:587` STARTTLS） |
| 差出人 | `news.grasp.magazine@gmail.com`（専用アカウント、`tools/send_email.py:35` の `DEFAULT_SENDER` で集約・正本） |
| App Password | `~/.secrets/news-grasp-smtp.txt`（差出人アカウントの Gmail App Password） |
| 配信宛先 | `hideki.kusunoki@gmail.com` / `h2-hiramatsu@nri.co.jp` |
| 旧 GAS Webhook 経路 | **2026-04 末で廃止**（旧 `hidepontrainer@gmail.com` 配下の web app。本番未使用） |

## 0. 前提環境

- Windows 11
- Git for Windows（`C:\Program Files\Git\cmd\git.exe`）
- Claude Code CLI（`C:\Users\hidek\.local\bin\claude.exe`、Max サブスクで認証済み）
- Python 3.13+（テスト実行用）
- `gh` CLI（HIDEPON-UMG でログイン済み）
- Gmail App Password を `~/.secrets/news-grasp-smtp.txt` に保存済み（差出人 `news.grasp.magazine@gmail.com` の 2FA → App Password 発行画面で生成）
- Obsidian Desktop（任意・閲覧用）

> **旧経路**: `clasp` v3.x（`hidepontrainer@gmail.com` 配下の GAS web app `news-grasp-mailer` 管理用）は 2026-04 末で廃止のため新規環境では不要。

## 1. リポジトリの clone

```powershell
cd "C:\Users\hidek\Obsidian\New's Grasp"
gh repo clone HIDEPON-UMG/News-Grasp
```

ボルト直下に `News-Grasp\` フォルダができ、Obsidian で digest が表示できる状態になる。

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

set REPO_DIR=C:\Users\hidek\Obsidian\New's Grasp\News-Grasp
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
cd "C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
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
cd "C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
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
| Obsidian で同期されない | Runner 自体が `git pull` を内包しているので、別途のタスクは不要。手動で `git pull` |

## 8. 関連ドキュメント

- [README.md](README.md) — 全体構成と運用フロー
- [prompts/routine-system.md](prompts/routine-system.md) — Runner プロンプトの完全仕様
- [prompts/obsidian-tagging-spec.md](prompts/obsidian-tagging-spec.md) — Obsidian タグ階層仕様（正本）
- [tests/README.md](tests/README.md) — 単体テストの使い方
- [docs/architecture.pptx](docs/architecture.pptx) — アーキテクチャ図と仕様まとめ（プレゼン用）
- [memory/feedback_windows_bat_gotchas.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/feedback_windows_bat_gotchas.md) — Windows .bat 落とし穴チェックリスト
- [memory/feedback_email_html_image_inline.md](../../../.claude/projects/c--Users-hidek-OneDrive--------ProjectFolders/memory/feedback_email_html_image_inline.md) — メール HTML 画像 base64 必須ルール
