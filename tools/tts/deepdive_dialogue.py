from __future__ import annotations

import argparse
from dataclasses import dataclass
from collections import Counter
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
BGM_EQ_FILTER = "highpass=f=110,equalizer=f=170:t=q:w=0.9:g=-4.0"
# 字数は価値IDを満たした後の音声適性だけを見る。固定文で1600字を埋める誘因を除く。
MIN_DIALOGUE_CHARS = 800
# 6分想定の品質調整は短すぎる場合だけ行う。上限は長尺化を抑えるためではなく、
# 生成暴走や貼り込み事故を止める安全弁として広めに置く。
MAX_DIALOGUE_CHARS = 3600
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
    value_id: str = ""
    evidence_id: str = ""
    support_id: str = ""


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
_DUPLICATE_CHECK_RE = re.compile(r"[\s\u3000、。，．・…「」『』（）()\[\]【】!！?？:：;；,.\-—–_#>*`~|/\\]")
_MIN_DUPLICATE_CHARS = 18
_VALUE_MARKER_RE = re.compile(
    r"<!--\s*value:([a-z0-9_]+)\s+evidence:([^\s]+)"
    r"(?:\s+support:([^\s]+))?\s*-->"
)
_SOURCE_FIELD_RE = re.compile(r'^source:\s*["\']?([^"\'\r\n]+)', re.MULTILINE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
REQUIRED_VALUE_IDS = (
    "current_signal",
    "evidence",
    "causal_chain",
    "counterevidence_or_limit",
    "change_over_time",
    "decision_implication",
    "next_action",
)
LEGACY_FILLER_PHRASES = (
    "つまり、過去回は背景知識ではなく、今回の判断項目を作る材料になるんですね",
    "二つの過去回を並べると、今回の記事で見るべき粒度が上がりますね",
    "ここまでをつなぐと、若手側からも一つ指摘できそうです",
    "数字の大小ではなく、業務OSとして定着する条件を見抜けるかが今回の肝だ",
    "ニュースを行動に翻訳するなら、導入ツール名ではなく、委任、検収、再利用の設計",
)
LEGACY_VALUE_SCAFFOLDS = (
    "起点はここです",
    "この二点を分けると、今日固有の変化が見えます",
    "数字、主体、時点を混ぜずに読む必要があります",
    "原因と結果を同じ事実として扱わないことが重要です",
    "ここから先は記事の根拠だけでは確定できないため、仮説として分離します",
    "同じ説明の再掲ではなく、前提、競争軸、判断対象のどれが移ったかを確認します",
    "顧客ごとに前提条件、影響対象、撤回条件を明示して比較します",
    "最後に、記事で未確定の点を担当者と期限付きで確認し、判断前提を更新してください",
)
MAX_SEGMENT_SIMILARITY = 0.58
MAX_CORPUS_REPEATED_TURN_RATE = 0.10
MAX_CROSS_SCRIPT_SIMILARITY = 0.45


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def parse_dialogue(markdown: str) -> list[DialogueTurn]:
    text = _FRONTMATTER_RE.sub("", markdown)
    turns: list[DialogueTurn] = []
    value_id = ""
    evidence_id = ""
    support_id = ""
    for line in text.splitlines():
        marker = _VALUE_MARKER_RE.search(line.strip())
        if marker:
            value_id, evidence_id, support_id = marker.groups()
            support_id = support_id or ""
            continue
        match = _TURN_RE.match(line.strip())
        if not match:
            continue
        role_key = _ROLE_LABELS[match.group(1)]
        turns.append(
            DialogueTurn(
                role_key=role_key,
                text=match.group(2).strip(),
                value_id=value_id,
                evidence_id=evidence_id,
                support_id=support_id,
            )
        )
    return turns


def _dialogue_quality_key(text: str) -> str:
    return _DUPLICATE_CHECK_RE.sub("", text).casefold()


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
    long_keys: list[str] = []
    for turn in turns:
        key = _dialogue_quality_key(turn.text)
        if len(key) >= _MIN_DUPLICATE_CHARS:
            long_keys.append(f"{turn.role_key}:{key}")
    duplicated_turns = sorted({key for key in long_keys if long_keys.count(key) > 1})
    if duplicated_turns:
        issues.append(f"重複セリフ: 同一セリフが反復しています ({len(duplicated_turns)}種類)")
    for block_size in (2, 3, 4):
        blocks = [
            tuple(long_keys[idx : idx + block_size])
            for idx in range(0, max(0, len(long_keys) - block_size + 1))
        ]
        if any(blocks.count(block) > 1 for block in blocks):
            issues.append(f"反復ブロック: {block_size}セリフ単位のやりとりが反復しています")
            break
    return issues


def _char_ngrams(text: str, size: int = 3) -> set[str]:
    normalized = _dialogue_quality_key(text)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[index:index + size] for index in range(len(normalized) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, len(left | right))


def source_evidence_sentences(markdown: str, *, limit: int = 20) -> list[str]:
    """記事本文から台本が参照できる根拠文を決定的に抽出する。"""
    body = _FRONTMATTER_RE.sub("", markdown)
    body = _CODE_FENCE_RE.sub("", body)
    body = _HEADING_RE.sub("", body)
    body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
    body = body.replace("__", "").replace("**", "")
    sentences: list[str] = []
    for sentence in re.split(r"(?<=[。！？])\s*", body):
        cleaned = re.sub(r"\s+", "", sentence).strip()
        if len(cleaned) >= 16:
            sentences.append(cleaned)
        if len(sentences) >= limit:
            break
    return sentences


def validate_source_grounding(turns: list[DialogueTurn], source_markdown: str) -> list[str]:
    """各価値区間の2根拠が実在し、回答本文に含まれることを検証する。"""
    issues: list[str] = []
    evidence = source_evidence_sentences(source_markdown)
    segments: dict[str, list[DialogueTurn]] = {}
    for turn in turns:
        segments.setdefault(turn.value_id, []).append(turn)
    for value_id in REQUIRED_VALUE_IDS:
        segment = segments.get(value_id, [])
        if not segment:
            continue
        segment_key = _dialogue_quality_key(
            "".join(turn.text for turn in segment if turn.role_key == "senior")
        )
        bindings = (
            ("主根拠", {turn.evidence_id for turn in segment if turn.evidence_id}),
            ("補助根拠", {turn.support_id for turn in segment if turn.support_id}),
        )
        for label, binding_ids in bindings:
            if len(binding_ids) != 1:
                continue
            binding_id = next(iter(binding_ids))
            match = re.fullmatch(r"source:(\d+)", binding_id)
            if not match:
                issues.append(f"価値ID {value_id}: {label}形式違反 ({binding_id})")
                continue
            index = int(match.group(1))
            if index >= len(evidence):
                issues.append(
                    f"価値ID {value_id}: {label}番号範囲外 "
                    f"({binding_id}, available={len(evidence)})"
                )
                continue
            source_key = _dialogue_quality_key(evidence[index])[:48]
            if not source_key or source_key not in segment_key:
                issues.append(f"価値ID {value_id}: {label}本文不一致 ({binding_id})")
    return issues


def validate_value_contract(turns: list[DialogueTurn]) -> list[str]:
    """価値ID、根拠、役割、意味非重複を単一fatal境界で検証する。"""
    issues: list[str] = []
    sequence: list[str] = []
    segments: dict[str, list[DialogueTurn]] = {}
    all_source_bindings: list[str] = []
    for turn in turns:
        if not turn.value_id:
            issues.append("価値ID欠落: 台本セリフが価値区間へ属していません")
            continue
        if not sequence or sequence[-1] != turn.value_id:
            sequence.append(turn.value_id)
        segments.setdefault(turn.value_id, []).append(turn)

    if tuple(sequence) != REQUIRED_VALUE_IDS:
        missing = [value_id for value_id in REQUIRED_VALUE_IDS if value_id not in sequence]
        issues.append(
            "価値ID順序・重複違反: "
            f"actual={sequence} required={list(REQUIRED_VALUE_IDS)} missing={missing}"
        )

    for value_id in REQUIRED_VALUE_IDS:
        segment = segments.get(value_id, [])
        if not segment:
            continue
        if len(segment) != 2:
            issues.append(f"価値ID {value_id}: 区間重複またはセリフ数違反 ({len(segment)}件、必要2件)")
        evidence_ids = {turn.evidence_id for turn in segment if turn.evidence_id}
        if len(evidence_ids) != 1:
            issues.append(f"価値ID {value_id}: 根拠参照は区間内で1件に固定してください")
        support_ids = {turn.support_id for turn in segment if turn.support_id}
        if len(support_ids) != 1:
            issues.append(f"価値ID {value_id}: 補助根拠参照は区間内で1件に固定してください")
        if len(evidence_ids) == 1 and len(support_ids) == 1:
            evidence_id = next(iter(evidence_ids))
            support_id = next(iter(support_ids))
            if evidence_id == support_id:
                issues.append(f"価値ID {value_id}: 主根拠と補助根拠が重複しています")
            all_source_bindings.extend((evidence_id, support_id))
        roles = {turn.role_key for turn in segment}
        if roles != {"junior", "senior"}:
            issues.append(f"価値ID {value_id}: 若手と先輩の両役割が必要です")

    if len(all_source_bindings) == len(REQUIRED_VALUE_IDS) * 2 and (
        len(set(all_source_bindings)) != len(all_source_bindings)
    ):
        issues.append("価値区間の根拠再利用: 14個の主根拠・補助根拠は全て異なる必要があります")

    segment_grams = {
        value_id: _char_ngrams("".join(turn.text for turn in segments.get(value_id, [])))
        for value_id in REQUIRED_VALUE_IDS
    }
    for index, left_id in enumerate(REQUIRED_VALUE_IDS):
        for right_id in REQUIRED_VALUE_IDS[index + 1:]:
            similarity = _jaccard(segment_grams[left_id], segment_grams[right_id])
            if similarity > MAX_SEGMENT_SIMILARITY:
                issues.append(
                    f"価値区間の意味反復: {left_id}/{right_id} similarity={similarity:.3f}"
                )
    return issues


def validate_dialogue_document(markdown: str, *, source_markdown: str | None = None) -> list[str]:
    """synthesisと再利用判定が共有する台本文書のfatal品質境界。"""
    turns = parse_dialogue(markdown)
    issues = validate_dialogue(turns)
    issues.extend(validate_value_contract(turns))
    if source_markdown is not None:
        issues.extend(validate_source_grounding(turns, source_markdown))
    for phrase in LEGACY_FILLER_PHRASES:
        if phrase in markdown:
            issues.append(f"旧定型句反復: {phrase}")
    matched_scaffolds = [phrase for phrase in LEGACY_VALUE_SCAFFOLDS if phrase in markdown]
    if matched_scaffolds:
        issues.append(
            "固定価値テンプレート反復: "
            + ", ".join(matched_scaffolds)
        )
    return issues


def _source_path_for_script(script_path: Path, markdown: str) -> tuple[Path | None, str | None]:
    match = _SOURCE_FIELD_RE.search(markdown)
    if not match:
        return None, "source frontmatter欠落"
    raw = match.group(1).strip().replace("\\", "/")
    if Path(raw).is_absolute():
        return None, "sourceはrepo内相対パスでなければなりません"
    resolved = (REPO_ROOT / raw).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None, "sourceがrepo外を参照しています"
    if not resolved.is_file():
        return None, f"source実体欠落: {raw}"
    return resolved, None


def audit_dialogue_corpus(paths: list[Path]) -> dict[str, object]:
    """日別台本を横断し、日をまたぐテンプレ反復をfatalとして返す。"""
    scripts: list[tuple[Path, list[DialogueTurn], set[str]]] = []
    issues: list[str] = []
    long_turn_keys: list[str] = []
    for path in paths:
        markdown = path.read_text(encoding="utf-8-sig")
        turns = parse_dialogue(markdown)
        source_path, source_issue = _source_path_for_script(path, markdown)
        document_issues = [source_issue] if source_issue else validate_dialogue_document(
            markdown,
            source_markdown=source_path.read_text(encoding="utf-8-sig") if source_path else None,
        )
        if document_issues:
            issues.extend(f"{path.name}: {issue}" for issue in document_issues)
        keys = [_dialogue_quality_key(turn.text) for turn in turns]
        long_turn_keys.extend(key for key in keys if len(key) >= _MIN_DUPLICATE_CHARS)
        scripts.append((path, turns, _char_ngrams("".join(turn.text for turn in turns))))

    counts = Counter(long_turn_keys)
    repeated_occurrences = sum(count for count in counts.values() if count > 1)
    repeated_rate = repeated_occurrences / max(1, len(long_turn_keys))
    if repeated_rate > MAX_CORPUS_REPEATED_TURN_RATE:
        issues.append(
            f"日跨ぎ完全反復率超過: {repeated_rate:.3f} > {MAX_CORPUS_REPEATED_TURN_RATE:.3f}"
        )

    maximum_similarity = 0.0
    maximum_pair = ("", "")
    for index, (left_path, _left_turns, left_grams) in enumerate(scripts):
        for right_path, _right_turns, right_grams in scripts[index + 1:]:
            similarity = _jaccard(left_grams, right_grams)
            if similarity > maximum_similarity:
                maximum_similarity = similarity
                maximum_pair = (left_path.name, right_path.name)
    if maximum_similarity > MAX_CROSS_SCRIPT_SIMILARITY:
        issues.append(
            "日跨ぎ台本類似度超過: "
            f"{maximum_similarity:.3f} > {MAX_CROSS_SCRIPT_SIMILARITY:.3f} pair={maximum_pair}"
        )
    return {
        "script_count": len(paths),
        "turn_count": sum(len(turns) for _path, turns, _grams in scripts),
        "repeated_turn_rate": repeated_rate,
        "maximum_cross_script_similarity": maximum_similarity,
        "maximum_pair": maximum_pair,
        "issues": issues,
    }


def normalize_turn_text(text: str) -> str:
    normalized = build_script.normalize_for_tts(text)
    return normalized.strip()


def mix_voice_wav_with_bgm(voice_wav: Path, bgm_path: Path, mp3_out: Path) -> None:
    duration = synthesize_daily._wav_duration_seconds(voice_wav)
    fade_out_start = max(duration - 5.0, 0.0)
    filter_complex = (
        f"[1:a]volume={BGM_VOLUME_DB:.1f}dB,"
        f"{BGM_EQ_FILTER},"
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
    source_path, source_issue = _source_path_for_script(script_path, markdown)
    issues = [source_issue] if source_issue else validate_dialogue_document(
        markdown,
        source_markdown=source_path.read_text(encoding="utf-8-sig") if source_path else None,
    )
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
    parser.add_argument("--validate-only", action="store_true", help="価値台帳と対談品質だけを検証します")
    parser.add_argument("--audit-corpus", action="store_true", help="同じディレクトリの直近31台本も反復監査します")
    args = parser.parse_args(argv)
    if args.validate_only:
        markdown = args.script.read_text(encoding="utf-8-sig")
        source_path, source_issue = _source_path_for_script(args.script, markdown)
        issues = [source_issue] if source_issue else validate_dialogue_document(
            markdown,
            source_markdown=source_path.read_text(encoding="utf-8-sig") if source_path else None,
        )
        if args.audit_corpus:
            paths = sorted(args.script.parent.glob("*-DeepDive-dialogue.md"))[-31:]
            corpus_result = audit_dialogue_corpus(paths)
            issues.extend(str(issue) for issue in corpus_result["issues"])
        for issue in issues:
            _warn(issue)
        return 1 if issues else 0
    return 0 if synthesize_dialogue(args.script, out_name=args.out_name) is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
