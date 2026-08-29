---
name: news-grasp-direct-mainline
description: Run the 06:00 News-Grasp scheduled production directly with Codex, without the legacy runner. Use for same-day article generation, quality repair, Web/audio/YouTube/playlist/notification publication, public-only completion verification, and same-day direct recovery.
---

# News-Grasp Direct Mainline

当日版の読者可視公開を最上位目的にする。旧 runner、NoPublish、fallback、監査成果物、durable goal、URL 200 単独を完了 authority にしない。

## 開始契約

1. Asia/Tokyo の `issue_date` を確定し、`automation_id + canonical cwd + issue_date` の current execution だけを使用する。
2. 最初の実作業として host の title action を最大1回だけ試す。期待値は `YY/MM/DD News-Grasp 臨時本線日次バッチ 6:00 記事作成・公開` とする。
3. `python -m tools.news_grasp_title_control` で `updated / already_ok / unavailable / failed / skipped` を記録する。成功statusは実titleのexact一致を必須にする。失敗statusは `post_publish_issue_list` に残し、公開作業を止めない。
4. `tools.publish_inventory.scheduled_category_ids(issue_date)` を対象カテゴリ正本にする。固定7カテゴリへ戻さない。

## Direct 本線工程

次の順序を変えず、title control と下記1〜20を direct receipt の21要素 `stage_history` に記録する。21の最終報告は public Green 後に行い、completion gateへの循環依存を作らない。

1. 対象日・scheduled category inventoryを確定する。
2. カテゴリ別ニュースを収集する。
3. dedup、freshness、URL evidenceを検証する。
4. カテゴリdigestを生成する。
5. reporter outputをrecord単位で検証する。
6. `data/articles.jsonl`へ対象日recordを追記する。
7. Summaryを生成する。
8. Daily audio script、TTS、audio publishを行う。
9. DeepDive記事を生成する。
10. shared DeepDive qualityでprovenance、dialogue、rendered HTMLを検証する。
11. 日付HTML docsを生成する。
12. `python -m tools.validate_daily_quality --date <issue-date> --require-deepdive --json`をGreenにする。
13. Daily/DeepDive YouTube Podcastを作成・uploadする。
14. playlistへ登録する。
15. notificationを送信する。
16. distribution manifestを作成する。
17. `docs/publish-status.json`を対象日の`published_ok`へ更新する。
18. commitし、`origin/main`へpushする。
19. Pagesの対象日content identityを確認する。
20. runner/readinessを含まないdirect public completionを検証する。
21. title status、actual title、public evidence、SLO debt、post-publish issuesを報告する。

Reporter/editor/repair/newsroom_editor は repo-local model policy の Luna/max、DeepDive は Sol/high の独立routeを維持する。単一親モデルへ統合しない。

## Quality・公開gate

- `quality`より前にupload/publish完了を主張しない。
- DeepDiveのMarkdown存在だけでなく、provenance、dialogue、rendered publicをGreenにする。
- direct completion receiptはWeb、Daily audio、DeepDive article/audio、Daily/DeepDive YouTube、playlist、notification、distribution、publish-status、remote commit、Pagesを同一issue-dateで連言評価する。
- `publish_commit`をdistribution、remote commit、Pages反映と一致させる。
- title失敗は非阻害だが、`updated/already_ok`の不正title claimと失敗statusのissue未記録は拒否する。

## 速度・回復

- 45分を目標にし、45分時点で残工程を公開critical pathへ絞る。
- 75分以降は新規の任意high-cost stage、追加review、polishを開始しない。
- 90分超過はSLO debtとして記録し、実行可能なexact public successorを継続する。
- cost/ledger/binding failureは該当model operationだけをzero-call Redにする。fresh artifact、deterministic tool、公開可能なlocal successorがあれば同じrunで進む。
- OAuth、2FA、quota、外部障害は具体的証拠があるsurfaceだけをdeferする。他surfaceを継続し、全体Greenを偽らない。
- quality Redは該当artifactだけを修復し、同じquality gateを再実行する。旧 runner、NoPublish、fallbackへ切り替えない。

## 禁止

- `news_grasp_runner.py`、`news_grasp_nopublish.py`、`scripts/ops/news-grasp-runner.ps1`を起動しない。
- runner state、readiness、goal、audit/report、artifact existence、URL 200、fallback statusをcompletion authorityにしない。
- public incompleteかつexact successorがある状態で終了しない。
- raw process kill、focus theft、auto-open、user monitoringを行わない。
