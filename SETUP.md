# News-Grasp セットアップ手順

このドキュメントは **ユーザーが手動で行う必要があるステップ** をまとめたもの。Claude が自動化できる部分（GitHub repo、ファイル群、GAS プロジェクト本体、コードのデプロイ）は既に完了している。残りは認証・権限・シークレット投入が絡むため手作業が必要。

---

## 完了済み（Claude が実施）

- ✅ GitHub プライベート repo 作成: <https://github.com/HIDEPON-UMG/News-Grasp>
- ✅ ボルト内へ clone: `C:\Users\hidek\OneDrive\Obsidians\New's Grasp\News-Grasp\`
- ✅ 初期ファイル一式 commit & push
- ✅ GAS プロジェクト作成: `news-grasp-mailer`
- ✅ Web App デプロイ（v1）
- ✅ Windows 用同期バッチ: `C:\Users\hidek\bin\news-grasp-pull.bat`

### 確定値（設定時に使う）

| キー | 値 |
|---|---|
| GitHub repo | `HIDEPON-UMG/News-Grasp` |
| GAS scriptId | `1Vf0gQPHe-1SevaGqKBDz6FvJhrCUXGr7cWWwYAEv_lhktlwKWzNP3-Gd` |
| GAS エディタ URL | <https://script.google.com/d/1Vf0gQPHe-1SevaGqKBDz6FvJhrCUXGr7cWWwYAEv_lhktlwKWzNP3-Gd/edit> |
| **Webhook URL（v1）** | `https://script.google.com/macros/s/AKfycbxCNRk_M3s1xPyCm_9BObpVWAzilFGwXQxFi-XMBnBHu7-Ly3nhydzqL_cPJUOGYgGu/exec` |

---

## ステップ 1: WEBHOOK_SECRET を生成して保管

Routine と GAS の両方で使う共有シークレット。**32〜64 文字のランダム文字列を生成し、安全な場所にメモしておく**。例：

```bash
# PowerShell:
[Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Max 256 } | ForEach-Object { [byte]$_ }))
# または bash:
openssl rand -base64 48
```

このあと **同じ値** を以下 2 箇所に投入する：
1. GAS の Script Properties（GAS 側で受信を検証する）
2. Anthropic Routine の secrets（Routine 側で送信に付与する）

---

## ステップ 2: GAS の OAuth 同意とシークレット投入

GAS の `GmailApp.sendEmail` は初回実行時に OAuth 同意が必要。**ブラウザで手動実行する**。

1. GAS エディタを開く: <https://script.google.com/d/1Vf0gQPHe-1SevaGqKBDz6FvJhrCUXGr7cWWwYAEv_lhktlwKWzNP3-Gd/edit>
2. 関数選択ドロップダウンから **`testSendSelf`** を選んで「実行」をクリック
3. 「権限を確認」→ アカウント選択 → 「詳細」→「（安全ではないページ）に移動」→「許可」
4. 実行後、`hidepontrainer@gmail.com` 宛に「[News-Grasp] 疎通テスト」メールが届くことを確認

5. 続けて **シークレットを投入**：
   - GAS エディタ左サイドバーの ⚙️「プロジェクトの設定」
   - 「スクリプト プロパティ」セクションで **「スクリプト プロパティを追加」**
   - キー: `WEBHOOK_SECRET`、値: ステップ 1 で生成した文字列
   - 「スクリプト プロパティを保存」

---

## ステップ 3: Web App の公開設定確認

`appsscript.json` で `executeAs: USER_DEPLOYING` / `access: ANYONE_ANONYMOUS` を指定済みだが、初回デプロイは UI で再確認が安全：

1. GAS エディタ右上の **「デプロイ」→「デプロイを管理」**
2. 「News-Grasp Webhook v1」が表示されていることを確認
3. 「種類」が「ウェブアプリ」、「次のユーザーとして実行: 自分（hidepontrainer@gmail.com）」、「アクセスできるユーザー: 全員」になっていれば OK
4. もし違っていれば右上の鉛筆アイコンから編集 → 上記設定で「デプロイ」

---

## ステップ 4: Webhook 疎通テスト（任意）

ローカル PowerShell から実際に POST して確認：

```powershell
$secret = "<ステップ1で生成したシークレット>"
$body = @{
  secret   = $secret
  to       = @("hideki.kusunoki@gmail.com")
  subject  = "News-Grasp Webhook smoke test"
  htmlBody = "<p>If you can read this, the webhook is alive.</p>"
} | ConvertTo-Json
Invoke-RestMethod -Method POST -Uri "https://script.google.com/macros/s/AKfycbxCNRk_M3s1xPyCm_9BObpVWAzilFGwXQxFi-XMBnBHu7-Ly3nhydzqL_cPJUOGYgGu/exec" -Body $body -ContentType "application/json"
```

`{ ok: true, results: [...] }` が返り、Gmail に届けば成功。

---

## ステップ 5: Windows タスクスケジューラ登録

毎朝 07:00 JST に repo を pull してボルトに反映する。

1. Windows キーから「タスク スケジューラ」を起動
2. 右ペイン「**タスクの作成**」（基本タスクではなく詳細版）
3. 全般:
   - 名前: `News-Grasp Pull`
   - 説明: `毎朝 News-Grasp repo を Obsidian ボルトに同期`
   - 「ユーザーがログオンしているかどうかにかかわらず実行する」
4. トリガー → 新規:
   - スケジュール: 毎日
   - 開始: 07:00:00
   - 「有効」チェック
5. 操作 → 新規:
   - 操作: `プログラムの開始`
   - プログラム/スクリプト: `C:\Users\hidek\bin\news-grasp-pull.bat`
6. 条件:
   - 「タスクを実行するためにスリープを解除する」をチェック
   - 「コンピューターを AC 電源で使用している場合のみタスクを開始する」のチェックを **外す**（ノート PC でも動かす場合）
7. 設定:
   - 「タスクが失敗した場合の再起動の間隔: 5 分」、「再起動試行の最大数: 3」
8. 保存（パスワード入力を求められる）

ログは `C:\Users\hidek\bin\news-grasp-pull.log` に出る。

---

## ステップ 6: Anthropic Routine の登録（/schedule）

**`/schedule` コマンドを Claude Code 上で実行する**。Claude に以下のように依頼する：

> News-Grasp の Routine を /schedule で登録したい。
> - 名前: news-grasp-daily
> - cron: `0 21 * * *` (UTC = 06:00 JST)
> - リトライ: 3 回
> - secrets:
>   - GH_TOKEN = ghp_xxx (HIDEPON-UMG/News-Grasp の repo 権限)
>   - WEBHOOK_URL = https://script.google.com/macros/s/AKfycbxCNRk_M3s1xPyCm_9BObpVWAzilFGwXQxFi-XMBnBHu7-Ly3nhydzqL_cPJUOGYgGu/exec
>   - WEBHOOK_SECRET = <ステップ1の値>
> - プロンプト本体は HIDEPON-UMG/News-Grasp の prompts/routine-system.md をそのまま使う。

`GH_TOKEN` は GitHub > Settings > Developer settings > Personal access tokens > Fine-grained で作成：
- repository access: HIDEPON-UMG/News-Grasp のみ
- permissions: `Contents: Read and write`, `Metadata: Read-only`

---

## ステップ 7: 初回エンドツーエンド検証

`/schedule run news-grasp-daily` で即時実行し、以下を確認：

- [ ] GitHub repo の `digest/2026-MM-DD-AI.md` 等が新規追加されている
- [ ] `data/articles.jsonl` に新規記事メタが追記されている
- [ ] `data/_status.md` に成功行が追記されている
- [ ] `news-grasp-pull.bat` を手動実行（タスクスケジューラ待たずに）
- [ ] Obsidian で `News-Grasp/digest/` 配下に当日 Markdown が表示される
- [ ] `[[2026-MM-DD-AI]]` 等の wiki link が解決する
- [ ] Gmail に HTML メールが届く（2 宛先とも）
- [ ] NRI 宛が外部メールフィルタで弾かれていないか平松氏に確認依頼

---

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| `clasp logs` で `OAuth not granted` | ステップ 2 の OAuth 同意未完了。`testSendSelf` を再実行 |
| Webhook が `WEBHOOK_SECRET not configured` を返す | ステップ 2 の Script Properties 投入未完了 |
| Webhook が `invalid secret` を返す | Routine secrets と GAS Script Properties の値が不一致 |
| Routine が `gh: command not found` | Anthropic Routine 環境に `gh` プリインストール済みのはず。`/schedule` 設定で確認 |
| メールが NRI ドメインに届かない | NRI セキュリティ部に「外部 Gmail 経由の受信許可リクエスト」を出すか、`hideki.kusunoki@gmail.com` から手動転送する運用 |
| Obsidian で digest/ が見えない | `news-grasp-pull.bat` の手動実行 → ログ確認 |
| バッチで `.git not found` | clone 失敗。手動で `git -C "C:\Users\hidek\OneDrive\Obsidians\New's Grasp\News-Grasp" status` で状態確認 |

---

## 改修・拡張時のメモ

- watchlist 編集: ボルト内 `data/watchlist.md` を直接編集 → `git commit && git push` で翌朝から反映
- プロンプト改修: `prompts/routine-system.md` を編集 → push（Routine が次回起動時に最新版を読む）
- ジャンル追加: `routine-system.md` の曜日マトリクス + `watchlist.md` の対応セクション + email-template の `{{GENRE_BADGES}}` を更新
- 90 日 → 180 日に延ばす: `routine-system.md` の「直近 90 日」を書き換え
