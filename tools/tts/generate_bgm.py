from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import random
import wave
from pathlib import Path

from tools.tts import proc, synthesize_daily


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = REPO_ROOT / "assets" / "audio"
BUILD_DIR = REPO_ROOT / "build" / "tts"
DEFAULT_BGM_PATH = ASSET_DIR / "news-grasp-bgm.wav"
DEFAULT_DURATION_SECONDS = 15.0
SAMPLE_RATE = 44_100
MAX_INT16 = 32767
TARGET_PEAK = 0.12
JAZZ_BPM = 120
SWING_RATIO = 2.0 / 3.0
BEATS_PER_BAR = 4
BARS = 8
FFMPEG_TIMEOUT_SEC = 180


@dataclass(frozen=True)
class Chord:
    name: str
    midis: tuple[int, ...]


@dataclass(frozen=True)
class Style:
    name: str
    bpm: int
    progression: tuple[Chord, ...]
    swing_ratio: float = SWING_RATIO
    brightness: float = 1.0
    bass_mode: str = "walking"
    drum_mode: str = "swing"


CHORD_PROGRESSION: tuple[Chord, ...] = (
    Chord("Cmaj7", (60, 64, 67, 71)),
    Chord("A7", (57, 61, 64, 67)),
    Chord("Dm7", (62, 65, 69, 72)),
    Chord("G7", (55, 59, 62, 65)),
    Chord("Em7", (59, 62, 67, 71)),
    Chord("A7", (57, 61, 64, 67)),
    Chord("Dm7", (62, 65, 69, 72)),
    Chord("G7", (55, 59, 62, 65)),
)

COOL_MINOR_PROGRESSION: tuple[Chord, ...] = (
    Chord("Am9", (57, 60, 64, 67)),
    Chord("Fmaj7", (53, 57, 60, 64)),
    Chord("Bm7b5", (59, 62, 65, 69)),
    Chord("E7b9", (52, 56, 62, 67)),
    Chord("Am9", (57, 60, 64, 67)),
    Chord("Dm9", (50, 57, 60, 64)),
    Chord("Bm7b5", (59, 62, 65, 69)),
    Chord("E7b9", (52, 56, 62, 67)),
)

BOSSA_PROGRESSION: tuple[Chord, ...] = (
    Chord("Dm9", (50, 57, 60, 64)),
    Chord("G13", (55, 59, 64, 69)),
    Chord("Cmaj9", (48, 55, 59, 64)),
    Chord("Am9", (57, 60, 64, 67)),
    Chord("Dm9", (50, 57, 60, 64)),
    Chord("G13", (55, 59, 64, 69)),
    Chord("Em7", (52, 59, 62, 67)),
    Chord("A7b9", (57, 61, 67, 70)),
)

OFFICE_LOFI_PROGRESSION: tuple[Chord, ...] = (
    Chord("Cmaj9", (48, 55, 59, 64)),
    Chord("Am9", (57, 60, 64, 67)),
    Chord("Fmaj7", (53, 57, 60, 64)),
    Chord("G6", (55, 59, 62, 67)),
)

NEWSROOM_PROGRESSION: tuple[Chord, ...] = (
    Chord("Fm9", (53, 56, 60, 63)),
    Chord("Dbmaj7", (49, 53, 56, 60)),
    Chord("Gm7b5", (55, 58, 61, 65)),
    Chord("C7b9", (48, 52, 58, 61)),
    Chord("Fm9", (53, 56, 60, 63)),
    Chord("Bbm9", (58, 61, 65, 68)),
    Chord("Gm7b5", (55, 58, 61, 65)),
    Chord("C7b9", (48, 52, 58, 61)),
)

STYLES: dict[str, Style] = {
    "major-swing": Style(
        name="major-swing",
        bpm=JAZZ_BPM,
        progression=CHORD_PROGRESSION,
        brightness=1.0,
        bass_mode="walking",
        drum_mode="swing",
    ),
    "office-daily": Style(
        name="office-daily",
        bpm=128,
        progression=OFFICE_LOFI_PROGRESSION,
        swing_ratio=0.50,
        brightness=0.55,
        bass_mode="office",
        drum_mode="office",
    ),
    "cool-minor": Style(
        name="cool-minor",
        bpm=128,
        progression=COOL_MINOR_PROGRESSION,
        brightness=0.78,
        bass_mode="walking",
        drum_mode="swing",
    ),
    "bossa-lounge": Style(
        name="bossa-lounge",
        bpm=112,
        progression=BOSSA_PROGRESSION,
        swing_ratio=0.58,
        brightness=0.82,
        bass_mode="bossa",
        drum_mode="bossa",
    ),
    "newsroom-drive": Style(
        name="newsroom-drive",
        bpm=136,
        progression=NEWSROOM_PROGRESSION,
        swing_ratio=0.60,
        brightness=0.72,
        bass_mode="ostinato",
        drum_mode="sixteen",
    ),
    "office-lofi": Style(
        name="office-lofi",
        bpm=82,
        progression=OFFICE_LOFI_PROGRESSION,
        swing_ratio=0.52,
        brightness=0.62,
        bass_mode="lofi",
        drum_mode="lofi",
    ),
}

COMP_PATTERN: tuple[tuple[float, ...], ...] = (
    (0.67, 1.50, 2.67, 3.50),
    (0.50, 1.67, 2.50, 3.67),
)

WALKING_BASS: tuple[int, ...] = tuple(
    chord.midis[0] - 12 + offset
    for chord in CHORD_PROGRESSION
    for offset in (0, 3, 5, 7)
)


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _envelope(t: float, duration_seconds: float) -> float:
    fade = min(1.6, max(duration_seconds / 6.0, 0.08))
    return min(_smoothstep(t / fade), _smoothstep((duration_seconds - t) / fade), 1.0)


def _midi_freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _pluck_env(dt: float, duration: float, *, decay: float = 5.0) -> float:
    if dt < 0.0 or dt > duration:
        return 0.0
    attack = min(duration * 0.18, 0.035)
    if dt < attack:
        return _smoothstep(dt / attack)
    return math.exp(-decay * (dt - attack))


def _form_duration(style: Style) -> float:
    return len(style.progression) * BEATS_PER_BAR * 60.0 / style.bpm


def _bar_and_chord(beat: float, style: Style) -> tuple[int, Chord, float]:
    form_beats = len(style.progression) * BEATS_PER_BAR
    local = beat % form_beats
    bar_index = int(local // BEATS_PER_BAR)
    return bar_index, style.progression[bar_index], local - bar_index * BEATS_PER_BAR


def _walking_bass(beat: float, style: Style, phase: float) -> float:
    bar_index, chord, beat_in_bar = _bar_and_chord(beat, style)
    step = min(int(beat_in_bar), 3)
    dur, decay = 0.45, 7.5
    if style.bass_mode == "ostinato":
        midi = chord.midis[0] - 12 + (0, 0, 3, 5)[step]
    elif style.bass_mode == "bossa":
        midi = chord.midis[0] - 12 + (0, 7, 5, 7)[step]
    elif style.bass_mode == "lofi":
        if step % 2 == 1:
            return 0.0  # 2/4拍目は休符。低密度＝会話の邪魔をしない
        midi = chord.midis[0] - 12 + (0, 0, 7, 7)[step]
    elif style.bass_mode == "office":
        if step != 0:
            return 0.0  # 小節頭だけ。対談の邪魔をしない最低密度のロングトーン
        midi = chord.midis[0] - 12
        dur = BEATS_PER_BAR * 60.0 / style.bpm - 0.05
        decay = 1.1
    else:
        next_chord = style.progression[(bar_index + 1) % len(style.progression)]
        target = next_chord.midis[0] - 12
        root = chord.midis[0] - 12
        midi = (root, root + 4, root + 7, target - 1)[step]
    dt_beats = beat_in_bar - step
    dt = dt_beats * 60.0 / style.bpm
    env = _pluck_env(dt, dur, decay=decay)
    if env <= 0.0:
        return 0.0
    freq = _midi_freq(midi)
    return (
        math.sin(2.0 * math.pi * freq * dt + phase) * 0.050
        + math.sin(2.0 * math.pi * freq * 2.0 * dt + phase * 0.37) * 0.012
    ) * env


def _comping(beat: float, style: Style, phases: list[float]) -> float:
    bar_index, chord, beat_in_bar = _bar_and_chord(beat, style)
    freqs = tuple(_midi_freq(midi) for midi in chord.midis)
    pattern = COMP_PATTERN[bar_index % len(COMP_PATTERN)]
    value = 0.0
    for index, start in enumerate(pattern):
        dt_beats = beat_in_bar - start
        if 0.0 <= dt_beats <= 0.9:
            dt = dt_beats * 60.0 / style.bpm
            env = _pluck_env(dt, 0.42, decay=6.2)
            value += sum(
                math.sin(2.0 * math.pi * freq * dt + phases[(bar_index + index) % len(phases)]) * amp
                for freq, amp in zip(freqs, (0.016, 0.014, 0.012, 0.009))
            ) * env * style.brightness
    return value


def _ride(beat: float, style: Style, phase: float) -> float:
    beat_floor = math.floor(beat)
    beat_pos = beat - beat_floor
    if style.drum_mode == "sixteen":
        starts = (0.0, 0.25, 0.50, 0.75)
    elif style.drum_mode == "bossa":
        starts = (0.0, 0.50)
    elif style.drum_mode == "lofi":
        starts = (style.swing_ratio,)  # 裏拍 1 点のみ。控えめなチル感
    else:
        starts = (0.0, style.swing_ratio)
    value = 0.0
    for start in starts:
        dt_beats = beat_pos - start
        if 0.0 <= dt_beats <= 0.10:
            dt = dt_beats * 60.0 / style.bpm
            env = math.exp(-34.0 * dt)
            shimmer = (
                math.sin(2.0 * math.pi * 1760.0 * dt + phase)
                + math.sin(2.0 * math.pi * 2349.32 * dt + phase * 1.7)
                + math.sin(2.0 * math.pi * 3135.96 * dt + phase * 0.4)
            ) * 0.010
            value += shimmer * env
    return value


OFFICE_TEXTURE_PARTIALS: tuple[float, ...] = (5180.0, 6730.0, 8310.0, 9870.0, 11460.0)


def _office_texture(beat: float, style: Style, phase: float) -> float:
    """16分刻みの高域クリック群。状態を持たない非整数partial合成でタイピング/紙音っぽい質感を近似する。"""
    beat_floor = math.floor(beat)
    beat_pos = beat - beat_floor
    starts = (0.0, 0.25, 0.50, 0.75)
    value = 0.0
    for index, start in enumerate(starts):
        dt_beats = beat_pos - start
        if 0.0 <= dt_beats <= 0.045:
            dt = dt_beats * 60.0 / style.bpm
            env = math.exp(-95.0 * dt)
            click = sum(
                math.sin(2.0 * math.pi * f * dt + phase * (k + 1) * 0.71 + index * 1.3)
                for k, f in enumerate(OFFICE_TEXTURE_PARTIALS)
            ) / len(OFFICE_TEXTURE_PARTIALS)
            value += click * env * 0.010
    return value


JAZZ_MELODY_STARTS: tuple[tuple[float, int], ...] = (
    (0.67, 12),
    (1.67, 16),
    (2.67, 14),
    (3.34, 12),
    (8.67, 17),
    (9.67, 14),
    (10.67, 12),
    (11.50, 9),
    (16.67, 16),
    (17.67, 19),
    (18.67, 17),
    (19.50, 16),
)

# 参考BGM(オフィスの日常.mp3)のスペクトログラム解析所見: 高密度なリズムテクスチャが前面で、
# 明確に跳躍するメロディ線は無い。office-lofi だけ順次進行中心・低頻度・ロングトーンの専用メロディにする。
# 2026-06-21 自己相関+IOIヒストグラムで実測BPM再分析: 全帯域ピーク間隔は129/123/126/133近辺に集中
# (186点中165点)し、約128BPMが実テンポ。低域(<180Hz)だけは64BPM付近にも分散するが、これは
# キックが2拍に1回しか踏まれない倍音(オクターブ下)で、表テンポは128BPM側が正。office-daily は
# この実測BPM=128で「高密度テクスチャ・メロディ無し」をそのまま再現する専用スタイル。
OFFICE_LOFI_MELODY_STARTS: tuple[tuple[float, int], ...] = (
    (1.50, 4),
    (5.50, 2),
    (9.50, 0),
    (13.50, 5),
)


def _melody(beat: float, style: Style, phases: list[float]) -> float:
    form_beats = len(style.progression) * BEATS_PER_BAR
    local = beat % form_beats
    if style.bass_mode == "lofi":
        starts = OFFICE_LOFI_MELODY_STARTS
        note_dur, note_decay = 0.95, 2.0
    elif style.bass_mode == "office":
        starts = ()  # 元音源に跳躍メロディ線が無い所見を反映し、メロディ無し
        note_dur, note_decay = 0.95, 2.0
    else:
        starts = JAZZ_MELODY_STARTS
        note_dur, note_decay = 0.28, 5.8
    note_dur_beats = note_dur * style.bpm / 60.0
    value = 0.0
    base = style.progression[int(local // BEATS_PER_BAR)].midis[0]
    for index, (start, interval) in enumerate(starts):
        dt_beats = local - start
        if 0.0 <= dt_beats <= note_dur_beats:
            dt = dt_beats * 60.0 / style.bpm
            env = _pluck_env(dt, note_dur, decay=note_decay)
            freq = _midi_freq(base + interval)
            phase = phases[(index + 3) % len(phases)]
            value += (
                math.sin(2.0 * math.pi * freq * dt + phase) * 0.022
                + math.sin(2.0 * math.pi * freq * 2.0 * dt + phase * 0.37) * 0.005
            ) * env * style.brightness
    return value


def _sample(t: float, duration_seconds: float, style: Style, phases: list[float]) -> float:
    beat = t * style.bpm / 60.0
    air = math.sin(2.0 * math.pi * 0.17 * t + phases[6]) * 0.003
    texture = _office_texture(beat, style, phases[6]) if style.drum_mode == "office" else _ride(beat, style, phases[6])
    value = (
        _walking_bass(beat, style, phases[0])
        + _comping(beat, style, phases)
        + texture
        + _melody(beat, style, phases)
        + air
    )
    return value * _envelope(t, duration_seconds)


def _resolve_style(name: str | Style) -> Style:
    if isinstance(name, Style):
        return name
    try:
        return STYLES[name]
    except KeyError as exc:
        raise ValueError(f"unknown BGM style: {name}") from exc


def synthesize_bgm_samples(
    *,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    sample_rate: int = SAMPLE_RATE,
    seed: int = 20260617,
    style: str | Style = "major-swing",
) -> list[int]:
    if duration_seconds <= 0.0:
        raise ValueError("duration_seconds must be positive")
    selected = _resolve_style(style)
    rng = random.Random(seed + sum(ord(ch) for ch in selected.name))
    phases = [rng.random() * 2.0 * math.pi for _ in range(8)]
    count = int(round(duration_seconds * sample_rate))
    floats = [_sample(i / sample_rate, duration_seconds, selected, phases) for i in range(count)]
    peak = max((abs(value) for value in floats), default=0.0)
    scale = TARGET_PEAK / peak if peak > 0.0 else 1.0
    return [
        int(max(min(value * scale, 0.95), -0.95) * MAX_INT16)
        for value in floats
    ]


def write_bgm_wav(
    out_path: Path,
    *,
    duration_seconds: float | None = None,
    seed: int = 20260617,
    style: str | Style = "major-swing",
) -> Path:
    selected = _resolve_style(style)
    actual_duration = duration_seconds if duration_seconds is not None else _form_duration(selected)
    samples = synthesize_bgm_samples(duration_seconds=actual_duration, seed=seed, style=selected)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        frames = b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples)
        writer.writeframes(frames)
    return out_path


def mix_bgm_preview(
    voice_mp3: Path,
    bgm_wav: Path,
    out_mp3: Path,
    *,
    bgm_volume_db: float = -30.0,
) -> Path:
    duration = synthesize_daily.probe_duration_seconds(voice_mp3)
    if duration is None or duration <= 0:
        raise RuntimeError(f"voice duration could not be probed: {voice_mp3}")
    fade_out_start = max(duration - 5.0, 0.0)
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2147483647,atrim=0:{duration:.3f},"
        f"volume={bgm_volume_db:.1f}dB,"
        f"afade=t=in:st=0:d=2,"
        f"afade=t=out:st={fade_out_start:.3f}:d=5[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0,"
        "alimiter=limit=0.95[out]"
    )
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    proc.quiet_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            voice_mp3,
            "-stream_loop",
            "-1",
            "-i",
            bgm_wav,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-ac",
            "1",
            "-b:a",
            "80k",
            out_mp3,
        ],
        timeout=FFMPEG_TIMEOUT_SEC,
    )
    return out_mp3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="News-Grasp用の控えめなオリジナルBGMを生成します。")
    parser.add_argument("--style", choices=sorted(STYLES), default="major-swing")
    parser.add_argument("--out", type=Path, default=DEFAULT_BGM_PATH)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--seed", type=int, default=20260617)
    parser.add_argument("--preview-voice", type=Path)
    parser.add_argument("--preview-out", type=Path)
    parser.add_argument("--bgm-volume-db", type=float, default=-30.0)
    args = parser.parse_args(argv)

    bgm = write_bgm_wav(args.out, duration_seconds=args.duration, seed=args.seed, style=args.style)
    result: dict[str, str] = {"bgm_wav": str(bgm), "style": args.style}
    if args.preview_voice:
        preview_out = args.preview_out or (BUILD_DIR / f"{args.preview_voice.stem}-bgm-preview.mp3")
        result["preview_mp3"] = str(
            mix_bgm_preview(args.preview_voice, bgm, preview_out, bgm_volume_db=args.bgm_volume_db)
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
