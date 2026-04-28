# News Grasp — メールテンプレート単体テスト

`prompts/email-template.html` の修正を **Claude を呼ばず** 確認するための軽量ハーネス。

## 3 つのテストモード

| モード | コマンド | 用途 | コスト |
|---|---|---|---|
| **A** ローカルプレビュー | `python tests/render_email.py` | レイアウト確認（送信なし） | $0 |
| **C** Webhook E2E | `python tests/render_email.py --send` | 自分宛にメール送信、経路確認 | $0 |
| **B** 最新 digest からの再送 | `claude --print` 経由（実装は別途） | 既に生成済みの記事で送り直す | $0（軽量） |

## 使用例

### A. レイアウト確認

```powershell
cd "C:\Users\hidek\Obsidian\New's Grasp\News-Grasp"
python tests/render_email.py
```

→ `tests/output/preview.html` が生成される。ブラウザで開いて確認。
モックデータ（`tests/mock_data.py`）は 5 カテゴリ × 5 件で軽量化してある。

### C. Webhook 経由でテスト送信

```powershell
python tests/render_email.py --send
```

→ デフォルト宛先（`hideki.kusunoki@gmail.com` のみ）にテスト件名で送信。
NRI 宛も含めたい時は `--to addr1 --to addr2` で指定。

```powershell
python tests/render_email.py --send `
    --to hideki.kusunoki@gmail.com `
    --to h2-hiramatsu@nri.co.jp `
    --subject "[TEST 2] News Grasp dark theme"
```

## ファイル構成

```
tests/
├── README.md            # このファイル
├── mock_data.py         # サンプルデータ (5 カテゴリ × 5 件)
├── render_email.py      # テンプレート展開エンジン
└── output/              # 生成 HTML 保存先（git ignore）
    └── preview.html
```

## 修正フロー（推奨）

1. `prompts/email-template.html` を編集
2. `python tests/render_email.py` で preview.html 生成
3. ブラウザで開いて確認
4. 必要なら `--send` で実機送信
5. 問題なければ commit

## モックデータの拡張

`tests/mock_data.py` の `CATEGORIES` を編集すれば、特定の表示ケース（記事 0 件、bullets が長文 etc.）を再現できる。

## 関連

- 本番ジョブ（毎朝 06:00 JST 起動）: `news-grasp-runner.bat` → `claude --print`
- 本番のフルフロー: `prompts/routine-system.md` のステップ 1〜7 を Claude が一括実行
- 本テストはステップ 7（HTML 生成 + Webhook 送信）だけを切り出したもの
