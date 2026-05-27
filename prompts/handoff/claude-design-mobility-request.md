# Mobility カテゴリ クリエイティブ作成 + バランス調整 依頼

## あなたへの依頼

News-Grasp という日次ニュース digest メルマガサービスに、6 つ目のカテゴリ「Mobility」を追加します。
あなたには 2 つの依頼があります:

1. **Mobility カテゴリ用のサムネ画像 4 ファイル (+ SVG ソース 2 ファイル) を作成**してください
2. **既存 5 カテゴリと並んだときの視覚的バランスをレビュー**し、修正提案レポートを返してください

## 現行サイトと既存資産

- 公開 URL: https://hidepon-umg.github.io/News-Grasp
- 本日 digest URL: https://hidepon-umg.github.io/News-Grasp/2026-05-28/
- 既存サムネ CDN (10 ファイル、必ず 1 枚は WebFetch で確認してください):
  - https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-fx.jpg
  - https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-ai.jpg
  - https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-it.jpg
  - https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-economy.jpg
  - https://raw.githubusercontent.com/HIDEPON-UMG/news-grasp-assets/main/ng-thumb-game.jpg
  - (同 common 5 種、上記の `ng-thumb-` を `ng-thumb-common-` に置換)

## 並び順 (重要)

ナビバー・メール・γ schema 全てで **`fx → ai → it → mobility → economy → game`** の順で表示します。Mobility は IT-Consulting と経済の間に挟まる **4 番目** の位置。

## 現行 5 カテゴリのデザインシステム (これに統一感を合わせる)

| id | 和名 | 英名 | アクセント色 | グリフ |
|---|---|---|---|---|
| fx | 為替 | Foreign Exchange | #B8860B (琥珀) | ¥ |
| ai | AI | Artificial Intelligence | #2D5BB8 (電子青) | ◆ |
| it | IT-Consulting | IT & Consulting | #2E6B52 (苔緑) | ▲ |
| economy | 経済 | Economy | #8E2A19 (深紅) | ■ |
| game | ゲーム | Gaming | #5E3D8C (洋紫) | ● |

ベースカラー: #F0EEE9 (paper)、Ink #1A1A1A、Border #E2DED4。
タイポ: 本文 = Noto Serif JP、メタ = JetBrains Mono、英数 = Inter。

## 新 Mobility 仕様 (確定値)

| 項目 | 値 |
|---|---|
| id | mobility |
| 和名 | モビリティ |
| 英名 | Mobility |
| アクセント色 | #3A7B8C (ティール) |
| グリフ | ◎ |
| コンセプト | 自動車・電動・MaaS・近未来モビリティ |
| 並び位置 | IT (3 番目) と Economy (5 番目) の間 = 4 番目 |

## 依頼 1: クリエイティブ 4 + 2 ファイル

既存 5 種の `ng-thumb-{cat}.jpg` を必ず 1 枚は実際に見て、フォント・余白・グリフ位置・トーンを踏襲してください。

| ファイル名 | サイズ | フォーマット | 用途 |
|---|---|---|---|
| ng-thumb-mobility.jpg | 568×220 | JPG 品質 85 | メール TOP 記事 |
| ng-thumb-mobility.png | 568×220 | PNG | Obsidian 直貼り用 |
| ng-thumb-common-mobility.jpg | 140×90 | JPG 品質 85 | メールサイドサムネ |
| ng-thumb-common-mobility.png | 140×90 | PNG | 同上 PNG 版 |
| ng-thumb-mobility.svg | ベクター | SVG | 再書き出しソース |
| ng-thumb-common-mobility.svg | ベクター | SVG | 同上 |

レイアウト要件:

- 中央に大きく ◎ グリフ
- その下または右に「MOBILITY」「モビリティ」の 2 段表示
- 背景は #3A7B8C のグラデーション or 単色 (既存 5 種と同じ手法)
- アンチパターン: 既存 FX 琥珀 / AI 電子青 / Game 洋紫 と隣接して見たときに識別できないトーン

## 依頼 2: バランス調整レビュー

サイト URL (https://hidepon-umg.github.io/News-Grasp) を WebFetch で取得し、以下を確認・提案してください:

1. **トップページのレンズグリッド**: 現在 5 カテゴリの並び (5 列 or 2 段) に Mobility を加えて 6 になったとき、wrap や間隔が崩れないか。`grid-template-columns` の推奨設計を 1 案提示。**並び順は `fx → ai → it → mobility → economy → game`**。
2. **カラーパレット俯瞰**: 6 カテゴリのアクセント色 + Summary グレー (#475569) + Gold (#C9A155) を 1 枚のパレット図にまとめ、Mobility ティール (#3A7B8C) が浮いていないか / 既存色と被っていないかを視覚で示してください。特に **IT 苔緑 (#2E6B52) と Mobility ティール (#3A7B8C) が並んだとき** に区別できるかを重点確認。
3. **メールテンプレ縦尺**: 6 セクション縦並びになるとスクロール長が +20% 増える懸念。セクションヘッダ高さやカード間 margin の調整提案 1 案。

## 期待する成果物

1. **画像 6 ファイル** (zip or Claude.ai artifact 個別)
2. **バランス調整レビュー** (Markdown レポート 1 枚)
   - カラーパレット俯瞰図 (PNG or SVG 1 枚)
   - グリッド設計提案
   - メールテンプレ縦尺の調整案

完了したら zip + Markdown を返してください。何か疑問点があればこのチャットで質問してから着手して構いません。
