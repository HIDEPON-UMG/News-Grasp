---
version: alpha
name: News-Grasp
description: ニュースダイジェスト公開 web のデザインシステム — 明朝セリフ + 温白基盤に 6 カテゴリ accent を持つ静謐な読み物 UI
colors:
  primary: "#141413"
  secondary: "#5C5A52"
  tertiary: "#CC785C"
  neutral: "#F8F6F3"
  surface: "#FFFFFF"
  border: "#E8E6E3"
  on-tertiary: "#FFFFFF"
  on-primary: "#FAF9F5"
  accent-fx: "#B8860B"
  accent-ai: "#8B5CF6"
  accent-it: "#2563EB"
  accent-economy: "#047857"
  accent-game: "#DC2626"
  accent-summary: "#475569"
  success: "#3D7E60"
  warning: "#B7773D"
  error: "#B83A2D"
typography:
  h1:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', 'Hiragino Mincho ProN', serif"
    fontSize: 2.5rem
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  h2:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', 'Hiragino Mincho ProN', serif"
    fontSize: 1.75rem
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.005em"
  h3:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', 'Hiragino Mincho ProN', serif"
    fontSize: 1.25rem
    fontWeight: 700
    lineHeight: 1.4
  body-lg:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', 'Hiragino Mincho ProN', serif"
    fontSize: 1.0625rem
    lineHeight: 1.85
  body-md:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', 'Hiragino Mincho ProN', serif"
    fontSize: 0.9375rem
    lineHeight: 1.85
  body-sm:
    fontFamily: "'Inter', system-ui, -apple-system, sans-serif"
    fontSize: 0.8125rem
    lineHeight: 1.6
  label:
    fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace"
    fontSize: 0.6875rem
    fontWeight: 700
    letterSpacing: "0.12em"
  code:
    fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace"
    fontSize: 0.8125rem
    lineHeight: 1.5
rounded:
  sm: 4px
  md: 8px
  lg: 12px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  3xl: 64px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
    padding: 12px
    typography: "{typography.body-md}"
  button-primary-hover:
    backgroundColor: "#262625"
    textColor: "{colors.on-primary}"
  button-accent:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.on-tertiary}"
    rounded: "{rounded.md}"
    padding: 12px
    typography: "{typography.body-md}"
  button-accent-hover:
    backgroundColor: "#B86A50"
    textColor: "{colors.on-tertiary}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: 12px
    typography: "{typography.body-md}"
  button-secondary-hover:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: 24px
  card-muted:
    backgroundColor: "{colors.neutral}"
    rounded: "{rounded.lg}"
    padding: 24px
  article-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.lg}"
    padding: 24px
    typography: "{typography.body-md}"
  category-badge:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 4px
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.md}"
    padding: 12px
  badge:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
    rounded: "{rounded.full}"
    padding: 8px
---

## Overview

News Grasp 公開 web 配信のデザインシステム。**温白 (#F8F6F3) の読み物基盤** に **6 カテゴリ accent** を 1 点差し色として配置し、明朝セリフ (Noto Serif JP) を主体に長文記事を読みやすくする。OGP プレビュー (Slack / X / Threads / Discord) と本文 web の両方で同じトークンを参照することで、媒体間のブランド一貫性を担保する。

主な性格:

- **明朝主体の読み物 UI**: 見出しから本文まで Noto Serif JP。欧文 / 数値 / コードは JetBrains Mono / Inter にフォールバック
- **カテゴリ accent**: FX (金 #B8860B) / AI (紫 #8B5CF6) / IT (青 #2563EB) / 経済 (緑 #047857) / ゲーム (赤 #DC2626) / 総括 (鉄灰 #475569) の 6 種。同一画面で混ぜず、ページ単位で 1 色を主張させる
- **静謐な余白**: 8 の倍数 spacing。記事間は `2xl` (48px) 以上で確保し、罫線は `border` トークンに揃える
- **OGP 互換**: 1120 × 587 px の og:image を `assets/og/{cat}.jpg` に配置、og:description は 180 文字以下、twitter:card は summary_large_image

> **意図的に component から参照していないトークン**: `secondary` / `border` / `on-primary` / `accent-fx` / `accent-ai` / `accent-it` / `accent-economy` / `accent-game` / `accent-summary` / `success` / `warning` / `error` は実装側で**直接参照** (CSS 変数化 / Jinja2 経由) して使う前提。lint で `orphaned-tokens` warning が出るが意図通り。カテゴリ accent はページごとに 1 色が主導する設計で、component slot に固定で当てると逆に表現が痩せる。

## Colors

### コアパレット

| Token | Hex | 用途 |
|:--|:--|:--|
| `primary` | `#141413` | 見出し・本文の主要テキスト。**near-black**（純黒は使わない） |
| `secondary` | `#5C5A52` | 補助テキスト・metadata・出典 |
| `tertiary` | `#CC785C` | リンク・トップページのブランド誘導 |
| `neutral` | `#F8F6F3` | 温白ベース背景 (warm cream) |
| `surface` | `#FFFFFF` | 記事カード・モーダルなど一段持ち上げる面 |
| `border` | `#E8E6E3` | 罫線・区切り |

### カテゴリ accent

| Token | Hex | 用途 |
|:--|:--|:--|
| `accent-fx` | `#B8860B` | 為替 (FX)。金色寄りの落ち着いた黄土 |
| `accent-ai` | `#8B5CF6` | AI。深めの紫 |
| `accent-it` | `#2563EB` | IT / コンサル。鮮やかな青 |
| `accent-economy` | `#047857` | 経済。深緑 |
| `accent-game` | `#DC2626` | ゲーム。鮮やかな赤 |
| `accent-summary` | `#475569` | 総括。鉄灰 |

> **使用ルール**: ページごとに **1 カテゴリ accent のみ**主導する。1 ページに 2 色以上の accent を並べない (アーカイブ・トップページのカード一覧では各カードの**左罫線色**にだけ使う)。

### セマンティクス

| Token | Hex | 用途 |
|:--|:--|:--|
| `success` | `#3D7E60` | 成功・完了 |
| `warning` | `#B7773D` | 警告・要注意 |
| `error` | `#B83A2D` | エラー・破壊的操作 |

## Typography

- **見出し (h1–h3)**: `Noto Serif JP` → フォールバックで `Yu Mincho` / `Hiragino Mincho ProN` / `serif`。**weight 700–800**, 字詰めはやや tight
- **本文 (body-lg / body-md)**: `Noto Serif JP`。行間 1.85 で長文を読みやすく
- **補助 (body-sm)**: `Inter`。日付 / 出典 / カテゴリ説明など欧文混在テキスト
- **ラベル**: `JetBrains Mono` で `letter-spacing 0.12em` の小キャプス風
- **コード**: `JetBrains Mono` → `Consolas` → `Courier New`

> Noto Serif JP は Google Fonts 配信。HTML 出力時に preconnect で先読みする。Web フォント不可環境では `Yu Mincho` / `Hiragino Mincho ProN` にフォールバック。

## Layout

8 の倍数を基準にした段階。

| Token | px |
|:--|:--|
| `xs` | 4 |
| `sm` | 8 |
| `md` | 16 |
| `lg` | 24 |
| `xl` | 32 |
| `2xl` | 48 |
| `3xl` | 64 |

- 記事セクション間: `2xl` (48px)
- カード内パディング: `lg` (24px)
- 本文行間隔: `md` (16px)
- インライン要素間: `sm` (8px)

**ブレークポイント**:

| 名前 | 幅 |
|:--|:--|
| sm | 640px |
| md | 768px |
| lg | 1024px |

最大コンテンツ幅は `42rem` (672px) を本文に、`64rem` (1024px) をアーカイブ一覧に使う。

## Elevation & Depth

シャドウは控えめに。2 段階のみ。

| レベル | 値 |
|:--|:--|
| `none` | 平面（罫線のみで区切り） |
| `sm` | `0 1px 2px rgba(20, 20, 19, 0.04)` カード持ち上げ |

濃いシャドウや 3 段以上のレイヤは使わない。**境界は `border` トークンで表現**することを優先。

## Shapes

| Token | 値 | 用途 |
|:--|:--|:--|
| `rounded.sm` | 4px | バッジ・タグ |
| `rounded.md` | 8px | ボタン・入力 |
| `rounded.lg` | 12px | カード |
| `rounded.full` | 9999px | アバター・ピル |

新聞風の落ち着きを残すため、カードでも丸めは 12px までに留める。

## Components

### `article-card`

記事カード。`surface` 背景 + `rounded.lg` + 24px パディング。**左端 4px の罫線**にカテゴリ accent を使う (CSS 変数 `--accent-current` 経由)。

### `category-badge`

カテゴリラベル小バッジ。`neutral` 背景 + `primary` テキスト + `rounded.sm`。背景にカテゴリ accent を 8% 不透明で重ねる派生を 1 種だけ許可。

### `button-primary`

主要 CTA (near-black + warm-white)。記事末尾の「メール購読」「次の記事へ」など。

### `button-accent`

ブランド前面の誘導 CTA (Claude Orange + white)。トップ「最新号を読む」のみ。1 画面に 1 個。

### `card` / `card-muted` / `input` / `badge`

汎用。`card` は記事冒頭の summary callout、`card-muted` はメタ情報枠、`badge` は OGP プレビューや日付 chip に使う。

## Do's and Don'ts

### ✅ Do

- 1 ページに **1 カテゴリ accent** のみ主導させる (左罫線・見出し下線・リンク色に限定)
- 背景は **`neutral` (#F8F6F3)** を使う。純白はカード面のみ
- テキストは `primary` (#141413) と `secondary` (#5C5A52) の **2 色のみ**
- 余白は **8 の倍数**で揃える
- 罫線は `border` (`#E8E6E3`) で統一
- 見出しは明朝、ラベルは JetBrains Mono の小キャプス

### ❌ Don't

- 1 ページに 2 種以上のカテゴリ accent を並べない (アーカイブの **左罫線のみ** が例外)
- 純黒 `#000000` を本文に使わない (`#141413` を使う)
- 純白 `#FFFFFF` をページ背景に使わない (クリーム `#F8F6F3` を使う)
- 角を立てない (最小でも `rounded.sm` = 4px)
- カテゴリ accent をボタン背景に使わない (色がうるさくなる)
- 影を 2 段以上重ねない
