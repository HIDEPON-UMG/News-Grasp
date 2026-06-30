# Product Spec: News-Grasp

> **Status**: Constitution
> **Last Updated**: 2026-06-28
> **Owner**: News-Grasp Operator

## Product Constitution

News-Grasp は、繁忙なITコンサルタントが膨大なニュースを一つ一つ確認せず、重要論点を効率よく把握できる、分かりやすく実務示唆のあるニュース情報源である。

ミッションは、ITコンサルタントにとって最適・最良の情報を収集し、最適な粒度と効果的な伝達方法で届け、収集から執筆・編集・公開まで完全自立型ニュースサイトとして運営することにある。

この `docs/spec.md` は News-Grasp の上位プロダクト真実であり、日次バッチ、公開面、品質 gate、Podcast、通知、incident、runner state の改修判断はこの憲法に従う。

| Area | Requirement |
|---|---|
| Primary reader | 繁忙なITコンサルタント。一般ニュース読者ではなく、業務判断に使える粒度と示唆を必要とする読者。 |
| Core value | ニュース量を圧縮しつつ、重要論点、背景、影響、次に見るべき観点を短時間で掴めること。 |
| Delivery model | Web / Audio / YouTube Podcast / playlist / notification を整合した単一の公開体験として届けること。 |
| Operating model | 人手の常時介入を前提にせず、収集、執筆、編集、品質修復、公開、検証まで自走すること。 |

## Principle 1: 直せるものは直して完走

品質 gate は「問題を見つけたので止める」ためだけに存在しない。既知の品質問題は、止める前に repair、quarantine+refill、reporter retry、re-verify のいずれかへ分類し、修復可能な範囲では直せるものは直して完走する。

ただし、完走は壊れた公開を押し通すことではない。修復予算を超えた失敗、未知分類、外部依存、配信不能、security risk は typed fatal として止め、状態、exit code、incident evidence を残す。

## Definition of Done

通常日次バッチの OK marker は、次の成果物と公開面がすべて verified になった後にだけ書く。

| Area | Requirement |
|---|---|
| Digest | 当日の対象カテゴリ digest が揃い、Summary が当日の論点を統合している。 |
| DeepDive | DeepDive md と DeepDive HTML が生成され、公開ページから参照できる。 |
| Web | 日付 docs、カテゴリページ、summary ページ、GitHub Pages 反映、公開 URL sentinel が確認済み。 |
| Audio | TTS public audio が生成され、公開ページから再生可能な状態になっている。 |
| Podcast | YouTube Podcast が public 化され、playlist 反映まで verified になっている。 |
| Notification | 通知送信が完了するか、送信不能理由が typed status として残っている。 |
| State | runner state、distribution state、OK marker が同じ日付と同じ run intent を指している。 |

## Editorial Quality Bar

News-Grasp の記事は、ITコンサルタントが業務の隙間で読むことを前提にする。単なるニュース羅列ではなく、論点、背景、示唆、関係性、次の確認観点を明確にする。

必須品質は次の通り。

| Area | Requirement |
|---|---|
| Accuracy | 事実、日付、企業名、URL、引用関係が検証可能である。 |
| Relevance | ITコンサルタントの提案、調査、設計、顧客対話、意思決定に関係する論点を優先する。 |
| Granularity | 忙しい読者が短時間で要点を掴める粒度に圧縮し、必要な深掘り先も残す。 |
| Insight | 「何が起きたか」だけでなく「なぜ重要か」「どこに影響するか」を示す。 |
| Readability | 見出し、要約、カテゴリ、DeepDive、音声の伝達方法が相互に補完する。 |
| Source health | 出典 URL は記事単位の canonical URL を優先し、媒体トップやカテゴリトップで代替しない。 |

## System Integrity

News-Grasp は、部分成果の集合ではなく、読者が見る公開体験として成立して初めて成功とする。

Web / Audio / YouTube Podcast / playlist / notification は別々の付録ではない。どれか一つを WARN に落として OK にする場合は、この憲法の Definition of Done を満たさない理由を typed status と incident evidence に残す。

runner、watcher、repair、publish verification、podcast verification、distribution state は、同じ日付、同じ成果物、同じ完了条件を見なければならない。局所最適な修正で、別工程の正本や公開面との整合を壊してはならない。

## Fatal Boundaries

完全自立型は、外部依存や危険状態を無理に突破する意味ではない。次の状態は自動修復対象外とし、typed fatal で止める。

| Area | Requirement |
|---|---|
| Secrets | secret leak、OAuth secrets 欠落、認証情報破損は公開を進めない。 |
| External quota | YouTube quota、API project 制約、GitHub outage は外部依存として分離する。 |
| Repository safety | git push rejected、remote divergence、公開正本の重複リスクは人間が追える状態で止める。 |
| Security | security risk、権限逸脱、機密露出の疑いは完走より停止を優先する。 |
| Unknown class | 既知 handler に分類できない失敗は、推測で修復せず typed fatal とする。 |

## Change Governance

非自明な News-Grasp 改修では、計画段階でこの `docs/spec.md` との差分を確認する。特に、完了条件、配信経路、品質 gate、runner state、Podcast、通知、incident、主要ユーザー価値へ触る変更は、次を満たす。

| Area | Requirement |
|---|---|
| Constitution fit | 変更が「ITコンサルタントに最適・最良の情報を届ける」目的にどう効くかを書く。 |
| System fit | 前工程、当該工程、後工程、公開面の整合を確認する。 |
| Repair first | 直せる品質問題を停止で済ませていないか確認する。 |
| Verification | 契約テスト、dry-run、publish verify、podcast verify など自己完結の検証を置く。 |
| Decision record | 憲法に関わる判断を変える場合は、incident report、ADR、または計画書に context と consequence を残す。 |

## Feature Change Quality Gate Matrix

機能を追加、削除、修正する場合は、実装だけでなく同じ変更単位で品質 gate、契約テスト、公開検証、runner state、完了報告のどれを更新するかを先に決める。機能の成果物が Definition of Done のいずれかへ届くなら、その成果物を作る工程だけでなく、前工程の入力契約、当該工程の失敗分類、後工程の公開確認までを 1 セットで扱う。

次の表を変更計画の最低チェックリストとする。該当する行があるのに gate 更新が不要な場合は、不要理由を計画または incident evidence に残す。

| Change area | Update with the feature change | Minimum gate / test |
|---|---|---|
| Source collection / URL freshness / dedup | watchlist、検索 query、URL 正規化、公開日 freshness、重複 / follow-up 判定、`data/search_audit` を更新する。 | URL liveness / freshness / dedup 契約テスト、`tests/test_all_article_urls_live.py`、`tests/test_dedup_freshness.py`、`tests/test_dedup_followup_gate.py`。 |
| Article data / schema / tags | `data/articles.jsonl` schema、frontmatter、Obsidian tags、entities / topics / industries / events を更新する。 | `tools.validate_record`、`tests/test_validate_record.py`、tag / session URL / article append 系契約テスト。 |
| Digest / category schedule | 対象カテゴリ、休載条件、記事数不足時の refill / quarantine、`data/search_audit` 契約を更新する。 | `tools.validate_daily_quality --date <date> --require-deepdive`、カテゴリ presence / search audit 契約テスト。 |
| Summary / editorial reflection | Summary 構造、reflection、hero、key takeaways、日付 docs への反映を更新する。 | summary reflection 系テスト、`validate_daily_quality`、公開日付 docs sentinel。 |
| DeepDive | md、HTML、関係図、日付ページからの導線、公開 inventory を更新する。 | `--require-deepdive`、DeepDive presence / relation layout テスト、公開 URL sentinel。 |
| Public UI / OGP / PWA / thumbnails | template、CSS、OGP meta、thumbnail contract、manifest、service worker cache、offline page を更新する。 | `tests/test_pwa_meta.py`、`tests/test_thumb_contract.py`、`tests/test_fetch_ogp.py`、必要時 Chrome操作系スキルでの visual smoke と `docs/sw.js` version bump。 |
| Web publish surface | `docs/<date>/index.html`、summary、per-category docs、public status、GitHub Pages 反映を更新する。 | `verify-publish`、published docs presence、public URL 200 / sentinel、remote HEAD / Deploy workflow success / workflow Pages status built。 |
| Audio / TTS | 音声生成、release URL、ページ埋め込み、再生可能性、TTS required gate を更新する。 | TTS publish gate、audio URL presence、`verify-publish` audio check。 |
| YouTube Podcast / playlist | upload state、public video、playlist 反映、Daily Podcast と DeepDive Podcast の playlist 境界、同日重複禁止、Deleted video item 禁止、外部検証 fallback、token / quota / permission の typed status を更新する。 | `verify-podcast`、`tools.youtube_podcast.upload_episode <date> --audit-playlists`、`verify-publish --require-podcast`、外部 API 401/403/404 fallback 契約テスト、runner convergence 契約テスト。 |
| Notification | 送信条件、通知不要条件、失敗時 typed status、再送可否を更新する。 | notification dry-run / typed status テスト、送信不要時の完了条件テスト。 |
| Runner / state / recovery | full run / RecoverOnly / fallback publish / OK marker / distribution state の遷移を更新する。 | runner convergence / state watcher 契約テスト、full と RecoverOnly の両経路 dry-run。 |
| Incident / reporting | 障害 evidence、公開 inventory、報告 HTML、完了報告の必須項目を更新する。 | incident report validator、公開 inventory 確認、`tests/test_product_spec_contract.py`。 |
| External integration / auth | OAuth、API quota、権限、token expiry、公開反映遅延の failure domain を typed status に分ける。 | auth/quota/permission の fixture、retry しない fatal と fallback 可能な verify failure の分類テスト。 |

非自明な変更計画と完了報告には、必ず「Affected matrix rows」「Gate update decision」「Verification command」を書く。該当する row が無い機能を追加、削除、修正する場合は、実装と同じ変更単位でこの `Feature Change Quality Gate Matrix` と `tests/test_product_spec_contract.py` を更新してから完了扱いにする。

UI 修正、CSS 修正、PWA 修正、generated docs 修正が public surface に届く場合、local test pass や local DOM/visual sentinel だけでは完了ではない。公開が成功条件に含まれる作業は、commit、push、local HEAD / remote HEAD 一致、GitHub Pages 反映、public CSS、`docs/sw.js` service worker version、public DOM sentinel、番号付き要求 coverage を確認するまで `残タスクなし` と報告してはならない。未実施の gate は完了報告の `ToDo（今後の作業）` に residual work として残す。

今回の 2026-06-21 Podcast 検証障害のように、公開成果物は正常でも検証 API 側だけが 401 を返す場合は、成果物を未公開扱いにせず、別経路の公開確認へ fallback する。ただし fallback は無条件成功ではない。watch / playlist / public status のいずれかで同じ videoId、playlistId、title、日付を確認できる場合だけ Green とする。

## Incident Bugfix Horizontal Investigation Covenant

News-Grasp のバグ修正は、直接原因を 1 つの部品に閉じて扱ってはならない。原因が runner、repair、state、report のどこに見えていても、同じ incident 単位で runner / repair / state / report の横並び調査を必ず実施し、1 レーンでも未調査なら修正完了にしてはならない。

| Lane | Required investigation |
|---|---|
| runner | runner: 実行体、wrapper、stage 遷移、live copy、scheduler、NoPublish/RecoverOnly を調べ、実行 path と repo path の drift を分ける。 |
| repair | repair: coverage matrix、registry、handler 実装、same-gate re-verify を調べ、unknown / unimplemented / internal Red を Green に倒していないことを確認する。 |
| state | state: runner state、distribution manifest、gate attempts、publish-complete、recovery proof を調べ、同じ日付、同じ run intent、同じ HEAD を指すことを確認する。 |
| report | report: incident report、bug class、横並び類似候補、新規バグ候補、恒久対策を記録し、局所復旧だけで根因を閉じない。 |

過去障害と今後の障害は `tools.historical_failure_scenarios` の scenario 単位でこの 4 レーンを持つ。新しい incident、E2E 障害、runner 障害、repair 障害、公開確認障害を追加する場合は、該当 evidence と同時に 4 レーン横並び調査の summary を更新し、`tests/test_historical_failure_scenarios.py` で全 scenario に同じ契約がかかることを確認する。

## Category Schedule Source of Truth

曜日別の必須カテゴリは `tools.publish_inventory.scheduled_category_ids(issue)` を唯一の実装正本とする。runner、sub-agent、reporter、repair、gate、publish inventory、prompt、validator は、この関数が返したカテゴリだけを required として扱う。

| 曜日 | Required categories | Non-target categories |
|---|---|---|
| 月 | fx, ai, it, mobility, manufacturing, economy | game |
| 火 | fx, ai, it, mobility, manufacturing, economy, game | |
| 水 | fx, ai, it, mobility, manufacturing, economy | game |
| 木 | fx, ai, it, mobility, manufacturing, economy, game | |
| 金 | fx, ai, it, mobility, manufacturing, economy | game |
| 土 | fx, ai, it, mobility, game | manufacturing, economy |
| 日 | fx, ai, it, mobility, game | manufacturing, economy |

runner は 7 カテゴリ固定で sub-agent を起動してはならない。水曜日に Game を探索すること、土日に Manufacturing / Economy digest を required として探すこと、Game に限らず、任意の非対象カテゴリを repair / reporter / missing 判定へ流すことは禁止する。

非対象カテゴリ artifact が過去 run や手動復旧で残っていても、それを当日の required artifact へ昇格してはならない。逆に required category の artifact 欠落は失敗として検出する。公開済みの非対象カテゴリ artifact は存在してもよいが、当日必須カテゴリへ昇格しない。runner bug や repair bug の調査では、まず required / non-target の境界をこの表に戻して確認する。

Category schedule impact map:

| Impact area | Required reflection |
|---|---|
| Runner Stage0 / Stage2 reporter fan-out | `scheduled_category_ids(issue)` の結果だけを fan-out し、固定 7 カテゴリを作らない。 |
| Editor manifest / newsroom prompt | 当日必須カテゴリだけを統合対象にし、非対象カテゴリ不足を editorial defect にしない。 |
| publish inventory / repair scope | required artifact だけを missing / repair 対象にし、非対象カテゴリ artifact は通常完走でも failure でもない補助情報として扱う。 |
| generate_pages / public UI | 存在する artifact は公開できるが、当日 issue の required 判定には戻さない。 |
| validate_daily_quality / validate_generation_quality / reconcile | date から必須カテゴリを解決し、非対象カテゴリを required missing にしない。 |
| YouTube Podcast / publish_complete | required web/audio/deepdive の公開状態と Podcast/playlist 状態を確認し、非対象カテゴリ有無で完了判定を変えない。 |
| historical fallback evidence | 旧 fallback 証跡は通常完走ではなく、非対象カテゴリ探索失敗や required artifact 欠落の成功理由にしない。 |
| verify-publish-complete | public URL、publish-status、audio、Podcast の日付 sentinel を確認し、曜日別カテゴリ仕様と矛盾させない。 |

## Operational Premise Fidelity

復旧済みの公開成果物を、後続の goal、incident、E2E、または仕様整理の都合で未復旧扱いに巻き戻してはならない。現在状態の復旧タスクと、将来の完走判定 gate は分ける。

goal が打ち取れなかった理由、完走扱いになった理由、どの gate が公開未更新を止められなかったかは incident evidence に残す。ただし、復旧済みの公開成果物、公開済みの非対象カテゴリ artifact、または公開仕様上不要な artifact を後から required failure に変えてはならない。

pytest PASS は必要条件であり十分条件ではない。daily quality PASS は必要条件、public URL PASS は必要条件、runner/live SHA一致は必要条件である。効率的・完全完走を主張するための必要条件は、1時間以内の本番相当 push直前 E2E PASS、または同等の証跡で SLO と公開面が一致していることを示すことである。

SLO gate 実装を SLO 達成実測と混同してはならない。E2E 未実施なら効率的・完全・1時間以内完走とは報告してはならない。テスト Green、SLO gate 実装、または public URL 単発 200 は必要条件であって、単独では完全完走の十分証明ではない。

## Human Commitment

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | PLEASE IMPLEMENT THIS PLAN: |
| approved_goal_statement | News-Grasp最大重大障害 hardening + Plan Modeレビュー恒久対策 R7 を、Phase 0/A/B の範囲で実装する。 |
| approval_evidence_ref | current chat turn: user message `PLEASE IMPLEMENT THIS PLAN:` with R7 plan body |
| approved_at | 2026-06-26 |
| commitment_version | news-grasp-max-incident-hardening-r7 |
| commitment_scope | Phase 0 spec/provenance repair; Phase A review discipline; Phase B News-Grasp local hardening. Excludes live runner sync/full E2E/publish/push/public proof/rollback unless separately approved. |
| open_questions | None for Phase 0/A/B local implementation scope. Yellow public actions remain separately approval-gated. |

Codex はこの Human Commitment を自己判断で変更してはならない。repo-local pytest Green は実装証跡であり、人間承認ではない。full E2E 未実施時に 1時間以内の完全完走証明済み と報告してはならない。

## Summary Layer Lanes Commitment

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | PLEASE IMPLEMENT THIS PLAN: / 本修正は品質ゲートと完全に仕様をリンクすること。実装後に結合テストを実施しGreenの場合のみpushする。Yellow以下はGreenになるまで修正→テストすること。 / ここの「記者」「解説者」「予測者」は不要。すべてのテンプレから削除すること。品質ゲートも含めて合わせて修正せよ。ESSAY部分は中途半端に適用されているが、いっそのことカテゴリー別の様式と合わせたほうが良い。 / 別件ですが、スマホ版のページトップの見え方を右側の写真のようにして、上部の帯を圧縮してほしい。 |
| approved_goal_statement | News-Grasp 記事カード要約UIと ESSAY 要約部を、アイコンは保持したまま役割者名を出さない「事実・概要 / 背景・要点 / 影響・展望」の3層レーンへ統一する。記事要約エージェントの生成プロンプトも同じ3層に揃える。スマホ版トップ帯は日付メタを上段に寄せ、tagline / ISSUE label / TOKYO 行を畳んだ圧縮表示へ寄せる。PODCAST / ARCHIVE ボタンは YESTERDAY と重ならない昨日断面の小型ボタンに戻す。 |
| approval_evidence_ref | current chat turn: user messages `PLEASE IMPLEMENT THIS PLAN:` plus follow-up quality gate / integration test / push instruction, 2026-06-29 role-name removal / ESSAY alignment instruction, and mobile compact header screenshot instruction |
| approved_at | 2026-06-29 |
| commitment_version | summary-layer-lanes-2026-06-29 |
| commitment_scope | Article card summary UI in `page-template.html`, `category-template.html`, and `index-template.html`; ESSAY summary bullets in `summary-template.html`; reporter / routine / model-eval / Obsidian prompts that generate article bullets; mobile top brand band and home nav in `index-template.html` / `docs/assets/site.css`; category digest article bullet normalization excluding `digest/Summary`; local generation and integration verification. |
| open_questions | None for implementation when quality gates are Green. Commit/push is allowed only after Green verification and safe-commit gate. |

この改修は `Feature Change Quality Gate Matrix` の次の行に完全リンクする。

| Link item | Decision |
|---|---|
| Affected matrix rows | `Public UI / OGP / PWA / thumbnails`; `Summary / editorial reflection` |
| Gate update decision | 記事カード要約UIは `tests/test_summary_layer_lanes.py` で lane role / marker / spine / icon / card shell preservation と役割者名表示の不在を固定する。アイコンは DOM に存在するだけでは Green ではなく、`FACT / CONTEXT / OUTLOOK` の円形アバター背景、SVG、短ラベル、表示 marker `事実・概要 / 背景・要点 / 影響・展望` が3段すべて視認可能であること、`--summary-*` の未定義CSS変数がないこと、Claude Code 原本デザインの left avatar column / body column / spine 構造を壊さないことを同 test と Chrome 操作系スキルの実画面証跡で確認する。ESSAY 側は `tests/test_summary_pattern_d.py` で `summary-template.html` が同じ3層レーン部品を使い、アイコンは保持しつつ旧 `summary-sec__bullets` と旧役割者ラベルに退行しないことを固定する。Reporter 生成段階は `tests/test_newsroom_prompts.py` で `【事実・概要】：` / `【背景・要点】：` / `【影響・展望】：` を prompt 正本へ固定し、旧 `【事実】：` / `【背景】：` / `【展望】：` に戻さない。スマホ版トップ帯は `tests/test_home_variant_b.py::test_home_brand_mobile_uses_compact_issue_header` で日付メタ上段化、tagline / ISSUE label / TOKYO 行の非表示、Issue 番号の下段配置を固定し、`tests/test_home_variant_b.py::test_home_nav_mobile_uses_compact_yesterday_snapshot_for_actions` で PODCAST / ARCHIVE が YESTERDAY に被らない昨日断面の小型ボタンを固定する。過去記事要約3層リライトは `tests/test_rewrite_bullets_3layer.py` で3 bullet、URL、数値、固有名詞、`[[...]]` / `**...**` / `__...__` の保持を固定する。 |
| Verification command | `.venv\Scripts\python.exe -m pytest tests/test_summary_layer_lanes.py tests/test_summary_pattern_d.py tests/test_home_variant_b.py tests/test_rewrite_bullets_3layer.py tests/test_newsroom_prompts.py tests/test_card_summary_strip_markdown.py tests/test_generate_pages.py tests/test_product_spec_contract.py -q`; `.venv\Scripts\python.exe tools/generate_pages.py --full`; `designmd lint .\DESIGN.md` |
| Integration gate | 結合テスト Green の場合のみ commit/push する。Yellow 以下は修正と再テストを継続し、push しない。 |
| Public boundary | push 後の公開 URL / GitHub Pages / remote HEAD 確認は push を実行した場合だけ行う。 |

## User Answer Provenance

| Date | Source | Exact user text |
|---|---|---|
| 2026-06-26 | Current chat planning intent | ChatGPTレビューに通すための最低限の基準であるインプットは完全に用意してからレビューに渡す |
| 2026-06-26 | Current chat planning intent | その上で過去レビューで指摘された内容を字面だけでなく根本的に全体最適を考えた上で修正してからレビューに渡す |
| 2026-06-26 | Current chat implementation approval | PLEASE IMPLEMENT THIS PLAN: |
| 2026-06-28 | Current chat implementation approval | PLEASE IMPLEMENT THIS PLAN: |
| 2026-06-28 | Current chat quality gate instruction | 本修正は品質ゲートと完全に仕様をリンクすること。実装後に結合テストを実施しGreenの場合のみpushする。Yellow以下はGreenになるまで修正→テストすること。 |

## Sustainable Complete Repair

外部システム要因以外で公開面が揃わない停止は許容しない。fallback は通常日次完走ではない。通常日次バッチ経路の fallback publish は完全禁止とし、fallback_ok や published_fallback_with_notice を OK marker、terminal success、Podcast、DeepDive、distribution、notification の完了証跡として扱ってはならない。旧 fallback 証跡を読む場合は、歴史データまたは手動緊急公開の痕跡として扱い、通常完走に昇格しない。

handler 未実装は Red とする。coverage matrix に未掲載の failure は blocked_unknown_repair_class として止め、prose hint だけで repairable に倒してはならない。handler_unimplemented_red は最終 Green 条件では 0 件でなければならない。

repair completeness = coverage matrix + zero unimplemented + fixture repair + runner single path。existing artifact repair では LLM worker を起動しない。既存 artifact がある場合は deterministic handler または typed not-applicable / blocked status で扱い、対象 artifact が全 missing かつ typed reason がある場合だけ missing artifact generation を許可する。

live runner 上書きは backup + 明示承認 + rollback を満たす場合だけ許可する。repo runner と live runner の SHA 一致は必要条件であり、runner 実行・公開検証・Podcast 検証の代替にはならない。

## Repair Decision Debt Covenant

repair の根本対策は、repair の回数を増やすことではなく、validator / coverage matrix / orchestrator / registry / runner が何を決める責務を持つかを上流で固定することである。新しい repair failure を下流 test や smoke で塞ぐ前に、どの層が source of truth を読み、どの層が routing を決め、どの層が artifact scope を縮約し、どの層が terminal state を出すかを定義する。

| Layer | Decision responsibility |
|---|---|
| Validator | `issue_code`、対象 artifact、日付、category、evidence を構造化 issue として出す。prose だけの failure は legacy 補助であり、通常完走の完全性証跡ではない。 |
| Coverage matrix | `issue_code` から repair class、handler、allowed scope、failure status を一意に決める。未掲載は `blocked_unknown_repair_class`。 |
| Orchestrator | 複数 issue を ordered repair ledger として扱い、最初の issue だけで複合障害を代表させない。 |
| Registry | handler の存在、入力 scope、handler not-applicable、出力 scope を別 status で返す。 |
| Runner | selected issue artifacts だけを handler に渡し、typed status を `handler_unimplemented` や generic error へ丸めない。 |

決定債務 status は次を正本とする。

| Status | Meaning |
|---|---|
| `repair_context_overbroad` | gate が対象外 artifact も渡したが、in-scope artifact があり runner/registry が縮約して続行できた。 |
| `repair_context_scope_mismatch` | 選択された handler に渡せる artifact が 1 件もない。classifier / validator / matrix の接続バグとして Red。 |
| `blocked_repair_handler_unimplemented` | handler_id が registry に存在しない場合だけ。scope mismatch や handler 失敗をこの status に丸めない。 |
| `blocked_deterministic_repair_not_applicable` | handler は存在するが現 artifact を修復できず、別 issue へ継続できない。 |
| `repair_handler_output_scope_violation` | handler が許可 scope 外 artifact を返す、または変更しようとした。hard block。 |
| `blocked_unknown_repair_class` | coverage matrix 未掲載または未知 issue。推測 repair しない。 |

## Repair Decision Debt Commitment

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | 横並び調査の上で決定債務のあるべきを定義せよ / fallback を禁止しているにもかかわらずバッチが一度も完走しない状態は spec.md 違反 / 下流でテストやチェック対応検討する前に、必ず上流工程からそもそもバグが発生しないよう整理する / fallback_policy=完全禁止 / repair_scope=News-Grasp全repair |
| approved_goal_statement | News-Grasp 全 repair の決定責務を定義し、通常日次 fallback 完全禁止、上流工程優先、2週間未完走違反の再発防止を spec / harness / repair / runner / tests に固定する。 |
| approval_evidence_ref | current chat 2026-06-29 latest user request and explicit implementation approval `PLEASE IMPLEMENT THIS PLAN:` |
| approved_at | 2026-06-29 |
| commitment_version | repair-decision-debt-2026-06-29 |
| commitment_scope | News-Grasp local spec/provenance, repair coverage matrix, registry, orchestrator, runner, watcher, self-heal/publish/push status semantics, local AGENTS/CLAUDE, News-Grasp repair/e2e skills, local tests. Excludes push, live runner overwrite, full production E2E, public publish, rollback, and ProjectFolders-wide implementation unless separately approved. |
| open_questions | None for local implementation. Public actions remain separately approval-gated. |

## Acceptance Scenarios

| Scenario | Given | When | Then |
|---|---|---|---|
| Normal daily run | 外部依存が利用可能で、記事候補に修復可能な品質問題がある | 日次バッチが実行される | repair / quarantine+refill / reporter retry / re-verify により品質を整え、Definition of Done を満たして OK marker を書く。 |
| External failure | YouTube quota や GitHub outage など外部依存が失敗している | publish / podcast verification が実行される | 壊れた公開を進めず、typed fatal と evidence を残して止まる。 |
| Content shortfall | 不良 URL 隔離後に記事数が不足する | reserve 補充が可能である | カテゴリを再生成し、重複 URL と search audit を同期して re-verify する。 |
| Governance review | 完了条件や配信経路に触る改修が提案される | 実装計画を作る | この憲法との差分、前後工程、検証方法、未達時の terminal status を明記する。 |

## References

- Team charter / project charter の考え方: 目的、価値、成功条件、運用ルールを共有する。
- Architecture Decision Record の考え方: 重要判断は context、decision、consequence を短く残す。
- SRE monitoring の考え方: 長時間処理は latency、errors、saturation、progress を観測可能にする。
