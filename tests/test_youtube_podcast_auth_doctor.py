from __future__ import annotations

from pathlib import Path

from tools.youtube_podcast import auth_doctor


class _OkClient:
    @classmethod
    def from_local_secrets(cls, secrets_path: Path) -> "_OkClient":
        return cls()

    def ensure_playlist(self, kind: str = "daily") -> str:
        return f"{kind}-playlist"


class _InvalidGrantClient(_OkClient):
    def ensure_playlist(self, kind: str = "daily") -> str:
        raise RuntimeError("invalid_grant: token has been expired or revoked")


class _QuotaClient(_OkClient):
    def ensure_playlist(self, kind: str = "daily") -> str:
        raise RuntimeError("403 quotaExceeded: YouTube quota exceeded")


def test_auth_doctor_ok_checks_daily_and_deepdive(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.json"
    secrets.write_text('{"installed":{"client_id":"id","client_secret":"secret","refresh_token":"token"}}', encoding="utf-8")
    monkeypatch.setattr(auth_doctor, "YouTubePodcastClient", _OkClient)

    result = auth_doctor.diagnose_auth(secrets_path=secrets)

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["checked_kinds"] == ["daily", "deepdive"]


def test_auth_doctor_missing_secrets_is_local_config_error(tmp_path: Path) -> None:
    result = auth_doctor.diagnose_auth(secrets_path=tmp_path / "missing.json")

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["auth_status"] == "missing_secrets"


def test_auth_doctor_invalid_grant_is_oauth_consent_required(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.json"
    secrets.write_text('{"installed":{"client_id":"id","client_secret":"secret","refresh_token":"token"}}', encoding="utf-8")
    monkeypatch.setattr(auth_doctor, "YouTubePodcastClient", _InvalidGrantClient)

    result = auth_doctor.diagnose_auth(secrets_path=secrets)

    assert result["ok"] is False
    assert result["exit_code"] == 10
    assert result["external_kind"] == "oauth_consent_required"
    assert result["reauth_required"] is True


def test_auth_doctor_quota_is_blocked_external_readiness(monkeypatch, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.json"
    secrets.write_text('{"installed":{"client_id":"id","client_secret":"secret","refresh_token":"token"}}', encoding="utf-8")
    monkeypatch.setattr(auth_doctor, "YouTubePodcastClient", _QuotaClient)

    result = auth_doctor.diagnose_auth(secrets_path=secrets)

    assert result["ok"] is False
    assert result["exit_code"] == 71
    assert result["auth_status"] == "blocked_external_readiness"
    assert result["external_kind"] == "youtube_quota_or_permission"
