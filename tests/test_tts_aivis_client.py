from __future__ import annotations

import inspect
import subprocess

import pytest

from tools.tts import aivis_client


def test_live_aivis_smoke_is_excluded_from_static_runner_gate():
    """実 AivisSpeech 依存 test は runner の pytest-static gate に混入させない。"""
    source = inspect.getsource(test_aivis_client_resolves_style_and_synthesizes_short_wav_when_engine_is_up)
    assert "@pytest.mark.network" in source.split("def ", 1)[0]


def test_aivis_default_params_match_reviewed_voice_settings():
    assert aivis_client.DEFAULT_PARAMS["speedScale"] == 1.0
    assert aivis_client.DEFAULT_PARAMS["pitchScale"] == 0.0
    assert aivis_client.DEFAULT_PARAMS["intonationScale"] == 1.1
    assert aivis_client.DEFAULT_PARAMS["tempoDynamicsScale"] == 1.2
    assert aivis_client.DEFAULT_PARAMS["volumeScale"] == 1.0
    assert aivis_client.DEFAULT_PARAMS["pauseLengthScale"] == 1.1
    assert aivis_client.DEFAULT_PARAMS["outputStereo"] is False


def test_requested_dialogue_model_uuids_map_to_local_speaker_uuids():
    assert aivis_client._speaker_uuids_for_model("47e53151-a378-46f3-abee-ce13aa07feb1") == {
        "47e53151-a378-46f3-abee-ce13aa07feb1",
        "561e4e59-3bc9-4726-9028-44a3c12a6f1d",
    }
    assert aivis_client._speaker_uuids_for_model("59f96896-64d2-4378-830a-4d5feb3d81aa") == {
        "59f96896-64d2-4378-830a-4d5feb3d81aa",
        "05df32d1-1c20-48d3-860d-83310004e046",
    }


def test_candidate_engine_paths_include_current_installer_engine_location(monkeypatch, tmp_path):
    local_appdata = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("AIVISSPEECH_ENGINE_EXE", raising=False)

    paths = aivis_client._candidate_engine_paths()

    assert local_appdata / "Programs" / "AivisSpeech" / "AivisSpeech-Engine" / "run.exe" in paths


def test_candidate_engine_paths_prefer_engine_run_exe_before_gui(monkeypatch, tmp_path):
    local_appdata = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("AIVISSPEECH_ENGINE_EXE", raising=False)

    paths = aivis_client._candidate_engine_paths()
    engine = local_appdata / "Programs" / "AivisSpeech" / "AivisSpeech-Engine" / "run.exe"
    gui = local_appdata / "Programs" / "AivisSpeech" / "AivisSpeech.exe"

    assert paths.index(engine) < paths.index(gui)


@pytest.mark.network
def test_aivis_client_resolves_style_and_synthesizes_short_wav_when_engine_is_up():
    if not aivis_client.is_engine_up():
        pytest.skip("AivisSpeech engine is not running")

    style_id = aivis_client.resolve_style_id()
    wav = aivis_client.synthesize("こんにちは。", style_id)

    assert isinstance(style_id, int)
    assert wav.startswith(b"RIFF")
    assert len(wav) > 1024


def test_ensure_engine_records_only_process_it_started(monkeypatch):
    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    fake_process = FakeProcess()
    monkeypatch.setattr(aivis_client, "_owned_engine_process", None)
    monkeypatch.setattr(aivis_client, "is_engine_up", lambda: False)
    monkeypatch.setattr(aivis_client, "_candidate_engine_paths", lambda: [aivis_client.Path(__file__)])
    monkeypatch.setattr(aivis_client.proc, "spawn_detached", lambda _args, cwd=None: fake_process)
    monkeypatch.setattr(aivis_client, "_get_json", lambda _path, timeout=10: [])

    assert aivis_client.ensure_engine(timeout=1) is True
    assert aivis_client.engine_started_by_this_process() is True
    assert aivis_client._owned_engine_process is fake_process


def test_ensure_engine_starts_executable_from_its_parent_directory(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

    engine_dir = tmp_path / "AivisSpeech-Engine"
    engine_dir.mkdir()
    exe = engine_dir / "run.exe"
    exe.write_text("", encoding="utf-8")
    calls: list[tuple[list[aivis_client.Path], aivis_client.Path | None]] = []

    def fake_spawn(args, cwd=None):
        calls.append((args, cwd))
        return FakeProcess()

    monkeypatch.setattr(aivis_client, "_owned_engine_process", None)
    monkeypatch.setattr(aivis_client, "is_engine_up", lambda: False)
    monkeypatch.setattr(aivis_client, "_candidate_engine_paths", lambda: [exe])
    monkeypatch.setattr(aivis_client.proc, "spawn_detached", fake_spawn)
    monkeypatch.setattr(aivis_client, "_get_json", lambda _path, timeout=10: [])

    assert aivis_client.ensure_engine(timeout=1) is True
    assert calls == [([exe], exe.parent)]


def test_ensure_engine_does_not_take_ownership_of_preexisting_engine(monkeypatch):
    monkeypatch.setattr(aivis_client, "_owned_engine_process", None)
    monkeypatch.setattr(aivis_client, "is_engine_up", lambda: True)

    assert aivis_client.ensure_engine(timeout=1) is True
    assert aivis_client.engine_started_by_this_process() is False
    assert aivis_client._owned_engine_process is None


def test_shutdown_started_engine_only_terminates_owned_process(monkeypatch):
    events: list[str] = []

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def wait(self, timeout=None):
            events.append(f"wait:{timeout}")
            return 0

        def close_job(self):
            events.append("close_job")

        def close(self):
            events.append("close")

    fake_process = FakeProcess()
    monkeypatch.setattr(aivis_client, "_owned_engine_process", fake_process)
    monkeypatch.setattr(aivis_client, "_post_shutdown", lambda: events.append("shutdown"))

    assert aivis_client.shutdown_started_engine(timeout=3) is True
    assert events == ["shutdown", "wait:3", "close"]
    assert aivis_client._owned_engine_process is None


def test_shutdown_started_engine_is_noop_for_preexisting_engine(monkeypatch):
    monkeypatch.setattr(aivis_client, "_owned_engine_process", None)

    assert aivis_client.shutdown_started_engine(timeout=1) is True


def test_shutdown_started_engine_closes_owned_job_when_graceful_wait_times_out(monkeypatch):
    events: list[str] = []

    class FakeProcess:
        pid = 1234

        def __init__(self):
            self.job_closed = False

        def poll(self):
            return None

        def wait(self, timeout=None):
            events.append(f"wait:{timeout}")
            if not self.job_closed:
                raise subprocess.TimeoutExpired(cmd="AivisSpeech", timeout=timeout)
            return 1

        def close_job(self):
            events.append("close_job")
            self.job_closed = True

        def close(self):
            events.append("close")

    monkeypatch.setattr(aivis_client, "_owned_engine_process", FakeProcess())
    monkeypatch.setattr(aivis_client, "_post_shutdown", lambda: events.append("shutdown"))

    assert aivis_client.shutdown_started_engine(timeout=2) is True
    assert events == ["shutdown", "wait:2", "close_job", "wait:5", "close"]


def test_shutdown_started_engine_posts_shutdown_even_when_launcher_already_exited(monkeypatch):
    events: list[str] = []

    class FakeProcess:
        pid = 1234

        def poll(self):
            return 0

        def wait(self, timeout=None):
            events.append(f"wait:{timeout}")

        def close_job(self):
            events.append("close_job")

        def close(self):
            events.append("close")

    monkeypatch.setattr(aivis_client, "_owned_engine_process", FakeProcess())
    monkeypatch.setattr(aivis_client, "_post_shutdown", lambda: events.append("shutdown"))

    assert aivis_client.shutdown_started_engine(timeout=2) is True
    assert events == ["shutdown", "close"]
