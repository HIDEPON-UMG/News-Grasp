# News-Grasp 日次ニュース音声朗読機能 — 実装仕様書 + ToDo（Codex 引き継ぎ）

- 作成日: 2026-06-16
- 種別: 実装仕様書（handoff、HTML 化適用外＝編集前提ドキュメント）
- 実装担当: Codex（本書を唯一の正とする。再質問なしで完走できる decision-complete な ToDo を含む）
- 由来アイデア: IdeaStash の `News-Grasp-newsgrasp-essay-tts-2026-06-15.md`（※エッセイ/DeepDive 廃止済みのため、当初の「週次エッセイ音声化」案は無効。本書が上書き）

---

## 0. 目的と成功条件（最初に固定）

### 上位目的
News-Grasp の日次配信（7カテゴリ×各5記事＝最大35記事＋横断トレンド予測サマリー）に対し、**当日のニュースを「聴いて」把握できる音声導線（mp3）**を追加する。テキストを読まなくても、その日が「どんな日だったか」「カテゴリ単位で何があったか」を 8〜9 分で把握できる状態にする。

### 今回スコープ
1. 編集長（既存 Codex 編集ステージ）が**当日の朗読原稿（ナラティブ型・約2,670字）を生成**する。
2. 独立した**音声専用ステップ**が、その原稿を AivisSpeech（ローカル）で TTS → wav → mp3 化する。
3. mp3 を **GitHub Releases** に置き、トップページ(Home)最上部と横断サマリーページに `<audio>` で埋め込む。
4. mp3 は **1ヶ月分（31日）保持**し、それ以前は Releases アセットから削除（git 履歴を肥大させない）。
5. 既存 runner にステップを統合。AivisSpeech 未起動なら**自動起動を試み**、それでも不可なら**非致命スキップ**（digest 公開は止めない）。

### 対象外（スコープ外）
- 過去バックログの一括音声化（最新運用のみ）
- 外部ストレージ（R2/S3）移行
- 複数話者・BGM・効果音などの演出
- リアルタイム/サーバサイド合成
- エッセイ/DeepDive 音声化（機能自体が廃止済み）

### 成功条件（DoD）
- [ ] 当日 digest から編集長が朗読原稿（`digest/Summary/{date}-audio-script.md`）を生成し、全7カテゴリに言及し、総字数が **2,500〜3,000字** レンジに収まる（バリデータが gate）。
- [ ] 音声ステップが `build/tts/{date}.mp3`（モノラル / 64〜96kbps）を生成し、尺が **6〜10分** レンジに収まる（ffprobe 実測）。
- [ ] mp3 が GitHub Releases（タグ `audio-daily`）にアップロードされ、ブラウザから 200 でダウンロードできる。
- [ ] Home 最上部・横断サマリーページに `<audio preload="none" controls>` が最新日付の Releases URL を指して表示され、**PC とスマホの実機でブラウザ再生できる**。
- [ ] Releases アセットが 31 日より古いものは自動削除され、常に最大31本（＋当日）に保たれる。
- [ ] 契約テスト（後述4件＋統合1件）が pytest で PASS。
- [ ] runner 統合後、**AivisSpeech 不在/合成失敗でも digest 公開が止まらない**ことを dry-run/実行で確認。
- [ ] 指定音声モデルの**個別ライセンス（商用可否・クレジット表記要否）**を確認し、必要ならサイトにクレジットを入れた（要確認事項、§7）。

---

## 1. 確定要件（対話で合意済み・これが正）

| 項目 | 決定 | 備考 |
|---|---|---|
| 原稿の型 | **カテゴリ・ナラティブ型** | 35記事の機械的羅列ではない。トップ記事を軸に「その日カテゴリで何があったか」を語る |
| 朗読対象 | 7カテゴリ + 横断トレンド予測サマリー | FX/AI/IT-Consulting/Mobility/Manufacturing/Economy/Game。エッセイ/DeepDive 対象外 |
| カテゴリの深さ | トップ記事中心 + 主要な動きに軽く言及（各約300字） | AskUserQuestion で確定 |
| 目標尺 | **6分以上**（目安は約7〜9分） | 2026-06-16 試聴後、7分未満を不合格にせず 6分以上を合格に変更 |
| 声質トーン | **親しみやすい語り口**のバリトン男声 | AskUserQuestion で確定 |
| 原稿生成の担当 | **編集長（既存 Codex 編集ステージ）が原稿を作る**まで担当 | 「原稿の音声化自体は独立した音声専用ステップで実施」 |
| 音声化 | **独立した音声専用ステップ** | 編集長は文章まで。TTS→mp3 は別ステップ |
| プレイヤー配置 | **トップページ(Home)最上部** + **横断サマリーページ** | AskUserQuestion で確定 |
| 音声ファイル | **1本通し**（mp3 / モノラル） | |
| mp3 配置 | **GitHub Releases**（git/docs に置かない） | .git 履歴肥大回避。AskUserQuestion で確定（推奨採用） |
| 保持期間 | **1ヶ月（31日）** | 「ストレージを確認し可能なら1ヶ月」→ Releases なら .git を汚さず可能と判断し1ヶ月で確定 |
| エンジン起動 | **起動状況を判定し、未起動なら自動起動を試行** | 「起動していなければ起動できるか？」→ 可。失敗時は非致命スキップ |
| runner 統合 | generate_pages 周辺に音声ステップを追加。**非致命** | AivisSpeech 不在でも digest 公開を止めない |

### 原稿の構成と文量配分（編集長へのルール）

| ブロック | 内容 | 字数目安 | 尺(325字/分) |
|---|---|---|---|
| オープニング | 「今日はどんな日か」＝横断トレンドサマリーを核に全体像を語る | 約450字 | 約1.4分 |
| カテゴリ巡回 ×7 | 各カテゴリのトップ記事(`hero`)を主役に、その日の動きを語る。主要な関連記事に軽く言及 | 約300字 ×7 = 2,100字 | 約6.5分 |
| クロージング | 全体を束ねる締め・明日への視点 | 約120字 | 約0.4分 |
| **合計** | | **約2,670字** | **約8〜9分** |

> 尺は速度で微調整可（300字/分→+1割、350字/分→−1割）。AivisSpeech の `speedScale=1.0` 実速度はモデル依存で**未実測**のため、実装初期に1ブロック合成して実測し文量を再較正する。

---

## 2. AivisSpeech 仕様（調査済み・根拠付き）

エンジンは **VOICEVOX 互換 HTTP API**（Style-Bert-VITS2 ベース）。**ローカル動作のみ**（クラウド送信なし）。
出典: AivisSpeech 公式 https://aivis-project.com/ / Engine GitHub https://github.com/Aivis-Project/AivisSpeech-Engine / Web API 解説 https://zenn.dev/it_ks/articles/aivisspeech_api_intro

- **デフォルトポート: `10101`**（`http://127.0.0.1:10101`。VOICEVOX の 50021 とは別）
- **音声モデル UUID（指定・固定）: `47e53151-a378-46f3-abee-ce13aa07feb1`**（声質：バリトンボイスの朗らかおじさん）
- **合成フロー**:
  1. `POST /audio_query?text={text}&speaker={styleId}` → query JSON を得る
  2. query JSON のパラメータ（speedScale 等）を編集
  3. `POST /synthesis?speaker={styleId}`（body = 編集後 query JSON、`Content-Type: application/json`）→ **wav** バイト列を取得
- **UUID→style id 解決（ハードコード禁止）**: `/synthesis` の `speaker` は**整数の style id**。`GET /speakers` のレスポンス（話者配列）で各話者は `speaker_uuid` と `styles[]`（各要素に整数 `id`）を持つ。指定 UUID に一致する話者の `styles[0].id`（既定スタイル）を**実行時に解決**して使う。モデル更新で id がズレるため**起動時に1回引いてキャッシュ**する。
- **読み補正辞書**: 誤読は `/user_dict` 系 API で登録可能。または原稿生成/正規化時にカナ寄せ。
- **出力は wav** → mp3 化に **ffmpeg** が必要（`ffmpeg -i in.wav -ac 1 -b:a 80k out.mp3`）。
- **ライセンス**: エンジン本体 LGPL-3.0。**音声モデル(AIVM)はモデルごとに個別規約**（商用可否・クレジット表記要否が異なる）。指定モデルの規約を https://hub.aivis-project.com/ と https://aivis-project.com/terms/ で**実装前に確認**（§7 要確認事項）。

### 推奨パラメータ（初期値案・実装初期に1ブロック合成して耳で確定）

| パラメータ | 既定 | 初期値案 | 意図 |
|---|---|---|---|
| `speedScale` | 1.0 | **1.0** | 標準。実測後に尺へ合わせ 0.95〜1.05 で微調整 |
| `pitchScale` | 0.0 | **0.0** | バリトンの地声を活かす（上げない） |
| `intonationScale` | 1.0 | **1.1** | 抑揚を豊かにし親しみやすさを出す |
| `tempoDynamicsScale`（AivisSpeech独自） | 1.0 | **1.2** | テンポに緩急を付け単調さを避ける。VOICEVOX には無いパラメータ |
| `volumeScale` | 1.0 | **1.0** | 標準 |
| `pauseLengthScale` | 1.0 | **1.1** | 文間の“間”をやや長く取り聞き取りやすく |
| `prePhonemeLength` / `postPhonemeLength` | 既定 | 既定 | 必要時のみ無音調整 |
| `outputStereo` | true | **false（モノラル）** | mp3 容量節約 |

> 値はすべて `/audio_query` が返した query JSON のキーに対して上書きする（`tempoDynamicsScale` は AivisSpeech 独自で query JSON に含まれる。含まれない場合はキー追加で渡す）。**2026-06-16 試聴後の確定値は `intonationScale=1.1` / `tempoDynamicsScale=1.2` / `pauseLengthScale=1.1`**。短文合成の wav 生成は `tests/test_tts_aivis_client.py` で確認する。通し音声の耳確認と尺微調整は当日原稿生成後に実施する。

### 長文合成の注意
- 長文一括は読み崩れ/タイムアウト要因。**句点・段落単位で分割**して逐次 `/audio_query`→`/synthesis` し、wav を結合する。
- wikilink `[[エンティティ]]`・Markdown 記号・URL は読み上げ前に plain text へ正規化（§3 build_script.py が担当）。

### エンジン自動起動
- ポート 10101 を `socket.connect_ex(("127.0.0.1", 10101))` で LISTEN 確認（**netstat ポーリング禁止**、純 Python。グローバル CLAUDE.md 準拠）。
- 未 LISTEN なら AivisSpeech エンジン実行ファイルを起動（Windows は `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)`）。
  - 実行ファイルパスは環境変数 `AIVISSPEECH_ENGINE_EXE` を最優先。未設定時は既知の既定パスを順に探索（**要確認**: 実機の実インストールパス。AivisSpeech GUI アプリ本体 or 同梱 `AivisSpeech-Engine\run.exe`。Codex は実機で `Get-ChildItem` 等で実パスを特定し、既定探索リストに追記すること）。
- 起動後 `GET /speakers` が 200 を返すまでポーリング（タイムアウト 60s、間隔 1s）。
- タイムアウト/起動失敗 → **非致命スキップ**（例外を握りつぶさず WARN ログを出し、音声ステップ全体を skip。digest 公開は継続）。
- バッチ前から起動していた AivisSpeech は終了しない。未起動だったため本ステップが起動した AivisSpeech だけ、TTS ステップ終了時に best-effort で終了する。ready 待ち失敗・合成失敗・時間超過でも、今回起動したプロセスは `finally` 相当で cleanup する。

---

## 3. アーキテクチャ / ファイル構成（新規・変更）

```
News-Grasp/
├─ tools/
│  └─ tts/                         # 新規パッケージ
│     ├─ __init__.py
│     ├─ proc.py                   # subprocess 境界（ffmpeg/ffprobe/エンジン起動を CREATE_NO_WINDOW で集約）
│     ├─ aivis_client.py           # AivisSpeech クライアント（health-check/auto-start/resolve_style_id/synthesize）
│     ├─ build_script.py           # 編集長の原稿 md → 正規化済みプレーン原稿 + 字数/カテゴリ バリデート
│     ├─ synthesize_daily.py       # 正規化原稿 → 逐次合成 → wav 結合 → ffmpeg mp3
│     └─ publish_audio.py          # gh release upload + 31日ローテーション + <audio> 埋め込み URL 供給
├─ prompts/
│  ├─ newsroom-editor-system.md    # 改修: 朗読原稿生成ルールを追記
│  ├─ index-template.html          # 改修: Home 最上部に <audio>
│  └─ summary-template.html        # 改修: 横断サマリーページに <audio>
├─ scripts/ops/
│  └─ news-grasp-runner.ps1        # 改修: 音声ステップ（非致命）を追加
├─ digest/Summary/
│  └─ {date}-audio-script.md       # 新規生成物（編集長が出力する朗読原稿）
├─ build/tts/                      # 新規・中間生成物（git 管理外。.gitignore 追記）
│  ├─ {date}.script.txt            # 正規化済みプレーン原稿
│  └─ {date}.mp3                   # アップロード前の mp3
├─ tests/
│  ├─ fixtures/tts/                # 契約テスト用 fixture（原稿 md・HTML・asset 一覧 JSON 等）
│  ├─ test_tts_build_script.py
│  ├─ test_tts_proc_boundary.py
│  ├─ test_tts_rotation.py
│  ├─ test_tts_audio_embed.py
│  └─ test_tts_aivis_client.py     # 統合（エンジン未起動なら skip）
└─ docs/
   └─ handoff_2026-06-16_daily-tts.md  # 本書
```

### データフロー（1日分）
```
[既存] harvest→dedup→reporters×7→editor
            └→ editor が digest/{cat}/{date}-{cat}.md（hero含む）+ digest/Summary/{date}.md を生成
[改修] editor が追加で digest/Summary/{date}-audio-script.md（朗読原稿 約2,670字）も生成
[新規・音声ステップ（非致命）]
  build_script.py     : {date}-audio-script.md を読む → 7カテゴリ言及・字数レンジを validate（gate）
                        → wikilink/記号/URL/ブランド読み 正規化 → build/tts/{date}.script.txt
  synthesize_daily.py : aivis_client で health-check→（未起動なら auto-start）→ style id 解決
                        → 段落分割で逐次合成 → wav 結合 → ffmpeg → build/tts/{date}.mp3
                        → ffprobe で尺を検査（6〜10分レンジ外なら WARN）
  publish_audio.py    : gh release（タグ audio-daily）に {date}.mp3 を upload
                        → 31日より古い asset を削除（ローテーション）
                        → 最新 mp3 の Releases URL/日付を generate_pages へ供給
[既存] quality gates → generate_pages（Jinja2, latest_audio_url を注入）→ commit/push
```

> **配置順の注意**: `<audio>` 埋め込みは「最新 mp3 の URL を generate_pages の出力へ反映」する必要がある。実装は次のどちらか（Codex 判断、シンプル優先）:
> - **(A) 採用**: generate_pages の Jinja2 コンテキストに「最新音声URL/日付」を渡し、テンプレートが描画する（SSG 一貫）。音声ステップを generate_pages **前**に置き、mp3 URL を確定してコンテキストへ供給。
> - (B): generate_pages 後に publish_audio.py が生成済み HTML のプレースホルダを置換。
> フロー＝「build_script→synthesize→Releases upload→URL確定→generate_pages（URL注入）→commit」。runner の挿入位置は §4 ToDo 13 を参照。

---

## 4. 実装 ToDo（decision-complete・上から順に Red→Green）

> 各 ToDo は「対象ファイル / 変更内容 / 入力 / 期待出力 / 検証コマンド / 期待 exit code / 失敗時の戻り先」を持つ。TDD：契約テスト（Red）を先に書き、最小実装で Green。

### ToDo 1. パッケージ雛形と subprocess 境界 `tools/tts/proc.py`
- 対象ファイル: `tools/tts/__init__.py`（空）, `tools/tts/proc.py`
- 変更内容: `quiet_run(args, *, timeout=None, cwd=None, check=True)` を公開。Windows のみ `creationflags |= subprocess.CREATE_NO_WINDOW`。`capture_output=True, text=True, encoding="utf-8", errors="replace"` を強制。エンジン自動起動用に `spawn_detached(args)`（Popen + CREATE_NO_WINDOW）も公開。
- 入力: コマンド配列（ffmpeg/ffprobe/エンジン exe）。
- 期待出力: `CompletedProcess`（stdout/stderr 取得済み）。
- 再発防止(Lv2 境界集約): 以後の ffmpeg/ffprobe/エンジン起動は**必ず本モジュール経由**。直接 `subprocess.run` を他モジュールに書かない。
- 検証コマンド: `cd News-Grasp && python -m pytest tests/test_tts_proc_boundary.py -q`
- 期待 exit code: 0
- 失敗時の戻り先: 本 ToDo（境界の signature 修正）

### ToDo 2. 契約テスト `tests/test_tts_proc_boundary.py`（Red 先行）
- 変更内容: `proc.quiet_run` が **Windows で `CREATE_NO_WINDOW` を必ず付与**することを `unittest.mock.patch("subprocess.run")` で検証（実コマンドは起動しない）。`sys.platform` を分岐し、非 Windows では付与しないことも検証。
- 期待出力: PASS。
- 検証/exit code: 上と同じ。0。

### ToDo 3. AivisSpeech クライアント `tools/tts/aivis_client.py`
- 対象ファイル: `tools/tts/aivis_client.py`
- 変更内容:
  - 定数: `BASE = "http://127.0.0.1:10101"`、`PORT = 10101`、`MODEL_UUID = "47e53151-a378-46f3-abee-ce13aa07feb1"`、`DEFAULT_PARAMS`（§2 表の初期値）。
  - `is_engine_up() -> bool`: `socket.connect_ex(("127.0.0.1", PORT)) == 0`。
  - `ensure_engine(timeout=60) -> bool`: up なら True。down なら `AIVISSPEECH_ENGINE_EXE`(env)→既定探索でパス決定→`proc.spawn_detached` で起動→`/speakers` を 200 までポーリング。起動不可/タイムアウトは False（例外を投げず WARN）。
  - `engine_started_by_this_process() -> bool` / `shutdown_started_engine(timeout=10) -> bool`: この Python プロセスが自動起動した AivisSpeech だけを終了する。既存起動エンジンはユーザー利用中の可能性があるため終了しない。
  - `resolve_style_id(uuid=MODEL_UUID) -> int`: `GET /speakers` を引き、Hub のモデル UUID `47e...` に対応する話者 UUID `561e...`（実機 API 確認メモ参照）の `styles[0].id` を返す。無ければ `RuntimeError`。**プロセス内 1 回キャッシュ**。
  - `synthesize(text, style_id, params=DEFAULT_PARAMS) -> bytes`: `POST /audio_query?text=&speaker=` → query JSON に params を上書き（`tempoDynamicsScale` 含む。`outputStereo=False`）→ `POST /synthesis?speaker=`（body=query JSON, `application/json`）→ wav バイト列。
  - HTTP は標準 `urllib.request`（依存追加しない）。各呼び出しにタイムアウト明示。
- 検証コマンド: `cd News-Grasp && python -m pytest tests/test_tts_aivis_client.py -q`
- 期待 exit code: 0（エンジン未起動時は該当テストが **skip**）
- 失敗時の戻り先: 本 ToDo

### ToDo 4. 統合テスト `tests/test_tts_aivis_client.py`（skip マーカ付き）
- 変更内容: `is_engine_up()` が False なら `pytest.skip`。True のとき (a)`resolve_style_id` が整数を返す (b)`synthesize("こんにちは。", id)` が非空 wav（RIFF ヘッダで始まる）を返す、を検証。
- 検証/exit code: 上と同じ。

### ToDo 5. 原稿バリデート＋正規化 `tools/tts/build_script.py`
- 対象ファイル: `tools/tts/build_script.py`
- 入力: `digest/Summary/{date}-audio-script.md`（編集長生成）。**無ければ非致命スキップ**（WARN）。
- 変更内容:
  - `load_script(date) -> str`: md 本文を読む（frontmatter 除去）。
  - `validate_script(text) -> list[str]`: 検査し**問題リスト**を返す（空＝合格）。①7カテゴリ全てに言及（カテゴリ表記/日本語別名の出現。判定語は `Watchlist.md` のカテゴリ＋別名で定数化）②実効字数（空白・記号除く）が **2,500〜3,000字**。
  - `normalize_for_tts(text) -> str`: wikilink `[[X]]`→`X`、音声原稿タイトル行除去、見出し/箇条書き記号除去、URL 除去、連続空行圧縮、英略語の最低限カナ寄せ（任意・辞書定数）。
  - `build(date) -> Path|None`: load→validate（問題あれば WARN しつつ None でスキップ）→normalize→`build/tts/{date}.script.txt`。
- 期待出力: `build/tts/{date}.script.txt`。
- 検証コマンド: `cd News-Grasp && python -m pytest tests/test_tts_build_script.py -q`
- 期待 exit code: 0
- 失敗時の戻り先: 本 ToDo

### ToDo 6. 契約テスト `tests/test_tts_build_script.py`（Red 先行）
- fixture: `tests/fixtures/tts/good-audio-script.md`（7カテゴリ言及・約2,670字）, `missing-category.md`（6カテゴリ）, `too-short.md`（約1,000字）。
- 検証: ①good→`validate_script` 問題ゼロ ②missing-category→「カテゴリ不足」を含む ③too-short→「字数不足」を含む ④`normalize_for_tts` が `[[エンティティ]]`→`エンティティ`・URL 除去。
- 意図: 「全7カテゴリ網羅」「尺レンジ」を**原稿段階の gate**として locked-in（Lv4）。
- 検証/exit code: 上と同じ。

### ToDo 7. 音声生成 `tools/tts/synthesize_daily.py`
- 対象ファイル: `tools/tts/synthesize_daily.py`
- 入力: `build/tts/{date}.script.txt`。
- 変更内容:
  - `aivis_client.ensure_engine()` が False → **非致命スキップ**（WARN, return None）。
  - `ensure_engine()` が自動起動したエンジンは、成功/失敗/ready 待ち失敗のいずれでも `shutdown_started_engine()` を呼び、日次バッチ後に不要サーバーを残さない。
  - `resolve_style_id()` → 原稿を句点/段落で分割 → 各チャンク `synthesize` → wav を結合（`wave` 標準ライブラリでフレーム連結）。
  - `proc.quiet_run(["ffmpeg","-y","-i",tmp_wav,"-ac","1","-b:a","80k",mp3])` で `build/tts/{date}.mp3`。変換時間を `time.monotonic()` で計測し `[tts] ffmpeg mp3 conversion: {秒}s` としてログに出す。
  - `proc.quiet_run(["ffprobe",...])` で尺取得 → **6〜10分レンジ外は WARN**（致命にしない）。
  - 戻り値: `Path`（成功）/ `None`（スキップ）。
- 期待出力: `build/tts/{date}.mp3`（モノラル / 約80kbps）。
- 検証: エンジン無し環境で「エンジン不在 → None・例外なし」をテスト（`is_engine_up` を mock で False）。エンジン有り環境では手動 E2E（§5）。
- 期待 exit code: 0
- 失敗時の戻り先: 本 ToDo or ToDo 3

### ToDo 8. 公開＋ローテーション＋URL供給 `tools/tts/publish_audio.py`
- 対象ファイル: `tools/tts/publish_audio.py`
- 前提: リポ public（GitHub Pages 稼働）→ Releases アセットは匿名 DL 可。`gh` CLI 認証済み。
- 変更内容:
  - `RELEASE_TAG = "audio-daily"`。タグ無しなら `gh release create audio-daily --title "Daily Audio" --notes "日次朗読音声の保管"`（**冪等**にコードで存在確認）。
  - `upload(date, mp3_path)`: `gh release upload audio-daily {date}.mp3 --clobber`。
  - `audio_url(date) -> str`: `https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/{date}.mp3`（※ owner/repo は `gh repo view --json owner,name` で実値確認。GitHub username は `HIDEPON-UMG`）。
  - `rotate(today, keep_days=31)`: `gh release view audio-daily --json assets` → ファイル名日付が `today-31日` より古い asset を `gh release delete-asset audio-daily {name} -y`。
  - `latest_audio_for_pages(date) -> dict`: generate_pages へ渡す `{"latest_audio_url":..., "latest_audio_date":...}` を返す。
  - すべて非致命（gh 失敗時 WARN で digest 公開継続）。
- 検証コマンド: `cd News-Grasp && python -m pytest tests/test_tts_rotation.py tests/test_tts_audio_embed.py -q`
- 期待 exit code: 0
- 失敗時の戻り先: 本 ToDo

### ToDo 9. 契約テスト `tests/test_tts_rotation.py`（Red 先行）
- 変更内容: `rotate` を `gh` 呼び出し mock の asset 一覧（例: 35日分 `YYYY-MM-DD.mp3`）に対して実行 → **31日より古い asset 名のみ削除対象**、当日・直近31日は残る、を assert。`today` は引数注入（`Date.now()`/`new Date()` 不使用）。
- 意図: 「1ヶ月保持・git 履歴非肥大」を locked-in（Lv4）。
- 検証/exit code: 上と同じ。

### ToDo 10. 契約テスト `tests/test_tts_audio_embed.py`（Red 先行）
- 変更内容: 「Jinja2 テンプレート（index/summary）に `latest_audio_url` を渡してレンダリングすると、`<audio preload="none" controls>` と正しい Releases URL が **Home・横断サマリー両方**の出力に含まれる」を検証。`latest_audio_url` 未指定（音声無し日）では audio ブロックが出ないことも検証。
- 意図: 「配置2箇所に正URLの audio が必ず入る／無い日は出さない」を locked-in（Lv4）。
- 検証/exit code: 上と同じ。

### ToDo 11. 編集長プロンプト改修 `prompts/newsroom-editor-system.md`
- 対象ファイル: `prompts/newsroom-editor-system.md`
- 変更内容: 既存の横断サマリー生成に加え、**朗読原稿生成タスク**を追記。
  - 出力先: `digest/Summary/{date}-audio-script.md`（frontmatter 任意、本文は朗読原稿）。
  - 構成・字数: §1「原稿の構成と文量配分」表（オープニング約450字／カテゴリ巡回7×約300字／クロージング約120字／合計約2,670字）。
  - 文体: **親しみやすい語り口のバリトン男声で読む前提**。書き言葉でなく耳で分かる話し言葉。一文を短く、難読語/英略語はカナ補足。ブランド名を本文で読む場合は `News Grasp` ではなく `ニュース グラスプ` と書く。音声原稿タイトル行は読み上げ本文に含めない。冒頭は必ず当日の日付を述べ、「朝のニュースをお伝えします」という趣旨のセリフから入る。各カテゴリは hero（トップ記事）を主役に「今日そのカテゴリで何があったか」、主要関連記事に軽く言及。事実の羅列で終わらせず、時折あなたの短い感想を添える。
  - リスナーのペルソナ: 主なリスナーは ITコンサル、事業企画、DX/AI導入、経営・技術戦略など、事業・技術判断に関わるプロである。ニュースの読み方を教わりたい初心者ではないため、「細かな数字を覚えるより」「落ち着いて追えば」のような上から目線の助言は禁止する。提供価値は、事実の要約に加えて、リスナーが次の会話・提案・判断で使える観点を渡すこと。
  - 話し手としての親しみやすさ: 聞き手を下に見て教えることではなく、同じニュースを一緒に見ている伴走者として、驚き、違和感、共感、小さな感想を短く添えること。感想は事実より前に出しすぎず、各カテゴリやカテゴリ間の橋渡しに戦略的に置く。「これは現場側には重い話です」「地味ですが後から効きそうです」のように、ニュースの温度と話し手の個性を短く伝える。
  - 話者本人のペルソナ: 話者は外から解説する先生ではなく、リスナーと同じ立場で、ITコンサルや事業・技術判断に関わる同僚である。各ニュースを自分事として捉えたときに、どう感じ、どうするべきと考えたかを短く添える。ニュースそのものだけでなく、同僚がそのニュースにどう反応し、何を論点化するかを伝える。
  - 息継ぎ: 文末から次文の入りまでを詰めすぎない。TTS 合成では文単位のチャンク間に短い無音を挿入し、人間の息継ぎに近い間を確保する。
  - 読み方対策: AivisSpeech が漢字熟語を誤読する語は `tools/tts/build_script.py` の `PRONUNCIATION_REPLACEMENTS` に登録し、TTS 入力では読み仮名へ強制置換する。既知例は `後工程` → `あとこうてい`、`上方修正` → `じょうほうしゅうせい`。完全な未知語誤読の自動検知は難しいため、実聴で見つけた語を辞書と契約テストへ追加し、次回以降は事前対応する。
- 締め: 最後には必ず「今日の観点・考察」を置き、その日に複数カテゴリを貫いた判断軸を具体的にまとめる。ニュースの聞き方ではなく、今日のニュースから見えた構造、違和感、次に問うべき論点を短く残す。
- 禁止: 35記事の機械的羅列、URL/記号の読み上げを誘発する表記、wikilink 多用。
- 検証: 既存 editor 系テストを壊さない＋§4 ToDo 6 の good fixture を合格にできる構成と一致。

### 2026-06-17 追記: 日次バッチ完了条件の補強

- TTS は additive なので、AivisSpeech / ffmpeg / GitHub Releases の失敗は引き続き WARN の非致命扱いとし、digest 公開全体は止めない。
- ただし `build/tts/latest_audio.json` が当日音声を指している場合は、音声公開が成功した日として扱う。この場合、通常 publish 後の `tools.daily_self_heal verify-publish` は `publish-status.json` だけでなく、次をすべて確認する。
  - Releases の当日 mp3 URL が 200 を返す。
  - Home の公開 HTML に同じ `latest_audio_url` が含まれる。
  - 当日横断サマリーの公開 HTML に同じ `latest_audio_url` が含まれる。
- 上記のいずれかが欠ける場合、`verify-publish` は `public_audio_missing` などで非 0 終了し、runner は `publish_failed` として完了扱いにしない。
- 失敗時の戻り先: 本 ToDo

### ToDo 12. テンプレート改修（Home + 横断サマリー）+ generate_pages 連動
- 対象ファイル: `prompts/index-template.html`, `prompts/summary-template.html`, generate_pages 実体（`tools/` 配下。Codex が grep で特定）
- 変更内容: **Home 最上部**と**横断サマリーページ**に `<audio preload="none" controls src="{{ latest_audio_url }}">` を追加（`latest_audio_url` が真のときのみ描画）。DESIGN.md トークン（CSS 変数）で装飾（直書き禁止）。generate_pages の Jinja2 コンテキストに `latest_audio_url`/`latest_audio_date` を注入（publish_audio の `latest_audio_for_pages` から供給）。
- 検証: `tests/test_tts_audio_embed.py`（ToDo 10）＋ §5 実機 E2E。
- 失敗時の戻り先: 本 ToDo

### ToDo 13. runner 統合 `scripts/ops/news-grasp-runner.ps1`
- 対象ファイル: `scripts/ops/news-grasp-runner.ps1`
- 変更内容: editor ステージ後・generate_pages 前に音声ステップを挿入。
  - 順序: `python -m tools.tts.build_script {date}` → `python -m tools.tts.synthesize_daily {date}` → `python -m tools.tts.publish_audio {date}`（upload+rotate+URL確定）→ generate_pages（URL注入）。
  - **非致命**: 各音声ステップを `try/catch` で囲み、失敗は `Write-Warning` でログし**パイプライン全体を止めない**（generate_pages/commit は必ず継続）。終了コードで digest 公開を巻き込まない。
- 検証コマンド（dry-run）: runner dry-run/preflight で音声ステップが呼ばれ、AivisSpeech 不在でも後続が実行されること。`cd News-Grasp && python -m pytest -q`（全体回帰）。
- 期待 exit code: 0
- 失敗時の戻り先: 本 ToDo

### ToDo 14. `.gitignore` と中間生成物
- 対象ファイル: `.gitignore`
- 変更内容: `build/tts/` を git 管理外に追加（wav/mp3 を履歴に入れない）。`digest/Summary/{date}-audio-script.md` は既存 digest と同じ扱い（テキスト軽量なのでコミット可。既存運用に合わせる）。
- 検証: `git status` に wav/mp3 が出ないこと。

### ToDo 15. パラメータ実測・確定
- 変更内容: §2 表の初期値で1ブロック合成→耳で確認→確定値を §2 表に反映（更新コミット）。speedScale を尺に合わせ微調整。
- 検証: ffprobe で当日 mp3 が 6〜10分レンジ、通し試聴で読み崩れ無し。

### ToDo 16. ライセンス確認とクレジット（§7）
- 変更内容: 指定モデルの個別規約を hub.aivis-project.com で確認。商用/配信可否・クレジット要否を判断し、必要ならサイトフッタ等にクレジット。
- 検証: 規約 URL と判断結果を §7 に追記。

---

## 5. エンドツーエンド検証（実機）
1. AivisSpeech ローカル起動 → `cd News-Grasp && python -c "from tools.tts import aivis_client as a; print(a.resolve_style_id())"` で style id が整数。
2. 1文合成 → wav 再生で声質・パラメータ耳確認 → §2 表確定。
3. 当日 digest（編集長が `{date}-audio-script.md` 生成済み）→ `build_script` で全7カテゴリ・字数レンジ合格・`build/tts/{date}.script.txt` 生成。
4. `synthesize_daily` → `build/tts/{date}.mp3` → ffprobe で 6〜10分レンジ → 通し試聴。
5. `publish_audio` → Releases upload → ブラウザで `https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/{date}.mp3` が 200。
6. generate_pages → Home 最上部・横断サマリーに audio 表示 → **PC とスマホ実機**で再生・帯域確認（`preload="none"` で未再生時に取得しない）。
7. ローテーション: 32日分擬似 asset で `rotate` → 31本以内。
8. runner 統合: AivisSpeech を落として runner 実行 → 音声ステップ WARN スキップ、digest 公開完走。

---

## 6. 既知の前提・注意（Codex が踏み外さないため）
- **日本語環境/Windows 前提**: `.ps1` は UTF-8 BOM（hook 自動付与）。Python I/O は `encoding="utf-8"`。subprocess は `proc.quiet_run` 経由で `CREATE_NO_WINDOW` 強制（黒窓防止）。
- **依存追加は最小**: HTTP は標準 `urllib`、wav 結合は標準 `wave`。新規 pip 依存は原則入れない（ffmpeg/ffprobe/gh は外部 CLI 前提）。`pyproject.toml` 変更が要るなら理由明記。
- **非致命の徹底（Lv1）**: 音声ステップのどの失敗も digest 公開を止めない。例外は握って WARN。
- **URL 捏造禁止**: サイトに書く URL は実行中に 200 を確認したもののみ（News-Grasp の URL 生存ゲート方針）。本書記載の外部 URL は調査時点の実在 URL。
- **owner/repo 実値確認**: Releases URL の `HIDEPON-UMG/News-Grasp` は `gh repo view` で実 owner/name を確認してから確定。
- **push しない**: 明示指示があるまで `git push` しない。commit は safe-commit 5段ゲート経由。
- **plan v1 との差分**: 旧 plan ファイル（`.claude/plans/...lantern.md`）の「docs/{date}/audio.mp3・2本保持・日次オーバービュー配置」は**無効**。正は本書（Releases 配置・31日保持・Home+横断サマリー配置・編集長が原稿生成）。

## 7. 実装前の要確認事項（ブロッカー候補）
- [x] 指定音声モデル `47e53151-...` の**個別ライセンス**（商用/Web 配信可否・クレジット表記要否）。AivisHub 上の `阿井田 茂` はライセンス `ACML 1.0`。ACML 1.0 は禁止事項に該当しない個人・法人・非営利・営利利用を許可し、クレジット表記は任意。News-Grasp の日次ニュース朗読は不特定多数が任意入力する TTS サービスではなく編集済み原稿の配信なので、利用可・クレジット任意と判断。出典: https://hub.aivis-project.com/aivm-models/47e53151-a378-46f3-abee-ce13aa07feb1 / https://github.com/Aivis-Project/ACML/blob/master/ACML-1.0.md / https://aivis-project.com/terms/
- [x] AivisSpeech エンジン**実行ファイルの実パス**（自動起動用）。実機では `AIVISSPEECH_ENGINE_EXE` 未設定、エンジン本体は `%LOCALAPPDATA%\Programs\AivisSpeech\AivisSpeech-Engine\run.exe`、GUI 本体は `%LOCALAPPDATA%\Programs\AivisSpeech\AivisSpeech.exe`。短文TTSの実機スモークで `run.exe` は約10秒で `/speakers` 200 になり、`AivisSpeech.exe` はバッチ用途の自動起動では60秒以内に ready にならなかった。`aivis_client` は環境変数優先、未設定時は同梱 engine `run.exe` を GUI より優先して既定探索する。
- [x] News-Grasp リポジトリの**公開設定**と Releases アセットの匿名 DL 可否（`gh repo view --json visibility`）。`gh repo view --json owner,name,visibility,url` で `HIDEPON-UMG/News-Grasp` / `PUBLIC` を確認。Releases URL は `https://github.com/HIDEPON-UMG/News-Grasp/releases/download/audio-daily/{date}.mp3`。

### 実機 API 確認メモ

- AivisSpeech `/speakers` は Hub のモデル UUID `47e53151-a378-46f3-abee-ce13aa07feb1` ではなく、話者 UUID `561e4e59-3bc9-4726-9028-44a3c12a6f1d` を返す。
- 実機 `/speakers` で `阿井田 茂` の既定スタイルは `styles[0].id = 1310138976`。実装はこの style id をハードコードせず、`/speakers` から話者 UUID を解決して利用する。

---

## 8. 再発防止の設計（class of bugs を構造で封じる／チェックを増やさない）
- **UUID→style id ハードコード禁止 → 実行時解決**（Lv1: 不正状態を表現不能に）
- **subprocess は `tools/tts/proc.py` 1 モジュールに集約**＋`CREATE_NO_WINDOW`（Lv2: 境界集約。黒窓と直叩きを 1 箇所で封じる）
- **音声ステップは非致命**＝失敗が digest 公開を壊せない構造（Lv1）
- **契約テスト 4 件で不変条件を locked-in**（Lv4）: ①原稿に全7カテゴリ＆字数レンジ ②proc が CREATE_NO_WINDOW 付与 ③ローテ後は31日以内 ④audio 埋め込みが2箇所に正URL／無い日は出さない。個別 smoke 単独で完了報告しない。
