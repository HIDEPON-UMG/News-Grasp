# Product Spec: News-Grasp

> **Status**: Constitution
> **Last Updated**: 2026-06-20
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
