---
version: alpha
name: News-Grasp
description: Magazine Spread デザインシステム — navy×gold×paper の編集者的トーン。明朝×Inter×JetBrains Mono の 3 フォント体系で 6 カテゴリ accent を切替
colors:
  primary: "#1A1A1A"
  secondary: "#5C5A52"
  tertiary: "#C9A155"
  neutral: "#FAF7F0"
  surface: "#FFFFFF"
  border: "#E2DED4"
  paper-soft: "#F2EEE3"
  paper-dim: "#EAE3D3"
  on-tertiary: "#181C2A"
  on-primary: "#F0EBE0"
  navy: "#181C2A"
  cream: "#F0EBE0"
  gold: "#C9A155"
  accent-fx: "#B8860B"
  accent-ai: "#2D5BB8"
  accent-it: "#2E6B52"
  accent-mobility: "#3A7B8C"
  accent-economy: "#8E2A19"
  accent-game: "#5E3D8C"
  accent-summary: "#475569"
  success: "#3D7E60"
  warning: "#B7773D"
  error: "#B83A2D"
typography:
  h1:
    fontFamily: "'Inter', -apple-system, 'Segoe UI', sans-serif"
    fontSize: 3.5rem
    fontWeight: 900
    lineHeight: 0.95
    letterSpacing: "-0.03em"
  h2:
    fontFamily: "'Inter', -apple-system, 'Segoe UI', sans-serif"
    fontSize: 2.25rem
    fontWeight: 900
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  h3:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', serif"
    fontSize: 1.5rem
    fontWeight: 800
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  body-lg:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', serif"
    fontSize: 1rem
    lineHeight: 1.9
  body-md:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', serif"
    fontSize: 0.9375rem
    lineHeight: 1.85
  body-sm:
    fontFamily: "'Noto Serif JP', 'Yu Mincho', serif"
    fontSize: 0.84rem
    lineHeight: 1.7
  label:
    fontFamily: "'JetBrains Mono', Consolas, monospace"
    fontSize: 0.6875rem
    fontWeight: 700
    letterSpacing: "0.15em"
  code:
    fontFamily: "'JetBrains Mono', Consolas, monospace"
    fontSize: 0.8125rem
    lineHeight: 1.5
rounded:
  sm: 0px
  md: 0px
  lg: 0px
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
    backgroundColor: "{colors.navy}"
    textColor: "{colors.cream}"
    rounded: "{rounded.sm}"
    padding: 12px
    typography: "{typography.label}"
  button-primary-hover:
    backgroundColor: "#262B40"
    textColor: "{colors.cream}"
  button-accent:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.navy}"
    rounded: "{rounded.sm}"
    padding: 12px
    typography: "{typography.label}"
  button-accent-hover:
    backgroundColor: "#B58D45"
    textColor: "{colors.navy}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 12px
    typography: "{typography.label}"
  button-secondary-hover:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: 24px
  card-muted:
    backgroundColor: "{colors.paper-soft}"
    rounded: "{rounded.sm}"
    padding: 24px
  article-card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 24px
    typography: "{typography.body-md}"
  category-badge:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.cream}"
    rounded: "{rounded.sm}"
    padding: 4px
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary}"
    rounded: "{rounded.sm}"
    padding: 12px
  badge:
    backgroundColor: "{colors.navy}"
    textColor: "{colors.cream}"
    rounded: "{rounded.full}"
    padding: 8px
---

## Overview

Magazine Spread 編集者トーンのデザインシステム。**navy (`#181C2A`) × gold (`#C9A155`) × paper (`#FAF7F0`)** の 3 軸で「新聞・雑誌の紙面」のような情報密度と落ち着きを作る。本文は明朝 (Noto Serif JP)、見出し巨大数値は Inter Black 900、ラベル / メタは JetBrains Mono の **3 フォント鼎立**。角丸は全廃 (`rounded.sm = 0`) でフラット・エッジィな仕上げ。

主な性格:

- **navy ヘッダー + gold underline rule** で誌名 zone を強く立てる
- **明朝主体の長文読み物 UI**: body は Noto Serif JP、`line-height` 1.85–1.9 で 220 字級の長文ブロックを耐える
- **6 カテゴリ accent**: FX 琥珀 / AI 電子青 / IT 苔緑 / Economy 深紅 / Game 洋紫 / Summary 鉄灰。1 ページ 1 accent が原則
- **角丸 0px**: news-thumb プレースホルダのみ例外で 4px (アセット側に閉じる)
- **大型グリフ装飾**: editorial § や hero glyph を 96-200px の serif で gold 18% opacity 透過させる

> **意図的に component から参照していないトークン**: `secondary` / `border` / `paper-soft` / `cream` / `gold` / `navy` / `accent-fx/ai/it/economy/game/summary` / `on-tertiary` / `on-primary` / `success` / `warning` / `error` は実装側で**直接参照** (CSS 変数 / Jinja2 経由) する設計。component slot に固定すると Magazine の表現幅 (1 ページ 1 accent / dark Editorial / pull quote ハイライト) が痩せる。lint で `orphaned-tokens` warning が出るが意図通り。

## Colors

### コアパレット (Magazine 三軸)

| Token | Hex | 用途 |
|:--|:--|:--|
| `primary` | `#1A1A1A` | 本文・見出し主要テキスト |
| `secondary` | `#5C5A52` | 補助テキスト・メタ |
| `tertiary` | `#C9A155` | **gold**。Editorial / Subscribe / underline 装飾 (= `gold` の alias) |
| `neutral` | `#FAF7F0` | **paper**。ページ背景 |
| `paper-soft` | `#F2EEE3` | Categories / Footer / Subscribe など副背景 |
| `paper-dim` | `#EAE3D3` | **YESTERDAY** 日報 (Daily Overview 前日) の地色。paper を一段落とした暖色 |
| `surface` | `#FFFFFF` | カード面 |
| `border` | `#E2DED4` | 罫線・区切り |
| `navy` | `#181C2A` | Brand zone / dark editorial 背景 |
| `cream` | `#F0EBE0` | navy 上の文字色 |
| `gold` | `#C9A155` | アクセント (`tertiary` の意味的別名) |

### カテゴリ accent

| Token | Hex | 用途 |
|:--|:--|:--|
| `accent-fx` | `#B8860B` | 為替 (琥珀) |
| `accent-ai` | `#2D5BB8` | AI (電子青) |
| `accent-it` | `#2E6B52` | IT/コンサル (苔緑) |
| `accent-economy` | `#8E2A19` | 経済 (深紅) |
| `accent-game` | `#5E3D8C` | ゲーム (洋紫) |
| `accent-summary` | `#475569` | 総括 (鉄灰) |

### gold tint (Editorial / underline 装飾)

| 用途 | rgba 値 |
|:--|:--|
| gold underline (Hero keyword) | `rgba(201,161,85,0.35)` |
| § 装飾 (Editorial 大型) | `rgba(201,161,85,0.18)` |
| § 縦罫線 | `rgba(201,161,85,0.30)` |
| lead box 半透明地 | `rgba(201,161,85,0.12)` |
| emphasis `[[X]]` 背景 (gold base) | `rgba(201,161,85,0.18)` |

### セマンティクス

| Token | Hex | 用途 |
|:--|:--|:--|
| `success` | `#3D7E60` | 成功・完了 |
| `warning` | `#B7773D` | 警告 |
| `error` | `#B83A2D` | エラー |

## Typography

3 フォント鼎立:

- **本文 (body-lg / body-md / body-sm / h3)**: `Noto Serif JP` → `Yu Mincho` フォールバック
- **巨大見出し / 数値 (h1 / h2)**: `Inter` 900 weight。letter-spacing -0.02 ~ -0.03em で詰める
- **ラベル / メタ / グリフ (label / code)**: `JetBrains Mono` 700 weight。letter-spacing 0.12-0.15em で大文字キャプス風

Hero h1 は 3.5rem (56px) ~ 5.5rem (88px) まで使う。Editorial h2 は 2.25rem。本文は 0.9375rem-1rem。

### 強調記法 (3 階層・厳密ルール)

本文の強調は **3 階層のヒエラルキー** で使い分ける。強い順に:

#### 1. マーカー `[[X]]` (最強)

- **出力**: accent 28% 背景 + 太字 weight 900 + accent 色文字 + 微パディング
- **用途**: **1 段落につき 1〜2 箇所まで**。**固有名詞・人物名・組織名・銘柄・金額数値の主役** に使う
- **対象例**:
  - 人物・組織: `[[Warsh議長]]` `[[Accenture]]` `[[Anthropic]]`
  - 銘柄・指標: `[[USD/JPY]]` `[[日経平均]]`
  - 主役数値 (1 段落で最重要の数字): `[[ドル円159円]]` `[[GDP 2.1%]]`
- **禁止**: 1 段落に 3 個以上、1 文に 2 個以上、動詞句・形容詞句に使う

#### 2. 太字 `**X**` (中)

- **出力**: weight 900 + 本文同色 (色強調はマーカーの役目)
- **用途**: **1 段落につき 3〜5 箇所**。**主役動詞・補助数値・重要修飾語** に使う
- **対象例**:
  - 補助数値・%: `**5/22 NYクローズ**` `**3.8%**` `**1,100 人削減**`
  - 主役の動作: `**封印した**` `**正式開始**` `**買収完了**`
  - 重要修飾: `**過去最大水準**` `**5 期連続**`
- **禁止**: マーカー [[X]] と入れ子・併用、連続 3 単語以上

#### 3. 下線 `__X__` (弱・含意)

- **出力**: 2px accent 下線 + weight 600 (太字でないが少しだけ重め)
- **用途**: **1 段落につき 1〜2 箇所**。**解釈・含意・読み筋の核フレーズ** に使う
- **対象例**:
  - 含意: `__均衡なき均衡__` `__方向感なく週明けへ__`
  - 読み筋: `__利上げ織り込みは継続__` `__エコシステム占有率が真の戦場__`
  - 短期予測フレーズ: `__週後半に方向感が決まる__`
- **禁止**: 固有名詞・数値に使う (それはマーカー/太字の役目)、1 文に複数

#### 使い分けマトリクス (即決早見表)

| 強調したい要素 | 記法 | 例 |
|:--|:--|:--|
| 人名・組織名・銘柄 | `[[X]]` | `[[Warsh議長]]` |
| 段落の主役数値 | `[[X]]` | `[[ドル円159円]]` |
| 補助の数値・% | `**X**` | `**3.8%**` |
| 主役の動詞句 | `**X**` | `**封印した**` |
| 解釈・含意フレーズ | `__X__` | `__均衡なき均衡__` |

#### 段落構成のガイド (理想形)

1 段落 (3〜4 文 / 約 150 字) の中で **3 種類すべてを 1 回ずつ以上** 登場させ、目線を **マーカー → 太字 → 下線** の階層で誘導する。下記が手本:

> [[Warsh議長]] は就任初週の声明で **利下げ封印** の姿勢を維持し、ドル円は **159円台** で均衡を保った。 __方向感なく週明けへ__ 突入する中、**5/28 FOMC 議事録** が次の焦点となる。

## Layout

8 の倍数 spacing。

| Token | px |
|:--|:--|
| `xs` | 4 |
| `sm` | 8 |
| `md` | 16 |
| `lg` | 24 |
| `xl` | 32 |
| `2xl` | 48 |
| `3xl` | 64 |

- ページ最大幅: 1280px (Magazine 想定)
- セクション padding: 40-64px 横 / 24-64px 縦
- Lens nav padding: `12px 40px`
- Hero: 480px height のフル幅 KV
- More stories: 2 カラム grid `gap: 32 40`
- Editorial: 3 カラム `200px / 1fr / 280px`

**ブレークポイント**:

| 名前 | 幅 |
|:--|:--|
| sm | 640px |
| md | 768px |
| lg | 1024px |
| xl | 1280px |

## Elevation & Depth

シャドウなし (`elevation.none` のみ)。境界は **gold 2px rule** または `border` 1px で表現。

| レベル | 値 |
|:--|:--|
| `none` | 平面 (gold rule または border のみ) |

濃いシャドウや 2 段以上のレイヤは使わない。Magazine のフラットさを担保。

## Shapes

| Token | 値 | 用途 |
|:--|:--|:--|
| `rounded.sm` | 0px | 既定 |
| `rounded.md` | 0px | カード・ボタン |
| `rounded.lg` | 0px | モーダル |
| `rounded.full` | 9999px | バッジ・アバター (例外) |

**全要素 angular** が原則。badge / placeholder thumb / pull quote 等の例外は実装側でローカルに上書き。

## Components

### `article-card`

Magazine の "More stories" card。`surface` 背景 + 0 角丸 + 24px padding。下端 `1px solid border`。

### `category-badge`

`navy` 背景 + `cream` 文字 + 0 角丸。Brand zone の SUBSCRIBE ボタンと同じトーン。

### `button-primary` / `button-accent`

- primary: navy bg + cream text (Hero CTA / Subscribe form 送信)
- accent: gold bg + navy text (Editorial primary CTA)
両者とも 0 角丸 + mono ラベル + letter-spacing 0.15em。

### `card` / `card-muted`

- card: surface 背景 + border + 0 角丸
- card-muted: paper-soft 背景 + 0 角丸

### `input`

surface 背景 + 0 角丸。focus 時は `border: 2px solid gold` で囲む (実装側 CSS)。

### `badge`

`navy` 背景 + `cream` 文字 + `rounded.full`。Score chip など丸い小バッジ用 (角丸 0 原則の例外)。

## Do's and Don'ts

### ✅ Do

- 1 ページに **1 カテゴリ accent** のみ主導 (Hero 罫線 / TOP STORY バッジ / score / outline で)
- navy ヘッダーには **gold 2px rule (opacity 0.7)** を必ず添える
- emphasis は **3 階層** (`[[X]]` マーカー / `**X**` 太字 / `__X__` 下線) で使い分け、下の Typography セクション「強調記法」に従う
- 本文は明朝 1.85-1.9 行間。120 字以上のブロックは line-height を緩める
- ラベル / メタは mono 大文字 + letter-spacing 0.15em で「新聞風」を作る
- 角丸は 0px 維持 (Magazine の鋭利さが命)

### ❌ Don't

- 1 ページに 2 種以上のカテゴリ accent を並べない (アーカイブ一覧の左罫線のみ例外)
- 純黒 `#000000` をテキストに使わない (`#1A1A1A`)
- 純白 `#FFFFFF` をページ背景に使わない (`#FAF7F0` paper)
- 角を丸めない (`rounded.full` の badge 1 種だけ例外)
- 影を 2 段以上重ねない (本デザインは原則 shadow 無し)
- カテゴリ accent をボタン背景に使わない (CTA は navy / gold / outline の 3 種に限定)
- Hero の巨大グリフ・editorial § を accent カラーで塗らない (gold opacity 0.18 で抑える)
