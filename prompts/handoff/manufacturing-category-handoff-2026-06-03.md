# News Grasp 新カテゴリー「製造：Manufacturing」追加 — 実装引き継ぎ

- **日付**: 2026-06-03
- **状態**: 設計確定済 + コア3ファイル実装済 / 残9タスクは次セッション
- **対外名/識別子**: カテゴリ key = `manufacturing`、日本語 = 製造、英語 = Manufacturing

---

## 1. 経緯・目的（なぜ作るか）

既存「Mobility」カテゴリーが、Waymo / Tesla / BYD などの **ユーザー向けサービス**寄りニュースに偏り、ユーザーが本当に知りたい以下が拾われない：

- 自動車 OEM の社内事例・車開発の新規計画／中止・停止（例: トヨタ次世代 EV「LF-ZC」開発中止）
- サプライヤー（アイシン / デンソー等 Tier1）の新技術開発・特許・R&D 戦略シフト
- 生産技術（ギガキャスト・全固体電池量産）・工場・サプライチェーン動向

### 根本原因（実コードで確認済）

Mobility の watchlist には既にデンソー・ボッシュ等の Tier1 が入っている（[data/watchlist.md](../../data/watchlist.md) の「電池・サプライヤー」節）。にもかかわらず製造ニュースが拾われないのは **watchlist の問題ではなく選定指標の問題**。現行の重要度スコア（[prompts/routine-system.md](../routine-system.md) 3-A.1）は「影響範囲 35%＝読者の行動が変わる人数」＋「話題性 30%＝拡散量」で計 65% を占め、読者像が「自分の仕事・投資判断に直結するか」を最上位に置く。これは消費者/投資家目線で、**製造・技術・サプライチェーンの「地味だが構造を変える」ニュースは定義上負ける**。よって watchlist 拡充では解決せず、**指標そのものを変える**必要がある（これがユーザーの「異なる指標が必要」という直感の正体）。

### 検証に使った3記事（ユーザー提示）

| 記事 | 性質 | 現行Mobility指標での評価 |
|---|---|---|
| 朝日「トヨタが次世代EV(LF-ZC)を中止」 | 製品計画の中止判断＋ギガキャスト/新型電池継続 | 話題性高で拾われ得るが焦点が消費者目線に寄る |
| TBS/SBS（静岡・地域製造業） | 工場・地域製造動向 | 拡散小→ほぼ拾われない |
| 日経xTECH「デンソー/アイシン特許分析」 | Tier1の技術戦略シフト・R&D | 話題性ゼロ・行動変化ゼロ→構造上絶対に拾われない |

---

## 2. 確定した設計判断（ユーザー承認済）

| 項目 | 決定 |
|---|---|
| 読者像 | **産業・技術の観測者**（製造業従事者／技術者／事業・経営企画）。「何を買えるか」ではなく「製造業の競争力・技術蓄積・サプライチェーンが今どう動いているか」を最上位に置く |
| スコープ | **自動車を核に、車載半導体・電池素材・パワー半導体など隣接部素材まで** |
| スコア軸（製造専用） | 産業インパクト30% / 技術的新規性・深度25% / 戦略的シグナル25% / 一次情報度20%。**話題性（拡散量）は軸から削除**。**時間減衰なし**（ストック型ニュース対応）。**件数下限は3件で可** |
| Mobility境界 | 使う/乗る/サービスを受ける→Mobility、作る/誰が作る/作る計画→Manufacturing。境界記事は「製品計画の意思決定が主題なら Manufacturing」。dedup.py が重複掲載を構造的に防ぐ |
| 掲載頻度 | **平日のみ（月〜金）**。件数 月水金=6 / 火木=7 / 土日=据え置き5 |
| 命名・表示 | key=`manufacturing` / 日本語=製造 / 英語=Manufacturing / accent=**`#5A6B7B`（スチールグレー）** / glyph=**`⬢`**（六角形・ボルト/ナット） |
| 考察への掲載 | **「本日のテーマ考察」(γ schema §) に含める**（sections 8→9） |
| 画像 | **gimp-cli で生成**（スチールグレー基調） |
| 仕様書 | **docs/specs/ に HTML 仕様書を残す**（Mobility 版 2026-05-28 と同様） |

---

## 3. 完了済み（このセッションで実装）

> 全て影響調査（Read で全関連箇所を確認）後の計画的編集。手戻り・リトライなし。

### ① [tools/config.py](../../tools/config.py)
- `CATEGORIES` dict に `"manufacturing": {"label": "Manufacturing", "jp": "製造", "accent": "#5A6B7B", "glyph": "⬢"}` を **mobility の直後**に追加
- 並び順コメントを `fx → ai → it → mobility → manufacturing → economy → game` に更新

### ② [tools/generate_pages.py](../../tools/generate_pages.py)
- `TAG_TO_CID` に `"製造": "manufacturing"` を追加（モビリティの後）
- **fallback 定数3つを 7→9 要素化**（`_SUMMARY_SECTION_TAGS` / `_SUMMARY_SECTION_COLORS` / `_SUMMARY_CAT_ORDER`）。同時に **Mobility 追加時の積み残しバグ（mobility 未反映）も是正**。順序＝総論/為替/AI/IT/モビリティ/製造/経済/ゲーム/明日へ
- `_fallback_sections`：`range(7)` → `range(len(_SUMMARY_SECTION_TAGS))`、`i == 6`（明日へ判定）→ `i == last`（リスト長連動）に変更。**今後カテゴリ増減しても自動追従する構造**にした
- docstring の `7-grid` → `9-grid`、`§02-06` → `§02-08` を是正（2箇所）

### ③ [prompts/routine-system.md](../routine-system.md)
- ステップ1の対象カテゴリ決定ルールに「Economy と Manufacturing は平日のみ」
- 曜日別カテゴリ表に Manufacturing を平日追加（件数 月水金6 / 火木7）＋平日限定・件数下限の注記
- デザインシステム表に製造行（`#5A6B7B` / `⬢`）
- **新節「3-A.1-M Manufacturing（製造）カテゴリの重要度スコア特則」を 3-A.1 直後に挿入**：専用読者像 + 4軸テーブル + 3-A.1との差分（話題性削除・時間減衰なし・件数下限緩和）+ Mobility 境界ルール
- γ schema sections を 8→9（製造を §06 に挿入、経済/ゲーム/明日へを 7/8/9 へ）
- γ schema 必須ルール「sections は必ず 9 件」に更新
- ステップ7 NGプレースホルダ keys に `ng-thumb-manufacturing` / `ng-thumb-common-manufacturing`
- ステップ5-A の Genre 例に `Manufacturing`
- takeaways の color 候補に `#5A6B7B(製造)` を追加（2箇所）

---

## 4. 残タスク（次セッション）— 受領全量12 − 実装済3 = 9

### ④ [data/watchlist.md](../../data/watchlist.md) に Manufacturing セクション新設
Mobility の次（economy の前）に追加。**Mobility の「電池・サプライヤー」節と内容が重なるが、Manufacturing は "作り手・生産技術" 視点で別立て**（Mobility 側は消費者向けに据え置き）。追加する内容案：

- **見出し**: `## Manufacturing`（平日掲載。OEM の生産・開発、Tier1/2、生産技術、車載半導体・電池素材を "作り手" 視点で追跡）
- **優先情報源（媒体）**: 日経xTECH / 日経Automotive / 日経ものづくり / 日刊工業新聞 / 各社IR・適時開示 / プレスリリース / Google Patents・J-PlatPat / EE Times Japan / 日経エレクトロニクス / Nikkei Asia / レスポンス / 地域局（静岡新聞SBS等）
- **完成車OEM（生産・開発視点）**: トヨタ / レクサス / 日産 / ホンダ / スバル / マツダ / 三菱自動車 / スズキ / ダイハツ / BYD / VW / テスラ（工場・生産）
- **Tier1/Tier2 サプライヤー**: デンソー / アイシン / 豊田自動織機 / ジェイテクト / 豊田合成 / トヨタ紡織 / 日本電産(ニデック) / ボッシュ / コンチネンタル / ZF / ヴァレオ / マグナ / 現代モービス
- **車載半導体・電池素材**: ルネサス / ローム / 三菱電機(パワー半導体) / 富士電機 / 東芝(パワー半導体) / インフィニオン / STマイクロ / オンセミ / TSMC(車載) / CATL / パナソニックエナジー / 日本製鉄(電磁鋼板) / JFE / 旭化成(セパレータ)
- **生産技術キーワード**: ギガキャスト / メガキャスト / 一体成形 / 全固体電池 量産 / LFP 内製 / 工場新設 / マザー工場 / 設備投資 / 内製化 / 歩留まり / EV 専用ライン / 生産能力 / サプライチェーン / 車載半導体 内製 / 特許出願 / リコール対応(品質)

### ⑤ [prompts/obsidian-tagging-spec.md](../obsidian-tagging-spec.md)
- `cat/` 一覧（§4 付近）に `cat/manufacturing` を追記。Grep で `cat/mobility` の所在を確認し、その隣に1行追加

### ⑥ ~~prompts/email-template.html~~ (2026-06-05 削除)

- メール配信機能ごと削除されたため、本ステップはスキップ。`.acMf` クラスは公開 Web 側 (`docs/assets/site.css`) でのみ管理する

### ⑦ テスト更新
- [tests/test_reflection_theme_essay.py](../../tests/test_reflection_theme_essay.py) **L203-206**: `len(r["sections"]) == 8` → **`== 9`**。sections は dict[int]（1始まり）。モビリティは `[5]` のまま、**製造が `[6]`、明日へが `[8]`→`[9]`** にずれる。期待 index を要調整（mock digest 側に §06 製造を足す必要あり）
- [tests/test_generate_pages.py](../../tests/test_generate_pages.py) **L64-69**: `test_categories_include_all_six` の `expected` に `manufacturing`（と積み残しの `mobility`）を追加。`issubset` 判定なので現状は落ちないが、テスト名/docstring「six」も実態（8）に合わせて是正
- [tests/test_category_editorial_essay.py](../../tests/test_category_editorial_essay.py) **L138 付近**: parametrize リストに `("manufacturing", "Manufacturing")` を追加

### ⑧ pytest 実行
```powershell
cd "C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp"
.venv\Scripts\python.exe -m pytest -q
```
全 PASS を確認（特に test_reflection_theme_essay / test_generate_pages / test_category_editorial_essay）。**完了報告には PASS 件数と実測を含める**（feedback_test_before_report）。

### ⑨ gimp-cli で NG プレースホルダ画像3枚（スチールグレー #5A6B7B 基調）
- `ng-thumb-manufacturing.jpg`（568×220、FEATURED 用）→ **public repo `HIDEPON-UMG/news-grasp-assets`** の main に配置
- `ng-thumb-common-manufacturing.jpg`（140×90、サイドサムネ用）→ 同上
- `assets/og/manufacturing.jpg`（OG デフォルト）→ News-Grasp 側の `docs/assets/og/`（[generate_pages.py](../../tools/generate_pages.py) `resolve_og_image` の `{BASE_URL}/assets/og/{category_id}.jpg`）。既存の他カテゴリ og 画像の所在を確認して同じ場所に
- gimp-cli skill 使用。既存の `ng-thumb-mobility.jpg` 等のサイズ・トンマナを参照してから生成

### ⑩ docs/specs/2026-06-03_newsgrasp-manufacturing-category.html
- 既存 [docs/specs/2026-05-28_newsgrasp-mobility-category.html](../../docs/specs/2026-05-28_newsgrasp-mobility-category.html) をベース構成に
- `~/.claude/templates/SPEC.html` 継承、DESIGN.md トークン（または News-Grasp の配色）を CSS 変数で。本セッションの設計判断（§2）・3記事の論点・指標差分を記録
- 色直書き禁止 / 外部CDN禁止 / 画像はSVG（safe-commit ゲート4'）

### ⑪ commit（safe-commit 経由）
- `safe-commit` skill のゲートを通す。**push は実行しない**（News-Grasp は `news-grasp-runner.ps1` が Claude 終了後に push する設計。Bash 経由 push は hook で deny される）
- commit 対象: config.py / generate_pages.py / routine-system.md / watchlist.md / obsidian-tagging-spec.md / tests/ / docs/specs/ + handoff。画像 repo は別 commit (※ 旧版では email-template.html も対象だったが 2026-06-05 メール配信機能ごと削除済み)

---

## 5. 重要な技術メモ（次セッションで踏むと事故る点）

- **dedup.py はカテゴリ非依存**（URL/タイトル/トークンで判定）。変更不要・影響なし
- ~~**メール経路と Web 経路は別**~~ (2026-06-05 メール経路削除): Web（docs）は `generate_pages.py` が `CATEGORIES` を dict 駆動で回すので config.py 追加でほぼ自動対応。**メール経路は機能ごと廃止済み**のため `.acMf` クラスは公開 Web 側 (`docs/assets/site.css`) でのみ管理
- **fallback 定数の mobility 抜けは積み残しバグ**だった（digest 正常時は `parse_essay_sections` の動的パースが使われ、定数は使われないため顕在化していなかった）。今回是正済み
- **sections は dict[int]（1始まり）**。製造を §06 に挿入したので、テストの期待 index がずれる（経済 §06→§07、ゲーム §07→§08、明日へ §08→§09）。mock digest にも §06 製造セクションを足さないとテストが通らない
- 次セッション開始時、**グループA（実装）の必読 memory** を Read: `feedback_impact_analysis_before_modification` / `feedback_pre_implementation_checklist` / `feedback_japanese_env_first_scripting`
- routine-system.md のテーブルに IDE が出す **MD060 警告は既存スタイル踏襲**なので無視可（機能影響なし）

---

## 6. このセッションでの未確定事項（次セッションで判断 or ユーザー確認）

- watchlist の企業リストは §4 の案。**Mobility 側のサプライヤー節を縮小するか／重複させたまま境界ルールで振り分けるか**は未決（現案は重複容認＋境界ルールで振り分け＝Mobility 側は無変更）
- `assets/og/manufacturing.jpg` の正確な配置先（他カテゴリ og 画像の実在を確認してから）
