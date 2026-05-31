# 引き継ぎ書: DeepDive (週次ディープダイブ) と公開ページ生成の統合状況

- 作成日: 2026-05-31
- 対象セッション: DeepDive (`digest/DeepDive/*.md` / `kind: deepdive`) を開発中のセッション
- 関連コミット: `3f83108` "fix(LP): DeepDive digest が summary に化けトップ LP を全滅させる事故を修正"
- 一次ソース（コード）: [tools/generate_pages.py](tools/generate_pages.py) / [tools/fetch_ogp.py](tools/fetch_ogp.py) / [tests/test_deepdive_not_summary.py](tests/test_deepdive_not_summary.py)

---

## 1. 何が起きたか（事故の要約）

2026-05-31 の朝、トップページ (`docs/index.html`) が次の 3 つで同時に壊れた。

1. **TODAY'S THEME 大見出し**がその日固有のテーマではなくブランド標語「時勢を掴み、日々に新たに。」にフォールバック
2. **本日のテーマ考察**セクションが見出しだけで本文空
3. **各カテゴリページの「本日の考察」**の強調表現（太字 / 下線 / マーカー）が消え素テキスト化

真因は **DeepDive digest が公開ページ生成系で `category_id = "summary"` に化けていた**こと。

- `digest/DeepDive/2026-05-31-DeepDive.md` は `kind: deepdive`・`categoryId` 無し。
- 旧 `build_context` は未知ディレクトリ名 (`DeepDive`) を解決できず **無条件で `"summary"` に既定化**していた（`_resolve_cat_from_dirname(...) or "summary"`）。
- 結果、その日に `category_id == "summary"` のエントリが **2 本**（本物 `digest/Summary/2026-05-31.md` と DeepDive）できた。
- LP の editorial 選択は `next(e for e in same_day if e.category_id == "summary")` で**先頭の 1 本だけ**を拾う。collect 順（mtime 昇順）で DeepDive (00:03) が本物 Summary (06:15) より先に来たため、**reflection 空・theme が長文の DeepDive 側**が editorial に選ばれ、LP のテーマ・考察・強調が全滅した。

> 昨日まで DeepDive digest が存在しなかったため発覚せず、日をまたいで初めて表面化した（典型的な「データ追加で既存描画が静かに壊れる」回帰）。**データ（DeepDive 本文）は正しく、generate_pages の category 解決の問題**だった。

---

## 2. 今回の修正でコードがどう整理されたか

### 2-1. 未知/非カテゴリ digest は「summary に化けず」LP から除外する

[tools/generate_pages.py](tools/generate_pages.py) `build_context()` の category 解決を変更した（概念）:

```
category_id = frontmatter.categoryId（小文字）
  └ CATEGORIES に無ければ → 親フォルダ名から解決 (_resolve_cat_from_dirname)
        └ それでも解決不能（= 未知ディレクトリ / kind付き digest）なら
             → 旧: "summary" に既定化（★事故の元）
             → 新: category_id を "" のまま返す（date と kind だけ持つ最小 ctx）
```

最小 ctx `{"category_id": "", "date": ..., "kind": ...}` を返すと、呼び出し側の **date/category_id ガード**で自動的に skip される:

- `build_all()` … `if not ctx.date or not ctx.category_id: [skip]` → **個別ページを生成しない**
- `_collect_entries()` … 同条件で `continue` → **LP / カテゴリ / アーカイブ / overview / summary の entries に載らない**

### 2-2. 現在の DeepDive の「正確な」振る舞い

| 項目 | 現状 |
| --- | --- |
| `build_context()` の返り値 | `{category_id: "", date: "2026-05-31", kind: "deepdive"}` |
| 個別ページ (`docs/...`) | **生成されない**（build_all が skip。stderr に `[skip] missing date/category_id: 2026-05-31-DeepDive.md`） |
| LP / カテゴリ / アーカイブ等への掲載 | **されない**（entries に入らない） |
| 本物 Summary への影響 | **無し**（同日 summary は常に 1 本に戻った） |

つまり **DeepDive は現在「どこにもレンダリングされない」**。事故前も実質レンダリングされていなかった（summary 扱いで個別ページ skip されていた）が、本物 Summary を破壊していた。今回の修正で**破壊はしなくなったが、表示もされない**状態に整理された。

### 2-3. サムネ取得（OGP）側の修正（DeepDive と直接は無関係だが同コミット）

[tools/fetch_ogp.py](tools/fetch_ogp.py) の `_OGPParser` が `<body>` 突入で解析停止していたため、Next.js / React SSR（anthropic.com 等）が `<body>` より後方に出す `og:image` を取り逃していた。body-stop を撤去し、og:image と twitter:image が揃い次第早期終了する方式に変更。DeepDive 本文のサムネ取得にも同じ改善が効く。

---

## 3. DeepDive を正式に「表示」したい場合の統合ポイント

DeepDive を LP やどこかのページに出したいなら、**フォールバックに頼らず明示的に統合する**こと。守るべき不変条件と選択肢は以下。

### 守るべき不変条件（破ると事故再発）

1. **DeepDive digest は絶対に `category_id == "summary"` を取らない**（本物 Summary をシャドーする）。
2. **同一日付に `category_id == "summary"` のエントリは高々 1 本**。
3. LP の editorial 選択（`build_index` 内 `next(...summary...)`）が拾うのは**本物の `digest/Summary/{date}.md` 由来エントリのみ**であること。

これらは [tests/test_deepdive_not_summary.py](tests/test_deepdive_not_summary.py) が `assert` で**ビルド失敗（loud）として封じている**。DeepDive を触ってこのテストが落ちたら、それは「Summary をシャドーし始めた」サイン。

### 実装オプション A: DeepDive を独立カテゴリとして追加（推奨・正攻法）

1. [tools/config.py](tools/config.py) の `CATEGORIES` に `"deepdive"` を追加（既存カテゴリと同じ `{jp, label, accent, glyph}` 形）。
2. `build_context()` で `kind == "deepdive"`（または親フォルダ `DeepDive`）を `category_id = "deepdive"` に明示マップ。`""` で skip させている現行ガードの**手前**で分岐する。
3. 専用テンプレ or 既存 `category-template.html` を流用してページ出力先（例 `docs/deepdive/{date}/`）を決める。`_out_path_for()` は `category_id/date` でパスを作るので追従可能。
4. LP に出すなら、`build_index()` の lens cards / Editor's Top 3 とは**別枠**で DeepDive 専用の差し込み口を作る（summary とは混ぜない）。
5. 週次なので「最新 1 本だけ LP に出す」等の選択ロジックを `build_index` 側に足す。

> このオプションは `CATEGORIES`・テンプレ・ルーティング・LP 差し込みの 4 点をいじる新機能。**今回の修正スコープ外**なので未着手。

### 実装オプション B: DeepDive 専用レンダーパスを別関数で持つ

`build_all` / `_collect_entries`（= 日次カテゴリ前提のパイプライン）には載せず、`build_deepdive_pages()` のような独立関数で `digest/DeepDive/*.md` だけを走査して専用ページを出す。日次 LP とは疎結合のまま DeepDive を出せる。LP に出さない/別ハブに置く運用ならこちらが軽い。

> **✅ 採用・実装済み (2026-05-31)**: 本オプション B を採用。`tools/render_deepdive.py` の `build_deepdive_pages()` が `digest/DeepDive/*.md` を独立に走査し `docs/deepdive/{date}/` を生成する。**さらに LP 上部ヒーローに「SUMMARY ⇆ DEEP DIVE」スライダー**を追加し、`build_index()` が `_latest_deepdive_card()` で最新 DeepDive md を**直接読んで独立データとして明示注入**する（`_collect_entries` の entry ストリームには載せないため §3 の不変条件と両立）。hero lead は本文「## 背景」導入段落を `render_emph` で 3 階層強調描画し、Summary スライドと同等の強調を黒背景で再現する。10 秒で SUMMARY ⇆ DEEP DIVE を自動スライドし、ボタンクリックで自動切替を停止する。

---

## 4. 触る前に読むべき箇所（ファイル:行は目安）

| 関心事 | 場所 |
| --- | --- |
| digest 走査（DeepDive も含め全 `digest/**/*.md` を列挙） | `scan_digests()` [tools/generate_pages.py](tools/generate_pages.py) |
| category 解決（今回の修正の中心） | `build_context()` の category_id 決定ブロック |
| 個別ページ生成の skip 条件 | `build_all()` の `[skip] missing date/category_id` |
| LP / 各ページ用 entries 構築 | `_collect_entries()` |
| LP の editorial（本物 Summary）選択 | `build_index()` の `editorial = next(...summary...)` |
| カテゴリ定義（deepdive を足すならここ） | `CATEGORIES` [tools/config.py](tools/config.py) |
| 回帰を封じる契約テスト | [tests/test_deepdive_not_summary.py](tests/test_deepdive_not_summary.py) |

---

## 5. 一言サマリ（DeepDive セッションへ）

> 今は **DeepDive digest を置いても安全（本物 Summary を壊さない）だが、ページにもLPにも出ない**。出したいなら §3 のオプション A/B で**明示的に**統合し、§3 の不変条件 3 つ（特に「summary に化けない」）を守ること。`tests/test_deepdive_not_summary.py` が見張っている。
>
> **更新 (2026-05-31)**: オプション B を実装し、DeepDive は**専用ページ `docs/deepdive/{date}/` と LP 上部ヒーローの SUMMARY ⇆ DEEP DIVE スライダーの両方に出る**ようになった。LP への露出は `build_index()` 内 `_latest_deepdive_card()` の**直接注入**で、entry ストリーム（`build_all` / `_collect_entries`）は一切汚染しないため §3 の不変条件 3 つ（特に「summary に化けない」）は維持されている。`tests/test_deepdive_not_summary.py` に加え `tests/test_deepdive_render.py`（13 件）が見張る。
