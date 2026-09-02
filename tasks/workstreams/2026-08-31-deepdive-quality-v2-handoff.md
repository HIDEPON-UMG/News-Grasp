---
schemaVersion: NEWS_GRASP_DEEPDIVE_QUALITY_HANDOFF_V1
state: user_stopped_for_session_transfer
createdAt: 2026-08-31T16:01:21+09:00
repoRoot: C:\Users\hidek\OneDrive\ドキュメント\ProjectFolders\News-Grasp
baselineCommit: e6e704ddfac57ce9c3c8db40b8cab45fa46deeff
remoteBaseline: e6e704ddfac57ce9c3c8db40b8cab45fa46deeff
currentGeneration: 1
currentTodoId: No.04/A-04
nextCriticalOperation: Red fixtureを追加し、現行validatorが公開claim-source露出と対談混入を見逃すことを失敗として固定する
unresolvedDecisionIds: []
publicMutationPerformed: false
commitPerformed: false
pushPerformed: false
---

# DeepDive品質劣化・全履歴遡及修正 引継ぎ

## 1. 最新ユーザー意図と停止理由

ユーザー要求は、直近1週間のDeepDiveで露呈した問題を単発修正せず、News-Grasp `docs/spec.md` 違反を全件監査し、過去成果物を遡及修正し、日次バッチ、修復経路、監査、News-Grasp関連skillまで根本修正することである。skillの既存文面は絶対ではなく、Specと読者価値へ従属させる。

このセッションは、Codex UIに次の文言が表示され、ユーザーが別セッションへ移すと明示したため停止した。

> Selected model is at capacity. Please try a different model.

この表示はNews-Grasp repoの失敗、品質修正の完了、ユーザー契約プランの事実として扱わない。会話添付画像で観測された外部UI状態だけである。次セッションは利用可能モデルで再開し、この文言の真偽を推定しない。

## 2. 現在の結論

現行監査は偽陰性を起こしている。存在、件数、hash、URL href、完全一致の反復を主に検査し、次を判定していない。

1. 公開HTMLへescape済み内部コメントが表示されること。
2. 対談へclaim-source JSON、URL、Markdown断片が混入すること。
3. 関係種別が未定義でも既定黒線へ縮退し、異なる意味が同じ色になること。
4. 固定幅1080pxの関係図をモバイルで横スクロールさせ、全体関係を把握できないこと。
5. 先輩役が全発話で `です／ます` を使い、指定人格と矛盾すること。
6. 7価値を14ターンの固定scaffoldへ流し込み、記事語だけを差し替えること。
7. 文字数・想定分数の下限を満たすため、後半へ一般論と過去文脈を継ぎ足すこと。
8. 根拠不足時に追加調査せず、本文から14文を先頭順に切り出して対談を決定論的生成すること。
9. 見出しを本文証拠として複製し、見出し一覧をDeepDiveとして公開できること。
10. repair handlerが同じ決定論的generatorを呼び、同じ不良を再生成すること。

既存の対象テスト一式はGreenだが、実成果物の不良を検出していない。既存Greenを完了根拠にしない。

## 3. 全履歴監査の一次証拠

対象:

- DeepDive記事: 91本、2026-05-31〜2026-08-31
- DeepDive対談: 71本、2026-06-21〜2026-08-31
- 直近問題期間: 2026-08-26〜2026-08-31（2026-08-25は成果物なし）

観測結果:

| 違反クラス | 件数 | 対象 |
|---|---:|---|
| 公開HTMLにclaim-sourceが可視露出 | 4本 | 2026-08-28〜2026-08-31 |
| 対談にclaim-source/sourceUrlが混入 | 5本 | 2026-08-27〜2026-08-31 |
| 現行固定対談scaffold | 60本 | 2026-07-02〜2026-08-31 |
| 先輩の敬体終止 | 60本 | 同上。各本7/7先輩発話で検出 |
| URLまたはMarkdown断片を含む対談 | 5本 | 2026-08-27〜2026-08-31 |
| 未定義relation kindを含む記事 | 44本 | 全91本中 |
| 複数意味が同じstyleへ潰れる、または1styleだけの関係図 | 17本 | 2026-06-09、06-17、06-25、07-03、07-26、08-03、08-04、08-05、08-07、08-13、08-18、08-20、08-21、08-22、08-29、08-30、08-31 |
| 現行 `audit_issue` がGreenなのに上記既知不良を含む対談 | 60本 | 2026-07-02〜2026-08-31 |

直近記事の追加観測:

- 2026-08-30: `見出し` 18回、claim 5件中5件で `evidence == claim`。
- 2026-08-31: `見出し` 34回、claim 5件中5件で `evidence == claim`。同じ2段落を記事名だけ変えて5回反復している。
- 2026-08-29: claim 2件中1件で `evidence == claim`。
- 2026-08-30関係図: 5種類のkindが全て未定義で、全5辺が同じ黒線。最大次数5/5の単一hub。
- 2026-08-31関係図: 4種類のkindが全て未定義で、全4辺が同じ黒線。

現行共有監査を全91記事へ実行した結果は21 Green / 70 Red。旧日付の70 Redは主にprovenance欠落、article path drift、旧対談価値marker欠落である。一方、2026-08-27〜31は現行監査でGreen（08-26だけlegacy claim manifest Red）になり、今回の品質不良を見逃している。

## 4. 再現コマンド

### 4.1 直近1週間の現行監査

```powershell
.\.venv\Scripts\python.exe -m tools.deepdive_quality --repo-root . audit-period --start 2026-08-25 --end 2026-08-31 --require-rendered-public
```

期待観測: 08-25 missing、08-26 legacy claim manifest Red、08-27〜31 Green。これは正しい合格ではなく偽陰性の再現である。

### 4.2 現行テスト基準線

```powershell
python -m pytest -q tests\test_deepdive_dialogue_value_contract.py tests\test_deepdive_quality_engine.py tests\test_deepdive_render.py tests\test_validate_daily_quality.py tests\test_repair_registry.py tests\test_repair_coverage_matrix.py
```

実測: exit 0。2件skip（Windows symlink権限、2026-06-28 audio fixture欠落）。

注意: `.\.venv\Scripts\python.exe -m pytest ...` は `No module named pytest`。同じshapeを再試行せず、上記system `python` を使う。

### 4.3 全履歴の構造監査

このセッションではinline Pythonで `tools.render_deepdive.extract_blocks`、`EDGE_KINDS`、`tools.tts.deepdive_dialogue.parse_dialogue`、`validate_dialogue_document` を呼び、上表を集計した。次セッションは再走査から始めず、まず本資料の件数をfixtureへ固定する。必要な場合だけ同じ集計を再実行する。

## 5. 根本原因と責任surface

### 5.1 内部metadata露出

- `tools/generate_pages.py:770-785` の `_render_emph` が本文をHTML escapeし、`<!-- claim-source ... -->` を表示文字列へ変える。
- `tools/render_deepdive.py:170-174` の `_prose_paragraphs` はfenced JSONだけを除き、claim-source commentを除去しない。
- `tools/deepdive_quality.py:738-789` の `validate_rendered_public_surface` はhrefとsource SHAを検査するだけで、内部marker、raw Markdown、JSON露出を拒否しない。

### 5.2 関係図

- `tools/render_deepdive.py:62-107` の `EDGE_KINDS` は `競合 / 規制 / 出資 / 提携 / 供給 / 対立` だけ。
- 未定義kindは `_DEFAULT_EDGE` の黒線へsilent fallbackする。
- `prompts/deepdive-research-system.md:142-171` は正規語彙を指示するが、validatorが逸脱を拒否しない。
- `tools/validate_daily_quality.py:863-923` と `tools/output_quality.py:94-165` は主にnode貫通と一部重なりだけを検査し、色の意味対応、未知kind、全体把握、示唆性を見ない。
- `tools/render_deepdive.py:1340-1370` と `prompts/deepdive-template.html:392-398` は固定幅SVG＋モバイル横スクロールを意図的に採用している。今回のユーザー指示は、現在の実害に基づきこの旧判断を上書きする。

### 5.3 対談

- `tools/tts/deepdive_dialogue.py:207-220` の `source_evidence_sentences` がHTML comment、URL、claim JSON、Markdownを除去せず、根拠文として採用する。
- `tools/tts/deepdive_dialogue.py:164-341` は役、turn数、文字数、exact duplicate、7 value marker、source先頭48文字程度を検査するが、口調、接続、情報密度、具体的next actionを見ない。
- `tools/tts/build_deepdive_dialogue_script.py:287-369` は7価値×2turnの固定テンプレート。先輩文を全て敬体で生成する。
- `_clip` がcomment/URL/JSONを途中で切り、壊れた断片を発話へ入れる。
- `audio_target_minutes` はcontext数で5/6分を決め、内容価値から導出していない。
- `tools/deepdive_quality.py:1200-1325` のmaterializerと `tools/repair_registry.py:1548-1581` のrepairが同じ固定generatorを呼ぶ。
- `tools/repair_coverage_matrix.py:469-503` は `deepdive_dialogue_value_invalid` をdeterministic handlerへ分類している。

### 5.4 記事の深さ

- claim-source validatorはclaimとevidenceの同一文を拒否しない。
- 見出し、公開日、URL生存を確認できれば、本文を読まずに反復した段落でも公開できる。
- `docs/spec.md:217-231` のEditorial Quality Bar（見出し一覧でなく、示唆・関係・次の確認）と矛盾する。
- `docs/spec.md:391-397` のDeepDive Source and Podcast Value Covenantは固定scaffoldをfatalとする一方、repairをdeterministic handlerへ固定しており、実装と自己矛盾していた。

## 6. このセッションで行った変更（部分実装、未検証）

次の2点だけを変更した。production code、test、過去成果物、公開面はまだ変更していない。

1. `docs/spec.md:399` に `DeepDive Publication Quality V2（2026-08-31 user-confirmed amendment）` を追加。
   - 内部metadata非露出
   - claimとevidenceの複製禁止
   - 見出し一覧・固定段落反復禁止
   - relation kind正規語彙と一意style
   - モバイル専用配置
   - 先輩常体／若手敬体
   - 固定1問1答と引き延ばし禁止
   - 根拠不足時の追加調査
   - semantic rubric
   - 全履歴V2監査
2. `config/deepdive_quality_routes.json:2` を `DEEPDIVE_SHARED_QUALITY_ROUTES_V2` へ上げ、次のissue codeを追加。
   - `deepdive_article_value_invalid`
   - `deepdive_relation_quality_invalid`
   - `deepdive_research_evidence_insufficient`

これらは未検証であり、route consumer、repair matrix、testとの同期前である。次セッションはこの差分を戻さず、Red fixtureとconsumer同期を続ける。

## 7. 既存ユーザー差分との境界

作業開始時から次の差分が存在した。このセッションの所有物ではないため、戻さない。

```text
 M AGENTS.md
 M CLAUDE.md
 M automation/news-grasp-6-40/automation.toml.template
 M automation/skills/news-grasp-direct-mainline/SKILL.md
 M config/news_grasp_constitution_projection_v1.json
 M docs/spec.md
 M docs/specs/2026-08-12_news-grasp-product-constitution.html
 M tests/test_codex_runner_contract.py
 M tests/test_news_grasp_direct_mainline_integration.py
 M tests/test_product_spec_contract.py
 M tools/news_grasp_direct_runtime.py
 M tools/news_grasp_e2e_contract.py
 M tools/news_grasp_human_impact.py
 M tools/sync_news_grasp_codex_automation.py
?? scripts/ops/install-news-grasp-title-materializer.ps1
?? scripts/ops/news-grasp-title-materializer.pyw
?? tests/test_news_grasp_title_materializer.py
?? tools/news_grasp_title_materializer.py
```

`docs/spec.md` と `automation/skills/news-grasp-direct-mainline/SKILL.md` は既存title materializer変更と重なる。必ず `git diff -- <file>` を読み、追加行だけを統合する。

## 8. Append-only TODO状態

| ID | 工程 | 状態 | 内容 |
|---|---|---|---|
| No.01/A-01 | 要件・影響調査 | completed | 91記事・71対談・公開HTML・関係図・修復経路・skillの違反台帳 |
| No.02/A-02 | 設計・事前review | completed | DeepDive Publication Quality V2をSpecへ追加 |
| No.03/A-03 | 設計・事前review | completed | 4共有経路とV2 issue code設計 |
| No.04/A-04 | 実装＋unit | in_progress | 内部metadata／Markdown／URL露出のRedと修正 |
| No.05/A-05 | 実装＋unit | pending | relation kind・色・配置・モバイル・示唆性 |
| No.06/A-06 | 実装＋unit | pending | 対談人格・接続・scaffold・引き延ばし・調査追加 |
| No.07/A-07 | 実装＋unit | pending | deterministic repairを調査／書き直しrouteへ変更 |
| No.08/A-08 | 実装＋unit | pending | Spec・prompt・News-Grasp関連skill同期とskill validate |
| No.09/A-09 | 結合テスト | pending | 4共有経路、daily gate、repair、unknown route、過去fixture |
| No.10/A-10 | 過去遡及修正 | pending | 全履歴V2監査と正規経路での再生成 |
| No.11/A-11 | runtime canary | pending | 最新号＋代表過去号の公開相当surface／TTS前監査 |
| No.12/A-12 | 最終review | pending | 独立review、回帰、残件、公開操作境界 |

## 9. 次セッションのexact successor

次セッションは**必ずPlan Modeで開始する**。最初はread-only調査だけを行い、本資料と現行差分を根拠に、影響範囲、修正方式、過去遡及範囲、Red→Green検証、公開・commit・push境界を含む実装計画を提示する。ユーザーが計画を承認するまでproduction code、test、skill、過去成果物を変更しない。

新セッションへ渡す開始文面:

```text
必ずPlan Modeで開始してください。News-Grasp/tasks/workstreams/2026-08-31-deepdive-quality-v2-handoff.md を引継ぎ正本として完全に読み、既存差分を戻さず、まずread-onlyで現状を照合してください。その上で、DeepDive Publication Quality V2、4共有経路、過去遡及修正、Red→Green検証、関連skill修正、公開・commit・push境界を含む実装計画を提示してください。私が計画を承認するまでファイル変更を開始しないでください。承認後は No.04/A-04 のRed fixture追加から再開してください。
```

承認後は次の順で再開する。

1. 本資料、repo `AGENTS.md`、workspace root正本、`News-Grasp/docs/spec.md:391-430` を読む。
2. `git status --short` と `git diff -- docs/spec.md config/deepdive_quality_routes.json` を確認する。既存差分を戻さない。
3. No.04のRed fixtureを先に追加する。
   - escape済み `&lt;!-- claim-source` を公開validatorが拒否する。
   - raw comment、claim JSON、sourceUrl、raw Markdownを拒否する。
   - `_prose_paragraphs` と `source_evidence_sentences` が内部metadataを出力しない。
   - `evidence == claim` を `deepdive_article_value_invalid` とする。
4. Redを実測してからproduction codeを最小修正する。
5. No.05以降も同じRed→Green順を守る。後工程で新surfaceが見つかった場合はNo.01のimpact inventoryへ追加してから進む。

最初のtest候補:

- `tests/test_deepdive_quality_engine.py`
- `tests/test_deepdive_render.py`
- `tests/test_deepdive_dialogue_value_contract.py`
- `tests/test_tts_build_deepdive_dialogue_script.py`
- `tests/test_validate_daily_quality.py`
- `tests/test_repair_coverage_matrix.py`
- `tests/test_repair_registry.py`
- `tests/test_deepdive_quality_route_contract.py`

## 10. 実装方針の決定事項

- shared engineは `tools.deepdive_quality` を維持し、production generation、repair/publish、daily quality、Codex daily auditで分岐実装を作らない。
- transport metadataはsource/manifestに保持してよいが、公開HTMLとTTS入力では必ず除去し、validatorでも二重に拒否する。
- relation kindは `提携 / 出資 / 供給 / 競合 / 対立 / 規制 / 統制 / 依存`。unknown fallbackは禁止。
- モバイルはdesktop固定幅SVGの横スクロールだけで合格にしない。専用配置または同等の全体把握surfaceを作る。
- 先輩は常体、若手は敬体。現在の固定generatorを「語尾だけ直す」対応は不可。
- 文字数、音声時間は品質点にしない。上限だけを生成暴走の安全弁にする。
- 14根拠が足りない場合は追加調査へ戻し、modulo、切り刻み、一般論追記で埋めない。
- `deepdive_dialogue_value_invalid` はdeterministic rebuildではなくLLM rewrite/research routeへ変更する。
- semantic rubricは `theme_specific_insight / evidence_depth / causal_coherence / counterevidence / decision_utility / dialogue_naturalness / relation_map_utility`。各1〜5、2以下が一つでもあるか平均4未満ならRed。長さ・時間・コスト・存在は採点しない。
- 全履歴修正は新validatorがRedにしたlineageだけを正規経路で再構築する。ad hoc手編集を正規手段にしない。

## 11. 未実行・禁止事項

- commitしていない。
- pushしていない。
- public Pages、YouTube、audio、notificationを変更していない。
- full E2Eを実行していない。
- 過去91記事／71対談を再生成していない。
- installed skillやCodex automationを同期していない。
- UI/Chromeを操作していない。
- 別セッションはユーザーの明示がない限り、既存title materializer差分を整理・rollbackしない。
- public反映、commit、pushはそれぞれローカル実装・検証と状態分離して報告する。

## 12. 完了条件

このtaskは現在未完了。少なくとも次がすべて必要である。

1. 新Red fixturesが今回の実害と過去corpusを検出する。
2. 4共有経路が同じV2判定を使う。
3. production/repairが固定scaffoldを再生成しない。
4. 最新号と代表過去号の公開相当HTMLで内部metadata非露出、関係図の色・モバイル全体把握、対談口調・自然さを確認する。
5. 全履歴V2監査結果と修正対象一覧が残件0またはtyped external blockerになる。
6. 関連skillをSpecへ同期し、skill validatorとrepo契約testを通す。
7. focused回帰、broad回帰、独立reviewを最終mutation後に行う。
8. commit/push/public反映がユーザーscopeに含まれる場合だけ、各状態を別証拠で閉じる。
