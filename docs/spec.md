# Product Spec: News-Grasp

> **Status**: Constitution
> **Last Updated**: 2026-07-27
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

Scheduled Task / runner / bootstrap / deadman はTask Actionから最深childまでno-consoleで動作する。Taskは`pythonw.exe`とversioned launcherを使い、launcher childは`CREATE_NO_WINDOW`を強制する。このサブ端末では既存Disabled状態を維持し、no-console移行を理由に自動enableしない。

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

### News-Grasp 通常公開 inventory 必須

`news-grasp-publish-inventory-required`: News-Grasp の通常公開・本日分公開・途中再開を完了報告する場合、7カテゴリ digest、Summary、DeepDive md、DeepDive HTML、日付 docs、`docs/publish-status.json` の `published_ok`、公開 URL sentinel、`validate_daily_quality --require-deepdive` の証跡を必ず列挙する。公開に必要なコンテンツが 1 つでも欠ける場合は、正当な欠落理由と検証 gate を明記し、完了と言わない。

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

### Weekly Failure Source-of-Truth and Runner Terminal Semantics

週次・日次 failure の分類では、validator が生成した structured issue_code を唯一の分類正本とする。structured unknown を message prose から再分類してはならない。matrix は issue artifact と handler scope を実行前に照合し、registry は handler existence、scope mismatch、not-applicable、output scope violation を別 status として返す。

runner は固定 attempt 回数を terminal predicate にしてはならない。終了判定は deadline と typed repair ledger に基づき、同一 issue の ordered ledger、選択 artifact、handler status、same-gate reverify を残す。GitHub Release upload の HTTP 502 / 503 や Codex quota、OAuth readiness などの外部境界は blocked_external_readiness として content defect から分離する。

`verify-live-runner-readiness` は「次回 06:00 に起動できるか」を `next_run_readiness`、「直近 06:00 が成功したか」を `last_scheduled_attempt` として別々に返す。`verify-publish-complete` と `verify_public_surface` は `public_status`、`scheduled_attempt_status`、`recovery_attempt_status` を別フィールドで保持し、recovery 後の public Green で scheduled failure を成功へ書き換えない。週次分類では scheduled failure 後の公開完了を `recovered_after_failed_schedule` とし、`complete` と呼ばない。

distribution manifest は publish 前に作るため `publish_commit` が空でもよいが、その場合は `publish_commit_resolution=post_push_verify` と `same_publish_contract=pre_publish_commit_must_equal_verified_publish_commit` を必須とする。post-push verifier は `pre_publish_commit` が verified local/remote HEAD の ancestor であることを確認し、同じ `same_publish` proof に resolution と contract を保存する。空欄だけの manifest は `distribution_manifest_publish_commit_resolution_missing` で拒否する。

runner の start marker は `run_id` を同一行に含める。旧ログの run_id 欠落は `legacy_missing` と明示し、行範囲による代替 identity を使う。visible な `docs/incidents/2026-*-report.html` が historical corpus 未登録なら、監査は該当パスと suggested scenario stub を列挙し、pytest-static より後へ進めない。新規 incident report の既定置場が `build/incidents/` である契約は変更しない。

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

| Change area | Quality gate / predicate | Up/downstream artifacts | Required verification |
|---|---|---|---|
| Source collection / URL freshness / dedup | URL liveness は `tools.audit_all_article_urls.blocking_url_dates(issue_date)` が返す TODAY / YESTERDAY の2日間だけを daily blocking とする。2日以上前の過去URL不良は warning / inventory / repair candidate として扱い、日次公開 blocking 条件にしない。watchlist、検索 query、URL 正規化、公開日 freshness、重複 / follow-up 判定、`data/search_audit` を更新する。 | `data/articles.jsonl`、`data/search_audit/`、reporter/session URL、digest の記事リンク。 | `tests/test_all_article_urls_live.py`、URL liveness / freshness / dedup 契約テスト、`tests/test_dedup_freshness.py`、`tests/test_dedup_followup_gate.py`。 |
| Article data / schema / tags | record schema、frontmatter、Obsidian tags、entities / topics / industries / events の意味論を維持する。digest/current reporter/current articles の URL 差分は `digest_articles_digest_only` と `digest_articles_articles_only` に分け、前者は `digest-articles-digest-only-patch`、後者は `digest-card-insert-patch` へ route する。record thumb は `thumb_missing` と `thumb_invalid` を分け、record scope 内の `record-thumb-quarantine-patch` で同じ schema gate を通す。方向不明の legacy `thumb_invalid_or_missing` は `blocked_thumb_direction_unspecified` とする。 | `data/articles.jsonl`、current reporter records、digest frontmatter、tag / session URL / article append 成果物。 | `tools.validate_record`、`tests/test_validate_record.py`、`tests/test_digest_articles_reconcile.py`、`tests/test_repair_registry.py`、`tests/test_repair_matrix_validator_sync.py`、tag / session URL / article append 系契約テスト。 |
| Digest / category schedule | `tools.publish_inventory.scheduled_category_ids(issue)` を唯一の必須カテゴリ正本とし、対象カテゴリ、休載条件、記事数不足時の refill / quarantine、`data/search_audit` 契約を更新する。`articles_only` は reporter/current articles record の title/title_ja/source/published/thumb/summary/url/score/tag を使って既存 ordering policy へ card を挿入する。`digest_only` は current reporter evidence がある append 漏れと、authoritative manifest から旧 run 残存を証明できる除去だけを自動化し、曖昧なら `blocked_digest_only_ambiguous` とする。daily-quality は thumb の欠落/不正、source URL 未解決、search audit の coverage/queries/dropped evidence/欠落/破損/収集不足、TTS の台本/公開 state/HTML 反映を別 issue_code にし、実能力のない handler へまとめない。 | `digest/daily/`、category digest、current reporter records、`data/articles.jsonl`、日付 docs、カテゴリ docs、search audit、`build/tts/latest_audio.json`。 | `tools.validate_daily_quality --date <date> --docs-root docs --require-deepdive`、`tools.validate_digest_articles_reconcile --issue-date <date>`、`tools.publish_inventory --date <date> --kind categories --json`、全 daily-quality issue_code の AST matrix coverage、direction-specific matrix / registry / reconcile 契約テスト。 |
| Summary / editorial reflection | Summary 構造、reflection、hero、key takeaways、日付 docs への反映を daily-quality issue code と整合させる。 | Summary digest、home/date LP、summary page、記事カード要約。 | summary reflection 系テスト、`validate_daily_quality`、公開日付 docs sentinel。 |
| DeepDive | md、HTML、関係図、日付ページからの導線、公開 inventory、TTS audio refs を更新する。URL gate は要求生成不能・通信不能・証明書検証不能を生存へ読み替えず、Python CA失敗時だけOS TLSで再証明する。本番 runner / RecoverOnly は親環境の `NEWS_GRASP_SKIP_URL_CHECK` を継承しない。DeepDive対談は `current_signal`、`evidence`、`causal_chain`、`counterevidence_or_limit`、`change_over_time`、`decision_implication`、`next_action` の7価値を順に各1区間だけ持ち、各区間をrepo内に実在する記事根拠文へ `source:n` で1対1に結ぶ。全台本横断の完全反復率10%以下・最大3-gram類似度0.45以下を必須とし、字数だけの充足、旧定型句、根拠ラベルだけの見せかけ充足をfatalとする。 | `digest/DeepDive/`、`docs/deepdive/`、DeepDive dialogue、DeepDive audio、日付 docs link。 | `tools.validate_deepdive_urls digest/DeepDive/<date>-DeepDive.md`、`tools.tts.deepdive_dialogue <dialogue.md> --validate-only`、`tools.validate_daily_quality --date <date> --docs-root docs --require-deepdive`、`tests/test_deepdive_urls_live.py`、`tests/test_deepdive_dialogue_value_contract.py`、runner convergence、公開 URL sentinel。 |
| Public UI / OGP / PWA / thumbnails | template、CSS、OGP meta、thumbnail contract、manifest、service worker cache、offline page を更新する。公開 CSS / template / generated HTML を変える場合は `docs/sw.js` version bump を同じ変更単位に含める。 | `prompts/*template.html`、`docs/assets/site.css`、generated docs、`docs/sw.js`、thumbnail assets。 | `tests/test_pwa_meta.py`、`tests/test_thumb_contract.py`、`tests/test_fetch_ogp.py`、必要時 Chrome操作系スキルでの visual smoke と `docs/sw.js` version bump。 |
| Web publish surface | `docs/<date>/index.html`、summary、per-category docs、public status、GitHub Pages 反映を更新する。 | generated docs、`docs/publish-status.json`、public URL、GitHub Pages workflow。 | `verify-publish`、published docs presence、public URL 200 / sentinel、remote HEAD / Deploy workflow success / workflow Pages status built。 |
| Audio / TTS | 音声生成、release URL、ページ埋め込み、再生可能性、TTS required gate を更新する。当日検証は `build/tts/latest_audio.json` とhome/summaryの一致を要求する。過去日監査は現在日のlatest/homeを過去成果物へ誤適用せず、対象日のSummary HTMLに対象日mp3とaudio要素が残ることを日付固定証拠として検証する。 | public audio、Release asset、HTML audio refs、distribution manifest。 | TTS publish gate、audio URL presence、`tests/test_tts_required_publish_gate.py`、`verify-publish` audio check。 |
| YouTube Podcast / playlist | upload state、public video、playlist 反映、Daily Podcast と DeepDive Podcast の playlist 境界、同日重複禁止、Deleted video item 禁止、外部検証 fallback、token / quota / permission の typed status を更新する。 | YouTube video、playlist item、distribution manifest、Podcast metadata。 | `verify-podcast`、`tools.youtube_podcast.upload_episode <date> --audit-playlists`、`verify-publish --require-podcast`、外部 API 401/403/404 fallback 契約テスト、runner convergence 契約テスト。 |
| Notification | 送信条件、通知不要条件、失敗時 typed status、再送可否を更新する。 | notification payload、distribution state、runner state。 | notification dry-run / typed status テスト、送信不要時の完了条件テスト。 |
| Runner / state / recovery | full run / RecoverOnly / fallback publish / OK marker / distribution state / live ops readiness の遷移を更新する。repair runner は structured issue ledger の direction / evidence / selected artifacts を保持し、registry の `noop` / `not_applicable` を repair 成功として扱わない。matrix が所有する `verify_gate` / `allowed_artifacts` を registry metadata で上書きせず、registry 側の広い実装 scope は診断 metadata として分離する。`blocked_articles_only_record_incomplete` と `blocked_digest_only_ambiguous` を generic failure に丸めず、同じ gate の再検証 Green まで次 stage へ進まない。通常 publish_complete は repo/live runner SHA 一致、repo/live watcher SHA 一致、repo/live bootstrap SHA 一致、Runner task の 06:00 trigger / NextRunTime / NumberOfMissedRuns=0、Runner Action が `-SmokeTest` / `-Status` / `-StartOnly` 等ではない本番起動 mode、Scheduled Task が watcher/bootstrap を指すこと、watcher/bootstrap 経由の実起動 canary `smoke_ok` を含む `live_runner_readiness` 証跡なしに成立しない。既存 Runner task を権限上変更できない環境では、Runner task が live runner 直叩きでも、runner 本体が本番生成前の `Assert-PreRunBootstrapInterlock` を持ち、Runner より前の 05:55 Bootstrap task が有効、NextRunTime が 05:55、NumberOfMissedRuns=0、LastTaskResult=0、Action が `-SmokeTest` / short timeout / isolated state/log を明示し、repo/live bootstrap から watcher smoke を isolated state/log で実行済みであること、かつ fresh marker 後に repo/live runner drift を検出した場合は bootstrap self-repair 後に同期済み runner へ待機付き re-exec して本番生成へ入ることを同等条件とする。pytest-static は `NEWS_GRASP_SKIP_URL_CHECK=1` と `-m "not network"` を維持し、外部 URL liveness を混ぜない。 | runner ps1、watcher ps1、bootstrap ps1、state JSON、logs、gate attempts、structured repair ledger、selected artifacts、matrix scope、registry diagnostic metadata、distribution state、live runner、live watcher、live bootstrap、Task Scheduler、`build/live-runner-canary/`、`build/bootstrap-task-smoke/`。 | runner convergence / state watcher / direction ledger / matrix ownership 契約テスト、repair runtime dry-run、`tools.daily_self_heal verify-live-runner-readiness`、watcher/bootstrap smoke、full と RecoverOnly の両経路 dry-run。 |
| Incident / reporting / recovery evidence | 障害 evidence、公開 inventory、完了報告の必須項目を更新する。新規 `docs/incidents/*-report.html` は追跡・公開しない。HTML 証跡が必要な場合は untracked の `build/incidents/` を既定置場にし、公開が必要な場合は別途明示承認を要する。direction/handler capability drift は historical failure scenario と weekly regression case の双方へ登録する。 | `.gitignore`、`AGENTS.md`、`CLAUDE.md`、`build/incidents/`、historical failure scenario evidence、weekly failure regression corpus。 | `tests/test_incident_report_tracking_policy.py`、`tests/test_historical_failure_scenarios.py`、`tests/test_product_spec_contract.py`、公開 inventory 確認。 |
| External integration / auth | OAuth、API quota、権限、token expiry、公開反映遅延の failure domain を typed status に分ける。 | token / auth state、external API response、runner typed status。 | auth/quota/permission の fixture、retry しない fatal と fallback 可能な verify failure の分類テスト。 |

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
| YouTube Podcast / publish_complete | required web/audio/deepdive の公開状態、Podcast/playlist 状態、repo/live runner SHA、repo/live watcher SHA、repo/live bootstrap SHA、Runner task 06:00 / Bootstrap task 05:55 / NextRunTime / NumberOfMissedRuns=0、Runner Action 本番起動 mode、Scheduled Task watcher/bootstrap target、direct runner pre-run interlock と drift repair 後の同期済み runner re-exec、Bootstrap Action の `-SmokeTest` / short timeout / isolated state/log、watcher/bootstrap 経由の実起動 canary `smoke_ok` を確認し、非対象カテゴリ有無で完了判定を変えない。 |
| historical fallback evidence | 旧 fallback 証跡は通常完走ではなく、非対象カテゴリ探索失敗や required artifact 欠落の成功理由にしない。 |
| verify-publish-complete | public URL、publish-status、audio、Podcast の日付 sentinel を確認し、曜日別カテゴリ仕様と矛盾させない。 |

## Operational Premise Fidelity

復旧済みの公開成果物を、後続の goal、incident、E2E、または仕様整理の都合で未復旧扱いに巻き戻してはならない。現在状態の復旧タスクと、将来の完走判定 gate は分ける。

goal が打ち取れなかった理由、完走扱いになった理由、どの gate が公開未更新を止められなかったかは incident evidence に残す。ただし、復旧済みの公開成果物、公開済みの非対象カテゴリ artifact、または公開仕様上不要な artifact を後から required failure に変えてはならない。

pytest PASS は必要条件であり十分条件ではない。daily quality PASS は必要条件、public URL PASS は必要条件、runner/watcher live readiness は必要条件である。効率的・完全完走を主張するための必要条件は、1時間以内の本番相当 push直前 E2E PASS、または同等の証跡で SLO と公開面が一致していることを示すことである。

SLO gate 実装を SLO 達成実測と混同してはならない。E2E 未実施なら効率的・完全・1時間以内完走とは報告してはならない。テスト Green、SLO gate 実装、または public URL 単発 200 は必要条件であって、単独では完全完走の十分証明ではない。

## E2E Final Admission Covenant

News-GraspのE2Eは `final_confirmation_only` であり、未知欠陥の発見・デバッグ・readiness判定に使ってはならない。要求と運用を先に `static → contract → simulation → component → integration → live reconcile` の低コスト層で閉じ、各層の正負fixture、source hash、実consumer、live runtime freshnessがGreenになった後だけ `NEWS_GRASP_E2E_FINAL_ADMISSION_V1` を発行する。

admissionはrunner hash、引数、issue date、scheduled-equivalent intent、必須上流証跡のpath/hash/statusへ束縛する。callerが渡せる必須証拠は `efficiency_design`、`adversarial_review`、`route_manifest`、`red_suite_coverage`、`static`、`simulation`、`isolation` の7種である。`red_suite_execution` はcaller証拠として渡せず、公式admission producer自身が `tools.red_suite_execution` を一度だけ実行して8番目の証拠として挿入する。`red_suite_coverage` は `RED_SUITE_COVERAGE_REPORT_V1`、findings空、14 Requirement、10 viewpoints、3 domain scopes、49 unique fixtures、140 pair cases、5 routes、200 traceability cells、coverage hash一致を実consumerが再検証する。`red_suite_execution` は `RED_SUITE_EXECUTION_RECEIPT_V1`、49 selectorと140 pair Red caseから実収集されたexact 190 node、collection error 0、missing outcome 0、190 passed、収集node集合hash、matrix・fixture・pair case・historical corpus・producer・pair test sourceのhash一致を必須にし、文字列Green、件数、別名関数、caller作成JSONによる自己申告を拒否する。`isolation` は `tools/e2e_isolation.py` がexact source commitから新規detached worktreeを作り、対象日artifactだけを除去し、他日artifactとsource repoを不変に保ち、runnerの既存artifact述語がfalseであることを示したreceiptでなければならない。許可root外、既存target、commit不一致、壊れたJSONLは隔離作成前または対象artifact変更前にfail-closedとする。consumerはadmission内の自己整合だけでなく、wrapperが実際に起動する引数配列を別JSONから再読込し、順序・値・絶対pathまで完全一致しなければ `E2E_COMMAND_DRIFT` で拒否する。同一issue date・同一scheduled-equivalent intentで一回だけ消費でき、別worktree、別receipt、別run_idで試行回数をresetしない。公式wrapperはbudget admissionを一度だけ予約し、そのreceiptをrunnerへ引き継いで同じ予約を再利用し、final admission消費、runner起動の順を固定する。wrapper経由でhigh-cost attemptを二重予約してはならず、runner単独のproduction entryだけが自己予約する。NoPublishを必須、ResumeFromStageを禁止する。存在するだけの証跡、文字列Green、caller指定ledger、product alias、並行consume、stale sourceはfail-closedにする。

TDDのRedは、単一の失敗テストや単一fixtureを作れば足りるものではない。Requirementを正常、境界、異常、復旧、replay、identity drift、scope escape、他成果物不変、人間・資源影響へ分解し、各Acceptanceが独立した反証fixtureを持ち、一つのfixtureを削除または別fixtureで代用しても他の要件が検証済みに見えないAcceptance Matrixを実装前に固定する。網羅的なテスト観点を列挙できない状態は要件定義が未完了である反証として扱い、実装へ進まない。collection errorや未実装例外一件で全Redを代表させず、全fixtureを収集して各観点の失敗を個別に観測してからGreen capabilityを発行する。

独立fixtureは関数名や定数だけの違いでは成立しない。docstring-only、`pass`、`return None`、定数だけの`assert`、behavior observationを持たない関数をtrivialとして拒否し、文字列・数値定数を正規化したAST bodyが重複するfixtureも単一実装の別名として拒否する。意味形状hashはfixture本体だけでなく同一ファイル内で到達するhelper closureを含み、helper名だけを変えた薄いwrapperも同一実装として拒否する。各fixtureは直接assertionまたはtyped exception observationを所有し、source bytes hashと意味形状hashの両方をcoverageへ束縛する。

この分解の実行正本は `fixtures/deepdive_quality/tdd_acceptance_matrix.json` の `RED_SUITE_COVERAGE_V2` とする。E2Eは目的、非目的、L0-L8層、readiness/admission、attempt identity、checkpoint境界、探索分離、資源予算、副作用境界、停止・失敗、証跡、product完了境界の12 Requirementへ分け、DeepDive URL provenanceとPodcast読者価値を加えた14 Requirementを正本とする。観点集合は `normal/failure/boundary/substitution/drift/replay/missing/cross_lineage/recovery/human_impact` のexact 10種であり、`final_e2e`、`deepdive_url_provenance`、`podcast_reader_value` の3 domain scopeがそれぞれ固有の10観点fixtureを持つ。共有品質4経路とfinal wrapperへ `Requirement fixture × same-domain viewpoint fixture × route fixture` で結ぶ。12 E2E Requirement × 10観点 × final wrapperの120セルと、2 content Requirement × 10観点 × 4共有経路の80セル、合計200 traceability cellsを `python -m tools.deepdive_red_suite_coverage` が検証する。49 fixture Greenと140個別pair Redを `RED_SUITE_EXECUTION_RECEIPT_V1` へ束縛し、200個の重複testを作らず、各cellを実行済みのRequirement・同一domain観点・route証拠へ追跡可能にする。140 pair Redは要件と観点の個別bindingを壊す `traceability_only` のメタデータ完全性試験であり、本番欠陥の挙動証明を代用しない。本番挙動は14 Requirement、30 domain観点、5 routeの49 fixtureが所有する。異なるdomainの観点fixtureによる代用、観点欠落、route欠落、単一fixture・同一実装本体への集約、monolithic E2E Requirement、mock-only、production consumer・expected Red・counterevidence欠落、未知Requirementをfail-closedにする。件数、全体polarity、collection error、同じfixtureの別名だけではTDD admissionを発行しない。

validatorが同時に複数issueを返した場合は、先頭issueだけを直してretry budgetを消費してはならない。orchestratorは全issueをhandler別の有限 `repair-plan` へ変換し、artifactだけを重複除去して失敗観点を保持する。runnerは全deterministic handlerを同一再検証前に各一回だけ実行し、同じhandlerを別step名で再実行するplan、scope外artifact、unknown handlerを副作用前に拒否する。`followup_review_required` は偽URL隔離と混同せず、`followup-review-evidence-patch` がcurrent reporter artifact、公開日、date evidence、意味差分を一致確認できるfresh recordだけへreview証拠を付与する。reporter境界ではカテゴリ全体ではなく各recordのthumbを個別に検証し、一件でもnull、空、非HTTP、自己参照、Google News proxyならeditorへ渡さない。

高コストconsumerは英字keyだけをgoal権威としてはならない。`単一の最終production-equivalent NoPublish E2E`と、重複探索・E2E連発・無駄な外部model起動の禁止が同じNews-Grasp goalにある場合、final E2E上限を1、正常経路のmodel call上限をreporter 7 + editor 1 + DeepDive 1の9へ解釈する。retry/repair分を先回りで追加しない。旧parserが同一goalを上限0で登録済みでも、call countとE2E countがともに0、stateがactivated、現在goalから再計算した有限上限と完全一致する場合だけ、一度だけ `limits_promoted_from_goal_semantics` へ遷移できる。消費済み、曖昧goal、非0上限、二度目の変更、上限引下げ・引上げは `HIGH_COST_ISSUED_LIMIT_MISMATCH` で拒否し、ledger削除やtask identity変更で回避しない。

E2Eで初見の内部欠陥が出た場合は `UPSTREAM_DESIGN_ESCAPE` として該当する最上流の要件・影響調査・Red fixtureへ戻る。同一E2E runをpatch後にresumeせず、そのissue dateのE2E試行は消費済みとして保持する。外部認証などE2E外の境界が未達なら、そのoperationだけをdeferし、E2Eを繰り返して解決しようとしない。

coverage receiptは14個のRequirement fixture、30個のdomain固有viewpoint fixture、5個のroute fixtureの計49 fixtureについて、実行可能nodeだけでなくfixture本文SHA-256集合を保持する。さらに14 Requirement × 10観点を140個のaddressable pair Red caseへ展開し、各caseが対象Requirementの`expectedRed`と同一domain観点の`counterevidence`を個別に破壊して、対象ID入りの`missing_requirement_binding`と`missing_scope_viewpoint_binding`を観測する。これは `pairCaseMode=traceability_only` の追跡完全性試験であり、production defect injectionではない。公式admission producerは49 selectorと140 pair caseを単一pytest invocationで一度だけ実行し、exact 190 collected/passed nodeと集合hash、collectionとcall outcomeを分離したreceiptを生成する。caller receiptは受理しない。admission consumerは発行時と消費時にmatrix、49 fixture、140 pair case、historical corpus、producer、pair test sourceを再読込し、本文drift、cross-domain substitution、path escape、非Python、構文不正、過大fixture、collection error、missing outcomeをfail-closedにする。routeごとに同じtestを再実行せず、200セルは実行件数でなく49 Green fixture・140 Red pair・5 routeを結ぶtraceabilityとして扱う。

execution receiptはfixture自身だけでなく、`tools/**/*.py`、`scripts/ops/**/*.ps1`、`config/**/*.json`、`tests/**/*.py`、pytest設定、requirementsのpath→bytes hash集合をproduction dependency manifestとして束縛する。発行後にvalidator、runner、helper、conftest、plugin設定のいずれかが変わった場合、consume時にsource mismatchとして拒否する。

公式admission producerは190 node実行前に出力identityへ束縛したWindows file lockを非待機で取得する。同じoutputへ並行発行が来た場合は片方だけが実行を所有し、他方は `E2E_ADMISSION_ISSUE_BUSY` で実行前に拒否する。`exists()` の事前確認だけを排他制御として使わない。

## DeepDive Source and Podcast Value Covenant

DeepDiveの公開価値は、記事が表示されることではなく、読者が各主張の根拠へ到達でき、Podcastが記事の異なる判断価値を順序立てて提供することである。各記事は `data/deepdive-provenance/<date>.json` に、記事hash、URL集合hash、URLの全出現位置、公開href、最終URL、HTTP status、取得時刻、本文hashを束縛する。403、404、空本文、soft-404、汎用topへのredirect、未観測URL、記事変更後のstale manifestはGreenにしない。Python transport固有の403またはCA差だけは同じ本文検査を通るWindows system transportへ一回だけfallbackし、404/410ではfallbackしない。

Podcastは `current_signal`、`evidence`、`causal_chain`、`counterevidence_or_limit`、`change_over_time`、`decision_implication`、`next_action` の7区間を持つ。各区間はprimaryとsupportの14根拠を記事本文へ結び、全価値区間で再利用しない。固定scaffold、意味言換えloop、markerだけの根拠、出典本文不一致、14文未満をmoduloで水増しする生成はfatalとする。

この品質判定はproduction generation、repair/publish、daily quality、Codex日次監査の4経路が同じ `tools.deepdive_quality` CLIと同じroute registryを使う。URL側は `deepdive_url_provenance_invalid`、Podcast側は `deepdive_dialogue_value_invalid` を共有issue codeとし、coverage matrixとrepair registryがdeterministic handlerを所有する。日次監査だけの別validator、runnerだけのskip、過去日ごとの手修正は正規経路にしない。

## Human Commitment

### Luna-high Runtime Migration Commitment (2026-07-16)

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | そもそも5.4は近日中に廃止されるため、gpt-5.6-luna-highに切り替える方針とする。gpt-5.6-terraもgpt-5.6-luna-highに切り替える。5.4系に依存する処理が残らないように対応すること。 |
| approved_goal_statement | reporter、style editor、repair、newsroom editor を `gpt-5.6-luna` / reasoning effort `high` へ統一し、gpt-5.4系に依存する本番処理を残さない。DeepDive は既存 `gpt-5.6-sol` / high を維持する。 |
| approval_evidence_ref | current chat turn, 2026-07-16 |
| commitment_version | model-runtime-luna-high-2026-07-16 |
| commitment_scope | model policy、runner、Codex timeout wrapper、ops installer、operational prompts、newsroom preflight、judge、cost projection、runtime dependency audit、関連tests、live runner/wrapper同期。過去benchmark/raw/report/content evidenceは変更しない。 |
| open_questions | None. commit/push/public publishは今回未要求。 |

| Link item | Decision |
|---|---|
| Affected matrix rows | `Runner / state / recovery` |
| Gate update decision | modelとreasoning effortを同じpolicy正本からrunner/wrapperへ渡し、retired model参照はproduction/history/content/unknownへ分類する。productionまたはunknown残存はpreflight失敗とする。 |
| Verification command | `.venv\Scripts\python.exe -m pytest tests/test_model_policy_and_eval.py tests/test_runtime_model_dependency_audit.py tests/test_codex_wrapper_reasoning_effort.py tests/test_model_judge_policy.py tests/test_product_spec_contract.py -q`; `py -3.12 tools/audit_runtime_model_dependencies.py --repo-root . --format json` |
| Live reflection | backup付きinstallerでrepo runner/wrapperをlive binへ同期し、manifestとSHA parityを確認する。 |

### Artifact Lifecycle Commitment (2026-07-15)

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | 本件全体に関する恒久対策と対応をおねがいします。 / さっさと仕事してくれ。あと、勝手にVSCODEをたちあげるのやめろ。 |
| approved_goal_statement | benchmark / editor のraw artifactをGit管理面から分離し、既存未追跡を無損失archiveしたうえで、同じ大量未追跡を再発させない。作業中にVS Codeを起動しない。 |
| approval_evidence_ref | current chat turn, 2026-07-15 |
| commitment_version | artifact-lifecycle-2026-07-15 |
| commitment_scope | `tools/artifact_lifecycle.py`、benchmark runner 2種、editor attempt snapshot path、関連tests/spec、既存raw artifact archive。canonical tracked benchmark evidenceは保持する。 |
| open_questions | None. |

| Spec Item | User/Operator Outcome | Concrete Acceptance Example | Failure Signal | Green Verification | Evidence Plan |
|---|---|---|---|---|---|
| Local artifact lifecycle | raw benchmark/editor出力がGit statusを数千件汚さず、必要時にhash付きで復元できる | Given benchmark or editor attempt runs, When raw files are written, Then outputs stay under ignored `_ops/**`; archive is copy-verify-delete and resumable | raw default points to `build/**`; archive count/hash mismatch; active lock ignored; source deletion before copy verification | `.venv\Scripts\python.exe -m pytest tests/test_artifact_lifecycle.py tests/test_codex_recovery_benchmark.py tests/test_external_benchmark_matrix.py -q` | pytest、transaction manifest、journal、archive count/bytes、`git ls-files --others --exclude-standard` |

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
| Gate update decision | 記事カード要約UIは `tests/test_summary_layer_lanes.py` で lane role / marker / spine / icon / card shell preservation と役割者名表示の不在を固定する。アイコンは DOM に存在するだけでは Green ではなく、`FACT / CONTEXT / OUTLOOK` の円形アバター背景、SVG、短ラベル、表示 marker `事実・概要 / 背景・要点 / 影響・展望` が3段すべて視認可能であること、`--summary-*` の未定義CSS変数がないこと、Claude Code 原本デザインの left avatar column / body column / spine 構造を壊さないことを同 test と Chrome 操作系スキルの実画面証跡で確認する。ESSAY 側は `tests/test_summary_pattern_d.py` で `summary-template.html` が同じ3層レーン部品を使い、アイコンは保持しつつ旧 `summary-sec__bullets` と旧役割者ラベルに退行しないことを固定する。Summary 生成段階は LP「本日のテーマ考察」用 `theme_lanes` と各カテゴリ `section.lanes` を正本にし、現在の `lead` / `body` を後から文分割して `FACT / CONTEXT / OUTLOOK` や `WATCH / SIGNAL / IMPLICATION` へ割り振らない。`tests/test_newsroom_prompts.py` は `theme_lanes` / `"lanes"` / `【事実・概要】：` / `【背景・要点】：` / `【影響・展望】：` を prompt 正本へ固定し、`tests/test_reflection_theme_essay.py` は parser / LP / Summary カード / Tomorrow Board が明示 lanes を優先することを固定する。Reporter 生成段階は `tests/test_newsroom_prompts.py` で `【事実・概要】：` / `【背景・要点】：` / `【影響・展望】：` を prompt 正本へ固定し、旧 `【事実】：` / `【背景】：` / `【展望】：` に戻さない。スマホ版トップ帯は `tests/test_home_variant_b.py::test_home_brand_mobile_uses_compact_issue_header` で日付メタ上段化、tagline / ISSUE label / TOKYO 行の非表示、Issue 番号の下段配置を固定し、`tests/test_home_variant_b.py::test_home_nav_mobile_uses_compact_yesterday_snapshot_for_actions` で PODCAST / ARCHIVE が YESTERDAY に被らない昨日断面の小型ボタンを固定する。過去記事要約3層リライトは `tests/test_rewrite_bullets_3layer.py` で3 bullet、URL、数値、固有名詞、`[[...]]` / `**...**` / `__...__` の保持を固定する。 |
| Verification command | `.venv\Scripts\python.exe -m pytest tests/test_summary_layer_lanes.py tests/test_summary_pattern_d.py tests/test_home_variant_b.py tests/test_rewrite_bullets_3layer.py tests/test_newsroom_prompts.py tests/test_card_summary_strip_markdown.py tests/test_generate_pages.py tests/test_product_spec_contract.py -q`; `.venv\Scripts\python.exe tools/generate_pages.py --full`; `designmd lint .\DESIGN.md` |
| Integration gate | 結合テスト Green の場合のみ commit/push する。Yellow 以下は修正と再テストを継続し、push しない。 |
| Public boundary | push 後の公開 URL / GitHub Pages / remote HEAD 確認は push を実行した場合だけ行う。 |

## Category Hero Card Turn 4 Commitment

| Field | Value |
|---|---|
| approval_status | Committed |
| committed_by_human | true |
| approved_by_user_text | PLEASE IMPLEMENT THIS PLAN: |
| approved_goal_statement | News-Grasp のカテゴリートップ画面「ヒーローカード」を、`design_handoff_fx_hero_card/README.md` を正典として Turn 4 の 4a / 4b / 4c だけで hifi 実装する。 |
| approval_evidence_ref | current chat turn: user request `News Grasp のカテゴリートップ画面「ヒーローカード」を改善実装してください。` and follow-up `PLEASE IMPLEMENT THIS PLAN:` |
| approved_at | 2026-07-01 |
| commitment_version | category-hero-turn4-2026-07-01 |
| commitment_scope | `prompts/category-template.html`, `docs/assets/site.css`, `tools/generate_pages.py`, FX external rate helper, newsroom/routine prompts, generated category docs, service worker cache version, commit/push/public verification. |
| open_questions | None after ChatGPT review pass. API outage, GitHub outage, or remote divergence remains typed blocker and must not be force-pushed. |

この改修は `Feature Change Quality Gate Matrix` の次の行に完全リンクする。

| Link item | Decision |
|---|---|
| Affected matrix rows | `Public UI / OGP / PWA / thumbnails`; `Summary / editorial reflection`; `External integration / auth` |
| Gate update decision | ヒーローカードは `為替レンズ ヒーローカード改善` README を Visual Source of Truth とし、実装対象を Turn 4 の 4a / 4b / 4c のみに固定する。Turn 1/2/3 の旧方向比較・一覧・検証 strip を UI として採用しない。要約は文単位で `body_max_chars=104` に収まる文だけを箇条書き表示し、文中 `…` で切らない。あふれた場合だけ `続きを読む →` を出す。FX だけ `ExchangeRate-API Open` の `https://open.er-api.com/v6/latest/USD` を使い、公開 UI には `Rates By Exchange Rate API` の attribution と最終更新時刻を表示する。非FXは代表スコア重複を置かず、`lead-signal` panel でカテゴリートップの最重要シグナル（記事タイトル、媒体、時刻、短い含意）を出す。`lead-signal` の見出しは生成段で `lead_title_lines` へ構造化し、行数上限、概算表示幅、短すぎる孤立行、区切り記号末尾を `category hero lead title line quality` 契約で publish 前に落とす。Summary reflection のカテゴリ section 見出し `### §NN {tag} — {focus_title}` はカテゴリートップ hero の「今日の焦点」の生成正本であり、件数文・記事数・カテゴリ名だけの見出しは `tools.validate_summary_reflection` / `validate_daily_quality` で落とす。 |
| Verification command | `.venv\Scripts\python.exe -m pytest tests/test_category_hero_sentence_fit.py tests/test_category_hero_turn4_contract.py tests/test_fx_rates.py tests/test_newsroom_prompts.py tests/test_validate_summary_reflection.py tests/test_validate_daily_quality.py tests/test_category_editorial_essay.py tests/test_category_grid_fallback_emphasis.py tests/test_product_spec_contract.py -q`; `.venv\Scripts\python.exe tools/generate_pages.py --full`; `designmd lint .\DESIGN.md`; Playwright desktop/mobile visual smoke; push 後 public DOM/CSS/SW sentinel。 |
| Integration gate | Red tests を先に追加し、Green になるまで実装を続ける。ローカル Green 後のみ safe-commit、push、remote HEAD 一致、GitHub Pages public sentinel 確認へ進む。 |
| Public boundary | `docs/assets/site.css`、`prompts/category-template.html`、generated docs を変更するため、`docs/sw.js` の `SW_VERSION` bump と public CSS / public DOM / service worker version の確認を同じ変更単位に含める。 |

## User Answer Provenance

| Date | Source | Exact user text |
|---|---|---|
| 2026-07-16 | Current chat model runtime migration | そもそも5.4は近日中に廃止されるため、gpt-5.6-luna-highに切り替える方針とする。gpt-5.6-terraもgpt-5.6-luna-highに切り替える。5.4系に依存する処理が残らないように対応すること。 |
| 2026-06-26 | Current chat planning intent | ChatGPTレビューに通すための最低限の基準であるインプットは完全に用意してからレビューに渡す |
| 2026-06-26 | Current chat planning intent | その上で過去レビューで指摘された内容を字面だけでなく根本的に全体最適を考えた上で修正してからレビューに渡す |
| 2026-06-26 | Current chat implementation approval | PLEASE IMPLEMENT THIS PLAN: |
| 2026-06-28 | Current chat implementation approval | PLEASE IMPLEMENT THIS PLAN: |
| 2026-06-28 | Current chat quality gate instruction | 本修正は品質ゲートと完全に仕様をリンクすること。実装後に結合テストを実施しGreenの場合のみpushする。Yellow以下はGreenになるまで修正→テストすること。 |
| 2026-07-01 | Current chat implementation request | News Grasp のカテゴリートップ画面「ヒーローカード」を改善実装してください。デザイン仕様は同梱の `README.md` が正典です。まず `README.md` を通読してから着手してください。 |
| 2026-07-01 | Current chat design selection | 採用は Turn 4 の 4a / 4b / 4c の3つだけ |
| 2026-07-01 | Current chat external data selection | 外部API連携 |
| 2026-07-01 | Current chat publish scope selection | pushまで含める |
| 2026-07-01 | Current chat review gate request | ChatGTPレビューを受けてから再提出して。 |

## Sustainable Complete Repair

外部システム要因以外で公開面が揃わない停止は許容しない。fallback は通常日次完走ではない。通常日次バッチ経路の fallback publish は完全禁止とし、fallback_ok や published_fallback_with_notice を OK marker、terminal success、Podcast、DeepDive、distribution、notification の完了証跡として扱ってはならない。旧 fallback 証跡を読む場合は、歴史データまたは手動緊急公開の痕跡として扱い、通常完走に昇格しない。

handler 未実装は Red とする。coverage matrix に未掲載の failure は blocked_unknown_repair_class として止め、prose hint だけで repairable に倒してはならない。handler_unimplemented_red は最終 Green 条件では 0 件でなければならない。

repair completeness = coverage matrix + zero unimplemented + fixture repair + runner single path。existing artifact repair では LLM worker を起動しない。既存 artifact がある場合は deterministic handler または typed not-applicable / blocked status で扱い、対象 artifact が全 missing かつ typed reason がある場合だけ missing artifact generation を許可する。

DeepDive復旧は記事HTMLの存在だけで成功にしない。現在日付の引用URL生存証明と、対談の7価値・出典実在・根拠本文一致・日跨ぎ反復上限が同じ復旧runでGreenになった後だけ、TTS、publish、`publish_complete`へ進める。通信不能は明示的な監査延期であり生存証明ではない。静的pytestだけは外部URLを分離できるが、そのskip receiptを本番URL証明へ再利用してはならない。

live runner 上書きは backup + 明示承認 + rollback を満たす場合だけ許可する。repo runner と live runner の SHA 一致は必要条件であり、runner 実行・公開検証・Podcast 検証の代替にはならない。

## Repair Decision Debt Covenant

repair の根本対策は、repair の回数を増やすことではなく、validator / coverage matrix / orchestrator / registry / runner が何を決める責務を持つかを上流で固定することである。新しい repair failure を下流 test や smoke で塞ぐ前に、どの層が source of truth を読み、どの層が routing を決め、どの層が artifact scope を縮約し、どの層が terminal state を出すかを定義する。

| Layer | Decision responsibility |
|---|---|
| Validator | `issue_code`、対象 artifact、日付、category、evidence を構造化 issue として出す。prose だけの failure は legacy 補助であり、通常完走の完全性証跡ではない。 |
| Coverage matrix | `issue_code` から repair class、handler、allowed scope、failure status を一意に決める。未掲載は `blocked_unknown_repair_class`。 |
| Orchestrator | 複数 issue をordered repair ledgerからhandler別の有限`repair-plan`へ変換し、最初のissueだけで複合障害を代表させない。 |
| Registry | handler の存在、入力 scope、handler not-applicable、出力 scope を別 status で返す。 |
| Runner | `repair-plan`の全deterministic handlerへ各handlerに属するartifactだけを渡し、同一再検証前に各一回実行する。typed statusを`handler_unimplemented`やgeneric errorへ丸めない。 |

deterministic handler として宣言する row は `_blocked_ambiguous`、`noop`、`not_applicable` を修復実体の代替にしてはならない。registry が `noop` / `not_applicable` を返した場合は repair 成功として扱わない。same-gate re-verify が Green の場合だけ、別実行によって既に収束した状態として runner が次 stage へ進める。

repair の完全性 claim は `tools.repair_system_completeness` を単一 closed-world gate とする。この gate は validator issue code の source 抽出、coverage row の一意性、unknown route の fail-closed、deterministic row と registry handler の双方向到達性、handler artifact scope、matrix verify gate と handler `supported_verify_gates` の能力一致、`orphan_repair_implementation` の不存在、historical failure corpus / weekly regression count、runner を含む主要 source の `source_hashes` を同一 snapshot で検証する。手書き issue 集合、registry にだけ残る handler、未登録の `_repair_*` entrypoint、単一 `verify_gate` の偶然一致を完全性証拠にしてはならない。

同じ validator 語彙に複数の failure mode がある場合、handler を選ぶ前に issue_code を分ける。少なくとも `thumb_missing` / `thumb_invalid`、`search_audit_coverage_terms_missing` / `search_audit_queries_recoverable` / `search_audit_queries_insufficient` / `search_audit_dropped_evidence_recoverable` / `search_audit_dropped_evidence_missing` / `search_audit_missing` / `search_audit_invalid` / `search_audit_collection_shortfall`、`audio_script_missing` / `audio_script_quality_invalid` / `audio_publish_state_invalid` / `audio_public_reflection_missing` を別契約とする。evidence 不足を推測で補完して Green にせず、legacy の方向不明 code は explicit typed Red とする。

決定債務 status は次を正本とする。

| Status | Meaning |
|---|---|
| `repair_context_overbroad` | gate が対象外 artifact も渡したが、in-scope artifact があり runner/registry が縮約して続行できた。 |
| `repair_context_scope_mismatch` | 選択された handler に渡せる artifact が 1 件もない。classifier / validator / matrix の接続バグとして Red。 |
| `blocked_repair_handler_unimplemented` | handler_id が registry に存在しない場合だけ。scope mismatch や handler 失敗をこの status に丸めない。 |
| `blocked_deterministic_repair_not_applicable` | handler は存在するが現 artifact を修復できず、別 issue へ継続できない。 |
| `blocked_digest_only_ambiguous` | current reporter manifest から append 漏れか旧 run card 残存かを一意に判定できず、自動変更しない。 |
| `blocked_articles_only_record_incomplete` | digest card の必須 field または record evidence が不足し、安全な card 生成ができない。 |
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
