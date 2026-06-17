# News-Grasp 日次TTS音声へのジャズBGM適用 + 実配信統合 — 引き継ぎ仕様書（Codex）

- 作成日: 2026-06-18
- 作成: Claude（Opus 4.8）。本書を唯一の正とする。再質問なしで完走できる decision-complete な ToDo を含む。
- 種別: 引き継ぎ仕様書（handoff、HTML 化適用外＝編集前提ドキュメント）
- 前提資産: 既存 `docs/handoff_2026-06-16_daily-tts.md`（日次TTS音声の本体実装。本書はその「BGM 演出」追加分）

---

## 0. 上位目的と成功条件（最初に固定）

### 上位目的
News-Grasp の日次朗読音声（`build/tts/{date}.mp3` → GitHub Releases `audio-daily`）に、**真面目なニュースに合う落ち着いたジャズBGM（スタイル `cool-minor`）を敷く**。朗読を邪魔せず、機械音でなく音楽として流れ、明るすぎず「朝の出勤」のトーンにする。

### 今回スコープ（ユーザー確定）
1. 本番 BGM 素材を **`cool-minor`** に差し替える（`assets/audio/news-grasp-bgm.wav`）。
2. 日次配信音声に BGM を**実際にミックスして配信する**（現状 BGM は本番フロー未統合＝配信 mp3 に BGM が乗っていない）。
3. runner 通常公開で、自動的に **BGM 込みの mp3** が Releases に上がる状態にする。
4. safe-commit 5段ゲート通過後にコミット（push はユーザー明示まで禁止）。

### 対象外
- BGM スタイルの再選定（`cool-minor` で確定済み。他候補は §6 参照、変更は別タスク）。
- 複数話者・効果音などの追加演出。
- 過去バックログ音声の一括 BGM 化（最新運用のみ）。

### 成功条件（DoD）
- [ ] `assets/audio/news-grasp-bgm.wav` が `cool-minor`（Aマイナー ii-V-i / 128BPM / スイング）になっている（再生成コマンドで決定論的に再現可能）。
- [ ] `tools/tts/synthesize_daily.py` が、TTS 音声 wav に BGM を**ループ・控えめ音量・フェードで重ねて** `build/tts/{date}.mp3` を生成する。
- [ ] **BGM が無い/ミックス失敗でも、素の TTS mp3 で公開が継続する**（synthesize_daily は runner 上 fatal なので、BGM 失敗で exit≠0 にしてはいけない）。
- [ ] mp3 尺は既存の 6〜10 分レンジ判定を維持。
- [ ] 契約テスト（後述3件）+ 既存 tts テストが pytest で PASS。
- [ ] 実機（AivisSpeech 起動）で BGM 込み mp3 を試聴し、朗読下で `cool-minor` が馴染む音量に確定。
- [ ] safe-commit 5段ゲート通過後コミット。push はユーザー明示まで実行しない。

---

## 1. 現状（Claude 側で done。delivered）

| 項目 | 状態 |
|---|---|
| `tools/tts/generate_bgm.py` をスタイル対応にリファクタ | done。`Style`/`STYLES` 4種・`bass_mode`(walking/bossa/ostinato)・`drum_mode`(swing/bossa/sixteen)・walking 自動生成。`mix_bgm_preview`/`main` 維持 |
| 4候補生成（聴き比べ済み） | done。`major-swing`/`cool-minor`/`bossa-lounge`/`newsroom-drive`。試聴 mp3 は `build/tts/candidates/`（throwaway・コミット対象外） |
| スタイル契約テスト | done。`tests/test_tts_generate_bgm.py` 全10件 PASS（全スタイル mono/peak/決定論・マイナー候補存在・newsroom が major より低域オンセット密＝せわしさ） |
| ユーザーのスタイル選択 | done。**`cool-minor` で確定** |
| 本番 `assets/audio/news-grasp-bgm.wav` | **未差し替え**（前回の `major-swing` のまま）→ ToDo 1 |
| 配信音声への BGM 統合 | **未実装**（synthesize_daily は BGM なしで mp3 化）→ ToDo 2 |

### 残タスク = delivered − done（Codex がやる全量）
ToDo 1（差し替え）/ ToDo 2（統合）/ ToDo 3（契約テスト）/ ToDo 4（検証）/ ToDo 5（commit）。下記すべて。

---

## 2. アーキテクチャ（統合ポイントは1箇所）

```
[既存・本番] runner.ps1 §2.85 Daily TTS audio (fatal):
   tools.tts.build_script {date}     -> build/tts/{date}.script.txt
   tools.tts.synthesize_daily {date} -> build/tts/{date}.mp3   ← ★ここに BGM を統合
   tools.tts.publish_audio {date}    -> Releases audio-daily へ upload
```

- `synthesize_daily.synthesize()` の中で、AivisSpeech の wav チャンクを `combine_wavs` で 1 本の `voice.wav` にし、`convert_wav_to_mp3(voice.wav, {date}.mp3)` で mp3 化している（[synthesize_daily.py:104-144](../tools/tts/synthesize_daily.py)）。
- **この mp3 化を「BGM 込み」に差し替える**のが統合の本体。runner.ps1 は変更不要（synthesize_daily が内部で BGM を乗せる）。
- BGM 素材 = `assets/audio/news-grasp-bgm.wav`（ToDo 1 で `cool-minor` 化）。短い 1 ループ wav を朗読尺に合わせて無限ループして敷く。

### 設計上の必須制約（踏み外し厳禁）
1. **非致命フォールバック（Lv1）**: synthesize_daily は runner で **fatal**（runner.ps1 行1678-1688 で exit 1 → 公開停止）。BGM ミックスが例外/失敗したら、握りつぶして **素の `convert_wav_to_mp3` にフォールバック**し、必ず mp3 を返す。BGM 欠落で公開を止めない。
2. **循環 import 回避**: `generate_bgm.py` は `from tools.tts import proc, synthesize_daily` を import している。よって **synthesize_daily から generate_bgm を import してはいけない**。本番ミックス関数は synthesize_daily 側に新規実装する（ffmpeg フィルタは `generate_bgm.mix_bgm_preview` を参考にコピー）。`generate_bgm.mix_bgm_preview`（preview 用・voice **mp3** 入力）は残す。本番は voice **wav** 入力にして中間 mp3 を作らない。
3. **依存追加禁止**: 標準ライブラリ + ffmpeg/ffprobe(外部CLI) のみ。`News-Grasp/.venv` に numpy は無い。generate_bgm は pure python のまま。
4. **subprocess は `tools/tts/proc.py` 経由**（`proc.quiet_run`、`CREATE_NO_WINDOW`）。直接 subprocess を書かない。

---

## 3. 実装 ToDo（上から順に・decision-complete）

### ToDo 1. 本番 BGM を cool-minor に差し替え
- コマンド（`News-Grasp/` で実行。venv の python）:
  ```
  .venv/Scripts/python.exe -m tools.tts.generate_bgm --style cool-minor --out assets/audio/news-grasp-bgm.wav
  ```
- 期待出力: `{"bgm_wav": "assets\\audio\\news-grasp-bgm.wav", "style": "cool-minor"}`、exit 0。
- 検証: `ffprobe` で mono / 44100 / 16bit、尺 15.0 秒（=128BPM×8小節）。
- 失敗時の戻り先: 本 ToDo（`--style cool-minor` の綴り確認）。

### ToDo 2. synthesize_daily に BGM ミックスを統合（非致命）
- 対象: `tools/tts/synthesize_daily.py`
- 追加する定数:
  ```python
  ASSET_DIR = REPO_ROOT / "assets" / "audio"
  DEFAULT_BGM_PATH = ASSET_DIR / "news-grasp-bgm.wav"
  BGM_VOLUME_DB = -26.0  # 実機試聴で -24〜-30 の範囲で確定（ToDo 4）
  ```
- 新関数 `mix_voice_wav_with_bgm(voice_wav, bgm_wav, mp3_out, *, bgm_volume_db=BGM_VOLUME_DB) -> None`:
  - voice 尺を取得（`wave` で nframes/framerate、ffprobe 不要）。
  - ffmpeg（`proc.quiet_run`、`timeout=FFMPEG_TIMEOUT_SEC`）で、`generate_bgm.mix_bgm_preview` と同じフィルタ構成を voice **wav** 入力で実行:
    ```
    [1:a]aloop=loop=-1:size=2147483647,atrim=0:{dur},volume={bgm_volume_db}dB,
    afade=t=in:st=0:d=2,afade=t=out:st={dur-5}:d=5[bgm];
    [0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[out]
    ```
    出力は `-map [out] -ac 1 -b:a 80k`（既存 mp3 と同じ）。入力は `-i voice_wav -stream_loop -1 -i bgm_wav`。
- `synthesize()` の mp3 化箇所（現 `elapsed = convert_wav_to_mp3(wav_path, mp3_path)`）を改修:
  ```python
  try:
      if DEFAULT_BGM_PATH.exists():
          mix_voice_wav_with_bgm(wav_path, DEFAULT_BGM_PATH, mp3_path)
      else:
          _warn(f"BGM not found, plain voice mp3: {DEFAULT_BGM_PATH}")
          convert_wav_to_mp3(wav_path, mp3_path)
  except Exception as exc:
      _warn(f"BGM mix failed, fallback to plain voice mp3: {exc}")
      convert_wav_to_mp3(wav_path, mp3_path)  # ★公開を止めない
  ```
- 期待出力: `build/tts/{date}.mp3`（BGM 込み・モノラル・80kbps）。BGM 不在/失敗時も素 mp3 が必ず生成される。
- 検証: ToDo 3 の契約テスト + ToDo 4 の実機。
- 失敗時の戻り先: 本 ToDo（フィルタ文字列・フォールバック分岐）。

### ToDo 3. 契約テスト（Lv4・Red 先行）
- 対象: `tests/test_tts_synthesize_daily.py`（無ければ新規）
- テスト3件:
  1. `mix_voice_wav_with_bgm` が **bounded ffmpeg** で呼ばれる: `proc.quiet_run` を mock し、引数に `volume=-26.0dB`・`alimiter`・`-ac 1`・`amix` を含み、`kwargs["timeout"]==FFMPEG_TIMEOUT_SEC` を assert。
  2. **BGM 欠落フォールバック**: `DEFAULT_BGM_PATH.exists()` を False に mock（または存在しないパス）→ `synthesize` 相当の mp3 化分岐が `convert_wav_to_mp3`（素）を呼び、**例外を投げない**ことを assert。
  3. **ミックス失敗フォールバック**: `mix_voice_wav_with_bgm` を例外を投げる mock にして、mp3 化分岐が `convert_wav_to_mp3` にフォールバックし `synthesize` が None で落ちない（=公開を止めない）ことを assert。
- 意図: 「BGM 演出は本番公開を壊せない」を構造で locked-in（Lv1 を Lv4 テストで固定）。
- 検証コマンド: `.venv/Scripts/python.exe -m pytest tests/test_tts_synthesize_daily.py tests/test_tts_generate_bgm.py -q` → exit 0。

### ToDo 4. 検証（実機・非致命確認）
1. `.venv/Scripts/python.exe -m pytest -q`（全体回帰）→ PASS。
2. AivisSpeech 起動 → `.venv/Scripts/python.exe -m tools.tts.synthesize_daily {当日}` → `build/tts/{date}.mp3` が BGM 込みで生成、`ffprobe` 尺 6〜10 分。
3. **実機試聴**: 生成 mp3 を再生し、朗読下で `cool-minor` が馴染む音量か確認。耳で `BGM_VOLUME_DB` を -24〜-30 で確定し、確定値を定数に反映。
4. **非致命確認**: `assets/audio/news-grasp-bgm.wav` を一時退避 → `synthesize_daily` 実行 → **素 mp3 が生成され exit 0**（WARN ログのみ）を確認 → wav を戻す。
- 失敗時の戻り先: ToDo 2。

### ToDo 5. コミット（safe-commit 5段ゲート）
- 対象（path 指定で stage、無関係な作業ツリーを巻き込まない）:
  - `assets/audio/news-grasp-bgm.wav`（cool-minor）
  - `tools/tts/synthesize_daily.py`
  - `tools/tts/generate_bgm.py`
  - `tests/test_tts_generate_bgm.py` / `tests/test_tts_synthesize_daily.py`
  - `docs/handoff_2026-06-18_bgm-jazz-integration.md`（本書）
- **コミット対象外**: `build/tts/candidates/*`（throwaway 試聴素材。`build/tts/` は既存 .gitignore 確認。除外されていなければ stage しない）。
- safe-commit 5段ゲート（個人情報 / 脆弱性 / 機密 / DESIGN.md / スモーク）を通過後にコミット。コマンド末尾に `# CODEX_SAFE_COMMIT_CONFIRMED`。
- **push はユーザー明示まで禁止**。明示時のみ `# CODEX_PUSH_CONFIRMED`。push 後は remote HEAD 一致 + Actions/Pages 反映を確認。

---

## 4. Acceptance Matrix（要件を矮小化しない）

| ユーザー原文要求 | 期待最終状態 | 対象レイヤー | 検証 | 未達時 |
|---|---|---|---|---|
| 機械音でなく音楽・ジャズ・明るすぎず朝のせわしさ | cool-minor（マイナー基調・落ち着き＋推進力）を本番採用 | generate_bgm / assets wav | ToDo1 + 既存テスト | 別候補へ（§6） |
| cool-minor で本番化 | `news-grasp-bgm.wav` = cool-minor | assets wav | ffprobe + pytest | ToDo1 |
| 実配信への BGM 統合 | 日次 mp3 に BGM が乗り Releases 配信 | synthesize_daily | 実機 mp3 試聴 + ffprobe | ToDo2 |
| 公開を壊さない（暗黙・runner fatal 由来） | BGM 失敗で素 mp3 フォールバック・exit0 | synthesize_daily | 契約テスト2,3 + ToDo4-4 | ToDo2 |
| コミット | 5段ゲート通過後 commit・未 push | git | safe-commit | ToDo5 |

---

## 5. 既知の前提・注意（Codex が踏み外さないため）
- **synthesize_daily は runner 上 fatal**。BGM ミックス失敗で exit≠0 にしない（§2 制約1）。
- **循環 import 回避**: synthesize_daily から generate_bgm を import しない（§2 制約2）。
- **numpy 本番禁止**: `.venv` に numpy 無し。generate_bgm は pure python 維持（§2 制約3）。
- **決定論**: generate_bgm は seed 固定で同一 bytes。テスト `test_generate_bgm_is_deterministic_for_same_seed` を壊さない。
- **mix_bgm_preview は残す**（preview 用・voice mp3 入力）。本番は voice wav 入力の別関数。
- **日本語環境**: `.ps1` は UTF-8 BOM（hook 自動）。Python I/O は `encoding="utf-8"`。subprocess は `proc.quiet_run` 経由。
- **push しない**: 明示指示まで `git push` 禁止。commit は safe-commit 経由。

## 6. スタイルを変えたくなった場合（参考・本タスク対象外）
- 4スタイルは `tools/tts/generate_bgm.py` の `STYLES` に定義。`--style <name>` で wav を再生成し `assets/audio/news-grasp-bgm.wav` を上書きするだけで差し替え可能。
- 候補: `cool-minor`(確定) / `bossa-lounge` / `newsroom-drive` / `major-swing`(明るい・参考)。
- テンポ/楽器/音量の微調整は `STYLES` の各 `Style`（bpm/swing_ratio/brightness）と `_render_*` を変更。変更時は `tests/test_tts_generate_bgm.py` の契約（mono/peak/決定論/マイナー/せわしさ）を維持。
- 「本物の生音（GM 音源）」路線（fluidsynth + SoundFont）は**未検証**（fluidsynth 未インストール・手元 `gm.dls` の DLS 対応可否未確認）。採用するなら先に実機検証が必要。
