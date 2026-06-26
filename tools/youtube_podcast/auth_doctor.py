from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.youtube_podcast.upload_episode import SECRETS_PATH, YouTubePodcastClient


def _redacted(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~")


def _base(*, secrets_path: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "auth_status": "",
        "external_kind": "",
        "external_system": "youtube",
        "observed_error_code": "",
        "source_command": "python -m tools.youtube_podcast.auth_doctor --check-only --json",
        "detail": "",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "reauth_required": False,
        "token_store_path_redacted": _redacted(secrets_path),
        "next_action": "",
        "checked_kinds": [],
        "exit_code": 1,
    }


def _secrets_has_refresh_token(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    installed = payload.get("installed") if isinstance(payload, dict) else None
    web = payload.get("web") if isinstance(payload, dict) else None
    for section in (payload, installed, web):
        if isinstance(section, dict) and str(section.get("refresh_token") or "").strip():
            return True
    return False


def _classify_error(exc: BaseException, *, secrets_path: Path) -> dict[str, Any]:
    text = str(exc)
    folded = text.casefold()
    result = _base(secrets_path=secrets_path)
    result["detail"] = text
    if "invalid_grant" in folded or "expired" in folded or "revoked" in folded:
        result.update(
            auth_status="oauth_consent_required",
            external_kind="oauth_consent_required",
            observed_error_code="invalid_grant",
            reauth_required=True,
            next_action="Run OAuth consent flow for YouTube Podcast credentials.",
            exit_code=10,
        )
    elif "quota" in folded or "permission" in folded or "403" in folded:
        result.update(
            auth_status="blocked_external_readiness",
            external_kind="youtube_quota_or_permission",
            observed_error_code="403",
            next_action="Wait for quota reset or verify YouTube API permissions.",
            exit_code=71,
        )
    elif "admin" in folded or "policy" in folded or "session" in folded:
        result.update(
            auth_status="blocked_external_readiness",
            external_kind="youtube_admin_or_session_policy",
            observed_error_code="admin_or_session_policy",
            next_action="Resolve Google Workspace/admin/session policy before publish.",
            exit_code=71,
        )
    else:
        result.update(
            auth_status="unknown_google_api_error",
            external_kind="unknown_google_api_error",
            observed_error_code=exc.__class__.__name__,
            next_action="Inspect auth doctor JSON and Google API error payload.",
            exit_code=71,
        )
    return result


def diagnose_auth(secrets_path: Path = SECRETS_PATH, kinds: Iterable[str] = ("daily", "deepdive")) -> dict[str, Any]:
    secrets_path = Path(secrets_path)
    result = _base(secrets_path=secrets_path)
    if not secrets_path.exists():
        result.update(
            auth_status="missing_secrets",
            external_kind="local_config_missing",
            observed_error_code="missing_secrets",
            detail=f"missing secrets file: {_redacted(secrets_path)}",
            next_action="Create YouTube OAuth secrets before podcast publish.",
            exit_code=1,
        )
        return result
    if not _secrets_has_refresh_token(secrets_path):
        result.update(
            auth_status="missing_refresh_token",
            external_kind="oauth_consent_required",
            observed_error_code="missing_refresh_token",
            detail="refresh_token is missing from secrets file",
            reauth_required=True,
            next_action="Run OAuth consent flow to obtain refresh_token.",
            exit_code=10,
        )
        return result
    try:
        client = YouTubePodcastClient.from_local_secrets(secrets_path)
        checked: list[str] = []
        for kind in kinds:
            client.ensure_playlist(kind)
            checked.append(kind)
    except (ImportError, ModuleNotFoundError) as exc:
        result.update(
            auth_status="dependency_missing",
            external_kind="local_dependency_missing",
            observed_error_code=exc.__class__.__name__,
            detail=str(exc),
            next_action="Install Google API Python dependencies.",
            exit_code=1,
        )
        return result
    except Exception as exc:  # Google API clients raise provider-specific exceptions.
        return _classify_error(exc, secrets_path=secrets_path)
    return {
        **result,
        "ok": True,
        "auth_status": "ok",
        "external_kind": "none",
        "observed_error_code": "",
        "detail": "YouTube OAuth readiness OK",
        "next_action": "none",
        "checked_kinds": checked,
        "exit_code": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check YouTube Podcast OAuth readiness without uploading.")
    parser.add_argument("--secrets-path", type=Path, default=SECRETS_PATH)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = diagnose_auth(secrets_path=args.secrets_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
