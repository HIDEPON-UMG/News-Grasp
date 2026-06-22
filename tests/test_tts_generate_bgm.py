from __future__ import annotations

import math
import wave
from unittest.mock import patch

from tools.tts import generate_bgm


# ---- WAV 読み出し & 簡易フィルタ（numpy なし・標準ライブラリのみ） ----

def _read_samples(wav_path):
    with wave.open(str(wav_path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == generate_bgm.SAMPLE_RATE
        frames = reader.readframes(reader.getnframes())
    return [
        int.from_bytes(frames[i:i + 2], "little", signed=True) / 32767
        for i in range(0, len(frames), 2)
    ]


def _peak(samples):
    return max(abs(value) for value in samples)


def _rms(values):
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def _lowpass(samples, sample_rate, cutoff_hz):
    """1 次 IIR ローパス。ベース等の低域成分を抽出する。"""
    alpha = min(2.0 * math.pi * cutoff_hz / sample_rate, 1.0)
    state = 0.0
    out = []
    for value in samples:
        state += alpha * (value - state)
        out.append(state)
    return out


def _highpass(samples, sample_rate, cutoff_hz):
    """ローパスとの差でハイパス。ライドや倍音など高域成分を抽出する。"""
    low = _lowpass(samples, sample_rate, cutoff_hz)
    return [value - low_value for value, low_value in zip(samples, low)]


def _mean(values):
    return sum(values) / len(values) if values else 0.0


# ---- 出力仕様（モノラル・控えめ・決定論） ----

def test_generate_bgm_writes_quiet_original_mono_wav(tmp_path):
    out = tmp_path / "news-grasp-bgm.wav"

    generate_bgm.write_bgm_wav(out, duration_seconds=2.0, seed=1234)

    with wave.open(str(out), "rb") as reader:
        assert math.isclose(reader.getnframes() / reader.getframerate(), 2.0, abs_tol=0.01)
    peak = _peak(_read_samples(out))
    # BGM は TTS の下に -30dB で敷くため控えめ。だが無音でもない。
    assert 0.03 <= peak <= 0.18


def test_generate_bgm_is_deterministic_for_same_seed(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"

    generate_bgm.write_bgm_wav(first, duration_seconds=1.0, seed=42)
    generate_bgm.write_bgm_wav(second, duration_seconds=1.0, seed=42)

    assert first.read_bytes() == second.read_bytes()


# ---- 「眠くなる」対策＝推進力：ウォーキングベースが拍に乗る ----

def test_bgm_has_walking_bass_pulsing_on_beats(tmp_path):
    out = tmp_path / "bass.wav"
    generate_bgm.write_bgm_wav(out, duration_seconds=8.0, seed=7)

    samples = _read_samples(out)
    sr = generate_bgm.SAMPLE_RATE
    low = _lowpass(samples, sr, 200.0)
    beat = int(sr * 60 / generate_bgm.JAZZ_BPM)
    window = int(sr * 0.05)

    on_beat = [
        _rms(low[start:start + window])
        for start in range(0, len(low) - beat, beat)
    ]
    between = [
        _rms(low[start + beat // 2:start + beat // 2 + window])
        for start in range(0, len(low) - beat, beat)
    ]

    # 低域（ベース）が存在し、拍頭でアタックする＝ウォーキングの推進力
    assert _mean(on_beat) > 0.01
    assert _mean(on_beat) > _mean(between) * 1.10


# ---- ジャズのスイング：ライドが拍の裏（スイング位置）で鳴る ----

def test_bgm_swing_ride_present_on_offbeats(tmp_path):
    out = tmp_path / "swing.wav"
    generate_bgm.write_bgm_wav(out, duration_seconds=8.0, seed=7)

    samples = _read_samples(out)
    sr = generate_bgm.SAMPLE_RATE
    high = _highpass(samples, sr, 3500.0)
    beat = int(sr * 60 / generate_bgm.JAZZ_BPM)
    window = int(sr * 0.04)

    swing = [
        _rms(high[int(start + beat * generate_bgm.SWING_RATIO):int(start + beat * generate_bgm.SWING_RATIO) + window])
        for start in range(0, len(high) - beat, beat)
    ]
    silent_gap = [
        _rms(high[int(start + beat * 0.30):int(start + beat * 0.30) + window])
        for start in range(0, len(high) - beat, beat)
    ]

    # スイング位置（拍の 2/3 付近）にライドのエネルギーがあり、隙間より明確に強い
    assert _mean(swing) > 0.003
    assert _mean(swing) > _mean(silent_gap)


# ---- 「機械音声」対策：純正弦波でなく倍音・金属音を持つ ----

def test_bgm_is_not_pure_sine_has_harmonic_content(tmp_path):
    out = tmp_path / "rich.wav"
    generate_bgm.write_bgm_wav(out, duration_seconds=4.0, seed=7)

    samples = _read_samples(out)
    sr = generate_bgm.SAMPLE_RATE
    total = _rms(samples)
    high = _rms(_highpass(samples, sr, 3000.0))

    # 単一正弦波の低音単音なら高域はほぼ 0。倍音/ライドの金属音で高域が一定以上ある。
    assert total > 0.0
    assert high > total * 0.04


# ---- ジャズのハーモニー構造（循環コード + ウォーキングライン + シンコペ） ----

def test_bgm_has_jazz_trio_structure():
    assert generate_bgm.JAZZ_BPM == 120
    assert generate_bgm.BEATS_PER_BAR == 4
    assert generate_bgm.BARS == 8

    names = [chord.name for chord in generate_bgm.CHORD_PROGRESSION]
    assert len(names) == 8
    # ジャズの 7th コードによる循環（I-VI-ii-V 系ターンアラウンド）
    for required in ("Cmaj7", "A7", "Dm7", "G7"):
        assert required in names

    # ウォーキングベース＝1 小節 4 拍 × 8 小節 = 32 音
    assert len(generate_bgm.WALKING_BASS) == 32

    # コンピングは裏拍（非整数オフセット）を含むシンコペーション
    assert any(
        offset % 1.0 != 0.0
        for pattern in generate_bgm.COMP_PATTERN
        for offset in pattern
    )

    # スイングは 3 連符フィール（拍の中央より後ろ）
    assert 0.55 <= generate_bgm.SWING_RATIO <= 0.70


# ---- 複数スタイル候補（明るすぎ回避＋朝のせわしさ） ----

def test_all_styles_render_mono_quiet_and_deterministic(tmp_path):
    for name in generate_bgm.STYLES:
        first = tmp_path / f"{name}-1.wav"
        second = tmp_path / f"{name}-2.wav"
        generate_bgm.write_bgm_wav(first, duration_seconds=2.0, seed=5, style=name)
        generate_bgm.write_bgm_wav(second, duration_seconds=2.0, seed=5, style=name)
        peak = _peak(_read_samples(first))
        assert 0.03 <= peak <= 0.18, f"{name} peak={peak}"
        assert first.read_bytes() == second.read_bytes(), f"{name} not deterministic"


def test_styles_offer_dark_minor_and_busy_candidates():
    # ユーザー指摘「明るすぎる」への対策＝マイナー基調の落ち着いた候補を用意
    for required in ("cool-minor", "bossa-lounge", "newsroom-drive", "major-swing"):
        assert required in generate_bgm.STYLES

    for name, style in generate_bgm.STYLES.items():
        assert style.bpm > 0, name
        assert len(style.progression) >= 4, name
        assert 0.5 <= style.swing_ratio <= 0.70, name

    # 落ち着いた候補はマイナー/ハーフディミニッシュを含む（短3度＝明るすぎない）
    for name in ("cool-minor", "bossa-lounge", "newsroom-drive"):
        chord_names = [chord.name for chord in generate_bgm.STYLES[name].progression]
        assert any(("m7" in n or "m9" in n or "m7b5" in n) for n in chord_names), name
        # 暗さのためエレピの明度を下げている
        assert generate_bgm.STYLES[name].brightness < 1.0, name

    # 「朝のせわしさ」候補は 16 ビート刻み
    assert generate_bgm.STYLES["newsroom-drive"].drum_mode == "sixteen"


def _low_band_onsets(samples, sample_rate):
    """低域（ベース/キック）の音の立ち上がり回数。リズムの密度＝せわしさの実体。"""
    low = _lowpass(samples, sample_rate, 250.0)
    width = int(sample_rate * 0.012)
    envelope = [_rms(low[i:i + width]) for i in range(0, len(low) - width, width)]
    if not envelope:
        return 0
    floor = 0.22 * max(envelope)
    onsets = 0
    for i in range(1, len(envelope)):
        if envelope[i] > floor and envelope[i - 1] <= floor:
            onsets += 1
    return onsets


def test_newsroom_drive_is_busier_than_major_swing(tmp_path):
    swing_path = tmp_path / "swing.wav"
    drive_path = tmp_path / "drive.wav"
    generate_bgm.write_bgm_wav(swing_path, duration_seconds=4.0, seed=5, style="major-swing")
    generate_bgm.write_bgm_wav(drive_path, duration_seconds=4.0, seed=5, style="newsroom-drive")

    sr = generate_bgm.SAMPLE_RATE
    busy = _low_band_onsets(_read_samples(drive_path), sr)
    relaxed = _low_band_onsets(_read_samples(swing_path), sr)
    # 8 分オスティナートベース＋キックで低域の音数（推進感）が明確に多い＝せわしい
    assert busy > relaxed * 1.3, f"drive={busy} swing={relaxed}"


# ---- 「同僚同士の会話」用オフィス系ローファイ候補（会話を邪魔しない控えめさ） ----

def test_office_lofi_style_exists_with_mellow_relaxed_profile():
    assert "office-lofi" in generate_bgm.STYLES
    style = generate_bgm.STYLES["office-lofi"]

    # lofi らしい遅めのテンポ
    assert 65 <= style.bpm <= 95

    # 明るすぎない（エレピの明度を下げている）
    assert style.brightness < 1.0

    # lofi はあまりスイングしない（均等〜わずかなハネ）
    assert 0.50 <= style.swing_ratio <= 0.56

    # maj7/m9 系の落ち着いた進行（明るすぎない）
    chord_names = [chord.name for chord in style.progression]
    assert any(("maj7" in n or "maj9" in n or "m9" in n) for n in chord_names)


def test_office_lofi_is_calmer_than_newsroom_and_cool_minor(tmp_path):
    office_path = tmp_path / "office.wav"
    newsroom_path = tmp_path / "newsroom.wav"
    cool_path = tmp_path / "cool.wav"
    generate_bgm.write_bgm_wav(office_path, duration_seconds=4.0, seed=5, style="office-lofi")
    generate_bgm.write_bgm_wav(newsroom_path, duration_seconds=4.0, seed=5, style="newsroom-drive")
    generate_bgm.write_bgm_wav(cool_path, duration_seconds=4.0, seed=5, style="cool-minor")

    sr = generate_bgm.SAMPLE_RATE
    office_onsets = _low_band_onsets(_read_samples(office_path), sr)
    newsroom_onsets = _low_band_onsets(_read_samples(newsroom_path), sr)
    cool_onsets = _low_band_onsets(_read_samples(cool_path), sr)

    # 会話の邪魔をしない＝低域の音数（せわしさ）が他候補より明確に少ない
    assert office_onsets < newsroom_onsets, f"office={office_onsets} newsroom={newsroom_onsets}"
    assert office_onsets < cool_onsets, f"office={office_onsets} cool={cool_onsets}"


def test_office_lofi_still_has_harmonic_content_not_pure_sine(tmp_path):
    out = tmp_path / "office-harmonic.wav"
    generate_bgm.write_bgm_wav(out, duration_seconds=4.0, seed=7, style="office-lofi")

    samples = _read_samples(out)
    sr = generate_bgm.SAMPLE_RATE
    total = _rms(samples)
    high = _rms(_highpass(samples, sr, 3000.0))

    assert total > 0.0
    assert high > total * 0.04


# ---- 旋律を参考BGM(オフィスの日常.mp3)寄りに調整: 跳躍を抑えた控えめな旋律 ----
# 参考曲はスペクトログラム解析で「高密度なリズムテクスチャが前面、明確に跳躍するメロディ線は無い」
# と判明（自己相関ピッチ追跡は信頼性が低く不採用、視覚解析の所見を採用）。
# office-lofi だけ専用メロディに分岐させ、他スタイル(ジャズ系の倍音/コード進行/ベース/ドラム)は不変。

def test_office_lofi_melody_intervals_are_stepwise_not_jazz_leaps():
    # ジャズ系スタイルは1オクターブ近い跳躍(最大19半音)。office-lofi は順次進行中心に抑える。
    intervals = [interval for _, interval in generate_bgm.OFFICE_LOFI_MELODY_STARTS]
    assert intervals, "OFFICE_LOFI_MELODY_STARTS が空"
    assert max(abs(i) for i in intervals) <= 7


def test_office_lofi_melody_is_sparser_than_jazz_melody_pattern():
    # 参考曲はメロディが控えめ。ジャズ用の固定メロディ(12イベント)より少ないイベント数にする。
    assert len(generate_bgm.OFFICE_LOFI_MELODY_STARTS) < len(generate_bgm.JAZZ_MELODY_STARTS)


def test_office_lofi_melody_other_styles_untouched():
    # 既存スタイルの曲調(倍音構造/コード進行/ベース/ドラム)は今回の旋律修正で変えない。
    assert generate_bgm.STYLES["cool-minor"].progression == generate_bgm.COOL_MINOR_PROGRESSION
    assert generate_bgm.STYLES["cool-minor"].bass_mode == "walking"
    assert generate_bgm.STYLES["cool-minor"].drum_mode == "swing"
    assert generate_bgm.STYLES["office-lofi"].progression == generate_bgm.OFFICE_LOFI_PROGRESSION
    assert generate_bgm.STYLES["office-lofi"].bass_mode == "lofi"
    assert generate_bgm.STYLES["office-lofi"].drum_mode == "lofi"


# ---- 実測BPM反映の対談用候補(参考曲「オフィスの日常.mp3」の実測テンポを再現) ----
# 2026-06-21: 自己相関+ピーク間隔(IOI)ヒストグラムで実測。全帯域ピーク間隔は129/123/126/133近辺
# (186点中165点)に集中し、表テンポは約128BPM(低域だけ64BPM付近に分散するのはキックの倍音オクターブ)。

def test_office_daily_style_exists_with_measured_bpm_no_melody_no_swing():
    assert "office-daily" in generate_bgm.STYLES
    style = generate_bgm.STYLES["office-daily"]

    # 自己相関+IOIヒストグラムでの実測BPM
    assert style.bpm == 128
    # 元音源はストレートなテクスチャでスイングしない
    assert style.swing_ratio == 0.50
    assert style.bass_mode == "office"
    assert style.drum_mode == "office"
    # 「明確に跳躍するメロディ線は無い」所見を反映しメロディ無し
    assert generate_bgm._melody(0.5, style, [0.0] * 8) == 0.0


def _high_band_onsets(samples, sample_rate, cutoff_hz=4000.0):
    """高域(クリック/テクスチャ)の立ち上がり回数。office-daily の密度の実体。"""
    high = _highpass(samples, sample_rate, cutoff_hz)
    width = int(sample_rate * 0.006)
    envelope = [_rms(high[i:i + width]) for i in range(0, len(high) - width, width)]
    if not envelope:
        return 0
    floor = 0.25 * max(envelope)
    onsets = 0
    for i in range(1, len(envelope)):
        if envelope[i] > floor and envelope[i - 1] <= floor:
            onsets += 1
    return onsets


def test_office_daily_has_denser_high_band_texture_than_office_lofi(tmp_path):
    daily_path = tmp_path / "daily.wav"
    lofi_path = tmp_path / "lofi.wav"
    generate_bgm.write_bgm_wav(daily_path, duration_seconds=4.0, seed=5, style="office-daily")
    generate_bgm.write_bgm_wav(lofi_path, duration_seconds=4.0, seed=5, style="office-lofi")

    sr = generate_bgm.SAMPLE_RATE
    daily_high_onsets = _high_band_onsets(_read_samples(daily_path), sr)
    lofi_high_onsets = _high_band_onsets(_read_samples(lofi_path), sr)

    # 元音源「高密度なリズムテクスチャ」を反映し、office-lofi よりクリック密度が明確に高い
    assert daily_high_onsets > lofi_high_onsets, f"daily={daily_high_onsets} lofi={lofi_high_onsets}"


def test_office_daily_has_harmonic_content_not_pure_sine(tmp_path):
    out = tmp_path / "daily-harmonic.wav"
    generate_bgm.write_bgm_wav(out, duration_seconds=4.0, seed=7, style="office-daily")

    samples = _read_samples(out)
    sr = generate_bgm.SAMPLE_RATE
    total = _rms(samples)
    high = _rms(_highpass(samples, sr, 3000.0))

    assert total > 0.0
    assert high > total * 0.04


# ---- ミックス（TTS 下敷き）の ffmpeg 契約は維持 ----

def test_mix_preview_uses_bounded_ffmpeg_with_low_bgm_volume(tmp_path):
    voice = tmp_path / "voice.mp3"
    bgm = tmp_path / "bgm.wav"
    out = tmp_path / "preview.mp3"
    voice.write_bytes(b"voice")
    bgm.write_bytes(b"bgm")

    with patch.object(generate_bgm.synthesize_daily, "probe_duration_seconds", return_value=120.0), \
        patch.object(generate_bgm.proc, "quiet_run") as quiet_run:
        generate_bgm.mix_bgm_preview(voice, bgm, out, bgm_volume_db=-30.0)

    quiet_run.assert_called_once()
    args = quiet_run.call_args.args[0]
    assert args[:4] == ["ffmpeg", "-y", "-i", voice]
    assert "-filter_complex" in args
    assert "volume=-30.0dB" in args[args.index("-filter_complex") + 1]
    assert quiet_run.call_args.kwargs["timeout"] == generate_bgm.FFMPEG_TIMEOUT_SEC
