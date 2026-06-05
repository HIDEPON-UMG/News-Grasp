# 引継ぎ — タイトル文節改行 / 本日 AI digest 再オーサ (2026-06-05)

このファイルは次セッションの先頭で読み込めば、本セッションの実装意図と未完タスクを 1 ファイルで把握できる。Claude Code 想定 (workspace 内ファイル参照可)。

---

## 0. 本セッションのゴール (達成済)

ユーザの 3 件の指摘を解消:

1. **タイトル 3 行許容化** — TODAY'S THEME 見出しが 2 行強制で必要以上に縮小していた → 最大 3 行まで許容
2. **タイトル文節改行** — `word-break: auto-phrase` が BudouX で「円安／か利上げか」を別文節判定し「円安」を孤立改行させた → 句読点 (、。，．・) 後にのみ折返す方式へ
3. **本日 (06-05) TOP STORY が 06-03 と同じ Anthropic IPO で動かない** → MS Build / OpenAI Codex / Trump AI EO の 06-02/03 フレッシュ 3 件を上位に差替えて Anthropic IPO を AI 内 #4 に降格

実装 commit: **`9707875`** (push 済 → `HIDEPON-UMG/News-Grasp` `origin/main`)

---

## 1. 変更ファイル & 設計の要

### 1-1. タイトル文節改行 (Lv2 境界 1 箇所集約)

| ファイル | 行 | 変更概要 |
|---|---|---|
| [tools/generate_pages.py](../tools/generate_pages.py) | 460-477 | `insert_wbr` Jinja フィルタ新設。`、。，．・` の直後にのみ `<wbr>` を挿入する Markup を返す |
| [prompts/index-template.html](../prompts/index-template.html) | 116-117, 153-154 | `{{ hero_phrase_left\|insert_wbr }}` / `{{ hero_phrase_right\|insert_wbr }}` でフィルタ適用 (2 経路: latest_deepdive あり / なし) |
| [prompts/index-template.html](../prompts/index-template.html) | 317-343 | JS `fitHeroTitle` の閾値を `lh*2+1` → `lh*3+1` に変更し 3 行まで許容 |
| [docs/assets/site.css](../docs/assets/site.css) | 1124-1144 | `.home-hero__title { word-break: keep-all; line-break: strict; overflow-wrap: normal; }`。CJK の中途改行を全面禁止し `<wbr>` + 半角空白のみが break opportunity になる |

**設計階層** (feedback_check_design_principles に従う):
- Lv1 illegal state: N/A
- **Lv2 境界 1 箇所集約**: 句読点後 `<wbr>` 挿入を Jinja フィルタ 1 関数に寄せた (テンプレ毎の重複編集を消す)
- Lv3-5: 不要

**設計の核 — なぜ auto-phrase ではなく keep-all + insert_wbr か**:
- `auto-phrase` (Chrome 119+/Safari 17.4+) は BudouX による文節推定で改行点を選ぶが、「円安か利上げか」を「円安／か利上げか」と判定して "円安" を孤立改行させた (実機 06-05 朝の指摘)
- BudouX の出力はモデル学習結果なのでアプリ側から制御不能 = 句読点で必ず切れる保証がない
- `keep-all` で CJK 改行を全禁止 + 句読点後にのみ `<wbr>` を明示挿入する方式なら、改行点が**完全に決定論的** (句読点と半角空白のみ)
- 副作用: 句読点を含まない長い CJK 文字列は折り返せず overflow するが、hero_phrase は `_THEME_SPLIT_MAX_LEN=22` 以下に制約されているため実用上問題にならない

### 1-2. 本日 AI digest 再オーサ

ファイル: [digest/AI/2026-06-05-AI.md](../digest/AI/2026-06-05-AI.md)

| # | スコア | 記事 | URL | 鮮度 |
|---|---|---|---|---|
| 1 | 97 | Microsoft unveils new AI models to lessen reliance on OpenAI | https://www.cnbc.com/2026/06/02/microsoft-unveils-new-ai-models-lessen-reliance-on-openai-lower-costs.html | 06-02 |
| 2 | 93 | OpenAI: Codex for every role, tool, and workflow | https://openai.com/index/codex-for-every-role-tool-workflow/ | 06-03 |
| 3 | 88 | Trump signs EO seeking early government access to powerful AI models | https://www.cybersecuritydive.com/news/trump-ai-security-executive-order/821755/ | 06-02 |
| 4 | 84 | Anthropic confidentially files IPO prospectus (降格) | https://www.cnbc.com/2026/06/01/anthropic-ipo-s1-prospectus.html | 06-01 |
| 5 | 80 | Anthropic Ends Subscription Subsidy for Agents June 15 (継続) | http://www.techtimes.com/articles/317625/20260602/anthropic-ends-subscription-subsidy-agents-june-15-credit-pool-replaces-flat-rate-access.htm | 06-02 |

**URL gate 結果**: `tools/audit_all_article_urls.py --gate --match-session` で **206/206 OK** (HEAD/GET 全通過、`_session_urls.json` の date が 1970 で degrade モードに降りているため session 不一致 fatal は発生せず警告のみ)

**サムネ**: 暫定で予測 URL を `![thumb](...)` に貼った。失敗時は [docs/index.html](../docs/index.html) の `addEventListener('error', ...)` が `/assets/og/ai.jpg` にフォールバックするので表示崩れはしないが、本来は `tools/fetch_ogp.py` で実 OGP を取りに行く運用

---

## 2. テスト結果 (push 後)

```
$ News-Grasp/.venv/Scripts/python.exe -m pytest News-Grasp/tests/ --tb=no
265 passed, 5 errors, 2 failed (再走で 8 passed)
```

- **265 passed** — 本変更で回帰した契約テストはなし。`test_home_variant_b.py` は 20/20 PASS。`test_split_theme_phrases.py` も PASS
- **5 errors** (`test_email_full_render.py`): すべて `fixture 'html' not found` — pytest fixture 定義漏れの**事前バグ**で本変更とは無関係 (gen_email 系の test harness 整備が必要)
- **2 failed → 再走 8 passed** (`test_deepdive_urls_live.py`): HEAD/GET ネットワーク flake。再走で全 PASS なので無視可

---

## 3. 残タスク (delivered − done の差分)

`feedback_handoff_inventory_diff_closeout` に従い、意図的後回しと未着手を明示。

### 🟡 意図的後回し (対応設計付き)

| # | 残項目 | 対応設計 (これがあるから後回し可) | 工数感 |
|---|--------|--------------------------------|--------|
| **R3** | `data/articles.jsonl` に新 3 URL 追記 | アーカイブ・横断検索・過去 90 日マッチング (`routine-system.md` ステップ 2) が新 3 件を認識しないので追記が筋。`build/append_2026XXXX.py` パターンで bulk append すれば既存スクリプト群と整合する。タイトル + URL + source + score + tags + date を articles.jsonl の line JSON 形式で 3 行 append | 30 min |
| **R4** | Editorial / hero phrase の整合化 | `digest/Summary/2026-06-05.md` の frontmatter `theme:` は「Anthropic IPO と USD/JPY 160 円」のまま。home の hero 左タイトル「IPO 三つ巴と160円の壁 と AI バブルか革命か」は editorial 由来 (`tools/generate_pages.py` の `_split_theme_phrases` で 2 句に切られる) なので、本日の AI 主題が MS/Codex/Trump に振れた以上、editorial の `theme:` も再オーサが筋。提案フレーズ例: 「MS 脱 OpenAI と Codex 業務 OS と 160 円突破」を `_THEME_PRIMARY_SEPS` で 2 句に切れる形に成形 | 20 min |
| **R5** | 本物の OGP サムネ取得 | 暫定 thumb URL を `tools/fetch_ogp.py` で実 OGP に置換。失敗時はクライアント側 fallback (`/assets/og/ai.jpg`) で表示崩れはしないが本来の運用ではない | 15 min |

### 🔴 未着手 (連動推奨)

| # | 残項目 | 注記 |
|---|--------|------|
| **U2** | `docs/2026-06-05/index.html` (daily overview) 目視確認 | SSG では再生成済だが、AI 行のスコア順 + KV thumbnail が新 digest と一致しているか実視認していない |
| **U3** | `docs/ai/2026-06-05/index.html` (カテゴリ詳細) 目視確認 | 同上。Hero KV が Microsoft MAI で表示されているか |
| **U6** | `test_email_full_render.py` の fixture 修正 | 5 errors は本変更と無関係の事前バグ。`@pytest.fixture def html()` の定義が抜けている。conftest.py への移設 or test 内 fixture 化が必要 |

### ✅ 完了 (検証済)

| # | 項目 | 検証 |
|---|------|------|
| D1 | タイトル 3 行許容 + 文節改行 | JS eval で `titleText="IPO 三つ巴と160円の壁 と\nAI バブルか革命か、円安か利上げか。"` (3 行・font 50px・"円安か利上げか" が一塊) を確認 |
| D2 | `insert_wbr` フィルタ + `word-break: keep-all` | 計算済 style `wordBreak: "keep-all"` `overflowWrap: "normal"` 確認 |
| D3 | AI digest 再オーサ | 5 entries 構成書き出し |
| D4 | TOP STORY 差替 | featured セクションが Microsoft MAI (CNBC 06-02) に置換、Editor's Top 5 #1 も同記事 (97 / AI) |
| D5 | SSG 再生成 | `generate_pages.py` 完走 → 183 記事ページ + index/archive + 38 overviews + 6 deepdive |
| D6 | URL gate 通過 | 206/206 OK |
| D7 | push | commit `9707875` を `origin/main` へ反映 |
| D8 | 回帰テスト | 265 passed, 0 regression |

---

## 4. 次セッション最初のアクション候補

ユーザが「続きをよろしく」と言ってきた場合の選択肢:

### (A) 本番完全整合化 — 推奨経路
1. **R3 articles.jsonl 追記** (新 3 URL 3 行 append)
2. **R4 editorial theme 同期** (`digest/Summary/2026-06-05.md` の `theme:` を MS/Codex/Trump 主題に書き換え)
3. `tools/generate_pages.py` 再走
4. ブラウザで home / overview / category 詳細を実視認 (U2/U3)
5. 1 コミットで push (動詞+対象明示時のみ `# CLAUDE_PUSH_CONFIRMED` 付与)

### (B) サムネ品質向上のみ
1. **R5** `tools/fetch_ogp.py` で本日 AI 5 記事の OGP を再取得 → digest md の thumb URL を実 URL に置換
2. 再 SSG → push

### (C) テスト基盤整備
1. **U6** `test_email_full_render.py` の `html` fixture 修正
2. 単独 push (`tests: ...` プレフィックス)

---

## 5. 注意事項 (次セッションが踏みやすい地雷)

- **`data/_session_urls.json` を直接編集しない** — `.claude/hooks/append_session_urls.py` (PostToolUse:WebSearch/WebFetch) が自動管理する設計。LLM が触ると次の hook 発火で上書きされる
- **`word-break: auto-phrase` には戻さない** — BudouX が文節を誤判定する事例 (06-05 「円安か利上げか」) を本セッションで実機確認済。`keep-all` + `insert_wbr` 方式が結論
- **`<wbr>` を hero_phrase の data 側に書かない** — フィルタ層で挿入する設計に揃える。data 側に書くと Jinja autoescape で `&lt;wbr&gt;` 化されて効かない (Markup でないため)
- **push 前は必ず `audit_all_article_urls.py --gate` を通す** — 本セッションでは 206/206 OK だったが、R3 で articles.jsonl 追記後は新 URL も検証対象に含まれるため再走必要
- **コンテキスト膨張** — 本セッションは 185+ ターン到達。次セッション開始前に `/clear` 推奨。本ファイルがあれば文脈は復元できる

---

## 6. メタ情報

- 作成日時: 2026-06-05 (本セッション末)
- 作成者: Claude (Opus 4.7)
- 関連 memory:
  - `feedback_handoff_inventory_diff_closeout` — 残タスク列挙の方法論
  - `feedback_check_design_principles` — 5 段階で構造解決 (本件は Lv2 適用)
  - `feedback_llm_url_fabrication_ban` — URL 捏造防止 (本件は WebSearch 実通過 URL のみ使用)
  - `feedback_test_before_report` — push 後にテスト全走で回帰確認済
- 関連 git:
  - 本コミット: `9707875` (`home: タイトル 3 行許容 + 文節改行化 / 本日 AI digest を MS Build/Codex/Trump EO 中心に再オーサ`)
  - 直前: `a18fdcf` (Hero h2 fallback 修正 + dedup 続報ゲート機械化 + hook 発火 audit log)
