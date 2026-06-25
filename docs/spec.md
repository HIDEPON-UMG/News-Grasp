# Product Spec: News-Grasp

> **Status**: Constitution
> **Last Updated**: 2026-06-25
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

## Sustainable Complete Repair

外部システム要因以外で公開面が揃わない停止は許容しない。外部 API、認証、quota、GitHub Pages、YouTube、ネットワークなどの外部依存を除き、公開面が揃わない内部欠陥は停止理由ではなく repair handler 未実装として扱う。

fallback は通常日次完走ではない。fallback は読者保護のための一時的な公開面保護であり、Definition of Done、OK marker、publish_complete、または 1時間以内の完全完走証明へ昇格してはならない。

handler 未実装は Red とする。Summary emphasis、category card emphasis、audio script length、published docs presence、URL quarantine/refill など既知の内部欠陥は、既存 artifact を局所 repair して同じ gate を再実行する deterministic handler を持つ。該当 handler が無い場合は `blocked_repair_handler_unimplemented` として失敗させ、fallback や LLM worker のゼロベース再作成へ逃がさない。

repair completeness = coverage matrix + zero unimplemented + fixture repair + runner single path と定義する。coverage matrix は `tools/repair_coverage_matrix.py` を唯一の分類正本とし、validator / gate が返す `gate_id` と `issue_code` は coverage matrix の row に存在しなければならない。coverage matrix に未掲載の failure は blocked_unknown_repair_class として扱い、推測や prose hint で repairable に倒してはならない。

handler_unimplemented_red は最終 Green 条件では 0 件でなければならない。既知 failure row は deterministic handler、LLM missing artifact 生成、typed external、typed fatal のいずれかに分類し、内部欠陥を `handler_unimplemented_red` のまま本番導入しない。安全に局所 repair できない既存 artifact は `blocked_ambiguous_repair` として止め、LLM worker や広域再作成へ逃がさない。

runner の repair path は registry single path に集約する。`tools.auto_repair_orchestrator` は gate failure 1 回につき classifier JSON を 1 回だけ生成し、runner はその decision を `tools.repair_registry` と LLM preflight の両方へ渡す。existing artifact repair では LLM worker を起動しない。LLM worker は coverage matrix が `llm_generate_missing_artifact` を返し、対象 artifact が全 missing で、typed reason がある場合だけ起動できる。

SLO/progress evidence は runner が実データとして出す。`runner-progress` record は `required_units`、`completed_units`、`required_categories`、必要時 `repair_signature` と `artifact_progress` を持つ。`tools.validate_batch_slo` は 40 分で 50% 未満、非対象カテゴリ作業、同一 repair signature 反復、artifact progress なしを publish 前に失敗として扱う。

live runner 上書きは backup + 明示承認 + rollback を必須とする。repo 側の runner と tests が Green でも、`C:\Users\hidek\bin\news-grasp-runner.ps1` など live runner への反映は、反映前 backup、明示承認、反映後 hash/smoke、rollback 手順なしに実行してはならない。

## Human Commitment

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_goal_statement | 繁忙なITコンサルタントが、膨大なニュースを個別確認せず、実務判断に必要な重要論点・背景・示唆を短時間で把握できる完全自走型ニュース体験を、テキスト・音声・動画を用い、都度最適な手段で提供する。 |
| approved_by_user_text | 以下対応を実施の上で承認する。 |
| approval_evidence_ref | Current conversation, user answer on 2026-06-25. |
| approved_at | 2026-06-25 |
| commitment_version | 1 |
| commitment_scope | Product Constitution, Goal Quality Contract, Definition of Done, Sustainable Complete Repair, Change Governance, Feature/Test Traceability, SDD/TDD Quality Contract. |
| open_questions | None. |

完全自走 repair の実装証跡は、coverage matrix、registry handler、runner single path、SLO/progress gate、fixture repair、repo-local pytest で示す。ただし、Codex はこの Human Commitment を自己判断で変更してはならない。承認状態を変更できるのは、ユーザーが live runner 反映、full E2E、publish、push、public proof の実行範囲と判定結果を確認し、明示的に承認した場合だけである。

repo-local pytest Green は実装証跡であり、人間承認ではない。repo-local 検証が Green でも、live runner 同期、full E2E、publish、push、public URL / Podcast / playlist proof が未実行なら、それらは Yellow として扱う。

full E2E 未実施時に 1時間以内の完全完走証明済み と報告してはならない。SLO/progress gate の実装は SLO 達成実測ではなく、runner が 40分50%未満、非対象カテゴリ作業、同一 repair signature 反復、artifact progress なしを publish 前に止められることの repo-local 証跡である。

## User Answer Provenance

- source_status=UserConfirmed
- user_answer_text: News-Grasp の core_goal は「繁忙なITコンサルタントが、膨大なニュースを個別確認せず、実務判断に必要な重要論点・背景・示唆を短時間で把握できる完全自走型ニュース体験を、テキスト・音声・動画を用い、都度最適な手段で提供する」。success_condition は通常日次バッチで Web / Audio / YouTube Podcast / playlist / notification / runner state / OK marker が同じ日付・同じ run intent で揃うこと。non_goal_boundary は一般ニュースサイト化しない、外部依存を無理に突破しない、fallback を完全完走証明にしないこと。learning_loop は incident / repair coverage / publish verification / runner state / public proof のズレを継続的に潰すこと。Human Commitment は以下対応を実施の上で承認する。

## Goal Quality Contract

| Field | Value |
|---|---|
| core_goal | 繁忙なITコンサルタントが、膨大なニュースを個別確認せず、実務判断に必要な重要論点・背景・示唆を短時間で把握できる完全自走型ニュース体験を、テキスト・音声・動画を用い、都度最適な手段で提供する。 |
| target_user_or_operator | 繁忙なITコンサルタント、および News-Grasp の日次運用・復旧・公開確認を担う運用者。 |
| user_state_change | 読者は大量ニュースを個別確認しなくても、業務判断に必要な重要論点、背景、影響、次に見るべき観点を短時間で把握できる。運用者は、公開面の欠落や runner state のズレを検知・修復・証跡化できる。 |
| business_or_operational_value | 継続的な情報収集、編集、公開、検証、repair を自走させ、ニュース提供の運用品質と復旧可能性を高める。 |
| success_condition | 通常日次バッチで Web / Audio / YouTube Podcast / playlist / notification / runner state / OK marker が同じ日付・同じ run intent で揃う。 |
| non_goal_boundary | 一般ニュースサイト化しない。外部依存を無理に突破しない。fallback を完全完走証明にしない。 |
| learning_loop | incident / repair coverage / publish verification / runner state / public proof のズレを継続的に潰す。 |

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

## SDD / TDD Quality Contract

- SDD lifecycle: Spec -> Spec Questions -> Acceptance Matrix -> Plan -> Review -> Implementation -> Evidence -> Drift review
- TDD lifecycle: Red -> Green -> Refactor -> Evidence -> Completion
- 非自明な変更は、少なくとも 1 つの `Spec Item` を `User/Operator Outcome`、`Concrete Acceptance Example`、`Red Signal`、`Green Verification`、`Evidence Plan` に対応させる。
- ChatGPT review は plan gate であり、実装完了証跡ではない。News-Grasp の完了証跡は repo-local tests、publish verification、Podcast / playlist proof、runner state、incident evidence、public URL sentinel で示す。

## Feature Change Quality Gate Matrix

機能を追加、削除、修正する場合は、実装だけでなく同じ変更単位で品質 gate、契約テスト、公開検証、runner state、完了報告のどれを更新するかを先に決める。機能の成果物が Definition of Done のいずれかへ届くなら、その成果物を作る工程だけでなく、前工程の入力契約、当該工程の失敗分類、後工程の公開確認までを 1 セットで扱う。

次の表を変更計画の最低チェックリストとする。該当する行があるのに gate 更新が不要な場合は、不要理由を計画または incident evidence に残す。

| Spec Item | User/Operator Outcome | Concrete Acceptance Example | Red Signal | Green Verification | Evidence Plan |
|---|---|---|---|---|---|
| Daily complete public experience | 読者が同じ日付・同じ run intent の Web / Audio / Podcast / playlist / notification を欠落なく利用できる | Given 外部依存が利用可能, When 通常日次バッチが完走する, Then Definition of Done の公開面が verified になり OK marker が書かれる | OK marker が公開面 verified より先に書ける、または runner state と publish-status が別日付になる | `verify-publish-complete`、`verify-publish`、Podcast / playlist verification、public URL sentinel | コマンド出力、publish-status、runner state、public URL / YouTube watch / playlist proof |
| Repair-first operation | 修復可能な内部欠陥は停止で済ませず deterministic repair で完走または typed fatal に分類される | Given 既知の品質問題が発生, When repair classifier が動く, Then coverage matrix 上の handler または typed fatal に分類される | `handler_unimplemented_red`、`blocked_unknown_repair_class`、fallback を完全完走証明にする | `tools/repair_coverage_matrix.py`、repair registry tests、runner convergence tests | coverage matrix、pytest output、incident evidence |
| Editorial quality for IT consultants | 読者が重要論点、背景、示唆、次の確認観点を短時間で把握できる | Given digest / Summary / DeepDive が生成される, When quality gate が実行される, Then ITコンサルタント向けの relevance / insight / source health を満たす | source URL 不健全、Summary が当日論点を統合しない、DeepDive 導線欠落 | `tools.validate_daily_quality --require-deepdive`、summary reflection tests、URL liveness tests | validator output、generated docs inventory、public sentinel |
| Incident and drift learning | 障害や仕様ズレが incident / repair coverage / gate に還元される | Given publish verification や runner state にズレが出る, When 復旧または仕様変更を行う, Then incident evidence と matrix / tests の更新要否が記録される | 復旧済み公開成果物を未復旧扱いに戻す、または gate 不足を記録しない | incident report validator、product spec contract、harness / report tests | incident HTML、contract test output、drift review notes |

| Change area | Update with the feature change | Minimum gate / test |
|---|---|---|
| Source collection / URL freshness / dedup | watchlist、検索 query、URL 正規化、公開日 freshness、重複 / follow-up 判定、`data/search_audit` を更新する。 | URL liveness / freshness / dedup 契約テスト、`tests/test_all_article_urls_live.py`、`tests/test_dedup_freshness.py`、`tests/test_dedup_followup_gate.py`。 |
| Article data / schema / tags | `data/articles.jsonl` schema、frontmatter、Obsidian tags、entities / topics / industries / events を更新する。 | `tools.validate_record`、`tests/test_validate_record.py`、tag / session URL / article append 系契約テスト。 |
| Digest / category schedule | 対象カテゴリ、休載条件、記事数不足時の refill / quarantine、`data/search_audit` 契約を更新する。 | `tools.validate_daily_quality --date <date> --require-deepdive`、カテゴリ presence / search audit 契約テスト。 |
| Summary / editorial reflection | Summary 構造、reflection、hero、key takeaways、日付 docs への反映を更新する。 | summary reflection 系テスト、`validate_daily_quality`、公開日付 docs sentinel。 |
| DeepDive | md、HTML、関係図、日付ページからの導線、公開 inventory を更新する。 | `--require-deepdive`、DeepDive presence / relation layout テスト、公開 URL sentinel。 |
| Public UI / OGP / PWA / thumbnails | template、CSS、OGP meta、thumbnail contract、manifest、service worker cache、offline page を更新する。 | `tests/test_pwa_meta.py`、`tests/test_thumb_contract.py`、`tests/test_fetch_ogp.py`、必要時 Playwright / visual smoke と `docs/sw.js` version bump。 |
| Web publish surface | `docs/<date>/index.html`、summary、per-category docs、public status、GitHub Pages 反映を更新する。 | `verify-publish`、published docs presence、public URL 200 / sentinel、remote HEAD / Deploy workflow success / workflow Pages status built。 |
| Audio / TTS | 音声生成、release URL、ページ埋め込み、再生可能性、TTS required gate を更新する。 | TTS publish gate、audio URL presence、`verify-publish` audio check。 |
| YouTube Podcast / playlist | upload state、public video、playlist 反映、外部検証 fallback、token / quota / permission の typed status を更新する。 | `verify-podcast`、`verify-publish --require-podcast`、外部 API 401/403/404 fallback 契約テスト。 |
| Notification | 送信条件、通知不要条件、失敗時 typed status、再送可否を更新する。 | notification dry-run / typed status テスト、送信不要時の完了条件テスト。 |
| Runner / state / recovery | full run / RecoverOnly / fallback publish / OK marker / distribution state の遷移を更新する。 | runner convergence / state watcher 契約テスト、full と RecoverOnly の両経路 dry-run。 |
| Incident / reporting | 障害 evidence、公開 inventory、報告 HTML、完了報告の必須項目を更新する。 | incident report validator、公開 inventory 確認、`tests/test_product_spec_contract.py`。 |
| External integration / auth | OAuth、API quota、権限、token expiry、公開反映遅延の failure domain を typed status に分ける。 | auth/quota/permission の fixture、retry しない fatal と fallback 可能な verify failure の分類テスト。 |

非自明な変更計画と完了報告には、必ず「Affected matrix rows」「Gate update decision」「Verification command」を書く。該当する row が無い機能を追加、削除、修正する場合は、実装と同じ変更単位でこの `Feature Change Quality Gate Matrix` と `tests/test_product_spec_contract.py` を更新してから完了扱いにする。

今回の 2026-06-21 Podcast 検証障害のように、公開成果物は正常でも検証 API 側だけが 401 を返す場合は、成果物を未公開扱いにせず、別経路の公開確認へ fallback する。ただし fallback は無条件成功ではない。watch / playlist / public status のいずれかで同じ videoId、playlistId、title、日付を確認できる場合だけ Green とする。

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
| publish inventory / repair scope | required artifact だけを missing / repair 対象にし、非対象カテゴリ artifact は fallback_ok でも failure でもない補助情報として扱う。 |
| generate_pages / public UI | 存在する artifact は公開できるが、当日 issue の required 判定には戻さない。 |
| validate_daily_quality / validate_generation_quality / reconcile | date から必須カテゴリを解決し、非対象カテゴリを required missing にしない。 |
| YouTube Podcast / publish_complete | required web/audio/deepdive の公開状態と Podcast/playlist 状態を確認し、非対象カテゴリ有無で完了判定を変えない。 |
| fallback_ok | fallback が許されるのは公開品質保護のためであり、非対象カテゴリ探索失敗を fallback 理由にしない。 |
| verify-publish-complete | public URL、publish-status、audio、Podcast の日付 sentinel を確認し、曜日別カテゴリ仕様と矛盾させない。 |

## Operational Premise Fidelity

復旧済みの公開成果物を、後続の goal、incident、E2E、または仕様整理の都合で未復旧扱いに巻き戻してはならない。現在状態の復旧タスクと、将来の完走判定 gate は分ける。

goal が打ち取れなかった理由、完走扱いになった理由、どの gate が公開未更新を止められなかったかは incident evidence に残す。ただし、復旧済みの公開成果物、公開済みの非対象カテゴリ artifact、または公開仕様上不要な artifact を後から required failure に変えてはならない。

pytest PASS は必要条件であり十分条件ではない。daily quality PASS は必要条件、public URL PASS は必要条件、runner/live SHA一致は必要条件である。効率的・完全完走を主張するための必要条件は、1時間以内の本番相当 push直前 E2E PASS、または同等の証跡で SLO と公開面が一致していることを示すことである。

SLO gate 実装を SLO 達成実測と混同してはならない。E2E 未実施なら効率的・完全・1時間以内完走とは報告してはならない。テスト Green、SLO gate 実装、または public URL 単発 200 は必要条件であって、単独では完全完走の十分証明ではない。

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
