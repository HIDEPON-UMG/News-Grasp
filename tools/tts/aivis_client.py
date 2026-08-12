from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tools.tts import proc


BASE = "http://127.0.0.1:10101"
PORT = 10101
MODEL_UUID = "47e53151-a378-46f3-abee-ce13aa07feb1"
# AivisSpeech /speakers は Hub の model UUID ではなく speaker_uuid を返す。
AIDA_SHIGERU_SPEAKER_UUID = "561e4e59-3bc9-4726-9028-44a3c12a6f1d"
MODEL_TO_SPEAKER_UUIDS: dict[str, set[str]] = {
    MODEL_UUID: {MODEL_UUID, AIDA_SHIGERU_SPEAKER_UUID},
    # DeepDive 対談サンプル用。Hub model UUID と Engine speaker_uuid は別 ID。
    "70a875a9-feae-41e6-a586-8cf9e47c6c0b": {
        "70a875a9-feae-41e6-a586-8cf9e47c6c0b",
        "6dd8366d-b928-4d60-ac41-b3010c85c08e",
    },
    "e9339137-2ae3-4d41-9394-fb757a7e61e6": {
        "e9339137-2ae3-4d41-9394-fb757a7e61e6",
        "41b7785f-35cc-4089-a360-dd8a63da5e75",
    },
    "59f96896-64d2-4378-830a-4d5feb3d81aa": {
        "59f96896-64d2-4378-830a-4d5feb3d81aa",
        "05df32d1-1c20-48d3-860d-83310004e046",
    },
}
DEFAULT_PARAMS: dict[str, Any] = {
    "speedScale": 1.0,
    "pitchScale": 0.0,
    "intonationScale": 1.1,
    "tempoDynamicsScale": 1.2,
    "volumeScale": 1.0,
    "pauseLengthScale": 1.1,
    "outputStereo": False,
}

_style_id_cache: dict[str, int] = {}
_owned_engine_process: proc.OwnedProcess | None = None


def _warn(message: str) -> None:
    print(f"[tts][WARN] {message}", file=sys.stderr)


def is_engine_up() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        sock.close()


def _candidate_engine_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("AIVISSPEECH_ENGINE_EXE")
    if env_path:
        paths.append(Path(env_path))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        paths.extend([
            Path(local) / "Programs" / "AivisSpeech" / "AivisSpeech-Engine" / "run.exe",
            Path(local) / "Programs" / "AivisSpeech" / "resources" / "engine" / "run.exe",
            Path(local) / "Programs" / "AivisSpeech" / "resources" / "AivisSpeech-Engine" / "run.exe",
            Path(local) / "Programs" / "AivisSpeech" / "AivisSpeech.exe",
        ])
    program_files = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    for root in [p for p in program_files if p]:
        paths.extend([
            Path(root) / "AivisSpeech" / "AivisSpeech-Engine" / "run.exe",
            Path(root) / "AivisSpeech" / "resources" / "engine" / "run.exe",
            Path(root) / "AivisSpeech" / "resources" / "AivisSpeech-Engine" / "run.exe",
            Path(root) / "AivisSpeech" / "AivisSpeech.exe",
        ])
    return paths


def _get_json(path: str, *, timeout: int | float = 10) -> Any:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as response:
        return json.load(response)


def _post_shutdown() -> None:
    req = urllib.request.Request(f"{BASE}/shutdown", method="POST")
    with urllib.request.urlopen(req, timeout=5) as response:
        response.read()


def engine_started_by_this_process() -> bool:
    return _owned_engine_process is not None


def ensure_engine(timeout: int = 60) -> bool:
    global _owned_engine_process
    if is_engine_up():
        return True

    exe = next((p for p in _candidate_engine_paths() if p.exists()), None)
    if not exe:
        _warn("AivisSpeech engine executable was not found; TTS step skipped")
        return False
    try:
        _owned_engine_process = proc.spawn_detached([exe], cwd=exe.parent)
    except Exception as exc:
        _warn(f"AivisSpeech auto-start failed: {exc}")
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _get_json("/speakers", timeout=3)
            return True
        except Exception:
            time.sleep(1)
    _warn("AivisSpeech did not become ready within timeout; TTS step skipped")
    return False


def shutdown_started_engine(timeout: int = 10) -> bool:
    global _owned_engine_process
    owned = _owned_engine_process
    if owned is None:
        return True
    try:
        try:
            _post_shutdown()
        except Exception as exc:
            _warn(f"AivisSpeech shutdown endpoint failed: {exc}")
        if owned.poll() is None:
            try:
                owned.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _warn("AivisSpeech did not exit after shutdown; closing owned Job")
                owned.close_job()
                owned.wait(timeout=5)
        return True
    except Exception as exc:
        _warn(f"AivisSpeech owned process cleanup failed: {exc}")
        return False
    finally:
        if owned is not None:
            owned.close()
        _owned_engine_process = None


def _speaker_uuids_for_model(uuid: str) -> set[str]:
    return MODEL_TO_SPEAKER_UUIDS.get(uuid, {uuid})


def resolve_style_id(uuid: str = MODEL_UUID) -> int:
    if uuid in _style_id_cache:
        return _style_id_cache[uuid]

    speakers = _get_json("/speakers")
    expected = _speaker_uuids_for_model(uuid)
    for speaker in speakers:
        if speaker.get("speaker_uuid") not in expected:
            continue
        styles = speaker.get("styles") or []
        if not styles:
            raise RuntimeError(f"AivisSpeech speaker has no styles: {speaker.get('name')}")
        style_id = int(styles[0]["id"])
        _style_id_cache[uuid] = style_id
        return style_id
    raise RuntimeError(f"AivisSpeech model/speaker UUID not found: {uuid}")


def synthesize(text: str, style_id: int, params: dict[str, Any] | None = None) -> bytes:
    query = urllib.parse.urlencode({"text": text, "speaker": str(style_id)})
    req = urllib.request.Request(f"{BASE}/audio_query?{query}", method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        audio_query = json.load(response)

    for key, value in (params or DEFAULT_PARAMS).items():
        audio_query[key] = value

    body = json.dumps(audio_query, ensure_ascii=False).encode("utf-8")
    synth_req = urllib.request.Request(
        f"{BASE}/synthesis?speaker={style_id}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(synth_req, timeout=120) as response:
        return response.read()
