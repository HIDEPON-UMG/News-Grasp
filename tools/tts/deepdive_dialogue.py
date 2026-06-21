from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
import tempfile
import time
from pathlib import Path

from tools.tts import aivis_client, build_script, synthesize_daily


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = REPO_ROOT / "build" / "tts" / "deepdive"
DEFAULT_BGM_PATH = REPO_ROOT / "build" / "office-daily-bgm-standalone.mp3"
BGM_VOLUME_DB = 1.5
MIN_DIALOGUE_CHARS = 1200
MAX_DIALOGUE_CHARS = 2600
MIN_SECONDS = 4 * 60
MAX_SECONDS = 9 * 60
MAX_SYNTHESIS_SECONDS = 18 * 60
# 対談は朗読より聞き手の処理時間が要るため、セリフ境界に息継ぎ相当の間を置く。
INTER_TURN_SILENCE_SECONDS = 0.46


@dataclass(frozen=True)
class Role:
    label: str
    model_uuid: str
    params: dict[str, object]


@dataclass(frozen=True)
class DialogueTurn:
    role_key: str
    text: str


ROLES: dict[str, Role] = {
    "senior": Role(
        label="先輩",
        model_uuid=aivis_client.MODEL_UUID,
        params={
            **aivis_client.DEFAULT_PARAMS,
            "speedScale": 0.94,
            "pitchScale": 0.10,
            "intonationScale": 1.22,
            "tempoDynamicsScale": 1.20,
            "pauseLengthScale": 1.36,
        },
    ),
    "junior": Role(
        label="若手",
        model_uuid="59f96896-64d2-4378-830a-4d5feb3d81aa",
        params={
            **aivis_client.DEFAULT_PARAMS,
            "speedScale": 1.0,
            "pitchScale": 0.10,
            "intonationScale": 1.24,
            "tempoDynamicsScale": 1.20,
            "pauseLengthScale": 1.3,
        },
    ),
}

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
_TURN_RE = re.compile(r"^(若手|先輩)[:：]\s*(.+?)\s*$")
_ROLE_LABELS = {"若手": "junior", "先輩": "senior"}


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def parse_dialogue(markdown: str) -> list[DialogueTurn]:
    text = _FRONTMATTER_RE.sub("", markdown)
    turns: list[DialogueTurn] = []
    for line in text.splitlines():
        match = _TURN_RE.match(line.strip())
        if not match:
            continue
        role_key = _ROLE_LABELS[match.group(1)]
        turns.append(DialogueTurn(role_key=role_key, text=match.group(2).strip()))
    return turns


def validate_dialogue(turns: list[DialogueTurn]) -> list[str]:
    issues: list[str] = []
    role_keys = {turn.role_key for turn in turns}
    for role_key, role in ROLES.items():
        if role_key not in role_keys:
            issues.append(f"役割不足: {role.label}")
    if len(turns) < 8:
        issues.append(f"セリフ数不足: {len(turns)}件 (必要: 8件以上)")
    char_count = build_script.effective_char_count("\n".join(turn.text for turn in turns))
    if char_count < MIN_DIALOGUE_CHARS:
        issues.append(f"字数不足: {char_count}字 (必要: {MIN_DIALOGUE_CHARS}〜{MAX_DIALOGUE_CHARS}字)")
    elif char_count > MAX_DIALOGUE_CHARS:
        issues.append(f"字数超過: {char_count}字 (必要: {MIN_DIALOGUE_CHARS}〜{MAX_DIALOGUE_CHARS}字)")
    return issues


def normalize_turn_text(text: str) -> str:
    normalized = build_script.normalize_for_tts(text)
    return normalized.strip()


def mix_voice_wav_with_bgm(voice_wav: Path, bgm_path: Path, mp3_out: Path) -> None:
    duration = synthesize_daily._wav_duration_seconds(voice_wav)
    fade_out_start = max(duration - 5.0, 0.0)
    filter_complex = (
        f"[1:a]volume={BGM_VOLUME_DB:.1f}dB,"
        f"atrim=0:{duration:.3f},"
        "afade=t=in:st=0:d=2,"
        f"afade=t=out:st={fade_out_start:.3f}:d=5[bgm];"
        "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
        "alimiter=limit=0.95[out]"
    )
    mp3_out.parent.mkdir(parents=True, exist_ok=True)
    synthesize_daily.proc.quiet_run(
        [
            "ffmpeg",
            "-y",
            "-i",
            voice_wav,
            "-stream_loop",
            "-1",
            "-i",
            bgm_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-ac",
            "1",
            "-b:a",
            "80k",
            mp3_out,
        ],
        timeout=synthesize_daily.FFMPEG_TIMEOUT_SEC,
    )


def convert_voice_wav_to_delivery_mp3(wav_path: Path, mp3_path: Path) -> float:
    start = time.monotonic()
    if not DEFAULT_BGM_PATH.exists():
        _warn(f"DeepDive dialogue BGM not found, plain voice mp3: {DEFAULT_BGM_PATH}")
        return synthesize_daily.convert_wav_to_mp3(wav_path, mp3_path)
    try:
        mix_voice_wav_with_bgm(wav_path, DEFAULT_BGM_PATH, mp3_path)
        return time.monotonic() - start
    except Exception as exc:
        _warn(f"DeepDive dialogue BGM mix failed, fallback to plain voice mp3: {exc}")
        return synthesize_daily.convert_wav_to_mp3(wav_path, mp3_path)


def synthesize_dialogue(script_path: Path, *, out_name: str | None = None) -> Path | None:
    markdown = script_path.read_text(encoding="utf-8")
    turns = parse_dialogue(markdown)
    issues = validate_dialogue(turns)
    if issues:
        for issue in issues:
            _warn(issue)
        return None
    if not aivis_client.ensure_engine():
        if aivis_client.engine_started_by_this_process():
            aivis_client.shutdown_started_engine()
        return None

    try:
        started_at = time.monotonic()
        wavs: list[bytes] = []
        style_id_by_role: dict[str, int] = {}
        for turn in turns:
            if time.monotonic() - started_at > MAX_SYNTHESIS_SECONDS:
                _warn(f"DeepDive dialogue synthesis time budget exceeded: {MAX_SYNTHESIS_SECONDS}s")
                return None
            role = ROLES[turn.role_key]
            style_id = style_id_by_role.get(turn.role_key)
            if style_id is None:
                style_id = aivis_client.resolve_style_id(role.model_uuid)
                style_id_by_role[turn.role_key] = style_id
            wavs.append(aivis_client.synthesize(normalize_turn_text(turn.text), style_id, role.params))

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        stem = out_name or script_path.stem
        mp3_path = BUILD_DIR / f"{stem}.mp3"
        with tempfile.TemporaryDirectory(prefix="news-grasp-deepdive-dialogue-") as tmp:
            wav_path = Path(tmp) / f"{stem}.wav"
            synthesize_daily.combine_wavs(wavs, wav_path, silence_seconds=INTER_TURN_SILENCE_SECONDS)
            elapsed = convert_voice_wav_to_delivery_mp3(wav_path, mp3_path)
            print(f"[tts] DeepDive dialogue mp3 conversion: {elapsed:.2f}s")
        duration = synthesize_daily.probe_duration_seconds(mp3_path)
        if duration is not None and not (MIN_SECONDS <= duration <= MAX_SECONDS):
            _warn(f"DeepDive dialogue duration out of sample range: {duration:.1f}s")
        print(f"[tts] DeepDive dialogue mp3 built: {mp3_path}")
        return mp3_path
    except Exception as exc:
        _warn(f"DeepDive dialogue synthesis failed: {exc}")
        return None
    finally:
        if aivis_client.engine_started_by_this_process():
            aivis_client.shutdown_started_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DeepDive 解説対談台本を AivisSpeech mp3 にします。")
    parser.add_argument("script", type=Path, help="対談台本 Markdown")
    parser.add_argument("--out-name", help="build/tts/deepdive 配下の出力ファイル名 stem")
    args = parser.parse_args(argv)
    return 0 if synthesize_dialogue(args.script, out_name=args.out_name) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
