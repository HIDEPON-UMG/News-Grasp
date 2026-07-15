本リポジトリ内の `prompts/newsroom-editor-system.md` と `prompts/style-guide.md` を Read で読み込み、その指示に厳密に従って当日（JST）の News-Grasp 日次 digest を生成してください。`prompts/routine-system.md` を runner の入口として直接読んではいけません（旧体制の退避コピーは `prompts/runner-prompt-legacy.md`）。

モデル方針は `tools/model_policy.py` を正本とします。記者、必要時の文体調整、repair、編集長本体は `gpt-5.6-luna` / reasoning effort `high` に統一し、DeepDive は `gpt-5.6-sol` / reasoning effort `high` を採用します。文体調整は全記事一律ではなく、自然さ・News-Grasp らしさ・validator fail のいずれかで弱い記事だけに限定してください。

収集・dedup・鮮度・URL 生存確認を LLM 判断で代替してはいけません。WebSearch 前に候補収集を広げる場合も、`tools.harvest_candidates` と `tools.cross_category_dedup` の決定論出力を優先し、LLM は候補からの選定・要約・文体調整だけを担当してください。

移行期の互換規約として、カテゴリ別候補収集を実行する場合は `tools.harvest_candidates --category {cat}` を必ず使ってください。

記事 record の `date` は号日、`published_date` は記事公開日に分離します。git commit / git push / docs 生成 / publish gate 実行は絶対に行わないでください。Web Push も絶対に行わないでください。runner が gate、docs 生成、commit、push、retry budget、fallback publish を一元管理します。
